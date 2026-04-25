"""启动器设置窗口"""
import subprocess
import sys
import threading
import tkinter.messagebox as messagebox
from typing import Dict, Optional, Callable, Any

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _, get_available_languages, set_language, get_current_language


class LauncherSettingsWindow(ctk.CTkToplevel):
    """启动器设置窗口"""

    def __init__(self, parent, callbacks: Dict[str, Callable]):
        super().__init__(fg_color=COLORS["bg_dark"])
        self.callbacks = callbacks
        self.parent = parent

        self.title(_("settings_title"))
        self.geometry("450x620")
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

    def _restart_launcher(self):
        """重启启动器"""
        # 关闭设置窗口
        self.destroy()
        # 获取当前脚本路径
        script = sys.executable
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包环境下
            subprocess.Popen([script])
        else:
            # 开发环境下
            subprocess.Popen([script, 'main.py'])
        # 退出当前进程
        self.parent.quit()

    def _build_ui(self):
        """构建设置界面"""
        container = ctk.CTkFrame(self, fg_color="transparent")
        container.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        # 标题
        title = ctk.CTkLabel(
            container,
            text=_("settings_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title.pack(anchor=ctk.W, pady=(0, 20))

        # 启动后最小化开关
        minimize_frame = ctk.CTkFrame(container, fg_color="transparent")
        minimize_frame.pack(fill=ctk.X, pady=10)

        minimize_label = ctk.CTkLabel(
            minimize_frame,
            text=_("settings_minimize"),
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
            text=_("settings_mirror"),
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

        # ── 界面语言设置 ──
        language_frame = ctk.CTkFrame(container, fg_color="transparent")
        language_frame.pack(fill=ctk.X, pady=10)

        language_label = ctk.CTkLabel(
            language_frame,
            text=_("settings_language"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_primary"],
        )
        language_label.pack(side=ctk.LEFT)

        # 获取可用语言列表
        lang_map = get_available_languages()
        lang_values = list(lang_map.values())
        lang_codes = list(lang_map.keys())

        # 当前语言
        current_lang = get_current_language()
        current_lang_display = lang_map.get(current_lang, lang_map["zh_CN"])

        self.language_var = ctk.StringVar(value=current_lang_display)
        language_menu = ctk.CTkOptionMenu(
            language_frame,
            variable=self.language_var,
            values=lang_values,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
            command=self._on_language_change,
        )
        language_menu.pack(side=ctk.RIGHT)

        # 保存语言代码映射
        self._lang_display_to_code = {v: k for k, v in lang_map.items()}

        # ── 净读 AI 账号 ──
        jdz_section = ctk.CTkFrame(container, fg_color=COLORS["bg_medium"], corner_radius=8)
        jdz_section.pack(fill=ctk.X, pady=(15, 5))

        jdz_title = ctk.CTkLabel(
            jdz_section,
            text=_("netread_ai"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        jdz_title.pack(anchor=ctk.W, padx=12, pady=(10, 5))

        # Token 状态
        _saved_token = self.callbacks.get("get_jdz_token", lambda: None)()
        logged_in = _saved_token is not None
        self.jdz_status_label = ctk.CTkLabel(
            jdz_section,
            text=f"{_('netread_status')}: {_('netread_logged_in') if logged_in else _('netread_not_logged_in')}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["success"] if logged_in else COLORS["text_secondary"],
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
            placeholder_text=_("netread_username_placeholder"),
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
            placeholder_text=_("netread_password_placeholder"),
            width=140,
            show="•",
        )
        self.jdz_pass_entry.pack(side=ctk.LEFT, padx=(0, 5))

        self.jdz_login_btn = ctk.CTkButton(
            login_form,
            text=_("login"),
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_jdz_login,
        )
        self.jdz_login_btn.pack(side=ctk.LEFT, padx=(0, 5))

        self.jdz_logout_btn = ctk.CTkButton(
            login_form,
            text=_("netread_logout"),
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
            text=_("netread_register"),
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
            text=_("settings_download_threads"),
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
        btn_frame = ctk.CTkFrame(container, fg_color="transparent")
        btn_frame.pack(pady=(30, 0), fill=ctk.X)

        # 应用按钮
        apply_btn = ctk.CTkButton(
            btn_frame,
            text=_("settings_apply"),
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._restart_launcher,
        )
        apply_btn.pack(side=ctk.LEFT, padx=(0, 10))

        # 关闭按钮
        close_btn = ctk.CTkButton(
            btn_frame,
            text=_("settings_close"),
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self.destroy,
        )
        close_btn.pack(side=ctk.LEFT)

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

    def _on_language_change(self, lang_display: str):
        """语言切换回调"""
        # 获取语言代码
        lang_code = self._lang_display_to_code.get(lang_display)
        if not lang_code:
            return

        # 切换语言
        success = set_language(lang_code)
        if success:
            # 保存到配置
            if "set_language" in self.callbacks:
                self.callbacks["set_language"](lang_code)

            # 获取语言名称用于显示
            lang_name = get_available_languages().get(lang_code, lang_code)
            self.parent.set_status(
                _("settings_language_changed", lang=lang_name),
                "info"
            )

    def _on_threads_change(self, value):
        """下载线程数滑块变化"""
        threads = int(round(value))
        self.threads_value_label.configure(text=str(threads))
        if "set_download_threads" in self.callbacks:
            self.callbacks["set_download_threads"](threads)
        self.parent.set_status(_("settings_threads", threads=threads), "info")

    def _on_jdz_login(self):
        """净读 AI 登录"""
        username = self.jdz_user_entry.get().strip()
        password = self.jdz_pass_entry.get().strip()
        if not username or not password:
            messagebox.showwarning(_("warning"), _("please_enter_username_password"), parent=self)
            return

        self.jdz_login_btn.configure(state="disabled", text=_("logging_in") + "...")
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
        self.jdz_login_btn.configure(state="normal", text=_("login"))
        self.jdz_status_label.configure(
            text=f"{_('netread_status')}: {_('netread_logged_in')}",
            text_color=COLORS["success"]
        )
        self.parent.set_status(_("netread_login_success"), "success")

    def _jdz_login_fail(self, msg: str):
        self.jdz_login_btn.configure(state="normal", text=_("login"))
        self.jdz_status_label.configure(
            text=f"{_('netread_status')}: {_('login_failed')}",
            text_color=COLORS["error"]
        )
        messagebox.showerror(_("login_failed"), f"{_('netread_login_failed', error=msg)}", parent=self)

    def _on_jdz_logout(self):
        """退出净读 AI"""
        if "set_jdz_token" in self.callbacks:
            self.callbacks["set_jdz_token"](None)
        self.jdz_status_label.configure(
            text=f"{_('netread_status')}: {_('netread_not_logged_in')}",
            text_color=COLORS["text_secondary"]
        )
        self.jdz_user_entry.delete(0, "end")
        self.jdz_pass_entry.delete(0, "end")
        self.parent.set_status(_("netread_logged_out"), "info")
