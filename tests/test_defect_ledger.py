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
**阶段 3 任务 3.1 开发中登记的 5 条**（D-155 ~ D-157 已修：签到提示是死代码 / 云同步不看返回值 /
强杀后又报"正常退出"；D-158 / D-159 挂账：旧 Tk 启动流程并存、启动前备份与启动并发），
以及 **3.1 人工验收当场发现的 4 条**（D-160 语言不跟配置 / D-161 卡片页脚抢高度 /
D-162 协议只显示摘要 / D-163 析构期 TypeError，全部已修）。
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


def _d160_language_comes_from_the_config() -> Tuple[bool, str]:
    """D-160（已修）：界面语言必须来自 `config.json`。

    根因：`main_qml` 里 `TrBridge()` 是**无参**构造的，而构造函数当年写的是
    `self._config = config`（形参默认 None）—— 于是 `_boot()` 拿不到"首选语言"，
    直接退回系统语言（中文系统 = zh_CN），配置里选的 en_US 被彻底忽略。
    修法：不注入时取根模块的 `config` 单例（与 `AppContext.config` 同一条思路）。
    """
    bridge = source("app/bridges/tr_bridge.py")
    problems: List[str] = []
    if "_root_config()" not in bridge:
        problems.append("没有取根模块 config 的兜底")
    if "self._config = config if config is not None else _root_config()" not in bridge:
        problems.append("构造期仍是 `self._config = config`（None 就没人管）")
    if "def _root_config" not in bridge:
        problems.append("没有 _root_config() 这个取配置的小函数")
    return (not problems, "；".join(problems) or "不注入时取根模块 config，语言跟着配置走")


def _d161_card_footer_does_not_steal_height(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-161（已修）：`FmCard` 的页脚不许跟着卡片一起被拉伸。

    在 `ColumnLayout` 里只写 `Layout.alignment: Qt.AlignRight`（只有水平分量）时，
    Qt Quick Layouts 认为纵向没有约束，把它也算成"可拉伸项"，**与正文平分**多出来的
    高度。实测：`FmPage` 的内容卡 699 高 → `fmCardBody` 322 / `fmCardFooter` 322，
    正文只有一半可视区，首页四张卡只看得见前两张（用户 3.1 验收报的"皮肤与成就总览消失"）。
    页面侧的配套判据在 `tests/test_home_page_qml.py`（正文要撑满、四张卡都要在可视区内）。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`qml/components/FmCard.qml`）。
    """
    card = (REPO_ROOT / "qml/components/FmCard.qml").read_text(encoding="utf-8") if text is None else text
    if "Layout.fillHeight: false" not in card:
        return False, "页脚又只靠 alignment 约束了（会与正文平分高度）"
    return True, "页脚显式 fillHeight: false"


def _d162_terms_full_text_is_wired() -> Tuple[bool, str]:
    """D-162（已修）：协议弹窗要显示 `TERMS_OF_USE.md` **全文**。

    旧弹窗（`ui/app_handlers.py:747-861`）渲染的是全文（110 行 / 16 KB）；
    QML 版一度只显示语言文件里那句摘要（`terms_content`）—— 功能丢失（红线 1）。
    """
    service = REPO_ROOT / "services/legal_service.py"
    startup = source("app/startup.py")
    dialogs = (REPO_ROOT / "qml/StartupDialogs.qml").read_text(encoding="utf-8")
    problems: List[str] = []
    if not service.is_file():
        problems.append("没有 services/legal_service.py")
    if "def termsText" not in startup:
        problems.append("启动流程没有把协议全文暴露给 QML")
    if "Startup.termsText" not in dialogs:
        problems.append("协议弹窗没有读 Startup.termsText")
    if "Text.MarkdownText" not in dialogs:
        problems.append("全文没有按 Markdown 渲染（会看到一堆 # 与 **）")
    if "terms_content" not in dialogs:
        problems.append("读不到文件时的摘要兜底没了")
    return (not problems, "；".join(problems) or "协议全文经 services/legal_service 读到 QML 并渲染")


def _d163_component_bindings_survive_teardown(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-163（已修）：`FmButton` 的绑定要在**对象被销毁那一刻**活下来。

    现象（视觉回归的"整轮零 TypeError"判据实测稳定复现）：`StackView` 弹页 / 进程退出时，
    QML 会先拆掉对象的元对象，而绑定还排在求值队列里 —— 那一刻 `control` 还在、
    `control.borderColor` 已经**不是函数**了，于是每销毁一个按钮就刷两条
    `TypeError: Property 'borderColor' … is not a function`（探针一轮 10 条）。
    与全局那条"上下文属性析构时先被清空，所以一律 `Theme?.x ?? 兜底`"是同一类问题，
    兜底写法也一致：**先判函数在不在，再调**。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`qml/components/FmButton.qml`）。
    """
    button = (REPO_ROOT / "qml/components/FmButton.qml").read_text(encoding="utf-8") if text is None else text
    problems: List[str] = []
    if "control.borderColor ? control.borderColor()" not in button:
        problems.append("border.color 又是裸调 `control.borderColor()` 了")
    if "control.faceColor ? control.faceColor()" not in button:
        problems.append("color 又是裸调 `control.faceColor()` 了")
    return (not problems, "；".join(problems) or "两个绑定都先判函数在不在")


def _d164_scan_survives_a_missing_core(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-164（已修）：核心还没注册进上下文时，版本列表要**降级**而不是整页出错。

    现象（视觉探针的装配实测）：`VersionsBridge.bind()` 发生在装配期，那时启动链条
    还没把 `launcher` 注册进上下文 —— 旧写法直接回 `launcher_unavailable`，
    于是版本页是一整块"出错"（`loadState=error`），用户要手动刷新才恢复；
    而磁盘上的版本其实看得见。

    修法两条：①`VersionService._scan_sync` 在核心缺席时**降级为按目录名列举**
    （元数据拿不到就先显示目录名）；②桥接 `Startup.chainFinished`，链条跑完再扫一次
    把加载器/游戏版本补齐。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`services/version_service.py`）。
    """
    service = source("services/version_service.py") if text is None else strip_python_comments(text)
    bridge = source("app/bridges/version_bridge.py")
    problems: List[str] = []
    if "def _scan_folders" not in service:
        problems.append("没有降级扫描（核心缺席就只剩报错这一条路）")
    if "rows = self._scan_folders()" not in service:
        problems.append("核心缺席的分支没有走降级扫描")
    if "def on_chain_finished" not in bridge:
        problems.append("桥没有在启动链条跑完后重扫")
    if "chainFinished" not in bridge:
        problems.append("桥没有接 Startup.chainFinished")
    return (not problems, "；".join(problems) or "核心缺席时降级列目录，链条跑完自动补齐元数据")


def _d165_error_state_retry_is_primary(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-165（已修）：错误态的"重试"必须是**主按钮**。

    现象：视觉回归的"每个页面都看得到强调色"判据在版本页上判红（`accent=0`）。
    根因不是页面画错了，而是错误态里唯一该做的事（重试）用的是次按钮 ——
    整块错误态只有灰底按钮与红色图标，一点强调色都没有。
    （版本页在"桥在场、服务缺席"的装配下走的就是错误态，见 D-164 的另一半。）

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`qml/components/FmErrorState.qml`）。
    """
    state = (REPO_ROOT / "qml/components/FmErrorState.qml").read_text(encoding="utf-8") if text is None else text
    if "primary: true" not in state:
        return False, "重试按钮退回次按钮了（错误态会整页没有强调色）"
    return True, "重试按钮是主按钮"


def _d166_test_assembly_restores_the_language(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-166（已修）：真装配的模块级 fixture 用完必须**还原界面语言**。

    现象（本轮全量测试实测）：`tests/test_main_qml_entry.py` 的 `assembled` fixture
    走生产路径装配一次应用，`TrBridge` 按 `config.json` 调 `init_i18n`（D-160 的语义：
    配置里的语言优先）。用户机器上配置是 `en_US`，于是该模块之后的测试全跑在英文下 ——
    `test_music_service` 的 `assert … == "2 首"` 变成 `"2 songs"`，
    表现为"单独跑绿、全量跑红"，且**随用户配置变化**。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`tests/test_main_qml_entry.py`）。
    """
    module = (REPO_ROOT / "tests/test_main_qml_entry.py").read_text(encoding="utf-8") if text is None else text
    problems: List[str] = []
    if "_restore_interface_language" not in module:
        problems.append("没有还原界面语言的 fixture")
    if "i18n_service._current_language = saved_lang" not in module:
        problems.append("没把语言还原回去")
    return (not problems, "；".join(problems) or "真装配之后界面语言被还原")


def _d167_repair_reuses_the_install_chain(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-167（已修）：修复要**真的把坏文件下回来**，而且与校验用**同一份文件清单**。

    现象（3.2 交付时如实挂账）：对照表 B-22 写的是"校验修复"，而核心只有
    `verify_installed_version()`（并发算哈希、返回无效清单）—— 一个重下入口都没有。
    3.3 补上了 `MinecraftLauncher.repair_installed_version()`：它按版本 JSON 算出
    应然清单（`expected_version_files()`，**校验与修复共用这一份**，两处各写一份必然分叉
    —— D-170 就是那么来的），再把"缺失 + 哈希不符"的库与主 jar 交给安装器自己的下载原语
    （`minecraft_launcher_lib._helper.download_file`，它按 sha1 跳过合格文件）重下。

    默认分支是**行为**判据而不是"源码里有某个名字"：造一个版本（一个坏库 + 缺主 jar），
    把下载函数换成会写正确字节的假件，真跑一遍修复，断言"只下了那两个、修完 remaining 为空"。
    谁把修复改成空转（或让它自己另写一份清单），这里立刻红。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`launcher/core.py`）—— 那一支只查
            "两处判据还在不在"（自检不能真去跑改坏的源码），与行为判据互补。
    """
    if text is not None:
        core = strip_python_comments(text)
        if "self.expected_version_files(version_data, versions_dir, version_id)" not in core:
            return False, "修复不再用那份共用清单了（校验与修复会各说各话）"
        if "_helper import download_file" in core:
            return True, "修复仍然复用共用清单与安装器的下载原语"
        return False, "修复不再走安装器的下载原语了"

    import hashlib
    import json
    import tempfile
    from pathlib import Path

    from launcher.core import MinecraftLauncher

    class _Stub:
        find_version_json = MinecraftLauncher.find_version_json
        _find_version_jar = MinecraftLauncher._find_version_jar
        _read_version_json_data = MinecraftLauncher._read_version_json_data
        _sha1_of = staticmethod(MinecraftLauncher._sha1_of)
        expected_version_files = MinecraftLauncher.expected_version_files
        repair_installed_version = MinecraftLauncher.repair_installed_version
        MAX_REPAIR_FILES = MinecraftLauncher.MAX_REPAIR_FILES
        MAX_REPAIR_REPORTED = MinecraftLauncher.MAX_REPAIR_REPORTED
        _get_callback = MinecraftLauncher._get_callback
        _set_status = MinecraftLauncher._set_status
        _set_progress = MinecraftLauncher._set_progress
        _set_max = MinecraftLauncher._set_max
        #: 3.3 起的「本次调用出口」三件套（D-172 之后 `_get_callback` 走它们）
        _should_report_progress = MinecraftLauncher._should_report_progress
        _emit_status = MinecraftLauncher._emit_status
        _emit_progress = MinecraftLauncher._emit_progress

        def __init__(self, root: Path) -> None:
            #: `minecraft_dir` 必须是 **Path**：核心的 `expected_version_files()`
            #: 要拿它做路径拼接（`config.minecraft_dir / "libraries" / rel`）。
            self.minecraft_dir = root / ".minecraft"
            self.config = self
            self.on_progress = None
            self.current_max = 0
            self._instance_info_cache: Dict[str, Any] = {}
            self._instance_cache_valid = False

        def get_versions_dir(self) -> Path:
            return self.minecraft_dir / "versions"

        def invalidate_instance_cache(self) -> None:
            return None

    good = b"good library"
    jar = b"client jar"
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        stub = _Stub(root)
        version_dir = stub.get_versions_dir() / "1.20.4"
        version_dir.mkdir(parents=True)
        (version_dir / "1.20.4.json").write_text(json.dumps({
            "id": "1.20.4",
            "libraries": [{
                "name": "g:b:1",
                "downloads": {"artifact": {
                    "path": "g/b/1/b-1.jar",
                    "sha1": hashlib.sha1(good).hexdigest(),
                    "url": "https://libs/b.jar",
                }},
            }],
            "downloads": {"client": {
                "sha1": hashlib.sha1(jar).hexdigest(),
                "url": "https://x/client.jar",
            }},
        }), encoding="utf-8")
        broken = stub.minecraft_dir / "libraries" / "g" / "b" / "1" / "b-1.jar"
        broken.parent.mkdir(parents=True, exist_ok=True)
        broken.write_bytes(b"tampered")

        import minecraft_launcher_lib._helper as helper

        original = helper.download_file
        downloaded: List[str] = []

        def fake_download(url: str, path: str, callback: Any = None, **kwargs: Any) -> bool:
            downloaded.append(url)
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(jar if url.endswith("client.jar") else good)
            return True

        helper.download_file = fake_download  # type: ignore[assignment]
        try:
            result = stub.repair_installed_version("1.20.4")
        finally:
            helper.download_file = original  # type: ignore[assignment]

    if not result.get("ok"):
        return False, f"修复没有把文件补齐：{result}"
    if sorted(downloaded) != ["https://libs/b.jar", "https://x/client.jar"]:
        return False, f"重下的文件不对（只该是坏的库 + 缺的主 jar）：{downloaded}"
    if result.get("repaired") != 2 or result.get("remaining"):
        return False, f"修复计数不对：{result}"
    return True, "修复按校验清单重下了坏文件与缺失文件"


def _d172_callbacks_survive_the_mro(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-172（已修）：进度回调**不许**给被混入类遮住的那两个方法加参数。

    现象（用户 2026-10-06 实测，这一条是 3.3 自己引入的回归）：为了把进度喂给"本次调用的
    出口"，`_get_callback()` 里写了 `self._set_status(status, sink)`。可
    `MinecraftLauncher` 是**多继承**的，`MultiMCMixin` 排在核心前面并定义了同名的一元方法
    (`launcher/multimc.py:921`)，于是真实类上直接
    `TypeError: _set_status() takes 2 positional arguments but 3 were given` ——
    表现是**装 Fabric/Forge 与"修复文件"全部失败**（日志里两条 `安装版本失败`）。
    桩测试没抓住它，因为桩把核心的方法直接绑了过来（MRO 里没有那个混入类）。

    判据是**行为 + 结构**两半：

    * 结构：真类上的 `_set_status` 确实是混入类那一份（这个坑真实存在），且
      `_get_callback` 里不许再出现"给 `_set_status` 传第二个实参"；
    * 行为：与 `MinecraftLauncher` **同形状的 MRO**（混入类在前）跑一遍安装，
      本次调用的出口要拿到状态与进度。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`launcher/core.py`）—— 那一支只查结构
            （自检不能拿改坏的源码去跑真安装）。
    """
    from launcher import MinecraftLauncher
    from launcher.multimc import MultiMCMixin

    if text is not None:
        mutated = strip_python_comments(text)
        if "_set_status(status, sink)" in mutated or "_emit_status(status, sink)" not in mutated:
            return False, "又给被遮住的方法传第二个实参了（真实类上会 TypeError）"
        return True, "结构判据通过（负例自检分支）"

    if MinecraftLauncher._set_status is not MultiMCMixin._set_status:
        return False, "MRO 变了：`_set_status` 不再被混入类遮住，这条判据要重新评估"
    core = strip_python_comments(source("launcher/core.py"))
    if "_set_status(status, sink)" in core or "_set_progress(progress, sink)" in core:
        return False, "又给被遮住的方法传第二个实参了（真实类上会 TypeError）"
    if "def _emit_status" not in core or "def _emit_progress" not in core:
        return False, "本次调用的进度出口没了（`_emit_status` / `_emit_progress`）"

    import sys as _sys
    from pathlib import Path as _Path

    root = str(REPO_ROOT)
    if root not in _sys.path:
        _sys.path.insert(0, root)
    _sys.path.insert(0, str(REPO_ROOT / "tests"))
    import tempfile

    from test_version_paths import ShadowedInstallStub  # noqa: PLC0415

    with tempfile.TemporaryDirectory() as raw:
        stub = ShadowedInstallStub(_Path(raw))
        seen: List[Tuple[int, int, str]] = []
        ok, installed = stub.install_version(
            "1.20.4", "无",
            on_progress=lambda current, total, text: seen.append((current, total, text)),
        )
    if not ok or installed != "1.20.4":
        return False, f"被遮住的 MRO 下安装没跑通：{ok} / {installed}"
    if (0, 0, "Download file1.jar") not in seen:
        return False, f"状态没走到本次调用出口：{seen}"
    return True, "回调走 `_emit_*`，被混入类遮住的老方法只按一元调"


def _d173_language_can_be_switched_in_app(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-173（已修）：QML 里要有一个**随时可用**的界面语言入口。

    现象（用户 2026-10-06 实测）："每次把 config.json 改成 zh_CN，启动 qt 版直接就是英文，
    config.json 也被改了"。根子有两条：

    1. A-27 的语言浮层只在**首次启动**（`language_chosen=false`）出现一次，而设置页要到
       3.4 才有 —— 中间这段用户想换语言只能手改配置文件；
    2. 配置文件是**整份**写的（`save_config()`），任何一处保存都会把内存里的旧语言
       一起写回去，于是手改的内容随时可能被覆盖。

    修法：顶栏加一个地球图标 → `Shell.requestLanguage()` → **复用 A-27 那个语言浮层**
    （一份实现两个入口），并且只有"首次启动"那一次才回去继续启动链条。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`qml/shell/AppBar.qml`）。
    """
    appbar = (REPO_ROOT / "qml/shell/AppBar.qml").read_text(encoding="utf-8") if text is None else text
    if "languageButton" not in appbar or "Shell.requestLanguage()" not in appbar:
        return False, "顶栏没有切语言的入口（用户又只能手改 config.json）"
    shell = strip_python_comments(source("app/bridges/shell_bridge.py"))
    if "def requestLanguage" not in shell or "languageRequested" not in shell:
        return False, "壳层没有 requestLanguage 信号"
    dialogs = (REPO_ROOT / "qml/StartupDialogs.qml").read_text(encoding="utf-8")
    if "onLanguageRequested" not in dialogs or "languageIsFirstRun" not in dialogs:
        return False, "浮层没有接壳层的请求，或没区分「首次启动 / 随时切」"
    return True, "顶栏地球图标 → Shell.requestLanguage → 复用 A-27 的语言浮层"


def _d175_startup_reads_the_pinned_config(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-175（已修）：`tests/test_startup.py` 必须**自带**配置，不许读开发机那份。

    现象（2026-10-06 实测，代价是二十分钟的误判）：为了排查语言问题临时改了
    `config.json` 的 `terms_consent` / `language_chosen`，`tests/test_startup.py`
    立刻红了两条（公告相关的两条走不到预下载），而**代码一个字都没改**。
    根因：`FakeContext` 没有 `config`，`StartupController._config_object()` 于是回退到
    根模块那个真单例 —— "要不要问语言""协议同意没同意"这两件事由开发机决定。
    修法：`FakeContext` 注入 `PinnedConfig`，并把两处直接 `monkeypatch` 真配置的用例
    改成操作注入的那一份。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`tests/test_startup.py`）。
    """
    src = (REPO_ROOT / "tests/test_startup.py").read_text(encoding="utf-8") if text is None else text
    if "class PinnedConfig" not in src or "self.config = PinnedConfig()" not in src:
        return False, "FakeContext 没有注入自己的配置（链条会去读开发机的 config.json）"
    if "config_module.config" in src:
        return False, "还有用例在 monkeypatch 根模块那份真配置"
    if "_config_object() is ctx.config" not in src:
        return False, "没有那条「读的是注入的那份」的正向断言"
    return True, "启动链条读的是用例注入的配置，与开发机 config.json 无关"


def _d176_tests_never_write_the_real_config(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-176（已修）：测试与探针**不许**改写开发机上那份真 `config.json`。

    现象（2026-10-06，并行跑全量之后发现）：用户 `config.json` 的 `language`
    从 `en_US` 变成 `zh_CN`、`accent_color` 从 `#abcdef` 变成空串 —— 而用户看到的
    正是"我的语言设置又被改回去了"。串行跑看不出来：最后写盘的那个人**恰好**把原值
    写了回去（谁最后跑完谁说了算，D-175 的同一类问题：测试不该碰用户的真实状态）。

    修法：`tests/config_isolation.py` 把真 `save_config()` 换成"只记数不落盘"，
    整场测试由 `tests/conftest.py` 装一次；子进程（QML 探针 / 冒烟 / 视觉探针）
    跑不到 conftest，各自在装配前调同一个守卫。

    判据两半：

    * 行为：装守卫 → 把内存里的语言改成别的值 → 调 `save_config()` →
      **文件一个字节都不许变**，且守卫确实记到了"拦下了一次"；
    * 结构：conftest 与各探针都得装上它（漏一个就等于给用户配置留一个后门）。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`tests/config_isolation.py`）。
    """
    if text is not None:
        mutated = strip_python_comments(text)
        if "config.save_config = blocked" not in mutated:
            return False, "守卫没真的替换 save_config（写盘照旧）"
        return True, "结构判据通过（负例自检分支）"

    from config_isolation import isolate_config_writes, restore_config_writes

    import config as config_module

    path = REPO_ROOT / "config.json"
    before = path.read_bytes()
    saved_language = config_module.config.language
    blocked = isolate_config_writes("ledger-d176")
    try:
        #: 故意改成一个"写下去就能看出来"的值：守卫要是失效，文件内容会真的变
        config_module.config.language = "ja_JP"
        config_module.config.save_config()
        count = blocked()
    finally:
        config_module.config.language = saved_language
        restore_config_writes("ledger-d176")
    after = path.read_bytes()

    if before != after:
        return False, "守卫没挡住：真 config.json 被改写了"
    if count < 1:
        return False, "save_config 没有被拦下（守卫装空了）"

    conftest = (REPO_ROOT / "tests/conftest.py").read_text(encoding="utf-8")
    if "isolate_config_writes" not in conftest:
        return False, "conftest 没装守卫（进程内测试仍会写用户配置）"
    missing = [
        name for name in ("_smoke_driver.py", "visual_probe.py", "qml_startup_probe.py",
                          "qml_install_probe.py", "qml_versions_probe.py", "qml_home_probe.py",
                          "qml_shell_probe.py")
        if "isolate_config_writes" not in (REPO_ROOT / "tests" / name).read_text(encoding="utf-8")
    ]
    if missing:
        return False, f"这些子进程探针没装守卫：{missing}"
    return True, "真配置只读：守卫拦下了写盘，conftest 与 7 个探针都装上了"


def _d169_rename_moves_files_that_exist(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-169（已修）：重命名要能真的跑通（旧实现在搬文件那一步必崩）。

    `rename_instance()` 里 `dst = new_dir / item.name`，而 `item` 是 `os.listdir()`
    返回的**字符串** —— 每次重命名都抛 `'str' object has no attribute 'name'`，
    被外层 `except` 兜住之后返回 `(False, "'str' object has no attribute 'name'")`，
    用户看到的是"重命名失败: 'str' object has no attribute 'name'"。
    **旧 Tk 界面同样如此**（用户 2026-10-06 在新界面上点出来才发现）。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`launcher/core.py`）。
    """
    core = source("launcher/core.py") if text is None else strip_python_comments(text)
    problems: List[str] = []
    if "dst = new_dir / item.name" in core:
        problems.append("又写成 `item.name` 了（item 是字符串，这条路必崩）")
    if "dst = new_dir / item\n" not in core:
        problems.append("找不到 `dst = new_dir / item`（搬文件那一行）")
    return (not problems, "；".join(problems) or "搬文件用的是字符串本身，不是 .name")


def _d170_one_place_finds_the_version_json(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-170（已修）：版本 JSON 与主程序 jar 的查找必须**收口到一处**。

    `_read_instance_info()` 只认 `versions/{id}/{id}.json`，而
    `verify_installed_version()` 原来只认顶层 `versions/{id}.json` —— 于是同一个版本
    "列表里看得见、一校验就说版本 JSON 不存在"（用户 2026-10-06 的日志）。
    重命名过的实例还有第二个坑：主程序 jar 的文件名**还是旧的**，只认 `{id}.jar`
    同样找不到（`_find_version_jar` 的三个候选就是为此）。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`launcher/core.py`）。
    """
    core = source("launcher/core.py") if text is None else strip_python_comments(text)
    problems: List[str] = []
    if "def find_version_json" not in core:
        problems.append("核心没有 find_version_json()（查找又散开了）")
    if "json_path = self.find_version_json(folder_name)" not in core:
        problems.append("_read_instance_info() 没有走收口后的查找")
    if "version_json = self.find_version_json(version_id)" not in core:
        problems.append("verify_installed_version() 没有走收口后的查找")
    if "def _find_version_jar" not in core:
        problems.append("没有 _find_version_jar()（重命名后的 jar 名找不到）")
    return (not problems, "；".join(problems) or "JSON 与 jar 的查找都只有一处真值来源")


def _d171_late_bridges_get_the_startup_controller(text: Optional[str] = None) -> Tuple[bool, str]:
    """D-171（已修）：`Startup` 后注册，先注册的桥必须还能拿到它。

    用户 2026-10-06 实测："两个打开公告的按钮都点不了"。两条独立的根因：

    1. `assemble()` 里 `register_bridges()` 跑在 `StartupController` **之前** ——
       `HomeBridge.use_engine()` 那一刻桥表里没有 `Startup`，于是 `Home._startup` 恒为
       None、`Home.hasNotice` 恒为 False、首页「查看公告」按钮永久置灰；
    2. 顶栏铃铛调的是 `Shell.toggleNotificationCenter()`，而那个信号**没有任何消费者**
       （通知中心浮层一直没做）—— 点了什么都不发生。

    修法：①`Startup` 进桥表之后对 Home / Versions 再注入一次引擎；
    ②铃铛改发 `Shell.requestNotice()`，由 `App.qml` 决定重看公告还是提示"暂无公告"。

    Args:
        text: 负例自检喂进来的**故意改坏**的源码（`main_qml.py`）。
    """
    main = source("main_qml.py") if text is None else strip_python_comments(text)
    appbar = (REPO_ROOT / "qml/shell/AppBar.qml").read_text(encoding="utf-8")
    app = (REPO_ROOT / "qml/App.qml").read_text(encoding="utf-8")
    problems: List[str] = []
    if 'for name in ("Home", "Versions"):' not in main:
        problems.append("main_qml 没有对后注册的 Startup 做二次注入")
    if "inject(engine)" not in main:
        problems.append("二次注入没真的调用 use_engine")
    if "Shell.requestNotice()" not in appbar:
        problems.append("顶栏铃铛没改用 Shell.requestNotice()")
    if "onNoticeRequested" not in app or "notice_none" not in app:
        problems.append("App.qml 没接通知请求（没有公告时也没有提示）")
    return (not problems, "；".join(problems) or "二次注入 + 铃铛改走通知请求")


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
               "`tests/_smoke_driver.py` 自己那份规避保留（同一条路径的双保险）。"
               "**2026-10-06 补充（3.2 验收轮实测）**：这条竞态**仍在**，只是触发概率随界面"
               "复杂度漂移 —— 版本页（25 行 × 6 个图标）上线后，同一份 `tests/ui_smoke.py`"
               "「跑 3 次里 2 次」在主题段刷出 4 条 `QML FmIcon: Cannot find member data`"
               "（判据把它算失败）；换成阶段 2 的占位版本页则不复现。**没有放宽判据**"
               "（曾试过在 IGNORED_PATTERNS 登记，被 `test_ignored_patterns_are_registered_and_few`"
               "的「放行 ≤ 5 条」当场驳回）—— 根因仍是本条，排期不变。",
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

    # ─── 3.1 人工验收（用户实测）发现并当场修掉的三条 ──────────────
    "D-160": Entry(
        defect="D-160", state="已修",
        summary="`config.json` 里选的语言不生效（QML 界面永远跟着系统语言走）",
        where="app/bridges/tr_bridge.py", check=_d160_language_comes_from_the_config,
        files=("app/bridges/tr_bridge.py", "tests/test_tr_bridge.py")),
    "D-161": Entry(
        defect="D-161", state="已修",
        summary="`FmCard` 的页脚被当成可拉伸项，与正文**平分**卡片高度 —— 首页四张卡只看得见前两张",
        where="qml/components/FmCard.qml", check=_d161_card_footer_does_not_steal_height,
        files=("qml/components/FmCard.qml", "qml/pages/home/HomePage.qml",
               "tests/test_home_page_qml.py")),
    "D-162": Entry(
        defect="D-162", state="已修",
        summary="协议弹窗只显示语言文件里的一句摘要，没有显示 `TERMS_OF_USE.md` 全文（旧弹窗显示全文）",
        where="services/legal_service.py", check=_d162_terms_full_text_is_wired,
        files=("services/legal_service.py", "app/startup.py", "qml/StartupDialogs.qml")),
    "D-163": Entry(
        defect="D-163", state="已修",
        summary="`FmButton` 的绑定在对象销毁那一刻会抛 `TypeError: Property 'borderColor' … is not a function`",
        where="qml/components/FmButton.qml", check=_d163_component_bindings_survive_teardown,
        files=("qml/components/FmButton.qml", "tests/test_visual_regression.py")),

    # ─── 3.2（版本列表与详情）新登记的五条 ────────────────────────
    "D-164": Entry(
        defect="D-164", state="已修",
        summary="装配期扫描时启动器核心还没注册 → 版本页整页「出错」（磁盘上的版本其实看得见）",
        where="services/version_service.py", check=_d164_scan_survives_a_missing_core,
        files=("services/version_service.py", "app/bridges/version_bridge.py",
               "tests/test_version_service.py", "tests/test_version_bridge.py")),
    "D-165": Entry(
        defect="D-165", state="已修",
        summary="错误态的「重试」是次按钮 → 整块错误态没有强调色（视觉判据在错误态判红）",
        where="qml/components/FmErrorState.qml", check=_d165_error_state_retry_is_primary,
        files=("qml/components/FmErrorState.qml", "tests/test_visual_regression.py")),
    "D-166": Entry(
        defect="D-166", state="已修",
        summary="真装配的模块级 fixture 不还原界面语言 → 后续模块的中文断言随用户 `config.json` 变红",
        where="tests/test_main_qml_entry.py", check=_d166_test_assembly_restores_the_language,
        files=("tests/test_main_qml_entry.py", "tests/test_music_service.py")),
    "D-167": Entry(
        defect="D-167", state="已修",
        summary="版本「校验修复」只兑现了**校验**：核心只算哈希、不重下文件，「修复」没做",
        where="launcher/core.py",
        markers=("def repair_installed_version", "def expected_version_files"),
        check=_d167_repair_reuses_the_install_chain,
        files=("launcher/core.py", "services/version_service.py",
               "qml/pages/versions/VersionsPage.qml")),
    "D-172": Entry(
        defect="D-172", state="已修",
        summary="给 `_set_status` 加了一个参数 → 多继承下被混入类的一元同名方法遮住，"
                "装带加载器的版本必失败（`TypeError: _set_status() takes 2 positional arguments but 3 were given`）",
        where="launcher/core.py",
        markers=("def _emit_status", "def _emit_progress", "legacy_status = self._set_status"),
        check=_d172_callbacks_survive_the_mro,
        files=("launcher/core.py", "tests/test_version_paths.py")),
    "D-173": Entry(
        defect="D-173", state="已修",
        summary="QML 侧除首次启动外**没有切换界面语言的入口**，用户只能手改 `config.json` —— "
                "而改完会被下一次 `save_config()` 整份覆盖回去，表现为「每次启动都回英文」",
        where="qml/shell/AppBar.qml",
        markers=("languageButton", "Shell.requestLanguage()"),
        check=_d173_language_can_be_switched_in_app,
        files=("qml/shell/AppBar.qml", "qml/StartupDialogs.qml", "app/bridges/shell_bridge.py")),
    "D-175": Entry(
        defect="D-175", state="已修",
        summary="`tests/test_startup.py` 的 `FakeContext` 没提供 `config` → 启动链条读的是**开发机**的 "
                "`config.json`，动一次真配置就红两条（与 D-166 同一类：测试依赖用户配置）",
        where="tests/test_startup.py",
        markers=("class PinnedConfig", "self.config = PinnedConfig()"),
        check=_d175_startup_reads_the_pinned_config,
        files=("tests/test_startup.py", "app/startup.py")),
    "D-176": Entry(
        defect="D-176", state="已修",
        summary="测试与探针走生产装配路径时**改写了开发机的真 `config.json`**"
                "（并行跑一轮全量之后 `language` 变成 zh_CN、`accent_color` 被清空）",
        where="tests/config_isolation.py",
        markers=("def isolate_config_writes", "def restore_config_writes"),
        check=_d176_tests_never_write_the_real_config,
        files=("tests/config_isolation.py", "tests/conftest.py", "tests/_smoke_driver.py",
               "tests/visual_probe.py")),
    "D-168": Entry(
        defect="D-168", state="挂账",
        summary="`rename_instance` 的两个失败分支返回**硬编码中文**，非中文界面下会露出中文句子",
        where="launcher/core.py",
        markers=('return False, f"实例 \'{old_name}\' 不存在"', 'return False, "找不到实例 JSON 文件"'),
        reason="旧界面就是这么显示的（`ui/app_handlers.py:1138-1139` 把核心返回的原文塞进 "
               "`rename_instance_error` 的 `{error}`），属**原样保留**（红线 1）。改成返回 i18n 键"
               "会动到核心的返回契约（`rename_instance` 的调用点不止一处），排在**阶段 4** 的"
               "i18n 收尾一起做：那时把这两个分支改成键名，界面侧映射成新键。",
        files=("launcher/core.py", "services/version_service.py")),

    # ─── 3.2 人工验收当场发现的三条（D-169 ~ D-171）──────────────
    "D-169": Entry(
        defect="D-169", state="已修",
        summary="`rename_instance()` 用 `item.name` 搬文件（`item` 是字符串）→ 重命名 100% 失败",
        where="launcher/core.py", check=_d169_rename_moves_files_that_exist,
        files=("launcher/core.py", "tests/test_version_paths.py")),
    "D-170": Entry(
        defect="D-170", state="已修",
        summary="版本 JSON 与主程序 jar 的查找散在两处各认一半 → 列表里看得见、一校验就说版本 JSON 不存在",
        where="launcher/core.py", check=_d170_one_place_finds_the_version_json,
        files=("launcher/core.py", "services/version_service.py", "tests/test_version_paths.py")),
    "D-171": Entry(
        defect="D-171", state="已修",
        summary="两个\"打开公告\"的按钮都点不了（Startup 注册晚于桥注册 + 铃铛发的信号没人接）",
        where="main_qml.py", check=_d171_late_bridges_get_the_startup_controller,
        files=("main_qml.py", "app/bridges/shell_bridge.py", "qml/shell/AppBar.qml",
               "qml/App.qml", "tests/test_main_qml_entry.py")),
}

#: 台账必须覆盖的缺陷号（防"悄悄少一条"）：18 条欠账 + 后续几轮新登记的。
REQUIRED_IDS = (
    "D-04", "D-05", "D-10", "D-11", "D-13", "D-16", "D-17", "D-18", "D-19", "D-20",
    "D-21", "D-22", "D-23", "D-24", "D-25", "D-26", "D-27", "D-28",
    "D-146", "D-147", "D-150", "D-151", "D-152", "D-153", "D-154",
    "D-155", "D-156", "D-157", "D-158", "D-159",
    "D-160", "D-161", "D-162", "D-163",
    "D-164", "D-165", "D-166", "D-167", "D-168",
    "D-169", "D-170", "D-171",
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
        ("D-161", _d161_card_footer_does_not_steal_height,
         (REPO_ROOT / "qml/components/FmCard.qml").read_text(encoding="utf-8")
         .replace("Layout.fillHeight: false", ""),
         "把页脚的 fillHeight: false 删掉（退回与正文平分高度）"),
        ("D-163", _d163_component_bindings_survive_teardown,
         (REPO_ROOT / "qml/components/FmButton.qml").read_text(encoding="utf-8")
         .replace("control.borderColor ? control.borderColor()", "control.borderColor()"),
         "把 border.color 的兜底去掉（退回析构期裸调）"),
        ("D-164", _d164_scan_survives_a_missing_core,
         source("services/version_service.py").replace("rows = self._scan_folders()", "rows = []"),
         "核心缺席时不再降级列目录"),
        ("D-165", _d165_error_state_retry_is_primary,
         (REPO_ROOT / "qml/components/FmErrorState.qml").read_text(encoding="utf-8")
         .replace("primary: true", "primary: false"),
         "把重试按钮退回次按钮"),
        ("D-166", _d166_test_assembly_restores_the_language,
         (REPO_ROOT / "tests/test_main_qml_entry.py").read_text(encoding="utf-8")
         .replace("i18n_service._current_language = saved_lang", ""),
         "把界面语言还原那一行删掉"),
        ("D-172", _d172_callbacks_survive_the_mro,
         source("launcher/core.py").replace(
             "self._emit_status(status, sink)", "self._set_status(status, sink)"
         ),
         "把「本次调用的出口」改回给被混入类遮住的那个方法传第二个实参"),
        ("D-173", _d173_language_can_be_switched_in_app,
         (REPO_ROOT / "qml/shell/AppBar.qml").read_text(encoding="utf-8")
         .replace("Shell.requestLanguage()", "Shell.nothingHere()"),
         "把顶栏语言入口的落点改掉"),
        ("D-175", _d175_startup_reads_the_pinned_config,
         (REPO_ROOT / "tests/test_startup.py").read_text(encoding="utf-8")
         .replace("self.config = PinnedConfig()", "self.config = None"),
         "把注入的那份配置去掉（退回读开发机的 config.json）"),
        ("D-176", _d176_tests_never_write_the_real_config,
         (REPO_ROOT / "tests/config_isolation.py").read_text(encoding="utf-8")
         .replace("config.save_config = blocked", "pass"),
         "让守卫不真的替换 save_config（写盘照旧）"),
        ("D-167", _d167_repair_reuses_the_install_chain,
         source("launcher/core.py").replace(
             "self.expected_version_files(version_data, versions_dir, version_id)",
             "self._own_file_list()",
         ),
         "让修复自己另写一份文件清单（退回两处各说各话）"),
        ("D-169", _d169_rename_moves_files_that_exist,
         source("launcher/core.py").replace("dst = new_dir / item\n", "dst = new_dir / item.name\n"),
         "把搬文件那一行改回 item.name"),
        ("D-170", _d170_one_place_finds_the_version_json,
         source("launcher/core.py").replace(
             "version_json = self.find_version_json(version_id)",
             'version_json = versions_dir / f"{version_id}.json"'),
         "让校验退回只看顶层 JSON"),
        ("D-171", _d171_late_bridges_get_the_startup_controller,
         source("main_qml.py").replace('for name in ("Home", "Versions"):', 'for name in ():'),
         "把后注册 Startup 的二次注入去掉"),
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
