"""AGENT 智能助手模块

任务 1.14 后本模块只做**导出面的兼容**：业务实现（模型目录 / Provider /
工具注册表 / 标记常量）已搬到 ``services/agent/``，这里改为直接从服务层取；
只有真正依赖 Tk 的两样（``AgentChatView`` / ``AgentMixin``）仍留在 ``ui/agent/``。

``__all__`` 与搬家前逐字一致 —— 外部 ``from ui.agent import X`` 的写法不受影响。
"""

from services.agent.models import ModelInfo, get_default_model, get_model_catalog, get_models_by_provider
from services.agent.provider import BaseProvider
from services.agent.providers.anthropic import AnthropicProvider
from services.agent.providers.custom import CustomProvider
from services.agent.providers.jingdu import JingduProvider
from services.agent.providers.openai import OpenAIProvider
from services.agent.tool_registry import ToolRegistry, get_registry, get_tool_definitions
from services.agent.tools.system import DANGEROUS_MARKER, execute_dangerous_command
from services.agent.tools.user import ASK_USER_MARKER
from ui.agent.agent_chat import AgentChatView
from ui.agent.agent_mixin import AgentMixin

__all__ = [
    "ModelInfo",
    "get_model_catalog",
    "get_models_by_provider",
    "get_default_model",
    "BaseProvider",
    "JingduProvider",
    "OpenAIProvider",
    "AnthropicProvider",
    "CustomProvider",
    "AgentChatView",
    "AgentMixin",
    "ToolRegistry",
    "get_registry",
    "get_tool_definitions",
    "execute_dangerous_command",
    "DANGEROUS_MARKER",
    "ASK_USER_MARKER",
]
