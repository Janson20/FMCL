"""备份设置窗口"""
import customtkinter as ctk
from pathlib import Path

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


class BackupSettingsWindow(ctk.CTkToplevel):
    """备份设置弹窗"""

    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config
        self.title(_("backup_settings_window_title"))
        self.geometry("420x520")
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:
            pass

        self._build_ui()
        self._center_on_parent(parent)

    def _center_on_parent(self, parent):
        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_x(), parent.winfo_y()
        w, h = 420, 520
        self.geometry(f"{w}x{h}+{px + (pw - w) // 2}+{py + (ph - h) // 2}")

    def _build_ui(self):
        # 标题
        ctk.CTkLabel(
            self, text=_("backup_settings_window_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(pady=(20, 15))

        # 设置区域（可滚动）
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill=ctk.BOTH, expand=True, padx=20, pady=(0, 10))

        # ── 备份存储路径 ──
        ctk.CTkLabel(
            scroll, text=_("backup_settings_path_label"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, pady=(10, 5))

        path_frame = ctk.CTkFrame(scroll, fg_color="transparent")
        path_frame.pack(fill=ctk.X, pady=(0, 5))

        current_path = getattr(self.config, "backup_dir", None) or str(self.config.base_dir / "backups")
        self._backup_path_var = ctk.StringVar(value=current_path)

        self._backup_path_entry = ctk.CTkEntry(
            path_frame,
            textvariable=self._backup_path_var,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
        )
        self._backup_path_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 5))

        ctk.CTkButton(
            path_frame,
            text=_("backup_settings_browse"),
            width=60,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._browse_backup_dir,
        ).pack(side=ctk.RIGHT)

        ctk.CTkButton(
            path_frame,
            text=_("backup_settings_reset"),
            width=60,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._reset_backup_dir,
        ).pack(side=ctk.RIGHT, padx=(0, 5))

        # ── 压缩等级 ──
        ctk.CTkLabel(
            scroll, text=_("backup_settings_compress_label"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, pady=(15, 5))

        ctk.CTkLabel(
            scroll, text=_("backup_settings_compress_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=360,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(0, 5))

        current_level = getattr(self.config, "backup_compress_level", 6)
        level_labels = {
            _("backup_settings_compress_1"): 1,
            _("backup_settings_compress_3"): 3,
            _("backup_settings_compress_6"): 6,
            _("backup_settings_compress_9"): 9,
        }
        level_options = list(level_labels.keys())
        self._compress_var = ctk.StringVar()

        for opt, lvl in level_labels.items():
            if lvl == current_level:
                self._compress_var.set(opt)
                break

        self._compress_menu = ctk.CTkOptionMenu(
            scroll,
            variable=self._compress_var,
            values=level_options,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
        )
        self._compress_menu.pack(fill=ctk.X, pady=(0, 5))

        # ── 每个存档最大备份数 ──
        ctk.CTkLabel(
            scroll, text=_("backup_settings_max_label"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, pady=(15, 5))

        ctk.CTkLabel(
            scroll, text=_("backup_settings_max_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(0, 5))

        current_max = getattr(self.config, "backup_max_per_world", 10)
        max_options = ["5", "10", "20", "30", "50", _("backup_settings_max_unlimited")]
        self._max_var = ctk.StringVar(value=str(current_max))

        self._max_menu = ctk.CTkOptionMenu(
            scroll,
            variable=self._max_var,
            values=max_options,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
        )
        self._max_menu.pack(fill=ctk.X, pady=(0, 5))

        # ── 恢复时旧存档处理方式 ──
        ctk.CTkLabel(
            scroll, text=_("backup_settings_restore_label"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, pady=(15, 5))

        current_restore = getattr(self.config, "backup_restore_mode", "rename")
        restore_options = [
            _("backup_settings_restore_rename_opt"),
            _("backup_settings_restore_overwrite_opt"),
            _("backup_settings_restore_trash_opt"),
        ]
        self._restore_var = ctk.StringVar(value={
            "rename": _("backup_settings_restore_rename_opt"),
            "overwrite": _("backup_settings_restore_overwrite_opt"),
            "trash": _("backup_settings_restore_trash_opt"),
        }.get(current_restore, _("backup_settings_restore_rename_opt")))

        self._restore_menu = ctk.CTkOptionMenu(
            scroll,
            variable=self._restore_var,
            values=restore_options,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
        )
        self._restore_menu.pack(fill=ctk.X, pady=(0, 10))

        # 保存按钮
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill=ctk.X, padx=20, pady=(0, 20))

        ctk.CTkButton(
            btn_frame,
            text=_("backup_settings_cancel"),
            width=100,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["card_border"],
            command=self.destroy,
        ).pack(side=ctk.RIGHT, padx=(10, 0))

        ctk.CTkButton(
            btn_frame,
            text=_("backup_settings_save_btn"),
            width=100,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._save_settings,
        ).pack(side=ctk.RIGHT)

    def _browse_backup_dir(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title=_("backup_settings_select_dir"))
        if path:
            self._backup_path_var.set(path)

    def _reset_backup_dir(self):
        self._backup_path_var.set(str(self.config.base_dir / "backups"))

    def _save_settings(self):
        try:
            # 保存压缩等级
            compress_text = self._compress_var.get()
            level_map = {
                _("backup_settings_compress_1"): 1,
                _("backup_settings_compress_3"): 3,
                _("backup_settings_compress_6"): 6,
                _("backup_settings_compress_9"): 9,
            }
            self.config.backup_compress_level = level_map.get(compress_text, 6)

            # 保存最大备份数
            max_text = self._max_var.get()
            if max_text == _("backup_settings_max_unlimited"):
                self.config.backup_max_per_world = 0
            else:
                self.config.backup_max_per_world = int(max_text)

            # 保存恢复模式
            restore_text = self._restore_var.get()
            restore_map = {
                _("backup_settings_restore_rename_opt"): "rename",
                _("backup_settings_restore_overwrite_opt"): "overwrite",
                _("backup_settings_restore_trash_opt"): "trash",
            }
            self.config.backup_restore_mode = restore_map.get(restore_text, "rename")

            # 保存路径
            custom_path = self._backup_path_var.get().strip()
            default_path = str(self.config.base_dir / "backups")
            self.config.backup_dir = custom_path if custom_path != default_path else None

            self.config.save_config()
            self.set_status(_("backup_settings_save_success"), "success") if hasattr(self, "set_status") else None
            self.destroy()

        except Exception as e:
            from logzero import logger
            logger.error(f"保存备份设置失败: {e}")
            self.set_status(_("backup_settings_save_failed", error=str(e)), "error") if hasattr(self, "set_status") else None
