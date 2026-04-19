"""现代化UI界面模块 - 基于 CustomTkinter"""
import os
import io
import re
import sys
import platform
import logging
import subprocess
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
    "error": "#e74c3c",
    "text_primary": "#ffffff",
    "text_secondary": "#a0a0b0",
    "card_bg": "#1e2a4a",
    "card_border": "#2d3a5c",
}


# ─── 跨平台中文字体检测 ──────────────────────────────────────────
def _detect_font_family() -> str:
    """
    检测当前平台可用的中文字体，并返回字体名称。
    
    - Windows: 使用 Microsoft YaHei
    - macOS: 使用 PingFang SC
    - Linux: 通过 fc-list 检测，若无中文字体则尝试自动安装
    """
    system = platform.system().lower()

    if system == "windows":
        return "Microsoft YaHei"

    if system == "darwin":
        return "PingFang SC"

    # ── Linux: 使用 fc-list 检测中文字体 ──
    try:
        result = subprocess.run(
            ["fc-list", ":lang=zh", "family"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            fonts = set()
            for line in result.stdout.strip().split("\n"):
                for f in line.split(","):
                    f = f.strip()
                    if f:
                        fonts.add(f)

            # 按优先级选择
            for preferred in [
                "Noto Sans CJK SC", "Noto Sans SC",
                "WenQuanYi Micro Hei", "WenQuanYi Zen Hei",
                "Droid Sans Fallback",
            ]:
                if preferred in fonts:
                    return preferred

            # 返回第一个可用的中文字体
            if fonts:
                return next(iter(fonts))
    except Exception:
        pass

    # ── 无中文字体，尝试自动安装 ──
    _install_chinese_font()

    # 安装后再检测一次
    try:
        result = subprocess.run(
            ["fc-list", ":lang=zh", "family"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().split("\n")[0].split(",")[0].strip()
    except Exception:
        pass

    return ""  # 使用系统默认字体


def _install_chinese_font():
    """
    尝试在 Linux 上自动安装中文字体。
    
    支持 apt(Debian/Ubuntu)、dnf(Fedora)、pacman(Arch) 包管理器。
    优先使用 pkexec 进行图形化认证，回退到 sudo。
    """
    import shutil

    # 检测包管理器和对应的字体包名
    if shutil.which("apt"):
        pkg_cmd = ["apt", "install", "-y", "fonts-noto-cjk"]
    elif shutil.which("dnf"):
        pkg_cmd = ["dnf", "install", "-y", "google-noto-sans-cjk-fonts"]
    elif shutil.which("pacman"):
        pkg_cmd = ["pacman", "-S", "--noconfirm", "noto-fonts-cjk"]
    else:
        logging.warning("未检测到支持的包管理器，无法自动安装中文字体")
        return

    # 优先 pkexec（图形化认证对话框），回退 sudo
    if shutil.which("pkexec"):
        cmd = ["pkexec"] + pkg_cmd
    elif shutil.which("sudo"):
        cmd = ["sudo"] + pkg_cmd
    else:
        logging.warning("未找到 pkexec 或 sudo，无法安装中文字体")
        return

    try:
        logging.info(f"正在尝试安装中文字体: {' '.join(cmd)}")
        subprocess.run(cmd, timeout=180, check=False)
        # 刷新字体缓存
        subprocess.run(["fc-cache", "-f"], timeout=30, check=False)
        logging.info("中文字体安装完成")
    except Exception as e:
        logging.warning(f"安装中文字体失败: {e}")


FONT_FAMILY = _detect_font_family()
if FONT_FAMILY:
    logging.info(f"使用字体: {FONT_FAMILY}")
else:
    logging.warning("未检测到中文字体，将使用系统默认字体（中文可能显示异常）")


def _get_fmcl_version():
    """从 updater.py 获取 FMCL 版本号"""
    try:
        from updater import get_current_version
        return get_current_version()
    except Exception:
        pass
    return 'unknown'


class ModernApp(ctk.CTk):
    """FMCL 启动器主窗口"""

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
        self._current_skin_path: Optional[str] = None  # 当前皮肤路径

        # 窗口配置
        self.title("FMCL - Fusion Minecraft Launcher")
        self.geometry("1200x860")
        self.minsize(1060, 800)
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
        w, h = 1200, 860
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
            text="⛏ FMCL",
            font=ctk.CTkFont(family=FONT_FAMILY, size=28, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title_label.pack(side=ctk.LEFT, padx=(5, 0))

        subtitle = ctk.CTkLabel(
            header,
            text="Fusion Minecraft Launcher",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
        )
        subtitle.pack(side=ctk.LEFT, padx=(15, 0), pady=(10, 0))

        # 刷新按钮
        refresh_btn = ctk.CTkButton(
            header,
            text="🔄 刷新",
            width=100,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._refresh_versions,
        )
        refresh_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        # 检查更新按钮
        self.update_btn = ctk.CTkButton(
            header,
            text="⬆ 更新",
            width=90,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_check_update,
        )
        self.update_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        # 启动器设置按钮
        settings_btn = ctk.CTkButton(
            header,
            text="⚙ 设置",
            width=90,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._open_launcher_settings,
        )
        settings_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        # 关于按钮（winver 风格）
        about_btn = ctk.CTkButton(
            header,
            text="ℹ 关于",
            width=80,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._show_about,
        )
        about_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        # 保留设置变量（供内部使用）
        self.minimize_var = ctk.BooleanVar(value=self.callbacks.get("get_minimize_on_game_launch", lambda: False)())
        self.mirror_var = ctk.BooleanVar(value=self.callbacks.get("get_mirror_enabled", lambda: True)())

    def _build_content(self):
        """构建内容区域 - 使用标签页"""
        # 创建标签页容器
        self.tabview = ctk.CTkTabview(self.main_frame, fg_color="transparent")
        self.tabview.pack(fill=ctk.BOTH, expand=True, padx=0, pady=0)
        
        # 添加"游戏"标签页
        self.game_tab = self.tabview.add("🎮 游戏")
        self.game_tab.configure(fg_color="transparent")

        # 添加"开服"标签页
        self.server_tab = self.tabview.add("🖥 开服")
        self.server_tab.configure(fg_color="transparent")
        
        # 添加"链接"标签页
        self.links_tab = self.tabview.add("🔗 链接")
        self.links_tab.configure(fg_color="transparent")
        
        # 设置默认标签页为"游戏"
        self.tabview.set("🎮 游戏")
        
        # 构建游戏标签页内容
        self._build_game_tab_content()

        # 构建开服标签页内容
        self._build_server_tab_content()
        
        # 构建链接标签页内容
        self._build_links_tab_content()
    
    def _build_game_tab_content(self):
        """构建游戏标签页内容"""
        content = ctk.CTkFrame(self.game_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)
        
        # 最左侧 - 侧边栏（角色名、皮肤、日志）
        self._build_sidebar(content)

        # 中间 - 已安装版本
        self._build_installed_panel(content)

        # 右侧 - 操作面板
        self._build_action_panel(content)
    
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

    def _build_links_tab_content(self):
        """构建链接标签页内容"""
        # Minecraft相关网站数据
        minecraft_sites = [
            {
                "name": "Minecraft官网",
                "description": "Minecraft官方游戏网站，提供游戏下载、更新和官方信息，支持多平台版本获取与账号管理。",
                "tags": ["官方", "游戏下载", "更新"],
                "link": "https://www.minecraft.net/zh-hans"
            },
            {
                "name": "Minecraft中文官网",
                "description": "Minecraft中国版官方游戏平台，提供网易代理版本的下载、更新和本地化服务。",
                "tags": ["官方", "游戏下载", "中国版"],
                "link": "http://mc.163.com"
            },
            {
                "name": "Minecraft中文Wiki",
                "description": "最全面的Minecraft中文百科全书，提供游戏机制、合成表、生物群系等详细信息，运行12年以上且社区活跃。",
                "tags": ["百科", "知识库", "中文"],
                "link": "https://zh.minecraft.wiki"
            },
            {
                "name": "MineBBS",
                "description": "中国最大的Minecraft资源交流论坛，提供模组、地图、材质包、皮肤等全品类资源，支持Java版与基岩版。",
                "tags": ["论坛", "社区", "资源下载"],
                "link": "https://www.minebbs.com"
            },
            {
                "name": "Minecraft苦力怕论坛",
                "description": "国内活跃的Minecraft中文社区，提供资源下载、技术交流和创作分享，拥有大量基岩版资源。",
                "tags": ["论坛", "社区", "中文"],
                "link": "https://klpbbs.com"
            },
            {
                "name": "CurseForge",
                "description": "全球最大的Minecraft模组下载平台，拥有超过25万个模组资源，支持版本筛选和PCL2/HMCL启动器集成。",
                "tags": ["模组", "资源下载", "插件"],
                "link": "https://www.curseforge.com/minecraft"
            },
            {
                "name": "Modrinth",
                "description": "新兴Minecraft模组资源平台，界面友好、访问速度快，提供模组、资源包和整合包下载，支持中文资源。",
                "tags": ["模组", "资源下载", "整合包"],
                "link": "https://modrinth.com"
            },
            {
                "name": "PlanetMinecraft",
                "description": "专注于Minecraft地图、皮肤和资源包的下载网站，提供详细分类和预览功能，适合寻找特定内容。",
                "tags": ["地图", "皮肤", "资源下载"],
                "link": "https://www.planetminecraft.com"
            },
            {
                "name": "MinecraftSkins.net",
                "description": "全球玩家创作的Minecraft皮肤资源库，提供3D预览、按标签搜索和UUID匹配功能，每日更新新皮肤。",
                "tags": ["皮肤", "资源下载"],
                "link": "https://www.minecraftskins.net"
            },
            {
                "name": "NameMC",
                "description": "Minecraft正版玩家信息查询平台，可查看玩家历史皮肤、UUID信息，支持3D皮肤效果预览。",
                "tags": ["皮肤", "查询"],
                "link": "https://namemc.com"
            },
            {
                "name": "ChunkBase",
                "description": "专业的Minecraft种子查询工具，可分析区块、查找结构、定位生物群系，是建筑和探险的实用助手。",
                "tags": ["工具", "种子查询", "区块分析"],
                "link": "https://www.chunkbase.com"
            },
            {
                "name": "Minecraft教育版官网",
                "description": "Minecraft教育版官方平台，提供教学资源、课程模板和教育工具，专为教师和学生设计。",
                "tags": ["官方", "教育", "资源"],
                "link": "https://education.minecraft.net"
            },
            {
                "name": "Minecraft Heads",
                "description": "提供Minecraft装饰性头颅资源，支持自定义设计和下载，可用于游戏内装饰和建筑。",
                "tags": ["装饰", "资源", "头颅"],
                "link": "https://www.minecraft-heads.com"
            },
            {
                "name": "Amulet地图编辑器",
                "description": "开源的Minecraft世界编辑工具，支持Java 1.12+和Bedrock 1.7+版本，提供三维可视化编辑和精确坐标控制。",
                "tags": ["工具", "地图编辑", "世界转换"],
                "link": "https://gitcode.com/gh_mirrors/am/Amulet-Map-Editor"
            },
            {
                "name": "MCskin",
                "description": "Minecraft皮肤制作与编辑网站，提供皮肤抓取、自定义人物动作、调整光照和颜色背景功能，支持透明底下载。",
                "tags": ["皮肤", "编辑工具", "自定义"],
                "link": "https://mcskins.top"
            },
            {
                "name": "Minecraft Shaders",
                "description": "专注于Minecraft光影包（Shaders）的下载网站，提供各类光影效果的预览和下载，帮助你轻松提升游戏画面表现。",
                "tags": ["光影", "画面", "渲染"],
                "link": "https://minecraftshader.com"
            },
            {
                "name": "Resource Packs",
                "description": "老牌材质包（Resource Packs）下载站，分类详细，提供高清修复、奇幻风格、像素风等多种类型的材质包下载。",
                "tags": ["材质包", "高清修复", "纹理"],
                "link": "https://resourcepack.net"
            },
            {
                "name": "MCPEDL",
                "description": "全球知名的基岩版（Bedrock Edition）资源站，提供海量的 addons、地图、皮肤和模组，是手机版玩家的首选资源库。",
                "tags": ["基岩版", "手机版", "Addons"],
                "link": "https://mcpedl.com"
            },
            {
                "name": "Minecraft Maps",
                "description": "专业的Minecraft地图下载网站，收录了冒险地图、解谜地图、PVP地图和生存挑战等多种玩家自制地图。",
                "tags": ["地图", "冒险", "下载"],
                "link": "http://www.minecraftmaps.com"
            },
            {
                "name": "The Skindex",
                "description": "老牌皮肤网站，拥有庞大的皮肤库和简单易用的在线皮肤编辑器，支持直接预览和下载。",
                "tags": ["皮肤", "编辑器", "社区"],
                "link": "https://www.minecraftskins.com"
            },
            {
                "name": "Minecraft Servers",
                "description": "全球Minecraft服务器列表，玩家可以根据标签（如生存、空岛、小游戏）查找和投票支持喜欢的服务器。",
                "tags": ["服务器", "多人联机", "列表"],
                "link": "https://minecraftservers.org"
            },
            {
                "name": "我的世界服务器列表 (mclists)",
                "description": "国内知名的服务器宣传与列表平台，方便国内玩家查找稳定的中文服务器，涵盖各种玩法类型。",
                "tags": ["服务器", "国内", "中文服"],
                "link": "https://www.mclists.cn"
            },
            {
                "name": "Minecraft Tools",
                "description": "综合性工具箱网站，提供合成表查询、效果查询、附魔计算器、生物生成条件查询等实用功能。",
                "tags": ["工具", "合成表", "计算器"],
                "link": "https://minecraft.tools"
            },
            {
                "name": "Nova Skins",
                "description": "功能强大的皮肤编辑与壁纸生成工具，支持皮肤动图制作、披风编辑以及复杂的滤镜效果处理。",
                "tags": ["皮肤编辑", "壁纸生成", "工具"],
                "link": "https://novaskin.me"
            },
            {
                "name": "mclo.gs",
                "description": "服务器腐竹和玩家必备工具，用于上传和分析游戏崩溃日志（Logs），能快速定位错误原因并提供解决方案建议。",
                "tags": ["日志分析", "除错", "服务器管理"],
                "link": "https://mclo.gs"
            },
            {
                "name": "Chunker",
                "description": "在线存档转换工具，支持将Java版存档转换为基岩版（反之亦然），方便跨平台玩家迁移世界数据。",
                "tags": ["存档转换", "跨平台", "工具"],
                "link": "https://chunker.app"
            },
            {
                "name": "Minecraft Forge",
                "description": "Minecraft Java版最古老的模组加载器官网，提供最新版本的Forge下载，是运行大量经典模组的必要环境。",
                "tags": ["Forge", "模组加载器", "API"],
                "link": "https://www.minecraftforge.net"
            },
            {
                "name": "Fabric",
                "description": "轻量级、高性能的模组加载器，启动速度快，社区活跃，适合喜欢最新版本和轻量级模组的玩家。",
                "tags": ["Fabric", "模组加载器", "高性能"],
                "link": "https://fabricmc.net"
            },
            {
                "name": "ArmorTrims",
                "description": "1.20+版本盔甲纹饰预览工具，可以直观地查看不同锻造模板和材料组合后的盔甲外观效果。",
                "tags": ["盔甲纹饰", "预览", "1.20+"],
                "link": "https://www.armortrims.com"
            }
        ]
        
        # 主容器
        main_container = ctk.CTkFrame(self.links_tab, fg_color="transparent")
        main_container.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)
        
        # 标题
        title_label = ctk.CTkLabel(
            main_container,
            text="🔗 Minecraft 相关网站",
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title_label.pack(anchor=ctk.W, pady=(0, 10))
        
        # 描述
        desc_label = ctk.CTkLabel(
            main_container,
            text="收录了 Minecraft 相关的官方网站、社区、资源下载和工具网站，方便快速访问。",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_secondary"],
        )
        desc_label.pack(anchor=ctk.W, pady=(0, 20))
        
        # 网站列表容器（可滚动）
        scroll_frame = ctk.CTkScrollableFrame(
            main_container,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        scroll_frame.pack(fill=ctk.BOTH, expand=True)
        
        # 创建网站卡片
        for site in minecraft_sites:
            self._create_site_card(scroll_frame, site)
    
    def _create_site_card(self, parent, site):
        """创建网站卡片"""
        card = ctk.CTkFrame(
            parent,
            fg_color=COLORS["card_bg"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["card_border"],
        )
        card.pack(fill=ctk.X, pady=5)
        
        # 卡片内部容器
        card_inner = ctk.CTkFrame(card, fg_color="transparent")
        card_inner.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)
        
        # 网站名称和标签行
        name_frame = ctk.CTkFrame(card_inner, fg_color="transparent")
        name_frame.pack(fill=ctk.X, pady=(0, 8))
        
        # 网站名称
        name_label = ctk.CTkLabel(
            name_frame,
            text=site["name"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        )
        name_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True)
        
        # 标签
        tags_frame = ctk.CTkFrame(name_frame, fg_color="transparent")
        tags_frame.pack(side=ctk.RIGHT)
        
        for tag in site["tags"][:3]:  # 最多显示3个标签
            tag_label = ctk.CTkLabel(
                tags_frame,
                text=tag,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["accent"],
                fg_color=COLORS["bg_medium"],
                corner_radius=10,
                padx=8,
                pady=2,
            )
            tag_label.pack(side=ctk.LEFT, padx=(2, 0))
        
        # 网站描述
        desc_label = ctk.CTkLabel(
            card_inner,
            text=site["description"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=800,
            justify=ctk.LEFT,
            anchor=ctk.W,
        )
        desc_label.pack(fill=ctk.X, pady=(0, 10))
        
        # 链接和按钮行
        link_frame = ctk.CTkFrame(card_inner, fg_color="transparent")
        link_frame.pack(fill=ctk.X)
        
        # 链接地址
        link_label = ctk.CTkLabel(
            link_frame,
            text=site["link"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
        )
        link_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True)
        
        # 打开链接按钮
        def create_open_link_callback(url):
            import webbrowser
            return lambda: webbrowser.open(url)
        
        open_btn = ctk.CTkButton(
            link_frame,
            text="🌐 打开链接",
            width=100,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=create_open_link_callback(site["link"]),
        )
        open_btn.pack(side=ctk.RIGHT, padx=(10, 0))
        
        # 复制链接按钮
        def create_copy_link_callback(url, name):
            import pyperclip
            def copy_func():
                try:
                    pyperclip.copy(url)
                    # 显示复制成功提示
                    if hasattr(self, 'set_status'):
                        self.set_status(f"已复制链接: {name}", "success")
                except Exception as e:
                    if hasattr(self, 'set_status'):
                        self.set_status(f"复制失败: {e}", "error")
            return copy_func
        
        copy_btn = ctk.CTkButton(
            link_frame,
            text="📋 复制链接",
            width=90,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=create_copy_link_callback(site["link"], site["name"]),
        )
        copy_btn.pack(side=ctk.RIGHT)

    def _build_sidebar(self, parent):
        """构建左侧边栏：自定义角色名、自定义皮肤、启动器日志"""
        sidebar = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, width=220)
        sidebar.pack(side=ctk.LEFT, fill=ctk.Y, padx=(0, 10))
        sidebar.pack_propagate(False)

        # ── 自定义角色名 ──
        ctk.CTkLabel(
            sidebar,
            text="👤 角色名",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=12, pady=(15, 5), anchor=ctk.W)

        ctk.CTkFrame(sidebar, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(0, 8)
        )

        self.player_name_var = ctk.StringVar(value="")
        self.player_name_entry = ctk.CTkEntry(
            sidebar,
            textvariable=self.player_name_var,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="输入角色名",
        )
        self.player_name_entry.pack(fill=ctk.X, padx=12, pady=(0, 5))

        self.player_name_entry.bind("<FocusOut>", self._on_player_name_change)

        # ── 自定义皮肤 ──
        ctk.CTkLabel(
            sidebar,
            text="🎨 自定义皮肤",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=12, pady=(15, 5), anchor=ctk.W)

        ctk.CTkFrame(sidebar, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(0, 8)
        )

        # 皮肤预览区
        self.skin_preview_frame = ctk.CTkFrame(
            sidebar, fg_color=COLORS["bg_medium"], corner_radius=8, height=80
        )
        self.skin_preview_frame.pack(fill=ctk.X, padx=12, pady=(0, 5))
        self.skin_preview_frame.pack_propagate(False)

        self.skin_preview_label = ctk.CTkLabel(
            self.skin_preview_frame,
            text="暂无皮肤\n支持 64x64 / 64x32 PNG",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            justify=ctk.CENTER,
        )
        self.skin_preview_label.pack(expand=True)

        # 皮肤操作按钮行
        skin_btn_frame = ctk.CTkFrame(sidebar, fg_color="transparent", height=30)
        skin_btn_frame.pack(fill=ctk.X, padx=12, pady=(0, 5))
        skin_btn_frame.pack_propagate(False)

        ctk.CTkButton(
            skin_btn_frame,
            text="📂 选择皮肤",
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_select_skin,
        ).pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 3))

        self._skin_remove_btn = ctk.CTkButton(
            skin_btn_frame,
            text="🗑",
            width=36,
            height=28,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["accent"],
            text_color=COLORS["text_secondary"],
            command=self._on_remove_skin,
        )
        self._skin_remove_btn.pack(side=ctk.RIGHT)

        # ── 启动器日志 ──
        ctk.CTkLabel(
            sidebar,
            text="📋 启动器日志",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=12, pady=(15, 5), anchor=ctk.W)

        ctk.CTkFrame(sidebar, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(0, 8)
        )

        # 日志文本框（可滚动）
        self.log_text = ctk.CTkTextbox(
            sidebar,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_secondary"],
            activate_scrollbars=True,
            height=200,
            wrap=ctk.WORD,
            spacing3=1,
        )
        self.log_text.pack(fill=ctk.BOTH, expand=True, padx=12, pady=(0, 5))

        # 清空日志按钮
        ctk.CTkButton(
            sidebar,
            text="清空日志",
            height=26,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_clear_log,
        ).pack(fill=ctk.X, padx=12, pady=(0, 12))

        # 设置日志捕获
        self._setup_log_capture()

        # 记录启动日志
        self._append_log("[FMCL] 启动器已启动")

    def _setup_log_capture(self):
        """设置日志捕获，将 logzero 输出重定向到 UI 日志框"""
        self._log_buffer = io.StringIO()
        try:
            import logzero
            # 添加一个自定义 handler 将日志写入 buffer
            self._log_writer = logging.StreamHandler(self._log_buffer)
            self._log_writer.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
            self._log_writer.setLevel(logging.DEBUG)
            logzero.logger.addHandler(self._log_writer)
            self._log_capture_active = True
        except Exception:
            self._log_capture_active = False

    def _append_log(self, message: str):
        """追加日志到 UI 日志框（线程安全）"""
        def _do_append():
            self.log_text.insert(ctk.END, message + "\n")
            self.log_text.see(ctk.END)
        if self.winfo_exists():
            self.after(0, _do_append)

    def _poll_log_buffer(self):
        """轮询日志缓冲区，将新日志写入 UI"""
        if not self._running or not self._log_capture_active:
            return
        content = self._log_buffer.getvalue()
        if content:
            self._log_buffer.seek(0)
            self._log_buffer.truncate(0)
            lines = content.strip().split("\n")
            for line in lines:
                if line.strip():
                    self._append_log(line)
        self.after(500, self._poll_log_buffer)

    def _on_player_name_change(self, event=None):
        """角色名输入框失焦时保存"""
        name = self.player_name_var.get().strip()
        if name and "set_player_name" in self.callbacks:
            self.callbacks["set_player_name"](name)

    def _on_select_skin(self):
        """选择皮肤文件"""
        from tkinter import filedialog
        filetypes = [("皮肤文件", "*.png"), ("所有文件", "*.*")]
        filepath = filedialog.askopenfilename(
            title="选择皮肤文件",
            filetypes=filetypes,
        )
        if not filepath:
            return

        # 验证皮肤文件尺寸
        try:
            from PIL import Image
            with Image.open(filepath) as img:
                w, h = img.size
                if (w, h) not in [(64, 64), (64, 32), (128, 128), (128, 64)]:
                    self.set_status(f"皮肤尺寸 {w}x{h} 不支持，请使用 64x64 或 64x32", "warning")
                    return
        except ImportError:
            pass  # 无 PIL，跳过尺寸验证
        except Exception:
            self.set_status("无法读取皮肤文件", "error")
            return

        self._current_skin_path = filepath
        self._update_skin_preview(filepath)

        if "set_skin_path" in self.callbacks:
            self.callbacks["set_skin_path"](filepath)

        # 复制皮肤到 .minecraft 目录
        if "get_minecraft_dir" in self.callbacks:
            mc_dir = Path(self.callbacks["get_minecraft_dir"]())
            skin_dir = mc_dir / "skins"
            skin_dir.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(filepath, str(skin_dir / Path(filepath).name))
            self.set_status(f"皮肤已安装: {Path(filepath).name}", "success")

    def _update_skin_preview(self, filepath: str):
        """更新皮肤预览"""
        filename = Path(filepath).name
        if len(filename) > 20:
            filename = filename[:17] + "..."
        self.skin_preview_label.configure(text=f"✅ {filename}", text_color=COLORS["success"])

    def _on_remove_skin(self):
        """移除皮肤"""
        self._current_skin_path = None
        self.skin_preview_label.configure(text="暂无皮肤\n支持 64x64 / 64x32 PNG", text_color=COLORS["text_secondary"])
        if "set_skin_path" in self.callbacks:
            self.callbacks["set_skin_path"](None)
        self.set_status("皮肤已移除", "info")

    def _on_clear_log(self):
        """清空日志"""
        self.log_text.delete("1.0", ctk.END)

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
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self.version_count_label = ctk.CTkLabel(
            title_frame,
            text="0 个版本",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
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

        # 底部启动/结束按钮
        launch_frame = ctk.CTkFrame(panel, fg_color="transparent", height=50)
        launch_frame.pack(fill=ctk.X, padx=15, pady=(0, 12))
        launch_frame.pack_propagate(False)

        self.launch_btn = ctk.CTkButton(
            launch_frame,
            text="🚀 启动游戏",
            height=40,
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_launch,
        )
        self.launch_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        self.kill_btn = ctk.CTkButton(
            launch_frame,
            text="⏹",
            width=50,
            height=40,
            font=ctk.CTkFont(size=16),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            text_color=COLORS["text_primary"],
            command=self._on_kill_game,
        )
        self.kill_btn.pack(side=ctk.RIGHT, padx=(8, 0))
        self.kill_btn.configure(state=ctk.DISABLED)

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
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=15, pady=(15, 8), anchor=ctk.W)

        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(0, 10)
        )

        # 版本ID输入
        ctk.CTkLabel(
            panel,
            text="版本 ID:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(padx=15, anchor=ctk.W)

        self.version_entry = ctk.CTkEntry(
            panel,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="例如: 1.20.4 或 26.1",
        )
        self.version_entry.pack(fill=ctk.X, padx=15, pady=(5, 10))

        # 模组加载器选项
        ctk.CTkLabel(
            panel,
            text="模组加载器:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(padx=15, anchor=ctk.W)

        self.modloader_var = ctk.StringVar(value="无")
        self.modloader_menu = ctk.CTkOptionMenu(
            panel,
            variable=self.modloader_var,
            values=["无", "Forge", "Fabric", "NeoForge"],
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["warning"],
            wraplength=260,
            justify=ctk.LEFT,
        )
        self.modloader_hint.pack(padx=15, anchor=ctk.W, pady=(0, 10))
        self.modloader_var.trace_add("write", self._on_modloader_change)
        self._on_modloader_change()

        # 安装按钮 + 整合包按钮并排
        btn_row = ctk.CTkFrame(panel, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=15, pady=(0, 15))

        self.install_btn = ctk.CTkButton(
            btn_row,
            text="📥 安装版本",
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_install,
        )
        self.install_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 5))

        self.modpack_btn = ctk.CTkButton(
            btn_row,
            text="📦 安装整合包",
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_install_modpack,
        )
        self.modpack_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(5, 0))

        # ── 版本选择器 ──
        ctk.CTkLabel(
            panel,
            text="📋 快速选择",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self.progress_label.pack(side=ctk.RIGHT, padx=(0, 10))

        self._launch_anim_running = False

    # ─── 版本列表渲染 ─────────────────────────────────────────

    @staticmethod
    def _has_mod_loader(version_id: str) -> bool:
        """判断版本是否安装了模组加载器"""
        v = version_id.lower()
        return any(loader in v for loader in ("forge", "fabric", "neoforge"))

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
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=30)
            return

        for ver in versions:
            has_loader = self._has_mod_loader(ver)
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
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
                command=lambda v=ver: self._select_version(v),
            )
            btn.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=5, pady=3)

            # 删除按钮（最右边）
            del_btn = ctk.CTkButton(
                btn_frame,
                text="X",
                width=30,
                height=28,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                fg_color="transparent",
                hover_color=COLORS["accent"],
                text_color=COLORS["text_secondary"],
                command=lambda v=ver: self._on_delete_version(v),
            )
            del_btn.pack(side=ctk.RIGHT, padx=(0, 5))

            # 版本设置按钮
            settings_btn = ctk.CTkButton(
                btn_frame,
                text="⚙",
                width=30,
                height=28,
                font=ctk.CTkFont(size=14),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_secondary"],
                command=lambda v=ver: self._open_resource_manager_for_version(v),
            )
            settings_btn.pack(side=ctk.RIGHT, padx=(0, 2))

            # 安装模组按钮（仅模组加载器版本显示，最左边）
            if has_loader:
                mod_btn = ctk.CTkButton(
                    btn_frame,
                    text="🧩",
                    width=30,
                    height=28,
                    font=ctk.CTkFont(size=14),
                    fg_color="transparent",
                    hover_color=COLORS["bg_light"],
                    text_color=COLORS["success"],
                    command=lambda v=ver: self._open_mod_browser(v),
                )
                mod_btn.pack(side=ctk.RIGHT, padx=(0, 2))

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
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
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
                        font=ctk.CTkFont(family=FONT_FAMILY, size=11),
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

    # ─── 服务器标签页方法 ─────────────────────────────────────────

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
        import re
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

    def _on_kill_game(self):
        """强制结束游戏进程"""
        if "kill_game_process" in self.callbacks:
            success = self.callbacks["kill_game_process"]()
            if success:
                self._killed_by_user = True
                self.set_status("游戏进程已强制结束", "warning")
            else:
                self.set_status("没有正在运行的游戏进程", "info")
            self.kill_btn.configure(state=ctk.DISABLED)

    def _start_launch_animation(self):
        """启动进度条加载动画（来回滚动）"""
        self._launch_anim_running = True
        self._launch_anim_pos = 0.0
        self._launch_anim_dir = 1
        self.progress_label.configure(text="加载中...")
        self._launch_animate_step()

    def _launch_animate_step(self):
        """进度条动画单步"""
        if not self._launch_anim_running:
            return
        step = 0.02
        self._launch_anim_pos += step * self._launch_anim_dir
        if self._launch_anim_pos >= 1.0:
            self._launch_anim_pos = 1.0
            self._launch_anim_dir = -1
        elif self._launch_anim_pos <= 0.0:
            self._launch_anim_pos = 0.0
            self._launch_anim_dir = 1
        self.progress_bar.set(self._launch_anim_pos)
        self.after(50, self._launch_animate_step)

    def _stop_launch_animation(self):
        """停止进度条加载动画"""
        self._launch_anim_running = False
        self.progress_bar.set(0)
        self.progress_label.configure(text="")

    def _watch_game_exit(self):
        """监控游戏进程退出（后台线程），检测崩溃并通知主线程"""
        if "get_game_process" not in self.callbacks:
            return

        proc = self.callbacks["get_game_process"]()
        if proc is None:
            return

        # 等待进程退出（阻塞）
        proc.wait()
        exit_code = proc.returncode
        logger.info(f"游戏进程已退出，退出码: {exit_code}")

        if exit_code != 0 and not getattr(self, "_killed_by_user", False):
            # 退出码非 0 视为崩溃，收集崩溃文件信息
            crash_files = self._collect_crash_info()
            self._task_queue.put(("game_crashed", {"exit_code": exit_code, "crash_files": crash_files}))
        else:
            self._task_queue.put(("game_exited", None))

    def _collect_crash_info(self):
        """收集崩溃相关文件信息（后台线程调用），支持版本隔离目录"""
        import platform
        
        files = {}
        mc_dir = None
        try:
            if "get_minecraft_dir" in self.callbacks:
                mc_dir = Path(self.callbacks["get_minecraft_dir"]())
            if mc_dir and mc_dir.exists():
                base_dir = mc_dir.parent  # 项目根目录
                system = platform.system().lower()
                
                # ── 游戏日志：根据平台在不同位置查找 ──
                if system == "linux":
                    # Linux: 日志在 /var/log/fmcl/
                    logs_dir = Path("/var/log/fmcl")
                    if logs_dir.exists():
                        latest_log = logs_dir / "latest.log"
                        if latest_log.exists():
                            files["game_log"] = str(latest_log)
                        debug_log = logs_dir / "debug.log"
                        if debug_log.exists():
                            files["debug_log"] = str(debug_log)
                else:
                    # Windows/macOS: 日志在项目根目录的 ./logs 下
                    logs_dir = base_dir / "logs"
                    if logs_dir.exists():
                        latest_log = logs_dir / "latest.log"
                        if latest_log.exists():
                            files["game_log"] = str(latest_log)
                        debug_log = logs_dir / "debug.log"
                        if debug_log.exists():
                            files["debug_log"] = str(debug_log)

                # ── 崩溃报告：在版本隔离目录或全局 .minecraft 下查找 ──
                version_id = getattr(self, "_running_version_id", None)
                game_dirs = []
                if version_id:
                    version_game_dir = mc_dir / "versions" / version_id
                    if version_game_dir.exists():
                        game_dirs.append(version_game_dir)
                game_dirs.append(mc_dir)  # 最后检查全局目录作为回退

                for game_dir in game_dirs:
                    # 崩溃报告（取最新的）
                    crash_dir = game_dir / "crash-reports"
                    if crash_dir.exists():
                        crash_reports = sorted(crash_dir.glob("crash-*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
                        if crash_reports:
                            if "crash_report" not in files:
                                files["crash_report"] = str(crash_reports[0])
                            if "crash_report_list" not in files:
                                files["crash_report_list"] = [str(f) for f in crash_reports[:10]]
                    # JVM 崩溃日志 (hs_err_pid*.log)
                    hs_err_files = sorted(game_dir.glob("hs_err_pid*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
                    if hs_err_files and "jvm_crash_log" not in files:
                        files["jvm_crash_log"] = str(hs_err_files[0])
        except Exception as e:
            logger.error(f"收集崩溃信息失败: {e}")
        return files

    def _watch_game_stdout(self):
        """监控游戏进程 stdout，检测到游戏窗口出现后关闭管道以避免缓冲区满导致游戏卡顿（后台线程）"""
        if "get_game_process" not in self.callbacks:
            return

        proc = self.callbacks["get_game_process"]()
        if proc is None or proc.stdout is None:
            logger.warning("无法获取游戏进程 stdout")
            return

        marker = "Datafixer optimizations took"
        timeout = 120

        import threading
        import time

        logger.info("开始监控游戏进程 stdout")

        result = {"detected": False}

        def _reader():
            try:
                for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="ignore")
                    if not result["detected"] and marker in line:
                        result["detected"] = True
                        return
            except Exception:
                pass

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        # 等待检测到标记或超时
        start = time.time()
        while time.time() - start < timeout and self._running:
            if result["detected"]:
                logger.info("检测到游戏窗口出现 (Datafixer Bootstrap)，关闭 stdout 管道")
                try:
                    proc.stdout.close()
                except Exception:
                    pass
                self._task_queue.put(("game_window_detected", None))
                return
            if not reader_thread.is_alive() and not result["detected"]:
                # 读线程退出但没检测到标记（进程可能已退出），关闭管道
                logger.info("游戏 stdout 已关闭，释放管道")
                try:
                    proc.stdout.close()
                except Exception:
                    pass
                return
            time.sleep(0.2)

        # 超时：关闭管道避免缓冲区问题
        logger.info(f"游戏 stdout 监控超时 ({timeout}s)，关闭管道")
        try:
            proc.stdout.close()
        except Exception:
            pass

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

    # ─── 更新检查 ─────────────────────────────────────────────

    def _on_check_update(self):
        """检查更新按钮回调"""
        self.set_status("正在检查更新...", "loading")
        self.update_btn.configure(state=ctk.DISABLED)
        self._run_in_thread(self._check_update)

    def _check_update(self, silent: bool = False):
        """
        检查更新（后台线程）

        Args:
            silent: 是否静默模式（无新版本时不提示）
        """
        try:
            from updater import check_for_update
            release_info = check_for_update()

            if release_info:
                self._task_queue.put(("update_available", (release_info["version"], release_info.get("body", ""))))
            elif not silent:
                self._task_queue.put(("update_no_new", None))

        except Exception as e:
            if not silent:
                self._task_queue.put(("update_check_error", str(e)))

        finally:
            # 恢复按钮状态
            self.after(0, lambda: self.update_btn.configure(state=ctk.NORMAL))

    def _show_update_dialog(self, version: str, body: str):
        """显示更新可用对话框"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("发现新版本")
        dialog.geometry("500x350")
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self)
        dialog.grab_set()

        # 居中
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 500) // 2
        y = (dialog.winfo_screenheight() - 350) // 2
        dialog.geometry(f"500x350+{x}+{y}")

        # 标题
        ctk.CTkLabel(
            dialog,
            text=f"⬆ 发现新版本: {version}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(pady=(20, 10))

        # 更新日志
        changelog_frame = ctk.CTkScrollableFrame(
            dialog,
            fg_color=COLORS["bg_medium"],
            height=160,
        )
        changelog_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=(0, 10))

        display_text = body if body else "暂无更新日志"
        ctk.CTkLabel(
            changelog_frame,
            text=display_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_primary"],
            wraplength=440,
            justify=ctk.LEFT,
            anchor=ctk.W,
        ).pack(fill=ctk.BOTH, expand=True, padx=5, pady=5)

        # 按钮
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(0, 20))

        def on_update():
            dialog.grab_release()
            dialog.destroy()
            self._start_update_download()

        def on_skip():
            dialog.grab_release()
            dialog.destroy()
            self.set_status(f"已跳过更新 {version}", "info")

        ctk.CTkButton(
            btn_frame,
            text="立即更新",
            width=120,
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=on_update,
        ).pack(side=ctk.LEFT, padx=10)

        ctk.CTkButton(
            btn_frame,
            text="稍后再说",
            width=120,
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["card_border"],
            command=on_skip,
        ).pack(side=ctk.LEFT, padx=10)

    def _start_update_download(self):
        """开始下载并安装更新"""
        self.set_status("正在准备下载更新...", "loading")
        self.progress_bar.set(0)
        self._run_in_thread(self._do_update)

    def _do_update(self):
        """执行更新下载与安装（后台线程）"""
        try:
            from updater import check_for_update, find_suitable_asset, download_update, install_update

            # 重新检查获取 release 信息
            release_info = check_for_update()
            if not release_info:
                self._task_queue.put(("update_check_error", "未找到新版本"))
                return

            # 查找适合当前平台的安装包
            asset = find_suitable_asset(release_info["assets"])
            if not asset:
                self._task_queue.put(("update_download_error", "未找到适合当前平台的安装包"))
                return

            # 下载
            def _progress(downloaded: int, total: int):
                self._task_queue.put(("update_download_progress", (downloaded, total)))

            file_path = download_update(asset, progress_callback=_progress)
            if not file_path:
                self._task_queue.put(("update_download_error", "下载失败"))
                return

            self._task_queue.put(("update_download_done", None))

            # 安装
            success = install_update(file_path)
            if success:
                self._task_queue.put(("update_install_started", None))
            else:
                self._task_queue.put(("update_download_error", "启动安装程序失败"))

        except Exception as e:
            logger.error(f"更新失败: {e}")
            self._task_queue.put(("update_download_error", str(e)))

    def _on_app_ready(self):
        """应用初始化完成（由外部调用触发）"""
        self._launcher_ready = True
        # 重新加载用户设置
        self._reload_user_settings()
        # 启动日志轮询
        if self._log_capture_active:
            self._poll_log_buffer()

    def _reload_user_settings(self):
        """重新加载用户设置（callbacks 设置后调用）"""
        # 加载角色名
        if "get_player_name" in self.callbacks:
            saved_name = self.callbacks["get_player_name"]()
            if saved_name:
                self.player_name_var.set(saved_name)
        # 加载皮肤路径
        if "get_skin_path" in self.callbacks:
            saved_skin = self.callbacks["get_skin_path"]()
            if saved_skin and os.path.exists(saved_skin):
                self._current_skin_path = saved_skin
                self._update_skin_preview(saved_skin)
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

    def _on_install_modpack(self):
        """打开整合包安装窗口"""
        ModpackInstallWindow(self, self.callbacks)

    def _launch_game(self, version_id: str):
        """启动游戏（后台线程）"""
        try:
            minimize = self.minimize_var.get()
            success, target_version = self.callbacks["launch_game"](version_id, minimize_after=minimize)
            self._task_queue.put(("launch_done", (version_id, target_version, success)))
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
            # 加载服务器版本列表
            self._run_in_thread(self._load_server_versions)

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
            version_id, target_version, success = data
            if success:
                self._running_version_id = target_version or version_id
                self._killed_by_user = False
                self.set_status(f"{version_id} 已启动，等待游戏窗口...", "loading")
                self.kill_btn.configure(state=ctk.NORMAL)
                self._start_launch_animation()
                # 无论是否最小化都启动日志监控，检测到窗口后关闭管道避免缓冲区满导致游戏卡顿
                self._run_in_thread(self._watch_game_stdout)
                self._run_in_thread(self._watch_game_exit)
            else:
                self.set_status(f"{version_id} 启动失败", "error")
            self.launch_btn.configure(state=ctk.NORMAL)

        elif task_type == "launch_error":
            self.set_status(f"启动错误: {data}", "error")
            self.launch_btn.configure(state=ctk.NORMAL)

        # ── 服务器相关任务处理 ──
        elif task_type == "server_installed_loaded":
            installed = data
            self._render_server_versions(installed)

        elif task_type == "server_available_loaded":
            available = data
            self._render_server_available_versions(available)
            server_release_count = len(available)
            self.set_status(
                f"服务器可用 {server_release_count} 个正式版",
                "success"
            )

        elif task_type == "server_error":
            self.set_status(f"服务器操作错误: {data}", "error")

        elif task_type == "server_install_done":
            version_id, success = data
            if success:
                self.set_status(f"服务器 {version_id} 安装成功", "success")
                # 重新加载服务器列表
                if "get_installed_servers" in self.callbacks:
                    installed = self.callbacks["get_installed_servers"]()
                    self._render_server_versions(installed)
            else:
                self.set_status(f"服务器 {version_id} 安装失败", "error")
            self.server_install_btn.configure(state=ctk.NORMAL)

        elif task_type == "server_install_error":
            self.set_status(f"安装服务器错误: {data}", "error")
            self.server_install_btn.configure(state=ctk.NORMAL)

        elif task_type == "server_start_done":
            version_id, success = data
            if success:
                self.set_status(f"服务器 {version_id} 已启动", "success")
                self.server_stop_btn.configure(state=ctk.NORMAL)
                self.server_log_status_label.configure(text=f"运行中 ({version_id})", text_color=COLORS["success"])
                self._server_online_players = []
                self._update_player_display()
                self.server_mem_label.configure(text="0 MB")
                self._append_server_log(f"[FMCL] 服务器 {version_id} 已启动")
                # 启动服务器日志读取与退出监控
                self._run_in_thread(self._watch_server_exit)
                # 启动内存监控
                self._start_mem_monitor()
            else:
                self.set_status(f"服务器 {version_id} 启动失败", "error")
                self.server_start_btn.configure(state=ctk.NORMAL)

        elif task_type == "server_start_error":
            self.set_status(f"启动服务器错误: {data}", "error")
            self.server_start_btn.configure(state=ctk.NORMAL)

        elif task_type == "server_log":
            self._append_server_log(data)

        elif task_type == "server_exit":
            exit_code = data
            self.server_stop_btn.configure(state=ctk.DISABLED)
            self.server_start_btn.configure(state=ctk.NORMAL)
            self.server_log_status_label.configure(text="已停止", text_color=COLORS["text_secondary"])
            self._stop_mem_monitor()
            self._server_online_players = []
            self._update_player_display()
            self.server_mem_label.configure(text="0 MB")
            if exit_code == 0:
                self.set_status("服务器已正常停止", "info")
            else:
                self.set_status(f"服务器异常退出 (退出码: {exit_code})", "error")

        elif task_type == "server_remove_done":
            version_id, success = data
            if success:
                self.set_status(f"服务器 {version_id} 已删除", "success")
                # 如果删除的是当前选中的，取消选择
                if self.selected_server_version == version_id:
                    self.selected_server_version = None
                # 重新加载服务器列表
                if "get_installed_servers" in self.callbacks:
                    installed = self.callbacks["get_installed_servers"]()
                    self._render_server_versions(installed)
            else:
                self.set_status(f"删除服务器 {version_id} 失败", "error")

        elif task_type == "server_remove_error":
            self.set_status(f"删除服务器错误: {data}", "error")

        elif task_type == "server_join_done":
            version_id, success = data
            self.server_join_btn.configure(state=ctk.NORMAL)
            if success:
                self.set_status(f"正在加入服务器 ({version_id})", "success")
                self._running = True
                self.kill_btn.configure(state=ctk.NORMAL)
                self._start_launch_animation()
                # 加入服务器不监控 stdout（管道未捕获），仅监控进程退出
                self._run_in_thread(self._watch_game_exit)
            else:
                self.set_status(f"启动游戏失败 ({version_id})", "error")

        elif task_type == "server_join_error":
            self.server_join_btn.configure(state=ctk.NORMAL)
            self.set_status(f"加入服务器错误: {data}", "error")

        elif task_type == "game_window_detected":
            self._stop_launch_animation()
            if self.minimize_var.get():
                self.set_status("游戏窗口已出现，启动器已最小化", "success")
                self.iconify()
            else:
                self.set_status("游戏已就绪", "success")

        elif task_type == "game_exited":
            self._stop_launch_animation()
            self.kill_btn.configure(state=ctk.DISABLED)
            self.set_status("游戏已正常退出", "info")

        elif task_type == "game_crashed":
            self._stop_launch_animation()
            self.kill_btn.configure(state=ctk.DISABLED)
            exit_code = data["exit_code"]
            crash_files = data["crash_files"]
            self.set_status(f"游戏异常退出 (退出码: {exit_code})", "error")
            # 恢复最小化的窗口
            try:
                self.deiconify()
            except Exception:
                pass
            self._show_crash_dialog(exit_code, crash_files)

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

        elif task_type == "update_available":
            version, body = data
            self._show_update_dialog(version, body)

        elif task_type == "update_no_new":
            self.set_status("当前已是最新版本", "success")

        elif task_type == "update_check_error":
            self.set_status(f"检查更新失败: {data}", "warning")

        elif task_type == "update_download_progress":
            downloaded, total = data
            if total > 0:
                self.progress_bar.set(downloaded / total)
                self.progress_label.configure(text=f"{downloaded // (1024*1024)}MB / {total // (1024*1024)}MB")

        elif task_type == "update_download_done":
            self.set_status("下载完成，正在启动安装程序...", "success")

        elif task_type == "update_download_error":
            self.set_status(f"更新下载失败: {data}", "error")
            self.progress_bar.set(0)
            self.progress_label.configure(text="")

        elif task_type == "update_install_started":
            self.set_status("正在退出，即将开始安装更新...", "success")
            self.after(500, self.on_closing)

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

    def _open_resource_manager_for_version(self, version_id: str):
        """为指定版本打开资源管理窗口"""
        ResourceManagerWindow(self, version_id, self.callbacks)

    def _show_about(self):
        """显示关于对话框"""
        about = ctk.CTkToplevel(self)
        about.title("关于 FMCL")
        about.resizable(False, False)
        about.transient(self)
        about.grab_set()
        about.configure(fg_color=COLORS["bg_dark"])

        # 窗口尺寸与居中
        w, h = 460, 360
        about.geometry(f"{w}x{h}")
        about.update_idletasks()
        x = (about.winfo_screenwidth() - w) // 2
        y = (about.winfo_screenheight() - h) // 2
        about.geometry(f"+{x}+{y}")

        # 主容器
        main_frame = ctk.CTkFrame(about, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=30, pady=30)

        # 顶部区域：logo + 标题
        top_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        top_frame.pack(fill=ctk.X, pady=(0, 15))

        # Logo
        icon_path = os.path.join(
            getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
            "icon.ico",
        )
        icon_displayed = False
        if os.path.exists(icon_path):
            try:
                from PIL import Image as PILImage

                pil_img = PILImage.open(icon_path).resize((64, 64), PILImage.Resampling.LANCZOS)
                ctk_img = ctk.CTkImage(pil_img, size=(80, 80))
                ctk.CTkLabel(top_frame, image=ctk_img, text="").pack(
                    side=ctk.LEFT, padx=(0, 15)
                )
                # 保存对图像的引用以防止被垃圾回收
                setattr(about, '_icon_ref', ctk_img)
                icon_displayed = True
            except Exception:
                pass

        if not icon_displayed:
            ctk.CTkLabel(
                top_frame,
                text="\u26cf",
                font=ctk.CTkFont(size=36),
                text_color=COLORS["accent"],
            ).pack(side=ctk.LEFT, padx=(0, 15))

        # 标题信息
        info_frame = ctk.CTkFrame(top_frame, fg_color="transparent")
        info_frame.pack(side=ctk.LEFT)
        ctk.CTkLabel(
            info_frame,
            text="FMCL",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)
        ctk.CTkLabel(
            info_frame,
            text="Fusion Minecraft Launcher",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W)

        # 分隔线
        ctk.CTkFrame(
            main_frame, height=1, fg_color=COLORS["card_border"]
        ).pack(fill=ctk.X, pady=(0, 15))

        # 系统信息
        info_items = [
            ("版本", _get_fmcl_version()),
            ("Python", platform.python_version()),
            ("系统", f"{platform.system()} {platform.release()}"),
            ("架构", platform.machine()),
        ]

        info_container = ctk.CTkFrame(main_frame, fg_color="transparent")
        info_container.pack(fill=ctk.X, pady=(0, 15))

        for label_text, value in info_items:
            row = ctk.CTkFrame(info_container, fg_color="transparent")
            row.pack(fill=ctk.X, pady=2)
            ctk.CTkLabel(
                row,
                text=label_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
                width=60,
                anchor=ctk.E,
            ).pack(side=ctk.LEFT)
            ctk.CTkLabel(
                row,
                text=value,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
            ).pack(side=ctk.LEFT, padx=(10, 0))

        # 底部确定按钮
        ctk.CTkButton(
            main_frame,
            text="确定",
            width=80,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=about.destroy,
        ).pack(pady=(20, 0))

        about.bind("<Return>", lambda e: about.destroy())
        about.focus_set()

    # ── 崩溃类型检测 ──────────────────────────────────────────────

    CRASH_TYPES = [
        {
            "name": "Mixin 错误",
            "icon": "\U0001f9ec",
            "required": ["org.spongepowered.asm.mixin"],
            "optional": ["mixin apply for mod", ".mixins.json"],
            "cause": "优化类模组（如 Sodium、OptiFine）在修改游戏底层代码时注入失败，可能因目标代码不存在、签名不匹配或版本错误。",
            "advice": "检查崩溃报告中 .mixins.json 前的模组名，更新或移除该模组。",
        },
        {
            "name": "模组加载异常",
            "icon": "\u26a0\ufe0f",
            "required": ["Mod Loading has failed", "net.minecraftforge.fml.LoadingFailedException"],
            "optional": ["Could not execute entrypoint stage"],
            "cause": "某个模组初始化失败（配置文件错误、注册表溢出等）。",
            "advice": "查看崩溃报告中 Suspected Mod 字段或 Mod List 中标记为 E 的模组，更新或移除该模组。",
        },
        {
            "name": "依赖缺失/版本错误",
            "icon": "\U0001f517",
            "required": ["ClassNotFoundException", "NoClassDefFoundError", "NoSuchMethodError", "NoSuchFieldError"],
            "optional": ["Missing mod", "Requires", "depends"],
            "cause": "缺少必需的模组、模组版本与游戏或其他模组不兼容，或 API 版本不匹配。",
            "advice": "安装缺失的依赖模组，或更新相关模组到兼容版本。",
        },
        {
            "name": "模组冲突",
            "icon": "\u2694\ufe0f",
            "required": ["Exception caught during firing event: null"],
            "optional": ["conflict", "incompatible", "already registered", "Duplicate"],
            "cause": "两个或多个模组同时修改同一游戏内容，或模组之间不兼容。",
            "advice": "查看 Suspected Mods 字段，逐个禁用可疑模组定位冲突来源。",
        },
        {
            "name": "内存溢出",
            "icon": "\U0001f4be",
            "required": ["java.lang.OutOfMemoryError"],
            "optional": ["Unable to allocate", "heap space", "Metaspace", "GC overhead limit exceeded"],
            "cause": "分配给 Minecraft 的内存不足、内存泄漏（通常由模组引起）或数据集过大。",
            "advice": "在启动器设置中增加最大内存分配（建议 4-8GB），或检查是否有模组导致内存泄漏。",
        },
        {
            "name": "渲染与图形错误",
            "icon": "\U0001f3a8",
            "required": ["OpenGL"],
            "optional": ["GL error", "Shader", "Tesselator", "Rendering", "GPU", "Driver"],
            "cause": "显卡驱动问题、过时的 OpenGL 版本、着色器编译错误或显卡不兼容。",
            "advice": "更新显卡驱动，移除或更新光影/渲染优化模组，确保显卡支持所需 OpenGL 版本。",
        },
        {
            "name": "线程与并发错误",
            "icon": "\U0001f9f5",
            "required": ["ConcurrentModificationException"],
            "optional": ["Deadlock", "Thread stuck", "Wait timed out"],
            "cause": "模组在多线程环境下未正确处理同步。",
            "advice": "更新相关模组，或尝试移除最近添加的模组。",
        },
        {
            "name": "网络同步错误",
            "icon": "\U0001f310",
            "required": ["Connection refused"],
            "optional": ["Read timed out", "Packet handler", "NetworkManager"],
            "cause": "模组自定义网络包未正确注册、数据结构不一致或网络环境不稳定。",
            "advice": "检查网络连接，更新涉及网络功能的模组。",
        },
        {
            "name": "世界生成错误",
            "icon": "\U0001f5fa\ufe0f",
            "required": ["World Generation"],
            "optional": ["Chunk Loading", "Structure", "Biome", "Feature"],
            "cause": "模组的生物群系、结构或特征注册错误、生成算法有 bug，或与其他修改世界生成的模组冲突。",
            "advice": "更新涉及世界生成的模组，或创建新世界测试。",
        },
        {
            "name": "服务端/客户端逻辑错误",
            "icon": "\U0001f9e9",
            "required": ["Integrated Server"],
            "optional": ["Dedicated Server", "Logic error"],
            "cause": "模组未正确区分逻辑客户端与逻辑服务器，导致数据不同步。",
            "advice": "更新相关模组，检查模组是否支持当前游戏版本。",
        },
        {
            "name": "Java 虚拟机崩溃",
            "icon": "\U0001f4a5",
            "required": ["SIGSEGV", "EXCEPTION_ACCESS_VIOLATION"],
            "optional": ["Problematic frame", "fatal error"],
            "cause": "Java 版本不兼容、JVM 参数错误、本地代码崩溃（通常由模组触发）或硬件/驱动问题。",
            "advice": "更换兼容的 Java 版本，检查 JVM 参数，更新显卡驱动。",
        },
    ]

    def _diagnose_crash(self, crash_files: dict) -> list:
        """根据崩溃日志内容分析崩溃类型，返回匹配到的崩溃类型列表"""
        # 收集所有可用的日志文本
        text_parts = []

        for key in ("crash_report", "game_log", "debug_log", "jvm_crash_log"):
            path = crash_files.get(key)
            if path and os.path.exists(path):
                try:
                    for enc in ("utf-8", "gbk", "latin-1"):
                        try:
                            text_parts.append(Path(path).read_text(enc, errors="ignore"))
                            break
                        except (UnicodeDecodeError, UnicodeError):
                            continue
                except Exception:
                    pass

        combined_text = "\n".join(text_parts)
        if not combined_text.strip():
            return []

        matched = []
        for crash_type in self.CRASH_TYPES:
            required_hits = sum(1 for kw in crash_type["required"] if kw in combined_text)
            optional_hits = sum(1 for kw in crash_type["optional"] if kw in combined_text)
            if required_hits > 0:
                matched.append({
                    "name": crash_type["name"],
                    "icon": crash_type["icon"],
                    "cause": crash_type["cause"],
                    "advice": crash_type["advice"],
                    "score": required_hits + optional_hits * 0.5,
                })

        # 按匹配得分降序排列
        matched.sort(key=lambda x: x["score"], reverse=True)
        return matched[:3]  # 最多返回前 3 个最可能的崩溃类型

    def _show_crash_dialog(self, exit_code: int, crash_files: dict):
        """显示崩溃提示对话框"""
        import tkinter as tk
        from tkinter import filedialog
        import zipfile
        import shutil
        from datetime import datetime

        dialog = tk.Toplevel(self)
        dialog.title("游戏崩溃")
        dialog.resizable(False, False)
        dialog.attributes('-topmost', True)
        dialog.configure(bg='#1a1a2e')
        dialog.transient(self)
        dialog.grab_set()

        # 诊断崩溃类型
        diagnoses = self._diagnose_crash(crash_files)

        # 窗口尺寸根据诊断结果动态调整
        w = 440
        h_base = 290
        h_diag = min(len(diagnoses), 3) * 62 if diagnoses else 0
        h = h_base + h_diag
        dialog.geometry(f"{w}x{h}")
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - w) // 2
        y = (dialog.winfo_screenheight() - h) // 2
        dialog.geometry(f"+{x}+{y}")

        pad = 24

        # 崩溃图标
        icon_path = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))), 'icon.ico')
        icon_frame = tk.Frame(dialog, bg='#1a1a2e')
        icon_frame.place(x=pad, y=pad, width=64, height=64)
        if os.path.exists(icon_path):
            try:
                from PIL import Image as PILImage, ImageTk
                pil_img = PILImage.open(icon_path).resize((64, 64), PILImage.LANCZOS)
                tk_img = ImageTk.PhotoImage(pil_img)
                tk.Label(icon_frame, image=tk_img, bg='#1a1a2e').pack()
                # 保存对图像的引用以防止被垃圾回收
                setattr(dialog, '_icon_ref', tk_img)
            except Exception:
                tk.Label(icon_frame, text='\u26cf', font=(FONT_FAMILY, 28), fg='#e94560', bg='#1a1a2e').pack()
        else:
            tk.Label(icon_frame, text='\u26cf', font=(FONT_FAMILY, 28), fg='#e94560', bg='#1a1a2e').pack()

        # 崩溃信息
        has_crash_report = "crash_report" in crash_files
        has_game_log = "game_log" in crash_files
        has_jvm_crash = "jvm_crash_log" in crash_files

        info_text = f"游戏异常退出 (退出码: {exit_code})"
        tk.Label(dialog, text=info_text, font=(FONT_FAMILY, 14, 'bold'),
                 fg='#e94560', bg='#1a1a2e').place(x=pad + 72, y=pad + 5)

        detail_parts = []
        if has_crash_report:
            detail_parts.append("已检测到崩溃报告")
        if has_jvm_crash:
            detail_parts.append("JVM 崩溃日志可用")
        if has_game_log:
            detail_parts.append("游戏日志可用")
        detail = "；".join(detail_parts) if detail_parts else "未找到崩溃报告文件，仍可尝试导出"
        tk.Label(dialog, text=detail, font=(FONT_FAMILY, 10),
                 fg='#8899aa', bg='#1a1a2e').place(x=pad + 72, y=pad + 38)

        # 分隔线
        tk.Frame(dialog, bg='#0f3460', height=1).place(x=pad, y=pad + 68, width=w - 2 * pad)

        # 诊断结果区域
        diag_y = pad + 78
        if diagnoses:
            for i, diag in enumerate(diagnoses):
                y = diag_y + i * 62
                # 背景框
                diag_frame = tk.Frame(dialog, bg='#16213e', highlightbackground='#0f3460', highlightthickness=1)
                diag_frame.place(x=pad, y=y, width=w - 2 * pad, height=56)
                # 标题行
                tk.Label(diag_frame, text=f"{diag['icon']} {diag['name']}",
                         font=(FONT_FAMILY, 10, 'bold'), fg='#e94560', bg='#16213e').pack(
                    anchor='w', padx=10, pady=(6, 0))
                # 建议（单行截断）
                advice_text = diag['advice']
                if len(advice_text) > 48:
                    advice_text = advice_text[:47] + "…"
                tk.Label(diag_frame, text=f"💡 {advice_text}",
                         font=(FONT_FAMILY, 9), fg='#8899aa', bg='#16213e').pack(
                    anchor='w', padx=10, pady=(2, 0))
        btn_y = diag_y + h_diag + 8
        btn_h = 38

        def _open_crash_report():
            path = crash_files.get("crash_report")
            if path and os.path.exists(path):
                os.startfile(path)

        def _open_game_log():
            path = crash_files.get("game_log")
            if path and os.path.exists(path):
                os.startfile(path)
            else:
                mc_dir = None
                try:
                    if "get_minecraft_dir" in self.callbacks:
                        mc_dir = Path(self.callbacks["get_minecraft_dir"]())
                    if mc_dir:
                        log_dir = mc_dir / "logs"
                        if log_dir.exists():
                            os.startfile(str(log_dir))
                            return
                except Exception:
                    pass
                messagebox.showinfo("提示", "未找到游戏日志文件", parent=dialog)

        def _export_crash_report():
            filetypes = [("ZIP 压缩包", "*.zip")]
            default_name = f"FMCL-crash-{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            save_path = filedialog.asksaveasfilename(
                parent=dialog,
                title="导出崩溃报告",
                defaultextension=".zip",
                initialfile=default_name,
                filetypes=filetypes,
            )
            if not save_path:
                return
            try:
                with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    # 崩溃报告文件
                    if "crash_report" in crash_files:
                        p = crash_files["crash_report"]
                        if os.path.exists(p):
                            zf.write(p, f"crash-reports/{os.path.basename(p)}")
                    if "crash_report_list" in crash_files:
                        for p in crash_files["crash_report_list"]:
                            if os.path.exists(p):
                                zf.write(p, f"crash-reports/{os.path.basename(p)}")

                    # 游戏日志
                    for key, arcname in [("game_log", "logs/latest.log"), ("debug_log", "logs/debug.log"), ("jvm_crash_log", "hs_err_pid.log")]:
                        p = crash_files.get(key)
                        if p and os.path.exists(p):
                            zf.write(p, arcname)

                    # 启动器日志（从内存缓冲区或磁盘文件获取）
                    launcher_log_content = ""
                    # 优先从内存缓冲区获取
                    if hasattr(self, '_log_buffer') and self._log_buffer:
                        launcher_log_content = self._log_buffer.getvalue()
                    # 如果缓冲区为空，回退到 logzero 的磁盘日志文件
                    if not launcher_log_content.strip():
                        import platform as _platform
                        system = _platform.system().lower()
                        
                        if system == "linux":
                            # Linux: 日志在 /var/log/fmcl/latest.log
                            disk_log = Path("/var/log/fmcl/latest.log")
                        else:
                            # Windows/macOS: 日志在项目根目录
                            base_dir = crash_files.get("_mc_dir")
                            if base_dir:
                                disk_log = Path(base_dir) / "latest.log"
                            else:
                                disk_log = None
                        
                        if disk_log and disk_log.exists():
                            try:
                                # Windows 下 logzero 可能使用 GBK 编码
                                for enc in ("utf-8", "gbk", "latin-1"):
                                    try:
                                        launcher_log_content = disk_log.read_text(enc)
                                        break
                                    except (UnicodeDecodeError, UnicodeError):
                                        continue
                            except Exception:
                                pass
                    if launcher_log_content.strip():
                        zf.writestr("launcher.log", launcher_log_content.encode("utf-8", errors="replace"))


                    # 系统信息摘要
                    import platform as _platform
                    sys_info = (
                        f"FMCL Crash Report\n"
                        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"Exit Code: {exit_code}\n"
                        f"OS: {_platform.system()} {_platform.release()}\n"
                        f"Python: {_platform.python_version()}\n"
                        f"Architecture: {_platform.machine()}\n"
                    )
                    zf.writestr("system-info.txt", sys_info)

                messagebox.showinfo("导出成功", f"崩溃报告已保存至:\n{save_path}", parent=dialog)
            except Exception as e:
                messagebox.showerror("导出失败", f"导出崩溃报告时出错:\n{e}", parent=dialog)

        # 按钮样式参数
        btn_style = dict(font=(FONT_FAMILY, 10), relief='flat', cursor='hand2',
                         bg='#0f3460', fg='white', activebackground='#2d3a5c', activeforeground='white',
                         bd=0, highlightthickness=0)

        btn1 = tk.Button(dialog, text="📄 打开崩溃报告", command=_open_crash_report,
                         state='normal' if has_crash_report else 'disabled', **btn_style)
        btn1.place(x=pad, y=btn_y, width=w - 2 * pad, height=btn_h)

        btn2 = tk.Button(dialog, text="📋 打开游戏日志", command=_open_game_log,
                         state='normal' if has_game_log else 'normal', **btn_style)
        btn2.place(x=pad, y=btn_y + btn_h + 8, width=w - 2 * pad, height=btn_h)

        btn3 = tk.Button(dialog, text="📦 导出崩溃报告", command=_export_crash_report,
                         bg='#e94560', fg='white', activebackground='#ff6b81', activeforeground='white',
                         font=(FONT_FAMILY, 10, 'bold'), relief='flat', cursor='hand2',
                         bd=0, highlightthickness=0)
        btn3.place(x=pad, y=btn_y + (btn_h + 8) * 2, width=w - 2 * pad, height=btn_h)

        # AI 分析按钮
        _jdz_token = self.callbacks.get("get_jdz_token", lambda: None)() if self.callbacks else None

        ai_btn = tk.Button(dialog, text="🤖 AI 智能分析（净读 AI）",
                           command=lambda: self._ai_analyze_crash(crash_files, exit_code),
                           bg='#6c5ce7', fg='white', activebackground='#a29bfe', activeforeground='white',
                           font=(FONT_FAMILY, 10, 'bold'), relief='flat', cursor='hand2',
                           bd=0, highlightthickness=0,
                           state='normal' if _jdz_token else 'disabled')
        ai_btn.place(x=pad, y=btn_y + (btn_h + 8) * 3, width=w - 2 * pad, height=btn_h)

        if not _jdz_token:
            tk.Label(dialog, text="请先在设置中登录净读账号",
                     font=(FONT_FAMILY, 8), fg='#667788', bg='#1a1a2e').place(
                x=pad, y=btn_y + (btn_h + 8) * 3 + btn_h + 2)

        # 调整窗口高度以容纳新按钮
        h += btn_h + 8 + (12 if not _jdz_token else 0)
        dialog.geometry(f"{w}x{h}")
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - w) // 2
        y = (dialog.winfo_screenheight() - h) // 2
        dialog.geometry(f"+{x}+{y}")

        # 关闭按钮
        close_btn = tk.Button(dialog, text='关闭', command=dialog.destroy,
                              font=(FONT_FAMILY, 9), relief='flat', cursor='hand2',
                              bg='#1a1a2e', fg='#667788', activebackground='#1a1a2e',
                              activeforeground='#aabbcc', bd=0)
        close_btn.place(x=w // 2 - 20, y=h - 36, width=40)

    def _read_file_tail(self, filepath: str, lines: int = 200) -> str:
        """读取文件最后 lines 行"""
        if not filepath or not os.path.exists(filepath):
            return ""
        try:
            for enc in ("utf-8", "gbk", "latin-1"):
                try:
                    with open(filepath, "r", encoding=enc, errors="ignore") as f:
                        return "".join(f.readlines()[-lines:])
                except (UnicodeDecodeError, UnicodeError):
                    continue
        except Exception:
            pass
        return ""

    def _collect_ai_context(self, crash_files: dict, exit_code: int) -> str:
        """收集发送给 AI 的崩溃上下文信息"""
        parts = []

        # 系统信息
        parts.append(f"[系统信息]\nOS: {platform.system()} {platform.release()}\n"
                     f"Python: {platform.python_version()}\n"
                     f"Architecture: {platform.machine()}\n"
                     f"退出码: {exit_code}")

        # 崩溃报告（完整内容，通常不大）
        crash_report = crash_files.get("crash_report")
        if crash_report:
            content = self._read_file_tail(crash_report, 99999)
            if content:
                parts.append(f"[崩溃报告]\n{content}")

        # 游戏日志最后 200 行
        game_log = crash_files.get("game_log")
        if game_log:
            content = self._read_file_tail(game_log, 200)
            if content:
                parts.append(f"[游戏日志（最后200行）]\n{content}")

        # debug 日志最后 200 行
        debug_log = crash_files.get("debug_log")
        if debug_log:
            content = self._read_file_tail(debug_log, 200)
            if content:
                parts.append(f"[Debug 日志（最后200行）]\n{content}")

        # JVM 崩溃日志
        jvm_log = crash_files.get("jvm_crash_log")
        if jvm_log:
            content = self._read_file_tail(jvm_log, 200)
            if content:
                parts.append(f"[JVM 崩溃日志（最后200行）]\n{content}")

        # 启动器日志最后 200 行
        launcher_log = ""
        if hasattr(self, '_log_buffer') and self._log_buffer:
            launcher_log = self._log_buffer.getvalue()
        if not launcher_log.strip():
            try:
                system = platform.system().lower()
                if system == "linux":
                    disk_log = Path("/var/log/fmcl/latest.log")
                else:
                    disk_log = Path("latest.log")
                if disk_log.exists():
                    for enc in ("utf-8", "gbk", "latin-1"):
                        try:
                            launcher_log = disk_log.read_text(enc, errors="ignore")
                            break
                        except (UnicodeDecodeError, UnicodeError):
                            continue
            except Exception:
                pass
        if launcher_log.strip():
            log_lines = launcher_log.strip().splitlines()[-200:]
            parts.append(f"[启动器日志（最后200行）]\n" + "\n".join(log_lines))

        return "\n\n".join(parts)

    def _ai_analyze_crash(self, crash_files: dict, exit_code: int):
        """AI 分析崩溃（后台线程请求，主线程弹窗）"""
        token = self.callbacks.get("get_jdz_token", lambda: None)() if self.callbacks else None
        if not token:
            messagebox.showwarning("提示", "请先在设置中登录净读账号", parent=self)
            return

        # 收集上下文
        context = self._collect_ai_context(crash_files, exit_code)
        if not context.strip():
            messagebox.showwarning("提示", "未找到可用于分析的日志信息", parent=self)
            return

        # 构建请求消息
        system_prompt = (
            "你是一个 Minecraft 崩溃日志分析专家。根据用户提供的崩溃报告、游戏日志和系统信息，"
            "分析崩溃原因并给出具体、可操作的建议。\n"
            "请用中文回复，格式如下：\n"
            "## 崩溃原因分析\n（简明扼要地说明崩溃原因）\n\n"
            "## 建议操作\n（列出具体的解决步骤，每步用数字编号）"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请分析以下 Minecraft 崩溃信息：\n\n{context}"},
        ]

        # 显示加载窗口
        import tkinter as tk
        loading = tk.Toplevel(self)
        loading.title("AI 分析中...")
        loading.geometry("320x100")
        loading.resizable(False, False)
        loading.attributes('-topmost', True)
        loading.configure(bg='#1a1a2e')
        loading.transient(self)
        loading.grab_set()
        loading.update_idletasks()
        lx = (loading.winfo_screenwidth() - 320) // 2
        ly = (loading.winfo_screenheight() - 100) // 2
        loading.geometry(f"+{lx}+{ly}")

        tk.Label(loading, text="🤖 AI 正在分析崩溃原因...",
                 font=(FONT_FAMILY, 12), fg='#a0a0b0', bg='#1a1a2e').pack(pady=(20, 5))
        tk.Label(loading, text="请稍候，这可能需要几秒钟",
                 font=(FONT_FAMILY, 9), fg='#667788', bg='#1a1a2e').pack()

        def _do_analyze():
            import urllib.request
            import urllib.error
            import json
            try:
                req_data = json.dumps({
                    "model": "deepseek-chat",
                    "messages": messages,
                    "stream": False,
                }).encode("utf-8")

                req = urllib.request.Request(
                    "https://jingdu.qzz.io/api/deepseek/v1/chat/completions",
                    data=req_data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                        "User-Agent": "FMCL/1.0 (Minecraft Launcher; crash-analyzer)",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

                ai_content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not ai_content:
                    ai_content = "AI 未返回有效分析结果。"
                self.after(0, lambda: _show_result(ai_content))
            except urllib.error.HTTPError as e:
                _code = e.code
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                _err_msg = f"HTTP {_code}: {body[:200]}"
                self.after(0, lambda: _show_error(_err_msg))
            except Exception as e:
                _err_msg = str(e)
                self.after(0, lambda: _show_error(_err_msg))
            finally:
                self.after(0, loading.destroy)

        def _show_result(content: str):
            self._show_ai_result_dialog(content)

        def _show_error(msg: str):
            messagebox.showerror("AI 分析失败", f"分析请求失败:\n{msg}", parent=self)

        threading.Thread(target=_do_analyze, daemon=True).start()

    def _show_ai_result_dialog(self, content: str):
        """显示 AI 分析结果弹窗，支持保存为 txt"""
        import tkinter as tk
        from tkinter import filedialog
        from datetime import datetime

        result = tk.Toplevel(self)
        result.title("AI 崩溃分析结果")
        result.geometry("580x640")
        result.resizable(True, True)
        result.attributes('-topmost', True)
        result.configure(bg='#1a1a2e')
        result.transient(self)
        result.grab_set()
        result.update_idletasks()
        rx = (result.winfo_screenwidth() - 580) // 2
        ry = (result.winfo_screenheight() - 640) // 2
        result.geometry(f"+{rx}+{ry}")

        # 标题
        tk.Label(result, text="🤖 AI 崩溃分析结果",
                 font=(FONT_FAMILY, 14, 'bold'), fg='#ffffff', bg='#1a1a2e').pack(anchor='w', padx=16, pady=(16, 8))

        # 内容区域（可滚动文本框）
        text_frame = tk.Frame(result, bg='#16213e', highlightbackground='#2d3a5c', highlightthickness=1)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))

        text_widget = tk.Text(
            text_frame, wrap=tk.WORD, font=(FONT_FAMILY, 11),
            fg='#ffffff', bg='#16213e', bd=0, padx=12, pady=12,
            insertbackground='white', selectbackground='#0f3460',
            relief='flat',
        )
        scrollbar = tk.Scrollbar(text_frame, command=text_widget.yview, bg='#1a1a2e',
                                 troughcolor='#16213e', activebackground='#0f3460')
        text_widget.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert('1.0', content)
        text_widget.configure(state='disabled')

        # 按钮区
        btn_frame = tk.Frame(result, bg='#1a1a2e')
        btn_frame.pack(fill=tk.X, padx=16, pady=(0, 16))

        def _save_as_txt():
            default_name = f"FMCL-AI分析-{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            save_path = filedialog.asksaveasfilename(
                parent=result,
                title="保存分析结果",
                defaultextension=".txt",
                initialfile=default_name,
                filetypes=[("文本文件", "*.txt")],
            )
            if save_path:
                try:
                    with open(save_path, "w", encoding="utf-8") as f:
                        f.write(f"FMCL AI 崩溃分析结果\n"
                                f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"{'=' * 50}\n\n"
                                f"{content}\n")
                    messagebox.showinfo("保存成功", f"分析结果已保存至:\n{save_path}", parent=result)
                except Exception as e:
                    messagebox.showerror("保存失败", f"保存时出错:\n{e}", parent=result)

        tk.Button(btn_frame, text="💾 保存为 TXT", command=_save_as_txt,
                  font=(FONT_FAMILY, 10), relief='flat', cursor='hand2',
                  bg='#0f3460', fg='white', activebackground='#2d3a5c', activeforeground='white',
                  bd=0).pack(side=tk.LEFT)

        tk.Button(btn_frame, text="关闭", command=result.destroy,
                  font=(FONT_FAMILY, 10), relief='flat', cursor='hand2',
                  bg='#e94560', fg='white', activebackground='#ff6b81', activeforeground='white',
                  bd=0, width=80).pack(side=tk.RIGHT)

    def _open_launcher_settings(self):
        """打开启动器设置窗口"""
        LauncherSettingsWindow(self, self.callbacks)

    def _open_mod_browser(self, version_id: str):
        """打开模组浏览窗口（从 Modrinth 搜索安装模组）"""
        ModBrowserWindow(self, version_id, self.callbacks)

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
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
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
                font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._drag_hint_label.pack(side=ctk.LEFT)

        # 打开文件夹 + 选择文件安装 按钮
        self._open_folder_btn = ctk.CTkButton(
            top_bar,
            text="📂 打开文件夹",
            width=110,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
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
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"] if item.get("disabled") else COLORS["text_primary"],
            anchor=ctk.W,
        )
        name_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=5)

        # 大小信息
        if "size" in item:
            size_label = ctk.CTkLabel(
                row,
                text=item["size"],
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
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
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
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
        import shutil  # 延迟导入：仅资源管理窗口使用
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
        import shutil  # 延迟导入
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
        import shutil  # 延迟导入

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
        try:
            if self.winfo_exists():
                self._status_label.configure(text=text)
        except Exception:
            pass


class LauncherSettingsWindow(ctk.CTkToplevel):
    """启动器设置窗口"""

    def __init__(self, parent, callbacks: Dict[str, Callable]):
        super().__init__(fg_color=COLORS["bg_dark"])
        self.callbacks = callbacks
        self.parent = parent

        self.title("启动器设置")
        self.geometry("450x580")
        self.resizable(False, False)
        self.grab_set()

        self._build_ui()

    def destroy(self):
        """销毁窗口，先处理 CTkSlider 的 bug"""
        if hasattr(self, '_threads_slider'):
            try:
                if hasattr(self._threads_slider, '_variable'):
                    self._threads_slider._variable = None
            except Exception:
                pass
        super().destroy()

    def _build_ui(self):
        """构建设置界面"""
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        # 标题
        title = ctk.CTkLabel(
            container,
            text="⚙ 启动器设置",
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title.pack(anchor=ctk.W, pady=(0, 20))

        # 启动后最小化开关
        minimize_frame = ctk.CTkFrame(container, fg_color="transparent")
        minimize_frame.pack(fill=ctk.X, pady=10)

        minimize_label = ctk.CTkLabel(
            minimize_frame,
            text="🔽 启动后最小化",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_primary"],
        )
        minimize_label.pack(side=ctk.LEFT)

        self.minimize_var = ctk.BooleanVar(value=self.callbacks.get("get_minimize_on_game_launch", lambda: False)())
        minimize_switch = ctk.CTkSwitch(
            minimize_frame,
            text="",
            variable=self.minimize_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["text_secondary"],
            progress_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            command=self._on_minimize_toggle,
        )
        minimize_switch.pack(side=ctk.RIGHT)

        # 国内镜像源开关
        mirror_frame = ctk.CTkFrame(container, fg_color="transparent")
        mirror_frame.pack(fill=ctk.X, pady=10)

        mirror_label = ctk.CTkLabel(
            mirror_frame,
            text="🇨🇳 使用国内镜像源",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_primary"],
        )
        mirror_label.pack(side=ctk.LEFT)

        self.mirror_var = ctk.BooleanVar(value=self.callbacks.get("get_mirror_enabled", lambda: True)())
        mirror_switch = ctk.CTkSwitch(
            mirror_frame,
            text="",
            variable=self.mirror_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["text_secondary"],
            progress_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            command=self._on_mirror_toggle,
        )
        mirror_switch.pack(side=ctk.RIGHT)

        # ── 净读 AI 账号 ──
        jdz_section = ctk.CTkFrame(container, fg_color=COLORS["bg_medium"], corner_radius=8)
        jdz_section.pack(fill=ctk.X, pady=(15, 5))

        jdz_title = ctk.CTkLabel(
            jdz_section,
            text="🤖 净读 AI（崩溃智能分析）",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        jdz_title.pack(anchor=ctk.W, padx=12, pady=(10, 5))

        # Token 状态
        _saved_token = self.callbacks.get("get_jdz_token", lambda: None)()
        token_status = "已登录" if _saved_token else "未登录"
        token_color = COLORS["success"] if _saved_token else COLORS["text_secondary"]
        self.jdz_status_label = ctk.CTkLabel(
            jdz_section,
            text=f"状态: {token_status}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=token_color,
        )
        self.jdz_status_label.pack(anchor=ctk.W, padx=12, pady=(0, 5))

        # 登录表单
        login_form = ctk.CTkFrame(jdz_section, fg_color="transparent")
        login_form.pack(fill=ctk.X, padx=12, pady=(0, 10))

        self.jdz_user_entry = ctk.CTkEntry(
            login_form,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            placeholder_text="用户名",
            width=140,
        )
        self.jdz_user_entry.pack(side=ctk.LEFT, padx=(0, 5))

        self.jdz_pass_entry = ctk.CTkEntry(
            login_form,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            placeholder_text="密码",
            width=140,
            show="•",
        )
        self.jdz_pass_entry.pack(side=ctk.LEFT, padx=(0, 5))

        self.jdz_login_btn = ctk.CTkButton(
            login_form,
            text="登录",
            width=50,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_jdz_login,
        )
        self.jdz_login_btn.pack(side=ctk.LEFT, padx=(0, 5))

        self.jdz_logout_btn = ctk.CTkButton(
            login_form,
            text="退出",
            width=50,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_jdz_logout,
        )
        self.jdz_logout_btn.pack(side=ctk.LEFT)

        # 注册链接（单独一行）
        import webbrowser
        register_btn = ctk.CTkButton(
            jdz_section,
            text="没有账号？去注册",
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            anchor="w",
            command=lambda: webbrowser.open("https://jingdu.qzz.io/register"),
        )
        register_btn.pack(anchor="w", padx=12, pady=(0, 10))

        # 下载线程数滑块
        threads_frame = ctk.CTkFrame(container, fg_color="transparent")
        threads_frame.pack(fill=ctk.X, pady=10)

        threads_label = ctk.CTkLabel(
            threads_frame,
            text="⚡ 下载线程数",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_primary"],
        )
        threads_label.pack(side=ctk.LEFT)

        self.threads_value_label = ctk.CTkLabel(
            threads_frame,
            text=str(self.callbacks.get("get_download_threads", lambda: 4)()),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["accent"],
            width=30,
        )
        self.threads_value_label.pack(side=ctk.RIGHT)

        threads_slider = ctk.CTkSlider(
            container,
            from_=1,
            to=255,
            number_of_steps=254,
            command=self._on_threads_change,
            fg_color=COLORS["bg_light"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            progress_color=COLORS["accent"],
            width=380,
        )
        threads_slider.set(self.callbacks.get("get_download_threads", lambda: 4)())
        threads_slider.pack(fill=ctk.X, pady=(5, 0))
        self._threads_slider = threads_slider

        # 关闭按钮
        close_btn = ctk.CTkButton(
            container,
            text="关闭",
            width=120,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self.destroy,
        )
        close_btn.pack(pady=(30, 0))

    def _on_minimize_toggle(self):
        """启动后最小化开关切换"""
        enabled = self.minimize_var.get()
        if "set_minimize_on_game_launch" in self.callbacks:
            self.callbacks["set_minimize_on_game_launch"](enabled)
        # 同步主窗口变量
        self.parent.minimize_var.set(enabled)
        self.parent.set_status(
            f"游戏启动后最小化: {'已启用' if enabled else '已禁用'}",
            "success" if enabled else "info"
        )

    def _on_mirror_toggle(self):
        """镜像源开关切换"""
        enabled = self.mirror_var.get()
        if "set_mirror_enabled" in self.callbacks:
            self.callbacks["set_mirror_enabled"](enabled)
        # 同步主窗口变量
        self.parent.mirror_var.set(enabled)
        self.parent.set_status(
            f"国内镜像源: {'已启用' if enabled else '已禁用'}",
            "success" if enabled else "info"
        )

    def _on_threads_change(self, value):
        """下载线程数滑块变化"""
        threads = int(round(value))
        self.threads_value_label.configure(text=str(threads))
        if "set_download_threads" in self.callbacks:
            self.callbacks["set_download_threads"](threads)
        self.parent.set_status(f"下载线程数: {threads}", "info")

    def _on_jdz_login(self):
        """净读 AI 登录"""
        username = self.jdz_user_entry.get().strip()
        password = self.jdz_pass_entry.get().strip()
        if not username or not password:
            messagebox.showwarning("提示", "请输入用户名和密码", parent=self)
            return

        self.jdz_login_btn.configure(state="disabled", text="登录中...")
        self.update()

        def _do_login():
            import urllib.request
            import urllib.error
            import json
            try:
                req_data = json.dumps({"username": username, "password": password}).encode("utf-8")
                req = urllib.request.Request(
                    "https://jingdu.qzz.io/api/auth/login",
                    data=req_data,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "FMCL/1.0 (Minecraft Launcher; crash-analyzer)",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                token = result.get("token")
                if token:
                    if "set_jdz_token" in self.callbacks:
                        self.callbacks["set_jdz_token"](token)
                    self.after(0, lambda: self._jdz_login_success(token))
                else:
                    self.after(0, lambda: self._jdz_login_fail("未获取到 Token"))
            except urllib.error.HTTPError as e:
                _code = e.code
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                _err_msg = f"HTTP {_code}: {body[:100]}"
                self.after(0, lambda: self._jdz_login_fail(_err_msg))
            except Exception as e:
                _err_msg = str(e)
                self.after(0, lambda: self._jdz_login_fail(_err_msg))

        threading.Thread(target=_do_login, daemon=True).start()

    def _jdz_login_success(self, token: str):
        self.jdz_login_btn.configure(state="normal", text="登录")
        self.jdz_status_label.configure(text="状态: 已登录", text_color=COLORS["success"])
        self.parent.set_status("净读 AI 登录成功", "success")

    def _jdz_login_fail(self, msg: str):
        self.jdz_login_btn.configure(state="normal", text="登录")
        self.jdz_status_label.configure(text="状态: 登录失败", text_color=COLORS["error"])
        messagebox.showerror("登录失败", f"净读 AI 登录失败:\n{msg}", parent=self)

    def _on_jdz_logout(self):
        """退出净读 AI"""
        if "set_jdz_token" in self.callbacks:
            self.callbacks["set_jdz_token"](None)
        self.jdz_status_label.configure(text="状态: 未登录", text_color=COLORS["text_secondary"])
        self.jdz_user_entry.delete(0, "end")
        self.jdz_pass_entry.delete(0, "end")
        self.parent.set_status("净读 AI 已退出登录", "info")


class ModpackInstallWindow(ctk.CTkToplevel):
    """整合包（.mrpack）安装窗口 - 选择文件、确认信息、执行安装"""

    def __init__(self, parent, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self.callbacks = callbacks
        self._mrpack_path: Optional[str] = None
        self._mrpack_info: Optional[Dict[str, Any]] = None
        self._optional_var_map: Dict[str, ctk.BooleanVar] = {}

        # 窗口配置
        self.title("📦 安装整合包")
        self.geometry("580x780")
        self.minsize(520, 700)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        self.grab_set()

        # 居中
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 580, 780
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # 保存原始 on_progress 用于安装时替换
        self._launcher_instance: Optional[Any] = None
        self._orig_on_progress: Optional[Callable] = None
        self.on_progress_original: Optional[Callable] = None

        self._build_ui()

    def _build_ui(self):
        """构建界面"""
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        # ── 标题 ──
        ctk.CTkLabel(
            main_frame,
            text="📦 安装 Modrinth 整合包",
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(pady=(0, 5))

        ctk.CTkLabel(
            main_frame,
            text="选择本地 .mrpack 文件，自动下载模组、配置和依赖",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(pady=(0, 15))

        # ── 文件选择区域 ──
        file_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        file_frame.pack(fill=ctk.X, pady=(0, 12))

        self._file_label = ctk.CTkLabel(
            file_frame,
            text="未选择文件",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
            wraplength=440,
        )
        self._file_label.pack(padx=15, pady=(12, 5), fill=ctk.X)

        btn_row = ctk.CTkFrame(file_frame, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=15, pady=(0, 12))

        ctk.CTkButton(
            btn_row,
            text="📂 选择 .mrpack 文件",
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._select_file,
        ).pack(side=ctk.LEFT)

        # ── 整合包信息区域（初始隐藏）──
        self._info_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)

        self._info_name_label = ctk.CTkLabel(
            self._info_frame, text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"], anchor=ctk.W,
        )
        self._info_name_label.pack(padx=15, pady=(12, 2), fill=ctk.X)

        self._info_summary_label = ctk.CTkLabel(
            self._info_frame, text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"], anchor=ctk.W, wraplength=480,
        )
        self._info_summary_label.pack(padx=15, pady=(0, 2), fill=ctk.X)

        self._info_version_label = ctk.CTkLabel(
            self._info_frame, text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["success"], anchor=ctk.W,
        )
        self._info_version_label.pack(padx=15, pady=(0, 5), fill=ctk.X)

        # 可选文件
        self._optional_frame = ctk.CTkFrame(self._info_frame, fg_color="transparent")
        self._optional_frame.pack(padx=15, pady=(0, 5), fill=ctk.X)

        ctk.CTkFrame(self._info_frame, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(5, 0)
        )

        # 安装目录设置
        dir_row = ctk.CTkFrame(self._info_frame, fg_color="transparent")
        dir_row.pack(fill=ctk.X, padx=15, pady=(8, 12))

        ctk.CTkLabel(
            dir_row, text="安装目录:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT)

        self._dir_label = ctk.CTkLabel(
            dir_row, text="默认 (.minecraft)",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
        )
        self._dir_label.pack(side=ctk.LEFT, padx=(8, 8))

        ctk.CTkButton(
            dir_row, text="浏览", width=50, height=26,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"], hover_color=COLORS["card_border"],
            command=self._select_dir,
        ).pack(side=ctk.LEFT)

        self._custom_modpack_dir: Optional[str] = None

        # ── 进度区域（初始隐藏）──
        self._progress_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)

        self._progress_status = ctk.CTkLabel(
            self._progress_frame, text="正在安装...",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        )
        self._progress_status.pack(padx=15, pady=(12, 5), fill=ctk.X)

        self._progress_bar = ctk.CTkProgressBar(
            self._progress_frame, height=10,
            fg_color=COLORS["bg_medium"], progress_color=COLORS["accent"],
        )
        self._progress_bar.pack(fill=ctk.X, padx=15, pady=(0, 12))
        self._progress_bar.set(0)

        # ── 安装日志区域（初始隐藏）──
        self._log_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)

        log_header = ctk.CTkFrame(self._log_frame, fg_color="transparent")
        log_header.pack(fill=ctk.X, padx=15, pady=(10, 5))

        ctk.CTkLabel(
            log_header, text="📋 安装日志",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_secondary"], anchor=ctk.W,
        ).pack(side=ctk.LEFT)

        self._log_text = ctk.CTkTextbox(
            self._log_frame, height=200,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COLORS["bg_dark"], border_color=COLORS["card_border"],
            text_color=COLORS["text_secondary"],
            wrap=ctk.WORD, state=ctk.DISABLED,
        )
        self._log_text.pack(fill=ctk.BOTH, expand=True, padx=15, pady=(0, 12))

        # ── 底部按钮 ──
        self._bottom_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        self._bottom_frame.pack(fill=ctk.X, pady=(12, 0))

        self._install_btn = ctk.CTkButton(
            self._bottom_frame, text="📦 开始安装",
            height=40, font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            state=ctk.DISABLED,
            command=self._on_install,
        )
        self._install_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        self._close_btn = ctk.CTkButton(
            self._bottom_frame, text="关闭",
            height=40, width=80,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"], hover_color=COLORS["card_border"],
            command=self.destroy,
        )
        self._close_btn.pack(side=ctk.RIGHT, padx=(10, 0))

    # ─── 文件选择 ─────────────────────────────────────────────

    def _select_file(self):
        """选择 .mrpack 文件"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self,
            title="选择 Modrinth 整合包",
            filetypes=[("Modrinth 整合包", "*.mrpack"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self._mrpack_path = path
        self._file_label.configure(
            text=os.path.basename(path),
            text_color=COLORS["text_primary"],
        )
        # 后台加载整合包信息
        self._install_btn.configure(state=ctk.DISABLED, text="读取中...")
        self._run_in_thread(self._load_mrpack_info)

    def _select_dir(self):
        """选择安装目录"""
        from tkinter import filedialog
        path = filedialog.askdirectory(parent=self, title="选择整合包安装目录")
        if not path:
            return
        self._custom_modpack_dir = path
        self._dir_label.configure(text=path)

    def _load_mrpack_info(self):
        """读取整合包信息（后台线程）"""
        try:
            info = self.callbacks["get_mrpack_information"](self._mrpack_path)
            self._mrpack_info = info
            self.after(0, lambda: self._show_mrpack_info(info))
        except Exception as e:
            self.after(0, lambda: self._show_error(str(e)))

    def _show_mrpack_info(self, info: Dict[str, Any]):
        """在 UI 中显示整合包信息"""
        self._info_name_label.configure(text=info.get("name", "未知整合包"))
        summary = info.get("summary", "")
        if summary:
            self._info_summary_label.configure(text=summary)
        else:
            self._info_summary_label.pack_forget()

        mc_version = info.get("minecraftVersion", "未知")
        self._info_version_label.configure(text=f"Minecraft {mc_version}")

        # 可选文件
        optional_files = info.get("optionalFiles", [])
        if optional_files:
            ctk.CTkLabel(
                self._optional_frame, text="可选组件:",
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"], anchor=ctk.W,
            ).pack(fill=ctk.X, pady=(0, 3))

            self._optional_var_map.clear()
            for opt_name in optional_files:
                var = ctk.BooleanVar(value=False)
                self._optional_var_map[opt_name] = var
                ctk.CTkCheckBox(
                    self._optional_frame, text=opt_name, variable=var,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                    fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                    text_color=COLORS["text_primary"],
                ).pack(anchor=ctk.W, pady=1)

        self._info_frame.pack(fill=ctk.X, pady=(0, 5))
        self._install_btn.configure(state=ctk.NORMAL, text="📦 开始安装")

    def _show_error(self, msg: str):
        """显示错误"""
        self._file_label.configure(text="文件无效", text_color=COLORS["error"])
        self._install_btn.configure(state=ctk.DISABLED, text="📦 开始安装")
        messagebox.showerror("错误", f"无法读取整合包:\n{msg}", parent=self)

    # ─── 安装 ─────────────────────────────────────────────────

    def _on_install(self):
        """开始安装"""
        if not self._mrpack_path or not self._mrpack_info:
            return

        # 收集可选文件
        optional_files = [name for name, var in self._optional_var_map.items() if var.get()]

        # 切换到进度视图
        self._info_frame.pack_forget()
        self._progress_frame.pack(fill=ctk.X, pady=(0, 5))
        self._log_frame.pack(fill=ctk.BOTH, expand=True, pady=(5, 0))
        self._install_btn.configure(state=ctk.DISABLED, text="安装中...")
        self._close_btn.pack_forget()

        # 清空日志
        self._log_text.configure(state=ctk.NORMAL)
        self._log_text.delete("0.0", ctk.END)
        self._log_text.configure(state=ctk.DISABLED)

        self._run_in_thread(self._do_install, optional_files)

    def _do_install(self, optional_files: list):
        """执行安装（后台线程）"""
        def _log_append(text: str):
            """线程安全地追加日志行"""
            def _ui_append():
                self._log_text.configure(state=ctk.NORMAL)
                self._log_text.insert(ctk.END, text + "\n")
                self._log_text.see(ctk.END)
                self._log_text.configure(state=ctk.DISABLED)
            self.after(0, _ui_append)

        def _hook_progress(current, total, status):
            """临时进度回调：同时更新进度条和日志"""
            if status:
                _log_append(status)
            if self.on_progress_original and total > 0 and current > 0:
                self.on_progress_original(current, total, "")

        # 获取 launcher 实例并替换 on_progress
        launcher = getattr(self.callbacks.get("install_mrpack"), "__self__", None)
        if launcher:
            self._launcher_instance = launcher
            self._orig_on_progress = getattr(launcher, "on_progress", None)
            launcher.on_progress = _hook_progress

        try:
            success, result = self.callbacks["install_mrpack"](
                self._mrpack_path,
                optional_files=optional_files,
                modpack_directory=self._custom_modpack_dir,
            )
            self.after(0, lambda: self._on_install_done(success, result))
        except Exception as e:
            self.after(0, lambda: self._on_install_done(False, str(e)))
        finally:
            # 恢复原始 on_progress
            if launcher and self._orig_on_progress is not None:
                launcher.on_progress = self._orig_on_progress
            elif launcher:
                launcher.on_progress = None

    def _on_install_done(self, success: bool, result: str):
        """安装完成"""
        self._close_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        if success:
            self._progress_status.configure(text="安装完成!", text_color=COLORS["success"])
            self._install_btn.configure(
                text=f"✅ 安装完成 - {result}",
                fg_color=COLORS["success"],
                state=ctk.DISABLED,
            )

            def _append_done_log():
                self._log_text.configure(state=ctk.NORMAL)
                self._log_text.insert(ctk.END, f"\n✅ 安装完成! 启动版本: {result}\n")
                self._log_text.see(ctk.END)
                self._log_text.configure(state=ctk.DISABLED)
            self.after(0, _append_done_log)

            messagebox.showinfo(
                "安装完成",
                f"整合包安装成功！\n\n启动版本: {result}\n\n"
                f"请刷新版本列表后选择该版本启动游戏。",
                parent=self,
            )
            self.destroy()
        else:
            self._progress_status.configure(text="安装失败", text_color=COLORS["error"])
            self._install_btn.configure(text="📦 重新安装", state=ctk.NORMAL)

            def _append_err_log():
                self._log_text.configure(state=ctk.NORMAL)
                self._log_text.insert(ctk.END, f"\n❌ 安装失败: {result}\n")
                self._log_text.see(ctk.END)
                self._log_text.configure(state=ctk.DISABLED)
            self.after(0, _append_err_log)

            messagebox.showerror("安装失败", f"整合包安装失败:\n{result}", parent=self)

    # ─── 工具 ─────────────────────────────────────────────────

    def _run_in_thread(self, func, *args):
        """在后台线程运行函数"""
        t = threading.Thread(target=func, args=args, daemon=True)
        t.start()


class ModpackServerWindow(ctk.CTkToplevel):
    """整合包开服窗口 - 选择 .mrpack 文件，安装为服务器"""

    def __init__(self, parent, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self.callbacks = callbacks
        self.parent_app = parent
        self._mrpack_path: Optional[str] = None
        self._mrpack_info: Optional[Dict[str, Any]] = None
        self._optional_var_map: Dict[str, ctk.BooleanVar] = {}

        self.title("📦 整合包开服")
        self.geometry("580x820")
        self.minsize(520, 740)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        self.grab_set()

        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 580, 820
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # 保存原始 on_progress 用于安装时替换
        self._launcher_instance: Optional[Any] = None
        self._orig_on_progress: Optional[Callable] = None
        self.on_progress_original: Optional[Callable] = None

        self._build_ui()

    def _build_ui(self):
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        # 标题
        ctk.CTkLabel(
            main_frame,
            text="📦 整合包开服",
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(pady=(0, 5))

        ctk.CTkLabel(
            main_frame,
            text="安装 .mrpack 整合包作为服务器，自动下载模组、配置和服务器核心",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(pady=(0, 15))

        # 文件选择区域
        file_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        file_frame.pack(fill=ctk.X, pady=(0, 12))

        self._file_label = ctk.CTkLabel(
            file_frame, text="未选择文件",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W, wraplength=440,
        )
        self._file_label.pack(padx=15, pady=(12, 5), fill=ctk.X)

        btn_row = ctk.CTkFrame(file_frame, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=15, pady=(0, 12))

        ctk.CTkButton(
            btn_row, text="📂 选择 .mrpack 文件",
            height=34, font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._select_file,
        ).pack(side=ctk.LEFT)

        # 整合包信息区域（初始隐藏）
        self._info_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)

        self._info_name_label = ctk.CTkLabel(
            self._info_frame, text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"], anchor=ctk.W,
        )
        self._info_name_label.pack(padx=15, pady=(12, 2), fill=ctk.X)

        self._info_summary_label = ctk.CTkLabel(
            self._info_frame, text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"], anchor=ctk.W, wraplength=480,
        )
        self._info_summary_label.pack(padx=15, pady=(0, 2), fill=ctk.X)

        self._info_version_label = ctk.CTkLabel(
            self._info_frame, text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["success"], anchor=ctk.W,
        )
        self._info_version_label.pack(padx=15, pady=(0, 5), fill=ctk.X)

        # 可选文件
        self._optional_frame = ctk.CTkFrame(self._info_frame, fg_color="transparent")
        self._optional_frame.pack(padx=15, pady=(0, 5), fill=ctk.X)

        ctk.CTkFrame(self._info_frame, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(5, 0)
        )

        # 服务器名称设置
        name_row = ctk.CTkFrame(self._info_frame, fg_color="transparent")
        name_row.pack(fill=ctk.X, padx=15, pady=(8, 5))

        ctk.CTkLabel(
            name_row, text="服务器名称:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT)

        self._server_name_entry = ctk.CTkEntry(
            name_row, height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"], border_color=COLORS["card_border"],
            placeholder_text="留空则自动命名",
        )
        self._server_name_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(8, 0))

        # 提示
        ctk.CTkLabel(
            self._info_frame,
            text="✅ 自动检测并安装服务端 mod loader（Forge/Fabric/NeoForge/Quilt）",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"], anchor=ctk.W, wraplength=480,
        ).pack(padx=15, pady=(0, 12), fill=ctk.X)

        # 进度区域（初始隐藏）
        self._progress_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)

        self._progress_status = ctk.CTkLabel(
            self._progress_frame, text="正在安装...",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        )
        self._progress_status.pack(padx=15, pady=(12, 5), fill=ctk.X)

        self._progress_bar = ctk.CTkProgressBar(
            self._progress_frame, height=10,
            fg_color=COLORS["bg_medium"], progress_color=COLORS["accent"],
        )
        self._progress_bar.pack(fill=ctk.X, padx=15, pady=(0, 12))
        self._progress_bar.set(0)

        # 安装日志区域（初始隐藏）
        self._log_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)

        log_header = ctk.CTkFrame(self._log_frame, fg_color="transparent")
        log_header.pack(fill=ctk.X, padx=15, pady=(10, 5))

        ctk.CTkLabel(
            log_header, text="📋 安装日志",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_secondary"], anchor=ctk.W,
        ).pack(side=ctk.LEFT)

        self._log_text = ctk.CTkTextbox(
            self._log_frame, height=200,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COLORS["bg_dark"], border_color=COLORS["card_border"],
            text_color=COLORS["text_secondary"],
            wrap=ctk.WORD, state=ctk.DISABLED,
        )
        self._log_text.pack(fill=ctk.BOTH, expand=True, padx=15, pady=(0, 12))

        # 底部按钮
        self._bottom_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        self._bottom_frame.pack(fill=ctk.X, pady=(12, 0))

        self._install_btn = ctk.CTkButton(
            self._bottom_frame, text="🚀 开始安装服务器",
            height=40, font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            state=ctk.DISABLED,
            command=self._on_install,
        )
        self._install_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        self._close_btn = ctk.CTkButton(
            self._bottom_frame, text="关闭",
            height=40, width=80,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"], hover_color=COLORS["card_border"],
            command=self.destroy,
        )
        self._close_btn.pack(side=ctk.RIGHT, padx=(10, 0))

    def _select_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self,
            title="选择 Modrinth 整合包",
            filetypes=[("Modrinth 整合包", "*.mrpack"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self._mrpack_path = path
        self._file_label.configure(text=os.path.basename(path), text_color=COLORS["text_primary"])
        self._install_btn.configure(state=ctk.DISABLED, text="读取中...")
        self._run_in_thread(self._load_mrpack_info)

    def _load_mrpack_info(self):
        try:
            info = self.callbacks["get_mrpack_information"](self._mrpack_path)
            self._mrpack_info = info
            self.after(0, lambda: self._show_mrpack_info(info))
        except Exception as e:
            self.after(0, lambda: self._show_error(str(e)))

    def _show_mrpack_info(self, info: Dict[str, Any]):
        self._info_name_label.configure(text=info.get("name", "未知整合包"))
        summary = info.get("summary", "")
        if summary:
            self._info_summary_label.configure(text=summary)
        else:
            self._info_summary_label.pack_forget()

        mc_version = info.get("minecraftVersion", "未知")
        self._info_version_label.configure(text=f"Minecraft {mc_version}")

        # 可选文件
        optional_files = info.get("optionalFiles", [])
        if optional_files:
            ctk.CTkLabel(
                self._optional_frame, text="可选组件:",
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"], anchor=ctk.W,
            ).pack(fill=ctk.X, pady=(0, 3))

            self._optional_var_map.clear()
            for opt_name in optional_files:
                var = ctk.BooleanVar(value=False)
                self._optional_var_map[opt_name] = var
                ctk.CTkCheckBox(
                    self._optional_frame, text=opt_name, variable=var,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                    fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                    text_color=COLORS["text_primary"],
                ).pack(anchor=ctk.W, pady=1)

        self._info_frame.pack(fill=ctk.X, pady=(0, 5))
        self._install_btn.configure(state=ctk.NORMAL, text="🚀 开始安装服务器")

    def _show_error(self, msg: str):
        self._file_label.configure(text="文件无效", text_color=COLORS["error"])
        self._install_btn.configure(state=ctk.DISABLED, text="🚀 开始安装服务器")
        messagebox.showerror("错误", f"无法读取整合包:\n{msg}", parent=self)

    def _on_install(self):
        if not self._mrpack_path or not self._mrpack_info:
            return

        optional_files = [name for name, var in self._optional_var_map.items() if var.get()]
        custom_name = self._server_name_entry.get().strip() or None

        self._info_frame.pack_forget()
        self._progress_frame.pack(fill=ctk.X, pady=(0, 5))
        self._log_frame.pack(fill=ctk.BOTH, expand=True, pady=(5, 0))
        self._install_btn.configure(state=ctk.DISABLED, text="安装中...")
        self._close_btn.pack_forget()

        # 清空日志
        self._log_text.configure(state=ctk.NORMAL)
        self._log_text.delete("0.0", ctk.END)
        self._log_text.configure(state=ctk.DISABLED)

        self._run_in_thread(self._do_install, optional_files, custom_name)

    def _do_install(self, optional_files: list, server_name: Optional[str]):
        def _log_append(text: str):
            def _ui_append():
                self._log_text.configure(state=ctk.NORMAL)
                self._log_text.insert(ctk.END, text + "\n")
                self._log_text.see(ctk.END)
                self._log_text.configure(state=ctk.DISABLED)
            self.after(0, _ui_append)

        def _hook_progress(current, total, status):
            if status:
                _log_append(status)
            if self.on_progress_original and total > 0 and current > 0:
                self.on_progress_original(current, total, "")

        # 获取 launcher 实例并替换 on_progress
        launcher = getattr(self.callbacks.get("install_mrpack_server"), "__self__", None)
        if launcher:
            self._launcher_instance = launcher
            self._orig_on_progress = getattr(launcher, "on_progress", None)
            launcher.on_progress = _hook_progress

        try:
            success, result = self.callbacks["install_mrpack_server"](
                self._mrpack_path,
                optional_files=optional_files,
                server_name=server_name,
            )
            self.after(0, lambda: self._on_install_done(success, result))
        except Exception as e:
            self.after(0, lambda: self._on_install_done(False, str(e)))
        finally:
            if launcher and self._orig_on_progress is not None:
                launcher.on_progress = self._orig_on_progress
            elif launcher:
                launcher.on_progress = None

    def _on_install_done(self, success: bool, result: str):
        self._close_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        if success:
            self._progress_status.configure(text="安装完成!", text_color=COLORS["success"])
            self._install_btn.configure(
                text=f"✅ 安装完成 - {result}",
                fg_color=COLORS["success"],
                state=ctk.DISABLED,
            )

            def _append_done_log():
                self._log_text.configure(state=ctk.NORMAL)
                self._log_text.insert(ctk.END, f"\n✅ 服务器安装完成! 名称: {result}\n")
                self._log_text.see(ctk.END)
                self._log_text.configure(state=ctk.DISABLED)
            self.after(0, _append_done_log)

            messagebox.showinfo(
                "安装完成",
                f"整合包服务器安装成功！\n\n"
                f"服务器名称: {result}\n"
                f"目录: .minecraft/server/{result}/\n\n"
                f"请刷新服务器列表后选择该版本启动。",
                parent=self,
            )
            # 刷新父窗口的服务器列表
            self._refresh_parent_server_list()
            self.destroy()
        else:
            self._progress_status.configure(text="安装失败", text_color=COLORS["error"])
            self._install_btn.configure(text="🚀 重新安装", state=ctk.NORMAL)

            def _append_err_log():
                self._log_text.configure(state=ctk.NORMAL)
                self._log_text.insert(ctk.END, f"\n❌ 安装失败: {result}\n")
                self._log_text.see(ctk.END)
                self._log_text.configure(state=ctk.DISABLED)
            self.after(0, _append_err_log)

            messagebox.showerror("安装失败", f"整合包服务器安装失败:\n{result}", parent=self)

    def _refresh_parent_server_list(self):
        """刷新父窗口的服务器列表"""
        try:
            if "get_installed_servers" in self.callbacks:
                installed = self.callbacks["get_installed_servers"]()
                if hasattr(self.parent_app, "_render_server_versions"):
                    self.parent_app._render_server_versions(installed)
        except Exception:
            pass

    def _run_in_thread(self, func, *args):
        t = threading.Thread(target=func, args=args, daemon=True)
        t.start()


class ModBrowserWindow(ctk.CTkToplevel):
    """Modrinth 模组浏览窗口 - 搜索、浏览并安装模组"""

    PAGE_SIZE = 10

    def __init__(self, parent, version_id: str, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self.version_id = version_id
        self.callbacks = callbacks

        # 解析版本信息
        from modrinth import parse_mod_loader_from_version, parse_game_version_from_version
        self._mod_loader = parse_mod_loader_from_version(version_id)
        self._game_version = parse_game_version_from_version(version_id)

        # 窗口配置
        self.title(f"安装模组 - {version_id}")
        self.geometry("800x640")
        self.minsize(720, 560)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)

        # 居中
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 800, 640
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # 搜索状态
        self._current_offset = 0
        self._total_hits = 0
        self._current_query = ""

        self._build_ui()

        # 窗口打开时自动加载热门模组（后台线程，避免阻塞 UI）
        self.after(300, lambda: self._run_in_thread(self._do_search))

    def _build_ui(self):
        """构建界面"""
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        # ── 顶部标题与筛选区 ──
        header = ctk.CTkFrame(main_frame, fg_color="transparent")
        header.pack(fill=ctk.X, pady=(0, 10))

        ctk.CTkLabel(
            header,
            text=f"🧩 安装模组 - {self.version_id}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        # 版本/加载器信息标签
        info_parts = []
        if self._game_version:
            info_parts.append(f"MC {self._game_version}")
        if self._mod_loader:
            info_parts.append(self._mod_loader.capitalize())
        info_text = " | ".join(info_parts) if info_parts else "未识别版本信息"
        info_color = COLORS["success"] if info_parts else COLORS["warning"]
        ctk.CTkLabel(
            header,
            text=info_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=info_color,
        ).pack(side=ctk.RIGHT)

        # ── 搜索栏 ──
        search_frame = ctk.CTkFrame(main_frame, fg_color="transparent", height=40)
        search_frame.pack(fill=ctk.X, pady=(0, 8))
        search_frame.pack_propagate(False)

        self._search_entry = ctk.CTkEntry(
            search_frame,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="🔍 搜索模组...",
        )
        self._search_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._search_entry.bind("<Return>", lambda e: self._on_search())

        ctk.CTkButton(
            search_frame,
            text="搜索",
            width=80,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_search,
        ).pack(side=ctk.LEFT)

        # ── 模组列表 ──
        list_container = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        list_container.pack(fill=ctk.BOTH, expand=True, pady=(0, 8))

        self._mod_list_frame = ctk.CTkScrollableFrame(
            list_container,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        self._mod_list_frame.pack(fill=ctk.BOTH, expand=True, padx=5, pady=5)

        # 加载中占位
        self._loading_label = ctk.CTkLabel(
            self._mod_list_frame,
            text="⏳ 加载中...",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            justify=ctk.CENTER,
        )
        self._loading_label.pack(pady=40)

        # ── 分页控件 ──
        page_frame = ctk.CTkFrame(main_frame, fg_color="transparent", height=34)
        page_frame.pack(fill=ctk.X)
        page_frame.pack_propagate(False)

        self._prev_btn = ctk.CTkButton(
            page_frame,
            text="◀ 上一页",
            width=90,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_prev_page,
        )
        self._prev_btn.pack(side=ctk.LEFT)

        self._page_label = ctk.CTkLabel(
            page_frame,
            text="0 / 0",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            width=100,
        )
        self._page_label.pack(side=ctk.LEFT, padx=10)

        self._next_btn = ctk.CTkButton(
            page_frame,
            text="下一页 ▶",
            width=90,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_next_page,
        )
        self._next_btn.pack(side=ctk.LEFT)

        # 结果计数
        self._result_count_label = ctk.CTkLabel(
            page_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._result_count_label.pack(side=ctk.RIGHT)

        # ── 底部状态 ──
        self._status_label = ctk.CTkLabel(
            main_frame,
            text="就绪",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._status_label.pack(anchor=ctk.W, pady=(5, 0))

    def _on_search(self):
        """搜索按钮回调"""
        self._current_query = self._search_entry.get().strip()
        self._current_offset = 0
        self._set_status("正在搜索...")
        self._run_in_thread(self._do_search)

    def _do_search(self):
        """执行搜索（后台线程）"""
        from modrinth import search_mods

        try:
            result = search_mods(
                query=self._current_query,
                game_version=self._game_version,
                mod_loader=self._mod_loader,
                offset=self._current_offset,
                limit=self.PAGE_SIZE,
            )

            hits = result.get("hits", [])
            self._total_hits = result.get("total_hits", 0)

            self.after(0, self._render_results, hits)

        except Exception as e:
            logger.error(f"搜索模组失败: {e}")
            self.after(0, self._render_error, str(e))

    def _render_results(self, hits: List[Dict]):
        """渲染搜索结果（主线程）"""
        # 清空列表
        for w in self._mod_list_frame.winfo_children():
            w.destroy()

        if not hits:
            ctk.CTkLabel(
                self._mod_list_frame,
                text="未找到模组\n请尝试其他关键词",
                font=ctk.CTkFont(family=FONT_FAMILY, size=14),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=40)
            self._update_pagination()
            self._set_status("未找到结果")
            return

        for mod in hits:
            self._create_mod_item(mod)

        self._update_pagination()

        # 更新结果计数
        start = self._current_offset + 1
        end = min(self._current_offset + self.PAGE_SIZE, self._total_hits)
        self._result_count_label.configure(text=f"显示 {start}-{end} / 共 {self._total_hits} 个")
        self._set_status(f"共找到 {self._total_hits} 个模组")

    def _render_error(self, error_msg: str):
        """渲染错误状态"""
        for w in self._mod_list_frame.winfo_children():
            w.destroy()

        ctk.CTkLabel(
            self._mod_list_frame,
            text=f"搜索失败\n{error_msg}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["error"],
            justify=ctk.CENTER,
        ).pack(pady=40)
        self._set_status(f"搜索失败: {error_msg}")

    def _create_mod_item(self, mod: Dict):
        """创建单个模组条目"""
        row = ctk.CTkFrame(
            self._mod_list_frame,
            fg_color=COLORS["bg_medium"],
            corner_radius=8,
        )
        row.pack(fill=ctk.X, pady=3, padx=2)

        # 上行：模组名 + 下载按钮
        top_row = ctk.CTkFrame(row, fg_color="transparent", height=36)
        top_row.pack(fill=ctk.X, padx=10, pady=(8, 2))
        top_row.pack_propagate(False)

        # 模组名
        title = mod.get("title", "未知模组")
        ctk.CTkLabel(
            top_row,
            text=f"🧩 {title}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        ).pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        # 下载计数
        downloads = mod.get("downloads", 0)
        dl_text = self._format_downloads(downloads)
        ctk.CTkLabel(
            top_row,
            text=f"📥 {dl_text}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT, padx=(5, 0))

        # 安装按钮
        project_id = mod.get("project_id", "")
        ctk.CTkButton(
            top_row,
            text="📥 安装",
            width=70,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            text_color=COLORS["text_primary"],
            command=lambda pid=project_id, t=title: self._on_install_mod(pid, t),
        ).pack(side=ctk.RIGHT, padx=(8, 0))

        # 下行：描述
        description = mod.get("description", "")
        if description:
            desc_label = ctk.CTkLabel(
                row,
                text=description,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                wraplength=700,
                justify=ctk.LEFT,
                anchor=ctk.W,
            )
            desc_label.pack(fill=ctk.X, padx=10, pady=(0, 4))

        # 底部标签：支持的加载器与版本
        categories = mod.get("categories", [])
        versions_display = mod.get("versions", [])
        tag_parts = []
        if categories:
            loader_tags = [c for c in categories if c in ("forge", "fabric", "neoforge", "quilt")]
            if loader_tags:
                tag_parts.append(" | ".join(l.capitalize() for l in loader_tags))
        if versions_display:
            from modrinth import compress_game_versions
            compressed = compress_game_versions(versions_display)
            if compressed:
                tag_parts.append(compressed)

        if tag_parts:
            tags_text = "  ·  ".join(tag_parts)
            ctk.CTkLabel(
                row,
                text=tags_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X, padx=10, pady=(0, 8))

    @staticmethod
    def _format_downloads(count: int) -> str:
        """格式化下载数"""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _update_pagination(self):
        """更新分页控件状态"""
        total_pages = max(1, (self._total_hits + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        current_page = (self._current_offset // self.PAGE_SIZE) + 1

        self._page_label.configure(text=f"{current_page} / {total_pages}")
        self._prev_btn.configure(state=ctk.NORMAL if self._current_offset > 0 else ctk.DISABLED)
        self._next_btn.configure(
            state=ctk.NORMAL if self._current_offset + self.PAGE_SIZE < self._total_hits else ctk.DISABLED
        )

    def _on_prev_page(self):
        """上一页"""
        if self._current_offset > 0:
            self._current_offset -= self.PAGE_SIZE
            self._set_status("正在加载...")
            self._run_in_thread(self._do_search)

    def _on_next_page(self):
        """下一页"""
        if self._current_offset + self.PAGE_SIZE < self._total_hits:
            self._current_offset += self.PAGE_SIZE
            self._set_status("正在加载...")
            self._run_in_thread(self._do_search)

    def _on_install_mod(self, project_id: str, title: str):
        """安装模组按钮回调"""
        self._set_status(f"正在获取 {title} 版本信息...")
        self._run_in_thread(self._install_mod, project_id, title)

    def _install_mod(self, project_id: str, title: str):
        """安装模组（后台线程，含依赖自动安装）"""
        from modrinth import install_mod_with_deps

        try:
            if not self._game_version or not self._mod_loader:
                self.after(0, self._set_status, "无法确定游戏版本或加载器类型")
                return

            mods_dir = self._get_mods_dir()

            success, result, installed_names = install_mod_with_deps(
                project_id,
                game_version=self._game_version,
                mod_loader=self._mod_loader,
                mods_dir=mods_dir,
                status_callback=lambda msg: self.after(0, self._set_status, msg),
            )

            if success:
                if len(installed_names) > 1:
                    deps = ", ".join(installed_names[:-1])
                    self.after(
                        0,
                        self._set_status,
                        f"✅ {title} 及依赖安装成功! (依赖: {deps})",
                    )
                else:
                    self.after(0, self._set_status, f"✅ {title} 安装成功!")
                logger.info(f"模组安装成功: {installed_names} -> {result}")
            else:
                self.after(0, self._set_status, f"❌ {result}")
                logger.error(f"模组安装失败: {result}")

        except Exception as e:
            error_msg = str(e)
            self.after(0, self._set_status, f"安装失败: {error_msg}")
            logger.error(f"安装模组失败: {e}")

    def _get_mods_dir(self) -> str:
        """获取当前版本的 mods 目录"""
        mc_dir = Path(".")
        if "get_minecraft_dir" in self.callbacks:
            mc_dir = Path(self.callbacks["get_minecraft_dir"]())

        # 优先版本隔离
        version_base = mc_dir / "versions" / self.version_id
        if version_base.exists():
            return str(version_base / "mods")

        return str(mc_dir / "mods")

    def _set_status(self, text: str):
        """更新状态栏"""
        try:
            if self.winfo_exists():
                self._status_label.configure(text=text)
        except Exception:
            pass

    def _run_in_thread(self, target, *args, **kwargs):
        """后台线程执行"""
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
