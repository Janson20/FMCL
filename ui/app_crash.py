"""ModernApp 崩溃处理 Mixin - 崩溃诊断、AI 分析"""
import os
import re
import sys
import threading
import platform
import tkinter.messagebox as messagebox
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY


class CrashHandlerMixin(object):
    """崩溃处理 Mixin"""

    # ── 崩溃类型检测 ──────────────────────────────────────────────

    CRASH_TYPES = [
        {
            "name": "Mixin 错误",
            "icon": "\U0001f9ec",
            "required": ["org.spongepowered.asm.mixin"],
            "optional": ["mixin apply for mod", ".mixins.json"],
            "cause": "优化类模组（如 Sodium、OptiFine）在修改游戏底层代码时注入失败，可能因目标代码不存在、签名不匹配或版本错误。",
            "advice": "检查崩溃报告中 .mixins.json 前的模组名，更新或移除该模组。",
        },
        {
            "name": "模组加载异常",
            "icon": "\u26a0\ufe0f",
            "required": ["Mod Loading has failed", "net.minecraftforge.fml.LoadingFailedException"],
            "optional": ["Could not execute entrypoint stage"],
            "cause": "某个模组初始化失败（配置文件错误、注册表溢出等）。",
            "advice": "查看崩溃报告中 Suspected Mod 字段或 Mod List 中标记为 E 的模组，更新或移除该模组。",
        },
        {
            "name": "依赖缺失/版本错误",
            "icon": "\U0001f517",
            "required": ["ClassNotFoundException", "NoClassDefFoundError", "NoSuchMethodError", "NoSuchFieldError"],
            "optional": ["Missing mod", "Requires", "depends"],
            "cause": "缺少必需的模组、模组版本与游戏或其他模组不兼容，或 API 版本不匹配。",
            "advice": "安装缺失的依赖模组，或更新相关模组到兼容版本。",
        },
        {
            "name": "模组冲突",
            "icon": "\u2694\ufe0f",
            "required": ["Exception caught during firing event: null"],
            "optional": ["conflict", "incompatible", "already registered", "Duplicate"],
            "cause": "两个或多个模组同时修改同一游戏内容，或模组之间不兼容。",
            "advice": "查看 Suspected Mods 字段，逐个禁用可疑模组定位冲突来源。",
        },
        {
            "name": "内存溢出",
            "icon": "\U0001f4be",
            "required": ["java.lang.OutOfMemoryError"],
            "optional": ["Unable to allocate", "heap space", "Metaspace", "GC overhead limit exceeded"],
            "cause": "分配给 Minecraft 的内存不足、内存泄漏（通常由模组引起）或数据集过大。",
            "advice": "在启动器设置中增加最大内存分配（建议 4-8GB），或检查是否有模组导致内存泄漏。",
        },
        {
            "name": "渲染与图形错误",
            "icon": "\U0001f3a8",
            "required": ["OpenGL"],
            "optional": ["GL error", "Shader", "Tesselator", "Rendering", "GPU", "Driver"],
            "cause": "显卡驱动问题、过时的 OpenGL 版本、着色器编译错误或显卡不兼容。",
            "advice": "更新显卡驱动，移除或更新光影/渲染优化模组，确保显卡支持所需 OpenGL 版本。",
        },
        {
            "name": "线程与并发错误",
            "icon": "\U0001f9f5",
            "required": ["ConcurrentModificationException"],
            "optional": ["Deadlock", "Thread stuck", "Wait timed out"],
            "cause": "模组在多线程环境下未正确处理同步。",
            "advice": "更新相关模组，或尝试移除最近添加的模组。",
        },
        {
            "name": "网络同步错误",
            "icon": "\U0001f310",
            "required": ["Connection refused"],
            "optional": ["Read timed out", "Packet handler", "NetworkManager"],
            "cause": "模组自定义网络包未正确注册、数据结构不一致或网络环境不稳定。",
            "advice": "检查网络连接，更新涉及网络功能的模组。",
        },
        {
            "name": "世界生成错误",
            "icon": "\U0001f5fa\ufe0f",
            "required": ["World Generation"],
            "optional": ["Chunk Loading", "Structure", "Biome", "Feature"],
            "cause": "模组的生物群系、结构或特征注册错误、生成算法有 bug，或与其他修改世界生成的模组冲突。",
            "advice": "更新涉及世界生成的模组，或创建新世界测试。",
        },
        {
            "name": "服务端/客户端逻辑错误",
            "icon": "\U0001f9e9",
            "required": ["Integrated Server"],
            "optional": ["Dedicated Server", "Logic error"],
            "cause": "模组未正确区分逻辑客户端与逻辑服务器，导致数据不同步。",
            "advice": "更新相关模组，检查模组是否支持当前游戏版本。",
        },
        {
            "name": "Java 虚拟机崩溃",
            "icon": "\U0001f4a5",
            "required": ["SIGSEGV", "EXCEPTION_ACCESS_VIOLATION"],
            "optional": ["Problematic frame", "fatal error"],
            "cause": "Java 版本不兼容、JVM 参数错误、本地代码崩溃（通常由模组触发）或硬件/驱动问题。",
            "advice": "更换兼容的 Java 版本，检查 JVM 参数，更新显卡驱动。",
        },
    ]

    def _diagnose_crash(self, crash_files: dict) -> list:
        """根据崩溃日志内容分析崩溃类型，返回匹配到的崩溃类型列表"""
        # 收集所有可用的日志文本
        text_parts = []

        for key in ("crash_report", "game_log", "debug_log", "jvm_crash_log"):
            path = crash_files.get(key)
            if path and os.path.exists(path):
                try:
                    for enc in ("utf-8", "gbk", "latin-1"):
                        try:
                            text_parts.append(Path(path).read_text(enc, errors="ignore"))
                            break
                        except (UnicodeDecodeError, UnicodeError):
                            continue
                except Exception:
                    pass

        combined_text = "\n".join(text_parts)
        if not combined_text.strip():
            return []

        matched = []
        for crash_type in self.CRASH_TYPES:
            required_hits = sum(1 for kw in crash_type["required"] if kw in combined_text)
            optional_hits = sum(1 for kw in crash_type["optional"] if kw in combined_text)
            if required_hits > 0:
                matched.append({
                    "name": crash_type["name"],
                    "icon": crash_type["icon"],
                    "cause": crash_type["cause"],
                    "advice": crash_type["advice"],
                    "score": required_hits + optional_hits * 0.5,
                })

        # 按匹配得分降序排列
        matched.sort(key=lambda x: x["score"], reverse=True)
        return matched[:3]  # 最多返回前 3 个最可能的崩溃类型

    def _show_crash_dialog(self, exit_code: int, crash_files: dict):
        """显示崩溃提示对话框"""
        import tkinter as tk
        from tkinter import filedialog
        import zipfile
        import shutil
        from datetime import datetime

        dialog = tk.Toplevel(self)
        dialog.title("游戏崩溃")
        dialog.resizable(False, False)
        dialog.attributes('-topmost', True)
        dialog.configure(bg='#1a1a2e')
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
        icon_path = os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__))), 'icon.ico')
        icon_frame = tk.Frame(dialog, bg='#1a1a2e')
        icon_frame.place(x=pad, y=pad, width=64, height=64)
        if os.path.exists(icon_path):
            try:
                from PIL import Image as PILImage, ImageTk
                pil_img = PILImage.open(icon_path).resize((64, 64), PILImage.LANCZOS)
                tk_img = ImageTk.PhotoImage(pil_img)
                tk.Label(icon_frame, image=tk_img, bg='#1a1a2e').pack()
                # 保存对图像的引用以防止被垃圾回收
                setattr(dialog, '_icon_ref', tk_img)
            except Exception:
                tk.Label(icon_frame, text='\u26cf', font=(FONT_FAMILY, 28), fg='#e94560', bg='#1a1a2e').pack()
        else:
            tk.Label(icon_frame, text='\u26cf', font=(FONT_FAMILY, 28), fg='#e94560', bg='#1a1a2e').pack()

        # 崩溃信息
        has_crash_report = "crash_report" in crash_files
        has_game_log = "game_log" in crash_files
        has_jvm_crash = "jvm_crash_log" in crash_files

        info_text = f"游戏异常退出 (退出码: {exit_code})"
        tk.Label(dialog, text=info_text, font=(FONT_FAMILY, 14, 'bold'),
                 fg='#e94560', bg='#1a1a2e').place(x=pad + 72, y=pad + 5)

        detail_parts = []
        if has_crash_report:
            detail_parts.append("已检测到崩溃报告")
        if has_jvm_crash:
            detail_parts.append("JVM 崩溃日志可用")
        if has_game_log:
            detail_parts.append("游戏日志可用")
        detail = "；".join(detail_parts) if detail_parts else "未找到崩溃报告文件，仍可尝试导出"
        tk.Label(dialog, text=detail, font=(FONT_FAMILY, 10),
                 fg='#8899aa', bg='#1a1a2e').place(x=pad + 72, y=pad + 38)

        # 分隔线
        tk.Frame(dialog, bg='#0f3460', height=1).place(x=pad, y=pad + 68, width=w - 2 * pad)

        # 诊断结果区域
        diag_y = pad + 78
        if diagnoses:
            for i, diag in enumerate(diagnoses):
                y = diag_y + i * 62
                # 背景框
                diag_frame = tk.Frame(dialog, bg='#16213e', highlightbackground='#0f3460', highlightthickness=1)
                diag_frame.place(x=pad, y=y, width=w - 2 * pad, height=56)
                # 标题行
                tk.Label(diag_frame, text=f"{diag['icon']} {diag['name']}",
                         font=(FONT_FAMILY, 10, 'bold'), fg='#e94560', bg='#16213e').pack(
                    anchor='w', padx=10, pady=(6, 0))
                # 建议（单行截断）
                advice_text = diag['advice']
                if len(advice_text) > 48:
                    advice_text = advice_text[:47] + "…"
                tk.Label(diag_frame, text=f"💡 {advice_text}",
                         font=(FONT_FAMILY, 9), fg='#8899aa', bg='#16213e').pack(
                    anchor='w', padx=10, pady=(2, 0))
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
                with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
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
                    for key, arcname in [("game_log", "logs/latest.log"), ("debug_log", "logs/debug.log"), ("jvm_crash_log", "hs_err_pid.log")]:
                        p = crash_files.get(key)
                        if p and os.path.exists(p):
                            zf.write(p, arcname)

                    # 启动器日志（从内存缓冲区或磁盘文件获取）
                    launcher_log_content = ""
                    # 优先从内存缓冲区获取
                    if hasattr(self, '_log_buffer') and self._log_buffer:
                        launcher_log_content = self._log_buffer.getvalue()
                    # 如果缓冲区为空，回退到 logzero 的磁盘日志文件
                    if not launcher_log_content.strip():
                        import platform as _platform
                        system = _platform.system().lower()
                        
                        if system == "linux":
                            # Linux: 日志在 /var/log/fmcl/latest.log
                            disk_log = Path("/var/log/fmcl/latest.log")
                        else:
                            # Windows/macOS: 日志在项目根目录
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
        btn_style = dict(font=(FONT_FAMILY, 10), relief='flat', cursor='hand2',
                         bg='#0f3460', fg='white', activebackground='#2d3a5c', activeforeground='white',
                         bd=0, highlightthickness=0)

        from ui.i18n import _

        btn1 = tk.Button(dialog, text=f"📄 {_('crash_report')}", command=_open_crash_report,
                         state='normal' if has_crash_report else 'disabled', **btn_style)
        btn1.place(x=pad, y=btn_y, width=w - 2 * pad, height=btn_h)

        btn2 = tk.Button(dialog, text=f"📋 {_('crash_game_log')}", command=_open_game_log,
                         state='normal' if has_game_log else 'normal', **btn_style)
        btn2.place(x=pad, y=btn_y + btn_h + 8, width=w - 2 * pad, height=btn_h)

        btn3 = tk.Button(dialog, text=f"📦 {_('crash_export')}", command=_export_crash_report,
                         bg='#e94560', fg='white', activebackground='#ff6b81', activeforeground='white',
                         font=(FONT_FAMILY, 10, 'bold'), relief='flat', cursor='hand2',
                         bd=0, highlightthickness=0)
        btn3.place(x=pad, y=btn_y + (btn_h + 8) * 2, width=w - 2 * pad, height=btn_h)

        # 上传分享日志按钮
        def _share_game_log():
            game_log_path = crash_files.get("game_log")
            if not game_log_path or not os.path.exists(game_log_path):
                messagebox.showinfo(_("crash_title"), _("crash_share_no_log"), parent=dialog)
                return

            share_btn.configure(state='disabled', text=_("crash_share_uploading") + "...")
            dialog.update_idletasks()

            def _do_upload():
                from api.logshare import upload_game_log, LogShareError
                try:
                    url = upload_game_log(game_log_path)
                    if url:
                        dialog.clipboard_clear()
                        dialog.clipboard_append(url)
                        dialog.after(0, lambda: messagebox.showinfo(
                            _("crash_title"), f"{_('crash_share_success')}\n\n{url}", parent=dialog))
                    else:
                        dialog.after(0, lambda: messagebox.showerror(
                            _("crash_title"), _("crash_share_no_log"), parent=dialog))
                except LogShareError as e:
                    dialog.after(0, lambda: messagebox.showerror(
                        _("crash_title"), _("crash_share_failed", error=str(e)), parent=dialog))
                except Exception as e:
                    dialog.after(0, lambda: messagebox.showerror(
                        _("crash_title"), _("crash_share_failed", error=str(e)), parent=dialog))
                finally:
                    dialog.after(0, lambda: share_btn.configure(
                        state='normal' if game_log_path and os.path.exists(game_log_path) else 'disabled',
                        text=_("crash_share_log")))

            threading.Thread(target=_do_upload, daemon=True).start()

        share_btn = tk.Button(dialog, text=_("crash_share_log"),
                              command=_share_game_log,
                              bg='#0f3460', fg='white', activebackground='#2d3a5c', activeforeground='white',
                              font=(FONT_FAMILY, 10), relief='flat', cursor='hand2',
                              bd=0, highlightthickness=0,
                              state='normal' if has_game_log else 'disabled')
        share_btn.place(x=pad, y=btn_y + (btn_h + 8) * 3, width=w - 2 * pad, height=btn_h)

        # AI 分析按钮
        _jdz_token = self.callbacks.get("get_jdz_token", lambda: None)() if self.callbacks else None

        ai_btn = tk.Button(dialog, text=_("crash_ai_analyze"),
                           command=lambda: self._ai_analyze_crash(crash_files, exit_code),
                           bg='#6c5ce7', fg='white', activebackground='#a29bfe', activeforeground='white',
                           font=(FONT_FAMILY, 10, 'bold'), relief='flat', cursor='hand2',
                           bd=0, highlightthickness=0,
                           state='normal' if _jdz_token else 'disabled')
        ai_btn.place(x=pad, y=btn_y + (btn_h + 8) * 4, width=w - 2 * pad, height=btn_h)

        if not _jdz_token:
            tk.Label(dialog, text="请先在设置中登录净读账号",
                     font=(FONT_FAMILY, 8), fg='#667788', bg='#1a1a2e').place(
                x=pad, y=btn_y + (btn_h + 8) * 4 + btn_h + 2)

        # 调整窗口高度以容纳新按钮
        h += (btn_h + 8) + (btn_h + 8) + (12 if not _jdz_token else 0)
        dialog.geometry(f"{w}x{h}")
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - w) // 2
        y = (dialog.winfo_screenheight() - h) // 2
        dialog.geometry(f"+{x}+{y}")

        # 关闭按钮
        close_btn = tk.Button(dialog, text='关闭', command=dialog.destroy,
                              font=(FONT_FAMILY, 9), relief='flat', cursor='hand2',
                              bg='#1a1a2e', fg='#667788', activebackground='#1a1a2e',
                              activeforeground='#aabbcc', bd=0)
        close_btn.place(x=w // 2 - 20, y=h - 36, width=40)

    def _read_file_tail(self, filepath: str, lines: int = 200) -> str:
        """读取文件最后 lines 行"""
        if not filepath or not os.path.exists(filepath):
            return ""
        try:
            for enc in ("utf-8", "gbk", "latin-1"):
                try:
                    with open(filepath, "r", encoding=enc, errors="ignore") as f:
                        return "".join(f.readlines()[-lines:])
                except (UnicodeDecodeError, UnicodeError):
                    continue
        except Exception:
            pass
        return ""

    def _collect_ai_context(self, crash_files: dict, exit_code: int) -> str:
        """收集发送给 AI 的崩溃上下文信息"""
        parts = []

        # 系统信息
        parts.append(f"[系统信息]\nOS: {platform.system()} {platform.release()}\n"
                     f"Python: {platform.python_version()}\n"
                     f"Architecture: {platform.machine()}\n"
                     f"退出码: {exit_code}")

        # 崩溃报告（完整内容，通常不大）
        crash_report = crash_files.get("crash_report")
        if crash_report:
            content = self._read_file_tail(crash_report, 99999)
            if content:
                parts.append(f"[崩溃报告]\n{content}")

        # 游戏日志最后 200 行
        game_log = crash_files.get("game_log")
        if game_log:
            content = self._read_file_tail(game_log, 200)
            if content:
                parts.append(f"[游戏日志（最后200行）]\n{content}")

        # debug 日志最后 200 行
        debug_log = crash_files.get("debug_log")
        if debug_log:
            content = self._read_file_tail(debug_log, 200)
            if content:
                parts.append(f"[Debug 日志（最后200行）]\n{content}")

        # JVM 崩溃日志
        jvm_log = crash_files.get("jvm_crash_log")
        if jvm_log:
            content = self._read_file_tail(jvm_log, 200)
            if content:
                parts.append(f"[JVM 崩溃日志（最后200行）]\n{content}")

        # 启动器日志最后 200 行
        launcher_log = ""
        if hasattr(self, '_log_buffer') and self._log_buffer:
            launcher_log = self._log_buffer.getvalue()
        if not launcher_log.strip():
            try:
                system = platform.system().lower()
                if system == "linux":
                    disk_log = Path("/var/log/fmcl/latest.log")
                else:
                    disk_log = Path("latest.log")
                if disk_log.exists():
                    for enc in ("utf-8", "gbk", "latin-1"):
                        try:
                            launcher_log = disk_log.read_text(enc, errors="ignore")
                            break
                        except (UnicodeDecodeError, UnicodeError):
                            continue
            except Exception:
                pass
        if launcher_log.strip():
            log_lines = launcher_log.strip().splitlines()[-200:]
            parts.append(f"[启动器日志（最后200行）]\n" + "\n".join(log_lines))

        # 结构化日志（JSONL 格式，包含安装/启动/崩溃等核心流程的结构化记录）
        try:
            from config import config
            structured_log_path = config.base_dir / "latest_structured.log"
            if structured_log_path.exists():
                structured_content = self._read_file_tail(str(structured_log_path), 100)
                if structured_content:
                    parts.append(f"[结构化日志（最后100行）]\n{structured_content}")
        except Exception:
            pass

        from structured_logger import slog
        slog.info("ai_context_collected", exit_code=exit_code,
                  has_crash_report=bool(crash_files.get("crash_report")),
                  has_game_log=bool(crash_files.get("game_log")),
                  has_debug_log=bool(crash_files.get("debug_log")),
                  has_jvm_log=bool(crash_files.get("jvm_crash_log")),
                  has_launcher_log=bool(launcher_log.strip()),
                  context_length=len("\n\n".join(parts)))

        return "\n\n".join(parts)

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
            self._show_privacy_consent_dialog(
                lambda: self._do_server_ai_analyze(context, exit_code, token))
            return

        self._do_server_ai_analyze(context, exit_code, token)

    def _collect_server_ai_context(self, exit_code: int) -> str:
        """收集发送给 AI 的服务器崩溃上下文"""
        parts = []

        # 系统信息
        parts.append(f"[系统信息]\nOS: {platform.system()} {platform.release()}\n"
                     f"Python: {platform.python_version()}\n"
                     f"Architecture: {platform.machine()}\n"
                     f"退出码: {exit_code}\n"
                     f"场景: 服务器崩溃分析")

        # 服务器版本
        version_id = getattr(self, 'selected_server_version', '') or ''
        if version_id:
            parts.append(f"[服务器版本]\n{version_id}")

        # 服务器控制台日志（_server_log_lines 在 _watch_server_exit 中收集）
        server_log_lines = getattr(self, '_server_log_lines', [])
        if server_log_lines:
            parts.append(f"[服务器日志（最后200行）]\n" + "\n".join(server_log_lines[-200:]))

        # 启动器日志最后 200 行
        launcher_log = ""
        if hasattr(self, '_log_buffer') and self._log_buffer:
            launcher_log = self._log_buffer.getvalue()
        if not launcher_log.strip():
            try:
                system = platform.system().lower()
                if system == "linux":
                    disk_log = Path("/var/log/fmcl/latest.log")
                else:
                    disk_log = Path("latest.log")
                if disk_log.exists():
                    for enc in ("utf-8", "gbk", "latin-1"):
                        try:
                            launcher_log = disk_log.read_text(enc, errors="ignore")
                            break
                        except (UnicodeDecodeError, UnicodeError):
                            continue
            except Exception:
                pass
        if launcher_log.strip():
            log_lines = launcher_log.strip().splitlines()[-200:]
            parts.append(f"[启动器日志（最后200行）]\n" + "\n".join(log_lines))

        # 结构化日志
        try:
            from config import config
            structured_log_path = config.base_dir / "latest_structured.log"
            if structured_log_path.exists():
                structured_content = self._read_file_tail(str(structured_log_path), 100)
                if structured_content:
                    parts.append(f"[结构化日志（最后100行）]\n{structured_content}")
        except Exception:
            pass

        from structured_logger import slog
        slog.info("server_ai_context_collected", exit_code=exit_code,
                  has_server_log=bool(server_log_lines),
                  has_launcher_log=bool(launcher_log.strip()),
                  context_length=len("\n\n".join(parts)))

        return "\n\n".join(parts)

    def _do_server_ai_analyze(self, context: str, exit_code: int, token: str):
        """执行服务器 AI 分析（已通过隐私检查）"""
        system_prompt = (
            "你是一个 Minecraft 服务器崩溃日志分析专家。根据用户提供的服务器日志、启动器日志和系统信息，"
            "分析服务器崩溃或异常退出的原因并给出具体、可操作的建议。\n"
            "请用中文回复，格式如下：\n"
            "## 崩溃原因分析\n（简明扼要地说明崩溃原因）\n\n"
            "## 建议操作\n（列出具体的解决步骤，每步用数字编号）"
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请分析以下 Minecraft 服务器异常退出信息：\n\n{context}"},
        ]

        # 显示加载窗口
        import tkinter as tk
        loading = tk.Toplevel(self)
        loading.title("AI 分析中...")
        loading.geometry("320x100")
        loading.resizable(False, False)
        loading.attributes('-topmost', True)
        loading.configure(bg='#1a1a2e')
        loading.transient(self)
        try:
            loading.grab_set()
        except Exception:
            pass
        loading.update_idletasks()
        lx = (loading.winfo_screenwidth() - 320) // 2
        ly = (loading.winfo_screenheight() - 100) // 2
        loading.geometry(f"+{lx}+{ly}")

        tk.Label(loading, text="🤖 AI 正在分析服务器日志...",
                 font=(FONT_FAMILY, 12), fg='#a0a0b0', bg='#1a1a2e').pack(pady=(20, 5))
        tk.Label(loading, text="请稍候，这可能需要几秒钟",
                 font=(FONT_FAMILY, 9), fg='#667788', bg='#1a1a2e').pack()

        def _do_analyze():
            import urllib.request
            import urllib.error
            import json
            try:
                req_data = json.dumps({
                    "model": "deepseek-chat",
                    "messages": messages,
                    "stream": False,
                }).encode("utf-8")

                req = urllib.request.Request(
                    "https://jingdu.qzz.io/api/deepseek/v1/chat/completions",
                    data=req_data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                        "User-Agent": "FMCL/1.0 (Minecraft Launcher; server-crash-analyzer)",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

                ai_content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not ai_content:
                    ai_content = "AI 未返回有效分析结果。"
                from structured_logger import slog
                slog.info("ai_server_crash_analysis", exit_code=exit_code, result_length=len(ai_content))
                self.after(0, lambda: _show_result(ai_content))
            except urllib.error.HTTPError as e:
                _code = e.code
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                _err_msg = f"HTTP {_code}: {body[:200]}"
                from structured_logger import slog
                slog.error("ai_server_crash_analysis_failed", exit_code=exit_code, error=_err_msg)
                self.after(0, lambda: _show_error(_err_msg))
            except Exception as e:
                _err_msg = str(e)
                from structured_logger import slog
                slog.error("ai_server_crash_analysis_failed", exit_code=exit_code, error=_err_msg)
                self.after(0, lambda: _show_error(_err_msg))
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
        dialog.attributes('-topmost', True)
        dialog.configure(bg='#1a1a2e')
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        w, h = 440, 320
        dialog.geometry(f"{w}x{h}")
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - w) // 2
        y = (dialog.winfo_screenheight() - h) // 2
        dialog.geometry(f"+{x}+{y}")

        pad = 24

        # 标题
        tk.Label(dialog, text=_("ai_privacy_title"),
                 font=(FONT_FAMILY, 14, 'bold'), fg='#e94560', bg='#1a1a2e').place(x=pad, y=pad)

        # 隐私说明内容
        content_frame = tk.Frame(dialog, bg='#16213e', highlightbackground='#0f3460', highlightthickness=1)
        content_frame.place(x=pad, y=pad + 36, width=w - 2 * pad, height=160)

        content_text = _("ai_privacy_content")
        content_label = tk.Label(content_frame, text=content_text,
                                font=(FONT_FAMILY, 10), fg='#a0a0b0', bg='#16213e',
                                wraplength=w - 2 * pad - 20, justify='left', anchor='nw')
        content_label.pack(padx=10, pady=10, fill='both', expand=True)

        # 同意复选框
        consent_var = tk.BooleanVar(value=False)
        consent_cb = tk.Checkbutton(dialog, text=_("ai_privacy_agreement"),
                                    variable=consent_var,
                                    font=(FONT_FAMILY, 10), fg='#a0a0b0', bg='#1a1a2e',
                                    selectcolor='#16213e', activebackground='#1a1a2e',
                                    activeforeground='#ffffff')
        consent_cb.place(x=pad, y=pad + 204)

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
                consent_cb.configure(fg='#e94560')
                dialog.after(1500, lambda: consent_cb.configure(fg='#a0a0b0'))

        confirm_btn = tk.Button(dialog, text=_("ai_privacy_accept"),
                                command=_on_confirm,
                                font=(FONT_FAMILY, 10, 'bold'), relief='flat', cursor='hand2',
                                bg='#6c5ce7', fg='white', activebackground='#a29bfe',
                                activeforeground='white', bd=0)
        confirm_btn.place(x=pad, y=h - 52, width=(w - 2 * pad) // 2 - 4, height=36)

        cancel_btn = tk.Button(dialog, text=_("confirm"),
                               command=dialog.destroy,
                               font=(FONT_FAMILY, 10), relief='flat', cursor='hand2',
                               bg='#0f3460', fg='white', activebackground='#2d3a5c',
                               activeforeground='white', bd=0)
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
        loading.attributes('-topmost', True)
        loading.configure(bg='#1a1a2e')
        loading.transient(self)
        try:
            loading.grab_set()
        except Exception:
            pass
        loading.update_idletasks()
        lx = (loading.winfo_screenwidth() - 320) // 2
        ly = (loading.winfo_screenheight() - 100) // 2
        loading.geometry(f"+{lx}+{ly}")

        tk.Label(loading, text="🤖 AI 正在分析崩溃原因...",
                 font=(FONT_FAMILY, 12), fg='#a0a0b0', bg='#1a1a2e').pack(pady=(20, 5))
        tk.Label(loading, text="请稍候，这可能需要几秒钟",
                 font=(FONT_FAMILY, 9), fg='#667788', bg='#1a1a2e').pack()

        def _do_analyze():
            import urllib.request
            import urllib.error
            import json
            try:
                req_data = json.dumps({
                    "model": "deepseek-chat",
                    "messages": messages,
                    "stream": False,
                }).encode("utf-8")

                req = urllib.request.Request(
                    "https://jingdu.qzz.io/api/deepseek/v1/chat/completions",
                    data=req_data,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {token}",
                        "User-Agent": "FMCL/1.0 (Minecraft Launcher; crash-analyzer)",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=120) as resp:
                    result = json.loads(resp.read().decode("utf-8"))

                ai_content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                if not ai_content:
                    ai_content = "AI 未返回有效分析结果。"
                from structured_logger import slog
                slog.info("ai_crash_analysis", exit_code=exit_code, result_length=len(ai_content))
                self.after(0, lambda: _show_result(ai_content))
            except urllib.error.HTTPError as e:
                _code = e.code
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                _err_msg = f"HTTP {_code}: {body[:200]}"
                from structured_logger import slog
                slog.error("ai_crash_analysis_failed", exit_code=exit_code, error=_err_msg)
                self.after(0, lambda: _show_error(_err_msg))
            except Exception as e:
                _err_msg = str(e)
                from structured_logger import slog
                slog.error("ai_crash_analysis_failed", exit_code=exit_code, error=_err_msg)
                self.after(0, lambda: _show_error(_err_msg))
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
        from tkinter import filedialog
        from datetime import datetime

        result = tk.Toplevel(self)
        result.title(title)
        result.geometry("580x640")
        result.resizable(True, True)
        result.attributes('-topmost', True)
        result.configure(bg='#1a1a2e')
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
        tk.Label(result, text=f"🤖 {title}",
                 font=(FONT_FAMILY, 14, 'bold'), fg='#ffffff', bg='#1a1a2e').pack(anchor='w', padx=16, pady=(16, 8))

        # 内容区域（可滚动文本框）
        text_frame = tk.Frame(result, bg='#16213e', highlightbackground='#2d3a5c', highlightthickness=1)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 12))

        text_widget = tk.Text(
            text_frame, wrap=tk.WORD, font=(FONT_FAMILY, 11),
            fg='#ffffff', bg='#16213e', bd=0, padx=12, pady=12,
            insertbackground='white', selectbackground='#0f3460',
            relief='flat',
        )
        scrollbar = tk.Scrollbar(text_frame, command=text_widget.yview, bg='#1a1a2e',
                                 troughcolor='#16213e', activebackground='#0f3460')
        text_widget.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        text_widget.pack(fill=ctk.BOTH, expand=True)
        text_widget.insert('1.0', content)
        text_widget.configure(state='disabled')

        # 按钮区
        btn_frame = tk.Frame(result, bg='#1a1a2e')
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
                        f.write(f"FMCL AI 崩溃分析结果\n"
                                f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                                f"{'=' * 50}\n\n"
                                f"{content}\n")
                    messagebox.showinfo("保存成功", f"分析结果已保存至:\n{save_path}", parent=result)
                except Exception as e:
                    messagebox.showerror("保存失败", f"保存时出错:\n{e}", parent=result)

        tk.Button(btn_frame, text="💾 保存为 TXT", command=_save_as_txt,
                  font=(FONT_FAMILY, 10), relief='flat', cursor='hand2',
                  bg='#0f3460', fg='white', activebackground='#2d3a5c', activeforeground='white',
                  bd=0).pack(side=ctk.LEFT)

        tk.Button(btn_frame, text="关闭", command=result.destroy,
                  font=(FONT_FAMILY, 10), relief='flat', cursor='hand2',
                  bg='#e94560', fg='white', activebackground='#ff6b81', activeforeground='white',
                  bd=0, width=80).pack(side=ctk.RIGHT)
