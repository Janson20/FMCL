"""跨平台中文字体检测与按需安装（阶段 2 任务 2.10 的字体注入部分）。

原地址是 ``ui/constants.py`` 的字体段落，**逐字搬运，逻辑一行未改**；
``ui/constants.py`` 现在只做再导出（阶段 1 搬迁的一贯做法）。

## 为什么必须搬到服务层

QML 运行时**不许 import ``ui/`` 包**：``ui/__init__.py`` 会
``from ui.app import ModernApp`` → 把 ``customtkinter`` 拖进 PySide6 进程
（阶段 0 第 10.4 节实测踩过；阶段 2 契约第六节决策 12 把它定成红线）。
而字体检测**只依赖标准库 + ``services`` 自己**：``platform`` / ``subprocess``
（``fc-list`` / ``fc-match``）/ ``glob`` / ``tempfile``，一行界面代码都没有。
留在 ``ui/`` 里，QML 侧想给 ``QGuiApplication.setFont()`` 取字体名就只有两条路：
要么 import ``ui/``（拖进 Tk），要么把检测逻辑抄第二份（迁移红线 2：业务逻辑只搬一次）。
因此整体搬到服务层，由 ``services/palette.py`` / ``services/user_agent.py``
同一条路线（那两个也是从 ``ui/constants.py`` 搬出来的）。

## 本机实测值（Windows）

Windows 分支直接返回 ``"Microsoft YaHei"``，**不跑任何 subprocess 探测**；
本机（Python 3.11.14 / Windows x64）实测：

    >>> from services.font_service import FONT_FAMILY
    >>> print(FONT_FAMILY)
    Microsoft YaHei

macOS 分支返回 ``"PingFang SC"``。Linux 走三级兜底：
``fc-list :lang=zh`` → ``fc-match -s 中文测试 family`` → 扫常见字体目录，
三条都失败才回退 ``"Sans"`` 交给系统 fontconfig 兜底。

## 语义与边界（本轮不改，与旧实现逐字一致）

- ``FONT_FAMILY`` 是 :class:`services.user_agent.LazyStr`：**import 时不探测**，
  首次 ``str()`` / f-string / 格式化时才跑检测并缓存 —— 启动路径不为它付代价。
- 字体安装（:func:`_install_chinese_font` / :func:`_install_emoji_font`）
  **不再由检测自动触发**，只供设置界面按需调用；否则每次启动都会弹
  pkexec/sudo 密码框（旧实现踩过的坑，注释里保留了原话）。
- 安装尝试标记文件 :data:`_FONT_INSTALL_MARKER` 落在系统临时目录，跨会话去重。

## 与 ``ui/constants.py`` 原文的**唯一**差异

两处 ``subprocess.run(..., text=True)`` 补了 ``errors="replace"``：这是阶段 1 的既有守卫
``tests/test_server_service.py::test_subprocess_text_calls_tolerate_bad_bytes`` 强制的
（``services/**`` 里读子进程文本输出的调用必须显式容忍坏字节，否则在项目自己的
``-X utf8`` 命令下，reader 线程会抛 ``UnicodeDecodeError`` 而 ``stdout`` 变成半截）。
阶段 1 已经为 ``services/`` 修过 13 处同类缺陷，搬到服务层的代码必须守同一条规矩。
除此之外**逐字一致** —— ``poc/verify_font_move.py`` 逐函数比对基线 ``640f7eb``，
唯一允许的差异就是这一处（脚本里显式列出）。
"""

import glob
import logging
import os
import platform
import subprocess
import tempfile
from pathlib import Path

from services.user_agent import LazyStr

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
        result = subprocess.run(
            ["fc-list", f":lang={lang}", "family"], capture_output=True, text=True, errors="replace", timeout=5
        )
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
        result = subprocess.run(
            ["fc-match", "-s", cjk_text, "family"], capture_output=True, text=True, errors="replace", timeout=5
        )
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


FONT_FAMILY = LazyStr(_detect_font_family)


__all__ = [
    "FONT_FAMILY",
    "_CHINESE_FONT_PRIORITY",
    "_EMOJI_FONT_PRIORITY",
    "_FONT_INSTALL_MARKER",
    "_detect_font_family",
    "_has_install_attempted",
    "_install_chinese_font",
    "_install_emoji_font",
    "_mark_install_attempted",
    "_run_fc_list",
    "_run_fc_match",
    "_scan_common_cjk_fonts",
    "_select_preferred",
]
