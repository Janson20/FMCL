"""服务器资源管理窗口 - 管理服务端模组"""
import os
import json
import threading
import base64
from io import BytesIO
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any
from tkinter import messagebox, filedialog

import customtkinter as ctk
from logzero import logger
from PIL import Image

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


class ServerResourceManagerWindow(ctk.CTkToplevel):
    """服务器资源管理窗口 - 管理服务端模组"""

    def __init__(self, parent, version_id: str, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self._fix_customtkinter_icon(self)
        self.version_id = version_id
        self.callbacks = callbacks

        self.title(_("server_resource_manager", version=version_id))
        self.geometry("760x600")
        self.minsize(680, 520)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)

        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 760, 600
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self._mod_metadata: List[Dict] = []
        self._mod_loading: bool = False
        self._search_text: str = ""
        self._filtered_items: List[Dict] = []
        self._update_checking: bool = False
        self._update_info: Dict[str, Dict] = {}
        self._page_size: int = 10
        self._current_page: int = 1

        self._build_ui()
        self.after(200, self._refresh_mod_list)

    @staticmethod
    def _fix_customtkinter_icon(toplevel):
        """修复 CTkToplevel 因内置图标延迟回调崩溃的问题"""
        import types
        try:
            icon_path = Path(__file__).parent.parent.parent / "icon.ico"
            icon_str = str(icon_path) if icon_path.exists() else ""
        except Exception:
            icon_str = ""

        original = toplevel.iconbitmap

        def safe_iconbitmap(_self, bitmap=None, default=None):
            try:
                if bitmap is not None:
                    return original(bitmap=bitmap)
                if default is not None:
                    return original(default=default)
                return original()
            except Exception:
                pass

        toplevel.iconbitmap = types.MethodType(safe_iconbitmap, toplevel)

        if icon_str:
            try:
                original(bitmap=icon_str)
            except Exception:
                pass

    def _get_mods_dir(self) -> Path:
        server_dir = Path(".")
        if "get_server_dir" in self.callbacks:
            server_dir = Path(self.callbacks["get_server_dir"]())

        v = self.version_id.lower()
        if any(loader in v for loader in ("forge", "fabric", "neoforge")):
            mods_dir = server_dir / "versions" / self.version_id / "mods"
        else:
            mods_dir = server_dir / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        return mods_dir

    def _build_ui(self):
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        title_label = ctk.CTkLabel(
            main_frame,
            text=_("server_resource_manager_title", version=self.version_id),
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title_label.pack(anchor=ctk.W, pady=(0, 10))

        content_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        content_frame.pack(fill=ctk.BOTH, expand=True)

        top_bar = ctk.CTkFrame(content_frame, fg_color="transparent", height=42)
        top_bar.pack(fill=ctk.X, padx=12, pady=(10, 5))
        top_bar.pack_propagate(False)

        self._drag_hint_label = ctk.CTkLabel(
            top_bar,
            text=_("server_rm_mods_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._drag_hint_label.pack(side=ctk.LEFT)

        self._open_folder_btn = ctk.CTkButton(
            top_bar,
            text=_("resource_open_folder"),
            width=110,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._open_folder,
        )
        self._open_folder_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        self._add_file_btn = ctk.CTkButton(
            top_bar,
            text=_("resource_add"),
            width=130,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._select_file_install,
        )
        self._add_file_btn.pack(side=ctk.RIGHT)

        self._export_btn = ctk.CTkButton(
            top_bar,
            text=_("mod_export_list"),
            width=100,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._export_mod_list,
        )
        self._export_btn.pack(side=ctk.RIGHT, padx=(5, 5))

        self._check_updates_btn = ctk.CTkButton(
            top_bar,
            text=_("mod_check_updates"),
            width=100,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            text_color=COLORS["text_primary"],
            command=self._check_mod_updates,
        )
        self._check_updates_btn.pack(side=ctk.RIGHT, padx=(5, 5))

        ctk.CTkFrame(content_frame, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(0, 5)
        )

        self._search_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        self._search_entry = ctk.CTkEntry(
            self._search_frame,
            placeholder_text=_("rm_search_placeholder_mods"),
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
        )
        self._search_entry.pack(fill=ctk.X, padx=12, pady=(0, 5))
        self._search_frame.pack(fill=ctk.X, pady=(0, 0))
        self._search_entry.bind("<KeyRelease>", self._on_search)
        self._search_entry.bind("<Return>", self._on_search)

        self._loading_label = ctk.CTkLabel(
            content_frame,
            text=_("mod_loading_metadata"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_secondary"],
        )

        self._drop_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        self._drop_frame.pack(fill=ctk.BOTH, expand=True, padx=12, pady=(0, 10))

        self._empty_label = ctk.CTkLabel(
            self._drop_frame,
            text=_("rm_drop_hint"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            justify=ctk.CENTER,
        )

        self._list_frame = ctk.CTkScrollableFrame(
            self._drop_frame,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )

        self._page_frame = ctk.CTkFrame(content_frame, fg_color="transparent", height=34)
        self._page_frame.pack_propagate(False)

        self._prev_btn = ctk.CTkButton(
            self._page_frame,
            text=_("rm_page_prev"),
            width=85,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            state=ctk.DISABLED,
            command=self._on_prev_page,
        )
        self._prev_btn.pack(side=ctk.LEFT, padx=(12, 0))

        self._page_label = ctk.CTkLabel(
            self._page_frame,
            text="1 / 1",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            width=80,
        )
        self._page_label.pack(side=ctk.LEFT, padx=8)

        self._next_btn = ctk.CTkButton(
            self._page_frame,
            text=_("rm_page_next"),
            width=85,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            state=ctk.DISABLED,
            command=self._on_next_page,
        )
        self._next_btn.pack(side=ctk.LEFT)

        self._status_label = ctk.CTkLabel(
            main_frame,
            text=_("rm_status_ready"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._status_label.pack(anchor=ctk.W, pady=(5, 0))

    def _open_folder(self):
        mods_dir = self._get_mods_dir()
        if not mods_dir.exists():
            mods_dir.mkdir(parents=True, exist_ok=True)
        import sys, subprocess
        if sys.platform == 'win32':
            os.startfile(str(mods_dir))
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(mods_dir)])
        else:
            subprocess.Popen(['xdg-open', str(mods_dir)])

    def _select_file_install(self):
        mods_dir = self._get_mods_dir()
        files = filedialog.askopenfilenames(
            title=_("resource_select_file"),
            filetypes=[("Mod文件", "*.jar *.zip"), ("所有文件", "*.*")],
        )
        if not files:
            return
        import shutil
        installed = 0
        for fpath in files:
            p = Path(fpath)
            if p.suffix.lower() in (".jar", ".zip"):
                try:
                    shutil.copy2(str(p), str(mods_dir / p.name))
                    installed += 1
                except Exception as e:
                    logger.error(f"复制模组文件失败: {e}")
        if installed > 0:
            self._set_status(_("rm_install_count", count=installed))
            self._refresh_mod_list()

    def _refresh_mod_list(self):
        mods_dir = self._get_mods_dir()
        from modrinth import extract_all_mods_metadata

        self._mod_loading = True
        self._loading_label.pack(fill=ctk.X, padx=12, pady=(5, 10))

        def _load_metadata():
            try:
                results = extract_all_mods_metadata(
                    mods_dir,
                    status_callback=lambda done, total: self.after(0, lambda d=done, t=total: self._update_mod_loading(d, t)),
                )
                self.after(0, lambda r=results: self._on_mod_metadata_loaded(r))
            except Exception as e:
                logger.error(f"提取模组元数据失败: {e}")
                self.after(0, lambda: self._on_mod_metadata_loaded([]))

        thread = threading.Thread(target=_load_metadata, daemon=True)
        thread.start()

    def _update_mod_loading(self, done: int, total: int):
        if not self.winfo_exists():
            return
        if self._mod_loading:
            self._loading_label.configure(text=_("mod_loading_progress", done=done, total=total))

    def _on_mod_metadata_loaded(self, results: List[Dict]):
        if not self.winfo_exists():
            return
        self._mod_loading = False
        self._mod_metadata = results
        self._loading_label.pack_forget()
        self._render_mod_list()

    def _on_search(self, event=None):
        self._search_text = self._search_entry.get().strip().lower()
        self._current_page = 1
        self._render_mod_list()

    def _render_mod_list(self):
        self._list_frame.pack_forget()
        self._empty_label.pack_forget()

        if self._mod_loading:
            self._page_frame.pack_forget()
            return

        if not self._mod_metadata:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._page_frame.pack_forget()
            self._set_status(_("mod_folder_empty"))
            return

        if self._search_text:
            filtered = [
                m for m in self._mod_metadata
                if self._search_text in m.get("name", "").lower()
                or self._search_text in m.get("modid", "").lower()
                or self._search_text in m.get("author", "").lower()
                or self._search_text in m.get("description", "").lower()
                or self._search_text in m.get("filename", "").lower()
            ]
        else:
            filtered = self._mod_metadata

        self._filtered_items = filtered

        if not filtered:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._page_frame.pack_forget()
            self._set_status(_("mod_search_no_results"))
            return

        total_pages = max(1, (len(filtered) + self._page_size - 1) // self._page_size)
        if self._current_page > total_pages:
            self._current_page = total_pages

        for w in self._list_frame.winfo_children():
            w.destroy()

        start = (self._current_page - 1) * self._page_size
        end = min(start + self._page_size, len(filtered))
        page_items = filtered[start:end]

        self._list_frame.pack(fill=ctk.BOTH, expand=True)
        self._page_frame.pack(fill=ctk.X, padx=12, pady=(5, 0))

        for item in page_items:
            self._create_mod_card(item)

        self._page_label.configure(text=_("rm_page_info", current=self._current_page, total=total_pages))
        self._prev_btn.configure(state=ctk.NORMAL if self._current_page > 1 else ctk.DISABLED)
        self._next_btn.configure(state=ctk.NORMAL if self._current_page < total_pages else ctk.DISABLED)
        self._set_status(_("rm_list_count", count=len(filtered), page=self._current_page, total_pages=total_pages, label=_("resource_mods")))

    def _on_prev_page(self):
        if self._current_page > 1:
            self._current_page -= 1
            self._render_mod_list()

    def _on_next_page(self):
        total = len(self._filtered_items)
        total_pages = max(1, (total + self._page_size - 1) // self._page_size)
        if self._current_page < total_pages:
            self._current_page += 1
            self._render_mod_list()

    def _create_mod_card(self, item: Dict):
        row = ctk.CTkFrame(
            self._list_frame,
            fg_color=COLORS["bg_medium"],
            corner_radius=8,
        )
        row.pack(fill=ctk.X, pady=3, padx=2)

        icon_size = 48
        icon_frame = ctk.CTkFrame(row, fg_color="transparent", width=icon_size, height=icon_size)
        icon_frame.pack(side=ctk.LEFT, padx=(8, 8), pady=8)
        icon_frame.pack_propagate(False)

        icon_base64 = item.get("icon_base64")
        if icon_base64:
            try:
                img_data = base64.b64decode(icon_base64)
                img = Image.open(BytesIO(img_data))
                photo = ctk.CTkImage(img, size=(icon_size, icon_size))
                icon_label = ctk.CTkLabel(icon_frame, image=photo, text="")
                icon_label.pack(fill=ctk.BOTH, expand=True)
            except Exception:
                self._create_placeholder_icon(icon_frame, icon_size)
        else:
            self._create_placeholder_icon(icon_frame, icon_size)

        text_frame = ctk.CTkFrame(row, fg_color="transparent")
        text_frame.pack(side=ctk.LEFT, fill=ctk.X, expand=True, pady=6)

        name = item.get("name", item.get("filename", _("mod_unknown")))
        modid = item.get("modid", "")
        author = item.get("author", "")
        description = item.get("description", "")

        name_text = name
        if modid:
            name_text += f"  ({modid})"
        ctk.CTkLabel(
            text_frame,
            text=name_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        ).pack(fill=ctk.X)

        meta_parts = []
        if author:
            meta_parts.append(author)
        version = item.get("version", "")
        if version:
            meta_parts.append(version)
        if meta_parts:
            ctk.CTkLabel(
                text_frame,
                text=" | ".join(meta_parts),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X, pady=(1, 0))

        if description:
            desc_text = description if len(description) <= 80 else description[:77] + "..."
            ctk.CTkLabel(
                text_frame,
                text=desc_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
                wraplength=300,
                justify=ctk.LEFT,
            ).pack(fill=ctk.X, pady=(1, 0))

        disabled = item.get("disabled", False)
        btn_frame = ctk.CTkFrame(row, fg_color="transparent")
        btn_frame.pack(side=ctk.RIGHT, padx=(5, 8), pady=8)

        toggle_btn = ctk.CTkButton(
            btn_frame,
            text=_("mod_enable") if disabled else _("mod_disable"),
            width=65,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["warning"] if disabled else COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=lambda i=item: self._toggle_mod(i),
        )
        toggle_btn.pack(side=ctk.TOP, pady=(0, 3))

        delete_btn = ctk.CTkButton(
            btn_frame,
            text=_("mod_delete"),
            width=65,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            command=lambda i=item: self._delete_mod(i),
        )
        delete_btn.pack(side=ctk.TOP)

        if disabled:
            row.configure(fg_color=COLORS["bg_dark"])

    def _create_placeholder_icon(self, parent, size: int):
        frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_light"], width=size, height=size, corner_radius=8)
        frame.pack(fill=ctk.BOTH, expand=True)
        frame.pack_propagate(False)
        ctk.CTkLabel(
            frame,
            text="🧩",
            font=ctk.CTkFont(size=int(size * 0.5)),
            text_color=COLORS["text_secondary"],
        ).pack(expand=True)

    def _toggle_mod(self, item: Dict):
        filepath = item.get("filepath", "")
        if not filepath:
            return
        src = Path(filepath)
        if not src.exists():
            return
        disabled = item.get("disabled", False)
        if disabled:
            dst_name = src.name
            if dst_name.endswith(".disabled"):
                dst_name = dst_name[:-9]
            dst = src.parent / dst_name
        else:
            dst = src.parent / (src.name + ".disabled")

        try:
            src.rename(dst)
            self._refresh_mod_list()
            self._set_status(_("mod_disabled_ok" if not disabled else "mod_enabled_ok"))
        except Exception as e:
            logger.error(f"切换模组状态失败: {e}")

    def _delete_mod(self, item: Dict):
        filepath = item.get("filepath", "")
        if not filepath:
            return
        name = item.get("name", item.get("filename", ""))
        if not messagebox.askyesno(
            _("mod_delete_confirm_title"),
            _("mod_delete_confirm_msg", name=name),
        ):
            return
        try:
            Path(filepath).unlink()
            self._refresh_mod_list()
            self._set_status(_("mod_deleted_ok", name=name))
        except Exception as e:
            logger.error(f"删除模组失败: {e}")

    def _export_mod_list(self):
        if not self._mod_metadata:
            self._set_status(_("mod_export_no_mods"))
            return
        try:
            lines = [_("mod_export_header")]
            for m in self._mod_metadata:
                name = m.get("name", m.get("filename", ""))
                modid = m.get("modid", "")
                version = m.get("version", "")
                disabled = m.get("disabled", False)
                status = _("mod_disabled") if disabled else _("mod_enabled")
                parts = [name]
                if modid:
                    parts.append(f"ID: {modid}")
                if version:
                    parts.append(version)
                parts.append(status)
                lines.append(" - ".join(parts))
            content = "\n".join(lines)
            file_path = filedialog.asksaveasfilename(
                title=_("mod_export_save_title"),
                defaultextension=".txt",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            )
            if file_path:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)
                self._set_status(_("mod_export_done", path=file_path))
        except Exception as e:
            logger.error(f"导出模组列表失败: {e}")

    def _check_mod_updates(self):
        if self._update_checking:
            return

        from modrinth import parse_game_version_from_version, parse_mod_loader_from_version

        game_version = parse_game_version_from_version(self.version_id)
        mod_loader = parse_mod_loader_from_version(self.version_id)

        if not game_version:
            self._set_status(_("mod_update_unknown_version"))
            return

        mods_with_modid = [
            m for m in self._mod_metadata
            if m.get("modid") and not m.get("disabled")
        ]

        if not mods_with_modid:
            self._set_status(_("mod_update_no_modid"))
            return

        self._update_checking = True
        self._update_info.clear()
        self._check_updates_btn.configure(
            text=_("mod_checking_updates"),
            state=ctk.DISABLED,
            fg_color=COLORS["bg_light"],
        )
        self._set_status(_("mod_checking_updates_progress", current=0, total=len(mods_with_modid)))
        self._run_in_thread(self._do_check_updates, mods_with_modid, game_version, mod_loader)

    def _do_check_updates(self, mods_with_modid: List[Dict], game_version: str, mod_loader: Optional[str]):
        from modrinth import get_mod_versions

        for i, mod in enumerate(mods_with_modid):
            modid = mod.get("modid", "")
            try:
                versions = get_mod_versions(
                    project_id=modid,
                    game_version=game_version,
                    mod_loader=mod_loader,
                )
                if versions:
                    latest = versions[0]
                    current_version = mod.get("version", "")
                    if current_version and latest.get("version_number") != current_version:
                        self._update_info[modid] = {
                            "latest_version": latest["version_number"],
                            "project_id": modid,
                            "title": mod.get("name", ""),
                        }
            except Exception as e:
                logger.debug(f"检查模组 {modid} 更新失败: {e}")
            progress = i + 1
            total = len(mods_with_modid)
            self.after(0, lambda p=progress, t=total: self._set_status(
                _("mod_checking_updates_progress", current=p, total=t)
            ))

        self.after(0, self._on_update_check_done)

    def _on_update_check_done(self):
        if not self.winfo_exists():
            return
        self._update_checking = False
        self._check_updates_btn.configure(
            text=_("mod_check_updates"),
            state=ctk.NORMAL,
            fg_color=COLORS["success"],
        )
        if self._update_info:
            self._show_update_dialog()
        else:
            self._set_status(_("mod_update_all_latest"))

    def _show_update_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title(_("mod_update_dialog_title"))
        dialog.geometry("500x400")
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass
        dialog.configure(fg_color=COLORS["bg_dark"])

        ctk.CTkLabel(
            dialog,
            text=_("mod_update_found_count", count=len(self._update_info)),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=15, pady=(15, 10))

        scroll = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        scroll.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 10))

        checkbox_vars = {}
        for modid, info in self._update_info.items():
            frame = ctk.CTkFrame(scroll, fg_color=COLORS["bg_medium"], corner_radius=6)
            frame.pack(fill=ctk.X, pady=2, padx=2)

            var = ctk.BooleanVar(value=True)
            checkbox_vars[modid] = var
            cb = ctk.CTkCheckBox(
                frame,
                text=f"{info.get('title', '')} → {info.get('latest_version', '')}",
                variable=var,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_primary"],
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
            )
            cb.pack(padx=10, pady=6, anchor=ctk.W)

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill=ctk.X, padx=15, pady=(0, 15))

        ctk.CTkButton(
            btn_frame,
            text=_("mod_update_selected"),
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda: self._batch_update_mods(
                [mid for mid, var in checkbox_vars.items() if var.get()],
                checkbox_vars,
                dialog,
            ),
        ).pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 5))

        ctk.CTkButton(
            btn_frame,
            text=_("close"),
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=dialog.destroy,
        ).pack(side=ctk.RIGHT)

    def _batch_update_mods(self, modids: List[str], checkbox_vars: dict, dialog):
        if not modids:
            return

        dialog.destroy()
        self._set_status(_("mod_update_batch_starting", count=len(modids)))

        from modrinth import parse_game_version_from_version, parse_mod_loader_from_version
        from modrinth import download_mod, get_mod_versions
        from concurrent.futures import ThreadPoolExecutor, as_completed

        game_version = parse_game_version_from_version(self.version_id)
        mod_loader = parse_mod_loader_from_version(self.version_id)

        if not game_version or not mod_loader:
            self._set_status(_("mod_update_failed", error=_("mod_browser_unknown_loader")))
            return

        mods_dir = self._get_mods_dir()
        lock = threading.Lock()
        done = [0]
        success_count = [0]
        fail_count = [0]

        def _update_one(modid: str):
            try:
                versions = get_mod_versions(
                    project_id=modid,
                    game_version=game_version,
                    mod_loader=mod_loader,
                )
                if not versions:
                    with lock:
                        fail_count[0] += 1
                    return
                latest = versions[0]
                file_url = None
                file_name = None
                for v in latest.get("files", []):
                    if v.get("primary"):
                        file_url = v.get("url")
                        file_name = v.get("filename")
                        break
                if not file_url:
                    files = latest.get("files", [])
                    if files:
                        file_url = files[0].get("url")
                        file_name = files[0].get("filename")
                if file_url and file_name:
                    ok = download_mod(file_url, mods_dir, file_name)
                    if ok:
                        with lock:
                            success_count[0] += 1
                    else:
                        with lock:
                            fail_count[0] += 1
                else:
                    with lock:
                        fail_count[0] += 1
            except Exception:
                with lock:
                    fail_count[0] += 1
            finally:
                with lock:
                    done[0] += 1
                    d = done[0]
                    t = len(modids)
                    self.after(0, lambda: self._set_status(
                        _("mod_update_batch_progress", current=d, total=t)
                    ))

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(_update_one, mid): mid for mid in modids}
            for f in as_completed(futures):
                pass

        self._refresh_mod_list()
        self._set_status(
            _("mod_update_batch_done", success=success_count[0], fail=fail_count[0])
        )

    def _set_status(self, text: str):
        try:
            if self.winfo_exists() and hasattr(self, '_status_label') and self._status_label:
                self._status_label.configure(text=text)
        except Exception:
            pass

    def _run_in_thread(self, target, *args, **kwargs):
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
