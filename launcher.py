"""Minecraft启动器核心模块"""
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Dict, Optional

import minecraft_launcher_lib
from logzero import logger

from config import Config
from downloader import download_forge
from ui import VersionSelector, show_confirmation, show_alert


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
        
    def _set_status(self, status: str) -> None:
        """状态回调"""
        logger.info(status)
        
    def _set_progress(self, progress: int) -> None:
        """进度回调"""
        if self.current_max != 0:
            logger.debug(f"进度: {progress}/{self.current_max}")
            
    def _set_max(self, new_max: int) -> None:
        """设置最大值回调"""
        self.current_max = new_max
        logger.info(f"总任务数: {new_max}")
        
    def _get_callback(self) -> Dict[str, callable]:
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
            show_alert("首次使用,正在下载最新正式版...", "初始化")
            
            try:
                minecraft_launcher_lib.install.install_minecraft_version(
                    minecraft_launcher_lib.utils.get_latest_version()["release"],
                    self.minecraft_dir,
                    callback=self._get_callback()
                )
                logger.info("正式版下载成功")
                show_alert("初始版本下载成功!", "初始化完成")
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
    
    def install_version(self, version_id: str, install_forge: bool = False) -> bool:
        """
        安装Minecraft版本
        
        Args:
            version_id: 版本ID
            install_forge: 是否安装Forge
            
        Returns:
            是否安装成功
        """
        try:
            # 检查版本是否有效
            available_versions = self.get_available_versions()
            version_ids = [v["id"].split()[0] if isinstance(v["id"], str) else v["id"] for v in available_versions]
            
            if version_id not in version_ids:
                logger.error(f"无效的版本ID: {version_id}")
                show_alert(f"无效的版本ID: {version_id}", "错误")
                return False
            
            # 安装Forge
            if install_forge:
                if not show_confirmation(f"确认安装 Forge {version_id}?", "Forge安装"):
                    return False
                    
                logger.info(f"正在下载 Forge {version_id}")
                forge_file = download_forge(version_id, num_threads=self.config.download_threads)
                
                show_alert(
                    f"安装程序下载成功!\n请运行 {forge_file} 完成Forge安装",
                    "Forge下载完成"
                )
            
            # 安装Minecraft版本
            if not show_confirmation(f"确认安装 Minecraft {version_id}?", "确认安装"):
                return False
            
            logger.info(f"正在安装 Minecraft {version_id}")
            minecraft_launcher_lib.install.install_minecraft_version(
                version_id,
                self.minecraft_dir,
                callback=self._get_callback()
            )
            
            logger.info(f"Minecraft {version_id} 安装成功")
            show_alert(f"Minecraft {version_id} 安装成功!", "安装完成")
            return True
            
        except Exception as e:
            logger.error(f"安装版本失败: {str(e)}")
            show_alert(f"安装失败: {str(e)}", "错误")
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
                show_alert(f"版本未安装: {version_id}", "错误")
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
            show_alert(f"启动失败: {str(e)}", "错误")
            return False
    
    def run(self) -> None:
        """运行启动器主循环"""
        try:
            # 检查环境
            self.check_and_setup_environment()
            
            # 主循环
            while True:
                try:
                    # 询问是否安装新版本
                    if show_confirmation("是否安装新版本?", "MCL启动器"):
                        # 显示可用版本列表
                        versions = self.get_available_versions()
                        
                        if not versions:
                            show_alert("无法获取版本列表", "错误")
                            continue
                        
                        selector = VersionSelector("选择要安装的版本")
                        selected_version = selector.show(versions)
                        
                        if not selected_version:
                            continue
                        
                        # 提取版本ID
                        version_id = selected_version.split()[0]
                        
                        # 询问是否安装Forge
                        install_forge = show_confirmation("是否安装Forge?", "Forge安装")
                        
                        # 安装版本
                        self.install_version(version_id, install_forge)
                    
                    # 显示已安装版本
                    installed = self.get_installed_versions()
                    
                    if not installed:
                        show_alert("未找到已安装的版本", "提示")
                        continue
                    
                    installed_versions = [{"id": v, "type": "已安装"} for v in installed]
                    selector = VersionSelector("选择要启动的版本")
                    selected_version = selector.show(installed_versions)
                    
                    if selected_version:
                        version_id = selected_version.split()[0]
                        
                        # 启动游戏
                        if self.launch_game(version_id):
                            # 游戏结束后询问是否退出
                            if show_confirmation("是否退出启动器?", "退出确认"):
                                logger.info("用户选择退出")
                                return
                            
                except KeyboardInterrupt:
                    logger.info("用户中断")
                    return
                    
                except Exception as e:
                    logger.error(f"主循环错误: {str(e)}")
                    show_alert(f"发生错误: {str(e)}", "错误")
                    
                    if not show_confirmation("是否继续?", "错误"):
                        return
                        
        except Exception as e:
            logger.error(f"启动器运行失败: {str(e)}")
            raise
