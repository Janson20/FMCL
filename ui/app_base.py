"""ModernApp 基础 Mixin - 初始化、UI 构建、侧边栏、日志"""
import os
import io
import sys
import re
import logging
import platform
from pathlib import Path
from typing import List, Dict, Optional, Callable, Any

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY, _get_fmcl_version


class ModernAppBase(ctk.CTk):
    """ModernApp 基础 Mixin - UI 框架构建"""

    def __init__(self, launcher_callbacks: Dict[str, Callable]):
        """
        初始化主窗口

        Args:
            launcher_callbacks: 启动器回调函数字典
                - check_environment: 检查环境
                - get_available_versions: 获取可用版本
                - get_installed_versions: 获取已安装版本
                - install_version: 安装版本 (version_id, mod_loader) -> (bool, str)
                - launch_game: 启动游戏 (version_id) -> bool
        """
        super().__init__()

        self.callbacks = launcher_callbacks
        self._task_queue = None  # set by EventHandlerMixin._init_task_queue via after()
        self._running = True
        self._launcher_ready = False  # 标记 launcher 是否初始化完成
        self._current_skin_path: Optional[str] = None  # 当前皮肤路径

        # 窗口配置
        self.title("FMCL - Fusion Minecraft Launcher")
        self.geometry("1200x860")
        self.minsize(1060, 800)
        self.configure(fg_color=COLORS["bg_dark"])

        # 居中显示
        self._center_window()

        # 构建UI
        self._build_ui()

        # 启动队列轮询
        self.after(100, self._deferred_init)

    def _deferred_init(self):
        """延迟初始化 - 确保 _task_queue 在其他 mixin 设置后再启动轮询"""
        self._poll_queue()

    def _center_window(self):
        """窗口居中"""
        self.update_idletasks()
        w, h = 1200, 860
        x = (self.winfo_screenwidth() - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ─── UI 构建 ─────────────────────────────────────────────

    def _build_ui(self):
        """构建主界面"""
        # 主容器
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(fill=ctk.BOTH, expand=True, padx=20, pady=20)

        self._build_header()
        self._build_content()
        self._build_footer()

    def _build_header(self):
        """构建头部区域"""
        header = ctk.CTkFrame(self.main_frame, fg_color="transparent", height=60)
        header.pack(fill=ctk.X, pady=(0, 15))
        header.pack_propagate(False)

        # 标题
        title_label = ctk.CTkLabel(
            header,
            text="⛏ FMCL",
            font=ctk.CTkFont(family=FONT_FAMILY, size=28, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title_label.pack(side=ctk.LEFT, padx=(5, 0))

        subtitle = ctk.CTkLabel(
            header,
            text="Fusion Minecraft Launcher",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            text_color=COLORS["text_secondary"],
        )
        subtitle.pack(side=ctk.LEFT, padx=(15, 0), pady=(10, 0))

        # 刷新按钮
        refresh_btn = ctk.CTkButton(
            header,
            text="🔄 刷新",
            width=100,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._refresh_versions,
        )
        refresh_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        # 检查更新按钮
        self.update_btn = ctk.CTkButton(
            header,
            text="⬆ 更新",
            width=90,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_check_update,
        )
        self.update_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        # 启动器设置按钮
        settings_btn = ctk.CTkButton(
            header,
            text="⚙ 设置",
            width=90,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._open_launcher_settings,
        )
        settings_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        # 关于按钮（winver 风格）
        about_btn = ctk.CTkButton(
            header,
            text="ℹ 关于",
            width=80,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._show_about,
        )
        about_btn.pack(side=ctk.RIGHT, padx=(10, 0))

        # 保留设置变量（供内部使用）
        self.minimize_var = ctk.BooleanVar(value=self.callbacks.get("get_minimize_on_game_launch", lambda: False)())
        self.mirror_var = ctk.BooleanVar(value=self.callbacks.get("get_mirror_enabled", lambda: True)())

    def _build_content(self):
        """构建内容区域 - 使用标签页"""
        import queue

        # 初始化任务队列（在这里初始化确保所有 mixin 共享同一个）
        if not hasattr(self, '_task_queue') or self._task_queue is None:
            self._task_queue = queue.Queue()

        # 创建标签页容器
        self.tabview = ctk.CTkTabview(self.main_frame, fg_color="transparent")
        self.tabview.pack(fill=ctk.BOTH, expand=True, padx=0, pady=0)
        
        # 添加"游戏"标签页
        self.game_tab = self.tabview.add("🎮 游戏")
        self.game_tab.configure(fg_color="transparent")

        # 添加"备份"标签页
        self.backup_tab = self.tabview.add("💾 备份")
        self.backup_tab.configure(fg_color="transparent")

        # 添加"开服"标签页
        self.server_tab = self.tabview.add("🖥 开服")
        self.server_tab.configure(fg_color="transparent")
        
        # 添加"链接"标签页
        self.links_tab = self.tabview.add("🔗 链接")
        self.links_tab.configure(fg_color="transparent")
        
        # 设置默认标签页为"游戏"
        self.tabview.set("🎮 游戏")
        
        # 构建游戏标签页内容
        self._build_game_tab_content()

        # 构建备份标签页内容
        self._build_backup_tab_content()

        # 构建开服标签页内容
        self._build_server_tab_content()
        
        # 构建链接标签页内容
        self._build_links_tab_content()
    
    def _build_game_tab_content(self):
        """构建游戏标签页内容"""
        content = ctk.CTkFrame(self.game_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)
        
        # 最左侧 - 侧边栏（角色名、皮肤、日志）
        self._build_sidebar(content)

        # 中间 - 已安装版本
        self._build_installed_panel(content)

        # 右侧 - 操作面板
        self._build_action_panel(content)

    def _build_links_tab_content(self):
        """构建链接标签页内容"""
        # Minecraft相关网站数据
        minecraft_sites = [
            {
                "name": "Minecraft官网",
                "description": "Minecraft官方游戏网站，提供游戏下载、更新和官方信息，支持多平台版本获取与账号管理。",
                "tags": ["官方", "游戏下载", "更新"],
                "link": "https://www.minecraft.net/zh-hans"
            },
            {
                "name": "Minecraft中文官网",
                "description": "Minecraft中国版官方游戏平台，提供网易代理版本的下载、更新和本地化服务。",
                "tags": ["官方", "游戏下载", "中国版"],
                "link": "http://mc.163.com"
            },
            {
                "name": "Minecraft中文Wiki",
                "description": "最全面的Minecraft中文百科全书，提供游戏机制、合成表、生物群系等详细信息，运行12年以上且社区活跃。",
                "tags": ["百科", "知识库", "中文"],
                "link": "https://zh.minecraft.wiki"
            },
            {
                "name": "MineBBS",
                "description": "中国最大的Minecraft资源交流论坛，提供模组、地图、材质包、皮肤等全品类资源，支持Java版与基岩版。",
                "tags": ["论坛", "社区", "资源下载"],
                "link": "https://www.minebbs.com"
            },
            {
                "name": "Minecraft苦力怕论坛",
                "description": "国内活跃的Minecraft中文社区，提供资源下载、技术交流和创作分享，拥有大量基岩版资源。",
                "tags": ["论坛", "社区", "中文"],
                "link": "https://klpbbs.com"
            },
            {
                "name": "CurseForge",
                "description": "全球最大的Minecraft模组下载平台，拥有超过25万个模组资源，支持版本筛选和PCL2/HMCL启动器集成。",
                "tags": ["模组", "资源下载", "插件"],
                "link": "https://www.curseforge.com/minecraft"
            },
            {
                "name": "Modrinth",
                "description": "新兴Minecraft模组资源平台，界面友好、访问速度快，提供模组、资源包和整合包下载，支持中文资源。",
                "tags": ["模组", "资源下载", "整合包"],
                "link": "https://modrinth.com"
            },
            {
                "name": "PlanetMinecraft",
                "description": "专注于Minecraft地图、皮肤和资源包的下载网站，提供详细分类和预览功能，适合寻找特定内容。",
                "tags": ["地图", "皮肤", "资源下载"],
                "link": "https://www.planetminecraft.com"
            },
            {
                "name": "MinecraftSkins.net",
                "description": "全球玩家创作的Minecraft皮肤资源库，提供3D预览、按标签搜索和UUID匹配功能，每日更新新皮肤。",
                "tags": ["皮肤", "资源下载"],
                "link": "https://www.minecraftskins.net"
            },
            {
                "name": "NameMC",
                "description": "Minecraft正版玩家信息查询平台，可查看玩家历史皮肤、UUID信息，支持3D皮肤效果预览。",
                "tags": ["皮肤", "查询"],
                "link": "https://namemc.com"
            },
            {
                "name": "ChunkBase",
                "description": "专业的Minecraft种子查询工具，可分析区块、查找结构、定位生物群系，是建筑和探险的实用助手。",
                "tags": ["工具", "种子查询", "区块分析"],
                "link": "https://www.chunkbase.com"
            },
            {
                "name": "Minecraft教育版官网",
                "description": "Minecraft教育版官方平台，提供教学资源、课程模板和教育工具，专为教师和学生设计。",
                "tags": ["官方", "教育", "资源"],
                "link": "https://education.minecraft.net"
            },
            {
                "name": "Minecraft Heads",
                "description": "提供Minecraft装饰性头颅资源，支持自定义设计和下载，可用于游戏内装饰和建筑。",
                "tags": ["装饰", "资源", "头颅"],
                "link": "https://www.minecraft-heads.com"
            },
            {
                "name": "Amulet地图编辑器",
                "description": "开源的Minecraft世界编辑工具，支持Java 1.12+和Bedrock 1.7+版本，提供三维可视化编辑和精确坐标控制。",
                "tags": ["工具", "地图编辑", "世界转换"],
                "link": "https://gitcode.com/gh_mirrors/am/Amulet-Map-Editor"
            },
            {
                "name": "MCskin",
                "description": "Minecraft皮肤制作与编辑网站，提供皮肤抓取、自定义人物动作、调整光照和颜色背景功能，支持透明底下载。",
                "tags": ["皮肤", "编辑工具", "自定义"],
                "link": "https://mcskins.top"
            },
            {
                "name": "Minecraft Shaders",
                "description": "专注于Minecraft光影包（Shaders）的下载网站，提供各类光影效果的预览和下载，帮助你轻松提升游戏画面表现。",
                "tags": ["光影", "画面", "渲染"],
                "link": "https://minecraftshader.com"
            },
            {
                "name": "Resource Packs",
                "description": "老牌材质包（Resource Packs）下载站，分类详细，提供高清修复、奇幻风格、像素风等多种类型的材质包下载。",
                "tags": ["材质包", "高清修复", "纹理"],
                "link": "https://resourcepack.net"
            },
            {
                "name": "MCPEDL",
                "description": "全球知名的基岩版（Bedrock Edition）资源站，提供海量的 addons、地图、皮肤和模组，是手机版玩家的首选资源库。",
                "tags": ["基岩版", "手机版", "Addons"],
                "link": "https://mcpedl.com"
            },
            {
                "name": "Minecraft Maps",
                "description": "专业的Minecraft地图下载网站，收录了冒险地图、解谜地图、PVP地图和生存挑战等多种玩家自制地图。",
                "tags": ["地图", "冒险", "下载"],
                "link": "http://www.minecraftmaps.com"
            },
            {
                "name": "The Skindex",
                "description": "老牌皮肤网站，拥有庞大的皮肤库和简单易用的在线皮肤编辑器，支持直接预览和下载。",
                "tags": ["皮肤", "编辑器", "社区"],
                "link": "https://www.minecraftskins.com"
            },
            {
                "name": "Minecraft Servers",
                "description": "全球Minecraft服务器列表，玩家可以根据标签（如生存、空岛、小游戏）查找和投票支持喜欢的服务器。",
                "tags": ["服务器", "多人联机", "列表"],
                "link": "https://minecraftservers.org"
            },
            {
                "name": "我的世界服务器列表 (mclists)",
                "description": "国内知名的服务器宣传与列表平台，方便国内玩家查找稳定的中文服务器，涵盖各种玩法类型。",
                "tags": ["服务器", "国内", "中文服"],
                "link": "https://www.mclists.cn"
            },
            {
                "name": "Minecraft Tools",
                "description": "综合性工具箱网站，提供合成表查询、效果查询、附魔计算器、生物生成条件查询等实用功能。",
                "tags": ["工具", "合成表", "计算器"],
                "link": "https://minecraft.tools"
            },
            {
                "name": "Nova Skins",
                "description": "功能强大的皮肤编辑与壁纸生成工具，支持皮肤动图制作、披风编辑以及复杂的滤镜效果处理。",
                "tags": ["皮肤编辑", "壁纸生成", "工具"],
                "link": "https://novaskin.me"
            },
            {
                "name": "mclo.gs",
                "description": "服务器腐竹和玩家必备工具，用于上传和分析游戏崩溃日志（Logs），能快速定位错误原因并提供解决方案建议。",
                "tags": ["日志分析", "除错", "服务器管理"],
                "link": "https://mclo.gs"
            },
            {
                "name": "Chunker",
                "description": "在线存档转换工具，支持将Java版存档转换为基岩版（反之亦然），方便跨平台玩家迁移世界数据。",
                "tags": ["存档转换", "跨平台", "工具"],
                "link": "https://chunker.app"
            },
            {
                "name": "Minecraft Forge",
                "description": "Minecraft Java版最古老的模组加载器官网，提供最新版本的Forge下载，是运行大量经典模组的必要环境。",
                "tags": ["Forge", "模组加载器", "API"],
                "link": "https://www.minecraftforge.net"
            },
            {
                "name": "Fabric",
                "description": "轻量级、高性能的模组加载器，启动速度快，社区活跃，适合喜欢最新版本和轻量级模组的玩家。",
                "tags": ["Fabric", "模组加载器", "高性能"],
                "link": "https://fabricmc.net"
            },
            {
                "name": "ArmorTrims",
                "description": "1.20+版本盔甲纹饰预览工具，可以直观地查看不同锻造模板和材料组合后的盔甲外观效果。",
                "tags": ["盔甲纹饰", "预览", "1.20+"],
                "link": "https://www.armortrims.com"
            }
        ]
        
        # 主容器
        main_container = ctk.CTkFrame(self.links_tab, fg_color="transparent")
        main_container.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)
        
        # 标题
        title_label = ctk.CTkLabel(
            main_container,
            text="🔗 Minecraft 相关网站",
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        title_label.pack(anchor=ctk.W, pady=(0, 10))
        
        # 描述
        desc_label = ctk.CTkLabel(
            main_container,
            text="收录了 Minecraft 相关的官方网站、社区、资源下载和工具网站，方便快速访问。",
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_secondary"],
        )
        desc_label.pack(anchor=ctk.W, pady=(0, 20))
        
        # 网站列表容器（可滚动）
        scroll_frame = ctk.CTkScrollableFrame(
            main_container,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        scroll_frame.pack(fill=ctk.BOTH, expand=True)
        
        # 创建网站卡片
        for site in minecraft_sites:
            self._create_site_card(scroll_frame, site)
    
    def _create_site_card(self, parent, site):
        """创建网站卡片"""
        card = ctk.CTkFrame(
            parent,
            fg_color=COLORS["card_bg"],
            corner_radius=12,
            border_width=1,
            border_color=COLORS["card_border"],
        )
        card.pack(fill=ctk.X, pady=5)
        
        # 卡片内部容器
        card_inner = ctk.CTkFrame(card, fg_color="transparent")
        card_inner.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)
        
        # 网站名称和标签行
        name_frame = ctk.CTkFrame(card_inner, fg_color="transparent")
        name_frame.pack(fill=ctk.X, pady=(0, 8))
        
        # 网站名称
        name_label = ctk.CTkLabel(
            name_frame,
            text=site["name"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
            anchor=ctk.W,
        )
        name_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True)
        
        # 标签
        tags_frame = ctk.CTkFrame(name_frame, fg_color="transparent")
        tags_frame.pack(side=ctk.RIGHT)
        
        for tag in site["tags"][:3]:  # 最多显示3个标签
            tag_label = ctk.CTkLabel(
                tags_frame,
                text=tag,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["accent"],
                fg_color=COLORS["bg_medium"],
                corner_radius=10,
                padx=8,
                pady=2,
            )
            tag_label.pack(side=ctk.LEFT, padx=(2, 0))
        
        # 网站描述
        desc_label = ctk.CTkLabel(
            card_inner,
            text=site["description"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=800,
            justify=ctk.LEFT,
            anchor=ctk.W,
        )
        desc_label.pack(fill=ctk.X, pady=(0, 10))
        
        # 链接和按钮行
        link_frame = ctk.CTkFrame(card_inner, fg_color="transparent")
        link_frame.pack(fill=ctk.X)
        
        # 链接地址
        link_label = ctk.CTkLabel(
            link_frame,
            text=site["link"],
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
        )
        link_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True)
        
        # 打开链接按钮
        def create_open_link_callback(url):
            import webbrowser
            return lambda: webbrowser.open(url)
        
        open_btn = ctk.CTkButton(
            link_frame,
            text="🌐 打开链接",
            width=100,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=create_open_link_callback(site["link"]),
        )
        open_btn.pack(side=ctk.RIGHT, padx=(10, 0))
        
        # 复制链接按钮
        def create_copy_link_callback(url, name):
            import pyperclip
            def copy_func():
                try:
                    pyperclip.copy(url)
                    # 显示复制成功提示
                    if hasattr(self, 'set_status'):
                        self.set_status(f"已复制链接: {name}", "success")
                except Exception as e:
                    if hasattr(self, 'set_status'):
                        self.set_status(f"复制失败: {e}", "error")
            return copy_func
        
        copy_btn = ctk.CTkButton(
            link_frame,
            text="📋 复制链接",
            width=90,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=create_copy_link_callback(site["link"], site["name"]),
        )
        copy_btn.pack(side=ctk.RIGHT)

    def _build_sidebar(self, parent):
        """构建左侧边栏：自定义角色名、自定义皮肤、启动器日志"""
        sidebar = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, width=220)
        sidebar.pack(side=ctk.LEFT, fill=ctk.Y, padx=(0, 10))
        sidebar.pack_propagate(False)

        # ── 自定义角色名 ──
        ctk.CTkLabel(
            sidebar,
            text="👤 角色名",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=12, pady=(15, 5), anchor=ctk.W)

        ctk.CTkFrame(sidebar, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(0, 8)
        )

        self.player_name_var = ctk.StringVar(value="")
        self.player_name_entry = ctk.CTkEntry(
            sidebar,
            textvariable=self.player_name_var,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="输入角色名",
        )
        self.player_name_entry.pack(fill=ctk.X, padx=12, pady=(0, 5))

        self.player_name_entry.bind("<FocusOut>", self._on_player_name_change)

        # ── 自定义皮肤 ──
        ctk.CTkLabel(
            sidebar,
            text="🎨 自定义皮肤",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=12, pady=(15, 5), anchor=ctk.W)

        ctk.CTkFrame(sidebar, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(0, 8)
        )

        # 皮肤预览区
        self.skin_preview_frame = ctk.CTkFrame(
            sidebar, fg_color=COLORS["bg_medium"], corner_radius=8, height=80
        )
        self.skin_preview_frame.pack(fill=ctk.X, padx=12, pady=(0, 5))
        self.skin_preview_frame.pack_propagate(False)

        self.skin_preview_label = ctk.CTkLabel(
            self.skin_preview_frame,
            text="暂无皮肤\n支持 64x64 / 64x32 PNG",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            justify=ctk.CENTER,
        )
        self.skin_preview_label.pack(expand=True)

        # 皮肤操作按钮行
        skin_btn_frame = ctk.CTkFrame(sidebar, fg_color="transparent", height=30)
        skin_btn_frame.pack(fill=ctk.X, padx=12, pady=(0, 5))
        skin_btn_frame.pack_propagate(False)

        ctk.CTkButton(
            skin_btn_frame,
            text="📂 选择皮肤",
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_select_skin,
        ).pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 3))

        self._skin_remove_btn = ctk.CTkButton(
            skin_btn_frame,
            text="🗑",
            width=36,
            height=28,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["accent"],
            text_color=COLORS["text_secondary"],
            command=self._on_remove_skin,
        )
        self._skin_remove_btn.pack(side=ctk.RIGHT)

        # ── 启动器日志 ──
        ctk.CTkLabel(
            sidebar,
            text="📋 启动器日志",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=12, pady=(15, 5), anchor=ctk.W)

        ctk.CTkFrame(sidebar, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=12, pady=(0, 8)
        )

        # 日志文本框（可滚动）
        self.log_text = ctk.CTkTextbox(
            sidebar,
            font=ctk.CTkFont(family="Consolas", size=10),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_secondary"],
            activate_scrollbars=True,
            height=200,
            wrap=ctk.WORD,
            spacing3=1,
        )
        self.log_text.pack(fill=ctk.BOTH, expand=True, padx=12, pady=(0, 5))

        # 清空日志按钮
        ctk.CTkButton(
            sidebar,
            text="清空日志",
            height=26,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_clear_log,
        ).pack(fill=ctk.X, padx=12, pady=(0, 12))

        # 设置日志捕获
        self._setup_log_capture()

        # 记录启动日志
        self._append_log("[FMCL] 启动器已启动")

    def _setup_log_capture(self):
        """设置日志捕获，将 logzero 输出重定向到 UI 日志框"""
        self._log_buffer = io.StringIO()
        try:
            import logzero
            # 添加一个自定义 handler 将日志写入 buffer
            self._log_writer = logging.StreamHandler(self._log_buffer)
            self._log_writer.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S"))
            self._log_writer.setLevel(logging.DEBUG)
            logzero.logger.addHandler(self._log_writer)
            self._log_capture_active = True
        except Exception:
            self._log_capture_active = False

    def _append_log(self, message: str):
        """追加日志到 UI 日志框（线程安全）"""
        def _do_append():
            self.log_text.insert(ctk.END, message + "\n")
            self.log_text.see(ctk.END)
        if self.winfo_exists():
            self.after(0, _do_append)

    def _poll_log_buffer(self):
        """轮询日志缓冲区，将新日志写入 UI"""
        if not self._running or not self._log_capture_active:
            return
        content = self._log_buffer.getvalue()
        if content:
            self._log_buffer.seek(0)
            self._log_buffer.truncate(0)
            lines = content.strip().split("\n")
            for line in lines:
                if line.strip():
                    self._append_log(line)
        self.after(500, self._poll_log_buffer)

    def _on_player_name_change(self, event=None):
        """角色名输入框失焦时保存"""
        name = self.player_name_var.get().strip()
        if name and "set_player_name" in self.callbacks:
            self.callbacks["set_player_name"](name)

    def _on_select_skin(self):
        """选择皮肤文件"""
        from tkinter import filedialog
        filetypes = [("皮肤文件", "*.png"), ("所有文件", "*.*")]
        filepath = filedialog.askopenfilename(
            title="选择皮肤文件",
            filetypes=filetypes,
        )
        if not filepath:
            return

        # 验证皮肤文件尺寸
        try:
            from PIL import Image
            with Image.open(filepath) as img:
                w, h = img.size
                if (w, h) not in [(64, 64), (64, 32), (128, 128), (128, 64)]:
                    self.set_status(f"皮肤尺寸 {w}x{h} 不支持，请使用 64x64 或 64x32", "warning")
                    return
        except ImportError:
            pass  # 无 PIL，跳过尺寸验证
        except Exception:
            self.set_status("无法读取皮肤文件", "error")
            return

        self._current_skin_path = filepath
        self._update_skin_preview(filepath)

        if "set_skin_path" in self.callbacks:
            self.callbacks["set_skin_path"](filepath)

        # 复制皮肤到 .minecraft 目录
        if "get_minecraft_dir" in self.callbacks:
            mc_dir = Path(self.callbacks["get_minecraft_dir"]())
            skin_dir = mc_dir / "skins"
            skin_dir.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy2(filepath, str(skin_dir / Path(filepath).name))
            self.set_status(f"皮肤已安装: {Path(filepath).name}", "success")

    def _update_skin_preview(self, filepath: str):
        """更新皮肤预览"""
        filename = Path(filepath).name
        if len(filename) > 20:
            filename = filename[:17] + "..."
        self.skin_preview_label.configure(text=f"✅ {filename}", text_color=COLORS["success"])

    def _on_remove_skin(self):
        """移除皮肤"""
        self._current_skin_path = None
        self.skin_preview_label.configure(text="暂无皮肤\n支持 64x64 / 64x32 PNG", text_color=COLORS["text_secondary"])
        if "set_skin_path" in self.callbacks:
            self.callbacks["set_skin_path"](None)
        self.set_status("皮肤已移除", "info")

    def _on_clear_log(self):
        """清空日志"""
        self.log_text.delete("1.0", ctk.END)

    def _build_installed_panel(self, parent):
        """构建已安装版本面板"""
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12)
        panel.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 10))

        # 标题栏
        title_frame = ctk.CTkFrame(panel, fg_color="transparent", height=45)
        title_frame.pack(fill=ctk.X, padx=15, pady=(12, 0))
        title_frame.pack_propagate(False)

        ctk.CTkLabel(
            title_frame,
            text="📦 已安装版本",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self.version_count_label = ctk.CTkLabel(
            title_frame,
            text="0 个版本",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self.version_count_label.pack(side=ctk.RIGHT)

        # 设置按钮（资源管理）
        settings_btn = ctk.CTkButton(
            title_frame,
            text="⚙",
            width=30,
            height=28,
            font=ctk.CTkFont(size=16),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_secondary"],
            command=self._open_resource_manager,
        )
        settings_btn.pack(side=ctk.RIGHT, padx=(0, 8))

        # 分割线
        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(8, 5)
        )

        # 版本列表 (带滚动)
        list_frame = ctk.CTkScrollableFrame(
            panel, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        list_frame.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.version_list_frame = list_frame
        self.version_buttons: List[Dict[str, Any]] = []

        # 底部启动/结束按钮
        launch_frame = ctk.CTkFrame(panel, fg_color="transparent", height=50)
        launch_frame.pack(fill=ctk.X, padx=15, pady=(0, 12))
        launch_frame.pack_propagate(False)

        self.launch_btn = ctk.CTkButton(
            launch_frame,
            text="🚀 启动游戏",
            height=40,
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_launch,
        )
        self.launch_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        self.kill_btn = ctk.CTkButton(
            launch_frame,
            text="⏹",
            width=50,
            height=40,
            font=ctk.CTkFont(size=16),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            text_color=COLORS["text_primary"],
            command=self._on_kill_game,
        )
        self.kill_btn.pack(side=ctk.RIGHT, padx=(8, 0))
        self.kill_btn.configure(state=ctk.DISABLED)

        self.selected_version: Optional[str] = None

    def _build_action_panel(self, parent):
        """构建右侧操作面板"""
        panel = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, width=300)
        panel.pack(side=ctk.RIGHT, fill=ctk.Y, padx=(0, 0))
        panel.pack_propagate(False)

        # ── 安装新版本区域 ──
        ctk.CTkLabel(
            panel,
            text="📥 安装新版本",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=15, pady=(15, 8), anchor=ctk.W)

        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(0, 10)
        )

        # 版本ID输入
        ctk.CTkLabel(
            panel,
            text="版本 ID:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(padx=15, anchor=ctk.W)

        self.version_entry = ctk.CTkEntry(
            panel,
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text="例如: 1.20.4 或 26.1",
        )
        self.version_entry.pack(fill=ctk.X, padx=15, pady=(5, 10))

        # 模组加载器选项
        ctk.CTkLabel(
            panel,
            text="模组加载器:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(padx=15, anchor=ctk.W)

        self.modloader_var = ctk.StringVar(value="无")
        self.modloader_menu = ctk.CTkOptionMenu(
            panel,
            variable=self.modloader_var,
            values=["无", "Forge", "Fabric", "NeoForge"],
            height=35,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            button_color=COLORS["bg_light"],
            button_hover_color=COLORS["card_border"],
            dropdown_fg_color=COLORS["bg_medium"],
            dropdown_hover_color=COLORS["bg_light"],
        )
        self.modloader_menu.pack(fill=ctk.X, padx=15, pady=(5, 5))

        # 模组加载器提示
        self.modloader_hint = ctk.CTkLabel(
            panel,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["warning"],
            wraplength=260,
            justify=ctk.LEFT,
        )
        self.modloader_hint.pack(padx=15, anchor=ctk.W, pady=(0, 10))
        self.modloader_var.trace_add("write", self._on_modloader_change)
        self._on_modloader_change()

        # 安装按钮 + 整合包按钮并排
        btn_row = ctk.CTkFrame(panel, fg_color="transparent")
        btn_row.pack(fill=ctk.X, padx=15, pady=(0, 15))

        self.install_btn = ctk.CTkButton(
            btn_row,
            text="📥 安装版本",
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_install,
        )
        self.install_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 5))

        self.modpack_btn = ctk.CTkButton(
            btn_row,
            text="📦 安装整合包",
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_install_modpack,
        )
        self.modpack_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(5, 0))

        # ── 版本选择器 ──
        ctk.CTkLabel(
            panel,
            text="📋 快速选择",
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(padx=15, pady=(5, 8), anchor=ctk.W)

        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(0, 8)
        )

        # 正式版/测试版 Tab 切换
        self.version_tab_var = ctk.StringVar(value="release")
        tab_frame = ctk.CTkFrame(panel, fg_color="transparent", height=32)
        tab_frame.pack(fill=ctk.X, padx=15, pady=(0, 5))
        tab_frame.pack_propagate(False)

        release_tab = ctk.CTkRadioButton(
            tab_frame,
            text="📦 正式版",
            variable=self.version_tab_var,
            value="release",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_color=COLORS["text_secondary"],
            command=self._on_version_tab_change,
        )
        release_tab.pack(side=ctk.LEFT, padx=(0, 10))

        snapshot_tab = ctk.CTkRadioButton(
            tab_frame,
            text="🔬 测试版",
            variable=self.version_tab_var,
            value="snapshot",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            border_color=COLORS["text_secondary"],
            command=self._on_version_tab_change,
        )
        snapshot_tab.pack(side=ctk.LEFT)

        # 可用版本列表
        avail_frame = ctk.CTkScrollableFrame(
            panel, fg_color="transparent", height=155, scrollbar_button_color=COLORS["bg_light"]
        )
        avail_frame.pack(fill=ctk.X, padx=10, pady=(0, 5))

        self.available_list_frame = avail_frame
        self.available_version_buttons: List[Dict[str, Any]] = []
        self._all_available_versions: List[Dict[str, Any]] = []
        self._release_versions: List[Dict[str, Any]] = []
        self._snapshot_versions: List[Dict[str, Any]] = []

        # 分页控件
        page_frame = ctk.CTkFrame(panel, fg_color="transparent", height=30)
        page_frame.pack(fill=ctk.X, padx=10, pady=(0, 10))
        page_frame.pack_propagate(False)

        self._page_size = 20
        self._current_page = 1

        self._prev_page_btn = ctk.CTkButton(
            page_frame,
            text="◀",
            width=28,
            height=26,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_prev_page,
        )
        self._prev_page_btn.pack(side=ctk.LEFT)

        self._page_info_label = ctk.CTkLabel(
            page_frame,
            text="1/1",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            width=60,
        )
        self._page_info_label.pack(side=ctk.LEFT, padx=5)

        self._next_page_btn = ctk.CTkButton(
            page_frame,
            text="▶",
            width=28,
            height=26,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
            command=self._on_next_page,
        )
        self._next_page_btn.pack(side=ctk.LEFT)

    def _build_footer(self):
        """构建底部状态栏"""
        footer = ctk.CTkFrame(self.main_frame, fg_color=COLORS["card_bg"], corner_radius=8, height=45)
        footer.pack(fill=ctk.X, pady=(12, 0))
        footer.pack_propagate(False)

        # 状态文本
        self.status_label = ctk.CTkLabel(
            footer,
            text="✅ 就绪",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["success"],
        )
        self.status_label.pack(side=ctk.LEFT, padx=15)

        # 进度条
        self.progress_bar = ctk.CTkProgressBar(
            footer,
            width=200,
            height=8,
            fg_color=COLORS["bg_medium"],
            progress_color=COLORS["accent"],
        )
        self.progress_bar.pack(side=ctk.RIGHT, padx=15)
        self.progress_bar.set(0)

        # 进度文本
        self.progress_label = ctk.CTkLabel(
            footer,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self.progress_label.pack(side=ctk.RIGHT, padx=(0, 10))

        self._launch_anim_running = False
