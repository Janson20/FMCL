"""工具注册表 - 所有工具的中央注册、查找和执行

替代旧的 tools.py 中的 get_tool_definitions() 和 engine.py 中的 execute_tool()。
使用注册表模式，一处定义全局生效。
"""

import json
from typing import Dict, List, Optional, Callable, Any
from logzero import logger

from ui.agent.tools.base import ToolInfo, ToolResult
from ui.agent.tools import get_all_builtin_tools
from ui.agent.tools.system import DANGEROUS_MARKER, execute_dangerous_command
from ui.agent.tools.user import ASK_USER_MARKER


class ToolRegistry:
    """工具注册表 - 单例模式"""

    _instance: Optional["ToolRegistry"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tools: Dict[str, ToolInfo] = {}
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if not self._initialized:
            self._init_builtins()
            self._initialized = True

    def _init_builtins(self):
        """注册所有内置工具"""
        builtins = get_all_builtin_tools()
        for tool in builtins:
            self.register(tool)
        logger.info(f"[ToolRegistry] 已注册 {len(builtins)} 个内置工具")

    def register(self, tool: ToolInfo):
        """注册一个工具"""
        if tool.name in self._tools:
            logger.warning(f"[ToolRegistry] 工具 '{tool.name}' 已存在，将被覆盖")
        self._tools[tool.name] = tool

    def unregister(self, name: str):
        """注销一个工具"""
        self._tools.pop(name, None)

    def get(self, name: str) -> Optional[ToolInfo]:
        """获取指定工具"""
        return self._tools.get(name)

    def get_all(self) -> List[ToolInfo]:
        """获取所有已注册工具"""
        return list(self._tools.values())

    def get_by_category(self, category: str) -> List[ToolInfo]:
        """按分类获取工具"""
        return [t for t in self._tools.values() if t.category == category]

    def get_definitions(self) -> List[dict]:
        """获取 OpenAI function-calling 格式的工具定义列表"""
        return [t.to_openai_function() for t in self._tools.values()]

    def get_tool_names(self) -> List[str]:
        """获取所有工具名称列表"""
        return sorted(self._tools.keys())

    def execute(self, name: str, params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
        """执行指定工具

        Args:
            name: 工具名称
            params: 工具参数
            callbacks: 回调函数字典

        Returns:
            工具执行结果文本
        """
        tool = self._tools.get(name)
        if tool is None:
            return f"错误: 未知工具 '{name}'"

        try:
            logger.info(f"[ToolRegistry] 执行工具: {name}, 参数: {params}")
        except Exception:
            pass

        try:
            result_text = tool.execute(params, callbacks)
            try:
                logger.info(f"[ToolRegistry] 工具 '{name}' 执行结果 (前300字): {result_text[:300]}")
            except Exception:
                pass  # 日志写入失败不影响执行流程
            return result_text
        except Exception as e:
            try:
                logger.error(f"[ToolRegistry] 工具 '{name}' 执行异常: {e}", exc_info=True)
            except Exception:
                pass
            return f"❌ 工具 '{name}' 执行失败: {str(e)}"

    def has_tool(self, name: str) -> bool:
        """检查工具是否存在"""
        return name in self._tools

    def execute_with_result(self, name: str, params: Dict[str, str], callbacks: Dict[str, Callable]) -> ToolResult:
        """执行工具并返回结构化结果

        Returns:
            ToolResult 对象，包含成功状态和可能的用户确认需求
        """
        result_text = self.execute(name, params, callbacks)

        # 检查是否需要用户确认
        if result_text.startswith(DANGEROUS_MARKER):
            parts = result_text.split("|", 2)
            if len(parts) >= 3:
                return ToolResult(
                    success=False,
                    text=result_text,
                    needs_user_confirm="dangerous_command",
                    confirm_data={"path": parts[1], "command": parts[2]},
                )

        if result_text.startswith(ASK_USER_MARKER):
            rest = result_text[len(ASK_USER_MARKER) + 1:]
            try:
                # 新版格式: __ASK_USER__|questions_json
                questions = json.loads(rest)
                if isinstance(questions, list):
                    return ToolResult(
                        success=False,
                        text=result_text,
                        needs_user_confirm="ask_user",
                        confirm_data={"questions": questions},
                    )
            except json.JSONDecodeError:
                pass
            # 旧版格式: __ASK_USER__|question|options_json
            parts = rest.split("|", 1)
            question = parts[0]
            options = []
            try:
                if len(parts) > 1:
                    options = json.loads(parts[1])
            except json.JSONDecodeError:
                pass
            return ToolResult(
                success=False,
                text=result_text,
                needs_user_confirm="ask_user",
                confirm_data={
                    "questions": [{
                        "question": question,
                        "header": "选择",
                        "options": [{"label": o, "description": o} for o in options] if options else [],
                        "multiSelect": False,
                        "custom": True,
                    }],
                },
            )

        # 判断是否成功
        is_success = not result_text.startswith("❌") and not result_text.startswith("错误")
        return ToolResult(
            success=is_success,
            text=result_text,
        )


# 便捷访问
def get_registry() -> ToolRegistry:
    return ToolRegistry()


def execute_tool(name: str, params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """便捷函数：执行工具（兼容旧接口）"""
    return get_registry().execute(name, params, callbacks)


def get_tool_definitions() -> List[dict]:
    """便捷函数：获取工具定义列表（兼容旧接口）"""
    return get_registry().get_definitions()
