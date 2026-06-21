"""AGENT 工具集 - 汇总注册所有工具"""

from typing import List

from ui.agent.tools.base import ToolInfo
from ui.agent.tools.versions import _build_version_tools
from ui.agent.tools.mods import _build_mod_tools
from ui.agent.tools.server import _build_server_tools
from ui.agent.tools.modpack import _build_modpack_tools
from ui.agent.tools.resources import _build_resource_tools
from ui.agent.tools.system import _build_system_tools
from ui.agent.tools.user import _build_user_tools
from ui.agent.tools.web_search import _build_web_search_tool
from ui.agent.tools.web_fetch import _build_web_fetch_tool
from ui.agent.tools.todo_write import _build_todo_write_tool
from ui.agent.tools.skill import _build_skill_tool
from ui.agent.tools.files import _build_file_tools


def get_all_builtin_tools() -> List[ToolInfo]:
    """获取所有内置工具"""
    tools: List[ToolInfo] = []
    tools.extend(_build_version_tools())
    tools.extend(_build_mod_tools())
    tools.extend(_build_server_tools())
    tools.extend(_build_modpack_tools())
    tools.extend(_build_resource_tools())
    tools.extend(_build_system_tools())
    tools.extend(_build_file_tools())
    tools.extend(_build_user_tools())
    tools.append(_build_web_search_tool())
    tools.append(_build_web_fetch_tool())
    tools.append(_build_todo_write_tool())
    tools.append(_build_skill_tool())
    return tools
