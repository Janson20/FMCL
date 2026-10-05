"""壳层桥 —— QML 侧的 `Shell` 上下文属性（阶段 2 任务 2.12）。

## 它是什么

标题栏与底部状态条要用的**壳层**信息：当前页面标题、状态文本、后台任务数、
一级导航项、全局搜索与通知中心的入口。**不含任何业务**：它不查版本、不查服务器，
只把 `Nav`（路由）与 `Tasks`（任务数）的状态转成 QML 好用的形状。

## 为什么是"订阅 Nav 的信号"而不是"让 QML 自己组合"

另一种做法是 Shell 只暴露 `navItems()`，标题栏里写 `Nav.currentTitleKey` 让 QML
自己拼。这里选**订阅**，理由有三条：

1. **单一真相来源**：路由状态只有 `Nav` 一份，Shell 再算一次就是第二份实现
   （迁移红线 2：同一件事只搬一次）；
2. **标题栏是一整块**：标题、描述、返回可用性、面包屑都在同一个组件里，
   每个使用处各写一遍 `Nav.*` 只会让漏写一处就静默不刷新；
3. 装配期 `Shell` **必须**知道 `Nav`（`navItems()` 也要它），既然依赖已经存在，
   再让 QML 绕一圈没有收益。

代价是 Shell 依赖 Nav 的存在，所以两处都做了降级：`Nav` 缺席时属性返回空值、
`navItems()` 返回空表、`canGoBack` 为 false —— 界面缺功能但不会崩。

## 为什么 `currentTitle` 之外还有 `currentTitleKey`

`currentTitle` 是**翻译后**的字符串（命令式调用、日志、`console.log` 用着方便），
但它对 QML 绑定是"不响应语言切换的字符串"——Python 侧翻好的值不会自己重算
（契约 §5.2 的同一理由）。QML 里请绑 `Tr.map[Shell.currentTitleKey]`。
装了 `Tr`（`use_engine` 会找到）时 `currentTitle` 也会跟着语言变化重发信号，
但**不要**把这句话当成 QML 可以省掉 `Tr.map[…]` 的依据。

## `setStatus` 的 10 秒语义（对照表 A-09）

旧 `set_status(text, level)` 的行为是"显示后 10 秒自动清空"。QML 版**保留**它：
用 `QTimer`（可重入：再次调用会重新计时），而不是每条状态各自 `QTimer.singleShot`
——后者在连续状态更新时会留下多个未取消的定时器，把新状态提前清掉（旧实现没有这问题，
因为它是"一个 pending after id + 覆盖"；这里用同一个思路）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

logger = logging.getLogger("app.bridges.shell_bridge")

#: 旧 `set_status` 的自动清空时间（毫秒）。改动它要同步 `04-parity-matrix.md` 的 A-09。
DEFAULT_STATUS_TIMEOUT_MS = 10_000

#: 合法的状态级别。非法级别**降级成 info** 并记 warning，而不是往 navFailed 这类
#: 业务信号上塞（那不是导航失败）。
STATUS_LEVELS = ("info", "success", "warning", "error")


class ShellBridge(QObject):
    """上下文属性 `Shell`。"""

    #: 当前页面标题/描述变了（路由变化，或装了 Tr 之后语言变化）。
    titleChanged = Signal()
    #: 状态文本或级别变了（含自动清空）。
    statusChanged = Signal()
    #: 后台任务数变了。
    taskCountChanged = Signal()
    #: 全局搜索请求（具体搜索是阶段 3 的事）。
    searchRequested = Signal(str)
    #: 通知中心被要求切换（每次调用发一次，由浮层自己翻可见性）。
    notificationCenterToggled = Signal()

    def __init__(
        self,
        nav: Optional[QObject] = None,
        tasks: Optional[QObject] = None,
        parent: Optional[QObject] = None,
        status_timeout_ms: int = DEFAULT_STATUS_TIMEOUT_MS,
    ) -> None:
        """
        Args:
            nav: 注入 `NavBridge`（不传则在 `use_engine` / 首次读取时按注册表解析）。
            tasks: 注入 `TaskBridge`（同上）。
            parent: Qt 父对象。
            status_timeout_ms: 状态自动清空时间；**测试用**（默认就是 A-09 的 10 秒，
                正常路径不要传）。
        """
        super().__init__(parent)
        self._nav = nav
        self._tasks = tasks
        self._tr: Optional[Any] = None
        self._title_key = ""
        self._description_key = ""
        self._status_text = ""
        self._status_level = "info"
        self._task_count = 0
        self._timeout_ms = max(1, int(status_timeout_ms))

        # 自动清空：单发 + 每次设置重新计时（与旧实现的"覆盖 pending 回调"等价）。
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._timeout_ms)
        self._timer.timeout.connect(self.clearStatus)

        if nav is not None:
            self._bind_nav(nav)
        if tasks is not None:
            self._bind_tasks(tasks)

    # ─── 装配期注入 ─────────────────────────────────────────

    def use_engine(self, engine: Any) -> None:
        """`main_qml.register_bridges` 在注册后会调它。

        **为什么用引擎的桥表而不是自己去猜**：装配顺序里 `Nav` 先于 `Shell` 注册，
        所以此刻 `engine._fmcl_bridges["Nav"]` 一定是最新的那个（"自己去猜"被带偏的教训
        记在 `theme_bridge` 的模块文档里 —— 缺陷 D-153 删掉的正是那条兜底，这里不重犯）。
        """
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        nav = registry.get("Nav")
        if nav is not None and self._nav is None:
            self._bind_nav(nav)
            logger.debug("Shell 已接上 Nav（来自引擎桥表）")
        tasks = registry.get("Tasks")
        if tasks is not None and self._tasks is None:
            self._bind_tasks(tasks)
            logger.debug("Shell 已接上 Tasks（来自引擎桥表）")
        tr = registry.get("Tr")
        if tr is not None:
            self._bind_tr(tr)

    def bind(self, context: Any = None) -> None:
        """装配方约定的钩子（本桥不需要 `AppContext`，只记一条 debug）。"""
        logger.debug("ShellBridge.bind(%r)：壳层不依赖 AppContext", type(context).__name__)

    # ─── 标题（随路由变） ───────────────────────────────────

    @Property(str, notify=titleChanged)
    def currentTitle(self) -> str:  # noqa: N802
        """当前页面的**译文**。绑定请优先用 `currentTitleKey` + `Tr.map[…]`（见模块文档）。"""
        return self._translate(self._title_key)

    @Property(str, notify=titleChanged)
    def currentDescription(self) -> str:  # noqa: N802
        return self._translate(self._description_key)

    @Property(str, notify=titleChanged)
    def currentTitleKey(self) -> str:  # noqa: N802
        return self._title_key

    @Property(str, notify=titleChanged)
    def currentDescriptionKey(self) -> str:  # noqa: N802
        return self._description_key

    # ─── 状态条 ─────────────────────────────────────────────

    @Property(str, notify=statusChanged)
    def statusText(self) -> str:  # noqa: N802
        return self._status_text

    @Property(str, notify=statusChanged)
    def statusLevel(self) -> str:  # noqa: N802
        return self._status_level

    @Property(int, constant=True)
    def statusTimeout(self) -> int:  # noqa: N802
        """自动清空时间（毫秒）。做成属性是为了让"10 秒语义"可断言、可显示。"""
        return self._timeout_ms

    @Property(bool, notify=statusChanged)
    def hasStatus(self) -> bool:  # noqa: N802
        return bool(self._status_text)

    @Slot(str)
    @Slot(str, str)
    def setStatus(self, text: str, level: str = "info") -> None:  # noqa: N802
        """设状态条文本（旧 `set_status` 语义：**10 秒后自动清空**，再次调用重新计时）。

        空文本等价于清空。非法级别降级为 `info`（记 warning），不抛异常、不发失败信号
        —— 状态条是"提示"，它的参数写错不该变成一个需要用户处理的错误。
        """
        normalized = str(level or "info").strip().lower()
        if normalized not in STATUS_LEVELS:
            logger.warning("未知状态级别 %r，已降级为 info（合法值：%s）", level, " | ".join(STATUS_LEVELS))
            normalized = "info"
        message = str(text or "")
        if not message:
            self.clearStatus()
            return
        self._status_text = message
        self._status_level = normalized
        self._timer.start(self._timeout_ms)  # start() 会自动重排已有计时
        self.statusChanged.emit()
        logger.debug("状态条：%s（%s，%d ms 后自动清空）", message, normalized, self._timeout_ms)

    @Slot()
    def clearStatus(self) -> None:  # noqa: N802
        """立即清空状态文本（定时器到点也会走这里）。"""
        self._timer.stop()
        if not self._status_text and self._status_level == "info":
            return
        self._status_text = ""
        self._status_level = "info"
        self.statusChanged.emit()

    # ─── 后台任务（绑 Tasks.activeCount） ───────────────────

    @Property(int, notify=taskCountChanged)
    def backgroundTaskCount(self) -> int:  # noqa: N802
        tasks = self._tasks_obj()
        if tasks is None:
            return self._task_count
        try:
            return int(tasks.property("activeCount") or 0)
        except Exception as e:  # noqa: BLE001 - 任务桥异常不该让状态条炸掉
            logger.warning("读 Tasks.activeCount 失败: %s", e)
            return self._task_count

    @Property(bool, notify=taskCountChanged)
    def busy(self) -> bool:
        return self.backgroundTaskCount > 0

    # ─── 一级导航项 ─────────────────────────────────────────

    @Slot(result="QVariantList")
    def navItems(self) -> List[Dict[str, Any]]:  # noqa: N802
        """一级导航项 `[{id, title_key, icon, source}, …]`：内置 12 项 + 已登记的插件页。

        做成槽（而不是属性）是任务书冻结的签名；代价是**槽不参与绑定跟踪**，
        所以 `Navigation.qml` 在 `Nav.pluginRouteAdded` 时显式重取一次。
        """
        nav = self._nav_obj()
        if nav is None:
            logger.warning("Shell.navItems()：Nav 不可用，返回空表（界面将没有一级导航）")
            return []
        return nav.nav_items()

    # ─── 全局搜索与通知中心（阶段 3 接实现） ────────────────

    @Slot(str)
    def globalSearch(self, query: str) -> None:  # noqa: N802
        """发一次搜索请求；空串不发（避免清空输入框时也触发一次搜索）。"""
        text = str(query or "").strip()
        if not text:
            logger.debug("全局搜索：空关键词，不发出请求")
            return
        self.searchRequested.emit(text)

    @Slot()
    def toggleNotificationCenter(self) -> None:  # noqa: N802
        """切换通知中心（浮层由 2.13/2.15 的宿主实现，这里只发请求）。"""
        self.notificationCenterToggled.emit()

    # ─── 内部 ───────────────────────────────────────────────

    def _nav_obj(self) -> Optional[Any]:
        """惰性解析 Nav：显式注入 → 引擎桥表 → 模块注册表。"""
        if self._nav is None:
            from app.bridges import nav_bridge

            shared = nav_bridge.current_nav()
            if shared is not None:
                self._bind_nav(shared)
        return self._nav

    def _tasks_obj(self) -> Optional[Any]:
        return self._tasks

    def _bind_nav(self, nav: Any) -> None:
        self._nav = nav
        # 只接 `routeChanged`：标题/描述只取决于**当前**路由，面包屑怎么变与它无关
        # （两个信号都接会让每次导航发两遍 titleChanged）。
        signal = getattr(nav, "routeChanged", None)
        if signal is None:
            logger.warning("Nav 桥没有 routeChanged 信号，标题不会随路由刷新")
        else:
            try:
                signal.connect(self._on_route_changed)
            except Exception as e:  # noqa: BLE001 - 接不上只影响标题刷新
                logger.warning("接 Nav.routeChanged 失败: %s", e)
        self._read_titles()
        self.titleChanged.emit()

    def _bind_tasks(self, tasks: Any) -> None:
        self._tasks = tasks
        signal = getattr(tasks, "activeChanged", None)
        if signal is None:
            logger.warning("Tasks 桥没有 activeChanged 信号，后台任务数不会自动刷新")
            return
        try:
            signal.connect(self._on_tasks_changed)
        except Exception as e:  # noqa: BLE001
            logger.warning("接 Tasks.activeChanged 失败: %s", e)
        self.taskCountChanged.emit()

    def _bind_tr(self, tr: Any) -> None:
        """接 `Tr.languageChanged` —— 只为让 `currentTitle` / `currentDescription` 也跟着语言走。"""
        self._tr = tr
        signal = getattr(tr, "languageChanged", None)
        if signal is None:
            return
        try:
            signal.connect(self.titleChanged.emit)
        except Exception as e:  # noqa: BLE001
            logger.warning("接 Tr.languageChanged 失败: %s", e)
        self.titleChanged.emit()

    def _on_route_changed(self, *_args: Any) -> None:
        self._read_titles()
        self.titleChanged.emit()

    def _on_tasks_changed(self) -> None:
        self.taskCountChanged.emit()

    def _read_titles(self) -> None:
        nav = self._nav_obj()
        if nav is None:
            return
        try:
            self._title_key = str(nav.property("currentTitleKey") or "")
            self._description_key = str(nav.property("currentDescriptionKey") or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("读 Nav.currentTitleKey 失败: %s", e)

    def _translate(self, key: str) -> str:
        """查译文；缺 `Tr` 或键不存在时回退到**键名**（与旧 `_()` 的行为一致）。"""
        if not key:
            return ""
        tr = getattr(self, "_tr", None)
        if tr is None:
            return key
        try:
            table = tr.property("map") or {}
            return str(table.get(key, key))
        except Exception as e:  # noqa: BLE001
            logger.warning("查译文 %s 失败: %s", key, e)
            return key


__all__ = ["DEFAULT_STATUS_TIMEOUT_MS", "STATUS_LEVELS", "ShellBridge"]
