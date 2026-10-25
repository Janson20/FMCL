"""服务器会话状态机（阶段 3 任务 3.9 ~ 3.10；对照表 L-21 ~ L-35）。

## 为什么要有这个文件

旧界面 ui/app_server.py 把“已安装服务器 / 运行状态 / 安装进度 / 每服内存”
这些会话状态藏在 Tk 控件里：选中项是 selected_server_version，运行状态靠
server_log_status_label 的文本，安装进度来自核心的全局 on_progress。QML 页面
不能继承这些控件，也不该把这些规则用 JS 再写一份（迁移红线 2）。

本模块把这一坨状态搬进服务层，桥只负责把属性念给 QML 听：

- selected_server_version → ServerSession.selected（选中项，空串 = 没选）
- server_log_status_label → ServerSession.running（当前运行的服务器）
- _server_mem_monitor_after → _spawn_watcher（进程退出监控，恰好一次，D-84）
- _sync_server_memory_display → memory_for / set_memory（每服独立 .fmcl_server.json）
- _render_server_available_page → available_rows（正式版每页 10 条）
- _on_server_install → install（安装任务 + 进度回调）
- 控制台（3.10）：按版本隔离的日志游标 / 在线玩家 / 内存 / 命令 / 退出确认 /
  崩溃 AI 分析都在这里；桥只转发，QML 不保存任何业务状态。

## 线程约定

- install / start / remove / join 都走 TaskRunner.submit；任务的 on_done 与
  on_error 由调度器投递到主线程（app.tasks 的契约）。
- 安装进度回调是唯一从 worker 直接回调的入口：它只改受锁保护的数字，然后调
  观察者的 on_progress。桥侧把观察者事件排队后再 emit 信号（见
  app/bridges/servers_bridge.py），worker 不碰 QML。
- 进程退出监控是独立的 daemon 线程，复用 ServerConsoleWatcher —— 它保证
  on_exit 恰好一次（D-84 的修复不能因为换界面而丢掉）。

## 与旧实现刻意一致的地方

1. 只列正式版：get_server_versions 本身就是正式版过滤后的结果，这里不再筛。
2. 安装前先装同名客户端版本：这是核心 install_server 的第 1 步，自动下载
   Java runtime；本模块不重复实现。
3. 启动前同步每服内存：旧 _on_server_start 会先 _sync_server_memory_display；
   本模块的 start 直接读 .fmcl_server.json，核心 start_server 里还会再读一次
   （它有同样的优先级规则）。
4. 删除前先停止：旧 remove_server 自带这一条；本模块额外等进程真正退出
   （最多 REMOVE_STOP_TIMEOUT_S 秒），避免 Windows 上目录被占用导致删一半。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from launcher.server_config import get_server_launch_memory, set_server_launch_memory
from launcher.server_port import DEFAULT_PORT, read_server_port
from services.server_service import (
    LOG_BUFFER_MAX_LINES,
    ServerConsoleWatcher,
    apply_player_events,
    format_exit_info,
    paginate,
    running_process_memory,
)
from version_utils import parse_mc_version_from_id, parse_mod_loader_from_version

logger = logging.getLogger("services.server_session")

#: 每服最大内存下拉的取值（旧 server_memory_menu 的 values，逐字）。
MEMORY_OPTIONS: Tuple[str, ...] = ("1G", "2G", "4G", "6G", "8G", "12G", "16G")

#: 未设置时的默认值（旧 self.server_memory_var = ctk.StringVar(value="2G")）。
DEFAULT_MEMORY = "2G"

#: 可用版本每页条数（旧 self._server_page_size = 10）。
AVAILABLE_PAGE_SIZE = 10

#: 删除运行中的服务器时，等进程退出的上限。
REMOVE_STOP_TIMEOUT_S = 10.0

#: 列表状态（桥直接映射到 FmPage.contentState）。
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_EMPTY = "empty"
STATE_ERROR = "error"

#: 控制台运行状态（与旧界面三态逐字对齐：未运行 / 运行中 / 已停止）。
CONSOLE_STATE_RUNNING = "running"
CONSOLE_STATE_STOPPED = "stopped"
CONSOLE_STATE_IDLE = "not_running"

#: 旧实现把在线玩家上限硬编码成 20（self._server_max_players = 20）。
#: 3.10 保持同口径，不读 server.properties 的 max-players —— 那是 3.11 配置编辑器的事。
CONSOLE_MAX_PLAYERS = 20

#: 崩溃 AI 分析任务名（Tasks 面板与取消操作按名字找）。
TASK_CRASH = "server.crash"

#: 忙的种类（界面据此禁用按钮、显示不同进度条）。
BUSY_INSTALLING = "installing"
BUSY_STARTING = "starting"
BUSY_STOPPING = "stopping"
BUSY_REMOVING = "removing"
BUSY_JOINING = "joining"

#: 安装结果状态。
INSTALL_IDLE = "idle"
INSTALL_RUNNING = "installing"
INSTALL_DONE = "done"
INSTALL_FAILED = "failed"

#: 从核心启动器取的服务器回调清单。集中一份，少一个键就是“某个动作静默不可用”。
SERVER_CALLBACK_NAMES: Tuple[str, ...] = (
    "get_server_versions",
    "get_installed_servers",
    "install_server",
    "start_server",
    "stop_server",
    "is_server_running",
    "get_server_process",
    "remove_server",
    "get_server_dir",
    "send_server_command",
    "get_jdz_token",
    "get_installed_version_ids",
    "install_version",
    "launch_game",
)

#: 任务名（Tasks 面板与取消操作按名字找）。
TASK_INSTALL = "server.install"
TASK_START = "server.start"
TASK_REMOVE = "server.remove"
TASK_JOIN = "server.join"


def launcher_callbacks(launcher: Any) -> Dict[str, Callable[..., Any]]:
    """从核心启动器实例取服务器相关回调；取不到的键不放进字典。"""
    out: Dict[str, Callable[..., Any]] = {}
    if launcher is None:
        return out
    for name in SERVER_CALLBACK_NAMES:
        fn = getattr(launcher, name, None)
        if callable(fn):
            out[name] = fn
    return out


def loader_of(version_id: str) -> str:
    """服务器目录名 → 加载器名（空串 = 原版）。"""
    try:
        return str(parse_mod_loader_from_version(str(version_id or "")) or "")
    except Exception:  # noqa: BLE001 - 纯展示，解析失败按原版处理
        return ""


def game_version_of(version_id: str) -> str:
    """服务器目录名 → Minecraft 版本（解析不出时返回空串）。"""
    try:
        return str(parse_mc_version_from_id(str(version_id or "")) or "")
    except Exception:  # noqa: BLE001 - 纯展示
        return ""


class ServerSession:
    """服务器页的全部会话状态（不含任何 UI 引用）。"""

    def __init__(
        self,
        *,
        launcher: Any = None,
        tasks: Any = None,
        game: Any = None,
        achievement: Any = None,
        callbacks: Optional[Dict[str, Callable[..., Any]]] = None,
        observer: Any = None,
    ) -> None:
        self._launcher = launcher
        self._tasks = tasks
        self._game = game
        self._achievement = achievement
        self._callbacks: Dict[str, Callable[..., Any]] = launcher_callbacks(launcher)
        if callbacks:
            self._callbacks.update(callbacks)
        self._observer = observer
        self._lock = threading.RLock()

        self._installed: List[str] = []
        self._list_state = STATE_LOADING
        self._list_error = ""
        self._available: List[Dict[str, Any]] = []
        self._available_state = "idle"
        self._available_error = ""
        self._available_page = 1

        self._selected = ""
        #: 同时运行的服务器：version -> Popen。允许一台以上（3.9 验收反馈）。
        self._running: Dict[str, Any] = {}
        self._running_order: List[str] = []
        self._last_exit_code: Optional[int] = None
        self._last_exit_codes: Dict[str, int] = {}
        self._watch_threads: Dict[str, threading.Thread] = {}
        self._log_lines: Dict[str, List[str]] = {}
        #: 正在执行用户 stop 的版本：包装进程被 pause 卡住时按“正常停止”收尾。
        self._stopping: set = set()

        #: 控制台（3.10）：全部按 version 隔离，绝不读“最近启动”的全局状态。
        self._console_started: set = set()
        self._console_seqs: Dict[str, int] = {}
        self._console_cleared: Dict[str, int] = {}
        self._console_players: Dict[str, List[str]] = {}
        #: 退出后等待界面询问“是否正常运行”的退出码（一版本一条）。
        self._exit_prompts: Dict[str, int] = {}

        self._busy = ""
        self._install_state = INSTALL_IDLE
        self._install_version = ""
        self._install_error = ""
        self._progress = 0.0
        self._progress_current = 0
        self._progress_total = 0
        self._progress_determinate = False
        self._progress_phase = ""

        self._memory_cache: Dict[str, str] = {}
        self._available_seq = 0
        self._installed_seq = 0
        self._closing = False

    # ─── 观察者 ─────────────────────────────────────────────

    def set_observer(self, observer: Any) -> None:
        with self._lock:
            self._observer = observer

    def _emit(self, name: str, *args: Any) -> None:
        observer = self._observer
        if observer is None:
            return
        fn = getattr(observer, name, None)
        if not callable(fn):
            return
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001 - 观察者坏了不能拖垮任务
            logger.warning("服务器观察者 %s 失败: %s", name, e)

    def _status(self, key: str, level: str = "info", **params: Any) -> None:
        self._emit("on_status", key, level, dict(params))

    def _notice(self, key: str, level: str = "info", **params: Any) -> None:
        self._emit("on_notice", key, level, dict(params))

    def _changed(self, section: str) -> None:
        self._emit("on_changed", section)

    # ─── 只读状态 ───────────────────────────────────────────

    @property
    def list_state(self) -> str:
        with self._lock:
            return self._list_state

    @property
    def list_error(self) -> str:
        with self._lock:
            return self._list_error

    @property
    def available_state(self) -> str:
        with self._lock:
            return self._available_state

    @property
    def available_error(self) -> str:
        with self._lock:
            return self._available_error

    @property
    def available_page(self) -> int:
        with self._lock:
            return self._available_page

    @property
    def available_total(self) -> int:
        with self._lock:
            return len(self._available)

    @property
    def available_page_count(self) -> int:
        with self._lock:
            return max(1, (len(self._available) + AVAILABLE_PAGE_SIZE - 1) // AVAILABLE_PAGE_SIZE)

    @property
    def available_rows(self) -> List[Dict[str, Any]]:
        with self._lock:
            rows, page, _total = paginate(self._available, self._available_page, AVAILABLE_PAGE_SIZE)
            self._available_page = page
        return [self._decorate_version(row) for row in rows]

    @property
    def installed(self) -> List[str]:
        with self._lock:
            return list(self._installed)

    @property
    def installed_rows(self) -> List[Dict[str, Any]]:
        with self._lock:
            installed = list(self._installed)
            running = set(self._running)
        try:
            root = self._server_dir("", ensure=False)
        except Exception:  # noqa: BLE001 - 列目录拿不到时端口按默认值展示
            root = None
        rows: List[Dict[str, Any]] = []
        for version in installed:
            port = read_server_port(root / version) if root is not None else DEFAULT_PORT
            rows.append(
                {
                    "id": version,
                    "loader": loader_of(version),
                    "gameVersion": game_version_of(version),
                    "running": version in running,
                    "port": port,
                }
            )
        return rows

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._installed)

    @property
    def selected(self) -> str:
        with self._lock:
            return self._selected

    @property
    def has_selection(self) -> bool:
        with self._lock:
            return bool(self._selected)

    @property
    def running(self) -> str:
        """最近启动且仍在运行的那台（兼容旧桥的 runningVersion）。"""
        with self._lock:
            for version in reversed(self._running_order):
                if version in self._running:
                    return version
            return ""

    @property
    def running_versions(self) -> List[str]:
        with self._lock:
            return [version for version in self._running_order if version in self._running]

    @property
    def is_running(self) -> bool:
        with self._lock:
            return bool(self._running)

    @property
    def last_exit_code(self) -> Optional[int]:
        with self._lock:
            return self._last_exit_code

    @property
    def busy(self) -> str:
        with self._lock:
            return self._busy

    @property
    def install_state(self) -> str:
        with self._lock:
            return self._install_state

    @property
    def install_version(self) -> str:
        with self._lock:
            return self._install_version

    @property
    def install_error(self) -> str:
        with self._lock:
            return self._install_error

    @property
    def progress(self) -> float:
        with self._lock:
            return self._progress

    @property
    def progress_current(self) -> int:
        with self._lock:
            return self._progress_current

    @property
    def progress_total(self) -> int:
        with self._lock:
            return self._progress_total

    @property
    def progress_determinate(self) -> bool:
        with self._lock:
            return self._progress_determinate

    @property
    def selected_info(self) -> Dict[str, Any]:
        with self._lock:
            version = self._selected
            running = version in self._running
            installed = list(self._installed)
        if not version:
            return {}
        memory = self.memory_for(version)
        port = self._port_for(version)
        path = ""
        try:
            path = str(self._server_dir(version, ensure=False))
        except Exception as e:  # noqa: BLE001 - 路径只是展示
            logger.warning("取服务器目录失败 (%s): %s", version, e)
        return {
            "id": version,
            "loader": loader_of(version),
            "gameVersion": game_version_of(version),
            "memory": memory,
            "port": port,
            "running": running,
            "installed": version in installed,
            "path": path,
        }

    @property
    def memory_options(self) -> List[str]:
        return list(MEMORY_OPTIONS)

    # ─── 列表加载 ───────────────────────────────────────────

    def load_installed(self, *, select: str = "", quiet: bool = False) -> None:
        """加载已安装服务器；quiet 用于安装成功后的静默补齐（不闪加载态）。"""
        if self._closing:
            return
        fn = self._callbacks.get("get_installed_servers")
        if not callable(fn):
            with self._lock:
                self._list_state = STATE_ERROR
                self._list_error = "get_installed_servers 回调未注册"
            self._changed("installed")
            return
        self._installed_seq += 1
        seq = self._installed_seq
        if not quiet:
            with self._lock:
                self._list_state = STATE_LOADING
                self._list_error = ""
            self._changed("installed")

        def work() -> Any:
            return fn() or []

        def done(result: Any) -> None:
            if seq != self._installed_seq or self._closing:
                return
            self._apply_installed(result, select=select)

        def failed(error: Any) -> None:
            if seq != self._installed_seq or self._closing:
                return
            with self._lock:
                self._list_state = STATE_ERROR
                self._list_error = str(error)
            self._changed("installed")

        self._submit(work, name="server.installed", on_done=done, on_error=failed)

    def _apply_installed(self, rows: Any, *, select: str = "") -> None:
        versions = sorted({str(item) for item in (rows or []) if str(item).strip()})
        with self._lock:
            self._installed = versions
            self._list_state = STATE_READY
            self._list_error = ""
            if select:
                self._selected = str(select)
            elif self._selected and self._selected not in versions:
                self._selected = ""
        self._changed("installed")
        if select:
            self._changed("selection")

    def load_available(self) -> None:
        """加载正式版服务器清单（核心已过滤；每页 10 条只在本地切片）。"""
        if self._closing:
            return
        fn = self._callbacks.get("get_server_versions")
        if not callable(fn):
            with self._lock:
                self._available_state = STATE_ERROR
                self._available_error = "get_server_versions 回调未注册"
            self._changed("available")
            return
        self._available_seq += 1
        seq = self._available_seq
        with self._lock:
            self._available_state = STATE_LOADING
            self._available_error = ""
        self._changed("available")

        def work() -> Any:
            return fn() or []

        def done(result: Any) -> None:
            if seq != self._available_seq or self._closing:
                return
            rows = [dict(item) for item in (result or []) if isinstance(item, dict) and item.get("id")]
            with self._lock:
                self._available = rows
                self._available_page = 1
                self._available_state = STATE_READY if rows else STATE_EMPTY
                self._available_error = ""
            self._changed("available")

        def failed(error: Any) -> None:
            if seq != self._available_seq or self._closing:
                return
            with self._lock:
                self._available_state = STATE_ERROR
                self._available_error = str(error)
            self._changed("available")

        self._submit(work, name="server.available", on_done=done, on_error=failed)

    def set_available_page(self, page: int) -> None:
        with self._lock:
            _rows, current, _total = paginate(self._available, int(page or 1), AVAILABLE_PAGE_SIZE)
            self._available_page = current
        self._changed("available")

    def _decorate_version(self, row: Dict[str, Any]) -> Dict[str, Any]:
        version = str(row.get("id", "") or "")
        return {
            "id": version,
            "type": str(row.get("type", "") or ""),
            "loader": loader_of(version),
            "gameVersion": game_version_of(version),
        }

    # ─── 选中与每服配置 ─────────────────────────────────────

    def select(self, version: str, *, announce: bool = True) -> None:
        """选中一台服务器（空串 = 取消选中）。只记状态，不做启动前置校验。"""
        value = str(version or "").strip()
        with self._lock:
            changed = value != self._selected
            self._selected = value
        if value:
            self.memory_for(value)
        if changed:
            self._changed("selection")
        if value and announce:
            self._status("server_version_selected", "info", version=value)

    def _server_dir(self, version: str = "", *, ensure: bool = False) -> Path:
        """服务器根目录或某一台服务器目录。"""
        fn = self._callbacks.get("get_server_dir")
        if not callable(fn):
            raise RuntimeError("get_server_dir 回调未注册")
        root = Path(str(fn()))
        path = root / str(version) if str(version) else root
        if ensure:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def directory_for(self, version: str = "") -> str:
        """给桥的“打开目录”用：返回绝对路径，目录不存在就建出来。"""
        return str(self._server_dir(str(version or ""), ensure=True))

    def _port_for(self, version: str) -> int:
        """读取某台服务器当前配置的端口（失败按 25565）。"""
        try:
            return read_server_port(self._server_dir(str(version or ""), ensure=False))
        except Exception as e:  # noqa: BLE001 - 端口只是展示/连接参数
            logger.warning("读取服务器端口失败 (%s): %s", version, e)
            return DEFAULT_PORT

    def memory_for(self, version: str = "") -> str:
        """读取某台服务器的独立内存设置（未设置/读失败时回落默认 2G）。"""
        target = str(version or self.selected or "").strip()
        if not target:
            return DEFAULT_MEMORY
        with self._lock:
            cached = self._memory_cache.get(target)
        if cached:
            return cached
        value = DEFAULT_MEMORY
        try:
            stored = get_server_launch_memory(self._server_dir(target, ensure=False))
            if stored:
                value = str(stored)
        except Exception as e:  # noqa: BLE001 - 读不到就用默认值（旧实现同样吞掉）
            logger.warning("读取服务器内存设置失败 (%s): %s", target, e)
        with self._lock:
            self._memory_cache[target] = value
        return value

    def refresh_memory(self, version: str = "") -> str:
        """外部改了 .fmcl_server.json 后让缓存失效并重读（3.11 配置编辑器保存后调用）。"""
        target = str(version or self.selected or "").strip()
        if not target:
            return DEFAULT_MEMORY
        with self._lock:
            self._memory_cache.pop(target, None)
        return self.memory_for(target)

    def set_memory(self, memory: str, version: str = "") -> bool:
        """写入某台服务器的独立内存设置，并更新界面快照。"""
        target = str(version or self.selected or "").strip()
        value = str(memory or "").strip()
        if not target:
            self._status("server_no_version_selected", "error")
            return False
        if value not in MEMORY_OPTIONS:
            self._notice("server_config_save_failed", "error", error="invalid_memory")
            return False
        try:
            success, error = set_server_launch_memory(self._server_dir(target, ensure=False), value)
        except Exception as e:  # noqa: BLE001 - 统一按失败提示
            success, error = False, str(e)
        if success:
            with self._lock:
                self._memory_cache[target] = value
            self._status("server_memory_saved", "info", version=target, memory=value)
            self._changed("selection")
            return True
        logger.warning("保存服务器内存设置失败 (%s): %s", target, error)
        self._notice("server_config_save_failed", "error", error=str(error))
        return False

    # ─── 控制台（3.10）────────────────────────────────────────

    @property
    def console_max_lines(self) -> int:
        """控制台缓冲上限（与 ServerConsoleWatcher 是同一个常量）。"""
        return LOG_BUFFER_MAX_LINES

    def console_state(self, version: str = "") -> str:
        """某台服务器当前的运行状态：running / stopped / not_running。

        “已停止”与“未运行”的区别沿用旧界面：本会话启动过、且现在不在跑 = 已停止；
        从未启动过 = 未运行。状态只按传进来的版本判断，多服务器互不影响。
        """
        target = str(version or self.selected or "").strip()
        with self._lock:
            if target in self._running:
                return CONSOLE_STATE_RUNNING
            if target in self._console_started or target in self._last_exit_codes:
                return CONSOLE_STATE_STOPPED
        return CONSOLE_STATE_IDLE

    def console_snapshot(self, version: str = "") -> Dict[str, Any]:
        """控制台日志快照：只返回清屏点之后的行，并带绝对序号游标。

        游标是“这台服务器至今产生过多少行”（绝对序号），不是“缓冲里有多少行”：
        ServerConsoleWatcher 会裁掉最老的行，用长度当游标会在裁剪后错位。
        """
        target = str(version or self.selected or "").strip()
        if not target:
            return {"version": "", "lines": [], "seq": 0, "cleared": 0, "capacity": LOG_BUFFER_MAX_LINES}
        with self._lock:
            lines = list(self._log_lines.get(target, []))
            total = int(self._console_seqs.get(target, 0))
            cleared = int(self._console_cleared.get(target, 0))
        base = max(0, total - len(lines))
        first = max(cleared, base)
        start = max(0, first - base)
        return {
            "version": target,
            "lines": lines[start:],
            "seq": total,
            "cleared": cleared,
            "capacity": LOG_BUFFER_MAX_LINES,
        }

    def clear_console(self, version: str = "") -> int:
        """清屏：只移动显示游标，**不动日志缓冲**（崩溃分析仍要拿到清屏前的现场）。"""
        target = str(version or self.selected or "").strip()
        if not target:
            return 0
        with self._lock:
            total = int(self._console_seqs.get(target, 0))
            self._console_cleared[target] = total
        return total

    def console_info(self, version: str = "") -> Dict[str, Any]:
        """控制台状态快照（运行态 / 玩家 / 内存 / 退出码 / 行数）。"""
        target = str(version or self.selected or "").strip()
        if not target:
            return {}
        with self._lock:
            proc = self._running.get(target)
            is_running = proc is not None
            started = target in self._console_started
            exit_code = self._last_exit_codes.get(target)
            pending_exit = target in self._exit_prompts
            players = list(self._console_players.get(target, []))
            total = int(self._console_seqs.get(target, 0))
            cleared = int(self._console_cleared.get(target, 0))
            buffer_len = len(self._log_lines.get(target, []))
        base = max(0, total - buffer_len)
        visible = max(0, total - max(cleared, base))
        memory_mb: Optional[int] = None
        if is_running and proc is not None:
            # D-81 / D-80：psutil 实现，微秒级，不跑 tasklist；异常按“没采到”处理。
            try:
                memory_mb = running_process_memory(proc)
            except Exception as e:  # noqa: BLE001 - 进程刚退出时 poll/pid 可能抛
                logger.debug("采样服务器 %s 内存失败: %s", target, e)
        if is_running:
            state = CONSOLE_STATE_RUNNING
        elif started or exit_code is not None:
            state = CONSOLE_STATE_STOPPED
        else:
            state = CONSOLE_STATE_IDLE
        return {
            "version": target,
            "state": state,
            "running": is_running,
            "started": started,
            "players": players,
            "playerCount": len(players),
            "maxPlayers": CONSOLE_MAX_PLAYERS,
            "memoryMb": int(memory_mb) if memory_mb is not None else -1,
            "exitCode": int(exit_code) if exit_code is not None else None,
            "exitInfo": format_exit_info(int(exit_code)) if exit_code is not None else "",
            "pendingExit": pending_exit,
            "seq": total,
            "lineCount": visible,
            "capacity": LOG_BUFFER_MAX_LINES,
        }

    def take_exit_prompt(self, version: str = "") -> Optional[int]:
        """取走某台服务器待确认的退出码（没有则返回 None）。

        退出确认只问一次：界面取走之后不再重复弹。**主动停止**（停止按钮、删除
        运行中的服务器、退出启动器）不留下待确认项 —— 用户已经知道这次退出，
        不该再被问“服务器是否正常运行”；只有非主动退出才需要确认。
        """
        target = str(version or self.selected or "").strip()
        with self._lock:
            return self._exit_prompts.pop(target, None)

    def send_command(self, version: str, command: str) -> Tuple[bool, str]:
        """向指定版本发送控制台命令；成功后触发 server_admin 成就。

        Returns:
            (是否成功, 失败原因键)。原因键由桥翻译，服务层不碰 i18n。
        """
        target = str(version or self.selected or "").strip()
        cmd = str(command or "").strip()
        if not target:
            self._status("server_no_version_selected", "error")
            return False, "server_no_version_selected"
        if not cmd:
            return False, ""
        fn = self._callbacks.get("send_server_command")
        if not callable(fn):
            self._status("server_cmd_error_no_callback", "error")
            return False, "server_cmd_error_no_callback"
        try:
            ok = bool(fn(cmd, target))
        except Exception as e:  # noqa: BLE001 - 发送失败统一按“未运行”提示
            logger.warning("向服务器 %s 发送命令失败: %s", target, e)
            ok = False
        if ok:
            self._trigger_achievement("server_admin")
            return True, ""
        self._status("server_cmd_error_not_running", "error")
        return False, "server_cmd_error_not_running"

    def export_console(self, version: str, path: str) -> Tuple[bool, str]:
        """把控制台当前可见日志写到 path（UTF-8，LF 换行）。

        Returns:
            (是否成功, 路径或错误信息)。
        """
        target = str(version or self.selected or "").strip()
        if not target:
            return False, "server_no_version_selected"
        dest = str(path or "").strip()
        if not dest:
            return False, "empty_path"
        snapshot = self.console_snapshot(target)
        lines = list(snapshot.get("lines") or [])
        if not lines:
            return False, "empty_log"
        try:
            target_path = Path(dest).expanduser()
            target_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        except Exception as e:  # noqa: BLE001 - 路径不可写统一按失败返回
            logger.warning("导出服务器 %s 控制台日志失败: %s", target, e)
            return False, str(e)
        return True, str(target_path)

    def analyze_crash(self, version: str, exit_code: int) -> bool:
        """对某台服务器的退出做 AI 分析（复用 services.crash_service 的既有能力）。"""
        target = str(version or self.selected or "").strip()
        if not target:
            return False
        getter = self._callbacks.get("get_jdz_token")
        token = ""
        if callable(getter):
            try:
                token = str(getter() or "").strip()
            except Exception as e:  # noqa: BLE001 - 取不到 token 按未登录处理
                logger.warning("读取净读 Token 失败: %s", e)
        if not token:
            self._notice("ai_search_login_required", "warning")
            return False
        try:
            code = int(exit_code)
        except (TypeError, ValueError):
            code = -1
        with self._lock:
            lines = list(self._log_lines.get(target, []))

        def work() -> Any:
            from services.crash_service import analyze_server_crash, collect_server_ai_context

            context = collect_server_ai_context(code, version_id=target, server_log_lines=lines)
            return analyze_server_crash(context, code, token)

        def done(result: Any) -> None:
            self._emit("on_crash_analysis", target, str(result or ""))

        def failed(error: Any) -> None:
            logger.warning("分析服务器 %s 崩溃失败: %s", target, error)
            self._emit("on_crash_failed", target, str(error))

        self._submit(work, name=TASK_CRASH, on_done=done, on_error=failed)
        return True

    # ─── 安装 ───────────────────────────────────────────────

    def install(self, version: str) -> None:
        """安装一台服务器：核心 install_server 的进度回调映射到 0~100%。"""
        target = str(version or "").strip()
        if not target:
            self._status("server_enter_version", "error")
            return
        with self._lock:
            if self._busy:
                return
            self._busy = BUSY_INSTALLING
            self._install_state = INSTALL_RUNNING
            self._install_version = target
            self._install_error = ""
            self._progress = 0.0
            self._progress_current = 0
            self._progress_total = 0
            self._progress_determinate = False
            self._progress_phase = ""
        self._changed("install")
        self._status("server_install_loading", "loading", version_id=target)

        fn = self._callbacks.get("install_server")
        if not callable(fn):
            self._finish_install(False, "install_server 回调未注册", target)
            return

        def progress(phase: str, current: Any, total: Any) -> None:
            self._apply_install_progress(phase, current, total)

        def work() -> Any:
            return fn(target, progress_callback=progress)

        def done(result: Any) -> None:
            ok = bool(result[0]) if isinstance(result, (tuple, list)) and result else bool(result)
            detail = str(result[1]) if isinstance(result, (tuple, list)) and len(result) > 1 else ""
            self._finish_install(ok, detail, target)

        def failed(error: Any) -> None:
            logger.warning("安装服务器 %s 失败: %s", target, error)
            self._finish_install(False, str(error), target)

        self._submit(work, name=TASK_INSTALL, on_done=done, on_error=failed)

    def _apply_install_progress(self, phase: Any, current: Any, total: Any) -> None:
        try:
            cur = max(0, int(current))
            tot = max(0, int(total))
        except (TypeError, ValueError):
            return
        if tot <= 0 and cur <= 0:
            return
        name = str(phase or "")
        with self._lock:
            if tot > 0:
                ratio = max(0.0, min(1.0, cur / float(tot)))
                value = 0.85 + 0.15 * ratio if name == "server" else 0.85 * ratio
                value = min(0.99, value)
                # 进度只增不减：客户端阶段可能有多批下载，max 避免百分比回跳。
                self._progress = max(self._progress, value)
                self._progress_determinate = True
            else:
                self._progress_determinate = False
            self._progress_current = cur
            self._progress_total = tot
            self._progress_phase = name
            snapshot = (
                self._progress,
                self._progress_current,
                self._progress_total,
                self._progress_determinate,
            )
        self._emit("on_progress", *snapshot)

    def _finish_install(self, ok: bool, detail: str, version: str) -> None:
        with self._lock:
            self._busy = ""
            self._install_state = INSTALL_DONE if ok else INSTALL_FAILED
            self._install_error = "" if ok else str(detail)
            self._progress = 1.0 if ok else self._progress
            if ok:
                if version not in self._installed:
                    self._installed = sorted([*self._installed, version])
                self._selected = version
                self._list_state = STATE_READY
                self._list_error = ""
        self._changed("install")
        if ok:
            self._changed("installed")
            self._changed("selection")
            self._status("server_installed", "success", version=version)
            self._emit("on_install_finished", True, version, "")
            # 静默补齐：核心也可能在安装过程中写入同一目录，列表以磁盘为准。
            self.load_installed(select=version, quiet=True)
        else:
            self._status("server_install_failed", "error", version=version)
            self._emit("on_install_finished", False, version, str(detail))

    # ─── 启动 / 停止 ────────────────────────────────────────

    def start(self, version: str = "") -> bool:
        """启动一台服务器（非阻塞）。返回是否成功提交。"""
        target = str(version or self.selected or "").strip()
        if not target:
            self._status("server_no_version_selected", "error")
            return False
        with self._lock:
            if self._busy:
                return False
            if target in self._running:
                already = True
            else:
                already = False
                self._busy = BUSY_STARTING
        if already:
            self._status("server_already_running", "warning")
            return False
        fn = self._callbacks.get("start_server")
        if not callable(fn):
            with self._lock:
                self._busy = ""
            self._status("server_start_failed", "error", version=target)
            return False
        self._changed("run")
        self._status("server_starting", "loading", version=target)
        port_before = self._port_for(target)

        def work() -> Any:
            memory = self.memory_for(target) or DEFAULT_MEMORY
            return fn(target, memory)

        def done(result: Any) -> None:
            ok = bool(result[0]) if isinstance(result, (tuple, list)) and result else bool(result)
            proc = result[1] if isinstance(result, (tuple, list)) and len(result) > 1 else None
            self._finish_start(ok, proc, target)
            if ok and proc is not None:
                self._notify_port(target, port_before)

        def failed(error: Any) -> None:
            logger.warning("启动服务器 %s 失败: %s", target, error)
            with self._lock:
                self._busy = ""
            self._status("server_start_failed", "error", version=target)
            self._changed("run")

        self._submit(work, name=TASK_START, on_done=done, on_error=failed)
        return True

    def _finish_start(self, ok: bool, proc: Any, version: str) -> None:
        with self._lock:
            self._busy = ""
            if ok and proc is not None:
                self._running[version] = proc
                if version not in self._running_order:
                    self._running_order.append(version)
                self._last_exit_codes.pop(version, None)
                self._last_exit_code = None
                self._log_lines[version] = []
                self._console_started.add(version)
                self._console_seqs[version] = 0
                self._console_cleared[version] = 0
                self._console_players[version] = []
                self._exit_prompts.pop(version, None)
        if not ok or proc is None:
            self._status("server_start_failed", "error", version=version)
            self._changed("run")
            return
        self._status("server_started", "success", version=version)
        self._changed("run")
        self._emit("on_started", version)
        if proc is not None:
            self._spawn_watcher(version, proc)

    def _notify_port(self, version: str, port_before: int) -> None:
        """端口被自动顺延或本来就不是 25565 时，明确告诉用户该连哪个端口。"""
        port_after = self._port_for(version)
        if port_after != port_before:
            self._notice("server_port_auto", "warning", **{"from": port_before, "to": port_after})
        elif port_after != DEFAULT_PORT:
            self._notice("server_port_not_default", "warning", port=port_after)

    def _console_line(self, version: str, line: str) -> None:
        """一行服务器日志：推进绝对序号、解析玩家进出，再通知观察者。"""
        text = str(line or "")
        with self._lock:
            seq = int(self._console_seqs.get(version, 0)) + 1
            self._console_seqs[version] = seq
            players = self._console_players.setdefault(version, [])
            actions = apply_player_events(players, text)
            snapshot = list(players)
        self._emit("on_log", version, text, seq)
        if actions:
            self._emit("on_players", version, snapshot)

    def _spawn_watcher(self, version: str, proc: Any) -> None:
        """读 stdout 并等退出；无论走哪条路径都恰好投递一次退出（D-84）。"""
        def run() -> None:
            try:
                if getattr(proc, "stdout", None) is None:
                    while not self._closing and getattr(proc, "poll", lambda: 0)() is None:
                        time.sleep(0.5)
                    code = getattr(proc, "returncode", None)
                    self._handle_exit(version, code if isinstance(code, int) else -1)
                    return
                watcher = ServerConsoleWatcher(self._log_lines.setdefault(version, []))
                watcher.watch(
                    lambda: proc,
                    on_log=lambda line: self._console_line(version, line),
                    on_exit=lambda code: self._handle_exit(version, int(code)),
                )
            except Exception as e:  # noqa: BLE001 - 监控线程不许静默死掉
                logger.error("监控服务器 %s 退出失败: %s", version, e, exc_info=True)
                self._handle_exit(version, -1)

        thread = threading.Thread(target=run, name=f"server-watch-{version}", daemon=True)
        with self._lock:
            self._watch_threads[version] = thread
        thread.start()
        if getattr(proc, "_fmcl_wrapper", False):
            threading.Thread(
                target=self._monitor_wrapper,
                args=(version, proc),
                name=f"server-wrapper-watch-{version}",
                daemon=True,
            ).start()

    def _monitor_wrapper(self, version: str, proc: Any) -> None:
        """监视 run.bat/run.sh 的 java 子进程，避免 pause 把退出检测挂住。

        包装进程（cmd.exe / sh）自己不会在 java 退出后立刻结束：run.bat 末尾的
        pause 会一直等按键。只读 stdout 的话 UI 会永远显示“运行中”。这里用 psutil
        看包装进程的子进程：一旦看到过 java、随后子进程消失，就结束包装进程并收尾。
        """
        try:
            import psutil
        except ImportError:  # pragma: no cover - 项目硬依赖 psutil，仅兜底
            return
        seen_child = False
        quiet = 0
        started = time.monotonic()
        while not self._closing:
            with self._lock:
                if version not in self._running:
                    return
            try:
                if proc.poll() is not None:
                    return
                children = [
                    child
                    for child in psutil.Process(proc.pid).children(recursive=True)
                    # Windows 隐藏控制台会给 cmd.exe 挂一个 conhost.exe，它不是服务器；
                    # 不滤掉的话“子进程消失”永远不成立（实测）。其他平台没有它。
                    if child.name().lower() not in ("conhost.exe", "conhost", "openconsole.exe")
                ]
            except Exception:  # noqa: BLE001 - 进程已经消失就交给 stdout 监控
                return
            if children:
                seen_child = True
                quiet = 0
            elif seen_child:
                quiet += 1
                if quiet >= 2:
                    self._finish_wrapper(version, proc)
                    return
            elif time.monotonic() - started > 8.0:
                # 包装进程一直没拉起 java（脚本坏了/缺 Java），不能让它永久挂着。
                self._finish_wrapper(version, proc)
                return
            time.sleep(0.5)

    def _finish_wrapper(self, version: str, proc: Any) -> None:
        with self._lock:
            stopped_by_user = version in self._stopping
        logger.info(f"服务器 {version} 的 java 子进程已退出，结束包装进程")
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass
        self._handle_exit(version, 0 if stopped_by_user else -1)

    def _handle_exit(self, version: str, exit_code: int) -> None:
        with self._lock:
            if version not in self._running:
                return
            self._running.pop(version, None)
            if version in self._running_order:
                self._running_order.remove(version)
            self._watch_threads.pop(version, None)
            stop_requested = version in self._stopping
            self._stopping.discard(version)
            self._last_exit_codes[version] = int(exit_code)
            self._last_exit_code = int(exit_code)
            self._console_players.get(version, []).clear()
            # 退出质量确认只问一次；主动停止不登记（见 take_exit_prompt）。
            # 非主动退出（崩溃、进程被外部结束、服务器自己退出）才留给壳层询问。
            if not stop_requested:
                self._exit_prompts[version] = int(exit_code)
            if self._busy == BUSY_STOPPING:
                self._busy = ""
        self._changed("run")
        if exit_code == 0:
            self._status("server_stopped", "info")
        else:
            self._status("server_crashed", "error", code=int(exit_code))
        self._emit("on_exit", version, int(exit_code))

    def stop(self, version: str = "") -> bool:
        """停止指定服务器（空串 = 当前选中）；状态复位由该台的退出监控完成。"""
        target = str(version or self.selected or "").strip()
        with self._lock:
            if self._busy:
                return False
            if not target or target not in self._running:
                running = False
            else:
                running = True
                self._busy = BUSY_STOPPING
        if not running:
            self._status("server_no_running_server", "info")
            return False
        fn = self._callbacks.get("stop_server")
        if not callable(fn):
            with self._lock:
                self._busy = ""
            self._status("server_no_running_server", "info")
            return False
        self._status("server_stopping", "warning")
        self._changed("run")
        with self._lock:
            self._stopping.add(target)
        try:
            ok = bool(fn(target))
        except Exception as e:  # noqa: BLE001 - 停止失败按“没有在跑”复位
            logger.warning("停止服务器 %s 失败: %s", target, e)
            ok = False
        if not ok:
            with self._lock:
                self._busy = ""
                self._stopping.discard(target)
            self._status("server_no_running_server", "info")
            self._changed("run")
        return ok

    # ─── 删除 / 加入 / 收尾 ─────────────────────────────────

    def remove(self, version: str = "") -> bool:
        """删除一台服务器；运行中先停再等进程退出，避免目录被占用。"""
        target = str(version or self.selected or "").strip()
        if not target:
            self._status("server_no_version_selected", "error")
            return False
        with self._lock:
            if self._busy:
                return False
            self._busy = BUSY_REMOVING
        fn = self._callbacks.get("remove_server")
        if not callable(fn):
            with self._lock:
                self._busy = ""
            self._notice("server_remove_error", "error", error="remove_server 回调未注册")
            return False
        self._status("server_deleting", "loading", version=target)
        self._changed("install")

        def work() -> Any:
            with self._lock:
                running = target in self._running
            if running:
                with self._lock:
                    self._stopping.add(target)
                stop_fn = self._callbacks.get("stop_server")
                if callable(stop_fn):
                    try:
                        stop_fn(target)
                    except Exception as e:  # noqa: BLE001 - 下面统一等超时
                        logger.warning("删除前停止服务器失败: %s", e)
                deadline = time.monotonic() + REMOVE_STOP_TIMEOUT_S
                while time.monotonic() < deadline:
                    if not self._process_running(target):
                        break
                    time.sleep(0.2)
            return fn(target)

        def done(result: Any) -> None:
            ok = bool(result[0]) if isinstance(result, (tuple, list)) and result else bool(result)
            detail = str(result[1]) if isinstance(result, (tuple, list)) and len(result) > 1 else ""
            with self._lock:
                self._busy = ""
                if ok:
                    self._installed = [item for item in self._installed if item != target]
                    if self._selected == target:
                        self._selected = ""
            self._changed("install")
            if ok:
                self._status("version_deleted", "success", version=target)
                self._changed("installed")
                self._changed("selection")
                self._emit("on_removed", target)
            else:
                self._status("version_delete_failed", "error", version=target)
                self._notice("server_remove_error", "error", error=detail)

        def failed(error: Any) -> None:
            logger.warning("删除服务器 %s 失败: %s", target, error)
            with self._lock:
                self._busy = ""
            self._changed("install")
            self._notice("server_remove_error", "error", error=str(error))

        self._submit(work, name=TASK_REMOVE, on_done=done, on_error=failed)
        return True

    def _process_running(self, version: str = "") -> bool:
        target = str(version or self.selected or "").strip()
        with self._lock:
            proc = self._running.get(target) if target else None
            any_running = bool(self._running)
        if proc is not None:
            try:
                return proc.poll() is None
            except Exception:  # noqa: BLE001
                return False
        fn = self._callbacks.get("is_server_running")
        if callable(fn):
            try:
                return bool(fn(target)) if target else bool(fn())
            except TypeError:
                try:
                    return bool(fn())
                except Exception:  # noqa: BLE001
                    return any_running
            except Exception:  # noqa: BLE001
                return any_running
        return any_running

    def join(self, version: str = "") -> bool:
        """加入服务器：缺客户端版本先补装，再直连 localhost:25565。"""
        target = str(version or self.selected or "").strip()
        if not target:
            self._status("server_no_version_selected", "error")
            return False
        with self._lock:
            if self._busy:
                return False
            self._busy = BUSY_JOINING
        self._status("server_joining", "loading", version=target)
        self._changed("run")

        def work() -> Any:
            installed_fn = self._callbacks.get("get_installed_version_ids")
            install_fn = self._callbacks.get("install_version")
            if callable(installed_fn) and callable(install_fn):
                try:
                    installed = installed_fn() or []
                except Exception:  # noqa: BLE001
                    installed = []
                if target not in installed:
                    result = install_fn(target)
                    if not (isinstance(result, (tuple, list)) and result and result[0]):
                        return False, "version_install_failed"
            port = self._port_for(target)
            if self._game is not None:
                try:
                    self._game.launch(
                        target,
                        minimize_after=True,
                        server_ip="localhost",
                        server_port=port,
                    )
                    return True, ""
                except Exception as e:  # noqa: BLE001
                    return False, str(e)
            fn = self._callbacks.get("launch_game")
            if not callable(fn):
                return False, "launch_callback_unavailable"
            result = fn(target, minimize_after=True, server_ip="localhost", server_port=port)
            ok = bool(result[0]) if isinstance(result, (tuple, list)) and result else bool(result)
            return ok, ""

        def done(result: Any) -> None:
            ok = bool(result[0]) if isinstance(result, (tuple, list)) and result else bool(result)
            detail = str(result[1]) if isinstance(result, (tuple, list)) and len(result) > 1 else ""
            with self._lock:
                self._busy = ""
            self._changed("run")
            if not ok:
                if detail == "version_install_failed":
                    self._status("version_install_failed", "error", version=target)
                else:
                    self._status("server_join_error", "error", error=detail or "unknown")
            self._emit("on_join_finished", bool(ok), target, detail)

        def failed(error: Any) -> None:
            logger.warning("加入服务器 %s 失败: %s", target, error)
            with self._lock:
                self._busy = ""
            self._changed("run")
            self._status("server_join_error", "error", error=str(error))

        self._submit(work, name=TASK_JOIN, on_done=done, on_error=failed)
        return True

    def rebind(
        self,
        *,
        launcher: Any = None,
        tasks: Any = None,
        game: Any = None,
        achievement: Any = None,
        callbacks: Optional[Dict[str, Callable[..., Any]]] = None,
    ) -> None:
        """启动链条跑完后再接一次核心（launcher 在装配期还不在上下文里）。"""
        merged = launcher_callbacks(launcher)
        if callbacks:
            merged.update(callbacks)
        with self._lock:
            self._launcher = launcher
            self._tasks = tasks
            self._game = game
            if achievement is not None:
                self._achievement = achievement
            if merged:
                self._callbacks = merged

    def detach(self) -> None:
        """窗口退出：停止把事件投给桥；**不杀服务器进程**（旧实现也不杀）。"""
        self._closing = True
        self.set_observer(None)

    # ─── 成就 ───────────────────────────────────────────────

    def _trigger_achievement(self, achievement_id: str) -> bool:
        """推进一次成就进度（引擎是全局单例；取不到就静默跳过）。

        调用口子与旧界面一致：优先走 AppContext 里注册的 AchievementService.engine()，
        没有服务时退回根模块 get_achievement_engine()。QML 永远不碰这里。
        """
        engine = None
        service = self._achievement
        getter = getattr(service, "engine", None)
        if callable(getter):
            try:
                engine = getter()
            except Exception as e:  # noqa: BLE001 - 成就失败不能影响发送命令
                logger.warning("取成就引擎失败: %s", e)
        if engine is None:
            try:
                from achievement_engine import get_achievement_engine

                engine = get_achievement_engine()
            except Exception as e:  # noqa: BLE001
                logger.debug("成就引擎不可用: %s", e)
        if engine is None:
            return False
        try:
            engine.update_progress(achievement_id, value=1, trigger_type="increment")
        except Exception as e:  # noqa: BLE001
            logger.warning("触发成就 %s 失败: %s", achievement_id, e)
            return False
        return True

    # ─── 任务提交 ───────────────────────────────────────────

    def _submit(
        self,
        work: Callable[[], Any],
        *,
        name: str,
        on_done: Callable[[Any], None],
        on_error: Callable[[Any], None],
    ) -> Any:
        if self._tasks is None:
            # 独立使用的降级路径（测试/探针）：没有调度器就同步跑，语义仍是
            # “成功走 on_done、失败走 on_error”，不把异常漏给调用方。
            try:
                result = work()
            except Exception as e:  # noqa: BLE001
                on_error(e)
                return None
            on_done(result)
            return None
        try:
            return self._tasks.submit(work, name=name, on_done=on_done, on_error=on_error)
        except Exception as e:  # noqa: BLE001 - 提交失败按任务失败处理
            logger.error("提交服务器任务 %s 失败: %s", name, e)
            on_error(e)
            return None
