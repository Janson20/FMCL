"""UI 常量定义 - 颜色主题、字体检测、全局配置"""

import glob
import logging
import os
import platform
import subprocess
import tempfile
from pathlib import Path

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

# 运行时颜色引用（主题引擎会直接更新此字典，所有引用自动同步）
current_colors = COLORS

# ─── 跨平台中文字体检测 ──────────────────────────────────────────

# 中文字体优先级列表
_CHINESE_FONT_PRIORITY = [
    "Noto Sans CJK SC",
    "Noto Sans SC",
    "WenQuanYi Micro Hei",
    "WenQuanYi Zen Hei",
    "Droid Sans Fallback",
    "Source Han Sans SC",
    "Source Han Sans CN",
    "Noto Serif CJK SC",
    "Noto Serif SC",
    "AR PL UMing CN",
    "AR PL UKai CN",
]

# Emoji 字体优先级列表
_EMOJI_FONT_PRIORITY = [
    "Noto Color Emoji",
    "Symbola",
    "DejaVu Sans",
    "EmojiOne",
    "JoyPixels",
    "Twitter Color Emoji",
    "Apple Color Emoji",
]

# 安装尝试标记文件路径（用于避免每次启动都尝试安装）
_FONT_INSTALL_MARKER = os.path.join(tempfile.gettempdir(), ".fmcl_font_install_attempted")


def _run_fc_list(lang: str) -> set:
    """通过 fc-list 按语言查询字体族名，返回去重集合。"""
    fonts: set[str] = set()
    try:
        result = subprocess.run(["fc-list", f":lang={lang}", "family"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                for f in line.split(","):
                    f = f.strip()
                    if f:
                        fonts.add(f)
    except Exception:
        pass
    return fonts


def _run_fc_match(cjk_text: str = "中文测试") -> str:
    """
    通过 fc-match 测试指定文本能否被字体渲染，
    返回匹配到的字体族名，失败返回空字符串。
    """
    try:
        result = subprocess.run(["fc-match", "-s", cjk_text, "family"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            for line in result.stdout.strip().split("\n"):
                name = line.strip()
                if name and name != "sans-serif":
                    return name
    except Exception:
        pass
    return ""


def _scan_common_cjk_fonts() -> str:
    """
    扫描系统常见字体目录，寻找已知中文字体文件名。
    这是 fc-list 检测失败时的最后兜底方案。
    """
    cjk_name_patterns = [
        "*Noto*Sans*CJK*",
        "*Noto*Sans*SC*",
        "*Noto*Serif*CJK*",
        "*Noto*Serif*SC*",
        "*WenQuanYi*",
        "*Droid*Sans*Fallback*",
        "*Source*Han*Sans*SC*",
        "*Source*Han*Sans*CN*",
        "*wqy-microhei*",
        "*wqy-zenhei*",
        "*wqy-micro*",
        "*wqy-zen*",
    ]

    font_dirs = [
        "/usr/share/fonts",
        "/usr/local/share/fonts",
        os.path.expanduser("~/.fonts"),
        os.path.expanduser("~/.local/share/fonts"),
    ]

    found: set[str] = set()
    for d in font_dirs:
        for pattern in cjk_name_patterns:
            try:
                for fp in glob.glob(os.path.join(d, "**", pattern), recursive=True):
                    name = Path(fp).stem
                    found.add(name)
            except Exception:
                continue

    if found:
        return next(iter(found))
    return ""


def _select_preferred(available: set, priority: list) -> str:
    """从可用字体集合中返回优先级最高的字体名。"""
    if not available:
        return ""
    for pref in priority:
        if pref in available:
            return pref
    return next(iter(available))


def _has_install_attempted() -> bool:
    """检查是否已经尝试过自动安装字体（跨会话持久化）。"""
    return os.path.exists(_FONT_INSTALL_MARKER)


def _mark_install_attempted():
    """标记自动安装已尝试，避免重复执行。"""
    try:
        Path(_FONT_INSTALL_MARKER).touch()
    except Exception:
        pass


def _detect_font_family() -> str:
    """
    检测当前平台可用的中文字体，返回字体名称或字体组合。

    - Windows: Microsoft YaHei
    - macOS: PingFang SC
    - Linux: 多层次检测策略（fc-list → fc-match → 文件扫描），
             始终返回合理的兜底字体，不自动执行系统包管理器安装。
    """
    system = platform.system().lower()

    if system == "windows":
        return "Microsoft YaHei"

    if system == "darwin":
        return "PingFang SC"

    # ── Linux: 多层次字体检测 ──
    logging.info("正在检测系统字体...")

    # 策略1: fc-list 按语言检测（最标准的方式）
    chinese_fonts = _run_fc_list("zh")
    selected_chinese = _select_preferred(chinese_fonts, _CHINESE_FONT_PRIORITY)

    # 策略2: fc-match 测试 CJK 文本渲染
    if not selected_chinese:
        match_name = _run_fc_match("中文测试")
        if match_name:
            selected_chinese = match_name
            logging.debug(f"通过 fc-match 检测到中文字体: {selected_chinese}")

    # 策略3: 扫描字体文件路径
    if not selected_chinese:
        scanned = _scan_common_cjk_fonts()
        if scanned:
            selected_chinese = scanned
            logging.debug(f"通过文件扫描检测到中文字体: {selected_chinese}")

    # 检测 emoji 字体（仅用 fc-list，轻量且够用）
    emoji_fonts = _run_fc_list("emoji")
    selected_emoji = _select_preferred(emoji_fonts, _EMOJI_FONT_PRIORITY)

    # 组合字体链（emoji 字体优先 — 让系统用 emoji 字体渲染 😀，中文用中文字体）
    if selected_emoji and selected_chinese:
        result = f"{selected_emoji}, {selected_chinese}"
    elif selected_chinese:
        result = selected_chinese
    elif selected_emoji:
        result = selected_emoji
    else:
        # 所有检测策略均失败 → 返回 "Sans"，由 system font fallback 处理
        # 不再尝试自动安装字体，避免每次启动弹出 pkexec/sudo 密码框
        result = "Sans"
    logging.info(f"字体检测完成: {result}")
    return result


# ═══════════════════════════════════════════════════════════════
# 以下函数保留用于手动/按需安装（从设置界面调用），不再由 _detect_font_family 自动触发
# ═══════════════════════════════════════════════════════════════
def _install_chinese_font():
    """
    尝试在 Linux 上安装中文字体（apt/dnf/pacman）。
    仅应从设置界面按需调用，不应在启动时自动执行。
    """
    import shutil

    if _has_install_attempted():
        logging.info("此前已尝试过安装中文字体，跳过重复安装")
        return

    # 检测包管理器和对应的字体包名
    if shutil.which("apt"):
        pkg_cmd = ["apt", "install", "-y", "fonts-noto-cjk"]
    elif shutil.which("dnf"):
        pkg_cmd = ["dnf", "install", "-y", "google-noto-sans-cjk-fonts"]
    elif shutil.which("pacman"):
        pkg_cmd = ["pacman", "-S", "--noconfirm", "noto-fonts-cjk"]
    else:
        logging.warning("未检测到支持的包管理器，无法自动安装中文字体")
        _mark_install_attempted()
        return

    # 优先 pkexec（图形化认证对话框），回退 sudo
    if shutil.which("pkexec"):
        cmd = ["pkexec"] + pkg_cmd
    elif shutil.which("sudo"):
        cmd = ["sudo"] + pkg_cmd
    else:
        logging.warning("未找到 pkexec 或 sudo，无法安装中文字体")
        _mark_install_attempted()
        return

    try:
        logging.info(f"正在尝试安装中文字体: {' '.join(cmd)}")
        subprocess.run(cmd, timeout=180, check=False)
        # 刷新字体缓存
        subprocess.run(["fc-cache", "-f"], timeout=30, check=False)
        logging.info("中文字体安装完成")
    except Exception as e:
        logging.warning(f"安装中文字体失败: {e}")
    finally:
        _mark_install_attempted()


def _install_emoji_font():
    """
    尝试在 Linux 上安装 emoji 字体（apt/dnf/pacman）。
    仅应从设置界面按需调用，不应在启动时自动执行。
    """
    import shutil

    if _has_install_attempted():
        return

    # 检测包管理器和对应的字体包名
    if shutil.which("apt"):
        pkg_cmd = ["apt", "install", "-y", "fonts-noto-color-emoji", "fonts-symbola"]
    elif shutil.which("dnf"):
        pkg_cmd = ["dnf", "install", "-y", "google-noto-color-emoji-fonts"]
    elif shutil.which("pacman"):
        pkg_cmd = ["pacman", "-S", "--noconfirm", "noto-fonts-emoji"]
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
    finally:
        _mark_install_attempted()


class LazyStr:
    """惰性求值字符串 — 首次 str()/f-string/format 时才计算实际值

    各模块 ``from ui.constants import FONT_FAMILY`` 拿到的是轻量对象，
    真正的字体检测（subprocess 调用）在 CTkFont(family=FONT_FAMILY) 构建时发生。
    """

    def __init__(self, func):
        self._func = func
        self._value = None

    def __str__(self):
        if self._value is None:
            self._value = self._func()
        return self._value

    def __repr__(self):
        return str(self)


FONT_FAMILY = LazyStr(_detect_font_family)


def _get_fmcl_version():
    """从 updater.py 获取 FMCL 版本号"""
    try:
        from updater import get_current_version

        return get_current_version()
    except Exception:
        pass
    return "unknown"


def _get_user_agent() -> str:
    """获取 HTTP User-Agent 字符串"""
    return f"FMCL/{_get_fmcl_version()}"


USER_AGENT = LazyStr(_get_user_agent)


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
