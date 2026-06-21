"""AGENT 聊天 UI - 三栏布局（会话列表 | 对话 | Todo）+ 流式输出 + 模型选择器

布局:
┌─ 顶栏: 模型选择器 ─────────────────────────────┐
│ 净读 AI [deepseek-v4-flash ▼]    积分: 500    新会话 │
├──────┬────────────────────────┬─────────────────┤
│ 会话 │  对话区（流式输出）    │  Todo 面板      │
│ 列表 │                       │                 │
│      │   ┌─ 思考块（折叠） ┐ │  ⬜ pending     │
│      │   └────────────────┘ │  🔄 in_progress │
│      │   📝 AI 回复内容...   │  ✅ completed   │
│      │                       │                 │
│      │ [输入框____________] │                 │
│      │ [发送]               │                 │
├──────┴────────────────────────┴─────────────────┤
│ 状态栏: Token 用量 | 模型名                     │
└─────────────────────────────────────────────────┘
"""

import json
import threading
import time
from typing import Dict, List, Optional, Callable, Any, Generator
from logzero import logger

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY
from ui.dialogs import show_notification, show_confirmation
from ui.i18n import _
from ui.agent.models import ModelInfo, get_model_catalog, get_provider_names, get_default_model, get_models_by_provider
from ui.agent.provider import BaseProvider
from ui.agent.providers.jingdu import JingduProvider
from ui.agent.providers.openai import OpenAIProvider
from ui.agent.providers.anthropic import AnthropicProvider
from ui.agent.providers.custom import CustomProvider
from ui.agent.tool_registry import ToolRegistry, get_registry
from ui.agent.tools.system import DANGEROUS_MARKER, execute_dangerous_command
from ui.agent.tools.user import ASK_USER_MARKER
from ui.agent.tools.todo_write import load_todos
from ui.agent.system_prompt import get_system_prompt
from ui.agent.session import AgentSession
from ui.agent.permission import check_permission
from ui.agent.skill import get_skills_context_text


def _trigger_agent_ach(achievement_id: str, value: int = 1):
    try:
        from achievement_engine import get_achievement_engine
        engine = get_achievement_engine()
        if engine:
            engine.update_progress(achievement_id, value=value)
    except Exception:
        pass


# ============ 辅助对话框 ============

class TextInputDialog(ctk.CTkToplevel):
    """文本输入弹窗"""

    def __init__(self, parent, title: str, question: str, callback: Callable[[str], None]):
        super().__init__(parent)
        self._callback = callback

        self.title(title)
        self.configure(fg_color=COLORS["bg_dark"])
        try:
            self.grab_set()
        except Exception:
            pass

        w, h = 520, self._calc_height(question)
        self.geometry(f"{w}x{h}")
        self.resizable(False, False)
        self.update_idletasks()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        ctk.CTkLabel(main, text=question, font=ctk.CTkFont(family=FONT_FAMILY, size=14),
                     text_color=COLORS["text_primary"], wraplength=460, justify=ctk.LEFT,
                     ).pack(anchor=ctk.W, pady=(0, 12))

        self._entry = ctk.CTkEntry(main, height=38, font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                                    fg_color=COLORS["bg_medium"], border_color=COLORS["card_border"])
        self._entry.pack(fill=ctk.X, pady=(0, 15))
        self._entry.focus_set()
        self._entry.bind("<Return>", lambda e: self._on_confirm())

        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(fill=ctk.X)

        ctk.CTkButton(btn_frame, text=_("agent_cancel"), width=80, height=32,
                       font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                       fg_color=COLORS["bg_medium"], hover_color=COLORS["bg_light"],
                       text_color=COLORS["text_primary"], command=self._on_cancel).pack(side=ctk.LEFT)
        ctk.CTkButton(btn_frame, text=_("agent_send"), width=80, height=32,
                       font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                       fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                       command=self._on_confirm).pack(side=ctk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    @staticmethod
    def _calc_height(text: str) -> int:
        line_height = 22
        num_lines = max(1, (len(text) + 33 - 1) // 33)
        return max(240, num_lines * line_height + 170)

    def _on_confirm(self):
        value = self._entry.get().strip()
        if not value:
            return
        self.grab_release()
        self.destroy()
        if self._callback:
            self._callback(value)

    def _on_cancel(self):
        self.grab_release()
        self.destroy()
        if self._callback:
            self._callback("")


class OptionSelectDialog(ctk.CTkToplevel):
    """选项选择弹窗（增强版 - 支持多问题、多选、(Recommended) 标签）"""

    def __init__(self, parent, questions: List[dict], callback: Callable[[List[str]], None]):
        super().__init__(parent)
        self._callback = callback
        self._questions = questions
        self._answers: List[List[str]] = []
        self._current_idx = 0
        self._selected_opts: List[str] = []

        self.title("FMCL")
        self.configure(fg_color=COLORS["bg_dark"])
        try:
            self.grab_set()
        except Exception:
            pass

        w, h = 560, self._calc_height(questions[0] if questions else {})
        self.geometry(f"{w}x{h}")
        self.resizable(False, False)
        self.update_idletasks()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self._main = ctk.CTkFrame(self, fg_color="transparent")
        self._main.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        self._render_question()

        self.protocol("WM_DELETE_WINDOW", self._on_cancel_all)

    @staticmethod
    def _calc_height(q: dict) -> int:
        options = q.get("options", [])
        question = q.get("question", "")
        num_lines = max(1, (len(question) + 40 - 1) // 40)
        return max(360, num_lines * 22 + 70 * max(len(options), 2) + 120)

    def _render_question(self):
        for w in self._main.winfo_children():
            w.destroy()

        if self._current_idx >= len(self._questions):
            self._finish()
            return

        q = self._questions[self._current_idx]
        question = q.get("question", "")
        header = q.get("header", "")
        options = q.get("options", [])
        multi = q.get("multiSelect", False)
        custom = q.get("custom", True)
        self._selected_opts = []

        title = f"{header}: {question}" if header else question

        ctk.CTkLabel(self._main, text=title, font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                     text_color=COLORS["text_primary"], wraplength=520, justify=ctk.LEFT,
                     ).pack(anchor=ctk.W, pady=(0, 12))

        if multi:
            ctk.CTkLabel(self._main, text=_("agent_multi_select_hint"),
                         font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                         text_color=COLORS["text_secondary"]).pack(anchor=ctk.W, pady=(0, 5))

        for opt in options:
            label = opt.get("label", "")
            desc = opt.get("description", "")

            if multi:
                var = ctk.BooleanVar()
                self._selected_opts.append((label, var))

                cb = ctk.CTkCheckBox(
                    self._main, text=f"{label}  -  {desc}" if desc else label,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                    text_color=COLORS["text_primary"],
                    fg_color=COLORS["accent"],
                    variable=var,
                )
                cb.pack(fill=ctk.X, pady=3, padx=10, anchor=ctk.W)
            else:
                btn = ctk.CTkButton(
                    self._main,
                    text=f"{label}  -  {desc}" if desc else label,
                    height=38,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                    fg_color=COLORS["bg_medium"],
                    hover_color=COLORS["bg_light"],
                    text_color=COLORS["text_primary"],
                    anchor=ctk.W,
                    command=lambda l=label: self._on_select_single(l),
                )
                btn.pack(fill=ctk.X, pady=3)

        if custom:
            self._custom_entry = ctk.CTkEntry(
                self._main, height=32,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color=COLORS["bg_medium"],
                border_color=COLORS["card_border"],
                placeholder_text=_("agent_custom_answer_hint"),
            )
            self._custom_entry.pack(fill=ctk.X, pady=(10, 5))

        nav_frame = ctk.CTkFrame(self._main, fg_color="transparent")
        nav_frame.pack(fill=ctk.X, pady=(10, 0))

        if self._current_idx > 0:
            ctk.CTkButton(nav_frame, text=_("agent_prev"), width=80, height=30,
                          font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                          fg_color=COLORS["bg_medium"], hover_color=COLORS["bg_light"],
                          command=self._on_prev).pack(side=ctk.LEFT)

        next_label = _("agent_next") if self._current_idx < len(self._questions) - 1 else _("agent_send")
        ctk.CTkButton(nav_frame, text=next_label, width=80, height=30,
                       font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
                       fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                       command=self._on_next).pack(side=ctk.RIGHT)

        ctk.CTkLabel(nav_frame, text=f"{self._current_idx + 1}/{len(self._questions)}",
                     font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                     text_color=COLORS["text_secondary"]).pack(side=ctk.RIGHT, padx=(0, 15))

    def _on_select_single(self, label: str):
        self._answers.append([label])
        self._current_idx += 1
        self._render_question()

    def _on_next(self):
        q = self._questions[self._current_idx]
        multi = q.get("multiSelect", False)
        custom_allowed = q.get("custom", True)
        custom_text = ""
        if custom_allowed and hasattr(self, "_custom_entry"):
            custom_text = self._custom_entry.get().strip()

        if multi:
            selected = [l for l, var in self._selected_opts if var.get()]
            if custom_text:
                selected.append(custom_text)
            if not selected:
                return
            self._answers.append(selected)
        else:
            if custom_text:
                self._answers.append([custom_text])
            else:
                return  # 需要选择或输入

        self._current_idx += 1
        self._render_question()

    def _on_prev(self):
        if self._current_idx > 0:
            self._current_idx -= 1
            if self._answers:
                self._answers.pop()
            self._render_question()

    def _on_cancel_all(self):
        self.grab_release()
        self.destroy()
        if self._callback:
            self._callback([])

    def _finish(self):
        self.grab_release()
        self.destroy()
        if self._callback:
            # 返回展平的答案列表
            flat_answers = []
            for a in self._answers:
                flat_answers.extend(a)
            self._callback(flat_answers)


# ============ 主聊天视图 ============

class AgentChatView(ctk.CTkFrame):
    """AGENT 聊天视图 - 三栏布局（会话列表 | 对话 | Todo）"""

    MAX_ITERATIONS = 50
    MAX_EMPTY_RETRIES = 3

    def __init__(self, parent, callbacks: Dict[str, Callable], **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        self._callbacks = callbacks
        self._provider: Optional[BaseProvider] = None
        self._session: Optional[AgentSession] = None
        self._running = False
        self._pending_dangerous = None
        self._pending_ask_user = None
        self._thinking_visible = True  # 默认展开思考

        self._registry = get_registry()

        self._init_session()
        self._build_ui()
        self._refresh_session_list()

    def _init_session(self):
        """初始化或加载会话"""
        system_prompt = get_system_prompt() + get_skills_context_text()
        self._session = AgentSession.create_new(
            provider_id="jingdu",
            model_id="deepseek-v4-flash",
            system_prompt=system_prompt,
        )
        if self._session.messages:
            self._append_system_message(_("agent_welcome1"))
            self._append_system_message(_("agent_welcome2"))

    def _build_ui(self):
        """构建三栏布局"""
        # === 顶栏：模型选择器 ===
        self._build_header()

        # === 主体三栏 ===
        main_area = ctk.CTkFrame(self, fg_color="transparent")
        main_area.pack(fill=ctk.BOTH, expand=True, padx=5, pady=(0, 5))

        # 左栏：会话列表
        self._build_session_panel(main_area)

        # 中栏：对话
        self._build_chat_panel(main_area)

        # 右栏：Todo 面板
        self._build_todo_panel(main_area)

        # 状态栏
        self._build_status_bar()

    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color=COLORS["card_bg"], corner_radius=10, height=44)
        header.pack(fill=ctk.X, padx=5, pady=(5, 0))
        header.pack_propagate(False)

        # Provider 选择
        ctk.CTkLabel(header, text=_("agent_provider") + ":", font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                     text_color=COLORS["text_secondary"]).pack(side=ctk.LEFT, padx=(10, 4))

        self._provider_var = ctk.StringVar(value="jingdu")
        providers = get_provider_names()
        provider_names = [p["name"] for p in providers]
        self._provider_menu = ctk.CTkOptionMenu(
            header, values=provider_names, variable=self._provider_var,
            width=100, height=28, font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"], button_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_medium"],
            command=self._on_provider_changed,
        )
        self._provider_menu.pack(side=ctk.LEFT, padx=(0, 8))

        # Model 选择
        self._model_var = ctk.StringVar(value="deepseek-v4-flash")
        self._model_menu = ctk.CTkOptionMenu(
            header, values=[], variable=self._model_var,
            width=160, height=28, font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"], button_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_medium"],
            command=self._on_model_changed,
        )
        self._model_menu.pack(side=ctk.LEFT, padx=(0, 4))
        self._refresh_model_list("jingdu")

        # 思考模式开关（仅 DeepSeek V4 模型可见）
        self._thinking_var = ctk.BooleanVar(value=False)  # 默认非思考
        self._thinking_check = ctk.CTkCheckBox(
            header, text="思考", variable=self._thinking_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            border_color=COLORS["text_secondary"],
            checkmark_color=COLORS["bg_dark"],
            command=self._on_thinking_toggled,
            width=20, height=20,
        )
        # 初态：jingdu V4 Flash 默认不显示（thinking_default=False），模型切换后再决定
        self._thinking_check.pack_forget()

        # reasoning_effort（思考强度，思考开启时显示）
        self._effort_var = ctk.StringVar(value="high")
        self._effort_menu = ctk.CTkOptionMenu(
            header, values=["high", "max"], variable=self._effort_var,
            width=60, height=22, font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color=COLORS["bg_medium"], button_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_medium"],
        )
        self._effort_menu.pack_forget()

        # 新会话按钮
        ctk.CTkButton(header, text=_("agent_new_session"), width=70, height=26,
                       font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                       fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                       command=self._on_new_session).pack(side=ctk.RIGHT, padx=(0, 10))

        # 积分显示
        self._credits_label = ctk.CTkLabel(header, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                                            text_color=COLORS["text_secondary"])
        self._credits_label.pack(side=ctk.RIGHT, padx=(0, 8))

    def _build_session_panel(self, parent):
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=10, width=160)
        panel.pack(side=ctk.LEFT, fill=ctk.BOTH, padx=(0, 5))
        panel.pack_propagate(False)

        ctk.CTkLabel(panel, text=_("agent_sessions"), font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=8, pady=(8, 4), anchor=ctk.W)

        self._session_list_frame = ctk.CTkScrollableFrame(panel, fg_color="transparent")
        self._session_list_frame.pack(fill=ctk.BOTH, expand=True, padx=4, pady=(0, 4))

    def _build_chat_panel(self, parent):
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=10)
        panel.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 5))

        # 消息显示区
        self._msg_display = ctk.CTkTextbox(
            panel, font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"], border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"], wrap=ctk.WORD,
            state=ctk.DISABLED,
        )
        self._msg_display.pack(fill=ctk.BOTH, expand=True, padx=6, pady=(6, 4))
        self._msg_display.tag_config("user", foreground=COLORS["accent"])
        self._msg_display.tag_config("assistant", foreground=COLORS["success"])
        self._msg_display.tag_config("system", foreground=COLORS["text_secondary"])
        self._msg_display.tag_config("tool", foreground=COLORS["warning"])
        self._msg_display.tag_config("thinking", foreground=COLORS["text_secondary"])
        self._msg_display.tag_config("thinking_hidden", foreground=COLORS["text_secondary"], elide=True)
        self._msg_display.tag_config("divider", foreground=COLORS["text_secondary"])
        self._msg_display.tag_config("tool_card", background=COLORS["bg_light"])

        # 进度指示
        self._progress_bar = ctk.CTkProgressBar(panel, height=4, fg_color=COLORS["bg_medium"],
                                                  progress_color=COLORS["accent"], mode="indeterminate")

        # 输入框
        input_frame = ctk.CTkFrame(panel, fg_color="transparent", height=44)
        input_frame.pack(fill=ctk.X, padx=6, pady=(2, 6))
        input_frame.pack_propagate(False)

        self._input_entry = ctk.CTkEntry(
            input_frame, height=34, font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"], border_color=COLORS["card_border"],
            placeholder_text=_("agent_input_placeholder"),
        )
        self._input_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 6))
        self._input_entry.bind("<Return>", lambda e: self._on_send())

        self._send_btn = ctk.CTkButton(
            input_frame, text=_("agent_send"), width=70, height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._on_send,
        )
        self._send_btn.pack(side=ctk.RIGHT)

        # 声明
        ctk.CTkLabel(panel, text=_("agent_disclaimer"), font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                     text_color=COLORS["text_secondary"]).pack(pady=(0, 4))

    def _build_todo_panel(self, parent):
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=10, width=180)
        panel.pack(side=ctk.LEFT, fill=ctk.BOTH)
        panel.pack_propagate(False)

        ctk.CTkLabel(panel, text=_("agent_todo_title"), font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                     text_color=COLORS["text_primary"]).pack(padx=8, pady=(8, 4), anchor=ctk.W)

        self._todo_frame = ctk.CTkScrollableFrame(panel, fg_color="transparent")
        self._todo_frame.pack(fill=ctk.BOTH, expand=True, padx=4, pady=(0, 4))

        self._todo_empty_label = ctk.CTkLabel(
            self._todo_frame, text=_("agent_todo_empty"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._todo_empty_label.pack(pady=20)

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self, fg_color=COLORS["card_bg"], corner_radius=8, height=24)
        bar.pack(fill=ctk.X, padx=5, pady=(0, 5))
        bar.pack_propagate(False)

        self._status_label = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                                           text_color=COLORS["text_secondary"])
        self._status_label.pack(side=ctk.LEFT, padx=(10, 0))

        self._token_label = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                                          text_color=COLORS["text_secondary"])
        self._token_label.pack(side=ctk.RIGHT, padx=(0, 10))

    # ============ 模型选择 ============

    def _refresh_model_list(self, provider_id: str):
        models = get_models_by_provider(provider_id)
        values = [m.name for m in models]
        if values:
            self._model_menu.configure(values=values)
            default = get_default_model(provider_id)
            if default:
                self._model_var.set(default.name)
            else:
                self._model_var.set(values[0])

    def _on_provider_changed(self, choice: str):
        provider_map = {}
        for p in get_provider_names():
            provider_map[p["name"]] = p["id"]
        pid = provider_map.get(choice, "jingdu")
        self._refresh_model_list(pid)
        self._on_model_changed(self._model_var.get())

    def _on_model_changed(self, choice: str):
        logger.info(f"[Agent] 模型切换: {choice}")
        # 仅在 jingdu 且模型支持推理时显示思考开关
        model_info = None
        for m in get_model_catalog():
            if m.name == choice:
                model_info = m
                break

        if model_info and model_info.provider_id == "jingdu" and model_info.supports_reasoning:
            self._thinking_check.pack(side=ctk.LEFT, padx=(0, 4), after=self._model_menu)
            self._thinking_var.set(model_info.thinking_default)
            if model_info.thinking_default:
                self._effort_menu.pack(side=ctk.LEFT, padx=(0, 4), after=self._thinking_check)
            else:
                self._effort_menu.pack_forget()
        else:
            self._thinking_check.pack_forget()
            self._effort_menu.pack_forget()
            self._thinking_var.set(False)

        # 更新 provider 的 thinking 设置
        if self._provider and hasattr(self._provider, 'thinking_enabled'):
            self._provider.thinking_enabled = self._thinking_var.get()

    def _on_thinking_toggled(self):
        thinking_on = self._thinking_var.get()
        if thinking_on:
            self._effort_menu.pack(side=ctk.LEFT, padx=(0, 4), after=self._thinking_check)
        else:
            self._effort_menu.pack_forget()
        if self._provider and hasattr(self._provider, 'thinking_enabled'):
            self._provider.thinking_enabled = thinking_on

    # ============ 会话管理 ============

    def _refresh_session_list(self):
        for w in self._session_list_frame.winfo_children():
            w.destroy()

        sessions = AgentSession.list_all()
        for s in sessions:
            sid = s["id"]
            title = s.get("title", "无标题")
            if len(title) > 18:
                title = title[:18] + "..."

            frame = ctk.CTkFrame(self._session_list_frame, fg_color=COLORS["bg_medium"], corner_radius=6)
            frame.pack(fill=ctk.X, pady=2, padx=2)

            btn = ctk.CTkButton(frame, text=title, height=26,
                                 font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                                 fg_color="transparent", hover_color=COLORS["bg_light"],
                                 text_color=COLORS["text_primary"], anchor=ctk.W,
                                 command=lambda sid=sid: self._on_load_session(sid))
            btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(4, 0))

            del_btn = ctk.CTkButton(frame, text="X", width=22, height=22,
                                     font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                                     fg_color="transparent", hover_color=COLORS["error"],
                                     text_color=COLORS["text_secondary"],
                                     command=lambda sid=sid: self._on_delete_session(sid))
            del_btn.pack(side=ctk.RIGHT, padx=(0, 4))

    def _on_new_session(self):
        if self._session:
            self._session.save()
        system_prompt = get_system_prompt() + get_skills_context_text()
        self._session = AgentSession.create_new(system_prompt=system_prompt)
        self._clear_display()
        self._append_system_message(_("agent_new_session_created"))
        self._refresh_session_list()
        self._refresh_todos()

    def _on_load_session(self, session_id: str):
        self._session.save() if self._session else None
        loaded = AgentSession.load(session_id)
        if loaded:
            self._session = loaded
            self._clear_display()
            self._append_system_message(_("agent_session_loaded"))
            for msg in loaded.messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role == "user":
                    self._append_message("user", content)
                elif role == "assistant":
                    self._append_message("assistant", content)
            self._refresh_todos()

    def _on_delete_session(self, session_id: str):
        confirmed = show_confirmation(_("agent_confirm_delete_session"), title="FMCL")
        if confirmed:
            AgentSession.delete(session_id)
            if self._session and self._session.id == session_id:
                self._on_new_session()
            self._refresh_session_list()

    # ============ 消息渲染 ============

    def _append_message(self, role: str, text: str, tag: str = ""):
        role_tag = {"user": "🧑", "assistant": "🤖", "system": "⚙️", "tool": "🔧"}.get(role, "💬")
        prefix = f"{role_tag} "
        if tag:
            prefix += f"[{tag}] "
        self._safe_insert(f"{prefix}{text}\n\n", role)

    def _append_system_message(self, text: str):
        self._safe_insert(f"⚙️ {text}\n\n", "system")

    def _append_divider(self):
        self._safe_insert("─" * 50 + "\n\n", "divider")

    def _append_thinking(self, text: str):
        self._safe_insert(f"💭 {text}\n", "thinking")

    def _append_tool_start(self, name: str):
        self._safe_insert(f"🔧 正在调用 {name}...\n\n", "tool")

    def _append_tool_result(self, name: str, summary: str):
        self._safe_insert(f"🔧 [{name}]\n{summary}\n\n", "tool")

    def _safe_insert(self, text: str, tag: str = ""):
        try:
            self._msg_display.configure(state=ctk.NORMAL)
            if tag:
                self._msg_display.insert(ctk.END, text, tag)
            else:
                self._msg_display.insert(ctk.END, text)
            self._msg_display.configure(state=ctk.DISABLED)
            self._msg_display.see(ctk.END)
        except Exception:
            pass

    def _clear_display(self):
        try:
            self._msg_display.configure(state=ctk.NORMAL)
            self._msg_display.delete("1.0", ctk.END)
            self._msg_display.configure(state=ctk.DISABLED)
        except Exception:
            pass

    # ============ Todo 面板 ============

    def _refresh_todos(self):
        for w in self._todo_frame.winfo_children():
            w.destroy()
        todos = load_todos(self._session.id) if self._session else []
        status_icons = {"pending": "⬜", "in_progress": "🔄", "completed": "✅"}
        if not todos:
            self._todo_empty_label = ctk.CTkLabel(
                self._todo_frame, text=_("agent_todo_empty"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
            )
            self._todo_empty_label.pack(pady=20)
            return
        for t in todos:
            icon = status_icons.get(t.get("status", "pending"), "⬜")
            text = f"{icon} {t.get('content', '')}"
            ctk.CTkLabel(
                self._todo_frame, text=text[:50],
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_primary"], wraplength=160,
                justify=ctk.LEFT, anchor=ctk.W,
            ).pack(fill=ctk.X, pady=2, padx=4, anchor=ctk.W)

    # ============ 外部接口 ============

    def set_provider(self, provider: BaseProvider):
        self._provider = provider

    def set_callbacks(self, callbacks: Dict[str, Callable]):
        self._callbacks = callbacks

    def send_message(self, text: str):
        text = text.strip()
        if not text:
            return
        if not self._provider:
            self._append_system_message(_("agent_no_token_hint"))
            return
        self._input_entry.delete(0, ctk.END)
        self._send_btn.configure(state=ctk.DISABLED, text=_("agent_thinking"))
        self._progress_bar.pack(fill=ctk.X, padx=6, pady=(0, 2))
        self._progress_bar.start()
        self._append_message("user", text)
        self._append_divider()
        self._session.add_message({"role": "user", "content": text})
        if not self._session.title:
            self._session.set_title(text)
        self._session.save()
        _trigger_agent_ach("agent_first_chat")
        threading.Thread(target=self._process_ai_loop, daemon=True, name="AgentAI").start()

    def _on_send(self):
        user_input = self._input_entry.get().strip()
        if user_input:
            self.send_message(user_input)

    # ============ AI 处理循环（流式 + 非流式）============

    def _process_ai_loop(self):
        max_iterations = self.MAX_ITERATIONS
        iteration = 0
        empty_count = 0
        logger.info("[Agent] === AI 处理循环开始（流式模式）===")

        try:
            while iteration < max_iterations:
                iteration += 1
                if iteration > 1:
                    _trigger_agent_ach("agent_multi_turn")
                logger.info(f"[Agent] --- 迭代 {iteration}/{max_iterations} ---")

                # 自动压缩
                if iteration % 10 == 0 and self._session.estimate_tokens() > 60000:
                    self._session.compact()

                # 流式调用
                tools = self._registry.get_definitions()
                provider_id, model_id = self._get_active_model()
                model_name = model_id or "deepseek-v4-flash"

                provider = self._provider
                if provider is None:
                    self.after(0, lambda: self._append_system_message("未配置 AI 提供商"))
                    break

                try:
                    # 同步 DeepSeek 思考模式设置
                    if hasattr(provider, 'thinking_enabled'):
                        provider.thinking_enabled = self._thinking_var.get()
                    if hasattr(provider, 'reasoning_effort'):
                        provider.reasoning_effort = self._effort_var.get()
                    stream_gen = provider.stream_chat(
                        messages=self._session.messages,
                        tools=tools,
                        model=model_name,
                    )
                except Exception as e:
                    logger.error(f"[Agent] 启动流式调用失败: {e}")
                    # 回退到非流式
                    try:
                        response = provider.chat(messages=self._session.messages, tools=tools, model=model_name)
                        self._handle_non_stream_response(response)
                    except Exception as e2:
                        self.after(0, lambda e=str(e2): self._append_system_message(f"AI 调用失败: {e}"))
                        break
                    continue

                # 处理流式事件
                content, tool_calls, needs_break = self._handle_stream_events(stream_gen)

                if needs_break:
                    return  # 等待用户确认

                # 追加 assistant 消息
                if content or tool_calls:
                    msg = {"role": "assistant", "content": content}
                    if tool_calls:
                        msg["tool_calls"] = tool_calls
                    self._session.add_message(msg)

                # 执行工具调用
                if tool_calls:
                    logger.info(f"[Agent] 模型请求调用 {len(tool_calls)} 个工具")
                    all_ok = True
                    for tc in tool_calls:
                        result = self._execute_tool_call(tc)
                        if result is False:
                            all_ok = False
                            break
                        elif isinstance(result, str) and result == "WAIT_USER":
                            self._session.save()
                            return

                    if not all_ok:
                        continue

                    self._session.save()
                    continue
                else:
                    logger.info("[Agent] 任务完成")
                    self.after(0, self._append_divider)
                    show_notification("🤖", _("notify_ai_task_done"), "", notify_type="success")
                    _trigger_agent_ach("agent_nlp_master")
                    break

        except Exception as e:
            logger.error(f"[Agent] 处理循环异常: {e}", exc_info=True)
            show_notification("🤖", _("notify_ai_task_failed"), str(e)[:50], notify_type="error")
        finally:
            logger.info("[Agent] === AI 处理循环结束 ===")
            self.after(0, self._reset_send_button)
            self._session.save()
            self.after(0, self._refresh_todos)
            self.after(0, self._refresh_session_list)
            self.after(0, lambda: self._token_label.configure(
                text=f"Token ~{self._session.estimate_tokens():,}"
            ))

    def _handle_stream_events(self, stream_gen) -> tuple:
        """处理流式事件

        Returns:
            (content_text, tool_calls_list, needs_break)
        """
        accumulated_text = ""
        tool_calls = []
        current_tool_call = None

        try:
            for event in stream_gen:
                if not isinstance(event, dict):
                    continue

                event_type = event.get("type", "")

                if event_type == "text_delta":
                    text = event.get("text", "")
                    accumulated_text += text
                    self.after(0, lambda t=text: self._safe_insert(t, "assistant"))

                elif event_type == "thinking_delta":
                    thinking_text = event.get("text", "")
                    self.after(0, lambda t=thinking_text: self._append_thinking(t))

                elif event_type == "tool_call_start":
                    tc_id = event.get("tool_call_id", "")
                    current_tool_call = {"id": tc_id, "type": "function", "function": {"name": "", "arguments": ""}}
                    tool_calls.append(current_tool_call)

                elif event_type == "tool_call_name":
                    if current_tool_call:
                        current_tool_call["function"]["name"] = event.get("tool_name", "")
                    self.after(0, lambda n=event.get("tool_name", ""): self._append_tool_start(n))

                elif event_type == "tool_call_args":
                    if current_tool_call:
                        current_tool_call["function"]["arguments"] += event.get("tool_args", "")

                elif event_type == "tool_call_complete":
                    tc = event.get("tool_call", {})
                    # 替换当前 tool_call
                    for i, t in enumerate(tool_calls):
                        if t["id"] == tc.get("id"):
                            tool_calls[i] = tc
                            break

                elif event_type == "usage":
                    usage = event.get("usage", {})
                    total = usage.get("total_tokens", 0)
                    self.after(0, lambda t=total: self._token_label.configure(text=f"Token: {t}"))

                elif event_type == "done":
                    self.after(0, self._append_divider)

                elif event_type == "error":
                    err_msg = event.get("message", "未知错误")
                    self.after(0, lambda e=err_msg: self._append_system_message(f"❌ {e}"))
                    return accumulated_text, tool_calls, True

        except Exception as e:
            logger.error(f"[Agent] 流式处理异常: {e}")
            self.after(0, lambda e=str(e): self._append_system_message(f"流式错误: {e}"))

        return accumulated_text, tool_calls, False

    def _handle_non_stream_response(self, response: Dict):
        """处理非流式响应"""
        content = response.get("content", "")
        if content:
            self.after(0, lambda t=content: self._append_message("assistant", t) if content else None)
        tool_calls = response.get("tool_calls", [])
        if tool_calls:
            self._session.add_message(response)

    def _execute_tool_call(self, tc: Dict):
        """执行单个工具调用"""
        func = tc.get("function", {})
        tool_name = func.get("name", "")
        tool_call_id = tc.get("id", "")

        try:
            tool_params = json.loads(func.get("arguments", "{}"))
        except (json.JSONDecodeError, TypeError):
            tool_params = {}

        logger.info(f"[Agent] 工具调用: {tool_name}")

        # 权限检查
        effect = check_permission(tool_name)
        if effect == "deny":
            self.after(0, lambda: self._append_system_message(f"🔒 工具 {tool_name} 被策略禁止"))
            self._session.add_message({
                "role": "tool", "tool_call_id": tool_call_id,
                "content": f"工具 {tool_name} 被权限策略禁止",
            })
            return True
        if effect == "ask" and tool_name == "exec_command":
            result_text = self._registry.execute(tool_name, tool_params, self._callbacks)
            if result_text.startswith(DANGEROUS_MARKER):
                parts = result_text.split("|", 2)
                if len(parts) >= 3:
                    self._pending_dangerous = (parts[1], parts[2], tool_call_id)
                    self.after(0, lambda p=parts: self._show_dangerous_dialog(p[1], p[2]))
                    return "WAIT_USER"
            self._session.add_message({
                "role": "tool", "tool_call_id": tool_call_id, "content": result_text,
            })
            self.after(0, lambda n=tool_name, r=result_text: self._append_tool_result(n, r[:300]))
            return True

        # 执行工具
        result_text = self._registry.execute(tool_name, tool_params, self._callbacks)

        # 检测 ask_user
        if result_text.startswith(ASK_USER_MARKER):
            try:
                rest = result_text[len(ASK_USER_MARKER) + 1:]
                questions = json.loads(rest)
                if isinstance(questions, list):
                    self._pending_ask_user = (questions, tool_call_id)
                    self.after(0, lambda q=questions: self._show_ask_user_dialog(q))
                    return "WAIT_USER"
            except json.JSONDecodeError:
                pass

        self._session.add_message({
            "role": "tool", "tool_call_id": tool_call_id, "content": result_text,
        })
        self.after(0, lambda n=tool_name, r=result_text: self._append_tool_result(n, r[:300]))
        return True

    def _show_dangerous_dialog(self, path: str, command: str):
        if not self._pending_dangerous:
            return
        _trigger_agent_ach("agent_terminal_warrior")
        confirmed = show_confirmation(
            _("agent_exec_dangerous_warning", command=command[:100], path=path),
            title=_("agent_exec_dangerous_title"),
        )
        self._on_dangerous_command_confirmed(confirmed)

    def _on_dangerous_command_confirmed(self, confirmed: bool):
        if not self._pending_dangerous:
            return
        ep, ec, tc_id = self._pending_dangerous
        self._pending_dangerous = None
        if confirmed:
            result_text = execute_dangerous_command(ep, ec)
        else:
            result_text = f"⚠️ 用户取消了命令执行\n路径: {ep}\n命令: {ec}"
        self._session.add_message({
            "role": "tool", "tool_call_id": tc_id, "content": result_text,
        })
        self.after(0, lambda r=result_text: self._append_tool_result("exec_command", r[:300]))
        self._send_btn.configure(state=ctk.DISABLED, text=_("agent_thinking"))
        threading.Thread(target=self._process_ai_loop, daemon=True, name="AgentAI").start()

    def _show_ask_user_dialog(self, questions: List[dict]):
        OptionSelectDialog(
            self.winfo_toplevel(),
            questions=questions,
            callback=self._on_ask_user_response,
        )

    def _on_ask_user_response(self, answers: List[str]):
        if not self._pending_ask_user:
            return
        questions, tc_id = self._pending_ask_user
        self._pending_ask_user = None
        if not answers:
            answers = ["用户未回答"]
        self._session.add_message({
            "role": "tool", "tool_call_id": tc_id,
            "content": f"用户回答: {', '.join(answers)}",
        })
        self.after(0, lambda a=answers: self._append_message("user", f"选择: {', '.join(a)}"))
        self._send_btn.configure(state=ctk.DISABLED, text=_("agent_thinking"))
        threading.Thread(target=self._process_ai_loop, daemon=True, name="AgentAI").start()

    def _get_active_model(self) -> tuple:
        """获取当前活跃模型 (provider_id, model_id)"""
        provider_name = self._provider_var.get()
        model_name = self._model_var.get()
        provider_map = {}
        for p in get_provider_names():
            provider_map[p["name"]] = p["id"]
        pid = provider_map.get(provider_name, "jingdu")
        # 查找 model_id
        for m in get_model_catalog():
            if m.name == model_name and m.provider_id == pid:
                return pid, m.id
        return pid, "deepseek-v4-flash"

    def _reset_send_button(self):
        try:
            self._progress_bar.stop()
            self._progress_bar.pack_forget()
        except Exception:
            pass
        self._send_btn.configure(state=ctk.NORMAL, text=_("agent_send"))

    def update_credits(self, credits: int):
        self.after(0, lambda: self._credits_label.configure(text=_("agent_ai_credits", credits=credits)))

