"""启动器设置窗口"""
import threading
import tkinter.messagebox as messagebox
from typing import Dict, Optional, Callable, Any

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY


class LauncherSettingsWindow(ctk.CTkToplevel):
    """启动器设置窗口"""

    def __init__(self, parent, callbacks: Dict[str, Callable]):
        super().__init__(fg_color=COLORS["bg_dark"])
        self.callbacks = callbacks
        self.parent = parent

        self.title("启动器设置")
        self.geometry("450x580")
        self.resizable(False, False)
        self.grab_set()

        self._build_ui()

    def destroy(self):
        """销毁窗口，先处理 CTkSlider 的 bug"""
        if hasattr(self, '_threads_slider'):
            try:
                if hasattr(self._threads_slider, '_variable'):
                    self._threads_slider._variable = None
            except Exception:
                pass
        super().destroy()

    def _build_ui(self):
        """构建设置界面"""
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        # 标题
        title = ctk.CTkLabel(
            container,
            text="⚙ 启动器设置",
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title.pack(anchor=ctk.W, pady=(0, 20))

        # 启动后最小化开关
        minimize_frame = ctk.CTkFrame(container, fg_color="transparent")
        minimize_frame.pack(fill=ctk.X, pady=10)

        minimize_label = ctk.CTkLabel(
            minimize_frame,
            text="🔽 启动后最小化",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_primary"],
        )
        minimize_label.pack(side=ctk.LEFT)

        self.minimize_var = ctk.BooleanVar(value=self.callbacks.get("get_minimize_on_game_launch", lambda: False)())
        minimize_switch = ctk.CTkSwitch(
            minimize_frame,
            text="",
            variable=self.minimize_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["text_secondary"],
            progress_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            command=self._on_minimize_toggle,
        )
        minimize_switch.pack(side=ctk.RIGHT)

        # 国内镜像源开关
        mirror_frame = ctk.CTkFrame(container, fg_color="transparent")
        mirror_frame.pack(fill=ctk.X, pady=10)

        mirror_label = ctk.CTkLabel(
            mirror_frame,
            text="🇨🇳 使用国内镜像源",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_primary"],
        )
        mirror_label.pack(side=ctk.LEFT)

        self.mirror_var = ctk.BooleanVar(value=self.callbacks.get("get_mirror_enabled", lambda: True)())
        mirror_switch = ctk.CTkSwitch(
            mirror_frame,
            text="",
            variable=self.mirror_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["text_secondary"],
            progress_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            command=self._on_mirror_toggle,
        )
        mirror_switch.pack(side=ctk.RIGHT)

        # ── 净读 AI 账号 ──
        jdz_section = ctk.CTkFrame(container, fg_color=COLORS["bg_medium"], corner_radius=8)
        jdz_section.pack(fill=ctk.X, pady=(15, 5))

        jdz_title = ctk.CTkLabel(
            jdz_section,
            text="🤖 净读 AI（崩溃智能分析）",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        jdz_title.pack(anchor=ctk.W, padx=12, pady=(10, 5))

        # Token 状态
        _saved_token = self.callbacks.get("get_jdz_token", lambda: None)()
        token_status = "已登录" if _saved_token else "未登录"
        token_color = COLORS["success"] if _saved_token else COLORS["text_secondary"]
        self.jdz_status_label = ctk.CTkLabel(
            jdz_section,
            text=f"状态: {token_status}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=token_color,
        )
        self.jdz_status_label.pack(anchor=ctk.W, padx=12, pady=(0, 5))

        # 登录表单
        login_form = ctk.CTkFrame(jdz_section, fg_color="transparent")
        login_form.pack(fill=ctk.X, padx=12, pady=(0, 10))

        self.jdz_user_entry = ctk.CTkEntry(
            login_form,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            placeholder_text="用户名",
            width=140,
        )
        self.jdz_user_entry.pack(side=ctk.LEFT, padx=(0, 5))

        self.jdz_pass_entry = ctk.CTkEntry(
            login_form,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            placeholder_text="密码",
            width=140,
            show="•",
        )
        self.jdz_pass_entry.pack(side=ctk.LEFT, padx=(0, 5))

        self.jdz_login_btn = ctk.CTkButton(
            login_form,
            text="登录",
            width=50,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_jdz_login,
        )
        self.jdz_login_btn.pack(side=ctk.LEFT, padx=(0, 5))

        self.jdz_logout_btn = ctk.CTkButton(
            login_form,
            text="退出",
            width=50,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_jdz_logout,
        )
        self.jdz_logout_btn.pack(side=ctk.LEFT)

        # 注册链接（单独一行）
        import webbrowser
        register_btn = ctk.CTkButton(
            jdz_section,
            text="没有账号？去注册",
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            anchor="w",
            command=lambda: webbrowser.open("https://jingdu.qzz.io/register"),
        )
        register_btn.pack(anchor="w", padx=12, pady=(0, 10))

        # 下载线程数滑块
        threads_frame = ctk.CTkFrame(container, fg_color="transparent")
        threads_frame.pack(fill=ctk.X, pady=10)

        threads_label = ctk.CTkLabel(
            threads_frame,
            text="⚡ 下载线程数",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_primary"],
        )
        threads_label.pack(side=ctk.LEFT)

        self.threads_value_label = ctk.CTkLabel(
            threads_frame,
            text=str(self.callbacks.get("get_download_threads", lambda: 4)()),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["accent"],
            width=30,
        )
        self.threads_value_label.pack(side=ctk.RIGHT)

        threads_slider = ctk.CTkSlider(
            container,
            from_=1,
            to=255,
            number_of_steps=254,
            command=self._on_threads_change,
            fg_color=COLORS["bg_light"],
            button_color=COLORS["accent"],
            button_hover_color=COLORS["accent_hover"],
            progress_color=COLORS["accent"],
            width=380,
        )
        threads_slider.set(self.callbacks.get("get_download_threads", lambda: 4)())
        threads_slider.pack(fill=ctk.X, pady=(5, 0))
        self._threads_slider = threads_slider

        # 关闭按钮
        close_btn = ctk.CTkButton(
            container,
            text="关闭",
            width=120,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self.destroy,
        )
        close_btn.pack(pady=(30, 0))

    def _on_minimize_toggle(self):
        """启动后最小化开关切换"""
        enabled = self.minimize_var.get()
        if "set_minimize_on_game_launch" in self.callbacks:
            self.callbacks["set_minimize_on_game_launch"](enabled)
        # 同步主窗口变量
        self.parent.minimize_var.set(enabled)
        self.parent.set_status(
            f"游戏启动后最小化: {'已启用' if enabled else '已禁用'}",
            "success" if enabled else "info"
        )

    def _on_mirror_toggle(self):
        """镜像源开关切换"""
        enabled = self.mirror_var.get()
        if "set_mirror_enabled" in self.callbacks:
            self.callbacks["set_mirror_enabled"](enabled)
        # 同步主窗口变量
        self.parent.mirror_var.set(enabled)
        self.parent.set_status(
            f"国内镜像源: {'已启用' if enabled else '已禁用'}",
            "success" if enabled else "info"
        )

    def _on_threads_change(self, value):
        """下载线程数滑块变化"""
        threads = int(round(value))
        self.threads_value_label.configure(text=str(threads))
        if "set_download_threads" in self.callbacks:
            self.callbacks["set_download_threads"](threads)
        self.parent.set_status(f"下载线程数: {threads}", "info")

    def _on_jdz_login(self):
        """净读 AI 登录"""
        username = self.jdz_user_entry.get().strip()
        password = self.jdz_pass_entry.get().strip()
        if not username or not password:
            messagebox.showwarning("提示", "请输入用户名和密码", parent=self)
            return

        self.jdz_login_btn.configure(state="disabled", text="登录中...")
        self.update()

        def _do_login():
            import urllib.request
            import urllib.error
            import json
            try:
                req_data = json.dumps({"username": username, "password": password}).encode("utf-8")
                req = urllib.request.Request(
                    "https://jingdu.qzz.io/api/auth/login",
                    data=req_data,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "FMCL/1.0 (Minecraft Launcher; crash-analyzer)",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                token = result.get("token")
                if token:
                    if "set_jdz_token" in self.callbacks:
                        self.callbacks["set_jdz_token"](token)
                    self.after(0, lambda: self._jdz_login_success(token))
                else:
                    self.after(0, lambda: self._jdz_login_fail("未获取到 Token"))
            except urllib.error.HTTPError as e:
                _code = e.code
                body = ""
                try:
                    body = e.read().decode("utf-8", errors="ignore")
                except Exception:
                    pass
                _err_msg = f"HTTP {_code}: {body[:100]}"
                self.after(0, lambda: self._jdz_login_fail(_err_msg))
            except Exception as e:
                _err_msg = str(e)
                self.after(0, lambda: self._jdz_login_fail(_err_msg))

        threading.Thread(target=_do_login, daemon=True).start()

    def _jdz_login_success(self, token: str):
        self.jdz_login_btn.configure(state="normal", text="登录")
        self.jdz_status_label.configure(text="状态: 已登录", text_color=COLORS["success"])
        self.parent.set_status("净读 AI 登录成功", "success")

    def _jdz_login_fail(self, msg: str):
        self.jdz_login_btn.configure(state="normal", text="登录")
        self.jdz_status_label.configure(text="状态: 登录失败", text_color=COLORS["error"])
        messagebox.showerror("登录失败", f"净读 AI 登录失败:\n{msg}", parent=self)

    def _on_jdz_logout(self):
        """退出净读 AI"""
        if "set_jdz_token" in self.callbacks:
            self.callbacks["set_jdz_token"](None)
        self.jdz_status_label.configure(text="状态: 未登录", text_color=COLORS["text_secondary"])
        self.jdz_user_entry.delete(0, "end")
        self.jdz_pass_entry.delete(0, "end")
        self.parent.set_status("净读 AI 已退出登录", "info")
