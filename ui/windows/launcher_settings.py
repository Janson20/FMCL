"""启动器设置窗口"""
import subprocess
import sys
import threading
import tkinter.messagebox as messagebox
from typing import Dict, Optional, Callable, Any

import customtkinter as ctk
from tkinter import filedialog

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _, get_available_languages, set_language, get_current_language


class LauncherSettingsWindow(ctk.CTkToplevel):
    """启动器设置窗口"""

    def __init__(self, parent, callbacks: Dict[str, Callable]):
        super().__init__(fg_color=COLORS["bg_dark"])
        self.callbacks = callbacks
        self.parent = parent

        self.title(_("settings_title"))
        self.geometry("520x900")
        self.resizable(False, False)
        try:
            self.grab_set()
        except Exception:
            pass

        self._settings_theme_refs = []
        self._build_ui()

        # 窗口获得焦点时刷新插件信息
        self.bind("<FocusIn>", lambda e: self._refresh_plugin_info())

    def _r(self, widget, **mapping):
        """注册组件到主题刷新列表"""
        self._settings_theme_refs.append((widget, mapping))
        return widget

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
        # 标题
        title = ctk.CTkLabel(
            self,
            text=_("settings_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title.pack(anchor=ctk.W, padx=20, pady=(20, 0))

        # 标签页
        tabview = ctk.CTkTabview(self, fg_color=COLORS["bg_dark"], segmented_button_fg_color=COLORS["bg_medium"],
                                 segmented_button_selected_color=COLORS["accent"],
                                 segmented_button_unselected_color=COLORS["bg_medium"],
                                 segmented_button_selected_hover_color=COLORS["accent_hover"])
        tabview.pack(fill=ctk.BOTH, expand=True, padx=20, pady=(10, 5))
        self._r(tabview, fg_color="bg_dark", segmented_button_fg_color="bg_medium",
                segmented_button_selected_color="accent",
                segmented_button_unselected_color="bg_medium",
                segmented_button_selected_hover_color="accent_hover")

        tab_launcher = tabview.add(_("settings_tab_launcher"))
        tab_account = tabview.add(_("settings_tab_account"))
        tab_ai = tabview.add(_("settings_tab_ai"))
        tab_plugin = tabview.add(_("settings_tab_plugin"))

        # ── 标签页1: 启动器功能 ──
        container = ctk.CTkScrollableFrame(tab_launcher, fg_color="transparent")
        container.pack(fill=ctk.BOTH, expand=True, padx=10, pady=10)

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
        self._r(minimize_label, text_color="text_primary")

        self.minimize_var = ctk.BooleanVar(value=self.callbacks.get("get_minimize_on_game_launch", lambda: False)())
        self.minimize_switch = ctk.CTkSwitch(
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
        self.minimize_switch.pack(side=ctk.RIGHT)
        self._r(self.minimize_switch, fg_color="accent", button_color="text_primary",
                button_hover_color="text_secondary", progress_color="accent_hover")

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
        self._r(mirror_label, text_color="text_primary")

        self.mirror_var = ctk.BooleanVar(value=self.callbacks.get("get_mirror_enabled", lambda: True)())
        self.mirror_switch = ctk.CTkSwitch(
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
        self.mirror_switch.pack(side=ctk.RIGHT)
        self._r(self.mirror_switch, fg_color="accent", button_color="text_primary",
                button_hover_color="text_secondary", progress_color="accent_hover")

        # ── Java 运行时设置 ──
        java_section = ctk.CTkFrame(container, fg_color=COLORS["bg_medium"], corner_radius=8)
        java_section.pack(fill=ctk.X, pady=(15, 5))

        java_title = ctk.CTkLabel(
            java_section,
            text=_("settings_java_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        java_title.pack(anchor=ctk.W, padx=12, pady=(10, 5))

        # Java 选择模式
        java_mode_frame = ctk.CTkFrame(java_section, fg_color="transparent")
        java_mode_frame.pack(fill=ctk.X, padx=12, pady=5)

        java_mode_label = ctk.CTkLabel(
            java_mode_frame,
            text=_("settings_java_mode"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        )
        java_mode_label.pack(side=ctk.LEFT)

        self._java_mode_options = [
            _("settings_java_mode_auto"),
            _("settings_java_mode_scan"),
            _("settings_java_mode_custom"),
        ]
        self._java_mode_map = {
            _("settings_java_mode_auto"): "auto",
            _("settings_java_mode_scan"): "scan",
            _("settings_java_mode_custom"): "custom",
        }

        current_mode = self.callbacks.get("get_java_mode", lambda: "auto")()
        current_mode_display = (
            _("settings_java_mode_scan") if current_mode == "scan"
            else _("settings_java_mode_custom") if current_mode == "custom"
            else _("settings_java_mode_auto")
        )

        self.java_mode_var = ctk.StringVar(value=current_mode_display)
        self.java_mode_menu = ctk.CTkOptionMenu(
            java_mode_frame,
            variable=self.java_mode_var,
            values=self._java_mode_options,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
            command=self._on_java_mode_change,
        )
        self.java_mode_menu.pack(side=ctk.RIGHT)

        # 自定义路径输入（仅 custom 模式可见）
        self._java_custom_frame = ctk.CTkFrame(java_section, fg_color="transparent")
        self.java_custom_entry = ctk.CTkEntry(
            self._java_custom_frame,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            placeholder_text=_("settings_java_custom_placeholder"),
            width=280,
        )
        self.java_custom_entry.pack(side=ctk.LEFT, padx=(0, 5))

        self.java_custom_browse_btn = ctk.CTkButton(
            self._java_custom_frame,
            text=_("settings_java_custom_browse"),
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_java_custom_browse,
        )
        self.java_custom_browse_btn.pack(side=ctk.LEFT)

        saved_custom = self.callbacks.get("get_java_custom_path", lambda: None)() or ""
        self.java_custom_entry.insert(0, saved_custom)

        if current_mode == "custom":
            self._java_custom_frame.pack(fill=ctk.X, padx=12, pady=(5, 10))
        else:
            self._java_custom_frame.pack_forget()

        # 扫描列表区域（仅 scan 模式可见）
        self._java_scan_frame = ctk.CTkScrollableFrame(
            java_section,
            fg_color=COLORS["bg_dark"],
            height=140,
        )

        self._java_scan_list_label = ctk.CTkLabel(
            self._java_scan_frame,
            text=_("settings_java_scan_none"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._java_scan_list_label.pack(pady=20, padx=12)

        self._java_scan_runtimes = []
        self._java_scan_selected_var = ctk.StringVar(value="")

        self._java_scan_refresh_btn = ctk.CTkButton(
            java_section,
            text=_("settings_java_scan_refresh"),
            height=26,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_java_scan_refresh,
        )

        if current_mode == "scan":
            self._java_scan_frame.pack(fill=ctk.X, padx=12, pady=(5, 5))
            self._java_scan_refresh_btn.pack(anchor=ctk.W, padx=12, pady=(0, 10))
            self._populate_java_scan_list()
        else:
            self._java_scan_frame.pack_forget()
            self._java_scan_refresh_btn.pack_forget()

        # 主题注册
        self._r(java_section, fg_color="bg_medium")
        self._r(java_title, text_color="text_primary")
        self._r(java_mode_label, text_color="text_primary")
        self._r(self.java_mode_menu, fg_color="bg_dark", button_color="bg_light",
                button_hover_color="card_border", dropdown_fg_color="bg_medium",
                dropdown_hover_color="bg_light")
        self._r(self.java_custom_entry, fg_color="bg_dark", border_color="card_border",
                text_color="text_primary")
        self._r(self.java_custom_browse_btn, fg_color="bg_light", hover_color="card_border")
        self._r(self._java_scan_frame, fg_color="bg_dark")
        self._r(self._java_scan_list_label, text_color="text_secondary")
        self._r(self._java_scan_refresh_btn, fg_color="bg_light", hover_color="card_border")

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

        # ── 主题设置 ──
        theme_section = ctk.CTkFrame(container, fg_color=COLORS["bg_medium"], corner_radius=8)
        theme_section.pack(fill=ctk.X, pady=(15, 5))

        theme_title = ctk.CTkLabel(
            theme_section,
            text=_("settings_theme"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        theme_title.pack(anchor=ctk.W, padx=12, pady=(10, 5))

        # 主题选择
        theme_select_frame = ctk.CTkFrame(theme_section, fg_color="transparent")
        theme_select_frame.pack(fill=ctk.X, padx=12, pady=5)

        theme_label = ctk.CTkLabel(
            theme_select_frame,
            text=_("settings_theme_select"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        )
        theme_label.pack(side=ctk.LEFT)

        # 获取主题引擎
        theme_engine = self.callbacks.get("get_theme_engine", lambda: None)()
        available_themes = []
        theme_names = []
        if theme_engine:
            available_themes = theme_engine.get_available_themes()
            theme_names = [t["name"] for t in available_themes]
        if not theme_names:
            theme_names = ["default"]
            available_themes = [{"name": "default", "source": "preset"}]

        current_theme = self.callbacks.get("get_theme_name", lambda: "default")()

        # 主题名称显示映射
        theme_display_map = {}
        theme_display_list = []
        for t in available_themes:
            display = t["name"]
            if t.get("source") == "user":
                display = f"{t['name']} {_('settings_theme_user_tag')}"
            theme_display_list.append(display)
            theme_display_map[display] = t["name"]

        self._theme_display_to_name = theme_display_map

        current_display = current_theme
        if current_theme in theme_names:
            for display, name in theme_display_map.items():
                if name == current_theme:
                    current_display = display
                    break

        self.theme_var = ctk.StringVar(value=current_display)
        self.theme_menu = ctk.CTkOptionMenu(
            theme_select_frame,
            variable=self.theme_var,
            values=theme_display_list,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_dark"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
            command=self._on_theme_change,
        )
        self.theme_menu.pack(side=ctk.RIGHT)

        # 导入主题按钮
        import_theme_btn = ctk.CTkButton(
            theme_section,
            text=_("settings_theme_import"),
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_import_theme,
        )
        import_theme_btn.pack(anchor=ctk.W, padx=12, pady=(5, 2))

        # 自定义强调色
        accent_frame = ctk.CTkFrame(theme_section, fg_color="transparent")
        accent_frame.pack(fill=ctk.X, padx=12, pady=5)

        accent_label = ctk.CTkLabel(
            accent_frame,
            text=_("settings_accent_color"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        )
        accent_label.pack(side=ctk.LEFT)

        saved_accent = self.callbacks.get("get_accent_color", lambda: None)()
        self.accent_var = ctk.StringVar(value=saved_accent or "")
        accent_entry = ctk.CTkEntry(
            accent_frame,
            textvariable=self.accent_var,
            width=100,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["card_border"],
            placeholder_text="#e94560",
        )
        accent_entry.pack(side=ctk.RIGHT, padx=(5, 0))

        accent_apply_btn = ctk.CTkButton(
            accent_frame,
            text=_("settings_apply"),
            width=50,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_accent_apply,
        )
        accent_apply_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        # 随机强调色按钮
        random_accent_btn = ctk.CTkButton(
            accent_frame,
            text=_("settings_accent_random"),
            width=50,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_random_accent,
        )
        random_accent_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        # 版本动态主题开关
        dynamic_frame = ctk.CTkFrame(theme_section, fg_color="transparent")
        dynamic_frame.pack(fill=ctk.X, padx=12, pady=(5, 10))

        dynamic_label = ctk.CTkLabel(
            dynamic_frame,
            text=_("settings_dynamic_theme"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        )
        dynamic_label.pack(side=ctk.LEFT)

        self.dynamic_var = ctk.BooleanVar(value=self.callbacks.get("get_dynamic_version_theme", lambda: False)())
        dynamic_switch = ctk.CTkSwitch(
            dynamic_frame,
            text="",
            variable=self.dynamic_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["text_secondary"],
            progress_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            command=self._on_dynamic_toggle,
        )
        dynamic_switch.pack(side=ctk.RIGHT)

        dynamic_hint = ctk.CTkLabel(
            theme_section,
            text=_("settings_dynamic_theme_hint"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        dynamic_hint.pack(anchor=ctk.W, padx=12, pady=(0, 10))

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

        # ── 标签页2: 账户管理 ──
        account_container = ctk.CTkScrollableFrame(tab_account, fg_color="transparent")
        account_container.pack(fill=ctk.BOTH, expand=True, padx=10, pady=10)

        # ── Minecraft 账号管理 ──
        mc_account_section = ctk.CTkFrame(account_container, fg_color=COLORS["bg_medium"], corner_radius=8)
        mc_account_section.pack(fill=ctk.X, pady=(5, 5))
        self._r(mc_account_section, fg_color="bg_medium")

        mc_account_title = ctk.CTkLabel(
            mc_account_section,
            text=_("account_manager_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        mc_account_title.pack(anchor=ctk.W, padx=12, pady=(10, 5))
        self._r(mc_account_title, text_color="text_primary")

        mc_account_desc = ctk.CTkLabel(
            mc_account_section,
            text=_("account_manager_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            wraplength=440,
        )
        mc_account_desc.pack(anchor=ctk.W, padx=12, pady=(0, 10))
        self._r(mc_account_desc, text_color="text_secondary")

        self._mc_account_quick_info = ctk.CTkLabel(
            mc_account_section,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["accent"],
        )
        self._mc_account_quick_info.pack(anchor=ctk.W, padx=12, pady=(0, 5))
        self._r(self._mc_account_quick_info, text_color="accent")

        mc_account_btn = ctk.CTkButton(
            mc_account_section,
            text=_("account_sidebar_manage"),
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_open_account_manager,
        )
        mc_account_btn.pack(anchor=ctk.W, padx=12, pady=(5, 10))
        self._r(mc_account_btn, fg_color="accent", hover_color="accent_hover")

        # 更新快速信息
        self._update_mc_account_quick_info()

        jdz_section = ctk.CTkFrame(account_container, fg_color=COLORS["bg_medium"], corner_radius=8)
        jdz_section.pack(fill=ctk.X, pady=(5, 5))

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
        self._login_form = ctk.CTkFrame(jdz_section, fg_color="transparent")
        self._login_form.pack(fill=ctk.X, padx=12, pady=(0, 10))

        ctk.CTkLabel(
            self._login_form,
            text=_("netread_username_placeholder"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(0, 2))

        self.jdz_user_entry = ctk.CTkEntry(
            self._login_form,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
        )
        self.jdz_user_entry.pack(fill=ctk.X, pady=(0, 8))

        ctk.CTkLabel(
            self._login_form,
            text=_("netread_password_placeholder"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(0, 2))

        self.jdz_pass_entry = ctk.CTkEntry(
            self._login_form,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            show="•",
        )
        self.jdz_pass_entry.pack(fill=ctk.X, pady=(0, 8))

        btn_row = ctk.CTkFrame(self._login_form, fg_color="transparent")
        btn_row.pack(fill=ctk.X)

        self.jdz_login_btn = ctk.CTkButton(
            btn_row,
            text=_("login"),
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_jdz_login,
        )
        self.jdz_login_btn.pack(side=ctk.LEFT, padx=(0, 5))

        self.jdz_logout_btn = ctk.CTkButton(
            btn_row,
            text=_("netread_logout"),
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_jdz_logout,
        )
        self.jdz_logout_btn.pack(side=ctk.LEFT)

        # 账户信息面板（登录后显示）
        self._account_info_frame = ctk.CTkFrame(jdz_section, fg_color="transparent")

        self._account_info_labels = {}
        self._account_info_label_widgets = []
        info_fields = [
            ("username", _("account_info_username")),
            ("uuid", _("account_info_uuid")),
            ("level", _("account_info_level")),
            ("description", _("account_info_description")),
            ("ai_credits", _("account_info_ai_credits")),
        ]
        for key, label_text in info_fields:
            row = ctk.CTkFrame(self._account_info_frame, fg_color="transparent")
            row.pack(fill=ctk.X, padx=12, pady=1)
            key_label = ctk.CTkLabel(
                row,
                text=label_text + ":",
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
            )
            key_label.pack(side=ctk.LEFT)
            self._account_info_label_widgets.append(key_label)
            self._r(key_label, text_color="text_secondary")
            value_label = ctk.CTkLabel(
                row,
                text="-",
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_primary"],
            )
            value_label.pack(side=ctk.LEFT, padx=(5, 0))
            self._account_info_labels[key] = value_label
            self._r(value_label, text_color="text_primary")

        # 刷新 + 退出按钮行
        account_btn_row = ctk.CTkFrame(self._account_info_frame, fg_color="transparent")
        account_btn_row.pack(fill=ctk.X, padx=12, pady=(8, 5))

        self._account_refresh_btn = ctk.CTkButton(
            account_btn_row,
            text=_("account_refresh"),
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_account_refresh,
        )
        self._account_refresh_btn.pack(side=ctk.LEFT, padx=(0, 5))

        self._account_logout_btn = ctk.CTkButton(
            account_btn_row,
            text=_("netread_logout"),
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_jdz_logout,
        )
        self._account_logout_btn.pack(side=ctk.LEFT)

        # 注册链接
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
        register_btn.pack(anchor="w", padx=12, pady=(5, 10))

        # 初始化显示状态
        if logged_in:
            self._login_form.pack_forget()
            self._account_info_frame.pack(fill=ctk.X, padx=12, pady=(0, 10))
            self.after(100, self._on_account_refresh)

        # ── 标签页3: AI 模型配置 ──
        self._build_ai_tab(tab_ai)
        self._build_plugin_tab(tab_plugin)

        # ── 底部按钮 ──
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(pady=(0, 15), fill=ctk.X, padx=20)

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

        # 注册所有主题依赖组件
        self._r(title, text_color="text_primary")
        self._r(language_label, text_color="text_primary")
        self._r(language_menu, fg_color="bg_medium", button_color="bg_light",
                button_hover_color="card_border", dropdown_fg_color="bg_medium",
                dropdown_hover_color="bg_light")
        self._r(theme_section, fg_color="bg_medium")
        self._r(theme_title, text_color="text_primary")
        self._r(theme_label, text_color="text_primary")
        self._r(import_theme_btn, fg_color="bg_light", hover_color="card_border")
        self._r(self.theme_menu, fg_color="bg_dark", button_color="bg_light",
                button_hover_color="card_border", dropdown_fg_color="bg_medium",
                dropdown_hover_color="bg_light")
        self._r(accent_label, text_color="text_primary")
        self._r(accent_entry, fg_color="bg_dark", border_color="card_border")
        self._r(accent_apply_btn, fg_color="accent", hover_color="accent_hover")
        self._r(random_accent_btn, fg_color="bg_light", hover_color="card_border")
        self._r(dynamic_label, text_color="text_primary")
        self._r(dynamic_switch, fg_color="accent", button_color="text_primary",
                button_hover_color="text_secondary", progress_color="accent_hover")
        self._r(dynamic_hint, text_color="text_secondary")
        self._r(threads_label, text_color="text_primary")
        self._r(self.threads_value_label, text_color="accent")
        self._r(self._threads_slider, fg_color="bg_light", button_color="accent",
                button_hover_color="accent_hover", progress_color="accent")
        self._r(jdz_section, fg_color="bg_medium")
        self._r(jdz_title, text_color="text_primary")
        self._r(self.jdz_user_entry, fg_color="bg_dark", border_color="card_border",
                text_color="text_primary")
        self._r(self.jdz_pass_entry, fg_color="bg_dark", border_color="card_border",
                text_color="text_primary")
        self._r(self.jdz_login_btn, fg_color="accent", hover_color="accent_hover")
        self._r(self.jdz_logout_btn, fg_color="bg_light", hover_color="card_border")
        self._r(self._account_refresh_btn, fg_color="bg_light", hover_color="card_border")
        self._r(self._account_logout_btn, fg_color="bg_light", hover_color="card_border")
        self._r(register_btn, fg_color="accent", hover_color="accent_hover",
                text_color="text_primary")
        self._r(apply_btn, fg_color="accent", hover_color="accent_hover")
        self._r(close_btn, fg_color="bg_light", hover_color="card_border")

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
        try:
            self.parent._trigger_ach("advanced_mirror")
        except Exception:
            pass

    def _on_language_change(self, lang_display: str):
        """语言切换回调"""
        lang_code = self._lang_display_to_code.get(lang_display)
        if not lang_code:
            return

        success = set_language(lang_code)
        if success:
            if "set_language" in self.callbacks:
                self.callbacks["set_language"](lang_code)

            lang_name = get_available_languages().get(lang_code, lang_code)
            self.parent.set_status(
                _("settings_language_changed", lang=lang_name),
                "info"
            )
            try:
                self.parent._trigger_ach("advanced_polyglot")
            except Exception:
                pass

    def _on_threads_change(self, value):
        """下载线程数滑块变化"""
        threads = int(round(value))
        self.threads_value_label.configure(text=str(threads))
        if "set_download_threads" in self.callbacks:
            self.callbacks["set_download_threads"](threads)
        self.parent.set_status(_("settings_threads", threads=threads), "info")
        try:
            self.parent._check_ach("advanced_multithread", threads > 1)
        except Exception:
            pass

    def _on_theme_change(self, display_name: str):
        """主题切换回调"""
        theme_name = self._theme_display_to_name.get(display_name, display_name)
        if "set_theme_name" in self.callbacks:
            self.callbacks["set_theme_name"](theme_name)
        self._refresh_theme()
        self.parent.set_status(_("settings_theme_changed", theme=theme_name), "info")
        try:
            self.parent._trigger_ach("personalize_theme_master")
        except Exception:
            pass

    def _on_import_theme(self):
        """导入主题文件回调"""
        file_path = filedialog.askopenfilename(
            title=_("settings_theme_import_title"),
            filetypes=[("JSON Theme", "*.json"), ("All Files", "*.*")],
            parent=self,
        )
        if not file_path:
            return
        theme_engine = self.callbacks.get("get_theme_engine", lambda: None)()
        if not theme_engine:
            return
        success, msg = theme_engine.import_theme_from_file(file_path)
        if success:
            self._refresh_theme_list()
            self.parent.set_status(msg, "success")
            try:
                self.parent._trigger_ach("personalize_import_theme")
            except Exception:
                pass
        else:
            self.parent.set_status(msg, "error")

    def _on_accent_apply(self):
        """应用自定义强调色"""
        color = self.accent_var.get().strip()
        if not color:
            self.parent.set_status(_("settings_accent_cleared"), "info")
            if "set_accent_color" in self.callbacks:
                self.callbacks["set_accent_color"](None)
            self._refresh_theme()
            return
        if not color.startswith("#") or len(color) != 7:
            self.parent.set_status(_("settings_accent_invalid"), "error")
            return
        try:
            int(color[1:], 16)
        except ValueError:
            self.parent.set_status(_("settings_accent_invalid"), "error")
            return
        if "set_accent_color" in self.callbacks:
            self.callbacks["set_accent_color"](color)
        self._refresh_theme()
        self.parent.set_status(_("settings_accent_applied"), "success")
        try:
            self.parent._trigger_ach("personalize_my_color")
        except Exception:
            pass

    def _on_random_accent(self):
        """随机生成强调色"""
        from ui.theme_engine import ThemeEngine
        color = ThemeEngine.generate_random_accent()
        self.accent_var.set(color)
        if "set_accent_color" in self.callbacks:
            self.callbacks["set_accent_color"](color)
        self._refresh_theme()
        self.parent.set_status(_("settings_accent_random_applied", color=color), "success")
        try:
            self.parent._trigger_ach("personalize_my_color")
        except Exception:
            pass

    def _on_dynamic_toggle(self):
        """版本动态主题开关切换"""
        enabled = self.dynamic_var.get()
        if "set_dynamic_version_theme" in self.callbacks:
            self.callbacks["set_dynamic_version_theme"](enabled)
        status_key = "settings_dynamic_enabled" if enabled else "settings_dynamic_disabled"
        self.parent.set_status(_(status_key), "info")

    def _on_java_mode_change(self, display_name: str):
        mode = self._java_mode_map.get(display_name, "auto")
        if "set_java_mode" in self.callbacks:
            self.callbacks["set_java_mode"](mode)

        if mode == "custom":
            self._java_custom_frame.pack(fill=ctk.X, padx=12, pady=(5, 10))
            self._java_scan_frame.pack_forget()
            self._java_scan_refresh_btn.pack_forget()
        elif mode == "scan":
            self._java_custom_frame.pack_forget()
            self._java_scan_frame.pack(fill=ctk.X, padx=12, pady=(5, 5))
            self._java_scan_refresh_btn.pack(anchor=ctk.W, padx=12, pady=(0, 10))
            self._populate_java_scan_list()
        else:
            self._java_custom_frame.pack_forget()
            self._java_scan_frame.pack_forget()
            self._java_scan_refresh_btn.pack_forget()

        self.parent.set_status(_("settings_java_changed", mode=display_name), "info")

    def _on_java_custom_browse(self):
        file_path = filedialog.askopenfilename(
            title="选择 Java 可执行文件",
            filetypes=[
                ("Java Executable", "java.exe" if sys.platform == "win32" else "java"),
                ("All Files", "*.*"),
            ],
            parent=self,
        )
        if file_path:
            self.java_custom_entry.delete(0, "end")
            self.java_custom_entry.insert(0, file_path)
            if "set_java_custom_path" in self.callbacks:
                self.callbacks["set_java_custom_path"](file_path)
            self.parent.set_status(f"自定义 Java 路径已设置: {file_path}", "success")

    def _on_java_scan_refresh(self):
        self._populate_java_scan_list()
        self.parent.set_status(_("settings_java_scan_refresh"), "info")

    def _populate_java_scan_list(self):
        for w in self._java_scan_frame.winfo_children():
            w.destroy()

        self._java_scan_runtimes = []
        scan_result = self.callbacks.get("scan_system_java", lambda: [])()

        if not scan_result:
            self._java_scan_list_label = ctk.CTkLabel(
                self._java_scan_frame,
                text=_("settings_java_scan_none"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
            )
            self._java_scan_list_label.pack(pady=20, padx=12)
            self._r(self._java_scan_list_label, text_color="text_secondary")
            return

        self._java_scan_runtimes = scan_result
        self._java_scan_selected_var = ctk.StringVar(value="")

        for i, rt in enumerate(scan_result):
            kind = "JRE" if rt.get("is_jre") else "JDK"
            label = f"Java {rt.get('major_version')} ({kind}) - {rt.get('version_str')} [{rt.get('arch')}]"
            sublabel = rt.get("home", "")

            frame = ctk.CTkFrame(
                self._java_scan_frame,
                fg_color=COLORS["bg_medium"],
                corner_radius=6,
            )
            frame.pack(fill=ctk.X, pady=2, padx=5)

            radio = ctk.CTkRadioButton(
                frame,
                text=label,
                variable=self._java_scan_selected_var,
                value=rt.get("path", ""),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
                text_color=COLORS["text_primary"],
                command=lambda p=rt.get("path", ""): self._on_java_scan_select(p),
            )
            radio.pack(anchor=ctk.W, padx=10, pady=(4, 0))

            sub = ctk.CTkLabel(
                frame,
                text=sublabel,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["text_secondary"],
            )
            sub.pack(anchor=ctk.W, padx=30, pady=(0, 4))

    def _on_java_scan_select(self, path: str):
        if "set_java_custom_path" in self.callbacks:
            self.callbacks["set_java_custom_path"](path)
        self.parent.set_status(f"已选择 Java: {path}", "success")

    def _refresh_theme_list(self):
        """刷新主题下拉列表"""
        theme_engine = self.callbacks.get("get_theme_engine", lambda: None)()
        if not theme_engine:
            return
        available_themes = theme_engine.get_available_themes()
        theme_display_list = []
        theme_display_map = {}
        for t in available_themes:
            display = t["name"]
            if t.get("source") == "user":
                display = f"{t['name']} {_('settings_theme_user_tag')}"
            theme_display_list.append(display)
            theme_display_map[display] = t["name"]
        self._theme_display_to_name = theme_display_map
        self.theme_menu.configure(values=theme_display_list)

    def _refresh_theme(self):
        """刷新当前设置窗口和主窗口的UI颜色"""
        self._refresh_ui_colors()
        try:
            if hasattr(self.parent, '_reapply_theme'):
                self.parent._reapply_theme()
        except Exception:
            pass

    def _refresh_ui_colors(self):
        """使用注册的 _settings_theme_refs 刷新所有组件颜色"""
        self.configure(fg_color=COLORS["bg_dark"])
        for widget, mapping in self._settings_theme_refs:
            try:
                kwargs = {attr: COLORS[key] for attr, key in mapping.items()}
                if kwargs:
                    widget.configure(**kwargs)
            except Exception:
                pass

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
            try:
                import requests
                resp = requests.post(
                    "https://jingdu.qzz.io/api/auth/login",
                    json={"username": username, "password": password},
                    headers={"User-Agent": "FMCL/1.0 (Minecraft Launcher; crash-analyzer)"},
                    timeout=15,
                )
                result = resp.json()
                token = result.get("token")
                if token:
                    if "set_jdz_token" in self.callbacks:
                        self.callbacks["set_jdz_token"](token)
                    self.after(0, lambda: self._jdz_login_success(token))
                else:
                    self.after(0, lambda: self._jdz_login_fail("未获取到 Token"))
            except Exception as e:
                _err_msg = str(e)
                try:
                    if hasattr(e, 'response') and e.response is not None:
                        _err_msg = f"HTTP {e.response.status_code}: {e.response.text[:100]}"
                except Exception:
                    pass
                self.after(0, lambda: self._jdz_login_fail(_err_msg))

        threading.Thread(target=_do_login, daemon=True).start()

    def _jdz_login_success(self, token: str):
        if not self.winfo_exists():
            return
        self.jdz_login_btn.configure(state="normal", text=_("login"))
        self.jdz_status_label.configure(
            text=f"{_('netread_status')}: {_('netread_logged_in')}",
            text_color=COLORS["success"]
        )
        self._login_form.pack_forget()
        self._account_info_frame.pack(fill=ctk.X, padx=12, pady=(0, 10))
        self.parent.set_status(_("netread_login_success"), "success")
        self.after(100, self._on_account_refresh)
        if hasattr(self.parent, "_sync_agent_status"):
            self.parent._sync_agent_status()

    def _jdz_login_fail(self, msg: str):
        if not self.winfo_exists():
            return
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
        if "set_jdz_username" in self.callbacks:
            self.callbacks["set_jdz_username"](None)
        self.jdz_status_label.configure(
            text=f"{_('netread_status')}: {_('netread_not_logged_in')}",
            text_color=COLORS["text_secondary"]
        )
        self._account_info_frame.pack_forget()
        self._login_form.pack(fill=ctk.X, padx=12, pady=(0, 10))
        self.jdz_user_entry.delete(0, "end")
        self.jdz_pass_entry.delete(0, "end")
        self.parent.set_status(_("netread_logged_out"), "info")
        if hasattr(self.parent, "_sync_agent_status"):
            self.parent._sync_agent_status()

    def _on_account_refresh(self):
        if not self.winfo_exists():
            return
        if "fetch_jdz_user_info" not in self.callbacks:
            self.parent.set_status(_("account_refresh_failed", error="未找到回调"), "error")
            return
        self._account_refresh_btn.configure(state="disabled", text=_("account_fetching"))
        self.update()

        def _do_fetch():
            try:
                info = self.callbacks["fetch_jdz_user_info"]()
            except Exception as e:
                self.after(0, lambda: self._on_account_refresh_error(str(e)))
                return
            if info:
                self.after(0, lambda: self._update_account_info_display(info))
            else:
                self.after(0, lambda: self._on_account_refresh_error("API 返回空或网络错误"))

        threading.Thread(target=_do_fetch, daemon=True).start()

    def _on_account_refresh_error(self, error_msg: str):
        if not self.winfo_exists():
            return
        self._account_refresh_btn.configure(state="normal", text=_("account_refresh"))
        self.parent.set_status(_("account_refresh_failed", error=error_msg), "error")
        for key in self._account_info_labels:
            self._account_info_labels[key].configure(text=error_msg[:50])

    def _update_account_info_display(self, info: dict):
        """更新账户信息显示"""
        if not self.winfo_exists():
            return
        username = info.get("username") or "-"
        uuid_val = info.get("uuid") or "-"
        level = info.get("level", 0)
        level_name = info.get("level_name") or ""
        description = info.get("description") or "-"
        ai_credits = info.get("ai_credits", 0)

        level_text = f"{level} ({level_name})" if level_name else str(level)

        self._account_info_labels["username"].configure(text=username)
        self._account_info_labels["uuid"].configure(text=uuid_val)
        self._account_info_labels["level"].configure(text=level_text)
        self._account_info_labels["description"].configure(text=description)
        self._account_info_labels["ai_credits"].configure(text=str(ai_credits))

        self._account_refresh_btn.configure(state="normal", text=_("account_refresh"))
        self.parent.set_status(_("account_refresh_success"), "success")

    def _update_mc_account_quick_info(self):
        try:
            from launcher.account import get_account_system
            account_system = get_account_system()
            if not account_system or not self.winfo_exists():
                return
            acc = account_system.current_account
            if acc:
                type_labels = {
                    "microsoft": _("account_type_microsoft"),
                    "offline": _("account_type_offline"),
                    "yggdrasil": _("account_type_yggdrasil"),
                }
                info = f"\u2605 {acc.name} ({type_labels.get(acc.account_type.value, '')})"
                self._mc_account_quick_info.configure(text=info, text_color=COLORS["success"])
            else:
                self._mc_account_quick_info.configure(
                    text=_("account_sidebar_none"),
                    text_color=COLORS["text_secondary"],
                )
        except Exception:
            pass

    def _on_open_account_manager(self):
        try:
            from launcher.account import get_account_system
            account_system = get_account_system()
            if not account_system:
                return

            from ui.windows.account_manager import AccountManagerWindow
            AccountManagerWindow(
                self,
                account_system,
                on_account_changed=lambda: self._on_mc_account_changed(),
            )
        except Exception as e:
            import logzero
            logzero.logger.error(f"\u6253\u5F00\u8D26\u53F7\u7BA1\u7406\u5931\u8D25: {e}")

    def _on_mc_account_changed(self):
        self._update_mc_account_quick_info()
        if self.parent and hasattr(self.parent, '_update_sidebar_account'):
            self.parent._update_sidebar_account()

    # ── AI 模型配置 ──

    def _build_ai_tab(self, tab):
        """构建 AI 模型配置标签页"""
        container = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        container.pack(fill=ctk.BOTH, expand=True, padx=10, pady=10)

        # 获取当前配置
        try:
            from ui.agent.config import get_agent_config, save_agent_config
            config = get_agent_config()
        except Exception:
            config = None

        # ── OpenAI ──
        self._build_provider_section(container, "openai", "OpenAI",
            desc_line1="配置 OpenAI API Key 以使用 GPT-4o、GPT-4o-mini 等模型",
            desc_line2="API Key 以 sk- 开头。获取地址: https://platform.openai.com/api-keys")

        # ── Anthropic ──
        self._build_provider_section(container, "anthropic", "Anthropic",
            desc_line1="配置 Anthropic API Key 以使用 Claude 系列模型",
            desc_line2="API Key 以 sk-ant- 开头。获取地址: https://console.anthropic.com/")

        # ── 自定义端点 ──
        self._build_provider_section(container, "custom", _("settings_ai_custom"),
            desc_line1="配置自定义 OpenAI 兼容 API 端点",
            desc_line2="支持 Ollama、vLLM、LiteLLM 等兼容服务")

        # ── Bing API Key（用于 WebSearch）─
        bing_section = ctk.CTkFrame(container, fg_color=COLORS["bg_medium"], corner_radius=8)
        bing_section.pack(fill=ctk.X, pady=(15, 5))
        self._r(bing_section, fg_color="bg_medium")

        bing_title = ctk.CTkLabel(bing_section, text="Bing Search API",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"])
        bing_title.pack(anchor=ctk.W, padx=12, pady=(10, 3))

        bing_desc = ctk.CTkLabel(bing_section, text="可选：配置 Bing API Key 以获得更精准的搜索结果",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=COLORS["text_secondary"])
        bing_desc.pack(anchor=ctk.W, padx=12, pady=(0, 5))

        bing_entry_frame = ctk.CTkFrame(bing_section, fg_color="transparent")
        bing_entry_frame.pack(fill=ctk.X, padx=12, pady=(0, 10))

        bing_label = ctk.CTkLabel(bing_entry_frame, text="API Key:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=COLORS["text_primary"])
        bing_label.pack(side=ctk.LEFT)
        self._r(bing_label, text_color="text_primary")

        self._bing_key_entry = ctk.CTkEntry(bing_entry_frame, height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11), width=260,
            fg_color=COLORS["bg_dark"], border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"], show="•")
        self._bing_key_entry.pack(side=ctk.LEFT, padx=(10, 5))
        self._r(self._bing_key_entry, fg_color="bg_dark", border_color="card_border", text_color="text_primary")

        # 预填
        bing_key = config.bing_api_key if config else ""
        self._bing_key_entry.insert(0, bing_key)

        # ── 保存按钮 ──
        save_ai_btn = ctk.CTkButton(container, text=_("settings_save"),
            height=34, font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._on_save_ai_config)
        save_ai_btn.pack(pady=(15, 5))

    def _build_provider_section(self, container, pid, name, desc_line1, desc_line2):
        from ui.agent.config import get_agent_config

        config = get_agent_config()
        pc = config.providers.get(pid) if config else None
        api_key = pc.api_key if pc else ""
        api_url = pc.api_url if pc else ""
        models_str = ", ".join(pc.custom_models) if pc and pc.custom_models else ""

        section = ctk.CTkFrame(container, fg_color=COLORS["bg_medium"], corner_radius=8)
        section.pack(fill=ctk.X, pady=(5, 5))
        self._r(section, fg_color="bg_medium")

        # 标题 + 测试连接按钮
        title_frame = ctk.CTkFrame(section, fg_color="transparent")
        title_frame.pack(fill=ctk.X, padx=12, pady=(10, 3))

        title_label = ctk.CTkLabel(title_frame, text=name,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"])
        title_label.pack(side=ctk.LEFT)
        self._r(title_label, text_color="text_primary")

        test_btn = ctk.CTkButton(title_frame, text=_("settings_ai_test"),
            height=24, font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color=COLORS["bg_light"], hover_color=COLORS["card_border"],
            command=lambda p=pid: self._on_test_provider(p))
        test_btn.pack(side=ctk.RIGHT)

        # 描述
        desc = ctk.CTkLabel(section, text=desc_line1 + "\n" + desc_line2,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=COLORS["text_secondary"],
            wraplength=440, justify=ctk.LEFT)
        desc.pack(anchor=ctk.W, padx=12, pady=(0, 5))

        # API Key
        key_frame = ctk.CTkFrame(section, fg_color="transparent")
        key_frame.pack(fill=ctk.X, padx=12, pady=3)

        key_label = ctk.CTkLabel(key_frame, text="API Key:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=COLORS["text_primary"])
        key_label.pack(side=ctk.LEFT)
        self._r(key_label, text_color="text_primary")

        key_entry = ctk.CTkEntry(key_frame, height=28, font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_dark"], border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"], show="•")
        key_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(10, 0))
        key_entry.insert(0, api_key)
        self._r(key_entry, fg_color="bg_dark", border_color="card_border", text_color="text_primary")

        setattr(self, f"_{pid}_key_entry", key_entry)

        # Base URL (仅 custom / anthropic 显示)
        if pid in ("custom", "anthropic", "openai"):
            url_frame = ctk.CTkFrame(section, fg_color="transparent")
            url_frame.pack(fill=ctk.X, padx=12, pady=3)

            url_label = ctk.CTkLabel(url_frame, text="Base URL:",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=COLORS["text_primary"])
            url_label.pack(side=ctk.LEFT)
            self._r(url_label, text_color="text_primary")

            url_entry = ctk.CTkEntry(url_frame, height=28, font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color=COLORS["bg_dark"], border_color=COLORS["card_border"],
                text_color=COLORS["text_primary"])
            url_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(10, 0))
            if api_url:
                url_entry.insert(0, api_url)
            self._r(url_entry, fg_color="bg_dark", border_color="card_border", text_color="text_primary")

            setattr(self, f"_{pid}_url_entry", url_entry)

        # 自定义模型列表（仅 custom）
        if pid == "custom":
            models_frame = ctk.CTkFrame(section, fg_color="transparent")
            models_frame.pack(fill=ctk.X, padx=12, pady=(3, 10))

            models_label = ctk.CTkLabel(models_frame, text="模型列表:",
                font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=COLORS["text_primary"])
            models_label.pack(side=ctk.LEFT)
            self._r(models_label, text_color="text_primary")

            models_entry = ctk.CTkEntry(models_frame, height=28, font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                fg_color=COLORS["bg_dark"], border_color=COLORS["card_border"],
                text_color=COLORS["text_primary"],
                placeholder_text="gpt-4o, claude-3.5-sonnet, ...")
            models_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(10, 0))
            models_entry.insert(0, models_str)
            self._r(models_entry, fg_color="bg_dark", border_color="card_border", text_color="text_primary")

            setattr(self, f"_{pid}_models_entry", models_entry)

    def _on_save_ai_config(self):
        """保存 AI 配置"""
        try:
            from ui.agent.config import get_agent_config, save_agent_config
            config = get_agent_config()

            for pid in ("openai", "anthropic", "custom"):
                key_entry = getattr(self, f"_{pid}_key_entry", None)
                api_key = key_entry.get().strip() if key_entry else ""
                url_entry = getattr(self, f"_{pid}_url_entry", None)
                api_url = url_entry.get().strip() if url_entry else ""

                # 只保存有数据的
                if api_key or api_url:
                    from ui.agent.config import ProviderConfig
                    pc = ProviderConfig(
                        enabled=bool(api_key),
                        api_key=api_key,
                        api_url=api_url,
                        custom_models=[],
                    )
                    # custom 的模型列表
                    if pid == "custom":
                        models_entry = getattr(self, f"_{pid}_models_entry", None)
                        if models_entry:
                            models_str = models_entry.get().strip()
                            pc.custom_models = [m.strip() for m in models_str.split(",") if m.strip()]

                    config.providers[pid] = pc

            # Bing API Key
            bing_key = self._bing_key_entry.get().strip()
            config.bing_api_key = bing_key

            save_agent_config()
            import tkinter.messagebox as messagebox
            messagebox.showinfo("FMCL", _("settings_saved"))
        except Exception as e:
            import tkinter.messagebox as messagebox
            messagebox.showerror("FMCL", f"保存失败: {e}")

    def _on_test_provider(self, pid):
        """测试提供商连接"""
        import threading

        def _do_test():
            try:
                key_entry = getattr(self, f"_{pid}_key_entry", None)
                api_key = key_entry.get().strip() if key_entry else ""

                url_entry = getattr(self, f"_{pid}_url_entry", None)
                api_url = url_entry.get().strip() if url_entry else ""

                if not api_key:
                    import tkinter.messagebox as messagebox
                    self.after(0, lambda: messagebox.showwarning("FMCL", _("settings_ai_key_required")))
                    return

                import json, urllib.request, urllib.error
                if pid == "anthropic":
                    # Anthropic: GET /v1/messages 需要 POST，用 models 端点测试
                    test_url = "https://api.anthropic.com/v1/messages"
                    req_data = json.dumps({
                        "model": "claude-3-5-haiku-20241022",
                        "messages": [{"role": "user", "content": "hi"}],
                        "max_tokens": 1,
                    }).encode("utf-8")
                    req = urllib.request.Request(api_url or test_url, data=req_data,
                        headers={"Content-Type": "application/json", "x-api-key": api_key,
                                 "anthropic-version": "2023-06-01",
                                 "User-Agent": "FMCL/2.0"},
                        method="POST")
                    try:
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            json.loads(resp.read().decode("utf-8"))
                        self.after(0, lambda: messagebox.showinfo("FMCL", "✅ 连接成功"))
                    except urllib.error.HTTPError as e:
                        if e.code == 401:
                            self.after(0, lambda: messagebox.showerror("FMCL", "❌ 认证失败：API Key 无效"))
                        else:
                            body = e.read().decode("utf-8", errors="ignore")[:200]
                            self.after(0, lambda b=body: messagebox.showerror("FMCL", f"❌ HTTP {e.code}: {b}"))
                    except Exception as e:
                        self.after(0, lambda err=str(e): messagebox.showerror("FMCL", f"❌ 连接失败: {err}"))
                else:
                    # OpenAI 兼容
                    from ui.agent.provider import BaseProvider
                    result = BaseProvider.test_connection(api_url or "https://api.openai.com/v1", api_key, timeout=15)
                    if result["ok"]:
                        self.after(0, lambda: messagebox.showinfo("FMCL", "✅ 连接成功"))
                    else:
                        self.after(0, lambda msg=result["message"]: messagebox.showerror("FMCL", f"❌ {msg}"))

            except Exception as e:
                import tkinter.messagebox as messagebox
                self.after(0, lambda err=str(e): messagebox.showerror("FMCL", f"测试失败: {err}"))

        threading.Thread(target=_do_test, daemon=True).start()

    def _build_plugin_tab(self, tab):
        """构建插件管理标签页"""
        container = ctk.CTkScrollableFrame(tab, fg_color="transparent")
        container.pack(fill=ctk.BOTH, expand=True, padx=10, pady=10)

        ctk.CTkLabel(
            container,
            text=_("plugin_manager_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, pady=(10, 5))

        ctk.CTkLabel(
            container,
            text=_("plugin_manager_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=460,
        ).pack(anchor=ctk.W, pady=(0, 15))

        open_btn = ctk.CTkButton(
            container,
            text=_("plugin_open_manager"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            height=40,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_open_plugin_manager,
        )
        open_btn.pack(fill=ctk.X, pady=(0, 10))
        self._r(open_btn, fg_color="accent", hover_color="accent_hover")

        info_frame = ctk.CTkFrame(container, fg_color=COLORS["bg_medium"], corner_radius=8)
        info_frame.pack(fill=ctk.X, pady=10)

        ctk.CTkLabel(
            info_frame,
            text=_("plugin_quick_info"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, padx=12, pady=(10, 5))

        try:
            pm = self.callbacks.get("get_plugin_manager", lambda: None)()
            if pm:
                pm.scan()
                meta = pm.get_all_plugin_meta()
                enabled = pm.get_enabled_plugins()
                total = len(meta)
                info_text = _("plugin_status_summary", enabled=len(enabled), total=total)
            else:
                info_text = _("plugin_not_initialized")
        except Exception:
            info_text = _("plugin_not_initialized")

        self._plugin_info_label = ctk.CTkLabel(
            info_frame,
            text=info_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._plugin_info_label.pack(anchor=ctk.W, padx=12, pady=(0, 10))

        tips = [
            _("plugin_tip_1"),
            _("plugin_tip_2"),
            _("plugin_tip_3"),
        ]
        for tip in tips:
            ctk.CTkLabel(
                container,
                text=f"  {tip}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
            ).pack(anchor=ctk.W, pady=2)

        # 市场入口
        market_btn = ctk.CTkButton(
            container,
            text=_("plugin_market_open"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            height=40,
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=self._on_open_plugin_market,
        )
        market_btn.pack(fill=ctk.X, pady=(20, 10))
        self._r(market_btn, fg_color="success", hover_color="#27ae60")

    def _on_open_plugin_market(self):
        """打开插件市场窗口"""
        pm = self.callbacks.get("get_plugin_manager", lambda: None)()
        if pm is None:
            import tkinter.messagebox as messagebox
            messagebox.showwarning(
                _("warning"),
                _("plugin_not_initialized"),
                parent=self,
            )
            return
        market = pm.init_market()
        if market is None:
            import tkinter.messagebox as messagebox
            messagebox.showwarning(
                _("warning"),
                _("plugin_market_unavailable"),
                parent=self,
            )
            return
        from ui.windows.plugin_browser import PluginBrowserWindow
        PluginBrowserWindow(self, pm, market)

    def _refresh_plugin_info(self):
        """刷新设置页中的插件概况信息"""
        if not hasattr(self, '_plugin_info_label') or not self._plugin_info_label.winfo_exists():
            return
        try:
            pm = self.callbacks.get("get_plugin_manager", lambda: None)()
            if pm:
                pm.scan()
                meta = pm.get_all_plugin_meta()
                enabled = pm.get_enabled_plugins()
                total = len(meta)
                info_text = _("plugin_status_summary", enabled=len(enabled), total=total)
            else:
                info_text = _("plugin_not_initialized")
        except Exception:
            info_text = _("plugin_not_initialized")
        self._plugin_info_label.configure(text=info_text)

    def _on_open_plugin_manager(self):
        """打开插件管理窗口"""
        pm = self.callbacks.get("get_plugin_manager", lambda: None)()
        if pm is None:
            import tkinter.messagebox as messagebox
            messagebox.showwarning(
                _("warning"),
                _("plugin_not_initialized"),
                parent=self,
            )
            return
        from ui.windows.plugin_manager import PluginManagerWindow
        PluginManagerWindow(self, pm)
