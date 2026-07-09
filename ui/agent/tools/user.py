"""用户交互工具 - AskUser（增强版）

参考 opencode QuestionTool 设计：
- 支持 1-4 个问题（questions 数组）
- 每个问题：question + header + options + custom + multiSelect
- 每个选项：label + description
- 支持 "(Recommended)" 标签
- 支持自定义答案
"""

import json
from typing import Callable, Dict, List, Optional

from logzero import logger

from ui.agent.tools.base import CATEGORY_USER, ToolInfo

ASK_USER_MARKER = "__ASK_USER__"


def _build_user_tools() -> list:
    return [
        ToolInfo(
            name="ask_user",
            display_name="向用户提问",
            description="""使用此工具向用户提问。适用场景：
1. 需要用户选择、确认或补充信息
2. 遇到多个可能选项需要用户决策
3. 需要澄清模糊的指令

使用说明：
- 每个问题可指定 2-4 个选项，每个选项包含 label（简短标签）和 description（说明）
- 设置 multiSelect=true 允许多选
- 设置 custom=true（默认开启）允许用户输入自定义答案
- 推荐某个选项时，将其放在列表首位并加 "(Recommended)" 后缀
- 最多支持 4 个问题""",
            parameters={
                "type": "object",
                "properties": {
                    "questions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "question": {"type": "string", "description": "要向用户提出的问题，应清晰具体"},
                                "header": {
                                    "type": "string",
                                    "description": "简短的问题标签（最多 12 字符），用于 UI 显示",
                                },
                                "options": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "label": {
                                                "type": "string",
                                                "description": "选项的显示标签（1-5 词，简洁）。如果是推荐选项，在末尾加 ' (Recommended)'",
                                            },
                                            "description": {"type": "string", "description": "选项的详细说明"},
                                        },
                                        "required": ["label", "description"],
                                    },
                                    "description": "可选的候选项列表（2-4 个）",
                                },
                                "multiSelect": {"type": "boolean", "description": "是否允许选择多个选项（默认 false）"},
                                "custom": {"type": "boolean", "description": "是否允许用户输入自定义答案（默认 true）"},
                            },
                            "required": ["question", "header", "options"],
                        },
                        "description": "要问的问题列表（1-4 个问题）",
                        "minItems": 1,
                        "maxItems": 4,
                    }
                },
                "required": ["questions"],
            },
            category=CATEGORY_USER,
            execute=_ask_user,
            permission_action="ask_user",
        )
    ]


def _ask_user(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """增强版 ask_user - 支持多问题

    返回格式: __ASK_USER__|questions_json
    """
    questions = params.get("questions", [])
    if not questions:
        # 兼容旧版格式
        question = params.get("question", "").strip()
        if not question:
            return "错误: 缺少 question 参数"
        old_options = params.get("options", None)
        options_list = old_options if isinstance(old_options, list) else []
        if options_list:
            opts = [{"label": o, "description": o} for o in options_list]
        else:
            opts = []
        questions = [{"question": question, "header": "选择", "options": opts, "multiSelect": False, "custom": True}]

    # 验证问题数量
    if len(questions) > 4:
        questions = questions[:4]
    if len(questions) < 1:
        return "错误: 需要至少 1 个问题"

    questions_json = json.dumps(questions, ensure_ascii=False)
    return f"{ASK_USER_MARKER}|{questions_json}"


def get_legacy_ask_user(params: Dict[str, str]) -> str:
    """旧版 ask_user 兼容接口（单问题+字符串选项）"""
    question = params.get("question", "").strip()
    if not question:
        return "错误: 缺少 question 参数"

    options = params.get("options", None)
    options_json = json.dumps(options, ensure_ascii=False) if options else "[]"
    return f"{ASK_USER_MARKER}|{question}|{options_json}"
