"""``DialogBridge`` 的行为测试（阶段 2 任务 2.13）。

覆盖点（与任务书一一对应）：

1. 六个 kind 的 payload 形状与"用户作答"回填值，**逐条与 ``RecordingDialogHost`` 对照** ——
   同样的输入必须给出同样的答案（录制版是"功能等价"的基准）；
2. 宿主认不出的 kind 必须回填 ``fallback``（这条最容易漏，单独一组）；
3. 重复作答只认第一次（``QtUIPort`` 也有这道保护，但本桥要能独立成立）；
4. 进度会话：标题沿用、``on_cancel`` 转调、销毁时放行；
5. Toast：超时到期、上限 6 条挤最旧、``clearToasts``、空消息忽略；
6. ``set_clipboard`` 的主线程纪律与真实写入；
7. ``is_available()`` 的生命周期（未握手 / 握手后 / 宿主销毁后）；
8. **端到端**：worker 线程调 ``ui_port.confirm(...)`` → 主线程 QML 宿主作答 → 答案回到 worker。

不依赖 pytest-qt：自己建 ``QGuiApplication``（offscreen），用 ``QTest.qWait`` 驱动事件
循环；"主线程"就是 pytest 主线程，"worker 线程"是显式起的 ``threading.Thread``。

> 真 QML 的那一半在 ``tests/test_dialogs_qml.py``；本文件里的"QML 作答"用直接调用
> ``submitDialog`` / ``cancelDialog`` 模拟（那两个槽就是 QML 弹窗按钮的回填口）。
"""

from __future__ import annotations

import copy
import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

# 必须在 import PySide6 之前设置：offscreen 平台插件不依赖显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

import pytest  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.bridges.dialog_bridge import (  # noqa: E402
    DEFAULT_TOAST_DURATION_MS,
    KIND_COMPONENT,
    MAX_TOASTS,
    MIN_TOAST_DURATION_MS,
    QML_NAME,
    DialogBridge,
)
from app.bridges.dialog_host import (  # noqa: E402
    PRESENT_KINDS,
    DialogHost,
    FALLBACK_KEY,
    RecordingDialogHost,
)
from app.bridges.ui_port_qt import QtUIPort  # noqa: E402
from app.ports import Choice, ProgressReport  # noqa: E402

#: 测试用的短超时（避免任何用例挂住）
SHORT_TIMEOUT_S = 0.3
#: 测试用的轮询周期
POLL_MS = 10
#: worker 线程预算
WORKER_BUDGET_S = 5.0
#: Toast 到期一律用下限时长（300ms），等它到期时多给一点余量
TOAST_WAIT_MS = MIN_TOAST_DURATION_MS + 250

#: 六个 kind 各一份"标准 payload"，六种 kind 的输入在两组测试里完全一致
STANDARD_PAYLOADS: Dict[str, Dict[str, Any]] = {
    "info": {"title": "标题", "message": "内容", "level": "info", "blocking": False, FALLBACK_KEY: None},
    "warning": {"title": "标题", "message": "内容", "level": "warning", "blocking": False, FALLBACK_KEY: None},
    "error": {"title": "标题", "message": "内容", "level": "error", "blocking": False, FALLBACK_KEY: None},
    "confirm": {"title": "标题", "message": "内容", "default": True, FALLBACK_KEY: True},
    "ask_text": {"title": "标题", "prompt": "提示", "initial": "初值", "password": False, FALLBACK_KEY: None},
    "choose": {
        "title": "标题",
        "prompt": "提示",
        "options": [Choice("yes", "完整版"), Choice("lite", "轻量版"), Choice("no", "取消")],
        "default": "lite",
        "hint": "选一个",
        FALLBACK_KEY: None,
    },
}

#: 用户"点确定/选一项"时会回填的值（QML 组件的行为：见 qml/components/dialogs/*.qml）
ACCEPTED_VALUES: Dict[str, Any] = {
    "info": None,
    "warning": None,
    "error": None,
    "confirm": True,
    "ask_text": "用户输入",
    "choose": "lite",
}

#: 用户"点取消/按 Esc/关窗"时的回填值
CANCELLED_VALUES: Dict[str, Any] = {
    "info": None,
    "warning": None,
    "error": None,
    "confirm": False,  # 确认框的「取消」是 false，不是 fallback（fallback 可能是 true）
    "ask_text": None,
    "choose": None,
}


# ─── 夹具与驱动辅助 ─────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def qt_app() -> QGuiApplication:
    """整个会话共用同一个 ``QGuiApplication``（offscreen）。"""
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


@pytest.fixture()
def bridge() -> DialogBridge:
    """已握手（可用）的桥。用例结束不销毁 —— 桥没有需要收尾的线程/定时器。"""
    b = DialogBridge()
    b.markReady()
    return b


def pump(ms: int = 30) -> None:
    """推进 Qt 事件循环。"""
    QTest.qWait(ms)


def run_in_worker(fn: Callable[[], Any], *, budget_s: float = WORKER_BUDGET_S) -> Any:
    """在 worker 线程里执行 ``fn``，主线程一边跑事件循环一边等它结束。"""
    box: Dict[str, Any] = {}

    def _run() -> None:
        try:
            box["value"] = fn()
        except BaseException as e:  # noqa: BLE001 - 把异常带回主线程再抛
            box["error"] = e

    thread = threading.Thread(target=_run, daemon=True)
    started = time.monotonic()
    thread.start()
    while thread.is_alive() and time.monotonic() - started < budget_s:
        QTest.qWait(5)
    thread.join(1.0)
    assert not thread.is_alive(), "worker 线程没有在预算时间内结束（被卡住了）"
    if "error" in box:
        raise box["error"]
    return box.get("value")


class _Collector:
    """收集一次 ``present`` 的回填值（可能在之后的某一轮事件循环里才到）。"""

    def __init__(self) -> None:
        self.values: List[Any] = []
        self.calls = 0

    def __call__(self, value: Any) -> None:
        self.calls += 1
        self.values.append(value)

    @property
    def last(self) -> Any:
        return self.values[-1] if self.values else None


def present_and_get(b: DialogBridge, kind: str, payload: Dict[str, Any]) -> Tuple[int, _Collector]:
    """发一个请求，返回 (payload 里的 id, 收集器)。"""
    collector = _Collector()
    requested: List[Dict[str, Any]] = []
    b.dialogRequested.connect(requested.append)
    b.present(kind, payload, collector)
    assert requested, f"{kind} 的 dialogRequested 没有发出去（宿主没收到请求）"
    assert requested[-1]["kind"] == kind
    return int(requested[-1]["id"]), collector


def record_with(host: RecordingDialogHost, kind: str, payload: Dict[str, Any]) -> Tuple[Any, int]:
    """用录制版宿主走一遍同样的输入，返回 (回填值, 回填次数)。"""
    collector = _Collector()
    host.present(kind, payload, collector)
    return collector.last, collector.calls


def qml_cancel(b: DialogBridge, kind: str, dialog_id: int) -> bool:
    """模拟 QML 组件里的「取消 / Esc / 关闭」。

    `confirm` 的取消是 **false**（`ConfirmDialog.cancel()`），而不是兜底值 —— 兜底值
    等于 `default`，默认按钮为真时两者会不一样；其余 kind 的取消就是"答不上来"，
    走 `cancelDialog`（回 `payload.fallback`）。
    """
    if kind == "confirm":
        return b.submitDialog(dialog_id, CANCELLED_VALUES["confirm"])
    return b.cancelDialog(dialog_id)


def wait_until(predicate: Callable[[], bool], *, timeout_s: float = 3.0, step_ms: int = 5) -> bool:
    """一边跑事件循环一边等条件成立（跨线程用例的同步点）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        QTest.qWait(step_ms)
    return bool(predicate())


# ─── 1. 协议与生命周期 ──────────────────────────────────────


class TestProtocolAndAvailability:
    def test_implements_dialog_host_protocol(self, bridge):
        assert isinstance(bridge, DialogHost)
        assert QML_NAME == "Dialogs"  # 契约第四节冻结的注册名
        assert set(KIND_COMPONENT) == set(PRESENT_KINDS), "六个 kind 都要有对应组件"

    def test_unavailable_before_handshake_replying_fallback(self):
        b = DialogBridge()
        assert b.is_available() is False
        seen: List[Dict[str, Any]] = []
        b.dialogRequested.connect(seen.append)
        got = _Collector()
        b.present("confirm", dict(STANDARD_PAYLOADS["confirm"]), got)
        assert got.values == [True], "QML 没就绪时必须立刻用兜底值收尾"
        assert seen == [], "没握手就不该把请求发给不存在的界面"

    def test_available_after_handshake(self, bridge):
        assert bridge.is_available() is True
        assert bridge.describe()["attached"] is True
        bridge.markReady()  # 幂等
        assert bridge.is_available() is True

    def test_mark_closing_releases_waiting_callers(self, bridge):
        payload = dict(STANDARD_PAYLOADS["confirm"])
        _dialog_id, got = present_and_get(bridge, "confirm", payload)
        assert len(bridge.pendingDialogs()) == 1

        bridge.markClosing()
        assert got.values == [payload[FALLBACK_KEY]], "窗口销毁时必须放行调用方（不能让它等到超时）"
        assert bridge.pendingDialogs() == []
        assert bridge.is_available() is False

        # 引擎重建 / 根组件重载后重新握手仍然可用
        bridge.markReady()
        assert bridge.is_available() is True

    def test_present_from_worker_thread_replies_fallback(self, bridge):
        got = _Collector()
        run_in_worker(lambda: bridge.present("confirm", dict(STANDARD_PAYLOADS["confirm"]), got))
        assert got.values == [True], "非主线程调用一律按兜底值收尾（绝不碰 QML）"
        assert bridge.pendingDialogs() == []

    def test_describe_is_json_serializable(self, bridge):
        info = bridge.describe()
        assert info["class"] == "DialogBridge"
        assert info["qml_name"] == "Dialogs"
        assert info["available"] is True
        assert info["max_toasts"] == MAX_TOASTS
        json.dumps(info)  # 不抛异常即为合格
        assert "DialogBridge" in repr(bridge)


# ─── 2. 六个 kind：payload 形状 + 与录制版逐条对照 ──────────


class TestSixKindsParity:
    @pytest.mark.parametrize("kind", sorted(PRESENT_KINDS))
    def test_accept_matches_recording_host(self, bridge, kind):
        """用户"点确定/选一项"时，QML 版与录制版的回填值必须一致。"""
        payload = copy.deepcopy(STANDARD_PAYLOADS[kind])
        dialog_id, got = present_and_get(bridge, kind, payload)
        assert bridge.submitDialog(dialog_id, ACCEPTED_VALUES[kind]) is True

        host = RecordingDialogHost(answers={kind: ACCEPTED_VALUES[kind]})
        recorded, calls = record_with(host, kind, payload)
        assert calls == 1
        assert got.values == [recorded], f"{kind} 的作答回填值与录制版不一致"

    @pytest.mark.parametrize("kind", sorted(PRESENT_KINDS))
    def test_cancel_matches_recording_host(self, bridge, kind):
        """用户"点取消/按 Esc/关窗"时同样要与录制版一致。"""
        payload = copy.deepcopy(STANDARD_PAYLOADS[kind])
        dialog_id, got = present_and_get(bridge, kind, payload)
        assert qml_cancel(bridge, kind, dialog_id) is True

        host = RecordingDialogHost(answers={kind: CANCELLED_VALUES[kind]})
        recorded, calls = record_with(host, kind, payload)
        assert calls == 1
        assert got.values == [recorded]

    def test_confirm_cancel_is_false_not_fallback(self, bridge):
        """`confirm` 的取消是 **false**；默认按钮为真时特别容易把它写成 fallback。"""
        payload = {"title": "t", "message": "m", "default": True, FALLBACK_KEY: True}
        dialog_id, got = present_and_get(bridge, "confirm", payload)
        bridge.cancelDialog(dialog_id)
        assert got.values == [True], "cancelDialog 是「答不上来」的出口，回的是兜底值（= default）"

        dialog_id, got2 = present_and_get(bridge, "confirm", payload)
        bridge.submitDialog(dialog_id, CANCELLED_VALUES["confirm"])
        assert got2.values == [False], "QML 的「取消」按钮回填 false"

    @pytest.mark.parametrize("kind", sorted(PRESENT_KINDS))
    def test_payload_shape_for_qml(self, bridge, kind):
        payload = copy.deepcopy(STANDARD_PAYLOADS[kind])
        seen: List[Dict[str, Any]] = []
        bridge.dialogRequested.connect(seen.append)

        dialog_id, _got = present_and_get(bridge, kind, payload)
        item = seen[-1]
        for key in ("id", "kind", "title", "message", "prompt", "level", "fallback", "options"):
            assert key in item, f"{kind} 的 QML payload 缺少 {key}"
        assert item["fallback"] == payload[FALLBACK_KEY], "兜底值必须原样透出（宿主不许自己猜）"
        assert item["id"] == dialog_id

        second_id, _got2 = present_and_get(bridge, kind, copy.deepcopy(payload))
        assert second_id > dialog_id, "id 必须单调递增（QML 用它当令牌）"

    def test_alert_level_is_normalized(self, bridge):
        seen: List[Dict[str, Any]] = []
        bridge.dialogRequested.connect(seen.append)
        for raw, expected in (("warning", "warning"), ("error", "error"), ("info", "info"), ("bogus", "info")):
            payload = {"title": "t", "message": "m", "level": raw, FALLBACK_KEY: None}
            bridge.present("error" if raw == "error" else "warning", payload, _Collector())
            assert seen[-1]["level"] == expected, f"level {raw} 的归一化不对"

    def test_choose_options_are_converted_from_choice_objects(self, bridge):
        seen: List[Dict[str, Any]] = []
        bridge.dialogRequested.connect(seen.append)
        payload = copy.deepcopy(STANDARD_PAYLOADS["choose"])
        payload["options"].append(Choice("disabled_one", "不可选", description="说明", disabled=True, data={"k": 1}))
        bridge.present("choose", payload, _Collector())
        options = seen[-1]["options"]
        assert [o["value"] for o in options] == ["yes", "lite", "no", "disabled_one"]
        assert options[0]["label"] == "完整版"
        assert options[3] == {
            "value": "disabled_one",
            "label": "不可选",
            "description": "说明",
            "disabled": True,
            "data": {"k": 1},
        }
        assert seen[-1]["default"] == "lite", "choose 的 default（选项值）要原样透出"

    def test_ask_text_password_flag_is_passed_through(self, bridge):
        seen: List[Dict[str, Any]] = []
        bridge.dialogRequested.connect(seen.append)
        bridge.present("ask_text", {"title": "t", "prompt": "p", "initial": "abc", "password": True, FALLBACK_KEY: None}, _Collector())
        assert seen[-1]["password"] is True
        assert seen[-1]["initial"] == "abc"


# ─── 3. 宿主处理不了的 kind ────────────────────────────────


class TestUnsupportedKind:
    def test_unknown_kind_is_forwarded_and_cancel_replies_fallback(self, bridge):
        """认不出的 kind 也必须有人交代 —— 否则调用方只能干等到超时。"""
        seen: List[Dict[str, Any]] = []
        bridge.dialogRequested.connect(seen.append)
        got = _Collector()
        bridge.present("brand_new_kind", {"title": "t", FALLBACK_KEY: "FB"}, got)
        assert seen and seen[-1]["kind"] == "brand_new_kind"
        assert got.values == [], "还没作答，不该有人回填"

        assert bridge.cancelDialog(seen[-1]["id"]) is True
        assert got.values == ["FB"], "QML 认不出 kind 时回的必须是 payload 里的兜底值"

    def test_unknown_kind_payload_without_fallback_replies_none(self, bridge):
        seen: List[Dict[str, Any]] = []
        bridge.dialogRequested.connect(seen.append)
        got = _Collector()
        bridge.present("brand_new_kind", {"title": "t"}, got)
        bridge.cancelDialog(seen[-1]["id"])
        assert got.values == [None], "payload 没给 fallback 时按 None 收尾"

    def test_unknown_kind_when_unavailable_replies_immediately(self):
        b = DialogBridge()  # 未握手
        got = _Collector()
        b.present("brand_new_kind", {"title": "t", FALLBACK_KEY: "FB"}, got)
        assert got.values == ["FB"]

    def test_matches_recording_host_for_unknown_kind(self):
        """录制版对不认识的 kind 同样是"用兜底值回答"，两边的语义要对齐。"""
        host = RecordingDialogHost()
        recorded, calls = record_with(host, "brand_new_kind", {"title": "t", FALLBACK_KEY: "FB"})
        assert (recorded, calls) == ("FB", 1)


# ─── 4. 重复作答只认第一次 ─────────────────────────────────


class TestReplyProtection:
    def test_first_answer_wins_and_second_is_rejected(self, bridge):
        dialog_id, got = present_and_get(bridge, "confirm", dict(STANDARD_PAYLOADS["confirm"]))
        closed: List[int] = []
        bridge.dialogClosed.connect(closed.append)

        assert bridge.submitDialog(dialog_id, False) is True
        assert bridge.submitDialog(dialog_id, True) is False, "第二次作答必须被拒"
        assert bridge.cancelDialog(dialog_id) is False
        assert got.values == [False]
        assert got.calls == 1
        assert closed == [dialog_id], "dialogClosed 只该发一次"

    def test_answer_after_close_returns_false(self, bridge):
        dialog_id, got = present_and_get(bridge, "ask_text", dict(STANDARD_PAYLOADS["ask_text"]))
        bridge.submitDialog(dialog_id, "x")
        assert bridge.submitDialog(dialog_id, "y") is False
        assert got.values == ["x"]

    def test_reply_exception_does_not_break_the_bridge(self, bridge):
        seen: List[Dict[str, Any]] = []
        bridge.dialogRequested.connect(seen.append)

        def bad_reply(_value: Any) -> None:
            raise RuntimeError("调用方自己的回调炸了")

        bridge.present("confirm", dict(STANDARD_PAYLOADS["confirm"]), bad_reply)
        assert bridge.submitDialog(seen[-1]["id"], True) is True  # 不许抛出去
        assert bridge.pendingDialogs() == []
        assert bridge.is_available() is True


# ─── 5. 进度会话 ────────────────────────────────────────────


class TestProgress:
    def test_show_progress_payload_matches_tk(self, bridge):
        seen: List[Dict[str, Any]] = []
        closed: List[int] = []
        bridge.progressChanged.connect(seen.append)
        bridge.progressClosed.connect(lambda: closed.append(1))

        report = ProgressReport(title="下载", message="第一步", current=1, total=4)
        bridge.show_progress({"report": report, "title": "下载", "heading": "下载中", "detail": None, "cancel_label": ""})
        item = seen[-1]
        assert item["message"] == "第一步"
        assert item["determinate"] is True
        assert item["fraction"] == 0.25
        assert item["detail"] == "1 / 4", "确定进度的 detail 与 Tk 版同形（当前 / 总量）"
        assert item["heading"] == "下载中"
        assert item["cancellable"] is False
        assert bridge.progressState()["title"] == "下载"

        bridge.close_progress()
        assert bridge.progressState() == {}
        assert closed == [1]

    def test_empty_title_keeps_previous(self, bridge):
        """端口把 `report.title is None` 解析成"沿用上一个"；桥这边也必须沿用。"""
        bridge.show_progress({"report": ProgressReport(title="下载", current=0, total=2), "title": "下载"})
        bridge.show_progress({"report": ProgressReport(title=None, message="继续", current=1, total=2), "title": None})
        state = bridge.progressState()
        assert state["title"] == "下载"
        assert state["message"] == "继续"
        assert state["fraction"] == 0.5

    def test_indeterminate_uses_detail_not_counters(self, bridge):
        bridge.show_progress(
            {"report": ProgressReport(title="t", message="m", current=-1, total=0), "title": "t", "detail": "扫描中"}
        )
        state = bridge.progressState()
        assert state["determinate"] is False
        assert state["fraction"] == 0.0
        assert state["detail"] == "扫描中"

    def test_cancel_progress_only_calls_back(self, bridge):
        calls: List[int] = []
        bridge.show_progress(
            {"report": ProgressReport(title="t"), "title": "t", "cancel_label": "停", "on_cancel": lambda: calls.append(1)}
        )
        state = bridge.progressState()
        assert state["cancellable"] is True
        assert state["cancelLabel"] == "停"

        assert bridge.cancelProgress() is True
        assert calls == [1]
        assert bridge.progressState() != {}, "取消只转调回调，不自己关窗（与 Tk 版一致）"

        bridge.close_progress()
        assert bridge.cancelProgress() is False, "会话结束后没有可取消的东西"

    def test_build_params_only_apply_at_creation(self, bridge):
        """heading / cancel_label / on_cancel 是**建窗参数**，只在首次生效（照 Tk 版）。"""
        first: List[int] = []
        second: List[int] = []
        bridge.show_progress(
            {
                "report": ProgressReport(title="t"),
                "title": "t",
                "heading": "第一个标题",
                "cancel_label": "停",
                "on_cancel": lambda: first.append(1),
            }
        )
        bridge.show_progress(
            {
                "report": ProgressReport(title="t", message="更新"),
                "title": "t",
                "heading": "第二个标题",
                "cancel_label": "别停",
                "on_cancel": lambda: second.append(1),
            }
        )
        assert bridge.progressState()["heading"] == "第一个标题"
        assert bridge.progressState()["cancelLabel"] == "停"
        bridge.cancelProgress()
        assert (first, second) == ([1], [])

    def test_missing_report_is_ignored(self, bridge):
        bridge.show_progress({"title": "t"})
        assert bridge.progressState() == {}

    def test_close_without_session_is_a_noop(self, bridge):
        closed: List[int] = []
        bridge.progressClosed.connect(lambda: closed.append(1))
        bridge.close_progress()
        assert closed == []

    def test_handshake_lost_drops_progress(self, bridge):
        bridge.show_progress({"report": ProgressReport(title="t"), "title": "t"})
        bridge.markClosing()
        assert bridge.progressState() == {}


# ─── 6. Toast ───────────────────────────────────────────────


class TestToast:
    def test_notify_reaches_qml_with_default_duration(self, bridge):
        seen: List[Dict[str, Any]] = []
        bridge.toastRequested.connect(seen.append)
        bridge.notify({"message": "下载完成", "level": "success"})
        assert seen and seen[-1]["message"] == "下载完成"
        assert seen[-1]["level"] == "success"
        assert seen[-1]["duration_ms"] == DEFAULT_TOAST_DURATION_MS
        assert [t["message"] for t in bridge.activeToasts()] == ["下载完成"]

    def test_unknown_level_and_bad_duration_are_normalized(self, bridge):
        bridge.notify({"message": "x", "level": "bogus", "duration_ms": "abc"})
        toast = bridge.activeToasts()[0]
        assert toast["level"] == "info"
        assert toast["duration_ms"] == DEFAULT_TOAST_DURATION_MS

        bridge.clearToasts()
        bridge.notify({"message": "y", "duration_ms": 1})
        assert bridge.activeToasts()[0]["duration_ms"] == MIN_TOAST_DURATION_MS

    def test_toast_expires_after_duration(self, bridge):
        bridge.notify({"message": "会自己消失", "duration_ms": MIN_TOAST_DURATION_MS})
        assert len(bridge.activeToasts()) == 1
        assert wait_until(lambda: not bridge.activeToasts(), timeout_s=2.0), "Toast 到点没有消失"
        assert bridge.describe()["toasts_expired"] == 1

    def test_cap_evicts_oldest(self, bridge):
        """超过上限时挤掉最旧的一条（决策与理由见 ToastHost.qml）。"""
        for i in range(MAX_TOASTS + 2):
            bridge.notify({"message": f"toast-{i}", "duration_ms": DEFAULT_TOAST_DURATION_MS})
        messages = [t["message"] for t in bridge.activeToasts()]
        assert len(messages) == MAX_TOASTS
        assert messages[0] == "toast-2", "最老的两条应当被挤掉"
        assert messages[-1] == f"toast-{MAX_TOASTS + 1}"
        assert bridge.describe()["toasts_evicted"] == 2

    def test_dismiss_toast_removes_it(self, bridge):
        bridge.notify({"message": "点我", "duration_ms": DEFAULT_TOAST_DURATION_MS})
        toast_id = bridge.activeToasts()[0]["id"]
        assert bridge.dismissToast(toast_id) is True
        assert bridge.activeToasts() == []
        assert bridge.dismissToast(toast_id) is False, "已经摘掉的再点一次不该报成功"

    def test_clear_toasts_empties_the_queue(self, bridge):
        for i in range(3):
            bridge.notify({"message": f"t{i}", "duration_ms": DEFAULT_TOAST_DURATION_MS})
        changed: List[int] = []
        bridge.toastsChanged.connect(lambda: changed.append(1))
        bridge.clearToasts()
        assert bridge.activeToasts() == []
        assert changed, "清空必须发 toastsChanged（QML 靠它对账）"
        bridge.clearToasts()  # 空队列再清一次不发信号也不炸
        assert len(changed) == 1

    def test_empty_message_is_ignored(self, bridge):
        bridge.notify({"message": "", "level": "info"})
        bridge.notify({})
        assert bridge.activeToasts() == []

    def test_notify_when_unavailable_is_dropped(self):
        b = DialogBridge()  # 未握手
        b.notify({"message": "没人看得到"})
        assert b.activeToasts() == []


# ─── 7. 剪贴板 ──────────────────────────────────────────────


class TestClipboard:
    def test_set_clipboard_roundtrip(self, bridge, qt_app):
        assert bridge.set_clipboard("FMCL-2.13") is True
        assert qt_app.clipboard().text() == "FMCL-2.13"

    def test_set_clipboard_from_worker_returns_false(self, bridge):
        """`QGuiApplication.clipboard()` 必须在主线程 —— 非主线程一律拒绝，不假装成功。"""
        assert run_in_worker(lambda: bridge.set_clipboard("x")) is False


# ─── 8. 与 QtUIPort 的端到端（worker -> 端口 -> 宿主 -> worker） ──


@pytest.fixture()
def port(bridge: DialogBridge) -> QtUIPort:
    """注入桥的端口；用例结束自动 stop()，不留残余 QTimer。"""
    p = QtUIPort(bridge, poll_interval_ms=POLL_MS, timeout_s=SHORT_TIMEOUT_S)
    yield p
    try:
        p.stop()
    except Exception:  # noqa: BLE001 - 收尾失败不该掩盖用例结论
        pass


class TestQtUIPortEndToEnd:
    def test_worker_confirm_answer_comes_back_from_the_host(self, bridge, port):
        """worker 调 confirm -> 主线程弹窗 -> 用户作答 -> 答案回到 worker。"""
        port.start()
        assert port.is_available() is True

        box: Dict[str, Any] = {}

        def _worker() -> None:
            box["answer"] = port.confirm("删除版本", "确定要删除吗？", default=True)

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()
        assert wait_until(lambda: bool(bridge.pendingDialogs())), "请求没有到达宿主"
        pending = bridge.pendingDialogs()[0]
        assert pending["kind"] == "confirm"
        assert pending["title"] == "删除版本"

        # 用户点「取消」：答案是 False，而超时兜底值是 True —— 两者能区分开
        bridge.submitDialog(pending["id"], False)
        thread.join(2.0)
        assert not thread.is_alive(), "worker 没有被放行"
        assert box["answer"] is False

    def test_worker_ask_text_and_choose(self, bridge, port):
        port.start()
        text, choice = run_in_worker(
            lambda: (
                _answer_next(bridge, port, "ask_text", "用户输入的名字"),
                _answer_next(bridge, port, "choose", "lite"),
            )
        )
        assert text == "用户输入的名字"
        assert choice == "lite"

    def test_worker_progress_and_title_matching_close(self, bridge, port):
        port.start()
        run_in_worker(
            lambda: port.show_progress(ProgressReport(title="下载", message="第一步", current=1, total=4))
        )
        assert wait_until(lambda: bridge.progressState() != {}), "进度没有到达宿主"
        assert bridge.progressState()["fraction"] == 0.25

        # 标题不匹配：端口自己就忽略了（与 Tk 版一致），宿主这边不该收到关闭
        run_in_worker(lambda: port.close_progress("别的标题"))
        pump(POLL_MS * 4)
        assert bridge.progressState() != {}, "标题不匹配时不该关掉当前进度"

        run_in_worker(lambda: port.close_progress("下载"))
        assert wait_until(lambda: bridge.progressState() == {}), "标题匹配时应当关掉"

    def test_worker_notify_lands_in_the_toast_queue(self, bridge, port):
        port.start()
        run_in_worker(lambda: port.notify("来自 worker 的通知", "success"))
        assert wait_until(lambda: bool(bridge.activeToasts())), "通知没有到达宿主"
        toast = bridge.activeToasts()[-1]
        assert toast["message"] == "来自 worker 的通知"
        assert toast["level"] == "success"

    def test_timeout_returns_fallback_and_dialog_stays(self, bridge, port):
        port.start()
        # 谁都不作答：端口按"用户没回答"处理，返回 default
        assert run_in_worker(lambda: port.confirm("标题", "内容", default=True)) is True
        assert bridge.pendingDialogs(), "端口没有撤销弹窗的能力（与 Tk 版同一取舍）"
        # 超时之后用户才作答：不许抛异常，桥的状态要干净
        bridge.submitDialog(bridge.pendingDialogs()[0]["id"], False)
        assert bridge.pendingDialogs() == []

    def test_port_degrades_before_handshake_then_recovers(self):
        b = DialogBridge()  # 还没握手（QML 还没加载）
        p = QtUIPort(b, poll_interval_ms=POLL_MS, timeout_s=SHORT_TIMEOUT_S)
        p.start()
        try:
            assert p.is_available() is False
            assert p.confirm("标题", "内容", default=True) is True  # NullUIPort 语义
            assert p.ask_text("标题", "提示") is None
            assert b.pendingDialogs() == []

            b.markReady()  # QML 宿主加载完成
            assert wait_until(lambda: p.is_available()), "握手之后端口应当自动变可用"
            assert p.confirm("标题", "内容", default=False) is False  # 超时兜底（没人答）
            assert b.pendingDialogs(), "现在请求应当真的到达宿主了"
        finally:
            p.stop()


def _answer_next(bridge: DialogBridge, port: QtUIPort, kind: str, value: Any) -> Any:
    """在 worker 线程里发起一次问答，主线程等它到达宿主后作答，返回 worker 拿到的值。

    ``kind`` 决定调端口的哪个方法（``ask_text`` / ``choose``）；作答值由调用方给
    （模拟用户键入 / 点某个选项）。
    """
    box: Dict[str, Any] = {}

    def _worker() -> Any:
        if kind == "ask_text":
            return port.ask_text("标题", "提示", initial="")
        if kind == "choose":
            return port.choose("标题", "提示", [Choice("yes", "完整版"), Choice("lite", "轻量版")], default="yes")
        raise AssertionError(f"_answer_next 不认识 {kind}")

    def _run() -> None:
        box["value"] = _worker()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert wait_until(lambda: bool(bridge.pendingDialogs())), f"{kind} 的请求没有到达宿主"
    pending = bridge.pendingDialogs()[0]
    assert pending["kind"] == kind
    bridge.submitDialog(pending["id"], value)
    thread.join(2.0)
    assert not thread.is_alive(), f"{kind} 的 worker 没有被放行"
    return box.get("value")
