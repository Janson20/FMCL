"""ModernApp 存档备份 Mixin - 备份管理标签页"""
import os
import sys
import threading
import tkinter.filedialog as filedialog
import tkinter.messagebox as messagebox
from typing import List, Dict, Optional, Any

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY


class BackupTabMixin(object):
    """存档备份标签页 Mixin"""

    def _build_backup_tab_content(self):
        """构建存档备份标签页内容"""
        content = ctk.CTkFrame(self.backup_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        # 左侧 - 存档列表
        self._build_backup_world_list(content)

        # 右侧 - 备份列表与操作
        self._build_backup_panel(content)

    def _build_backup_world_list(self, parent):
        """构建左侧存档列表面板"""
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, width=220)
        panel.pack(side=ctk.LEFT, fill=ctk.Y, padx=(0, 10))
        panel.pack_propagate(False)

        # 标题栏
        title_frame = ctk.CTkFrame(panel, fg_color="transparent", height=40)
        title_frame.pack(fill=ctk.X, padx=12, pady=(12, 0))
        title_frame.pack_propagate(False)

        ctk.CTkLabel(
            title_frame,
            text="📁 存档列表",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        # 刷新按钮
        ctk.CTkButton(
            title_frame,
            text="🔄",
            width=28,
            height=28,
            font=ctk.CTkFont(size=13),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_secondary"],
            command=self._refresh_world_list,
        ).pack(side=ctk.RIGHT)

        # 分割线
        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(8, 5)
        )

        # 存档列表（可滚动）
        list_frame = ctk.CTkScrollableFrame(
            panel, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        list_frame.pack(fill=ctk.BOTH, expand=True, padx=8, pady=(0, 10))

        self.backup_world_list_frame = list_frame
        self.backup_world_buttons: List[Dict[str, Any]] = []
        self._selected_backup_world: Optional[str] = None

        # 自动备份设置
        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(5, 5)
        )

        ctk.CTkLabel(
            panel,
            text="⚙ 自动备份",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=12, anchor=ctk.W, pady=(0, 5))

        self.backup_auto_launch_var = ctk.BooleanVar(value=getattr(self._get_config(), 'backup_auto_launch', False))
        ctk.CTkCheckBox(
            panel,
            text="启动游戏前备份",
            variable=self.backup_auto_launch_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            command=self._on_auto_backup_setting_change,
        ).pack(padx=12, anchor=ctk.W)

        self.backup_auto_exit_var = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(
            panel,
            text="游戏退出后备份",
            variable=self.backup_auto_exit_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            command=self._on_auto_backup_setting_change,
        ).pack(padx=12, anchor=ctk.W, pady=(0, 12))

    def _build_backup_panel(self, parent):
        """构建右侧备份列表面板"""
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12)
        panel.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 0))

        # 顶部工具栏
        toolbar = ctk.CTkFrame(panel, fg_color="transparent", height=50)
        toolbar.pack(fill=ctk.X, padx=15, pady=(12, 0))
        toolbar.pack_propagate(False)

        ctk.CTkLabel(
            toolbar,
            text="💾 备份管理",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self.backup_count_label = ctk.CTkLabel(
            toolbar,
            text="选择一个存档",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self.backup_count_label.pack(side=ctk.RIGHT, padx=(10, 0))

        # 打开备份文件夹按钮
        ctk.CTkButton(
            toolbar,
            text="📂 文件夹",
            width=70,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._open_backup_folder,
        ).pack(side=ctk.RIGHT, padx=(5, 0))

        # 设置按钮
        ctk.CTkButton(
            toolbar,
            text="⚙ 设置",
            width=60,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._open_backup_settings,
        ).pack(side=ctk.RIGHT)

        # 分割线
        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(8, 5)
        )

        # 备份操作区（备注输入 + 手动备份按钮）
        action_frame = ctk.CTkFrame(panel, fg_color="transparent", height=42)
        action_frame.pack(fill=ctk.X, padx=15, pady=(0, 5))
        action_frame.pack_propagate(False)

        self.backup_note_entry = ctk.CTkEntry(
            action_frame,
            placeholder_text="添加备份备注（可选）...",
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
        )
        self.backup_note_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))

        self.backup_btn = ctk.CTkButton(
            action_frame,
            text="📥 备份",
            width=90,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_create_backup,
        )
        self.backup_btn.pack(side=ctk.RIGHT)

        # 备份列表（表格形式）
        # 表头
        header_frame = ctk.CTkFrame(panel, fg_color=COLORS["bg_medium"], height=32)
        header_frame.pack(fill=ctk.X, padx=15, pady=(0, 2))
        header_frame.pack_propagate(False)

        headers = [
            ("时间", 18),
            ("大小", 10),
            ("版本", 16),
            ("备注", 26),
            ("操作", 20),
        ]
        for text, weight in headers:
            ctk.CTkLabel(
                header_frame,
                text=text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
                text_color=COLORS["text_secondary"],
                width=0,
            ).pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=5, pady=4)

        # 备份列表（可滚动）
        list_frame = ctk.CTkScrollableFrame(
            panel, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        list_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=(0, 10))

        self.backup_list_frame = list_frame
        self._backup_entries: List[Any] = []

        # 无选中提示
        self._backup_empty_label = ctk.CTkLabel(
            list_frame,
            text="← 请先选择左侧的一个存档",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            justify=ctk.CENTER,
        )
        self._backup_empty_label.pack(pady=40)

    # ─── 存档列表操作 ─────────────────────────────────────────

    def _refresh_world_list(self):
        """刷新存档列表"""
        self.set_status("正在扫描存档...", "loading")
        self._run_in_thread(self._load_world_list)

    def _load_world_list(self):
        """加载存档列表（后台线程）"""
        try:
            from backup_manager import BackupManager
            bm = BackupManager(self._get_config())
            worlds = bm._find_all_world_dirs()
            self._task_queue.put(("backup_worlds_loaded", worlds))
        except Exception as e:
            self._task_queue.put(("backup_worlds_loaded", []))
            logger.error(f"加载存档列表失败: {e}")

    def _get_config(self):
        """获取 Config 实例"""
        try:
            from config import config
            return config
        except Exception:
            # 尝试从 callbacks 获取
            if self.callbacks and "get_minecraft_dir" in self.callbacks:
                class _Cfg:
                    pass
                from pathlib import Path
                mc_dir = Path(self.callbacks["get_minecraft_dir"]())
                _cfg.minecraft_dir = mc_dir
                _cfg.base_dir = mc_dir.parent
                _cfg.backup_dir = None
                _cfg.backup_compress_level = 6
                _cfg.backup_max_per_world = 10
                return _cfg
            raise

    def _render_world_list(self, worlds: List[Dict[str, Any]]):
        """渲染存档列表"""
        for widget in self.backup_world_list_frame.winfo_children():
            widget.destroy()
        self.backup_world_buttons.clear()

        if not worlds:
            ctk.CTkLabel(
                self.backup_world_list_frame,
                text="未找到存档\n请先在游戏中创建存档",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=30)
            return

        for w in worlds:
            name = w["name"]
            is_isolated = w.get("is_isolated", False)
            suffix = " 🔒" if is_isolated else ""

            btn = ctk.CTkButton(
                self.backup_world_list_frame,
                text=f"  {name}{suffix}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
                height=32,
                command=lambda n=name: self._select_backup_world(n),
            )
            btn.pack(fill=ctk.X, pady=1)
            self.backup_world_buttons.append({"name": name, "button": btn})

    def _select_backup_world(self, world_name: str):
        """选择存档"""
        self._selected_backup_world = world_name

        # 高亮选中
        for item in self.backup_world_buttons:
            if item["name"] == world_name:
                item["button"].configure(fg_color=COLORS["bg_light"])
            else:
                item["button"].configure(fg_color="transparent")

        self.set_status(f"已选择存档: {world_name}", "info")
        self._run_in_thread(self._load_backup_list, world_name)

    # ─── 备份列表操作 ─────────────────────────────────────────

    def _load_backup_list(self, world_name: str):
        """加载备份列表（后台线程）"""
        try:
            from backup_manager import BackupManager
            bm = BackupManager(self._get_config())
            backups = bm.get_backups(world_name)
            self._task_queue.put(("backup_list_loaded", (world_name, backups)))
        except Exception as e:
            logger.error(f"加载备份列表失败: {e}")
            self._task_queue.put(("backup_list_loaded", (world_name, [])))

    def _render_backup_list(self, world_name: str, backups: list):
        """渲染备份列表"""
        for widget in self.backup_list_frame.winfo_children():
            widget.destroy()
        self._backup_entries.clear()

        self.backup_count_label.configure(text=f"{len(backups)} 个备份")

        if not backups:
            ctk.CTkLabel(
                self.backup_list_frame,
                text=f"暂无 {world_name} 的备份\n点击上方「备份」按钮创建第一个备份",
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=40)
            return

        for entry in backups:
            self._create_backup_row(entry)

    def _create_backup_row(self, entry):
        """创建备份行"""
        row = ctk.CTkFrame(
            self.backup_list_frame,
            fg_color=COLORS["bg_medium"],
            corner_radius=6,
            height=36,
        )
        row.pack(fill=ctk.X, pady=2)
        row.pack_propagate(False)

        # 时间
        try:
            dt = entry.timestamp_dt
            time_str = dt.strftime("%m-%d %H:%M")
        except Exception:
            time_str = entry.timestamp[:16] if entry.timestamp else "未知"

        ctk.CTkLabel(
            row,
            text=time_str,
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=5, pady=6)

        # 大小
        ctk.CTkLabel(
            row,
            text=entry.size_display,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=5, pady=6)

        # 版本
        ver_text = entry.game_version if entry.game_version else "-"
        ctk.CTkLabel(
            row,
            text=ver_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
        ).pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=5, pady=6)

        # 备注
        note_text = entry.note if entry.note else "-"
        ctk.CTkLabel(
            row,
            text=note_text[:30] + ("..." if len(note_text) > 30 else ""),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        ).pack(side=ctk.LEFT, expand=True, fill=ctk.X, padx=5, pady=6)

        # 操作按钮
        btn_frame = ctk.CTkFrame(row, fg_color="transparent")
        btn_frame.pack(side=ctk.RIGHT, padx=5)

        ctk.CTkButton(
            btn_frame,
            text="🔄",
            width=26,
            height=26,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=COLORS["success"],
            text_color=COLORS["text_secondary"],
            command=lambda e=entry: self._on_restore_backup(e),
        ).pack(side=ctk.LEFT, padx=1)

        ctk.CTkButton(
            btn_frame,
            text="📤",
            width=26,
            height=26,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_secondary"],
            command=lambda e=entry: self._on_export_backup(e),
        ).pack(side=ctk.LEFT, padx=1)

        ctk.CTkButton(
            btn_frame,
            text="🗑",
            width=26,
            height=26,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=COLORS["accent"],
            text_color=COLORS["text_secondary"],
            command=lambda e=entry: self._on_delete_backup(e),
        ).pack(side=ctk.LEFT, padx=1)

    # ─── 备份操作 ─────────────────────────────────────────────

    def _on_create_backup(self):
        """手动备份按钮回调"""
        if not self._selected_backup_world:
            self.set_status("请先选择一个存档", "error")
            return

        world_name = self._selected_backup_world
        note = self.backup_note_entry.get().strip()
        self.backup_note_entry.delete(0, ctk.END)

        self.set_status(f"正在备份 {world_name}...", "loading")
        self.backup_btn.configure(state=ctk.DISABLED)
        self._run_in_thread(self._do_create_backup, world_name, note)

    def _do_create_backup(self, world_name: str, note: str):
        """执行备份（后台线程）"""
        try:
            from backup_manager import BackupManager
            bm = BackupManager(self._get_config())

            def _progress(current, total, status):
                self._task_queue.put(("backup_progress", (current, total, status)))

            success, msg = bm.create_backup(world_name, note, progress_callback=_progress)
            self._task_queue.put(("backup_done", (world_name, success, msg)))
        except Exception as e:
            self._task_queue.put(("backup_done", (world_name, False, str(e))))

    def _on_restore_backup(self, entry):
        """恢复备份按钮回调"""
        if not self._selected_backup_world:
            return

        world_name = self._selected_backup_world
        if not messagebox.askyesno(
            "确认恢复",
            f"确定要将存档「{world_name}」恢复到此备份吗？\n\n"
            f"当前存档将被重命名保留（_bak_时间戳）。\n"
            f"备份时间: {entry.timestamp[:19] if entry.timestamp else '未知'}\n"
            f"备注: {entry.note or '无'}",
        ):
            return

        self.set_status(f"正在恢复 {world_name}...", "loading")
        self._run_in_thread(self._do_restore_backup, world_name, entry.id)

    def _do_restore_backup(self, world_name: str, entry_id: str):
        """执行恢复（后台线程）"""
        try:
            from backup_manager import BackupManager
            bm = BackupManager(self._get_config())

            def _progress(current, total, status):
                self._task_queue.put(("backup_progress", (current, total, status)))

            success, msg = bm.restore_backup(entry_id, world_name, progress_callback=_progress)
            self._task_queue.put(("restore_done", (world_name, success, msg)))
        except Exception as e:
            self._task_queue.put(("restore_done", (world_name, False, str(e))))

    def _on_delete_backup(self, entry):
        """删除备份按钮回调"""
        if not self._selected_backup_world:
            return

        world_name = self._selected_backup_world
        time_str = entry.timestamp[:19] if entry.timestamp else "未知"
        note_str = entry.note or "无备注"

        if not messagebox.askyesno(
            "确认删除",
            f"确定要删除此备份吗？\n\n"
            f"时间: {time_str}\n"
            f"大小: {entry.size_display}\n"
            f"备注: {note_str}\n\n"
            f"此操作不可恢复。",
        ):
            return

        self._run_in_thread(self._do_delete_backup, world_name, entry.id)

    def _do_delete_backup(self, world_name: str, entry_id: str):
        """执行删除（后台线程）"""
        try:
            from backup_manager import BackupManager
            bm = BackupManager(self._get_config())
            success, msg = bm.delete_backup(entry_id, world_name)
            self._task_queue.put(("delete_backup_done", (world_name, success, msg)))
        except Exception as e:
            self._task_queue.put(("delete_backup_done", (world_name, False, str(e))))

    def _on_export_backup(self, entry):
        """导出备份按钮回调"""
        if not self._selected_backup_world:
            return

        default_name = entry.file_name
        filepath = filedialog.asksaveasfilename(
            title="导出备份",
            defaultextension=".zip",
            initialfile=default_name,
            filetypes=[("ZIP 压缩包", "*.zip")],
        )
        if not filepath:
            return

        self._run_in_thread(self._do_export_backup, entry.id, self._selected_backup_world, filepath)

    def _do_export_backup(self, entry_id: str, world_name: str, dest_path: str):
        """执行导出（后台线程）"""
        try:
            from backup_manager import BackupManager
            bm = BackupManager(self._get_config())
            success, msg = bm.export_backup(entry_id, world_name, dest_path)
            self._task_queue.put(("export_backup_done", (success, msg)))
        except Exception as e:
            self._task_queue.put(("export_backup_done", (False, str(e))))

    def _open_backup_folder(self):
        """打开备份文件夹"""
        try:
            from backup_manager import BackupManager
            bm = BackupManager(self._get_config())
            path = str(bm.backup_root)
            if not os.path.exists(path):
                os.makedirs(path, exist_ok=True)
            if sys.platform == 'win32':
                os.startfile(path)
            elif sys.platform == 'darwin':
                import subprocess
                subprocess.Popen(['open', path])
            else:
                import subprocess
                subprocess.Popen(['xdg-open', path])
        except Exception as e:
            self.set_status(f"打开文件夹失败: {e}", "error")

    def _open_backup_settings(self):
        """打开备份设置弹窗"""
        from ui.windows.backup_settings import BackupSettingsWindow
        BackupSettingsWindow(self, self._get_config())

    def _on_auto_backup_setting_change(self):
        """自动备份设置变更回调"""
        try:
            from config import config
            config.backup_auto_launch = self.backup_auto_launch_var.get()
            config.backup_auto_exit = self.backup_auto_exit_var.get()
            config.save_config()
        except Exception:
            pass

    # ─── 自动备份触发 ─────────────────────────────────────────

    def _auto_backup_before_launch(self, version_id: str):
        """游戏启动前自动备份"""
        try:
            from config import config
            if not getattr(config, "backup_auto_launch", False):
                return
        except Exception:
            return

        self._run_in_thread(self._do_auto_backup, "launch")

    def _auto_backup_after_exit(self):
        """游戏退出后自动备份"""
        try:
            from config import config
            if not getattr(config, "backup_auto_exit", False):
                return
        except Exception:
            return

        self._run_in_thread(self._do_auto_backup, "exit")

    def _do_auto_backup(self, trigger: str):
        """执行自动备份（后台线程）"""
        try:
            from backup_manager import BackupManager
            from config import config
            bm = BackupManager(config)
            worlds = bm._find_all_world_dirs()

            if not worlds:
                logger.info("自动备份: 未找到存档")
                return

            # 备份最近修改的存档
            world = worlds[0]
            note = f"自动备份({trigger}) - {trigger == 'launch' and '启动前' or '退出后'}"
            success, msg = bm.create_backup(world["name"], note)
            if success:
                logger.info(f"自动备份成功: {world['name']}")
                self._task_queue.put(("auto_backup_done", (world["name"], msg)))
            else:
                logger.warning(f"自动备份失败: {msg}")
        except Exception as e:
            logger.error(f"自动备份异常: {e}")

    # ─── 队列任务处理（在 app_handlers.py 的 _handle_task 中调用） ───

    def _handle_backup_task(self, task_type: str, data: Any):
        """处理备份相关的队列任务，返回 True 表示已处理"""
        if task_type == "backup_worlds_loaded":
            worlds = data
            self._render_world_list(worlds)
            self.set_status(f"找到 {len(worlds)} 个存档", "success")
            return True

        elif task_type == "backup_list_loaded":
            world_name, backups = data
            self._render_backup_list(world_name, backups)
            return True

        elif task_type == "backup_progress":
            current, total, status = data
            if total > 0:
                self.progress_bar.set(current / total)
                self.progress_label.configure(text=f"{_pct(current, total)}")
            if status:
                self.set_status(status, "loading")
            return True

        elif task_type == "backup_done":
            world_name, success, msg = data
            self.backup_btn.configure(state=ctk.NORMAL)
            if success:
                self.set_status(f"备份成功: {world_name}", "success")
                self._run_in_thread(self._load_backup_list, world_name)
            else:
                self.set_status(f"备份失败: {msg}", "error")
            self.progress_bar.set(0)
            self.progress_label.configure(text="")
            return True

        elif task_type == "restore_done":
            world_name, success, msg = data
            if success:
                self.set_status(f"恢复成功: {world_name}", "success")
                self._run_in_thread(self._load_backup_list, world_name)
            else:
                self.set_status(f"恢复失败: {msg}", "error")
            self.progress_bar.set(0)
            self.progress_label.configure(text="")
            return True

        elif task_type == "delete_backup_done":
            world_name, success, msg = data
            if success:
                self.set_status("备份已删除", "success")
                self._run_in_thread(self._load_backup_list, world_name)
            else:
                self.set_status(f"删除失败: {msg}", "error")
            return True

        elif task_type == "export_backup_done":
            success, msg = data
            if success:
                self.set_status("导出成功", "success")
            else:
                self.set_status(f"导出失败: {msg}", "error")
            return True

        elif task_type == "auto_backup_done":
            world_name, msg = data
            self.set_status(f"自动备份完成: {world_name}", "success")
            return True

        return False


def _pct(current: int, total: int) -> str:
    """计算百分比字符串"""
    if total <= 0:
        return ""
    p = current * 100 // total
    return f"{p}%"
