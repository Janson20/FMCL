"""音乐源通用工具 - 加密/签名/HTTP会话"""
import hashlib
import random
import string
from typing import Dict, Optional

import requests

# ─── User-Agent 池 ────────────────────────────────────

UA_POOL = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36 Edg/130.0.0.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) Gecko/20100101 Firefox/133.0",
]

MOBILE_UA = (
    "Mozilla/5.0 (Linux; Android 14; EBG-AN10 Build/HONOREBG-AN10; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 "
    "Chrome/131.0.6778.135 Mobile Safari/537.36"
)


def create_session(headers: Optional[Dict] = None, use_mobile_ua: bool = False) -> requests.Session:
    """创建带UA的requests.Session"""
    session = requests.Session()
    ua = MOBILE_UA if use_mobile_ua else random.choice(UA_POOL)
    default_headers = {
        "User-Agent": ua,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Accept-Encoding": "gzip, deflate",
    }
    if headers:
        default_headers.update(headers)
    session.headers.update(default_headers)
    return session


# ─── 通用哈希 ────────────────────────────────────────

def md5(text: str) -> str:
    """MD5 哈希 (小写hex)"""
    return hashlib.md5(text.encode()).hexdigest()


def sha1(text: str) -> str:
    """SHA1 哈希 (小写hex)"""
    return hashlib.sha1(text.encode()).hexdigest()


def sha256(text: str) -> str:
    """SHA256 哈希 (小写hex)"""
    return hashlib.sha256(text.encode()).hexdigest()


# ─── AES 加密 (网易云音乐) ────────────────────────────

def aes_encrypt_ecb(data: bytes, key: bytes) -> bytes:
    """AES-128-ECB 加密 (NoPadding)"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        # 回退到 pycryptodome
        from Crypto.Cipher import AES
        cipher = AES.new(key, AES.MODE_ECB)
        return cipher.encrypt(_pad(data, 16))
    cipher = Cipher(algorithms.AES(key), modes.ECB())
    encryptor = cipher.encryptor()
    return encryptor.update(_pad(data, 16)) + encryptor.finalize()


def aes_encrypt_cbc(data: bytes, key: bytes, iv: bytes) -> bytes:
    """AES-128-CBC 加密"""
    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        from Crypto.Cipher import AES
        cipher = AES.new(key, AES.MODE_CBC, iv)
        return cipher.encrypt(_pad(data, 16))
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    return encryptor.update(_pad(data, 16)) + encryptor.finalize()


def _pad(data: bytes, block_size: int) -> bytes:
    """PKCS7 填充"""
    padding_len = block_size - (len(data) % block_size)
    return data + bytes([padding_len] * padding_len)


# ─── RSA 加密 (网易云音乐) ─────────────────────────────

WY_PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDgtQn2JZ34ZC28NWYpAUd98iZ3
7BUrX/aKzmFbt7clFSs6sXqHauqKWqdtLkF2KexO40H1YTX8z2lSgBBOAxLsvakl
V8k4cBFK9snQXE9/DDaFt6Rr7iVZMldczhC0JNgTz+SHXT6CBHuX3e9SdB1Ua44o
ncaTWz7OBGLbCiK45wIDAQAB
-----END PUBLIC KEY-----"""


def rsa_encrypt(data: bytes) -> bytes:
    """RSA 公钥加密 (NoPadding, 128字节块)"""
    try:
        from cryptography.hazmat.primitives import serialization, hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        from cryptography.hazmat.backends import default_backend
        pub_key = serialization.load_pem_public_key(WY_PUBLIC_KEY.encode(), backend=default_backend())
        return pub_key.encrypt(data, padding.PKCS1v15())
    except ImportError:
        from Crypto.PublicKey import RSA
        from Crypto.Cipher import PKCS1_v1_5
        key = RSA.import_key(WY_PUBLIC_KEY)
        cipher = PKCS1_v1_5.new(key)
        return cipher.encrypt(data)


# ─── 网易云 eapi 加密 ─────────────────────────────────

WY_EAPI_KEY = b"e82ckenh8dichen8"
WY_PRESET_KEY = b"0CoJUm6Qyw8W8jud"
WY_IV = b"0102030405060708"
WY_LINUXAPI_KEY = b"rFgB&h#%2?^eDg:Q"
_BASE62 = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def _random_hex(n: int) -> str:
    return "".join(random.choices("0123456789abcdef", k=n))


def wy_weapi(data: dict) -> Dict[str, str]:
    """网易云 weapi 加密"""
    import json
    text = json.dumps(data)
    secret_key = "".join(random.choices(_BASE62, k=16)).encode()
    first = aes_encrypt_cbc(text.encode(), WY_PRESET_KEY, WY_IV)
    second = aes_encrypt_cbc(first, secret_key, WY_IV)
    enc_sec_key = rsa_encrypt(secret_key[::-1]).hex()
    return {
        "params": _base64_encode(second),
        "encSecKey": enc_sec_key,
    }


def wy_eapi(url: str, data: dict) -> Dict[str, str]:
    """网易云 eapi 加密"""
    import json
    text = json.dumps(data)
    message = f"nobody{url}use{text}md5forencrypt"
    digest = md5(message)
    raw = f"{url}-36cd479b6b5-{text}-36cd479b6b5-{digest}"
    encrypted = aes_encrypt_ecb(raw.encode(), WY_EAPI_KEY)
    return {"params": encrypted.hex().upper()}


def wy_linuxapi(data: dict) -> Dict[str, str]:
    """网易云 linuxapi 加密"""
    import json
    text = json.dumps(data)
    encrypted = aes_encrypt_ecb(text.encode(), WY_LINUXAPI_KEY)
    return {"eparams": encrypted.hex().upper()}


# ─── QQ音乐 zzc 签名 ──────────────────────────────────

_PART1_IDX = [23, 14, 6, 36, 16, 40, 7, 19]
_PART2_IDX = [16, 1, 32, 12, 19, 27, 8, 5]
_SCRAMBLE = [89, 39, 179, 150, 218, 82, 58, 252, 177, 52, 186, 123, 120, 64, 242, 133, 143, 161, 121, 179]


def tx_zzc_sign(text: str) -> str:
    """QQ音乐 zzc 签名算法"""
    h = sha1(text)
    part1 = "".join(h[i] if i < len(h) else "" for i in _PART1_IDX)
    part2 = "".join(h[i] if i < len(h) else "" for i in _PART2_IDX)
    part3 = bytes(_SCRAMBLE[i] ^ int(h[i * 2:i * 2 + 2], 16) for i in range(20))
    b64part = _base64_encode(part3).replace("/", "").replace("+", "").replace("=", "")
    return f"zzc{part1}{b64part}{part2}".lower()


# ─── 咪咕签名 ─────────────────────────────────────────

MG_DEVICE_ID = "963B7AA0D21511ED807EE5846EC87D20"
MG_SIGN_KEY = "6cdc72a439cef99a3418d2a78aa28c73"


def mg_create_sign(timestamp: int, keyword: str) -> str:
    """咪咕搜索签名"""
    return md5(f"{keyword}{MG_SIGN_KEY}yyapp2d16148780a1dcc7408e06336b98cfd50{MG_DEVICE_ID}{timestamp}")


# ─── Base64 ──────────────────────────────────────────

def _base64_encode(data: bytes) -> str:
    """标准 Base64 编码"""
    import base64
    return base64.b64encode(data).decode()


# ─── 文本处理 ────────────────────────────────────────

def decode_name(raw: str) -> str:
    """解码可能被编码的名称"""
    if not raw:
        return raw
    try:
        if raw.startswith("&#"):
            import html
            return html.unescape(raw)
    except Exception:
        pass
    return raw


def format_singer(raw: str) -> str:
    """格式化歌手名 (替换分隔符)"""
    if not raw:
        return ""
    return raw.replace("&", "、").replace(";", "、").replace("/", "、")
