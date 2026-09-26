"""热键桥测试（阶段 2.6）。

三条纪律：

- ``QT_QPA_PLATFORM=offscreen`` 在 **import PySide6 之前**设置；
- **一个系统级热键都不注册**：真后端只用到 ``available``（那是纯 ``import keyboard``，
  实测 ``keyboard`` 的钩子只在 ``listen`` / ``add_hotkey`` 路径上安装），注册/触发
  全部走注入的替身后端；
- 不引第三方测试依赖（没有 ``pytest-qt``）：自己建 ``QGuiApplication`` 并用
  ``processEvents()`` 驱动主线程事件循环，模拟"热键库线程回调 → 主线程收到"。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import builtins
import logging
import threading
import time
from typing import Any, Callable, Iterator, List, Tuple

import pytest
from PySide6.QtCore import QObject, QThread, Slot
from PySide6.QtGui import QGuiApplication

from app.bridges.hotkey_bridge import (
    LEGACY_HOTKEYS,
    HotkeyBackendUnavailable,
    HotkeyBridge,
    KeyboardHotkeyBackend,
    NullHotkeyBackend,
)


def _qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


_APP = _qapp()
_MAIN_IDENT = threading.get_ident()


def _pump(condition: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        _APP.processEvents()
        if condition():
            return True
        time.sleep(0.005)
    _APP.processEvents()
    return condition()


class _FakeBackend:
    """记录型替身后端：把回调存起来，由测试自己在指定线程上触发。"""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.added: List[Tuple[str, Callable[[], None]]] = []
        self.removed: List[Any] = []
        self.released = 0
        self._seq = 0

    def add_hotkey(self, combo: str, callback: Callable[[], None]) -> Any:
        self._seq += 1
        handle = f"h{self._seq}"
        self.added.append((combo, callback))
        return handle

    def remove_hotkey(self, handle: Any) -> None:
        self.removed.append(handle)

    def release_warmup(self) -> None:
        self.released += 1

    def fire(self, combo: str) -> None:
        """触发某个组合键的回调（**已注销的不算**：注销只记录，不清 added）。"""
        for registered, callback in self.added:
            if registered == combo:
                callback()


class _RaisingBackend:
    """注册必失败的后端（模拟无权限 / 无桌面）。"""

    available = True

    def add_hotkey(self, combo: str, callback: Callable[[], None]) -> Any:
        raise OSError("模拟注册被系统拒绝")

    def remove_hotkey(self, handle: Any) -> None:
        raise OSError("模拟注销被系统拒绝")


class _TriggerRecorder(QObject):
    """主线程接收者：记下 ``triggered`` 的名字与收到它的线程。"""

    def __init__(self) -> None:
        super().__init__()
        self.calls: List[Tuple[str, bool]] = []
        self.qt_main_thread = QThread.currentThread()

    @Slot(str)
    def on_triggered(self, name: str) -> None:
        self.calls.append((name, threading.get_ident() == _MAIN_IDENT))


@pytest.fixture()
def fake() -> _FakeBackend:
    return _FakeBackend()


@pytest.fixture()
def bridge(fake: _FakeBackend) -> Iterator[HotkeyBridge]:
    obj = HotkeyBridge(fake)
    yield obj
    obj.unregister_all()


# ─── 注册 / 注销链路 ────────────────────────────────────────


def test_注册把组合键与回调交给后端并记进_activeKeys(fake: _FakeBackend, bridge: HotkeyBridge):
    assert bridge.activeKeys == []
    assert bridge.register("music_next", "ctrl+shift+right", "下一首") is True
    assert len(fake.added) == 1
    assert fake.added[0][0] == "ctrl+shift+right"
    assert bridge.activeKeys == ["music_next"]
    assert bridge.activeHotkeys() == [
        {"name": "music_next", "combo": "ctrl+shift+right", "description": "下一首"}
    ]


def test_activeKeys_按注册顺序且_变更时会发信号(fake: _FakeBackend, bridge: HotkeyBridge):
    changes: List[int] = []
    bridge.activeKeysChanged.connect(lambda: changes.append(len(changes)))
    bridge.register("b", "ctrl+b")
    bridge.register("a", "ctrl+a")
    assert bridge.activeKeys == ["b", "a"]
    bridge.unregister("b")
    assert bridge.activeKeys == ["a"]
    assert len(changes) == 3
    bridge.unregister_all()
    assert len(changes) == 4
    assert bridge.activeKeys == []


def test_重复注册同名被拒绝且不动后端(fake: _FakeBackend, bridge: HotkeyBridge):
    failures: List[Tuple[str, str]] = []
    bridge.registrationFailed.connect(lambda name, reason: failures.append((name, reason)))
    assert bridge.register("next", "ctrl+shift+right") is True
    assert bridge.register("next", "ctrl+alt+n") is False
    assert len(fake.added) == 1, "第二次注册不该再调后端"
    assert bridge.activeHotkeys()[0]["combo"] == "ctrl+shift+right", "已注册的组合键不该被覆盖"
    assert failures and failures[0][0] == "next"
    assert "已注册" in failures[0][1]


def test_组合键撞车只警告不拦截(fake: _FakeBackend, bridge: HotkeyBridge, caplog: pytest.LogCaptureFixture):
    """现有实现里监控开关与静音切换就是同一个 ctrl+shift+m，桥不替产品做决定。"""
    with caplog.at_level(logging.WARNING, logger="app.bridges.hotkey_bridge"):
        assert bridge.register("monitor_toggle", "ctrl+shift+m") is True
        assert bridge.register("vol_mute", "ctrl+shift+m") is True
    assert len(fake.added) == 2
    assert "占用" in caplog.text


def test_空名字或空组合键被拒绝(fake: _FakeBackend, bridge: HotkeyBridge):
    failures: List[Tuple[str, str]] = []
    bridge.registrationFailed.connect(lambda name, reason: failures.append((name, reason)))
    assert bridge.register("", "ctrl+a") is False
    assert bridge.register("no_combo", "   ") is False
    assert fake.added == []
    assert len(failures) == 2


def test_unregister_摘掉后端句柄并更新_activeKeys(fake: _FakeBackend, bridge: HotkeyBridge):
    bridge.register("play_pause", "ctrl+shift+space", "播放/暂停")
    assert bridge.unregister("play_pause") is True
    assert fake.removed == ["h1"]
    assert bridge.activeKeys == []
    assert bridge.unregister("play_pause") is False  # 已注销：空操作


def test_unregister_all_注销全部并拆预热钩子(fake: _FakeBackend, bridge: HotkeyBridge):
    for name, combo in (("a", "ctrl+a"), ("b", "ctrl+b"), ("c", "ctrl+c")):
        bridge.register(name, combo)
    assert bridge.unregister_all() == 3
    assert fake.removed == ["h1", "h2", "h3"]
    assert fake.released == 1
    assert bridge.activeKeys == []
    assert bridge.unregister_all() == 0
    assert fake.released == 1  # 没有热键时不该再拆一次


# ─── 没有可选方法的后端 ─────────────────────────────────────


def test_没有_release_warmup_的后端也能注销(bridge: HotkeyBridge):
    """协议里只有三个必需成员，``release_warmup`` 是可选扩展。"""
    backend = _RaisingBackend()
    obj = HotkeyBridge(backend)
    assert obj.register("a", "ctrl+a") is False  # 该后端注册必失败
    assert obj.unregister_all() == 0


# ─── 触发链路（跨线程） ─────────────────────────────────────


def test_后端回调经信号回到主线程(fake: _FakeBackend, bridge: HotkeyBridge):
    rec = _TriggerRecorder()
    bridge.triggered.connect(rec.on_triggered)
    bridge.register("vol_up", "ctrl+shift+up", "音量 +5")
    fire_ident: List[int] = []

    def library_thread() -> None:
        fire_ident.append(threading.get_ident())
        fake.fire("ctrl+shift+up")  # 模拟 keyboard 库在线程里回调

    thread = threading.Thread(target=library_thread, name="fake-keyboard-thread")
    thread.start()
    thread.join()

    assert fire_ident and fire_ident[0] != _MAIN_IDENT, "回调必须真的在另一个线程里跑"
    assert _pump(lambda: len(rec.calls) >= 1), f"主线程没收到 triggered: {rec.calls}"
    assert rec.calls == [("vol_up", True)]


def test_已注销的热键迟到回调被丢弃(bridge: HotkeyBridge, fake: _FakeBackend):
    rec = _TriggerRecorder()
    bridge.triggered.connect(rec.on_triggered)
    bridge.register("vol_mute", "ctrl+shift+m")
    bridge.unregister("vol_mute")
    fake.fire("ctrl+shift+m")  # 后端线程上还留着旧回调时会发生这种情况
    _APP.processEvents()
    assert rec.calls == []


def test_桥被销毁后热键回调不崩(fake: _FakeBackend, caplog: pytest.LogCaptureFixture):
    import shiboken6

    obj = HotkeyBridge(fake)
    obj.register("next", "ctrl+shift+right")
    shiboken6.delete(obj)
    with caplog.at_level(logging.DEBUG, logger="app.bridges.hotkey_bridge"):
        fake.fire("ctrl+shift+right")  # 后端线程上的回调晚于桥的销毁
    assert "ERROR" not in caplog.text
    assert "已丢弃" in caplog.text


# ─── 降级 ───────────────────────────────────────────────────


def test_后端抛异常时桥降级不打断启动():
    failures: List[Tuple[str, str]] = []
    bridge = HotkeyBridge(_RaisingBackend())
    bridge.registrationFailed.connect(lambda name, reason: failures.append((name, reason)))
    assert bridge.register("next", "ctrl+shift+right") is False
    assert bridge.activeKeys == []
    assert failures and "模拟注册被系统拒绝" in failures[0][1]
    assert bridge.unregister_all() == 0  # 没注册成功的东西不会去注销


def test_真后端在_keyboard_缺失时优雅降级(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture):
    """真后端 + 模块缺失：只 warning，不抛异常打断启动。"""
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "keyboard":
            raise ImportError("模拟未安装 keyboard")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with caplog.at_level(logging.WARNING, logger="app.bridges.hotkey_bridge"):
        backend = KeyboardHotkeyBackend()
        assert backend.available is False
        with pytest.raises(HotkeyBackendUnavailable):
            backend.add_hotkey("ctrl+a", lambda: None)
        bridge = HotkeyBridge(backend)
        assert bridge.backendAvailable is False
        assert bridge.register("next", "ctrl+shift+right") is False
        assert "降级" in caplog.text or "失败" in caplog.text
    assert bridge.activeKeys == []


def test_真后端在无桌面环境下只报告可用性不注册任何热键():
    """只读 ``available``（纯 import），**不调 add_hotkey**：本测试不碰系统热键。"""
    backend = KeyboardHotkeyBackend()
    available = backend.available
    assert isinstance(available, bool)
    if available:  # 本机装了 keyboard：再确认一次"预热"与"释放"都能安全往返
        assert backend.prewarm() is True
        backend.release_warmup()


def test_NullHotkeyBackend_下桥能跑完整个链路不崩():
    null_backend = NullHotkeyBackend()
    bridge = HotkeyBridge(null_backend)
    assert bridge.backendAvailable is False  # 空后端如实报告"没接真实系统"
    assert bridge.register("play_pause", "ctrl+shift+space", "播放/暂停") is True  # 只记账
    assert bridge.activeKeys == ["play_pause"]
    assert null_backend.calls == [("add", "ctrl+shift+space")]
    assert bridge.unregister_all() == 1
    assert null_backend.calls[-1][0] == "remove"
    assert bridge.activeKeys == []


# ─── 8 组现有热键清单 ───────────────────────────────────────


def test_清单里的组合键与_services_里的表逐字一致():
    """清单会随服务漂移，所以直接拿服务里的唯一真相来对。"""
    import services.monitor_service as monitor_service
    import services.music_hotkeys as music_hotkeys

    combos = {item["name"]: item["combo"] for item in LEGACY_HOTKEYS}
    assert len(LEGACY_HOTKEYS) == 8
    assert combos["monitor_toggle"] == monitor_service.MONITOR_HOTKEY
    for action in music_hotkeys.HOTKEY_ACTIONS:
        assert combos[action] == music_hotkeys.DEFAULT_HOTKEYS[action]
    # 已知冲突：监控开关与音乐静音是同一个组合键（本轮只记录不修）
    assert combos["monitor_toggle"] == combos["vol_mute"] == "ctrl+shift+m"


def test_清单每一项都写明了注册模块与回调():
    for item in LEGACY_HOTKEYS:
        assert item["owner"], item
        assert item["callback"], item
        assert item["callback_module"].endswith(
            (
                "_toggle_monitor",
                "_music_hotkey_play_pause",
                "_music_hotkey_prev",
                "_music_hotkey_next",
                "_music_hotkey_stop",
                "_music_hotkey_vol_up",
                "_music_hotkey_vol_down",
                "_music_hotkey_vol_mute",
            )
        ), item
