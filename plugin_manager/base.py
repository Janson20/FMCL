"""插件基类与钩子点定义"""

import abc
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from plugin_manager.manifest import PluginManifest
from plugin_manager.permissions import PluginPermissionState

if TYPE_CHECKING:
    from plugin_manager.manager import PluginManager


class PluginState(str, Enum):
    """插件状态"""

    SCANNED = "scanned"  # 已发现，尚未加载
    LOADING = "loading"  # 正在加载模块
    LOADED = "loaded"  # 模块已 import，Plugin 实例已创建
    ENABLED = "enabled"  # on_enable() 已执行
    INIT_ERROR = "init_error"  # 初始化阶段出错
    RUNNING = "running"  # 正常运行中
    DISABLED = "disabled"  # 用户主动禁用
    ERROR = "error"  # 运行中出错
    INCOMPATIBLE = "incompatible"  # 版本不兼容
    UNINSTALLING = "uninstalling"


class HookPoint(str, Enum):
    """钩子点定义 - 插件可以注册到这些生命周期事件

    命名规范: domain.event 形式
    """

    # ── 应用生命周期 ──
    APP_STARTUP = "app.startup"  # 启动器初始化完成
    APP_SHUTDOWN = "app.shutdown"  # 启动器关闭前

    # ── 游戏生命周期 ──
    GAME_PRE_LAUNCH = "game.pre_launch"  # 游戏启动前 (可修改启动参数)
    GAME_POST_LAUNCH = "game.post_launch"  # 游戏启动后 (参数: pid, process)
    GAME_STOPPED = "game.stopped"  # 游戏进程停止 (参数: exit_code)
    GAME_CRASHED = "game.crashed"  # 游戏崩溃后 (参数: crash_report)

    # ── 版本生命周期 ──
    VERSION_PRE_INSTALL = "version.pre_install"  # 版本安装前 (参数: version_id)
    VERSION_POST_INSTALL = "version.post_install"  # 版本安装后 (参数: version_id, success)
    VERSION_PRE_REMOVE = "version.pre_remove"  # 版本删除前 (参数: version_id)

    # ── 服务器生命周期 ──
    SERVER_PRE_START = "server.pre_start"  # 服务器启动前 (参数: server_name)
    SERVER_POST_START = "server.post_start"  # 服务器启动后 (参数: server_name, process)
    SERVER_STOPPED = "server.stopped"  # 服务器停止 (参数: server_name, exit_code)

    # ── UI 扩展 ──
    UI_TAB_REGISTER = "ui.tab.register"  # 注册主界面标签页
    UI_SIDEBAR_REGISTER = "ui.sidebar.register"  # 注册侧边栏项目
    UI_SETTINGS_REGISTER = "ui.settings.register"  # 注册设置条目

    # ── 下载生命周期 ──
    DOWNLOAD_PRE_DOWNLOAD = "download.pre_download"  # 文件下载前 (可修改 URL)
    DOWNLOAD_POST_DOWNLOAD = "download.post_download"  # 文件下载完成


class PluginBase(abc.ABC):
    """插件基类

    所有 FMCL 插件必须继承此类并实现 on_enable / on_disable。
    引擎会将以下属性注入实例:
        - self.manifest: 插件清单
        - self.plugin_dir: 插件目录
        - self.data_dir: 插件数据目录
        - self.config: 插件配置 (自动持久化的 dict)
        - self._manager: PluginManager 引用
        - self._perm_state: 权限状态
    """

    # 注入属性类型声明
    manifest: PluginManifest
    plugin_dir: Path
    data_dir: Path
    config: dict
    _manager: "PluginManager"
    _perm_state: PluginPermissionState

    # ── 生命周期方法 (子类必须实现) ──

    @abc.abstractmethod
    def on_enable(self) -> None:
        """插件启用时调用

        在此注册钩子、创建 UI 组件、启动后台任务等。
        抛出异常将导致插件进入 ERROR 状态。
        """
        ...

    @abc.abstractmethod
    def on_disable(self) -> None:
        """插件停用时调用

        在此注销钩子、清理资源、保存状态。
        抛出异常会被捕获并记录日志，不会阻止停用流程。
        """
        ...

    # ── 可选覆盖的生命周期方法 ──

    def on_load(self) -> None:
        """模块 import 后首次调用（on_enable 之前）

        可用于初始化非 UI 的轻量操作。
        """
        pass

    def on_uninstall(self) -> None:
        """插件被卸载前调用

        用于清理插件创建的全局资源。
        """
        pass

    # ── 可选覆盖的 UI 方法 ──

    def get_settings_ui(self, parent) -> Optional[Any]:
        """返回设置面板 UI 组件

        Args:
            parent: 父级 tkinter/CTk 容器

        Returns:
            设置面板控件，或 None 表示无设置页
        """
        return None

    def get_tab_ui(self, parent) -> Optional[Any]:
        """返回主界面标签页 UI 组件

        Args:
            parent: 父级 tkinter/CTk 容器

        Returns:
            (tab_name: str, tab_frame: CTkFrame) 元组，或 None
        """
        return None

    def get_sidebar_item(self) -> Optional[Dict[str, Any]]:
        """返回侧边栏项目信息

        Returns:
            {
                "id": str,           # 唯一标识
                "text": str,         # 显示文本 (i18n 键)
                "icon": str,         # 图标字符
                "command": callable, # 点击回调
                "order": int,        # 排序权重
            }
            或 None 表示不添加
        """
        return None

    # ── 默认配置 ──

    def get_default_config(self) -> dict:
        """返回默认配置字典，子类可覆盖"""
        return {}

    # ── 便利方法 ──

    def log(self, message: str, level: str = "info"):
        """通过启动器日志系统记录"""
        from logzero import logger

        log_func = getattr(logger, level, logger.info)
        log_func(f"[Plugin:{self.manifest.id}] {message}")

    def notify(self, title: str, message: str, level: str = "info"):
        """发送 Toast 通知给用户（需要 ui.notification 权限）

        Args:
            title: 通知标题
            message: 通知内容
            level: info / warning / error
        """
        from plugin_manager.permissions import PluginPermission

        if not self._perm_state.is_granted(PluginPermission.UI_NOTIFICATION):
            self.log(f"通知被拦截 (权限不足): {title} - {message}", "warning")
            return
        if hasattr(self._manager, "_notify_user"):
            self._manager._notify_user(self.manifest.id, title, message, level)
