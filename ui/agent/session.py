"""AGENT 会话管理 - 对话持久化 + Token 估算 + 自动 Compaction

存储到 ./data/agent/{session_id}.json
每个会话包含：消息历史、模型信息、时间戳。
"""

import json
import os
import uuid
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from logzero import logger

from ui.agent.models import ModelInfo

# 默认上下文缓冲区（Token）
DEFAULT_BUFFER_TOKENS = 20000
# 压缩后保留最近 N 轮对话
DEFAULT_KEEP_TURNS = 8
# 估算：1 Token ≈ 4 字符（中英文混合）
CHARS_PER_TOKEN = 4


def _ensure_data_dir() -> str:
    """确保数据目录存在"""
    base = os.path.join(os.getcwd(), "data", "agent")
    os.makedirs(base, exist_ok=True)
    return base


@dataclass
class AgentSession:
    """AGENT 会话"""

    id: str
    title: str = ""
    provider_id: str = "jingdu"
    model_id: str = "deepseek-v4-flash"
    messages: List[dict] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = self.created_at

    def add_message(self, msg: dict):
        """添加一条消息"""
        self.messages.append(msg)
        self.updated_at = time.time()

    def set_title(self, text: str, max_len: int = 50):
        """从用户消息中提取标题"""
        cleaned = text.replace("\n", " ").strip()
        if len(cleaned) > max_len:
            cleaned = cleaned[:max_len] + "..."
        self.title = cleaned

    def estimate_tokens(self) -> int:
        """估算总 Token 数（粗略：字符数/4）"""
        total_chars = 0
        for msg in self.messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            # tool_calls 也计入
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                total_chars += len(json.dumps(tool_calls, ensure_ascii=False))
        return max(1, total_chars // CHARS_PER_TOKEN)

    def compact(self, keep_turns: int = DEFAULT_KEEP_TURNS):
        """压缩旧消息，保留 system + 最近 N 轮对话

        "一轮" = user + assistant + 可能的 tool 调用结果
        """
        if not self.messages:
            return

        system_msgs = []
        other_msgs = []

        for msg in self.messages:
            if msg.get("role") == "system":
                system_msgs.append(msg)
            else:
                other_msgs.append(msg)

        if len(other_msgs) <= keep_turns * 2:
            return

        # 计算应保留的轮数
        # 从后往前找 N 个 user 消息
        user_indices = []
        for i, msg in enumerate(other_msgs):
            if msg.get("role") == "user":
                user_indices.append(i)

        if len(user_indices) <= keep_turns:
            return

        keep_from = user_indices[-keep_turns]
        kept_others = other_msgs[keep_from:]

        # 添加压缩摘要
        removed_count = len(other_msgs) - len(kept_others)
        summary = {
            "role": "system",
            "content": f"[上下文压缩] 已移除 {removed_count} 条早期对话消息，保留了最近 {keep_turns} 轮对话。之前已完成的任务信息如有需要请通过工具重新获取。",
        }

        self.messages = system_msgs + [summary] + kept_others
        logger.info(f"[Session] 压缩完成: 移除 {removed_count} 条消息, 当前 Token 约 {self.estimate_tokens()}")

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "id": self.id,
            "title": self.title,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "messages": self.messages,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AgentSession":
        """从字典反序列化"""
        return cls(
            id=data.get("id", ""),
            title=data.get("title", ""),
            provider_id=data.get("provider_id", "jingdu"),
            model_id=data.get("model_id", "deepseek-v4-flash"),
            messages=data.get("messages", []),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
        )

    def save(self):
        """保存到文件"""
        try:
            base = _ensure_data_dir()
            filepath = os.path.join(base, f"{self.id}.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info(f"[Session] 已保存: {self.id} ({len(self.messages)} 条消息)")
        except Exception as e:
            logger.error(f"[Session] 保存失败: {e}")

    @classmethod
    def load(cls, session_id: str) -> Optional["AgentSession"]:
        """从文件加载"""
        try:
            base = _ensure_data_dir()
            filepath = os.path.join(base, f"{session_id}.json")
            if not os.path.exists(filepath):
                return None
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls.from_dict(data)
        except Exception as e:
            logger.error(f"[Session] 加载失败: {e}")
            return None

    @classmethod
    def delete(cls, session_id: str):
        """删除会话文件"""
        try:
            base = _ensure_data_dir()
            filepath = os.path.join(base, f"{session_id}.json")
            if os.path.exists(filepath):
                os.remove(filepath)
            # 也删除关联的 todos
            todos_file = os.path.join(base, "todos", f"{session_id}.json")
            if os.path.exists(todos_file):
                os.remove(todos_file)
            logger.info(f"[Session] 已删除: {session_id}")
        except Exception as e:
            logger.error(f"[Session] 删除失败: {e}")

    @classmethod
    def list_all(cls) -> List[dict]:
        """列出所有会话摘要"""
        try:
            base = _ensure_data_dir()
            sessions = []
            for filename in os.listdir(base):
                if filename.endswith(".json") and filename != "config.json":
                    filepath = os.path.join(base, filename)
                    try:
                        with open(filepath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        sessions.append({
                            "id": data.get("id", filename[:-5]),
                            "title": data.get("title", "无标题"),
                            "provider_id": data.get("provider_id", ""),
                            "model_id": data.get("model_id", ""),
                            "message_count": len(data.get("messages", [])),
                            "updated_at": data.get("updated_at", 0),
                            "created_at": data.get("created_at", 0),
                        })
                    except Exception:
                        continue
            # 按更新时间倒序
            sessions.sort(key=lambda s: s["updated_at"], reverse=True)
            return sessions
        except Exception as e:
            logger.error(f"[Session] 列表加载失败: {e}")
            return []

    @classmethod
    def create_new(cls, provider_id: str = "jingdu", model_id: str = "deepseek-v4-flash", system_prompt: str = "") -> "AgentSession":
        """创建新会话"""
        session = cls(
            id=str(uuid.uuid4()),
            provider_id=provider_id,
            model_id=model_id,
        )
        if system_prompt:
            session.add_message({"role": "system", "content": system_prompt})
        return session
