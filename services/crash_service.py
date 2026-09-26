"""崩溃诊断与 AI 分析（原 ``ui/app_crash.py`` 的业务逻辑部分，阶段 1 任务 1.7）。

搬运来源与方法映射（改造前 → 本模块）::

    ui/app_crash.py（改造前）                 本模块
    ──────────────────────────────────────────────────────────────────────────
    CrashHandlerMixin.CRASH_TYPES         →  CRASH_TYPES（同一个 list 对象）
    _diagnose_crash(crash_files)          →  diagnose_crash(crash_files)
    _read_file_tail(path, lines)          →  read_file_tail(path, lines)
    _collect_ai_context(files, code)      →  collect_ai_context(files, code, log_buffer=…)
    _collect_server_ai_context(code)      →  collect_server_ai_context(code, version_id=…,
                                                                            server_log_lines=…,
                                                                            log_buffer=…)
    _do_ai_analyze 的"建消息 + 发请求"    →  analyze_crash(context, exit_code, token,
                                                              *, transport=None)
    _do_server_ai_analyze 的同名部分      →  analyze_server_crash(context, exit_code, token,
                                                                   *, transport=None)

**留在** ``ui/app_crash.py`` 的是纯界面部分：崩溃对话框（``_show_crash_dialog``）、
隐私同意弹窗、AI 结果弹窗、AI 加载窗口，以及"起线程 / 用 ``after(0, …)`` 把结果切回
主线程"的接线。本模块**不碰 Tk、不起线程**：``analyze_*`` 是同步阻塞调用，
跑在哪个线程由调用方决定（现役界面把它放在原来的守护线程里）。

三处必要的形参化（原实现直接读 ``self``，而 ``self`` 是 Tk 应用对象）:

===================  ==========================  ==================================
形参                  原属性                      调用方取值方式
===================  ==========================  ==================================
``log_buffer``       ``self._log_buffer``        ``getattr(self, "_log_buffer", None)``
``version_id``       ``self.selected_server_version``  ``getattr(self, "selected_server_version", "") or ""``
``server_log_lines`` ``self._server_log_lines``  ``getattr(self, "_server_log_lines", [])``
===================  ==========================  ==================================

取值与原来的 ``hasattr(self, "_log_buffer") and self._log_buffer`` 判定等价
（属性缺失 → ``None`` → 假值）。

**网络调用可注入替换**：``analyze_crash`` / ``analyze_server_crash`` 接受
``transport`` 参数，签名 ``(url, data, headers, timeout) -> dict``；默认走
``_http_post_json()`` 的真实 urllib 实现。单测注入假实现即可完全离线，
也能断言真实发出的 URL / 请求体 / 请求头 / 超时。

失败表达：AI 请求失败时抛 ``NetworkError``，其 ``message`` 与旧实现显示给用户的
``_err_msg`` **逐字一致**（``HTTP {code}: {body[:200]}`` 或 ``str(e)``）；
旧实现是在 worker 线程内 ``except`` 掉再回调弹窗，现在由 UI 侧捕获同一个字符串。
"""

import os
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from services.base import Service
from services.errors import NetworkError

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    from app.context import AppContext

#: AI 分析接口地址（与改造前 ``ui/app_crash.py`` 的字面量逐字一致）
AI_ENDPOINT = "https://jingdu.qzz.io/api/deepseek/v1/chat/completions"

#: 可注入的传输实现：``(url, data, headers, timeout) -> 解析后的 JSON dict``
Transport = Callable[[str, bytes, Dict[str, str], int], Any]


#: 崩溃类型检测表。原为 ``CrashHandlerMixin.CRASH_TYPES``，**同一个 list 对象**：
#: ``ui/app_crash.py`` 里仍然以 ``CRASH_TYPES = _CRASH_TYPES`` 绑定它，外部
#: 通过 ``self.CRASH_TYPES`` / ``CrashHandlerMixin.CRASH_TYPES`` 看到的还是这一份。
CRASH_TYPES = [
    {
        "name": "Mixin 错误",
        "icon": "\U0001f9ec",
        "required": ["org.spongepowered.asm.mixin"],
        "optional": ["mixin apply for mod", ".mixins.json"],
        "cause": "优化类模组（如 Sodium、OptiFine）在修改游戏底层代码时注入失败，可能因目标代码不存在、签名不匹配或版本错误。",
        "advice": "检查崩溃报告中 .mixins.json 前的模组名，更新或移除该模组。",
    },
    {
        "name": "模组加载异常",
        "icon": "\u26a0\ufe0f",
        "required": ["Mod Loading has failed", "net.minecraftforge.fml.LoadingFailedException"],
        "optional": ["Could not execute entrypoint stage"],
        "cause": "某个模组初始化失败（配置文件错误、注册表溢出等）。",
        "advice": "查看崩溃报告中 Suspected Mod 字段或 Mod List 中标记为 E 的模组，更新或移除该模组。",
    },
    {
        "name": "依赖缺失/版本错误",
        "icon": "\U0001f517",
        "required": ["ClassNotFoundException", "NoClassDefFoundError", "NoSuchMethodError", "NoSuchFieldError"],
        "optional": ["Missing mod", "Requires", "depends"],
        "cause": "缺少必需的模组、模组版本与游戏或其他模组不兼容，或 API 版本不匹配。",
        "advice": "安装缺失的依赖模组，或更新相关模组到兼容版本。",
    },
    {
        "name": "模组冲突",
        "icon": "\u2694\ufe0f",
        "required": ["Exception caught during firing event: null"],
        "optional": ["conflict", "incompatible", "already registered", "Duplicate"],
        "cause": "两个或多个模组同时修改同一游戏内容，或模组之间不兼容。",
        "advice": "查看 Suspected Mods 字段，逐个禁用可疑模组定位冲突来源。",
    },
    {
        "name": "内存溢出",
        "icon": "\U0001f4be",
        "required": ["java.lang.OutOfMemoryError"],
        "optional": ["Unable to allocate", "heap space", "Metaspace", "GC overhead limit exceeded"],
        "cause": "分配给 Minecraft 的内存不足、内存泄漏（通常由模组引起）或数据集过大。",
        "advice": "在启动器设置中增加最大内存分配（建议 4-8GB），或检查是否有模组导致内存泄漏。",
    },
    {
        "name": "渲染与图形错误",
        "icon": "\U0001f3a8",
        "required": ["OpenGL"],
        "optional": ["GL error", "Shader", "Tesselator", "Rendering", "GPU", "Driver"],
        "cause": "显卡驱动问题、过时的 OpenGL 版本、着色器编译错误或显卡不兼容。",
        "advice": "更新显卡驱动，移除或更新光影/渲染优化模组，确保显卡支持所需 OpenGL 版本。",
    },
    {
        "name": "线程与并发错误",
        "icon": "\U0001f9f5",
        "required": ["ConcurrentModificationException"],
        "optional": ["Deadlock", "Thread stuck", "Wait timed out"],
        "cause": "模组在多线程环境下未正确处理同步。",
        "advice": "更新相关模组，或尝试移除最近添加的模组。",
    },
    {
        "name": "网络同步错误",
        "icon": "\U0001f310",
        "required": ["Connection refused"],
        "optional": ["Read timed out", "Packet handler", "NetworkManager"],
        "cause": "模组自定义网络包未正确注册、数据结构不一致或网络环境不稳定。",
        "advice": "检查网络连接，更新涉及网络功能的模组。",
    },
    {
        "name": "世界生成错误",
        "icon": "\U0001f5fa\ufe0f",
        "required": ["World Generation"],
        "optional": ["Chunk Loading", "Structure", "Biome", "Feature"],
        "cause": "模组的生物群系、结构或特征注册错误、生成算法有 bug，或与其他修改世界生成的模组冲突。",
        "advice": "更新涉及世界生成的模组，或创建新世界测试。",
    },
    {
        "name": "服务端/客户端逻辑错误",
        "icon": "\U0001f9e9",
        "required": ["Integrated Server"],
        "optional": ["Dedicated Server", "Logic error"],
        "cause": "模组未正确区分逻辑客户端与逻辑服务器，导致数据不同步。",
        "advice": "更新相关模组，检查模组是否支持当前游戏版本。",
    },
    {
        "name": "Java 虚拟机崩溃",
        "icon": "\U0001f4a5",
        "required": ["SIGSEGV", "EXCEPTION_ACCESS_VIOLATION"],
        "optional": ["Problematic frame", "fatal error"],
        "cause": "Java 版本不兼容、JVM 参数错误、本地代码崩溃（通常由模组触发）或硬件/驱动问题。",
        "advice": "更换兼容的 Java 版本，检查 JVM 参数，更新显卡驱动。",
    },
]


def read_file_tail(filepath: str, lines: int = 200) -> str:
    """读取文件最后 lines 行。

    阶段 1.23 修正（既有缺陷）：原实现在三个编码上都用 ``errors="ignore"``，
    而 ``errors="ignore"`` 意味着 ``UnicodeDecodeError`` **永远不会抛出** ——
    于是 gbk/latin-1 两级回退是**不可达的死代码**，GBK 编码的日志会被当作
    utf-8 逐字节丢弃（中文全部消失）后送给 AI 分析。

    现在前两级用严格解码（失败才换下一级），latin-1 作为最后一档
    （它把每个字节一一映射成码点，永不失败，因此必须放在最后）。

    ``lines <= 0`` 视为"不限行数、读全文"：原文是 ``readlines()[-lines:]``，
    当 ``lines=0`` 时 ``-0 == 0`` 取到整份文件、当 ``lines<0`` 时变成
    "掐掉开头若干行"，两种行为都不像本意，这里显式写明。
    """
    if not filepath or not os.path.exists(filepath):
        return ""
    try:
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                with open(filepath, "r", encoding=enc) as f:
                    rows = f.readlines()
                    return "".join(rows) if lines <= 0 else "".join(rows[-lines:])
            except (UnicodeDecodeError, UnicodeError):
                continue
    except Exception:
        pass
    return ""


def diagnose_crash(crash_files: dict) -> list:
    """根据崩溃日志内容分析崩溃类型，返回匹配到的崩溃类型列表"""
    # 收集所有可用的日志文本
    text_parts = []

    for key in ("crash_report", "game_log", "debug_log", "jvm_crash_log"):
        path = crash_files.get(key)
        if path and os.path.exists(path):
            try:
                for enc in ("utf-8", "gbk", "latin-1"):
                    try:
                        # 严格解码：失败才换下一级（原 errors="ignore" 让回退不可达）
                        text_parts.append(Path(path).read_text(enc))
                        break
                    except (UnicodeDecodeError, UnicodeError):
                        continue
            except Exception:
                pass

    combined_text = "\n".join(text_parts)
    if not combined_text.strip():
        return []

    matched = []
    for crash_type in CRASH_TYPES:
        required_hits = sum(1 for kw in crash_type["required"] if kw in combined_text)
        optional_hits = sum(1 for kw in crash_type["optional"] if kw in combined_text)
        if required_hits > 0:
            matched.append(
                {
                    "name": crash_type["name"],
                    "icon": crash_type["icon"],
                    "cause": crash_type["cause"],
                    "advice": crash_type["advice"],
                    "score": required_hits + optional_hits * 0.5,
                }
            )

    # 按匹配得分降序排列
    matched.sort(key=lambda x: x["score"], reverse=True)
    return matched[:3]  # 最多返回前 3 个最可能的崩溃类型


def collect_ai_context(crash_files: dict, exit_code: int, log_buffer: Any = None) -> str:
    """收集发送给 AI 的崩溃上下文信息

    Args:
        crash_files: 崩溃相关文件路径字典（crash_report / game_log / debug_log /
            jvm_crash_log / crash_report_list / _mc_dir）。
        exit_code: 游戏进程退出码。
        log_buffer: 原 ``self._log_buffer``（内存中的启动器日志缓冲区，需有
            ``getvalue()``）。为假值时回落到磁盘日志，与原实现一致。
    """
    parts = []

    # 系统信息
    parts.append(
        f"[系统信息]\nOS: {platform.system()} {platform.release()}\n"
        f"Python: {platform.python_version()}\n"
        f"Architecture: {platform.machine()}\n"
        f"退出码: {exit_code}"
    )

    # 崩溃报告（完整内容，通常不大）
    crash_report = crash_files.get("crash_report")
    if crash_report:
        content = read_file_tail(crash_report, 99999)
        if content:
            parts.append(f"[崩溃报告]\n{content}")

    # 游戏日志最后 200 行
    game_log = crash_files.get("game_log")
    if game_log:
        content = read_file_tail(game_log, 200)
        if content:
            parts.append(f"[游戏日志（最后200行）]\n{content}")

    # debug 日志最后 200 行
    debug_log = crash_files.get("debug_log")
    if debug_log:
        content = read_file_tail(debug_log, 200)
        if content:
            parts.append(f"[Debug 日志（最后200行）]\n{content}")

    # JVM 崩溃日志
    jvm_log = crash_files.get("jvm_crash_log")
    if jvm_log:
        content = read_file_tail(jvm_log, 200)
        if content:
            parts.append(f"[JVM 崩溃日志（最后200行）]\n{content}")

    # 启动器日志最后 200 行
    launcher_log = ""
    if log_buffer:
        launcher_log = log_buffer.getvalue()
    if not launcher_log.strip():
        disk_log = None
        try:
            from config import config as _cfg

            disk_log = _cfg.log_file
        except Exception:
            system = platform.system().lower()
            if system == "linux":
                disk_log = Path.home() / ".local" / "share" / "fmcl" / "fmcl.log"
            else:
                disk_log = Path("latest.log")
        if disk_log and disk_log.exists():
            for enc in ("utf-8", "gbk", "latin-1"):
                try:
                    # 严格解码：失败才换下一级（原 errors="ignore" 让回退不可达）
                    launcher_log = disk_log.read_text(enc)
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
    if launcher_log.strip():
        log_lines = launcher_log.strip().splitlines()[-200:]
        parts.append(f"[启动器日志（最后200行）]\n" + "\n".join(log_lines))

    # 结构化日志（JSONL 格式，包含安装/启动/崩溃等核心流程的结构化记录）
    try:
        from config import config

        structured_log_path = config.base_dir / "latest_structured.log"
        if structured_log_path.exists():
            structured_content = read_file_tail(str(structured_log_path), 100)
            if structured_content:
                parts.append(f"[结构化日志（最后100行）]\n{structured_content}")
    except Exception:
        pass

    from structured_logger import slog

    slog.info(
        "ai_context_collected",
        exit_code=exit_code,
        has_crash_report=bool(crash_files.get("crash_report")),
        has_game_log=bool(crash_files.get("game_log")),
        has_debug_log=bool(crash_files.get("debug_log")),
        has_jvm_log=bool(crash_files.get("jvm_crash_log")),
        has_launcher_log=bool(launcher_log.strip()),
        context_length=len("\n\n".join(parts)),
    )

    return "\n\n".join(parts)


def collect_server_ai_context(
    exit_code: int, version_id: str = "", server_log_lines: Optional[List[str]] = None, log_buffer: Any = None
) -> str:
    """收集发送给 AI 的服务器崩溃上下文

    Args:
        exit_code: 服务器进程退出码。
        version_id: 原 ``self.selected_server_version``。
        server_log_lines: 原 ``self._server_log_lines``（在 ``_watch_server_exit``
            中收集的服务器控制台日志行）。
        log_buffer: 原 ``self._log_buffer``。
    """
    parts = []

    # 系统信息
    parts.append(
        f"[系统信息]\nOS: {platform.system()} {platform.release()}\n"
        f"Python: {platform.python_version()}\n"
        f"Architecture: {platform.machine()}\n"
        f"退出码: {exit_code}\n"
        f"场景: 服务器崩溃分析"
    )

    # 服务器版本
    if version_id:
        parts.append(f"[服务器版本]\n{version_id}")

    # 服务器控制台日志（_server_log_lines 在 _watch_server_exit 中收集）
    if server_log_lines:
        parts.append(f"[服务器日志（最后200行）]\n" + "\n".join(server_log_lines[-200:]))

    # 启动器日志最后 200 行
    launcher_log = ""
    if log_buffer:
        launcher_log = log_buffer.getvalue()
    if not launcher_log.strip():
        disk_log = None
        try:
            from config import config as _cfg

            disk_log = _cfg.log_file
        except Exception:
            system = platform.system().lower()
            if system == "linux":
                disk_log = Path.home() / ".local" / "share" / "fmcl" / "fmcl.log"
            else:
                disk_log = Path("latest.log")
        if disk_log and disk_log.exists():
            for enc in ("utf-8", "gbk", "latin-1"):
                try:
                    # 严格解码：失败才换下一级（原 errors="ignore" 让回退不可达）
                    launcher_log = disk_log.read_text(enc)
                    break
                except (UnicodeDecodeError, UnicodeError):
                    continue
    if launcher_log.strip():
        log_lines = launcher_log.strip().splitlines()[-200:]
        parts.append(f"[启动器日志（最后200行）]\n" + "\n".join(log_lines))

    # 结构化日志
    try:
        from config import config

        structured_log_path = config.base_dir / "latest_structured.log"
        if structured_log_path.exists():
            structured_content = read_file_tail(str(structured_log_path), 100)
            if structured_content:
                parts.append(f"[结构化日志（最后100行）]\n{structured_content}")
    except Exception:
        pass

    from structured_logger import slog

    slog.info(
        "server_ai_context_collected",
        exit_code=exit_code,
        has_server_log=bool(server_log_lines),
        has_launcher_log=bool(launcher_log.strip()),
        context_length=len("\n\n".join(parts)),
    )

    return "\n\n".join(parts)


def _http_post_json(url: str, data: bytes, headers: Dict[str, str], timeout: int) -> Any:
    """默认传输实现：POST 一段 JSON 并把响应体解析成 dict。

    与改造前 ``ui/app_crash.py`` 内联的 ``urllib.request.Request(...)`` +
    ``urllib.request.urlopen(req, timeout=120)`` + ``json.loads(resp.read()...)``
    逐字等价；``urllib.error.HTTPError`` 照旧向上冒泡给调用方处理。
    """
    import json
    import urllib.request

    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def analyze_crash(context: str, exit_code: int, token: str, *, transport: Optional[Transport] = None) -> str:
    """调用净读 AI 接口分析崩溃，返回分析正文。

    原实现是 ``ui/app_crash.py`` 的 ``_do_ai_analyze`` 里那个 ``_do_analyze()``
    线程函数，去掉 Tk 加载窗口、``self.after(0, …)`` 回调与 ``threading.Thread``
    之后剩下的部分。同步阻塞（原 ``urlopen`` 超时 120 秒）。

    Args:
        context: ``collect_ai_context()`` 的产物。
        exit_code: 游戏进程退出码（只进结构化日志）。
        token: 净读账号 token。
        transport: 可注入的传输实现，默认 ``_http_post_json``。

    Returns:
        AI 分析正文；AI 返回空内容时是 ``"AI 未返回有效分析结果。"``。

    Raises:
        NetworkError: 请求失败。``message`` 与旧实现交给 ``_show_error()`` 显示的
            文本逐字一致（HTTP 错误 → ``HTTP {code}: {body[:200]}``，
            其它异常 → ``str(e)``）。
    """
    # 构建请求消息
    system_prompt = (
        "你是一个 Minecraft 崩溃日志分析专家。根据用户提供的崩溃报告、游戏日志和系统信息，"
        "分析崩溃原因并给出具体、可操作的建议。\n"
        "请用中文回复，格式如下：\n"
        "## 崩溃原因分析\n（简明扼要地说明崩溃原因）\n\n"
        "## 建议操作\n（列出具体的解决步骤，每步用数字编号）"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请分析以下 Minecraft 崩溃信息：\n\n{context}"},
    ]

    import json
    import urllib.error

    try:
        req_data = json.dumps({"model": "deepseek-chat", "messages": messages, "stream": False}).encode("utf-8")

        send = transport if transport is not None else _http_post_json
        result = send(
            AI_ENDPOINT,
            req_data,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "FMCL/1.0 (Minecraft Launcher; crash-analyzer)",
            },
            120,
        )

        ai_content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not ai_content:
            ai_content = "AI 未返回有效分析结果。"
        from structured_logger import slog

        slog.info("ai_crash_analysis", exit_code=exit_code, result_length=len(ai_content))
        return ai_content
    except urllib.error.HTTPError as e:
        _code = e.code
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        _err_msg = f"HTTP {_code}: {body[:200]}"
        from structured_logger import slog

        slog.error("ai_crash_analysis_failed", exit_code=exit_code, error=_err_msg)
        raise NetworkError(_err_msg, detail=f"endpoint={AI_ENDPOINT}", cause=e) from e
    except Exception as e:
        _err_msg = str(e)
        from structured_logger import slog

        slog.error("ai_crash_analysis_failed", exit_code=exit_code, error=_err_msg)
        raise NetworkError(_err_msg, detail=f"endpoint={AI_ENDPOINT}", cause=e) from e


def analyze_server_crash(context: str, exit_code: int, token: str, *, transport: Optional[Transport] = None) -> str:
    """调用净读 AI 接口分析服务器崩溃，返回分析正文。

    原实现是 ``ui/app_crash.py`` 的 ``_do_server_ai_analyze`` 里那个
    ``_do_analyze()`` 线程函数，去掉 Tk 加载窗口、``self.after(0, …)`` 回调与
    ``threading.Thread`` 之后剩下的部分。与 :func:`analyze_crash` 的唯一差别是
    提示词、User-Agent 与结构化日志事件名（与改造前一样保持两份独立实现）。
    """
    system_prompt = (
        "你是一个 Minecraft 服务器崩溃日志分析专家。根据用户提供的服务器日志、启动器日志和系统信息，"
        "分析服务器崩溃或异常退出的原因并给出具体、可操作的建议。\n"
        "请用中文回复，格式如下：\n"
        "## 崩溃原因分析\n（简明扼要地说明崩溃原因）\n\n"
        "## 建议操作\n（列出具体的解决步骤，每步用数字编号）"
    )
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"请分析以下 Minecraft 服务器异常退出信息：\n\n{context}"},
    ]

    import json
    import urllib.error

    try:
        req_data = json.dumps({"model": "deepseek-chat", "messages": messages, "stream": False}).encode("utf-8")

        send = transport if transport is not None else _http_post_json
        result = send(
            AI_ENDPOINT,
            req_data,
            {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "FMCL/1.0 (Minecraft Launcher; server-crash-analyzer)",
            },
            120,
        )

        ai_content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
        if not ai_content:
            ai_content = "AI 未返回有效分析结果。"
        from structured_logger import slog

        slog.info("ai_server_crash_analysis", exit_code=exit_code, result_length=len(ai_content))
        return ai_content
    except urllib.error.HTTPError as e:
        _code = e.code
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        _err_msg = f"HTTP {_code}: {body[:200]}"
        from structured_logger import slog

        slog.error("ai_server_crash_analysis_failed", exit_code=exit_code, error=_err_msg)
        raise NetworkError(_err_msg, detail=f"endpoint={AI_ENDPOINT}", cause=e) from e
    except Exception as e:
        _err_msg = str(e)
        from structured_logger import slog

        slog.error("ai_server_crash_analysis_failed", exit_code=exit_code, error=_err_msg)
        raise NetworkError(_err_msg, detail=f"endpoint={AI_ENDPOINT}", cause=e) from e


class CrashService(Service):
    """崩溃诊断与 AI 分析服务（``name = "crash"``）。

    每个方法都是上文模块级函数的薄转发。之所以保留实例方法这一层：界面侧统一写
    ``self._crash_service().diagnose_crash(...)``，阶段 2 接上 ``AppContext`` 后
    只要把实例换成注册表里的那一个，调用点不用动。

    本服务**不需要** ``AppContext``：``CrashService()`` 可以独立实例化并直接使用
    （阶段 1 的 Tk 界面就是这么取的），也不访问 ``self.ui`` / ``self.tasks`` /
    ``self.config`` —— 需要弹窗或后台线程时由调用方负责。

    Args:
        context: 可选 ``AppContext``。
        transport: 可选默认传输实现（供单测注入假 AI 接口），
            单次调用仍可用 ``transport=`` 覆盖。
    """

    name = "crash"
    label = "崩溃诊断"

    def __init__(self, context: Optional["AppContext"] = None, *, transport: Optional[Transport] = None) -> None:
        super().__init__(context)
        self._transport = transport

    # ─── 崩溃诊断 ────────────────────────────────────────────

    def diagnose_crash(self, crash_files: dict) -> list:
        """转发 :func:`diagnose_crash`。"""
        return diagnose_crash(crash_files)

    def read_file_tail(self, filepath: str, lines: int = 200) -> str:
        """转发 :func:`read_file_tail`。"""
        return read_file_tail(filepath, lines)

    # ─── AI 上下文 ───────────────────────────────────────────

    def collect_ai_context(self, crash_files: dict, exit_code: int, log_buffer: Any = None) -> str:
        """转发 :func:`collect_ai_context`。"""
        return collect_ai_context(crash_files, exit_code, log_buffer)

    def collect_server_ai_context(
        self, exit_code: int, version_id: str = "", server_log_lines: Optional[List[str]] = None, log_buffer: Any = None
    ) -> str:
        """转发 :func:`collect_server_ai_context`。"""
        return collect_server_ai_context(exit_code, version_id, server_log_lines, log_buffer)

    # ─── AI 分析 ─────────────────────────────────────────────

    def analyze_crash(self, context: str, exit_code: int, token: str, *, transport: Optional[Transport] = None) -> str:
        """转发 :func:`analyze_crash`（``transport`` 缺省时用构造期注入的那个）。"""
        return analyze_crash(context, exit_code, token, transport=transport or self._transport)

    def analyze_server_crash(
        self, context: str, exit_code: int, token: str, *, transport: Optional[Transport] = None
    ) -> str:
        """转发 :func:`analyze_server_crash`（``transport`` 缺省时用构造期注入的那个）。"""
        return analyze_server_crash(context, exit_code, token, transport=transport or self._transport)


__all__ = [
    "AI_ENDPOINT",
    "CRASH_TYPES",
    "CrashService",
    "Transport",
    "analyze_crash",
    "analyze_server_crash",
    "collect_ai_context",
    "collect_server_ai_context",
    "diagnose_crash",
    "read_file_tail",
]
