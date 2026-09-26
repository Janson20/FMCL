#!/usr/bin/env python
"""分层纯净度静态检查（阶段 1 验收标准第 1 条，CI 固化入口）。

用法::

    python scripts/check_services_purity.py            # 检查全部规则
    python scripts/check_services_purity.py --group services
    python scripts/check_services_purity.py --list      # 打印规则与扫描范围
    python scripts/check_services_purity.py --show-ok   # 连通过的文件也打印

退出码：0 = 全部通过；1 = 存在违规；2 = 用法错误。

为什么用 AST 而不是 grep：注释、docstring、字符串里出现的 "tkinter"
（例如本仓库的文档与规则说明）不该被算成违规，反之
``importlib.import_module("tkinter")`` 这类动态导入又必须算。
AST 能同时处理这两类，grep 两头都会错。
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# 各层禁止引入的顶层模块名（含其子模块）
UI_MODULES: Tuple[str, ...] = (
    "tkinter",
    "customtkinter",
    "tkinterdnd2",
    "tkinterweb",
    "tkhtmlview",
    "PySide6",
    "shiboken6",
    "PyQt5",
    "PyQt6",
)
UI_INTERNAL = "ui"


@dataclass
class Rule:
    """一条分层规则。"""

    group: str
    label: str
    roots: Sequence[str]
    forbidden: Tuple[str, ...]
    allow: Tuple[str, ...] = ()
    description: str = ""


RULES: Tuple[Rule, ...] = (
    Rule(
        group="services",
        label="services 层零 UI 依赖",
        roots=("services",),
        forbidden=UI_MODULES + (UI_INTERNAL,),
        description="业务逻辑必须与界面无关；UI 差异通过 app.ports.UIPort 注入",
    ),
    Rule(
        group="core",
        label="core/launcher 层零 UI 依赖",
        roots=("launcher",),
        forbidden=UI_MODULES + (UI_INTERNAL,),
        description="核心层不得弹窗、不得写剪贴板到 Tk；改用 AppContext.ui 端口",
    ),
    Rule(
        group="entry",
        label="入口文件不得内联 Tk 界面代码",
        roots=("main.py",),
        forbidden=("tkinter",),
        allow=("customtkinter",),
        description="splash 与启动期错误弹窗属于 ui/ 层；入口只负责装配（阶段 4 会整体切换为 QML）",
    ),
    Rule(
        group="core",
        label="移植中的根模块零 UI 依赖",
        roots=(
            "achievement_engine.py",
            "achievement_sync.py",
            "backup_manager.py",
            "curseforge.py",
            "downloader.py",
            "download_config.py",
            "mirror.py",
            "modrinth.py",
            "secure_storage.py",
            "structured_logger.py",
            "updater.py",
            "validation.py",
            "version_utils.py",
            "config.py",
            "plugin_manager",
        ),
        forbidden=UI_MODULES + (UI_INTERNAL,),
        description="这些模块被服务层与 CLI 模式共用，不能被界面绑死",
    ),
)


@dataclass
class Violation:
    file: Path
    line: int
    column: int
    module: str
    detail: str

    def render(self) -> str:
        rel = self.file.relative_to(REPO_ROOT)
        return f"  {rel}:{self.line}:{self.column}  禁止依赖 {self.module!r}（{self.detail}）"

    def key(self) -> Tuple[str, str]:
        """用于匹配已登记例外的键：文件名 + 被依赖模块。"""
        return (self.file.relative_to(REPO_ROOT).as_posix(), self.module)


@dataclass(frozen=True)
class KnownViolation:
    """已登记的例外：有明确归属任务与理由，不算失败但会被打印出来。

    为什么要有它：一个长期变红的检查器最后一定会被忽略，那样它就再也
    抓不到**新增**的违规了。把已知项显式登记、并把"未知项"作为失败条件，
    才能让 CI 保持有效。每条例外都必须写清归属任务号，防止变成垃圾场。
    """

    path: str
    module: str
    task: str
    reason: str

    def matches(self, violation: Violation) -> bool:
        return violation.key() == (self.path, self.module)


#: 当前已知且已排期的分层违规。**新增违规不得往这里加**，必须直接修；
#: 只有"该依赖要等某个后续任务才能移除"的情况才允许登记。
#:
#: 已消除的登记（留痕，说明登记表确实在缩短而不是只增不减）：
#: - ``launcher/core.py -> ui.music_source``（归属 1.4）：音源适配整包搬进
#:   ``services/music_source/`` 后，core.py 改为从服务层 import，例外已移除。
#: - ``modrinth.py -> ui.agent.providers.jingdu``（归属 1.14）：Agent 的
#:   providers/tools 等模块搬进 ``services/agent/`` 后，modrinth.py 改为
#:   ``from services.agent.providers.jingdu import JingduProvider``，例外已移除。
KNOWN_VIOLATIONS: Tuple[KnownViolation, ...] = ()


@dataclass
class FileReport:
    path: Path
    violations: List[Violation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


def iter_python_files(roots: Sequence[str]) -> Iterable[Path]:
    """展开规则里的扫描根，跳过缓存与虚拟环境。"""
    skip_dirs = {"__pycache__", ".venv", ".git", "build", "dist", "node_modules"}
    for root in roots:
        base = REPO_ROOT / root
        if base.is_file():
            if base.suffix == ".py":
                yield base
            continue
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if any(part in skip_dirs for part in path.parts):
                continue
            yield path


def _top_level(module: str) -> str:
    return module.split(".", 1)[0]


def _is_forbidden(module: str, forbidden: Sequence[str], allow: Sequence[str]) -> bool:
    if not module:
        return False
    if any(module == a or module.startswith(a + ".") for a in allow):
        return False
    top = _top_level(module)
    for item in forbidden:
        if module == item or top == item:
            return True
    return False


class _ImportVisitor(ast.NodeVisitor):
    """收集文件中所有 import 的模块名与位置。"""

    def __init__(self) -> None:
        self.found: List[Tuple[str, int, int, str]] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.found.append((alias.name, node.lineno, node.col_offset, "import"))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level and node.level > 0:
            return  # 相对导入不出包，无需检查
        if node.module:
            self.found.append((node.module, node.lineno, node.col_offset, "from-import"))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # __import__("tkinter") / importlib.import_module("tkinter")
        name = None
        if isinstance(node.func, ast.Name) and node.func.id == "__import__":
            name = "dynamic-import"
        elif isinstance(node.func, ast.Attribute) and node.func.attr in ("import_module", "__import__"):
            name = "dynamic-import"
        if name and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            self.found.append((node.args[0].value, node.lineno, node.col_offset, name))
        self.generic_visit(node)


def check_file(path: Path, rule: Rule) -> FileReport:
    report = FileReport(path=path)
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        report.violations.append(
            Violation(path, 0, 0, "<unreadable>", f"读取失败: {type(e).__name__}: {e}")
        )
        return report

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as e:
        report.violations.append(
            Violation(path, e.lineno or 0, e.offset or 0, "<syntax-error>", f"{e.msg}")
        )
        return report

    visitor = _ImportVisitor()
    visitor.visit(tree)
    for module, lineno, col, kind in visitor.found:
        if _is_forbidden(module, rule.forbidden, rule.allow):
            report.violations.append(Violation(path, lineno, col, module, kind))
    return report


def run_group(rules: Sequence[Rule], show_ok: bool = False, strict: bool = False) -> Tuple[int, int, int]:
    """执行一组规则，返回 (文件数, 未登记违规数, 已登记例外的违规数)。"""
    checked = 0
    unknown_violations = 0
    known_violations = 0
    for rule in rules:
        files = list(iter_python_files(rule.roots))
        print(f"\n[{rule.label}]  扫描 {len(files)} 个文件  —— {rule.description}")
        group_unknown = 0
        group_known = 0
        for path in files:
            report = check_file(path, rule)
            checked += 1
            if report.ok:
                if show_ok:
                    print(f"  OK   {path.relative_to(REPO_ROOT)}")
                continue
            unknown = []
            known = []
            for v in report.violations:
                match = next((k for k in KNOWN_VIOLATIONS if k.matches(v)), None)
                (known if match is not None else unknown).append((v, match))

            if unknown:
                group_unknown += len(unknown)
                print(f"  FAIL {path.relative_to(REPO_ROOT)}")
                for v, _ in unknown:
                    print(v.render())
            if known:
                group_known += len(known)
                if not unknown:
                    print(f"  已知例外 {path.relative_to(REPO_ROOT)}")
                for v, match in known:
                    print(f"{v.render()}  [已登记 -> 任务 {match.task}]")
                    print(f"        理由: {match.reason}")
        unknown_violations += group_unknown
        known_violations += group_known
        parts = []
        parts.append("通过" if group_unknown == 0 else f"失败（{group_unknown} 处未登记）")
        if group_known:
            parts.append(f"另有 {group_known} 处已登记例外")
        print(f"  -> {'；'.join(parts)}")
    return checked, unknown_violations, known_violations


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="分层纯净度静态检查")
    parser.add_argument("--group", choices=sorted({r.group for r in RULES}), help="只检查某一组规则")
    parser.add_argument("--list", action="store_true", help="打印规则与扫描范围后退出")
    parser.add_argument("--list-known", action="store_true", help="打印已登记例外后退出")
    parser.add_argument("--show-ok", action="store_true", help="连通过的文件也打印")
    parser.add_argument("--strict", action="store_true", help="已登记例外也算失败（阶段收口时用）")
    args = parser.parse_args(argv)

    if args.list:
        for rule in RULES:
            files = list(iter_python_files(rule.roots))
            print(f"[{rule.group}] {rule.label}")
            print(f"  扫描根: {', '.join(rule.roots)}  (实际 {len(files)} 个 .py)")
            print(f"  禁止:   {', '.join(rule.forbidden)}")
            print(f"  说明:   {rule.description}")
        return 0

    if args.list_known:
        for k in KNOWN_VIOLATIONS:
            print(f"{k.path}  ->  {k.module}")
            print(f"  归属任务: {k.task}")
            print(f"  理由:     {k.reason}")
        print(f"\n共 {len(KNOWN_VIOLATIONS)} 条已登记例外", file=sys.stderr)
        return 0

    rules = [r for r in RULES if not args.group or r.group == args.group]
    if not rules:
        print(f"没有匹配的规则组: {args.group}", file=sys.stderr)
        return 2

    checked, unknown, known = run_group(rules, show_ok=args.show_ok, strict=args.strict)
    print("\n" + "=" * 72)
    if unknown == 0 and (known == 0 or not args.strict):
        print(f"分层检查通过：{checked} 个文件，0 处未登记违规，{known} 处已登记例外")
        if known:
            print("已登记例外见 --list-known；各自归属任务完成后必须移除登记。")
        return 0
    if unknown:
        print(f"分层检查失败：{checked} 个文件，{unknown} 处未登记违规，{known} 处已登记例外")
        print("修复方向见 docs/refactor/03-phases.md 阶段 1 任务 1.3 / 1.16")
        return 1
    print(f"分层检查失败（--strict）：{checked} 个文件，{known} 处已登记例外未清除")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
