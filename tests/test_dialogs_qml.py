"""真 QML 的对话框 / Toast 测试（阶段 2 任务 2.13）。

这一半测的是**真的 QML**：真引擎、真 ``QQmlApplicationEngine``、真场景树。要点：

* ``QT_QPA_PLATFORM=offscreen``（在 import PySide6 **之前** 设），并且**先建
  ``QGuiApplication`` 再建 ``QQmlApplicationEngine`` —— 顺序反了进程会硬崩
  （``exit=3221226505``，任务书里点名的坑）；
* **委托只能从视觉子树里数**：``findChildren()`` 看不见 Repeater/Loader 建出来的
  对象，必须走 ``childItems()``（契约第六节决策 7 / 阶段 0 第 13.6 节）；
* 组件目录**整片复制到 tmp 再加载**：负例自检要往副本里写字，绝不能动仓库里的源文件；
* 每个用例一个引擎，用完即弃（``deleteLater`` + 还原消息处理器）。

作答的驱动方式：``QMetaObject.invokeMethod(item, "accept")`` —— QML 里 ``function``
声明出来的函数在元对象系统里是可调用的（``poc/_probe_qml_invoke_2_13.py`` 实测过
2 参与 3 参两种形态）。另有**一条真鼠标点击**的用例（``QTest.mouseClick``）证明按钮
真的接上了。
"""

from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

# 必须在 import PySide6 之前设置
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

import pytest  # noqa: E402
from PySide6.QtCore import QMetaObject, QObject, QPointF, Qt, QUrl, qInstallMessageHandler  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.bridges.dialog_bridge import (  # noqa: E402
    MAX_TOASTS,
    MIN_TOAST_DURATION_MS,
    DialogBridge,
)
from app.bridges.dialog_host import FALLBACK_KEY, RecordingDialogHost  # noqa: E402
from app.bridges.runtime_bridge import RuntimeBridge  # noqa: E402
from app.bridges.theme_bridge import ThemeBridge  # noqa: E402
from app.bridges.tr_bridge import TrBridge, locales_dir  # noqa: E402
from app.ports import Choice, ProgressReport  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPONENTS_SRC = REPO_ROOT / "qml" / "components"

#: 宿主包装：把 DialogHost / ToastHost 放进一个真窗口里（2.12 的 App.qml 也是这么挂的）
WRAPPER = """
import QtQuick
import QtQuick.Controls
import "components"
import "components/dialogs"

// 刻意**不覆盖** objectName：两个组件的根各自叫 dialogHost / toastHost，测试按它们找。
Window {
    id: probeWindow
    width: 900
    height: 640
    visible: true
    title: "dialog qml probe"

    DialogHost {
        anchors.fill: parent
        z: 100
    }

    ToastHost {
        anchors.fill: parent
        z: 90
    }
}
"""

#: 测试要用到的短时长（Toast 时长下限是 300ms，桥会把它抬上来）
TOAST_MS = MIN_TOAST_DURATION_MS
#: 等动画/定时器收敛的余量
SETTLE_MS = 700


class FakeConfig:
    """假配置：主题/语言只读它，绝不碰真实的 config.json。"""

    language = "zh_CN"
    theme_name = "default"
    accent_color = None
    base_dir = str(REPO_ROOT)

    def save_config(self) -> None:  # pragma: no cover - 主题桥只会在写盘时调它
        pass


def stage_components(tmp_path: Path, mutate: Optional[Dict[str, Tuple[str, str]]] = None) -> Path:
    """把 ``qml/components`` 复制到 tmp，可选地做字符串变异，返回临时 QML 根目录。

    变异用 ``assert old in text`` 自校验：源文件改了、要替换的片段没了，负例自检会
    立刻变红——不允许它悄悄退化成"什么都没改所以当然通过"。
    """
    root = tmp_path / "qml"
    shutil.copytree(COMPONENTS_SRC, root / "components")
    for rel, (old, new) in (mutate or {}).items():
        target = root / rel
        text = target.read_text(encoding="utf-8")
        assert old in text, f"负例自检失效：{rel} 里找不到 {old!r}（源文件被改过了？）"
        target.write_text(text.replace(old, new, 1), encoding="utf-8", newline="")
    (root / "Wrapper.qml").write_text(WRAPPER, encoding="utf-8", newline="")
    return root


class Harness:
    """一次 QML 加载的全部把手（引擎 / 桥 / 场景树查询 / 作答驱动）。"""

    def __init__(self, tmp_path: Path, mutate: Optional[Dict[str, Tuple[str, str]]] = None) -> None:
        self.messages: List[Tuple[str, str]] = []
        self.answered: List[Any] = []
        self._previous_handler: Any = None
        self.qml_root = stage_components(tmp_path, mutate)

        self.app = QGuiApplication.instance() or QGuiApplication([])
        self._previous_handler = qInstallMessageHandler(self._on_message)

        self.engine = QQmlApplicationEngine()
        self.bridge = DialogBridge(engine=self.engine)
        # 主题桥显式注入 theme_engine（用户主题目录指向 tmp）—— 不碰真实的应用数据目录
        from services.theme_service import ThemeEngine

        self.theme = ThemeBridge(
            engine=self.engine, theme_engine=ThemeEngine(str(tmp_path / "themes")), config=FakeConfig()
        )
        self.tr = TrBridge(config=FakeConfig(), locales=locales_dir())
        self.runtime = RuntimeBridge()
        for name, obj in (("Dialogs", self.bridge), ("Theme", self.theme), ("Tr", self.tr), ("Runtime", self.runtime)):
            self.engine.rootContext().setContextProperty(name, obj)

        self.engine.load(QUrl.fromLocalFile(str(self.qml_root / "Wrapper.qml")))
        self.roots = list(self.engine.rootObjects())
        self.window = self.roots[0] if self.roots else None

    # ─── 生命周期 ───────────────────────────────────────────

    def _on_message(self, mode: Any, context: Any, message: str) -> None:
        self.messages.append((str(mode), str(message)))

    def close(self) -> None:
        try:
            if self.window is not None:
                self.window.setProperty("visible", False)
        except Exception:  # noqa: BLE001 - 收尾失败不该掩盖用例结论
            pass
        try:
            self.engine.deleteLater()
        except Exception:  # noqa: BLE001
            pass
        QTest.qWait(10)
        if self._previous_handler is not None:
            qInstallMessageHandler(self._previous_handler)
        self._previous_handler = None

    # ─── 场景树查询 ─────────────────────────────────────────

    def find(self, name: str) -> Optional[QObject]:
        """按 objectName 找**非视觉**对象（Loader / Repeater / ListModel 这类）。"""
        return self.window.findChild(QObject, name) if self.window is not None else None

    def visual_all(self, name: str) -> List[QQuickItem]:
        """按 objectName 找**视觉**子树里的项 —— 委托只在这里可见（契约决策 7）。"""
        found: List[QQuickItem] = []

        def walk(item: QQuickItem) -> None:
            if item.objectName() == name:
                found.append(item)
            for child in item.childItems():
                walk(child)

        if self.window is not None:
            walk(self.window.contentItem())
        return found

    def visual_one(self, name: str) -> QQuickItem:
        found = self.visual_all(name)
        assert found, f"视觉树里找不到 {name!r}（当前可见项：{sorted(self.visual_names())}）"
        return found[0]

    def visual_names(self) -> List[str]:
        names: List[str] = []

        def walk(item: QQuickItem) -> None:
            if item.objectName():
                names.append(item.objectName())
            for child in item.childItems():
                walk(child)

        if self.window is not None:
            walk(self.window.contentItem())
        return names

    def loader_item(self) -> Optional[QQuickItem]:
        """当前显示的对话框（DialogHost 里那个 Loader 的 item）。"""
        loader = self.find("dialogLoader")
        if loader is None:
            return None
        item = loader.property("item")
        return item if isinstance(item, QQuickItem) else None

    def progress_item(self) -> Optional[QQuickItem]:
        loader = self.find("progressLoader")
        if loader is None:
            return None
        item = loader.property("item")
        return item if isinstance(item, QQuickItem) else None

    def toast_cards(self) -> List[QQuickItem]:
        """Toast 委托，按槽号从下到上排序（第 0 个最靠下 = 最早到的那条）。

        正在淡出的排在最后：它们保持旧槽号，不能参与"当前堆了几条"的判断。
        """
        return sorted(
            self.visual_all("toastItem"),
            key=lambda item: (bool(item.property("closing")), int(item.property("slot"))),
        )

    # ─── 驱动 ───────────────────────────────────────────────

    def pump(self, ms: int = 60) -> None:
        QTest.qWait(ms)

    def present(self, kind: str, payload: Dict[str, Any]) -> int:
        """发一个请求并返回它的 id；回填值收进 ``self.answered``。"""
        seen: List[Dict[str, Any]] = []
        self.bridge.dialogRequested.connect(seen.append)
        self.bridge.present(kind, payload, self.answered.append)
        self.pump(30)
        if not seen:
            return -1
        return int(seen[-1]["id"])

    def invoke(self, item: QQuickItem, method: str) -> bool:
        """调 QML 里声明的函数（弹窗的 accept / cancel / select 等）。"""
        ok = QMetaObject.invokeMethod(item, method, Qt.ConnectionType.DirectConnection)
        self.pump(20)
        return bool(ok)

    def click(self, item: QQuickItem) -> None:
        """真的点一下（窗口坐标取件中心）。"""
        centre = item.mapToScene(QPointF(item.width() / 2.0, item.height() / 2.0))
        QTest.mouseClick(self.window, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, centre.toPoint())
        self.pump(40)

    def wait_until(self, predicate: Callable[[], bool], *, timeout_s: float = 3.0, step_ms: int = 10) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate():
                return True
            QTest.qWait(step_ms)
        return bool(predicate())

    def errors(self) -> List[str]:
        """QML 的报错（绑定错误 / 未定义名字 / 类型错误）。"""
        keywords = ("is not defined", "TypeError", "ReferenceError", "Unable to assign", "SyntaxError", "is not a")
        return [msg for mode, msg in self.messages if any(k in msg for k in keywords)]


@pytest.fixture(scope="session", autouse=True)
def qt_app() -> QGuiApplication:
    """一个进程只能有一个 ``QGuiApplication``，且必须先于任何 ``QQmlApplicationEngine``。"""
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


@pytest.fixture()
def harness(qt_app, tmp_path: Path):
    """默认宿主（不改任何 QML）。"""
    box = Harness(tmp_path)
    assert box.roots, f"QML 没加载起来：{box.messages}"
    yield box
    box.close()


def make_harness(tmp_path: Path, mutate: Dict[str, Tuple[str, str]]) -> Harness:
    """建一个**被改过**的宿主（负例自检用）。"""
    box = Harness(tmp_path, mutate)
    assert box.roots, f"变异后的 QML 没加载起来：{box.messages}"
    return box


#: 三个 kind 的标准 payload（与 tests/test_dialog_bridge.py 里那份同形）
def confirm_payload(default: bool = True) -> Dict[str, Any]:
    return {"title": "确认标题", "message": "确认内容", "default": default, FALLBACK_KEY: default}


def alert_payload(level: str) -> Dict[str, Any]:
    return {"title": "提示标题", "message": "提示内容", "level": level, "blocking": False, FALLBACK_KEY: None}


def ask_payload(**over: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "title": "输入标题",
        "prompt": "请输入",
        "initial": "初值  ",
        "password": False,
        FALLBACK_KEY: None,
    }
    payload.update(over)
    return payload


def choose_payload(default: Optional[str] = "lite") -> Dict[str, Any]:
    return {
        "title": "选择标题",
        "prompt": "选一个",
        "options": [Choice("yes", "完整版"), Choice("lite", "轻量版"), Choice("no", "都不要")],
        "default": default,
        "hint": "提示一行",
        FALLBACK_KEY: None,
    }


# ─── 1. 就绪握手 ────────────────────────────────────────────


class TestHandshake:
    def test_qml_host_marks_ready(self, harness):
        assert harness.bridge.is_available() is True
        assert harness.bridge.describe()["attached"] is True
        assert harness.find("dialogHost") is not None
        assert harness.find("toastHost") is not None

    def test_destroying_the_root_releases_waiting_dialogs(self, harness):
        """根组件销毁时 QML 侧调 ``markClosing()``：必须放行还在等待的调用方。"""
        harness.present("confirm", confirm_payload())
        assert harness.bridge.pendingDialogs()

        harness.window.deleteLater()
        harness.pump(100)
        assert harness.answered == [True], "销毁时必须用兜底值收尾（不能让调用方等到超时）"
        assert harness.bridge.is_available() is False


# ─── 2. 确认框 ──────────────────────────────────────────────


class TestConfirmDialog:
    def test_loads_confirm_component_with_texts(self, harness):
        harness.present("confirm", confirm_payload())
        item = harness.loader_item()
        assert item is not None and item.objectName() == "confirmDialog"
        assert harness.visual_one("dialogTitle").property("text") == "确认标题"
        assert harness.visual_one("dialogMessage").property("text") == "确认内容"

    def test_accept_answers_true(self, harness):
        harness.present("confirm", confirm_payload())
        assert harness.invoke(harness.loader_item(), "accept") is True
        assert harness.answered == [True]
        assert harness.loader_item() is None, "答完就该从队列里摘掉"

    def test_cancel_answers_false(self, harness):
        harness.present("confirm", confirm_payload(default=True))
        assert harness.invoke(harness.loader_item(), "cancel") is True
        assert harness.answered == [False], "确认框的取消是 false，不是兜底值（兜底值此时是 true）"

    def test_default_button_drives_the_enter_key(self, harness):
        """`default` 决定**回车**落到哪个按钮上。

        不去读按钮的 ``focus``：Qt Quick Controls 自己管这个属性（实测读到的恒为
        false），真正有意义的是"回车触发哪个"，所以断言钉在 ``activateDefault()`` 上。
        """
        harness.present("confirm", confirm_payload(default=True))
        item = harness.loader_item()
        assert item.property("defaultIsConfirm") is True
        assert harness.visual_one("okButton").property("primary") is True, "确定按钮始终是主按钮（旧实现也如此）"
        assert harness.invoke(item, "activateDefault") is True
        assert harness.answered == [True], "default=true 时回车应当落到「确定」"

        harness.present("confirm", confirm_payload(default=False))
        item = harness.loader_item()
        assert item.property("defaultIsConfirm") is False
        harness.invoke(item, "activateDefault")
        assert harness.answered == [True, False], "default=false 时回车应当落到「取消」"
        assert harness.bridge.pendingDialogs() == []

    def test_real_mouse_click_on_ok_button(self, harness):
        """真鼠标点击（不是 invokeMethod）—— 证明按钮真的接上了。"""
        harness.present("confirm", confirm_payload())
        harness.click(harness.visual_one("okButton"))
        assert harness.answered == [True]

    def test_button_labels_come_from_i18n(self, harness):
        harness.present("confirm", confirm_payload())
        assert harness.visual_one("okButton").property("text") == harness.tr.map["confirm"]
        assert harness.visual_one("cancelButton").property("text") == harness.tr.map["cancel"]
        harness.invoke(harness.loader_item(), "cancel")


# ─── 3. 提示框（info / warning / error 三合一） ─────────────


class TestMessageDialogs:
    @pytest.mark.parametrize("kind", ("info", "warning", "error"))
    def test_alert_replies_fallback_like_recording_host(self, harness, kind):
        harness.present(kind, alert_payload(kind))
        item = harness.loader_item()
        assert item is not None and item.objectName() == "messageDialog"
        assert item.property("level") == kind
        assert harness.visual_one("dialogMessage").property("text") == "提示内容"

        assert harness.invoke(item, "accept") is True
        assert harness.answered == [None], "提示类不需要作答内容，回填兜底值（None）"

        host = RecordingDialogHost()
        recorded: List[Any] = []
        host.present(kind, alert_payload(kind), recorded.append)
        assert harness.answered == recorded, "与录制版宿主的回填值必须一致"

    def test_unknown_level_falls_back_to_info(self, harness):
        harness.present("info", {"title": "t", "message": "m", "level": "nonsense", FALLBACK_KEY: None})
        item = harness.loader_item()
        assert item.property("level") == "info", "未知 level 按 info 处理（与端口一致）"
        harness.invoke(item, "accept")
        assert harness.answered == [None]


# ─── 4. 文本输入框 ──────────────────────────────────────────


class TestTextInputDialog:
    def test_initial_is_prefilled_and_trimmed_on_accept(self, harness):
        harness.present("ask_text", ask_payload())
        item = harness.loader_item()
        assert item is not None and item.objectName() == "textInputDialog"
        field = harness.visual_one("inputField")
        assert field.property("text") == "初值  ", "预填 initial（不 trim，用户的输入还没发生）"
        assert item.property("fieldText") == "初值  "

        assert harness.invoke(item, "accept") is True
        assert harness.answered == ["初值"], "明文输入按 Tk 版语义 strip()"

    def test_password_is_masked_and_not_stripped(self, harness):
        harness.present("ask_text", ask_payload(password=True, initial=" pw "))
        item = harness.loader_item()
        field = harness.visual_one("inputField")
        assert field.property("text") == " pw "
        display = str(field.property("displayText"))
        assert display != " pw ", "密码框必须掩码显示"
        assert len(display) == 4, f"掩码长度应当与明文一致，实际 {display!r}"

        harness.invoke(item, "accept")
        assert harness.answered == [" pw "], "密码不做 strip（空格可能是密码的一部分）"

    def test_cancel_replies_fallback(self, harness):
        harness.present("ask_text", ask_payload())
        harness.invoke(harness.loader_item(), "cancel")
        assert harness.answered == [None]

    def test_prompt_is_shown(self, harness):
        harness.present("ask_text", ask_payload(prompt="请输入版本名"))
        assert harness.visual_one("dialogPrompt").property("text") == "请输入版本名"
        harness.invoke(harness.loader_item(), "cancel")


# ─── 5. 选项框 ──────────────────────────────────────────────


class TestChoiceDialog:
    def test_default_option_is_primary(self, harness):
        harness.present("choose", choose_payload("lite"))
        item = harness.loader_item()
        assert item is not None and item.objectName() == "choiceDialog"
        assert item.property("primaryIndex") == 1
        buttons = harness.visual_all("optionButton")
        assert [b.property("text") for b in buttons] == ["完整版", "轻量版", "都不要"]
        assert [bool(b.property("primary")) for b in buttons] == [False, True, False]

    def test_select_answers_value(self, harness):
        harness.present("choose", choose_payload("lite"))
        assert harness.invoke(harness.loader_item(), "selectDefault") is True
        assert harness.answered == ["lite"]

        host = RecordingDialogHost(answers={"choose": "lite"})
        recorded: List[Any] = []
        host.present("choose", choose_payload("lite"), recorded.append)
        assert harness.answered == recorded

    def test_no_default_means_first_option_is_primary(self, harness):
        harness.present("choose", choose_payload(None))
        assert harness.loader_item().property("primaryIndex") == 0
        harness.invoke(harness.loader_item(), "selectDefault")
        assert harness.answered == ["yes"]

    def test_disabled_option_and_hint(self, harness):
        payload = choose_payload(None)
        payload["options"] = [Choice("a", "可用"), Choice("b", "不可用", disabled=True)]
        payload["hint"] = "选一个吧"
        harness.present("choose", payload)
        assert [bool(b.property("enabled")) for b in harness.visual_all("optionButton")] == [True, False]
        hint = harness.visual_one("dialogHint")
        assert hint.property("text") == "选一个吧"
        assert bool(hint.property("visible")) is True
        harness.invoke(harness.loader_item(), "cancel")

    def test_cancel_replies_fallback(self, harness):
        harness.present("choose", choose_payload())
        harness.invoke(harness.loader_item(), "cancel")
        assert harness.answered == [None]


# ─── 6. 宿主处理不了的 kind（最容易漏的一条） ───────────────


class TestUnsupportedKind:
    def test_unknown_kind_replies_fallback_immediately(self, harness):
        """契约：宿主处理不了的 kind 也必须 ``reply(payload['fallback'])``。"""
        dialog_id = harness.present("brand_new_kind", {"title": "t", FALLBACK_KEY: "FB"})
        assert dialog_id > 0, "请求应当被送到 QML 宿主（桥不做 kind 白名单）"
        assert harness.answered == ["FB"], "认不出的 kind 必须立刻回填兜底值，不能挂着让人等到超时"
        assert harness.bridge.pendingDialogs() == []
        assert harness.loader_item() is None, "认不出的 kind 不该建出任何弹窗"

    def test_unknown_kind_with_non_string_fallback(self, harness):
        harness.present("another_new_kind", {"title": "t", FALLBACK_KEY: 42})
        assert harness.answered == [42]

    def test_unknown_kind_does_not_enter_the_queue(self, harness):
        harness.present("brand_new_kind", {"title": "t", FALLBACK_KEY: 1})
        host = harness.find("dialogHost")
        # `property var queue` 读出来是 QJSValue，先 toVariant() 再比
        assert list(host.property("queue").toVariant()) == [], "认不出的 kind 不该进队列"


# ─── 7. 队列是模态的 ────────────────────────────────────────


class TestDialogQueue:
    def test_second_request_waits_for_the_first(self, harness):
        harness.present("confirm", confirm_payload())
        harness.present("ask_text", ask_payload())
        assert len(harness.bridge.pendingDialogs()) == 2
        assert harness.loader_item().objectName() == "confirmDialog", "同时只显示队首那一个"
        assert harness.answered == []

        harness.invoke(harness.loader_item(), "accept")
        assert harness.answered == [True]
        harness.pump(40)
        assert harness.loader_item() is not None
        assert harness.loader_item().objectName() == "textInputDialog", "第一条答完要接着显示第二条"

        harness.invoke(harness.loader_item(), "cancel")
        assert harness.answered == [True, None]
        harness.pump(20)
        assert harness.bridge.pendingDialogs() == []
        assert harness.loader_item() is None


# ─── 8. 进度面板（独立于对话框队列，也独立于 Toast） ────────


class TestProgressPanel:
    def test_determinate_progress_and_close(self, harness):
        harness.bridge.show_progress(
            {
                "report": ProgressReport(title="下载", message="第一步", current=1, total=4),
                "title": "下载",
                "heading": "下载中",
                "detail": None,
                "cancel_label": "",
            }
        )
        harness.pump(40)
        item = harness.progress_item()
        assert item is not None and item.objectName() == "progressDialog"
        assert harness.visual_one("progressHeading").property("text") == "下载中"
        assert harness.visual_one("progressMessage").property("text") == "第一步"
        assert harness.visual_one("progressDetail").property("text") == "1 / 4"
        bar = harness.visual_one("progressBar")
        assert bool(bar.property("visible")) is True
        assert abs(float(bar.property("value")) - 0.25) < 1e-6
        assert bool(harness.visual_one("progressCancelButton").property("visible")) is False, "没有 on_cancel 就不该有取消按钮"

        # 第二次上报（同一个窗口，只更新数据）
        harness.bridge.show_progress(
            {"report": ProgressReport(title="下载", message="第二步", current=3, total=4), "title": "下载"}
        )
        harness.pump(40)
        assert abs(float(harness.visual_one("progressBar").property("value")) - 0.75) < 1e-6
        assert harness.visual_one("progressMessage").property("text") == "第二步"

        harness.bridge.close_progress()
        harness.pump(40)
        assert harness.progress_item() is None

    def test_indeterminate_uses_busy_indicator(self, harness):
        harness.bridge.show_progress({"report": ProgressReport(title="扫描", current=-1, total=0), "title": "扫描"})
        harness.pump(40)
        assert bool(harness.visual_one("progressBar").property("visible")) is False
        assert bool(harness.visual_one("progressBusy").property("visible")) is True
        harness.bridge.close_progress()
        harness.pump(20)

    def test_cancel_button_calls_the_callback_without_closing(self, harness):
        calls: List[int] = []
        harness.bridge.show_progress(
            {
                "report": ProgressReport(title="下载", message="下载中"),
                "title": "下载",
                "cancel_label": "停下来",
                "on_cancel": lambda: calls.append(1),
            }
        )
        harness.pump(40)
        button = harness.visual_one("progressCancelButton")
        assert bool(button.property("visible")) is True
        assert button.property("text") == "停下来"

        harness.click(button)  # 真点一下取消
        assert calls == [1]
        assert harness.progress_item() is not None, "取消只转调回调，关窗由任务调 close_progress（与 Tk 版一致）"

        harness.bridge.close_progress()
        harness.pump(20)

    def test_progress_is_not_a_toast(self, harness):
        """旧界面里进度是独立窗口而不是右下角通知 —— 两者不能混。"""
        harness.bridge.show_progress({"report": ProgressReport(title="下载", current=1, total=2), "title": "下载"})
        harness.pump(40)
        assert harness.progress_item() is not None
        assert harness.toast_cards() == [], "进度不该出现在 Toast 队列里"
        harness.bridge.close_progress()
        harness.pump(20)


# ─── 9. Toast：向上堆叠、淡入淡出、超时、超上限 ─────────────


class TestToastStack:
    def test_new_toast_stacks_upward(self, harness):
        """新 toast 往上叠、y 递减（旧实现 `ui/dialogs.py:98-110` 的方向）。"""
        for i in range(3):
            harness.bridge.notify({"message": f"消息 {i}", "level": "info", "duration_ms": 30000})
        harness.pump(300)

        cards = harness.toast_cards()
        assert len(cards) == 3
        ys = [float(c.property("y")) for c in cards]
        assert ys == sorted(ys, reverse=True), f"y 必须随槽号递减（第 0 个最靠下），实际 {ys}"
        assert ys[0] > ys[1] > ys[2]

        host = harness.find("toastHost")
        item_h = float(host.property("itemHeight"))
        gap = float(host.property("itemGap"))
        margin = float(host.property("itemMargin"))
        for upper, lower in zip(ys, ys[1:]):
            assert abs((upper - lower) - (item_h + gap)) < 0.5, "相邻两条的间距应当是 高 + 间隙"
        assert abs(ys[0] - (float(host.property("height")) - margin - item_h)) < 0.5, "最下面那条贴着右下角"

        right_x = float(host.property("width")) - float(host.property("itemWidth")) - margin
        assert all(abs(float(c.property("x")) - right_x) < 0.5 for c in cards), "都靠右"

    def test_toast_fades_in(self, harness):
        host = harness.find("toastHost")
        host.setProperty("fadeInDuration", 1200)  # 放慢，好在动画中途采样
        harness.bridge.notify({"message": "淡入", "duration_ms": 30000})
        assert harness.wait_until(
            lambda: harness.toast_cards() and 0.0 < float(harness.toast_cards()[0].property("opacity")) < 1.0,
            timeout_s=2.0,
        ), "淡入应当在动画中途出现过中间值"
        assert harness.wait_until(
            lambda: float(harness.toast_cards()[0].property("opacity")) > 0.99, timeout_s=3.0
        ), "淡入结束应当是完全不透明"

    def test_expiry_fades_out_then_removes(self, harness):
        host = harness.find("toastHost")
        host.setProperty("fadeOutDuration", 900)
        harness.bridge.notify({"message": "会消失", "level": "info", "duration_ms": TOAST_MS})

        assert harness.wait_until(lambda: not harness.bridge.activeToasts(), timeout_s=2.5), "桥到点没摘掉 Toast"
        assert harness.wait_until(
            lambda: harness.toast_cards() and float(harness.toast_cards()[0].property("opacity")) < 0.9,
            timeout_s=2.0,
        ), "淡出没有开始（桥摘掉之后界面要渐隐，不能'啪'地消失）"
        cards = harness.toast_cards()
        assert cards, "淡出中途委托应当还在"
        assert float(cards[0].property("opacity")) > 0.0, "淡出是渐隐而不是瞬间归零"
        assert harness.wait_until(lambda: not harness.toast_cards(), timeout_s=3.0), "淡出结束应当销毁委托"

    def test_cap_evicts_oldest_and_nothing_goes_offscreen(self, harness):
        for i in range(MAX_TOASTS + 2):
            harness.bridge.notify({"message": f"m{i}", "level": "info", "duration_ms": 30000})
        harness.pump(60)
        assert len(harness.bridge.activeToasts()) == MAX_TOASTS
        assert harness.wait_until(
            lambda: len(harness.visual_all("toastItem")) <= MAX_TOASTS, timeout_s=2.0
        ), "被挤掉的那条淡出之后应当销毁，界面上最多留 6 条"

        messages = [c.property("message") for c in harness.toast_cards()]
        assert "m0" not in messages and "m1" not in messages, "被挤掉的应当是最旧的两条"
        assert messages == [f"m{i}" for i in range(2, MAX_TOASTS + 2)]

        # 全都在窗口里（旧实现的 down 模式会跑出下沿）
        host = harness.find("toastHost")
        top = float(host.property("itemMargin"))
        for card in harness.toast_cards():
            y = float(card.property("y"))
            assert y >= top - 0.5, f"Toast 跑到窗口上沿之外了：y={y}"
            assert y + float(host.property("itemHeight")) <= float(host.property("height")) + 0.5

    def test_clear_toasts_empties_the_qml_queue(self, harness):
        for i in range(3):
            harness.bridge.notify({"message": f"c{i}", "duration_ms": 30000})
        harness.pump(80)
        assert len(harness.visual_all("toastItem")) == 3

        harness.bridge.clearToasts()  # 退出链要调的那个槽
        assert harness.wait_until(lambda: not harness.visual_all("toastItem"), timeout_s=2.0), "clearToasts 之后界面要清空"

    def test_dismiss_by_click(self, harness):
        harness.bridge.notify({"message": "点我", "duration_ms": 30000})
        harness.pump(80)
        harness.click(harness.toast_cards()[0])
        assert harness.bridge.activeToasts() == []
        assert harness.wait_until(lambda: not harness.visual_all("toastItem"), timeout_s=2.0)


# ─── 10. 负例自检：把承重的那一行改掉，断言必须变红 ─────────


class TestNegativeSelfChecks:
    """三条变异测试，证明前面的断言不是空的（负例自己也会因为源码改不动而变红）。"""

    def test_negative_toast_stacking_direction(self, qt_app, tmp_path):
        """N1：把 slotY 的减号改成加号 —— "y 递减"的断言必须失败，且会跑出下沿。"""
        box = make_harness(
            tmp_path,
            {
                "components/ToastHost.qml": (
                    "return height - itemMargin - itemHeight - slot * (itemHeight + itemGap)",
                    "return height - itemMargin - itemHeight + slot * (itemHeight + itemGap)",
                )
            },
        )
        try:
            for i in range(3):
                box.bridge.notify({"message": f"n{i}", "duration_ms": 30000})
            box.pump(300)
            cards = box.toast_cards()
            assert len(cards) == 3

            ys = [float(c.property("y")) for c in cards]
            assert ys != sorted(ys, reverse=True), "变异之后断言竟然还是过的 —— 那条断言是空的"
            by_slot = sorted((int(c.property("slot")), float(c.property("y"))) for c in cards)
            assert by_slot[0][1] < by_slot[-1][1], "方向被反转：槽号越大 y 越大"
            assert by_slot[-1][1] + float(box.find("toastHost").property("itemHeight")) > float(
                box.find("toastHost").property("height")
            ), "反向堆叠会把 toast 顶出窗口下沿 —— 这正是要避免的"
        finally:
            box.close()

    def test_negative_unknown_kind_swallowed(self, qt_app, tmp_path):
        """N2：把"认不出的 kind 立刻回兜底值"删掉 —— 调用方就只能干等到超时。"""
        box = make_harness(
            tmp_path,
            {
                "components/dialogs/DialogHost.qml": (
                    '            failRequest(request, "unknown kind")\n            return',
                    "            return",
                )
            },
        )
        try:
            box.present("brand_new_kind", {"title": "t", FALLBACK_KEY: "FB"})
            assert box.answered == [], "变异之后不该有人回填（否则这条负例没意义）"
            box.pump(250)
            assert box.answered == [], "什么都不做 -> 调用方只能干等到端口超时"
            assert len(box.bridge.pendingDialogs()) == 1, "请求会一直挂在桥上"
        finally:
            box.close()

    def test_negative_confirm_cancel_becomes_fallback(self, qt_app, tmp_path):
        """N3：把确认框的「取消」改成回兜底值 —— default=True 时答案会变成 true。"""
        box = make_harness(
            tmp_path,
            {
                "components/dialogs/ConfirmDialog.qml": (
                    "    function cancel() {\n        submit(false)\n    }",
                    "    function cancel() {\n        submit(request ? request.fallback : false)\n    }",
                )
            },
        )
        try:
            box.present("confirm", confirm_payload(default=True))
            box.invoke(box.loader_item(), "cancel")
            assert box.answered == [True], "变异之后「取消」变成了兜底值（= default = true）"
            assert box.answered != [False], "正确实现（未变异）必须是 false —— 说明上面那条断言不是空的"
        finally:
            box.close()


# ─── 11. 全链路：worker -> QtUIPort -> 桥 -> 真 QML 弹窗 -> 回 worker ──


class TestPortToQmlEndToEnd:
    def test_worker_confirm_answered_by_the_real_qml_dialog(self, harness):
        """一条真正意义上的端到端：四段全是真的（见 ``poc/dialog_e2e_2_13.txt`` 的同款过程）。"""
        from app.bridges.ui_port_qt import QtUIPort

        port = QtUIPort(harness.bridge, poll_interval_ms=10, timeout_s=5.0)
        port.start()
        try:
            assert port.is_available() is True
            box: Dict[str, Any] = {}

            def _worker() -> None:
                box["answer"] = port.confirm("删除版本", "确定要删除吗？", default=False)

            thread = threading.Thread(target=_worker, daemon=True)
            thread.start()
            assert harness.wait_until(lambda: harness.loader_item() is not None), "QML 弹窗没有出现"
            item = harness.loader_item()
            assert item.objectName() == "confirmDialog"
            assert harness.visual_one("dialogTitle").property("text") == "删除版本"
            assert item.property("defaultIsConfirm") is False

            harness.click(harness.visual_one("okButton"))  # 真鼠标点「确定」
            thread.join(3.0)
            assert not thread.is_alive(), "worker 没有被放行"
            assert box["answer"] is True, "default=False 时只有界面真的作答才会是 True（超时兜底是 False）"
            assert harness.wait_until(lambda: harness.loader_item() is None), "答完弹窗要摘掉"
        finally:
            port.stop()


# ─── 12. 全链路不留 QML 报错 ────────────────────────────────


class TestNoQmlErrors:
    def test_full_round_trip_has_no_qml_errors(self, harness):
        harness.present("confirm", confirm_payload())
        harness.invoke(harness.loader_item(), "accept")

        harness.present("ask_text", ask_payload())
        harness.invoke(harness.loader_item(), "accept")

        harness.present("choose", choose_payload())
        harness.invoke(harness.loader_item(), "selectDefault")

        harness.present("error", alert_payload("error"))
        harness.invoke(harness.loader_item(), "accept")

        harness.present("brand_new_kind", {"title": "t", FALLBACK_KEY: 1})

        harness.bridge.notify({"message": "一条通知", "level": "success", "duration_ms": TOAST_MS})
        harness.bridge.show_progress(
            {"report": ProgressReport(title="进度", message="m", current=1, total=2), "title": "进度"}
        )
        harness.pump(50)
        harness.bridge.close_progress()
        assert harness.wait_until(lambda: not harness.bridge.activeToasts(), timeout_s=3.0)

        assert harness.answered == [True, "初值", "lite", None, 1]
        assert harness.errors() == [], f"QML 报了错：{harness.errors()}"

