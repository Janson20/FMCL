"""凭据加密存储。

设计目标（相对 FMCL ``secure_storage.py`` 的改进）
------------------------------------------------
1. **带版本的自描述信封**：文件头写明 ``format_version`` / ``cipher`` / ``kdf`` /
   ``iterations`` / ``salt`` / ``nonce``，换算法或换参数时仍可读旧文件。
2. **认证加密**：AES-256-GCM，密文被篡改会直接解密失败，不会像 Fernet + base64
   回退那样把损坏数据当明文返回。
3. **绝不静默重建密钥**：密钥文件存在但损坏时抛 :class:`VaultError` 并保留原文件，
   不会覆盖导致旧凭据永久不可解。
4. **原子落盘**：先写临时文件再 ``os.replace``，避免中途崩溃留下半截文件。
5. **密钥与密文强制分离**，两者都在程序目录下，保证绿色便携。

密钥装载优先级
--------------
1. 环境变量 ``FUSION_MUSIC_MASTER_PASSWORD`` → PBKDF2-HMAC-SHA256(600k) 派生（真加密，
   换机可用，但忘记主密码即丢失凭据）。
2. ``data/keys/master.key``：32 字节随机主密钥，按**便携模式**决定存法：

   ==================  ==========================================  ==============
   开关                文件形态                                    换台电脑
   ==================  ==========================================  ==============
   便携模式**开启**    ``PORT`` + 32 字节裸密钥                    能解开
   便携模式**关闭**    ``DPA1`` + DPAPI（当前用户）包裹的密钥      解不开
   ==================  ==========================================  ==============

   便携模式关掉时用 DPAPI（用户作用域）包裹，因此该文件被拷到别的机器 / 别的
   Windows 用户下无法解开；打开后直接存裸密钥，整个程序目录拷到哪儿都能解开，
   账号保持登录。非 Windows 平台没有 DPAPI，只能存裸密钥（本来就与机器无关）。

安全边界（务必知悉）
--------------------
密钥文件与密文同处一个可拷贝目录中，因此本方案的定位是「防止凭据以明文形式暴露」
（配置文件被查看、同步、误发、日志泄漏），**不是**对能读取该目录的本地攻击者的防护。
便携模式额外放弃了「拷到别的机器就解不开」这层保护 —— 换来的是换机不用重新登录。
需要更强保护时请启用主密码模式。
"""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import secrets
import stat
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

from .. import paths
from ..jsonio import atomic_write_bytes

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
CIPHER = "aes-256-gcm"
KDF_HKDF = "hkdf-sha256"
KDF_PBKDF2 = "pbkdf2-sha256"
PBKDF2_ITERATIONS = 600_000
KEY_BYTES = 32
NONCE_BYTES = 12
SALT_BYTES = 32
HKDF_INFO = b"FusionMusicPlayer/credentials/v1"
MASTER_PASSWORD_ENV = "FUSION_MUSIC_MASTER_PASSWORD"

#: 密钥文件头：便携模式下裸存的密钥（见文件头那张表）
DPAPI_MAGIC = b"DPA1"
PORTABLE_MAGIC = b"PORT"

#: 便携模式的**偏好**。密钥文件是「当前事实」，这个是「下次生成密钥时怎么办」——
#: 两者分开是因为密钥可能还没生成（用户没登录过），而开关得先把意图记住。
#: 由配置驱动（见 AccountManager._sync_portable），模块级是因为主密钥装载发生在
#: 类之外，而且 vault 这个单例在 import 时就建好了。
_portable_preference = False


def set_portable_preference(enabled: bool) -> None:
    global _portable_preference
    _portable_preference = bool(enabled)


def portable_preference() -> bool:
    return _portable_preference


def dpapi_available() -> bool:
    """当前平台有没有 DPAPI（决定密钥能不能绑在本机）。"""
    return _dpapi_available()


class VaultError(RuntimeError):
    """凭据加解密或密钥装载失败。"""


# ──────────────────────────────────────────────────────────────
# DPAPI（仅 Windows）
# ──────────────────────────────────────────────────────────────


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi_protect(data: bytes) -> Optional[bytes]:
    """用当前 Windows 用户的 DPAPI 凭据加密数据。"""
    if not _dpapi_available():
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_in = _DataBlob(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception as e:  # pragma: no cover - 平台相关
        logger.debug("DPAPI 加密失败: %s", e)
        return None


def _dpapi_unprotect(data: bytes) -> Optional[bytes]:
    """解开 DPAPI 保护的数据。"""
    if not _dpapi_available():
        return None
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_in = _DataBlob(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
        blob_out = _DataBlob()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(blob_out.pbData)
    except Exception as e:  # pragma: no cover - 平台相关
        logger.debug("DPAPI 解密失败: %s", e)
        return None


# ──────────────────────────────────────────────────────────────
# 文件权限
# ──────────────────────────────────────────────────────────────


def _restrict_permissions(path: Path) -> None:
    """把文件权限收紧到「仅当前用户」。

    Windows 上用 ``/inheritance:r`` 去掉继承来的 ACE（这样同一台机器上的其它
    用户读不到），再只授予当前用户权限。**必须给完全控制（F）而不是只读+写**：
    ``(R,W)`` 不含删除权限，会导致用户无法删除自己的数据目录。
    """
    try:
        if os.name == "posix":
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            return
        user = os.environ.get("USERNAME") or os.environ.get("USER") or ""
        if not user:
            return
        proc = subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F"],
            capture_output=True,
            timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode != 0:
            logger.debug(
                "收紧文件权限失败（非致命）: %s -> %s",
                path,
                (proc.stderr or proc.stdout or b"").decode("utf-8", "ignore").strip(),
            )
    except Exception as e:
        logger.debug("收紧文件权限异常（非致命）: %s - %s", path, e)


def _atomic_write(path: Path, data: bytes) -> None:
    """原子写入密钥 / 盐，写完顺手收紧权限。

    原子部分与别处同一份（:func:`app.jsonio.atomic_write_bytes`），这里多一步
    Windows ACL 收紧：凭据解不开的密钥文件落在盘上，只有当前用户该读得到。
    """
    atomic_write_bytes(path, data)
    _restrict_permissions(path)


def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))


# ──────────────────────────────────────────────────────────────
# 主密钥装载
# ──────────────────────────────────────────────────────────────


class _MasterKey:
    """已装载的主密钥及其来源描述。"""

    __slots__ = ("raw", "source", "fingerprint")

    def __init__(self, raw: bytes, source: str):
        self.raw = raw
        self.source = source
        import hashlib

        self.fingerprint = hashlib.sha256(raw).hexdigest()[:16]


def _load_or_create_master_key() -> _MasterKey:
    """装载主密钥；不存在则创建。绝不覆盖已存在但损坏的密钥文件。"""
    password = os.environ.get(MASTER_PASSWORD_ENV)
    if password:
        salt = _load_or_create_salt()
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=KEY_BYTES,
            salt=salt,
            iterations=PBKDF2_ITERATIONS,
        )
        return _MasterKey(kdf.derive(password.encode("utf-8")), "password")

    key_path = paths.key_file()
    if key_path.exists():
        raw = key_path.read_bytes()
        if raw:
            # 1) 裸密钥：非 Windows 就是这么存的，也是老版本在 DPAPI 不可用时的回退。
            #    先按**长度**判断，免得一段碰巧以 "PORT" 开头的裸密钥被误认成便携格式。
            if len(raw) == KEY_BYTES:
                return _MasterKey(raw, "keyfile")
            # 2) 便携模式：PORT + 裸密钥（换台电脑照样解得开）
            if raw[:4] == PORTABLE_MAGIC and len(raw) == KEY_BYTES + 4:
                return _MasterKey(raw[4:], "portable")
            # 3) DPAPI 包裹（默认，绑当前 Windows 用户）
            if raw[:4] == DPAPI_MAGIC:
                unwrapped = _dpapi_unprotect(raw[4:])
                if unwrapped is None:
                    raise VaultError(
                        "密钥文件由另一个 Windows 用户或另一台机器保护，无法解开。\n"
                        f"路径: {key_path}\n"
                        "已保存的登录状态只属于那台机器，无法在这里恢复。\n"
                        "出路有两条：\n"
                        "  1. 在设置 → 账号里开启「便携模式」，然后重新登录一次 —— "
                        "以后整个程序目录拷到哪台电脑都能保持登录；\n"
                        f"  2. 手动删掉上面这个文件后重启，再重新登录。"
                    )
                if len(unwrapped) == KEY_BYTES:
                    return _MasterKey(unwrapped, "dpapi")
        raise VaultError(
            f"密钥文件已损坏或格式无法识别: {key_path}\n"
            "为避免旧凭据永久不可解，程序不会自动覆盖它。"
            "请手动移走该文件后重新启动并重新登录。"
        )

    raw = secrets.token_bytes(KEY_BYTES)
    source = _write_master_key(key_path, raw, portable=portable_preference())
    return _MasterKey(raw, source)


def _write_master_key(key_path: Path, raw: bytes, *, portable: bool) -> str:
    """按指定模式写主密钥，返回实际落盘的来源标记。

    ``portable=False`` 但平台没有 DPAPI 时只能存裸密钥 —— 返回 ``"keyfile"``
    如实告诉调用方「没能绑到本机」，界面据此提示，而不是假装已经关掉了便携模式。
    """
    if portable:
        _atomic_write(key_path, PORTABLE_MAGIC + raw)
        return "portable"
    blob = _dpapi_protect(raw)
    if blob is None:
        _atomic_write(key_path, raw)
        return "keyfile"
    _atomic_write(key_path, DPAPI_MAGIC + blob)
    return "dpapi"


def _load_or_create_salt() -> bytes:
    """主密码模式下用于 PBKDF2 的盐（存放在密钥目录，可随程序目录迁移）。"""
    salt_path = paths.key_file().parent / "master.salt"
    if salt_path.exists():
        data = salt_path.read_bytes()
        if len(data) >= 16:
            return data
        raise VaultError(f"盐文件已损坏: {salt_path}")
    salt = secrets.token_bytes(SALT_BYTES)
    _atomic_write(salt_path, salt)
    return salt


# ──────────────────────────────────────────────────────────────
# Vault
# ──────────────────────────────────────────────────────────────


class CredentialVault:
    """加密凭据仓库，默认落盘到 ``<数据目录>/credentials.enc``。

    默认路径**每次用的时候才解析**（``self.path``），不在这里定死：数据目录可以
    被 ``--data-dir`` 或 ``FUSION_MUSIC_HOME`` 改，而模块级单例是在 ``import``
    时就建好的 —— 早先写死成实例属性，导致 ``--data-dir`` 下凭据留在老位置、
    主密钥却跟着新目录走，两者分了家（拷贝目录带不走登录状态）。密钥文件那边
    本来就是动态解析的（``paths.key_file()``），这里对齐它。
    """

    def __init__(self, path: Optional[Path] = None):
        self._explicit_path = Path(path) if path else None
        self._master: Optional[_MasterKey] = None
        self._cache: Optional[Dict[str, Any]] = None
        self._cache_path: Optional[Path] = None
        self._loaded = False

    # ── 内部 ────────────────────────────────────────────────

    def _master_key(self) -> _MasterKey:
        if self._master is None:
            self._master = _load_or_create_master_key()
        return self._master

    def _derive_data_key(self, salt: bytes, kdf_name: str, iterations: int) -> bytes:
        master = self._master_key()
        if kdf_name == KDF_PBKDF2:
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(), length=KEY_BYTES, salt=salt, iterations=iterations
            )
            return kdf.derive(master.raw)
        hkdf = HKDF(algorithm=hashes.SHA256(), length=KEY_BYTES, salt=salt, info=HKDF_INFO)
        return hkdf.derive(master.raw)

    # ── 公开 API ────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._explicit_path or paths.credentials_file()

    def exists(self) -> bool:
        return self.path.exists()

    def save(self, payload: Dict[str, Any]) -> bool:
        """加密并原子写入凭据。加密失败时**不落盘**，返回 False。"""
        try:
            salt = secrets.token_bytes(SALT_BYTES)
            kdf_name = KDF_PBKDF2 if os.environ.get(MASTER_PASSWORD_ENV) else KDF_HKDF
            iterations = PBKDF2_ITERATIONS if kdf_name == KDF_PBKDF2 else 0
            data_key = self._derive_data_key(salt, kdf_name, iterations)
            nonce = secrets.token_bytes(NONCE_BYTES)

            plaintext = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            # 信封头部作为 AAD 参与认证，防止降级篡改
            envelope_meta = {
                "format_version": FORMAT_VERSION,
                "cipher": CIPHER,
                "kdf": kdf_name,
                "iterations": iterations,
                "salt": _b64e(salt),
                "nonce": _b64e(nonce),
                "key_source": self._master_key().source,
                "saved_at": int(time.time()),
            }
            aad = json.dumps(envelope_meta, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, aad)

            envelope = dict(envelope_meta)
            envelope["payload"] = _b64e(ciphertext)
            _atomic_write(self.path, json.dumps(envelope, ensure_ascii=False, indent=2).encode("utf-8"))
            self._cache = dict(payload)
            self._cache_path = self.path
            self._loaded = True
            logger.info("凭据已加密保存: %s (key_source=%s)", self.path, envelope_meta["key_source"])
            return True
        except VaultError:
            raise
        except Exception as e:
            logger.error("凭据加密保存失败，已放弃写入以避免明文落盘: %s", e)
            return False

    def load(self, *, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """解密读取凭据。文件不存在或解密失败返回 None（绝不返回密文）。"""
        # 缓存只在**同一个文件**上有效：路径是用的时候才解析的（见 self.path），
        # 数据目录一旦改了就绝不能把上一个目录的内容当成这一个的答案。
        if use_cache and self._loaded and self._cache_path == self.path:
            return dict(self._cache) if self._cache else None
        if not self.path.exists():
            self._loaded = True
            self._cache = None
            self._cache_path = self.path
            return None
        try:
            envelope = json.loads(self.path.read_text(encoding="utf-8"))
            version = int(envelope.get("format_version", 0))
            if version != FORMAT_VERSION:
                raise VaultError(f"不支持的凭据文件版本: {version}")
            if envelope.get("cipher") != CIPHER:
                raise VaultError(f"不支持的加密算法: {envelope.get('cipher')}")

            salt = _b64d(envelope["salt"])
            nonce = _b64d(envelope["nonce"])
            iterations = int(envelope.get("iterations") or 0)
            data_key = self._derive_data_key(salt, envelope.get("kdf", KDF_HKDF), iterations)

            meta = {
                k: envelope[k]
                for k in (
                    "format_version",
                    "cipher",
                    "kdf",
                    "iterations",
                    "salt",
                    "nonce",
                    "key_source",
                    "saved_at",
                )
                if k in envelope
            }
            aad = json.dumps(meta, sort_keys=True, separators=(",", ":")).encode("utf-8")
            plaintext = AESGCM(data_key).decrypt(nonce, _b64d(envelope["payload"]), aad)
            payload = json.loads(plaintext.decode("utf-8"))
            self._cache = payload
            self._cache_path = self.path
            self._loaded = True
            return dict(payload)
        except VaultError:
            raise
        except Exception as e:
            logger.warning("凭据解密失败（文件可能损坏或密钥不匹配）: %s", e)
            self._loaded = True
            self._cache = None
            self._cache_path = self.path
            return None

    def delete(self) -> None:
        """删除凭据文件（保留主密钥，便于以后换账号复用）。"""
        self._cache = None
        self._cache_path = self.path
        self._loaded = True
        try:
            if self.path.exists():
                self.path.unlink()
                logger.info("凭据文件已删除: %s", self.path)
        except OSError as e:
            logger.warning("删除凭据文件失败: %s", e)

    def key_source(self) -> str:
        """密钥来源描述。未真正需要密钥时不创建它，保持数据目录干净。"""
        if os.environ.get(MASTER_PASSWORD_ENV):
            return "password"
        if not paths.key_file().exists():
            return "未生成"
        try:
            return self._master_key().source
        except VaultError:
            return "不可用"

    def key_usable(self) -> bool:
        """主密钥在本机解得开吗。密钥还没生成也算「可用」（迟早会正常生成）。"""
        if os.environ.get(MASTER_PASSWORD_ENV):
            return True
        if not paths.key_file().exists():
            return True
        try:
            self._master_key()
            return True
        except VaultError:
            return False

    def portable(self) -> bool:
        """凭据能不能跟着程序目录换机器解开（便携模式 / 主密码模式都算）。

        密钥还没生成时按**偏好**回答：开关开着就该说「是」，否则界面会在用户
        还没登录过的时候显示成关着的。
        """
        if os.environ.get(MASTER_PASSWORD_ENV):
            return True
        key_path = paths.key_file()
        if not key_path.exists():
            return portable_preference()
        try:
            raw = key_path.read_bytes()
        except OSError:
            return False
        if len(raw) == KEY_BYTES:
            return True
        return raw[:4] == PORTABLE_MAGIC and len(raw) == KEY_BYTES + 4

    def dpapi_bound(self) -> bool:
        """密钥此刻是不是由本机 DPAPI 绑着（拷走就解不开）。"""
        try:
            raw = paths.key_file().read_bytes()
        except OSError:
            return False
        return raw[:4] == DPAPI_MAGIC

    def set_portable(self, enabled: bool) -> str:
        """切换便携模式，返回落盘后的密钥来源；解不开密钥时抛 :class:`VaultError`。

        **只换密钥文件的存法，不换密钥本身** —— 数据密钥没变，已经保存的凭据
        照样能解开，所以这个开关在登录状态下可以随时拨动，不需要重新登录。

        密钥还没生成时只记下偏好（不提前造一个密钥出来），等第一次真正需要密钥
        时按偏好创建。
        """
        enabled = bool(enabled)
        set_portable_preference(enabled)

        key_path = paths.key_file()
        if not key_path.exists():
            return "portable" if enabled else "dpapi"

        master = self._master_key()  # 解不开会在这里抛 VaultError
        if master.source == "password":
            # 主密码派生出来的密钥本来就与机器无关，密钥文件是死是活都不影响
            return master.source

        source = _write_master_key(key_path, master.raw, portable=enabled)
        self._master = _MasterKey(master.raw, source)
        logger.info("凭据主密钥已切换为 %s（portable=%s）", source, enabled)
        return source

    def rebuild_key(self) -> str:
        """丢弃解不开的主密钥，按便携模式偏好重新生成一个。

        只在密钥**已经解不开**时才有意义（旧凭据本来就恢复不了）。旧密钥文件
        会改名为 ``master.key.unreadable`` 留着，不做静默删除 —— 万一用户还能
        回到原来那台机器 / 那个 Windows 用户下，把文件名改回去就能救回来。
        """
        key_path = paths.key_file()
        if key_path.exists():
            backup = key_path.with_name(key_path.name + ".unreadable")
            try:
                if backup.exists():
                    backup.unlink()
                os.replace(key_path, backup)
            except OSError as e:
                raise VaultError(f"无法移走旧密钥文件（{key_path}）: {e}") from e

        raw = secrets.token_bytes(KEY_BYTES)
        source = _write_master_key(key_path, raw, portable=portable_preference())
        self._master = _MasterKey(raw, source)
        self._cache = None
        self._loaded = False

        # 旧凭据是拿旧密钥加密的，留着只会每次启动都报一次解密失败
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError as e:
            logger.warning("删除已失效的凭据文件失败: %s", e)
        logger.info("主密钥已重建（来源 %s），旧凭据已作废", source)
        return source

    def key_fingerprint(self) -> str:
        if not paths.key_file().exists() and not os.environ.get(MASTER_PASSWORD_ENV):
            return "-"
        try:
            return self._master_key().fingerprint
        except VaultError:
            return "-"

    def describe(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "exists": self.exists(),
            "key_source": self.key_source(),
            "key_fingerprint": self.key_fingerprint(),
            "portable": self.portable(),
            "dpapi_available": dpapi_available(),
            "key_usable": self.key_usable(),
            "cipher": CIPHER,
            "format_version": FORMAT_VERSION,
        }


vault = CredentialVault()
