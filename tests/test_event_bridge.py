"""事件桥测试（阶段 2.5）。

约定：

- ``QT_QPA_PLATFORM=offscreen`` 必须在 **import PySide6 之前**设置（本文件顶部）；
- 不引第三方测试依赖（仓库没装 ``pytest-qt``）：自己建 ``QGuiApplication``，用
  ``processEvents()`` 驱动主线程事件循环 —— 这正是"Queued 投递需要接收者线程有
  事件循环"这一前提的最直接验证；
- 只断言**主线程收到了**（契约红线 3 的要求），不断言"信号在 worker 线程上被同步
  调用"这类实现细节。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging
import threading
import time
from typing import Any, Callable, Iterator, List, Tuple

import pytest
from PySide6.QtCore import QObject, QThread, Slot
from PySide6.QtGui import QGuiApplication

from app.bridges.event_bridge import (
    EVENT_SIGNAL_MAP,
    WATCHED_BY_DEFAULT,
    EventBridge,
    resolve_signal_name,
)
from app.events import EventBus

NAMED_SIGNALS: Tuple[str, ...] = (
    "taskStarted",
    "taskFinished",
    "taskFailed",
    "toastRequested",
    "statusChanged",
    "achievementUnlocked",
)


def _qapp() -> QGuiApplication:
    """取/建进程内唯一的 QGuiApplication（多个测试模块共用一个进程时复用）。"""
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


_APP = _qapp()
_MAIN_IDENT = threading.get_ident()


def _pump(condition: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    """驱动主线程事件循环直到条件成立；返回条件是否成立。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _APP.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    _APP.processEvents()
    return condition()


class _Recorder(QObject):
    """主线程接收者：每条调用都记下"收到它的线程是不是主线程"。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: List[Tuple[str, bool, Any]] = []
        self.qt_main_thread = QThread.currentThread()

    def take(self, name: str, payload: Any) -> None:
        self.calls.append((name, threading.get_ident() == _MAIN_IDENT, payload))

    def names(self) -> List[str]:
        return [c[0] for c in self.calls]

    @Slot(dict)
    def on_task_started(self, payload: dict) -> None:
        self.take("taskStarted", payload)

    @Slot(dict)
    def on_task_finished(self, payload: dict) -> None:
        self.take("taskFinished", payload)

    @Slot(dict)
    def on_task_failed(self, payload: dict) -> None:
        self.take("taskFailed", payload)

    @Slot(dict)
    def on_toast_requested(self, payload: dict) -> None:
        self.take("toastRequested", payload)

    @Slot(dict)
    def on_status_changed(self, payload: dict) -> None:
        self.take("statusChanged", payload)

    @Slot(dict)
    def on_achievement_unlocked(self, payload: dict) -> None:
        self.take("achievementUnlocked", payload)

    @Slot(str, dict)
    def on_event(self, name: str, payload: dict) -> None:
        self.take(f"event:{name}", payload)


def _wire(bridge: EventBridge, recorder: _Recorder) -> None:
    """把 8 个信号全部接到 recorder 上。"""
    bridge.taskStarted.connect(recorder.on_task_started)
    bridge.taskFinished.connect(recorder.on_task_finished)
    bridge.taskFailed.connect(recorder.on_task_failed)
    bridge.toastRequested.connect(recorder.on_toast_requested)
    bridge.statusChanged.connect(recorder.on_status_changed)
    bridge.achievementUnlocked.connect(recorder.on_achievement_unlocked)
    bridge.event.connect(recorder.on_event)


@pytest.fixture()
def bus() -> EventBus:
    return EventBus()


@pytest.fixture()
def bridge(bus: EventBus) -> Iterator[EventBridge]:
    obj = EventBridge(bus)
    yield obj
    obj.detach()


# ─── 映射表本身 ─────────────────────────────────────────────


def test_六个具名信号都真的存在且与契约同名():
    for name in NAMED_SIGNALS:
        assert hasattr(EventBridge, name), f"契约要求的信号 {name} 不存在"
    assert sorted(EVENT_SIGNAL_MAP.values()) == sorted(NAMED_SIGNALS)


def test_两种键写法都解析到同一个具名信号():
    assert resolve_signal_name("task.started") == "taskStarted"
    assert resolve_signal_name("taskStarted") == "taskStarted"
    assert resolve_signal_name("plugin.enabled") is None
    # 别名也必须在默认订阅表里：EventBus 按 key 订阅，不订阅就到不了桥
    assert set(EVENT_SIGNAL_MAP) <= set(WATCHED_BY_DEFAULT)
    assert set(EVENT_SIGNAL_MAP.values()) <= set(WATCHED_BY_DEFAULT)


# ─── 具名派发 ───────────────────────────────────────────────


def test_具名事件派发到对应信号并带上负载(bus: EventBus, bridge: EventBridge):
    rec = _Recorder()
    _wire(bridge, rec)
    for index, key in enumerate(EVENT_SIGNAL_MAP):
        assert bus.publish(key, seq=index) == 1
    assert rec.names() == list(EVENT_SIGNAL_MAP.values())
    assert [call[2] for call in rec.calls] == [{"seq": i} for i in range(len(EVENT_SIGNAL_MAP))]
    assert all(call[1] is True for call in rec.calls)


def test_驼峰别名也能命中具名信号(bus: EventBus, bridge: EventBridge):
    rec = _Recorder()
    _wire(bridge, rec)
    bus.publish("taskFinished", task_id=9)
    assert rec.names() == ["taskFinished"]
    assert rec.calls[0][2] == {"task_id": 9}


def test_未具名事件走通用信号(bus: EventBus, bridge: EventBridge):
    rec = _Recorder()
    _wire(bridge, rec)
    assert bridge.watch("plugin.enabled") == 1
    assert bridge.watch("plugin.enabled") == 0  # 幂等
    bus.publish("plugin.enabled", plugin_id="com.demo", version="1.0")
    assert rec.names() == ["event:plugin.enabled"]
    assert rec.calls[0][2] == {"plugin_id": "com.demo", "version": "1.0"}


def test_构造时传的_extra_events_也走通用信号(bus: EventBus):
    rec = _Recorder()
    obj = EventBridge(bus, extra_events=["plugin.enabled"])
    try:
        _wire(obj, rec)
        bus.publish("plugin.enabled", plugin_id="x")
        assert rec.names() == ["event:plugin.enabled"]
    finally:
        obj.detach()


def test_没订阅过的事件根本到不了桥(bus: EventBus, bridge: EventBridge):
    """EventBus 按 key 订阅、没有通配 —— 这是它的冻结语义，不是桥的疏忽。"""
    rec = _Recorder()
    _wire(bridge, rec)
    assert bus.publish("nobody.listens", a=1) == 0
    assert rec.calls == []
    assert bridge.watchedEvents() == sorted(WATCHED_BY_DEFAULT)


# ─── history ────────────────────────────────────────────────


def test_history_返回最近事件且字段名是_QML_能读的(bus: EventBus, bridge: EventBridge):
    for i in range(5):
        bus.publish("status.changed", text=f"第{i}步")
    records = bridge.history(2)
    assert [r["event"] for r in records] == ["status.changed", "status.changed"]
    assert records[0]["payload"] == {"text": "第3步"}
    assert set(records[0]) == {"event", "payload", "handlerCount"}
    assert records[0]["handlerCount"] == 1  # 桥自己订阅了 status.changed
    assert len(bridge.history()) == 5  # 默认 limit=20


def test_history_的负载是副本不会跟着总线变(bus: EventBus, bridge: EventBridge):
    bus.publish("task.failed", error="boom")
    snapshot = bridge.history(1)[0]["payload"]
    snapshot["error"] = "改过了"
    assert bus.history(1)[0].payload["error"] == "boom"


def test_history_把_limit_原样交给_EventBus_不额外纠正(bus: EventBus, bridge: EventBridge):
    """行为钉住：``EventBus.history(0)`` 的切片是 ``[-0:]``，返回**全部**历史。

    这是既有接口的既有行为（负数 limit 会丢掉前 |n| 条），桥**不**在中间加一层
    "聪明"的纠正 —— 纠正会让同一个参数在两处含义不同，更难查。
    """
    for i in range(3):
        bus.publish("task.started", seq=i)
    assert len(bridge.history(0)) == 3
    assert len(bus.history(0)) == 3


# ─── 退订 ───────────────────────────────────────────────────


def test_退订之后不再收到该事件(bus: EventBus, bridge: EventBridge):
    rec = _Recorder()
    _wire(bridge, rec)
    bus.publish("status.changed", text="a")
    assert rec.names() == ["statusChanged"]
    assert bridge.unwatch("status.changed") is True
    assert bridge.unwatch("status.changed") is False  # 第二次是空操作
    assert bus.publish("status.changed", text="b") == 0
    assert rec.names() == ["statusChanged"]


def test_detach_之后所有事件都不再到达(bus: EventBus, bridge: EventBridge):
    rec = _Recorder()
    _wire(bridge, rec)
    assert bridge.detach() == len(WATCHED_BY_DEFAULT)
    assert bridge.detach() == 0  # 幂等
    assert bridge.watch("late.event") == 0  # detach 之后不再接受登记
    for key in EVENT_SIGNAL_MAP:
        assert bus.publish(key, a=1) == 0
    assert rec.calls == []


# ─── 跨线程（本任务的核心断言） ─────────────────────────────


def test_worker线程publish时主线程确实收到(bus: EventBus, bridge: EventBridge):
    rec = _Recorder()
    _wire(bridge, rec)
    worker_ident: List[int] = []
    worker_is_qt_main: List[bool] = []

    def worker() -> None:
        worker_ident.append(threading.get_ident())
        worker_is_qt_main.append(QThread.currentThread() is rec.qt_main_thread)
        bus.publish("task.started", task_id=42, kind="download")
        bus.publish("plugin.enabled", plugin_id="com.demo")
        bus.publish("task.failed", error="boom")

    thread = threading.Thread(target=worker, name="fake-service-worker")
    thread.start()
    thread.join()

    assert worker_ident and worker_ident[0] != _MAIN_IDENT, "worker 必须真的在另一个线程里 publish"
    assert worker_is_qt_main == [False], "worker 线程不该是 Qt 主线程"
    assert _pump(lambda: len(rec.calls) >= 2), f"主线程没收到事件: {rec.calls}"
    # 具名事件与通用事件都到达；未 watch 的 plugin.enabled 不算（见另一个测试）
    assert rec.names() == ["taskStarted", "taskFailed"]
    for name, is_main, _payload in rec.calls:
        assert is_main is True, f"{name} 的槽跑在了非主线程"
    assert rec.calls[0][2] == {"task_id": 42, "kind": "download"}


def test_worker线程对未具名事件也要走通用信号(bus: EventBus, bridge: EventBridge):
    rec = _Recorder()
    _wire(bridge, rec)
    bridge.watch("plugin.enabled")

    def worker() -> None:
        bus.publish("plugin.enabled", plugin_id="com.demo")

    thread = threading.Thread(target=worker, name="fake-service-worker-2")
    thread.start()
    thread.join()
    assert _pump(lambda: len(rec.calls) >= 1), f"主线程没收到通用事件: {rec.calls}"
    assert rec.names() == ["event:plugin.enabled"]
    assert rec.calls[0][1] is True


# ─── 销毁 ───────────────────────────────────────────────────


def test_桥被销毁后_publish_不崩(bus: EventBus, caplog: pytest.LogCaptureFixture):
    """生命周期是显式的：桥死了以后总线上可能还留着它的 handler（空转），但绝不能崩。"""
    import shiboken6

    obj = EventBridge(bus)
    assert bus.handler_count("task.started") == 1
    handlers = list(obj._handlers.values())  # 白盒：拿到"总线上挂着的那几个 handler"
    shiboken6.delete(obj)
    with caplog.at_level(logging.DEBUG, logger="app.bridges.event_bridge"):
        # 1) 正常路径：publish 不会因为订阅者已死而抛异常
        bus.publish("task.started", task_id=1)
        # 2) 兜底路径：直接调那个漏摘的 handler 也不许抛
        for handler in handlers:
            handler(a=1)
    assert "ERROR" not in caplog.text
    assert "已丢弃" in caplog.text


def test_detach_会把订阅从总线上摘干净(bus: EventBus):
    obj = EventBridge(bus)
    assert bus.handler_count("task.started") == 1
    assert obj.detach() == len(WATCHED_BY_DEFAULT)
    assert bus.handler_count("task.started") == 0
    obj.detach()  # 幂等，不抛


def test_桥建在非主线程时会留下明确错误日志(caplog: pytest.LogCaptureFixture):
    """跨线程投递的前提是"桥属于主线程"；不满足时必须留下明确的错误日志。"""
    import shiboken6

    caplog.set_level(logging.ERROR, logger="app.bridges.event_bridge")
    done: List[str] = []

    def build() -> None:
        obj = EventBridge(EventBus())
        shiboken6.delete(obj)
        done.append("ok")

    thread = threading.Thread(target=build, name="wrong-thread-builder")
    thread.start()
    thread.join()
    assert done == ["ok"]
    assert "非主线程" in caplog.text
    assert "wrong-thread-builder" in caplog.text


def test_没有_qapp_时构造只报错不抛异常(caplog: pytest.LogCaptureFixture, monkeypatch):
    """真无 QCoreApplication 的环境（纯服务单测进程）不该因为建桥而崩。"""
    from app.bridges import event_bridge as module

    class _NoApp:
        @staticmethod
        def instance() -> None:
            return None

    caplog.set_level(logging.ERROR, logger="app.bridges.event_bridge")
    monkeypatch.setattr(module, "QCoreApplication", _NoApp)
    obj = EventBridge(EventBus())
    try:
        assert "没有 QCoreApplication" in caplog.text
    finally:
        obj.detach()


# ─── QML 侧可用性（Python 侧测试查不出"漏了 @Slot"这类问题） ──

#: 只暴露标量属性：QML 侧拿到的东西不许再靠 JSON 字符串比对。
_QML_PROBE = """
import QtQml
QtObject {
    property int histLen: -1
    property string histEvent: "unset"
    property string histText: "unset"
    property int histHandlerCount: -1
    property bool regOk: false
    property bool regWithDescOk: false
    property bool dupRejected: false
    property int keyCount: -1
    property string firstCombo: "unset"
    property string firstDesc: "unset"
    property bool backendAvailable: true
    Component.onCompleted: {
        var records = Events.history(2)
        histLen = records.length
        if (records.length > 0) {
            histEvent = records[0].event
            histText = records[0].payload.text
            histHandlerCount = records[0].handlerCount
        }
        regOk = Hotkeys.register("qml_next", "ctrl+shift+right")
        regWithDescOk = Hotkeys.register("qml_up", "ctrl+shift+up", "音量加")
        dupRejected = !Hotkeys.register("qml_next", "ctrl+x")
        keyCount = Hotkeys.activeKeys.length
        var details = Hotkeys.activeHotkeys()
        firstCombo = details[0].combo
        firstDesc = details[1].description
        backendAvailable = Hotkeys.backendAvailable
    }
}
"""

_QML_CONNECTIONS = """
import QtQml
Connections {
    id: conn
    property int hits: 0
    property string payload: ""
    target: Events
    function onStatusChanged(p) { conn.hits += 1; conn.payload = p.text }
}
"""

_QML_ENGINE: Any = None


def _qml_engine() -> Any:
    """惰性建一个常驻 QQmlEngine（反复建/销毁引擎在解释器退出期更容易出噪音）。"""
    global _QML_ENGINE
    if _QML_ENGINE is None:
        from PySide6.QtQml import QQmlEngine

        _QML_ENGINE = QQmlEngine()
    return _QML_ENGINE


def _qml_object(source: str) -> Tuple[Any, Any]:
    from PySide6.QtCore import QByteArray, QUrl
    from PySide6.QtQml import QQmlComponent

    component = QQmlComponent(_qml_engine())
    component.setData(QByteArray(source.encode("utf-8")), QUrl("test_bridges.qml"))
    obj = component.create()
    assert obj is not None, [e.toString() for e in component.errors()]
    return obj, component  # 组件要跟着对象一起活到测试结束


def test_QML_侧真的能调用这两个桥(bus: EventBus, bridge: EventBridge):
    from app.bridges.hotkey_bridge import HotkeyBridge, NullHotkeyBackend

    hotkeys = HotkeyBridge(NullHotkeyBackend())
    engine = _qml_engine()
    engine.rootContext().setContextProperty("Events", bridge)
    engine.rootContext().setContextProperty("Hotkeys", hotkeys)
    bus.publish("status.changed", text="准备中")

    obj, _component = _qml_object(_QML_PROBE)
    assert obj.property("histLen") == 1
    assert obj.property("histEvent") == "status.changed"
    assert obj.property("histText") == "准备中"
    assert obj.property("histHandlerCount") == 1
    assert obj.property("regOk") is True
    assert obj.property("regWithDescOk") is True
    assert obj.property("dupRejected") is True
    assert obj.property("keyCount") == 2
    assert obj.property("firstCombo") == "ctrl+shift+right"
    assert obj.property("firstDesc") == "音量加"
    assert obj.property("backendAvailable") is False
    assert hotkeys.activeKeys == ["qml_next", "qml_up"]


def test_QML_的_Connections_也能收到_worker_线程发的事件(bus: EventBus, bridge: EventBridge):
    engine = _qml_engine()
    engine.rootContext().setContextProperty("Events", bridge)
    conn, _component = _qml_object(_QML_CONNECTIONS)

    def worker() -> None:
        bus.publish("status.changed", text="来自 worker")

    thread = threading.Thread(target=worker, name="qml-worker")
    thread.start()
    thread.join()
    assert _pump(lambda: conn.property("hits") == 1), "QML 处理器没收到 worker 线程的事件"
    assert conn.property("payload") == "来自 worker"
