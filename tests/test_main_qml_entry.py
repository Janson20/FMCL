"""阶段 2 任务 2.2（QML 版入口装配）的回归守卫。

## 这一组测试想钉住什么

入口是最容易"看起来能跑但其实没接上"的地方 —— 一个 `QQmlApplicationEngine`
只要 QML 文件语法正确就返回 0，哪怕里面一个桥都没注册、一个属性都是空的。
所以这里断言的都不是"没崩"，而是**接线本身**：

1. 引擎挂上了 FluentUI 的 QML 导入路径（阶段 0 的产物，缺了它 FluentUI 控件全废）；
2. 根组件真的建出来了，并且**能读到 Python 侧写进去的值**（`Runtime.bridgeStatus`）；
3. 桥接注册结果如实反映出"哪些桥还没落地"——B/C/D 组并行开发期间，
   这条信息必须是**可见**的，否则会退化成白屏
   （阶段 0 第 13.6 节的教训：环境/接线结论必须用最小用例独立验证，不能靠观感）；
4. 主线程调度器语义与旧界面的 `after(0, ...)` 等价（任意线程可调、一定排队）；
5. 单实例守卫：第二个进程**不会**再起一套界面，并且会把请求发给第一个；
6. **CLI 语义没有第二份实现** —— 入口必须复用 `main.py` 的解析与实现（迁移红线 2）。

## 环境前提

FluentUI 的 QML 模块来自阶段 0 的自编译产物（`third_party/_install/qml`），
它是构建产物、不入库。**缺它时这一组整体跳过**（而不是失败）：干净克隆上
本文件的断言没有意义，跑之前得先 `scripts/build_fluentui.ps1`。
"""

from __future__ import annotations

import ast
import os
import sys
import threading
from pathlib import Path

import pytest

# 必须在任何 PySide6 import 之前设好：入口自己也会 setdefault，但测试进程里
# 可能已经有别的 Qt 测试先把 app 建起来了。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

FLUENT_DIR = REPO_ROOT / "third_party" / "_install" / "qml" / "FluentUI"

pytestmark = pytest.mark.skipif(
    not FLUENT_DIR.is_dir(),
    reason=f"FluentUI QML 模块未构建（{FLUENT_DIR}）—— 先跑 scripts/build_fluentui.ps1",
)


@pytest.fixture(scope="module")
def assembled():
    """装配一次，整组复用（QGuiApplication 每进程只能有一个）。

    **必须保存并还原进程级"当前上下文"**：`assemble()` 走的是生产路径，
    `build_context()` 会调 `AppContext.set_current(ctx)`。测试进程里其它模块
    （`test_server_service` / `test_voice_input` 等）依赖 `AppContext.current()`
    来断言"服务被缓存 / 优先用 owner.context"，被我们改掉之后就会全量跑时红、
    单独跑绿 —— 这正是阶段 1 踩过的"测试间状态污染"。
    """
    import main_qml
    from app.context import AppContext

    previous = AppContext.current()
    built = main_qml.assemble([], init_logging=False)
    try:
        yield built
    finally:
        try:
            built.guard.release()
        except Exception:  # noqa: BLE001 - 清理失败不影响断言结论
            pass
        AppContext.set_current(previous)


# ─── 1. 导入路径 ────────────────────────────────────────────────


def test_qml_import_path_points_at_the_compiled_module():
    import main_qml

    path = main_qml.qml_import_path()
    assert path.is_dir(), f"导入路径不存在: {path}"
    assert (path / "FluentUI").is_dir(), f"导入路径下没有 FluentUI 模块: {path}"


def test_engine_has_the_fluent_import_path(assembled):
    import main_qml

    # Qt 会把导入路径归一化成正斜杠，所以按 Path 比，不按原始字符串比。
    expected = main_qml.qml_import_path().resolve()
    listed = {Path(p).resolve() for p in assembled.engine.importPathList() if p and not p.startswith("qrc:")}
    assert expected in listed, (
        f"引擎没有挂上 FluentUI 的导入路径（期望 {expected}，实际 {sorted(str(p) for p in listed)}）"
        " —— QML 里 import FluentUI 会直接失败"
    )


def test_fluentui_types_are_actually_loadable(assembled):
    """光有路径不算数：真要能 import 到 FluWindow 这个类型。

    用一个一次性 QML 片段把 `FluWindow` 实例化出来（不加载根组件），
    这样"模块在但类型坏掉"（例如 Qt 版本不匹配、插件 DLL 缺失）会被抓住。
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent

    src = b"import QtQuick\nimport FluentUI\nFluWindow { width: 10; height: 10 }\n"
    component = QQmlComponent(assembled.engine)
    component.setData(src, QUrl.fromLocalFile(str(REPO_ROOT / "qml" / "_probe_fluent.qml")))
    assert component.isReady(), f"FluWindow 无法实例化: {[e.toString() for e in component.errors()]}"
    obj = component.create()
    assert obj is not None, "FluWindow 创建返回 None"
    obj.deleteLater()


# ─── 2. 根组件 + 接线 ───────────────────────────────────────────


def test_root_object_is_created(assembled):
    roots = assembled.engine.rootObjects()
    assert len(roots) == 1, f"根对象数量异常: {len(roots)}"
    # 根对象就是 App.qml 的 Window（类名形如 App_QMLTYPE_2），不是别的组件
    assert "App" in roots[0].metaObject().className(), (
        f"根对象类名不像 App.qml 的 Window: {roots[0].metaObject().className()}"
    )


def test_no_qml_binding_errors_at_process_exit(tmp_path):
    """**进程退出**时不能出现 QML 绑定错误（这一条是实测出来的缺陷）。

    现场：解释器关闭时上下文属性先被清掉，而 `Window.title` 绑定的求值还排在队列里，
    于是 stderr 刷出 `TypeError: Cannot read property 'appName' of null`。
    它不影响功能，但会污染日志，并且会让"日志里有没有 QML 报错"这条判据
    （任务 2.19 的冒烟测试要用）**永远为真** —— 那就等于没有判据。
    修法是 `App.qml` 里每个 `Runtime` 绑定都判空。

    ## 两个必须写下来的坑（都是我自己踩的）

    1. **必须用子进程**：缺陷只在"解释器关闭"这个顺序下出现。第一版写成
       "本进程里 `shiboken6.delete(engine)` + 泵事件循环"，去掉判空后**测试依然绿** ——
       那版是**空断言**（`delete` 会立刻销毁根对象，走不到关闭路径）。
    2. **子进程的工作目录要换掉**：`config.base_dir` 在 Windows 上就是当前目录，
       而单实例键由它派生 —— 不换目录的话子进程会撞上本测试进程持有的单实例，
       走到 `AlreadyRunning` 就退出了，同样是个空断言。这里用 `tmp_path` 绕开。
    3. **先有 `QGuiApplication`**：没有它就建 `QQmlApplicationEngine` 会直接崩
       （实测 `exit=3221226505` / `STATUS_STACK_BUFFER_OVERRUN`，连 stdout 都来不及刷）。

    非空断言的证据：把 `App.qml` 两处判空都去掉后，这条测试报红并给出
    `App.qml:31`（runtimeLabel）+ `App.qml:69`（bridgeStatus）两条 TypeError，
    见 `poc/_probe_qml_shutdown_full.py --no-guard` 的实测输出。
    """
    import subprocess

    code = (
        "import os, sys;"
        "os.environ['QT_QPA_PLATFORM']='offscreen';"
        "sys.path.insert(0, r'{root}');"
        "import main_qml;"
        "built = main_qml.assemble([], init_logging=False);"
        "root = built.engine.rootObjects()[0];"
        "print('ROOTS=' + str(len(built.engine.rootObjects())), flush=True);"
        "print('TITLE=' + repr(root.property('title')), flush=True);"
        "print('READY', flush=True)"
    ).format(root=REPO_ROOT)

    out = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        capture_output=True,
        text=True,
        # **必须显式给 encoding + errors**：不带它时 subprocess 用本地码页（本机 gbk）
        # 解码子进程输出，遇到 UTF-8 字节就在 reader 线程抛 UnicodeDecodeError，
        # `out.stderr` 直接变成 None。这正是阶段 1 给 `services/` 修过 13 处的同族缺陷
        # ——我自己写这条测试时又踩了一次。
        encoding="utf-8",
        errors="replace",
        cwd=str(tmp_path),  # 换个 base_dir，避开本进程持有的单实例
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    combined = out.stdout + out.stderr
    assert "READY" in out.stdout, f"子进程没有跑到结束: stdout={out.stdout!r} stderr={out.stderr!r}"
    assert "ROOTS=1" in out.stdout, "根组件没建出来，后面的退出断言没有意义"
    assert "TITLE='" in out.stdout, "title 绑定没生效，后面的退出断言没有意义"
    bad = [ln.strip() for ln in combined.splitlines() if "TypeError" in ln or "of null" in ln]
    assert bad == [], f"进程退出阶段出现 QML 绑定错误（App.qml 的绑定缺少判空？）: {bad}"


def test_qml_settings_context_property_is_visible_from_qml(assembled):
    """**接线证据**：QML 侧能读到 Python 写进去的值。

    这比"根对象存在"强得多 —— 根对象存在只说明 QML 语法对，
    而能读到 `Runtime.bridgeStatus` 才说明上下文属性真的注入了。
    """
    from PySide6.QtCore import QObject

    root = assembled.engine.rootObjects()[0]
    label = root.findChild(QObject, "bridgeStatusText")
    assert label is not None, "找不到 bridgeStatusText —— App.qml 的结构变了？"
    text = label.property("text")
    assert isinstance(text, str) and text, "bridgeStatus 是空的：Runtime 桥没注进去或没写状态"
    assert "bridges" in text


def test_bridge_status_reports_the_real_registry_state(assembled):
    """注册结果必须**如实**反映现状：缺的桥要出现，成功的桥也要出现。

    阶段 2 是四条支线并行落的，缺桥是常态；关键是这个事实**可见**。
    断言写成"与入口自己返回的列表一致"，而不是写死一份名单 ——
    否则 B/C 组每补一个桥都得来改这个测试（那种测试会被人绕过）。
    """
    from PySide6.QtCore import QObject

    root = assembled.engine.rootObjects()[0]
    label = root.findChild(QObject, "bridgeStatusText")
    text = label.property("text")

    registered = assembled.bridges["registered"]
    missing = assembled.bridges["missing"]

    assert "Runtime" in registered, "Runtime 桥是入口自己注册的，必须成功"
    for name in missing:
        assert name in text, f"缺失的桥 {name} 没有出现在状态提示里: {text!r}"


def test_no_qml_errors_during_load(assembled):
    """加载期不许有 QML 错误。

    `QtMessageSink` 把 Qt 消息收进列表（入口已经装好 handler）。
    只挑真正是"错误"的行，避免把 `qt.qpa` 之类平台提示算进来。
    """
    fatal = [m for m in assembled.sink.messages if "is not defined" in m or "Unable to assign" in m]
    assert fatal == [], f"加载根组件时出现 QML 错误: {fatal}"


def test_runtime_icon_url_is_callable_from_qml(assembled):
    """`Runtime.iconUrl()`（阶段 2 任务 2.10）必须真的能被 QML 调到，且结果是能加载的图标。

    为什么要在**装配好的引擎**上测：图标路径只能由 Python 侧拼（开发态与打包态不同），
    而"槽存不存在 / 名字对不对 / 上下文属性接上没有"这三件事都只有真跑一次才知道。

    为什么断言到像素尺寸：QML `Image.source` 收到裸路径（`D:/…/check.svg`）会当成
    **相对当前 QML 文件的相对路径**去解析，结果是 `Image.Error` + `implicitWidth == 0`
    **且不报错**。只断言"返回非空字符串"就是空断言。
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlComponent
    # 必须先 import QtQuick：否则 create() 只返回 QWindow 壳（没有 contentItem），实测踩过。
    from PySide6.QtQuick import QQuickWindow  # noqa: F401

    src = (
        b"import QtQuick\n"
        b"import QtQuick.Window\n"
        b"Window {\n"
        b"    width: 10; height: 10; visible: false\n"
        b'    Image { objectName: "iconProbe"; source: Runtime.iconUrl("check")\n'
        b"            sourceSize.width: 24; sourceSize.height: 24 }\n"
        b"}\n"
    )
    component = QQmlComponent(assembled.engine)
    component.setData(src, QUrl.fromLocalFile(str(REPO_ROOT / "qml" / "_probe_icon.qml")))
    assert component.isReady(), f"探针 QML 不 ready: {[e.toString() for e in component.errors()[:3]]}"
    window = component.create()
    assert window is not None, "探针 QML 创建失败"

    # 不用 window.findChild：实测它找不到 Repeater/内联子项里的 QQuickItem，从 contentItem 往下走
    found = []

    def walk(item):
        for child in item.childItems():
            if child.objectName() == "iconProbe":
                found.append(child)
            walk(child)

    walk(window.contentItem())
    assert found, "QML 里没有建出 iconProbe 这个 Image"
    image = found[0]
    assert image.property("implicitWidth") == 24, (
        f"Runtime.iconUrl(\"check\") 的结果加载不出来（implicitWidth="
        f"{image.property('implicitWidth')}）—— 返回的必须是可以直接用的 file URL"
    )
    window.deleteLater()


def test_duplicate_bridges_are_kept_alive(assembled):
    """桥对象必须留强引用。

    修掉的是真实崩溃模式：`setContextProperty` / 单例注册**不接管所有权**，
    桥被 GC 掉之后 QML 侧拿到空壳，而且不会有任何有用报错。
    """
    kept = getattr(assembled.engine, "_fmcl_bridges", {})
    assert set(kept) == set(assembled.bridges["registered"])
    for name, obj in kept.items():
        assert obj is not None, f"{name} 的强引用是 None"


# ─── 2.5 Runtime 桥的自查（任务 2.19 的冒烟测试发现的一处真缺陷）────────


def test_runtime_reports_the_real_app_version():
    """`Runtime.appVersion` 必须是真版本号，**不能**恒为 `unknown`。

    这条是任务 2.19 的冒烟测试抓出来的：`runtime_bridge._app_version()` 原先写的是
    `from services.user_agent import get_fmcl_version`，而那个模块里实际叫
    `_get_fmcl_version` —— ImportError 被兜底的 `except` 吞掉后恒返回 `"unknown"`，
    于是窗口标题一直是 `FMCL unknown`。

    这类错误之所以能潜伏：兜底值本身是**合法字符串**（不是空串、不是 None），
    界面上"看起来正常"。所以判据不能是"非空"，必须是"**等于服务层的真版本**"。
    """
    from app.bridges.runtime_bridge import _app_version
    from services.user_agent import _get_fmcl_version

    real = str(_get_fmcl_version())
    assert real and real != "unknown", "服务层自己都拿不到版本，这条断言失去意义"
    assert _app_version() == real, f"Runtime.appVersion 报的是 {_app_version()!r}，真值是 {real!r}"


def test_runtime_version_property_is_wired(assembled):
    """从**装配好的** Runtime 桥上再读一次（属性层也要通，不只是那个私有函数）。"""
    from services.user_agent import _get_fmcl_version

    runtime = getattr(assembled.engine, "_fmcl_bridges", {}).get("Runtime")
    assert runtime is not None, "Runtime 桥不在强引用表里"
    assert runtime.property("appVersion") == str(_get_fmcl_version())
    assert "unknown" not in str(runtime.title() if hasattr(runtime, "title") else "")


# ─── 3. 主线程调度器 ────────────────────────────────────────────


def test_dispatcher_queues_same_thread_calls(assembled):
    """同线程提交也**必须排队**（与 `after(0, ...)` 语义一致，不是立即执行）。"""
    seen = []
    assembled.dispatcher.submit(lambda: seen.append(threading.get_ident()))
    assert seen == [], "同线程提交被同步执行了 —— 语义与 after(0, ...) 不一致"
    assembled.app.processEvents()
    assert seen == [threading.get_ident()]


def test_dispatcher_marshals_worker_thread_calls(assembled):
    """worker 线程提交的回调必须在主线程执行（红线 3 的基础设施）。"""
    main_ident = threading.get_ident()
    result = {}
    done = threading.Event()

    def worker():
        assembled.dispatcher.submit(lambda: (result.update(ident=threading.get_ident()), done.set()))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    t.join(timeout=5)
    for _ in range(200):
        assembled.app.processEvents()
        if done.is_set():
            break
    assert done.is_set(), "worker 提交的回调没有被主线程执行（事件循环没被驱动？）"
    assert result["ident"] == main_ident, "回调执行在 worker 线程 —— 会直接崩 Qt"


def test_dispatcher_swallows_callback_exceptions(assembled):
    """回调抛异常不能掀翻事件循环。"""
    called = []

    def boom():
        called.append(1)
        raise ValueError("故意炸")

    assembled.dispatcher.submit(boom)
    assembled.app.processEvents()
    assert called == [1]
    assert assembled.dispatcher.describe()["failures"] >= 1


# ─── 4. 单实例 ──────────────────────────────────────────────────


def test_single_instance_blocks_a_second_acquire():
    from app.bridges.single_instance import SingleInstance

    key = "fmcl-test-single-instance-2-2"
    first = SingleInstance(key)
    assert first.try_acquire() is True
    try:
        second = SingleInstance(key)
        assert second.try_acquire() is False, "第二个实例竟然也拿到了主实例身份"
    finally:
        first.release()
    # 释放之后又能拿回来 —— 证明 release 真的把服务器交还了
    third = SingleInstance(key)
    assert third.try_acquire() is True
    third.release()


def test_single_instance_notifies_the_primary(assembled):
    """第二个实例的启动请求要真的到达主实例（否则"提到前面"永远不触发）。"""
    from app.bridges.single_instance import SingleInstance

    key = assembled.guard.key
    hits = []
    assembled.guard.activateRequested.connect(lambda: hits.append(1))

    second = SingleInstance(key)
    assert second.try_acquire() is False
    for _ in range(100):
        assembled.app.processEvents()
        if hits:
            break
    assert hits, "主实例没有收到激活请求"
    assert assembled.guard.client_count >= 1


def test_single_instance_key_depends_on_data_dir():
    """不同数据目录 = 不同实例（用户复制一份数据目录开第二个是常见用法，不能禁掉）。"""
    from app.bridges.single_instance import default_key

    assert default_key("FMCL", "C:/a") != default_key("FMCL", "C:/b")
    assert default_key("FMCL", "C:/a") == default_key("FMCL", "C:/a")


# ─── 5. CLI 语义只有一份实现 ────────────────────────────────────


def test_entry_reuses_main_cli_instead_of_reimplementing_it():
    """迁移红线 2：入口不许出现第二份 CLI 实现。

    判据用 AST 而不是 grep：要求 `main_qml.py` 里**没有** `argparse` 导入、
    并且真的从 `main` 导入了 `_parse_cli_args`。
    """
    src = (REPO_ROOT / "main_qml.py").read_text(encoding="utf-8")
    tree = ast.parse(src)

    imported_from_main = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "main":
            imported_from_main.update(a.name for a in node.names)
        if isinstance(node, ast.Import):
            for a in node.names:
                assert a.name != "argparse", "入口自己引入了 argparse —— CLI 语义要复用 main.py"

    assert "_parse_cli_args" in imported_from_main, "入口没有复用 main.py 的 CLI 解析"
    for needed in ("run_login_mode", "run_agent_cli_mode"):
        assert needed in imported_from_main, f"入口没有复用 main.py 的 {needed}"


def test_entry_does_not_import_the_tk_ui_package():
    """契约决策 12：阶段 2 的运行时不 import `ui/`（它会拉起 customtkinter）。

    用子进程真跑一次 import，而不是只 grep —— 间接导入链才是真正会出事的地方。
    """
    import subprocess

    code = (
        "import sys, os;"
        "os.environ['QT_QPA_PLATFORM']='offscreen';"
        "sys.path.insert(0, r'%s');"
        "import main_qml;"
        "bad=[m for m in sys.modules if m=='ui' or m.startswith('ui.')];"
        "print('UI_MODULES=' + ','.join(sorted(bad)))" % REPO_ROOT
    )
    out = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    assert out.returncode == 0, f"导入入口失败: {out.stderr}"
    line = [ln for ln in out.stdout.splitlines() if ln.startswith("UI_MODULES=")][0]
    assert line == "UI_MODULES=", f"入口把 Tk 界面包拖进来了: {line}"


def test_main_returns_zero_without_event_loop():
    """`main(run_loop=False)` 的端到端退出码（装配成功就是 0）。"""
    import main_qml

    # 单实例已被 fixture 占着 —— 这次启动应走"已经有实例在跑"的分支，同样是 0
    assert main_qml.main([], run_loop=False, init_logging=False) == 0


# ─── 启动完成后的窗口提升（返工 A 组） ─────────────────────────


class _FakeWindow:
    """记账用的假窗口：只关心 raise_ / requestActivate 有没有被调。"""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[str] = []
        self._fail = fail

    def raise_(self):  # noqa: N802 - Qt 命名
        if self._fail:
            raise RuntimeError("平台不支持 raise()")
        self.calls.append("raise_")

    def requestActivate(self):  # noqa: N802
        self.calls.append("requestActivate")


class _FakeEngineForRaise:
    def __init__(self, window) -> None:
        self._window = window

    def rootObjects(self):  # noqa: N802
        return [self._window]


def test_raise_main_window_is_skipped_on_offscreen():
    """offscreen 平台必须**跳过**窗口提升 —— 缺陷 D-142 的连带修复。

    基线 `main.py` 的 `_on_app_ready` 结尾是 `app.lift() + app.focus_force()`，本实现逐字对齐；
    但 offscreen 平台不支持 raise()，会打一条 `QtWarningMsg: This plugin does not support raise()`，
    而 [P0] 冒烟测试把"未登记的 Qt 警告"当作失败 —— 第一版把它写在 QML 的
    `onVisibleChanged` 里，冒烟测试当场变红（那次实测值 183/183 → 1 条失败）。
    """
    import main_qml
    from PySide6.QtGui import QGuiApplication

    assert QGuiApplication.platformName() == "offscreen", "本仓库的 Qt 测试都在 offscreen 下跑"
    window = _FakeWindow()
    assert main_qml.raise_main_window(_FakeEngineForRaise(window)) is False
    assert window.calls == [], "offscreen 下不该调 raise_/requestActivate"


def test_raise_main_window_calls_both_methods_on_a_real_platform(monkeypatch):
    """真平台（这里用假平台名模拟）上要**两个都调**，且单个失败不影响另一个。"""
    import main_qml
    from PySide6.QtGui import QGuiApplication

    monkeypatch.setattr(QGuiApplication, "platformName", staticmethod(lambda: "windows"))
    window = _FakeWindow()
    assert main_qml.raise_main_window(_FakeEngineForRaise(window)) is True
    assert window.calls == ["raise_", "requestActivate"]

    # raise_ 抛异常时：requestActivate 照常走完，异常不许冒出去（提不动窗口不能挡住启动）
    broken = _FakeWindow(fail=True)
    assert main_qml.raise_main_window(_FakeEngineForRaise(broken)) is True
    assert broken.calls == ["requestActivate"]


def test_raise_main_window_refuses_off_the_main_thread(monkeypatch):
    """**非主线程必须拒绝**（返工 A 组真机踩到的静默崩溃）。

    启动链条的 `chainFinished` 是从任务线程发出来的，直连的 Python lambda 会在那条线程上跑，
    而 `QWindow.raise_()` 只能在 GUI 线程调 —— 真机表现是**进程静默消失**：窗口一直不出现、
    日志停在"启动器初始化完成"、没有任何 traceback。所以这里把"拒绝"变成判据。
    """
    import threading

    import main_qml
    from PySide6.QtGui import QGuiApplication

    monkeypatch.setattr(QGuiApplication, "platformName", staticmethod(lambda: "windows"))
    window = _FakeWindow()
    engine = _FakeEngineForRaise(window)
    result = {}

    def worker() -> None:
        result["raised"] = main_qml.raise_main_window(engine)

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=10)
    assert result.get("raised") is False, "非主线程竟然去动窗口了 —— 这会静默崩掉进程"
    assert window.calls == [], "非主线程上调了 raise_/requestActivate"
