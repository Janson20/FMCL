"""AGENT 配置管理 - Provider 密钥、模型选择、权限规则

配置持久化到 config.json 的 agent 字段。
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from logzero import logger

from ui.agent.permission import PermissionManager, DEFAULT_RULES


@dataclass
class ProviderConfig:
    """单个提供商的配置"""
    enabled: bool = False
    api_key: str = ""
    api_url: str = ""           # 自定义 API URL
    default_model: str = ""     # 默认模型
    custom_models: List[str] = field(default_factory=list)  # 自定义模型列表


@dataclass
class AgentConfig:
    """Agent 全局配置"""

    # 当前选中的提供商
    active_provider: str = "jingdu"

    # 各提供商配置
    providers: Dict[str, ProviderConfig] = field(default_factory=dict)

    # 当前选中的模型（按提供商存储）
    active_models: Dict[str, str] = field(default_factory=dict)

    # 权限规则（ask/deny 必须在 allow 通配符之前）
    permissions: List[dict] = field(default_factory=lambda: [
        {"action": "exec_command", "resource": "*", "effect": "ask"},
        {"action": "write_file", "resource": "*", "effect": "ask"},
        {"action": "replace_in_file", "resource": "*", "effect": "ask"},
        {"action": "delete_file", "resource": "*", "effect": "ask"},
        {"action": "*", "resource": "*", "effect": "allow"},
    ])

    # 通用设置
    max_iterations: int = 50
    stream_enabled: bool = True
    compact_auto: bool = True      # 自动压缩上下文

    # Bing API Key
    bing_api_key: str = ""

    def get_provider_config(self, provider_id: str) -> ProviderConfig:
        """获取指定提供商的配置"""
        if provider_id not in self.providers:
            # 净读 AI 始终启用（如果设置了 token）
            self.providers[provider_id] = ProviderConfig()
        return self.providers[provider_id]

    def set_provider_config(self, provider_id: str, config: ProviderConfig):
        """设置提供商配置"""
        self.providers[provider_id] = config

    def get_active_model(self) -> tuple:
        """获取当前活动的模型 (provider_id, model_id)"""
        provider_id = self.active_provider
        model_id = self.active_models.get(provider_id, "")
        return provider_id, model_id

    def set_active_model(self, provider_id: str, model_id: str):
        """设置当前活动的模型"""
        self.active_provider = provider_id
        self.active_models[provider_id] = model_id

    def is_provider_ready(self, provider_id: str) -> bool:
        """检查提供商是否可用（已配置 API Key）"""
        if provider_id == "jingdu":
            return True  # 净读 AI 通过主配置管理
        pc = self.providers.get(provider_id)
        if pc is None:
            return False
        return bool(pc.api_key)

    def to_dict(self) -> dict:
        """序列化"""
        return {
            "active_provider": self.active_provider,
            "active_models": self.active_models,
            "providers": {
                pid: {
                    "enabled": pc.enabled,
                    "api_key": pc.api_key,
                    "api_url": pc.api_url,
                    "default_model": pc.default_model,
                    "custom_models": pc.custom_models,
                }
                for pid, pc in self.providers.items()
            },
            "permissions": self.permissions,
            "max_iterations": self.max_iterations,
            "stream_enabled": self.stream_enabled,
            "compact_auto": self.compact_auto,
            "bing_api_key": self.bing_api_key,
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "AgentConfig":
        """反序列化"""
        if data is None:
            return cls()

        config = cls()
        config.active_provider = data.get("active_provider", "jingdu")
        config.active_models = data.get("active_models", {})
        config.max_iterations = data.get("max_iterations", 50)
        config.stream_enabled = data.get("stream_enabled", True)
        config.compact_auto = data.get("compact_auto", True)
        config.bing_api_key = data.get("bing_api_key", "")

        # 加载提供商配置
        providers_data = data.get("providers", {})
        for pid, pd in providers_data.items():
            config.providers[pid] = ProviderConfig(
                enabled=pd.get("enabled", False),
                api_key=pd.get("api_key", ""),
                api_url=pd.get("api_url", ""),
                default_model=pd.get("default_model", ""),
                custom_models=pd.get("custom_models", []),
            )

        # 加载权限规则
        permissions = data.get("permissions", [])
        if permissions:
            config.permissions = permissions

        return config

    @classmethod
    def load_from_file(cls, config_path: str = "") -> "AgentConfig":
        """从主配置文件加载 agent 配置"""
        if not config_path:
            config_path = os.path.join(os.getcwd(), "config.json")

        try:
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    main_config = json.load(f)
                agent_data = main_config.get("agent", {})
                return cls.from_dict(agent_data)
        except Exception as e:
            logger.error(f"[AgentConfig] 加载配置失败: {e}")

        return cls()

    def save_to_file(self, config_path: str = ""):
        """保存 agent 配置到主配置文件"""
        if not config_path:
            config_path = os.path.join(os.getcwd(), "config.json")

        try:
            # 读取现有配置，只更新 agent 部分
            main_config = {}
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    main_config = json.load(f)

            # 加密存储 API Key（敏感信息）
            providers_safe = {}
            for pid, pc in self.providers.items():
                providers_safe[pid] = {
                    "enabled": pc.enabled,
                    "api_key": pc.api_key,  # 由主配置的 secure_storage 负责加密
                    "api_url": pc.api_url,
                    "default_model": pc.default_model,
                    "custom_models": pc.custom_models,
                }

            main_config["agent"] = {
                "active_provider": self.active_provider,
                "active_models": self.active_models,
                "providers": providers_safe,
                "permissions": self.permissions,
                "max_iterations": self.max_iterations,
                "stream_enabled": self.stream_enabled,
                "compact_auto": self.compact_auto,
            }

            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(main_config, f, ensure_ascii=False, indent=2)

        except Exception as e:
            logger.error(f"[AgentConfig] 保存配置失败: {e}")


# 全局 AgentConfig 实例
_agent_config: Optional[AgentConfig] = None


def get_agent_config() -> AgentConfig:
    """获取全局 Agent 配置"""
    global _agent_config
    if _agent_config is None:
        _agent_config = AgentConfig.load_from_file()
    return _agent_config


def init_agent_config(config_path: str = ""):
    """初始化 Agent 配置"""
    global _agent_config
    _agent_config = AgentConfig.load_from_file(config_path)


def save_agent_config(config_path: str = ""):
    """保存 Agent 配置"""
    get_agent_config().save_to_file(config_path)
