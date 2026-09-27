"""i18n 闸门（`scripts/check_i18n.py`）自己的测试 —— 重点是返工 D 组新加的「检查 6」。

## 背景：检查 6 之前，QML 侧的取键从来没被查过

语言文件 `ui/locales/*.json` 是 Tk 与 QML **两侧共用**的，而闸门原来的扫描范围只有
Python（`CODE_ROOTS = ui, services, launcher, app, main.py`）。QML 走
`Tr?.map["key"]`，于是**QML 引用的键从来没进过任何检查** —— D 组补上检查 6 之后，
一次就报出 32 个缺键（31 个 `dev_gallery_*` + `version_not_found`），
而闸门在此之前一直是绿的。这些键在界面上显示成键名本身（QML 普遍写着 `?? key`
的兜底，兜底只是把"找不到"变成"显示键名"）。

## 这个文件钉住四件事

1. **取键的写法都认**：`Tr?.map["k"]` / `Tr.map["k"]` / `Tr?.t("k")` / `Tr.t('k')`；
2. **注释不算引用**：文件头里用 `Tr?.map[…]` 举例的部分不能被当成代码
   （第一版没抹注释，于是 `FmPage.qml` 注释里的 `versions_none` 被报成缺键）；
3. **URL 里的 `//` 不会把行尾吃掉**（`image://fmcl-icon/…` 后面还跟着取键写法）；
4. **真的会红**：给一段引用了不存在键的 QML，检查必须报出来；
   并且**当前仓库是干净的**（32 个键已经补齐），否则这道闸门形同虚设。

跑法::

    .venv\\Scripts\\python.exe -m pytest tests/test_i18n_gate.py -q
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
from pathlib import Path
from typing import List, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_i18n.py"


def _load_gate():
    """按文件路径加载 `scripts/check_i18n.py`（`scripts/` 不是包）。

    与 `tests/test_qml_rules_gate.py:_load_gate` 同一做法：必须先登记进 `sys.modules`，
    否则 `@dataclass` 处理类时按 `cls.__module__` 反查会 AttributeError。
    """
    cached = sys.modules.get("_fmcl_i18n_gate")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("_fmcl_i18n_gate", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _run(argv: List[str]) -> Tuple[int, str]:
    gate = _load_gate()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = gate.main(list(argv))
    return code, buffer.getvalue()


def _keys(text: str) -> Tuple[List[str], List[str]]:
    """一段 QML 文本 → （静态键, 动态键）。"""
    gate = _load_gate()
    static, dynamic = gate.qml_usages_in_text(Path("synthetic.qml"), text)
    return [usage.key for usage in static], [usage.key for usage in dynamic]


# ─── 1. 取键的写法 ───────────────────────────────────────────────


def test_all_four_key_spellings_are_picked_up() -> None:
    static, _ = _keys("""
Text { text: Tr?.map["gallery_intro"] ?? "gallery_intro" }
Text { text: Tr.map["plain_map"] ?? "plain_map" }
Item { Component.onCompleted: console.log(Tr?.t("imperative_optional")) }
Item { Component.onCompleted: console.log(Tr.t('imperative_plain')) }
""")
    assert static == ["gallery_intro", "plain_map", "imperative_optional", "imperative_plain"]


def test_dynamic_keys_are_reported_separately() -> None:
    static, dynamic = _keys("""
Text { text: Tr?.map[modelData.title_key] ?? modelData.title_key }
Text { text: Tr?.map["literal_one"] ?? "literal_one" }
""")
    assert static == ["literal_one"], "字面量键要进静态那一栏"
    assert dynamic == ["<modelData.title_key>"], f"动态键应单独报：{dynamic}"


# ─── 2. 注释不算引用（第一版就是栽在这里） ────────────────────────


def test_keys_inside_comments_are_not_counted() -> None:
    static, dynamic = _keys("""
// 真实页面这样覆盖：`emptyText: Tr?.map["versions_none"] ?? "versions_none"`
/* 块注释里也一样：Tr.map["block_comment_key"] */
Text { text: Tr?.map["real_key"] ?? "real_key" }   // 行尾注释 Tr.map["trailing_key"]
""")
    assert static == ["real_key"], f"注释里的键被算成引用了：{static}"
    assert dynamic == [], f"注释里的 `Tr.map[…]` 被算成动态键了：{dynamic}"


def test_url_with_double_slash_does_not_eat_the_rest_of_the_line() -> None:
    """`image://fmcl-icon/…` 里的 `//` 不是行注释 —— 抹注释要看着字符串状态走。"""
    static, _ = _keys("""
FmIcon { name: "guide"; source: "image://fmcl-icon/guide?color=%23ffffff" }
Text { text: Tr?.map["after_url"] ?? "after_url" }
Text { text: "文字里的 // 也不是注释"; }
Text { text: Tr?.map["after_string_slash"] ?? "after_string_slash" }
""")
    assert static == ["after_url", "after_string_slash"], static


def test_stripping_comments_keeps_line_numbers() -> None:
    """抹注释必须**保留换行**，否则报出来的行号全是错的。"""
    gate = _load_gate()
    text = 'A\n// comment\nB\n/* multi\nline */\nC\n'
    stripped = gate.strip_qml_comments(text)
    assert stripped.count("\n") == text.count("\n")
    assert stripped.splitlines()[0] == "A" and stripped.splitlines()[2] == "B"
    assert stripped.splitlines()[5] == "C"


# ─── 3. 缺键检查真的会红 ────────────────────────────────────────


def test_missing_qml_key_is_reported(tmp_path: Path) -> None:
    """给一段引用了不存在键的 QML，`check_qml_keys` 必须报出来（不是空断言）。"""
    gate = _load_gate()
    static, _ = gate.qml_usages_in_text(tmp_path / "x.qml", """
FmPage { emptyText: Tr?.map["definitely_missing_key"] ?? "definitely_missing_key" }
FmPage { title: Tr?.map["app_title"] ?? "app_title" }
""")
    locales = gate.load_locales()
    absent = gate.check_qml_keys(locales, static)
    assert "definitely_missing_key" in absent, f"缺键没被报出来：{absent}"
    assert "app_title" not in absent, "已有的键被误报成缺键"


def test_gate_scans_the_real_qml_tree() -> None:
    """扫描面必须真的盖住 `qml/**`（没人调用的检查等于没有）。"""
    gate = _load_gate()
    files = gate.iter_qml_files()
    assert len(files) >= 50, f"只扫到 {len(files)} 个 QML 文件"
    assert any(path.name == "Gallery.qml" for path in files)
    static, _ = gate.collect_qml_usages()
    assert len(static) >= 50, f"只取到 {len(static)} 处静态取键"


# ─── 4. 路由表里的键（检查 7） ──────────────────────────────────


def test_route_table_keys_are_collected_from_the_ast() -> None:
    """路由表 / 导航分组里的键要能被静态解析出来（`nav_bridge.py` 的两张表）。"""
    gate = _load_gate()
    usages = gate.route_table_keys()
    keys = {usage.key for usage in usages}
    assert len(usages) >= 50, f"只解析出 {len(usages)} 个键，AST 解析可能没覆盖两张表"
    assert "version_detail_title" in keys, "`_ROUTE_TABLE` 的 title_key 列没取到"
    assert "home" not in keys, "把路由 id 当成键取进来了（列号错了）"
    for usage in usages:
        assert usage.file.name == "nav_bridge.py" and usage.line > 0


def test_route_key_check_would_flag_a_missing_key(tmp_path: Path) -> None:
    """缺键检查对路由表这一批同样有牙齿（不是只对 QML 那批）。"""
    gate = _load_gate()
    locales = gate.load_locales()
    fake = [gate.Usage(key="route_title_that_does_not_exist", file=tmp_path / "nav_bridge.py", line=1)]
    absent = gate.check_qml_keys(locales, fake)
    assert "route_title_that_does_not_exist" in absent


def test_repository_route_keys_all_exist() -> None:
    """当前仓库里路由表登记的键必须全部有词条。

    这一条抓的是一类**真机上直接可见**的缺陷：QML 用 `Tr?.map[modelData.title_key]`
    动态解析路由标题，所以检查 2（Python）与检查 6（QML 字面量）**都看不见它** ——
    19 个详情页标题就是这么一直缺着的，界面上显示成 `version_detail_title` 这样的键名。
    """
    gate = _load_gate()
    findings = gate.build_findings()
    missing = sorted(findings.route_absent)
    assert missing == [], f"路由表登记的键里有 {len(missing)} 个没有词条：{missing[:8]}"


def test_cli_route_only_mode_exits_zero() -> None:
    code, output = _run(["--only", "route"])
    assert code == 0, output[-1500:]
    assert "[检查 7] 路由表 / 导航分组登记的键是否存在" in output
    assert "通过（0 个）" in output


# ─── 5. 仓库当前是干净的（缺键已全部补齐） ──────────────────────


def test_repository_has_no_missing_qml_keys() -> None:
    gate = _load_gate()
    findings = gate.build_findings()
    missing = sorted(findings.qml_absent)
    assert missing == [], (
        f"QML 引用了但语言文件里没有这 {len(missing)} 个键"
        f"（界面上会显示成键名）：{missing[:8]}"
    )
    assert findings.ok is True, "闸门整体判据应当是绿的"


def test_cli_qml_only_mode_exits_zero_and_reports_the_scan() -> None:
    code, output = _run(["--only", "qml"])
    assert code == 0, output[-2000:]
    assert "[检查 6] QML 引用了、但语言文件里没有的键" in output
    assert "扫到" in output and "通过（0 个）" in output


def test_ok_property_agrees_with_exit_code() -> None:
    """`Findings.ok` 与 CLI 退出码必须同口径。

    这一条抓的是一个**一直被藏着的错**：`ok` 原来写成 `not self.lang_missing`，
    而 `check_language_parity` 总会给每个语言建一个键（内容可以是空列表）——
    字典非空时 `ok` 恒为 False。CLI 走的是 `problems` 计数，所以界面上一路绿灯，
    属性却是错的。任何"看 `ok` 判断要不要失败"的调用方都会被它坑。
    """
    gate = _load_gate()
    code, _ = _run([])
    assert gate.build_findings().ok is (code == 0), "ok 属性与退出码不一致"
