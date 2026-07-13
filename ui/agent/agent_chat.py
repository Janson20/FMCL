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
import os
import re
import subprocess
import threading
import time
import tkinter as tk
from typing import Any, Callable, Dict, Generator, List, Optional

import customtkinter as ctk
import markdown
from logzero import logger

try:
    from tkinterweb import HtmlFrame

    _HAVE_HTMLFRAME = True
except Exception:
    _HAVE_HTMLFRAME = False

try:
    from tkhtmlview import HTMLScrolledText

    _HAVE_HTMLVIEW = True
except Exception:
    _HAVE_HTMLVIEW = False

from ui.agent.models import ModelInfo, get_default_model, get_model_catalog, get_models_by_provider, get_provider_names
from ui.agent.permission import check_permission
from ui.agent.provider import BaseProvider
from ui.agent.providers.anthropic import AnthropicProvider
from ui.agent.providers.custom import CustomProvider
from ui.agent.providers.jingdu import JingduProvider
from ui.agent.providers.openai import OpenAIProvider
from ui.agent.session import AgentSession
from ui.agent.skill import _get_skills_dir, get_skills_context_text, load_all_skills
from ui.agent.system_prompt import get_system_prompt
from ui.agent.tool_registry import ToolRegistry, get_registry
from ui.agent.tools.files import FILE_EDIT_MARKER
from ui.agent.tools.system import DANGEROUS_MARKER, execute_dangerous_command
from ui.agent.tools.todo_write import load_todos
from ui.agent.tools.user import ASK_USER_MARKER
from ui.constants import COLORS, FONT_FAMILY
from ui.dialogs import show_confirmation, show_notification
from ui.i18n import _


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

        ctk.CTkLabel(
            main,
            text=question,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_primary"],
            wraplength=460,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(0, 12))

        self._entry = ctk.CTkEntry(
            main,
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
        )
        self._entry.pack(fill=ctk.X, pady=(0, 15))
        self._entry.focus_set()
        self._entry.bind("<Return>", lambda e: self._on_confirm())

        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(fill=ctk.X)

        ctk.CTkButton(
            btn_frame,
            text=_("agent_cancel"),
            width=80,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_cancel,
        ).pack(side=ctk.LEFT)
        ctk.CTkButton(
            btn_frame,
            text=_("agent_send"),
            width=80,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_confirm,
        ).pack(side=ctk.RIGHT)

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

        ctk.CTkLabel(
            self._main,
            text=title,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
            wraplength=520,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(0, 12))

        if multi:
            ctk.CTkLabel(
                self._main,
                text=_("agent_multi_select_hint"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
            ).pack(anchor=ctk.W, pady=(0, 5))

        for opt in options:
            label = opt.get("label", "")
            desc = opt.get("description", "")

            if multi:
                var = ctk.BooleanVar()
                self._selected_opts.append((label, var))

                cb = ctk.CTkCheckBox(
                    self._main,
                    text=f"{label}  -  {desc}" if desc else label,
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
                self._main,
                height=32,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color=COLORS["bg_medium"],
                border_color=COLORS["card_border"],
                placeholder_text=_("agent_custom_answer_hint"),
            )
            self._custom_entry.pack(fill=ctk.X, pady=(10, 5))

        nav_frame = ctk.CTkFrame(self._main, fg_color="transparent")
        nav_frame.pack(fill=ctk.X, pady=(10, 0))

        if self._current_idx > 0:
            ctk.CTkButton(
                nav_frame,
                text=_("agent_prev"),
                width=80,
                height=30,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color=COLORS["bg_medium"],
                hover_color=COLORS["bg_light"],
                command=self._on_prev,
            ).pack(side=ctk.LEFT)

        next_label = _("agent_next") if self._current_idx < len(self._questions) - 1 else _("agent_send")
        ctk.CTkButton(
            nav_frame,
            text=next_label,
            width=80,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_next,
        ).pack(side=ctk.RIGHT)

        ctk.CTkLabel(
            nav_frame,
            text=f"{self._current_idx + 1}/{len(self._questions)}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.RIGHT, padx=(0, 15))

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


# ============ 文件编辑确认弹窗 ============


class FileEditConfirmDialog(ctk.CTkToplevel):
    """文件编辑确认弹窗 - 显示 diff 预览，用户确认后执行"""

    def __init__(self, parent, confirm_data: dict, op_type: str, summary: str, callback: Callable[[bool], None]):
        super().__init__(parent)
        self._callback = callback

        self.title("FMCL - 确认文件操作")
        self.configure(fg_color=COLORS["bg_dark"])
        try:
            self.grab_set()
        except Exception:
            pass

        file_path = confirm_data.get("filePath", "")
        op_labels = {"write": "创建/覆盖文件", "replace": "替换文件内容", "delete": "删除文件"}
        op_label = op_labels.get(op_type, "文件操作")

        # 计算窗口高度
        line_count = min(summary[:600].count("\n") + 5, 30)
        h = min(200 + line_count * 18, 680)
        w = 620
        self.geometry(f"{w}x{h}")
        self.resizable(False, False)
        self.update_idletasks()
        x = (self.winfo_screenwidth() - w) // 2
        y = max(0, (self.winfo_screenheight() - h) // 2)
        self.geometry(f"+{x}+{y}")

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        # 标题行
        ctk.CTkLabel(
            main,
            text=op_label,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, pady=(0, 2))
        ctk.CTkLabel(
            main,
            text=f"文件: {file_path}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(0, 10))

        # Diff 内容区（可滚动）
        text_box = ctk.CTkTextbox(
            main,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=COLORS["bg_medium"],
            text_color=COLORS["text_primary"],
            wrap=ctk.NONE,
        )
        text_box.pack(fill=ctk.BOTH, expand=True, pady=(0, 12))
        text_box.insert("1.0", summary[:3000])
        text_box.configure(state=ctk.DISABLED)

        # 按钮
        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(fill=ctk.X)

        ctk.CTkButton(
            btn_frame,
            text="取消",
            width=90,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_cancel,
        ).pack(side=ctk.LEFT)

        ctk.CTkButton(
            btn_frame,
            text="确认执行",
            width=90,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_confirm,
        ).pack(side=ctk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_confirm(self):
        self.grab_release()
        self.destroy()
        if self._callback:
            self._callback(True)

    def _on_cancel(self):
        self.grab_release()
        self.destroy()
        if self._callback:
            self._callback(False)


# ============ Skill 管理弹窗 ============


class SkillManageDialog(ctk.CTkToplevel):
    """Skill 管理弹窗 - 查看/创建/打开技能"""

    def __init__(self, parent, on_changed: Callable[[], None] = None):
        super().__init__(parent)
        self._on_changed = on_changed

        self.title(_("skills_title"))
        self.configure(fg_color=COLORS["bg_dark"])
        try:
            self.grab_set()
        except Exception:
            pass

        w, h = 520, 420
        self.geometry(f"{w}x{h}")
        self.resizable(False, False)
        self.update_idletasks()
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"+{x}+{y}")

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        # 标题
        ctk.CTkLabel(
            main,
            text="Skill 管理",
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, pady=(0, 4))
        ctk.CTkLabel(
            main,
            text="Skill 是 AI 可加载的专用指令文件。创建 SKILL.md 在对应目录即可。",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=460,
        ).pack(anchor=ctk.W, pady=(0, 12))

        # Skill 列表区域
        list_frame = ctk.CTkScrollableFrame(main, fg_color=COLORS["bg_medium"], corner_radius=8)
        list_frame.pack(fill=ctk.BOTH, expand=True, pady=(0, 12))

        self._skills_dir = _get_skills_dir()
        self._refresh_skill_list(list_frame)

        # 底部按钮
        btn_frame = ctk.CTkFrame(main, fg_color="transparent")
        btn_frame.pack(fill=ctk.X)

        ctk.CTkButton(
            btn_frame,
            text=_("skills_new"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_new_skill,
        ).pack(side=ctk.LEFT)

        ctk.CTkButton(
            btn_frame,
            text=_("skills_open_dir"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=lambda: self._open_dir(self._skills_dir),
        ).pack(side=ctk.LEFT, padx=(8, 0))

        ctk.CTkButton(
            btn_frame,
            text="关闭",
            width=80,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self.destroy,
        ).pack(side=ctk.RIGHT)

        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _refresh_skill_list(self, list_frame: ctk.CTkScrollableFrame):
        for w in list_frame.winfo_children():
            w.destroy()

        skills = load_all_skills()
        if not skills:
            ctk.CTkLabel(
                list_frame,
                text="暂无 Skill\n\n在 skills 目录下创建子目录并添加 SKILL.md 文件即可",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
                wraplength=440,
            ).pack(pady=30)
            return

        for skill in skills:
            row = ctk.CTkFrame(list_frame, fg_color=COLORS["bg_dark"], corner_radius=6)
            row.pack(fill=ctk.X, pady=3, padx=4)

            info_frame = ctk.CTkFrame(row, fg_color="transparent")
            info_frame.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=8, pady=6)

            ctk.CTkLabel(
                info_frame,
                text=skill.name,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X)
            ctk.CTkLabel(
                info_frame,
                text=skill.description[:80],
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X)
            if skill.files:
                ctk.CTkLabel(
                    info_frame,
                    text=f"附件: {len(skill.files)} 个文件",
                    font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                    text_color=COLORS["text_secondary"],
                    anchor=ctk.W,
                ).pack(fill=ctk.X)

            ctk.CTkButton(
                row,
                text="打开",
                width=50,
                height=24,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                fg_color=COLORS["bg_medium"],
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_primary"],
                command=lambda d=skill.directory: self._open_dir(d),
            ).pack(side=ctk.RIGHT, padx=(4, 6), pady=4)

    def _on_new_skill(self):
        """新建技能弹窗"""
        dialog = TextInputDialog(
            self, title=_("skills_new"), question="输入技能名称（英文，无空格）：", callback=self._create_skill
        )

    def _create_skill(self, name: str):
        name = name.strip().lower().replace(" ", "-")
        if not name:
            return
        skill_dir = os.path.join(self._skills_dir, name)
        if os.path.exists(skill_dir):
            show_notification("Skill", f"技能 '{name}' 已存在", notify_type="warning")
            return
        try:
            os.makedirs(skill_dir, exist_ok=True)
            template = f"# Skill: {name}\n\n"
            template += f"此技能为 '{name}' 的使用说明。\n\n"
            template += "## 用途\n\n描述此技能的用途。\n\n"
            template += "## 指令\n\nAI 加载此技能后应遵循的指令。\n"
            with open(os.path.join(skill_dir, "SKILL.md"), "w", encoding="utf-8") as f:
                f.write(template)
            show_notification("Skill", f"已创建技能: {name}", notify_type="success")
            self._open_dir(skill_dir)
            self.destroy()
            if self._on_changed:
                self._on_changed()
        except Exception as e:
            logger.error(f"[Skill] 创建技能失败: {e}")
            show_notification("Skill", f"创建失败: {e}", notify_type="error")

    @staticmethod
    def _open_dir(path: str):
        """在文件管理器中打开目录"""
        try:
            if os.path.isdir(path):
                os.startfile(path)
        except Exception:
            try:
                subprocess.Popen(["explorer", path])
            except Exception:
                pass


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
        self._ai_processing = False
        self._pending_dangerous = None
        self._pending_ask_user = None
        self._pending_file_edit = None
        self._thinking_visible = True  # 默认展开思考

        self._registry = get_registry()

        # 消息块必须在 _init_session 之前初始化（_init_session 会追加消息）
        self._message_blocks: List[dict] = []
        self._streaming_block: Optional[dict] = None
        self._render_scheduled = False

        self._init_session()
        self._build_ui()
        self._refresh_session_list()

    def _init_session(self):
        """初始化或加载会话"""
        system_prompt = get_system_prompt() + get_skills_context_text()
        self._session = AgentSession.create_new(
            provider_id="jingdu", model_id="deepseek-v4-flash", system_prompt=system_prompt
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
        ctk.CTkLabel(
            header,
            text=_("agent_provider") + ":",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT, padx=(10, 4))

        self._provider_var = ctk.StringVar(value="净读 AI")
        providers = get_provider_names()
        provider_names = [p["name"] for p in providers]
        self._provider_menu = ctk.CTkOptionMenu(
            header,
            values=provider_names,
            variable=self._provider_var,
            width=100,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_medium"],
            command=self._on_provider_changed,
        )
        self._provider_menu.pack(side=ctk.LEFT, padx=(0, 8))

        # Model 选择
        self._model_var = ctk.StringVar(value="deepseek-v4-flash")
        self._model_menu = ctk.CTkOptionMenu(
            header,
            values=[],
            variable=self._model_var,
            width=160,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_medium"],
            command=self._on_model_changed,
        )
        self._model_menu.pack(side=ctk.LEFT, padx=(0, 4))
        self._refresh_model_list("jingdu")

        # 思考模式开关（仅 DeepSeek V4 模型可见）
        self._thinking_var = ctk.BooleanVar(value=False)  # 默认非思考
        self._thinking_check = ctk.CTkCheckBox(
            header,
            text="思考",
            variable=self._thinking_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_color=COLORS["text_secondary"],
            checkmark_color=COLORS["bg_dark"],
            command=self._on_thinking_toggled,
            width=20,
            height=20,
        )
        # 初态：jingdu V4 Flash 默认不显示（thinking_default=False），模型切换后再决定
        self._thinking_check.pack_forget()

        # reasoning_effort（思考强度，思考开启时显示）
        self._effort_var = ctk.StringVar(value="high")
        self._effort_menu = ctk.CTkOptionMenu(
            header,
            values=["high", "max"],
            variable=self._effort_var,
            width=60,
            height=22,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["accent"],
            dropdown_fg_color=COLORS["bg_medium"],
        )
        self._effort_menu.pack_forget()

        # Skills 管理按钮
        ctk.CTkButton(
            header,
            text="Skills",
            width=50,
            height=26,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            command=self._on_manage_skills,
        ).pack(side=ctk.RIGHT, padx=(0, 6))

        # 新会话按钮
        ctk.CTkButton(
            header,
            text=_("agent_new_session"),
            width=70,
            height=26,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_new_session,
        ).pack(side=ctk.RIGHT, padx=(0, 10))

        # 积分显示
        self._credits_label = ctk.CTkLabel(
            header, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=COLORS["text_secondary"]
        )
        self._credits_label.pack(side=ctk.RIGHT, padx=(0, 8))

    def _build_session_panel(self, parent):
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=10, width=160)
        panel.pack(side=ctk.LEFT, fill=ctk.BOTH, padx=(0, 5))
        panel.pack_propagate(False)

        ctk.CTkLabel(
            panel,
            text=_("agent_sessions"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=8, pady=(8, 4), anchor=ctk.W)

        self._session_list_frame = ctk.CTkScrollableFrame(panel, fg_color="transparent")
        self._session_list_frame.pack(fill=ctk.BOTH, expand=True, padx=4, pady=(0, 4))

    def _build_chat_panel(self, parent):
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=10)
        panel.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 5))

        # 消息显示区 - HtmlFrame → HTMLScrolledText → CTkTextbox 三级回退
        self._msg_frame = None
        if _HAVE_HTMLFRAME:
            try:
                self._msg_frame = HtmlFrame(
                    panel,
                    messages_enabled=False,
                    images_enabled=False,
                    forms_enabled=False,
                    objects_enabled=False,
                    javascript_enabled=False,
                    dark_theme_enabled=True,
                    vertical_scrollbar=True,
                )
            except Exception:
                self._msg_frame = None
        if self._msg_frame is None and _HAVE_HTMLVIEW:
            try:
                self._msg_frame = HTMLScrolledText(
                    panel,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                    background=COLORS["bg_medium"],
                    foreground=COLORS["text_primary"],
                    padx=6,
                    pady=6,
                )
            except Exception:
                self._msg_frame = None
        if self._msg_frame is None:
            self._msg_frame = ctk.CTkTextbox(
                panel,
                wrap=ctk.WORD,
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                fg_color=COLORS["bg_medium"],
                text_color=COLORS["text_primary"],
            )
        self._msg_frame.pack(fill=ctk.BOTH, expand=True, padx=6, pady=(6, 4))

        # 进度指示
        self._progress_bar = ctk.CTkProgressBar(
            panel, height=4, fg_color=COLORS["bg_medium"], progress_color=COLORS["accent"], mode="indeterminate"
        )

        # 输入框
        input_frame = ctk.CTkFrame(panel, fg_color="transparent", height=44)
        input_frame.pack(fill=ctk.X, padx=6, pady=(2, 6))
        input_frame.pack_propagate(False)

        self._input_entry = ctk.CTkEntry(
            input_frame,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=_("agent_input_placeholder"),
        )
        self._input_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 6))
        self._input_entry.bind("<Return>", lambda e: self._on_send())

        self._send_btn = ctk.CTkButton(
            input_frame,
            text=_("agent_send"),
            width=70,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_send,
        )
        self._send_btn.pack(side=ctk.RIGHT)

        # 声明
        ctk.CTkLabel(
            panel,
            text=_("agent_disclaimer"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=9),
            text_color=COLORS["text_secondary"],
        ).pack(pady=(0, 4))

        # 渲染 _init_session 时已追加的消息
        self._render_full()

    def _build_todo_panel(self, parent):
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=10, width=180)
        panel.pack(side=ctk.LEFT, fill=ctk.BOTH)
        panel.pack_propagate(False)

        ctk.CTkLabel(
            panel,
            text=_("agent_todo_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=8, pady=(8, 4), anchor=ctk.W)

        self._todo_frame = ctk.CTkScrollableFrame(panel, fg_color="transparent")
        self._todo_frame.pack(fill=ctk.BOTH, expand=True, padx=4, pady=(0, 4))

        self._todo_empty_label = ctk.CTkLabel(
            self._todo_frame,
            text=_("agent_todo_empty"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._todo_empty_label.pack(pady=20)

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self, fg_color=COLORS["card_bg"], corner_radius=8, height=24)
        bar.pack(fill=ctk.X, padx=5, pady=(0, 5))
        bar.pack_propagate(False)

        self._status_label = ctk.CTkLabel(
            bar, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=COLORS["text_secondary"]
        )
        self._status_label.pack(side=ctk.LEFT, padx=(10, 0))

        self._token_label = ctk.CTkLabel(
            bar, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=COLORS["text_secondary"]
        )
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
        if self._provider and hasattr(self._provider, "thinking_enabled"):
            self._provider.thinking_enabled = self._thinking_var.get()

    def _on_thinking_toggled(self):
        thinking_on = self._thinking_var.get()
        if thinking_on:
            self._effort_menu.pack(side=ctk.LEFT, padx=(0, 4), after=self._thinking_check)
        else:
            self._effort_menu.pack_forget()
        if self._provider and hasattr(self._provider, "thinking_enabled"):
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
                display = title[:18] + "..."
            else:
                display = title

            frame = ctk.CTkFrame(self._session_list_frame, fg_color=COLORS["bg_medium"], corner_radius=6)
            frame.pack(fill=ctk.X, pady=2, padx=2)

            # 使用 Label + left-click 代替 CTkButton 以支持右键菜单
            lbl = ctk.CTkLabel(
                frame,
                text=f"💬 {display}",
                height=26,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                fg_color="transparent",
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
                corner_radius=6,
            )
            lbl.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(4, 0))
            lbl.bind("<Button-1>", lambda e, sid=sid: self._on_load_session(sid))
            lbl.bind("<Button-3>", lambda e, sid=sid, t=title: self._on_session_context_menu(e, sid, t))

            del_btn = ctk.CTkButton(
                frame,
                text="X",
                width=22,
                height=22,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                fg_color="transparent",
                hover_color=COLORS["error"],
                text_color=COLORS["text_secondary"],
                command=lambda sid=sid: self._on_delete_session(sid),
            )
            del_btn.pack(side=ctk.RIGHT, padx=(0, 4))

    def _on_session_context_menu(self, event, session_id: str, title: str):
        """右键上下文菜单"""
        menu = tk.Menu(
            self,
            tearoff=0,
            bg=COLORS["bg_medium"],
            fg=COLORS["text_primary"],
            activebackground=COLORS["accent"],
            activeforeground="white",
            font=(FONT_FAMILY, 10),
        )
        menu.add_command(label=_("agent_rename_session"), command=lambda: self._on_rename_session(session_id, title))
        menu.add_separator()
        menu.add_command(label=_("agent_delete_session"), command=lambda: self._on_delete_session(session_id))
        menu.post(event.x_root, event.y_root)

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
            # 从 assistant 的 tool_calls 中提取 tool_name 映射 (by id)
            tool_call_map: dict = {}
            for msg in loaded.messages:
                if msg.get("role") == "assistant":
                    for tc in msg.get("tool_calls") or []:
                        fn = tc.get("function", {})
                        if tc.get("id") and fn.get("name"):
                            tool_call_map[tc["id"]] = fn["name"]

            for msg in loaded.messages:
                role = msg.get("role", "")
                content = msg.get("content", "")

                if role == "system":
                    # 跳过系统提示词等不对外展示的消息
                    pass
                elif role == "user":
                    self._append_message("user", content)
                elif role == "assistant":
                    thinking = msg.get("_thinking", "")
                    if thinking:
                        self._append_thinking(thinking)
                    self._append_message("assistant", content)
                elif role == "tool":
                    # 从 tool_call_id 反查工具名
                    tc_id = msg.get("tool_call_id", "")
                    tool_name = tool_call_map.get(tc_id, "?")
                    self._append_tool_result(tool_name, content[:500])
            self._refresh_todos()

    def _on_delete_session(self, session_id: str):
        confirmed = show_confirmation(_("agent_confirm_delete_session"), title="FMCL")
        if confirmed:
            AgentSession.delete(session_id)
            if self._session and self._session.id == session_id:
                self._session = None
                self._clear_display()
                self._refresh_todos()
            self._refresh_session_list()

    def _on_rename_session(self, session_id: str, old_title: str):
        """重命名会话"""
        dialog = TextInputDialog(
            self.winfo_toplevel(),
            title="重命名会话",
            question=f"请输入新名称:",
            callback=lambda new_name: self._do_rename_session(session_id, new_name),
        )

    def _do_rename_session(self, session_id: str, new_name: str):
        new_name = new_name.strip()
        if not new_name:
            return
        session = AgentSession.load(session_id)
        if session:
            session.title = new_name
            session.save()
            self._refresh_session_list()

    # ============ 消息渲染 (Markdown) ============

    _MD = markdown.Markdown(extensions=["fenced_code", "tables", "nl2br"])
    _MD_LOCK = threading.Lock()

    _CSS = """
    body { margin: 0; padding: 8px; font-family: 'Microsoft YaHei', sans-serif; font-size: 13px;
           color: #d4d4d4; background: #1e1e2e; line-height: 1.6; }
    .msg { margin: 4px 0; padding: 8px 12px; border-radius: 8px; }
    .msg-user { background: #2a2a3e; border-left: 3px solid #7c3aed; }
    .msg-assistant { background: #1e2a1e; border-left: 3px solid #22c55e; }
    .msg-system { background: #2a2a2e; border-left: 3px solid #6b7280; color: #9ca3af; font-size: 12px; }
    .msg-tool { background: #2a2a20; border-left: 3px solid #eab308; font-size: 12px; }
    .msg-thinking { background: #1e1e2a; border-left: 3px dashed #6b7280; color: #9ca3af; font-size: 11px;
                     font-style: italic; max-height: 200px; overflow-y: auto; }
    .msg-divider { border: none; border-top: 1px solid #3f3f5c; margin: 8px 0; }
    .role-icon { font-size: 11px; margin-bottom: 4px; opacity: 0.7; }
    .role-icon span { font-weight: bold; }
    pre { background: #111827; padding: 10px; border-radius: 6px; overflow-x: auto; font-size: 12px; }
    code { background: #374151; padding: 1px 5px; border-radius: 3px; font-size: 12px; }
    pre code { background: none; padding: 0; }
    blockquote { border-left: 3px solid #6b7280; margin: 4px 0; padding: 4px 12px; color: #9ca3af; }
    a { color: #60a5fa; }
    table { border-collapse: collapse; margin: 4px 0; }
    th, td { border: 1px solid #3f3f5c; padding: 4px 8px; }
    th { background: #2a2a3e; }
    ul, ol { padding-left: 20px; }
    h1, h2, h3, h4 { margin: 6px 0 2px; }
    """

    def _html_template(self, body: str) -> str:
        return f"<html><head><style>{self._CSS}</style></head><body>{body}</body></html>"

    def _md_to_html(self, text: str) -> str:
        try:
            with self._MD_LOCK:
                self._MD.reset()
                return self._MD.convert(text)
        except Exception:
            return text.replace("\n", "<br>")

    def _escape_html(self, text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _build_block_html(self, block: dict) -> str:
        role = block.get("role", "system")
        content = block.get("content", "")
        thinking = block.get("thinking", "")
        tag = block.get("tag", "")

        icons = {"user": "🧑 你", "assistant": "🤖 FMCL Agent", "system": "⚙️ 系统", "tool": "🔧 工具"}
        icon = icons.get(role, "💬")
        if tag:
            icon += f" [{tag}]"

        if role in ("assistant",):
            # assistant 用 markdown 渲染
            html = f'<div class="msg msg-{role}"><div class="role-icon">{icon}</div>'
            if thinking:
                html += f'<div class="msg-thinking">{self._escape_html(thinking)}</div>'
            html += self._md_to_html(content) + "</div>"
        elif role in ("system", "tool"):
            html = f'<div class="msg msg-{role}"><div class="role-icon">{icon}</div>{self._escape_html(content)}</div>'
        else:
            # user: 也是 markdown，但简单
            html = f'<div class="msg msg-user"><div class="role-icon">{icon}</div>{self._md_to_html(content)}</div>'
        return html

    @staticmethod
    def _strip_html(html: str) -> str:
        """将 HTML 转换为可读纯文本（用于 CTkTextbox 回退）"""
        text = html
        # 块级标签换行
        text = re.sub(r"</?(div|p|br|hr|h[1-6]|li|tr|td|blockquote)(\s[^>]*)?>", "\n", text, flags=re.IGNORECASE)
        # 其余标签移除
        text = re.sub(r"<[^>]+>", "", text)
        # HTML 实体解码
        text = (
            text.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
        )
        # 折叠多余空行
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def _render_full(self):
        """完整重建消息区（HtmlFrame 加载 HTML / CTkTextbox 显示纯文本）"""
        parts = []
        for block in self._message_blocks:
            if block.get("type") == "divider":
                parts.append('<hr class="msg-divider">')
            else:
                parts.append(self._build_block_html(block))
        # 流式块
        if self._streaming_block:
            parts.append(self._build_block_html(self._streaming_block))

        if hasattr(self._msg_frame, "load_html"):
            html = self._html_template("".join(parts))
            try:
                self._msg_frame.load_html(html)
                self._defer_scroll()
            except Exception:
                pass
        elif hasattr(self._msg_frame, "set_html"):
            try:
                self._msg_frame.set_html("".join(parts))
                self._defer_scroll()
            except Exception:
                pass
        else:
            text = self._strip_html("".join(parts))
            self._msg_frame.configure(state=ctk.NORMAL)
            self._msg_frame.delete("1.0", ctk.END)
            self._msg_frame.insert(ctk.END, text)
            self._msg_frame.configure(state=ctk.DISABLED)
            self._defer_scroll()

    def _defer_scroll(self):
        if hasattr(self, "_scroll_after_id") and self._scroll_after_id:
            self.after_cancel(self._scroll_after_id)
            self._scroll_after_id = None
        self._scroll_after_id = self.after(100, self._scroll_to_bottom)

    def _scroll_to_bottom(self):
        self._scroll_after_id = None
        try:
            self._msg_frame.yview_moveto(1.0)
        except Exception:
            pass

    def _schedule_render(self, force: bool = False):
        # 所有渲染必须通过事件循环，不可在非主线程直接操作 tk widget
        if hasattr(self, "_render_after_id") and self._render_after_id:
            self.after_cancel(self._render_after_id)
            self._render_after_id = None
        self._render_scheduled = True
        delay = 1 if force else 500
        self._render_after_id = self.after(delay, self._do_render)

    def _do_render(self):
        self._render_scheduled = False
        self._render_after_id = None
        self._render_full()

    def _append_message(self, role: str, text: str, tag: str = ""):
        block = {"role": role, "content": text, "tag": tag, "thinking": ""}
        self._message_blocks.append(block)
        self._schedule_render()

    def _append_system_message(self, text: str):
        self._append_message("system", text)

    def _append_divider(self):
        self._message_blocks.append({"type": "divider"})
        self._schedule_render()

    def _append_thinking(self, text: str):
        # 将思考文本拼到最后一个 assistant 块或流式块
        target = self._streaming_block or (self._message_blocks[-1] if self._message_blocks else None)
        if target and target.get("role") == "assistant":
            target["thinking"] = target.get("thinking", "") + text

    def _append_tool_start(self, name: str):
        self._append_message("tool", f"正在调用 {name}...")

    def _append_tool_result(self, name: str, summary: str):
        self._append_message("tool", f"[{name}]\n{summary[:1000]}")

    def _clear_display(self):
        self._message_blocks.clear()
        self._streaming_block = None
        self._schedule_render()

    # ============ Todo 面板 ============

    def _refresh_todos(self):
        for w in self._todo_frame.winfo_children():
            w.destroy()
        todos = load_todos(self._session.id) if self._session else []
        status_icons = {"pending": "⬜", "in_progress": "🔄", "completed": "✅"}
        if not todos:
            self._todo_empty_label = ctk.CTkLabel(
                self._todo_frame,
                text=_("agent_todo_empty"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
            )
            self._todo_empty_label.pack(pady=20)
            return
        for t in todos:
            icon = status_icons.get(t.get("status", "pending"), "⬜")
            text = f"{icon} {t.get('content', '')}"
            ctk.CTkLabel(
                self._todo_frame,
                text=text[:50],
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_primary"],
                wraplength=160,
                justify=ctk.LEFT,
                anchor=ctk.W,
            ).pack(fill=ctk.X, pady=2, padx=4, anchor=ctk.W)

    def _on_manage_skills(self):
        """打开 Skill 管理弹窗"""
        SkillManageDialog(self.winfo_toplevel(), on_changed=self._on_skills_changed)

    def _on_skills_changed(self):
        """Skill 变更后刷新系统提示词中的技能列表"""
        # 重建系统提示词
        if self._session and self._session.messages:
            for i, msg in enumerate(self._session.messages):
                if msg.get("role") == "system":
                    new_prompt = get_system_prompt() + get_skills_context_text()
                    self._session.messages[i] = {"role": "system", "content": new_prompt}
                    break
            else:
                self._session.messages.insert(
                    0, {"role": "system", "content": get_system_prompt() + get_skills_context_text()}
                )

    # ============ 外部接口 ============

    def set_provider(self, provider: BaseProvider):
        self._provider = provider

    def set_callbacks(self, callbacks: Dict[str, Callable]):
        self._callbacks = callbacks

    def is_processing(self) -> bool:
        """返回 AI 是否正在处理任务"""
        return self._ai_processing

    def send_message(self, text: str):
        text = text.strip()
        if not text:
            return
        if not self._provider:
            self._append_system_message(_("agent_no_token_hint"))
            return
        # 会话被删除后自动创建新会话
        if self._session is None:
            system_prompt = get_system_prompt() + get_skills_context_text()
            self._session = AgentSession.create_new(system_prompt=system_prompt)
            self._refresh_session_list()
        self._input_entry.delete(0, ctk.END)
        self._send_btn.configure(state=ctk.DISABLED, text=_("agent_thinking"))
        self._progress_bar.pack(fill=ctk.X, padx=6, pady=(0, 2))
        self._progress_bar.start()
        self._append_message("user", text)
        self._append_divider()
        self._session.add_message({"role": "user", "content": text})
        if not self._session.title:
            self._session.set_title(text)
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
        self._ai_processing = True

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
                    if hasattr(provider, "thinking_enabled"):
                        provider.thinking_enabled = self._thinking_var.get()
                    if hasattr(provider, "reasoning_effort"):
                        provider.reasoning_effort = self._effort_var.get()
                    stream_gen = provider.stream_chat(messages=self._session.messages, tools=tools, model=model_name)
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

                # 处理流式事件 - 先创建流式块
                self._streaming_block = {"role": "assistant", "content": "", "thinking": "", "tag": ""}
                content, tool_calls, needs_break = self._handle_stream_events(stream_gen)
                # 完成流式渲染：将流式块固定为永久消息块
                streaming_thinking = ""
                if self._streaming_block:
                    streaming_thinking = self._streaming_block.get("thinking", "")
                    if self._streaming_block.get("content") or streaming_thinking:
                        self._message_blocks.append(self._streaming_block)
                self._streaming_block = None
                # 流式结束，强制立即渲染最终内容
                self._schedule_render(force=True)

                if needs_break:
                    return  # 等待用户确认

                # 追加 assistant 消息（保留思考过程供历史回放）
                if content or tool_calls:
                    msg = {"role": "assistant", "content": content}
                    if tool_calls:
                        msg["tool_calls"] = tool_calls
                    if streaming_thinking:
                        msg["_thinking"] = streaming_thinking
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

                    # 每次工具调用后立即刷新 Todo 面板
                    self.after(0, self._refresh_todos)

                    if not all_ok:
                        continue

                    # 不在循环中保存，最终由 finally 统一保存
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
            wait_user = bool(self._pending_dangerous or self._pending_ask_user or self._pending_file_edit)
            logger.info(f"[Agent] === AI 处理循环结束 === (wait_user={wait_user})")
            if wait_user:
                # 等待用户确认时，保存会话但不重置 UI（确认回调会启动新循环）
                sess = self._session
                threading.Thread(target=lambda s=sess: s.save(), daemon=True).start()
            else:
                self._ai_processing = False
                self.after(0, self._reset_send_button)
                sess = self._session
                threading.Thread(target=lambda s=sess: s.save(), daemon=True).start()
                self.after(0, self._refresh_todos)
                self.after(0, self._refresh_session_list)
                self.after(0, lambda: self._token_label.configure(text=f"Token ~{self._session.estimate_tokens():,}"))

    def _handle_stream_events(self, stream_gen) -> tuple:
        """处理流式事件 - 更新 _streaming_block + 调度渲染

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
                    if self._streaming_block:
                        self._streaming_block["content"] = accumulated_text
                        self._schedule_render()

                elif event_type == "thinking_delta":
                    thinking_text = event.get("text", "")
                    if self._streaming_block:
                        self._streaming_block["thinking"] = self._streaming_block.get("thinking", "") + thinking_text
                        self._schedule_render()

                elif event_type == "tool_call_start":
                    tc_id = event.get("tool_call_id", "")
                    current_tool_call = {"id": tc_id, "type": "function", "function": {"name": "", "arguments": ""}}
                    tool_calls.append(current_tool_call)

                elif event_type == "tool_call_name":
                    name = event.get("tool_name", "")
                    if current_tool_call:
                        current_tool_call["function"]["name"] = name
                    self._append_tool_start(name)

                elif event_type == "tool_call_args":
                    if current_tool_call:
                        current_tool_call["function"]["arguments"] += event.get("tool_args", "")

                elif event_type == "tool_call_complete":
                    tc = event.get("tool_call", {})
                    for i, t in enumerate(tool_calls):
                        if t["id"] == tc.get("id"):
                            tool_calls[i] = tc
                            break

                elif event_type == "usage":
                    usage = event.get("usage", {})
                    total = usage.get("total_tokens", 0)
                    self.after(0, lambda t=total: self._token_label.configure(text=f"Token: {t}"))

                elif event_type == "done":
                    self._append_divider()

                elif event_type == "error":
                    err_msg = event.get("message", "未知错误")
                    self._append_system_message(f"❌ {err_msg}")
                    return accumulated_text, tool_calls, True

        except Exception as e:
            logger.error(f"[Agent] 流式处理异常: {e}")
            self._append_system_message(f"流式错误: {e}")

        return accumulated_text, tool_calls, False

    def _handle_non_stream_response(self, response: Dict):
        """处理非流式响应"""
        content = response.get("content", "")
        if content:
            self._append_message("assistant", content)
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
            self._append_system_message(f"🔒 工具 {tool_name} 被策略禁止")
            self._session.add_message(
                {"role": "tool", "tool_call_id": tool_call_id, "content": f"工具 {tool_name} 被权限策略禁止"}
            )
            return True
        if effect == "ask" and tool_name == "exec_command":
            result_text = self._registry.execute(tool_name, tool_params, self._callbacks)
            if result_text.startswith(DANGEROUS_MARKER):
                try:
                    rest = result_text[len(DANGEROUS_MARKER) + 1 :]
                    payload = json.loads(rest)
                    path = payload["path"]
                    command = payload["command"]
                    self._pending_dangerous = (path, command, tool_call_id)
                    self.after(0, lambda p=path, c=command: self._show_dangerous_dialog(p, c))
                    return "WAIT_USER"
                except (json.JSONDecodeError, KeyError) as e:
                    logger.error(f"[Agent] DANGEROUS_MARKER 解析失败: {e}")
            self._session.add_message({"role": "tool", "tool_call_id": tool_call_id, "content": result_text})
            self._append_tool_result(tool_name, result_text[:300])
            return True

        if effect == "ask" and tool_name in ("write_file", "replace_in_file", "delete_file"):
            result_text = self._registry.execute(tool_name, tool_params, self._callbacks)
            if result_text.startswith(FILE_EDIT_MARKER):
                try:
                    rest = result_text[len(FILE_EDIT_MARKER) + 1 :]
                    payload = json.loads(rest)
                    confirm_data = payload["data"]
                    op_type = payload["op"]
                    summary = payload.get("summary", "")
                    self._pending_file_edit = (confirm_data, op_type, tool_call_id)
                    self.after(0, lambda c=confirm_data, o=op_type, s=summary: self._show_file_edit_dialog(c, o, s))
                    return "WAIT_USER"
                except (json.JSONDecodeError, KeyError) as e:
                    logger.error(f"[Agent] FILE_EDIT_MARKER 解析失败: {e}, result头部: {result_text[:200]}")
            self._session.add_message({"role": "tool", "tool_call_id": tool_call_id, "content": result_text})
            self._append_tool_result(tool_name, result_text[:300])
            return True

        # 执行工具
        result_text = self._registry.execute(tool_name, tool_params, self._callbacks)

        # 检测 ask_user
        if result_text.startswith(ASK_USER_MARKER):
            try:
                rest = result_text[len(ASK_USER_MARKER) + 1 :]
                questions = json.loads(rest)
                if isinstance(questions, list):
                    self._pending_ask_user = (questions, tool_call_id)
                    self.after(0, lambda q=questions: self._show_ask_user_dialog(q))
                    return "WAIT_USER"
            except json.JSONDecodeError:
                pass

        self._session.add_message({"role": "tool", "tool_call_id": tool_call_id, "content": result_text})
        self._append_tool_result(tool_name, result_text[:300])
        return True

    def _show_dangerous_dialog(self, path: str, command: str):
        if not self._pending_dangerous:
            return
        _trigger_agent_ach("agent_terminal_warrior")
        confirmed = show_confirmation(
            _("agent_exec_dangerous_warning", command=command[:100], path=path), title=_("agent_exec_dangerous_title")
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
        self._session.add_message({"role": "tool", "tool_call_id": tc_id, "content": result_text})
        self._append_tool_result("exec_command", result_text[:300])
        self._send_btn.configure(state=ctk.DISABLED, text=_("agent_thinking"))
        threading.Thread(target=self._process_ai_loop, daemon=True, name="AgentAI").start()

    def _show_file_edit_dialog(self, confirm_data: dict, op_type: str, summary: str):
        """显示文件编辑确认弹窗"""
        if not self._pending_file_edit:
            return
        FileEditConfirmDialog(
            self.winfo_toplevel(),
            confirm_data=confirm_data,
            op_type=op_type,
            summary=summary,
            callback=self._on_file_edit_confirmed,
        )

    def _on_file_edit_confirmed(self, confirmed: bool):
        """处理文件编辑确认结果"""
        if not self._pending_file_edit:
            return
        confirm_data, op_type, tc_id = self._pending_file_edit
        self._pending_file_edit = None
        if confirmed:
            result_text = self._apply_file_edit(confirm_data, op_type)
        else:
            result_text = f"用户取消了文件操作\n类型: {op_type}\n文件: {confirm_data.get('filePath', '')}"
        self._session.add_message({"role": "tool", "tool_call_id": tc_id, "content": result_text})
        self._append_tool_result(op_type, result_text[:300])
        self._send_btn.configure(state=ctk.DISABLED, text=_("agent_thinking"))
        threading.Thread(target=self._process_ai_loop, daemon=True, name="AgentAI").start()

    def _apply_file_edit(self, confirm_data: dict, op_type: str) -> str:
        """实际执行文件修改"""
        file_path = confirm_data.get("filePath", "")
        try:
            if op_type == "write" or op_type == "replace":
                # 确保父目录存在
                parent = os.path.dirname(file_path)
                if parent and not os.path.isdir(parent):
                    os.makedirs(parent, exist_ok=True)
                content = confirm_data.get("newText", "")
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                if op_type == "replace":
                    replacements = confirm_data.get("replacements", 1)
                    return f"已替换 {replacements} 处: {file_path}"
                return f"已写入文件: {file_path}"
            elif op_type == "delete":
                os.remove(file_path)
                return f"已删除文件: {file_path}"
            return f"未知操作类型: {op_type}"
        except Exception as e:
            return f"文件操作失败: {e}"

    def _show_ask_user_dialog(self, questions: List[dict]):
        OptionSelectDialog(self.winfo_toplevel(), questions=questions, callback=self._on_ask_user_response)

    def _on_ask_user_response(self, answers: List[str]):
        if not self._pending_ask_user:
            return
        questions, tc_id = self._pending_ask_user
        self._pending_ask_user = None
        if not answers:
            answers = ["用户未回答"]
        self._session.add_message({"role": "tool", "tool_call_id": tc_id, "content": f"用户回答: {', '.join(answers)}"})
        self._append_message("user", f"选择: {', '.join(answers)}")
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
