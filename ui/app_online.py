"""ModernApp 联机 Mixin - 陶瓦联机标签页相关方法

业务逻辑已搬到 ``services/online_service.py``（阶段 1 任务 1.5）：EasyTier 检测 /
测速选镜像 / 下载解压 / 启停、陶瓦（Scaffolding）大厅协议、大厅码编解码与校验、
局域网广播世界发现、MC Ping、TCP 端口转发、peer 探测与 easytier-cli 参数拼装。
本文件只剩纯界面部分（控件构建、``after`` 调度、通知弹窗、剪贴板、日志框投递、
i18n 文案）；下面每个受影响的方法都退化成对服务的**薄委托**，
**方法名与签名保持不变**，界面可见行为不变。

两条既有缺陷的处置（任务 1.24）：

- **D-90 已修正**：联机日志的投递改成"**worker 只入队 + 主线程轮询 drain**"。
  ``_append_online_log`` 现在**任何线程**调用都只往队列里放一条字符串，不碰
  控件、不调 ``winfo_exists()``、不调 ``after``；渲染由主线程的
  ``_drain_online_log`` 完成（定时器只在 ``_build_online_output_panel`` 里由主线程
  排期）。渲染逻辑单独放在 ``_do_append_online_log`` 里，签名与旧实现无关。
- **D-83 已修正**（有意的行为变更）：旧行为是 ``download()`` 在 worker 线程里无声
  删掉版本根下所有非当前版本目录；现在服务层只给"看起来是旧版本"的清单，
  由本文件在下载完成的回调（**主线程**）里用对话框问用户，确认后才删。
  没人确认 → 一个目录都不删。

迁移前的公开名（``EasyTierManager`` / ``ScaffoldingClient`` / ``LobbyCodeGenerator``
……）在本模块**继续可导入**，且与 ``services.online_service`` 里的是**同一批对象**
（``ui.app_online.X is services.online_service.X``）—— 与阶段 1 其余的搬家保持同一
语义：通过旧路径打补丁、做 ``is`` 判定都与搬家前一致。
"""

import queue
import threading
from tkinter import messagebox
from typing import Any, Optional

import customtkinter as ctk
from logzero import logger

from app.context import current_context
from services.online_service import (
    AD_RE,
    EASYTIER_DOWNLOAD_URLS,
    EASYTIER_VERSION,
    HOST_VIRTUAL_IP,
    LOOPBACK,
    MC_MULTICAST_GROUP,
    MC_MULTICAST_GROUP_V6,
    MOTD_RE,
    BroadcastListener,
    EasyTierManager,
    GameWatcher,
    LobbyCodeGenerator,
    LobbyInfo,
    LobbyState,
    McBroadcastSimulator,
    McPing,
    OnlineService,
    ScaffoldingClient,
    ScaffoldingServer,
    TcpPortForwarder,
)
from ui.constants import COLORS, FONT_FAMILY
from ui.dialogs import show_notification
from ui.i18n import _
from ui.log_widget import append_line

#: 联机日志的"入队 → 主线程渲染"轮询间隔（毫秒）与单次最多渲染的行数。
#: 只在主线程排期（``_build_online_output_panel``），worker 线程永远只入队 ——
#: 这正是 ``ui/ports_tk.TkUIPort`` 用的那套"队列 + 主线程 after 轮询"模型。
#: 一次 drain 最多渲染 200 行，避免积压几千行时卡住主线程；剩下的留给下一拍。
ONLINE_LOG_DRAIN_MS = 120
ONLINE_LOG_DRAIN_BATCH = 200

#: 搬家前本模块公开过的名字，继续从这里可导入（都是服务层那批对象的别名）。
__all__ = [
    "AD_RE",
    "EASYTIER_DOWNLOAD_URLS",
    "EASYTIER_VERSION",
    "HOST_VIRTUAL_IP",
    "LOOPBACK",
    "MC_MULTICAST_GROUP",
    "MC_MULTICAST_GROUP_V6",
    "MOTD_RE",
    "BroadcastListener",
    "EasyTierManager",
    "GameWatcher",
    "LobbyCodeGenerator",
    "LobbyInfo",
    "LobbyState",
    "McBroadcastSimulator",
    "McPing",
    "OnlineService",
    "OnlineTabMixin",
    "ScaffoldingClient",
    "ScaffoldingServer",
    "TcpPortForwarder",
]


def _get_online_service(owner: Any = None) -> OnlineService:
    """惰性取得联机服务实例。

    写成**模块级函数**（而不是只在 Mixin 上的方法）是因为联机 Mixin 的方法会被
    测试与探针以"未绑定方法 + 假对象"的方式直接调用，那些假对象上并没有
    ``_online_service`` 方法。``ui/app_server.py`` 的 ``_get_server_service(owner)``
    正是同一个形式；``ui/app_tools.py`` 的 ``_tool_service()`` 用的是同一套查找
    顺序（context.try_get → 自造并缓存），只是写成了方法。

    取法：优先用 ``AppContext`` 里注册的那个（阶段 2 接上之后），取不到就在
    ``owner`` 上造一个并缓存 —— ``OnlineService`` 不需要 ``AppContext``，
    构造期也只保存参数。
    """
    ctx = getattr(owner, "context", None) or current_context()
    if ctx is not None:
        getter = getattr(ctx, "try_get", None)
        if callable(getter):
            try:
                service = getter(OnlineService.name)
            except Exception:
                service = None
            if service is not None:
                return service
    service = getattr(owner, "_online_service_fallback", None)
    if service is None:
        service = OnlineService()
        try:
            owner._online_service_fallback = service
        except Exception:
            # owner 可能是 __slots__ 对象或 None：不缓存，本次调用照常可用
            pass
    return service


class OnlineTabMixin(object):
    """联机标签页 Mixin - 陶瓦联机功能"""

    # ── 服务层接线（实现在 services/online_service.py）──────────────
    #
    # 方法体里统一用**模块级** ``_get_online_service(self)`` 而不是
    # ``self._online_service()`` 那种写法：联机 Mixin 的方法会被测试与探针以
    # "未绑定方法 + 假对象"的方式直接调用，那些假对象上没有这个属性。
    # ``ui/app_server.py`` 的 ``_get_server_service(self)`` 是同一取舍。

    def _online_service(self) -> OnlineService:
        """惰性取得联机服务实例（见模块级 :func:`_get_online_service`）。"""
        return _get_online_service(self)

    def _build_online_tab_content(self):
        if not _get_online_service(self).is_platform_supported():
            self._build_online_unsupported_tab()
            return

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

        self._et_manager: Optional[EasyTierManager] = None
        self._lobby_info: Optional[LobbyInfo] = None
        self._is_host: bool = False
        self._broadcast_sim: Optional[McBroadcastSimulator] = None
        self._tcp_forwarder: Optional[TcpPortForwarder] = None
        self._local_mc_port: int = 0
        self._public_address: Optional[str] = None
        self._member_poll_id: Optional[str] = None
        self._scf_client: Optional[ScaffoldingClient] = None
        self._game_watcher: Optional[GameWatcher] = None
        self._lobby_state = LobbyState.IDLE

        self.after(200, self._init_online_state)

    def _build_online_unsupported_tab(self):
        content = ctk.CTkFrame(self.online_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        ctk.CTkLabel(
            content,
            text=_("online_unsupported_platform"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15),
            text_color=COLORS["text_secondary"],
            wraplength=600,
            justify=ctk.CENTER,
        ).pack(expand=True)

    def _init_online_state(self):
        self._et_manager = _get_online_service(self).create_manager()
        self._et_manager._ensure_relay_prefetch()
        if self._et_manager.is_installed:
            self._append_online_log(f"[FMCL] EasyTier {EASYTIER_VERSION} " + _("online_et_ready"))
            self._update_env_easytier_label(_("online_et_ready"), "success")
        else:
            self._append_online_log("[FMCL] " + _("online_et_not_found"))
            self._update_env_easytier_label(_("online_et_not_found"), "error")

    def _build_online_control_panel(self, parent):
        self._online_control_frame = ctk.CTkScrollableFrame(
            parent, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        self._online_control_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 10))

        self._build_online_env_section(self._online_control_frame)
        self._build_online_discover_section(self._online_control_frame)
        self._build_online_create_section(self._online_control_frame)
        self._build_online_join_section(self._online_control_frame)
        self._build_online_lobby_section(self._online_control_frame)
        self._build_online_tips_section(self._online_control_frame)
        self._build_online_compat_section(self._online_control_frame)

        self._theme_refs.append((self._online_control_frame, {"scrollbar_button_color": "bg_light"}))

    def _build_online_env_section(self, parent):
        card = ctk.CTkFrame(
            parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"]
        )
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

        et_row = ctk.CTkFrame(inner, fg_color="transparent")
        et_row.pack(fill=ctk.X, pady=(0, 8))

        ctk.CTkLabel(et_row, text="📦", font=ctk.CTkFont(size=14), text_color=COLORS["text_secondary"]).pack(
            side=ctk.LEFT, padx=(0, 4)
        )

        self._online_env_et_label = ctk.CTkLabel(
            et_row,
            text=_("online_et_checking"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._online_env_et_label.pack(side=ctk.LEFT)

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
        self._theme_refs.append((self._online_env_et_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._online_env_setup_btn, {"fg_color": "accent", "hover_color": "accent_hover"}))

    def _build_online_discover_section(self, parent):
        card = ctk.CTkFrame(
            parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"]
        )
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        title_row = ctk.CTkFrame(inner, fg_color="transparent")
        title_row.pack(fill=ctk.X)

        ctk.CTkLabel(
            title_row,
            text="🔍 " + _("online_discover_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self._online_discover_btn = ctk.CTkButton(
            title_row,
            text="🔄 " + _("online_discover_btn"),
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_discover_worlds,
        )
        self._online_discover_btn.pack(side=ctk.RIGHT)

        self._online_discover_list_label = ctk.CTkLabel(
            inner,
            text=_("online_no_worlds"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        )
        self._online_discover_list_label.pack(anchor=ctk.W, pady=(6, 0))

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_discover_list_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._online_discover_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))

    def _set_lobby_state(self, new_state: LobbyState):
        if not hasattr(self, "_lobby_state"):
            self._lobby_state = LobbyState.IDLE
        self._lobby_state = _get_online_service(self).lobby_state_transition(self._lobby_state, new_state)

    def _on_discover_worlds(self):
        if not self._et_manager or not self._et_manager.is_installed:
            self._append_online_log("[FMCL] " + _("online_et_not_found"))
            return

        self._online_discover_btn.configure(state=ctk.DISABLED, text="🔄 " + _("online_discovering"))
        self._online_discover_list_label.configure(text=_("online_discovering"))
        self._append_online_log("[FMCL] " + _("online_discovering"))
        self._set_lobby_state(LobbyState.DISCOVERING)

        recorded_ports: set = set()
        listener = _get_online_service(self).create_broadcast_listener(True)

        def on_world_found(port: int, motd: str):
            if port in recorded_ports:
                return
            recorded_ports.add(port)
            self.after(0, lambda: self._on_world_discovered(port, motd))

        listener.on_receive = on_world_found
        listener.start()

        def finish_discovery():
            listener.stop()
            self._online_discover_btn.configure(state=ctk.NORMAL, text="🔄 " + _("online_discover_btn"))
            if not recorded_ports:
                self._online_discover_list_label.configure(text=_("online_no_worlds"))
            self._set_lobby_state(LobbyState.INITIALIZED)

        self.after(3500, finish_discovery)

    def _on_world_discovered(self, port: int, motd: str):
        def _update():
            text = f"🎮 {motd} (:{port})"
            current = self._online_discover_list_label.cget("text")
            if current == _("online_no_worlds") or current == _("online_discovering"):
                self._online_discover_list_label.configure(text=text)
            elif text not in current:
                self._online_discover_list_label.configure(text=current + "\n" + text)

        self.after(0, _update)

    def _build_online_create_section(self, parent):
        card = ctk.CTkFrame(
            parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"]
        )
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_create_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_create_desc"),
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

        self._online_create_btn = ctk.CTkButton(
            inner,
            text=_("online_create_lobby"),
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=self._on_create_lobby,
        )
        self._online_create_btn.pack(fill=ctk.X, pady=(12, 0))

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_port_entry, {"fg_color": "bg_medium", "border_color": "card_border"}))
        self._theme_refs.append((self._online_create_btn, {"fg_color": "success"}))

    def _build_online_join_section(self, parent):
        card = ctk.CTkFrame(
            parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"]
        )
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_join_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_join_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(4, 0))

        ctk.CTkLabel(
            inner,
            text=_("online_lobby_code_label"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(10, 0))

        self._online_lobby_code_entry = ctk.CTkEntry(
            inner,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=_("online_lobby_code_placeholder"),
        )
        self._online_lobby_code_entry.pack(fill=ctk.X, pady=(4, 0))

        self._online_join_btn = ctk.CTkButton(
            inner,
            text=_("online_join_lobby"),
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_join_lobby,
        )
        self._online_join_btn.pack(fill=ctk.X, pady=(12, 0))

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append(
            (self._online_lobby_code_entry, {"fg_color": "bg_medium", "border_color": "card_border"})
        )
        self._theme_refs.append((self._online_join_btn, {"fg_color": "accent", "hover_color": "accent_hover"}))

    def _build_online_lobby_section(self, parent):
        card = ctk.CTkFrame(
            parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"]
        )
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_lobby_info_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        self._online_lobby_code_display_label = ctk.CTkLabel(
            inner,
            text=_("online_no_lobby"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["accent"],
            wraplength=340,
        )
        self._online_lobby_code_display_label.pack(anchor=ctk.W, pady=(8, 0))

        self._online_lobby_status_label = ctk.CTkLabel(
            inner, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=COLORS["text_secondary"]
        )
        self._online_lobby_status_label.pack(anchor=ctk.W, pady=(4, 0))

        self._online_members_label = ctk.CTkLabel(
            inner,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        )
        self._online_members_label.pack(anchor=ctk.W, pady=(4, 0))

        btn_frame = ctk.CTkFrame(inner, fg_color="transparent")
        btn_frame.pack(fill=ctk.X, pady=(10, 0))

        self._online_copy_code_btn = ctk.CTkButton(
            btn_frame,
            text=_("online_copy_code"),
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_copy_code,
        )
        self._online_copy_code_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 4))

        self._online_leave_btn = ctk.CTkButton(
            btn_frame,
            text=_("online_leave_lobby"),
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            text_color=COLORS["text_primary"],
            command=self._on_leave_lobby,
        )
        self._online_leave_btn.pack(side=ctk.RIGHT)
        self._online_leave_btn.configure(state=ctk.DISABLED)

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_lobby_code_display_label, {"text_color": "accent"}))
        self._theme_refs.append((self._online_lobby_status_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._online_copy_code_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))
        self._theme_refs.append((self._online_leave_btn, {"fg_color": "error", "text_color": "text_primary"}))

    def _build_online_tips_section(self, parent):
        card = ctk.CTkFrame(
            parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"]
        )
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

    def _build_online_compat_section(self, parent):
        card = ctk.CTkFrame(
            parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"]
        )
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_compat_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_compat_content"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(4, 0))

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))

    def _build_online_output_panel(self, parent):
        self._online_output_frame = ctk.CTkFrame(
            parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"]
        )
        self._online_output_frame.pack(side=ctk.RIGHT, fill=ctk.BOTH, expand=True, padx=(0, 0))

        frame = self._online_output_frame

        title_frame = ctk.CTkFrame(frame, fg_color="transparent", height=40)
        title_frame.pack(fill=ctk.X, padx=15, pady=(12, 0))
        title_frame.pack_propagate(False)

        ctk.CTkLabel(
            title_frame,
            text=_("online_output_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self._online_status_label = ctk.CTkLabel(
            title_frame,
            text=_("online_et_stopped"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._online_status_label.pack(side=ctk.RIGHT)

        ctk.CTkLabel(
            title_frame,
            text=_("online_log_note"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(2, 0))

        ctk.CTkFrame(frame, fg_color=COLORS["card_border"], height=1).pack(fill=ctk.X, padx=15, pady=(8, 5))

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
        # D-90：轮询只在主线程排期；上面那条日志已经在队列里，第一拍就会渲染出来。
        self._start_online_log_drain()

        self._theme_refs.append((self._online_output_frame, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_status_label, {"text_color": "text_secondary"}))
        self._theme_refs.append(
            (
                self._online_log_text,
                {"fg_color": "bg_dark", "border_color": "card_border", "text_color": "text_primary"},
            )
        )

    # ── 联机日志的投递（D-90，任务 1.24 修正）────────────────────────────
    #
    # 旧实现（1.5 及以前）在工作线程里直接调 `winfo_exists()` + `after(0, ...)`：
    # `EasyTierManager._read_output` 的 reader 线程回调 on_output，最终走到这里。
    # Tk 下不可靠、Qt 下必崩（AGENTS.md 明令禁止）。
    #
    # 现在把"投递"与"渲染"彻底分开：
    #   worker 线程（任意线程） → _append_online_log → 只往 queue.Queue 放字符串
    #   主线程                  → _drain_online_log（after 轮询）→ _do_append_online_log
    # 队列与定时器都为**懒创建**：本 Mixin 没有 __init__，且方法会被测试/探针以
    # "未绑定方法 + 假对象"的方式直接调用（见模块里 _get_online_service 的说明）。

    def _online_log_queue(self) -> queue.Queue:
        """取（必要时创建）本页的日志队列。任意线程可调用。"""
        q = getattr(self, "_online_log_q", None)
        if q is None:
            lock = getattr(self, "_online_log_lock", None)
            if lock is None:
                lock = threading.Lock()
                self._online_log_lock = lock
            with lock:
                q = getattr(self, "_online_log_q", None)
                if q is None:
                    q = queue.Queue()
                    self._online_log_q = q
        return q

    def _append_online_log(self, message: str):
        """把一条日志**入队**（线程安全，任意线程可调用）。

        D-90（任务 1.24 修正）：本函数**不碰任何控件、不调 ``winfo_exists()``、
        不调 ``after``** —— 服务层的 reader 线程、下载进度回调、后台任务回调都可以
        直接调用它。真正的渲染在主线程的 :meth:`_drain_online_log` 里做。
        界面销毁后队列里的内容自然丢弃（不会抛 TclError）。
        """
        try:
            self._online_log_queue().put(message)
        except Exception as e:  # noqa: BLE001 - 假对象/队列异常也不能把 worker 弄崩
            logger.debug(f"联机日志入队失败（已丢弃）: {e}")

    def _start_online_log_drain(self):
        """排一次主线程渲染（**只能由主线程调用**，在输出面板建好之后）。"""
        if getattr(self, "_online_log_timer", None) is not None:
            return
        try:
            self._online_log_timer = self.after(ONLINE_LOG_DRAIN_MS, self._drain_online_log)
        except Exception as e:  # noqa: BLE001 - 界面已销毁
            self._online_log_timer = None
            logger.debug(f"联机日志轮询排期失败（界面已销毁？）: {e}")

    def _drain_online_log(self):
        """主线程轮询体：把队列里的日志渲染进文本框，然后排下一拍。"""
        self._online_log_timer = None
        q = self._online_log_queue()
        rendered = 0
        while rendered < ONLINE_LOG_DRAIN_BATCH:
            try:
                message = q.get_nowait()
            except queue.Empty:
                break
            self._do_append_online_log(message)
            rendered += 1
        try:
            if not self.winfo_exists():
                return  # 界面没了就不再排期
        except Exception:  # noqa: BLE001 - TclError：界面已销毁
            return
        self._start_online_log_drain()

    def _do_append_online_log(self, message: str):
        """把一条日志写进文本框（**只能由主线程调用**，D-90 之前的旧函数体）。"""
        self._online_log_text.configure(state=ctk.NORMAL)
        # 阶段 1.22 修正（D-86）：联机日志框原先只 append、从不删除，
        # 长时间联机会让文本控件内存持续增长。append_line 限制保留最近 N 行。
        append_line(self._online_log_text, message)
        self._online_log_text.configure(state=ctk.DISABLED)

    def _set_online_status(self, message: str, color_key: str = "text_secondary"):
        def _do_set():
            self._online_status_label.configure(
                text=message, text_color=COLORS.get(color_key, COLORS["text_secondary"])
            )

        if self.winfo_exists():
            self.after(0, _do_set)

    def _update_env_easytier_label(self, text: str, color_key: str = "text_secondary"):
        def _do():
            self._online_env_et_label.configure(text=text, text_color=COLORS.get(color_key, COLORS["text_secondary"]))

        if self.winfo_exists():
            self.after(0, _do)

    def _run_online_thread(self, target, args=(), on_done=None, on_error=None):
        def wrapper():
            try:
                result = target(*args)
                if on_done:
                    self.after(0, lambda: on_done(result))
            except Exception as e:
                if on_error:
                    self.after(0, lambda err=str(e): on_error(err))
                else:
                    self.after(0, lambda err=str(e): self._append_online_log(f"[FMCL] Error: {err}"))

        threading.Thread(target=wrapper, daemon=True).start()

    def _get_display_name(self) -> str:
        if "get_jdz_username" in self.callbacks:
            name = self.callbacks["get_jdz_username"]()
            if name:
                return name
        return "Host"

    def _check_logged_in(self) -> bool:
        return not self._get_login_error()

    def _get_login_error(self) -> str:
        if "get_jdz_username" in self.callbacks and self.callbacks["get_jdz_username"]():
            return ""
        if "get_jdz_token" in self.callbacks and self.callbacks["get_jdz_token"]():
            return _("online_login_expired")
        return _("online_need_login")

    def _start_member_poll(self):
        self._refresh_members()
        self._member_poll_id = self.after(5000, self._poll_members_loop)

    def _poll_members_loop(self):
        if self._is_host:
            if not self._et_manager or not self._et_manager._scf_server:
                self._member_poll_id = None
                return
        else:
            if not self._scf_client or not self._scf_client.is_connected:
                self._member_poll_id = None
                return
        self._refresh_members()
        self._member_poll_id = self.after(5000, self._poll_members_loop)

    def _stop_member_poll(self):
        if self._member_poll_id:
            self.after_cancel(self._member_poll_id)
            self._member_poll_id = None

    def _refresh_members(self):
        if self._is_host:
            if not self._et_manager or not self._et_manager._scf_server:
                return
            profiles = self._et_manager._scf_server.all_profiles
        else:
            if not self._scf_client or not self._scf_client.is_connected:
                return
            profiles = self._scf_client.profiles
        profiles = _get_online_service(self).sort_player_list(profiles)
        lines = _get_online_service(self).format_member_lines(profiles)
        if not lines:
            lines.append(_("online_no_members"))
        lat_str = ""
        if not self._is_host and self._scf_client and self._scf_client.latency_ms > 0:
            lat_str = f"\n⏱ {self._scf_client.latency_ms}ms"
        self._online_members_label.configure(text="\n".join(lines) + lat_str)

    def _on_members_changed(self, profiles: list):
        if not self.winfo_exists():
            return
        self._refresh_members()

    def _on_server_shutdown_detected(self):
        if not self.winfo_exists():
            return
        self._append_online_log("[FMCL] " + _("online_forward_failed_et"))
        self._online_lobby_status_label.configure(text=_("online_forward_failed_et"), text_color=COLORS["error"])
        self._on_leave_lobby()

    def _on_setup_environment(self):
        if self._et_manager is None:
            self._et_manager = _get_online_service(self).create_manager()

        self._online_env_setup_btn.configure(text=_("online_env_setup_start"), state=ctk.DISABLED)
        self._append_online_log("[FMCL] " + _("online_env_setup_start"))

        def _setup():
            try:
                # D-90：on_progress 由 download() 在 worker 线程里调用 —— 这里
                # **不再 after(0, ...)**（那是"worker 直接碰 Tk"），只入队。
                ok = self._et_manager.download(
                    on_progress=lambda msg: self._append_online_log(f"[FMCL] {msg}")
                )
                return ok
            except Exception as e:
                logger.error(f"EasyTier setup failed: {e}")
                return False

        def on_done(ok):
            if ok:
                self._update_env_easytier_label(_("online_et_ready"), "success")
                self._online_env_setup_btn.configure(
                    text="✅ " + _("online_env_setup_done"), state=ctk.DISABLED, fg_color=COLORS["success"]
                )
                self._append_online_log("[FMCL] " + _("online_env_setup_done"))
                self.set_status(_("online_env_setup_done"), "success")
                # D-83（任务 1.24）：旧行为是 download() 在 worker 线程里无声删掉
                # 版本根下所有非当前版本目录。现在改成本回调（主线程）问用户。
                self._offer_stale_version_cleanup()
            else:
                self._update_env_easytier_label(_("online_et_not_found"), "error")
                self._online_env_setup_btn.configure(
                    text=_("online_env_setup_retry"), state=ctk.NORMAL, fg_color=COLORS["accent"]
                )
                self._append_online_log("[FMCL] " + _("online_env_setup_failed"))
                self.set_status(_("online_env_setup_failed"), "warning")

        def on_error(err):
            self._update_env_easytier_label(_("online_et_not_found"), "error")
            self._online_env_setup_btn.configure(
                text=_("online_env_setup_retry"), state=ctk.NORMAL, fg_color=COLORS["accent"]
            )
            self._append_online_log("[FMCL] " + str(err))

        self._run_online_thread(_setup, on_done=on_done, on_error=on_error)

    def _offer_stale_version_cleanup(self):
        """问用户要不要删掉 EasyTier 旧版本目录（D-83，任务 1.24 收口）。

        **只在主线程调用**（当前唯一调用点是 `_on_setup_environment` 的 `on_done`，
        它由 `_run_online_thread` 用 `after(0, ...)` 送回主线程）。分工是刻意的：
        **服务层只算清单**（`EasyTierManager.stale_version_dirs()`，纯读取、不删任何
        东西），"删不删"在这里问用户，确认后才让服务删（`remove_stale_versions`）。

        有意的行为变更（对照旧行为）：旧 `download()` 在 worker 线程里
        `shutil.rmtree` 掉版本根下**所有**非当前版本目录 —— 无提示、无备份、
        `ignore_errors=True` 连失败都不报。现在的规则只有一条：
        **没有界面 / 用户点"否" / 对话框本身抛异常 → 一个目录都不删。**

        i18n：本轮不允许新增键（`ui/locales/*.json` 在禁改清单里），所以文案由现成
        的键拼出：`version_count`（"{count} 个版本"）+ `ach_confirm_step1_msg`
        （"此操作不可撤销，是否继续？"），标题用 `backup_delete_confirm_title`
        （"确认删除"）—— 三个键在四份语言文件里都在，语义不作扭曲。
        """
        manager = getattr(self, "_et_manager", None)
        if manager is None:
            return
        stale = list(getattr(manager, "last_stale_version_dirs", []) or [])
        if not stale:
            return

        message = "\n\n".join(
            [
                _("version_count", count=len(stale)),
                "\n".join(str(p) for p in stale),
                _("ach_confirm_step1_msg"),
            ]
        )
        try:
            confirmed = messagebox.askyesno(_("backup_delete_confirm_title"), message, parent=self)
        except Exception as e:  # noqa: BLE001 - 没有界面/对话框失败 → 什么都不删
            logger.warning(f"询问 EasyTier 旧版本清理失败，按「不删除」处理: {e}")
            return
        if not confirmed:
            logger.info(f"用户选择保留 {len(stale)} 个 EasyTier 旧版本目录，未删除任何内容")
            return

        removed = manager.remove_stale_versions(stale)
        failures = {str(p): reason for p, reason in getattr(manager, "last_remove_failures", [])}
        for path in stale:
            reason = failures.get(str(path))
            if reason:
                self._append_online_log("[FMCL] " + _("rm_delete_failed", error=f"{path.name}: {reason}"))
            else:
                self._append_online_log("[FMCL] " + _("rm_deleted", name=path.name))
        logger.info(f"EasyTier 旧版本目录清理：成功 {removed} 个 / 共 {len(stale)} 个")

    def _on_create_lobby(self):
        login_err = self._get_login_error()
        if login_err:
            self._append_online_log("[FMCL] " + login_err)
            self.set_status(login_err, "warning")
            return

        if self._et_manager is None or not self._et_manager.is_installed:
            self._append_online_log("[FMCL] " + _("online_et_not_found"))
            self.set_status(_("online_et_not_found"), "warning")
            return

        if self._et_manager.is_running:
            self._append_online_log("[FMCL] " + _("online_et_already_running"))
            self.set_status(_("online_et_already_running"), "warning")
            return

        mc_port, port_str = _get_online_service(self).parse_mc_port(self._online_port_entry.get())
        if mc_port is None:
            self._append_online_log("[FMCL] " + _("online_invalid_port", port=port_str))
            self.set_status(_("online_invalid_port", port=port_str), "error")
            return

        self._online_create_btn.configure(state=ctk.DISABLED)
        self._online_join_btn.configure(state=ctk.DISABLED)
        self._append_online_log("[FMCL] " + _("online_creating_lobby"))
        self._set_lobby_state(LobbyState.CREATING)

        def _create():
            lobby = _get_online_service(self).generate_lobby()
            lobby.minecraft_port = mc_port
            lobby.is_host = True
            return lobby

        def on_done(lobby):
            self._lobby_info = lobby
            self._is_host = True
            self._local_mc_port = mc_port

            self._online_lobby_code_display_label.configure(text=lobby.full_code, text_color=COLORS["success"])
            self._online_lobby_status_label.configure(text="")
            self._online_leave_btn.configure(state=ctk.NORMAL)

            result = self._et_manager.launch(
                lobby,
                as_host=True,
                on_output=lambda line: self._append_online_log(line),
                on_exited=lambda code: self.after(0, lambda: self._on_easytier_exited(code)),
                player_name=self._get_display_name(),
            )

            if result == 0:
                scf = self._et_manager._scf_server
                if scf:
                    scf.on_profiles_changed = lambda profiles: self.after(0, lambda: self._on_members_changed(profiles))
                self._set_lobby_state(LobbyState.CONNECTED)
                self._game_watcher = _get_online_service(self).create_game_watcher(mc_port)
                self._game_watcher.on_game_stopped = lambda: self.after(0, self._on_host_game_stopped)
                self._game_watcher.start()
                self._set_online_status(_("online_et_connecting"), "warning")
                self._online_lobby_status_label.configure(
                    text=_("online_waiting_network"), text_color=COLORS["warning"]
                )
                self._append_online_log("[FMCL] " + _("online_lobby_created", code=lobby.full_code))
                self._append_online_log("[FMCL] " + _("online_waiting_network"))
                self.set_status(_("online_lobby_created", code=lobby.full_code), "success")
                self._trigger_ach("online_first_online")
                self._trigger_ach("online_room_owner")
                show_notification("🏠", _("notify_room_created"), lobby.full_code, notify_type="success")
                self._start_member_poll()
                self.after(5000, self._on_host_network_ready)
            else:
                self._append_online_log("[FMCL] " + _("online_launch_failed"))
                self.set_status(_("online_launch_failed"), "error")
                self._reset_lobby_state()
                self._online_create_btn.configure(state=ctk.NORMAL)
                self._online_join_btn.configure(state=ctk.NORMAL)

        def on_error(err):
            self._append_online_log("[FMCL] " + _("online_create_failed", error=err))
            self.set_status(_("online_create_failed", error=err), "error")
            show_notification("🏠", _("notify_room_create_failed"), err[:50], notify_type="error")
            self._online_create_btn.configure(state=ctk.NORMAL)
            self._online_join_btn.configure(state=ctk.NORMAL)

        self._run_online_thread(_create, on_done=on_done, on_error=on_error)

    def _on_host_network_ready(self):
        if not self._is_host or not self._et_manager or not self._et_manager.is_running:
            return
        self._set_online_status(_("online_et_running", pid=self._et_manager.pid), "success")
        self._online_lobby_status_label.configure(text="")
        self._append_online_log("[FMCL] " + _("online_forward_complete", local_port=self._local_mc_port))

    def _on_host_game_stopped(self):
        if not self._is_host:
            return
        self._append_online_log("[FMCL] MC instance stopped, leaving lobby")
        self._online_lobby_status_label.configure(text="MC instance closed", text_color=COLORS["warning"])
        self._on_leave_lobby()

    def _on_join_lobby(self):
        login_err = self._get_login_error()
        if login_err:
            self._append_online_log("[FMCL] " + login_err)
            self.set_status(login_err, "warning")
            return

        if self._et_manager is None or not self._et_manager.is_installed:
            self._append_online_log("[FMCL] " + _("online_et_not_found"))
            self.set_status(_("online_et_not_found"), "warning")
            return

        if self._et_manager.is_running:
            self._append_online_log("[FMCL] " + _("online_et_already_running"))
            self.set_status(_("online_et_already_running"), "warning")
            return

        code = self._online_lobby_code_entry.get().strip()
        if not code:
            self._append_online_log("[FMCL] " + _("online_empty_code"))
            self.set_status(_("online_empty_code"), "warning")
            return

        lobby = _get_online_service(self).parse_lobby_code(code)
        if lobby is None:
            self._append_online_log("[FMCL] " + _("online_invalid_code"))
            self.set_status(_("online_invalid_code"), "warning")
            return

        self._online_create_btn.configure(state=ctk.DISABLED)
        self._online_join_btn.configure(state=ctk.DISABLED)
        self._append_online_log("[FMCL] " + _("online_joining_lobby", code=lobby.full_code))
        self._set_lobby_state(LobbyState.JOINING)

        def _join():
            return lobby

        def on_done(lobby):
            self._lobby_info = lobby
            self._is_host = False

            result = self._et_manager.launch(
                lobby,
                as_host=False,
                on_output=lambda line: self._append_online_log(line),
                on_exited=lambda code: self.after(0, lambda: self._on_easytier_exited(code)),
            )

            if result == 0:
                self._set_lobby_state(LobbyState.CONNECTED)
                self._set_online_status(_("online_et_connecting"), "warning")
                self._online_lobby_code_display_label.configure(text=lobby.full_code, text_color=COLORS["accent"])
                self._online_lobby_status_label.configure(
                    text=_("online_waiting_network"), text_color=COLORS["warning"]
                )
                self._online_leave_btn.configure(state=ctk.NORMAL)
                self._append_online_log("[FMCL] " + _("online_joined_lobby", code=lobby.full_code))
                self._append_online_log("[FMCL] " + _("online_waiting_network"))
                self.set_status(_("online_joined_lobby", code=lobby.full_code), "success")
                self._trigger_ach("online_first_online")
                self._trigger_ach("online_guest")
                show_notification("🚪", _("notify_room_joined"), lobby.full_code, notify_type="success")

                self.after(3000, self._setup_guest_connection)
            else:
                self._append_online_log("[FMCL] " + _("online_launch_failed"))
                self.set_status(_("online_launch_failed"), "error")
                self._reset_lobby_state()
                self._online_create_btn.configure(state=ctk.NORMAL)
                self._online_join_btn.configure(state=ctk.NORMAL)

        def on_error(err):
            self._append_online_log("[FMCL] " + _("online_join_failed", error=err))
            self.set_status(_("online_join_failed", error=err), "error")
            show_notification("🚪", _("notify_room_join_failed"), err[:50], notify_type="error")
            self._online_create_btn.configure(state=ctk.NORMAL)
            self._online_join_btn.configure(state=ctk.NORMAL)

        self._run_online_thread(_join, on_done=on_done, on_error=on_error)

    def _setup_guest_connection(self):
        if not self._et_manager or not self._et_manager.is_running:
            self._append_online_log("[FMCL] " + _("online_forward_failed_et"))
            return

        if self._is_host:
            return

        self._append_online_log("[FMCL] " + _("online_setting_up_forward"))

        def _setup():
            host_info = None
            for attempt in range(2):
                self._append_online_log(
                    f"[FMCL] " + _("online_discovering_host", attempt=attempt + 1)
                )
                host_info = self._et_manager.discover_host(timeout=40.0)
                if host_info is not None:
                    break
            if host_info is None:
                return None
            host_ip, scf_port = host_info
            scf_local_port = self._et_manager.add_port_forward(host_ip, scf_port)
            if scf_local_port is None:
                return None
            self._scf_client = _get_online_service(self).create_scaffolding_client(
                "127.0.0.1", scf_local_port, self._get_display_name(),
                _get_online_service(self).machine_id(), _get_online_service(self).vendor(),
            )
            if not self._scf_client.connect():
                self._scf_client = None
                return None
            self._scf_client.on_player_list_changed = lambda profiles: self.after(
                0, lambda: self._on_members_changed(profiles)
            )
            self._scf_client.on_server_shutdown = lambda: self.after(0, self._on_server_shutdown_detected)
            mc_port = self._scf_client.get_server_port()
            if mc_port is None or mc_port <= 0:
                logger.warning("c:server_port returned invalid port, falling back to 25565")
                mc_port = 25565
            local_port = self._et_manager.add_port_forward(host_ip, mc_port)
            return local_port

        def on_done(local_port):
            if local_port is None:
                self._append_online_log("[FMCL] " + _("online_forward_failed"))
                self._online_lobby_status_label.configure(text=_("online_forward_failed"), text_color=COLORS["error"])
                return

            self._local_mc_port = local_port
            self._online_lobby_status_label.configure(
                text=_("online_forward_ready", port=local_port), text_color=COLORS["success"]
            )
            self._append_online_log("[FMCL] " + _("online_forward_ready", port=local_port))

            tcp_forward_port = _get_online_service(self).get_random_port()
            self._tcp_forwarder = _get_online_service(self).create_tcp_forwarder(
                tcp_forward_port, LOOPBACK, local_port
            )
            self._tcp_forwarder.start()

            desc = f"§eFMCL 大厅 - {self._get_display_name()}"

            self._broadcast_sim = _get_online_service(self).create_broadcast_simulator()
            self._broadcast_sim.start(desc, tcp_forward_port)

            self._set_online_status(_("online_et_running", pid=self._et_manager.pid), "success")
            self._append_online_log("[FMCL] " + _("online_forward_complete", local_port=tcp_forward_port))
            self.set_status(_("online_forward_complete", local_port=tcp_forward_port), "success")

            self._start_member_poll()

        def on_error(err):
            self._append_online_log("[FMCL] " + _("online_forward_failed_detail", error=err))
            self._online_lobby_status_label.configure(
                text=_("online_forward_failed_detail", error=err), text_color=COLORS["error"]
            )

        self._run_online_thread(_setup, on_done=on_done, on_error=on_error)

    def _on_leave_lobby(self):
        self._stop_member_poll()
        self._append_online_log("[FMCL] " + _("online_leaving_lobby"))

        if self._broadcast_sim:
            self._broadcast_sim.stop()
            self._broadcast_sim = None

        if self._tcp_forwarder:
            self._tcp_forwarder.stop()
            self._tcp_forwarder = None

        if self._et_manager:
            self._et_manager.stop_async()

        self._reset_lobby_state()
        self._set_online_status(_("online_et_stopped"), "text_secondary")
        self._append_online_log("[FMCL] " + _("online_left_lobby"))
        self.set_status(_("online_left_lobby"), "info")

    def _reset_lobby_state(self):
        self._stop_member_poll()
        self._set_lobby_state(LobbyState.LEAVING)
        if self._scf_client:
            self._scf_client.on_player_list_changed = None
            self._scf_client.on_server_shutdown = None
            self._scf_client.on_heartbeat = None
            self._scf_client.disconnect()
            self._scf_client = None
        if self._et_manager and self._et_manager._scf_server:
            self._et_manager._scf_server.on_profiles_changed = None
        if hasattr(self, "_game_watcher") and self._game_watcher:
            self._game_watcher.stop()
            self._game_watcher = None
        self._lobby_info = None
        self._is_host = False
        self._local_mc_port = 0
        self._public_address = None
        self._online_lobby_code_display_label.configure(text=_("online_no_lobby"), text_color=COLORS["accent"])
        self._online_lobby_status_label.configure(text="")
        self._online_leave_btn.configure(state=ctk.DISABLED)
        self._online_create_btn.configure(state=ctk.NORMAL)
        self._online_join_btn.configure(state=ctk.NORMAL)
        self._set_lobby_state(LobbyState.IDLE)

    def _on_easytier_exited(self, exit_code: int):
        self._append_online_log(f"[FMCL] EasyTier exited with code {exit_code}")
        self._set_online_status(_("online_et_stopped"), "text_secondary")
        if self._broadcast_sim:
            self._broadcast_sim.stop()
            self._broadcast_sim = None
        if self._tcp_forwarder:
            self._tcp_forwarder.stop()
            self._tcp_forwarder = None
        self._reset_lobby_state()
        self.set_status(_("online_et_exited", exit_code=exit_code), "warning")

    def _on_copy_code(self):
        if not self._lobby_info:
            return
        try:
            import pyperclip

            pyperclip.copy(self._lobby_info.full_code)
            self.set_status(_("online_copy_success", code=self._lobby_info.full_code), "success")
        except Exception as e:
            self.set_status(_("copy_failed", error=str(e)), "error")
