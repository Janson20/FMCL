"""联机桥 —— QML 侧的 Online 上下文属性（阶段 3 任务 3.13）。

链路：

    qml/pages/online/OnlinePage.qml
        -> app/bridges/online_bridge.py（本模块，零业务规则）
        -> services/online_session.py（状态机与全部时序）
        -> services/online_service.py（EasyTier / 陶瓦 / 转发 / 广播 / Ping 原语）

桥只做三件事：把会话属性念给 QML、把 QML 动作转成会话调用、把后台回调变成 Qt
信号。能不能创建、什么时候重试、成员怎么排序，一条都不在这里判（迁移红线 2）。

## 线程约定

会话的观察者回调可能来自 EasyTier reader 线程、SCF 线程、定时器线程。所有事件
先进入 _pending 队列，由 posted 信号回到主线程后才 emit 页面信号 —— 与
ServersBridge / ModpackBridge 同一套（D-90 的纪律）。

日志单独留一份 5000 行缓冲：页面切走再回来时 QML 对象会重建，日志不能跟着丢；
logSnapshot() 给页面回放，logLines 只发增量。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Qt, Signal, Slot
from PySide6.QtGui import QGuiApplication

from services.i18n_service import _
from services.online_service import OnlineService
from services.online_session import (
    ENV_CHECKING,
    ENV_FAILED,
    ENV_INSTALLING,
    ENV_MISSING,
    ENV_READY,
    INSTALL_DONE,
    INSTALL_FAILED,
    INSTALL_RUNNING,
    LOG_BUFFER_MAX_LINES,
    OnlineSession,
    launcher_callbacks,
)

logger = logging.getLogger("app.bridges.online_bridge")


class OnlineBridge(QObject):
    """上下文属性 Online。"""

    envChanged = Signal()
    lobbyChanged = Signal()
    membersChanged = Signal()
    worldsChanged = Signal()
    staleChanged = Signal()
    #: 日志增量（行列表, 这批最后一行的绝对序号）。
    logLines = Signal("QVariantList", int)
    #: 底部状态条（已翻译）。
    statusMessage = Signal(str, str)
    #: Toast（标题, 正文, 级别），页面拿到后调 Dialogs.notify。
    noticeRequested = Signal(str, str, str)
    posted = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._session: Optional[OnlineSession] = None
        self._service: Optional[OnlineService] = None
        self._launcher: Any = None
        self._achievement: Any = None
        self._pending: List[Dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        self._closing = False
        self._log_lines: List[str] = []
        self._log_seq = 0
        #: 即使 _post 从主线程发起，也排到下一拍再投递：页面构造/入栈期间
        #: 同步 emit 会让 QML 在别的组件还没编译完时重算绑定，D-150 的同族
        #: 警告就是这么被撞出来的（本桥的 envChanged 实测能稳定复现）。
        self.posted.connect(self._deliver, Qt.QueuedConnection)

    # ─── 装配 ───────────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        self._context = context
        session = self._ensure_session()
        if session is not None and session.platform_supported:
            # 在 QML 加载前先把环境态定下来：页面第一次读 installButtonKey 时就是
            # 最终值，避免"检查中 → 已就绪"在页面存活期间改按钮文案/布局。
            # 这一步只读本地安装状态，不起网络线程（中继预取推迟到启动大厅）。
            try:
                session.refresh_env()
            except Exception as e:  # noqa: BLE001 - 环境检查失败不该挡住启动
                logger.warning("联机环境预检查失败: %s", e)

    def _service_of(self, name: str) -> Any:
        if self._context is None:
            return None
        try:
            return self._context.try_get(name)
        except Exception as e:  # noqa: BLE001 - 单个服务取不到不该让整页打不开
            logger.warning("联机桥取服务 %s 失败: %s", name, e)
            return None

    def _ensure_session(self) -> Optional[OnlineSession]:
        service = self._service_of("online")
        if service is None:
            service = self._service if self._service is not None else OnlineService()
        launcher = self._service_of("launcher")
        achievement = self._service_of("achievement")
        callbacks = launcher_callbacks(launcher)
        if self._session is None:
            self._service = service
            self._launcher = launcher
            self._achievement = achievement
            self._session = OnlineSession(
                service=service,
                launcher=launcher,
                achievement=achievement,
                callbacks=callbacks,
                observer=self,
            )
        else:
            self._session.rebind(
                launcher=launcher,
                achievement=achievement,
                callbacks=callbacks,
            )
            self._launcher = launcher
            self._achievement = achievement
        return self._session

    def detach(self) -> None:
        self._closing = True
        with self._pending_lock:
            self._pending.clear()
        session = self._session
        if session is not None:
            try:
                session.detach()
            except Exception as e:  # noqa: BLE001 - 退出清理失败不能挡住退出
                logger.warning("停止联机会话失败: %s", e)

    # ─── 属性 ───────────────────────────────────────────────

    @Property(bool, constant=True)
    def available(self) -> bool:  # noqa: N802
        return True

    @Property(bool, constant=True)
    def platformSupported(self) -> bool:  # noqa: N802
        session = self._ensure_session()
        return bool(session.platform_supported) if session is not None else False

    @Property(str, notify=envChanged)
    def envState(self) -> str:  # noqa: N802
        session = self._session
        return session.env_state if session is not None else ENV_CHECKING

    @Property(str, notify=envChanged)
    def envTextKey(self) -> str:  # noqa: N802
        """环境文案的 i18n 键；QML 用 Tr.map[Online.envTextKey] 绑定。"""
        session = self._session
        if session is None:
            return "online_et_checking"
        state = session.env_state
        if state == ENV_READY or session.install_state == INSTALL_DONE:
            return "online_et_ready"
        if state == ENV_INSTALLING:
            return "online_env_setup_start"
        if state == ENV_FAILED:
            return "online_env_setup_failed"
        if state == ENV_MISSING:
            return "online_et_not_found"
        return "online_et_checking"

    @Property(str, notify=envChanged)
    def envLevel(self) -> str:  # noqa: N802
        session = self._session
        if session is None:
            return "neutral"
        state = session.env_state
        if state == ENV_READY:
            return "success"
        if state == ENV_INSTALLING:
            return "warning"
        if state == ENV_FAILED:
            return "error"
        if state == ENV_MISSING:
            return "warning"
        return "neutral"

    @Property(str, notify=envChanged)
    def installState(self) -> str:  # noqa: N802
        session = self._session
        return session.install_state if session is not None else "idle"

    @Property(str, notify=envChanged)
    def installButtonKey(self) -> str:  # noqa: N802
        session = self._session
        if session is None:
            return "online_env_setup_btn"
        if session.install_state == INSTALL_RUNNING:
            return "online_env_setup_start"
        if session.env_state == ENV_READY or session.install_state == INSTALL_DONE:
            return "online_env_setup_done"
        if session.install_state == INSTALL_FAILED:
            return "online_env_setup_retry"
        return "online_env_setup_btn"

    @Property(bool, notify=envChanged)
    def canInstall(self) -> bool:  # noqa: N802
        session = self._session
        return bool(session.can_install) if session is not None else False

    @Property(bool, notify=envChanged)
    def installRunning(self) -> bool:  # noqa: N802
        session = self._session
        return bool(session is not None and session.install_state == INSTALL_RUNNING)

    @Property(bool, notify=envChanged)
    def etRunning(self) -> bool:  # noqa: N802
        session = self._session
        return bool(session.et_running) if session is not None else False

    @Property(int, notify=envChanged)
    def etPid(self) -> int:  # noqa: N802
        session = self._session
        return int(session.et_pid) if session is not None else 0

    @Property(str, notify=envChanged)
    def etStatusText(self) -> str:  # noqa: N802
        session = self._session
        if session is None:
            return self._text("online_et_stopped")
        return self._text(session.et_status_key, session.et_status_params)

    @Property(str, notify=lobbyChanged)
    def lobbyState(self) -> str:  # noqa: N802
        session = self._session
        return session.lobby_state if session is not None else "idle"

    @Property(bool, notify=lobbyChanged)
    def isHost(self) -> bool:  # noqa: N802
        session = self._session
        return bool(session.is_host) if session is not None else False

    @Property(str, notify=lobbyChanged)
    def lobbyCode(self) -> str:  # noqa: N802
        session = self._session
        return session.lobby_code if session is not None else ""

    @Property(bool, notify=lobbyChanged)
    def hasLobby(self) -> bool:  # noqa: N802
        session = self._session
        return bool(session.has_lobby) if session is not None else False

    @Property(int, notify=lobbyChanged)
    def localPort(self) -> int:  # noqa: N802
        session = self._session
        return int(session.local_port) if session is not None else 0

    @Property(str, notify=lobbyChanged)
    def lobbyStatusText(self) -> str:  # noqa: N802
        session = self._session
        if session is None or not session.lobby_status_key:
            return ""
        return self._text(session.lobby_status_key, session.lobby_status_params)

    @Property(str, notify=lobbyChanged)
    def lobbyStatusLevel(self) -> str:  # noqa: N802
        session = self._session
        return session.lobby_status_level if session is not None else "info"

    @Property(bool, notify=lobbyChanged)
    def canCreate(self) -> bool:  # noqa: N802
        session = self._session
        return bool(session.can_create) if session is not None else False

    @Property(bool, notify=lobbyChanged)
    def canJoin(self) -> bool:  # noqa: N802
        session = self._session
        return bool(session.can_join) if session is not None else False

    @Property(bool, notify=lobbyChanged)
    def canLeave(self) -> bool:  # noqa: N802
        session = self._session
        return bool(session.can_leave) if session is not None else False

    @Property(bool, notify=worldsChanged)
    def canScan(self) -> bool:  # noqa: N802
        session = self._session
        return bool(session.can_scan) if session is not None else False

    @Property("QVariantList", notify=membersChanged)
    def members(self) -> List[Dict[str, Any]]:
        session = self._session
        return session.members if session is not None else []

    @Property(int, notify=membersChanged)
    def guestLatency(self) -> int:  # noqa: N802
        session = self._session
        return int(session.guest_latency) if session is not None else 0

    @Property("QVariantList", notify=worldsChanged)
    def worlds(self) -> List[Dict[str, Any]]:
        session = self._session
        return session.worlds if session is not None else []

    @Property(bool, notify=worldsChanged)
    def scanning(self) -> bool:
        session = self._session
        return bool(session.scanning) if session is not None else False

    @Property("QVariantList", notify=staleChanged)
    def staleVersions(self) -> List[str]:
        session = self._session
        return session.stale_versions if session is not None else []

    # ─── 槽 ─────────────────────────────────────────────────

    @Slot()
    def activate(self) -> None:
        session = self._ensure_session()
        if session is not None:
            session.activate()

    @Slot()
    def refreshEnv(self) -> None:  # noqa: N802
        session = self._ensure_session()
        if session is not None:
            session.refresh_env()

    @Slot()
    def installEnv(self) -> None:  # noqa: N802
        session = self._ensure_session()
        if session is not None:
            session.install_env()

    @Slot()
    def scanWorlds(self) -> None:  # noqa: N802
        session = self._ensure_session()
        if session is not None:
            session.scan_worlds()

    @Slot(str)
    def createLobby(self, portText: str) -> None:  # noqa: N802
        session = self._ensure_session()
        if session is not None:
            session.create_lobby(portText)

    @Slot(str)
    def joinLobby(self, code: str) -> None:  # noqa: N802
        session = self._ensure_session()
        if session is not None:
            session.join_lobby(code)

    @Slot()
    def leaveLobby(self) -> None:  # noqa: N802
        session = self._ensure_session()
        if session is not None:
            session.leave_lobby()

    @Slot(result=str)
    def copyLobbyCode(self) -> str:  # noqa: N802
        session = self._session
        if session is None or not session.lobby_code:
            return ""
        app = QGuiApplication.instance()
        if app is None:
            return "no QGuiApplication"
        try:
            clipboard = app.clipboard()
            if clipboard is None:
                return "clipboard unavailable"
            clipboard.setText(str(session.lobby_code))
            return ""
        except Exception as e:  # noqa: BLE001 - 剪贴板可能被别的程序占用
            logger.warning("写入剪贴板失败: %s", e)
            return str(e)

    @Slot()
    def cleanupStaleVersions(self) -> None:  # noqa: N802
        session = self._ensure_session()
        if session is not None:
            session.cleanup_stale_versions()

    @Slot()
    def dismissStaleVersions(self) -> None:  # noqa: N802
        session = self._ensure_session()
        if session is not None:
            session.dismiss_stale_versions()

    @Slot(result="QVariantMap")
    def logSnapshot(self) -> Dict[str, Any]:  # noqa: N802
        return {"lines": list(self._log_lines), "seq": int(self._log_seq)}

    # ─── 观察者回调（任意线程）───────────────────────────────

    def on_changed(self, section: str) -> None:
        self._post({"kind": "changed", "section": str(section)})

    def on_log(self, text: str) -> None:
        self._post({"kind": "log", "text": str(text)})

    def on_log_key(self, key: str, params: Dict[str, Any]) -> None:
        self._post({"kind": "log_key", "key": str(key), "params": dict(params)})

    def on_status(self, key: str, level: str, params: Dict[str, Any]) -> None:
        self._post(
            {"kind": "status", "key": str(key), "level": str(level), "params": dict(params)}
        )

    def on_notice(self, key: str, level: str, detail: str) -> None:
        self._post(
            {"kind": "notice", "key": str(key), "level": str(level), "detail": str(detail)}
        )

    # ─── 队列投递 ───────────────────────────────────────────

    def _post(self, item: Dict[str, Any]) -> None:
        if self._closing:
            return
        with self._pending_lock:
            self._pending.append(item)
        self.posted.emit()

    @Slot()
    def _deliver(self) -> None:
        if self._closing:
            return
        with self._pending_lock:
            batch = self._pending[:]
            self._pending.clear()
        for item in batch:
            kind = item.get("kind")
            try:
                if kind == "changed":
                    section = str(item.get("section", ""))
                    if section in ("env", "install"):
                        self.envChanged.emit()
                    elif section == "lobby":
                        self.lobbyChanged.emit()
                    elif section == "members":
                        self.membersChanged.emit()
                    elif section == "worlds":
                        self.worldsChanged.emit()
                    elif section == "stale":
                        self.staleChanged.emit()
                elif kind == "log":
                    self._append_log(str(item.get("text", "")))
                elif kind == "log_key":
                    text = self._text(str(item.get("key", "")), item.get("params") or {})
                    self._append_log("[FMCL] " + text)
                elif kind == "status":
                    text = self._text(str(item.get("key", "")), item.get("params") or {})
                    self.statusMessage.emit(text, str(item.get("level", "info")))
                elif kind == "notice":
                    title = self._text(str(item.get("key", "")), {})
                    detail = str(item.get("detail", ""))
                    self.noticeRequested.emit(
                        title, detail, str(item.get("level", "info"))
                    )
            except Exception as e:  # noqa: BLE001 - 一条坏事件不拖垮后面的
                logger.error("处理联机投递失败 (%s): %s", kind, e)

    def _append_log(self, text: str) -> None:
        lines = [line for line in str(text).splitlines() if line != ""]
        if not lines:
            return
        self._log_lines.extend(lines)
        overflow = len(self._log_lines) - LOG_BUFFER_MAX_LINES
        if overflow > 0:
            del self._log_lines[:overflow]
        self._log_seq += len(lines)
        self.logLines.emit(lines, int(self._log_seq))

    @staticmethod
    def _text(key: str, params: Optional[Dict[str, Any]] = None) -> str:
        try:
            if params:
                return _(key, **params)
            return _(key)
        except Exception:  # noqa: BLE001 - 文案失败不挡住动作
            return key


__all__ = ["OnlineBridge"]
