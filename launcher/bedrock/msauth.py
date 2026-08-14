"""微软账户 OAuth（设备代码流）——获取用于 Xbox 认证的 access_token

对齐 BedrockBoot 的 MsaDeviceCodeClient（client_id/scope/端点一致）：
1. 请求设备代码（login.live.com/oauth20_connect.srf）
2. 用户打开验证页输入代码
3. 轮询换取 access_token / refresh_token

凭证缓存：登录成功后 access_token/refresh_token 经 FMCL secure_storage
加密持久化（<base_dir>/bedrock_msa_cache.json），后续启动优先用缓存
自动刷新，无需重复登录。
"""

import json
import time
import webbrowser
from pathlib import Path
from typing import Callable, Dict, Optional

import requests
from logzero import logger

from launcher.bedrock.source import USER_AGENT

MSA_CLIENT_ID = "0000000048183522"
MSA_SCOPE = "service::user.auth.xboxlive.com::MBI_SSL"
MSA_CONNECT_URL = "https://login.live.com/oauth20_connect.srf"
MSA_TOKEN_URL = "https://login.live.com/oauth20_token.srf"

POLL_INTERVAL = 5
POLL_TIMEOUT = 900

CACHE_FILE_NAME = "bedrock_msa_cache.json"
_ACCESS_TOKEN_LIFETIME = 3600  # 默认 access_token 有效期（秒），用于过期判断
_cache_dir: Optional[Path] = None


class MsaAuthError(RuntimeError):
    """微软账户认证错误"""


def _post_form(url: str, fields: Dict[str, str], timeout: int = 30) -> Dict:
    resp = requests.post(url, data=fields, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def request_device_code() -> Dict:
    """请求设备代码，返回 {device_code, user_code, verification_uri, interval, expires_in}"""
    try:
        data = _post_form(
            MSA_CONNECT_URL,
            {
                "client_id": MSA_CLIENT_ID,
                "scope": MSA_SCOPE,
                "response_type": "device_code",
            },
        )
    except requests.RequestException as e:
        raise MsaAuthError(f"请求设备代码失败: {e}") from e
    if "device_code" not in data:
        raise MsaAuthError(f"设备代码响应异常: {data.get('error', 'unknown')} {data.get('error_description', '')}")
    return data


def poll_device_code(
    device_code: str,
    interval: int = POLL_INTERVAL,
    timeout: int = POLL_TIMEOUT,
    status_cb: Optional[Callable[[str], None]] = None,
) -> Dict:
    """轮询设备代码授权结果，返回 {access_token, refresh_token, expires_in}"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data = _post_form(
                MSA_TOKEN_URL,
                {
                    "client_id": MSA_CLIENT_ID,
                    "grant_type": "device_code",
                    "device_code": device_code,
                },
            )
        except requests.RequestException as e:
            if status_cb:
                status_cb(f"轮询失败，重试中: {e}")
            time.sleep(max(1, interval))
            continue
        error = data.get("error")
        if error is None:
            if "access_token" not in data:
                raise MsaAuthError("令牌响应缺少 access_token")
            return data
        if error == "authorization_pending":
            if status_cb:
                status_cb("等待用户在浏览器中完成登录...")
            time.sleep(max(1, interval))
            continue
        if error == "authorization_declined":
            raise MsaAuthError("用户拒绝了登录")
        if error == "expired_token":
            raise MsaAuthError("登录代码已过期，请重试")
        raise MsaAuthError(f"设备代码轮询失败: {error} {data.get('error_description', '')}")
    raise MsaAuthError("登录超时（15 分钟），请重试")


def login_with_device_code(
    status_cb: Optional[Callable[[str], None]] = None,
    open_browser: bool = True,
    cache: bool = True,
) -> Dict:
    """完整设备代码登录流程：请求代码 → 打开浏览器 → 轮询

    Args:
        status_cb: 状态回调（提示用户输入代码等）
        open_browser: 是否自动打开验证页面
        cache: 登录成功后是否缓存凭证（默认 True）

    Returns:
        {access_token, refresh_token, expires_in}

    Raises:
        MsaAuthError: 登录失败或取消
    """
    device = request_device_code()
    verification_uri = device.get("verification_uri") or "https://www.microsoft.com/link"
    user_code = device.get("user_code", "")
    if status_cb:
        status_cb(f"请打开 {verification_uri} 并输入代码 {user_code} 完成微软账户登录")
    if open_browser:
        try:
            webbrowser.open(verification_uri)
        except Exception as e:
            logger.warning(f"打开浏览器失败: {e}")
    result = poll_device_code(
        device["device_code"],
        interval=int(device.get("interval", POLL_INTERVAL)),
        status_cb=status_cb,
    )
    if cache:
        save_credentials(result.get("access_token", ""), result.get("refresh_token", ""))
    return result


def refresh_access_token(refresh_token: str) -> Optional[Dict]:
    """用 refresh_token 刷新 access_token"""
    try:
        data = _post_form(
            MSA_TOKEN_URL,
            {
                "client_id": MSA_CLIENT_ID,
                "scope": MSA_SCOPE,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
        )
        if "access_token" in data:
            return data
    except requests.RequestException as e:
        logger.warning(f"刷新令牌失败: {e}")
    return None


# ─── 凭证缓存 ─────────────────────────────────────────────


def set_cache_dir(directory: Optional[Path]) -> None:
    """设置凭证缓存目录（默认使用 FMCL config.base_dir 或当前目录）"""
    global _cache_dir
    _cache_dir = Path(directory) if directory else None


def get_cache_file() -> Path:
    """获取凭证缓存文件路径"""
    if _cache_dir is not None:
        return _cache_dir / CACHE_FILE_NAME
    try:
        from config import config

        return Path(config.base_dir) / CACHE_FILE_NAME
    except Exception:
        return Path.cwd() / CACHE_FILE_NAME


def save_credentials(access_token: str, refresh_token: str = "") -> bool:
    """加密保存凭证到缓存文件（v2 格式：强制加密，字段带版本标记）

    注意：必须无条件加密。不能用 is_encrypted 判断——微软账户 token 本身
    是 base64 风格（gAAAA...），is_encrypted 会误判为"已加密"导致明文落盘。

    Args:
        access_token: 微软账户 access_token
        refresh_token: refresh_token（用于后续自动刷新）

    Returns:
        是否保存成功（加密不可用时返回 False 但不抛异常）
    """
    if not access_token:
        return False
    try:
        from secure_storage import encrypt_token

        data = {
            "format_version": 2,
            "access_token": encrypt_token(access_token),
            "refresh_token": encrypt_token(refresh_token) if refresh_token else "",
            "saved_at": int(time.time()),
        }
        # 加密失败时拒绝写入明文（token 已损坏时宁可下次重新登录）
        if not data["access_token"] or (refresh_token and not data["refresh_token"]):
            logger.error("微软账户凭证加密失败，已放弃写入缓存（避免明文落盘）")
            return False
        cache_file = get_cache_file()
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        logger.info(f"微软账户凭证已缓存: {cache_file}")
        return True
    except Exception as e:
        logger.warning(f"缓存微软账户凭证失败: {e}")
        return False


def load_credentials() -> Dict:
    """读取缓存的凭证，返回 {access_token, refresh_token, saved_at}（可能为空）

    格式兼容：
    - v2（format_version=2）：字段为 Fernet 密文，解密失败视为损坏 → 丢弃
    - 旧格式（无版本标记，历史 bug 误存明文）：优先尝试 Fernet 解密，
      失败则按明文使用（MSA token 以 gAAAA 开头，与 Fernet 前缀无法区分，
      只能以"能否解密成功"为准），并自动迁移重写为 v2 加密格式。
    """
    try:
        cache_file = get_cache_file()
        if not cache_file.exists():
            return {}
        data = json.loads(cache_file.read_text(encoding="utf-8"))
        from secure_storage import decrypt_token

        is_v2 = int(data.get("format_version", 1)) >= 2

        def read_field(key: str) -> str:
            value = str(data.get(key, ""))
            if not value:
                return ""
            plain = decrypt_token(value)
            if plain and plain != value:
                return plain  # Fernet 解密成功（含 v2 与旧格式中恰好加密过的字段）
            if is_v2:
                logger.warning(f"微软账户凭证 {key} 解密失败（可能已损坏），已丢弃")
                return ""
            return value  # 旧格式：解密失败视为历史误存的明文

        result: Dict = {}
        access_token = read_field("access_token")
        refresh_token = read_field("refresh_token")
        if access_token:
            result["access_token"] = access_token
        if refresh_token:
            result["refresh_token"] = refresh_token
        if data.get("saved_at"):
            result["saved_at"] = int(data["saved_at"])

        # 旧格式明文缓存：自动迁移为 v2 加密格式（仅当确有有效凭证）
        if not is_v2 and (access_token or refresh_token):
            save_credentials(access_token, refresh_token)
        return result
    except Exception as e:
        logger.warning(f"读取缓存的微软账户凭证失败: {e}")
        return {}


def clear_credentials() -> None:
    """删除缓存的凭证文件"""
    try:
        cache_file = get_cache_file()
        if cache_file.exists():
            cache_file.unlink()
            logger.info(f"已清除微软账户凭证缓存: {cache_file}")
    except Exception as e:
        logger.warning(f"清除微软账户凭证缓存失败: {e}")


def get_cached_access_token(status_cb: Optional[Callable[[str], None]] = None) -> str:
    """获取缓存的可用 access_token（有 refresh_token 时自动刷新）

    Args:
        status_cb: 状态回调

    Returns:
        可用的 access_token；无缓存或刷新失败返回空字符串
    """
    creds = load_credentials()
    if not creds:
        return ""
    # 优先用 refresh_token 刷新（access_token 一般 1 小时过期）
    if creds.get("refresh_token"):
        if status_cb:
            status_cb("正在刷新微软账户令牌...")
        data = refresh_access_token(creds["refresh_token"])
        if data and data.get("access_token"):
            # 刷新成功，顺带更新缓存（可能返回新的 refresh_token）
            new_refresh = data.get("refresh_token") or creds.get("refresh_token", "")
            save_credentials(data["access_token"], new_refresh)
            return data["access_token"]
        if status_cb:
            status_cb("刷新令牌失败，可能需要重新登录")
        return ""
    # 无 refresh_token：access_token 仍在有效期（1 小时内）时直接使用
    saved_at = creds.get("saved_at", 0)
    if saved_at and time.time() - saved_at < _ACCESS_TOKEN_LIFETIME - 60:
        return creds.get("access_token", "")
    return ""
