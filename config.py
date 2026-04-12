"""配置文件管理模块"""
import json
import os
from pathlib import Path
from typing import Optional

import logzero
from logzero import logger


class Config:
    """启动器配置类"""

    # 默认配置
    DEFAULT_MIRROR_ENABLED = True

    def __init__(self, base_dir: Optional[str] = None):
        """
        初始化配置

        Args:
            base_dir: 基础目录,默认为当前工作目录
        """
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.minecraft_dir = self.base_dir / ".minecraft"
        self.log_file = self.base_dir / "latest.log"
        self.config_file = self.base_dir / "config.json"

        # 下载配置
        self.download_threads = 4
        self.chunk_size = 8192

        # 镜像源配置
        self.mirror_enabled = self.DEFAULT_MIRROR_ENABLED

        # 日志配置
        self.log_level = logzero.INFO

        # 从配置文件加载
        self._load_config()

    def _load_config(self) -> None:
        """从配置文件加载配置"""
        if not self.config_file.exists():
            return

        try:
            with open(self.config_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            if "mirror_enabled" in data:
                self.mirror_enabled = data["mirror_enabled"]
            if "download_threads" in data:
                self.download_threads = data["download_threads"]

            logger.info(f"配置已加载: 镜像源={'启用' if self.mirror_enabled else '禁用'}")

        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")

    def save_config(self) -> None:
        """保存配置到文件"""
        try:
            data = {
                "mirror_enabled": self.mirror_enabled,
                "download_threads": self.download_threads,
            }
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            logger.info("配置已保存")
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")

    def ensure_directories(self) -> None:
        """确保必要的目录存在"""
        self.minecraft_dir.mkdir(parents=True, exist_ok=True)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_versions_dir(self) -> Path:
        """获取版本目录路径"""
        return self.minecraft_dir / "versions"

    def __repr__(self) -> str:
        return f"Config(base_dir={self.base_dir}, minecraft_dir={self.minecraft_dir}, mirror={'ON' if self.mirror_enabled else 'OFF'})"


# 全局配置实例
config = Config()
