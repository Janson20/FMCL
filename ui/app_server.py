"""ModernApp 服务器 Mixin - 开服标签页相关方法"""
import os
import re
import sys
import subprocess
import platform
import tkinter.messagebox as messagebox
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.windows.modpack_server import ModpackServerWindow


class ServerTabMixin(object):
    """服务器标签页 Mixin"""

    def _build_server_tab_content(self):
        """构建开服标签页内容"""
        content = ctk.CTkFrame(self.server_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        # 左侧 - 服务器日志控制台
        self._build_server_log_panel(content)

        # 中间 - 已安装的服务器
        self._build_server_installed_panel(content)

        # 右侧 - 安装与控制面板
        self._build_server_control_panel(content)

    def _build_server_log_panel(self, parent):
        """构建服务器日志面板（左侧）"""
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, width=400)
        panel.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 10))
        panel.pack_propagate(False)

        # 标题栏
        title_frame = ctk.CTkFrame(panel, fg_color="transparent", height=40)
        title_frame.pack(fill=ctk.X, padx=15, pady=(12, 0))
        title_frame.pack_propagate(False)

        ctk.CTkLabel(
            title_frame,
            text="📜 服务器控制台",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self.server_log_status_label = ctk.CTkLabel(
            title_frame,
            text="未运行",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self.server_log_status_label.pack(side=ctk.RIGHT)

        # 分割线
        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(8, 5)
        )

        # 日志文本框
        self.server_log_text = ctk.CTkTextbox(
            panel,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            activate_scrollbars=True,
            wrap=ctk.NONE,
            spacing3=0,
        )
        self.server_log_text.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 5))
        # 设置为只读（通过禁用编辑，但仍可插入）
        self.server_log_text.configure(state=ctk.DISABLED)
        self._server_log_lines: List[str] = []

        # 状态栏：玩家列表 + 内存占用
        status_bar = ctk.CTkFrame(panel, fg_color=COLORS["bg_medium"], corner_radius=8, height=52)
        status_bar.pack(fill=ctk.X, padx=10, pady=(0, 5))
        status_bar.pack_propagate(False)

        # 玩家信息（左侧）
        player_frame = ctk.CTkFrame(status_bar, fg_color="transparent")
        player_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(8, 4), pady=6)

        ctk.CTkLabel(
            player_frame,
            text="👥",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT)

        self.server_player_label = ctk.CTkLabel(
            player_frame,
            text="0 / 20",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        self.server_player_label.pack(side=ctk.LEFT, padx=(4, 6))

        self.server_player_names_label = ctk.CTkLabel(
            player_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            anchor="w",
        )
        self.server_player_names_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        # 内存占用（右侧）
        mem_frame = ctk.CTkFrame(status_bar, fg_color="transparent")
        mem_frame.pack(side=ctk.RIGHT, fill=ctk.Y, padx=(4, 8), pady=6)

        ctk.CTkLabel(
            mem_frame,
            text="💾",
            font=ctk.CTkFont(size=13),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT)

        self.server_mem_label = ctk.CTkLabel(
            mem_frame,
            text="0 MB",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_primary"],
        )
        self.server_mem_label.pack(side=ctk.LEFT, padx=(4, 0))

        # 内部状态
        self._server_online_players: List[str] = []
        self._server_max_players: int = 20
        self._server_mem_monitor_after_id = None

        # 命令输入区域
        cmd_frame = ctk.CTkFrame(panel, fg_color="transparent", height=42)
        cmd_frame.pack(fill=ctk.X, padx=10, pady=(0, 10))
        cmd_frame.pack_propagate(False)

        ctk.CTkLabel(
            cmd_frame,
            text=">",
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(side=ctk.LEFT, padx=(5, 0))

        self.server_cmd_entry = ctk.CTkEntry(
            cmd_frame,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            placeholder_text="输入服务器命令...",
            height=34,
        )
        self.server_cmd_entry.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(5, 5))
        # 绑定回车发送命令
        self.server_cmd_entry.bind("<Return>", self._on_server_cmd_enter)

        self.server_cmd_send_btn = ctk.CTkButton(
            cmd_frame,
            text="发送",
            width=55,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["bg_light"],
            command=self._on_server_cmd_send,
        )
        self.server_cmd_send_btn.pack(side=ctk.RIGHT)

        # 插入初始提示
        self._append_server_log("[FMCL] 等待服务器启动...")

    def _build_server_installed_panel(self, parent):
        """构建服务器已安装版本面板"""
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12)
        panel.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 10))

        # 标题栏
        title_frame = ctk.CTkFrame(panel, fg_color="transparent", height=45)
        title_frame.pack(fill=ctk.X, padx=15, pady=(12, 0))
        title_frame.pack_propagate(False)

        ctk.CTkLabel(
            title_frame,
            text="📦 已安装服务器",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self.server_count_label = ctk.CTkLabel(
            title_frame,
            text="0 个版本",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self.server_count_label.pack(side=ctk.RIGHT)

        # 打开服务器目录按钮
        open_dir_btn = ctk.CTkButton(
            title_frame,
            text="📂",
            width=30,
            height=28,
            font=ctk.CTkFont(size=16),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_secondary"],
            command=self._open_server_dir,
        )
        open_dir_btn.pack(side=ctk.RIGHT, padx=(0, 8))

        # 分割线
        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(8, 5)
        )

        # 服务器列表 (带滚动)
        list_frame = ctk.CTkScrollableFrame(
            panel, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        list_frame.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.server_list_frame = list_frame
        self.server_buttons: List[Dict[str, Any]] = []

        # 底部启动/停止按钮
        launch_frame = ctk.CTkFrame(panel, fg_color="transparent", height=50)
        launch_frame.pack(fill=ctk.X, padx=15, pady=(0, 12))
        launch_frame.pack_propagate(False)

        self.server_start_btn = ctk.CTkButton(
            launch_frame,
            text="🚀 启动服务器",
            height=40,
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=self._on_server_start,
        )
        self.server_start_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        self.server_join_btn = ctk.CTkButton(
            launch_frame,
            text="🎮 加入服务器",
            width=120,
            height=40,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_server_join,
        )
        self.server_join_btn.pack(side=ctk.LEFT, padx=(8, 0))

        self.server_stop_btn = ctk.CTkButton(
            launch_frame,
            text="⏹ 停止",
            width=80,
            height=40,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            text_color=COLORS["text_primary"],
            command=self._on_server_stop,
        )
        self.server_stop_btn.pack(side=ctk.RIGHT, padx=(8, 0))
        self.server_stop_btn.configure(state=ctk.DISABLED)

        self.selected_server_version: Optional[str] = None

    def _build_server_control_panel(self, parent):
        """构建右侧服务器控制面板"""
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, width=300)
        panel.pack(side=ctk.RIGHT, fill=ctk.Y, padx=(0, 0))
        panel.pack_propagate(False)

        # ── 安装服务器区域 ──
        ctk.CTkLabel(
            panel,
            text="📥 安装服务器",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=15, pady=(15, 8), anchor=ctk.W)

        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(0, 10)
        )

        # 版本ID输入
        ctk.CTkLabel(
            panel,
            text="版本 ID（仅正式版）:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(padx=15, anchor=ctk.W)

        self.server_version_entry = ctk.CTkEntry(
            panel,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="例如: 1.21.4",
        )
        self.server_version_entry.pack(fill=ctk.X, padx=15, pady=(5, 10))

        # 安装按钮 + 整合包开服按钮并排
        btn_row = ctk.CTkFrame(panel, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=15, pady=(0, 15))

        self.server_install_btn = ctk.CTkButton(
            btn_row,
            text="📥 安装服务器",
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_server_install,
        )
        self.server_install_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 5))

        self.server_modpack_btn = ctk.CTkButton(
            btn_row,
            text="📦 整合包开服",
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_server_modpack,
        )
        self.server_modpack_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(5, 0))

        # ── 快速选择版本 ──
        ctk.CTkLabel(
            panel,
            text="📋 快速选择",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=15, pady=(5, 8), anchor=ctk.W)

        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(0, 8)
        )

        # 服务器版本列表（只有正式版）
        server_avail_frame = ctk.CTkScrollableFrame(
            panel, fg_color="transparent", height=180, scrollbar_button_color=COLORS["bg_light"]
        )
        server_avail_frame.pack(fill=ctk.X, padx=10, pady=(0, 5))

        self.server_available_list_frame = server_avail_frame
        self.server_available_version_buttons: List[Dict[str, Any]] = []
        self._server_available_versions: List[Dict[str, Any]] = []

        # 分页控件
        server_page_frame = ctk.CTkFrame(panel, fg_color="transparent", height=30)
        server_page_frame.pack(fill=ctk.X, padx=10, pady=(0, 10))
        server_page_frame.pack_propagate(False)

        self._server_page_size = 10
        self._server_current_page = 1

        self._server_prev_page_btn = ctk.CTkButton(
            server_page_frame,
            text="◀",
            width=28,
            height=26,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_server_prev_page,
        )
        self._server_prev_page_btn.pack(side=ctk.LEFT)

        self._server_page_label = ctk.CTkLabel(
            server_page_frame,
            text="1/1",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._server_page_label.pack(side=ctk.LEFT, expand=True)

        self._server_next_page_btn = ctk.CTkButton(
            server_page_frame,
            text="▶",
            width=28,
            height=26,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_server_next_page,
        )
        self._server_next_page_btn.pack(side=ctk.LEFT)

        # ── 服务器设置 ──
        ctk.CTkLabel(
            panel,
            text="⚙ 服务器设置",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=15, pady=(10, 8), anchor=ctk.W)

        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(0, 10)
        )

        # 最大内存设置
        ctk.CTkLabel(
            panel,
            text="最大内存:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(padx=15, anchor=ctk.W)

        self.server_memory_var = ctk.StringVar(value="2G")
        self.server_memory_menu = ctk.CTkOptionMenu(
            panel,
            variable=self.server_memory_var,
            values=["1G", "2G", "4G", "6G", "8G", "12G", "16G"],
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
        )
        self.server_memory_menu.pack(fill=ctk.X, padx=15, pady=(5, 10))

        # 服务器端口提示
        ctk.CTkLabel(
            panel,
            text="默认端口: 25565 (离线模式)",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=260,
            justify=ctk.LEFT,
        ).pack(padx=15, anchor=ctk.W, pady=(0, 5))

        ctk.CTkLabel(
            panel,
            text="EULA 将自动同意",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["warning"],
            wraplength=260,
            justify=ctk.LEFT,
        ).pack(padx=15, anchor=ctk.W)

    def _render_server_versions(self, versions: List[str]):
        """渲染已安装服务器版本列表"""
        for widget in self.server_list_frame.winfo_children():
            widget.destroy()
        self.server_buttons.clear()

        self.server_count_label.configure(text=f"{len(versions)} 个版本")

        if not versions:
            ctk.CTkLabel(
                self.server_list_frame,
                text="暂无已安装的服务器\n请在右侧安装服务器版本",
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=30)
            return

        for ver in versions:
            btn_frame = ctk.CTkFrame(
                self.server_list_frame,
                fg_color=COLORS["bg_medium"],
                corner_radius=8,
                height=42,
            )
            btn_frame.pack(fill=ctk.X, pady=2)
            btn_frame.pack_propagate(False)

            btn = ctk.CTkButton(
                btn_frame,
                text=f"  🖥 {ver}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
                command=lambda v=ver: self._select_server_version(v),
            )
            btn.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=5, pady=3)

            del_btn = ctk.CTkButton(
                btn_frame,
                text="X",
                width=30,
                height=28,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                fg_color="transparent",
                hover_color=COLORS["accent"],
                text_color=COLORS["text_secondary"],
                command=lambda v=ver: self._on_server_remove(v),
            )
            del_btn.pack(side=ctk.RIGHT, padx=(0, 3), pady=5)

            self.server_buttons.append({
                "version": ver,
                "frame": btn_frame,
                "button": btn,
                "delete_btn": del_btn,
            })

    def _select_server_version(self, version: str):
        """选中服务器版本"""
        self.selected_server_version = version

        for item in self.server_buttons:
            if item["version"] == version:
                item["frame"].configure(fg_color=COLORS["bg_light"])
                item["button"].configure(text_color=COLORS["accent"])
            else:
                item["frame"].configure(fg_color=COLORS["bg_medium"])
                item["button"].configure(text_color=COLORS["text_primary"])

        self.set_status(f"已选择服务器: {version}", "info")

    def _render_server_available_versions(self, versions: List[Dict[str, Any]]):
        """渲染服务器可用版本列表"""
        self._server_available_versions = versions
        self._server_current_page = 1
        self._render_server_available_page()

    def _render_server_available_page(self):
        """渲染服务器版本分页"""
        for widget in self.server_available_list_frame.winfo_children():
            widget.destroy()
        self.server_available_version_buttons.clear()

        versions = self._server_available_versions
        total_pages = max(1, (len(versions) + self._server_page_size - 1) // self._server_page_size)
        self._server_current_page = max(1, min(self._server_current_page, total_pages))

        start = (self._server_current_page - 1) * self._server_page_size
        end = start + self._server_page_size
        page_versions = versions[start:end]

        self._server_page_label.configure(text=f"{self._server_current_page}/{total_pages}")
        self._server_prev_page_btn.configure(state=ctk.NORMAL if self._server_current_page > 1 else ctk.DISABLED)
        self._server_next_page_btn.configure(state=ctk.NORMAL if self._server_current_page < total_pages else ctk.DISABLED)

        for v in page_versions:
            version_id = v.get("id", "")
            btn = ctk.CTkButton(
                self.server_available_list_frame,
                text=f"  📦 {version_id}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color=COLORS["bg_medium"],
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
                height=28,
                command=lambda vid=version_id: self._on_server_version_quick_select(vid),
            )
            btn.pack(fill=ctk.X, pady=1)
            self.server_available_version_buttons.append({"version": version_id, "button": btn})

    def _on_server_version_quick_select(self, version_id: str):
        """快速选择服务器版本"""
        self.server_version_entry.delete(0, ctk.END)
        self.server_version_entry.insert(0, version_id)
        self.set_status(f"已选择服务器版本: {version_id}", "info")

    def _on_server_prev_page(self):
        """服务器版本上一页"""
        self._server_current_page -= 1
        self._render_server_available_page()

    def _on_server_next_page(self):
        """服务器版本下一页"""
        self._server_current_page += 1
        self._render_server_available_page()

    def _load_server_versions(self):
        """加载服务器版本列表（后台线程）"""
        try:
            # 加载已安装的服务器
            if "get_installed_servers" in self.callbacks:
                installed = self.callbacks["get_installed_servers"]()
                self._task_queue.put(("server_installed_loaded", installed))

            # 加载可用的服务器版本（只有正式版）
            if "get_server_versions" in self.callbacks:
                available = self.callbacks["get_server_versions"]()
                self._task_queue.put(("server_available_loaded", available))
        except Exception as e:
            self._task_queue.put(("server_error", str(e)))

    def _on_server_install(self):
        """安装服务器按钮回调"""
        version_id = self.server_version_entry.get().strip()
        if not version_id:
            self.set_status("请输入服务器版本 ID", "error")
            return

        self.set_status(f"正在安装服务器 {version_id} ...", "loading")
        self.server_install_btn.configure(state=ctk.DISABLED)
        self._run_in_thread(self._install_server, version_id)

    def _on_server_modpack(self):
        """整合包开服按钮回调"""
        ModpackServerWindow(self, self.callbacks)

    def _install_server(self, version_id: str):
        """安装服务器（后台线程）"""
        try:
            if "install_server" in self.callbacks:
                success, result_version = self.callbacks["install_server"](version_id)
                self._task_queue.put(("server_install_done", (version_id, success)))
        except Exception as e:
            self._task_queue.put(("server_install_error", str(e)))

    def _on_server_start(self):
        """启动服务器按钮回调"""
        if not self.selected_server_version:
            self.set_status("请先选择一个服务器版本", "error")
            return

        # 检查是否已有服务器在运行
        if "is_server_running" in self.callbacks and self.callbacks["is_server_running"]():
            self.set_status("已有服务器正在运行", "warning")
            return

        version_id = self.selected_server_version
        max_memory = self.server_memory_var.get()
        self.set_status(f"正在启动服务器 {version_id} ...", "loading")
        self.server_start_btn.configure(state=ctk.DISABLED)
        self._run_in_thread(self._start_server, version_id, max_memory)

    def _start_server(self, version_id: str, max_memory: str):
        """启动服务器（后台线程）"""
        try:
            if "start_server" in self.callbacks:
                success, process = self.callbacks["start_server"](version_id, max_memory)
                self._task_queue.put(("server_start_done", (version_id, success)))
        except Exception as e:
            self._task_queue.put(("server_start_error", str(e)))

    def _on_server_stop(self):
        """停止服务器按钮回调"""
        if "stop_server" in self.callbacks:
            success = self.callbacks["stop_server"]()
            if success:
                self.set_status("正在停止服务器...", "warning")
            else:
                self.set_status("没有正在运行的服务器", "info")
            self.server_stop_btn.configure(state=ctk.DISABLED)
            self.server_start_btn.configure(state=ctk.NORMAL)

    def _on_server_remove(self, version_id: str):
        """删除服务器版本"""
        if not messagebox.askyesno("确认删除", f"确定要删除服务器版本 {version_id} 吗？\n\n服务器文件将被永久删除。"):
            return

        self.set_status(f"正在删除服务器 {version_id} ...", "loading")
        self._run_in_thread(self._remove_server, version_id)

    def _remove_server(self, version_id: str):
        """删除服务器（后台线程）"""
        try:
            if "remove_server" in self.callbacks:
                success, _ = self.callbacks["remove_server"](version_id)
                self._task_queue.put(("server_remove_done", (version_id, success)))
        except Exception as e:
            self._task_queue.put(("server_remove_error", str(e)))

    def _on_server_join(self):
        """一键加入服务器按钮回调"""
        if not self.selected_server_version:
            self.set_status("请先选择一个服务器版本", "error")
            return
        version_id = self.selected_server_version
        self.set_status(f"正在准备加入服务器 {version_id}...", "loading")
        self.server_join_btn.configure(state=ctk.DISABLED)
        self._run_in_thread(self._join_server, version_id)

    def _join_server(self, version_id: str):
        """一键加入服务器（后台线程）：安装客户端版本后直连 localhost:25565"""
        try:
            # 确保客户端版本已安装
            if "install_game" in self.callbacks:
                self.callbacks["install_game"](version_id)

            # 启动游戏并直连服务器
            if "launch_game" in self.callbacks:
                success, target = self.callbacks["launch_game"](
                    version_id,
                    minimize_after=True,
                    server_ip="localhost",
                    server_port=25565,
                )
                self._task_queue.put(("server_join_done", (version_id, success)))
            else:
                self._task_queue.put(("server_join_error", "启动游戏回调未注册"))
        except Exception as e:
            self._task_queue.put(("server_join_error", str(e)))

    def _open_server_dir(self):
        """打开服务器目录"""
        if "get_server_dir" in self.callbacks:
            server_dir = self.callbacks["get_server_dir"]()
            path = Path(server_dir)
            if not path.exists():
                path.mkdir(parents=True, exist_ok=True)
            if sys.platform == 'win32':
                os.startfile(str(path))
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', str(path)])
            else:
                subprocess.Popen(['xdg-open', str(path)])

    def _append_server_log(self, message: str):
        """追加日志到服务器控制台（线程安全）并解析玩家事件"""
        # 解析玩家加入
        join_match = re.search(r'joined the game$', message)
        if join_match:
            # 提取玩家名（格式: [HH:MM:SS] [Server thread/INFO]: <PlayerName> joined the game）
            name_match = re.search(r'<([^>]+)> joined the game', message)
            if name_match:
                player = name_match.group(1)
                if player not in self._server_online_players:
                    self._server_online_players.append(player)
                    self.after(0, self._update_player_display)

        # 解析玩家离开
        leave_match = re.search(r'left the game$', message)
        if leave_match:
            name_match = re.search(r'<([^>]+)> left the game', message)
            if name_match:
                player = name_match.group(1)
                if player in self._server_online_players:
                    self._server_online_players.remove(player)
                    self.after(0, self._update_player_display)

        def _do_append():
            if not hasattr(self, 'server_log_text') or not self.server_log_text.winfo_exists():
                return
            self.server_log_text.configure(state=ctk.NORMAL)
            self.server_log_text.insert(ctk.END, message + "\n")
            self.server_log_text.see(ctk.END)
            self.server_log_text.configure(state=ctk.DISABLED)
        self.after(0, _do_append)

    def _update_player_display(self):
        """更新玩家列表显示"""
        count = len(self._server_online_players)
        self.server_player_label.configure(text=f"{count} / {self._server_max_players}")
        if self._server_online_players:
            names = ", ".join(self._server_online_players)
            self.server_player_names_label.configure(text=names)
        else:
            self.server_player_names_label.configure(text="")

    def _start_mem_monitor(self):
        """启动服务器内存监控定时器"""
        self._stop_mem_monitor()
        self._update_mem_display()

    def _stop_mem_monitor(self):
        """停止服务器内存监控定时器"""
        if self._server_mem_monitor_after_id is not None:
            self.after_cancel(self._server_mem_monitor_after_id)
            self._server_mem_monitor_after_id = None

    def _update_mem_display(self):
        """更新内存占用显示并重新调度"""
        try:
            if "get_server_process" in self.callbacks:
                proc = self.callbacks["get_server_process"]()
                if proc is not None and proc.poll() is None:
                    pid = proc.pid
                    mem_mb = self._get_process_memory(pid)
                    if mem_mb is not None:
                        if mem_mb >= 1024:
                            text = f"{mem_mb / 1024:.1f} GB"
                        else:
                            text = f"{mem_mb} MB"
                        self.server_mem_label.configure(text=text)
        except Exception:
            pass

        # 每 2 秒刷新一次
        self._server_mem_monitor_after_id = self.after(2000, self._update_mem_display)

    @staticmethod
    def _get_process_memory(pid: int) -> Optional[int]:
        """获取进程的内存占用（MB），Windows 用 tasklist，Linux 用 /proc"""
        import subprocess
        try:
            import platform
            if platform.system() == "Windows":
                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                    capture_output=True, text=True, timeout=5,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                for line in result.stdout.splitlines():
                    if f'"{pid}"' in line:
                        # CSV 格式: "name","pid","session","session#","mem"
                        parts = line.strip('"').split('","')
                        if len(parts) >= 5:
                            mem_str = parts[4].replace(",", "").replace(" K", "").strip()
                            return int(mem_str) // 1024  # KB -> MB
            else:
                # Linux: /proc/<pid>/status
                with open(f"/proc/{pid}/status", "r") as f:
                    for line in f:
                        if line.startswith("VmRSS:"):
                            kb = int(line.split()[1])
                            return kb // 1024  # KB -> MB
        except Exception:
            pass
        return None

    def _on_server_cmd_enter(self, event=None):
        """命令输入框回车回调"""
        self._on_server_cmd_send()

    def _on_server_cmd_send(self):
        """发送命令到服务器"""
        cmd = self.server_cmd_entry.get().strip()
        if not cmd:
            return
        self.server_cmd_entry.delete(0, ctk.END)

        if "send_server_command" not in self.callbacks:
            self._append_server_log(f"[FMCL] 错误: 无法发送命令（回调未注册）")
            return

        success = self.callbacks["send_server_command"](cmd)
        if success:
            self._append_server_log(f"> {cmd}")
        else:
            self._append_server_log(f"[FMCL] 无法发送命令: 服务器未运行")

    def _watch_server_exit(self):
        """监控服务器进程退出并实时读取日志（后台线程）"""
        if "get_server_process" not in self.callbacks:
            return

        proc = self.callbacks["get_server_process"]()
        if proc is None:
            return

        # 清空上次启动的日志缓存
        self._server_log_lines = []

        try:
            # 读取所有输出直到 EOF（即使进程已经退出也能读取管道中残留的数据）
            while True:
                line = proc.stdout.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip("\r\n")
                if text:
                    self._server_log_lines.append(text)
                    self._task_queue.put(("server_log", text))

            exit_code = proc.wait()
            self._task_queue.put(("server_exit", exit_code))
        except Exception as e:
            logger.error(f"监控服务器退出失败: {e}")
