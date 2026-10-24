"""服务器桥 —— QML 侧的 Servers 上下文属性（阶段 3 任务 3.9）。

链路：

    qml/pages/servers/ServersPage.qml / ServerDetailPage.qml / ServerInstallPage.qml
        ↓ 取属性 / 调槽
    app/bridges/servers_bridge.py（本模块，零业务规则）
        ↓ 转发
    services/server_session.py（会话：列表 / 选中 / 运行态 / 安装进度 / 每服内存）
        ↓ 委托
    launcher/server.py + launcher/server_config.py（核心安装、启动、删除、内存）

桥只做三件事：把会话的属性念给 QML 听、把 QML 的动作转成会话调用、把后台
回调变成 Qt 信号。列表怎么排序、安装进度怎么映射、删除前要不要等进程退出，
一条都不在这里判（迁移红线 2）。

## 线程约定

启动/安装/删除任务由 TaskRunner 调度，on_done / on_error 在主线程；安装进度与
进程退出回调来自 worker。观察者的所有事件都先进入 _pending 队列，由 posted
信号回到主线程后才 emit 页面信号 —— 与 ModpackBridge / ResourcesBridge 同一套。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from services.i18n_service import _
from services.server_session import (
    STATE_ERROR,
    STATE_LOADING,
    STATE_READY,
    ServerSession,
    launcher_callbacks,
)

logger = logging.getLogger("app.bridges.servers_bridge")


class ServersBridge(QObject):
    """上下文属性 Servers。"""

    listChanged = Signal()
    availableChanged = Signal()
    selectionChanged = Signal()
    runChanged = Signal()
    installChanged = Signal()
    busyChanged = Signal()
    statusMessage = Signal(str, str)
    noticeRequested = Signal(str, str)
    posted = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._session: Optional[ServerSession] = None
        self._launcher: Any = None
        self._game: Any = None
        self._tasks: Any = None
        self._nav: Any = None
        self._modpack: Any = None
        self._startup: Any = None
        self._version_input = ""
        self._pending: List[Dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        self._closing = False
        self.posted.connect(self._deliver)

    # ─── 装配 ───────────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        self._context = context
        self._ensure_services()
        if self._launcher is None:
            logger.warning("核心启动器不可用：服务器页只会显示错误态")

    def _ensure_services(self) -> bool:
        """取核心；启动链条跑完后 launcher 才会出现在上下文里，所以每次进页面都再看一眼。"""
        launcher = self._service_of(self._context, "launcher")
        game = self._service_of(self._context, "game")
        tasks = getattr(self._context, "tasks", None) if self._context is not None else None
        callbacks = launcher_callbacks(launcher)
        if self._session is None:
            self._session = ServerSession(
                launcher=launcher,
                tasks=tasks,
                game=game,
                callbacks=callbacks,
                observer=self,
            )
        elif launcher is not None and launcher is not self._launcher:
            self._session.rebind(launcher=launcher, tasks=tasks, game=game, callbacks=callbacks)
        self._launcher = launcher
        self._game = game
        self._tasks = tasks
        return launcher is not None

    @staticmethod
    def _service_of(context: Any, name: str) -> Any:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001 - 单个服务取不到不该让整页打不开
            logger.warning("服务器桥取服务 %s 失败: %s", name, e)
            return None

    def use_engine(self, engine: Any) -> None:
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        self._nav = registry.get("Nav")
        modpack = registry.get("Modpack")
        if modpack is not None and modpack is not self._modpack:
            if self._modpack is not None:
                old_signal = getattr(self._modpack, "serverInstalled", None)
                if old_signal is not None:
                    try:
                        old_signal.disconnect(self._on_server_installed)
                    except Exception:  # noqa: BLE001
                        pass
            self._modpack = modpack
            signal = getattr(modpack, "serverInstalled", None)
            if signal is not None:
                try:
                    signal.connect(self._on_server_installed)
                except Exception as e:  # noqa: BLE001 - 接不上只是列表不自动刷新
                    logger.warning("接 Modpack.serverInstalled 失败: %s", e)
        startup = registry.get("Startup")
        if startup is not None and startup is not self._startup:
            self._startup = startup
            signal = getattr(startup, "chainFinished", None)
            if signal is not None:
                try:
                    signal.connect(self._on_chain_finished)
                except Exception as e:  # noqa: BLE001
                    logger.warning("接 Startup.chainFinished 失败: %s", e)

    @Slot()
    def _on_chain_finished(self) -> None:
        if self._closing:
            return
        self._ensure_services()
        if self._session is None:
            return
        self._session.load_installed()
        if self._session.available_state != "idle":
            self._session.load_available()

    def detach(self) -> None:
        self._closing = True
        if self._modpack is not None:
            signal = getattr(self._modpack, "serverInstalled", None)
            if signal is not None:
                try:
                    signal.disconnect(self._on_server_installed)
                except Exception:  # noqa: BLE001 - 没接上就忽略
                    pass
        if self._session is not None:
            self._session.detach()

    @Slot(str)
    def _on_server_installed(self, name: str) -> None:
        if self._session is None or self._closing:
            return
        self._session.load_installed(select=str(name or ""))

    # ─── 列表 ───────────────────────────────────────────────

    @Property(bool, constant=True)
    def available(self) -> bool:
        return self._session is not None

    @Property(str, notify=listChanged)
    def listState(self) -> str:  # noqa: N802
        if self._session is None:
            return STATE_ERROR
        state = self._session.list_state
        if state == STATE_READY and self._session.count == 0:
            return "empty"
        return state

    @Property(str, notify=listChanged)
    def listError(self) -> str:  # noqa: N802
        return self._session.list_error if self._session is not None else ""

    @Property("QVariantList", notify=listChanged)
    def servers(self) -> List[Dict[str, Any]]:
        return self._session.installed_rows if self._session is not None else []

    @Property(int, notify=listChanged)
    def count(self) -> int:
        return self._session.count if self._session is not None else 0

    # ─── 选中 / 运行态 ───────────────────────────────────────

    @Property(str, notify=selectionChanged)
    def selected(self) -> str:
        return self._session.selected if self._session is not None else ""

    @Property(bool, notify=selectionChanged)
    def hasSelection(self) -> bool:  # noqa: N802
        return bool(self._session and self._session.has_selection)

    @Property("QVariantMap", notify=selectionChanged)
    def selectedInfo(self) -> Dict[str, Any]:  # noqa: N802
        return self._session.selected_info if self._session is not None else {}

    @Property(str, notify=selectionChanged)
    def memory(self) -> str:
        info = self.selectedInfo
        return str(info.get("memory", "2G") or "2G")

    @Property("QVariantList", constant=True)
    def memoryOptions(self) -> List[str]:  # noqa: N802
        return list(self._session.memory_options) if self._session is not None else ["1G", "2G", "4G", "6G", "8G", "12G", "16G"]

    @Property(str, notify=runChanged)
    def runningVersion(self) -> str:  # noqa: N802
        return self._session.running if self._session is not None else ""

    @Property(bool, notify=runChanged)
    def isRunning(self) -> bool:  # noqa: N802
        return bool(self._session and self._session.is_running)

    @Property(str, notify=busyChanged)
    def busyKind(self) -> str:  # noqa: N802
        return self._session.busy if self._session is not None else ""

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return bool(self._session and self._session.busy)

    @Property(bool, notify=busyChanged)
    def canStart(self) -> bool:  # noqa: N802
        if self._launcher is None or self._session is None or self._session.busy:
            return False
        info = self.selectedInfo
        return bool(info.get("id") and info.get("installed", False) and not info.get("running", False))

    @Property(bool, notify=busyChanged)
    def canStop(self) -> bool:  # noqa: N802
        return bool(
            self._launcher is not None
            and self._session
            and not self._session.busy
            and self.selectedInfo.get("running", False)
        )

    @Property(bool, notify=busyChanged)
    def canJoin(self) -> bool:  # noqa: N802
        return bool(self._launcher is not None and self._session and self._session.has_selection and not self._session.busy)

    @Property(bool, notify=busyChanged)
    def canRemove(self) -> bool:  # noqa: N802
        return bool(self._launcher is not None and self._session and self._session.has_selection and not self._session.busy)

    # ─── 安装输入 / 进度 ────────────────────────────────────

    @Property(str, notify=installChanged)
    def versionInput(self) -> str:  # noqa: N802
        return self._version_input

    @Property(bool, notify=installChanged)
    def canInstall(self) -> bool:  # noqa: N802
        return bool(self._launcher is not None and self._version_input.strip() and self._session is not None and not self._session.busy)

    @Property(str, notify=installChanged)
    def installState(self) -> str:  # noqa: N802
        return self._session.install_state if self._session is not None else "idle"

    @Property(str, notify=installChanged)
    def installVersion(self) -> str:  # noqa: N802
        return self._session.install_version if self._session is not None else ""

    @Property(str, notify=installChanged)
    def installError(self) -> str:  # noqa: N802
        return self._session.install_error if self._session is not None else ""

    @Property(float, notify=installChanged)
    def progress(self) -> float:
        return self._session.progress if self._session is not None else 0.0

    @Property(int, notify=installChanged)
    def progressCurrent(self) -> int:  # noqa: N802
        return self._session.progress_current if self._session is not None else 0

    @Property(int, notify=installChanged)
    def progressTotal(self) -> int:  # noqa: N802
        return self._session.progress_total if self._session is not None else 0

    @Property(bool, notify=installChanged)
    def progressDeterminate(self) -> bool:  # noqa: N802
        return bool(self._session and self._session.progress_determinate)

    # ─── 可用版本 ───────────────────────────────────────────

    @Property(str, notify=availableChanged)
    def availableState(self) -> str:  # noqa: N802
        if self._session is None:
            return STATE_ERROR
        state = self._session.available_state
        return STATE_LOADING if state == "idle" else state

    @Property(str, notify=availableChanged)
    def availableError(self) -> str:  # noqa: N802
        return self._session.available_error if self._session is not None else ""

    @Property("QVariantList", notify=availableChanged)
    def availableRows(self) -> List[Dict[str, Any]]:  # noqa: N802
        return self._session.available_rows if self._session is not None else []

    @Property(int, notify=availableChanged)
    def availablePage(self) -> int:  # noqa: N802
        return self._session.available_page if self._session is not None else 1

    @Property(int, notify=availableChanged)
    def availablePageCount(self) -> int:  # noqa: N802
        return self._session.available_page_count if self._session is not None else 1

    @Property(int, notify=availableChanged)
    def availableTotal(self) -> int:  # noqa: N802
        return self._session.available_total if self._session is not None else 0

    # ─── 页面动作 ───────────────────────────────────────────

    @Slot()
    def activateList(self) -> None:  # noqa: N802
        if self._closing:
            return
        self._ensure_services()
        if self._session is not None:
            self._session.load_installed()

    @Slot(str)
    def activateDetail(self, version: str) -> None:  # noqa: N802
        if self._closing:
            return
        self._ensure_services()
        if self._session is None:
            return
        wanted = str(version or "").strip()
        if self._session.list_state == STATE_LOADING:
            self._session.load_installed(select=wanted)
        elif wanted and wanted != self._session.selected:
            self._session.select(wanted, announce=False)

    @Slot(str)
    def activateInstall(self, version: str) -> None:  # noqa: N802
        if self._closing:
            return
        self._ensure_services()
        if self._session is None:
            return
        wanted = str(version or "").strip()
        if wanted and wanted != self._version_input:
            self._version_input = wanted
            self.installChanged.emit()
        if self._session.available_state in ("idle", STATE_ERROR):
            self._session.load_available()

    @Slot()
    def refresh(self) -> None:
        if self._session is None or self._closing:
            return
        self._session.load_installed()
        self._session.load_available()

    @Slot(str)
    def select(self, version: str) -> None:
        if self._session is not None and not self._closing:
            self._session.select(str(version or ""))

    @Slot(str)
    def openDetail(self, version: str) -> None:  # noqa: N802
        if self._session is None or self._closing:
            return
        wanted = str(version or "").strip()
        if wanted:
            self._session.select(wanted, announce=False)
        self._push("servers/detail", {"server": wanted} if wanted else {})

    @Slot()
    def openInstall(self) -> None:  # noqa: N802
        self._push("servers/install", {})

    @Slot()
    def openModpack(self) -> None:  # noqa: N802
        self._push("servers/modpack", {})

    @Slot()
    def goBack(self) -> None:  # noqa: N802
        if self._nav is None:
            return
        try:
            self._nav.goBack()
        except Exception as e:  # noqa: BLE001
            logger.warning("返回上一页失败: %s", e)

    @Slot(str)
    def setVersionInput(self, text: str) -> None:  # noqa: N802
        value = str(text or "")
        if value == self._version_input:
            return
        self._version_input = value
        self.installChanged.emit()

    @Slot(str)
    def quickSelect(self, version: str) -> None:  # noqa: N802
        self._version_input = str(version or "")
        self.installChanged.emit()
        try:
            self.statusMessage.emit(_("server_quick_select_status", version_id=self._version_input), "info")
        except Exception:  # noqa: BLE001
            pass

    @Slot()
    def install(self) -> None:
        if self._session is not None and not self._closing:
            self._session.install(self._version_input.strip())

    @Slot()
    def start(self) -> None:
        if self._session is not None and not self._closing:
            self._session.start()

    @Slot(str)
    def startVersion(self, version: str) -> None:  # noqa: N802
        """列表行直接启动：先选中该行，再提交启动（避免启动到上一次选中的服务器）。"""
        if self._session is None or self._closing:
            return
        wanted = str(version or "").strip()
        if not wanted:
            return
        self._session.select(wanted, announce=False)
        self._session.start()

    @Slot(str)
    def stopVersion(self, version: str) -> None:  # noqa: N802
        """列表行直接停止指定服务器（多服务器时不能停在选中项上）。"""
        if self._session is not None and not self._closing:
            self._session.stop(str(version or ""))

    @Slot()
    def stop(self) -> None:
        if self._session is not None and not self._closing:
            self._session.stop()

    @Slot(str)
    def remove(self, version: str) -> None:
        if self._session is not None and not self._closing:
            self._session.remove(str(version or ""))

    @Slot(str)
    def join(self, version: str) -> None:
        if self._session is not None and not self._closing:
            self._session.join(str(version or ""))

    @Slot(str)
    def setMemory(self, memory: str) -> None:  # noqa: N802
        if self._session is not None and not self._closing:
            self._session.set_memory(str(memory or ""))

    @Slot(str)
    def openDir(self, version: str) -> None:  # noqa: N802
        if self._session is None or self._closing:
            return
        try:
            path = self._session.directory_for(str(version or ""))
        except Exception as e:  # noqa: BLE001
            self.noticeRequested.emit(_("rm_open_folder_failed", error=str(e)), "error")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.noticeRequested.emit(_("rm_open_folder_failed", error=path), "error")

    @Slot(int)
    def setAvailablePage(self, page: int) -> None:  # noqa: N802
        if self._session is not None and not self._closing:
            self._session.set_available_page(int(page))

    @Slot()
    def refreshAvailable(self) -> None:  # noqa: N802
        if self._session is not None and not self._closing:
            self._session.load_available()

    def _push(self, route_id: str, params: Dict[str, Any]) -> None:
        if self._nav is None:
            return
        try:
            self._nav.push(route_id, params)
        except Exception as e:  # noqa: BLE001
            logger.warning("打开服务器路由 %s 失败: %s", route_id, e)

    # ─── 观察者（可能来自 worker）────────────────────────────

    def on_changed(self, section: str) -> None:
        self._post({"kind": "changed", "section": str(section)})

    def on_status(self, key: str, level: str, params: Dict[str, Any]) -> None:
        self._post({"kind": "status", "key": str(key), "level": str(level), "params": dict(params or {})})

    def on_notice(self, key: str, level: str, params: Dict[str, Any]) -> None:
        self._post({"kind": "notice", "key": str(key), "level": str(level), "params": dict(params or {})})

    def on_progress(self, *args: Any) -> None:
        self._post({"kind": "progress"})

    def on_install_finished(self, success: bool, version: str, detail: str) -> None:
        self._post({"kind": "install-finished", "success": bool(success), "version": str(version), "detail": str(detail)})

    def on_started(self, version: str) -> None:
        self._post({"kind": "started", "version": str(version)})

    def on_exit(self, version: str, exit_code: int) -> None:
        self._post({"kind": "exited", "version": str(version), "code": int(exit_code)})

    def on_removed(self, version: str) -> None:
        self._post({"kind": "removed", "version": str(version)})

    def on_join_finished(self, success: bool, version: str, detail: str) -> None:
        self._post({"kind": "joined", "success": bool(success), "version": str(version), "detail": str(detail)})

    def on_log(self, version: str, line: str) -> None:
        # 3.10 的控制台页面会订阅这里；3.9 不显示日志，所以刻意不建信号。
        return

    # ─── 队列投递 ───────────────────────────────────────────

    def _post(self, item: Dict[str, Any]) -> None:
        if self._closing:
            return
        with self._pending_lock:
            self._pending.append(item)
        self.posted.emit()

    def _deliver(self) -> None:
        with self._pending_lock:
            batch = self._pending[:]
            self._pending.clear()
        for item in batch:
            kind = item.get("kind")
            try:
                if kind == "changed":
                    section = item.get("section")
                    if section == "installed":
                        self.listChanged.emit()
                    elif section == "available":
                        self.availableChanged.emit()
                    elif section == "selection":
                        self.selectionChanged.emit()
                        self.busyChanged.emit()
                    elif section == "run":
                        self.runChanged.emit()
                        self.busyChanged.emit()
                        self.selectionChanged.emit()
                        self.listChanged.emit()
                    elif section == "install":
                        self.installChanged.emit()
                        self.busyChanged.emit()
                elif kind == "progress":
                    self.installChanged.emit()
                elif kind == "status":
                    self.statusMessage.emit(
                        self._text(str(item.get("key", "")), item.get("params") or {}),
                        str(item.get("level", "info")),
                    )
                elif kind == "notice":
                    self.noticeRequested.emit(
                        self._text(str(item.get("key", "")), item.get("params") or {}),
                        str(item.get("level", "info")),
                    )
                elif kind == "install-finished":
                    self.installChanged.emit()
                    if item.get("success"):
                        self.noticeRequested.emit(self._text("notify_server_installed", {}), "success")
                    else:
                        self.noticeRequested.emit(self._text("notify_server_failed", {}), "error")
                elif kind == "started":
                    self.runChanged.emit()
                    self.busyChanged.emit()
                elif kind == "exited":
                    self.runChanged.emit()
                    self.busyChanged.emit()
                elif kind == "removed":
                    self.listChanged.emit()
                    self.selectionChanged.emit()
                elif kind == "joined":
                    self.runChanged.emit()
                    self.busyChanged.emit()
            except Exception as e:  # noqa: BLE001 - 一条失败不拖垮后面的
                logger.error("处理服务器投递失败 (%s): %s", kind, e)

    @staticmethod
    def _text(key: str, params: Dict[str, Any]) -> str:
        try:
            return _(key, **params) if params else _(key)
        except Exception:  # noqa: BLE001 - 文案失败不挡住动作
            return key
