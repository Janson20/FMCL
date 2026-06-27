"""桌面歌词窗口 - 独立置顶半透明歌词显示"""
import tkinter as tk
import customtkinter as ctk
from typing import Optional, List

from ui.constants import COLORS, FONT_FAMILY
from ui.music_lyrics import LyricParser, LyricLine


class DesktopLyricWindow(ctk.CTkToplevel):
    """桌面歌词独立窗口

    特性:
        - 置顶、半透明（默认 85% 不透明度）
        - 支持拖拽移动
        - 支持锁定时不可移动
        - 支持关闭按钮
        - 当前行高亮 + 渐变过渡
        - 主题跟随主窗口
    """

    def __init__(self, parent):
        super().__init__(parent)
        self._parent: ctk.CTk = parent
        self._locked: bool = False
        self._lyric_lines: List[LyricLine] = []
        self._current_line_index: int = -1
        self._alpha: float = 0.85
        self._drag_data: dict = {"x": 0, "y": 0}

        self._init_ui()

    def _init_ui(self):
        """初始化窗口UI"""
        self.title("FMCL - 桌面歌词")
        self.geometry("600x120+100+100")

        # 置顶
        self.attributes("-topmost", True)
        # 透明度
        self.attributes("-alpha", self._alpha)
        # 无标题栏（保留关闭按钮）
        self.overrideredirect(True)

        # 主框架
        self.configure(fg_color=COLORS["bg_dark"])
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill=tk.BOTH, expand=True, padx=4, pady=4)

        # 顶部控制栏
        control_bar = ctk.CTkFrame(main, fg_color="transparent", height=22)
        control_bar.pack(fill=tk.X)
        control_bar.pack_propagate(False)

        # 锁定按钮
        self._lock_btn = ctk.CTkButton(
            control_bar, text="🔓", width=22, height=20,
            font=ctk.CTkFont(size=10), fg_color="transparent",
            hover_color=COLORS["bg_light"],
            command=self._toggle_lock,
        )
        self._lock_btn.pack(side=tk.LEFT, padx=(4, 0))

        # 透明度调节按钮
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

        # 标题
        title_label = ctk.CTkLabel(
            control_bar,
            text="桌面歌词",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        title_label.pack(side=tk.LEFT, padx=10)

        # 关闭按钮
        close_btn = ctk.CTkButton(
            control_bar, text="✕", width=22, height=20,
            font=ctk.CTkFont(size=11), fg_color="transparent",
            hover_color=COLORS["accent"],
            command=self.hide_lyric,
        )
        close_btn.pack(side=tk.RIGHT, padx=4)

        # 前一行（第二行）歌词
        self._prev_line_label = ctk.CTkLabel(
            main,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            wraplength=580,
        )
        self._prev_line_label.pack(fill=tk.X, pady=(2, 0), padx=10)

        # 当前行歌词（高亮）
        self._current_line_label = ctk.CTkLabel(
            main,
            text="FMCL 音乐播放器",
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["accent"],
            wraplength=580,
        )
        self._current_line_label.pack(fill=tk.X, pady=(0, 2), padx=10)

        # 下一行歌词
        self._next_line_label = ctk.CTkLabel(
            main,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            wraplength=580,
        )
        self._next_line_label.pack(fill=tk.X, padx=10)

        # 绑定拖拽
        control_bar.bind("<Button-1>", self._start_drag)
        control_bar.bind("<B1-Motion>", self._drag)
        title_label.bind("<Button-1>", self._start_drag)
        title_label.bind("<B1-Motion>", self._drag)

        # 防止关闭时的内存泄漏
        self.protocol("WM_DELETE_WINDOW", self.hide_lyric)

        self.withdraw()  # 默认隐藏

    # ─── 拖拽 ─────────────────────────────────────────

    def _start_drag(self, event):
        if self._locked:
            return
        self._drag_data["x"] = event.x
        self._drag_data["y"] = event.y

    def _drag(self, event):
        if self._locked:
            return
        x = self.winfo_x() + (event.x - self._drag_data["x"])
        y = self.winfo_y() + (event.y - self._drag_data["y"])
        self.geometry(f"+{x}+{y}")

    # ─── 锁定 ─────────────────────────────────────────

    def _toggle_lock(self):
        self._locked = not self._locked
        self._lock_btn.configure(text="🔒" if self._locked else "🔓")

    # ─── 透明度 ───────────────────────────────────────

    def _increase_opacity(self):
        self._alpha = min(1.0, self._alpha + 0.05)
        self.attributes("-alpha", self._alpha)

    def _decrease_opacity(self):
        self._alpha = max(0.2, self._alpha - 0.05)
        self.attributes("-alpha", self._alpha)

    # ─── 歌词更新 ─────────────────────────────────────

    def set_lyric_lines(self, lines: List[LyricLine]):
        """设置歌词行列表"""
        self._lyric_lines = lines
        self._current_line_index = -1
        if lines:
            self._current_line_label.configure(
                text="设置完成，等待播放..."
            )
        else:
            self._current_line_label.configure(text="")

    def update_progress(self, elapsed_ms: int):
        """根据播放进度更新歌词显示

        Args:
            elapsed_ms: 当前播放位置 (毫秒)
        """
        if not self._lyric_lines:
            self._current_line_label.configure(text="🎵")
            self._prev_line_label.configure(text="")
            self._next_line_label.configure(text="")
            return

        # 查找当前行
        current_idx = self._find_current_line(elapsed_ms)
        if current_idx < 0:
            self._current_line_label.configure(text="♫ ...")
            self._prev_line_label.configure(text="")
            self._next_line_label.configure(
                text=self._lyric_lines[0].text if self._lyric_lines else ""
            )
            return

        if current_idx != self._current_line_index:
            self._current_line_index = current_idx

        current = self._lyric_lines[current_idx]
        self._current_line_label.configure(text=current.text)

        # 上一行
        if current_idx > 0:
            prev = self._lyric_lines[current_idx - 1]
            self._prev_line_label.configure(text=prev.text)
        else:
            self._prev_line_label.configure(text="")

        # 下一行
        if current_idx + 1 < len(self._lyric_lines):
            nxt = self._lyric_lines[current_idx + 1]
            self._next_line_label.configure(text=nxt.text)
        else:
            self._next_line_label.configure(text="")

    def _find_current_line(self, elapsed_ms: int) -> int:
        """二分查找当前歌词行索引"""
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
        """显示桌面歌词窗口"""
        self.deiconify()
        self.lift()
        self.focus_force()

    def hide_lyric(self):
        """隐藏桌面歌词窗口"""
        self.withdraw()

    def destroy_lyric(self):
        """安全销毁桌面歌词窗口"""
        try:
            self.withdraw()
            self.destroy()
        except Exception:
            pass

    @property
    def is_visible(self) -> bool:
        """是否可见"""
        try:
            return self.state() != "withdrawn" and self.winfo_exists()
        except Exception:
            return False

    @property
    def is_locked(self) -> bool:
        return self._locked

    @property
    def opacity(self) -> float:
        return self._alpha
