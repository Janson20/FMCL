"""UI 主应用模块 - 组合自各个 Mixin"""

from typing import Callable, Dict

import customtkinter as ctk

from ui.agent import AgentMixin
from ui.app_about import AboutTabMixin
from ui.app_achievements import AchievementTabMixin
from ui.app_backup import BackupTabMixin
from ui.app_base import ModernAppBase
from ui.app_crash import CrashHandlerMixin
from ui.app_handlers import EventHandlerMixin
from ui.app_monitor import MonitorMixin
from ui.app_music import MusicPlayerMixin
from ui.app_online import OnlineTabMixin
from ui.app_server import ServerTabMixin
from ui.app_tools import ToolsTabMixin
from ui.constants import COLORS


class ModernApp(
    CrashHandlerMixin,
    EventHandlerMixin,
    BackupTabMixin,
    OnlineTabMixin,
    ServerTabMixin,
    AchievementTabMixin,
    MusicPlayerMixin,
    MonitorMixin,
    ToolsTabMixin,
    AboutTabMixin,
    AgentMixin,
    ModernAppBase,
):
    """FMCL 启动器主窗口 - 组合自各功能 Mixin"""

    pass


__all__ = ["ModernApp"]
