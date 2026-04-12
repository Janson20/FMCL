"""Minecraft启动器核心模块"""
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any, Tuple

import minecraft_launcher_lib
from logzero import logger

from config import Config
from mirror import mirror, MirrorSource


class MinecraftLauncher:
    """Minecraft启动器类"""

    def __init__(self, config: Config):
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
        """获取可用版本列表"""
        try:
            versions = minecraft_launcher_lib.utils.get_available_versions(self.minecraft_dir)
            logger.info(f"获取到 {len(versions)} 个版本")
            return versions
        except Exception as e:
            logger.error(f"获取版本列表失败: {str(e)}")
            return []

    def get_installed_versions(self) -> List[str]:
        """获取已安装的版本列表"""
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

    def install_version(self, version_id: str, mod_loader: str = "无") -> Tuple[bool, str]:
        """
        安装Minecraft版本

        安装逻辑：
        - 无模组加载器: 仅安装原版 Minecraft
        - 有模组加载器: mod_loader.install() 会自动先装原版再装loader

        Args:
            version_id: 版本ID (如 "1.20.4")
            mod_loader: 模组加载器 ("无", "Forge", "Fabric", "NeoForge")

        Returns:
            (是否成功, 安装后的版本ID) 元组
            安装原版时返回 version_id
            安装模组加载器时返回 loader 创建的版本ID (如 "1.20.4-forge-49.0.26")
        """
        try:
            # 检查版本是否有效
            available_versions = self.get_available_versions()
            version_ids = [v["id"].split()[0] if isinstance(v["id"], str) else v["id"] for v in available_versions]

            if version_id not in version_ids:
                logger.error(f"无效的版本ID: {version_id}")
                return False, version_id

            if mod_loader and mod_loader != "无":
                # 安装模组加载器 (会自动安装原版)
                from downloader import install_mod_loader
                logger.info(f"正在安装 {mod_loader} for Minecraft {version_id}")
                installed_version_id, loader_version = install_mod_loader(
                    loader=mod_loader,
                    version=version_id,
                    minecraft_dir=self.minecraft_dir,
                    num_threads=self.config.download_threads,
                    mirror=self._mirror,
                    callback=self._get_callback(),
                )
                logger.info(f"安装完成: {installed_version_id} (Loader: {mod_loader} {loader_version})")
                return True, installed_version_id
            else:
                # 仅安装原版 Minecraft
                logger.info(f"正在安装 Minecraft {version_id}")
                minecraft_launcher_lib.install.install_minecraft_version(
                    version_id,
                    self.minecraft_dir,
                    callback=self._get_callback()
                )
                logger.info(f"Minecraft {version_id} 安装成功")
                return True, version_id

        except Exception as e:
            logger.error(f"安装版本失败: {str(e)}")
            return False, version_id

    def launch_game(self, version_id: str) -> bool:
        """
        启动游戏

        Args:
            version_id: 版本ID (可以是原版ID如 "1.20.4"，也可以是loader版本ID如 "1.20.4-forge-49.0.26")

        Returns:
            是否启动成功
        """
        try:
            # 检查版本是否已安装
            installed_versions = self.get_installed_versions()

            # 精确匹配
            if version_id in installed_versions:
                target_version = version_id
            else:
                # 尝试模糊匹配：用户可能选了原版ID，但实际安装的是loader版本
                # 例如用户选 "1.20.4"，但安装的是 "1.20.4-forge-49.0.26"
                matches = [v for v in installed_versions if v.startswith(version_id)]
                if len(matches) == 1:
                    target_version = matches[0]
                    logger.info(f"模糊匹配: {version_id} -> {target_version}")
                elif len(matches) > 1:
                    # 多个匹配，优先选择带 loader 的版本
                    loader_matches = [v for v in matches if "-" in v and v != version_id]
                    if loader_matches:
                        target_version = loader_matches[0]
                        logger.info(f"多个匹配，选择: {target_version}")
                    else:
                        target_version = matches[0]
                else:
                    logger.error(f"版本未安装: {version_id}")
                    return False

            # 获取启动命令
            logger.info(f"正在生成启动命令: {target_version}")
            minecraft_command = minecraft_launcher_lib.command.get_minecraft_command(
                target_version,
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

    def remove_version(self, version_id: str) -> Tuple[bool, str]:
        """
        删除已安装的版本

        删除 versions/{version_id}/ 目录和 versions/{version_id}.json 文件。

        Args:
            version_id: 版本ID (如 "1.20.4" 或 "1.20.4-forge-49.0.26")

        Returns:
            (是否成功, 版本ID) 元组
        """
        try:
            versions_dir = self.config.get_versions_dir()
            version_dir = versions_dir / version_id
            version_json = versions_dir / f"{version_id}.json"

            if not version_dir.exists() and not version_json.exists():
                logger.error(f"版本未安装: {version_id}")
                return False, version_id

            # 删除版本目录
            if version_dir.exists():
                shutil.rmtree(str(version_dir))
                logger.info(f"已删除版本目录: {version_dir}")

            # 删除版本JSON文件
            if version_json.exists():
                version_json.unlink()
                logger.info(f"已删除版本JSON: {version_json}")

            logger.info(f"版本 {version_id} 删除成功")
            return True, version_id

        except Exception as e:
            logger.error(f"删除版本失败: {str(e)}")
            return False, version_id

    def get_callbacks(self) -> Dict[str, Callable]:
        """获取供UI调用的回调函数字典"""
        return {
            "check_environment": self.check_and_setup_environment,
            "get_available_versions": self.get_available_versions,
            "get_installed_versions": self.get_installed_versions,
            "install_version": self.install_version,
            "remove_version": self.remove_version,
            "launch_game": self.launch_game,
            "set_mirror_enabled": self.set_mirror_enabled,
            "get_mirror_enabled": self.get_mirror_enabled,
            "test_mirror_connection": self.test_mirror_connection,
            "get_mirror_name": self.get_mirror_name,
        }

    def set_mirror_enabled(self, enabled: bool) -> None:
        """设置镜像源启用状态"""
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
