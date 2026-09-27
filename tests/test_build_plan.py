"""打包计划闸门（阶段 3 前置：QML 可打包）。

## 为什么需要它

QML 侧的桥与服务表全是 `importlib.import_module("模块名")` 的**动态**导入
（`main_qml.SINGLETON_BRIDGES`、`main_qml.CONTEXT_BRIDGES`、`app.bootstrap.SERVICE_FACTORIES`），
PyInstaller 的静态分析看不见它们。漏收一条的后果是"打包产物一启动就崩"，而源码态与全部单元
测试都照常绿 —— 这是最难在开发机上发现的一类回归，所以要用闸门钉住。

另一件被钉住的事：**默认后端必须还是 `tk`**。阶段 3 期间 QML 的 12 个页面还是占位壳，
把默认切成 qml 等于把空页面发给用户（阶段 4.1 才切）。

## 覆盖

1. 后端表与默认值；
2. 动态导入覆盖（对着三张真源表逐条核对）；
3. tk 后端与迁移前的 `build.spec` 逐条一致（entry / onefile / excludes / datas 目标 / 顶层依赖）；
4. qml 后端的形态（entry、onedir、不再排 PySide6、QML 数据文件的目标名）；
5. `build.spec` 真的把计划用上了（用假 PyInstaller 符号 exec 一遍 spec，检查 Analysis 参数、
   裁剪图案是否生效、EXE/COLLECT 与 onefile 是否配对）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SPEC_PATH = REPO_ROOT / "build.spec"

from scripts.build_plan import (  # noqa: E402
    BACKENDS,
    DEFAULT_UI_BACKEND,
    QML_DYNAMIC_IMPORTS,
    UI_BACKENDS,
    missing_qml_requirements,
    normalize_backend,
    plan,
    should_drop_collected_binary,
    should_drop_qml_module_dir,
)

# ─── 1. 后端表与默认值 ─────────────────────────────────────────


def test_backend_table_is_tk_and_qml() -> None:
    assert UI_BACKENDS == ("tk", "qml")
    assert set(BACKENDS) == set(UI_BACKENDS)


def test_default_backend_stays_tk_until_phase_4() -> None:
    """默认后端必须是 tk（阶段 4.1 才改）。

    这条红了只有两种可能：① 有人提前把默认切到 qml（QML 页面还是占位壳，等于发空页面）；
    ② 阶段 4.1 真的开做了 —— 那就连这条注释一起改，别只改断言。
    """
    assert DEFAULT_UI_BACKEND == "tk"
    assert normalize_backend(None) == "tk"
    assert normalize_backend("不认识的") == "tk"
    assert normalize_backend(3) == "tk"  # type: ignore[arg-type]


def test_normalize_backend_accepts_case_and_padding() -> None:
    assert normalize_backend("qml") == "qml"
    assert normalize_backend(" QML ") == "qml"
    assert normalize_backend("Tk") == "tk"


# ─── 2. 动态导入覆盖 ───────────────────────────────────────────


def test_dynamic_imports_cover_every_bridge() -> None:
    """`main_qml` 的两张桥表必须全部登记在 hiddenimports 里。"""
    import main_qml

    declared = {module for _, module, _ in main_qml.SINGLETON_BRIDGES + main_qml.CONTEXT_BRIDGES}
    assert declared, "桥表读出来是空的，说明表被改了名字/结构"
    missing = sorted(declared - set(plan("qml")["hidden_imports"]))
    assert not missing, f"这些桥没被收进 hiddenimports，打包后会启动即崩：{missing}"


def test_dynamic_imports_cover_every_service() -> None:
    """`app.bootstrap.SERVICE_FACTORIES` 的服务必须全部登记在 hiddenimports 里。"""
    from app import bootstrap

    declared = {spec.split(":", 1)[0] for _, spec in bootstrap.SERVICE_FACTORIES}
    assert declared, "服务表读出来是空的，说明表被改了名字/结构"
    missing = sorted(declared - set(plan("qml")["hidden_imports"]))
    assert not missing, f"这些服务没被收进 hiddenimports，打包后会启动即崩：{missing}"


def test_dynamic_import_list_has_no_phantom_entries() -> None:
    """反向检查：登记了但真源表里没有的条目也要清掉（否则清单会越攒越假）。"""
    import main_qml
    from app import bootstrap

    real = {module for _, module, _ in main_qml.SINGLETON_BRIDGES + main_qml.CONTEXT_BRIDGES}
    real |= {spec.split(":", 1)[0] for _, spec in bootstrap.SERVICE_FACTORIES}
    phantom = sorted(set(QML_DYNAMIC_IMPORTS) - real)
    assert not phantom, f"动态导入清单里有真源表里不存在的条目：{phantom}"


def test_qml_backend_keeps_qt_and_tk_backend_does_not() -> None:
    assert "PySide6" in plan("tk")["excludes"]
    assert "PySide6" not in plan("qml")["excludes"]


# ─── 3. tk 后端与迁移前一致 ────────────────────────────────────


def test_tk_backend_is_the_single_file_entry() -> None:
    tk = plan("tk")
    assert tk["entry"] == "main.py"
    assert tk["onefile"] is True
    assert tk["product_name"] == "FMCL"
    assert tk["with_qt"] is False
    assert tk["filter_collected"] is False


def test_tk_backend_top_level_dependencies_are_frozen() -> None:
    """迁移前 `build.spec` 的顶层依赖包（漏一个就是少一个运行期依赖）。"""
    frozen = {
        "minecraft_launcher_lib",
        "forgepy",
        "requests",
        "logzero",
        "tqdm",
        "keyboard",
        "tkinter",
        "PIL",
        "customtkinter",
        "orjson",
        "urllib3",
        "rarfile",
        "markdown",
        "tkinterweb",
        "_build_secrets",
        # 语音输入栈
        "sounddevice",
        "_sounddevice_data",
        "numpy",
        "sentencepiece",
        "onnxruntime",
        "services.voice_service",
    }
    declared = set(plan("tk")["hidden_imports"])
    missing = sorted(frozen - declared)
    assert not missing, f"tk 后端的 hiddenimports 少了迁移前就有的包：{missing}"


def test_tk_backend_data_targets_are_frozen() -> None:
    targets = sorted(dest for _, dest in plan("tk")["datas"])
    assert "." in targets, "图标 / pyproject / 用户协议应放在产物根"
    assert os.path.join("ui", "locales") in targets, "语言包必须进产物（tr_bridge 按 ui/locales 找）"
    assert os.path.join("ui", "static") in targets


# ─── 4. qml 后端的形态 ─────────────────────────────────────────


def test_qml_backend_is_a_directory_build() -> None:
    qml = plan("qml")
    assert qml["entry"] == "main_qml.py"
    # onefile 每次启动都要把 Qt 运行时解到临时目录 —— 启动器不能这样
    assert qml["onefile"] is False, "QML 产物必须是目录产物（COLLECT）"
    assert qml["product_name"] == "FMCL-QML"
    assert qml["with_qt"] is True and qml["filter_collected"] is True


def test_qml_backend_collects_app_qml_and_fluentui() -> None:
    """目标名必须与 `main_qml` 的定位函数一致，否则产物"找不到界面"。"""
    datas = {str(Path(src).resolve()): dest for src, dest in plan("qml")["datas"]}
    assert datas[str((REPO_ROOT / "qml").resolve())] == "app_qml", "`qml/` 要装成 <_MEIPASS>/app_qml"
    fluentui = (REPO_ROOT / "third_party" / "_install" / "qml").resolve()
    if fluentui.is_dir():
        assert datas[str(fluentui)] == "qml", "FluentUI 模块要装成 <_MEIPASS>/qml"
    else:
        # third_party/* 不入库，新克隆的机器上不会有它 —— 那种情况下构建必须被前置检查拦住
        assert missing_qml_requirements(REPO_ROOT), "缺 FluentUI 模块却没被前置检查发现"


def test_missing_qml_requirements_detects_absent_fluentui(tmp_path: Path) -> None:
    """前置检查：空仓库 + 缺文件时必须报出原因（而不是产出一个残废产物）。"""
    reasons = missing_qml_requirements(tmp_path)
    assert len(reasons) == 2, f"空目录下应报出两条缺失原因，实际：{reasons}"
    assert any("App.qml" in reason for reason in reasons)
    assert any("FluentUI" in reason for reason in reasons)


def test_qml_backend_drops_unused_qt_modules() -> None:
    assert should_drop_collected_binary("Qt6WebEngineCore.dll") is True
    assert should_drop_collected_binary("x/Qt6Quick3D.dll") is True
    assert should_drop_collected_binary("Qt6Core.dll") is False
    assert should_drop_collected_binary("Qt6Svg.dll") is False
    assert should_drop_collected_binary("Qt6Network.dll") is False
    # Qt QML 模块目录：Qt3D 丢掉，QtQuick（Controls/Basic/Templates 都在里面）必须留
    # 真实 TOC 里的目标名是**反斜杠**（PyInstaller 6.22 实测），两种分隔符都要认。
    assert should_drop_qml_module_dir("PySide6/qml/Qt3D/Core/plugins.qmltypes") is True
    assert should_drop_qml_module_dir(r"PySide6\qml\Qt3D\Core\qmldir") is True
    assert should_drop_qml_module_dir(r"PySide6\qml\QtQuick\Controls\qmldir") is False
    assert should_drop_qml_module_dir("PySide6/qml/Qt5Compat/GraphicalEffects/qmldir") is False
    assert should_drop_qml_module_dir(r"PySide6\qml\QtQml\Models\qmldir") is False


def test_trim_never_touches_our_own_qml_files() -> None:
    """回归：裁剪刀曾经把本项目自己的 QML 一起裁掉（源路径里也有 `/qml/`）。

    实测代价：产物里没有 `app_qml/`、FluentUI 的 120 个文件只剩 1 个 DLL ——
    "看起来正常、一启动就没有界面"。判据必须只认 Qt 自己的包内路径。
    """
    ours = [
        r"D:\repo\FMCL\qml\App.qml",
        r"D:\repo\FMCL\qml\components\FmButton.qml",
        "app_qml/shell/AppShell.qml",
        r"D:\repo\FMCL\third_party\_install\qml\FluentUI\i18n\zh_CN.json",
        r"D:\repo\FMCL\third_party\_install\qml\FluentUI\Controls\Button.qml",
        "qml/FluentUI/qmldir",
    ]
    dropped = [path for path in ours if should_drop_qml_module_dir(path)]
    assert not dropped, f"这些是 App 自己的 QML，绝不能被裁：{dropped}"


def test_unknown_backend_falls_back_to_tk_plan() -> None:
    assert plan("不认识的")["entry"] == plan("tk")["entry"]


# ─── 5. build.spec 真的用上了计划 ──────────────────────────────


class _FakeAnalysis:
    """够用的 `Analysis` 替身：把入参转成 PyInstaller 那种 (dest, src, type) 三段式 TOC。"""

    instances: List["_FakeAnalysis"] = []

    def __init__(self, scripts, binaries=None, datas=None, hiddenimports=None, excludes=None, **kwargs) -> None:
        self.scripts = list(scripts)
        self.binaries = [(dest, src, "BINARY") for src, dest in (binaries or [])]
        self.binaries += [("Qt6WebEngineCore.dll", "src/Qt6WebEngineCore.dll", "BINARY")]
        self.binaries += [("Qt6Core.dll", "src/Qt6Core.dll", "BINARY")]
        self.datas = [(dest, src, "DATA") for src, dest in (datas or [])]
        self.datas += [("PySide6/qml/Qt3D/Foo.qml", "src/Foo.qml", "DATA")]
        self.datas += [("PySide6/qml/QtQuick/Controls/qmldir", "src/qmldir", "DATA")]
        self.hiddenimports = list(hiddenimports or [])
        self.excludes = list(excludes or [])
        self.pure: List[Any] = []
        self.zipped_data: List[Any] = []
        self.zipfiles: List[Any] = []
        self.kwargs = kwargs
        _FakeAnalysis.instances.append(self)


class _FakePyz:
    def __init__(self, *args, **kwargs) -> None:
        self.args = args


class _FakeExe:
    calls: List[Dict[str, Any]] = []

    def __init__(self, *args, **kwargs) -> None:
        _FakeExe.calls.append({"args": args, "kwargs": kwargs})


class _FakeCollect:
    calls: List[Dict[str, Any]] = []

    def __init__(self, *args, **kwargs) -> None:
        _FakeCollect.calls.append({"args": args, "kwargs": kwargs})


def _run_spec(monkeypatch: pytest.MonkeyPatch, backend: Optional[str]) -> Tuple[_FakeAnalysis, Dict[str, Any]]:
    """在假 PyInstaller 环境下 exec 一遍真正的 `build.spec`，返回主 Analysis 与调用记录。

    `backend=None` 表示**不设** `UI_BACKEND`（验默认回落 tk）。
    """
    _FakeAnalysis.instances = []
    _FakeExe.calls = []
    _FakeCollect.calls = []
    monkeypatch.chdir(REPO_ROOT)
    if backend is None:
        monkeypatch.delenv("UI_BACKEND", raising=False)
    else:
        monkeypatch.setenv("UI_BACKEND", backend)
    monkeypatch.delenv("PLATFORM", raising=False)
    monkeypatch.delenv("ARCH", raising=False)

    namespace: Dict[str, Any] = {
        "SPECPATH": str(REPO_ROOT),
        "Analysis": _FakeAnalysis,
        "PYZ": _FakePyz,
        "EXE": _FakeExe,
        "COLLECT": _FakeCollect,
        "BUNDLE": _FakeCollect,
    }
    source = SPEC_PATH.read_text(encoding="utf-8")
    exec(compile(source, str(SPEC_PATH), "exec"), namespace)  # noqa: S102 - 要测的就是这个文件

    assert _FakeAnalysis.instances, "build.spec 没有调用 Analysis"
    return _FakeAnalysis.instances[0], {"exe": _FakeExe.calls, "collect": _FakeCollect.calls}


def test_spec_wires_the_plan_into_analysis(monkeypatch: pytest.MonkeyPatch) -> None:
    analysis, calls = _run_spec(monkeypatch, "qml")

    assert analysis.scripts == ["main_qml.py"], "QML 后端的入口必须是 main_qml.py"
    assert "PySide6" not in analysis.excludes
    assert set(QML_DYNAMIC_IMPORTS) <= set(analysis.hiddenimports)
    assert any(dest == "app_qml" for dest, _, _ in analysis.datas)
    assert any(dest == "qml" for dest, _, _ in analysis.datas)
    assert calls["collect"], "QML 后端必须是目录产物：没有 COLLECT 就等于没产出运行目录"
    assert calls["collect"][0]["kwargs"]["name"] == "FMCL-QML"
    assert calls["exe"][0]["kwargs"]["exclude_binaries"] is True


def test_spec_trims_collected_qt_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    analysis, _ = _run_spec(monkeypatch, "qml")

    binary_names = [dest for dest, _, _ in analysis.binaries]
    assert "Qt6WebEngineCore.dll" not in binary_names, "WebEngine 是 142 MB 的单个 DLL，必须裁掉"
    assert "Qt6Core.dll" in binary_names, "裁过头了：Qt6Core 被丢了"
    data_names = [dest for dest, _, _ in analysis.datas]
    assert not [name for name in data_names if name.startswith("PySide6/qml/Qt3D/")], "Qt3D 的 QML 模块该丢"
    assert "PySide6/qml/QtQuick/Controls/qmldir" in data_names, "QtQuick 的 QML 模块必须留"
    assert "app_qml" in data_names, "本项目自己的 QML 不能被裁掉"


def test_spec_keeps_tk_backend_a_single_file(monkeypatch: pytest.MonkeyPatch) -> None:
    analysis, calls = _run_spec(monkeypatch, "tk")

    assert analysis.scripts == ["main.py"]
    assert "PySide6" in analysis.excludes
    assert not calls["collect"], "tk 后端必须还是单文件（今天的发布形态）"
    assert calls["exe"][0]["kwargs"]["name"] == "FMCL"
    assert calls["exe"][0]["kwargs"].get("exclude_binaries") in (None, False)


def test_spec_defaults_to_tk_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """没设 `UI_BACKEND` 时 spec 必须按 tk 构建（与今天的行为一致）。"""
    analysis, calls = _run_spec(monkeypatch, None)
    assert analysis.scripts == ["main.py"]
    assert not calls["collect"], "没设后端时不该产出目录产物"
