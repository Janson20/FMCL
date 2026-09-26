"""QML 版任务调度桥（阶段 2 任务 2.4）。

## 它做了什么、**没有**做什么

`03-phases.md` 的 2.2 要求"`TaskRunner` 换成 Qt 实现（QThreadPool + 信号），并保留
『进度合并 + 定时刷新』语义"。这里的做法**不是**再写一个 `QtTaskRunner`，而是：

1. `app/tasks.py` 增加一个可替换的**执行基底**（`TaskRunner.set_executor`）；
   本模块提供一个 `QThreadPool` 适配器装进去。
2. 于是取消语义、回调契约（`on_done/on_error/on_progress/on_status`）、任务回收、
   `TaskHandle` 的 `wait/join/skipped` 全部**仍然只有一份实现** —— 这是刻意的，
   两套语义迟早会分叉（`app/bootstrap.py` 的注释里已经记过同一种教训）。

## 进度合并 + 定时刷新（对应风险 R-30 / 缺陷 D-61）

实测确认：`TaskContext.progress()` 是**在调用它的那条线程上**直接执行 `on_progress`
的（不经过调度器）。也就是说进度回调**跑在 worker 线程**里。因此：

* worker 里的 `on_progress` **只往一个加锁的字典里写"最新值"**，绝不碰 QObject 属性；
* 主线程上一个 100ms 的 `QTimer` 负责把字典里的内容**合并后一次性**发成 QML 信号。

这样即使服务侧每毫秒报一次进度，QML 也只会收到每 100ms 一次 —— 与旧 Tk 实现
"进度回调逐条入队、由轮询侧合并"的语义一致，但少了一层轮询。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QRunnable, QThreadPool, QTimer, Signal, Slot

from app.tasks import TaskRunner

logger = logging.getLogger("app.bridges.qt_tasks")

#: 进度刷新间隔（毫秒）。旧 Tk 实现的轮询周期在 40~100ms 量级；
#: 阶段 0 的富文本性能实测建议节流 80~120ms，这里取中间值。
DEFAULT_FLUSH_MS = 100


class _Runnable(QRunnable):
    """把一个无参可调用对象交给 QThreadPool。

    注意 `QRunnable.run()` 里**不能**让异常逃出去：PySide6 把它交给 C++ 之后，
    未捕获的 Python 异常会打断线程池的清理路径。真正的错误回报由
    `TaskRunner._execute` 的 try/except 负责，这里只兜底。
    """

    def __init__(self, fn: Callable[[], None]) -> None:
        super().__init__()
        self._fn = fn
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover - 正常路径不会走到 except
        try:
            self._fn()
        except Exception:  # noqa: BLE001
            logger.exception("QThreadPool 任务抛出未捕获异常")


class QtTaskExecutor:
    """`TaskRunner` 的执行基底：`QThreadPool`。"""

    def __init__(self, max_threads: int = 8) -> None:
        self._pool = QThreadPool()
        self._pool.setMaxThreadCount(max(1, int(max_threads)))
        self._submitted = 0

    def __call__(self, fn: Callable[[], None]) -> None:
        self._submitted += 1
        self._pool.start(_Runnable(fn))

    @property
    def submitted(self) -> int:
        return self._submitted

    @property
    def max_thread_count(self) -> int:
        return self._pool.maxThreadCount()

    def active_thread_count(self) -> int:
        return self._pool.activeThreadCount()

    def wait_for_done(self, msecs: int = 5000) -> bool:
        return self._pool.waitForDone(msecs)

    def shutdown(self) -> None:
        self._pool.clear()
        self._pool.waitForDone(2000)


class TaskBridge(QObject):
    """上下文属性 `Tasks` —— QML 侧查询/提交/取消后台任务的唯一入口。

    QML 用法（阶段 3 的页面会这样写）::

        Button { text: "…"; onClicked: Tasks.submit("install_version", {"version": v}) }
        ProgressBar { value: Tasks.progressFraction }
        Connections { target: Tasks; function onProgress(p) { ... } }
    """

    #: 合并后的进度（每 `flush_ms` 最多一条）。载荷键：
    #: ``taskId`` ``name`` ``current`` ``total`` ``message`` ``level`` ``title``
    #: ``determinate`` ``fraction``
    progress = Signal("QVariantMap")
    #: 合并后的状态文本（只有 message/level 变化）。载荷键：``taskId`` ``name`` ``message`` ``level``
    statusChanged = Signal("QVariantMap")
    #: 任务结束/失败。载荷键：``taskId`` ``name`` ``result`` / ``error`` ``cancelled``
    taskFinished = Signal("QVariantMap")
    taskFailed = Signal("QVariantMap")
    #: 活跃任务集合变化（增删）—— QML 的"后台任务指示"绑它
    activeChanged = Signal()

    def __init__(
        self,
        max_workers: int = 8,
        flush_ms: int = DEFAULT_FLUSH_MS,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._runner = TaskRunner(max_workers=max_workers, name="qml-tasks")
        self._executor = QtTaskExecutor(max_workers)
        self._runner.set_executor(self._executor)
        self._flush_ms = max(10, int(flush_ms))
        self._lock = threading.Lock()
        self._pending_progress: Dict[int, Dict[str, Any]] = {}
        self._pending_status: Dict[int, Dict[str, Any]] = {}
        self._active: Dict[int, Dict[str, Any]] = {}
        self._kinds: Dict[str, Callable[..., Any]] = {}
        self._flush_count = 0
        self._dropped = 0

        # 只在主线程启动的重复定时器：worker 永远不碰它。
        self._timer = QTimer(self)
        self._timer.setInterval(self._flush_ms)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    # ─── 装配 ────────────────────────────────────────────────

    def bind(self, context: Any) -> None:
        """接上 `AppContext`：**用它自己的 TaskRunner**，只把执行基底换成 QThreadPool。

        这样 `services/` 里通过 `ctx.tasks.submit(...)` 提交的任务自动走 Qt 线程池，
        全进程只有一份任务语义（迁移红线 2）。
        """
        if context is None:
            return
        runner = getattr(context, "tasks", None)
        if runner is None:
            logger.warning("上下文里没有 tasks，TaskBridge 继续用自建 TaskRunner")
            return
        self._runner = runner
        runner.set_executor(self._executor)
        scheduler = getattr(context, "scheduler", None)
        if scheduler is not None:
            runner.set_scheduler(scheduler)

    @property
    def runner(self) -> TaskRunner:
        """底层 `TaskRunner`（测试与阶段 3 的页面会用到）。"""
        return self._runner

    @property
    def executor(self) -> QtTaskExecutor:
        return self._executor

    # ─── 任务种类注册 ────────────────────────────────────────

    def register_kind(self, kind: str, fn: Callable[..., Any], pass_context: bool = False) -> None:
        """登记一种任务：QML 用 `Tasks.submit(kind, params)` 提交。

        Args:
            fn: 在工作线程上以 `fn(**params)` 调用。
            pass_context: 为 True 时把 `TaskContext` 作为**第一个位置参数**传入，
                任务函数据此报进度（`ctx.progress(...)`）与查取消（`ctx.cancelled`）。
                与 `TaskRunner.submit` 的同名参数语义一致。

        `fn` 里**不许写业务规则**：桥只做"名字 → 可调用对象"的映射（迁移红线 2）。
        """
        if not kind:
            raise ValueError("kind 不能为空")
        self._kinds[kind] = (fn, bool(pass_context))

    @Slot(result="QVariantList")
    def kinds(self) -> List[str]:
        return sorted(self._kinds)

    # ─── QML 只读属性 ───────────────────────────────────────

    @Property(int, notify=activeChanged)
    def activeCount(self) -> int:  # noqa: N802
        return len(self._active)

    @Property("QVariantList", notify=activeChanged)
    def activeTasks(self) -> List[Dict[str, Any]]:  # noqa: N802
        with self._lock:
            # 不能把 handle 透给 QML：它不是可序列化类型。
            return [{k: v for k, v in entry.items() if k != "handle"} for _, entry in sorted(self._active.items())]

    @Property(int, constant=True)
    def flushInterval(self) -> int:  # noqa: N802
        return self._flush_ms

    @Property(int, constant=True)
    def submittedCount(self) -> int:  # noqa: N802
        return self._executor.submitted

    @Property(int, notify=activeChanged)
    def droppedProgressCount(self) -> int:  # noqa: N802
        """被合并掉（未单独发出）的进度上报条数 —— 界面上看不到，但诊断时有用。"""
        return self._dropped

    # ─── QML 可调用 ─────────────────────────────────────────

    @Slot(str, "QVariantMap", result=int)
    def submit(self, kind: str, params: Optional[Dict[str, Any]] = None) -> int:
        """提交一个已登记种类的任务，返回 taskId；kind 未登记或提交失败返回 -1。"""
        fn = self._kinds.get(kind)
        if fn is None:
            logger.error("未登记的任务种类: %s（已登记: %s）", kind, sorted(self._kinds))
            return -1
        target, pass_context = fn

        handle = self._runner.submit(
            target,
            name=kind,
            pass_context=pass_context,
            on_progress=self._on_progress,
            on_status=self._on_status,
            **(params or {}),
        )
        with self._lock:
            # 存 handle 而不是只存 id：`TaskRunner` 的 `on_done/on_error` 回调**拿不到
            # taskId**（契约是 `on_done(result)`），所以结束事件由主线程定时收割
            # （见 `_reap_finished`）—— 那样就不用塞闭包、也不会有"回调早于 id 写入"的竞态。
            self._active[handle.id] = {"taskId": handle.id, "name": kind, "handle": handle}
        self.activeChanged.emit()
        return handle.id

    @Slot(int, result=bool)
    def cancel(self, task_id: int) -> bool:
        """请求取消。**是请求不是强杀** —— 任务需自己查 `ctx.cancelled` 才会退出。"""
        with self._lock:
            entry = self._active.get(int(task_id))
        if entry is None:
            return False
        entry["handle"].cancel()
        return True

    @Slot(int, result=bool)
    def cancelAll(self, timeout_ms: int = 0) -> bool:  # noqa: N802
        """取消全部活跃任务。返回是否在 `timeout_ms` 内全部结束。"""
        handles = list(self._runner.active)
        for h in handles:
            h.cancel()
        if timeout_ms <= 0:
            return True
        deadline = timeout_ms / 1000.0
        for h in handles:
            h.wait(deadline)
        return all(h.done for h in handles)

    @Slot(result="QVariantMap")
    def describe(self) -> Dict[str, Any]:
        return {
            "active": len(self._active),
            "kinds": sorted(self._kinds),
            "submitted": self._executor.submitted,
            "flushes": self._flush_count,
            "dropped_progress": self._dropped,
            "flush_ms": self._flush_ms,
            "has_scheduler": self._runner.has_scheduler,
        }

    # ─── 回调（可能跑在 worker 线程！） ──────────────────────

    def _on_progress(
        self,
        task_id: int,
        name: str,
        current: int,
        total: int,
        message: str = "",
        level: str = "info",
        title: Optional[str] = None,
    ) -> None:
        """**worker 线程**：只写加锁字典，绝不 emit、绝不碰 QObject 属性。"""
        with self._lock:
            if task_id in self._pending_progress:
                self._dropped += 1
            self._pending_progress[task_id] = {
                "taskId": task_id,
                "name": name,
                "current": int(current),
                "total": int(total),
                "message": message or "",
                "level": level or "info",
                "title": title or "",
            }

    def _on_status(self, task_id: int, name: str, message: str, level: str = "info") -> None:
        """**worker 线程**：同上。"""
        with self._lock:
            self._pending_status[task_id] = {
                "taskId": task_id,
                "name": name,
                "message": message or "",
                "level": level or "info",
            }

    def _on_done(self, task_id: int, name: str, result: Any) -> None:
        """（保留给"直接给定 id 的回调"用；主路径走 `_reap_finished`。）"""
        self._finish(task_id)
        self.taskFinished.emit({"taskId": task_id, "name": name, "result": result})

    # ─── 主线程 ─────────────────────────────────────────────

    def _reap_finished(self) -> List[Dict[str, Any]]:
        """收割已经结束的任务，返回要发的 `taskFinished/taskFailed` 载荷。

        为什么放在主线程定时器里而不是用 `on_done` 回调：`TaskRunner` 的
        `on_done(result)` / `on_error(exception)` **不带 taskId**，用它就得在闭包里
        回填 id，而"没有调度器时 `dispatch` 会内联执行"这条路径下回调可能早于 id 写入。
        主线程收割没有这个问题，代价是结束通知最多晚 `flush_ms`。
        """
        events: List[Dict[str, Any]] = []
        with self._lock:
            finished = [tid for tid, e in self._active.items() if e["handle"].done]
            for tid in finished:
                entry = self._active.pop(tid)
                self._pending_progress.pop(tid, None)
                self._pending_status.pop(tid, None)
                handle = entry["handle"]
                payload = {"taskId": tid, "name": entry["name"]}
                if isinstance(handle.error, BaseException):
                    payload["error"] = str(handle.error)
                    payload["errorType"] = type(handle.error).__name__
                    payload["cancelled"] = handle.cancelled
                    payload["ok"] = False
                else:
                    payload["result"] = handle.result
                    payload["cancelled"] = handle.cancelled
                    payload["ok"] = True
                events.append(payload)
        return events

    def _finish(self, task_id: int) -> None:
        with self._lock:
            existed = self._active.pop(task_id, None) is not None
            self._pending_progress.pop(task_id, None)
            self._pending_status.pop(task_id, None)
        if existed:
            self.activeChanged.emit()

    def _flush(self) -> None:
        """主线程定时器：把合并后的进度/状态一次发出去，然后收割结束的任务。

        **顺序很重要**：必须先取走并发出待发的进度，再收割。

        这是测试抓出来的真缺陷：任务如果在**同一个刷新周期内**报完进度就结束
        （很常见 —— 短任务、或者最后一条是 `total=total` 的收尾上报），
        先收割就会把 `_pending_progress` 里那条"最终值"一起丢掉，
        用户看到的进度条永远到不了 100%。实测就是
        `test_progress_payload_carries_fraction_and_indeterminate` 报红。
        """
        with self._lock:
            progress = list(self._pending_progress.values())
            self._pending_progress.clear()
            status = list(self._pending_status.values())
            self._pending_status.clear()
        self._flush_count += 1
        for item in status:
            self.statusChanged.emit(item)
        for payload in progress:
            total = payload.get("total") or 0
            current = payload.get("current") or 0
            payload["determinate"] = total > 0
            payload["fraction"] = (current / total) if total > 0 else 0.0
            self.progress.emit(payload)

        # 收割放在最后：结束事件一定排在"最终进度"之后。
        events = self._reap_finished()
        if events:
            self.activeChanged.emit()
            for payload in events:
                if payload["ok"]:
                    self.taskFinished.emit(payload)
                else:
                    self.taskFailed.emit(payload)

    def stop(self) -> None:
        """退出路径：停表 + 等线程池收干净。"""
        try:
            self._timer.stop()
        except RuntimeError:
            pass
        self._executor.shutdown()


__all__ = ["DEFAULT_FLUSH_MS", "QtTaskExecutor", "TaskBridge"]
