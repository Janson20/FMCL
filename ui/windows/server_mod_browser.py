"""服务器 Modrinth Mod 浏览窗口 - 浏览并安装服务端模组"""
import threading
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


class ServerModBrowserWindow(ctk.CTkToplevel):
    """服务器 Modrinth 模组浏览窗口 - 仅显示支持服务端的模组"""

    PAGE_SIZE = 10

    def __init__(self, parent, version_id: str, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self.version_id = version_id
        self.callbacks = callbacks

        from modrinth import parse_mod_loader_from_version, parse_game_version_from_version
        self._mod_loader = parse_mod_loader_from_version(version_id)
        self._game_version = parse_game_version_from_version(version_id)

        self.title(_("server_mod_browser_title", version=version_id))
        self.geometry("800x680")
        self.minsize(720, 580)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)

        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 800, 680
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self._current_offset = 0
        self._total_hits = 0
        self._current_query = ""
        self._ai_cached_hits: Optional[List[Dict]] = None
        self._search_entry: Optional[ctk.CTkEntry] = None
        self._list_frame: Optional[ctk.CTkScrollableFrame] = None
        self._loading_label: Optional[ctk.CTkLabel] = None
        self._page_label: Optional[ctk.CTkLabel] = None
        self._prev_btn: Optional[ctk.CTkButton] = None
        self._next_btn: Optional[ctk.CTkButton] = None
        self._result_count_label: Optional[ctk.CTkLabel] = None
        self._status_label: Optional[ctk.CTkLabel] = None

        self._build_ui()
        self.after(300, self._do_initial_search)

    def _build_ui(self):
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        header = ctk.CTkFrame(main_frame, fg_color="transparent")
        header.pack(fill=ctk.X, pady=(0, 10))

        ctk.CTkLabel(
            header,
            text=_("server_mod_browser_header", version=self.version_id),
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        info_parts = []
        if self._game_version:
            info_parts.append(f"MC {self._game_version}")
        if self._mod_loader:
            info_parts.append(self._mod_loader.capitalize())
        info_parts.append(_("server_mod_browser_server_only"))
        info_text = " | ".join(info_parts)
        info_color = COLORS["success"]
        ctk.CTkLabel(
            header,
            text=info_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=info_color,
        ).pack(side=ctk.RIGHT)

        search_frame = ctk.CTkFrame(main_frame, fg_color="transparent", height=40)
        search_frame.pack(fill=ctk.X, pady=(0, 8))
        search_frame.pack_propagate(False)

        self._search_entry = ctk.CTkEntry(
            search_frame,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=_("server_mod_browser_search_placeholder"),
        )
        self._search_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._search_entry.bind("<Return>", lambda e: self._on_search())

        search_btn = ctk.CTkButton(
            search_frame,
            text=_("mod_browser_search"),
            width=80,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_search,
        )
        search_btn.pack(side=ctk.LEFT)

        ai_search_btn = ctk.CTkButton(
            search_frame,
            text=_("ai_search_btn"),
            width=80,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=self._on_ai_search,
        )
        ai_search_btn.pack(side=ctk.LEFT, padx=(4, 0))

        list_container = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        list_container.pack(fill=ctk.BOTH, expand=True, pady=(0, 8))

        self._list_frame = ctk.CTkScrollableFrame(
            list_container,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        self._list_frame.pack(fill=ctk.BOTH, expand=True, padx=5, pady=5)

        self._loading_label = ctk.CTkLabel(
            self._list_frame,
            text=_("mod_browser_loading"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            justify=ctk.CENTER,
        )
        self._loading_label.pack(pady=40)

        page_frame = ctk.CTkFrame(main_frame, fg_color="transparent", height=34)
        page_frame.pack(fill=ctk.X)
        page_frame.pack_propagate(False)

        self._prev_btn = ctk.CTkButton(
            page_frame,
            text=_("mod_browser_prev_page"),
            width=90,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_prev_page,
        )
        self._prev_btn.pack(side=ctk.LEFT)

        self._page_label = ctk.CTkLabel(
            page_frame,
            text="0 / 0",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            width=100,
        )
        self._page_label.pack(side=ctk.LEFT, padx=10)

        self._next_btn = ctk.CTkButton(
            page_frame,
            text=_("mod_browser_next_page"),
            width=90,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_next_page,
        )
        self._next_btn.pack(side=ctk.LEFT)

        self._result_count_label = ctk.CTkLabel(
            page_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._result_count_label.pack(side=ctk.RIGHT)

        self._status_label = ctk.CTkLabel(
            main_frame,
            text=_("mod_browser_ready"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._status_label.pack(anchor=ctk.W, pady=(5, 0))

    def _do_initial_search(self):
        self._current_query = ""
        self._current_offset = 0
        self._set_status(_("mod_browser_searching"))
        self._run_in_thread(self._do_search)

    def _on_search(self):
        if self._search_entry:
            self._current_query = self._search_entry.get().strip()
        self._current_offset = 0
        self._ai_cached_hits = None
        self._set_status(_("mod_browser_searching"))
        self._run_in_thread(self._do_search)

    def _on_ai_search(self):
        from ui.i18n import _
        token = self.callbacks.get("get_ai_token", lambda: "")()
        if not token:
            self._set_status(_("ai_search_login_required"))
            return
        if self._search_entry:
            query = self._search_entry.get().strip()
        else:
            query = ""
        if not query:
            return
        self._current_query = query
        self._set_status(_("ai_search_optimizing"))
        self._ai_cached_hits = None
        self._run_in_thread(self._do_ai_search, query, token)

    def _do_ai_search(self, query: str, token: str):
        from modrinth import ai_expand_search_keywords, search_server_mods
        try:
            keywords = ai_expand_search_keywords(query, token)
            if not keywords:
                self.after(0, lambda: self._set_status(_("mod_browser_no_results")))
                return

            seen_ids = set()
            merged = []

            for kw in keywords:
                try:
                    result = search_server_mods(
                        query=kw,
                        game_version=self._game_version,
                        mod_loader=self._mod_loader,
                        offset=0,
                        limit=30,
                    )
                    hits = result.get("hits", [])
                    for hit in hits:
                        pid = hit.get("project_id", "")
                        if pid and pid not in seen_ids:
                            seen_ids.add(pid)
                            merged.append(hit)
                except Exception as e:
                    logger.warning(f"AI搜索关键词 '{kw}' 失败: {e}")

            merged.sort(key=lambda h: h.get("downloads", 0), reverse=True)
            self._ai_cached_hits = merged
            self._total_hits = len(merged)
            self._current_offset = 0
            page = merged[:self.PAGE_SIZE]
            self.after(0, self._render_results, page)
            self.after(0, self._update_pagination)
            self.after(0, lambda: self._set_status(
                _("ai_search_done", keywords=", ".join(keywords), total=len(merged))
            ))
        except Exception as e:
            logger.error(f"AI搜索失败: {e}")
            self.after(0, lambda: self._set_status(
                _("mod_browser_search_failed_status", error=str(e))
            ))

    def _do_search(self):
        from modrinth import search_server_mods
        try:
            result = search_server_mods(
                query=self._current_query,
                game_version=self._game_version,
                mod_loader=self._mod_loader,
                offset=self._current_offset,
                limit=self.PAGE_SIZE,
            )
            hits = result.get("hits", [])
            self._total_hits = result.get("total_hits", 0)
            self.after(0, self._render_results, hits)
            self.after(0, self._update_pagination)
            if self._current_query:
                self.after(0, lambda: self._set_status(
                    _("mod_browser_result_range",
                      start=self._current_offset + 1,
                      end=self._current_offset + len(hits),
                      total=self._total_hits)
                ))
            else:
                self.after(0, lambda: self._set_status(
                    _("mod_browser_total_found", total=self._total_hits)
                ))
        except Exception as e:
            logger.error(f"搜索服务端模组失败: {e}")
            self.after(0, lambda: self._set_status(
                _("mod_browser_search_failed_status", error=str(e))
            ))

    def _render_results(self, hits: List[Dict]):
        if not self._list_frame or not self._list_frame.winfo_exists():
            return
        for widget in self._list_frame.winfo_children():
            widget.destroy()

        if not hits:
            no_results_label = ctk.CTkLabel(
                self._list_frame,
                text=_("mod_browser_no_mods"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=14),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            )
            no_results_label.pack(pady=40)
            return

        for mod in hits:
            project_id = mod.get("project_id", "")
            title = mod.get("title", _("mod_browser_unknown"))
            description = mod.get("description", "")
            downloads = mod.get("downloads", 0)
            categories = mod.get("categories", [])
            versions_display = mod.get("versions", [])

            card = ctk.CTkFrame(
                self._list_frame,
                fg_color=COLORS["bg_medium"],
                corner_radius=8,
            )
            card.pack(fill=ctk.X, pady=3, padx=2)

            top_row = ctk.CTkFrame(card, fg_color="transparent", height=36)
            top_row.pack(fill=ctk.X, padx=10, pady=(8, 2))
            top_row.pack_propagate(False)

            ctk.CTkLabel(
                top_row,
                text=f"🧩 {title}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
            ).pack(side=ctk.LEFT, fill=ctk.X, expand=True)

            dl_text = self._format_downloads(downloads)
            ctk.CTkLabel(
                top_row,
                text=f"📥 {dl_text}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
            ).pack(side=ctk.LEFT, padx=(5, 0))

            install_btn = ctk.CTkButton(
                top_row,
                text=_("mod_browser_install"),
                width=70,
                height=28,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color=COLORS["success"],
                hover_color="#27ae60",
                text_color=COLORS["text_primary"],
                command=lambda pid=project_id, t=title: self._on_install_mod(pid, t),
            )
            install_btn.pack(side=ctk.RIGHT, padx=(8, 0))

            if description:
                ctk.CTkLabel(
                    card,
                    text=description,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                    text_color=COLORS["text_secondary"],
                    wraplength=700,
                    justify=ctk.LEFT,
                    anchor=ctk.W,
                ).pack(fill=ctk.X, padx=10, pady=(0, 4))

            tag_parts = []
            if categories:
                loader_tags = [c for c in categories if c in ("forge", "fabric", "neoforge", "quilt")]
                if loader_tags:
                    tag_parts.append(" | ".join(c.capitalize() for c in loader_tags))
            if versions_display:
                from modrinth import compress_game_versions
                compressed = compress_game_versions(versions_display)
                if compressed:
                    tag_parts.append(compressed)

            if tag_parts:
                tags_text = "  ·  ".join(tag_parts)
                ctk.CTkLabel(
                    card,
                    text=tags_text,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                    text_color=COLORS["text_secondary"],
                    anchor=ctk.W,
                ).pack(fill=ctk.X, padx=10, pady=(0, 8))

    def _update_pagination(self):
        if not self.winfo_exists():
            return
        total_pages = max(1, (self._total_hits + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        current_page = self._current_offset // self.PAGE_SIZE + 1
        self._page_label.configure(text=f"{current_page} / {total_pages}")
        has_prev = self._current_offset > 0
        has_next = self._current_offset + self.PAGE_SIZE < self._total_hits
        self._prev_btn.configure(state=ctk.NORMAL if has_prev else ctk.DISABLED)
        self._next_btn.configure(state=ctk.NORMAL if has_next else ctk.DISABLED)
        if self._total_hits > 0:
            self._result_count_label.configure(
                text=_("mod_browser_total_found", total=self._total_hits)
            )

    def _on_prev_page(self):
        if self._current_offset >= self.PAGE_SIZE:
            self._current_offset -= self.PAGE_SIZE
            if self._ai_cached_hits is not None:
                self._render_results(self._ai_cached_hits[self._current_offset:self._current_offset + self.PAGE_SIZE])
                self._update_pagination()
            else:
                self._set_status(_("mod_browser_searching"))
                self._run_in_thread(self._do_search)

    def _on_next_page(self):
        if self._current_offset + self.PAGE_SIZE < self._total_hits:
            self._current_offset += self.PAGE_SIZE
            if self._ai_cached_hits is not None:
                self._render_results(self._ai_cached_hits[self._current_offset:self._current_offset + self.PAGE_SIZE])
                self._update_pagination()
            else:
                self._set_status(_("mod_browser_searching"))
                self._run_in_thread(self._do_search)

    def _on_install_mod(self, project_id: str, title: str):
        self._set_status(_("mod_browser_fetching_version", title=title))
        self._run_in_thread(lambda: self._install_mod(project_id, title))

    def _install_mod(self, project_id: str, title: str):
        from modrinth import install_mod_with_deps

        try:
            if not self._game_version or not self._mod_loader:
                self.after(0, lambda: self._set_status(_("mod_browser_unknown_loader")))
                return

            mods_dir = self._get_mods_dir()

            success, result, installed_names = install_mod_with_deps(
                project_id,
                game_version=self._game_version,
                mod_loader=self._mod_loader,
                mods_dir=mods_dir,
                status_callback=lambda msg: self.after(0, lambda: self._set_status(msg)),
            )

            if success:
                if len(installed_names) > 1:
                    deps = ", ".join(installed_names[:-1])
                    self.after(
                        0,
                        lambda: self._set_status(
                            _("mod_browser_install_success_deps", title=title, deps=deps),
                        ),
                    )
                else:
                    self.after(
                        0,
                        lambda: self._set_status(_("mod_browser_install_success", title=title)),
                    )
                logger.info(f"服务端模组安装成功: {installed_names} -> {result}")
            else:
                self.after(0, lambda: self._set_status(_("mod_browser_install_failed", error=result)))
                logger.error(f"服务端模组安装失败: {result}")

        except Exception as e:
            error_msg = str(e)
            self.after(
                0,
                lambda: self._set_status(_("mod_browser_install_error", error=error_msg)),
            )
            logger.error(f"安装服务端模组失败: {e}")

    def _get_mods_dir(self) -> str:
        server_dir = Path(".")
        if "get_server_dir" in self.callbacks:
            server_dir = Path(self.callbacks["get_server_dir"]())

        v = self.version_id.lower()
        if any(loader in v for loader in ("forge", "fabric", "neoforge")):
            mods_dir = server_dir / "versions" / self.version_id / "mods"
        else:
            mods_dir = server_dir / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        return str(mods_dir)

    @staticmethod
    def _format_downloads(count: int) -> str:
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _set_status(self, text: str):
        try:
            if self.winfo_exists() and self._status_label:
                self._status_label.configure(text=text)
        except Exception:
            pass

    def _run_in_thread(self, target, *args, **kwargs):
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
