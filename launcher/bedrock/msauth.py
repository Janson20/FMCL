"""微软账户 OAuth（设备代码流）——获取用于 Xbox 认证的 access_token

对齐 BedrockBoot 的 MsaDeviceCodeClient（client_id/scope/端点一致）：
1. 请求设备代码（login.live.com/oauth20_connect.srf）
2. 用户打开验证页输入代码
3. 轮询换取 access_token / refresh_token
"""

import time
import webbrowser
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
) -> Dict:
    """完整设备代码登录流程：请求代码 → 打开浏览器 → 轮询

    Args:
        status_cb: 状态回调（提示用户输入代码等）
        open_browser: 是否自动打开验证页面

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
    return poll_device_code(
        device["device_code"],
        interval=int(device.get("interval", POLL_INTERVAL)),
        status_cb=status_cb,
    )


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
