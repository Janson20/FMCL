"""阶段 2 任务 2.4（Qt 版任务调度）的回归守卫。

## 这一组测试的重点

`TaskRunner` 换执行基底这件事，最容易的错法是"再写一个 Qt 版 runner"——
那样取消语义、回调契约、进度上报就会长出第二份实现，迟早分叉。
所以这里**不测"有没有 QThreadPool"**，而是测"语义有没有变"：

1. 用 Qt 基底跑的任务，`TaskHandle.done/result/error/cancelled/wait/join` 与内建基底一致；
2. 进度**合并 + 定时刷新**真的生效（同一任务报 100 次进度，QML 只收到远少于 100 条）；
3. 进度回调**跑在 worker 线程**，因此它绝不能碰 QObject —— 这条用"只写加锁字典"的设计保证，
   并由测试断言 `progress` 信号只在主线程发出；
4. 取消是**协作式**的（请求而非强杀），任务不查 `ctx.cancelled` 就不会停；
5. `bind(context)` 之后用的是**上下文自己的** `TaskRunner`（全进程一份任务语义）。
"""

from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402


def _app():
    """复用进程里已有的 QGuiApplication（一个进程只能有一个）。"""
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


def _pump(ms: int = 400, until=None):
    """泵事件循环直到 `until()` 为真或超时。"""
    app = _app()
    deadline = time.time() + ms / 1000.0
    while time.time() < deadline:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.01)
    return until() if until is not None else True


@pytest.fixture
def bridge():
    # **必须先有 QGuiApplication**：`TaskBridge` 在 __init__ 里就建 `QTimer` 并 start()，
    # 没有 app 时定时器起不来（而且不报错），后面所有"等定时器刷新"的断言都会超时。
    _app()
    from app.bridges.qt_tasks import TaskBridge

    b = TaskBridge(max_workers=4, flush_ms=20)
    yield b
    b.stop()


# ─── 1. 执行基底确实换成了 Qt ──────────────────────────────────


def test_executor_is_installed_on_the_runner(bridge):
    from app.bridges.qt_tasks import QtTaskExecutor

    assert isinstance(bridge.runner._executor, QtTaskExecutor), (
        "TaskBridge 没有把 QtTaskExecutor 装进 TaskRunner —— 任务还在走内建线程池"
    )
    assert bridge.runner._executor.max_thread_count == 4


def test_task_runs_on_a_qt_pool_thread_not_a_python_thread(bridge):
    """任务函数里 `QThread.currentThread()` 必须是 Qt 的池线程，而不是裸 Python 线程。

    判据取"当前不是主线程" + "池里有活跃线程"，因为 `QThreadPool` 的线程
    在 Python 侧也会包成 QThread 对象，但绝不会是主线程。
    """
    seen = {}
    done = threading.Event()

    def job():
        seen["in_main"] = QThread.currentThread() is QGuiApplication.instance().thread()
        seen["has_thread"] = QThread.currentThread() is not None
        done.set()
        return "ok"

    bridge.register_kind("probe", job)
    tid = bridge.submit("probe", {})
    assert tid > 0, "submit 返回了无效 id"
    assert done.wait(5), "任务没跑起来"
    assert seen["in_main"] is False, "任务跑在主线程上 —— 那等于没有后台执行"
    assert seen["has_thread"] is True
    _pump(500, lambda: bridge.activeCount == 0)
    assert bridge.activeCount == 0


# ─── 2. 语义与内建基底一致 ─────────────────────────────────────


def _run_and_wait(bridge, kind, fn, timeout=5.0, **params):
    bridge.register_kind(kind, fn)
    tid = bridge.submit(kind, params)
    assert tid > 0
    handle = _handle_of(bridge, tid)
    assert handle is not None, "提交后活动表里没有这条任务"
    assert handle.wait(timeout), f"任务 {kind} 在 {timeout}s 内没结束"
    return handle


def _handle_of(bridge, tid):
    with bridge._lock:  # noqa: SLF001 - 测试要看底层句柄，这是刻意的白盒
        entry = bridge._active.get(tid)
    return entry["handle"] if entry else None


def test_handle_semantics_match_the_builtin_runner(bridge):
    handle = _run_and_wait(bridge, "ok", lambda: 42)
    assert handle.done is True
    assert handle.result == 42
    assert handle.error is None
    assert handle.cancelled is False
    assert handle.join(1) == 42


def test_failure_is_reported_with_the_original_exception(bridge):
    def boom():
        raise ValueError("故意失败")

    handle = _run_and_wait(bridge, "boom", boom)
    assert handle.done is True
    assert isinstance(handle.error, ValueError)
    assert "故意失败" in str(handle.error)


def test_task_finished_event_reaches_the_main_thread(bridge):
    """结束事件由主线程收割后发出 —— 记录线程，必须全是主线程。"""
    threads = []
    bridge.taskFinished.connect(lambda payload: threads.append(threading.get_ident()))
    bridge.register_kind("ok2", lambda: 1)
    bridge.submit("ok2", {})
    _pump(3000, lambda: bool(threads))
    assert threads, "没有收到 taskFinished"
    assert set(threads) == {threading.get_ident()}, "taskFinished 不在主线程发出"


# ─── 3. 进度合并 + 定时刷新（风险 R-30 / 缺陷 D-61） ────────────


def test_progress_is_merged_not_streamed(bridge):
    """同一任务报 200 次进度，QML 侧收到的条数必须**远少于** 200。

    这条直接对应红线"禁止逐条直连"：旧 Tk 实现刻意做了合并 + 定时刷新，
    直接绑定属性会在批量下载时把界面卡死。
    """
    received = []
    bridge.progress.connect(lambda payload: received.append(payload))

    def job(ctx):
        for i in range(200):
            ctx.progress(i + 1, 200, f"step {i}")
            time.sleep(0.001)

    bridge.register_kind("stream", job, pass_context=True)
    bridge.submit("stream", {})
    # 一直泵到任务结束（而不是"收到第一条就停"）：合并的语义是"保留最新值"，
    # 只泵到第一条就断言末值等于 200 会误判成缺陷。
    _pump(6000, lambda: bridge.activeCount == 0 and len(received) > 0)
    _pump(200)

    assert received, "一条进度都没收到"
    assert len(received) < 200, f"进度没有被合并：收到 {len(received)} 条"
    # 最后一条必须是最终值（合并保留"最新值"，不是随便抽一条）
    assert received[-1]["current"] == 200
    assert received[-1]["determinate"] is True
    assert received[-1]["fraction"] == pytest.approx(1.0)
    assert bridge.droppedProgressCount > 0, "丢弃计数为 0，说明没有发生合并"


def test_progress_payload_carries_fraction_and_indeterminate(bridge):
    received = []
    bridge.progress.connect(lambda payload: received.append(payload))

    def job(ctx):
        ctx.progress(3, 0, "working")  # total=0 => 不确定进度

    bridge.register_kind("indet", job, pass_context=True)
    bridge.submit("indet", {})
    _pump(3000, lambda: bool(received))
    assert received
    payload = received[-1]
    assert payload["determinate"] is False
    assert payload["fraction"] == 0.0
    assert payload["message"] == "working"


def test_progress_callback_runs_on_a_worker_thread(bridge):
    """进度回调**本来就在 worker 线程**上执行 —— 这是设计前提，必须钉住。

    因为它是前提，桥里才必须做到"worker 只写加锁字典、绝不 emit"。
    如果哪天 `TaskRunner` 改成在 worker 里调度进度，这条会红，提醒重新审视设计。
    """
    idents = []

    def job(ctx):
        ctx.progress(1, 10, "x")
        with bridge._lock:  # noqa: SLF001
            idents.append(threading.get_ident())

    bridge.register_kind("which-thread", job, pass_context=True)
    bridge.submit("which-thread", {})
    _pump(3000, lambda: bool(idents))
    assert idents and idents[0] != threading.get_ident(), "进度回调竟然在主线程 —— 前提变了"


# ─── 4. 取消是协作式的 ─────────────────────────────────────────


def test_cancel_is_cooperative(bridge):
    """发了取消请求之后，**不查 `ctx.cancelled` 的任务照样跑完**（与旧实现一致）。"""
    started = threading.Event()

    def stubborn(ctx):
        started.set()
        time.sleep(0.3)
        return "finished-anyway"

    bridge.register_kind("stubborn", stubborn, pass_context=True)
    tid = bridge.submit("stubborn", {})
    assert started.wait(5)
    assert bridge.cancel(tid) is True
    handle = _handle_of(bridge, tid)
    assert handle is not None
    assert handle.wait(5)
    assert handle.result == "finished-anyway", "取消把任务强杀了 —— 语义与旧实现不一致"
    assert handle.cancelled is True


def test_cooperative_task_stops_on_cancel(bridge):
    def cooperative(ctx):
        for _ in range(300):
            ctx.raise_if_cancelled()
            time.sleep(0.01)
        return "not-cancelled"

    bridge.register_kind("coop", cooperative, pass_context=True)
    tid = bridge.submit("coop", {})
    time.sleep(0.1)
    assert bridge.cancel(tid) is True
    handle = _handle_of(bridge, tid)
    assert handle.wait(5)
    assert handle.cancelled is True
    assert handle.result is None
    assert handle.error is not None, "协作式取消应当以 OperationCancelled 结束"


def test_cancel_unknown_task_returns_false(bridge):
    assert bridge.cancel(999999) is False


# ─── 5. 与 AppContext 的接线 ───────────────────────────────────


def test_bind_reuses_the_context_runner():
    """`bind(context)` 必须用**上下文自己的** TaskRunner —— 全进程一份任务语义。"""
    from app.bridges.qt_tasks import TaskBridge
    from app.bootstrap import build_context

    b = TaskBridge(flush_ms=20)
    try:
        ctx = build_context(register=False, set_current=False)
        ctx_runner = ctx.tasks
        b.bind(ctx)
        assert b.runner is ctx_runner, "桥自建了 runner，服务与 QML 会各跑一套任务语义"
        assert ctx.tasks._executor is b.executor
    finally:
        b.stop()


def test_unregistered_kind_is_rejected(bridge):
    assert bridge.submit("not-registered", {}) == -1
    assert bridge.kinds() == []


def test_kinds_and_describe(bridge):
    bridge.register_kind("b", lambda: None)
    bridge.register_kind("a", lambda: None)
    assert bridge.kinds() == ["a", "b"]
    info = bridge.describe()
    assert info["kinds"] == ["a", "b"]
    assert info["flush_ms"] == 20
