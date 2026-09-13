"""服务器配置编辑窗口 - 用图形界面编辑单个服务器的 server.properties

设计目标（对新手友好）：
- 左侧按用途分类，右侧每项都显示「名称 + 键名 + 一句话说明 + 取值范围」
- 开关 / 下拉 / 输入框三种最直观的控件，不用记配置语法
- 搜索框可以按名称、键名或说明文字快速找到配置项
- 保存前不会写盘，随时可以「重新加载」或「恢复默认」
- 需要写原文本的高级用户可以在「原始文件」分类里直接编辑
"""

import os
import subprocess
import sys
import tkinter.messagebox as messagebox
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk
from logzero import logger

from launcher.server_config import (
    format_properties,
    get_server_launch_memory,
    get_server_properties_path,
    read_eula,
    read_server_properties,
    set_server_launch_memory,
    write_server_properties,
)
from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _
from ui.server_config_schema import (
    ALL_OPTIONS,
    BOOL,
    CATEGORIES,
    CONFIG_OPTIONS,
    ENUM,
    INFO,
    INT,
    MEMORY,
    STR,
    get_options,
)

WINDOW_WIDTH = 1000
WINDOW_HEIGHT = 700


class ServerConfigEditorWindow(ctk.CTkToplevel):
    """单个服务器的配置编辑窗口"""

    def __init__(self, parent, version_id: str, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self._fix_customtkinter_icon(self)

        self.version_id = version_id
        self.callbacks = callbacks
        self.parent_app = parent

        self.title(_("server_config_title", version=version_id))
        self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}")
        self.minsize(880, 600)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)

        self.update_idletasks()
        try:
            px, py = parent.winfo_x(), parent.winfo_y()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            x = px + max(0, (pw - WINDOW_WIDTH) // 2)
            y = py + max(0, (ph - WINDOW_HEIGHT) // 2)
            self.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{x}+{y}")
        except Exception:
            pass

        # ── 路径 ──
        self.server_dir: Path = self._resolve_server_dir()
        self.props_path: Path = get_server_properties_path(self.server_dir)

        # ── 状态 ──
        self._file_values: Dict[str, str] = {}  # 磁盘上的原始内容
        self._widget_values: Dict[str, str] = {}  # 表单当前值（含未写入文件的默认值）
        self._touched: set = set()  # 用户动过的键
        self._widgets: Dict[str, Dict[str, Any]] = {}  # 控件引用
        self._file_missing = False
        self._memory_value = "2G"
        self._original_memory = "2G"
        self._eula_agreed = False
        self._active_category = "world"
        self._search_text = ""
        self._nav_buttons: Dict[str, Any] = {}
        self._dirty_count = 0

        self._build_ui()
        self._load_from_disk(initial=True)

    # ─── 初始化辅助 ──────────────────────────────────────────

    @staticmethod
    def _fix_customtkinter_icon(toplevel):
        """修复 CTkToplevel 因内置图标延迟回调崩溃的问题"""
        import types

        try:
            icon_path = Path(__file__).parent.parent.parent / "icon.ico"
            icon_str = str(icon_path) if icon_path.exists() else ""
        except Exception:
            icon_str = ""

        original = toplevel.iconbitmap

        def safe_iconbitmap(_self, bitmap=None, default=None):
            try:
                if bitmap is not None:
                    return original(bitmap=bitmap)
                if default is not None:
                    return original(default=default)
                return original()
            except Exception:
                pass

        toplevel.iconbitmap = types.MethodType(safe_iconbitmap, toplevel)

        if icon_str:
            try:
                original(bitmap=icon_str)
            except Exception:
                pass

    def _resolve_server_dir(self) -> Path:
        """服务器目录：<服务器根目录>/<版本 ID>/"""
        root = Path(".")
        try:
            if "get_server_dir" in self.callbacks:
                root = Path(self.callbacks["get_server_dir"]())
        except Exception as e:
            logger.error(f"获取服务器目录失败: {e}")
        return root / self.version_id

    def _is_server_running(self) -> bool:
        try:
            if "is_server_running" in self.callbacks:
                return bool(self.callbacks["is_server_running"]())
        except Exception:
            pass
        return False

    def _parent_memory_hint(self) -> Optional[str]:
        """服务器还没有独立内存设置时，沿用开服页面当前选中的内存值"""
        try:
            selected = getattr(self.parent_app, "selected_server_version", None)
            var = getattr(self.parent_app, "server_memory_var", None)
            if selected == self.version_id and var is not None:
                value = str(var.get()).strip()
                if value:
                    return value
        except Exception:
            pass
        return None

    # ─── 界面构建 ────────────────────────────────────────────

    def _build_ui(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill=ctk.BOTH, expand=True, padx=15, pady=12)

        self._build_header(main)
        self._build_body(main)
        self._build_footer(main)

    def _build_header(self, parent):
        header = ctk.CTkFrame(parent, fg_color="transparent")
        header.pack(fill=ctk.X)

        title_row = ctk.CTkFrame(header, fg_color="transparent")
        title_row.pack(fill=ctk.X)

        ctk.CTkLabel(
            title_row,
            text=_("server_config_title", version=self.version_id),
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self._state_label = ctk.CTkLabel(
            title_row,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._state_label.pack(side=ctk.RIGHT)

        path_row = ctk.CTkFrame(header, fg_color="transparent")
        path_row.pack(fill=ctk.X, pady=(3, 4))

        self._path_label = ctk.CTkLabel(
            path_row,
            text=f"📁 {self.props_path}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
        )
        self._path_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        self._dirty_label = ctk.CTkLabel(
            path_row,
            text=_("server_config_no_changes"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._dirty_label.pack(side=ctk.RIGHT, padx=(8, 0))

        ctk.CTkLabel(
            header,
            text=_("server_config_subtitle"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
            justify=ctk.LEFT,
        ).pack(fill=ctk.X, pady=(0, 8))

        search_row = ctk.CTkFrame(header, fg_color="transparent")
        search_row.pack(fill=ctk.X, pady=(0, 8))

        self._search_entry = ctk.CTkEntry(
            search_row,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            placeholder_text=_("server_config_search_placeholder"),
        )
        self._search_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True)
        self._search_entry.bind("<KeyRelease>", self._on_search)

        self._search_label = ctk.CTkLabel(
            search_row,
            text="",
            width=190,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            anchor=ctk.E,
        )
        self._search_label.pack(side=ctk.RIGHT, padx=(8, 0))

    def _build_body(self, parent):
        body = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12)
        body.pack(fill=ctk.BOTH, expand=True)

        nav = ctk.CTkFrame(body, fg_color="transparent", width=160)
        nav.pack(side=ctk.LEFT, fill=ctk.Y, padx=(10, 6), pady=10)
        nav.pack_propagate(False)

        for category in CATEGORIES:
            btn = ctk.CTkButton(
                nav,
                text=_(category["i18n"]),
                anchor=ctk.W,
                height=34,
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_primary"],
                command=lambda c=category["id"]: self._on_category(c),
            )
            btn.pack(fill=ctk.X, pady=2)
            self._nav_buttons[category["id"]] = btn

        ctk.CTkFrame(body, fg_color=COLORS["card_border"], width=1).pack(side=ctk.LEFT, fill=ctk.Y, pady=10)

        self._content = ctk.CTkFrame(body, fg_color="transparent")
        self._content.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=10, pady=10)

        # 表单视图
        self._options_frame = ctk.CTkScrollableFrame(
            self._content, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )

        # 原始文件视图
        self._raw_frame = ctk.CTkFrame(self._content, fg_color="transparent")

        ctk.CTkLabel(
            self._raw_frame,
            text=_("server_config_raw_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        ).pack(fill=ctk.X)

        ctk.CTkLabel(
            self._raw_frame,
            text=_("server_config_raw_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
            justify=ctk.LEFT,
            wraplength=740,
        ).pack(fill=ctk.X, pady=(3, 8))

        self._raw_textbox = ctk.CTkTextbox(
            self._raw_frame,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            wrap=ctk.NONE,
        )
        self._raw_textbox.pack(fill=ctk.BOTH, expand=True)

        raw_buttons = ctk.CTkFrame(self._raw_frame, fg_color="transparent")
        raw_buttons.pack(fill=ctk.X, pady=(8, 0))

        ctk.CTkButton(
            raw_buttons,
            text=_("server_config_raw_write_btn"),
            width=150,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_raw_write,
        ).pack(side=ctk.RIGHT)

    def _build_footer(self, parent):
        footer = ctk.CTkFrame(parent, fg_color="transparent")
        footer.pack(fill=ctk.X, pady=(10, 0))

        self._status_label = ctk.CTkLabel(
            footer,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
            justify=ctk.LEFT,
        )
        self._status_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        self._close_btn = ctk.CTkButton(
            footer,
            text=_("server_config_close_btn"),
            width=80,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_close,
        )
        self._close_btn.pack(side=ctk.RIGHT, padx=(8, 0))

        self._save_btn = ctk.CTkButton(
            footer,
            text=_("server_config_save_btn"),
            width=150,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_save,
        )
        self._save_btn.pack(side=ctk.RIGHT, padx=(8, 0))

        self._reload_btn = ctk.CTkButton(
            footer,
            text=_("server_config_reload_btn"),
            width=110,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_reload,
        )
        self._reload_btn.pack(side=ctk.RIGHT, padx=(8, 0))

        self._reset_btn = ctk.CTkButton(
            footer,
            text=_("server_config_reset_btn"),
            width=110,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_reset_all,
        )
        self._reset_btn.pack(side=ctk.RIGHT, padx=(8, 0))

    # ─── 读取 / 状态 ─────────────────────────────────────────

    def _load_from_disk(self, initial: bool = False):
        """从磁盘读取配置并刷新界面"""
        try:
            self._file_missing = not self.props_path.exists()
            raw = read_server_properties(self.server_dir)

            self._file_values = dict(raw)
            self._widget_values = {}
            for option in CONFIG_OPTIONS:
                key = option["key"]
                self._widget_values[key] = raw.get(key, str(option["default"]))

            self._touched.clear()
            memory = get_server_launch_memory(self.server_dir) or self._parent_memory_hint()
            self._memory_value = memory or "2G"
            self._original_memory = self._memory_value
            self._eula_agreed = read_eula(self.server_dir)
        except Exception as e:
            logger.error(f"读取服务器配置失败: {e}")
            self._set_status(_("server_config_load_failed", error=str(e)))
            return

        self._refresh_state_label()
        self._refresh_view(sync=False)
        self._update_dirty()

        if not initial:
            self._set_status(_("server_config_reloaded"))

    def _refresh_state_label(self):
        """刷新顶部的运行状态 / 文件缺失提示"""
        parts = []
        if self._is_server_running():
            parts.append(_("server_config_running_warning"))
        else:
            parts.append(_("server_config_not_running"))
        if self._file_missing:
            parts.append(_("server_config_file_missing"))
        try:
            self._state_label.configure(text="   ".join(parts))
        except Exception:
            pass

    def _set_status(self, text: str, color: Optional[str] = None):
        try:
            if self._status_label.winfo_exists():
                self._status_label.configure(text=text, text_color=color or COLORS["text_secondary"])
        except Exception:
            pass

    def _current_write_values(self) -> Dict[str, str]:
        """当前真正需要写入 server.properties 的键值对"""
        values: Dict[str, str] = {}
        for key, value in self._file_values.items():
            if key not in self._widget_values:
                values[key] = value  # 保留编辑器不认识的键
        for key, value in self._widget_values.items():
            if self._file_missing or key in self._file_values or key in self._touched:
                values[key] = str(value)
        return values

    def _dirty_keys(self) -> List[str]:
        """发生变化的 server.properties 键"""
        current = self._current_write_values()
        if self._file_missing:
            return [k for k in current if k in self._touched]
        dirty = []
        for key, value in current.items():
            if self._file_values.get(key) != value:
                dirty.append(key)
        for key in self._file_values:
            if key not in current:
                dirty.append(key)
        return dirty

    def _update_dirty(self):
        """刷新「已修改 N 项」计数与按钮状态"""
        count = len(self._dirty_keys())
        if self._memory_value != self._original_memory:
            count += 1
        self._dirty_count = count

        try:
            if count:
                self._dirty_label.configure(
                    text=_("server_config_modified", count=count), text_color=COLORS["warning"]
                )
            else:
                self._dirty_label.configure(text=_("server_config_no_changes"), text_color=COLORS["text_secondary"])
        except Exception:
            pass

    # ─── 视图切换 ────────────────────────────────────────────

    def _on_category(self, category_id: str):
        if category_id == self._active_category and not self._search_text:
            return
        self._sync_widgets()
        self._active_category = category_id
        if self._search_text:
            self._search_text = ""
            try:
                self._search_entry.delete(0, ctk.END)
            except Exception:
                pass
        self._refresh_view()

    def _on_search(self, event=None):
        self._sync_widgets()
        try:
            self._search_text = self._search_entry.get().strip()
        except Exception:
            self._search_text = ""

        if self._search_text and self._active_category == "raw":
            self._active_category = "world"

        self._refresh_view(sync=False)

    def _refresh_view(self, sync: bool = True):
        """根据当前分类 / 搜索词重建右侧内容"""
        if sync:
            self._sync_widgets()

        raw_mode = self._active_category == "raw" and not self._search_text

        if raw_mode:
            self._options_frame.pack_forget()
            self._raw_frame.pack(fill=ctk.BOTH, expand=True)
            self._fill_raw_text()
            try:
                self._search_label.configure(text="")
            except Exception:
                pass
        else:
            self._raw_frame.pack_forget()
            self._options_frame.pack(fill=ctk.BOTH, expand=True)
            self._render_options()

        self._update_nav_highlight()

    def _update_nav_highlight(self):
        for category_id, btn in self._nav_buttons.items():
            try:
                if category_id == self._active_category and not self._search_text:
                    btn.configure(fg_color=COLORS["bg_light"], text_color=COLORS["accent"])
                else:
                    btn.configure(fg_color="transparent", text_color=COLORS["text_primary"])
            except Exception:
                pass

    def _visible_options(self) -> List[Dict[str, Any]]:
        """当前应该显示的配置项列表"""
        if self._search_text:
            needle = self._search_text.lower()
            matched = []
            for option in ALL_OPTIONS:
                name = _(option["i18n"] + "_n")
                desc = _(option["i18n"] + "_d")
                if (
                    needle in name.lower()
                    or needle in desc.lower()
                    or needle in str(option["key"]).lower()
                    or needle in _(option["i18n"] + "_n").lower()
                ):
                    matched.append(option)
            return matched
        return get_options(self._active_category)

    # ─── 渲染配置项 ──────────────────────────────────────────

    def _render_options(self):
        for widget in self._options_frame.winfo_children():
            widget.destroy()
        self._widgets.clear()

        options = self._visible_options()
        if not options:
            ctk.CTkLabel(
                self._options_frame,
                text=_("server_config_search_empty"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=40)
            try:
                self._search_label.configure(text=_("server_config_search_result", count=0))
            except Exception:
                pass
            return

        try:
            if self._search_text:
                self._search_label.configure(text=_("server_config_search_result", count=len(options)))
            else:
                self._search_label.configure(text="")
        except Exception:
            pass

        # 搜索时按分类分组；分类浏览时把常用项与高级项分开
        if self._search_text:
            for category in CATEGORIES:
                group = [o for o in options if o["category"] == category["id"]]
                if not group:
                    continue
                self._add_group_header(_(category["i18n"]), len(group))
                for option in group:
                    self._create_option_row(option)
        else:
            basic = [o for o in options if not o["advanced"]]
            advanced = [o for o in options if o["advanced"]]
            if basic:
                self._add_group_header(_("server_config_group_basic"), len(basic))
                for option in basic:
                    self._create_option_row(option)
            if advanced:
                self._add_group_header(_("server_config_group_advanced"), len(advanced))
                for option in advanced:
                    self._create_option_row(option)

    def _add_group_header(self, text: str, count: int):
        frame = ctk.CTkFrame(self._options_frame, fg_color="transparent")
        frame.pack(fill=ctk.X, pady=(8, 4), padx=2)

        ctk.CTkLabel(
            frame,
            text=f"{text}  ({count})",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
        ).pack(side=ctk.LEFT)

    def _create_option_row(self, option: Dict[str, Any]):
        key = option["key"]
        kind = option["kind"]
        i18n = option["i18n"]

        row = ctk.CTkFrame(self._options_frame, fg_color=COLORS["bg_medium"], corner_radius=8)
        row.pack(fill=ctk.X, pady=3, padx=2)

        info = ctk.CTkFrame(row, fg_color="transparent")
        info.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(12, 6), pady=8)

        title_row = ctk.CTkFrame(info, fg_color="transparent")
        title_row.pack(fill=ctk.X)

        ctk.CTkLabel(
            title_row,
            text=_(i18n + "_n"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        ).pack(side=ctk.LEFT)

        if option["advanced"]:
            ctk.CTkLabel(
                title_row,
                text=f" {_('server_config_advanced_badge')} ",
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["bg_dark"],
                fg_color=COLORS["warning"],
                corner_radius=4,
            ).pack(side=ctk.LEFT, padx=(6, 0))

        if not key.startswith("@"):
            ctk.CTkLabel(
                title_row,
                text=f"  {key}",
                font=ctk.CTkFont(family="Consolas", size=10),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(side=ctk.LEFT)

        desc_text = _(i18n + "_d")
        if kind == INT and option["min"] is not None and option["max"] is not None:
            desc_text += "   " + _("server_config_int_range", min=option["min"], max=option["max"])

        ctk.CTkLabel(
            info,
            text=desc_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
            justify=ctk.LEFT,
            wraplength=470,
        ).pack(fill=ctk.X, pady=(2, 0))

        controls = ctk.CTkFrame(row, fg_color="transparent")
        controls.pack(side=ctk.RIGHT, padx=(4, 12), pady=8)

        if kind == BOOL:
            self._create_bool_control(controls, option)
        elif kind == INT or kind == STR:
            self._create_entry_control(controls, option)
        elif kind == ENUM:
            self._create_enum_control(controls, option)
        elif kind == MEMORY:
            self._create_memory_control(controls, option)
        elif kind == INFO:
            self._create_info_control(controls, option)

        # 单项恢复默认
        if kind != INFO:
            ctk.CTkButton(
                controls,
                text="↺",
                width=30,
                height=30,
                font=ctk.CTkFont(size=14),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_secondary"],
                command=lambda o=option: self._on_reset_one(o),
            ).pack(side=ctk.LEFT, padx=(6, 0))
            try:
                self._attach_tip(controls.winfo_children()[-1], _("server_config_reset_tip"))
            except Exception:
                pass

        self._widgets[key] = {**self._widgets.get(key, {}), "kind": kind, "row": row}

    def _attach_tip(self, widget, tip: str):
        """给控件挂一个悬停提示（显示在窗口底部状态栏）

        CTkButton 的鼠标事件落在内部 canvas 上，因此需要一并绑定子控件。
        """
        targets = [widget]

        def _collect(current):
            for child in current.winfo_children():
                targets.append(child)
                _collect(child)

        try:
            _collect(widget)
        except Exception:
            pass

        for target in targets:
            try:
                target.bind("<Enter>", lambda _e, t=tip: self._set_status(t), add="+")
                target.bind("<Leave>", lambda _e: self._set_status(""), add="+")
            except Exception:
                continue

    def _create_bool_control(self, parent, option: Dict[str, Any]):
        key = option["key"]
        value = str(self._widget_values.get(key, option["default"])).strip().lower() == "true"

        var = ctk.BooleanVar(value=value)
        switch = ctk.CTkSwitch(
            parent,
            text=_("server_config_bool_on") if value else _("server_config_bool_off"),
            variable=var,
            width=120,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_primary"] if value else COLORS["text_secondary"],
            progress_color=COLORS["success"],
            fg_color=COLORS["card_border"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["text_secondary"],
            command=lambda k=key: self._on_bool_toggle(k),
        )
        switch.pack(side=ctk.LEFT)

        self._widgets[key] = {"kind": BOOL, "var": var, "switch": switch}

    def _on_bool_toggle(self, key: str):
        widget = self._widgets.get(key, {})
        var = widget.get("var")
        switch = widget.get("switch")
        if var is None or switch is None:
            return
        try:
            value = bool(var.get())
            switch.configure(
                text=_("server_config_bool_on") if value else _("server_config_bool_off"),
                text_color=COLORS["text_primary"] if value else COLORS["text_secondary"],
            )
        except Exception:
            pass
        self._widget_values[key] = "true" if var.get() else "false"
        self._touched.add(key)
        self._update_dirty()

    def _create_entry_control(self, parent, option: Dict[str, Any]):
        key = option["key"]
        value = str(self._widget_values.get(key, option["default"]))

        entry = ctk.CTkEntry(
            parent,
            width=200,
            height=32,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            justify=ctk.LEFT if option["kind"] == STR else ctk.CENTER,
        )
        entry.insert(0, value)
        entry.pack(side=ctk.LEFT)
        entry.bind("<KeyRelease>", lambda _e, k=key: self._on_entry_change(k))
        entry.bind("<FocusOut>", lambda _e, k=key: self._on_entry_change(k))

        self._widgets[key] = {"kind": option["kind"], "entry": entry}

    def _on_entry_change(self, key: str):
        widget = self._widgets.get(key, {})
        entry = widget.get("entry")
        if entry is None:
            return
        try:
            self._widget_values[key] = entry.get().strip()
        except Exception:
            return
        self._touched.add(key)
        self._update_dirty()

    def _create_enum_control(self, parent, option: Dict[str, Any]):
        key = option["key"]
        current = str(self._widget_values.get(key, option["default"]))

        pairs = list(option["values"] or [])
        if current not in [p[0] for p in pairs]:
            pairs.append((current, None))  # 未知取值原样保留，避免保存时把配置改坏

        value_to_label: Dict[str, str] = {}
        for raw_value, i18n_key in pairs:
            label = _(i18n_key) if i18n_key else raw_value
            if label in value_to_label.values():
                label = f"{label} ({raw_value})"
            value_to_label[raw_value] = label

        var = ctk.StringVar(value=value_to_label.get(current, current))
        menu = ctk.CTkOptionMenu(
            parent,
            values=list(value_to_label.values()),
            variable=var,
            width=200,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
            dropdown_text_color=COLORS["text_primary"],
            command=lambda choice, k=key: self._on_enum_change(k, choice),
        )
        menu.pack(side=ctk.LEFT)

        self._widgets[key] = {
            "kind": ENUM,
            "var": var,
            "menu": menu,
            "map": {label: raw for raw, label in value_to_label.items()},
        }

    def _on_enum_change(self, key: str, choice: str):
        widget = self._widgets.get(key, {})
        raw = widget.get("map", {}).get(choice, choice)
        self._widget_values[key] = raw
        self._touched.add(key)
        self._update_dirty()

    def _create_memory_control(self, parent, option: Dict[str, Any]):
        choices = list(option["choices"] or ["1G", "2G", "4G", "8G"])
        if self._memory_value not in choices:
            choices.append(self._memory_value)
        choices.sort(key=self._memory_sort_key)

        var = ctk.StringVar(value=self._memory_value)
        menu = ctk.CTkOptionMenu(
            parent,
            values=choices,
            variable=var,
            width=200,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
            dropdown_text_color=COLORS["text_primary"],
            command=self._on_memory_change,
        )
        menu.pack(side=ctk.LEFT)

        self._widgets["@memory"] = {"kind": MEMORY, "var": var, "menu": menu}

    @staticmethod
    def _memory_sort_key(text: str) -> float:
        """把 1G / 2048M 之类的内存字符串换算成可比较的数字（MB）"""
        try:
            text = str(text).strip().upper()
            if text.endswith("G"):
                return float(text[:-1]) * 1024
            if text.endswith("M"):
                return float(text[:-1])
        except Exception:
            pass
        return 999999.0

    def _on_memory_change(self, choice: str):
        self._memory_value = str(choice).strip()
        self._touched.add("@memory")
        self._update_dirty()

    def _create_info_control(self, parent, option: Dict[str, Any]):
        key = option["key"]

        if key == "@eula":
            agreed = self._eula_agreed
            ctk.CTkLabel(
                parent,
                text=_("server_config_eula_yes") if agreed else _("server_config_eula_no"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                text_color=COLORS["success"] if agreed else COLORS["warning"],
                anchor=ctk.E,
                justify=ctk.RIGHT,
                wraplength=260,
            ).pack(side=ctk.LEFT)
        elif key == "@dir":
            ctk.CTkLabel(
                parent,
                text=str(self.server_dir),
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
                anchor=ctk.E,
                justify=ctk.RIGHT,
                wraplength=260,
            ).pack(side=ctk.LEFT)

            folder_btn = ctk.CTkButton(
                parent,
                text="📂",
                width=34,
                height=30,
                font=ctk.CTkFont(size=14),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_primary"],
                command=self._open_server_dir,
            )
            folder_btn.pack(side=ctk.LEFT, padx=(6, 0))
            self._attach_tip(folder_btn, _("server_config_open_folder"))

        self._widgets[key] = {"kind": INFO}

    # ─── 控件 → 数据同步 ─────────────────────────────────────

    def _sync_widgets(self):
        """把界面上的值收集回 self._widget_values / self._memory_value"""
        for key, widget in self._widgets.items():
            kind = widget.get("kind")
            try:
                if kind == BOOL:
                    self._widget_values[key] = "true" if widget["var"].get() else "false"
                elif kind in (INT, STR):
                    self._widget_values[key] = widget["entry"].get().strip()
                elif kind == ENUM:
                    label = widget["var"].get()
                    self._widget_values[key] = widget.get("map", {}).get(label, label)
                elif kind == MEMORY:
                    self._memory_value = str(widget["var"].get()).strip()
            except Exception:
                continue

    # ─── 校验与保存 ──────────────────────────────────────────

    def _validate(self) -> Optional[str]:
        """校验表单，返回第一条错误信息"""
        will_write = self._current_write_values()
        for option in CONFIG_OPTIONS:
            if option["kind"] != INT:
                continue
            key = option["key"]
            raw = str(self._widget_values.get(key, "")).strip()
            name = _(option["i18n"] + "_n")

            if not raw:
                if key in will_write:
                    return _("server_config_invalid_int_plain", name=name)
                continue

            try:
                number = int(raw)
            except ValueError:
                return _("server_config_invalid_int_plain", name=name)

            if option["min"] is not None and option["max"] is not None:
                if not (option["min"] <= number <= option["max"]):
                    return _(
                        "server_config_invalid_int", name=name, min=option["min"], max=option["max"]
                    )

        return None

    def _on_save(self):
        self._sync_widgets()

        error = self._validate()
        if error:
            messagebox.showerror(_("error"), error, parent=self)
            self._set_status(error, COLORS["error"])
            return

        values = self._current_write_values()
        if values:
            ok, err = write_server_properties(self.server_dir, values)
            if not ok:
                messagebox.showerror(
                    _("error"), _("server_config_save_failed", error=err), parent=self
                )
                self._set_status(_("server_config_save_failed", error=err), COLORS["error"])
                return

        ok, err = set_server_launch_memory(self.server_dir, self._memory_value)
        if not ok:
            logger.warning(f"保存服务器内存设置失败: {err}")

        self._load_from_disk()
        self._set_status(_("server_config_saved"), COLORS["success"])

        # 让开服标签页的内存下拉框同步显示当前服务器的设置
        try:
            if hasattr(self.parent_app, "_sync_server_memory_display"):
                self.parent_app._sync_server_memory_display(self.version_id)
        except Exception:
            pass

    def _on_reload(self):
        self._load_from_disk()
        self._set_status(_("server_config_reloaded"))

    def _on_reset_one(self, option: Dict[str, Any]):
        key = option["key"]
        if key == "@memory":
            self._memory_value = str(option.get("default", "2G"))
            self._touched.add(key)
        else:
            self._widget_values[key] = str(option["default"])
            self._touched.add(key)
        self._refresh_view(sync=False)
        self._set_status(_("server_config_reset_one_done"))

    def _on_reset_all(self):
        options = self._visible_options()
        if not options:
            return

        if self._search_text:
            category_name = _("server_config_search_placeholder")
        else:
            category_name = next(
                (_(c["i18n"]) for c in CATEGORIES if c["id"] == self._active_category), self._active_category
            )

        if not messagebox.askyesno(
            _("server_config_reset_confirm_title"),
            _("server_config_reset_confirm_msg", category=category_name),
            parent=self,
        ):
            return

        for option in options:
            key = option["key"]
            self._touched.add(key)
            if key == "@memory":
                self._memory_value = str(option.get("default", "2G"))
            elif not key.startswith("@"):
                self._widget_values[key] = str(option["default"])

        self._refresh_view(sync=False)
        self._set_status(_("server_config_reset_done"), COLORS["warning"])

    # ─── 原始文件 ────────────────────────────────────────────

    def _fill_raw_text(self):
        try:
            if self.props_path.exists():
                content = self.props_path.read_text(encoding="utf-8", errors="replace")
            else:
                content = format_properties({o["key"]: o["default"] for o in CONFIG_OPTIONS})
        except Exception as e:
            content = f"# {e}\n"

        try:
            self._raw_textbox.delete("1.0", ctk.END)
            self._raw_textbox.insert("1.0", content)
        except Exception:
            pass

    def _on_raw_write(self):
        try:
            content = self._raw_textbox.get("1.0", ctk.END)
        except Exception:
            return

        # tkinter 会在末尾补一个换行，先去掉再统一成单个换行结尾
        if content.endswith("\n"):
            content = content[:-1]
        content = content.replace("\r\n", "\n").rstrip("\n") + "\n"

        if not messagebox.askyesno(
            _("server_config_raw_confirm_title"), _("server_config_raw_confirm_msg"), parent=self
        ):
            return

        try:
            self.props_path.parent.mkdir(parents=True, exist_ok=True)
            self.props_path.write_text(content, encoding="utf-8")
        except Exception as e:
            messagebox.showerror(_("error"), _("server_config_save_failed", error=str(e)), parent=self)
            return

        self._load_from_disk()
        self._set_status(_("server_config_raw_written"), COLORS["success"])

    # ─── 其他 ────────────────────────────────────────────────

    def _open_server_dir(self):
        try:
            self.server_dir.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                os.startfile(str(self.server_dir))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self.server_dir)])
            else:
                subprocess.Popen(["xdg-open", str(self.server_dir)])
        except Exception as e:
            logger.error(f"打开服务器目录失败: {e}")

    def _on_close(self):
        if self._dirty_count:
            if not messagebox.askyesno(
                _("server_config_close_confirm_title"),
                _("server_config_close_confirm_msg", count=self._dirty_count),
                parent=self,
            ):
                return
        self.destroy()
