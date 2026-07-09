"""UI 独立窗口类包"""

from ui.windows.launcher_settings import LauncherSettingsWindow
from ui.windows.mod_browser import ModBrowserWindow
from ui.windows.modpack_browser import ModpackBrowserWindow
from ui.windows.modpack_install import ModpackInstallWindow
from ui.windows.modpack_server import ModpackServerWindow
from ui.windows.plugin_browser import PluginBrowserWindow
from ui.windows.plugin_manager import PluginManagerWindow
from ui.windows.plugin_permission_dialog import PluginPermissionDialog
from ui.windows.resource_manager import ResourceManagerWindow

__all__ = [
    "ResourceManagerWindow",
    "LauncherSettingsWindow",
    "ModpackInstallWindow",
    "ModpackServerWindow",
    "ModBrowserWindow",
    "ModpackBrowserWindow",
    "PluginManagerWindow",
    "PluginPermissionDialog",
    "PluginBrowserWindow",
]
