"""ModernApp 工具 Mixin - 工具标签页相关方法"""
import os
import hashlib
import threading
import time
from datetime import date
from pathlib import Path
from tkinter import filedialog

import requests
import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


def _format_size(bytes_count: int) -> str:
    if bytes_count < 1024:
        return f"{bytes_count} B"
    elif bytes_count < 1024 * 1024:
        return f"{bytes_count / 1024:.1f} KB"
    elif bytes_count < 1024 * 1024 * 1024:
        return f"{bytes_count / (1024 * 1024):.1f} MB"
    else:
        return f"{bytes_count / (1024 * 1024 * 1024):.2f} GB"


class ToolsTabMixin(object):
    """工具标签页 Mixin"""

    def _build_tools_tab_content(self):
        content = ctk.CTkScrollableFrame(self.tools_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        self._build_tool_clean_junk(content)
        self._build_tool_daily_fortune(content)
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
            card,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )

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

    def _on_clean_junk(self):
        btn = self._clean_junk_btn
        btn.configure(state=ctk.DISABLED, text=_("tool_clean_junk_scanning"))

        def _task():
            try:
                mc_dir = self._get_minecraft_dir_for_tools()
                junk_files = []
                total_size = 0

                for root, dirs, files in os.walk(str(mc_dir)):
                    for f in files:
                        if f.endswith(".log") or f.endswith(".tmp"):
                            fp = os.path.join(root, f)
                            try:
                                size = os.path.getsize(fp)
                            except OSError:
                                size = 0
                            junk_files.append((fp, size))
                            total_size += size

                def _update_ui():
                    if not junk_files:
                        self._clean_junk_status.configure(
                            text=_("tool_clean_junk_none"),
                            text_color=COLORS["text_secondary"],
                        )
                        btn.configure(state=ctk.NORMAL, text=_("tool_clean_junk_scan"))
                        self._clean_junk_status.pack(anchor=ctk.W, padx=16, pady=(0, 8))
                    else:
                        self._clean_junk_status.configure(
                            text=_("tool_clean_junk_found", count=len(junk_files), size=_format_size(total_size)),
                            text_color=COLORS["accent"],
                        )
                        self._clean_junk_status.pack(anchor=ctk.W, padx=16, pady=(0, 8))
                        btn.configure(
                            state=ctk.NORMAL,
                            text=_("tool_clean_junk_delete"),
                            fg_color=COLORS["accent"],
                            hover_color=COLORS["accent_hover"],
                            command=self._on_delete_junk_files,
                        )
                        self._clean_junk__files = junk_files
                        self._clean_junk__total_size = total_size
                self.after(0, _update_ui)
            except Exception as e:
                logger.error(f"扫描垃圾文件失败: {e}")

                def _error_ui():
                    btn.configure(state=ctk.NORMAL, text=_("tool_clean_junk_scan"))
                    self._clean_junk_status.configure(
                        text=_("tool_clean_junk_error", error=str(e)),
                        text_color=COLORS["text_secondary"],
                    )
                    self._clean_junk_status.pack(anchor=ctk.W, padx=16, pady=(0, 8))
                self.after(0, _error_ui)

        threading.Thread(target=_task, daemon=True).start()

    def _on_delete_junk_files(self):
        files = getattr(self, "_clean_junk__files", [])
        total_size = getattr(self, "_clean_junk__total_size", 0)
        if not files:
            return

        btn = self._clean_junk_btn
        btn.configure(state=ctk.DISABLED, text=_("tool_clean_junk_deleting"))

        def _task():
            deleted = 0
            failed = 0
            for fp, _ in files:
                try:
                    os.remove(fp)
                    deleted += 1
                except OSError as e:
                    logger.error(f"删除文件失败 {fp}: {e}")
                    failed += 1

            def _update_ui():
                self._clean_junk_status.configure(
                    text=_("tool_clean_junk_done", deleted=deleted, failed=failed, size=_format_size(total_size)),
                    text_color=COLORS["accent"] if failed == 0 else COLORS["text_secondary"],
                )
                self._clean_junk_status.pack(anchor=ctk.W, padx=16, pady=(0, 8))
                btn.configure(state=ctk.NORMAL, text=_("tool_clean_junk_scan"), fg_color=COLORS["bg_light"],
                              hover_color=COLORS["card_border"], command=self._on_clean_junk)
                self._clean_junk__files = []
                self._clean_junk__total_size = 0
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
        today = date.today().isoformat()
        seed_str = f"fmcl_fortune_{today}"
        seed = int(hashlib.sha256(seed_str.encode()).hexdigest(), 16)
        value = seed % 101

        if value <= 20:
            level_key = "tool_fortune_terrible"
            emoji = "💀"
        elif value <= 40:
            level_key = "tool_fortune_bad"
            emoji = "😟"
        elif value <= 60:
            level_key = "tool_fortune_normal"
            emoji = "😐"
        elif value <= 80:
            level_key = "tool_fortune_good"
            emoji = "😊"
        elif value <= 95:
            level_key = "tool_fortune_great"
            emoji = "🌟"
        else:
            level_key = "tool_fortune_legendary"
            emoji = "👑"

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
        path = filedialog.asksaveasfilename(
            title=_("tool_download_save_title"),
            parent=self,
        )
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

        if not url:
            self.set_status(_("tool_download_no_url"), "error")
            return
        if not save_path:
            save_dir = filedialog.askdirectory(
                title=_("tool_download_save_title"),
                parent=self,
            )
            if not save_dir:
                return
        else:
            save_dir = os.path.dirname(save_path)
            if not save_dir:
                save_dir = "."

        if not os.path.isdir(save_dir):
            try:
                os.makedirs(save_dir, exist_ok=True)
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
            self._dl_progress_frame,
            width=400,
            height=12,
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
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

        def _task():
            import time as _time

            num_threads = self._get_download_threads_for_tools()
            self._dl_cancel_flag = False

            try:
                resp = requests.head(url, headers={"User-Agent": ua}, timeout=15)
                resp.raise_for_status()

                total_size = int(resp.headers.get("Content-Length", 0))
                if total_size == 0:
                    resp2 = requests.get(url, headers={"User-Agent": ua}, stream=True, timeout=30)
                    resp2.raise_for_status()
                    chunks = []
                    for chunk in resp2.iter_content(chunk_size=8192):
                        if self._dl_cancel_flag:
                            resp2.close()
                            raise Exception("cancelled")
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    total_size = len(content)
                    if not save_path or os.path.isdir(save_path):
                        filename_from_url = url.split("/")[-1].split("?")[0]
                        if not filename_from_url:
                            filename_from_url = "downloaded_file"
                        filename = os.path.join(save_dir, filename_from_url)
                    else:
                        filename = save_path
                    with open(filename, "wb") as f:
                        f.write(content)

                    def _single_done():
                        self._dl_progress_bar.set(1)
                        self._dl_speed_label.configure(text="")
                        self._dl_status_label.configure(
                            text=_("tool_download_success", path=filename),
                            text_color=COLORS["accent"],
                        )
                        self._dl_start_btn.configure(state=ctk.NORMAL, text=_("tool_download_start"))
                    self.after(0, _single_done)
                    return

                if not save_path or os.path.isdir(save_path):
                    filename_from_url = url.split("/")[-1].split("?")[0]
                    if not filename_from_url:
                        filename_from_url = "downloaded_file"
                    filename = os.path.join(save_dir, filename_from_url)
                else:
                    filename = save_path

                downloaded = 0
                lock = threading.Lock()
                start_time = _time.time()
                part_size = total_size // num_threads

                def _dl_part(start: int, end: int, idx: int):
                    nonlocal downloaded
                    headers = {
                        "User-Agent": ua,
                        "Range": f"bytes={start}-{end}",
                    }
                    part_file = f"{filename}.part{idx}"
                    try:
                        r = requests.get(url, headers=headers, stream=True, timeout=60)
                        r.raise_for_status()
                        with open(part_file, "wb") as pf:
                            for chunk in r.iter_content(chunk_size=8192):
                                if self._dl_cancel_flag:
                                    r.close()
                                    return
                                if chunk:
                                    pf.write(chunk)
                                    with lock:
                                        downloaded += len(chunk)
                                        elapsed = _time.time() - start_time
                                        if elapsed > 0:
                                            speed = downloaded / elapsed

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
                    except Exception as e:
                        logger.error(f"分段下载 {idx} 失败: {e}")
                        raise

                threads_list = []
                for i in range(num_threads):
                    start_byte = i * part_size
                    end_byte = start_byte + part_size - 1 if i < num_threads - 1 else total_size - 1
                    t = threading.Thread(target=_dl_part, args=(start_byte, end_byte, i))
                    t.daemon = True
                    threads_list.append(t)
                    t.start()

                for t in threads_list:
                    t.join()

                if self._dl_cancel_flag:
                    for i in range(num_threads):
                        pf = f"{filename}.part{i}"
                        if os.path.exists(pf):
                            os.remove(pf)

                    def _cancel_ui():
                        self._dl_status_label.configure(
                            text=_("tool_download_cancelled"),
                            text_color=COLORS["text_secondary"],
                        )
                        self._dl_speed_label.configure(text="")
                        self._dl_start_btn.configure(state=ctk.NORMAL, text=_("tool_download_start"))
                    self.after(0, _cancel_ui)
                    return

                with open(filename, "wb") as outf:
                    for i in range(num_threads):
                        pf = f"{filename}.part{i}"
                        if os.path.exists(pf):
                            with open(pf, "rb") as inf:
                                outf.write(inf.read())
                            os.remove(pf)

                def _done_ui():
                    self._dl_progress_bar.set(1)
                    self._dl_speed_label.configure(text="")
                    self._dl_status_label.configure(
                        text=_("tool_download_success", path=filename),
                        text_color=COLORS["accent"],
                    )
                    self._dl_start_btn.configure(state=ctk.NORMAL, text=_("tool_download_start"))
                self.after(0, _done_ui)

            except Exception as e:
                if str(e) == "cancelled":
                    return
                logger.error(f"下载失败: {e}")

                def _error_ui():
                    self._dl_status_label.configure(
                        text=_("tool_download_error", error=str(e)),
                        text_color="#cd5c5c",
                    )
                    self._dl_speed_label.configure(text="")
                    self._dl_start_btn.configure(state=ctk.NORMAL, text=_("tool_download_start"))
                self.after(0, _error_ui)

        threading.Thread(target=_task, daemon=True).start()
