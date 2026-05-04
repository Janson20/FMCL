"""ModernApp 成就 Mixin - 成就标签页"""

from typing import List, Dict, Any

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


class AchievementTabMixin(object):
    """成就标签页 Mixin"""

    def _build_achievements_tab_content(self):
        """构建成就标签页内容"""
        content = ctk.CTkFrame(self.achievements_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        stats_frame = ctk.CTkFrame(content, fg_color=COLORS["card_bg"], corner_radius=8)
        stats_frame.pack(fill=ctk.X, padx=5, pady=(10, 10))

        stats_inner = ctk.CTkFrame(stats_frame, fg_color="transparent")
        stats_inner.pack(fill=ctk.X, padx=20, pady=15)

        self.ach_stats_title = ctk.CTkLabel(
            stats_inner,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self.ach_stats_title.pack(side=ctk.LEFT)

        self.ach_stats_detail = ctk.CTkLabel(
            stats_inner,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self.ach_stats_detail.pack(side=ctk.RIGHT)

        self.ach_progress_bar = ctk.CTkProgressBar(
            stats_frame, height=6, fg_color=COLORS["bg_medium"], progress_color=COLORS["accent"]
        )
        self.ach_progress_bar.pack(fill=ctk.X, padx=20, pady=(0, 15))
        self.ach_progress_bar.set(0)

        self.ach_scroll = ctk.CTkScrollableFrame(
            content, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        self.ach_scroll.pack(fill=ctk.BOTH, expand=True, padx=5, pady=(0, 10))

        self.ach_category_frames: Dict[str, ctk.CTkFrame] = {}
        self.ach_card_refs: list = []

        self._theme_refs.append((self.ach_stats_title, {"text_color": "text_primary"}))
        self._theme_refs.append((self.ach_stats_detail, {"text_color": "text_secondary"}))
        self._theme_refs.append((self.ach_progress_bar, {"fg_color": "bg_medium", "progress_color": "accent"}))
        self._theme_refs.append((self.ach_scroll, {"scrollbar_button_color": "bg_light"}))

        self._ach_data_cache: List[Dict[str, Any]] = []

    def _refresh_achievements(self):
        """刷新成就标签页"""
        if not hasattr(self, 'ach_scroll') or not self.ach_scroll.winfo_exists():
            return

        from achievement_engine import get_achievement_engine
        engine = get_achievement_engine()
        if engine is None:
            return

        data = engine.get_all()
        self._ach_data_cache = data
        self._render_achievement_data(data)

    def _render_achievement_data(self, data: List[Dict[str, Any]]):
        """渲染成就数据"""
        for w in self.ach_scroll.winfo_children():
            w.destroy()
        self.ach_category_frames.clear()
        self.ach_card_refs.clear()

        total = 0
        unlocked = 0
        unlocked_stages = 0
        total_stages = 0

        for cat_data in data:
            ach_list = cat_data["achievements"]
            for a in ach_list:
                total += 1
                total_stages += a["max_stage"]
                if a["progress_stage"] > 0:
                    unlocked += 1
                    unlocked_stages += a["progress_stage"]

        self.ach_stats_title.configure(text=_("ach_stats_title"))
        self.ach_stats_detail.configure(text=_("ach_stats_detail", unlocked=unlocked, total=total))
        pct = round(unlocked / total * 100, 1) if total > 0 else 0
        self.ach_progress_bar.set(pct / 100)

        for cat_data in data:
            self._render_category_section(cat_data)

    def _render_category_section(self, cat_data: Dict[str, Any]):
        """渲染单个分类区域"""
        cat_meta = cat_data["category_meta"]
        cat_key = cat_data["category"]
        achievements = cat_data["achievements"]

        cat_header = ctk.CTkFrame(self.ach_scroll, fg_color="transparent", height=36)
        cat_header.pack(fill=ctk.X, pady=(12, 6))
        cat_header.pack_propagate(False)

        cat_icon = cat_meta.get("icon", "🏆")
        cat_name_key = cat_meta.get("i18n_key", "ach_category_unknown")
        cat_name = _(cat_name_key)

        ctk.CTkLabel(
            cat_header,
            text=f"{cat_icon}  {cat_name}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT, padx=8)

        cat_unlocked = sum(1 for a in achievements if a["progress_stage"] > 0)
        ctk.CTkLabel(
            cat_header,
            text=f"{cat_unlocked}/{len(achievements)}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.RIGHT, padx=12)

        sep = ctk.CTkFrame(self.ach_scroll, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, padx=4, pady=(0, 4))

        for ach in achievements:
            self._render_achievement_card(ach)

    def _render_achievement_card(self, ach: Dict[str, Any]):
        """渲染单个成就卡片"""
        unlocked = ach["progress_stage"] > 0
        maxed = ach["progress_stage"] >= ach["max_stage"]

        card = ctk.CTkFrame(
            self.ach_scroll,
            fg_color=COLORS["card_bg"] if unlocked else COLORS["bg_medium"],
            corner_radius=8,
        )
        card.pack(fill=ctk.X, pady=3, padx=4)

        card_inner = ctk.CTkFrame(card, fg_color="transparent")
        card_inner.pack(fill=ctk.BOTH, padx=12, pady=10)

        icon_label = ctk.CTkLabel(
            card_inner,
            text=ach["icon"],
            font=ctk.CTkFont(size=22),
            width=36,
        )
        icon_label.pack(side=ctk.LEFT, padx=(0, 10))

        info_frame = ctk.CTkFrame(card_inner, fg_color="transparent")
        info_frame.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        ach_name = _(ach["i18n_key"])
        ach_desc = _(ach["desc_i18n_key"])

        name_color = COLORS["accent"] if unlocked else COLORS["text_primary"]
        ctk.CTkLabel(
            info_frame,
            text=ach_name,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=name_color,
            anchor=ctk.W,
        ).pack(fill=ctk.X)

        ctk.CTkLabel(
            info_frame,
            text=ach_desc,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
        ).pack(fill=ctk.X)

        if ach["max_stage"] > 1:
            prog_frame = ctk.CTkFrame(info_frame, fg_color="transparent", height=18)
            prog_frame.pack(fill=ctk.X, pady=(4, 0))
            prog_frame.pack_propagate(False)

            pct = ach["progress_percent"]
            progress_bar = ctk.CTkProgressBar(
                prog_frame,
                height=5,
                fg_color=COLORS["bg_medium"],
                progress_color=COLORS["accent"] if unlocked else COLORS["card_border"],
            )
            progress_bar.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
            progress_bar.set(pct / 100)

            stage_text = ach["current_stage_name"] or ""
            if maxed:
                stage_text = _("ach_maxed")
            elif ach["next_threshold"] is not None:
                stage_text = f"{ach['progress_current']}/{ach['next_threshold']}"

            ctk.CTkLabel(
                prog_frame,
                text=stage_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
                width=70,
            ).pack(side=ctk.RIGHT)

        stage_badge = ctk.CTkFrame(card_inner, fg_color="transparent")
        stage_badge.pack(side=ctk.RIGHT, padx=(10, 0))

        if unlocked:
            stage_name = ach["current_stage_name"]
            badge_color = COLORS["success"] if maxed else COLORS["accent"]
            badge_text = "✓" if ach["max_stage"] == 1 else stage_name
            ctk.CTkLabel(
                stage_badge,
                text=badge_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
                text_color=badge_color,
            ).pack()
        else:
            ctk.CTkLabel(
                stage_badge,
                text="🔒",
                font=ctk.CTkFont(size=14),
                text_color=COLORS["text_secondary"],
            ).pack()

    def _on_achievement_unlock(self, ach_def, stage: int, stage_name: str):
        """成就解锁回调 - 显示 Toast 通知并刷新"""
        from ui.dialogs import show_toast_notification
        from ui.i18n import _

        if self.winfo_exists():
            icon = ach_def.icon
            name = _(ach_def.i18n_key)
            subtitle = f"🏆 {_('ach_unlocked')}"

            if stage_name:
                subtitle = stage_name

            def _do_toast():
                show_toast_notification(self, icon, name, subtitle)

            self.after(100, _do_toast)
            self.after(600, self._refresh_achievements)

    def _refresh_ach_colors(self):
        """刷新成就标签页颜色（主题切换时调用）"""
        if not hasattr(self, 'ach_scroll') or not self.ach_scroll.winfo_exists():
            return
        self._refresh_achievements()
