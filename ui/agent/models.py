"""AI 模型目录 - 定义所有可用模型及其能力元数据

净读 AI 模型排在最前面，作为首选提供商。
"""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ModelInfo:
    """单个 AI 模型的元数据"""
    id: str                          # API 模型标识，如 "deepseek-v4-flash", "gpt-4o"
    provider_id: str                 # 所属提供商 ID，如 "jingdu", "openai", "anthropic"
    name: str                        # 用户友好的显示名称
    description: str = ""            # 简短描述
    supports_tools: bool = True      # 是否支持 Function Calling
    supports_stream: bool = True     # 是否支持 SSE 流式输出
    supports_reasoning: bool = False # 是否支持思维链/推理过程
    thinking_default: bool = True    # DeepSeek: 默认是否开启思考模式
    context_limit: int = 128000      # 上下文 Token 上限
    max_output: int = 8192           # 最大输出 Token 数
    cost_input_per_1m: float = 0.0   # 每百万输入 Token 价格($)
    cost_output_per_1m: float = 0.0  # 每百万输出 Token 价格($)
    status: str = "active"           # "active" | "beta" | "deprecated"
    requires_custom_url: bool = False # 是否需要用户配置自定义 URL
    api_url: str = ""                # API 端点 URL（提供商层面配置，此为覆盖值）


def _build_catalog() -> List[ModelInfo]:
    """构建所有可用模型的目录（净读 AI 排首位）"""
    models: List[ModelInfo] = []

    # ============ 净读 AI（DeepSeek V4，默认首选）============

    # DeepSeek-V4-Flash（主力模型，非思考模式）
    models.append(ModelInfo(
        id="deepseek-v4-flash",
        provider_id="jingdu",
        name="DeepSeek V4 Flash",
        description="最新旗舰轻量模型，1M 上下文，384K 输出，默认非思考",
        supports_tools=True,
        supports_stream=True,
        supports_reasoning=True,
        thinking_default=False,
        context_limit=1048576,
        max_output=393216,
        cost_input_per_1m=0.27,
        cost_output_per_1m=1.10,
        status="active",
    ))
    # DeepSeek-V4-Pro（推理增强，思考模式默认开启）
    models.append(ModelInfo(
        id="deepseek-v4-pro",
        provider_id="jingdu",
        name="DeepSeek V4 Pro",
        description="旗舰推理模型，1M 上下文，384K 输出，默认思考模式",
        supports_tools=True,
        supports_stream=True,
        supports_reasoning=True,
        thinking_default=True,
        context_limit=1048576,
        max_output=393216,
        cost_input_per_1m=0.55,
        cost_output_per_1m=2.19,
        status="active",
    ))

    # 旧版兼容（2026/07/24 弃用）
    models.append(ModelInfo(
        id="deepseek-chat",
        provider_id="jingdu",
        name="DeepSeek V3 (旧版)",
        description="将于 2026/07/24 弃用，请使用 V4 Flash 非思考模式",
        supports_tools=True,
        supports_stream=True,
        supports_reasoning=False,
        thinking_default=False,
        context_limit=128000,
        max_output=8192,
        cost_input_per_1m=0.27,
        cost_output_per_1m=1.10,
        status="deprecated",
    ))
    models.append(ModelInfo(
        id="deepseek-reasoner",
        provider_id="jingdu",
        name="DeepSeek R1 (旧版)",
        description="将于 2026/07/24 弃用，请使用 V4 Pro",
        supports_tools=True,
        supports_stream=True,
        supports_reasoning=True,
        thinking_default=True,
        context_limit=128000,
        max_output=8192,
        cost_input_per_1m=0.55,
        cost_output_per_1m=2.19,
        status="deprecated",
    ))

    # ============ OpenAI ============
    models.append(ModelInfo(
        id="gpt-4o",
        provider_id="openai",
        name="GPT-4o",
        description="OpenAI 最新旗舰多模态模型",
        supports_tools=True,
        supports_stream=True,
        supports_reasoning=False,
        context_limit=128000,
        max_output=16384,
        cost_input_per_1m=2.50,
        cost_output_per_1m=10.00,
        status="active",
    ))
    models.append(ModelInfo(
        id="gpt-4o-mini",
        provider_id="openai",
        name="GPT-4o Mini",
        description="轻量快速，成本极低",
        supports_tools=True,
        supports_stream=True,
        supports_reasoning=False,
        context_limit=128000,
        max_output=16384,
        cost_input_per_1m=0.15,
        cost_output_per_1m=0.60,
        status="active",
    ))
    models.append(ModelInfo(
        id="o3-mini",
        provider_id="openai",
        name="o3 Mini",
        description="推理模型，擅长数学与逻辑",
        supports_tools=True,
        supports_stream=True,
        supports_reasoning=True,
        context_limit=200000,
        max_output=100000,
        cost_input_per_1m=1.10,
        cost_output_per_1m=4.40,
        status="active",
    ))

    # ============ Anthropic ============
    models.append(ModelInfo(
        id="claude-sonnet-4-20250514",
        provider_id="anthropic",
        name="Claude Sonnet 4",
        description="Anthropic 最新高性能模型",
        supports_tools=True,
        supports_stream=True,
        supports_reasoning=False,
        context_limit=200000,
        max_output=8192,
        cost_input_per_1m=3.00,
        cost_output_per_1m=15.00,
        status="active",
    ))
    models.append(ModelInfo(
        id="claude-3-5-haiku-20241022",
        provider_id="anthropic",
        name="Claude 3.5 Haiku",
        description="快速轻量的 Claude 模型",
        supports_tools=True,
        supports_stream=True,
        supports_reasoning=False,
        context_limit=200000,
        max_output=8192,
        cost_input_per_1m=0.80,
        cost_output_per_1m=4.00,
        status="active",
    ))

    return models


# 单例模式，避免重复构建
_MODEL_CATALOG: Optional[List[ModelInfo]] = None


def get_model_catalog() -> List[ModelInfo]:
    """获取完整模型目录"""
    global _MODEL_CATALOG
    if _MODEL_CATALOG is None:
        _MODEL_CATALOG = _build_catalog()
    return _MODEL_CATALOG


def get_models_by_provider(provider_id: str) -> List[ModelInfo]:
    """按提供商筛选模型"""
    return [m for m in get_model_catalog() if m.provider_id == provider_id]


def get_model_by_id(model_id: str) -> Optional[ModelInfo]:
    """根据模型 ID 查找模型信息"""
    for m in get_model_catalog():
        if m.id == model_id:
            return m
    return None


def get_default_model(provider_id: str = "jingdu") -> Optional[ModelInfo]:
    """获取指定提供商的默认模型（第一个 active 模型）"""
    models = get_models_by_provider(provider_id)
    for m in models:
        if m.status == "active":
            return m
    return models[0] if models else None


def get_provider_names() -> List[dict]:
    """获取所有提供商的基本信息（供 UI 选择器使用）"""
    providers = {}
    for m in get_model_catalog():
        if m.provider_id not in providers:
            providers[m.provider_id] = {
                "id": m.provider_id,
                "name": _get_provider_display_name(m.provider_id),
                "models": [],
            }
        providers[m.provider_id]["models"].append(m)
    # 净读排首位
    ordering = ["jingdu", "openai", "anthropic"]
    result = []
    for pid in ordering:
        if pid in providers:
            result.append(providers.pop(pid))
    for pid, info in providers.items():
        result.append(info)
    return result


def _get_provider_display_name(provider_id: str) -> str:
    _map = {
        "jingdu": "净读 AI",
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "custom": "自定义",
    }
    return _map.get(provider_id, provider_id)
