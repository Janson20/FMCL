"""AI 提供商抽象层 - 多模型支持（净读 AI / OpenAI / Anthropic / 自定义）

每个 Provider 负责与特定 API 通信，封装认证、请求构建、响应解析。
提供 chat() 非流式和 stream_chat() 流式两种调用方式。
"""

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Callable, Dict, Generator, List, Optional

from logzero import logger

from ui.agent.models import ModelInfo, get_default_model, get_models_by_provider


class BaseProvider(ABC):
    """AI 提供商基类"""

    # 子类必须设置
    provider_id: str = ""
    provider_name: str = ""
    default_api_url: str = ""

    def __init__(
        self, api_key: str, api_url: str = "", timeout: int = 120, extra_headers: Optional[Dict[str, str]] = None
    ):
        self.api_key = api_key
        self.api_url = api_url or self.default_api_url
        self.timeout = timeout
        self.extra_headers = extra_headers or {}

    @property
    def models(self) -> List[ModelInfo]:
        """返回此提供商支持的所有模型"""
        return get_models_by_provider(self.provider_id)

    @property
    def default_model(self) -> Optional[ModelInfo]:
        """返回默认模型"""
        return get_default_model(self.provider_id)

    @abstractmethod
    def chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        model: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Dict:
        """非流式聊天补全

        Returns:
            {"role": "assistant", "content": "...", "tool_calls": [...]}
        """
        ...

    @abstractmethod
    def stream_chat(
        self,
        messages: List[Dict],
        tools: Optional[List[Dict]] = None,
        model: str = "",
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> Generator[Dict, None, None]:
        """流式聊天补全

        Yields:
            事件字典，格式为 {"type": "text_delta"|"thinking_delta"|"tool_call_start"|... , ...}
        """
        ...

    @classmethod
    def from_config(
        cls, api_key: str, api_url: str = "", timeout: int = 120, extra_headers: Optional[Dict[str, str]] = None
    ):
        return cls(api_key=api_key, api_url=api_url, timeout=timeout, extra_headers=extra_headers)

    @staticmethod
    def test_connection(api_url: str, api_key: str, timeout: int = 15) -> dict:
        """测试 API 连接是否正常

        Returns:
            {"ok": True/False, "message": "...", "models": [...]}
        """
        try:
            req_data = json.dumps(
                {"model": "gpt-3.5-turbo", "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1}
            ).encode("utf-8")

            url = api_url.rstrip("/") + "/chat/completions"
            req = urllib.request.Request(
                url,
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "User-Agent": "FMCL/2.0 (Minecraft Launcher; agent-connection-test)",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                models_resp = json.loads(resp.read().decode("utf-8"))
                models = []
                if "data" in models_resp:
                    models = [m.get("id", "") for m in models_resp.get("data", [])]

            return {"ok": True, "message": "连接成功", "models": models}
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")[:300]
            except Exception:
                pass
            if e.code == 401:
                return {"ok": False, "message": "认证失败：API Key 无效", "models": []}
            return {"ok": False, "message": f"HTTP {e.code}: {body}", "models": []}
        except Exception as e:
            return {"ok": False, "message": f"连接失败: {e}", "models": []}
