"""AgentMixin - 集成到主应用的 AGENT 标签页（三栏布局）

新版设计中 AgentChatView 自带模型选择器和会话管理，
agent_mixin 主要负责初始化 provider、同步状态、对接主应用 callbacks。
"""

import threading
from typing import Dict, Optional, Callable
from logzero import logger

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _
from ui.agent.providers.jingdu import JingduProvider
from ui.agent.providers.openai import OpenAIProvider
from ui.agent.providers.anthropic import AnthropicProvider
from ui.agent.providers.custom import CustomProvider
from ui.agent.agent_chat import AgentChatView
from ui.agent.config import get_agent_config, save_agent_config, init_agent_config


class AgentMixin(object):
    """AGENT 智能助手 Mixin - 添加 AGENT 标签页到主窗口"""

    def _build_agent_tab_content(self):
        """构建 AGENT 标签页内容"""
        logger.info("[Agent] 开始构建 AGENT 标签页（新三栏布局）")

        container = ctk.CTkFrame(self.agent_tab, fg_color="transparent")
        container.pack(fill=ctk.BOTH, expand=True)

        # 创建新的 AgentChatView（内部自带顶栏和三栏布局）
        self._agent_chat = AgentChatView(container, callbacks=self.callbacks)
        self._agent_chat.pack(fill=ctk.BOTH, expand=True, padx=5, pady=5)
        logger.info("[Agent] 标签页 UI 构建完成")

        # 注册主题引用
        if not hasattr(self, '_theme_refs'):
            self._theme_refs = []

    def _refresh_agent_colors(self):
        self._sync_agent_status()

    def _on_agent_clear_log(self):
        """AGENT 已不再单独维护日志侧边栏，保留接口兼容"""
        pass

    def _get_agent_token(self) -> str:
        """获取净读 AI Token"""
        if "get_jdz_token" in self.callbacks:
            return self.callbacks["get_jdz_token"]() or ""
        return ""

    def _on_agent_quick_send(self, event=None):
        """从顶部快速输入框发送消息到 AGENT 标签页"""
        text = self._agent_quick_input.get().strip()
        if not text:
            return
        self._agent_quick_input.delete(0, ctk.END)
        self.tabview.set(_("tab_agent"))
        if hasattr(self, "_agent_chat") and self._agent_chat:
            self._agent_chat.send_message(text)

    def _sync_agent_status(self):
        """同步 provider 和会话状态"""
        if not hasattr(self, "_agent_chat") or self._agent_chat is None:
            return

        has_token = bool(self._get_agent_token())
        logger.info(f"[Agent] 同步状态: Token={'有值' if has_token else '空'}")

        # 初始化配置
        try:
            init_agent_config()
        except Exception as e:
            logger.error(f"[Agent] 配置初始化失败: {e}")

        if has_token:
            # 净读 AI 始终可用
            try:
                provider = JingduProvider(api_key=self._get_agent_token())
                self._agent_chat.set_provider(provider)
                self._agent_chat.set_callbacks(self.callbacks)
                logger.info("[Agent] 净读 AI Provider 初始化成功")
            except Exception as e:
                logger.error(f"[Agent] Provider 初始化失败: {e}")
            self._refresh_agent_credits()

        # 尝试加载其他 Provider 配置
        try:
            config = get_agent_config()
            for pid in ["openai", "anthropic", "custom"]:
                pc = config.providers.get(pid)
                if pc and pc.api_key:
                    # Provider 配置在 agent_chat 内部通过 _on_provider_changed 处理
                    pass
        except Exception as e:
            logger.error(f"[Agent] 加载 Provider 配置失败: {e}")

    def _refresh_agent_credits(self):
        """刷新 AI 积分余额"""
        if not hasattr(self, "_agent_chat") or not self._agent_chat:
            return
        if "fetch_jdz_user_info" not in self.callbacks:
            return

        def _do_refresh():
            info = self.callbacks["fetch_jdz_user_info"]()
            if info and hasattr(self, "_agent_chat"):
                credits = info.get("ai_credits", 0)
                self._agent_chat.update_credits(credits)

        threading.Thread(target=_do_refresh, daemon=True).start()

    def _update_agent_callbacks(self):
        """更新 AGENT 的回调（在启动器就绪后调用）"""
        logger.info("[Agent] _update_agent_callbacks 被调用")
        if hasattr(self, "_agent_chat") and self._agent_chat:
            self._agent_chat.set_callbacks(self.callbacks)
        else:
            logger.warning("[Agent] _agent_chat 尚未创建，跳过更新")
        self._sync_agent_status()

    def _get_agent_current_session_id(self) -> str:
        """获取当前会话 ID（供工具使用）"""
        if hasattr(self, "_agent_chat") and self._agent_chat and self._agent_chat._session:
            return self._agent_chat._session.id
        return ""
