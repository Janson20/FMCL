"""AGENT 聊天 UI 组件 - 消息展示 + 输入框"""

import json
import threading
from typing import Dict, List, Optional, Callable, Any
from logzero import logger

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY
from ui.dialogs import show_notification
from ui.i18n import _
from ui.agent.provider import AIProvider
from ui.agent.tools import get_tool_definitions, get_system_prompt
from ui.agent.engine import execute_tool, DANGEROUS_MARKER, ASK_USER_MARKER, execute_dangerous_command
from ui.dialogs import show_confirmation


def _trigger_agent_ach(achievement_id: str, value: int = 1):
    try:
        from achievement_engine import get_achievement_engine
        engine = get_achievement_engine()
        if engine:
            engine.update_progress(achievement_id, value=value)
    except Exception:
        pass


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

        w = 520
        h = self._calc_height(question)

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
        chars_per_line = 33
        line_height = 22
        num_lines = max(1, (len(text) + chars_per_line - 1) // chars_per_line)
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
    """选项选择弹窗"""

    def __init__(self, parent, title: str, options: List[Dict[str, str]], callback: Callable[[str], None]):
        super().__init__(parent)
        self._callback = callback
        self._selected = None

        self.title(title)
        self.configure(fg_color=COLORS["bg_dark"])
        try:
            self.grab_set()
        except Exception:
            pass

        w = 500
        h = min(60 * len(options) + 120, 500)
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
            text=title,
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, pady=(0, 15))

        for opt in options:
            btn = ctk.CTkButton(
                main,
                text=opt["label"],
                height=42,
                font=ctk.CTkFont(family=FONT_FAMILY, size=14),
                fg_color=COLORS["bg_medium"],
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
                command=lambda v=opt["value"]: self._on_select(v),
            )
            btn.pack(fill=ctk.X, pady=3)

    def _on_select(self, value: str):
        self._selected = value
        self.grab_release()
        self.destroy()
        if self._callback:
            self._callback(value)


class AgentChatView(ctk.CTkFrame):
    """AGENT 聊天视图"""

    def __init__(self, parent, callbacks: Dict[str, Callable], **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)

        self._callbacks = callbacks
        self._provider: Optional[AIProvider] = None
        self._messages: List[Dict] = []
        self._running = False
        self._pending_dangerous = None
        self._pending_ask_user = None

        self._init_messages()
        self._build_ui()

    def _init_messages(self):
        self._messages = [
            {"role": "system", "content": get_system_prompt()},
        ]
        logger.info(f"[Agent] 初始化消息列表，已插入系统提示词 (长度={len(get_system_prompt())})")

    def _build_ui(self):
        chat_container = ctk.CTkFrame(self, fg_color="transparent")
        chat_container.pack(fill=ctk.BOTH, expand=True, padx=5, pady=5)

        msg_header = ctk.CTkFrame(chat_container, fg_color="transparent", height=30)
        msg_header.pack(fill=ctk.X, pady=(0, 5))
        msg_header.pack_propagate(False)

        ctk.CTkLabel(
            msg_header,
            text=_("agent_chat_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        clear_btn = ctk.CTkButton(
            msg_header,
            text=_("agent_clear"),
            width=60,
            height=24,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._clear_chat,
        )
        clear_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        self._msg_display = ctk.CTkTextbox(
            chat_container,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            wrap=ctk.WORD,
            state=ctk.DISABLED,
        )
        self._msg_display.pack(fill=ctk.BOTH, expand=True, pady=(0, 8))

        input_frame = ctk.CTkFrame(chat_container, fg_color="transparent", height=50)
        input_frame.pack(fill=ctk.X)
        input_frame.pack_propagate(False)

        self._input_entry = ctk.CTkEntry(
            input_frame,
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=_("agent_input_placeholder"),
        )
        self._input_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._input_entry.bind("<Return>", lambda e: self._on_send())

        self._send_btn = ctk.CTkButton(
            input_frame,
            text=_("agent_send"),
            width=80,
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_send,
        )
        self._send_btn.pack(side=ctk.RIGHT)

        disclaimer_label = ctk.CTkLabel(
            chat_container,
            text=_("agent_disclaimer"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        disclaimer_label.pack(pady=(4, 0))

        self._msg_batch_buffer: list = []
        self._msg_batch_scheduled: bool = False

        self._append_system_message(_("agent_welcome1"))
        self._append_system_message(_("agent_welcome2") + "\n" + _("agent_example1") + "\n" + _("agent_example2") + "\n" + _("agent_example3") + "\n" + _("agent_example4") + "\n" + _("agent_example5") + "\n" + _("agent_example6"))
        self._append_divider()

    def set_provider(self, provider: AIProvider):
        """设置 AI 提供商"""
        logger.info(f"[Agent] 设置 AIProvider: model={provider.model}, url={provider.api_url}")
        self._provider = provider

    def set_callbacks(self, callbacks: Dict[str, Callable]):
        logger.info(f"[Agent] 更新 callbacks, 包含键: {list(callbacks.keys())}")
        self._callbacks = callbacks

    def _append_message(self, role: str, text: str, tag: str = ""):
        role_tag = {"user": "🧑 💬", "assistant": "🤖 💬", "system": "⚙ ℹ", "tool": "🔧 ⚡"}.get(role, "💬")

        prefix = f"{role_tag} "
        if tag:
            prefix += f"[{tag}] "

        self._msg_batch_buffer.append((prefix, text + "\n\n", "tag_bold"))

        if not self._msg_batch_scheduled:
            self._msg_batch_scheduled = True
            self.after(10, self._flush_msg_batch)

    def _flush_msg_batch(self):
        self._msg_batch_scheduled = False
        if not self._msg_batch_buffer:
            return
        self._msg_display.configure(state=ctk.NORMAL)
        for entry in self._msg_batch_buffer:
            if len(entry) == 3:
                text1, text2, tag_name = entry
                self._msg_display.insert(ctk.END, text1, tag_name)
                self._msg_display.insert(ctk.END, text2)
            else:
                text1, tag_name = entry
                self._msg_display.insert(ctk.END, text1, tag_name)
        self._msg_batch_buffer.clear()
        self._msg_display.configure(state=ctk.DISABLED)
        self._msg_display.see(ctk.END)

    def _append_system_message(self, text: str):
        self._append_message("system", text)

    def _append_divider(self):
        self._msg_batch_buffer.append(("─" * 50 + "\n\n", "tag_dim"))
        if not self._msg_batch_scheduled:
            self._msg_batch_scheduled = True
            self.after(10, self._flush_msg_batch)

    def _clear_chat(self):
        logger.info("[Agent] 清空聊天")
        self._msg_display.configure(state=ctk.NORMAL)
        self._msg_display.delete("1.0", ctk.END)
        self._msg_display.configure(state=ctk.DISABLED)
        self._init_messages()
        self._append_system_message(_("agent_chat_cleared"))
        self._append_divider()

    def send_message(self, text: str):
        """外部快速发送消息（供快速输入框调用）"""
        text = text.strip()
        if not text:
            return
        if not self._provider:
            self._append_message("system", _("agent_no_token_hint"))
            return
        if not self._provider.api_key:
            self._append_message("system", _("agent_no_token_hint"))
            return
        self._input_entry.delete(0, ctk.END)
        self._send_btn.configure(state=ctk.DISABLED, text=_("agent_thinking"))
        self._append_message("user", text)
        self._append_divider()
        self._messages.append({"role": "user", "content": text})
        _trigger_agent_ach("agent_first_chat")
        threading.Thread(target=self._process_ai_loop, daemon=True, name="AgentAI").start()

    def _on_send(self):
        user_input = self._input_entry.get().strip()
        logger.info(f"[Agent] _on_send 触发, 输入: '{user_input}'")
        if not user_input:
            logger.info("[Agent] 输入为空，忽略")
            return

        if not self._provider:
            logger.warning("[Agent] provider 未设置，提示用户前往设置")
            self._append_message("system", _("agent_no_token_hint"))
            return

        if not self._provider.api_key:
            logger.warning("[Agent] provider Token 为空")
            self._append_message("system", _("agent_no_token_hint"))
            return

        logger.info(f"[Agent] provider 已设置，开始处理消息")
        self._input_entry.delete(0, ctk.END)
        self._send_btn.configure(state=ctk.DISABLED, text=_("agent_thinking"))

        self._append_message("user", user_input)
        self._append_divider()

        self._messages.append({"role": "user", "content": user_input})
        logger.info(f"[Agent] 当前消息数: {len(self._messages)}, 启动后台线程")

        _trigger_agent_ach("agent_first_chat")
        threading.Thread(target=self._process_ai_loop, daemon=True, name="AgentAI").start()

    def _on_choice_selected(self, choice_value: str):
        logger.info(f"[Agent] 用户选择: {choice_value}")
        self._append_message("user", _("agent_choose_option", value=choice_value))
        self._append_divider()

        self._messages.append({"role": "user", "content": f"我选择: {choice_value}"})

        self._send_btn.configure(state=ctk.DISABLED, text=_("agent_thinking"))
        threading.Thread(target=self._process_ai_loop, daemon=True, name="AgentAI").start()

    def _on_ask_user_response(self, response: str):
        if not self._pending_ask_user:
            return
        question, tool_call_id = self._pending_ask_user
        self._pending_ask_user = None

        logger.info(f"[Agent] 用户回复问题: 问题='{question}', 回复='{response}'")
        self._append_message("user", _("agent_choose_option", value=response))
        self._append_divider()

        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": f"用户的回复: {response}",
        })
        logger.info("[Agent] 用户回复已追加，继续下一轮迭代")

        self._send_btn.configure(state=ctk.DISABLED, text=_("agent_thinking"))
        threading.Thread(target=self._process_ai_loop, daemon=True, name="AgentAI").start()

    def _on_dangerous_command_confirmed(self, confirmed: bool):
        if not self._pending_dangerous:
            return
        exec_path, exec_command, tool_call_id = self._pending_dangerous
        self._pending_dangerous = None

        if confirmed:
            logger.info(f"[Agent] 用户确认执行高危命令: {exec_command}")
            self._append_message("tool", _("agent_exec_confirmed", command=exec_command), tag=_("agent_tool_result"))
            result_text = execute_dangerous_command(exec_path, exec_command)
            _trigger_agent_ach("agent_terminal_warrior")
        else:
            logger.info(f"[Agent] 用户取消了高危命令: {exec_command}")
            self._append_message("tool", _("agent_exec_cancelled", command=exec_command), tag=_("agent_tool_result"))
            result_text = f"⚠️ 用户取消了命令执行\n路径: {exec_path}\n命令: {exec_command}"

        self._append_message("tool", result_text[:500], tag=_("agent_tool_result"))
        self._messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result_text,
        })
        logger.info("[Agent] 工具结果已追加到消息列表，继续下一轮迭代")

        self._send_btn.configure(state=ctk.DISABLED, text=_("agent_thinking"))
        threading.Thread(target=self._process_ai_loop, daemon=True, name="AgentAI").start()

    def _process_ai_loop(self):
        max_iterations = 50
        max_empty_retries = 3
        iteration = 0
        empty_content_count = 0
        logger.info("[Agent] === AI 处理循环开始 ===")

        try:
            while iteration < max_iterations:
                iteration += 1
                if iteration > 1:
                    _trigger_agent_ach("agent_multi_turn")
                logger.info(f"[Agent] --- 迭代 {iteration}/{max_iterations} ---")
                logger.info(f"[Agent] 发送消息数: {len(self._messages)}")

                try:
                    response_msg = self._provider.chat(
                        messages=self._messages,
                        tools=get_tool_definitions(),
                    )
                except Exception as e:
                    logger.error(f"[Agent] API 调用失败: {e}", exc_info=True)
                    self.after(0, lambda err=str(e): self._append_message(
                        "system", _("agent_processing_error", error=err)
                    ))
                    break

                logger.info(f"[Agent] API 返回 message keys: {list(response_msg.keys())}")
                logger.info(f"[Agent] content 前200字: {(response_msg.get('content', '') or '')[:200]}")
                logger.info(f"[Agent] tool_calls 数量: {len(response_msg.get('tool_calls', []))}")

                tool_calls = response_msg.get("tool_calls", [])
                content = response_msg.get("content", "")

                if not content and not tool_calls:
                    empty_content_count += 1
                    logger.warning(f"[Agent] API 返回空内容 ({empty_content_count}/{max_empty_retries})")
                    if empty_content_count >= max_empty_retries:
                        self.after(0, lambda: self._append_message(
                            "system", "AI 多次返回空内容，请检查 Token 是否有效或模型是否支持 function calling"
                        ))
                        break
                    self._messages.append({
                        "role": "user",
                        "content": "你返回了空内容，请继续",
                    })
                    continue

                empty_content_count = 0

                self._messages.append(response_msg)

                if content:
                    self.after(0, lambda t=content: self._append_message("assistant", t))
                else:
                    logger.info("[Agent] content 为空，不追加 assistant 消息")

                if tool_calls:
                    logger.info(f"[Agent] 模型请求调用 {len(tool_calls)} 个工具")
                    for tc in tool_calls:
                        func = tc.get("function", {})
                        tool_name = func.get("name", "")
                        tool_call_id = tc.get("id", "")

                        try:
                            tool_params = json.loads(func.get("arguments", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            logger.warning(f"[Agent] 工具参数 JSON 解析失败: {func.get('arguments', '')[:200]}")
                            tool_params = {}

                        logger.info(f"[Agent] 工具调用: {tool_name}, 参数: {tool_params}")

                        self.after(0, lambda n=tool_name, p=tool_params: self._append_message(
                            "tool", _("agent_tool_call", name=n) + "\n" + _("agent_tool_params", params=json.dumps(p, ensure_ascii=False))
                        ))

                        result_text = execute_tool(tool_name, tool_params, self._callbacks)
                        logger.info(f"[Agent] 工具执行结果 (前300字): {result_text[:300]}")

                        if result_text.startswith(DANGEROUS_MARKER):
                            parts = result_text.split("|", 2)
                            exec_path = parts[1]
                            exec_command = parts[2]
                            self._pending_dangerous = (exec_path, exec_command, tool_call_id)
                            logger.info(f"[Agent] 需要用户确认高危命令: {exec_command}")

                            def _make_danger_dialog(ep, ec):
                                def _show():
                                    if not self._pending_dangerous:
                                        return
                                    dp, dc, _ = self._pending_dangerous
                                    confirmed = show_confirmation(
                                        _("agent_exec_dangerous_warning", command=dc, path=dp),
                                        title=_("agent_exec_dangerous_title"),
                                    )
                                    self.after(0, lambda c=confirmed: self._on_dangerous_command_confirmed(c))
                                return _show

                            self.after(0, _make_danger_dialog(exec_path, exec_command))
                            logger.info("[Agent] 高危命令等待用户确认，退出循环")
                            return

                        if result_text.startswith(ASK_USER_MARKER):
                            parts = result_text.split("|", 2)
                            question = parts[1]
                            options_json = parts[2] if len(parts) > 2 else "[]"
                            try:
                                options = json.loads(options_json)
                            except (json.JSONDecodeError, TypeError):
                                options = []
                            self._pending_ask_user = (question, tool_call_id)
                            logger.info(f"[Agent] AI 向用户提问: '{question}', 选项数: {len(options)}")

                            self.after(0, lambda q=question, opts=options: self._show_ask_user_dialog(q, opts))
                            logger.info("[Agent] ask_user 等待用户回复，退出循环")
                            return

                        self.after(0, lambda r=result_text[:500]: self._append_message(
                            "tool", r, tag=_("agent_tool_result")
                        ))

                        self._messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call_id,
                            "content": result_text,
                        })

                    logger.info("[Agent] 工具结果已追加到消息列表，继续下一轮迭代")
                    continue

                else:
                    logger.info("[Agent] 任务完成（无工具调用）")
                    self.after(0, self._append_divider)
                    show_notification("🤖", _("notify_ai_task_done"), "", notify_type="success")
                    _trigger_agent_ach("agent_nlp_master")
                    break

        except Exception as e:
            logger.error(f"[Agent] 处理循环异常: {e}", exc_info=True)
            show_notification("🤖", _("notify_ai_task_failed"), str(e)[:50], notify_type="error")
            self.after(0, lambda: self._append_message(
                "system", _("agent_processing_error", error=str(e))
            ))
        finally:
            logger.info("[Agent] === AI 处理循环结束 ===")
            self.after(0, self._reset_send_button)
            self.after(0, self._refresh_credits_after_loop)

    def _show_choice_dialog(self, message: str, options: List[Dict[str, str]]):
        logger.info(f"[Agent] 显示选择对话框: 选项数={len(options)}")
        OptionSelectDialog(
            self.winfo_toplevel(),
            title=_("agent_choose"),
            options=options,
            callback=self._on_choice_selected,
        )

    def _show_ask_user_dialog(self, question: str, options: List[str]):
        if not self._pending_ask_user:
            return
        if options:
            opts = [{"value": o, "label": o} for o in options]
            logger.info(f"[Agent] 显示 ask_user 选择对话框: 选项数={len(options)}")
            OptionSelectDialog(
                self.winfo_toplevel(),
                title=question,
                options=opts,
                callback=self._on_ask_user_response,
            )
        else:
            logger.info(f"[Agent] 显示 ask_user 文本输入对话框")
            TextInputDialog(
                self.winfo_toplevel(),
                title=_("agent_choose"),
                question=question,
                callback=self._on_ask_user_response,
            )

    def _reset_send_button(self):
        logger.info("[Agent] 重置发送按钮状态")
        self._send_btn.configure(state=ctk.NORMAL, text=_("agent_send"))

    def _refresh_credits_after_loop(self):
        """AI 处理循环结束后刷新积分余额"""
        top = self.winfo_toplevel()
        if hasattr(top, '_refresh_agent_credits'):
            top._refresh_agent_credits()
