"""模组加载器安装模块

使用 minecraft_launcher_lib.mod_loader 统一安装 Forge/Fabric/NeoForge。
安装顺序：先安装原版 Minecraft，再安装模组加载器。
mod_loader.install() 会在原版未安装时自动安装原版。

安装后生成的版本ID格式：
  - Forge:    {mc_version}-forge-{forge_version}  (如 "1.20.4-forge-49.0.26" 或 "26.1-forge-1.0.0")
  - Fabric:   fabric-loader-{loader_version}-{mc_version}  (如 "fabric-loader-0.15.11-1.20.4" 或 "fabric-loader-0.16.0-26.1.1")
  - NeoForge: {mc_version}-neoforge-{neoforge_version}  (如 "1.20.4-neoforge-20.4.234" 或 "26.1-neoforge-1.0.0")
"""
import asyncio
import os
import threading
import time
from typing import Optional, Callable, Dict, Tuple, List
from pathlib import Path

import requests
from logzero import logger

from structured_logger import slog


def _patch_neoforge_normalize():
    """修复 NeoForge 模块的 _normalize_minecraft_version 方法，
    使其支持 Minecraft 新版本命名格式 (YY.D, YY.D.H)
    
    NeoForge 的 maven API 返回版本如 20.4.234、26.1.0，
    其 _normalize_minecraft_version 实现为 f"1.{prefix}"，
    这对旧格式 (20.4→1.20.4) 正确，但新格式 (26.1→1.26.1) 会匹配失败。
    """
    try:
        from minecraft_launcher_lib.mod_loader._neoforge import Neoforge

        _original_normalize = Neoforge._normalize_minecraft_version

        def _patched_normalize(self, minecraft_version: str) -> str:
            # 检测新格式版本 (YY.D, YY.D.H, YY >= 26)
            parts = minecraft_version.split(".")
            if len(parts) >= 2 and not minecraft_version.startswith("1."):
                try:
                    yy = int(parts[0])
                    if yy >= 26:
                        # 新格式直接返回原值，不需要加 "1." 前缀
                        return minecraft_version
                except ValueError:
                    pass
            # 旧格式保持原行为
            return _original_normalize(self, minecraft_version)

        Neoforge._normalize_minecraft_version = _patched_normalize
        logger.info("已修补 NeoForge._normalize_minecraft_version (支持新版本格式)")
    except Exception as e:
        logger.debug(f"修补 NeoForge._normalize_minecraft_version 失败: {e}")


# 应用 NeoForge 版本规范化补丁
_patch_neoforge_normalize()


class MultiThreadDownloader:
    """多线程下载器"""

    def __init__(self, num_threads: int = 4, chunk_size: int = 8192):
        self.num_threads = num_threads
        self.chunk_size = chunk_size
        self.downloaded_bytes = 0
        self.total_size = 0
        self.start_time = 0
        self.lock = threading.Lock()

    def _download_part(
        self,
        url: str,
        start_byte: int,
        end_byte: int,
        part_number: int,
        filename: Path,
        progress_bar
    ) -> None:
        headers = {'Range': f'bytes={start_byte}-{end_byte}'}
        try:
            response = requests.get(url, headers=headers, stream=True, timeout=30)
            response.raise_for_status()

            part_filename = f"{filename}.part{part_number}"
            with open(part_filename, 'wb') as f:
                for chunk in response.iter_content(chunk_size=self.chunk_size):
                    if chunk:
                        f.write(chunk)
                        with self.lock:
                            self.downloaded_bytes += len(chunk)
                            progress_bar.update(len(chunk))
        except Exception as e:
            logger.error(f"下载分段 {part_number} 失败: {str(e)}")
            raise

    def _merge_files(self, filename: Path) -> None:
        try:
            with open(filename, 'wb') as outfile:
                for i in range(self.num_threads):
                    part_filename = f"{filename}.part{i}"
                    if os.path.exists(part_filename):
                        with open(part_filename, 'rb') as infile:
                            outfile.write(infile.read())
                        os.remove(part_filename)
            logger.info(f"文件合并成功: {filename}")
        except Exception as e:
            logger.error(f"合并文件失败: {str(e)}")
            raise

    def download(self, url: str, output_dir: Optional[str] = None) -> str:
        try:
            response = requests.head(url, timeout=10)
            response.raise_for_status()

            self.total_size = int(response.headers.get('Content-Length', 0))
            if self.total_size == 0:
                raise ValueError("无法获取文件大小")

            filename = Path(url.split('/')[-1])
            if output_dir:
                filename = Path(output_dir) / filename

            part_size = self.total_size // self.num_threads
            threads = []

            from tqdm import tqdm
            progress_bar = tqdm(
                total=self.total_size,
                unit='B',
                unit_scale=True,
                desc=filename.name
            )

            self.downloaded_bytes = 0
            self.start_time = time.time()

            for i in range(self.num_threads):
                start_byte = i * part_size
                end_byte = start_byte + part_size - 1 if i < self.num_threads - 1 else self.total_size - 1
                thread = threading.Thread(
                    target=self._download_part,
                    args=(url, start_byte, end_byte, i, filename, progress_bar)
                )
                threads.append(thread)
                thread.start()

            for thread in threads:
                thread.join()

            progress_bar.close()
            self._merge_files(filename)
            logger.info(f"文件下载成功: {filename}")
            return str(filename)
        except Exception as e:
            logger.error(f"下载失败: {str(e)}")
            raise


# ─── 异步并发下载器 ─────────────────────────────────────────────

class AsyncBatchDownloader:
    """
    异步并发下载器 — 基于 asyncio + aiohttp

    适用于批量下载大量小文件（如游戏资源、库文件），
    单线程内通过协程并发处理数百个下载任务，
    效率远高于同步逐个下载或传统多线程下载。

    用法:
        downloader = AsyncBatchDownloader(max_concurrent=20)
        tasks = [
            ("https://example.com/file1.jar", "/path/to/file1.jar"),
            ("https://example.com/file2.jar", "/path/to/file2.jar"),
        ]
        results = downloader.run(tasks)
    """

    def __init__(self, max_concurrent: int = 20, chunk_size: int = 65536):
        """
        Args:
            max_concurrent: 最大并发下载数
            chunk_size: 下载块大小（字节）
        """
        self.max_concurrent = max_concurrent
        self.chunk_size = chunk_size

    def run(
        self,
        tasks: List[Tuple[str, str]],
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> Dict[str, bool]:
        """
        同步接口：在后台运行异步下载

        Args:
            tasks: [(url, 保存路径)] 列表
            progress_callback: 进度回调 (已完成, 总数, 当前文件名)

        Returns:
            {保存路径: 是否成功} 字典
        """
        try:
            import aiohttp
        except ImportError:
            logger.warning("aiohttp 未安装，回退到同步逐个下载")
            return self._sync_fallback(tasks, progress_callback)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果已在 asyncio 事件循环中（如 Qt 集成场景），用线程运行
                result = [None]
                error = [None]

                def _run():
                    try:
                        result[0] = asyncio.run(self._async_download_all(tasks, progress_callback))
                    except Exception as e:
                        error[0] = e

                t = threading.Thread(target=_run, daemon=True)
                t.start()
                t.join()
                if error[0]:
                    raise error[0]
                return result[0]
            else:
                return loop.run_until_complete(
                    self._async_download_all(tasks, progress_callback)
                )
        except RuntimeError:
            return asyncio.run(self._async_download_all(tasks, progress_callback))

    async def _async_download_all(
        self,
        tasks: List[Tuple[str, str]],
        progress_callback: Optional[Callable[[int, int, str], None]],
    ) -> Dict[str, bool]:
        """异步下载所有文件"""
        import aiohttp

        results: Dict[str, bool] = {}
        semaphore = asyncio.Semaphore(self.max_concurrent)
        total = len(tasks)
        done = [0]  # 用列表以便闭包修改

        async def _download_one(session: aiohttp.ClientSession, url: str, save_path: str):
            async with semaphore:
                try:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                        if resp.status != 200:
                            logger.warning(f"下载失败 (HTTP {resp.status}): {url}")
                            slog.warning("download_failed", url=url[:200], status_code=resp.status)
                            return save_path, False

                        # 确保目标目录存在
                        Path(save_path).parent.mkdir(parents=True, exist_ok=True)

                        with open(save_path, "wb") as f:
                            async for chunk in resp.content.iter_chunked(self.chunk_size):
                                f.write(chunk)

                    done[0] += 1
                    if progress_callback:
                        progress_callback(done[0], total, Path(save_path).name)
                    return save_path, True

                except Exception as e:
                    logger.debug(f"下载异常 {url}: {e}")
                    done[0] += 1
                    return save_path, False

        connector = aiohttp.TCPConnector(limit=self.max_concurrent, limit_per_host=5)
        async with aiohttp.ClientSession(connector=connector) as session:
            coros = [_download_one(session, url, path) for url, path in tasks]
            download_results = await asyncio.gather(*coros, return_exceptions=True)

        for item in download_results:
            if isinstance(item, Exception):
                logger.debug(f"下载任务异常: {item}")
                continue
            path, success = item
            results[path] = success

        success_count = sum(1 for v in results.values() if v)
        logger.info(f"异步下载完成: {success_count}/{total} 成功")
        return results

    def _sync_fallback(
        self,
        tasks: List[Tuple[str, str]],
        progress_callback: Optional[Callable[[int, int, str], None]],
    ) -> Dict[str, bool]:
        """无 aiohttp 时的同步回退"""
        results: Dict[str, bool] = {}
        total = len(tasks)
        for i, (url, save_path) in enumerate(tasks):
            try:
                Path(save_path).parent.mkdir(parents=True, exist_ok=True)
                resp = requests.get(url, timeout=30)
                resp.raise_for_status()
                with open(save_path, "wb") as f:
                    f.write(resp.content)
                results[save_path] = True
            except Exception as e:
                logger.debug(f"同步下载失败 {url}: {e}")
                slog.warning("download_failed_sync", url=url[:200], error=str(e)[:200])
                results[save_path] = False

            if progress_callback:
                progress_callback(i + 1, total, Path(save_path).name)

        return results


# ─── 模组加载器安装 ──────────────────────────────────────────────

# 模组加载器名称映射: 用户选择 -> minecraft_launcher_lib mod_loader ID
MOD_LOADER_IDS = {
    "Forge": "forge",
    "Fabric": "fabric",
    "NeoForge": "neoforge",
}


def install_mod_loader(
    loader: str,
    version: str,
    minecraft_dir: str,
    num_threads: int = 4,
    mirror=None,
    callback: Dict[str, Callable] = None,
    java: str = None,
) -> Tuple[str, str]:
    """
    安装模组加载器（会自动安装原版 Minecraft 如果未安装）

    Args:
        loader: 加载器类型 ("Forge", "Fabric", "NeoForge")
        version: Minecraft版本 (如 "1.20.4" 或 "26.1")
        minecraft_dir: Minecraft目录
        num_threads: 线程数 (未使用，保留兼容)
        mirror: 镜像源实例 (未使用，由 patch 控制)
        callback: 安装回调
        java: Java 可执行文件路径

    Returns:
        (installed_version_id, loader_version) 元组
        installed_version_id: 安装后的完整版本ID (如 "1.20.4-forge-49.0.26" 或 "26.1-forge-1.0.0")
        loader_version: 加载器版本号 (如 "49.0.26")

    Raises:
        ValueError: 不支持的加载器类型
        Exception: 安装失败
    """
    import minecraft_launcher_lib

    loader_id = MOD_LOADER_IDS.get(loader)
    if not loader_id:
        raise ValueError(f"不支持的模组加载器: {loader}，支持: {list(MOD_LOADER_IDS.keys())}")

    try:
        logger.info(f"正在安装 {loader} for Minecraft {version}")

        # 获取 ModLoader 实例
        mod_loader = minecraft_launcher_lib.mod_loader.get_mod_loader(loader_id)

        # 获取最新的 loader 版本
        loader_version = mod_loader.get_latest_loader_version(version)
        logger.info(f"最新 {loader} 版本: {loader_version}")

        # 安装（mod_loader.install 会在原版未安装时自动安装原版）
        installed_version_id = mod_loader.install(
            minecraft_version=version,
            minecraft_directory=minecraft_dir,
            loader_version=loader_version,
            callback=callback,
            java=java,
        )

        logger.info(f"{loader} 安装成功: 版本ID={installed_version_id}, Loader版本={loader_version}")
        slog.info("mod_loader_installed", loader=loader_id, version=version,
                  installed_version_id=installed_version_id, loader_version=loader_version)
        return installed_version_id, loader_version

    except Exception as e:
        logger.error(f"安装 {loader} 失败: {str(e)}")
        slog.error("mod_loader_install_failed", loader=loader_id, version=version, error=str(e))
        raise
