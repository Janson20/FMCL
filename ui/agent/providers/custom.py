"""自定义 OpenAI 兼容提供商 - 用户自定义 API 端点

与 OpenAIProvider 逻辑相同，但使用用户配置的 Base URL 和模型列表。
"""

import json
import urllib.request
import urllib.error
from typing import Dict, List, Optional, Generator
from logzero import logger

from ui.agent.provider import BaseProvider
from ui.agent.stream import SSEParser, SSEEventType
from ui.agent.models import ModelInfo


class CustomProvider(BaseProvider):
    """自定义 OpenAI 兼容端点提供商"""

    provider_id = "custom"
    provider_name = "自定义端点"
    default_api_url = ""

    def __init__(
        self,
        api_key: str,
        api_url: str = "",
        timeout: int = 120,
        extra_headers: Optional[Dict[str, str]] = None,
        custom_models: Optional[List[str]] = None,
    ):
        super().__init__(api_key=api_key, api_url=api_url, timeout=timeout, extra_headers=extra_headers)
        self._custom_models: List[ModelInfo] = []
        if custom_models:
            for m_id in custom_models:
                self._custom_models.append(ModelInfo(
                    id=m_id,
                    provider_id="custom",
                    name=m_id,
                    description="用户自定义模型",
                    requires_custom_url=True,
                ))

    @property
    def models(self) -> List[ModelInfo]:
        if self._custom_models:
            return self._custom_models
        # 回退默认模型列表
        return [
            ModelInfo(
                id="default",
                provider_id="custom",
                name="自定义模型",
                description="请在设置中配置模型列表",
                requires_custom_url=True,
            ),
        ]

    @property
    def default_model(self) -> Optional[ModelInfo]:
        models = self.models
        return models[0] if models else None

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
        model_name = model or (self.models[0].id if self.models else "default")
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _ensure_chat_completions_url(self) -> str:
        """确保 URL 指向 chat/completions 端点"""
        url = self.api_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        return url

    def chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        model: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Dict:
        payload = self._build_payload(messages, tools, model, max_tokens, temperature, stream=False)
        url = self._ensure_chat_completions_url()

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=req_data,
                headers=self._build_headers(),
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            message = result.get("choices", [{}])[0].get("message", {})
            if not message:
                return {"role": "assistant", "content": ""}

            tool_calls = message.get("tool_calls", [])
            if tool_calls:
                for tc in tool_calls:
                    if "function" not in tc:
                        tc["function"] = {"name": "", "arguments": "{}"}

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
            logger.error(f"[custom] API HTTP 错误: {err_msg}")
            raise RuntimeError(err_msg) from e
        except Exception as e:
            logger.error(f"[custom] API 调用失败: {e}")
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
        url = self._ensure_chat_completions_url()

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url,
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
            logger.error(f"[custom] 流式 API HTTP 错误: {err_msg}")
            yield {"type": "error", "message": err_msg}
        except Exception as e:
            logger.error(f"[custom] 流式 API 调用失败: {e}")
            yield {"type": "error", "message": str(e)}
