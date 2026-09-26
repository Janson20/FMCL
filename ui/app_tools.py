"""ModernApp 工具 Mixin - 工具标签页相关方法

业务逻辑已搬到 ``services/tool_service.py``（阶段 1 任务 1.6）：8 个工具
（垃圾清理、端口检测、哈希计算、坐标转换、每日运势、MC 冷知识、MC 知识问答、
多线程下载器）的算法与 IO。本文件只剩纯界面部分（卡片与部件、读输入框、
把结果刷到控件上）与"起线程 + 把结果切回主线程"的接线；受影响的方法都退化成
对服务的薄委托，**方法名与签名保持不变**，界面可见行为不变。

未受影响的界面代码（卡片构建、勾选联动、控件排版、文案、颜色、间距）
逐字保留 —— 改写由 ``poc/build_app_tools.py`` 按行区间拼接完成，
未列入替换清单的行不可能被改动。

唯一对象在服务里的常量/工具函数在这里只做**绑定**（与 ``ui/app_monitor.py``
对 ``services/monitor_service.py`` 的做法一致）：``_MC_FACTS`` /
``_QUIZ_SYSTEM_PROMPT`` / ``_format_size`` / ``_mc_write_varint`` /
``_mc_read_varint`` 看到的都还是服务里那一份。
"""

import io
import os
import threading
from pathlib import Path
from tkinter import filedialog
from typing import Any, Dict, List, Optional

import customtkinter as ctk
from logzero import logger

from services import tool_service as _tool_svc
from services.errors import InvalidArgument
from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _

# ─── 已搬进 services/tool_service.py 的工具函数（同一批对象）────────────
_format_size = _tool_svc._format_size
_mc_write_varint = _tool_svc._mc_write_varint
_mc_read_varint = _tool_svc._mc_read_varint


class ToolsTabMixin(object):
    """工具标签页 Mixin"""

    # ── 服务层接线（实现在 services/tool_service.py）──────────────

    def _tool_service(self) -> "_tool_svc.ToolService":
        """惰性取得工具箱服务实例。

        优先用 ``AppContext`` 里注册的那个（阶段 2 接上之后），取不到就自己造一个
        并缓存下来 —— ``ToolService`` 不需要 ``AppContext``，构造期也只保存参数。
        """
        ctx = getattr(self, "context", None)
        if ctx is not None:
            getter = getattr(ctx, "try_get", None)
            if callable(getter):
                try:
                    service = getter(_tool_svc.ToolService.name)
                except Exception:
                    service = None
                if service is not None:
                    return service
        service = getattr(self, "_tool_service_fallback", None)
        if service is None:
            service = _tool_svc.ToolService()
            self._tool_service_fallback = service
        return service

    def _build_tools_tab_content(self):
        content = ctk.CTkScrollableFrame(self.tools_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        self._build_tool_clean_junk(content)
        self._build_tool_daily_fortune(content)
        self._build_tool_coordinate_converter(content)
        self._build_tool_hash_calculator(content)
        self._build_tool_port_checker(content)
        self._build_tool_minecraft_facts(content)
        self._build_tool_minecraft_quiz(content)
        self._build_tool_multi_download(content)

    def _make_tool_card(self, parent, title: str, desc: str) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12)
        card.pack(fill=ctk.X, pady=(0, 12))

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.pack(fill=ctk.X, padx=16, pady=(14, 0))

        ctk.CTkLabel(
            header,
            text=title,
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            card,
            text=desc,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=600,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, padx=16, pady=(4, 12))

        return card

    def _build_tool_clean_junk(self, parent):
        card = self._make_tool_card(parent, _("tool_clean_junk_title"), _("tool_clean_junk_desc"))

        self._clean_junk_status = ctk.CTkLabel(
            card, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=COLORS["text_secondary"]
        )

        settings = ctk.CTkFrame(card, fg_color="transparent")
        settings.pack(fill=ctk.X, padx=16, pady=(0, 8))

        folder_row = ctk.CTkFrame(settings, fg_color="transparent")
        folder_row.pack(fill=ctk.X, pady=(0, 6))

        ctk.CTkLabel(
            folder_row,
            text=_("tool_clean_junk_folder"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT, padx=(0, 6))

        self._clean_junk_folder_entry = ctk.CTkEntry(
            folder_row,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
        )
        self._clean_junk_folder_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 6))
        self._clean_junk_folder_entry.insert(0, str(self._get_minecraft_dir_for_tools()))

        ctk.CTkButton(
            folder_row,
            text="📂",
            width=36,
            height=32,
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_browse_clean_junk_folder,
        ).pack(side=ctk.RIGHT)

        depth_row = ctk.CTkFrame(settings, fg_color="transparent")
        depth_row.pack(fill=ctk.X)

        ctk.CTkLabel(
            depth_row,
            text=_("tool_clean_junk_depth"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT, padx=(0, 6))

        self._clean_junk_depth_entry = ctk.CTkEntry(
            depth_row,
            width=70,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
        )
        self._clean_junk_depth_entry.insert(0, "5")
        self._clean_junk_depth_entry.pack(side=ctk.LEFT)

        ctk.CTkLabel(
            settings,
            text=_("tool_clean_junk_skip_hint"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(6, 0))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 14))

        self._clean_junk_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_clean_junk_scan"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_clean_junk,
        )
        self._clean_junk_btn.pack(side=ctk.LEFT)

        self._clean_junk_list_frame = ctk.CTkScrollableFrame(
            card, height=160, fg_color=COLORS["bg_medium"], corner_radius=8
        )

        self._clean_junk_selected_label = ctk.CTkLabel(
            card, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=COLORS["text_secondary"]
        )

        self._clean_junk_dirs = {}
        self._clean_junk_base = ""

    def _refresh_junk_selection(self):
        count = 0
        size = 0
        for info in self._clean_junk_dirs.values():
            for fp, (cb, _row, file_size) in info["files"].items():
                if cb.get():
                    count += 1
                    size += file_size
        self._clean_junk_selected_label.configure(
            text=_("tool_clean_junk_selected", count=count, size=_format_size(size))
        )

    def _sync_junk_dir_checkbox(self, dir_key: str):
        info = self._clean_junk_dirs[dir_key]
        files = info["files"]
        if files and all(cb.get() for cb, _row, _size in files.values()):
            info["dir_cb"].select()
        else:
            info["dir_cb"].deselect()

    def _set_junk_dir_files_state(self, dir_key: str, checked: bool):
        info = self._clean_junk_dirs[dir_key]
        for cb, _row, _size in info["files"].values():
            if checked:
                cb.select()
            else:
                cb.deselect()

    def _on_junk_file_toggle(self, dir_key: str):
        self._sync_junk_dir_checkbox(dir_key)
        self._refresh_junk_selection()

    def _on_junk_dir_toggle(self, dir_key: str):
        info = self._clean_junk_dirs[dir_key]
        self._set_junk_dir_files_state(dir_key, bool(info["dir_cb"].get()))
        self._refresh_junk_selection()

    def _toggle_junk_dir_expand(self, dir_key: str):
        info = self._clean_junk_dirs[dir_key]
        info["expanded"] = not info["expanded"]
        if info["expanded"]:
            info["expand_btn"].configure(text="▼")
            info["file_frame"].pack(fill=ctk.X)
        else:
            info["expand_btn"].configure(text="▶")
            info["file_frame"].pack_forget()

    def _add_junk_dir_row(self, dir_key: str, display: str, count: int, size: int):
        dir_row = ctk.CTkFrame(self._clean_junk_list_frame, fg_color="transparent")
        dir_row.pack(fill=ctk.X, pady=(3, 0))

        expand_btn = ctk.CTkButton(
            dir_row,
            text="▶",
            width=26,
            height=22,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color="transparent",
            hover_color=COLORS["card_border"],
            command=lambda: self._toggle_junk_dir_expand(dir_key),
        )
        expand_btn.pack(side=ctk.LEFT, padx=(2, 2))

        dir_cb = ctk.CTkCheckBox(
            dir_row,
            text="",
            width=26,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_color=COLORS["card_border"],
            command=lambda: self._on_junk_dir_toggle(dir_key),
        )
        dir_cb.select()
        dir_cb.pack(side=ctk.LEFT, padx=(4, 6))

        ctk.CTkLabel(
            dir_row,
            text=display,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_primary"],
            wraplength=380,
            justify=ctk.LEFT,
            anchor=ctk.W,
        ).pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        count_label = ctk.CTkLabel(
            dir_row,
            text=_("tool_clean_junk_dir_files", count=count, size=_format_size(size)),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        count_label.pack(side=ctk.RIGHT, padx=(8, 4))

        file_frame = ctk.CTkFrame(self._clean_junk_list_frame, fg_color="transparent")
        self._clean_junk_dirs[dir_key] = {
            "dir_row": dir_row,
            "expand_btn": expand_btn,
            "dir_cb": dir_cb,
            "count_label": count_label,
            "file_frame": file_frame,
            "expanded": False,
            "files": {},
        }

    def _add_junk_file_row(self, dir_key: str, fp: str, size: int):
        rel = self._tool_service().relative_path_from(fp, self._clean_junk_base)
        info = self._clean_junk_dirs[dir_key]
        row = ctk.CTkFrame(info["file_frame"], fg_color="transparent")
        row.pack(fill=ctk.X, pady=(1, 0))
        cb = ctk.CTkCheckBox(
            row,
            text="",
            width=26,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_color=COLORS["card_border"],
            command=lambda: self._on_junk_file_toggle(dir_key),
        )
        cb.select()
        cb.pack(side=ctk.LEFT, padx=(24, 6))
        ctk.CTkLabel(
            row,
            text=rel,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_primary"],
            wraplength=380,
            justify=ctk.LEFT,
            anchor=ctk.W,
        ).pack(side=ctk.LEFT, fill=ctk.X, expand=True)
        ctk.CTkLabel(
            row,
            text=_format_size(size),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.RIGHT, padx=(8, 4))
        info["files"][fp] = (cb, row, size)

    def _clear_junk_rows(self):
        for info in self._clean_junk_dirs.values():
            info["dir_row"].destroy()
            info["file_frame"].destroy()
        self._clean_junk_dirs = {}

    def _reset_clean_junk_btn(self):
        self._clean_junk_btn.configure(
            state=ctk.NORMAL,
            text=_("tool_clean_junk_scan"),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_clean_junk,
        )

    def _on_browse_clean_junk_folder(self):
        path = filedialog.askdirectory(title=_("tool_clean_junk_folder_title"), parent=self)
        if path:
            self._clean_junk_folder_entry.delete(0, "end")
            self._clean_junk_folder_entry.insert(0, path)

    def _is_protected_system_dir(self, path: str) -> bool:
        """路径是否在系统目录内（实现在 services/tool_service.py）"""
        return self._tool_service().is_protected_system_dir(path)

    def _on_clean_junk(self):
        btn = self._clean_junk_btn
        service = self._tool_service()

        scan_dir = self._clean_junk_folder_entry.get().strip()
        if not scan_dir:
            scan_dir = str(self._get_minecraft_dir_for_tools())
        if not os.path.isdir(scan_dir):
            self.set_status(_("tool_clean_junk_folder_error"), "error")
            return
        if service.is_protected_system_dir(scan_dir):
            self.set_status(_("tool_clean_junk_system_dir_error"), "error")
            return

        try:
            max_depth = service.parse_max_depth(self._clean_junk_depth_entry.get().strip())
        except InvalidArgument:
            self.set_status(_("tool_clean_junk_depth_error"), "error")
            return

        btn.configure(state=ctk.DISABLED, text=_("tool_clean_junk_scanning"))

        def _task():
            try:
                scan = service.scan_junk_files(scan_dir, max_depth)
                junk_files = scan.files
                total_size = scan.total_size
                base = scan.base

                def _update_ui():
                    self._clean_junk_list_frame.pack_forget()
                    self._clean_junk_selected_label.pack_forget()
                    self._clear_junk_rows()

                    if not junk_files:
                        self._clean_junk_status.configure(
                            text=_("tool_clean_junk_none"), text_color=COLORS["text_secondary"]
                        )
                        self._reset_clean_junk_btn()
                        self._clean_junk_status.pack(anchor=ctk.W, padx=16, pady=(0, 8))
                    else:
                        self._clean_junk_status.configure(
                            text=_("tool_clean_junk_found", count=len(junk_files), size=_format_size(total_size)),
                            text_color=COLORS["accent"],
                        )
                        self._clean_junk_status.pack(anchor=ctk.W, padx=16, pady=(0, 8))
                        self._clean_junk_base = base
                        self._clean_junk_list_frame.pack(fill=ctk.X, padx=16, pady=(0, 6))
                        for group in service.group_junk_files(base, junk_files):
                            self._add_junk_dir_row(
                                group.dir, group.display, len(group.files), sum(s for _f, s in group.files)
                            )
                            for fp, size in group.files:
                                self._add_junk_file_row(group.dir, fp, size)
                        self._refresh_junk_selection()
                        self._clean_junk_selected_label.pack(anchor=ctk.W, padx=16, pady=(0, 8))
                        btn.configure(
                            state=ctk.NORMAL,
                            text=_("tool_clean_junk_delete_selected"),
                            fg_color=COLORS["accent"],
                            hover_color=COLORS["accent_hover"],
                            command=self._on_delete_selected_junk,
                        )

                self.after(0, _update_ui)
            except Exception as e:
                logger.error(f"扫描垃圾文件失败: {e}")
                _scan_err = str(e)

                def _error_ui():
                    self._clean_junk_list_frame.pack_forget()
                    self._clean_junk_selected_label.pack_forget()
                    self._reset_clean_junk_btn()
                    self._clean_junk_status.configure(
                        text=_("tool_clean_junk_error", error=_scan_err), text_color=COLORS["text_secondary"]
                    )
                    self._clean_junk_status.pack(anchor=ctk.W, padx=16, pady=(0, 8))

                self.after(0, _error_ui)

        threading.Thread(target=_task, daemon=True).start()

    # ═══════════ MC 知识问答 ═══════════

    _QUIZ_SYSTEM_PROMPT = _tool_svc.QUIZ_SYSTEM_PROMPT

    def _build_tool_minecraft_quiz(self, parent):
        card = self._make_tool_card(parent, _("tool_quiz_title"), _("tool_quiz_desc"))

        self._quiz_token_status = ctk.CTkLabel(
            card,
            text=_("tool_quiz_not_logged_in"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._quiz_token_status.pack(anchor=ctk.W, padx=16, pady=(0, 4))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 4))

        self._quiz_gen_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_quiz_generate"),
            width=120,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_quiz_generate,
        )
        self._quiz_gen_btn.pack(side=ctk.LEFT, padx=(0, 8))
        self._quiz_gen_btn.configure(state=ctk.DISABLED)

        self._quiz_next_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_quiz_next"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_quiz_next,
        )
        self._quiz_next_btn.pack(side=ctk.LEFT)

        self._quiz_remaining_label = ctk.CTkLabel(
            btn_row, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=COLORS["text_secondary"]
        )
        self._quiz_remaining_label.pack(side=ctk.LEFT, padx=(15, 0))

        self._quiz_content_frame = ctk.CTkFrame(card, fg_color="transparent")

        self._quiz_questions = []
        self._quiz_current_index = 0
        self._quiz_answered = False
        self._quiz_load_from_file()
        self._quiz_update_remaining()
        self.after(50, self._quiz_sync_token_status)

    def _quiz_load_from_file(self):
        loaded = self._tool_service().load_quiz_questions(self._get_quiz_path())
        if loaded is not None:
            self._quiz_questions = loaded

    def _quiz_save_to_file(self):
        self._tool_service().save_quiz_questions(self._get_quiz_path(), self._quiz_questions)

    def _get_quiz_path(self) -> Path:
        try:
            if self.callbacks and "get_minecraft_dir" in self.callbacks:
                base = Path(self.callbacks["get_minecraft_dir"]()).parent
                return base / "quiz.json"
        except Exception:
            pass
        return Path("quiz.json")

    def _quiz_sync_token_status(self):
        token = self._get_quiz_token()
        if token:
            self._quiz_token_status.configure(text=_("tool_quiz_logged_in"), text_color="#4caf50")
            self._quiz_gen_btn.configure(state=ctk.NORMAL)
            if self._quiz_questions:
                self._quiz_show_current()
            else:
                self._quiz_show_empty()
        else:
            self._quiz_token_status.configure(text=_("tool_quiz_not_logged_in"), text_color=COLORS["text_secondary"])
            self._quiz_gen_btn.configure(state=ctk.DISABLED)
            self._quiz_show_login_hint()

    def _get_quiz_token(self) -> str:
        try:
            if self.callbacks and "get_jdz_token" in self.callbacks:
                return self.callbacks["get_jdz_token"]() or ""
        except Exception:
            pass
        try:
            from config import config

            return config.jdz_token or ""
        except Exception:
            pass
        return ""

    def _quiz_show_login_hint(self):
        for w in self._quiz_content_frame.winfo_children():
            w.destroy()
        self._quiz_content_frame.pack_forget()
        self._quiz_content_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))
        ctk.CTkLabel(
            self._quiz_content_frame,
            text=_("tool_quiz_login_hint"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_secondary"],
            wraplength=550,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=10)

    def _quiz_show_empty(self):
        for w in self._quiz_content_frame.winfo_children():
            w.destroy()
        self._quiz_content_frame.pack_forget()
        self._quiz_content_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))
        ctk.CTkLabel(
            self._quiz_content_frame,
            text=_("tool_quiz_no_questions"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=10)

    def _quiz_update_remaining(self):
        cnt = len(self._quiz_questions)
        self._quiz_remaining_label.configure(text=_("tool_quiz_remaining", count=cnt))
        if cnt <= 5 and self._get_quiz_token():
            self._quiz_auto_refill()

    def _quiz_auto_refill(self):
        if getattr(self, "_quiz_refilling", False):
            return
        self._quiz_refilling = True
        service = self._tool_service()

        def _task():
            try:
                new_qs = self._quiz_call_ai_generate()
                if new_qs:
                    reindexed = service.merge_generated_questions(self._quiz_questions, new_qs)
                    self._quiz_questions.extend(reindexed)
                    self._quiz_save_to_file()

                    def _done():
                        self._quiz_update_remaining()
                        self.set_status(_("tool_quiz_auto_refilled", count=len(reindexed)), "success")

                    self.after(0, _done)
            except Exception as e:
                logger.error(f"自动补充题目失败: {e}")
            finally:
                self._quiz_refilling = False

        threading.Thread(target=_task, daemon=True).start()

    def _quiz_call_ai_generate(self) -> List[Dict]:
        token = self._get_quiz_token()

        def _chat(messages):
            from ui.agent.providers.jingdu import JingduProvider

            provider = JingduProvider(api_key=token)
            return provider.chat(messages)

        return self._tool_service().generate_quiz(token, chat=_chat)

    def _quiz_show_current(self):
        for w in self._quiz_content_frame.winfo_children():
            w.destroy()
        self._quiz_content_frame.pack_forget()

        if not self._quiz_questions or self._quiz_current_index >= len(self._quiz_questions):
            self._quiz_current_index = 0
        if not self._quiz_questions:
            self._quiz_show_empty()
            return

        self._quiz_content_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))
        self._quiz_answered = False

        q = self._quiz_questions[self._quiz_current_index]

        sep = ctk.CTkFrame(self._quiz_content_frame, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, pady=(4, 8))

        qid = q.get("id", self._quiz_current_index + 1)
        total = len(self._quiz_questions)
        ctk.CTkLabel(
            self._quiz_content_frame,
            text=_("tool_quiz_progress", current=qid),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(0, 4))

        ctk.CTkLabel(
            self._quiz_content_frame,
            text=q.get("question", ""),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
            wraplength=560,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(0, 8))

        options = q.get("options", [])
        if options:
            self._quiz_option_btns = []
            opts_frame = ctk.CTkFrame(self._quiz_content_frame, fg_color="transparent")
            opts_frame.pack(anchor=ctk.W, pady=(0, 8))
            for i, opt in enumerate(options):
                letter = chr(ord("A") + i)
                btn = ctk.CTkButton(
                    opts_frame,
                    text=opt,
                    width=500,
                    height=34,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                    fg_color=COLORS["bg_light"],
                    hover_color=COLORS["card_border"],
                    anchor=ctk.W,
                    command=lambda idx=i: self._on_quiz_select(idx),
                )
                btn.pack(anchor=ctk.W, pady=2)
                self._quiz_option_btns.append(btn)
        else:
            self._quiz_option_btns = []
            self._quiz_answer_entry = ctk.CTkEntry(
                self._quiz_content_frame,
                height=34,
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                fg_color=COLORS["bg_medium"],
                border_color=COLORS["card_border"],
                placeholder_text=_("tool_quiz_answer_placeholder"),
            )
            self._quiz_answer_entry.pack(fill=ctk.X, pady=(0, 8))
            self._quiz_answer_entry.bind("<Return>", lambda e: self._on_quiz_submit_text())
            btn = ctk.CTkButton(
                self._quiz_content_frame,
                text=_("tool_quiz_submit"),
                width=100,
                height=30,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
                command=self._on_quiz_submit_text,
            )
            btn.pack(anchor=ctk.W, pady=(0, 8))
            self._quiz_submit_btn = btn

        self._quiz_result_label = ctk.CTkLabel(
            self._quiz_content_frame, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=13)
        )

    def _on_quiz_select(self, index: int):
        if self._quiz_answered:
            return
        self._quiz_answered = True
        q = self._quiz_questions[self._quiz_current_index]
        options = q.get("options", [])
        selected = options[index] if index < len(options) else ""
        correct = q.get("answer", "")
        explanation = q.get("explanation", "")

        is_correct = self._tool_service().check_choice_answer(selected, correct)
        self._quiz_result_label.configure(
            text=_("tool_quiz_correct") if is_correct else _("tool_quiz_wrong", correct=correct),
            text_color="#4caf50" if is_correct else "#cd5c5c",
        )
        self._quiz_result_label.pack(anchor=ctk.W, pady=(4, 0))

        if explanation:
            ctk.CTkLabel(
                self._quiz_content_frame,
                text=f"💡 {explanation}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
                wraplength=550,
                justify=ctk.LEFT,
            ).pack(anchor=ctk.W, pady=(4, 0))

        for i, btn in enumerate(self._quiz_option_btns):
            if options[i].strip() == correct.strip():
                btn.configure(fg_color="#4caf50", hover_color="#388e3c", state=ctk.DISABLED)
            elif i == index and not is_correct:
                btn.configure(fg_color="#cd5c5c", hover_color="#b71c1c", state=ctk.DISABLED)
            else:
                btn.configure(state=ctk.DISABLED)

    def _on_quiz_submit_text(self):
        if self._quiz_answered:
            return
        answer = self._quiz_answer_entry.get().strip()
        if not answer:
            return
        self._quiz_answered = True
        self._quiz_submit_btn.configure(state=ctk.DISABLED)
        self._quiz_answer_entry.configure(state=ctk.DISABLED)

        q = self._quiz_questions[self._quiz_current_index]
        correct = q.get("answer", "")
        explanation = q.get("explanation", "")

        is_correct = self._tool_service().check_text_answer(answer, correct)
        self._quiz_result_label.configure(
            text=_("tool_quiz_correct") if is_correct else _("tool_quiz_wrong", correct=correct),
            text_color="#4caf50" if is_correct else "#cd5c5c",
        )
        self._quiz_result_label.pack(anchor=ctk.W, pady=(4, 0))

        if explanation:
            ctk.CTkLabel(
                self._quiz_content_frame,
                text=f"💡 {explanation}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
                wraplength=550,
                justify=ctk.LEFT,
            ).pack(anchor=ctk.W, pady=(4, 0))

    def _on_quiz_generate(self):
        token = self._get_quiz_token()
        if not token:
            self._quiz_show_login_hint()
            self.set_status(_("tool_quiz_not_logged_in"), "error")
            return

        self._quiz_gen_btn.configure(state=ctk.DISABLED, text=_("tool_quiz_generating"))
        self._quiz_next_btn.configure(state=ctk.DISABLED)

        for w in self._quiz_content_frame.winfo_children():
            w.destroy()
        self._quiz_content_frame.pack_forget()
        self._quiz_content_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))
        ctk.CTkLabel(
            self._quiz_content_frame,
            text=_("tool_quiz_ai_thinking"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=10)

        service = self._tool_service()

        def _task():
            try:
                questions = self._quiz_call_ai_generate()
                reindexed = service.reindex_questions(questions)
                self._quiz_questions = reindexed
                self._quiz_current_index = 0
                self._quiz_save_to_file()

                def _done():
                    self._quiz_gen_btn.configure(state=ctk.NORMAL, text=_("tool_quiz_generate"))
                    self._quiz_next_btn.configure(state=ctk.NORMAL)
                    self._quiz_show_current()
                    self._quiz_update_remaining()
                    self.set_status(_("tool_quiz_generated", count=len(questions)), "success")

                self.after(0, _done)
            except Exception as e:
                logger.error(f"生成题目失败: {e}")
                _quiz_err = str(e)

                def _error():
                    self._quiz_gen_btn.configure(state=ctk.NORMAL, text=_("tool_quiz_generate"))
                    self._quiz_next_btn.configure(state=ctk.NORMAL)
                    self.set_status(_("tool_quiz_gen_error", error=_quiz_err), "error")
                    self._quiz_show_empty()

                self.after(0, _error)

        threading.Thread(target=_task, daemon=True).start()

    def _on_quiz_next(self):
        if not self._quiz_questions:
            return

        self._tool_service().drop_question(self._quiz_questions, self._quiz_current_index)
        self._quiz_save_to_file()

        if not self._quiz_questions:
            self._quiz_show_empty()
            self._quiz_update_remaining()
            return

        if self._quiz_current_index >= len(self._quiz_questions):
            self._quiz_current_index = len(self._quiz_questions) - 1
            if self._quiz_current_index < 0:
                self._quiz_current_index = 0
                self._quiz_show_empty()
                self._quiz_update_remaining()
                return

        self._quiz_answered = False
        self._quiz_show_current()
        self._quiz_update_remaining()

    # ═══════════ Minecraft 冷知识 ═══════════

    _MC_FACTS = _tool_svc.MC_FACTS

    def _build_tool_minecraft_facts(self, parent):
        card = self._make_tool_card(parent, _("tool_fact_title"), _("tool_fact_desc"))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 4))

        self._fact_new_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_fact_new"),
            width=120,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_new_fact,
        )
        self._fact_new_btn.pack(side=ctk.LEFT, padx=(0, 8))

        self._fact_random_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_fact_random"),
            width=120,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_random_fact,
        )
        self._fact_random_btn.pack(side=ctk.LEFT)

        self._fact_display_frame = ctk.CTkFrame(card, fg_color="transparent")

        self._on_new_fact()

    def _on_new_fact(self):
        index = self._tool_service().fact_of_the_day()
        self._show_fact(index, _("tool_fact_today"))

    def _on_random_fact(self):
        index = self._tool_service().random_fact_index()
        self._show_fact(index, _("tool_fact_random_title"))

    def _show_fact(self, index: int, tag: str):
        fact = self._MC_FACTS[index]

        for w in self._fact_display_frame.winfo_children():
            w.destroy()
        self._fact_display_frame.pack_forget()
        self._fact_display_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))

        sep = ctk.CTkFrame(self._fact_display_frame, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, pady=(4, 8))

        ctk.CTkLabel(
            self._fact_display_frame,
            text=f"💎 {fact}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_primary"],
            wraplength=560,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(0, 4))

        ctk.CTkLabel(
            self._fact_display_frame,
            text=_("tool_fact_index", index=index + 1, total=len(self._MC_FACTS)),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W)

    # ═══════════ 端口检测器 ═══════════

    _PORT_PRESETS = [("", 0), ("localhost", 25565), ("localhost", 19132), ("localhost", 80), ("localhost", 443)]

    def _build_tool_port_checker(self, parent):
        card = self._make_tool_card(parent, _("tool_port_title"), _("tool_port_desc"))

        host_row = ctk.CTkFrame(card, fg_color="transparent")
        host_row.pack(fill=ctk.X, padx=16, pady=(0, 6))

        ctk.CTkLabel(
            host_row,
            text=_("tool_port_host"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
            width=60,
        ).pack(side=ctk.LEFT)

        self._port_host_entry = ctk.CTkEntry(
            host_row,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="example.com 或 127.0.0.1",
        )
        self._port_host_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))

        ctk.CTkLabel(
            host_row,
            text=_("tool_port_port"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
            width=40,
        ).pack(side=ctk.LEFT)

        self._port_port_entry = ctk.CTkEntry(
            host_row,
            width=80,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
        )
        self._port_port_entry.pack(side=ctk.LEFT)

        preset_row = ctk.CTkFrame(card, fg_color="transparent")
        preset_row.pack(fill=ctk.X, padx=16, pady=(0, 6))

        ctk.CTkLabel(
            preset_row,
            text=_("tool_port_presets"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT, padx=(0, 8))

        presets = [("MC Java", "25565"), ("MC Bedrock", "19132"), ("HTTP", "80"), ("HTTPS", "443")]
        for name, port in presets:
            ctk.CTkButton(
                preset_row,
                text=f"{name} ({port})",
                width=100,
                height=26,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                fg_color=COLORS["bg_light"],
                hover_color=COLORS["card_border"],
                command=lambda p=port: self._on_port_preset(p),
            ).pack(side=ctk.LEFT, padx=(0, 5))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 4))

        self._port_test_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_port_test"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_test_port,
        )
        self._port_test_btn.pack(side=ctk.LEFT, padx=(0, 8))

        self._port_peek_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_port_peek"),
            width=130,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_peek_server,
        )
        self._port_peek_btn.pack(side=ctk.LEFT)

        self._port_history_frame = ctk.CTkFrame(card, fg_color="transparent")
        self._port_results = []

        self._port_server_info_frame = ctk.CTkFrame(card, fg_color="transparent")

    def _on_port_preset(self, port: str):
        self._port_port_entry.delete(0, "end")
        self._port_port_entry.insert(0, port)

    def _on_test_port(self):
        host = self._port_host_entry.get().strip()
        port_str = self._port_port_entry.get().strip()

        if not host:
            self.set_status(_("tool_port_no_host"), "error")
            return
        if not port_str:
            self.set_status(_("tool_port_no_port"), "error")
            return
        service = self._tool_service()
        try:
            port = service.parse_port(port_str)
        except InvalidArgument:
            self.set_status(_("tool_port_invalid_port"), "error")
            return

        btn = self._port_test_btn
        btn.configure(state=ctk.DISABLED, text=_("tool_port_testing"))

        def _task():
            probe = service.probe_port(host, port)
            entry = (host, port, probe.status, probe.latency_ms, probe.error)
            self._port_results.insert(0, entry)
            if len(self._port_results) > 10:
                self._port_results = self._port_results[:10]

            def _update():
                for w in self._port_history_frame.winfo_children():
                    w.destroy()
                self._port_history_frame.pack_forget()
                self._port_history_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))

                sep = ctk.CTkFrame(self._port_history_frame, fg_color=COLORS["card_border"], height=1)
                sep.pack(fill=ctk.X, pady=(4, 8))

                for h, p, st, lat, e_msg in self._port_results:
                    row = ctk.CTkFrame(self._port_history_frame, fg_color="transparent")
                    row.pack(fill=ctk.X, pady=1)

                    icon_color = "#4caf50" if st == "open" else "#cd5c5c"
                    icon_text = _("tool_port_open") if st == "open" else _("tool_port_closed")
                    lat_text = f"  {lat}ms" if st == "open" else ""
                    err_text = f"  ({e_msg})" if e_msg else ""

                    ctk.CTkLabel(
                        row,
                        text=f"{h}:{p}",
                        font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                        text_color=COLORS["text_primary"],
                    ).pack(side=ctk.LEFT, padx=(0, 10))

                    ctk.CTkLabel(
                        row,
                        text=f"{icon_text}{lat_text}{err_text}",
                        font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                        text_color=icon_color,
                    ).pack(side=ctk.LEFT)

                btn.configure(state=ctk.NORMAL, text=_("tool_port_test"))

            self.after(0, _update)

        threading.Thread(target=_task, daemon=True).start()

    def _clear_server_info(self):
        for w in self._port_server_info_frame.winfo_children():
            w.destroy()
        self._port_server_info_frame.pack_forget()

    def _display_server_info(self, info: Dict[str, Any], latency_ms: int):
        self._clear_server_info()
        self._port_server_info_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))

        sep = ctk.CTkFrame(self._port_server_info_frame, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, pady=(4, 8))

        info_frame = ctk.CTkFrame(self._port_server_info_frame, fg_color=COLORS["bg_medium"], corner_radius=8)
        info_frame.pack(fill=ctk.X, pady=(0, 4))

        service = self._tool_service()
        favicon = info.get("favicon")
        if favicon:
            try:
                img_data = service.decode_favicon_data(favicon)
                from PIL import Image as PILImage
                from PIL import ImageTk

                img = PILImage.open(io.BytesIO(img_data))
                img = img.resize((48, 48), PILImage.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                icon_label = ctk.CTkLabel(info_frame, image=photo, text="")
                icon_label.image = photo
            except Exception:
                icon_label = ctk.CTkLabel(
                    info_frame, text="🖥", font=ctk.CTkFont(size=36), text_color=COLORS["text_secondary"]
                )
        else:
            icon_label = ctk.CTkLabel(
                info_frame, text="🖥", font=ctk.CTkFont(size=36), text_color=COLORS["text_secondary"]
            )
        icon_label.pack(side=ctk.LEFT, padx=(10, 12), pady=10)

        text_col = ctk.CTkFrame(info_frame, fg_color="transparent")
        text_col.pack(side=ctk.LEFT, fill=ctk.X, expand=True, pady=10)

        summary = service.summarize_server_info(info)

        ctk.CTkLabel(
            text_col,
            text=summary.desc_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        ).pack(anchor=ctk.W)

        latency_color = "#4caf50" if latency_ms < 150 else ("#ff9800" if latency_ms < 400 else "#cd5c5c")
        detail_text = _(
            "tool_peek_detail",
            version=summary.version_name,
            online=summary.online,
            max_p=summary.max_players,
            latency=latency_ms,
        )
        ctk.CTkLabel(
            text_col,
            text=detail_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=latency_color,
            anchor=ctk.W,
        ).pack(anchor=ctk.W, pady=(4, 0))

        if summary.sample_text:
            ctk.CTkLabel(
                text_col,
                text=summary.sample_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
                wraplength=400,
            ).pack(anchor=ctk.W, pady=(2, 0))

    def _on_peek_server(self):
        host = self._port_host_entry.get().strip()
        port_str = self._port_port_entry.get().strip()

        if not host:
            self.set_status(_("tool_port_no_host"), "error")
            return
        if not port_str:
            port_str = "25565"

        service = self._tool_service()
        try:
            # 注意：这里**不做** 1~65535 范围校验（搬运前的行为就是如此，
            # 与 _on_test_port 不一致，见 services/tool_service.parse_port 的说明）
            port = service.parse_port(port_str, validate_range=False)
        except InvalidArgument:
            self.set_status(_("tool_port_invalid_port"), "error")
            return

        peek_btn = self._port_peek_btn
        test_btn = self._port_test_btn
        peek_btn.configure(state=ctk.DISABLED, text=_("tool_peek_querying"))
        test_btn.configure(state=ctk.DISABLED)
        self._clear_server_info()

        def _task():
            peek = service.peek_server(host, port)
            info = peek.info
            err = peek.error
            elapsed = peek.latency_ms

            def _update():
                if err or info is None:
                    text = _("tool_peek_error", error=err or _("tool_peek_unknown_error"))
                    self.set_status(text, "error")
                    self._port_server_info_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))
                    for w in self._port_server_info_frame.winfo_children():
                        w.destroy()
                    sep = ctk.CTkFrame(self._port_server_info_frame, fg_color=COLORS["card_border"], height=1)
                    sep.pack(fill=ctk.X, pady=(4, 8))
                    ctk.CTkLabel(
                        self._port_server_info_frame,
                        text=text,
                        font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                        text_color="#cd5c5c",
                        wraplength=550,
                    ).pack(anchor=ctk.W)
                else:
                    self._display_server_info(info, int(elapsed))
                peek_btn.configure(state=ctk.NORMAL, text=_("tool_port_peek"))
                test_btn.configure(state=ctk.NORMAL, text=_("tool_port_test"))

            self.after(0, _update)

        threading.Thread(target=_task, daemon=True).start()

    # ═══════════ 坐标转换器 ═══════════

    def _build_tool_coordinate_converter(self, parent):
        card = self._make_tool_card(parent, _("tool_coord_title"), _("tool_coord_desc"))

        inp_frame = ctk.CTkFrame(card, fg_color="transparent")
        inp_frame.pack(fill=ctk.X, padx=16, pady=(0, 8))

        labels = [("X:", 0), ("Y:", 1), ("Z:", 2)]
        self._coord_entries = {}
        for lbl_text, col in labels:
            sub = ctk.CTkFrame(inp_frame, fg_color="transparent")
            sub.pack(side=ctk.LEFT, padx=(0, 8) if col < 2 else (0, 0))
            ctk.CTkLabel(
                sub, text=lbl_text, font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=COLORS["text_secondary"]
            ).pack(side=ctk.LEFT, padx=(0, 4))
            entry = ctk.CTkEntry(
                sub,
                width=100,
                height=34,
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                fg_color=COLORS["bg_medium"],
                border_color=COLORS["card_border"],
            )
            entry.pack(side=ctk.LEFT)
            self._coord_entries[lbl_text[0]] = entry

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 6))

        self._coord_to_nether_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_coord_to_nether"),
            width=130,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=lambda: self._on_convert_coord("nether"),
        )
        self._coord_to_nether_btn.pack(side=ctk.LEFT, padx=(0, 8))

        self._coord_to_overworld_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_coord_to_overworld"),
            width=130,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=lambda: self._on_convert_coord("overworld"),
        )
        self._coord_to_overworld_btn.pack(side=ctk.LEFT)

        self._coord_result_frame = ctk.CTkFrame(card, fg_color="transparent")

    def _copy_result_to_clipboard(self, text: str):
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.set_status(_("tool_copy_copied"), "success")
        except Exception as e:
            logger.error(f"复制到剪贴板失败: {e}")
            self.set_status(_("copy_failed", error=str(e)), "error")

    def _parse_coord(self, entry) -> int:
        return self._tool_service().parse_coordinate(entry.get())

    def _on_convert_coord(self, target: str):
        x = self._parse_coord(self._coord_entries["X"])
        y = self._parse_coord(self._coord_entries["Y"])
        z = self._parse_coord(self._coord_entries["Z"])

        for w in self._coord_result_frame.winfo_children():
            w.destroy()
        self._coord_result_frame.pack_forget()

        result = self._tool_service().convert_coordinates(x, y, z, target)
        desc = result.desc

        self._coord_result_frame.pack(fill=ctk.X, padx=16, pady=(0, 12))

        sep = ctk.CTkFrame(self._coord_result_frame, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, pady=(4, 8))

        result_row = ctk.CTkFrame(self._coord_result_frame, fg_color="transparent")
        result_row.pack(fill=ctk.X, pady=(0, 2))

        ctk.CTkLabel(
            result_row,
            text=f"{result.rx}, {result.y}, {result.rz}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(side=ctk.LEFT)

        ctk.CTkButton(
            result_row,
            text=_("tool_copy_result"),
            width=70,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=lambda t=f"{result.rx}, {result.y}, {result.rz}": self._copy_result_to_clipboard(t),
        ).pack(side=ctk.LEFT, padx=(12, 0))

        ctk.CTkLabel(
            self._coord_result_frame,
            text=desc,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=500,
        ).pack(anchor=ctk.W)

    # ═══════════ Hash 计算器 ═══════════

    def _build_tool_hash_calculator(self, parent):
        card = self._make_tool_card(parent, _("tool_hash_title"), _("tool_hash_desc"))

        file_row = ctk.CTkFrame(card, fg_color="transparent")
        file_row.pack(fill=ctk.X, padx=16, pady=(0, 8))

        self._hash_file_entry = ctk.CTkEntry(
            file_row,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=_("tool_hash_select_file"),
        )
        self._hash_file_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 6))

        ctk.CTkButton(
            file_row,
            text="📂",
            width=40,
            height=34,
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_browse_hash_file,
        ).pack(side=ctk.RIGHT)

        algo_row = ctk.CTkFrame(card, fg_color="transparent")
        algo_row.pack(fill=ctk.X, padx=16, pady=(0, 10))

        self._hash_algo_var = ctk.StringVar(value="SHA256")
        for alg in ("MD5", "SHA1", "SHA256", "SHA512"):
            ctk.CTkRadioButton(
                algo_row,
                text=alg,
                variable=self._hash_algo_var,
                value=alg,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
                border_color=COLORS["text_secondary"],
                text_color=COLORS["text_primary"],
            ).pack(side=ctk.LEFT, padx=(0, 15))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 4))

        self._hash_calc_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_hash_calculate"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_calc_hash,
        )
        self._hash_calc_btn.pack(side=ctk.LEFT)

        self._hash_result_frame = ctk.CTkFrame(card, fg_color="transparent")

    def _on_browse_hash_file(self):
        path = filedialog.askopenfilename(title=_("tool_hash_select_file"), parent=self)
        if path:
            self._hash_file_entry.delete(0, "end")
            self._hash_file_entry.insert(0, path)

    def _on_calc_hash(self):
        filepath = self._hash_file_entry.get().strip()
        if not filepath or not os.path.isfile(filepath):
            self.set_status(_("tool_hash_file_error"), "error")
            return

        algo = self._hash_algo_var.get()
        service = self._tool_service()

        btn = self._hash_calc_btn
        btn.configure(state=ctk.DISABLED, text=_("tool_hash_calculating"))

        for w in self._hash_result_frame.winfo_children():
            w.destroy()
        self._hash_result_frame.pack_forget()

        def _task():
            try:
                result = service.hash_file(filepath, algo)

                def _done():
                    self._hash_result_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))
                    sep = ctk.CTkFrame(self._hash_result_frame, fg_color=COLORS["card_border"], height=1)
                    sep.pack(fill=ctk.X, pady=(4, 8))
                    hash_text = f"{algo}:  {result}"
                    ctk.CTkLabel(
                        self._hash_result_frame,
                        text=hash_text,
                        font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                        text_color=COLORS["accent"],
                        wraplength=550,
                    ).pack(anchor=ctk.W, pady=(0, 2))
                    ctk.CTkButton(
                        self._hash_result_frame,
                        text=_("tool_copy_result"),
                        width=70,
                        height=26,
                        font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                        fg_color=COLORS["bg_light"],
                        hover_color=COLORS["card_border"],
                        command=lambda t=hash_text: self._copy_result_to_clipboard(t),
                    ).pack(anchor=ctk.W, pady=(0, 4))
                    fname = os.path.basename(filepath)
                    ctk.CTkLabel(
                        self._hash_result_frame,
                        text=_("tool_hash_file", name=fname, size=_format_size(os.path.getsize(filepath))),
                        font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                        text_color=COLORS["text_secondary"],
                    ).pack(anchor=ctk.W)
                    btn.configure(state=ctk.NORMAL, text=_("tool_hash_calculate"))

                self.after(0, _done)
            except Exception as e:
                logger.error(f"Hash 计算失败: {e}")
                _hash_err = str(e)

                def _error():
                    btn.configure(state=ctk.NORMAL, text=_("tool_hash_calculate"))
                    self.set_status(_("tool_hash_calc_error", error=_hash_err), "error")

                self.after(0, _error)

        threading.Thread(target=_task, daemon=True).start()

    def _on_delete_selected_junk(self):
        selected = []
        for dir_key, info in self._clean_junk_dirs.items():
            for fp, (cb, _row, size) in info["files"].items():
                if cb.get():
                    selected.append((dir_key, fp, size))
        if not selected:
            self.set_status(_("tool_clean_junk_select_none"), "error")
            return

        btn = self._clean_junk_btn
        btn.configure(state=ctk.DISABLED, text=_("tool_clean_junk_deleting"))
        service = self._tool_service()

        def _task():
            result = service.delete_junk_files(selected)
            deleted = result.deleted
            failed = result.failed
            deleted_size = result.deleted_size

            def _update_ui():
                for dir_key, fp, _size in selected:
                    info = self._clean_junk_dirs.get(dir_key)
                    if info is None:
                        continue
                    entry = info["files"].pop(fp, None)
                    if entry is not None:
                        entry[1].destroy()
                for dir_key in list(self._clean_junk_dirs.keys()):
                    info = self._clean_junk_dirs[dir_key]
                    if not info["files"]:
                        info["dir_row"].destroy()
                        info["file_frame"].destroy()
                        del self._clean_junk_dirs[dir_key]
                    else:
                        dir_size = sum(s for _cb, _row, s in info["files"].values())
                        info["count_label"].configure(
                            text=_("tool_clean_junk_dir_files", count=len(info["files"]), size=_format_size(dir_size))
                        )
                        self._sync_junk_dir_checkbox(dir_key)
                self._clean_junk_status.configure(
                    text=_("tool_clean_junk_done", deleted=deleted, failed=failed, size=_format_size(deleted_size)),
                    text_color=COLORS["accent"] if failed == 0 else COLORS["text_secondary"],
                )
                self._clean_junk_status.pack(anchor=ctk.W, padx=16, pady=(0, 8))
                if not self._clean_junk_dirs:
                    self._clean_junk_list_frame.pack_forget()
                    self._clean_junk_selected_label.pack_forget()
                    self._reset_clean_junk_btn()
                else:
                    self._refresh_junk_selection()
                    btn.configure(
                        state=ctk.NORMAL,
                        text=_("tool_clean_junk_delete_selected"),
                        fg_color=COLORS["accent"],
                        hover_color=COLORS["accent_hover"],
                        command=self._on_delete_selected_junk,
                    )

            self.after(0, _update_ui)

        threading.Thread(target=_task, daemon=True).start()

    def _build_tool_daily_fortune(self, parent):
        card = self._make_tool_card(parent, _("tool_fortune_title"), _("tool_fortune_desc"))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 4))

        self._fortune_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_fortune_check"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_check_fortune,
        )
        self._fortune_btn.pack(side=ctk.LEFT)

        self._fortune_result_frame = ctk.CTkFrame(card, fg_color="transparent")

    def _on_check_fortune(self):
        fortune = self._tool_service().daily_fortune()
        today = fortune.today
        value = fortune.value
        level_key = fortune.level_key
        emoji = fortune.emoji

        level_text = _(level_key)
        color_map = {
            "tool_fortune_terrible": "#8b0000",
            "tool_fortune_bad": "#cd5c5c",
            "tool_fortune_normal": "#a0a0b0",
            "tool_fortune_good": "#4caf50",
            "tool_fortune_great": "#ff9800",
            "tool_fortune_legendary": "#e94560",
        }

        for w in self._fortune_result_frame.winfo_children():
            w.destroy()
        self._fortune_result_frame.pack_forget()

        self._fortune_result_frame.pack(fill=ctk.X, padx=16, pady=(0, 12))

        value_label = ctk.CTkLabel(
            self._fortune_result_frame,
            text=f"{emoji}  {value}  {level_text}  {emoji}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=24, weight="bold"),
            text_color=color_map.get(level_key, COLORS["text_primary"]),
        )
        value_label.pack(anchor=ctk.W, pady=(4, 0))

        ctk.CTkLabel(
            self._fortune_result_frame,
            text=_("tool_fortune_date", date=today),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(2, 0))

    def _build_tool_multi_download(self, parent):
        card = self._make_tool_card(parent, _("tool_download_title"), _("tool_download_desc"))

        url_row = ctk.CTkFrame(card, fg_color="transparent")
        url_row.pack(fill=ctk.X, padx=16, pady=(0, 6))

        ctk.CTkLabel(
            url_row,
            text=_("tool_download_url"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
            width=80,
        ).pack(side=ctk.LEFT)

        self._dl_url_entry = ctk.CTkEntry(
            url_row,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="https://...",
        )
        self._dl_url_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        ua_row = ctk.CTkFrame(card, fg_color="transparent")
        ua_row.pack(fill=ctk.X, padx=16, pady=(0, 6))

        ctk.CTkLabel(
            ua_row,
            text=_("tool_download_ua"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
            width=80,
        ).pack(side=ctk.LEFT)

        self._dl_ua_entry = ctk.CTkEntry(
            ua_row,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
        )
        self._dl_ua_entry.insert(0, "FMCL/2.0")
        self._dl_ua_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        save_row = ctk.CTkFrame(card, fg_color="transparent")
        save_row.pack(fill=ctk.X, padx=16, pady=(0, 10))

        ctk.CTkLabel(
            save_row,
            text=_("tool_download_save_path"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
            width=80,
        ).pack(side=ctk.LEFT)

        self._dl_save_entry = ctk.CTkEntry(
            save_row,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
        )
        self._dl_save_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 6))

        self._dl_browse_btn = ctk.CTkButton(
            save_row,
            text="📂",
            width=40,
            height=34,
            font=ctk.CTkFont(size=14),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_browse_save_path,
        )
        self._dl_browse_btn.pack(side=ctk.RIGHT)

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=16, pady=(0, 4))

        self._dl_start_btn = ctk.CTkButton(
            btn_row,
            text=_("tool_download_start"),
            width=100,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_start_download,
        )
        self._dl_start_btn.pack(side=ctk.LEFT)

        self._dl_progress_frame = ctk.CTkFrame(card, fg_color="transparent")

    def _on_browse_save_path(self):
        path = filedialog.asksaveasfilename(title=_("tool_download_save_title"), parent=self)
        if path:
            self._dl_save_entry.delete(0, "end")
            self._dl_save_entry.insert(0, path)

    def _get_minecraft_dir_for_tools(self) -> Path:
        try:
            if self.callbacks and "get_minecraft_dir" in self.callbacks:
                return Path(self.callbacks["get_minecraft_dir"]())
        except Exception:
            pass
        try:
            from config import config

            return config.minecraft_dir
        except Exception:
            pass
        return Path(".minecraft")

    def _get_download_threads_for_tools(self) -> int:
        try:
            if self.callbacks and "get_download_threads" in self.callbacks:
                return self.callbacks["get_download_threads"]()
        except Exception:
            pass
        try:
            from config import config

            return config.download_threads
        except Exception:
            pass
        return 4

    def _on_start_download(self):
        url = self._dl_url_entry.get().strip()
        ua = self._dl_ua_entry.get().strip()
        save_path = self._dl_save_entry.get().strip()

        service = self._tool_service()

        if not url:
            self.set_status(_("tool_download_no_url"), "error")
            return
        if not save_path:
            save_dir = filedialog.askdirectory(title=_("tool_download_save_title"), parent=self)
            if not save_dir:
                return
        else:
            save_dir = os.path.dirname(save_path)
            if not save_dir:
                save_dir = "."

        if not os.path.isdir(save_dir):
            try:
                service.ensure_directory(save_dir)
            except OSError as e:
                self.set_status(_("tool_download_mkdir_error", error=str(e)), "error")
                return

        if not ua:
            ua = "FMCL/2.0"

        for w in self._dl_progress_frame.winfo_children():
            w.destroy()
        self._dl_progress_frame.pack_forget()
        self._dl_progress_frame.pack(fill=ctk.X, padx=16, pady=(4, 12))

        self._dl_status_label = ctk.CTkLabel(
            self._dl_progress_frame,
            text=_("tool_download_connecting"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._dl_status_label.pack(anchor=ctk.W, pady=(0, 4))

        self._dl_progress_bar = ctk.CTkProgressBar(
            self._dl_progress_frame, width=400, height=12, fg_color=COLORS["bg_light"], progress_color=COLORS["accent"]
        )
        self._dl_progress_bar.pack(fill=ctk.X, pady=(0, 4))
        self._dl_progress_bar.set(0)

        self._dl_speed_label = ctk.CTkLabel(
            self._dl_progress_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._dl_speed_label.pack(anchor=ctk.W)

        self._dl_start_btn.configure(state=ctk.DISABLED, text=_("tool_download_downloading"))

        self._dl_cancel_flag = False

        def _on_progress(downloaded, total_size, speed):
            def _update():
                if total_size > 0:
                    self._dl_progress_bar.set(min(downloaded / total_size, 1.0))
                self._dl_speed_label.configure(text=_format_size(int(speed)) + "/s")
                self._dl_status_label.configure(
                    text=_(
                        "tool_download_progress",
                        current=_format_size(downloaded),
                        total=_format_size(total_size),
                    )
                )

            self.after(0, _update)

        def _task():
            num_threads = self._get_download_threads_for_tools()
            self._dl_cancel_flag = False

            try:
                outcome = service.download_multi(
                    url,
                    service.resolve_download_target(url, save_path, save_dir),
                    user_agent=ua,
                    threads=num_threads,
                    cancel_check=lambda: self._dl_cancel_flag,
                    on_progress=_on_progress,
                )

                if outcome.status == "cancelled":

                    def _cancel_ui():
                        self._dl_status_label.configure(
                            text=_("tool_download_cancelled"), text_color=COLORS["text_secondary"]
                        )
                        self._dl_speed_label.configure(text="")
                        self._dl_start_btn.configure(state=ctk.NORMAL, text=_("tool_download_start"))

                    self.after(0, _cancel_ui)
                    return

                def _done_ui():
                    self._dl_progress_bar.set(1)
                    self._dl_speed_label.configure(text="")
                    self._dl_status_label.configure(
                        text=_("tool_download_success", path=outcome.path), text_color=COLORS["accent"]
                    )
                    self._dl_start_btn.configure(state=ctk.NORMAL, text=_("tool_download_start"))

                self.after(0, _done_ui)

            except Exception as e:
                if str(e) == "cancelled":
                    return
                logger.error(f"下载失败: {e}")
                _dl_err = str(e)

                def _error_ui():
                    self._dl_status_label.configure(text=_("tool_download_error", error=_dl_err), text_color="#cd5c5c")
                    self._dl_speed_label.configure(text="")
                    self._dl_start_btn.configure(state=ctk.NORMAL, text=_("tool_download_start"))

                self.after(0, _error_ui)

        threading.Thread(target=_task, daemon=True).start()
