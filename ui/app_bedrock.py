"""ModernApp 基岩版标签页 Mixin - 下载、启动、删除基岩版版本

业务逻辑已搬到 ``services/bedrock_service.py``（阶段 1 任务 1.11）：可用/已安装
版本列表的取值、过滤与分页、安装前置检查（MCAPPX 条款 / GDK 的 .NET 10 SDK）、
安装 / 启动 / 删除的编排、GDK 闭源认证组件检查、微软账户设备码登录流程、
打开版本根目录。本文件只剩纯界面部分（控件构建、对话框、``after`` 调度、
``_task_queue`` 投递）；下面每个方法都退化成对服务的薄委托，
**方法名与签名保持不变**，界面可见行为不变。
"""

from typing import Any, Dict, List, Optional

import customtkinter as ctk
from logzero import logger

from app.context import current_context
from services.bedrock_service import BedrockService
from services.bedrock_service import LAUNCH_LOCK as _BEDROCK_LAUNCH_LOCK
from services.bedrock_service import MSA_LOGIN_LOCK as _MSA_LOGIN_LOCK
from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _

_TYPE_LABEL_KEY = {"release": "bedrock_type_release", "preview": "bedrock_type_preview", "beta": "bedrock_type_beta"}

# 两个进程级互斥锁已搬进 `services/bedrock_service.py`（任务 1.11）。
# 上面保留**同名别名**：模块级名字面不变、解析到的是同一个锁对象，
# 获取与释放则统一由服务负责（原文的 `try/finally` 语义随之搬走）。
# `import threading` 因为本文件再无其他用途而删除（见报告的"必要改动"）。


def _get_bedrock_service(owner: Any = None) -> BedrockService:
    """惰性取得基岩版服务实例。

    与 ``ui/app_server.py`` 的 ``_get_server_service(owner)`` /
    ``ui/windows/resource_manager.py`` 的 ``_get_resource_service(owner)``
    同一取舍：写成**模块级函数**（而不是 Mixin 上的方法）是因为调用方
    （测试 / 探针）会以"未绑定方法 + 假对象"的方式直接调这些方法，那些假对象
    上并没有这个方法；同时给 Mixin 加方法会改变方法面（本轮要求逐字不变）。

    取法：优先用 ``AppContext`` 里注册的那个（阶段 2 接上之后），取不到就在
    ``owner`` 上造一个并缓存 —— ``BedrockService`` 不需要 ``AppContext``，
    构造期也只保存参数。
    """
    ctx = getattr(owner, "context", None) or current_context()
    if ctx is not None:
        getter = getattr(ctx, "try_get", None)
        if callable(getter):
            try:
                service = getter(BedrockService.name)
            except Exception:
                service = None
            if service is not None:
                return service
    service = getattr(owner, "_bedrock_service_fallback", None)
    if service is None:
        service = BedrockService()
        try:
            owner._bedrock_service_fallback = service
        except Exception:
            # owner 可能是 __slots__ 对象或 None：不缓存，本次调用照常可用
            pass
    return service


class BedrockMixin:
    """基岩版标签页 Mixin（仅 Windows 平台挂载）"""

    def _build_bedrock_tab_content(self):
        """构建基岩版标签页内容"""
        content = ctk.CTkFrame(self.bedrock_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        self.bedrock_installed: List[Dict[str, Any]] = []
        self.bedrock_available: List[Dict[str, Any]] = []
        self.bedrock_filtered: List[Dict[str, Any]] = []
        self.bedrock_selected: Optional[Dict[str, Any]] = None

        self.bedrock_search_var = ctk.StringVar()
        self.bedrock_search_var.trace_add("write", lambda *_: self._on_bedrock_filter_change())
        self.bedrock_filter_var = ctk.StringVar(value=_("bedrock_filter_all"))

        self._build_bedrock_installed_panel(content)
        self._build_bedrock_download_panel(content)
        self._refresh_bedrock_versions()

    # ─── 左侧：已安装版本 ───────────────────────────────────

    def _build_bedrock_installed_panel(self, parent):
        self.bedrock_installed_panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12)
        self.bedrock_installed_panel.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 8))

        self.bedrock_installed_title = ctk.CTkLabel(
            self.bedrock_installed_panel,
            text=_("bedrock_installed"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self.bedrock_installed_title.pack(padx=15, pady=(15, 8), anchor=ctk.W)

        self.bedrock_installed_count = ctk.CTkLabel(
            self.bedrock_installed_panel,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self.bedrock_installed_count.pack(padx=15, anchor=ctk.W)

        sep = ctk.CTkFrame(self.bedrock_installed_panel, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, padx=15, pady=(8, 5))

        self.bedrock_installed_list = ctk.CTkScrollableFrame(
            self.bedrock_installed_panel,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        self.bedrock_installed_list.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 10))

        self._theme_refs.append((self.bedrock_installed_panel, {"fg_color": "card_bg"}))
        self._theme_refs.append((self.bedrock_installed_title, {"text_color": "text_primary"}))
        self._theme_refs.append((self.bedrock_installed_count, {"text_color": "text_secondary"}))
        self._theme_refs.append((sep, {"fg_color": "card_border"}))
        self._theme_refs.append((self.bedrock_installed_list, {"scrollbar_button_color": "bg_light"}))

    def _render_bedrock_installed(self):
        """渲染已安装版本列表"""
        for child in self.bedrock_installed_list.winfo_children():
            child.destroy()
        self.bedrock_installed_cards: List[tuple] = []
        self.bedrock_installed_count.configure(text=_("bedrock_count").format(count=len(self.bedrock_installed)))
        if not self.bedrock_installed:
            empty = ctk.CTkLabel(
                self.bedrock_installed_list,
                text=_("bedrock_no_installed"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
            )
            empty.pack(pady=30)
            return
        for info in self.bedrock_installed:
            self._render_bedrock_card(info)

    def _render_bedrock_card(self, info: Dict[str, Any]):
        card = ctk.CTkFrame(self.bedrock_installed_list, fg_color=COLORS["bg_light"], corner_radius=8)
        card.pack(fill=ctk.X, padx=2, pady=4)
        card.pack_propagate(False)
        card.configure(height=100)

        name_label = ctk.CTkLabel(
            card,
            text=info.get("name", ""),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        name_label.pack(anchor=ctk.W, padx=12, pady=(8, 0))

        type_text = _(_TYPE_LABEL_KEY.get(info.get("game_type", "release"), "bedrock_type_release"))
        version_text = info.get("version", "")
        detail = ctk.CTkLabel(
            card,
            text=f"{version_text}  |  {info.get('build_type', 'UWP')}  |  {type_text}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        detail.pack(anchor=ctk.W, padx=12)

        btn_frame = ctk.CTkFrame(card, fg_color="transparent")
        btn_frame.pack(fill=ctk.X, padx=12, pady=(4, 8))

        name = info.get("name", "")
        launch_btn = ctk.CTkButton(
            btn_frame,
            text=_("bedrock_launch"),
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda n=name: self._on_bedrock_launch(n),
        )
        launch_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 6))

        remove_btn = ctk.CTkButton(
            btn_frame,
            text=_("bedrock_delete"),
            height=36,
            width=80,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["error"],
            text_color=COLORS["text_secondary"],
            command=lambda n=name: self._on_bedrock_remove(n),
        )
        remove_btn.pack(side=ctk.RIGHT)

        self.bedrock_installed_cards.append((card, name_label, detail, launch_btn, remove_btn))

    # ─── 右侧：下载面板 ─────────────────────────────────────

    def _build_bedrock_download_panel(self, parent):
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, width=420)
        panel.pack(side=ctk.RIGHT, fill=ctk.Y, padx=(8, 0))
        panel.pack_propagate(False)

        title = ctk.CTkLabel(
            panel,
            text=_("bedrock_available"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title.pack(padx=15, pady=(15, 8), anchor=ctk.W)
        self._theme_refs.append((title, {"text_color": "text_primary"}))

        sep = ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, padx=15, pady=(0, 10))
        self._theme_refs.append((sep, {"fg_color": "card_border"}))

        filter_row = ctk.CTkFrame(panel, fg_color="transparent")
        filter_row.pack(fill=ctk.X, padx=15, pady=(0, 8))

        search_entry = ctk.CTkEntry(
            filter_row,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            textvariable=self.bedrock_search_var,
            placeholder_text=_("bedrock_search_placeholder"),
        )
        search_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._theme_refs.append((search_entry, {"fg_color": "bg_medium", "border_color": "card_border"}))

        filter_menu = ctk.CTkOptionMenu(
            filter_row,
            variable=self.bedrock_filter_var,
            values=[_("bedrock_filter_all"), "UWP", "GDK"],
            height=32,
            width=90,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
            command=lambda _v: self._on_bedrock_filter_change(),
        )
        filter_menu.pack(side=ctk.RIGHT)
        self._theme_refs.append((filter_menu, {"fg_color": "bg_medium", "button_color": "bg_light"}))

        self.bedrock_available_list = ctk.CTkScrollableFrame(
            panel,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        self.bedrock_available_list.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 8))
        self._theme_refs.append((self.bedrock_available_list, {"scrollbar_button_color": "bg_light"}))

        # 分页栏（每页 20 条，对齐游戏标签页）
        self._bedrock_page_size = 20
        self._bedrock_page = 1
        pager = ctk.CTkFrame(panel, fg_color="transparent", height=30)
        pager.pack(fill=ctk.X, padx=15, pady=(0, 6))
        pager.pack_propagate(False)

        pager_btn_cfg = {
            "height": 26,
            "width": 70,
            "font": ctk.CTkFont(family=FONT_FAMILY, size=12),
            "fg_color": COLORS["bg_medium"],
            "hover_color": COLORS["bg_light"],
            "text_color": COLORS["text_primary"],
        }
        self.bedrock_prev_page_btn = ctk.CTkButton(
            pager, text=_("bedrock_page_prev"), command=self._on_bedrock_prev_page, **pager_btn_cfg
        )
        self.bedrock_prev_page_btn.pack(side=ctk.LEFT)
        self.bedrock_next_page_btn = ctk.CTkButton(
            pager, text=_("bedrock_page_next"), command=self._on_bedrock_next_page, **pager_btn_cfg
        )
        self.bedrock_next_page_btn.pack(side=ctk.RIGHT)
        self.bedrock_page_info_label = ctk.CTkLabel(
            pager,
            text="1/1",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self.bedrock_page_info_label.pack(side=ctk.RIGHT, padx=8)

        self._theme_refs.append((self.bedrock_page_info_label, {"text_color": "text_secondary"}))
        self._theme_refs.append(
            (self.bedrock_prev_page_btn, {"fg_color": "bg_medium", "hover_color": "bg_light", "text_color": "text_primary"})
        )
        self._theme_refs.append(
            (self.bedrock_next_page_btn, {"fg_color": "bg_medium", "hover_color": "bg_light", "text_color": "text_primary"})
        )

        action_row = ctk.CTkFrame(panel, fg_color="transparent")
        action_row.pack(fill=ctk.X, padx=15, pady=(0, 12))

        self.bedrock_install_btn = ctk.CTkButton(
            action_row,
            text=_("bedrock_install"),
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            state=ctk.DISABLED,
            command=self._on_bedrock_install,
        )
        self.bedrock_install_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._theme_refs.append((self.bedrock_install_btn, {"fg_color": "accent", "hover_color": "accent_hover"}))

        refresh_btn = ctk.CTkButton(
            action_row,
            text=_("bedrock_refresh"),
            height=38,
            width=80,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_bedrock_refresh,
        )
        refresh_btn.pack(side=ctk.RIGHT)
        self._theme_refs.append((refresh_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))

    # ─── 数据加载 ───────────────────────────────────────────

    def _refresh_bedrock_versions(self):
        self._run_in_thread(self._load_bedrock_installed)

    def _load_bedrock_installed(self):
        try:
            installed = _get_bedrock_service(self).load_installed(self.callbacks)
            self._task_queue.put(("bedrock_installed_loaded", installed))
        except Exception as e:
            logger.error(f"加载已安装基岩版失败: {e}")
            self._task_queue.put(("bedrock_load_error", str(e)))

    def _load_bedrock_available(self):
        try:
            available = _get_bedrock_service(self).load_available(self.callbacks)
            self._task_queue.put(("bedrock_available_loaded", available))
        except Exception as e:
            logger.error(f"加载可下载基岩版失败: {e}")
            self._task_queue.put(("bedrock_load_error", str(e)))

    def _on_bedrock_filter_change(self):
        # 过滤规则（关键字去首尾空白并转小写 + UWP/GDK 档位）在服务里；
        # `_("bedrock_filter_all")` 这个字面量键仍留在界面文件里，
        # `scripts/check_i18n.py` 的"键存在 / 占位符 / 调用点缺参"三项检查
        # 覆盖范围不缩水（服务层不允许出现 i18n 文案）。
        self.bedrock_filtered = _get_bedrock_service(self).filter_versions(
            self.bedrock_available,
            self.bedrock_search_var.get(),
            self.bedrock_filter_var.get(),
            _("bedrock_filter_all"),
        )
        self._bedrock_page = 1
        self._render_bedrock_available()

    def _get_bedrock_total_pages(self) -> int:
        return _get_bedrock_service(self).total_pages(len(self.bedrock_filtered), self._bedrock_page_size)

    def _render_bedrock_available(self):
        for child in self.bedrock_available_list.winfo_children():
            child.destroy()
        self.bedrock_available_cards: List[ctk.CTkFrame] = []

        page_slice = _get_bedrock_service(self).paginate(
            self.bedrock_filtered, self._bedrock_page, self._bedrock_page_size
        )
        total_pages = page_slice.total_pages
        # 原文"先夹紧页码、再配置标签"的顺序不变：列表变短后停在越界页会自愈
        self._bedrock_page = page_slice.page
        self.bedrock_page_info_label.configure(text=f"{self._bedrock_page}/{total_pages}")
        self.bedrock_prev_page_btn.configure(
            state=ctk.NORMAL if self._bedrock_page > 1 else ctk.DISABLED
        )
        self.bedrock_next_page_btn.configure(
            state=ctk.NORMAL if self._bedrock_page < total_pages else ctk.DISABLED
        )

        display_versions = page_slice.items

        if not self.bedrock_available:
            empty = ctk.CTkLabel(
                self.bedrock_available_list,
                text=_("bedrock_no_available"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
            )
            empty.pack(pady=30)
            return
        if not display_versions:
            empty = ctk.CTkLabel(
                self.bedrock_available_list,
                text=_("bedrock_no_match"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
            )
            empty.pack(pady=30)
            return
        for version in display_versions:
            self._render_bedrock_available_row(version)

    def _on_bedrock_prev_page(self):
        if self._bedrock_page > 1:
            self._bedrock_page -= 1
            self._render_bedrock_available()

    def _on_bedrock_next_page(self):
        if self._bedrock_page < self._get_bedrock_total_pages():
            self._bedrock_page += 1
            self._render_bedrock_available()

    def _render_bedrock_available_row(self, version: Dict[str, Any]):
        selected = self.bedrock_selected and self.bedrock_selected.get("version") == version.get("version")
        card = ctk.CTkFrame(
            self.bedrock_available_list,
            fg_color=COLORS["accent"] if selected else COLORS["bg_light"],
            corner_radius=8,
            cursor="hand2",
        )
        card.pack(fill=ctk.X, padx=2, pady=3)
        card.bind("<Button-1>", lambda _e, v=version: self._select_bedrock_version(v))

        type_text = _(_TYPE_LABEL_KEY.get(version.get("type", "release"), "bedrock_type_release"))
        text_color = COLORS["text_primary"] if not selected else "#ffffff"
        sub_color = COLORS["text_secondary"] if not selected else "#e0e0e0"

        name_label = ctk.CTkLabel(
            card,
            text=version.get("version", ""),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=text_color,
        )
        name_label.pack(anchor=ctk.W, padx=12, pady=(8, 0))
        name_label.bind("<Button-1>", lambda _e, v=version: self._select_bedrock_version(v))

        detail = ctk.CTkLabel(
            card,
            text=f"{version.get('build_type', 'UWP')}  |  {type_text}  |  {version.get('date', '')}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=sub_color,
        )
        detail.pack(anchor=ctk.W, padx=12, pady=(2, 8))
        detail.bind("<Button-1>", lambda _e, v=version: self._select_bedrock_version(v))

        self.bedrock_available_cards.append(card)

    def _refresh_bedrock_colors(self):
        """主题切换时刷新动态创建的基岩版卡片颜色"""
        for card, name_label, detail, launch_btn, remove_btn in getattr(self, "bedrock_installed_cards", []):
            try:
                card.configure(fg_color=COLORS["bg_light"])
                name_label.configure(text_color=COLORS["text_primary"])
                detail.configure(text_color=COLORS["text_secondary"])
                launch_btn.configure(fg_color=COLORS["accent"], hover_color=COLORS["accent_hover"])
                remove_btn.configure(fg_color=COLORS["bg_medium"], text_color=COLORS["text_secondary"])
            except Exception:
                pass
        for card in getattr(self, "bedrock_available_cards", []):
            try:
                card.configure(fg_color=COLORS["bg_light"])
            except Exception:
                pass

    def _select_bedrock_version(self, version: Dict[str, Any]):
        if self.bedrock_selected and self.bedrock_selected.get("version") == version.get("version"):
            return
        self.bedrock_selected = version
        self.bedrock_install_btn.configure(state=ctk.NORMAL)
        self._render_bedrock_available()

    # ─── 操作 ───────────────────────────────────────────────

    def _on_bedrock_install(self):
        if not self.bedrock_selected:
            return
        service = _get_bedrock_service(self)
        # 首次下载基岩版需确认 MCAPPX 版本库条款（同意后持久化，不再重复弹窗）
        # "是否已同意 / 记下同意"在服务里；弹窗与文案留界面。
        # `service` 刻意取在 try 之外：取服务本身失败不该被下面的
        # "异常即放行下载"吞掉（否则 `service` 会变成未绑定名）。
        try:
            if not service.terms_accepted():
                import tkinter.messagebox

                if not tkinter.messagebox.askyesno(
                    _("bedrock_terms_title"),
                    _("bedrock_terms_msg"),
                    parent=self,
                    icon=tkinter.messagebox.WARNING,
                ):
                    return
                service.mark_terms_accepted()
        except Exception as e:
            logger.warning(f"基岩版条款确认异常（放行下载）: {e}")
        # GDK 版解压依赖 .NET 10 SDK（运行时构建解压组件）：缺失时引导下载
        if service.requires_dotnet10(self.bedrock_selected) and not self._ensure_dotnet10_for_gdk():
            return
        version = self.bedrock_selected.get("version", "")
        self.bedrock_install_btn.configure(state=ctk.DISABLED)
        self.set_status(_("bedrock_installing").format(version=version), "loading")
        self._run_in_thread(self._install_bedrock_worker, version)

    def _ensure_dotnet10_for_gdk(self) -> bool:
        """GDK 版下载前检测 .NET 10 SDK，缺失时弹窗并打开官方下载直链

        返回 False 表示用户已决定去安装（本次中止），True 表示继续下载。
        """
        try:
            # 检测与"取直链失败退回下载页"都在服务里；这里只负责弹窗与开浏览器。
            # `tkinter.messagebox` / `webbrowser` 的 import 位置照旧放在检测之后，
            # 已装 SDK 的常见路径不会为它们付出导入代价。
            check = _get_bedrock_service(self).check_dotnet10()
            if check.has_sdk10:
                return True
            import tkinter.messagebox
            import webbrowser

            if tkinter.messagebox.askyesno(
                _("bedrock_dotnet_title"),
                _("bedrock_dotnet_msg"),
                parent=self,
                icon=tkinter.messagebox.WARNING,
            ):
                webbrowser.open(check.download_url)
            return False
        except Exception as e:
            logger.warning(f".NET 10 检测异常（放行下载）: {e}")
            return True

    def _install_bedrock_worker(self, version: str):
        try:
            # "成功且 info 是 dict 就取 name，否则 str(info)" 这段归一化在服务里
            outcome = _get_bedrock_service(self).install_version(self.callbacks, version)
            self._task_queue.put(("bedrock_install_done", (version, outcome.success, outcome.display)))
        except Exception as e:
            logger.error(f"基岩版安装异常: {e}")
            self._task_queue.put(("bedrock_install_done", (version, False, str(e))))

    def _on_bedrock_launch(self, name: str):
        # GDK 版需要闭源认证组件：缺失时先征得用户同意（主线程弹窗）
        try:
            # "该版本是不是 GDK、组件是否已就绪"在服务里；问不问用户由界面决定。
            # 短路顺序照旧：只有真缺组件时才会弹出同意框。
            needs_consent = _get_bedrock_service(self).component_setup_needed(self.callbacks, name)
            if needs_consent and not self._confirm_bedrock_components():
                return
        except Exception as e:
            logger.warning(f"基岩版认证组件检查异常（放行启动）: {e}")
        self.set_status(_("bedrock_launching").format(name=name), "loading")
        self._run_in_thread(self._launch_bedrock_worker, name)

    def _confirm_bedrock_components(self) -> bool:
        """GDK 闭源认证组件下载同意（同意后持久化，不再重复弹窗）"""
        try:
            service = _get_bedrock_service(self)
            if service.component_download_consented():
                return True
            import tkinter.messagebox

            if not tkinter.messagebox.askyesno(
                _("bedrock_component_title"),
                _("bedrock_component_msg"),
                parent=self,
                icon=tkinter.messagebox.WARNING,
            ):
                self.set_status(_("bedrock_component_declined"), "error")
                return False
            service.mark_components_consented()
            return True
        except Exception as e:
            logger.warning(f"基岩版组件同意确认异常（放行）: {e}")
            return True

    def _launch_bedrock_worker(self, name: str):
        # 防重入 / GDK 组件按需下载 / Xbox 身份检查 / MSA 设备码登录 / 调
        # `bedrock_launch_version` 全在服务里；三个 sink 把界面动作接回 _task_queue。
        # `download_notice` 只提供**文案**（服务决定播报时机），文案留界面。
        try:
            outcome = _get_bedrock_service(self).run_launch(
                self.callbacks,
                name,
                status_cb=lambda text: self._task_queue.put(("bedrock_component_status", text)),
                download_notice=_("bedrock_component_downloading"),
                login_status_cb=lambda text: self._task_queue.put(("bedrock_msa_status", text)),
                login_code_cb=lambda code, uri: self._task_queue.put(("bedrock_msa_code", (code, uri))),
                login_done_cb=lambda token, error: self._task_queue.put(
                    ("bedrock_msa_done", (token, error))
                ),
            )
        except Exception as e:
            logger.error(f"基岩版启动异常: {e}")
            self._task_queue.put(("bedrock_launch_done", (name, False, str(e))))
            return
        if outcome is None:
            # 防重入：已有启动流程在跑。服务已按原文记了那条 warning，
            # 原文此时**不投递任何任务**，所以这里也不刷新状态栏。
            return
        self._task_queue.put(("bedrock_launch_done", (name, outcome.success, outcome.message)))

    def _msa_login_flow(self) -> str:
        """获取微软账户 access_token（优先用缓存的凭证自动刷新，无缓存才走设备码登录）

        多线程并发点击时互斥：只允许一个线程发起设备码登录弹窗，
        其余线程等待其完成并复用缓存的凭证，避免弹出多个登录窗口。

        任务 1.11：流程本体（互斥锁、设备码轮询、凭证落盘）已搬进
        `services/bedrock_service.py::BedrockService.msa_login_flow`，
        这里只把三处界面动作接成 sink。**注意**：`_launch_bedrock_worker`
        现在直接调服务的方法，所以本方法不再被内部调用 —— 方法面必须逐字
        保留，故仍保留（行为与原来完全一致，见报告"必要改动"）。
        """
        return _get_bedrock_service(self).msa_login_flow(
            status_cb=lambda text: self._task_queue.put(("bedrock_msa_status", text)),
            code_cb=lambda code, uri: self._task_queue.put(("bedrock_msa_code", (code, uri))),
            done_cb=lambda token, error: self._task_queue.put(
                ("bedrock_msa_done", (token, error))
            ),
        )

    def _on_bedrock_remove(self, name: str):
        import tkinter.messagebox

        if not tkinter.messagebox.askyesno(
            _("confirm_delete"), _("bedrock_confirm_delete").format(name=name), parent=self
        ):
            return
        self._run_in_thread(self._remove_bedrock_worker, name)

    def _remove_bedrock_worker(self, name: str):
        try:
            outcome = _get_bedrock_service(self).remove_version(self.callbacks, name)
            self._task_queue.put(("bedrock_remove_done", (name, outcome.success, outcome.message)))
        except Exception as e:
            logger.error(f"基岩版删除异常: {e}")
            self._task_queue.put(("bedrock_remove_done", (name, False, str(e))))

    def _on_bedrock_refresh(self):
        self.set_status("", "info")
        self._run_in_thread(self._load_bedrock_available)
        self._run_in_thread(self._load_bedrock_installed)

    def _open_bedrock_folder(self):
        # 取根目录这一行原文就在 try 之外（回调抛异常会直接冒泡），服务的
        # `root_dir` 保持同样的边界；`os.startfile` 走可注入的 open_in_explorer。
        root = _get_bedrock_service(self).root_dir(self.callbacks)
        if not root:
            return
        try:
            _get_bedrock_service(self).open_in_explorer(root)
        except Exception as e:
            logger.warning(f"打开基岩版目录失败: {e}")

    # ─── 任务分发 ───────────────────────────────────────────

    def _handle_bedrock_task(self, task_type: str, data: Any) -> bool:
        """处理基岩版相关队列任务，返回是否已处理"""
        if not hasattr(self, "bedrock_tab") or self.bedrock_tab is None:
            return False
        if task_type == "bedrock_installed_loaded":
            self.bedrock_installed = data or []
            self._render_bedrock_installed()
            self._run_in_thread(self._load_bedrock_available)
            return True

        if task_type == "bedrock_available_loaded":
            self.bedrock_available = data or []
            self._on_bedrock_filter_change()
            release_count = _get_bedrock_service(self).count_release(self.bedrock_available)
            self.set_status(
                _("bedrock_available_status").format(total=len(self.bedrock_available), release=release_count),
                "success",
            )
            return True

        if task_type == "bedrock_load_error":
            self.set_status(_("bedrock_load_error").format(error=data), "error")
            return True

        if task_type == "bedrock_install_done":
            version, success, info = data
            self.bedrock_install_btn.configure(state=ctk.NORMAL)
            if success:
                self.set_status(_("bedrock_install_success").format(version=info), "success")
                self._run_in_thread(self._load_bedrock_installed)
            else:
                self.set_status(_("bedrock_install_failed").format(version=version, error=info), "error")
            return True

        if task_type == "bedrock_msa_code":
            code, uri = data
            import tkinter.messagebox
            import webbrowser

            webbrowser.open(uri)
            tkinter.messagebox.showinfo(
                _("bedrock_msa_title"),
                _("bedrock_msa_code_hint").format(code=code, uri=uri),
                parent=self,
            )
            return True

        if task_type == "bedrock_component_status":
            self.set_status(str(data), "loading")
            return True

        if task_type == "bedrock_msa_status":
            self.set_status(str(data), "loading")
            return True

        if task_type == "bedrock_msa_done":
            token, error = data
            if token:
                self.set_status(_("bedrock_msa_success"), "success")
            else:
                self.set_status(_("bedrock_msa_failed").format(error=error), "error")
            return True

        if task_type == "bedrock_launch_done":
            name, success, msg = data
            if success:
                self.set_status(_("bedrock_launch_success").format(name=name), "success")
            else:
                self.set_status(_("bedrock_launch_failed").format(name=name, error=msg), "error")
            return True

        if task_type == "bedrock_remove_done":
            name, success, msg = data
            if success:
                self.set_status(_("bedrock_remove_success").format(name=name), "success")
            else:
                self.set_status(_("bedrock_remove_failed").format(error=msg), "error")
            # 无论成败都刷新已安装列表（失败时同步真实状态，避免残留显示）
            self._run_in_thread(self._load_bedrock_installed)
            return True

        return False
