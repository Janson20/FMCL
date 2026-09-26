"""Modrinth 整合包浏览窗口 - 搜索、浏览并下载整合包

业务逻辑已搬到 ``services/modpack_service.py``（阶段 1 任务 1.8-B）：单源搜索与
结果归一化、AI 合并搜索、版本列表的分组/排序（``_build_sorted_version_list``）、
版本下载调用。本文件只剩纯界面部分（控件构建、版本选择器子窗口、``after`` 调度、
i18n 文案、渲染、线程启动）；下面每个受影响的方法都退化成对服务的**薄委托**，
**方法名与签名保持不变**，界面可见行为不变。

既有的两处现状缺陷照旧保留（本轮只搬家）：

- ``_fetch_versions_and_pick`` 里"获取版本列表失败: {e}"是**硬编码中文**，没走 i18n；
- 本窗口**没有**请求世代守卫（D-93 只加在 ``mod_browser`` 上）：worker 线程直接写
  ``self._total_hits`` / ``self._ai_cached_hits`` 再 ``after(0, ...)`` 渲染。
"""

import os
import threading
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk
from logzero import logger

from app.context import current_context
from services.browse_common import (
    MODPACK_VERSION_PAGE_SIZE,
    format_downloads,
    page_slice,
    total_pages as browse_total_pages,
)
from services.modpack_service import ModpackService
from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


def _get_modpack_service(owner: Any = None) -> ModpackService:
    """惰性取得服务实例（实现见 ``services/modpack_service.py``）。

    查找顺序："``owner.context.try_get`` → ``owner`` 上自造并缓存"。
    服务不需要 ``AppContext``，构造期也只保存参数，因此界面在
    ``__init__`` 之前（例如后台线程里）调用也安全。
    """
    ctx = getattr(owner, "context", None) or current_context()
    if ctx is not None:
        getter = getattr(ctx, "try_get", None)
        if callable(getter):
            try:
                service = getter(ModpackService.name)
            except Exception:
                service = None
            if service is not None:
                return service
    service = getattr(owner, "_modpack_service_fallback", None)
    if service is None:
        service = ModpackService()
        try:
            owner._modpack_service_fallback = service
        except Exception:
            # owner 可能是 __slots__ 对象或 None：不缓存，本次调用照常可用
            pass
    return service


class ModpackBrowserWindow(ctk.CTkToplevel):
    """Modrinth 整合包浏览窗口 - 搜索、浏览并下载整合包 .mrpack"""

    PAGE_SIZE = 10

    def __init__(self, parent, on_modpack_selected: Callable[[str], None]):
        super().__init__(parent)
        self._on_modpack_selected = on_modpack_selected

        self.title(_("mp_browser_title"))
        self.geometry("800x640")
        self.minsize(720, 560)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        try:
            self.grab_set()
        except Exception:
            pass

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
            text=_("mp_browser_title"),
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
            placeholder_text=_("mp_browser_search_placeholder"),
        )
        self._search_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._search_entry.bind("<Return>", lambda e: self._on_search())

        ctk.CTkButton(
            search_frame,
            text=_("mod_browser_search"),
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
            list_container, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        self._list_frame.pack(fill=ctk.BOTH, expand=True, padx=5, pady=5)

        self._loading_label = ctk.CTkLabel(
            self._list_frame,
            text=_("mp_browser_loading"),
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
            page_frame, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=COLORS["text_secondary"]
        )
        self._result_count_label.pack(side=ctk.RIGHT)

        self._status_label = ctk.CTkLabel(
            main_frame,
            text=_("mp_browser_ready"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._status_label.pack(anchor=ctk.W, pady=(5, 0))

    def _on_search(self):
        self._current_query = self._search_entry.get().strip()
        self._current_offset = 0
        self._ai_cached_hits = None
        self._set_status(_("mp_browser_searching"))
        self._run_in_thread(self._do_search)

    def _do_search(self):
        try:
            # 单源搜索 + 结果归一化在服务层；渲染/错误态留在本方法里（下文一字未改）。
            outcome = _get_modpack_service(self).search_modpacks(
                self._current_query, self._current_offset, self.PAGE_SIZE
            )

            hits = outcome.hits
            self._total_hits = outcome.total_hits

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
            messagebox.showwarning(_("warning"), _("ai_search_login_required"), parent=self)
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
        try:
            # 关键词扩展 + 逐词搜索 + 去重 + 排序（search_type="modpacks"）在服务层
            all_hits, keywords = _get_modpack_service(self).search_ai_modpacks(query, token)

            self._ai_cached_hits = all_hits
            self._total_hits = len(all_hits)
            self._current_offset = 0

            page = page_slice(all_hits, 0, self.PAGE_SIZE)
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
                text=_("mp_browser_no_results"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=14),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=40)
            self._update_pagination()
            self._set_status(_("mp_browser_no_results_status"))
            return

        for modpack in hits:
            self._create_modpack_item(modpack)

        self._update_pagination()

        start = self._current_offset + 1
        end = min(self._current_offset + self.PAGE_SIZE, self._total_hits)
        self._result_count_label.configure(
            text=_("mp_browser_results_count", start=start, end=end, total=self._total_hits)
        )
        self._set_status(_("mp_browser_total_found", total=self._total_hits))

    def _render_error(self, error_msg: str):
        for w in self._list_frame.winfo_children():
            w.destroy()

        ctk.CTkLabel(
            self._list_frame,
            text=_("mp_browser_search_error", error=error_msg),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["error"],
            justify=ctk.CENTER,
        ).pack(pady=40)
        self._set_status(_("mp_browser_search_error_status", error=error_msg))

    def _create_modpack_item(self, modpack: Dict):
        row = ctk.CTkFrame(self._list_frame, fg_color=COLORS["bg_medium"], corner_radius=8)
        row.pack(fill=ctk.X, pady=3, padx=2)

        top_row = ctk.CTkFrame(row, fg_color="transparent", height=36)
        top_row.pack(fill=ctk.X, padx=10, pady=(8, 2))
        top_row.pack_propagate(False)

        title = modpack.get("title", _("mp_browser_unknown_modpack"))
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
            text=_("mp_browser_install_btn"),
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
        """下载量格式化（实现逐字搬到 :func:`services.browse_common.format_downloads`）"""
        return format_downloads(count)

    def _update_pagination(self):
        # 与 server_mod_browser 同理：本窗口按 offset/limit 向后端取一页，
        # ``_total_hits`` 是后端报的匹配总数，这里用它算页数是正确口径。
        total_pages = browse_total_pages(self._total_hits, self.PAGE_SIZE)
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
                self._set_status(_("mp_browser_loading_status"))
                self._run_in_thread(self._do_search)

    def _on_next_page(self):
        if self._current_offset + self.PAGE_SIZE < self._total_hits:
            self._current_offset += self.PAGE_SIZE
            if self._ai_cached_hits is not None:
                self._render_page_from_ai_cache()
            else:
                self._set_status(_("mp_browser_loading_status"))
                self._run_in_thread(self._do_search)

    def _render_page_from_ai_cache(self):
        if self._ai_cached_hits is None:
            return
        offset = self._current_offset
        page = page_slice(self._ai_cached_hits, offset, self.PAGE_SIZE)
        self._render_results(page)
        self._update_pagination()

    #: 版本选择器一次渲染多少条（数据源在服务层，界面沿用同名类属性）
    VERSION_PAGE_SIZE = MODPACK_VERSION_PAGE_SIZE

    def _on_install_modpack(self, project_id: str, title: str):
        self._set_status(_("mp_browser_fetching_versions", title=title))
        self._run_in_thread(self._fetch_versions_and_pick, project_id, title)

    def _fetch_versions_and_pick(self, project_id: str, title: str):
        try:
            versions = _get_modpack_service(self).fetch_modpack_versions(project_id)
        except Exception as e:
            logger.error(f"获取整合包版本列表失败: {e}")
            self.after(0, self._set_status, f"获取版本列表失败: {e}")
            return

        if not versions:
            self.after(0, self._set_status, _("mp_browser_no_versions", title=title))
            return

        self.after(0, self._set_status, _("mp_browser_versions_found", count=len(versions)))

        all_sorted = self._build_sorted_version_list(versions)

        self.after(0, self._show_version_picker, project_id, title, all_sorted, len(versions))

    def _build_sorted_version_list(self, versions: List[Dict]) -> List[Dict]:
        """按 MC 版本分组 + 组内倒序 + 插分组头（薄委托：services/modpack_service.py）

        "未知版本"的占位文案由本层用 i18n 拼好传进服务 —— 服务层不 import
        ``ui.i18n``，而 ``scripts/check_i18n.py`` 仍能在**界面文件**里看到
        ``mp_browser_unknown_version`` 这个字面量键。
        """
        return _get_modpack_service(self).build_sorted_version_list(versions, _("mp_browser_unknown_version"))

    def _show_version_picker(self, project_id: str, title: str, all_sorted: List[Dict], total_count: int):
        picker = ctk.CTkToplevel(self)
        picker.title(_("mp_browser_version_picker_title", title=title))
        picker.geometry("620x540")
        picker.minsize(500, 400)
        picker.configure(fg_color=COLORS["bg_dark"])
        picker.transient(self)
        try:
            picker.grab_set()
        except Exception:
            pass

        picker.update_idletasks()
        pw = self.winfo_width()
        ph = self.winfo_height()
        px = self.winfo_x()
        py = self.winfo_y()
        w, h = 620, 540
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        picker.geometry(f"{w}x{h}+{x}+{y}")

        picker_state = {"all_sorted": all_sorted, "rendered_count": 0, "total_count": total_count}

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
            text=_("mp_browser_version_picker_header", total=total_count),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        info_label.pack(anchor=ctk.W, pady=(0, 10))

        list_container = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        list_container.pack(fill=ctk.BOTH, expand=True, pady=(0, 10))

        scroll_frame = ctk.CTkScrollableFrame(
            list_container, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        scroll_frame.pack(fill=ctk.BOTH, expand=True, padx=5, pady=5)

        picker_state["scroll_frame"] = scroll_frame
        picker_state["info_label"] = info_label

        bottom_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        bottom_frame.pack(fill=ctk.X)

        load_more_btn = ctk.CTkButton(
            bottom_frame,
            text=_("mp_browser_load_more", shown=0, total=total_count),
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
            text=_("mp_browser_cancel"),
            height=30,
            width=70,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=picker.destroy,
        ).pack(side=ctk.RIGHT, pady=(5, 0))

        self._render_version_batch(picker, project_id, title, picker_state)

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
                    text=_("mp_browser_version_count", count=count),
                    font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                    text_color=COLORS["text_secondary"],
                ).pack(side=ctk.RIGHT)
                continue

            version_number = item.get("version_number", _("mp_browser_unknown_version"))
            date_published = item.get("date_published", "")[:10]
            game_versions_str = ", ".join(item.get("game_versions", []))

            version_row = ctk.CTkFrame(scroll_frame, fg_color=COLORS["bg_medium"], corner_radius=6)
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
                text=_("mp_browser_download_btn"),
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
            state["load_more_btn"].configure(text=_("mp_browser_all_loaded", total=len(all_sorted)), state=ctk.DISABLED)
        else:
            total = len(all_sorted)
            state["load_more_btn"].configure(text=_("mp_browser_load_more_2", shown=end, total=total))
        state["info_label"].configure(text=_("mp_browser_version_picker_msg", total=state["total_count"], shown=end))

    def _on_version_selected(self, project_id: str, title: str, version_data: Dict, picker):
        picker.destroy()
        self._set_status(_("mp_browser_downloading", title=title, version=version_data.get("version_number", "")))
        self._run_in_thread(self._download_version, project_id, title, version_data)

    def _download_version(self, project_id: str, title: str, version_data: Dict):
        version_number = version_data.get("version_number", "")

        def _status(msg):
            self.after(0, self._set_status, msg)

        _status(_("mp_browser_downloading", title=title, version=version_number))

        try:
            success, result = _get_modpack_service(self).download_modpack_version(
                project_id, version_data, status_callback=_status
            )
        except Exception as e:
            logger.error(f"整合包下载异常: {e}")
            self.after(0, self._set_status, _("mp_browser_download_error", error=str(e)))
            return

        if not success:
            self.after(0, self._set_status, _("mp_browser_download_error", error=result))
            logger.error(f"整合包下载失败: {result}")
            return

        mrpack_path = result
        logger.info(f"整合包下载完成: {mrpack_path}")

        self.after(0, self._set_status, _("mp_browser_download_done", filename=os.path.basename(mrpack_path)))

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
