"""`services/game_service.py` 的永久回归守卫（阶段 3 任务 3.1）。

这一层守的是**行为**，不是实现：启动/强杀/进程监控/崩溃归因这四件事原来长在
`ui/app_handlers.py` 里（混在 Tk 窗口类上，只能靠手点验证），现在长在服务上，
所以每一条规则都要有自动化判据 —— 对照表 B-03 ~ B-08 的"迁移后行为不变"就靠本文件。

测试里**不碰真的子进程**：`FakeLauncher` / `FakeProcess` 就是"核心层"的替身。
真进程只在手工验收里跑（启动一次真实游戏）。
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.context import AppContext  # noqa: E402
from services.game_service import (  # noqa: E402
    STATE_CRASHED,
    STATE_EXITED,
    STATE_IDLE,
    STATE_LAUNCHING,
    STATE_RUNNING,
    STATE_WAITING,
    WINDOW_MARKER,
    GameService,
)

# ─── 替身 ──────────────────────────────────────────────────────


class FakeStdout:
    """游戏 stdout 的替身：迭代完就结束（模拟管道关闭）。"""

    def __init__(self, lines: List[str]) -> None:
        self._lines = [line.encode("utf-8") for line in lines]
        self.closed = False

    def __iter__(self) -> Any:
        return iter(self._lines)

    def close(self) -> None:
        self.closed = True


class FakeProcess:
    """`subprocess.Popen` 的替身（只实现服务用到的那几个成员）。"""

    def __init__(self, exit_code: int = 0, stdout: Optional[FakeStdout] = None) -> None:
        self.returncode = exit_code
        self.stdout = stdout
        self.waited = False

    def wait(self, timeout: Optional[float] = None) -> int:
        self.waited = True
        return self.returncode


class FakeLauncher:
    """`MinecraftLauncher` 的替身。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.launch_result: Tuple[bool, Optional[str]] = (True, "1.20.4")
        self.raise_on_launch: Optional[BaseException] = None
        self.process: Optional[FakeProcess] = None
        self.kill_result = True
        self.minimize_default = False
        self.minecraft_dir: Optional[Path] = None
        self.skin_path: Optional[str] = None
        self.killed = 0

    def launch_game(self, version_id: str, minimize_after: bool = False, **kwargs: Any) -> Tuple[bool, Optional[str]]:
        self.calls.append({"version": version_id, "minimize": minimize_after, **kwargs})
        if self.raise_on_launch is not None:
            raise self.raise_on_launch
        return self.launch_result

    def kill_game_process(self) -> bool:
        self.killed += 1
        return self.kill_result

    def is_game_running(self) -> bool:
        return self.process is not None

    def get_game_process(self) -> Optional[FakeProcess]:
        return self.process

    def get_minimize_on_game_launch(self) -> bool:
        return self.minimize_default

    def get_minecraft_dir(self) -> str:
        return str(self.minecraft_dir or REPO_ROOT / ".minecraft")

    def get_skin_path(self) -> Optional[str]:
        return self.skin_path

    def set_skin_path(self, path: Optional[str]) -> None:
        self.skin_path = path


class RecordingObserver:
    """把服务回调按顺序记下来（**线程安全**：监控线程也会回调）。"""

    def __init__(self) -> None:
        self.states: List[str] = []
        self.statuses: List[Tuple[str, str, Dict[str, Any]]] = []
        self.launch_results: List[Tuple[str, Optional[str], bool, str]] = []
        self.windows = 0
        self.exits: List[Tuple[int, bool, Dict[str, Any], bool]] = []
        self.killed = 0
        self.backups: List[Tuple[str, str, bool]] = []
        self._lock = threading.Lock()

    def on_state(self, state: str) -> None:
        with self._lock:
            self.states.append(state)

    def on_status(self, key: str, level: str, params: Dict[str, Any]) -> None:
        with self._lock:
            self.statuses.append((key, level, dict(params)))

    def on_launch_result(self, version: str, target: Optional[str], success: bool, error: str) -> None:
        with self._lock:
            self.launch_results.append((version, target, success, error))

    def on_window_detected(self) -> None:
        with self._lock:
            self.windows += 1

    def on_game_exit(self, exit_code: int, crashed: bool, crash_files: Dict[str, Any], killed: bool) -> None:
        with self._lock:
            self.exits.append((exit_code, crashed, dict(crash_files), killed))

    def on_killed(self, exit_code: int) -> None:
        with self._lock:
            self.killed += 1

    def on_auto_backup(self, world: str, message: str, success: bool) -> None:
        with self._lock:
            self.backups.append((world, message, success))

    def status_keys(self) -> List[str]:
        with self._lock:
            return [key for key, _level, _params in self.statuses]


class FakeConfig:
    """配置替身：只放本服务读的那几个字段。"""

    def __init__(self, **kwargs: Any) -> None:
        self.backup_auto_launch = kwargs.get("backup_auto_launch", False)
        self.backup_auto_exit = kwargs.get("backup_auto_exit", False)
        self.last_launched_version = kwargs.get("last_launched_version", "")
        self.log_file = kwargs.get("log_file", Path("latest.log"))
        self.saved = 0

    def save_config(self) -> None:
        self.saved += 1


def make_service(launcher: Optional[FakeLauncher] = None, **config_kwargs: Any) -> Tuple[GameService, FakeLauncher, FakeConfig, RecordingObserver]:
    """按**生产取数路径**装一个服务：真 `AppContext` + 注入的假 launcher。"""
    fake = launcher or FakeLauncher()
    config = FakeConfig(**config_kwargs)
    ctx = AppContext(config=config)
    ctx.register_instance("launcher", fake)
    service = GameService(launcher=fake, clock=lambda: 0.0)
    ctx.register(service)
    observer = RecordingObserver()
    service.set_observer(observer)
    return service, fake, config, observer


def wait_for(predicate: Any, timeout: float = 5.0) -> bool:
    """等一个条件成立（监控线程是异步的）。"""
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return bool(predicate())


# ─── B-03 启动 ─────────────────────────────────────────────────


class TestLaunch:
    def test_launch_reports_launching_then_waiting(self) -> None:
        service, fake, _config, observer = make_service()
        handle = service.launch("1.20.4")
        assert handle is not None
        assert handle.wait(5.0) is True

        assert fake.calls and fake.calls[0]["version"] == "1.20.4"
        assert service.state == STATE_WAITING
        assert observer.states[:2] == [STATE_LAUNCHING, STATE_WAITING]
        assert observer.status_keys()[:2] == ["game_launching", "game_launched"]
        assert observer.launch_results == [("1.20.4", "1.20.4", True, "")]

    def test_launch_minimize_default_comes_from_the_core(self) -> None:
        fake = FakeLauncher()
        fake.minimize_default = True
        service, fake, _config, _observer = make_service(fake)
        handle = service.launch("1.20.4")
        assert handle is not None and handle.wait(5.0) is True
        assert fake.calls[0]["minimize"] is True
        assert service.minimize_after is True

    def test_explicit_minimize_flag_overrides_the_core_default(self) -> None:
        fake = FakeLauncher()
        fake.minimize_default = True
        service, fake, _config, _observer = make_service(fake)
        handle = service.launch("1.20.4", minimize_after=False)
        assert handle is not None and handle.wait(5.0) is True
        assert fake.calls[0]["minimize"] is False

    def test_launch_failure_returns_to_idle_with_a_status(self) -> None:
        fake = FakeLauncher()
        fake.launch_result = (False, None)
        service, _fake, _config, observer = make_service(fake)
        success, target = service._launch_sync("1.20.4", False, None, 25565)

        assert (success, target) == (False, None)
        assert service.state == STATE_IDLE
        assert "game_launch_failed" in observer.status_keys()
        assert observer.launch_results[-1][2] is False

    def test_launch_exception_becomes_a_reported_error(self) -> None:
        fake = FakeLauncher()
        fake.raise_on_launch = RuntimeError("boom")
        service, _fake, _config, observer = make_service(fake)
        success, _target = service._launch_sync("1.20.4", False, None, 25565)

        assert success is False
        assert service.state == STATE_IDLE
        assert "game_launch_error" in observer.status_keys()
        assert observer.statuses[-1][2]["error"] == "boom"

    def test_empty_version_is_rejected_without_touching_the_core(self) -> None:
        service, fake, _config, observer = make_service()
        assert service.launch("   ") is None
        assert fake.calls == []
        assert observer.status_keys() == ["version_id_required"]

    def test_launcher_missing_is_reported_not_crashed(self) -> None:
        ctx = AppContext(config=FakeConfig())
        service = GameService(clock=lambda: 0.0)
        ctx.register(service)
        observer = RecordingObserver()
        service.set_observer(observer)

        assert service.launch("1.20.4") is not None
        assert wait_for(lambda: "game_launch_error" in observer.status_keys())
        assert service.state == STATE_IDLE


# ─── B-04 强杀 ─────────────────────────────────────────────────


class TestKill:
    def test_kill_marks_the_user_and_notifies(self) -> None:
        service, fake, _config, observer = make_service()
        assert service.kill() is True
        assert fake.killed == 1
        assert observer.killed == 1
        assert observer.status_keys() == ["game_process_killed"]

    def test_kill_without_a_process_reports_info(self) -> None:
        fake = FakeLauncher()
        fake.kill_result = False
        service, _fake, _config, observer = make_service(fake)
        assert service.kill() is False
        assert observer.status_keys() == ["game_no_process"]
        assert observer.killed == 0


# ─── B-06 / B-07 进程监控 ──────────────────────────────────────


class TestWatching:
    def test_window_detection_fires_on_the_marker(self) -> None:
        fake = FakeLauncher()
        stdout = FakeStdout(["hello", f"[main/INFO]: {WINDOW_MARKER} 123 ms"])
        fake.process = FakeProcess(exit_code=0, stdout=stdout)
        service, _fake, _config, observer = make_service(fake)

        service._watch_stdout()
        assert observer.windows == 1
        assert service.state == STATE_RUNNING
        assert stdout.closed is True, "见到特征串后必须关管道（否则游戏会因缓冲区满卡顿）"

    def test_window_detection_without_the_marker_just_closes(self) -> None:
        fake = FakeLauncher()
        stdout = FakeStdout(["nothing useful"])
        fake.process = FakeProcess(exit_code=0, stdout=stdout)
        service, _fake, _config, observer = make_service(fake)

        service._watch_stdout()
        assert observer.windows == 0
        assert stdout.closed is True

    def test_window_detection_skips_without_stdout(self) -> None:
        fake = FakeLauncher()
        fake.process = FakeProcess(exit_code=0, stdout=None)
        service, _fake, _config, observer = make_service(fake)

        service._watch_stdout()
        assert observer.windows == 0

    def test_clean_exit_is_not_a_crash(self) -> None:
        fake = FakeLauncher()
        fake.process = FakeProcess(exit_code=0)
        service, _fake, _config, observer = make_service(fake)

        service._watch_exit()
        exit_code, crashed, crash_files, killed = observer.exits[-1]
        assert (exit_code, crashed, killed) == (0, False, False)
        assert crash_files == {}
        assert service.state == STATE_EXITED
        assert observer.statuses[-1][0] == "game_exited"

    def test_nonzero_exit_is_a_crash_with_collected_files(self, tmp_path: Path) -> None:
        fake = FakeLauncher()
        game_dir = tmp_path / ".minecraft"
        (game_dir / "crash-reports").mkdir(parents=True)
        crash = game_dir / "crash-reports" / "crash-2026-01-01_00.00.00-client.txt"
        crash.write_text("java.lang.NullPointerException", encoding="utf-8")
        (game_dir / "hs_err_pid1234.log").write_text("jvm", encoding="utf-8")
        (tmp_path / "logs").mkdir()
        (tmp_path / "logs" / "latest.log").write_text("log", encoding="utf-8")
        fake.minecraft_dir = game_dir
        fake.process = FakeProcess(exit_code=1)

        service, _fake, _config, observer = make_service(fake)
        service._watch_exit()

        exit_code, crashed, crash_files, killed = observer.exits[-1]
        assert (exit_code, crashed, killed) == (1, True, False)
        assert crash_files["crash_report"] == str(crash)
        assert crash_files["crash_report_list"] == [str(crash)]
        assert crash_files["jvm_crash_log"].endswith("hs_err_pid1234.log")
        assert crash_files["game_log"].endswith("latest.log")
        assert service.state == STATE_CRASHED
        assert observer.statuses[-1] == ("game_crashed", "error", {"code": 1})

    def test_killed_process_is_not_attributed_to_a_crash(self) -> None:
        fake = FakeLauncher()
        fake.process = FakeProcess(exit_code=-1)
        service, _fake, _config, observer = make_service(fake)

        service.kill()
        service._watch_exit()

        exit_code, crashed, _files, killed = observer.exits[-1]
        assert crashed is False, "用户强杀不是崩溃"
        assert killed is True
        assert exit_code == -1

    def test_version_isolated_crash_dir_wins_over_the_global_one(self, tmp_path: Path) -> None:
        fake = FakeLauncher()
        game_dir = tmp_path / ".minecraft"
        version_dir = game_dir / "versions" / "1.20.4-forge-49.0.26" / "crash-reports"
        version_dir.mkdir(parents=True)
        version_crash = version_dir / "crash-a.txt"
        version_crash.write_text("x", encoding="utf-8")
        global_dir = game_dir / "crash-reports"
        global_dir.mkdir(parents=True)
        (global_dir / "crash-b.txt").write_text("y", encoding="utf-8")
        fake.minecraft_dir = game_dir
        fake.process = FakeProcess(exit_code=2)

        service, _fake, _config, _observer = make_service(fake)
        service._current_version = "1.20.4-forge-49.0.26"
        files = service.collect_crash_info()
        assert files["crash_report"] == str(version_crash)

    def test_crash_collection_never_raises(self) -> None:
        fake = FakeLauncher()
        fake.minecraft_dir = Path("Z:/definitely/not/here")
        service, _fake, _config, _observer = make_service(fake)
        assert service.collect_crash_info() == {}


# ─── B-03 的两处自动备份 ───────────────────────────────────────


class TestAutoBackup:
    def test_disabled_flags_submit_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: List[str] = []
        monkeypatch.setattr(GameService, "auto_backup_sync", lambda self, trigger: called.append(trigger))
        service, _fake, _config, _observer = make_service()
        service._auto_backup("launch")
        service._auto_backup("exit")
        assert called == []

    def test_launch_backup_runs_a_background_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        called: List[str] = []
        monkeypatch.setattr(GameService, "auto_backup_sync", lambda self, trigger: called.append(trigger))
        service, _fake, _config, _observer = make_service(backup_auto_launch=True)
        service._auto_backup("launch")
        assert wait_for(lambda: called == ["launch"])
        # 退出后备份是另一条开关，没开就不该跑
        service._auto_backup("exit")
        assert called == ["launch"]

    def test_auto_backup_uses_the_legacy_note_keys(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """备份备注走语言文件（`backup_auto_note_launch` / `_exit`），不是硬编码中文。

        两段判据缺一不可：**服务确实去要了这两个键**（把 `_` 换成记录器），
        **这两个键在 4 个语言文件里真的存在**（直接读 json）——
        `_(note_key)` 是变量调用，`scripts/check_i18n.py` 的静态扫描看不见它，
        所以这里必须自己把"键存在"钉住。
        """
        notes: List[str] = []

        import services.game_service as game_service

        monkeypatch.setattr(game_service, "_", lambda key, **kwargs: f"<{key}>")

        class FakeManager:
            def __init__(self, _config: Any) -> None:
                pass

            def _find_all_world_dirs(self) -> List[Dict[str, Any]]:
                return [{"name": "world"}]

            def create_backup(self, world: str, note: str = "", progress_callback: Any = None) -> Tuple[bool, str]:
                notes.append(note)
                return True, "ok"

        import services.backup_manager as backup_manager

        monkeypatch.setattr(backup_manager, "BackupManager", FakeManager)
        service, _fake, _config, observer = make_service()
        assert service.auto_backup_sync("launch") == ("world", "ok")
        assert service.auto_backup_sync("exit") == ("world", "ok")
        assert notes == ["<backup_auto_note_launch>", "<backup_auto_note_exit>"]
        assert [item[2] for item in observer.backups] == [True, True]
        # 键必须在 4 个语言文件里都存在（占位符一致性由 check_i18n 管，这里只管存在性）
        import json

        for lang in ("zh_CN", "en_US", "zh_TW", "ja_JP"):
            table = json.loads((REPO_ROOT / "ui" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            for key in ("backup_auto_note_launch", "backup_auto_note_exit"):
                assert key in table, f"{lang} 缺少 {key}"

    def test_no_worlds_is_a_silent_no_op(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeManager:
            def __init__(self, _config: Any) -> None:
                pass

            def _find_all_world_dirs(self) -> List[Dict[str, Any]]:
                return []

        import services.backup_manager as backup_manager

        monkeypatch.setattr(backup_manager, "BackupManager", FakeManager)
        service, _fake, _config, _observer = make_service()
        assert service.auto_backup_sync("launch") is None

    def test_exit_backup_only_happens_on_clean_exit(self) -> None:
        """与旧实现一致：崩溃分支**不**触发退出后备份。"""
        triggered: List[str] = []
        fake = FakeLauncher()
        fake.process = FakeProcess(exit_code=1)
        service, _fake, _config, _observer = make_service(fake)
        service._auto_backup = lambda trigger: triggered.append(trigger)  # type: ignore[assignment]
        service._watch_exit()
        assert triggered == []

        fake2 = FakeLauncher()
        fake2.process = FakeProcess(exit_code=0)
        service2, _f2, _c2, _o2 = make_service(fake2)
        service2._auto_backup = lambda trigger: triggered.append(trigger)  # type: ignore[assignment]
        service2._watch_exit()
        assert triggered == ["exit"]


# ─── 最近使用版本 / 成就 / 停止 ────────────────────────────────


class TestBookkeeping:
    def test_last_launched_version_is_remembered_and_persisted(self) -> None:
        fake = FakeLauncher()
        fake.launch_result = (True, "1.20.4-forge-49.0.26")
        service, _fake, config, _observer = make_service(fake)
        service._launch_sync("1.20.4", False, None, 25565)

        assert service.last_launched_version == "1.20.4-forge-49.0.26"
        assert config.saved == 1, "记忆必须落盘，否则重启后首页就没有'最近使用版本'"

    def test_remembering_the_same_version_does_not_write_again(self) -> None:
        service, _fake, config, _observer = make_service(last_launched_version="1.20.4")
        service._remember_version("1.20.4")
        assert config.saved == 0

    def test_last_launched_version_defaults_to_empty(self) -> None:
        service, _fake, _config, _observer = make_service()
        assert service.last_launched_version == ""

    def test_launch_triggers_the_two_gamer_achievements(self, monkeypatch: pytest.MonkeyPatch) -> None:
        triggered: List[str] = []

        class FakeEngine:
            def update_progress(self, achievement_id: str, value: int = 1, trigger_type: Any = None) -> None:
                triggered.append(achievement_id)

        import achievement_engine

        monkeypatch.setattr(achievement_engine, "get_achievement_engine", lambda: FakeEngine())
        service, _fake, _config, _observer = make_service()
        service._launch_sync("1.20.4", False, None, 25565)
        assert triggered == ["gamer_first_launch", "gamer_launch_master"]

    def test_crash_triggers_the_crash_achievement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        triggered: List[str] = []

        class FakeEngine:
            def update_progress(self, achievement_id: str, value: int = 1, trigger_type: Any = None) -> None:
                triggered.append(achievement_id)

        import achievement_engine

        monkeypatch.setattr(achievement_engine, "get_achievement_engine", lambda: FakeEngine())
        fake = FakeLauncher()
        fake.process = FakeProcess(exit_code=3)
        service, _fake, _config, _observer = make_service(fake)
        service._watch_exit()
        assert triggered == ["advanced_crash_analyst"]

    def test_broken_observer_does_not_break_the_flow(self) -> None:
        class BadObserver:
            def on_state(self, state: str) -> None:
                raise RuntimeError("界面坏了")

        service, _fake, _config, _observer = make_service()
        service.set_observer(BadObserver())
        success, _target = service._launch_sync("1.20.4", False, None, 25565)
        assert success is True
        assert service.state == STATE_WAITING

    def test_stop_stops_the_stdout_watch(self) -> None:
        """`stop()` 之后窗口监控不再空转（120 秒的轮询要能被打断）。"""
        fake = FakeLauncher()
        stdout = FakeStdout([])
        fake.process = FakeProcess(exit_code=0, stdout=stdout)
        service, _fake, _config, observer = make_service(fake)
        service.stop()
        service._watch_stdout()
        assert observer.windows == 0


class TestStateContract:
    def test_state_values_are_stable(self) -> None:
        assert (
            STATE_IDLE,
            STATE_LAUNCHING,
            STATE_WAITING,
            STATE_RUNNING,
            STATE_EXITED,
            STATE_CRASHED,
        ) == ("idle", "launching", "waiting", "running", "exited", "crashed")

    def test_game_running_falls_back_to_state_without_a_core(self) -> None:
        ctx = AppContext(config=FakeConfig())
        service = GameService(clock=lambda: 0.0)
        ctx.register(service)
        assert service.game_running is False
        service._set_state(STATE_RUNNING)
        assert service.game_running is True
