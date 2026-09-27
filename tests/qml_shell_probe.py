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

# 图标上色 provider 必须注册（生产路径在 `main_qml.assemble()` 里做）：
# 不注册的话每个 `image://fmcl-icon/…` 都会得到一条 `Invalid image provider` 警告，
# 而"消息里有没有 Qt 报错"是判据之一 —— 那会让探针自己制造的噪声看起来像缺陷。
from app.bridges.icon_provider import install as install_icon_provider  # noqa: E402

install_icon_provider(ENGINE)

BRIDGES = main_qml.register_bridges(ENGINE, None)
NAV = ENGINE._fmcl_bridges["Nav"]
SHELL = ENGINE._fmcl_bridges["Shell"]
ENGINE.load(QUrl.fromLocalFile(str(main_qml.app_qml_path("App.qml"))))
ROOT = ENGINE.rootObjects()[0] if ENGINE.rootObjects() else None
QTest.qWait(120)


def js_list(value: Any) -> list:
    """把 QML 里的 JS 数组读成 Python 列表。

    `property()` 拿到的是 **QJSValue**（不是 list），直接 `for … in` 会抛
    `TypeError: 'QJSValue' object is not iterable`（返工 B 组实测踩到）。
    """
    if value is None:
        return []
    for converter in ("toVariant",):
        method = getattr(value, converter, None)
        if callable(method):
            converted = method()
            if converted is not None:
                try:
                    return list(converted)
                except TypeError:
                    pass
    length = getattr(value, "property", None)
    if callable(length):
        try:
            return [value.property(index) for index in range(int(value.property("length")))]
        except Exception:  # noqa: BLE001 - 不是数组就当空表
            return []
    try:
        return list(value)
    except TypeError:
        return []


def item(name: str) -> Any:
    return ROOT.findChild(QQuickItem, name) if ROOT is not None else None


def nav_delegates() -> Dict[str, Any]:
    """导航列表里的**导航项**（返工 B 组之后列表里还有组标题行，按前缀筛掉）。

    组标题的 objectName 是 `navGroup_<组 id>`，导航项是 `navItem_<领域 id>` ——
    两者都是 delegate 的根（delegate 根才是 `contentItem` 的子项），
    所以这里必须按名字筛，不能"凡是带 objectName 的都算导航项"。
    """
    listing = item("navigationList")
    if listing is None:
        return {}
    content = listing.property("contentItem")
    if content is None:
        return {}
    return {
        child.objectName(): child
        for child in content.childItems()
        if child.objectName().startswith("navItem_")
    }


def nav_group_rows() -> List[str]:
    """导航列表里的**组标题行**（返工 B 组的导航分组）。"""
    listing = item("navigationList")
    if listing is None:
        return []
    content = listing.property("contentItem")
    if content is None:
        return []
    return sorted(
        child.objectName() for child in content.childItems()
        if child.objectName().startswith("navGroup_")
    )


def nav_rows_by_position() -> List[str]:
    """导航列表里**按屏幕位置从上到下**排好的行名（返工 B 组的分组顺序就靠它断言）。

    delegate 的 `y` 是 ListView 排出来的真实位置；用 `contentY` 偏移在首屏可以忽略
    （列表还没滚动过）。
    """
    listing = item("navigationList")
    if listing is None:
        return []
    content = listing.property("contentItem")
    if content is None:
        return []
    rows = [child for child in content.childItems() if child.objectName()]
    rows.sort(key=lambda child: float(child.property("y")))
    return [child.objectName() for child in rows]


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
    # `titleBar` 也一并记下来：返工 B 组把那条自绘顶栏并进了 `appBar`，
    # 这里留着它就是为了让测试能断言"旧的那条真的没了"（两条横条叠着是返工前的观感）。
    report["layers"] = {
        name: item(name) is not None
        for name in ("appBar", "titleBar", "breadcrumb", "navigation", "pageStack",
                     "statusBar", "globalSearchBox")
    }
    # 顶栏把"可交互项"声明成一个列表，App.qml 在完成时逐个登记进 FluFrameless 的命中测试
    # 白名单（不登记的话点击会被系统当成拖窗口吃掉：不报错、只是按钮没反应）。
    # `_hitTestList` 是 C++ 侧私有成员，离屏下也观察不到 —— 这里记录**声明**本身，
    # 由测试断言"声明的名字恰好是那四个、且界面上都真的存在"（漏一个就是漏一个）。
    app_bar = ROOT.property("appBar")
    declared = []
    if app_bar is not None:
        for entry in js_list(app_bar.property("interactiveItems")):
            if entry is not None:
                declared.append(entry.objectName())
    report["hitTest"] = {
        "declared": sorted(name for name in declared if name),
        "present": sorted(
            name for name in ("backButton", "globalSearchBox", "notificationButton", "accountButton")
            if item(name) is not None
        ),
    }
    report["navItems"] = [entry["id"] for entry in SHELL.navItems()]
    report["navGroups"] = [entry["group"] for entry in SHELL.navItems()]
    report["navGroupHeaders"] = nav_group_rows()
    #: 屏幕上的真实顺序（组标题与导航项混在一起）—— 分组显示的判据
    report["navRowOrder"] = nav_rows_by_position()
    report["navDelegates"] = sorted(nav_delegates())
    report["navItemTexts"] = {
        name: str(widget.findChild(QQuickItem, "navItemText").property("text"))
        for name, widget in nav_delegates().items()
    }
    # 选中态：导航项的指示条与图标颜色（返工 B 组的"一眼看得出在哪一页"）。
    # 首页是栈底，所以进页面后 `navItem_home` 一定是选中态。
    home_row = nav_delegates().get("navItem_home")
    if home_row is not None:
        indicator = home_row.findChild(QQuickItem, "navItemIndicator")
        icon = home_row.findChild(QQuickItem, "navItemIcon")
        report["navSelection"] = {
            "indicatorOpacity": float(indicator.property("opacity")) if indicator is not None else -1.0,
            "indicatorColor": str(indicator.property("color")) if indicator is not None else "",
            "iconColor": str(icon.property("color")) if icon is not None else "",
            "index": int(home_row.property("y")),
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
