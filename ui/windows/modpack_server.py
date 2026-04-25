"""整合包开服窗口 - 选择 .mrpack 文件，安装为服务器"""
import os
import threading
import tkinter.messagebox as messagebox
from typing import List, Dict, Optional, Callable, Any

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY


class ModpackServerWindow(ctk.CTkToplevel):
    """整合包开服窗口 - 选择 .mrpack 文件，安装为服务器"""

    def __init__(self, parent, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self.callbacks = callbacks
        self.parent_app = parent
        self._mrpack_path: Optional[str] = None
        self._mrpack_info: Optional[Dict[str, Any]] = None
        self._optional_var_map: Dict[str, ctk.BooleanVar] = {}

        self.title("📦 整合包开服")
        self.geometry("580x820")
        self.minsize(520, 740)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)
        self.grab_set()

        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 580, 820
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # 保存原始 on_progress 用于安装时替换
        self._launcher_instance: Optional[Any] = None
        self._orig_on_progress: Optional[Callable] = None
        self.on_progress_original: Optional[Callable] = None

        self._build_ui()

    def _build_ui(self):
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        # 标题
        ctk.CTkLabel(
            main_frame,
            text="📦 整合包开服",
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(pady=(0, 5))

        ctk.CTkLabel(
            main_frame,
            text="安装 .mrpack 整合包作为服务器，自动下载模组、配置和服务器核心",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(pady=(0, 15))

        # 文件选择区域
        file_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        file_frame.pack(fill=ctk.X, pady=(0, 12))

        self._file_label = ctk.CTkLabel(
            file_frame, text="未选择文件",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W, wraplength=440,
        )
        self._file_label.pack(padx=15, pady=(12, 5), fill=ctk.X)

        btn_row = ctk.CTkFrame(file_frame, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=15, pady=(0, 12))

        ctk.CTkButton(
            btn_row, text="📂 选择 .mrpack 文件",
            height=34, font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._select_file,
        ).pack(side=ctk.LEFT)

        # 整合包信息区域（初始隐藏）
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
        self._optional_frame.pack(padx=15, pady=(0, 5), fill=ctk.X)

        ctk.CTkFrame(self._info_frame, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(5, 0)
        )

        # 服务器名称设置
        name_row = ctk.CTkFrame(self._info_frame, fg_color="transparent")
        name_row.pack(fill=ctk.X, padx=15, pady=(8, 5))

        ctk.CTkLabel(
            name_row, text="服务器名称:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT)

        self._server_name_entry = ctk.CTkEntry(
            name_row, height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"], border_color=COLORS["card_border"],
            placeholder_text="留空则自动命名",
        )
        self._server_name_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(8, 0))

        # 提示
        ctk.CTkLabel(
            self._info_frame,
            text="✅ 自动检测并安装服务端 mod loader（Forge/Fabric/NeoForge/Quilt）",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"], anchor=ctk.W, wraplength=480,
        ).pack(padx=15, pady=(0, 12), fill=ctk.X)

        # 进度区域（初始隐藏）
        self._progress_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)

        self._progress_status = ctk.CTkLabel(
            self._progress_frame, text="正在安装...",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        )
        self._progress_status.pack(padx=15, pady=(12, 5), fill=ctk.X)

        self._progress_bar = ctk.CTkProgressBar(
            self._progress_frame, height=10,
            fg_color=COLORS["bg_medium"], progress_color=COLORS["accent"],
        )
        self._progress_bar.pack(fill=ctk.X, padx=15, pady=(0, 12))
        self._progress_bar.set(0)

        # 安装日志区域（初始隐藏）
        self._log_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)

        log_header = ctk.CTkFrame(self._log_frame, fg_color="transparent")
        log_header.pack(fill=ctk.X, padx=15, pady=(10, 5))

        ctk.CTkLabel(
            log_header, text="📋 安装日志",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_secondary"], anchor=ctk.W,
        ).pack(side=ctk.LEFT)

        self._log_text = ctk.CTkTextbox(
            self._log_frame, height=200,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COLORS["bg_dark"], border_color=COLORS["card_border"],
            text_color=COLORS["text_secondary"],
            wrap=ctk.WORD, state=ctk.DISABLED,
        )
        self._log_text.pack(fill=ctk.BOTH, expand=True, padx=15, pady=(0, 12))

        # 底部按钮
        self._bottom_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        self._bottom_frame.pack(fill=ctk.X, pady=(12, 0))

        self._install_btn = ctk.CTkButton(
            self._bottom_frame, text="🚀 开始安装服务器",
            height=40, font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            state=ctk.DISABLED,
            command=self._on_install,
        )
        self._install_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        self._close_btn = ctk.CTkButton(
            self._bottom_frame, text="关闭",
            height=40, width=80,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"], hover_color=COLORS["card_border"],
            command=self.destroy,
        )
        self._close_btn.pack(side=ctk.RIGHT, padx=(10, 0))

    def _select_file(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(
            parent=self,
            title="选择 Modrinth 整合包",
            filetypes=[("Modrinth 整合包", "*.mrpack"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self._mrpack_path = path
        self._file_label.configure(text=os.path.basename(path), text_color=COLORS["text_primary"])
        self._install_btn.configure(state=ctk.DISABLED, text="读取中...")
        self._run_in_thread(self._load_mrpack_info)

    def _load_mrpack_info(self):
        try:
            info = self.callbacks["get_mrpack_information"](self._mrpack_path)
            self._mrpack_info = info
            self.after(0, lambda: self._show_mrpack_info(info))
        except Exception as e:
            self.after(0, lambda: self._show_error(str(e)))

    def _show_mrpack_info(self, info: Dict[str, Any]):
        self._info_name_label.configure(text=info.get("name", "未知整合包"))
        summary = info.get("summary", "")
        if summary:
            self._info_summary_label.configure(text=summary)
        else:
            self._info_summary_label.pack_forget()

        mc_version = info.get("minecraftVersion", "未知")
        self._info_version_label.configure(text=f"Minecraft {mc_version}")

        # 可选文件
        optional_files = info.get("optionalFiles", [])
        if optional_files:
            ctk.CTkLabel(
                self._optional_frame, text="可选组件:",
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
        self._install_btn.configure(state=ctk.NORMAL, text="🚀 开始安装服务器")

    def _show_error(self, msg: str):
        self._file_label.configure(text="文件无效", text_color=COLORS["error"])
        self._install_btn.configure(state=ctk.DISABLED, text="🚀 开始安装服务器")
        messagebox.showerror("错误", f"无法读取整合包:\n{msg}", parent=self)

    def _on_install(self):
        if not self._mrpack_path or not self._mrpack_info:
            return

        optional_files = [name for name, var in self._optional_var_map.items() if var.get()]
        custom_name = self._server_name_entry.get().strip() or None

        self._info_frame.pack_forget()
        self._progress_frame.pack(fill=ctk.X, pady=(0, 5))
        self._log_frame.pack(fill=ctk.BOTH, expand=True, pady=(5, 0))
        self._install_btn.configure(state=ctk.DISABLED, text="安装中...")
        self._close_btn.pack_forget()

        # 清空日志
        self._log_text.configure(state=ctk.NORMAL)
        self._log_text.delete("0.0", ctk.END)
        self._log_text.configure(state=ctk.DISABLED)

        self._run_in_thread(self._do_install, optional_files, custom_name)

    def _do_install(self, optional_files: list, server_name: Optional[str]):
        def _log_append(text: str):
            def _ui_append():
                self._log_text.configure(state=ctk.NORMAL)
                self._log_text.insert(ctk.END, text + "\n")
                self._log_text.see(ctk.END)
                self._log_text.configure(state=ctk.DISABLED)
            self.after(0, _ui_append)

        def _hook_progress(current, total, status):
            if status:
                _log_append(status)
            if self.on_progress_original and total > 0 and current > 0:
                self.on_progress_original(current, total, "")

        # 获取 launcher 实例并替换 on_progress
        launcher = getattr(self.callbacks.get("install_mrpack_server"), "__self__", None)
        if launcher:
            self._launcher_instance = launcher
            self._orig_on_progress = getattr(launcher, "on_progress", None)
            launcher.on_progress = _hook_progress

        try:
            success, result = self.callbacks["install_mrpack_server"](
                self._mrpack_path,
                optional_files=optional_files,
                server_name=server_name,
            )
            self.after(0, lambda: self._on_install_done(success, result))
        except Exception as e:
            self.after(0, lambda: self._on_install_done(False, str(e)))
        finally:
            if launcher and self._orig_on_progress is not None:
                launcher.on_progress = self._orig_on_progress
            elif launcher:
                launcher.on_progress = None

    def _on_install_done(self, success: bool, result: str):
        self._close_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        if success:
            self._progress_status.configure(text="安装完成!", text_color=COLORS["success"])
            self._install_btn.configure(
                text=f"✅ 安装完成 - {result}",
                fg_color=COLORS["success"],
                state=ctk.DISABLED,
            )

            def _append_done_log():
                self._log_text.configure(state=ctk.NORMAL)
                self._log_text.insert(ctk.END, f"\n✅ 服务器安装完成! 名称: {result}\n")
                self._log_text.see(ctk.END)
                self._log_text.configure(state=ctk.DISABLED)
            self.after(0, _append_done_log)

            messagebox.showinfo(
                "安装完成",
                f"整合包服务器安装成功！\n\n"
                f"服务器名称: {result}\n"
                f"目录: .minecraft/server/{result}/\n\n"
                f"请刷新服务器列表后选择该版本启动。",
                parent=self,
            )
            # 刷新父窗口的服务器列表
            self._refresh_parent_server_list()
            self.destroy()
        else:
            self._progress_status.configure(text="安装失败", text_color=COLORS["error"])
            self._install_btn.configure(text="🚀 重新安装", state=ctk.NORMAL)

            def _append_err_log():
                self._log_text.configure(state=ctk.NORMAL)
                self._log_text.insert(ctk.END, f"\n❌ 安装失败: {result}\n")
                self._log_text.see(ctk.END)
                self._log_text.configure(state=ctk.DISABLED)
            self.after(0, _append_err_log)

            messagebox.showerror("安装失败", f"整合包服务器安装失败:\n{result}", parent=self)

    def _refresh_parent_server_list(self):
        """刷新父窗口的服务器列表"""
        try:
            if "get_installed_servers" in self.callbacks:
                installed = self.callbacks["get_installed_servers"]()
                if hasattr(self.parent_app, "_render_server_versions"):
                    self.parent_app._render_server_versions(installed)
        except Exception:
            pass

    def _run_in_thread(self, func, *args):
        t = threading.Thread(target=func, args=args, daemon=True)
        t.start()
