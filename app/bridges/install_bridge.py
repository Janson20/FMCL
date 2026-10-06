"""安装向导桥 —— QML 侧的 `Install` 上下文属性（阶段 3 任务 3.3）。

## 它是什么

向导页要的东西有四类：**可用版本清单（含正式版/测试版切换与分页）、版本 ID 与加载器
表单、加载器兼容提示、安装进度与结果**。桥只做三件事：把服务/任务线程的回报变成
QML 能绑定的属性与信号、把 QML 的动作转成服务调用或任务提交、把 i18n 键翻成
当前语言的一句话。**一句业务规则都不写**（红线 2）：版本 ID 截断、分页夹取、
加载器映射、兼容判定、安装结果判读都在 `InstallService`。

## 为什么安装任务走 `Tasks` 桥而不是 `self.tasks`

`app/bridges/qt_tasks.py` 是阶段 2 为"阶段 3 的页面"准备的入口：经它提交的任务
才算**后台任务** —— 状态栏的 `Shell.busy`（2.7 的后台任务指示）只认
`Tasks.activeCount`，进度按 100ms 合并后一次性送到 QML，取消就是 `Tasks.cancel(id)`。
服务自己的 `self.tasks.submit()` 走的是同一个线程池，但**不计入** `activeCount`
（3.2 的校验/删除就是这样，它们不需要全局指示）。

因此本桥在 `use_engine()` 里把**服务的两个工作者**登记成任务种类
（`version.install`）—— `register_kind` 的约定是"名字 → 可调用对象"，
业务逻辑仍然在服务里。

## 线程约定

任务的进度/结束信号由 `TaskBridge` 在主线程的定时器里发出，服务的观察者回调却
可能在任务线程里发生 —— 所以回调里**只写普通 Python 属性 + emit 信号**
（Qt 信号跨线程是排队投递），不碰 QML 引擎（契约第三节硬规则 3）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from services.i18n_service import _
from services.install_service import (
    COMPAT_BAD,
    COMPAT_CHECKING,
    COMPAT_IDLE,
    COMPAT_OK,
    COMPAT_UNKNOWN,
    LOADER_DISPLAY,
    LOADER_IDS,
    LOADER_LABEL_KEYS,
    LOADER_NONE,
    RESULT_CANCELLED,
    RESULT_DONE,
    RESULT_FAILED,
    TAB_RELEASE,
    TAB_SNAPSHOT,
    TABS,
    clean_version_id,
)

logger = logging.getLogger("app.bridges.install_bridge")

#: 可用版本清单的三态（与 `FmPage.contentState` 的取值一一对应）
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_EMPTY = "empty"
STATE_ERROR = "error"

#: 登记到 `Tasks` 桥的任务种类名
KIND_INSTALL = "version.install"


class InstallBridge(QObject):
    """上下文属性 `Install`。"""

    #: 可用版本清单 / 分页 / 标签页 / 计数变了。
    availableChanged = Signal()
    #: 表单（版本 ID / 加载器）或兼容提示变了。
    formChanged = Signal()
    #: 进度、忙碌、结果变了。
    progressChanged = Signal()
    #: 状态条文案（**已翻译**；由页面转给 `Shell.setStatus`）。
    statusMessage = Signal(str, str)
    #: 装好了（参数 = 安装后的版本 id）—— 页面据此提示并回列表。
    installed = Signal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._service: Any = None
        self._nav: Any = None
        self._tasks: Any = None
        #: 任务种类是否已经登记过（`_bind_tasks` 的幂等判据）
        self._kinds_registered = False

        # ── 普通 Python 属性（回调可能在别的线程里写它们）──
        self._rows: List[Dict[str, Any]] = []
        self._tab = TAB_RELEASE
        self._page = 1
        self._page_count = 1
        self._total = 0
        self._release_count = 0
        self._snapshot_count = 0
        self._available_state = STATE_LOADING
        self._available_error = ""
        self._version_id = ""
        self._loader = LOADER_NONE
        self._compat_state = COMPAT_IDLE
        self._compat_key = ""
        self._compat_source = ""
        self._busy = False
        self._task_id = 0
        self._progress_current = 0
        self._progress_total = 0
        self._progress_fraction = 0.0
        self._result_state = ""
        self._result_key = ""
        self._result_version = ""
        self._result_installed = ""
        self._result_remaining = 0

    # ─── 装配期注入 ─────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        """`main_qml.register_bridges` 在注册前调它：取安装服务并挂上观察者。"""
        self._service = self._service_of(context, "install")
        if self._service is not None:
            self._service.set_observer(self)
        else:
            logger.warning("安装服务不可用：向导页只会显示错误态")

    def use_engine(self, engine: Any) -> None:
        """接上 `Nav`（入口跳转）与 `Tasks`（安装任务的提交/进度/取消）。"""
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        nav = registry.get("Nav")
        if nav is not None:
            self._nav = nav
        tasks = registry.get("Tasks")
        if tasks is not None:
            self._bind_tasks(tasks)

    def _bind_tasks(self, tasks: Any) -> None:
        """接上 `Tasks` 桥：登记任务种类 + 订阅三条信号。

        **必须幂等**：`use_engine()` 每次装配都会调一遍（`main_qml.py` 的二次注入名单
        目前只有 `Home`/`Versions`，但谁把它加进去就会踩到）—— 重复 `connect` 会让
        同一个进度事件处理两遍、结束事件走两条路。这里认"同一个 Tasks 对象只接一次"。
        """
        if tasks is self._tasks and self._kinds_registered:
            return
        self._tasks = tasks
        register = getattr(tasks, "register_kind", None)
        if callable(register) and self._service is not None:
            try:
                register(KIND_INSTALL, self._service.install_task, True)
                self._kinds_registered = True
            except Exception as e:  # noqa: BLE001 - 登记不上只是装不了，不该让装配失败
                logger.warning("登记任务种类 %s 失败: %s", KIND_INSTALL, e)
        for signal_name, handler in (
            ("progress", self._on_task_progress),
            ("taskFinished", self._on_task_finished),
            ("taskFailed", self._on_task_failed),
        ):
            signal = getattr(tasks, signal_name, None)
            if signal is None:
                logger.warning("Tasks 桥没有 %s 信号，向导页的进度不会刷新", signal_name)
                continue
            try:
                signal.connect(handler)
            except Exception as e:  # noqa: BLE001
                logger.warning("接 Tasks.%s 失败: %s", signal_name, e)

    @staticmethod
    def _service_of(context: Any, name: str) -> Any:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001 - 取不到不该让整页打不开
            logger.warning("安装向导取服务 %s 失败: %s", name, e)
            return None

    # ─── 可用版本清单（B-13）────────────────────────────────

    @Property(str, notify=availableChanged)
    def availableState(self) -> str:  # noqa: N802
        """`FmPage.contentState` 直接绑它。"""
        return self._available_state

    @Property(str, notify=availableChanged)
    def availableError(self) -> str:  # noqa: N802
        return self._available_error

    @Property(str, notify=availableChanged)
    def tab(self) -> str:  # noqa: N802
        return self._tab

    @Property(int, notify=availableChanged)
    def page(self) -> int:  # noqa: N802
        return self._page

    @Property(int, notify=availableChanged)
    def pageCount(self) -> int:  # noqa: N802
        return self._page_count

    @Property(int, notify=availableChanged)
    def total(self) -> int:  # noqa: N802
        """当前标签页的总条数（分页控件的"共 N 条"）。"""
        return self._total

    @Property("QVariantList", notify=availableChanged)
    def versions(self) -> List[Dict[str, Any]]:  # noqa: N802
        """当前页的可用版本行（`{id, type, snapshot}`）。"""
        return self._rows

    @Property(int, notify=availableChanged)
    def releaseCount(self) -> int:  # noqa: N802
        return self._release_count

    @Property(int, notify=availableChanged)
    def snapshotCount(self) -> int:  # noqa: N802
        return self._snapshot_count

    @Slot()
    def loadAvailable(self) -> None:  # noqa: N802
        """页面出现时调：吃服务里的复用窗口（窗口内不联网）。"""
        self._load(force=False)

    @Slot()
    def refreshAvailable(self) -> None:  # noqa: N802
        """刷新可用版本清单（无条件重新联网）。"""
        self._load(force=True)

    def _load(self, *, force: bool) -> None:
        if self._service is None:
            self._set_available_state(STATE_ERROR, _("plugin_state_error"))
            return
        self._set_available_state(STATE_LOADING, "")
        try:
            self._service.load_available(force=force)
        except Exception as e:  # noqa: BLE001
            logger.warning("取可用版本失败: %s", e)
            self._set_available_state(STATE_ERROR, str(e))

    @Slot(str)
    def setTab(self, tab: str) -> None:  # noqa: N802
        """切换正式版 / 测试版（旧界面：切标签页把页码重置回 1）。"""
        wanted = str(tab or "")
        self._tab = wanted if wanted in TABS else TAB_RELEASE
        self._page = 1
        self._rebuild_page()

    @Slot(int)
    def setPage(self, page: int) -> None:  # noqa: N802
        try:
            self._page = int(page)
        except (TypeError, ValueError):
            self._page = 1
        self._rebuild_page()

    @Slot()
    def prevPage(self) -> None:  # noqa: N802
        self.setPage(self._page - 1)

    @Slot()
    def nextPage(self) -> None:  # noqa: N802
        self.setPage(self._page + 1)

    @Slot(str)
    def quickSelect(self, versionId: str) -> None:  # noqa: N802
        """点列表里的一行 → 回填输入框（旧 `_quick_select_version`）。"""
        self.setVersionId(clean_version_id(versionId))

    # ─── 表单与兼容提示（B-09 / B-10）───────────────────────

    @Property(str, notify=formChanged)
    def versionId(self) -> str:  # noqa: N802
        return self._version_id

    @Property(str, notify=formChanged)
    def loader(self) -> str:  # noqa: N802
        return self._loader

    @Property(str, notify=formChanged)
    def loaderName(self) -> str:  # noqa: N802
        """传给核心的加载器显示名（也是 `mod_loader_hint` 的 `{loader}` 参数）。"""
        return LOADER_DISPLAY.get(self._loader, "无")

    @Property("QVariantList", constant=True)
    def loaderOptions(self) -> List[Dict[str, Any]]:  # noqa: N802
        """9 种加载器下拉的数据源；标签是 **i18n 键**，页面自己翻（语言切换能跟着走）。"""
        return [
            {"value": loader_id, "labelKey": LOADER_LABEL_KEYS.get(loader_id, loader_id)}
            for loader_id in LOADER_IDS
        ]

    @Property(str, notify=formChanged)
    def compatState(self) -> str:  # noqa: N802
        return self._compat_state

    @Property(str, notify=formChanged)
    def compatKey(self) -> str:  # noqa: N802
        """兼容提示的 i18n 键（页面用 `page.t(key)` + 两个参数填）。"""
        return self._compat_key

    @Property(str, notify=formChanged)
    def compatSource(self) -> str:  # noqa: N802
        """提示的出处：`local:xxx` / `remote` / 空 —— 验收时用来分辨"谁说的"。"""
        return self._compat_source

    @Slot(str)
    def setVersionId(self, text: str) -> None:  # noqa: N802
        value = str(text or "")
        if value == self._version_id:
            return
        self._version_id = value
        self._reset_compat()
        self.formChanged.emit()

    @Slot(str)
    def setLoader(self, loader: str) -> None:  # noqa: N802
        wanted = str(loader or "")
        if wanted not in LOADER_IDS:
            wanted = LOADER_NONE
        if wanted == self._loader:
            return
        self._loader = wanted
        self._reset_compat()
        self.formChanged.emit()

    @Slot()
    def checkCompatibility(self) -> None:  # noqa: N802
        """查一次兼容性（去抖由页面负责：输入框改了 400ms 后才调）。"""
        if self._service is None:
            return
        try:
            self._service.check_compat(self._version_id, self._loader)
        except Exception as e:  # noqa: BLE001 - 查询失败不该让页面报错
            logger.warning("兼容查询失败: %s", e)

    def _reset_compat(self) -> None:
        self._compat_state = COMPAT_IDLE
        self._compat_key = ""
        self._compat_source = ""

    # ─── 安装（B-11 / B-12）────────────────────────────────

    @Property(bool, notify=progressChanged)
    def busy(self) -> bool:  # noqa: N802
        return self._busy

    @Property(bool, notify=progressChanged)
    def canCancel(self) -> bool:  # noqa: N802
        """安装进行中才谈得上取消（旧界面没有取消，这是 3.3 新增的能力）。"""
        return self._busy and self._task_id > 0

    @Property(float, notify=progressChanged)
    def progress(self) -> float:  # noqa: N802
        return self._progress_fraction

    @Property(int, notify=progressChanged)
    def progressCurrent(self) -> int:  # noqa: N802
        return self._progress_current

    @Property(int, notify=progressChanged)
    def progressTotal(self) -> int:  # noqa: N802
        return self._progress_total

    @Property(bool, notify=progressChanged)
    def progressDeterminate(self) -> bool:  # noqa: N802
        return self._progress_total > 0

    @Property(str, notify=progressChanged)
    def resultState(self) -> str:  # noqa: N802
        """``""`` / ``done`` / ``cancelled`` / ``failed``。"""
        return self._result_state

    @Property(str, notify=progressChanged)
    def resultKey(self) -> str:  # noqa: N802
        return self._result_key

    @Property(str, notify=progressChanged)
    def resultVersion(self) -> str:  # noqa: N802
        return self._result_version

    @Property(str, notify=progressChanged)
    def resultInstalled(self) -> str:  # noqa: N802
        return self._result_installed

    @Property(int, notify=progressChanged)
    def resultRemaining(self) -> int:  # noqa: N802
        return self._result_remaining

    @Slot()
    def install(self) -> None:  # noqa: N802
        """提交安装任务（参数校验在服务里也有一道，这里只是不白起一个任务）。"""
        if self._busy:
            return
        cleaned = clean_version_id(self._version_id)
        if not cleaned:
            self.statusMessage.emit(_("version_id_required"), "error")
            return
        if self._tasks is None or self._service is None:
            self.statusMessage.emit(_("plugin_state_error"), "error")
            return
        self._clear_result()
        task_id = 0
        try:
            task_id = int(
                self._tasks.submit(KIND_INSTALL, {"version_id": cleaned, "loader": self._loader})
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("提交安装任务失败: %s", e)
        if task_id <= 0:
            self.statusMessage.emit(_("plugin_state_error"), "error")
            return
        self._task_id = task_id
        self._busy = True
        self._progress_current = 0
        self._progress_total = 0
        self._progress_fraction = 0.0
        self.progressChanged.emit()

    @Slot()
    def cancel(self) -> None:  # noqa: N802
        """请求取消（**请求**：核心要等下载回调下一次检查才停）。"""
        if not self.canCancel:
            return
        try:
            self._tasks.cancel(self._task_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("取消安装失败: %s", e)

    @Slot()
    def installModpack(self) -> None:  # noqa: N802
        """安装整合包入口（B-12）：**只推路由**，页面归 3.8。"""
        if self._nav is None:
            logger.debug("Install 跳转 versions/modpack 失败：Nav 未注入")
            return
        try:
            self._nav.push("versions/modpack", {})
        except Exception as e:  # noqa: BLE001
            logger.warning("跳转 versions/modpack 失败: %s", e)

    @Slot()
    def reset(self) -> None:  # noqa: N802
        """清空表单与结果（离开页面再回来时用）。"""
        self._version_id = ""
        self._loader = LOADER_NONE
        self._reset_compat()
        self._clear_result()
        self.formChanged.emit()

    def _clear_result(self) -> None:
        self._result_state = ""
        self._result_key = ""
        self._result_version = ""
        self._result_installed = ""
        self._result_remaining = 0

    # ─── Tasks 桥的回调（**主线程**）────────────────────────

    def _is_my_task(self, payload: Dict[str, Any]) -> bool:
        if not self._task_id:
            return False
        try:
            return int(payload.get("taskId", 0) or 0) == self._task_id
        except (TypeError, ValueError):
            return False

    def _on_task_progress(self, payload: Dict[str, Any]) -> None:
        if not self._is_my_task(payload):
            return
        self._progress_current = int(payload.get("current", 0) or 0)
        self._progress_total = int(payload.get("total", 0) or 0)
        try:
            self._progress_fraction = float(payload.get("fraction", 0.0) or 0.0)
        except (TypeError, ValueError):
            self._progress_fraction = 0.0
        self.progressChanged.emit()

    def _on_task_finished(self, payload: Dict[str, Any]) -> None:
        """任务正常结束：结果字典就是 `InstallService.install_task()` 的返回值。

        注意 `result` 可能是 **None**：`TaskRunner` 对"还没开始就被取消"的任务是
        **直接跳过**的（`app/tasks.py::_execute` 开头那条），那时没有结果字典，
        只有 `cancelled=True`。"结果为空 + cancelled"必须认成**已取消**，
        不能掉进 `else` 的"安装失败"——用户点完安装立刻点取消就会走这条路。
        """
        if not self._is_my_task(payload):
            return
        result = payload.get("result")
        data = result if isinstance(result, dict) else {}
        cancelled = bool(payload.get("cancelled")) or bool(data.get("cancelled"))
        state = str(data.get("state", "") or "")
        if not state and cancelled:
            state = RESULT_CANCELLED
        self._task_id = 0
        self._busy = False
        self._progress_fraction = 1.0 if data.get("ok") else self._progress_fraction
        self._result_state = state or (RESULT_DONE if data.get("ok") else RESULT_FAILED)
        self._result_version = str(
            data.get("installed") or data.get("requested") or clean_version_id(self._version_id)
        )
        self._result_installed = str(data.get("installed") or "")
        self._result_remaining = int(data.get("remaining", 0) or 0)
        if self._result_state == RESULT_CANCELLED:
            self._result_key = "version_install_cancelled"
        elif data.get("ok"):
            self._result_key = "version_installed"
        else:
            self._result_key = "version_install_failed"
        self.progressChanged.emit()
        if data.get("ok") and self._result_installed:
            self.installed.emit(self._result_installed)

    def _on_task_failed(self, payload: Dict[str, Any]) -> None:
        """任务抛异常（理论上服务会把异常转成结果字典，这里是兜底）。"""
        if not self._is_my_task(payload):
            return
        self._task_id = 0
        self._busy = False
        self._result_state = RESULT_FAILED
        self._result_key = "version_install_failed"
        self._result_version = clean_version_id(self._version_id)
        self.progressChanged.emit()

    # ─── InstallService 的观察者回调（**任意线程**）─────────

    def on_available(self, rows: List[Dict[str, Any]], error: str) -> None:
        self._available_error = str(error or "")
        self._rebuild_page()
        if self._available_error and not (self._release_count or self._snapshot_count):
            self._set_available_state(STATE_ERROR, self._available_error)
        elif not (self._release_count or self._snapshot_count):
            self._set_available_state(STATE_EMPTY, "")
        else:
            self._set_available_state(STATE_READY, self._available_error)

    def on_status(self, key: str, level: str, params: Dict[str, Any]) -> None:
        self.statusMessage.emit(self._status_text(key, params), level)

    def on_compat(
        self, loader: str, version: str, state: str, source: str, supported: Optional[bool]
    ) -> None:
        """兼容查询结果 —— **只收当前表单的答案**（用户已经改过输入就丢掉）。"""
        if str(loader or "") != self._loader or str(version or "") != clean_version_id(self._version_id):
            return
        self._compat_state = state
        self._compat_source = str(source or "")
        self._compat_key = {
            COMPAT_CHECKING: "version_compat_checking",
            COMPAT_OK: "version_compat_ok",
            COMPAT_BAD: "version_compat_bad",
            COMPAT_UNKNOWN: "version_compat_unknown",
        }.get(state, "")
        self.formChanged.emit()

    # ─── 内部 ───────────────────────────────────────────────

    def _rebuild_page(self) -> None:
        if self._service is None:
            self._rows = []
            self.availableChanged.emit()
            return
        try:
            data = self._service.page_of(self._tab, self._page)
        except Exception as e:  # noqa: BLE001 - 分页算不出来就显示空页
            logger.warning("分页失败: %s", e)
            data = {"rows": [], "page": 1, "pageCount": 1, "total": 0}
        self._rows = list(data.get("rows", []) or [])
        self._page = int(data.get("page", 1) or 1)
        self._page_count = int(data.get("pageCount", 1) or 1)
        self._total = int(data.get("total", 0) or 0)
        self._release_count = int(data.get("releaseCount", 0) or 0)
        self._snapshot_count = int(data.get("snapshotCount", 0) or 0)
        self.availableChanged.emit()

    def _set_available_state(self, state: str, error: str = "") -> None:
        if state == self._available_state and error == self._available_error:
            return
        self._available_state = state
        self._available_error = error
        self.availableChanged.emit()

    @staticmethod
    def _status_text(key: str, params: Dict[str, Any]) -> str:
        """把服务发来的 i18n 键翻成一句话。

        键都以**字面量**出现（`_("version_installing", version=…)` 那种），
        这样 `scripts/check_i18n.py` 的静态扫描看得见 —— 写成变量就查不到了
        （与 `VersionBridge._status_text` 同一条约定）。
        """
        data = dict(params or {})
        if key == "version_id_required":
            return _("version_id_required")
        if key == "version_installing":
            return _("version_installing", version=data.get("version", ""))
        if key == "version_installing_loader":
            return _("version_installing_loader", version=data.get("version", ""), loader=data.get("loader", ""))
        if key == "version_installed":
            return _("version_installed", version=data.get("version", ""))
        if key == "version_install_failed":
            return _("version_install_failed", version=data.get("version", ""))
        if key == "version_install_cancelled":
            return _("version_install_cancelled", version=data.get("version", ""))
        if key == "version_available_failed":
            return _("version_available_failed")
        if key == "version_available_loaded":
            return _("version_available_loaded", release=data.get("release", 0), snapshot=data.get("snapshot", 0))
        if key == "loading_available":
            return _("loading_available")
        #: 未知键**回落成键名本身**（而不是空串）：键名虽然不好看，但至少能一眼看出
        #: "哪条文案漏了"，而状态栏闪一下什么都不显示是查不出来的
        #: （与 `VersionBridge._status_text` 同一条约定）。
        return key


__all__ = ["InstallBridge"]
