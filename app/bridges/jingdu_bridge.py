"""净读账号 QML 桥（阶段 3 任务 3.21 设置里的登录）。

链路：

    qml/components/settings/FmJingduAccountSection.qml
        -> Jingdu.login / logout / refreshInfo
    app/bridges/jingdu_bridge.py（本模块）
        -> services/jingdu_account_service.py（HTTP / 配置落盘）

网络在 TaskRunner 的 worker 里跑；worker 只返回普通字典，真正写 QObject 属性与
配置的动作全部在主线程 _deliver() 里。登录失败按服务给出的 kind 选 i18n 文案，
不把 HTTP 响应正文原样丢给用户。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PySide6.QtCore import Property, QObject, Qt, Signal, Slot

from services.i18n_service import _
from services.jingdu_account_service import JingduAccountError, JingduAccountService

logger = logging.getLogger("app.bridges.jingdu_bridge")

REGISTER_URL = "https://jingdu.org/register"


class JingduBridge(QObject):
    """上下文属性 Jingdu。"""

    changed = Signal()
    statusMessage = Signal(str, str)
    posted = Signal("QVariantMap")

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._service: Optional[JingduAccountService] = None
        self._tasks: Any = None
        self._handles: Dict[str, Any] = {}
        self._closing = False

        self._logged_in = False
        self._username = ""
        self._busy = False
        self._error_text = ""
        self._status_text = ""
        self._status_level = "info"
        self._user_info: Dict[str, Any] = {}

        self.posted.connect(self._deliver, Qt.QueuedConnection)

    # ─── 装配 ───────────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        self._context = context
        self._tasks = getattr(context, "tasks", None) if context is not None else None
        if context is not None:
            getter = getattr(context, "try_get", None)
            if callable(getter):
                try:
                    self._service = getter("jingdu_account")
                except Exception as e:  # noqa: BLE001
                    logger.warning("取净读账号服务失败: %s", e)
        self.refresh()

    def detach(self) -> None:
        self._closing = True
        for key, handle in list(self._handles.items()):
            try:
                handle.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._handles.clear()

    # ─── 只读属性 ───────────────────────────────────────────

    @Property(bool, notify=changed)
    def available(self) -> bool:
        return self._service is not None

    @Property(bool, notify=changed)
    def loggedIn(self) -> bool:  # noqa: N802
        return self._logged_in

    @Property(str, notify=changed)
    def username(self) -> str:
        return self._username

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=changed)
    def errorText(self) -> str:  # noqa: N802
        return self._error_text

    @Property(str, notify=changed)
    def statusText(self) -> str:  # noqa: N802
        return self._status_text

    @Property(str, notify=changed)
    def statusLevel(self) -> str:  # noqa: N802
        return self._status_level

    @Property("QVariantMap", notify=changed)
    def userInfo(self) -> Dict[str, Any]:  # noqa: N802
        return dict(self._user_info)

    @Property(str, constant=True)
    def registerUrl(self) -> str:  # noqa: N802
        return REGISTER_URL

    # ─── 刷新当前状态 ───────────────────────────────────────

    @Slot()
    def refresh(self) -> None:  # noqa: N802
        if self._service is None:
            self._logged_in = False
            self._username = ""
            self._user_info = {}
            self.changed.emit()
            return
        token = self._service.current_token()
        self._logged_in = bool(token)
        self._username = self._service.current_username()
        self._user_info = self._service.cached_info()
        self.changed.emit()

    # ─── 登录 / 退出 / 刷新信息 ─────────────────────────────

    @Slot(str, str)
    def login(self, username: str, password: str) -> None:  # noqa: N802
        if self._service is None or self._busy:
            return
        name = str(username or "").strip()
        secret = str(password or "")
        if not name or not secret:
            self._set_error(_("please_enter_username_password"))
            return
        self._busy = True
        self._error_text = ""
        self._status_text = _("logging_in") + "..."
        self._status_level = "info"
        self.changed.emit()

        def work() -> Dict[str, Any]:
            try:
                result = self._service.login(name, secret)
            except JingduAccountError as e:
                return {
                    "ok": False,
                    "kind": e.kind,
                    "detail": e.detail,
                    "status": e.status,
                }
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "kind": "unknown", "detail": str(e), "status": 0}
            token = str(result.get("token") or "")
            info = result.get("info")
            if not isinstance(info, dict):
                fetched = self._fetch_result(token)
                if fetched.get("expired"):
                    return {
                        "ok": False,
                        "kind": "token_expired",
                        "detail": str(fetched.get("error", "") or ""),
                        "status": int(fetched.get("status", 0) or 0),
                    }
                info = fetched.get("info")
            return {
                "ok": True,
                "token": token,
                "info": dict(info) if isinstance(info, dict) else {},
                "username": name,
            }

        self._submit(
            "jingdu.login",
            work,
            lambda result: self._post({"kind": "login-done", "result": result}),
            lambda error: self._post({"kind": "login-error", "error": str(error)}),
        )

    @Slot()
    def logout(self) -> None:  # noqa: N802
        if self._service is None or self._busy:
            return
        ok, error = self._service.logout()
        self._busy = False
        self._logged_in = False
        self._username = ""
        self._user_info = {}
        self._error_text = ""
        if ok:
            self._status_text = _("netread_logged_out")
            self._status_level = "info"
        else:
            self._status_text = _("netread_save_failed", error=error)
            self._status_level = "warning"
        self.changed.emit()
        self.statusMessage.emit(self._status_text, self._status_level)

    @Slot()
    def refreshInfo(self) -> None:  # noqa: N802
        if self._service is None or self._busy:
            return
        token = self._service.current_token()
        if not token:
            self.refresh()
            return
        self._busy = True
        self._error_text = ""
        self._status_text = _("account_fetching")
        self._status_level = "info"
        self.changed.emit()

        def work() -> Any:
            return self._fetch_result(token)

        self._submit(
            "jingdu.info",
            work,
            lambda result: self._post({"kind": "info-done", "result": result}),
            lambda error: self._post({"kind": "info-error", "error": str(error)}),
        )

    def _fetch_result(self, token: str) -> Dict[str, Any]:
        """优先用服务的新结构化接口；旧替身只有 fetch_user_info 时降级。"""
        if self._service is None:
            return {"ok": False, "info": None, "expired": False, "status": 0, "error": "service unavailable"}
        fn = getattr(self._service, "fetch_user_info_result", None)
        if callable(fn):
            return dict(fn(token) or {})
        info = self._service.fetch_user_info(token)
        return {"ok": bool(info), "info": info, "expired": False, "status": 0, "error": ""}

    # ─── 任务线程 → 主线程 ──────────────────────────────────

    def _submit(self, key: str, work: Any, done: Any, failed: Any) -> None:
        if self._tasks is None:
            try:
                result = work()
            except Exception as e:  # noqa: BLE001
                failed(e)
            else:
                done(result)
            return
        try:
            handle = self._tasks.submit(work, name=key, on_done=done, on_error=failed)
        except Exception as e:  # noqa: BLE001
            failed(e)
            return
        self._handles[key] = handle

    def _post(self, payload: Dict[str, Any]) -> None:
        if self._closing:
            return
        try:
            self.posted.emit(payload)
        except RuntimeError:
            pass

    def _deliver(self, payload: Dict[str, Any]) -> None:
        if self._closing:
            return
        kind = str(payload.get("kind", "") or "")
        if kind == "login-done":
            self._handles.pop("jingdu.login", None)
            self._finish_login(dict(payload.get("result") or {}))
            return
        if kind == "login-error":
            self._handles.pop("jingdu.login", None)
            self._finish_login({"ok": False, "kind": "unknown", "detail": str(payload.get("error", ""))})
            return
        if kind == "info-done":
            self._handles.pop("jingdu.info", None)
            self._finish_info(dict(payload.get("result") or {}))
            return
        if kind == "info-error":
            self._handles.pop("jingdu.info", None)
            self._finish_info_error(str(payload.get("error", "")))
            return

    def _finish_login(self, result: Dict[str, Any]) -> None:
        self._busy = False
        if not result.get("ok"):
            message = self._error_message(
                str(result.get("kind", "") or ""),
                str(result.get("detail", "") or ""),
                int(result.get("status", 0) or 0),
            )
            self._error_text = message
            self._status_text = _("netread_login_failed", error=message)
            self._status_level = "error"
            self.changed.emit()
            self.statusMessage.emit(self._status_text, self._status_level)
            return

        token = str(result.get("token") or "")
        info = result.get("info") if isinstance(result.get("info"), dict) else {}
        name = str(result.get("username") or "")
        if self._service is None:
            save_ok, save_error = False, "service unavailable"
        else:
            save_ok, save_error = self._service.save_session(token, info, name)
        self._logged_in = bool(token)
        self._user_info = dict(info)
        self._username = str((info or {}).get("username") or name or "")
        self._error_text = ""
        if save_ok:
            self._status_text = _("netread_login_success")
            self._status_level = "success"
        else:
            self._status_text = _("netread_save_failed", error=save_error)
            self._status_level = "warning"
        self.changed.emit()
        self.statusMessage.emit(self._status_text, self._status_level)

    def _finish_info(self, result: Dict[str, Any]) -> None:
        self._busy = False
        if result.get("expired"):
            self._expire_session()
            return
        info = result.get("info") if result.get("ok") else None
        if isinstance(info, dict) and info:
            if self._service is not None:
                self._service.cache_info(info)
            self._user_info = dict(info)
            username = str(info.get("username") or "")
            if username:
                self._username = username
            self._error_text = ""
            self._status_text = _("account_refresh_success")
            self._status_level = "success"
        else:
            self._error_text = _("netread_info_empty")
            self._status_text = _("account_refresh_failed", error=self._error_text)
            self._status_level = "error"
        self.changed.emit()
        self.statusMessage.emit(self._status_text, self._status_level)

    def _expire_session(self) -> None:
        """401：清掉本地 Token，回到登录表单，并给出明确的“登录已过期”提示。"""
        if self._service is not None:
            try:
                self._service.logout()
            except Exception as e:  # noqa: BLE001 - 清不掉也要让用户重新登录
                logger.warning("清理过期净读会话失败: %s", e)
        self._busy = False
        self._logged_in = False
        self._username = ""
        self._user_info = {}
        self._error_text = _("netread_token_expired")
        self._status_text = self._error_text
        self._status_level = "error"
        self.changed.emit()
        self.statusMessage.emit(self._status_text, self._status_level)

    def _finish_info_error(self, error: str) -> None:
        self._busy = False
        self._error_text = _("netread_info_empty")
        self._status_text = _("account_refresh_failed", error=error or self._error_text)
        self._status_level = "error"
        self.changed.emit()
        self.statusMessage.emit(self._status_text, self._status_level)

    # ─── 错误文案 ───────────────────────────────────────────

    @staticmethod
    def _error_message(kind: str, detail: str, status: int) -> str:
        if kind == "empty":
            return _("please_enter_username_password")
        if kind == "bad_credentials":
            return _("netread_login_bad_credentials")
        if kind == "http_error":
            return _("netread_login_http_error", status=status)
        if kind == "network":
            return _("netread_login_network_error", error=detail or "")
        if kind == "no_token":
            return _("netread_login_no_token")
        if kind == "token_expired":
            return _("netread_token_expired")
        return _("netread_login_failed", error=detail or kind)

    def _set_error(self, text: str) -> None:
        self._error_text = str(text or "")
        self._status_text = self._error_text
        self._status_level = "error"
        self.changed.emit()
        self.statusMessage.emit(self._status_text, self._status_level)


__all__ = ["JingduBridge", "REGISTER_URL"]
