"""事件总线 —— 服务层与 UI 层之间的单向通知通道。

为什么需要它：服务层不能 import UI，但需要"通知"界面（例如"下载进度更新了"、
"插件被启用了"）。阶段 2 的 Qt 实现会把 ``publish`` 接到 Qt 信号上；阶段 1
的 Tk 实现则通过 ``TaskRunner.dispatch`` 切回主线程。

线程约定：``publish`` 在**调用者线程**上同步执行所有 handler。服务层因此
必须假设 handler 可能跑在任意线程；需要触碰界面的 handler 要自己
``context.tasks.dispatch(...)``。这个"不做隐式切换"的决定是有意的：
隐式切换会让调用顺序变得不可预测，也让单元测试难以断言。
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any, Callable, Dict, List, NamedTuple

logger = logging.getLogger(__name__)

Handler = Callable[..., None]


class EventRecord(NamedTuple):
    """``events.history()`` 返回的历史条目，便于排查"信号发了但没人收"。"""

    event: str
    payload: Dict[str, Any]
    handler_count: int


class EventBus:
    """极简发布/订阅。

    设计取舍：

    - **同步派发**：便于测试与顺序保证；不做线程池、不做队列。
    - **handler 异常不中断其他 handler**：记录日志后继续，避免一个坏
      订阅者让整条业务链断掉。
    - **历史环形缓冲**：保留最近 ``history_size`` 条，仅用于诊断。
    """

    def __init__(self, history_size: int = 200) -> None:
        self._lock = threading.RLock()
        self._handlers: Dict[str, List[Handler]] = defaultdict(list)
        self._history: List[EventRecord] = []
        self._history_size = max(0, history_size)
        self._publish_depth = 0
        self.log = logging.getLogger("app.events")

    # ─── 订阅 ───────────────────────────────────────────────

    def subscribe(self, event: str, handler: Handler) -> None:
        """注册 handler。同一 (event, handler) 重复注册会被忽略。"""
        if not callable(handler):
            raise TypeError(f"handler 必须可调用，收到 {type(handler).__name__}")
        with self._lock:
            handlers = self._handlers[event]
            if handler not in handlers:
                handlers.append(handler)

    def unsubscribe(self, event: str, handler: Handler) -> None:
        """注销 handler；不存在时静默返回（便于 stop() 里无条件清理）。"""
        with self._lock:
            handlers = self._handlers.get(event)
            if not handlers:
                return
            try:
                handlers.remove(handler)
            except ValueError:
                return
            if not handlers:
                self._handlers.pop(event, None)

    def unsubscribe_all(self, handler: Handler) -> int:
        """把某个 handler 从所有事件上摘掉，返回解除的订阅数。"""
        removed = 0
        with self._lock:
            for event in list(self._handlers):
                handlers = self._handlers[event]
                before = len(handlers)
                self._handlers[event] = [h for h in handlers if h is not handler]
                removed += before - len(self._handlers[event])
                if not self._handlers[event]:
                    self._handlers.pop(event, None)
        return removed

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()

    # ─── 发布 ───────────────────────────────────────────────

    def publish(self, event: str, **payload: Any) -> int:
        """同步派发，返回实际被调用的 handler 数量。

        重入（handler 里又 publish）是允许的，用深度计数避免死锁。
        """
        with self._lock:
            handlers = list(self._handlers.get(event, ()))
            self._publish_depth += 1
            try:
                self._remember(event, payload, len(handlers))
            finally:
                self._publish_depth -= 1

        for handler in handlers:
            try:
                handler(**payload)
            except Exception as e:  # noqa: BLE001 - 单个订阅者失败不得影响其他订阅者
                self.log.error(
                    "事件 %s 的订阅者 %r 抛出异常: %s",
                    event,
                    getattr(handler, "__qualname__", handler),
                    e,
                    exc_info=True,
                )
        return len(handlers)

    def _remember(self, event: str, payload: Dict[str, Any], count: int) -> None:
        if self._history_size <= 0:
            return
        self._history.append(EventRecord(event=event, payload=dict(payload), handler_count=count))
        if len(self._history) > self._history_size:
            del self._history[: len(self._history) - self._history_size]

    # ─── 诊断 ───────────────────────────────────────────────

    def handler_count(self, event: str) -> int:
        with self._lock:
            return len(self._handlers.get(event, ()))

    def events(self) -> List[str]:
        """当前有订阅者的事件名（已排序，便于测试断言）。"""
        with self._lock:
            return sorted(self._handlers)

    def history(self, limit: int = 20) -> List[EventRecord]:
        with self._lock:
            return self._history[-limit:]

    def __repr__(self) -> str:  # pragma: no cover
        with self._lock:
            total = sum(len(v) for v in self._handlers.values())
        return f"<EventBus events={len(self._handlers)} handlers={total}>"


__all__ = ["EventBus", "EventRecord", "Handler"]
