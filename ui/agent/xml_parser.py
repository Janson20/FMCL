"""XML 响应解析 - 解析 AI 的 XML 格式回复"""

import re
from typing import Dict, List, Optional, Any
from logzero import logger


class ParsedResponse:
    """解析后的 AI 响应"""

    def __init__(self):
        self.thinking: str = ""
        self.message: str = ""
        self.action_type: Optional[str] = None  # tool_call / await_choice / complete
        self.tool_name: Optional[str] = None
        self.tool_params: Dict[str, str] = {}
        self.options: List[Dict[str, str]] = []
        self.raw: str = ""

    @classmethod
    def parse(cls, xml_text: str) -> "ParsedResponse":
        """解析 XML 格式的 AI 响应"""
        result = cls()
        result.raw = xml_text

        thinking_match = re.search(r"<thinking>(.*?)</thinking>", xml_text, re.DOTALL)
        if thinking_match:
            result.thinking = thinking_match.group(1).strip()

        message_match = re.search(r"<message>(.*?)</message>", xml_text, re.DOTALL)
        if message_match:
            result.message = message_match.group(1).strip()

        action_match = re.search(r'<action type="([^"]+)"', xml_text)
        if action_match:
            result.action_type = action_match.group(1)

        tool_match = re.search(r"<tool>(.*?)</tool>", xml_text, re.DOTALL)
        if tool_match:
            result.tool_name = tool_match.group(1).strip()

        param_matches = re.finditer(
            r'<(?:param|parameter)\s+name="([^"]+)"[^>]*>(.*?)</(?:param|parameter)>', xml_text, re.DOTALL
        )
        for m in param_matches:
            result.tool_params[m.group(1).strip()] = m.group(2).strip()

        if not result.tool_params and result.tool_name:
            result.tool_params = cls._fallback_parse_params(xml_text, result.tool_name)

        option_matches = re.finditer(
            r'<option value="([^"]+)"[^>]*>(.*?)</option>', xml_text, re.DOTALL
        )
        for m in option_matches:
            result.options.append({
                "value": m.group(1).strip(),
                "label": m.group(2).strip(),
            })

        return result

    @staticmethod
    def _fallback_parse_params(xml_text: str, tool_name: str) -> Dict[str, str]:
        params = {}
        params_block_match = re.search(r"<params>(.*?)</params>", xml_text, re.DOTALL)
        if not params_block_match:
            return params
        params_block = params_block_match.group(1)

        known_param_names = {
            "exec_command": ["command", "path"],
            "install_version": ["version_id", "mod_loader"],
            "launch_game": ["version_id"],
            "search_mods": ["query", "game_version", "mod_loader"],
            "install_mod": ["version_id", "mod_loader", "mod_name", "mod_project_id"],
            "delete_version": ["version_id"],
            "start_server": ["version_id", "max_memory"],
            "delete_server_version": ["version_id"],
            "install_modpack": ["mrpack_path"],
            "install_modpack_server": ["mrpack_path"],
            "search_modpack": ["query", "game_version"],
            "download_modpack": ["project_id", "game_version"],
            "search_resource_packs": ["query", "game_version"],
            "install_resource_pack": ["version_id", "pack_name", "project_id"],
            "search_shaders": ["query", "game_version"],
            "install_shader": ["version_id", "shader_name", "project_id"],
            "list_version_resources": ["version_id", "resource_type"],
        }

        candidate_names = known_param_names.get(tool_name, [])
        if not candidate_names:
            candidate_names = ["command", "path"]

        for name in candidate_names:
            m = re.search(
                rf"<{name}>(.*?)</{name}>", params_block, re.DOTALL
            )
            if m:
                params[name] = m.group(1).strip()

        if params:
            logger.info(f"[XML Parser] 通过降级匹配解析到参数 (tool={tool_name}): {list(params.keys())}")
        else:
            logger.warning(f"[XML Parser] 无法解析参数, tool={tool_name}, params_block前200字: {params_block[:200]}")

        return params

    def has_action(self) -> bool:
        return self.action_type is not None

    def is_tool_call(self) -> bool:
        return self.action_type == "tool_call"

    def is_await_choice(self) -> bool:
        return self.action_type == "await_choice"

    def is_complete(self) -> bool:
        return self.action_type == "complete"
