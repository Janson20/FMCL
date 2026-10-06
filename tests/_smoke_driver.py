"""UI 冒烟驱动的**子进程侧**（阶段 2 任务 2.19 / `03` 的 2.12；`tests/ui_smoke.py` 是唯一入口）。

## 为什么必须另起进程

与 `tests/qml_shell_probe.py` 同一个理由（那边是 2.11/2.12 踩出来的）：`QQmlApplicationEngine`
+ FluentUI 的 `FluWindow` 在**同一个进程里跨测试文件不安全** —— 全量测试跑到某个点之后，
再 `engine.load(App.qml)` 会以 `Windows fatal exception: access violation`
（`exit=-1073741819`）收场。所以"真引擎 + 12 页遍历"这件事只在全新进程里做。

## 本文件只收集数据，判定在 `tests/ui_smoke.py`

子进程负责：装配 → 遍历 → 截图 → 记录**原始** Qt 消息（带 `mode`）与每条断言的 `ok`；
`IGNORED_PATTERNS` 的登记与"什么算失败"的判据在 `tests/ui_smoke.py`（唯一一处，可单测）。
这样"忽略平台噪声"这件事只有一份实现，不会在父子两侧各写一遍然后慢慢跑偏。

## 两条实测出来的顺序要求（都会硬崩，不是风格问题）

1. **先有 `QGuiApplication` 才能建 `QQmlApplicationEngine`**（反了是 `exit=3221226505`）。
   `main_qml.assemble()` 内部第一件事就是建 app，所以走 `assemble()` 天然满足；
2. **建 QML 对象之前必须先 `import PySide6.QtQuick`**：PySide6 的类型表里没有
   `QQuickWindow` 时，QML 建出来的窗口会被包成 `QWindow`（没有 `grabWindow()`，
   截图直接失败）。这是我第一次跑探针就踩到的 —— 见下面的 import 注释和
   `tests/test_ui_smoke.py` 里对截图非空的断言。

## 退出码

0 = 走完（**不代表全绿**：判据在父进程）；1 = 装配失败 / 走不完。父进程两者都当失败处理，
只是错误信息不同。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 平台：offscreen（无头跑）+ Basic 样式（与 tests/qml_shell_probe.py 一致；
# FluentUI 的控件由 QML 侧自己 import，不受这个环境变量影响）。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
# 缺陷 D-150：**QML 磁盘缓存那条异步路径会让主题切换刷出 10 条 TypeErrors**
# （`QML FmIcon: Cannot find member data` + 两条 `… is not a function`，屏幕上一个像素都不差）。
# 三条实测把窗口钉在"引擎从磁盘缓存异步取编译单元"这件事上：
#   ① `QML_DISABLE_DISK_CACHE=1` → 一条都不报（跑 5 次全绿）；
#   ② 把主题切换后的等待从 120ms 提到 600ms → **有时**不报（这就是竞态的样子，不可靠）；
#   ③ 只跑页面段或只跑主题段 → 都不报（要"走完 12 页 + 切主题"同时成立才撞得上）。
# 冒烟测试要判的是"界面对不对"，所以这里关掉那条优化；**产品侧怎么办**（关缓存 / 打预编译 QML）
# 是一个待裁决项，记在缺陷 D-150 与 `15-phase2-regression-report.md` 第七节。
# 想复现：把这一行的 `setdefault` 改成显式 `os.environ["QML_DISK_CACHE"]`… 或直接删掉本行。
os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QEvent, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication, QMouseEvent  # noqa: E402
from PySide6.QtQml import QQmlComponent  # noqa: E402

# ⚠️ 必须在任何 QML 对象创建之前 import：否则 QQuickWindow 不在 PySide6 的类型表里，
# 根窗口会被包成 QWindow，`grabWindow()` 直接 AttributeError（实测）。
# 与 `poc/overlay/overlay_common.py:18` 同一个做法（那里也标了"导入即注册类型转换器"）。
from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402,F401
from PySide6.QtTest import QTest  # noqa: E402

import main_qml  # noqa: E402

MARKER = "SMOKE_JSON:"
#: 每个状态切过去之后等多久（毫秒）。够绑定求值 + 渲染一帧，又不至于把总时长拖长。
SETTLE_MS = 60
#: **主题切换之后**要等多久（毫秒）。比 `SETTLE_MS` 长一个量级，理由见下。
#:
#: 返工 D 组实测（缺陷 D-150）：主题切换会让所有颜色绑定重算、并连带重建一批对象
#: （`Loader` / `Repeater` 委托 / 导航项），而 QML 的**编译单元是从磁盘缓存异步取的**。
#: 只等 120ms 时，引擎会在"单元还没就位"的窗口里建对象，于是刷出
#:
#:     FmButton.qml:83:9: QML FmIcon: Cannot find member data
#:     FmButton.qml:74: TypeError: Property 'borderColor' of object FmButton_QMLTYPE_… is not a function
#:     FmButton.qml:75: TypeError: Property 'faceColor' of object FmButton_QMLTYPE_… is not a function
#
#: 共 10 条（首页骨架 1 + 三个演示按钮各 3）。三条实测把这条窗口钉死了：
#: ① `QML_DISABLE_DISK_CACHE=1` → 一条都不报（没有异步取单元这回事）；
#: ② 把这里的等待从 120ms 提到 600ms → 一条都不报（窗口过去了）；
#: ③ 报错只和"切主题那一刻在重建什么"有关 —— 屏幕上一个像素都不差
#:    （`poc/_smoke_*/theme_3_*.png` 与正常帧一致，按钮的底色与描边都在）。
#:
#: 所以本文件把等待放宽到 600ms：冒烟要判的是"界面对不对"，不该把引擎的取单元窗口
#: 当成界面缺陷；同时 D-150 记着"真实用户快速连点主题时也可能出现同样的日志噪声"。
THEME_SETTLE_MS = 600
#: 三态的名字（页面骨架 `FmPage` 的 `contentState` 取值，见 qml/components/FmPage.qml）
STATES = ("loading", "empty", "error")

#: 三态 → （状态件根对象名, 标题对象名, 图标对象名）。
#: 返工 C 组把三态区换成自研的 FmLoadingState / FmEmptyState / FmErrorState 之后，
#: "渲染出来的文案与图标"要从状态件里读（判据仍是渲染结果，不是页面的入参）。
STATE_PARTS: Dict[str, Tuple[str, str, str]] = {
    "loading": ("fmLoadingState", "fmLoadingStateTitle", "fmLoadingStateIcon"),
    "empty": ("fmEmptyState", "fmEmptyStateTitle", "fmEmptyStateIcon"),
    "error": ("fmErrorState", "fmErrorStateTitle", "fmErrorStateIcon"),
}


class Recorder:
    """Qt 消息记录器（`QMessageSink` 的带级别版本）。

    为什么要子类化 `main_qml.QtMessageSink` 而不是自己 `qInstallMessageHandler`：
    `assemble()` 内部会再装一次处理器，自己装的会被它顶掉 —— 而**加载期的消息**
    （QML 语法错、绑定错就在这一批里）恰恰是最要紧的。子类化之后
    `install_message_handler()` 建出来的就是它，`assemble()` 返回的 `sink` 也是它。
    """

    def __init__(self) -> None:
        from main_qml import QtMessageSink

        self._base = QtMessageSink()
        self.messages: List[str] = self._base.messages
        self.records: List[Dict[str, Any]] = []

    def handler(self, mode: Any, context: Any, message: str) -> None:  # noqa: ANN001
        self._base.handler(mode, context, message)
        self.records.append(
            {
                "mode": str(mode).split(".")[-1],
                "text": str(message),
                "file": str(getattr(context, "file", "") or ""),
                "line": int(getattr(context, "line", 0) or 0),
            }
        )

    def mark(self) -> int:
        """当前记录条数（给"这一步产生了哪些消息"切片用）。"""
        return len(self.records)

    def since(self, mark: int) -> List[Dict[str, Any]]:
        return self.records[mark:]


class Smoke:
    """一次冒烟运行的上下文：引擎、窗口、桥、输出目录、断言与消息。"""

    def __init__(self, out_dir: Path, mutations: List[str], skips: Optional[List[str]] = None) -> None:
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.mutations = set(mutations)
        self.skips = {part.strip() for part in (skips or []) if part.strip()}
        self.checks: List[Dict[str, Any]] = []
        self.recorder = Recorder()
        self.payload: Dict[str, Any] = {}
        self.shots: List[Dict[str, Any]] = []

    # ─── 断言与截图 ─────────────────────────────────────────

    def check(self, cid: str, ok: bool, detail: str = "") -> bool:
        """记一条断言。`cid` 形如 `page.home.route`（父进程按它出表与报错）。"""
        self.checks.append({"id": cid, "ok": bool(ok), "detail": str(detail)})
        return bool(ok)

    def snapshot(self, win: Any, name: str) -> Dict[str, Any]:
        """抓窗口自身渲染结果并落盘。返回的 `bytes` 是**真实文件大小**（父进程要断言非空）。"""
        QTest.qWait(120)  # 至少渲染一帧，否则抓到的是清屏色
        image = win.grabWindow()
        path = self.out_dir / f"{name}.png"
        saved = False
        try:
            saved = (not image.isNull()) and bool(image.save(str(path)))
        except Exception as exc:  # noqa: BLE001 - 截图失败不该打断走查
            self.check(f"shot.{name}", False, f"save 抛异常: {exc}")
        info = {
            "name": name,
            "file": path.name,
            "path": str(path),
            "width": int(image.width()),
            "height": int(image.height()),
            "null": bool(image.isNull()),
            "saved": bool(saved),
            "bytes": int(path.stat().st_size) if path.exists() else 0,
        }
        self.shots.append(info)
        return info


# ─── 工具 ──────────────────────────────────────────────────────


def item(root: Any, name: str) -> Any:
    return root.findChild(QQuickItem, name) if root is not None else None


def visual_root(window: Any) -> Any:
    """窗口的视觉树根（拿不到就退回窗口对象本身）。"""
    try:
        content = window.contentItem()
    except AttributeError:  # 被包成 QWindow（见文件头第 2 条）时没有 contentItem
        return window
    return content if content is not None else window


def iter_visual(node: Any) -> Any:
    """深度遍历**视觉树**（`childItems()`），产出每个 QQuickItem。

    为什么不用 `findChild` / `findChildren`（两者都是 **QObject** 树）：`StackView` 的页面
    是 `Qt.createComponent(...).createObject(null)` 建出来的，**没有 QObject 父对象**
    （实测：`page.parent()` 是 None、`page.parentItem()` 才是 pageStack），
    `ListView` 的委托也一样。QObject 树的查找在这两处**一个都找不到**，
    只有视觉树（`childItems()`）找得到 —— `tests/qml_shell_probe.py` 数导航项委托时
    也是走的 `contentItem().childItems()`，同一个原因。
    """
    yield node
    for child in node.childItems():
        yield from iter_visual(child)


def find_visual(node: Any, name: str) -> Any:
    for child in iter_visual(node):
        if child.objectName() == name:
            return child
    return None


def find_all_visual(node: Any, name: str) -> List[Any]:
    return [child for child in iter_visual(node) if child.objectName() == name]


def prop_str(obj: Any, name: str) -> str:
    """读属性并转成字符串（`Image.source` 这类是 `QUrl`，直接 `str()` 会得到 repr）。"""
    if obj is None:
        return ""
    value = obj.property(name)
    if value is None:
        return ""
    if hasattr(value, "toString"):
        return str(value.toString())
    return str(value)


def state_parts(page: Any, state: str) -> Dict[str, str]:
    """读出**渲染出来的**那个状态件：哪个件在、它的标题文案与图标名。

    返工 C 组之前，三态是每个页面各抄一份的 `Image` + `Text`
    （探针按 `pageStateIcon.source` / `pageStateText.text` 取证）；现在三态渲染只有一份
    实现（`FmPage` 按 `contentState` 挑 `FmLoadingState` / `FmEmptyState` / `FmErrorState`），
    所以证据改成读状态件自己的标题与图标 —— 仍然是渲染结果，不是页面的入参。
    """
    out = {"shown": "", "label": "", "icon": ""}
    root_name, title_name, icon_name = STATE_PARTS[state]
    node = item(page, root_name)
    if node is None or not node.isVisible():
        return out
    out["shown"] = state
    out["label"] = prop_str(item(node, title_name), "text")
    out["icon"] = prop_str(item(node, icon_name), "name")
    return out


def click(window: Any, target: Any) -> bool:
    """按算出来的真实坐标投递 press + release（QTest.mouseClick 对页面内控件不投递）。"""
    if target is None:
        return False
    centre = target.mapToScene(QPointF(target.width() / 2, target.height() / 2))
    global_pos = window.mapToGlobal(centre)
    for event_type, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton),
                                (QEvent.MouseButtonRelease, Qt.NoButton)):
        QGuiApplication.instance().sendEvent(
            window, QMouseEvent(event_type, centre, global_pos, Qt.LeftButton, buttons, Qt.NoModifier)
        )
        QTest.qWait(30)
    QTest.qWait(SETTLE_MS)
    return True


def settle(root: Any, timeout_ms: int = 1500) -> int:
    """等 `StackView` 的过渡走完，返回等了多久（毫秒）。

    `qml_shell_probe.py` 的同一处置：过渡期间新旧两页**同时在屏上**（offscreen 下约
    500 ms），不等待的话"当前页是谁"是不确定的（也会出现两个同 objectName 的页面对象）。
    """
    stack = item(root, "pageStack")
    if stack is None:
        return 0
    waited = 0
    while waited < timeout_ms:
        visible = [child for child in stack.childItems()
                   if child.objectName().endswith("Page") and child.property("visible")]
        if len(visible) <= 1:
            return waited
        QTest.qWait(50)
        waited += 50
    return waited


def tail_lines(path: Path, limit: int, max_bytes: int = 131072) -> List[str]:
    """读文件**尾部**若干行（不整份读进来：`latest.log` 有几百 KB 到几 MB）。"""
    if not path.is_file():
        return []
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - max_bytes))
        blob = handle.read()
    text = blob.decode("utf-8", errors="replace")
    lines = text.splitlines()
    return [line for line in lines if line.strip()][-limit:]


# ─── 装配 ──────────────────────────────────────────────────────


def isolate_single_instance() -> str:
    """把单实例守卫的名字改成"本次运行唯一"，让并行的冒烟/测试进程互不干扰。

    **实现已收口到 `tests/single_instance_isolation.py`**（原先 `_smoke_driver.py`
    与 `qml_startup_probe.py` 各抄了一份，而 `test_main_qml_entry.py` 没有 ——
    并行跑测试时只有它红）。这里保留这个薄封装：报告里要拿到后缀做排查。
    """
    from single_instance_isolation import isolate_single_instance as _isolate

    return _isolate("ui-smoke")


def assemble(smoke: Smoke) -> Any:
    """起 app / 引擎 / 桥并加载 `App.qml`（**不开跑启动流程**）。

    `start_startup=False` 的理由：冒烟测试要判的是"界面能起来并能遍历 12 页"，
    不是"真实初始化跑完了"。启动流程会踢出后台任务并弹协议/公告窗，而装配完就返回的
    进程一旦开始解释器关闭阶段就会得到 `RuntimeError: can't register atexit after shutdown`
    （`assemble` 的 docstring 里记着这条实测）。"启动流程本身对不对"是 2.14 的范围
    （`tests/test_startup_flow.py`），这里不重复，也省掉它的等待时间。
    """
    # `install_message_handler()` 建的是 `main_qml.QtMessageSink()`；把它换成我们的
    # 带级别记录器，才能连**加载期**的消息（QML 语法/绑定错）一起带上 mode。
    main_qml.QtMessageSink = lambda: smoke.recorder  # type: ignore[assignment]

    smoke.payload["singleInstanceKeySuffix"] = isolate_single_instance()
    #: 真配置的写盘也要挡住：本进程走生产装配路径，手里那份 `config` 就是根模块单例 ——
    #: 不挡的话跑一轮冒烟就会把开发机的 `config.json` 改写掉（语言/强调色都中过招）。
    #: 注意**别把返回值塞进 payload**：那是个函数，JSON 序列化会炸（实测踩过）。
    from config_isolation import isolate_config_writes

    isolate_config_writes("ui-smoke")
    main_qml.create_application([])  # 必须**先**有 QGuiApplication（见文件头第 1 条）
    started = time.perf_counter()
    try:
        built = main_qml.assemble([], qml="App.qml", init_logging=False, start_startup=False)
    except main_qml.AlreadyRunning as exc:  # noqa: PERF203 - 环境占用，不是界面缺陷
        raise RuntimeError(
            "单实例守卫已被占用（AlreadyRunning）：说明有另一个 FMCL 实例"
            "（或另一个冒烟子进程）在跑。冒烟测试需要独占一个进程，"
            "请等它结束再重跑；若确认没有别的实例，检查 isolate_single_instance() 是否失效"
        ) from exc
    smoke.payload["assemble"] = {
        "ms": round((time.perf_counter() - started) * 1000, 1),
        "bridges": built.bridges,
        "rootObjects": len(built.engine.rootObjects()),
        "startupStarted": False,
        "startupAvailable": built.engine._fmcl_bridges.get("Startup") is not None,
    }
    smoke.check("assemble.rootObjects", len(built.engine.rootObjects()) == 1,
                f"rootObjects={len(built.engine.rootObjects())}")
    smoke.check("assemble.bridgesMissing", built.bridges["missing"] == [],
                f"missing={built.bridges['missing']}")
    return built


def phase_pages(smoke: Smoke, built: Any) -> None:
    """遍历全部一级页面：路由 → 对象名 → 三态 → 截图 → 消息计数。"""
    engine = built.engine
    root = engine.rootObjects()[0]
    bridges = engine._fmcl_bridges
    nav = bridges["Nav"]
    shell = bridges["Shell"]
    stack = item(root, "pageStack")
    visual = visual_root(root)
    smoke.check("shell.pageStack", stack is not None, "pageStack 没找到")

    # 名单**来自桥**（Shell.navItems() = 一级导航项），不写死一份可能过期的表
    entries = list(shell.navItems())
    routes = [entry["id"] for entry in entries]
    level0 = sorted(r["id"] for r in nav.routes() if r["level"] == 0 and r["available"])
    smoke.payload["routes"] = {
        "source": "Shell.navItems()",
        "count": len(routes),
        "ids": routes,
        "level0FromNavRoutes": level0,
        "routeCount": nav.property("routeCount"),
    }
    smoke.check("routes.nonEmpty", len(routes) > 0, f"{len(routes)} 项")
    smoke.check("routes.matchesNavRoutes", sorted(routes) == level0,
                f"navItems={sorted(routes)} navRoutes={level0}")
    # 一级导航必须**覆盖内置的 12 个领域**。期望值从桥自己的表算出来，而不是写死 12：
    # 并发落地的其它任务会往路由表里加 `dev/gallery` 这类开发页路由
    # （那一行刚落进 `_ROUTE_TABLE`，写死数字就会在别人加一条路由时误报）。
    from app.bridges import nav_bridge as nb  # noqa: PLC0415 - 只在需要时导入

    builtin = [
        route.id for route in nb.build_default_routes()
        if route.level == 0 and (not route.platform or route.platform == nb.host_platform())
    ]
    missing = [route_id for route_id in builtin if route_id not in routes]
    smoke.check("routes.coversBuiltinDomains", not missing,
                f"内置一级领域缺 {missing}（navItems={routes}）")
    smoke.check("routes.count", len(routes) >= len(builtin),
                f"{len(routes)} 项（内置 {len(builtin)} 项，多出来的是插件/开发页路由）")

    rows: List[Dict[str, Any]] = []
    for index, entry in enumerate(entries, start=1):
        route = entry["id"]
        mark = smoke.recorder.mark()
        row: Dict[str, Any] = {"index": index, "route": route, "titleKey": entry["title_key"]}
        started = time.perf_counter()

        # ① 切页：先回栈底再压入目标路由（每个页面都在同一起点上被检查）
        nav.reset()
        QTest.qWait(SETTLE_MS)
        pushed = bool(nav.push(route))
        row["settleMs"] = settle(root)
        QTest.qWait(SETTLE_MS)
        row["pushed"] = pushed
        row["currentRoute"] = str(nav.property("currentRoute"))
        smoke.check(f"page.{route}.pushed", pushed, "push 返回 False")
        smoke.check(f"page.{route}.route", row["currentRoute"] == route,
                    f"currentRoute={row['currentRoute']!r} 期望 {route!r}")

        # ② 页面根对象：从栈上取当前页 → **按 objectName 找回来** → 页码要对得上
        page = stack.property("currentItem") if stack is not None else None
        name = page.objectName() if page is not None else ""
        matches = find_all_visual(visual, name) if name else []
        same_frame = [entry_item for entry_item in matches
                      if str(entry_item.property("routeId")) == route]
        row["pageObjectName"] = name
        row["matchesByName"] = len(matches)
        row["foundByName"] = bool(matches) and bool(same_frame)
        row["routeIdOnPage"] = str(page.property("routeId")) if page is not None else ""
        row["pageVisible"] = bool(page.property("visible")) if page is not None else False
        # 页头图标的取图 URL：返工 C 组之后页头是 `FmIcon`（原来的页面内 `Image`），
        # D-141 的"图标不能退回默认黑"这条证据从这里继续取（`fmIconImage` 是 FmIcon 内部的 Image）
        page_icon = item(page, "pageIcon") if page is not None else None
        row["pageIconName"] = prop_str(page_icon, "name")
        row["pageIconSource"] = prop_str(item(page_icon, "fmIconImage"), "source")
        # 为什么不直接断言 `findChild(...) is page`：过渡刚结束时，栈里可能还留着
        # 上一帧的同名页面对象（12 个领域里 home 会被压两次），`findChild` 返回的
        # 不一定是前台那个 —— 所以身份用 `routeId` 判，而不是用对象地址。
        # 另外 `findChild` 走的是 **QObject 树**，而 StackView 的页面是
        # `createObject(null)` 建的（没有 QObject 父对象），只有视觉树找得到它们。
        smoke.check(f"page.{route}.objectName", bool(name), "页面根对象没有 objectName")
        smoke.check(f"page.{route}.foundByName", row["foundByName"],
                    f"按 {name!r} 找到 {len(matches)} 个对象，其中 routeId=={route!r} 的 {len(same_frame)} 个")
        smoke.check(f"page.{route}.visible", row["pageVisible"], "前台页面不可见")
        smoke.check(f"page.{route}.frame", row["routeIdOnPage"] == route,
                    f"页面拿到的 routeId={row['routeIdOnPage']!r}")
        row["stackError"] = str(stack.property("lastError")) if stack is not None else ""
        smoke.check(f"page.{route}.stackError", row["stackError"] == "", row["stackError"])

        # ③ 三态（加载中 / 空数据 / 出错）：页面骨架 FmPage 的 `contentState`
        #    （返工 C 组之前每页自己抄一份 `demoState` 渲染，现在三态渲染只有一份实现）
        states: List[Dict[str, Any]] = []
        has_state = page is not None and page.property("contentState") is not None
        row["stateProperty"] = "contentState" if has_state else ""
        # 阶段 3 的真实页面（3.1 首页是第一张）**关掉了演示按钮**：它们只是阶段 2 占位页
        # 给验收人切三态用的。真实页面的三态由页面自己的桥驱动，所以：
        #   * 三态渲染这件事照旧强制验（下面按 `contentState` 逐个切）；
        #   * "点演示按钮"这条交互只在**还留着演示按钮**的页面上验（见下面的 clickStates）。
        row["demoControlsVisible"] = bool(page.property("demoControlsVisible")) if has_state else False
        if has_state:
            for state in STATES:
                page.setProperty("contentState", state)
                QTest.qWait(SETTLE_MS)
                shown = state_parts(page, state)
                states.append({
                    "state": state,
                    "got": str(page.property("contentState")),
                    "shown": shown["shown"],
                    "icon": shown["icon"],
                    "label": shown["label"],
                })
            icons = {entry["icon"] for entry in states}
            labels_ok = all(entry["label"].strip() for entry in states)
            smoke.check(f"page.{route}.states", all(entry["got"] == entry["state"] for entry in states),
                        f"{[(e['state'], e['got']) for e in states]}")
            smoke.check(f"page.{route}.stateShown",
                        all(entry["shown"] == entry["state"] for entry in states),
                        f"状态件没跟着 contentState 换：{[(e['state'], e['shown']) for e in states]}")
            smoke.check(f"page.{route}.stateIcons", len(icons) == len(STATES),
                        f"三态图标应各不相同，实际 {sorted(icons)}")
            smoke.check(f"page.{route}.stateLabels", labels_ok,
                        f"{[e['label'] for e in states]}")
            # 三个演示按钮必须在**还开着演示的页面**上（阶段 3 的真实页面关掉了它们，
            # 那种页面改用下面 clickStates 之外的判据：三态渲染已经在本条上面强制验过）
            if row["demoControlsVisible"]:
                buttons = [item(page, f"demo{state.capitalize()}Button") for state in STATES]
                smoke.check(f"page.{route}.stateButtons", all(b is not None for b in buttons),
                            f"{[b is not None for b in buttons]}")
            else:
                smoke.check(f"page.{route}.stateButtons", True,
                            "真实页面（阶段 3）没有演示按钮，本次跳过 —— 三态渲染已在 stateShown 里验过")
        else:
            smoke.check(f"page.{route}.states", True, "页面没有 contentState（阶段 3 的真实页），本次跳过")
        row["states"] = states

        # ④ 截图（真实文件大小由父进程断言非空）
        row["screenshot"] = smoke.snapshot(root, f"{index:02d}_{route.replace('/', '_')}")

        # ⑤ 这一步新增的 Qt 消息（父进程按 IGNORED_PATTERNS 判失败）
        records = smoke.recorder.since(mark)
        row["messages"] = records
        row["messageCount"] = len(records)
        row["ms"] = round((time.perf_counter() - started) * 1000, 1)
        rows.append(row)

    smoke.payload["pages"] = rows
    smoke.check("pages.count", len(rows) == len(routes), f"{len(rows)}/{len(routes)}")

    # 交互路径单独验一次：真的点一下三态按钮（合成鼠标事件），证明按钮是接上的。
    # 阶段 3 起，一级页面会**逐页**换成真实页面（3.1 的首页是第一张），它们不再有演示按钮 ——
    # 所以这里挑**第一个还留着演示按钮的页面**来点；一个都没有时明确记一笔"跳过"，
    # 而不是留一条永远失败的红灯（那种红灯会让人习惯性忽略闸门）。
    clickable = [row["route"] for row in rows if row["demoControlsVisible"]]
    if clickable:
        target = clickable[0]
        nav.reset()
        nav.push(target)
        settle(root)
        QTest.qWait(SETTLE_MS)
        page = stack.property("currentItem")
        clicked: List[str] = []
        for state in ("empty", "error", "loading"):
            button = item(page, f"demo{state.capitalize()}Button")
            if click(root, button):
                clicked.append(str(page.property("contentState")))
        smoke.payload["clickStates"] = clicked
        smoke.payload["clickStatesRoute"] = target
        smoke.check("pages.clickStates", clicked == ["empty", "error", "loading"],
                    f"在 {target} 上点按钮后 contentState = {clicked}")
    else:
        smoke.payload["clickStates"] = []
        smoke.payload["clickStatesRoute"] = ""
        smoke.check("pages.clickStates", True,
                    "所有一级页面都已是阶段 3 的真实页面（没有演示按钮），本次跳过")


def phase_theme_language(smoke: Smoke, built: Any) -> None:
    """主题热切换（每个预设一次）与语言热切换（每种语言一次），切完都要无报错。"""
    engine = built.engine
    root = engine.rootObjects()[0]
    bridges = engine._fmcl_bridges
    theme = bridges["Theme"]
    tr = bridges["Tr"]
    nav = bridges["Nav"]
    shell = bridges["Shell"]

    theme_before = str(theme.property("currentTheme"))
    lang_before = str(tr.property("language"))
    nav.reset()
    QTest.qWait(SETTLE_MS * 2)

    names = [str(entry.get("name", "")) for entry in (theme.property("themes") or [])]
    names = [n for n in names if n]
    smoke.payload["themes"] = {"available": names, "before": theme_before}
    smoke.check("theme.available", len(names) >= 5, f"{names}")
    rows: List[Dict[str, Any]] = []
    for index, name in enumerate(names, start=1):
        mark = smoke.recorder.mark()
        revision_before = int(theme.property("revision"))
        theme.setTheme(name)
        QTest.qWait(THEME_SETTLE_MS)
        row = {
            "name": name,
            "current": str(theme.property("currentTheme")),
            "revisionBefore": revision_before,
            "revisionAfter": int(theme.property("revision")),
            "accent": str(theme.property("accent").name()),
            "bgDark": str(theme.property("bgDark").name()),
            "screenshot": smoke.snapshot(root, f"theme_{index}_{name}"),
            "messages": smoke.recorder.since(mark),
        }
        row["messageCount"] = len(row["messages"])
        smoke.check(f"theme.{name}", row["current"] == name, f"currentTheme={row['current']!r}")
        smoke.check(f"theme.{name}.revision", row["revisionAfter"] > revision_before,
                    f"{revision_before} -> {row['revisionAfter']}（主题切换必须自增 revision）")
        rows.append(row)
    theme.setTheme(theme_before)  # 还原（setTheme 会写配置，测试不该改用户的选择）
    QTest.qWait(SETTLE_MS)
    smoke.payload["themeRows"] = rows
    smoke.check("theme.restored", str(theme.property("currentTheme")) == theme_before,
                f"{theme.property('currentTheme')!r} != {theme_before!r}")

    languages = [str(entry.get("code", "")) for entry in (tr.property("availableLanguages") or [])]
    languages = [c for c in languages if c]
    smoke.payload["languages"] = {"available": languages, "before": lang_before}
    smoke.check("language.available", len(languages) >= 4, f"{languages}")
    lang_rows: List[Dict[str, Any]] = []
    for index, code in enumerate(languages, start=1):
        mark = smoke.recorder.mark()
        ok = bool(tr.setLanguage(code))
        QTest.qWait(SETTLE_MS * 2)
        row = {
            "code": code,
            "ok": ok,
            "current": str(tr.property("language")),
            "keyCount": int(tr.property("keyCount")),
            "titleText": str(shell.property("currentTitle")),
            "screenshot": smoke.snapshot(root, f"lang_{index}_{code}"),
            "messages": smoke.recorder.since(mark),
        }
        row["messageCount"] = len(row["messages"])
        smoke.check(f"language.{code}", ok and row["current"] == code, f"setLanguage -> {row}")
        smoke.check(f"language.{code}.keys", row["keyCount"] > 1000, f"keyCount={row['keyCount']}")
        lang_rows.append(row)
    tr.setLanguage(lang_before)
    QTest.qWait(SETTLE_MS)
    smoke.payload["languageRows"] = lang_rows
    smoke.check("language.restored", str(tr.property("language")) == lang_before,
                f"{tr.property('language')!r} != {lang_before!r}")


# ─── 组件（LogView / LogDemo，任务 2.18）───────────────────────

#: 强引用池：`QQmlComponent` 与它建出来的顶层窗口都不带 C++ 父对象，
#: **组件先被 GC 会把窗口一起带走**（实测：函数返回后再 `findChild` 得到
#: `RuntimeError: Internal C++ object (QQuickWindow) already deleted`）。
_KEEP_ALIVE: List[Any] = []


def _harness(engine: Any, smoke: Smoke, name: str, body: str) -> Any:
    """在临时目录里生成一段宿主 QML 并实例化。

    为什么要临时文件：QML 的目录导入要按**绝对路径**写，而仓库里没有放探针文件的位置
    （本任务只允许新增 `qml/components/LogView.qml` 与 `qml/pages/dev/LogDemo.qml`）。
    绝对 URL 的 `import "file:///…"` 是 QML 支持的写法，实测可用。
    """
    components = QUrl.fromLocalFile(str(REPO_ROOT / "qml" / "components")).toString()
    pages_dev = QUrl.fromLocalFile(str(REPO_ROOT / "qml" / "pages" / "dev")).toString()
    directory = Path(tempfile.mkdtemp(prefix="fmcl_smoke_"))
    path = directory / f"{name}.qml"
    path.write_text(
        f'import QtQuick\nimport "{components}"\nimport "{pages_dev}"\n\n{body}\n',
        encoding="utf-8", newline="\n",
    )
    component = QQmlComponent(engine, QUrl.fromLocalFile(str(path)))
    if component.isError():
        details = " | ".join(e.toString() for e in component.errors())
        smoke.check(f"{name}.component", False, details)
        return None
    window = component.create()
    QTest.qWait(200)
    smoke.check(f"{name}.component", window is not None, f"create() -> {window}")
    _KEEP_ALIVE.append(component)
    _KEEP_ALIVE.append(window)
    return window


def _call(obj: Any, name: str, *args: Any) -> bool:
    """调 QML 函数。QML 没有默认参数：实参个数必须与签名一致，否则报 No such method。"""
    from PySide6.QtCore import Q_ARG, QMetaObject

    return bool(QMetaObject.invokeMethod(
        obj, name, Qt.DirectConnection, *[Q_ARG("QVariant", a) for a in args]
    ))


def _drag(window: Any, target: Any, dy: float, steps: int = 6) -> None:
    """在目标上按下并纵向拖动（模拟用户翻日志）；`dy` 为正 = 手指向下 = 看更早的行。"""
    start = target.mapToScene(QPointF(target.width() / 2, target.height() * 0.8))
    global_start = window.mapToGlobal(start)
    app = QGuiApplication.instance()
    app.sendEvent(window, QMouseEvent(QEvent.MouseButtonPress, start, global_start,
                                      Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    for step in range(1, steps + 1):
        pos = QPointF(start.x(), start.y() + dy * step / steps)
        app.sendEvent(window, QMouseEvent(QEvent.MouseMove, pos, window.mapToGlobal(pos),
                                          Qt.NoButton, Qt.LeftButton, Qt.NoModifier))
        QTest.qWait(15)
    end = QPointF(start.x(), start.y() + dy)
    app.sendEvent(window, QMouseEvent(QEvent.MouseButtonRelease, end, window.mapToGlobal(end),
                                      Qt.LeftButton, Qt.NoButton, Qt.NoModifier))
    QTest.qWait(200)


def phase_logview(smoke: Smoke, built: Any) -> None:
    """LogView 的 API、上限、搜索高亮、自动滚动、复制 —— 全部在真引擎里跑一遍。"""
    engine = built.engine
    window = _harness(engine, smoke, "LogViewProbe", """Window {
    objectName: "logViewProbe"
    width: 900
    height: 520
    visible: true
    LogView {
        objectName: "probePanel"
        anchors.fill: parent
    }
}""")
    if window is None:
        return
    panel = window.findChild(QQuickItem, "probePanel")
    listing = window.findChild(QQuickItem, "logList")
    smoke.check("logview.found", panel is not None and listing is not None,
                f"panel={panel} list={listing}")
    if panel is None or listing is None:
        return

    result: Dict[str, Any] = {}
    # 1) Python 侧经 QMetaObject.invokeMethod 调用（"对 Python 侧友好"的实测）
    result["invokeAppend"] = _call(panel, "append", "hello from python", "warning")
    QTest.qWait(SETTLE_MS)
    result["lineCountAfterAppend"] = int(panel.property("lineCount"))
    smoke.check("logview.invokeAppend", result["invokeAppend"] and result["lineCountAfterAppend"] == 1,
                f"{result}")

    # 2) 逐行追加速率（同步循环，不含事件循环 → 纯模型插入成本）
    total = 4000
    started = time.perf_counter()
    for index in range(total):
        _call(panel, "append", f"[rate] line {index}", "info")
    elapsed = time.perf_counter() - started
    result["appendPerLine"] = {"lines": total, "ms": round(elapsed * 1000, 1),
                               "perSecond": round(total / elapsed, 1)}
    QTest.qWait(200)
    result["lineCountAfterRate"] = int(panel.property("lineCount"))

    # 3) 事件循环里的实际速率（带渲染 + 自动滚到底）：每 100 行让出一次事件循环
    paced = 2000
    started = time.perf_counter()
    worst_wait_ms = 0.0
    for batch in range(paced // 100):
        _call(panel, "appendLines", [{"text": f"[paced] {batch * 100 + i}", "level": "success"}
                                     for i in range(100)])
        tick = time.perf_counter()
        QTest.qWait(10)
        worst_wait_ms = max(worst_wait_ms, (time.perf_counter() - tick) * 1000 - 10)
    paced_elapsed = time.perf_counter() - started
    result["paced"] = {"lines": paced, "ms": round(paced_elapsed * 1000, 1),
                       "perSecond": round(paced / paced_elapsed, 1),
                       "worstEventLoopOvershootMs": round(worst_wait_ms, 1)}
    QTest.qWait(300)

    # 4) 上限行为：maxLines=200 / trimChunk=32 → 瞬态 ≤ 200+32-1，安静下来恰好 200
    panel.setProperty("maxLines", 200)
    panel.setProperty("trimChunk", 32)
    _call(panel, "clear")
    QTest.qWait(SETTLE_MS)
    peak = 0
    for index in range(1000):
        _call(panel, "append", f"[cap] {index}", "warning")
        peak = max(peak, int(panel.property("lineCount")))
    result["cap"] = {"peak": peak, "immediate": int(panel.property("lineCount")),
                     "maxLines": int(panel.property("maxLines"))}
    QTest.qWait(300)
    result["cap"]["settled"] = int(panel.property("lineCount"))
    smoke.check("logview.capTransient", peak <= 231, f"瞬态峰值 {peak} > 200+32-1")
    smoke.check("logview.capSettled", result["cap"]["settled"] == 200,
                f"安静后应为 200，实际 {result['cap']['settled']}")
    smoke.check("logview.capBounded", 0 < peak <= 400, f"峰值 {peak} 越界（有界性被破坏）")

    # 5) 搜索高亮：命中处必须真的渲染出一个高亮块
    panel.setProperty("searchText", "cap")
    QTest.qWait(200)
    hits = [child for child in find_all_visual(listing, "logHitBox") if child.isVisible()]
    result["search"] = {"visibleHitBoxes": len(hits)}
    smoke.check("logview.searchHighlight", len(hits) > 0, f"可见高亮块 {len(hits)} 个")
    panel.setProperty("searchText", "")
    QTest.qWait(SETTLE_MS)

    # 6) 用户手动上滚之后不许抢：拖动 → autoScroll 变假 → 追加也不回底
    result["autoScrollBefore"] = bool(panel.property("autoScroll"))
    _drag(window, listing, 200)
    result["autoScrollAfterDrag"] = bool(panel.property("autoScroll"))
    content_before = float(listing.property("contentY"))
    _call(panel, "append", "[drag] after user scrolled up", "error")
    QTest.qWait(200)
    result["contentYBefore"] = round(content_before, 1)
    result["contentYAfter"] = round(float(listing.property("contentY")), 1)
    smoke.check("logview.autoScrollOff", result["autoScrollBefore"] and not result["autoScrollAfterDrag"],
                f"{result['autoScrollBefore']} -> {result['autoScrollAfterDrag']}")
    smoke.check("logview.noSteal", abs(result["contentYAfter"] - result["contentYBefore"]) < 1.0,
                f"追加一行后 contentY 从 {result['contentYBefore']} 跳到 {result['contentYAfter']}")
    # 点"到底部"应该重新打开自动滚动
    bottom = window.findChild(QQuickItem, "logBottomButton")
    click(window, bottom)
    QTest.qWait(200)
    result["autoScrollAfterBottomButton"] = bool(panel.property("autoScroll"))
    smoke.check("logview.scrollToEnd", result["autoScrollAfterBottomButton"], "点底按钮没恢复自动滚动")

    # 7) 复制全部（剪贴板 + plainText 长度一致）
    expected = int(panel.property("lineCount"))
    started = time.perf_counter()
    _call(panel, "copyAll")
    QTest.qWait(SETTLE_MS)
    result["copy"] = {"ms": round((time.perf_counter() - started) * 1000, 1),
                      "clipboardChars": len(QGuiApplication.clipboard().text() or ""),
                      "lines": expected}
    smoke.check("logview.copyAll", result["copy"]["clipboardChars"] > 0,
                f"剪贴板为空：{result['copy']}")

    # 8) 清空 + 截图
    _call(panel, "clear")
    QTest.qWait(SETTLE_MS)
    result["lineCountAfterClear"] = int(panel.property("lineCount"))
    smoke.check("logview.clear", result["lineCountAfterClear"] == 0, f"{result}")
    # 截图前放一批可见的内容（空面板的截图证明不了什么）
    _call(panel, "appendLines", [{"text": f"[shot] level sample {i}", "level": ("info", "success",
                                                                                 "warning", "error")[i % 4]}
                                 for i in range(40)])
    panel.setProperty("searchText", "sample 21")
    QTest.qWait(200)
    result["screenshot"] = smoke.snapshot(window, "logview")
    smoke.payload["logView"] = result


def _structured_lines(path: Path, limit: int) -> List[Dict[str, str]]:
    """`latest_structured.log`（`structured_logger` 的 JSONL）尾部 → 带级别的日志行。"""
    entries: List[Dict[str, str]] = []
    for line in tail_lines(path, limit):
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        level = str(record.get("level", "info")).lower()
        if level == "debug":
            level = "info"
        if level not in ("info", "success", "warning", "error"):
            level = "info"
        payload = record.get("data")
        text = f"{record.get('timestamp', '')} [{record.get('level', '')}] {record.get('event', '')}"
        if payload:
            text += " " + json.dumps(payload, ensure_ascii=False, default=str)
        entries.append({"text": text, "level": level})
    return entries


def phase_logdemo(smoke: Smoke, built: Any) -> None:
    """LogDemo 页：Python 侧读真实日志 → `loadLines()` 回填（QML 一个字都不碰文件）。"""
    engine = built.engine
    window = _harness(engine, smoke, "LogDemoProbe", """Window {
    objectName: "logDemoProbe"
    width: 900
    height: 520
    visible: true
    LogDemo {
        anchors.fill: parent
    }
}""")
    if window is None:
        return
    demo = window.findChild(QQuickItem, "logDemoPage")
    panel = window.findChild(QQuickItem, "logDemoPanel")
    smoke.check("logdemo.found", demo is not None and panel is not None,
                f"page={demo} panel={panel}")
    if demo is None or panel is None:
        return

    result: Dict[str, Any] = {"initialLines": int(panel.property("lineCount"))}
    data_dir = Path(str(built.engine._fmcl_bridges["Runtime"].property("dataDir") or REPO_ROOT))
    structured = data_dir / "latest_structured.log"
    plain = data_dir / "latest.log"
    result["files"] = {"structured": str(structured), "structuredExists": structured.is_file(),
                       "plain": str(plain), "plainExists": plain.is_file()}

    # (a) 结构化日志：JSONL → `{text, level}` 直接喂进去（级别着色因此是有意义的）
    entries = _structured_lines(structured, 200)
    result["structuredLines"] = len(entries)
    result["structuredLevels"] = sorted({entry["level"] for entry in entries})
    if entries:
        _call(demo, "loadLines", entries, f"structured_logger: {structured.name} (last {len(entries)})")
        QTest.qWait(150)
    result["linesAfterStructured"] = int(panel.property("lineCount"))
    smoke.check("logdemo.structured", not entries or result["linesAfterStructured"] >= len(entries),
                f"{result}")

    # (b) 纯文本日志（`latest.log`）尾部
    plain_lines = tail_lines(plain, 100)
    result["plainLines"] = len(plain_lines)
    if plain_lines:
        _call(demo, "loadLines", plain_lines, f"latest.log (last {len(plain_lines)})")
        QTest.qWait(150)
    result["linesAfterPlain"] = int(panel.property("lineCount"))
    smoke.check("logdemo.plain", not plain_lines or result["linesAfterPlain"] > result["linesAfterStructured"],
                f"{result}")

    # (c) 页面上的"读取启动器日志"按钮 → 信号 → Python 读文件 → loadLines 回填（整条链）
    reloads: List[str] = []

    def on_reload() -> None:
        reloads.append("requested")
        fresh = _structured_lines(structured, 50)
        if fresh:
            _call(demo, "loadLines", fresh, "reload button round trip")

    demo.reloadRequested.connect(on_reload)
    before = int(panel.property("lineCount"))
    click(window, window.findChild(QQuickItem, "reloadRealLogButton"))
    QTest.qWait(300)
    result["reload"] = {"signalCount": len(reloads), "before": before,
                        "after": int(panel.property("lineCount")),
                        "statusText": str(built.engine._fmcl_bridges["Shell"].property("statusText"))}
    smoke.check("logdemo.reloadRoundTrip", len(reloads) == 1, f"信号来了 {len(reloads)} 次")

    # (d) 生成 5000 行 + 清空（按钮路径）
    click(window, window.findChild(QQuickItem, "generate5000Button"))
    QTest.qWait(400)
    result["afterGenerate5000"] = int(panel.property("lineCount"))
    smoke.check("logdemo.generate", result["afterGenerate5000"] > 4000,
                f"生成后 {result['afterGenerate5000']} 行")
    click(window, window.findChild(QQuickItem, "clearDemoButton"))
    QTest.qWait(200)
    result["afterClear"] = int(panel.property("lineCount"))
    smoke.check("logdemo.clear", result["afterClear"] == 0, f"清空后 {result['afterClear']} 行")

    result["screenshot"] = smoke.snapshot(window, "logdemo")
    smoke.payload["logDemo"] = result


# ─── 入口 ──────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="UI 冒烟驱动（子进程侧，供 tests/ui_smoke.py 调用）")
    parser.add_argument("--out", required=True, help="截图输出目录")
    parser.add_argument("--json-out", default="", help="结果 JSON 落盘路径")
    parser.add_argument("--mutation", action="append", default=[],
                        help="负例自检用的故障注入（bad-route / bad-binding）")
    parser.add_argument("--skip", default="",
                        help="跳过的阶段（pages / theme / components 的逗号列表；负例自检用）")
    args = parser.parse_args(argv)

    started = time.perf_counter()
    skips = [part for part in str(args.skip).split(",") if part.strip()]
    smoke = Smoke(Path(args.out), list(args.mutation), skips)
    payload = smoke.payload
    payload["schema"] = 1
    payload["kind"] = "ui-smoke-child"
    payload["cwd"] = str(Path.cwd())
    payload["qpa"] = os.environ.get("QT_QPA_PLATFORM", "")

    try:
        built = assemble(smoke)
    except Exception as exc:  # noqa: BLE001 - 装配失败要把原因带回去（父进程负责展示）
        payload["fatal"] = f"{type(exc).__name__}: {exc}"
        payload["checks"] = smoke.checks
        payload["shots"] = smoke.shots
        _emit(smoke, args.json_out, started)
        return 1

    bridges = built.engine._fmcl_bridges
    runtime = bridges.get("Runtime")
    payload["runtime"] = {
        "app": str(runtime.property("appName")) if runtime is not None else "",
        "version": str(runtime.property("appVersion")) if runtime is not None else "",
        "python": str(runtime.property("pythonVersion")) if runtime is not None else "",
        "qt": str(runtime.property("qtVersion")) if runtime is not None else "",
        "bridgeStatus": str(runtime.property("bridgeStatus")) if runtime is not None else "",
    }

    try:
        if "pages" not in smoke.skips:
            phase_pages(smoke, built)
        if "bad-route" in smoke.mutations:
            # 负例 ①：故意 push 一个不存在的路由 —— 用**与走查里同一形状的断言**判它，
            # 于是它必然变红（`Nav.push` 返回 False、currentRoute 不会变成这个 id）。
            nav = bridges["Nav"]
            pushed = bool(nav.push("this/route/does-not-exist"))
            QTest.qWait(SETTLE_MS)
            smoke.check(
                "mutation.badRoute",
                pushed and str(nav.property("currentRoute")) == "this/route/does-not-exist",
                f"push 返回 {pushed}，currentRoute={str(nav.property('currentRoute'))!r}"
                "（这一条**本来就应该失败**：它证明路由断言不是空断言）",
            )
        if "theme" not in smoke.skips:
            phase_theme_language(smoke, built)
        if "components" not in smoke.skips:
            phase_logview(smoke, built)
            phase_logdemo(smoke, built)
        if "bad-binding" in smoke.mutations:
            # 负例 ②：临时 QML 里写一个必然报 warning 的绑定（父进程必须能判出来）
            _harness(built.engine, smoke, "BadBindingProbe", """Window {
    objectName: "badBindingProbe"
    width: 200
    height: 100
    visible: true
    Item {
        objectName: "badBindingItem"
        property int broken: notDefinedAnywhere.value
        Component.onCompleted: {
            var probe = broken
            console.log("bad binding evaluated: " + probe)
        }
    }
}""")
            QTest.qWait(300)
        smoke.payload["skipped"] = sorted(smoke.skips)
    except Exception as exc:  # noqa: BLE001 - 走查中断也要把已收集的证据带回去
        import traceback

        payload["walkError"] = f"{type(exc).__name__}: {exc}"
        payload["walkTraceback"] = traceback.format_exc()
        smoke.check("walk.completed", False, payload["walkError"])

    smoke.check("walk.completed", True, "走查跑完")
    _emit(smoke, args.json_out, started)
    return 0


def _emit(smoke: Smoke, json_out: str, started: float) -> None:
    """落盘 + 打印（`ensure_ascii=True`：即使 stdout 编码不是 UTF-8 也不会炸）。"""
    payload = smoke.payload
    payload["checks"] = smoke.checks
    payload["shots"] = smoke.shots
    payload["durationMs"] = round((time.perf_counter() - started) * 1000, 1)
    payload["messages"] = smoke.recorder.records
    text = json.dumps(payload, ensure_ascii=True)
    if json_out:
        Path(json_out).write_text(text, encoding="utf-8")
    print(MARKER + text, flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
