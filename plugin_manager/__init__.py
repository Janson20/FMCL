"""插件管理器包 - FMCL Plugin System

提供插件的加载、卸载、权限控制、钩子系统、依赖解析等核心功能。
"""

from plugin_manager.manifest import PluginManifest, PLUGIN_MANIFEST_SCHEMA
from plugin_manager.permissions import PluginPermission, PermissionRiskLevel, classify_permissions
from plugin_manager.base import PluginBase, PluginState, HookPoint
from plugin_manager.hook_bus import HookBus
from plugin_manager.dependency import DependencyResolver
from plugin_manager.loader import PluginLoader
from plugin_manager.installer import PluginInstaller
from plugin_manager.manager import PluginManager
from plugin_manager.market import PluginMarket

__all__ = [
    "PluginManifest",
    "PLUGIN_MANIFEST_SCHEMA",
    "PluginPermission",
    "PermissionRiskLevel",
    "classify_permissions",
    "PluginBase",
    "PluginState",
    "HookPoint",
    "HookBus",
    "DependencyResolver",
    "PluginLoader",
    "PluginInstaller",
    "PluginManager",
    "PluginMarket",
]
