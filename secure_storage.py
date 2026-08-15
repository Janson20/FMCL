"""安全存储模块 - 加密敏感数据存储

使用 Fernet (AES-128-CBC + HMAC-SHA256) 对 Token 等敏感数据进行加密存储。

密钥管理策略：
- 首次使用时在 <base_dir>/.fmcl_key 生成随机密钥并保存
- 使用随机盐值文件 .fmcl_salt 进行密码派生（每用户唯一）
- 密钥文件权限限制为仅所有者可访问
- 可选密码派生：通过环境变量 FMCL_ENC_KEY_PASSWORD 设置密码
- 后向兼容：保留硬件指纹密钥和固定盐值作为解密回退
"""

import base64
import hashlib
import os
import platform
import stat
from pathlib import Path
from typing import Optional

from logzero import logger

_KEY_FILE_NAME = ".fmcl_key"
_SALT_FILE_NAME = ".fmcl_salt"
# 旧版固定盐值（仅用于向后兼容解密）
_LEGACY_SALT = b"FMCL_ENC_KEY_SALT_v1"
_key_dir: Optional[Path] = None
# 错误回调（由 UI 层注册）
_error_callback = None


def set_error_callback(callback):
    """设置加密模块错误回调（由 UI 层注册，用于显示错误弹窗）

    Args:
        callback: 接受 (title: str, message: str) 的回调函数
    """
    global _error_callback
    _error_callback = callback


def _notify_error(title: str, message: str):
    """通知加密错误"""
    if _error_callback:
        try:
            _error_callback(title, message)
        except Exception:
            pass


def _get_key_dir() -> Optional[Path]:
    """获取密钥文件存储目录"""
    if _key_dir is not None:
        return _key_dir
    return None


def set_key_dir(base_dir: Path) -> None:
    """设置密钥文件存储目录

    应由 config 初始化时调用，传入 config.base_dir

    Args:
        base_dir: 基础目录，密钥文件将存放在 base_dir /.fmcl_key
    """
    global _key_dir
    _key_dir = base_dir


def _get_key_file_path() -> Path:
    """获取密钥文件路径"""
    key_dir = _get_key_dir()
    if key_dir is None:
        key_dir = Path.cwd()
    return key_dir / _KEY_FILE_NAME


def _load_or_create_key() -> Optional[bytes]:
    """从密钥文件加载密钥，若不存在则生成并保存

    新创建的密钥文件使用受限权限 (仅所有者可读)。

    Returns:
        Fernet 兼容的 32 字节 base64 密钥，失败返回 None
    """
    key_file = _get_key_file_path()

    # 1. 优先从环境变量读取密码派生密钥
    env_password = os.environ.get("FMCL_ENC_KEY_PASSWORD")
    if env_password:
        key = _derive_key_from_password(env_password)
        logger.debug("使用环境变量 FMCL_ENC_KEY_PASSWORD 派生密钥")
        return key

    # 2. 尝试从密钥文件加载
    if key_file.exists():
        try:
            key_data = key_file.read_bytes().strip()
            # 验证密钥格式（Fernet 密钥是 32 字节 base64 编码）
            decoded = base64.urlsafe_b64decode(key_data)
            if len(decoded) == 32:
                logger.debug(f"从密钥文件加载成功: {key_file}")
                return key_data
            logger.warning(f"密钥文件格式无效，将重新生成: {key_file}")
        except Exception as e:
            logger.warning(f"读取密钥文件失败，将重新生成: {e}")
            _notify_error(
                "密钥文件读取失败",
                f"密钥文件 (.fmcl_key) 可能已损坏，将生成新密钥。\n"
                f"注意：之前加密的 Token 将无法解密，需要重新登录。",
            )

    # 3. 生成新密钥并保存（受限权限）
    try:
        from cryptography.fernet import Fernet

        new_key = Fernet.generate_key()
        key_file.parent.mkdir(parents=True, exist_ok=True)

        # 使用受限权限创建密钥文件
        _write_restricted_file(key_file, new_key)

        logger.info(f"已生成新密钥并保存到: {key_file}")
        return new_key
    except Exception as e:
        logger.error(f"生成密钥失败: {e}")
        _notify_error("密钥生成失败", f"无法生成加密密钥。Token 将不会被加密存储。\n" f"错误: {e}")
        return None


def _write_restricted_file(file_path: Path, data: bytes) -> None:
    """以受限权限写入文件

    POSIX: 0o600 (仅所有者可读写)
    Windows: 通过 DACL 限制为仅当前用户
    """
    if os.name == "posix":
        fd = os.open(str(file_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    else:
        # Windows: 先创建文件再限制权限
        file_path.write_bytes(data)
        try:
            # 移除继承权限，仅保留当前用户完全控制
            import subprocess as _sp

            _sp.run(
                ["icacls", str(file_path), "/inheritance:r", "/grant:r", f"{os.getlogin()}:(R,W)"],
                capture_output=True,
                timeout=10,
                creationflags=getattr(_sp, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:
            logger.debug(f"设置 Windows DACL 失败（非致命）: {e}")


def _load_or_create_salt() -> bytes:
    """加载或创建随机盐值文件

    盐值用于 PBKDF2 密码派生，确保每个用户的盐值唯一。
    """
    key_dir = _get_key_dir()
    if key_dir is None:
        key_dir = Path.cwd()
    salt_file = key_dir / _SALT_FILE_NAME

    if salt_file.exists():
        try:
            data = salt_file.read_bytes()
            if len(data) >= 16:
                return data
        except Exception:
            pass

    # 生成 32 字节随机盐
    new_salt = os.urandom(32)
    salt_file.parent.mkdir(parents=True, exist_ok=True)
    _write_restricted_file(salt_file, new_salt)
    logger.debug(f"已生成随机盐值: {salt_file}")
    return new_salt


def _derive_key_from_password(password: str) -> bytes:
    """从用户密码派生 Fernet 密钥

    使用 PBKDF2-HMAC-SHA256 进行密钥派生，迭代 600000 次。
    盐值从 .fmcl_salt 文件加载（每用户唯一随机盐）。
    """
    salt = _load_or_create_salt()
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600000)
    return base64.urlsafe_b64encode(derived)


def _derive_key_from_password_legacy(password: str) -> bytes:
    """使用旧版固定盐值派生密钥（仅用于向后兼容解密）"""
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _LEGACY_SALT, 600000)
    return base64.urlsafe_b64encode(derived)


def _get_legacy_machine_key() -> bytes:
    """基于机器硬件指纹生成 Fernet 兼容的 32 字节密钥（后向兼容用）"""
    components = []
    try:
        components.append(platform.node())
    except Exception:
        pass
    try:
        components.append(platform.machine())
    except Exception:
        pass
    try:
        components.append(platform.processor())
    except Exception:
        pass
    try:
        import uuid

        components.append(str(uuid.getnode()))
    except Exception:
        pass
    raw = "-".join(components)
    key = hashlib.sha256(raw.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(key)


def _get_cipher():
    """获取 Fernet 密文对象（优先使用密钥文件）"""
    try:
        from cryptography.fernet import Fernet

        key = _load_or_create_key()
        if key:
            return Fernet(key)
        return None
    except ImportError:
        return None


def encrypt_token(plaintext: str) -> Optional[str]:
    """加密 Token，返回 base64 密文字符串

    Args:
        plaintext: 明文 Token

    Returns:
        加密后的 base64 字符串，加密失败返回 None（不再回退到不安全的 base64 编码）
    """
    if not plaintext:
        return None
    try:
        cipher = _get_cipher()
        if cipher:
            return cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        logger.error("cryptography 库不可用，无法加密 Token")
        _notify_error("加密不可用", "cryptography 库未安装或加载失败，Token 不会被保存。\n请安装: pip install cryptography")
        return None
    except Exception as e:
        logger.error(f"加密 Token 失败: {e}")
        _notify_error("Token 加密失败", f"无法加密 Token，配置保存时将写空值。\n" f"错误: {e}")
        return None


def decrypt_token(ciphertext: str) -> Optional[str]:
    """解密 Token

    尝试以下方式按顺序解密：
    1. 使用密钥文件密钥
    2. 使用环境变量密码派生密钥
    3. 使用旧版硬件指纹密钥（后向兼容）
    4. base64 解码回退

    Args:
        ciphertext: 加密后的 base64 字符串

    Returns:
        明文 Token，解密失败返回 None
    """
    if not ciphertext:
        return None

    from cryptography.fernet import Fernet, InvalidToken

    # 收集所有可能的密钥
    candidate_keys = []

    # 1. 密钥文件密钥
    file_key = _load_or_create_key()
    if file_key:
        candidate_keys.append(("key_file", file_key))

    # 2. 环境变量密码派生密钥（新随机盐）
    env_password = os.environ.get("FMCL_ENC_KEY_PASSWORD")
    if env_password:
        candidate_keys.append(("env_password", _derive_key_from_password(env_password)))
        # 2b. 旧版固定盐值密码派生密钥（向后兼容）
        candidate_keys.append(("env_password_legacy", _derive_key_from_password_legacy(env_password)))

    # 3. 旧版硬件指纹密钥（后向兼容）
    candidate_keys.append(("legacy_hardware", _get_legacy_machine_key()))

    # 尝试所有密钥
    for source, key in candidate_keys:
        try:
            cipher = Fernet(key)
            plaintext = cipher.decrypt(ciphertext.encode("utf-8"))
            logger.debug(f"Token 解密成功（来源: {source}）")
            return plaintext.decode("utf-8")
        except InvalidToken:
            continue
        except Exception as e:
            logger.debug(f"Token 解密尝试失败（来源: {source}）: {e}")
            continue

    # 最后尝试 base64 解码回退
    try:
        return base64.b64decode(ciphertext.encode("utf-8")).decode("utf-8")
    except Exception:
        logger.warning("所有解密方式均失败，Token 可能已损坏")
        return ciphertext


def is_encrypted(value: str) -> bool:
    """判断值是否已加密

    Args:
        value: 待检查的字符串

    Returns:
        是否已加密
    """
    if not value:
        return False
    try:
        base64.b64decode(value.encode("utf-8"))
        return True
    except Exception:
        return False
