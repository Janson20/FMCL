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
    Linux 优先选择支持 emoji 的字体组合。

    - Windows: 使用 Microsoft YaHei
    - macOS: 使用 PingFang SC
    - Linux: 通过 fc-list 检测，若无中文字体则尝试自动安装
    """
    system = platform.system().lower()

    if system == "windows":
        return "Microsoft YaHei"

    if system == "darwin":
        return "PingFang SC"

    # ── Linux: 使用 fc-list 检测中文字体和 emoji 字体 ──
    emoji_fonts = set()
    chinese_fonts = set()

    try:
        # 检测 emoji 字体
        emoji_result = subprocess.run(
            ["fc-list", ":lang=emoji", "family"],
            capture_output=True, text=True, timeout=5,
        )
        if emoji_result.returncode == 0 and emoji_result.stdout.strip():
            for line in emoji_result.stdout.strip().split("\n"):
                for f in line.split(","):
                    f = f.strip()
                    if f:
                        emoji_fonts.add(f)

        # 检测中文字体
        chinese_result = subprocess.run(
            ["fc-list", ":lang=zh", "family"],
            capture_output=True, text=True, timeout=5,
        )
        if chinese_result.returncode == 0 and chinese_result.stdout.strip():
            for line in chinese_result.stdout.strip().split("\n"):
                for f in line.split(","):
                    f = f.strip()
                    if f:
                        chinese_fonts.add(f)
    except Exception:
        pass

    # 优先 emoji 字体
    emoji_priority = ["Noto Color Emoji", "Symbola", "DejaVu Sans", "EmojiOne"]
    selected_emoji = None
    for pref in emoji_priority:
        if pref in emoji_fonts:
            selected_emoji = pref
            break
    if not selected_emoji and emoji_fonts:
        selected_emoji = next(iter(emoji_fonts))

    # 中文字体
    chinese_priority = [
        "Noto Sans CJK SC", "Noto Sans SC",
        "WenQuanYi Micro Hei", "WenQuanYi Zen Hei",
        "Droid Sans Fallback",
    ]
    selected_chinese = None
    for pref in chinese_priority:
        if pref in chinese_fonts:
            selected_chinese = pref
            break
    if not selected_chinese and chinese_fonts:
        selected_chinese = next(iter(chinese_fonts))

    # 如果有中文字体但没有 emoji 字体，尝试自动安装
    if selected_chinese and not selected_emoji:
        _install_emoji_font()
        # 再检测一次
        try:
            emoji_result = subprocess.run(
                ["fc-list", ":lang=emoji", "family"],
                capture_output=True, text=True, timeout=5,
            )
            if emoji_result.returncode == 0 and emoji_result.stdout.strip():
                for line in emoji_result.stdout.strip().split("\n"):
                    for f in line.split(","):
                        f = f.strip()
                        if f:
                            emoji_fonts.add(f)
                for pref in emoji_priority:
                    if pref in emoji_fonts:
                        selected_emoji = pref
                        break
                if not selected_emoji and emoji_fonts:
                    selected_emoji = next(iter(emoji_fonts))
        except Exception:
            pass

    # 如果连中文字体都没有，尝试自动安装
    if not selected_chinese:
        _install_chinese_font()
        # 安装后再检测一次
        try:
            chinese_result = subprocess.run(
                ["fc-list", ":lang=zh", "family"],
                capture_output=True, text=True, timeout=5,
            )
            if chinese_result.returncode == 0 and chinese_result.stdout.strip():
                for line in chinese_result.stdout.strip().split("\n"):
                    for f in line.split(","):
                        f = f.strip()
                        if f:
                            chinese_fonts.add(f)
                for pref in chinese_priority:
                    if pref in chinese_fonts:
                        selected_chinese = pref
                        break
                if not selected_chinese and chinese_fonts:
                    selected_chinese = next(iter(chinese_fonts))
        except Exception:
            pass

    # 组合字体链（emoji 字体优先）
    if selected_emoji and selected_chinese:
        return f"{selected_emoji}, {selected_chinese}"
    elif selected_chinese:
        return selected_chinese
    elif selected_emoji:
        return selected_emoji

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


def _install_emoji_font():
    """
    尝试在 Linux 上自动安装 emoji 字体。
    支持 apt(Debian/Ubuntu)、dnf(Fedora)、pacman(Arch) 包管理器。
    """
    import shutil

    # 检测包管理器和对应的字体包名
    if shutil.which("apt"):
        pkg_cmd = ["apt", "install", "-y", "fonts-noto-color-emoji", "fonts-symbola"]
    elif shutil.which("dnf"):
        pkg_cmd = ["dnf", "install", "-y", "google-noto-color-emoji-fonts", "fonts-symbola"]
    elif shutil.which("pacman"):
        pkg_cmd = ["pacman", "-S", "--noconfirm", "noto-fonts-emoji", "ttf-symbola"]
    else:
        return

    if shutil.which("pkexec"):
        cmd = ["pkexec"] + pkg_cmd
    elif shutil.which("sudo"):
        cmd = ["sudo"] + pkg_cmd
    else:
        return

    try:
        logging.info(f"正在尝试安装 emoji 字体: {' '.join(cmd)}")
        subprocess.run(cmd, timeout=180, check=False)
        subprocess.run(["fc-cache", "-f"], timeout=30, check=False)
        logging.info("emoji 字体安装完成")
    except Exception as e:
        logging.warning(f"安装 emoji 字体失败: {e}")


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
