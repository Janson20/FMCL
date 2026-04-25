"""UI 包 - 向后兼容导出"""
from ui.constants import COLORS, FONT_FAMILY, RESOURCE_TYPES
from ui.dialogs import show_confirmation, show_alert, VersionSelectorDialog
from ui.app import ModernApp
from ui.windows import (
    ResourceManagerWindow,
    LauncherSettingsWindow,
    ModpackInstallWindow,
    ModpackServerWindow,
    ModBrowserWindow,
)

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
