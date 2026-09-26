"""资源管理窗口 - 模组/资源包/地图/光影管理

业务逻辑已搬到 ``services/resource_service.py``（阶段 1 任务 1.8-A）：目录解析、
扫描、搜索/过滤/分页、启停（``.disabled`` 后缀约定）、删除/安装/导入、导出文本、
双源更新检查与批量更新、缩略图取图与去重、成就触发。本文件只剩纯界面部分
（控件构建、``after`` 调度、``messagebox`` / ``filedialog``、剪贴板、通知弹窗、
``CTkImage`` 构造、``TaskRunner`` 缩略图池、i18n 文案）；下面每个受影响的方法
都退化成对服务的**薄委托**，**方法名与签名保持不变**，界面可见行为不变。

``_trigger_ach`` / ``_check_ach`` 的实现搬到了服务层，这里**继续可导入**，
且与那里是**同一批对象**（``ui.windows.resource_manager._trigger_ach is
services.resource_service._trigger_ach``）。

既有缺陷原样保留（本轮只搬家，不顺手修）：

- **D-91 / D-103 同型缺陷在本文件里不存在**（已逐处核对）：没有任何工作线程读
  控件值；分页总数一律由 ``len(filtered)`` / ``len(sorted_items)`` 算出，
  没有用后端 ``total_hits``。
- ``_update_pagination`` **全文件没有任何调用点**（死代码），保留不删。
- ``_check_full_house`` 查的 ``"datapacks"`` 不在 ``RESOURCE_TYPES`` 里，
  每次都抛 ``KeyError`` 被吞掉 → ``modder_full_house`` 成就不可达。
- ``_export_mod_list`` 的禁用标记是硬编码英文 ``" [Disabled]"``。
- 客户端 ``_open_folder`` 只有 Windows 分支（macOS/Linux 上必然失败）。
"""

import logging
import os
import threading
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk
from logzero import logger

from app.tasks import TaskRunner
from app.context import current_context
from services.resource_service import (
    THUMB_WORKERS,
    InstallResult,
    ResourceService,
    _check_ach,
    _trigger_ach,
)
from ui.constants import COLORS, FONT_FAMILY, RESOURCE_TYPES
from ui.dialogs import show_notification
from ui.i18n import _

try:
    from tkinterdnd2 import DND_FILES

    HAS_DND: bool = True
except ImportError:
    HAS_DND = False


def _get_resource_service(owner: Any = None) -> ResourceService:
    """惰性取得资源服务实例。

    写成**模块级函数**（而不是只在窗口类上的方法）与 ``ui/app_server.py`` 的
    ``_get_server_service(owner)`` / ``ui/app_online.py`` 的
    ``_get_online_service(owner)`` 保持同一形式：查找顺序是
    "``owner.context.try_get`` → ``owner`` 上自造并缓存"，
    ``ResourceService`` 不需要 ``AppContext``，构造期也只保存参数。
    """
    ctx = getattr(owner, "context", None) or current_context()
    if ctx is not None:
        getter = getattr(ctx, "try_get", None)
        if callable(getter):
            try:
                service = getter(ResourceService.name)
            except Exception:
                service = None
            if service is not None:
                return service
    service = getattr(owner, "_resource_service_fallback", None)
    if service is None:
        service = ResourceService()
        try:
            owner._resource_service_fallback = service
        except Exception:
            # owner 可能是 __slots__ 对象或 None：不缓存，本次调用照常可用
            pass
    return service


def _report_install_result(owner: Any, result: InstallResult) -> bool:
    """把服务的安装结果映射成状态栏文案，返回 ``result.ok``。

    ``outcome`` → i18n 键的映射刻意留在**界面层**：服务层因此不需要知道任何文案键，
    而 ``scripts/check_i18n.py`` 仍能看到这些**字面量键**
    （若改成服务传键、界面写 ``_(key)``，这几个键会从"键存在性 / 占位符一致 /
    调用点缺参"三项静态检查里静默掉出去）。
    """
    if result.outcome == "exists":
        owner._set_status(_("rm_file_exists", name=result.name))
    elif result.outcome == "map_exists":
        owner._set_status(_("rm_map_exists", name=result.name))
    elif result.outcome == "unsupported":
        owner._set_status(_("rm_unsupported_format", ext=result.ext))
    elif result.outcome == "failed":
        owner._set_status(_("rm_install_failed", error=result.error))
    return result.ok


class ResourceManagerWindow(ctk.CTkToplevel):
    """资源管理窗口 - 模组/资源包/地图/光影管理"""

    def __init__(self, parent, version_id: str, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self._fix_customtkinter_icon(self)
        self.version_id = version_id
        self.callbacks = callbacks

        self.title(_("resource_manager", version=version_id))
        self.geometry("760x600")
        self.minsize(680, 520)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)

        # 居中
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 760, 600
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self._mod_metadata: List[Dict] = []
        self._mod_loading: bool = False

        # ─── 缩略图加载状态（阶段 1.22 修正 D-92）───
        # 原实现"每个 zip 起一个线程"，无节流、无去重、无取消；而 _on_search
        # 会在**不换代**的情况下重渲染列表，于是搜索框每敲一个字符就为每个
        # 条目再起一批线程。现在改为：有界线程池 + 按路径去重 + 代际校验。
        self._thumb_pool: Optional[TaskRunner] = None
        #: 每当列表被"重新加载"（而非仅重渲染）就递增，用于丢弃过期回调
        self._thumb_generation: int = 0
        #: 本代际内已在队列/执行中的 zip 路径，防止重复解压
        self._thumb_inflight: set = set()
        self._search_text: str = ""
        self._current_items: List[Dict] = []
        self._filtered_items: List[Dict] = []
        self._update_checking: bool = False
        self._update_info: Dict[str, Dict] = {}  # modid -> {latest_version, project_id, ...}
        self._thumbnails: Dict[str, str] = {}  # path -> base64 thumbnail cache
        self._page_size: int = 10
        self._current_page: int = 1
        self._update_dialog_page: int = 1
        self._scan_cache: Dict[str, tuple] = {}  # rtype -> (last_items, dir_mtime)
        self._page_cache: Dict[str, dict] = {}  # "type_page" -> (items_slice, rendered_widgets)

        self._build_ui()

        # 注册拖拽支持
        if HAS_DND:
            self.after(100, self._register_dnd)

        # 加载当前标签页的资源列表
        self.after(200, self._refresh_current_list)

    def _get_minecraft_dir(self) -> Path:
        """获取当前版本的 .minecraft 目录（薄委托：services/resource_service.py）"""
        return _get_resource_service(self).minecraft_dir(self.callbacks)

    def _has_mod_loader(self, version_id: str) -> bool:
        """判断版本是否安装了模组加载器（需要版本隔离）

        优先读取版本 JSON 文件判断，回退到版本 ID 字符串匹配。

        参考 PCL-CE: McInstance.Modable 属性。
        """
        service = _get_resource_service(self)
        return service.has_mod_loader(version_id, service.minecraft_dir(self.callbacks))

    def _get_resource_dir(self, resource_type: str) -> Path:
        """获取指定资源类型的目录，仅模组加载器版本使用版本隔离目录（薄委托）"""
        return _get_resource_service(self).resource_dir_for(self.callbacks, self.version_id, resource_type)

    def _get_resource_label(self, rtype: str) -> str:
        labels = {
            "mods": "resource_mods",
            "resourcepacks": "resource_packs",
            "saves": "resource_maps",
            "shaderpacks": "resource_shaders",
        }
        return _(labels.get(rtype, rtype))

    def _get_resource_desc(self, rtype: str) -> str:
        descs = {
            "mods": "rm_mods_desc",
            "resourcepacks": "rm_resourcepacks_desc",
            "saves": "rm_saves_desc",
            "shaderpacks": "rm_shaderpacks_desc",
        }
        return _(descs.get(rtype, ""))

    def _build_ui(self):
        """构建界面"""
        # 主容器
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        # 标题
        title_label = ctk.CTkLabel(
            main_frame,
            text=_("rm_title", version=self.version_id),
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title_label.pack(anchor=ctk.W, pady=(0, 10))

        # 标签页切换按钮
        tab_frame = ctk.CTkFrame(main_frame, fg_color="transparent", height=38)
        tab_frame.pack(fill=ctk.X, pady=(0, 10))
        tab_frame.pack_propagate(False)

        self._tab_var = ctk.StringVar(value="mods")
        self._tab_buttons: Dict[str, ctk.CTkButton] = {}

        for rtype, rconf in RESOURCE_TYPES.items():
            label_text: str = self._get_resource_label(rtype)
            btn = ctk.CTkButton(
                tab_frame,
                text=label_text,
                height=32,
                font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
                fg_color=COLORS["bg_light"] if rtype == "mods" else "transparent",
                hover_color=COLORS["card_border"],
                text_color=COLORS["text_primary"],
                corner_radius=6,
                command=lambda t=rtype: self._switch_tab(t),
            )
            btn.pack(side=ctk.LEFT, padx=(0, 5))
            self._tab_buttons[rtype] = btn

        # 内容区域
        content_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        content_frame.pack(fill=ctk.BOTH, expand=True)

        # 拖拽提示区 + 操作按钮
        top_bar = ctk.CTkFrame(content_frame, fg_color="transparent", height=42)
        top_bar.pack(fill=ctk.X, padx=12, pady=(10, 5))
        top_bar.pack_propagate(False)

        self._drag_hint_label = ctk.CTkLabel(
            top_bar,
            text=self._get_resource_desc("mods"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._drag_hint_label.pack(side=ctk.LEFT)

        # 打开文件夹 + 选择文件安装 按钮
        self._open_folder_btn = ctk.CTkButton(
            top_bar,
            text=_("resource_open_folder"),
            width=110,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._open_folder,
        )
        self._open_folder_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        self._add_file_btn = ctk.CTkButton(
            top_bar,
            text=_("resource_add"),
            width=130,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._select_file_install,
        )
        self._add_file_btn.pack(side=ctk.RIGHT)

        # 导出模组列表按钮（仅模组标签页可见）
        self._export_btn = ctk.CTkButton(
            top_bar,
            text=_("mod_export_list"),
            width=100,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._export_mod_list,
        )
        self._export_btn.pack(side=ctk.RIGHT, padx=(5, 5))

        # 检查更新按钮（仅模组标签页可见）
        self._check_updates_btn = ctk.CTkButton(
            top_bar,
            text=_("mod_check_updates"),
            width=100,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            text_color=COLORS["text_primary"],
            command=self._check_mod_updates,
        )
        self._check_updates_btn.pack(side=ctk.RIGHT, padx=(5, 5))

        # 分割线
        ctk.CTkFrame(content_frame, fg_color=COLORS["card_border"], height=1).pack(fill=ctk.X, padx=12, pady=(0, 5))

        # 通用搜索栏
        self._search_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        self._search_entry = ctk.CTkEntry(
            self._search_frame,
            placeholder_text=_("rm_search_placeholder_mods"),
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
        )
        self._search_entry.pack(fill=ctk.X, padx=12, pady=(0, 5))
        self._search_frame.pack(fill=ctk.X, pady=(0, 0))
        self._search_entry.bind("<KeyRelease>", self._on_search)
        self._search_entry.bind("<Return>", self._on_search)

        # 加载中提示（仅模组标签页可见）
        self._loading_label = ctk.CTkLabel(
            content_frame,
            text=_("mod_loading_metadata"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_secondary"],
        )

        # 拖拽放置区 + 资源列表
        self._drop_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        self._drop_frame.pack(fill=ctk.BOTH, expand=True, padx=12, pady=(0, 10))

        # 空状态提示（拖拽区域背景）
        self._empty_label = ctk.CTkLabel(
            self._drop_frame,
            text=_("rm_drop_hint"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            justify=ctk.CENTER,
        )

        # 资源列表（可滚动）- 初始不pack，由_refresh_current_list管理
        self._list_frame = ctk.CTkScrollableFrame(
            self._drop_frame, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )

        # 分页控件
        self._page_frame = ctk.CTkFrame(content_frame, fg_color="transparent", height=34)
        self._page_frame.pack_propagate(False)

        self._prev_btn = ctk.CTkButton(
            self._page_frame,
            text=_("rm_page_prev"),
            width=85,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            state=ctk.DISABLED,
            command=self._on_prev_page,
        )
        self._prev_btn.pack(side=ctk.LEFT, padx=(12, 0))

        self._page_label = ctk.CTkLabel(
            self._page_frame,
            text="1 / 1",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            width=80,
        )
        self._page_label.pack(side=ctk.LEFT, padx=8)

        self._next_btn = ctk.CTkButton(
            self._page_frame,
            text=_("rm_page_next"),
            width=85,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            state=ctk.DISABLED,
            command=self._on_next_page,
        )
        self._next_btn.pack(side=ctk.LEFT)

        # 底部状态栏
        self._status_label = ctk.CTkLabel(
            main_frame,
            text=_("rm_status_ready"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._status_label.pack(anchor=ctk.W, pady=(5, 0))

    def _register_dnd(self):
        """注册拖拽支持"""
        if not HAS_DND:
            return
        try:
            self.tk.call("package", "require", "tkdnd")
            self._drop_frame.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self._drop_frame.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            self._list_frame.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
            self._list_frame.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            logger.info("拖拽支持已注册")
        except Exception as e:
            logger.warning(f"拖拽注册失败: {e}")

    def _on_drop(self, event):
        """拖拽文件放下回调（路径解析与"能不能装"的判定薄委托到服务层）"""
        service = _get_resource_service(self)
        # tkinterdnd2 传递的路径可能用 {} 包裹且以空格分隔
        raw = event.data
        files = service.parse_drop_paths(raw)

        current_type = self._tab_var.get()

        installed = 0
        for fpath in files:
            fpath = fpath.strip()
            if not fpath:
                continue
            # 地图存档可能是文件夹
            if service.accept_drop_entry(fpath, current_type):
                if self._install_resource(fpath, current_type):
                    installed += 1

        if installed > 0:
            self._set_status(_("rm_install_count", count=installed))
            self._refresh_current_list()
        else:
            self._set_status(_("rm_no_valid_files"))

    def _switch_tab(self, tab_name: str):
        """切换标签页"""
        self._tab_var.set(tab_name)

        # 更新按钮高亮
        for rtype, btn in self._tab_buttons.items():
            if rtype == tab_name:
                btn.configure(fg_color=COLORS["bg_light"])
            else:
                btn.configure(fg_color="transparent")

        # 更新提示文字
        self._drag_hint_label.configure(text=self._get_resource_desc(tab_name))

        # 更新搜索栏占位符
        placeholder_keys = {
            "mods": "rm_search_placeholder_mods",
            "resourcepacks": "rm_search_placeholder_resourcepacks",
            "saves": "rm_search_placeholder_saves",
            "shaderpacks": "rm_search_placeholder_shaderpacks",
        }
        self._search_entry.configure(placeholder_text=_(placeholder_keys.get(tab_name, "rm_search_placeholder_mods")))

        # 显示/隐藏导出和更新按钮（仅模组标签页可见）
        if tab_name == "mods":
            self._export_btn.pack(side=ctk.RIGHT, padx=(5, 5), before=self._open_folder_btn)
            self._check_updates_btn.pack(side=ctk.RIGHT, padx=(5, 5), before=self._export_btn)
        else:
            self._export_btn.pack_forget()
            self._check_updates_btn.pack_forget()

        # 清空搜索
        self._search_entry.delete(0, "end")
        self._search_text = ""

        # 重置分页
        self._current_page = 1
        self._page_cache.clear()

        # 隐藏加载中标签
        self._loading_label.pack_forget()

        self._refresh_current_list()

    def _refresh_current_list(self, force: bool = False):
        """刷新当前标签页的资源列表"""
        current_type = self._tab_var.get()
        resource_dir = self._get_resource_dir(current_type)

        logger.info(f"刷新资源列表: type={current_type}, dir={resource_dir}, exists={resource_dir.exists()}")

        # 重置分页
        self._current_page = 1
        self._page_cache.clear()

        # 先隐藏两个区域
        self._empty_label.pack_forget()
        self._list_frame.pack_forget()
        self._loading_label.pack_forget()
        self._page_frame.pack_forget()

        # 清空列表
        for w in self._list_frame.winfo_children():
            w.destroy()

        if current_type == "mods":
            self._scan_cache.pop("mods", None)
            self._refresh_mod_list(resource_dir)
            return

        # 非模组类型：检查缓存（mtime 比对本身在服务层）
        if not force and current_type in self._scan_cache:
            cached_items, cached_mtime = self._scan_cache[current_type]
            if _get_resource_service(self).scan_cache_usable(cached_mtime, resource_dir):
                logger.info(f"使用缓存扫描结果: type={current_type}, items={len(cached_items)}")
                self._current_items = cached_items
                if not cached_items:
                    self._empty_label.pack(fill=ctk.BOTH, expand=True)
                    self._set_status(_("rm_folder_empty", label=self._get_resource_label(current_type)))
                else:
                    self._render_filtered_list(current_type)
                return

        if not resource_dir.exists():
            self._current_items = []
            self._scan_cache.pop(current_type, None)
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._set_status(_("rm_folder_not_exist", path=str(resource_dir)))
            return

        # 获取资源文件列表（同步扫描，速度快）
        items = self._scan_resources(resource_dir, current_type)
        logger.info(f"扫描到 {len(items)} 个资源")

        self._current_items = items

        # 更新缓存
        try:
            self._scan_cache[current_type] = (items, resource_dir.stat().st_mtime)
        except Exception:
            pass

        if not items:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._set_status(_("rm_folder_empty", label=self._get_resource_label(current_type)))
            return

        # 缩略图进度跟踪（仅资源包和光影标签页）
        # 阶段 1.22（D-92）：这里是一次"重新加载"，换代并清空在途去重表。
        # 换代后，上一轮尚未返回的缩略图回调不会再抬高新一轮的计数器。
        self._thumb_generation += 1
        self._thumb_inflight.clear()
        self._thumbnail_loaded = 0
        self._thumbnail_total = 0
        zip_items = _get_resource_service(self).thumbnail_candidates(items, current_type)

        if zip_items:
            self._thumbnail_total = len(zip_items)

        self._render_filtered_list(current_type)

    def _refresh_mod_list(self, mods_dir: Path):
        """刷新模组列表（含元数据提取；提取自身薄委托到服务层）"""
        service = _get_resource_service(self)

        self._mod_loading = True
        self._loading_label.pack(before=self._drop_frame, fill=ctk.X, padx=12, pady=(5, 10))

        def _load_metadata():
            try:
                results = service.extract_mods_metadata(
                    mods_dir,
                    status_callback=lambda done, total: self.after(
                        0, lambda d=done, t=total: self._update_mod_loading(d, t)
                    ),
                )
                self.after(0, lambda r=results: self._on_mod_metadata_loaded(r))
            except Exception as e:
                logger.error(f"提取模组元数据失败: {e}")
                self.after(0, lambda: self._on_mod_metadata_loaded([]))

        thread = threading.Thread(target=_load_metadata, daemon=True)
        thread.start()

    def _update_mod_loading(self, done: int, total: int):
        """更新模组加载进度"""
        if not self.winfo_exists():
            return
        if self._mod_loading:
            self._loading_label.configure(text=_("mod_loading_progress", done=done, total=total))

    def _on_mod_metadata_loaded(self, results: List[Dict]):
        """模组元数据加载完成回调"""
        if not self.winfo_exists():
            return
        self._mod_loading = False
        self._mod_metadata = results
        self._loading_label.pack_forget()
        self._render_mod_list()
        if results:
            _trigger_ach("modder_first_mod", value=len(results))
            _trigger_ach("modder_mod_expert", value=len(results), trigger_type="set")
        self._check_full_house()

    def _check_full_house(self):
        """检查是否已安装所有资源类型（全家福成就）

        判定在服务层；**注意这里不是逐字搬家** —— 原文那五种资源目录的判定里
        ``"datapacks"`` 必然 ``KeyError``（``RESOURCE_TYPES`` 只有 4 个键），
        被吞掉后成就从不触发（D-109）。任务下发者已裁决按 4 语言文案实现
        「Forge / Fabric / NeoForge 各装过一个版本」（``version_utils.has_all_mod_loaders``），
        本轮把这条**悬空的接线**补在服务层，见 ``services/resource_service.py``
        的 ``check_full_house``。
        """
        _get_resource_service(self).check_full_house(self._get_minecraft_dir())

    @staticmethod
    def _dir_has_content(directory: Path) -> bool:
        """目录是否有内容（薄委托；本方法无 self，故直接走服务类的静态方法）"""
        return ResourceService.dir_has_content(directory)

    def _on_search(self, event=None):
        """搜索过滤（词规范化薄委托；控件读取留在界面）"""
        self._search_text = _get_resource_service(self).normalize_search(self._search_entry.get())
        self._current_page = 1
        self._page_cache.clear()
        current_type = self._tab_var.get()
        if current_type == "mods":
            self._render_mod_list()
        else:
            self._render_filtered_list(current_type)

    def _render_mod_list(self):
        """渲染模组列表"""
        self._list_frame.pack_forget()
        self._empty_label.pack_forget()

        if self._mod_loading:
            self._page_frame.pack_forget()
            return

        if not self._mod_metadata:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._page_frame.pack_forget()
            self._set_status(_("mod_folder_empty"))
            return

        # 搜索过滤（匹配规则在服务层）
        service = _get_resource_service(self)
        filtered = service.filter_mods(self._mod_metadata, self._search_text)

        self._filtered_items = filtered

        if not filtered:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._page_frame.pack_forget()
            self._set_status(_("mod_search_no_results"))
            return

        # 页数计算与页码钳制在服务层（原：max(1, ceil(...)) + if 越界则钳到末页）
        self._current_page = service.clamp_page(
            self._current_page, service.total_pages(len(filtered), self._page_size)
        )

        self._render_current_page()

    def _render_filtered_list(self, resource_type: str):
        """渲染非模组标签页的过滤列表"""
        self._list_frame.pack_forget()
        self._empty_label.pack_forget()

        service = _get_resource_service(self)
        self._filtered_items = service.filter_items(self._current_items, self._search_text)

        if not self._filtered_items:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._page_frame.pack_forget()
            self._set_status(_("rm_search_no_results"))
            return

        # 页数计算与页码钳制在服务层
        self._current_page = service.clamp_page(
            self._current_page, service.total_pages(len(self._filtered_items), self._page_size)
        )

        self._render_current_page()

    def _render_current_page(self):
        """渲染当前分页的资源列表（使用页面缓存加速翻页）"""
        current_type = self._tab_var.get()
        service = _get_resource_service(self)
        total = len(self._filtered_items)
        total_pages = service.total_pages(total, self._page_size)

        start = (self._current_page - 1) * self._page_size
        end = min(start + self._page_size, total)
        page_items = self._filtered_items[start:end]

        cache_key = f"{current_type}_{self._current_page}"
        items_key = service.page_items_key(page_items)

        cached = self._page_cache.get(cache_key)
        if cached and cached.get("items_key") == items_key:
            cached["widgets"] = [w for w in cached["widgets"] if w.winfo_exists()]
            if cached["widgets"]:
                for w in self._list_frame.winfo_children():
                    w.pack_forget()
                self._list_frame.pack(fill=ctk.BOTH, expand=True)
                self._page_frame.pack(fill=ctk.X, padx=12, pady=(5, 0))
                for w in cached["widgets"]:
                    w.pack(fill=ctk.X, pady=2)
                self._update_pagination_labels(total_pages, total, current_type)
                return

        for w in self._list_frame.winfo_children():
            w.destroy()

        self._list_frame.pack(fill=ctk.BOTH, expand=True)
        self._page_frame.pack(fill=ctk.X, padx=12, pady=(5, 0))

        rendered = []
        if current_type == "mods":
            for item in page_items:
                self._create_mod_card(item)
                rendered.append(self._list_frame.winfo_children()[-1])
        else:
            for item in page_items:
                self._create_resource_item(item, current_type)
                rendered.append(self._list_frame.winfo_children()[-1])

        self._page_cache[cache_key] = {"items_key": items_key, "widgets": rendered}
        self._update_pagination_labels(total_pages, total, current_type)

    def _update_pagination_labels(self, total_pages: int, total: int, current_type: str):
        self._page_label.configure(text=_("rm_page_info", current=self._current_page, total=total_pages))
        self._prev_btn.configure(state=ctk.NORMAL if self._current_page > 1 else ctk.DISABLED)
        self._next_btn.configure(state=ctk.NORMAL if self._current_page < total_pages else ctk.DISABLED)
        label = self._get_resource_label(current_type)
        self._set_status(_("rm_list_count", count=total, page=self._current_page, total_pages=total_pages, label=label))

    def _update_pagination(self):
        """更新分页控件状态

        记录现状、疑为缺陷：**本方法全文件没有任何调用点**（列表渲染走的是
        ``_update_pagination_labels``），属于死代码。为保证"方法名与签名一字不变"，
        本轮原样保留，只把页数计算薄委托出去。
        """
        service = _get_resource_service(self)
        total = len(self._filtered_items)
        total_pages = service.total_pages(total, self._page_size)
        self._page_label.configure(text=_("rm_page_info", current=self._current_page, total=total_pages))
        self._prev_btn.configure(state=ctk.NORMAL if self._current_page > 1 else ctk.DISABLED)
        self._next_btn.configure(state=ctk.NORMAL if self._current_page < total_pages else ctk.DISABLED)

    def _on_prev_page(self):
        """上一页"""
        if self._current_page > 1:
            self._current_page -= 1
            self._render_current_page()

    def _on_next_page(self):
        """下一页"""
        service = _get_resource_service(self)
        total = len(self._filtered_items)
        total_pages = service.total_pages(total, self._page_size)
        if self._current_page < total_pages:
            self._current_page += 1
            self._render_current_page()

    def _create_mod_card(self, item: Dict):
        """创建模组卡片"""
        row = ctk.CTkFrame(self._list_frame, fg_color=COLORS["bg_medium"], corner_radius=8)
        row.pack(fill=ctk.X, pady=3, padx=2)

        # 左侧: 图标
        icon_size = 48
        icon_frame = ctk.CTkFrame(row, fg_color="transparent", width=icon_size, height=icon_size)
        icon_frame.pack(side=ctk.LEFT, padx=(8, 8), pady=8)
        icon_frame.pack_propagate(False)

        icon_base64 = item.get("icon_base64")
        if icon_base64:
            try:
                import base64
                from io import BytesIO

                from PIL import Image

                img_data = base64.b64decode(icon_base64)
                img = Image.open(BytesIO(img_data))
                photo = ctk.CTkImage(img, size=(icon_size, icon_size))
                icon_label = ctk.CTkLabel(icon_frame, image=photo, text="")
                icon_label.pack(fill=ctk.BOTH, expand=True)
            except Exception:
                self._create_fallback_icon(icon_frame, item, icon_size)
        else:
            self._create_fallback_icon(icon_frame, item, icon_size)

        # 右侧按钮区（先打包，确保不被打扰文本遮挡）
        btn_frame = ctk.CTkFrame(row, fg_color="transparent")
        btn_frame.pack(side=ctk.RIGHT, padx=(0, 5), pady=4)

        # 启用/禁用按钮（仅模组）
        toggle_text = _("resource_enable") if item.get("disabled") else _("resource_disable")
        toggle_btn = ctk.CTkButton(
            btn_frame,
            text=toggle_text,
            width=45,
            height=24,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_secondary"],
            command=lambda p=item["path"], d=item.get("disabled", False): self._toggle_mod(p, d),
        )
        toggle_btn.pack(side=ctk.RIGHT, padx=(2, 2))

        # 删除按钮
        del_btn = ctk.CTkButton(
            btn_frame,
            text="🗑",
            width=26,
            height=24,
            font=ctk.CTkFont(size=11),
            fg_color="transparent",
            hover_color=COLORS["accent"],
            text_color=COLORS["text_secondary"],
            command=lambda p=item["path"], n=item.get("name", item.get("filename", "")): self._delete_resource(p, n),
        )
        del_btn.pack(side=ctk.RIGHT, padx=(0, 2))

        # 右侧: 信息区（后打包，自动填充按钮剩余空间）
        info_frame = ctk.CTkFrame(row, fg_color="transparent")
        info_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 8), pady=6)

        # 第一行: 名称
        name_text = item.get("name", item.get("filename", "???"))
        if item.get("disabled"):
            name_text += _("rm_disabled_suffix")
        name_label = ctk.CTkLabel(
            info_frame,
            text=name_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_secondary"] if item.get("disabled") else COLORS["text_primary"],
            anchor=ctk.W,
        )
        name_label.pack(fill=ctk.X)

        # 第二行: 作者
        author = item.get("author", "")
        if author:
            author_short = author[:70] + "..." if len(author) > 70 else author
            author_label = ctk.CTkLabel(
                info_frame,
                text=author_short,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            )
            author_label.pack(fill=ctk.X, pady=(2, 0))

        # 第三行: 简介
        description = item.get("description", "")
        if description:
            desc_clean = " ".join(description.split())
            if desc_clean:
                desc_short = desc_clean[:80] + "..." if len(desc_clean) > 80 else desc_clean
                desc_label = ctk.CTkLabel(
                    info_frame,
                    text=desc_short,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                    text_color=COLORS["text_secondary"],
                    anchor=ctk.W,
                )
                desc_label.pack(fill=ctk.X, pady=(1, 0))

        # 第四行: modid + 版本 + 文件名
        bottom_frame = ctk.CTkFrame(info_frame, fg_color="transparent")
        bottom_frame.pack(fill=ctk.X, pady=(3, 0))

        modid = item.get("modid", "")
        if modid:
            modid_label = ctk.CTkLabel(
                bottom_frame,
                text=modid,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["accent"],
                anchor=ctk.W,
            )
            modid_label.pack(side=ctk.LEFT)

        mod_version = item.get("version", "")
        if mod_version:
            ver_label = ctk.CTkLabel(
                bottom_frame,
                text=f"v{mod_version}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["success"],
                anchor=ctk.W,
            )
            ver_label.pack(side=ctk.LEFT, padx=(6, 0))

        filename = item.get("filename", "")
        if filename:
            filename_label = ctk.CTkLabel(
                bottom_frame,
                text=filename[:50] + "..." if len(filename) > 50 else filename,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            )
            filename_label.pack(side=ctk.LEFT, padx=(8, 0))

    def _create_fallback_icon(self, parent: ctk.CTkFrame, item: Dict, size: int):
        """创建默认图标"""
        icon_text = "🔕" if item.get("disabled") else "🧩"
        icon_label = ctk.CTkLabel(
            parent, text=icon_text, font=ctk.CTkFont(size=size // 2), text_color=COLORS["text_secondary"]
        )
        icon_label.pack(fill=ctk.BOTH, expand=True)

    def _scan_resources(self, resource_dir: Path, resource_type: str) -> List[Dict]:
        """扫描资源目录（薄委托：枚举/排序/体积/禁用态判定都在服务层）"""
        return _get_resource_service(self).scan_resources(resource_dir, resource_type)

    def _format_size(self, size: int) -> str:
        """格式化文件大小（薄委托）"""
        return _get_resource_service(self).format_size(size)

    def _create_resource_item(self, item: Dict, resource_type: str):
        """创建资源列表项"""
        row = ctk.CTkFrame(self._list_frame, fg_color=COLORS["bg_medium"], corner_radius=6, height=36)
        row.pack(fill=ctk.X, pady=2)
        row.pack_propagate(False)

        # 缩略图/图标
        icon_frame = ctk.CTkFrame(row, fg_color="transparent", width=32, height=32)
        icon_frame.pack(side=ctk.LEFT, padx=(5, 2), pady=2)
        icon_frame.pack_propagate(False)

        has_preview = False

        # 尝试为资源包/光影提取预览缩略图
        if resource_type in ("resourcepacks", "shaderpacks") and not item.get("is_dir"):
            from pathlib import Path as _Path

            zip_p = _Path(item["path"])
            ext = zip_p.suffix.lower()
            if ext in (".zip", ".jar"):
                # 检查是否已有缓存的缩略图
                cached = item.get("_thumbnail")
                if cached:
                    self._set_thumbnail_icon(icon_frame, cached, 28)
                    has_preview = True
                else:
                    # 异步加载缩略图
                    self._load_thumbnail_async(icon_frame, item, 28)
                    # 占位图标在下面设置

        # 默认图标
        if not has_preview:
            if item.get("disabled"):
                icon_text = "🔕"
            elif item.get("is_dir"):
                icon_text = "📁"
            elif resource_type == "mods":
                icon_text = "🧩"
            elif resource_type == "resourcepacks":
                icon_text = "🎨"
            elif resource_type == "shaderpacks":
                icon_text = "✨"
            else:
                icon_text = "📄"

            icon_label = ctk.CTkLabel(
                icon_frame, text=icon_text, font=ctk.CTkFont(size=14), text_color=COLORS["text_secondary"]
            )
            icon_label.pack(fill=ctk.BOTH, expand=True)

        # 删除按钮（先打包右侧按钮，确保不被打扰文本遮挡）
        del_btn = ctk.CTkButton(
            row,
            text="🗑",
            width=30,
            height=26,
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=COLORS["accent"],
            text_color=COLORS["text_secondary"],
            command=lambda p=item["path"], n=item["name"]: self._delete_resource(p, n),
        )
        del_btn.pack(side=ctk.RIGHT, padx=(0, 2))

        # 启用/禁用按钮（仅模组）
        if resource_type == "mods" and not item.get("is_dir"):
            toggle_text = _("rm_enable") if item.get("disabled") else _("rm_disable")
            toggle_btn = ctk.CTkButton(
                row,
                text=toggle_text,
                width=50,
                height=26,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color="transparent",
                hover_color=COLORS["bg_light"],
                text_color=COLORS["text_secondary"],
                command=lambda p=item["path"], d=item.get("disabled", False): self._toggle_mod(p, d),
            )
            toggle_btn.pack(side=ctk.RIGHT, padx=(2, 2))

        # 名称（后打包，自动填充按钮剩余空间）
        name_text = item["name"]
        if item.get("disabled"):
            name_text += _("rm_disabled_suffix")
        if item.get("is_dir") and not item.get("has_level_dat"):
            name_text += _("rm_non_standard_map")

        name_label = ctk.CTkLabel(
            row,
            text=name_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"] if item.get("disabled") else COLORS["text_primary"],
            anchor=ctk.W,
        )
        name_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=5)

        # 大小信息
        if "size" in item:
            size_label = ctk.CTkLabel(
                row,
                text=item["size"],
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
            )
            size_label.pack(side=ctk.LEFT, padx=(0, 5))

    def _set_thumbnail_icon(self, icon_frame: ctk.CTkFrame, base64_data: str, size: int):
        """设置缩略图图标（会先清除已有的子控件，避免与 emoji 图标重叠）

        任务 1.24（D-114，调查 D-94 时实测出来的真实现场）：晚到的缩略图回调可能打到
        一个**已被销毁**的卡片上 —— 翻页 / 刷新走 cache-miss 分支时会
        `destroy()` 掉 `_list_frame` 的全部子控件，而 zip 的取图还在线程池里。
        此时 `icon_frame.winfo_children()` 抛
        ``TclError: bad window path name``（实测复现见 `poc/_probe_d94_page_cache.py`），
        它抛在 `after` 回调里 → 走 Tk 的 report_callback_exception（用户看到控制台堆栈），
        **并且同一函数后面的 `_on_thumbnail_done()` 永远执行不到**，缩略图进度标签
        就永久停在 x/y。控件已经不在了就是"没什么可画"，直接返回即可。
        """
        if not self.winfo_exists():
            return
        try:
            children = icon_frame.winfo_children()
        except Exception:  # noqa: BLE001 - 目标控件已被销毁（翻页/换代）
            logger.debug("缩略图目标控件已销毁，跳过绘制")
            return
        for w in children:
            w.destroy()
        try:
            import base64
            from io import BytesIO

            from PIL import Image

            img_data = base64.b64decode(base64_data)
            img = Image.open(BytesIO(img_data))
            photo = ctk.CTkImage(img, size=(size, size))
            icon_label = ctk.CTkLabel(icon_frame, image=photo, text="")
            icon_label.pack(fill=ctk.BOTH, expand=True)
        except Exception:
            pass

    #: 缩略图解压的并发上限（原实现是"每个 zip 一个线程"，大型整合包会瞬间上百）
    #: 值来自 ``services/resource_service.py`` 的 ``THUMB_WORKERS`` —— **不要在这里
    #: 写字面量 4**，D-92 的上限只保留一个真源（tests 会钉住它等于 4）。
    _THUMB_WORKERS = THUMB_WORKERS

    def _thumb_runner(self) -> TaskRunner:
        """惰性创建缩略图线程池（有界 + daemon）。

        阶段 1.22 修正（D-92）：``TaskRunner`` 的工作线程是 daemon，即使不显式
        回收也不会拖住启动器退出；并发数有上限，直接消灭线程风暴。
        """
        if self._thumb_pool is None:
            self._thumb_pool = TaskRunner(max_workers=self._THUMB_WORKERS, name="rm-thumb")
        return self._thumb_pool

    def _load_thumbnail_async(self, icon_frame: ctk.CTkFrame, item: Dict, size: int):
        """异步加载资源包/光影的预览缩略图（有界并发 + 去重 + 代际校验）。

        阶段 1.22 修正（D-92）。原实现的四个问题与现在的做法：

        ==================  ====================================================
        原实现               现在
        ==================  ====================================================
        每个 zip 起一个线程   交给 ``TaskRunner`` 的有界池（``_THUMB_WORKERS``）
        无去重               按 zip 路径去重（``_thumb_inflight``）
        无代际校验           带 ``_thumb_generation``，过期结果不碰界面
        worker 写共享字典     缓存写入回到主线程执行
        ==================  ====================================================
        """
        zip_path = Path(item["path"])
        service = _get_resource_service(self)
        key = service.claim_thumbnail(self._thumb_inflight, zip_path)
        if key is None:
            return
        generation = self._thumb_generation

        def _load(ctx) -> Optional[str]:
            # 取消检查属于 TaskContext 协议（调度层），留在界面侧
            ctx.raise_if_cancelled()
            return service.load_thumbnail(zip_path, max_size=size)

        def _loaded(thumbnail) -> None:
            # on_done 在没有 scheduler 的情况下是在 worker 线程上执行的，
            # 所以这里显式切回 Tk 主线程 —— 与改造前的线程语义保持一致。
            self.after(
                0, lambda: self._apply_thumbnail(generation, key, icon_frame, item, thumbnail, size)
            )

        def _failed(exc) -> None:
            logger.debug(f"缩略图加载失败 {zip_path.name}: {exc}")
            self.after(0, lambda: self._apply_thumbnail(generation, key, icon_frame, item, None, size))

        self._thumb_runner().submit(
            _load,
            name=f"thumb:{zip_path.name}",
            pass_context=True,
            on_done=_loaded,
            on_error=_failed,
        )

    def _apply_thumbnail(
        self,
        generation: int,
        key: str,
        icon_frame: ctk.CTkFrame,
        item: Dict,
        thumbnail: Optional[str],
        size: int,
    ) -> None:
        """在主线程应用缩略图结果（只能由 ``after`` 调度进来）。

        两件事分开处理：

        - **缓存写入不受代际约束**：某个 zip 的缩略图不会因为列表被重渲染而失效，
          所以总是写缓存，避免下次重新解压。
        - **界面更新与进度计数受代际约束**：换代后旧的 ``icon_frame`` 已被销毁，
          再去操作它会抛 TclError；旧结果也不该抬高新代际的计数器。
        """
        service = _get_resource_service(self)
        service.release_thumbnail(self._thumb_inflight, key)
        if thumbnail:
            item["_thumbnail"] = thumbnail

        if not service.thumbnail_is_current(generation, self._thumb_generation):
            return  # 列表已重新加载：结果已进缓存，界面交给新的那一批
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return

        if thumbnail:
            self._set_thumbnail_icon(icon_frame, thumbnail, size)
        self._on_thumbnail_done()

    def _release_thumb_pool(self) -> None:
        """回收缩略图线程池（供 ``destroy()`` 与单元测试调用）。

        池里都是 daemon 线程，不回收也不会拖住启动器退出；但主动取消能立刻
        停止对已关闭窗口的无用工作。抽成独立方法是为了能在不构造 Tk 窗口的
        情况下验证回收行为。
        """
        pool, self._thumb_pool = getattr(self, "_thumb_pool", None), None
        if pool is None:
            return
        try:
            pool.shutdown(wait=False, cancel_pending=True)
        except Exception as e:  # noqa: BLE001 - 关闭失败不应影响窗口销毁
            logger.debug(f"回收缩略图线程池失败: {e}")

    def destroy(self):
        """关闭窗口时回收缩略图线程池（阶段 1.22）。"""
        self._release_thumb_pool()
        super().destroy()

    def _on_thumbnail_done(self):
        """缩略图加载完成回调，更新进度状态"""
        if not self.winfo_exists():
            return
        total = getattr(self, "_thumbnail_total", 0)
        if total <= 0:
            return
        self._thumbnail_loaded += 1
        loaded = self._thumbnail_loaded
        current_type = self._tab_var.get()
        label = self._get_resource_label(current_type)
        count = len(self._current_items)
        if loaded >= total:
            self._set_status(_("rm_item_count", count=count, label=label))
        else:
            self._set_status(_("rm_thumbnail_progress", count=count, label=label, loaded=loaded, total=total))

    def _install_resource(self, src_path: str, resource_type: str) -> bool:
        """安装资源文件到对应目录（薄委托：复制/解压/"已存在"判定在服务层）

        目录解析用 ``lambda`` **延迟到服务层的 try 内部**求值 —— 原文那一行
        ``resource_dir = self._get_resource_dir(resource_type)`` 就在 ``try`` 里，
        提前求值会改变异常边界。
        """
        result = _get_resource_service(self).install_resource(
            src_path, resource_type, lambda: self._get_resource_dir(resource_type)
        )
        return _report_install_result(self, result)

    def _install_save(self, src: Path, saves_dir: Path) -> bool:
        """安装地图存档：支持zip自动解压和文件夹直接复制（薄委托，逐字搬进服务层）"""
        return _report_install_result(self, _get_resource_service(self).install_save(src, saves_dir))

    def _select_file_install(self):
        """通过文件选择对话框安装资源"""
        current_type = self._tab_var.get()
        ext_filter = RESOURCE_TYPES[current_type]["extensions"]

        # 构建文件类型过滤
        ext_list = " ".join(f"*{e}" for e in ext_filter)
        filetypes = [(self._get_resource_label(current_type), ext_list), ("所有文件", "*.*")]  # type: ignore[list-item]

        from tkinter import filedialog

        files = filedialog.askopenfilenames(
            title=_("rm_select_file_title", label=self._get_resource_label(current_type)), filetypes=filetypes
        )

        if not files:
            return

        installed = 0
        for f in files:
            if self._install_resource(f, current_type):
                installed += 1

        if installed > 0:
            self._set_status(_("rm_install_count", count=installed))
            self._refresh_current_list()
            show_notification("🧩", _("notify_resource_done"), str(installed), notify_type="success")
            if current_type == "mods":
                _trigger_ach("modder_first_mod", value=installed)
                _trigger_ach("modder_diy")
        else:
            self._set_status(_("rm_no_resources_installed"))

    def _open_folder(self):
        """打开当前资源类型的文件夹（薄委托：建目录 + shell 启动在服务层）"""
        current_type = self._tab_var.get()
        resource_dir = self._get_resource_dir(current_type)

        try:
            _get_resource_service(self).open_resource_folder(resource_dir)
        except Exception as e:
            logger.error(f"打开文件夹失败: {e}")
            self._set_status(_("rm_open_folder_failed", error=str(e)))

    def _delete_resource(self, path: str, name: str):
        """删除资源（薄委托：删除动作在服务层，确认弹窗/文案/刷新留界面）"""
        if not messagebox.askyesno(_("rm_delete_confirm_title"), _("rm_delete_confirm_msg", name=name)):
            return

        try:
            _get_resource_service(self).delete_path(path)
            logger.info(f"已删除: {name}")
            self._set_status(_("rm_deleted", name=name))
            self._refresh_current_list()
        except Exception as e:
            logger.error(f"删除失败: {e}")
            self._set_status(_("rm_delete_failed", error=str(e)))

    def _toggle_mod(self, path: str, is_disabled: bool):
        """启用/禁用模组"""
        from structured_logger import slog

        try:
            # 改名约定（禁用 = 追加 .disabled / 启用 = Path.with_suffix("")）在服务层
            new_name = _get_resource_service(self).toggle_mod(path, is_disabled)
            if is_disabled:
                self._set_status(_("rm_enabled_status", name=new_name))
            else:
                self._set_status(_("rm_disabled_status", name=new_name))
            self._refresh_current_list()
        except Exception as e:
            logger.error(f"切换模组状态失败: {e}")
            slog.error(
                "mod_toggle_failed",
                mod_name=Path(path).name,
                action="enable" if is_disabled else "disable",
                error=str(e)[:200],
            )
            self._set_status(_("rm_operation_failed", error=str(e)))

    def _export_mod_list(self):
        """导出当前版本模组列表为分享文本"""
        if not self._mod_metadata and self._tab_var.get() != "mods":
            self._set_status(_("mod_export_empty"))
            return

        mods = self._mod_metadata
        if not mods:
            self._set_status(_("mod_export_empty"))
            return

        # 文本生成在服务层；标题行由界面用 i18n 拼好（服务层不 import ui.i18n）
        header = f"=== {_('mod_export_header', version=self.version_id)} ===\n"
        text = _get_resource_service(self).client_mod_list_text(mods, header)

        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self._set_status(_("mod_export_copied", count=len(mods)))
            logger.info(f"已导出 {len(mods)} 个模组的列表到剪贴板")
        except Exception as e:
            logger.error(f"导出模组列表失败: {e}")
            self._set_status(_("rm_operation_failed", error=str(e)))

    def _check_mod_updates(self):
        """检查模组更新"""
        if self._update_checking:
            return

        service = _get_resource_service(self)
        game_version, mod_loader = service.update_targets(self.version_id)

        if not game_version:
            self._set_status(_("mod_update_unknown_version"))
            return

        mods_with_modid = service.updatable_mods(self._mod_metadata)

        if not mods_with_modid:
            self._set_status(_("mod_update_no_modid"))
            return

        self._update_checking = True
        self._update_info.clear()
        self._check_updates_btn.configure(
            text=_("mod_checking_updates"), state=ctk.DISABLED, fg_color=COLORS["bg_light"]
        )
        self._set_status(_("mod_checking_updates_progress", current=0, total=len(mods_with_modid)))

        def _do_check():
            service = _get_resource_service(self)
            total = len(mods_with_modid)

            def _on_progress(current, prog_total):
                # 仍在服务层的锁内被调用；这里 after 回主线程并把值绑进默认参数
                self.after(
                    0,
                    lambda c=current, t=prog_total: self._set_status(
                        _("mod_checking_updates_progress", current=c, total=t)
                    ),
                )

            def _on_update(modid, info):
                self._update_info[modid] = info

            try:
                # 双源检查的并发度(8)、进度时机、结果字典键都在服务层
                updates_found = service.check_updates(
                    mods_with_modid,
                    game_version,
                    mod_loader,
                    on_progress=_on_progress,
                    on_update=_on_update,
                )

                self.after(0, lambda: self._on_update_check_done(updates_found, total))

            except Exception as e:
                logger.error(f"检查模组更新失败: {e}")
                self.after(0, lambda err=str(e): self._on_update_check_error(err))

        thread = threading.Thread(target=_do_check, daemon=True)
        thread.start()

    def _on_update_check_done(self, updates_found: int, total: int):
        """更新检查完成回调"""
        if not self.winfo_exists():
            return
        self._update_checking = False
        self._check_updates_btn.configure(text=_("mod_check_updates"), state=ctk.NORMAL, fg_color=COLORS["success"])

        if updates_found > 0:
            self._set_status(_("mod_updates_available", count=updates_found))
            self._show_update_dialog()
        else:
            self._set_status(_("mod_up_to_date", total=total))

    def _on_update_check_error(self, error: str):
        """更新检查错误回调"""
        if not self.winfo_exists():
            return
        self._update_checking = False
        self._check_updates_btn.configure(text=_("mod_check_updates"), state=ctk.NORMAL, fg_color=COLORS["success"])
        self._set_status(_("mod_update_check_failed", error=error))

    def _show_update_dialog(self):
        """显示更新选择弹窗，用户勾选要更新的模组后一键批量更新"""
        if not self._update_info:
            return

        dialog = ctk.CTkToplevel(self)
        self._fix_customtkinter_icon(dialog)
        dialog.title(_("mod_update_dialog_title"))
        dialog.geometry("580x480")
        dialog.minsize(480, 360)
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        dialog.update_idletasks()
        pw = self.winfo_width()
        ph = self.winfo_height()
        px = self.winfo_x()
        py = self.winfo_y()
        dw, dh = 580, 480
        x = px + (pw - dw) // 2
        y = py + (ph - dh) // 2
        dialog.geometry(f"{dw}x{dh}+{x}+{y}")

        main = ctk.CTkFrame(dialog, fg_color="transparent")
        main.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        header = ctk.CTkLabel(
            main,
            text=_("mod_update_dialog_header", count=len(self._update_info)),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        header.pack(anchor=ctk.W, pady=(0, 5))

        hint = ctk.CTkLabel(
            main,
            text=_("mod_update_dialog_hint"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        hint.pack(anchor=ctk.W, pady=(0, 8))

        list_frame = ctk.CTkScrollableFrame(
            main, fg_color=COLORS["card_bg"], corner_radius=8, scrollbar_button_color=COLORS["bg_light"]
        )
        list_frame.pack(fill=ctk.BOTH, expand=True, pady=(0, 5))

        checkbox_vars: Dict[str, ctk.BooleanVar] = {}

        sorted_items = sorted(self._update_info.items(), key=lambda x: x[1].get("mod_name", x[0]))
        page_size = 10
        current_page = [1]
        # 页数走服务：总数取的是**本地缓存条数** len(sorted_items)，不是后端 total_hits
        total_pages = _get_resource_service(self).total_pages(len(sorted_items), page_size)

        def _render_dialog_page():
            for w in list_frame.winfo_children():
                w.destroy()

            start = (current_page[0] - 1) * page_size
            end = min(start + page_size, len(sorted_items))
            page_items = sorted_items[start:end]

            for modid, info in page_items:
                row = ctk.CTkFrame(list_frame, fg_color=COLORS["bg_medium"], corner_radius=6, height=36)
                row.pack(fill=ctk.X, pady=2, padx=2)
                row.pack_propagate(False)

                if modid not in checkbox_vars:
                    checkbox_vars[modid] = ctk.BooleanVar(value=True)
                var = checkbox_vars[modid]

                cb = ctk.CTkCheckBox(
                    row,
                    text="",
                    variable=var,
                    width=22,
                    height=22,
                    fg_color=COLORS["accent"],
                    hover_color=COLORS["accent_hover"],
                    border_color=COLORS["card_border"],
                    checkmark_color=COLORS["text_primary"],
                )
                cb.pack(side=ctk.LEFT, padx=(8, 4), pady=6)

                name_label = ctk.CTkLabel(
                    row,
                    text=info["mod_name"],
                    font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
                    text_color=COLORS["text_primary"],
                    anchor=ctk.W,
                )
                name_label.pack(side=ctk.LEFT, padx=(0, 8))

                ver_text = f"v{info['current_version']} → v{info['latest_version']}"
                ver_label = ctk.CTkLabel(
                    row, text=ver_text, font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=COLORS["success"]
                )
                ver_label.pack(side=ctk.RIGHT, padx=(0, 8))

            page_label.configure(text=_("rm_page_info", current=current_page[0], total=total_pages))
            prev_btn.configure(state=ctk.NORMAL if current_page[0] > 1 else ctk.DISABLED)
            next_btn.configure(state=ctk.NORMAL if current_page[0] < total_pages else ctk.DISABLED)

        def _on_dialog_prev():
            if current_page[0] > 1:
                current_page[0] -= 1
                _render_dialog_page()

        def _on_dialog_next():
            if current_page[0] < total_pages:
                current_page[0] += 1
                _render_dialog_page()

        # 分页控件
        page_frame = ctk.CTkFrame(main, fg_color="transparent", height=32)
        page_frame.pack(fill=ctk.X, pady=(0, 8))
        page_frame.pack_propagate(False)

        prev_btn = ctk.CTkButton(
            page_frame,
            text=_("rm_page_prev"),
            width=80,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            state=ctk.DISABLED,
            command=_on_dialog_prev,
        )
        prev_btn.pack(side=ctk.LEFT)

        page_label = ctk.CTkLabel(
            page_frame,
            text="1 / 1",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            width=80,
        )
        page_label.pack(side=ctk.LEFT, padx=8)

        next_btn = ctk.CTkButton(
            page_frame,
            text=_("rm_page_next"),
            width=80,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            state=ctk.DISABLED if total_pages <= 1 else ctk.NORMAL,
            command=_on_dialog_next,
        )
        next_btn.pack(side=ctk.LEFT)

        _render_dialog_page()

        actions = ctk.CTkFrame(main, fg_color="transparent", height=38)
        actions.pack(fill=ctk.X)
        actions.pack_propagate(False)

        cancel_btn = ctk.CTkButton(
            actions,
            text=_("mod_update_cancel"),
            width=90,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=dialog.destroy,
        )
        cancel_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        update_all_btn = ctk.CTkButton(
            actions,
            text=_("mod_update_all"),
            width=130,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            text_color=COLORS["text_primary"],
            command=lambda: self._batch_update_mods(list(self._update_info.keys()), checkbox_vars, dialog),
        )
        update_all_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        update_selected_btn = ctk.CTkButton(
            actions,
            text=_("mod_update_selected"),
            width=130,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            text_color=COLORS["text_primary"],
            command=lambda: self._batch_update_mods(
                [mid for mid, v in checkbox_vars.items() if v.get()], checkbox_vars, dialog
            ),
        )
        update_selected_btn.pack(side=ctk.RIGHT, padx=(5, 0))

    def _batch_update_mods(self, modids: list, checkbox_vars: dict, dialog):
        """批量更新选中的模组（多线程下载）"""
        if not modids:
            return

        dialog.destroy()
        self._set_status(_("mod_update_batch_starting", count=len(modids)))

        service = _get_resource_service(self)
        game_version, mod_loader = service.update_targets(self.version_id)

        if not game_version or not mod_loader:
            self._set_status(_("mod_update_failed", error=_("mod_browser_unknown_loader")))
            return

        def _on_progress(done, total):
            self.after(
                0, lambda d=done, t=total: self._set_status(_("mod_update_batch_progress", done=d, total=t))
            )

        def _run_batch():
            # 并发度(8)、单模组失败聚合、旧文件删除时机都在服务层
            # （服务内的 ThreadPoolExecutor 在一次同步调用里建池并 join 完）
            success_count, fail_count = service.batch_update_mods(
                modids, self._update_info, game_version, mod_loader, on_progress=_on_progress
            )

            self.after(0, lambda: self._on_batch_done(success_count, fail_count))

        thread = threading.Thread(target=_run_batch, daemon=True)
        thread.start()

    def _on_batch_done(self, success: int, failed: int):
        """批量更新完成回调"""
        if not self.winfo_exists():
            return
        if failed == 0:
            self._set_status(_("mod_update_batch_done", success=success))
            show_notification("🧩", _("notify_resource_done"), str(success), notify_type="success")
        else:
            self._set_status(_("mod_update_batch_done_partial", success=success, failed=failed))
            show_notification("🧩", _("notify_resource_partial", success=success, failed=failed), notify_type="warning")
        self._update_info.clear()
        self.after(200, self._refresh_current_list)

    @staticmethod
    def _fix_customtkinter_icon(toplevel):
        """修复 CTkToplevel 因内置图标延迟回调崩溃的问题

        CTkToplevel.__init__ 内会在 after(200, ...) 中设置内置图标，
        部分环境下该路径无法被 tk 解析，抛出 TclError。
        此处 monkey-patch iconbitmap，吞掉异常，再设置正确的图标。
        """
        import types

        try:
            icon_path = Path(__file__).parent.parent.parent / "icon.ico"
            icon_str = str(icon_path) if icon_path.exists() else ""
        except Exception:
            icon_str = ""

        original = toplevel.iconbitmap

        def safe_iconbitmap(_self, bitmap=None, default=None):
            try:
                if bitmap is not None:
                    return original(bitmap=bitmap)
                if default is not None:
                    return original(default=default)
                return original()
            except Exception:
                pass

        toplevel.iconbitmap = types.MethodType(safe_iconbitmap, toplevel)

        if icon_str:
            try:
                original(bitmap=icon_str)
            except Exception:
                pass

    def _set_status(self, text: str):
        """更新状态栏"""
        try:
            if self.winfo_exists():
                self._status_label.configure(text=text)
        except Exception:
            pass
