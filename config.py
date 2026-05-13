"""配置文件管理模块"""
import os
import platform
from pathlib import Path
from typing import Optional

import logzero
from logzero import logger

from secure_storage import encrypt_token, decrypt_token, set_key_dir

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
    DEFAULT_LANGUAGE = "zh_CN"

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

        # 账号系统配置
        self.accounts_file: Optional[str] = None
        self.current_account_id: Optional[str] = None
        self.microsoft_client_id: str = "00000000402b5328"

        # 旧配置迁移标记
        self._account_migration_done: bool = False

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
            if "microsoft_client_id" in data:
                self.microsoft_client_id = data["microsoft_client_id"]
            if "account_migration_done" in data:
                self._account_migration_done = data["account_migration_done"]

            logger.info(f"配置已加载: 镜像源={'启用' if self.mirror_enabled else '禁用'}, 启动后最小化={'启用' if self.minimize_on_game_launch else '禁用'}, 自动检查更新={'启用' if self.auto_check_update else '禁用'}, 玩家名={self.player_name}, 语言={self.language}")

        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")

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
                "microsoft_client_id": self.microsoft_client_id,
                "account_migration_done": self._account_migration_done,
            }
            content = _json_dumps(data, indent=2, ensure_ascii=False)
            # 原子写入：先写临时文件，再重命名，防止写入过程中崩溃导致配置文件损坏
            fd, tmp_path = tempfile.mkstemp(dir=str(self.config_file.parent), suffix='.tmp')
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
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

    def migrate_accounts(self) -> bool:
        """旧配置自动迁移：将旧 player_name 迁移为离线账号"""
        if self._account_migration_done:
            return False

        from launcher.account import init_account_system, get_account_system, create_offline_account, AccountType

        account_system = init_account_system(self.base_dir)

        existing = account_system.get_accounts_by_type(AccountType.OFFLINE)
        if existing:
            self._account_migration_done = True
            self.save_config()
            return False

        old_name = self.player_name
        if old_name and old_name != "Steve":
            logger.info(f"\u6B63\u5728\u5C06\u65E7\u914D\u7F6E\u8FC1\u79FB\u4E3A\u8D26\u53F7: {old_name}")
            account = create_offline_account(old_name)
            account_system.add_account(account)
            account_system.set_current_account(account.id)
        elif not account_system.accounts:
            default_account = create_offline_account("Steve")
            account_system.add_account(default_account)
            account_system.set_current_account(default_account.id)

        self._account_migration_done = True
        self.save_config()
        logger.info("\u65E7\u914D\u7F6E\u8FC1\u79FB\u5B8C\u6210")
        return True

    def __repr__(self) -> str:
        return f"Config(base_dir={self.base_dir}, minecraft_dir={self.minecraft_dir}, mirror={'ON' if self.mirror_enabled else 'OFF'}, minimize={'ON' if self.minimize_on_game_launch else 'OFF'}, auto_update={'ON' if self.auto_check_update else 'OFF'})"


# 全局配置实例
config = Config()
