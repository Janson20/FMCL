"""Modrinth 资源浏览窗口 - 浏览并安装模组、资源包、光影"""
import threading
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


class ModBrowserWindow(ctk.CTkToplevel):
    """Modrinth 资源浏览窗口 - 支持模组、资源包、光影三个标签页"""

    PAGE_SIZE = 10

    TAB_MODS = "mods"
    TAB_RESOURCE_PACKS = "resourcepacks"
    TAB_SHADERS = "shaders"

    def __init__(self, parent, version_id: str, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self.version_id = version_id
        self.callbacks = callbacks

        from modrinth import parse_mod_loader_from_version, parse_game_version_from_version
        self._mod_loader = parse_mod_loader_from_version(version_id)
        self._game_version = parse_game_version_from_version(version_id)

        self.title(_("mod_browser_title", version=version_id))
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

        self._tab_states: Dict[str, Dict] = {}
        for tab_key in (self.TAB_MODS, self.TAB_RESOURCE_PACKS, self.TAB_SHADERS):
            self._tab_states[tab_key] = {
                "current_offset": 0,
                "total_hits": 0,
                "current_query": "",
                "search_entry": None,
                "list_frame": None,
                "loading_label": None,
                "page_label": None,
                "prev_btn": None,
                "next_btn": None,
                "result_count_label": None,
                "status_label": None,
            }

        self._build_ui()

        self._switch_to_tab(self.TAB_MODS)
        self.after(300, lambda: self._do_tab_search(self.TAB_MODS))

    def _build_ui(self):
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        header = ctk.CTkFrame(main_frame, fg_color="transparent")
        header.pack(fill=ctk.X, pady=(0, 10))

        ctk.CTkLabel(
            header,
            text=_("mod_browser_header", version=self.version_id),
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        info_parts = []
        if self._game_version:
            info_parts.append(f"MC {self._game_version}")
        if self._mod_loader:
            info_parts.append(self._mod_loader.capitalize())
        info_text = " | ".join(info_parts) if info_parts else _("mod_browser_unknown_version")
        info_color = COLORS["success"] if info_parts else COLORS["warning"]
        ctk.CTkLabel(
            header,
            text=info_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=info_color,
        ).pack(side=ctk.RIGHT)

        self._tabview = ctk.CTkTabview(
            main_frame,
            fg_color="transparent",
            segmented_button_fg_color=COLORS["bg_medium"],
            segmented_button_selected_color=COLORS["accent"],
            segmented_button_unselected_color=COLORS["bg_medium"],
            segmented_button_selected_hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            text_color_disabled=COLORS["text_secondary"],
        )
        self._tabview.pack(fill=ctk.BOTH, expand=True, pady=(0, 5))

        self._tabview.add(_("mod_browser_tab_mods"))
        self._tabview.add(_("mod_browser_tab_resourcepacks"))
        self._tabview.add(_("mod_browser_tab_shaders"))

        self._build_tab_content(self._tabview.tab(_("mod_browser_tab_mods")), self.TAB_MODS)
        self._build_tab_content(self._tabview.tab(_("mod_browser_tab_resourcepacks")), self.TAB_RESOURCE_PACKS)
        self._build_tab_content(self._tabview.tab(_("mod_browser_tab_shaders")), self.TAB_SHADERS)

        self._tabview.configure(command=self._on_tab_changed)

    def _build_tab_content(self, tab_frame, tab_key: str):
        state = self._tab_states[tab_key]

        search_frame = ctk.CTkFrame(tab_frame, fg_color="transparent", height=40)
        search_frame.pack(fill=ctk.X, pady=(5, 8))
        search_frame.pack_propagate(False)

        placeholders = {
            self.TAB_MODS: _("mod_browser_search_mods_placeholder"),
            self.TAB_RESOURCE_PACKS: _("mod_browser_search_rp_placeholder"),
            self.TAB_SHADERS: _("mod_browser_search_shaders_placeholder"),
        }

        entry = ctk.CTkEntry(
            search_frame,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=placeholders.get(tab_key, "🔍 搜索..."),
        )
        entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        entry.bind("<Return>", lambda e, tk=tab_key: self._on_tab_search(tk))
        state["search_entry"] = entry

        search_btn = ctk.CTkButton(
            search_frame,
            text=_("mod_browser_search"),
            width=80,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda tk=tab_key: self._on_tab_search(tk),
        )
        search_btn.pack(side=ctk.LEFT)

        list_container = ctk.CTkFrame(tab_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        list_container.pack(fill=ctk.BOTH, expand=True, pady=(0, 8))

        list_frame = ctk.CTkScrollableFrame(
            list_container,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        list_frame.pack(fill=ctk.BOTH, expand=True, padx=5, pady=5)
        state["list_frame"] = list_frame

        loading_label = ctk.CTkLabel(
            list_frame,
            text=_("mod_browser_loading"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            justify=ctk.CENTER,
        )
        loading_label.pack(pady=40)
        state["loading_label"] = loading_label

        page_frame = ctk.CTkFrame(tab_frame, fg_color="transparent", height=34)
        page_frame.pack(fill=ctk.X)
        page_frame.pack_propagate(False)

        prev_btn = ctk.CTkButton(
            page_frame,
            text=_("mod_browser_prev_page"),
            width=90,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=lambda tk=tab_key: self._on_tab_prev_page(tk),
        )
        prev_btn.pack(side=ctk.LEFT)
        state["prev_btn"] = prev_btn

        page_label = ctk.CTkLabel(
            page_frame,
            text="0 / 0",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            width=100,
        )
        page_label.pack(side=ctk.LEFT, padx=10)
        state["page_label"] = page_label

        next_btn = ctk.CTkButton(
            page_frame,
            text=_("mod_browser_next_page"),
            width=90,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=lambda tk=tab_key: self._on_tab_next_page(tk),
        )
        next_btn.pack(side=ctk.LEFT)
        state["next_btn"] = next_btn

        result_count_label = ctk.CTkLabel(
            page_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        result_count_label.pack(side=ctk.RIGHT)
        state["result_count_label"] = result_count_label

        status_label = ctk.CTkLabel(
            tab_frame,
            text=_("mod_browser_ready"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        status_label.pack(anchor=ctk.W, pady=(5, 0))
        state["status_label"] = status_label

    def _on_tab_changed(self):
        selected = self._tabview.get()
        tab_map = {
            _("mod_browser_tab_mods"): self.TAB_MODS,
            _("mod_browser_tab_resourcepacks"): self.TAB_RESOURCE_PACKS,
            _("mod_browser_tab_shaders"): self.TAB_SHADERS,
        }
        tab_key = tab_map.get(selected)
        if tab_key:
            self._switch_to_tab(tab_key)

    def _switch_to_tab(self, tab_key: str):
        state = self._tab_states[tab_key]
        if state["total_hits"] == 0 and not state["current_query"]:
            children = state["list_frame"].winfo_children()
            has_content = any(not isinstance(w, ctk.CTkLabel) or w != state["loading_label"] for w in children)
            if not has_content or (len(children) == 1 and children[0] == state["loading_label"]):
                self._do_tab_search(tab_key)

    def _on_tab_search(self, tab_key: str):
        state = self._tab_states[tab_key]
        entry = state["search_entry"]
        if entry:
            state["current_query"] = entry.get().strip()
        state["current_offset"] = 0
        self._set_tab_status(tab_key, _("mod_browser_searching"))
        self._run_in_thread(lambda: self._do_tab_search(tab_key))

    def _do_tab_search(self, tab_key: str):
        state = self._tab_states[tab_key]

        try:
            if tab_key == self.TAB_MODS:
                from modrinth import search_mods
                result = search_mods(
                    query=state["current_query"],
                    game_version=self._game_version,
                    mod_loader=self._mod_loader,
                    offset=state["current_offset"],
                    limit=self.PAGE_SIZE,
                )
            elif tab_key == self.TAB_RESOURCE_PACKS:
                from modrinth import search_resource_packs
                result = search_resource_packs(
                    query=state["current_query"],
                    game_version=self._game_version,
                    offset=state["current_offset"],
                    limit=self.PAGE_SIZE,
                )
            elif tab_key == self.TAB_SHADERS:
                from modrinth import search_shaders
                result = search_shaders(
                    query=state["current_query"],
                    game_version=self._game_version,
                    offset=state["current_offset"],
                    limit=self.PAGE_SIZE,
                )
            else:
                return

            hits = result.get("hits", [])
            state["total_hits"] = result.get("total_hits", 0)

            self.after(0, lambda: self._render_tab_results(tab_key, hits))

        except Exception as e:
            logger.error(f"搜索失败 ({tab_key}): {e}")
            self.after(0, lambda: self._render_tab_error(tab_key, str(e)))

    def _render_tab_results(self, tab_key: str, hits: List[Dict]):
        state = self._tab_states[tab_key]
        list_frame = state["list_frame"]

        for w in list_frame.winfo_children():
            w.destroy()

        not_found_texts = {
            self.TAB_MODS: _("mod_browser_no_mods"),
            self.TAB_RESOURCE_PACKS: _("mod_browser_no_rp"),
            self.TAB_SHADERS: _("mod_browser_no_shaders"),
        }

        if not hits:
            ctk.CTkLabel(
                list_frame,
                text=not_found_texts.get(tab_key, _("mod_browser_no_results")),
                font=ctk.CTkFont(family=FONT_FAMILY, size=14),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=40)
            self._update_tab_pagination(tab_key)
            self._set_tab_status(tab_key, _("mod_browser_no_results"))
            return

        for item in hits:
            self._create_item(tab_key, item)

        self._update_tab_pagination(tab_key)

        start = state["current_offset"] + 1
        end = min(state["current_offset"] + self.PAGE_SIZE, state["total_hits"])
        result_label = state["result_count_label"]
        if result_label:
            result_label.configure(text=_("mod_browser_result_range", start=start, end=end, total=state["total_hits"]))
        self._set_tab_status(tab_key, _("mod_browser_total_found", total=state["total_hits"]))

    def _render_tab_error(self, tab_key: str, error_msg: str):
        state = self._tab_states[tab_key]
        list_frame = state["list_frame"]

        for w in list_frame.winfo_children():
            w.destroy()

        ctk.CTkLabel(
            list_frame,
            text=_("mod_browser_search_failed", error=error_msg),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["error"],
            justify=ctk.CENTER,
        ).pack(pady=40)
        self._set_tab_status(tab_key, _("mod_browser_search_failed_status", error=error_msg))

    def _create_item(self, tab_key: str, item: Dict):
        state = self._tab_states[tab_key]
        list_frame = state["list_frame"]

        row = ctk.CTkFrame(
            list_frame,
            fg_color=COLORS["bg_medium"],
            corner_radius=8,
        )
        row.pack(fill=ctk.X, pady=3, padx=2)

        top_row = ctk.CTkFrame(row, fg_color="transparent", height=36)
        top_row.pack(fill=ctk.X, padx=10, pady=(8, 2))
        top_row.pack_propagate(False)

        title = item.get("title", _("mod_browser_unknown"))

        icons = {
            self.TAB_MODS: "🧩",
            self.TAB_RESOURCE_PACKS: "🎨",
            self.TAB_SHADERS: "✨",
        }
        icon = icons.get(tab_key, "📦")

        ctk.CTkLabel(
            top_row,
            text=f"{icon} {title}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        ).pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        downloads = item.get("downloads", 0)
        dl_text = self._format_downloads(downloads)
        ctk.CTkLabel(
            top_row,
            text=f"📥 {dl_text}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT, padx=(5, 0))

        project_id = item.get("project_id", "")

        install_texts = {
            self.TAB_MODS: _("mod_browser_install"),
            self.TAB_RESOURCE_PACKS: _("mod_browser_install"),
            self.TAB_SHADERS: _("mod_browser_install"),
        }

        if tab_key == self.TAB_MODS:
            btn_cmd = lambda pid=project_id, t=title: self._on_install_mod(pid, t)
        elif tab_key == self.TAB_RESOURCE_PACKS:
            btn_cmd = lambda pid=project_id, t=title: self._on_install_resource_pack(pid, t)
        elif tab_key == self.TAB_SHADERS:
            btn_cmd = lambda pid=project_id, t=title: self._on_install_shader(pid, t)
        else:
            btn_cmd = None

        if btn_cmd:
            ctk.CTkButton(
                top_row,
                text=install_texts.get(tab_key, _("mod_browser_install")),
                width=70,
                height=28,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color=COLORS["success"],
                hover_color="#27ae60",
                text_color=COLORS["text_primary"],
                command=btn_cmd,
            ).pack(side=ctk.RIGHT, padx=(8, 0))

        description = item.get("description", "")
        if description:
            ctk.CTkLabel(
                row,
                text=description,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                wraplength=700,
                justify=ctk.LEFT,
                anchor=ctk.W,
            ).pack(fill=ctk.X, padx=10, pady=(0, 4))

        if tab_key == self.TAB_MODS:
            categories = item.get("categories", [])
            versions_display = item.get("versions", [])
            tag_parts = []
            if categories:
                loader_tags = [c for c in categories if c in ("forge", "fabric", "neoforge", "quilt")]
                if loader_tags:
                    tag_parts.append(" | ".join(l.capitalize() for l in loader_tags))
            if versions_display:
                from modrinth import compress_game_versions
                compressed = compress_game_versions(versions_display)
                if compressed:
                    tag_parts.append(compressed)
        else:
            versions_display = item.get("versions", [])
            tag_parts = []
            if versions_display:
                from modrinth import compress_game_versions
                compressed = compress_game_versions(versions_display)
                if compressed:
                    tag_parts.append(compressed)

        if tag_parts:
            tags_text = "  ·  ".join(tag_parts)
            ctk.CTkLabel(
                row,
                text=tags_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X, padx=10, pady=(0, 8))

    def _update_tab_pagination(self, tab_key: str):
        state = self._tab_states[tab_key]
        total_pages = max(1, (state["total_hits"] + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        current_page = (state["current_offset"] // self.PAGE_SIZE) + 1

        page_label = state["page_label"]
        prev_btn = state["prev_btn"]
        next_btn = state["next_btn"]

        if page_label:
            page_label.configure(text=f"{current_page} / {total_pages}")
        if prev_btn:
            prev_btn.configure(state=ctk.NORMAL if state["current_offset"] > 0 else ctk.DISABLED)
        if next_btn:
            next_btn.configure(
                state=ctk.NORMAL if state["current_offset"] + self.PAGE_SIZE < state["total_hits"] else ctk.DISABLED
            )

    def _on_tab_prev_page(self, tab_key: str):
        state = self._tab_states[tab_key]
        if state["current_offset"] > 0:
            state["current_offset"] -= self.PAGE_SIZE
            self._set_tab_status(tab_key, _("mod_browser_loading"))
            self._run_in_thread(lambda: self._do_tab_search(tab_key))

    def _on_tab_next_page(self, tab_key: str):
        state = self._tab_states[tab_key]
        if state["current_offset"] + self.PAGE_SIZE < state["total_hits"]:
            state["current_offset"] += self.PAGE_SIZE
            self._set_tab_status(tab_key, _("mod_browser_loading"))
            self._run_in_thread(lambda: self._do_tab_search(tab_key))

    def _on_install_mod(self, project_id: str, title: str):
        self._set_tab_status(self.TAB_MODS, _("mod_browser_fetching_version", title=title))
        self._run_in_thread(lambda: self._install_mod(project_id, title))

    def _install_mod(self, project_id: str, title: str):
        from modrinth import install_mod_with_deps

        try:
            if not self._game_version or not self._mod_loader:
                self.after(0, lambda: self._set_tab_status(self.TAB_MODS, _("mod_browser_unknown_loader")))
                return

            mods_dir = self._get_mods_dir()

            success, result, installed_names = install_mod_with_deps(
                project_id,
                game_version=self._game_version,
                mod_loader=self._mod_loader,
                mods_dir=mods_dir,
                status_callback=lambda msg: self.after(0, lambda: self._set_tab_status(self.TAB_MODS, msg)),
            )

            if success:
                if len(installed_names) > 1:
                    deps = ", ".join(installed_names[:-1])
                    self.after(
                        0,
                        lambda: self._set_tab_status(
                            self.TAB_MODS,
                            _("mod_browser_install_success_deps", title=title, deps=deps),
                        ),
                    )
                else:
                    self.after(
                        0,
                        lambda: self._set_tab_status(self.TAB_MODS, _("mod_browser_install_success", title=title)),
                    )
                logger.info(f"模组安装成功: {installed_names} -> {result}")
            else:
                self.after(0, lambda: self._set_tab_status(self.TAB_MODS, _("mod_browser_install_failed", error=result)))
                logger.error(f"模组安装失败: {result}")

        except Exception as e:
            error_msg = str(e)
            self.after(
                0,
                lambda: self._set_tab_status(self.TAB_MODS, _("mod_browser_install_error", error=error_msg)),
            )
            logger.error(f"安装模组失败: {e}")

    def _on_install_resource_pack(self, project_id: str, title: str):
        self._set_tab_status(self.TAB_RESOURCE_PACKS, _("mod_browser_fetching_version", title=title))
        self._run_in_thread(lambda: self._install_resource_pack(project_id, title))

    def _install_resource_pack(self, project_id: str, title: str):
        from modrinth import install_resource_pack

        try:
            if not self._game_version:
                self.after(
                    0,
                    lambda: self._set_tab_status(self.TAB_RESOURCE_PACKS, _("mod_browser_unknown_version")),
                )
                return

            rp_dir = self._get_resourcepacks_dir()

            success, result = install_resource_pack(
                project_id,
                game_version=self._game_version,
                resourcepacks_dir=rp_dir,
                status_callback=lambda msg: self.after(
                    0,
                    lambda: self._set_tab_status(self.TAB_RESOURCE_PACKS, msg),
                ),
            )

            if success:
                self.after(
                    0,
                    lambda: self._set_tab_status(
                        self.TAB_RESOURCE_PACKS,
                        _("mod_browser_install_success", title=title),
                    ),
                )
                logger.info(f"资源包安装成功: {title} -> {result}")
            else:
                self.after(
                    0,
                    lambda: self._set_tab_status(
                        self.TAB_RESOURCE_PACKS,
                        _("mod_browser_install_failed", error=result),
                    ),
                )
                logger.error(f"资源包安装失败: {result}")

        except Exception as e:
            error_msg = str(e)
            self.after(
                0,
                lambda: self._set_tab_status(
                    self.TAB_RESOURCE_PACKS,
                    _("mod_browser_install_error", error=error_msg),
                ),
            )
            logger.error(f"安装资源包失败: {e}")

    def _on_install_shader(self, project_id: str, title: str):
        self._set_tab_status(self.TAB_SHADERS, _("mod_browser_fetching_version", title=title))
        self._run_in_thread(lambda: self._install_shader(project_id, title))

    def _install_shader(self, project_id: str, title: str):
        from modrinth import install_shader

        try:
            if not self._game_version:
                self.after(
                    0,
                    lambda: self._set_tab_status(self.TAB_SHADERS, _("mod_browser_unknown_version")),
                )
                return

            shader_dir = self._get_shaderpacks_dir()

            success, result = install_shader(
                project_id,
                game_version=self._game_version,
                shaderpacks_dir=shader_dir,
                status_callback=lambda msg: self.after(
                    0,
                    lambda: self._set_tab_status(self.TAB_SHADERS, msg),
                ),
            )

            if success:
                self.after(
                    0,
                    lambda: self._set_tab_status(
                        self.TAB_SHADERS,
                        _("mod_browser_install_success", title=title),
                    ),
                )
                logger.info(f"光影安装成功: {title} -> {result}")
            else:
                self.after(
                    0,
                    lambda: self._set_tab_status(
                        self.TAB_SHADERS,
                        _("mod_browser_install_failed", error=result),
                    ),
                )
                logger.error(f"光影安装失败: {result}")

        except Exception as e:
            error_msg = str(e)
            self.after(
                0,
                lambda: self._set_tab_status(
                    self.TAB_SHADERS,
                    _("mod_browser_install_error", error=error_msg),
                ),
            )
            logger.error(f"安装光影失败: {e}")

    def _get_mods_dir(self) -> str:
        mc_dir = Path(".")
        if "get_minecraft_dir" in self.callbacks:
            mc_dir = Path(self.callbacks["get_minecraft_dir"]())

        v = self.version_id.lower()
        if any(loader in v for loader in ("forge", "fabric", "neoforge")):
            version_dir = mc_dir / "versions" / self.version_id / "mods"
            return str(version_dir)

        return str(mc_dir / "mods")

    def _get_resourcepacks_dir(self) -> str:
        mc_dir = Path(".")
        if "get_minecraft_dir" in self.callbacks:
            mc_dir = Path(self.callbacks["get_minecraft_dir"]())

        v = self.version_id.lower()
        if any(loader in v for loader in ("forge", "fabric", "neoforge")):
            version_dir = mc_dir / "versions" / self.version_id / "resourcepacks"
            return str(version_dir)

        return str(mc_dir / "resourcepacks")

    def _get_shaderpacks_dir(self) -> str:
        mc_dir = Path(".")
        if "get_minecraft_dir" in self.callbacks:
            mc_dir = Path(self.callbacks["get_minecraft_dir"]())

        v = self.version_id.lower()
        if any(loader in v for loader in ("forge", "fabric", "neoforge")):
            version_dir = mc_dir / "versions" / self.version_id / "shaderpacks"
            return str(version_dir)

        return str(mc_dir / "shaderpacks")

    @staticmethod
    def _format_downloads(count: int) -> str:
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _set_tab_status(self, tab_key: str, text: str):
        try:
            if self.winfo_exists():
                state = self._tab_states.get(tab_key)
                if state and state["status_label"]:
                    state["status_label"].configure(text=text)
        except Exception:
            pass

    def _run_in_thread(self, target, *args, **kwargs):
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
