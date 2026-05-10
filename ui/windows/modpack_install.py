"""整合包安装窗口 - 选择 .mrpack 文件，确认信息，执行安装"""
import os
import threading
import tkinter.messagebox as messagebox
from typing import List, Dict, Optional, Callable, Any

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY
from ui.dialogs import show_notification
from ui.i18n import _


def _trigger_ach(achievement_id: str, value: int = 1, trigger_type: str = "increment"):
    try:
        from achievement_engine import get_achievement_engine
        engine = get_achievement_engine()
        if engine:
            engine.update_progress(achievement_id, value=value, trigger_type=trigger_type)
    except Exception:
        pass


class ModpackInstallWindow(ctk.CTkToplevel):
    """整合包（.mrpack）安装窗口 - 选择文件、确认信息、执行安装"""

    def __init__(self, parent, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self.callbacks = callbacks
        self._mrpack_path: Optional[str] = None
        self._mrpack_info: Optional[Dict[str, Any]] = None
        self._optional_var_map: Dict[str, ctk.BooleanVar] = {}

        # 窗口配置
        self.title(_("mp_install_title"))
        self.geometry("580x480")
        self.minsize(520, 420)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        self.grab_set()

        # 居中
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 580, 480
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # 保存原始 on_progress 用于安装时替换
        self._launcher_instance: Optional[Any] = None
        self._orig_on_progress: Optional[Callable] = None
        self.on_progress_original: Optional[Callable] = None

        self._build_ui()

    def _build_ui(self):
        """构建界面"""
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        # ── 标题 ──
        ctk.CTkLabel(
            main_frame,
            text=_("mp_install_header"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(pady=(0, 5))

        ctk.CTkLabel(
            main_frame,
            text=_("mp_install_subtitle"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(pady=(0, 15))

        # ── 文件选择区域 ──
        file_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        file_frame.pack(fill=ctk.X, pady=(0, 12))

        self._file_label = ctk.CTkLabel(
            file_frame,
            text=_("mp_no_file_selected"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
            wraplength=440,
        )
        self._file_label.pack(padx=15, pady=(12, 5), fill=ctk.X)

        btn_row = ctk.CTkFrame(file_frame, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=15, pady=(0, 12))

        ctk.CTkButton(
            btn_row,
            text=_("mp_select_file_btn"),
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._select_file,
        ).pack(side=ctk.LEFT, padx=(0, 8))

        ctk.CTkButton(
            btn_row,
            text=_("mp_download_modrinth"),
            height=34,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=self._open_modrinth_browser,
        ).pack(side=ctk.LEFT)

        # ── 整合包信息区域（初始隐藏）──
        self._info_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)

        self._info_name_label = ctk.CTkLabel(
            self._info_frame, text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"], anchor=ctk.W,
        )
        self._info_name_label.pack(padx=15, pady=(12, 2), fill=ctk.X)

        self._info_summary_label = ctk.CTkLabel(
            self._info_frame, text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"], anchor=ctk.W, wraplength=480,
        )
        self._info_summary_label.pack(padx=15, pady=(0, 2), fill=ctk.X)

        self._info_version_label = ctk.CTkLabel(
            self._info_frame, text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["success"], anchor=ctk.W,
        )
        self._info_version_label.pack(padx=15, pady=(0, 5), fill=ctk.X)

        # 可选文件
        self._optional_frame = ctk.CTkFrame(self._info_frame, fg_color="transparent")
        self._optional_frame.pack(padx=15, pady=(0, 12), fill=ctk.X)

        # ── 进度区域（初始隐藏）──
        self._progress_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)

        ctk.CTkLabel(
            self._progress_frame, text=_("mp_install_progress_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=15, pady=(12, 8), anchor=ctk.W)

        self._mp_progress_label = ctk.CTkLabel(
            self._progress_frame, text=_("mp_prog_mrpack_init"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"], anchor=ctk.W,
        )
        self._mp_progress_label.pack(padx=15, pady=(0, 2), fill=ctk.X)

        self._mc_progress_label = ctk.CTkLabel(
            self._progress_frame, text=_("mp_prog_vanilla_init"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"], anchor=ctk.W,
        )
        self._mc_progress_label.pack(padx=15, pady=(0, 8), fill=ctk.X)

        self._progress_status = ctk.CTkLabel(
            self._progress_frame, text=_("mp_installing"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        )
        self._progress_status.pack(padx=15, pady=(0, 5), fill=ctk.X)

        self._progress_bar = ctk.CTkProgressBar(
            self._progress_frame, height=12,
            fg_color=COLORS["bg_medium"], progress_color=COLORS["accent"],
        )
        self._progress_bar.pack(fill=ctk.X, padx=15, pady=(0, 12))
        self._progress_bar.set(0)

        # ── 底部按钮 ──
        self._bottom_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        self._bottom_frame.pack(fill=ctk.X, pady=(12, 0))

        self._install_btn = ctk.CTkButton(
            self._bottom_frame, text=_("modpack_start_install"),
            height=40, font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            state=ctk.DISABLED,
            command=self._on_install,
        )
        self._install_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        self._close_btn = ctk.CTkButton(
            self._bottom_frame, text=_("close"),
            height=40, width=80,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"], hover_color=COLORS["card_border"],
            command=self.destroy,
        )
        self._close_btn.pack(side=ctk.RIGHT, padx=(10, 0))

    # ─── 文件选择 ─────────────────────────────────────────────

    def _select_file(self):
        """选择 .mrpack 文件"""
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self,
            title=_("mp_select_dialog_title"),
            filetypes=[("Modrinth 整合包", "*.mrpack"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self._mrpack_path = path
        self._file_label.configure(
            text=os.path.basename(path),
            text_color=COLORS["text_primary"],
        )
        # 后台加载整合包信息
        self._install_btn.configure(state=ctk.DISABLED, text=_("mp_reading_info"))
        self._run_in_thread(self._load_mrpack_info)

    def _open_modrinth_browser(self):
        """打开 Modrinth 整合包浏览窗口"""
        from ui.windows.modpack_browser import ModpackBrowserWindow
        ModpackBrowserWindow(self, on_modpack_selected=self._on_modrinth_downloaded)

    def _on_modrinth_downloaded(self, mrpack_path: str):
        """Modrinth 整合包下载完成后的回调"""
        self._mrpack_path = mrpack_path
        self._file_label.configure(
            text=os.path.basename(mrpack_path),
            text_color=COLORS["text_primary"],
        )
        self._install_btn.configure(state=ctk.DISABLED, text=_("mp_reading_info"))
        self._run_in_thread(self._load_mrpack_info)

    def _load_mrpack_info(self):
        """读取整合包信息（后台线程）"""
        try:
            info = self.callbacks["get_mrpack_information"](self._mrpack_path)
            self._mrpack_info = info
            self.after(0, lambda: self._show_mrpack_info(info))
        except Exception as e:
            self.after(0, lambda: self._show_error(str(e)))

    def _show_mrpack_info(self, info: Dict[str, Any]):
        """在 UI 中显示整合包信息"""
        if not self.winfo_exists():
            return
        self._info_name_label.configure(text=info.get("name", _("mp_unknown_modpack")))
        summary = info.get("summary", "")
        if summary:
            self._info_summary_label.configure(text=summary)
        else:
            self._info_summary_label.pack_forget()

        mc_version = info.get("minecraftVersion", "未知")
        self._info_version_label.configure(text=_("mp_mc_version", version=mc_version))

        # 可选文件
        optional_files = info.get("optionalFiles", [])
        if optional_files:
            ctk.CTkLabel(
                self._optional_frame, text=_("mp_optional_components"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"], anchor=ctk.W,
            ).pack(fill=ctk.X, pady=(0, 3))

            self._optional_var_map.clear()
            for opt_name in optional_files:
                var = ctk.BooleanVar(value=False)
                self._optional_var_map[opt_name] = var
                ctk.CTkCheckBox(
                    self._optional_frame, text=opt_name, variable=var,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                    fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                    text_color=COLORS["text_primary"],
                ).pack(anchor=ctk.W, pady=1)

        self._info_frame.pack(fill=ctk.X, pady=(0, 5))
        self._install_btn.configure(state=ctk.NORMAL, text=_("modpack_start_install"))

    def _show_error(self, msg: str):
        """显示错误"""
        if not self.winfo_exists():
            return
        self._file_label.configure(text=_("mp_invalid_file"), text_color=COLORS["error"])
        self._install_btn.configure(state=ctk.DISABLED, text=_("modpack_start_install"))
        messagebox.showerror(_("error"), _("mp_read_error", error=msg), parent=self)

    # ─── 安装 ─────────────────────────────────────────────────

    def _on_install(self):
        """开始安装"""
        if not self._mrpack_path or not self._mrpack_info:
            return

        # 收集可选文件
        optional_files = [name for name, var in self._optional_var_map.items() if var.get()]

        # 切换到进度视图
        self._info_frame.pack_forget()
        self._progress_frame.pack(fill=ctk.X, pady=(0, 5))
        self._install_btn.configure(state=ctk.DISABLED, text=_("mp_installing"))
        self._close_btn.pack_forget()

        self._run_in_thread(self._do_install, optional_files)

    def _do_install(self, optional_files: list):
        """执行安装（后台线程）"""
        launcher = getattr(self.callbacks.get("install_mrpack"), "__self__", None)
        if launcher:
            self._launcher_instance = launcher
            self._orig_on_progress = getattr(launcher, "on_progress", None)

        self._polling = True

        def _poll_progress():
            if not self._polling or not self.winfo_exists():
                return
            try:
                launcher_inst = self._launcher_instance
                if launcher_inst and hasattr(launcher_inst, "_mp_progress"):
                    mp = launcher_inst._mp_progress
                    mp_data = mp.get("mrpack", {})
                    mc_data = mp.get("vanilla", {})
                    phase = mp.get("phase", "")

                    mp_pct = (mp_data.get("current", 0) / max(mp_data.get("max", 1), 1)) * 100
                    mc_pct = (mc_data.get("current", 0) / max(mc_data.get("max", 1), 1)) * 100

                    self._mp_progress_label.configure(
                        text=_("mp_prog_mrpack_label", pct=f"{mp_pct:.0f}",
                               label=mp_data.get('label', ''))
                    )
                    self._mc_progress_label.configure(
                        text=_("mp_prog_vanilla_label", pct=f"{mc_pct:.0f}",
                               label=mc_data.get('label', ''))
                    )

                    overall = mp.get("overall", 0)
                    self._progress_bar.set(overall)

                    if phase == "loader":
                        self._progress_status.configure(
                            text=_("mp_loader_status", name=mp.get('loader_label', ''))
                        )
                    elif phase == "done":
                        self._polling = False
                        return
                    else:
                        status_text = mp.get("status_text", _("mp_parallel_installing"))
                        self._progress_status.configure(text=status_text)
            except Exception:
                pass

            if self._polling:
                self.after(200, _poll_progress)

        self.after(0, _poll_progress)

        try:
            success, result = self.callbacks["install_mrpack"](
                self._mrpack_path,
                optional_files=optional_files,
            )
            self.after(0, lambda: self._on_install_done(success, result))
        except Exception as e:
            self.after(0, lambda: self._on_install_done(False, str(e)))
        finally:
            self._polling = False
            if launcher and self._orig_on_progress is not None:
                launcher.on_progress = self._orig_on_progress
            elif launcher:
                launcher.on_progress = None

    def _on_install_done(self, success: bool, result: str):
        """安装完成"""
        if not self.winfo_exists():
            return
        self._close_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        if success:
            self._progress_status.configure(text=_("mp_install_done"), text_color=COLORS["success"])
            self._install_btn.configure(
                text=_("mp_install_done_btn", result=result),
                fg_color=COLORS["success"],
                state=ctk.DISABLED,
            )
            show_notification("📦", _("notify_modpack_installed"), result, notify_type="success")
            _trigger_ach("modder_lazy")
            messagebox.showinfo(
                _("mp_install_done_title"),
                _("mp_install_done_msg", result=result),
                parent=self,
            )
            self.destroy()
        else:
            self._progress_status.configure(text=_("mp_install_failed_status"), text_color=COLORS["error"])
            self._install_btn.configure(text=_("mp_install_retry"), state=ctk.NORMAL)
            show_notification("📦", _("notify_modpack_failed"), str(result)[:50], notify_type="error")
            messagebox.showerror(_("mp_install_failed_status"), _("mp_install_error", error=result), parent=self)

    # ─── 工具 ─────────────────────────────────────────────────

    def _run_in_thread(self, func, *args):
        """在后台线程运行函数"""
        t = threading.Thread(target=func, args=args, daemon=True)
        t.start()
