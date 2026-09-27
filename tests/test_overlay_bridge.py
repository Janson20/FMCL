"""悬浮窗桥的回归守卫（阶段 2 任务 2.15）。

## 这一组测试真正想钉住什么

1. **三种窗口的窗口语义只有一份实现**：显示/隐藏/切换、`configure` 的属性生效、
   位置持久化往返、`overlayFailed` 在所有非法入口上都发得出来；
2. **坐标换算**（最容易写错的一处）：`toLogical` / `toPhysical` 用**注入的 dpr**
   （1.25 / 1.5 / 2.0）与**非零的屏幕原点**做双向断言，不依赖本机 1.25；
3. **Win32 补丁的调用顺序**（阶段 0 第 11.4 节的硬约束）：
   `winId()` -> 读/写 `GWL_EXSTYLE` -> `SetWindowPos(SWP_FRAMECHANGED|SWP_NOACTIVATE)`。
   这条在离屏环境下**可以**用注入的 user32 替身断言顺序；但"补丁在真实桌面上
   真的让窗口不抢焦点"这件事**无法在本环境自动化验证**（需要真实 HWND 与鼠标），
   报告里如实列为未验证。
4. **非 Windows / offscreen 的降级路径**：`platformSupported == False`、
   `applyNoActivate()` 返回 `False` 且**只记 debug**（那是预期降级，不是错误）。

## 纪律

* `QT_QPA_PLATFORM=offscreen` 在 import PySide6 **之前**设好；
* **注入假配置**（`FakeConfig`）：这一组测试一次都不许碰真实的 `config.json`；
* 不引第三方测试依赖（没有 pytest-qt）：自己建 `QGuiApplication` + `QTest.qWait`
  驱动事件循环。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import logging  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QGuiApplication, QWindow  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.bridges import overlay_bridge as ob  # noqa: E402


def _qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


_APP = _qapp()


def wait_until(predicate: Any, timeout_ms: int = 4000, step_ms: int = 20) -> bool:
    """把事件循环转到条件成立为止（返回是否等到）。

    为什么不用"睡固定时间再断言"：那是**时间代理断言**，机器一忙就假红 ——
    指标采集这条链是 `worker 线程取数 → 主线程 flush 定时器（100ms）→ 通知`，
    全量测试里同进程还挂着别的用例留下的线程池时，250ms 并不总是够。
    本仓库已经因为同一类写法修过一次（`tests/test_ui_port_qt.py` 的 60ms/50ms 赛跑）。
    """
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return True
        QTest.qWait(step_ms)
        waited += step_ms
    return bool(predicate())


# ─── 替身 ───────────────────────────────────────────────────────


class FakeConfig:
    """假配置：**不碰**真实 `config.json`，记录落盘次数。"""

    def __init__(self, overlay_geometry: Any = None) -> None:
        self.overlay_geometry = {} if overlay_geometry is None else overlay_geometry
        self.saves = 0

    def save_config(self) -> None:
        self.saves += 1


class FailingConfig(FakeConfig):
    """落盘必失败的配置（模拟只读目录 / 磁盘满）。"""

    def save_config(self) -> None:
        raise OSError("磁盘只读")


class Spy:
    """信号记录器。"""

    def __init__(self, signal: Any) -> None:
        self.calls: List[Tuple[Any, ...]] = []
        signal.connect(self._record)

    def _record(self, *args: Any) -> None:
        self.calls.append(args)

    def __len__(self) -> int:
        return len(self.calls)


class FakeUser32:
    """user32 替身：记录调用顺序与参数。

    `log` 是与 `FakeWindow` 共享的一个列表 —— 只有把两边的动作记进**同一个**序列，
    才能断言"先 winId() 再写样式"这个顺序（阶段 0 第 11.4 节）。
    """

    def __init__(self, exstyle: int = 0, log: Optional[List[str]] = None) -> None:
        self.exstyle = int(exstyle)
        self.calls: List[Tuple[str, Any]] = []
        self.log = log if log is not None else []

    def GetWindowLongW(self, hwnd: Any, index: int) -> int:  # noqa: N802 - Win32 名字
        self.log.append("GetWindowLongW")
        self.calls.append(("GetWindowLongW", int(hwnd)))
        return self.exstyle

    def SetWindowLongW(self, hwnd: Any, index: int, value: int) -> int:  # noqa: N802
        self.log.append("SetWindowLongW")
        self.calls.append(("SetWindowLongW", int(value)))
        self.exstyle = int(value)
        return 0

    def SetWindowPos(self, hwnd: Any, after: Any, x: int, y: int, cx: int, cy: int, flags: int) -> int:  # noqa: N802
        self.log.append("SetWindowPos")
        self.calls.append(("SetWindowPos", int(flags)))
        return 1


class FakeWindow:
    """只实现桥用到的那一个方法：`winId()`。"""

    def __init__(self, hwnd: int = 0x1234, log: Optional[List[str]] = None) -> None:
        self._hwnd = hwnd
        self.log = log if log is not None else []
        self.win_id_calls = 0

    def winId(self) -> int:  # noqa: N802 - Qt 名字
        self.log.append("winId")
        self.win_id_calls += 1
        return self._hwnd


class FakeHotkeys(QObject):
    """`HotkeyBridge` 的最小替身（只要一个 `triggered` 信号）。"""

    triggered = Signal(str)


# ─── 夹具 ───────────────────────────────────────────────────────


def fake_screen(
    dpr: float = 1.25,
    logical: Tuple[int, int, int, int] = (0, 0, 2048, 1152),
    name: str = "FAKE-1",
) -> ob.ScreenInfo:
    """按 `logical * dpr` 造一份屏幕信息（与 `read_screen_info` 同一条公式）。"""
    physical = (
        ob._round_half_up(logical[0] * dpr), ob._round_half_up(logical[1] * dpr),
        ob._round_half_up(logical[2] * dpr), ob._round_half_up(logical[3] * dpr),
    )
    return ob.ScreenInfo(name=name, dpr=dpr, logical=logical, physical=physical)


def make_bridge(
    config: Any = None,
    screen: Optional[ob.ScreenInfo] = None,
    metrics_provider: Any = None,
    executor: Any = None,
    user32: Any = None,
) -> ob.OverlayBridge:
    """建一个桥：默认注入假配置 + dpr 1.25 的假屏幕。"""
    return ob.OverlayBridge(
        config=FakeConfig() if config is None else config,
        screen_provider=(lambda: screen if screen is not None else fake_screen()),
        metrics_provider=metrics_provider,
        executor=executor if executor is not None else (lambda fn: fn()),
        user32=user32,
    )


@pytest.fixture
def bridge() -> ob.OverlayBridge:
    made = make_bridge()
    yield made
    made.shutdown()


# ─── 1. 显示 / 隐藏 / 切换 ───────────────────────────────────────


@pytest.mark.parametrize("kind", ob.KINDS)
def test_show_hide_toggle_for_every_kind(bridge: ob.OverlayBridge, kind: str) -> None:
    visible_prop = {"monitor": "monitorVisible", "lyric": "lyricVisible", "toast": "toastVisible"}[kind]
    assert bridge.property(visible_prop) is False, "新建的桥三个窗口都该是隐藏的"

    bridge.setVisible(kind, True)
    assert bridge.property(visible_prop) is True
    bridge.setVisible(kind, False)
    assert bridge.property(visible_prop) is False

    changed = Spy(bridge._visibility_signals[kind])
    assert bridge.setVisible(kind, True) is True
    assert bridge.property(visible_prop) is True
    assert len(changed) == 1, "可见性变化必须发一次 *VisibleChanged（QML 绑定靠它）"


def test_monitor_and_lyric_toggle_signals(bridge: ob.OverlayBridge) -> None:
    monitors, lyrics = Spy(bridge.monitorToggled), Spy(bridge.lyricToggled)
    bridge.toggleMonitor()
    assert bridge.monitorVisible is True and monitors.calls == [(True,)]
    bridge.toggleMonitor()
    assert bridge.monitorVisible is False and monitors.calls == [(True,), (False,)]

    bridge.toggleLyric()
    bridge.hideLyric()
    assert lyrics.calls == [(True,), (False,)]
    assert monitors.calls[-1] == (False,), "歌词窗的开关不该惊动监控窗的信号"


def test_repeated_show_does_not_respam_signals(bridge: ob.OverlayBridge) -> None:
    """幂等：重复 show 不该刷信号（QML 的 NOTIFY 语义）。"""
    toggled, changed = Spy(bridge.monitorToggled), Spy(bridge.monitorVisibleChanged)
    bridge.showMonitor()
    bridge.showMonitor()
    bridge.showMonitor()
    assert len(toggled) == 1 and len(changed) == 1


def test_show_toast_and_hide_toast(bridge: ob.OverlayBridge) -> None:
    bridge.showToast()
    assert bridge.toastVisible is True
    bridge.hideToast()
    assert bridge.toastVisible is False


def test_negative_valid_kinds_never_report_failure(bridge: ob.OverlayBridge) -> None:
    """负例自检（1/3）：`overlayFailed` 不是"永远在发"的假信号。

    如果它被写成无条件 emit，下面这条就会红 —— 所以"非法 kind 下发出"的断言才有意义。
    """
    failed = Spy(bridge.overlayFailed)
    for kind in ob.KINDS:
        bridge.setVisible(kind, True)
        bridge.configure(kind, {"opacity": 0.5})
        bridge.reportPosition(kind, 1, 2)
        bridge.geometryFor(kind)
    assert failed.calls == [], f"合法调用竟然报了失败: {failed.calls}"


# ─── 2. 非法 kind / 非法属性 ─────────────────────────────────────


def test_illegal_kind_emits_overlay_failed_everywhere(bridge: ob.OverlayBridge) -> None:
    """非法 kind 在**每一个**带 kind 的入口上都要发 `overlayFailed`。"""
    failed = Spy(bridge.overlayFailed)
    assert bridge.setVisible("bogus", True) is False
    assert bridge.configure("bogus", {"opacity": 0.5}) is False
    bridge.reportPosition("bogus", 1, 2)
    assert bridge.geometryFor("bogus") == {}
    assert bridge.positionFor("bogus") == {}
    assert bridge.defaultGeometry("bogus") == {}
    assert bridge.applyNoActivate("bogus") is False
    assert bridge.forgetPosition("bogus") is False
    assert bridge.setupWindow("bogus", QObject()) is False
    assert bridge.increaseOpacity("bogus") == 0.0
    assert len(failed) == 10, f"应当每一处都报一次失败，实际 {failed.calls}"
    assert all("bogus" in str(call[0]) for call in failed.calls)


# ─── 3. configure 的属性生效 ─────────────────────────────────────


def test_configure_applies_props_and_notifies(bridge: ob.OverlayBridge) -> None:
    configured, config_changed = Spy(bridge.overlayConfigured), Spy(bridge.configChanged)
    assert bridge.configure("monitor", {"opacity": 0.5, "width": 320, "height": 180, "topMost": False}) is True
    props = bridge.monitorProps
    assert (props["opacity"], props["width"], props["height"], props["topMost"]) == (0.5, 320, 180, False)
    assert bridge.lyricProps["opacity"] == 0.85, "改监控窗不该动歌词窗"
    assert len(config_changed) == 1 and len(configured) == 1
    assert configured.calls[0][0] == "monitor"
    assert configured.calls[0][1]["width"] == 320


@pytest.mark.parametrize(
    "props,expect_ok,unchanged",
    [
        ({"width": "320"}, False, "width"),
        ({"height": None}, False, "height"),
        ({"height": 0}, False, "height"),
        ({"height": -10}, False, "height"),
        ({"width": 20000}, False, "width"),
        ({"x": 1.5}, False, None),
        ({"y": "10"}, False, None),
        ({"topMost": "yes"}, False, "topMost"),
        ({"opactiy": 0.5}, False, None),
        ({"zzz": 1}, False, None),
    ],
)
def test_configure_rejects_bad_props(
    bridge: ob.OverlayBridge, props: Dict[str, Any], expect_ok: bool, unchanged: Optional[str]
) -> None:
    failed = Spy(bridge.overlayFailed)
    before = dict(bridge.monitorProps)
    before_pos = bridge.geometryFor("monitor")
    assert bridge.configure("monitor", props) is expect_ok
    assert len(failed) == 1, f"非法属性必须报一次失败: {props}"
    for key, value in props.items():
        if key in before:
            assert bridge.monitorProps[key] == before[key], f"被拒绝的 {key} 不该落进属性表"
    if "x" in props or "y" in props:
        assert (bridge.geometryFor("monitor")["x"], bridge.geometryFor("monitor")["y"]) == (
            before_pos["x"], before_pos["y"]
        ), "被拒绝的坐标不该改动位置"
    if unchanged is not None:
        assert unchanged in before, "参数表写错了：这个键本来就在属性表里"


def test_configure_clamps_opacity_instead_of_rejecting() -> None:
    """超出 [0,1] 的不透明度是"钳制"而不是"拒绝"（旧实现的 +/- 就是靠边界钳制的）。"""
    bridge = make_bridge()
    try:
        assert bridge.configure("lyric", {"opacity": 5}) is True
        assert bridge.lyricProps["opacity"] == 1.0
        assert bridge.configure("lyric", {"opacity": -5}) is True
        assert bridge.lyricProps["opacity"] == 0.0
    finally:
        bridge.shutdown()


def test_configure_xy_moves_position_and_emits_moved(bridge: ob.OverlayBridge) -> None:
    moved = Spy(bridge.overlayMoved)
    assert bridge.configure("lyric", {"x": 111, "y": 222}) is True
    assert bridge.geometryFor("lyric")["x"] == 111
    assert bridge.geometryFor("lyric")["y"] == 222
    assert bridge.geometryFor("lyric")["source"] == "restored"
    assert ("lyric", 111, 222) in moved.calls


def test_configure_with_mixed_props_keeps_the_valid_ones(bridge: ob.OverlayBridge) -> None:
    """一次调用里合法键照常生效，非法键报失败 —— 不要"一颗老鼠屎坏一锅汤"。"""
    failed = Spy(bridge.overlayFailed)
    assert bridge.configure("monitor", {"opacity": 0.42, "bogus": 1}) is False
    assert bridge.monitorProps["opacity"] == 0.42
    assert len(failed) == 1


def test_configure_empty_props_is_a_noop_query(bridge: ob.OverlayBridge) -> None:
    configured = Spy(bridge.overlayConfigured)
    assert bridge.configure("toast", {}) is True
    assert len(configured) == 1 and configured.calls[0] == ("toast", bridge.toastProps)


def test_opacity_nudge_uses_service_limits(bridge: ob.OverlayBridge) -> None:
    from services.desktop_lyric import ALPHA_MAX, ALPHA_MIN

    for _ in range(30):
        high = bridge.increaseOpacity("lyric")
    assert high == pytest.approx(ALPHA_MAX), "上限必须来自 services.desktop_lyric.ALPHA_MAX"
    for _ in range(30):
        low = bridge.decreaseOpacity("lyric")
    assert low == pytest.approx(ALPHA_MIN), "下限必须来自 services.desktop_lyric.ALPHA_MIN"
    assert bridge.lyricProps["opacity"] == pytest.approx(ALPHA_MIN)


def test_toggle_lyric_lock_uses_service_rule(bridge: ob.OverlayBridge) -> None:
    changed = Spy(bridge.lyricLockChanged)
    assert bridge.lyricLocked is False
    assert bridge.toggleLyricLock() is True
    assert bridge.lyricLocked is True and bridge.lyricProps["locked"] is True
    assert bridge.toggleLyricLock() is False
    assert len(changed) == 2
    # configure 的 locked 与专用槽改的是同一份状态
    assert bridge.configure("lyric", {"locked": True}) is True
    assert bridge.lyricLocked is True
    assert len(changed) == 3


# ─── 4. 位置持久化 ───────────────────────────────────────────────


def test_position_persistence_round_trip() -> None:
    """回报 -> 落盘 -> 新建桥（同一份配置）能读回同样的位置。"""
    cfg = FakeConfig()
    first = make_bridge(config=cfg)
    try:
        first.reportPosition("monitor", 1234, 56)
        first.reportPosition("lyric", 700, 900)
        first.configure("lyric", {"opacity": 0.6})
        first.savePosition()
    finally:
        first.shutdown()
    assert cfg.saves == 1, "savePosition 必须真的落盘一次"
    assert cfg.overlay_geometry["monitor"] == {
        "x": 1234, "y": 56, "width": 400, "height": 200, "opacity": 0.8,
    }
    assert cfg.overlay_geometry["lyric"]["x"] == 700

    second = make_bridge(config=cfg)  # 模拟重启：同一份配置
    try:
        geo = second.geometryFor("monitor")
        assert (geo["x"], geo["y"], geo["source"]) == (1234, 56, "restored")
        assert second.geometryFor("lyric")["opacity"] == 0.6
        assert "toast" not in cfg.overlay_geometry, "没建过窗的 kind 不该被写入空位置"
    finally:
        second.shutdown()


def test_save_position_merges_instead_of_overwriting() -> None:
    """合并语义：配置里别的键（用户手加的 / 别的 kind）不能被抹掉。"""
    cfg = FakeConfig({
        "monitor": {"x": 10, "y": 20, "note": "手写的"},
        "unknown_kind": {"x": 1, "y": 2},
    })
    bridge = make_bridge(config=cfg)
    try:
        bridge.reportPosition("lyric", 5, 6)
        bridge.savePosition()
    finally:
        bridge.shutdown()
    assert cfg.overlay_geometry["monitor"]["x"] == 10, "同 kind 的旧值在没回报过时保留"
    assert cfg.overlay_geometry["monitor"]["note"] == "手写的"
    assert cfg.overlay_geometry["unknown_kind"] == {"x": 1, "y": 2}
    assert cfg.overlay_geometry["lyric"]["x"] == 5


def test_negative_save_keeps_config_entries_it_cannot_parse() -> None:
    """负例自检（2/3）：落盘是**合并**语义，读不懂的旧条目一个字都不能动。

    如果 `savePosition` 写成"从内存重建整个 payload"，用户手改过或旧版本留下的
    条目会被抹掉 —— 那正是"拖一次窗口就把别的窗口位置弄丢"的真实缺陷。
    """
    cfg = FakeConfig({"monitor": {"x": "100", "y": 200}})
    bridge = make_bridge(config=cfg)
    try:
        assert bridge.geometryFor("monitor")["source"] == "default", "坏坐标解析不进内存"
        bridge.reportPosition("lyric", 1, 2)
        bridge.savePosition()
    finally:
        bridge.shutdown()
    assert cfg.overlay_geometry["monitor"] == {"x": "100", "y": 200}, "读不懂的旧条目必须原样保留"
    assert "toast" not in cfg.overlay_geometry, "既没建过窗、配置里也没有的 kind 不该被补写空位置"
    assert cfg.overlay_geometry["lyric"]["x"] == 1


def test_save_position_keeps_the_position_of_a_kind_it_only_restored() -> None:
    """从配置恢复出来的位置不该因为落盘而丢失（只会顺带补上当前的尺寸/不透明度）。"""
    cfg = FakeConfig({"toast": {"x": 5, "y": 6}})
    bridge = make_bridge(config=cfg)
    try:
        bridge.reportPosition("lyric", 1, 2)
        bridge.savePosition()
    finally:
        bridge.shutdown()
    assert (cfg.overlay_geometry["toast"]["x"], cfg.overlay_geometry["toast"]["y"]) == (5, 6)
    assert cfg.overlay_geometry["toast"]["width"] == 280, "顺带写回当前尺寸（configure 改过才需要）"


def test_save_position_reports_failure_when_disk_write_fails() -> None:
    bridge = make_bridge(config=FailingConfig())
    try:
        failed = Spy(bridge.overlayFailed)
        bridge.reportPosition("monitor", 1, 2)
        bridge.savePosition()
        assert len(failed) == 1 and "写盘失败" in failed.calls[0][0]
    finally:
        bridge.shutdown()


def test_restore_position_pushes_only_changed_kinds() -> None:
    """`restorePosition()` 的语义：把**与配置不一致**的位置推回窗口（拖动后"复位"）。"""
    cfg = FakeConfig({"monitor": {"x": 11, "y": 22}, "lyric": {"x": 33, "y": 44}})
    bridge = make_bridge(config=cfg)
    try:
        bridge.reportPosition("monitor", 500, 600)  # 模拟被拖走：内存与配置不一致
        moved = Spy(bridge.overlayMoved)
        bridge.restorePosition()
        assert moved.calls == [("monitor", 11, 22)], f"只该推不一致的那个: {moved.calls}"
        assert (bridge.geometryFor("monitor")["x"], bridge.geometryFor("monitor")["y"]) == (11, 22)
        assert bridge.geometryFor("monitor")["source"] == "restored"
        bridge.restorePosition()
        assert len(moved) == 1, "第二次恢复没有任何变化，不该再发信号（否则 QML 那边会成环）"
    finally:
        bridge.shutdown()


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "not-a-dict",
        {"monitor": "not-a-dict"},
        {"monitor": {"x": "100", "y": 200}},
        {"monitor": {"x": True, "y": 5}},
        {"monitor": {"x": 1}},
        {"lyric": {"x": 1, "y": 2, "width": -5, "height": 0, "opacity": float("nan")}},
    ],
)
def test_restore_position_tolerates_corrupt_config(payload: Any) -> None:
    """坏配置（用户手改过）只该被忽略，不该把桥卡住。"""
    cfg = FakeConfig(payload)
    bridge = make_bridge(config=cfg)
    try:
        bridge.restorePosition()
        assert bridge.geometryFor("monitor")["source"] == "default"
    finally:
        bridge.shutdown()


# ─── 5. 坐标与 DPI（最容易写错的一处） ──────────────────────────


def test_screen_properties_and_conversion_with_injected_dpr() -> None:
    """注入 dpr=1.25（本机实测值，但这里**不读本机**）与 2048x1152 -> 2560x1440。

    这一对数字正是阶段 0 第 12.1 节记录的环境事实：
    逻辑 2048x1152 / 物理 2560x1440 / dpr 1.25。
    """
    bridge = make_bridge(screen=fake_screen(dpr=1.25))
    try:
        assert bridge.devicePixelRatio == pytest.approx(1.25)
        assert bridge.screenName == "FAKE-1"
        assert bridge.screenGeometry == {"x": 0, "y": 0, "width": 2048, "height": 1152}
        assert bridge.physicalScreenGeometry == {"x": 0, "y": 0, "width": 2560, "height": 1440}
        physical = bridge.toPhysical(100, 200)
        assert (physical["x"], physical["y"], physical["system"]) == (125, 250, "physical")
        logical = bridge.toLogical(125, 250)
        assert (logical["x"], logical["y"], logical["system"]) == (100, 200, "logical")
    finally:
        bridge.shutdown()


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_coordinate_round_trip_is_exact(dpr: float) -> None:
    """往返必须精确（每一步的舍入误差 < 0.5/dpr，dpr >= 1 时必定回到原值）。"""
    bridge = make_bridge(screen=fake_screen(dpr=dpr))
    try:
        for x, y in [(0, 0), (1, 1), (3, 7), (100, 200), (2047, 1151), (-40, -17)]:
            back = bridge.toLogical(bridge.toPhysical(x, y)["x"], bridge.toPhysical(x, y)["y"])
            assert (back["x"], back["y"]) == (x, y), f"dpr={dpr} ({x},{y}) 往返变成 ({back['x']},{back['y']})"
    finally:
        bridge.shutdown()


def test_conversion_respects_a_nonzero_screen_origin() -> None:
    """屏幕原点非零（副屏在主屏左侧/上方）时，换算要带上两套原点。

    这里的物理原点**故意不等于** `逻辑原点 * dpr`：那正是混合 DPI 双屏的真实样子
    （100% 的主屏 + 150% 的副屏），也是"直接乘 dpr"这种错误实现会露馅的地方。
    """
    screen = ob.ScreenInfo(name="FAKE-2", dpr=1.5, logical=(-1920, 0, 1920, 1080), physical=(-2560, 0, 2880, 1620))
    bridge = make_bridge(screen=screen)
    try:
        physical = bridge.toPhysical(-1820, 100)
        assert (physical["x"], physical["y"]) == (-2410, 150)
        back = bridge.toLogical(-2410, 150)
        assert (back["x"], back["y"]) == (-1820, 100)
        assert bridge.physicalScreenGeometry == {"x": -2560, "y": 0, "width": 2880, "height": 1620}
    finally:
        bridge.shutdown()


def test_negative_naive_scaling_differs_on_a_nonzero_origin() -> None:
    """负例自检（3/3）：保证上面那些换算断言**不是空的**。

    "忘了减逻辑原点、再加物理原点"的实现（等价于直接 `x * dpr`）在原点非零且
    物理原点不等于 `逻辑原点 * dpr` 时会算出不同的值。如果换算函数被写坏成这样，
    上面的断言会红 —— 反之，这条不同就说明那一组断言确实在看东西。
    """
    screen = ob.ScreenInfo(name="FAKE-2", dpr=1.5, logical=(-1920, 0, 1920, 1080), physical=(-2560, 0, 2880, 1620))
    bridge = make_bridge(screen=screen)
    try:
        correct = bridge.toPhysical(-1820, 100)
        naive = (ob._round_half_up(-1820 * 1.5), ob._round_half_up(100 * 1.5))
        assert (correct["x"], correct["y"]) != naive
        assert correct["x"] - naive[0] == 320, "差值 == 物理原点与逻辑原点缩放后的偏差"
    finally:
        bridge.shutdown()


def test_position_persistence_uses_logical_pixels() -> None:
    """落盘的是逻辑像素：QML 的 `Window.x/y` 能直接拿它复原，不需要再换算。"""
    cfg = FakeConfig()
    bridge = make_bridge(config=cfg, screen=fake_screen(dpr=1.25))
    try:
        bridge.reportPosition("monitor", 1600, 48)
        bridge.savePosition()
    finally:
        bridge.shutdown()
    assert cfg.overlay_geometry["monitor"]["x"] == 1600
    assert cfg.overlay_geometry["monitor"]["x"] * 1.25 == 2000, "物理像素是换算结果，**不**该出现在配置里"


# ─── 6. 默认摆放 ─────────────────────────────────────────────────


def test_default_positions_come_from_the_reference_screen() -> None:
    bridge = make_bridge(screen=fake_screen(dpr=1.25, logical=(-1920, -100, 1920, 1080)))
    try:
        monitor = bridge.defaultGeometry("monitor")
        # 右上角：屏幕右边界 - 窗宽 - 10，上边界 + 50（旧 ui/app_monitor.py:68 的规则）
        assert (monitor["x"], monitor["y"]) == (0 - 400 - 10, -100 + 50)
        lyric = bridge.defaultGeometry("lyric")
        # 下方居中：由 services.desktop_lyric.calc_center_bottom_position 算，桥只加屏幕原点
        from services.desktop_lyric import calc_center_bottom_position

        base_x, base_y = calc_center_bottom_position(1920, 1080, 600, 140)
        assert (lyric["x"], lyric["y"]) == (-1920 + base_x, -100 + base_y)
        toast = bridge.defaultGeometry("toast")
        assert (toast["x"], toast["y"]) == (-1920 + 1920 - 280 - 16, -100 + 1080 - 72 - 16)
    finally:
        bridge.shutdown()


def test_default_position_follows_window_screen_not_primary() -> None:
    """行为改进（阶段 0 第 12.5 节）：默认位置取**窗口所在屏幕**，不是 primaryScreen。

    做法：让桥"看得见"一个登记过的窗口，并让 `screen_provider` 报告那块屏的几何；
    这里断言的是"桥用的是提供者给的屏幕"这条链路（真多屏行为**未在真机验证**）。
    """
    screen = fake_screen(dpr=1.0, logical=(1920, 0, 1920, 1080), name="SECOND")
    bridge = make_bridge(screen=screen)
    try:
        window = QWindow()
        window.setObjectName("monitorOverlay")
        assert bridge.setupWindow("monitor", window) is True
        geo = bridge.geometryFor("monitor")
        assert geo["x"] == 1920 + 1920 - 400 - 10, f"用了参照屏幕的右边界: {geo}"
        window.setObjectName("")  # 别把同名窗口留给后面的用例扫到
        window.close()
    finally:
        bridge.shutdown()


# ─── 7. 平台能力 与 Win32 补丁 ───────────────────────────────────


def test_platform_supported_is_false_under_offscreen(bridge: ob.OverlayBridge) -> None:
    assert bridge.platformName == "offscreen"
    assert bridge.platformSupported is False
    assert ob.native_patch_supported() is False


def test_apply_no_activate_degrades_with_a_debug_log(bridge: ob.OverlayBridge, caplog: Any) -> None:
    """非 Windows：返回 False 但**不报错**（预期降级），只留一条 debug。"""
    failed = Spy(bridge.overlayFailed)
    with caplog.at_level(logging.DEBUG, logger="app.bridges.overlay_bridge"):
        assert bridge.applyNoActivate("monitor") is False
    assert len(failed) == 0, "降级不该发 overlayFailed"
    assert any("跳过" in record.getMessage() for record in caplog.records), "应当留一条 debug 说明原因"


def test_apply_no_activate_call_order_matches_phase0(monkeypatch: Any) -> None:
    """阶段 0 第 11.4 节的顺序：`winId()` -> 置位 -> `SetWindowPos` -> 读回校验。"""
    monkeypatch.setattr(ob, "_qpa_platform_name", lambda: "windows")
    log: List[str] = []
    api, window = FakeUser32(log=log), FakeWindow(log=log)
    assert ob.apply_no_activate(window, user32=api) is True
    assert log == ["winId", "GetWindowLongW", "SetWindowLongW", "SetWindowPos", "GetWindowLongW"], log
    flags = [value for name, value in api.calls if name == "SetWindowPos"][0]
    assert flags & ob.SWP_FRAMECHANGED, "必须带 SWP_FRAMECHANGED，否则样式改动不立即生效"
    assert flags & ob.SWP_NOACTIVATE, "必须带 SWP_NOACTIVATE，否则这次调用本身会激活窗口"
    assert flags & ob.SWP_NOMOVE and flags & ob.SWP_NOSIZE and flags & ob.SWP_NOZORDER
    assert api.exstyle & ob.WS_EX_NOACTIVATE
    assert api.calls[1][1] == (0 | ob.WS_EX_NOACTIVATE), "写下去的值应当只多出 WS_EX_NOACTIVATE 这一位"


def test_apply_no_activate_is_idempotent(monkeypatch: Any) -> None:
    """已经置上的窗口不该重复写样式位（每次显示都会调它，幂等是必需的）。"""
    monkeypatch.setattr(ob, "_qpa_platform_name", lambda: "windows")
    log: List[str] = []
    api = FakeUser32(exstyle=ob.WS_EX_NOACTIVATE, log=log)
    assert ob.apply_no_activate(FakeWindow(log=log), user32=api) is True
    assert "SetWindowLongW" not in log, f"不该重复写样式位: {log}"


def test_apply_no_activate_without_a_native_handle(monkeypatch: Any) -> None:
    """`winId()` 为 0（没有原生句柄）时直接跳过，不去写一个假句柄。"""
    monkeypatch.setattr(ob, "_qpa_platform_name", lambda: "windows")
    api = FakeUser32()
    assert ob.apply_no_activate(FakeWindow(hwnd=0), user32=api) is False
    assert api.calls == []


def test_apply_no_activate_missing_window_reports_failure(monkeypatch: Any) -> None:
    bridge = make_bridge(user32=FakeUser32())
    try:
        monkeypatch.setattr(ob, "_qpa_platform_name", lambda: "windows")
        failed = Spy(bridge.overlayFailed)
        assert bridge.applyNoActivate("toast") is False
        assert len(failed) == 1 and "未登记" in failed.calls[0][0]
    finally:
        bridge.shutdown()


def test_setup_window_registers_and_falls_back_to_object_name(bridge: ob.OverlayBridge) -> None:
    # 没登记时按约定的 objectName 扫 allWindows() 兜底（阶段 0 的 PoC 就是这么找窗口的）
    stray = QWindow()
    stray.setObjectName("lyricOverlay")
    stray.show()
    QTest.qWait(20)
    try:
        assert bridge.describe()["windows"] == [], "登记表还是空的（下面命中靠的是 objectName 兜底）"
        assert bridge.isRegistered("lyric") is True
    finally:
        stray.setObjectName("")
        stray.close()

    registered = QWindow()
    registered.setObjectName("monitorOverlay")
    try:
        assert bridge.setupWindow("monitor", registered) is True
        assert bridge.isRegistered("monitor") is True
        assert bridge.describe()["windows"] == ["monitor"], "登记过的窗口要出现在诊断快照里"
    finally:
        registered.setObjectName("")
        registered.close()


# ─── 8. 指标（值来自服务，桥只转发） ─────────────────────────────


def test_metrics_refresh_updates_property_through_the_flush_timer() -> None:
    """worker 取数 -> 主线程定时器合并 -> `monitorMetrics` 更新并 notify。"""
    calls: List[int] = []
    payload = {"cpu_percent": 42.5, "cpu_freq": "3420 MHz", "mem_percent": 61, "gpu_util": "18%"}
    bridge = make_bridge(metrics_provider=lambda: calls.append(1) or payload)
    try:
        changed = Spy(bridge.metricsChanged)
        assert bridge.monitorMetrics == {}, "还没采集时是空表"
        bridge.showMonitor()
        assert bridge.metricsRunning is True, "监控窗可见就必须在轮询"
        # 等**条件**而不是等固定时间：worker 取数 + 主线程 flush 定时器（100ms）都要走完
        assert wait_until(lambda: bridge.monitorMetrics == payload), (
            f"flush 定时器过后 monitorMetrics 仍是 {bridge.monitorMetrics!r}"
        )
        assert len(changed) >= 1
        assert calls, "provider 必须被真的调用过"
    finally:
        bridge.shutdown()


def test_metrics_polling_stops_when_monitor_hides() -> None:
    calls: List[int] = []
    bridge = make_bridge(metrics_provider=lambda: calls.append(1) or {"cpu_percent": 1})
    try:
        bridge.showMonitor()
        # 先等到**真的采过一次**再隐藏：不然 `after_hide == 0`，后面的断言是空断言
        # （"没有再采"在"从来没采过"时也成立）。
        assert wait_until(lambda: len(calls) >= 1), "监控窗显示后一直没采集，判据不成立"
        bridge.hideMonitor()
        assert bridge.metricsRunning is False
        after_hide = len(calls)
        QTest.qWait(300)
        assert len(calls) == after_hide, "隐藏后不该继续采集（不可见的窗口不采）"
    finally:
        bridge.shutdown()


def test_metrics_provider_error_does_not_break_the_bridge() -> None:
    def boom() -> Dict[str, Any]:
        raise RuntimeError("psutil 炸了")

    bridge = make_bridge(metrics_provider=boom)
    try:
        bridge.showMonitor()
        assert wait_until(lambda: "error" in bridge.monitorMetrics), (
            f"采集失败没在指标里露出来：{bridge.monitorMetrics!r}"
        )
        assert bridge.monitorVisible is True, "采集失败不该影响窗口"
        assert "psutil" in bridge.monitorMetrics["error"]
    finally:
        bridge.shutdown()


def test_metrics_refresh_is_not_reentrant() -> None:
    """上一轮还没回来时不该再提交一轮（`collect()` 是阻塞式的，堆请求会积压）。"""
    pending: List[Any] = []
    bridge = make_bridge(metrics_provider=lambda: {"cpu_percent": 1}, executor=pending.append)
    try:
        bridge.refreshMetrics()
        bridge.refreshMetrics()
        assert len(pending) == 1, "第二次 refresh 应被 inflight 挡住"
        pending[0]()  # 手动跑完
        bridge.refreshMetrics()
        assert len(pending) == 2, "上一轮结束后可以再来一轮"
    finally:
        bridge.shutdown()


def test_metrics_provider_default_is_lazy_and_never_touches_psutil_here() -> None:
    """不注入 provider 时桥也只是**记着**要用服务，不在构造期 import 重依赖。"""
    bridge = ob.OverlayBridge(config=FakeConfig(), screen_provider=lambda: fake_screen())
    try:
        assert bridge._metrics_provider is None, "构造期不该建采集器（懒加载）"
        assert bridge.monitorMetrics == {}
    finally:
        bridge.shutdown()


# ─── 9. 热键接线 ─────────────────────────────────────────────────


def test_bind_hotkeys_maps_monitor_toggle(bridge: ob.OverlayBridge) -> None:
    hotkeys = FakeHotkeys()
    assert bridge.bind_hotkeys(hotkeys) is True
    hotkeys.triggered.emit("monitor_toggle")
    assert bridge.monitorVisible is True, "Ctrl+Shift+M 就是 toggleMonitor"
    hotkeys.triggered.emit("play_pause")  # 别人的热键不该动悬浮窗
    assert bridge.monitorVisible is True
    hotkeys.triggered.emit("monitor_toggle")
    assert bridge.monitorVisible is False


def test_bind_hotkeys_is_idempotent(bridge: ob.OverlayBridge) -> None:
    """重复接线会让一次按键切两次（等于没反应）—— 必须幂等。"""
    hotkeys = FakeHotkeys()
    assert bridge.bind_hotkeys(hotkeys) is True
    assert bridge.bind_hotkeys(hotkeys) is True
    hotkeys.triggered.emit("monitor_toggle")
    assert bridge.monitorVisible is True


def test_bind_hotkeys_rejects_objects_without_the_signal(bridge: ob.OverlayBridge) -> None:
    assert bridge.bind_hotkeys(None) is False
    assert bridge.bind_hotkeys(object()) is False


# ─── 10. 诊断快照 ────────────────────────────────────────────────


def test_describe_shape() -> None:
    cfg = FakeConfig()
    bridge = make_bridge(config=cfg, screen=fake_screen(dpr=1.25))
    try:
        bridge.showLyric()
        bridge.reportPosition("monitor", 5, 6)
        info = bridge.describe()
        assert info["platform"] == "offscreen"
        assert info["platform_supported"] is False
        assert info["dpr"] == pytest.approx(1.25)
        assert info["visible"]["lyric"] is True and info["visible"]["monitor"] is False
        assert info["positions"]["monitor"] == [5, 6]
        assert info["props"]["monitor"]["width"] == 400
        assert info["config_field"] == "overlay_geometry"
        assert info["hotkeys_bound"] is False
        assert set(info) >= {
            "platform", "platform_supported", "dpr", "screen", "screen_logical",
            "screen_physical", "visible", "positions", "props", "windows",
            "metrics_keys", "metrics_refreshes", "metrics_running", "hotkeys_bound",
            "config_field",
        }
    finally:
        bridge.shutdown()


def test_bind_uses_the_context_config() -> None:
    """`bind(context)` 是入口的装配钩子：位置持久化沿用 AppContext 的 config。

    构造时**不注入**配置（模拟 `main_qml` 的 `OverlayBridge()`），`bind` 必须把
    上下文里的配置接管过来 —— 否则会写到另一份配置上（阶段 2 实测踩过这个坑）。
    """

    class Ctx:
        def __init__(self) -> None:
            self.config = FakeConfig()

    ctx = Ctx()
    bridge = ob.OverlayBridge(screen_provider=lambda: fake_screen(), executor=lambda fn: fn())
    try:
        bridge.bind(ctx)
        bridge.reportPosition("monitor", 7, 8)
        bridge.savePosition()
        assert ctx.config.saves == 1, "位置必须落到上下文那份配置上"
        assert ctx.config.overlay_geometry["monitor"]["x"] == 7
    finally:
        bridge.shutdown()


def test_bind_does_not_override_an_injected_config() -> None:
    """显式注入的配置**不会**被 `bind` 覆盖（测试注入假配置时的安全阀）。"""

    class Ctx:
        def __init__(self) -> None:
            self.config = FakeConfig()

    injected, ctx = FakeConfig(), Ctx()
    bridge = ob.OverlayBridge(config=injected, screen_provider=lambda: fake_screen())
    try:
        bridge.bind(ctx)
        bridge.reportPosition("monitor", 7, 8)
        bridge.savePosition()
        assert injected.saves == 1 and ctx.config.saves == 0
    finally:
        bridge.shutdown()


def test_bind_tolerates_a_context_without_config() -> None:
    """拿不到配置也要能建起来（位置退化成"只在内存里"）。"""
    bridge = ob.OverlayBridge(config=FakeConfig(), screen_provider=lambda: fake_screen())
    try:
        bridge.bind(object())
        assert bridge.geometryFor("monitor")["source"] == "default"
        assert bridge.bind(None) is None
    finally:
        bridge.shutdown()



