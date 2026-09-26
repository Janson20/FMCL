"""两个悬浮窗 QML 的回归守卫（阶段 2 任务 2.15）。

## 这一组测试要钉住什么

1. **窗口语义真的落到窗口上**（不是"QML 里写了 flags 就算数"）：`flags()` 里必须有
   `FramelessWindowHint` 与 `WindowStaysOnTopHint`，`opacity()` 必须等于配置值，
   **窗口底色必须是不透光的实色**（`alpha == 255`）—— 这条正是闸门 R6 想守的语义；
2. **阶段 0 的"不抢焦点"顺序**：`applyNoActivate()` 被调用时窗口**还没可见**
   （QML 用 hostReady 两段式可见性保证这一点）。真实桌面上"补丁确实让窗口不抢焦点"
   **无法在本环境自动化验证**（需要真 HWND 与鼠标），报告里如实列为未验证；
3. **拖拽 = 增量累加 + 只在结束时落盘**：QTest 合成拖拽读回坐标差必须**精确等于**鼠标位移；
   拖动过程中 `reportPosition` 每帧都报，而 `savePosition` 只在**真的拖过**时被调一次；
4. **锁定态**：锁上之后拖拽不改变坐标（与未锁定时形成对照，证明断言不是空的）；
5. **锁定图标是 SVG 而不是 emoji**（D-07）：读回 `Image.source` 断言它是 `lock.svg` /
   `unlock.svg`。

## 环境纪律

* `QT_QPA_PLATFORM=offscreen` 在 import PySide6 **之前**设好；
* offscreen 下 `MouseEvent.scenePosition` 是 undefined（阶段 0 实测），所以拖拽用
  `mouse.x` / `mouse.y` 局部坐标；**不使用** `Window.startSystemMove()`（无法自动化验证）；
* 不引第三方测试依赖（没有 pytest-qt）：自己建 `QGuiApplication` + `QTest.qWait`；
* 每个用例都装一个 Qt 消息收集器：QML 的绑定错误默认只打 stderr，不收集就会
  "测试全绿但界面是坏的"。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path  # noqa: E402
import tempfile  # noqa: E402
from typing import Any, Dict, List, Optional, Tuple  # noqa: E402

import pytest  # noqa: E402
from PySide6.QtCore import QObject, QPoint, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QColor, QGuiApplication  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.bridges import overlay_bridge as ob  # noqa: E402
from app.bridges import theme_bridge as tb  # noqa: E402
from app.bridges.runtime_bridge import RuntimeBridge  # noqa: E402
from app.bridges.tr_bridge import TrBridge  # noqa: E402
from services import palette  # noqa: E402
from services.theme_service import ThemeEngine  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
MONITOR_QML = REPO_ROOT / "qml" / "overlays" / "MonitorOverlay.qml"
LYRIC_QML = REPO_ROOT / "qml" / "overlays" / "DesktopLyricOverlay.qml"
LOCALES = REPO_ROOT / "ui" / "locales"

MONITOR_W, MONITOR_H = 400, 200
LYRIC_W, LYRIC_H = 600, 140
#: 假屏幕（逻辑像素）；offscreen 的真屏幕是 800x800，这里刻意换成 800x600 让
#: "右上角/下方居中"两类默认位置算得出来且各处不同
SCREEN = ob.ScreenInfo(name="FAKE-SCREEN", dpr=1.0, logical=(0, 0, 800, 600), physical=(0, 0, 800, 600))
METRICS = {"cpu_percent": 42.0, "cpu_freq": "3420 MHz", "mem_percent": 61.0, "mem_used": "9.8 GB",
           "mem_total": "16.0 GB", "gpu_util": "18%", "gpu_temp": "54 C", "gpu_mem": "1.2 GB / 8.0 GB"}


def _qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


_APP = _qapp()

#: 强引用：Qt 的上下文属性与引擎都不接管所有权，被 GC 掉之后 QML 侧会拿到空壳
_KEEP_ALIVE: List[Any] = []


@pytest.fixture(autouse=True)
def pristine_palette() -> Any:
    """用例前后还原全局 `COLORS`（`ThemeBridge` 会改这个全进程唯一的字典）。"""
    saved = dict(palette.COLORS)
    try:
        yield
    finally:
        palette.COLORS.clear()
        palette.COLORS.update(saved)


class _ConfigStub:
    """给 Theme / Tr 桥用的假配置（**不碰**真实 config.json）。"""

    def __init__(self) -> None:
        self.language = "zh_CN"
        self.theme_name = "default"
        self.accent_color = None
        self.overlay_geometry: Dict[str, Any] = {}
        self.saves = 0

    def save_config(self) -> None:
        self.saves += 1


class _Messages:
    """Qt 消息收集器：QML 的绑定错误默认只打 stderr，收集了才断言得了。"""

    #: 这些字样出现就说明 QML 里有真的问题（绑定失败 / 变量名写错 / 赋值类型不对）
    BAD = ("is not defined", "Unable to assign", "TypeError", "ReferenceError", "Cannot assign", "is not a function")

    def __init__(self) -> None:
        self.lines: List[str] = []

    def handler(self, mode: Any, context: Any, message: str) -> None:  # noqa: ANN001
        self.lines.append(str(message))

    def problems(self) -> List[str]:
        return [line for line in self.lines if any(bad in line for bad in self.BAD)]


class RecordingOverlay(ob.OverlayBridge):
    """记录型 Overlay：把"调补丁时窗口是否已经可见""落盘次数""位置回报"记下来。

    必须是**子类**而不是"给实例换个属性"：QML 通过元对象系统调槽，
    实例属性上的替身根本不会被调用（那样测试会静默地什么都没测到）。
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.apply_calls: List[Tuple[str, bool]] = []
        self.save_calls = 0
        self.reported: List[Tuple[str, int, int]] = []

    def applyNoActivate(self, kind: str) -> bool:  # noqa: N802 - QML 槽名
        window = self._find_window(kind)
        self.apply_calls.append((kind, bool(window is not None and window.isVisible())))
        return super().applyNoActivate(kind)

    def savePosition(self) -> None:  # noqa: N802
        self.save_calls += 1
        super().savePosition()

    def reportPosition(self, kind: str, x: int, y: int) -> None:  # noqa: N802
        self.reported.append((kind, int(x), int(y)))
        super().reportPosition(kind, x, y)


class Harness:
    """一套装配好的 QML 环境：真 Theme / Tr / Runtime 桥 + 记录型 Overlay 桥。"""

    def __init__(self, geometry: Optional[Dict[str, Any]] = None) -> None:
        self.engine = QQmlApplicationEngine()
        self.config = _ConfigStub()
        self.config.overlay_geometry = dict(geometry or {})
        self.theme_dir = tempfile.mkdtemp(prefix="fmcl_2_15_theme_")
        self.overlay = RecordingOverlay(
            config=self.config,
            screen_provider=lambda: SCREEN,
            metrics_provider=lambda: dict(METRICS),
            executor=lambda fn: fn(),  # 同步执行：指标测试不需要真线程
        )
        self.theme = tb.ThemeBridge(
            engine=self.engine, theme_engine=ThemeEngine(self.theme_dir), config=self.config,
        )
        self.tr = TrBridge(config=self.config, locales=LOCALES)
        self.runtime = RuntimeBridge()
        self.messages = _Messages()
        self.windows: List[Any] = []
        context = self.engine.rootContext()
        for name, obj in (
            ("Theme", self.theme), ("Tr", self.tr), ("Runtime", self.runtime), ("Overlay", self.overlay),
        ):
            context.setContextProperty(name, obj)
        _KEEP_ALIVE.extend([self.engine, self.overlay, self.theme, self.tr, self.runtime])

    # ── 装载 QML ──
    def load(self, qml_path: Path, object_name: str) -> Any:
        before = len(self.engine.rootObjects())
        self.engine.load(QUrl.fromLocalFile(str(qml_path)))
        roots = self.engine.rootObjects()
        assert len(roots) == before + 1, (
            f"QML 没建出窗口：{qml_path}\nQML 消息：{self.messages.problems() or self.messages.lines}"
        )
        window = roots[-1]
        assert window.objectName() == object_name, (
            f"objectName 必须是 {object_name}（桥按它兜底找窗口），实际 {window.objectName()!r}"
        )
        self.windows.append(window)
        QTest.qWait(60)  # 至少渲染一帧
        self.assert_clean()
        return window

    def assert_clean(self) -> None:
        problems = self.messages.problems()
        assert not problems, "QML 报了错（绑定/类型/未定义变量）：\n" + "\n".join(problems)

    def find(self, window: Any, object_name: str) -> Any:
        found = window.findChildren(QObject, object_name)
        assert found, f"窗口里找不到 objectName={object_name} 的子项"
        return found[0]

    def close(self) -> None:
        self.overlay.shutdown()
        for window in self.windows:
            window.setVisible(False)
            window.deleteLater()
        self.windows.clear()
        QTest.qWait(20)


@pytest.fixture
def harness() -> Any:
    made = Harness()
    try:
        yield made
    finally:
        made.close()


@pytest.fixture
def saved_harness() -> Any:
    """带"上次退出时存下来的位置"的一套环境（模拟重启后恢复）。"""
    made = Harness(geometry={
        "monitor": {"x": 123, "y": 45, "width": 400, "height": 200, "opacity": 0.8},
        "lyric": {"x": 77, "y": 88, "width": 600, "height": 140, "opacity": 0.9},
    })
    try:
        yield made
    finally:
        made.close()


def drag(window: Any, press: QPoint, moves: List[QPoint]) -> Tuple[int, int]:
    """QTest 合成一次左键拖拽，返回拖拽前后的坐标差。

    **前提：窗口必须已经可见**。隐藏窗口收不到合成的移动事件（实测：不显示就拖，
    位移恒为 (0,0)），少了这条前置断言会得到"看起来很对但其实什么都没发生"的假绿。
    """
    assert window.isVisible(), "拖拽前必须先把窗口显示出来"
    before = (window.x(), window.y())
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, press)
    QTest.qWait(50)
    for point in moves:
        QTest.mouseMove(window, point, 20)
        QTest.qWait(50)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, moves[-1])
    QTest.qWait(80)
    return (window.x() - before[0], window.y() - before[1])


def click(window: Any, at: QPoint) -> None:
    """QTest 合成一次没有位移的点击（按下 + 原地抬起）。"""
    QTest.mousePress(window, Qt.LeftButton, Qt.NoModifier, at)
    QTest.qWait(40)
    QTest.mouseRelease(window, Qt.LeftButton, Qt.NoModifier, at)
    QTest.qWait(80)


def _url_text(value: Any) -> str:
    """QML 的 `source` 属性读回来是 `QUrl`（不是 str），统一转成字符串再断言。"""
    return value.toString() if hasattr(value, "toString") else str(value)


# ─── 1. 监控窗：窗口语义 ─────────────────────────────────────────


def _flags_ok(window: Any) -> None:
    flags = window.flags()
    assert flags & Qt.FramelessWindowHint, f"没有 FramelessWindowHint（阶段 0 决策 2）: {int(flags)}"
    assert flags & Qt.WindowStaysOnTopHint, f"没有 WindowStaysOnTopHint: {int(flags)}"
    assert flags & Qt.WindowDoesNotAcceptFocus, f"没有 WindowDoesNotAcceptFocus: {int(flags)}"


def test_monitor_window_flags_opacity_and_solid_color(harness: Any) -> None:
    window = harness.load(MONITOR_QML, "monitorOverlay")
    _flags_ok(window)
    assert (window.width(), window.height()) == (MONITOR_W, MONITOR_H), (
        "尺寸必须精确等于请求值 —— FluWindow 会按 appBar 高度把 200 撑成 230（阶段 0 第 11.1 节）"
    )
    assert window.opacity() == pytest.approx(0.80), "整窗不透明度（等价旧 Tk 的 attributes -alpha）"
    color = window.property("color")
    assert isinstance(color, QColor)
    assert color.alpha() == 255, f"窗口底色必须是不透光的实色（闸门 R6 的语义）: {color.name(QColor.HexArgb)}"
    assert color != QColor(Qt.transparent)
    expected = harness.theme.bgDark
    assert (color.red(), color.green(), color.blue()) == (expected.red(), expected.green(), expected.blue()), (
        "颜色只能来自 Theme.*（闸门 R8）"
    )


def test_monitor_window_starts_hidden_and_becomes_visible_through_the_bridge(harness: Any) -> None:
    window = harness.load(MONITOR_QML, "monitorOverlay")
    assert window.isVisible() is False, "建窗时不能可见（hostReady 两段式可见性）"
    assert harness.overlay.apply_calls == [("monitor", False)], (
        f"补丁必须在窗口可见**之前**打（阶段 0 第 11.4 节）: {harness.overlay.apply_calls}"
    )
    harness.overlay.showMonitor()
    QTest.qWait(60)
    assert window.isVisible() is True
    harness.overlay.hideMonitor()
    QTest.qWait(60)
    assert window.isVisible() is False
    harness.overlay.toggleMonitor()
    QTest.qWait(60)
    assert window.isVisible() is True
    # 每次重新显示都会再补一次（幂等），用来覆盖"建窗时就已经可见"的边角
    assert len(harness.overlay.apply_calls) >= 2, harness.overlay.apply_calls


def test_monitor_default_position_is_the_top_right_corner(harness: Any) -> None:
    window = harness.load(MONITOR_QML, "monitorOverlay")
    assert (window.x(), window.y()) == (800 - MONITOR_W - 10, 50), (
        f"默认右上角（旧 ui/app_monitor.py:68 的规则）: {(window.x(), window.y())}"
    )
    assert harness.overlay.positionFor("monitor")["source"] == "default"


def test_persisted_position_is_restored_on_creation(saved_harness: Any) -> None:
    window = saved_harness.load(MONITOR_QML, "monitorOverlay")
    assert (window.x(), window.y()) == (123, 45), f"建窗要用持久化位置: {(window.x(), window.y())}"
    assert saved_harness.overlay.positionFor("monitor")["source"] == "restored"
    assert window.opacity() == pytest.approx(0.8)


def test_monitor_opacity_and_size_follow_configure(harness: Any) -> None:
    window = harness.load(MONITOR_QML, "monitorOverlay")
    assert harness.overlay.configure("monitor", {"opacity": 0.5, "width": 320, "height": 180}) is True
    QTest.qWait(60)
    assert window.opacity() == pytest.approx(0.5), "QML 的 opacity 绑定要跟着 configure 走"
    assert (window.width(), window.height()) == (320, 180)
    harness.assert_clean()


def test_monitor_metrics_are_rendered(harness: Any) -> None:
    """指标行显示的是服务返回值（桥不加工），QML 侧的绑定真的生效。"""
    harness.overlay.showMonitor()
    QTest.qWait(200)  # 让 flush 定时器把指标发出来
    harness.load(MONITOR_QML, "monitorOverlay")
    QTest.qWait(60)
    text = harness.find(harness.windows[-1], "monitorCpuValue").property("text")
    assert "42" in text and "3420" in text, f"CPU 行应当显示服务返回的数值: {text!r}"
    assert harness.overlay.monitorMetrics["mem_percent"] == 61.0
    harness.assert_clean()


# ─── 2. 监控窗：拖拽（增量累加 + 只在结束时落盘） ────────────────


def test_drag_moves_the_window_by_exactly_the_pointer_delta(harness: Any) -> None:
    harness.overlay.showMonitor()
    window = harness.load(MONITOR_QML, "monitorOverlay")
    QTest.qWait(60)
    delta = drag(window, QPoint(200, 100), [QPoint(240, 130), QPoint(280, 140)])
    assert delta == (80, 40), (
        f"拖拽位移必须精确等于鼠标位移（增量累加，阶段 0 决策 4）: {delta}"
    )
    assert harness.overlay.save_calls == 1, "拖拽结束时落盘一次"
    assert len(harness.overlay.reported) >= 2, (
        f"拖动过程中要每帧回报位置（桥只更新内存）: {harness.overlay.reported}"
    )
    assert harness.overlay.reported[-1] == ("monitor", window.x(), window.y())


def test_click_without_movement_does_not_save(harness: Any) -> None:
    """没有位移的点击不是拖拽：不该落盘（否则每次点一下都会写配置）。"""
    harness.overlay.showMonitor()
    window = harness.load(MONITOR_QML, "monitorOverlay")
    QTest.qWait(60)
    before = (window.x(), window.y())
    click(window, QPoint(200, 100))
    assert (window.x(), window.y()) == before
    assert harness.overlay.save_calls == 0, "纯点击不该写盘"


def test_drag_then_save_then_restore_brings_the_window_back(harness: Any) -> None:
    """端到端：拖走 -> 落盘 -> restorePosition() -> 回到落盘时的位置。"""
    harness.overlay.showMonitor()
    window = harness.load(MONITOR_QML, "monitorOverlay")
    QTest.qWait(60)
    drag(window, QPoint(200, 100), [QPoint(260, 160)])
    saved = (window.x(), window.y())
    assert harness.config.overlay_geometry["monitor"]["x"] == saved[0], "落盘内容要等于当前坐标"
    drag(window, QPoint(200, 100), [QPoint(120, 60)])  # 再拖走（也会落盘）
    harness.overlay.restorePosition()  # 读配置 -> 推回窗口
    QTest.qWait(60)
    assert (window.x(), window.y()) == (harness.overlay.geometryFor("monitor")["x"],
                                        harness.overlay.geometryFor("monitor")["y"]), (
        "restorePosition 之后窗口坐标必须等于桥里的位置"
    )
    assert harness.overlay.geometryFor("monitor")["source"] == "restored"
    harness.assert_clean()


def test_synthetic_drag_uses_local_coordinates_not_scene_position(harness: Any) -> None:
    """offscreen 下 `MouseEvent.scenePosition` 是 undefined（阶段 0 实测）——

    所以拖拽只依赖 `mouse.x/mouse.y`。这条把"换了坐标系也会过"的写法钉住：
    拖拽区铺满整窗且原点在 (0,0)，局部坐标即窗口内容坐标。
    """
    window = harness.load(MONITOR_QML, "monitorOverlay")
    window.setX(1000)
    harness.overlay.showMonitor()
    QTest.qWait(60)
    delta = drag(window, QPoint(10, 10), [QPoint(30, 25)])
    assert delta == (20, 15), f"窗口换位置后位移仍然是纯增量: {delta}"


# ─── 3. 歌词窗：窗口语义 + 两行歌词 + 锁定 ───────────────────────


def test_lyric_window_flags_opacity_and_bottom_center_default(harness: Any) -> None:
    window = harness.load(LYRIC_QML, "lyricOverlay")
    _flags_ok(window)
    assert (window.width(), window.height()) == (LYRIC_W, LYRIC_H)
    assert window.opacity() == pytest.approx(0.85), "旧实现是 attributes -alpha 0.85"
    assert window.property("color").alpha() == 255, "实色底（不许 transparent）"
    # 下方居中：x = (屏幕宽 - 窗宽) / 2，y = 屏幕高 - 窗高 - 80（旧实现的规则）
    assert (window.x(), window.y()) == ((800 - LYRIC_W) // 2, 600 - LYRIC_H - 80)
    assert harness.overlay.apply_calls == [("lyric", False)], (
        f"补丁必须在窗口可见之前打: {harness.overlay.apply_calls}"
    )
    harness.assert_clean()


def test_lyric_window_shows_two_lines_with_i18n_placeholders(harness: Any) -> None:
    harness.overlay.showLyric()
    window = harness.load(LYRIC_QML, "lyricOverlay")
    QTest.qWait(60)
    assert window.isVisible() is True
    current = harness.find(window, "lyricCurrentLine").property("text")
    loading = harness.tr.map["music_lyric_loading"]
    assert current == loading, f"没有歌词时第一行显示 i18n 占位（R3：不许硬编码中文）: {current!r}"
    assert harness.find(window, "lyricNextLine").property("text") == "", "第二行等歌词服务推送"
    # 歌词文本推过来之后直接显示（桥不解析歌词，界面也不加工）
    window.setProperty("currentLine", "第一句歌词")
    window.setProperty("nextLine", "第二句歌词")
    QTest.qWait(40)
    assert harness.find(window, "lyricCurrentLine").property("text") == "第一句歌词"
    assert harness.find(window, "lyricNextLine").property("text") == "第二句歌词"
    harness.assert_clean()


def test_lyric_drag_moves_the_window(harness: Any) -> None:
    harness.overlay.showLyric()
    window = harness.load(LYRIC_QML, "lyricOverlay")
    QTest.qWait(60)
    delta = drag(window, QPoint(300, 100), [QPoint(350, 120)])
    assert delta == (50, 20), f"未锁定时拖拽生效: {delta}"
    assert harness.overlay.save_calls == 1


def test_lyric_lock_blocks_dragging(harness: Any) -> None:
    """锁定后拖拽不改变坐标 —— 与未锁定形成对照，证明拖拽断言不是空的。"""
    harness.overlay.showLyric()
    window = harness.load(LYRIC_QML, "lyricOverlay")
    QTest.qWait(60)
    assert harness.overlay.toggleLyricLock() is True
    QTest.qWait(60)
    before = (window.x(), window.y())
    drag(window, QPoint(300, 100), [QPoint(420, 180), QPoint(500, 80)])
    assert (window.x(), window.y()) == before, "锁定态下拖拽区整体失效"
    assert harness.overlay.save_calls == 0, "没拖动就不该落盘"

    assert harness.overlay.toggleLyricLock() is False
    QTest.qWait(60)
    delta = drag(window, QPoint(300, 100), [QPoint(340, 130)])
    assert delta == (40, 30), f"解锁后拖拽恢复: {delta}"


def test_lyric_lock_button_uses_an_svg_icon_instead_of_an_emoji(harness: Any) -> None:
    """D-07：旧实现用锁形 emoji，新界面必须是 `qml/assets/icons` 下的 SVG。"""
    window = harness.load(LYRIC_QML, "lyricOverlay")
    icon = harness.find(window, "lyricLockIcon")
    unlocked = _url_text(icon.property("source"))
    assert unlocked.endswith("unlock.svg"), f"未锁定时应当是 unlock.svg: {unlocked!r}"
    assert all(ord(ch) < 0x2000 for ch in unlocked), "图标 URL 里不许出现 emoji"

    harness.overlay.toggleLyricLock()
    QTest.qWait(60)
    locked = _url_text(harness.find(window, "lyricLockIcon").property("source"))
    assert locked.endswith("lock.svg"), f"锁定后应当是 lock.svg: {locked!r}"
    assert locked != unlocked
    harness.assert_clean()


def test_lyric_opacity_buttons_go_through_the_bridge(harness: Any) -> None:
    """+/- 按钮改的是桥里的不透明度（步长/边界在服务层），窗口 opacity 跟着走。"""
    window = harness.load(LYRIC_QML, "lyricOverlay")
    button = harness.find(window, "lyricOpacityDown")
    center = QPoint(int(button.property("x") + button.property("width") / 2),
                    int(button.property("y") + button.property("height") / 2))
    click(window, center)
    assert harness.overlay.lyricProps["opacity"] == pytest.approx(0.80), "0.85 - 0.05"
    QTest.qWait(40)
    assert window.opacity() == pytest.approx(0.80)
    harness.assert_clean()


def test_lyric_close_button_hides_the_window(harness: Any) -> None:
    harness.overlay.showLyric()
    window = harness.load(LYRIC_QML, "lyricOverlay")
    QTest.qWait(60)
    assert window.isVisible() is True
    button = harness.find(window, "lyricCloseButton")
    center = QPoint(int(button.property("x") + button.property("width") / 2),
                    int(button.property("y") + button.property("height") / 2))
    click(window, center)
    QTest.qWait(60)
    assert window.isVisible() is False, "点关闭按钮应当隐藏歌词窗（走 Overlay.hideLyric）"
    harness.assert_clean()


def test_lyric_restores_saved_geometry_and_lock_defaults_to_unlocked(saved_harness: Any) -> None:
    window = saved_harness.load(LYRIC_QML, "lyricOverlay")
    assert (window.x(), window.y()) == (77, 88), "持久化位置要生效"
    assert window.opacity() == pytest.approx(0.9), "持久化的不透明度也要生效"
    assert saved_harness.overlay.lyricLocked is False, "锁定态**不**持久化（与旧实现一致）"


# ─── 4. 负例自检：断言不是空的 ───────────────────────────────────


_PROBE_QML = b"""
import QtQuick
import QtQuick.Window

Window {
    objectName: "negativeProbe"
    width: 100
    height: 100
    visible: true
    opacity: 1.0
    color: "transparent"
}
"""


def test_negative_probe_window_shows_the_assertions_can_go_red(harness: Any) -> None:
    """负例自检（窗口侧）：故意造一个"错的"窗口，证明上面那些断言真的在看东西。

    * 没有 `flags` -> `FramelessWindowHint` / `WindowStaysOnTopHint` 都是假的；
    * `color: "transparent"` -> alpha 为 0（这正是闸门 R6 要拦的写法）；
    * 尺寸故意给 100x100 -> 与悬浮窗的 400x200 不同。
    如果这些差别读不出来，"窗口语义已生效"的断言就都是空话。
    """
    component = QQmlComponent(harness.engine)
    component.setData(_PROBE_QML, QUrl.fromLocalFile(str(REPO_ROOT / "qml" / "overlays" / "_negative_probe.qml")))
    assert component.isReady(), [e.toString() for e in component.errors()]
    probe = component.create()
    assert probe is not None
    try:
        flags = probe.flags()
        assert not (flags & Qt.FramelessWindowHint), "探针窗口本来就没有 Frameless，说明这条判据可红"
        assert not (flags & Qt.WindowStaysOnTopHint)
        assert probe.property("color").alpha() == 0, "transparent 的 alpha 是 0（R6 拦的就是它）"
        assert (probe.width(), probe.height()) == (100, 100)
        assert probe.objectName() == "negativeProbe"
    finally:
        probe.deleteLater()
        QTest.qWait(20)


