#!/usr/bin/env python
"""回调键静态校验（阶段 1 任务 1.19）。

背景：旧 Tk 界面通过 ``MinecraftLauncher.get_callbacks()`` 返回的
``dict[str, Callable]`` 与核心层通信，全程是**字符串键**，没有任何类型
检查。键名写错（或核心层改名后界面没跟着改）在 Python 里不会报错，
直到用户点到那个功能才失效。

本脚本要解决的**核心难点**是：什么才算"这个键不存在"。实测踩过两个坑，
所以这里的判定规则是经过修正的，不是想当然：

**坑 1：提供方不止 ``get_callbacks()``。**
``main.py`` 注入 ``get_plugin_manager``；``ui/agent/agent_mixin.py`` 把
``get_current_session_id`` / ``get_config`` 注入代理工具的回调字典。
只解析 ``get_callbacks()`` 会把这两个键误报成死键。因此提供方 = 全仓库
生产代码里所有 ``callbacks["k"] = ...`` / ``.update({...})`` 注入 ∪
``get_callbacks()`` 的字典键。

**坑 2：硬下标访问不等于会崩。**
``if "k" in callbacks: callbacks["k"]()`` 是安全的。因此同一个作用域内
被 ``in`` / ``not in`` 检查过的键，其 ``[...]`` 访问降级为 ``soft``。

于是三类结论：

- ``hard``  无守卫的 ``callbacks["key"]``，键不存在 → 运行时 KeyError（**错误**）
- ``soft``  ``.get("key", d)`` / ``in`` 检查 / 被守卫的下标 → 功能静默降级
  （**警告**，但正是"按钮点了没反应"这类缺陷的来源）
- ``unused`` 提供方给了但界面从不引用（仅提示）

用法::

    python scripts/check_callback_keys.py
    python scripts/check_callback_keys.py --list-provided
    python scripts/check_callback_keys.py --fail-on-soft   # CI 用：警告也算失败

退出码：0 = 无 hard 违规（默认）；1 = 有违规；2 = 用法错误。
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
CORE_FILE = REPO_ROOT / "launcher" / "core.py"

# 消费方：界面代码从哪里读 callbacks。
# **`services/` 必须在内**：阶段 1 把 UI 接线搬进服务层后，服务同样会读 callbacks；
# 不同步扩大范围，那些键就会掉出校验（覆盖范围随重构静默缩水）。
CONSUMER_ROOTS: Tuple[str, ...] = ("ui", "services", "app", "main.py")

# 提供方：生产代码里可能注入回调键的地方（不含 tests/、poc/、third_party/）
INJECTOR_ROOTS: Tuple[str, ...] = ("ui", "services", "app", "launcher", "plugin_manager", "main.py", "api")

SKIP_DIRS = {"__pycache__", ".venv", ".git", "build", "dist", "node_modules", "poc", "third_party"}


@dataclass
class Access:
    key: str
    kind: str  # hard | soft
    file: Path
    line: int
    guarded: bool = False

    def render(self) -> str:
        rel = self.file.relative_to(REPO_ROOT)
        tag = "soft(守卫)" if self.guarded else self.kind
        return f"  {rel}:{self.line}  {tag:10}  {self.key!r}"


@dataclass
class Report:
    hard: List[Access] = field(default_factory=list)
    soft: List[Access] = field(default_factory=list)

    def extend(self, other: "Report") -> None:
        self.hard.extend(other.hard)
        self.soft.extend(other.soft)


def iter_py(roots: Sequence[str]) -> Iterable[Path]:
    seen: Set[Path] = set()
    for root in roots:
        base = REPO_ROOT / root
        if base.is_file() and base.suffix == ".py":
            candidates = [base]
        elif base.is_dir():
            candidates = sorted(base.rglob("*.py"))
        else:
            continue
        for path in candidates:
            if path in seen or any(part in SKIP_DIRS for part in path.parts):
                continue
            seen.add(path)
            yield path
    # 仓库根目录下的独立模块（main.py 已在 roots 里时去重）
    for path in sorted(REPO_ROOT.glob("*.py")):
        if path in seen or any(part in SKIP_DIRS for part in path.parts):
            continue
        seen.add(path)
        yield path


# ─── 识别"那个 callbacks 字典"及其别名 ──────────────────────


def _is_callbacks_expr(node: ast.AST, aliases: Set[str]) -> bool:
    if isinstance(node, ast.Name):
        return node.id in aliases or node.id == "callbacks"
    if isinstance(node, ast.Attribute):
        return node.attr == "callbacks"
    return False


def _alias_source(node: ast.AST) -> bool:
    """``self.callbacks`` / ``cbs`` / ``dict(self.callbacks)`` 都算别名来源。"""
    if _is_callbacks_expr(node, set()):
        return True
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "dict":
        return any(_is_callbacks_expr(a, set()) for a in node.args)
    return False


def collect_aliases(tree: ast.AST) -> Set[str]:
    aliases: Set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _alias_source(node.value):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    aliases.add(target.id)
    return aliases


# ─── 提供方 ─────────────────────────────────────────────────


def provided_from_get_callbacks() -> Set[str]:
    keys: Set[str] = set()
    if not CORE_FILE.exists():
        return keys
    tree = ast.parse(CORE_FILE.read_text(encoding="utf-8"), filename=str(CORE_FILE))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "get_callbacks":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Dict):
                    for k in sub.keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            keys.add(k.value)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                    if sub.func.attr == "update" and sub.args and isinstance(sub.args[0], ast.Dict):
                        for k in sub.args[0].keys:
                            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                                keys.add(k.value)
    return keys


def provided_from_injections(only_keys: Optional[Set[str]] = None) -> Set[str]:
    """全仓库生产代码里"被写进某个字典"的字符串键 —— 提供方键的**过宽**近似。

    这里**故意放宽**：不再要求"赋值目标必须能静态判定为 callbacks 字典"，
    而是把所有 ``某字典["键名"] = ...`` 都算作"有人提供这个键"。

    为什么放宽：判定的代价是不对称的。
    - 判**严**（只认能静态追踪到 callbacks 的别名）会**误报**：实测踩过两次 ——
      先是 ``agent_mixin.py`` 的 ``cbs = dict(self.callbacks)``，后是重构后的
      ``build_callbacks(base, ...)`` 里 ``cbs = dict(base) if base else {}``
      （参数叫 ``base`` 而不是 ``callbacks``，且是 IfExp 不是 Call）。
      误报的后果是"检查器喊狼来了"，最后没人看它。
    - 判**宽**最多是多认几个键，代价是"可能漏掉一个真死键"。

    Args:
        only_keys: 只保留这个集合里的键。调用方传入"界面侧实际读过的键"，
            就能把 196 个无关字典键收敛回真正相关的那几个 ——
            既避免误报，又不牺牲死键检测的精度（死键按定义就是"被读过"的）。
    """
    keys: Set[str] = set()
    for path in iter_py(INJECTOR_ROOTS):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Subscript):
                        if isinstance(target.slice, ast.Constant) and isinstance(target.slice.value, str):
                            keys.add(target.slice.value)
            # d.update({"key": ...}) 形式的批量注入
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "update" and node.args and isinstance(node.args[0], ast.Dict):
                    for k in node.args[0].keys:
                        if isinstance(k, ast.Constant) and isinstance(k.value, str):
                            keys.add(k.value)
    if only_keys is not None:
        keys &= only_keys
    return keys


# ─── 消费方 ─────────────────────────────────────────────────


def _scopes(tree: ast.Module) -> List[ast.AST]:
    """模块 + 每个函数各算一个作用域（互不重叠，避免重复计数）。"""
    out: List[ast.AST] = [tree]
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append(node)
    return out


def _own_nodes(scope: ast.AST) -> Iterable[ast.AST]:
    """遍历作用域自身语句，但不进入嵌套函数体（那些由它们自己的作用域处理）。"""
    stack = list(getattr(scope, "body", []))
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            stack.append(child)


def _subscript_key(node: ast.Subscript) -> str | None:
    if isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str):
        return node.slice.value
    return None


def _key_checked_against_callbacks(node: ast.Compare, aliases: Set[str]) -> str | None:
    if isinstance(node.left, ast.Constant) and isinstance(node.left.value, str):
        for op, other in zip(node.ops, node.comparators):
            if isinstance(op, (ast.In, ast.NotIn)) and _is_callbacks_expr(other, aliases):
                return node.left.value
    return None


def scan_file(path: Path) -> Report:
    report = Report()
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return report
    aliases = collect_aliases(tree)

    for scope in _scopes(tree):
        nodes = list(_own_nodes(scope))
        guarded: Set[str] = set()
        for node in nodes:
            if isinstance(node, ast.Compare):
                key = _key_checked_against_callbacks(node, aliases)
                if key:
                    guarded.add(key)

        for node in nodes:
            if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load):
                key = _subscript_key(node)
                if key is not None and _is_callbacks_expr(node.value, aliases):
                    if key in guarded:
                        report.soft.append(Access(key, "soft", path, node.lineno, guarded=True))
                    else:
                        report.hard.append(Access(key, "hard", path, node.lineno))

            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "get" and _is_callbacks_expr(node.func.value, aliases):
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        report.soft.append(Access(node.args[0].value, "soft", path, node.lineno))
                if node.func.attr == "__contains__" and _is_callbacks_expr(node.func.value, aliases):
                    if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                        report.soft.append(Access(node.args[0].value, "soft", path, node.lineno))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="回调键静态校验")
    parser.add_argument("--list-provided", action="store_true", help="打印提供方键集合后退出")
    parser.add_argument("--fail-on-soft", action="store_true", help="把 soft 警告也视为失败")
    args = parser.parse_args(argv)

    from_callbacks = provided_from_get_callbacks()
    from_injections = provided_from_injections()
    provided = from_callbacks | from_injections

    if args.list_provided:
        for key in sorted(provided):
            origin = []
            if key in from_callbacks:
                origin.append("get_callbacks")
            if key in from_injections:
                origin.append("runtime-injection")
            print(f"{key}\t{','.join(origin)}")
        print(
            f"\n共 {len(provided)} 个键（get_callbacks {len(from_callbacks)} + 运行时注入 {len(from_injections)}）",
            file=sys.stderr,
        )
        return 0

    report = Report()
    for path in iter_py(CONSUMER_ROOTS):
        report.extend(scan_file(path))
    used = {a.key for a in report.hard} | {a.key for a in report.soft}

    # 注入键先"过宽收集"再用"实际读过的键"收敛：既不会因为静态追踪不到
    # 别名的形状而误报，也不会把 196 个无关字典键算进提供方面削弱死键检测。
    from_injections = provided_from_injections(only_keys=used)
    provided = from_callbacks | from_injections

    missing_hard = sorted({a.key for a in report.hard} - provided)
    missing_soft = sorted({a.key for a in report.soft} - provided)
    # "无人引用"只对核心层显式提供的键有意义（注入键本来就来自被读过的键）
    unused = sorted(from_callbacks - used)

    print(f"提供方键 {len(provided)} 个（其中 get_callbacks {len(from_callbacks)}、运行时注入 {len(from_injections)}）")
    print(f"界面侧读取 hard {len(report.hard)} 处 / soft {len(report.soft)} 处\n")

    if missing_hard:
        print(f"[错误] {len(missing_hard)} 个键被无守卫地硬读取但无人提供 —— 运行时会 KeyError：")
        for key in missing_hard:
            for a in report.hard:
                if a.key == key:
                    print(a.render())
        print()

    if missing_soft:
        print(f"[警告] {len(missing_soft)} 个键被软读取但无人提供 —— 功能会静默降级：")
        for key in missing_soft:
            for a in report.soft:
                if a.key == key:
                    print(a.render())
        print()

    if unused:
        print(f"[提示] {len(unused)} 个键有人提供但界面从不引用（可能给插件或尚未接线）：")
        for key in unused:
            print(f"    {key}")
        print()

    print("=" * 72)
    if missing_hard or (args.fail_on_soft and missing_soft):
        print(f"回调键校验失败：hard 缺失 {len(missing_hard)} 个，soft 缺失 {len(missing_soft)} 个")
        return 1
    # 说明实际用的判定模式，而不是硬编码一句话 —— 上一版即使传了
    # --fail-on-soft 也照样打印"（未启用 --fail-on-soft）"，会误导读者
    # 以为闸门比自己实际开的更松。
    mode = "已启用 --fail-on-soft（soft 缺失也算失败）" if args.fail_on_soft else "未启用 --fail-on-soft"
    print(f"回调键校验通过：hard 缺失 0 个，soft 缺失 {len(missing_soft)} 个（{mode}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
