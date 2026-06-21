"""净读 AI 提供商 - 封装净读 AI API（DeepSeek V4 后端）

API 端点: https://jingdu.qzz.io/api/deepseek/v1/chat/completions
兼容 OpenAI chat/completions 格式。

模型更新 (2026/06):
- deepseek-v4-flash: 新一代主力模型，1M 上下文，384K 输出，支持思考模式切换
- deepseek-v4-pro: 旗舰推理模型，1M 上下文，384K 输出
- deepseek-chat / deepseek-reasoner: 将于 2026/07/24 弃用

思考模式控制:
- thinking: {"type": "enabled"/"disabled"}  开关
- reasoning_effort: "high"/"max"             思考强度（默认 high）
"""

import json
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Generator
from logzero import logger

from ui.agent.provider import BaseProvider
from ui.agent.models import get_model_by_id
from ui.agent.stream import SSEParser, SSEEventType

JDZ_DEFAULT_API_URL = "https://jingdu.qzz.io/api/deepseek/v1/chat/completions"
JDZ_DEFAULT_MODEL = "deepseek-v4-flash"


class JingduProvider(BaseProvider):
    """净读 AI 提供商（DeepSeek V4 API）"""

    provider_id = "jingdu"
    provider_name = "净读 AI"
    default_api_url = JDZ_DEFAULT_API_URL

    def __init__(
        self,
        api_key: str,
        api_url: str = "",
        timeout: int = 120,
        extra_headers: Optional[Dict[str, str]] = None,
        thinking_enabled: Optional[bool] = None,   # None = 模型默认
        reasoning_effort: str = "high",             # "high" | "max"
    ):
        super().__init__(api_key=api_key, api_url=api_url or JDZ_DEFAULT_API_URL, timeout=timeout, extra_headers=extra_headers)
        self.thinking_enabled = thinking_enabled
        self.reasoning_effort = reasoning_effort

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
            "model": model or JDZ_DEFAULT_MODEL,
            "messages": messages,
            "stream": stream,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        # V4 思考模式控制
        model_info = get_model_by_id(model) if model else None
        if model_info is None:
            model_info = get_model_by_id(JDZ_DEFAULT_MODEL)

        if model_info and model_info.supports_reasoning:
            # 确定思考模式开关：显式传参 > 模型默认 > True
            if self.thinking_enabled is not None:
                thinking_on = self.thinking_enabled
            else:
                thinking_on = model_info.thinking_default

            thinking_type = "enabled" if thinking_on else "disabled"
            payload["thinking"] = {"type": thinking_type}

            if thinking_on:
                payload["reasoning_effort"] = self.reasoning_effort

        if tools:
            payload["tools"] = tools

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

            # 将 reasoning_content 合并到 content（如果 V4 返回了思考内容）
            reasoning = message.get("reasoning_content", "")
            content = message.get("content", "") or ""
            if reasoning and not content:
                content = f"[思考过程]\n{reasoning}\n\n[分析完毕]"
            message["content"] = content

            # 标准化 tool_calls
            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    if "function" not in tc:
                        tc["function"] = {"name": "", "arguments": "{}"}
                message["tool_calls"] = tool_calls

            return message

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            err_msg = f"HTTP {e.code}: {body[:200]}"
            logger.error(f"[jingdu] API HTTP 错误: {err_msg}")
            raise RuntimeError(err_msg) from e
        except Exception as e:
            logger.error(f"[jingdu] API 调用失败: {e}")
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
            logger.error(f"[jingdu] 流式 API HTTP 错误: {err_msg}")
            yield {"type": "error", "message": err_msg}
        except Exception as e:
            logger.error(f"[jingdu] 流式 API 调用失败: {e}")
            yield {"type": "error", "message": str(e)}
