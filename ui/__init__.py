"""UI 包 - 向后兼容导出（PEP 562 惰性加载）

**为什么惰性**：本模块原先 eager ``from ui.app import ModernApp``，导致
``import ui.constants``（乃至核心层的 ``from ui.constants import USER_AGENT``）
都会先执行本文件、把 customtkinter + tkinter + 整个界面拖进来
（实测 1661 个模块）。改成模块级 ``__getattr__`` 后，
``import ui.constants`` 只会加载 ``ui.constants`` 这个纯标准库模块。

``COLORS`` / ``FONT_FAMILY`` / ``RESOURCE_TYPES`` 本来就是轻量名字，
保持为真实模块属性（与改造前的 ``ui.COLORS`` 语义一致）；
需要界面重模块的名字一律走 ``__getattr__`` 首次访问时再导入。
"""

from ui.constants import COLORS, FONT_FAMILY, RESOURCE_TYPES

__all__ = [
    "COLORS",
    "FONT_FAMILY",
    "RESOURCE_TYPES",
    "show_confirmation",
    "show_alert",
    "VersionSelectorDialog",
    "ModernApp",
    "ResourceManagerWindow",
    "LauncherSettingsWindow",
    "ModpackInstallWindow",
    "ModpackServerWindow",
    "ModBrowserWindow",
]

# 惰性名字 → 所属界面模块
_DIALOG_NAMES = ("show_alert", "show_confirmation", "VersionSelectorDialog")
_WINDOW_NAMES = (
    "ResourceManagerWindow",
    "LauncherSettingsWindow",
    "ModpackInstallWindow",
    "ModpackServerWindow",
    "ModBrowserWindow",
)


def __getattr__(name):
    """首次访问时才导入对应界面模块（不缓存到本模块，保持薄转发）。"""
    if name == "ModernApp":
        import ui.app as _module
    elif name in _DIALOG_NAMES:
        import ui.dialogs as _module
    elif name in _WINDOW_NAMES:
        import ui.windows as _module
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(_module, name)


def __dir__():
    return sorted(set(globals()) | set(__all__))
