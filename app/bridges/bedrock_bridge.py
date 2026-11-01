"""基岩版桥（阶段 3 任务 3.15）—— QML 上下文属性 Bedrock。

链路：

    qml/pages/bedrock/BedrockPage.qml
        -> 本模块（Qt 属性 / 信号 / TaskRunner / posted 队列，零业务规则）
        -> services/bedrock_session.py（列表 / 过滤 / 分页 / 单飞 / 前置计划）
        -> services/bedrock_service.py（安装 / 启动 / 删除 / 条款 / .NET / MSA）

线程约定与 3.6 ~ 3.14 一致：长活全部走 TaskRunner；worker 只 _post 排队，
真正改状态的是主线程 _deliver()。桥自己不做文件操作、不拼业务判断。
"""

from __future__ import annotations

import logging
import threading
from functools import partial
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Qt, Signal, Slot

from services.bedrock_service import BedrockService
from services.bedrock_session import (
    FILTER_ALL,
    STATE_IDLE,
    STATE_LOADING,
    STATE_READY,
    BedrockSession,
    InstallPlan,
)
from services.i18n_service import _

logger = logging.getLogger("app.bridges.bedrock_bridge")

QML_NAME = "Bedrock"

TASK_INSTALLED = "bedrock.installed"
TASK_AVAILABLE = "bedrock.available"
TASK_PLAN = "bedrock.plan"
TASK_INSTALL = "bedrock.install"
TASK_LAUNCH = "bedrock.launch"
TASK_REMOVE = "bedrock.remove"

TYPE_KEYS = {
    "release": "bedrock_type_release",
    "preview": "bedrock_type_preview",
    "beta": "bedrock_type_beta",
}


class BedrockBridge(QObject):
    """上下文属性 Bedrock。"""

    viewChanged = Signal()
    filtersChanged = Signal()
    busyChanged = Signal()
    progressChanged = Signal()
    pendingChanged = Signal()
    msaChanged = Signal()
    statusMessage = Signal(str, str)
    noticeRequested = Signal(str, str)
    msaCodeReady = Signal(str, str)
    detailClosed = Signal()
    posted = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._service: Optional[BedrockService] = None
        self._session: Optional[BedrockSession] = None
        self._launcher: Any = None
        self._tasks: Any = None
        self._engine: Any = None
        self._startup: Any = None
        self._tr: Any = None
        self._core_ready_seen = False
        self._closing = False

        self._installed_rows: List[Dict[str, Any]] = []
        self._version_rows: List[Dict[str, Any]] = []
        self._filters: List[Dict[str, Any]] = self._make_filters()
        self._detail_name = ""
        self._detail_info: Dict[str, Any] = {}
        self._detail_seen = False
        self._route = ""

        self._busy = False
        self._busy_kind = ""
        self._busy_name = ""
        self._progress: Dict[str, Any] = {}

        self._pending_terms = ""
        self._pending_dotnet = ""
        self._pending_component = ""
        self._plan_version = ""
        self._msa_pending = False
        self._msa_code = ""
        self._msa_uri = ""

        self._installed_token = 0
        self._available_token = 0
        self._plan_token = 0
        self._op_token = 0
        self._handles: Dict[str, Any] = {}
        self._pending: List[Dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        self._progress_prev: Any = None
        self._progress_cb: Any = None
        self.posted.connect(self._deliver, Qt.QueuedConnection)

    # ─── 装配 ───────────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        self._context = context
        self._service = self._service_of(context, "bedrock") or BedrockService()
        self._tasks = getattr(context, "tasks", None) if context is not None else None
        self._launcher = self._service_of(context, "launcher")
        self._session = BedrockSession(self._service, launcher=self._launcher)
        self._core_ready_seen = bool(self._session.core_ready)
        self._closing = False
        self._installed_rows = []
        self._version_rows = []
        self._detail_name = ""
        self._detail_info = {}
        self._detail_seen = False
        self._filters = self._make_filters()
        self._sync()

    def _service_of(self, context: Any, name: str) -> Any:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001 - 单个服务缺失不该让整页打不开
            logger.warning("基岩桥取服务 %s 失败: %s", name, e)
            return None

    def use_engine(self, engine: Any) -> None:
        self._engine = engine
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        tr = registry.get("Tr")
        if tr is not None and tr is not self._tr and hasattr(tr, "languageChanged"):
            self._tr = tr
            try:
                tr.languageChanged.connect(self._on_language_changed)
            except Exception as e:  # noqa: BLE001
                logger.warning("连接语言切换信号失败: %s", e)
        startup = registry.get("Startup")
        if startup is not None and startup is not self._startup:
            self._startup = startup
            for signal_name, slot in (
                ("chainFinished", self._on_chain_finished),
                ("phaseChanged", self._on_startup_phase_changed),
            ):
                signal = getattr(startup, signal_name, None)
                if signal is None:
                    continue
                try:
                    signal.connect(slot)
                except Exception as e:  # noqa: BLE001
                    logger.warning("接 Startup.%s 失败: %s", signal_name, e)

    @Slot()
    def _on_startup_phase_changed(self) -> None:
        """phase 变到 ready 时 launcher 已经注册；不必等整条启动链（含预下载）跑完。"""
        if self._closing or self._startup is None:
            return
        try:
            ready = bool(self._startup.property("launcherReady"))
        except Exception:  # noqa: BLE001 - 诊断字段读不到就等 chainFinished
            ready = False
        if ready:
            self._on_core_ready()

    @Slot()
    def _on_chain_finished(self) -> None:
        self._on_core_ready()

    def _on_core_ready(self) -> None:
        """核心第一次可用：重新取 launcher，并只加载一次列表。"""
        if self._closing:
            return
        session = self._ensure_ready()
        if session is None or not session.core_ready or self._core_ready_seen:
            return
        self._core_ready_seen = True
        self._load_installed(force=True)
        if "download" in self._route or self._route == "bedrock":
            self._load_available(force=True)

    def _on_language_changed(self) -> None:
        self._filters = self._make_filters()
        self.filtersChanged.emit()
        self._sync()

    def detach(self) -> None:
        self._closing = True
        self._stop_progress_capture()
        self._clear_pending()
        self._clear_msa()
        with self._pending_lock:
            self._pending.clear()
        for handle in list(self._handles.values()):
            try:
                handle.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._handles.clear()

    def _ensure_ready(self) -> Optional[BedrockSession]:
        if self._session is None:
            return None
        if self._context is None:
            return self._session
        launcher = self._service_of(self._context, "launcher")
        if launcher is not self._launcher:
            self._launcher = launcher
            self._session.use_launcher(launcher)
        return self._session

    # ─── 基础属性 ───────────────────────────────────────────

    @Property(bool, notify=viewChanged)
    def available(self) -> bool:
        return self._session is not None

    @Property(bool, notify=viewChanged)
    def coreReady(self) -> bool:  # noqa: N802
        return bool(self._session is not None and self._session.core_ready)

    @Property("QVariantList", notify=filtersChanged)
    def filters(self) -> List[Dict[str, Any]]:
        return self._filters

    @Property(int, notify=viewChanged)
    def filterIndex(self) -> int:  # noqa: N802
        return self._session.filter_index if self._session is not None else 0

    @Property(str, notify=viewChanged)
    def searchText(self) -> str:  # noqa: N802
        return self._session.search if self._session is not None else ""

    @Property(str, notify=viewChanged)
    def buildFilter(self) -> str:  # noqa: N802
        return self._session.build_filter if self._session is not None else FILTER_ALL

    @Property("QVariantList", notify=viewChanged)
    def installed(self) -> List[Dict[str, Any]]:
        return self._installed_rows

    @Property(int, notify=viewChanged)
    def installedCount(self) -> int:  # noqa: N802
        return len(self._installed_rows)

    @Property(str, notify=viewChanged)
    def installedState(self) -> str:  # noqa: N802
        return self._session.installed_state if self._session is not None else "idle"

    @Property(str, notify=viewChanged)
    def installedError(self) -> str:  # noqa: N802
        return self._session.installed_error if self._session is not None else ""

    @Property("QVariantList", notify=viewChanged)
    def versions(self) -> List[Dict[str, Any]]:
        return self._version_rows

    @Property(int, notify=viewChanged)
    def availableCount(self) -> int:  # noqa: N802
        return self._session.available_count if self._session is not None else 0

    @Property(int, notify=viewChanged)
    def releaseCount(self) -> int:  # noqa: N802
        return self._session.release_count if self._session is not None else 0

    @Property(int, notify=viewChanged)
    def filteredCount(self) -> int:  # noqa: N802
        return self._session.filtered_count if self._session is not None else 0

    @Property(str, notify=viewChanged)
    def availableState(self) -> str:  # noqa: N802
        return self._session.available_state if self._session is not None else "idle"

    @Property(str, notify=viewChanged)
    def availableError(self) -> str:  # noqa: N802
        return self._session.available_error if self._session is not None else ""

    @Property(int, notify=viewChanged)
    def page(self) -> int:
        return self._session.page if self._session is not None else 1

    @Property(int, notify=viewChanged)
    def pageCount(self) -> int:  # noqa: N802
        return self._session.page_count if self._session is not None else 1

    @Property(str, notify=viewChanged)
    def selectedVersion(self) -> str:  # noqa: N802
        return self._session.selected if self._session is not None else ""

    @Property(bool, notify=viewChanged)
    def canInstall(self) -> bool:  # noqa: N802
        if self._session is None:
            return False
        return bool(self._session.selected and self._session.writable and not self.busy)

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return bool(self._busy or (self._session is not None and self._session.busy))

    @Property(str, notify=busyChanged)
    def busyKind(self) -> str:  # noqa: N802
        return self._busy_kind

    @Property(str, notify=busyChanged)
    def busyName(self) -> str:  # noqa: N802
        return self._busy_name

    @Property("QVariantMap", notify=progressChanged)
    def progress(self) -> Dict[str, Any]:
        return self._progress

    @Property(str, notify=pendingChanged)
    def pendingTermsVersion(self) -> str:  # noqa: N802
        return self._pending_terms

    @Property(str, notify=pendingChanged)
    def pendingDotnetUrl(self) -> str:  # noqa: N802
        return self._pending_dotnet

    @Property(str, notify=pendingChanged)
    def pendingComponentName(self) -> str:  # noqa: N802
        return self._pending_component

    @Property(bool, notify=msaChanged)
    def msaPending(self) -> bool:  # noqa: N802
        return self._msa_pending

    @Property(str, notify=msaChanged)
    def msaCode(self) -> str:  # noqa: N802
        return self._msa_code

    @Property(str, notify=msaChanged)
    def msaUri(self) -> str:  # noqa: N802
        return self._msa_uri

    @Property("QVariantMap", notify=viewChanged)
    def detail(self) -> Dict[str, Any]:
        return self._detail_info

    @Property(str, notify=viewChanged)
    def detailName(self) -> str:  # noqa: N802
        return self._detail_name

    # ─── 导航 / 刷新 / 过滤 ─────────────────────────────────

    @Slot(str, str)
    def activate(self, route_id: str, name: str = "") -> None:
        session = self._ensure_ready()
        if session is None:
            return
        self._route = str(route_id or "")
        if "detail" in self._route:
            wanted = str(name or "")
            if wanted:
                self._detail_name = wanted
            elif not self._detail_name:
                rows = session.installed
                if rows:
                    self._detail_name = str(rows[0].get("name", ""))
            self._load_installed()
        elif "download" in self._route:
            self._detail_name = ""
            self._load_installed()
            self._load_available()
        else:
            self._detail_name = ""
            self._load_installed()
        self._sync()

    @Slot()
    def refresh(self) -> None:
        session = self._ensure_ready()
        if session is None:
            return
        if self.busy:
            return
        if not session.core_ready:
            self._status("bedrock_core_not_ready", {}, "warning")
            self._sync()
            return
        self._status("refreshing", {}, "info")
        self._load_installed(force=True)
        self._load_available(refresh_db=True, force=True)

    @Slot()
    def loadAvailable(self) -> None:  # noqa: N802
        self._ensure_ready()
        if self._session is not None:
            self._load_available()
            self._sync()

    @Slot(str)
    def setSearch(self, text: str) -> None:  # noqa: N802
        session = self._ensure_ready()
        if session is not None and session.set_search(text):
            self._sync()

    @Slot(str)
    def setBuildFilter(self, key: str) -> None:  # noqa: N802
        session = self._ensure_ready()
        if session is not None and session.set_build_filter(key):
            self._sync()

    @Slot(int)
    def goToPage(self, page: int) -> None:  # noqa: N802
        session = self._ensure_ready()
        if session is not None and session.go_to_page(page):
            self._sync()

    @Slot(str)
    def selectVersion(self, version: str) -> None:  # noqa: N802
        session = self._ensure_ready()
        if session is not None and session.select_version(version):
            self._sync()


    # ─── 操作入口 ───────────────────────────────────────────

    @Slot()
    def install(self) -> None:
        session = self._ensure_ready()
        if session is None or self.busy:
            return
        version = session.selected
        if not version:
            return
        self._plan_version = version
        self._start_plan(version)

    @Slot()
    def confirmTerms(self) -> None:  # noqa: N802
        session = self._ensure_ready()
        if session is None or not self._pending_terms:
            return
        version = self._pending_terms
        try:
            session.accept_terms()
        except Exception as e:  # noqa: BLE001
            logger.error("保存基岩版条款同意状态失败: %s", e)
            self._status("bedrock_install_failed", {"version": version, "error": str(e)}, "error")
            return
        self._pending_terms = ""
        self.pendingChanged.emit()
        self._sync()
        self._plan_version = version
        self._start_plan(version)

    @Slot()
    def cancelTerms(self) -> None:  # noqa: N802
        self._pending_terms = ""
        self.pendingChanged.emit()
        self._sync()

    @Slot()
    def dismissDotnet(self) -> None:  # noqa: N802
        self._pending_dotnet = ""
        self.pendingChanged.emit()
        self._sync()

    @Slot()
    def confirmComponents(self) -> None:  # noqa: N802
        session = self._ensure_ready()
        if session is None or not self._pending_component:
            return
        name = self._pending_component
        try:
            session.accept_components()
        except Exception as e:  # noqa: BLE001
            logger.error("保存基岩版组件同意状态失败: %s", e)
            self._status("bedrock_component_declined", {}, "error")
            self._pending_component = ""
            self.pendingChanged.emit()
            return
        self._pending_component = ""
        self.pendingChanged.emit()
        self._start_launch(name)

    @Slot()
    def declineComponents(self) -> None:  # noqa: N802
        self._pending_component = ""
        self.pendingChanged.emit()
        self._status("bedrock_component_declined", {}, "error")
        self._notice("bedrock_component_declined", {}, "warning")
        self._sync()

    @Slot(str)
    def launch(self, name: str) -> None:
        session = self._ensure_ready()
        if session is None or self.busy:
            return
        if not session.core_ready:
            self._status("bedrock_core_not_ready", {}, "warning")
            return
        try:
            plan = session.launch_plan(name)
        except Exception as e:  # noqa: BLE001 - 旧实现同样是"检查异常即放行启动"
            logger.warning("基岩版启动前置检查异常（放行）: %s", e)
            self._start_launch(name)
            return
        if plan.component_consent_required:
            self._pending_component = name
            self.pendingChanged.emit()
            self._sync()
            return
        self._start_launch(name)

    @Slot(str)
    def remove(self, name: str) -> None:
        session = self._ensure_ready()
        if session is None or self.busy or not name:
            return
        if not session.core_ready:
            self._status("bedrock_core_not_ready", {}, "warning")
            return
        if not session.begin("remove", name):
            return
        self._begin_busy("remove", name, _("refreshing"))
        token = self._next_op_token()
        self._submit(
            TASK_REMOVE,
            partial(session.run_remove, name),
            partial(self._task_done, "remove-done", token),
            partial(self._task_error, "remove-error", token),
        )

    @Slot()
    def openFolder(self) -> None:  # noqa: N802
        session = self._ensure_ready()
        if session is None:
            return
        if not session.core_ready:
            self._status("bedrock_core_not_ready", {}, "warning")
            return
        try:
            opened = session.open_root()
        except Exception as e:  # noqa: BLE001
            logger.warning("打开基岩版目录失败: %s", e)
            self._status("version_open_folder_failed", {"error": str(e)}, "error")
            return
        if not opened:
            self._status("bedrock_core_not_ready", {}, "warning")

    @Slot()
    def clearDetail(self) -> None:  # noqa: N802
        self._detail_name = ""
        self._detail_info = {}
        self._detail_seen = False
        self.detailClosed.emit()
        self._sync()

    # ─── 内部：加载 / 任务 ─────────────────────────────────

    def _load_installed(self, force: bool = False) -> None:
        session = self._session
        if session is None:
            return
        if not force and session.installed_state in (STATE_LOADING, STATE_READY):
            return
        if self._tasks is None:
            session.apply_installed([])
            self._sync()
            return
        self._installed_token += 1
        token = self._installed_token
        session.mark_installed_loading()
        self._sync()
        self._submit(
            TASK_INSTALLED,
            session.load_installed,
            partial(self._task_done, "installed-done", token),
            partial(self._task_error, "installed-error", token),
        )

    def _load_available(self, refresh_db: bool = False, force: bool = False) -> None:
        session = self._session
        if session is None:
            return
        if not force and session.available_state in (STATE_LOADING, STATE_READY):
            return
        if self._tasks is None:
            session.apply_available([])
            self._sync()
            return
        self._available_token += 1
        token = self._available_token
        session.mark_available_loading()
        self._sync()
        self._submit(
            TASK_AVAILABLE,
            partial(session.load_available, refresh_db),
            partial(self._task_done, "available-done", token),
            partial(self._task_error, "available-error", token),
        )

    def _start_plan(self, version: str) -> None:
        session = self._session
        if session is None:
            return
        if not session.writable:
            self._status("bedrock_core_not_ready", {}, "warning")
            return
        self._plan_token += 1
        token = self._plan_token
        self._begin_busy("preflight", version, _("refreshing"))
        self._submit(
            TASK_PLAN,
            partial(session.install_plan, version),
            partial(self._task_done, "plan-done", token),
            partial(self._task_error, "plan-error", token),
        )

    def _start_install(self, version: str) -> None:
        session = self._session
        if session is None or not version:
            return
        if not session.begin("install", version):
            return
        token = self._next_op_token()
        self._begin_busy("install", version, _("bedrock_installing", version=version))
        self._start_progress_capture(token)
        self._submit(
            TASK_INSTALL,
            partial(session.run_install, version),
            partial(self._task_done, "install-done", token),
            partial(self._task_error, "install-error", token),
        )

    def _start_launch(self, name: str) -> None:
        session = self._session
        if session is None or not name:
            return
        if not session.begin("launch", name):
            return
        token = self._next_op_token()
        self._begin_busy("launch", name, _("bedrock_launching", name=name))

        def status_cb(text: str) -> None:
            self._post_event("component-status", token)

        def login_status_cb(text: str) -> None:
            self._post_event("msa-status", token)

        def login_code_cb(code: str, uri: str) -> None:
            self._post_event("msa-code", token, code=str(code or ""), uri=str(uri or ""))

        def login_done_cb(access_token: str, error: str) -> None:
            self._post_event("msa-done", token, access_token=str(access_token or ""), error=str(error or ""))

        self._submit(
            TASK_LAUNCH,
            partial(
                session.run_launch,
                name,
                status_cb=status_cb,
                download_notice=_("bedrock_component_downloading"),
                login_status_cb=login_status_cb,
                login_code_cb=login_code_cb,
                login_done_cb=login_done_cb,
            ),
            partial(self._task_done, "launch-done", token),
            partial(self._task_error, "launch-error", token),
        )

    def _submit(self, task_name: str, work: Any, on_done: Any, on_error: Any) -> None:
        if self._tasks is None:
            on_error(RuntimeError("tasks_unavailable"))
            return
        old = self._handles.get(task_name)
        if old is not None:
            try:
                old.cancel()
            except Exception:  # noqa: BLE001
                pass
        try:
            self._handles[task_name] = self._tasks.submit(
                work, name=task_name, on_done=on_done, on_error=on_error
            )
        except Exception as e:  # noqa: BLE001
            on_error(e)

    def _task_done(self, kind: str, token: int, result: Any) -> None:
        self._post({"kind": kind, "token": int(token), "result": result})

    def _task_error(self, kind: str, token: int, error: Any) -> None:
        self._post({"kind": kind, "token": int(token), "error": str(error)})

    def _next_op_token(self) -> int:
        self._op_token += 1
        return self._op_token

    def _begin_busy(self, kind: str, name: str, message: str) -> None:
        self._busy = True
        self._busy_kind = str(kind or "")
        self._busy_name = str(name or "")
        self._progress = {
            "message": str(message or ""),
            "current": 0,
            "total": 0,
            "fraction": 0.0,
            "indeterminate": True,
        }
        self.busyChanged.emit()
        self.progressChanged.emit()

    def _finish_op(self) -> None:
        self._stop_progress_capture()
        if self._session is not None:
            self._session.finish()
        self._busy = False
        self._busy_kind = ""
        self._busy_name = ""
        self._progress = {}
        self.busyChanged.emit()
        self.progressChanged.emit()

    # ─── 内部：进度捕获（旧 Tk 的 launcher.on_progress 同一条通路）──

    def _start_progress_capture(self, token: int) -> None:
        launcher = self._launcher
        if launcher is None:
            return
        previous = getattr(launcher, "on_progress", None)

        def callback(current: Any, total: Any, status: str = "") -> None:
            if callable(previous):
                try:
                    previous(current, total, status)
                except Exception:  # noqa: BLE001 - 旧回调失败不该打断安装
                    pass
            if self._closing:
                return
            try:
                cur = int(current)
                tot = int(total)
            except (TypeError, ValueError):
                cur = 0
                tot = 0
            self._post({"kind": "progress", "token": int(token), "current": cur, "total": tot})

        self._progress_prev = previous
        self._progress_cb = callback
        try:
            launcher.on_progress = callback
        except Exception as e:  # noqa: BLE001 - 核心对象不允许挂属性时只是没有进度
            logger.warning("挂接基岩版安装进度失败: %s", e)
            self._progress_prev = None
            self._progress_cb = None

    def _stop_progress_capture(self) -> None:
        launcher = self._launcher
        callback = self._progress_cb
        if launcher is not None and callback is not None:
            try:
                if getattr(launcher, "on_progress", None) is callback:
                    launcher.on_progress = self._progress_prev
            except Exception:  # noqa: BLE001
                pass
        self._progress_prev = None
        self._progress_cb = None

    # ─── 内部：投递队列 ─────────────────────────────────────

    def _post_event(self, kind: str, token: int, **payload: Any) -> None:
        item: Dict[str, Any] = {"kind": kind, "token": int(token)}
        item.update(payload)
        self._post(item)

    def _post(self, item: Dict[str, Any]) -> None:
        if self._closing:
            return
        with self._pending_lock:
            self._pending.append(item)
        self.posted.emit()

    def _deliver(self) -> None:
        while True:
            with self._pending_lock:
                if not self._pending:
                    return
                item = self._pending.pop(0)
            if self._closing:
                return
            self._dispatch(item)

    def _dispatch(self, item: Dict[str, Any]) -> None:
        session = self._session
        if session is None:
            return
        kind = str(item.get("kind", ""))
        token = int(item.get("token", 0) or 0)

        if kind == "installed-done":
            if token != self._installed_token:
                return
            session.apply_installed(item.get("result") or [])
            self._sync()
            if session.available_state == STATE_IDLE:
                self._load_available()
        elif kind == "installed-error":
            if token != self._installed_token:
                return
            session.fail_installed(str(item.get("error", "")))
            self._sync()
            self._status("bedrock_load_error", {"error": item.get("error", "")}, "error")
        elif kind == "available-done":
            if token != self._available_token:
                return
            session.apply_available(item.get("result") or [])
            self._sync()
            self._status(
                "bedrock_available_status",
                {"total": session.available_count, "release": session.release_count},
                "success",
            )
        elif kind == "available-error":
            if token != self._available_token:
                return
            session.fail_available(str(item.get("error", "")))
            self._sync()
            self._status("bedrock_load_error", {"error": item.get("error", "")}, "error")
        elif kind == "plan-done":
            if token != self._plan_token:
                return
            self._finish_op()
            plan = item.get("result")
            if not isinstance(plan, InstallPlan):
                return
            if plan.terms_required:
                self._pending_terms = plan.version
                self.pendingChanged.emit()
                self._sync()
                return
            if plan.dotnet is not None and not plan.dotnet.has_sdk10:
                self._pending_dotnet = str(plan.dotnet.download_url or "")
                self.pendingChanged.emit()
                self._sync()
                return
            self._start_install(plan.version)
        elif kind == "plan-error":
            if token != self._plan_token:
                return
            self._finish_op()
            version = self._plan_version
            logger.warning("基岩版安装前置检查失败，继续安装: %s", item.get("error", ""))
            self._status(
                "bedrock_preflight_failed", {"error": item.get("error", "")}, "warning"
            )
            if version:
                self._start_install(version)
        elif kind == "progress":
            if token != self._op_token:
                return
            current = int(item.get("current", 0) or 0)
            total = int(item.get("total", 0) or 0)
            fraction = (float(current) / float(total)) if total > 0 else 0.0
            self._progress = {
                "message": self._progress.get("message", ""),
                "current": current,
                "total": total,
                "fraction": fraction,
                "indeterminate": total <= 0,
            }
            self.progressChanged.emit()
        elif kind == "install-done":
            if token != self._op_token:
                return
            self._finish_op()
            outcome = item.get("result")
            if outcome is not None and getattr(outcome, "success", False):
                self._status("bedrock_install_success", {"version": outcome.display}, "success")
                self._notice("bedrock_install_success", {"version": outcome.display}, "success")
                self._load_installed(force=True)
            else:
                error = getattr(outcome, "display", item.get("error", ""))
                version = getattr(outcome, "version", "")
                self._status("bedrock_install_failed", {"version": version, "error": error}, "error")
                self._notice("bedrock_install_failed", {"version": version, "error": error}, "error")
                self._load_installed(force=True)
        elif kind == "install-error":
            if token != self._op_token:
                return
            self._finish_op()
            version = self._plan_version
            self._status("bedrock_install_failed", {"version": version, "error": item.get("error", "")}, "error")
            self._notice("bedrock_install_failed", {"version": version, "error": item.get("error", "")}, "error")
            self._load_installed(force=True)
        elif kind == "launch-done":
            if token != self._op_token:
                return
            self._finish_op()
            self._clear_msa()
            outcome = item.get("result")
            if outcome is None:
                self._status("bedrock_launch_busy", {}, "warning")
            elif getattr(outcome, "success", False):
                name = getattr(outcome, "name", "")
                self._status("bedrock_launch_success", {"name": name}, "success")
                self._notice("bedrock_launch_success", {"name": name}, "success")
            else:
                name = getattr(outcome, "name", "")
                message = getattr(outcome, "message", "") or item.get("error", "")
                self._status("bedrock_launch_failed", {"name": name, "error": message}, "error")
                self._notice("bedrock_launch_failed", {"name": name, "error": message}, "error")
        elif kind == "launch-error":
            if token != self._op_token:
                return
            name = self._busy_name
            self._finish_op()
            self._clear_msa()
            self._status("bedrock_launch_failed", {"name": name, "error": item.get("error", "")}, "error")
            self._notice("bedrock_launch_failed", {"name": name, "error": item.get("error", "")}, "error")
        elif kind == "remove-done":
            if token != self._op_token:
                return
            self._finish_op()
            outcome = item.get("result")
            if outcome is not None and getattr(outcome, "success", False):
                name = getattr(outcome, "name", "")
                self._status("bedrock_remove_success", {"name": name}, "success")
                self._notice("bedrock_remove_success", {"name": name}, "success")
                if name and name == self._detail_name:
                    self._detail_name = ""
                    self._detail_info = {}
                    self._detail_seen = False
                    self.detailClosed.emit()
            else:
                error = getattr(outcome, "message", item.get("error", ""))
                self._status("bedrock_remove_failed", {"error": error}, "error")
                self._notice("bedrock_remove_failed", {"error": error}, "error")
            self._load_installed(force=True)
        elif kind == "remove-error":
            if token != self._op_token:
                return
            self._finish_op()
            self._status("bedrock_remove_failed", {"error": item.get("error", "")}, "error")
            self._notice("bedrock_remove_failed", {"error": item.get("error", "")}, "error")
            self._load_installed(force=True)
        elif kind == "component-status":
            if token == self._op_token and self._busy_kind == "launch":
                self._status("bedrock_component_downloading", {}, "info")
        elif kind == "msa-status":
            if token == self._op_token:
                self._status("bedrock_msa_waiting", {}, "info")
        elif kind == "msa-code":
            if token != self._op_token:
                return
            code = str(item.get("code", "") or "")
            uri = str(item.get("uri", "") or "")
            self._msa_pending = bool(code)
            self._msa_code = code
            self._msa_uri = uri
            self.msaChanged.emit()
            self._status("bedrock_msa_waiting", {}, "info")
            self.msaCodeReady.emit(code, uri)
        elif kind == "msa-done":
            if token != self._op_token:
                return
            access_token = str(item.get("access_token", "") or "")
            error = str(item.get("error", "") or "")
            self._clear_msa()
            if access_token and not error:
                self._status("bedrock_msa_success", {}, "success")
            elif error:
                self._status("bedrock_msa_failed", {"error": error}, "error")

    # ─── 内部：同步 / 文案 ─────────────────────────────────

    def _sync(self) -> None:
        session = self._session
        if session is None:
            self._installed_rows = []
            self._version_rows = []
            self._detail_info = {}
            self.viewChanged.emit()
            return
        self._installed_rows = [self._decorate_installed(row) for row in session.installed]
        self._version_rows = [self._decorate_version(row) for row in session.page_rows]
        if "detail" in self._route:
            if not self._detail_name and self._installed_rows:
                self._detail_name = str(self._installed_rows[0].get("name", ""))
            info = session.find_installed(self._detail_name) if self._detail_name else None
            self._detail_info = self._decorate_installed(info) if info else {}
            if info:
                self._detail_seen = True
        else:
            self._detail_info = {}
        self.viewChanged.emit()

    def _make_filters(self) -> List[Dict[str, Any]]:
        return [
            {"key": FILTER_ALL, "label": _("bedrock_filter_all")},
            {"key": "UWP", "label": "UWP"},
            {"key": "GDK", "label": "GDK"},
        ]

    def _decorate_installed(self, row: Dict[str, Any]) -> Dict[str, Any]:
        game_type = str(row.get("game_type", "release") or "release")
        return {
            "name": str(row.get("name", "") or ""),
            "version": str(row.get("version", "") or ""),
            "buildType": str(row.get("build_type", "UWP") or "UWP"),
            "gameType": game_type,
            "typeKey": TYPE_KEYS.get(game_type, "bedrock_type_release"),
            "typeText": self._type_text(game_type),
            "path": str(row.get("path", "") or ""),
        }

    def _decorate_version(self, row: Dict[str, Any]) -> Dict[str, Any]:
        game_type = str(row.get("type", "release") or "release")
        key = TYPE_KEYS.get(game_type, "bedrock_type_release")
        return {
            "version": str(row.get("version", "") or ""),
            "buildType": str(row.get("build_type", "UWP") or "UWP"),
            "typeKey": key,
            "typeText": self._type_text(game_type),
            "date": str(row.get("date", "") or ""),
        }

    def _type_text(self, game_type: str) -> str:
        return _(TYPE_KEYS.get(str(game_type or "release"), "bedrock_type_release"))

    def _status(self, key: str, params: Dict[str, Any], level: str = "info") -> None:
        self.statusMessage.emit(self._text(key, params), level)

    def _notice(self, key: str, params: Dict[str, Any], level: str = "info") -> None:
        self.noticeRequested.emit(self._text(key, params), level)

    @staticmethod
    def _text(key: str, params: Dict[str, Any]) -> str:
        try:
            return _(key, **(params or {}))
        except Exception:  # noqa: BLE001
            return str(key)

    # ─── 内部：清除 / 登录状态 ─────────────────────────────

    def _clear_pending(self) -> None:
        changed = bool(self._pending_terms or self._pending_dotnet or self._pending_component)
        self._pending_terms = ""
        self._pending_dotnet = ""
        self._pending_component = ""
        if changed:
            self.pendingChanged.emit()

    def _clear_msa(self) -> None:
        changed = bool(self._msa_pending or self._msa_code or self._msa_uri)
        self._msa_pending = False
        self._msa_code = ""
        self._msa_uri = ""
        if changed:
            self.msaChanged.emit()

