"""后台任务调度器（阶段 1 的 threading 实现）。

阶段 2 会换成 QThreadPool + 信号；**接口保持不变**，这样阶段 1 搬进
services 的业务代码在阶段 2 不需要再改一遍。

三个设计决定，都是为了修掉现有代码里的真实缺陷：

1. **自己管 daemon 线程池，不用 ThreadPoolExecutor**。
   ``ThreadPoolExecutor`` 的工作线程是非 daemon 的，解释器退出时会 join，
   一个卡住的长下载会让整个启动器退不掉。同时并发数有上限，
   直接消灭"缩略图线程风暴"（阶段 1.22）。
2. **取消是协作式的**。``cancel()`` 只置位 ``cancel_event``，任务自己
   在安全点检查 ``ctx.cancelled`` 并尽快返回。已排队未开跑的任务会被跳过。
3. **回调统一切回主线程**。``dispatch`` 通过注入的 scheduler 完成；
   没有 scheduler 时（CLI / pytest）直接内联执行。
   scheduler 必须保证 **FIFO**，否则同一次任务的 progress/on_done 顺序会乱。
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional

from services.errors import OperationCancelled

logger = logging.getLogger(__name__)

TaskFn = Callable[..., Any]
Dispatcher = Callable[[Callable[[], None]], None]

_ID_SEQ = itertools.count(1)


class TaskContext:
    """传给任务函数的句柄：报进度、查取消、切主线程。"""

    def __init__(
        self,
        task_id: int,
        name: str,
        cancel_event: threading.Event,
        runner: "TaskRunner",
        on_progress: Optional[Callable[..., None]] = None,
        on_status: Optional[Callable[..., None]] = None,
    ) -> None:
        self.id = task_id
        self.name = name
        self.cancel_event = cancel_event
        self._runner = runner
        self._on_progress = on_progress
        self._on_status = on_status
        self.log = logging.getLogger(f"task.{name}")
        self._last_fraction = -1.0

    # ─── 取消 ───────────────────────────────────────────────

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise OperationCancelled(f"任务「{self.name}」已取消")

    def sleep(self, seconds: float) -> bool:
        """可被取消打断的 sleep。返回 False 表示期间被取消。"""
        return not self.cancel_event.wait(seconds)

    # ─── 上报 ───────────────────────────────────────────────

    def progress(
        self,
        current: int,
        total: int = 0,
        message: str = "",
        level: str = "info",
        title: Optional[str] = None,
    ) -> None:
        """上报进度。``total <= 0`` 表示不确定进度。"""
        handler = self._on_progress
        if handler is None:
            return
        fraction = (current / total) if total > 0 else -1.0
        self._last_fraction = fraction
        self._runner._safe_call(
            self._on_progress,
            task_id=self.id,
            name=self.name,
            current=current,
            total=total,
            message=message,
            level=level,
            title=title,
        )

    def status(self, message: str, level: str = "info") -> None:
        """只更新文字状态，不带数值进度。"""
        if self._on_status is None:
            return
        self._runner._safe_call(self._on_status, task_id=self.id, name=self.name, message=message, level=level)

    # ─── 线程 ───────────────────────────────────────────────

    def dispatch(self, fn: Callable[[], None]) -> bool:
        """把一个回调切回 UI 主线程执行。返回是否成功入队/执行。"""
        return self._runner.dispatch(fn)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaskContext #{self.id} {self.name!r} cancelled={self.cancelled}>"


class TaskHandle:
    """提交后返回的句柄：取消、查询、同步等待。"""

    def __init__(self, task_id: int, name: str, context: TaskContext) -> None:
        self.id = task_id
        self.name = name
        self.context = context
        self._done = threading.Event()
        self._result: Any = None
        self._error: Optional[BaseException] = None
        self._skipped = False

    @property
    def done(self) -> bool:
        return self._done.is_set()

    @property
    def cancelled(self) -> bool:
        return self.context.cancelled

    @property
    def skipped(self) -> bool:
        """已排队但开跑前就被取消，函数从未执行。"""
        return self._skipped

    @property
    def result(self) -> Any:
        return self._result

    @property
    def error(self) -> Optional[BaseException]:
        return self._error

    def cancel(self) -> None:
        self.context.cancel_event.set()

    def wait(self, timeout: Optional[float] = None) -> bool:
        """等待任务结束。返回 True 表示已结束（成功、失败或跳过）。"""
        return self._done.wait(timeout)

    def join(self, timeout: Optional[float] = None) -> Any:
        """等待并返回结果；任务失败则抛出原异常。"""
        if not self._done.wait(timeout):
            raise TimeoutError(f"任务「{self.name}」在 {timeout}s 内未结束")
        if isinstance(self._error, BaseException):
            raise self._error
        return self._result

    def _finish(self, result: Any, error: Optional[BaseException], skipped: bool = False) -> None:
        self._result = result
        self._error = error
        self._skipped = skipped
        self._done.set()

    def __repr__(self) -> str:  # pragma: no cover
        state = "done" if self.done else ("cancelled" if self.cancelled else "running")
        return f"<TaskHandle #{self.id} {self.name!r} {state}>"


class TaskRunner:
    """有界并发 + 主线程回调的任务调度器。"""

    def __init__(
        self,
        max_workers: int = 8,
        scheduler: Optional[Dispatcher] = None,
        name: str = "tasks",
        logger_: Optional[logging.Logger] = None,
    ) -> None:
        if max_workers < 1:
            raise ValueError("max_workers 必须 >= 1")
        self.max_workers = max_workers
        self.name = name
        self.log = logger_ or logging.getLogger("app.tasks")
        self._scheduler = scheduler
        self._executor: Optional[Callable[[Callable[[], None]], None]] = None
        self._queue: "Queue[Optional[tuple]]" = Queue()
        self._workers: List[threading.Thread] = []
        self._lock = threading.RLock()
        self._running: Dict[int, TaskHandle] = {}
        self._shutdown = False

    # ─── 调度器（主线程切换） ───────────────────────────────

    def set_scheduler(self, scheduler: Optional[Dispatcher]) -> None:
        """注入主线程调度器（Tk: ``widget.after`` 包装；Qt: 信号/事件队列）。"""
        self._scheduler = scheduler

    @property
    def scheduler(self) -> Optional[Dispatcher]:
        """当前的主线程调度器（阶段 2 任务 2.4：Qt 侧的桥要用它把回调排回主线程）。"""
        return self._scheduler

    def set_executor(self, executor: Optional[Callable[[Callable[[], None]], None]]) -> None:
        """替换**执行基底**（阶段 2 任务 2.4）。

        默认基底是内建线程池（`_queue` + `_worker_loop`，旧 Tk 界面用）。QML 侧传一个
        `QThreadPool` 适配器进来，就完成了"`TaskRunner` 换成 Qt 实现"这件事 ——
        **而取消语义、回调契约、进度上报、任务回收全部只有一份实现**。

        为什么不做成"另一个 QtTaskRunner 类"：那会立刻长出第二套语义（`app/bootstrap.py`
        的注释里已经记过同一种教训）。把基底做成一个可替换的接缝，代价是一个函数指针，
        收益是行为不会分叉。

        Args:
            executor: ``fn -> None``，负责在**别的线程**上跑 ``fn``。传 None 恢复内建线程池。
        """
        self._executor = executor

    @property
    def has_scheduler(self) -> bool:
        return self._scheduler is not None

    def dispatch(self, fn: Callable[[], None]) -> bool:
        """把 ``fn`` 排到 UI 主线程。无 scheduler 时内联执行。"""
        if self._scheduler is None:
            self._safe_call(fn)
            return True
        try:
            self._scheduler(fn)
            return True
        except Exception as e:  # noqa: BLE001 - 界面已销毁时不能连累后台任务
            self.log.warning("主线程调度失败，改为内联执行: %s", e)
            self._safe_call(fn)
            return False

    def _safe_call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        try:
            fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - 回调异常不得杀死 worker 线程
            self.log.error("任务回调 %r 抛出异常: %s", getattr(fn, "__qualname__", fn), e, exc_info=True)

    # ─── 提交 ───────────────────────────────────────────────

    def submit(
        self,
        fn: TaskFn,
        *args: Any,
        name: Optional[str] = None,
        on_done: Optional[Callable[..., None]] = None,
        on_error: Optional[Callable[..., None]] = None,
        on_progress: Optional[Callable[..., None]] = None,
        on_status: Optional[Callable[..., None]] = None,
        pass_context: bool = False,
        pass_handle: bool = False,
        **kwargs: Any,
    ) -> TaskHandle:
        """提交一个后台任务。

        Args:
            pass_context: 为 True 时把 ``TaskContext`` 作为第一个位置参数传入，
                任务函数据此报进度/查取消。
            pass_handle: 为 True 时把 ``TaskHandle`` 作为末位关键字参数
                ``handle=`` 传入（注意：构造 TaskHandle 与传给任务之间存在
                极短的竞态，只应在需要"自取消"等特殊场景使用）。

        回调契约（全部在 UI 主线程执行）：
            ``on_done(result)`` / ``on_error(exception)``
            ``on_progress(task_id, name, current, total, message, level, title)``
            ``on_status(task_id, name, message, level)``
        """
        if self._shutdown:
            raise RuntimeError("TaskRunner 已关闭，无法提交新任务")
        with self._lock:
            task_id = next(_ID_SEQ)
            task_name = name or getattr(fn, "__name__", "task")
            cancel_event = threading.Event()
            handle_box: List[TaskHandle] = []
            ctx = TaskContext(task_id, task_name, cancel_event, self, on_progress, on_status)
            handle = TaskHandle(task_id, task_name, ctx)
            handle_box.append(handle)
            self._running[task_id] = handle
            if self._executor is None:
                # 只有走内建线程池时才需要保证 worker 存在；Qt 路径由 QThreadPool 管线程。
                self._ensure_workers()

        def _runner() -> None:
            self._execute(handle, ctx, fn, args, kwargs, on_done, on_error, pass_context, pass_handle)

        if self._executor is not None:
            # Qt 路径：交给 QThreadPool。提交本身失败（池已关闭之类）也要把任务收干净，
            # 否则它会一直挂在 `_running` 里，界面上表现为"永远有一个后台任务在跑"。
            try:
                self._executor(_runner)
            except Exception as e:  # noqa: BLE001
                handle._finish(None, e)
                self._reap(handle)
                self.log.error("任务「%s」提交到执行基底失败: %s", handle.name, e)
                if on_error is not None:
                    self.dispatch(lambda err=e: self._safe_call(on_error, err))
        else:
            self._queue.put((handle, _runner))
        return handle

    # ─── 执行 ───────────────────────────────────────────────

    def _execute(
        self,
        handle: TaskHandle,
        ctx: TaskContext,
        fn: TaskFn,
        args: tuple,
        kwargs: Dict[str, Any],
        on_done: Optional[Callable[..., None]],
        on_error: Optional[Callable[..., None]],
        pass_context: bool,
        pass_handle: bool,
    ) -> None:
        if ctx.cancelled:
            handle._finish(None, None, skipped=True)
            self._reap(handle)
            return
        call_args = (ctx, *args) if pass_context else args
        call_kwargs = dict(kwargs)
        if pass_handle:
            call_kwargs["handle"] = handle
        try:
            result = fn(*call_args, **call_kwargs)
        except OperationCancelled as e:
            handle._finish(None, e)
            self._reap(handle)
            self.log.info("任务「%s」已取消", handle.name)
            if on_error is not None:
                # 必须用默认参数把 e 绑进闭包：Python 会在 except 块结束时
                # 隐式 `del e`，延迟执行的 lambda 直接引用 e 会 NameError。
                self.dispatch(lambda err=e: self._safe_call(on_error, err))
            return
        except BaseException as e:  # noqa: BLE001 - 必须把失败回报给 UI，不能让线程静默死掉
            handle._finish(None, e)
            self._reap(handle)
            self.log.error("任务「%s」失败: %s", handle.name, e, exc_info=True)
            if on_error is not None:
                self.dispatch(lambda err=e: self._safe_call(on_error, err))
            return
        handle._finish(result, None)
        self._reap(handle)
        if on_done is not None:
            self.dispatch(lambda res=result: self._safe_call(on_done, res))

    def _reap(self, handle: TaskHandle) -> None:
        with self._lock:
            self._running.pop(handle.id, None)

    # ─── 线程池 ─────────────────────────────────────────────

    def _ensure_workers(self) -> None:
        with self._lock:
            alive = [w for w in self._workers if w.is_alive()]
            self._workers = alive
            while len(self._workers) < self.max_workers:
                idx = len(self._workers)
                worker = threading.Thread(
                    target=self._worker_loop,
                    name=f"{self.name}-{idx}",
                    daemon=True,
                )
                self._workers.append(worker)
                worker.start()

    def _worker_loop(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=0.5)
            except Empty:
                if self._shutdown:
                    return
                continue
            if item is None:  # 关闭哨兵
                self._queue.task_done()
                return
            handle, job = item
            try:
                # 注意：这里**不能**再判一次 handle.context.cancelled 就跳过 job()。
                # 跳过 job() 会让 _execute 里的完成记账（_finish/_reap/on_error）
                # 全部丢失，handle 永远不 done，wait() 挂死、active 计数泄漏。
                # 取消判定统一放在 _execute 内部处理。
                job()
            finally:
                self._queue.task_done()

    # ─── 查询与关闭 ─────────────────────────────────────────

    def active(self) -> List[TaskHandle]:
        with self._lock:
            return list(self._running.values())

    def active_count(self) -> int:
        with self._lock:
            return len(self._running)

    def wait_all(self, timeout: Optional[float] = None) -> bool:
        """等待所有已提交任务结束。返回是否全部结束。"""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            with self._lock:
                handles = list(self._running.values())
            pending = [h for h in handles if not h.done]
            if not pending:
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            pending[0].wait(0.05)

    def shutdown(self, wait: bool = False, cancel_pending: bool = True) -> None:
        """停止接受新任务。``wait=False`` 不阻塞退出（worker 是 daemon）。"""
        if cancel_pending:
            with self._lock:
                handles = list(self._running.values())
            for h in handles:
                h.cancel()
        if wait:
            self.wait_all(timeout=10.0)
        self._shutdown = True
        with self._lock:
            worker_count = len(self._workers)
        for _ in range(worker_count):
            self._queue.put(None)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaskRunner workers={self.max_workers} active={self.active_count()} shutdown={self._shutdown}>"


__all__ = ["TaskRunner", "TaskContext", "TaskHandle"]
