"""悬浮窗桥（阶段 2 任务 2.15）—— 一个桥管三个窗口：性能监控 / 桌面歌词 / Toast 宿主。

## 为什么三个窗口共用一个桥

它们的**窗口语义完全一样**：无边框 + 置顶 + 整窗不透明度 + 不抢焦点 + 增量拖拽 +
位置持久化；差别只在内容（监控指标 / 歌词 / Toast 卡片）。所以差异部分留给 QML，
共同的窗口语义与持久化只写一份。

## 这个桥**不**做什么（迁移红线 2）

* 不算指标：CPU/内存/GPU 全部来自 `services/monitor_service.MetricsCollector`，
  桥只把它的返回字典原样发给 QML（`monitorMetrics`）；
* 不解析歌词：歌词行由 `services/music_lyric_display` / `services/music_lyrics` 产出，
  桥只暴露窗口；透明度步长与边界来自 `services.desktop_lyric`（`ALPHA_STEP` /
  `ALPHA_MIN` / `ALPHA_MAX`），锁定态翻转来自 `services.desktop_lyric.toggle_lock`；
* 不做窗口摆放的数学：桌面歌词的"屏幕下方居中"直接调
  `services.desktop_lyric.calc_center_bottom_position`，不在这里重算。

## 从阶段 0 的 PoC 搬进来的东西（逐条，来源 `poc/overlay/`）

| PoC 位置 | 本模块 | 说明 |
|----------|--------|------|
| `overlay_common.py:apply_no_activate` | `apply_no_activate()` | Win32 补丁，**顺序原样**：`winId()` → `GWL_EXSTYLE\\|=WS_EX_NOACTIVATE` → `SetWindowPos(SWP_FRAMECHANGED\\|SWP_NOACTIVATE)` |
| `overlay_common.py` 的 user32 签名声明 | `_load_user32()` | 64 位下 HWND 必须按指针传，不能当 32 位 int 截断 |
| `overlay_common.py:is_windows_platform` | `native_patch_supported()` | 判据收窄成"**真 HWND + Win32 user32**"两个条件（见下） |
| `toast_manager.py` 的 280x72 / 16px 内缩 | `DEFAULT_PROPS["toast"]` / 默认位置 | Toast 宿主的默认尺寸与右下角内缩 |
| `qml/WindowDragArea.qml` 的增量累加 | 两个 overlay QML 的拖拽区 | 行为搬进 QML（不允许新增 components 文件，见报告） |

`poc/` 是**过程产物、不入库**，所以这里是**搬迁**而不是 import —— 生产代码不依赖 `poc.*`。

## 坐标与 DPI（阶段 0 第 12.4 节，本机 dpr=1.25）

两套坐标同时存在，**必须明确用哪一套**：

| 坐标系 | 来源 | 谁在用 |
|--------|------|--------|
| **逻辑像素** | `QScreen.geometry()` / QML `Window.x/y/width/height` | 窗口定位、拖拽、`geometryFor()`、**位置持久化**（恢复时直接喂给 `Window.x/y`） |
| **物理像素** | Win32 窗口矩形 / `GetCursorPos` | 与系统坐标打交道的地方（读回窗口真实矩形、真实光标、跨进程对齐） |

换算：`physical = 屏幕物理原点 + (logical - 屏幕逻辑原点) * dpr`，逆变换同理。
物理原点当前按 `逻辑原点 * dpr` 得到 —— 这在**单一 dpr** 下与阶段 0 实测一致
（逻辑 2048x1152、dpr 1.25 → 物理 2560x1440），但**混合 DPI 多屏下不成立**（见"未验证"。）

**持久化只写逻辑像素**：物理像素落盘后一旦 dpr 变化（用户改缩放）就会错位。

## 平台差异

* **Windows**（QPA `windows`）：`platformSupported == True`。无边框/置顶/整窗透明度
  三件套在阶段 0 被 Win32 + 像素级双重验证（第 11、12 节，47/47）；
  另外必须补 `WS_EX_NOACTIVATE`，否则点一下悬浮窗就会把全屏游戏踢出前台。
* **Linux / macOS**：`platformSupported == False` —— 不是"用不了"，而是**未实测**。
  降级路径：`applyNoActivate()` 直接返回 `False`（Linux 侧由 Qt 自己处理 X11 input hint），
  无边框/置顶交给 Qt 的平台实现，`Window.opacity` 在**无合成器的 X11 下可能被忽略**
  （Wayland 下置顶也由合成器决定）。QML 侧不需要写平台分支：窗口语义全靠 `flags` 与
  `opacity`，两个平台走同一份 QML。
* **offscreen**（CI / 测试）：`platformSupported == False`，因为**没有真 HWND**，
  给假句柄写 `WS_EX_NOACTIVATE` 是没有意义的。这条判据让"降级分支"能在离屏下被真断言。
"""

from __future__ import annotations

import logging
import math
import sys
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from PySide6.QtCore import Property, QObject, QRunnable, QThreadPool, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

logger = logging.getLogger(__name__)

#: 上下文属性名（`main_qml.py` 的 `CONTEXT_BRIDGES` 里冻结为 `Overlay`）。
QML_NAME = "Overlay"

#: 三种窗口的 kind（QML 侧所有调用都带它）。
KINDS: Tuple[str, ...] = ("monitor", "lyric", "toast")

#: QML 悬浮窗的 `objectName` 约定：`setupWindow()` 没被调用时的兜底查找依据。
OBJECT_NAMES: Dict[str, str] = {
    "monitor": "monitorOverlay",
    "lyric": "lyricOverlay",
    "toast": "toastOverlay",
}

#: 配置字段名（`config.py` 的 `Config.overlay_geometry`）—— 唯一的位置存储。
CONFIG_FIELD = "overlay_geometry"

#: 全局热键名（`app/bridges/hotkey_bridge.py:LEGACY_HOTKEYS` 里 `ctrl+shift+m` 那一条）。
HOTKEY_MONITOR_TOGGLE = "monitor_toggle"

#: 指标刷新节奏（毫秒）。与旧界面 `PerformanceMonitorWindow` 的 1 秒刷新一致
#: （`services/monitor_service._REFRESH_INTERVAL = 1.0`）。
DEFAULT_METRICS_INTERVAL_MS = 1000

#: 指标从 worker 取回后合并发出的节流周期（毫秒）—— 与 `qt_tasks.TaskBridge` 同一套路。
METRICS_FLUSH_MS = 100

#: 默认窗口属性。数值来源逐个标注，避免"拍脑袋的默认值"：
#: * monitor：旧 `ui/app_monitor.py` 写死 400x200 / `attributes("-alpha", 0.80)`；
#: * lyric：`services/desktop_lyric.py` 的 `DEFAULT_WINDOW_WIDTH/HEIGHT/DEFAULT_ALPHA`；
#: * toast：阶段 0 PoC `poc/overlay/toast_manager.py` 的 `TOAST_W=280` / `TOAST_H=72`
#:   （透明度由 Toast 自己的淡入淡出动画控制，所以默认 1.0）。
DEFAULT_PROPS: Dict[str, Dict[str, Any]] = {
    "monitor": {"opacity": 0.80, "width": 400, "height": 200, "topMost": True, "locked": False},
    "lyric": {"opacity": 0.85, "width": 600, "height": 140, "topMost": True, "locked": False},
    "toast": {"opacity": 1.00, "width": 280, "height": 72, "topMost": True, "locked": False},
}

#: `configure()` 允许的属性键（其余一律拒绝并报 `overlayFailed`）。
CONFIG_KEYS: Tuple[str, ...] = ("opacity", "width", "height", "x", "y", "topMost", "locked")

# ─── Win32 常量 ────────────────────────────────────────────────

GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
SWP_NOMOVE = 0x0001
SWP_NOSIZE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020


def _qpa_platform_name() -> str:
    """当前 QPA 平台插件名（`windows` / `xcb` / `wayland` / `offscreen` / `""`）。"""
    try:
        return str(QGuiApplication.platformName() or "")
    except Exception:  # noqa: BLE001 - 探测平台绝不能抛
        return ""


def native_patch_supported() -> bool:
    """Win32 原生补丁（`WS_EX_NOACTIVATE`）是否可用。

    **两个条件都要满足**：

    1. `sys.platform == "win32"` —— 操作系统是 Windows；
    2. QPA 平台插件是 `windows` —— 窗口才有**真 HWND**。

    第 2 条是被阶段 0 之外的场景逼出来的：`QT_QPA_PLATFORM=offscreen` 下
    `QWindow.winId()` 返回的是一个假句柄（实测为 `1`），往它上面写扩展样式毫无意义。
    把判据收窄到"真 HWND"之后，"非 Windows 直接降级"这条路径在离屏环境下**可被真断言**，
    而不是靠 mock 装出来 —— 这也是 `platformSupported` 的语义。
    """
    return sys.platform == "win32" and _qpa_platform_name() == "windows"


_USER32: Any = None
_USER32_LOADED = False


def _load_user32(user32: Any = None) -> Any:
    """懒加载并声明 `user32` 的函数签名（非 Windows 返回 `None`）。

    签名必须显式声明：**64 位下 HWND 是 64 位指针**，不声明 argtypes 时 ctypes 会按
    C int 传递，句柄被截断后 `SetWindowLongW` 会写到别的窗口上（阶段 0
    `overlay_common.py` 的原注释）。这里用 `ctypes.c_void_p` 而不是 `ctypes.wintypes.HWND`，
    是为了让本模块在 Linux 上也能被 import（`ctypes.wintypes` 在非 Windows 上不保证可用）。
    """
    global _USER32, _USER32_LOADED
    if user32 is not None:
        return user32
    if _USER32_LOADED:
        return _USER32
    _USER32_LOADED = True
    if sys.platform != "win32":
        return None
    try:
        import ctypes

        api = ctypes.WinDLL("user32", use_last_error=True)
        api.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        api.GetWindowLongW.restype = ctypes.c_long
        api.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
        api.SetWindowLongW.restype = ctypes.c_long
        api.SetWindowPos.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint,
        ]
        api.SetWindowPos.restype = ctypes.c_int
        _USER32 = api
    except Exception as e:  # noqa: BLE001 - 拿不到 user32 只是补丁失效，不该挡住界面
        logger.warning("加载 user32 失败，悬浮窗的 WS_EX_NOACTIVATE 补丁将不可用: %s", e)
        _USER32 = None
    return _USER32


def apply_no_activate(window: Any, user32: Any = None) -> bool:
    """给悬浮窗补上 `WS_EX_NOACTIVATE`，做到"点击/显示都不抢焦点"。

    **顺序不可换**（阶段 0 第 11.4 节，任务书硬约束）：

    1. 先 `winId()` 强制创建原生窗口 —— 句柄不存在时后面两步都是空操作；
    2. `GWL_EXSTYLE |= WS_EX_NOACTIVATE`；
    3. `SetWindowPos(..., SWP_FRAMECHANGED | SWP_NOACTIVATE)` 让样式立即生效；
    4. **然后**调用方才可以 `setVisible(True)`。

    为什么必须自己做：Qt 6.7.3 的 Windows QPA **不会**把
    `Qt::WindowDoesNotAcceptFocus` 翻成 `WS_EX_NOACTIVATE`，窗口 `show()` 之后照样
    成为前台窗口 —— 不补这一步，点一下性能监控悬浮窗就会打断全屏游戏。

    Args:
        window: `QWindow`（QML `Window` 的根对象）；为 `None` 直接返回 `False`。
        user32: 注入的 user32 替身（测试用；`None` 时用真 `ctypes.WinDLL("user32")`）。

    Returns:
        读回 `GWL_EXSTYLE` 后该位是否真的置上了。平台不支持、句柄拿不到、
        API 不可用时返回 `False`（只记 debug，不发 `overlayFailed` —— 那是预期降级）。
    """
    if window is None:
        return False
    if not native_patch_supported():
        logger.debug(
            "跳过 WS_EX_NOACTIVATE 补丁：sys.platform=%s，QPA=%s（非 Windows 由 Qt 平台实现处理）",
            sys.platform, _qpa_platform_name(),
        )
        return False
    api = _load_user32(user32)
    if api is None:
        logger.debug("跳过 WS_EX_NOACTIVATE 补丁：user32 不可用")
        return False
    try:
        hwnd = int(window.winId())  # 第 1 步
    except Exception as e:  # noqa: BLE001 - 窗口已销毁/无原生句柄
        logger.warning("取 winId() 失败，无法补 WS_EX_NOACTIVATE: %s", e)
        return False
    if not hwnd:
        logger.debug("winId() 返回 0（窗口尚无原生句柄），跳过补丁")
        return False
    try:
        exstyle = int(api.GetWindowLongW(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
        if not (exstyle & WS_EX_NOACTIVATE):  # 第 2 步（幂等：已置上就不重复写）
            api.SetWindowLongW(hwnd, GWL_EXSTYLE, exstyle | WS_EX_NOACTIVATE)
        api.SetWindowPos(  # 第 3 步
            hwnd, None, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )
        now = int(api.GetWindowLongW(hwnd, GWL_EXSTYLE)) & 0xFFFFFFFF
    except Exception as e:  # noqa: BLE001 - Win32 调用失败退化成"没补上"
        logger.warning("写 WS_EX_NOACTIVATE 失败（hwnd=%s）: %s", hwnd, e)
        return False
    applied = bool(now & WS_EX_NOACTIVATE)
    if not applied:
        logger.warning("WS_EX_NOACTIVATE 写入后读回仍为 0（hwnd=%s）", hwnd)
    return applied


# ─── 屏幕坐标信息 ──────────────────────────────────────────────


def _round_half_up(value: float) -> int:
    """四舍五入（**不用** Python 内置 `round` 的银行家舍入）。

    `round(0.5) == 0`、`round(1.5) == 2` 这种"取偶"行为会让换算在 `.5` 边界上抖动，
    而坐标换算两个方向必须自洽。
    """
    if value >= 0:
        return int(math.floor(value + 0.5))
    return -int(math.floor(-value + 0.5))


@dataclass(frozen=True)
class ScreenInfo:
    """一块屏幕的两套几何（逻辑 / 物理）。

    `logical` / `physical` 都是 `(x, y, width, height)`。物理几何当前由逻辑几何乘
    `dpr` 得到 —— **只对单一 dpr 成立**（见模块 docstring 的"未验证"）。
    """

    name: str
    dpr: float
    logical: Tuple[int, int, int, int]
    physical: Tuple[int, int, int, int]

    def geometry_dict(self) -> Dict[str, int]:
        return {"x": self.logical[0], "y": self.logical[1], "width": self.logical[2], "height": self.logical[3]}

    def physical_geometry_dict(self) -> Dict[str, int]:
        return {
            "x": self.physical[0], "y": self.physical[1],
            "width": self.physical[2], "height": self.physical[3],
        }

    def to_physical(self, x: int, y: int) -> Tuple[int, int]:
        return (
            self.physical[0] + _round_half_up((int(x) - self.logical[0]) * self.dpr),
            self.physical[1] + _round_half_up((int(y) - self.logical[1]) * self.dpr),
        )

    def to_logical(self, x: int, y: int) -> Tuple[int, int]:
        dpr = self.dpr or 1.0
        return (
            self.logical[0] + _round_half_up((int(x) - self.physical[0]) / dpr),
            self.logical[1] + _round_half_up((int(y) - self.physical[1]) / dpr),
        )


def read_screen_info(screen: Any = None) -> ScreenInfo:
    """读一块 `QScreen` 的两套几何；`screen` 为 `None` 时用主屏。

    物理几何 = 逻辑几何 × dpr。本机（dpr 1.25）与阶段 0 实测的
    "逻辑 2048x1152 / 物理 2560x1440" 完全一致；**混合 DPI 多屏下这个公式不成立**
    （每块屏的物理原点不能各自缩放推导），这一条写在报告里当"未验证"。
    """
    scr = screen
    if scr is None:
        try:
            scr = QGuiApplication.primaryScreen()
        except Exception:  # noqa: BLE001
            scr = None
    if scr is None:
        return ScreenInfo(name="", dpr=1.0, logical=(0, 0, 0, 0), physical=(0, 0, 0, 0))
    try:
        dpr = float(scr.devicePixelRatio() or 1.0)
    except Exception:  # noqa: BLE001
        dpr = 1.0
    try:
        name = str(scr.name() or "")
    except Exception:  # noqa: BLE001
        name = ""
    try:
        g = scr.geometry()
        logical = (int(g.x()), int(g.y()), int(g.width()), int(g.height()))
    except Exception:  # noqa: BLE001
        logical = (0, 0, 0, 0)
    physical = (
        _round_half_up(logical[0] * dpr), _round_half_up(logical[1] * dpr),
        _round_half_up(logical[2] * dpr), _round_half_up(logical[3] * dpr),
    )
    return ScreenInfo(name=name, dpr=dpr, logical=logical, physical=physical)


# ─── 小工具 ────────────────────────────────────────────────────


def _object_name(obj: Any) -> str:
    try:
        return str(obj.objectName() or "")
    except Exception:  # noqa: BLE001 - 已销毁的对象访问会抛
        return ""


def _is_alive(obj: Any) -> bool:
    """`QObject` 的 C++ 侧是否还在（PySide6 对已删除对象会抛 `RuntimeError`）。"""
    if obj is None:
        return False
    try:
        obj.objectName()
    except RuntimeError:
        return False
    except Exception:  # noqa: BLE001
        return False
    return True


def _parse_persisted(raw: Any) -> Tuple[Dict[str, Tuple[int, int]], Dict[str, Dict[str, Any]]]:
    """把配置里的 `overlay_geometry` 解析成 `(位置, 窗口属性)`，**坏值一律丢弃**。

    配置是用户可见、可手改的文件：这里不假设它长得对。非 dict、缺键、坐标是字符串、
    布尔当数字、负的宽高……全部静默忽略并退回默认值，绝不让一个坏配置把悬浮窗卡死
    （旧配置层也没有做校验）。
    """
    positions: Dict[str, Tuple[int, int]] = {}
    props: Dict[str, Dict[str, Any]] = {}
    if not isinstance(raw, dict):
        return positions, props
    for kind in KINDS:
        entry = raw.get(kind)
        if not isinstance(entry, dict):
            continue
        x, y = entry.get("x"), entry.get("y")
        if (
            isinstance(x, (int, float)) and not isinstance(x, bool)
            and isinstance(y, (int, float)) and not isinstance(y, bool)
        ):
            positions[kind] = (int(x), int(y))
        clean: Dict[str, Any] = {}
        width, height = entry.get("width"), entry.get("height")
        if isinstance(width, (int, float)) and not isinstance(width, bool) and int(width) > 0:
            clean["width"] = int(width)
        if isinstance(height, (int, float)) and not isinstance(height, bool) and int(height) > 0:
            clean["height"] = int(height)
        opacity = entry.get("opacity")
        if isinstance(opacity, (int, float)) and not isinstance(opacity, bool):
            if not math.isnan(float(opacity)):
                clean["opacity"] = min(1.0, max(0.0, float(opacity)))
        if clean:
            props[kind] = clean
    return positions, props


def _normalize_prop(key: str, value: Any) -> Tuple[bool, Any, str]:
    """校验并归一化一个 `configure()` 属性。返回 `(是否接受, 归一化值, 拒绝原因)`。

    这里刻意**不做静默兜底**：QML 侧属性名写错（`Opactiy`）或类型写错（字符串坐标）
    必须留下痕迹，否则表现是"调了 configure 但窗口没反应"，排查成本极高。
    """
    if key not in CONFIG_KEYS:
        return False, None, f"未知属性（可选 {', '.join(CONFIG_KEYS)}）"
    if key == "opacity":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, None, "需要 0.0~1.0 的数字"
        number = float(value)
        if math.isnan(number):
            return False, None, "不是有效数字"
        if number < 0.0 or number > 1.0:
            logger.debug("opacity=%r 超出 [0,1]，已钳制", number)
        return True, min(1.0, max(0.0, number)), ""
    if key in ("width", "height"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, None, "需要正整数"
        size = int(value)
        if size <= 0:
            return False, None, "必须大于 0"
        if size > 10000:
            return False, None, "超过合理上限 10000"
        return True, size, ""
    if key in ("x", "y"):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False, None, "需要整数（逻辑像素）"
        if isinstance(value, float) and not float(value).is_integer():
            return False, None, "需要整数（逻辑像素，物理像素请先用 toLogical 换算）"
        return True, int(value), ""
    # topMost / locked
    if not isinstance(value, bool):
        return False, None, "需要布尔值"
    return True, bool(value), ""


class _MetricsRunnable(QRunnable):
    """把一次指标采集交给 `QThreadPool`。

    `run()` 里的异常**必须自己吞掉**：PySide6 把 `QRunnable` 交给 C++ 之后，
    逃出去的 Python 异常会打断线程池的清理路径（`qt_tasks._Runnable` 里记过同一条）。
    """

    def __init__(self, fn: Callable[[], None]) -> None:
        super().__init__()
        self._fn = fn
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover - 正常路径不会走到 except
        try:
            self._fn()
        except Exception:  # noqa: BLE001
            logger.exception("指标采集任务抛出未捕获异常")


class OverlayBridge(QObject):
    """上下文属性 `Overlay` —— 三个悬浮窗（监控 / 桌面歌词 / Toast 宿主）的唯一入口。

    QML 用法（三种窗口同一套写法）::

        Window {
            objectName: "monitorOverlay"          // 与 OBJECT_NAMES 对应
            flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowDoesNotAcceptFocus
                   | (Overlay.monitorProps.topMost === false ? 0 : Qt.WindowStaysOnTopHint)
            opacity: Overlay.monitorProps.opacity
            visible: hostReady && Overlay.monitorVisible
            Component.onCompleted: {
                Overlay.setupWindow("monitor", monitorWin)   // 让桥拿到窗口对象
                var g = Overlay.geometryFor("monitor")
                monitorWin.x = g.x; monitorWin.y = g.y        // x/y 用命令式赋值，避免与拖拽抢绑定
                Overlay.applyNoActivate("monitor")           // 必须在可见之前（阶段 0 第 11.4 节）
                hostReady = true
            }
        }

    只做三件事：**参数转换、调服务、发信号**。指标与歌词内容一律来自 `services/`。
    """

    #: 可见性变化（QML 属性绑定靠它重算）。
    monitorVisibleChanged = Signal()
    lyricVisibleChanged = Signal()
    toastVisibleChanged = Signal()
    #: 显示/隐藏切换结果（`toggleMonitor` 的产物；值 == 切换后的可见性）。
    monitorToggled = Signal(bool)
    lyricToggled = Signal(bool)
    #: 悬浮窗位置变化（QML 在 `onXChanged/onYChanged` 里回报；桥只在**值真的变了**时发）。
    overlayMoved = Signal(str, int, int)
    #: 失败（非法 kind、非法属性、窗口未注册、持久化失败）—— 载荷是人话原因。
    overlayFailed = Signal(str)
    #: `configure()` 生效后的完整窗口属性（QML 也可以只靠 `*Props` 绑定，不必监听它）。
    overlayConfigured = Signal(str, "QVariantMap")
    #: 三个窗口共用的属性表发生变化（QML 的 `*Props` 绑定靠它重算）。
    configChanged = Signal()
    #: 参照屏幕变化（设备像素比 / 屏幕名 / 屏幕几何）。
    screenChanged = Signal()
    #: 桌面歌词锁定态变化（锁定后拖拽区失效）。
    lyricLockChanged = Signal()
    #: `monitorMetrics` 更新。
    metricsChanged = Signal()

    def __init__(
        self,
        config: Any = None,
        screen_provider: Optional[Callable[[], ScreenInfo]] = None,
        metrics_provider: Optional[Callable[[], Dict[str, Any]]] = None,
        executor: Optional[Callable[[Callable[[], None]], None]] = None,
        user32: Any = None,
        parent: Optional[QObject] = None,
    ) -> None:
        """
        Args:
            config: 配置对象（**测试必须注入**，否则会写真实的 `config.json`）；
                `None` 时首次用到才懒加载根模块的 `config` 单例。
            screen_provider: 屏幕信息提供者（测试注入假 dpr / 假屏幕；`None` 时读真屏幕）。
            metrics_provider: 指标提供者（`() -> dict`，与 `MetricsCollector.collect` 同形）；
                `None` 时首次刷新才懒建 `services.monitor_service.MetricsCollector`。
            executor: 后台执行基底（`fn -> None`）；`None` 时用 `QThreadPool`。
                与 `app/tasks.py:TaskRunner.set_executor` 同一思路：测试注入同步执行器，
                就能把"异步取指标"这条路径变成确定性的。
            user32: Win32 API 替身（测试注入以验证补丁调用顺序）。
        """
        super().__init__(parent)
        self._config = config
        #: 配置是**显式注入**的还是懒加载的全局单例 —— 决定 `bind(context)` 能不能覆盖它。
        #: 不区分这两者会有一个很隐蔽的后果：`OverlayBridge()` 一构造就把全局 `config`
        #: 记下来（为了读持久化位置），随后 `bind(context)` 看到"_config 不是 None"就跳过，
        #: 于是**写到另一份配置上**（阶段 2 实测踩过：把位置写进了仓库根目录的 config.json）。
        self._config_injected = config is not None
        self._screen_provider = screen_provider
        self._metrics_provider = metrics_provider
        self._user32 = user32
        self._lock = threading.RLock()

        self._visible: Dict[str, bool] = {kind: False for kind in KINDS}
        self._props: Dict[str, Dict[str, Any]] = {kind: dict(DEFAULT_PROPS[kind]) for kind in KINDS}
        self._positions: Dict[str, Tuple[int, int]] = {}
        self._windows: Dict[str, Any] = {}
        self._hotkeys: Any = None

        # 启动即恢复：配置里的位置/尺寸/不透明度进内存（QML 建窗时用 geometryFor 取）
        self._load_persisted()

        # 指标：worker 只写加锁的普通属性，主线程定时器负责发信号（R7 线程红线）
        self._metrics: Dict[str, Any] = {}
        self._pending_metrics: Optional[Dict[str, Any]] = None
        self._metrics_inflight = False
        self._metrics_count = 0
        self._pool: Optional[QThreadPool] = None
        self._executor = executor
        self._flush_timer = QTimer(self)
        self._flush_timer.setInterval(METRICS_FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush_metrics)
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(DEFAULT_METRICS_INTERVAL_MS)
        self._refresh_timer.timeout.connect(self._on_refresh_tick)

        self._visibility_signals = {
            "monitor": self.monitorVisibleChanged,
            "lyric": self.lyricVisibleChanged,
            "toast": self.toastVisibleChanged,
        }
        self._toggled_signals = {"monitor": self.monitorToggled, "lyric": self.lyricToggled}

    # ─── 装配 ────────────────────────────────────────────────

    def bind(self, context: Any) -> None:
        """接上 `AppContext`：用它自己的 `config`（位置持久化沿用既有配置，不新造存储）。

        由 `main_qml.register_bridges()` 调用（它会给每个桥调一次 `bind(context)`）。
        **显式注入过配置的桥不会被覆盖**（测试注入假配置时，这条保证测试不会误写真实配置）。
        """
        if context is None:
            return
        try:
            cfg = getattr(context, "config", None)
        except Exception as e:  # noqa: BLE001
            logger.debug("从上下文取 config 失败: %s", e)
            return
        if cfg is None or self._config_injected:
            return
        self._config = cfg
        self._load_persisted()

    def _load_persisted(self) -> None:
        """把配置里的位置与窗口属性读进内存（构造期与 `bind()` 共用同一条路径）。"""
        positions, persisted_props = _parse_persisted(self._config_field())
        self._positions.update(positions)
        for kind, overrides in persisted_props.items():
            self._props[kind].update(overrides)

    def _config_object(self) -> Any:
        """配置对象（懒加载根单例）。拿不到时返回 `None`，调用方退化成"不持久化"。"""
        if self._config is None:
            try:
                from config import config as root_config

                self._config = root_config
            except Exception as e:  # noqa: BLE001 - 配置不可用不该挡住悬浮窗
                logger.warning("拿不到全局配置，悬浮窗位置不会被持久化: %s", e)
                return None
        return self._config

    def _config_field(self) -> Any:
        cfg = self._config_object()
        return getattr(cfg, CONFIG_FIELD, None) if cfg is not None else None

    # ─── 屏幕 / 坐标 ──────────────────────────────────────────

    def _screen_info(self) -> ScreenInfo:
        if self._screen_provider is not None:
            try:
                return self._screen_provider()
            except Exception as e:  # noqa: BLE001 - 注入的提供者坏掉也要能退回去
                logger.debug("注入的 screen_provider 失败，回退真屏幕: %s", e)
        return read_screen_info(self._reference_screen())

    def _reference_screen(self) -> Any:
        """参照屏幕 —— **不是**无条件 `primaryScreen()`（阶段 0 第 12.5 节的行为改进）。

        1. 已注册的悬浮窗**自己所在**的 `QScreen`（窗口被拖到副屏后仍按那块屏算）；
        2. 否则第一个"可见且不是悬浮窗"的顶级窗口所在屏幕（实际就是主窗口 ——
           悬浮窗应当跟着用户正在操作的屏幕，而不是永远挤在主屏右上角）；
        3. 都没有才退回 `primaryScreen()`。

        为什么第 2 条是启发式：`QGuiApplication.allWindows()` 的顺序 Qt 没有承诺，
        所以把它当"尽量正确"的兜底而不是判据。
        """
        for kind in KINDS:
            win = self._find_window(kind)
            if win is not None:
                scr = self._window_screen(win)
                if scr is not None:
                    return scr
        ours = set(OBJECT_NAMES.values())
        for win in self._all_windows():
            if _object_name(win) in ours:
                continue
            try:
                if not win.isVisible():
                    continue
            except Exception:  # noqa: BLE001
                continue
            scr = self._window_screen(win)
            if scr is not None:
                return scr
        try:
            return QGuiApplication.primaryScreen()
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _window_screen(win: Any) -> Any:  # noqa: ANN401 - QScreen，避免 import QtGui 具名类型
        try:
            return win.screen()
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _all_windows() -> List[Any]:
        try:
            return list(QGuiApplication.allWindows())
        except Exception:  # noqa: BLE001
            return []

    def _default_geometry(self, kind: str) -> Dict[str, int]:
        """没有持久化位置时的默认摆放（逻辑像素）。

        * **monitor**：参照屏幕右上角，`x = 右边界 - 宽 - 10`、`y = 上边界 + 50`
          —— 与旧 `ui/app_monitor.py:68` 的 `screen_w - 400 - 10` / `+50` 逐字一致；
        * **lyric**：屏幕下方居中 —— **调 `services.desktop_lyric.calc_center_bottom_position`**，
          不在桥里重算（该函数连"x 不钳制"这个已知现状都保留着）；
        * **toast**：参照屏幕右下角内缩 16px（阶段 0 `toast_manager.py` 的 `MARGIN`；
          真正的堆叠由 2.13 的 ToastHost 负责，这里只给基准点）。
        """
        info = self._screen_info()
        sx, sy, sw, sh = info.logical
        props = self._props[kind]
        width, height = int(props["width"]), int(props["height"])
        if sw <= 0 or sh <= 0:
            # 屏幕探不到时的兜底分辨率沿用服务层的常量（旧 Tk 是 1920x1080）
            from services.desktop_lyric import FALLBACK_SCREEN_SIZE

            sw, sh = FALLBACK_SCREEN_SIZE
            sx = sy = 0
        if kind == "lyric":
            from services.desktop_lyric import calc_center_bottom_position

            x, y = calc_center_bottom_position(sw, sh, width, height)
            return {"x": sx + x, "y": sy + y, "width": width, "height": height}
        if kind == "toast":
            margin = 16
            return {
                "x": sx + sw - width - margin, "y": sy + sh - height - margin,
                "width": width, "height": height,
            }
        return {"x": sx + sw - width - 10, "y": sy + 50, "width": width, "height": height}

    @Slot(str, result="QVariantMap")
    def defaultGeometry(self, kind: str) -> Dict[str, int]:  # noqa: N802 - QML 槽名
        """某种悬浮窗的默认几何（不含持久化位置）—— 供"重置位置"之类的入口用。"""
        if kind not in KINDS:
            self.overlayFailed.emit(f"未知的悬浮窗类型: {kind!r}")
            return {}
        return self._default_geometry(kind)

    @Slot(int, int, result="QVariantMap")
    def toPhysical(self, x: int, y: int) -> Dict[str, Any]:  # noqa: N802 - QML 槽名
        """**逻辑像素** -> **物理像素**（Win32 窗口矩形 / 光标的坐标系）。

        用途：把 QML 的 `Window.x/y` 换算成系统坐标再与 `GetWindowRect` / `GetCursorPos`
        对比、或反过来驱动系统级输入。**不要把结果落盘**（dpr 一变就错）。

        返回 `{"x", "y", "dpr", "screen", "system"}`（`system == "physical"`）。
        """
        info = self._screen_info()
        px, py = info.to_physical(x, y)
        return {"x": px, "y": py, "dpr": info.dpr, "screen": info.name, "system": "physical"}

    @Slot(int, int, result="QVariantMap")
    def toLogical(self, x: int, y: int) -> Dict[str, Any]:  # noqa: N802 - QML 槽名
        """**物理像素** -> **逻辑像素**（`toPhysical` 的逆变换）。

        用途：热键/鼠标钩子拿到的是物理坐标（例如 Win32 的光标位置），要摆悬浮窗就得
        换回 QML 的 `Window.x/y` 坐标系。持久化用的是逻辑像素，所以这里是"入盘前"的换算。
        """
        info = self._screen_info()
        lx, ly = info.to_logical(x, y)
        return {"x": lx, "y": ly, "dpr": info.dpr, "screen": info.name, "system": "logical"}

    @Property(float, notify=screenChanged)
    def devicePixelRatio(self) -> float:  # noqa: N802 - QML 属性名
        """参照屏幕的设备像素比（本机 1.25）。"""
        return float(self._screen_info().dpr)

    @Property(str, notify=screenChanged)
    def screenName(self) -> str:  # noqa: N802
        """参照屏幕的名字（`QScreen.name()`；offscreen 下是空串）。"""
        return self._screen_info().name

    @Property("QVariantMap", notify=screenChanged)
    def screenGeometry(self) -> Dict[str, int]:  # noqa: N802
        """参照屏幕的**逻辑**几何 `{x, y, width, height}` —— QML 定位用这一套。"""
        return self._screen_info().geometry_dict()

    @Property("QVariantMap", notify=screenChanged)
    def physicalScreenGeometry(self) -> Dict[str, int]:  # noqa: N802
        """参照屏幕的**物理**几何 —— 与 Win32 打交道时用（单一 dpr 缩放，见模块文档）。"""
        return self._screen_info().physical_geometry_dict()

    @Slot()
    def refreshScreen(self) -> None:  # noqa: N802 - QML 槽名
        """屏幕变化后重发 `screenChanged`（QML 把它挂在 `Screen.onGeometryChanged` 上）。"""
        self.screenChanged.emit()

    # ─── 平台能力 ────────────────────────────────────────────

    @Property(bool, constant=True)
    def platformSupported(self) -> bool:  # noqa: N802 - QML 属性名
        """当前平台是否拿到了**已被实测**的"无边框 + 置顶 + 整窗透明度"三件套。

        Windows 为 `True`（阶段 0 第 11、12 节，离屏 27 + 真实桌面 20 全绿）。
        其它平台（含 CI 的 `offscreen`）为 `False` —— 语义是"**未实测/需要降级路径**"，
        不是"窗口建不出来"：Linux 侧无边框与置顶交给 Qt，`Window.opacity` 依赖合成器
        （无合成器的 X11 下可能被忽略），Wayland 下置顶由合成器决定。
        """
        return native_patch_supported()

    @Property(str, constant=True)
    def platformName(self) -> str:  # noqa: N802 - QML 属性名
        """QPA 平台插件名（`windows` / `xcb` / `wayland` / `offscreen`）—— 诊断用。"""
        return _qpa_platform_name()

    # ─── 可见性（三种窗口同一套） ────────────────────────────

    @Property(bool, notify=monitorVisibleChanged)
    def monitorVisible(self) -> bool:  # noqa: N802 - QML 属性名
        """监控悬浮窗是否可见（QML 用 `visible: hostReady && Overlay.monitorVisible` 绑定）。"""
        return bool(self._visible["monitor"])

    @Property(bool, notify=lyricVisibleChanged)
    def lyricVisible(self) -> bool:  # noqa: N802
        """桌面歌词窗是否可见。"""
        return bool(self._visible["lyric"])

    @Property(bool, notify=toastVisibleChanged)
    def toastVisible(self) -> bool:  # noqa: N802
        """Toast 宿主的开关状态（宿主窗口归 2.13；桥只管这个语义位）。"""
        return bool(self._visible["toast"])

    @Slot()
    def showMonitor(self) -> None:  # noqa: N802 - QML 槽名
        """显示性能监控悬浮窗（同时开始按 `services` 的节奏取指标）。"""
        self._set_visible("monitor", True)

    @Slot()
    def hideMonitor(self) -> None:  # noqa: N802
        """隐藏性能监控悬浮窗（停止指标轮询 —— 不可见时不该继续采）。"""
        self._set_visible("monitor", False)

    @Slot()
    def toggleMonitor(self) -> None:  # noqa: N802
        """显示/隐藏性能监控悬浮窗 —— **全局热键 `Ctrl+Shift+M` 就是它**
        （`bind_hotkeys()` 把 `Hotkeys.triggered("monitor_toggle")` 接到这里）。
        """
        self._set_visible("monitor", not self._visible["monitor"])

    @Slot()
    def showLyric(self) -> None:  # noqa: N802
        """显示桌面歌词窗。"""
        self._set_visible("lyric", True)

    @Slot()
    def hideLyric(self) -> None:  # noqa: N802
        """隐藏桌面歌词窗。"""
        self._set_visible("lyric", False)

    @Slot()
    def toggleLyric(self) -> None:  # noqa: N802
        """显示/隐藏桌面歌词窗。"""
        self._set_visible("lyric", not self._visible["lyric"])

    @Slot()
    def showToast(self) -> None:  # noqa: N802
        """打开 Toast 宿主（具体卡片由 2.13 的 ToastHost 负责）。"""
        self._set_visible("toast", True)

    @Slot()
    def hideToast(self) -> None:  # noqa: N802
        """关闭 Toast 宿主。"""
        self._set_visible("toast", False)

    @Slot(str, bool, result=bool)
    def setVisible(self, kind: str, visible: bool) -> bool:  # noqa: N802
        """通用可见性设置（QML 侧做统一循环时用）。返回可见性是否真的变了。"""
        return self._set_visible(kind, visible)

    def _set_visible(self, kind: str, value: bool) -> bool:
        """可见性的唯一实现：改状态 -> 发 `*VisibleChanged` -> 发 `*Toggled` -> 起停指标。

        只在**值真的变了**时发信号（QML 的 NOTIFY 语义；重复 `showMonitor()` 不会刷信号）。
        """
        if kind not in KINDS:
            self.overlayFailed.emit(f"未知的悬浮窗类型: {kind!r}（可选 {', '.join(KINDS)}）")
            return False
        value = bool(value)
        with self._lock:
            changed = bool(self._visible.get(kind)) != value
            self._visible[kind] = value
        if not changed:
            return False
        self._visibility_signals[kind].emit()
        toggled = self._toggled_signals.get(kind)
        if toggled is not None:
            toggled.emit(value)
        if kind == "monitor":
            self._start_metrics() if value else self._stop_metrics()
        return True

    # ─── 窗口属性（configure） ───────────────────────────────

    @Property("QVariantMap", notify=configChanged)
    def monitorProps(self) -> Dict[str, Any]:  # noqa: N802 - QML 属性名
        """监控窗的当前属性表（`QML` 直接绑定：`opacity: Overlay.monitorProps.opacity`）。"""
        with self._lock:
            return dict(self._props["monitor"])

    @Property("QVariantMap", notify=configChanged)
    def lyricProps(self) -> Dict[str, Any]:  # noqa: N802
        """桌面歌词窗的当前属性表。"""
        with self._lock:
            return dict(self._props["lyric"])

    @Property("QVariantMap", notify=configChanged)
    def toastProps(self) -> Dict[str, Any]:  # noqa: N802
        """Toast 宿主的当前属性表（2.13 的 ToastHost 可选用）。"""
        with self._lock:
            return dict(self._props["toast"])

    @Slot(str, "QVariantMap", result=bool)
    def configure(self, kind: str, props: Optional[Dict[str, Any]] = None) -> bool:
        """设置某种悬浮窗的属性（**三种窗口共用**）。

        可接受的键：`opacity`（0.0~1.0，超出会钳制）、`width` / `height`（正整数）、
        `x` / `y`（**逻辑像素**整数）、`topMost` / `locked`（布尔）。

        非法键与非法值**不静默吞掉**：返回 `False` 并发 `overlayFailed`（带人话原因），
        同一次调用里合法的键照常生效 —— 这样 QML 侧的拼写错误在日志里看得见。
        `x`/`y` 生效后会发 `overlayMoved`，QML 收到即移动窗口。
        """
        if kind not in KINDS:
            self.overlayFailed.emit(f"未知的悬浮窗类型: {kind!r}（可选 {', '.join(KINDS)}）")
            return False
        incoming = dict(props or {})
        if not incoming:
            self.overlayConfigured.emit(kind, self._props_snapshot(kind))
            return True
        applied: Dict[str, Any] = {}
        accepted = True
        for key, value in incoming.items():
            good, normalized, reason = _normalize_prop(key, value)
            if not good:
                accepted = False
                logger.warning("configure(%s) 拒绝属性 %s=%r: %s", kind, key, value, reason)
                self.overlayFailed.emit(f"{kind}: 属性 {key} 不合法（{reason}）")
                continue
            applied[key] = normalized
        with self._lock:
            position_applied = "x" in applied or "y" in applied
            if position_applied:
                pos = self._positions.get(kind)
                if pos is None:
                    # 还没有位置时以默认摆放为基准，只改被点名的那个分量
                    geo = self._default_geometry(kind)
                    pos = (int(geo["x"]), int(geo["y"]))
                px = int(applied.pop("x", pos[0]))
                py = int(applied.pop("y", pos[1]))
                self._positions[kind] = (px, py)
            self._props[kind].update(applied)
            snapshot = dict(self._props[kind])
        self.configChanged.emit()
        self.overlayConfigured.emit(kind, snapshot)
        if kind == "lyric" and "locked" in applied:
            self.lyricLockChanged.emit()
        if position_applied:
            # 只有 x/y **真的被接受**时才推：被拒绝的坐标不该让窗口跳到别处，
            # 也不该在这里读一个可能不存在的内存位置（按 incoming 判断会撞 KeyError）
            self.overlayMoved.emit(kind, int(px), int(py))
        return accepted

    def _props_snapshot(self, kind: str) -> Dict[str, Any]:
        with self._lock:
            return dict(self._props[kind])

    @Slot(str, result=float)
    def increaseOpacity(self, kind: str) -> float:  # noqa: N802 - QML 槽名
        """调高整窗不透明度并返回新值。

        步长与上限来自 `services.desktop_lyric.increase_alpha`（0.05 / 1.0）——
        桥不重算这套边界，改策略只改服务。
        """
        return self._nudge_opacity(kind, +1)

    @Slot(str, result=float)
    def decreaseOpacity(self, kind: str) -> float:  # noqa: N802
        """调低整窗不透明度并返回新值（边界来自 `services.desktop_lyric.decrease_alpha`）。"""
        return self._nudge_opacity(kind, -1)

    def _nudge_opacity(self, kind: str, direction: int) -> float:
        if kind not in KINDS:
            self.overlayFailed.emit(f"未知的悬浮窗类型: {kind!r}")
            return 0.0
        from services.desktop_lyric import decrease_alpha, increase_alpha

        with self._lock:
            current = float(self._props[kind]["opacity"])
            value = increase_alpha(current) if direction > 0 else decrease_alpha(current)
            self._props[kind]["opacity"] = value
        self.configChanged.emit()
        return value

    @Property(bool, notify=lyricLockChanged)
    def lyricLocked(self) -> bool:  # noqa: N802 - QML 属性名
        """桌面歌词是否锁定（锁定后拖拽区失效 —— 旧实现的行为等价物）。"""
        with self._lock:
            return bool(self._props["lyric"]["locked"])

    @Slot(result=bool)
    def toggleLyricLock(self) -> bool:  # noqa: N802 - QML 槽名
        """翻转桌面歌词锁定态并返回新值（规则来自 `services.desktop_lyric.toggle_lock`）。

        旧实现用两个锁形 emoji（U+1F512 / U+1F513）表示该状态；本项目禁止 emoji（D-07），
        QML 侧改用 `Runtime.iconUrl("lock"/"unlock")` 的图标 —— 桥这里只给布尔值。
        """
        from services.desktop_lyric import toggle_lock

        with self._lock:
            locked = toggle_lock(bool(self._props["lyric"]["locked"]))
            self._props["lyric"]["locked"] = locked
        self.lyricLockChanged.emit()
        self.configChanged.emit()
        return locked

    # ─── 位置与持久化 ────────────────────────────────────────

    @Slot(str, result="QVariantMap")
    def geometryFor(self, kind: str) -> Dict[str, Any]:
        """QML 建窗时取初始几何（**全部是逻辑像素**）。

        有持久化位置就用它（`source == "restored"`），否则按默认摆放
        （`source == "default"`，见 `_default_geometry`）。`x`/`y` 请用**命令式赋值**
        写进窗口（`monitorWin.x = g.x`），不要写成绑定 —— 否则拖拽改坐标会把绑定打断，
        Qt 会打一堆"binding loop / 无法赋值"的警告。
        """
        if kind not in KINDS:
            self.overlayFailed.emit(f"未知的悬浮窗类型: {kind!r}（可选 {', '.join(KINDS)}）")
            return {}
        with self._lock:
            pos = self._positions.get(kind)
            props = dict(self._props[kind])
        if pos is None:
            geo = self._default_geometry(kind)
            source = "default"
        else:
            geo = {
                "x": int(pos[0]), "y": int(pos[1]),
                "width": int(props["width"]), "height": int(props["height"]),
            }
            source = "restored"
        geo.update({
            "opacity": props["opacity"],
            "locked": bool(props["locked"]),
            "topMost": bool(props["topMost"]),
            "source": source,
        })
        return geo

    @Slot(str, int, int)
    def reportPosition(self, kind: str, x: int, y: int) -> None:  # noqa: N802 - QML 槽名
        """QML 在 `onXChanged/onYChanged` 里回报窗口位置（**逻辑像素**）。

        拖动过程中每帧都会调到这里，所以这里**只更新内存**并（在真的变了时）发
        `overlayMoved`；落盘由拖拽结束时的 `savePosition()` 负责。
        """
        if kind not in KINDS:
            self.overlayFailed.emit(f"未知的悬浮窗类型: {kind!r}（可选 {', '.join(KINDS)}）")
            return
        pos = (int(x), int(y))
        with self._lock:
            changed = self._positions.get(kind) != pos
            self._positions[kind] = pos
        if changed:
            self.overlayMoved.emit(kind, pos[0], pos[1])

    @Slot(str, result="QVariantMap")
    def positionFor(self, kind: str) -> Dict[str, Any]:  # noqa: N802 - QML 槽名
        """桥**内存中**的位置（没有则给默认值）—— 诊断/测试用，等于 `geometryFor` 的 x/y 部分。"""
        geo = self.geometryFor(kind)
        if not geo:
            return {}
        return {"x": geo["x"], "y": geo["y"], "source": geo["source"]}

    @Slot()
    def savePosition(self) -> None:  # noqa: N802 - QML 槽名
        """把三个窗口当前的位置（+ 尺寸/不透明度）写进配置并落盘。

        **只在拖拽结束时调**：拖动过程中写盘会把配置读写放大到不可接受。
        写入是**合并**语义：配置里其它 kind / 其它键（例如用户手加的）原样保留，
        不会整体覆盖。
        """
        cfg = self._config_object()
        if cfg is None:
            self.overlayFailed.emit("配置不可用：悬浮窗位置无法持久化")
            return
        with self._lock:
            positions = dict(self._positions)
            props = {kind: dict(value) for kind, value in self._props.items()}
        raw = getattr(cfg, CONFIG_FIELD, None)
        payload: Dict[str, Any] = {
            key: dict(value) for key, value in (raw.items() if isinstance(raw, dict) else [])
            if isinstance(value, dict)
        }
        for kind in KINDS:
            pos = positions.get(kind)
            if pos is None:
                continue  # 窗口还没建过：不写入，避免用空值抹掉已有位置
            entry = payload.get(kind, {})
            entry.update({
                "x": int(pos[0]), "y": int(pos[1]),
                "width": int(props[kind]["width"]), "height": int(props[kind]["height"]),
                "opacity": float(props[kind]["opacity"]),
            })
            payload[kind] = entry
        try:
            setattr(cfg, CONFIG_FIELD, payload)
            save = getattr(cfg, "save_config", None)
            if callable(save):
                save()
            else:
                logger.warning("配置对象没有 save_config()：位置只留在内存里")
        except Exception as e:  # noqa: BLE001 - 写盘失败不该影响窗口
            logger.warning("写悬浮窗位置失败: %s", e)
            self.overlayFailed.emit(f"悬浮窗位置写盘失败: {e}")

    @Slot()
    def restorePosition(self) -> None:  # noqa: N802 - QML 槽名
        """从配置重新读一次位置/属性，并把**变化过的**位置用 `overlayMoved` 推给窗口。

        幂等：与内存里一致的位置不会再发信号（否则 QML 的赋值 -> 回报 -> 再赋值会成环）。
        """
        positions, persisted_props = _parse_persisted(self._config_field())
        pushed: List[Tuple[str, int, int]] = []
        with self._lock:
            for kind, pos in positions.items():
                if self._positions.get(kind) != pos:
                    self._positions[kind] = pos
                    pushed.append((kind, pos[0], pos[1]))
            for kind, overrides in persisted_props.items():
                self._props[kind].update(overrides)
        if persisted_props:
            self.configChanged.emit()
        for kind, x, y in pushed:
            self.overlayMoved.emit(kind, x, y)

    @Slot(str, result=bool)
    def forgetPosition(self, kind: str) -> bool:  # noqa: N802 - QML 槽名
        """丢掉某种窗口的内存位置（下次 `geometryFor` 回到默认摆放）；不动配置。"""
        if kind not in KINDS:
            self.overlayFailed.emit(f"未知的悬浮窗类型: {kind!r}")
            return False
        with self._lock:
            return self._positions.pop(kind, None) is not None

    # ─── 窗口登记 与 Win32 补丁 ──────────────────────────────

    @Slot(str, QObject, result=bool)
    def setupWindow(self, kind: str, window: QObject) -> bool:  # noqa: N802 - QML 槽名
        """QML 悬浮窗把自己的根对象登记给桥（在 `Component.onCompleted` 里调）。

        为什么要显式登记：`applyNoActivate(kind)` 需要一个真 `QWindow` 才能取 `winId()`，
        而桥不持有 QML 对象树。没登记时还有一个兜底：按 `OBJECT_NAMES` 里约定的
        `objectName` 扫 `QGuiApplication.allWindows()`。
        """
        if kind not in KINDS:
            self.overlayFailed.emit(f"未知的悬浮窗类型: {kind!r}（可选 {', '.join(KINDS)}）")
            return False
        if window is None:
            self.overlayFailed.emit(f"{kind}: setupWindow 收到空对象")
            return False
        with self._lock:
            self._windows[kind] = window
        try:
            # 窗口被销毁后必须丢掉引用：拿着已删除的 C++ 对象再访问会抛 RuntimeError
            window.destroyed.connect(lambda *_a, _k=kind: self._forget_window(_k))
        except Exception as e:  # noqa: BLE001 - 连不上 destroyed 只是少一层保护
            logger.debug("连接窗口 destroyed 失败（不影响使用）: %s", e)
        self.screenChanged.emit()  # 参照屏幕可能因此从 primaryScreen 变成窗口所在屏
        return True

    def _forget_window(self, kind: str) -> None:
        with self._lock:
            self._windows.pop(kind, None)

    def _find_window(self, kind: str) -> Any:
        """按登记表 -> `objectName` 兜底找窗口；找不到返回 `None`。"""
        with self._lock:
            win = self._windows.get(kind)
        if _is_alive(win):
            return win
        if win is not None:
            logger.debug("悬浮窗 %s 已被销毁，从登记表移除", kind)
            self._forget_window(kind)
        name = OBJECT_NAMES.get(kind, "")
        if name:
            for candidate in self._all_windows():
                if _object_name(candidate) == name:
                    return candidate
        return None

    @Slot(str, result=bool)
    def applyNoActivate(self, kind: str) -> bool:  # noqa: N802 - QML 槽名
        """给某种悬浮窗补 Win32 `WS_EX_NOACTIVATE`（**必须在它可见之前调**）。

        实现原样搬自阶段 0 的 `poc/overlay/overlay_common.py:apply_no_activate`；
        顺序（`winId()` -> 置位 -> `SetWindowPos(SWP_FRAMECHANGED)`）不可改。

        返回：Windows 上是否真的置上了该位；**非 Windows / offscreen 直接返回 `False`**
        并记 debug —— 这是预期降级（Linux 侧 Qt 自己处理 X11 input hint，Wayland 由合成器
        决定），不是错误，所以**不发** `overlayFailed`。窗口没登记/写失败才会发。
        """
        if kind not in KINDS:
            self.overlayFailed.emit(f"未知的悬浮窗类型: {kind!r}（可选 {', '.join(KINDS)}）")
            return False
        if not native_patch_supported():
            logger.debug(
                "applyNoActivate(%s) 跳过：sys.platform=%s，QPA=%s",
                kind, sys.platform, _qpa_platform_name(),
            )
            return False
        window = self._find_window(kind)
        if window is None:
            self.overlayFailed.emit(
                f"{kind}: 悬浮窗未登记（先调 setupWindow），无法应用 WS_EX_NOACTIVATE"
            )
            return False
        ok = apply_no_activate(window, user32=self._user32)
        if not ok:
            self.overlayFailed.emit(f"{kind}: WS_EX_NOACTIVATE 未能写入（详见日志）")
        return ok

    @Slot(str, result=bool)
    def isRegistered(self, kind: str) -> bool:  # noqa: N802 - QML 槽名
        """某种悬浮窗的窗口对象当前是否在登记表里（诊断/测试用）。"""
        return self._find_window(kind) is not None

    # ─── 指标（worker 取数 + 主线程发信号） ──────────────────

    @Slot()
    def refreshMetrics(self) -> None:  # noqa: N802 - QML 槽名
        """取一次系统指标（异步）。

        为什么异步：`MetricsCollector.collect()` 是**阻塞式**的（`psutil.cpu_percent(0.1)`
        会真的睡 100ms），服务层文档明确要求"应在工作线程调用"。在 GUI 线程直接调会让
        界面每秒卡 100ms —— 旧 Tk 实现也是 `after(0, ...)` 从 worker 切回主线程的。
        """
        with self._lock:
            if self._metrics_inflight:
                return
            self._metrics_inflight = True
        try:
            self._ensure_executor()(self._collect_metrics_worker)
        except Exception as e:  # noqa: BLE001 - 提交失败退化成"这次没数据"
            with self._lock:
                self._metrics_inflight = False
            logger.warning("提交指标采集任务失败: %s", e)
            self.overlayFailed.emit(f"提交指标采集任务失败: {e}")

    @Property("QVariantMap", notify=metricsChanged)
    def monitorMetrics(self) -> Dict[str, Any]:  # noqa: N802 - QML 属性名
        """最近一次采集到的指标（`services.monitor_service.MetricsCollector.collect()` 的键）。

        形如 `cpu_percent` / `cpu_freq` / `mem_percent` / `mem_used` / `mem_total` /
        `gpu_name` / `gpu_util` / `gpu_mem` / `gpu_temp`；采集失败时带一个 `error` 键。
        桥**不加工**这些值（不算百分比、不做换算），QML 直接显示。
        """
        with self._lock:
            return dict(self._metrics)

    @Property(int, constant=True)
    def metricsIntervalMs(self) -> int:  # noqa: N802
        """指标轮询周期（毫秒）—— 供 QML 显示或自建定时器时对齐。"""
        return DEFAULT_METRICS_INTERVAL_MS

    def _ensure_executor(self) -> Callable[[Callable[[], None]], None]:
        """后台执行基底：默认 `QThreadPool`（1 线程足够），可由构造参数替换。"""
        if self._executor is None:
            if self._pool is None:
                pool = QThreadPool()
                pool.setMaxThreadCount(1)
                pool.setExpiryTimeout(5000)
                self._pool = pool
            pool = self._pool
            self._executor = lambda fn: pool.start(_MetricsRunnable(fn))
        return self._executor

    def _metrics_provider_fn(self) -> Callable[[], Dict[str, Any]]:
        """拿指标提供者（**可能在 worker 线程被调用**，所以只碰加锁的普通属性）。"""
        with self._lock:
            if self._metrics_provider is not None:
                return self._metrics_provider
        try:
            from services.monitor_service import MetricsCollector

            collector = MetricsCollector()
            # GPU 探测会起子进程（nvidia-smi 之类），**故意放在 worker 线程**做，
            # 避免首次刷新时卡住主线程
            collector.init_gpu()
            provider: Callable[[], Dict[str, Any]] = collector.collect
        except Exception as e:  # noqa: BLE001 - 服务不可用时退化成空指标
            logger.warning("指标采集服务不可用，监控窗将显示占位: %s", e)
            provider = lambda: {}
        with self._lock:
            self._metrics_provider = provider
        return provider

    def _collect_metrics_worker(self) -> None:
        """**worker 线程**：只调服务 + 写加锁的普通属性。

        绝不 emit、绝不碰 QML 可见属性、绝不碰 QML 引擎（契约第三节红线 3 / 闸门 R7）——
        结果的投递由主线程的 `_flush_timer` 负责。
        """
        provider = self._metrics_provider_fn()
        try:
            data = provider()
            payload: Dict[str, Any] = dict(data) if isinstance(data, dict) else {"value": data}
        except Exception as e:  # noqa: BLE001 - 采集失败也要让界面知道
            payload = {"error": str(e)}
        with self._lock:
            self._pending_metrics = payload
            self._metrics_inflight = False

    def _flush_metrics(self) -> None:
        """**主线程**定时器：把 worker 放下的结果发成 QML 信号（合并 + 节流）。"""
        with self._lock:
            pending = self._pending_metrics
            self._pending_metrics = None
        if pending is None:
            return
        with self._lock:
            self._metrics = pending
            self._metrics_count += 1
        self.metricsChanged.emit()

    def _on_refresh_tick(self) -> None:
        self.refreshMetrics()

    def _start_metrics(self) -> None:
        """监控窗显示时开始轮询（首次立刻取一次，不等第一个周期）。"""
        self._flush_timer.start()
        self._refresh_timer.start()
        self.refreshMetrics()

    def _stop_metrics(self) -> None:
        """监控窗隐藏时停止轮询（不可见的窗口不该继续每秒采集）。"""
        self._refresh_timer.stop()
        self._flush_timer.stop()

    @Property(bool)
    def metricsRunning(self) -> bool:  # noqa: N802 - QML 属性名
        """指标轮询是否在跑（监控窗可见即开；无 NOTIFY，仅供诊断/测试读）。"""
        try:
            return bool(self._refresh_timer.isActive())
        except RuntimeError:  # pragma: no cover - 桥已销毁
            return False

    # ─── 热键接线 ────────────────────────────────────────────

    def bind_hotkeys(self, hotkeys: Any) -> bool:
        """把 `Hotkeys.triggered("monitor_toggle")` 接到 `toggleMonitor()`（`Ctrl+Shift+M`）。

        这是一条**装配期**接线（不是 `@Slot`：QML 侧若要自己接，用
        `Connections { function onTriggered(n) { Overlay.onHotkey(n) } }`）。
        桥**不自己注册系统热键**：`app/bridges/hotkey_bridge.py` 已经是唯一注册入口
        （阶段 0/1 已确认"保留非 Qt 全局热键方案"），再注册一套会长出第二份语义。

        同一对象重复调用是幂等的 —— 否则热键触发时槽会被调两次，`toggle` 两次等于没反应。
        """
        if hotkeys is None:
            return False
        if hotkeys is self._hotkeys:
            return True
        signal = getattr(hotkeys, "triggered", None)
        if signal is None or not hasattr(signal, "connect"):
            logger.warning("bind_hotkeys: 对象没有 triggered 信号，跳过热键接线")
            return False
        try:
            signal.connect(self.onHotkey)
        except Exception as e:  # noqa: BLE001 - 接线失败只影响热键，不影响按钮
            logger.warning("连接热键信号失败: %s", e)
            return False
        self._hotkeys = hotkeys  # 留强引用，避免桥被 GC 后信号无处可去
        return True

    @Slot(str)
    def onHotkey(self, name: str) -> None:  # noqa: N802 - QML 槽名
        """热键名字 -> 动作（目前只有 `monitor_toggle`）。

        `HotkeyBridge.triggered` 已经从热键库线程回到主线程（跨线程自动 Queued），
        所以这里可以在主线程直接改状态。
        """
        if str(name) == HOTKEY_MONITOR_TOGGLE:
            self.toggleMonitor()
        else:
            logger.debug("悬浮窗桥不处理热键 %r", name)

    # ─── 诊断 与 收尾 ────────────────────────────────────────

    @Slot(result="QVariantMap")
    def describe(self) -> Dict[str, Any]:
        """一次性诊断快照（启动自检、测试与问题定位用）。"""
        info = self._screen_info()
        with self._lock:
            positions = {kind: list(pos) for kind, pos in self._positions.items()}
            props = {kind: dict(value) for kind, value in self._props.items()}
            visible = dict(self._visible)
            metric_keys = sorted(self._metrics)
            metrics_count = self._metrics_count
            registry = sorted(kind for kind, win in self._windows.items() if _is_alive(win))
        return {
            "platform": _qpa_platform_name(),
            "platform_supported": native_patch_supported(),
            "dpr": info.dpr,
            "screen": info.name,
            "screen_logical": info.geometry_dict(),
            "screen_physical": info.physical_geometry_dict(),
            "visible": visible,
            "positions": positions,
            "props": props,
            "windows": registry,
            "metrics_keys": metric_keys,
            "metrics_refreshes": metrics_count,
            "metrics_running": self.metricsRunning,
            "hotkeys_bound": self._hotkeys is not None,
            "config_field": CONFIG_FIELD,
        }

    def shutdown(self) -> None:
        """退出路径：停表 + 等指标线程池收干净（由入口调用）。

        刻意**不**接 `QObject.destroyed` 自动做（PySide6 6.7.3 下"接收者是发送者自己"
        的连接收不到该信号，`event_bridge.py` 里记过这个实测结论）——
        漏掉的代价只是退出时多等一次线程池，不会留下系统级热键那样的副作用。
        """
        for timer in (self._refresh_timer, self._flush_timer):
            try:
                timer.stop()
            except RuntimeError:  # pragma: no cover - 桥已销毁
                pass
        pool = self._pool
        if pool is not None:
            try:
                pool.clear()
                pool.waitForDone(2000)
            except Exception as e:  # noqa: BLE001
                logger.debug("等待指标线程池结束失败: %s", e)


__all__ = [
    "CONFIG_FIELD",
    "DEFAULT_METRICS_INTERVAL_MS",
    "DEFAULT_PROPS",
    "HOTKEY_MONITOR_TOGGLE",
    "KINDS",
    "METRICS_FLUSH_MS",
    "OBJECT_NAMES",
    "QML_NAME",
    "OverlayBridge",
    "ScreenInfo",
    "apply_no_activate",
    "native_patch_supported",
    "read_screen_info",
]


