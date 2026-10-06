"""版本页桥 —— QML 侧的 `Versions` 上下文属性（阶段 3 任务 3.2）。

## 它是什么

版本页要的东西有四类：**已安装版本列表（含计数、搜索、排序）、当前选中版本的详情、
五个行操作入口（模组 / 资源管理 / 重命名 / 删除 / 详情）、游戏进程状态**。
桥只做三件事：把服务的返回值变成 QML 能绑定的属性与信号、把 i18n 键翻成当前语言的
一句话、把 QML 的动作转成服务调用。**一句业务规则都不写**（红线 2）：显示文本拼法、
排序键、删除后清"最近使用版本"、校验结果判读都在 `VersionService`。

## 与 `HomeBridge` 的两处刻意不同

1. **不做"全员广播"的观察者**：游戏状态由两个桥共同观察（两页都能启动/强杀），
   但**状态文案、崩溃 Toast、最小化窗口**这三件事**只由 `HomeBridge` 发** ——
   它是常驻的那个（首页永远在栈底）。本桥只实现 `on_state` / `on_killed` /
   `on_launch_result` 三个"刷新按钮可用性"用的回调，刻意**不实现** `on_status` /
   `on_window_detected` / `on_game_exit`，否则同一件事会有两条状态、两个 Toast。
2. **列表内容走显式的 `load()` / `refresh()`**：`VersionService.load()` 每次都会重扫，
   页面出现时调 `load()`（吃核心缓存）、点刷新调 `refresh()`（强制失效缓存重扫）。

## 线程约定

`VersionService` 的回调可能在任务线程里发生，所以回调里**只写普通 Python 属性 +
`emit` 信号**（Qt 信号跨线程是排队投递），不碰 QML 引擎（契约第三节硬规则 3）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from services.game_service import (
    STATE_LAUNCHING,
    STATE_RUNNING,
    STATE_WAITING,
)
from services.i18n_service import _

logger = logging.getLogger("app.bridges.version_bridge")

#: 游戏状态里"正在进行中"的那几个（决定启动/强杀按钮的可用性）。
BUSY_STATES = (STATE_LAUNCHING, STATE_WAITING, STATE_RUNNING)

#: 页面三态（与 `FmPage.contentState` 的取值一一对应）。
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_EMPTY = "empty"
STATE_ERROR = "error"


class _GameWatcher:
    """只订阅"按钮可用性"那三个游戏回调的观察者（见模块文档第 1 条）。

    为什么不直接让 `VersionsBridge` 去观察 `GameService`：那样 `GameService` 的
    `on_status` / `on_game_exit` 也会打到本桥身上，同一件事就会出现**两条状态、
    两个崩溃 Toast**（`HomeBridge` 也在观察）。这个小对象只实现必要的那三个回调，
    把"谁负责对用户说话"这件事固定成 `HomeBridge` 一家的职责。
    """

    def __init__(self, bridge: "VersionsBridge") -> None:
        self._bridge = bridge

    def on_state(self, state: str) -> None:
        self._bridge.report_game_state(state)

    def on_killed(self, exit_code: int) -> None:
        self._bridge.refresh_game_state()

    def on_launch_result(
        self, version_id: str, target_version: Optional[str], success: bool, error: str
    ) -> None:
        self._bridge.refresh_game_state()


class VersionsBridge(QObject):
    """上下文属性 `Versions`。"""

    #: 列表内容、过滤结果、计数变了。
    versionsChanged = Signal()
    #: 页面三态变了（loading / ready / empty / error）。
    loadStateChanged = Signal()
    #: 选中的版本或它的详情变了。
    selectionChanged = Signal()
    #: 游戏状态、启动/强杀可用性变了。
    gameChanged = Signal()
    #: 有后台操作（重命名/删除/校验）在跑。
    busyChanged = Signal()
    #: 状态条文案（**已翻译**；由 App.qml 转给 `Shell.setStatus`）。
    statusMessage = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._service: Any = None
        self._game: Any = None
        self._nav: Any = None
        self._startup: Any = None

        # ── 普通 Python 属性（服务回调可能在别的线程里写它们）──
        self._rows: List[Dict[str, Any]] = []
        self._visible: List[Dict[str, Any]] = []
        self._filter = ""
        self._sort_key = "name"
        self._load_state = STATE_LOADING
        self._load_error = ""
        self._current_id = ""
        self._detail: Dict[str, Any] = {}
        self._game_state = ""
        self._busy = False

    # ─── 装配期注入 ─────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        """`main_qml.register_bridges` 在注册前调它：取版本服务，并观察游戏状态。"""
        self._service = self._service_of(context, "version")
        self._game = self._service_of(context, "game")
        self._game_watcher = _GameWatcher(self)
        if self._service is not None:
            self._service.set_observer(self)
            self._service.load()
        if self._game is not None:
            # 追加而不是替换：首页桥（3.1）也在观察同一个 GameService。
            add = getattr(self._game, "add_observer", None)
            try:
                if callable(add):
                    add(self._game_watcher)
                else:  # 理论上不会走到：3.2 起 GameService 支持多观察者
                    self._game.set_observer(self._game_watcher)
            except Exception as e:  # noqa: BLE001 - 观察不上只是按钮不变灰
                logger.warning("版本页观察游戏服务失败: %s", e)
        self.refresh_game_state()

    def use_engine(self, engine: Any) -> None:
        """接上 `Nav`（详情跳转、模组/资源入口）与 `Startup`（启动链条跑完后再扫一次）。

        **为什么必须接 `Startup`**：`bind()` 发生在装配期，那时启动链条还没把
        `launcher`（启动器核心）注册进上下文 —— 第一次扫描必然拿不到它。
        不接的话，用户第一次进版本页看到的会是一块"出错"（实测：视觉探针里
        `loadState=error / loadError=launcher_unavailable`），要等手动刷新才恢复。
        """
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        nav = registry.get("Nav")
        if nav is not None:
            self._nav = nav
        startup = registry.get("Startup")
        if startup is not None:
            self._startup = startup
            signal = getattr(startup, "chainFinished", None)
            if signal is not None:
                try:
                    signal.connect(self.on_chain_finished)
                except Exception as e:  # noqa: BLE001 - 接不上只是不自动重扫
                    logger.warning("接 Startup.chainFinished 失败: %s", e)

    @Slot()
    def on_chain_finished(self) -> None:
        """启动链条跑完（核心已注册）→ 重新扫一次，把加载器/游戏版本这些元数据补齐。

        第一次扫描发生在装配期（`bind()`），那时核心还没注册 —— 服务会**降级**成
        按目录名列举（见 `VersionService._scan_sync`），所以有这一步来补齐。
        """
        self.load()

    @staticmethod
    def _service_of(context: Any, name: str) -> Any:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001 - 单个服务取不到不该让整页打不开
            logger.warning("版本页取服务 %s 失败: %s", name, e)
            return None

    # ─── 列表（B-01 / B-19）────────────────────────────────

    @Property("QVariantList", notify=versionsChanged)
    def versions(self) -> List[Dict[str, Any]]:  # noqa: N802
        """过滤 + 排序之后的可见行。"""
        return self._visible

    @Property(int, notify=versionsChanged)
    def count(self) -> int:  # noqa: N802
        """可见行数（搜索框旁边那个计数）。"""
        return len(self._visible)

    @Property(int, notify=versionsChanged)
    def totalCount(self) -> int:  # noqa: N802
        """已安装总数（不看搜索框）。"""
        return len(self._rows)

    @Property(str, notify=versionsChanged)
    def filter(self) -> str:  # noqa: N802
        return self._filter

    @Property(str, notify=versionsChanged)
    def sortKey(self) -> str:  # noqa: N802
        return self._sort_key

    @Property(bool, notify=versionsChanged)
    def ready(self) -> bool:  # noqa: N802
        """是否已经取过一轮列表（区分"加载中"与"真的没装版本"）。"""
        return self._load_state != STATE_LOADING

    @Property(str, notify=loadStateChanged)
    def loadState(self) -> str:  # noqa: N802
        """`FmPage.contentState` 直接绑它。"""
        return self._load_state

    @Property(str, notify=loadStateChanged)
    def loadError(self) -> str:  # noqa: N802
        return self._load_error

    @Slot()
    def load(self) -> None:  # noqa: N802
        """页面出现时调：吃核心缓存重扫一遍。"""
        if self._service is None:
            self._set_load_state(STATE_ERROR, _("plugin_state_error"))
            return
        self._submit(self._service.load())

    @Slot()
    def refresh(self) -> None:  # noqa: N802
        """刷新按钮：强制失效核心的实例缓存再扫（A-04）。"""
        if self._service is None:
            self._set_load_state(STATE_ERROR, _("plugin_state_error"))
            return
        self._submit(self._service.load(force=True))

    @Slot(str)
    def setFilter(self, text: str) -> None:  # noqa: N802
        self._filter = str(text or "")
        self._rebuild()

    @Slot(str)
    def setSortKey(self, key: str) -> None:  # noqa: N802
        self._sort_key = str(key or "name")
        self._rebuild()

    @Slot()
    def clearFilter(self) -> None:  # noqa: N802
        self._filter = ""
        self._rebuild()

    # ─── 详情与选中（B-02 / B-20）──────────────────────────

    @Property(str, notify=selectionChanged)
    def currentId(self) -> str:  # noqa: N802
        return self._current_id

    @Property(bool, notify=selectionChanged)
    def hasCurrent(self) -> bool:  # noqa: N802
        return bool(self._current_id)

    @Property("QVariantMap", notify=selectionChanged)
    def detail(self) -> Dict[str, Any]:  # noqa: N802
        return self._detail

    @Slot(str)
    def select(self, versionId: str) -> None:  # noqa: N802
        """选中一个版本（只记状态 + 取详情，不发状态文案 —— 旧界面那句是硬编码中文）。"""
        vid = str(versionId or "").strip()
        if not vid or self._service is None:
            self._clear_selection()
            return
        self._current_id = vid
        try:
            self._detail = self._service.detail(vid)
        except Exception as e:  # noqa: BLE001 - 详情取不到不该让选中失败
            logger.warning("取版本详情失败 (%s): %s", vid, e)
            self._detail = {"id": vid, "exists": False}
        self.selectionChanged.emit()
        # `canLaunch` 同时取决于"选没选版本"和"游戏状态"，而它只能挂一个 notify
        # （`gameChanged`）—— 所以选中变化时必须把它也发一次，否则按钮的可用性
        # 要等到下一次游戏状态变化才更新（3.2 探针实测：选了版本启动键还是灰的）。
        self.gameChanged.emit()
        if self._detail.get("exists"):
            self.statusMessage.emit(_("version_selected", version=vid), "info")

    @Slot()
    def clearSelection(self) -> None:  # noqa: N802
        self._clear_selection()

    @Slot(str)
    def openDetail(self, versionId: str) -> None:  # noqa: N802
        """选中并跳到版本详情页（L2）。"""
        self.select(versionId)
        self._push("versions/detail", {"version": str(versionId or "").strip()})

    @Slot()
    def goBack(self) -> None:  # noqa: N802
        if self._nav is None:
            return
        try:
            self._nav.goBack()
        except Exception as e:  # noqa: BLE001
            logger.warning("返回上一页失败: %s", e)

    # ─── 行操作（B-02 / B-21 / B-22）───────────────────────

    @Slot(str)
    def renameVersion(self, versionId: str) -> None:  # noqa: N802
        """重命名：输入框与确认框由服务经 `UIPort` 弹出（顺序与旧界面一致）。"""
        if self._service is None:
            return
        self._submit(self._service.rename(versionId), busy=True)

    @Slot(str)
    def removeVersion(self, versionId: str) -> None:  # noqa: N802
        if self._service is None:
            return
        self._submit(self._service.remove(versionId), busy=True)

    @Slot(str)
    def verify(self, versionId: str) -> None:  # noqa: N802
        """校验文件完整性（B-22，新增）；结果由服务发状态文案。"""
        if self._service is None:
            return
        self._submit(self._service.verify(versionId), busy=True)

    @Slot(str)
    def openFolder(self, versionId: str) -> None:  # noqa: N802
        """打开版本目录（B-21，新增）：同步、失败会发一条状态。"""
        if self._service is None:
            return
        self._service.open_folder(versionId)

    @Slot(str)
    def browseMods(self, versionId: str) -> None:  # noqa: N802
        """进入"版本模组"（L3）—— 该页在 3.7 落地，这里只把路由与参数带过去。"""
        vid = str(versionId or "").strip()
        loader = ""
        row = self._row(vid)
        if row:
            loader = str(row.get("loader", "") or "")
        self._push("resources/browse", {"version": vid, "loader": loader})

    @Slot(str)
    def openResourceManager(self, versionId: str) -> None:  # noqa: N802
        """进入"我的资源"（该页在 3.6 落地，路由先通）。"""
        self._push("resources/mods", {"version": str(versionId or "").strip()})

    @Slot()
    def browseAllResources(self) -> None:  # noqa: N802
        """不带版本筛选的资源浏览（B-14）。"""
        self._push("resources/browse", {})

    @Slot()
    def openResourceManagerForCurrent(self) -> None:  # noqa: N802
        """资源管理入口（B-15）：没选中版本时按旧界面提示"请先选择一个版本"。"""
        if not self._current_id:
            self.statusMessage.emit(_("version_select_first"), "error")
            return
        self.openResourceManager(self._current_id)

    # ─── 游戏进程（B-03 / B-04 的复用）─────────────────────

    @Property(str, notify=gameChanged)
    def gameState(self) -> str:  # noqa: N802
        return self._game_state or "idle"

    @Property(bool, notify=gameChanged)
    def gameRunning(self) -> bool:  # noqa: N802
        return self.gameState in BUSY_STATES

    @Property(bool, notify=gameChanged)
    def canLaunch(self) -> bool:  # noqa: N802
        """能不能启动：旧界面在这里就是"选中了版本且不在启动中"（`_on_launch` 只查 selected）。"""
        return bool(self._current_id) and self.gameState not in (STATE_LAUNCHING, STATE_WAITING)

    @Property(bool, notify=gameChanged)
    def canKill(self) -> bool:  # noqa: N802
        return self.gameState in BUSY_STATES

    @Slot(str)
    def launch(self, versionId: str = "") -> None:  # noqa: N802
        """启动游戏。`versionId` 为空时用当前选中的版本。"""
        version = str(versionId or "").strip() or self._current_id
        if not version:
            self.statusMessage.emit(_("version_select_first"), "error")
            return
        if self._game is None:
            logger.warning("Versions.launch(%s)：游戏服务不可用", version)
            return
        self._game.launch(version)

    @Slot()
    def killGame(self) -> None:  # noqa: N802
        if self._game is None:
            return
        self._game.kill()

    @Slot(str)
    def repair(self, versionId: str) -> None:  # noqa: N802
        """修复版本文件（B-22 的后半，D-167）。"""
        vid = str(versionId or "").strip() or self._current_id
        if not vid:
            self.statusMessage.emit(_("version_select_first"), "error")
            return
        if self._service is None:
            return
        if self._busy:
            self.statusMessage.emit(_("version_select_first"), "error")
            return
        self._busy = True
        self.busyChanged.emit()
        self._submit(self._service.repair(vid))

    @Slot()
    def cancelRepair(self) -> None:  # noqa: N802
        """请求取消正在跑的修复（**请求**：核心下一次查标志时才停）。"""
        if self._service is None:
            return
        self._service.cancel_repair()

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:  # noqa: N802
        """有后台操作在跑（行按钮据此禁用，防止重复提交）。"""
        return self._busy

    # ─── VersionService 的观察者回调（**任意线程**）────────

    def on_versions(self, rows: List[Dict[str, Any]], error: str) -> None:
        self._rows = list(rows or [])
        if error:
            self._set_load_state(STATE_ERROR, str(error))
        else:
            self._set_load_state(STATE_EMPTY if not self._rows else STATE_READY, "")
        self._rebuild()
        # 装完一个版本之后（3.3）：列表重扫完要**自动选中新装的那个** ——
        # 这是用户 2026-10-06 裁决的"装完自动回列表并选中"，也是旧界面
        # "清空输入框 + 刷新列表"的等价物。待选中的 id 由 `VersionService` 记着，
        # 取走即清（只用一次）。
        pending = ""
        consume = getattr(self._service, "consume_pending_selection", None)
        if callable(consume):
            try:
                pending = str(consume() or "")
            except Exception as e:  # noqa: BLE001 - 自动选中失败不该影响列表
                logger.warning("取待选中版本失败: %s", e)
        if pending and self._row(pending):
            self.select(pending)
        # 选中的版本可能已经不在了（被删/被改名）—— 详情跟着校正，别显示陈旧内容
        elif self._current_id and not self._row(self._current_id):
            self._clear_selection()

    def on_status(self, key: str, level: str, params: Dict[str, Any]) -> None:
        self.statusMessage.emit(self._status_text(key, params), level)

    def on_result(self, kind: str, ok: bool, data: Dict[str, Any]) -> None:
        """一次操作结束：清 busy；删除/重命名成功后重扫列表。"""
        if self._busy:
            self._busy = False
            self.busyChanged.emit()
        if not ok:
            return
        if kind in ("rename", "remove") and not data.get("cancelled"):
            if self._service is not None:
                self._submit(self._service.load(force=True))
        if kind == "rename" and data.get("newName"):
            if self._current_id == data.get("id"):
                self.select(str(data["newName"]))

    def on_state(self, state: str) -> None:
        self._game_state = state
        self.gameChanged.emit()

    def on_killed(self, exit_code: int) -> None:
        self.refresh_game_state()

    def on_launch_result(
        self, version_id: str, target_version: Optional[str], success: bool, error: str
    ) -> None:
        """启动结果只用来刷新按钮可用性 —— 状态文案由 `HomeBridge` 发（见模块文档）。"""
        self.refresh_game_state()

    def report_game_state(self, state: str) -> None:
        """`_GameWatcher` 的回调入口：写属性 + 发信号（可能在任务线程里）。"""
        self._game_state = str(state or "")
        self.gameChanged.emit()

    def refresh_game_state(self) -> None:
        """主动问一次 `GameService` 当前状态（主线程里调，QML 也可见）。"""
        state = ""
        if self._game is not None:
            try:
                state = str(self._game.state or "")
            except Exception as e:  # noqa: BLE001 - 取不到就按"空闲"显示
                logger.warning("取游戏状态失败: %s", e)
        self.report_game_state(state)

    # ─── 内部 ───────────────────────────────────────────────

    def _rebuild(self) -> None:
        if self._service is None:
            self._visible = []
        else:
            try:
                self._visible = self._service.filtered(self._filter, self._sort_key)
            except Exception as e:  # noqa: BLE001 - 过滤失败退化成"全显示"
                logger.warning("过滤版本列表失败: %s", e)
                self._visible = list(self._rows)
        self.versionsChanged.emit()

    def _row(self, version_id: str) -> Optional[Dict[str, Any]]:
        for row in self._rows:
            if row.get("id") == version_id:
                return row
        return None

    def _clear_selection(self) -> None:
        changed = bool(self._current_id) or bool(self._detail)
        self._current_id = ""
        self._detail = {}
        if changed:
            self.selectionChanged.emit()
            self.gameChanged.emit()  # 见 select()：canLaunch 也取决于选中

    def _set_load_state(self, state: str, error: str = "") -> None:
        changed = state != self._load_state or error != self._load_error
        self._load_state = state
        self._load_error = error
        if changed:
            self.loadStateChanged.emit()

    def _submit(self, handle: Any, *, busy: bool = False) -> None:
        """记下任务句柄；`busy=True` 时置忙标志（任务结束由 `on_result` 清）。"""
        if handle is None:
            return
        if busy:
            self._busy = True
            self.busyChanged.emit()

    def _refresh_game_state(self) -> None:
        self.refresh_game_state()

    def _push(self, route_id: str, params: Dict[str, Any]) -> None:
        if self._nav is None:
            logger.debug("Versions 跳转 %s 失败：Nav 未注入", route_id)
            return
        try:
            self._nav.push(route_id, params)
        except Exception as e:  # noqa: BLE001
            logger.warning("跳转 %s 失败: %s", route_id, e)

    @staticmethod
    def _status_text(key: str, params: Dict[str, Any]) -> str:
        """把服务发来的 i18n 键翻成一句话。

        键都以**字面量**出现（`_("version_removed", version=…)` 那种），
        这样 `scripts/check_i18n.py` 的静态扫描看得见 —— 写成变量就查不到了。
        """
        data = dict(params or {})
        if key == "version_loading_installed":
            return _("version_loading_installed")
        if key == "version_loaded":
            return _("version_loaded", count=data.get("count", 0))
        if key == "version_load_failed":
            return _("version_load_failed", error=data.get("error", ""))
        if key == "version_renaming":
            return _("version_renaming", old=data.get("old", ""), new=data.get("new", ""))
        if key == "version_removed":
            return _("version_removed", version=data.get("version", ""))
        if key == "version_remove_failed":
            return _("version_remove_failed", version=data.get("version", ""))
        if key == "version_remove_error":
            return _("version_remove_error", error=data.get("error", ""))
        if key == "version_select_first":
            return _("version_select_first")
        if key == "version_verifying":
            return _("version_verifying", version=data.get("version", ""))
        if key == "version_verify_ok":
            return _("version_verify_ok", total=data.get("total", 0))
        if key == "version_verify_bad":
            return _("version_verify_bad", total=data.get("total", 0), invalid=data.get("invalid", 0))
        if key == "version_verify_empty":
            return _("version_verify_empty")
        if key == "version_verify_failed":
            return _("version_verify_failed", error=data.get("error", ""))
        if key == "version_detail_missing":
            return _("version_detail_missing")
        if key == "version_open_folder_failed":
            return _("version_open_folder_failed", error=data.get("error", ""))
        if key == "version_repairing":
            return _("version_repairing", version=data.get("version", ""))
        if key == "version_repair_ok":
            return _("version_repair_ok", repaired=data.get("repaired", 0))
        if key == "version_repair_none":
            return _("version_repair_none")
        if key == "version_repair_failed":
            return _("version_repair_failed", remaining=data.get("remaining", 0))
        if key == "version_repair_error":
            return _("version_repair_error", error=data.get("error", ""))
        if key == "version_repair_cancelled":
            return _("version_repair_cancelled", version=data.get("version", ""))
        if key == "rename_instance_success":
            return _("rename_instance_success", old=data.get("old", ""), new=data.get("new", ""))
        if key == "rename_instance_invalid":
            return _("rename_instance_invalid")
        if key == "rename_instance_exists":
            return _("rename_instance_exists", name=data.get("name", ""))
        if key == "rename_instance_error":
            return _("rename_instance_error", error=data.get("error", ""))
        if key == "deleting_version":
            return _("deleting_version", version=data.get("version", ""))
        return key


__all__ = ["VersionsBridge", "BUSY_STATES"]
