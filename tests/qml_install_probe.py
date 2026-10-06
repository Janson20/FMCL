"""安装向导 QML 探针（阶段 3 任务 3.3；`tests/test_install_page_qml.py` 用**子进程**驱动）。

## 为什么另起进程

与 `tests/qml_versions_probe.py` 同一个理由：真引擎 + FluentUI 的 `FluWindow` 在同一个
进程里跨测试文件不安全。这里只加载一次 `App.qml`。

## 它验证什么

`qml/pages/versions/InstallVersionPage.qml` 是 3.3 的交付页，探针要证明八件事：

1. **入口真的跳得过来**：在版本页点「安装新版本」→ 路由变成 `versions/install`
   （3.2 的按钮 + 3.3 的路由表指向新页面文件，两件事一起验）；
2. **可用版本那块三态会切**：loading / ready / empty / error（整页始终 ready —— 离线
   也要能手动输版本 ID，这是本页刻意的设计）；
3. **分页与标签页**：每页 20 条、翻页、正式版/测试版切换各走一次桥；
4. **9 种加载器**：下拉里就是那 9 项（顺序与旧界面一致），选中会回写桥；
5. **点一行回填版本 ID**（B-13 的"点击回填"）；
6. **兼容提示真的显示服务给的那句话**（B-10，三态：bad / ok / unknown）；
7. **安装全流程**：点安装 → `Tasks` 桥收到任务（真任务桥！）→ 进度条动 → 取消能停
   → 再来一次成功 → 页面显示结果并在 1.5 秒后自动回版本列表；
8. **语言热切换**：加载器下拉的标签与按钮文案跟着 `Tr.setLanguage` 变。

## 为什么用**真的** `TaskBridge`

安装任务是唯一"经 `Tasks` 桥提交"的界面动作（状态栏的后台任务指示只认它）。
用假桥就只能验"页面调了 submit"，验不到"进度真的合并着发回来、取消真的传到
`TaskContext.cancelled`"。所以这里让真 `TaskBridge` + 一个假 `install` 服务配合：
服务的工作者每 50ms 报一次进度并查一次取消标志 —— 与真实现同一套契约。
"""

from __future__ import annotations

import json
import os
import sys
import time
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
from services.install_service import page_slice, split_available  # noqa: E402

MARKER = "PROBE_JSON:"
APP = QGuiApplication.instance() or QGuiApplication([])
SINK = main_qml.install_message_handler()

#: `--shots <目录>`：顺手把关键几帧落盘（人工验收用的证据截图）。
#: 判据**不读图片**，它只是给"并排比对"留一份可看的东西（与 `tests/visual_probe.py` 同一约定）。
SHOT_DIR: Optional[Path] = None
SHOTS: List[str] = []

QML_ERROR_MARKERS = ("is not defined", "TypeError", "Unable to assign", "Cannot assign", "ReferenceError")
BENIGN_MARKERS = (
    "This plugin does not support",
    "QFont::setPointSize",
    "propagateSizeHints",
    "Failed to create",
)


# ─── 假服务 ────────────────────────────────────────────────────

#: 45 个正式版 + 3 个测试版：够验"每页 20、共 3 页"
RELEASES = ["1.%d" % (20 - (index // 10)) + (".%d" % (index % 10)) for index in range(45)]
SNAPSHOTS = ["24w%02da" % (index + 1) for index in range(3)]


class FakeInstall:
    """`InstallService` 的替身：读侧按最小语义返回，写侧用真 `Tasks` 契约跑工作者。

    分页与分组直接用**服务里那两个纯函数**（`split_available` / `page_slice`）——
    假服务要是自己再写一套分页，验的就不是产品的那套语义了。
    """

    name = "install"

    def __init__(self) -> None:
        self.calls: List[Tuple[Any, ...]] = []
        self.observers: List[Any] = []
        self.available: List[Dict[str, Any]] = [{"id": item, "type": "release"} for item in RELEASES]
        self.available += [{"id": item, "type": "snapshot"} for item in SNAPSHOTS]
        self.available_error = ""
        self.compat_state = "bad"
        self.compat_supported: Optional[bool] = False
        self.install_plan = "wait_cancel"  # wait_cancel / succeed
        self.install_delay_ms = 300
        self.installed_id = "1.20.4-forge-49.0.26"

    def set_observer(self, observer: Any) -> None:
        self.observers.append(observer)

    # 读侧 ─────────────────────────────────────────────────────
    def load_available(self, *, force: bool = False) -> Any:
        self.calls.append(("load_available", bool(force)))
        for observer in self.observers:
            observer.on_available(list(self.available), self.available_error)
        return None

    def page_of(self, tab: str, page: int) -> Dict[str, Any]:
        release, snapshot = split_available(self.available)
        key = tab if tab in ("release", "snapshot") else "release"
        data = page_slice(release if key == "release" else snapshot, page)
        data.update({"tab": key, "releaseCount": len(release), "snapshotCount": len(snapshot)})
        return data

    def check_compat(self, version_id: str, loader: str) -> Any:
        self.calls.append(("check_compat", str(version_id), str(loader)))
        for observer in self.observers:
            observer.on_compat(
                str(loader), str(version_id).split(" ")[0], self.compat_state, "remote", self.compat_supported
            )
        return None

    # 写侧：工作者（跑在真 `TaskBridge` 的工作线程上）──────────
    def install_task(self, ctx: Any, *, version_id: str = "", loader: str = "none") -> Dict[str, Any]:
        self.calls.append(("install_task", str(version_id), str(loader)))
        total = 5
        for step in range(1, total + 1):
            if getattr(ctx, "cancelled", False):
                self.calls.append(("install_cancelled", str(version_id)))
                return {"ok": False, "state": "cancelled", "requested": version_id, "loader": loader}
            try:
                ctx.progress(step, total, "")
            except Exception:  # noqa: BLE001
                pass
            time.sleep(self.install_delay_ms / 1000.0 / total)
        self.calls.append(("install_done", str(version_id), str(loader)))
        return {
            "ok": True, "state": "done", "requested": version_id,
            "installed": self.installed_id, "loader": loader, "loaderName": "Forge",
        }

    def emit_status(self, key: str, level: str, params: Dict[str, Any]) -> None:
        for observer in self.observers:
            observer.on_status(key, level, params)


class FakeVersion:
    name = "version"

    def __init__(self) -> None:
        self.observers: List[Any] = []
        self.calls: List[Tuple[Any, ...]] = []
        self.pending = ""

    def set_observer(self, observer: Any) -> None:
        self.observers.append(observer)

    def load(self, *, force: bool = False) -> Any:
        self.calls.append(("load", bool(force)))
        return None

    def filtered(self, text: str = "", sort_key: str = "name") -> List[Dict[str, Any]]:
        return []

    def detail(self, version_id: str) -> Dict[str, Any]:
        return {"id": version_id, "exists": True, "hasLoader": True}

    def consume_pending_selection(self) -> str:
        value = self.pending
        self.pending = ""
        return value

    def note_installed(self, version_id: str) -> None:
        self.pending = str(version_id)
        self.calls.append(("note_installed", str(version_id)))


class FakeGame:
    name = "game"

    def __init__(self) -> None:
        self.state = "idle"
        self.observers: List[Any] = []
        self.last_launched_version = ""

    def add_observer(self, observer: Any) -> None:
        self.observers.append(observer)

    def set_observer(self, observer: Any) -> None:
        self.observers = [observer]

    def launch(self, version_id: str, **_kwargs: Any) -> None:
        return None

    def kill(self) -> bool:
        return True


# ─── 装配 ──────────────────────────────────────────────────────


class PinnedConfig:
    """钉住界面语言（`TrBridge` 会读根模块的 `config`，见 D-160 的修法）。"""

    def __init__(self, language: str = "zh_CN") -> None:
        self.language = language
        self.language_chosen = True

    def save_config(self) -> bool:
        return True


from app.bridges import tr_bridge as _tr_bridge  # noqa: E402

_tr_bridge._root_config = lambda: PinnedConfig()  # type: ignore[assignment]

FAKE_INSTALL = FakeInstall()
FAKE_VERSION = FakeVersion()
FAKE_GAME = FakeGame()

CONTEXT = build_context(config=None, register=True, set_current=False)
CONTEXT.register_instance("install", FAKE_INSTALL, replace=True)
CONTEXT.register_instance("version", FAKE_VERSION, replace=True)
CONTEXT.register_instance("game", FAKE_GAME, replace=True)

#: 真配置的写盘也要挡住：本探针走生产桥装配路径，手里那份 `config` 就是根模块单例 ——
#: 不挡的话跑一轮探针就会把开发机的 `config.json` 改写掉（语言/强调色都中过招）。
from config_isolation import isolate_config_writes  # noqa: E402

isolate_config_writes("install-probe")

ENGINE = main_qml.build_engine()
install_icon_provider(ENGINE)
BRIDGES = main_qml.register_bridges(ENGINE, CONTEXT)

INSTALL = ENGINE._fmcl_bridges["Install"]
VERSIONS = ENGINE._fmcl_bridges["Versions"]
NAV = ENGINE._fmcl_bridges["Nav"]
SHELL = ENGINE._fmcl_bridges["Shell"]
TASKS = ENGINE._fmcl_bridges["Tasks"]
TR = ENGINE._fmcl_bridges["Tr"]
INSTALL.use_engine(ENGINE)
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
    """按 objectName 找项（走视觉树；同名时优先可见的那个，理由见 3.2 探针）。"""
    found = _matches(name)
    for candidate in found:
        try:
            if candidate.isVisible():
                return candidate
        except Exception:  # noqa: BLE001
            break
    return found[0] if found else None


def items_named(name: str) -> List[Any]:
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


def wait_until(predicate: Any, timeout_ms: int = 4000, step_ms: int = 50) -> bool:
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return True
        QTest.qWait(step_ms)
        waited += step_ms
    return bool(predicate())


def calls_of(kind: str) -> List[Tuple[Any, ...]]:
    return [call for call in FAKE_INSTALL.calls if call[0] == kind]


def loader_labels(combo: Any) -> List[str]:
    """读下拉里 9 项的**显示文本**（探针要证明用户看到的那份译文是对的）。"""
    if combo is None:
        return []
    model = combo.property("model")
    out = []
    for index in range(int(combo.property("count"))):
        row = model[index] if hasattr(model, "__getitem__") else model.get(index)
        out.append(str(row.get("label") if hasattr(row, "get") else row))
    return out


def shot(name: str) -> None:
    """把当前一帧落盘（只在 `--shots` 给了目录时做；失败不影响判据）。"""
    if SHOT_DIR is None or ROOT is None:
        return
    try:
        image = ROOT.grabWindow()
        target = SHOT_DIR / f"{name}.png"
        if image.isNull() or not image.save(str(target)):
            return
        SHOTS.append(str(target))
    except Exception:  # noqa: BLE001 - 截图失败不该打断探针
        return


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
    installed_events: List[str] = []
    INSTALL.installed.connect(lambda version: installed_events.append(str(version)))

    # ── 0. 从版本页的入口进向导（3.2 的按钮 → 3.3 的页面）──
    #: 版本页要先有数据才显示内容区（三态里 loading/empty 都不显示工具条），
    #: 所以先喂一行已安装版本，否则那个入口按钮根本不在视觉树里。
    VERSIONS.on_versions([{
        "id": "1.20.4", "display": "1.20.4  (1.20.4)", "vanilla": "1.20.4", "loader": "",
        "loaderVersion": "", "hasLoader": False, "state": "original", "reliable": True, "javaMajor": 17,
    }], "")
    NAV.reset()
    NAV.push("versions")
    settle()
    QTest.qWait(150)
    click(item("versionsInstallButton"))
    QTest.qWait(300)
    settle()
    report["route_after_entry"] = str(NAV.property("currentRoute"))
    page = item("installVersionPage")
    report["page"] = page is not None
    report["objectNames"] = {
        name: item(name) is not None
        for name in (
            "installPane", "installFormCard", "installVersionId", "installLoader",
            "installCompatBar", "installStartButton", "installModpackButton",
            "installVersionsCard", "installTabRelease", "installTabSnapshot", "installCounts",
            "installRefreshAvailable", "installPager", "installListLoading", "installListEmpty",
            "installListError", "installVersionGrid", "installProgressCard", "installProgressBar",
            "installResultBar", "installCancelButton", "installRetryButton", "installBackButton",
        )
    }
    report["qml_errors_at_start"] = qml_errors(SINK.messages)

    # ── 1. 可用版本三态：先空、再 ready、再 error ──
    FAKE_INSTALL.available = []
    FAKE_INSTALL.load_available()
    QTest.qWait(80)
    report["list_state_empty"] = str(INSTALL.property("availableState"))
    report["empty_state_visible"] = visible("installListEmpty")
    shot("versions_install_empty")

    FAKE_INSTALL.available = [{"id": item_id, "type": "release"} for item_id in RELEASES]
    FAKE_INSTALL.available += [{"id": item_id, "type": "snapshot"} for item_id in SNAPSHOTS]
    FAKE_INSTALL.load_available()
    QTest.qWait(120)
    report["list_state_ready"] = str(INSTALL.property("availableState"))
    report["grid_visible"] = visible("installVersionGrid")
    shot("versions_install_ready")

    # ── 2. 每页 20 + 共 3 页 ──
    report["page_count"] = int(INSTALL.property("pageCount"))
    report["chip_count_page1"] = len(items_named("installVersionChip"))
    report["pager_visible"] = visible("installPager")
    click(item("installPager"))
    INSTALL.nextPage()
    QTest.qWait(120)
    report["page_after_next"] = int(INSTALL.property("page"))
    report["chip_count_page2"] = len(items_named("installVersionChip"))

    # ── 3. 标签页切换：测试版只有 3 条、页码回 1 ──
    snapshot_tab = item("installTabSnapshot")
    if snapshot_tab is not None:
        snapshot_tab.setProperty("checked", True)
        snapshot_tab.toggled.emit()
    QTest.qWait(120)
    report["tab_after_switch"] = str(INSTALL.property("tab"))
    report["page_after_tab_switch"] = int(INSTALL.property("page"))
    report["snapshot_chips"] = len(items_named("installVersionChip"))
    release_tab = item("installTabRelease")
    if release_tab is not None:
        release_tab.setProperty("checked", True)
        release_tab.toggled.emit()
    QTest.qWait(120)
    report["tab_back"] = str(INSTALL.property("tab"))

    # ── 4. 9 种加载器下拉 ──
    loader = item("installLoader")
    report["loader_count"] = int(loader.property("count")) if loader is not None else -1
    report["loader_labels"] = loader_labels(loader)
    if loader is not None:
        loader.setProperty("currentIndex", 1)
        loader.activated.emit(1)
    QTest.qWait(120)
    report["loader_after_pick"] = str(INSTALL.property("loader"))
    report["loader_name_after_pick"] = str(INSTALL.property("loaderName"))

    # ── 5. 兼容提示三态 ──
    INSTALL.setVersionId("1.20.4")
    QTest.qWait(60)
    FAKE_INSTALL.compat_state = "bad"
    FAKE_INSTALL.compat_supported = False
    INSTALL.checkCompatibility()
    QTest.qWait(100)
    report["compat_key_bad"] = str(INSTALL.property("compatKey"))
    report["compat_bar_text_bad"] = text_of("installCompatBar")
    FAKE_INSTALL.compat_state = "ok"
    FAKE_INSTALL.compat_supported = True
    INSTALL.checkCompatibility()
    QTest.qWait(100)
    report["compat_key_ok"] = str(INSTALL.property("compatKey"))
    report["compat_bar_text_ok"] = text_of("installCompatBar")

    # ── 6. 点一行回填版本 ID（B-13）──
    chips = items_named("installVersionChip")
    if chips:
        click(chips[2])
    QTest.qWait(120)
    report["version_id_after_pick"] = str(INSTALL.property("versionId"))
    report["field_text_after_pick"] = str(item("installVersionId").property("text")) \
        if item("installVersionId") is not None else "<missing>"

    # ── 7. 安装：进度 + 取消（走真 `Tasks` 桥）──
    FAKE_INSTALL.install_plan = "wait_cancel"
    FAKE_INSTALL.install_delay_ms = 1500
    INSTALL.setVersionId("1.20.4")
    click(item("installStartButton"))
    QTest.qWait(200)
    report["busy_during_install"] = bool(INSTALL.property("busy"))
    report["progress_card_visible"] = visible("installProgressCard")
    report["progress_bar_visible"] = visible("installProgressBar")
    report["active_tasks"] = int(TASKS.property("activeCount"))
    report["shell_busy"] = bool(SHELL.property("busy"))
    wait_until(lambda: float(INSTALL.property("progress")) > 0.0, timeout_ms=1500)
    #: 进度条文案要等**那一条**真的合并着发过来（100ms 一刷）：并行跑测试时 CPU 一忙，
    #: 早一步读到的还是空串或上一档（实测：`-n auto` 下这条断言偶发红）。
    bar_label = lambda: str(item("installProgressBar").property("label")) \
        if item("installProgressBar") is not None else ""
    wait_until(lambda: "/" in bar_label(), timeout_ms=2000)
    report["progress_moved"] = float(INSTALL.property("progress"))
    report["progress_label"] = bar_label()
    shot("versions_install_progress")
    click(item("installCancelButton"))
    wait_until(lambda: not bool(INSTALL.property("busy")), timeout_ms=4000)
    report["state_after_cancel"] = str(INSTALL.property("resultState"))
    report["cancel_calls"] = [call for call in calls_of("install_cancelled")]
    report["active_tasks_after_cancel"] = int(TASKS.property("activeCount"))

    # ── 8. 安装成功 → 结果 + 自动回列表 ──
    FAKE_INSTALL.install_delay_ms = 200
    click(item("installRetryButton")) if visible("installRetryButton") else click(item("installStartButton"))
    wait_until(lambda: str(INSTALL.property("resultState")) == "done", timeout_ms=4000)
    QTest.qWait(120)
    report["state_after_success"] = str(INSTALL.property("resultState"))
    report["result_bar_text"] = text_of("installResultBar")
    report["result_level"] = str(item("installResultBar").property("level")) \
        if item("installResultBar") is not None else "<missing>"
    report["installed_signals"] = list(installed_events)
    report["install_done_calls"] = [call for call in calls_of("install_done")]
    report["installed_version"] = str(INSTALL.property("resultInstalled"))
    shot("versions_install_done")
    wait_until(lambda: str(NAV.property("currentRoute")) != "versions/install", timeout_ms=3000)
    settle()
    report["route_after_success"] = str(NAV.property("currentRoute"))

    # ── 9. 整合包入口（B-12）与错误文案（缺版本 ID）──
    NAV.push("versions/install")
    QTest.qWait(200)
    settle()
    click(item("installModpackButton"))
    QTest.qWait(200)
    report["route_after_modpack_click"] = str(NAV.property("currentRoute"))
    NAV.goBack()
    QTest.qWait(150)
    settle()
    INSTALL.setVersionId("")
    click(item("installStartButton"))
    QTest.qWait(100)
    report["status_after_empty_install"] = str(SHELL.property("statusText"))

    # ── 10. 语言热切换 ──
    TR.setLanguage("en_US")
    QTest.qWait(200)
    report["loader_labels_en"] = loader_labels(item("installLoader"))
    report["retry_text_en"] = text_of("installRetryButton")
    TR.setLanguage("zh_CN")
    QTest.qWait(200)
    report["loader_labels_zh"] = loader_labels(item("installLoader"))

    # ── 11. 语言入口：顶栏地球图标 → 复用 A-27 那个语言浮层（2026-10-06 验收补的入口）──
    click(item("languageButton"))
    QTest.qWait(200)
    report["language_overlay_visible"] = visible("languageOverlay")
    report["language_title_nonempty"] = text_of("languageTitle") != ""
    report["language_option_count"] = len(items_named("languageOption_zh_CN")) \
        + len(items_named("languageOption_en_US")) + len(items_named("languageOption_ja_JP")) \
        + len(items_named("languageOption_zh_TW"))
    click(item("languageConfirmButton"))
    QTest.qWait(200)
    report["language_overlay_closed"] = not visible("languageOverlay")
    report["language_after_confirm"] = str(TR.property("language"))

    report["qml_errors"] = qml_errors(SINK.messages)
    report["bridges_registered"] = BRIDGES["registered"]
    report["bridges_missing"] = BRIDGES["missing"]
    report["task_kinds"] = list(TASKS.kinds())
    report["shots"] = list(SHOTS)

    print(MARKER + json.dumps(report, ensure_ascii=False))
    return 0


def parse_args(argv: List[str]) -> Optional[Path]:
    """`--shots <目录>`（可选）。"""
    if "--shots" not in argv:
        return None
    index = argv.index("--shots")
    if index + 1 >= len(argv):
        raise SystemExit("--shots 后面要给一个目录")
    path = Path(argv[index + 1]).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


if __name__ == "__main__":
    SHOT_DIR = parse_args(sys.argv[1:])
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
            "available_state": INSTALL.property("availableState"),
            "messages": [str(message) for message in SINK.messages[-15:]],
            "push_result": bool(NAV.push("versions/install")),
        }
        print(MARKER + json.dumps(DEBUG, ensure_ascii=False))
        CODE = 1
    sys.exit(CODE)
