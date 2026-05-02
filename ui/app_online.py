"""ModernApp 联机 Mixin - NAT 穿透联机标签页相关方法"""
import os
import re
import subprocess
import threading
import platform
import urllib.request
from pathlib import Path
from typing import Optional

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


NATTER_REPO_URL = "https://github.com/MikeWang000000/Natter.git"
DEFAULT_PYTHON_VERSION = "3.12.0"
PYTHON_DOWNLOAD_URL = "https://www.python.org/ftp/python/{version}/python-{version}-amd64.exe"


class OnlineTabMixin(object):
    """联机标签页 Mixin - NAT 穿透联机功能"""

    def _build_online_tab_content(self):
        content = ctk.CTkFrame(self.online_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        top_bar = ctk.CTkFrame(content, fg_color="transparent")
        top_bar.pack(fill=ctk.X, pady=(0, 10))

        self._online_title_label = ctk.CTkLabel(
            top_bar,
            text=_("online_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self._online_title_label.pack(anchor=ctk.W)

        self._online_desc_label = ctk.CTkLabel(
            top_bar,
            text=_("online_description"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_secondary"],
            wraplength=1100,
            justify=ctk.LEFT,
            anchor=ctk.W,
        )
        self._online_desc_label.pack(anchor=ctk.W, pady=(5, 0))

        main_container = ctk.CTkFrame(content, fg_color="transparent")
        main_container.pack(fill=ctk.BOTH, expand=True)

        self._build_online_output_panel(main_container)
        self._build_online_control_panel(main_container)

        self._theme_refs.append((self._online_title_label, {"text_color": "text_primary"}))
        self._theme_refs.append((self._online_desc_label, {"text_color": "text_secondary"}))

        self._natter_process: Optional[subprocess.Popen] = None
        self._natter_pid: Optional[int] = None
        self._python_path: Optional[str] = None
        self._natter_dir: Optional[str] = None
        self._public_address: Optional[str] = None

        self.after(200, self._init_online_state)

    def _init_online_state(self):
        self._detect_python()
        self._detect_natter_dir()

    def _build_online_control_panel(self, parent):
        self._online_control_frame = ctk.CTkScrollableFrame(
            parent,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        self._online_control_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 10))

        self._build_online_env_section(self._online_control_frame)
        self._build_online_nat_section(self._online_control_frame)
        self._build_online_config_section(self._online_control_frame)
        self._build_online_actions_section(self._online_control_frame)
        self._build_online_address_section(self._online_control_frame)
        self._build_online_tips_section(self._online_control_frame)

        self._theme_refs.append((self._online_control_frame, {"scrollbar_button_color": "bg_light"}))

    def _build_online_env_section(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_env_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_env_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(4, 8))

        py_row = ctk.CTkFrame(inner, fg_color="transparent")
        py_row.pack(fill=ctk.X, pady=(0, 4))

        self._online_py_icon_label = ctk.CTkLabel(
            py_row,
            text="🐍",
            font=ctk.CTkFont(size=14),
            text_color=COLORS["text_secondary"],
        )
        self._online_py_icon_label.pack(side=ctk.LEFT, padx=(0, 4))

        self._online_env_python_label = ctk.CTkLabel(
            py_row,
            text=_("online_env_python_checking"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._online_env_python_label.pack(side=ctk.LEFT)

        natter_row = ctk.CTkFrame(inner, fg_color="transparent")
        natter_row.pack(fill=ctk.X, pady=(0, 8))

        self._online_natter_icon_label = ctk.CTkLabel(
            natter_row,
            text="📦",
            font=ctk.CTkFont(size=14),
            text_color=COLORS["text_secondary"],
        )
        self._online_natter_icon_label.pack(side=ctk.LEFT, padx=(0, 4))

        self._online_env_natter_label = ctk.CTkLabel(
            natter_row,
            text=_("online_env_natter_checking"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._online_env_natter_label.pack(side=ctk.LEFT)

        self._online_env_setup_btn = ctk.CTkButton(
            inner,
            text=_("online_env_setup_btn"),
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_setup_environment,
        )
        self._online_env_setup_btn.pack(fill=ctk.X, pady=(0, 0))

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_env_python_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._online_env_natter_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._online_env_setup_btn, {"fg_color": "accent", "hover_color": "accent_hover"}))

    def _build_online_nat_section(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_step1_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_step1_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(4, 0))

        nat_status_frame = ctk.CTkFrame(inner, fg_color="transparent")
        nat_status_frame.pack(fill=ctk.X, pady=(8, 0))

        ctk.CTkLabel(
            nat_status_frame,
            text=_("online_nat_type_label"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT)

        self._online_nat_type_label = ctk.CTkLabel(
            nat_status_frame,
            text=_("online_nat_type_unknown"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
            wraplength=260,
        )
        self._online_nat_type_label.pack(side=ctk.LEFT, padx=(6, 0))

        self._online_check_nat_btn = ctk.CTkButton(
            inner,
            text=_("online_check_nat"),
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_check_nat,
        )
        self._online_check_nat_btn.pack(fill=ctk.X, pady=(10, 0))

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_nat_type_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._online_check_nat_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))

    def _build_online_config_section(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_step2_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_step2_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(4, 0))

        ctk.CTkLabel(
            inner,
            text=_("online_port_label"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(10, 0))

        self._online_port_entry = ctk.CTkEntry(
            inner,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=_("online_port_placeholder"),
        )
        self._online_port_entry.pack(fill=ctk.X, pady=(4, 0))
        self._online_port_entry.insert(0, "25565")

        ctk.CTkLabel(
            inner,
            text=_("online_target_ip_label"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(8, 0))

        self._online_ip_entry = ctk.CTkEntry(
            inner,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=_("online_target_ip_placeholder"),
        )
        self._online_ip_entry.pack(fill=ctk.X, pady=(4, 0))

        ctk.CTkLabel(
            inner,
            text=_("online_keepalive_label"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(8, 0))

        self._online_keepalive_entry = ctk.CTkEntry(
            inner,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
        )
        self._online_keepalive_entry.pack(fill=ctk.X, pady=(4, 0))
        self._online_keepalive_entry.insert(0, "20")

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_port_entry, {"fg_color": "bg_medium", "border_color": "card_border"}))
        self._theme_refs.append((self._online_ip_entry, {"fg_color": "bg_medium", "border_color": "card_border"}))
        self._theme_refs.append((self._online_keepalive_entry, {"fg_color": "bg_medium", "border_color": "card_border"}))

    def _build_online_actions_section(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        self._online_start_btn = ctk.CTkButton(
            inner,
            text=_("online_start_natter"),
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=self._on_start_natter,
        )
        self._online_start_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 4))

        self._online_stop_btn = ctk.CTkButton(
            inner,
            text=_("online_stop_natter"),
            width=100,
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            text_color=COLORS["text_primary"],
            command=self._on_stop_natter,
        )
        self._online_stop_btn.pack(side=ctk.RIGHT)
        self._online_stop_btn.configure(state=ctk.DISABLED)

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_start_btn, {"fg_color": "success"}))
        self._theme_refs.append((self._online_stop_btn, {"fg_color": "error", "text_color": "text_primary"}))

    def _build_online_address_section(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_step3_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_step3_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(4, 0))

        addr_frame = ctk.CTkFrame(inner, fg_color="transparent")
        addr_frame.pack(fill=ctk.X, pady=(10, 0))

        ctk.CTkLabel(
            addr_frame,
            text=_("online_public_address_label"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W)

        self._online_address_label = ctk.CTkLabel(
            addr_frame,
            text=_("online_no_address"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["accent"],
            wraplength=340,
        )
        self._online_address_label.pack(anchor=ctk.W, pady=(4, 0))

        btn_frame = ctk.CTkFrame(inner, fg_color="transparent")
        btn_frame.pack(fill=ctk.X, pady=(10, 0))

        self._online_copy_address_btn = ctk.CTkButton(
            btn_frame,
            text=_("online_copy_address"),
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_copy_address,
        )
        self._online_copy_address_btn.pack(fill=ctk.X)

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_address_label, {"text_color": "accent"}))
        self._theme_refs.append((self._online_copy_address_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))

    def _build_online_tips_section(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_tips_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_tips_content"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(4, 0))

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))

    def _build_online_output_panel(self, parent):
        self._online_output_frame = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        self._online_output_frame.pack(side=ctk.RIGHT, fill=ctk.BOTH, expand=True, padx=(0, 0))

        frame = self._online_output_frame

        title_frame = ctk.CTkFrame(frame, fg_color="transparent", height=40)
        title_frame.pack(fill=ctk.X, padx=15, pady=(12, 0))
        title_frame.pack_propagate(False)

        ctk.CTkLabel(
            title_frame,
            text=_("online_natter_output_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self._online_status_label = ctk.CTkLabel(
            title_frame,
            text=_("online_natter_stopped"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._online_status_label.pack(side=ctk.RIGHT)

        ctk.CTkFrame(frame, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(8, 5)
        )

        self._online_log_text = ctk.CTkTextbox(
            frame,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            activate_scrollbars=True,
            wrap=ctk.WORD,
            spacing3=0,
        )
        self._online_log_text.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 10))
        self._online_log_text.configure(state=ctk.DISABLED)

        self._append_online_log("[FMCL] " + _("status_ready"))

        self._theme_refs.append((self._online_output_frame, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_status_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._online_log_text, {"fg_color": "bg_dark", "border_color": "card_border", "text_color": "text_primary"}))

    def _append_online_log(self, message: str):
        def _do_append():
            self._online_log_text.configure(state=ctk.NORMAL)
            self._online_log_text.insert(ctk.END, message + "\n")
            self._online_log_text.see(ctk.END)
            self._online_log_text.configure(state=ctk.DISABLED)
        if self.winfo_exists():
            self.after(0, _do_append)

    def _set_online_status(self, message: str, color_key: str = "text_secondary"):
        def _do_set():
            self._online_status_label.configure(text=message, text_color=COLORS.get(color_key, COLORS["text_secondary"]))
        if self.winfo_exists():
            self.after(0, _do_set)

    def _set_public_address(self, address: Optional[str]):
        self._public_address = address
        def _do_set():
            if address:
                self._online_address_label.configure(text=address, text_color=COLORS["success"])
            else:
                self._online_address_label.configure(text=_("online_no_address"), text_color=COLORS["text_secondary"])
        if self.winfo_exists():
            self.after(0, _do_set)

    def _run_online_thread(self, target, args=(), on_done=None, on_error=None):
        def wrapper():
            try:
                result = target(*args)
                if on_done:
                    self.after(0, lambda: on_done(result))
            except Exception as e:
                if on_error:
                    self.after(0, lambda: on_error(str(e)))
                else:
                    self.after(0, lambda: self._append_online_log(f"[FMCL] Error: {e}"))
        threading.Thread(target=wrapper, daemon=True).start()

    @staticmethod
    def _get_natter_base_dir() -> Path:
        if platform.system().lower() == "windows":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        elif platform.system().lower() == "darwin":
            base = Path.home() / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
        return base / "FMCL" / "Natter"

    def _detect_natter_dir(self):
        natter_dir = self._get_natter_base_dir()
        natter_py = natter_dir / "natter.py"
        if natter_py.exists():
            self._natter_dir = str(natter_dir)
        else:
            self._natter_dir = None

    def _update_env_python_label(self, text: str, color_key: str = "text_secondary"):
        def _do():
            self._online_env_python_label.configure(text=text, text_color=COLORS.get(color_key, COLORS["text_secondary"]))
        if self.winfo_exists():
            self.after(0, _do)

    def _update_env_natter_label(self, text: str, color_key: str = "text_secondary"):
        def _do():
            self._online_env_natter_label.configure(text=text, text_color=COLORS.get(color_key, COLORS["text_secondary"]))
        if self.winfo_exists():
            self.after(0, _do)

    def _detect_python(self):
        def _check():
            python_cmd = "python3" if platform.system().lower() != "windows" else "python"
            for cmd in [python_cmd, "python"]:
                try:
                    result = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        return result.stdout.strip() or result.stderr.strip()
                except Exception:
                    continue
            return None

        def on_done(version):
            if version:
                self._python_path = "python"
                self._update_env_python_label(version, "success")
            else:
                self._python_path = None
                self._update_env_python_label(_("online_python_not_found"), "error")

        self._run_online_thread(_check, on_done=on_done)

    def _on_setup_environment(self):
        self._online_env_setup_btn.configure(text=_("online_env_setup_btn"), state=ctk.DISABLED)
        self._append_online_log("[FMCL] " + _("online_env_setup_start"))

        def _setup():
            steps = []
            python_ok = True
            natter_ok = True

            check_cmd = "python3" if platform.system().lower() != "windows" else "python"
            python_found = False
            for cmd in [check_cmd, "python"]:
                try:
                    result = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=5)
                    if result.returncode == 0:
                        version = result.stdout.strip() or result.stderr.strip()
                        python_found = True
                        steps.append(("python", version, "ok"))
                        break
                except Exception:
                    continue

            if not python_found:
                if platform.system().lower() == "windows":
                    steps.append(("python", _("online_python_downloading"), "installing"))
                    version = DEFAULT_PYTHON_VERSION
                    download_url = PYTHON_DOWNLOAD_URL.format(version=version)
                    tmp_dir = os.environ.get("TEMP", os.path.expanduser("~"))
                    installer_path = os.path.join(tmp_dir, f"python-{version}-amd64.exe")
                    self.after(0, lambda: self._append_online_log(f"[FMCL] Downloading {download_url}..."))
                    try:
                        urllib.request.urlretrieve(download_url, installer_path)
                    except Exception as e:
                        steps.append(("python", str(e), "fail"))
                        python_ok = False
                    if python_ok:
                        install_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Programs", "Python", f"Python{version.replace('.', '')}")
                        self.after(0, lambda: self._append_online_log("[FMCL] Running Python installer (silent)..."))
                        try:
                            result = subprocess.run(
                                [installer_path, "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_test=0", f"DefaultAllUsersTargetDir={install_dir}"],
                                capture_output=True, text=True, timeout=300,
                            )
                            if result.returncode not in (0, 3010):
                                raise Exception(f"Exit code {result.returncode}")
                            steps.append(("python", version, "ok"))
                        except Exception as e:
                            steps.append(("python", str(e), "fail"))
                            python_ok = False
                        finally:
                            try:
                                os.unlink(installer_path)
                            except Exception:
                                pass
                else:
                    steps.append(("python", _("online_env_natter_not_installed"), "fail"))
                    python_ok = False

            natter_dir = self._get_natter_base_dir()
            natter_dir.mkdir(parents=True, exist_ok=True)
            natter_py = natter_dir / "natter.py"

            if natter_py.exists():
                self.after(0, lambda: self._append_online_log("[FMCL] " + _("online_natter_git_updating")))
                try:
                    result = subprocess.run(["git", "-C", str(natter_dir), "pull"], capture_output=True, text=True, timeout=30)
                    if result.returncode == 0:
                        steps.append(("natter", _("online_natter_git_update_done"), "ok"))
                    else:
                        raise Exception(result.stderr.strip() or f"Exit code {result.returncode}")
                except Exception as e:
                    steps.append(("natter", str(e), "fail"))
                    natter_ok = False
            else:
                self.after(0, lambda: self._append_online_log("[FMCL] " + _("online_natter_git_cloning")))
                try:
                    result = subprocess.run(["git", "clone", NATTER_REPO_URL, str(natter_dir)], capture_output=True, text=True, timeout=60)
                    if result.returncode == 0:
                        steps.append(("natter", _("online_natter_git_clone_done"), "ok"))
                    else:
                        raise Exception(result.stderr.strip() or f"Exit code {result.returncode}")
                except Exception as e:
                    steps.append(("natter", str(e), "fail"))
                    natter_ok = False

            return steps, python_ok, natter_ok, natter_dir

        def on_done(data):
            steps, python_ok, natter_ok, natter_dir = data
            for what, msg, status in steps:
                if what == "python":
                    if status == "ok":
                        self._python_path = "python"
                        self._update_env_python_label(msg, "success")
                    else:
                        self._update_env_python_label(_("online_python_not_found"), "error")
                elif what == "natter":
                    if status == "ok":
                        self._natter_dir = str(natter_dir)
                        self._update_env_natter_label(msg, "success")
                    else:
                        self._update_env_natter_label(_("online_env_natter_not_installed"), "error")

            if python_ok and natter_ok:
                self._online_env_setup_btn.configure(text="✅ " + _("online_env_setup_done"), state=ctk.DISABLED, fg_color=COLORS["success"])
                self._append_online_log("[FMCL] " + _("online_env_setup_done"))
                self.set_status(_("online_env_setup_done"), "success")
            else:
                self._online_env_setup_btn.configure(text=_("online_env_setup_retry"), state=ctk.NORMAL, fg_color=COLORS["accent"])
                self._append_online_log("[FMCL] " + _("online_env_setup_failed"))
                self.set_status(_("online_env_setup_failed"), "warning")

        def on_error(error):
            self._update_env_python_label(_("online_python_not_found"), "error")
            self._update_env_natter_label(_("online_env_natter_not_installed"), "error")
            self._online_env_setup_btn.configure(text=_("online_env_setup_retry"), state=ctk.NORMAL, fg_color=COLORS["accent"])
            self._append_online_log("[FMCL] " + str(error))

        self._run_online_thread(_setup, on_done=on_done, on_error=on_error)

    def _on_check_nat(self):
        if not self._natter_dir:
            self._append_online_log("[FMCL] " + _("online_natter_git_not_found"))
            self.set_status(_("online_natter_git_not_found"), "warning")
            return
        if not self._python_path:
            self._append_online_log("[FMCL] " + _("online_python_not_found"))
            self.set_status(_("online_python_not_found"), "warning")
            return

        self._online_check_nat_btn.configure(
            text=_("online_checking_nat"), state=ctk.DISABLED
        )
        self._append_online_log("[FMCL] " + _("online_checking_nat"))

        natter_py = os.path.join(self._natter_dir, "natter.py")

        def _check():
            try:
                result = subprocess.run(
                    [self._python_path, natter_py, "--check"],
                    capture_output=True, text=True, timeout=90,
                    cwd=self._natter_dir,
                )
                output = result.stdout + result.stderr
                return output, result.returncode
            except subprocess.TimeoutExpired:
                raise Exception("NAT check timed out")
            except Exception as e:
                raise Exception(str(e))

        def on_done(data):
            output, returncode = data
            self._append_online_log(output.strip() or "[FMCL] NAT check completed")

            nat_types = re.findall(r"NAT Type:\s*(\d)", output)
            nat_num = min(int(n) for n in nat_types) if nat_types else 0

            if nat_num == 1:
                label = _("online_nat_type_fullcone")
                color = "success"
            elif nat_num == 2:
                label = _("online_nat_type_restricted")
                color = "warning"
            elif nat_num in (3, 4):
                label = _("online_nat_type_symmetric")
                color = "error"
            elif returncode != 0:
                label = _("online_nat_type_unknown")
                color = "text_secondary"
                self.set_status(_("online_nat_check_failed", error=output.strip()[-100:]), "warning")
            else:
                label = _("online_nat_type_fullcone")
                color = "success"

            self._online_nat_type_label.configure(text=label, text_color=COLORS[color])
            self._online_check_nat_btn.configure(text=_("online_check_nat"), state=ctk.NORMAL)

            if nat_num == 1 or (returncode == 0 and nat_num == 0):
                self.set_status(_("online_nat_type_fullcone"), "success")
            elif nat_num == 2:
                self.set_status(_("online_nat_type_restricted"), "warning")
            elif nat_num in (3, 4):
                self.set_status(_("online_nat_type_symmetric"), "warning")

        def on_error(error):
            self._append_online_log("[FMCL] " + _("online_nat_check_failed", error=error))
            self._online_nat_type_label.configure(
                text=_("online_nat_type_unknown"), text_color=COLORS["warning"]
            )
            self._online_check_nat_btn.configure(
                text=_("online_check_nat"), state=ctk.NORMAL
            )
            self.set_status(_("online_nat_check_failed", error=error), "error")

        self._run_online_thread(_check, on_done=on_done, on_error=on_error)

    def _on_start_natter(self):
        if self._natter_process is not None:
            self._append_online_log("[FMCL] Natter is already running")
            return
        if not self._natter_dir:
            self._append_online_log("[FMCL] " + _("online_natter_git_not_found"))
            self.set_status(_("online_natter_git_not_found"), "warning")
            return
        if not self._python_path:
            self._append_online_log("[FMCL] " + _("online_python_not_found"))
            self.set_status(_("online_python_not_found"), "warning")
            return

        port = self._online_port_entry.get().strip()
        if not port:
            port = "25565"
        try:
            int(port)
        except ValueError:
            self._append_online_log("[FMCL] Invalid port number: " + port)
            self.set_status("Invalid port number", "error")
            return

        target_ip = self._online_ip_entry.get().strip()
        keepalive = self._online_keepalive_entry.get().strip()
        if not keepalive:
            keepalive = "20"

        natter_py = os.path.join(self._natter_dir, "natter.py")
        cmd = [self._python_path, natter_py, "-b", port]
        if target_ip:
            cmd.extend(["-t", target_ip, "-p", port])
        if keepalive:
            try:
                int(keepalive)
                cmd.extend(["-k", keepalive])
            except ValueError:
                pass

        self._append_online_log(f"[FMCL] Starting: {' '.join(cmd)}")
        try:
            self._natter_process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=self._natter_dir,
                creationflags=subprocess.CREATE_NO_WINDOW if platform.system().lower() == "windows" else 0,
            )
            self._natter_pid = self._natter_process.pid
            self._set_online_status(_("online_natter_running", pid=self._natter_pid), "success")
            self._online_start_btn.configure(state=ctk.DISABLED)
            self._online_stop_btn.configure(state=ctk.NORMAL)
            self._set_public_address(None)

            self._start_natter_reader()
        except Exception as e:
            self._append_online_log(f"[FMCL] Failed to start Natter: {e}")
            self.set_status(f"Failed to start Natter: {e}", "error")

    def _start_natter_reader(self):
        threading.Thread(target=self._natter_reader_loop, daemon=True).start()

    def _natter_reader_loop(self):
        proc = self._natter_process
        if proc is None:
            return
        for line in iter(proc.stdout.readline, ""):
            if proc.poll() is not None and not line:
                break
            stripped = line.strip()
            if not stripped:
                continue
            self.after(0, lambda s=stripped: self._handle_natter_line(s))
        self.after(0, self._on_natter_exited)

    def _handle_natter_line(self, line: str):
        if self._natter_process is None:
            return
        self._append_online_log(line)
        m = re.search(r"public address:\s*([^\s]+)", line, re.IGNORECASE)
        if m:
            addr = m.group(1)
            self._set_public_address(addr)
            self._append_online_log(f"[FMCL] >>> Public address detected: {addr}")
            return
        m2 = re.search(r"(\d+\.\d+\.\d+\.\d+:\d+)", line)
        if m2 and self._public_address is None:
            addr = m2.group(1)
            self._set_public_address(addr)
            self._append_online_log(f"[FMCL] >>> Public address detected: {addr}")

    def _on_natter_exited(self):
        exit_code = self._natter_process.returncode if self._natter_process else -1
        self._natter_process = None
        self._natter_pid = None
        self._set_online_status(_("online_natter_stopped"), "text_secondary")
        self._online_start_btn.configure(state=ctk.NORMAL)
        self._online_stop_btn.configure(state=ctk.DISABLED)
        self._append_online_log(f"[FMCL] Natter exited with code {exit_code}")

    def _on_stop_natter(self):
        if self._natter_process is None:
            return
        self._append_online_log("[FMCL] Stopping Natter...")
        try:
            self._natter_process.terminate()
            try:
                self._natter_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._natter_process.kill()
                self._natter_process.wait(timeout=3)
        except Exception as e:
            self._append_online_log(f"[FMCL] Error stopping Natter: {e}")
        self._on_natter_exited()

    def _on_copy_address(self):
        if not self._public_address:
            return
        try:
            import pyperclip
            pyperclip.copy(self._public_address)
            self.set_status(_("online_copy_success", address=self._public_address), "success")
        except Exception as e:
            self.set_status(_("copy_failed", error=str(e)), "error")