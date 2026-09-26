#!/usr/bin/env python
"""UI 冒烟测试（阶段 2 任务 2.19 / `03` 的 2.12；唯一入口）。

    .venv\\Scripts\\python.exe tests\\ui_smoke.py            # 人读的表 + 汇总
    .venv\\Scripts\\python.exe tests\\ui_smoke.py --json      # 机器可读结果（stdout 只有 JSON）
    .venv\\Scripts\\python.exe tests\\ui_smoke.py --out DIR   # 截图目录（默认 poc/ui_smoke/）
    .venv\\Scripts\\python.exe tests\\ui_smoke.py --self-test # 负例自检（证明本脚本不是空断言）
    .venv\\Scripts\\python.exe tests\\ui_smoke.py --list-ignored

退出码：0 = 全绿；1 = 有失败（结构断言失败 / QML 报了 warning 或 error）；2 = 用法错误。

## 判据（"任一页面报错即失败"）

`tests/_smoke_driver.py` 在**子进程**里装配真引擎、遍历 12 个一级页面、截图，并把
**每一条 Qt 消息连级别一起**记下来；本文件负责判定，判据只有这一处：

1. **结构断言**：驱动里的每条 `check` 都必须成立（路由切过去了、页面根对象找得到、
   三态能渲染、主题/语言切换生效、`Text`/`Icon` 换出来的东西不一样…）；
2. **Qt 消息**：
   * 文本命中 `IGNORED_PATTERNS` → **放行**（并记下命中的是哪一条、为什么登记它）；
   * 否则级别是 warning / critical / fatal → **失败**；
   * 级别是 debug / info（QML 的 `console.log` 就走这里）时再按文本判一次
     `FAILURE_MARKERS`：`PageStack` 内部 `console.log` 出来的同步失败、绑定报错
     这类必须算失败，不能因为"它是 debug 级"就漏掉。

`IGNORED_PATTERNS` 是**逐条登记**的（每条都带理由与出处），不是"忽略一切"：
`--list-ignored` 能把它们列出来，`tests/test_ui_smoke.py` 里还有负例钉住
"真正的 TypeError 不许被它们吞掉"。

## 为什么父子分离

见 `tests/_smoke_driver.py` 的文件头：`QQmlApplicationEngine` + FluentUI 的 `FluWindow`
在同一个进程里跨测试文件不安全（access violation），所以"真引擎 + 12 页遍历"只能在
全新进程里做；本文件因此不 import PySide6，纯标准库，连报告都能在没有 Qt 的机器上跑。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
DRIVER = Path(__file__).resolve().parent / "_smoke_driver.py"
DEFAULT_OUT = REPO_ROOT / "poc" / "ui_smoke"
MARKER = "SMOKE_JSON:"
SCHEMA = 1

#: 驱动的默认超时（秒）。实测一次完整走查约 25 s（含 12 页 × 三态 + 5 主题 + 4 语言 +
#: 两个组件的性能段），给足余量以便在慢机器/CI 上不至于误杀。
DEFAULT_TIMEOUT = 300

SEVERITY_FAIL = ("QtWarningMsg", "QtCriticalMsg", "QtFatalMsg")


@dataclass(frozen=True)
class IgnoredPattern:
    """一条**登记在案**的噪声。`reason` 必须写清"为什么它不是缺陷"。"""

    pattern: str
    reason: str


#: 明确放行的平台噪声。判据是**子串命中**，且只放行"文本命中且理由成立"的那一条。
#:
#: 三条都不是 QML 的问题，而且都与本阶段的功能无关 —— 但都**必须逐条登记**：
#: 少登记一条会让冒烟测试在干净的机器上变红，多放行一条（比如整段 `qt.qpa` 前缀）
#: 就会把真正的界面报错一起吞掉。
IGNORED_PATTERNS: Tuple[IgnoredPattern, ...] = (
    IgnoredPattern(
        "QFontDatabase: Cannot find font directory",
        "offscreen 平台的字体库提示：Qt 6 不再自带字体，PySide6 的 lib/fonts 是空的。"
        "驱动会尽量设 QT_QPA_FONTDIR 指向系统字体目录来规避它；"
        "在拿不到系统字体目录的环境（CI/精简容器）里它会照样出现，此时界面仍正常渲染，"
        "只是截图里的字会变成方块 —— 属于环境，不是界面的缺陷",
    ),
    IgnoredPattern(
        "does not support propagateSizeHints()",
        "offscreen 平台插件不支持窗口尺寸提示（QWindow::setSizeHints 的直接后果）。"
        "真实平台上由窗口管理器处理，与 QML 无关",
    ),
    IgnoredPattern(
        "QUnifiedTimer::stopAnimationDriver: driver is not running",
        "Qt 动画驱动在**退出清理阶段**的已知提示（窗口已销毁、驱动已停）。"
        "它出现在走查结束之后，不影响任何一页的渲染结果",
    ),
    IgnoredPattern(
        "FT_New_Face failed with index 0",
        "freetype 后端扫描 QT_QPA_FONTDIR 时遇到读不了的字体文件（Windows 字体目录里"
        "混有 .ttc/授权受限字体）。QtDebugMsg 级，只说明有一个字体被跳过",
    ),
)

#: 级别是 debug/info 也要判失败的文本特征（QML 的报错签名）。
#: 与 `tests/test_shell_qml.py:QML_ERROR_MARKERS` 同一份判据，另加 PageStack 自己的
#: `console.log("PageStack sync failed: …")`（它是 debug 级，只按级别判会漏掉）。
FAILURE_MARKERS: Tuple[str, ...] = (
    "TypeError", "ReferenceError", "SyntaxError", "is not defined", "Unable to assign",
    "Cannot assign", "is not a type", "Cannot read property", "of null", "is not a function",
    "failed to load component", "PageStack sync failed", "PageStack:", "Unable to assign",
)


def ignored_reason(text: str) -> str:
    """命中登记表则返回理由，否则空串。"""
    for entry in IGNORED_PATTERNS:
        if entry.pattern in text:
            return entry.reason
    return ""


def classify(record: Dict[str, Any]) -> str:
    """一条 Qt 消息的判定：`ignored` / `failure` / `ok`。"""
    text = str(record.get("text", ""))
    if ignored_reason(text):
        return "ignored"
    if str(record.get("mode", "")) in SEVERITY_FAIL:
        return "failure"
    for marker in FAILURE_MARKERS:
        if marker in text:
            return "failure"
    return "ok"


def classify_all(records: Sequence[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {"ignored": [], "failure": [], "ok": []}
    for record in records:
        entry = dict(record)
        entry["reason"] = ignored_reason(str(record.get("text", "")))
        buckets[classify(record)].append(entry)
    return buckets


# ─── 跑子进程 ──────────────────────────────────────────────────


@dataclass
class ChildRun:
    """一次子进程运行的结果。"""

    returncode: int
    payload: Optional[Dict[str, Any]]
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    command: List[str] = field(default_factory=list)

    @property
    def parse_error(self) -> str:
        if self.payload is not None:
            return ""
        tail = "\n".join(self.stdout.splitlines()[-8:])
        return f"子进程没有输出 {MARKER} 结果（exit={self.returncode}）；stdout 末尾：\n{tail}"


def child_env() -> Dict[str, str]:
    """子进程环境。

    * `QT_QPA_PLATFORM=offscreen`：无头跑（真窗口会抢焦点）；
    * `QT_QPA_FONTDIR`：offscreen 用 freetype 后端，默认找不到任何字体（Qt 6 不再自带），
      于是截图里的字全是方块 —— 指向系统字体目录之后截图才有可读的文字。
      只是在"Windows 且目录存在且调用方没自己设"时才补，不覆盖调用方的选择；
    * `PYTHONIOENCODING=utf-8`：子进程输出里有中文（Qt 消息），别让本地码页把它编坏。
    """
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = env.get("QT_QPA_PLATFORM_OVERRIDE", "offscreen")
    env["QT_QUICK_CONTROLS_STYLE"] = env.get("QT_QUICK_CONTROLS_STYLE", "Basic")
    env["PYTHONIOENCODING"] = "utf-8"
    if not env.get("QT_QPA_FONTDIR") and os.name == "nt":
        system_fonts = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"
        if system_fonts.is_dir():
            env["QT_QPA_FONTDIR"] = str(system_fonts)
    return env


def run_child(out_dir: Path, mutations: Sequence[str] = (), skip: Sequence[str] = (),
              timeout: int = DEFAULT_TIMEOUT) -> ChildRun:
    """按唯一入口起一次子进程走查（`python tests/_smoke_driver.py …`）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "smoke_result.json"
    command = [sys.executable, "-X", "utf8", str(DRIVER), "--out", str(out_dir),
               "--json-out", str(json_path)]
    for mutation in mutations:
        command += ["--mutation", mutation]
    if skip:
        command += ["--skip", ",".join(skip)]
    started = time.perf_counter()
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), env=child_env(), timeout=timeout,
    )
    duration = (time.perf_counter() - started) * 1000
    payload: Optional[Dict[str, Any]] = None
    for line in completed.stdout.splitlines():
        if line.startswith(MARKER):
            try:
                payload = json.loads(line[len(MARKER):])
            except ValueError as exc:  # noqa: BLE001 - 结果解析失败要在报告里说清楚
                payload = {"_parseError": str(exc)}
    if payload is None and json_path.is_file():
        try:
            payload = json.loads(json_path.read_text(encoding="utf-8"))
        except ValueError:
            payload = None
    return ChildRun(returncode=completed.returncode, payload=payload, stdout=completed.stdout,
                    stderr=completed.stderr, duration_ms=duration, command=command)


# ─── 判定 ──────────────────────────────────────────────────────

#: 截图的合法性底线：PNG magic + 至少 1 KB（空白窗口的 PNG 只有几 KB，
#: 而"文件存在但 0 字节"这种假绿必须被判出来）。
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MIN_SHOT_BYTES = 1024


@dataclass
class Report:
    """一次冒烟运行的完整判定。"""

    run: ChildRun
    out_dir: Path
    payload: Dict[str, Any]
    failures: List[Dict[str, str]] = field(default_factory=list)
    ignored: List[Dict[str, str]] = field(default_factory=list)
    messages: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    checks_total: int = 0
    checks_failed: int = 0
    shots: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures and self.run.returncode == 0

    def fail(self, kind: str, ident: str, detail: str) -> None:
        self.failures.append({"kind": kind, "id": ident, "detail": detail})


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate(run: ChildRun, out_dir: Path) -> Report:
    """把子进程的原始数据判成"绿 / 红"：结构断言 + Qt 消息 + 截图文件。"""
    payload = run.payload or {}
    report = Report(run=run, out_dir=out_dir, payload=payload)

    if run.payload is None:
        report.fail("child", "child.noPayload", run.parse_error)
        return report
    if run.returncode != 0:
        report.fail("child", "child.exitCode",
                    f"子进程退出码 {run.returncode}（装配失败或走查中断）")
    if payload.get("fatal"):
        report.fail("child", "child.fatal", str(payload["fatal"]))
    if payload.get("walkError"):
        report.fail("child", "child.walkError", str(payload["walkError"]))

    # 1) 结构断言
    checks = payload.get("checks", [])
    report.checks_total = len(checks)
    for entry in checks:
        if not entry.get("ok"):
            report.checks_failed += 1
            report.fail("check", str(entry.get("id", "?")), str(entry.get("detail", "")))
    if not checks:
        report.fail("check", "checks.empty", "驱动一条断言都没记（走查没跑起来）")

    # 2) Qt 消息
    report.messages = classify_all(payload.get("messages", []))
    for entry in report.messages["failure"]:
        report.fail("message", str(entry.get("mode", "?")), str(entry.get("text", "")))
    report.ignored = report.messages["ignored"]

    # 3) 截图：文件必须真的存在、非空、是 PNG，且与驱动记录的大小一致
    for shot in payload.get("shots", []):
        path = Path(str(shot.get("path", "")))
        info = dict(shot)
        if not path.is_file():
            report.fail("shot", str(shot.get("file", "?")), f"截图文件不存在：{path}")
            info["sha256"] = ""
            info["verified"] = False
            report.shots.append(info)
            continue
        size = path.stat().st_size
        head = path.read_bytes()[:8]
        verified = size >= MIN_SHOT_BYTES and head == PNG_MAGIC and size == int(shot.get("bytes", -1))
        info["bytesOnDisk"] = size
        info["sha256"] = sha256_of(path)
        info["verified"] = bool(verified)
        if not verified:
            report.fail("shot", str(shot.get("file", "?")),
                        f"截图不合法：{size} 字节（记录 {shot.get('bytes')}）、"
                        f"magic={head!r}、阈值 {MIN_SHOT_BYTES}")
        report.shots.append(info)
    if not report.shots:
        report.fail("shot", "shots.empty", "一张截图都没有")
    return report


# ─── 输出 ──────────────────────────────────────────────────────


def render_table(report: Report) -> str:
    """页面 / 路由 / 对象名 / 截图文件 / 消息数 的表 + 汇总行。"""
    payload = report.payload
    lines: List[str] = []
    env = payload.get("runtime", {})
    assemble = payload.get("assemble", {})
    lines.append("=" * 96)
    lines.append("FMCL 阶段 2 UI 冒烟测试（任务 2.19）—— 12 个一级页面 + 主题/语言热切换 + 组件")
    lines.append("=" * 96)
    lines.append(f"平台：{payload.get('qpa', '?')}   Qt：{env.get('qt', '?')}   "
                 f"Python：{env.get('python', '?')}   桥：{env.get('bridgeStatus', '?')}")
    lines.append(f"装配：{assemble.get('ms', '?')} ms（start_startup=False，不开跑启动流程）；"
                 f"路由表 {payload.get('routes', {}).get('routeCount', '?')} 条，"
                 f"一级导航 {payload.get('routes', {}).get('count', '?')} 项")
    lines.append("")
    header = f"{'#':>3}  {'路由':<13}{'对象名':<20}{'三态':<6}{'截图文件':<26}{'大小':>9}  {'消息':>4}"
    lines.append(header)
    lines.append("-" * 96)
    for row in payload.get("pages", []):
        shot = row.get("screenshot", {})
        states = row.get("states", [])
        lines.append(
            f"{row.get('index', 0):>3}  {row.get('route', ''):<13}{row.get('pageObjectName', ''):<20}"
            f"{len(states)}/{len(states) if states else 0:<4}{shot.get('file', ''):<26}"
            f"{shot.get('bytes', 0):>9}  {row.get('messageCount', 0):>4}"
        )
    lines.append("")
    lines.append("主题热切换（每个预设一次，切完零报错）：")
    for row in payload.get("themeRows", []):
        lines.append(f"  - {row.get('name', ''):<10} currentTheme={row.get('current', ''):<10} "
                     f"revision {row.get('revisionBefore')}->{row.get('revisionAfter')} "
                     f"bgDark={row.get('bgDark')} 截图={row.get('screenshot', {}).get('file')} "
                     f"消息={row.get('messageCount')}")
    lines.append("语言热切换（每种语言一次，切完零报错）：")
    for row in payload.get("languageRows", []):
        lines.append(f"  - {row.get('code', ''):<7} 键数={row.get('keyCount')} "
                     f"标题={row.get('titleText', '')!r} 截图={row.get('screenshot', {}).get('file')} "
                     f"消息={row.get('messageCount')}")
    logview = payload.get("logView") or {}
    if logview:
        rate = logview.get("appendPerLine", {})
        paced = logview.get("paced", {})
        cap = logview.get("cap", {})
        lines.append("组件（LogView / LogDemo，任务 2.18）：")
        lines.append(f"  - LogView 追加：{rate.get('lines')} 行 {rate.get('ms')} ms "
                     f"= {rate.get('perSecond')} 行/秒（同步循环）；"
                     f"带事件循环 {paced.get('lines')} 行 {paced.get('ms')} ms "
                     f"= {paced.get('perSecond')} 行/秒")
        lines.append(f"  - LogView 上限：maxLines={cap.get('maxLines')} 瞬态峰值 {cap.get('peak')}、"
                     f"安静后 {cap.get('settled')}；搜索高亮可见块 "
                     f"{logview.get('search', {}).get('visibleHitBoxes')}；"
                     f"自动滚动不抢={logview.get('autoScrollAfterDrag')}；"
                     f"复制 {logview.get('copy', {}).get('clipboardChars')} 字符")
    logdemo = payload.get("logDemo") or {}
    if logdemo:
        lines.append(f"  - LogDemo：结构化日志 {logdemo.get('structuredLines')} 行"
                     f"（级别 {logdemo.get('structuredLevels')}）+ 纯文本 {logdemo.get('plainLines')} 行 → "
                     f"{logdemo.get('linesAfterPlain')} 行；按钮回环 {logdemo.get('reload', {}).get('signalCount')} 次；"
                     f"生成 5000 行后 {logdemo.get('afterGenerate5000')}、清空后 {logdemo.get('afterClear')}")
    lines.append("")
    lines.append("-" * 96)
    summary = report_summary(report)
    lines.append(
        f"汇总：页面 {summary['pages']} 个全部切换并截图成功；主题 {summary['themes']} 个、"
        f"语言 {summary['languages']} 种全部切换生效；"
        f"结构断言 {summary['checks'] - summary['failedChecks']}/{summary['checks']} 通过；"
        f"Qt 消息 {summary['messages']} 条（放行 {summary['ignored']} 条、失败 {summary['failedMessages']} 条）；"
        f"截图 {summary['shotsNonEmpty']}/{summary['shots']} 张非空"
    )
    lines.append(f"耗时：子进程 {report.run.duration_ms / 1000:.1f} s"
                 + ("（未解析到子进程结果）" if report.run.payload is None else ""))
    if report.ignored:
        lines.append("放行的平台噪声（登记在案，逐条列理由）：")
        for entry in report.ignored:
            lines.append(f"  - {entry['text'].splitlines()[0][:70]} —— {entry['reason'][:60]}…")
    if report.failures:
        lines.append("")
        lines.append(f"失败 {len(report.failures)} 条：")
        for entry in report.failures:
            lines.append(f"  [FAIL/{entry['kind']}] {entry['id']}: {entry['detail'][:200]}")
    else:
        lines.append("结论：全绿 —— 12 个一级页面都能起来、三态都能渲染、主题与语言热切换无报错。")
    lines.append("=" * 96)
    return "\n".join(lines)


def report_summary(report: Report) -> Dict[str, Any]:
    payload = report.payload
    buckets = report.messages or {"ignored": [], "failure": [], "ok": []}
    return {
        "pages": len(payload.get("pages", [])),
        "themes": len(payload.get("themeRows", [])),
        "languages": len(payload.get("languageRows", [])),
        "checks": report.checks_total,
        "failedChecks": report.checks_failed,
        "messages": len(payload.get("messages", [])),
        "failedMessages": len(buckets["failure"]),
        "ignored": len(buckets["ignored"]),
        "shots": len(report.shots),
        "shotsNonEmpty": sum(1 for shot in report.shots if shot.get("verified")),
        "routes": [row.get("route") for row in payload.get("pages", [])],
    }


def report_as_json(report: Report, run: ChildRun) -> str:
    """机器可读结果（`--json`）。**只包含数据**，人读的表在默认模式下输出。"""
    payload = report.payload
    data = {
        "schema": SCHEMA,
        "kind": "ui-smoke",
        "ok": report.ok,
        "child": {
            "returncode": run.returncode,
            "durationMs": round(run.duration_ms, 1),
            "command": run.command,
            "stderrTail": "\n".join(run.stderr.splitlines()[-20:]),
            "parseError": run.parse_error,
        },
        "environment": {
            "qpa": payload.get("qpa", ""),
            "cwd": payload.get("cwd", ""),
            "runtime": payload.get("runtime", {}),
            "assemble": payload.get("assemble", {}),
        },
        "summary": report_summary(report),
        "failures": report.failures,
        "ignored": report.ignored,
        "ignoredPatterns": [{"pattern": e.pattern, "reason": e.reason} for e in IGNORED_PATTERNS],
        # 结构断言原文：机器消费者（tests/test_ui_smoke.py）要能复核每一条，
        # 而不是只看到一个"通过"的结论
        "checks": payload.get("checks", []),
        "routes": payload.get("routes", {}),
        "pages": payload.get("pages", []),
        "themes": payload.get("themes", {}),
        "themeRows": payload.get("themeRows", []),
        "languages": payload.get("languages", {}),
        "languageRows": payload.get("languageRows", []),
        "logView": payload.get("logView"),
        "logDemo": payload.get("logDemo"),
        "shots": report.shots,
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


# ─── 负例自检（证明本脚本不是空断言） ──────────────────────────

#: 第三条自检用的样本：`(消息, 期望被判成失败)`。文本尽量与真实 QML 报错一致。
CLASSIFY_SAMPLES: Tuple[Tuple[str, str, bool], ...] = (
    ("TypeError: Cannot read property 'x' of null",
     "QtWarningMsg", True),
    ("ReferenceError: notDefinedAnywhere is not defined",
     "QtWarningMsg", True),
    ("PageStack sync failed: component not ready",
     "QtDebugMsg", True),
    ("qml: 这一页正常打了一条日志",
     "QtDebugMsg", False),
    ("QFontDatabase: Cannot find font directory /tmp/fonts",
     "QtWarningMsg", False),
)


def self_test(out_root: Path, timeout: int = DEFAULT_TIMEOUT) -> int:
    """三条负例自检：

    ① 故意 push 一个不存在的路由 → 走查里的路由断言必须变红（**脚本必须报错**）；
    ② 临时 QML 里一个必然报 warning 的绑定 → "消息计数"必须能把它判成失败；
    ③ `IGNORED_PATTERNS` 不许吞掉真正的 TypeError（直接判分类函数）。

    自检**要求**这些负例被抓住：抓住了才是绿的。这就是"闸门自己也要被测试"。
    """
    results: List[Tuple[str, bool, str]] = []

    run = run_child(out_root / "bad_route", mutations=["bad-route"],
                    skip=["theme", "components"], timeout=timeout)
    report = evaluate(run, out_root / "bad_route")
    hits = [entry for entry in report.failures if entry["id"] == "mutation.badRoute"]
    results.append((
        "① 非法路由必须让脚本失败",
        (not report.ok) and bool(hits),
        f"父进程判定={'红（exit 1）' if not report.ok else '绿'}，"
        f"{len(report.failures)} 条失败，命中 mutation.badRoute：{bool(hits)}",
    ))

    run = run_child(out_root / "bad_binding", mutations=["bad-binding"],
                    skip=["pages", "theme", "components"], timeout=timeout)
    report = evaluate(run, out_root / "bad_binding")
    messages = [entry for entry in report.failures if entry["kind"] == "message"]
    caught = [entry for entry in messages
              if "notDefinedAnywhere" in entry["detail"] or "is not defined" in entry["detail"]]
    results.append((
        "② 坏绑定必须被消息计数判成失败",
        (not report.ok) and bool(caught),
        f"父进程判定={'红（exit 1）' if not report.ok else '绿'}，"
        f"消息类失败 {len(messages)} 条，命中绑定报错（notDefinedAnywhere）{len(caught)} 条",
    ))

    wrong: List[str] = []
    for text, mode, expected in CLASSIFY_SAMPLES:
        got = classify({"mode": mode, "text": text}) == "failure"
        if got != expected:
            wrong.append(f"{text!r} 判成 {got}，期望 {expected}")
    # 登记表里的每一条都必须真的被放行，且不许出现"像报错"的文本
    for entry in IGNORED_PATTERNS:
        if classify({"mode": "QtWarningMsg", "text": entry.pattern}) != "ignored":
            wrong.append(f"登记项没被放行：{entry.pattern!r}")
        for marker in ("TypeError", "ReferenceError", "is not defined", "of null"):
            if marker in entry.pattern:
                wrong.append(f"登记项吞掉了报错特征 {marker!r}：{entry.pattern!r}")
    results.append(("③ IGNORED_PATTERNS 不许吞掉真报错", not wrong,
                    "; ".join(wrong) if wrong else f"{len(CLASSIFY_SAMPLES)} 个样本 + "
                                                   f"{len(IGNORED_PATTERNS)} 条登记项判定正确"))

    print("=" * 96)
    print("UI 冒烟测试 负例自检（三条都要被抓住，全抓住才是绿）")
    print("=" * 96)
    for name, ok, detail in results:
        print(f"  [{'OK' if ok else 'FAIL'}] {name} —— {detail}")
    failed = [name for name, ok, _ in results if not ok]
    print("-" * 96)
    print("自检结论：" + ("三条负例全被抓住，本脚本的失败判定不是空断言。"
                          if not failed else f"有 {len(failed)} 条没被抓住：{failed}"))
    return 0 if not failed else 1


# ─── 入口 ──────────────────────────────────────────────────────


def list_ignored() -> int:
    """列出登记在案的平台噪声（--list-ignored）。"""
    print(f"登记在案的平台噪声（{len(IGNORED_PATTERNS)} 条；判据是子串命中）：")
    for entry in IGNORED_PATTERNS:
        print(f"  - {entry.pattern}")
        print(f"      理由：{entry.reason}")
    print("")
    print("级别为 debug/info 时仍会被判失败的文本特征（FAILURE_MARKERS）：")
    for marker in FAILURE_MARKERS:
        print(f"  - {marker}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="FMCL 阶段 2 UI 冒烟测试（任务 2.19）：装配真引擎，遍历 12 个一级页面并截图，"
                    "任一处 QML warning/error 即失败。",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读结果（stdout 只有 JSON）")
    parser.add_argument("--json-out", default="",
                        help="把机器可读结果**另存**到这个路径（UTF-8 无 BOM）——"
                             "CI 与 PowerShell 下比重定向 stdout 可靠（PS 5.1 的 `>` 会写成 UTF-16）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="截图目录（默认 poc/ui_smoke/）")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="子进程超时（秒）")
    parser.add_argument("--self-test", action="store_true", help="负例自检：证明失败判定不是空断言")
    parser.add_argument("--list-ignored", action="store_true", help="列出登记在案的平台噪声")
    # 隐藏开关：只给自检与排查用，正常跑法不需要
    parser.add_argument("--mutation", action="append", default=[], help=argparse.SUPPRESS)
    parser.add_argument("--skip", default="", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:  # 中文输出在 cp936 控制台下也不会因为个别字符把脚本打断
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    if args.list_ignored:
        return list_ignored()

    out_root = Path(args.out)
    if not out_root.is_absolute():
        out_root = REPO_ROOT / out_root

    if args.self_test:
        return self_test(out_root / "self_test", args.timeout)

    skip = [part.strip() for part in str(args.skip).split(",") if part.strip()]
    run = run_child(out_root, mutations=args.mutation, skip=skip, timeout=args.timeout)
    report = evaluate(run, out_root)
    document = report_as_json(report, run)
    if args.json_out:
        target = Path(args.json_out)
        if not target.is_absolute():
            target = REPO_ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document, encoding="utf-8", newline="\n")
    if args.json:
        print(document)
    else:
        print(render_table(report))
        if run.stderr.strip():
            print("（子进程 stderr 末尾 5 行）")
            for line in run.stderr.strip().splitlines()[-5:]:
                print("  " + line)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
