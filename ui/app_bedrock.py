"""ModernApp 基岩版标签页 Mixin - 下载、启动、删除基岩版版本"""

from typing import Any, Dict, List, Optional

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _

_TYPE_LABEL_KEY = {"release": "bedrock_type_release", "preview": "bedrock_type_preview", "beta": "bedrock_type_beta"}


class BedrockMixin:
    """基岩版标签页 Mixin（仅 Windows 平台挂载）"""

    def _build_bedrock_tab_content(self):
        """构建基岩版标签页内容"""
        content = ctk.CTkFrame(self.bedrock_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        self.bedrock_installed: List[Dict[str, Any]] = []
        self.bedrock_available: List[Dict[str, Any]] = []
        self.bedrock_filtered: List[Dict[str, Any]] = []
        self.bedrock_selected: Optional[Dict[str, Any]] = None

        self.bedrock_search_var = ctk.StringVar()
        self.bedrock_search_var.trace_add("write", lambda *_: self._on_bedrock_filter_change())
        self.bedrock_filter_var = ctk.StringVar(value=_("bedrock_filter_all"))

        self._build_bedrock_installed_panel(content)
        self._build_bedrock_download_panel(content)
        self._refresh_bedrock_versions()

    # ─── 左侧：已安装版本 ───────────────────────────────────

    def _build_bedrock_installed_panel(self, parent):
        self.bedrock_installed_panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12)
        self.bedrock_installed_panel.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 8))

        self.bedrock_installed_title = ctk.CTkLabel(
            self.bedrock_installed_panel,
            text=_("bedrock_installed"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self.bedrock_installed_title.pack(padx=15, pady=(15, 8), anchor=ctk.W)

        self.bedrock_installed_count = ctk.CTkLabel(
            self.bedrock_installed_panel,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self.bedrock_installed_count.pack(padx=15, anchor=ctk.W)

        sep = ctk.CTkFrame(self.bedrock_installed_panel, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, padx=15, pady=(8, 5))

        self.bedrock_installed_list = ctk.CTkScrollableFrame(
            self.bedrock_installed_panel,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        self.bedrock_installed_list.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._theme_refs.append((self.bedrock_installed_panel, {"fg_color": "card_bg"}))
        self._theme_refs.append((self.bedrock_installed_title, {"text_color": "text_primary"}))
        self._theme_refs.append((self.bedrock_installed_count, {"text_color": "text_secondary"}))
        self._theme_refs.append((sep, {"fg_color": "card_border"}))
        self._theme_refs.append((self.bedrock_installed_list, {"scrollbar_button_color": "bg_light"}))

    def _render_bedrock_installed(self):
        """渲染已安装版本列表"""
        for child in self.bedrock_installed_list.winfo_children():
            child.destroy()
        self.bedrock_installed_cards: List[tuple] = []
        self.bedrock_installed_count.configure(text=_("bedrock_count").format(count=len(self.bedrock_installed)))
        if not self.bedrock_installed:
            empty = ctk.CTkLabel(
                self.bedrock_installed_list,
                text=_("bedrock_no_installed"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
            )
            empty.pack(pady=30)
            return
        for info in self.bedrock_installed:
            self._render_bedrock_card(info)

    def _render_bedrock_card(self, info: Dict[str, Any]):
        card = ctk.CTkFrame(self.bedrock_installed_list, fg_color=COLORS["bg_light"], corner_radius=8)
        card.pack(fill=ctk.X, padx=2, pady=4)
        card.pack_propagate(False)
        card.configure(height=100)

        name_label = ctk.CTkLabel(
            card,
            text=info.get("name", ""),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        name_label.pack(anchor=ctk.W, padx=12, pady=(8, 0))

        type_text = _(_TYPE_LABEL_KEY.get(info.get("game_type", "release"), "bedrock_type_release"))
        version_text = info.get("version", "")
        detail = ctk.CTkLabel(
            card,
            text=f"{version_text}  |  {info.get('build_type', 'UWP')}  |  {type_text}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        detail.pack(anchor=ctk.W, padx=12)

        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.pack(fill=ctk.X, padx=12, pady=(4, 8))

        name = info.get("name", "")
        launch_btn = ctk.CTkButton(
            btn_frame,
            text=_("bedrock_launch"),
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda n=name: self._on_bedrock_launch(n),
        )
        launch_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 6))

        remove_btn = ctk.CTkButton(
            btn_frame,
            text=_("bedrock_delete"),
            height=36,
            width=80,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["error"],
            text_color=COLORS["text_secondary"],
            command=lambda n=name: self._on_bedrock_remove(n),
        )
        remove_btn.pack(side=ctk.RIGHT)

        self.bedrock_installed_cards.append((card, name_label, detail, launch_btn, remove_btn))

    # ─── 右侧：下载面板 ─────────────────────────────────────

    def _build_bedrock_download_panel(self, parent):
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, width=420)
        panel.pack(side=ctk.RIGHT, fill=ctk.Y, padx=(8, 0))
        panel.pack_propagate(False)

        title = ctk.CTkLabel(
            panel,
            text=_("bedrock_available"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title.pack(padx=15, pady=(15, 8), anchor=ctk.W)
        self._theme_refs.append((title, {"text_color": "text_primary"}))

        sep = ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, padx=15, pady=(0, 10))
        self._theme_refs.append((sep, {"fg_color": "card_border"}))

        filter_row = ctk.CTkFrame(panel, fg_color="transparent")
        filter_row.pack(fill=ctk.X, padx=15, pady=(0, 8))

        search_entry = ctk.CTkEntry(
            filter_row,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            textvariable=self.bedrock_search_var,
            placeholder_text=_("bedrock_search_placeholder"),
        )
        search_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._theme_refs.append((search_entry, {"fg_color": "bg_medium", "border_color": "card_border"}))

        filter_menu = ctk.CTkOptionMenu(
            filter_row,
            variable=self.bedrock_filter_var,
            values=[_("bedrock_filter_all"), "UWP", "GDK"],
            height=32,
            width=90,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
            command=lambda _v: self._on_bedrock_filter_change(),
        )
        filter_menu.pack(side=ctk.RIGHT)
        self._theme_refs.append((filter_menu, {"fg_color": "bg_medium", "button_color": "bg_light"}))

        self.bedrock_available_list = ctk.CTkScrollableFrame(
            panel,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        self.bedrock_available_list.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 8))
        self._theme_refs.append((self.bedrock_available_list, {"scrollbar_button_color": "bg_light"}))

        # 分页栏（每页 20 条，对齐游戏标签页）
        self._bedrock_page_size = 20
        self._bedrock_page = 1
        pager = ctk.CTkFrame(panel, fg_color="transparent", height=30)
        pager.pack(fill=ctk.X, padx=15, pady=(0, 6))
        pager.pack_propagate(False)

        pager_btn_cfg = {
            "height": 26,
            "width": 70,
            "font": ctk.CTkFont(family=FONT_FAMILY, size=12),
            "fg_color": COLORS["bg_medium"],
            "hover_color": COLORS["bg_light"],
            "text_color": COLORS["text_primary"],
        }
        self.bedrock_prev_page_btn = ctk.CTkButton(
            pager, text=_("bedrock_page_prev"), command=self._on_bedrock_prev_page, **pager_btn_cfg
        )
        self.bedrock_prev_page_btn.pack(side=ctk.LEFT)
        self.bedrock_next_page_btn = ctk.CTkButton(
            pager, text=_("bedrock_page_next"), command=self._on_bedrock_next_page, **pager_btn_cfg
        )
        self.bedrock_next_page_btn.pack(side=ctk.RIGHT)
        self.bedrock_page_info_label = ctk.CTkLabel(
            pager,
            text="1/1",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self.bedrock_page_info_label.pack(side=ctk.RIGHT, padx=8)

        self._theme_refs.append((self.bedrock_page_info_label, {"text_color": "text_secondary"}))
        self._theme_refs.append(
            (self.bedrock_prev_page_btn, {"fg_color": "bg_medium", "hover_color": "bg_light", "text_color": "text_primary"})
        )
        self._theme_refs.append(
            (self.bedrock_next_page_btn, {"fg_color": "bg_medium", "hover_color": "bg_light", "text_color": "text_primary"})
        )

        action_row = ctk.CTkFrame(panel, fg_color="transparent")
        action_row.pack(fill=ctk.X, padx=15, pady=(0, 12))

        self.bedrock_install_btn = ctk.CTkButton(
            action_row,
            text=_("bedrock_install"),
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            state=ctk.DISABLED,
            command=self._on_bedrock_install,
        )
        self.bedrock_install_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._theme_refs.append((self.bedrock_install_btn, {"fg_color": "accent", "hover_color": "accent_hover"}))

        refresh_btn = ctk.CTkButton(
            action_row,
            text=_("bedrock_refresh"),
            height=38,
            width=80,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_bedrock_refresh,
        )
        refresh_btn.pack(side=ctk.RIGHT)
        self._theme_refs.append((refresh_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))

    # ─── 数据加载 ───────────────────────────────────────────

    def _refresh_bedrock_versions(self):
        self._run_in_thread(self._load_bedrock_installed)

    def _load_bedrock_installed(self):
        try:
            installed = self.callbacks.get("bedrock_get_installed_versions", lambda: [])()
            self._task_queue.put(("bedrock_installed_loaded", installed))
        except Exception as e:
            logger.error(f"加载已安装基岩版失败: {e}")
            self._task_queue.put(("bedrock_load_error", str(e)))

    def _load_bedrock_available(self):
        try:
            available = self.callbacks.get("bedrock_get_available_versions", lambda: [])()
            self._task_queue.put(("bedrock_available_loaded", available))
        except Exception as e:
            logger.error(f"加载可下载基岩版失败: {e}")
            self._task_queue.put(("bedrock_load_error", str(e)))

    def _on_bedrock_filter_change(self):
        keyword = self.bedrock_search_var.get().strip().lower()
        build_filter = self.bedrock_filter_var.get()
        self.bedrock_filtered = [
            v
            for v in self.bedrock_available
            if (not keyword or keyword in v.get("version", "").lower())
            and (build_filter == _("bedrock_filter_all") or v.get("build_type") == build_filter)
        ]
        self._bedrock_page = 1
        self._render_bedrock_available()

    def _get_bedrock_total_pages(self) -> int:
        if not self.bedrock_filtered:
            return 1
        return max(1, (len(self.bedrock_filtered) + self._bedrock_page_size - 1) // self._bedrock_page_size)

    def _render_bedrock_available(self):
        for child in self.bedrock_available_list.winfo_children():
            child.destroy()
        self.bedrock_available_cards: List[ctk.CTkFrame] = []

        total_pages = self._get_bedrock_total_pages()
        if self._bedrock_page > total_pages:
            self._bedrock_page = total_pages
        if self._bedrock_page < 1:
            self._bedrock_page = 1
        self.bedrock_page_info_label.configure(text=f"{self._bedrock_page}/{total_pages}")
        self.bedrock_prev_page_btn.configure(
            state=ctk.NORMAL if self._bedrock_page > 1 else ctk.DISABLED
        )
        self.bedrock_next_page_btn.configure(
            state=ctk.NORMAL if self._bedrock_page < total_pages else ctk.DISABLED
        )

        start = (self._bedrock_page - 1) * self._bedrock_page_size
        display_versions = self.bedrock_filtered[start : start + self._bedrock_page_size]

        if not self.bedrock_available:
            empty = ctk.CTkLabel(
                self.bedrock_available_list,
                text=_("bedrock_no_available"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
            )
            empty.pack(pady=30)
            return
        if not display_versions:
            empty = ctk.CTkLabel(
                self.bedrock_available_list,
                text=_("bedrock_no_match"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
            )
            empty.pack(pady=30)
            return
        for version in display_versions:
            self._render_bedrock_available_row(version)

    def _on_bedrock_prev_page(self):
        if self._bedrock_page > 1:
            self._bedrock_page -= 1
            self._render_bedrock_available()

    def _on_bedrock_next_page(self):
        if self._bedrock_page < self._get_bedrock_total_pages():
            self._bedrock_page += 1
            self._render_bedrock_available()

    def _render_bedrock_available_row(self, version: Dict[str, Any]):
        selected = self.bedrock_selected and self.bedrock_selected.get("version") == version.get("version")
        card = ctk.CTkFrame(
            self.bedrock_available_list,
            fg_color=COLORS["accent"] if selected else COLORS["bg_light"],
            corner_radius=8,
            cursor="hand2",
        )
        card.pack(fill=ctk.X, padx=2, pady=3)
        card.bind("<Button-1>", lambda _e, v=version: self._select_bedrock_version(v))

        type_text = _(_TYPE_LABEL_KEY.get(version.get("type", "release"), "bedrock_type_release"))
        text_color = COLORS["text_primary"] if not selected else "#ffffff"
        sub_color = COLORS["text_secondary"] if not selected else "#e0e0e0"

        name_label = ctk.CTkLabel(
            card,
            text=version.get("version", ""),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=text_color,
        )
        name_label.pack(anchor=ctk.W, padx=12, pady=(8, 0))
        name_label.bind("<Button-1>", lambda _e, v=version: self._select_bedrock_version(v))

        detail = ctk.CTkLabel(
            card,
            text=f"{version.get('build_type', 'UWP')}  |  {type_text}  |  {version.get('date', '')}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=sub_color,
        )
        detail.pack(anchor=ctk.W, padx=12, pady=(2, 8))
        detail.bind("<Button-1>", lambda _e, v=version: self._select_bedrock_version(v))

        self.bedrock_available_cards.append(card)

    def _refresh_bedrock_colors(self):
        """主题切换时刷新动态创建的基岩版卡片颜色"""
        for card, name_label, detail, launch_btn, remove_btn in getattr(self, "bedrock_installed_cards", []):
            try:
                card.configure(fg_color=COLORS["bg_light"])
                name_label.configure(text_color=COLORS["text_primary"])
                detail.configure(text_color=COLORS["text_secondary"])
                launch_btn.configure(fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"])
                remove_btn.configure(fg_color=COLORS["bg_medium"], text_color=COLORS["text_secondary"])
            except Exception:
                pass
        for card in getattr(self, "bedrock_available_cards", []):
            try:
                card.configure(fg_color=COLORS["bg_light"])
            except Exception:
                pass

    def _select_bedrock_version(self, version: Dict[str, Any]):
        if self.bedrock_selected and self.bedrock_selected.get("version") == version.get("version"):
            return
        self.bedrock_selected = version
        self.bedrock_install_btn.configure(state=ctk.NORMAL)
        self._render_bedrock_available()

    # ─── 操作 ───────────────────────────────────────────────

    def _on_bedrock_install(self):
        if not self.bedrock_selected:
            return
        version = self.bedrock_selected.get("version", "")
        self.bedrock_install_btn.configure(state=ctk.DISABLED)
        self.set_status(_("bedrock_installing").format(version=version), "loading")
        self._run_in_thread(self._install_bedrock_worker, version)

    def _install_bedrock_worker(self, version: str):
        try:
            success, info = self.callbacks["bedrock_install_version"](version, "")
            if success and isinstance(info, dict):
                self._task_queue.put(("bedrock_install_done", (version, True, info.get("name", version))))
            else:
                self._task_queue.put(("bedrock_install_done", (version, False, str(info))))
        except Exception as e:
            logger.error(f"基岩版安装异常: {e}")
            self._task_queue.put(("bedrock_install_done", (version, False, str(e))))

    def _on_bedrock_launch(self, name: str):
        self.set_status(_("bedrock_launching").format(name=name), "loading")
        self._run_in_thread(self._launch_bedrock_worker, name)

    def _launch_bedrock_worker(self, name: str):
        try:
            success, msg = self.callbacks["bedrock_launch_version"](name, "")
            self._task_queue.put(("bedrock_launch_done", (name, success, msg)))
        except Exception as e:
            logger.error(f"基岩版启动异常: {e}")
            self._task_queue.put(("bedrock_launch_done", (name, False, str(e))))

    def _on_bedrock_remove(self, name: str):
        import tkinter.messagebox

        if not tkinter.messagebox.askyesno(
            _("confirm_delete"), _("bedrock_confirm_delete").format(name=name), parent=self
        ):
            return
        self._run_in_thread(self._remove_bedrock_worker, name)

    def _remove_bedrock_worker(self, name: str):
        try:
            success, msg = self.callbacks["bedrock_remove_version"](name)
            self._task_queue.put(("bedrock_remove_done", (name, success, msg)))
        except Exception as e:
            logger.error(f"基岩版删除异常: {e}")
            self._task_queue.put(("bedrock_remove_done", (name, False, str(e))))

    def _on_bedrock_refresh(self):
        self.set_status("", "info")
        self._run_in_thread(self._load_bedrock_available)
        self._run_in_thread(self._load_bedrock_installed)

    def _open_bedrock_folder(self):
        root = self.callbacks.get("bedrock_get_root", lambda: "")()
        if not root:
            return
        try:
            import os

            os.startfile(root)
        except Exception as e:
            logger.warning(f"打开基岩版目录失败: {e}")

    # ─── 任务分发 ───────────────────────────────────────────

    def _handle_bedrock_task(self, task_type: str, data: Any) -> bool:
        """处理基岩版相关队列任务，返回是否已处理"""
        if not hasattr(self, "bedrock_tab") or self.bedrock_tab is None:
            return False
        if task_type == "bedrock_installed_loaded":
            self.bedrock_installed = data or []
            self._render_bedrock_installed()
            self._run_in_thread(self._load_bedrock_available)
            return True

        if task_type == "bedrock_available_loaded":
            self.bedrock_available = data or []
            self._on_bedrock_filter_change()
            release_count = len([v for v in self.bedrock_available if v.get("type") == "release"])
            self.set_status(
                _("bedrock_available_status").format(total=len(self.bedrock_available), release=release_count),
                "success",
            )
            return True

        if task_type == "bedrock_load_error":
            self.set_status(_("bedrock_load_error").format(error=data), "error")
            return True

        if task_type == "bedrock_install_done":
            version, success, info = data
            self.bedrock_install_btn.configure(state=ctk.NORMAL)
            if success:
                self.set_status(_("bedrock_install_success").format(version=info), "success")
                self._run_in_thread(self._load_bedrock_installed)
            else:
                self.set_status(_("bedrock_install_failed").format(version=version, error=info), "error")
            return True

        if task_type == "bedrock_launch_done":
            name, success, msg = data
            if success:
                self.set_status(_("bedrock_launch_success").format(name=name), "success")
            else:
                self.set_status(_("bedrock_launch_failed").format(name=name, error=msg), "error")
            return True

        if task_type == "bedrock_remove_done":
            name, success, msg = data
            if success:
                self.set_status(_("bedrock_remove_success").format(name=name), "success")
                self._run_in_thread(self._load_bedrock_installed)
            else:
                self.set_status(_("bedrock_remove_failed").format(error=msg), "error")
            return True

        return False
