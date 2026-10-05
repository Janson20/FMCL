"""游戏进程与启动流程服务（阶段 3 任务 3.1；对应对照表 B-03 ~ B-08）。

## 它是什么

"启动一个已安装的版本"这件事在旧界面里被拆成**三处**：`ui/app_handlers.py`
（启动/强杀/进度动画/窗口检测/退出监控/崩溃归因）、`ui/app_backup.py`
（启动前与退出后的自动备份）、以及 `launcher/core.py`（真正的子进程启动）。
QML 界面要的不是这套"混在窗口类里的方法"，而是一个能单独实例化、能单独测试、
**与界面无关**的对象 —— 就是本服务。

搬到这里的规则（逐条对照 `04-parity-matrix.md`）：

| 对照表 | 规则 | 旧实现位置 |
|--------|------|-----------|
| B-03 | 启动前自动备份 → 启动 → 最小化开关 | `ui/app_handlers.py:941-952`；`ui/app_backup.py:636-658` |
| B-04 | 强制结束游戏进程（用户强杀标记） | `ui/app_handlers.py:356-367` |
| B-05 | 启动过程"加载中"动画的**启停时机** | `ui/app_handlers.py:389-416`、`1144-1160` |
| B-06 | 游戏窗口出现检测（读 stdout 特征串，120 秒超时） | `ui/app_handlers.py:506-565` |
| B-07 | 游戏退出监控与崩溃归因（退出码非 0 且非用户强杀） | `ui/app_handlers.py:418-437` |
| B-08 | 崩溃文件收集（latest.log / debug.log / crash-reports 最新 10 个 / hs_err_pid） | `ui/app_handlers.py:439-504` |

**动画本身不在这里**：B-05 的"进度条往返滚动、50ms 步进"是纯视图行为，
服务只负责**告诉界面"该起动画了"和"该停动画了"**（`on_launch_result` /
`on_window_detected` / `on_game_exit` 三个回调就是那两个时机）。

## 观察者协议（本服务唯一的对外出口）

服务**不 import 任何 UI 库**，也**不持有界面文案**。它做两件事：

1. 改自己的状态，然后调 `observer.on_state(state)`；
2. 把"要对用户说的话"表示成 **i18n 键 + 参数**（``on_status(key, level, params)``），
   由界面侧翻译（旧界面 `_(key, **params)`、QML 界面 `Tr.map[key]`）。

回调**可能在任何线程被调用**（启动、退出监控都在工作线程里）。这是刻意的：
Qt 侧的正确做法就是"worker 只 emit 信号"（契约第三节硬规则 3），Tk 侧则是
"worker 只往队列里放东西"（项目经验：绝不让 worker 碰 Tk）。两边都天然满足，
所以服务不必知道自己在哪个线程。

## 与旧实现刻意一致的三处（不是疏漏）

1. **启动前备份不阻塞启动**：旧实现 `_auto_backup_before_launch()` 是"再开一个
   线程去备份"，`launch_game()` 不等它 —— 备份大存档时启动并不变慢。
   本服务保持同序（备份任务与启动任务各自独立提交），行为一致。
2. **崩溃时不自动备份**：旧实现只在 `game_exited`（正常退出）分支里调
   `_auto_backup_after_exit()`，崩溃分支没有。保持。
3. **超时后照样关 stdout 管道**：窗口检测超时（120 秒）不等于失败，旧实现
   只是关掉管道避免缓冲区打满导致游戏卡顿，仍然继续监控退出。保持。
"""

from __future__ import annotations

import platform
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from services.base import Service
from services.i18n_service import _

# ─── 数值常量（全部抄自旧实现；改动要同步对照表 B-05/B-06/B-07 的备注）───

#: 游戏窗口出现的判据：Minecraft 输出这行就说明 Bootstrap 已经跑完
#: （旧 `ui/app_handlers.py:516`）。
WINDOW_MARKER = "Datafixer optimizations took"

#: 窗口检测超时（秒，旧 `ui/app_handlers.py:517`）。
WINDOW_TIMEOUT_S = 120.0

#: 轮询间隔（秒，旧 `ui/app_handlers.py:558`）。
POLL_INTERVAL_S = 0.2

#: 强杀时给插件钩子的退出码（旧 `ui/app_handlers.py:364`）。
KILL_EXIT_CODE = -1

#: 崩溃报告最多收集几个（旧 `ui/app_handlers.py:495`）。
CRASH_REPORT_LIMIT = 10

# ─── 状态机（界面据此决定按钮可用性；取值是稳定契约）───

STATE_IDLE = "idle"
STATE_LAUNCHING = "launching"
STATE_WAITING = "waiting"  # 进程已起，等游戏窗口
STATE_RUNNING = "running"  # 窗口已出现
STATE_EXITED = "exited"
STATE_CRASHED = "crashed"

ALL_STATES: Tuple[str, ...] = (
    STATE_IDLE,
    STATE_LAUNCHING,
    STATE_WAITING,
    STATE_RUNNING,
    STATE_EXITED,
    STATE_CRASHED,
)


class GameService(Service):
    """启动/强杀/监控一个已安装版本，并把过程讲给观察者听。"""

    name = "game"
    label = "游戏进程与启动"

    def __init__(
        self,
        context: Any = None,
        *,
        launcher: Any = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """
        Args:
            context: ``AppContext``（由 ``Service.attach`` 注入）。
            launcher: 显式注入的 ``MinecraftLauncher``。**测试用**；生产路径为
                None，届时从 ``AppContext`` 取 ``"launcher"``
                （由 ``app/startup.py`` 在启动流程里注册进去）。
            clock: 时间源。**测试用**（默认 ``time.time``）。
        """
        super().__init__(context)
        self._launcher = launcher
        self._clock = clock or time.time
        self._observer: Any = None
        self._state = STATE_IDLE
        self._current_version = ""
        self._minimize_after = False
        self._killed_by_user = False
        self._stopping = False
        self._lock = threading.RLock()
        #: 退出监控线程的句柄，``stop()`` 时只置标志、不强杀（进程是用户的）
        self._exit_thread: Optional[threading.Thread] = None
        self._stdout_thread: Optional[threading.Thread] = None

    # ─── 装配 ───────────────────────────────────────────────

    def use_launcher(self, launcher: Any) -> None:
        """注入启动器核心（``AppContext`` 里那份唯一实例）。"""
        self._launcher = launcher

    def set_observer(self, observer: Any) -> None:
        """设置观察者（界面桥）。同一时刻只保留一个 —— 它代表"当前那个界面"。"""
        self._observer = observer

    def stop(self) -> None:
        """退出期：置停止标志，让两个监控线程自然收尾（**不杀游戏进程**）。"""
        self._stopping = True
        super().stop()

    # ─── 状态 ───────────────────────────────────────────────

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def current_version(self) -> str:
        with self._lock:
            return self._current_version

    @property
    def minimize_after(self) -> bool:
        """本次启动是否要求"窗口出现后最小化启动器"（决定权在界面侧，见 B-03）。"""
        with self._lock:
            return self._minimize_after

    @property
    def game_running(self) -> bool:
        """游戏进程是否还在（以核心为准；核心不可用时退化为状态判断）。"""
        launcher = self._resolve_launcher()
        if launcher is not None:
            try:
                return bool(launcher.is_game_running())
            except Exception as e:  # noqa: BLE001 - 探测失败不该抛给界面
                self.log.warning("询问游戏进程状态失败: %s", e)
        return self.state in (STATE_LAUNCHING, STATE_WAITING, STATE_RUNNING)

    @property
    def last_launched_version(self) -> str:
        """上次成功启动的版本 ID（首页"最近使用版本"，A/3.1 的摘要项）。

        读 ``config.last_launched_version`` —— 这是阶段 3 新增的**可选**键：
        旧配置里没有它时取空串，不会让老配置文件失效（红线 4：数据格式不变）。
        """
        try:
            return str(getattr(self.config, "last_launched_version", "") or "")
        except Exception as e:  # noqa: BLE001
            self.log.warning("读取 last_launched_version 失败: %s", e)
            return ""

    # ─── 启动 ───────────────────────────────────────────────

    def launch(
        self,
        version_id: str,
        *,
        minimize_after: Optional[bool] = None,
        server_ip: Optional[str] = None,
        server_port: int = 25565,
    ) -> Any:
        """启动一个版本（非阻塞）。返回 ``TaskHandle``（测试可 ``wait()``）。

        Args:
            version_id: 版本 ID（原版 ID 或 loader 版本 ID，核心自己会做模糊匹配）。
            minimize_after: 窗口出现后是否最小化启动器。None = 读配置
                （``get_minimize_on_game_launch()``，与旧界面的 `minimize_var` 同源）。
            server_ip / server_port: 联机直连参数（3.13 用；默认不直连）。
        """
        version = str(version_id or "").strip()
        if not version:
            self._emit_status("version_id_required", "error", {})
            return None

        minimize = self._read_minimize_default() if minimize_after is None else bool(minimize_after)
        with self._lock:
            self._minimize_after = minimize
            self._killed_by_user = False
            self._current_version = version

        self._set_state(STATE_LAUNCHING)
        self._emit_status("game_launching", "loading", {"version": version})

        # B-03：启动前自动备份。**刻意不等它**（见模块文档"与旧实现刻意一致的三处"）。
        self._auto_backup("launch")

        return self.tasks.submit(
            self._launch_sync,
            version,
            minimize,
            server_ip,
            server_port,
            name=f"game.launch.{version}",
            on_error=lambda e, v=version: self._on_launch_error(v, e),
        )

    def _launch_sync(
        self, version: str, minimize: bool, server_ip: Optional[str], server_port: int
    ) -> Tuple[bool, Optional[str]]:
        """工作线程：真正调核心启动，成功后踢出两个监控线程。"""
        launcher = self._resolve_launcher()
        if launcher is None:
            self._on_launch_error(version, "launcher 不可用")
            return False, None
        try:
            success, target_version = launcher.launch_game(
                version, minimize_after=minimize, server_ip=server_ip, server_port=server_port
            )
        except Exception as e:  # noqa: BLE001 - 核心抛异常要变成"启动失败"而不是崩掉
            self.log.error("启动 %s 失败: %s", version, e, exc_info=True)
            self._on_launch_error(version, e)
            return False, None

        if success:
            target = str(target_version or version)
            with self._lock:
                self._current_version = target
            self._remember_version(target)
            self._set_state(STATE_WAITING)
            self._emit_status("game_launched", "loading", {"version": version})
            # 旧实现在 `launch_done` 分支里触发这两项（ui/app_handlers.py:1155-1156）
            self._trigger_ach("gamer_first_launch")
            self._trigger_ach("gamer_launch_master")
            self._spawn_watchers()
        else:
            self._set_state(STATE_IDLE)
            self._emit_status("game_launch_failed", "error", {"version": version})
        self._emit("on_launch_result", version, target_version, bool(success), "")
        return bool(success), target_version

    def _on_launch_error(self, version: str, error: Any) -> None:
        self._set_state(STATE_IDLE)
        self._emit_status("game_launch_error", "error", {"error": str(error)})
        self._emit("on_launch_result", version, None, False, str(error))

    def _spawn_watchers(self) -> None:
        """B-06 + B-07：两个后台监控线程（窗口出现、进程退出）。"""
        self._stdout_thread = threading.Thread(
            target=self._watch_stdout, name="game-watch-stdout", daemon=True
        )
        self._exit_thread = threading.Thread(target=self._watch_exit, name="game-watch-exit", daemon=True)
        self._stdout_thread.start()
        self._exit_thread.start()

    # ─── B-04 强杀 ──────────────────────────────────────────

    def kill(self) -> bool:
        """强制结束游戏进程。返回是否真的杀掉了（没有进程时返回 False）。"""
        launcher = self._resolve_launcher()
        if launcher is None:
            return False
        try:
            success = bool(launcher.kill_game_process())
        except Exception as e:  # noqa: BLE001
            self.log.error("强制结束游戏进程失败: %s", e)
            return False
        if success:
            with self._lock:
                self._killed_by_user = True
            self._emit_status("game_process_killed", "warning", {})
            self._emit("on_killed", KILL_EXIT_CODE)
        else:
            self._emit_status("game_no_process", "info", {})
        return success

    # ─── B-07 退出监控 ──────────────────────────────────────

    def _watch_exit(self) -> None:
        proc = self._game_process()
        if proc is None:
            return
        try:
            proc.wait()
        except Exception as e:  # noqa: BLE001
            self.log.warning("等待游戏进程退出失败: %s", e)
            return
        exit_code = proc.returncode
        self.log.info("游戏进程已退出，退出码: %s", exit_code)

        with self._lock:
            killed = self._killed_by_user
            self._killed_by_user = False
        crashed = exit_code != 0 and not killed
        crash_files = self.collect_crash_info() if crashed else {}
        self._set_state(STATE_CRASHED if crashed else STATE_EXITED)
        if crashed:
            self._emit_status("game_crashed", "error", {"code": exit_code})
            # 旧实现在 `game_crashed` 分支里触发（ui/app_handlers.py:1322）
            self._trigger_ach("advanced_crash_analyst")
        else:
            self._emit_status("game_exited", "info", {})
        if not crashed:
            # 与旧实现一致：只有正常退出才触发退出后备份（见模块文档第 2 条）
            self._auto_backup("exit")
        self._emit("on_game_exit", exit_code, crashed, crash_files, killed)

    def _game_process(self) -> Any:
        launcher = self._resolve_launcher()
        if launcher is None:
            return None
        try:
            return launcher.get_game_process()
        except Exception as e:  # noqa: BLE001
            self.log.warning("取游戏进程句柄失败: %s", e)
            return None

    # ─── B-06 窗口出现检测 ──────────────────────────────────

    def _watch_stdout(self) -> None:
        """读游戏 stdout，见到特征串就关掉管道（避免缓冲区打满让游戏卡顿）。"""
        proc = self._game_process()
        if proc is None or getattr(proc, "stdout", None) is None:
            self.log.warning("无法获取游戏进程 stdout，跳过窗口检测")
            return

        detected = threading.Event()

        def _reader() -> None:
            try:
                for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="ignore") if isinstance(raw_line, bytes) else str(raw_line)
                    if WINDOW_MARKER in line:
                        detected.set()
                        return
            except Exception:  # noqa: BLE001 - 管道被关掉是正常路径
                pass

        reader = threading.Thread(target=_reader, name="game-watch-stdout-reader", daemon=True)
        reader.start()

        deadline = self._clock() + WINDOW_TIMEOUT_S
        while not self._stopping and self._clock() < deadline:
            if detected.is_set():
                self.log.info("检测到游戏窗口出现 (Datafixer Bootstrap)，关闭 stdout 管道")
                self._close_stdout(proc)
                self._set_state(STATE_RUNNING)
                self._emit("on_window_detected")
                return
            if not reader.is_alive():
                self.log.info("游戏 stdout 已关闭，释放管道")
                self._close_stdout(proc)
                return
            time.sleep(POLL_INTERVAL_S)

        self.log.info("游戏 stdout 监控超时 (%.0fs)，关闭管道", WINDOW_TIMEOUT_S)
        self._close_stdout(proc)

    @staticmethod
    def _close_stdout(proc: Any) -> None:
        try:
            stdout = getattr(proc, "stdout", None)
            if stdout is not None:
                stdout.close()
        except Exception:  # noqa: BLE001 - 已经关了/已被回收都算成功
            pass

    # ─── B-08 崩溃文件收集 ──────────────────────────────────

    def collect_crash_info(self) -> Dict[str, Any]:
        """收集崩溃相关文件（后台线程调用；失败只记日志，返回已收到的部分）。

        返回键：``game_log`` / ``debug_log`` / ``crash_report``（最新一个）/
        ``crash_report_list``（最新 10 个）/ ``jvm_crash_log``。旧实现同样"有就放，
        没有就不放"，调用方要用 ``in`` 判断而不是取默认值。
        """
        files: Dict[str, Any] = {}
        try:
            launcher = self._resolve_launcher()
            mc_dir = Path(launcher.get_minecraft_dir()) if launcher is not None else None
            if mc_dir is None or not mc_dir.exists():
                return files
            base_dir = mc_dir.parent

            # ── 游戏日志：按平台找 ──
            if platform.system().lower() == "linux":
                logs_dir = Path(self.config.log_file).parent
            else:
                logs_dir = base_dir / "logs"
            if logs_dir.exists():
                for key, name in (("game_log", "latest.log"), ("debug_log", "debug.log")):
                    candidate = logs_dir / name
                    if candidate.exists():
                        files[key] = str(candidate)

            # ── 崩溃报告 / JVM 崩溃日志：先版本隔离目录，再全局目录 ──
            game_dirs = []
            version_id = self.current_version
            if version_id:
                version_dir = mc_dir / "versions" / version_id
                if version_dir.exists():
                    game_dirs.append(version_dir)
            game_dirs.append(mc_dir)

            for game_dir in game_dirs:
                crash_dir = game_dir / "crash-reports"
                if crash_dir.exists():
                    reports = sorted(
                        crash_dir.glob("crash-*.txt"), key=lambda f: f.stat().st_mtime, reverse=True
                    )
                    if reports:
                        files.setdefault("crash_report", str(reports[0]))
                        files.setdefault("crash_report_list", [str(f) for f in reports[:CRASH_REPORT_LIMIT]])
                hs_err = sorted(game_dir.glob("hs_err_pid*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
                if hs_err:
                    files.setdefault("jvm_crash_log", str(hs_err[0]))
        except Exception as e:  # noqa: BLE001 - 崩溃收集绝不能再抛异常打断退出路径
            self.log.error("收集崩溃信息失败: %s", e)
        return files

    # ─── 自动备份（B-03 的启动前 / 退出后两处）──────────────

    def _auto_backup(self, trigger: str) -> None:
        """按配置决定是否自动备份（``backup_auto_launch`` / ``backup_auto_exit``）。"""
        attr = "backup_auto_launch" if trigger == "launch" else "backup_auto_exit"
        try:
            if not bool(getattr(self.config, attr, False)):
                return
        except Exception:  # noqa: BLE001 - 读配置失败按"不备份"处理（旧实现同）
            return
        self.tasks.submit(self.auto_backup_sync, trigger, name=f"game.autobackup.{trigger}",
                          on_error=lambda e: self.log.warning("自动备份任务失败: %s", e))

    def auto_backup_sync(self, trigger: str) -> Optional[Tuple[str, str]]:
        """真正做自动备份（工作线程）。返回 ``(世界名, 消息)``；无事可做返回 None。"""
        try:
            from services.backup_manager import BackupManager

            manager = BackupManager(self.config)
            worlds = manager._find_all_world_dirs()
            if not worlds:
                self.log.info("自动备份: 未找到存档")
                return None
            world = worlds[0]
            note_key = "backup_auto_note_launch" if trigger == "launch" else "backup_auto_note_exit"
            success, message = manager.create_backup(world["name"], _(note_key))
            if success:
                self.log.info("自动备份成功: %s", world["name"])
                self._emit("on_auto_backup", world["name"], message, True)
                return world["name"], message
            self.log.warning("自动备份失败: %s", message)
            self._emit("on_auto_backup", world["name"], message, False)
            return None
        except Exception as e:  # noqa: BLE001 - 自动备份失败不该影响启动/退出
            self.log.error("自动备份异常: %s", e, exc_info=True)
            return None

    # ─── A-26 游戏语言 ──────────────────────────────────────

    def ensure_game_language(self) -> bool:
        """把 `.minecraft/options.txt` 的游戏语言设成中文（对照表 A-26）。

        **复用旧实现**（`main.set_chinese_language`）而不是在这里重写一遍正则：
        红线 2 要求同一件事只搬一次。放在服务上的原因只有一个 —— 它属于"游戏相关的
        启动前准备"，由服务持有才不会被界面各自调用一遍；旧入口在 UI 之前调它，
        现在由装配层在建好核心之后调，对用户可见的结果相同。

        失败只记日志：options.txt 不存在/只读都不该影响启动（旧实现同）。
        """
        try:
            from main import set_chinese_language

            set_chinese_language()
            return True
        except Exception as e:  # noqa: BLE001
            self.log.warning("设置游戏语言失败（不影响启动）: %s", e)
            return False

    # ─── 内部 ───────────────────────────────────────────────

    def _resolve_launcher(self) -> Any:
        if self._launcher is None and self.attached:
            self._launcher = self.try_get("launcher")
        return self._launcher

    def _trigger_ach(self, achievement_id: str, value: int = 1, trigger_type: Optional[str] = None) -> None:
        """触发成就进度（线程安全；失败静默）。

        ``trigger_type=None`` 表示"用成就定义里的默认触发方式" —— 与旧界面
        ``ui/app_base.py:1593`` 的语义一致（那边也是 None，只有 modpack/browser
        那几个窗口自己改成了 "increment"）。懒导入路径沿用旧代码的
        ``achievement_engine``（它现在是 ``services.achievement_engine`` 的别名 shim，
        两个名字 ``is`` 同一个函数对象）。
        """
        try:
            from achievement_engine import get_achievement_engine

            engine = get_achievement_engine()
            if engine:
                engine.update_progress(achievement_id, value=value, trigger_type=trigger_type)
        except Exception as e:  # noqa: BLE001 - 成就失败绝不影响启动/退出主流程
            self.log.debug("触发成就 %s 失败: %s", achievement_id, e)

    def _read_minimize_default(self) -> bool:
        launcher = self._resolve_launcher()
        if launcher is None:
            return False
        try:
            return bool(launcher.get_minimize_on_game_launch())
        except Exception as e:  # noqa: BLE001
            self.log.warning("读取最小化开关失败: %s", e)
            return False

    def _remember_version(self, version_id: str) -> None:
        """记下最近启动成功的版本（首页要它；写盘失败不影响启动）。"""
        try:
            if getattr(self.config, "last_launched_version", None) == version_id:
                return
            self.config.last_launched_version = version_id
            save = getattr(self.config, "save_config", None)
            if callable(save):
                save()
        except Exception as e:  # noqa: BLE001
            self.log.warning("记录最近启动版本失败: %s", e)

    def _set_state(self, state: str) -> None:
        with self._lock:
            if self._state == state:
                return
            self._state = state
        self._emit("on_state", state)

    def _emit_status(self, key: str, level: str, params: Dict[str, Any]) -> None:
        self._emit("on_status", key, level, dict(params or {}))

    def _emit(self, name: str, *args: Any) -> None:
        """调观察者的可选回调（缺席或抛异常都不影响流程）。"""
        observer = self._observer
        if observer is None:
            return
        callback = getattr(observer, name, None)
        if not callable(callback):
            return
        try:
            callback(*args)
        except Exception as e:  # noqa: BLE001 - 界面回调坏了不该把服务带崩
            self.log.warning("观察者回调 %s 失败: %s", name, e)


__all__ = [
    "ALL_STATES",
    "CRASH_REPORT_LIMIT",
    "GameService",
    "KILL_EXIT_CODE",
    "POLL_INTERVAL_S",
    "STATE_CRASHED",
    "STATE_EXITED",
    "STATE_IDLE",
    "STATE_LAUNCHING",
    "STATE_RUNNING",
    "STATE_WAITING",
    "WINDOW_MARKER",
    "WINDOW_TIMEOUT_S",
]
