"""现代化UI界面模块 - 基于 CustomTkinter"""
import os
import shutil
import threading
import queue
import tkinter.messagebox as messagebox
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any

import customtkinter as ctk
from logzero import logger

try:
    from tkinterdnd2 import DND_FILES
    HAS_DND: bool = True
except ImportError:
    HAS_DND = False


# ─── 颜色主题 ───────────────────────────────────────────────
COLORS = {
    "bg_dark": "#1a1a2e",
    "bg_medium": "#16213e",
    "bg_light": "#0f3460",
    "accent": "#e94560",
    "accent_hover": "#ff6b81",
    "success": "#2ecc71",
    "warning": "#f39c12",
    "text_primary": "#ffffff",
    "text_secondary": "#a0a0b0",
    "card_bg": "#1e2a4a",
    "card_border": "#2d3a5c",
}


class ModernApp(ctk.CTk):
    """MCL 启动器主窗口"""

    def __init__(self, launcher_callbacks: Dict[str, Callable]):
        """
        初始化主窗口

        Args:
            launcher_callbacks: 启动器回调函数字典
                - check_environment: 检查环境
                - get_available_versions: 获取可用版本
                - get_installed_versions: 获取已安装版本
                - install_version: 安装版本 (version_id, mod_loader) -> (bool, str)
                - launch_game: 启动游戏 (version_id) -> bool
        """
        super().__init__()

        self.callbacks = launcher_callbacks
        self._task_queue = queue.Queue()
        self._running = True
        self._launcher_ready = False  # 标记 launcher 是否初始化完成

        # 窗口配置
        self.title("MCL - Minecraft Launcher")
        self.geometry("960x860")
        self.minsize(860, 800)
        self.configure(fg_color=COLORS["bg_dark"])

        # 居中显示
        self._center_window()

        # 构建UI
        self._build_ui()

        # 启动队列轮询
        self._poll_queue()

        # 注意：初始化不再自动触发，由外部调用 _on_app_ready() 启动

    def _center_window(self):
        """窗口居中"""
        self.update_idletasks()
        w, h = 960, 860
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ─── UI 构建 ─────────────────────────────────────────────

    def _build_ui(self):
        """构建主界面"""
        # 主容器
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        self._build_header()
        self._build_content()
        self._build_footer()

    def _build_header(self):
        """构建头部区域"""
        header = ctk.CTkFrame(self.main_frame, fg_color="transparent", height=60)
        header.pack(fill=ctk.X, pady=(0, 15))
        header.pack_propagate(False)

        # 标题
        title_label = ctk.CTkLabel(
            header,
            text="⛏ MCL",
            font=ctk.CTkFont(family="Microsoft YaHei", size=28, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title_label.pack(side=ctk.LEFT, padx=(5, 0))

        subtitle = ctk.CTkLabel(
            header,
            text="Minecraft Launcher",
            font=ctk.CTkFont(family="Microsoft YaHei", size=14),
            text_color=COLORS["text_secondary"],
        )
        subtitle.pack(side=ctk.LEFT, padx=(15, 0), pady=(10, 0))

        # 刷新按钮
        refresh_btn = ctk.CTkButton(
            header,
            text="🔄 刷新",
            width=100,
            height=35,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._refresh_versions,
        )
        refresh_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        # 镜像源开关
        self.mirror_var = ctk.BooleanVar(value=True)
        mirror_switch = ctk.CTkSwitch(
            header,
            text="🇨🇳 国内镜像",
            variable=self.mirror_var,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            fg_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["text_secondary"],
            progress_color=COLORS["accent_hover"],
            text_color=COLORS["text_secondary"],
            command=self._on_mirror_toggle,
        )
        mirror_switch.pack(side=ctk.RIGHT, padx=(10, 0))

    def _build_content(self):
        """构建内容区域"""
        content = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        # 左侧 - 已安装版本
        self._build_installed_panel(content)

        # 右侧 - 操作面板
        self._build_action_panel(content)

    def _build_installed_panel(self, parent):
        """构建已安装版本面板"""
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12)
        panel.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 10))

        # 标题栏
        title_frame = ctk.CTkFrame(panel, fg_color="transparent", height=45)
        title_frame.pack(fill=ctk.X, padx=15, pady=(12, 0))
        title_frame.pack_propagate(False)

        ctk.CTkLabel(
            title_frame,
            text="📦 已安装版本",
            font=ctk.CTkFont(family="Microsoft YaHei", size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self.version_count_label = ctk.CTkLabel(
            title_frame,
            text="0 个版本",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color=COLORS["text_secondary"],
        )
        self.version_count_label.pack(side=ctk.RIGHT)

        # 设置按钮（资源管理）
        settings_btn = ctk.CTkButton(
            title_frame,
            text="⚙",
            width=30,
            height=28,
            font=ctk.CTkFont(size=16),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_secondary"],
            command=self._open_resource_manager,
        )
        settings_btn.pack(side=ctk.RIGHT, padx=(0, 8))

        # 分割线
        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(8, 5)
        )

        # 版本列表 (带滚动)
        list_frame = ctk.CTkScrollableFrame(
            panel, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        list_frame.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.version_list_frame = list_frame
        self.version_buttons: List[Dict[str, Any]] = []

        # 底部启动按钮
        launch_frame = ctk.CTkFrame(panel, fg_color="transparent", height=50)
        launch_frame.pack(fill=ctk.X, padx=15, pady=(0, 12))
        launch_frame.pack_propagate(False)

        self.launch_btn = ctk.CTkButton(
            launch_frame,
            text="🚀 启动游戏",
            height=40,
            font=ctk.CTkFont(family="Microsoft YaHei", size=15, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_launch,
        )
        self.launch_btn.pack(fill=ctk.X)

        self.selected_version: Optional[str] = None

    def _build_action_panel(self, parent):
        """构建右侧操作面板"""
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, width=300)
        panel.pack(side=ctk.RIGHT, fill=ctk.Y, padx=(0, 0))
        panel.pack_propagate(False)

        # ── 安装新版本区域 ──
        ctk.CTkLabel(
            panel,
            text="📥 安装新版本",
            font=ctk.CTkFont(family="Microsoft YaHei", size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=15, pady=(15, 8), anchor=ctk.W)

        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(0, 10)
        )

        # 版本ID输入
        ctk.CTkLabel(
            panel,
            text="版本 ID:",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color=COLORS["text_secondary"],
        ).pack(padx=15, anchor=ctk.W)

        self.version_entry = ctk.CTkEntry(
            panel,
            height=35,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="例如: 1.20.4",
        )
        self.version_entry.pack(fill=ctk.X, padx=15, pady=(5, 10))

        # 模组加载器选项
        ctk.CTkLabel(
            panel,
            text="模组加载器:",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color=COLORS["text_secondary"],
        ).pack(padx=15, anchor=ctk.W)

        self.modloader_var = ctk.StringVar(value="无")
        self.modloader_menu = ctk.CTkOptionMenu(
            panel,
            variable=self.modloader_var,
            values=["无", "Forge", "Fabric", "NeoForge"],
            height=35,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
        )
        self.modloader_menu.pack(fill=ctk.X, padx=15, pady=(5, 5))

        # 模组加载器提示
        self.modloader_hint = ctk.CTkLabel(
            panel,
            text="",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            text_color=COLORS["warning"],
            wraplength=260,
            justify=ctk.LEFT,
        )
        self.modloader_hint.pack(padx=15, anchor=ctk.W, pady=(0, 10))
        self.modloader_var.trace_add("write", self._on_modloader_change)
        self._on_modloader_change()

        # 安装按钮
        self.install_btn = ctk.CTkButton(
            panel,
            text="📥 安装版本",
            height=38,
            font=ctk.CTkFont(family="Microsoft YaHei", size=14, weight="bold"),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_install,
        )
        self.install_btn.pack(fill=ctk.X, padx=15, pady=(0, 15))

        # ── 版本选择器 ──
        ctk.CTkLabel(
            panel,
            text="📋 快速选择",
            font=ctk.CTkFont(family="Microsoft YaHei", size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=15, pady=(5, 8), anchor=ctk.W)

        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(0, 8)
        )

        # 正式版/测试版 Tab 切换
        self.version_tab_var = ctk.StringVar(value="release")
        tab_frame = ctk.CTkFrame(panel, fg_color="transparent", height=32)
        tab_frame.pack(fill=ctk.X, padx=15, pady=(0, 5))
        tab_frame.pack_propagate(False)

        release_tab = ctk.CTkRadioButton(
            tab_frame,
            text="📦 正式版",
            variable=self.version_tab_var,
            value="release",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_color=COLORS["text_secondary"],
            command=self._on_version_tab_change,
        )
        release_tab.pack(side=ctk.LEFT, padx=(0, 10))

        snapshot_tab = ctk.CTkRadioButton(
            tab_frame,
            text="🔬 测试版",
            variable=self.version_tab_var,
            value="snapshot",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_color=COLORS["text_secondary"],
            command=self._on_version_tab_change,
        )
        snapshot_tab.pack(side=ctk.LEFT)

        # 可用版本列表
        avail_frame = ctk.CTkScrollableFrame(
            panel, fg_color="transparent", height=155, scrollbar_button_color=COLORS["bg_light"]
        )
        avail_frame.pack(fill=ctk.X, padx=10, pady=(0, 5))

        self.available_list_frame = avail_frame
        self.available_version_buttons: List[Dict[str, Any]] = []
        self._all_available_versions: List[Dict[str, Any]] = []
        self._release_versions: List[Dict[str, Any]] = []
        self._snapshot_versions: List[Dict[str, Any]] = []

        # 分页控件
        page_frame = ctk.CTkFrame(panel, fg_color="transparent", height=30)
        page_frame.pack(fill=ctk.X, padx=10, pady=(0, 10))
        page_frame.pack_propagate(False)

        self._page_size = 20
        self._current_page = 1

        self._prev_page_btn = ctk.CTkButton(
            page_frame,
            text="◀",
            width=28,
            height=26,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_prev_page,
        )
        self._prev_page_btn.pack(side=ctk.LEFT)

        self._page_info_label = ctk.CTkLabel(
            page_frame,
            text="1/1",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            text_color=COLORS["text_secondary"],
            width=60,
        )
        self._page_info_label.pack(side=ctk.LEFT, padx=5)

        self._next_page_btn = ctk.CTkButton(
            page_frame,
            text="▶",
            width=28,
            height=26,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_next_page,
        )
        self._next_page_btn.pack(side=ctk.LEFT)

    def _build_footer(self):
        """构建底部状态栏"""
        footer = ctk.CTkFrame(self.main_frame, fg_color=COLORS["card_bg"], corner_radius=8, height=45)
        footer.pack(fill=ctk.X, pady=(12, 0))
        footer.pack_propagate(False)

        # 状态文本
        self.status_label = ctk.CTkLabel(
            footer,
            text="✅ 就绪",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color=COLORS["success"],
        )
        self.status_label.pack(side=ctk.LEFT, padx=15)

        # 进度条
        self.progress_bar = ctk.CTkProgressBar(
            footer,
            width=200,
            height=8,
            fg_color=COLORS["bg_medium"],
            progress_color=COLORS["accent"],
        )
        self.progress_bar.pack(side=ctk.RIGHT, padx=15)
        self.progress_bar.set(0)

        # 进度文本
        self.progress_label = ctk.CTkLabel(
            footer,
            text="",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            text_color=COLORS["text_secondary"],
        )
        self.progress_label.pack(side=ctk.RIGHT, padx=(0, 10))

    # ─── 版本列表渲染 ─────────────────────────────────────────

    def _render_installed_versions(self, versions: List[str]):
        """渲染已安装版本列表"""
        # 清空现有
        for widget in self.version_list_frame.winfo_children():
            widget.destroy()
        self.version_buttons.clear()

        self.version_count_label.configure(text=f"{len(versions)} 个版本")

        if not versions:
            ctk.CTkLabel(
                self.version_list_frame,
                text="暂无已安装的版本\n请在右侧安装新版本",
                font=ctk.CTkFont(family="Microsoft YaHei", size=13),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=30)
            return

        for ver in versions:
            btn_frame = ctk.CTkFrame(
                self.version_list_frame,
                fg_color=COLORS["bg_medium"],
                corner_radius=8,
                height=42,
            )
            btn_frame.pack(fill=ctk.X, pady=2)
            btn_frame.pack_propagate(False)

            btn = ctk.CTkButton(
                btn_frame,
                text=f"  {ver}",
                font=ctk.CTkFont(family="Microsoft YaHei", size=13),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
                command=lambda v=ver: self._select_version(v),
            )
            btn.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=5, pady=3)

            # 删除按钮
            del_btn = ctk.CTkButton(
                btn_frame,
                text="X",
                width=30,
                height=28,
                font=ctk.CTkFont(family="Microsoft YaHei", size=12, weight="bold"),
                fg_color="transparent",
                hover_color=COLORS["accent"],
                text_color=COLORS["text_secondary"],
                command=lambda v=ver: self._on_delete_version(v),
            )
            del_btn.pack(side=ctk.RIGHT, padx=(0, 5))

            self.version_buttons.append({"frame": btn_frame, "version": ver})

    def _render_available_versions(self, versions: List[Dict[str, Any]]):
        """渲染可用版本列表，按正式版/测试版分类"""
        # 保存全量数据
        self._all_available_versions = versions
        self._release_versions = [v for v in versions if v.get("type") == "release"]
        self._snapshot_versions = [v for v in versions if v.get("type") == "snapshot"]

        # 重置页码
        self._current_page = 1
        self._render_current_tab()

    def _on_version_tab_change(self):
        """Tab 切换回调"""
        self._current_page = 1
        self._render_current_tab()

    def _get_current_tab_versions(self) -> List[Dict[str, Any]]:
        """获取当前 tab 对应的全量版本列表"""
        tab = self.version_tab_var.get()
        if tab == "release":
            return self._release_versions
        return self._snapshot_versions

    def _get_total_pages(self) -> int:
        """获取总页数"""
        versions = self._get_current_tab_versions()
        if not versions:
            return 1
        return max(1, (len(versions) + self._page_size - 1) // self._page_size)

    def _render_current_tab(self):
        """渲染当前选中的 tab 列表（分页）"""
        tab = self.version_tab_var.get()
        all_versions = self._get_current_tab_versions()
        total_pages = self._get_total_pages()

        # 确保页码合法
        if self._current_page > total_pages:
            self._current_page = total_pages
        if self._current_page < 1:
            self._current_page = 1

        # 分页切片
        start = (self._current_page - 1) * self._page_size
        end = start + self._page_size
        display_versions = all_versions[start:end]

        # 更新分页信息
        self._page_info_label.configure(text=f"{self._current_page}/{total_pages}")
        self._prev_page_btn.configure(state=ctk.NORMAL if self._current_page > 1 else ctk.DISABLED)
        self._next_page_btn.configure(state=ctk.NORMAL if self._current_page < total_pages else ctk.DISABLED)

        # 清空列表
        for widget in self.available_list_frame.winfo_children():
            widget.destroy()
        self.available_version_buttons.clear()

        if not display_versions:
            ctk.CTkLabel(
                self.available_list_frame,
                text="暂无版本" if tab == "release" else "暂无测试版",
                font=ctk.CTkFont(family="Microsoft YaHei", size=12),
                text_color=COLORS["text_secondary"],
            ).pack(pady=10)
            return

        for i in range(0, len(display_versions), 2):
            row_frame = ctk.CTkFrame(self.available_list_frame, fg_color="transparent", height=28)
            row_frame.pack(fill=ctk.X, pady=1)
            row_frame.pack_propagate(False)

            for j in range(2):
                if i + j < len(display_versions):
                    ver = display_versions[i + j]
                    ver_id = ver.get("id", "Unknown")
                    ver_type = ver.get("type", "")
                    type_icon = "📦" if ver_type == "release" else "🔬"

                    btn = ctk.CTkButton(
                        row_frame,
                        text=f"{type_icon} {ver_id}",
                        font=ctk.CTkFont(family="Microsoft YaHei", size=11),
                        fg_color="transparent",
                        hover_color=COLORS["bg_light"],
                        text_color=COLORS["text_primary"],
                        anchor=ctk.W,
                        height=26,
                        command=lambda v=ver_id: self._quick_select_version(v),
                    )
                    btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 2))

    def _on_prev_page(self):
        """上一页"""
        if self._current_page > 1:
            self._current_page -= 1
            self._render_current_tab()

    def _on_next_page(self):
        """下一页"""
        if self._current_page < self._get_total_pages():
            self._current_page += 1
            self._render_current_tab()

    def _select_version(self, version: str):
        """选择已安装版本"""
        self.selected_version = version

        # 高亮选中
        for item in self.version_buttons:
            frame = item["frame"]
            if item["version"] == version:
                frame.configure(fg_color=COLORS["bg_light"])
            else:
                frame.configure(fg_color=COLORS["bg_medium"])

        self.set_status(f"已选择: {version}", "info")

    def _quick_select_version(self, version_id: str):
        """快速选择可用版本填入输入框"""
        # 提取纯版本号
        clean_id = version_id.split()[0] if " " in version_id else version_id
        self.version_entry.delete(0, ctk.END)
        self.version_entry.insert(0, clean_id)
        self.set_status(f"已选择版本: {clean_id}", "info")

    def _on_modloader_change(self, *args):
        """模组加载器选项变更回调"""
        loader = self.modloader_var.get()
        if loader and loader != "无":
            self.modloader_hint.configure(
                text=f"提示: 安装 {loader} 会同时安装原版 Minecraft，两者均可独立启动"
            )
        else:
            self.modloader_hint.configure(text="")

    def _on_delete_version(self, version: str):
        """删除版本按钮回调"""
        if not messagebox.askyesno("确认删除", f"确定要删除版本 {version} 吗？\n此操作不可恢复。"):
            return
        self.set_status(f"正在删除 {version}...", "loading")
        self._run_in_thread(self._remove_version, version)

    def _remove_version(self, version_id: str):
        """删除版本（后台线程）"""
        try:
            success, vid = self.callbacks["remove_version"](version_id)
            self._task_queue.put(("remove_done", (vid, success)))
        except Exception as e:
            self._task_queue.put(("remove_error", str(e)))

    # ─── 事件处理 ─────────────────────────────────────────────

    def _on_mirror_toggle(self):
        """镜像源开关切换"""
        enabled = self.mirror_var.get()
        if "set_mirror_enabled" in self.callbacks:
            self.callbacks["set_mirror_enabled"](enabled)
            name = self.callbacks.get("get_mirror_name", lambda: "未知")()
            self.set_status(
                f"已切换到: {name}",
                "success" if enabled else "warning"
            )
            # 测试连接
            self._run_in_thread(self._test_connection)

    def _test_connection(self):
        """测试当前镜像源连接（后台线程）"""
        if "test_mirror_connection" in self.callbacks:
            try:
                ok = self.callbacks["test_mirror_connection"]()
                name = self.callbacks.get("get_mirror_name", lambda: "未知")()
                if ok:
                    self._task_queue.put(("connection_ok", name))
                else:
                    self._task_queue.put(("connection_fail", name))
            except Exception as e:
                self._task_queue.put(("connection_fail", str(e)))

    def _on_app_ready(self):
        """应用初始化完成（由外部调用触发）"""
        self._launcher_ready = True
        self.set_status("正在初始化环境...", "loading")
        self._run_in_thread(self._init_environment)

    def _init_environment(self):
        """初始化环境（后台线程）"""
        try:
            self.callbacks["check_environment"]()
            self._task_queue.put(("init_done", None))
        except Exception as e:
            self._task_queue.put(("init_error", str(e)))

    def _refresh_versions(self):
        """刷新版本列表：先加载已安装版本，再异步加载可用版本"""
        self.set_status("正在加载已安装版本...", "loading")
        self._run_in_thread(self._load_installed_versions)

    def _load_installed_versions(self):
        """加载已安装版本（后台线程，本地操作无需网络）"""
        try:
            installed = self.callbacks["get_installed_versions"]()
            self._task_queue.put(("installed_loaded", installed))
        except Exception as e:
            self._task_queue.put(("load_error", str(e)))

    def _load_available_versions(self):
        """加载可用版本列表（后台线程，需要网络）"""
        try:
            available = self.callbacks["get_available_versions"]()
            self._task_queue.put(("available_loaded", available))
        except Exception as e:
            self._task_queue.put(("available_load_error", str(e)))

    def _on_install(self):
        """安装按钮回调"""
        version_id = self.version_entry.get().strip()
        if not version_id:
            self.set_status("请输入版本 ID", "error")
            return

        mod_loader = self.modloader_var.get()
        loader_text = f" + {mod_loader}" if mod_loader != "无" else ""
        self.set_status(f"正在安装 {version_id}{loader_text}...", "loading")
        self._set_buttons_enabled(False)

        self._run_in_thread(self._install_version, version_id, mod_loader)

    def _install_version(self, version_id: str, mod_loader: str):
        """安装版本（后台线程）"""
        try:
            success, installed_version_id = self.callbacks["install_version"](version_id, mod_loader)
            self._task_queue.put(("install_done", (version_id, installed_version_id, success)))
        except Exception as e:
            self._task_queue.put(("install_error", str(e)))

    def _on_launch(self):
        """启动按钮回调"""
        if not self.selected_version:
            self.set_status("请先选择一个版本", "error")
            return

        version_id = self.selected_version.split()[0] if " " in self.selected_version else self.selected_version
        self.set_status(f"正在启动 {version_id}...", "loading")
        self.launch_btn.configure(state=ctk.DISABLED)

        self._run_in_thread(self._launch_game, version_id)

    def _launch_game(self, version_id: str):
        """启动游戏（后台线程）"""
        try:
            result = self.callbacks["launch_game"](version_id)
            self._task_queue.put(("launch_done", (version_id, result)))
        except Exception as e:
            self._task_queue.put(("launch_error", str(e)))

    # ─── 队列轮询 ─────────────────────────────────────────────

    def _poll_queue(self):
        """轮询任务队列，在主线程中更新UI"""
        if not self._running:
            return

        try:
            while True:
                try:
                    task_type, data = self._task_queue.get_nowait()
                    self._handle_task(task_type, data)
                except queue.Empty:
                    break
        except Exception as e:
            logger.error(f"队列处理错误: {e}")

        self.after(100, self._poll_queue)

    def _handle_task(self, task_type: str, data: Any):
        """处理队列任务"""
        if task_type == "init_done":
            self.set_status("环境初始化完成", "success")
            self._refresh_versions()

        elif task_type == "init_error":
            self.set_status(f"初始化失败: {data}", "error")

        elif task_type == "installed_loaded":
            installed = data
            self._render_installed_versions(installed)
            self.set_status(
                f"已安装 {len(installed)} 个版本 | 正在获取可用版本...",
                "loading"
            )
            # 异步加载可用版本列表（可能因网络失败）
            self._run_in_thread(self._load_available_versions)

        elif task_type == "available_loaded":
            available = data
            self._render_available_versions(available)
            release_count = len([v for v in available if v.get("type") == "release"])
            snapshot_count = len([v for v in available if v.get("type") == "snapshot"])
            installed_count = len([b["version"] for b in self.version_buttons])
            self.set_status(
                f"已安装 {installed_count} 个 | 正式版 {release_count} 个 | 测试版 {snapshot_count} 个",
                "success"
            )

        elif task_type == "available_load_error":
            installed_count = len([b["version"] for b in self.version_buttons])
            self.set_status(
                f"已安装 {installed_count} 个 | 可用版本列表获取失败（离线模式）",
                "warning"
            )

        elif task_type == "load_error":
            self.set_status(f"加载失败: {data}", "error")

        elif task_type == "install_done":
            version_id, installed_version_id, success = data
            if success:
                display = installed_version_id if installed_version_id != version_id else version_id
                self.set_status(f"{display} 安装成功!", "success")
                self.version_entry.delete(0, ctk.END)
                self._refresh_versions()
            else:
                self.set_status(f"{version_id} 安装失败", "error")
            self._set_buttons_enabled(True)

        elif task_type == "install_error":
            self.set_status(f"安装错误: {data}", "error")
            self._set_buttons_enabled(True)

        elif task_type == "remove_done":
            version_id, success = data
            if success:
                self.set_status(f"{version_id} 已删除", "success")
                if self.selected_version == version_id:
                    self.selected_version = None
                self._refresh_versions()
            else:
                self.set_status(f"{version_id} 删除失败", "error")

        elif task_type == "remove_error":
            self.set_status(f"删除错误: {data}", "error")

        elif task_type == "launch_done":
            version_id, success = data
            if success:
                self.set_status(f"{version_id} 已启动", "success")
            else:
                self.set_status(f"{version_id} 启动失败", "error")
            self.launch_btn.configure(state=ctk.NORMAL)

        elif task_type == "launch_error":
            self.set_status(f"启动错误: {data}", "error")
            self.launch_btn.configure(state=ctk.NORMAL)

        elif task_type == "progress_update":
            current, total, status = data
            if total > 0:
                self.progress_bar.set(current / total)
                self.progress_label.configure(text=f"{current}/{total}")
            if status:
                self.set_status(status, "loading")

        elif task_type == "connection_ok":
            self.set_status(f"镜像源连接正常: {data}", "success")

        elif task_type == "connection_fail":
            self.set_status(f"镜像源连接失败: {data}", "warning")

    # ─── 工具方法 ─────────────────────────────────────────────

    def _run_in_thread(self, target: Callable, *args, **kwargs):
        """在后台线程中运行函数"""
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()

    def set_status(self, text: str, status_type: str = "info"):
        """设置状态栏文本"""
        icons = {
            "success": "✅",
            "error": "❌",
            "warning": "⚠️",
            "loading": "⏳",
            "info": "ℹ️",
        }
        colors = {
            "success": COLORS["success"],
            "error": COLORS["accent"],
            "warning": COLORS["warning"],
            "loading": COLORS["text_secondary"],
            "info": COLORS["text_primary"],
        }
        icon = icons.get(status_type, "")
        color = colors.get(status_type, COLORS["text_primary"])
        self.status_label.configure(text=f"{icon} {text}", text_color=color)

    def _set_buttons_enabled(self, enabled: bool):
        """启用/禁用操作按钮"""
        state = ctk.NORMAL if enabled else ctk.DISABLED
        self.install_btn.configure(state=state)

    def update_progress(self, current: int, total: int, status: str = ""):
        """更新进度条（线程安全）"""
        self._task_queue.put(("progress_update", (current, total, status)))

    def _open_resource_manager(self):
        """打开资源管理窗口"""
        if not self.selected_version:
            self.set_status("请先选择一个版本", "error")
            return
        ResourceManagerWindow(self, self.selected_version, self.callbacks)

    def on_closing(self):
        """窗口关闭事件"""
        self._running = False
        self.destroy()


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
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
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
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["card_border"],
            command=self._on_cancel,
        ).pack(side=ctk.RIGHT, padx=(10, 0))

        ctk.CTkButton(
            btn_frame,
            text="选择",
            width=100,
            height=35,
            font=ctk.CTkFont(family="Microsoft YaHei", size=13),
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
                font=ctk.CTkFont(family="Microsoft YaHei", size=13),
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
        font=ctk.CTkFont(family="Microsoft YaHei", size=14),
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

    dialog.mainloop()
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
        font=ctk.CTkFont(family="Microsoft YaHei", size=14),
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

    dialog.mainloop()


# ─── 资源类型配置 ─────────────────────────────────────────────

RESOURCE_TYPES = {
    "mods": {
        "label": "🧩 模组",
        "folder": "mods",
        "extensions": {".jar", ".zip", ".disabled"},
        "description": "将 .jar / .zip 模组文件拖拽到此处安装",
    },
    "resourcepacks": {
        "label": "🎨 资源包",
        "folder": "resourcepacks",
        "extensions": {".zip"},
        "description": "将 .zip 资源包文件拖拽到此处安装",
    },
    "saves": {
        "label": "🗺️ 地图",
        "folder": "saves",
        "extensions": {".zip"},
        "description": "将 .zip 地图存档拖拽到此处安装",
    },
    "shaderpacks": {
        "label": "✨ 光影",
        "folder": "shaderpacks",
        "extensions": {".zip"},
        "description": "将 .zip 光影包文件拖拽到此处安装",
    },
}


class ResourceManagerWindow(ctk.CTkToplevel):
    """资源管理窗口 - 模组/资源包/地图/光影管理"""

    def __init__(self, parent, version_id: str, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self.version_id = version_id
        self.callbacks = callbacks

        self.title(f"资源管理 - {version_id}")
        self.geometry("720x560")
        self.minsize(640, 480)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)

        # 居中
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 720, 560
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self._build_ui()

        # 注册拖拽支持
        if HAS_DND:
            self.after(100, self._register_dnd)

        # 加载当前标签页的资源列表
        self.after(200, self._refresh_current_list)

    def _get_minecraft_dir(self) -> Path:
        """获取当前版本的 .minecraft 目录"""
        if "get_minecraft_dir" in self.callbacks:
            return Path(self.callbacks["get_minecraft_dir"]())
        return Path(".") / ".minecraft"

    def _get_resource_dir(self, resource_type: str) -> Path:
        """获取指定资源类型的目录，优先使用版本隔离目录"""
        mc_dir = self._get_minecraft_dir()
        folder_name: str = RESOURCE_TYPES[resource_type]["folder"]

        # 版本隔离：如果 .minecraft/versions/{版本名}/ 存在，则使用隔离目录
        version_base = mc_dir / "versions" / self.version_id
        if version_base.exists():
            version_dir = version_base / folder_name
            logger.info(f"使用版本隔离目录: {version_dir}")
            return version_dir

        # 回退：全局 .minecraft/{folder}/
        global_dir = mc_dir / folder_name
        logger.info(f"使用全局目录: {global_dir}")
        return global_dir

    def _build_ui(self):
        """构建界面"""
        # 主容器
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        # 标题
        title_label = ctk.CTkLabel(
            main_frame,
            text=f"📁 {self.version_id} - 资源管理",
            font=ctk.CTkFont(family="Microsoft YaHei", size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title_label.pack(anchor=ctk.W, pady=(0, 10))

        # 标签页切换按钮
        tab_frame = ctk.CTkFrame(main_frame, fg_color="transparent", height=38)
        tab_frame.pack(fill=ctk.X, pady=(0, 10))
        tab_frame.pack_propagate(False)

        self._tab_var = ctk.StringVar(value="mods")
        self._tab_buttons: Dict[str, ctk.CTkButton] = {}

        for rtype, rconf in RESOURCE_TYPES.items():
            label_text: str = rconf["label"]
            btn = ctk.CTkButton(
                tab_frame,
                text=label_text,
                height=32,
                font=ctk.CTkFont(family="Microsoft YaHei", size=13, weight="bold"),
                fg_color=COLORS["bg_light"] if rtype == "mods" else "transparent",
                hover_color=COLORS["card_border"],
                text_color=COLORS["text_primary"],
                corner_radius=6,
                command=lambda t=rtype: self._switch_tab(t),
            )
            btn.pack(side=ctk.LEFT, padx=(0, 5))
            self._tab_buttons[rtype] = btn

        # 内容区域
        content_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        content_frame.pack(fill=ctk.BOTH, expand=True)

        # 拖拽提示区 + 操作按钮
        top_bar = ctk.CTkFrame(content_frame, fg_color="transparent", height=42)
        top_bar.pack(fill=ctk.X, padx=12, pady=(10, 5))
        top_bar.pack_propagate(False)

        self._drag_hint_label = ctk.CTkLabel(
            top_bar,
            text=RESOURCE_TYPES["mods"]["description"],
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color=COLORS["text_secondary"],
        )
        self._drag_hint_label.pack(side=ctk.LEFT)

        # 打开文件夹 + 选择文件安装 按钮
        self._open_folder_btn = ctk.CTkButton(
            top_bar,
            text="📂 打开文件夹",
            width=110,
            height=30,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._open_folder,
        )
        self._open_folder_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        self._add_file_btn = ctk.CTkButton(
            top_bar,
            text="➕ 选择文件安装",
            width=130,
            height=30,
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._select_file_install,
        )
        self._add_file_btn.pack(side=ctk.RIGHT)

        # 分割线
        ctk.CTkFrame(content_frame, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(0, 5)
        )

        # 拖拽放置区 + 资源列表
        self._drop_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        self._drop_frame.pack(fill=ctk.BOTH, expand=True, padx=12, pady=(0, 10))

        # 空状态提示（拖拽区域背景）
        self._empty_label = ctk.CTkLabel(
            self._drop_frame,
            text="将文件拖拽到此处\n或点击「选择文件安装」",
            font=ctk.CTkFont(family="Microsoft YaHei", size=14),
            text_color=COLORS["text_secondary"],
            justify=ctk.CENTER,
        )

        # 资源列表（可滚动）- 初始不pack，由_refresh_current_list管理
        self._list_frame = ctk.CTkScrollableFrame(
            self._drop_frame,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )

        # 底部状态栏
        self._status_label = ctk.CTkLabel(
            main_frame,
            text="就绪",
            font=ctk.CTkFont(family="Microsoft YaHei", size=11),
            text_color=COLORS["text_secondary"],
        )
        self._status_label.pack(anchor=ctk.W, pady=(5, 0))

    def _register_dnd(self):
        """注册拖拽支持"""
        if not HAS_DND:
            return
        try:
            self._drop_frame.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self._drop_frame.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            self._list_frame.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self._list_frame.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            logger.info("拖拽支持已注册")
        except Exception as e:
            logger.warning(f"拖拽注册失败: {e}")

    def _on_drop(self, event):
        """拖拽文件放下回调"""
        # tkinterdnd2 传递的路径可能用 {} 包裹且以空格分隔
        raw = event.data
        # 处理 Windows 路径格式
        if raw.startswith("{"):
            files = []
            i = 0
            while i < len(raw):
                if raw[i] == "{":
                    end = raw.index("}", i)
                    files.append(raw[i + 1:end])
                    i = end + 2
                else:
                    parts = raw[i:].split()
                    files.extend(parts)
                    break
        else:
            files = raw.split()

        current_type = self._tab_var.get()
        ext_filter = RESOURCE_TYPES[current_type]["extensions"]

        installed = 0
        for fpath in files:
            fpath = fpath.strip()
            if not fpath:
                continue
            p = Path(fpath)
            if p.exists() and p.suffix.lower() in ext_filter:
                if self._install_resource(fpath, current_type):
                    installed += 1
            elif p.exists() and p.is_dir() and current_type == "saves":
                # 地图存档可能是文件夹
                if self._install_resource(fpath, current_type):
                    installed += 1

        if installed > 0:
            self._set_status(f"成功安装 {installed} 个资源")
            self._refresh_current_list()
        else:
            self._set_status("没有可安装的文件（请检查文件格式）")

    def _switch_tab(self, tab_name: str):
        """切换标签页"""
        self._tab_var.set(tab_name)

        # 更新按钮高亮
        for rtype, btn in self._tab_buttons.items():
            if rtype == tab_name:
                btn.configure(fg_color=COLORS["bg_light"])
            else:
                btn.configure(fg_color="transparent")

        # 更新提示文字
        self._drag_hint_label.configure(text=RESOURCE_TYPES[tab_name]["description"])
        self._refresh_current_list()

    def _refresh_current_list(self):
        """刷新当前标签页的资源列表"""
        current_type = self._tab_var.get()
        resource_dir = self._get_resource_dir(current_type)

        logger.info(f"刷新资源列表: type={current_type}, dir={resource_dir}, exists={resource_dir.exists()}")

        # 先隐藏两个区域
        self._empty_label.pack_forget()
        self._list_frame.pack_forget()

        # 清空列表
        for w in self._list_frame.winfo_children():
            w.destroy()

        if not resource_dir.exists():
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._set_status(f"文件夹不存在: {resource_dir}")
            return

        # 获取资源文件列表
        items = self._scan_resources(resource_dir, current_type)
        logger.info(f"扫描到 {len(items)} 个资源")

        if not items:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._set_status(f"{RESOURCE_TYPES[current_type]['label']} 文件夹为空")
            return

        self._list_frame.pack(fill=ctk.BOTH, expand=True)

        for item in items:
            self._create_resource_item(item, current_type)

        self._set_status(f"共 {len(items)} 个{RESOURCE_TYPES[current_type]['label']}")

    def _scan_resources(self, resource_dir: Path, resource_type: str) -> List[Dict]:
        """扫描资源目录"""
        items = []
        try:
            entries = list(resource_dir.iterdir())
            logger.info(f"目录 {resource_dir} 共有 {len(entries)} 个条目")
            if resource_type == "saves":
                # 地图是文件夹
                for entry in sorted(entries):
                    if entry.is_dir() and not entry.name.startswith("."):
                        # 检查是否是有效的地图存档
                        level_dat = entry / "level.dat"
                        items.append({
                            "name": entry.name,
                            "path": str(entry),
                            "is_dir": True,
                            "has_level_dat": level_dat.exists(),
                        })
            else:
                # 模组/资源包/光影是文件
                ext_filter = RESOURCE_TYPES[resource_type]["extensions"]
                for entry in sorted(entries):
                    if not entry.is_file():
                        continue
                    # 检查文件扩展名：支持 .jar 和 .jar.disabled 等格式
                    is_disabled = entry.suffix.lower() == ".disabled"
                    actual_ext = entry.suffixes[-2].lower() if is_disabled and len(entry.suffixes) >= 2 else entry.suffix.lower()
                    if actual_ext in ext_filter or entry.suffix.lower() in ext_filter:
                        # 文件大小
                        try:
                            size = entry.stat().st_size
                            size_str = self._format_size(size)
                        except Exception:
                            size_str = "?"
                        items.append({
                            "name": entry.name,
                            "path": str(entry),
                            "is_dir": False,
                            "size": size_str,
                            "disabled": is_disabled,
                        })
        except Exception as e:
            logger.error(f"扫描资源目录失败: {e}")

        return items

    def _format_size(self, size: int) -> str:
        """格式化文件大小"""
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"

    def _create_resource_item(self, item: Dict, resource_type: str):
        """创建资源列表项"""
        row = ctk.CTkFrame(
            self._list_frame,
            fg_color=COLORS["bg_medium"],
            corner_radius=6,
            height=36,
        )
        row.pack(fill=ctk.X, pady=2)
        row.pack_propagate(False)

        # 图标
        if item.get("disabled"):
            icon = "🔕"
        elif item.get("is_dir"):
            icon = "📁"
        elif resource_type == "mods":
            icon = "🧩"
        elif resource_type == "resourcepacks":
            icon = "🎨"
        elif resource_type == "shaderpacks":
            icon = "✨"
        else:
            icon = "📄"

        name_text = item["name"]
        if item.get("disabled"):
            name_text += " (已禁用)"
        if item.get("is_dir") and not item.get("has_level_dat"):
            name_text += " (非标准地图)"

        name_label = ctk.CTkLabel(
            row,
            text=f"  {icon} {name_text}",
            font=ctk.CTkFont(family="Microsoft YaHei", size=12),
            text_color=COLORS["text_secondary"] if item.get("disabled") else COLORS["text_primary"],
            anchor=ctk.W,
        )
        name_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=5)

        # 大小信息
        if "size" in item:
            size_label = ctk.CTkLabel(
                row,
                text=item["size"],
                font=ctk.CTkFont(family="Microsoft YaHei", size=11),
                text_color=COLORS["text_secondary"],
            )
            size_label.pack(side=ctk.LEFT, padx=(0, 5))

        # 启用/禁用按钮（仅模组）
        if resource_type == "mods" and not item.get("is_dir"):
            toggle_text = "启用" if item.get("disabled") else "禁用"
            toggle_btn = ctk.CTkButton(
                row,
                text=toggle_text,
                width=50,
                height=26,
                font=ctk.CTkFont(family="Microsoft YaHei", size=11),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_secondary"],
                command=lambda p=item["path"], d=item.get("disabled", False): self._toggle_mod(p, d),
            )
            toggle_btn.pack(side=ctk.RIGHT, padx=(2, 2))

        # 删除按钮
        del_btn = ctk.CTkButton(
            row,
            text="🗑",
            width=30,
            height=26,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=COLORS["accent"],
            text_color=COLORS["text_secondary"],
            command=lambda p=item["path"], n=item["name"]: self._delete_resource(p, n),
        )
        del_btn.pack(side=ctk.RIGHT, padx=(0, 2))

    def _install_resource(self, src_path: str, resource_type: str) -> bool:
        """安装资源文件到对应目录"""
        try:
            resource_dir = self._get_resource_dir(resource_type)
            resource_dir.mkdir(parents=True, exist_ok=True)

            src = Path(src_path)

            if resource_type == "saves":
                return self._install_save(src, resource_dir)
            else:
                dst = resource_dir / src.name
                if dst.exists():
                    logger.warning(f"资源已存在: {dst}")
                    self._set_status(f"文件已存在: {src.name}")
                    return False
                shutil.copy2(str(src), str(dst))
                logger.info(f"资源安装成功: {src.name} -> {dst}")
                return True

        except Exception as e:
            logger.error(f"安装资源失败: {e}")
            self._set_status(f"安装失败: {e}")
            return False

    def _install_save(self, src: Path, saves_dir: Path) -> bool:
        """安装地图存档：支持zip自动解压和文件夹直接复制"""
        import zipfile

        if src.is_dir():
            # 文件夹直接复制到 saves/地图名/
            dst = saves_dir / src.name
            if dst.exists():
                logger.warning(f"地图已存在: {dst}")
                self._set_status(f"地图已存在: {src.name}")
                return False
            shutil.copytree(str(src), str(dst))
            logger.info(f"地图安装成功(文件夹): {src.name} -> {dst}")
            return True

        elif src.suffix.lower() == ".zip":
            # zip 文件解压到 saves/地图名/ 下
            # 地图名 = zip 文件名去掉扩展名
            map_name = src.stem
            dst = saves_dir / map_name
            if dst.exists():
                logger.warning(f"地图已存在: {dst}")
                self._set_status(f"地图已存在: {map_name}")
                return False

            with zipfile.ZipFile(str(src), "r") as zf:
                namelist = zf.namelist()
                # 检查zip内部结构：可能是直接包含level.dat，也可能有一层包装目录
                # 情况1: zip内顶层就有 level.dat -> 解压到 saves/地图名/
                # 情况2: zip内有一个子目录包含 level.dat -> 解压该子目录到 saves/地图名/
                top_entries = [n for n in namelist if "/" not in n.rstrip("/") or n.count("/") == 0]
                has_root_level_dat = any(n == "level.dat" for n in namelist)

                if has_root_level_dat:
                    # 直接解压所有内容到 dst
                    zf.extractall(str(dst))
                else:
                    # 查找包含 level.dat 的子目录
                    level_dat_entries = [n for n in namelist if n.endswith("level.dat")]
                    if level_dat_entries:
                        # 取 level.dat 所在的子目录名
                        sub_dir = level_dat_entries[0].rsplit("level.dat", 1)[0].rstrip("/")
                        # 解压该子目录的内容到 dst
                        for member in zf.namelist():
                            if member.startswith(sub_dir + "/"):
                                # 去掉子目录前缀，提取到 dst
                                relative = member[len(sub_dir) + 1:]
                                if not relative:
                                    continue
                                target = dst / relative
                                if member.endswith("/"):
                                    target.mkdir(parents=True, exist_ok=True)
                                else:
                                    target.parent.mkdir(parents=True, exist_ok=True)
                                    with zf.open(member) as src_file:
                                        with open(str(target), "wb") as dst_file:
                                            dst_file.write(src_file.read())
                    else:
                        # 没找到 level.dat，直接全部解压
                        zf.extractall(str(dst))

            logger.info(f"地图安装成功(zip): {src.name} -> {dst}")
            return True

        else:
            logger.warning(f"不支持的地图格式: {src.suffix}")
            self._set_status(f"不支持的地图格式: {src.suffix}")
            return False

    def _select_file_install(self):
        """通过文件选择对话框安装资源"""
        current_type = self._tab_var.get()
        ext_filter = RESOURCE_TYPES[current_type]["extensions"]

        # 构建文件类型过滤
        ext_list = " ".join(f"*{e}" for e in ext_filter)
        filetypes = [(RESOURCE_TYPES[current_type]["label"], ext_list), ("所有文件", "*.*")]  # type: ignore[list-item]

        from tkinter import filedialog
        files = filedialog.askopenfilenames(
            title=f"选择{RESOURCE_TYPES[current_type]['label']}文件",
            filetypes=filetypes,
        )

        if not files:
            return

        installed = 0
        for f in files:
            if self._install_resource(f, current_type):
                installed += 1

        if installed > 0:
            self._set_status(f"成功安装 {installed} 个资源")
            self._refresh_current_list()
        else:
            self._set_status("未安装任何资源")

    def _open_folder(self):
        """打开当前资源类型的文件夹"""
        current_type = self._tab_var.get()
        resource_dir = self._get_resource_dir(current_type)
        resource_dir.mkdir(parents=True, exist_ok=True)

        try:
            os.startfile(str(resource_dir))
        except Exception as e:
            logger.error(f"打开文件夹失败: {e}")
            self._set_status(f"打开文件夹失败: {e}")

    def _delete_resource(self, path: str, name: str):
        """删除资源"""
        if not messagebox.askyesno("确认删除", f"确定要删除 {name} 吗？"):
            return

        try:
            p = Path(path)
            if p.is_dir():
                shutil.rmtree(str(p))
            else:
                p.unlink()
            logger.info(f"已删除: {name}")
            self._set_status(f"已删除: {name}")
            self._refresh_current_list()
        except Exception as e:
            logger.error(f"删除失败: {e}")
            self._set_status(f"删除失败: {e}")

    def _toggle_mod(self, path: str, is_disabled: bool):
        """启用/禁用模组"""
        try:
            p = Path(path)
            if is_disabled:
                # 启用：移除 .disabled 后缀
                new_path = p.with_suffix("")
                p.rename(new_path)
                logger.info(f"模组已启用: {p.name} -> {new_path.name}")
                self._set_status(f"已启用: {new_path.name}")
            else:
                # 禁用：添加 .disabled 后缀
                new_path = Path(str(p) + ".disabled")
                p.rename(new_path)
                logger.info(f"模组已禁用: {p.name} -> {new_path.name}")
                self._set_status(f"已禁用: {new_path.name}")
            self._refresh_current_list()
        except Exception as e:
            logger.error(f"切换模组状态失败: {e}")
            self._set_status(f"操作失败: {e}")

    def _set_status(self, text: str):
        """更新状态栏"""
        self._status_label.configure(text=text)
