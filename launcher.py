"""Minecraft启动器核心模块"""
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any

import minecraft_launcher_lib
from logzero import logger

from config import Config
from downloader import download_forge
from mirror import mirror, MirrorSource


class MinecraftLauncher:
    """Minecraft启动器类"""

    def __init__(self, config: Config):
        """
        初始化启动器

        Args:
            config: 配置对象
        """
        self.config = config
        self.minecraft_dir = str(config.minecraft_dir)
        self.options = minecraft_launcher_lib.utils.generate_test_options()
        self.current_max = 0

        # UI回调 (可选,用于进度更新)
        self.on_progress: Optional[Callable[[int, int, str], None]] = None

        # 初始化镜像源
        self._mirror = MirrorSource(enabled=config.mirror_enabled)
        self._apply_mirror_patch()

    def _apply_mirror_patch(self):
        """应用镜像源补丁"""
        if self._mirror.enabled:
            self._mirror.patch_minecraft_launcher_lib()
            logger.info(f"已启用镜像源: {self._mirror.get_mirror_name()}")
        else:
            logger.info("使用Mojang官方源")

    def _set_status(self, status: str) -> None:
        """状态回调"""
        logger.info(status)
        if self.on_progress:
            self.on_progress(0, 0, status)

    def _set_progress(self, progress: int) -> None:
        """进度回调"""
        if self.current_max != 0:
            logger.debug(f"进度: {progress}/{self.current_max}")
            if self.on_progress:
                self.on_progress(progress, self.current_max, "")

    def _set_max(self, new_max: int) -> None:
        """设置最大值回调"""
        self.current_max = new_max
        logger.info(f"总任务数: {new_max}")

    def _get_callback(self) -> Dict[str, Callable]:
        """获取回调函数字典"""
        return {
            "setStatus": self._set_status,
            "setProgress": self._set_progress,
            "setMax": self._set_max
        }

    def check_and_setup_environment(self) -> None:
        """检查并设置环境"""
        logger.info("正在检查文件夹...")

        if not self.config.minecraft_dir.exists():
            logger.warning("Minecraft目录不存在")
            logger.info("首次使用,正在初始化...")

            self.config.ensure_directories()
            logger.info("目录创建成功")

            logger.info("正在下载最新正式版...")

            try:
                # 优先使用镜像源获取最新版本号
                if self._mirror.enabled:
                    latest = self._mirror.get_latest_version()
                    latest_release = latest.get("release", "")
                    if latest_release:
                        logger.info(f"从镜像源获取最新版本: {latest_release}")
                    else:
                        latest_release = minecraft_launcher_lib.utils.get_latest_version()["release"]
                else:
                    latest_release = minecraft_launcher_lib.utils.get_latest_version()["release"]

                minecraft_launcher_lib.install.install_minecraft_version(
                    latest_release,
                    self.minecraft_dir,
                    callback=self._get_callback()
                )
                logger.info("正式版下载成功")
            except Exception as e:
                logger.error(f"下载初始版本失败: {str(e)}")
                raise
        else:
            logger.info("文件夹检查完成")

    def get_available_versions(self) -> List[Dict[str, str]]:
        """
        获取可用版本列表

        Returns:
            版本列表
        """
        try:
            versions = minecraft_launcher_lib.utils.get_available_versions(self.minecraft_dir)
            logger.info(f"获取到 {len(versions)} 个版本")
            return versions
        except Exception as e:
            logger.error(f"获取版本列表失败: {str(e)}")
            return []

    def get_installed_versions(self) -> List[str]:
        """
        获取已安装的版本列表

        Returns:
            已安装版本ID列表
        """
        try:
            versions_dir = self.config.get_versions_dir()
            if not versions_dir.exists():
                return []

            installed = [
                v for v in os.listdir(str(versions_dir))
                if v not in ['jre_manifest.json', 'version_manifest_v2.json']
            ]
            logger.info(f"已安装 {len(installed)} 个版本")
            return installed

        except Exception as e:
            logger.error(f"获取已安装版本失败: {str(e)}")
            return []

    def install_version(self, version_id: str, mod_loader: str = "无") -> bool:
        """
        安装Minecraft版本

        Args:
            version_id: 版本ID
            mod_loader: 模组加载器 ("无", "Forge", "Fabric", "NeoForge")

        Returns:
            是否安装成功
        """
        try:
            # 检查版本是否有效
            available_versions = self.get_available_versions()
            version_ids = [v["id"].split()[0] if isinstance(v["id"], str) else v["id"] for v in available_versions]

            if version_id not in version_ids:
                logger.error(f"无效的版本ID: {version_id}")
                return False

            # 安装模组加载器
            if mod_loader and mod_loader != "无":
                from downloader import download_mod_loader
                logger.info(f"正在下载 {mod_loader} {version_id}")
                loader_file = download_mod_loader(
                    mod_loader,
                    version_id,
                    num_threads=self.config.download_threads,
                    mirror=self._mirror,
                    minecraft_dir=self.minecraft_dir,
                    callback=self._get_callback(),
                )
                if loader_file:
                    logger.info(f"{mod_loader} 安装完成: {loader_file}")
                else:
                    logger.info(f"{mod_loader} 安装流程完成")

            # 安装Minecraft版本
            logger.info(f"正在安装 Minecraft {version_id}")
            minecraft_launcher_lib.install.install_minecraft_version(
                version_id,
                self.minecraft_dir,
                callback=self._get_callback()
            )

            logger.info(f"Minecraft {version_id} 安装成功")
            return True

        except Exception as e:
            logger.error(f"安装版本失败: {str(e)}")
            return False

    def launch_game(self, version_id: str) -> bool:
        """
        启动游戏

        Args:
            version_id: 版本ID

        Returns:
            是否启动成功
        """
        try:
            # 检查版本是否已安装
            installed_versions = self.get_installed_versions()

            if version_id not in installed_versions:
                logger.error(f"版本未安装: {version_id}")
                return False

            # 获取启动命令
            logger.info(f"正在生成启动命令: {version_id}")
            minecraft_command = minecraft_launcher_lib.command.get_minecraft_command(
                version_id,
                self.minecraft_dir,
                self.options
            )

            logger.info("正在启动游戏...")
            subprocess.run(minecraft_command)

            logger.info("游戏进程已退出")
            return True

        except Exception as e:
            logger.error(f"启动游戏失败: {str(e)}")
            return False

    def get_callbacks(self) -> Dict[str, Callable]:
        """
        获取供UI调用的回调函数字典

        Returns:
            回调函数字典
        """
        return {
            "check_environment": self.check_and_setup_environment,
            "get_available_versions": self.get_available_versions,
            "get_installed_versions": self.get_installed_versions,
            "install_version": self.install_version,
            "launch_game": self.launch_game,
            "set_mirror_enabled": self.set_mirror_enabled,
            "get_mirror_enabled": self.get_mirror_enabled,
            "test_mirror_connection": self.test_mirror_connection,
            "get_mirror_name": self.get_mirror_name,
        }

    def set_mirror_enabled(self, enabled: bool) -> None:
        """
        设置镜像源启用状态

        Args:
            enabled: 是否启用镜像源
        """
        self._mirror.enabled = enabled
        self.config.mirror_enabled = enabled
        self.config.save_config()

        if enabled:
            self._mirror.patch_minecraft_launcher_lib()
            logger.info(f"已启用镜像源: {self._mirror.get_mirror_name()}")
        else:
            logger.info("已切换到Mojang官方源")

    def get_mirror_enabled(self) -> bool:
        """获取镜像源启用状态"""
        return self._mirror.enabled

    def test_mirror_connection(self) -> bool:
        """测试当前镜像源连接"""
        return self._mirror.test_connection()

    def get_mirror_name(self) -> str:
        """获取当前镜像源名称"""
        return self._mirror.get_mirror_name()
