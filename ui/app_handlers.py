"""ModernApp 事件处理 Mixin - 版本管理、游戏操作、更新检查、队列处理等"""
import os
import io
import re
import sys
import logging
import platform
import subprocess
import threading
import queue
import tkinter.messagebox as messagebox
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY, _get_fmcl_version
from ui.windows.resource_manager import ResourceManagerWindow
from ui.windows.launcher_settings import LauncherSettingsWindow
from ui.windows.modpack_install import ModpackInstallWindow
from ui.windows.mod_browser import ModBrowserWindow
from ui.i18n import _, get_available_languages, set_language
from structured_logger import slog


class EventHandlerMixin(object):
    """事件处理和游戏管理 Mixin"""

    # ─── 版本列表渲染 ─────────────────────────────────────────

    @staticmethod
    def _has_mod_loader(version_id: str) -> bool:
        """判断版本是否安装了模组加载器"""
        v = version_id.lower()
        return any(loader in v for loader in ("forge", "fabric", "neoforge"))

    def _render_installed_versions(self, versions: List[str]):
        """渲染已安装版本列表"""
        # 清空现有
        for widget in self.version_list_frame.winfo_children():
            widget.destroy()
        self.version_buttons.clear()

        self.version_count_label.configure(text=_("version_count", count=len(versions)))

        if not versions:
            ctk.CTkLabel(
                self.version_list_frame,
                text=_("no_installed_versions"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=30)
            return

        for ver in versions:
            has_loader = self._has_mod_loader(ver)
            btn_frame = ctk.CTkFrame(
                self.version_list_frame,
                fg_color=COLORS["bg_medium"],
                corner_radius=8,
                height=42,
            )
            btn_frame.pack(fill=ctk.X, pady=2)
            btn_frame.pack_propagate(False)

            btn = ctk.CTkButton(
                btn_frame,
                text=f"  {ver}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
                command=lambda v=ver: self._select_version(v),
            )
            btn.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=5, pady=3)

            # 删除按钮（最右边）
            del_btn = ctk.CTkButton(
                btn_frame,
                text="X",
                width=30,
                height=28,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                fg_color="transparent",
                hover_color=COLORS["accent"],
                text_color=COLORS["text_secondary"],
                command=lambda v=ver: self._on_delete_version(v),
            )
            del_btn.pack(side=ctk.RIGHT, padx=(0, 5))

            # 版本设置按钮
            settings_btn = ctk.CTkButton(
                btn_frame,
                text="⚙",
                width=30,
                height=28,
                font=ctk.CTkFont(size=14),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_secondary"],
                command=lambda v=ver: self._open_resource_manager_for_version(v),
            )
            settings_btn.pack(side=ctk.RIGHT, padx=(0, 2))

            # 安装模组按钮（仅模组加载器版本显示，最左边）
            if has_loader:
                mod_btn = ctk.CTkButton(
                    btn_frame,
                    text="🧩",
                    width=30,
                    height=28,
                    font=ctk.CTkFont(size=14),
                    fg_color="transparent",
                    hover_color=COLORS["bg_light"],
                    text_color=COLORS["success"],
                    command=lambda v=ver: self._open_mod_browser(v),
                )
                mod_btn.pack(side=ctk.RIGHT, padx=(0, 2))

            self.version_buttons.append({"frame": btn_frame, "version": ver})

    def _render_available_versions(self, versions: List[Dict[str, Any]]):
        """渲染可用版本列表，按正式版/测试版分类"""
        # 保存全量数据
        self._all_available_versions = versions
        self._release_versions = [v for v in versions if v.get("type") == "release"]
        self._snapshot_versions = [v for v in versions if v.get("type") == "snapshot"]

        # 重置页码
        self._current_page = 1
        self._render_current_tab()

    def _on_version_tab_change(self):
        """Tab 切换回调"""
        self._current_page = 1
        self._render_current_tab()

    def _get_current_tab_versions(self) -> List[Dict[str, Any]]:
        """获取当前 tab 对应的全量版本列表"""
        tab = self.version_tab_var.get()
        if tab == "release":
            return self._release_versions
        return self._snapshot_versions

    def _get_total_pages(self) -> int:
        """获取总页数"""
        versions = self._get_current_tab_versions()
        if not versions:
            return 1
        return max(1, (len(versions) + self._page_size - 1) // self._page_size)

    def _render_current_tab(self):
        """渲染当前选中的 tab 列表（分页）"""
        tab = self.version_tab_var.get()
        all_versions = self._get_current_tab_versions()
        total_pages = self._get_total_pages()

        # 确保页码合法
        if self._current_page > total_pages:
            self._current_page = total_pages
        if self._current_page < 1:
            self._current_page = 1

        # 分页切片
        start = (self._current_page - 1) * self._page_size
        end = start + self._page_size
        display_versions = all_versions[start:end]

        # 更新分页信息
        self._page_info_label.configure(text=f"{self._current_page}/{total_pages}")
        self._prev_page_btn.configure(state=ctk.NORMAL if self._current_page > 1 else ctk.DISABLED)
        self._next_page_btn.configure(state=ctk.NORMAL if self._current_page < total_pages else ctk.DISABLED)

        # 清空列表
        for widget in self.available_list_frame.winfo_children():
            widget.destroy()
        self.available_version_buttons.clear()

        if not display_versions:
            ctk.CTkLabel(
                self.available_list_frame,
                text="暂无版本" if tab == "release" else "暂无测试版",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
            ).pack(pady=10)
            return

        for i in range(0, len(display_versions), 2):
            row_frame = ctk.CTkFrame(self.available_list_frame, fg_color="transparent", height=28)
            row_frame.pack(fill=ctk.X, pady=1)
            row_frame.pack_propagate(False)

            for j in range(2):
                if i + j < len(display_versions):
                    ver = display_versions[i + j]
                    ver_id = ver.get("id", "Unknown")
                    ver_type = ver.get("type", "")
                    type_icon = "📦" if ver_type == "release" else "🔬"

                    btn = ctk.CTkButton(
                        row_frame,
                        text=f"{type_icon} {ver_id}",
                        font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                        fg_color="transparent",
                        hover_color=COLORS["bg_light"],
                        text_color=COLORS["text_primary"],
                        anchor=ctk.W,
                        height=26,
                        command=lambda v=ver_id: self._quick_select_version(v),
                    )
                    btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 2))

    def _on_prev_page(self):
        """上一页"""
        if self._current_page > 1:
            self._current_page -= 1
            self._render_current_tab()

    def _on_next_page(self):
        """下一页"""
        if self._current_page < self._get_total_pages():
            self._current_page += 1
            self._render_current_tab()

    def _select_version(self, version: str):
        """选择已安装版本"""
        self.selected_version = version

        # 高亮选中
        for item in self.version_buttons:
            frame = item["frame"]
            if item["version"] == version:
                frame.configure(fg_color=COLORS["bg_light"])
            else:
                frame.configure(fg_color=COLORS["bg_medium"])

        self.set_status(f"已选择: {version}", "info")

    def _quick_select_version(self, version_id: str):
        """快速选择可用版本填入输入框"""
        # 提取纯版本号
        clean_id = version_id.split()[0] if " " in version_id else version_id
        self.version_entry.delete(0, ctk.END)
        self.version_entry.insert(0, clean_id)
        self.set_status(f"已选择版本: {clean_id}", "info")

    def _on_modloader_change(self, *args):
        """模组加载器选项变更回调"""
        loader = self.modloader_var.get()
        # 获取原始加载器名称用于显示
        loader_map = {
            _("mod_loader_none"): "None",
            _("mod_loader_forge"): "Forge",
            _("mod_loader_fabric"): "Fabric",
            _("mod_loader_neoforge"): "NeoForge",
        }
        raw_loader = loader_map.get(loader, "")
        if raw_loader and raw_loader != "None":
            self.modloader_hint.configure(
                text=_("mod_loader_hint", loader=raw_loader)
            )
        else:
            self.modloader_hint.configure(text="")

    def _on_delete_version(self, version: str):
        """删除版本按钮回调"""
        if not messagebox.askyesno(_("confirm_delete"), _("confirm_delete_version", version=version)):
            return
        self.set_status(_("deleting_version", version=version), "loading")
        self._run_in_thread(self._remove_version, version)

    def _remove_version(self, version_id: str):
        """删除版本（后台线程）"""
        try:
            success, vid = self.callbacks["remove_version"](version_id)
            self._task_queue.put(("remove_done", (vid, success)))
        except Exception as e:
            self._task_queue.put(("remove_error", str(e)))

    # ─── 事件处理 ─────────────────────────────────────────────

    def _on_kill_game(self):
        """强制结束游戏进程"""
        if "kill_game_process" in self.callbacks:
            success = self.callbacks["kill_game_process"]()
            if success:
                self._killed_by_user = True
                self.set_status("游戏进程已强制结束", "warning")
            else:
                self.set_status("没有正在运行的游戏进程", "info")
            self.kill_btn.configure(state=ctk.DISABLED)

    def _start_launch_animation(self):
        """启动进度条加载动画（来回滚动）"""
        self._launch_anim_running = True
        self._launch_anim_pos = 0.0
        self._launch_anim_dir = 1
        self.progress_label.configure(text="加载中...")
        self._launch_animate_step()

    def _launch_animate_step(self):
        """进度条动画单步"""
        if not self._launch_anim_running:
            return
        step = 0.02
        self._launch_anim_pos += step * self._launch_anim_dir
        if self._launch_anim_pos >= 1.0:
            self._launch_anim_pos = 1.0
            self._launch_anim_dir = -1
        elif self._launch_anim_pos <= 0.0:
            self._launch_anim_pos = 0.0
            self._launch_anim_dir = 1
        self.progress_bar.set(self._launch_anim_pos)
        self.after(50, self._launch_animate_step)

    def _stop_launch_animation(self):
        """停止进度条加载动画"""
        self._launch_anim_running = False
        self.progress_bar.set(0)
        self.progress_label.configure(text="")

    def _watch_game_exit(self):
        """监控游戏进程退出（后台线程），检测崩溃并通知主线程"""
        if "get_game_process" not in self.callbacks:
            return

        proc = self.callbacks["get_game_process"]()
        if proc is None:
            return

        # 等待进程退出（阻塞）
        proc.wait()
        exit_code = proc.returncode
        logger.info(f"游戏进程已退出，退出码: {exit_code}")

        if exit_code != 0 and not getattr(self, "_killed_by_user", False):
            # 退出码非 0 视为崩溃，收集崩溃文件信息
            crash_files = self._collect_crash_info()
            self._task_queue.put(("game_crashed", {"exit_code": exit_code, "crash_files": crash_files}))
        else:
            self._task_queue.put(("game_exited", None))

    def _collect_crash_info(self):
        """收集崩溃相关文件信息（后台线程调用），支持版本隔离目录"""
        
        files = {}
        mc_dir = None
        try:
            if "get_minecraft_dir" in self.callbacks:
                mc_dir = Path(self.callbacks["get_minecraft_dir"]())
            if mc_dir and mc_dir.exists():
                base_dir = mc_dir.parent  # 项目根目录
                system = platform.system().lower()
                
                # ── 游戏日志：根据平台在不同位置查找 ──
                if system == "linux":
                    # Linux: 日志在 /var/log/fmcl/
                    logs_dir = Path("/var/log/fmcl")
                    if logs_dir.exists():
                        latest_log = logs_dir / "latest.log"
                        if latest_log.exists():
                            files["game_log"] = str(latest_log)
                        debug_log = logs_dir / "debug.log"
                        if debug_log.exists():
                            files["debug_log"] = str(debug_log)
                else:
                    # Windows/macOS: 日志在项目根目录的 ./logs 下
                    logs_dir = base_dir / "logs"
                    if logs_dir.exists():
                        latest_log = logs_dir / "latest.log"
                        if latest_log.exists():
                            files["game_log"] = str(latest_log)
                        debug_log = logs_dir / "debug.log"
                        if debug_log.exists():
                            files["debug_log"] = str(debug_log)

                # ── 崩溃报告：在版本隔离目录或全局 .minecraft 下查找 ──
                version_id = getattr(self, "_running_version_id", None)
                game_dirs = []
                if version_id:
                    version_game_dir = mc_dir / "versions" / version_id
                    if version_game_dir.exists():
                        game_dirs.append(version_game_dir)
                game_dirs.append(mc_dir)  # 最后检查全局目录作为回退

                for game_dir in game_dirs:
                    # 崩溃报告（取最新的）
                    crash_dir = game_dir / "crash-reports"
                    if crash_dir.exists():
                        crash_reports = sorted(crash_dir.glob("crash-*.txt"), key=lambda f: f.stat().st_mtime, reverse=True)
                        if crash_reports:
                            if "crash_report" not in files:
                                files["crash_report"] = str(crash_reports[0])
                            if "crash_report_list" not in files:
                                files["crash_report_list"] = [str(f) for f in crash_reports[:10]]
                    # JVM 崩溃日志 (hs_err_pid*.log)
                    hs_err_files = sorted(game_dir.glob("hs_err_pid*.log"), key=lambda f: f.stat().st_mtime, reverse=True)
                    if hs_err_files and "jvm_crash_log" not in files:
                        files["jvm_crash_log"] = str(hs_err_files[0])
        except Exception as e:
            logger.error(f"收集崩溃信息失败: {e}")
        return files

    def _watch_game_stdout(self):
        """监控游戏进程 stdout，检测到游戏窗口出现后关闭管道以避免缓冲区满导致游戏卡顿（后台线程）"""
        if "get_game_process" not in self.callbacks:
            return

        proc = self.callbacks["get_game_process"]()
        if proc is None or proc.stdout is None:
            logger.warning("无法获取游戏进程 stdout")
            return

        marker = "Datafixer optimizations took"
        timeout = 120

        import threading
        import time

        logger.info("开始监控游戏进程 stdout")

        result = {"detected": False}

        def _reader():
            try:
                for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", errors="ignore")
                    if not result["detected"] and marker in line:
                        result["detected"] = True
                        return
            except Exception:
                pass

        reader_thread = threading.Thread(target=_reader, daemon=True)
        reader_thread.start()

        # 等待检测到标记或超时
        start = time.time()
        while time.time() - start < timeout and self._running:
            if result["detected"]:
                logger.info("检测到游戏窗口出现 (Datafixer Bootstrap)，关闭 stdout 管道")
                try:
                    proc.stdout.close()
                except Exception:
                    pass
                self._task_queue.put(("game_window_detected", None))
                return
            if not reader_thread.is_alive() and not result["detected"]:
                # 读线程退出但没检测到标记（进程可能已退出），关闭管道
                logger.info("游戏 stdout 已关闭，释放管道")
                try:
                    proc.stdout.close()
                except Exception:
                    pass
                return
            time.sleep(0.2)

        # 超时：关闭管道避免缓冲区问题
        logger.info(f"游戏 stdout 监控超时 ({timeout}s)，关闭管道")
        try:
            proc.stdout.close()
        except Exception:
            pass

    def _test_connection(self):
        """测试当前镜像源连接（后台线程）"""
        if "test_mirror_connection" in self.callbacks:
            try:
                ok = self.callbacks["test_mirror_connection"]()
                name = self.callbacks.get("get_mirror_name", lambda: "未知")()
                if ok:
                    self._task_queue.put(("connection_ok", name))
                else:
                    self._task_queue.put(("connection_fail", name))
            except Exception as e:
                self._task_queue.put(("connection_fail", str(e)))

    # ─── 更新检查 ─────────────────────────────────────────────

    def _on_check_update(self):
        """检查更新按钮回调"""
        self.set_status("正在检查更新...", "loading")
        self.update_btn.configure(state=ctk.DISABLED)
        self._run_in_thread(self._check_update)

    def _check_update(self, silent: bool = False):
        """
        检查更新（后台线程）

        Args:
            silent: 是否静默模式（无新版本时不提示）
        """
        try:
            from updater import check_for_update
            release_info = check_for_update()

            if release_info:
                self._task_queue.put(("update_available", (release_info["version"], release_info.get("body", ""))))
            elif not silent:
                self._task_queue.put(("update_no_new", None))

        except Exception as e:
            if not silent:
                self._task_queue.put(("update_check_error", str(e)))

        finally:
            # 恢复按钮状态
            self.after(0, lambda: self.update_btn.configure(state=ctk.NORMAL))

    def _show_update_dialog(self, version: str, body: str):
        """显示更新可用对话框"""
        dialog = ctk.CTkToplevel(self)
        dialog.title("发现新版本")
        dialog.geometry("500x350")
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self)
        dialog.grab_set()

        # 居中
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - 500) // 2
        y = (dialog.winfo_screenheight() - 350) // 2
        dialog.geometry(f"500x350+{x}+{y}")

        # 标题
        ctk.CTkLabel(
            dialog,
            text=f"⬆ 发现新版本: {version}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["accent"],
        ).pack(pady=(20, 10))

        # 更新日志
        changelog_frame = ctk.CTkScrollableFrame(
            dialog,
            fg_color=COLORS["bg_medium"],
            height=160,
        )
        changelog_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=(0, 10))

        display_text = body if body else "暂无更新日志"
        ctk.CTkLabel(
            changelog_frame,
            text=display_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_primary"],
            wraplength=440,
            justify=ctk.LEFT,
            anchor=ctk.W,
        ).pack(fill=ctk.BOTH, expand=True, padx=5, pady=5)

        # 按钮
        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(0, 20))

        def on_update():
            dialog.grab_release()
            dialog.destroy()
            self._start_update_download()

        def on_skip():
            dialog.grab_release()
            dialog.destroy()
            self.set_status(f"已跳过更新 {version}", "info")

        ctk.CTkButton(
            btn_frame,
            text="立即更新",
            width=120,
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=on_update,
        ).pack(side=ctk.LEFT, padx=10)

        ctk.CTkButton(
            btn_frame,
            text="稍后再说",
            width=120,
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["card_border"],
            command=on_skip,
        ).pack(side=ctk.LEFT, padx=10)

    def _start_update_download(self):
        """开始下载并安装更新"""
        self.set_status("正在准备下载更新...", "loading")
        self.progress_bar.set(0)
        self._run_in_thread(self._do_update)

    def _do_update(self):
        """执行更新下载与安装（后台线程）"""
        try:
            from updater import check_for_update, find_suitable_asset, download_update, install_update

            # 重新检查获取 release 信息
            release_info = check_for_update()
            if not release_info:
                self._task_queue.put(("update_check_error", "未找到新版本"))
                return

            # 查找适合当前平台的安装包
            asset = find_suitable_asset(release_info["assets"])
            if not asset:
                self._task_queue.put(("update_download_error", "未找到适合当前平台的安装包"))
                return

            # 下载
            def _progress(downloaded: int, total: int):
                self._task_queue.put(("update_download_progress", (downloaded, total)))

            file_path = download_update(asset, progress_callback=_progress)
            if not file_path:
                self._task_queue.put(("update_download_error", "下载失败"))
                return

            self._task_queue.put(("update_download_done", None))

            # 安装
            success = install_update(file_path)
            if success:
                self._task_queue.put(("update_install_started", None))
            else:
                self._task_queue.put(("update_download_error", "启动安装程序失败"))

        except Exception as e:
            logger.error(f"更新失败: {e}")
            self._task_queue.put(("update_download_error", str(e)))

    def _on_app_ready(self, on_agreement_complete=None):
        """应用初始化完成（由外部调用触发）"""
        self._launcher_ready = True
        # 重新加载用户设置
        self._reload_user_settings()
        # 启动日志轮询
        if self._log_capture_active:
            self._poll_log_buffer()
        # 检查是否需要显示使用条款同意弹窗
        self._check_terms_consent(on_complete=on_agreement_complete)

    def _check_terms_consent(self, on_complete=None):
        """检查使用条款/AI隐私同意状态，任一未同意则显示弹窗"""
        from config import config
        if not config.terms_consent or not config.ai_privacy_consent:
            self.after(500, lambda: self._show_terms_consent_dialog(on_complete))
        elif on_complete:
            on_complete()

    def _show_terms_consent_dialog(self, on_complete=None):
        """显示使用条款与隐私协议同意弹窗"""
        import tkinter as tk
        from config import config

        dialog = tk.Toplevel(self)
        dialog.title(_("terms_title"))
        dialog.resizable(False, False)
        dialog.attributes('-topmost', True)
        dialog.configure(bg='#1a1a2e')
        dialog.transient(self)
        dialog.grab_set()

        w, h = 480, 320
        dialog.geometry(f"{w}x{h}")
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - w) // 2
        y = (dialog.winfo_screenheight() - h) // 2
        dialog.geometry(f"+{x}+{y}")

        pad = 24

        # 标题
        tk.Label(dialog, text=_("terms_title"),
                 font=(FONT_FAMILY, 16, 'bold'), fg='#e94560', bg='#1a1a2e').place(x=pad, y=pad)

        # 内容区域
        content_frame = tk.Frame(dialog, bg='#16213e', highlightbackground='#0f3460', highlightthickness=1)
        content_frame.place(x=pad, y=pad + 40, width=w - 2 * pad, height=180)

        content_text = _("terms_content")
        content_label = tk.Label(content_frame, text=content_text,
                                font=(FONT_FAMILY, 11), fg='#a0a0b0', bg='#16213e',
                                wraplength=w - 2 * pad - 20, justify='left', anchor='nw')
        content_label.pack(padx=10, pady=10, fill='both', expand=True)

        # 同意复选框
        consent_var = tk.BooleanVar(value=False)
        consent_cb = tk.Checkbutton(dialog, text=_("terms_agree"),
                                    variable=consent_var,
                                    font=(FONT_FAMILY, 10), fg='#a0a0b0', bg='#1a1a2e',
                                    selectcolor='#16213e', activebackground='#1a1a2e',
                                    activeforeground='#ffffff')
        consent_cb.place(x=pad, y=h - 72)

        # 确认按钮
        def _on_confirm():
            if consent_var.get():
                config.terms_consent = True
                config.ai_privacy_consent = True
                config.save_config()
                dialog.destroy()
                if on_complete:
                    on_complete()
            else:
                consent_cb.configure(fg='#e94560')
                dialog.after(1500, lambda: consent_cb.configure(fg='#a0a0b0'))

        confirm_btn = tk.Button(dialog, text=_("confirm"),
                                command=_on_confirm,
                                font=(FONT_FAMILY, 10, 'bold'), relief='flat', cursor='hand2',
                                bg='#6c5ce7', fg='white', activebackground='#a29bfe',
                                activeforeground='white', bd=0)
        confirm_btn.place(x=w - pad - 120, y=h - 52, width=120, height=36)

    def _reload_user_settings(self):
        """重新加载用户设置（callbacks 设置后调用）"""
        # 加载角色名
        if "get_player_name" in self.callbacks:
            saved_name = self.callbacks["get_player_name"]()
            if saved_name:
                self.player_name_var.set(saved_name)
        # 加载皮肤路径
        if "get_skin_path" in self.callbacks:
            saved_skin = self.callbacks["get_skin_path"]()
            if saved_skin and os.path.exists(saved_skin):
                self._current_skin_path = saved_skin
                self._update_skin_preview(saved_skin)
        self.set_status("正在初始化环境...", "loading")
        self._run_in_thread(self._init_environment)

    def _init_environment(self):
        """初始化环境（后台线程）"""
        try:
            self.callbacks["check_environment"]()
            self._task_queue.put(("init_done", None))
        except Exception as e:
            self._task_queue.put(("init_error", str(e)))

    def _refresh_versions(self):
        """刷新版本列表：先加载已安装版本，再异步加载可用版本"""
        self.set_status("正在加载已安装版本...", "loading")
        self._run_in_thread(self._load_installed_versions)

    def _load_installed_versions(self):
        """加载已安装版本（后台线程，本地操作无需网络）"""
        try:
            installed = self.callbacks["get_installed_versions"]()
            self._task_queue.put(("installed_loaded", installed))
        except Exception as e:
            self._task_queue.put(("load_error", str(e)))

    def _load_available_versions(self):
        """加载可用版本列表（后台线程，需要网络）"""
        try:
            available = self.callbacks["get_available_versions"]()
            self._task_queue.put(("available_loaded", available))
        except Exception as e:
            self._task_queue.put(("available_load_error", str(e)))

    def _on_install(self):
        """安装按钮回调"""
        version_id = self.version_entry.get().strip()
        if not version_id:
            self.set_status("请输入版本 ID", "error")
            return

        mod_loader = self.modloader_var.get()
        loader_text = f" + {mod_loader}" if mod_loader != "无" else ""
        self.set_status(f"正在安装 {version_id}{loader_text}...", "loading")
        self._set_buttons_enabled(False)

        self._run_in_thread(self._install_version, version_id, mod_loader)

    def _install_version(self, version_id: str, mod_loader: str):
        """安装版本（后台线程）"""
        try:
            success, installed_version_id = self.callbacks["install_version"](version_id, mod_loader)
            self._task_queue.put(("install_done", (version_id, installed_version_id, success)))
        except Exception as e:
            self._task_queue.put(("install_error", str(e)))

    def _on_launch(self):
        """启动按钮回调"""
        if not self.selected_version:
            self.set_status("请先选择一个版本", "error")
            return

        version_id = self.selected_version.split()[0] if " " in self.selected_version else self.selected_version
        self.set_status(f"正在启动 {version_id}...", "loading")
        self.launch_btn.configure(state=ctk.DISABLED)

        self._run_in_thread(self._launch_game, version_id)

    def _on_install_modpack(self):
        """打开整合包安装窗口"""
        ModpackInstallWindow(self, self.callbacks)

    def _launch_game(self, version_id: str):
        """启动游戏（后台线程）"""
        # 启动前自动备份
        if hasattr(self, '_auto_backup_before_launch'):
            self._auto_backup_before_launch(version_id)

        try:
            minimize = self.minimize_var.get()
            success, target_version = self.callbacks["launch_game"](version_id, minimize_after=minimize)
            self._task_queue.put(("launch_done", (version_id, target_version, success)))
        except Exception as e:
            self._task_queue.put(("launch_error", str(e)))

    def _trigger_ach_install(self, version_id: str):
        """处理版本安装相关成就"""
        import re
        from datetime import datetime, timedelta
        from achievement_engine import get_achievement_engine
        engine = get_achievement_engine()
        if not engine:
            return

        installed = self.callbacks.get("get_installed_versions", lambda: [])()
        count = len(installed)
        engine.update_progress("gamer_version_collector", value=count, trigger_type="set")

        has_release = any(v.get("type") == "release" for v in installed)
        has_snapshot = any(v.get("type") == "snapshot" for v in installed)
        engine.check_and_unlock("gamer_cross_era", has_release and has_snapshot)

        if version_id:
            match = re.match(r'(\d+)\.(\d+)', version_id)
            if match:
                major, minor = int(match.group(1)), int(match.group(2))
                if major == 1 and minor <= 12:
                    engine.check_and_unlock("gamer_retro", True)

        if version_id and ('pre' in version_id or 'rc' in version_id or 'snapshot' in version_id):
            try:
                from achievement_engine import ACHIEVEMENTS
            except ImportError:
                pass
            engine.check_and_unlock("gamer_frontier", True)

    # ─── 队列轮询 ─────────────────────────────────────────────

    def _poll_queue(self):
        """轮询任务队列，在主线程中更新UI"""
        if not self._running:
            return

        try:
            processed = 0
            latest_progress = None
            latest_status = ""
            while processed < 100:
                try:
                    task_type, data = self._task_queue.get_nowait()
                    if task_type == "progress_update":
                        current, total, status = data
                        if total > 0:
                            latest_progress = (current, total)
                        if status:
                            latest_status = status
                    else:
                        self._handle_task(task_type, data)
                    processed += 1
                except queue.Empty:
                    break

            if latest_progress:
                current, total = latest_progress
                self.progress_bar.set(current / total)
                self.progress_label.configure(text=f"{current}/{total}")
            if latest_status:
                self.set_status(latest_status, "loading")
        except Exception as e:
            logger.error(f"队列处理错误: {e}")

        # 检测标签页切换
        try:
            current_tab = self.tabview.get()
            prev = getattr(self, '_last_tab', None)
            if current_tab != prev:
                self._last_tab = current_tab
                if current_tab == "💾 备份" and hasattr(self, '_refresh_world_list'):
                    self._refresh_world_list()
        except Exception:
            pass

        self.after(100, self._poll_queue)

    def _handle_task(self, task_type: str, data: Any):
        """处理队列任务"""
        # 备份相关任务
        if hasattr(self, '_handle_backup_task'):
            handled = self._handle_backup_task(task_type, data)
            if handled:
                return

        if task_type == "init_done":
            self.set_status("环境初始化完成", "success")
            self._refresh_versions()
            # 加载服务器版本列表
            self._run_in_thread(self._load_server_versions)

        elif task_type == "init_error":
            self.set_status(f"初始化失败: {data}", "error")

        elif task_type == "installed_loaded":
            installed = data
            self._render_installed_versions(installed)
            self.set_status(
                f"已安装 {len(installed)} 个版本 | 正在获取可用版本...",
                "loading"
            )
            # 异步加载可用版本列表（可能因网络失败）
            self._run_in_thread(self._load_available_versions)

        elif task_type == "available_loaded":
            available = data
            self._render_available_versions(available)
            release_count = len([v for v in available if v.get("type") == "release"])
            snapshot_count = len([v for v in available if v.get("type") == "snapshot"])
            installed_count = len([b["version"] for b in self.version_buttons])
            self.set_status(
                f"已安装 {installed_count} 个 | 正式版 {release_count} 个 | 测试版 {snapshot_count} 个",
                "success"
            )

        elif task_type == "available_load_error":
            installed_count = len([b["version"] for b in self.version_buttons])
            self.set_status(
                f"已安装 {installed_count} 个 | 可用版本列表获取失败（离线模式）",
                "warning"
            )

        elif task_type == "load_error":
            self.set_status(f"加载失败: {data}", "error")

        elif task_type == "install_done":
            version_id, installed_version_id, success = data
            if success:
                display = installed_version_id if installed_version_id != version_id else version_id
                self.set_status(f"{display} 安装成功!", "success")
                self.version_entry.delete(0, ctk.END)
                self._refresh_versions()
                slog.info("version_installed", version=display, requested=version_id)
                self._trigger_ach_install(display)
            else:
                self.set_status(f"{version_id} 安装失败", "error")
                slog.error("version_install_failed", version=version_id)
            self._set_buttons_enabled(True)

        elif task_type == "install_error":
            self.set_status(f"安装错误: {data}", "error")
            self._set_buttons_enabled(True)

        elif task_type == "remove_done":
            version_id, success = data
            if success:
                self.set_status(f"{version_id} 已删除", "success")
                if self.selected_version == version_id:
                    self.selected_version = None
                self._refresh_versions()
            else:
                self.set_status(f"{version_id} 删除失败", "error")

        elif task_type == "remove_error":
            self.set_status(f"删除错误: {data}", "error")

        elif task_type == "launch_done":
            version_id, target_version, success = data
            if success:
                self._running_version_id = target_version or version_id
                self._killed_by_user = False
                self.set_status(f"{version_id} 已启动，等待游戏窗口...", "loading")
                self.kill_btn.configure(state=ctk.NORMAL)
                self._start_launch_animation()
                slog.info("game_launched", version=version_id, target=target_version or version_id)
                self._run_in_thread(self._watch_game_stdout)
                self._run_in_thread(self._watch_game_exit)
                self._trigger_ach("gamer_first_launch")
                self._trigger_ach("gamer_launch_master")
            else:
                self.set_status(f"{version_id} 启动失败", "error")
                slog.error("game_launch_failed", version=version_id)
            self.launch_btn.configure(state=ctk.NORMAL)

        elif task_type == "launch_error":
            self.set_status(f"启动错误: {data}", "error")
            self.launch_btn.configure(state=ctk.NORMAL)

        # ── 服务器相关任务处理 ──
        elif task_type == "server_installed_loaded":
            installed = data
            self._render_server_versions(installed)

        elif task_type == "server_available_loaded":
            available = data
            self._render_server_available_versions(available)
            server_release_count = len(available)
            self.set_status(
                f"服务器可用 {server_release_count} 个正式版",
                "success"
            )

        elif task_type == "server_error":
            self.set_status(f"服务器操作错误: {data}", "error")

        elif task_type == "server_install_done":
            version_id, success = data
            if success:
                self.set_status(f"服务器 {version_id} 安装成功", "success")
                if "get_installed_servers" in self.callbacks:
                    installed = self.callbacks["get_installed_servers"]()
                    self._render_server_versions(installed)
            else:
                self.set_status(f"服务器 {version_id} 安装失败", "error")
            self.server_install_btn.configure(state=ctk.NORMAL)

        elif task_type == "server_install_error":
            self.set_status(f"安装服务器错误: {data}", "error")
            self.server_install_btn.configure(state=ctk.NORMAL)

        elif task_type == "server_start_done":
            version_id, success = data
            if success:
                self.set_status(f"服务器 {version_id} 已启动", "success")
                self.server_stop_btn.configure(state=ctk.NORMAL)
                self.server_log_status_label.configure(text=f"运行中 ({version_id})", text_color=COLORS["success"])
                self._server_online_players = []
                self._update_player_display()
                self.server_mem_label.configure(text="0 MB")
                self._append_server_log(f"[FMCL] 服务器 {version_id} 已启动")
                self._run_in_thread(self._watch_server_exit)
                self._start_mem_monitor()
                self._trigger_ach("server_first_server")
            else:
                self.set_status(f"服务器 {version_id} 启动失败", "error")
                self.server_start_btn.configure(state=ctk.NORMAL)

        elif task_type == "server_start_error":
            self.set_status(f"启动服务器错误: {data}", "error")
            self.server_start_btn.configure(state=ctk.NORMAL)

        elif task_type == "server_log":
            self._append_server_log(data)

        elif task_type == "server_exit":
            exit_code = data
            self.server_stop_btn.configure(state=ctk.DISABLED)
            self.server_start_btn.configure(state=ctk.NORMAL)
            self.server_log_status_label.configure(text=_("server_status_stopped"), text_color=COLORS["text_secondary"])
            self._stop_mem_monitor()
            self._server_online_players = []
            self._update_player_display()
            self.server_mem_label.configure(text="0 MB")
            if exit_code == 0:
                self.set_status(_("server_stopped"), "info")
            else:
                self.set_status(_("server_crashed").format(code=exit_code), "error")

            # 服务器退出后弹窗询问是否正常运行
            self._ask_server_exit_quality(exit_code)

        elif task_type == "server_remove_done":
            version_id, success = data
            if success:
                self.set_status(f"服务器 {version_id} 已删除", "success")
                # 如果删除的是当前选中的，取消选择
                if self.selected_server_version == version_id:
                    self.selected_server_version = None
                # 重新加载服务器列表
                if "get_installed_servers" in self.callbacks:
                    installed = self.callbacks["get_installed_servers"]()
                    self._render_server_versions(installed)
            else:
                self.set_status(f"删除服务器 {version_id} 失败", "error")

        elif task_type == "server_remove_error":
            self.set_status(f"删除服务器错误: {data}", "error")

        elif task_type == "server_join_done":
            version_id, success = data
            self.server_join_btn.configure(state=ctk.NORMAL)
            if success:
                self.set_status(f"正在加入服务器 ({version_id})", "success")
                self._running = True
                self.kill_btn.configure(state=ctk.NORMAL)
                self._start_launch_animation()
                # 加入服务器不监控 stdout（管道未捕获），仅监控进程退出
                self._run_in_thread(self._watch_game_exit)
            else:
                self.set_status(f"启动游戏失败 ({version_id})", "error")

        elif task_type == "server_join_error":
            self.server_join_btn.configure(state=ctk.NORMAL)
            self.set_status(f"加入服务器错误: {data}", "error")

        elif task_type == "game_window_detected":
            self._stop_launch_animation()
            if self.minimize_var.get():
                self.set_status("游戏窗口已出现，启动器已最小化", "success")
                self.iconify()
            else:
                self.set_status("游戏已就绪", "success")

        elif task_type == "game_exited":
            self._stop_launch_animation()
            self.kill_btn.configure(state=ctk.DISABLED)
            self.set_status("游戏已正常退出", "info")
            # 退出后自动备份
            if hasattr(self, '_auto_backup_after_exit'):
                self._auto_backup_after_exit()

        elif task_type == "game_crashed":
            self._stop_launch_animation()
            self.kill_btn.configure(state=ctk.DISABLED)
            exit_code = data["exit_code"]
            crash_files = data["crash_files"]
            self.set_status(f"游戏异常退出 (退出码: {exit_code})", "error")

            # 提取 error_type 和 log_snippet 用于结构化日志
            _error_type = ""
            _log_snippet = ""
            _crash_report_path = crash_files.get("crash_report")
            if _crash_report_path:
                try:
                    from pathlib import Path as _P
                    _content = Path(_crash_report_path).read_text(encoding="utf-8", errors="ignore")
                    import re as _re
                    _match = _re.search(r"(java\.\w+\.\w+Exception|net\.minecraft\.\w+Exception|OutOfMemoryError|StackOverflowError)", _content)
                    if _match:
                        _error_type = _match.group(1)
                    _lines = _content.strip().splitlines()
                    _log_snippet = "\n".join(_lines[-5:]) if len(_lines) > 5 else _content[:500]
                except Exception:
                    pass

            # 从版本ID解析 loader
            _version_id = getattr(self, '_running_version_id', '') or ''
            _loader = ''
            _vl = _version_id.lower()
            if 'forge' in _vl:
                _loader = 'forge'
            elif 'fabric' in _vl:
                _loader = 'fabric'
            elif 'neoforge' in _vl:
                _loader = 'neoforge'

            slog.error("game_crash", exit_code=exit_code,
                       version=_version_id, loader=_loader,
                       error_type=_error_type,
                       has_crash_report="crash_report" in crash_files,
                       has_game_log="game_log" in crash_files,
                       log_snippet=_log_snippet[:300])
            # 恢复最小化的窗口
            try:
                self.deiconify()
            except Exception:
                pass
            self._show_crash_dialog(exit_code, crash_files)

        elif task_type == "progress_update":
            current, total, status = data
            if total > 0:
                self.progress_bar.set(current / total)
                self.progress_label.configure(text=f"{current}/{total}")
            if status:
                self.set_status(status, "loading")

        elif task_type == "connection_ok":
            self.set_status(f"镜像源连接正常: {data}", "success")

        elif task_type == "connection_fail":
            self.set_status(f"镜像源连接失败: {data}", "warning")

        elif task_type == "update_available":
            version, body = data
            self._show_update_dialog(version, body)

        elif task_type == "update_no_new":
            self.set_status("当前已是最新版本", "success")

        elif task_type == "update_check_error":
            self.set_status(f"检查更新失败: {data}", "warning")

        elif task_type == "update_download_progress":
            downloaded, total = data
            if total > 0:
                self.progress_bar.set(downloaded / total)
                self.progress_label.configure(text=f"{downloaded // (1024*1024)}MB / {total // (1024*1024)}MB")

        elif task_type == "update_download_done":
            self.set_status("下载完成，正在启动安装程序...", "success")

        elif task_type == "update_download_error":
            self.set_status(f"更新下载失败: {data}", "error")
            self.progress_bar.set(0)
            self.progress_label.configure(text="")

        elif task_type == "update_install_started":
            self.set_status("正在退出，即将开始安装更新...", "success")
            self.after(500, self.on_closing)

    # ─── 工具方法 ─────────────────────────────────────────────

    def _run_in_thread(self, target: Callable, *args, **kwargs):
        """在后台线程中运行函数"""
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()

    def set_status(self, text: str, status_type: str = "info"):
        """设置状态栏文本"""
        icons = {
            "success": "✅",
            "error": "❌",
            "warning": "⚠️",
            "loading": "⏳",
            "info": "ℹ️",
        }
        colors = {
            "success": COLORS["success"],
            "error": COLORS["accent"],
            "warning": COLORS["warning"],
            "loading": COLORS["text_secondary"],
            "info": COLORS["text_primary"],
        }
        icon = icons.get(status_type, "")
        color = colors.get(status_type, COLORS["text_primary"])
        self.status_label.configure(text=f"{icon} {text}", text_color=color)

    def _set_buttons_enabled(self, enabled: bool):
        """启用/禁用操作按钮"""
        state = ctk.NORMAL if enabled else ctk.DISABLED
        self.install_btn.configure(state=state)

    def update_progress(self, current: int, total: int, status: str = ""):
        """更新进度条（线程安全）"""
        self._task_queue.put(("progress_update", (current, total, status)))

    def _open_resource_manager(self):
        """打开资源管理窗口"""
        if not self.selected_version:
            self.set_status("请先选择一个版本", "error")
            return
        ResourceManagerWindow(self, self.selected_version, self.callbacks)

    def _open_resource_manager_for_version(self, version_id: str):
        """为指定版本打开资源管理窗口"""
        ResourceManagerWindow(self, version_id, self.callbacks)

    def _show_about(self):
        """显示关于对话框"""
        about = ctk.CTkToplevel(self)
        about.title(_("about_title"))
        about.resizable(False, False)
        about.transient(self)
        about.grab_set()
        about.configure(fg_color=COLORS["bg_dark"])

        # 窗口尺寸与居中
        w, h = 460, 390
        about.geometry(f"{w}x{h}")
        about.update_idletasks()
        x = (about.winfo_screenwidth() - w) // 2
        y = (about.winfo_screenheight() - h) // 2
        about.geometry(f"+{x}+{y}")

        # 主容器
        main_frame = ctk.CTkFrame(about, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=30, pady=30)

        # 顶部区域：logo + 标题
        top_frame = ctk.CTkFrame(main_frame, fg_color="transparent")
        top_frame.pack(fill=ctk.X, pady=(0, 15))

        # Logo
        icon_path = os.path.join(
            getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__))),
            "icon.ico",
        )
        icon_displayed = False
        if os.path.exists(icon_path):
            try:
                from PIL import Image as PILImage

                pil_img = PILImage.open(icon_path).resize((64, 64), PILImage.Resampling.LANCZOS)
                ctk_img = ctk.CTkImage(pil_img, size=(80, 80))
                ctk.CTkLabel(top_frame, image=ctk_img, text="").pack(
                    side=ctk.LEFT, padx=(0, 15)
                )
                # 保存对图像的引用以防止被垃圾回收
                setattr(about, '_icon_ref', ctk_img)
                icon_displayed = True
            except Exception:
                pass

        if not icon_displayed:
            ctk.CTkLabel(
                top_frame,
                text="\u26cf",
                font=ctk.CTkFont(size=36),
                text_color=COLORS["accent"],
            ).pack(side=ctk.LEFT, padx=(0, 15))

        # 标题信息
        info_frame = ctk.CTkFrame(top_frame, fg_color="transparent")
        info_frame.pack(side=ctk.LEFT)
        ctk.CTkLabel(
            info_frame,
            text="FMCL",
            font=ctk.CTkFont(size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)
        ctk.CTkLabel(
            info_frame,
            text="Fusion Minecraft Launcher",
            font=ctk.CTkFont(size=10),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W)

        # 分隔线
        ctk.CTkFrame(
            main_frame, height=1, fg_color=COLORS["card_border"]
        ).pack(fill=ctk.X, pady=(0, 15))

        # 系统信息
        info_items = [
            (_("about_version"), _get_fmcl_version()),
            (_("about_python"), platform.python_version()),
            (_("about_system"), f"{platform.system()} {platform.release()}"),
            (_("about_arch"), platform.machine()),
        ]

        info_container = ctk.CTkFrame(main_frame, fg_color="transparent")
        info_container.pack(fill=ctk.X, pady=(0, 15))

        for label_text, value in info_items:
            row = ctk.CTkFrame(info_container, fg_color="transparent")
            row.pack(fill=ctk.X, pady=2)
            ctk.CTkLabel(
                row,
                text=label_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
                width=60,
                anchor=ctk.E,
            ).pack(side=ctk.LEFT)
            ctk.CTkLabel(
                row,
                text=value,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_primary"],
                anchor=ctk.W,
            ).pack(side=ctk.LEFT, padx=(10, 0))

        # 许可证信息
        ctk.CTkLabel(
            main_frame,
            text=_("about_license"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(pady=(0, 10))

        # 底部确定按钮
        ctk.CTkButton(
            main_frame,
            text=_("confirm"),
            width=80,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=about.destroy,
        ).pack(pady=(20, 0))

        about.bind("<Return>", lambda e: about.destroy())
        about.focus_set()

    def _open_launcher_settings(self):
        """打开启动器设置窗口"""
        LauncherSettingsWindow(self, self.callbacks)

    def _open_mod_browser(self, version_id: str):
        """打开模组浏览窗口（从 Modrinth 搜索安装模组）"""
        ModBrowserWindow(self, version_id, self.callbacks)

    def on_closing(self):
        """窗口关闭事件"""
        self._running = False
        self.destroy()
