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
        """构建内容区域"""
        content = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        # 最左侧 - 侧边栏（角色名、皮肤、日志）
        self._build_sidebar(content)

        # 中间 - 已安装版本
        self._build_installed_panel(content)

        # 右侧 - 操作面板
        self._build_action_panel(content)

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

        # 安装按钮
        self.install_btn = ctk.CTkButton(
            panel,
            text="📥 安装版本",
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_install,
        )
        self.install_btn.pack(fill=ctk.X, padx=15, pady=(0, 15))

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
                # 无论是否最小化都启动日志监控，检测到窗口后关闭管道避免缓冲区满导致游戏卡顿
                self._run_in_thread(self._watch_game_stdout)
                self._run_in_thread(self._watch_game_exit)
            else:
                self.set_status(f"{version_id} 启动失败", "error")
            self.launch_btn.configure(state=ctk.NORMAL)

        elif task_type == "launch_error":
            self.set_status(f"启动错误: {data}", "error")
            self.launch_btn.configure(state=ctk.NORMAL)

        elif task_type == "game_window_detected":
            if self.minimize_var.get():
                self.set_status("游戏窗口已出现，启动器已最小化", "success")
                self.iconify()
            else:
                self.set_status("游戏已就绪", "success")

        elif task_type == "game_exited":
            self.kill_btn.configure(state=ctk.DISABLED)
            self.set_status("游戏已正常退出", "info")

        elif task_type == "game_crashed":
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

                pil_img = PILImage.open(icon_path).resize((80, 80), PILImage.LANCZOS)
                ctk_img = ctk.CTkImage(pil_img, size=(80, 80))
                ctk.CTkLabel(top_frame, image=ctk_img, text="").pack(
                    side=ctk.LEFT, padx=(0, 15)
                )
                about._icon_ref = ctk_img
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
                dialog._icon_ref = tk_img
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

        # 关闭按钮
        close_btn = tk.Button(dialog, text='关闭', command=dialog.destroy,
                              font=(FONT_FAMILY, 9), relief='flat', cursor='hand2',
                              bg='#1a1a2e', fg='#667788', activebackground='#1a1a2e',
                              activeforeground='#aabbcc', bd=0)
        close_btn.place(x=w // 2 - 20, y=h - 36, width=40)

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
        self.geometry("450x420")
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
