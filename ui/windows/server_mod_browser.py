"""服务器 Modrinth Mod 浏览窗口 - 浏览并安装服务端模组

业务逻辑已搬到 ``services/mod_browser_service.py``（阶段 1 任务 1.8-B）：
单源搜索（Modrinth ``search_server_mods``，后端分页口径）、AI 关键词搜索的内联
编排（扩展关键词 → 逐词搜索 → 去重 → 排序 → 单词失败只告警）、服务端模组目录
解析、模组安装调用。本文件只剩纯界面部分（控件构建、``after`` 调度、i18n 文案、
渲染、线程启动）；下面每个受影响的方法都退化成对服务的**薄委托**，
**方法名与签名保持不变**，界面可见行为不变。

本窗口**没有** D-93 那套请求世代守卫（阶段 1.22 只给 ``mod_browser`` 加了）——
记录现状、疑为缺陷：这里的 ``_do_search`` / ``_do_ai_search`` 仍然在 **worker 线程**
里直接写 ``self._total_hits`` / ``self._ai_cached_hits`` 再 ``after(0, ...)`` 渲染，
连打两次搜索时晚返回的旧请求仍会覆盖新结果。本轮只搬家，**不顺手改**行为。
"""

import threading
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk
from logzero import logger

from app.context import current_context
from services.browse_common import (
    MOD_LOADER_COMPAT_MAP,
    format_downloads,
    page_slice,
    total_pages as browse_total_pages,
)
from services.mod_browser_service import ModBrowserService, compat_loader
from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


def _get_mod_browser_service(owner: Any = None) -> ModBrowserService:
    """惰性取得服务实例（实现见 ``services/mod_browser_service.py``）。

    查找顺序："``owner.context.try_get`` → ``owner`` 上自造并缓存"。
    服务不需要 ``AppContext``，构造期也只保存参数，因此界面在
    ``__init__`` 之前（例如后台线程里）调用也安全。
    """
    ctx = getattr(owner, "context", None) or current_context()
    if ctx is not None:
        getter = getattr(ctx, "try_get", None)
        if callable(getter):
            try:
                service = getter(ModBrowserService.name)
            except Exception:
                service = None
            if service is not None:
                return service
    service = getattr(owner, "_mod_browser_service_fallback", None)
    if service is None:
        service = ModBrowserService()
        try:
            owner._mod_browser_service_fallback = service
        except Exception:
            # owner 可能是 __slots__ 对象或 None：不缓存，本次调用照常可用
            pass
    return service


class ServerModBrowserWindow(ctk.CTkToplevel):
    """服务器 Modrinth 模组浏览窗口 - 仅显示支持服务端的模组"""

    PAGE_SIZE = 10

    # 特殊模组加载器兼容映射：将无法被 API 识别的加载器映射为兼容等效类型
    # 与客户端模组窗口、服务层共用**同一份**映射表（避免两份数据漂移）
    MOD_LOADER_COMPAT_MAP: Dict[str, str] = MOD_LOADER_COMPAT_MAP

    @property
    def _search_loader(self) -> Optional[str]:
        """获取用于 API 搜索/安装的加载器类型

        对特殊加载器进行兼容映射，因为 CurseForge 和 Modrinth API
        不识别 legacyfabric、cleanroom 等加载器类型。
        """
        return compat_loader(self._mod_loader)

    def __init__(self, parent, version_id: str, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self.version_id = version_id
        self.callbacks = callbacks

        from modrinth import parse_game_version_from_version, parse_mod_loader_from_version

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
        ctk.CTkLabel(header, text=info_text, font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=info_color).pack(
            side=ctk.RIGHT
        )

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
            list_container, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
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
            page_frame, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=COLORS["text_secondary"]
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

        # 修正（阶段 1.19）：原键名 "get_ai_token" 核心层从未提供过，
        # 导致 AI 关键词搜索永远走到"需要登录"分支、功能整体失效。
        # 正确键名是 "get_jdz_token"（见 launcher/core.py: get_callbacks）。
        token = self.callbacks.get("get_jdz_token", lambda: "")()
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
        try:
            # 关键词扩展 → 逐词搜索 → 按 project_id 去重 → 按下载量排序 →
            # 单词失败只告警，这五件事整体搬到服务层（``modrinth.ai_merged_search``
            # 没有"只搜服务端模组"这条路，所以服务里重写了一遍同样的逻辑）。
            # ``keywords`` 为空 ↔ 原文的 ``after(0, 无结果状态); return``。
            merged, keywords = _get_mod_browser_service(self).search_server_ai(
                query, token, self._game_version, self._search_loader
            )
            if not keywords:
                self.after(0, lambda: self._set_status(_("mod_browser_no_results")))
                return

            self._ai_cached_hits = merged
            self._total_hits = len(merged)
            self._current_offset = 0
            page = page_slice(merged, 0, self.PAGE_SIZE)
            self.after(0, self._render_results, page)
            self.after(0, self._update_pagination)
            self.after(
                0, lambda: self._set_status(_("ai_search_done", keywords=", ".join(keywords), total=len(merged)))
            )
        except Exception as e:
            logger.error(f"AI搜索失败: {e}")
            self.after(0, lambda err=str(e): self._set_status(_("mod_browser_search_failed_status", error=err)))

    def _do_search(self):
        try:
            # 单源（Modrinth）、**后端分页**口径（offset=当前偏移、limit=一页）、
            # 结果归一化都在服务层；渲染与状态栏留在本方法里（下文一字未改）。
            outcome = _get_mod_browser_service(self).search_server_page(
                self._current_query,
                self._game_version,
                self._search_loader,
                self._current_offset,
                self.PAGE_SIZE,
            )
            hits = outcome.hits
            self._total_hits = outcome.total_hits
            self.after(0, self._render_results, hits)
            self.after(0, self._update_pagination)
            if self._current_query:
                self.after(
                    0,
                    lambda: self._set_status(
                        _(
                            "mod_browser_result_range",
                            start=self._current_offset + 1,
                            end=self._current_offset + len(hits),
                            total=self._total_hits,
                        )
                    ),
                )
            else:
                self.after(0, lambda: self._set_status(_("mod_browser_total_found", total=self._total_hits)))
        except Exception as e:
            logger.error(f"搜索服务端模组失败: {e}")
            self.after(0, lambda err=str(e): self._set_status(_("mod_browser_search_failed_status", error=err)))

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

            card = ctk.CTkFrame(self._list_frame, fg_color=COLORS["bg_medium"], corner_radius=8)
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
        # 这里用**后端报的总数**算页数是正确的：本窗口每次请求都带
        # offset/limit 从后端取一页（不是 mod_browser 那种"本地缓存 300 条"的口径），
        # 因此 D-103 的"本地条目数"口径**不适用于本窗口**。
        total_pages = browse_total_pages(self._total_hits, self.PAGE_SIZE)
        current_page = self._current_offset // self.PAGE_SIZE + 1
        self._page_label.configure(text=f"{current_page} / {total_pages}")
        has_prev = self._current_offset > 0
        has_next = self._current_offset + self.PAGE_SIZE < self._total_hits
        self._prev_btn.configure(state=ctk.NORMAL if has_prev else ctk.DISABLED)
        self._next_btn.configure(state=ctk.NORMAL if has_next else ctk.DISABLED)
        if self._total_hits > 0:
            self._result_count_label.configure(text=_("mod_browser_total_found", total=self._total_hits))

    def _on_prev_page(self):
        if self._current_offset >= self.PAGE_SIZE:
            self._current_offset -= self.PAGE_SIZE
            if self._ai_cached_hits is not None:
                self._render_results(page_slice(self._ai_cached_hits, self._current_offset, self.PAGE_SIZE))
                self._update_pagination()
            else:
                self._set_status(_("mod_browser_searching"))
                self._run_in_thread(self._do_search)

    def _on_next_page(self):
        if self._current_offset + self.PAGE_SIZE < self._total_hits:
            self._current_offset += self.PAGE_SIZE
            if self._ai_cached_hits is not None:
                self._render_results(page_slice(self._ai_cached_hits, self._current_offset, self.PAGE_SIZE))
                self._update_pagination()
            else:
                self._set_status(_("mod_browser_searching"))
                self._run_in_thread(self._do_search)

    def _on_install_mod(self, project_id: str, title: str):
        self._set_status(_("mod_browser_fetching_version", title=title))
        self._run_in_thread(lambda: self._install_mod(project_id, title))

    def _install_mod(self, project_id: str, title: str):
        try:
            if not self._game_version or not self._search_loader:
                self.after(0, lambda: self._set_status(_("mod_browser_unknown_loader")))
                return

            mods_dir = self._get_mods_dir()

            # 服务端窗口只有一条安装路径（没有 source 参数、没有 curseforge 分支）。
            success, result, installed_names = _get_mod_browser_service(self).server_install_mod(
                project_id,
                self._game_version,
                self._search_loader,
                mods_dir,
                status_callback=lambda msg: self.after(0, lambda: self._set_status(msg)),
            )

            if success:
                if len(installed_names) > 1:
                    deps = ", ".join(installed_names[:-1])
                    self.after(
                        0, lambda: self._set_status(_("mod_browser_install_success_deps", title=title, deps=deps))
                    )
                else:
                    self.after(0, lambda: self._set_status(_("mod_browser_install_success", title=title)))
                logger.info(f"服务端模组安装成功: {installed_names} -> {result}")
            else:
                self.after(0, lambda: self._set_status(_("mod_browser_install_failed", error=result)))
                logger.error(f"服务端模组安装失败: {result}")

        except Exception as e:
            error_msg = str(e)
            self.after(0, lambda: self._set_status(_("mod_browser_install_error", error=error_msg)))
            logger.error(f"安装服务端模组失败: {e}")

    def _get_mods_dir(self) -> str:
        """服务端模组目录（薄委托：services/mod_browser_service.py，**仍会建目录**）"""
        return _get_mod_browser_service(self).server_mods_dir(self.callbacks, self.version_id)

    @staticmethod
    def _format_downloads(count: int) -> str:
        """下载量格式化（实现逐字搬到 :func:`services.browse_common.format_downloads`）"""
        return format_downloads(count)

    def _set_status(self, text: str):
        try:
            if self.winfo_exists() and self._status_label:
                self._status_label.configure(text=text)
        except Exception:
            pass

    def _run_in_thread(self, target, *args, **kwargs):
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
