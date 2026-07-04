"""插件市场浏览窗口 - 在线搜索和安装第三方插件"""

import threading
from typing import Dict, List, Optional, Callable

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _
from ui.windows.plugin_permission_dialog import PluginPermissionDialog

RISK_COLORS_MAP = {
    "high": "#e74c3c",
    "medium": "#f39c12",
    "low": "#2ecc71",
}


class PluginBrowserWindow(ctk.CTkToplevel):
    """插件市场浏览器"""

    PAGE_SIZE = 10

    def __init__(self, parent, plugin_manager, market):
        """
        Args:
            parent: 父窗口
            plugin_manager: PluginManager 实例
            market: PluginMarket 实例
        """
        super().__init__(parent)
        self._pm = plugin_manager
        self._market = market
        self._plugins: List[dict] = []
        self._filtered: List[dict] = []
        self._current_page = 0
        self._searching = False
        self._installing_ids: set = set()
        self._updating_ids: set = set()
        self._update_info: Dict[str, dict] = {}  # 更新信息缓存

        self.title(_("plugin_market_title"))
        self.geometry("780x660")
        self.minsize(640, 520)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)

        try:
            self.grab_set()
        except Exception:
            pass

        self._build_ui()
        self._center_on_parent(parent)

        # 异步加载插件列表
        self.after(100, self._async_load_index)

        # 窗口获得焦点时自动刷新
        self.bind("<FocusIn>", lambda e: self._on_focus_in())

    def _on_focus_in(self):
        """窗口获得焦点时刷新"""
        if not self._searching:
            self._async_load_index()

    def _center_on_parent(self, parent):
        try:
            self.update_idletasks()
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_x()
            py = parent.winfo_y()
            w = self.winfo_width()
            h = self.winfo_height()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 2
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _build_ui(self):
        """构建界面"""
        # ── 顶部: 标题 + 刷新 ──
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill=ctk.X, padx=16, pady=(14, 8))

        ctk.CTkLabel(
            top,
            text=_("plugin_market_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=17, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        refresh_btn = ctk.CTkButton(
            top,
            text=_("refresh"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            width=70,
            height=30,
            command=self._async_load_index,
        )
        refresh_btn.pack(side=ctk.RIGHT)

        # ── 搜索栏 ──
        search_frame = ctk.CTkFrame(self, fg_color="transparent")
        search_frame.pack(fill=ctk.X, padx=16, pady=(0, 8))

        self._search_entry = ctk.CTkEntry(
            search_frame,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=_("plugin_market_search_placeholder"),
        )
        self._search_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._search_entry.bind("<Return>", lambda e: self._on_search())

        ctk.CTkButton(
            search_frame,
            text=_("search"),
            width=70,
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_search,
        ).pack(side=ctk.LEFT)

        # ── 标签筛选栏 ──
        self._tag_frame = ctk.CTkFrame(self, fg_color="transparent", height=0)
        self._tag_buttons: Dict[str, ctk.CTkButton] = {}
        self._active_tag: Optional[str] = None

        # ── 状态栏 ──
        self._status_label = ctk.CTkLabel(
            self,
            text=_("plugin_market_loading"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._status_label.pack(anchor=ctk.W, padx=16, pady=(0, 2))

        # ── 滚动列表 ──
        self._list_frame = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
        )
        self._list_frame.pack(fill=ctk.BOTH, expand=True, padx=16, pady=(0, 4))

        # ── 底部分页 ──
        pager = ctk.CTkFrame(self, fg_color="transparent")
        pager.pack(fill=ctk.X, padx=16, pady=(2, 12))

        self._prev_btn = ctk.CTkButton(
            pager,
            text=_("plugin_market_prev"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            width=80,
            height=28,
            command=lambda: self._change_page(-1),
        )
        self._prev_btn.pack(side=ctk.LEFT)

        self._page_label = ctk.CTkLabel(
            pager,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._page_label.pack(side=ctk.LEFT, padx=12)

        self._next_btn = ctk.CTkButton(
            pager,
            text=_("plugin_market_next"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            width=80,
            height=28,
            command=lambda: self._change_page(1),
        )
        self._next_btn.pack(side=ctk.LEFT)

    # ── 数据加载 ──

    def _async_load_index(self, force: bool = False):
        """异步加载插件市场索引"""
        if self._searching:
            return
        self._searching = True
        self._set_status(_("plugin_market_loading"), "loading")

        def _load():
            plugins, error = self._market.fetch_index(force=force)
            self.after(0, lambda: self._on_index_loaded(plugins, error))

        threading.Thread(target=_load, daemon=True).start()

    def _on_index_loaded(self, plugins: List[dict], error: str):
        """索引加载完成（主线程）"""
        self._searching = False
        if error:
            self._set_status(error, "error")
            return
        self._plugins = plugins
        self._update_tag_bar()
        self._check_updates_async()
        self._apply_filter()

    def _check_updates_async(self):
        """异步检查插件更新"""
        def _check():
            versions = self._pm.get_installed_versions_map()
            self._update_info = self._market.check_updates(versions)
            self.after(0, lambda: (
                self._render_page(),
                self._update_status_summary(),
            ))

        threading.Thread(target=_check, daemon=True).start()

    def _update_status_summary(self):
        """在状态栏显示更新摘要"""
        update_count = sum(
            1 for info in self._update_info.values() if info["has_update"]
        )
        if update_count > 0:
            self._set_status(
                _("plugin_market_updates_available", count=update_count),
                "warning",
            )

    def _update_tag_bar(self):
        """更新标签筛选栏"""
        for w in self._tag_frame.winfo_children():
            w.destroy()
        self._tag_buttons.clear()

        tag_map = self._market.get_available_tags()
        if not tag_map:
            self._tag_frame.pack_forget()
            return

        # 显示标签栏
        self._tag_frame.pack(fill=ctk.X, padx=16, pady=(0, 8))
        self._tag_frame.configure(height=32)

        # 「全部」按钮
        all_btn = ctk.CTkButton(
            self._tag_frame,
            text=_("all"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color=COLORS["accent"] if self._active_tag is None else COLORS["bg_medium"],
            hover_color=COLORS["accent_hover"],
            width=50, height=24,
            command=lambda: self._on_tag_click(None),
        )
        all_btn.pack(side=ctk.LEFT, padx=1)
        self._tag_buttons["_all"] = all_btn

        for tag_key, tag_name in tag_map.items():
            display = tag_name[:4]
            btn = ctk.CTkButton(
                self._tag_frame,
                text=display,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                fg_color=COLORS["accent"] if self._active_tag == tag_key else COLORS["bg_medium"],
                hover_color=COLORS["accent_hover"],
                width=60, height=24,
                command=lambda t=tag_key: self._on_tag_click(t),
            )
            btn.pack(side=ctk.LEFT, padx=1)
            self._tag_buttons[tag_key] = btn

    def _on_tag_click(self, tag: Optional[str]):
        """点击标签筛选"""
        self._active_tag = tag
        self._update_tag_bar_colors()
        self._apply_filter()

    def _update_tag_bar_colors(self):
        for key, btn in self._tag_buttons.items():
            active = (
                (key == "_all" and self._active_tag is None)
                or (key == self._active_tag)
            )
            btn.configure(fg_color=COLORS["accent"] if active else COLORS["bg_medium"])

    def _on_search(self):
        """搜索"""
        self._apply_filter()

    def _apply_filter(self):
        """应用搜索 + 标签筛选"""
        query = (self._search_entry.get() or "").strip()
        tags = [self._active_tag] if self._active_tag else None
        self._filtered = self._market.search(query=query, tags=tags)
        self._current_page = 0
        self._render_page()

    # ── 渲染 ──

    def _render_page(self):
        """渲染当前页"""
        for w in self._list_frame.winfo_children():
            w.destroy()

        total = len(self._filtered)
        if total == 0:
            ctk.CTkLabel(
                self._list_frame,
                text=_("plugin_market_no_results"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                text_color=COLORS["text_secondary"],
            ).pack(pady=40)
            self._set_status(_("plugin_market_no_results"))
            self._prev_btn.configure(state="disabled")
            self._next_btn.configure(state="disabled")
            self._page_label.configure(text="")
            return

        start = self._current_page * self.PAGE_SIZE
        end = min(start + self.PAGE_SIZE, total)
        page_items = self._filtered[start:end]

        for plugin in page_items:
            self._create_plugin_card(plugin)

        # 分页
        total_pages = (total + self.PAGE_SIZE - 1) // self.PAGE_SIZE
        self._page_label.configure(text=f"{self._current_page + 1} / {total_pages}")
        self._prev_btn.configure(
            state="normal" if self._current_page > 0 else "disabled"
        )
        self._next_btn.configure(
            state="normal" if end < total else "disabled"
        )
        self._set_status(_("plugin_market_total", total=total))

    def _create_plugin_card(self, plugin: dict):
        """创建单个插件卡片"""
        pid = plugin.get("id", "")
        name = plugin.get("name", pid)
        version = plugin.get("version", "?")
        author = plugin.get("author", _("unknown"))
        tags = plugin.get("tags", [])
        permissions = plugin.get("permissions", [])
        desc = plugin.get("description", {})
        desc_text = desc.get("zh_CN", "") or desc.get("en_US", "") or ""
        license_val = plugin.get("license", "")
        homepage = plugin.get("homepage", "")

        # 已安装检查
        installed = pid in (self._pm.get_all_plugin_meta() or {})
        installed_state = ""
        if installed:
            state = self._pm.get_plugin_state(pid)
            if state:
                installed_state = state.value

        # 更新检查
        update_info = self._update_info.get(pid, {})
        has_update = update_info.get("has_update", False)
        installed_ver = update_info.get("installed", "")
        latest_ver = update_info.get("latest", version)

        card = ctk.CTkFrame(self._list_frame, fg_color=COLORS["card_bg"], corner_radius=8)
        card.pack(fill=ctk.X, pady=3)

        # 顶行: 名称 + 已安装状态 / 更新标记
        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.pack(fill=ctk.X, padx=10, pady=(8, 2))

        ctk.CTkLabel(
            top_row,
            text=name,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        # 更新提示
        if has_update:
            update_tag = ctk.CTkFrame(
                top_row, fg_color=COLORS["warning"], corner_radius=4,
            )
            update_tag.pack(side=ctk.RIGHT, padx=2)
            ctk.CTkLabel(
                update_tag,
                text=_("plugin_update_available_tag"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color="#ffffff",
            ).pack(padx=6, pady=2)

        # 已安装标记
        if installed:
            state_labels = {
                "enabled": _("installed"),
                "disabled": _("disabled"),
            }
            installed_label = state_labels.get(installed_state, _("installed"))
            tag_frame = ctk.CTkFrame(top_row, fg_color=COLORS["success"], corner_radius=4)
            tag_frame.pack(side=ctk.RIGHT, padx=2)
            ctk.CTkLabel(
                tag_frame,
                text=installed_label,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color="#ffffff",
            ).pack(padx=6, pady=2)

        # 版本 + 作者
        meta_row = ctk.CTkFrame(card, fg_color="transparent")
        meta_row.pack(fill=ctk.X, padx=10, pady=(0, 2))

        if has_update:
            ver_text = f"v{installed_ver} → v{latest_ver}"
            ver_color = COLORS["warning"]
        else:
            ver_text = f"v{version}"
            ver_color = COLORS["accent"]

        ctk.CTkLabel(
            meta_row,
            text=ver_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=ver_color,
        ).pack(side=ctk.LEFT)

        ctk.CTkLabel(
            meta_row,
            text=author,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.RIGHT)

        # 描述
        if desc_text:
            ctk.CTkLabel(
                card,
                text=desc_text[:100] + ("..." if len(desc_text) > 100 else ""),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                wraplength=700,
                justify="left",
            ).pack(anchor=ctk.W, padx=10, pady=(0, 2))

        # 标签 + 权限数量
        info_row = ctk.CTkFrame(card, fg_color="transparent")
        info_row.pack(fill=ctk.X, padx=10, pady=(0, 4))

        for tag in tags[:3]:
            tag_btn = ctk.CTkButton(
                info_row,
                text=tag,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                fg_color=COLORS["bg_light"],
                hover_color=COLORS["card_border"],
                width=50, height=20,
                command=lambda t=tag: self._on_tag_single_click(t),
            )
            tag_btn.pack(side=ctk.LEFT, padx=1)

        if permissions:
            high_count = sum(
                1 for p in permissions
                if p in ("network.socket", "core.launch_hook", "core.process")
            )
            medium_count = sum(
                1 for p in permissions
                if p in ("filesystem.write", "core.download", "core.version", "data.settings")
            )
            low_count = len(permissions) - high_count - medium_count
            perm_parts = []
            if high_count:
                perm_parts.append(f"H:{high_count}")
            if medium_count:
                perm_parts.append(f"M:{medium_count}")
            if low_count:
                perm_parts.append(f"L:{low_count}")
            ctk.CTkLabel(
                info_row,
                text=f"| {' '.join(perm_parts)}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
            ).pack(side=ctk.LEFT, padx=4)

        # 许可证
        if license_val:
            ctk.CTkLabel(
                info_row,
                text=license_val,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
            ).pack(side=ctk.RIGHT)

        # 底部按钮
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=10, pady=(2, 8))

        is_installing = pid in self._installing_ids
        is_updating = pid in self._updating_ids

        if installed and has_update:
            # 更新按钮
            update_btn = ctk.CTkButton(
                btn_row,
                text=_("plugin_updating") if is_updating else _("plugin_update"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color=COLORS["warning"],
                hover_color="#e67e22",
                width=80, height=26,
                state="disabled" if is_updating else "normal",
                command=lambda p=plugin: self._update_plugin(p),
            )
            update_btn.pack(side=ctk.LEFT)

        elif installed:
            if installed_state == "enabled":
                ctk.CTkButton(
                    btn_row,
                    text=_("installed"),
                    font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                    fg_color=COLORS["success"],
                    hover_color="#27ae60",
                    width=80, height=26,
                    state="disabled",
                ).pack(side=ctk.LEFT)
            else:
                ctk.CTkButton(
                    btn_row,
                    text=_("enable"),
                    font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                    fg_color=COLORS["success"],
                    hover_color="#27ae60",
                    width=80, height=26,
                    command=lambda p=pid: self._enable_plugin(p),
                ).pack(side=ctk.LEFT)
        else:
            install_btn = ctk.CTkButton(
                btn_row,
                text=_("plugin_market_installing") if is_installing else _("install"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
                width=80, height=26,
                state="disabled" if is_installing else "normal",
                command=lambda p=plugin: self._install_plugin(p),
            )
            install_btn.pack(side=ctk.LEFT)

    # ── 安装 ──

    def _install_plugin(self, plugin_info: dict):
        """下载并安装插件"""
        pid = plugin_info["id"]
        name = plugin_info.get("name", pid)

        # 权限检查
        permissions = plugin_info.get("permissions", [])
        if permissions:
            dialog = PluginPermissionDialog(
                self, plugin_name=name, permissions=permissions,
            )
            if not dialog.get_result():
                self._set_status(_("plugin_permission_denied"))
                return

        self._installing_ids.add(pid)
        self._render_page()  # 更新按钮状态

        def _download_and_install():
            self.after(0, lambda: self._set_status(
                _("plugin_market_downloading", name=name), "loading",
            ))

            fmpl_path, error = self._market.download_plugin(
                pid,
                progress_callback=lambda stage, cur, tot: self.after(
                    0, lambda s=stage, c=cur, t=tot: self._set_status(
                        _("plugin_market_downloading_progress", name=name, cur=c, total=t),
                        "loading",
                    )
                ),
            )

            if error:
                self.after(0, lambda e=error: self._on_install_failed(pid, e))
                return

            # 安装
            ok, msg = self._pm.install_from_file(fmpl_path, pid)

            if ok:
                # 授权权限
                if permissions:
                    self._pm.grant_all_permissions(pid)
                # 启用
                self._pm.load_plugin(pid)
                self._pm.enable_plugin(pid)
                self.after(0, lambda n=name: self._on_install_success(pid, n))
            else:
                self.after(0, lambda m=msg: self._on_install_failed(pid, m))

        threading.Thread(target=_download_and_install, daemon=True).start()

    def _on_install_success(self, pid: str, name: str):
        """安装成功回调"""
        self._installing_ids.discard(pid)
        self._set_status(_("plugin_install_success", name=name))
        self._render_page()

    def _on_install_failed(self, pid: str, error: str):
        """安装失败回调"""
        self._installing_ids.discard(pid)
        self._set_status(f"{_('error')}: {error}")
        self._render_page()

    # ── 更新 ──

    def _update_plugin(self, plugin_info: dict):
        """从市场更新已安装的插件"""
        pid = plugin_info["id"]
        name = plugin_info.get("name", pid)

        self._updating_ids.add(pid)
        self._render_page()

        self._set_status(_("plugin_updating_progress", name=name), "loading")

        def _do_update():
            ok, msg = self._pm.update_plugin_from_market(
                pid,
                progress_callback=lambda stage, cur, tot: self.after(
                    0, lambda s=stage, c=cur, t=tot: self._set_status(
                        _("plugin_market_downloading_progress", name=name, cur=c, total=t),
                        "loading",
                    )
                ),
            )
            if ok:
                self.after(0, lambda n=name: self._on_update_success(pid, n))
            else:
                self.after(0, lambda m=msg: self._on_update_failed(pid, m))

        threading.Thread(target=_do_update, daemon=True).start()

    def _on_update_success(self, pid: str, name: str):
        """更新成功回调"""
        self._updating_ids.discard(pid)
        self._update_info.pop(pid, None)
        self._set_status(_("plugin_update_success", name=name))
        self._render_page()

    def _on_update_failed(self, pid: str, error: str):
        """更新失败回调"""
        self._updating_ids.discard(pid)
        self._set_status(f"{_('error')}: {error}")
        self._render_page()

    def _enable_plugin(self, pid: str):
        """启用已安装但禁用状态的插件"""
        permissions = []
        meta = self._pm.get_all_plugin_meta().get(pid, {})
        manifest_data = meta.get("manifest", {})
        if manifest_data:
            permissions = manifest_data.get("permissions", [])
        if permissions:
            perm_state = self._pm.get_permission_state(pid)
            if perm_state and perm_state.get_ungranted_permissions():
                dialog = PluginPermissionDialog(
                    self,
                    plugin_name=manifest_data.get("name", pid),
                    permissions=permissions,
                )
                if dialog.get_result():
                    self._pm.grant_all_permissions(pid)
                else:
                    return
        self._pm.load_plugin(pid)
        ok, msg = self._pm.enable_plugin(pid)
        if ok:
            self._set_status(_("plugin_state_enabled") + f": {pid}")
        else:
            self._set_status(msg)
        self._render_page()

    # ── 工具方法 ──

    def _on_tag_single_click(self, tag: str):
        """点击单个标签 → 设为筛选条件"""
        self._active_tag = tag
        self._update_tag_bar_colors()
        self._search_entry.delete(0, "end")
        self._apply_filter()

    def _change_page(self, delta: int):
        new_page = self._current_page + delta
        if 0 <= new_page < (len(self._filtered) + self.PAGE_SIZE - 1) // self.PAGE_SIZE:
            self._current_page = new_page
            self._render_page()

    def _set_status(self, text: str, level: str = "info"):
        if hasattr(self, '_status_label') and self._status_label.winfo_exists():
            color = COLORS.get("text_secondary", "#a0a0b0")
            if level == "error":
                color = COLORS.get("error", "#e74c3c")
            elif level == "success":
                color = COLORS.get("success", "#2ecc71")
            self._status_label.configure(text=text, text_color=color)
