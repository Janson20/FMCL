"""Minecraft启动器包"""
from launcher.core import MinecraftLauncher as MinecraftLauncherCore
from launcher.server import ServerMixin
from launcher.mrpack import MrpackMixin
from launcher.verify import concurrent_file_verify


class MinecraftLauncher(MrpackMixin, ServerMixin, MinecraftLauncherCore):
    """Minecraft启动器类 - 组合自核心模块、服务器模块和整合包模块"""
    pass


__all__ = ["MinecraftLauncher", "concurrent_file_verify"]
