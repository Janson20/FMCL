"""OpenAI 提供商 - 标准 OpenAI chat/completions API + 自定义端点兼容"""

import json
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Generator
from logzero import logger

from ui.agent.provider import BaseProvider
from ui.agent.stream import SSEParser, SSEEventType

OPENAI_DEFAULT_API_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_DEFAULT_MODEL = "gpt-4o"


class OpenAIProvider(BaseProvider):
    """标准 OpenAI 兼容提供商"""

    provider_id = "openai"
    provider_name = "OpenAI"
    default_api_url = OPENAI_DEFAULT_API_URL

    def __init__(self, api_key: str, api_url: str = "", timeout: int = 120, extra_headers: Optional[Dict[str, str]] = None):
        super().__init__(api_key=api_key, api_url=api_url or OPENAI_DEFAULT_API_URL, timeout=timeout, extra_headers=extra_headers)

    def _build_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "FMCL/2.0 (Minecraft Launcher; agent)",
            **self.extra_headers,
        }

    def _build_payload(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        model: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
        stream: bool = False,
    ) -> Dict:
        payload = {
            "model": model or OPENAI_DEFAULT_MODEL,
            "messages": messages,
            "stream": stream,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        model: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Dict:
        payload = self._build_payload(messages, tools, model, max_tokens, temperature, stream=False)

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.api_url,
                data=req_data,
                headers=self._build_headers(),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            message = result.get("choices", [{}])[0].get("message", {})
            if not message:
                return {"role": "assistant", "content": ""}

            # 标准化 tool_calls
            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    if "function" not in tc:
                        tc["function"] = {"name": "", "arguments": "{}"}

            # 如果 content 是 None（纯工具调用），设为空字符串
            if message.get("content") is None:
                message["content"] = ""

            return message

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            err_msg = f"HTTP {e.code}: {body[:200]}"
            logger.error(f"[openai] API HTTP 错误: {err_msg}")
            raise RuntimeError(err_msg) from e
        except Exception as e:
            logger.error(f"[openai] API 调用失败: {e}")
            raise

    def stream_chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        model: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Generator[Dict, None, None]:
        payload = self._build_payload(messages, tools, model, max_tokens, temperature, stream=True)
        # OpenAI stream_options 返回 usage
        if payload.get("stream"):
            payload["stream_options"] = {"include_usage": True}

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.api_url,
                data=req_data,
                headers=self._build_headers(),
                method="POST",
            )

            parser = SSEParser()
            accumulated_text = ""

            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for line in resp:
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
                        yield {"type": "text_delta", "text": event.text, "finish_reason": event.finish_reason}
                    elif event.type == SSEEventType.THINKING_DELTA:
                        yield {"type": "thinking_delta", "text": event.text}
                    elif event.type == SSEEventType.TOOL_CALL_START:
                        yield {"type": "tool_call_start", "tool_call_id": event.tool_call_id}
                    elif event.type == SSEEventType.TOOL_CALL_NAME:
                        yield {"type": "tool_call_name", "tool_call_id": event.tool_call_id, "tool_name": event.tool_name}
                    elif event.type == SSEEventType.TOOL_CALL_ARGS:
                        yield {"type": "tool_call_args", "tool_call_id": event.tool_call_id, "tool_args": event.tool_args}
                    elif event.type == SSEEventType.USAGE:
                        yield {"type": "usage", "usage": event.usage}
                    elif event.type == SSEEventType.DONE:
                        yield {"type": "done", "text": accumulated_text}
                    elif event.type == SSEEventType.ERROR:
                        yield {"type": "error", "message": event.error_message}

            completed_tools = parser.get_completed_tool_calls()
            if completed_tools:
                for tc in completed_tools:
                    yield {"type": "tool_call_complete", "tool_call": tc}
                yield {"type": "done", "text": accumulated_text, "tool_calls": completed_tools}

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            err_msg = f"HTTP {e.code}: {body[:200]}"
            logger.error(f"[openai] 流式 API HTTP 错误: {err_msg}")
            yield {"type": "error", "message": err_msg}
        except Exception as e:
            logger.error(f"[openai] 流式 API 调用失败: {e}")
            yield {"type": "error", "message": str(e)}
