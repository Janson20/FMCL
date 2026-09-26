"""``QtUIPort`` 的行为测试（阶段 2 任务 2.3）。

覆盖点（与任务书一一对应）：

1. 未 ``start()`` 时 10 个方法全部退化为 ``NullUIPort`` 兜底值（逐方法断言，且不许碰宿主）；
2. 主线程调用 ``confirm`` / ``ask_text`` / ``choose`` 能拿到宿主给的答案（含"答案晚到"）；
3. **worker 线程**调用 ``confirm`` 能拿到主线程给的答案 —— 这是这个端口存在的理由；
4. 宿主不 reply 时按超时返回兜底值（0.2 秒，不让测试挂住）；
5. ``is_available()`` 在 start / stop / 无宿主 / 宿主不可用四种情况下的取值；
6. ``show_progress`` / ``close_progress`` / ``set_clipboard`` / ``notify`` 的负载原样到达宿主；
7. 非主线程调用 ``start()`` 被拒绝且不崩。

不依赖 pytest-qt：这里自己建 ``QGuiApplication``（offscreen），用 ``QTest.qWait`` 驱动
Qt 事件循环。测试里的"主线程"就是 pytest 主线程；"worker 线程"是显式起的
``threading.Thread``，主线程一边 ``qWait`` 一边等它 —— 这正是真实调用方的样子
（主线程必须继续跑事件循环，队列才会被 QTimer 排空）。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional

# 必须在 import PySide6 之前设置：offscreen 平台插件不依赖显示器
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.bridges.dialog_host import NO_REPLY, DialogHost, NullDialogHost, RecordingDialogHost  # noqa: E402
from app.bridges.ui_port_qt import DEFAULT_POLL_MS, DEFAULT_TIMEOUT_S, QtUIPort  # noqa: E402
from app.ports import Choice, ProgressReport, UIPort  # noqa: E402

#: 测试用的短超时（避免任何用例挂住）
SHORT_TIMEOUT_S = 0.2
#: 测试用的轮询周期
POLL_MS = 10
#: worker 线程预算（够用又不至于挂住测试）
WORKER_BUDGET_S = 5.0


# ─── 夹具与驱动辅助 ─────────────────────────────────────────


@pytest.fixture(scope="session", autouse=True)
def qt_app() -> QGuiApplication:
    """整个会话共用同一个 ``QGuiApplication``（offscreen）。

    用 autouse：任何用例在构造端口之前都必须先有 Qt 应用实例，否则
    ``QThread.currentThread() == app.thread()`` 这条主线程判定就退化成
    "构造端口的那条线程"。重复构造 QGuiApplication 会让 Qt 直接 abort。
    """
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


@pytest.fixture()
def host() -> RecordingDialogHost:
    """默认宿主：confirm 答 True、ask_text 答"输入的文本"、choose 答 "lite"。"""
    return RecordingDialogHost(answers={"confirm": True, "ask_text": "输入的文本", "choose": "lite"})


@pytest.fixture()
def make_port():
    """端口工厂：用例结束自动 ``stop()``，不留残余 QTimer。"""
    created: List[QtUIPort] = []

    def _factory(
        dialog_host: Optional[DialogHost] = None,
        *,
        start: bool = False,
        timeout_s: float = SHORT_TIMEOUT_S,
        poll_interval_ms: int = POLL_MS,
    ) -> QtUIPort:
        port = QtUIPort(dialog_host, poll_interval_ms=poll_interval_ms, timeout_s=timeout_s)
        created.append(port)
        if start:
            port.start()
        return port

    yield _factory
    for port in created:
        try:
            port.stop()
        except Exception:  # noqa: BLE001 - 收尾失败不该掩盖用例本身的结论
            pass


def pump(ms: int = 40) -> None:
    """推进 Qt 事件循环（QTimer 与 singleShot 都会在这一步被处理）。"""
    QTest.qWait(ms)


def run_in_worker(fn: Callable[[], Any], *, budget_s: float = WORKER_BUDGET_S) -> Any:
    """在 worker 线程里执行 ``fn``，主线程一边跑事件循环一边等它结束。

    主线程的 ``qWait`` 是必须的：只有主线程继续处理事件，``QtUIPort`` 的
    QTimer 轮询体才会把队列排空。
    """
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
    assert not thread.is_alive(), "worker 线程没有在预算时间内结束（端口把它卡住了）"
    if "error" in box:
        raise box["error"]
    return box.get("value")


def timed_in_worker(fn: Callable[[], Any], *, budget_s: float = WORKER_BUDGET_S) -> Any:
    """同 ``run_in_worker``，另外返回耗时（用于超时/放行语义的断言）。"""
    started = time.monotonic()
    value = run_in_worker(fn, budget_s=budget_s)
    elapsed = time.monotonic() - started
    return value, elapsed


class _MainThreadOnlyHost(RecordingDialogHost):
    """记录每次回调发生在哪条线程，用来验证红线 3（worker 不许碰 QObject）。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.call_threads: List[int] = []

    def _note(self) -> None:
        self.call_threads.append(threading.get_ident())

    def present(self, kind: str, payload: Dict[str, Any], reply) -> None:
        self._note()
        super().present(kind, payload, reply)

    def show_progress(self, payload: Dict[str, Any]) -> None:
        self._note()
        super().show_progress(payload)

    def close_progress(self) -> None:
        self._note()
        super().close_progress()

    def notify(self, payload: Dict[str, Any]) -> None:
        self._note()
        super().notify(payload)

    def set_clipboard(self, text: str) -> bool:
        self._note()
        return super().set_clipboard(text)


# ─── 1. 未 start 时整体退化为 NullUIPort 行为 ───────────────


class TestNotStartedDegradation:
    OPTIONS = [Choice("yes", "完整版"), Choice("lite", "轻量版"), Choice("no", "取消")]

    def test_all_ten_methods_degrade_before_start(self, host, make_port):
        port = make_port(host, start=False, timeout_s=0.01)

        assert port.is_available() is False
        assert port.show_info("标题", "内容") is None
        assert port.show_warning("标题", "内容") is None
        assert port.show_error("标题", "内容") is None
        assert port.confirm("标题", "内容") is False
        assert port.confirm("标题", "内容", default=True) is True
        assert port.ask_text("标题", "提示") is None
        assert port.ask_text("标题", "提示", initial="x", password=True) is None
        assert port.choose("标题", "提示", self.OPTIONS) is None
        assert port.choose("标题", "提示", self.OPTIONS, default="lite") == "lite"
        assert port.show_progress(ProgressReport(current=1, total=2)) is None
        assert port.close_progress() is None
        assert port.close_progress("标题") is None
        assert port.set_clipboard("x") is False
        assert port.notify("消息") is None
        assert port.notify("消息", "error") is None

        # 未 start 时一次都不许碰宿主（QML 引擎可能还不存在）
        assert host.requests == []
        assert host.progress == []
        assert host.notifications == []
        assert host.clipboard_texts == []
        assert host.closed == 0

    def test_degrade_after_stop_is_identical(self, host, make_port):
        port = make_port(host, start=True)
        assert port.is_available() is True
        port.stop()

        assert port.is_available() is False
        assert port.confirm("标题", "内容", default=True) is True
        assert port.ask_text("标题", "提示") is None
        assert port.choose("标题", "提示", self.OPTIONS, default="no") == "no"
        assert port.set_clipboard("x") is False
        assert port.show_info("标题", "内容") is None
        assert port.notify("消息") is None


# ─── 2. is_available() 的四种取值 ───────────────────────────


class TestAvailability:
    def test_is_available_before_start_after_start_after_stop(self, host, make_port):
        port = make_port(host, start=False)
        assert port.is_available() is False

        port.start()
        assert port.is_available() is True

        port.stop()
        assert port.is_available() is False

    def test_is_available_without_host_is_false_and_start_is_refused(self, make_port):
        port = make_port(None, start=False)
        port.start()
        assert port.is_available() is False
        assert port.describe()["started"] is False
        assert port.describe()["host"] is None
        assert port.confirm("标题", "内容", default=True) is True

    def test_is_available_follows_host_availability(self, make_port):
        quiet = RecordingDialogHost(available=False)
        port = make_port(quiet, start=True)

        assert port.is_available() is False
        assert port.confirm("标题", "内容", default=True) is True  # 退化为兜底值
        assert quiet.requests == []  # 宿主说不可用，端口就不该把请求丢给它

        quiet.set_answer("confirm", False)
        quiet.set_available(True)
        pump(POLL_MS * 4)
        assert port.is_available() is True
        # 宿主改答 False：能拿到 False 才说明请求真的送到了宿主，而不是走了降级
        assert port.confirm("标题", "内容", default=True) is False
        assert len(quiet.requests) == 1

    def test_is_available_from_worker_thread_reads_cached_flag(self, host, make_port):
        port = make_port(host, start=True)
        assert run_in_worker(port.is_available) is True
        port.stop()
        assert run_in_worker(port.is_available) is False


# ─── 3. start() / stop() 的生命周期纪律 ─────────────────────


class TestLifecycle:
    def test_start_from_worker_thread_is_rejected(self, host, make_port):
        port = make_port(host, start=False)
        run_in_worker(port.start)  # 不许抛异常

        assert port.describe()["started"] is False
        assert port.is_available() is False
        assert port.confirm("标题", "内容", default=True) is True  # 退化为兜底值
        assert host.requests == []

        # 被拒绝之后，主线程仍然能正常启动
        port.start()
        assert port.is_available() is True

    def test_start_twice_is_idempotent_and_stop_is_final(self, host, make_port):
        port = make_port(host, start=False)
        port.start()
        port.start()
        assert port.describe()["started"] is True
        assert port.is_available() is True

        port.stop()
        port.start()  # 已停止：记 warning 后忽略
        assert port.is_available() is False
        assert port.describe()["stopped"] is True

    def test_stop_from_worker_thread_does_not_raise(self, host, make_port):
        port = make_port(host, start=True)
        run_in_worker(port.stop)
        assert port.is_available() is False

    def test_defaults_match_tk_port(self, host, make_port):
        # 契约 5.3：问答超时沿用 Tk 版的 DEFAULT_TIMEOUT_S
        port = make_port(host, timeout_s=DEFAULT_TIMEOUT_S, poll_interval_ms=DEFAULT_POLL_MS)
        info = port.describe()
        assert info["timeout_s"] == DEFAULT_TIMEOUT_S
        assert info["poll_interval_ms"] == DEFAULT_POLL_MS
        assert DEFAULT_TIMEOUT_S == 30.0  # 与 ui/ports_tk.py:TkUIPort 的默认值一致

    def test_describe_is_json_serializable(self, host, make_port):
        port = make_port(host, start=True)
        info = port.describe()
        assert info["class"] == "QtUIPort"
        assert info["available"] is True
        assert info["host"] == "RecordingDialogHost"
        json.dumps(info)  # 不抛异常即为合格
        assert "QtUIPort" in repr(port)

    def test_satisfies_ui_port_protocol(self, host, make_port):
        port = make_port(host)
        assert isinstance(port, UIPort)
        assert isinstance(host, DialogHost)
        assert isinstance(NullDialogHost(), DialogHost)


# ─── 4. 主线程调用能得到宿主给的答案 ─────────────────────────


class TestMainThreadAnswers:
    def test_confirm_ask_text_choose_return_host_answers(self, host, make_port):
        port = make_port(host, start=True)

        assert port.confirm("确认", "要继续吗") is True
        assert port.ask_text("输入", "请输入名称", initial="默认") == "输入的文本"
        options = [Choice("yes", "完整版"), Choice("lite", "轻量版")]
        assert port.choose("选择", "选一个", options, default="yes") == "lite"

        assert host.kinds() == ["confirm", "ask_text", "choose"]
        assert host.find("ask_text")[0]["payload"] == {
            "title": "输入",
            "prompt": "请输入名称",
            "initial": "默认",
            "password": False,
            "fallback": None,
        }
        choose_payload = host.find("choose")[0]["payload"]
        assert choose_payload["options"] == options  # Choice 对象原样交给宿主
        assert choose_payload["default"] == "yes"
        assert choose_payload["fallback"] is None  # 有默认按钮，但超时语义仍是 None

    def test_password_flag_reaches_host(self, host, make_port):
        port = make_port(host, start=True)
        assert port.ask_text("输入", "请输入密码", initial="abc", password=True) == "输入的文本"
        assert host.find("ask_text")[0]["payload"]["password"] is True

    def test_alert_payload_carries_level_and_message(self, host, make_port):
        port = make_port(host, start=True)
        port.show_info("信息", "内容一")
        port.show_warning("警告", "内容二")
        port.show_error("错误", "内容三")

        assert host.kinds() == ["info", "warning", "error"]
        assert [r["payload"]["level"] for r in host.requests] == ["info", "warning", "error"]
        assert [r["payload"]["message"] for r in host.requests] == ["内容一", "内容二", "内容三"]
        assert host.requests[0]["payload"]["blocking"] is False
        assert host.requests[0]["payload"]["fallback"] is None

    def test_confirm_default_is_fallback_not_button_order(self, host, make_port):
        host.set_answer("confirm", False)
        port = make_port(host, start=True)

        # 用户答了 False：兜底值（default=True）不生效
        assert port.confirm("确认", "要继续吗", default=True) is False
        # 但 default 仍然要传给宿主，供它决定默认按钮
        assert host.find("confirm")[0]["payload"]["default"] is True
        assert host.find("confirm")[0]["payload"]["fallback"] is True

    def test_answer_arriving_later_is_awaited_in_nested_loop(self, host, make_port):
        host.set_auto_reply(False)
        port = make_port(host, start=True)

        QTimer.singleShot(POLL_MS * 3, lambda: host.reply_latest("晚到的答案"))
        started = time.monotonic()
        value = port.ask_text("输入", "请输入")
        elapsed = time.monotonic() - started

        assert value == "晚到的答案"
        assert 0.0 <= elapsed < 2.0  # 嵌套事件循环期间 QTimer 照常工作

    def test_blocking_alert_on_main_thread_waits_for_reply(self, host, make_port):
        host.set_auto_reply(False)
        port = make_port(host, start=True)

        QTimer.singleShot(POLL_MS * 3, lambda: host.reply_latest(None))
        started = time.monotonic()
        port.show_warning("警告", "内容", blocking=True)
        elapsed = time.monotonic() - started

        assert host.kinds() == ["warning"]
        assert elapsed >= POLL_MS / 1000.0

    def test_non_blocking_alert_on_main_thread_returns_immediately(self, host, make_port):
        host.set_auto_reply(False)
        port = make_port(host, start=True)

        started = time.monotonic()
        port.show_info("信息", "内容")
        elapsed = time.monotonic() - started

        assert host.kinds() == ["info"]
        assert elapsed < 0.1  # QML 对话框异步：默认不阻塞主线程


# ─── 5. worker 线程调用（这个端口存在的理由） ───────────────


class TestWorkerThread:
    def test_worker_confirm_gets_answer_from_main_thread(self, host, make_port):
        host.set_auto_reply(False)
        port = make_port(host, start=True)
        QTimer.singleShot(60, lambda: host.reply_kind("confirm", True))

        value = run_in_worker(lambda: port.confirm("确认", "要继续吗", default=False))

        assert value is True
        assert len(host.find("confirm")) == 1

    def test_worker_ask_text_and_choose(self, host, make_port):
        port = make_port(host, start=True)
        assert run_in_worker(lambda: port.ask_text("输入", "请输入")) == "输入的文本"
        options = [Choice("yes", "完整版"), Choice("lite", "轻量版")]
        assert run_in_worker(lambda: port.choose("选择", "选一个", options, default="yes")) == "lite"

    def test_worker_fire_and_forget_returns_immediately(self, host, make_port):
        port = make_port(host, start=True)

        value, elapsed = timed_in_worker(lambda: port.notify("来自 worker", "success"))

        assert value is None
        assert elapsed < 0.5  # 即发即忘：worker 不等主线程
        pump(POLL_MS * 4)
        assert host.notifications[-1] == {"message": "来自 worker", "level": "success"}

    def test_worker_blocking_alert_waits_for_user(self, host, make_port):
        host.set_auto_reply(False)
        port = make_port(host, start=True)
        QTimer.singleShot(60, lambda: host.reply_kind("warning", None))

        value, elapsed = timed_in_worker(lambda: port.show_warning("警告", "内容", blocking=True))

        assert value is None
        assert elapsed >= 0.05  # 确实等到了用户关掉弹窗

    def test_worker_progress_reaches_host(self, host, make_port):
        port = make_port(host, start=True)
        report = ProgressReport(title="预下载", message="下载中", current=1, total=4)

        run_in_worker(lambda: port.show_progress(report, detail="1 MB / 4 MB"))
        pump(POLL_MS * 4)

        assert host.progress[-1]["report"] is report
        assert host.progress[-1]["detail"] == "1 MB / 4 MB"

    def test_worker_set_clipboard_returns_host_result(self, host, make_port):
        port = make_port(host, start=True)
        assert run_in_worker(lambda: port.set_clipboard("来自 worker")) is True
        assert host.clipboard_texts == ["来自 worker"]

    def test_host_is_only_touched_on_main_thread(self, make_port):
        """红线 3：worker 只能入队，宿主（QObject/QML）永远在主线程被调用。"""
        main_thread_id = threading.get_ident()
        host = _MainThreadOnlyHost(answers={"confirm": True})
        port = make_port(host, start=True)

        run_in_worker(lambda: port.confirm("确认", "内容"))
        run_in_worker(lambda: port.notify("通知", "info"))
        run_in_worker(lambda: port.set_clipboard("剪贴板"))
        run_in_worker(lambda: port.show_progress(ProgressReport(title="进度", message="下载中")))
        run_in_worker(lambda: port.close_progress("进度"))

        deadline = time.monotonic() + 2.0
        while len(host.call_threads) < 5 and time.monotonic() < deadline:
            pump(POLL_MS)
        assert len(host.call_threads) == 5
        assert set(host.call_threads) == {main_thread_id}


# ─── 6. 超时返回兜底值 ──────────────────────────────────────


class TestTimeouts:
    def test_worker_confirm_timeout_returns_default(self, host, make_port):
        host.set_auto_reply(False)
        port = make_port(host, start=True, timeout_s=SHORT_TIMEOUT_S)

        value, elapsed = timed_in_worker(lambda: port.confirm("确认", "内容", default=True))

        assert value is True
        assert elapsed < 1.5  # 0.2 秒超时，不该拖到 worker 预算
        assert len(host.find("confirm")) == 1  # 请求确实送达过，只是没人回答

    def test_worker_ask_text_timeout_returns_none(self, host, make_port):
        host.set_auto_reply(False)
        port = make_port(host, start=True, timeout_s=SHORT_TIMEOUT_S)

        value, elapsed = timed_in_worker(lambda: port.ask_text("输入", "请输入", initial="abc"))

        assert value is None
        assert elapsed < 1.5

    def test_worker_choose_timeout_returns_none_even_with_default(self, host, make_port):
        host.set_auto_reply(False)
        port = make_port(host, start=True, timeout_s=SHORT_TIMEOUT_S)

        value, elapsed = timed_in_worker(lambda: port.choose("选择", "选一个", [Choice("a", "A")], default="a"))

        # 端口可用但没人回答 -> 按"用户没回答"处理（与 Tk 版一致）；
        # 只有端口整体不可用时才返回 default
        assert value is None
        assert elapsed < 1.5

    def test_main_thread_timeout_returns_fallback(self, host, make_port):
        host.set_auto_reply(False)
        port = make_port(host, start=True, timeout_s=SHORT_TIMEOUT_S)

        started = time.monotonic()
        assert port.confirm("确认", "内容", default=True) is True
        elapsed = time.monotonic() - started

        assert elapsed >= SHORT_TIMEOUT_S - 0.05  # 确实等满了一个超时周期
        assert elapsed < 2.0

    def test_timeout_leaves_dialog_open(self, host, make_port):
        host.set_auto_reply(False)
        port = make_port(host, start=True, timeout_s=SHORT_TIMEOUT_S)

        port.confirm("确认", "内容")

        # 取舍（与 Tk 版一致）：端口没有"撤销已显示对话框"的能力，弹窗留着
        assert len(host.pending()) == 1

    def test_per_call_timeout_override(self, host, make_port):
        host.set_auto_reply(False)
        port = make_port(host, start=True, timeout_s=5.0)  # 实例默认值很长

        value, elapsed = timed_in_worker(lambda: port.ask_text("输入", "提示", timeout_s=0.15))

        assert value is None
        assert elapsed < 1.0  # 调用级 timeout_s 覆盖了实例默认值

    def test_double_reply_keeps_first_answer(self, host, make_port):
        """QML 宿主可能既触发按钮又触发关闭：重复作答不许改掉第一次的答案。"""
        host.set_auto_reply(False)
        port = make_port(host, start=True)

        def _answer_twice() -> None:
            record = host.find("ask_text")[0]
            record["reply"]("第一次")
            record["reply"]("第二次")

        QTimer.singleShot(20, _answer_twice)
        assert port.ask_text("输入", "提示") == "第一次"


# ─── 7. 进度 / 通知 / 剪贴板的负载原样到达宿主 ──────────────


class TestProgressNotifyClipboard:
    def test_progress_payload_reaches_host(self, host, make_port):
        port = make_port(host, start=True)
        report = ProgressReport(title="预下载", message="正在下载", current=3, total=10)

        port.show_progress(report, heading="标题", detail="3 MB / 10 MB", cancel_label="取消", 未登记参数=1)

        assert len(host.progress) == 1
        payload = host.progress[0]
        assert payload["report"] is report  # 原对象，端口不加工
        assert payload["title"] == "预下载"
        assert payload["heading"] == "标题"
        assert payload["detail"] == "3 MB / 10 MB"
        assert payload["cancel_label"] == "取消"
        assert payload["on_cancel"] is None
        assert "未登记参数" not in payload  # 未登记的关键字被过滤（与 Tk 版一致）
        assert report.determinate is True
        assert report.fraction == 0.3

    def test_progress_reuses_title_when_report_title_is_none(self, host, make_port):
        port = make_port(host, start=True)
        port.show_progress(ProgressReport(title="预下载", message="下载中", current=1, total=4))
        port.show_progress(ProgressReport(message="合并中", current=1, total=1))

        assert [p["title"] for p in host.progress] == ["预下载", "预下载"]
        assert host.progress[1]["report"].title is None

    def test_close_progress_only_closes_matching_title(self, host, make_port):
        port = make_port(host, start=True)
        port.show_progress(ProgressReport(title="预下载", message="下载中"))

        port.close_progress("别的标题")
        assert host.closed == 0

        port.close_progress("预下载")
        assert host.closed == 1

        port.close_progress(None)  # 会话已经结束，再关无意义
        assert host.closed == 1

    def test_close_progress_without_session_is_ignored(self, host, make_port):
        port = make_port(host, start=True)
        port.close_progress("从没开过的进度")
        assert host.closed == 0
        assert port.describe()["progress_title"] is None

    def test_modal_progress_is_released_by_close_progress(self, host, make_port):
        """worker 的 close_progress 必须能在嵌套事件循环期间被排空，否则主线程死等。"""
        port = make_port(host, start=True)
        finished = threading.Event()
        watchdog = threading.Timer(3.0, finished.set)

        def _closer() -> None:
            time.sleep(0.1)
            port.close_progress("预下载")

        watchdog.start()
        threading.Thread(target=_closer, daemon=True).start()
        started = time.monotonic()
        port.show_progress(
            ProgressReport(title="预下载", message="下载中", current=1, total=4), modal=True, wait_event=finished
        )
        elapsed = time.monotonic() - started
        watchdog.cancel()

        assert elapsed < 2.0  # 靠 close_progress 放行，而不是靠 wait_event 兜底
        assert len(host.progress) == 1
        assert host.closed == 1
        assert port.describe()["modal_waiting"] is False

    def test_set_clipboard_returns_host_result(self, make_port):
        ok_host = RecordingDialogHost(clipboard=True)
        port = make_port(ok_host, start=True)
        assert port.set_clipboard("FMCL-剪贴板测试") is True
        assert ok_host.clipboard_texts == ["FMCL-剪贴板测试"]
        port.stop()

        bad_host = RecordingDialogHost(clipboard=False)
        port2 = make_port(bad_host, start=True)
        assert port2.set_clipboard("x") is False  # 不假装成功
        assert bad_host.clipboard_texts == ["x"]

    def test_notify_payload_and_level_normalization(self, host, make_port):
        port = make_port(host, start=True)
        port.notify("下载完成", "success")
        port.notify("没听说过的级别", "purple")

        assert host.notifications == [
            {"message": "下载完成", "level": "success"},
            {"message": "没听说过的级别", "level": "info"},
        ]


# ─── 8. stop() 放行所有等待方 ───────────────────────────────


class TestStopReleases:
    def test_stop_releases_queued_worker(self, host, make_port):
        """轮询周期极大 -> 请求只能躺在队列里，这时 stop() 必须立刻放行它。"""
        host.set_auto_reply(False)
        port = make_port(host, start=True, timeout_s=5.0, poll_interval_ms=10_000)

        def _stopper() -> None:
            time.sleep(0.1)
            port.stop()

        threading.Thread(target=_stopper, daemon=True).start()
        value, elapsed = timed_in_worker(lambda: port.confirm("确认", "内容", default=True))

        assert value is True
        assert elapsed < 2.0  # 没有干等到 5 秒超时

    def test_stop_releases_inflight_worker(self, host, make_port):
        """请求已经交给宿主、用户还没点：stop() 也要放行（界面销毁时的收尾）。"""
        host.set_auto_reply(False)
        port = make_port(host, start=True, timeout_s=5.0)

        def _stopper() -> None:
            time.sleep(0.15)  # 留出时间让轮询把请求投给宿主
            port.stop()

        threading.Thread(target=_stopper, daemon=True).start()
        value, elapsed = timed_in_worker(lambda: port.confirm("确认", "内容", default=True))

        assert value is True
        assert elapsed < 2.0
        assert len(host.find("confirm")) == 1  # 确实投出去过

    def test_stop_releases_main_thread_wait(self, host, make_port):
        """主线程自己等在嵌套循环里时，stop() 也要能放行它（否则退出会挂住）。"""
        host.set_auto_reply(False)
        port = make_port(host, start=True, timeout_s=5.0)
        QTimer.singleShot(30, port.stop)

        started = time.monotonic()
        assert port.confirm("确认", "内容", default=True) is True
        assert time.monotonic() - started < 2.0


# ─── 9. NullDialogHost（无界面场景） ────────────────────────


class TestNullDialogHost:
    def test_present_answers_with_fallback_immediately(self):
        dialog_host = NullDialogHost()
        seen: List[Any] = []

        dialog_host.present("confirm", {"title": "t", "message": "m", "fallback": True}, seen.append)
        dialog_host.present("ask_text", {"title": "t", "prompt": "p", "fallback": None}, seen.append)
        dialog_host.present("info", {"title": "t", "message": "m", "fallback": None}, seen.append)

        assert seen == [True, None, None]

    def test_no_ui_and_clipboard_refused(self):
        dialog_host = NullDialogHost()
        assert dialog_host.is_available() is False
        assert dialog_host.set_clipboard("x") is False
        dialog_host.show_progress({"title": "t", "report": ProgressReport(current=1, total=2)})
        dialog_host.close_progress()
        dialog_host.notify({"message": "m", "level": "info"})

    def test_port_with_null_host_behaves_like_null_port(self, make_port):
        port = make_port(NullDialogHost(), start=True)

        assert port.is_available() is False  # 没有界面，服务层应当走静默降级
        assert port.confirm("t", "m", default=True) is True
        assert port.choose("t", "m", [Choice("a", "A")], default="a") == "a"
        assert port.ask_text("t", "m") is None
        assert port.set_clipboard("x") is False
        assert port.notify("m") is None


# ─── 10. RecordingDialogHost 的脚本能力（测试替身自身） ─────


class TestRecordingDialogHost:
    def test_no_reply_script_and_auto_reply_switch(self):
        dialog_host = RecordingDialogHost(answers={"confirm": NO_REPLY})
        seen: List[Any] = []

        dialog_host.present("confirm", {"fallback": False}, seen.append)
        assert seen == []  # 故意不回答
        assert len(dialog_host.pending()) == 1
        assert dialog_host.reply_latest(True) is True
        assert seen == [True]
        assert dialog_host.reply_latest(True) is False  # 已经没有待答请求

        dialog_host.set_auto_reply(False)
        dialog_host.present("info", {"fallback": None}, seen.append)
        assert len(dialog_host.pending()) == 1

    def test_callable_script_sees_payload(self):
        dialog_host = RecordingDialogHost(answers={"choose": lambda payload: payload["options"][0].value})
        seen: List[Any] = []

        dialog_host.present("choose", {"options": [Choice("b", "B")], "fallback": None}, seen.append)

        assert seen == ["b"]

    def test_unknown_kind_falls_back_to_payload_fallback(self):
        """宿主不认识新 kind 时也能收尾：兜底值就在 payload 里（端口算好的）。"""
        dialog_host = RecordingDialogHost()
        seen: List[Any] = []

        dialog_host.present("未来的新种类", {"fallback": "兜底"}, seen.append)

        assert seen == ["兜底"]

    def test_records_clear_and_find(self):
        dialog_host = RecordingDialogHost(answers={"confirm": True})
        dialog_host.present("confirm", {"fallback": False}, lambda _value: None)

        assert dialog_host.kinds() == ["confirm"]
        assert len(dialog_host.find("confirm")) == 1
        assert "RecordingDialogHost" in repr(dialog_host)

        dialog_host.clear()
        assert dialog_host.kinds() == []
        assert dialog_host.progress == []
        assert dialog_host.closed == 0
