# -*- coding: utf-8 -*-
"""构建计划 —— `build.spec` 的唯一真源。

## 为什么把逻辑从 spec 里搬出来

`build.spec` 是被 PyInstaller 用 `exec` 执行的脚本，测试 import 不了它，改错了只能"打个包
试试"。而两个后端（tk / qml）的差异恰好属于**最容易悄悄错**的那一类：QML 侧的桥与服务表
全是 `importlib.import_module("模块名")` 的动态导入（`main_qml.SINGLETON_BRIDGES`、
`main_qml.CONTEXT_BRIDGES`、`app.bootstrap` 的服务表），PyInstaller 的静态分析**看不见**它们 ——
漏收一个的后果是"打包产物一启动就崩"，而源码态永远复现不出来。

搬成普通模块后 `tests/test_build_plan.py` 可以直接对着它断言（动态导入清单是否覆盖了那三张
真源表、QML 后端有没有把 PySide6 排掉、entry 与 onefile 是否配对），桥或服务表变动时会红。

## 后端选择

环境变量 `UI_BACKEND`（`tk` / `qml`），与既有的 `PLATFORM` / `ARCH` 同一套路：

* `tk`（默认）：入口 `main.py`，单文件 EXE，**排除** PySide6 —— 与今天发布的产物一致；
* `qml`：入口 `main_qml.py`，**onedir**（`COLLECT`），收集 `qml/`、自编译的 FluentUI 模块
  与 Qt 运行时。

为什么 QML 走 onedir 而不是 onefile：onefile 每次启动都要把 Qt 运行时（约 200 MB）解压到临时
目录，启动器的冷启动体验不可接受；而 `updater.py` 只认 `FMCL-Setup-*.exe` 安装包，**装目录
不影响自动更新链路**（已核对 `updater.py::find_suitable_asset`）。

## 没有做单产物双后端

阶段 4.1 才需要"一个 exe 里同时有 Tk 与 Qt，用 `ui_backend` 现场切换"。现在做等于让每个验收
产物多背一套界面，且会在 `build.spec` 里引入两套 hook 的相互干扰 —— 留到阶段 4.3。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

#: 合法的界面后端。`config.ui_backend` 的校验、`main.py` 的入口分派与 build.spec 共用这一份。
UI_BACKENDS: Tuple[str, ...] = ("tk", "qml")

#: 默认后端。**阶段 3 期间必须保持 tk** —— QML 侧的 12 个页面还是占位壳，
#: 提前切默认等于把空页面发给用户（阶段 4.1 才切，并留 `ui_backend` 作为回滚开关）。
DEFAULT_UI_BACKEND = "tk"


def normalize_backend(value: Optional[str]) -> str:
    """把任意输入收敛成合法后端名：非法值一律回落到默认后端（调用方负责记日志）。"""
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in UI_BACKENDS:
            return candidate
    return DEFAULT_UI_BACKEND


def detect_platform(sys_platform: str) -> str:
    """`sys.platform` → 构建平台码（与 build.spec 原有语义一致）。"""
    if sys_platform == "win32":
        return "win"
    if sys_platform == "darwin":
        return "mac"
    return "linux"


# ─── 依赖清单 ──────────────────────────────────────────────────
#: 通用隐式导入（与迁移前逐条一致，不要凭感觉删：tkinterweb / customtkinter 这类包
#: 是"运行时按需 import"，静态分析未必收得全）。
COMMON_HIDDEN_IMPORTS: Tuple[str, ...] = (
    "minecraft_launcher_lib",
    "minecraft_launcher_lib._helper",
    "minecraft_launcher_lib._internal_types",
    "minecraft_launcher_lib.command",
    "minecraft_launcher_lib.exceptions",
    "minecraft_launcher_lib.fabric",
    "minecraft_launcher_lib.forge",
    "minecraft_launcher_lib.install",
    "minecraft_launcher_lib.java_utils",
    "minecraft_launcher_lib.microsoft_account",
    "minecraft_launcher_lib.mod_loader",
    "minecraft_launcher_lib.mod_loader._forge",
    "minecraft_launcher_lib.mod_loader._fabric",
    "minecraft_launcher_lib.mod_loader._neoforge",
    "minecraft_launcher_lib.mrpack",
    "minecraft_launcher_lib.natives",
    "minecraft_launcher_lib.news",
    "minecraft_launcher_lib.quilt",
    "minecraft_launcher_lib.runtime",
    "minecraft_launcher_lib.types",
    "minecraft_launcher_lib.utils",
    "minecraft_launcher_lib.vanilla_launcher",
    "forgepy",
    "requests",
    "logzero",
    "tqdm",
    "keyboard",
    "tkinter",
    "tkinter.ttk",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.colorchooser",
    "tkinter.commondialog",
    "tkinter.constants",
    "PIL",
    "PIL.Image",
    "PIL.ImageTk",
    "customtkinter",
    "orjson",
    "urllib3",
    "rarfile",
    "markdown",
    "tkinterweb",
    "_build_secrets",
)

#: 语音输入栈（sounddevice + onnxruntime + sentencepiece），两种后端都要。
VOICE_HIDDEN_IMPORTS: Tuple[str, ...] = (
    "sounddevice",
    "_sounddevice_data",
    "numpy",
    "sentencepiece",
    "onnxruntime",
    "ui.agent.voice_input",
    "ui.agent.voice",
    "ui.agent.voice.sensevoice",
    "ui.agent.voice.models",
    # 任务 1.15：业务逻辑已搬到 services/，ui.* 只剩转发 shim；sensevoice 依然是
    # 延迟 import（`_load_engine` 内），所以两边都登记。
    "services.voice_service",
    "services.voice",
    "services.voice.sensevoice",
    "services.voice.models",
)

#: **动态导入**清单：这些模块名只以字符串形式出现，静态分析收不到。
#: 三张真源表：`main_qml.SINGLETON_BRIDGES`、`main_qml.CONTEXT_BRIDGES`、
#: `app.bootstrap` 的服务表；`tests/test_build_plan.py` 会逐条核对（漏一条即红）。
QML_DYNAMIC_IMPORTS: Tuple[str, ...] = (
    # main_qml.SINGLETON_BRIDGES（qmlRegisterSingletonType 的三个参数之一）
    "app.bridges.theme_bridge",
    "app.bridges.tr_bridge",
    # main_qml.CONTEXT_BRIDGES（setContextProperty 的对象来源）
    "app.bridges.runtime_bridge",
    "app.bridges.event_bridge",
    "app.bridges.qt_tasks",
    "app.bridges.dialog_bridge",
    "app.bridges.nav_bridge",
    "app.bridges.hotkey_bridge",
    "app.bridges.overlay_bridge",
    "app.bridges.shell_bridge",
    "app.bridges.home_bridge",
    "app.bridges.version_bridge",
    # app.bootstrap 的服务表（`_make_factory` 按 "模块:类名" 导入）
    "services.account_service",
    "services.achievement_service",
    "services.agent_service",
    "services.bedrock_service",
    "services.crash_service",
    "services.game_service",
    "services.mod_browser_service",
    "services.modpack_service",
    "services.music_player",
    "services.online_service",
    "services.plugin_browser_service",
    "services.resource_service",
    "services.server_service",
    "services.tool_service",
    "services.version_service",
    "services.voice_service",
)

#: tk 后端排除的包（与迁移前逐条一致：tk 产物里不该出现 Qt 与科学计算栈）。
TK_EXCLUDES: Tuple[str, ...] = (
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "matplotlib",
    "pandas",
    "scipy",
    "IPython",
    "jupyter",
    "notebook",
)

#: qml 后端排除的 Python 绑定：**不能**再排 PySide6（那正是 QML 后端的界面栈）。
#: 其余与 tk 后端保持一致 —— 包括 `scipy`：今天两个产物都不含它（音效链的 EQ 依赖 scipy，
#: D-151 已挂账"整条链在任何机器上都不跑"），修音效链时（阶段 3.18）两个产物一起评估，
#: 现在偷偷只给 QML 产物加上会让两个产物行为不一致。
QML_EXCLUDES: Tuple[str, ...] = (
    "PyQt5",
    "PyQt6",
    "PySide2",
    "matplotlib",
    "pandas",
    "scipy",
    "IPython",
    "jupyter",
    "notebook",
    # 用不到的 Qt 绑定（体积大头；依据 poc/fmcl_qml_poc.spec 的实测裁剪表 + 本项目
    # `app/` 与 `main_qml.py` 里的 `from PySide6.* import` 实测清单：
    # QtCore / QtGui / QtNetwork / QtQml / QtQuick / QtSvg，仅此六个）。
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
    "PySide6.QtWebSockets",
    "PySide6.QtWebView",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DRender",
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtGraphs",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtSpatialAudio",
    "PySide6.QtQuick3D",
    "PySide6.QtQuick3DUtils",
    "PySide6.QtBluetooth",
    "PySide6.QtNfc",
    "PySide6.QtPositioning",
    "PySide6.QtLocation",
    "PySide6.QtSql",
    "PySide6.QtTest",
    "PySide6.QtSensors",
    "PySide6.QtSerialPort",
    "PySide6.QtSerialBus",
    "PySide6.QtRemoteObjects",
    "PySide6.QtScxml",
    "PySide6.QtTextToSpeech",
    "PySide6.QtHelp",
    "PySide6.QtDesigner",
    "PySide6.QtUiTools",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtNetworkAuth",
    "PySide6.QtHttpServer",
)

#: 收集到的二进制里按**子串**丢弃的名字（PyInstaller 的 PySide6 hook 会连带收集一批
#: 未使用的大体积 DLL，其中 Qt6WebEngineCore.dll 单个就有 142 MB）。
#: 依据 `poc/fmcl_qml_poc.spec` 的实测裁剪表（POC 打包后能跑）。
QML_DROP_BINARY_PATTERNS: Tuple[str, ...] = (
    "Qt6WebEngineCore",
    "Qt6WebEngineQuick",
    "Qt6WebEngine",
    "Qt6Pdf",
    "Qt6Designer",
    "Qt6Charts",
    "Qt6DataVisualization",
    "Qt6Graphs",
    "Qt6Multimedia",
    "Qt6Quick3D",
    "Qt63D",
    "Qt6Location",
    "Qt6Positioning",
    "Qt6Bluetooth",
    "Qt6Nfc",
    "Qt6Sql",
    "Qt6Test",
    "Qt6Sensors",
    "Qt6SerialPort",
    "Qt6RemoteObjects",
    "Qt6Scxml",
    "Qt6TextToSpeech",
    "Qt6Help",
    "Qt6UiTools",
    "Qt6VirtualKeyboard",
    "Qt6WebSockets",
    "Qt6WebChannel",
    "avcodec",
    "avformat",
    "avutil",
    "swresample",
    "swscale",
)

#: `PySide6/qml` 下要**保留**的模块目录。实测要用到的 Qt QML 模块（从 FluentUI 上游与本项目
#: QML 的 `import` 行统计）：QtQuick（含 Controls/Basic/impl/Templates/Layouts/Window/Shapes）、
#: QtQml、Qt5Compat.GraphicalEffects、Qt.labs.qmlmodels —— 都在 `Qt` / `QtCore` / `QtNetwork` /
#: `QtQml` / `QtQuick` / `Qt5Compat` 这六个目录里。其余（Qt3D / QtCharts / QtMultimedia /
#: QtWebEngine / QtQuick3D / QtSensors …）在 QML 侧没有任何 import，整目录丢掉。
QML_KEEP_MODULE_DIRS: Tuple[str, ...] = ("Qt", "QtCore", "Qt5Compat", "QtNetwork", "QtQml", "QtQuick")


def should_drop_collected_binary(name: str) -> bool:
    """收集到的二进制（DLL/pyd）是否该丢。`name` 是**基名或路径**，按子串匹配。"""
    base = os.path.basename(name)
    return any(pattern in base for pattern in QML_DROP_BINARY_PATTERNS)


def should_drop_qml_module_dir(path: str) -> bool:
    """`PySide6\\qml\\<模块>` 下的文件是否该丢（只保留 `QML_KEEP_MODULE_DIRS` 里的目录）。

    **必须带 `PySide6` 前缀**，这是踩过的坑：第一版只找 `/qml/`，于是本项目自己的
    `qml/App.qml` 与 `third_party/_install/qml/FluentUI/**` 的**源路径**里也含 `/qml/`，
    整个 App 的 QML 与 FluentUI 的 120 个文件被当场裁光 —— 产物"看着正常、一启动就没有界面"。
    判据只认 Qt 自己的包内路径（大小写不敏感，`\\` 与 `/` 都当分隔符）。
    """
    normalized = path.replace("\\", "/").lower()
    marker = "pyside6/qml/"
    if marker not in normalized:
        return False
    tail = normalized.split(marker, 1)[1]
    first = tail.split("/", 1)[0]
    if not first or first.endswith(".qmltypes") or first.endswith(".json"):
        return False
    return first not in {name.lower() for name in QML_KEEP_MODULE_DIRS}


# ─── 数据文件 ──────────────────────────────────────────────────


def _data(root: Path, relative_source: str, target: str) -> Optional[Tuple[str, str]]:
    """存在才收（与迁移前 spec 的 `if os.path.exists(...)` 语义一致）。"""
    source = root / relative_source
    if not source.exists():
        return None
    return (str(source), target)


def common_datas(root: Path) -> List[Tuple[str, str]]:
    """两种后端共用的数据文件：图标 + 版本元数据 + 语言包 + 静态资源 + 条款 + .NET helper 源码。"""
    entries: List[Optional[Tuple[str, str]]] = [
        (str(root / "icon.ico"), "."),
        (str(root / "pyproject.toml"), "."),
        _data(root, "ui/locales", os.path.join("ui", "locales")),
        _data(root, "ui/static", os.path.join("ui", "static")),
        _data(root, "TERMS_OF_USE.md", "."),
    ]
    # .NET 辅助程序源码（GDK 解压/认证注入：运行时用 dotnet 在用户机器上构建）
    native_root_rel = os.path.join("launcher", "bedrock", "native")
    for relative in (
        os.path.join("helper", "BedrockGdkHelper.csproj"),
        os.path.join("helper", "Program.cs"),
        os.path.join("extractor", "BedrockXvdExtractor.csproj"),
        os.path.join("extractor", "Program.cs"),
    ):
        entries.append(_data(root, os.path.join(native_root_rel, relative), os.path.join(native_root_rel, os.path.dirname(relative))))
    return [entry for entry in entries if entry is not None]


def qml_datas(root: Path) -> List[Tuple[str, str]]:
    """QML 后端独有的数据文件。

    目标名必须与代码里的定位方式一致，否则打包产物会"找不到界面"：

    * `qml/` → `<_MEIPASS>/app_qml`（`main_qml.app_qml_path()`）；
    * `third_party/_install/qml` → `<_MEIPASS>/qml`（`main_qml.qml_import_path()`，
      里面是自己编译的 `fluentuiplugin.dll`）；
    * `ui/locales` 走 `common_datas`（`tr_bridge.locales_dir()` 的第一个候选就是 `ui/locales`）。
    """
    entries: List[Optional[Tuple[str, str]]] = [
        _data(root, "qml", "app_qml"),
        _data(root, os.path.join("third_party", "_install", "qml"), "qml"),
    ]
    return [entry for entry in entries if entry is not None]


def missing_qml_requirements(root: Path) -> List[str]:
    """QML 后端必需、但仓库里可能还没有的东西（`[]` = 齐了）。

    **为什么要在构建前显式拦一道**：`third_party/*` 是 `.gitignore` 的（体积原因，只入库
    来源记录与取源/构建脚本），所以一台新克隆的机器上根本没有 `_install/qml`。不拦的话
    PyInstaller 会照常产出一个"看起来正常、一启动就找不到界面"的产物 —— 那是最难查的一类
    打包故障（源码态永远复现不出来）。宁可构建时**响亮地失败**，并给出补救命令。
    """
    checks = (
        (root / "qml" / "App.qml", "本项目 QML 入口缺失：qml/App.qml"),
        (
            root / "third_party" / "_install" / "qml" / "FluentUI" / "qmldir",
            "自编译的 FluentUI 模块缺失：third_party/_install/qml/FluentUI/qmldir",
        ),
    )
    return [reason for path, reason in checks if not path.is_file()]


# ─── 计划 ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class BackendSpec:
    """一个后端的构建参数。"""

    backend: str
    product_name: str
    entry: str
    onefile: bool
    hidden_imports: Tuple[str, ...] = ()
    excludes: Tuple[str, ...] = ()
    #: 是否收集 QML 数据文件与 Qt 运行时（决定要不要跑 collect_dynamic_libs 与 qml 目录过滤）
    with_qt: bool = False
    #: 是否在 `Analysis` 之后再过滤收集到的二进制/数据（Qt hook 会带来一批没用的 DLL）
    filter_collected: bool = False


BACKENDS: Dict[str, BackendSpec] = {
    "tk": BackendSpec(
        backend="tk",
        product_name="FMCL",
        entry="main.py",
        onefile=True,
        hidden_imports=(),
        excludes=TK_EXCLUDES,
        with_qt=False,
        filter_collected=False,
    ),
    "qml": BackendSpec(
        backend="qml",
        product_name="FMCL-QML",
        entry="main_qml.py",
        # onefile 每次启动都要把 Qt 运行时解到临时目录；启动器不能这样。
        onefile=False,
        hidden_imports=QML_DYNAMIC_IMPORTS,
        excludes=QML_EXCLUDES,
        with_qt=True,
        filter_collected=True,
    ),
}


def plan(backend: Optional[str] = None, root: Optional[str] = None, platform: Optional[str] = None) -> Dict[str, object]:
    """产出构建计划（`build.spec` 与测试共用的入口）。

    Args:
        backend: 后端名；None 或非法值 → 默认后端（`normalize_backend`）。
        root: 仓库根目录；None → 当前工作目录（PyInstaller 就是从仓库根跑的）。
        platform: `win` / `mac` / `linux`；None → 按当前平台检测。

    Returns:
        dict：backend / product_name / entry / onefile / datas / hidden_imports / excludes /
        filter_collected / with_qt。
    """
    spec = BACKENDS[normalize_backend(backend)]
    root_path = Path(root or os.getcwd()).resolve()
    resolved_platform = platform or detect_platform(sys.platform)

    datas = common_datas(root_path)
    if spec.with_qt:
        datas = datas + qml_datas(root_path)

    hidden = list(COMMON_HIDDEN_IMPORTS) + list(VOICE_HIDDEN_IMPORTS) + list(spec.hidden_imports)
    if resolved_platform == "win":
        hidden += ["pywintypes", "win32com", "win32com.client"]
    elif resolved_platform == "mac":
        hidden += ["AppKit", "Foundation"]

    return {
        "backend": spec.backend,
        "product_name": spec.product_name,
        "entry": spec.entry,
        "onefile": spec.onefile,
        "datas": datas,
        "hidden_imports": hidden,
        "excludes": list(spec.excludes),
        "filter_collected": spec.filter_collected,
        "with_qt": spec.with_qt,
        "platform": resolved_platform,
        "root": str(root_path),
        "icon": str(root_path / "icon.ico"),
    }
