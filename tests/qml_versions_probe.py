"""版本页 QML 探针（阶段 3 任务 3.2；`tests/test_versions_page_qml.py` 用**子进程**驱动）。

## 为什么另起进程

与 `tests/qml_home_probe.py` 同一个理由：真引擎 + FluentUI 的 `FluWindow` 在同一个进程里
跨测试文件不安全。这里只加载一次 `App.qml`。

## 它验证什么

`qml/pages/versions/VersionsPage.qml` 是阶段 3 的第二个真实页面，探针要证明六件事：

1. **三态真的会切**：没有版本 → empty、有版本 → ready、桥缺席 → error；
2. **列表真的渲染出来**：行数 = 服务给的行数，显示文本就是服务拼的那一份（B-01）；
3. **点击 → 桥 → 服务**：选中/详情/模组/资源管理/重命名/删除/刷新/启动/强杀九个动作
   都落到注入的假服务上（不是页面自己另写一套）；
4. **搜索与排序真的接上了**（B-19）：在搜索框里打字、改排序下拉，列表跟着变；
5. **详情页真的显示服务给的字段**（B-20）：路径 / 所需 Java / 模组数量；
6. **语言热切换**：整页文案跟着 `Tr.setLanguage` 变，不留硬编码。

## 假服务为什么必须走 `AppContext`

桥只认 `context.try_get("version")` 这一条取数路径（红线 2）。用 `build_context()` 建一个
真上下文、再把假实例 `register_instance(..., replace=True)` 塞进去，测的就是生产路径本身。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QEvent, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import main_qml  # noqa: E402
from app.bootstrap import build_context  # noqa: E402
from app.bridges.icon_provider import install as install_icon_provider  # noqa: E402

MARKER = "PROBE_JSON:"
APP = QGuiApplication.instance() or QGuiApplication([])
SINK = main_qml.install_message_handler()

QML_ERROR_MARKERS = ("is not defined", "TypeError", "Unable to assign", "Cannot assign", "ReferenceError")
BENIGN_MARKERS = (
    "This plugin does not support",
    "QFont::setPointSize",
    "propagateSizeHints",
    "Failed to create",
)


# ─── 假服务 ────────────────────────────────────────────────────


class FakeVersion:
    """`VersionService` 的替身：记录调用，其余按最小语义返回。"""

    name = "version"

    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []
        self.observers: List[Any] = []
        self.calls: List[Tuple[Any, ...]] = []
        self.detail_data: Dict[str, Any] = {
            "exists": True, "modsCount": 5, "path": "C:/mc/versions/1.20.4-forge",
            "jsonPath": "C:/mc/versions/1.20.4-forge/1.20.4-forge.json",
            "vanilla": "1.20.4", "loader": "forge", "loaderVersion": "49.0.26",
            "hasLoader": True, "javaMajor": 17, "modsDir": "C:/mc/mods",
        }

    def set_observer(self, observer: Any) -> None:
        self.observers.append(observer)

    def load(self, *, force: bool = False) -> Any:
        self.calls.append(("load", force))
        return object()

    def filtered(self, text: str = "", sort_key: str = "name") -> List[Dict[str, Any]]:
        self.calls.append(("filtered", text, sort_key))
        rows = [dict(row) for row in self.rows]
        if text:
            needle = text.lower()
            rows = [row for row in rows if needle in str(row.get("id", "")).lower()]
        if sort_key == "vanilla":
            rows.sort(key=lambda r: str(r.get("vanilla", "")))
        elif sort_key == "loader":
            rows.sort(key=lambda r: str(r.get("loader", "")))
        else:
            rows.sort(key=lambda r: str(r.get("id", "")))
        return rows

    def detail(self, version_id: str) -> Dict[str, Any]:
        self.calls.append(("detail", version_id))
        data = dict(self.detail_data)
        data["id"] = version_id
        return data

    def rename(self, version_id: str) -> Any:
        self.calls.append(("rename", version_id))
        return object()

    def remove(self, version_id: str) -> Any:
        self.calls.append(("remove", version_id))
        return object()

    def verify(self, version_id: str) -> Any:
        self.calls.append(("verify", version_id))
        return object()

    def open_folder(self, version_id: str) -> bool:
        self.calls.append(("open_folder", version_id))
        return True


class FakeGame:
    name = "game"

    def __init__(self) -> None:
        self.observers: List[Any] = []
        self.state = "idle"
        self.launched: List[str] = []
        self.kills = 0
        #: 首页桥也会读这两个（本探针里它同样注册着）
        self.last_launched_version = ""
        self.minimize_after = False

    def add_observer(self, observer: Any) -> None:
        self.observers.append(observer)

    def set_observer(self, observer: Any) -> None:
        self.observers = [observer]

    def launch(self, version_id: str, **_kwargs: Any) -> None:
        self.launched.append(version_id)

    def kill(self) -> bool:
        self.kills += 1
        return True


def row(version_id: str, vanilla: str, loader: str = "", loader_version: str = "", java: int = 0) -> Dict[str, Any]:
    display = version_id
    if loader:
        display += " [%s%s]" % (loader.title(), (" " + loader_version) if loader_version else "")
    elif vanilla:
        display += "  (%s)" % vanilla
    return {
        "id": version_id, "display": display, "vanilla": vanilla,
        "loader": loader, "loaderVersion": loader_version, "hasLoader": bool(loader),
        "state": "original", "reliable": True, "javaMajor": java,
    }


# ─── 装配 ──────────────────────────────────────────────────────

FAKE_VERSION = FakeVersion()
FAKE_GAME = FakeGame()


class PinnedConfig:
    """钉住界面语言（`TrBridge` 会读根模块的 `config`，见 D-160 的修法）。"""

    def __init__(self, language: str = "zh_CN") -> None:
        self.language = language
        self.language_chosen = True

    def save_config(self) -> bool:
        return True


from app.bridges import tr_bridge as _tr_bridge  # noqa: E402

_tr_bridge._root_config = lambda: PinnedConfig()  # type: ignore[assignment]

CONTEXT = build_context(config=None, register=True, set_current=False)
CONTEXT.register_instance("version", FAKE_VERSION, replace=True)
CONTEXT.register_instance("game", FAKE_GAME, replace=True)

#: 真配置的写盘也要挡住：本探针走生产桥装配路径，手里那份 `config` 就是根模块单例 ——
#: 不挡的话跑一轮探针就会把开发机的 `config.json` 改写掉（语言/强调色都中过招）。
from config_isolation import isolate_config_writes  # noqa: E402

isolate_config_writes("versions-probe")

ENGINE = main_qml.build_engine()
install_icon_provider(ENGINE)
BRIDGES = main_qml.register_bridges(ENGINE, CONTEXT)

VERSIONS = ENGINE._fmcl_bridges["Versions"]
NAV = ENGINE._fmcl_bridges["Nav"]
SHELL = ENGINE._fmcl_bridges["Shell"]
TR = ENGINE._fmcl_bridges["Tr"]
VERSIONS.use_engine(ENGINE)

ENGINE.load(QUrl.fromLocalFile(str(main_qml.app_qml_path("App.qml"))))
ROOT = ENGINE.rootObjects()[0] if ENGINE.rootObjects() else None
QTest.qWait(150)


def _walk(root_item: Any) -> Any:
    for child in root_item.childItems():
        yield child
        yield from _walk(child)


def _matches(name: str) -> List[Any]:
    out: List[Any] = []
    if ROOT is None:
        return out
    content = ROOT.contentItem()
    if content is None:
        return out
    for candidate in _walk(content):
        if candidate.objectName() == name:
            out.append(candidate)
    return out


def item(name: str) -> Any:
    """按 objectName 找项。

    * 走**视觉树**（`StackView` 推入的页面只有视觉父项、没有 QObject 父项，
      `findChild` 顺着 QObject 树找不到它 —— 3.1 的首页探针踩过）；
    * **优先返回可见的那个**：换页过程中栈里会短暂留着上一页的副本，
      同名控件因此可能同时存在两份，取到隐藏的那份就会出现"控件明明在却点不到"。
    """
    found = _matches(name)
    for candidate in found:
        try:
            if candidate.isVisible():
                return candidate
        except Exception:  # noqa: BLE001 - 拿不到可见性就当不可见
            break
    return found[0] if found else None


def items_named(name: str) -> List[Any]:
    """同名项的**可见**那一批（当前页面里的那些）。"""
    found = _matches(name)
    alive = [node for node in found if _visible(node)]
    return alive if alive else found


def _visible(node: Any) -> bool:
    try:
        return bool(node.isVisible())
    except Exception:  # noqa: BLE001
        return False


def text_of(name: str) -> str:
    target = item(name)
    if target is None:
        return "<missing>"
    value = target.property("text")
    return "" if value is None else str(value)


def visible(name: str) -> bool:
    target = item(name)
    return bool(target is not None and target.property("visible"))


def enabled(name: str) -> bool:
    target = item(name)
    return bool(target is not None and target.property("enabled"))


def settle(timeout_ms: int = 2000) -> None:
    stack = item("pageStack")
    if stack is None:
        return
    waited = 0
    while waited < timeout_ms:
        pages = [
            child for child in stack.childItems()
            if child.objectName().endswith("Page") and child.property("visible")
        ]
        if len(pages) <= 1:
            return
        QTest.qWait(50)
        waited += 50


def click(target: Any) -> None:
    if target is None:
        raise RuntimeError("点击目标不存在")
    settle()
    centre = target.mapToScene(QPointF(target.width() / 2, target.height() / 2))
    global_pos = ROOT.mapToGlobal(centre)
    for event_type, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton),
                                (QEvent.MouseButtonRelease, Qt.NoButton)):
        APP.sendEvent(ROOT, QMouseEvent(event_type, centre, global_pos, Qt.LeftButton, buttons, Qt.NoModifier))
        QTest.qWait(40)


def feed(rows: List[Dict[str, Any]], error: str = "") -> None:
    """模拟一次真实扫描：假服务的行数据与桥收到的通知必须一致。"""
    FAKE_VERSION.rows = list(rows)
    VERSIONS.on_versions(list(rows), error)
    QTest.qWait(60)


def calls_of(kind: str) -> List[Tuple[Any, ...]]:
    return [call for call in FAKE_VERSION.calls if call[0] == kind]


def qml_errors(messages: List[str]) -> List[str]:
    out = []
    for message in messages:
        if any(marker in message for marker in BENIGN_MARKERS):
            continue
        if any(marker in message for marker in QML_ERROR_MARKERS):
            out.append(message)
    return out


def main() -> int:
    report: Dict[str, Any] = {}

    # ── 0. 进版本页 ──
    NAV.reset()
    NAV.push("versions")
    settle()
    page = item("versionsPage")
    report["page"] = page is not None
    report["route"] = str(NAV.property("currentRoute"))
    report["state_loading"] = page.property("contentState") if page is not None else None
    report["qml_errors_at_start"] = qml_errors(SINK.messages)
    report["objectNames"] = {
        name: item(name) is not None
        for name in (
            "versionsToolbar", "versionsSearch", "versionsSort", "versionsCount",
            "versionsRefreshButton", "versionsInstallButton", "versionsBrowseAllButton",
            "versionsResourceButton",
            "versionsListView", "versionsLaunchButton", "versionsKillButton",
            "versionsDetailPane", "versionsDetailBack", "versionsDetailTitle",
            "versionsDetailMissing", "versionsDetailLaunch", "versionsDetailOpenFolder",
            "versionsDetailVerify", "versionsDetailRename", "versionsDetailDelete",
            "versionsDetailMods", "versionsDetailResources",
        )
    }

    # ── 1. 空态 ──
    feed([])
    report["state_empty"] = page.property("contentState") if page is not None else None

    # ── 2. 有数据 → ready，列表逐行渲染 ──
    rows = [
        row("1.20.4-forge-49.0.26", "1.20.4", "forge", "49.0.26", 17),
        row("1.19.2", "1.19.2"),
        row("1.20.1-fabric-0.15.0", "1.20.1", "fabric", "0.15.0", 17),
    ]
    feed(rows)
    report["state_ready"] = page.property("contentState") if page is not None else None
    report["row_count"] = len(items_named("versionRow"))
    titles = [str(node.property("text")) for node in items_named("versionRowTitle")]
    report["row_titles"] = titles
    report["count_text"] = text_of("versionsCount")
    report["refresh_text"] = text_of("versionsRefreshButton")
    report["launchEnabled_no_selection"] = enabled("versionsLaunchButton")
    report["killEnabled_idle"] = enabled("versionsKillButton")

    # ── 3. 点第一行 → 选中（状态条 + 启动按钮可用）──
    row_hits = items_named("versionRowHit")
    if row_hits:
        click(row_hits[0])
    QTest.qWait(80)
    report["status_after_select"] = SHELL.property("statusText")
    report["launchEnabled_selected"] = enabled("versionsLaunchButton")
    report["resourceEnabled_selected"] = enabled("versionsResourceButton")
    report["detail_calls"] = [call[1] for call in calls_of("detail")]

    # ── 4. 搜索：在真搜索框里打字 ──
    search = item("versionsSearch")
    if search is not None:
        search.setProperty("text", "fabric")
    QTest.qWait(80)
    report["count_text_filtered"] = text_of("versionsCount")
    report["rows_filtered"] = len(items_named("versionRow"))
    if search is not None:
        search.setProperty("text", "")
    QTest.qWait(80)
    report["rows_after_clear"] = len(items_named("versionRow"))

    # ── 5. 排序：改下拉（currentIndex + activated 信号 = 用户那一下）──
    combo = item("versionsSort")
    if combo is not None:
        combo.setProperty("currentIndex", 1)
        combo.activated.emit(1)
    QTest.qWait(80)
    report["sort_after_switch"] = VERSIONS.property("sortKey")
    report["filtered_calls"] = [call[2] for call in calls_of("filtered")][-1:]

    # ── 6. 刷新按钮 → 强制重扫 ──
    click(item("versionsRefreshButton"))
    QTest.qWait(80)
    report["load_forces"] = [call[1] for call in calls_of("load")]

    # ── 6b. 安装入口（用户实测报过"没有找到在哪里装版本"）──
    click(item("versionsInstallButton"))
    QTest.qWait(150)
    settle()
    report["route_after_install_click"] = str(NAV.property("currentRoute"))
    NAV.goBack()
    QTest.qWait(150)
    settle()

    # ── 7. 行操作：重命名 / 删除 / 校验 / 打开目录 / 模组 / 资源管理 ──
    hits = items_named("versionRowHit")
    if hits:
        VERSIONS.select("1.20.4-forge-49.0.26")
    QTest.qWait(60)
    click(item("versionRowRenameButton"))
    QTest.qWait(60)
    #: 假服务不会自己回结果，手动收尾一次（否则 busy 一直挂着，后面的按钮全是灰的）
    VERSIONS.on_result("rename", True, {"id": "1.19.2", "cancelled": True})
    QTest.qWait(60)
    click(item("versionRowDeleteButton"))
    QTest.qWait(60)
    VERSIONS.on_result("remove", False, {"id": "1.19.2"})
    QTest.qWait(60)
    report["rename_calls"] = [call[1] for call in calls_of("rename")]
    report["remove_calls"] = [call[1] for call in calls_of("remove")]

    # ── 8. 启动 / 强杀 ──
    click(item("versionsLaunchButton"))
    QTest.qWait(60)
    report["launched"] = list(FAKE_GAME.launched)
    report["killEnabled_running"] = None
    VERSIONS.report_game_state("running")
    QTest.qWait(80)
    report["killEnabled_running"] = enabled("versionsKillButton")
    click(item("versionsKillButton"))
    QTest.qWait(60)
    report["kills"] = FAKE_GAME.kills

    # ── 9. 详情：路由 + 字段 + 动作 ──
    VERSIONS.select("1.20.4-forge-49.0.26")
    NAV.push("versions/detail", {"version": "1.20.4-forge-49.0.26"})
    settle()
    QTest.qWait(80)
    report["detail_route"] = str(NAV.property("currentRoute"))
    report["detail_pane_visible"] = visible("versionsDetailPane")
    report["detail_title"] = text_of("versionsDetailTitle")
    report["detail_values"] = [str(node.property("text")) for node in items_named("versionsDetailInfoValue")]
    report["detail_missing_hidden"] = not visible("versionsDetailMissing")
    click(item("versionsDetailVerify"))
    QTest.qWait(60)
    VERSIONS.on_result("verify", True, {"id": "1.20.4-forge-49.0.26", "total": 3})
    QTest.qWait(60)
    click(item("versionsDetailOpenFolder"))
    QTest.qWait(60)
    report["verify_calls"] = [call[1] for call in calls_of("verify")]
    report["open_folder_calls"] = [call[1] for call in calls_of("open_folder")]

    #: 版本不存在时给出警示条（B-20 的降级分支）
    FAKE_VERSION.detail_data = {"exists": False, "modsCount": 0, "path": "", "vanilla": "", "loader": ""}
    VERSIONS.select("1.20.4-forge-49.0.26")
    QTest.qWait(80)
    report["detail_missing_shown"] = visible("versionsDetailMissing")
    FAKE_VERSION.detail_data = {
        "exists": True, "modsCount": 5, "path": "C:/mc/versions/1.20.4-forge",
        "jsonPath": "", "vanilla": "1.20.4", "loader": "forge", "loaderVersion": "49.0.26",
        "hasLoader": True, "javaMajor": 17, "modsDir": "C:/mc/mods",
    }
    VERSIONS.select("1.20.4-forge-49.0.26")
    QTest.qWait(60)

    click(item("versionsDetailBack"))
    QTest.qWait(120)
    settle()
    report["route_after_back"] = str(NAV.property("currentRoute"))
    report["qml_errors_after_back"] = qml_errors(SINK.messages)

    # ── 10. 语言热切换（整页文案跟着变）──
    TR.setLanguage("en_US")
    QTest.qWait(150)
    report["refresh_text_en"] = text_of("versionsRefreshButton")
    report["search_placeholder_en"] = str(item("versionsSearch").property("placeholderText")) \
        if item("versionsSearch") is not None else "<missing>"
    TR.setLanguage("zh_CN")
    QTest.qWait(150)
    report["refresh_text_zh"] = text_of("versionsRefreshButton")

    report["qml_errors"] = qml_errors(SINK.messages)
    report["bridges_registered"] = BRIDGES["registered"]
    report["bridges_missing"] = BRIDGES["missing"]

    print(MARKER + json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        CODE = main()
    except Exception as exc:  # noqa: BLE001 - 探针失败也要把已有结论打印出来
        import traceback

        traceback.print_exc()
        DEBUG = {
            "failed": repr(exc),
            "names": sorted({
                node.objectName() for node in _walk(ROOT.contentItem())
            }) if ROOT is not None and ROOT.contentItem() is not None else [],
            "route": str(NAV.property("currentRoute")),
            "load_state": VERSIONS.property("loadState"),
        }
        print(MARKER + json.dumps(DEBUG, ensure_ascii=False))
        CODE = 1
    sys.exit(CODE)
