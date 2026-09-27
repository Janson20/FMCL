"""启动画面可见性走查（界面返工 A 组 / 缺陷 D-142）—— 子进程探针。

## 为什么要单起一个进程跑真 QML

`FluWindow` 在同一进程里开第二个引擎会以 `Windows fatal exception: access violation` 收场
（阶段 2 实测，见 `tests/qml_shell_probe.py` 的文件头），所以"真的加载 `App.qml`"
这件事只能放在子进程里做。本探针同 `qml_shell_probe.py` 的约定：
把观察结果以 `PROBE_JSON:{...}` 单行打印出来，由 `tests/test_startup_visibility.py` 断言。

## 它验证的是**用户报上来的那个现象**

> 启动时的加载窗在加载完后没消失。

所以判据必须是"跑完真实启动时序之后，两个窗口的可见性各自正确"，而不是"某一行代码里有绑定"：

1. `start_startup=True` 装配之后（启动流程开跑的那一刻）：
   启动画面**必须可见**、主窗口**必须不可见**；
2. 初始化就绪 + 过了最小显示时长之后：
   启动流程收尾（`dismissed=True`）→ 启动画面**必须先淡出再隐藏**、主窗口**必须可见**；
3. 整个过程里 QML 消息里**不许有绑定错误**（旧版 `visible: true` 与新版绑定的判空都在这条里）。

## 哪些东西被换成了空实现（以及为什么）

* 启动器核心 / 成就引擎的初始化 → 立即返回的对象（真初始化要导入 launcher 与成就引擎，
  会把探针拖到几秒，而且会踢出一堆后台任务）；
* 公告拉取 / 预下载检查 / 协议检查 → 空实现：它们会读真实 `config.json`、发网络请求、
  并通过 `UIPort` 弹模态框。**协议与预下载链条的正确性由 `tests/test_startup.py` 负责**，
  本探针只判"窗口可见性"这一件事，不该被网络与弹窗搅进来。

用法：

    uv run python tests/qml_startup_probe.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QObject  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import main_qml  # noqa: E402
from app import startup as startup_module  # noqa: E402

MARKER = "PROBE_JSON:"

#: 观测时间点（毫秒）。最小显示时长是 1 秒，轮询周期 200ms。
OBSERVE_MS = 250
#: 从装配到"应当已经收尾"的等待：1 秒最小显示 + 200ms 轮询 + 余量。
SETTLE_MS = 1800
#: 收尾之后留给淡出动画（300ms）+ 兜底定时器（+80ms）的时间。
FADE_MS = 600


def isolate_single_instance() -> None:
    """单实例守卫的键里带上 PID —— 与本仓库其它探针同一套隔离（见 _smoke_driver.py）。"""
    from app.bridges import single_instance

    original = single_instance.default_key
    marker = f"-startup-probe-{os.getpid()}"

    def patched(app_name: str, data_dir: str) -> str:
        return original(app_name, data_dir) + marker

    single_instance.default_key = patched  # type: ignore[assignment]


def patch_startup_defaults() -> None:
    """把初始化与启动链条换成空实现（理由见文件头）。"""
    ctl = startup_module.StartupController
    ctl._default_launcher_factory = staticmethod(lambda: QObject())  # type: ignore[assignment]
    ctl._default_achievements_factory = staticmethod(lambda: QObject())  # type: ignore[assignment]
    ctl._default_notice_fetcher = staticmethod(lambda: None)  # type: ignore[assignment]
    ctl._default_predownload_runner = lambda self: None  # type: ignore[assignment]
    ctl._check_agreement = lambda self: None  # type: ignore[assignment]


def window_state(startup: Any, splash: Any, root: Any, sink: Any) -> Dict[str, Any]:
    """某一时刻的完整观察快照。"""
    status_text = ""
    card = root.findChild(QObject, "splashStatusText")
    if card is not None:
        status_text = str(card.property("text"))
    transient = None
    try:
        transient = splash.transientParent()
    except Exception:  # noqa: BLE001 - 老版本 Qt 没有这个访问器时不影响其它判据
        transient = None
    return {
        "atMs": None,  # 由调用方补
        "dismissed": bool(startup.property("dismissed")),
        "startupActive": bool(startup.property("startupActive")),
        "splashVisible": bool(splash.isVisible()),
        # `isVisible()` 是 Qt 侧的真实状态，`visible` 是 QML 侧请求的值 ——
        # 两者不一致时（offscreen 平台实测出现过）要能一眼看出来是哪一侧的问题
        "splashVisibleProperty": bool(splash.property("visible")),
        "splashVisibility": int(splash.property("visibility").value),
        "splashFlags": int(splash.flags().value),
        "splashTransientParent": "main" if transient is root else ("none" if transient is None else "other"),
        "mainVisible": bool(root.isVisible()),
        "statusText": status_text,
        "messages": len(sink.messages),
    }


def main() -> int:
    app = QGuiApplication.instance() or QGuiApplication([])
    del app  # noqa: F841 - 只是确保实例存在
    sink = main_qml.install_message_handler()
    isolate_single_instance()
    patch_startup_defaults()

    report: Dict[str, Any] = {"ok": False, "observations": [], "errors": []}
    started = time.perf_counter()

    try:
        built = main_qml.assemble([], qml="App.qml", init_logging=False, start_startup=True)
    except Exception as exc:  # noqa: BLE001 - 装配失败要能被 pytest 看见
        report["errors"].append(f"assemble 失败: {type(exc).__name__}: {exc}")
        print(MARKER + json.dumps(report, ensure_ascii=False))
        return 1

    roots = built.engine.rootObjects()
    if not roots:
        report["errors"].append("App.qml 没有根对象")
        print(MARKER + json.dumps(report, ensure_ascii=False))
        return 1

    root = roots[0]
    startup = built.engine._fmcl_bridges.get("Startup")
    splash = root.findChild(QObject, "splashWindow")
    if startup is None:
        report["errors"].append("Startup 桥没注册上")
    if splash is None:
        report["errors"].append("找不到 splashWindow（App.qml 里的 Splash 不见了？）")
    if report["errors"]:
        print(MARKER + json.dumps(report, ensure_ascii=False))
        return 1

    def observe(tag: str, at_ms: int) -> Dict[str, Any]:
        snap = window_state(startup, splash, root, sink)
        snap["atMs"] = at_ms
        snap["tag"] = tag
        report["observations"].append(snap)
        if tag == "装载完成":
            # 装载完成那一刻就必须是"启动画面占屏、主窗口不见"
            if not snap["splashVisible"]:
                report["errors"].append("启动画面在装载完成时不可见")
            if snap["mainVisible"]:
                report["errors"].append("主窗口在启动画面期间就露出来了")
        return snap

    observe("装载完成", 0)
    QTest.qWait(OBSERVE_MS)
    startup_snap = observe("初始化中", OBSERVE_MS)
    QTest.qWait(SETTLE_MS - OBSERVE_MS)
    settled = observe("收尾后", SETTLE_MS)
    QTest.qWait(FADE_MS)
    final = observe("淡出结束", SETTLE_MS + FADE_MS)

    report["describe"] = dict(startup.describe())
    report["elapsedMs"] = round((time.perf_counter() - started) * 1000, 1)
    report["fadeMs"] = FADE_MS
    report["messages"] = list(sink.messages)
    report["errors"] = list(report["errors"]) + list(built.sink.errors)

    # ── 判据（每条都给得出"为什么"） ──
    checks = {
        "装载时启动画面可见": startup_snap is not None and report["observations"][0]["splashVisible"],
        "装载时主窗口不可见": report["observations"][0]["mainVisible"] is False,
        "初始化中文案已翻译": report["observations"][1]["statusText"] not in ("", "startup_initializing"),
        "初始化中启动画面仍可见": report["observations"][1]["splashVisible"] is True,
        "收尾后已标记 dismissed": settled["dismissed"] is True,
        "收尾后 startupActive 为假": settled["startupActive"] is False,
        "淡出结束后启动画面隐藏": final["splashVisible"] is False,
        "主窗口最终可见": final["mainVisible"] is True,
        "没有 QML 绑定错误": report["errors"] == [],
    }
    report["checks"] = checks
    report["ok"] = all(checks.values())
    print(MARKER + json.dumps(report, ensure_ascii=False))

    # 退出清理：先删根对象再放引擎（否则每个绑定刷一条 TypeError，污染"有没有 QML 报错"）
    try:
        root.deleteLater()
        QTest.qWait(40)
    except Exception:  # noqa: BLE001
        pass
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
