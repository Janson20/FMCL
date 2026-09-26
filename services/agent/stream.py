"""SSE 流式事件解析器 - 通用 Server-Sent Events 解析

支持 OpenAI/DeepSeek/Anthropic 的流式响应格式，
统一为 FMCL 内部的 SSEEvent 事件类型。
"""

import json
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, Generator, List, Optional

from logzero import logger


class SSEEventType(Enum):
    """流式事件类型"""

    TEXT_DELTA = auto()  # 文本增量
    THINKING_DELTA = auto()  # 思考过程增量 (DeepSeek R1)
    THINKING_DONE = auto()  # 思考结束
    TOOL_CALL_START = auto()  # 工具调用开始 (带 tool_call_id)
    TOOL_CALL_NAME = auto()  # 工具名称确定
    TOOL_CALL_ARGS = auto()  # 工具参数增量
    TOOL_CALL_END = auto()  # 工具调用结束
    USAGE = auto()  # Token 用量统计
    ERROR = auto()  # 错误
    DONE = auto()  # 流结束
    ABORT = auto()  # 被取消


@dataclass
class SSEEvent:
    """统一的流式事件数据类"""

    type: SSEEventType
    text: str = ""
    tool_call_id: str = ""
    tool_name: str = ""
    tool_args: str = ""
    usage: Optional[dict] = None
    finish_reason: str = ""
    error_message: str = ""


class SSEParser:
    """SSE 流式数据解析器

    支持三种模式的流式输出：
    1. OpenAI 兼容 API (data: {"choices":[{"delta":{"content":"..."}}]})
    2. OpenAI 兼容 API 带 reasoning_content (DeepSeek R1)
    3. OpenAI 兼容 API 带 tool_calls (Function Calling)
    """

    def __init__(self):
        self._buffer = ""
        self._current_tool_calls: Dict[int, dict] = {}
        self._thinking_accumulated = ""

    def feed_line(self, line: str) -> Optional[SSEEvent]:
        """喂入一行 SSE 数据，返回解析出的事件（可能为 None）"""
        if line.startswith("data: "):
            data_str = line[6:]
            if data_str.strip() == "[DONE]":
                return SSEEvent(type=SSEEventType.DONE, text="")

            try:
                data = json.loads(data_str)
                return self._parse_chunk(data)
            except json.JSONDecodeError:
                logger.debug(f"[SSE] JSON 解析失败: {data_str[:100]}")
                return None
        return None

    def feed_bytes(self, data: bytes) -> List[SSEEvent]:
        """喂入字节数据，返回所有解析出的事件"""
        events: List[SSEEvent] = []
        text = data.decode("utf-8", errors="replace")
        self._buffer += text

        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if line:
                event = self.feed_line(line)
                if event:
                    events.append(event)
        return events

    def _parse_chunk(self, chunk: dict) -> Optional[SSEEvent]:
        """解析单个 JSON chunk"""
        choices = chunk.get("choices", [])
        if not choices:
            # 可能是 usage 信息
            if "usage" in chunk:
                return SSEEvent(type=SSEEventType.USAGE, usage=chunk["usage"])
            return None

        choice = choices[0]
        delta = choice.get("delta", {})
        finish_reason = choice.get("finish_reason") or ""

        # 思考内容 (DeepSeek R1 reasoning_content)
        reasoning = delta.get("reasoning_content", "")
        if reasoning:
            return SSEEvent(type=SSEEventType.THINKING_DELTA, text=reasoning)

        # 思考结束后标记
        if finish_reason == "stop" and self._thinking_accumulated:
            self._thinking_accumulated = ""
            # 不发单独结束事件，用 DONE 即可

        # 工具调用
        tool_calls = delta.get("tool_calls", [])
        if tool_calls:
            return self._parse_tool_call_chunk(tool_calls, finish_reason)

        # 普通文本增量
        content = delta.get("content", "")
        if content:
            if finish_reason:
                return SSEEvent(type=SSEEventType.TEXT_DELTA, text=content, finish_reason=finish_reason)
            return SSEEvent(type=SSEEventType.TEXT_DELTA, text=content)

        # 仅有 finish_reason 无内容
        if finish_reason and finish_reason != "stop":
            return SSEEvent(type=SSEEventType.DONE, finish_reason=finish_reason)

        return None

    def _parse_tool_call_chunk(self, tool_calls: list, finish_reason: str) -> Optional[SSEEvent]:
        """解析工具调用增量"""
        for tc in tool_calls:
            index = tc.get("index", 0)
            tc_id = tc.get("id", "")
            func = tc.get("function", {})
            name = func.get("name", "")
            arguments = func.get("arguments", "")

            if index not in self._current_tool_calls:
                self._current_tool_calls[index] = {"id": tc_id or "", "name": name or "", "arguments": ""}
                if tc_id:
                    return SSEEvent(type=SSEEventType.TOOL_CALL_START, tool_call_id=tc_id)

            current = self._current_tool_calls[index]
            if tc_id and current["id"] != tc_id:
                current["id"] = tc_id
            if name and current["name"] != name:
                current["name"] = name
                return SSEEvent(type=SSEEventType.TOOL_CALL_NAME, tool_call_id=current["id"], tool_name=name)
            if arguments:
                current["arguments"] += arguments
                return SSEEvent(type=SSEEventType.TOOL_CALL_ARGS, tool_call_id=current["id"], tool_args=arguments)

        return None

    def get_completed_tool_calls(self) -> List[dict]:
        """获取已完成的工具调用列表，并清空内部状态"""
        result = []
        for idx in sorted(self._current_tool_calls.keys()):
            tc = self._current_tool_calls[idx]
            if tc["id"] and tc["name"]:
                try:
                    args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                except json.JSONDecodeError:
                    args = {}
                result.append(
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": json.dumps(args, ensure_ascii=False)},
                    }
                )
        self._current_tool_calls.clear()
        return result

    def reset(self):
        """重置解析器状态"""
        self._buffer = ""
        self._current_tool_calls.clear()
        self._thinking_accumulated = ""


# ---------- 高级流式接口 ----------


def parse_event_stream(
    response,
    on_text: callable = None,
    on_thinking: callable = None,
    on_tool_call_start: callable = None,
    on_tool_call_name: callable = None,
    on_tool_call_args: callable = None,
    on_tool_call_end: callable = None,
    on_usage: callable = None,
    on_error: callable = None,
    on_done: callable = None,
) -> List[dict]:
    """解析 SSE 响应流并回调

    Args:
        response: urllib / requests 的响应对象（需支持 iter_lines）
        on_*: 各类事件的回调函数

    Returns:
        完成的工具调用列表
    """
    parser = SSEParser()
    accumulated_text = ""

    try:
        for line in response:
            line_str = ""
            if isinstance(line, bytes):
                line_str = line.decode("utf-8", errors="replace").strip()
            else:
                line_str = str(line).strip()

            if not line_str:
                continue
            if not line_str.startswith("data: "):
                continue

            event = parser.feed_line(line_str)
            if event is None:
                continue

            if event.type == SSEEventType.TEXT_DELTA:
                accumulated_text += event.text
                if on_text:
                    on_text(event.text)
            elif event.type == SSEEventType.THINKING_DELTA:
                if on_thinking:
                    on_thinking(event.text)
            elif event.type == SSEEventType.TOOL_CALL_START:
                if on_tool_call_start:
                    on_tool_call_start(event.tool_call_id)
            elif event.type == SSEEventType.TOOL_CALL_NAME:
                if on_tool_call_name:
                    on_tool_call_name(event.tool_call_id, event.tool_name)
            elif event.type == SSEEventType.TOOL_CALL_ARGS:
                if on_tool_call_args:
                    on_tool_call_args(event.tool_call_id, event.tool_args)
            elif event.type == SSEEventType.USAGE:
                if on_usage:
                    on_usage(event.usage)
            elif event.type == SSEEventType.ERROR:
                if on_error:
                    on_error(event.error_message)
            elif event.type == SSEEventType.DONE:
                if on_done:
                    on_done(accumulated_text)

    except Exception as e:
        logger.error(f"[SSE] 流式解析异常: {e}")
        if on_error:
            on_error(str(e))

    completed_tools = parser.get_completed_tool_calls()
    if completed_tools and on_tool_call_end:
        for tc in completed_tools:
            on_tool_call_end(tc)

    return completed_tools
