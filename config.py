"""配置文件管理模块"""
import os
from pathlib import Path
from typing import Optional


class Config:
    """启动器配置类"""
    
    def __init__(self, base_dir: Optional[str] = None):
        """
        初始化配置
        
        Args:
            base_dir: 基础目录,默认为当前工作目录
        """
        self.base_dir = Path(base_dir) if base_dir else Path.cwd()
        self.minecraft_dir = self.base_dir / ".minecraft"
        self.log_file = self.base_dir / "latest.log"
        
        # 下载配置
        self.download_threads = 4
        self.chunk_size = 8192
        
        # 日志配置
        self.log_level = logzero.INFO
        
    def ensure_directories(self) -> None:
        """确保必要的目录存在"""
        self.minecraft_dir.mkdir(parents=True, exist_ok=True)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
    def get_versions_dir(self) -> Path:
        """获取版本目录路径"""
        return self.minecraft_dir / "versions"
    
    def __repr__(self) -> str:
        return f"Config(base_dir={self.base_dir}, minecraft_dir={self.minecraft_dir})"


# 全局配置实例
config = Config()
