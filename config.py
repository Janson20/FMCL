"""配置文件管理模块"""
import os
from pathlib import Path
from typing import Optional

import logzero
from logzero import logger

# 高性能 JSON 解析：orjson 比 stdlib json 快 3-10 倍
try:
    import orjson as _json_mod

    def _json_loads(data: bytes | str):
        return _json_mod.loads(data)

    def _json_dumps(obj, indent: int = 2, ensure_ascii: bool = False) -> str:
        # orjson.dumps 返回 bytes，需解码
        opts = _json_mod.OPT_INDENT_2 if indent == 2 else 0
        if not ensure_ascii:
            opts |= _json_mod.OPT_NON_STR_KEYS
        return _json_mod.dumps(obj, option=opts).decode("utf-8")

except ImportError:
    import json as _json_mod  # type: ignore[no-redef]

    def _json_loads(data: bytes | str):
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return _json_mod.loads(data)

    def _json_dumps(obj, indent: int = 2, ensure_ascii: bool = False) -> str:
        return _json_mod.dumps(obj, indent=indent, ensure_ascii=ensure_ascii)


class Config:
    """启动器配置类"""

    # 默认配置
    DEFAULT_MIRROR_ENABLED = True
    DEFAULT_MINIMIZE_ON_GAME_LAUNCH = False
    DEFAULT_AUTO_CHECK_UPDATE = True

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

        # 启动行为配置
        self.minimize_on_game_launch = self.DEFAULT_MINIMIZE_ON_GAME_LAUNCH

        # 更新配置
        self.auto_check_update = self.DEFAULT_AUTO_CHECK_UPDATE

        # 日志配置
        self.log_level = logzero.INFO

        # 从配置文件加载
        self._load_config()

    def _load_config(self) -> None:
        """从配置文件加载配置"""
        if not self.config_file.exists():
            return

        try:
            with open(self.config_file, "rb") as f:
                data = _json_loads(f.read())

            if "mirror_enabled" in data:
                self.mirror_enabled = data["mirror_enabled"]
            if "download_threads" in data:
                self.download_threads = data["download_threads"]
            if "minimize_on_game_launch" in data:
                self.minimize_on_game_launch = data["minimize_on_game_launch"]
            if "auto_check_update" in data:
                self.auto_check_update = data["auto_check_update"]

            logger.info(f"配置已加载: 镜像源={'启用' if self.mirror_enabled else '禁用'}, 启动后最小化={'启用' if self.minimize_on_game_launch else '禁用'}, 自动检查更新={'启用' if self.auto_check_update else '禁用'}")

        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")

    def save_config(self) -> None:
        """保存配置到文件"""
        try:
            data = {
                "mirror_enabled": self.mirror_enabled,
                "download_threads": self.download_threads,
                "minimize_on_game_launch": self.minimize_on_game_launch,
                "auto_check_update": self.auto_check_update,
            }
            with open(self.config_file, "w", encoding="utf-8") as f:
                f.write(_json_dumps(data, indent=2, ensure_ascii=False))
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
        return f"Config(base_dir={self.base_dir}, minecraft_dir={self.minecraft_dir}, mirror={'ON' if self.mirror_enabled else 'OFF'}, minimize={'ON' if self.minimize_on_game_launch else 'OFF'}, auto_update={'ON' if self.auto_check_update else 'OFF'})"


# 全局配置实例
config = Config()
