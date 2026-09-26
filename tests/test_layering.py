"""分层与搬家的回归测试（阶段 1 验收标准的可执行版本）。

这些测试把"阶段性成果"变成"以后改坏了会立刻报错"的守卫：

1. 核心层 import 不再拖入界面层（阶段 0 实测 1661 个模块 → 现在应为 0 个 ctk）；
2. ``services/`` 层零 UI 依赖（对应阶段 1 验收标准第 1 条）；
3. 转发 shim 必须是**同一批对象**（否则 ``from ui.i18n import _`` 的 31 处
   绑定语义会悄悄失效）；
4. 主题引擎搬家后行为不变（预设数量、版本调色表逐值比对）；
5. 回调键没有"软缺失"（1.19 修掉的两个死键不得回退）。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"


def _load_script(name: str):
    """按文件路径加载 ``scripts/*.py``（该目录不是包，没有 __init__.py）。

    必须先把模块登记进 ``sys.modules``：``@dataclass`` 在处理类时会
    按 ``cls.__module__`` 反查 ``sys.modules``，未登记会直接
    ``AttributeError: 'NoneType' object has no attribute '__dict__'``。
    """
    cached = sys.modules.get(f"_fmcl_script_{name}")
    if cached is not None:
        return cached
    path = SCRIPTS_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_fmcl_script_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _run_probe(code: str) -> str:
    """在干净子进程里跑一段探针代码，返回 stdout。

    必须用子进程：``sys.modules`` 是进程级状态，同进程内前面测试导入过的
    模块会污染判断。
    """
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        timeout=180,
    )
    assert result.returncode == 0, f"探针失败:\nstdout={result.stdout}\nstderr={result.stderr}"
    return result.stdout


# ─── 1. 核心层 import 纯净度 ────────────────────────────────


TK_MODULES = ("customtkinter", "tkinter", "PIL", "pygame")


def test_launcher_core_import_does_not_pull_ui_toolkit():
    """`import launcher.core` 不得拖入任何 GUI 栈。

    改造前实测：新增加载 1661 个模块，含 customtkinter / tkinter / PIL / ui.app。
    根因是 `launcher/core.py` 为了一个 HTTP UA 字符串 import 了 `ui.constants`，
    而 `ui/__init__.py` 当时 eager import `ui.app`。
    """
    out = _run_probe(
        "import sys; import launcher.core; "
        "print('|'.join(m for m in %r if m in sys.modules))" % (TK_MODULES,)
    ).strip()
    assert out == "", f"核心层仍在拖入界面栈: {out}"


def test_import_ui_constants_is_light():
    """`import ui.constants` 是纯标准库模块，不该拉起界面。"""
    out = _run_probe(
        "import sys; import ui.constants; "
        "print('|'.join(m for m in %r if m in sys.modules))" % (TK_MODULES,)
    ).strip()
    assert out == "", f"ui.constants 拖入了界面栈: {out}"


def test_import_ui_package_is_lazy_but_exports_work():
    """惰性 `ui/__init__.py` 仍须支持全部 12 个历史导出名。"""
    out = _run_probe(
        "import sys, ui; "
        "names = ['COLORS','FONT_FAMILY','RESOURCE_TYPES','show_confirmation','show_alert',"
        "'VersionSelectorDialog','ModernApp','ResourceManagerWindow','LauncherSettingsWindow',"
        "'ModpackInstallWindow','ModpackServerWindow','ModBrowserWindow']; "
        "before = 'customtkinter' in sys.modules; "
        "missing_light = [n for n in names[:3] if not hasattr(ui, n)]; "
        "print(f'ctk_before={before} missing_light={missing_light} all={sorted(ui.__all__)==sorted(names)}')"
    ).strip()
    assert out == "ctk_before=False missing_light=[] all=True", out


# ─── 2. services 层零 UI 依赖 ───────────────────────────────


def test_services_layer_has_zero_ui_violations():
    """阶段 1 验收标准第 1 条：services/ 下不得出现任何 UI 依赖。"""
    purity = _load_script("check_services_purity")
    rule = next(r for r in purity.RULES if r.group == "services")
    files = list(purity.iter_python_files(rule.roots))
    assert files, "services/ 下没扫到文件，规则失效了？"

    offenders = []
    for path in files:
        report = purity.check_file(path, rule)
        offenders.extend(v.render() for v in report.violations)
    assert offenders == [], "services/ 出现 UI 依赖:\n" + "\n".join(offenders)


def test_purity_known_exceptions_are_all_real():
    """已登记例外必须真的还存在——否则是忘了清理登记。"""
    purity = _load_script("check_services_purity")
    seen = set()
    for rule in purity.RULES:
        for path in purity.iter_python_files(rule.roots):
            for v in purity.check_file(path, rule).violations:
                seen.add(v.key())
    stale = [f"{k.path} -> {k.module}" for k in purity.KNOWN_VIOLATIONS if (k.path, k.module) not in seen]
    assert stale == [], f"这些已登记例外已经不存在了，请从 KNOWN_VIOLATIONS 移除: {stale}"


# ─── 3. 转发 shim 的对象同一性 ──────────────────────────────


def test_ui_shims_reexport_identical_objects():
    """shim 必须是同一批对象，不能包装。

    `from ui.i18n import _` 在 import 时绑定函数对象；如果 shim 用
    ``def _(...): return service._(...)`` 包装，状态可见性与绑定语义都会变。
    """
    import services.i18n_service as si
    import services.palette as sp
    import services.theme_service as st
    import services.user_agent as su
    import ui.constants as c
    import ui.i18n as i
    import ui.theme_engine as te

    assert c.COLORS is sp.COLORS
    assert c.current_colors is sp.COLORS
    assert c.USER_AGENT is su.USER_AGENT
    assert c.LazyStr is su.LazyStr

    for name in ("Theme", "ThemeEngine", "init_theme_engine", "get_theme_engine"):
        assert getattr(te, name) is getattr(st, name), f"ui.theme_engine.{name} 不是同一对象"

    for name in (
        "AVAILABLE_LANGUAGES",
        "DEFAULT_LANGUAGE",
        "init_i18n",
        "get_current_language",
        "set_language",
        "get_available_languages",
        "_",
        "tr",
        "_translate",
    ):
        assert getattr(i, name) is getattr(si, name), f"ui.i18n.{name} 不是同一对象"


def test_ui_constants_keeps_names_ui_modules_import_directly():
    """`ui/` 内多个模块直接 import 这些名字，少一个就是 ImportError。"""
    import ui.constants as c

    for name in (
        "COLORS",
        "FONT_FAMILY",
        "RESOURCE_TYPES",
        "USER_AGENT",
        "current_colors",
        "LazyStr",
        "_get_fmcl_version",
        "_get_user_agent",
    ):
        assert hasattr(c, name), f"ui.constants 丢了 {name}"


def test_i18n_language_state_visible_through_shim():
    """通过 shim 切换语言，直接引用服务层函数的调用方也必须看到新语言。"""
    import services.i18n_service as i18n
    from ui.i18n import _, get_current_language, set_language

    original = get_current_language()
    # 「语言包**是否已加载**」也是一个全局状态：切过一次语言之后，`_translations`
    # 就从空字典变成整份译文，于是 `_()` 从"返回键名"变成"返回译文"。
    # 只 restore 语言代码会把这半个副作用泄漏给同进程后面的测试 ——
    # **实测踩到**：`tests/test_online_stale_cleanup.py` 里两条用例
    # "单独跑绿、全量跑红"（第 11 轮收尾时留下的 2 个红灯），根因就在这里。
    # 所以这里把语言代码与译文表**一起**还原。
    had_translations = dict(i18n._translations)
    try:
        assert set_language("en_US") is True
        assert get_current_language() == "en_US"
        assert _("not_a_real_key_zzz") == "not_a_real_key_zzz", "缺键必须返回键名本身"
        assert set_language("zz_ZZ") is False, "非法语言必须返回 False"
        assert get_current_language() == "en_US", "非法语言不得改变当前语言"
    finally:
        set_language(original)
        i18n._translations = had_translations


# ─── 4. 主题引擎搬家后行为不变 ──────────────────────────────


def test_theme_engine_presets_and_version_map_unchanged():
    """搬家属于"只搬不改"：预设数量与版本调色表必须逐值保持。"""
    from services.theme_service import ThemeEngine

    engine = ThemeEngine(".")
    names = [t.name for t in engine._preset_themes]
    assert names == ["default", "ocean", "forest", "lavender", "sunset"], names

    expected = {
        "1.21": "#6a0dad",
        "1.20": "#c9a84c",
        "1.19": "#2d7d46",
        "1.18": "#4a8fbf",
        "1.16": "#8b4513",
        "1.8": "#9b59b6",
        "1.7": "#e74c3c",
    }
    assert len(engine.VERSION_COLOR_MAP) == 15
    for version, accent in expected.items():
        assert engine.VERSION_COLOR_MAP[version]["accent"] == accent, version


def test_theme_engine_mutates_the_single_shared_colors_dict():
    """COLORS 必须是唯一对象并被原地修改，否则已构建的 Tk 控件看不到新主题。"""
    from services.palette import COLORS
    from services.theme_service import ThemeEngine

    engine = ThemeEngine(".")
    before_id = id(COLORS)
    original_accent = COLORS["accent"]
    try:
        engine.apply_theme(engine.load_theme("ocean"))
        assert COLORS["accent"] == "#00b4d8"
        assert id(COLORS) == before_id, "COLORS 被整体替换了，引用会失联"
    finally:
        engine.apply_theme(engine.load_theme("default"))
    assert COLORS["accent"] == original_accent


# ─── 5. 回调键不得回退（阶段 1.19） ─────────────────────────


def test_no_missing_callback_keys():
    """所有被读取的回调键都必须有人提供（含运行时注入的键）。

    这里刻意用 `--fail-on-soft` 的等价判定：1.19 修掉的 `install_game` /
    `get_ai_token` 都属于"软缺失 + 功能静默失效"，只查硬缺失会漏掉。
    """
    keys = _load_script("check_callback_keys")

    provided = keys.provided_from_get_callbacks() | keys.provided_from_injections()
    assert len(provided) > 50, f"只解析到 {len(provided)} 个提供方键，解析逻辑可能失效"

    report = keys.Report()
    for path in keys.iter_py(keys.CONSUMER_ROOTS):
        report.extend(keys.scan_file(path))

    missing_hard = sorted({a.key for a in report.hard} - provided)
    missing_soft = sorted({a.key for a in report.soft} - provided)
    assert missing_hard == [], f"硬读取了无人提供的键（会 KeyError）: {missing_hard}"
    assert missing_soft == [], f"软读取了无人提供的键（功能会静默失效）: {missing_soft}"


def test_install_game_and_get_ai_token_are_gone():
    """两个历史死键不得复活。"""
    keys = _load_script("check_callback_keys")
    used = set()
    for path in keys.iter_py(keys.CONSUMER_ROOTS):
        report = keys.scan_file(path)
        used |= {a.key for a in report.hard} | {a.key for a in report.soft}
    assert "install_game" not in used, "install_game 死键复活了（正确键是 install_version）"
    assert "get_ai_token" not in used, "get_ai_token 死键复活了（正确键是 get_jdz_token）"


# ─── 6. i18n 完整性（阶段 1.20 的守卫） ─────────────────────


def test_locale_files_have_identical_key_sets():
    """4 个语言文件的键集合必须一致，否则该语言下会显示键名。"""
    tables = {}
    for path in sorted((REPO_ROOT / "ui" / "locales").glob("*.json")):
        tables[path.stem] = set(json.loads(path.read_text(encoding="utf-8")))
    assert len(tables) == 4, f"语言文件数量异常: {sorted(tables)}"

    reference = tables["zh_CN"]
    problems = {lang: sorted(reference - keys) for lang, keys in tables.items() if keys != reference}
    assert problems == {}, f"语言键集合不一致: {problems}"


def test_no_placeholder_mismatch_across_languages():
    """占位符跨语言必须一致，否则某些语言下参数会被静默丢弃。"""
    i18n_check = _load_script("check_i18n")
    locales = i18n_check.load_locales()
    mismatches = i18n_check.check_placeholders(locales)
    assert mismatches == [], f"占位符不一致: {[k for k, _ in mismatches]}"


def test_no_code_referenced_key_is_absent_from_locales():
    """代码里 `_("key")` 引用的键必须存在于语言文件。

    历史：阶段 1.20 之前这里挂着 ``pytest.xfail``，因为当时确实有 16 个键
    缺失（界面会显示成键名本身）。16 个键补齐后转为普通断言 —— 保留 xfail
    会让这条守卫永远不生效。
    """
    i18n_check = _load_script("check_i18n")
    findings = i18n_check.build_findings()
    missing = sorted(findings.used_but_absent)
    assert missing == [], f"有 {len(missing)} 个键被代码引用但语言文件里没有: {missing}"
