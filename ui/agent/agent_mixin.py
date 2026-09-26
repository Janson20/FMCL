"""AgentMixin - 集成到主应用的 AGENT 标签页（三栏布局）

新版设计中 AgentChatView 自带模型选择器和会话管理，
agent_mixin 主要负责初始化 provider、同步状态、对接主应用 callbacks。

任务 1.14：本 Mixin 里的**业务部分**（配置初始化、回调编排、积分取值）已搬进
``services/agent_service.py``；本文件只保留"拿到宿主窗口的控件/回调、再调服务"
这一步，公开方法名与签名一律不变。
"""

import threading
from typing import Callable, Dict, Optional

import customtkinter as ctk
from logzero import logger

from services.agent_service import AgentService, build_callbacks, fetch_credits, load_provider_configs
from ui.agent.agent_chat import AgentChatView
from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


class AgentMixin(object):
    """AGENT 智能助手 Mixin - 添加 AGENT 标签页到主窗口"""

    def _agent_svc(self) -> AgentService:
        """AGENT 业务服务（任务 1.14）。

        Mixin 没有 ``__init__``（它被 mix 进主窗口），所以惰性建一次并挂在实例上。
        服务构造期不做任何重活（不读盘、不连网），在这里建是安全的。
        """
        svc = getattr(self, "_agent_service_inst", None)
        if svc is None:
            svc = AgentService()
            self._agent_service_inst = svc
        return svc

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
        if not hasattr(self, "_theme_refs"):
            self._theme_refs = []

    def _refresh_agent_colors(self):
        self._sync_agent_status()
        if hasattr(self, "_agent_chat") and self._agent_chat and hasattr(self._agent_chat, "_voice_btn"):
            try:
                self._agent_chat._voice_btn.refresh_theme()
            except Exception:
                pass

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
            self._agent_svc().init_config()
        except Exception as e:
            logger.error(f"[Agent] 配置初始化失败: {e}")

        if has_token:
            # 净读 AI 始终可用
            try:
                provider = self._agent_svc().jingdu_provider(self._get_agent_token())
                cbs = build_callbacks(self.callbacks, self._get_agent_current_session_id, self._get_agent_cfg)
                self._agent_chat.set_provider(provider)
                self._agent_chat.set_callbacks(cbs)
                logger.info("[Agent] 净读 AI Provider 初始化成功")
            except Exception as e:
                logger.error(f"[Agent] Provider 初始化失败: {e}")
            self._refresh_agent_credits()

        # 尝试加载其他 Provider 配置
        load_provider_configs()

    def _refresh_agent_credits(self):
        """刷新 AI 积分余额"""
        if not hasattr(self, "_agent_chat") or not self._agent_chat:
            return
        if "fetch_jdz_user_info" not in self.callbacks:
            return

        def _do_refresh():
            credits = fetch_credits(self.callbacks["fetch_jdz_user_info"])
            if credits is not None and hasattr(self, "_agent_chat"):
                self._agent_chat.update_credits(credits)

        threading.Thread(target=_do_refresh, daemon=True).start()

    def _update_agent_callbacks(self):
        """更新 AGENT 的回调（在启动器就绪后调用）"""
        logger.info("[Agent] _update_agent_callbacks 被调用")
        if hasattr(self, "_agent_chat") and self._agent_chat:
            # 注入 get_current_session_id 回调供 todo_write 工具使用
            cbs = build_callbacks(self.callbacks, self._get_agent_current_session_id, self._get_agent_cfg)
            self._agent_chat.set_callbacks(cbs)
        else:
            logger.warning("[Agent] _agent_chat 尚未创建，跳过更新")
        self._sync_agent_status()

    def _get_agent_current_session_id(self) -> str:
        """获取当前会话 ID（供工具使用）"""
        if hasattr(self, "_agent_chat") and self._agent_chat and self._agent_chat._session:
            return self._agent_chat._session.id
        return ""

    def _get_agent_cfg(self) -> dict:
        """获取 Agent 配置（供 web_search 等工具使用）"""
        try:
            return {"bing_api_key": self._agent_svc().bing_api_key()}
        except Exception:
            return {}
