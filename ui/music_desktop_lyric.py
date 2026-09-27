"""桌面歌词窗口 - 独立置顶半透明歌词显示

单窗口架构（**以实现为准**，D-23 修正）:
    - 无边框（``overrideredirect(True)``）+ 置顶（``attributes("-topmost")``）
    - 半透明靠**整窗** ``attributes("-alpha", ...)``：窗口里的一切（含文字）
      都按同一个 alpha 变淡，没有例外
    - 底色是**实色** ``COLORS["bg_dark"]``（``win.configure(fg_color=_BG_COLOR)``），
      不是"背景透明"；因此**没有鼠标穿透**，整窗都接收事件

为什么这里要写清楚：本模块的旧 docstring 曾宣称"用 ``-transparentcolor``
实现背景透明 / 文字不受 alpha 影响 / 背景区域鼠标事件穿透"—— 全文件从来没有
调用过 ``-transparentcolor``，那三句都是错的（D-23 记录的"docstring 在说谎"）。
新界面 ``qml/overlays/DesktopLyricOverlay.qml`` 已按**同样的实际行为**实现
（``opacity: 0.85`` 整窗 + ``color: Theme.bgDark`` 实色底），并由
``tests/test_overlay_windows.py`` 的 ``window.opacity() == 0.85`` /
``color.alpha() == 255``（注释写着"实色底，不许 transparent"）钉住。
**鼠标穿透是阶段 3.18 的新增项**（用户已裁决本轮不做），不是本模块缺失的能力；
真要做得先改那条"不许 transparent"的断言，并把 C2 从"推迟"改成"本次实现"。

阶段 1 任务 1.4-A（形态 2：逻辑与窗口切分）：**零 GUI 的规则**已搬进
``services/desktop_lyric.py`` —— 屏幕尺寸 → 窗口坐标、进度 → 当前歌词行（二分）、
透明度边界与步长、拖动位置计算、锁定态翻转，以及状态数据类 ``LyricWindowState``。
本文件只剩 Tk 表示层：控件创建/配置、`geometry()` / `attributes()`、事件绑定与
窗口生命周期。判据是"能不能在不创建 Tk 窗口的前提下算出结果"。

``_alpha_color`` 按同一条判据**留在本文件**：它把 ``ui/constants.py`` 的
``COLORS["bg_dark"]`` 转成 customtkinter 认的 ``"#rrggbb"`` 串，是 Tk 的颜色表示，
不是纯计算。详见 `services/desktop_lyric.py` 的模块 docstring。
"""

import tkinter as tk
from typing import List, Optional

import customtkinter as ctk

from services.desktop_lyric import (
    ALPHA_MAX,
    ALPHA_MIN,
    DEFAULT_ALPHA,
    DEFAULT_WINDOW_HEIGHT,
    DEFAULT_WINDOW_WIDTH,
    FALLBACK_SCREEN_SIZE,
    LyricWindowState,
    calc_center_bottom_position,
    find_current_line,
    neighbor_line_texts,
)
from ui.constants import COLORS, FONT_FAMILY
from ui.music_lyrics import LyricLine

_BG_COLOR = COLORS["bg_dark"]  # "#1a1a2e"


def _alpha_color(alpha: float) -> str:
    """将 hex 颜色与透明度混合生成带透明度的 hex 颜色字符串 (模拟 RGBA)

    customtkinter 只认 "#RRGGBB"，不支持 alpha，所以本函数其实**没有**在混 alpha：
    它把 ``_BG_COLOR`` 原样解析成小写 hex 再拼回去（等价于恒等变换）。
    真正控制透明度的只有窗口的 ``attributes("-alpha", ...)``，那会让**整窗**
    （含文字）一起变淡 —— 与旧 docstring 说的"文字不受影响因为背景是
    transparentcolor"相反，本文件没有任何 ``-transparentcolor`` 调用（D-23）。
    本函数暂不直接使用，保留备用。
    """
    r = int(_BG_COLOR[1:3], 16)
    g = int(_BG_COLOR[3:5], 16)
    b = int(_BG_COLOR[5:7], 16)
    return f"#{r:02x}{g:02x}{b:02x}"


class DesktopLyricWindow:
    """桌面歌词独立窗口管理器

    单窗口实现（D-23 修正后的实况）：无边框 + 置顶，透明效果来自整窗
    ``attributes("-alpha", ...)``，底是 ``COLORS["bg_dark"]`` 实色；
    +/- 控件通过 ``_increase_opacity`` / ``_decrease_opacity`` 调同一个 alpha。

    文字与背景**共用**这一个 alpha（不存在"文字不透明"这回事）；
    窗口也**不穿透**：整窗都接收鼠标事件，控制栏上的底色面板不是"为了让穿透的
    背景区域能收事件"而存在的，它只是视觉分区。
    """

    def __init__(self, parent):
        self._parent: ctk.CTk = parent
        # 位置/透明度/锁定/当前行索引由服务侧的状态数据类持有（阶段 1.4-A）
        self._state = LyricWindowState(
            width=DEFAULT_WINDOW_WIDTH, height=DEFAULT_WINDOW_HEIGHT, alpha=DEFAULT_ALPHA
        )
        self._lyric_lines: List[LyricLine] = []

        self._win = self._create_window()
        self._win.withdraw()

    def _create_window(self) -> ctk.CTkToplevel:
        win = ctk.CTkToplevel(self._parent)
        win.title("FMCL 桌面歌词")
        win.attributes("-topmost", True)
        win.attributes("-alpha", self._state.alpha)
        win.overrideredirect(True)

        # 计算屏幕下方居中位置
        x, y = self._calc_center_bottom_position(win, self._state.width, self._state.height)
        win.geometry(f"{self._state.width}x{self._state.height}+{x}+{y}")
        win.configure(fg_color=_BG_COLOR)
        win.protocol("WM_DELETE_WINDOW", self.hide_lyric)

        main = ctk.CTkFrame(win, fg_color="transparent")
        main.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # ── 控制栏 ──
        control_bar = ctk.CTkFrame(main, fg_color="transparent", height=22)
        control_bar.pack(fill=tk.X)
        control_bar.pack_propagate(False)

        self._lock_btn = ctk.CTkButton(
            control_bar,
            text="🔓",
            width=22,
            height=20,
            font=ctk.CTkFont(size=10),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            command=self._toggle_lock,
        )
        self._lock_btn.pack(side=tk.LEFT, padx=(4, 0))

        ctk.CTkButton(
            control_bar,
            text="+",
            width=22,
            height=20,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            command=self._increase_opacity,
        ).pack(side=tk.LEFT)
        ctk.CTkButton(
            control_bar,
            text="-",
            width=22,
            height=20,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            command=self._decrease_opacity,
        ).pack(side=tk.LEFT)

        title_label = ctk.CTkLabel(
            control_bar,
            text="桌面歌词",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        title_label.pack(side=tk.LEFT, padx=10)

        ctk.CTkButton(
            control_bar,
            text="✕",
            width=22,
            height=20,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            hover_color=COLORS["accent"],
            command=self.hide_lyric,
        ).pack(side=tk.RIGHT, padx=4)

        # ── 歌词行 ──
        self._prev_line_label = ctk.CTkLabel(
            main,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            wraplength=580,
        )
        self._prev_line_label.pack(fill=tk.X, pady=(6, 0), padx=10)

        self._current_line_label = ctk.CTkLabel(
            main,
            text="FMCL 音乐播放器",
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["accent"],
            wraplength=580,
        )
        self._current_line_label.pack(fill=tk.X, pady=(0, 6), padx=10)

        self._next_line_label = ctk.CTkLabel(
            main,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            wraplength=580,
        )
        self._next_line_label.pack(fill=tk.X, padx=10)

        # 拖拽绑定
        for w in (control_bar, title_label):
            w.bind("<Button-1>", self._start_drag)
            w.bind("<B1-Motion>", self._drag)

        return win

    # ─── 拖拽 ─────────────────────────────────────────

    def _calc_center_bottom_position(self, win, width: int, height: int) -> tuple:
        """计算屏幕下方居中位置，避开任务栏

        计算规则已搬进 `services.desktop_lyric.calc_center_bottom_position`；
        这里只负责"问 Tk 要屏幕尺寸"（探测失败时沿用原文的 1920x1080 兜底）。
        """
        try:
            screen_w = win.winfo_screenwidth()
            screen_h = win.winfo_screenheight()
        except Exception:
            screen_w, screen_h = FALLBACK_SCREEN_SIZE
        return calc_center_bottom_position(screen_w, screen_h, width, height)

    def _start_drag(self, event):
        if not self._state.is_draggable:
            return
        try:
            self._state.begin_drag(
                event.x_root, event.y_root, self._win.winfo_x(), self._win.winfo_y()
            )
        except Exception:
            pass

    def _drag(self, event):
        if not self._state.is_draggable:
            return
        try:
            x, y = self._state.drag_to(event.x_root, event.y_root)
            self._win.geometry(f"+{x}+{y}")
        except Exception:
            pass

    # ─── 锁定 ─────────────────────────────────────────

    def _toggle_lock(self):
        locked = self._state.toggle_lock()
        self._lock_btn.configure(text="🔒" if locked else "🔓")

    # ─── 透明度 ───────────────────────────────────────

    def _increase_opacity(self):
        alpha = self._state.increase_opacity()
        try:
            self._win.attributes("-alpha", alpha)
        except Exception:
            pass

    def _decrease_opacity(self):
        alpha = self._state.decrease_opacity()
        try:
            self._win.attributes("-alpha", alpha)
        except Exception:
            pass

    # ─── 歌词更新 ─────────────────────────────────────

    def set_lyric_lines(self, lines: List[LyricLine]):
        self._lyric_lines = lines
        self._state.reset_current_line()
        if lines:
            self._current_line_label.configure(text="设置完成，等待播放...")
        else:
            self._current_line_label.configure(text="")

    def update_progress(self, elapsed_ms: int):
        if not self._lyric_lines:
            self._current_line_label.configure(text="🎵")
            self._prev_line_label.configure(text="")
            self._next_line_label.configure(text="")
            return

        current_idx = self._find_current_line(elapsed_ms)
        if current_idx < 0:
            self._current_line_label.configure(text="♫ ...")
            self._prev_line_label.configure(text="")
            nxt_text = self._lyric_lines[0].text if self._lyric_lines else ""
            self._next_line_label.configure(text=nxt_text)
            return

        if current_idx != self._state.current_line_index:
            self._state.current_line_index = current_idx

        current = self._lyric_lines[current_idx]
        self._current_line_label.configure(text=current.text)

        prev_text, nxt_text = neighbor_line_texts(self._lyric_lines, current_idx)
        self._prev_line_label.configure(text=prev_text)
        self._next_line_label.configure(text=nxt_text)

    def _find_current_line(self, elapsed_ms: int) -> int:
        """二分查找当前歌词行（规则已搬进 `services.desktop_lyric.find_current_line`）。"""
        return find_current_line(self._lyric_lines, elapsed_ms)

    # ─── 显示/隐藏 ────────────────────────────────────

    def show_lyric(self):
        try:
            self._win.deiconify()
            self._win.lift()
            self._win.focus_force()
        except Exception:
            pass

    def hide_lyric(self):
        try:
            self._win.withdraw()
        except Exception:
            pass

    def destroy_lyric(self):
        try:
            self._win.withdraw()
            self._win.destroy()
        except Exception:
            pass

    @property
    def is_visible(self) -> bool:
        try:
            return self._win.state() != "withdrawn" and self._win.winfo_exists()
        except Exception:
            return False

    @property
    def is_locked(self) -> bool:
        return self._state.locked

    @property
    def opacity(self) -> float:
        return self._state.alpha
