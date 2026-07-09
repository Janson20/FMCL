"""AGENT 智能助手模块"""

from ui.agent.agent_chat import AgentChatView
from ui.agent.agent_mixin import AgentMixin
from ui.agent.models import ModelInfo, get_default_model, get_model_catalog, get_models_by_provider
from ui.agent.provider import BaseProvider
from ui.agent.providers.anthropic import AnthropicProvider
from ui.agent.providers.custom import CustomProvider
from ui.agent.providers.jingdu import JingduProvider
from ui.agent.providers.openai import OpenAIProvider
from ui.agent.tool_registry import ToolRegistry, get_registry, get_tool_definitions
from ui.agent.tools.system import DANGEROUS_MARKER, execute_dangerous_command
from ui.agent.tools.user import ASK_USER_MARKER

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
