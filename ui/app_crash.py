"""ModernApp 崩溃处理 Mixin - 崩溃诊断、AI 分析

业务逻辑已搬到 ``services/crash_service.py``（阶段 1 任务 1.7）：崩溃原因诊断、
日志尾部读取、AI 上下文组装、以及 AI 请求本身。本文件只剩纯界面部分
（崩溃对话框、隐私同意弹窗、AI 结果弹窗、加载窗口）与"起线程 + 把结果切回主
线程"的接线；下面每个方法都退化成对服务的薄委托，**方法名与签名保持不变**，
界面可见行为不变。
"""

import os
import re
import sys
import threading
import tkinter.messagebox as messagebox
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk

from services.crash_service import CRASH_TYPES as _CRASH_TYPES
from services.crash_service import CrashService
from services.errors import ServiceError
from ui.constants import COLORS, FONT_FAMILY


class CrashHandlerMixin(object):
    """崩溃处理 Mixin"""

    # ── 崩溃类型检测表（唯一对象在 services/crash_service.py）────────────

    CRASH_TYPES = _CRASH_TYPES

    # ── 服务层接线 ───────────────────────────────────────────────

    def _crash_service(self) -> CrashService:
        """惰性取得崩溃服务实例。

        优先用 ``AppContext`` 里注册的那个（阶段 2 接上之后），取不到就自己造一个
        并缓存下来 —— ``CrashService`` 不需要 ``AppContext``，构造期也只保存参数。
        """
        ctx = getattr(self, "context", None)
        if ctx is not None:
            getter = getattr(ctx, "try_get", None)
            if callable(getter):
                try:
                    service = getter(CrashService.name)
                except Exception:
                    service = None
                if service is not None:
                    return service
        service = getattr(self, "_crash_service_fallback", None)
        if service is None:
            service = CrashService()
            self._crash_service_fallback = service
        return service

    def _diagnose_crash(self, crash_files: dict) -> list:
        """根据崩溃日志内容分析崩溃类型，返回匹配到的崩溃类型列表（委托服务层）"""
        return self._crash_service().diagnose_crash(crash_files)

    def _show_crash_dialog(self, exit_code: int, crash_files: dict):
        """显示崩溃提示对话框"""
        import shutil
        import tkinter as tk
        import zipfile
        from datetime import datetime
        from tkinter import filedialog

        dialog = tk.Toplevel(self)
        dialog.title("游戏崩溃")
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        dialog.configure(bg="#1a1a2e")
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        # 诊断崩溃类型
        diagnoses = self._diagnose_crash(crash_files)

        # 窗口尺寸根据诊断结果动态调整
        w = 440
        h_base = 290
        h_diag = min(len(diagnoses), 3) * 62 if diagnoses else 0
        h = h_base + h_diag
        dialog.geometry(f"{w}x{h}")
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - w) // 2
        y = (dialog.winfo_screenheight() - h) // 2
        dialog.geometry(f"+{x}+{y}")

        pad = 24

        # 崩溃图标
        icon_path = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))), "icon.ico")
        icon_frame = tk.Frame(dialog, bg="#1a1a2e")
        icon_frame.place(x=pad, y=pad, width=64, height=64)
        if os.path.exists(icon_path):
            try:
                from PIL import Image as PILImage
                from PIL import ImageTk

                pil_img = PILImage.open(icon_path).resize((64, 64), PILImage.LANCZOS)
                tk_img = ImageTk.PhotoImage(pil_img)
                tk.Label(icon_frame, image=tk_img, bg="#1a1a2e").pack()
                # 保存对图像的引用以防止被垃圾回收
                setattr(dialog, "_icon_ref", tk_img)
            except Exception:
                tk.Label(icon_frame, text="\u26cf", font=(FONT_FAMILY, 28), fg="#e94560", bg="#1a1a2e").pack()
        else:
            tk.Label(icon_frame, text="\u26cf", font=(FONT_FAMILY, 28), fg="#e94560", bg="#1a1a2e").pack()

        # 崩溃信息
        has_crash_report = "crash_report" in crash_files
        has_game_log = "game_log" in crash_files
        has_jvm_crash = "jvm_crash_log" in crash_files

        info_text = f"游戏异常退出 (退出码: {exit_code})"
        tk.Label(dialog, text=info_text, font=(FONT_FAMILY, 14, "bold"), fg="#e94560", bg="#1a1a2e").place(
            x=pad + 72, y=pad + 5
        )

        detail_parts = []
        if has_crash_report:
            detail_parts.append("已检测到崩溃报告")
        if has_jvm_crash:
            detail_parts.append("JVM 崩溃日志可用")
        if has_game_log:
            detail_parts.append("游戏日志可用")
        detail = "；".join(detail_parts) if detail_parts else "未找到崩溃报告文件，仍可尝试导出"
        tk.Label(dialog, text=detail, font=(FONT_FAMILY, 10), fg="#8899aa", bg="#1a1a2e").place(x=pad + 72, y=pad + 38)

        # 分隔线
        tk.Frame(dialog, bg="#0f3460", height=1).place(x=pad, y=pad + 68, width=w - 2 * pad)

        # 诊断结果区域
        diag_y = pad + 78
        if diagnoses:
            for i, diag in enumerate(diagnoses):
                y = diag_y + i * 62
                # 背景框
                diag_frame = tk.Frame(dialog, bg="#16213e", highlightbackground="#0f3460", highlightthickness=1)
                diag_frame.place(x=pad, y=y, width=w - 2 * pad, height=56)
                # 标题行
                tk.Label(
                    diag_frame,
                    text=f"{diag['icon']} {diag['name']}",
                    font=(FONT_FAMILY, 10, "bold"),
                    fg="#e94560",
                    bg="#16213e",
                ).pack(anchor="w", padx=10, pady=(6, 0))
                # 建议（单行截断）
                advice_text = diag["advice"]
                if len(advice_text) > 48:
                    advice_text = advice_text[:47] + "…"
                tk.Label(diag_frame, text=f"💡 {advice_text}", font=(FONT_FAMILY, 9), fg="#8899aa", bg="#16213e").pack(
                    anchor="w", padx=10, pady=(2, 0)
                )
        btn_y = diag_y + h_diag + 8
        btn_h = 38

        def _open_crash_report():
            path = crash_files.get("crash_report")
            if path and os.path.exists(path):
                os.startfile(path)

        def _open_game_log():
            path = crash_files.get("game_log")
            if path and os.path.exists(path):
                os.startfile(path)
            else:
                mc_dir = None
                try:
                    if "get_minecraft_dir" in self.callbacks:
                        mc_dir = Path(self.callbacks["get_minecraft_dir"]())
                    if mc_dir:
                        log_dir = mc_dir / "logs"
                        if log_dir.exists():
                            os.startfile(str(log_dir))
                            return
                except Exception:
                    pass
                messagebox.showinfo("提示", "未找到游戏日志文件", parent=dialog)
            self._trigger_ach("advanced_log_hunter")

        def _export_crash_report():
            filetypes = [("ZIP 压缩包", "*.zip")]
            default_name = f"FMCL-crash-{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            save_path = filedialog.asksaveasfilename(
                parent=dialog,
                title="导出崩溃报告",
                defaultextension=".zip",
                initialfile=default_name,
                filetypes=filetypes,
            )
            if not save_path:
                return
            try:
                with zipfile.ZipFile(save_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    # 崩溃报告文件
                    if "crash_report" in crash_files:
                        p = crash_files["crash_report"]
                        if os.path.exists(p):
                            zf.write(p, f"crash-reports/{os.path.basename(p)}")
                    if "crash_report_list" in crash_files:
                        for p in crash_files["crash_report_list"]:
                            if os.path.exists(p):
                                zf.write(p, f"crash-reports/{os.path.basename(p)}")

                    # 游戏日志
                    for key, arcname in [
                        ("game_log", "logs/latest.log"),
                        ("debug_log", "logs/debug.log"),
                        ("jvm_crash_log", "hs_err_pid.log"),
                    ]:
                        p = crash_files.get(key)
                        if p and os.path.exists(p):
                            zf.write(p, arcname)

                    # 启动器日志（从内存缓冲区或磁盘文件获取）
                    launcher_log_content = ""
                    # 优先从内存缓冲区获取
                    if hasattr(self, "_log_buffer") and self._log_buffer:
                        launcher_log_content = self._log_buffer.getvalue()
                    if not launcher_log_content.strip():
                        # 使用配置中的日志路径
                        try:
                            from config import config as _cfg

                            disk_log = _cfg.log_file
                        except Exception:
                            # 回退到基于平台的默认路径
                            import platform as _platform

                            system = _platform.system().lower()
                            if system == "linux":
                                disk_log = Path.home() / ".local" / "share" / "fmcl" / "fmcl.log"
                            else:
                                base_dir = crash_files.get("_mc_dir")
                                if base_dir:
                                    disk_log = Path(base_dir) / "latest.log"
                                else:
                                    disk_log = None

                        if disk_log and disk_log.exists():
                            try:
                                # Windows 下 logzero 可能使用 GBK 编码
                                for enc in ("utf-8", "gbk", "latin-1"):
                                    try:
                                        launcher_log_content = disk_log.read_text(enc)
                                        break
                                    except (UnicodeDecodeError, UnicodeError):
                                        continue
                            except Exception:
                                pass
                    if launcher_log_content.strip():
                        zf.writestr("launcher.log", launcher_log_content.encode("utf-8", errors="replace"))

                    # 系统信息摘要
                    import platform as _platform

                    sys_info = (
                        f"FMCL Crash Report\n"
                        f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"Exit Code: {exit_code}\n"
                        f"OS: {_platform.system()} {_platform.release()}\n"
                        f"Python: {_platform.python_version()}\n"
                        f"Architecture: {_platform.machine()}\n"
                    )
                    zf.writestr("system-info.txt", sys_info)

                messagebox.showinfo("导出成功", f"崩溃报告已保存至:\n{save_path}", parent=dialog)
            except Exception as e:
                messagebox.showerror("导出失败", f"导出崩溃报告时出错:\n{e}", parent=dialog)

        # 按钮样式参数
        btn_style = dict(
            font=(FONT_FAMILY, 10),
            relief="flat",
            cursor="hand2",
            bg="#0f3460",
            fg="white",
            activebackground="#2d3a5c",
            activeforeground="white",
            bd=0,
            highlightthickness=0,
        )

        from ui.i18n import _

        btn1 = tk.Button(
            dialog,
            text=f"📄 {_('crash_report')}",
            command=_open_crash_report,
            state="normal" if has_crash_report else "disabled",
            **btn_style,
        )
        btn1.place(x=pad, y=btn_y, width=w - 2 * pad, height=btn_h)

        btn2 = tk.Button(
            dialog,
            text=f"📋 {_('crash_game_log')}",
            command=_open_game_log,
            state="normal" if has_game_log else "normal",
            **btn_style,
        )
        btn2.place(x=pad, y=btn_y + btn_h + 8, width=w - 2 * pad, height=btn_h)

        btn3 = tk.Button(
            dialog,
            text=f"📦 {_('crash_export')}",
            command=_export_crash_report,
            bg="#e94560",
            fg="white",
            activebackground="#ff6b81",
            activeforeground="white",
            font=(FONT_FAMILY, 10, "bold"),
            relief="flat",
            cursor="hand2",
            bd=0,
            highlightthickness=0,
        )
        btn3.place(x=pad, y=btn_y + (btn_h + 8) * 2, width=w - 2 * pad, height=btn_h)

        # 上传分享日志按钮
        def _share_game_log():
            game_log_path = crash_files.get("game_log")
            if not game_log_path or not os.path.exists(game_log_path):
                messagebox.showinfo(_("crash_title"), _("crash_share_no_log"), parent=dialog)
                return

            share_btn.configure(state="disabled", text=_("crash_share_uploading") + "...")
            dialog.update_idletasks()

            def _do_upload():
                from api.logshare import LogShareError, upload_game_log

                try:
                    url = upload_game_log(game_log_path)
                    if url:
                        dialog.clipboard_clear()
                        dialog.clipboard_append(url)
                        dialog.after(
                            0,
                            lambda: messagebox.showinfo(
                                _("crash_title"), f"{_('crash_share_success')}\n\n{url}", parent=dialog
                            ),
                        )
                    else:
                        dialog.after(
                            0, lambda: messagebox.showerror(_("crash_title"), _("crash_share_no_log"), parent=dialog)
                        )
                except LogShareError as e:
                    dialog.after(
                        0,
                        lambda err=str(e): messagebox.showerror(
                            _("crash_title"), _("crash_share_failed", error=err), parent=dialog
                        ),
                    )
                except Exception as e:
                    dialog.after(
                        0,
                        lambda err=str(e): messagebox.showerror(
                            _("crash_title"), _("crash_share_failed", error=err), parent=dialog
                        ),
                    )
                finally:
                    dialog.after(
                        0,
                        lambda: share_btn.configure(
                            state="normal" if game_log_path and os.path.exists(game_log_path) else "disabled",
                            text=_("crash_share_log"),
                        ),
                    )

            threading.Thread(target=_do_upload, daemon=True).start()

        share_btn = tk.Button(
            dialog,
            text=_("crash_share_log"),
            command=_share_game_log,
            bg="#0f3460",
            fg="white",
            activebackground="#2d3a5c",
            activeforeground="white",
            font=(FONT_FAMILY, 10),
            relief="flat",
            cursor="hand2",
            bd=0,
            highlightthickness=0,
            state="normal" if has_game_log else "disabled",
        )
        share_btn.place(x=pad, y=btn_y + (btn_h + 8) * 3, width=w - 2 * pad, height=btn_h)

        # AI 分析按钮
        _jdz_token = self.callbacks.get("get_jdz_token", lambda: None)() if self.callbacks else None

        ai_btn = tk.Button(
            dialog,
            text=_("crash_ai_analyze"),
            command=lambda: self._ai_analyze_crash(crash_files, exit_code),
            bg="#6c5ce7",
            fg="white",
            activebackground="#a29bfe",
            activeforeground="white",
            font=(FONT_FAMILY, 10, "bold"),
            relief="flat",
            cursor="hand2",
            bd=0,
            highlightthickness=0,
            state="normal" if _jdz_token else "disabled",
        )
        ai_btn.place(x=pad, y=btn_y + (btn_h + 8) * 4, width=w - 2 * pad, height=btn_h)

        if not _jdz_token:
            tk.Label(dialog, text="请先在设置中登录净读账号", font=(FONT_FAMILY, 8), fg="#667788", bg="#1a1a2e").place(
                x=pad, y=btn_y + (btn_h + 8) * 4 + btn_h + 2
            )

        # 调整窗口高度以容纳新按钮
        h += (btn_h + 8) + (btn_h + 8) + (12 if not _jdz_token else 0)
        dialog.geometry(f"{w}x{h}")
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - w) // 2
        y = (dialog.winfo_screenheight() - h) // 2
        dialog.geometry(f"+{x}+{y}")

        # 关闭按钮
        close_btn = tk.Button(
            dialog,
            text="关闭",
            command=dialog.destroy,
            font=(FONT_FAMILY, 9),
            relief="flat",
            cursor="hand2",
            bg="#1a1a2e",
            fg="#667788",
            activebackground="#1a1a2e",
            activeforeground="#aabbcc",
            bd=0,
        )
        close_btn.place(x=w // 2 - 20, y=h - 36, width=40)

    def _read_file_tail(self, filepath: str, lines: int = 200) -> str:
        """读取文件最后 lines 行（委托服务层）"""
        return self._crash_service().read_file_tail(filepath, lines)

    def _collect_ai_context(self, crash_files: dict, exit_code: int) -> str:
        """收集发送给 AI 的崩溃上下文信息（委托服务层）"""
        return self._crash_service().collect_ai_context(
            crash_files, exit_code, log_buffer=getattr(self, "_log_buffer", None)
        )

    def _ai_analyze_server_crash(self, exit_code: int):
        """AI 分析服务器崩溃（后台线程请求，主线程弹窗）"""
        token = self.callbacks.get("get_jdz_token", lambda: None)() if self.callbacks else None
        if not token:
            messagebox.showwarning("提示", "请先在设置中登录净读账号", parent=self)
            return

        # 收集服务器日志上下文
        context = self._collect_server_ai_context(exit_code)
        if not context.strip():
            messagebox.showwarning("提示", "未找到可用于分析的服务器日志信息", parent=self)
            return

        # 检查隐私同意
        from config import config

        if not config.ai_privacy_consent:
            self._show_privacy_consent_dialog(lambda: self._do_server_ai_analyze(context, exit_code, token))
            return

        self._do_server_ai_analyze(context, exit_code, token)

    def _collect_server_ai_context(self, exit_code: int) -> str:
        """收集发送给 AI 的服务器崩溃上下文（委托服务层）"""
        return self._crash_service().collect_server_ai_context(
            exit_code,
            version_id=getattr(self, "selected_server_version", "") or "",
            server_log_lines=getattr(self, "_server_log_lines", []),
            log_buffer=getattr(self, "_log_buffer", None),
        )

    def _do_server_ai_analyze(self, context: str, exit_code: int, token: str):
        """执行服务器 AI 分析（已通过隐私检查）"""
        # 显示加载窗口
        import tkinter as tk

        loading = tk.Toplevel(self)
        loading.title("AI 分析中...")
        loading.geometry("320x100")
        loading.resizable(False, False)
        loading.attributes("-topmost", True)
        loading.configure(bg="#1a1a2e")
        loading.transient(self)
        try:
            loading.grab_set()
        except Exception:
            pass
        loading.update_idletasks()
        lx = (loading.winfo_screenwidth() - 320) // 2
        ly = (loading.winfo_screenheight() - 100) // 2
        loading.geometry(f"+{lx}+{ly}")

        tk.Label(loading, text="🤖 AI 正在分析服务器日志...", font=(FONT_FAMILY, 12), fg="#a0a0b0", bg="#1a1a2e").pack(
            pady=(20, 5)
        )
        tk.Label(loading, text="请稍候，这可能需要几秒钟", font=(FONT_FAMILY, 9), fg="#667788", bg="#1a1a2e").pack()

        def _do_analyze():
            try:
                ai_content = self._crash_service().analyze_server_crash(context, exit_code, token)
            except ServiceError as e:
                _err_msg = e.message
                self.after(0, lambda msg=_err_msg: _show_error(msg))
            except Exception as e:
                _err_msg = str(e)
                self.after(0, lambda msg=_err_msg: _show_error(msg))
            else:
                self.after(0, lambda content=ai_content: _show_result(content))
            finally:
                self.after(0, loading.destroy)

        def _show_result(content: str):
            self._show_ai_result_dialog(content, title="AI 服务器崩溃分析结果")

        def _show_error(msg: str):
            messagebox.showerror("AI 分析失败", f"分析请求失败:\n{msg}", parent=self)

        threading.Thread(target=_do_analyze, daemon=True).start()

    def _show_privacy_consent_dialog(self, on_accept):
        """显示 AI 分析隐私同意弹窗，同意后调用 on_accept 回调"""
        import tkinter as tk

        from ui.i18n import _

        dialog = tk.Toplevel(self)
        dialog.title(_("ai_privacy_title"))
        dialog.resizable(False, False)
        dialog.attributes("-topmost", True)
        dialog.configure(bg="#1a1a2e")
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        w, h = 440, 340
        dialog.geometry(f"{w}x{h}")
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - w) // 2
        y = (dialog.winfo_screenheight() - h) // 2
        dialog.geometry(f"+{x}+{y}")

        pad = 24

        # 标题
        tk.Label(dialog, text=_("ai_privacy_title"), font=(FONT_FAMILY, 14, "bold"), fg="#e94560", bg="#1a1a2e").place(
            x=pad, y=pad
        )

        # 隐私说明内容
        content_frame = tk.Frame(dialog, bg="#16213e", highlightbackground="#0f3460", highlightthickness=1)
        content_frame.place(x=pad, y=pad + 36, width=w - 2 * pad, height=160)

        content_text = _("ai_privacy_content")
        content_label = tk.Label(
            content_frame,
            text=content_text,
            font=(FONT_FAMILY, 10),
            fg="#a0a0b0",
            bg="#16213e",
            wraplength=w - 2 * pad - 20,
            justify="left",
            anchor="nw",
        )
        content_label.pack(padx=10, pady=10, fill="both", expand=True)

        # 同意复选框
        consent_var = tk.BooleanVar(value=False)
        consent_cb = tk.Checkbutton(
            dialog,
            text=_("ai_privacy_agreement"),
            variable=consent_var,
            font=(FONT_FAMILY, 10),
            fg="#a0a0b0",
            bg="#1a1a2e",
            selectcolor="#16213e",
            activebackground="#1a1a2e",
            activeforeground="#ffffff",
            wraplength=w - 2 * pad - 30,
            justify="left",
        )
        consent_cb.place(x=pad, y=pad + 210)

        # 按钮区
        def _on_confirm():
            if consent_var.get():
                # 保存同意状态
                from config import config

                config.ai_privacy_consent = True
                config.save_config()
                dialog.destroy()
                on_accept()
            else:
                consent_cb.configure(fg="#e94560")
                dialog.after(1500, lambda: consent_cb.configure(fg="#a0a0b0"))

        confirm_btn = tk.Button(
            dialog,
            text=_("ai_privacy_accept"),
            command=_on_confirm,
            font=(FONT_FAMILY, 10, "bold"),
            relief="flat",
            cursor="hand2",
            bg="#6c5ce7",
            fg="white",
            activebackground="#a29bfe",
            activeforeground="white",
            bd=0,
        )
        confirm_btn.place(x=pad, y=h - 52, width=(w - 2 * pad) // 2 - 4, height=36)

        cancel_btn = tk.Button(
            dialog,
            text=_("confirm"),
            command=dialog.destroy,
            font=(FONT_FAMILY, 10),
            relief="flat",
            cursor="hand2",
            bg="#0f3460",
            fg="white",
            activebackground="#2d3a5c",
            activeforeground="white",
            bd=0,
        )
        cancel_btn.place(x=pad + (w - 2 * pad) // 2 + 4, y=h - 52, width=(w - 2 * pad) // 2 - 4, height=36)

    def _ai_analyze_crash(self, crash_files: dict, exit_code: int):
        """AI 分析崩溃（后台线程请求，主线程弹窗）"""
        token = self.callbacks.get("get_jdz_token", lambda: None)() if self.callbacks else None
        if not token:
            messagebox.showwarning("提示", "请先在设置中登录净读账号", parent=self)
            return

        # 检查隐私同意
        from config import config

        if not config.ai_privacy_consent:
            self._show_privacy_consent_dialog(lambda: self._do_ai_analyze(crash_files, exit_code, token))
            return

        self._do_ai_analyze(crash_files, exit_code, token)

    def _do_ai_analyze(self, crash_files: dict, exit_code: int, token: str = None):
        """执行 AI 分析（已通过隐私检查）"""
        if token is None:
            token = self.callbacks.get("get_jdz_token", lambda: None)() if self.callbacks else None

        # 收集上下文
        context = self._collect_ai_context(crash_files, exit_code)
        if not context.strip():
            messagebox.showwarning("提示", "未找到可用于分析的日志信息", parent=self)
            return

        # 构建请求消息
        system_prompt = (
            "你是一个 Minecraft 崩溃日志分析专家。根据用户提供的崩溃报告、游戏日志和系统信息，"
            "分析崩溃原因并给出具体、可操作的建议。\n"
            "请用中文回复，格式如下：\n"
            "## 崩溃原因分析\n（简明扼要地说明崩溃原因）\n\n"
            "## 建议操作\n（列出具体的解决步骤，每步用数字编号）"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请分析以下 Minecraft 崩溃信息：\n\n{context}"},
        ]

        # 显示加载窗口
        import tkinter as tk

        loading = tk.Toplevel(self)
        loading.title("AI 分析中...")
        loading.geometry("320x100")
        loading.resizable(False, False)
        loading.attributes("-topmost", True)
        loading.configure(bg="#1a1a2e")
        loading.transient(self)
        try:
            loading.grab_set()
        except Exception:
            pass
        loading.update_idletasks()
        lx = (loading.winfo_screenwidth() - 320) // 2
        ly = (loading.winfo_screenheight() - 100) // 2
        loading.geometry(f"+{lx}+{ly}")

        tk.Label(loading, text="🤖 AI 正在分析崩溃原因...", font=(FONT_FAMILY, 12), fg="#a0a0b0", bg="#1a1a2e").pack(
            pady=(20, 5)
        )
        tk.Label(loading, text="请稍候，这可能需要几秒钟", font=(FONT_FAMILY, 9), fg="#667788", bg="#1a1a2e").pack()

        def _do_analyze():
            try:
                ai_content = self._crash_service().analyze_crash(context, exit_code, token)
            except ServiceError as e:
                _err_msg = e.message
                self.after(0, lambda msg=_err_msg: _show_error(msg))
            except Exception as e:
                _err_msg = str(e)
                self.after(0, lambda msg=_err_msg: _show_error(msg))
            else:
                self.after(0, lambda content=ai_content: _show_result(content))
            finally:
                self.after(0, loading.destroy)

        def _show_result(content: str):
            self._show_ai_result_dialog(content)

        def _show_error(msg: str):
            messagebox.showerror("AI 分析失败", f"分析请求失败:\n{msg}", parent=self)

        threading.Thread(target=_do_analyze, daemon=True).start()

    def _show_ai_result_dialog(self, content: str, title: str = "AI 崩溃分析结果"):
        """显示 AI 分析结果弹窗，支持保存为 txt"""
        import tkinter as tk
        from datetime import datetime
        from tkinter import filedialog

        result = tk.Toplevel(self)
        result.title(title)
        result.geometry("580x640")
        result.resizable(True, True)
        result.attributes("-topmost", True)
        result.configure(bg="#1a1a2e")
        result.transient(self)
        try:
            result.grab_set()
        except Exception:
            pass
        result.update_idletasks()
        rx = (result.winfo_screenwidth() - 580) // 2
        ry = (result.winfo_screenheight() - 640) // 2
        result.geometry(f"+{rx}+{ry}")

        # 标题
        tk.Label(result, text=f"🤖 {title}", font=(FONT_FAMILY, 14, "bold"), fg="#ffffff", bg="#1a1a2e").pack(
            anchor="w", padx=16, pady=(16, 8)
        )

        # 内容区域（可滚动文本框）
        text_frame = tk.Frame(result, bg="#16213e", highlightbackground="#2d3a5c", highlightthickness=1)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))

        text_widget = tk.Text(
            text_frame,
            wrap=tk.WORD,
            font=(FONT_FAMILY, 11),
            fg="#ffffff",
            bg="#16213e",
            bd=0,
            padx=12,
            pady=12,
            insertbackground="white",
            selectbackground="#0f3460",
            relief="flat",
        )
        scrollbar = tk.Scrollbar(
            text_frame, command=text_widget.yview, bg="#1a1a2e", troughcolor="#16213e", activebackground="#0f3460"
        )
        text_widget.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text_widget.pack(fill=ctk.BOTH, expand=True)
        text_widget.insert("1.0", content)
        text_widget.configure(state="disabled")

        # 按钮区
        btn_frame = tk.Frame(result, bg="#1a1a2e")
        btn_frame.pack(fill=tk.X, padx=16, pady=(0, 16))

        def _save_as_txt():
            default_name = f"FMCL-AI分析-{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            save_path = filedialog.asksaveasfilename(
                parent=result,
                title="保存分析结果",
                defaultextension=".txt",
                initialfile=default_name,
                filetypes=[("文本文件", "*.txt")],
            )
            if save_path:
                try:
                    with open(save_path, "w", encoding="utf-8") as f:
                        f.write(
                            f"FMCL AI 崩溃分析结果\n"
                            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"{'=' * 50}\n\n"
                            f"{content}\n"
                        )
                    messagebox.showinfo("保存成功", f"分析结果已保存至:\n{save_path}", parent=result)
                except Exception as e:
                    messagebox.showerror("保存失败", f"保存时出错:\n{e}", parent=result)

        tk.Button(
            btn_frame,
            text="💾 保存为 TXT",
            command=_save_as_txt,
            font=(FONT_FAMILY, 10),
            relief="flat",
            cursor="hand2",
            bg="#0f3460",
            fg="white",
            activebackground="#2d3a5c",
            activeforeground="white",
            bd=0,
        ).pack(side=ctk.LEFT)

        tk.Button(
            btn_frame,
            text="关闭",
            command=result.destroy,
            font=(FONT_FAMILY, 10),
            relief="flat",
            cursor="hand2",
            bg="#e94560",
            fg="white",
            activebackground="#ff6b81",
            activeforeground="white",
            bd=0,
            width=80,
        ).pack(side=ctk.RIGHT)
