"""阶段 1.22 / 1.23 线程与生命周期缺陷的永久回归守卫。

覆盖三处已修缺陷，每处都曾经是真实缺陷（不是假想）：

- **D-84** ``_watch_server_exit`` 有三条路径不投递 ``server_exit``，而那是
  **唯一**会恢复"启动/停止"按钮与内存定时器的消息 → 界面永久卡在"运行中"。
- **D-85** ``_append_server_log`` 无条件 ``after(0, ...)``，导致崩溃排查时
  模态对话框抢在最后几行日志之前弹出。
- **D-92** 缩略图"每个 zip 起一个线程"，且搜索框每敲一个字符就重渲染列表、
  再起一批线程（无去重、无代际校验）。
"""

from __future__ import annotations

import queue
import threading
import time

import pytest

from app.tasks import TaskRunner
from ui.app_server import ServerTabMixin
from ui.windows.resource_manager import ResourceManagerWindow as RM


# ─── D-84：server_exit 必须恰好投递一次 ─────────────────────


class FakeStdout:
    def __init__(self, lines, raise_after=None):
        self._lines = list(lines)
        self._raise_after = raise_after

    def readline(self):
        if self._raise_after is not None and len(self._lines) <= self._raise_after:
            raise ValueError("read of closed file")  # 强杀进程时管道关闭的真实表现
        return self._lines.pop(0) if self._lines else b""


class FakeProc:
    def __init__(self, lines=(), returncode=0, raise_after=None):
        self.stdout = FakeStdout(lines, raise_after=raise_after)
        self.returncode = returncode
        self._raise = raise_after is not None

    def wait(self, timeout=None):
        if self._raise:
            raise ValueError("wait on closed pipe")
        return self.returncode


class ExitApp:
    """只带 ``_watch_server_exit`` 会碰到的属性，不构造 Tk。"""

    def __init__(self, callbacks):
        self.callbacks = callbacks
        self._server_log_lines = []
        self._task_queue = queue.Queue()


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


def exit_payloads(q):
    return [payload for kind, payload in drain(q) if kind == "server_exit"]


class TestServerExitAlwaysReported:
    def test_missing_callback_still_reports(self):
        app = ExitApp({})
        ServerTabMixin._watch_server_exit(app)
        assert len(exit_payloads(app._task_queue)) == 1

    def test_none_process_still_reports(self):
        app = ExitApp({"get_server_process": lambda: None})
        ServerTabMixin._watch_server_exit(app)
        assert len(exit_payloads(app._task_queue)) == 1

    def test_pipe_error_still_reports_and_salvages_code(self):
        proc = FakeProc([b"only\n"], returncode=7, raise_after=2)
        app = ExitApp({"get_server_process": lambda: proc})
        ServerTabMixin._watch_server_exit(app)
        assert exit_payloads(app._task_queue) == [7], "异常路径必须投递，并尽量回捞真实退出码"

    def test_normal_path_orders_logs_before_exit(self):
        proc = FakeProc([b"l1\n", b"l2\n", b"[ERROR] boom\n"], returncode=3)
        app = ExitApp({"get_server_process": lambda: proc})
        ServerTabMixin._watch_server_exit(app)
        items = drain(app._task_queue)
        assert [k for k, _ in items] == ["server_log", "server_log", "server_log", "server_exit"]
        assert [p for k, p in items if k == "server_exit"] == [3]
        assert app._server_log_lines == ["l1", "l2", "[ERROR] boom"]

    def test_exit_reported_exactly_once_even_on_error(self):
        proc = FakeProc([b"x\n"], returncode=1, raise_after=1)
        app = ExitApp({"get_server_process": lambda: proc})
        ServerTabMixin._watch_server_exit(app)
        assert len(exit_payloads(app._task_queue)) == 1, "重复投递会让退出处理跑两遍"


# ─── D-85：主线程日志渲染必须同步 ───────────────────────────


class FakeText:
    def __init__(self, exists=True):
        self.inserted = []
        self._exists = exists

    def winfo_exists(self):
        return self._exists

    def configure(self, **kwargs):
        pass

    def insert(self, index, text):
        self.inserted.append(text)

    def see(self, index):
        pass


class LogApp:
    def __init__(self, text):
        self.server_log_text = text
        self._server_online_players = []
        self.after_calls = []
        self._tab_var = type("V", (), {"get": staticmethod(lambda: "mods")})()

    def after(self, delay, fn=None):
        self.after_calls.append(delay)
        if fn is not None:
            fn()  # 立即执行，代替事件循环
        return "id"


class TestServerLogRendering:
    def test_renders_synchronously_on_main_thread(self):
        text = FakeText()
        app = LogApp(text)
        ServerTabMixin._append_server_log(app, "[ERROR] 崩溃现场")
        assert text.inserted == ["[ERROR] 崩溃现场\n"], "主线程调用必须同步插入"
        assert app.after_calls == [], "不得再绕一层 after，否则模态对话框会抢在日志前弹出"

    def test_destroyed_widget_is_safely_skipped(self):
        text = FakeText(exists=False)
        app = LogApp(text)
        ServerTabMixin._append_server_log(app, "x")  # 不得抛异常
        assert text.inserted == []


# ─── D-92：缩略图加载 ───────────────────────────────────────


class Extractor:
    """受控的 ``extract_zip_thumbnail`` 替身：记录次数与并发峰值。"""

    def __init__(self, delay=0.0, fail_for=()):
        self.delay = delay
        self.fail_for = set(fail_for)
        self.calls = []
        self.active = 0
        self.peak = 0
        self._lock = threading.Lock()

    def __call__(self, zip_path, max_size=None):
        from pathlib import Path

        with self._lock:
            self.calls.append(str(zip_path))
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            if Path(zip_path).name in self.fail_for:
                raise RuntimeError("模拟解压失败")
            return f"B64::{Path(zip_path).stem}"
        finally:
            with self._lock:
                self.active -= 1


class ThumbApp:
    """复用真实方法，只提供假 Tk 接口。"""

    _THUMB_WORKERS = RM._THUMB_WORKERS
    _thumb_runner = RM._thumb_runner
    _load_thumbnail_async = RM._load_thumbnail_async
    _apply_thumbnail = RM._apply_thumbnail
    _release_thumb_pool = RM._release_thumb_pool
    _on_thumbnail_done = RM._on_thumbnail_done

    def __init__(self, alive=True):
        self._thumb_pool = None
        self._thumb_generation = 0
        self._thumb_inflight = set()
        self._thumbnail_loaded = 0
        self._thumbnail_total = 0
        self._tab_var = type("V", (), {"get": staticmethod(lambda: "resourcepacks")})()
        self._current_items = []
        self.alive = alive
        self.pending = []
        self.icons = []
        self.statuses = []

    def after(self, delay, fn=None):
        self.pending.append(fn)
        return "id"

    def winfo_exists(self):
        return self.alive

    def _set_thumbnail_icon(self, icon_frame, data, size):
        assert self.alive, "窗口已销毁却仍在操作控件"
        self.icons.append(data)

    def _get_resource_label(self, resource_type):
        return resource_type

    def _set_status(self, text, level="info"):
        self.statuses.append(text)

    def drain(self, timeout=20.0):
        """在主线程消费 after 队列，直到连续多轮无新回调。"""
        deadline = time.monotonic() + timeout
        idle = 0
        while time.monotonic() < deadline:
            if self.pending:
                self.pending.pop(0)()
                idle = 0
            else:
                idle += 1
                if idle > 40:
                    return
                time.sleep(0.01)

    def close(self):
        self._release_thumb_pool()


@pytest.fixture
def patched_extractor(monkeypatch):
    import modrinth

    holder = {}

    def install(delay=0.0, fail_for=()):
        ext = Extractor(delay=delay, fail_for=fail_for)
        monkeypatch.setattr(modrinth, "extract_zip_thumbnail", ext)
        holder["ext"] = ext
        return ext

    yield install
    holder.clear()


def items(n, prefix="pack", root="."):
    from pathlib import Path

    base = Path(root).resolve()
    return [{"path": str(base / f"{prefix}-{i}.zip")} for i in range(n)]


class TestThumbnailPool:
    def test_concurrency_is_bounded(self, patched_extractor):
        ext = patched_extractor(delay=0.15)
        app = ThumbApp()
        try:
            batch = items(12)
            app._thumbnail_total = len(batch)
            for it in batch:
                app._load_thumbnail_async(object(), it, 28)
            app.drain()
            assert len(ext.calls) == 12
            assert ext.peak == RM._THUMB_WORKERS, f"并发峰值应为 {RM._THUMB_WORKERS}，实际 {ext.peak}"
            assert len(app.icons) == 12
            assert app._thumbnail_loaded == 12
        finally:
            app.close()

    def test_same_path_is_not_extracted_twice(self, patched_extractor):
        """搜索框每敲一个字符都会重渲染列表，去重必须生效。"""
        ext = patched_extractor()
        app = ThumbApp()
        try:
            batch = items(3, prefix="dup")
            for _ in range(2):  # 第二次模拟 _on_search 重渲染
                for it in batch:
                    app._load_thumbnail_async(object(), it, 28)
            app.drain()
            assert len(ext.calls) == 3, f"重复提交被解压了 {len(ext.calls)} 次"
        finally:
            app.close()

    def test_stale_generation_writes_cache_but_not_ui(self, patched_extractor):
        patched_extractor(delay=0.25)
        app = ThumbApp()
        try:
            item = items(1, prefix="stale")[0]
            app._thumbnail_total = 1
            app._load_thumbnail_async(object(), item, 28)
            # 结果返回前列表被重新加载 → 换代
            app._thumb_generation += 1
            app._thumb_inflight.clear()
            app._thumbnail_loaded = 0
            app.drain()
            assert item.get("_thumbnail") == "B64::stale-0", "已完成的结果应进缓存，不该浪费"
            assert app.icons == [], "过期结果不得操作已被销毁的控件"
            assert app._thumbnail_loaded == 0, "过期结果不得抬高新一代计数"
        finally:
            app.close()

    def test_failed_extraction_still_counts(self, patched_extractor):
        """失败也要收尾，否则进度永远停在 total-1。"""
        patched_extractor(fail_for={"bad-0.zip"})
        app = ThumbApp()
        try:
            batch = items(3, prefix="mix") + items(1, prefix="bad")
            app._thumbnail_total = len(batch)
            for it in batch:
                app._load_thumbnail_async(object(), it, 28)
            app.drain()
            assert app._thumbnail_loaded == 4
            assert len(app.icons) == 3
            assert batch[3].get("_thumbnail") is None, "失败不得被当成已加载写入缓存"
        finally:
            app.close()

    def test_dead_window_is_safe(self, patched_extractor):
        patched_extractor()
        app = ThumbApp(alive=False)
        try:
            item = items(1, prefix="dead")[0]
            app._thumbnail_total = 1
            app._load_thumbnail_async(object(), item, 28)
            app.drain()
            assert app.icons == []
            assert app._thumbnail_loaded == 0
            assert item.get("_thumbnail") == "B64::dead-0"
        finally:
            app.close()

    def test_release_pool_stops_and_cancels(self):
        app = ThumbApp()
        pool = app._thumb_runner()
        blocker = threading.Event()
        # 必须占满所有 worker，第 N+1 个任务才真的处于排队状态
        blockers = [pool.submit(lambda: blocker.wait(5.0)) for _ in range(RM._THUMB_WORKERS)]
        queued = pool.submit(lambda: None)
        time.sleep(0.2)
        try:
            assert queued.done is False
            app.close()
            assert app._thumb_pool is None
            assert pool._shutdown is True
            assert queued.cancelled is True
        finally:
            blocker.set()
            for h in blockers:
                h.wait(5.0)

    def test_old_implementation_would_have_spawned_one_thread_per_item(self):
        """对照：旧实现是每个条目一个 threading.Thread（这里是 30 个）。

        不断言"旧实现有缺陷"，只固定"新实现不会随条目数线性增长线程"这一事实：
        新实现的工作线程数恒等于 ``_THUMB_WORKERS``。
        """
        before = threading.active_count()
        app = ThumbApp()
        try:
            pool = app._thumb_runner()
            for _ in range(30):
                pool.submit(lambda: None)
            pool.wait_all(timeout=10)
            workers = [t for t in threading.enumerate() if t.name.startswith("rm-thumb-")]
            assert len(workers) == RM._THUMB_WORKERS
            assert threading.active_count() <= before + RM._THUMB_WORKERS + 1
        finally:
            app.close()

    def test_pool_workers_are_daemon(self):
        app = ThumbApp()
        try:
            pool = app._thumb_runner()
            pool.submit(lambda: None).wait(5.0)
            workers = [t for t in threading.enumerate() if t.name.startswith("rm-thumb-")]
            assert workers and all(t.daemon for t in workers), "非 daemon 会让启动器退不掉"
        finally:
            app.close()


def test_task_runner_is_reusable_after_shutdown_is_not_silent():
    """``TaskRunner`` 关闭后再提交必须显式报错，而不是静默丢弃。"""
    runner = TaskRunner(max_workers=1, name="regress")
    runner.shutdown(wait=True)
    with pytest.raises(RuntimeError):
        runner.submit(lambda: None)
