"""模组加载器安装模块

使用 minecraft_launcher_lib.mod_loader 统一安装 Forge/Fabric/NeoForge。
安装顺序：先安装原版 Minecraft，再安装模组加载器。
mod_loader.install() 会在原版未安装时自动安装原版。

安装后生成的版本ID格式：
  - Forge:    {mc_version}-forge-{forge_version}  (如 "1.20.4-forge-49.0.26")
  - Fabric:   fabric-loader-{loader_version}-{mc_version}  (如 "fabric-loader-0.15.11-1.20.4")
  - NeoForge: {mc_version}-neoforge-{neoforge_version}  (如 "1.20.4-neoforge-20.4.234")
"""
import os
import threading
import time
from typing import Optional, Callable, Dict, Tuple
from pathlib import Path

import requests
from logzero import logger


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
        version: Minecraft版本 (如 "1.20.4")
        minecraft_dir: Minecraft目录
        num_threads: 线程数 (未使用，保留兼容)
        mirror: 镜像源实例 (未使用，由 patch 控制)
        callback: 安装回调
        java: Java 可执行文件路径

    Returns:
        (installed_version_id, loader_version) 元组
        installed_version_id: 安装后的完整版本ID (如 "1.20.4-forge-49.0.26")
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
        return installed_version_id, loader_version

    except Exception as e:
        logger.error(f"安装 {loader} 失败: {str(e)}")
        raise
