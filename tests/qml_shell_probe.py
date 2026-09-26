"""QML 骨架探针（`tests/test_shell_qml.py` 用的**子进程**驱动）。

## 为什么必须另起一个进程

`QQmlApplicationEngine` + FluentUI 的 `FluWindow` 在同一个进程里**跨测试文件不安全**：
全量测试跑到 `tests/test_shell_qml.py` 时（此前已有若干个测试文件建过引擎与 FluWindow），
`engine.load(App.qml)` 会以 `Windows fatal exception: access violation` 收场
（`exit=-1073741819`）；单独跑本文件、或只带两三个 QML 测试文件跑都干净。
把"真引擎加载骨架"这件事放到**全新进程**里做，是唯一稳定的做法 ——
`tests/test_main_qml_entry.py` 的退出行为测试也是同一个理由用子进程的。

跑法（父测试自动调）：`.venv\\Scripts\\python.exe tests\\qml_shell_probe.py`
输出：一行 `PROBE_JSON:{...}`（父测试解析它做断言），进程退出码 0/1。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QEvent, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication, QMouseEvent  # noqa: E402
from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import main_qml  # noqa: E402
from app.bridges import nav_bridge as nb  # noqa: E402
from app.bridges.shell_bridge import ShellBridge  # noqa: E402

MARKER = "PROBE_JSON:"
APP = QGuiApplication.instance() or QGuiApplication([])
SINK = main_qml.install_message_handler()
ENGINE = main_qml.build_engine()
BRIDGES = main_qml.register_bridges(ENGINE, None)
NAV = ENGINE._fmcl_bridges["Nav"]
SHELL = ENGINE._fmcl_bridges["Shell"]
ENGINE.load(QUrl.fromLocalFile(str(main_qml.app_qml_path("App.qml"))))
ROOT = ENGINE.rootObjects()[0] if ENGINE.rootObjects() else None
QTest.qWait(120)


def item(name: str) -> Any:
    return ROOT.findChild(QQuickItem, name) if ROOT is not None else None


def nav_delegates() -> Dict[str, Any]:
    listing = item("navigationList")
    if listing is None:
        return {}
    content = listing.property("contentItem")
    if content is None:
        return {}
    return {child.objectName(): child for child in content.childItems() if child.objectName()}


def settle(timeout_ms: int = 3000) -> None:
    """等 StackView 的过渡走完：offscreen 下约 500 ms 内新旧两页重叠，点谁是不确定的。"""
    stack = item("pageStack")
    if stack is None:
        return
    waited = 0
    while waited < timeout_ms:
        visible = [c for c in stack.childItems()
                   if c.objectName().endswith("Page") and c.property("visible")]
        if len(visible) <= 1:
            return
        QTest.qWait(50)
        waited += 50


def click(target: Any) -> None:
    """按算出来的真实坐标投递 press + release（QTest.mouseClick 对页面内控件不投递）。"""
    if target is None:
        raise RuntimeError("点击目标不存在")
    settle()
    centre = target.mapToScene(QPointF(target.width() / 2, target.height() / 2))
    global_pos = ROOT.mapToGlobal(centre)
    for event_type, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton),
                                (QEvent.MouseButtonRelease, Qt.NoButton)):
        APP.sendEvent(ROOT, QMouseEvent(event_type, centre, global_pos, Qt.LeftButton, buttons, Qt.NoModifier))
        QTest.qWait(30)
    QTest.qWait(60)


def current_page() -> Any:
    stack = item("pageStack")
    return stack.property("currentItem") if stack is not None else None


def page_name() -> str:
    page = current_page()
    return page.objectName() if page is not None else ""


def page_text(object_name: str) -> str:
    page = current_page()
    child = page.findChild(QQuickItem, object_name) if page is not None else None
    return str(child.property("text")) if child is not None else ""


def status_text() -> str:
    widget = item("statusText")
    return str(widget.property("text")) if widget is not None else ""


def walk() -> List[Dict[str, Any]]:
    """点每一个一级导航项各切一次。"""
    rows: List[Dict[str, Any]] = []
    delegates = nav_delegates()
    for domain in [entry["id"] for entry in SHELL.navItems()]:
        NAV.reset()
        click(delegates.get(f"navItem_{domain}"))
        rows.append({
            "domain": domain,
            "route": NAV.currentRoute,
            "page": page_name(),
            "depth": item("pageStack").property("depth"),
            "routeInfo": page_text("pageRouteInfo"),
        })
    return rows


def main() -> int:
    report: Dict[str, Any] = {
        "platform": APP.platformName(),
        "bridges": BRIDGES,
        "errors": [],
    }
    if ROOT is None:
        report["errors"] = SINK.messages
        print(MARKER + json.dumps(report, ensure_ascii=False))
        return 1

    report["root"] = {
        "objectName": ROOT.objectName(),
        "isWindow": isinstance(ROOT, QQuickWindow),
        "effect": ROOT.property("effect"),
        "visible": bool(ROOT.property("visible")),
        "title": str(ROOT.property("title")),
    }
    report["layers"] = {
        name: item(name) is not None
        for name in ("titleBar", "breadcrumb", "navigation", "pageStack", "statusBar", "globalSearchBox")
    }
    report["navItems"] = [entry["id"] for entry in SHELL.navItems()]
    report["navDelegates"] = sorted(nav_delegates())
    report["navItemTexts"] = {
        name: str(widget.findChild(QQuickItem, "navItemText").property("text"))
        for name, widget in nav_delegates().items()
    }

    # 1) 点一遍 12 个一级导航项
    report["walk"] = walk()

    # 2) 三态演示（点页面里的三个按钮）
    NAV.reset()
    click(nav_delegates().get("navItem_tools"))
    settle()
    states = []
    page = current_page()
    icon = page.findChild(QQuickItem, "pageStateIcon")
    for button_name, expected in (("demoEmptyButton", "empty"), ("demoErrorButton", "error"),
                                  ("demoLoadingButton", "loading")):
        click(page.findChild(QQuickItem, button_name))
        states.append({
            "expected": expected,
            "state": str(page.property("demoState")),
            "icon": str(icon.property("source")),
            "label": str(page.findChild(QQuickItem, "pageStateText").property("text")),
        })
    report["threeStates"] = states

    # 3) 状态条：绑定 + 到点自动清空（把 10 秒压成 150 ms 跑同一套机制）
    report["status"] = {"timeoutMs": SHELL.statusTimeout, "empty": status_text()}
    SHELL.setStatus("skeleton check", "warning")
    QTest.qWait(60)
    report["status"]["afterSet"] = status_text()
    report["status"]["level"] = SHELL.statusLevel
    SHELL.clearStatus()
    QTest.qWait(60)
    report["status"]["afterClear"] = status_text()

    short = ShellBridge(nav=NAV, status_timeout_ms=150)
    ENGINE.rootContext().setContextProperty("Shell", short)
    ENGINE._fmcl_bridges["Shell"] = short
    short.setStatus("auto clear me", "success")
    QTest.qWait(60)
    report["status"]["shortTimeoutBefore"] = status_text()
    QTest.qWait(400)
    report["status"]["shortTimeoutAfter"] = status_text()
    ENGINE.rootContext().setContextProperty("Shell", SHELL)
    ENGINE._fmcl_bridges["Shell"] = SHELL
    QTest.qWait(60)

    # 4) 非法路由 / 非法深链：必须**看得见**（状态条上是原因）
    NAV.reset()
    SHELL.clearStatus()
    before = len(SINK.messages)
    pushed = NAV.push("versions/does-not-exist")
    QTest.qWait(60)
    report["unknownRoute"] = {
        "returned": pushed,
        "statusText": status_text(),
        "currentRoute": NAV.currentRoute,
        "qmlMessages": SINK.messages[before:],
    }

    NAV.reset()
    SHELL.clearStatus()
    before = len(SINK.messages)
    opened = NAV.openDeepLink("fmcl://versions/detail?version=1.21.4")
    QTest.qWait(80)
    report["deepLink"] = {
        "returned": opened,
        "page": page_name(),
        "params": NAV.currentParams,
        "routeInfo": page_text("pageRouteInfo"),
    }

    NAV.reset()
    SHELL.clearStatus()
    rejected = NAV.openDeepLink("https://example.com/versions")
    QTest.qWait(60)
    report["badDeepLink"] = {
        "returned": rejected,
        "statusText": status_text(),
        "currentRoute": NAV.currentRoute,
        "depth": item("pageStack").property("depth"),
    }

    # 5) 面包屑点击 + 标题栏返回按钮
    NAV.reset()
    NAV.push("versions")
    NAV.push("versions/detail", {"version": "1.21.4"})
    QTest.qWait(200)
    crumbs = [c for c in _walk_items(item("breadcrumb")) if c.objectName() == "crumbItem"]
    report["breadcrumb"] = {
        "items": len(crumbs),
        "titles": [str(c.property("text")) for c in crumbs],
        "depth": NAV.depth,
    }
    if len(crumbs) >= 2:
        click(crumbs[1])
        report["breadcrumb"]["afterCrumbClick"] = NAV.currentRoute
    click(item("backButton"))
    report["breadcrumb"]["afterBackButton"] = NAV.currentRoute

    # 6) 插件页（只给一个 QML 文件 URL、没有声明 PageStack 传的属性）
    plugin_dir = Path(tempfile.mkdtemp(prefix="fmcl_plugin_"))
    plugin_qml = plugin_dir / "PluginMain.qml"
    plugin_qml.write_text(
        'import QtQuick\nItem {\n    objectName: "pluginPage"\n'
        '    Text { objectName: "pluginLabel"; text: "plugin page" }\n}\n',
        encoding="utf-8", newline="\n",
    )
    before = len(SINK.messages)
    registered = NAV.registerPluginRoute(
        "plugin/com.demo/main", "tab_agent", QUrl.fromLocalFile(str(plugin_qml)).toString(), "com.demo")
    pushed = NAV.push("plugin/com.demo/main")
    QTest.qWait(200)
    report["pluginPage"] = {
        "registered": registered,
        "pushed": pushed,
        "page": page_name(),
        "route": NAV.currentRoute,
        "qmlMessages": SINK.messages[before:],
    }

    # 7) 收尾：整段期间的 QML 消息（判据是"一条报错都不许有"）
    report["qmlMessages"] = SINK.messages
    print(MARKER + json.dumps(report, ensure_ascii=False))
    return 0


def _walk_items(root: Any) -> List[Any]:
    out: List[Any] = []
    for child in root.childItems():
        out.append(child)
        out.extend(_walk_items(child))
    return out


if __name__ == "__main__":
    raise SystemExit(main())
