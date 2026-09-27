#!/usr/bin/env python
"""i18n 键完整性校验（阶段 1 任务 1.20 的工具，阶段 4 验收复用）。

校验四件事，每件都对应真实会出的问题：

1. **语言间键集合一致** —— 某个语言漏了键，该语言下界面就显示成键名。
2. **代码引用的键必须存在** —— `_("key")` 里的 key 不在任何语言文件里，
   界面显示成键名本身（用户可见的破绽）。
3. **占位符跨语言一致** —— `"服务器安装失败: {error}"` 的 4 个语言版本
   必须都含 `{error}`；否则某语言下格式化失败，参数被静默吞掉
   （`_translate()` 对 KeyError/ValueError 是静默忽略的）。
4. **零引用键** —— 语言文件里有但代码从不引用（历史残留）。

用法::

    python scripts/check_i18n.py                     # 全部检查
    python scripts/check_i18n.py --show-missing      # 列出全部缺失键
    python scripts/check_i18n.py --show-unused 40    # 列出前 40 个零引用键
    python scripts/check_i18n.py --only placeholders # 只查占位符

退出码：0 = 通过；1 = 有问题；2 = 用法错误。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = REPO_ROOT / "ui" / "locales"

#: 视为"参照语言"：其他语言与它比对
REFERENCE_LANG = "zh_CN"

#: 代码里被当作翻译函数调用的名字
TRANSLATE_FUNCS = {"_", "tr", "_translate", "translate"}

#: 扫描范围（生产代码）。
#:
#: **`services/` 必须在内**：阶段 1 正在把业务逻辑（连带其中的 `_()` 调用）
#: 从 `ui/` 搬进 `services/`，如果这里不同步扩大范围，那些键就会掉出
#: 「缺键 / 占位符一致 / 调用点缺参」三项检查 —— 覆盖范围会随着重构**静默缩水**。
#: 这个漏洞是实测发现的：语音服务搬家后，9 个 `voice_*` 键被判成"零引用"。
CODE_ROOTS: Tuple[str, ...] = ("ui", "services", "launcher", "app", "main.py")

SKIP_DIRS = {"__pycache__", ".venv", ".git", "build", "dist", "node_modules", "poc", "third_party"}

#: 占位符形如 {name}；排除 {{ }} 转义与 {0} 位置参数（一并抓，够用）
PLACEHOLDER_RE = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*|\d+)\}(?!\})")


@dataclass
class Usage:
    key: str
    file: Path
    line: int
    #: 键字面量在行内的列偏移，用于和"事后填充"（``.format()``）按位置配对
    column: int = 0
    #: 调用点实际传进来的关键字参数名（用于"缺参"检查）
    kwargs: Set[str] = field(default_factory=set)
    #: 调用点是否带 ``**kwargs``（展开时无法静态判定，只能豁免）
    has_kwargs_splat: bool = False
    #: 调用点传入的位置参数个数（不含键本身），用于 ``{0}`` 这类位置占位符
    positional: int = 0


@dataclass
class Findings:
    lang_missing: Dict[str, List[str]] = field(default_factory=dict)
    lang_extra: Dict[str, List[str]] = field(default_factory=dict)
    used_but_absent: Dict[str, List[Usage]] = field(default_factory=dict)
    placeholder_mismatch: List[Tuple[str, Dict[str, Set[str]]]] = field(default_factory=list)
    missing_call_kwargs: List[Tuple[str, List[str], Usage]] = field(default_factory=list)
    unused: List[str] = field(default_factory=list)
    dynamic_calls: List[Usage] = field(default_factory=list)
    #: QML 侧扫描结果（返工 D 组）：引用到的静态键数量、动态键、缺键。
    qml_usages: int = 0
    qml_dynamic_calls: List[Usage] = field(default_factory=list)
    qml_absent: Dict[str, List[Usage]] = field(default_factory=dict)
    #: 路由表 / 导航分组里登记的键与其中的缺键（返工 D 组追加）。
    route_keys: List[Usage] = field(default_factory=list)
    route_absent: Dict[str, List[Usage]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """整体判定。**必须与 `report()` 的 `problems` 口径一致**（见测试
        `test_ok_property_agrees_with_exit_code`）。

        第一版写的是 `not self.lang_missing` —— 而 `check_language_parity` 总会给
        **每个**语言建一个键（内容可能是空列表），字典非空但没有任何问题，
        于是 `ok` **永远是 False**。CLI 走的是 `problems` 计数而不是这个属性，
        所以这个错一直没被看见（返工 D 组给闸门补测试时抓到）。
        `lang_extra`（参照语言没有的键）按原判据**只报不判失败**，这里保持一致。
        """
        return not (
            any(self.lang_missing.values())
            or self.used_but_absent
            or self.placeholder_mismatch
            or self.missing_call_kwargs
            or self.qml_absent
            or self.route_absent
        )


# ─── 载入语言文件 ───────────────────────────────────────────


def load_locales() -> Dict[str, Dict[str, str]]:
    locales: Dict[str, Dict[str, str]] = {}
    for path in sorted(LOCALES_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - 坏文件要报出来而不是跳过
            print(f"[错误] 无法解析 {path.name}: {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            print(f"[错误] {path.name} 顶层不是对象", file=sys.stderr)
            continue
        locales[path.stem] = {str(k): str(v) for k, v in data.items()}
    return locales


# ─── 扫描代码里的翻译调用 ───────────────────────────────────


def iter_code_files() -> Iterable[Path]:
    seen: Set[Path] = set()
    for root in CODE_ROOTS:
        base = REPO_ROOT / root
        candidates = [base] if base.is_file() else sorted(base.rglob("*.py")) if base.is_dir() else []
        for path in candidates:
            if path in seen or any(part in SKIP_DIRS for part in path.parts):
                continue
            seen.add(path)
            yield path


def collect_usages() -> Tuple[List[Usage], List[Usage]]:
    """返回 (静态键调用, 动态键调用)。

    静态调用会记录它**实际传了哪些关键字参数** —— 这是"缺参"检查的依据：
    ``_translate()`` 对 ``KeyError`` 是静默忽略的，所以少传一个参数不会报错，
    用户只会看到字面量 ``{error}``。
    """
    static: List[Usage] = []
    dynamic: List[Usage] = []
    for path in iter_code_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        fills = _deferred_fill_kwargs(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name not in TRANSLATE_FUNCS:
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                in_call = {kw.arg for kw in node.keywords if kw.arg is not None}
                deferred = fills.get((node.lineno, node.col_offset), set())
                static.append(
                    Usage(
                        key=first.value,
                        file=path,
                        line=node.lineno,
                        column=node.col_offset,
                        kwargs=in_call | deferred,
                        has_kwargs_splat=any(kw.arg is None for kw in node.keywords),
                        positional=len(node.args) - 1,
                    )
                )
            else:
                dynamic.append(
                    Usage(key=ast.unparse(first)[:60], file=path, line=node.lineno, column=node.col_offset)
                )
    return static, dynamic


# ─── 各项检查 ───────────────────────────────────────────────


def check_language_parity(locales: Dict[str, Dict[str, str]]) -> Findings:
    f = Findings()
    if REFERENCE_LANG not in locales:
        print(f"[错误] 参照语言 {REFERENCE_LANG} 缺失", file=sys.stderr)
        return f
    reference = set(locales[REFERENCE_LANG])
    for lang, table in locales.items():
        if lang == REFERENCE_LANG:
            continue
        f.lang_missing[lang] = sorted(reference - set(table))
        f.lang_extra[lang] = sorted(set(table) - reference)
    return f


def check_placeholders(locales: Dict[str, Dict[str, str]]) -> List[Tuple[str, Dict[str, Set[str]]]]:
    """同一键在各语言下的占位符集合必须一致。"""
    all_keys: Set[str] = set()
    for table in locales.values():
        all_keys |= set(table)

    bad: List[Tuple[str, Dict[str, Set[str]]]] = []
    for key in sorted(all_keys):
        per_lang: Dict[str, Set[str]] = {}
        for lang, table in locales.items():
            if key in table:
                per_lang[lang] = set(PLACEHOLDER_RE.findall(table[key]))
        if len(per_lang) < 2:
            continue
        distinct = {frozenset(v) for v in per_lang.values()}
        if len(distinct) > 1:
            bad.append((key, per_lang))
    return bad


def _is_translate_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    name = func.id if isinstance(func, ast.Name) else (func.attr if isinstance(func, ast.Attribute) else None)
    if name not in TRANSLATE_FUNCS or not node.args:
        return False
    first = node.args[0]
    return isinstance(first, ast.Constant) and isinstance(first.value, str)


def _deferred_fill_kwargs(tree: ast.AST) -> Dict[Tuple[int, int], Set[str]]:
    """收集 ``_("k").format(**kw)`` / ``_("k") % (...)`` 这类"事后填充"的参数。

    这是本仓库真实存在的写法（例如
    ``_("bedrock_installing").format(version=version)``）：``_()`` 只取模板，
    ``.format()`` 再填值。如果只看着 ``_()`` 的调用点判断，就会把这种正确写法
    误报成"缺参"。按 ``(行, 列)`` 定位到那个 ``_()`` 调用并登记它的填充参数。
    """
    fills: Dict[Tuple[int, int], Set[str]] = {}
    for node in ast.walk(tree):
        target = None
        extra: Set[str] = set()
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and _is_translate_call(node.func.value)
        ):
            target = node.func.value
            extra = {kw.arg for kw in node.keywords if kw.arg is not None}
        elif (
            isinstance(node, ast.BinOp)
            and isinstance(node.op, ast.Mod)
            and _is_translate_call(node.left)
        ):
            target = node.left  # 用 % 填充时参数是位置参数，由 positional 计数覆盖
        if target is None:
            continue
        fills.setdefault((target.lineno, target.col_offset), set()).update(extra)
    return fills


def check_call_site_kwargs(
    locales: Dict[str, Dict[str, str]],
    usages: List[Usage],
) -> List[Tuple[str, List[str], Usage]]:
    """调用点必须覆盖该键需要的全部占位符（否则界面上出现字面量 ``{error}``）。

    取**所有语言占位符的并集**作为需求：某个语言多一个占位符，而调用点没传，
    那个语言下就会显示成字面量。仅拿参照语言比对会漏掉这种情况。

    ``_translate()`` 对格式化失败是静默忽略的（返回未格式化文本），
    所以这类缺陷不会在日志里留下痕迹 —— 只能靠静态检查抓。

    参数来源有两处：``_("k", a=1)`` 的关键字参数，以及
    ``_("k").format(a=1)`` 的事后填充（见 ``_deferred_fill_kwargs``）。
    """
    required_by_key: Dict[str, Set[str]] = {}
    for table in locales.values():
        for key, text in table.items():
            required_by_key.setdefault(key, set()).update(PLACEHOLDER_RE.findall(text))

    problems: List[Tuple[str, List[str], Usage]] = []
    for usage in usages:
        required = required_by_key.get(usage.key)
        if not required:
            continue
        if usage.has_kwargs_splat:
            continue  # **kwargs 展开，无法静态判定，豁免
        provided = set(usage.kwargs)
        named = {p for p in required if not p.isdigit()}
        positional_needed = sum(1 for p in required if p.isdigit())
        missing = sorted(named - provided)
        if positional_needed and usage.positional < positional_needed:
            missing.append(f"缺 {positional_needed - usage.positional} 个位置参数")
        if missing:
            problems.append((usage.key, missing, usage))
    return problems


# ─── QML 侧取词（返工 D 组补：语言文件是 ui/locales，但 QML 也在读它） ──
#
# 为什么必须补这一项：本脚本原来的扫描范围是**Python**（`CODE_ROOTS`），
# 而 QML 侧走的是 `Tr?.map["key"]` —— 语言文件对两侧是同一份，检查却只覆盖了
# 一侧。返工 C 组给画廊补 5 个键时就是手工数的（`poc/_fix_locale_newlines.py`），
# D 组一量才发现另有 **27 个** `dev_gallery_*` 键从来没进过语言文件：
# 它们在界面上直接显示成键名本身（`dev_gallery_hint_button`），而闸门一直是绿的。

#: QML 里取键的两种写法：绑定用 `Tr?.map["k"]`（`Tr.map["k"]` 也允许），
#: 命令式用 `Tr.t("k")`。方括号里是**变量**（`Tr?.map[modelData.title_key]`）的
#: 无法静态判定，单独计入 `dynamic_calls`。
QML_KEY_RES: Tuple["re.Pattern[str]", ...] = (
    re.compile(r"""Tr\??\.map\s*\[\s*["']([A-Za-z0-9_]+)["']\s*\]"""),
    re.compile(r"""Tr\??\.t\s*\(\s*["']([A-Za-z0-9_]+)["']"""),
)
#: QML 动态取键（方括号里不是字面量）
QML_DYNAMIC_RE = re.compile(r"""Tr\??\.map\s*\[\s*([^"'\]]+?)\s*\]""")

#: QML 扫描根（相对仓库根）
QML_ROOTS: Tuple[str, ...] = ("qml",)


def iter_qml_files() -> List[Path]:
    out: List[Path] = []
    for root in QML_ROOTS:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.qml")):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            out.append(path)
    return out


def strip_qml_comments(text: str) -> str:
    """把 QML 注释抹成空格（换行保留，所以行号不变）。

    为什么必须抹：本仓库的组件文件头大量用 `Tr?.map[…]` 举例说明写法，
    不抹注释就会把**示例**当成"代码引用了这个键"（第一版就是这么把
    `FmPage.qml` 注释里的 `versions_none` 报成缺键的），注释里的省略号
    也会被当成动态键。**字符串字面量必须留着** —— 取键的写法就在字符串里。

    坑：`//` 也出现在 URL 里（`image://fmcl-icon/…`），所以要看"当前是否在字符串内"。
    模板字符串（反引号）与正则字面量本仓库没有用到，这里不处理。
    """
    out: List[str] = []
    index = 0
    length = len(text)
    quote = ""
    while index < length:
        char = text[index]
        if quote:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char in "\"'":
            quote = char
            out.append(char)
            index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "/":
            while index < length and text[index] != "\n":
                out.append(" ")
                index += 1
            continue
        if char == "/" and index + 1 < length and text[index + 1] == "*":
            while index < length and not (text[index] == "*" and index + 1 < length
                                          and text[index + 1] == "/"):
                out.append("\n" if text[index] == "\n" else " ")
                index += 1
            out.append("  ")
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def qml_usages_in_text(path: Path, text: str) -> Tuple[List[Usage], List[Usage]]:
    """从**一段 QML 文本**里取键（注释先抹掉），返回（静态键, 动态键）。

    单独抽出来是为了能被测试直接喂字符串：闸门自己也要被测
    （见 `tests/test_i18n_gate.py`）。
    """
    scanned = strip_qml_comments(text)
    static: List[Usage] = []
    dynamic: List[Usage] = []
    found: Set[Tuple[int, str]] = set()
    for regex in QML_KEY_RES:
        for match in regex.finditer(scanned):
            key = match.group(1)
            line = scanned.count("\n", 0, match.start()) + 1
            if (line, key) in found:
                continue
            found.add((line, key))
            static.append(Usage(key=key, file=path, line=line))
    for match in QML_DYNAMIC_RE.finditer(scanned):
        inner = match.group(1).strip()
        if inner.startswith(("'", '"')):
            continue  # 字面量，上面已经收过
        line = scanned.count("\n", 0, match.start()) + 1
        dynamic.append(Usage(key=f"<{inner}>", file=path, line=line))
    return static, dynamic


def collect_qml_usages() -> Tuple[List[Usage], List[Usage]]:
    """扫 `qml/**` 里的取键调用，返回（静态键, 动态键）。注释先抹掉。"""
    static: List[Usage] = []
    dynamic: List[Usage] = []
    for path in iter_qml_files():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"[错误] 读不了 {path}: {e}", file=sys.stderr)
            continue
        file_static, file_dynamic = qml_usages_in_text(path, text)
        static.extend(file_static)
        dynamic.extend(file_dynamic)
    return static, dynamic


def check_qml_keys(
    locales: Dict[str, Dict[str, str]],
    usages: List[Usage],
) -> Dict[str, List[Usage]]:
    """QML 引用、但**任何语言文件里都没有**的键。

    判据与检查 2 相同（缺键在界面上显示成键名），只是扫描面从 Python 换到 QML。
    注意：QML 里普遍写着 ``Tr?.map[k] ?? k`` 的兜底 —— 兜底**不**算有词条，
    它只是把"找不到"变成"显示键名"，用户看到的仍然是英文键名。
    """
    all_keys: Set[str] = set()
    for table in locales.values():
        all_keys |= set(table)
    absent: Dict[str, List[Usage]] = {}
    for usage in usages:
        if usage.key not in all_keys:
            absent.setdefault(usage.key, []).append(usage)
    return absent


# ─── 路由表里的 i18n 键（返工 D 组追加的一项） ────────────────────
#
# 为什么还要单独查这一处：`app/bridges/nav_bridge.py` 的 `_ROUTE_TABLE` 把**键名写在表里**
# （`("versions/detail", "version_detail_title", …)`），QML 侧用
# `Tr?.map[modelData.title_key]` **动态**解析 —— 于是检查 2（扫 Python 的 `_()` 调用）
# 与检查 6（扫 QML 字面量）**都看不见它**。后果是真机上直接可见的：
# 面包屑与页头显示成 `version_detail_title` 这个键名本身
# （D 组就是"去真机截图上多看一眼"才发现组件画廊的页头写着 `dev_gallery_title`）。
#
# 解析方式：**静态读 AST**，不 import 桥（闸门不该为了查键去拉起 Qt 依赖）。
# 取 `_ROUTE_TABLE` 的第 1 列（title_key）与第 5 列（description_key）、
# `NAV_GROUPS` 的第 1 列（组标题键）。

#: 路由表的列：`(id, title_key, icon, parent, qml, description_key)`。
ROUTE_TABLE_FILE = "app/bridges/nav_bridge.py"
ROUTE_KEY_TABLES: Tuple[Tuple[str, Tuple[int, ...]], ...] = (
    ("_ROUTE_TABLE", (1, 5)),
    ("NAV_GROUPS", (1,)),
)


def route_table_keys() -> List[Usage]:
    """路由表 / 导航分组里登记的所有 i18n 键（静态解析，不 import app 代码）。"""
    path = REPO_ROOT / ROUTE_TABLE_FILE
    if not path.is_file():
        return []
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as e:
        print(f"[错误] 解析 {ROUTE_TABLE_FILE} 失败: {e}", file=sys.stderr)
        return []
    usages: List[Usage] = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
            value = node.value
        elif isinstance(node, ast.Assign):
            targets = list(node.targets)
            value = node.value
        else:
            continue
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        for table_name, columns in ROUTE_KEY_TABLES:
            if table_name not in names or not isinstance(value, ast.Tuple):
                continue
            for row in value.elts:
                if not isinstance(row, ast.Tuple):
                    continue
                for column in columns:
                    if len(row.elts) <= column:
                        continue
                    cell = row.elts[column]
                    if not isinstance(cell, ast.Constant) or not isinstance(cell.value, str):
                        continue
                    key = cell.value.strip()
                    if key:
                        usages.append(Usage(key=key, file=path, line=int(cell.lineno)))
    return usages


def build_findings() -> Findings:
    locales = load_locales()
    static, dynamic = collect_usages()
    f = check_language_parity(locales)
    f.dynamic_calls = dynamic
    f.placeholder_mismatch = check_placeholders(locales)
    f.missing_call_kwargs = check_call_site_kwargs(locales, static)
    qml_static, qml_dynamic = collect_qml_usages()
    f.qml_usages = len(qml_static)
    f.qml_dynamic_calls = qml_dynamic
    f.qml_absent = check_qml_keys(locales, qml_static)
    f.route_keys = route_table_keys()
    f.route_absent = check_qml_keys(locales, f.route_keys)

    all_keys: Set[str] = set()
    for table in locales.values():
        all_keys |= set(table)

    used: Set[str] = set()
    for usage in static:
        used.add(usage.key)
        if usage.key not in all_keys:
            f.used_but_absent.setdefault(usage.key, []).append(usage)

    f.unused = sorted(all_keys - used)
    return f


# ─── 输出 ───────────────────────────────────────────────────


def report(f: Findings, show_missing: bool, show_unused: int, locales_count: int) -> int:
    total_keys = 0
    for path in sorted(LOCALES_DIR.glob("*.json")):
        try:
            total_keys = max(total_keys, len(json.loads(path.read_text(encoding="utf-8"))))
        except Exception:  # noqa: BLE001
            pass

    print(f"语言文件 {locales_count} 个，参照语言 {REFERENCE_LANG} 共 {total_keys} 个键\n")
    problems = 0

    if f.lang_missing or f.lang_extra:
        print("[检查 1] 语言间键集合一致性")
        for lang, keys in sorted(f.lang_missing.items()):
            state = "通过" if not keys else f"缺失 {len(keys)} 个"
            print(f"  {lang}: {state}")
            if keys:
                problems += len(keys)
                if show_missing:
                    for k in keys:
                        print(f"      - {k}")
        for lang, keys in sorted(f.lang_extra.items()):
            if keys:
                print(f"  {lang}: 多出 {len(keys)} 个（参照语言没有）")
                if show_missing:
                    for k in keys:
                        print(f"      + {k}")
    else:
        print("[检查 1] 语言间键集合一致性：通过")
    print()

    print("[检查 2] 代码引用但语言文件缺失的键")
    if not f.used_but_absent:
        print("  通过（0 个）")
    else:
        problems += len(f.used_but_absent)
        print(f"  缺失 {len(f.used_but_absent)} 个 —— 这些键在界面上会显示成键名本身：")
        for key, usages in sorted(f.used_but_absent.items()):
            print(f"    {key!r}")
            for u in usages:
                print(f"        {u.file.relative_to(REPO_ROOT)}:{u.line}")
    print()

    print("[检查 3] 占位符跨语言一致性")
    if not f.placeholder_mismatch:
        print("  通过（0 个）")
    else:
        problems += len(f.placeholder_mismatch)
        print(f"  不一致 {len(f.placeholder_mismatch)} 个 —— 这些键在部分语言下参数会被静默丢弃：")
        for key, per_lang in f.placeholder_mismatch:
            print(f"    {key!r}")
            for lang, ph in sorted(per_lang.items()):
                print(f"        {lang}: {sorted(ph) if ph else '（无占位符）'}")
    print()

    print("[检查 5] 调用点是否传齐了所需参数（缺参会让界面显示字面量 {xxx}）")
    if not f.missing_call_kwargs:
        print("  通过（0 处）")
    else:
        problems += len(f.missing_call_kwargs)
        print(f"  缺参 {len(f.missing_call_kwargs)} 处：")
        for key, missing, usage in f.missing_call_kwargs:
            rel = usage.file.relative_to(REPO_ROOT)
            print(f"    {key!r} 缺 {', '.join(missing)}")
            print(f"        {rel}:{usage.line}  调用点已传: {sorted(usage.kwargs) or '（无）'}")
    print()

    print("[检查 6] QML 引用了、但语言文件里没有的键")
    print(f"  扫到 {f.qml_usages} 处静态取键（`Tr?.map[\"k\"]` / `Tr.t(\"k\")`）")
    if not f.qml_absent:
        print("  通过（0 个）")
    else:
        problems += len(f.qml_absent)
        print(f"  缺 {len(f.qml_absent)} 个 —— 这些键在界面上会显示成键名本身"
              "（QML 普遍写着 `?? k` 的兜底，兜底只是把'找不到'变成'显示键名'）：")
        for key, usages in sorted(f.qml_absent.items()):
            files = "、".join(sorted({
                f"{u.file.relative_to(REPO_ROOT)}:{u.line}" for u in usages}))
            print(f"    {key!r}  {len(usages)} 处：{files}")
    if f.qml_dynamic_calls:
        print(f"  另有 {len(f.qml_dynamic_calls)} 处**动态键**（方括号里是变量）无法静态判定：")
        for u in f.qml_dynamic_calls[:10]:
            print(f"      {u.file.relative_to(REPO_ROOT)}:{u.line}  Tr.map[{u.key[1:-1]}]")
        if len(f.qml_dynamic_calls) > 10:
            print(f"      ...（还有 {len(f.qml_dynamic_calls) - 10} 处）")
    print()

    print("[检查 7] 路由表 / 导航分组登记的键是否存在")
    print(f"  扫到 {len(f.route_keys)} 个键（`{ROUTE_TABLE_FILE}` 的 `_ROUTE_TABLE` 与 `NAV_GROUPS`）")
    if not f.route_absent:
        print("  通过（0 个）")
    else:
        problems += len(f.route_absent)
        print(f"  缺 {len(f.route_absent)} 个 —— 这些键会显示在**面包屑与页头**上"
              "（QML 侧是 `Tr?.map[modelData.title_key]`，动态解析，前面两项检查都看不见）：")
        for key, usages in sorted(f.route_absent.items()):
            where = "、".join(f"{u.file.relative_to(REPO_ROOT)}:{u.line}" for u in usages)
            print(f"    {key!r}  {where}")
    print()

    print("[检查 4] 语言文件有但代码从不引用的键")
    print(f"  共 {len(f.unused)} 个")
    if show_unused:
        for key in f.unused[:show_unused]:
            print(f"      - {key}")
        if len(f.unused) > show_unused:
            print(f"      ...（还有 {len(f.unused) - show_unused} 个）")
    if f.dynamic_calls:
        print(f"  另有 {len(f.dynamic_calls)} 处**动态键**调用无法静态判定（其键不会被计入'已引用'）：")
        for u in f.dynamic_calls[:10]:
            print(f"      {u.file.relative_to(REPO_ROOT)}:{u.line}  _({u.key})")
        if len(f.dynamic_calls) > 10:
            print(f"      ...（还有 {len(f.dynamic_calls) - 10} 处）")
    print()

    print("=" * 72)
    if problems:
        print(f"i18n 校验失败：{problems} 处问题")
        return 1
    print("i18n 校验通过：键集合一致、无缺失键、占位符一致")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="i18n 键完整性校验")
    parser.add_argument("--show-missing", action="store_true", help="列出全部缺失键")
    parser.add_argument("--show-unused", type=int, default=0, metavar="N", help="列出前 N 个零引用键")
    parser.add_argument(
        "--only",
        choices=["parity", "missing", "placeholders", "unused", "qml", "route"],
        help="只跑某一项检查",
    )
    args = parser.parse_args(argv)

    if not LOCALES_DIR.exists():
        print(f"找不到语言目录: {LOCALES_DIR}", file=sys.stderr)
        return 2

    f = build_findings()
    locales_count = len(list(LOCALES_DIR.glob("*.json")))

    # `--only X` 的语义：**只留 X**，其余检查的结果全部清掉（否则别的检查的缺键会一起报）。
    # 每加一项新检查都要在这五个分支里补上它，否则 `--only` 会误报 ——
    # 这也是把它写成"先全清、再按项保留"更不容易漏的原因（下面按项清）。
    if args.only == "parity":
        f.used_but_absent.clear()
        f.placeholder_mismatch.clear()
        f.qml_absent.clear()
        f.route_absent.clear()
    elif args.only == "missing":
        f.lang_missing.clear()
        f.lang_extra.clear()
        f.placeholder_mismatch.clear()
        f.qml_absent.clear()
        f.route_absent.clear()
    elif args.only == "placeholders":
        f.lang_missing.clear()
        f.lang_extra.clear()
        f.used_but_absent.clear()
        f.qml_absent.clear()
        f.route_absent.clear()
    elif args.only == "unused":
        f.lang_missing.clear()
        f.lang_extra.clear()
        f.used_but_absent.clear()
        f.placeholder_mismatch.clear()
        f.qml_absent.clear()
        f.route_absent.clear()
        args.show_unused = args.show_unused or 50
    elif args.only == "qml":
        f.lang_missing.clear()
        f.lang_extra.clear()
        f.used_but_absent.clear()
        f.placeholder_mismatch.clear()
        f.missing_call_kwargs.clear()
        f.route_absent.clear()
    elif args.only == "route":
        f.lang_missing.clear()
        f.lang_extra.clear()
        f.used_but_absent.clear()
        f.placeholder_mismatch.clear()
        f.missing_call_kwargs.clear()
        f.qml_absent.clear()

    return report(f, args.show_missing, args.show_unused, locales_count)


if __name__ == "__main__":
    raise SystemExit(main())
