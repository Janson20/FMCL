"""UI 常量定义 - 颜色主题、字体检测、全局配置"""
import logging
import os
import platform
import subprocess

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
