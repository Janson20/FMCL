"""QML 版入口 —— 程序装配（阶段 2 任务 2.2）。

## 它做什么、不做什么

**做**：QML 引擎、QML 导入路径、桥接对象注册、`AppContext` 装配、单实例、
致命错误兜底窗口、CLI 模式透传。

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


def register_bridges(engine: Any, context: Any) -> Dict[str, List[str]]:
    """注册单例与上下文属性。

    返回 ``{"registered": [...], "missing": [...]}`` —— **缺哪个桥不影响启动**，
    缺失项会记 warning 并出现在返回值里，供测试与诊断使用。
    """
    from PySide6.QtQml import qmlRegisterSingletonInstance

    registered: List[str] = []
    missing: List[str] = []

    for name, module_name, class_name in SINGLETON_BRIDGES + CONTEXT_BRIDGES:
        try:
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

        try:
            if (name, module_name, class_name) in SINGLETON_BRIDGES:
                qmlRegisterSingletonInstance(QML_MODULE_URI, QML_MODULE_VERSION_MAJOR, 0, name, obj)
            else:
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


def make_dialog_host() -> Any:
    """2.13 之前没有 QML 宿主，用 `NullDialogHost`（端口整体退化为兜底值）。"""
    try:
        from app.bridges.dialog_host import NullDialogHost

        return NullDialogHost()
    except Exception as e:  # noqa: BLE001
        logger.warning("NullDialogHost 不可用（%s），端口走内置兜底", e)
        return None


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
) -> Assembled:
    """建 app / 引擎 / 上下文 / 桥接并加载根组件。**不进事件循环**。

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
    context = build_qt_context(dispatcher, make_dialog_host())

    result = register_bridges(engine, context)
    logger.info("桥接注册完成：%s；缺失 %s", result["registered"], result["missing"])

    # 把注册结果告诉 Runtime 桥：QML 侧据此给出"缺哪些桥"的人话提示，
    # 也让"接线到底通没通"变成可断言的（见 tests/test_main_qml_entry.py）。
    runtime = getattr(engine, "_fmcl_bridges", {}).get("Runtime")
    if runtime is not None:
        try:
            runtime.set_bridge_status(result)
        except Exception as e:  # noqa: BLE001
            logger.warning("写入桥接状态失败: %s", e)

    from PySide6.QtCore import QUrl

    engine.load(QUrl.fromLocalFile(str(app_qml_path(qml or "App.qml"))))
    if not engine.rootObjects():
        raise RuntimeError("QML 根对象未能创建（看 latest.log 里的 [QML] 行）")

    # 把"第二个实例请求激活"转给窗口（2.12 的骨架会接上真正的前置逻辑）。
    guard.activateRequested.connect(lambda: logger.info("收到第二个实例的激活请求"))

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
        built = assemble(argv, qml=qml, init_logging=init_logging)
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
