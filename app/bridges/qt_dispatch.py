"""主线程调度器 —— 把任意线程的回调排到 Qt 主线程执行。

## 为什么需要它

阶段 1 的 `TaskRunner` 与 `AppContext` 都接受一个"主线程调度器"（
`AppContext.set_scheduler` / `build_context(scheduler=...)`），旧 Tk 界面传的是
``lambda fn: app.after(0, fn)``。QML 版必须提供语义等价的替代品：

* **任意线程可调用**（`services/` 的 worker 会用它把结果送回主线程）；
* **一定排到主线程**，即使调用方本来就在主线程 —— 这与 `after(0, ...)` 一致
  （它不是"立即执行"，而是"排到当前事件循环之后再执行"）；
* 回调里抛异常**不能**掀翻事件循环。

## 实现要点

``_invoked`` 连接时**显式指定 `Qt.QueuedConnection`**，而不是靠 `AutoConnection`
的跨线程推断：同线程 emit 时 AutoConnection 会变成直接调用（同步执行），语义就
与 `after(0, ...)` 不一致了。显式 Queued 让"同线程也得排队"这件事变成确定的。

**禁止用 `QMetaObject.invokeMethod`**：阶段 0 实测它在 PySide6 6.7.3 上遇到参数类型
不匹配会**静默失败**（见 `docs/refactor/08-phase0-execution-log.md` 第 10.2 节）。
信号槽没有这个问题。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot

logger = logging.getLogger("app.bridges.qt_dispatch")


class MainThreadDispatcher(QObject):
    """把一个可调用对象排到**主线程**执行。

    典型用法（装配期建一个，然后交给 `AppContext`）::

        dispatcher = MainThreadDispatcher()
        ctx = build_context(config=config, ui=ui_port, scheduler=dispatcher.as_scheduler())

    之后 `services/` 里的任何线程调 `ctx.dispatch(fn)` 都会在主线程跑。
    """

    #: 载荷是"无参可调用对象"。用 object 而不是 QVariant 的子类型，避免 PySide6
    #: 在排队时尝试拷贝 Python 对象（实测可调用对象能安全跨线程排队）。
    _invoked = Signal(object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._owner_thread = QThread.currentThread()
        # 显式 QueuedConnection：见模块文档，同线程调用也必须排队。
        self._invoked.connect(self._run, Qt.QueuedConnection)
        self._pending_count = 0
        self._failure_count = 0

    # ─── 对外 ────────────────────────────────────────────────

    @property
    def is_owner_thread(self) -> bool:
        """当前线程是不是构造本对象的那条线程（正常即主线程）。"""
        try:
            return QThread.currentThread() is self._owner_thread
        except RuntimeError:
            # 底层 C++ 对象已销毁（退出路径上可能出现）
            return False

    def submit(self, fn: Callable[[], Any]) -> None:
        """把 ``fn`` 排到主线程。**任意线程可调用**；失败只记日志，不抛。"""
        self._pending_count += 1
        try:
            self._invoked.emit(fn)
        except RuntimeError as e:
            # 对象已销毁（退出中）：丢弃即可，不能打断调用方线程。
            self._pending_count -= 1
            logger.debug("调度器已销毁，丢弃一个回调: %s", e)

    def as_scheduler(self) -> Callable[[Callable[[], Any]], None]:
        """给 `AppContext.set_scheduler` / `build_context(scheduler=...)` 用的形式。"""
        return self.submit

    def describe(self) -> dict:
        return {
            "pending": self._pending_count,
            "failures": self._failure_count,
            "running": self._pending_count - self._failure_count,
        }

    # ─── 内部 ────────────────────────────────────────────────

    @Slot(object)
    def _run(self, fn: object) -> None:
        try:
            fn()  # type: ignore[operator]
        except Exception:  # noqa: BLE001 - 回调异常绝不能掀翻事件循环
            self._failure_count += 1
            logger.exception("主线程回调抛异常（已吞掉，事件循环继续）")


__all__ = ["MainThreadDispatcher"]
