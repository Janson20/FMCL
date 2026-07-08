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

# 模组加载器名称映射: 用户选择 -> minecraft_launcher_lib mod_loader ID / 自定义安装 ID
# 参考 PCL-CE: McInstanceState 枚举定义的加载器分类 + HMCL 扩展
MOD_LOADER_IDS = {
    "Forge": "forge",
    "Fabric": "fabric",
    "NeoForge": "neoforge",
    "Quilt": "quilt",
    "LiteLoader": "liteloader",
    "LegacyFabric": "legacyfabric",
    "Cleanroom": "cleanroom",
    "OptiFine": "optifine",
}

# 自定义安装的加载器（不走 minecraft_launcher_lib.mod_loader）
_CUSTOM_INSTALL_LOADERS = {"liteloader", "legacyfabric", "cleanroom", "optifine"}


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
        loader: 加载器类型 ("Forge", "Fabric", "NeoForge", "Quilt",
                "LiteLoader", "LegacyFabric", "Cleanroom", "OptiFine")
        version: Minecraft版本 (如 "1.20.4" 或 "26.1")
        minecraft_dir: Minecraft目录
        num_threads: 线程数 (未使用，保留兼容)
        mirror: 镜像源实例 (未使用，由 patch 控制)
        callback: 安装回调
        java: Java 可执行文件路径

    Returns:
        (installed_version_id, loader_version) 元组
        installed_version_id: 安装后的完整版本ID
        loader_version: 加载器版本号

    Raises:
        ValueError: 不支持的加载器类型
        Exception: 安装失败
    """
    import minecraft_launcher_lib

    loader_id = MOD_LOADER_IDS.get(loader)
    if not loader_id:
        raise ValueError(f"不支持的模组加载器: {loader}，支持: {list(MOD_LOADER_IDS.keys())}")

    # 自定义安装路径（不走 minecraft_launcher_lib.mod_loader）
    if loader_id in _CUSTOM_INSTALL_LOADERS:
        custom_installers = {
            "liteloader": _install_liteloader,
            "legacyfabric": _install_legacyfabric,
            "cleanroom": _install_cleanroom,
            "optifine": _install_optifine,
        }
        installer = custom_installers.get(loader_id)
        if installer:
            return installer(version, minecraft_dir, java)

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


# ══════════════════════════════════════════════════════════════════════
# LiteLoader 安装
# ══════════════════════════════════════════════════════════════════════

_LITELOADER_VERSIONS_URL = "https://dl.liteloader.com/versions/versions.json"
_LITELOADER_MIRROR_URL = "https://bmclapi.bangbang93.com/maven/com/mumfrey/liteloader/versions.json"


def _get_liteloader_versions(version: str) -> Optional[str]:
    """获取指定 MC 版本的最新 LiteLoader 版本号"""
    import json as _json
    try:
        url = _LITELOADER_MIRROR_URL if _is_mirror_enabled() else _LITELOADER_VERSIONS_URL
        resp = requests.get(url, timeout=30,
                          headers={"User-Agent": "FMCL/2.11.0"})
        if resp.status_code != 200:
            logger.warning(f"获取 LiteLoader 版本列表失败: HTTP {resp.status_code}")
            return None
        data = _json.loads(resp.text)
        versions_map = data.get("versions", {})
        mc_data = versions_map.get(version)
        if not mc_data:
            logger.warning(f"LiteLoader 不支持 MC {version}")
            return None

        # BMCLAPI 镜像使用英式拼写 "artefacts"，hash 为 key
        artefacts = mc_data.get("artefacts", {})
        liteloader_data = artefacts.get("com.mumfrey:liteloader", {})

        best_version = None
        best_ver_num = -1

        for _hash, entry in liteloader_data.items():
            if not isinstance(entry, dict):
                continue
            ver = entry.get("version")
            if not ver:
                continue
            stream = entry.get("stream", "")
            if stream and stream != "RELEASE":
                continue
            try:
                parts = ver.split("_")[-1]
                ver_num = int(parts)
            except (ValueError, IndexError):
                ver_num = 0
            if ver_num > best_ver_num:
                best_ver_num = ver_num
                best_version = ver

        # 回退：无 RELEASE 时取任何 stream 的最高版本
        if best_version is None:
            for _hash, entry in liteloader_data.items():
                if not isinstance(entry, dict):
                    continue
                ver = entry.get("version")
                if not ver:
                    continue
                try:
                    parts = ver.split("_")[-1]
                    ver_num = int(parts)
                except (ValueError, IndexError):
                    ver_num = 0
                if ver_num > best_ver_num:
                    best_ver_num = ver_num
                    best_version = ver

        if best_version:
            return best_version

        # 回退：snapshots（1.12.2 等版本只有 SNAPSHOT）
        snapshots = mc_data.get("snapshots", {})
        snap_ll = snapshots.get("com.mumfrey:liteloader", {})
        snap_latest = snap_ll.get("latest", {})
        snap_ver = snap_latest.get("version")
        if snap_ver:
            return snap_ver

        # 最后回退：repo.lastSuccess
        repo = mc_data.get("repo", {})
        last_success = repo.get("lastSuccess", {})
        return last_success.get("version")
    except Exception as e:
        logger.error(f"解析 LiteLoader 版本列表失败: {e}")
        return None


def _install_liteloader(version: str, minecraft_dir: str, java: str = None) -> Tuple[str, str]:
    """
    安装 LiteLoader（参考 HMCL LiteLoaderInstallTask）

    LiteLoader 必须安装在一个已有 Forge 的版本上（通过 tweakClass 注入）。
    这里创建一个新的版本 JSON patch，添加 LiteLoader library 和 tweakClass 参数。

    Args:
        version: Minecraft 版本号
        minecraft_dir: .minecraft 目录
        java: Java 可执行文件路径

    Returns:
        (installed_version_id, loader_version)
    """
    import json as _json
    import minecraft_launcher_lib

    loader_version = _get_liteloader_versions(version)
    if not loader_version:
        raise ValueError(f"未找到 MC {version} 对应的 LiteLoader 版本")

    logger.info(f"安装 LiteLoader {loader_version} for MC {version}")

    # 确保原版已安装
    vanilla_version_id = version
    try:
        minecraft_launcher_lib.install.install_minecraft_version(
            version, minecraft_dir
        )
    except Exception:
        pass

    # 读取原版版本 JSON
    versions_dir = Path(minecraft_dir) / "versions" / vanilla_version_id
    version_json_path = versions_dir / f"{vanilla_version_id}.json"
    if not version_json_path.exists():
        raise FileNotFoundError(f"版本 JSON 不存在: {version_json_path}")

    with open(version_json_path, "r", encoding="utf-8") as f:
        base_version_json = _json.loads(f.read())

    # 构建 LiteLoader 版本 ID
    installed_version_id = f"{version}-liteloader-{loader_version}"

    # 检查是否已存在 LiteLoader 安装
    target_dir = Path(minecraft_dir) / "versions" / installed_version_id
    if target_dir.exists():
        logger.info(f"LiteLoader 版本 {installed_version_id} 已存在，跳过安装")
        return installed_version_id, loader_version

    # 构建 LiteLoader library
    lite_lib = {
        "name": f"com.mumfrey:liteloader:{loader_version}",
        "url": "http://dl.liteloader.com/versions/"
    }

    # 创建新版本 JSON（patch 格式，继承原版）
    new_version = {
        "id": installed_version_id,
        "inheritsFrom": vanilla_version_id,
        "type": "release",
        "mainClass": "net.minecraft.launchwrapper.Launch",
        "arguments": {
            "game": [
                "--tweakClass",
                "com.mumfrey.liteloader.launch.LiteLoaderTweaker"
            ]
        },
        "libraries": [lite_lib],
    }

    # 写入新版本 JSON
    target_dir.mkdir(parents=True, exist_ok=True)
    new_json_path = target_dir / f"{installed_version_id}.json"
    with open(new_json_path, "w", encoding="utf-8") as f:
        _json.dump(new_version, f, indent=2)

    logger.info(f"LiteLoader 安装成功: {installed_version_id}")
    slog.info("mod_loader_installed", loader="liteloader", version=version,
              installed_version_id=installed_version_id, loader_version=loader_version)
    return installed_version_id, loader_version


# ══════════════════════════════════════════════════════════════════════
# LegacyFabric 安装
# ══════════════════════════════════════════════════════════════════════

_LEGACY_FABRIC_GAME_URL = "https://meta.legacyfabric.net/v2/versions/game"
_LEGACY_FABRIC_LOADER_URL = "https://meta.legacyfabric.net/v2/versions/loader"
_LEGACY_FABRIC_LAUNCH_META_URL = "https://meta.legacyfabric.net/v2/versions/loader/{game}/{loader}"


def _get_legacyfabric_versions(version: str) -> Optional[str]:
    """获取指定 MC 版本的最新 LegacyFabric Loader 版本号"""
    import json as _json
    try:
        # 获取游戏版本列表
        resp = requests.get(_LEGACY_FABRIC_GAME_URL, timeout=30,
                          headers={"User-Agent": "FMCL/2.11.0"})
        if resp.status_code != 200:
            return None
        game_versions = {gv.get("version"): gv for gv in _json.loads(resp.text)}

        # 标准化版本号（LegacyFabric 用 2point0_ 前缀表示 2.0）
        normalized = version
        if version.startswith("2.0"):
            normalized = "2point0_" + version[4:]

        if normalized not in game_versions:
            logger.warning(f"LegacyFabric 不支持 MC {version}")
            return None

        # 获取 loader 版本列表
        resp2 = requests.get(_LEGACY_FABRIC_LOADER_URL, timeout=30,
                           headers={"User-Agent": "FMCL/2.11.0"})
        if resp2.status_code != 200:
            return None
        loader_versions = _json.loads(resp2.text)
        if not isinstance(loader_versions, list) or not loader_versions:
            return None

        # 取最新版本
        latest = loader_versions[-1]
        return latest.get("version")
    except Exception as e:
        logger.error(f"获取 LegacyFabric 版本列表失败: {e}")
        return None


def _install_legacyfabric(version: str, minecraft_dir: str, java: str = None) -> Tuple[str, str]:
    """
    安装 LegacyFabric（参考 HMCL LegacyFabricInstallTask）

    Args:
        version: Minecraft 版本号
        minecraft_dir: .minecraft 目录
        java: Java 可执行文件路径

    Returns:
        (installed_version_id, loader_version)
    """
    import json as _json
    import minecraft_launcher_lib

    loader_version = _get_legacyfabric_versions(version)
    if not loader_version:
        raise ValueError(f"未找到 MC {version} 对应的 LegacyFabric 版本")

    logger.info(f"安装 LegacyFabric Loader {loader_version} for MC {version}")

    installed_version_id = f"{version}-legacyfabric-{loader_version}"
    target_dir = Path(minecraft_dir) / "versions" / installed_version_id
    if target_dir.exists():
        logger.info(f"LegacyFabric 版本 {installed_version_id} 已存在，跳过安装")
        return installed_version_id, loader_version

    # 确保原版已安装
    try:
        minecraft_launcher_lib.install.install_minecraft_version(
            version, minecraft_dir
        )
    except Exception:
        pass

    # 标准化版本号用于 API
    normalized = version
    if version.startswith("2.0"):
        normalized = "2point0_" + version[4:]

    # 获取 launcher meta
    launch_meta_url = _LEGACY_FABRIC_LAUNCH_META_URL.format(
        game=normalized, loader=loader_version
    )
    resp = requests.get(launch_meta_url, timeout=30,
                      headers={"User-Agent": "FMCL/2.11.0"})
    resp.raise_for_status()
    launcher_meta = _json.loads(resp.text)

    # 提取 mainClass
    main_class_obj = launcher_meta.get("launcherMeta", {}).get("mainClass", {})
    if isinstance(main_class_obj, dict):
        main_class = main_class_obj.get("client", "net.fabricmc.loader.impl.launch.knot.KnotClient")
    else:
        main_class = main_class_obj

    # 提取 libraries
    libraries_obj = launcher_meta.get("launcherMeta", {}).get("libraries", {})
    libraries = []
    for side in ("common", "server"):
        for lib in libraries_obj.get(side, []):
            libraries.append(lib)

    # 添加 intermediary 和 loader
    intermediary = launcher_meta.get("intermediary", {})
    loader_info = launcher_meta.get("loader", {})
    if intermediary.get("maven"):
        libraries.append({"name": intermediary["maven"]})
    if loader_info.get("maven"):
        libraries.append({"name": loader_info["maven"]})

    # 检查 launchwrapper
    arguments = {"game": []}
    if launcher_meta.get("launcherMeta", {}).get("launchwrapper"):
        tweakers = launcher_meta["launcherMeta"]["launchwrapper"].get("tweakers", {})
        client_tweakers = tweakers.get("client", [])
        if client_tweakers:
            arguments["game"].extend(["--tweakClass", client_tweakers[0]])

    # 创建新版本 JSON
    vanilla_version_id = version
    new_version = {
        "id": installed_version_id,
        "inheritsFrom": vanilla_version_id,
        "type": "release",
        "mainClass": main_class,
        "arguments": arguments,
        "libraries": libraries,
    }

    target_dir.mkdir(parents=True, exist_ok=True)
    new_json_path = target_dir / f"{installed_version_id}.json"
    with open(new_json_path, "w", encoding="utf-8") as f:
        _json.dump(new_version, f, indent=2)

    logger.info(f"LegacyFabric 安装成功: {installed_version_id}")
    slog.info("mod_loader_installed", loader="legacyfabric", version=version,
              installed_version_id=installed_version_id, loader_version=loader_version)
    return installed_version_id, loader_version


# ══════════════════════════════════════════════════════════════════════
# Cleanroom 安装
# ══════════════════════════════════════════════════════════════════════

_CLEANROOM_INDEX_URL = "https://hmcl.glavo.site/metadata/cleanroom/index.json"
_CLEANROOM_INSTALLER_URL = "https://hmcl.glavo.site/metadata/cleanroom/files/cleanroom-{version}-installer.jar"


def _get_cleanroom_versions(version: str) -> Optional[str]:
    """获取指定 MC 版本的最新 Cleanroom 版本号"""
    import json as _json
    if version != "1.12.2":
        logger.warning(f"Cleanroom 仅支持 MC 1.12.2，不支持 {version}")
        return None
    try:
        resp = requests.get(_CLEANROOM_INDEX_URL, timeout=30,
                          headers={"User-Agent": "FMCL/2.11.0"})
        if resp.status_code != 200:
            return None
        releases = _json.loads(resp.text)
        if not releases:
            return None
        # 取最新版本
        latest = releases[-1]
        return latest.get("name")
    except Exception as e:
        logger.error(f"获取 Cleanroom 版本列表失败: {e}")
        return None


def _install_cleanroom(version: str, minecraft_dir: str, java: str = None) -> Tuple[str, str]:
    """
    安装 Cleanroom（参考 HMCL CleanroomInstallTask）

    Cleanroom 是 Forge 的 forks，仅支持 1.12.2。
    安装方式：下载 Cleanroom 安装器 jar，手动执行 Forge 式安装（提取
    install_profile.json 中的 version.json、安装 libraries、提取 forge universal jar），
    最后将版本 ID 改为包含 "cleanroom" 标识。

    Args:
        version: Minecraft 版本号（仅 "1.12.2"）
        minecraft_dir: .minecraft 目录
        java: Java 可执行文件路径

    Returns:
        (installed_version_id, loader_version)
    """
    import json as _json
    import minecraft_launcher_lib
    import tempfile
    import zipfile
    import shutil

    loader_version = _get_cleanroom_versions(version)
    if not loader_version:
        raise ValueError(f"未找到 MC {version} 对应的 Cleanroom 版本")

    logger.info(f"安装 Cleanroom {loader_version} for MC {version}")

    installed_version_id = f"{version}-cleanroom-{loader_version}"
    target_dir = Path(minecraft_dir) / "versions" / installed_version_id
    if target_dir.exists():
        logger.info(f"Cleanroom 版本 {installed_version_id} 已存在，跳过安装")
        return installed_version_id, loader_version

    # 确保原版已安装
    try:
        minecraft_launcher_lib.install.install_minecraft_version(
            version, minecraft_dir
        )
    except Exception:
        pass

    # 下载 Cleanroom 安装器
    installer_url = _CLEANROOM_INSTALLER_URL.format(version=loader_version)
    logger.info(f"下载 Cleanroom 安装器: {installer_url}")
    resp = requests.get(installer_url, timeout=120,
                      headers={"User-Agent": "FMCL/2.11.0"})
    resp.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".jar", delete=False) as tmp:
        tmp.write(resp.content)
        installer_path = tmp.name

    try:
        with zipfile.ZipFile(installer_path, "r") as zf:
            # 读取 install_profile.json
            with zf.open("install_profile.json", "r") as f:
                install_profile = _json.loads(f.read())

            minecraft_version = install_profile.get("minecraft") or install_profile.get("install", {}).get("minecraft", version)
            forge_version = f"{minecraft_version}-{loader_version}"

            # 安装 libraries
            if "libraries" in install_profile:
                from minecraft_launcher_lib.install import install_libraries
                install_libraries(minecraft_version, install_profile["libraries"], str(minecraft_dir), {})

            # 提取 version.json
            client_json = None
            if "version.json" in zf.namelist():
                with zf.open("version.json", "r") as f:
                    client_json = _json.loads(f.read())
            elif "versionInfo" in install_profile:
                client_json = install_profile["versionInfo"]

            if client_json is None:
                raise RuntimeError("无法从 Cleanroom 安装器中提取 version.json")

            # 使用 Cleanroom 版本 ID
            client_json["id"] = installed_version_id

            target_dir.mkdir(parents=True, exist_ok=True)
            json_path = target_dir / f"{installed_version_id}.json"
            with open(json_path, "w", encoding="utf-8") as f:
                _json.dump(client_json, f, ensure_ascii=False, indent=4)

            # 提取 forge universal jar
            # Cleanroom 的 maven 路径: com.cleanroommc:cleanroom 而不是 net.minecraftforge:forge
            cleanroom_lib_path = os.path.join(
                minecraft_dir, "libraries", "com", "cleanroommc", "cleanroom", forge_version
            )
            os.makedirs(cleanroom_lib_path, exist_ok=True)

            # 尝试多种可能的 universal jar 路径
            possible_paths = [
                f"maven/com/cleanroommc/cleanroom/{forge_version}/cleanroom-{forge_version}-universal.jar",
                f"maven/net/minecraftforge/forge/{forge_version}/forge-{forge_version}-universal.jar",
                f"forge-{forge_version}-universal.jar",
                f"cleanroom-{forge_version}-universal.jar",
            ]
            extracted = False
            for path in possible_paths:
                try:
                    with zf.open(path) as src:
                        dest_file = os.path.join(
                            cleanroom_lib_path,
                            f"cleanroom-{forge_version}.jar"
                        )
                        with open(dest_file, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                    extracted = True
                    break
                except KeyError:
                    continue

            if not extracted:
                logger.warning("未在 Cleanroom 安装器中找到 universal jar，但不影响安装")
    finally:
        try:
            os.unlink(installer_path)
        except Exception:
            pass

    logger.info(f"Cleanroom 安装成功: {installed_version_id}")
    slog.info("mod_loader_installed", loader="cleanroom", version=version,
              installed_version_id=installed_version_id, loader_version=loader_version)
    return installed_version_id, loader_version


# ══════════════════════════════════════════════════════════════════════
# OptiFine 安装
# ══════════════════════════════════════════════════════════════════════

_OPTIFINE_BMCLAPI_LIST_URL = "https://bmclapi2.bangbang93.com/optifine/{version}"


def _get_optifine_versions(version: str) -> Optional[str]:
    """获取指定 MC 版本的最新 OptiFine 版本号

    BMCLAPI 返回格式: [{"patch": "E7", "type": "HD_U", ...}, ...]
    返回格式: "{type}_{patch}" (如 "HD_U_E7", "HD_U_G6_pre1")
    """
    import json as _json
    try:
        url = _OPTIFINE_BMCLAPI_LIST_URL.format(version=version)
        resp = requests.get(url, timeout=30,
                          headers={"User-Agent": "FMCL/2.11.0"})
        if resp.status_code != 200:
            logger.warning(f"获取 OptiFine 版本列表失败: HTTP {resp.status_code}")
            return None
        versions = _json.loads(resp.text)
        if not versions:
            return None
        latest = versions[-1]
        if isinstance(latest, dict):
            of_type = latest.get("type", "HD_U")
            of_patch = latest.get("patch", "")
            return f"{of_type}_{of_patch}"
        elif isinstance(latest, str):
            return latest
        return None
    except Exception as e:
        logger.error(f"获取 OptiFine 版本列表失败: {e}")
        return None


def _install_optifine(version: str, minecraft_dir: str, java: str = None) -> Tuple[str, str]:
    """
    安装 OptiFine（参考 HMCL OptiFineInstallTask）

    OptiFine 安装流程：
    1. 下载 OptiFine 安装器 JAR
    2. 从安装器中提取 OptiFine 类库
    3. 创建版本 JSON patch，添加 OptiFine library 和 tweakClass

    注意: OptiFine 必须在最后安装（在 Forge 之后），且依赖 launchwrapper。

    Args:
        version: Minecraft 版本号
        minecraft_dir: .minecraft 目录
        java: Java 可执行文件路径

    Returns:
        (installed_version_id, loader_version)
    """
    import json as _json
    import minecraft_launcher_lib
    import tempfile
    import zipfile
    import shutil

    loader_version = _get_optifine_versions(version)
    if not loader_version:
        raise ValueError(f"未找到 MC {version} 对应的 OptiFine 版本")

    logger.info(f"安装 OptiFine {loader_version} for MC {version}")

    installed_version_id = f"{version}-optifine-{loader_version}"
    target_dir = Path(minecraft_dir) / "versions" / installed_version_id
    if target_dir.exists():
        logger.info(f"OptiFine 版本 {installed_version_id} 已存在，跳过安装")
        return installed_version_id, loader_version

    # 确保原版已安装
    try:
        minecraft_launcher_lib.install.install_minecraft_version(
            version, minecraft_dir
        )
    except Exception:
        pass

    # 下载 OptiFine 安装器
    # BMCLAPI OptiFine 下载格式:
    # https://bmclapi2.bangbang93.com/optifine/{mc_version}/{type}/{patch}
    # loader_version 格式为 "{type}_{patch}", 按最后一个 _ 拆分
    last_underscore = loader_version.rfind("_")
    of_type = loader_version[:last_underscore] if last_underscore > 0 else "HD_U"
    of_patch = loader_version[last_underscore + 1:] if last_underscore > 0 else loader_version
    download_url = f"https://bmclapi2.bangbang93.com/optifine/{version}/{of_type}/{of_patch}"

    logger.info(f"下载 OptiFine 安装器: {download_url}")
    resp = requests.get(download_url, timeout=120,
                      headers={"User-Agent": "FMCL/2.11.0"})
    resp.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".jar", delete=False) as tmp:
        tmp.write(resp.content)
        installer_path = tmp.name

    try:
        # 从安装器提取 OptiFine 库文件
        vanilla_version_id = version

        # 创建 OptiFine 版本 JSON (patch 格式)
        new_version = {
            "id": installed_version_id,
            "inheritsFrom": vanilla_version_id,
            "type": "release",
            "mainClass": "net.minecraft.launchwrapper.Launch",
            "arguments": {
                "game": [
                    "--tweakClass",
                    "optifine.OptiFineTweaker"
                ]
            },
            "libraries": [
                {"name": "net.minecraft:launchwrapper:1.12"},
                {
                    "name": f"optifine:OptiFine:{version}_{loader_version}",
                },
            ],
        }

        target_dir.mkdir(parents=True, exist_ok=True)
        new_json_path = target_dir / f"{installed_version_id}.json"
        with open(new_json_path, "w", encoding="utf-8") as f:
            _json.dump(new_version, f, indent=2)

        # 将 OptiFine 安装器复制到 libraries 目录
        # 路径: libraries/optifine/OptiFine/{mc_version}_{of_version}/OptiFine-{mc_version}_{of_version}.jar
        maven_version = f"{version}_{loader_version}"
        of_lib_dir = Path(minecraft_dir) / "libraries" / "optifine" / "OptiFine" / maven_version
        of_lib_dir.mkdir(parents=True, exist_ok=True)
        of_lib_path = of_lib_dir / f"OptiFine-{maven_version}.jar"
        shutil.copy2(installer_path, str(of_lib_path))

    finally:
        try:
            os.unlink(installer_path)
        except Exception:
            pass

    logger.info(f"OptiFine 安装成功: {installed_version_id}")
    slog.info("mod_loader_installed", loader="optifine", version=version,
              installed_version_id=installed_version_id, loader_version=loader_version)
    return installed_version_id, loader_version


# ══════════════════════════════════════════════════════════════════════
# 镜像源辅助函数
# ══════════════════════════════════════════════════════════════════════

def _is_mirror_enabled() -> bool:
    """检测镜像源是否启用"""
    try:
        from mirror import mirror
        return mirror.enabled
    except Exception:
        return False
