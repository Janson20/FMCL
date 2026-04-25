"""UI 主应用模块 - 组合自各个 Mixin"""
import customtkinter as ctk
from typing import Dict, Callable

from ui.constants import COLORS
from ui.app_base import ModernAppBase
from ui.app_server import ServerTabMixin
from ui.app_handlers import EventHandlerMixin
from ui.app_crash import CrashHandlerMixin


class ModernApp(CrashHandlerMixin, EventHandlerMixin, ServerTabMixin, ModernAppBase):
    """FMCL 启动器主窗口 - 组合自各功能 Mixin"""
    pass


__all__ = ["ModernApp"]
