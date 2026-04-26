"""AI 提供商抽象层 - 净读 AI（DeepSeek）API 封装"""

import json
import urllib.request
import urllib.error
from typing import List, Dict, Optional
from logzero import logger


JDZ_API_URL = "https://jingdu.qzz.io/api/deepseek/v1/chat/completions"


class AIProvider:
    """AI API 调用封装（净读 AI DeepSeek）"""

    def __init__(self, api_key: str, api_url: str = JDZ_API_URL, model: str = "deepseek-chat"):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.model = model

    def chat(self, messages: List[Dict], tools: Optional[List[Dict]] = None, stream: bool = False) -> str:
        """调用净读 AI 聊天补全 API"""
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools

        try:
            req_data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                self.api_url,
                data=req_data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "User-Agent": "FMCL/1.0 (Minecraft Launcher; agent)",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))

            content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content

        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="ignore")
            except Exception:
                pass
            err_msg = f"HTTP {e.code}: {body[:200]}"
            logger.error(f"净读 AI API HTTP 错误: {err_msg}")
            raise RuntimeError(err_msg) from e

        except Exception as e:
            logger.error(f"净读 AI API 调用失败: {e}")
            raise

    @classmethod
    def from_config(cls, token: str) -> "AIProvider":
        """从配置创建提供商"""
        return cls(api_key=token)
