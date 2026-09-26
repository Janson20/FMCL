"""桌面歌词的**零 GUI 逻辑**（阶段 1 任务 1.4-A，形态 2：逻辑与窗口切分）。

对应宿主是 `ui/music_desktop_lyric.py`（305 行，`DesktopLyricWindow` 17 个方法）。
切分判据只有一条：**能不能在不创建 Tk 窗口的前提下算出结果**。

| 搬进来（纯逻辑）                                   | 留在 ui/（Tk 表示层）                         |
| -------------------------------------------------- | --------------------------------------------- |
| 屏幕尺寸 + 窗口尺寸 → 窗口坐标                     | `CTkToplevel` 的创建 / `geometry()` / `attributes()` |
| 进度 ms + 歌词行（有 `.time`）→ 当前行索引（二分） | 三个 `CTkLabel` 的 `configure(text=...)`      |
| 透明度增减的边界与步长                             | `win.attributes("-alpha", ...)`               |
| 拖动位置计算（`事件坐标 - 抓取偏移`）              | `<Button-1>` / `<B1-Motion>` 绑定与回调       |
| 锁定态翻转                                         | 锁按钮图标的 `configure(text=...)`            |
| 位置/透明度/锁定 → `LyricWindowState` 状态数据类   | 窗口生命周期（`withdraw` / `deiconify` / `destroy`） |

## 为什么不把 `_alpha_color` 搬进来

它是 **Tk 表示层**函数：入参是 `ui/constants.py` 的 `COLORS["bg_dark"]`，出参是
customtkinter 认的 `"#rrggbb"` 字符串，且原文 docstring 写明"本函数暂不直接使用，
保留备用"。抽进 `services/` 只会把一条 UI 颜色约定搬到一个"零 UI"的模块里 ——
判据不是"它算不算纯计算"，而是"它是不是 Tk 的颜色表示"。

## 记录现状、疑为缺陷（**未改**，只搬）

1. `calc_center_bottom_position` 的 `x` **不做钳制**：屏幕比窗口窄时（例如 400px 宽
   的屏幕、600px 宽的歌词窗）会算出负的 `x`，窗口左边会被推到屏幕外。原文只对 `y`
   做了 `max(0, ...)`。本轮逐字保留 —— 改它属于行为变更，不在抽取任务内。
2. 拖动（`drag_position`）**没有任何屏幕内钳制**：可以把歌词窗拖到任何地方（包括
   完全拖出屏幕后无法再抓回来）。原文如此，此处逐字保留。
3. 拖动只绑在控制栏与标题标签上，歌词文字区不能拖（原文如此，属 UI 绑定，留界面侧）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple

#: 桌面歌词窗口的默认几何（原文写死在 `_create_window` 的 `600x140` 里）
DEFAULT_WINDOW_WIDTH = 600
DEFAULT_WINDOW_HEIGHT = 140

#: 底部留白（避开任务栏），原文 `_calc_center_bottom_position` 里写死 80
BOTTOM_MARGIN = 80

#: 屏幕尺寸探测失败时的兜底分辨率（原文 `except Exception: screen_w, screen_h = 1920, 1080`）
FALLBACK_SCREEN_SIZE = (1920, 1080)

#: 透明度边界与步长（原文 `_increase_opacity` / `_decrease_opacity` 里写死）
ALPHA_STEP = 0.05
ALPHA_MIN = 0.15
ALPHA_MAX = 1.0
DEFAULT_ALPHA = 0.85


def calc_center_bottom_position(
    screen_w: int,
    screen_h: int,
    width: int = DEFAULT_WINDOW_WIDTH,
    height: int = DEFAULT_WINDOW_HEIGHT,
    bottom_margin: int = BOTTOM_MARGIN,
) -> Tuple[int, int]:
    """屏幕下方居中位置（避开任务栏）。

    原文（`ui/music_desktop_lyric.py:_calc_center_bottom_position`）逐字搬来：
    ``x`` 不钳制，``y`` 钳到非负。屏幕尺寸由调用方探测后传入 —— 服务不碰控件。

    Args:
        screen_w: 屏幕宽（像素）
        screen_h: 屏幕高（像素）
        width: 窗口宽
        height: 窗口高
        bottom_margin: 底部留白

    Returns:
        ``(x, y)``；屏幕比窗口窄时 ``x`` 可能为负（记录现状、疑为缺陷，见模块 docstring）
    """
    x = (screen_w - width) // 2
    # 底部留 80px 边距 (避开任务栏)
    y = screen_h - height - bottom_margin
    return x, max(0, y)


def find_current_line(lines: Sequence, elapsed_ms: int) -> int:
    """二分查找当前歌词行索引（``lines[i].time <= elapsed_ms`` 的最大 ``i``）。

    与原文 `DesktopLyricWindow._find_current_line` 逐字一致：
    空列表返回 ``-1``；进度早于第一行时也返回 ``-1``；进度超过最后一行时返回最后一行索引。

    ``lines`` 的元素只需有 ``.time`` 属性（毫秒），所以服务层不必 import
    `ui.music_lyrics.LyricLine` —— 鸭子类型即可，测试也能用假对象。
    """
    if not lines:
        return -1
    lo, hi = 0, len(lines) - 1
    result = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if lines[mid].time <= elapsed_ms:
            result = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def increase_alpha(alpha: float) -> float:
    """调高不透明度（步长 0.05，上界 1.0）。"""
    return min(ALPHA_MAX, alpha + ALPHA_STEP)


def decrease_alpha(alpha: float) -> float:
    """调低不透明度（步长 0.05，下界 0.15）。"""
    return max(ALPHA_MIN, alpha - ALPHA_STEP)


def toggle_lock(locked: bool) -> bool:
    """锁定态翻转（锁定后拖动被忽略，窗口不再跟随鼠标）。"""
    return not locked


def drag_offset(event_root: int, window_pos: int) -> int:
    """按下鼠标时记录"抓取点相对窗口左上角的偏移"。"""
    return event_root - window_pos


def drag_position(event_x_root: int, event_y_root: int, offset_x: int, offset_y: int) -> Tuple[int, int]:
    """拖动中的窗口新位置 = 鼠标根坐标 - 抓取偏移。

    **不做屏幕内钳制**：原文 `_drag` 直接 ``win.geometry(f"+{x}+{y}")``，
    没有任何 clamp，此处逐字保留（记录现状、疑为缺陷，见模块 docstring）。
    """
    return event_x_root - offset_x, event_y_root - offset_y


def neighbor_line_texts(lines: Sequence, index: int) -> Tuple[str, str]:
    """当前行上下两行的文本（原文 `update_progress` 里取 prev / next 的规则）。

    越界一律给空串：``index <= 0`` 时上一行为空，``index`` 为最后一行时下一行为空。
    该规则原先散在 `update_progress` 的四个分支里（含"进度还没到第一行"时下一行
    取 ``lines[0]`` 的特例），这里只抽出"上一行/下一行"的通用部分，
    未命中第一行之前的特例仍留在界面侧按其原文处理。
    """
    prev_text = lines[index - 1].text if index > 0 else ""
    nxt_text = lines[index + 1].text if index + 1 < len(lines) else ""
    return prev_text, nxt_text


@dataclass
class LyricWindowState:
    """桌面歌词窗口的可变状态：位置 / 透明度 / 是否锁定（+ 当前行索引）。

    界面侧持有一个实例，所有状态迁移都走这里的方法（`after` 与控件调用仍留界面）。
    """

    x: int = 0
    y: int = 0
    width: int = DEFAULT_WINDOW_WIDTH
    height: int = DEFAULT_WINDOW_HEIGHT
    alpha: float = DEFAULT_ALPHA
    locked: bool = False
    current_line_index: int = -1
    #: 抓取偏移（拖动用），键与原文 `_drag_data` 一致：{"x": ..., "y": ...}
    drag_data: dict = field(default_factory=lambda: {"x": 0, "y": 0})

    def place_at_screen_bottom_center(self, screen_w: int, screen_h: int) -> Tuple[int, int]:
        """按"屏幕下方居中"重算位置并记下，返回 ``(x, y)``。"""
        self.x, self.y = calc_center_bottom_position(screen_w, screen_h, self.width, self.height)
        return self.x, self.y

    def begin_drag(self, event_x_root: int, event_y_root: int, win_x: int, win_y: int) -> None:
        """记录抓取偏移（锁定态下由调用方直接跳过，见 `is_draggable`）。"""
        self.drag_data["x"] = drag_offset(event_x_root, win_x)
        self.drag_data["y"] = drag_offset(event_y_root, win_y)

    def drag_to(self, event_x_root: int, event_y_root: int) -> Tuple[int, int]:
        """算出拖动后的新位置并记下，返回 ``(x, y)``。"""
        self.x, self.y = drag_position(event_x_root, event_y_root, self.drag_data["x"], self.drag_data["y"])
        return self.x, self.y

    def toggle_lock(self) -> bool:
        """翻转锁定态并返回新值。"""
        self.locked = toggle_lock(self.locked)
        return self.locked

    def increase_opacity(self) -> float:
        """调高透明度并返回新值。"""
        self.alpha = increase_alpha(self.alpha)
        return self.alpha

    def decrease_opacity(self) -> float:
        """调低透明度并返回新值。"""
        self.alpha = decrease_alpha(self.alpha)
        return self.alpha

    def reset_current_line(self) -> None:
        """换歌词时把当前行索引复位（原文 `set_lyric_lines` 的 ``-1``）。"""
        self.current_line_index = -1

    @property
    def is_draggable(self) -> bool:
        """锁定态下不可拖动（原文 `_start_drag` / `_drag` 的首个判断）。"""
        return not self.locked


# ══════════════════════════════════════════════════════════════════════
# 阶段 1 任务 1.4-C：宿主 `ui/app_music.py` 的桌面歌词开关那一族
#
# `_music_toggle_desktop_lyric` / `_music_show_desktop_lyric` 里唯二不是控件动作的
# 判定搬到这里；创建/显示/隐藏/销毁窗口本身仍是 Tk 表示层的事。
# ══════════════════════════════════════════════════════════════════════


def target_visible(window: Any) -> bool:
    """点击"桌面歌词"开关后的**目标**可见性。

    原文 `if self._music_desktop_lyric and self._music_desktop_lyric.is_visible: 隐藏 else: 显示`，
    即"不存在或不可见 → 应变可见"。用 ``bool(window) and ...`` 逐字保留原文的真值判断
    （不假定窗口对象一定为真）。
    """
    return not (bool(window) and window.is_visible)


def ready_lines(parser: Any) -> Optional[List[Any]]:
    """已解析完成时应当推给歌词窗的歌词行；``None`` 表示"还没解析好，别推"。

    原文 ``if self._music_lyric_parser.is_parsed: window.set_lyric_lines(parser.lines)``。
    返回的是 ``parser.lines`` **本身**（不是副本）—— 窗口把它存进 `_lyric_lines`，
    换成副本会让后续解析（`clear()` 改的是原 list）与窗口看到的内容脱钩。
    """
    return parser.lines if parser.is_parsed else None


__all__ = [
    "ALPHA_MAX",
    "ALPHA_MIN",
    "ALPHA_STEP",
    "DEFAULT_ALPHA",
    "DEFAULT_WINDOW_HEIGHT",
    "DEFAULT_WINDOW_WIDTH",
    "FALLBACK_SCREEN_SIZE",
    "LyricWindowState",
    "calc_center_bottom_position",
    "decrease_alpha",
    "drag_offset",
    "drag_position",
    "find_current_line",
    "increase_alpha",
    "neighbor_line_texts",
    "ready_lines",
    "target_visible",
    "toggle_lock",
]
