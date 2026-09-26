"""TaskRunner 单元测试：主线程回调、进度、取消、并发上限。

重点验证三件容易写错的事：
1. 回调确实经过 scheduler（而不是落在 worker 线程上）；
2. 取消是协作式的、已排队任务会被跳过、取消不吞掉其他任务的回调；
3. 并发数真的被 max_workers 限制住（这是"缩略图线程风暴"的修复依据）。
"""

import threading
import time

import pytest

from app.tasks import TaskRunner
from services.errors import OperationCancelled


def drain_until(predicate, queue, timeout=5.0):
    """把 scheduler 队列里的回调搬到当前线程执行，直到 predicate 为真。

    返回是否在超时前满足条件。因为 ``on_done`` 是在任务体结束**之后**
    才入队的，``handle.wait()`` 返回时回调可能还没进队列，所以测试必须
    靠 drain 而不是靠 wait 来同步。
    """
    deadline = time.monotonic() + timeout
    while True:
        while queue:
            queue.pop(0)()
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.005)


@pytest.fixture
def manual():
    """返回 (runner, queue)：回调不自动执行，由测试自己在主线程 drain。"""
    queue = []
    runner = TaskRunner(max_workers=2, scheduler=queue.append)
    yield runner, queue
    runner.shutdown(wait=True)


class TestBasicExecution:
    def test_result_reaches_on_done_via_scheduler(self, manual):
        runner, queue = manual
        got = []
        handle = runner.submit(lambda: 6 * 7, name="answer", on_done=got.append)
        assert handle.wait() is True
        assert drain_until(lambda: got, queue), "on_done 一直没被调度"
        assert got == [42]
        assert handle.result == 42
        assert handle.error is None

    def test_callback_runs_on_scheduler_thread_not_worker(self, manual):
        runner, queue = manual
        main_ident = threading.get_ident()
        seen = []
        runner.submit(
            lambda: threading.get_ident(),
            on_done=lambda value: seen.append((value, threading.get_ident())),
        )
        assert drain_until(lambda: seen, queue)
        worker_ident, callback_ident = seen[0]
        assert worker_ident != main_ident, "任务体应该跑在别的线程上"
        assert callback_ident == main_ident, "回调必须回到 scheduler 所在线程"

    def test_exception_reaches_on_error_with_traceback(self, manual):
        runner, queue = manual
        errors = []

        def boom():
            raise ValueError("炸了")

        handle = runner.submit(boom, on_error=errors.append)
        assert drain_until(lambda: errors, queue)
        assert isinstance(errors[0], ValueError)
        assert handle.error is errors[0]
        assert handle.result is None

    def test_join_reraises_original_exception(self):
        runner = TaskRunner(max_workers=1)
        try:
            handle = runner.submit(lambda: 1 / 0)
            with pytest.raises(ZeroDivisionError):
                handle.join(timeout=5)
        finally:
            runner.shutdown(wait=True)

    def test_join_times_out_on_long_task(self):
        runner = TaskRunner(max_workers=1)
        try:
            handle = runner.submit(lambda: time.sleep(1.0))
            with pytest.raises(TimeoutError):
                handle.join(timeout=0.05)
            assert handle.done is False
        finally:
            runner.shutdown(wait=True)

    def test_pass_context_prepends_argument(self):
        runner = TaskRunner(max_workers=1)
        try:
            seen = {}
            handle = runner.submit(
                lambda ctx: seen.update(name=ctx.name, id=ctx.id, has_runner=ctx._runner is runner),
                name="ctx-task",
                pass_context=True,
            )
            handle.join(timeout=5)
            assert seen == {"name": "ctx-task", "id": handle.id, "has_runner": True}
        finally:
            runner.shutdown(wait=True)

    def test_pass_handle_injects_handle_kwarg(self):
        runner = TaskRunner(max_workers=1)
        try:
            seen = []
            handle = runner.submit(lambda handle=None: seen.append(handle.id), pass_handle=True)
            handle.join(timeout=5)
            assert seen == [handle.id]
        finally:
            runner.shutdown(wait=True)

    def test_default_name_comes_from_function(self):
        runner = TaskRunner(max_workers=1)
        try:
            def my_task():
                return 1

            handle = runner.submit(my_task)
            assert handle.name == "my_task"
            handle.join(timeout=5)
        finally:
            runner.shutdown(wait=True)


class TestProgress:
    def test_progress_forwards_all_fields(self, manual):
        runner, queue = manual
        records = []
        runner.submit(
            lambda ctx: [ctx.progress(i, 4, f"第{i}步", "warning", "标题") for i in range(1, 3)],
            name="progressed",
            pass_context=True,
            on_progress=lambda **kw: records.append(kw),
        )
        assert drain_until(lambda: len(records) == 2, queue)
        first = records[0]
        assert first["current"] == 1
        assert first["total"] == 4
        assert first["message"] == "第1步"
        assert first["level"] == "warning"
        assert first["title"] == "标题"
        assert first["name"] == "progressed"
        assert isinstance(first["task_id"], int)

    def test_status_forwards_without_numbers(self, manual):
        runner, queue = manual
        records = []
        runner.submit(
            lambda ctx: ctx.status("正在连接", "warning"),
            pass_context=True,
            on_status=lambda **kw: records.append(kw),
        )
        assert drain_until(lambda: records, queue)
        assert records[0]["message"] == "正在连接"
        assert records[0]["level"] == "warning"

    def test_progress_without_handler_is_silent(self):
        runner = TaskRunner(max_workers=1)
        try:
            runner.submit(lambda ctx: ctx.progress(1, 2), pass_context=True).join(timeout=5)
        finally:
            runner.shutdown(wait=True)

    def test_broken_callback_does_not_kill_worker(self, manual, caplog):
        runner, queue = manual
        gate = []

        def bad_progress(**kw):
            raise RuntimeError("回调坏了")

        first = runner.submit(
            lambda ctx: ctx.progress(1, 2), pass_context=True, on_progress=bad_progress
        )
        second = runner.submit(lambda: "still-alive", on_done=gate.append)
        assert drain_until(lambda: gate, queue)
        assert gate == ["still-alive"]
        assert first.done and second.done
        assert second.error is None


class TestCancellation:
    def test_cancel_before_start_skips_the_body(self):
        runner = TaskRunner(max_workers=1)
        try:
            ran = []
            blocker = runner.submit(lambda: time.sleep(0.4))
            queued = runner.submit(lambda: ran.append(1))
            queued.cancel()
            assert blocker.wait(timeout=5)
            assert queued.wait(timeout=5)
            assert queued.skipped is True
            assert ran == [], "已取消的排队任务绝不能被执行"
            assert queued.error is None, "主动取消不算失败"
        finally:
            runner.shutdown(wait=True)

    def test_cancel_during_run_raises_operation_cancelled(self, manual):
        runner, queue = manual
        errors = []
        started = threading.Event()

        def long_task(ctx):
            started.set()
            for _ in range(200):
                ctx.raise_if_cancelled()
                time.sleep(0.01)
            return "不该走到这里"

        handle = runner.submit(long_task, pass_context=True, on_error=errors.append)
        assert started.wait(timeout=5)
        handle.cancel()
        assert handle.wait(timeout=5)
        assert drain_until(lambda: errors, queue)
        assert isinstance(errors[0], OperationCancelled)
        assert handle.result is None

    def test_cancellable_sleep_returns_false_when_cancelled(self):
        runner = TaskRunner(max_workers=1)
        try:
            results = []
            elapsed = []

            def task(ctx):
                # 直接置位取消事件，避免依赖"handle 赋值"与 worker 启动之间的竞态
                ctx.cancel_event.set()
                started = time.monotonic()
                results.append(ctx.sleep(5.0))
                elapsed.append(time.monotonic() - started)

            runner.submit(task, pass_context=True).join(timeout=5)
            assert results == [False], "sleep 必须在取消后立刻返回"
            assert elapsed[0] < 1.0, f"sleep 实际等了 {elapsed[0]:.2f}s，没有被打断"
        finally:
            runner.shutdown(wait=True)

    def test_cancel_is_idempotent(self):
        runner = TaskRunner(max_workers=1)
        try:
            handle = runner.submit(lambda: time.sleep(0.2))
            handle.cancel()
            handle.cancel()
            assert handle.cancelled is True
            handle.wait(timeout=5)
        finally:
            runner.shutdown(wait=True)


class TestDispatch:
    def test_dispatch_without_scheduler_runs_inline(self):
        runner = TaskRunner(max_workers=1)
        try:
            inline = []
            assert runner.dispatch(lambda: inline.append(1)) is True
            assert inline == [1]
        finally:
            runner.shutdown(wait=True)

    def test_dispatch_with_scheduler_queues_instead_of_running(self):
        queue = []
        runner = TaskRunner(max_workers=1, scheduler=queue.append)
        try:
            ran = []
            assert runner.dispatch(lambda: ran.append(1)) is True
            assert ran == [], "有 scheduler 时不允许内联执行"
            queue.pop(0)()
            assert ran == [1]
        finally:
            runner.shutdown(wait=True)

    def test_broken_scheduler_falls_back_to_inline(self, caplog):
        def broken_scheduler(fn):
            raise RuntimeError("界面已经销毁")

        runner = TaskRunner(max_workers=1, scheduler=broken_scheduler)
        try:
            ran = []
            assert runner.dispatch(lambda: ran.append(1)) is False
            assert ran == [1], "调度失败时必须内联兜底，不能丢事件"
        finally:
            runner.shutdown(wait=True)

    def test_context_dispatch_is_available_to_tasks(self, manual):
        runner, queue = manual
        seen = []
        runner.submit(
            lambda ctx: ctx.dispatch(lambda: seen.append("marshalled")),
            pass_context=True,
        )
        assert drain_until(lambda: seen, queue)
        assert seen == ["marshalled"]


class TestConcurrencyAndLifecycle:
    def test_concurrency_is_capped_by_max_workers(self):
        runner = TaskRunner(max_workers=2)
        lock = threading.Lock()
        state = {"active": 0, "peak": 0}

        def task():
            with lock:
                state["active"] += 1
                state["peak"] = max(state["peak"], state["active"])
            time.sleep(0.3)
            with lock:
                state["active"] -= 1

        try:
            handles = [runner.submit(task) for _ in range(6)]
            assert runner.wait_all(timeout=10)
            assert state["peak"] == 2, f"并发上限失效，峰值 {state['peak']}"
            assert all(h.done for h in handles)
        finally:
            runner.shutdown(wait=True)

    def test_worker_threads_are_daemon(self):
        # 用独立线程名前缀，避免被其他用例尚未退出的 worker 干扰计数
        runner = TaskRunner(max_workers=3, name="daemonprobe")
        try:
            runner.submit(lambda: None).join(timeout=5)
            workers = [t for t in threading.enumerate() if t.name.startswith("daemonprobe-")]
            assert len(workers) == 3
            assert all(t.daemon for t in workers), "非 daemon 线程会让启动器退不掉"
        finally:
            runner.shutdown(wait=True)

    def test_active_count_tracks_running_tasks(self):
        runner = TaskRunner(max_workers=1)
        try:
            handle = runner.submit(lambda: time.sleep(0.2))
            assert runner.active_count() == 1
            handle.join(timeout=5)
            deadline = time.monotonic() + 2
            while runner.active_count() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert runner.active_count() == 0
        finally:
            runner.shutdown(wait=True)

    def test_shutdown_rejects_new_submissions(self):
        runner = TaskRunner(max_workers=1)
        runner.shutdown(wait=True)
        with pytest.raises(RuntimeError):
            runner.submit(lambda: None)

    def test_shutdown_cancels_pending_by_default(self):
        runner = TaskRunner(max_workers=1)
        ran = []
        runner.submit(lambda: time.sleep(0.3))
        queued = runner.submit(lambda: ran.append(1))
        runner.shutdown(wait=True)
        assert queued.done
        assert ran == []

    def test_max_workers_must_be_positive(self):
        with pytest.raises(ValueError):
            TaskRunner(max_workers=0)

    def test_submitting_many_tasks_reuses_worker_threads(self):
        runner = TaskRunner(max_workers=4, name="reuseprobe")
        try:
            for _ in range(50):
                runner.submit(lambda: None)
            assert runner.wait_all(timeout=10)
            workers = [t for t in threading.enumerate() if t.name.startswith("reuseprobe-")]
            assert len(workers) == 4, "线程数不应该随任务数增长"
        finally:
            runner.shutdown(wait=True)
