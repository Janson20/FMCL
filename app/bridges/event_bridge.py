"""事件桥（阶段 2.5）—— 把 ``app.events.EventBus`` 的键转成 Qt 信号。

QML 侧通过上下文属性 ``Events`` 使用（注册名由阶段 2 契约第四节冻结）::

    Connections { target: Events; function onTaskFinished(p) { ... } }
    Connections { target: Events; function onEvent(name, payload) { ... } }

## 事件键的核实结论（写代码时实测，不是猜的）

``grep`` 全仓库 ``publish(`` 的结论：**当前没有任何生产代码 publish 过事件**。
``services/base.py:90`` 的 ``Service.publish`` 是唯一的生产发布入口，没有任何调用点；
``app/events.py`` 只有总线实现本身；用过的事件键只出现在测试里
（``tests/test_services_foundation.py`` 的 ``thing.done`` / ``e`` / ``a`` 等）。
所以本模块的键名**不是从服务里抄出来的**，而是由契约 5.4 要求的信号名反推出的键名，
并把两种写法一起登记，避免"键名对不上 → 具名信号永远是死代码"这种静默失效：

===============  ==================  =====================
总线键（点号式）  总线键（驼峰别名）   Qt 信号
===============  ==================  =====================
task.started      taskStarted         taskStarted
task.finished     taskFinished        taskFinished
task.failed       taskFailed          taskFailed
toast.requested   toastRequested      toastRequested
status.changed    statusChanged       statusChanged
achievement.unlocked  achievementUnlocked  achievementUnlocked
===============  ==================  =====================

阶段 3 的服务 publish 时请用点号式键（与仓库既有风格一致）；用别名同样能命中具名信号
（两种写法都在构造时订阅了，见 ``WATCHED_BY_DEFAULT``）。

## 未具名事件为什么要 ``watch()``

``EventBus`` 是**按 key 订阅**的，没有通配订阅（``app/events.py`` 已冻结，本任务不得
修改）。桥没登记的键，事件根本到不了 Qt 这一侧。契约 5.4 的"任何未具名的事件也发
出来"因此这样落地：构造时自动 watch 6 个具名键 + ``extra_events``，运行期还能
``watch(name)`` 补登记。**阶段 3 加事件不用改桥** —— 装配处传 ``extra_events``，或调
``Events.watch("新键")`` 即可，凡是没有具名信号的键一律从通用 ``event(name, payload)``
出去。

（考虑过并否决的替代方案：用 QTimer 轮询 ``bus.history()`` 把新记录全发出来。否决理由
是它引入了延迟、需要去重、且在环形缓冲被截断时会静默丢事件。）

## 跨线程

``services/`` 的 worker 线程 publish 时，总线**在 worker 线程上同步**调用本桥的
handler；handler 立刻 ``emit``。Qt 的 ``AutoConnection`` 在"发送线程 != 接收者线程"时
自动降级为 Queued，把 ``QVariantMap`` 拷进接收者线程的事件队列。

**前提**：接收者属于主线程且该线程有事件循环 —— 所以本对象**必须建在 Qt 主线程**，
构造时会检查并 ``logger.error``（见 :meth:`EventBridge._check_owner_thread`）。

实测（本机 Qt 6.7.3）：worker 线程 emit → 主线程 QObject 槽、主线程普通 Python 函数、
QML ``Connections`` 处理器，三者都只在主线程被调用；payload 里塞自定义 Python 对象
也不会在排队搬运时丢失。证据见 ``tests/test_event_bridge.py``。

## 桥上只做三件事

订阅总线、转换 payload（``EventRecord`` → ``dict``）、发信号。没有任何业务规则。
"""

from __future__ import annotations

import logging
import threading
import weakref
from typing import Any, Callable, Dict, Iterable, List, Optional

from PySide6.QtCore import QCoreApplication, QObject, Signal, Slot

from app.events import EventBus

logger = logging.getLogger(__name__)

#: 契约第四节冻结的 QML 注册名（由 2.2 入口 ``setContextProperty`` 注册）。
QML_NAME = "Events"

#: 总线键 → 具名信号名。**唯一真相**：阶段 3 的服务 publish 前先看这张表。
EVENT_SIGNAL_MAP: Dict[str, str] = {
    "task.started": "taskStarted",
    "task.finished": "taskFinished",
    "task.failed": "taskFailed",
    "toast.requested": "toastRequested",
    "status.changed": "statusChanged",
    "achievement.unlocked": "achievementUnlocked",
}

#: 驼峰别名集合：``publish("taskStarted")`` 与 ``publish("task.started")`` 等价。
_SIGNAL_NAMES = frozenset(EVENT_SIGNAL_MAP.values())

#: 构造时默认订阅的键：6 个点号式键 + 6 个驼峰别名。
#: 别名也要**真的订阅**才有用 —— EventBus 按 key 订阅，不订阅的键到不了桥
#: （第一版只订阅了点号式，实测 ``publish("taskStarted")`` 直接消失）。
WATCHED_BY_DEFAULT: tuple = tuple(EVENT_SIGNAL_MAP) + tuple(EVENT_SIGNAL_MAP.values())


def resolve_signal_name(event: str) -> Optional[str]:
    """事件键 → 具名信号名；未登记（含别名）时返回 ``None``，调用方走通用信号。"""
    if event in EVENT_SIGNAL_MAP:
        return EVENT_SIGNAL_MAP[event]
    if event in _SIGNAL_NAMES:
        return event
    return None


def _make_handler(bridge: "EventBridge", event: str) -> Callable[..., None]:
    """造一个把事件名钉在原地的总线 handler。

    两层考虑：

    1. ``EventBus`` 调 handler 时只传 ``**payload``，事件名只能靠闭包记住；
    2. 闭包里只放**弱引用**：否则总线会一直持有桥，桥永远不会被回收
       （总线活多久，桥就活多久）。弱引用失效时静默跳过即可 —— 桥已经没了，
       这条事件本来就没人要。
    """

    ref = weakref.ref(bridge)

    def _handler(**payload: Any) -> None:
        target = ref()
        if target is None:
            return
        target._dispatch(event, payload)

    _handler.__qualname__ = f"EventBridge._handler[{event}]"
    return _handler


def _make_handler(bridge: "EventBridge", event: str) -> Callable[..., None]:
    """造一个把事件名钉在原地的总线 handler。

    两层考虑：

    1. ``EventBus`` 调 handler 时只传 ``**payload``，事件名只能靠闭包记住；
    2. 闭包里只放**弱引用**：否则总线会一直持有桥，桥永远不会被回收
       （总线活多久，桥就活多久）。弱引用失效时静默跳过即可 —— 桥已经没了，
       这条事件本来就没人要。
    """

    ref = weakref.ref(bridge)

    def _handler(**payload: Any) -> None:
        target = ref()
        if target is None:
            return
        target._dispatch(event, payload)

    _handler.__qualname__ = f"EventBridge._handler[{event}]"
    return _handler


class EventBridge(QObject):
    """``EventBus`` → Qt 信号。构造后立即可用，不需要 ``start()``。

    生命周期用**显式 detach**，不接 ``QObject.destroyed`` 自动退订 —— 为什么：

    1. ``self.destroyed.connect(self._on_destroyed)``（接收者是发送者自己）在 PySide6
       6.7.3 下**收不到信号**（同一个对象换成外部接收者就能收到）；
    2. 换成模块级函数能收到，但信号传进来的参数是**退化成 ``QObject`` 的包装对象**，
       它已经没有 Python 子类的方法（``getattr(obj, "detach")`` 为 None），拿不到桥；
    3. 换成捕获桥的闭包能工作，但解释器退出/循环回收时会刷 PySide6 的
       ``RuntimeWarning: Skipping callback call ... because the callback object is being
       destructed``，在清理回调里再 ``disconnect`` 又会得到 ``RuntimeWarning: Failed to
       disconnect ... from signal "destroyed()"``。

    所以"桥被销毁后 publish 不崩"改由 :meth:`_dispatch` 的 ``RuntimeError`` 分支保证，
    装配方（2.2 入口）在退出路径显式调一次 :meth:`detach`。不调也不致命：handler 里只有
    弱引用（不会把桥钉在总线上），桥一死它们就是空转闭包。
    """

    taskStarted = Signal(dict)
    taskFinished = Signal(dict)
    taskFailed = Signal(dict)
    toastRequested = Signal(dict)
    statusChanged = Signal(dict)
    achievementUnlocked = Signal(dict)
    #: 通用兜底：任何**没有**具名信号的事件都从这里出去（阶段 3 加事件不用改桥）。
    event = Signal(str, dict)
    #: 订阅集合变化（watch / unwatch / detach），供诊断界面用。
    watchedChanged = Signal()

    def __init__(
        self,
        bus: Optional[EventBus] = None,
        extra_events: Iterable[str] = (),
        parent: Optional[QObject] = None,
    ) -> None:
        """
        Args:
            bus: 要包装的事件总线。``None`` 时取 ``AppContext.current().events``；
                连上下文都没有就新建一条（记 warning）—— 生产路径**必须**显式传。
            extra_events: 阶段 3 要额外收的键（没有具名信号，走通用 ``event``）。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._bus = bus if bus is not None else self._fallback_bus()
        self._lock = threading.RLock()
        self._handlers: Dict[str, Callable[..., None]] = {}
        self._detached = False
        self._check_owner_thread()
        # 逐个登记：watch() 的签名是"一个键"，一次传一个列表会被当成一个畸形键名
        # （实测：整表传进去只会订阅到一个假键）。
        for event in WATCHED_BY_DEFAULT + tuple(str(e) for e in extra_events):
            self.watch(event)

    @staticmethod
    def _fallback_bus() -> EventBus:
        from app.context import AppContext  # 只在兜底路径上 import，避免硬依赖

        ctx = AppContext.current()
        events = getattr(ctx, "events", None) if ctx is not None else None
        if isinstance(events, EventBus):
            return events
        logger.warning("EventBridge 未注入 EventBus，且 AppContext.current() 也没有，"
                       "本桥将挂到一条新的空总线上（生产路径必须显式传 ctx.events）")
        return EventBus()

    # ─── 线程前提 ───────────────────────────────────────────

    def _check_owner_thread(self) -> None:
        """检查"桥建在主线程"这个 Queued 投递的前提。

        不满足时**只报错不抛异常**：QML 入口在装配期崩掉比"信号收不到"更难查，
        而这两种失败都会在日志里留下明确的一行。
        """
        if QCoreApplication.instance() is None:
            logger.error(
                "EventBridge 在**没有 QCoreApplication** 的进程里创建："
                "worker 线程 emit 的信号会排进一个不存在的事件循环，永远不会投递"
            )
            return
        if threading.current_thread() is not threading.main_thread():
            logger.error(
                "EventBridge 建在非主线程（%s）：接收者必须属于主线程且有事件循环，"
                "否则 Queued 投递会落到错误的线程上",
                threading.current_thread().name,
            )

    # ─── 订阅 ───────────────────────────────────────────────

    @Slot(str, result=int)
    def watch(self, event: str) -> int:
        """登记一个事件键（幂等）。返回 1 表示本次真的新增了订阅。

        为什么必须显式登记：``EventBus`` 按 key 订阅、没有通配，桥不登记的键
        根本到不了 Qt 这一侧（详见模块文档）。
        """
        added = 0
        with self._lock:
            key = str(event or "").strip()
            if not key or key in self._handlers:
                return 0
            if self._detached:
                logger.warning("桥已 detach，watch(%r) 被忽略", event)
                return 0
            handler = _make_handler(self, key)
            self._handlers[key] = handler
            self._bus.subscribe(key, handler)
            added = 1
        self._emit_watched_changed()
        return added

    @Slot(str, result=bool)
    def unwatch(self, event: str) -> bool:
        """退订一个事件键。返回是否真的退掉了一个订阅。"""
        key = str(event or "").strip()
        with self._lock:
            handler = self._handlers.pop(key, None)
        if handler is None:
            return False
        try:
            self._bus.unsubscribe(key, handler)
        except Exception as e:  # noqa: BLE001 - 退订失败不影响其他订阅
            logger.debug("退订 %s 失败: %s", key, e)
        self._emit_watched_changed()
        return True

    @Slot(result="QStringList")
    def watchedEvents(self) -> List[str]:
        """当前已订阅的事件键（排序，便于诊断与测试断言）。"""
        with self._lock:
            return sorted(self._handlers)

    def detach(self) -> int:
        """摘掉全部订阅（幂等）；返回摘掉的条数。退出/销毁路径用。"""
        with self._lock:
            if self._detached:
                return 0
            self._detached = True
            handlers = list(self._handlers.items())
            self._handlers.clear()
        removed = 0
        for key, handler in handlers:
            try:
                self._bus.unsubscribe(key, handler)
                removed += 1
            except Exception as e:  # noqa: BLE001
                logger.debug("退订 %s 失败: %s", key, e)
        self._emit_watched_changed()
        return removed

    def _emit_watched_changed(self) -> None:
        try:
            self.watchedChanged.emit()
        except RuntimeError:  # 已销毁的 QObject 不能再 emit
            pass

    # ─── 派发 ───────────────────────────────────────────────

    def _dispatch(self, event: str, payload: Dict[str, Any]) -> None:
        """在**发布者线程**上被 ``EventBus`` 同步调用；只做 payload 转换 + emit。

        这里**不**提前判断"桥是否已 detach"：detach 会把订阅从总线上摘掉，正常
        路径下根本不会再有 handler 调到这儿；而"漏摘的 handler + 已销毁的桥"这个
        兜底场景恰恰要靠下面的 ``RuntimeError`` 分支兜住（实测：已销毁的 QObject
        上 ``emit`` 抛 ``RuntimeError: Internal C++ object already deleted``）。
        """
        signal_name = resolve_signal_name(event)
        data = dict(payload)  # 立刻拷一份：Queued 连接要跨线程搬走它
        try:
            if signal_name is None:
                self.event.emit(event, data)
            else:
                getattr(self, signal_name).emit(data)
        except RuntimeError as e:
            # C++ 对象已销毁（QML 引擎重载 / 窗口关闭）：静默丢弃。
            # 一个已死的订阅者绝不能把服务的 publish 打断。
            logger.debug("事件 %s 到达时桥已销毁，已丢弃: %s", event, e)
            self.detach()
        except Exception as e:  # noqa: BLE001 - 派发失败不该影响发布者
            logger.error("事件 %s 派发失败: %s", event, e, exc_info=True)

    # ─── 诊断（QML 可调用） ────────────────────────────────

    #: 契约 5.4 要求的是 "Q_INVOKABLE 方法"。**实测**：PySide6 6.7.3 的
    #: ``PySide6.QtCore`` 里根本没有 ``Q_INVOKABLE``（``AttributeError``），
    #: 让方法可被 QML 调用的等价手段就是 ``@Slot``；未加装饰器的普通方法在 QML 里
    #: 会报 ``TypeError: Property 'x' of object ... is not a function``（都实测过）。
    #: 叠加两个 ``@Slot`` 是为了让 QML 既能写 ``history()`` 也能写 ``history(2)``。
    @Slot(result="QVariantList")
    @Slot(int, result="QVariantList")
    def history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """最近 ``limit`` 条事件（最新在最后），语义与顺序照 ``EventBus.history``。

        返回 ``[{event, payload, handlerCount}, ...]`` 而不是 ``EventRecord`` 具名元组：
        QML 读不到 NamedTuple 的字段名，只认属性名（实测 6.7.3）。这是**参数转换**。

        ``limit`` 原样交给 ``EventBus.history``（它用切片实现：``0`` 会返回**全部**
        历史、负数会丢掉前 ``|n|`` 条）—— 桥上不额外纠正，否则同一个参数在两处含义
        不同，更难查。
        """
        try:
            count = int(limit)
        except (TypeError, ValueError):
            count = 20
        return [
            {"event": r.event, "payload": dict(r.payload), "handlerCount": r.handler_count}
            for r in self._bus.history(count)
        ]


__all__ = ["EventBridge", "EVENT_SIGNAL_MAP", "QML_NAME", "resolve_signal_name"]
