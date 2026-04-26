"""安全存储模块 - 加密敏感数据存储

使用 Fernet (AES-128-CBC + HMAC-SHA256) 对 Token 等敏感数据进行加密存储。

密钥管理策略：
- 首次使用时在 <base_dir>/.fmcl_key 生成随机密钥并保存
- 后续始终从密钥文件读取，确保同一台机器上密钥稳定
- 密钥文件可随 config.json 一起拷贝到新机器实现迁移
- 可选密码派生：通过环境变量 FMCL_ENC_KEY_PASSWORD 设置密码
- 后向兼容：保留硬件指纹密钥作为解密回退
"""
import base64
import hashlib
import os
import platform
from pathlib import Path
from typing import Optional

from logzero import logger


_KEY_FILE_NAME = ".fmcl_key"
_key_dir: Optional[Path] = None


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

    # 3. 生成新密钥并保存
    try:
        from cryptography.fernet import Fernet
        new_key = Fernet.generate_key()
        key_file.parent.mkdir(parents=True, exist_ok=True)
        key_file.write_bytes(new_key)
        logger.info(f"已生成新密钥并保存到: {key_file}")
        return new_key
    except Exception as e:
        logger.error(f"生成密钥失败: {e}")
        return None


def _derive_key_from_password(password: str) -> bytes:
    """从用户密码派生 Fernet 密钥

    使用 PBKDF2-HMAC-SHA256 进行密钥派生，迭代 600000 次。
    盐值使用固定应用标识，确保同一密码始终派生相同密钥。

    Args:
        password: 用户密码

    Returns:
        Fernet 兼容的 32 字节 base64 密钥
    """
    salt = b"FMCL_ENC_KEY_SALT_v1"
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600000)
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
        加密后的 base64 字符串，加密失败返回 None
    """
    if not plaintext:
        return None
    try:
        cipher = _get_cipher()
        if cipher:
            return cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")
        logger.warning("cryptography 未安装，使用 base64 编码存储（安全性较低）")
        return base64.b64encode(plaintext.encode("utf-8")).decode("utf-8")
    except Exception as e:
        logger.error(f"加密 Token 失败: {e}")
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

    # 2. 环境变量密码派生密钥
    env_password = os.environ.get("FMCL_ENC_KEY_PASSWORD")
    if env_password:
        candidate_keys.append(("env_password", _derive_key_from_password(env_password)))

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
