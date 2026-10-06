"""QML 版入口 —— 程序装配（阶段 2 任务 2.2）。

## 它做什么、不做什么

**做**：QML 引擎、QML 导入路径、桥接对象注册、`AppContext` 装配、单实例、
致命错误兜底窗口、CLI 模式透传、把 `Theme.fontFamily` 应用到应用级字体（任务 2.10，
见 `apply_theme_font()`）。

**不做**（留给后面的任务）：

* 启动画面与"协议 → 公告 → 预下载"链条 → 任务 2.14（`03` 的 2.1 曾把它写进入口，
  按 `06` 的任务集拆出去了）；
* 窗口骨架与一级导航 → 任务 2.12；
* 主题 / i18n 单例的实现 → 任务 2.8 / 2.9（本文件只负责**注册**它们，
  且**在它们还不存在时优雅跳过**，这样入口可以先独立跑起来并被测试）。

## 三条硬约束

1. **不 import `ui/`**：`ui/__init__.py` 会拉起 `customtkinter`（阶段 0 第 10.4 节实测踩过）。
2. **复用而不是重写**：CLI 解析、登录模式、Agent CLI 模式、日志初始化、错误弹窗文案
   全部从 `main.py` 导入复用 —— `main.py` 的顶层 import 只有标准库 + logzero + config，
   所以 import 它是安全的（`customtkinter` 在 `main()` 函数体内才导入）。
   迁移红线 2：业务逻辑只搬一次，入口不许出现第二份实现。
3. **注册容错**：某个桥模块缺失/导入报错时只记警告并继续，不能因此起不来 ——
   阶段 2 是分四条支线并行落地的，入口必须能在"桥只到齐一半"时启动，
   否则每个人都被别人的进度卡住。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent
APP_NAME = "FMCL"

logger = logging.getLogger("main_qml")

# ─── QML 引擎的进程级开关（D-150）──────────────────────────────
# **必须在任何 `QQmlApplicationEngine()` 建起来之前**设好：QML 磁盘缓存是引擎侧的静态状态，
# 引擎一建就定了，之后再改环境变量没有任何作用（`poc/_probe_disk_cache_state.py` 实测：
# 设 `1` 时缓存目录里 0 个 `.qmlc`，不设或设 `0` 时写出 27 个）。
#
# 为什么产品侧要关掉它（D-150）：磁盘缓存让编译单元**异步**取回，而主题热切换会连带重建一批
# 对象；若在"单元还没取回来"的窗口里建对象，引擎就报 `QmlIcon: Cannot find member data` /
# `Property 'xxx' is not a function` —— 那是**对象建了一半**，不只是日志噪声。
# 实测（`poc/_measure_d150.py`，每档各跑两遍）：缓存开着 → 主题段稳定 10 条；关掉 → 0 条。
# 代价（打包产物冷启动到窗口出现，`poc/_smoke_packaged.py`）：关掉后 ≤1.2 s，与开着相比
# 差值落在 400 ms 采样精度之内 —— 用这点启动时间换掉一类"半成品对象"竞态是划算的。
#
# `setdefault`：显式设 `QML_DISABLE_DISK_CACHE=0` 仍可把它打开（做对照实验用）。
os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

#: `QML` 单例（C++ 侧注册成 URI 类型，QML 里 `import FMCL 1.0`）。
#: 名字必须与 `docs/refactor/11-phase2-contract.md` 第四节一致。
SINGLETON_BRIDGES: Tuple[Tuple[str, str, str], ...] = (
    ("Theme", "app.bridges.theme_bridge", "ThemeBridge"),
    ("Tr", "app.bridges.tr_bridge", "TrBridge"),
)

#: 上下文属性（QML 里直接按名字用，不需要 import）。
CONTEXT_BRIDGES: Tuple[Tuple[str, str, str], ...] = (
    ("Runtime", "app.bridges.runtime_bridge", "RuntimeBridge"),
    ("Events", "app.bridges.event_bridge", "EventBridge"),
    ("Tasks", "app.bridges.qt_tasks", "TaskBridge"),
    ("Dialogs", "app.bridges.dialog_bridge", "DialogBridge"),
    ("Nav", "app.bridges.nav_bridge", "NavBridge"),
    ("Hotkeys", "app.bridges.hotkey_bridge", "HotkeyBridge"),
    ("Overlay", "app.bridges.overlay_bridge", "OverlayBridge"),
    ("Shell", "app.bridges.shell_bridge", "ShellBridge"),
    ("Home", "app.bridges.home_bridge", "HomeBridge"),
    ("Versions", "app.bridges.version_bridge", "VersionsBridge"),
    ("Install", "app.bridges.install_bridge", "InstallBridge"),
    # 设置域（阶段 3 任务 3.4）：三个桥对应设置页的三块能力 ——
    # 草稿/主题/Java/语言（`Settings`）、日志（`Logs`）、关于（`About`）。
    ("Settings", "app.bridges.settings_bridge", "SettingsBridge"),
    ("Logs", "app.bridges.log_bridge", "LogBridge"),
    ("About", "app.bridges.about_bridge", "AboutBridge"),
)

QML_MODULE_URI = "FMCL"
QML_MODULE_VERSION_MAJOR = 1


# ─── 路径 ──────────────────────────────────────────────────────


def qml_import_path() -> Path:
    """FluentUI 自编译插件的 QML 模块目录。

    * 打包后：PyInstaller 把 `third_party/_install/qml` 整体放到 `<_MEIPASS>/qml`
      （见 `poc/fmcl_qml_poc.spec` 的做法，阶段 4 的 `build.spec` 会照抄）。
    * 开发态：直接用仓库里的构建产物。
    """
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "qml"
    return PROJECT_ROOT / "third_party" / "_install" / "qml"


def app_qml_path(name: str = "App.qml") -> Path:
    """本项目的 QML 根组件路径。"""
    meipass = getattr(sys, "_MEIPASS", None)
    base = Path(meipass) / "app_qml" if meipass else PROJECT_ROOT / "qml"
    return base / name


# ─── Qt 消息 → 启动器日志 ──────────────────────────────────────


class QtMessageSink:
    """收集 QML/引擎的警告与错误（供日志与冒烟测试用）。

    QML 的 `console.log` 与绑定错误默认只打到 stderr，用户看不到、测试也断言不了。
    这里把它们收进一个列表，既能落进 `latest.log`，也能被 2.19 的冒烟测试当作
    "任一页面报错即失败"的判据。
    """

    def __init__(self) -> None:
        self.messages: List[str] = []

    def handler(self, mode: Any, context: Any, message: str) -> None:  # noqa: ANN001
        text = str(message)
        self.messages.append(text)
        if "error" in str(mode).lower():
            logger.error("[QML] %s", text)
        elif "warning" in str(mode).lower():
            logger.warning("[QML] %s", text)
        else:
            logger.debug("[QML] %s", text)

    @property
    def errors(self) -> List[str]:
        return [m for m in self.messages if "is not defined" in m or "TypeError" in m or "Unable to assign" in m]


def install_message_handler(sink: Optional[QtMessageSink] = None) -> QtMessageSink:
    """把 Qt 的消息处理器指向启动器日志。返回收集器（测试可断言其内容）。"""
    from PySide6.QtCore import qInstallMessageHandler

    sink = sink or QtMessageSink()
    qInstallMessageHandler(sink.handler)
    return sink


# ─── 桥接注册 ──────────────────────────────────────────────────


def _instantiate(module_name: str, class_name: str) -> Any:
    """按"模块:类名"懒导入并实例化（与 `app/bootstrap.py` 的服务表同一套路）。"""
    import importlib

    module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    return cls()


def register_bridges(engine: Any, context: Any, prebuilt: Optional[Dict[str, Any]] = None) -> Dict[str, List[str]]:
    """注册单例与上下文属性。

    返回 ``{"registered": [...], "missing": [...]}`` —— **缺哪个桥不影响启动**，
    缺失项会记 warning 并出现在返回值里，供测试与诊断使用。

    ## 为什么单例桥用"模块登记 + 上下文属性"，而不是 `qmlRegisterSingletonInstance`

    阶段 2 任务 2.8 报告的说法是"任何 `qmlRegister*` 之后同进程再编 `QtQuick.Controls`
    必现 `Cannot assign object to list property "data"`"。**我独立复核后把这个说法收窄了**
    （证据：`poc/_verify_register_hazard.py`、`_verify_register_signature.py`、
    `_verify_register_forms.py`、`_verify_register_subclass.py`，每个变体独立子进程）：

    | 调用 | 实测结果 |
    |------|----------|
    | 什么都不注册 | 最小 QML（Window + StackView）**可编译** |
    | `qmlRegisterType(...)` | **可编译** —— 所以"任何 `qmlRegister*` 都有害"**不成立** |
    | `qmlRegisterModule(...)` | 可编译 |
    | `setContextProperty(...)` | 可编译，且 QML 真能取到对象（实测读到 `probe-ok`） |
    | `qmlRegisterSingletonInstance(...)` / `qmlRegisterSingletonType(...)` | **只试出"抛错"这一种结果** —— 按 `PySide6/QtQml.pyi` 的签名 `(type_obj, uri, major, minor, qml_name, callback)` 传满 6 个参数（名字用 bytes、末参分别试过实例/`lambda: 实例`/`lambda eng, api: 实例`）都得到 `ValueError: wrong argument values`。**本次没能找到可用的调用形式。** |

    所以准确的结论是："**单例注册的可用调用形式在 PySide6 6.7.3 上没找到**"，而不是
    "注册函数有平台级缺陷"。既然目标是"QML 侧写法不变"，那就用实测可用的那两步：

    1. `qmlRegisterModule("FMCL", 1, 0)` —— 让 QML 里的 `import FMCL 1.0` 有东西可导入；
    2. `setContextProperty(name, obj)` —— `Theme.bgDark` / `Tr.map[…]` 的写法**一个字都不用改**。

    后续若有人要让 `Theme` / `Tr` 变成真正的 QML 单例类型，先把上面四种 6 参形式再试一遍
    （四个探针都是现成的），别照抄网上 5 参的写法 —— 那在 6.7.3 上一定抛 TypeError。
    """
    from PySide6.QtQml import qmlRegisterModule

    registered: List[str] = []
    missing: List[str] = []

    # 先把模块名登记上：QML 侧 `import FMCL 1.0` 才有东西可导入（实测无副作用）。
    try:
        qmlRegisterModule(QML_MODULE_URI, QML_MODULE_VERSION_MAJOR, 0)
    except Exception as e:  # noqa: BLE001 - 模块登记失败只影响 `import FMCL 1.0` 这句
        logger.warning("登记 QML 模块 %s 失败: %s", QML_MODULE_URI, e)

    for name, module_name, class_name in SINGLETON_BRIDGES + CONTEXT_BRIDGES:
        try:
            # 已经建好的实例优先（`Dialogs` 必须这样：同一个实例要同时喂给 QtUIPort 与 QML）
            if prebuilt and prebuilt.get(name) is not None:
                obj = prebuilt[name]
            else:
                obj = _instantiate(module_name, class_name)
        except Exception as e:  # noqa: BLE001 - 桥还没落地/依赖缺失都不该挡住启动
            logger.warning("桥接对象 %s 不可用（%s: %s）—— 相应功能将在 QML 侧缺席", name, module_name, e)
            missing.append(name)
            continue

        # 约定：桥若定义了 bind(context)，就在这里把 AppContext 交给它。
        bind = getattr(obj, "bind", None)
        if callable(bind):
            try:
                bind(context)
            except Exception as e:  # noqa: BLE001
                logger.warning("桥接对象 %s 的 bind(context) 失败: %s", name, e)

        # 约定：桥若需要 QML 引擎（`Theme` 要写 FluTheme），**显式注入**。
        # 桥自己不许去猜（缺陷 D-153 已把 `ThemeBridge` 里"从 `gc` 里取最后一个引擎"那条
        # 兜底整条删掉）：那种猜法会被"装配前创建过引擎的代码"（测试探针之类）带偏，
        # 把 FluTheme 注到错的引擎上，症状是根组件起不来且报错与主题毫不相干。
        # 这里注入的就是本函数刚建的那个引擎，`Theme` 拿到后会**当场**解析一次 FluTheme
        # （入口后面没有别的同步点，不这么做第一帧用的就是 FluentUI 的默认色）。
        use_engine = getattr(obj, "use_engine", None)
        if callable(use_engine):
            try:
                use_engine(engine)
            except Exception as e:  # noqa: BLE001
                logger.warning("桥接对象 %s 的 use_engine(engine) 失败: %s", name, e)

        try:
            engine.rootContext().setContextProperty(name, obj)
        except Exception as e:  # noqa: BLE001
            logger.error("注册 %s 失败: %s", name, e)
            missing.append(name)
            continue

        # 必须留一份强引用：`setContextProperty` / 单例注册都不接管所有权，
        # 对象被 GC 掉之后 QML 侧会拿到空壳（这类崩溃没有任何有用的报错）。
        engine._fmcl_bridges = getattr(engine, "_fmcl_bridges", {})
        engine._fmcl_bridges[name] = obj
        registered.append(name)

    return {"registered": registered, "missing": missing}


# ─── 应用级字体（任务 2.10） ───────────────────────────────────


def apply_theme_font(engine: Any) -> bool:
    """把 `Theme.fontFamily` 设成**应用级字体**（阶段 2 任务 2.10）。

    为什么在入口做而不是在桥里：`QGuiApplication.setFont()` 改的是进程级默认字体，
    属于"装配"而不是"主题状态"，而且必须在 `engine.load()` **之前**设好 ——
    否则先建出来的控件拿到的还是旧字体（QML 里没写 `font.family` 的控件全都受影响）。

    `Theme` 桥缺席（模块还没落地 / 注册失败）时**静默跳过**：字体只是观感，
    不该把启动挡住 —— 返回 False 只是给测试与诊断一个确定信号，不影响主流程。
    `Theme.fontFamily` 本身是 `LazyStr`，首次读它才会跑字体检测。

    Returns:
        是否真的把应用级字体设上了。
    """
    theme = getattr(engine, "_fmcl_bridges", {}).get("Theme")
    if theme is None:
        logger.debug("Theme 桥不在，跳过应用级字体设置")
        return False
    try:
        from PySide6.QtGui import QFont, QGuiApplication

        app = QGuiApplication.instance()
        if app is None:
            return False
        family = str(theme.property("fontFamily") or "")
        if not family:
            logger.warning("Theme.fontFamily 是空的，保持系统默认字体")
            return False
        # Linux 上 FONT_FAMILY 可能是 "Noto Color Emoji, Noto Sans CJK SC" 这样的**组合链**
        # （services/font_service.py 的原样行为），交给 setFamilies 处理：
        # 整串当一个 family 名是匹配不上的，会静默掉回系统默认字体。
        font = QFont()
        font.setFamilies([part.strip() for part in family.split(",") if part.strip()] or [family])
        app.setFont(font)
        logger.info("应用级字体已设为 %s", family)
        return True
    except Exception as e:  # noqa: BLE001 - 字体失败绝不能挡住启动
        logger.warning("设置应用级字体失败（保持系统默认字体）: %s", e)
        return False


# ─── AppContext 装配 ───────────────────────────────────────────


def build_qt_context(dispatcher: Any, dialog_host: Any = None) -> Any:
    """建 `AppContext`：Qt 版 UI 端口 + Qt 主线程调度器 + 默认服务表。"""
    from app.bootstrap import build_context
    from app.bridges.ui_port_qt import QtUIPort
    from config import config

    ui_port = QtUIPort(host=dialog_host)
    ui_port.start()
    ctx = build_context(config=config, ui=ui_port, scheduler=dispatcher.as_scheduler())
    try:
        ctx.start_all()
    except Exception as e:  # noqa: BLE001 - 服务启动失败绝不能挡住界面
        logger.error("服务启动失败: %s", e, exc_info=True)
    return ctx


def make_dialog_host(prebuilt: Optional[Dict[str, Any]] = None) -> Any:
    """取对话框宿主：**优先用已经建好的 `DialogBridge`**，否则退化为 `NullDialogHost`。

    为什么要有这个顺序（任务 2.14 接线时改的）：`QtUIPort` 在 `build_qt_context()` 里就要
    拿到宿主，而桥是在 `register_bridges()` 里才注册的 —— 如果那一步才建桥，端口手里就只剩
    `NullDialogHost`，**服务层的所有弹窗都会走静默降级**（`Dialogs` 在 QML 里看得见，
    但端口不知道它存在）。所以装配顺序改成：**先建 Dialogs 桥 → 喂给端口 → 再把同一个实例
    注册给 QML**。`qml/components/dialogs/README.md` 也记了这个坑。
    """
    if prebuilt and prebuilt.get("Dialogs") is not None:
        return prebuilt["Dialogs"]
    try:
        from app.bridges.dialog_host import NullDialogHost

        logger.warning("没有可用的 QML 对话框宿主，端口整体退化为 NullUIPort 行为")
        return NullDialogHost()
    except Exception as e:  # noqa: BLE001
        logger.warning("NullDialogHost 不可用（%s），端口走内置兜底", e)
        return None


# ─── 窗口提升（启动完成） ──────────────────────────────────────


def raise_main_window(engine: Any) -> bool:
    """把主窗口提到前面并抢焦点 —— 逐字对齐旧实现 `main.py` 的 `app.lift() + app.focus_force()`。

    ## 为什么需要它

    启动画面是 `WindowStaysOnTopHint` 的置顶窗（基线用的是 Tk 的 `-topmost`），而主窗口在
    启动期间是**藏起来的**（返工 A 组修掉的缺陷 D-142）。启动链条跑完之后，主窗口虽然显示了，
    但它不一定在别的窗口前面 —— 返工 A 组真机截图时就撞到过"界面在文件资源管理器后面"。

    ## 为什么在 Python 侧做，而不是写进 QML 的 `onVisibleChanged`

    offscreen 平台**不支持** `raise()`，会打一条 `QtWarningMsg: This plugin does not support
    raise()`，而 [P0] 冒烟测试把"未登记的 Qt 警告"当失败（第一版写在 QML 里，冒烟测试当场变红）；
    而且那一下在控件装载期就会触发。窗口提升本来就属于"装配 + 平台能力"，放在装配处最合适。

    ## 主线程前提（**踩过的坑**）

    `QWindow.raise_()` / `requestActivate()` 只能在 GUI 线程调。启动链条的信号是从
    **任务线程**发出来的（`StartupController.startPredownload` 里的 `tasks.submit`），
    直连的 Python lambda 会在那条线程上执行 —— 真机实测的表现是**进程静默消失**
    （窗口一直不出现、日志停在"启动器初始化完成"、没有任何 Python traceback）。
    所以调用方必须用 `MainThreadDispatcher` 兜一层（见 `assemble()` 里的接线），
    本函数另外自带一道判据：不在主线程就直接拒绝并记 warning，宁可不动窗口也不崩进程。

    Returns:
        是否真的调了（offscreen 或不在主线程时返回 False，测试据此断言分支被跳过）。
    """
    from PySide6.QtCore import QThread
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        return False
    try:
        # 判据与 `DialogBridge._on_main_thread` 一致：`==` 而不是 `is` ——
        # PySide6 会给同一个 C++ 线程对象包出不同的 Python 包装器，`is` 会误判。
        on_main = QThread.currentThread() == app.thread()
    except RuntimeError:  # C++ 对象已析构（进程收尾时可能出现）
        on_main = False
    if not on_main:
        logger.warning("raise_main_window 只能在主线程调用（当前不在主线程），已跳过")
        return False
    if QGuiApplication.platformName() == "offscreen":
        logger.debug("offscreen 平台不支持 raise()，跳过窗口提升")
        return False
    raised = False
    for obj in list(engine.rootObjects()):
        for name in ("raise_", "requestActivate"):
            method = getattr(obj, name, None)
            if callable(method):
                try:
                    method()
                    raised = True
                except Exception as e:  # noqa: BLE001 - 提不动就算了，不能挡住启动
                    logger.debug("窗口提升 %s() 失败: %s", name, e)
    return raised


# ─── 致命错误兜底 ──────────────────────────────────────────────


def show_fatal_error(title: str, message: str) -> None:
    """启动期致命错误：写 stderr + 尽力弹一个 QML 兜底窗。

    不引入 `QtWidgets`（`QMessageBox`）：一是与"界面全部走 QML"保持一致，
    二是 2.14 的启动错误路径本来就要走 QML，这里先把最小形态立起来。
    """
    text = f"{title}: {message}"
    print(text, file=sys.stderr)
    logger.error(text)
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtQml import QQmlApplicationEngine

        if QGuiApplication.instance() is None:
            return
        path = qml_import_path()
        if path.is_dir():
            engine = QQmlApplicationEngine()
            engine.addImportPath(str(path))
            root_ctx = engine.rootContext()
            root_ctx.setContextProperty("fatalTitle", title)
            root_ctx.setContextProperty("fatalMessage", message)
            engine.load(QUrl.fromLocalFile(str(app_qml_path("FatalError.qml"))))
    except Exception as e:  # noqa: BLE001 - 兜底再失败就只能靠 stderr 了
        logger.error("兜底错误窗也起不来: %s", e)


# ─── 主流程 ────────────────────────────────────────────────────


def create_application(argv: Optional[List[str]] = None) -> Any:
    """建 `QGuiApplication` 并设好引擎需要的环境变量（必须在建 app 之前设）。

    **已有实例则复用**：一个进程只能有一个 `QGuiApplication`，而测试进程里
    `tests/test_ui_port_qt.py` / `test_event_bridge.py` 等也会建它 —— 硬建第二个会
    直接抛 `RuntimeError`。生产路径上不会有实例，所以这只是把测试的共存做成确定的。
    """
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    from PySide6.QtGui import QGuiApplication

    existing = QGuiApplication.instance()
    if existing is not None:
        return existing

    app = QGuiApplication(list(sys.argv if argv is None else argv))
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    return app


def build_engine(import_path: Optional[Path] = None) -> Any:
    """建 QML 引擎并挂上 FluentUI 的导入路径。"""
    from PySide6.QtQml import QQmlApplicationEngine

    engine = QQmlApplicationEngine()
    path = import_path or qml_import_path()
    if path.is_dir():
        engine.addImportPath(str(path))
    else:
        logger.error("FluentUI QML 模块目录不存在: %s（先跑 scripts/build_fluentui.ps1）", path)
    return engine


class AlreadyRunning(RuntimeError):
    """已有实例在跑 —— `assemble()` 用它表示"本次应当退出"，而不是返回半个装配结果。"""


class Assembled(NamedTuple):
    """装配结果。**拆出来是为了可测**：`main()` 只负责"装配 + 进事件循环"，
    测试要能拿到 engine / context / 桥接结果自己断言，而不是只能看退出码。"""

    app: Any
    engine: Any
    context: Any
    dispatcher: Any
    guard: Any
    sink: QtMessageSink
    bridges: Dict[str, List[str]]


def assemble(
    argv: Optional[List[str]] = None,
    *,
    qml: Optional[str] = None,
    init_logging: bool = True,
    start_startup: bool = False,
) -> Assembled:
    """建 app / 引擎 / 上下文 / 桥接并加载根组件。**不进事件循环**。

    Args:
        start_startup: 是否立刻开跑启动流程（`StartupController.start()`）。
            **默认 False**，由 `main()` 在真的要进事件循环时传 True。

            为什么要有这个开关：启动流程会踢出后台任务（导入 launcher 核心、初始化成就引擎）。
            如果调用方装配完就立刻返回（测试、探针），进程会在这些导入还在飞行时进入解释器
            关闭阶段 —— 实测会得到
            `RuntimeError: can't register atexit after shutdown`（`concurrent.futures`
            在 `threading._register_atexit` 里抛）。这不是缺陷而是"没有事件循环却启动了
            异步流程"的必然结果，所以把"开跑"交给调用方显式决定。

    Raises:
        AlreadyRunning: 已有实例在跑（调用方应返回 0 退出）。
    """
    if init_logging:
        from main import setup_logging

        setup_logging()
    logger.info("=" * 60)
    logger.info("FMCL QML 版启动（阶段 2 骨架）")
    logger.info("=" * 60)

    app = create_application(argv)
    sink = install_message_handler()

    from config import config

    try:
        config.ensure_directories()
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(f"无法创建必要的目录: {e}") from e

    # 单实例：已有实例在跑就把请求交给它并退出（CLI 模式在 __main__ 里已经分流）。
    from app.bridges.single_instance import SingleInstance, default_key

    guard = SingleInstance(default_key(APP_NAME, str(config.base_dir)))
    if not guard.try_acquire():
        logger.info("已有一个实例在运行，本次启动已请它激活窗口，退出。")
        raise AlreadyRunning()

    engine = build_engine()
    from app.bridges.qt_dispatch import MainThreadDispatcher

    dispatcher = MainThreadDispatcher()

    # ── 装配顺序（任务 2.14 接线时定的）：先把对话框宿主建出来 ──
    # 同一个 `DialogBridge` 实例要喂给两处：`QtUIPort`（服务层弹窗的出口）与 QML
    # （`Dialogs` 上下文属性）。顺序反过来的话端口手里只剩 `NullDialogHost`，
    # 服务层的弹窗会**静默降级**，而 QML 侧看起来一切正常 —— 这种"两边都以为对方在管"
    # 的接线错误没有运行期报错，所以顺序在这里写死并加了注释。
    prebuilt: Dict[str, Any] = {}
    try:
        prebuilt["Dialogs"] = _instantiate("app.bridges.dialog_bridge", "DialogBridge")
    except Exception as e:  # noqa: BLE001 - 宿主缺席时端口走兜底，不挡住启动
        logger.warning("DialogBridge 不可用（%s），对话框将退化为无界面兜底值", e)

    context = build_qt_context(dispatcher, make_dialog_host(prebuilt))

    result = register_bridges(engine, context, prebuilt=prebuilt)
    logger.info("桥接注册完成：%s；缺失 %s", result["registered"], result["missing"])

    # 任务 2.10：中文字体注入（必须在 engine.load() 之前；桥缺席时静默跳过）。
    apply_theme_font(engine)

    # 任务 2.16：图标上色 provider（**必须**在 engine.load() 之前注册）。
    # 为什么需要它：`fill="currentColor"` 在 QtSvg 里被解析成**不透明黑**，而
    # `ColorOverlay` / `MultiEffect` 在 offscreen（本仓库所有测试的跑法）下静默失效
    # （阶段 2 任务 2.10 的实测结论）—— 唯一在两种环境下都精确的做法是
    # Python 侧换色：`image://fmcl-icon/<name>?color=%23RRGGBB`。
    # 没注册也不会白屏：`FmIcon` 会退化成未上色的 SVG（看得见但黑），
    # 并把 `fallbackUsed` 置真，Gallery 的自检会因此报警。
    try:
        from app.bridges.icon_provider import install as install_icon_provider

        install_icon_provider(engine)
    except Exception as e:  # noqa: BLE001 - 图标上色失败不该挡住启动
        logger.warning("注册图标 provider 失败（图标将退化为未上色）: %s", e)

    # ── 任务 2.14：启动流程控制器 ──
    # 挂在 QML 上是 `Startup`（启动画面的显示、协议/公告/预下载链条都由它驱动）。
    # `splash_expected` 必须与下面的 `start_startup` **同源**：它让 QML 在 `load()` 期
    # 就知道"启动画面要占屏"，主窗口因此从一开始就藏着，不会先闪一帧（缺陷 D-142）。
    startup = None
    try:
        from app.startup import StartupController

        startup = StartupController(
            context, ui_port=getattr(context, "ui", None), splash_expected=start_startup
        )
        engine.rootContext().setContextProperty("Startup", startup)
        engine._fmcl_bridges = getattr(engine, "_fmcl_bridges", {})
        engine._fmcl_bridges["Startup"] = startup
        result["registered"].append("Startup")
        # 把桥表回填给启动流程：启动后置任务要把成就总览交给首页桥（它注册得比这里早，
        # 构造期还看不到 Home）。见 `StartupController.set_bridges` 的说明。
        set_bridges = getattr(startup, "set_bridges", None)
        if callable(set_bridges):
            try:
                set_bridges(engine._fmcl_bridges)
            except Exception as e:  # noqa: BLE001
                logger.warning("回填桥表给 Startup 失败: %s", e)

        # ── 再注入一次引擎：`Startup` 是**刚**进桥表的 ──
        # `register_bridges()`（上面第 541 行）在这一步之前就跑完了，那时桥表里还没有
        # `Startup` —— 而 `HomeBridge.use_engine()` 正是**那一刻**去桥表里找 Startup 的
        # （它要接公告入口与启动链条的两个信号）。结果：`Home.hasNotice` 恒为 False、
        # 首页那个「查看公告」按钮**永远点不了**，而且启动链条跑完也不会刷首页。
        # 用户 2026-10-06 实测报的就是这个（"打开公告的按钮点不了"）。
        # 修法照搬 `tests/qml_home_probe.py` 里那条既有的正确顺序：Startup 进桥表之后
        # **再注入一次**（`use_engine` 本来就是幂等的：它只存引用 + 接信号）。
        for name in ("Home", "Versions"):
            obj = engine._fmcl_bridges.get(name)
            inject = getattr(obj, "use_engine", None)
            if not callable(inject):
                continue
            try:
                inject(engine)
            except Exception as e:  # noqa: BLE001 - 注不进只是那一页少一块信息
                logger.warning("第二次注入引擎给 %s 失败: %s", name, e)
        logger.info("已把 Startup 注入给需要它的桥（Home / Versions）")
    except Exception as e:  # noqa: BLE001 - 启动流程起不来也要能进界面（宁可没有启动画面）
        logger.error("StartupController 不可用（%s）—— 跳过启动画面与启动链条", e)

    # 把注册结果告诉 Runtime 桥：QML 侧据此给出"缺哪些桥"的人话提示，
    # 也让"接线到底通没通"变成可断言的（见 tests/test_main_qml_entry.py）。
    runtime = getattr(engine, "_fmcl_bridges", {}).get("Runtime")
    if runtime is not None:
        try:
            runtime.set_bridge_status({"registered": result["registered"], "missing": result["missing"]})
        except Exception as e:  # noqa: BLE001
            logger.warning("写入桥接状态失败: %s", e)

    from PySide6.QtCore import QUrl

    engine.load(QUrl.fromLocalFile(str(app_qml_path(qml or "App.qml"))))
    if not engine.rootObjects():
        raise RuntimeError("QML 根对象未能创建（看 latest.log 里的 [QML] 行）")

    # 把"第二个实例请求激活"转给窗口（2.12 的骨架会接上真正的前置逻辑）。
    guard.activateRequested.connect(lambda: logger.info("收到第二个实例的激活请求"))

    # ── 任务 2.14：把三条支线接起来（悬浮窗热键 + 启动流程 + 退出清理）──
    bridges = getattr(engine, "_fmcl_bridges", {})
    overlay = bridges.get("Overlay")
    hotkeys = bridges.get("Hotkeys")
    if overlay is not None and hotkeys is not None:
        bind = getattr(overlay, "bind_hotkeys", None)
        if callable(bind):
            try:
                # **只在这里接一次**：键位映射由桥负责（它认 monitor_toggle 之类的名字），
                # 事件来自 HotkeyBridge（非 Qt 全局热键 → 信号 → 主线程）。
                bind(hotkeys)
                # `ContextProperty` 只知道 Dart 名字，不知道 Python 信号；桥自己订阅。
                logger.info("悬浮窗全局热键已接线")
            except Exception as e:  # noqa: BLE001
                logger.warning("悬浮窗热键接线失败（热键不可用）: %s", e)

    # ── 启动画面收起 / 启动链条走完后，把主窗口提到前面（返工 A 组接线）──
    # 基线 `main.py` 的 `_on_app_ready` 结尾是两行：`app.lift()` + `app.focus_force()`；
    # 另外 `_dismiss_splash` 里那一下 `app.deiconify()` 在 Tk 上本身就会把窗口提到前面。
    # Qt 这边只 `show()` 不一定会激活（Windows 的前台窗口锁），真机实测会出现
    # "启动完成后界面在别的窗口后面" —— 所以两处都要提一次。
    #
    # **必须走 dispatcher**：`chainFinished` 是从任务线程发出来的（`tasks.submit`），
    # 直连的 Python lambda 会在那条线程上执行，而 `QWindow.raise_()` 只能在 GUI 线程调
    # —— 真机实测的表现是进程静默消失（窗口不出现、日志停在"初始化完成"、无 traceback）。
    if startup is not None:
        try:
            schedule = dispatcher.as_scheduler()

            def _bring_to_front() -> None:
                schedule(lambda: raise_main_window(engine))

            wired = []
            for signal_name in ("splashDismissed", "chainFinished"):
                signal = getattr(startup, signal_name, None)
                if signal is not None:
                    signal.connect(_bring_to_front)
                    wired.append(signal_name)
            logger.info("已接线『把主窗口提到前面』：%s", wired)
        except Exception as e:  # noqa: BLE001 - 提不动窗口不该挡住启动
            logger.warning("接线『启动完成提窗口』失败: %s", e)

    def _on_quit() -> None:
        """退出清理链（对照表 A-20，逐条对齐旧实现：
        插件 APP_SHUTDOWN → Toast 队列 → 音乐热键 → 监控热键 → 监控窗 → 音乐停止）。

        QML 侧能做的六步里，涉及本阶段落地的三样：启动流程停表、Toast 队列、
        悬浮窗（含监控/歌词窗与它们的热键）。插件与音乐那两步在阶段 3 的页面迁移里接。
        """
        logger.info("开始退出清理...")
        if startup is not None:
            try:
                startup.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("停止启动流程失败: %s", e)
        dialogs = bridges.get("Dialogs")
        if dialogs is not None:
            for slot_name in ("markClosing", "clearToasts"):
                slot = getattr(dialogs, slot_name, None)
                if callable(slot):
                    try:
                        slot()
                    except Exception as e:  # noqa: BLE001
                        logger.warning("%s 失败: %s", slot_name, e)
        if overlay is not None:
            shutdown = getattr(overlay, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown()
                except Exception as e:  # noqa: BLE001
                    logger.warning("关闭悬浮窗失败: %s", e)
        if hotkeys is not None:
            unregister = getattr(hotkeys, "unregister_all", None)
            if callable(unregister):
                try:
                    unregister()
                except Exception as e:  # noqa: BLE001
                    logger.warning("注销全局热键失败: %s", e)
        logger.info("退出清理完成")

    try:
        app.aboutToQuit.connect(_on_quit)
    except Exception as e:  # noqa: BLE001
        logger.warning("挂退出清理失败: %s", e)

    # ── 启动流程开跑（必须在 engine.load() **之后**：协议/公告弹窗要靠 QML 侧的
    #    StartupDialogs 显示；顺序反了的话第一批信号没人接）──
    if startup is not None and start_startup:
        try:
            startup.start()
        except Exception as e:  # noqa: BLE001 - 启动流程失败不该让程序起不来
            logger.error("启动流程启动失败: %s", e, exc_info=True)
    elif startup is not None:
        logger.info("assemble(start_startup=False)：启动流程已就绪但未开跑（测试/探针路径）")

    return Assembled(app, engine, context, dispatcher, guard, sink, result)


def main(
    argv: Optional[List[str]] = None,
    *,
    run_loop: bool = True,
    qml: Optional[str] = None,
    init_logging: bool = True,
) -> int:
    """装配并（默认）进入事件循环。返回退出码。

    Args:
        run_loop: False 时只装配、不进入事件循环（测试用）。
        qml: 覆盖根组件文件名（默认 `App.qml`）。
        init_logging: 是否调用 `main.setup_logging()`。测试传 False，
            避免测试去改写仓库里的 `latest.log`。
    """
    try:
        # run_loop=True 才开跑启动流程（见 assemble 的 start_startup 说明）
        built = assemble(argv, qml=qml, init_logging=init_logging, start_startup=run_loop)
    except AlreadyRunning:
        return 0
    except Exception as e:  # noqa: BLE001 - 启动期任何异常都要有可见出口
        logger.exception("启动失败")
        show_fatal_error("启动失败", str(e))
        return 1

    if not run_loop:
        return 0
    code = built.app.exec()
    logger.info("QML 版退出，code=%d，QML 消息 %d 条", code, len(built.sink.messages))
    return int(code)


if __name__ == "__main__":
    # CLI 模式与旧入口**完全一致**：直接复用 main.py 的解析与实现，
    # 避免出现第二份 CLI 语义（迁移红线 2）。
    from main import _parse_cli_args, run_agent_cli_mode, run_login_mode

    _mode, _payload = _parse_cli_args()
    if _mode == "login" and isinstance(_payload, tuple):
        run_login_mode(_payload[0], _payload[1])
    elif _mode == "agent":
        run_agent_cli_mode(_payload)
    else:
        sys.exit(main())
