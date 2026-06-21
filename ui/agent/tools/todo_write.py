"""TodoWrite 工具 - AI 管理任务列表

参考 opencode TodoWrite 设计：
AI 通过此工具创建和维护结构化任务列表，
用于跟踪多步骤工作进度。
"""

import json
import os
from typing import Dict, List, Optional, Callable
from logzero import logger

from ui.agent.tools.base import ToolInfo, CATEGORY_SYSTEM


def _build_todo_write_tool() -> ToolInfo:
    return ToolInfo(
        name="todo_write",
        display_name="任务列表管理",
        description="创建和管理结构化任务列表。用于跟踪多步骤工作的进度，确保所有步骤都已完成。使用此工具来：1) 创建初始任务计划，2) 标记任务为进行中，3) 标记任务为已完成",
        parameters={
            "type": "object",
            "properties": {
                "todos": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {
                                "type": "string",
                                "description": "任务唯一标识",
                            },
                            "content": {
                                "type": "string",
                                "description": "任务描述/内容",
                            },
                            "status": {
                                "type": "string",
                                "enum": ["pending", "in_progress", "completed"],
                                "description": "任务状态：pending=待开始, in_progress=进行中, completed=已完成",
                            },
                            "priority": {
                                "type": "string",
                                "enum": ["high", "medium", "low"],
                                "description": "任务优先级",
                            },
                        },
                        "required": ["content", "status", "id", "priority"],
                    },
                    "description": "更新后的完整任务列表",
                },
            },
            "required": ["todos"],
        },
        category=CATEGORY_SYSTEM,
        execute=_todo_write,
        permission_action="todo_write",
    )


def _todo_write(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """执行 TodoWrite - 更新并持久化任务列表"""
    todos = params.get("todos", [])
    if not isinstance(todos, list):
        return "错误: todos 参数必须是数组"

    # 验证每个 todo
    valid_statuses = {"pending", "in_progress", "completed"}
    valid_priorities = {"high", "medium", "low"}
    valid_todos = []
    for t in todos:
        if not isinstance(t, dict):
            continue
        content = t.get("content", "").strip()
        if not content:
            continue
        status = t.get("status", "pending")
        if status not in valid_statuses:
            status = "pending"
        priority = t.get("priority", "medium")
        if priority not in valid_priorities:
            priority = "medium"
        valid_todos.append({
            "id": t.get("id", f"todo_{len(valid_todos) + 1}"),
            "content": content,
            "status": status,
            "priority": priority,
        })

    # 获取会话 ID 用于持久化
    session_id = _get_session_id(callbacks)

    # 持久化到文件
    if session_id:
        _save_todos(session_id, valid_todos)

    # 构建返回文本
    if not valid_todos:
        return "✅ 任务列表已清空"

    lines = ["✅ 任务列表已更新:\n"]
    for t in valid_todos:
        status_icon = {"pending": "⬜", "in_progress": "🔄", "completed": "✅"}.get(t["status"], "⬜")
        priority_mark = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(t["priority"], "")
        lines.append(f"  {status_icon} {priority_mark} [{t['id']}] {t['content']}")

    return "\n".join(lines)


def _get_session_id(callbacks: Dict[str, Callable]) -> str:
    """从 callbacks 获取当前会话 ID"""
    if "get_current_session_id" in callbacks:
        return callbacks["get_current_session_id"]()
    return ""


def _get_todos_dir() -> str:
    """获取 todos 存储目录"""
    base = os.path.join(os.getcwd(), "data", "agent")
    todos_dir = os.path.join(base, "todos")
    os.makedirs(todos_dir, exist_ok=True)
    return todos_dir


def _save_todos(session_id: str, todos: List[dict]):
    """持久化任务列表"""
    try:
        todos_dir = _get_todos_dir()
        filepath = os.path.join(todos_dir, f"{session_id}.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(todos, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存 todos 失败: {e}")


def load_todos(session_id: str) -> List[dict]:
    """加载任务列表"""
    try:
        todos_dir = _get_todos_dir()
        filepath = os.path.join(todos_dir, f"{session_id}.json")
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"加载 todos 失败: {e}")
    return []
