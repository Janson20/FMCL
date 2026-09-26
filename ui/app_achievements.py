"""ModernApp 成就 Mixin - 成就标签页 + 云同步

业务逻辑已搬到 ``services/achievement_service.py``（阶段 1 任务 1.12）：
进度数据的统计准备（总量 / 已解锁 / 百分比、分类计数）、上次同步时间的格式化、
Token 取值、同步 / 云重置 / 本地重置的调用编排、解锁载荷的归一化。
本文件只剩纯界面部分（控件构建、成就卡片渲染、**三次确认对话框**、Toast 通知、
``after`` / 线程调度）；下面每个方法都退化成对服务的薄委托，
**方法名与签名保持不变**，界面可见行为不变。
"""

import threading
from typing import Any, Dict, List

import customtkinter as ctk

from app.context import current_context
from services.achievement_service import AchievementService
from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _, get_current_language

# `import time` 因唯一两个使用点（strftime / time.time）都搬进服务而删除，
# 见报告的"必要改动"。


def _get_achievement_service(owner: Any = None) -> AchievementService:
    """惰性取得成就服务实例。

    与 ``ui/app_server.py`` 的 ``_get_server_service(owner)`` /
    ``ui/windows/resource_manager.py`` 的 ``_get_resource_service(owner)``
    同一取舍：写成**模块级函数**（而不是 Mixin 上的方法）是因为调用方
    （测试 / 探针）会以"未绑定方法 + 假对象"的方式直接调这些方法，那些假对象
    上并没有这个方法；同时给 Mixin 加方法会改变方法面（本轮要求逐字不变）。

    取法：优先用 ``AppContext`` 里注册的那个（阶段 2 接上之后），取不到就在
    ``owner`` 上造一个并缓存 —— ``AchievementService`` 不需要 ``AppContext``，
    构造期也只保存参数。
    """
    ctx = getattr(owner, "context", None) or current_context()
    if ctx is not None:
        getter = getattr(ctx, "try_get", None)
        if callable(getter):
            try:
                service = getter(AchievementService.name)
            except Exception:
                service = None
            if service is not None:
                return service
    service = getattr(owner, "_achievement_service_fallback", None)
    if service is None:
        service = AchievementService()
        try:
            owner._achievement_service_fallback = service
        except Exception:
            # owner 可能是 __slots__ 对象或 None：不缓存，本次调用照常可用
            pass
    return service


class AchievementTabMixin(object):
    """成就标签页 Mixin"""

    def _build_achievements_tab_content(self):
        """构建成就标签页内容"""
        content = ctk.CTkFrame(self.achievements_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        stats_frame = ctk.CTkFrame(content, fg_color=COLORS["card_bg"], corner_radius=8)
        stats_frame.pack(fill=ctk.X, padx=5, pady=(10, 10))

        stats_inner = ctk.CTkFrame(stats_frame, fg_color="transparent")
        stats_inner.pack(fill=ctk.X, padx=20, pady=(12, 0))

        self.ach_stats_title = ctk.CTkLabel(
            stats_inner,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self.ach_stats_title.pack(side=ctk.LEFT, pady=(0, 4))

        self.ach_sync_status = ctk.CTkLabel(
            stats_inner, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=COLORS["text_secondary"]
        )
        self.ach_sync_status.pack(side=ctk.RIGHT, padx=(10, 0), pady=(0, 4))

        self.ach_stats_detail = ctk.CTkLabel(
            stats_inner, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=COLORS["text_secondary"]
        )
        self.ach_stats_detail.pack(side=ctk.RIGHT, pady=(0, 4))

        self.ach_last_sync_label = ctk.CTkLabel(
            stats_frame, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=COLORS["text_secondary"]
        )
        self.ach_last_sync_label.pack(fill=ctk.X, padx=20, pady=(0, 2))

        self.ach_progress_bar = ctk.CTkProgressBar(
            stats_frame, height=6, fg_color=COLORS["bg_medium"], progress_color=COLORS["accent"]
        )
        self.ach_progress_bar.pack(fill=ctk.X, padx=20, pady=(0, 10))

        btn_row = ctk.CTkFrame(stats_frame, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=20, pady=(0, 12))

        self.ach_sync_btn = ctk.CTkButton(
            btn_row,
            text=_("ach_sync_btn"),
            width=120,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_ach_sync,
        )
        self.ach_sync_btn.pack(side=ctk.LEFT, padx=(0, 8))

        self.ach_reset_cloud_btn = ctk.CTkButton(
            btn_row,
            text=_("ach_reset_cloud_btn"),
            width=120,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["warning"],
            hover_color="#e67e22",
            text_color=COLORS["text_primary"],
            command=self._on_ach_reset_cloud,
        )
        self.ach_reset_cloud_btn.pack(side=ctk.LEFT, padx=(0, 8))

        self.ach_reset_local_btn = ctk.CTkButton(
            btn_row,
            text=_("ach_reset_local_btn"),
            width=120,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            text_color=COLORS["text_primary"],
            command=self._on_ach_reset_local,
        )
        self.ach_reset_local_btn.pack(side=ctk.LEFT)

        self.ach_scroll = ctk.CTkScrollableFrame(
            content, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        self.ach_scroll.pack(fill=ctk.BOTH, expand=True, padx=5, pady=(0, 10))

        self.ach_category_frames: Dict[str, ctk.CTkFrame] = {}
        self.ach_card_refs: list = []

        self._theme_refs.append((self.ach_stats_title, {"text_color": "text_primary"}))
        self._theme_refs.append((self.ach_stats_detail, {"text_color": "text_secondary"}))
        self._theme_refs.append((self.ach_sync_status, {"text_color": "text_secondary"}))
        self._theme_refs.append((self.ach_last_sync_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self.ach_progress_bar, {"fg_color": "bg_medium", "progress_color": "accent"}))
        self._theme_refs.append((self.ach_sync_btn, {"fg_color": "accent", "hover_color": "accent_hover"}))
        self._theme_refs.append((self.ach_reset_cloud_btn, {"fg_color": "warning", "text_color": "text_primary"}))
        self._theme_refs.append((self.ach_reset_local_btn, {"fg_color": "error", "text_color": "text_primary"}))
        self._theme_refs.append((self.ach_scroll, {"scrollbar_button_color": "bg_light"}))

        self._ach_data_cache: List[Dict[str, Any]] = []

    # ═══════════ 成就渲染 ═══════════

    def _refresh_achievements(self):
        """刷新成就标签页"""
        if not hasattr(self, "ach_scroll") or not self.ach_scroll.winfo_exists():
            return

        # 引擎单例的取用与 `engine.get_all()` 在服务里；引擎未初始化时
        # 服务返回 None，等价于原文这里直接 return。
        data = _get_achievement_service(self).load_progress()
        if data is None:
            return

        self._ach_data_cache = data
        self._render_achievement_data(data)
        self._update_ach_last_sync_label()

    def _render_achievement_data(self, data: List[Dict[str, Any]]):
        """渲染成就数据"""
        for w in self.ach_scroll.winfo_children():
            w.destroy()
        self.ach_category_frames.clear()
        self.ach_card_refs.clear()

        # 原文的"先数一遍总量、再遍历一遍渲染"两遍结构不变：这里只做第一遍
        summary = _get_achievement_service(self).summarize(data)

        self.ach_stats_title.configure(text=_("ach_stats_title"))
        self.ach_stats_detail.configure(
            text=_("ach_stats_detail", unlocked=summary.unlocked, total=summary.total)
        )
        self.ach_progress_bar.set(summary.percent / 100)

        for cat_data in data:
            self._render_category_section(cat_data)

    # ═══════════ 分类渲染 ═══════════

    def _render_category_section(self, cat_data: Dict[str, Any]):
        """渲染单个分类区域"""
        cat_meta = cat_data["category_meta"]
        achievements = cat_data["achievements"]

        cat_header = ctk.CTkFrame(self.ach_scroll, fg_color="transparent", height=36)
        cat_header.pack(fill=ctk.X, pady=(12, 6))
        cat_header.pack_propagate(False)

        cat_icon = cat_meta.get("icon", "🏆")
        cat_name = _(cat_meta.get("i18n_key", "ach_category_unknown"))

        ctk.CTkLabel(
            cat_header,
            text=f"{cat_icon}  {cat_name}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT, padx=8)

        cat_unlocked, cat_total = _get_achievement_service(self).category_counts(achievements)
        ctk.CTkLabel(
            cat_header,
            text=f"{cat_unlocked}/{cat_total}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.RIGHT, padx=12)

        sep = ctk.CTkFrame(self.ach_scroll, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, padx=4, pady=(0, 4))

        for ach in achievements:
            self._render_achievement_card(ach)

    # ═══════════ 成就卡片 ═══════════

    def _render_achievement_card(self, ach: Dict[str, Any]):
        """渲染单个成就卡片"""
        unlocked = ach["progress_stage"] > 0
        maxed = ach["progress_stage"] >= ach["max_stage"]

        card = ctk.CTkFrame(
            self.ach_scroll, fg_color=COLORS["card_bg"] if unlocked else COLORS["bg_medium"], corner_radius=8
        )
        card.pack(fill=ctk.X, pady=3, padx=4)

        card_inner = ctk.CTkFrame(card, fg_color="transparent")
        card_inner.pack(fill=ctk.BOTH, padx=12, pady=10)

        icon_label = ctk.CTkLabel(card_inner, text=ach["icon"], font=ctk.CTkFont(size=22), width=36)
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
            ctk.CTkLabel(stage_badge, text="🔒", font=ctk.CTkFont(size=14), text_color=COLORS["text_secondary"]).pack()

    # ═══════════ Token 获取 ═══════════

    def _get_ach_token(self) -> str:
        """获取净读 AI Token（用于云同步）"""
        return _get_achievement_service(self).get_token(self.callbacks)

    # ═══════════ 手动同步 ═══════════

    def _on_ach_sync(self):
        """手动同步按钮"""
        token = self._get_ach_token()
        if not token:
            self._show_sync_login_hint()
            return

        self.ach_sync_btn.configure(state=ctk.DISABLED, text=_("ach_syncing"))
        self._set_sync_status(_("ach_sync_status_syncing"))

        service = _get_achievement_service(self)
        engine = service.engine()
        if not engine:
            self.ach_sync_btn.configure(state=ctk.NORMAL, text=_("ach_sync_btn"))
            self._set_sync_status(_("ach_sync_failed"))
            return

        def _run():
            # 同步本体（download → merge → upload，带 engine 的锁与 last_sync_time）
            # 在服务里；线程仍由界面起，服务不自己起线程。
            ok = service.sync_with_engine(engine, token)
            if self.winfo_exists():
                self.after(0, lambda: self._on_sync_done(ok))

        threading.Thread(target=_run, daemon=True).start()

    def _on_sync_done(self, success: bool):
        self.ach_sync_btn.configure(state=ctk.NORMAL, text=_("ach_sync_btn"))
        if success:
            self._set_sync_status(_("ach_sync_success"))
            self._refresh_achievements()
        else:
            self._set_sync_status(_("ach_sync_failed"))

    def _set_sync_status(self, text: str):
        try:
            if self.ach_sync_status.winfo_exists():
                self.ach_sync_status.configure(text=text)
        except Exception:
            pass

    def _update_ach_last_sync_label(self):
        """从数据库加载上次同步时间并更新标签"""
        try:
            if not hasattr(self, "ach_last_sync_label") or not self.ach_last_sync_label.winfo_exists():
                return
            service = _get_achievement_service(self)
            ts = service.last_sync_time()
            if ts is not None:
                formatted = service.format_sync_time(ts)
                self.ach_last_sync_label.configure(text=_("ach_last_sync_time", time=formatted))
            else:
                self.ach_last_sync_label.configure(text="")
        except Exception:
            pass

    def _show_sync_login_hint(self):
        import tkinter.messagebox as messagebox

        messagebox.showinfo(_("ach_sync_login_title"), _("ach_sync_login_hint"), parent=self)

    # ═══════════ 重置云存档（三次确认） ═══════════

    def _on_ach_reset_cloud(self):
        token = self._get_ach_token()
        if not token:
            self._show_sync_login_hint()
            return
        self._triple_confirm(
            confirm_key=_("ach_reset_cloud_confirm_key"), on_confirm=lambda: self._do_reset_cloud(token)
        )

    def _do_reset_cloud(self, token: str):
        self.ach_reset_cloud_btn.configure(state=ctk.DISABLED)
        self._set_sync_status(_("ach_sync_status_resetting"))

        service = _get_achievement_service(self)

        def _run():
            # `reset_cloud_db` 的调用在服务里；线程仍由界面起
            ok = service.reset_cloud(token)
            if self.winfo_exists():
                self.after(0, lambda: self._on_reset_cloud_done(ok))

        threading.Thread(target=_run, daemon=True).start()

    def _on_reset_cloud_done(self, success: bool):
        self.ach_reset_cloud_btn.configure(state=ctk.NORMAL)
        self._set_sync_status(_("ach_reset_cloud_success") if success else _("ach_sync_failed"))

    # ═══════════ 重置本地成就（三次确认） ═══════════

    def _on_ach_reset_local(self):
        self._triple_confirm(confirm_key=_("ach_reset_local_confirm_key"), on_confirm=self._do_reset_local)

    def _do_reset_local(self):
        # 返回值（"到底有没有真的重置"）本轮不使用：原文无论有没有引擎都提示
        # "重置成功"，那是既有行为，改它属于另一个裁决（见服务的 reset_local）。
        _get_achievement_service(self).reset_local()
        self._set_sync_status(_("ach_reset_local_success"))
        self._refresh_achievements()

    # ═══════════ 三次确认弹窗 ═══════════

    def _triple_confirm(self, confirm_key: str, on_confirm):
        """三步确认：①点击按钮 ②确认对话框 ③输入确认短语"""
        import tkinter as tk
        import tkinter.messagebox as messagebox

        first = messagebox.askyesno(_("ach_confirm_step1_title"), _("ach_confirm_step1_msg"), parent=self)
        if not first:
            return

        dialog = tk.Toplevel(self)
        dialog.title(_("ach_confirm_step2_title"))
        dialog.resizable(False, False)
        dialog.configure(bg=COLORS["bg_dark"])
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        w, h = 420, 200
        dialog.geometry(f"{w}x{h}")
        dialog.update_idletasks()
        x = (dialog.winfo_screenwidth() - w) // 2
        y = (dialog.winfo_screenheight() - h) // 2
        dialog.geometry(f"{w}x{h}+{x}+{y}")

        pad = 24

        tk.Label(
            dialog,
            text=_("ach_confirm_step2_title"),
            font=(FONT_FAMILY, 14, "bold"),
            fg=COLORS["error"],
            bg=COLORS["bg_dark"],
        ).place(x=pad, y=pad)

        tk.Label(
            dialog,
            text=_("ach_confirm_step2_msg", phrase=confirm_key),
            font=(FONT_FAMILY, 11),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_dark"],
            wraplength=370,
            justify="left",
        ).place(x=pad, y=pad + 35)

        entry = tk.Entry(
            dialog,
            font=(FONT_FAMILY, 12),
            bg=COLORS["bg_medium"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
            relief="flat",
            bd=0,
        )
        entry.place(x=pad, y=pad + 90, width=w - 2 * pad, height=30)

        error_label = tk.Label(dialog, text="", font=(FONT_FAMILY, 10), fg=COLORS["error"], bg=COLORS["bg_dark"])
        error_label.place(x=pad, y=pad + 130)

        def _confirm():
            if entry.get().strip() == confirm_key:
                dialog.grab_release()
                dialog.destroy()
                on_confirm()
            else:
                error_label.configure(text=_("ach_confirm_mismatch"))

        tk.Button(
            dialog,
            text=_("confirm"),
            width=10,
            bg=COLORS["error"],
            fg="white",
            relief="flat",
            activebackground="#c0392b",
            activeforeground="white",
            font=(FONT_FAMILY, 11),
            command=_confirm,
        ).place(x=w - pad - 100, y=pad + 135)

        tk.Button(
            dialog,
            text=_("cancel"),
            width=10,
            bg=COLORS["bg_medium"],
            fg=COLORS["text_primary"],
            relief="flat",
            activebackground=COLORS["card_border"],
            font=(FONT_FAMILY, 11),
            command=lambda: (dialog.grab_release(), dialog.destroy()),
        ).place(x=w - pad - 210, y=pad + 135)

        entry.bind("<Return>", lambda e: _confirm())
        entry.focus_set()
        dialog.wait_window()

    # ═══════════ 成就解锁回调 ═══════════

    def _on_achievement_unlock(self, ach_def, stage: int, stage_name: str):
        """成就解锁回调 - 显示 Toast 通知 + 后台同步"""
        from ui.dialogs import show_toast_notification

        if self.winfo_exists():
            service = _get_achievement_service(self)
            # "哪一项成就、第几阶段解锁了"由引擎判定并回调进来；这里只把
            # `ach_def` 归一成界面渲染 Toast 需要的字段（图标 / 文案键 / 阶段名）。
            notice = service.unlock_notice(ach_def, stage, stage_name)
            token = self._get_ach_token()

            def _do_toast():
                show_toast_notification(self, notice.icon, _(notice.i18n_key), notice.stage_name)

            self.after(100, _do_toast)
            self.after(600, self._refresh_achievements)

            if token:

                def _push_sync():
                    # 推送编排（含成功后才写 last_sync_time）在服务里；
                    # 线程仍由界面起，刷新标签仍回主线程。
                    if service.push_sync(token) and self.winfo_exists():
                        self.after(0, self._update_ach_last_sync_label)

                threading.Thread(target=_push_sync, daemon=True).start()

    # ═══════════ 主题刷新 ═══════════

    def _refresh_ach_colors(self):
        """刷新成就标签页颜色（主题切换时调用）"""
        if not hasattr(self, "ach_scroll") or not self.ach_scroll.winfo_exists():
            return
        self._refresh_achievements()
