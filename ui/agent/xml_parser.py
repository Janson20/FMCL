"""XML 响应解析 - 解析 AI 的 XML 格式回复"""

import re
from typing import Dict, List, Optional, Any


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

        option_matches = re.finditer(
            r'<option value="([^"]+)"[^>]*>(.*?)</option>', xml_text, re.DOTALL
        )
        for m in option_matches:
            result.options.append({
                "value": m.group(1).strip(),
                "label": m.group(2).strip(),
            })

        return result

    def has_action(self) -> bool:
        return self.action_type is not None

    def is_tool_call(self) -> bool:
        return self.action_type == "tool_call"

    def is_await_choice(self) -> bool:
        return self.action_type == "await_choice"

    def is_complete(self) -> bool:
        return self.action_type == "complete"
