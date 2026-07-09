"""插件权限确认弹窗"""

from typing import List, Optional

import customtkinter as ctk

from plugin_manager.permissions import (
    PermissionRiskLevel,
    PluginPermission,
    classify_permissions,
    get_permission_display_key,
    get_permission_risk,
)
from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _

# 风险等级颜色
RISK_COLORS = {
    PermissionRiskLevel.LOW: "#2ecc71",
    PermissionRiskLevel.MEDIUM: "#f39c12",
    PermissionRiskLevel.HIGH: "#e74c3c",
}

RISK_LABELS = {
    PermissionRiskLevel.LOW: "plugin_permission_risk_low",
    PermissionRiskLevel.MEDIUM: "plugin_permission_risk_medium",
    PermissionRiskLevel.HIGH: "plugin_permission_risk_high",
}


class PluginPermissionDialog(ctk.CTkToplevel):
    """插件权限确认弹窗

    用法:
        dialog = PluginPermissionDialog(parent, plugin_name="My Plugin", permissions=["network.http", "ui.extend"])
        result = dialog.get_result()  # True/False
    """

    def __init__(self, parent, plugin_name: str, permissions: List[str], title: Optional[str] = None):
        super().__init__(parent)
        self._result: bool = False

        classified = classify_permissions(permissions)

        self.title(title or _("plugin_permission_dialog_title", name=plugin_name))
        self.geometry("520x480")
        self.resizable(False, False)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)

        try:
            self.grab_set()
        except Exception:
            pass

        self._build_ui(plugin_name, classified)

        # 居中
        self.update_idletasks()
        self._center_on_parent(parent)

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

    def get_result(self) -> bool:
        """获取用户选择结果"""
        self.wait_window()
        return self._result

    def _build_ui(self, plugin_name: str, classified: dict):
        """构建 UI"""
        # ── 标题 ──
        ctk.CTkLabel(
            self,
            text=_("plugin_permission_dialog_title", name=plugin_name),
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W, padx=20, pady=(20, 4))

        ctk.CTkLabel(
            self,
            text=_("plugin_permission_dialog_subtitle"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=470,
        ).pack(anchor=ctk.W, padx=20, pady=(0, 12))

        # ── 可滚动权限列表 ──
        scroll_frame = ctk.CTkScrollableFrame(self, fg_color=COLORS["bg_medium"], width=480, height=280)
        scroll_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=(0, 12))

        # 按风险等级从高到低排列
        for risk in (PermissionRiskLevel.HIGH, PermissionRiskLevel.MEDIUM, PermissionRiskLevel.LOW):
            perms = classified.get(risk, [])
            if not perms:
                continue

            # 风险等级标题
            risk_color = RISK_COLORS.get(risk, COLORS["text_secondary"])
            header_frame = ctk.CTkFrame(scroll_frame, fg_color="transparent")
            header_frame.pack(fill=ctk.X, padx=4, pady=(8, 2))

            ctk.CTkLabel(
                header_frame,
                text=_(RISK_LABELS.get(risk, "")),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
                text_color=risk_color,
            ).pack(side=ctk.LEFT, padx=2)

            # 分级说明
            risk_tips = {
                PermissionRiskLevel.LOW: "plugin_permission_risk_low_tip",
                PermissionRiskLevel.MEDIUM: "plugin_permission_risk_medium_tip",
                PermissionRiskLevel.HIGH: "plugin_permission_risk_high_tip",
            }
            ctk.CTkLabel(
                header_frame,
                text=_(risk_tips.get(risk, "")),
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
            ).pack(side=ctk.RIGHT, padx=2)

            # 权限项
            for perm in perms:
                item_frame = ctk.CTkFrame(scroll_frame, fg_color=COLORS["card_bg"], corner_radius=6)
                item_frame.pack(fill=ctk.X, padx=4, pady=2)

                # 左侧颜色条
                ctk.CTkFrame(item_frame, fg_color=risk_color, width=3, corner_radius=2).pack(
                    side=ctk.LEFT, fill=ctk.Y, padx=(0, 8)
                )

                # 权限名称
                ctk.CTkLabel(
                    item_frame,
                    text=_(get_permission_display_key(perm)),
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                    text_color=COLORS["text_primary"],
                ).pack(side=ctk.LEFT, padx=2, pady=6)

                # 权限标识
                ctk.CTkLabel(
                    item_frame,
                    text=perm.value,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                    text_color=COLORS["text_secondary"],
                ).pack(side=ctk.RIGHT, padx=8, pady=6)

        # ── 提示 ──
        ctk.CTkLabel(
            self,
            text=_("plugin_permission_dialog_footer"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=470,
        ).pack(anchor=ctk.W, padx=20, pady=(0, 4))

        # ── 按钮 ──
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill=ctk.X, padx=20, pady=(0, 16))

        ctk.CTkButton(
            btn_frame,
            text=_("plugin_permission_deny"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_secondary"],
            command=self._on_deny,
        ).pack(side=ctk.LEFT, padx=(0, 8))

        ctk.CTkButton(
            btn_frame,
            text=_("plugin_permission_approve"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_approve,
        ).pack(side=ctk.RIGHT)

    def _on_approve(self):
        self._result = True
        self.destroy()

    def _on_deny(self):
        self._result = False
        self.destroy()
