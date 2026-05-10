"""UI 窗口类 - 独立弹窗和对话框"""
import os
import threading
import time
from typing import List, Dict, Optional, Callable, Any, Literal

import customtkinter as ctk
import requests
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY, _get_fmcl_version

try:
    from tkinterdnd2 import DND_FILES
    HAS_DND: bool = True
except ImportError:
    HAS_DND = False

_app_ref: Optional[ctk.CTk] = None

NotifyType = Literal["info", "success", "warning", "error"]

NOTIFY_BORDER_COLORS: Dict[NotifyType, str] = {
    "info": "#4a9eff",
    "success": "#2ecc71",
    "warning": "#f39c12",
    "error": "#e74c3c",
}


def set_app_reference(app: ctk.CTk) -> None:
    global _app_ref
    _app_ref = app


def show_notification(icon: str, title: str, subtitle: str = "",
                      notify_type: NotifyType = "info",
                      duration_ms: int = 4500) -> None:
    border_color = NOTIFY_BORDER_COLORS.get(notify_type, NOTIFY_BORDER_COLORS["info"])

    if _app_ref is None:
        logger.warning("show_notification: app reference not set, cannot display notification")
        return

    app = _app_ref
    if not app.winfo_exists():
        return

    app.after(0, lambda: _show_notification_impl(app, icon, title, subtitle, border_color, duration_ms))


def _show_notification_impl(parent, icon: str, title: str, subtitle: str,
                            border_color: str, duration_ms: int) -> None:
    toast = ctk.CTkToplevel(parent)
    toast.overrideredirect(True)
    toast.attributes('-topmost', True)
    toast.configure(fg_color=COLORS["card_bg"])
    toast.transient(parent)

    w, h = 280, 72
    toast.geometry(f"{w}x{h}")

    border_frame = ctk.CTkFrame(toast, fg_color=border_color, corner_radius=8)
    border_frame.pack(fill=ctk.BOTH, expand=True, padx=1, pady=1)

    inner = ctk.CTkFrame(border_frame, fg_color=COLORS["card_bg"], corner_radius=7)
    inner.pack(fill=ctk.BOTH, expand=True, padx=2, pady=2)

    icon_label = ctk.CTkLabel(
        inner, text=icon,
        font=ctk.CTkFont(size=24),
        text_color=COLORS["text_primary"],
        width=40,
    )
    icon_label.pack(side=ctk.LEFT, padx=(12, 4), pady=10)

    text_frame = ctk.CTkFrame(inner, fg_color="transparent")
    text_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 12), pady=8)

    ctk.CTkLabel(
        text_frame, text=subtitle if subtitle else "",
        font=ctk.CTkFont(family=FONT_FAMILY, size=10),
        text_color=border_color,
        anchor=ctk.W,
    ).pack(fill=ctk.X)

    ctk.CTkLabel(
        text_frame, text=title,
        font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
        text_color=COLORS["text_primary"],
        anchor=ctk.W,
    ).pack(fill=ctk.X)

    toast.update_idletasks()
    pw = parent.winfo_width()
    ph = parent.winfo_height()
    px = parent.winfo_x()
    py = parent.winfo_y()

    offset = 0
    for existing in _toast_queue[:]:
        try:
            if existing.winfo_exists():
                offset += existing.winfo_height() + 8
            else:
                _toast_queue.remove(existing)
        except Exception:
            _toast_queue.remove(existing)

    x = px + pw - w - 16
    y = py + ph - h - 16 - offset
    toast.geometry(f"{w}x{h}+{x}+{y}")

    _toast_queue.append(toast)

    def _fade_out():
        try:
            for alpha in range(100, -1, -5):
                if not toast.winfo_exists():
                    break
                toast.attributes('-alpha', alpha / 100.0)
                toast.update()
                time.sleep(0.015)
        except Exception:
            pass
        finally:
            try:
                if toast.winfo_exists():
                    toast.destroy()
            except Exception:
                pass
            if toast in _toast_queue:
                _toast_queue.remove(toast)

    toast.after(duration_ms, _fade_out)


def show_confirmation(message: str, title: str = "确认") -> bool:
    """显示确认对话框"""
    result = [False]

    dialog = ctk.CTkToplevel()
    dialog.title(title)
    dialog.geometry("400x200")
    dialog.configure(fg_color=COLORS["bg_dark"])
    dialog.grab_set()

    # 居中
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() - 400) // 2
    y = (dialog.winfo_screenheight() - 200) // 2
    dialog.geometry(f"400x200+{x}+{y}")

    ctk.CTkLabel(
        dialog,
        text=message,
        font=ctk.CTkFont(family=FONT_FAMILY, size=14),
        text_color=COLORS["text_primary"],
        wraplength=350,
    ).pack(pady=(30, 20))

    btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
    btn_frame.pack()

    def on_yes():
        result[0] = True
        dialog.grab_release()
        dialog.destroy()

    def on_no():
        result[0] = False
        dialog.grab_release()
        dialog.destroy()

    ctk.CTkButton(
        btn_frame,
        text="确认",
        width=80,
        fg_color=COLORS["accent"],
        hover_color=COLORS["accent_hover"],
        command=on_yes,
    ).pack(side=ctk.LEFT, padx=10)

    ctk.CTkButton(
        btn_frame,
        text="取消",
        width=80,
        fg_color=COLORS["bg_medium"],
        hover_color=COLORS["card_border"],
        command=on_no,
    ).pack(side=ctk.LEFT, padx=10)

    dialog.wait_window()
    return result[0]


def show_alert(message: str, title: str = "提示") -> None:
    """显示提示对话框"""
    dialog = ctk.CTkToplevel()
    dialog.title(title)
    dialog.geometry("400x180")
    dialog.configure(fg_color=COLORS["bg_dark"])
    dialog.grab_set()

    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() - 400) // 2
    y = (dialog.winfo_screenheight() - 180) // 2
    dialog.geometry(f"400x180+{x}+{y}")

    ctk.CTkLabel(
        dialog,
        text=message,
        font=ctk.CTkFont(family=FONT_FAMILY, size=14),
        text_color=COLORS["text_primary"],
        wraplength=350,
    ).pack(pady=(30, 20))

    ctk.CTkButton(
        dialog,
        text="确定",
        width=100,
        fg_color=COLORS["accent"],
        hover_color=COLORS["accent_hover"],
        command=lambda: (dialog.grab_release(), dialog.destroy()),
    ).pack()

    dialog.wait_window()


class VersionSelectorDialog(ctk.CTkToplevel):
    """版本选择对话框（弹出窗口）"""

    def __init__(self, parent, versions: List[Dict[str, Any]], title: str = "选择版本"):
        super().__init__(parent)
        self.title(title)
        self.geometry("600x500")
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        self.grab_set()

        self.selected_version: Optional[str] = None
        self._versions = versions

        self._build_ui()
        self._center_on_parent(parent)

    def _center_on_parent(self, parent):
        """在父窗口居中"""
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 600, 500
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _build_ui(self):
        """构建对话框UI"""
        # 搜索框
        search_frame = ctk.CTkFrame(self, fg_color="transparent", height=40)
        search_frame.pack(fill=ctk.X, padx=15, pady=(15, 10))

        self.search_entry = ctk.CTkEntry(
            search_frame,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="🔍 搜索版本...",
        )
        self.search_entry.pack(fill=ctk.X)
        self.search_entry.bind("<KeyRelease>", self._on_search)

        # 版本列表
        self.list_frame = ctk.CTkScrollableFrame(
            self, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        self.list_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=(0, 10))

        self._render_versions(self._versions)

        # 按钮区
        btn_frame = ctk.CTkFrame(self, fg_color="transparent", height=45)
        btn_frame.pack(fill=ctk.X, padx=15, pady=(0, 15))

        ctk.CTkButton(
            btn_frame,
            text="取消",
            width=100,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["card_border"],
            command=self._on_cancel,
        ).pack(side=ctk.RIGHT, padx=(10, 0))

        ctk.CTkButton(
            btn_frame,
            text="选择",
            width=100,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_select,
        ).pack(side=ctk.RIGHT)

    def _render_versions(self, versions: List[Dict[str, Any]]):
        """渲染版本列表"""
        for w in self.list_frame.winfo_children():
            w.destroy()

        for ver in versions:
            ver_id = ver.get("id", "Unknown")
            ver_type = ver.get("type", "")
            type_icon = "📦" if ver_type == "release" else "🔬" if ver_type == "snapshot" else "❓"

            btn = ctk.CTkButton(
                self.list_frame,
                text=f"{type_icon} {ver_id}  ({ver_type})",
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
                height=32,
                command=lambda v=ver_id: self._click_version(v),
            )
            btn.pack(fill=ctk.X, pady=2)

    def _on_search(self, event=None):
        """搜索过滤"""
        keyword = self.search_entry.get().strip().lower()
        if not keyword:
            filtered = self._versions
        else:
            filtered = [
                v for v in self._versions
                if keyword in str(v.get("id", "")).lower()
            ]
        self._render_versions(filtered)

    def _click_version(self, version_id: str):
        """点击版本"""
        self.selected_version = version_id

    def _on_select(self):
        """确认选择"""
        self.grab_release()
        self.destroy()

    def _on_cancel(self):
        """取消"""
        self.selected_version = None
        self.grab_release()
        self.destroy()


NOTICE_URL = "https://jingdu.qzz.io/static/fmcl-notice.txt"


def fetch_notice() -> Optional[str]:
    try:
        resp = requests.get(NOTICE_URL, timeout=10)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        text = resp.text.strip()
        return text if text else None
    except Exception as e:
        logger.warning(f"获取公告失败: {e}")
        return None


def show_notice_dialog(parent, content: str, on_dismiss=None) -> None:
    from ui.i18n import _
    dialog = ctk.CTkToplevel(parent)
    dialog.title(_("notice_title"))
    dialog.configure(fg_color=COLORS["bg_dark"])
    dialog.transient(parent)
    dialog.grab_set()

    w, h = 500, 400
    dialog.geometry(f"{w}x{h}")
    dialog.update_idletasks()
    x = (dialog.winfo_screenwidth() - w) // 2
    y = (dialog.winfo_screenheight() - h) // 2
    dialog.geometry(f"{w}x{h}+{x}+{y}")

    def _on_dismiss():
        dialog.grab_release()
        dialog.destroy()
        if on_dismiss:
            on_dismiss()

    ctk.CTkLabel(
        dialog,
        text=f"📢 {_('notice_title')}",
        font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
        text_color=COLORS["accent"],
    ).pack(pady=(20, 10))

    text_frame = ctk.CTkScrollableFrame(
        dialog,
        fg_color=COLORS["bg_medium"],
    )
    text_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=(0, 10))

    ctk.CTkLabel(
        text_frame,
        text=content,
        font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        text_color=COLORS["text_primary"],
        wraplength=440,
        justify=ctk.LEFT,
        anchor=ctk.W,
    ).pack(fill=ctk.BOTH, expand=True, padx=10, pady=10)

    ctk.CTkButton(
        dialog,
        text=_("confirm"),
        width=100,
        height=35,
        font=ctk.CTkFont(family=FONT_FAMILY, size=13),
        fg_color=COLORS["accent"],
        hover_color=COLORS["accent_hover"],
        command=_on_dismiss,
    ).pack(pady=(0, 20))


_toast_queue: List[ctk.CTkToplevel] = []


def show_toast_notification(parent, icon: str, title: str, subtitle: str = "",
                            duration_ms: int = 4500) -> None:
    """显示右下角 Toast 通知弹窗（成就解锁等）

    Args:
        parent: 父窗口
        icon: 图标 emoji
        title: 标题（成就名称）
        subtitle: 副标题（阶段名称）
        duration_ms: 显示时长(毫秒)
    """
    toast = ctk.CTkToplevel(parent)
    toast.overrideredirect(True)
    toast.attributes('-topmost', True)
    toast.configure(fg_color=COLORS["card_bg"])
    toast.transient(parent)

    w, h = 280, 72
    toast.geometry(f"{w}x{h}")

    border_frame = ctk.CTkFrame(toast, fg_color=COLORS["accent"], corner_radius=8)
    border_frame.pack(fill=ctk.BOTH, expand=True, padx=1, pady=1)

    inner = ctk.CTkFrame(border_frame, fg_color=COLORS["card_bg"], corner_radius=7)
    inner.pack(fill=ctk.BOTH, expand=True, padx=2, pady=2)

    icon_label = ctk.CTkLabel(
        inner, text=icon,
        font=ctk.CTkFont(size=24),
        text_color=COLORS["text_primary"],
        width=40,
    )
    icon_label.pack(side=ctk.LEFT, padx=(12, 4), pady=10)

    text_frame = ctk.CTkFrame(inner, fg_color="transparent")
    text_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 12), pady=8)

    ctk.CTkLabel(
        text_frame, text=subtitle if subtitle else "",
        font=ctk.CTkFont(family=FONT_FAMILY, size=10),
        text_color=COLORS["accent"],
        anchor=ctk.W,
    ).pack(fill=ctk.X)

    ctk.CTkLabel(
        text_frame, text=title,
        font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
        text_color=COLORS["text_primary"],
        anchor=ctk.W,
    ).pack(fill=ctk.X)

    toast.update_idletasks()
    pw = parent.winfo_width()
    ph = parent.winfo_height()
    px = parent.winfo_x()
    py = parent.winfo_y()

    offset = 0
    for existing in _toast_queue[:]:
        try:
            if existing.winfo_exists():
                offset += existing.winfo_height() + 8
            else:
                _toast_queue.remove(existing)
        except Exception:
            _toast_queue.remove(existing)

    x = px + pw - w - 16
    y = py + ph - h - 16 - offset
    toast.geometry(f"{w}x{h}+{x}+{y}")

    _toast_queue.append(toast)

    def _fade_out():
        try:
            for alpha in range(100, -1, -5):
                if not toast.winfo_exists():
                    break
                toast.attributes('-alpha', alpha / 100.0)
                toast.update()
                time.sleep(0.015)
        except Exception:
            pass
        finally:
            try:
                if toast.winfo_exists():
                    toast.destroy()
            except Exception:
                pass
            if toast in _toast_queue:
                _toast_queue.remove(toast)

    toast.after(duration_ms, _fade_out)
