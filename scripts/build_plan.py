# -*- coding: utf-8 -*-
r"""构建计划 —— `build.spec` 的唯一真源。

## 为什么把逻辑从 spec 里搬出来

`build.spec` 是被 PyInstaller 用 `exec` 执行的脚本，测试 import 不了它，改错了只能"打个包
试试"。QML 侧的桥与服务表全是 `importlib.import_module("模块名")` 的动态导入
（`main_qml.SINGLETON_BRIDGES`、`main_qml.CONTEXT_BRIDGES`、`app.bootstrap` 的服务表），
PyInstaller 的静态分析**看不见**它们 —— 漏收一个的后果是"打包产物一启动就崩"，而源码态
永远复现不出来。

搬成普通模块后 `tests/test_build_plan.py` 可以直接对着它断言（动态导入清单是否覆盖了那三张
真源表、QML 后端有没有把 PySide6 排掉、entry 是否指向 main_qml、产物是否 onedir），
桥或服务表变动时会红。

## 产物形态

只有一种产物：入口 `main_qml.py`，**onedir**（`COLLECT`）到 `dist/FMCL/`，
收集 `qml/`、自编译的 FluentUI 模块与 Qt 运行时。没有后端开关，也没有 onefile。

为什么 QML 走 onedir 而不是 onefile：onefile 每次启动都要把 Qt 运行时（约 200 MB）解压到临时
目录，启动器的冷启动体验不可接受；而 `updater.py` 只认 `FMCL-Setup-*.exe` 安装包，**装目录
不影响自动更新链路**（已核对 `updater.py::find_suitable_asset`）。

安装目录固定为 `%LOCALAPPDATA%\Programs\FMCL`。用户数据默认跟随程序目录（`config.json`、
`.minecraft`、账号、歌单、成就），产物名与安装路径保持不变，升级不会切换数据位置。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def detect_platform(sys_platform: str) -> str:
    """`sys.platform` → 构建平台码（与 build.spec 原有语义一致）。"""
    if sys_platform == "win32":
        return "win"
    if sys_platform == "darwin":
        return "mac"
    return "linux"


# ─── 依赖清单 ──────────────────────────────────────────────────
#: 通用隐式导入。动态 import 的包不要凭感觉删：它们不在静态分析结果里，
#: 漏收的后果是打包产物运行到对应功能时才失败。
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
    "PIL",
    "PIL.Image",
    "orjson",
    "urllib3",
    "rarfile",
    "markdown",
    "_build_secrets",
)

#: 语音输入栈（sounddevice + onnxruntime + sentencepiece）。
VOICE_HIDDEN_IMPORTS: Tuple[str, ...] = (
    "sounddevice",
    "_sounddevice_data",
    "numpy",
    "sentencepiece",
    "onnxruntime",
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
    "app.bridges.install_bridge",
    # 设置、日志、关于
    "app.bridges.settings_bridge",
    "app.bridges.log_bridge",
    "app.bridges.about_bridge",
    # 账号、资源、在线资源浏览
    "app.bridges.accounts_bridge",
    "app.bridges.resources_bridge",
    "app.bridges.mod_browser_bridge",
    # 整合包、服务器、服务器配置
    "app.bridges.modpack_bridge",
    "app.bridges.servers_bridge",
    "app.bridges.server_config_bridge",
    # 服务器模组与资源包
    "app.bridges.server_mods_bridge",
    "app.bridges.server_mod_browser_bridge",
    # 联机、存档备份、基岩版
    "app.bridges.online_bridge",
    "app.bridges.backup_bridge",
    "app.bridges.bedrock_bridge",
    # 音乐、工具箱、相关网站
    "app.bridges.music_bridge",
    "app.bridges.tools_bridge",
    "app.bridges.links_bridge",
    # 成就、净读账号、AGENT
    "app.bridges.achievements_bridge",
    "app.bridges.jingdu_bridge",
    "app.bridges.agent_bridge",
    # 插件、更新
    "app.bridges.plugin_bridge",
    "app.bridges.update_bridge",
    # app.bootstrap 的服务表（`_make_factory` 按 "模块:类名" 导入）
    "services.about_service",
    "services.account_service",
    "services.achievement_service",
    "services.agent_service",
    "services.agent_chat_service",
    "services.bedrock_service",
    "services.crash_service",
    "services.game_service",
    "services.install_service",
    "services.jingdu_account_service",
    "services.links_service",
    "services.log_service",
    "services.mod_browser_service",
    "services.modpack_service",
    "services.online_service",
    # 插件服务（管理与市场共用）
    "services.plugin_service",
    # 更新服务
    "services.update_service",
    "services.plugin_browser_service",
    "services.resource_service",
    "services.server_service",
    "services.settings_service",
    "services.tool_service",
    "services.version_service",
    "services.voice_service",
)

#: QML 产物排除的 Python 绑定：**不能**排 PySide6（那正是 QML 的界面栈）。
#: 额外 hiddenimports：不在 main_qml 桥表 / 服务表里，但运行时才 import 的包。
#: 单独一张表，避免污染 `QML_DYNAMIC_IMPORTS` 的"真源表"反向检查。
QML_EXTRA_HIDDEN_IMPORTS: Tuple[str, ...] = ("fusion_music.host", "fusion_music.migration")

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
    # 用不到的 Qt 绑定（体积大头）。保留的模块以 `app/` 与 `main_qml.py` 的
    # `from PySide6.* import` 实际清单为准。
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
#: 依据实测裁剪表（POC 打包后能跑）。
QML_DROP_BINARY_PATTERNS: Tuple[str, ...] = (
    "Qt6WebEngineCore",
    "Qt6WebEngineQuick",
    "Qt6WebEngine",
    "Qt6Pdf",
    "Qt6Designer",
    "Qt6Charts",
    "Qt6DataVisualization",
    "Qt6Graphs",
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
)

#: `PySide6/qml` 下要**保留**的模块目录。实测要用到的 Qt QML 模块（从 FluentUI 上游与本项目
#: QML 的 `import` 行统计）：QtQuick（含 Controls/Basic/impl/Templates/Layouts/Window/Shapes）、
#: QtQml、Qt5Compat.GraphicalEffects、Qt.labs.qmlmodels —— 都在 `Qt` / `QtCore` / `QtNetwork` /
#: `QtQml` / `QtQuick` / `Qt5Compat` 这些目录里。`QtMultimedia` 不是 QML 页面直接 import 的，
#: 但 `fusion_music` 的 Python 侧用 QMediaPlayer / QAudioOutput / QAudioDecoder，必须保留。
#: 其余（Qt3D / QtCharts / QtWebEngine / QtQuick3D / QtSensors …）整目录丢掉。
QML_KEEP_MODULE_DIRS: Tuple[str, ...] = ("Qt", "QtCore", "Qt5Compat", "QtNetwork", "QtMultimedia", "QtQml", "QtQuick")


def should_drop_collected_binary(name: str) -> bool:
    """收集到的二进制（DLL/pyd）是否该丢。`name` 是**基名或路径**，按子串匹配。"""
    base = os.path.basename(name)
    return any(pattern in base for pattern in QML_DROP_BINARY_PATTERNS)


def should_drop_qml_module_dir(path: str) -> bool:
    """`PySide6\\qml\\<模块>` 下的文件是否该丢（只保留 `QML_KEEP_MODULE_DIRS` 里的目录）。

    **必须带 `PySide6` 前缀**：本项目自己的 `qml/App.qml` 与
    `third_party/_install/qml/FluentUI/**` 的**源路径**里也含 `/qml/`，只按 `/qml/`
    匹配会把整个 App 的 QML 与 FluentUI 模块裁掉，产物"看着正常、一启动就没有界面"。
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
    """存在才收（与 `build.spec` 的 `if os.path.exists(...)` 语义一致）。"""
    source = root / relative_source
    if not source.exists():
        return None
    return (str(source), target)


def common_datas(root: Path) -> List[Tuple[str, str]]:
    """所有产物都要带的数据文件：图标 + 版本元数据 + 语言包 + 静态资源 + 条款 + .NET helper 源码。"""
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
        entries.append(
            _data(
                root, os.path.join(native_root_rel, relative), os.path.join(native_root_rel, os.path.dirname(relative))
            )
        )
    return [entry for entry in entries if entry is not None]


def qml_datas(root: Path) -> List[Tuple[str, str]]:
    """QML 产物需要的数据文件。

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


#: FluentUI QML 插件文件名（Windows / Linux / macOS）。
#:
#: 只校验 qmldir 不够：qmldir 里的 `plugin fluentuiplugin` 需要同目录的真实动态库，
#: 缺了它 QML 引擎会在 `import FluentUI` 处失败、整个根对象建不出来。
FLUENTUI_PLUGIN_FILENAMES: Tuple[str, ...] = (
    "fluentuiplugin.dll",
    "fluentuiplugin.so",
    "libfluentuiplugin.so",
    "fluentuiplugin.dylib",
    "libfluentuiplugin.dylib",
)


def missing_qml_requirements(root: Path) -> List[str]:
    """QML 后端必需、但仓库里可能还没有的东西（`[]` = 齐了）。

    **为什么要在构建前显式拦一道**：`third_party/*` 是 `.gitignore` 的（体积原因，只入库
    来源记录与取源/构建脚本），所以一台新克隆的机器上根本没有 `_install/qml`。不拦的话
    PyInstaller 会照常产出一个"看起来正常、一启动就找不到界面"的产物 —— 那是最难查的一类
    打包故障（源码态永远复现不出来）。宁可构建时**响亮地失败**，并给出补救命令。
    """
    checks = (
        (root / "qml" / "App.qml", "missing project QML entry: qml/App.qml"),
        (
            root / "third_party" / "_install" / "qml" / "FluentUI" / "qmldir",
            "missing compiled FluentUI module: third_party/_install/qml/FluentUI/qmldir",
        ),
    )
    reasons = [reason for path, reason in checks if not path.is_file()]
    plugin_dir = root / "third_party" / "_install" / "qml" / "FluentUI"
    if not any((plugin_dir / name).is_file() for name in FLUENTUI_PLUGIN_FILENAMES):
        reasons.append(
            "missing compiled FluentUI plugin binary in third_party/_install/qml/FluentUI"
        )
    return reasons


# ─── 计划 ──────────────────────────────────────────────────────


#: 唯一产物名。安装器、快捷方式、注册表键与
#: `%LOCALAPPDATA%\Programs\FMCL` 都依赖它，不要改名。
PRODUCT_NAME = "FMCL"


def plan(root: Optional[str] = None, platform: Optional[str] = None) -> Dict[str, object]:
    """产出构建计划（`build.spec` 与测试共用的入口）。

    Args:
        root: 仓库根目录；None → 当前工作目录（PyInstaller 就是从仓库根跑的）。
        platform: `win` / `mac` / `linux`；None → 按当前平台检测。

    Returns:
        dict：product_name / entry / datas / hidden_imports / excludes /
        filter_collected / with_qt。

    QML 是唯一界面，没有后端参数，也不再读取 `UI_BACKEND`。
    """
    root_path = Path(root or os.getcwd()).resolve()
    resolved_platform = platform or detect_platform(sys.platform)

    datas = common_datas(root_path) + qml_datas(root_path)

    hidden = list(COMMON_HIDDEN_IMPORTS) + list(VOICE_HIDDEN_IMPORTS)
    hidden += list(QML_DYNAMIC_IMPORTS) + list(QML_EXTRA_HIDDEN_IMPORTS)
    if resolved_platform == "mac":
        hidden += ["AppKit", "Foundation"]

    return {
        "product_name": PRODUCT_NAME,
        "entry": "main_qml.py",
        "datas": datas,
        "hidden_imports": hidden,
        "excludes": list(QML_EXCLUDES),
        "filter_collected": True,
        "with_qt": True,
        "platform": resolved_platform,
        "root": str(root_path),
        "icon": str(root_path / "icon.ico"),
    }
