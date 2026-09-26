"""服务器服务（阶段 1 任务 1.9）的单元测试。

覆盖被搬进 ``services/server_service.py`` 的**纯逻辑**：控制台日志行解析、玩家
列表维护、退出码判定与回捞、日志缓冲上限、内存文本格式化、可用版本分页、
每服启动配置读写，以及"服务在无 ``AppContext`` 时可独立实例化"。

**全部离线**：不启动真实服务器、不联网；进程一律用假对象（``FakeProc`` /
``FakeStdout``）。唯一碰真实系统的是"读本进程内存"一条（只读 psutil，不起子进程、
不发网络请求），并且 psutil 缺失时跳过。

界面侧的接线已经有永久守卫（``tests/test_thread_safety_fixes.py`` 的 D-84 恰好
投递一次、D-85 同步渲染；``poc/probe_server_exit.py`` / ``probe_server_mem.py``），
这里只补两件事：服务层自身的边界，以及"界面薄委托确实走到了服务"。
"""

from __future__ import annotations

import ast
import os
import queue
from pathlib import Path

import pytest

from services.server_service import (
    LOG_BUFFER_MAX_LINES,
    UNKNOWN_EXIT_CODE,
    ServerConsoleWatcher,
    ServerService,
    apply_player_events,
    format_exit_info,
    format_memory_mb,
    get_process_memory,
    paginate,
    parse_joined_player,
    parse_left_player,
    running_process_memory,
    salvage_exit_code,
)
from ui.app_server import ServerTabMixin, _get_server_service

# ─── 假进程对象（形状对齐 subprocess.Popen）──────────────────


class FakeStdout:
    def __init__(self, lines, raise_after=None):
        self._lines = list(lines)
        self._raise_after = raise_after

    def readline(self):
        if self._raise_after is not None and len(self._lines) <= self._raise_after:
            raise ValueError("read of closed file")  # 强杀进程时管道关闭的真实表现
        return self._lines.pop(0) if self._lines else b""


class FakeProc:
    def __init__(self, lines=(), returncode=0, raise_after=None, poll_result=None, pid=None):
        self.stdout = FakeStdout(lines, raise_after=raise_after)
        self.returncode = returncode
        self._raise = raise_after is not None
        self._poll_result = poll_result
        self.pid = pid

    def wait(self, timeout=None):
        if self._raise:
            raise ValueError("wait on closed pipe")
        return self.returncode

    def poll(self):
        return self._poll_result


class RaisingReturncode:
    """``returncode`` 是会抛异常的属性 —— 回捞逻辑必须容忍它。"""

    @property
    def returncode(self):
        raise RuntimeError("管道已关闭，拿不到退出码")


def watch_once(watcher, get_process=None):
    """跑一次 ``watch``，返回 (退出码, [日志行], [退出回调])。"""
    logs = []
    exits = []
    code = watcher.watch(get_process, on_log=logs.append, on_exit=exits.append)
    return code, logs, exits


# ─── 控制台日志行解析 ───────────────────────────────────────


class TestPlayerLineParsing:
    def test_join_line_extracts_player_name(self):
        assert parse_joined_player("[12:00:00] [Server thread/INFO]: <Steve> joined the game") == "Steve"

    def test_leave_line_extracts_player_name(self):
        assert parse_left_player("[12:00:00] [Server thread/INFO]: <Steve> left the game") == "Steve"

    def test_join_line_is_not_a_leave_event(self):
        line = "<Alex> joined the game"
        assert parse_joined_player(line) == "Alex"
        assert parse_left_player(line) is None

    def test_leave_line_is_not_a_join_event(self):
        line = "<Alex> left the game"
        assert parse_left_player(line) == "Alex"
        assert parse_joined_player(line) is None

    def test_name_keeps_inner_characters(self):
        """``<([^>]+)>`` 贪婪到最后一个 ``>`` 之前，名字里有空格/点也照收。"""
        assert parse_joined_player("<Player.1 two> joined the game") == "Player.1 two"

    def test_join_without_name_brackets_is_ignored(self):
        assert parse_joined_player("Steve joined the game") is None

    def test_trailing_whitespace_breaks_the_end_anchor(self):
        """外层 ``joined the game$`` 要求行尾**恰好**是它（记录现状：多一个空格就不认账）。"""
        assert parse_joined_player("<Steve> joined the game ") is None
        assert parse_left_player("<Steve> left the game ") is None

    def test_phrase_not_at_line_end_is_ignored(self):
        assert parse_joined_player("<Steve> joined the game (world)") is None

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "[12:00:00] [Server thread/INFO]: Done (1.234s)! For help, type \"help\"",
            "Steve[/127.0.0.1:5555] logged in with entity id 123",
            "Stopping the server",
            "<Steve> joined the gam",  # 被截断
        ],
    )
    def test_ordinary_lines_produce_no_event(self, line):
        assert parse_joined_player(line) is None
        assert parse_left_player(line) is None


class TestApplyPlayerEvents:
    def test_join_then_leave_updates_list_in_place(self):
        players = []
        assert apply_player_events(players, "<Steve> joined the game") == ["join"]
        assert players == ["Steve"]
        assert apply_player_events(players, "<Steve> left the game") == ["leave"]
        assert players == []

    def test_duplicate_join_is_not_counted_twice(self):
        players = ["Steve"]
        assert apply_player_events(players, "<Steve> joined the game") == []
        assert players == ["Steve"]

    def test_leave_of_unknown_player_is_ignored(self):
        players = ["Alex"]
        assert apply_player_events(players, "<Steve> left the game") == []
        assert players == ["Alex"]

    def test_works_on_the_callers_list_object(self):
        """界面侧 ``_server_online_players`` 必须被**原地**更新（其他分支直接读它）。"""
        players = []
        ref = players
        apply_player_events(players, "<Steve> joined the game")
        assert ref is players and ref == ["Steve"]

    def test_plain_log_line_changes_nothing(self):
        players = ["Steve"]
        assert apply_player_events(players, "[INFO] Saving chunks") == []
        assert players == ["Steve"]

    def test_two_players_join_in_order(self):
        players = []
        apply_player_events(players, "<A> joined the game")
        apply_player_events(players, "<B> joined the game")
        assert players == ["A", "B"]
        apply_player_events(players, "<A> left the game")
        assert players == ["B"]


# ─── 退出码 ─────────────────────────────────────────────────


class TestExitCode:
    def test_salvages_real_returncode(self):
        assert salvage_exit_code(FakeProc(returncode=7)) == 7

    def test_falls_back_to_sentinel_when_absent(self):
        assert salvage_exit_code(object()) == UNKNOWN_EXIT_CODE == -1

    def test_falls_back_when_returncode_is_not_an_int(self):
        assert salvage_exit_code(FakeProc(returncode=None)) == -1

    def test_tolerates_raising_returncode_attribute(self):
        assert salvage_exit_code(RaisingReturncode()) == -1

    def test_explicit_default_is_used(self):
        assert salvage_exit_code(None, 42) == 42

    def test_bool_returncode_is_accepted_as_int(self):
        """记录现状、疑为缺陷：``isinstance(True, int)`` 为真，于是退出码变成 ``True``。

        这是改造前就有的行为，搬运时逐字保留、不做修正（阶段 1 只搬家不改逻辑）。
        """
        assert salvage_exit_code(FakeProc(returncode=True)) is True

    def test_format_exit_info(self):
        assert format_exit_info(0) == ""
        assert format_exit_info(3) == " (exit_code=3)"
        assert format_exit_info(-1) == " (exit_code=-1)"


# ─── 内存采样的纯部分与格式化 ───────────────────────────────


class TestMemoryHelpers:
    def test_exited_process_yields_none(self):
        assert running_process_memory(FakeProc(poll_result=0, pid=os.getpid())) is None

    def test_missing_process_handle_yields_none(self):
        assert running_process_memory(None) is None

    def test_running_process_is_sampled(self):
        pytest.importorskip("psutil")
        mem = running_process_memory(FakeProc(poll_result=None, pid=os.getpid()))
        assert isinstance(mem, int) and mem > 0

    def test_unknown_pid_yields_none(self):
        assert get_process_memory(999_999_999) is None

    @pytest.mark.parametrize(
        "mem_mb,expected",
        [
            (0, "0 MB"),
            (512, "512 MB"),
            (1023, "1023 MB"),
            (1024, "1.0 GB"),
            (1536, "1.5 GB"),
            (2048, "2.0 GB"),
        ],
    )
    def test_format_memory_mb(self, mem_mb, expected):
        assert format_memory_mb(mem_mb) == expected

    def test_unit_is_injected_by_the_caller(self):
        """翻译留在界面侧（服务层不得 import ``ui.i18n``），所以单位是参数。"""
        assert format_memory_mb(300, "兆") == "300 兆"


# ─── 可用版本分页 ───────────────────────────────────────────


class TestPaginate:
    def test_empty_list_is_page_one_of_one(self):
        assert paginate([], 1, 10) == ([], 1, 1)

    def test_first_page(self):
        items = list(range(25))
        assert paginate(items, 1, 10) == ([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], 1, 3)

    def test_middle_page(self):
        items = list(range(25))
        assert paginate(items, 2, 10) == (list(range(10, 20)), 2, 3)

    def test_last_partial_page(self):
        items = list(range(25))
        assert paginate(items, 3, 10) == ([20, 21, 22, 23, 24], 3, 3)

    def test_page_beyond_end_is_clamped(self):
        assert paginate(list(range(25)), 99, 10) == ([20, 21, 22, 23, 24], 3, 3)

    def test_page_below_one_is_clamped(self):
        items = list(range(25))
        assert paginate(items, 0, 10) == ([0, 1, 2, 3, 4, 5, 6, 7, 8, 9], 1, 3)
        assert paginate(items, -5, 10) == (list(range(10)), 1, 3)

    def test_exact_multiple_does_not_add_a_page(self):
        assert paginate(list(range(20)), 1, 10)[2] == 2

    def test_dicts_are_returned_unchanged(self):
        versions = [{"id": "1.20.1"}, {"id": "1.20.2"}]
        assert paginate(versions, 1, 10)[0] == versions


# ─── 控制台读取器：日志缓冲 ─────────────────────────────────


class TestConsoleWatcherBuffer:
    def test_buffer_keeps_only_the_last_n_lines(self):
        watcher = ServerConsoleWatcher([], max_lines=3)
        for i in range(6):
            watcher.append_line(f"line-{i}")
        assert watcher.log_lines == ["line-3", "line-4", "line-5"]

    def test_default_cap_is_the_shared_limit(self):
        assert ServerConsoleWatcher([]).max_lines == LOG_BUFFER_MAX_LINES

    def test_cap_does_not_trim_before_it_is_reached(self):
        watcher = ServerConsoleWatcher([], max_lines=3)
        watcher.append_line("a")
        watcher.append_line("b")
        assert watcher.log_lines == ["a", "b"]

    def test_writes_into_the_callers_list_object(self):
        """必须原地写入调用方的列表 —— 界面与崩溃报告读的就是那个对象。"""
        lines = ["old"]
        ref = lines
        ServerConsoleWatcher(lines, max_lines=2).append_line("new")
        assert ref is lines and ref == ["old", "new"]

    def test_reset_clears_in_place(self):
        lines = ["old-1", "old-2"]
        ref = lines
        ServerConsoleWatcher(lines).reset()
        assert ref is lines and ref == []

    def test_self_created_buffer_is_used_when_none_given(self):
        watcher = ServerConsoleWatcher()
        watcher.append_line("only")
        assert watcher.log_lines == ["only"]

    def test_zero_max_lines_means_no_truncation(self):
        """记录现状、疑为缺陷：``max_lines=0`` 时 ``del lst[:-0]`` 是空切片，
        一行都不删（表现出"不限"而不是"全丢"）。运行期不可达（界面固定传
        ``LOG_BUFFER_MAX_LINES``、服务默认 5000），搬运时逐字保留、不做修正。
        """
        watcher = ServerConsoleWatcher([], max_lines=0)
        for i in range(3):
            watcher.append_line(f"line-{i}")
        assert watcher.log_lines == ["line-0", "line-1", "line-2"]


# ─── 控制台读取器：读管道 + 恰好一次退出回调 ────────────────


class TestConsoleWatcherWatch:
    def test_missing_callback_still_reports_exit_once(self):
        code, logs, exits = watch_once(ServerConsoleWatcher([]), None)
        assert logs == [] and exits == [UNKNOWN_EXIT_CODE]
        assert code == UNKNOWN_EXIT_CODE

    def test_none_process_still_reports_exit_once(self):
        code, logs, exits = watch_once(ServerConsoleWatcher([]), lambda: None)
        assert logs == [] and exits == [UNKNOWN_EXIT_CODE]

    def test_normal_path_orders_logs_before_exit(self):
        proc = FakeProc([b"l1\n", b"l2\n", b"[ERROR] boom\n"], returncode=3)
        code, logs, exits = watch_once(ServerConsoleWatcher([]), lambda: proc)
        assert logs == ["l1", "l2", "[ERROR] boom"]
        assert exits == [3] and code == 3

    def test_read_lines_are_buffered_and_trimmed(self):
        proc = FakeProc([b"l1\n", b"l2\n", b"l3\n", b"l4\n"], returncode=0)
        watcher = ServerConsoleWatcher([], max_lines=2)
        watch_once(watcher, lambda: proc)
        assert watcher.log_lines == ["l3", "l4"]

    def test_stale_buffer_is_cleared_before_reading(self):
        proc = FakeProc([b"fresh\n"], returncode=0)
        watcher = ServerConsoleWatcher(["stale-1", "stale-2"])
        watch_once(watcher, lambda: proc)
        assert watcher.log_lines == ["fresh"]

    def test_only_empty_byte_lines_are_dropped(self):
        proc = FakeProc([b"\n", b"   \n", b"real\n"], returncode=0)
        _, logs, _ = watch_once(ServerConsoleWatcher([]), lambda: proc)
        assert logs == ["   ", "real"], "只丢掉空字节串（EOF），非空行原样保留"

    def test_crlf_and_invalid_utf8_are_normalised(self):
        proc = FakeProc([b"windows\r\n", b"\xff\xfe bad\n"], returncode=0)
        _, logs, _ = watch_once(ServerConsoleWatcher([]), lambda: proc)
        assert logs[0] == "windows"
        assert logs[1].endswith(" bad") and "\ufffd" in logs[1]

    def test_pipe_error_reports_exit_once_and_salvages_code(self):
        proc = FakeProc([b"a\n", b"b\n", b"c\n"], returncode=7, raise_after=2)
        code, logs, exits = watch_once(ServerConsoleWatcher([]), lambda: proc)
        assert logs == ["a"], "异常发生前读到的行照旧进缓冲并转发"
        assert exits == [7] and code == 7
        assert exits.count(7) == 1, "重复投递会让退出处理跑两遍"

    def test_wait_error_reports_exit_once(self):
        proc = FakeProc([b"x\n"], returncode=5, raise_after=1)
        _, _, exits = watch_once(ServerConsoleWatcher([]), lambda: proc)
        assert exits == [5]

    def test_callback_error_does_not_break_the_exit_report(self):
        """``on_log`` 抛异常时，退出消息**仍然**恰好投递一次（finally 保证）。"""
        proc = FakeProc([b"a\n", b"b\n"], returncode=0)
        exits = []

        def boom(_text):
            raise RuntimeError("界面回调炸了")

        ServerConsoleWatcher([]).watch(lambda: proc, on_log=boom, on_exit=exits.append)
        assert exits == [0]

    def test_callbacks_are_optional(self):
        proc = FakeProc([b"a\n"], returncode=0)
        assert ServerConsoleWatcher([]).watch(lambda: proc) == 0


# ─── 服务对象本身 ───────────────────────────────────────────


class TestServerService:
    def test_identity_and_lifecycle(self):
        service = ServerService()
        assert service.name == "server"
        assert service.label == "服务器"
        assert service.requires == ()
        assert service.attached is False, "阶段 1 的 Tk 界面没有 AppContext，服务必须能独立实例化"
        assert service.describe()["class"] == "ServerService"

    def test_context_can_be_attached_like_any_service(self):
        from app.context import AppContext

        ctx = AppContext()
        service = ctx.register(ServerService())
        assert service.context is ctx
        assert ctx.try_get("server") is service
        assert service.attached is True

    def test_methods_forward_to_module_functions(self):
        service = ServerService()
        players = []
        assert service.apply_player_events(players, "<Steve> joined the game") == ["join"]
        assert players == ["Steve"]
        assert service.parse_joined_player("<S> joined the game") == "S"
        assert service.parse_left_player("<S> left the game") == "S"
        assert service.get_process_memory(999_999_999) is None
        assert service.running_process_memory(None) is None
        assert service.salvage_exit_code(FakeProc(returncode=4)) == 4
        assert service.format_exit_info(4) == " (exit_code=4)"
        assert service.format_memory_mb(2048) == "2.0 GB"
        assert service.paginate_versions([1, 2, 3], 2, 2) == ([3], 2, 2)

    def test_console_watcher_binds_the_given_buffer(self):
        lines = ["stale"]
        watcher = ServerService().console_watcher(lines, max_lines=1)
        assert isinstance(watcher, ServerConsoleWatcher)
        watcher.append_line("new")
        assert lines == ["new"]

    def test_watch_server_exit_returns_the_exit_code(self):
        proc = FakeProc([b"hello\n"], returncode=9)
        lines = []
        logged = []
        exits = []
        code = ServerService().watch_server_exit(
            lines,
            get_process=lambda: proc,
            on_log=logged.append,
            on_exit=exits.append,
        )
        assert (code, exits) == (9, [9])
        assert lines == ["hello"] and logged == ["hello"]

    # ── 每服启动配置（离线，只碰 tmp_path）──

    def test_server_dir_path(self, tmp_path):
        assert ServerService.server_dir_path(tmp_path, "1.20.1") == tmp_path / "1.20.1"

    def test_launch_memory_round_trip(self, tmp_path):
        service = ServerService()
        server_dir = service.server_dir_path(tmp_path, "1.20.1-forge-47.2.0")
        assert service.get_launch_memory(server_dir) is None, "未设置时必须是 None"
        assert service.set_launch_memory(server_dir, "4G") == (True, "")
        assert service.get_launch_memory(server_dir) == "4G"
        assert service.set_launch_memory(server_dir, None) == (True, "")
        assert service.get_launch_memory(server_dir) is None

    def test_launch_memory_read_failure_is_none(self):
        """读失败（不是目录、权限不足……）返回 None，不抛异常。"""
        assert ServerService().get_launch_memory("\0 非法路径") is None


# ─── 界面侧的薄委托（不构造任何 Tk 控件）────────────────────


class _FakeLabel:
    def __init__(self):
        self.texts = []

    def configure(self, **kwargs):
        self.texts.append(kwargs.get("text"))


class _FakeText:
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


class _UiFakeApp(ServerTabMixin):
    """只带被委托方法会碰到的属性，不构造任何 Tk 控件。

    直接继承真实的 ``ServerTabMixin``，这样测的是**真实方法**在假对象上的行为
    （与 ``tests/test_thread_safety_fixes.py`` 的 ``ThumbApp`` 同一思路），
    而不是复刻一份委托代码。
    """

    def __init__(self, **kwargs):
        self.callbacks = kwargs.pop("callbacks", {})
        self._server_log_lines = kwargs.pop("_server_log_lines", [])
        self._server_online_players = []
        self._task_queue = queue.Queue()
        self.after_calls = []
        self.statuses = []
        self.selected_server_version = kwargs.pop("selected_server_version", None)
        self._server_mem_monitor_after_id = None
        self.server_log_text = kwargs.pop("server_log_text", _FakeText())
        self.server_mem_label = _FakeLabel()
        for key, value in kwargs.items():
            setattr(self, key, value)

    def after(self, delay, fn=None):
        self.after_calls.append(delay)
        return "after-id"

    def set_status(self, text, level="info"):
        self.statuses.append((text, level))


class TestUiDelegation:
    def test_process_memory_is_the_service_function(self):
        """``_get_process_memory`` 必须**就是**服务层函数本身，而不是一层转发壳。

        ``poc/probe_server_mem.py`` 用 ``inspect.getsource`` 检查实现里的三个平台
        分支；转发壳会让那个探针看不到真实实现而误报失败。
        """
        from ui.app_server import ServerTabMixin

        assert ServerTabMixin._get_process_memory is get_process_memory

    def test_service_helper_caches_and_prefers_context(self):
        from app.context import AppContext

        from ui.app_server import ServerTabMixin, _get_server_service

        app = _UiFakeApp()
        service = ServerTabMixin._server_service(app)
        assert isinstance(service, ServerService)
        assert ServerTabMixin._server_service(app) is service, "同一次运行内应复用同一个实例"
        assert app._server_service_fallback is service

        registered = ServerService()
        AppContext().register(registered)  # 未 attach 到 app 的 context，不应被采用
        assert _get_server_service(app) is service

        ctx = AppContext()
        ctx.register(registered, replace=True)
        app.context = ctx
        assert _get_server_service(app) is registered, "有 AppContext 时必须优先用注册表里的实例"

    def test_service_helper_survives_broken_context(self):
        from ui.app_server import _get_server_service

        class _BrokenContext:
            def try_get(self, name):
                raise RuntimeError("注册表炸了")

        app = _UiFakeApp()
        app.context = _BrokenContext()
        assert isinstance(_get_server_service(app), ServerService)

    def test_helper_works_for_objects_without_setattr_space(self):
        """假对象/``__slots__`` 对象上缓存失败也要能用（探针就是这么调的）。"""
        from ui.app_server import _get_server_service

        class _Slotted:
            __slots__ = ()

        assert isinstance(_get_server_service(_Slotted()), ServerService)
        assert isinstance(_get_server_service(None), ServerService)

    def test_append_log_updates_players_through_service(self):
        from ui.app_server import ServerTabMixin

        app = _UiFakeApp()
        ServerTabMixin._append_server_log(app, "[12:00:00] [Server thread/INFO]: <Steve> joined the game")
        assert app._server_online_players == ["Steve"]
        assert app.after_calls == [0], "一个事件对应一次刷新投递"
        ServerTabMixin._append_server_log(app, "<Steve> left the game")
        assert app._server_online_players == []
        assert app.after_calls == [0, 0]

    def test_append_log_plain_line_schedules_nothing(self):
        from ui.app_server import ServerTabMixin

        app = _UiFakeApp()
        ServerTabMixin._append_server_log(app, "[INFO] Saving chunks")
        assert app.after_calls == []
        assert app.server_log_text.inserted == ["[INFO] Saving chunks\n"]

    def test_get_server_dir_path_delegates(self, tmp_path):
        from ui.app_server import ServerTabMixin

        app = _UiFakeApp(callbacks={"get_server_dir": lambda: str(tmp_path)})
        assert ServerTabMixin._get_server_dir_path(app, "1.20.1") == tmp_path / "1.20.1"

    def test_get_server_dir_path_without_callback(self):
        from ui.app_server import ServerTabMixin

        assert ServerTabMixin._get_server_dir_path(_UiFakeApp(), "1.20.1") is None

    def test_memory_setting_round_trip_through_mixin(self, tmp_path):
        from ui.app_server import ServerTabMixin

        app = _UiFakeApp(callbacks={"get_server_dir": lambda: str(tmp_path)}, selected_server_version="1.20.1")
        assert ServerTabMixin._get_server_memory_for(app, "1.20.1") is None
        ServerTabMixin._on_server_memory_changed(app, "4G")
        assert ServerTabMixin._get_server_memory_for(app, "1.20.1") == "4G"
        assert app.statuses and app.statuses[-1][1] == "info"

    def test_memory_change_without_selection_is_a_noop(self):
        from ui.app_server import ServerTabMixin

        app = _UiFakeApp(selected_server_version=None)
        ServerTabMixin._on_server_memory_changed(app, "4G")
        assert app.statuses == []

    def test_update_mem_display_formats_through_service(self):
        from ui.app_server import ServerTabMixin
        from ui.i18n import _

        pytest.importorskip("psutil")
        proc = FakeProc(poll_result=None, pid=os.getpid())
        app = _UiFakeApp(callbacks={"get_server_process": lambda: proc})
        ServerTabMixin._update_mem_display(app)
        assert len(app.server_mem_label.texts) == 1
        # <1GB 时单位必须来自界面的 _("mb")（翻译留在界面侧，服务只做数值分支）
        assert app.server_mem_label.texts[0].endswith(("GB", _("mb")))
        assert app.after_calls == [2000], "无论如何都要重新调度下一次采样"

    def test_update_mem_display_skips_exited_process(self):
        from ui.app_server import ServerTabMixin

        proc = FakeProc(poll_result=0, pid=os.getpid())
        app = _UiFakeApp(callbacks={"get_server_process": lambda: proc})
        ServerTabMixin._update_mem_display(app)
        assert app.server_mem_label.texts == []
        assert app.after_calls == [2000]

    def test_default_buffer_limits_stay_in_sync(self):
        """服务独立使用时的默认上限必须与控件侧常量一致（服务不能 import ``ui.*``）。"""
        from services.server_service import LOG_BUFFER_MAX_LINES as service_cap
        from ui.log_widget import LOG_BUFFER_MAX_LINES as widget_cap

        assert service_cap == widget_cap


# ═══════════════════════════════════════════════════════════════════
# 守卫：读子进程文本输出的调用必须显式容忍坏字节（第 12 轮新增）
#
# 这条守卫对应一个**实测踩到的**缺陷：`subprocess.run(..., text=True)` 不指定
# `encoding` 时用 `locale.getpreferredencoding(False)`，而项目自己的文档命令是
# `.venv\Scripts\python.exe -X utf8 -m pytest ...` —— `-X utf8` 一开，那个函数
# 返回 **utf-8**，于是子进程按 OEM 码页输出的中文（`tasklist` 就是）在 subprocess
# 的 reader 线程里抛 `UnicodeDecodeError`（实测 `byte 0xd0`）。
# 症状有两层：① 测试**绿着**却挂 `PytestUnhandledThreadExceptionWarning`；
# ② 生产侧内存/GPU 采样本来就跑在后台线程里，解码失败会让 `stdout` 变成空或半截，
# 采样**静默降级**成"取不到值"。
# ═══════════════════════════════════════════════════════════════════


def test_subprocess_text_calls_tolerate_bad_bytes():
    """`services/**` 里每个 `text=True` 的 subprocess 调用都要带 `errors=`。

    用 AST 判（不靠字符串搜索：注释里的 `text=True` 不是调用）。
    判据是"**必须有** errors="，不是"不能有 text=True" —— 读子进程输出本身没问题，
    问题只是"默认按本地码页解码、遇到坏字节就炸"。
    """
    repo_root = Path(__file__).resolve().parent.parent
    offenders: list[str] = []
    checked = 0
    for path in sorted((repo_root / "services").rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = ast.unparse(node.func)
            # 直接 `subprocess.*`，或服务自己的注入缝（`self._popen(...)`，默认就是 Popen）
            if not (fname.split(".")[0] == "subprocess" or fname.endswith(("_popen", "_run"))):
                continue
            kwargs = {k.arg for k in node.keywords if k.arg}
            if not ({"text", "universal_newlines"} & kwargs):
                continue
            checked += 1
            if "errors" not in kwargs:
                offenders.append(f"{path.relative_to(repo_root).as_posix()}:{node.lineno} {fname}")
    assert checked >= 10, f"只扫到 {checked} 处文本子进程调用，扫描范围是不是失效了？"
    assert offenders == [], (
        "下列调用读子进程文本输出时没有指定 errors=，在 `-X utf8`（项目自己的命令）下"
        "会在 reader 线程抛 UnicodeDecodeError：\n  " + "\n  ".join(offenders)
    )
