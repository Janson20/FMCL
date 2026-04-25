"""Modrinth 模组浏览窗口 - 搜索、浏览并安装模组"""
import logging
import threading
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY


class ModBrowserWindow(ctk.CTkToplevel):
    """Modrinth 模组浏览窗口 - 搜索、浏览并安装模组"""

    PAGE_SIZE = 10

    def __init__(self, parent, version_id: str, callbacks: Dict[str, Callable]):
        super().__init__(parent)
        self.version_id = version_id
        self.callbacks = callbacks

        # 解析版本信息
        from modrinth import parse_mod_loader_from_version, parse_game_version_from_version
        self._mod_loader = parse_mod_loader_from_version(version_id)
        self._game_version = parse_game_version_from_version(version_id)

        # 窗口配置
        self.title(f"安装模组 - {version_id}")
        self.geometry("800x640")
        self.minsize(720, 560)
        self.configure(fg_color=COLORS["bg_dark"])
        self.transient(parent)

        # 居中
        self.update_idletasks()
        pw = parent.winfo_width()
        ph = parent.winfo_height()
        px = parent.winfo_x()
        py = parent.winfo_y()
        w, h = 800, 640
        x = px + (pw - w) // 2
        y = py + (ph - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

        # 搜索状态
        self._current_offset = 0
        self._total_hits = 0
        self._current_query = ""

        self._build_ui()

        # 窗口打开时自动加载热门模组（后台线程，避免阻塞 UI）
        self.after(300, lambda: self._run_in_thread(self._do_search))

    def _build_ui(self):
        """构建界面"""
        main_frame = ctk.CTkFrame(self, fg_color="transparent")
        main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        # ── 顶部标题与筛选区 ──
        header = ctk.CTkFrame(main_frame, fg_color="transparent")
        header.pack(fill=ctk.X, pady=(0, 10))

        ctk.CTkLabel(
            header,
            text=f"🧩 安装模组 - {self.version_id}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=18, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        # 版本/加载器信息标签
        info_parts = []
        if self._game_version:
            info_parts.append(f"MC {self._game_version}")
        if self._mod_loader:
            info_parts.append(self._mod_loader.capitalize())
        info_text = " | ".join(info_parts) if info_parts else "未识别版本信息"
        info_color = COLORS["success"] if info_parts else COLORS["warning"]
        ctk.CTkLabel(
            header,
            text=info_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=info_color,
        ).pack(side=ctk.RIGHT)

        # ── 搜索栏 ──
        search_frame = ctk.CTkFrame(main_frame, fg_color="transparent", height=40)
        search_frame.pack(fill=ctk.X, pady=(0, 8))
        search_frame.pack_propagate(False)

        self._search_entry = ctk.CTkEntry(
            search_frame,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="🔍 搜索模组...",
        )
        self._search_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._search_entry.bind("<Return>", lambda e: self._on_search())

        ctk.CTkButton(
            search_frame,
            text="搜索",
            width=80,
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_search,
        ).pack(side=ctk.LEFT)

        # ── 模组列表 ──
        list_container = ctk.CTkFrame(main_frame, fg_color=COLORS["card_bg"], corner_radius=10)
        list_container.pack(fill=ctk.BOTH, expand=True, pady=(0, 8))

        self._mod_list_frame = ctk.CTkScrollableFrame(
            list_container,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        self._mod_list_frame.pack(fill=ctk.BOTH, expand=True, padx=5, pady=5)

        # 加载中占位
        self._loading_label = ctk.CTkLabel(
            self._mod_list_frame,
            text="⏳ 加载中...",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
            justify=ctk.CENTER,
        )
        self._loading_label.pack(pady=40)

        # ── 分页控件 ──
        page_frame = ctk.CTkFrame(main_frame, fg_color="transparent", height=34)
        page_frame.pack(fill=ctk.X)
        page_frame.pack_propagate(False)

        self._prev_btn = ctk.CTkButton(
            page_frame,
            text="◀ 上一页",
            width=90,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_prev_page,
        )
        self._prev_btn.pack(side=ctk.LEFT)

        self._page_label = ctk.CTkLabel(
            page_frame,
            text="0 / 0",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            width=100,
        )
        self._page_label.pack(side=ctk.LEFT, padx=10)

        self._next_btn = ctk.CTkButton(
            page_frame,
            text="下一页 ▶",
            width=90,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_next_page,
        )
        self._next_btn.pack(side=ctk.LEFT)

        # 结果计数
        self._result_count_label = ctk.CTkLabel(
            page_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._result_count_label.pack(side=ctk.RIGHT)

        # ── 底部状态 ──
        self._status_label = ctk.CTkLabel(
            main_frame,
            text="就绪",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._status_label.pack(anchor=ctk.W, pady=(5, 0))

    def _on_search(self):
        """搜索按钮回调"""
        self._current_query = self._search_entry.get().strip()
        self._current_offset = 0
        self._set_status("正在搜索...")
        self._run_in_thread(self._do_search)

    def _do_search(self):
        """执行搜索（后台线程）"""
        from modrinth import search_mods

        try:
            result = search_mods(
                query=self._current_query,
                game_version=self._game_version,
                mod_loader=self._mod_loader,
                offset=self._current_offset,
                limit=self.PAGE_SIZE,
            )

            hits = result.get("hits", [])
            self._total_hits = result.get("total_hits", 0)

            self.after(0, self._render_results, hits)

        except Exception as e:
            logger.error(f"搜索模组失败: {e}")
            self.after(0, self._render_error, str(e))

    def _render_results(self, hits: List[Dict]):
        """渲染搜索结果（主线程）"""
        # 清空列表
        for w in self._mod_list_frame.winfo_children():
            w.destroy()

        if not hits:
            ctk.CTkLabel(
                self._mod_list_frame,
                text="未找到模组\n请尝试其他关键词",
                font=ctk.CTkFont(family=FONT_FAMILY, size=14),
                text_color=COLORS["text_secondary"],
                justify=ctk.CENTER,
            ).pack(pady=40)
            self._update_pagination()
            self._set_status("未找到结果")
            return

        for mod in hits:
            self._create_mod_item(mod)

        self._update_pagination()

        # 更新结果计数
        start = self._current_offset + 1
        end = min(self._current_offset + self.PAGE_SIZE, self._total_hits)
        self._result_count_label.configure(text=f"显示 {start}-{end} / 共 {self._total_hits} 个")
        self._set_status(f"共找到 {self._total_hits} 个模组")

    def _render_error(self, error_msg: str):
        """渲染错误状态"""
        for w in self._mod_list_frame.winfo_children():
            w.destroy()

        ctk.CTkLabel(
            self._mod_list_frame,
            text=f"搜索失败\n{error_msg}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["error"],
            justify=ctk.CENTER,
        ).pack(pady=40)
        self._set_status(f"搜索失败: {error_msg}")

    def _create_mod_item(self, mod: Dict):
        """创建单个模组条目"""
        row = ctk.CTkFrame(
            self._mod_list_frame,
            fg_color=COLORS["bg_medium"],
            corner_radius=8,
        )
        row.pack(fill=ctk.X, pady=3, padx=2)

        # 上行：模组名 + 下载按钮
        top_row = ctk.CTkFrame(row, fg_color="transparent", height=36)
        top_row.pack(fill=ctk.X, padx=10, pady=(8, 2))
        top_row.pack_propagate(False)

        # 模组名
        title = mod.get("title", "未知模组")
        ctk.CTkLabel(
            top_row,
            text=f"🧩 {title}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        ).pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        # 下载计数
        downloads = mod.get("downloads", 0)
        dl_text = self._format_downloads(downloads)
        ctk.CTkLabel(
            top_row,
            text=f"📥 {dl_text}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT, padx=(5, 0))

        # 安装按钮
        project_id = mod.get("project_id", "")
        ctk.CTkButton(
            top_row,
            text="📥 安装",
            width=70,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            text_color=COLORS["text_primary"],
            command=lambda pid=project_id, t=title: self._on_install_mod(pid, t),
        ).pack(side=ctk.RIGHT, padx=(8, 0))

        # 下行：描述
        description = mod.get("description", "")
        if description:
            desc_label = ctk.CTkLabel(
                row,
                text=description,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                text_color=COLORS["text_secondary"],
                wraplength=700,
                justify=ctk.LEFT,
                anchor=ctk.W,
            )
            desc_label.pack(fill=ctk.X, padx=10, pady=(0, 4))

        # 底部标签：支持的加载器与版本
        categories = mod.get("categories", [])
        versions_display = mod.get("versions", [])
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

        if tag_parts:
            tags_text = "  ·  ".join(tag_parts)
            ctk.CTkLabel(
                row,
                text=tags_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
                anchor=ctk.W,
            ).pack(fill=ctk.X, padx=10, pady=(0, 8))

    @staticmethod
    def _format_downloads(count: int) -> str:
        """格式化下载数"""
        if count >= 1_000_000:
            return f"{count / 1_000_000:.1f}M"
        elif count >= 1_000:
            return f"{count / 1_000:.1f}K"
        return str(count)

    def _update_pagination(self):
        """更新分页控件状态"""
        total_pages = max(1, (self._total_hits + self.PAGE_SIZE - 1) // self.PAGE_SIZE)
        current_page = (self._current_offset // self.PAGE_SIZE) + 1

        self._page_label.configure(text=f"{current_page} / {total_pages}")
        self._prev_btn.configure(state=ctk.NORMAL if self._current_offset > 0 else ctk.DISABLED)
        self._next_btn.configure(
            state=ctk.NORMAL if self._current_offset + self.PAGE_SIZE < self._total_hits else ctk.DISABLED
        )

    def _on_prev_page(self):
        """上一页"""
        if self._current_offset > 0:
            self._current_offset -= self.PAGE_SIZE
            self._set_status("正在加载...")
            self._run_in_thread(self._do_search)

    def _on_next_page(self):
        """下一页"""
        if self._current_offset + self.PAGE_SIZE < self._total_hits:
            self._current_offset += self.PAGE_SIZE
            self._set_status("正在加载...")
            self._run_in_thread(self._do_search)

    def _on_install_mod(self, project_id: str, title: str):
        """安装模组按钮回调"""
        self._set_status(f"正在获取 {title} 版本信息...")
        self._run_in_thread(self._install_mod, project_id, title)

    def _install_mod(self, project_id: str, title: str):
        """安装模组（后台线程，含依赖自动安装）"""
        from modrinth import install_mod_with_deps

        try:
            if not self._game_version or not self._mod_loader:
                self.after(0, self._set_status, "无法确定游戏版本或加载器类型")
                return

            mods_dir = self._get_mods_dir()

            success, result, installed_names = install_mod_with_deps(
                project_id,
                game_version=self._game_version,
                mod_loader=self._mod_loader,
                mods_dir=mods_dir,
                status_callback=lambda msg: self.after(0, self._set_status, msg),
            )

            if success:
                if len(installed_names) > 1:
                    deps = ", ".join(installed_names[:-1])
                    self.after(
                        0,
                        self._set_status,
                        f"✅ {title} 及依赖安装成功! (依赖: {deps})",
                    )
                else:
                    self.after(0, self._set_status, f"✅ {title} 安装成功!")
                logger.info(f"模组安装成功: {installed_names} -> {result}")
            else:
                self.after(0, self._set_status, f"❌ {result}")
                logger.error(f"模组安装失败: {result}")

        except Exception as e:
            error_msg = str(e)
            self.after(0, self._set_status, f"安装失败: {error_msg}")
            logger.error(f"安装模组失败: {e}")

    def _get_mods_dir(self) -> str:
        """获取当前版本的 mods 目录"""
        mc_dir = Path(".")
        if "get_minecraft_dir" in self.callbacks:
            mc_dir = Path(self.callbacks["get_minecraft_dir"]())

        # 仅模组加载器版本使用版本隔离目录
        v = self.version_id.lower()
        if any(loader in v for loader in ("forge", "fabric", "neoforge")):
            version_dir = mc_dir / "versions" / self.version_id / "mods"
            return str(version_dir)

        return str(mc_dir / "mods")

    def _set_status(self, text: str):
        """更新状态栏"""
        try:
            if self.winfo_exists():
                self._status_label.configure(text=text)
        except Exception:
            pass

    def _run_in_thread(self, target, *args, **kwargs):
        """后台线程执行"""
        thread = threading.Thread(target=target, args=args, kwargs=kwargs, daemon=True)
        thread.start()
