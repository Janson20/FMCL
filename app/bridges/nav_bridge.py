"""导航桥 —— QML 侧的 `Nav` 上下文属性（阶段 2 任务 2.11）。

## 它管什么、不管什么

**管**：哪一页在前台、栈有多深、面包屑是什么、每页的 QML 文件在哪、参数是什么。
**不管**：页面上要显示的数据。页面自己去问各自的桥（迁移红线 2：业务只存在于
`services/`，桥上不许出现 `if 版本号 > …` 之类的判断）。

## 三个已定的设计决定（都写了理由，不是随手选的）

1. **栈的真相只在 Python 侧这一份**。`qml/shell/PageStack.qml` 里的 `StackView`
   只是**镜像**：它按 `Nav.breadcrumb` 重建自己，不自己维护历史。
   这样"深链一次跳两级""Agent 直接跳转""插件注册的路由"这些不需要在 QML 里
   再走一遍同样的栈逻辑（那必然出现两份实现和不同步）。

2. **面包屑就是栈的映射**，不是另外算出来的一条路径。每项带
   `{id, title_key, description_key, icon, qml_url, params, back_count}`：
   `Breadcrumb.qml` 用前两个与 `back_count` 渲染，`PageStack.qml` 用 `qml_url`
   与 `params` 建页。**故意不另开一个 `stackRoutes()` 槽** —— 两个 API 表达同一份
   数据时，唯一确定的结果是"某天其中一份忘了同步"。

3. **`title_key` 一律复用现有 i18n 键**（契约 §2：沿用现有 1529 键、不新造前缀），
   键值由语言文件决定，与图标（`Runtime.iconUrl`）分开 —— 这样阶段 3 清理语言文件里
   的旧 emoji 前缀时，QML 一个字都不用改。老界面**没有**对应页面的新页（02 §4 的
   详情类页面）才新建键，前缀沿用各域已有的家族（`version_*` / `resource_*` / …），
   缺键时 QML 按契约 §5.2 的写法回退显示键名。

## 非法输入不静默

未知路由、深链格式错、插件路由非法，一律 `navFailed(str)` + 日志。
唯一的例外是**查询类**调用（`resolve()`）——那不是在导航，返回空表即可。

## 线程前提

与其它桥一致：**只在主线程用**（QML 与装配方都在主线程）。阶段 3 的 Agent / 插件
若在 worker 线程里想跳转，必须经 `app/bridges/qt_dispatch.py` 投递到主线程。
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PySide6.QtCore import Property, QObject, QUrl, QUrlQuery, Signal, Slot

logger = logging.getLogger("app.bridges.nav_bridge")

#: 首页路由 id —— 根组件启动时压入的栈底（也是 `reset()` 的落点）。
HOME_ROUTE = "home"
#: 插件路由 id 前缀。插件页面可能重名（不同作者的 `main` / `settings`），
#: 加前缀既防撞名，也让 `Shell.navItems()` 能把插件项与内置项分开。
PLUGIN_ROUTE_PREFIX = "plugin/"
#: 深链协议。
DEEP_LINK_SCHEME = "fmcl"
#: 平台标识（与 `Runtime.describe()["platform"]` 的取值风格一致：windows / linux / macos）。
WINDOWS_PLATFORM = "windows"
#: 栈深上限：超过就丢**栈底**（保留离当前最近的 MAX_DEPTH 帧）而不是拒绝新页 ——
#: 用户点了第 33 层页面时，把"新页面打不开"摆在他面前是最差的选择。
#: 正常使用到不了 32 层，所以这条只在深链递归 / Agent 循环跳转时才会触发。
MAX_DEPTH = 32

#: **只在某个平台存在的领域**（02 §4.11：基岩版仅 Windows）。
#:
#: 处置方式（本任务的选择，两条一起做）：
#:   1. **一级导航里隐藏** —— 与旧界面一致（`ui/app_base.py:284` 就是"仅 Windows 才建这个页"）；
#:   2. **跳转时明确拒绝** —— 深链 / 插件 / Agent 在非 Windows 上跳到 `fmcl://bedrock*` 时
#:      `navFailed(原因)`，状态条会显示"仅 Windows 支持"，而不是一页空白。
#: 只做 1 会在深链下变成"点了空白"；只做 2 会在 Linux 上摆一个永远点不开的导航项。
_PLATFORM_ONLY_DOMAINS: Dict[str, str] = {"bedrock": WINDOWS_PLATFORM}


def host_platform() -> str:
    """当前平台标识（做成函数是为了可注入：测试用它验证非 Windows 的降级路径）。"""
    if os.name == "nt":
        return WINDOWS_PLATFORM
    if sys.platform == "darwin":
        return "macos"
    return "linux"



@dataclass(frozen=True)
class Route:
    """一条路由。字段就是契约里冻结的 `{id, title_key, icon, parent, qml_file}`。"""

    id: str
    title_key: str
    icon: str
    parent: str
    qml_file: str  # 相对 `qml/` 的路径
    description_key: str = ""
    source: str = "builtin"  # builtin | plugin
    owner: str = ""  # 插件路由的属主（插件 id）
    platform: str = ""  # 空 = 全平台；"windows" = 仅 Windows（见 _PLATFORM_ONLY_DOMAINS）

    @property
    def level(self) -> int:
        """层级：`versions` = 0，`versions/detail` = 1，`versions/mods/...` = 2。"""
        return self.id.count("/")

    def as_map(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "title_key": self.title_key,
            "description_key": self.description_key,
            "icon": self.icon,
            "parent": self.parent,
            "qml_file": self.qml_file,
            "level": self.level,
            "source": self.source,
            "owner": self.owner,
            "platform": self.platform,
        }


#: 完整路由表（02 §4 的界面信息架构：12 个一级领域 + 各自的 L2/L3 页面）。
#: 每行 `(id, title_key, icon, parent, qml_file, description_key)`。
_ROUTE_TABLE: Tuple[Tuple[str, str, str, str, str, str], ...] = (
    # ── 4.1 首页 ───────────────────────────────────────────────
    ("home", "tab_game", "home", "", "pages/home/HomePage.qml", ""),
    ("home/notice", "notice_title", "notify", "home", "pages/home/HomePage.qml", ""),
    ("home/achievements", "ach_recent_title", "trophy", "home", "pages/home/HomePage.qml", ""),
    # ── 4.2 版本 ───────────────────────────────────────────────
    ("versions", "installed_versions", "package", "", "pages/versions/VersionsPage.qml", ""),
    ("versions/detail", "version_detail_title", "info", "versions", "pages/versions/VersionsPage.qml", ""),
    # L3 挂在**版本详情**下面，所以 id 走 `versions/detail/…`：路由 id 的层级与
    # `parent` 链严格一致（测试钉住这条不变量），阶段 3 用 parent 生成二级菜单时不用再猜。
    ("versions/detail/mods", "resource_mods", "mods", "versions/detail", "pages/versions/VersionsPage.qml", ""),
    ("versions/detail/launch", "version_launch_title", "slider", "versions/detail",
     "pages/versions/VersionsPage.qml", ""),
    ("versions/install", "install_new_version", "download", "versions", "pages/versions/VersionsPage.qml", ""),
    ("versions/install/progress", "mp_install_progress_title", "pending", "versions/install",
     "pages/versions/VersionsPage.qml", ""),
    ("versions/modpack", "modpack_info", "modpack", "versions", "pages/versions/VersionsPage.qml", ""),
    ("versions/predownload", "predownload_title", "download", "versions", "pages/versions/VersionsPage.qml", ""),
    # ── 4.3 资源 ───────────────────────────────────────────────
    ("resources", "mod_browser_title_all", "mods", "", "pages/resources/ResourcesPage.qml", ""),
    ("resources/mods", "resource_mods", "mods", "resources", "pages/resources/ResourcesPage.qml", ""),
    ("resources/mods/detail", "mod_detail_title", "info", "resources/mods", "pages/resources/ResourcesPage.qml", ""),
    ("resources/packs", "resource_packs", "resourcepack", "resources", "pages/resources/ResourcesPage.qml", ""),
    ("resources/shaders", "resource_shaders", "shaderpack", "resources", "pages/resources/ResourcesPage.qml", ""),
    ("resources/maps", "resource_maps", "world", "resources", "pages/resources/ResourcesPage.qml", ""),
    ("resources/browse", "mod_browser_search", "search", "resources", "pages/resources/ResourcesPage.qml", ""),
    ("resources/browse/results", "resource_search_title", "search", "resources/browse",
     "pages/resources/ResourcesPage.qml", ""),
    ("resources/browse/detail", "resource_detail_title", "file", "resources/browse",
     "pages/resources/ResourcesPage.qml", ""),
    ("resources/updates", "mod_update_dialog_title", "refresh", "resources", "pages/resources/ResourcesPage.qml", ""),
    # ── 4.4 服务器 ─────────────────────────────────────────────
    ("servers", "server_tab", "server", "", "pages/servers/ServersPage.qml", ""),
    ("servers/detail", "server_detail_title", "info", "servers", "pages/servers/ServersPage.qml", ""),
    ("servers/detail/console", "server_log_title", "monitor", "servers/detail", "pages/servers/ServersPage.qml", ""),
    ("servers/detail/config", "server_config_panel_btn", "slider", "servers/detail", "pages/servers/ServersPage.qml", ""),
    ("servers/detail/mods", "server_mods_title", "mods", "servers/detail", "pages/servers/ServersPage.qml", ""),
    ("servers/detail/packs", "server_packs_title", "resourcepack", "servers/detail", "pages/servers/ServersPage.qml", ""),
    ("servers/install", "server_install_title", "download", "servers", "pages/servers/ServersPage.qml", ""),
    ("servers/modpack", "ach_server_modpack_server", "modpack", "servers", "pages/servers/ServersPage.qml", ""),
    # ── 4.5 联机 ───────────────────────────────────────────────
    ("online", "online_title", "online", "", "pages/online/OnlinePage.qml", "online_description"),
    ("online/create", "online_create_title", "add", "online", "pages/online/OnlinePage.qml", ""),
    ("online/join", "online_join_title", "door", "online", "pages/online/OnlinePage.qml", ""),
    ("online/lan", "online_discover_title", "network", "online", "pages/online/OnlinePage.qml", ""),
    ("online/log", "online_output_title", "note", "online", "pages/online/OnlinePage.qml", ""),
    # ── 4.6 存档 ───────────────────────────────────────────────
    ("backups", "backup_world", "saves", "", "pages/backups/BackupsPage.qml", ""),
    ("backups/world", "backup_world_detail_title", "info", "backups", "pages/backups/BackupsPage.qml", ""),
    ("backups/world/backup", "backup_detail_title", "history", "backups/world", "pages/backups/BackupsPage.qml", ""),
    ("backups/settings", "backup_settings_title", "settings-gear", "backups", "pages/backups/BackupsPage.qml", ""),
    # ── 4.7 音乐 ───────────────────────────────────────────────
    ("music", "tab_music", "music", "", "pages/music/MusicPage.qml", ""),
    ("music/playlist", "music_playlists", "note", "music", "pages/music/MusicPage.qml", ""),
    ("music/online", "music_online_title", "online", "music", "pages/music/MusicPage.qml", ""),
    ("music/online/results", "music_search_title", "search", "music/online", "pages/music/MusicPage.qml", ""),
    ("music/fx", "music_sound_effect", "equalizer", "music", "pages/music/MusicPage.qml", ""),
    ("music/lyrics", "music_desktop_lyric", "lyrics", "music", "pages/music/MusicPage.qml", ""),
    ("music/account", "settings_wy_login_title", "account", "music", "pages/music/MusicPage.qml", ""),
    ("music/mini", "music_mini_mode", "play", "music", "pages/music/MusicPage.qml", ""),
    # ── 4.8 工具箱 ─────────────────────────────────────────────
    ("tools", "tab_tools", "tool", "", "pages/tools/ToolsPage.qml", ""),
    ("tools/junk", "tool_clean_junk_title", "trash", "tools", "pages/tools/ToolsPage.qml", "tool_clean_junk_desc"),
    ("tools/port", "tool_port_title", "target", "tools", "pages/tools/ToolsPage.qml", "tool_port_desc"),
    ("tools/hash", "tool_hash_title", "file", "tools", "pages/tools/ToolsPage.qml", "tool_hash_desc"),
    ("tools/coord", "tool_coord_title", "world", "tools", "pages/tools/ToolsPage.qml", "tool_coord_desc"),
    ("tools/fortune", "tool_fortune_title", "dice", "tools", "pages/tools/ToolsPage.qml", "tool_fortune_desc"),
    ("tools/fact", "tool_fact_title", "bulb", "tools", "pages/tools/ToolsPage.qml", "tool_fact_desc"),
    ("tools/quiz", "tool_quiz_title", "guide", "tools", "pages/tools/ToolsPage.qml", "tool_quiz_desc"),
    ("tools/download", "tool_download_title", "download", "tools", "pages/tools/ToolsPage.qml", "tool_download_desc"),
    # ── 4.9 成就 ───────────────────────────────────────────────
    ("achievements", "ach_stats_title", "trophy", "", "pages/achievements/AchievementsPage.qml", ""),
    ("achievements/category", "ach_category_title", "folder", "achievements", "pages/achievements/AchievementsPage.qml", ""),
    ("achievements/detail", "ach_detail_title", "target", "achievements", "pages/achievements/AchievementsPage.qml", ""),
    ("achievements/cloud", "ach_cloud_title", "upload", "achievements", "pages/achievements/AchievementsPage.qml", ""),
    # ── 4.10 AGENT ─────────────────────────────────────────────
    ("agent", "tab_agent", "agent", "", "pages/agent/AgentPage.qml", "agent_desc"),
    ("agent/sessions", "agent_sessions", "history", "agent", "pages/agent/AgentPage.qml", ""),
    ("agent/models", "agent_provider", "memory", "agent", "pages/agent/AgentPage.qml", ""),
    ("agent/tools", "agent_tools_title", "tool", "agent", "pages/agent/AgentPage.qml", ""),
    ("agent/skills", "skills_title", "guide", "agent", "pages/agent/AgentPage.qml", ""),
    # ── 4.11 基岩版（仅 Windows） ──────────────────────────────
    ("bedrock", "bedrock_installed", "bedrock", "", "pages/bedrock/BedrockPage.qml", ""),
    ("bedrock/detail", "bedrock_detail_title", "info", "bedrock", "pages/bedrock/BedrockPage.qml", ""),
    ("bedrock/download", "bedrock_available", "download", "bedrock", "pages/bedrock/BedrockPage.qml", ""),
    # ── 4.12 设置 ──────────────────────────────────────────────
    ("settings", "settings", "settings-gear", "", "pages/settings/SettingsPage.qml", ""),
    ("settings/launcher", "settings_tab_launcher", "rocket", "settings", "pages/settings/SettingsPage.qml", ""),
    ("settings/account", "account_manager_title", "account", "settings", "pages/settings/SettingsPage.qml",
     "account_manager_desc"),
    ("settings/java", "settings_java_title", "bolt", "settings", "pages/settings/SettingsPage.qml", ""),
    ("settings/ai", "settings_tab_ai", "agent", "settings", "pages/settings/SettingsPage.qml", ""),
    ("settings/theme", "settings_theme", "theme", "settings", "pages/settings/SettingsPage.qml", ""),
    ("settings/plugin", "plugin_manager_title", "plugin", "settings", "pages/settings/SettingsPage.qml", ""),
    ("settings/log", "launcher_log", "note", "settings", "pages/settings/SettingsPage.qml", ""),
    ("settings/about", "about_title", "info", "settings", "pages/settings/SettingsPage.qml", ""),
    # ── 开发自查：组件画廊（阶段 2 任务 2.16 / 2.17）─────────────────
    # **刻意不放进 `_NAV_ORDER`，并且给一个非空 `parent`**：本表的所有**无父路由**
    # 都会被 `NavBridge` 当成一级导航项收进 `_roots`（构造时那段"表里没写进
    # _NAV_ORDER 的根路由也要能出现在导航里"），而 `nav_items()` 会跳过有父的路由。
    # 所以 `parent="settings"` 这一格就是"进得去、但不在 12 个一级导航里露出来"的
    # 实现方式：它是一个开发自查页，不该占一个用户可见的导航位。
    # 进入方式二选一（本任务选深链）：`fmcl://dev/gallery` 或 `Nav.push("dev/gallery")`；
    # `parent` 与 id 的层级一致（`dev/gallery` 是 level 1，`settings` 是 level 0），
    # 满足 `tests/test_nav_bridge.py` 钉住的"id 层级与 parent 链严格一致"这条不变量。
    ("dev/gallery", "dev_gallery_title", "guide", "settings", "pages/dev/Gallery.qml", ""),
)

#: 一级导航项的顺序（02 §3.1 的骨架图，从上到下）。
_NAV_ORDER: Tuple[str, ...] = (
    "home", "versions", "resources", "servers", "online", "backups",
    "music", "tools", "achievements", "agent", "bedrock", "settings",
)


def build_default_routes() -> List[Route]:
    """按表构造内置路由（每次调用返回新列表，测试可以安全地改）。

    "仅某平台"的领域在这里打上 `platform` 标记（表里不写，免得 76 行都要多带一列）。
    """
    routes: List[Route] = []
    for row in _ROUTE_TABLE:
        route = Route(*row)
        platform = _PLATFORM_ONLY_DOMAINS.get(route.id.split("/")[0], "")
        routes.append(replace(route, platform=platform) if platform else route)
    return routes


def qml_root() -> Path:
    """`qml/` 目录（开发态 → 打包态）。与 `runtime_bridge.qmlSourcePath` 同一规则。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "app_qml"
    return Path(__file__).resolve().parents[2] / "qml"


def qml_url(relative: str) -> str:
    """相对 `qml/` 的路径 → `file:///` 绝对 URL。

    为什么要绝对 URL：QML 的相对路径是**相对写这句表达式的那个文件**解析的，
    而 `PageStack.qml` 在 `qml/shell/` 下，写成 `"pages/…"` 会解析到
    `qml/shell/pages/…`（图标资源上已经踩过一次同族的坑，见 2.10 的 README）。
    """
    return QUrl.fromLocalFile(str(qml_root() / relative)).toString()


#: 最近构造的实例 —— 让 `Shell` 在"装配方忘了显式注入"时也能找到 Nav。
#: 装配路径上只有一个 Nav（`main_qml.register_bridges` 的 `CONTEXT_BRIDGES`），
#: 所以"最近构造"= "当前那个"。显式注入（构造参数或 `use_engine`）永远优先。
_current: Optional["NavBridge"] = None


def current_nav() -> Optional["NavBridge"]:
    """当前进程里的 Nav 实例（没有则 None）。"""
    return _current


class NavBridge(QObject):
    """上下文属性 `Nav`。"""

    #: 前台页面变了（push / goBack / goHome / reset 都会发）。
    routeChanged = Signal(str)
    #: 面包屑变了（与 `routeChanged` 同时发，但深链等场景下调用方可能只关心它）。
    breadcrumbChanged = Signal()
    #: 导航失败（未知路由 / 深链格式错 / 插件路由非法），带人话原因。
    navFailed = Signal(str)
    #: 插件路由登记成功。
    pluginRouteAdded = Signal(str)

    def __init__(
        self,
        routes: Optional[Sequence[Route]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        """
        Args:
            routes: 覆盖路由表（测试用）。默认用 02 §4 的内置表。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._routes: Dict[str, Route] = {}
        self._roots: List[str] = []
        for route in (build_default_routes() if routes is None else list(routes)):
            self._routes[route.id] = route
        for route_id in _NAV_ORDER:
            if route_id in self._routes:
                self._roots.append(route_id)
        # 表里没写进 _NAV_ORDER 的根路由（测试自造的表）也要能出现在导航里。
        for route in self._routes.values():
            if not route.parent and route.id not in self._roots:
                self._roots.append(route.id)

        #: 栈：`[(route_id, params), …]`，最后一项是前台页面。
        self._stack: List[Tuple[str, Dict[str, Any]]] = []

        global _current
        _current = self

    # ─── 只读属性 ───────────────────────────────────────────

    @Property(str, notify=routeChanged)
    def currentRoute(self) -> str:  # noqa: N802 - QML 属性名
        """前台路由 id；栈空时是空串（`reset()` 之后立刻会回到首页，见该槽的说明）。"""
        return self._stack[-1][0] if self._stack else ""

    @Property(str, notify=routeChanged)
    def currentTitleKey(self) -> str:  # noqa: N802
        """前台路由的标题 i18n 键。

        给的是**键**而不是译文：Python 侧翻好的字符串不会跟着语言切换重算
        （契约 §5.2 的同一理由），QML 侧请写 `Tr.map[Nav.currentTitleKey]`。
        """
        route = self._current_route()
        return route.title_key if route else ""

    @Property(str, notify=routeChanged)
    def currentDescriptionKey(self) -> str:  # noqa: N802
        route = self._current_route()
        return route.description_key if route else ""

    @Property(str, notify=routeChanged)
    def currentQmlUrl(self) -> str:  # noqa: N802
        """前台页面的 QML 文件 URL（`PageStack` 镜像栈时用）。"""
        route = self._current_route()
        return "" if route is None else self._url_for(route)

    @Property("QVariantMap", notify=routeChanged)
    def currentParams(self) -> Dict[str, Any]:  # noqa: N802
        """前台路由的参数（深链的查询串 / `push` 传进来的那份）。"""
        return dict(self._stack[-1][1]) if self._stack else {}

    @Property("QVariantList", notify=breadcrumbChanged)
    def breadcrumb(self) -> List[Dict[str, Any]]:
        """栈的映射：从栈底到前台，每项一项。`back_count` = 退回该项要点几次返回。"""
        total = len(self._stack)
        items: List[Dict[str, Any]] = []
        for index, (route_id, params) in enumerate(self._stack):
            route = self._routes[route_id]
            item = route.as_map()
            item["params"] = dict(params)
            item["qml_url"] = self._url_for(route)
            item["back_count"] = total - 1 - index
            item["is_current"] = index == total - 1
            items.append(item)
        return items

    @Property(bool, notify=routeChanged)
    def canGoBack(self) -> bool:  # noqa: N802
        """栈里是否还有上一页。"""
        return len(self._stack) > 1

    @Property(int, notify=routeChanged)
    def depth(self) -> int:
        """栈深（前台页面的层数 + 1）。"""
        return len(self._stack)

    @Property(int, constant=True)
    def depthLimit(self) -> int:  # noqa: N802
        return MAX_DEPTH

    @Property(int, notify=routeChanged)
    def routeCount(self) -> int:  # noqa: N802
        """已登记的路由总数（含插件路由）—— 冒烟测试与诊断用。"""
        return len(self._routes)

    # ─── 导航槽 ─────────────────────────────────────────────

    @Slot(str, result=bool)
    @Slot(str, "QVariantMap", result=bool)
    def push(self, routeId: str, params: Optional[Dict[str, Any]] = None) -> bool:  # noqa: N802
        """压入一页。

        两个重载（`push(id)` / `push(id, params)`）是给 QML 用的：Qt 不做默认参数，
        只注册 2 参签名的话 `Nav.push("home")` 会报"参数不足"。

        **重复 push 同一路由**：
        * 路由与参数都一样 → 完全不动（不新增栈帧、不发信号）—— 点两下同一个导航项
          不该出现两页一模一样的页面；
        * 路由相同但**参数不同** → 正常压栈（例如连着看两个版本的详情，返回要能回到上一个）。
        """
        return self._push(routeId, params or {}, announce=True)

    @Slot()
    def goBack(self) -> bool:  # noqa: N802
        """退一页。已经在栈底时**什么也不做**（返回 false）—— 那不是错误，不报 navFailed。"""
        if len(self._stack) <= 1:
            logger.debug("已在栈底，goBack 无事可做（当前栈深 %d）", len(self._stack))
            return False
        self._stack.pop()
        self._announce()
        return True

    @Slot(int)
    def goBackTo(self, steps: int) -> bool:  # noqa: N802
        """退 `steps` 页（面包屑点击用：点第 i 项 = 退 `back_count` 页）。越界会被夹到合法范围。"""
        try:
            count = int(steps)
        except (TypeError, ValueError):
            self._fail(f"goBackTo 的步数不是整数: {steps!r}")
            return False
        if count <= 0:
            return False
        count = min(count, len(self._stack) - 1)
        if count <= 0:
            return False
        del self._stack[len(self._stack) - count:]
        self._announce()
        return True

    @Slot()
    def goHome(self) -> bool:  # noqa: N802
        """去首页（**压栈**，保留返回路径）；已经在首页时不动。"""
        return self._push(HOME_ROUTE, {}, announce=True)

    @Slot()
    def reset(self) -> bool:  # noqa: N802
        """回到"启动时的状态"：栈重置为 `[首页]`。

        与 `goHome()` 的区别：`goHome()` 是"去首页"（能返回），`reset()` 是"推倒重来"
        （退不回任何页面），给"切换账号 / 退出登录 / 恢复默认布局"这类场景用。
        **刻意不把栈清空**：空栈等于界面上一页都没有，那正是"点了空白"。
        """
        if len(self._stack) == 1 and self._stack[0][0] == HOME_ROUTE:
            return False
        self._stack = [(HOME_ROUTE, {})]
        self._announce()
        return True

    # ─── 查询槽 ─────────────────────────────────────────────

    @Slot(str, result="QVariantMap")
    def resolve(self, routeId: str) -> Dict[str, Any]:
        """查一条路由的描述；未知 id 返回**空表**（查询不是导航，不报 navFailed）。"""
        route = self._routes.get(str(routeId or "").strip())
        if route is None:
            return {}
        info = route.as_map()
        info["available"] = self._available(route)
        return info

    @Slot(result="QVariantList")
    def routes(self) -> List[Dict[str, Any]]:
        """全部路由（内置在前、插件在后），每项带 `qml_url` 与 `available`。"""
        out: List[Dict[str, Any]] = []
        for route in self._routes.values():
            item = route.as_map()
            item["qml_url"] = self._url_for(route)
            item["available"] = self._available(route)
            out.append(item)
        return out

    # ─── 深链（QML 与 Agent / 插件都会用） ───────────────────

    @Slot(str, result=bool)
    def openDeepLink(self, url: str) -> bool:  # noqa: N802
        """解析 `fmcl://versions/detail?version=1.21.4` 并跳转。

        接受两种写法（都由 QUrl 的语义决定，不自己拍脑袋）：
        `fmcl://versions/detail`（host + path）与 `fmcl:///versions/detail`（全在 path）。
        只做**结构**校验（协议 / 路径 / 路由是否存在）；参数里的业务校验
        （"这个版本装没装"）是页面与服务的事（红线 2）。
        """
        parsed = self._parse_deep_link(url)
        if parsed is None:
            return False
        route_id, params = parsed
        return self._push(route_id, params, announce=True)

    # ─── 插件路由（02 §7 的 UI API v2） ─────────────────────

    @Slot(str, str, str, str, result=bool)
    def registerPluginRoute(self, routeId: str, titleKey: str, qmlUrl: str, owner: str) -> bool:  # noqa: N802
        """登记一条插件页面路由。**阶段 2 只登记，不加载插件**（插件加载是 3.27）。

        按 02 §7 的 UI API v2 设计：

        * 插件页面必须是 **QML 组件**（`qml_url` 非空），签名为
          `(id, title_key, qml_url, owner)`；
        * 旧插件（`ui_api` 缺省=1，那套 `get_tab_ui(parent)` 传 tkinter 容器）
          **无法提供 QML 组件** → 这里明确拒绝并 `navFailed`，让设置页能显示
          "该插件界面不受支持"，而不是静默少一项（非 UI 钩子不受影响，走的是别的入口）；
        * id 必须带 `plugin/` 前缀（防与内置路由及别的插件撞名）；
        * 同一属主重复登记同一条 → 幂等成功（插件重新加载时不该报错）。
        """
        rid = str(routeId or "").strip()
        if not rid.startswith(PLUGIN_ROUTE_PREFIX) or len(rid) <= len(PLUGIN_ROUTE_PREFIX):
            self._fail(f"插件路由 id 必须以 {PLUGIN_ROUTE_PREFIX!r} 开头且不能只有前缀: {rid!r}")
            return False
        if rid in self._routes:
            existing = self._routes[rid]
            if existing.source == "plugin" and existing.owner == owner and existing.qml_file == qmlUrl:
                return True
            self._fail(f"插件路由 id 已被占用: {rid}（占用者 {existing.owner or existing.source}）")
            return False
        if not str(qmlUrl or "").strip():
            self._fail(
                f"插件 {owner or '?'} 未提供 qml_url：旧版 UI API（v1，tkinter 容器）在 QML 下不受支持，"
                "该插件界面不加载（非 UI 功能不受影响）"
            )
            return False

        key = str(titleKey or "").strip() or rid  # 没有键就拿 id 当键：界面显示 id，看得见、可排查
        route = Route(
            id=rid,
            title_key=key,
            icon="plugin",
            parent="",
            qml_file=str(qmlUrl),
            description_key="",
            source="plugin",
            owner=str(owner or ""),
        )
        self._routes[rid] = route
        if rid not in self._roots:
            self._roots.append(rid)  # 插件页面按 02 §7 是一级导航项
        logger.info("插件路由已登记：%s（属主 %s，标题键 %s）", rid, owner, key)
        self.pluginRouteAdded.emit(rid)
        return True

    # ─── 给 Shell 用的普通方法（不是槽：一级导航项的唯一入口是 Shell.navItems()） ──

    def nav_items(self) -> List[Dict[str, Any]]:
        """一级导航项：`[{id, title_key, icon, source}, …]`（内置 12 项 + 插件项）。

        当前平台不支持的领域（如非 Windows 上的基岩版）**不出现**在这里 ——
        与旧界面"仅 Windows 才建这个页"一致，也让用户看不到点不开的项。
        """
        items: List[Dict[str, Any]] = []
        for route_id in self._roots:
            route = self._routes.get(route_id)
            if route is None or route.parent or not self._available(route):
                continue
            items.append(
                {
                    "id": route.id,
                    "title_key": route.title_key,
                    "icon": route.icon,
                    "source": route.source,
                }
            )
        return items

    def stack_ids(self) -> List[str]:
        """当前栈的路由 id（诊断与测试用）。"""
        return [route_id for route_id, _ in self._stack]

    # ─── 内部 ───────────────────────────────────────────────

    def _current_route(self) -> Optional[Route]:
        return self._routes.get(self._stack[-1][0]) if self._stack else None

    def _available(self, route: Route) -> bool:
        """这条路由在当前平台上能不能用（空 `platform` = 全平台）。"""
        return not route.platform or route.platform == host_platform()

    def _url_for(self, route: Route) -> str:
        """路由 → 可供 `StackView.push()` 直接用的 URL。

        插件页的 URL 由插件自己给（相对路径对插件包没有意义，见 3.27），原样透出；
        内置页一律换算成 `file:///` 绝对 URL。
        """
        return route.qml_file if route.source == "plugin" else qml_url(route.qml_file)

    def _push(self, routeId: str, params: Dict[str, Any], announce: bool) -> bool:
        route_id = str(routeId or "").strip()
        route = self._routes.get(route_id)
        if route is None:
            self._fail(f"未知路由: {route_id!r}（已登记 {len(self._routes)} 条）")
            return False
        if not self._available(route):
            self._fail(
                f"{route_id} 仅 {route.platform} 平台支持（当前平台 {host_platform()}）—— "
                "该领域的页面不会打开"
            )
            return False

        clean = self._clean_params(params)
        if self._stack and self._stack[-1] == (route_id, clean):
            logger.debug("重复 push 同一路由与同一参数，忽略：%s", route_id)
            return True

        if len(self._stack) >= MAX_DEPTH:
            dropped = self._stack.pop(0)
            logger.warning(
                "栈深达到上限 %d，已丢弃栈底 %s（保留离当前最近的 %d 帧）",
                MAX_DEPTH, dropped[0], MAX_DEPTH - 1,
            )
        self._stack.append((route_id, clean))
        if announce:
            self._announce()
        return True

    def _clean_params(self, params: Any) -> Dict[str, Any]:
        """参数归一化成 `{str: str}`。

        QML 的 `QVariantMap` 到 Python 侧可能是 `dict`、`QVariantMap` 或 None；
        值统一成字符串（深链参数本来就是字符串，页面按需自己转）。
        """
        if not params:
            return {}
        try:
            items = dict(params).items()
        except (TypeError, ValueError):
            logger.warning("路由参数不是键值表，已忽略: %r", params)
            return {}
        out: Dict[str, Any] = {}
        for key, value in items:
            if value is None:
                continue
            out[str(key)] = value if isinstance(value, (int, float, bool)) else str(value)
        return out

    def _announce(self) -> None:
        current = self.currentRoute
        self.routeChanged.emit(current)
        self.breadcrumbChanged.emit()
        logger.debug("导航到 %s（栈深 %d）", current or "<空>", len(self._stack))

    def _fail(self, reason: str) -> None:
        logger.error("导航失败：%s", reason)
        self.navFailed.emit(reason)

    def _parse_deep_link(self, url: str) -> Optional[Tuple[str, Dict[str, str]]]:
        """`fmcl://…` → `(route_id, params)`；不合法返回 None（并已发 navFailed）。"""
        text = str(url or "").strip()
        if not text:
            self._fail("深链是空的")
            return None
        qurl = QUrl(text)
        if qurl.scheme().lower() != DEEP_LINK_SCHEME:
            self._fail(f"深链协议必须是 {DEEP_LINK_SCHEME}://，收到 {qurl.scheme() or '<空>'!r}：{text}")
            return None
        # host 与 path 都可能是路由的一部分：`fmcl://versions/detail` 里 host=versions。
        # QUrl 会把 host 归一成小写（URL 语义如此），path 保持原样 —— 路由 id 全是小写，
        # 所以只有 path 写错大小写时才会落到"未知路由"，那是我们要的行为。
        head = qurl.host()
        tail = qurl.path().strip("/")
        segments = [part for part in [head] + tail.split("/") if part]
        if not segments:
            self._fail(f"深链里没有路由: {text}")
            return None
        route_id = "/".join(segments)
        params: Dict[str, str] = {}
        for key, value in QUrlQuery(qurl).queryItems(QUrl.ComponentFormattingOption.FullyDecoded):
            params[str(key)] = str(value)
        return route_id, params


__all__ = [
    "DEEP_LINK_SCHEME",
    "HOME_ROUTE",
    "MAX_DEPTH",
    "PLUGIN_ROUTE_PREFIX",
    "NavBridge",
    "Route",
    "build_default_routes",
    "current_nav",
    "qml_root",
    "qml_url",
]
