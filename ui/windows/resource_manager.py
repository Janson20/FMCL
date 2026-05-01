"""资源管理窗口 - 模组/资源包/地图/光影管理"""
import os
import logging
import threading
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY, RESOURCE_TYPES
from ui.i18n import _

try:
    from tkinterdnd2 import DND_FILES
    HAS_DND: bool = True
except ImportError:
    HAS_DND = False


class ResourceManagerWindow(ctk.CTkToplevel):
    """资源管理窗口 - 模组/资源包/地图/光影管理"""

    def __init__(self, parent, version_id: str, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self.version_id = version_id
        self.callbacks = callbacks

        self.title(f"资源管理 - {version_id}")
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
        self._mod_search_text: str = ""

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

    def _build_ui(self):
        """构建界面"""
        # 主容器
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        # 标题
        title_label = ctk.CTkLabel(
            main_frame,
            text=f"📁 {self.version_id} - 资源管理",
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
            label_text: str = rconf["label"]
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
            text=RESOURCE_TYPES["mods"]["description"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._drag_hint_label.pack(side=ctk.LEFT)

        # 打开文件夹 + 选择文件安装 按钮
        self._open_folder_btn = ctk.CTkButton(
            top_bar,
            text="📂 打开文件夹",
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
            text="➕ 选择文件安装",
            width=130,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._select_file_install,
        )
        self._add_file_btn.pack(side=ctk.RIGHT)

        # 分割线
        ctk.CTkFrame(content_frame, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(0, 5)
        )

        # 模组搜索栏（仅模组标签页可见）
        self._search_frame = ctk.CTkFrame(content_frame, fg_color="transparent")
        self._search_entry = ctk.CTkEntry(
            self._search_frame,
            placeholder_text=_("mod_search_placeholder"),
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
        )
        self._search_entry.pack(fill=ctk.X, padx=12, pady=(0, 5))

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
            text="将文件拖拽到此处\n或点击「选择文件安装」",
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

        # 底部状态栏
        self._status_label = ctk.CTkLabel(
            main_frame,
            text="就绪",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._status_label.pack(anchor=ctk.W, pady=(5, 0))

    def _register_dnd(self):
        """注册拖拽支持"""
        if not HAS_DND:
            return
        try:
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
            self._set_status(f"成功安装 {installed} 个资源")
            self._refresh_current_list()
        else:
            self._set_status("没有可安装的文件（请检查文件格式）")

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
        self._drag_hint_label.configure(text=RESOURCE_TYPES[tab_name]["description"])

        # 模组标签页显示搜索栏，其他标签页隐藏
        if tab_name == "mods":
            self._search_frame.pack(before=self._drop_frame, fill=ctk.X, padx=0, pady=(0, 0))
            self._search_entry.delete(0, "end")
            self._mod_search_text = ""
            self._search_entry.bind("<KeyRelease>", self._on_mod_search)
            self._search_entry.bind("<Return>", self._on_mod_search)
        else:
            self._search_entry.unbind("<KeyRelease>")
            self._search_entry.unbind("<Return>")
            self._search_frame.pack_forget()

        # 隐藏加载中标签
        self._loading_label.pack_forget()

        self._refresh_current_list()

    def _refresh_current_list(self):
        """刷新当前标签页的资源列表"""
        current_type = self._tab_var.get()
        resource_dir = self._get_resource_dir(current_type)

        logger.info(f"刷新资源列表: type={current_type}, dir={resource_dir}, exists={resource_dir.exists()}")

        # 先隐藏两个区域
        self._empty_label.pack_forget()
        self._list_frame.pack_forget()
        self._loading_label.pack_forget()

        # 清空列表
        for w in self._list_frame.winfo_children():
            w.destroy()

        if current_type == "mods":
            self._refresh_mod_list(resource_dir)
            return

        if not resource_dir.exists():
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._set_status(f"文件夹不存在: {resource_dir}")
            return

        # 获取资源文件列表
        items = self._scan_resources(resource_dir, current_type)
        logger.info(f"扫描到 {len(items)} 个资源")

        if not items:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._set_status(f"{RESOURCE_TYPES[current_type]['label']} 文件夹为空")
            return

        self._list_frame.pack(fill=ctk.BOTH, expand=True)

        for item in items:
            self._create_resource_item(item, current_type)

        self._set_status(f"共 {len(items)} 个{RESOURCE_TYPES[current_type]['label']}")

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
        if self._mod_loading:
            self._loading_label.configure(text=_("mod_loading_progress", done=done, total=total))

    def _on_mod_metadata_loaded(self, results: List[Dict]):
        """模组元数据加载完成回调"""
        self._mod_loading = False
        self._mod_metadata = results
        self._loading_label.pack_forget()
        self._render_mod_list()

    def _on_mod_search(self, event=None):
        """搜索模组"""
        self._mod_search_text = self._search_entry.get().strip().lower()
        self._render_mod_list()

    def _render_mod_list(self):
        """渲染模组列表"""
        # 清空列表
        for w in self._list_frame.winfo_children():
            w.destroy()
        self._empty_label.pack_forget()
        self._list_frame.pack_forget()

        if self._mod_loading:
            return

        if not self._mod_metadata:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._set_status(_("mod_folder_empty"))
            return

        # 搜索过滤
        if self._mod_search_text:
            filtered = [
                m for m in self._mod_metadata
                if self._mod_search_text in m.get("name", "").lower()
                or self._mod_search_text in m.get("modid", "").lower()
                or self._mod_search_text in m.get("author", "").lower()
                or self._mod_search_text in m.get("description", "").lower()
                or self._mod_search_text in m.get("filename", "").lower()
            ]
        else:
            filtered = self._mod_metadata

        if not filtered:
            self._empty_label.pack(fill=ctk.BOTH, expand=True)
            self._set_status(_("mod_search_no_results"))
            return

        self._list_frame.pack(fill=ctk.BOTH, expand=True)

        for item in filtered:
            self._create_mod_card(item)

        self._set_status(_("mod_list_count", count=len(filtered)))

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
                from PIL import Image, ImageTk
                img_data = base64.b64decode(icon_base64)
                img = Image.open(BytesIO(img_data))
                img = img.resize((icon_size, icon_size), Image.LANCZOS)
                photo = ImageTk.PhotoImage(img)
                icon_label = ctk.CTkLabel(icon_frame, image=photo, text="")
                icon_label.image = photo
                icon_label.pack(fill=ctk.BOTH, expand=True)
            except Exception:
                self._create_fallback_icon(icon_frame, item, icon_size)
        else:
            self._create_fallback_icon(icon_frame, item, icon_size)

        # 右侧: 信息区
        info_frame = ctk.CTkFrame(row, fg_color="transparent")
        info_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 8), pady=6)

        # 第一行: 名称
        name_text = item.get("name", item.get("filename", "???"))
        if item.get("disabled"):
            name_text += " (已禁用)"
        name_label = ctk.CTkLabel(
            info_frame,
            text=name_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_secondary"] if item.get("disabled") else COLORS["text_primary"],
            anchor=ctk.W,
        )
        name_label.pack(fill=ctk.X)

        # 第二行: 作者 + 简介
        author = item.get("author", "")
        description = item.get("description", "")
        if author or description:
            author_desc_text = ""
            if author:
                author_desc_text = author
            if description:
                if author_desc_text:
                    author_desc_text += " · "
                desc_short = description[:80] + "..." if len(description) > 80 else description
                author_desc_text += desc_short
            author_desc_label = ctk.CTkLabel(
                info_frame,
                text=author_desc_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            )
            author_desc_label.pack(fill=ctk.X, pady=(2, 0))

        # 第三行: modid + 文件名
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

        filename = item.get("filename", "")
        if filename:
            filename_label = ctk.CTkLabel(
                bottom_frame,
                text=filename,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            )
            filename_label.pack(side=ctk.LEFT, padx=(8, 0))

        # 右侧按钮区
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

        # 图标
        if item.get("disabled"):
            icon = "🔕"
        elif item.get("is_dir"):
            icon = "📁"
        elif resource_type == "mods":
            icon = "🧩"
        elif resource_type == "resourcepacks":
            icon = "🎨"
        elif resource_type == "shaderpacks":
            icon = "✨"
        else:
            icon = "📄"

        name_text = item["name"]
        if item.get("disabled"):
            name_text += " (已禁用)"
        if item.get("is_dir") and not item.get("has_level_dat"):
            name_text += " (非标准地图)"

        name_label = ctk.CTkLabel(
            row,
            text=f"  {icon} {name_text}",
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

        # 启用/禁用按钮（仅模组）
        if resource_type == "mods" and not item.get("is_dir"):
            toggle_text = "启用" if item.get("disabled") else "禁用"
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

        # 删除按钮
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
                    self._set_status(f"文件已存在: {src.name}")
                    return False
                shutil.copy2(str(src), str(dst))
                logger.info(f"资源安装成功: {src.name} -> {dst}")
                return True

        except Exception as e:
            logger.error(f"安装资源失败: {e}")
            self._set_status(f"安装失败: {e}")
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
                self._set_status(f"地图已存在: {src.name}")
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
                self._set_status(f"地图已存在: {map_name}")
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
            self._set_status(f"不支持的地图格式: {src.suffix}")
            return False

    def _select_file_install(self):
        """通过文件选择对话框安装资源"""
        current_type = self._tab_var.get()
        ext_filter = RESOURCE_TYPES[current_type]["extensions"]

        # 构建文件类型过滤
        ext_list = " ".join(f"*{e}" for e in ext_filter)
        filetypes = [(RESOURCE_TYPES[current_type]["label"], ext_list), ("所有文件", "*.*")]  # type: ignore[list-item]

        from tkinter import filedialog
        files = filedialog.askopenfilenames(
            title=f"选择{RESOURCE_TYPES[current_type]['label']}文件",
            filetypes=filetypes,
        )

        if not files:
            return

        installed = 0
        for f in files:
            if self._install_resource(f, current_type):
                installed += 1

        if installed > 0:
            self._set_status(f"成功安装 {installed} 个资源")
            self._refresh_current_list()
        else:
            self._set_status("未安装任何资源")

    def _open_folder(self):
        """打开当前资源类型的文件夹"""
        current_type = self._tab_var.get()
        resource_dir = self._get_resource_dir(current_type)
        resource_dir.mkdir(parents=True, exist_ok=True)

        try:
            os.startfile(str(resource_dir))
        except Exception as e:
            logger.error(f"打开文件夹失败: {e}")
            self._set_status(f"打开文件夹失败: {e}")

    def _delete_resource(self, path: str, name: str):
        """删除资源"""
        import shutil  # 延迟导入

        if not messagebox.askyesno("确认删除", f"确定要删除 {name} 吗？"):
            return

        try:
            p = Path(path)
            if p.is_dir():
                shutil.rmtree(str(p))
            else:
                p.unlink()
            logger.info(f"已删除: {name}")
            self._set_status(f"已删除: {name}")
            self._refresh_current_list()
        except Exception as e:
            logger.error(f"删除失败: {e}")
            self._set_status(f"删除失败: {e}")

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
                self._set_status(f"已启用: {new_path.name}")
                slog.info("mod_enabled", mod_name=mod_name, new_name=new_path.name)
            else:
                # 禁用：添加 .disabled 后缀
                new_path = Path(str(p) + ".disabled")
                p.rename(new_path)
                logger.info(f"模组已禁用: {p.name} -> {new_path.name}")
                self._set_status(f"已禁用: {new_path.name}")
                slog.info("mod_disabled", mod_name=mod_name, new_name=new_path.name)
            self._refresh_current_list()
        except Exception as e:
            logger.error(f"切换模组状态失败: {e}")
            slog.error("mod_toggle_failed", mod_name=Path(path).name, action="enable" if is_disabled else "disable", error=str(e)[:200])
            self._set_status(f"操作失败: {e}")

    def _set_status(self, text: str):
        """更新状态栏"""
        try:
            if self.winfo_exists():
                self._status_label.configure(text=text)
        except Exception:
            pass
