"""Skill 工具 - AI 加载技能文件"""

from typing import Dict, Callable
from logzero import logger

from ui.agent.tools.base import ToolInfo, CATEGORY_SYSTEM
from ui.agent.skill import get_skill_by_name, load_all_skills


def _build_skill_tool() -> ToolInfo:
    return ToolInfo(
        name="skill",
        display_name="加载技能",
        description="加载一个专用技能文件，将技能指令和资源注入当前对话。当任务与可用技能列表中某项匹配时使用。技能名称必须与系统上下文中列出的可用技能完全匹配。",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "要加载的技能名称（必须与可用技能列表中的名称完全匹配）",
                },
            },
            "required": ["name"],
        },
        category=CATEGORY_SYSTEM,
        execute=_execute_skill,
        permission_action="skill",
    )


def _execute_skill(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """执行 Skill 工具 - 加载技能"""
    name = params.get("name", "").strip()
    if not name:
        return "错误: 缺少 name 参数"

    skill = get_skill_by_name(name)
    if skill is None:
        available = load_all_skills()
        if available:
            names = ", ".join(s.name for s in available)
            return f"未找到技能 '{name}'。可用技能: {names}"
        return f"未找到技能 '{name}'，也没有其他可用技能"

    logger.info(f"[Skill] 加载技能: {name}")
    return skill.to_prompt_text()
