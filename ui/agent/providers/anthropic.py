"""Anthropic 提供商 - 封装 Anthropic Messages API

Anthropic 的 API 格式与 OpenAI 不同，需做格式转换：
- system prompt 为独立参数，不放在 messages 中
- tools 格式不同
- 响应中 tool_use 格式不同

内部统一为 OpenAI 兼容格式输出。
"""

import json
import urllib.error
import urllib.request
from typing import Dict, Generator, List, Optional

from logzero import logger

ANTHROPIC_DEFAULT_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_DEFAULT_MODEL = "claude-sonnet-4-20250514"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    """Anthropic Claude 提供商

    注意：不继承 BaseProvider，因为 Anthropic API 格式完全不同。
    但对外暴露统一的 chat() 和 stream_chat() 接口。
    """

    provider_id = "anthropic"
    provider_name = "Anthropic"
    default_api_url = ANTHROPIC_DEFAULT_API_URL

    def __init__(
        self, api_key: str, api_url: str = "", timeout: int = 120, extra_headers: Optional[Dict[str, str]] = None
    ):
        self.api_key = api_key
        self.api_url = api_url or ANTHROPIC_DEFAULT_API_URL
        self.timeout = timeout
        self.extra_headers = extra_headers or {}

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "User-Agent": "FMCL/2.0 (Minecraft Launcher; agent)",
            **self.extra_headers,
        }

    def _convert_messages(self, messages: List[Dict]) -> tuple:
        """将 OpenAI 格式消息转为 Anthropic 格式

        Returns:
            (system_text, anthropic_messages)
        """
        system_text = ""
        anthropic_msgs = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "system":
                if system_text:
                    system_text += "\n\n"
                system_text += content
                continue

            if role == "user":
                anthropic_msgs.append({"role": "user", "content": content or ""})
            elif role == "assistant":
                # 处理 assistant 消息（可能包含 tool_calls）
                tool_calls = msg.get("tool_calls", [])
                if tool_calls:
                    parts = []
                    if content:
                        parts.append({"type": "text", "text": content})
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        parts.append(
                            {
                                "type": "tool_use",
                                "id": tc.get("id", ""),
                                "name": func.get("name", ""),
                                "input": (
                                    json.loads(func.get("arguments", "{}"))
                                    if isinstance(func.get("arguments"), str)
                                    else func.get("arguments", {})
                                ),
                            }
                        )
                    anthropic_msgs.append({"role": "assistant", "content": parts})
                else:
                    anthropic_msgs.append({"role": "assistant", "content": content or ""})
            elif role == "tool":
                # tool 结果 → tool_result
                tool_call_id = msg.get("tool_call_id", "")
                anthropic_msgs.append(
                    {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": tool_call_id, "content": content}],
                    }
                )

        return system_text or None, anthropic_msgs

    def _convert_tools(self, tools: List[Dict]) -> List[Dict]:
        """将 OpenAI 格式 tools 转为 Anthropic 格式"""
        anthropic_tools = []
        for t in tools:
            if t.get("type") == "function":
                func = t["function"]
                anthropic_tools.append(
                    {
                        "name": func["name"],
                        "description": func.get("description", ""),
                        "input_schema": func.get("parameters", {"type": "object", "properties": {}}),
                    }
                )
        return anthropic_tools

    def _convert_response(self, response: Dict) -> Dict:
        """将 Anthropic 响应转为 OpenAI 兼容格式"""
        content = response.get("content", [])
        text_content = ""
        tool_calls = []

        for block in content:
            if block.get("type") == "text":
                text_content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    {
                        "id": block.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": block.get("name", ""),
                            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                        },
                    }
                )

        return {"role": "assistant", "content": text_content, "tool_calls": tool_calls if tool_calls else None}

    def _convert_sse_event(self, event: Dict) -> Optional[Dict]:
        """将 Anthropic SSE 事件转为统一的流式事件格式"""
        event_type = event.get("type", "")

        if event_type == "content_block_delta":
            delta = event.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                return {"type": "text_delta", "text": delta.get("text", "")}
            elif delta_type == "input_json_delta":
                return {"type": "tool_call_args", "tool_call_id": "", "tool_args": delta.get("partial_json", "")}
            elif delta_type == "thinking_delta":
                return {"type": "thinking_delta", "text": delta.get("thinking", "")}
        elif event_type == "content_block_start":
            block = event.get("content_block", {})
            if block.get("type") == "tool_use":
                return {"type": "tool_call_start", "tool_call_id": block.get("id", "")}
        elif event_type == "content_block_stop":
            return None  # 中间状态，不转发
        elif event_type == "message_delta":
            delta = event.get("delta", {})
            usage_data = event.get("usage", {})
            if usage_data:
                return {"type": "usage", "usage": usage_data}
            return {"type": "done", "text": ""}
        elif event_type == "message_stop":
            return {"type": "done", "text": ""}
        elif event_type == "error":
            error_data = event.get("error", {})
            return {"type": "error", "message": error_data.get("message", str(event))}
        elif event_type == "ping":
            return None

        return None

    def chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        model: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Dict:
        system_text, anthropic_msgs = self._convert_messages(messages)

        payload = {
            "model": model or ANTHROPIC_DEFAULT_MODEL,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_text:
            payload["system"] = system_text
        if tools:
            payload["tools"] = self._convert_tools(tools)

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(self.api_url, data=req_data, headers=self._build_headers(), method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            return self._convert_response(result)

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            err_msg = f"HTTP {e.code}: {body[:200]}"
            logger.error(f"[anthropic] API HTTP 错误: {err_msg}")
            raise RuntimeError(err_msg) from e
        except Exception as e:
            logger.error(f"[anthropic] API 调用失败: {e}")
            raise

    def stream_chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        model: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Generator[Dict, None, None]:
        system_text, anthropic_msgs = self._convert_messages(messages)

        payload = {
            "model": model or ANTHROPIC_DEFAULT_MODEL,
            "messages": anthropic_msgs,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if system_text:
            payload["system"] = system_text
        if tools:
            payload["tools"] = self._convert_tools(tools)

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(self.api_url, data=req_data, headers=self._build_headers(), method="POST")

            accumulated_text = ""
            current_tool_call = None

            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for line in resp:
                    line_str = ""
                    if isinstance(line, bytes):
                        line_str = line.decode("utf-8", errors="replace").strip()
                    else:
                        line_str = str(line).strip()

                    if not line_str:
                        continue

                    # Anthropic SSE 格式: "event: xxx\ndata: {...}"
                    if line_str.startswith("data: "):
                        data_str = line_str[6:]
                        try:
                            event_data = json.loads(data_str)
                            unified = self._convert_sse_event(event_data)
                            if unified:
                                if unified["type"] == "text_delta":
                                    accumulated_text += unified["text"]
                                elif unified["type"] == "tool_call_start":
                                    current_tool_call = {"id": unified["tool_call_id"], "name": "", "arguments": ""}
                                yield unified
                        except json.JSONDecodeError:
                            continue

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            err_msg = f"HTTP {e.code}: {body[:200]}"
            logger.error(f"[anthropic] 流式 API HTTP 错误: {err_msg}")
            yield {"type": "error", "message": err_msg}
        except Exception as e:
            logger.error(f"[anthropic] 流式 API 调用失败: {e}")
            yield {"type": "error", "message": str(e)}
