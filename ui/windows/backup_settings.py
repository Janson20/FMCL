"""备份设置窗口"""
import customtkinter as ctk
from pathlib import Path

from ui.constants import COLORS, FONT_FAMILY


class BackupSettingsWindow(ctk.CTkToplevel):
    """备份设置弹窗"""

    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config
        self.title("备份设置")
        self.geometry("420x520")
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        self.grab_set()

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
            self, text="⚙ 备份设置",
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(pady=(20, 15))

        # 设置区域（可滚动）
        scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        scroll.pack(fill=ctk.BOTH, expand=True, padx=20, pady=(0, 10))

        # ── 备份存储路径 ──
        ctk.CTkLabel(
            scroll, text="备份存储路径",
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
            text="浏览",
            width=60,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._browse_backup_dir,
        ).pack(side=ctk.RIGHT)

        ctk.CTkButton(
            path_frame,
            text="重置",
            width=60,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._reset_backup_dir,
        ).pack(side=ctk.RIGHT, padx=(0, 5))

        # ── 压缩等级 ──
        ctk.CTkLabel(
            scroll, text="压缩等级",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, pady=(15, 5))

        ctk.CTkLabel(
            scroll, text="低压缩 = 更快速度，大体积 | 高压缩 = 慢速度，小体积",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=360,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(0, 5))

        current_level = getattr(self.config, "backup_compress_level", 6)
        level_labels = {
            "1 - 最快 (低压缩)": 1,
            "3 - 较快": 3,
            "6 - 适中 (推荐)": 6,
            "9 - 最小体积 (慢)": 9,
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
            scroll, text="每个存档最大备份数",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, pady=(15, 5))

        ctk.CTkLabel(
            scroll, text="超出数量的旧备份将自动删除",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(0, 5))

        current_max = getattr(self.config, "backup_max_per_world", 10)
        max_options = ["5", "10", "20", "30", "50", "不限制"]
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
            scroll, text="恢复时旧存档处理",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, pady=(15, 5))

        current_restore = getattr(self.config, "backup_restore_mode", "rename")
        restore_options = ["重命名为 .bak (推荐)", "直接覆盖", "移至回收站"]
        self._restore_var = ctk.StringVar(value={
            "rename": "重命名为 .bak (推荐)",
            "overwrite": "直接覆盖",
            "trash": "移至回收站",
        }.get(current_restore, "重命名为 .bak (推荐)"))

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
            text="取消",
            width=100,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["card_border"],
            command=self.destroy,
        ).pack(side=ctk.RIGHT, padx=(10, 0))

        ctk.CTkButton(
            btn_frame,
            text="保存",
            width=100,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._save_settings,
        ).pack(side=ctk.RIGHT)

    def _browse_backup_dir(self):
        from tkinter import filedialog
        path = filedialog.askdirectory(title="选择备份存储目录")
        if path:
            self._backup_path_var.set(path)

    def _reset_backup_dir(self):
        self._backup_path_var.set(str(self.config.base_dir / "backups"))

    def _save_settings(self):
        try:
            # 保存压缩等级
            compress_text = self._compress_var.get()
            level_map = {
                "1 - 最快 (低压缩)": 1,
                "3 - 较快": 3,
                "6 - 适中 (推荐)": 6,
                "9 - 最小体积 (慢)": 9,
            }
            self.config.backup_compress_level = level_map.get(compress_text, 6)

            # 保存最大备份数
            max_text = self._max_var.get()
            if max_text == "不限制":
                self.config.backup_max_per_world = 0
            else:
                self.config.backup_max_per_world = int(max_text)

            # 保存恢复模式
            restore_text = self._restore_var.get()
            restore_map = {
                "重命名为 .bak (推荐)": "rename",
                "直接覆盖": "overwrite",
                "移至回收站": "trash",
            }
            self.config.backup_restore_mode = restore_map.get(restore_text, "rename")

            # 保存路径
            custom_path = self._backup_path_var.get().strip()
            default_path = str(self.config.base_dir / "backups")
            self.config.backup_dir = custom_path if custom_path != default_path else None

            self.config.save_config()
            self.set_status("备份设置已保存", "success") if hasattr(self, "set_status") else None
            self.destroy()

        except Exception as e:
            from logzero import logger
            logger.error(f"保存备份设置失败: {e}")
            self.set_status(f"保存失败: {e}", "error") if hasattr(self, "set_status") else None
