"""QML 规则闸门（`scripts/check_qml_rules.py`）自己的测试（阶段 2 任务 2.7）。

为什么要专门测闸门：阶段 1 的教训是"测试替身自己也要被测试"。一道**永远只会绿**的
闸门不是闸门，只是一句安慰。这里用五类断言把它钉住：

1. **基线**：当前仓库（`qml/` 还一个文件都没有）跑闸门必须通过 —— 阶段 2 才刚开头，
   闸门不能因为"没有 QML 文件"就报红，但必须**明说**自己的判定范围为空。
2. **能变红**：`poc/qml_rules_fixtures/bad/` 下每个负例都必须报出**对应**的规则号，
   7 条规则每条至少一个负例；而且不许有"预期之外的文件报红"（防 fixture 互相污染）。
3. **不误报**：`poc/qml_rules_fixtures/good/` 下必须零违规；并且正例里真的含有那些
   最容易判错的写法（中文注释、命令式 `Tr.t(`、JS 对象字面量、处理器表达式绑定），
   否则"不误报"是空断言。边界还额外用**变异**证明：把正例里的 `Tr.map[…]` 换成
   `Tr.t(…)`、把中文注释换成中文字面量，必须立刻变红。
4. **例外机制**：登记过的例外打 `[REGISTERED]`、不算失败；登记了却一次都没命中
   必须报"过期"（照 `scripts/relocate_module.py` 的反向守卫）。
5. **降级与 CLI**：`qml/components/COMPONENTS.md` 不存在时 R5 的组件判定要"跳过并说明"；
   `--json` 要是合法 JSON，且 `summary.ok` 与退出码一致。

跑法::

    .venv\\Scripts\\python.exe -m pytest tests/test_qml_rules_gate.py -q
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, FrozenSet, Iterator, List, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_qml_rules.py"
FIXTURES = REPO_ROOT / "poc" / "qml_rules_fixtures"
GOOD = FIXTURES / "good"
BAD = FIXTURES / "bad"

#: 负例文件 → 它**只**应该让哪些规则变红。
#: 精确比对（而不是"包含"）是刻意的：fixture 之间互相污染过就说明判据有交叉，
#: 那种闸门在真项目里会给出误导性的原因。
#:
#: **两处刻意的交叉**（不是判据写漏了，是数据本身同时犯了两条）：
#: `qml/R1_gradient.qml` 的 `GradientStop` 里写死了两个十六进制颜色 —— 渐变（R1）
#: 与硬编码颜色（R8）都该报，改数据来"消灾"反而会掩盖真实的交叉；
#: 反向的边界则收在 R8 里：`qml/R6_transparent.qml` 的 `"#00000000"` 是 **alpha 为 0**
#: 的写法，R8 按判据**故意不判**（全透明归 R6），所以那个 fixture 仍然只报 R6。
EXPECTED_BAD: Dict[str, FrozenSet[str]] = {
    "qml/R1_gradient.qml": frozenset({"R1", "R8"}),
    "qml/R1_acrylic.qml": frozenset({"R1"}),
    "qml/R2_emoji.qml": frozenset({"R2"}),
    "app/bridges/bad_emoji.py": frozenset({"R2"}),
    "qml/R3_chinese.qml": frozenset({"R3"}),
    "qml/R4_binding.qml": frozenset({"R4"}),
    "qml/R4_multiline_binding.qml": frozenset({"R4"}),
    "qml/pages/Home/R5_bad_component.qml": frozenset({"R5"}),
    "qml/overlays/R6_transparent.qml": frozenset({"R6"}),
    "app/bridges/bad_thread.py": frozenset({"R7"}),
    "qml/R8_hardcoded_color.qml": frozenset({"R8"}),
}


def _load_gate():
    """按文件路径加载 `scripts/check_qml_rules.py`（`scripts/` 不是包）。

    必须先登记进 `sys.modules`：`@dataclass` 处理类时会按 `cls.__module__`
    反查 `sys.modules`，未登记会直接 AttributeError。
    """
    cached = sys.modules.get("_fmcl_qml_gate")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("_fmcl_qml_gate", SCRIPT)
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
    """按 CLI 入口跑一次，返回 (退出码, stdout)。"""
    gate = _load_gate()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = gate.main(list(argv))
    return code, buffer.getvalue()


@contextlib.contextmanager
def _patched_registry(mapping: Dict[str, Tuple[str, str]]) -> Iterator[object]:
    """临时替换 `REGISTERED_EXCEPTIONS`（跑完必须还原，避免污染同进程的其它测试）。"""
    gate = _load_gate()
    original = dict(gate.REGISTERED_EXCEPTIONS)
    gate.REGISTERED_EXCEPTIONS.clear()
    gate.REGISTERED_EXCEPTIONS.update(mapping)
    try:
        yield gate
    finally:
        gate.REGISTERED_EXCEPTIONS.clear()
        gate.REGISTERED_EXCEPTIONS.update(original)


def _copy_fixture(name: str, tmp_path: Path) -> Path:
    """把 fixture 复制到临时目录再改，**绝不**动仓库里那份。"""
    destination = tmp_path / name
    shutil.copytree(FIXTURES / name, destination)
    return destination


def _rules_hit(report) -> Dict[str, Set[str]]:
    """把报告折叠成 `文件 → 命中的规则集合`（只看未登记违规）。"""
    hits: Dict[str, Set[str]] = {}
    for result in report.results:
        for violation in result.violations:
            hits.setdefault(violation.path, set()).add(violation.rule)
    return hits


# ─── 1. 基线：当前仓库必须通过 ─────────────────────────────────


def test_repo_passes_with_real_qml_files():
    """现状（`qml/` 下已有真实文件）必须通过。

    **前提已被合法作废**：第一版断言写的是"`qml/` 为空、说明里要讲清判定范围为空"，
    那是任务 2.7 刚落地时的现状。任务 2.2 之后 `qml/App.qml` 与 `qml/FatalError.qml`
    就在仓库里了，于是断言改成"确实扫到了真实 QML 且仍然零违规"——
    这比原来更强：它开始真的检查真实文件，而不是只证明"范围为空时不会误报"。
    闸门的判定能力仍由 `poc/qml_rules_fixtures/` 的负例证明（见本文件第 2 节）。
    """
    gate = _load_gate()
    report = gate.run_gate(REPO_ROOT)
    assert report.ok, [v.render() for v in report.violations]
    assert report.qml_files >= 2, (
        f"qml/ 下应当已有根组件（App.qml / FatalError.qml），实际只扫到 {report.qml_files} 个"
        " —— 如果确实被删了，请同时更新这条断言并在 11 号契约里记录原因"
    )


def test_repo_cli_exit_code_is_zero():
    """任务书里的验收命令就是这一条，直接按 CLI 跑。"""
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(SCRIPT)],
        cwd=str(REPO_ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "QML 规则检查通过" in completed.stdout


def test_repo_r5_whitelist_check_is_not_degraded(tmp_path):
    """`qml/components/COMPONENTS.md` 已在任务 2.16 落地 → R5 **真的在判**，不再跳过。

    **前提已被合法作废**（与同文件的 `test_repo_passes_with_real_qml_files` 同一类）：
    这条原先断言的是"`COMPONENTS.md` 还不存在 → R5 必须跳过并说明"，那是 2.16 之前的现状；
    2.16 的交付物正是让 R5 不再跳过，于是旧断言必红。改法是把"跳过"这条降级路径
    **搬到它仍然成立的地方**（把白名单从副本里拿掉），既不丢覆盖面、也不再过期。
    """
    import shutil

    gate = _load_gate()
    r5 = next(r for r in gate.run_gate(REPO_ROOT).results if r.rule == "R5")
    assert r5.note == "", f"白名单已落地，R5 不该跳过: {r5.note!r}"
    assert r5.files, "R5 一个页面都没扫到"

    # fixture 里白名单是有的 → 那条规则必须真的在跑
    bad_r5 = next(r for r in gate.run_gate(BAD).results if r.rule == "R5")
    assert bad_r5.note == "", f"fixture 里白名单存在，R5 不该跳过: {bad_r5.note!r}"

    # 降级路径仍然成立：把白名单从**副本**里拿掉 → 必须"跳过并说明"
    stripped = tmp_path / "no_whitelist"
    shutil.copytree(BAD, stripped)
    whitelist = stripped / "qml" / "components" / "COMPONENTS.md"
    assert whitelist.is_file(), f"副本里没有白名单，这条测试失去意义: {whitelist}"
    whitelist.unlink()
    note = next(r for r in gate.run_gate(stripped).results if r.rule == "R5").note
    assert "COMPONENTS.md" in note and "跳过" in note, f"白名单缺失时的降级说明丢了: {note!r}"


# ─── 2. 负例：7 条规则每条都能变红 ─────────────────────────────


def test_every_bad_fixture_turns_exactly_its_own_rule_red():
    """每个负例都要报出预期的那条规则，且不许带上别的规则（判据不能互相污染）。"""
    gate = _load_gate()
    report = gate.run_gate(BAD)
    assert not report.ok, "负例 base 居然全绿 —— 闸门是空断言"
    hits = _rules_hit(report)
    problems = []
    for rel, expected in EXPECTED_BAD.items():
        actual = hits.get(rel)
        if actual is None:
            problems.append(f"{rel} 一处违规都没报（预期 {sorted(expected)}）")
        elif actual != set(expected):
            problems.append(f"{rel} 命中的规则是 {sorted(actual)}，预期 {sorted(expected)}")
    unexpected = sorted(set(hits) - set(EXPECTED_BAD))
    if unexpected:
        problems.append(f"预期外的文件报红了（fixture 之间互相污染？）：{unexpected}")
    assert problems == [], "\n".join(problems)


def test_all_eight_rules_have_a_negative_fixture():
    """8 条规则每条至少 1 个负例 —— "能变红"的证明不能有缺口。"""
    covered: Set[str] = set()
    for rules in EXPECTED_BAD.values():
        covered |= set(rules)
    assert covered == {"R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"}
    gate = _load_gate()
    report = gate.run_gate(BAD)
    fired = {v.rule for v in report.violations}
    assert fired == covered, f"负例预期 {sorted(covered)}，实际只红了 {sorted(fired)}"


def test_r2_catches_escaped_emoji_in_python_sources():
    """Python 解析时就把 `\\uXXXX` 解成了真字符 —— 必须看**源码片段**才发现转义写法。

    这是实测抓到的一个漏判：只扫 `node.value` 的话，`"\\uD83D\\uDE00"` 与直接写
    emoji 等价这件事就没人管了。
    """
    report = _load_gate().run_gate(BAD, only=["R2"])
    in_bridge = [v for v in report.violations if v.path == "app/bridges/bad_emoji.py"]
    assert len(in_bridge) == 3, [v.render() for v in in_bridge]
    assert any("转义" in v.detail for v in in_bridge), "转义写法的 emoji 漏判了"


def test_r2_covers_the_symbols_this_project_uses_as_icons():
    """R2 必须覆盖"本项目当图标用的文本符号"与**补齐的 SMP 区块**。

    ## 为什么单独钉这一条

    任务 2.10 的 emoji 清单实测发现 R2 有三处盲区，其中两处**正好是任务书点名的图标**：

    * `Emoji_Presentation=No` 的文本符号 —— `⏹`（旧界面的"强杀游戏"）与 `⚙`（"资源管理"
      入口）**都不在**任何 emoji 区间里，闸门完全看不见；
    * SMP 漏了 4 个块，其中 `1F7E0–1F7EB` 是**真 emoji**（🟡🟢 就落在里面）。

    这条测试直接对判据函数下手（不依赖 fixture），把"看不见"变成"看得见"：
    修好之前它会红，修好之后它保护这个修复不被回退。
    """
    gate = _load_gate()
    must_flag = {
        0x23F9: "⏹ 强杀",
        0x2699: "⚙ 资源管理",
        0x1F7E1: "🟡 彩色圆点（SMP 补块）",
        0x1F7E2: "🟢 彩色圆点（SMP 补块）",
        0x1F650: "SMP 装饰符号块起点",
        0x1FA00: "SMP 象棋符号块起点",
    }
    missed = [label for cp, label in must_flag.items() if not gate._emoji_desc(cp)]
    assert missed == [], f"这些码点仍然看不见: {missed}"

    # 反向：真·文字符号不许被误报（否则闸门会被绕过）
    must_pass = {0x2713: "✓ 勾", 0x2605: "★ 星", 0x2665: "♥ 心", 0x2192: "→ 箭头"}
    noise = [label for cp, label in must_pass.items() if gate._emoji_desc(cp)]
    assert noise == [], f"这些是纯文字符号，不该报: {noise}"


def test_r7_negative_covers_the_three_required_shapes():
    """R7 要求"至少覆盖"的三种写法都要真的被负例覆盖到（否则是空断言）。"""
    gate = _load_gate()
    report = gate.run_gate(BAD, only=["R7"])
    details = " ".join(v.detail for v in report.violations)
    assert "QMetaObject.invokeMethod" in details, "缺 invokeMethod 负例"
    assert "rootObjects" in details, "缺 rootObjects 负例"
    assert "直接写 QML 可见属性 self.count" in details, "缺 worker 直接写属性的负例"
    assert len(report.violations) == 4, [v.render() for v in report.violations]


# ─── 2b. R8（任务 2.8 新增）：颜色字面量 ───────────────────────


def test_r8_catches_the_four_color_literal_forms():
    """R8 的四种写法都要真的报出来：`#rrggbb` / `#rgb` / `#aarrggbb` / `Qt.rgba(...)`。"""
    gate = _load_gate()
    report = gate.run_gate(BAD, only=["R8"])
    mine = [v for v in report.violations if v.path == "qml/R8_hardcoded_color.qml"]
    details = " ".join(v.detail for v in mine)
    assert "#1a1a2e" in details, "缺 6 位写法"
    assert "#fff" in details, "缺 3 位写法"
    assert "#aabbccdd" in details, "缺 8 位（带 alpha）写法"
    assert "Qt.rgba(" in details, "缺 Qt.rgba 写法"
    assert len(mine) == 4, [v.render() for v in mine]
    assert [v.line for v in mine] == sorted(v.line for v in mine)


def test_r8_allows_theme_bindings_transparent_and_comments():
    """放行边界：`Theme.*`、alpha 为 0、以及注释里的颜色值，一个都不许报。"""
    gate = _load_gate()
    fixture = (BAD / "qml" / "R8_hardcoded_color.qml").read_text(encoding="utf-8")
    assert "Theme.textPrimary" in fixture, "放行清单里缺少 Theme.* 的用例"
    assert '"#00000000"' in fixture, "放行清单里缺少 alpha 为 0 的用例"
    assert "#1a1a2e" in fixture.split("import QtQuick")[0], "放行清单里缺少「注释里的颜色值」用例"

    report = gate.run_gate(BAD, only=["R8"])
    reported = [v.render() for v in report.violations if v.path == "qml/R8_hardcoded_color.qml"]
    assert len(reported) == 4, f"放行的写法被误报了: {reported}"
    # 正例整体也必须干净（正例里 FmCard.qml 就是用 Theme.cardBg 写的）
    assert gate.run_gate(GOOD, only=["R8"]).violations == []


def test_r8_ignores_svg_assets(tmp_path):
    """`.svg` **不在** R8 的扫描范围：图标资源天生带 `fill="#ffffff"`。"""
    base = _copy_fixture("good", tmp_path)
    assets = base / "qml" / "assets" / "icons"
    assets.mkdir(parents=True)
    (assets / "play.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg">\n'
        '  <rect width="24" height="24" fill="#1a1a2e" stroke="#ffffff"/>\n'
        "</svg>\n",
        encoding="utf-8",
    )
    gate = _load_gate()
    report = gate.run_gate(base, only=["R8"])
    assert report.violations == [], [v.render() for v in report.violations]
    # 反证：同一份颜色写在 .qml 里必须报（否则上面那句"干净"毫无意义）
    (base / "qml" / "SvgLike.qml").write_text(
        'import QtQuick\nRectangle {\n    color: "#1a1a2e"\n}\n', encoding="utf-8"
    )
    after = gate.run_gate(base, only=["R8"])
    assert [v.path for v in after.violations] == ["qml/SvgLike.qml"], [v.render() for v in after.violations]


def test_r8_boundary_pinned_by_mutation(tmp_path):
    """把正例里的一处 `Theme.*` 换成颜色字面量 → 必须立刻变红。"""
    base = _copy_fixture("good", tmp_path)
    gate = _load_gate()
    assert gate.run_gate(base, only=["R8"]).violations == []

    card = base / "qml" / "components" / "FmCard.qml"
    text = card.read_text(encoding="utf-8")
    mutated = text.replace("color: Theme.cardBg", 'color: "#1e2a4a"')
    assert mutated != text, "变异锚点没命中，测试本身失效了"
    card.write_text(mutated, encoding="utf-8")

    report = gate.run_gate(base, only=["R8"])
    assert [v.path for v in report.violations] == ["qml/components/FmCard.qml"], [v.render() for v in report.violations]
    assert "R-15" in report.violations[0].detail, "违规原因里要写清依据（D-102 / R-15）"


def test_r8_registered_exception_marks_and_expires():
    """R8 也吃同一套例外机制：命中打 `[REGISTERED]`、没命中报过期。"""
    gate = _load_gate()
    report = gate.run_gate(BAD, only=["R8"])
    keys = sorted(v.key for v in report.violations if v.path == "qml/R8_hardcoded_color.qml")
    assert len(keys) == 4, keys

    with _patched_registry({key: ("夹具：负例数据，不修", "2.8") for key in keys}):
        partial = gate.run_gate(BAD, only=["R8"])
        assert partial.registered_count == 4
        assert all(k not in keys for k in (v.key for v in partial.violations)), "登记过的还在报"
        assert not partial.ok, "R1_gradient 的两处仍未登记，整体仍应失败"
        code, out = _run(["--base", str(BAD), "--rule", "R8"])
        assert code == 1 and "[REGISTERED]" in out

    with _patched_registry({"qml/R8_hardcoded_color.qml:99999:R8": ("故意指向不存在的行号", "2.8")}):
        expired = gate.run_gate(BAD, only=["R8"])
        assert [key for key, _, _ in expired.expired] == ["qml/R8_hardcoded_color.qml:99999:R8"]


# ─── 3. 正例：不许误报（并证明正例不是空文件） ─────────────────


def test_good_fixtures_pass_every_rule():
    """误报会让闸门被绕过，所以正例必须零违规。"""
    gate = _load_gate()
    report = gate.run_gate(GOOD)
    assert report.violations == [], [v.render() for v in report.violations]
    assert report.expired == []
    assert report.ok
    # 防"空扫描"：正例里必须真的有 QML 与桥，否则上面那句通过毫无意义
    assert report.qml_files >= 6 and report.bridge_files >= 2


def test_good_fixture_contains_the_two_critical_boundaries():
    """任务书点名的两条边界必须真的写在正例里：中文注释、命令式 Tr.t(。"""
    app = (GOOD / "qml" / "App.qml").read_text(encoding="utf-8")
    assert "// 显式关闭亚克力/Mica 材质" in app, "缺少中文注释（R3 的放行边界）"
    assert 'console.log(Tr.t("help_clicked"))' in app, "缺少处理器表达式里的命令式 Tr.t("
    assert 'text: Tr.map["start_game"]' in app, "缺少 Tr.map 的正确绑定写法"
    home = (GOOD / "qml" / "pages" / "Home" / "HomePage.qml").read_text(encoding="utf-8")
    assert "Tr.t(" in home, "缺少命令式 Tr.t( 用例"
    assert 'label: Tr.t("menu_label"),' in home, "缺少多行 JS 对象字面量（return { … label: … }）用例"
    helpers = (GOOD / "qml" / "pages" / "Home" / "page_helpers.js").read_text(encoding="utf-8")
    assert "Tr.t(" in helpers, "缺少 .js 里的命令式 Tr.t( 用例（.js 不判 R4）"
    bridge = (GOOD / "app" / "bridges" / "tr_bridge.py").read_text(encoding="utf-8")
    assert "Thread(target=self._reload" in bridge and "self.languageChanged.emit()" in bridge
    assert "QMetaObject.invokeMethod" not in bridge


def test_good_fixture_pins_the_scanner_boundaries():
    """R3 的难点全在"分清字面量与注释"：注释里的引号、注释里的中文、字符串里的 //。

    这三条任一判错，R3 要么满屏误报（注释里的中文被算成硬编码），要么把整段
    代码吞成字符串（真违规全漏）。所以正例里必须显式钉住它们。
    """
    text = (GOOD / "qml" / "shell" / "TitleBar.qml").read_text(encoding="utf-8")
    assert '注释里可以写引号与中文："设置"' in text, "缺少「注释里有引号与中文」的用例"
    assert "注释里出现不配对的引号" in text and '" 也不能' in text, "缺少未配对引号的用例"
    assert '"qrc:///icons/play.svg"' in text, "缺少「字符串里有 //」的用例"
    gate = _load_gate()
    assert gate.run_gate(GOOD, only=["R3"]).violations == [], "注释里的引号/中文字把 R3 判红了"


def test_r3_boundary_pinned_by_mutation(tmp_path):
    """中文注释放行、中文字面量报红 —— 两边都证明，否则边界只是嘴上说说。"""
    base = _copy_fixture("good", tmp_path)
    gate = _load_gate()
    assert gate.run_gate(base).ok, "复制出来的正例也必须先通过"

    app = base / "qml" / "App.qml"
    text = app.read_text(encoding="utf-8")
    mutated = text.replace('effect: "normal"', 'effect: "normal"\n    objectName: "硬编码中文"')
    assert mutated != text, "变异锚点没命中，测试本身失效了"
    app.write_text(mutated, encoding="utf-8")

    report = gate.run_gate(base, only=["R3"])
    assert [v.rule for v in report.violations] == ["R3"], "注释里的中文不许连坐，字面量必须报"
    assert report.violations[0].path == "qml/App.qml"


def test_r4_boundary_pinned_by_mutation(tmp_path):
    """命令式 Tr.t( 放行、绑定里的 Tr.t( 报红 —— 把正例里的一处 Tr.map 换成 Tr.t 就变红。"""
    base = _copy_fixture("good", tmp_path)
    gate = _load_gate()
    imperative = sum(p.read_text(encoding="utf-8").count("Tr.t(") for p in base.rglob("*.qml"))
    assert imperative >= 3, f"正例里的命令式 Tr.t( 太少（{imperative} 处），边界钉不住"
    assert gate.run_gate(base).ok

    app = base / "qml" / "App.qml"
    text = app.read_text(encoding="utf-8")
    mutated = text.replace('title: Tr.map["app_title"] ?? "app_title"', 'title: Tr.t("app_title")')
    assert mutated != text, "变异锚点没命中，测试本身失效了"
    app.write_text(mutated, encoding="utf-8")

    report = gate.run_gate(base, only=["R4"])
    assert [v.rule for v in report.violations] == ["R4"], "只该报这一处绑定"
    assert "宿主键 title" in report.violations[0].detail


# ─── 4. 已登记例外：命中打标记、没命中报过期 ───────────────────


def test_registered_exception_is_marked_and_all_registered_passes():
    gate = _load_gate()
    baseline = gate.run_gate(BAD, only=["R3"])
    keys = [v.key for v in baseline.violations]
    assert len(keys) >= 2, "R3 负例太少，登记机制的测试会退化成空断言"

    with _patched_registry({keys[0]: ("夹具：这条只是负例数据，不修", "2.7")}):
        partial = gate.run_gate(BAD, only=["R3"])
        assert len(partial.violations) == len(keys) - 1, "登记的那条不该再算失败"
        assert partial.registered_count == 1
        assert not partial.ok, "还有未登记的违规，整体仍应失败"
        code, out = _run(["--base", str(BAD), "--rule", "R3"])
        assert code == 1
        assert "[REGISTERED]" in out

        with _patched_registry({key: ("夹具：整批都是负例数据", "2.7") for key in keys}):
            full = gate.run_gate(BAD, only=["R3"])
            assert full.ok and full.expired == [], [v.render() for v in full.violations]
            assert full.registered_count == len(keys)
            code, out = _run(["--base", str(BAD), "--rule", "R3"])
            assert code == 0
            assert out.count("[REGISTERED]") == len(keys)
            assert "[FAIL]" not in out


def test_unmatched_registration_is_reported_as_expired():
    """登记了却一次都没命中 → 必须报错，否则这张表会腐化成永久豁免名单。"""
    stale = "qml/R3_chinese.qml:99999:R3"
    with _patched_registry({stale: ("故意指向不存在的行号", "2.7")}):
        gate = _load_gate()
        report = gate.run_gate(BAD, only=["R3"])
        assert [key for key, _, _ in report.expired] == [stale]
        assert not report.ok
        code, out = _run(["--base", str(BAD), "--rule", "R3"])
        assert code == 1
        assert "登记过期" in out and stale in out


def test_malformed_registration_key_is_reported():
    with _patched_registry({"qml/R3_chinese.qml:7": ("少了规则号，永远不可能命中", "2.7")}):
        report = _load_gate().run_gate(BAD, only=["R3"])
        assert len(report.expired) == 1
        assert "形状非法" in report.expired[0][1]


def test_registration_of_another_rule_is_not_expired_when_running_one_rule():
    """`--rule R3` 单跑时，别的规则的登记项不该被判过期（否则单跑一条就没法用）。

    判据里那条"只对本次跑过的规则做过期检查"就是这么设计的；这里用一条
    **指向 R1 的、命中不了的**登记来钉死它。
    """
    gate = _load_gate()
    stale_r1 = "qml/R1_gradient.qml:9999:R1"
    with _patched_registry({stale_r1: ("其它规则的过期登记", "2.7")}):
        assert gate.run_gate(BAD, only=["R3"]).expired == [], "没跑 R1，不许判它过期"
        assert [k for k, _, _ in gate.run_gate(BAD, only=["R1"]).expired] == [stale_r1]
        assert [k for k, _, _ in gate.run_gate(BAD).expired] == [stale_r1]


def test_real_registry_entries_are_wellformed():
    """真表里的每条登记：键形状合法、理由与任务号都不能空。"""
    gate = _load_gate()
    for key, (reason, task) in gate.REGISTERED_EXCEPTIONS.items():
        assert gate._REGISTRY_KEY_RE.match(key), f"登记键形状非法: {key}"
        assert reason.strip(), f"登记项缺少理由: {key}"
        assert task.strip(), f"登记项缺少归属任务: {key}"


# ─── 5. --json 与解析器细节 ───────────────────────────────────


def test_json_output_agrees_with_exit_code():
    for base, expected_ok in ((GOOD, True), (BAD, False)):
        code, out = _run(["--base", str(base), "--json"])
        payload = json.loads(out)
        assert [r["id"] for r in payload["rules"]] == ["R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8"]
        assert payload["summary"]["ok"] is expected_ok
        assert (code == 0) is expected_ok
        for result in payload["rules"]:
            for item in result["violations"]:
                assert set(item) == {"rule", "file", "line", "column", "detail", "key"}
        if not expected_ok:
            assert payload["summary"]["violations"] > 0
            assert payload["expired_registrations"] == []


def test_qt_visible_names_handles_the_three_declaration_forms():
    gate = _load_gate()
    tree = ast.parse(
        "class A(QObject):\n"
        '    Q_PROPERTY("int count READ count NOTIFY countChanged")\n'
        "    items = Property(list)\n"
        "    sig = Signal()\n"
        "    @Property(str, notify=sig)\n"
        "    def title(self): ...\n"
    )
    assert gate.qt_visible_names(tree) == {"count", "items", "sig", "title"}


def test_worker_function_names_resolves_thread_submit_and_partial():
    gate = _load_gate()
    tree = ast.parse(
        "t = threading.Thread(target=self._a)\n"
        "p = Thread(target=_b, args=(1,))\n"
        "pool.submit(self._c)\n"
        "pool.submit(partial(self._d, 1))\n"
        "pool.submit(lambda: None)\n"
    )
    # 解析不到名字的（lambda）必须被丢掉 —— R7 宁可漏判也不能误判
    assert gate.worker_function_names(tree) == {"_a", "_b", "_c", "_d"}
