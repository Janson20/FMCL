"""Modrinth 整合包浏览窗口 - 搜索、浏览并下载整合包"""
import os
import threading
from typing import List, Dict, Optional, Callable, Any

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


class ModpackBrowserWindow(ctk.CTkToplevel):
    """Modrinth 整合包浏览窗口 - 搜索、浏览并下载整合包 .mrpack"""

    PAGE_SIZE = 10

    def __init__(self, parent, on_modpack_selected: Callable[[str], None]):
        super().__init__(parent)
        self._on_modpack_selected = on_modpack_selected

        self.title("🌐 从 Modrinth 下载整合包")
        self.geometry("800x640")
        self.minsize(720, 560)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        self.grab_set()

        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 800, 640
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self._current_offset = 0
        self._total_hits = 0
        self._current_query = ""
        self._ai_search_btn = None
        self._ai_cached_hits = None

        self._build_ui()

        self.after(300, lambda: self._run_in_thread(self._do_search))

    def _build_ui(self):
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        header = ctk.CTkFrame(main_frame, fg_color="transparent")
        header.pack(fill=ctk.X, pady=(0, 10))

        ctk.CTkLabel(
            header,
            text="🌐 从 Modrinth 下载整合包",
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        search_frame = ctk.CTkFrame(main_frame, fg_color="transparent", height=40)
        search_frame.pack(fill=ctk.X, pady=(0, 8))
        search_frame.pack_propagate(False)

        self._search_entry = ctk.CTkEntry(
            search_frame,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="🔍 搜索整合包...",
        )
        self._search_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._search_entry.bind("<Return>", lambda e: self._on_search())

        ctk.CTkButton(
            search_frame,
            text="搜索",
            width=80,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_search,
        ).pack(side=ctk.LEFT)

        self._ai_search_btn = ctk.CTkButton(
            search_frame,
            text=_("ai_search_btn"),
            width=80,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=self._on_ai_search,
        )
        self._ai_search_btn.pack(side=ctk.LEFT, padx=(4, 0))

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
            text="⏳ 加载中...",
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
            text="◀ 上一页",
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
            text="下一页 ▶",
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
            text="就绪",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._status_label.pack(anchor=ctk.W, pady=(5, 0))

    def _on_search(self):
        self._current_query = self._search_entry.get().strip()
        self._current_offset = 0
        self._ai_cached_hits = None
        self._set_status("正在搜索...")
        self._run_in_thread(self._do_search)

    def _do_search(self):
        from modrinth import search_modpacks

        try:
            result = search_modpacks(
                query=self._current_query,
                offset=self._current_offset,
                limit=self.PAGE_SIZE,
            )

            hits = result.get("hits", [])
            self._total_hits = result.get("total_hits", 0)

            self.after(0, self._render_results, hits)

        except Exception as e:
            logger.error(f"搜索整合包失败: {e}")
            self.after(0, self._render_error, str(e))

    def _on_ai_search(self):
        from tkinter import messagebox
        query = self._search_entry.get().strip()
        if not query:
            self._current_query = ""
            self._current_offset = 0
            self._on_search()
            return

        from config import config
        token = config.jdz_token
        if not token:
            messagebox.showwarning(
                _("warning"),
                _("ai_search_login_required"),
                parent=self,
            )
            return

        self._current_query = query
        self._current_offset = 0
        if self._ai_search_btn:
            try:
                self._ai_search_btn.configure(state="disabled", text=_("ai_search_optimizing"))
            except Exception:
                pass
        self._set_status(_("ai_search_optimizing"))
        self._run_in_thread(self._do_ai_search, query, token)

    def _do_ai_search(self, query: str, token: str):
        from modrinth import ai_merged_search

        try:
            result = ai_merged_search(
                query=query,
                token=token,
                search_type="modpacks",
                max_per_keyword=30,
            )

            all_hits = result.get("hits", [])
            keywords = result.get("keywords", [])

            self._ai_cached_hits = all_hits
            self._total_hits = len(all_hits)
            self._current_offset = 0

            page = all_hits[:self.PAGE_SIZE]
            kw_text = ", ".join(keywords) if keywords else query
            self.after(0, self._render_results, page)
            self.after(0, self._set_status, _("ai_search_done", keywords=kw_text, total=len(all_hits)))
            self.after(0, self._restore_ai_button)

        except Exception as e:
            logger.error(f"AI 搜索整合包失败: {e}")
            self.after(0, self._render_error, str(e))
            self.after(0, self._restore_ai_button)

    def _restore_ai_button(self):
        if self._ai_search_btn:
            try:
                self._ai_search_btn.configure(state="normal", text=_("ai_search_btn"))
            except Exception:
                pass

    def _render_results(self, hits: List[Dict]):
        for w in self._list_frame.winfo_children():
            w.destroy()

        if not hits:
            ctk.CTkLabel(
                self._list_frame,
                text="未找到整合包\n请尝试其他关键词",
                font=ctk.CTkFont(family=FONT_FAMILY, size=14),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=40)
            self._update_pagination()
            self._set_status("未找到结果")
            return

        for modpack in hits:
            self._create_modpack_item(modpack)

        self._update_pagination()

        start = self._current_offset + 1
        end = min(self._current_offset + self.PAGE_SIZE, self._total_hits)
        self._result_count_label.configure(text=f"显示 {start}-{end} / 共 {self._total_hits} 个")
        self._set_status(f"共找到 {self._total_hits} 个整合包")

    def _render_error(self, error_msg: str):
        for w in self._list_frame.winfo_children():
            w.destroy()

        ctk.CTkLabel(
            self._list_frame,
            text=f"搜索失败\n{error_msg}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["error"],
            justify=ctk.CENTER,
        ).pack(pady=40)
        self._set_status(f"搜索失败: {error_msg}")

    def _create_modpack_item(self, modpack: Dict):
        row = ctk.CTkFrame(
            self._list_frame,
            fg_color=COLORS["bg_medium"],
            corner_radius=8,
        )
        row.pack(fill=ctk.X, pady=3, padx=2)

        top_row = ctk.CTkFrame(row, fg_color="transparent", height=36)
        top_row.pack(fill=ctk.X, padx=10, pady=(8, 2))
        top_row.pack_propagate(False)

        title = modpack.get("title", "未知整合包")
        ctk.CTkLabel(
            top_row,
            text=f"📦 {title}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        ).pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        downloads = modpack.get("downloads", 0)
        dl_text = self._format_downloads(downloads)
        ctk.CTkLabel(
            top_row,
            text=f"📥 {dl_text}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT, padx=(5, 0))

        project_id = modpack.get("project_id", "")
        ctk.CTkButton(
            top_row,
            text="📥 安装",
            width=70,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            text_color=COLORS["text_primary"],
            command=lambda pid=project_id, t=title: self._on_install_modpack(pid, t),
        ).pack(side=ctk.RIGHT, padx=(8, 0))

        description = modpack.get("description", "")
        if description:
            desc_label = ctk.CTkLabel(
                row,
                text=description,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                wraplength=700,
                justify=ctk.LEFT,
                anchor=ctk.W,
            )
            desc_label.pack(fill=ctk.X, padx=10, pady=(0, 4))

        versions_display = modpack.get("versions", [])
        if versions_display:
            from modrinth import compress_game_versions
            compressed = compress_game_versions(versions_display)
            if compressed:
                ctk.CTkLabel(
                    row,
                    text=compressed,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                    text_color=COLORS["text_secondary"],
                    anchor=ctk.W,
                ).pack(fill=ctk.X, padx=10, pady=(0, 8))

    @staticmethod
    def _format_downloads(count: int) -> str:
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _update_pagination(self):
        total_pages = max(1, (self._total_hits + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        current_page = (self._current_offset // self.PAGE_SIZE) + 1

        self._page_label.configure(text=f"{current_page} / {total_pages}")
        self._prev_btn.configure(state=ctk.NORMAL if self._current_offset > 0 else ctk.DISABLED)
        self._next_btn.configure(
            state=ctk.NORMAL if self._current_offset + self.PAGE_SIZE < self._total_hits else ctk.DISABLED
        )

    def _on_prev_page(self):
        if self._current_offset > 0:
            self._current_offset -= self.PAGE_SIZE
            if self._ai_cached_hits is not None:
                self._render_page_from_ai_cache()
            else:
                self._set_status("正在加载...")
                self._run_in_thread(self._do_search)

    def _on_next_page(self):
        if self._current_offset + self.PAGE_SIZE < self._total_hits:
            self._current_offset += self.PAGE_SIZE
            if self._ai_cached_hits is not None:
                self._render_page_from_ai_cache()
            else:
                self._set_status("正在加载...")
                self._run_in_thread(self._do_search)

    def _render_page_from_ai_cache(self):
        if self._ai_cached_hits is None:
            return
        offset = self._current_offset
        page = self._ai_cached_hits[offset:offset + self.PAGE_SIZE]
        self._render_results(page)
        self._update_pagination()

    VERSION_PAGE_SIZE = 50

    def _on_install_modpack(self, project_id: str, title: str):
        self._set_status(f"正在获取 {title} 版本列表...")
        self._run_in_thread(self._fetch_versions_and_pick, project_id, title)

    def _fetch_versions_and_pick(self, project_id: str, title: str):
        from modrinth import get_modpack_versions

        versions = get_modpack_versions(project_id)

        if not versions:
            self.after(0, self._set_status, f"❌ {title} 没有可用的版本")
            return

        self.after(0, self._set_status, f"找到 {len(versions)} 个版本，请选择...")

        all_sorted = self._build_sorted_version_list(versions)

        self.after(0, self._show_version_picker, project_id, title, all_sorted, len(versions))

    def _build_sorted_version_list(self, versions: List[Dict]) -> List[Dict]:
        grouped: Dict[str, List[Dict]] = {}
        for v in versions:
            game_versions = v.get("game_versions", [])
            label = game_versions[0] if game_versions else "未知版本"
            grouped.setdefault(label, []).append(v)

        def _mc_sort_key(mc_label: str) -> tuple:
            parts = mc_label.split(".")
            try:
                return tuple(int(p) for p in parts)
            except (ValueError, IndexError):
                return (0,)

        group_order = sorted(grouped.keys(), key=_mc_sort_key, reverse=True)

        for mc_label in group_order:
            grouped[mc_label].sort(
                key=lambda v: v.get("date_published", ""),
                reverse=True,
            )

        result: List[Dict] = []
        for mc_label in group_order:
            result.append({"_header": mc_label, "_count": len(grouped[mc_label])})
            result.extend(grouped[mc_label])

        return result

    def _show_version_picker(
        self,
        project_id: str,
        title: str,
        all_sorted: List[Dict],
        total_count: int,
    ):
        picker = ctk.CTkToplevel(self)
        picker.title(f"选择版本 - {title}")
        picker.geometry("620x540")
        picker.minsize(500, 400)
        picker.configure(fg_color=COLORS["bg_dark"])
        picker.transient(self)
        picker.grab_set()

        picker.update_idletasks()
        pw = self.winfo_width()
        ph = self.winfo_height()
        px = self.winfo_x()
        py = self.winfo_y()
        w, h = 620, 540
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        picker.geometry(f"{w}x{h}+{x}+{y}")

        picker_state = {
            "all_sorted": all_sorted,
            "rendered_count": 0,
            "total_count": total_count,
        }

        main_frame = ctk.CTkFrame(picker, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        ctk.CTkLabel(
            main_frame,
            text=f"📦 {title}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, pady=(0, 2))

        info_label = ctk.CTkLabel(
            main_frame,
            text=f"共 {total_count} 个版本，请选择要安装的版本",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        info_label.pack(anchor=ctk.W, pady=(0, 10))

        list_container = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        list_container.pack(fill=ctk.BOTH, expand=True, pady=(0, 10))

        scroll_frame = ctk.CTkScrollableFrame(
            list_container,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        scroll_frame.pack(fill=ctk.BOTH, expand=True, padx=5, pady=5)

        picker_state["scroll_frame"] = scroll_frame
        picker_state["info_label"] = info_label

        self._render_version_batch(picker, project_id, title, picker_state)

        bottom_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        bottom_frame.pack(fill=ctk.X)

        load_more_btn = ctk.CTkButton(
            bottom_frame,
            text=f"加载更多 (已显示 {picker_state['rendered_count']}/{total_count})",
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=lambda: self._render_version_batch(picker, project_id, title, picker_state),
        )
        load_more_btn.pack(side=ctk.LEFT, pady=(5, 0))

        picker_state["load_more_btn"] = load_more_btn

        ctk.CTkButton(
            bottom_frame,
            text="取消",
            height=30,
            width=70,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=picker.destroy,
        ).pack(side=ctk.RIGHT, pady=(5, 0))

    def _render_version_batch(self, picker, project_id: str, title: str, state: dict):
        all_sorted = state["all_sorted"]
        scroll_frame = state["scroll_frame"]
        start = state["rendered_count"]
        end = min(start + self.VERSION_PAGE_SIZE, len(all_sorted))

        if start >= len(all_sorted):
            return

        for i in range(start, end):
            item = all_sorted[i]

            header = item.get("_header")
            if header is not None:
                count = item.get("_count", 0)
                hf = ctk.CTkFrame(scroll_frame, fg_color="transparent", height=28)
                hf.pack(fill=ctk.X, pady=(8, 2), padx=5)
                hf.pack_propagate(False)

                ctk.CTkLabel(
                    hf,
                    text=f"▸ Minecraft {header}",
                    font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
                    text_color=COLORS["success"],
                    anchor=ctk.W,
                ).pack(side=ctk.LEFT)

                ctk.CTkLabel(
                    hf,
                    text=f"{count} 个版本",
                    font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                    text_color=COLORS["text_secondary"],
                ).pack(side=ctk.RIGHT)
                continue

            version_number = item.get("version_number", "未知")
            date_published = item.get("date_published", "")[:10]
            game_versions_str = ", ".join(item.get("game_versions", []))

            version_row = ctk.CTkFrame(
                scroll_frame,
                fg_color=COLORS["bg_medium"],
                corner_radius=6,
            )
            version_row.pack(fill=ctk.X, pady=2, padx=5)

            info_col = ctk.CTkFrame(version_row, fg_color="transparent")
            info_col.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=10, pady=6)

            ctk.CTkLabel(
                info_col,
                text=version_number,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X)

            meta_text = f"📅 {date_published}"
            if game_versions_str:
                meta_text += f"  ·  🎮 {game_versions_str}"
            ctk.CTkLabel(
                info_col,
                text=meta_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X)

            ctk.CTkButton(
                version_row,
                text="📥 下载",
                width=65,
                height=26,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color=COLORS["success"],
                hover_color="#27ae60",
                text_color=COLORS["text_primary"],
                command=lambda vd=item, pk=picker: self._on_version_selected(project_id, title, vd, pk),
            ).pack(side=ctk.RIGHT, padx=(0, 10), pady=6)

        state["rendered_count"] = end

        remaining = len(all_sorted) - end
        if remaining <= 0:
            state["load_more_btn"].configure(
                text=f"✅ 已显示全部 {len(all_sorted)} 个版本",
                state=ctk.DISABLED,
            )
        else:
            total = len(all_sorted)
            state["load_more_btn"].configure(
                text=f"加载更多 (已显示 {end}/{total})",
            )
        state["info_label"].configure(
            text=f"共 {state['total_count']} 个版本，请选择要安装的版本（已显示 {end} 个）"
        )

    def _on_version_selected(self, project_id: str, title: str, version_data: Dict, picker):
        picker.destroy()
        self._set_status(f"正在下载 {title} ({version_data.get('version_number', '')})...")
        self._run_in_thread(self._download_version, project_id, title, version_data)

    def _download_version(self, project_id: str, title: str, version_data: Dict):
        from modrinth import download_modpack_file

        version_number = version_data.get("version_number", "")

        def _status(msg):
            self.after(0, self._set_status, msg)

        _status(f"正在下载 {title} {version_number}...")

        success, result = download_modpack_file(
            project_id,
            version_data=version_data,
            status_callback=_status,
        )

        if not success:
            self.after(0, self._set_status, f"❌ 下载失败: {result}")
            logger.error(f"整合包下载失败: {result}")
            return

        mrpack_path = result
        logger.info(f"整合包下载完成: {mrpack_path}")

        self.after(0, self._set_status, f"✅ {os.path.basename(mrpack_path)} 下载完成!")

        def _done():
            try:
                self._on_modpack_selected(mrpack_path)
            finally:
                if self.winfo_exists():
                    self.destroy()

        self.after(500, _done)

    def _set_status(self, text: str):
        try:
            if self.winfo_exists():
                self._status_label.configure(text=text)
        except Exception:
            pass

    def _run_in_thread(self, target, *args, **kwargs):
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
