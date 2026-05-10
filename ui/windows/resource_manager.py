"""资源管理窗口 - 模组/资源包/地图/光影管理"""
import os
import logging
import threading
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any
from tkinter import messagebox

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY, RESOURCE_TYPES
from ui.dialogs import show_notification
from ui.i18n import _

try:
    from tkinterdnd2 import DND_FILES
    HAS_DND: bool = True
except ImportError:
    HAS_DND = False


def _trigger_ach(achievement_id: str, value: int = 1, trigger_type: str = "increment"):
    try:
        from achievement_engine import get_achievement_engine
        engine = get_achievement_engine()
        if engine:
            engine.update_progress(achievement_id, value=value, trigger_type=trigger_type)
    except Exception:
        pass


def _check_ach(achievement_id: str, condition: bool):
    try:
        from achievement_engine import get_achievement_engine
        engine = get_achievement_engine()
        if engine:
            engine.check_and_unlock(achievement_id, condition)
    except Exception:
        pass


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
        self._search_text: str = ""
        self._current_items: List[Dict] = []
        self._filtered_items: List[Dict] = []
        self._update_checking: bool = False
        self._update_info: Dict[str, Dict] = {}  # modid -> {latest_version, project_id, ...}
        self._thumbnails: Dict[str, str] = {}  # path -> base64 thumbnail cache
        self._page_size: int = 10
        self._current_page: int = 1
        self._update_dialog_page: int = 1

        self._build_ui()

        # 注册拖拽支持
        if HAS_DND:
            self.after(100, self._register_dnd)

        # 加载当前标签页的资源列表
        self.after(200, self._refresh_current_list)

    def _get_minecraft_dir(self) -> Path:
        """获取当前版本的 .minecraft 目录"""
        if "get_minecraft_dir" in self.callbacks:
            return Path(self.callbacks["get_minecraft_dir"]())
        return Path(".") / ".minecraft"

    @staticmethod
    def _has_mod_loader(version_id: str) -> bool:
        """判断版本是否安装了模组加载器（需要版本隔离）"""
        v = version_id.lower()
        return any(loader in v for loader in ("forge", "fabric", "neoforge"))

    def _get_resource_dir(self, resource_type: str) -> Path:
        """获取指定资源类型的目录，仅模组加载器版本使用版本隔离目录"""
        mc_dir = self._get_minecraft_dir()
        folder_name: str = RESOURCE_TYPES[resource_type]["folder"]

        # 版本隔离：仅当版本安装了模组加载器时，才使用隔离目录
        # 原版客户端虽然 versions/{版本名}/ 也存在（含 jar/json），
        # 但启动时未设置 gameDirectory，游戏资源（saves 等）仍在全局目录
        if self._has_mod_loader(self.version_id):
            version_dir = mc_dir / "versions" / self.version_id / folder_name
            logger.info(f"使用版本隔离目录: {version_dir}")
            return version_dir

        # 回退：全局 .minecraft/{folder}/
        global_dir = mc_dir / folder_name
        logger.info(f"使用全局目录: {global_dir}")
        return global_dir

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
        ctk.CTkFrame(content_frame, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(0, 5)
        )

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
            self._drop_frame,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
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
        """拖拽文件放下回调"""
        # tkinterdnd2 传递的路径可能用 {} 包裹且以空格分隔
        raw = event.data
        # 处理 Windows 路径格式
        if raw.startswith("{"):
            files = []
            i = 0
            while i < len(raw):
                if raw[i] == "{":
                    end = raw.index("}", i)
                    files.append(raw[i + 1:end])
                    i = end + 2
                else:
                    parts = raw[i:].split()
                    files.extend(parts)
                    break
        else:
            files = raw.split()

        current_type = self._tab_var.get()
        ext_filter = RESOURCE_TYPES[current_type]["extensions"]

        installed = 0
        for fpath in files:
            fpath = fpath.strip()
            if not fpath:
                continue
            p = Path(fpath)
            if p.exists() and p.suffix.lower() in ext_filter:
                if self._install_resource(fpath, current_type):
                    installed += 1
            elif p.exists() and p.is_dir() and current_type == "saves":
                # 地图存档可能是文件夹
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

        # 隐藏加载中标签
        self._loading_label.pack_forget()

        self._refresh_current_list()

    def _refresh_current_list(self):
        """刷新当前标签页的资源列表"""
        current_type = self._tab_var.get()
        resource_dir = self._get_resource_dir(current_type)

        logger.info(f"刷新资源列表: type={current_type}, dir={resource_dir}, exists={resource_dir.exists()}")

        # 重置分页
        self._current_page = 1

        # 先隐藏两个区域
        self._empty_label.pack_forget()
        self._list_frame.pack_forget()
        self._loading_label.pack_forget()
        self._page_frame.pack_forget()

        # 清空列表
        for w in self._list_frame.winfo_children():
            w.destroy()

        if current_type == "mods":
            self._refresh_mod_list(resource_dir)
            return

        if not resource_dir.exists():
            self._current_items = []
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._set_status(_("rm_folder_not_exist", path=str(resource_dir)))
            return

        # 获取资源文件列表（同步扫描，速度快）
        items = self._scan_resources(resource_dir, current_type)
        logger.info(f"扫描到 {len(items)} 个资源")

        self._current_items = items

        if not items:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._set_status(_("rm_folder_empty", label=self._get_resource_label(current_type)))
            return

        # 缩略图进度跟踪（仅资源包和光影标签页）
        self._thumbnail_loaded = 0
        self._thumbnail_total = 0
        zip_items = []
        for item in items:
            if current_type in ("resourcepacks", "shaderpacks") and not item.get("is_dir"):
                ext = Path(item["path"]).suffix.lower()
                if ext in (".zip",) and not item.get("_thumbnail"):
                    zip_items.append(item)

        if zip_items:
            self._thumbnail_total = len(zip_items)

        self._render_filtered_list(current_type)

    def _refresh_mod_list(self, mods_dir: Path):
        """刷新模组列表（含元数据提取）"""
        from modrinth import extract_all_mods_metadata

        self._mod_loading = True
        self._loading_label.pack(before=self._drop_frame, fill=ctk.X, padx=12, pady=(5, 10))

        def _load_metadata():
            try:
                results = extract_all_mods_metadata(
                    mods_dir,
                    status_callback=lambda done, total: self.after(0, lambda d=done, t=total: self._update_mod_loading(d, t)),
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
        """检查是否已安装所有资源类型（全家福成就）"""
        try:
            has_mods = bool(self._mod_metadata)
            has_resourcepacks = self._dir_has_content(self._get_resource_dir("resourcepacks"))
            has_saves = self._dir_has_content(self._get_resource_dir("saves"))
            has_shaders = self._dir_has_content(self._get_resource_dir("shaderpacks"))
            has_datapacks = self._dir_has_content(self._get_resource_dir("datapacks"))
            full_house = has_mods and has_resourcepacks and has_saves and has_shaders and has_datapacks
            _check_ach("modder_full_house", full_house)
        except Exception:
            pass

    @staticmethod
    def _dir_has_content(directory: Path) -> bool:
        try:
            if not directory.exists():
                return False
            return any(directory.iterdir())
        except Exception:
            return False

    def _on_search(self, event=None):
        """搜索过滤"""
        self._search_text = self._search_entry.get().strip().lower()
        self._current_page = 1
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

        # 搜索过滤
        if self._search_text:
            filtered = [
                m for m in self._mod_metadata
                if self._search_text in m.get("name", "").lower()
                or self._search_text in m.get("modid", "").lower()
                or self._search_text in m.get("author", "").lower()
                or self._search_text in m.get("description", "").lower()
                or self._search_text in m.get("filename", "").lower()
            ]
        else:
            filtered = self._mod_metadata

        self._filtered_items = filtered

        if not filtered:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._page_frame.pack_forget()
            self._set_status(_("mod_search_no_results"))
            return

        total_pages = max(1, (len(filtered) + self._page_size - 1) // self._page_size)
        if self._current_page > total_pages:
            self._current_page = total_pages

        self._render_current_page()

    def _render_filtered_list(self, resource_type: str):
        """渲染非模组标签页的过滤列表"""
        self._list_frame.pack_forget()
        self._empty_label.pack_forget()

        if self._search_text:
            self._filtered_items = [
                item for item in self._current_items
                if self._search_text in item.get("name", "").lower()
            ]
        else:
            self._filtered_items = self._current_items

        if not self._filtered_items:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._page_frame.pack_forget()
            self._set_status(_("rm_search_no_results"))
            return

        total_pages = max(1, (len(self._filtered_items) + self._page_size - 1) // self._page_size)
        if self._current_page > total_pages:
            self._current_page = total_pages

        self._render_current_page()

    def _render_current_page(self):
        """渲染当前分页的资源列表"""
        for w in self._list_frame.winfo_children():
            w.destroy()

        total = len(self._filtered_items)
        total_pages = max(1, (total + self._page_size - 1) // self._page_size)

        start = (self._current_page - 1) * self._page_size
        end = min(start + self._page_size, total)
        page_items = self._filtered_items[start:end]

        self._list_frame.pack(fill=ctk.BOTH, expand=True)
        self._page_frame.pack(fill=ctk.X, padx=12, pady=(5, 0))

        current_type = self._tab_var.get()

        if current_type == "mods":
            for item in page_items:
                self._create_mod_card(item)
        else:
            for item in page_items:
                self._create_resource_item(item, current_type)

        # 更新分页控件状态
        self._page_label.configure(text=_("rm_page_info", current=self._current_page, total=total_pages))
        self._prev_btn.configure(state=ctk.NORMAL if self._current_page > 1 else ctk.DISABLED)
        self._next_btn.configure(state=ctk.NORMAL if self._current_page < total_pages else ctk.DISABLED)

        label = self._get_resource_label(current_type)
        self._set_status(_("rm_list_count", count=total, page=self._current_page, total_pages=total_pages, label=label))

    def _update_pagination(self):
        """更新分页控件状态"""
        total = len(self._filtered_items)
        total_pages = max(1, (total + self._page_size - 1) // self._page_size)
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
        total = len(self._filtered_items)
        total_pages = max(1, (total + self._page_size - 1) // self._page_size)
        if self._current_page < total_pages:
            self._current_page += 1
            self._render_current_page()

    def _create_mod_card(self, item: Dict):
        """创建模组卡片"""
        row = ctk.CTkFrame(
            self._list_frame,
            fg_color=COLORS["bg_medium"],
            corner_radius=8,
        )
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
            parent,
            text=icon_text,
            font=ctk.CTkFont(size=size // 2),
            text_color=COLORS["text_secondary"],
        )
        icon_label.pack(fill=ctk.BOTH, expand=True)

    def _scan_resources(self, resource_dir: Path, resource_type: str) -> List[Dict]:
        """扫描资源目录"""
        items = []
        try:
            entries = list(resource_dir.iterdir())
            logger.info(f"目录 {resource_dir} 共有 {len(entries)} 个条目")
            if resource_type == "saves":
                # 地图是文件夹
                for entry in sorted(entries):
                    if entry.is_dir() and not entry.name.startswith("."):
                        # 检查是否是有效的地图存档
                        level_dat = entry / "level.dat"
                        items.append({
                            "name": entry.name,
                            "path": str(entry),
                            "is_dir": True,
                            "has_level_dat": level_dat.exists(),
                        })
            else:
                # 模组/资源包/光影是文件
                ext_filter = RESOURCE_TYPES[resource_type]["extensions"]
                for entry in sorted(entries):
                    if not entry.is_file():
                        continue
                    # 检查文件扩展名：支持 .jar 和 .jar.disabled 等格式
                    is_disabled = entry.suffix.lower() == ".disabled"
                    actual_ext = entry.suffixes[-2].lower() if is_disabled and len(entry.suffixes) >= 2 else entry.suffix.lower()
                    if actual_ext in ext_filter or entry.suffix.lower() in ext_filter:
                        # 文件大小
                        try:
                            size = entry.stat().st_size
                            size_str = self._format_size(size)
                        except Exception:
                            size_str = "?"
                        items.append({
                            "name": entry.name,
                            "path": str(entry),
                            "is_dir": False,
                            "size": size_str,
                            "disabled": is_disabled,
                        })
        except Exception as e:
            logger.error(f"扫描资源目录失败: {e}")

        return items

    def _format_size(self, size: int) -> str:
        """格式化文件大小"""
        if size < 1024:
            return f"{size} B"
        elif size < 1024 * 1024:
            return f"{size / 1024:.1f} KB"
        else:
            return f"{size / (1024 * 1024):.1f} MB"

    def _create_resource_item(self, item: Dict, resource_type: str):
        """创建资源列表项"""
        row = ctk.CTkFrame(
            self._list_frame,
            fg_color=COLORS["bg_medium"],
            corner_radius=6,
            height=36,
        )
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
                icon_frame,
                text=icon_text,
                font=ctk.CTkFont(size=14),
                text_color=COLORS["text_secondary"],
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
        """设置缩略图图标（会先清除已有的子控件，避免与 emoji 图标重叠）"""
        if not self.winfo_exists():
            return
        for w in icon_frame.winfo_children():
            w.destroy()
        try:
            from io import BytesIO
            import base64
            from PIL import Image
            img_data = base64.b64decode(base64_data)
            img = Image.open(BytesIO(img_data))
            photo = ctk.CTkImage(img, size=(size, size))
            icon_label = ctk.CTkLabel(icon_frame, image=photo, text="")
            icon_label.pack(fill=ctk.BOTH, expand=True)
        except Exception:
            pass

    def _load_thumbnail_async(self, icon_frame: ctk.CTkFrame, item: Dict, size: int):
        """异步加载资源包/光影的预览缩略图（完成后更新进度）"""
        zip_path = Path(item["path"])

        def _load():
            try:
                from modrinth import extract_zip_thumbnail
                thumbnail = extract_zip_thumbnail(zip_path, max_size=size)
                if thumbnail:
                    item["_thumbnail"] = thumbnail
                    self.after(0, lambda: self._set_thumbnail_icon(icon_frame, thumbnail, size))
            except Exception:
                pass
            finally:
                self.after(0, self._on_thumbnail_done)

        thread = threading.Thread(target=_load, daemon=True)
        thread.start()

    def _on_thumbnail_done(self):
        """缩略图加载完成回调，更新进度状态"""
        if not self.winfo_exists():
            return
        total = getattr(self, '_thumbnail_total', 0)
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
        """安装资源文件到对应目录"""
        import shutil  # 延迟导入：仅资源管理窗口使用
        try:
            resource_dir = self._get_resource_dir(resource_type)
            resource_dir.mkdir(parents=True, exist_ok=True)

            src = Path(src_path)

            if resource_type == "saves":
                return self._install_save(src, resource_dir)
            else:
                dst = resource_dir / src.name
                if dst.exists():
                    logger.warning(f"资源已存在: {dst}")
                    self._set_status(_("rm_file_exists", name=src.name))
                    return False
                shutil.copy2(str(src), str(dst))
                logger.info(f"资源安装成功: {src.name} -> {dst}")
                return True

        except Exception as e:
            logger.error(f"安装资源失败: {e}")
            self._set_status(_("rm_install_failed", error=str(e)))
            return False

    def _install_save(self, src: Path, saves_dir: Path) -> bool:
        """安装地图存档：支持zip自动解压和文件夹直接复制"""
        import shutil  # 延迟导入
        import zipfile

        if src.is_dir():
            # 文件夹直接复制到 saves/地图名/
            dst = saves_dir / src.name
            if dst.exists():
                logger.warning(f"地图已存在: {dst}")
                self._set_status(_("rm_map_exists", name=src.name))
                return False
            shutil.copytree(str(src), str(dst))
            logger.info(f"地图安装成功(文件夹): {src.name} -> {dst}")
            return True

        elif src.suffix.lower() == ".zip":
            # zip 文件解压到 saves/地图名/ 下
            # 地图名 = zip 文件名去掉扩展名
            map_name = src.stem
            dst = saves_dir / map_name
            if dst.exists():
                logger.warning(f"地图已存在: {dst}")
                self._set_status(_("rm_map_exists", name=map_name))
                return False

            with zipfile.ZipFile(str(src), "r") as zf:
                namelist = zf.namelist()
                # 检查zip内部结构：可能是直接包含level.dat，也可能有一层包装目录
                # 情况1: zip内顶层就有 level.dat -> 解压到 saves/地图名/
                # 情况2: zip内有一个子目录包含 level.dat -> 解压该子目录到 saves/地图名/
                top_entries = [n for n in namelist if "/" not in n.rstrip("/") or n.count("/") == 0]
                has_root_level_dat = any(n == "level.dat" for n in namelist)

                if has_root_level_dat:
                    # 直接解压所有内容到 dst
                    zf.extractall(str(dst))
                else:
                    # 查找包含 level.dat 的子目录
                    level_dat_entries = [n for n in namelist if n.endswith("level.dat")]
                    if level_dat_entries:
                        # 取 level.dat 所在的子目录名
                        sub_dir = level_dat_entries[0].rsplit("level.dat", 1)[0].rstrip("/")
                        # 解压该子目录的内容到 dst
                        for member in zf.namelist():
                            if member.startswith(sub_dir + "/"):
                                # 去掉子目录前缀，提取到 dst
                                relative = member[len(sub_dir) + 1:]
                                if not relative:
                                    continue
                                target = dst / relative
                                if member.endswith("/"):
                                    target.mkdir(parents=True, exist_ok=True)
                                else:
                                    target.parent.mkdir(parents=True, exist_ok=True)
                                    with zf.open(member) as src_file:
                                        with open(str(target), "wb") as dst_file:
                                            dst_file.write(src_file.read())
                    else:
                        # 没找到 level.dat，直接全部解压
                        zf.extractall(str(dst))

            logger.info(f"地图安装成功(zip): {src.name} -> {dst}")
            return True

        else:
            logger.warning(f"不支持的地图格式: {src.suffix}")
            self._set_status(_("rm_unsupported_format", ext=src.suffix))
            return False

    def _select_file_install(self):
        """通过文件选择对话框安装资源"""
        current_type = self._tab_var.get()
        ext_filter = RESOURCE_TYPES[current_type]["extensions"]

        # 构建文件类型过滤
        ext_list = " ".join(f"*{e}" for e in ext_filter)
        filetypes = [(self._get_resource_label(current_type), ext_list), ("所有文件", "*.*")]  # type: ignore[list-item]

        from tkinter import filedialog
        files = filedialog.askopenfilenames(
            title=_("rm_select_file_title", label=self._get_resource_label(current_type)),
            filetypes=filetypes,
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
        """打开当前资源类型的文件夹"""
        current_type = self._tab_var.get()
        resource_dir = self._get_resource_dir(current_type)
        resource_dir.mkdir(parents=True, exist_ok=True)

        try:
            os.startfile(str(resource_dir))
        except Exception as e:
            logger.error(f"打开文件夹失败: {e}")
            self._set_status(_("rm_open_folder_failed", error=str(e)))

    def _delete_resource(self, path: str, name: str):
        """删除资源"""
        import shutil  # 延迟导入

        if not messagebox.askyesno(_("rm_delete_confirm_title"), _("rm_delete_confirm_msg", name=name)):
            return

        try:
            p = Path(path)
            if p.is_dir():
                shutil.rmtree(str(p))
            else:
                p.unlink()
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
            p = Path(path)
            mod_name = p.name
            if is_disabled:
                # 启用：移除 .disabled 后缀
                new_path = p.with_suffix("")
                p.rename(new_path)
                logger.info(f"模组已启用: {p.name} -> {new_path.name}")
                self._set_status(_("rm_enabled_status", name=new_path.name))
                slog.info("mod_enabled", mod_name=mod_name, new_name=new_path.name)
            else:
                # 禁用：添加 .disabled 后缀
                new_path = Path(str(p) + ".disabled")
                p.rename(new_path)
                logger.info(f"模组已禁用: {p.name} -> {new_path.name}")
                self._set_status(_("rm_disabled_status", name=new_path.name))
                slog.info("mod_disabled", mod_name=mod_name, new_name=new_path.name)
            self._refresh_current_list()
        except Exception as e:
            logger.error(f"切换模组状态失败: {e}")
            slog.error("mod_toggle_failed", mod_name=Path(path).name, action="enable" if is_disabled else "disable", error=str(e)[:200])
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

        lines = []
        lines.append(f"=== {_('mod_export_header', version=self.version_id)} ===\n")

        for i, mod in enumerate(mods, 1):
            name = mod.get("name", mod.get("filename", "???"))
            modid = mod.get("modid", "-")
            version = mod.get("version", "-")
            disabled = " [Disabled]" if mod.get("disabled") else ""
            lines.append(f"{i}. {name}{disabled}")
            lines.append(f"   modid: {modid}  |  version: {version}")

        lines.append(f"\nTotal: {len(mods)} mods")
        text = "\n".join(lines)

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

        from modrinth import parse_game_version_from_version, parse_mod_loader_from_version

        game_version = parse_game_version_from_version(self.version_id)
        mod_loader = parse_mod_loader_from_version(self.version_id)

        if not game_version:
            self._set_status(_("mod_update_unknown_version"))
            return

        mods_with_modid = [
            m for m in self._mod_metadata
            if m.get("modid") and not m.get("disabled")
        ]

        if not mods_with_modid:
            self._set_status(_("mod_update_no_modid"))
            return

        self._update_checking = True
        self._update_info.clear()
        self._check_updates_btn.configure(
            text=_("mod_checking_updates"),
            state=ctk.DISABLED,
            fg_color=COLORS["bg_light"],
        )
        self._set_status(_("mod_checking_updates_progress", current=0, total=len(mods_with_modid)))

        def _do_check():
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import threading as _threading

            try:
                from modrinth import (
                    search_project_by_slug,
                    get_project_latest_version,
                    compare_mod_versions,
                )

                lock = _threading.Lock()
                checked = [0]
                updates_found = [0]
                total = len(mods_with_modid)

                def _check_one(mod):
                    modid = mod["modid"]
                    current_version = mod.get("version", "")

                    try:
                        project = search_project_by_slug(modid)
                        if not project:
                            return None

                        project_id = project.get("id", "")
                        if not project_id:
                            return None

                        latest = get_project_latest_version(
                            project_id,
                            game_version=game_version,
                            mod_loader=mod_loader,
                        )
                        if not latest:
                            return None

                        latest_version = latest.get("version_number", "")
                        if not latest_version:
                            return None

                        result = compare_mod_versions(current_version, latest_version)
                        if result is None:
                            return None

                        if result < 0:
                            return {
                                "modid": modid,
                                "project_id": project_id,
                                "latest_version": latest_version,
                                "current_version": current_version,
                                "mod_name": mod.get("name", modid),
                                "mod_path": mod.get("path", ""),
                            }
                        return None
                    except Exception as e:
                        logger.debug(f"检查模组更新失败 ({modid}): {e}")
                        return None
                    finally:
                        with lock:
                            checked[0] += 1
                            self.after(0, lambda c=checked[0]: self._set_status(
                                _("mod_checking_updates_progress", current=c, total=total)))

                with ThreadPoolExecutor(max_workers=8) as executor:
                    futures = {executor.submit(_check_one, mod): mod for mod in mods_with_modid}
                    for future in as_completed(futures):
                        result = future.result()
                        if result:
                            updates_found[0] += 1
                            self._update_info[result["modid"]] = {
                                "project_id": result["project_id"],
                                "latest_version": result["latest_version"],
                                "current_version": result["current_version"],
                                "mod_name": result["mod_name"],
                                "mod_path": result["mod_path"],
                            }

                self.after(0, lambda: self._on_update_check_done(updates_found[0], total))

            except Exception as e:
                logger.error(f"检查模组更新失败: {e}")
                self.after(0, lambda: self._on_update_check_error(str(e)))

        thread = threading.Thread(target=_do_check, daemon=True)
        thread.start()

    def _on_update_check_done(self, updates_found: int, total: int):
        """更新检查完成回调"""
        if not self.winfo_exists():
            return
        self._update_checking = False
        self._check_updates_btn.configure(
            text=_("mod_check_updates"),
            state=ctk.NORMAL,
            fg_color=COLORS["success"],
        )

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
        self._check_updates_btn.configure(
            text=_("mod_check_updates"),
            state=ctk.NORMAL,
            fg_color=COLORS["success"],
        )
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
            main,
            fg_color=COLORS["card_bg"],
            corner_radius=8,
            scrollbar_button_color=COLORS["bg_light"],
        )
        list_frame.pack(fill=ctk.BOTH, expand=True, pady=(0, 5))

        checkbox_vars: Dict[str, ctk.BooleanVar] = {}

        sorted_items = sorted(self._update_info.items(), key=lambda x: x[1].get("mod_name", x[0]))
        page_size = 10
        current_page = [1]
        total_pages = max(1, (len(sorted_items) + page_size - 1) // page_size)

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
                    row,
                    text=ver_text,
                    font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                    text_color=COLORS["success"],
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
            command=lambda: self._batch_update_mods(
                list(self._update_info.keys()), checkbox_vars, dialog
            ),
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
                [mid for mid, v in checkbox_vars.items() if v.get()],
                checkbox_vars, dialog,
            ),
        )
        update_selected_btn.pack(side=ctk.RIGHT, padx=(5, 0))

    def _batch_update_mods(self, modids: list, checkbox_vars: dict, dialog):
        """批量更新选中的模组（多线程下载）"""
        if not modids:
            return

        dialog.destroy()
        self._set_status(_("mod_update_batch_starting", count=len(modids)))

        from modrinth import parse_game_version_from_version, parse_mod_loader_from_version
        from modrinth import download_mod, get_mod_versions
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import threading as _threading

        game_version = parse_game_version_from_version(self.version_id)
        mod_loader = parse_mod_loader_from_version(self.version_id)

        if not game_version or not mod_loader:
            self._set_status(_("mod_update_failed", error=_("mod_browser_unknown_loader")))
            return

        lock = _threading.Lock()
        done = [0]
        success_count = [0]
        fail_count = [0]

        def _download_one(modid):
            info = self._update_info.get(modid)
            if not info:
                return False, modid

            project_id = info["project_id"]
            mod_name = info["mod_name"]
            mod_path = info["mod_path"]

            try:
                versions = get_mod_versions(
                    project_id,
                    game_version=game_version,
                    mod_loader=mod_loader,
                )
                if not versions:
                    return False, mod_name

                version = versions[0]
                files = version.get("files", [])
                primary = next((f for f in files if f.get("primary")), None) or (files[0] if files else None)
                if not primary:
                    return False, mod_name

                download_url = primary.get("url", "")
                filename = primary.get("filename", f"{mod_name}.jar")
                if not download_url:
                    return False, mod_name

                mods_dir = str(Path(mod_path).parent)

                # 删除旧文件
                try:
                    old_path = Path(mod_path)
                    if old_path.exists():
                        old_path.unlink()
                except Exception:
                    pass

                hashes = primary.get("hashes")
                dl_success, _dl_result = download_mod(download_url, mods_dir, filename, expected_hashes=hashes)

                if dl_success:
                    logger.info(f"模组更新完成: {mod_name} → {version.get('version_number','?')}")
                    return True, mod_name
                else:
                    return False, mod_name

            except Exception as e:
                logger.error(f"更新模组失败 ({mod_name}): {e}")
                return False, mod_name
            finally:
                with lock:
                    done[0] += 1
                    self.after(0, lambda d=done[0]: self._set_status(
                        _("mod_update_batch_progress", done=d, total=len(modids))))

        def _run_batch():
            with ThreadPoolExecutor(max_workers=8) as executor:
                futures = {executor.submit(_download_one, mid): mid for mid in modids}
                for future in as_completed(futures):
                    ok, name = future.result()
                    if ok:
                        success_count[0] += 1
                    else:
                        fail_count[0] += 1

            self.after(0, lambda: self._on_batch_done(success_count[0], fail_count[0]))

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
