"""游戏窗口检测的钉子（阶段 3 任务 3.4 验收修复）。

## 它守的是什么

用户 2026-10-06 真机验收报的第三条："游戏窗口出现后没有检测到并最小化"。

根因是**两个读取方抢同一根 stdout 管道**：核心层建了进程并用自己的读取线程消费
stdout（转写终端），而界面侧（旧 Tk 的 `_watch_game_stdout` 与 QML 的
`GameService._watch_stdout`）又各开一个读取线程 `for line in proc.stdout`。
两个迭代器在同一个 `BufferedReader` 上，marker 那一行只会落到其中一方手里 ——
检测是否发生**取决于调度运气**。

修法：检测点收到核心层（它是管道的主人），界面侧只轮询 `window_detected()`。

这一组用例钉四件事：

1. `pump_game_output()`：见到 marker 触发一次回调、**之后不再读**、并把管道关掉；
2. `MinecraftLauncher.window_detected()` / `clear_window_detected()` 的语义；
3. `GameService._watch_stdout` 只轮询核心层（不再自己读管道）——
   用"给假进程一根**永不产出**的 stdout"来证明：老实现会一直卡在读取上，
   新实现照样能靠轮询拿到结果；
4. 两个界面侧（Tk 的 `_watch_game_stdout` 与 QML 的服务）走的是**同一个**检测来源。
"""

from __future__ import annotations

import io
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from launcher.core import WINDOW_MARKER, pump_game_output  # noqa: E402
from services.game_service import POLL_INTERVAL_S, GameService  # noqa: E402


class FakeStdout:
    """假管道：可按行喂，并记录"读了几行、关没关"。"""

    def __init__(self, lines: List[str], block_after: bool = False) -> None:
        self._lines = list(lines)
        self.closed = False
        self.read_lines = 0
        self._block_after = block_after

    def __iter__(self) -> Any:
        for line in self._lines:
            self.read_lines += 1
            yield (line + "\n").encode("utf-8")
        if self._block_after:
            # 模拟"游戏还活着、管道一直开着"：老实现会永远卡在这里
            while not self.closed:
                time.sleep(0.01)
                yield b""
        raise StopIteration

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    def __init__(self, stdout: Optional[FakeStdout], returncode: Optional[int] = None) -> None:
        self.stdout = stdout
        self._returncode = returncode

    def poll(self) -> Optional[int]:
        return self._returncode


# ─── 1. 核心层的读取原语 ───────────────────────────────────────


class TestPumpGameOutput:
    def test_marker_triggers_once_and_stops_reading(self) -> None:
        stdout = FakeStdout(["a", f"x {WINDOW_MARKER} y", "after-marker", "more"])
        lines: List[str] = []
        detected: List[int] = []
        ok = pump_game_output(FakeProcess(stdout), lines.append, lambda: detected.append(1))
        assert ok is True
        assert detected == [1], "窗口标记只该回调一次"
        assert lines == ["a", f"x {WINDOW_MARKER} y"], "见到标记之后就**不该**再往下读"
        assert stdout.closed is True, "管道必须关掉（不关会填满游戏的 stdout 缓冲）"

    def test_without_marker_reads_everything_and_keeps_the_pipe(self) -> None:
        stdout = FakeStdout(["a", "b", "c"])
        lines: List[str] = []
        ok = pump_game_output(FakeProcess(stdout), lines.append)
        assert ok is False
        assert lines == ["a", "b", "c"]
        assert stdout.closed is False, "没检测到就不该关（还有别的消费者要读终端输出）"

    def test_blank_lines_are_skipped(self) -> None:
        stdout = FakeStdout(["", "  ", "real"])
        lines: List[str] = []
        pump_game_output(FakeProcess(stdout), lines.append)
        assert lines == ["real"]

    def test_missing_stdout_is_not_an_error(self) -> None:
        assert pump_game_output(FakeProcess(None), lambda _line: None) is False

    def test_callback_needing_to_see_the_marker_line_itself(self) -> None:
        """回调**先于**判定：调用方（终端转写）要能拿到 marker 那一行。"""
        stdout = FakeStdout([f"prefix {WINDOW_MARKER}"])
        lines: List[str] = []
        pump_game_output(FakeProcess(stdout), lines.append, lambda: None)
        assert lines == [f"prefix {WINDOW_MARKER}"]

    def test_broken_callback_does_not_escape(self) -> None:
        stdout = FakeStdout([WINDOW_MARKER])

        def boom(_line: str) -> None:
            raise RuntimeError("回调坏了")

        assert pump_game_output(FakeProcess(stdout), boom) is False


# ─── 2. 核心层的公开状态 ───────────────────────────────────────


class TestLauncherState:
    """直接用**真的** `MinecraftLauncher` 太贵（要 mcllib + 镜像 + 主题引擎），

    所以这里只钉两个方法的语义：它们在实例上就是一个 Event。
    """

    def _instance(self) -> Any:
        from launcher.core import MinecraftLauncher

        class Stub:
            """只借用那两个方法与它们依赖的 `_window_detected`。"""

            window_detected = MinecraftLauncher.window_detected
            clear_window_detected = MinecraftLauncher.clear_window_detected

            def __init__(self) -> None:
                self._window_detected = threading.Event()

        return Stub()

    def test_flag_starts_false_and_flips(self) -> None:
        stub = self._instance()
        assert stub.window_detected() is False
        stub._window_detected.set()
        assert stub.window_detected() is True
        stub.clear_window_detected()
        assert stub.window_detected() is False, "每次启动前要能清掉上一次的检测结果"

    def test_missing_event_is_false_not_crash(self) -> None:
        class NoEvent:
            window_detected = __import__("launcher.core", fromlist=["MinecraftLauncher"]).MinecraftLauncher.window_detected

        assert NoEvent().window_detected() is False


# ─── 3. 服务侧的监控线程 ───────────────────────────────────────


class FakeLauncher:
    """假核心：只实现窗口检测 + 进程查询（`detected` 由用例控制）。"""

    def __init__(self, process: Optional[FakeProcess] = None) -> None:
        self.detected = False
        self.clears = 0
        self.process = process

    def window_detected(self) -> bool:
        return self.detected

    def clear_window_detected(self) -> None:
        self.clears += 1
        self.detected = False

    def get_game_process(self) -> Optional[FakeProcess]:
        return self.process


class RecordingObserver:
    def __init__(self) -> None:
        self.events: List[str] = []
        self.statuses: List[Any] = []

    def on_window_detected(self) -> None:
        self.events.append("window")

    def on_status(self, key: str, level: str, params: Any = None) -> None:
        self.statuses.append((key, level))


class TestServiceWatchesTheCore:
    def _service(self, launcher: FakeLauncher, observer: RecordingObserver) -> GameService:
        service = GameService(launcher=launcher)
        service.set_observer(observer)
        service._clock = time.monotonic  # noqa: SLF001 - 用真时钟，超时不会触发
        return service

    def test_polls_the_core_instead_of_reading_the_pipe(self) -> None:
        """**这条就是缺陷的判据**：管道永远不产出，轮询仍然拿得到窗口出现。

        老实现（自己读管道）在这里会一直阻塞 —— 因为没有任何一行输出。
        """
        blocker = FakeStdout([], block_after=True)
        launcher = FakeLauncher(FakeProcess(blocker, returncode=None))
        observer = RecordingObserver()
        service = self._service(launcher, observer)

        thread = threading.Thread(target=service._watch_stdout, daemon=True)  # noqa: SLF001
        thread.start()
        time.sleep(POLL_INTERVAL_S * 3)
        launcher.detected = True
        deadline = time.time() + 3
        while time.time() < deadline and not observer.events:
            time.sleep(POLL_INTERVAL_S)

        assert observer.events == ["window"], "核心层说窗口出现了，服务就该发 on_window_detected"
        assert blocker.read_lines == 0, "服务**不许**再去读那根管道"
        assert blocker.closed is True, "检测到之后要关管道"
        assert service.state == "running"

    def test_stops_when_the_process_is_gone(self) -> None:
        """进程已经退出就别再等 120 秒（旧实现靠"读线程死了"判断，这里看 poll）。"""
        launcher = FakeLauncher(FakeProcess(None, returncode=0))
        observer = RecordingObserver()
        service = self._service(launcher, observer)
        started = time.time()
        service._watch_stdout()  # noqa: SLF001
        assert time.time() - started < 2
        assert observer.events == []

    def test_falls_back_to_reading_when_the_core_has_no_api(self) -> None:
        """没有核心层的替身（单元测试里的假 launcher）走老路 —— 生产不会到这儿。"""

        class OldLauncher:
            def __init__(self) -> None:
                self.process = FakeProcess(FakeStdout([f"{WINDOW_MARKER} now"]))

            def get_game_process(self) -> FakeProcess:
                return self.process

        observer = RecordingObserver()
        service = GameService(launcher=OldLauncher())
        service.set_observer(observer)
        service._watch_stdout()  # noqa: SLF001
        assert observer.events == ["window"]

    def test_launch_clears_the_previous_detection(self) -> None:
        """连开两次游戏：第二次不该被上一次的检测结果直接算成"窗口已出现"。"""
        launcher = FakeLauncher()
        launcher.detected = True
        service = GameService(launcher=launcher)
        assert launcher.clears == 0
        # 直接调**工作者**（`launch()` 只是把它交给任务队列，清标志发生在工作者里）
        service._launch_sync("1.20.4", True, None, 25565)  # noqa: SLF001
        assert launcher.clears >= 1, "启动前必须 clear_window_detected()"


# ─── 4. 两个界面侧走同一个来源 ─────────────────────────────────


class TestBothUisUseTheSameSource:
    @staticmethod
    def _body(path: Path, start_marker: str, end_marker: str) -> str:
        """取一段函数体，**去掉三引号文档字符串**（判据要看代码，不是注释）。"""
        source = io.open(path, encoding="utf-8").read()
        start = source.find(start_marker)
        assert start >= 0, f"找不到 {start_marker}"
        end = source.find(end_marker, start)
        body = source[start:end if end > 0 else len(source)]
        out: List[str] = []
        in_doc = False
        for line in body.split("\n"):
            if line.count('"""') % 2 == 1:
                in_doc = not in_doc
                continue
            if not in_doc:
                out.append(line)
        return "\n".join(out)

    def test_tk_handler_polls_the_callback_key(self) -> None:
        body = self._body(REPO_ROOT / "ui" / "app_handlers.py", "def _watch_game_stdout", "def _test_connection")
        assert '"window_detected"' in body, "Tk 侧没有轮询核心层的检测结果"
        assert "for raw_line in proc.stdout" not in body, "Tk 侧又回去抢管道了"

    def test_service_polls_the_launcher(self) -> None:
        body = self._body(REPO_ROOT / "services" / "game_service.py", "def _watch_stdout(", "def _watch_stdout_directly")
        assert "launcher.window_detected()" in body
        assert "for raw_line in proc.stdout" not in body, "服务侧又回去抢管道了"

    def test_marker_constant_has_one_definition(self) -> None:
        """两份 `WINDOW_MARKER` 字符串一旦漂移，症状是"永远检测不到"且没有报错。"""
        core = io.open(REPO_ROOT / "launcher" / "core.py", encoding="utf-8").read()
        service = io.open(REPO_ROOT / "services" / "game_service.py", encoding="utf-8").read()
        assert core.count(f'WINDOW_MARKER = "{WINDOW_MARKER}"') == 1
        assert f'WINDOW_MARKER = "{WINDOW_MARKER}"' not in service, "服务侧要转出核心层的常量，不许再写一份"
        assert "from launcher.core import WINDOW_MARKER as CORE_WINDOW_MARKER" in service

    def test_core_callbacks_expose_the_detection(self) -> None:
        source = io.open(REPO_ROOT / "launcher" / "core.py", encoding="utf-8").read()
        assert '"window_detected": self.window_detected' in source
        assert '"clear_window_detected": self.clear_window_detected' in source
