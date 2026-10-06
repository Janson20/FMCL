"""首页 QML 探针（阶段 3 任务 3.1；`tests/test_home_page_qml.py` 用**子进程**驱动）。

## 为什么另起进程

与 `tests/qml_shell_probe.py` 同一个理由：真引擎 + FluentUI 的 `FluWindow` 在同一个
进程里跨测试文件不安全（会以 access violation 收场）。这里只加载一次 `App.qml`。

## 它验证什么

首页（`qml/pages/home/HomePage.qml`）是阶段 3 的第一个真实页面，探针要证明四件事：

1. **三态真的会切**：桥给"空数据"时走 empty、有数据时走 ready；
2. **属性 → 界面**：账号名/最近版本/游戏状态/皮肤/签到天数都真的显示出来了；
3. **点击 → 桥 → 服务**：启动与强杀按钮点下去，落到注入的假服务上（不是自己另写一套）；
4. **信号 → 壳层**：桥发的状态文案真的进了状态条，且**跟着语言切换**。

## 假服务为什么必须走 `AppContext`

桥只认 `context.try_get("game")` 这一条取数路径（服务层是唯一逻辑来源，红线 2）。
用 `build_context()` 建一个真上下文、再把假实例 `register_instance(..., replace=True)`
塞进去，测的就是生产路径本身 —— 而不是"给桥开后门"。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import Property, QEvent, QObject, QPointF, Qt, QUrl, Signal, Slot  # noqa: E402
from PySide6.QtGui import QGuiApplication, QMouseEvent  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import main_qml  # noqa: E402
from app.bootstrap import build_context  # noqa: E402
from app.bridges.icon_provider import install as install_icon_provider  # noqa: E402

MARKER = "PROBE_JSON:"
APP = QGuiApplication.instance() or QGuiApplication([])
SINK = main_qml.install_message_handler()

#: QML 报错的判据（与 `tests/test_shell_qml.py` 同一套）
QML_ERROR_MARKERS = ("is not defined", "TypeError", "Unable to assign", "Cannot assign", "ReferenceError")
BENIGN_MARKERS = (
    "This plugin does not support",
    "QFont::setPointSize",
    "propagateSizeHints",
    "Failed to create",  # 无 GPU 的离屏环境
)


# ─── 假服务（只实现桥真正用到的那几个成员）─────────────────────


class FakeGame:
    """`GameService` 的替身：记录"谁被调用了"，其余按最小语义返回。"""

    name = "game"

    def __init__(self) -> None:
        self.launched: List[str] = []
        self.kill_calls = 0
        self.observer: Any = None
        self.state = "idle"
        self.last_launched_version = ""
        self.minimize_after = False

    def set_observer(self, observer: Any) -> None:
        self.observer = observer

    def launch(self, version_id: str, **_kwargs: Any) -> None:
        self.launched.append(version_id)

    def kill(self) -> bool:
        self.kill_calls += 1
        return True


class FakeAccount:
    name = "account"

    def __init__(self) -> None:
        self.has_account = False
        self.skin = ""
        self.selected: List[str] = []
        self.removed = 0

    def account_summary(self) -> Dict[str, Any]:
        if not self.has_account:
            return {"has_account": False, "id": "", "name": "", "type": "", "type_key": "", "uuid": ""}
        return {
            "has_account": True,
            "id": "acc-1",
            "name": "Steve",
            "type": "offline",
            "type_key": "account_type_offline",
            "uuid": "00000000-0000-0000-0000-000000000001",
        }

    def skin_path(self) -> str:
        return self.skin

    def select_skin(self, path: str) -> Any:
        self.selected.append(path)
        self.skin = path
        return True, "skin_installed", {"filename": Path(path).name}

    def remove_skin(self) -> None:
        self.removed += 1
        self.skin = ""


class FakeAchievement:
    name = "achievement"

    def __init__(self) -> None:
        self.streak = 0
        self.total = 0
        self.unlocked = 0

    def checkin_streak(self) -> int:
        return self.streak

    def summarize(self, data: Any) -> Any:
        class _Summary:
            pass

        summary = _Summary()
        summary.total = self.total
        summary.unlocked = self.unlocked
        summary.percent = 0.0
        return summary


class FakeStartup(QObject):
    """首页与壳层真正会碰的那部分 `StartupController` 面。

    **必须把信号也声明全**：`App.qml` 与 `StartupDialogs.qml` 里有 `Connections`，
    target 上找不到同名信号时 Qt 会打 `Detected function "onXxx" ... no signal of
    the target matches the name` —— 那是探针自己造的噪声，会被判成 QML 报错。
    """

    phaseChanged = Signal()
    splashDismissed = Signal()
    coreFailed = Signal(str, str)
    statusChanged = Signal(str, str)
    agreementRequired = Signal()
    noticeReady = Signal(str)
    noticeChanged = Signal()
    chainFinished = Signal()
    updateAvailable = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self._notice = ""
        self.replayed = 0
        self.dismissed_notice = 0

    @Property(bool, notify=phaseChanged)
    def startupActive(self) -> bool:  # noqa: N802
        """主窗口可见性由它决定；探针里恒为 False = 直接显示主窗口。"""
        return False

    @Property(str, notify=phaseChanged)
    def phase(self) -> str:
        return "ready"

    @Property(str, notify=phaseChanged)
    def statusText(self) -> str:  # noqa: N802
        return ""

    @Property(str, notify=phaseChanged)
    def statusLevel(self) -> str:  # noqa: N802
        return "info"

    @Property(bool, notify=phaseChanged)
    def splashExpected(self) -> bool:  # noqa: N802
        return False

    @Property(bool, notify=phaseChanged)
    def dismissed(self) -> bool:
        return True

    @Property(bool, notify=noticeChanged)
    def hasNotice(self) -> bool:  # noqa: N802
        return bool(self._notice)

    def set_notice(self, text: str) -> None:
        self._notice = text
        self.noticeChanged.emit()

    @Slot()
    def replayNotice(self) -> None:  # noqa: N802
        self.replayed += 1

    @Slot()
    def dismissNotice(self) -> None:  # noqa: N802
        self.dismissed_notice += 1

    @Slot()
    def confirmAgreement(self) -> None:  # noqa: N802
        pass


# ─── 装配 ──────────────────────────────────────────────────────

FAKE_GAME = FakeGame()
FAKE_ACCOUNT = FakeAccount()
FAKE_ACH = FakeAchievement()
FAKE_STARTUP = FakeStartup()


class PinnedConfig:
    """只用于**钉住界面语言**的内存配置。

    为什么必须钉：`TrBridge` 无参构造时会去读根模块的 `config`（缺陷 D-160 的修法），
    于是"开发机上把界面语言改成 en_US"会让本探针里所有中文断言集体变红
    （本轮实测踩到：全量测试里本文件红了 6 条）。探针的环境必须自洽。
    """

    def __init__(self, language: str = "zh_CN") -> None:
        self.language = language
        self.language_chosen = True

    def save_config(self) -> bool:
        return True


from app.bridges import tr_bridge as _tr_bridge  # noqa: E402

_tr_bridge._root_config = lambda: PinnedConfig()  # type: ignore[assignment]

CONTEXT = build_context(config=None, register=True, set_current=False)
CONTEXT.register_instance("game", FAKE_GAME, replace=True)
CONTEXT.register_instance("account", FAKE_ACCOUNT, replace=True)
CONTEXT.register_instance("achievement", FAKE_ACH, replace=True)

#: 真配置的写盘也要挡住：本探针走生产桥装配路径，手里那份 `config` 就是根模块单例 ——
#: 不挡的话跑一轮探针就会把开发机的 `config.json` 改写掉（语言/强调色都中过招）。
from config_isolation import isolate_config_writes  # noqa: E402

isolate_config_writes("home-probe")

ENGINE = main_qml.build_engine()
install_icon_provider(ENGINE)
BRIDGES = main_qml.register_bridges(ENGINE, CONTEXT)
ENGINE._fmcl_bridges["Startup"] = FAKE_STARTUP
ENGINE.rootContext().setContextProperty("Startup", FAKE_STARTUP)

HOME = ENGINE._fmcl_bridges["Home"]
NAV = ENGINE._fmcl_bridges["Nav"]
SHELL = ENGINE._fmcl_bridges["Shell"]
TR = ENGINE._fmcl_bridges["Tr"]
# `Startup` 是在 register_bridges 之后才进桥表的，所以首页桥要再被"注入一次引擎"
HOME.use_engine(ENGINE)

ENGINE.load(QUrl.fromLocalFile(str(main_qml.app_qml_path("App.qml"))))
ROOT = ENGINE.rootObjects()[0] if ENGINE.rootObjects() else None
QTest.qWait(150)


def _walk(root_item: Any) -> Any:
    """按**视觉**父子关系遍历（`childItems()`）。"""
    for child in root_item.childItems():
        yield child
        yield from _walk(child)


def item(name: str) -> Any:
    """按 objectName 找项。

    **不能用 `ROOT.findChild`**：`StackView` 在 `Component.onCompleted` 里推入的页面
    只有视觉父项、没有 QObject 父项（实测 `parent()` 为 None），`findChild` 顺着
    QObject 树找不到它。`childItems()` 走的是视觉树，这才是可靠的那条路。
    """
    if ROOT is None:
        return None
    content = ROOT.contentItem()
    if content is None:
        return None
    for candidate in _walk(content):
        if candidate.objectName() == name:
            return candidate
    return None


def text_of(name: str) -> str:
    target = item(name)
    if target is None:
        return "<missing>"
    value = target.property("text")
    return "" if value is None else str(value)


def label_of(name: str) -> str:
    """进度条那类组件的文案在 `label` 上（它没有 `text` 属性）。"""
    target = item(name)
    if target is None:
        return "<missing>"
    value = target.property("label")
    return "" if value is None else str(value)


def visible(name: str) -> bool:
    target = item(name)
    return bool(target is not None and target.property("visible"))


def enabled(name: str) -> bool:
    target = item(name)
    return bool(target is not None and target.property("enabled"))


def rect(name: str) -> Dict[str, float]:
    """控件的几何（y / height / width）；找不到时全 -1。"""
    target = item(name)
    if target is None:
        return {"y": -1.0, "height": -1.0, "width": -1.0}
    return {"y": float(target.y()), "height": float(target.height()), "width": float(target.width())}


def settle(timeout_ms: int = 2000) -> None:
    stack = item("pageStack")
    if stack is None:
        return
    waited = 0
    while waited < timeout_ms:
        visible_pages = [
            child for child in stack.childItems()
            if child.objectName().endswith("Page") and child.property("visible")
        ]
        if len(visible_pages) <= 1:
            return
        QTest.qWait(50)
        waited += 50


def click(target: Any) -> None:
    """按真实坐标投递 press + release（与 `tests/qml_shell_probe.py` 同一套手法）。"""
    if target is None:
        raise RuntimeError("点击目标不存在")
    settle()
    centre = target.mapToScene(QPointF(target.width() / 2, target.height() / 2))
    global_pos = ROOT.mapToGlobal(centre)
    for event_type, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton),
                                (QEvent.MouseButtonRelease, Qt.NoButton)):
        APP.sendEvent(ROOT, QMouseEvent(event_type, centre, global_pos, Qt.LeftButton, buttons, Qt.NoModifier))
        QTest.qWait(40)


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

    settle()
    page = item("homePage")
    report["page"] = page is not None
    report["objectNames"] = {
        name: item(name) is not None
        for name in (
            "homeAccountCard", "homeAccountName", "homeAccountManageButton",
            "homeGameCard", "homeRecentVersion", "homeGameStateText",
            "homeLaunchButton", "homeKillButton", "homeOpenVersionsButton",
            "homeSkinCard", "homeSkinName", "homeSkinSelectButton", "homeSkinRemoveButton",
            "homeProgressCard", "homeCheckinStreak", "homeAchievementBar", "homeNoticeButton",
        )
    }

    # ── 1. 空态（首启：没账号、没皮肤、没启动过、成就 0）──
    HOME.refresh()
    QTest.qWait(80)
    report["state_empty"] = page.property("contentState") if page is not None else None

    # ── 2. 有数据 → ready，各块内容都显示出来 ──
    FAKE_ACCOUNT.has_account = True
    FAKE_GAME.last_launched_version = "1.20.4"
    FAKE_ACCOUNT.skin = "C:/mc/skins/hero.png"
    FAKE_ACH.streak = 3
    FAKE_ACH.total = 40
    FAKE_ACH.unlocked = 12
    HOME.publish_achievements([{}])
    HOME.refresh()
    QTest.qWait(80)

    report["state_ready"] = page.property("contentState") if page is not None else None
    report["accountName"] = text_of("homeAccountName")
    report["recentVersion"] = text_of("homeRecentVersion")
    report["skinName"] = text_of("homeSkinName")
    report["checkinStreak"] = text_of("homeCheckinStreak")
    report["achievementLabel"] = label_of("homeAchievementBar")
    report["achievementValue"] = item("homeAchievementBar").property("value") if item("homeAchievementBar") else None
    report["gameStateText"] = text_of("homeGameStateText")
    report["launchEnabled"] = enabled("homeLaunchButton")
    report["killEnabled"] = enabled("homeKillButton")
    report["noticeEnabled"] = enabled("homeNoticeButton")
    report["launchText"] = text_of("homeLaunchButton")

    # ── 2b. 布局（D-161 的判据：正文要撑满内容卡、卡片要占满宽度、四张卡都在可视区内）──
    card_names = ("homeAccountCard", "homeGameCard", "homeSkinCard", "homeProgressCard")
    body = rect("pageContentBody")
    scroller = rect("fmScrollView")
    cards = [rect(name) for name in card_names]
    report["layout"] = {
        "bodyHeight": body["height"],
        "bodyWidth": body["width"],
        "scrollerHeight": scroller["height"],
        "cardWidths": [c["width"] for c in cards],
        "cardHeights": [c["height"] for c in cards],
        "cardBottom": max(c["y"] + c["height"] for c in cards),
    }

    # ── 3. 点启动 → 桥 → 假服务（版本号取自"最近使用版本"）──
    click(item("homeLaunchButton"))
    QTest.qWait(60)
    report["launched"] = list(FAKE_GAME.launched)

    # ── 4. 运行中：状态文案 + 按钮可用性 ──
    HOME.on_state("running")
    HOME.on_status("game_window_ready", "success", {})
    QTest.qWait(80)
    report["status_running"] = SHELL.property("statusText")
    report["status_level"] = SHELL.property("statusLevel")
    report["gameStateText_running"] = text_of("homeGameStateText")
    report["launchEnabled_running"] = enabled("homeLaunchButton")
    report["killEnabled_running"] = enabled("homeKillButton")
    report["launchLoading_running"] = bool(item("homeLaunchButton").property("loading"))

    # ── 4b. "启动中"（进程已起、窗口未出现）：按钮显示忙碌 ──
    HOME.on_state("waiting")
    QTest.qWait(60)
    report["launchLoading_waiting"] = bool(item("homeLaunchButton").property("loading"))
    report["launchEnabled_waiting"] = enabled("homeLaunchButton")
    HOME.on_state("running")
    QTest.qWait(60)

    # ── 5. 点强杀 → 假服务 ──
    click(item("homeKillButton"))
    QTest.qWait(60)
    report["kill_calls"] = FAKE_GAME.kill_calls

    # ── 6. 正常退出 ──
    HOME.on_game_exit(0, False, {}, False)
    QTest.qWait(80)
    report["status_exited"] = SHELL.property("statusText")

    # ── 7. 崩溃：状态条给出退出码（B-07）──
    HOME.on_game_exit(1, True, {"crash_report": "C:/mc/crash-reports/crash-1.txt"}, False)
    QTest.qWait(80)
    report["status_crashed"] = SHELL.property("statusText")

    # ── 8. 公告入口：没有公告时禁用，拉到公告后可用且能重看（A-22）──
    report["noticeEnabled_before"] = enabled("homeNoticeButton")
    FAKE_STARTUP.set_notice("hello")
    QTest.qWait(60)
    report["noticeEnabled_after"] = enabled("homeNoticeButton")
    click(item("homeNoticeButton"))
    QTest.qWait(60)
    report["notice_replayed"] = FAKE_STARTUP.replayed

    # ── 9. 皮肤：选择与移除走桥（B-17）──
    HOME.selectSkin("C:/mc/skins/other.png")
    QTest.qWait(60)
    report["skin_selected"] = list(FAKE_ACCOUNT.selected)
    report["skinName_after"] = text_of("homeSkinName")
    click(item("homeSkinRemoveButton"))
    QTest.qWait(60)
    report["skin_removed"] = FAKE_ACCOUNT.removed
    report["skin_remove_text"] = text_of("homeSkinName")

    # ── 10. 语言热切换：首页文案跟着变（A-15 在真实页面上的落地）──
    TR.setLanguage("en_US")
    QTest.qWait(120)
    report["launchText_en"] = text_of("homeLaunchButton")
    report["checkinText_en"] = text_of("homeCheckinStreak")
    report["recentVersionLabel_en"] = text_of("homeRecentVersion")
    TR.setLanguage("zh_CN")
    QTest.qWait(120)
    report["launchText_zh"] = text_of("homeLaunchButton")

    report["qml_errors"] = qml_errors(SINK.messages)
    report["bridges_registered"] = BRIDGES["registered"]
    report["bridges_missing"] = BRIDGES["missing"]

    print(MARKER + json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
