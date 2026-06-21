"""工具基础定义 - ToolInfo 数据类 + ToolResult"""

from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Any


@dataclass
class ToolInfo:
    """单个工具的自描述元数据"""
    name: str                              # 工具唯一标识
    description: str                       # 工具描述（供 AI 理解）
    parameters: dict                       # JSON Schema 参数定义
    category: str                          # "version" | "mod" | "server" | "modpack" | "resource" | "system" | "user" | "web"
    permission_action: str                 # 权限标识（默认同 name）
    execute: Callable[..., str]            # 执行函数 (params, callbacks) -> str
    display_name: str = ""                 # 用户友好显示名称

    def to_openai_function(self) -> dict:
        """转换为 OpenAI function calling 格式"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __post_init__(self):
        if not self.display_name:
            self.display_name = self.name
        if not self.permission_action:
            self.permission_action = self.name


@dataclass
class ToolResult:
    """工具执行结果"""
    success: bool = True
    text: str = ""
    error: str = ""
    # 需要用户确认的类型
    needs_user_confirm: str = ""            # "dangerous_command" | "ask_user"
    confirm_data: Optional[dict] = None     # 确认相关的附加数据


@dataclass
class CallbackContext:
    """工具执行所需的回调上下文"""
    get_available_versions: Callable[[], list] = field(default_factory=lambda: lambda: [])
    get_installed_versions: Callable[[], list] = field(default_factory=lambda: lambda: [])
    install_version: Callable[[str, str], tuple] = field(default_factory=lambda: lambda a, b: (False, ""))
    launch_game: Callable[[str], tuple] = field(default_factory=lambda: lambda a: (False, ""))
    remove_version: Callable[[str], tuple] = field(default_factory=lambda: lambda a: (False, ""))
    get_installed_servers: Callable[[], list] = field(default_factory=lambda: lambda: [])
    start_server: Callable[[str, str], tuple] = field(default_factory=lambda: lambda a, b: (False, None))
    remove_server: Callable[[str], tuple] = field(default_factory=lambda: lambda a: (False, ""))
    get_mrpack_information: Callable[[str], dict] = field(default_factory=lambda: lambda a: {})
    install_mrpack: Callable[[str], tuple] = field(default_factory=lambda: lambda a: (False, ""))
    install_mrpack_server: Callable[[str], tuple] = field(default_factory=lambda: lambda a: (False, ""))
    get_minecraft_dir: Callable[[], str] = field(default_factory=lambda: lambda: "")
    get_game_process: Callable[[], Any] = field(default_factory=lambda: lambda: None)

    @classmethod
    def from_callbacks_dict(cls, callbacks: Dict[str, Callable]) -> "CallbackContext":
        """从 callbacks 字典创建"""
        return cls(
            get_available_versions=callbacks.get("get_available_versions", lambda: []),
            get_installed_versions=callbacks.get("get_installed_versions", lambda: []),
            install_version=callbacks.get("install_version", lambda a, b: (False, "")),
            launch_game=callbacks.get("launch_game", lambda a: (False, "")),
            remove_version=callbacks.get("remove_version", lambda a: (False, "")),
            get_installed_servers=callbacks.get("get_installed_servers", lambda: []),
            start_server=callbacks.get("start_server", lambda a, b: (False, None)),
            remove_server=callbacks.get("remove_server", lambda a: (False, "")),
            get_mrpack_information=callbacks.get("get_mrpack_information", lambda a: {}),
            install_mrpack=callbacks.get("install_mrpack", lambda a: (False, "")),
            install_mrpack_server=callbacks.get("install_mrpack_server", lambda a: (False, "")),
            get_minecraft_dir=callbacks.get("get_minecraft_dir", lambda: ""),
            get_game_process=callbacks.get("get_game_process", lambda: None),
        )


# 工具分类常量
CATEGORY_VERSION = "version"
CATEGORY_MOD = "mod"
CATEGORY_SERVER = "server"
CATEGORY_MODPACK = "modpack"
CATEGORY_RESOURCE = "resource"
CATEGORY_SYSTEM = "system"
CATEGORY_USER = "user"
CATEGORY_WEB = "web"
