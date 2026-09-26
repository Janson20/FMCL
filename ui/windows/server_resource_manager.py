"""服务器资源管理窗口 - 管理服务端模组

业务逻辑已搬到 ``services/resource_service.py``（阶段 1 任务 1.8-A）：模组目录推导、
元数据提取、搜索/过滤/分页、启停（``.disabled`` 后缀）、删除/导入/导出、
双源更新检查与批量更新、"仅客户端模组"筛选。本文件只剩纯界面部分
（控件构建、``after`` 调度、``messagebox`` / ``filedialog``、通知弹窗、i18n 文案）；
受影响的方法都退化成对服务的**薄委托**，**方法名与签名保持不变**。

既有缺陷原样保留（本轮只搬家，不顺手修）：

- **D-91 / D-103 同型缺陷在本文件里不存在**（已逐处核对）：没有任何工作线程读
  控件值；分页/列表总数一律由 ``len(filtered)`` 算出，没有用后端 ``total_hits``。
- ``_do_check_updates`` 在 worker 线程里**直接写** ``self._update_info[modid]``
  （共享字典，跨线程写；主线程只在 ``_on_update_check_done`` 之后才读它，
  实际不可观测，但仍是并发写）。
- 服务端 ``_batch_update_mods`` 在**主线程同步**跑线程池并在结束后才回状态栏，
  排队中的进度 ``after`` 回调会在"完成"提示之后才被处理 → 用户最后看到的是
  "更新中... (N/N)"。
- ``_set_status`` 的失败文案是硬编码中文 f-string（未走 i18n），
  与客户端版的 ``_("rm_delete_failed", ...)`` 不一致。
- ``_batch_update_mods`` 收尾用的 ``_("mod_update_batch_done", success=..., fail=...)``
  里 ``fail=`` 在四份语言文件里**没有对应占位符**（该键只有 ``{success}``），
  失败数被静默吞掉。
"""

import base64
import json
import os
import threading
from io import BytesIO
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Any, Callable, Dict, List, Optional

import customtkinter as ctk
from logzero import logger
from PIL import Image

from app.context import current_context
from services.resource_service import ResourceService
from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _

# 拖拽支持（阶段 1 第 7 轮修正 D-104 / L-Q6）：
# 本窗口的 `rm_drop_hint` 文案一直宣称"把 .jar 拖到此处安装"，但**从来没有注册过任何拖拽目标**
# —— 用户照做没有任何反应。客户端窗口（`ui/windows/resource_manager.py`）是同一份文案、
# 也真的接了 `tkinterdnd2`，所以这里按同样的方式补齐，而不是把文案删掉了事。
# 依赖缺失时降级为"无拖拽"（与客户端窗口一致，不阻断窗口创建）。
try:
    from tkinterdnd2 import DND_FILES

    HAS_DND: bool = True
except Exception:  # noqa: BLE001 - 缺少 tkdnd 运行库时不应让窗口打不开
    HAS_DND = False


def _get_resource_service(owner: Any = None) -> ResourceService:
    """惰性取得资源服务实例。

    写成**模块级函数**与 ``ui/app_server.py`` 的 ``_get_server_service(owner)`` /
    ``ui/app_online.py`` 的 ``_get_online_service(owner)`` 保持同一形式：
    "``owner.context.try_get`` → ``owner`` 上自造并缓存"。
    ``ui/windows/resource_manager.py`` 里有一份同名同实现的副本（两个窗口各自被
    单独导入，不互相依赖）。
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


class ServerResourceManagerWindow(ctk.CTkToplevel):
    """服务器资源管理窗口 - 管理服务端模组"""

    def __init__(self, parent, version_id: str, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self._fix_customtkinter_icon(self)
        self.version_id = version_id
        self.callbacks = callbacks

        self.title(_("server_resource_manager", version=version_id))
        self.geometry("1292x600")
        self.minsize(1156, 520)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)

        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 1292, 600
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        self._mod_metadata: List[Dict] = []
        self._mod_loading: bool = False
        self._search_text: str = ""
        self._filtered_items: List[Dict] = []
        self._update_checking: bool = False
        self._update_info: Dict[str, Dict] = {}
        self._page_size: int = 10
        self._current_page: int = 1

        self._build_ui()
        # 注册拖拽支持（阶段 1 第 7 轮修正 D-104 / L-Q6）：
        # 文案「把 .jar 拖到此处安装」原先没有任何实现；这里按客户端窗口的同一方式补齐。
        # 延迟 100ms 是因为 tkdnd 需要窗口先完成映射，与客户端窗口一致。
        if HAS_DND:
            self.after(100, self._register_dnd)
        self.after(200, self._refresh_mod_list)

    def _register_dnd(self):
        """注册拖拽支持（阶段 1 第 7 轮补齐 D-104 / L-Q6）。

        与客户端 ``ResourceManagerWindow._register_dnd`` 同一套写法：
        `tkdnd` 需要显式 `package require`，注册目标同时覆盖空态提示区与列表区
        （用户把文件拖到列表上也应当有效）。
        """
        if not HAS_DND:
            return
        try:
            self.tk.call("package", "require", "tkdnd")
            for widget in (self._drop_frame, self._list_frame):
                widget.drop_target_register(DND_FILES)  # type: ignore[attr-defined]
                widget.dnd_bind("<<Drop>>", self._on_drop)  # type: ignore[attr-defined]
            logger.info("服务端模组窗口：拖拽支持已注册")
        except Exception as e:  # noqa: BLE001 - 注册失败只降级，不影响窗口其余功能
            logger.warning(f"服务端模组窗口：拖拽注册失败: {e}")

    def _on_drop(self, event):
        """拖拽放下回调：路径解析与"能不能装"的判定都薄委托到服务层。

        服务端窗口只有模组一种资源类型，所以 ``accept_drop_entry`` 固定传 ``"mods"``
        （白名单 ``.jar`` / ``.zip``，与 ``_select_file_install`` 的文件过滤器一致）。
        安装走与"选择文件安装"**同一条**服务调用，因此失败处理、日志、状态文案全部一致。
        """
        service = _get_resource_service(self)
        # tkinterdnd2 传递的路径可能用 {} 包裹且以空格分隔，必须走服务的解析器
        files = service.parse_drop_paths(event.data)
        accepted = [p for p in (f.strip() for f in files) if p and service.accept_drop_entry(p, "mods")]
        if not accepted:
            self._set_status(_("rm_no_valid_files"))
            return
        installed = service.install_server_mods(accepted, self._get_mods_dir())
        if installed > 0:
            self._set_status(_("rm_install_count", count=installed))
            self._refresh_mod_list()
        else:
            self._set_status(_("rm_no_valid_files"))

    @staticmethod
    def _fix_customtkinter_icon(toplevel):
        """修复 CTkToplevel 因内置图标延迟回调崩溃的问题"""
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

    def _get_mods_dir(self) -> Path:
        """服务端模组目录（薄委托：路径推导与建目录都在服务层）

        ⚠ 本方法**有副作用（建目录）**，原文如此，薄委托后依旧会建。
        """
        return _get_resource_service(self).server_mods_dir(self.callbacks, self.version_id)

    def _build_ui(self):
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        title_label = ctk.CTkLabel(
            main_frame,
            text=_("server_resource_manager_title", version=self.version_id),
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title_label.pack(anchor=ctk.W, pady=(0, 10))

        content_frame = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        content_frame.pack(fill=ctk.BOTH, expand=True)

        top_bar = ctk.CTkFrame(content_frame, fg_color="transparent", height=42)
        top_bar.pack(fill=ctk.X, padx=12, pady=(10, 5))
        top_bar.pack_propagate(False)

        self._drag_hint_label = ctk.CTkLabel(
            top_bar,
            text=_("server_rm_mods_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._drag_hint_label.pack(side=ctk.LEFT)

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

        self._filter_client_btn = ctk.CTkButton(
            top_bar,
            text=_("mod_filter_client_btn"),
            width=100,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["warning"],
            hover_color="#e67e22",
            command=self._filter_client_mods,
        )
        self._filter_client_btn.pack(side=ctk.RIGHT, padx=(5, 5))

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

        ctk.CTkFrame(content_frame, fg_color=COLORS["card_border"], height=1).pack(fill=ctk.X, padx=12, pady=(0, 5))

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

        self._loading_label = ctk.CTkLabel(
            content_frame,
            text=_("mod_loading_metadata"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_secondary"],
        )

        self._drop_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        self._drop_frame.pack(fill=ctk.BOTH, expand=True, padx=12, pady=(0, 10))

        self._empty_label = ctk.CTkLabel(
            self._drop_frame,
            text=_("rm_drop_hint"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            justify=ctk.CENTER,
        )

        self._list_frame = ctk.CTkScrollableFrame(
            self._drop_frame, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )

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

        self._status_label = ctk.CTkLabel(
            main_frame,
            text=_("rm_status_ready"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._status_label.pack(anchor=ctk.W, pady=(5, 0))

    def _open_folder(self):
        """打开模组目录（薄委托：目录创建与平台分支在服务层）"""
        mods_dir = self._get_mods_dir()
        _get_resource_service(self).open_server_mods_folder(mods_dir)

    def _select_file_install(self):
        mods_dir = self._get_mods_dir()
        files = filedialog.askopenfilenames(
            title=_("resource_select_file"), filetypes=[("Mod文件", "*.jar *.zip"), ("所有文件", "*.*")]
        )
        if not files:
            return

        installed = _get_resource_service(self).install_server_mods(files, mods_dir)
        if installed > 0:
            self._set_status(_("rm_install_count", count=installed))
            self._refresh_mod_list()

    def _refresh_mod_list(self):
        """刷新服务端模组列表（元数据提取薄委托到服务层）"""
        mods_dir = self._get_mods_dir()
        service = _get_resource_service(self)

        self._mod_loading = True
        self._loading_label.pack(fill=ctk.X, padx=12, pady=(5, 10))

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
        if not self.winfo_exists():
            return
        if self._mod_loading:
            self._loading_label.configure(text=_("mod_loading_progress", done=done, total=total))

    def _on_mod_metadata_loaded(self, results: List[Dict]):
        if not self.winfo_exists():
            return
        self._mod_loading = False
        self._mod_metadata = results
        self._loading_label.pack_forget()
        self._render_mod_list()

    def _on_search(self, event=None):
        self._search_text = _get_resource_service(self).normalize_search(self._search_entry.get())
        self._current_page = 1
        self._render_mod_list()

    def _render_mod_list(self):
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

        service = _get_resource_service(self)
        filtered = service.filter_mods(self._mod_metadata, self._search_text)

        self._filtered_items = filtered

        if not filtered:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._page_frame.pack_forget()
            self._set_status(_("mod_search_no_results"))
            return

        # 分页（总页数 / 页码钳制 / 页内切片）整体走服务
        page_items, self._current_page, total_pages = service.paginate(filtered, self._current_page, self._page_size)

        for w in self._list_frame.winfo_children():
            w.destroy()

        self._list_frame.pack(fill=ctk.BOTH, expand=True)
        self._page_frame.pack(fill=ctk.X, padx=12, pady=(5, 0))

        for item in page_items:
            self._create_mod_card(item)

        self._page_label.configure(text=_("rm_page_info", current=self._current_page, total=total_pages))
        self._prev_btn.configure(state=ctk.NORMAL if self._current_page > 1 else ctk.DISABLED)
        self._next_btn.configure(state=ctk.NORMAL if self._current_page < total_pages else ctk.DISABLED)
        self._set_status(
            _(
                "rm_list_count",
                count=len(filtered),
                page=self._current_page,
                total_pages=total_pages,
                label=_("resource_mods"),
            )
        )

    def _on_prev_page(self):
        if self._current_page > 1:
            self._current_page -= 1
            self._render_mod_list()

    def _on_next_page(self):
        service = _get_resource_service(self)
        total = len(self._filtered_items)
        total_pages = service.total_pages(total, self._page_size)
        if self._current_page < total_pages:
            self._current_page += 1
            self._render_mod_list()

    def _create_mod_card(self, item: Dict):
        row = ctk.CTkFrame(self._list_frame, fg_color=COLORS["bg_medium"], corner_radius=8)
        row.pack(fill=ctk.X, pady=3, padx=2)

        icon_size = 48
        icon_frame = ctk.CTkFrame(row, fg_color="transparent", width=icon_size, height=icon_size)
        icon_frame.pack(side=ctk.LEFT, padx=(8, 8), pady=8)
        icon_frame.pack_propagate(False)

        icon_base64 = item.get("icon_base64")
        if icon_base64:
            try:
                img_data = base64.b64decode(icon_base64)
                img = Image.open(BytesIO(img_data))
                photo = ctk.CTkImage(img, size=(icon_size, icon_size))
                icon_label = ctk.CTkLabel(icon_frame, image=photo, text="")
                icon_label.pack(fill=ctk.BOTH, expand=True)
            except Exception:
                self._create_placeholder_icon(icon_frame, icon_size)
        else:
            self._create_placeholder_icon(icon_frame, icon_size)

        text_frame = ctk.CTkFrame(row, fg_color="transparent")
        text_frame.pack(side=ctk.LEFT, fill=ctk.X, expand=True, pady=6)

        name = item.get("name", item.get("filename", _("mod_unknown")))
        modid = item.get("modid", "")
        author = item.get("author", "")
        description = item.get("description", "")

        name_text = name
        if modid:
            name_text += f"  ({modid})"
        ctk.CTkLabel(
            text_frame,
            text=name_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        ).pack(fill=ctk.X)

        meta_parts = []
        if author:
            meta_parts.append(author if len(author) <= 30 else author[:28] + "…")
        version = item.get("version", "")
        if version:
            meta_parts.append(version)
        if meta_parts:
            ctk.CTkLabel(
                text_frame,
                text=" | ".join(meta_parts),
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X, pady=(1, 0))

        if description:
            desc_text = description if len(description) <= 80 else description[:77] + "..."
            ctk.CTkLabel(
                text_frame,
                text=desc_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
                wraplength=300,
                justify=ctk.LEFT,
            ).pack(fill=ctk.X, pady=(1, 0))

        disabled = item.get("disabled", False)
        btn_frame = ctk.CTkFrame(row, fg_color="transparent")
        btn_frame.pack(side=ctk.RIGHT, padx=(5, 8), pady=8)

        toggle_btn = ctk.CTkButton(
            btn_frame,
            text=_("mod_enable") if disabled else _("mod_disable"),
            width=65,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["warning"] if disabled else COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=lambda i=item: self._toggle_mod(i),
        )
        toggle_btn.pack(side=ctk.TOP, pady=(0, 3))

        delete_btn = ctk.CTkButton(
            btn_frame,
            text=_("mod_delete"),
            width=65,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            command=lambda i=item: self._delete_mod(i),
        )
        delete_btn.pack(side=ctk.TOP)

        if disabled:
            row.configure(fg_color=COLORS["bg_dark"])

    def _create_placeholder_icon(self, parent, size: int):
        frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_light"], width=size, height=size, corner_radius=8)
        frame.pack(fill=ctk.BOTH, expand=True)
        frame.pack_propagate(False)
        ctk.CTkLabel(
            frame, text="🧩", font=ctk.CTkFont(size=int(size * 0.5)), text_color=COLORS["text_secondary"]
        ).pack(expand=True)

    def _toggle_mod(self, item: Dict):
        filepath = item.get("filepath", "")
        if not filepath:
            return
        src = Path(filepath)
        if not src.exists():
            return
        disabled = item.get("disabled", False)

        try:
            # 改名约定（追加 / 切掉 .disabled）在服务层；返回操作后的禁用状态
            now_disabled = _get_resource_service(self).toggle_server_mod(filepath, disabled)
            self._refresh_mod_list()
            if now_disabled:
                self._set_status(_("mod_disabled_ok"))
            else:
                self._set_status(_("mod_enabled_ok"))
        except Exception as e:
            logger.error(f"切换模组状态失败: {e}")
            self._set_status(f"切换模组状态失败: {e}")

    def _delete_mod(self, item: Dict):
        filepath = item.get("filepath", "")
        if not filepath:
            return
        name = item.get("name", item.get("filename", ""))
        if not messagebox.askyesno(_("mod_delete_confirm_title"), _("mod_delete_confirm_msg", name=name)):
            return
        try:
            _get_resource_service(self).delete_path(filepath)
            self._refresh_mod_list()
            self._set_status(_("mod_deleted_ok", name=name))
        except Exception as e:
            logger.error(f"删除模组失败: {e}")
            self._set_status(f"删除模组失败: {e}")

    def _export_mod_list(self):
        if not self._mod_metadata:
            self._set_status(_("mod_export_no_mods"))
            return
        try:
            # 文本生成在服务层；三个文案由界面用 i18n 拼好（服务层不 import ui.i18n）
            content = _get_resource_service(self).server_mod_list_text(
                self._mod_metadata,
                _("mod_export_header", version=self.version_id),
                _("mod_enabled"),
                _("mod_disabled"),
            )
            file_path = filedialog.asksaveasfilename(
                title=_("mod_export_save_title"),
                defaultextension=".txt",
                filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")],
            )
            if file_path:
                _get_resource_service(self).write_text_file(file_path, content)
                self._set_status(_("mod_export_done", path=file_path))
        except Exception as e:
            logger.error(f"导出模组列表失败: {e}")

    def _check_mod_updates(self):
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
        self._run_in_thread(self._do_check_updates, mods_with_modid, game_version, mod_loader)

    def _do_check_updates(self, mods_with_modid: List[Dict], game_version: str, mod_loader: Optional[str]):
        """串行检查每个模组的更新（编排搬到服务层；after 与线程仍由界面起）

        记录现状、疑为缺陷：本方法在 **worker 线程**里跑（``_run_in_thread``），
        下面的界面动作都走了 ``self.after(0, ...)`` —— 但 ``self._update_info``
        是共享字典，原文是在这个线程里逐个写、服务层这次改成一次性 ``update``，
        两者对主线程都不可观测（主线程只在 ``_on_update_check_done`` 之后才读）。
        """

        def _on_progress(progress, total):
            self.after(
                0, lambda p=progress, t=total: self._set_status(_("mod_checking_updates_progress", current=p, total=t))
            )

        self._update_info.update(
            _get_resource_service(self).collect_updates(
                mods_with_modid, game_version, mod_loader, on_progress=_on_progress
            )
        )

        self.after(0, self._on_update_check_done)

    def _on_update_check_done(self):
        if not self.winfo_exists():
            return
        self._update_checking = False
        self._check_updates_btn.configure(text=_("mod_check_updates"), state=ctk.NORMAL, fg_color=COLORS["success"])
        if self._update_info:
            self._show_update_dialog()
        else:
            self._set_status(_("mod_update_all_latest"))

    def _show_update_dialog(self):
        dialog = ctk.CTkToplevel(self)
        dialog.title(_("mod_update_dialog_title"))
        dialog.geometry("500x400")
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass
        dialog.configure(fg_color=COLORS["bg_dark"])

        ctk.CTkLabel(
            dialog,
            text=_("mod_update_found_count", count=len(self._update_info)),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=15, pady=(15, 10))

        scroll = ctk.CTkScrollableFrame(dialog, fg_color="transparent")
        scroll.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 10))

        checkbox_vars = {}
        for modid, info in self._update_info.items():
            frame = ctk.CTkFrame(scroll, fg_color=COLORS["bg_medium"], corner_radius=6)
            frame.pack(fill=ctk.X, pady=2, padx=2)

            var = ctk.BooleanVar(value=True)
            checkbox_vars[modid] = var
            cb = ctk.CTkCheckBox(
                frame,
                text=f"{info.get('title', '')} → {info.get('latest_version', '')}",
                variable=var,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_primary"],
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
            )
            cb.pack(padx=10, pady=6, anchor=ctk.W)

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(fill=ctk.X, padx=15, pady=(0, 15))

        ctk.CTkButton(
            btn_frame,
            text=_("mod_update_selected"),
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda: self._batch_update_mods(
                [mid for mid, var in checkbox_vars.items() if var.get()], checkbox_vars, dialog
            ),
        ).pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 5))

        ctk.CTkButton(
            btn_frame,
            text=_("close"),
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=dialog.destroy,
        ).pack(side=ctk.RIGHT)

    def _batch_update_mods(self, modids: List[str], checkbox_vars: dict, dialog):
        if not modids:
            return

        dialog.destroy()
        self._set_status(_("mod_update_batch_starting", count=len(modids)))

        service = _get_resource_service(self)
        game_version, mod_loader = service.update_targets(self.version_id)

        if not game_version or not mod_loader:
            self._set_status(_("mod_update_failed", error=_("mod_browser_unknown_loader")))
            return

        mods_dir = self._get_mods_dir()

        def _on_progress(done, total):
            self.after(0, lambda: self._set_status(_("mod_update_batch_progress", done=done, total=total)))

        # 并发度(4)、服务端专属的取版本逻辑、失败聚合都在服务层。
        # 注意：这里**仍在调用线程同步执行**（原文就是同步跑线程池的，界面会卡住，
        # 排队中的进度 after 回调要等本方法返回后才被处理）—— 记录现状、疑为缺陷，本轮不改。
        success_count, fail_count = service.server_batch_update_mods(
            modids, mods_dir, game_version, mod_loader, on_progress=_on_progress
        )

        self._refresh_mod_list()
        self._set_status(_("mod_update_batch_done", success=success_count, fail=fail_count))

    def _filter_client_mods(self):
        """使用 mod_classifier 自动识别并禁用纯客户端模组"""
        if not self._mod_metadata:
            return

        mods_dir = str(self._get_mods_dir())
        self._filter_client_btn.configure(state=ctk.DISABLED, text=_("mod_filtering"))
        self._set_status(_("mod_filtering_status"))

        def _progress(completed: int, total: int, filename: str):
            pct = int(completed / total * 100) if total > 0 else 0
            self.after(0, lambda: self._set_status(f"正在筛选模组... {pct}% ({completed}/{total})"))

        self._run_in_thread(self._do_filter_client_mods, mods_dir, _progress)

    def _do_filter_client_mods(self, mods_dir: str, progress_callback):
        """仅客户端模组识别与禁用（判定规则在 launcher/mod_classifier/，本来就零 GUI）

        记录现状、疑为缺陷：``filter_server_mods(dry_run=False)`` 会**真的改文件名**，
        而本方法跑在 worker 线程、所有反馈都靠 ``after`` 排队。
        """

        try:
            result = _get_resource_service(self).filter_client_mods(mods_dir, progress_callback=progress_callback)
            self.after(0, lambda: self._refresh_mod_list())
            self.after(0, lambda: self._set_status(f"筛选完成: {result.summary}"))
            self.after(0, lambda: self._filter_client_btn.configure(state=ctk.NORMAL, text=_("mod_filter_client_btn")))
            if result.unknown:
                names = [r["file_name"] for r in result.unknown]
                from ui.dialogs import show_notification

                self.after(
                    0,
                    lambda: show_notification(
                        "⚠", "待确认模组", f"{len(names)} 个无法确定：{', '.join(names[:5])}...", notify_type="warning"
                    ),
                )
        except Exception as e:
            logger.error(f"剔除客户端模组失败: {e}")
            err_msg = str(e)
            self.after(0, lambda msg=err_msg: self._set_status(f"筛选失败: {msg}"))
            self.after(0, lambda: self._filter_client_btn.configure(state=ctk.NORMAL, text=_("mod_filter_client_btn")))

    def _set_status(self, text: str):
        try:
            if self.winfo_exists() and hasattr(self, "_status_label") and self._status_label:
                self._status_label.configure(text=text)
        except Exception:
            pass

    def _run_in_thread(self, target, *args, **kwargs):
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
