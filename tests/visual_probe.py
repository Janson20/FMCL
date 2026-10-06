"""视觉回归探针（`tests/test_visual_regression.py` 用的**子进程**驱动）。

## 为什么必须另起进程

与 `tests/qml_shell_probe.py` / `tests/_smoke_driver.py` 同一个理由（那边是踩出来的）：
`QQmlApplicationEngine` + FluentUI 的 `FluWindow` 在同一个进程里**跨测试文件不安全**。
本探针还要抓帧，比只读属性更挑：同一进程里第二次建引擎时抓到的可能是上一帧的缓冲。

## 它采集什么（判定全在父测试里）

`tests/visual_metrics.py` 定义了"像素判据"（令牌命中 / 浅色外来件 / 成片 / 对比度），
本文件只负责**把帧变成那些量**，连同 PNG 一起落盘：

* `pages`：12 个一级领域页各一帧 + **页头图标的定点像素**（图标必须是主题文字色、
  不许是黑 —— D-141 / D-144 的像素级证据，原来只有属性字符串那条）；
* `states`：首页的三态各一帧（三态渲染只有一份实现，所以只测一页；12 页 × 3 态的
  属性级证据在 `tests/ui_smoke.py` 里）；
* `themes`：5 个预设各一帧（**每个预设的强调色都要真的出现在屏幕上**才是"切过去了"）；
* `gallery`：组件画廊的顶/底两帧（26 个组件同时在场，是最容易冒出外来浅色件的地方）；
* `mutation`：同一块深色底上，原生 `Button`（Basic 样式）与自研 `FmButton` 各一帧 ——
  R9 的判据文本写着"原生控件在深色界面里必然是浅色的外来件"，这里把它量出来；
* `determinism`：同一状态连抓两次是否逐字节相同。

## 主题会被**钉住**（并且还原）

基线要和机器、和开发者的个人配置无关，所以本探针先把主题钉到预设 `default`、
把自定义强调色清空（`setAccent("")`），跑完在 `finally` 里还原主题名与强调色。
不这么做的话：用户自定义的强调色会让 `accent` 命中数与按钮颜色随人而变，
基线就成了一张"只在我机器上成立"的表。

跑法（父测试自动调）::

    .\\.venv\\Scripts\\python.exe -X utf8 tests\\visual_probe.py --out <目录>

输出：一行 `PROBE_JSON:{...}`；退出码 0 = 走完（不代表全绿，判据在父进程）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
# 缺陷 D-150：QML 磁盘缓存那条**异步取编译单元**的路径会让主题切换时的对象创建报
# `TypeError` / `Cannot find member data`（屏幕无可见损伤）。本探针也要切 5 个主题，
# 所以和 `_smoke_driver.py` 用同一个规避（理由与复现方式写在那个文件里）。
os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from PySide6.QtCore import QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlComponent  # noqa: E402

# 必须在任何 QML 对象之前 import：否则 QQuickWindow 不在 PySide6 的类型表里，
# 根窗口会被包成 QWindow，`grabWindow()` 直接 AttributeError（见 `_smoke_driver.py` 文件头）。
from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402,F401
from PySide6.QtTest import QTest  # noqa: E402
from visual_metrics import color_from_hex, measure, relative_luminance  # noqa: E402

import main_qml  # noqa: E402
from app.bridges.icon_provider import install as install_icon_provider  # noqa: E402
from app.bridges.theme_bridge import COLOR_KEYS, TOKEN_KEYS  # noqa: E402

MARKER = "PROBE_JSON:"
#: 钉住的基线与主题（见文件头）。
BASELINE_THEME = "default"
#: 三态的名字（页面骨架 `FmPage` 的 `contentState` 取值）。
STATES = ("loading", "empty", "error")
#: 三态 → （状态件根对象名, 图标对象名）。与 `tests/_smoke_driver.py:STATE_PARTS` 同一份。
STATE_PARTS = {
    "loading": ("fmLoadingState", "fmLoadingStateIcon"),
    "empty": ("fmEmptyState", "fmEmptyStateIcon"),
    "error": ("fmErrorState", "fmErrorStateIcon"),
}
#: 令牌名：12 个主题色 + 15 个派生令牌（它们出现在屏幕上都算"合法"）。
TOKEN_NAMES = tuple(name for name, _ in COLOR_KEYS) + tuple(TOKEN_KEYS)
#: 组件画廊的路由（level 1，靠 `Nav.push` 进去）。
GALLERY_ROUTE = "dev/gallery"
#: 页头图标的定点像素判据：图标是 18px 的字号，抗锯齿会把颜色摊开，
#: 所以用较宽的容差（48）判"接近文字色"，再用"近黑像素"判 D-141 的回归。
ICON_TOL = 48
#: 等一帧的毫秒数（够绑定求值 + 渲染一帧）。
SETTLE_MS = 60

KEEP_ALIVE: List[Any] = []


class Probe:
    """一次探针运行的上下文（引擎、窗口、桥、输出目录、错误）。"""

    def __init__(self, out_dir: Path) -> None:
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.errors: List[str] = []
        self.payload: Dict[str, Any] = {}
        self.shots: List[str] = []

    def fail(self, where: str, detail: str) -> None:
        self.errors.append(f"{where}: {detail}")

    def shot(self, image: Any, name: str, record: Dict[str, Any]) -> None:
        """把**已经量过的那一帧**落盘（人复查用；判据不读文件，只看 JSON 里的量）。"""
        path = self.out_dir / f"{name}.png"
        try:
            if image.isNull() or not image.save(str(path)):
                self.fail(name, "抓帧为空或保存失败")
                return
        except Exception as exc:  # noqa: BLE001 - 落盘失败不该打断采集
            self.fail(name, f"保存抛异常 {exc}")
            return
        self.shots.append(path.name)
        record["file"] = path.name


# ─── 装配（与 tests/qml_shell_probe.py 同一做法：手装，不跑启动流程） ──────


def item(name: str, root: Any = None) -> Any:
    node = root if root is not None else ROOT
    return node.findChild(QQuickItem, name) if node is not None else None


def settle(root: Any, timeout_ms: int = 2000) -> int:
    """等 `StackView` 的过渡走完（过渡期间新旧两页同时在屏上，抓帧会抓到一半一半）。"""
    stack = item("pageStack", root)
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


def goto(route: str) -> None:
    """回栈底再压入目标路由（每页都从同一起点开始）。"""
    NAV.reset()
    QTest.qWait(SETTLE_MS)
    NAV.push(route)
    settle(ROOT)
    QTest.qWait(SETTLE_MS * 2)


def tokens_now() -> Dict[str, str]:
    table = {}
    for name in TOKEN_NAMES:
        value = THEME.property(name)
        if value is not None:
            table[name] = value.name()
    return table


def patch(probe: Probe, window: Any, node: Any, tokens: Dict[str, str]) -> Dict[str, Any]:
    """定点像素：某个 QQuickItem 覆盖的那块区域里，各种像素各有多少。

    为什么不能只看整帧统计：页头图标是 18px 的细笔画，降采样 4 倍之后整块的平均色
    已经和背景差不多了 —— 要证明"图标真的画成了主题文字色、不是黑"，
    必须回**原始分辨率**去数那一小块（实测一小块 18x18 = 324 像素，够用且几乎不花时间）。
    """
    if node is None or window is None:
        return {"found": False}
    image = window.grabWindow()
    scale = image.width() / max(1, window.width())
    origin = node.mapToScene(QPointF(0, 0))
    x0 = max(0, int(round(origin.x() * scale)))
    y0 = max(0, int(round(origin.y() * scale)))
    width = max(1, int(round(node.width() * scale)))
    height = max(1, int(round(node.height() * scale)))
    wanted = color_from_hex(tokens.get("textPrimary", "#ffffff"))
    near_text = near_black = opaque = 0
    for y in range(y0, min(y0 + height, image.height())):
        for x in range(x0, min(x0 + width, image.width())):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() < 250:
                continue
            opaque += 1
            rgb = (pixel.red(), pixel.green(), pixel.blue())
            if sum(abs(rgb[i] - wanted[i]) for i in range(3)) <= ICON_TOL:
                near_text += 1
            if max(rgb) <= 40:
                near_black += 1
    return {
        "found": True, "rect": [x0, y0, width, height], "width": round(node.width(), 1),
        "visible": bool(node.isVisible()), "opaque": opaque,
        "nearTextPrimary": near_text, "nearBlack": near_black,
        "lumaOfText": round(relative_luminance(wanted), 3),
    }


# ─── 装配（模块导入即完成；与 tests/qml_shell_probe.py 同一做法：手装，不跑启动流程） ──
#
# 为什么不用 `main_qml.assemble()`：那一步会获取单实例锁（并行跑测试时互相打架，
# 见 `_smoke_driver.isolate_single_instance` 的说明）并写日志。本探针只要"引擎 + 桥 +
# 一个真的 App.qml 窗口"，手装更可控，也和 `tests/qml_shell_probe.py` 保持一致。

APP = QGuiApplication.instance() or QGuiApplication([])
#: 真配置的写盘也要挡住：本探针走生产桥装配路径，手里那份 `config` 就是根模块单例 ——
#: 不挡的话跑一轮探针就会把开发机的 `config.json` 改写掉（语言/强调色都中过招）。
from config_isolation import isolate_config_writes  # noqa: E402

isolate_config_writes("visual-probe")

SINK = main_qml.install_message_handler()
ENGINE = main_qml.build_engine()
# 图标上色 provider 必须注册：不注册的话每个 `image://fmcl-icon/…` 都画不出东西，
# 于是"页头图标的像素"这一条会被自己的噪声判红。
install_icon_provider(ENGINE)
main_qml.register_bridges(ENGINE, None)
BRIDGES = ENGINE._fmcl_bridges
THEME = BRIDGES["Theme"]
NAV = BRIDGES["Nav"]
SHELL = BRIDGES["Shell"]
#: 跑之前的用户选择（跑完要还原；`setTheme` / `setAccent` 都会写配置）。
ORIGINAL_THEME = str(THEME.property("currentTheme"))
ORIGINAL_ACCENT = THEME.property("accent").name()
# 钉住基线：清掉自定义强调色（空串 = 回到主题自带的强调色），再切到预设 default。
THEME.setAccent("")
THEME.setTheme(BASELINE_THEME)
ENGINE.load(QUrl.fromLocalFile(str(main_qml.app_qml_path("App.qml"))))
ROOT = ENGINE.rootObjects()[0] if ENGINE.rootObjects() else None
QTest.qWait(400)


def restore_theme() -> None:
    """还原用户原来的主题与强调色（先清自定义色，再按需写回 —— 见 `setAccent` 的语义）。"""
    try:
        THEME.setTheme(ORIGINAL_THEME)
        THEME.setAccent("")
        if THEME.property("accent").name() != ORIGINAL_ACCENT:
            THEME.setAccent(ORIGINAL_ACCENT)
    except Exception as exc:  # noqa: BLE001 - 还原失败不该盖掉真正的结果
        print(f"还原主题失败：{exc}", file=sys.stderr)


# ─── 采集 ──────────────────────────────────────────────────────


def grab(probe: Probe, name: str, tokens: Dict[str, str]) -> Dict[str, Any]:
    """抓一帧 → 量 → 落盘。**量的是落盘的那一张**（动画在跑时两次抓帧会不一样）。"""
    image = ROOT.grabWindow()
    record = measure(image, tokens)
    probe.shot(image, name, record)
    return record


def current_page() -> Any:
    stack = item("pageStack")
    return stack.property("currentItem") if stack is not None else None


def section_pages(probe: Probe, tokens: Dict[str, str]) -> List[Dict[str, Any]]:
    """12 个一级领域页：整帧统计 + 页头图标的定点像素。"""
    rows: List[Dict[str, Any]] = []
    for entry in list(SHELL.navItems()):
        route = str(entry["id"])
        started = time.perf_counter()
        goto(route)
        page = current_page()
        frame = grab(probe, f"page_{route.replace('/', '_')}", tokens)
        rows.append({
            "route": route,
            "objectName": page.objectName() if page is not None else "",
            "ms": round((time.perf_counter() - started) * 1000, 1),
            "frame": frame,
            "icon": patch(probe, ROOT, item("pageIcon", page), tokens),
        })
        if page is None:
            probe.fail(f"page.{route}", "pageStack 上没有当前页")
    return rows


def section_states(probe: Probe, tokens: Dict[str, str]) -> List[Dict[str, Any]]:
    """首页的三态各一帧（三态渲染只有一份实现，所以只测一页）。"""
    goto("home")
    page = current_page()
    if page is None:
        probe.fail("states", "首页没拿到")
        return []
    rows: List[Dict[str, Any]] = []
    for state in STATES:
        page.setProperty("contentState", state)
        QTest.qWait(SETTLE_MS * 2)
        root_name, icon_name = STATE_PARTS[state]
        node = item(root_name, page)
        rows.append({
            "state": state,
            "shown": bool(node is not None and node.isVisible()),
            "frame": grab(probe, f"state_{state}", tokens),
            "icon": patch(probe, ROOT, item(icon_name, page), tokens),
        })
    page.setProperty("contentState", "ready")
    QTest.qWait(SETTLE_MS)
    rows.append({"state": "ready", "shown": True,
                 "frame": grab(probe, "state_ready", tokens), "icon": {"found": False}})
    page.setProperty("contentState", "loading")
    QTest.qWait(SETTLE_MS)
    return rows


def section_themes(probe: Probe) -> List[Dict[str, Any]]:
    """5 个预设各一帧，**各用各的令牌表**量（切过去了就要看到那个主题的颜色）。"""
    rows: List[Dict[str, Any]] = []
    names = [str(entry.get("name", "")) for entry in (THEME.property("themes") or [])]
    for name in [n for n in names if n]:
        THEME.setTheme(name)
        QTest.qWait(300)
        table = tokens_now()
        rows.append({"name": name, "tokens": table,
                     "frame": grab(probe, f"theme_{name}", table)})
    THEME.setTheme(BASELINE_THEME)
    QTest.qWait(300)
    return rows


def section_gallery(probe: Probe, tokens: Dict[str, str]) -> List[Dict[str, Any]]:
    """画廊的顶/底两帧：26 个组件同时在场，最容易冒出外来浅色件。

    顺带**数这一段的 QML 报错**：画廊不在 `Shell.navItems()` 里（它是 level 1 的开发页），
    冒烟测试的 12 页走查从不经过它 —— 第一次真的进来，就发现它每次都在刷
    `TypeError`（缺陷 D-145：`Repeater` 委托根读 `parent.width/height`）。
    """
    before = len(SINK.messages)
    goto(GALLERY_ROUTE)
    rows = [{"name": "top", "frame": grab(probe, "gallery_top", tokens)}]
    page = current_page()
    scroll = item("galleryScroll", page)
    flick = scroll.property("contentItem") if scroll is not None else None
    if flick is None:
        probe.fail("gallery", "拿不到画廊的滚动内容（contentItem 为空）")
        return rows
    bottom = float(flick.property("contentHeight")) - float(flick.property("height"))
    flick.setProperty("contentY", max(0.0, bottom))
    QTest.qWait(SETTLE_MS * 3)
    rows.append({"name": "bottom", "contentY": round(float(flick.property("contentY")), 1),
                 "frame": grab(probe, "gallery_bottom", tokens)})
    messages = [str(text).splitlines()[0] for text in SINK.messages[before:]]
    for row in rows:
        row["messages"] = messages
    return rows


#: 视觉回归里要额外走一遍的**二级路由**（阶段 3 任务 3.5）。
#: 一级页面清单来自 `Shell.navItems()`，二级路由一律不在里面 —— 所以"设置页里的
#: 账号分区"这种内容从来不会被像素级判据扫到。每个条目是
#: `(路由, 用来确认分区真的渲染了的 objectName)`。
SUB_ROUTES: Tuple[Tuple[str, str], ...] = (
    ("settings/account", "accountTitle"),
)


def section_sub_routes(probe: Probe, tokens: Dict[str, str]) -> List[Dict[str, Any]]:
    """二级路由各一帧 + 关键控件在场判据（`tests/test_visual_regression.py` 据此断言）。

    顺带**数这一段的 QML 报错**：与 `section_gallery` 同一个理由 —— 这些路由不在
    12 个一级页面里，冒烟测试走查从不经过它们。
    """
    before = len(SINK.messages)
    rows: List[Dict[str, Any]] = []
    for route, marker in SUB_ROUTES:
        goto(route)
        page = current_page()
        node = item(marker, page)
        row = {
            "route": route,
            "marker": marker,
            "rendered": bool(node is not None and node.isVisible()),
            "objectName": page.objectName() if page is not None else "",
            "frame": grab(probe, f"sub_{route.replace('/', '_')}", tokens),
        }
        rows.append(row)
        if not row["rendered"]:
            probe.fail(f"subRoute.{route}", f"分区没渲染出来（找不到可见的 {marker}）")
    messages = [str(text).splitlines()[0] for text in SINK.messages[before:]]
    for row in rows:
        row["messages"] = messages
    return rows


#: 突变对照的宿主 QML：同一块深色底，一个用自研件，一个用原生件（**故意的负例**）。
MUTATION_BODY = """Window {
    objectName: "%(name)s"
    width: 420
    height: 200
    visible: true
    color: "%(bg)s"
    %(widget)s {
        objectName: "widget"
        anchors.centerIn: parent
        width: 160
        height: 36
        text: "Confirm"
    }
}"""


def mutation_window(probe: Probe, name: str, widget: str, background: str,
                    extra_import: str = "") -> Any:
    """在临时目录里生成宿主 QML 并实例化（与 `tests/_smoke_driver.py:_harness` 同一做法）。

    临时文件不落在 `qml/` 下 —— 这里的原生 `Button` 是**故意的负例**，
    闸门 R9 管的是产品代码（`qml/**`），不该被这里的对照样本判红。
    """
    components = QUrl.fromLocalFile(str(REPO_ROOT / "qml" / "components")).toString()
    directory = probe.out_dir / f"harness_{name}"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{name}.qml"
    path.write_text(
        f'import QtQuick\nimport "{components}"\n{extra_import}\n'
        + MUTATION_BODY % {"name": name, "bg": background, "widget": widget},
        encoding="utf-8", newline="\n",
    )
    component = QQmlComponent(ENGINE, QUrl.fromLocalFile(str(path)))
    if component.isError():
        probe.fail(f"mutation.{name}", " | ".join(e.toString() for e in component.errors()))
        return None
    window = component.create()
    KEEP_ALIVE.extend([component, window])
    QTest.qWait(400)
    return window


def section_mutation(probe: Probe, tokens: Dict[str, str]) -> Dict[str, Any]:
    """R9 的像素证据：同一场景下原生控件与自研件的"浅色外来成片"数对比。"""
    background = tokens.get("windowBg", "#1a1a2e")
    out: Dict[str, Any] = {}
    for name, widget, extra in (("FmButton", "FmButton", ""),
                                ("native", "Button", "import QtQuick.Controls")):
        window = mutation_window(probe, name, widget, background, extra)
        if window is None:
            continue
        node = item("widget", window)
        image = window.grabWindow()
        record = measure(image, tokens)
        if node is not None:
            record["widgetSize"] = [round(float(node.width()), 1), round(float(node.height()), 1)]
        path = probe.out_dir / f"mutation_{name}.png"
        if not image.isNull() and image.save(str(path)):
            probe.shots.append(path.name)
            record["file"] = path.name
        out[name] = record
    return out


def raw_bytes(window: Any) -> bytes:
    """抓帧 → 原始缓冲的 `bytes`。

    **必须先把 QImage 存进变量**：写成 `bytes(window.grabWindow().constBits())` 会让
    `QImage` 变成临时对象，而 `constBits()` 交出去的 memoryview **不持有它的所有权** ——
    实测第二次抓帧时进程以 `exit=-1073741819`（访问违例）硬崩
    （`poc/_probe_grab_lifetime.py` 的 A/B/C 三个变体对照：行内临时对象崩、
    先绑变量或用 `bits()` 都不崩）。
    """
    image = window.grabWindow()
    return bytes(image.constBits())


def section_determinism(probe: Probe) -> Dict[str, Any]:
    """同一状态连抓两次：静态页面必须**逐字节相同**，加载态则**故意不同**（转圈动画）。

    为什么分两种：返工 C 组之后页面默认是 `contentState: "loading"`，而
    `FmLoadingState` 里有一个会转的进度环 —— 像素每时每刻都在变，那不是缺陷。
    所以判据写成两条：静态（ready）必须稳定（否则"基线"这件事根本不成立），
    加载态**必须**在动（否则说明动画死了 —— 那才是缺陷）。
    """
    out: Dict[str, Any] = {}
    goto("versions")
    page = current_page()
    for name, state, expect_same in (("ready", "ready", True), ("loading", "loading", False)):
        if page is not None:
            page.setProperty("contentState", state)
            QTest.qWait(400)
        first = raw_bytes(ROOT)
        QTest.qWait(150)
        second = raw_bytes(ROOT)
        same = first == second
        waited = 0
        while expect_same and not same and waited < 1200:
            QTest.qWait(300)
            waited += 300
            second = raw_bytes(ROOT)
            same = first == second
        out[name] = {"identical": bool(same), "expectedIdentical": expect_same,
                     "retryWaitMs": waited, "bytes": len(first)}
    if page is not None:
        page.setProperty("contentState", "loading")
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="视觉回归探针（像素级）")
    parser.add_argument("--out", default=str(REPO_ROOT / "poc" / "review" / "d_group" / "visual"))
    parser.add_argument("--only", default="",
                        help="只跑某几段（逗号分隔：pages,states,themes,gallery,subroutes,mutation,determinism）")
    parser.add_argument("--json", default="", help="把 JSON 另存一份（UTF-8，无 BOM）")
    args = parser.parse_args(argv)
    only = {part.strip() for part in args.only.split(",") if part.strip()}

    def wanted(name: str) -> bool:
        """没给 `--only` 就跑全部；给了就只跑点名的那几段（调参时省时间）。"""
        return not only or name in only

    probe = Probe(Path(args.out))
    started = time.perf_counter()
    try:
        if ROOT is None:
            probe.fail("engine", "App.qml 没有建出根对象")
        else:
            tokens = tokens_now()
            probe.payload["tokens"] = tokens
            if wanted("pages"):
                probe.payload["pages"] = section_pages(probe, tokens)
            if wanted("states"):
                probe.payload["states"] = section_states(probe, tokens)
            if wanted("themes"):
                probe.payload["themes"] = section_themes(probe)
            if wanted("gallery"):
                probe.payload["gallery"] = section_gallery(probe, tokens)
            if wanted("subroutes"):
                probe.payload["subRoutes"] = section_sub_routes(probe, tokens)
            if wanted("mutation"):
                probe.payload["mutation"] = section_mutation(probe, tokens)
            if wanted("determinism"):
                probe.payload["determinism"] = section_determinism(probe)
    finally:
        restore_theme()
    probe.payload.update({
        "ok": not probe.errors,
        "errors": probe.errors,
        "platform": os.environ.get("QT_QPA_PLATFORM", ""),
        "style": os.environ.get("QT_QUICK_CONTROLS_STYLE", ""),
        "pinned": {"theme": BASELINE_THEME, "accentOverride": "cleared"},
        "seconds": round(time.perf_counter() - started, 1),
        "shots": probe.shots,
        "messages": [str(m).splitlines()[0] for m in SINK.messages],
    })
    payload_text = json.dumps(probe.payload, ensure_ascii=False)
    if args.json:
        # 为什么另存一份：PowerShell 的 `*>` 重定向写出的是 **UTF-16LE**（本仓库踩过的坑：
        # `>` / `Out-File` 带 BOM 或换编码，下游按 utf-8 读就找不到标记行）。
        # 落一份明确的 UTF-8/无 BOM 文件，人复查与脚本标定都省事。
        Path(args.json).write_bytes(payload_text.encode("utf-8"))
    print(MARKER + payload_text)
    return 0 if not probe.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
