"""插件管理窗口 - 查看/启用/禁用/卸载已安装插件"""

import threading
from typing import Dict, Optional, Callable

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _
from ui.windows.plugin_permission_dialog import PluginPermissionDialog
from plugin_manager.permissions import (
    PluginPermission, PermissionRiskLevel, get_permission_risk,
    classify_permissions, get_permission_display_key,
)

# 状态显示映射
_STATE_DISPLAY = {
    "scanned": "plugin_state_scanned",
    "loading": "plugin_state_loading",
    "loaded": "plugin_state_loaded",
    "enabled": "plugin_state_enabled",
    "init_error": "plugin_state_init_error",
    "disabled": "plugin_state_disabled",
    "error": "plugin_state_error",
    "incompatible": "plugin_state_incompatible",
    "running": "plugin_state_running",
}

_STATE_COLORS = {
    "enabled": COLORS["success"],
    "running": COLORS["success"],
    "disabled": COLORS["text_secondary"],
    "scanned": COLORS["text_secondary"],
    "loaded": COLORS["text_secondary"],
    "loading": COLORS["warning"],
    "error": COLORS["error"],
    "init_error": COLORS["error"],
    "incompatible": COLORS["warning"],
}


class PluginManagerWindow(ctk.CTkToplevel):
    """插件管理窗口"""

    def __init__(self, parent, plugin_manager):
        """
        Args:
            parent: 父窗口
            plugin_manager: plugin_manager.manager.PluginManager 实例
        """
        super().__init__(parent)
        self._pm = plugin_manager
        self._plugin_cards: Dict[str, Dict] = {}

        self.title(_("plugin_manager_title"))
        self.geometry("680x580")
        self.minsize(600, 480)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)

        try:
            self.grab_set()
        except Exception:
            pass

        self._build_ui()

        # 居中
        self.update_idletasks()
        self._center_on_parent(parent)

        # 加载数据
        self.after(100, self._refresh)

    def _center_on_parent(self, parent):
        try:
            pw = parent.winfo_width()
            ph = parent.winfo_height()
            px = parent.winfo_x()
            py = parent.winfo_y()
            w = self.winfo_width()
            h = self.winfo_height()
            x = px + (pw - w) // 2
            y = py + (ph - h) // 2
            self.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _build_ui(self):
        """构建 UI"""
        # 顶部操作栏
        top_frame = ctk.CTkFrame(self, fg_color="transparent")
        top_frame.pack(fill=ctk.X, padx=20, pady=(16, 8))

        ctk.CTkLabel(
            top_frame,
            text=_("plugin_manager_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        # 安装按钮
        self._install_btn = ctk.CTkButton(
            top_frame,
            text=_("plugin_install_from_file"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_install_from_file,
        )
        self._install_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        # 市场按钮
        self._market_btn = ctk.CTkButton(
            top_frame,
            text=_("plugin_market_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=self._on_open_market,
        )
        self._market_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        # 刷新按钮
        self._refresh_btn = ctk.CTkButton(
            top_frame,
            text=_("refresh"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            command=self._refresh,
        )
        self._refresh_btn.pack(side=ctk.RIGHT, padx=(0, 5))

        # 分隔线
        ctk.CTkFrame(
            self, height=1, fg_color=COLORS["card_border"],
        ).pack(fill=ctk.X, padx=20)

        # 状态栏
        self._status_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._status_label.pack(anchor=ctk.W, padx=20, pady=(4, 0))

        # 可滚动列表
        self._list_frame = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
        )
        self._list_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=(0, 16))

    def _refresh(self):
        """刷新插件列表"""
        self._set_status(_("refreshing") + "...")
        self._pm.scan()
        self._rebuild_cards()

    def _rebuild_cards(self):
        """重新构建插件卡片"""
        # 清除旧卡片
        for widget in self._list_frame.winfo_children():
            widget.destroy()
        self._plugin_cards.clear()

        meta = self._pm.get_all_plugin_meta()
        if not meta:
            empty_label = ctk.CTkLabel(
                self._list_frame,
                text=_("plugin_no_plugins"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=13),
                text_color=COLORS["text_secondary"],
            )
            empty_label.pack(pady=40)
            self._set_status(_("plugin_no_plugins"))
            return

        for plugin_id, info in meta.items():
            self._create_plugin_card(plugin_id, info)

        enabled_count = self._pm.get_enabled_plugins()
        total = len(meta)
        self._set_status(_("plugin_status_summary", enabled=len(enabled_count), total=total))

    def _create_plugin_card(self, plugin_id: str, info: dict):
        """创建单个插件卡片"""
        manifest_data = info.get("manifest", {})
        state = info.get("state", "scanned")
        error_msg = info.get("error", "")
        permissions_data = info.get("permissions", {})

        name = manifest_data.get("name", plugin_id)
        version = manifest_data.get("version", "?")
        author = manifest_data.get("author", _("unknown"))
        description = manifest_data.get("description", {})
        desc_text = description.get("zh_CN", "") or description.get("en_US", "") or ""
        permissions_list = manifest_data.get("permissions", [])

        # 卡片框架
        card = ctk.CTkFrame(
            self._list_frame,
            fg_color=COLORS["card_bg"],
            corner_radius=8,
        )
        card.pack(fill=ctk.X, pady=4)

        # 顶部行: 名称 + 状态
        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.pack(fill=ctk.X, padx=12, pady=(10, 4))

        ctk.CTkLabel(
            top_row,
            text=name,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        state_color = _STATE_COLORS.get(state, COLORS["text_secondary"])
        state_key = _STATE_DISPLAY.get(state, state)

        ctk.CTkLabel(
            top_row,
            text=_(state_key),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=state_color,
        ).pack(side=ctk.RIGHT, padx=(0, 4))

        # 版本 + 作者
        meta_row = ctk.CTkFrame(card, fg_color="transparent")
        meta_row.pack(fill=ctk.X, padx=12, pady=(0, 2))

        ctk.CTkLabel(
            meta_row,
            text=f"v{version}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["accent"],
        ).pack(side=ctk.LEFT)

        ctk.CTkLabel(
            meta_row,
            text=author,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.RIGHT)

        # 描述
        if desc_text:
            desc_label = ctk.CTkLabel(
                card,
                text=desc_text[:120] + ("..." if len(desc_text) > 120 else ""),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                wraplength=600,
                justify="left",
            )
            desc_label.pack(anchor=ctk.W, padx=12, pady=(0, 4))

        # 权限标签
        if permissions_list:
            perm_row = ctk.CTkFrame(card, fg_color="transparent")
            perm_row.pack(fill=ctk.X, padx=12, pady=(0, 4))

            classified = classify_permissions(permissions_list)
            for risk in (PermissionRiskLevel.HIGH, PermissionRiskLevel.MEDIUM, PermissionRiskLevel.LOW):
                perms = classified.get(risk, [])
                if not perms:
                    continue
                for perm in perms[:3]:  # 最多显示 3 个
                    risk_colors = {
                        PermissionRiskLevel.LOW: "#2ecc71",
                        PermissionRiskLevel.MEDIUM: "#f39c12",
                        PermissionRiskLevel.HIGH: "#e74c3c",
                    }
                    color = risk_colors.get(risk, COLORS["text_secondary"])
                    tag = ctk.CTkFrame(perm_row, fg_color=color, corner_radius=4)
                    tag.pack(side=ctk.LEFT, padx=1)
                    ctk.CTkLabel(
                        tag,
                        text=_(get_permission_display_key(perm))[:8],
                        font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                        text_color="#ffffff",
                    ).pack(padx=4, pady=1)
                if len(perms) > 3:
                    ctk.CTkLabel(
                        perm_row,
                        text=f"+{len(perms) - 3}",
                        font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                        text_color=COLORS["text_secondary"],
                    ).pack(side=ctk.LEFT, padx=2)

        # 错误信息（如果有）
        if error_msg and state in ("error", "init_error", "incompatible"):
            err_label = ctk.CTkLabel(
                card,
                text=f"{_('error')}: {error_msg}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["error"],
                wraplength=600,
            )
            err_label.pack(anchor=ctk.W, padx=12, pady=(0, 4))

        # 底部按钮行
        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=12, pady=(4, 10))

        # 权限管理按钮
        perm_btn = ctk.CTkButton(
            btn_row,
            text=_("plugin_permissions_manage"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            width=100,
            height=26,
            command=lambda pid=plugin_id, pms=permissions_list: self._on_manage_permissions(pid, pms),
        )
        perm_btn.pack(side=ctk.LEFT, padx=(0, 4))

        # 启用/禁用按钮
        if state == "enabled" or state == "running":
            toggle_btn = ctk.CTkButton(
                btn_row,
                text=_("disable"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color=COLORS["warning"],
                hover_color="#e67e22",
                width=80,
                height=26,
                command=lambda pid=plugin_id: self._on_toggle(pid),
            )
            toggle_btn.pack(side=ctk.LEFT, padx=2)
        elif state in ("disabled", "scanned", "loaded"):
            toggle_btn = ctk.CTkButton(
                btn_row,
                text=_("enable"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color=COLORS["success"],
                hover_color="#27ae60",
                width=80,
                height=26,
                command=lambda pid=plugin_id: self._on_toggle(pid),
            )
            toggle_btn.pack(side=ctk.LEFT, padx=2)

        # 卸载按钮
        uninstall_btn = ctk.CTkButton(
            btn_row,
            text=_("uninstall"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            width=80,
            height=26,
            command=lambda pid=plugin_id, n=name: self._on_uninstall(pid, n),
        )
        uninstall_btn.pack(side=ctk.RIGHT)

        # 存储引用
        self._plugin_cards[plugin_id] = {
            "card": card,
            "state": state,
            "info": info,
        }

    def _on_toggle(self, plugin_id: str):
        """切换启用/禁用"""
        state = self._pm.get_plugin_state(plugin_id)
        if state is None:
            return

        if state.value == "enabled":
            ok, msg = self._pm.disable_plugin(plugin_id)
            self._set_status(msg if ok else _(msg))
        else:
            # 检查权限
            meta = self._pm.get_all_plugin_meta().get(plugin_id, {})
            perms = meta.get("manifest", {}).get("permissions", [])
            if perms:
                perm_state = self._pm.get_permission_state(plugin_id)
                if perm_state is None or perm_state.get_ungranted_permissions():
                    # 需要用户确认
                    manifest_data = meta.get("manifest", {})
                    dialog = PluginPermissionDialog(
                        self,
                        plugin_name=manifest_data.get("name", plugin_id),
                        permissions=perms,
                    )
                    if not dialog.get_result():
                        self._set_status(_("plugin_permission_denied"))
                        return
                    self._pm.grant_all_permissions(plugin_id)

            ok, msg = self._pm.enable_plugin(plugin_id)
            self._set_status(f"{_('plugin_state_enabled')}: {plugin_id}" if ok else msg)

        self._refresh()

    def _on_uninstall(self, plugin_id: str, name: str):
        """卸载插件"""
        from ui.dialogs import show_confirmation
        if not show_confirmation(
            _("plugin_uninstall_confirm", name=name),
            title=_("confirm_delete"),
        ):
            return

        # 先禁用（如果已启用）
        state = self._pm.get_plugin_state(plugin_id)
        if state and state.value == "enabled":
            self._pm.disable_plugin(plugin_id)

        ok, msg = self._pm.uninstall_plugin(plugin_id)
        if ok:
            self._set_status(_("plugin_uninstall_success", name=name))
        else:
            self._set_status(f"{_('error')}: {msg}")

        self._refresh()

    def _on_manage_permissions(self, plugin_id: str, permissions: list):
        """打开权限管理"""
        if not permissions:
            self._set_status(_("plugin_no_permissions"))
            return
        dialog = PluginPermissionDialog(
            self,
            plugin_name=plugin_id,
            permissions=permissions,
            title=_("plugin_permissions_manage"),
        )
        if dialog.get_result():
            self._pm.grant_all_permissions(plugin_id)
            self._set_status(_("plugin_permission_granted"))
        else:
            self._set_status(_("plugin_permission_unchanged"))
        self._refresh()

    def _on_open_market(self):
        """打开插件市场"""
        market = self._pm.init_market()
        if market is None:
            self._set_status(_("plugin_market_unavailable"))
            return
        from ui.windows.plugin_browser import PluginBrowserWindow
        PluginBrowserWindow(self, self._pm, market)

    def _on_install_from_file(self):
        """从文件安装插件"""
        from tkinter import filedialog
        filepath = filedialog.askopenfilename(
            parent=self,
            title=_("plugin_install_from_file"),
            filetypes=[
                (_("plugin_file_type"), "*.fmpl"),
                (_("all_files"), "*.*"),
            ],
        )
        if not filepath:
            return

        # 先读取 manifest 获取插件 ID
        import zipfile
        import json
        try:
            with zipfile.ZipFile(filepath, "r") as zf:
                if "plugin.json" not in zf.namelist():
                    self._set_status(_("plugin_invalid_fmpl"))
                    return
                data = json.loads(zf.read("plugin.json").decode("utf-8"))
                plugin_id = data.get("id", "")
                if not plugin_id:
                    self._set_status(_("plugin_invalid_manifest"))
                    return

                # 检查权限
                permissions = data.get("permissions", [])
                if permissions:
                    dialog = PluginPermissionDialog(
                        self,
                        plugin_name=data.get("name", plugin_id),
                        permissions=permissions,
                    )
                    if not dialog.get_result():
                        self._set_status(_("plugin_permission_denied"))
                        return

            # 安装
            self._set_status(_("installing") + "...")
            ok, msg = self._pm.install_from_file(filepath, plugin_id)
            if ok:
                if permissions:
                    self._pm.grant_all_permissions(plugin_id)
                self._set_status(_("plugin_install_success", name=data.get("name", plugin_id)))
            else:
                self._set_status(f"{_('error')}: {msg}")

        except Exception as e:
            self._set_status(f"{_('error')}: {e}")

        self._refresh()

    def _set_status(self, text: str):
        """设置状态栏文字"""
        if hasattr(self, '_status_label') and self._status_label.winfo_exists():
            self._status_label.configure(text=text)
