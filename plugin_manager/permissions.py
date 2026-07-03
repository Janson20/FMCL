"""插件权限系统 - 权限枚举、分级与运行时确认逻辑"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Set, Optional


class PluginPermission(str, Enum):
    """插件权限枚举

    每个权限对应插件可以执行的操作。权限字符串同时也是 i18n 键名。
    """
    # ── 文件系统 ──
    FILESYSTEM_READ = "filesystem.read"       # 读取文件（排除启动器核心目录）
    FILESYSTEM_WRITE = "filesystem.write"     # 写入文件（仅 .minecraft + 插件数据目录）

    # ── 网络 ──
    NETWORK_HTTP = "network.http"             # HTTP(S) 请求（通过启动器代理 UA）
    NETWORK_SOCKET = "network.socket"         # 原始 Socket 连接

    # ── UI ──
    UI_EXTEND = "ui.extend"                   # 注册标签页/侧边栏/设置面板
    UI_NOTIFICATION = "ui.notification"       # 弹窗/Toast 通知

    # ── 核心 ──
    CORE_DOWNLOAD = "core.download"           # 注册自定义下载源
    CORE_VERSION = "core.version"             # 注册自定义版本源
    CORE_LAUNCH_HOOK = "core.launch_hook"     # 游戏启动前/后钩子（可修改启动参数）
    CORE_PROCESS = "core.process"             # 执行外部进程

    # ── 数据 ──
    DATA_STORE = "data.store"                 # 持久化存储（插件数据目录读写）
    DATA_SETTINGS = "data.settings"           # 读取/修改启动器设置


class PermissionRiskLevel(Enum):
    """权限风险等级"""
    LOW = "low"         # 安装时一次性确认
    MEDIUM = "medium"   # 安装时确认 + 设置中可撤销
    HIGH = "high"       # 每次触发时弹窗确认，可选「始终允许」


# 权限到风险等级的映射
_PERMISSION_RISK_MAP: Dict[PluginPermission, PermissionRiskLevel] = {
    # 低风险
    PluginPermission.FILESYSTEM_READ: PermissionRiskLevel.LOW,
    PluginPermission.NETWORK_HTTP: PermissionRiskLevel.LOW,
    PluginPermission.UI_EXTEND: PermissionRiskLevel.LOW,
    PluginPermission.UI_NOTIFICATION: PermissionRiskLevel.LOW,
    PluginPermission.DATA_STORE: PermissionRiskLevel.LOW,
    # 中风险
    PluginPermission.FILESYSTEM_WRITE: PermissionRiskLevel.MEDIUM,
    PluginPermission.CORE_DOWNLOAD: PermissionRiskLevel.MEDIUM,
    PluginPermission.CORE_VERSION: PermissionRiskLevel.MEDIUM,
    PluginPermission.DATA_SETTINGS: PermissionRiskLevel.MEDIUM,
    # 高风险
    PluginPermission.NETWORK_SOCKET: PermissionRiskLevel.HIGH,
    PluginPermission.CORE_LAUNCH_HOOK: PermissionRiskLevel.HIGH,
    PluginPermission.CORE_PROCESS: PermissionRiskLevel.HIGH,
}


# 权限的显示名称 i18n 键
_PERMISSION_DISPLAY_KEYS: Dict[PluginPermission, str] = {
    PluginPermission.FILESYSTEM_READ: "plugin_permission_filesystem_read",
    PluginPermission.FILESYSTEM_WRITE: "plugin_permission_filesystem_write",
    PluginPermission.NETWORK_HTTP: "plugin_permission_network_http",
    PluginPermission.NETWORK_SOCKET: "plugin_permission_network_socket",
    PluginPermission.UI_EXTEND: "plugin_permission_ui_extend",
    PluginPermission.UI_NOTIFICATION: "plugin_permission_ui_notification",
    PluginPermission.CORE_DOWNLOAD: "plugin_permission_core_download",
    PluginPermission.CORE_VERSION: "plugin_permission_core_version",
    PluginPermission.CORE_LAUNCH_HOOK: "plugin_permission_core_launch_hook",
    PluginPermission.CORE_PROCESS: "plugin_permission_core_process",
    PluginPermission.DATA_STORE: "plugin_permission_data_store",
    PluginPermission.DATA_SETTINGS: "plugin_permission_data_settings",
}


@dataclass
class PermissionGrant:
    """单个权限的授权状态"""
    permission: PluginPermission
    risk_level: PermissionRiskLevel
    granted: bool = False
    always_allowed: bool = False      # 仅对高风险权限有效


@dataclass
class PluginPermissionState:
    """插件的全部权限授权状态"""
    plugin_id: str
    grants: Dict[PluginPermission, PermissionGrant] = field(default_factory=dict)

    def __post_init__(self):
        if not self.grants:
            for perm in PluginPermission:
                risk = _PERMISSION_RISK_MAP.get(perm, PermissionRiskLevel.LOW)
                self.grants[perm] = PermissionGrant(
                    permission=perm,
                    risk_level=risk,
                    granted=False,
                )

    def is_granted(self, permission: PluginPermission) -> bool:
        """检查指定权限是否已授权"""
        g = self.grants.get(permission)
        return g is not None and g.granted

    def check_or_request(
        self, permission: PluginPermission,
    ) -> str:
        """检查权限，返回状态字符串: 'granted' | 'need_confirm' | 'denied'"""
        g = self.grants.get(permission)
        if g is None:
            return "denied"
        if g.granted:
            return "granted"
        return "need_confirm"

    def grant(self, permission: PluginPermission, always: bool = False):
        """授权指定权限"""
        g = self.grants.get(permission)
        if g:
            g.granted = True
            if always:
                g.always_allowed = True

    def revoke(self, permission: PluginPermission):
        """撤销指定权限"""
        g = self.grants.get(permission)
        if g:
            g.granted = False
            g.always_allowed = False

    def get_ungranted_permissions(self) -> List[PluginPermission]:
        """获取所有未授权的权限"""
        return [p for p, g in self.grants.items() if not g.granted]

    def to_dict(self) -> dict:
        """序列化为持久化格式"""
        result: Dict[str, dict] = {}
        for perm, grant in self.grants.items():
            result[perm.value] = {
                "granted": grant.granted,
                "always_allowed": grant.always_allowed,
            }
        return result

    @classmethod
    def from_dict(cls, plugin_id: str, data: dict) -> "PluginPermissionState":
        """从持久化格式恢复"""
        state = cls(plugin_id=plugin_id)
        for key, val in data.items():
            try:
                perm = PluginPermission(key)
                g = state.grants.get(perm)
                if g:
                    g.granted = val.get("granted", False)
                    g.always_allowed = val.get("always_allowed", False)
            except ValueError:
                pass
        return state


def classify_permissions(
    permissions: List[str],
) -> Dict[PermissionRiskLevel, List[PluginPermission]]:
    """将权限字符串列表按风险等级分类"""
    result: Dict[PermissionRiskLevel, List[PluginPermission]] = {
        PermissionRiskLevel.LOW: [],
        PermissionRiskLevel.MEDIUM: [],
        PermissionRiskLevel.HIGH: [],
    }
    for p_str in permissions:
        try:
            perm = PluginPermission(p_str)
            risk = _PERMISSION_RISK_MAP.get(perm, PermissionRiskLevel.LOW)
            result[risk].append(perm)
        except ValueError:
            pass
    return result


def get_permission_display_key(permission: PluginPermission) -> str:
    """获取权限的 i18n 显示键"""
    return _PERMISSION_DISPLAY_KEYS.get(permission, f"plugin_permission_{permission.value}")


def get_permission_risk(permission: PluginPermission) -> PermissionRiskLevel:
    """获取权限的风险等级"""
    return _PERMISSION_RISK_MAP.get(permission, PermissionRiskLevel.LOW)


def get_all_permissions() -> List[PluginPermission]:
    """获取所有定义的权限"""
    return list(PluginPermission)
