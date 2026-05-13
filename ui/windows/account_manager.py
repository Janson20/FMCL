"""账号管理窗口"""

import tkinter.messagebox as messagebox
import threading
import tkinter.filedialog as filedialog
from typing import Dict, Optional, Callable, Any

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


class AddAccountDialog(ctk.CTkToplevel):
    def __init__(self, parent, account_type: str, on_done: Callable[[dict], None]):
        super().__init__(fg_color=COLORS["bg_dark"])
        self._on_done = on_done
        self._account_type = account_type

        titles = {
            "microsoft": _("account_add_microsoft"),
            "offline": _("account_add_offline"),
            "yggdrasil": _("account_add_yggdrasil"),
        }
        self.title(titles.get(account_type, _("account_add")))
        self.geometry("420x320")
        self.resizable(False, False)
        try:
            self.grab_set()
        except Exception:
            pass

        self._build_ui()

    def _build_ui(self):
        if self._account_type == "microsoft":
            self._build_microsoft_ui()
        elif self._account_type == "offline":
            self._build_offline_ui()
        elif self._account_type == "yggdrasil":
            self._build_yggdrasil_ui()

    def _build_microsoft_ui(self):
        info = ctk.CTkLabel(
            self, text=_("account_ms_info"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=380,
        )
        info.pack(padx=20, pady=(20, 10))

        btn = ctk.CTkButton(
            self, text=_("account_ms_login_btn"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._do_microsoft_login,
            height=40,
        )
        btn.pack(padx=20, pady=(10, 20))

    def _build_offline_ui(self):
        ctk.CTkLabel(
            self, text=_("account_offline_name"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        ).pack(padx=20, pady=(20, 5), anchor=ctk.W)

        self._name_entry = ctk.CTkEntry(
            self, height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"], border_color=COLORS["card_border"],
            placeholder_text=_("account_offline_name_placeholder"),
        )
        self._name_entry.pack(fill=ctk.X, padx=20, pady=(0, 10))

        ctk.CTkButton(
            self, text=_("account_add"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._do_offline_create,
            height=38,
        ).pack(padx=20, pady=(10, 20))

    def _build_yggdrasil_ui(self):
        ctk.CTkLabel(
            self, text=_("account_ygg_server"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        ).pack(padx=20, pady=(20, 5), anchor=ctk.W)

        self._server_entry = ctk.CTkEntry(
            self, height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"], border_color=COLORS["card_border"],
            placeholder_text=_("account_ygg_server_placeholder"),
        )
        self._server_entry.pack(fill=ctk.X, padx=20, pady=(0, 8))

        ctk.CTkLabel(
            self, text=_("account_ygg_username"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        ).pack(padx=20, pady=(0, 5), anchor=ctk.W)

        self._ygg_user_entry = ctk.CTkEntry(
            self, height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"], border_color=COLORS["card_border"],
        )
        self._ygg_user_entry.pack(fill=ctk.X, padx=20, pady=(0, 8))

        ctk.CTkLabel(
            self, text=_("account_ygg_password"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
        ).pack(padx=20, pady=(0, 5), anchor=ctk.W)

        self._ygg_pass_entry = ctk.CTkEntry(
            self, height=36, show="\u2022",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"], border_color=COLORS["card_border"],
        )
        self._ygg_pass_entry.pack(fill=ctk.X, padx=20, pady=(0, 10))

        ctk.CTkButton(
            self, text=_("account_add"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
            command=self._do_yggdrasil_login,
            height=38,
        ).pack(padx=20, pady=(5, 20))

    def _do_microsoft_login(self):
        self.destroy()
        self._on_done({"type": "microsoft"})

    def _do_offline_create(self):
        name = self._name_entry.get().strip()
        if not name:
            messagebox.showwarning(_("warning"), _("account_name_required"), parent=self)
            return
        self.destroy()
        self._on_done({"type": "offline", "name": name})

    def _do_yggdrasil_login(self):
        server = self._server_entry.get().strip()
        username = self._ygg_user_entry.get().strip()
        password = self._ygg_pass_entry.get().strip()
        if not server or not username or not password:
            messagebox.showwarning(_("warning"), _("account_ygg_fields_required"), parent=self)
            return
        self.destroy()
        self._on_done({
            "type": "yggdrasil",
            "server_url": server,
            "username": username,
            "password": password,
        })


class AccountManagerWindow(ctk.CTkToplevel):
    def __init__(self, parent, account_system, on_account_changed: Optional[Callable[[], None]] = None):
        super().__init__(fg_color=COLORS["bg_dark"])
        self._account_system = account_system
        self._on_account_changed = on_account_changed

        self.title(_("account_manager_title"))
        self.geometry("580x650")
        self.resizable(False, False)
        try:
            self.grab_set()
        except Exception:
            pass

        self._theme_refs = []
        self._account_cards: Dict[str, ctk.CTkFrame] = {}
        self._build_ui()
        self._refresh_account_list()
        self.after(150, lambda: self.focus_set())

    def _r(self, widget, **mapping):
        self._theme_refs.append((widget, mapping))
        return widget

    def destroy(self):
        super().destroy()

    def _build_ui(self):
        title = ctk.CTkLabel(
            self, text=_("account_manager_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title.pack(anchor=ctk.W, padx=20, pady=(20, 5))
        self._r(title, text_color="text_primary")

        desc = ctk.CTkLabel(
            self, text=_("account_manager_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=540,
        )
        desc.pack(anchor=ctk.W, padx=20, pady=(0, 10))

        btn_bar = ctk.CTkFrame(self, fg_color="transparent")
        btn_bar.pack(fill=ctk.X, padx=20, pady=(0, 10))

        self._add_ms_btn = ctk.CTkButton(
            btn_bar, text=_("account_add_microsoft"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["success"], hover_color="#27ae60",
            command=self._on_add_microsoft,
            height=32, width=100,
        )
        self._add_ms_btn.pack(side=ctk.LEFT, padx=(0, 5))

        self._add_offline_btn = ctk.CTkButton(
            btn_bar, text=_("account_add_offline"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"], hover_color=COLORS["card_border"],
            command=self._on_add_offline,
            height=32, width=80,
        )
        self._add_offline_btn.pack(side=ctk.LEFT, padx=(0, 5))

        self._add_ygg_btn = ctk.CTkButton(
            btn_bar, text=_("account_add_yggdrasil"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"], hover_color=COLORS["card_border"],
            command=self._on_add_yggdrasil,
            height=32, width=80,
        )
        self._add_ygg_btn.pack(side=ctk.LEFT, padx=(0, 5))

        import_btn = ctk.CTkButton(
            btn_bar, text=_("account_import"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"], hover_color=COLORS["card_border"],
            command=self._on_import_accounts,
            height=32, width=60,
        )
        import_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        export_btn = ctk.CTkButton(
            btn_bar, text=_("account_export"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"], hover_color=COLORS["card_border"],
            command=self._on_export_accounts,
            height=32, width=60,
        )
        export_btn.pack(side=ctk.RIGHT)

        self._account_scroll = ctk.CTkScrollableFrame(
            self, fg_color="transparent", height=420,
        )
        self._account_scroll.pack(fill=ctk.BOTH, expand=True, padx=20, pady=(0, 10))

        close_btn = ctk.CTkButton(
            self, text=_("settings_close"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"], hover_color=COLORS["card_border"],
            command=self.destroy,
            height=34,
        )
        close_btn.pack(pady=(0, 15))

        self._r(title, text_color="text_primary")
        self._r(desc, text_color="text_secondary")

    def _refresh_account_list(self):
        for widget in self._account_scroll.winfo_children():
            widget.destroy()
        self._account_cards.clear()

        accounts = self._account_system.accounts
        current_id = self._account_system.current_account_id

        if not accounts:
            ctk.CTkLabel(
                self._account_scroll, text=_("account_no_accounts"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                text_color=COLORS["text_secondary"],
            ).pack(pady=30)
            return

        type_labels = {
            "microsoft": _("account_type_microsoft"),
            "offline": _("account_type_offline"),
            "yggdrasil": _("account_type_yggdrasil"),
        }
        type_colors = {
            "microsoft": COLORS["success"],
            "offline": COLORS["warning"],
            "yggdrasil": COLORS["accent"],
        }

        for acc in accounts:
            is_current = (acc.id == current_id)
            card = ctk.CTkFrame(
                self._account_scroll,
                fg_color=COLORS["bg_medium"] if not is_current else COLORS["bg_light"],
                corner_radius=8,
            )
            card.pack(fill=ctk.X, pady=3)
            self._account_cards[acc.id] = card

            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill=ctk.X, padx=12, pady=8)

            line1 = ctk.CTkFrame(inner, fg_color="transparent")
            line1.pack(fill=ctk.X)

            acc_type = acc.account_type.value
            type_label = ctk.CTkLabel(
                line1, text=type_labels.get(acc_type, acc_type),
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=type_colors.get(acc_type, COLORS["text_secondary"]),
                fg_color=COLORS["bg_dark"], corner_radius=4,
                padx=6, pady=2,
            )
            type_label.pack(side=ctk.LEFT, padx=(0, 8))

            name_display = acc.name
            if is_current:
                name_display = f"\u2605 {acc.name}"
            name_label = ctk.CTkLabel(
                line1, text=name_display,
                font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
                text_color=COLORS["text_primary"],
            )
            name_label.pack(side=ctk.LEFT)

            if is_current:
                current_tag = ctk.CTkLabel(
                    line1, text=_("account_current"),
                    font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                    text_color=COLORS["accent"],
                )
                current_tag.pack(side=ctk.LEFT, padx=(8, 0))

            line2 = ctk.CTkFrame(inner, fg_color="transparent")
            line2.pack(fill=ctk.X, pady=(4, 0))

            uuid_text = acc.uuid or "-"
            ctk.CTkLabel(
                line2, text=f"UUID: {uuid_text[:20]}...",
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
            ).pack(side=ctk.LEFT)

            btn_area = ctk.CTkFrame(inner, fg_color="transparent")
            btn_area.pack(fill=ctk.X, pady=(6, 0))

            if not is_current:
                ctk.CTkButton(
                    btn_area, text=_("account_set_current"),
                    font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                    fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"],
                    command=lambda aid=acc.id: self._on_set_current(aid),
                    height=26,
                ).pack(side=ctk.LEFT, padx=(0, 5))

            ctk.CTkButton(
                btn_area, text=_("account_delete"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color=COLORS["error"], hover_color="#c0392b",
                command=lambda aid=acc.id: self._on_delete_account(aid),
                height=26,
            ).pack(side=ctk.LEFT)

            if acc_type == "microsoft":
                ctk.CTkButton(
                    btn_area,
                    text=_("account_refresh_token"),
                    font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                    fg_color=COLORS["bg_light"], hover_color=COLORS["card_border"],
                    command=lambda a=acc: self._on_refresh_token(a),
                    height=26, width=80,
                ).pack(side=ctk.RIGHT)

    def _on_add_microsoft(self):
        def do_login(_params):
            self._set_buttons_state(ctk.DISABLED)
            account = self._account_system.microsoft_login(
                status_callback=lambda s: self._update_status(s)
            )
            self.after(0, lambda: self._on_login_complete(account, "microsoft"))

        AddAccountDialog(self, "microsoft", do_login)

    def _on_add_offline(self):
        def do_login(params):
            account = self._account_system.offline_login(params["name"])
            self.after(0, lambda: self._on_login_complete(account, "offline"))

        AddAccountDialog(self, "offline", do_login)

    def _on_add_yggdrasil(self):
        def do_login(params):
            self._set_buttons_state(ctk.DISABLED)

            def _login():
                account = self._account_system.yggdrasil_login(
                    params["server_url"],
                    params["username"],
                    params["password"],
                    status_callback=lambda s: None,
                )
                self.after(0, lambda: self._on_login_complete(account, "yggdrasil"))

            threading.Thread(target=_login, daemon=True).start()

        AddAccountDialog(self, "yggdrasil", do_login)

    def _on_login_complete(self, account, account_type):
        self._set_buttons_state(ctk.NORMAL)
        if account:
            logger_info = __import__("logzero", fromlist=["logger"]).logger
            logger_info.info(f"{account_type} \u767B\u5F55\u6210\u529F: {account.name}")
            if self._on_account_changed:
                self._on_account_changed()
        else:
            logger_info = __import__("logzero", fromlist=["logger"]).logger
            logger_info.error(f"{account_type} \u767B\u5F55\u5931\u8D25")
            messagebox.showwarning(
                _("warning"), _("account_login_failed", type=account_type), parent=self
            )
        self._refresh_account_list()

    def _on_set_current(self, account_id):
        self._account_system.set_current_account(account_id)
        self._refresh_account_list()
        if self._on_account_changed:
            self._on_account_changed()

    def _on_delete_account(self, account_id):
        acc = self._account_system.get_account(account_id)
        if not acc:
            return
        ok = messagebox.askyesno(
            _("confirm_delete"),
            _("account_delete_confirm", name=acc.name),
            parent=self,
        )
        if ok:
            self._account_system.remove_account(account_id)
            self._refresh_account_list()
            if self._on_account_changed:
                self._on_account_changed()

    def _on_refresh_token(self, account):
        success = self._account_system.refresh_account_token(account)
        if success:
            messagebox.showinfo(
                _("account_refresh_token"), _("account_refresh_token_success"), parent=self
            )
        else:
            messagebox.showerror(
                _("account_refresh_token"), _("account_refresh_token_failed"), parent=self
            )

    def _on_import_accounts(self):
        filepath = filedialog.askopenfilename(
            title=_("account_import_title"),
            filetypes=[("FMCL Accounts", "*.fmcl_accounts"), ("All Files", "*.*")],
            parent=self,
        )
        if not filepath:
            return

        dialog = ctk.CTkInputDialog(
            text=_("account_import_password_prompt"),
            title=_("account_import_title"),
            fg_color=COLORS["bg_dark"],
        )
        password = dialog.get_input()
        if not password:
            return

        try:
            with open(filepath, "rb") as f:
                data = f.read()
        except Exception as e:
            messagebox.showerror(_("account_import_title"), str(e), parent=self)
            return

        result = self._account_system.import_accounts(password, data)
        if result == -1:
            messagebox.showerror(
                _("account_import_title"), _("account_import_password_error"), parent=self
            )
        elif result > 0:
            messagebox.showinfo(
                _("account_import_title"),
                _("account_import_success", count=result),
                parent=self,
            )
        self._refresh_account_list()

    def _on_export_accounts(self):
        dialog = ctk.CTkInputDialog(
            text=_("account_export_password_prompt"),
            title=_("account_export_title"),
            fg_color=COLORS["bg_dark"],
        )
        password = dialog.get_input()
        if not password:
            return

        confirm = ctk.CTkInputDialog(
            text=_("account_export_password_confirm"),
            title=_("account_export_title"),
            fg_color=COLORS["bg_dark"],
        )
        password2 = confirm.get_input()
        if password != password2:
            messagebox.showwarning(
                _("warning"), _("account_export_password_mismatch"), parent=self
            )
            return

        data = self._account_system.export_accounts(password)
        if not data:
            messagebox.showerror(_("account_export_title"), _("account_export_failed"), parent=self)
            return

        filepath = filedialog.asksaveasfilename(
            title=_("account_export_title"),
            defaultextension=".fmcl_accounts",
            filetypes=[("FMCL Accounts", "*.fmcl_accounts")],
            parent=self,
        )
        if filepath:
            try:
                with open(filepath, "wb") as f:
                    f.write(data)
                messagebox.showinfo(
                    _("account_export_title"), _("account_export_success"), parent=self
                )
            except Exception as e:
                messagebox.showerror(_("account_export_title"), str(e), parent=self)

    def _set_buttons_state(self, state):
        for btn in [self._add_ms_btn, self._add_offline_btn, self._add_ygg_btn]:
            try:
                btn.configure(state=state)
            except Exception:
                pass

    def _update_status(self, status):
        pass
