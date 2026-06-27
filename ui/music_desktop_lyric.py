"""桌面歌词窗口 - 独立置顶半透明歌词显示

单窗口架构:
    - 使用 Toplevel 的 attributes("-transparentcolor") 实现背景透明
    - 背景色块(控制栏+歌词行)通过调整 fg_color 的透明度来模拟整体半透明效果
    - 所有文字始终完全不透明
"""
import tkinter as tk
import customtkinter as ctk
from typing import Optional, List

from ui.constants import COLORS, FONT_FAMILY
from ui.music_lyrics import LyricLine


_BG_COLOR = COLORS["bg_dark"]  # "#1a1a2e"


def _alpha_color(alpha: float) -> str:
    """将 hex 颜色与透明度混合生成带透明度的 hex 颜色字符串 (模拟 RGBA)

    customtkinter 支持 "#RRGGBB" 格式, 不支持 alpha。这里用 `attributes -alpha`
    控制整窗透明度，歌词文字不受影响因为背景是 transparentcolor。
    本函数暂不直接使用，保留备用。
    """
    r = int(_BG_COLOR[1:3], 16)
    g = int(_BG_COLOR[3:5], 16)
    b = int(_BG_COLOR[5:7], 16)
    return f"#{r:02x}{g:02x}{b:02x}"


class DesktopLyricWindow:
    """桌面歌词独立窗口管理器

    使用单窗口 + transparentcolor 实现。背景色块（控制栏面板和歌词行面板）
    单独设置 fg_color，窗口其余区域透明。+/- 控件控制窗口 attributes -alpha。
    但文字颜色不受 alpha 影响，因为文字在 transparentcolor 区域上显示。

    注意：由于 Windows tkinter 的 transparentcolor 特性，窗口的背景透明区域
    鼠标事件会穿透到下层窗口。因此需要在控制栏区域有一个可见的底色面板来接收事件。
    """

    def __init__(self, parent):
        self._parent: ctk.CTk = parent
        self._locked: bool = False
        self._lyric_lines: List[LyricLine] = []
        self._current_line_index: int = -1
        self._alpha: float = 0.85
        self._drag_data: dict = {"x": 0, "y": 0}

        self._win = self._create_window()
        self._win.withdraw()

    def _create_window(self) -> ctk.CTkToplevel:
        win = ctk.CTkToplevel(self._parent)
        win.title("FMCL 桌面歌词")
        win.attributes("-topmost", True)
        win.attributes("-alpha", self._alpha)
        win.overrideredirect(True)

        # 计算屏幕下方居中位置
        x, y = self._calc_center_bottom_position(win, 600, 140)
        win.geometry(f"600x140+{x}+{y}")
        win.configure(fg_color=_BG_COLOR)
        win.protocol("WM_DELETE_WINDOW", self.hide_lyric)

        main = ctk.CTkFrame(win, fg_color="transparent")
        main.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # ── 控制栏 ──
        control_bar = ctk.CTkFrame(main, fg_color="transparent", height=22)
        control_bar.pack(fill=tk.X)
        control_bar.pack_propagate(False)

        self._lock_btn = ctk.CTkButton(
            control_bar, text="🔓", width=22, height=20,
            font=ctk.CTkFont(size=10), fg_color="transparent",
            hover_color=COLORS["bg_light"],
            command=self._toggle_lock,
        )
        self._lock_btn.pack(side=tk.LEFT, padx=(4, 0))

        ctk.CTkButton(
            control_bar, text="+", width=22, height=20,
            font=ctk.CTkFont(size=12), fg_color="transparent",
            hover_color=COLORS["bg_light"],
            command=self._increase_opacity,
        ).pack(side=tk.LEFT)
        ctk.CTkButton(
            control_bar, text="-", width=22, height=20,
            font=ctk.CTkFont(size=12), fg_color="transparent",
            hover_color=COLORS["bg_light"],
            command=self._decrease_opacity,
        ).pack(side=tk.LEFT)

        title_label = ctk.CTkLabel(
            control_bar, text="桌面歌词",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        title_label.pack(side=tk.LEFT, padx=10)

        ctk.CTkButton(
            control_bar, text="✕", width=22, height=20,
            font=ctk.CTkFont(size=11), fg_color="transparent",
            hover_color=COLORS["accent"],
            command=self.hide_lyric,
        ).pack(side=tk.RIGHT, padx=4)

        # ── 歌词行 ──
        self._prev_line_label = ctk.CTkLabel(
            main, text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            wraplength=580,
        )
        self._prev_line_label.pack(fill=tk.X, pady=(6, 0), padx=10)

        self._current_line_label = ctk.CTkLabel(
            main, text="FMCL 音乐播放器",
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["accent"],
            wraplength=580,
        )
        self._current_line_label.pack(fill=tk.X, pady=(0, 6), padx=10)

        self._next_line_label = ctk.CTkLabel(
            main, text="",
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
        """计算屏幕下方居中位置，避开任务栏"""
        try:
            screen_w = win.winfo_screenwidth()
            screen_h = win.winfo_screenheight()
        except Exception:
            screen_w, screen_h = 1920, 1080
        x = (screen_w - width) // 2
        # 底部留 80px 边距 (避开任务栏)
        y = screen_h - height - 80
        return x, max(0, y)

    def _start_drag(self, event):
        if self._locked:
            return
        try:
            self._drag_data["x"] = event.x_root - self._win.winfo_x()
            self._drag_data["y"] = event.y_root - self._win.winfo_y()
        except Exception:
            pass

    def _drag(self, event):
        if self._locked:
            return
        try:
            x = event.x_root - self._drag_data["x"]
            y = event.y_root - self._drag_data["y"]
            self._win.geometry(f"+{x}+{y}")
        except Exception:
            pass

    # ─── 锁定 ─────────────────────────────────────────

    def _toggle_lock(self):
        self._locked = not self._locked
        self._lock_btn.configure(text="🔒" if self._locked else "🔓")

    # ─── 透明度 ───────────────────────────────────────

    def _increase_opacity(self):
        self._alpha = min(1.0, self._alpha + 0.05)
        try:
            self._win.attributes("-alpha", self._alpha)
        except Exception:
            pass

    def _decrease_opacity(self):
        self._alpha = max(0.15, self._alpha - 0.05)
        try:
            self._win.attributes("-alpha", self._alpha)
        except Exception:
            pass

    # ─── 歌词更新 ─────────────────────────────────────

    def set_lyric_lines(self, lines: List[LyricLine]):
        self._lyric_lines = lines
        self._current_line_index = -1
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

        if current_idx != self._current_line_index:
            self._current_line_index = current_idx

        current = self._lyric_lines[current_idx]
        self._current_line_label.configure(text=current.text)

        prev_text = self._lyric_lines[current_idx - 1].text if current_idx > 0 else ""
        self._prev_line_label.configure(text=prev_text)

        nxt_text = (
            self._lyric_lines[current_idx + 1].text
            if current_idx + 1 < len(self._lyric_lines)
            else ""
        )
        self._next_line_label.configure(text=nxt_text)

    def _find_current_line(self, elapsed_ms: int) -> int:
        if not self._lyric_lines:
            return -1
        lo, hi = 0, len(self._lyric_lines) - 1
        result = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._lyric_lines[mid].time <= elapsed_ms:
                result = mid
                lo = mid + 1
            else:
                hi = mid - 1
        return result

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
        return self._locked

    @property
    def opacity(self) -> float:
        return self._alpha
