"""净读账号服务（设置里的净读登录）。

只做三件事：登录换 Token、读取用户信息、把 Token/用户名写回配置。它不碰 Qt，
不弹窗，不起线程；网络调用由调用方放到 TaskRunner 的 worker 里。

端点：登录 POST /api/auth/login，用户信息 GET /api/user/info，Token 写
config.jdz_token，用户信息缓存在 config.jdz_user_info（不持久化），用户名加密写
config.jdz_username。HTTP 错误归一成 JingduAccountError.kind，让 QML 能选一句
可读文案，而不是把 HTTP 401: {...} 原样丢给用户。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional, Tuple

import requests

from services.base import Service

logger = logging.getLogger(__name__)

BASE_URL = "https://jingdu.org"
LOGIN_URL = f"{BASE_URL}/api/auth/login"
USER_INFO_URL = f"{BASE_URL}/api/user/info"
USER_AGENT = "FMCL/1.0 (Minecraft Launcher; account)"
TIMEOUT_SECONDS = 15


class JingduAccountError(Exception):
    """登录失败的可读分类。kind 由桥翻译成 i18n 文案。"""

    def __init__(self, kind: str, detail: str = "", status: int = 0) -> None:
        super().__init__(detail or kind)
        self.kind = str(kind or "unknown")
        self.detail = str(detail or "")
        self.status = int(status or 0)


class JingduAccountService(Service):
    """净读 AI 账号登录 / 用户信息 / 会话落盘。"""

    name = "jingdu_account"
    label = "净读账号"

    def __init__(
        self,
        context: Any = None,
        *,
        http_post: Optional[Callable[..., Any]] = None,
        http_get: Optional[Callable[..., Any]] = None,
    ) -> None:
        super().__init__(context)
        self._http_post = http_post
        self._http_get = http_get

    # ─── HTTP 注入（测试不碰网络）────────────────────────────

    def _post(self, *args: Any, **kwargs: Any) -> Any:
        fn = self._http_post or requests.post
        return fn(*args, **kwargs)

    def _get(self, *args: Any, **kwargs: Any) -> Any:
        fn = self._http_get or requests.get
        return fn(*args, **kwargs)

    def _headers(self, token: str = "") -> Dict[str, str]:
        headers = {"User-Agent": USER_AGENT}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    # ─── 登录 ───────────────────────────────────────────────

    def login(self, username: str, password: str) -> Dict[str, Any]:
        """登录并返回 {token, info}；失败抛 JingduAccountError。

        password 不做 strip（空格可能是密码的一部分）；用户名做 strip。
        """
        name = str(username or "").strip()
        secret = str(password or "")
        if not name or not secret:
            raise JingduAccountError("empty")
        try:
            resp = self._post(
                LOGIN_URL,
                json={"username": name, "password": secret},
                headers=self._headers(),
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as e:
            raise JingduAccountError("network", str(e)) from e
        except Exception as e:  # noqa: BLE001 - 注入的 HTTP 替身也只允许可读失败
            raise JingduAccountError("network", str(e)) from e

        status = int(getattr(resp, "status_code", 0) or 0)
        data = self._json_dict(resp)
        detail = self._detail(data)
        if status == 401 and "token expired" in detail.lower():
            raise JingduAccountError("token_expired", detail, status)
        if status in (401, 403):
            raise JingduAccountError("bad_credentials", detail, status)
        if status >= 400:
            raise JingduAccountError("http_error", detail, status)
        if status == 0:
            raise JingduAccountError("network", "empty HTTP response")

        token = data.get("token") if isinstance(data, dict) else None
        info: Optional[Dict[str, Any]] = None
        if isinstance(data, dict):
            nested = data.get("data")
            if not token and isinstance(nested, dict):
                token = nested.get("token")
            candidate = data.get("user")
            if isinstance(candidate, dict):
                info = dict(candidate)
            elif isinstance(nested, dict) and ("username" in nested or "uuid" in nested):
                info = dict(nested)
        if not token:
            raise JingduAccountError("no_token", detail, status)
        return {"token": str(token), "info": info}

    def fetch_user_info_result(self, token: str) -> Dict[str, Any]:
        """读取用户信息，并把“Token 过期”作为结构化结果返回。

        401 一律按“登录已过期”处理：用户信息端点不校验密码，401 只可能是
        Token 无效/过期。返回结构：
        {"ok", "info", "expired", "status", "error"}；网络失败与普通 HTTP
        错误只返回 ok=False，不误报成过期。
        """
        if not token:
            return {"ok": False, "info": None, "expired": False, "status": 0, "error": "no token"}
        try:
            resp = self._get(USER_INFO_URL, headers=self._headers(token), timeout=TIMEOUT_SECONDS)
            status = int(getattr(resp, "status_code", 0) or 0)
            data = self._json_dict(resp)
            detail = self._detail(data)
            if status == 401:
                logger.info("净读 Token 已过期: %s", detail or "HTTP 401")
                return {
                    "ok": False,
                    "info": None,
                    "expired": True,
                    "status": status,
                    "error": detail or "Token expired",
                }
            if status >= 400:
                logger.warning("读取净读用户信息失败 HTTP %s", status)
                return {
                    "ok": False,
                    "info": None,
                    "expired": False,
                    "status": status,
                    "error": detail,
                }
            if isinstance(data, dict):
                nested = data.get("data")
                info = dict(nested) if isinstance(nested, dict) else dict(data)
            else:
                info = {}
            if not info:
                return {"ok": False, "info": None, "expired": False, "status": status, "error": ""}
            return {"ok": True, "info": info, "expired": False, "status": status, "error": ""}
        except requests.RequestException as e:
            logger.warning("读取净读用户信息网络失败: %s", e)
            return {"ok": False, "info": None, "expired": False, "status": 0, "error": str(e)}
        except Exception as e:  # noqa: BLE001 - 展示态失败不该阻断登录
            logger.warning("读取净读用户信息异常: %s", e)
            return {"ok": False, "info": None, "expired": False, "status": 0, "error": str(e)}

    def fetch_user_info(self, token: str) -> Optional[Dict[str, Any]]:
        """兼容旧调用：成功返回 info，任何失败（含过期）返回 None。"""
        result = self.fetch_user_info_result(token)
        info = result.get("info")
        return dict(info) if result.get("ok") and isinstance(info, dict) else None

    # ─── 会话读写 ───────────────────────────────────────────

    def current_token(self) -> str:
        config = self._config()
        if config is None:
            return ""
        try:
            return str(getattr(config, "jdz_token", "") or "")
        except Exception:  # noqa: BLE001
            return ""

    def current_username(self) -> str:
        config = self._config()
        if config is None:
            return ""
        try:
            info = getattr(config, "jdz_user_info", None)
            if isinstance(info, dict) and info.get("username"):
                return str(info["username"])
            return str(getattr(config, "jdz_username", "") or "")
        except Exception:  # noqa: BLE001
            return ""

    def cached_info(self) -> Dict[str, Any]:
        config = self._config()
        if config is None:
            return {}
        try:
            info = getattr(config, "jdz_user_info", None)
            return dict(info) if isinstance(info, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def cache_info(self, info: Optional[Dict[str, Any]]) -> None:
        """把用户信息放进内存缓存（旧实现也不持久化这份缓存）。"""
        config = self._config()
        if config is None or not isinstance(info, dict):
            return
        try:
            config.jdz_user_info = dict(info)
        except Exception as e:  # noqa: BLE001
            logger.warning("缓存净读用户信息失败: %s", e)

    def save_session(
        self, token: str, info: Optional[Dict[str, Any]] = None, username: str = ""
    ) -> Tuple[bool, str]:
        """把 Token / 用户名 / 用户信息写回配置。返回 (是否落盘, 错误)。"""
        config = self._config()
        if config is None:
            return False, "config unavailable"
        name = str(username or "").strip()
        if isinstance(info, dict) and info.get("username"):
            name = str(info.get("username") or "").strip()
        try:
            config.jdz_token = str(token or "")
            config.jdz_user_info = dict(info) if isinstance(info, dict) else None
            config.jdz_username = name or None
            config.save_config()
            return True, ""
        except Exception as e:  # noqa: BLE001 - 桥会如实提示"登录成功但保存失败"
            logger.error("保存净读账号失败: %s", e)
            return False, str(e)

    def logout(self) -> Tuple[bool, str]:
        """退出净读账号并清掉缓存。返回 (是否落盘, 错误)。"""
        config = self._config()
        if config is None:
            return False, "config unavailable"
        try:
            config.jdz_token = None
            config.jdz_user_info = None
            config.jdz_username = None
            config.save_config()
            return True, ""
        except Exception as e:  # noqa: BLE001
            logger.error("退出净读账号时保存失败: %s", e)
            return False, str(e)

    # ─── 内部 ───────────────────────────────────────────────

    def _config(self) -> Any:
        if self._context is None:
            return None
        try:
            return getattr(self._context, "config", None)
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _json_dict(resp: Any) -> Dict[str, Any]:
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            return {}
        return dict(data) if isinstance(data, dict) else {}

    @staticmethod
    def _detail(data: Any) -> str:
        if isinstance(data, dict):
            for key in ("message", "error", "detail"):
                value = data.get(key)
                if value:
                    return str(value)[:300]
        return ""

    @staticmethod
    def error_text(error: BaseException) -> str:
        """把服务异常转成日志/兜底文案（桥优先用 kind 走 i18n）。"""
        if isinstance(error, JingduAccountError):
            return error.detail or error.kind
        return str(error)


__all__ = [
    "BASE_URL",
    "JingduAccountError",
    "JingduAccountService",
    "LOGIN_URL",
    "USER_INFO_URL",
]
