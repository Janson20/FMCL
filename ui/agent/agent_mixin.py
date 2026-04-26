"""AgentMixin - 集成到主应用的 AGENT 标签页"""

import threading
from typing import Dict, Callable
from logzero import logger

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _
from ui.agent.provider import AIProvider
from ui.agent.agent_chat import AgentChatView


class AgentMixin(object):
    """AGENT 智能助手 Mixin - 添加 AGENT 标签页到主窗口"""

    def _build_agent_tab_content(self):
        """构建 AGENT 标签页内容"""
        logger.info("[Agent] 开始构建 AGENT 标签页")
        container = ctk.CTkFrame(self.agent_tab, fg_color="transparent")
        container.pack(fill=ctk.BOTH, expand=True)

        header = ctk.CTkFrame(container, fg_color="transparent", height=40)
        header.pack(fill=ctk.X, pady=(10, 0), padx=15)
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text=_("agent_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self._agent_status_label = ctk.CTkLabel(
            header,
            text=_("agent_status_not_configured"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["warning"],
        )
        self._agent_status_label.pack(side=ctk.RIGHT, padx=(0, 10))

        body = ctk.CTkFrame(container, fg_color="transparent")
        body.pack(fill=ctk.BOTH, expand=True, padx=15, pady=(0, 10))

        sidebar = ctk.CTkFrame(body, fg_color=COLORS["card_bg"], corner_radius=12, width=220)
        sidebar.pack(side=ctk.LEFT, fill=ctk.BOTH, padx=(0, 10))
        sidebar.pack_propagate(False)

        ctk.CTkLabel(
            sidebar,
            text=_("launcher_log"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=12, pady=(15, 5), anchor=ctk.W)

        ctk.CTkFrame(sidebar, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(0, 8)
        )

        self._agent_log_text = ctk.CTkTextbox(
            sidebar,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_secondary"],
            activate_scrollbars=True,
            wrap=ctk.WORD,
            spacing3=1,
        )
        self._agent_log_text.pack(fill=ctk.BOTH, expand=True, padx=12, pady=(0, 5))

        ctk.CTkButton(
            sidebar,
            text=_("clear_log"),
            height=26,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_agent_clear_log,
        ).pack(fill=ctk.X, padx=12, pady=(0, 12))

        chat_frame = ctk.CTkFrame(body, fg_color=COLORS["card_bg"], corner_radius=12)
        chat_frame.pack(side=ctk.RIGHT, fill=ctk.BOTH, expand=True)

        self._agent_chat = AgentChatView(chat_frame, callbacks=self.callbacks)
        self._agent_chat.pack(fill=ctk.BOTH, expand=True, padx=10, pady=10)
        logger.info("[Agent] 标签页 UI 构建完成")

    def _on_agent_clear_log(self):
        """清空 AGENT 标签页的日志"""
        if hasattr(self, "_agent_log_text"):
            self._agent_log_text.delete("1.0", ctk.END)

    def _get_agent_token(self) -> str:
        """获取 AI Token"""
        if "get_jdz_token" in self.callbacks:
            token = self.callbacks["get_jdz_token"]()
            result = token or ""
            return result
        return ""

    def _on_agent_quick_send(self, event=None):
        """从顶部快速输入框发送消息到 AGENT 标签页"""
        text = self._agent_quick_input.get().strip()
        if not text:
            return
        self._agent_quick_input.delete(0, ctk.END)
        self.tabview.set("🤖 AGENT")
        if hasattr(self, "_agent_chat") and self._agent_chat:
            self._agent_chat.send_message(text)

    def _sync_agent_status(self):
        """根据当前 callbacks 同步状态标签和 provider"""
        has_token = bool(self._get_agent_token())
        logger.info(f"[Agent] 同步状态: Token={'有值' if has_token else '空'}")

        status_text = _("agent_status_ready") if has_token else _("agent_status_not_configured")
        status_color = COLORS["success"] if has_token else COLORS["warning"]
        if hasattr(self, "_agent_status_label"):
            self._agent_status_label.configure(text=status_text, text_color=status_color)

        if has_token and hasattr(self, "_agent_chat"):
            try:
                provider = AIProvider.from_config(self._get_agent_token())
                self._agent_chat.set_provider(provider)
                self._agent_chat.set_callbacks(self.callbacks)
                logger.info("[Agent] AI 提供商初始化成功")
            except Exception as e:
                logger.error(f"[Agent] AI 提供商初始化失败: {e}")

    def _update_agent_callbacks(self):
        """更新 AGENT 的回调（在启动器就绪后调用）"""
        logger.info("[Agent] _update_agent_callbacks 被调用")
        if hasattr(self, "_agent_chat") and self._agent_chat:
            logger.info("[Agent] 更新 agent_chat 的 callbacks")
            self._agent_chat.set_callbacks(self.callbacks)
        else:
            logger.warning("[Agent] _agent_chat 尚未创建，跳过 callbacks 更新")
        self._sync_agent_status()
