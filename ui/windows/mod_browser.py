"""Modrinth 资源浏览窗口 - 浏览并安装模组、资源包、光影

业务逻辑已搬到 ``services/mod_browser_service.py``（阶段 1 任务 1.8-B）：
双源（Modrinth + CurseForge）搜索编排与结果归一化、AI 关键词搜索编排、
安装目标目录解析与已装实例匹配、模组/资源包/光影的下载安装调用。
本文件只剩纯界面部分（控件构建、``after`` 调度、``filedialog``、i18n 文案、
``CTkImage`` 之外的一切渲染、``_trigger_ach``、线程启动）；下面每个受影响的方法
都退化成对服务的**薄委托**，**方法名与签名保持不变**，界面可见行为不变。

既有的三处修复在本轮**原样保留**（改造过程中逐个复核过）：

- **D-91**：版本/加载器下拉框的纯值镜像 ``_mirror_version`` / ``_mirror_loader``
  仍在主线程写入，两个 getter 只读镜像 —— 全文件**没有** ``CTkOptionMenu.get()``；
- **D-103**：分页与"显示区间"一律走 ``_browsable_hits``（本地缓存条数），
  该方法的判定体搬到了 ``services/browse_common.browsable_count``；
- **D-93**：``_begin_request`` / ``_is_stale`` / ``_do_tab_search(tab_key, seq)`` /
  ``_do_ai_search(tab_key, query, token, seq)`` / ``_publish_*`` 六件套逐个保留，
  世代判定与原子提交的**代码形状一字未改**（``tests/test_mod_browser_staleness.py``
  的 19 条守卫继续有效）。
"""

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk
from logzero import logger

from app.context import current_context
from services.browse_common import (
    MOD_LOADER_COMPAT_MAP,
    browsable_count,
    format_downloads,
    page_slice,
    total_pages as browse_total_pages,
)
from services.mod_browser_service import (
    CLIENT_TABS,
    TAB_MODS,
    TAB_RESOURCE_PACKS,
    TAB_SHADERS,
    ModBrowserService,
    compat_loader,
)
from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _


def _get_mod_browser_service(owner: Any = None) -> ModBrowserService:
    """惰性取得服务实例（实现见 ``services/mod_browser_service.py``）。

    查找顺序："``owner.context.try_get`` → ``owner`` 上自造并缓存"。
    服务不需要 ``AppContext``，构造期也只保存参数，因此界面在
    ``__init__`` 之前（例如后台线程里）调用也安全。
    """
    ctx = getattr(owner, "context", None) or current_context()
    if ctx is not None:
        getter = getattr(ctx, "try_get", None)
        if callable(getter):
            try:
                service = getter(ModBrowserService.name)
            except Exception:
                service = None
            if service is not None:
                return service
    service = getattr(owner, "_mod_browser_service_fallback", None)
    if service is None:
        service = ModBrowserService()
        try:
            owner._mod_browser_service_fallback = service
        except Exception:
            # owner 可能是 __slots__ 对象或 None：不缓存，本次调用照常可用
            pass
    return service


def _trigger_ach(achievement_id: str, value: int = 1, trigger_type: str = "increment"):
    try:
        from achievement_engine import get_achievement_engine

        engine = get_achievement_engine()
        if engine:
            engine.update_progress(achievement_id, value=value, trigger_type=trigger_type)
    except Exception:
        pass


class ModBrowserWindow(ctk.CTkToplevel):
    """Modrinth 资源浏览窗口 - 支持模组、资源包、光影三个标签页"""

    PAGE_SIZE = 10

    # 三个标签页键与服务层**同一份数据**：服务要按 tab_key 分派后端入口，
    # 界面要拿它们当状态字典的键，两份各自硬编码迟早漂移。
    TAB_MODS: str = TAB_MODS
    TAB_RESOURCE_PACKS: str = TAB_RESOURCE_PACKS
    TAB_SHADERS: str = TAB_SHADERS

    # 特殊模组加载器兼容映射：将无法被 API 识别的加载器映射为兼容等效类型
    # （实现已搬到服务层；类属性保留同名的**同一份字典**，公开面不变）
    MOD_LOADER_COMPAT_MAP: Dict[str, str] = MOD_LOADER_COMPAT_MAP

    @property
    def _search_loader(self) -> Optional[str]:
        """获取用于 API 搜索/安装的加载器类型

        对特殊加载器进行兼容映射，因为 CurseForge 和 Modrinth API
        不识别 legacyfabric、cleanroom 等加载器类型。
        """
        return compat_loader(self._mod_loader)

    def __init__(self, parent, version_id: Optional[str] = None, callbacks: Optional[Dict[str, Callable]] = None):
        super().__init__(parent)
        self.version_id = version_id or ""
        self.callbacks = callbacks or {}

        from modrinth import parse_game_version_from_version, parse_mod_loader_from_version

        self._mod_loader = parse_mod_loader_from_version(self.version_id) if self.version_id else None
        self._game_version = parse_game_version_from_version(self.version_id) if self.version_id else None

        self.title(
            _("mod_browser_title", version=self.version_id) if self.version_id else _("mod_browser_title_all")
        )
        self.geometry("800x680")
        self.minsize(720, 580)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)

        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 800, 680
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self._tab_states: Dict[str, Dict] = {}

        # 筛选下拉选项：加载器（显示文本 → API 值）
        loader_options = [
            (_("mod_browser_filter_all_loaders"), None),
            (_("mod_loader_forge"), "forge"),
            (_("mod_loader_fabric"), "fabric"),
            (_("mod_loader_neoforge"), "neoforge"),
            (_("mod_loader_quilt"), "quilt"),
        ]
        # 默认筛选值取自窗口来源版本（如已安装版本），API 兼容映射后与选项比对
        default_loader = self._search_loader
        if default_loader not in [opt[1] for opt in loader_options]:
            default_loader = None

        for tab_key in (self.TAB_MODS, self.TAB_RESOURCE_PACKS, self.TAB_SHADERS):
            self._tab_states[tab_key] = {
                "current_offset": 0,
                "total_hits": 0,
                "current_query": "",
                "search_entry": None,
                "list_frame": None,
                "loading_label": None,
                "page_label": None,
                "prev_btn": None,
                "next_btn": None,
                "result_count_label": None,
                "status_label": None,
                "ai_search_btn": None,
                "_ai_cached_hits": None,
                "_cached_hits": None,  # 常规搜索缓存，用于本地分页
                # 筛选状态
                "version_var": None,
                "version_menu": None,
                "version_options": [_("mod_browser_all_versions")],
                "default_version": self._game_version,
                "loader_var": None,
                "loader_menu": None,
                "loader_options": loader_options,
                "default_loader": default_loader,
            }

        self._build_ui()

        self._switch_to_tab(self.TAB_MODS)

        # 后台加载游戏版本列表，填充筛选下拉菜单
        self._run_in_thread(self._load_version_options)

    def _build_ui(self):
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        header = ctk.CTkFrame(main_frame, fg_color="transparent")
        header.pack(fill=ctk.X, pady=(0, 10))

        header_text = (
            _("mod_browser_header", version=self.version_id) if self.version_id else _("mod_browser_header_all")
        )
        ctk.CTkLabel(
            header,
            text=header_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        info_parts = []
        if self._game_version:
            info_parts.append(f"MC {self._game_version}")
        if self._mod_loader:
            info_parts.append(self._mod_loader.capitalize())
        if self.version_id:
            info_text = " | ".join(info_parts) if info_parts else _("mod_browser_unknown_version")
            info_color = COLORS["success"] if info_parts else COLORS["warning"]
        else:
            info_text = _("mod_browser_all_versions")
            info_color = COLORS["success"]
        ctk.CTkLabel(header, text=info_text, font=ctk.CTkFont(family=FONT_FAMILY, size=12), text_color=info_color).pack(
            side=ctk.RIGHT
        )

        self._tabview = ctk.CTkTabview(
            main_frame,
            fg_color="transparent",
            segmented_button_fg_color=COLORS["bg_medium"],
            segmented_button_selected_color=COLORS["accent"],
            segmented_button_unselected_color=COLORS["bg_medium"],
            segmented_button_selected_hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            text_color_disabled=COLORS["text_secondary"],
        )
        self._tabview.pack(fill=ctk.BOTH, expand=True, pady=(0, 5))

        # 阶段 1.23（D-100）：标签页显示名在**构建时**记录一次。
        # 原先 _on_tab_changed 在调用时重算 _()，而设置窗口的 _on_language_change
        # 会**立刻**改全局翻译状态却不重建界面 —— 于是切换语言后控件标题仍是旧
        # 语言文本，重算出的新语言文本与之不相等，标签映射直接断裂（切页不生效）。
        self._tab_titles = {
            self.TAB_MODS: _("mod_browser_tab_mods"),
            self.TAB_RESOURCE_PACKS: _("mod_browser_tab_resourcepacks"),
            self.TAB_SHADERS: _("mod_browser_tab_shaders"),
        }
        for _title in self._tab_titles.values():
            self._tabview.add(_title)

        self._build_tab_content(self._tabview.tab(self._tab_titles[self.TAB_MODS]), self.TAB_MODS)
        self._build_tab_content(self._tabview.tab(self._tab_titles[self.TAB_RESOURCE_PACKS]), self.TAB_RESOURCE_PACKS)
        self._build_tab_content(self._tabview.tab(self._tab_titles[self.TAB_SHADERS]), self.TAB_SHADERS)

        self._tabview.configure(command=self._on_tab_changed)

    def _build_tab_content(self, tab_frame, tab_key: str):
        state = self._tab_states[tab_key]

        search_frame = ctk.CTkFrame(tab_frame, fg_color="transparent", height=40)
        search_frame.pack(fill=ctk.X, pady=(5, 8))
        search_frame.pack_propagate(False)

        placeholders = {
            self.TAB_MODS: _("mod_browser_search_mods_placeholder"),
            self.TAB_RESOURCE_PACKS: _("mod_browser_search_rp_placeholder"),
            self.TAB_SHADERS: _("mod_browser_search_shaders_placeholder"),
        }

        entry = ctk.CTkEntry(
            search_frame,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=placeholders.get(tab_key, "🔍 搜索..."),
        )
        entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        entry.bind("<Return>", lambda e, tk=tab_key: self._on_tab_search(tk))
        state["search_entry"] = entry

        search_btn = ctk.CTkButton(
            search_frame,
            text=_("mod_browser_search"),
            width=80,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda tk=tab_key: self._on_tab_search(tk),
        )
        search_btn.pack(side=ctk.LEFT)

        ai_search_btn = ctk.CTkButton(
            search_frame,
            text=_("ai_search_btn"),
            width=80,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=lambda tk=tab_key: self._on_ai_search(tk),
        )
        ai_search_btn.pack(side=ctk.LEFT, padx=(4, 0))
        state["ai_search_btn"] = ai_search_btn

        # ── 筛选区域：游戏版本（+ 模组加载器，仅模组页） ──
        filter_frame = ctk.CTkFrame(tab_frame, fg_color="transparent")
        filter_frame.pack(fill=ctk.X, pady=(0, 8))

        default_version = state["default_version"] or _("mod_browser_all_versions")
        version_var = ctk.StringVar(value=default_version)
        # 阶段 1.22 修正（D-91）：下拉框当前值额外用**纯 Python 值**镜像一份。
        # 原因：ctk.StringVar 是 Tk 变量，只能在主线程读；而 `_get_selected_version()`
        # 会被后台搜索/安装线程调用（worker 读 Tk 变量在 Tk 下不可靠、在 Qt 下会崩）。
        # 每个改动点都在主线程回调里更新这个镜像，后台线程只读它。
        self._mirror_version(tab_key, default_version)
        version_menu = ctk.CTkOptionMenu(
            filter_frame,
            variable=version_var,
            values=state["version_options"],
            width=170,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
            command=lambda value, tk=tab_key: self._mirror_version(tk, value),
        )
        version_menu.pack(side=ctk.LEFT, padx=(0, 8))
        state["version_var"] = version_var
        state["version_menu"] = version_menu

        if tab_key == self.TAB_MODS:
            default_loader_display = _("mod_browser_filter_all_loaders")
            for display, loader in state["loader_options"]:
                if loader == state["default_loader"]:
                    default_loader_display = display
                    break
            loader_var = ctk.StringVar(value=default_loader_display)
            # 同 D-91：加载器筛选也用纯值镜像一份（后台线程只读镜像）
            self._mirror_loader(tab_key, default_loader_display)
            loader_menu = ctk.CTkOptionMenu(
                filter_frame,
                variable=loader_var,
                values=[display for display, _ in state["loader_options"]],
                width=150,
                height=30,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color=COLORS["bg_medium"],
                button_color=COLORS["bg_light"],
                button_hover_color=COLORS["card_border"],
                dropdown_fg_color=COLORS["bg_medium"],
                dropdown_hover_color=COLORS["bg_light"],
                command=lambda value, tk=tab_key: self._mirror_loader(tk, value),
            )
            loader_menu.pack(side=ctk.LEFT)
            state["loader_var"] = loader_var
            state["loader_menu"] = loader_menu

        list_container = ctk.CTkFrame(tab_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        list_container.pack(fill=ctk.BOTH, expand=True, pady=(0, 8))

        list_frame = ctk.CTkScrollableFrame(
            list_container, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        list_frame.pack(fill=ctk.BOTH, expand=True, padx=5, pady=5)
        state["list_frame"] = list_frame

        loading_label = ctk.CTkLabel(
            list_frame,
            text=_("mod_browser_loading"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            justify=ctk.CENTER,
        )
        loading_label.pack(pady=40)
        state["loading_label"] = loading_label

        page_frame = ctk.CTkFrame(tab_frame, fg_color="transparent", height=34)
        page_frame.pack(fill=ctk.X)
        page_frame.pack_propagate(False)

        prev_btn = ctk.CTkButton(
            page_frame,
            text=_("mod_browser_prev_page"),
            width=90,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=lambda tk=tab_key: self._on_tab_prev_page(tk),
        )
        prev_btn.pack(side=ctk.LEFT)
        state["prev_btn"] = prev_btn

        page_label = ctk.CTkLabel(
            page_frame,
            text="0 / 0",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            width=100,
        )
        page_label.pack(side=ctk.LEFT, padx=10)
        state["page_label"] = page_label

        next_btn = ctk.CTkButton(
            page_frame,
            text=_("mod_browser_next_page"),
            width=90,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=lambda tk=tab_key: self._on_tab_next_page(tk),
        )
        next_btn.pack(side=ctk.LEFT)
        state["next_btn"] = next_btn

        result_count_label = ctk.CTkLabel(
            page_frame, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=COLORS["text_secondary"]
        )
        result_count_label.pack(side=ctk.RIGHT)
        state["result_count_label"] = result_count_label

        status_label = ctk.CTkLabel(
            tab_frame,
            text=_("mod_browser_ready"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        status_label.pack(anchor=ctk.W, pady=(5, 0))
        state["status_label"] = status_label

    def _on_tab_changed(self, value=None):
        selected = self._tabview.get()
        # 用构建时记录的显示名反查（见 _tab_titles 处的说明）：不能再调用时重算 _()
        tab_key = {title: key for key, title in self._tab_titles.items()}.get(selected)
        if tab_key:
            self._switch_to_tab(tab_key)

    def _switch_to_tab(self, tab_key: str):
        state = self._tab_states[tab_key]
        if state["total_hits"] == 0 and not state["current_query"]:
            children = state["list_frame"].winfo_children()
            has_content = any(not isinstance(w, ctk.CTkLabel) or w != state["loading_label"] for w in children)
            if not has_content or (len(children) == 1 and children[0] == state["loading_label"]):
                self._set_tab_status(tab_key, _("mod_browser_loading"))
                seq = self._begin_request(tab_key)
                self._run_in_thread(lambda: self._do_tab_search(tab_key, seq))

    def _on_tab_search(self, tab_key: str):
        state = self._tab_states[tab_key]
        entry = state["search_entry"]
        if entry:
            state["current_query"] = entry.get().strip()
        state["current_offset"] = 0
        state["_ai_cached_hits"] = None
        state["_cached_hits"] = None  # 清除常规搜索缓存
        self._set_tab_status(tab_key, _("mod_browser_searching"))
        seq = self._begin_request(tab_key)
        self._run_in_thread(lambda: self._do_tab_search(tab_key, seq))

    # ─── 请求世代守卫（阶段 1.22 修正 D-93）─────────────────────────
    # 原实现没有任何请求序号/取消/过期判定，于是有两个真实竞态：
    #   ① 连打两次搜索时，"晚返回的旧请求"会把新结果覆盖掉；
    #   ② 旧请求先写缓存、新请求后写缓存，而两个 after(0) 渲染回调
    #      的执行顺序不确定 → 列表显示 A 的第一页、缓存却是 B 的结果，
    #      用户一按"下一页"就翻到完全不相干的内容。
    # 守卫方式：每次**用户意图**（搜索 / AI 搜索 / 切页 / 首次加载）自增世代号，
    # 工作线程与 after 回调都带着自己那一代，对不上就整批丢弃（连缓存都不写）。

    def _begin_request(self, tab_key: str) -> int:
        """开始一次"决定显示内容"的新请求，返回其世代号。"""
        state = self._tab_states[tab_key]
        seq = int(state.get("_req_seq") or 0) + 1
        state["_req_seq"] = seq
        return seq

    def _is_stale(self, tab_key: str, seq: Optional[int]) -> bool:
        """该请求是否已被更新的请求取代（``seq`` 为 None 表示"不做世代判定"）。"""
        if seq is None:
            return False
        return seq != self._tab_states[tab_key].get("_req_seq")

    def _do_tab_search(self, tab_key: str, seq: Optional[int] = None):
        state = self._tab_states[tab_key]

        # 如果已有缓存，直接本地分页（翻页复用）
        if state["_cached_hits"] is not None:
            offset = state["current_offset"]
            page = page_slice(state["_cached_hits"], offset, self.PAGE_SIZE)
            self.after(0, lambda: self._render_tab_results(tab_key, page, seq))
            return

        try:
            # 原文对**未知标签页**是 ``else: return``（什么都不发、也不报错），
            # 而且那条分支发生在读筛选值**之前** —— 这里照原样先判一次，
            # 否则未知 tab_key 会先在 ``_get_selected_version`` 上抛 KeyError，
            # 被下面同一个 ``except`` 兜成"搜索失败"错误态（可见行为就变了）。
            if tab_key not in CLIENT_TABS:
                return

            # 三个标签页 → 三个双源入口的映射、结果归一化、来源统计都在服务层；
            # 服务按属性调用 ``curseforge.unified_search_*``，所以
            # ``tests/test_mod_browser_staleness.py`` 的 monkeypatch 替身照旧生效。
            outcome = _get_mod_browser_service(self).search_client_tab(
                tab_key,
                state["current_query"],
                self._get_selected_version(tab_key),
                self._get_selected_loader(tab_key),
            )
            if outcome is None:
                return

            # 阶段 1.22（D-93）：**发布动作整体搬到主线程**并带上世代号。
            # 原来在工作线程里直接写 state（缓存/总数/来源），再 after(0) 渲染，
            # 于是"缓存"与"被渲染的那一页"可能来自不同的请求。
            # 现在两者在同一次主线程回调里原子完成 —— 要么都生效、要么都丢弃。
            self.after(
                0,
                lambda: self._publish_tab_results(
                    tab_key, outcome.hits, outcome.total_hits, outcome.sources, seq
                ),
            )

        except Exception as e:
            logger.error(f"搜索失败 ({tab_key}): {e}")
            self.after(0, lambda err=str(e): self._render_tab_error(tab_key, err, seq))

    def _publish_tab_results(
        self,
        tab_key: str,
        all_hits: List[Dict],
        total_hits: int,
        sources: Dict,
        seq: Optional[int] = None,
    ):
        """在主线程一次性提交一次常规搜索的结果（缓存 + 首页渲染 + 状态）。

        阶段 1.22（D-93）：三个副作用必须在同一代里原子完成。
        """
        if self._is_stale(tab_key, seq):
            logger.debug(f"丢弃过期搜索结果: {tab_key} seq={seq}")
            return
        state = self._tab_states[tab_key]
        state["_cached_hits"] = all_hits
        state["total_hits"] = total_hits
        state["_sources"] = sources
        offset = state["current_offset"]
        self._render_tab_results(tab_key, all_hits[offset : offset + self.PAGE_SIZE], seq)

    def _publish_ai_results(
        self,
        tab_key: str,
        all_hits: List[Dict],
        keywords: List[str],
        query: str,
        seq: Optional[int] = None,
    ):
        """AI 搜索结果的原子提交（缓存 + 渲染 + 状态 + 按钮复位）。"""
        if self._is_stale(tab_key, seq):
            logger.debug(f"丢弃过期 AI 搜索结果: {tab_key} seq={seq}")
            return
        state = self._tab_states[tab_key]
        state["_ai_cached_hits"] = all_hits
        state["total_hits"] = len(all_hits)
        state["current_offset"] = 0
        self._render_tab_results(tab_key, all_hits[: self.PAGE_SIZE], seq)
        kw_text = ", ".join(keywords) if keywords else query
        self._set_tab_status(tab_key, _("ai_search_done", keywords=kw_text, total=len(all_hits)))
        self._restore_ai_button(tab_key)

    def _on_ai_search(self, tab_key: str):
        from tkinter import messagebox

        state = self._tab_states[tab_key]
        entry = state["search_entry"]
        query = entry.get().strip() if entry else ""
        if not query:
            state["current_query"] = ""
            state["current_offset"] = 0
            seq = self._begin_request(tab_key)
            self._run_in_thread(lambda: self._do_tab_search(tab_key, seq))
            return

        token = self.callbacks.get("get_jdz_token", lambda: None)()
        if not token:
            messagebox.showwarning(_("warning"), _("ai_search_login_required"), parent=self)
            return

        state["current_query"] = query
        state["current_offset"] = 0
        ai_btn = state.get("ai_search_btn")
        if ai_btn:
            try:
                ai_btn.configure(state="disabled", text=_("ai_search_optimizing"))
            except Exception:
                pass
        self._set_tab_status(tab_key, _("ai_search_optimizing"))
        seq = self._begin_request(tab_key)
        self._run_in_thread(lambda: self._do_ai_search(tab_key, query, token, seq))

    def _do_ai_search(self, tab_key: str, query: str, token: str, seq: Optional[int] = None):
        try:
            # 关键词扩展 → 逐词搜索 → 去重 → 按下载量排序 → 单词失败只告警，
            # 这五件事本来就在 ``modrinth.ai_merged_search`` 里，服务只做编排
            # （含 ``max_per_keyword=30`` 与"仅模组页传加载器"这两个约定）。
            outcome = _get_mod_browser_service(self).search_ai_merged_tab(
                tab_key,
                query,
                token,
                self._get_selected_version(tab_key),
                self._get_selected_loader(tab_key) if tab_key == self.TAB_MODS else None,
            )

            # 阶段 1.22（D-93）：与常规搜索同理 —— 一次性提交，且必须是最新一代。
            # 原来的 3 个 after(0, ...) 是分开排队的，执行顺序不确定，
            # 会出现"状态栏说 AI 搜索完成、列表却还是常规搜索的结果"。
            self.after(
                0,
                lambda: self._publish_ai_results(
                    tab_key, outcome.hits, outcome.keywords, query, seq
                ),
            )

        except Exception as e:
            logger.error(f"AI 搜索失败 ({tab_key}): {e}")
            if not self._is_stale(tab_key, seq):
                self.after(0, lambda err=str(e): self._render_tab_error(tab_key, err, seq))
            self.after(0, lambda: self._restore_ai_button_if_current(tab_key, seq))

    def _restore_ai_button_if_current(self, tab_key: str, seq: Optional[int] = None):
        """只在请求仍是最新一代时复位 AI 按钮（避免旧请求把新请求的按钮状态改回去）。"""
        if self._is_stale(tab_key, seq):
            return
        self._restore_ai_button(tab_key)

    def _restore_ai_button(self, tab_key: str):
        state = self._tab_states.get(tab_key)
        if state:
            ai_btn = state.get("ai_search_btn")
            if ai_btn:
                try:
                    ai_btn.configure(state="normal", text=_("ai_search_btn"))
                except Exception:
                    pass

    def _mirror_version(self, tab_key: str, display_value: Optional[str]) -> None:
        """把版本下拉框的显示值镜像成"纯值"（**只能在主线程调用**）。

        阶段 1.22 修正（D-91）。镜像规则与改造前 `_get_selected_version()` 的判定
        完全一致：空值或"全部版本"→ ``None``，否则就是版本字符串。
        """
        state = self._tab_states.get(tab_key)
        if state is None:
            return
        if not display_value or display_value == _("mod_browser_all_versions"):
            state["selected_version"] = None
        else:
            state["selected_version"] = display_value

    def _mirror_loader(self, tab_key: str, display_value: Optional[str]) -> None:
        """把加载器下拉框的显示值镜像成加载器标识（**只能在主线程调用**）。

        判定与改造前 `_get_selected_loader()` 一致：按显示名反查 ``loader_options``，
        找不到就是 ``None``。
        """
        state = self._tab_states.get(tab_key)
        if state is None:
            return
        loader = None
        for display, value in state.get("loader_options", []):
            if display == display_value:
                loader = value
                break
        state["selected_loader"] = loader

    def _get_selected_version(self, tab_key: str) -> Optional[str]:
        """获取当前筛选选中的游戏版本（未选择返回 None）。

        阶段 1.22 修正（D-91）：读的是主线程维护的**纯值镜像**，不再读
        ``ctk.StringVar`` —— 本方法会被后台搜索/安装线程调用，而 Tk 变量只能在
        主线程读（AGENTS.md：never let workers touch Tk）。
        """
        state = self._tab_states[tab_key]
        if "selected_version" not in state:
            # 兜底：镜像尚未建立（例如下拉框还没构建）时退回默认值
            return state.get("default_version")
        return state.get("selected_version")

    def _get_selected_loader(self, tab_key: str) -> Optional[str]:
        """获取当前筛选选中的模组加载器（未选择返回 None）。

        阶段 1.22 修正（D-91）：同 :meth:`_get_selected_version`，读纯值镜像。
        """
        state = self._tab_states[tab_key]
        if "selected_loader" not in state:
            return state.get("default_loader")
        return state.get("selected_loader")

    def _load_version_options(self):
        """后台加载游戏版本列表，填充筛选下拉菜单"""
        try:
            from modrinth import get_all_game_versions

            versions = get_all_game_versions()
        except Exception as e:
            logger.debug(f"加载游戏版本列表失败: {e}")
            versions = []

        def _apply():
            if not self.winfo_exists():
                return
            all_display = _("mod_browser_all_versions")
            options = [all_display] + list(versions)
            for tab_key, state in self._tab_states.items():
                menu = state.get("version_menu")
                var = state.get("version_var")
                if menu is None or var is None:
                    continue
                try:
                    menu.configure(values=options)
                    if var.get() not in options:
                        var.set(all_display)
                        # 阶段 1.22（D-91）：Tk 变量被改写时，纯值镜像也要跟着改，
                        # 否则后台线程会一直读到旧版本号。
                        self._mirror_version(tab_key, all_display)
                except Exception:
                    pass

        self.after(0, _apply)

    def _render_tab_results(self, tab_key: str, hits: List[Dict], seq: Optional[int] = None):
        """渲染某一页结果。

        ``seq`` 不为 None 时先做世代校验（阶段 1.22 / D-93）：过期的渲染请求
        必须整个丢弃，否则"旧请求的 after 回调晚于新请求执行"就会把界面改回旧数据。
        """
        if self._is_stale(tab_key, seq):
            logger.debug(f"丢弃过期渲染: {tab_key} seq={seq}")
            return
        state = self._tab_states[tab_key]
        list_frame = state["list_frame"]

        for w in list_frame.winfo_children():
            w.destroy()

        not_found_texts = {
            self.TAB_MODS: _("mod_browser_no_mods"),
            self.TAB_RESOURCE_PACKS: _("mod_browser_no_rp"),
            self.TAB_SHADERS: _("mod_browser_no_shaders"),
        }

        if not hits:
            ctk.CTkLabel(
                list_frame,
                text=not_found_texts.get(tab_key, _("mod_browser_no_results")),
                font=ctk.CTkFont(family=FONT_FAMILY, size=14),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=40)
            self._update_tab_pagination(tab_key)
            self._set_tab_status(tab_key, _("mod_browser_no_results"))
            return

        for item in hits:
            self._create_item(tab_key, item)

        self._update_tab_pagination(tab_key)

        start = state["current_offset"] + 1
        # 阶段 1.23（D-103）：区间上界以**本地实际条目数**为准，不能用后端报的
        # total_hits —— 否则会出现"显示 291-300，共 1500 个"这种与内容矛盾的文案。
        available = self._browsable_hits(state)
        end = min(state["current_offset"] + self.PAGE_SIZE, available)
        result_label = state["result_count_label"]
        if result_label:
            result_label.configure(text=_("mod_browser_result_range", start=start, end=end, total=available))
        self._set_tab_status(tab_key, _("mod_browser_total_found", total=state["total_hits"]))

    def _render_tab_error(self, tab_key: str, error_msg: str, seq: Optional[int] = None):
        """渲染错误态。``seq`` 不为 None 时同样要做世代校验（D-93）：
        旧请求的失败不能覆盖新请求已经拿到的成功结果。"""
        if self._is_stale(tab_key, seq):
            logger.debug(f"丢弃过期错误渲染: {tab_key} seq={seq}")
            return
        state = self._tab_states[tab_key]
        list_frame = state["list_frame"]

        for w in list_frame.winfo_children():
            w.destroy()

        ctk.CTkLabel(
            list_frame,
            text=_("mod_browser_search_failed", error=error_msg),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["error"],
            justify=ctk.CENTER,
        ).pack(pady=40)
        self._set_tab_status(tab_key, _("mod_browser_search_failed_status", error=error_msg))

    def _create_item(self, tab_key: str, item: Dict):
        state = self._tab_states[tab_key]
        list_frame = state["list_frame"]

        row = ctk.CTkFrame(list_frame, fg_color=COLORS["bg_medium"], corner_radius=8)
        row.pack(fill=ctk.X, pady=3, padx=2)

        top_row = ctk.CTkFrame(row, fg_color="transparent", height=36)
        top_row.pack(fill=ctk.X, padx=10, pady=(8, 2))
        top_row.pack_propagate(False)

        title = item.get("title", _("mod_browser_unknown"))

        icons = {self.TAB_MODS: "🧩", self.TAB_RESOURCE_PACKS: "🎨", self.TAB_SHADERS: "✨"}
        icon = icons.get(tab_key, "📦")

        ctk.CTkLabel(
            top_row,
            text=f"{icon} {title}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        ).pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        downloads = item.get("downloads", 0)
        dl_text = self._format_downloads(downloads)
        ctk.CTkLabel(
            top_row,
            text=f"📥 {dl_text}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT, padx=(5, 0))

        project_id = item.get("project_id", "")

        install_texts = {
            self.TAB_MODS: _("mod_browser_install"),
            self.TAB_RESOURCE_PACKS: _("mod_browser_install"),
            self.TAB_SHADERS: _("mod_browser_install"),
        }

        if tab_key == self.TAB_MODS:
            src = item.get("source", "modrinth")
            btn_cmd = lambda pid=project_id, t=title, s=src: self._on_install_mod(pid, t, s)
        elif tab_key == self.TAB_RESOURCE_PACKS:
            btn_cmd = lambda pid=project_id, t=title: self._on_install_resource_pack(pid, t)
        elif tab_key == self.TAB_SHADERS:
            btn_cmd = lambda pid=project_id, t=title: self._on_install_shader(pid, t)
        else:
            btn_cmd = None

        if btn_cmd:
            ctk.CTkButton(
                top_row,
                text=install_texts.get(tab_key, _("mod_browser_install")),
                width=70,
                height=28,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color=COLORS["success"],
                hover_color="#27ae60",
                text_color=COLORS["text_primary"],
                command=btn_cmd,
            ).pack(side=ctk.RIGHT, padx=(8, 0))

        description = item.get("description", "")
        if description:
            ctk.CTkLabel(
                row,
                text=description,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                wraplength=700,
                justify=ctk.LEFT,
                anchor=ctk.W,
            ).pack(fill=ctk.X, padx=10, pady=(0, 4))

        if tab_key == self.TAB_MODS:
            categories = item.get("categories", [])
            versions_display = item.get("versions", [])
            tag_parts = []
            if categories:
                loader_tags = [c for c in categories if c in ("forge", "fabric", "neoforge", "quilt")]
                if loader_tags:
                    tag_parts.append(" | ".join(l.capitalize() for l in loader_tags))
            if versions_display:
                from modrinth import compress_game_versions

                compressed = compress_game_versions(versions_display)
                if compressed:
                    tag_parts.append(compressed)
        else:
            versions_display = item.get("versions", [])
            tag_parts = []
            if versions_display:
                from modrinth import compress_game_versions

                compressed = compress_game_versions(versions_display)
                if compressed:
                    tag_parts.append(compressed)

        if tag_parts:
            tags_text = "  ·  ".join(tag_parts)
            ctk.CTkLabel(
                row,
                text=tags_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X, padx=10, pady=(0, 8))

    def _browsable_hits(self, state: Dict) -> int:
        """**本地能翻到的**条目数（阶段 1.23 修正 D-103）。

        缺陷：请求用 ``limit=300`` 一次拉一批，而后端在 ``total_hits`` 里报的是
        **匹配总数**（可能上千）。原实现用 ``total_hits`` 算页数，于是缓存只有
        300 条时分页却显示"1 / 150"：翻到缓存尾部就是**空页但页码仍在**，
        状态栏与内容互相矛盾。

        分页与"显示区间"都必须以**本地实际拥有的条目**为准；``total_hits`` 只用于
        "共找到 N 个"这种信息性提示。
        """
        # 优先级（AI 缓存 → 常规缓存 → 退回后端总数）的判定体搬到了服务层，
        # 本方法保持同名同签名，D-103 的守卫与调用点一字不改。
        return browsable_count(state.get("_ai_cached_hits"), state.get("_cached_hits"), state.get("total_hits", 0))

    def _update_tab_pagination(self, tab_key: str):
        state = self._tab_states[tab_key]
        available = self._browsable_hits(state)
        total_pages = browse_total_pages(available, self.PAGE_SIZE)
        current_page = (state["current_offset"] // self.PAGE_SIZE) + 1

        page_label = state["page_label"]
        prev_btn = state["prev_btn"]
        next_btn = state["next_btn"]

        if page_label:
            page_label.configure(text=f"{current_page} / {total_pages}")
        if prev_btn:
            prev_btn.configure(state=ctk.NORMAL if state["current_offset"] > 0 else ctk.DISABLED)
        if next_btn:
            # 用 available 而不是 total_hits：否则会给出一个翻过去必定空白的"下一页"
            next_btn.configure(
                state=ctk.NORMAL if state["current_offset"] + self.PAGE_SIZE < available else ctk.DISABLED
            )

    def _on_tab_prev_page(self, tab_key: str):
        state = self._tab_states[tab_key]
        if state["current_offset"] > 0:
            state["current_offset"] -= self.PAGE_SIZE
            if state["_ai_cached_hits"] is not None:
                self._render_page_from_ai_cache(tab_key)
            elif state["_cached_hits"] is not None:
                self._render_page_from_cache(tab_key)
            else:
                self._set_tab_status(tab_key, _("mod_browser_loading"))
                seq = self._begin_request(tab_key)
                self._run_in_thread(lambda: self._do_tab_search(tab_key, seq))

    def _on_tab_next_page(self, tab_key: str):
        state = self._tab_states[tab_key]
        # 阶段 1.23（D-103）：与按钮可用性用同一口径（本地实际条目数），
        # 否则按钮虽已禁用、键盘/程序化触发仍会翻到空页。
        if state["current_offset"] + self.PAGE_SIZE < self._browsable_hits(state):
            state["current_offset"] += self.PAGE_SIZE
            if state["_ai_cached_hits"] is not None:
                self._render_page_from_ai_cache(tab_key)
            elif state["_cached_hits"] is not None:
                self._render_page_from_cache(tab_key)
            else:
                self._set_tab_status(tab_key, _("mod_browser_loading"))
                seq = self._begin_request(tab_key)
                self._run_in_thread(lambda: self._do_tab_search(tab_key, seq))

    def _render_page_from_cache(self, tab_key: str):
        """从常规搜索缓存渲染页面"""
        state = self._tab_states[tab_key]
        cached = state["_cached_hits"]
        if cached is None:
            return
        offset = state["current_offset"]
        page = page_slice(cached, offset, self.PAGE_SIZE)
        self._render_tab_results(tab_key, page)
        self._update_tab_pagination(tab_key)

    def _render_page_from_ai_cache(self, tab_key: str):
        state = self._tab_states[tab_key]
        cached = state["_ai_cached_hits"]
        if cached is None:
            return
        offset = state["current_offset"]
        page = page_slice(cached, offset, self.PAGE_SIZE)
        self._render_tab_results(tab_key, page)
        self._update_tab_pagination(tab_key)

    def _ask_install_dir(self, default_dir: str, title: str) -> Optional[str]:
        """弹出系统文件夹选择器，初始导航到默认目录（不存在时自动创建）

        Args:
            default_dir: 默认目标目录（如 .minecraft/versions/<版本>/mods）
            title: 对话框标题

        Returns:
            用户选择的目录，取消返回 None
        """
        from tkinter import filedialog

        initial = default_dir
        if initial:
            try:
                Path(initial).mkdir(parents=True, exist_ok=True)
                initial = str(Path(initial))
            except Exception as e:
                # 创建失败（如路径非法或父路径不可写）时回退到最近的已存在父目录
                logger.warning(f"自动创建目录失败 ({initial}): {e}")
                p = Path(initial)
                while p and not p.exists():
                    p = p.parent
                initial = str(p) if p else None

        chosen = filedialog.askdirectory(initialdir=initial, title=title, parent=self)
        if not chosen:
            return None
        return chosen

    def _on_install_mod(self, project_id: str, title: str, source: str = "modrinth"):
        save_dir = self._ask_install_dir(
            self._get_mods_dir(), _("mod_browser_select_dir", kind=_("mod_browser_tab_mods"))
        )
        if save_dir is None:
            self._set_tab_status(self.TAB_MODS, _("mod_browser_install_cancelled"))
            return
        self._set_tab_status(self.TAB_MODS, _("mod_browser_fetching_version", title=title))
        self._run_in_thread(lambda: self._install_mod(project_id, title, source, save_dir))

    def _install_mod(self, project_id: str, title: str, source: str = "modrinth", mods_dir: Optional[str] = None):
        try:
            game_version = self._get_selected_version(self.TAB_MODS)
            mod_loader = self._get_selected_loader(self.TAB_MODS)
            if not game_version or not mod_loader:
                self.after(0, lambda: self._set_tab_status(self.TAB_MODS, _("mod_browser_need_version_install")))
                return

            if not mods_dir:
                mods_dir = self._get_mods_dir()

            # 两条分支（curseforge 无依赖列表 / modrinth 带依赖递归）、
            # "已装文件名列表"的约定、以及下载器的 status_callback 接线都在服务层；
            # 状态栏文案与成就触发留在本方法里（下文一字未改）。
            success, result, installed_names = _get_mod_browser_service(self).install_mod(
                project_id,
                title,
                source,
                game_version,
                mod_loader,
                mods_dir,
                status_callback=lambda msg: self.after(0, lambda: self._set_tab_status(self.TAB_MODS, msg)),
            )

            if success:
                if len(installed_names) > 1:
                    deps = ", ".join(installed_names[:-1])
                    self.after(
                        0,
                        lambda: self._set_tab_status(
                            self.TAB_MODS, _("mod_browser_install_success_deps", title=title, deps=deps)
                        ),
                    )
                    _trigger_ach("modder_dependency_expert")
                else:
                    self.after(
                        0, lambda: self._set_tab_status(self.TAB_MODS, _("mod_browser_install_success", title=title))
                    )
                _trigger_ach("modder_first_mod", value=len(installed_names))
                logger.info(f"模组安装成功: {installed_names} -> {result}")
            else:
                self.after(
                    0, lambda: self._set_tab_status(self.TAB_MODS, _("mod_browser_install_failed", error=result))
                )
                logger.error(f"模组安装失败: {result}")

        except Exception as e:
            error_msg = str(e)
            self.after(0, lambda: self._set_tab_status(self.TAB_MODS, _("mod_browser_install_error", error=error_msg)))
            logger.error(f"安装模组失败: {e}")

    def _on_install_resource_pack(self, project_id: str, title: str):
        save_dir = self._ask_install_dir(
            self._get_resourcepacks_dir(), _("mod_browser_select_dir", kind=_("mod_browser_tab_resourcepacks"))
        )
        if save_dir is None:
            self._set_tab_status(self.TAB_RESOURCE_PACKS, _("mod_browser_install_cancelled"))
            return
        self._set_tab_status(self.TAB_RESOURCE_PACKS, _("mod_browser_fetching_version", title=title))
        self._run_in_thread(lambda: self._install_resource_pack(project_id, title, save_dir))

    def _install_resource_pack(self, project_id: str, title: str, rp_dir: Optional[str] = None):
        try:
            game_version = self._get_selected_version(self.TAB_RESOURCE_PACKS)
            if not game_version:
                self.after(
                    0, lambda: self._set_tab_status(self.TAB_RESOURCE_PACKS, _("mod_browser_need_version_install"))
                )
                return

            if not rp_dir:
                rp_dir = self._get_resourcepacks_dir()

            success, result = _get_mod_browser_service(self).install_resource_pack(
                project_id,
                game_version=game_version,
                resourcepacks_dir=rp_dir,
                status_callback=lambda msg: self.after(0, lambda: self._set_tab_status(self.TAB_RESOURCE_PACKS, msg)),
            )

            if success:
                self.after(
                    0,
                    lambda: self._set_tab_status(
                        self.TAB_RESOURCE_PACKS, _("mod_browser_install_success", title=title)
                    ),
                )
                logger.info(f"资源包安装成功: {title} -> {result}")
            else:
                self.after(
                    0,
                    lambda: self._set_tab_status(
                        self.TAB_RESOURCE_PACKS, _("mod_browser_install_failed", error=result)
                    ),
                )
                logger.error(f"资源包安装失败: {result}")

        except Exception as e:
            error_msg = str(e)
            self.after(
                0,
                lambda: self._set_tab_status(self.TAB_RESOURCE_PACKS, _("mod_browser_install_error", error=error_msg)),
            )
            logger.error(f"安装资源包失败: {e}")

    def _on_install_shader(self, project_id: str, title: str):
        save_dir = self._ask_install_dir(
            self._get_shaderpacks_dir(), _("mod_browser_select_dir", kind=_("mod_browser_tab_shaders"))
        )
        if save_dir is None:
            self._set_tab_status(self.TAB_SHADERS, _("mod_browser_install_cancelled"))
            return
        self._set_tab_status(self.TAB_SHADERS, _("mod_browser_fetching_version", title=title))
        self._run_in_thread(lambda: self._install_shader(project_id, title, save_dir))

    def _install_shader(self, project_id: str, title: str, shader_dir: Optional[str] = None):
        try:
            game_version = self._get_selected_version(self.TAB_SHADERS)
            if not game_version:
                self.after(0, lambda: self._set_tab_status(self.TAB_SHADERS, _("mod_browser_need_version_install")))
                return

            if not shader_dir:
                shader_dir = self._get_shaderpacks_dir()

            success, result = _get_mod_browser_service(self).install_shader(
                project_id,
                game_version=game_version,
                shaderpacks_dir=shader_dir,
                status_callback=lambda msg: self.after(0, lambda: self._set_tab_status(self.TAB_SHADERS, msg)),
            )

            if success:
                self.after(
                    0, lambda: self._set_tab_status(self.TAB_SHADERS, _("mod_browser_install_success", title=title))
                )
                logger.info(f"光影安装成功: {title} -> {result}")
            else:
                self.after(
                    0, lambda: self._set_tab_status(self.TAB_SHADERS, _("mod_browser_install_failed", error=result))
                )
                logger.error(f"光影安装失败: {result}")

        except Exception as e:
            error_msg = str(e)
            self.after(
                0, lambda: self._set_tab_status(self.TAB_SHADERS, _("mod_browser_install_error", error=error_msg))
            )
            logger.error(f"安装光影失败: {e}")

    def _get_mods_dir(self) -> str:
        return self._resolve_install_dir(self.TAB_MODS)

    def _get_resourcepacks_dir(self) -> str:
        return self._resolve_install_dir(self.TAB_RESOURCE_PACKS)

    def _get_shaderpacks_dir(self) -> str:
        return self._resolve_install_dir(self.TAB_SHADERS)

    def _resolve_install_dir(self, tab_key: str) -> str:
        """解析安装目标目录（薄委托：services/mod_browser_service.py）

        依据当前筛选的游戏版本/加载器，在已安装版本中查找匹配实例：
        - 命中 → .minecraft/versions/<实例文件夹>/mods|resourcepacks|shaderpacks
        - 未命中或未指定筛选 → .minecraft 根目录下的全局资源目录

        两个 getter 作为**回调**传进服务（而不是先算出值再传）：服务内部的
        "先算 target、再匹配实例"顺序因此与改造前逐字一致。
        """
        return _get_mod_browser_service(self).resolve_install_dir(
            self.callbacks,
            tab_key,
            self._game_version,
            self.version_id,
            self._get_selected_version,
            self._get_selected_loader,
        )

    def _match_installed_instance(self, game_version: Optional[str], mod_loader: Optional[str]) -> Optional[str]:
        """在已安装版本中查找与目标版本/加载器匹配的实例文件夹名（薄委托）

        优先级:
        1. 窗口来源版本自身（filter 与来源一致时直接命中）
        2. 版本+加载器精确匹配的实例
        3. 仅版本匹配的实例（偏好带加载器的实例）

        Args:
            game_version: 目标游戏版本（如 "1.20.4"），None 表示不筛选
            mod_loader: 目标加载器（如 "forge"），None 表示不筛选

        Returns:
            实例文件夹名（版本 ID），未找到返回 None
        """
        return _get_mod_browser_service(self).match_installed_instance(
            self.callbacks, self.version_id, game_version, mod_loader
        )

    @staticmethod
    def _format_downloads(count: int) -> str:
        """下载量格式化（实现逐字搬到 :func:`services.browse_common.format_downloads`）"""
        return format_downloads(count)

    def _set_tab_status(self, tab_key: str, text: str):
        try:
            if self.winfo_exists():
                state = self._tab_states.get(tab_key)
                if state and state["status_label"]:
                    state["status_label"].configure(text=text)
        except Exception:
            pass

    def _run_in_thread(self, target, *args, **kwargs):
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
