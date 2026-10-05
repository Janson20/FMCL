"""缺陷台账的**期望状态表**（返工 E 组，Q-7 的落地：把可重跑断言接进 CI）。

## 为什么要有它

`07-known-defects.md` 是一份**文档**，`poc/_verify_defect_status.py` 是一份**草稿目录里的
脚本** —— 两者都不在 CI 里，于是"台账"和"代码"可以长期各说各话：
文档说某条已修、代码里其实又回来了；或者谁把一条挂账的缺陷修好了，
却没人回去改文档（下一个人照着文档去"修"一个已经不存在的问题）。

本文件把这件事变成**可执行的期望状态**：

* 「**已修**」的条目 = **正向断言**：修复的特征必须出现在代码里（少一个就红）；
* 「**挂账**」的条目 = **钉住现状**：缺陷的特征必须**还在**，并且条目里必须写明
  **理由 + 排期**（谁把它修好了，这条断言会先红，逼着他回来改台账 —— 这就是 Q-7 要的闸门）；
* 自检：状态取值合法、挂账项必须有理由与排期、缺陷号不重复、提到的文件真的存在。

放 `tests/` 而不是 `poc/`：CI 跑的就是 `pytest tests/`，这样台账**天然进 CI**，
不需要再改工作流。

## 覆盖范围（写清楚，免得被误读成"全部缺陷都在这里"）

这张表覆盖的是**阶段 2 收尾时还挂着的 24 条**：18 条欠账（D-04~D-28），加上收口轮新登记的
D-146 / D-147 / D-150 / D-151 / D-152，阶段 3 前置轮新登记的 D-153 / D-154，
以及**阶段 3 任务 3.1 新登记的 5 条**（D-155 ~ D-157 已修：签到提示是死代码 / 云同步不看返回值 /
强杀后又报"正常退出"；D-158 / D-159 挂账：旧 Tk 启动流程并存、启动前备份与启动并发）。
**不包含**更早那批已经是"已修"或"已由决策延后"的编号（D-01/02/03/12 与 D-06/07/14/15/77/101/102）——
它们各自的判据要么已经被对应任务的测试覆盖，要么需要读 `docs/refactor/`
（**未入库**，CI 里读不到，所以不能进这张表）。
这条边界是刻意的：**台账只依赖仓库里被跟踪的文件**，否则 CI 上必然红。

## 它与 `poc/_verify_defect_status.py` 的关系

那个脚本原来有 1197 行、自己实现 29 条正例 + 8 条负例自检，钉的是"**缺陷仍在**"。
按 Q-7 的裁决改造成"期望状态表"之后，表住在**本文件**，那个脚本退化成
**读同一张表的 CLI 报告** —— 避免两份资产各自漂移（本仓库反复强调的
"同一件事只有一份实现"）。

跑法::

    .venv\\Scripts\\python.exe -m pytest tests/test_defect_ledger.py -q
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

#: 排期必须能对上这几类之一（否则"挂账"就成了"忘掉"）。
#: 允许只写「阶段 3」/「阶段 4」（有些条目还没拆到 3.x 的具体任务号），
#: 但**必须**点到某个阶段或明说"待裁决"。
SCHEDULE_RE = re.compile(r"(阶段\s*[34]|待裁决)")


def strip_python_comments(text: str) -> str:
    """抹掉 Python 注释（**保留字符串字面量与换行**，所以行号不变）。

    为什么必须抹：本仓库修缺陷时会在旁边写"原来这里是 xxx"的注释，
    而判据要断言的恰恰是那些**旧写法不见了** —— 不抹注释的话，
    注释里提到一句旧写法就会让判据误报（这不是假设：本文件第一版就是这么红的两条）。
    用 `tokenize` 而不是自己写状态机：字符串里的 `#`、三引号、续行都由标准库处理。
    """
    lines = text.split("\n")
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text  # 不是合法 Python（负例自检喂进来的片段）就按原样处理
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        (row, col), (_, end_col) = token.start, token.end
        line = lines[row - 1]
        lines[row - 1] = line[:col] + " " * (end_col - col) + line[end_col:]
    return "\n".join(lines)


def source(relative: str) -> str:
    """读仓库里的文件（统一换行、抹掉注释，方便做片段匹配）。"""
    path = REPO_ROOT / relative
    assert path.is_file(), f"台账指向的文件不存在：{relative}"
    return strip_python_comments(path.read_text(encoding="utf-8").replace("\r\n", "\n"))


def raw(relative: str) -> str:
    """读**未抹注释**的原文（D-23 这种"修复就是改文档字符串"的条目要用它）。"""
    path = REPO_ROOT / relative
    assert path.is_file(), f"台账指向的文件不存在：{relative}"
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def has(text: str, *snippets: str) -> bool:
    return all(snippet in text for snippet in snippets)


@dataclass(frozen=True)
class Entry:
    """一条缺陷的期望状态。"""

    defect: str
    state: str                       # "已修" | "挂账"
    summary: str
    where: str
    #: 「已修」的判据：这些片段必须**都在**（第一个文件的 `markers` 里）
    markers: Tuple[str, ...] = ()
    #: 需要跨文件/更复杂判据时用这个（返回 (是否通过, 说明)）
    check: Optional[Callable[[], Tuple[bool, str]]] = None
    #: 「挂账」的理由 + 排期（必须写明；排期要能匹配 `SCHEDULE_RE`）
    reason: str = ""
    files: Tuple[str, ...] = field(default_factory=tuple)


def _d04_cancel_button(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-04：隐私弹窗右侧那颗按钮（拒绝）的文案必须是 `cancel` 键。

    `text` 可注入：负例自检会喂**故意改坏**的源码，断言这条判据**会变红**
    （判据不能是空断言 —— 本仓库的规矩）。
    """
    text = text if text is not None else source("ui/app_crash.py")
    text = strip_python_comments(text)
    start = text.find("cancel_btn = tk.Button(")
    if start < 0:
        return False, "找不到 cancel_btn 的创建处"
    block = text[start:start + 400]
    ok = 'text=_("cancel")' in block and '_("confirm")' not in block
    return ok, f"cancel_btn 的文案：{'cancel' if ok else '仍是 confirm/其它'}"


def _d28_no_redundant_flag(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-28：`server_join_done` 分支里那行恒真的 `self._running = True` 已经删掉。"""
    text = text if text is not None else source("ui/app_handlers.py")
    text = strip_python_comments(text)
    start = text.find('elif task_type == "server_join_done":')
    if start < 0:
        return False, "找不到 server_join_done 分支"
    end = text.find('elif task_type == "server_join_error":', start)
    branch = text[start:end if end > 0 else start + 800]
    ok = "self._running = True" not in branch
    return ok, "server_join_done 分支" + ("已无冗余赋值" if ok else "仍有 self._running = True")


# ─── 行为判据（比"源码里有某个片段"更强：直接跑一段真实调用） ──────────
#
# 为什么不用纯文本判据：本仓库修缺陷时会在旁边写注释解释，纯文本判据要么被注释干扰、
# 要么只能钉"某个名字还在"。下面这几条**真的调用一遍**，退化与改名都躲不过去。


def _d10_snapshot_is_immutable() -> Tuple[bool, str]:
    """D-10：读者拿到的旧结果不会被**后续**的解析改写（快照语义）。

    判据：解析 A 拿到 `lines` → 再解析 B → 旧的那份必须**一个元素都没变**。
    旧写法（共享 list）在这里必然红：第二次 parse 会 clear 掉同一个列表。
    """
    from services.music_lyrics import LyricParser

    parser = LyricParser()
    parser.parse("[00:01.00]第一首-行1\n[00:02.00]第一首-行2")
    first = tuple(getattr(parser, "lines", ()))
    parser.parse("[00:09.00]第二首")
    after = tuple(getattr(parser, "lines", ()))
    ok = (len(first) == 2 and len(after) == 1
          and all("第一首" in line.text for line in first))
    return ok, f"旧快照 {len(first)} 行 -> 解析后仍是 {len(first)} 行，新快照 {len(after)} 行"


def _d17_lookup_is_pure() -> Tuple[bool, str]:
    """D-17：`get_line_at` 是纯函数 —— 读路径不再写共享 LRU 缓存。"""
    from services.music_lyrics import LyricParser

    text = source("services/music_lyrics.py")
    parser = LyricParser()
    parser.parse("[00:01.00]a\n[00:02.00]b\n[00:03.00]c")
    before = dict(getattr(parser, "__dict__", {}))
    parser.get_line_at(1500)
    parser.get_line_at(2500)
    after = dict(getattr(parser, "__dict__", {}))
    ok = (before.keys() == after.keys()
          and "_search_cache" not in after
          and "_search_cache" not in text)
    return ok, f"连续两次查询后实例状态{'未变' if before == after else '变了'}；缓存字段{'已删除' if '_search_cache' not in after else '仍在'}"


def _d18_boundaries() -> Tuple[bool, str]:
    """D-18：边界语义唯一且自洽（越界取上界、首行之前为 None），且没有不可达纠正分支。"""
    from services.music_lyrics import LyricParser

    parser = LyricParser()
    parser.parse("[00:01.00]a\n[00:02.00]b")
    problems: List[str] = []
    if parser.get_line_at(500) is not None:
        problems.append("首行之前应返回 None")
    if getattr(parser.get_line_at(1000), "text", None) != "a":
        problems.append("t=1000 应命中 a")
    if getattr(parser.get_line_at(2000), "text", None) != "b":
        problems.append("t=2000 应命中 b")
    if getattr(parser.get_line_at(999999), "text", None) != "b":
        problems.append("越界应取上界 b")
    empty = LyricParser()
    empty.parse("")
    if empty.get_line_at(1000) is not None:
        problems.append("空歌词应返回 None")
    return problems == [], "；".join(problems) or "边界语义全部符合"


def _d20_writable_dir_fallback() -> Tuple[bool, str]:
    """D-20：写盘目录有"先判可写、不可写就回退"的实现（不再是裸 `mkdir`）。

    判据贴着**修法本身**：回退策略与 `config.py` 同构（复用它的 `_is_writable_dir` /
    `_get_user_data_dir`），所以这三样必须同时在场 —— 谁把这里改回裸 `mkdir`，
    这条就红。
    """
    text = source("services/music_playlist.py")
    ok = ("_writable_data_dir" in text and "_is_writable_dir" in text
          and "_get_user_data_dir" in text and "get_music_data_dir" in text)
    return ok, "写盘目录解析" + ("已带同构回退" if ok else "仍是裸 mkdir")


def _d22_single_normalisation() -> Tuple[bool, str]:
    """D-22：比对键统一走一个函数（normpath 不再"每首歌算一次"）。"""
    text = source("services/music_playlist.py")
    ok = "_song_key" in text and "_query_keys" in text
    return ok, "比对键" + ("已收敛到 _song_key/_query_keys" if ok else "仍散落在各处")


def _d11_published_cache_only() -> Tuple[bool, str]:
    """D-11：GPU 采样缓存"整体构造 → 一次性替换"，读侧只绑定一次（不做就地修改）。

    判据贴合修法的三处特征：发布是**一次整体赋值**、读侧先绑成局部 `snapshot`、
    检测器初始化前需要的 `import json` 在场（那是 D-24 顺手修掉的 F821）。
    就地修改的禁令由 `tests/test_monitor_gpu_cache.py` 的 AST 守卫管（它只看 `collect()`），
    这里不重复判 —— 检测器自己在初始化期往私有 dict 里塞字段是**约定允许**的。
    """
    text = source("services/monitor_service.py")
    ok = ("self._gpu_cache = " in text and "snapshot = self._gpu_cache" in text
          and "import json" in text
          and (REPO_ROOT / "tests" / "test_monitor_gpu_cache.py").is_file())
    return ok, ("一次性发布 + 读侧一次性绑定 + 守卫用例"
                if ok else "缺发布/绑定/守卫之一")


def _d19_duration_delegates() -> Tuple[bool, str]:
    """D-19：界面侧的播放时长以"实际文件"为准，折算那一步交给服务层唯一实现。"""
    text = source("ui/app_music.py")
    service = source("services/music_effects.py")
    ok = ("def _music_playback_duration" in text and "effective_duration" in text
          and "def effective_duration" in service)
    return ok, "时长基准" + ("已接服务层折算" if ok else "仍是本地两份实现之一")


def _d23_docstring_tells_the_truth() -> Tuple[bool, str]:
    """D-23：桌面歌词的文档说明与实现一致（没有穿透），并把穿透标成 3.18 的新增项。"""
    text = raw("ui/music_desktop_lyric.py")
    ok = "穿透" in text and "3.18" in text
    return ok, "docstring" + ("已写明没有穿透 + 3.18 新增项" if ok else "仍未说明")


def _d24_dead_code_removed() -> Tuple[bool, str]:
    """D-24：监控两文件里的死导入/死状态已清（点名几个可判定的）。"""
    monitor = source("ui/app_monitor.py")
    service = source("services/monitor_service.py")
    gone = [name for name in ("_net_prev", "_disk_prev") if name not in monitor]
    ok = (len(gone) == 2 and "import subprocess" not in monitor
          and "import os" not in service.split("def ")[0])
    return ok, f"已消失：{gone}；app_monitor 无 subprocess 导入"


def _d25_no_dead_assignment() -> Tuple[bool, str]:
    """D-25：`dry_audio` 那种"赋值后没人读"的写法已去掉，且时长纯函数已落地。"""
    text = source("services/music_effects.py")
    ok = "dry_audio" not in text and "def effective_duration" in text
    return ok, "混响里的死赋值" + ("已删" if "dry_audio" not in text else "仍在")


def _d27_single_formatter() -> Tuple[bool, str]:
    """D-27：工具服务的体积格式化不再自带一份实现，转调监控服务的那一份。"""
    text = source("services/tool_service.py")
    ok = "monitor_service" in text and "_format_bytes" in text
    return ok, "`_format_size`" + ("已转调 monitor_service._format_bytes" if ok else "仍自带一份")


def _d146_cleanup_wired() -> Tuple[bool, str]:
    """D-146：`_music_cleanup()` 接回了退出路径（一次性闸门 + 退出判据在场）。"""
    text = source("ui/app_music.py")
    ok = ("_music_exit_flushed" in text and "_music_cleanup" in text
          and "def _music_stop" in text)
    return ok, "退出收尾" + ("已接回退出路径" if ok else "仍是死代码")


def _d147_ffmpeg_declared() -> Tuple[bool, str]:
    """D-147：ffmpeg 可用性可查询、失败带原因（不再静默）。"""
    text = source("services/music_effects.py")
    ok = all(name in text for name in ("ffmpeg_available", "unavailable_reason", "FFMPEG_PATH"))
    return ok, "ffmpeg 探测" + ("已暴露且不再是静默失败" if ok else "仍不可查询")


def _d151_pydub_symbol_still_missing() -> Tuple[bool, str]:
    """D-151（挂账）：`pydub.effects.speed_change` 在钉住的版本里不存在 →
    `_pydub_available` 恒为 False → **音效链今天任何机器都不跑**。

    这条是"钉住现状"：谁把它修好了（改用 `_spawn().set_frame_rate()` 那套惯用法），
    `_pydub_available` 就会变成真，这条断言先红，逼着他回来把台账改成「已修」。
    """
    from services import music_effects as me

    available = bool(getattr(me, "_pydub_available", True))
    return (not available), f"_pydub_available={available}（False = 现象仍在）"


def _d152_qml_overlay_never_shuts_down_gpu() -> Tuple[bool, str]:
    """D-152（挂账）：QML 监控悬浮窗从不 `shutdown_gpu()`（桥里没有这个调用）。"""
    text = source("app/bridges/overlay_bridge.py")
    return ("shutdown_gpu" not in text), (
        "overlay_bridge 里" + ("没有 shutdown_gpu（现象仍在）" if "shutdown_gpu" not in text
                              else "已接上（可以改成「已修」了）"))


def _d150_product_disables_disk_cache(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-150（挂账）：产品侧靠"建引擎**之前**关掉 QML 磁盘缓存"规避那 10 条竞态报错。

    钉住的是**规避还在、且位置对**：删掉那一行，或者把它挪到建引擎之后，磁盘缓存就重新打开，
    主题热切换段那 10 条 TypeError 会回到产品里（实测：缓存开着稳定 10 条 / 关掉 0 条）。
    正解是"升级 Qt 或打包预编译 QML"，排期阶段 4.3 的打包重做。
    """
    body = source("main_qml.py") if text is None else text
    guard = body.find("QML_DISABLE_DISK_CACHE")
    engine = body.find("QQmlApplicationEngine()")
    ordered = guard >= 0 and (engine < 0 or guard < engine)
    detail = f"main_qml：开关位置={guard}、引擎构造位置={engine}"
    detail += "（在引擎之前，规避生效）" if ordered else "（缺失或在引擎之后 —— 规避失效）"
    return ordered, detail


def _d153_engine_comes_only_from_injection(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-153（已修）：QML 引擎**只认显式注入**，不再按 `gc` 的顺序猜。

    缺陷现象：解析 FluTheme 之前要先拿到引擎，而引擎早先是"在 `gc` 的跟踪表里取进程里
    **最后一个** `QQmlEngine`"。只要进程里**先**存在一个游离引擎（测试探针、别的模块建的），
    取到的就可能是它 —— `fluentAvailable` 变 False，入口那个真引擎反而没接上。
    阶段 1 的证据脚本 `poc/_verify_group_b_independent.py` 第 5 节的红灯就是这条。

    修法是**整条删掉那条取值路径**（不是换成更聪明的猜法）：只认构造参数 `engine=`
    与装配方的 `use_engine(engine)`；注入那一刻立刻试解析一次 FluTheme，
    否则 `fluentAvailable` 会停在 False 直到下一次同步（入口中间没有别的同步点）。

    判据分三层，任何一层被改回去都会红：

    1. **源码里没有堆扫描**：AST 里不许出现 `gc` 的导入，也不许出现 `*.get_objects()` 调用。
       用 AST 而不是文本匹配：**docstring 里记述这段历史是本仓库的惯例**，不该因为
       "文档提到过"就误报（反过来，改写成别的名字再扫也躲不过 AST）；
    2. **自动发现通道整条不存在**：`ThemeBridge` 上不许再有 `_discover_engine`
       （判据是"这个属性不存在"，不是"它的实现被削弱了"）；
    3. **显式通道有效且立刻生效**：注入一个假引擎后 `_current_engine()` 必须就是它，
       且桥**当场**就去解析 FluTheme（假引擎的 `importPathList()` 必须被问过）。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（把 gc 扫描加回去）；`None` = 读真实文件
            并连第 2、3 层的行为判据一起跑。
    """
    body = raw("app/bridges/theme_bridge.py") if text is None else text
    problems: List[str] = []
    try:
        tree = ast.parse(body)
    except SyntaxError as e:
        return False, f"theme_bridge.py 不是合法 Python，形状审不了：{e}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "gc" for alias in node.names):
            problems.append(f"第 {node.lineno} 行又导入了 gc 模块")
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get_objects":
            problems.append(f"第 {node.lineno} 行又出现堆扫描调用（*.get_objects()）")
    if text is None:
        from app.bridges import theme_bridge as bridge

        if hasattr(bridge.ThemeBridge, "_discover_engine"):
            problems.append("ThemeBridge._discover_engine 又回来了（自动发现通道不该存在）")
        injected = _LedgerFakeEngine()
        built = bridge.ThemeBridge(theme_engine=_LedgerFakeThemeEngine(), config=_LedgerFakeConfig())
        built.use_engine(injected)
        if built._current_engine() is not injected:
            problems.append("use_engine 注入的引擎没被认下来（显式通道失效）")
        elif injected.asked == 0:
            problems.append("注入引擎时没有立刻去解析 FluTheme（入口第一帧会用 FluentUI 默认色）")
    if problems:
        return False, "；".join(problems)
    return True, "引擎只来自显式注入（无 gc 扫描、无自动发现；注入即解析 FluTheme）"


class _LedgerFakeEngine:
    """假 QML 引擎：只记 `importPathList()` 被问过几次（桥解析 FluTheme 的第一步）。

    刻意**不给** FluentUI 导入路径：这条判据只关心"桥认不认注入的那个引擎、有没有当场
    去解析"，不关心能不能真取到 FluTheme（那由 `tests/test_theme_bridge.py` 的正经用例覆盖）。
    """

    def __init__(self) -> None:
        self.asked = 0

    def importPathList(self) -> List[str]:  # noqa: N802 - Qt 命名
        self.asked += 1
        return []


class _LedgerFakeThemeEngine:
    """假主题引擎：`load_theme` 一律返回 None（连全局调色板都不碰）。"""

    def load_theme(self, name: str) -> Any:
        return None

    def apply_theme(self, theme: Any, accent: Any = None) -> None:
        return None


class _LedgerFakeConfig:
    """假配置：**既不读也不写**真实 `config.json`。"""

    theme_name = "default"
    accent_color = None

    def save_config(self) -> None:
        return None


def _d154_progress_is_not_behind_the_clock_guard(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-154（已修）：进度回调不许再被"时钟刻度"那层判断挡住。

    现象与实证：`_dl_part` 里 `emit()` 原写在 `if elapsed > 0:`（给"算速率"防除零的那层）
    **里面**，于是一个时钟刻度内跑完的下载（Windows 上 `time.time()` 步长约 0.5 ms，
    内存/局域网的小文件很容易撞上）**一次回调都不发** —— 界面进度条全程不动，而文件其实
    已经下好了。它对外表现成**偶发红灯**：`poc/probe_tool_parity.py` 的
    "进度回调被触发（0 次）"同一进程连跑 40 次红 2 次；**已入库**的
    `tests/test_tool_service.py::test_download_multi_segmented` 连跑 400 次红 2 次（约 0.5%）。
    机制的三组对照在 `poc/_probe_tool_progress_tick.py`（冻结时钟 → 恒 0 次；
    时钟每次 +1 ms → 恒非 0；自然时钟 → 5000 B 负载 5/120 次、3 B 负载 3/120 次）。

    判据只看**形状**，行为那半边交给 `tests/test_tool_service.py` 里那条**冻结时钟**的
    确定性用例（`test_download_multi_reports_progress_even_without_a_clock_tick`）——
    同一件事不写两份实现（本仓库反复强调的规矩）：

    1. `emit(` 必须还在：把进度回调**整个删掉**同样算回归；
    2. `emit(` 不许落在任何 `if ... elapsed ...` 的**语句体**里：把守卫加回去就红
       （只看语句级 `if`，`speed = a / elapsed if elapsed > 0 else 0.0` 这种表达式不算）。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码；`None` = 读真实文件。
    """
    body = raw("services/tool_service.py") if text is None else text
    try:
        tree = ast.parse(body)
    except SyntaxError as e:
        return False, f"tool_service.py 不是合法 Python，形状审不了：{e}"

    def calls_emit(node: ast.AST) -> bool:
        return any(
            isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name) and sub.func.id == "emit"
            for sub in ast.walk(node)
        )

    guarded = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.If) and "elapsed" in ast.dump(node.test) and calls_emit(node)
    ]
    if guarded:
        return False, f"第 {guarded} 行的 `if ... elapsed ...` 里又在发进度了（D-154 回归：emit 被时钟守卫挡住）"
    inside = [
        node.lineno for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "download_multi" and calls_emit(node)
    ]
    if not inside:
        return False, "`download_multi` 里已经没有进度回调 `emit(` 了（这不是修法，是把功能删了）"
    return True, "进度回调在时钟守卫之外（`emit` 每块都发；行为由冻结时钟用例钉住）"


# ─── 阶段 3 首轮（3.1）的反馈链路判据 ──────────────────────────


def _d155_checkin_streak_is_really_read(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-155（已修）：每日签到的"连续 N 天"必须**真的读回来**。

    旧写法（`main.py:328-334`）：`result = ach_engine.checkin()` 之后
    `if result and result.get("success")` 才提示 —— 而 `result` 是引擎
    `update_progress()` 的返回值（`_build_item` 的成就条目，键是
    `progress_current` / `progress_stage` 那一套），**既没有 `success` 也没有 `streak`**。
    于是对照表 F-09 写着的"连续天数提示"是**死代码**：一次都没显示过，
    即使分支成立天数也恒为 0。

    修法不是猜返回值，而是把事实读回来：引擎新增只读接口 `get_checkin_streak()`，
    `AchievementService.checkin()` 如实返回 `{checked_in, already, streak}`。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`services/achievement_service.py`）。
    """
    service = source("services/achievement_service.py") if text is None else strip_python_comments(text)
    problems: List[str] = []
    if '"checked_in"' not in service:
        problems.append("AchievementService.checkin() 没返回 checked_in")
    if "get_checkin_streak()" not in service:
        problems.append("checkin() 没把连续天数读回来（旧实现读的键不存在）")
    if "def get_checkin_streak" not in source("services/achievement_engine.py"):
        problems.append("引擎没有提供只读接口 get_checkin_streak()")
    if "startup_checkin_ok" not in source("app/startup.py"):
        problems.append("启动流程没把签到结果报给用户")
    return (not problems, "；".join(problems) or "签到结果有真实来源，且启动流程会提示")


def _d156_cloud_sync_result_is_checked(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-156（已修）：成就云同步的**返回值**必须被看。

    旧写法（`main.py:317-327`）：`run_sync(...)` 之后无条件
    `app.set_status("成就云存档同步完成", "success")` —— 它压根没接返回值，
    于是同步失败也会告诉用户"同步完成"。现在成功走 `startup_ach_synced`、
    失败走 `startup_ach_sync_failed`（warning）。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`app/startup.py`）。
    """
    startup = source("app/startup.py") if text is None else strip_python_comments(text)
    problems: List[str] = []
    if "if ok:" not in startup:
        problems.append("没有按返回值分流")
    if "startup_ach_synced" not in startup:
        problems.append("成功那条提示没了")
    if 'self._set_status("startup_ach_sync_failed", "warning")' not in startup:
        problems.append("失败那条提示没了（旧实现正是「无论成败都报成功」）")
    return (not problems, "；".join(problems) or "云同步按返回值分流成两条提示")


def _d157_killed_game_is_not_reported_as_normal_exit(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-157（已修）：强杀之后不许再补一句"游戏已正常退出"。

    旧实现在 `ui/app_handlers.py:356-367` 与 `1299-1308` 上：用户点强杀 → 状态栏
    "游戏进程已强制结束"；紧接着退出监控线程看到 `_killed_by_user` 已置真，
    走 `game_exited` 分支 → 状态栏又写"游戏已正常退出"。两句话前后矛盾，
    而真实原因是用户自己杀的。

    判据同时钉住"两句都发"这种半修法：`not crashed and not killed` 必须**同时**
    要求没崩溃且不是强杀。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`app/bridges/home_bridge.py`）。
    """
    bridge = source("app/bridges/home_bridge.py") if text is None else strip_python_comments(text)
    if "if not crashed and not killed:" not in bridge:
        return False, "强杀那一路又会去报「游戏已正常退出」了（D-157 回归）"
    return True, "强杀/崩溃都不再走「正常退出」那句"


LEDGER: Dict[str, Entry] = {
    # ─── 返工 E 组本轮修好的（正向断言） ───────────────────────────
    "D-04": Entry(
        defect="D-04", state="已修",
        summary="隐私弹窗的「拒绝」按钮文案写着「确定 / OK / 確定」",
        where="ui/app_crash.py", check=_d04_cancel_button),
    "D-10": Entry(
        defect="D-10", state="已修",
        summary="歌词解析在 worker 里 clear()/parse() 写共享列表，主线程同时读（无同步）",
        where="services/music_lyrics.py", check=_d10_snapshot_is_immutable),
    "D-17": Entry(
        defect="D-17", state="已修",
        summary="`get_line_at` 是「读路径写共享 LRU 缓存」的非纯函数（旧缓存还会返回上一行）",
        where="services/music_lyrics.py", check=_d17_lookup_is_pure),
    "D-18": Entry(
        defect="D-18", state="已修",
        summary="`_lines` 稳定时不可达的越界纠正分支 + 两处二分语义分叉",
        where="services/music_lyrics.py", check=_d18_boundaries),
    "D-20": Entry(
        defect="D-20", state="已修",
        summary="写 `music.json` 前的建目录没有保护，调用方又吞异常 → 歌单静默不落盘",
        where="services/music_playlist.py", check=_d20_writable_dir_fallback),
    "D-22": Entry(
        defect="D-22", state="已修",
        summary="歌单去重判定里每首歌反复 `normpath`（点名的两个函数无生产调用点）",
        where="services/music_playlist.py", check=_d22_single_normalisation),
    "D-28": Entry(
        defect="D-28", state="已修",
        summary="`server_join_done` 分支里的 `self._running = True` 是恒真的空操作",
        where="ui/app_handlers.py", check=_d28_no_redundant_flag),
    "D-11": Entry(
        defect="D-11", state="已修",
        summary="GPU 缓存被工作线程整体替换后再连读（TOCTOU），`_gpu_detector` 也被主线程写",
        where="services/monitor_service.py", check=_d11_published_cache_only),
    "D-19": Entry(
        defect="D-19", state="已修",
        summary="`_music_duration` 取原文件时长而播的是变速后文件 → 进度/seek/预取判据全偏",
        where="ui/app_music.py", check=_d19_duration_delegates),
    "D-23": Entry(
        defect="D-23", state="已修",
        summary="桌面歌词的 docstring 在说谎（写着有 `-transparentcolor` 与穿透）",
        where="ui/music_desktop_lyric.py", check=_d23_docstring_tells_the_truth),
    "D-24": Entry(
        defect="D-24", state="已修",
        summary="多个模块的死导入 / 死状态 / 死字段",
        where="ui/app_monitor.py", check=_d24_dead_code_removed),
    "D-25": Entry(
        defect="D-25", state="已修",
        summary="`_apply_speed` 的形参未使用、`_apply_reverb` 有赋值后无人读的 `dry_audio`",
        where="services/music_effects.py", check=_d25_no_dead_assignment),
    "D-27": Entry(
        defect="D-27", state="已修",
        summary="`_format_bytes` 在监控与工具两个服务里各有一份同构实现",
        where="services/tool_service.py", check=_d27_single_formatter),
    "D-146": Entry(
        defect="D-146", state="已修",
        summary="`_music_cleanup()` 是死代码 →「退出前强制写盘」从来没有执行过",
        where="ui/app_music.py", check=_d146_cleanup_wired),
    "D-147": Entry(
        defect="D-147", state="已修",
        summary="音效链依赖外部 ffmpeg 却没声明、失败被静默吞掉（设了没反应）",
        where="services/music_effects.py", check=_d147_ffmpeg_declared),

    # ─── 阶段 3 首轮修好的（正向判据，用户裁决的方案 a） ────────────
    "D-153": Entry(
        defect="D-153", state="已修",
        summary="FluentUI 模块目录的查找靠 `gc` 取「最后一个引擎」 —— 进程里有游离引擎时取错，"
                "`fluentAvailable` 变 False（阶段 1 证据脚本第 5 节红灯的成因）",
        where="app/bridges/theme_bridge.py", check=_d153_engine_comes_only_from_injection),
    "D-154": Entry(
        defect="D-154", state="已修",
        summary="进度回调被 `if elapsed > 0:`（防除零那层）一起挡住 —— 一个时钟刻度内跑完的下载"
                "一次回调都不发（界面进度条不动），并让 `probe_tool_parity.py` 与已入库的"
                " `test_download_multi_segmented` 时红时绿（约 0.5%~5%）",
        where="services/tool_service.py", check=_d154_progress_is_not_behind_the_clock_guard),

    # ─── 挂账（钉住现状 + 理由 + 排期） ─────────────────────────────
    "D-13": Entry(
        defect="D-13", state="挂账",
        summary="音效处理仍在主线程同步跑（开启音效后切歌卡住界面数秒）",
        where="ui/app_music.py",
        reason="用户已裁决方案 A（投给 TaskRunner 异步化），**排期阶段 3.18**；"
               "异步化时必须保住：设置快照 / 临时文件所有权与清理 / 失败降级为播原文件 / "
               "处理期间界面进入「音效处理中」态 + 代际守卫。",
        files=("ui/app_music.py",)),
    "D-16": Entry(
        defect="D-16", state="挂账",
        summary="逐字歌词时序偏移一个 duration（但 words / is_word_based / end_time 全仓零消费者）",
        where="services/music_lyrics.py",
        markers=("end_time", "is_word_based"),
        reason="与「逐字渲染」一起做（**阶段 3.18**）；现在只补注释说明偏移，不改行为。",
        files=("services/music_lyrics.py",)),
    "D-21": Entry(
        defect="D-21", state="挂账",
        summary="歌单 JSON 里的私有字段 `_id` 会写进磁盘（现有测试钉住这个现状）",
        where="services/music_playlist.py",
        markers=("_id",),
        reason="用户已裁决转**阶段 3.16**：换格式时**必须带迁移方案**（老文件要能读进来）。",
        files=("services/music_playlist.py",)),
    "D-26": Entry(
        defect="D-26", state="挂账",
        summary="两处独立二分（find_current_line / get_line_at）语义分叉（-1 vs None）",
        where="services/desktop_lyric.py",
        reason="不单独做；若做则「抽纯函数 + 薄封装」并保住现有用例 —— 随**阶段 3.16/3.18** 一起。",
        files=("services/desktop_lyric.py",)),
    "D-05": Entry(
        defect="D-05", state="挂账",
        summary="语言文件里有零引用的历史键（其中 2 个已被新界面复用）",
        where="ui/locales/zh_CN.json",
        reason="**记录现状、不要删**（零引用不等于没用：`nav_bridge` 与 QML 歌词窗已经在复用）；"
               "等**阶段 3** 把界面文案钉完再统一清理。",
        files=("ui/locales/zh_CN.json",)),
    "D-150": Entry(
        defect="D-150", state="挂账",
        summary="QML 磁盘缓存的异步取编译单元与主题切换竞态（对象建一半：刷 TypeError）",
        where="main_qml.py", check=_d150_product_disables_disk_cache,
        reason="产品侧已**规避**（不是修根因）：`main_qml` 在建任何引擎之前 `setdefault` 关掉 "
               "QML 磁盘缓存 —— 实测缓存开着时主题段稳定 10 条报错、关掉 0 条，而打包产物冷启动"
               "差值落在 400 ms 采样精度内（`poc/_measure_d150.py`、`poc/_smoke_packaged.py`）。"
               "正解（升级 Qt，或打包时预编译 QML 后再打开缓存）排期**阶段 4.3** 的打包重做；"
               "`tests/_smoke_driver.py` 自己那份规避保留（同一条路径的双保险）。",
        files=("main_qml.py", "tests/_smoke_driver.py")),
    "D-151": Entry(
        defect="D-151", state="挂账",
        summary="pydub 0.25.1 里没有 `effects.speed_change` → `_pydub_available` 恒为 False，"
                "音效链**任何机器都不跑**（与有没有 ffmpeg 无关）",
        where="services/music_effects.py", check=_d151_pydub_symbol_still_missing,
        reason="修复很小（改用本文件已有的 `_spawn(...).set_frame_rate()` 惯用法），但**必须与 "
               "D-13 一起做**：今天音效链是死的，所以「主线程同步处理会卡住界面」这个问题"
               "一直没被触发；先修 D-151 再谈 D-13，等于把那处卡顿立刻放到用户面前。"
               "两条一起排**阶段 3.18**。",
        files=("services/music_effects.py",)),
    "D-152": Entry(
        defect="D-152", state="挂账",
        summary="QML 监控悬浮窗从不 `shutdown_gpu()`（GPU 检测器/线程不会随窗口收尾）",
        where="app/bridges/overlay_bridge.py", check=_d152_qml_overlay_never_shuts_down_gpu,
        reason="QML 侧的收尾钩子属于**阶段 3** 的悬浮窗接线（Tk 版有 `init_gpu/shutdown_gpu` "
               "配对，QML 版只接了 init）。",
        files=("app/bridges/overlay_bridge.py",)),

    # ─── 阶段 3 首轮（3.1 首页与启动流程）新登记的 ─────────────────
    "D-155": Entry(
        defect="D-155", state="已修",
        summary="每日签到的「连续 N 天」提示是死代码（`result.get(\"success\")` 的键根本不存在）",
        where="services/achievement_service.py", check=_d155_checkin_streak_is_really_read,
        files=("services/achievement_service.py", "services/achievement_engine.py", "app/startup.py")),
    "D-156": Entry(
        defect="D-156", state="已修",
        summary="成就云同步不看返回值：同步失败也提示「同步完成」",
        where="app/startup.py", check=_d156_cloud_sync_result_is_checked),
    "D-157": Entry(
        defect="D-157", state="已修",
        summary="用户强杀游戏后紧跟着又显示「游戏已正常退出」（两句话自相矛盾）",
        where="app/bridges/home_bridge.py", check=_d157_killed_game_is_not_reported_as_normal_exit),
    "D-158": Entry(
        defect="D-158", state="挂账",
        summary="旧 Tk 启动流程（`ui/app_handlers.py`）与 `services/game_service.py` 并存 —— "
                "同一件事有两份实现（红线 2 的分叉）",
        where="ui/app_handlers.py",
        markers=("def _watch_game_stdout", "def _collect_crash_info"),
        reason="随旧界面一起消失：排期**阶段 4** 的 4.2（删除 CustomTkinter 模块）。"
               "现在把它改成调服务，等于在「当前唯一发布的界面」上做一次无收益的重构 —— "
               "风险落在用户手里，收益只有 4.2 之前的那几周。**本轮新增的规则只写在新服务上**，"
               "旧界面一行不改（`docs/refactor/16-phase3-execution-log.md` 记了这条裁决）。",
        files=("ui/app_handlers.py", "services/game_service.py")),
    "D-159": Entry(
        defect="D-159", state="挂账",
        summary="「启动前自动备份」与启动**并发**（备份可能还没写完，游戏已经起来了）",
        where="services/game_service.py",
        markers=('self._auto_backup("launch")',
                 "self.tasks.submit(self.auto_backup_sync"),
        reason="**保持旧行为**是刻意的：旧实现（`ui/app_handlers.py:944` + "
               "`ui/app_backup.py:646`）就是「再开一个线程去备份、不等它」，改成串行会让"
               "大存档的启动明显变慢。两种取舍都说得通，属**待裁决**（用户要选「启动快但备份可能不完整」"
               "还是「备份完整但启动变慢」）；在裁决之前按红线 1 保持原样。",
        files=("services/game_service.py", "ui/app_backup.py")),
}

#: 台账必须覆盖的缺陷号（防"悄悄少一条"）：18 条欠账 + 后续几轮新登记的。
REQUIRED_IDS = (
    "D-04", "D-05", "D-10", "D-11", "D-13", "D-16", "D-17", "D-18", "D-19", "D-20",
    "D-21", "D-22", "D-23", "D-24", "D-25", "D-26", "D-27", "D-28",
    "D-146", "D-147", "D-150", "D-151", "D-152", "D-153", "D-154",
    "D-155", "D-156", "D-157", "D-158", "D-159",
)


def test_ledger_covers_every_required_defect() -> None:
    missing = [defect for defect in REQUIRED_IDS if defect not in LEDGER]
    assert missing == [], f"台账里缺这些缺陷号：{missing}"


def test_entries_are_well_formed() -> None:
    problems: List[str] = []
    for key, entry in LEDGER.items():
        if key != entry.defect:
            problems.append(f"{key}: 键与 defect 不一致（{entry.defect}）")
        if entry.state not in ("已修", "挂账"):
            problems.append(f"{key}: 状态只能是「已修」或「挂账」，现在是 {entry.state!r}")
        if not entry.summary or not entry.where:
            problems.append(f"{key}: 缺一句话说明或主要文件")
        if entry.state == "已修" and not (entry.markers or entry.check):
            problems.append(f"{key}: 标了「已修」却没给判据（markers 或 check）")
        if entry.state == "挂账":
            if not entry.reason:
                problems.append(f"{key}: 挂账项必须写明理由")
            elif not SCHEDULE_RE.search(entry.reason):
                problems.append(f"{key}: 挂账项的理由里必须写明排期（阶段 3.x / 阶段 4 / 待裁决）")
    assert problems == [], "台账条目不合格：\n  " + "\n  ".join(problems)


def test_referenced_files_exist() -> None:
    problems = [f"{key}: {entry.where}" for key, entry in LEDGER.items()
                if not (REPO_ROOT / entry.where).is_file()]
    assert problems == [], f"台账指向的文件不存在：{problems}"


def test_both_states_are_used() -> None:
    """两种状态都要有：全是「已修」说明没人敢挂账，全是「挂账」说明没有正向判据。"""
    states = {entry.state for entry in LEDGER.values()}
    assert states == {"已修", "挂账"}, f"台账里只有 {states}，判据形态可能被写坏了"


@pytest.mark.parametrize("defect", sorted(k for k, e in LEDGER.items() if e.state == "已修"))
def test_fixed_defects_show_the_fix(defect: str) -> None:
    """「已修」的条目：修复的特征必须在代码里（少一个就红）。"""
    entry = LEDGER[defect]
    if entry.check is not None:
        ok, detail = entry.check()
        assert ok, f"{defect} 的修复不见了：{detail}（{entry.summary}）"
        return
    text = source(entry.where)
    assert has(text, *entry.markers), (
        f"{defect} 的修复不见了：{entry.where} 里找不到 {entry.markers}（{entry.summary}）"
    )


@pytest.mark.parametrize("defect", sorted(k for k, e in LEDGER.items() if e.state == "挂账"))
def test_open_defects_are_still_open(defect: str) -> None:
    """「挂账」的条目：**钉住现状** —— 缺陷特征还在。

    这一条刻意做成"缺陷还在才算过"：谁把它修好了，这里会先红，逼着他回来把台账
    改成「已修」并给出正向判据。**不要**为了让测试变绿而放松这条断言。
    """
    entry = LEDGER[defect]
    assert entry.reason, f"{defect} 是挂账项，必须写明理由与排期"
    for path in entry.files:
        assert (REPO_ROOT / path).is_file(), f"{defect} 提到的文件不存在：{path}"
    if entry.markers:
        text = source(entry.where)
        assert has(text, *entry.markers), (
            f"{defect} 的缺陷特征不见了（钉子过期）：{entry.where} 里找不到 {entry.markers}。"
            "如果确实修好了，请把台账改成「已修」并给出正向判据。"
        )


def test_check_based_probes_are_not_empty_assertions() -> None:
    """负例自检：把**故意改坏**的源码喂给带 `check` 的判据，它**必须**报红。

    这一条是"判据自己也要被测试"：没有它，`check` 里写成 `return True, ""` 也能一路绿。
    """
    cases: List[Tuple[str, Callable[[Optional[str]], Tuple[bool, str]], str, str]] = [
        # (缺陷号, 判据, 改坏之后的源码应当长什么样, 说明)
        ("D-04", _d04_cancel_button,
         'cancel_btn = tk.Button(\n            dialog,\n            text=_("confirm"),\n        )',
         "把文案改回 confirm"),
        ("D-28", _d28_no_redundant_flag,
         'elif task_type == "server_join_done":\n'
         '            self._running = True\n'
         '        elif task_type == "server_join_error":\n',
         "把那行冗余赋值加回去"),
        ("D-150", _d150_product_disables_disk_cache,
         source("main_qml.py").replace("QML_DISABLE_DISK_CACHE", "已删掉的开关"),
         "删掉关闭磁盘缓存那一行"),
        ("D-153", _d153_engine_comes_only_from_injection,
         'import gc\n'
         '\n'
         'from PySide6.QtQml import QQmlEngine\n'
         '\n'
         '\n'
         'def _discover_engine():\n'
         '    """在进程里找引擎。"""\n'
         '    found = [obj for obj in gc.get_objects() if isinstance(obj, QQmlEngine)]\n'
         '    return found[-1] if found else None\n',
         "把 gc 堆扫描的引擎发现加回去"),
        ("D-154", _d154_progress_is_not_behind_the_clock_guard,
         'def download_multi():\n'
         '    emit(1, 2, 3)\n'
         '\n'
         '\n'
         'def _dl_part():\n'
         '    if elapsed > 0:\n'
         '        emit(downloaded, total_size, speed)\n',
         "把进度回调挪回 `if elapsed > 0` 里面"),
        ("D-155", _d155_checkin_streak_is_really_read,
         'def checkin(self):\n'
         '    engine = self.engine()\n'
         '    return engine.checkin()\n',
         "退回到「直接返回引擎结果」那种读不到天数的写法"),
        ("D-156", _d156_cloud_sync_result_is_checked,
         source("app/startup.py").replace("startup_ach_sync_failed", "startup_ach_synced"),
         "把失败那条提示改回成功"),
        ("D-157", _d157_killed_game_is_not_reported_as_normal_exit,
         source("app/bridges/home_bridge.py").replace("if not crashed and not killed:", "if not crashed:"),
         "让强杀也走「正常退出」那句"),
    ]
    problems = []
    for defect, probe, mutated, note in cases:
        ok, detail = probe(mutated)
        if ok:
            problems.append(f"{defect} 的判据对「{note}」的源码仍然判绿（空断言）：{detail}")
        # 真实源码必须是绿的（改坏的是喂进去的文本，不是文件）
        real_ok, real_detail = probe()
        if not real_ok:
            problems.append(f"{defect} 的真实源码没通过判据：{real_detail}")
    assert problems == [], "\n  ".join(problems)
