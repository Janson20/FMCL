"""配置文件管理模块"""

import os
import platform
from pathlib import Path
from typing import Any, Dict, Optional

import logzero
from logzero import logger

from secure_storage import decrypt_token, encrypt_token, set_key_dir

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


def _is_writable_dir(path: Path) -> bool:
    """检测目录是否可写（通过创建临时探针文件测试）

    Args:
        path: 待检测目录

    Returns:
        目录存在且可写返回 True，否则返回 False
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".fmcl_write_probe_{os.getpid()}"
        probe.write_bytes(b"ok")
        try:
            probe.unlink()
        except Exception:
            pass
        return True
    except (PermissionError, OSError):
        return False


def _get_user_data_dir() -> Path:
    """获取用户可写的数据目录（cwd 不可写时的回退位置）

    Windows: %LOCALAPPDATA%\\FMCL
    macOS:   ~/Library/Application Support/FMCL
    Linux:   ~/.fmcl（与常规路径一致）
    """
    system = platform.system().lower()
    if system == "windows":
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            return Path(local_appdata) / "FMCL"
        return Path.home() / "AppData" / "Local" / "FMCL"
    if system == "darwin":
        return Path.home() / "Library" / "Application Support" / "FMCL"
    return Path.home() / ".fmcl"


# 回退模式下需要迁移的用户数据项（密钥文件优先，保证 config.json 解密可用）
_MIGRATABLE_ITEMS = [
    ".fmcl_key",
    ".fmcl_salt",
    "config.json",
    "accounts.json",
    "achievements.db",
    "latest.log",
    "latest_structured.log",
    ".minecraft",
    "themes",
    "plugins",
    "backups",
    "logs",
    "authlib-injector",
]


def _migrate_user_data_if_needed(src: Path, dst: Path) -> bool:
    """将旧目录中的 FMCL 数据复制迁移到新目录（仅复制，不删除原目录）

    触发条件：回退模式首次启动，新目录尚无 FMCL 数据，且旧目录存在
    config.json 或 .minecraft。旧目录（如 Program Files）对普通用户
    通常不可写，因此只读复制并保留原目录。

    Args:
        src: 旧数据目录（通常是不可写的 cwd）
        dst: 新数据目录（用户可写）

    Returns:
        是否发生了迁移
    """
    if src == dst or not src.exists():
        return False
    if not (src / "config.json").exists() and not (src / ".minecraft").exists():
        return False
    if dst.exists() and (dst / "config.json").exists():
        return False

    import shutil

    dst.mkdir(parents=True, exist_ok=True)
    migrated: list = []
    for item in _MIGRATABLE_ITEMS:
        s = src / item
        d = dst / item
        if not s.exists() or d.exists():
            continue
        try:
            if s.is_dir():
                shutil.copytree(s, d)
            else:
                shutil.copy2(s, d)
            migrated.append(item)
        except Exception as e:
            # 复制失败（如部分文件无读权限）时清理残留，下次启动重试
            shutil.rmtree(d, ignore_errors=True)
            logger.warning(f"迁移数据失败 ({item}): {e}")
    if migrated:
        logger.info(f"已将旧数据从 {src} 迁移到 {dst}: {', '.join(migrated)}")
    return bool(migrated)


def _get_platform_paths():
    """
    根据操作系统返回平台特定的路径配置

    Returns:
        dict: {
            'base_dir': 基础目录（Windows/macOS: 可写的当前目录, 否则用户数据目录;
                        Linux: ~/.fmcl）,
            'minecraft_dir': Minecraft 目录,
            'log_file': 日志文件路径,
            'config_file': 配置文件路径,
            'used_fallback': 是否回退到了用户数据目录（cwd 不可写）
        }
    """
    system = platform.system().lower()

    if system == "linux":
        # Linux: 遵循 XDG Base Directory 规范
        home = Path.home()
        xdg_config_home = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
        xdg_data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))

        # 配置文件: ~/.config/fmcl/config.json
        config_dir = xdg_config_home / "fmcl"
        config_file = config_dir / "config.json"

        # 日志文件: ~/.local/share/fmcl/fmcl.log
        log_dir = xdg_data_home / "fmcl"
        log_file = log_dir / "fmcl.log"

        # Minecraft 目录: ~/.minecraft
        minecraft_dir = home / ".minecraft"

        # 基础目录: ~/.fmcl (用于其他运行时文件)
        base_dir = home / ".fmcl"

        return {
            "base_dir": base_dir,
            "minecraft_dir": minecraft_dir,
            "log_file": log_file,
            "config_file": config_file,
            "used_fallback": False,
        }
    else:
        # Windows/macOS: 优先使用当前工作目录（便携模式，数据跟随 exe）。
        # 当 cwd 不可写时（如安装到 Program Files），回退到用户可写目录，
        # 否则启动器必须管理员运行才能创建数据目录（issue #10）。
        cwd = Path.cwd()
        used_fallback = not _is_writable_dir(cwd)
        base_dir = _get_user_data_dir() if used_fallback else cwd
        return {
            "base_dir": base_dir,
            "minecraft_dir": base_dir / ".minecraft",
            "log_file": base_dir / "latest.log",
            "config_file": base_dir / "config.json",
            "used_fallback": used_fallback,
        }


class Config:
    """启动器配置类"""

    # 默认配置
    DEFAULT_MIRROR_ENABLED = True
    DEFAULT_MINIMIZE_ON_GAME_LAUNCH = False
    DEFAULT_AUTO_CHECK_UPDATE = True
    DEFAULT_PLAYER_NAME = "Steve"
    DEFAULT_LANGUAGE = "zh_CN"

    # 配置错误回调（由 UI 层注册，用于显示错误弹窗）
    _error_callback: Any = None

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
            self.base_dir = platform_paths["base_dir"]
            self.minecraft_dir = platform_paths["minecraft_dir"]
            self.log_file = platform_paths["log_file"]
            self.config_file = platform_paths["config_file"]

        # 旧数据迁移：cwd 不可写回退到用户数据目录时，
        # 将原 cwd 中已有的 FMCL 数据复制到新目录（仅首次运行）
        if base_dir is None and platform_paths.get("used_fallback"):
            _migrate_user_data_if_needed(Path.cwd(), self.base_dir)

        # 设置密钥存储目录
        set_key_dir(self.base_dir)

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

        # 净读 AI 用户名（加密存储，禁止修改）
        self._jdz_username: Optional[str] = None

        # 净读 AI 用户信息缓存（内存缓存，不持久化）
        self._jdz_user_info: Optional[dict] = None

        # AI 隐私同意
        self.ai_privacy_consent: bool = False

        # 使用条款同意（Minecraft EULA + 净读协议）
        self.terms_consent: bool = False

        # 语言设置
        self.language: str = self.DEFAULT_LANGUAGE

        # 主题设置
        self.theme_name: str = "default"
        self.accent_color: Optional[str] = None
        self.dynamic_version_theme: bool = False

        # Java 运行时配置
        self.java_mode: str = "auto"
        self.java_custom_path: Optional[str] = None

        # 备份配置
        self.backup_dir: Optional[str] = None
        self.backup_compress_level: int = 6
        self.backup_max_per_world: int = 10
        self.backup_restore_mode: str = "rename"
        self.backup_auto_launch: bool = False
        self.backup_auto_exit: bool = False

        # MultiMC 整合包配置
        self.mmc_configurations: Dict[str, dict] = {}

        # 账号系统配置
        self.accounts_file: Optional[str] = None
        self.current_account_id: Optional[str] = None

        # 旧配置迁移标记
        self._account_migration_done: bool = False

        # 音乐播放器状态
        self.music_state: dict = {}

        # 从配置文件加载
        self._load_config()

    @property
    def jdz_username(self) -> Optional[str]:
        return self._jdz_username

    @jdz_username.setter
    def jdz_username(self, value: Optional[str]) -> None:
        self._jdz_username = value

    @property
    def jdz_user_info(self) -> Optional[dict]:
        return self._jdz_user_info

    @jdz_user_info.setter
    def jdz_user_info(self, value: Optional[dict]) -> None:
        self._jdz_user_info = value

    @classmethod
    def set_error_callback(cls, callback):
        """设置配置错误回调（由 UI 层注册，用于显示错误弹窗）

        Args:
            callback: 接受 (title: str, message: str) 的回调函数
        """
        cls._error_callback = callback

    @classmethod
    def _notify_error(cls, title: str, message: str):
        """通知配置错误"""
        if cls._error_callback:
            try:
                cls._error_callback(title, message)
            except Exception:
                pass

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
                stored = data["jdz_token"]
                if stored:
                    self.jdz_token = decrypt_token(stored)
                else:
                    self.jdz_token = None
            if "jdz_username" in data:
                stored = data["jdz_username"]
                if stored:
                    self._jdz_username = decrypt_token(stored)
                else:
                    self._jdz_username = None
            if "backup_dir" in data:
                self.backup_dir = data["backup_dir"]
            if "backup_compress_level" in data:
                self.backup_compress_level = data["backup_compress_level"]
            if "backup_max_per_world" in data:
                self.backup_max_per_world = data["backup_max_per_world"]
            if "backup_restore_mode" in data:
                self.backup_restore_mode = data["backup_restore_mode"]
            if "backup_auto_launch" in data:
                self.backup_auto_launch = data["backup_auto_launch"]
            if "backup_auto_exit" in data:
                self.backup_auto_exit = data["backup_auto_exit"]
            if "language" in data:
                self.language = data["language"]
            if "theme_name" in data:
                self.theme_name = data["theme_name"]
            if "accent_color" in data:
                self.accent_color = data["accent_color"]
            if "dynamic_version_theme" in data:
                self.dynamic_version_theme = data["dynamic_version_theme"]
            if "java_mode" in data:
                self.java_mode = data["java_mode"]
            if "java_custom_path" in data:
                self.java_custom_path = data["java_custom_path"]
            if "ai_privacy_consent" in data:
                self.ai_privacy_consent = data["ai_privacy_consent"]
            if "terms_consent" in data:
                self.terms_consent = data["terms_consent"]
            if "accounts_file" in data:
                self.accounts_file = data["accounts_file"]
            if "current_account_id" in data:
                self.current_account_id = data["current_account_id"]
            if "account_migration_done" in data:
                self._account_migration_done = data["account_migration_done"]
            if "music_state" in data:
                self.music_state = data["music_state"]

            logger.info(
                f"配置已加载: 镜像源={'启用' if self.mirror_enabled else '禁用'}, 启动后最小化={'启用' if self.minimize_on_game_launch else '禁用'}, 自动检查更新={'启用' if self.auto_check_update else '禁用'}, 玩家名={self.player_name}, 语言={self.language}"
            )

        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            Config._notify_error("配置加载失败", f"配置文件可能已损坏，已恢复默认设置:\n{e}")

    def save_config(self) -> None:
        """保存配置到文件（使用原子写入防止文件损坏）"""
        import tempfile

        try:
            data = {
                "mirror_enabled": self.mirror_enabled,
                "download_threads": self.download_threads,
                "minimize_on_game_launch": self.minimize_on_game_launch,
                "auto_check_update": self.auto_check_update,
                "player_name": self.player_name,
                "skin_path": self.skin_path,
                "jdz_token": encrypt_token(self.jdz_token) if self.jdz_token else None,
                "jdz_username": encrypt_token(self._jdz_username) if self._jdz_username else None,
                "language": self.language,
                "theme_name": self.theme_name,
                "accent_color": self.accent_color,
                "dynamic_version_theme": self.dynamic_version_theme,
                "ai_privacy_consent": self.ai_privacy_consent,
                "terms_consent": self.terms_consent,
                "backup_dir": self.backup_dir,
                "backup_compress_level": self.backup_compress_level,
                "backup_max_per_world": self.backup_max_per_world,
                "backup_restore_mode": self.backup_restore_mode,
                "backup_auto_launch": self.backup_auto_launch,
                "backup_auto_exit": self.backup_auto_exit,
                "java_mode": self.java_mode,
                "java_custom_path": self.java_custom_path,
                "accounts_file": self.accounts_file,
                "current_account_id": self.current_account_id,
                "account_migration_done": self._account_migration_done,
                "music_state": self.music_state,
            }
            content = _json_dumps(data, indent=2, ensure_ascii=False)
            # 原子写入：先写临时文件，再重命名，防止写入过程中崩溃导致配置文件损坏
            fd, tmp_path = tempfile.mkstemp(dir=str(self.config_file.parent), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(content)
                os.replace(tmp_path, str(self.config_file))
            except Exception:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
                raise
            logger.info("配置已保存")
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            Config._notify_error("配置保存失败", f"无法保存配置文件:\n{e}")

    def ensure_directories(self) -> None:
        """确保必要的目录存在（失败时记录日志并重新抛出）"""
        for d in (self.minecraft_dir, self.base_dir, self.config_file.parent, self.log_file.parent):
            try:
                d.mkdir(parents=True, exist_ok=True)
            except (PermissionError, OSError) as e:
                logger.error(f"创建目录失败: {d}: {e}")
                Config._notify_error("目录创建失败", f"无法创建目录:\n{d}\n\n请检查磁盘空间与目录权限。\n错误: {e}")
                raise

    def get_versions_dir(self) -> Path:
        """获取版本目录路径"""
        return self.minecraft_dir / "versions"

    def get_mmc_config_path(self, version_id: str) -> Path:
        """获取 MultiMC 整合包配置文件的路径

        Args:
            version_id: 版本 ID

        Returns:
            mmc_config.json 的完整路径
        """
        return self.get_versions_dir() / version_id / "mmc_config.json"

    def save_mmc_config(self, version_id: str, config_data: dict) -> None:
        """保存 MultiMC 整合包配置到版本目录

        Args:
            version_id: 版本 ID
            config_data: 配置字典（ModpackConfiguration.to_dict() 的输出）
        """
        import json

        config_path = self.get_mmc_config_path(version_id)
        try:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存 MultiMC 配置失败 ({version_id}): {e}")
            Config._notify_error("配置保存失败", f"无法保存整合包配置:\n{e}")
        self.mmc_configurations[version_id] = config_data
        logger.info(f"MultiMC 配置已保存: {version_id}")

    def load_mmc_config(self, version_id: str) -> Optional[dict]:
        """加载 MultiMC 整合包配置

        Args:
            version_id: 版本 ID

        Returns:
            配置字典或 None
        """
        import json

        config_path = self.get_mmc_config_path(version_id)
        if not config_path.is_file():
            return None
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"加载 MultiMC 配置失败 {version_id}: {e}")
            return None

    def migrate_accounts(self) -> bool:
        """旧配置自动迁移：将旧 player_name 迁移为离线账号"""
        from launcher.account import AccountType, create_offline_account, get_account_system, init_account_system

        account_system = init_account_system(self.base_dir)

        if self._account_migration_done:
            return False

        existing = account_system.get_accounts_by_type(AccountType.OFFLINE)
        if existing:
            self._account_migration_done = True
            self.save_config()
            return False

        old_name = self.player_name
        if old_name and old_name != "Steve":
            logger.info(f"\u6b63\u5728\u5c06\u65e7\u914d\u7f6e\u8fc1\u79fb\u4e3a\u8d26\u53f7: {old_name}")
            account = create_offline_account(old_name)
            account_system.add_account(account)
            account_system.set_current_account(account.id)
        elif not account_system.accounts:
            default_account = create_offline_account("Steve")
            account_system.add_account(default_account)
            account_system.set_current_account(default_account.id)

        self._account_migration_done = True
        self.save_config()
        logger.info("\u65e7\u914d\u7f6e\u8fc1\u79fb\u5b8c\u6210")
        return True

    def __repr__(self) -> str:
        return f"Config(base_dir={self.base_dir}, minecraft_dir={self.minecraft_dir}, mirror={'ON' if self.mirror_enabled else 'OFF'}, minimize={'ON' if self.minimize_on_game_launch else 'OFF'}, auto_update={'ON' if self.auto_check_update else 'OFF'})"


# 全局配置实例
config = Config()
