"""配置文件管理模块"""
import os
import platform
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


def _get_platform_paths():
    """
    根据操作系统返回平台特定的路径配置
    
    Returns:
        dict: {
            'base_dir': 基础目录（Windows/macOS: 当前目录, Linux: ~/.fmcl）,
            'minecraft_dir': Minecraft 目录,
            'log_file': 日志文件路径,
            'config_file': 配置文件路径
        }
    """
    system = platform.system().lower()
    
    if system == "linux":
        # Linux: 遵循 FHS 标准
        home = Path.home()
        
        # 配置文件: /etc/fmcl/config.json
        config_dir = Path("/etc/fmcl")
        config_file = config_dir / "config.json"
        
        # 日志文件: /var/log/fmcl/latest.log
        log_dir = Path("/var/log/fmcl")
        log_file = log_dir / "latest.log"
        
        # Minecraft 目录: ~/.minecraft
        minecraft_dir = home / ".minecraft"
        
        # 基础目录: ~/.fmcl (用于其他运行时文件)
        base_dir = home / ".fmcl"
        
        return {
            'base_dir': base_dir,
            'minecraft_dir': minecraft_dir,
            'log_file': log_file,
            'config_file': config_file,
        }
    else:
        # Windows/macOS: 使用当前工作目录
        base_dir = Path.cwd()
        return {
            'base_dir': base_dir,
            'minecraft_dir': base_dir / ".minecraft",
            'log_file': base_dir / "latest.log",
            'config_file': base_dir / "config.json",
        }


class Config:
    """启动器配置类"""

    # 默认配置
    DEFAULT_MIRROR_ENABLED = True
    DEFAULT_MINIMIZE_ON_GAME_LAUNCH = False
    DEFAULT_AUTO_CHECK_UPDATE = True
    DEFAULT_PLAYER_NAME = "Steve"

    def __init__(self, base_dir: Optional[str] = None):
        """
        初始化配置

        Args:
            base_dir: 基础目录,默认为根据平台自动检测
        """
        # 获取平台特定路径
        platform_paths = _get_platform_paths()
        
        # 如果手动指定了 base_dir，则覆盖自动检测的路径（仅非 Linux 平台有效）
        if base_dir is not None and platform.system().lower() != "linux":
            self.base_dir = Path(base_dir)
            self.minecraft_dir = self.base_dir / ".minecraft"
            self.log_file = self.base_dir / "latest.log"
            self.config_file = self.base_dir / "config.json"
        else:
            self.base_dir = platform_paths['base_dir']
            self.minecraft_dir = platform_paths['minecraft_dir']
            self.log_file = platform_paths['log_file']
            self.config_file = platform_paths['config_file']

        # 下载配置
        self.download_threads = 4
        self.chunk_size = 8192

        # 镜像源配置
        self.mirror_enabled = self.DEFAULT_MIRROR_ENABLED

        # 启动行为配置
        self.minimize_on_game_launch = self.DEFAULT_MINIMIZE_ON_GAME_LAUNCH

        # 更新配置
        self.auto_check_update = self.DEFAULT_AUTO_CHECK_UPDATE

        # 玩家配置
        self.player_name = self.DEFAULT_PLAYER_NAME
        self.skin_path: Optional[str] = None

        # 日志配置
        self.log_level = logzero.INFO

        # 净读 AI Token
        self.jdz_token: Optional[str] = None

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
            if "player_name" in data:
                self.player_name = data["player_name"]
            if "skin_path" in data:
                self.skin_path = data["skin_path"]
            if "jdz_token" in data:
                self.jdz_token = data["jdz_token"]

            logger.info(f"配置已加载: 镜像源={'启用' if self.mirror_enabled else '禁用'}, 启动后最小化={'启用' if self.minimize_on_game_launch else '禁用'}, 自动检查更新={'启用' if self.auto_check_update else '禁用'}, 玩家名={self.player_name}")

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
                "player_name": self.player_name,
                "skin_path": self.skin_path,
                "jdz_token": self.jdz_token,
            }
            with open(self.config_file, "w", encoding="utf-8") as f:
                f.write(_json_dumps(data, indent=2, ensure_ascii=False))
            logger.info("配置已保存")
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")

    def ensure_directories(self) -> None:
        """确保必要的目录存在"""
        import platform
        
        system = platform.system().lower()
        
        # 创建 Minecraft 目录
        self.minecraft_dir.mkdir(parents=True, exist_ok=True)
        
        # 创建基础目录
        self.base_dir.mkdir(parents=True, exist_ok=True)
        
        # Linux 特殊处理：确保 /etc/fmcl 和 /var/log/fmcl 存在
        if system == "linux":
            config_dir = self.config_file.parent
            log_dir = self.log_file.parent
            
            try:
                # 尝试创建配置目录（可能需要 sudo）
                config_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"配置目录已确保: {config_dir}")
            except PermissionError:
                logger.warning(f"无权限创建配置目录: {config_dir}")
                logger.warning(f"请运行: sudo mkdir -p {config_dir} && sudo chown $USER:$USER {config_dir}")
            except Exception as e:
                logger.error(f"创建配置目录失败: {e}")
            
            try:
                # 尝试创建日志目录（可能需要 sudo）
                log_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"日志目录已确保: {log_dir}")
            except PermissionError:
                logger.warning(f"无权限创建日志目录: {log_dir}")
                logger.warning(f"请运行: sudo mkdir -p {log_dir} && sudo chown $USER:$USER {log_dir}")
            except Exception as e:
                logger.error(f"创建日志目录失败: {e}")

    def get_versions_dir(self) -> Path:
        """获取版本目录路径"""
        return self.minecraft_dir / "versions"

    def __repr__(self) -> str:
        return f"Config(base_dir={self.base_dir}, minecraft_dir={self.minecraft_dir}, mirror={'ON' if self.mirror_enabled else 'OFF'}, minimize={'ON' if self.minimize_on_game_launch else 'OFF'}, auto_update={'ON' if self.auto_check_update else 'OFF'})"


# 全局配置实例
config = Config()
