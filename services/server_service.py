"""服务器服务（阶段 1 任务 1.9）—— 服务器生命周期、控制台流与配置读写的纯逻辑。

从 ``ui/app_server.py``（``ServerTabMixin``）**逐字搬运**而来。留在界面文件里的是
控件构建、对话框、``after`` 调度与 ``_task_queue`` 投递；搬进来的是这些：

- **控制台日志解析**：玩家加入/离开的两条正则、日志行缓冲与行数上限（D-86）
- **进程退出监控**：读管道 → 行回调 → 退出回调，**恰好回调一次**（D-84）
- **进程内存采样**：``get_process_memory`` 的三平台统一实现（D-80 / D-81）
- **每服启动配置**：``.fmcl_server.json`` 里的最大内存读写（转发 ``launcher.server_config``）
- **可用版本分页**、内存与退出码的文本格式化

设计约定（与 ``services/crash_service.py`` / ``services/voice_service.py`` 一致）：

- 纯计算写成**模块级函数**，``ServerService`` 上的同名方法只是薄转发；
- 需要状态（日志缓冲）的放进 :class:`ServerConsoleWatcher`；
- **不弹窗、不碰控件、不自己起线程**：线程仍由界面侧的 ``_run_in_thread`` 起，
  ``watch_server_exit`` 期望在 worker 线程里被调用，且它只调用调用方传入的回调；
- 失败以**返回值**表达（``None`` / ``-1`` 哨兵 / ``(成功, 错误信息)``），
  与改造前一致 —— 阶段 1 不引入新的失败表达方式。

本服务**不需要** ``AppContext``：``ServerService()`` 可以独立实例化并直接使用
（阶段 1 的 Tk 界面就是这么取的），也不访问 ``self.ui`` / ``self.tasks`` /
``self.config`` —— 需要弹窗或后台线程时由调用方负责。

.. note::
   日志用 ``logzero.logger`` 而不是 ``logging.getLogger(__name__)``，这是**刻意的**：
   界面侧 ``ui/app_base.py`` 把日志框写入器挂在 ``logzero.logger`` 上
    （``logzero.logger.addHandler(self._log_writer)``，见该文件 882-891 行），
   换成模块自己的 logger 会让这几条日志**从界面日志框里消失**。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple

from logzero import logger

from launcher.server_config import get_server_dir
from launcher.server_config import get_server_launch_memory, set_server_launch_memory
from services.base import Service

#: 服务器控制台 Python 侧行缓冲的默认上限。
#:
#: 界面侧调用时会把 ``ui.log_widget.LOG_BUFFER_MAX_LINES`` 显式传进来，那个常量才是
#: 运行期的唯一真源；这里保留同名默认值只是为了让服务能独立使用 —— 服务层不能
#: import ``ui.*``（``scripts/check_services_purity.py`` 强制），拿不到那个常量。
LOG_BUFFER_MAX_LINES: int = 5000

#: 拿不到真实退出码时的哨兵值。界面据此仍会正确复位（只要消息投递到了）。
UNKNOWN_EXIT_CODE: int = -1


# ─── 控制台日志行解析 ────────────────────────────────────────
#
# 两条正则与判定顺序**逐字**来自 ``ServerTabMixin._append_server_log``。注意外层
# ``...$`` 与内层 ``<名字>`` 是**两个独立条件**：原实现只有两者同时命中才认账，
# 所以 ``"<Steve> joined the game  "``（行尾多一个空格）不算加入事件。


def parse_joined_player(message: str) -> Optional[str]:
    """解析玩家加入事件，返回玩家名；不是"加入"行则返回 ``None``。"""
    join_match = re.search(r"joined the game$", message)
    if join_match:
        # 提取玩家名（格式: [HH:MM:SS] [Server thread/INFO]: <PlayerName> joined the game）
        name_match = re.search(r"<([^>]+)> joined the game", message)
        if name_match:
            return name_match.group(1)
    return None


def parse_left_player(message: str) -> Optional[str]:
    """解析玩家离开事件，返回玩家名；不是"离开"行则返回 ``None``。"""
    leave_match = re.search(r"left the game$", message)
    if leave_match:
        name_match = re.search(r"<([^>]+)> left the game", message)
        if name_match:
            return name_match.group(1)
    return None


def apply_player_events(players: List[str], message: str) -> List[str]:
    """解析一行服务器日志中的玩家事件，并**就地**更新 ``players``。

    原实现把"加入"与"离开"写成同一个方法里两个独立的 ``if``（先判加入、再判离开）。
    两条规则都以 ``$`` 结尾锚定，所以同一行不可能同时命中（返回列表至多一个元素）；
    这里保留同样的独立判定与"只有真的发生变化才算一次"的语义。

    Args:
        players: 在线玩家列表，**就地**修改（与改造前 ``self._server_online_players``
            是同一个列表对象，界面其他分支直接读它）。
        message: 一行服务器控制台日志。

    Returns:
        本次真正发生变化的动作列表，元素为 ``"join"`` / ``"leave"``；可能是空列表
        （无事件，或玩家本就在/不在列表里 —— 原实现同样会跳过）。
    """
    actions: List[str] = []

    player = parse_joined_player(message)
    if player is not None:
        if player not in players:
            players.append(player)
            actions.append("join")

    player = parse_left_player(message)
    if player is not None:
        if player in players:
            players.remove(player)
            actions.append("leave")

    return actions


# ─── 退出码 ──────────────────────────────────────────────────


def salvage_exit_code(proc: Any, default: int = UNKNOWN_EXIT_CODE) -> int:
    """进程异常退出时尽量回捞真实退出码，拿不到就用 ``default``（-1 哨兵）。

    逐字来自 ``_watch_server_exit`` 的异常分支：``getattr`` 与 ``isinstance`` 都
    套在 ``try`` 里 —— 有的进程包装对象的 ``returncode`` 是**会抛异常的属性**，
    宁可退出码不精确，也不能让界面卡死。
    """
    exit_code = default
    try:
        code = getattr(proc, "returncode", None)
        if isinstance(code, int):
            exit_code = code
    except Exception:
        pass
    return exit_code


def format_exit_info(exit_code: int) -> str:
    """退出码的展示后缀：0 返回空串，非 0 返回 ``" (exit_code=N)"``。

    逐字来自 ``_ask_server_exit_quality`` 的
    ``f" ({exit_code=})" if exit_code != 0 else ""``。
    """
    return f" ({exit_code=})" if exit_code != 0 else ""


# ─── 内存采样与格式化 ────────────────────────────────────────


def get_process_memory(pid: int) -> Optional[int]:
    """获取进程的内存占用（MB）。

    阶段 1.21 修正（对应 D-80 / D-81）：

    - 原实现按 ``platform.system() == "Windows"`` 二分，macOS（``"Darwin"``）
      落进 ``/proc`` 分支 —— 那个路径在 macOS 上不存在，于是**服务器内存
      永远显示旧值**。
    - 原实现的主路径是主线程上每 2 秒同步跑一次 ``tasklist``（``timeout=5``），
      最坏每 2 秒卡界面 5 秒。

    现改为**优先 psutil**（本项目硬依赖，三平台统一、微秒级、不起子进程），
    仅在 psutil 不可用时才退回各平台原生方式（Windows tasklist /
    Linux ``/proc`` / macOS ``ps``）。这比"把阻塞调用挪到别的线程"更彻底：
    阻塞源本身被去掉了，主线程调用也就安全了。
    """
    # ── 首选：psutil，三平台统一 ──
    try:
        import psutil

        return psutil.Process(pid).memory_info().rss // (1024 * 1024)
    except ImportError:
        pass
    except Exception:
        # NoSuchProcess / AccessDenied / ZombieProcess 等：再走一遍原生方式兜底
        pass

    # ── 退回：各平台原生方式 ──
    import platform
    import subprocess

    try:
        system = platform.system()

        if system == "Windows":
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            for line in result.stdout.splitlines():
                if f'"{pid}"' in line:
                    # CSV 格式: "name","pid","session","session#","mem"
                    parts = line.strip('"').split('","')
                    if len(parts) >= 5:
                        mem_str = parts[4].replace(",", "").replace(" K", "").strip()
                        return int(mem_str) // 1024  # KB -> MB

        elif system == "Darwin":
            # macOS：/proc 不存在，用 ps 的 RSS 列（单位 KB）
            result = subprocess.run(
                ["ps", "-o", "rss=", "-p", str(pid)],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
            )
            text = result.stdout.strip()
            if text:
                return int(text.split()[0]) // 1024  # KB -> MB

        else:
            # Linux: /proc/<pid>/status
            with open(f"/proc/{pid}/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        kb = int(line.split()[1])
                        return kb // 1024  # KB -> MB
    except Exception:
        pass
    return None


def running_process_memory(proc: Any) -> Optional[int]:
    """进程仍在运行时返回其内存占用（MB），否则返回 ``None``。

    逐字来自 ``_update_mem_display`` 的两个前置判断：句柄非 None 且
    ``poll() is None``（仍在跑）才去读 ``pid``。任何异常（含 ``proc.pid``
    取不到）都照旧向上抛，由界面那层原本就有的 ``try/except`` 兜住 ——
    这样异常的作用域与改造前一致。
    """
    if proc is not None and proc.poll() is None:
        return get_process_memory(proc.pid)
    return None


def format_memory_mb(mem_mb: int, mb_unit: str = "MB") -> str:
    """把 MB 数格式化成界面文本：``>= 1024`` 显示 GB，否则显示 ``<n> <单位>``。

    ``mb_unit`` 由调用方传入（界面传 ``_("mb")``）—— 服务层不能 import ``ui.i18n``，
    翻译始终留在界面侧，这里只做数值分支。
    """
    if mem_mb >= 1024:
        return f"{mem_mb / 1024:.1f} GB"
    return f"{mem_mb} {mb_unit}"


# ─── 可用版本分页 ────────────────────────────────────────────


def paginate(items: Sequence[Any], page: int, page_size: int) -> Tuple[List[Any], int, int]:
    """分页计算（逐字来自 ``_render_server_available_page``）。

    Returns:
        ``(当前页条目, 修正后的页码, 总页数)``。页码越界时被夹到 ``[1, 总页数]``，
        且总页数至少为 1（空列表也是"1/1"页，与原实现的按钮禁用逻辑一致）。
    """
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    current_page = max(1, min(page, total_pages))
    start = (current_page - 1) * page_size
    end = start + page_size
    return list(items[start:end]), current_page, total_pages


def server_dir_path(server_root: Any, version_id: str) -> Path:
    """由服务器根目录与版本 ID 得到单个服务器的目录。

    与改造前的 ``Path(self.callbacks["get_server_dir"]()) / version_id`` 等价：
    转发到 ``launcher.server_config.get_server_dir``（实现就是
    ``return Path(server_root) / version_id``）。
    """
    return get_server_dir(server_root, version_id)


# ─── 控制台读取与日志缓冲 ────────────────────────────────────


class ServerConsoleWatcher:
    """服务器控制台日志的读取、缓冲与退出投递（原 ``_watch_server_exit`` 的线程主体）。

    **无论走哪条路径都必须恰好投递一次 ``on_exit``。**

    阶段 1.23 修正（D-84）：原实现有三条路径直接 return 或吞掉异常而不投递
    ``server_exit``（缺少 ``get_server_process`` 回调、进程句柄为 None、
    读日志/等待退出抛异常）。而 ``server_exit`` 是**唯一**会恢复界面状态的
    消息 —— 见 ``ui/app_handlers.py`` 的 ``server_exit`` 分支，它负责：
    禁用"停止"按钮、恢复"启动"按钮、停止内存监控定时器、复位状态栏与内存
    显示、触发成就与插件钩子。

    缺少这条消息，界面就永久停在"运行中"：启动按钮一直禁用、内存定时器
    空转，用户**再也无法启动服务器**（只能重启启动器）。

    Args:
        log_lines: 日志缓冲列表，**由调用方持有**（界面侧就是
            ``self._server_log_lines``，``ui/app_crash.py`` 的崩溃报告直接读它）。
            服务只持有引用并原地增删，界面因此始终看到同一份数据；
            传 ``None`` 时自建一个空列表（供独立使用/单测）。
        max_lines: 缓冲保留的最大行数。
    """

    def __init__(self, log_lines: Optional[List[str]] = None, max_lines: int = LOG_BUFFER_MAX_LINES) -> None:
        self.log_lines: List[str] = log_lines if log_lines is not None else []
        self.max_lines = max_lines

    def reset(self) -> None:
        """清空上次启动的日志缓存（原地清空，调用方持有的同一个列表随之更新）。"""
        del self.log_lines[:]

    def append_line(self, text: str) -> None:
        """追加一行，并只保留最近 ``max_lines`` 行。

        阶段 1.22 修正（D-86）：Python 侧缓冲原先无上限。
        崩溃报告只取最后 200 行（ui/app_crash.py 的 [-200:]），
        所以保留最近 LOG_BUFFER_MAX_LINES 行足够，且能防止
        服务器挂机数天时列表无限增长。
        """
        self.log_lines.append(text)
        if len(self.log_lines) > self.max_lines:
            del self.log_lines[:-self.max_lines]

    def watch(
        self,
        get_process: Optional[Callable[[], Any]],
        on_log: Optional[Callable[[str], None]] = None,
        on_exit: Optional[Callable[[int], None]] = None,
    ) -> int:
        """读取进程输出直到 EOF，随后等待退出。

        Args:
            get_process: 返回进程对象（``subprocess.Popen`` 形状）的可调用对象；
                传 ``None`` 表示界面没有注册 ``get_server_process`` 回调
                —— 这条路径**同样**投递退出消息（D-84 的第 1 条缺陷路径）。
            on_log: 每读到一行非空输出时调用一次（界面据此投递 ``server_log``）。
            on_exit: 退出回调，**恰好一次**（界面据此投递 ``server_exit``）。

        Returns:
            退出码；回捞不到真实退出码时为 ``-1``。调用方不必依赖返回值，
            ``on_exit`` 才是权威路径。
        """
        exit_code = UNKNOWN_EXIT_CODE  # 未知退出码的哨兵值；界面据此仍会正确复位
        proc = None

        try:
            if get_process is None:
                logger.warning("缺少 get_server_process 回调，无法监控服务器退出")
                return exit_code

            proc = get_process()
            if proc is None:
                logger.warning("服务器进程句柄为空，无法监控退出")
                return exit_code

            # 清空上次启动的日志缓存
            self.reset()

            # 读取所有输出直到 EOF（即使进程已经退出也能读取管道中残留的数据）
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if text:
                    self.append_line(text)
                    if on_log is not None:
                        on_log(text)

            exit_code = proc.wait()
        except Exception as e:
            logger.error(f"监控服务器退出失败: {e}", exc_info=True)
            # 管道被强杀等情况下 readline/wait 会抛异常；此时尽量回捞真实退出码，
            # 拿不到就保留 -1。宁可退出码不精确，也不能让界面卡死。
            exit_code = salvage_exit_code(proc, exit_code)
        finally:
            if on_exit is not None:
                on_exit(exit_code)

        return exit_code


# ─── 服务对象 ────────────────────────────────────────────────


class ServerService(Service):
    """服务器服务（``name = "server"``）。

    每个方法都是上文模块级函数的薄转发。保留实例方法这一层，是为了让界面侧统一写
    ``self._server_service().xxx(...)``：阶段 2 接上 ``AppContext`` 之后只要把实例
    换成注册表里的那一个，调用点不用动。
    """

    name = "server"
    label = "服务器"

    # ─── 控制台日志解析 ──────────────────────────────────────

    @staticmethod
    def parse_joined_player(message: str) -> Optional[str]:
        """转发 :func:`parse_joined_player`。"""
        return parse_joined_player(message)

    @staticmethod
    def parse_left_player(message: str) -> Optional[str]:
        """转发 :func:`parse_left_player`。"""
        return parse_left_player(message)

    @staticmethod
    def apply_player_events(players: List[str], message: str) -> List[str]:
        """转发 :func:`apply_player_events`。"""
        return apply_player_events(players, message)

    # ─── 进程退出监控 ────────────────────────────────────────

    @staticmethod
    def console_watcher(
        log_lines: Optional[List[str]] = None, max_lines: int = LOG_BUFFER_MAX_LINES
    ) -> ServerConsoleWatcher:
        """创建一个控制台读取器（日志缓冲由调用方持有的列表承载）。"""
        return ServerConsoleWatcher(log_lines, max_lines)

    def watch_server_exit(
        self,
        log_lines: Optional[List[str]] = None,
        *,
        get_process: Optional[Callable[[], Any]] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_exit: Optional[Callable[[int], None]] = None,
        max_lines: int = LOG_BUFFER_MAX_LINES,
    ) -> int:
        """转发 :meth:`ServerConsoleWatcher.watch`（D-84 的"恰好一次"在那里）。"""
        watcher = ServerConsoleWatcher(log_lines, max_lines)
        return watcher.watch(get_process, on_log=on_log, on_exit=on_exit)

    @staticmethod
    def salvage_exit_code(proc: Any, default: int = UNKNOWN_EXIT_CODE) -> int:
        """转发 :func:`salvage_exit_code`。"""
        return salvage_exit_code(proc, default)

    @staticmethod
    def format_exit_info(exit_code: int) -> str:
        """转发 :func:`format_exit_info`。"""
        return format_exit_info(exit_code)

    # ─── 内存采样与格式化 ────────────────────────────────────

    @staticmethod
    def get_process_memory(pid: int) -> Optional[int]:
        """转发 :func:`get_process_memory`。"""
        return get_process_memory(pid)

    @staticmethod
    def running_process_memory(proc: Any) -> Optional[int]:
        """转发 :func:`running_process_memory`。"""
        return running_process_memory(proc)

    @staticmethod
    def format_memory_mb(mem_mb: int, mb_unit: str = "MB") -> str:
        """转发 :func:`format_memory_mb`。"""
        return format_memory_mb(mem_mb, mb_unit)

    # ─── 可用版本分页 ────────────────────────────────────────

    @staticmethod
    def paginate_versions(versions: Sequence[Any], page: int, page_size: int) -> Tuple[List[Any], int, int]:
        """转发 :func:`paginate`。"""
        return paginate(versions, page, page_size)

    # ─── 每服启动配置（转发 launcher.server_config）──────────

    @staticmethod
    def server_dir_path(server_root: Any, version_id: str) -> Path:
        """转发 :func:`server_dir_path`。"""
        return server_dir_path(server_root, version_id)

    @staticmethod
    def get_launch_memory(server_dir: Any) -> Optional[str]:
        """读取某个服务器单独设置的最大内存（未设置或读失败时返回 ``None``）。

        逐字保留改造前 ``_get_server_memory_for`` 的 ``try/except → None``。
        """
        try:
            return get_server_launch_memory(server_dir)
        except Exception:
            return None

    @staticmethod
    def set_launch_memory(server_dir: Any, memory: Optional[str]) -> Tuple[bool, str]:
        """写入某个服务器单独的最大内存，返回 ``(是否成功, 错误信息)``。

        这是**历史遗留的元组返回值**，按阶段 1 约定原样保留（见
        ``services/errors.py`` 模块说明），由调用方继续按老方式处理。
        """
        return set_server_launch_memory(server_dir, memory)


__all__ = [
    "LOG_BUFFER_MAX_LINES",
    "UNKNOWN_EXIT_CODE",
    "ServerConsoleWatcher",
    "ServerService",
    "apply_player_events",
    "format_exit_info",
    "format_memory_mb",
    "get_process_memory",
    "paginate",
    "parse_joined_player",
    "parse_left_player",
    "running_process_memory",
    "salvage_exit_code",
    "server_dir_path",
]
