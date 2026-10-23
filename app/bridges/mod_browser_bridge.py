"""在线资源浏览桥 —— QML 侧的 `ModBrowser` 上下文属性（阶段 3 任务 3.7，对照表 L-56）。

## 它在链路的哪一环

    qml/pages/resources/BrowsePage.qml          （资源浏览：类型 + 关键词 + 筛选 + 结果 + 分页 + 安装）
    qml/pages/resources/BrowseDetailPage.qml    （资源详情：版本选择 + 依赖预览 + 安装）
        ↓ 取属性 / 调槽
    app/bridges/mod_browser_bridge.py（本模块，**零业务规则**）
        ↓ 转发
    services/mod_browser_service.py（双源搜索 / 安装 / 详情页三件事）
        ↓ 委托
    modrinth.py / curseforge.py（网络与下载）

旧实现是一个 1248 行的 Tk 窗口（`ui/windows/mod_browser.py`）：三标签页 + 页内搜索 +
页内结果 + 卡片直接安装。**服务层在阶段 1/2 已经迁完**（`services/mod_browser_service.py`
634 行，D-91 / D-93 / D-100 / D-103 四条缺陷都已修好并被
`tests/test_mod_browser_staleness.py` 钉住），所以本任务的正文是**桥 + 两页 QML**。

## 用户裁决

* **2026-10-17：按 IA 拆页** —— 原计划三页（在线浏览 → 搜索结果 → 资源详情），
  详情页本轮一并做（版本选择 + 依赖预览 + 按所选版本安装，旧实现没有这一页）。
* **2026-10-23：并回两页** —— 用户看到实物后指出「搜索和资源搜索两个页面重复」：
  L1 只有一张表单，L2 又是一个一模一样的工具条。现在**搜索与结果同页**
  （旧窗口也是一屏），只保留 `resources/browse` 与 `resources/browse/detail` 两层。
* **来源不切换**：Modrinth + CurseForge **合并搜索**，卡片上标来源（与旧实现逐字一致）。
* 验证走精简档（1 个桥测试文件 + 1 个 QML 判据脚本 + 截图 + 全量 pytest + 五道闸门）。

## 两个页面的状态怎么共享

桥是**单例**，两页共用同一份会话状态（与旧窗口"三个标签页各记一份"等价，只是键换成了
`tab_key`）：

* `resources/browse` 入场时调 :meth:`ModBrowserBridge.activate`（只记上下文、不搜索），
  用户点「搜索」时才走 :meth:`search` / :meth:`aiSearch`，结果与分页**在同一页**渲染；
* `resources/browse/detail` 入场时调 :meth:`ModBrowserBridge.openDetail`。

**世代号（D-93 的延续）**：每个标签页一个只增不减的 `seq`，搜索 / AI 搜索启动时自增；
worker 与投递回调都带着自己那一代，对不上就整批丢弃（连缓存都不写）。这条约定旧实现
有、`tests/test_mod_browser_staleness.py` 钉着服务侧的同形实现，桥侧照抄。

## 线程约定

搜索 / AI 搜索 / 详情 / 安装全部走 `ResourceService` 同款的 `TaskRunner.submit`。
`TaskRunner` 的回调虽然会切回主线程（有 scheduler 时），但**没有 scheduler 时是内联执行**，
所以这里一律按"回调可能在工作线程"处理：只排队 + emit `posted`，
真正落属性的 `_deliver()` 住在主线程（跨线程 emit 是队列投递）。
契约第六节决策 10 禁止 `QMetaObject.invokeMethod`（var 签名不匹配会静默失败），见闸门 R7。
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from services.browse_common import (
    DEFAULT_PAGE_SIZE,
    browsable_count,
    format_downloads,
    loader_tags_text,
)
from services.i18n_service import _
from services.mod_browser_service import (
    CLIENT_TABS,
    MOD_LOADER_COMPAT_MAP,
    TAB_MODS,
    TAB_RESOURCE_PACKS,
    TAB_SHADERS,
)

logger = logging.getLogger("app.bridges.mod_browser_bridge")

#: 契约第四节冻结的 QML 注册名（入口 `main_qml.CONTEXT_BRIDGES` 注册成上下文属性）。
QML_NAME = "ModBrowser"

#: 后台任务名。
TASK_SEARCH = "mod_browser.search"
TASK_DETAIL = "mod_browser.detail"
TASK_INSTALL = "mod_browser.install"

#: 每页条数（旧 `ModBrowserWindow.PAGE_SIZE = 10`）。
PAGE_SIZE = DEFAULT_PAGE_SIZE

#: 标签页 → 图标名（`qml/assets/icons/*.svg` 里**存在**的名字；与 3.6 资源页同一套）。
TAB_ICONS: Dict[str, str] = {
    TAB_MODS: "mods",
    TAB_RESOURCE_PACKS: "resourcepack",
    TAB_SHADERS: "shaderpack",
}

#: 标签页 → 文案键（页签条与空态用）。
TAB_LABEL_KEYS: Dict[str, str] = {
    TAB_MODS: "mod_browser_tab_mods",
    TAB_RESOURCE_PACKS: "mod_browser_tab_resourcepacks",
    TAB_SHADERS: "mod_browser_tab_shaders",
}

#: 标签页 → "没有结果"的文案键（旧 `_render_tab_results` 里那三个）。
TAB_EMPTY_KEYS: Dict[str, str] = {
    TAB_MODS: "mod_browser_no_mods",
    TAB_RESOURCE_PACKS: "mod_browser_no_rp",
    TAB_SHADERS: "mod_browser_no_shaders",
}

#: 标签页 → 安装子目录（与 `services.mod_browser_service.INSTALL_SUBDIRS` 同值；
#: 这里只用于给目录选择框一个默认路径）。
TAB_SUBDIRS: Dict[str, str] = {
    TAB_MODS: "mods",
    TAB_RESOURCE_PACKS: "resourcepacks",
    TAB_SHADERS: "shaderpacks",
}

#: 加载器筛选的候选（旧 `loader_options`）：`(文案键, 值)`，值 `None` = 全部。
LOADER_CHOICES: tuple = (
    ("mod_browser_filter_all_loaders", None),
    ("mod_loader_forge", "forge"),
    ("mod_loader_fabric", "fabric"),
    ("mod_loader_neoforge", "neoforge"),
    ("mod_loader_quilt", "quilt"),
)

#: 加载器值的合法集（来源版本的加载器不在其中时按"全部"处理 —— 旧实现同一判定）。
LOADER_VALUES: tuple = tuple(value for _, value in LOADER_CHOICES if value)


def _new_tab_state(game_version: Optional[str], loader: Optional[str]) -> Dict[str, Any]:
    """一个标签页的初始状态（字段对应旧窗口 `_tab_states[tab_key]` 里那几个）。"""
    return {
        "query": "",
        "game_version": game_version,
        "loader": loader,
        "page": 1,
        "hits": [],
        "total": 0,
        "keywords": [],
        "ai": False,
        "state": "idle",
        "error": "",
        "seq": 0,
    }


class ModBrowserBridge(QObject):
    """上下文属性 `ModBrowser`。"""

    #: 列表 / 页码 / 计数 / 筛选 / 状态等**一次快照**里的东西变了。
    itemsChanged = Signal()
    #: 详情页的东西变了（项目信息 / 版本列表 / 依赖）。
    detailChanged = Signal()
    #: 忙（安装中）状态变了。
    busyChanged = Signal()
    #: 安装进度文案变了。
    progressChanged = Signal()
    #: 状态条文案（**已翻译**；页面转给 `Shell.setStatus`）。
    statusMessage = Signal(str, str)
    #: 一条提示（安装结果 / AI 未登录）—— 页面按 level 转成 Toast。
    noticeRequested = Signal(str, str)
    #: 要求页面弹目录选择框（资源包/光影安装用）。
    #: 参数 = 标签页键 / 默认路径 / 默认路径的 `QUrl`（`FolderDialog.currentFolder` 只收 url）。
    directoryRequested = Signal(str, str, "QUrl")
    #: **内部投递信号**：worker 线程只排队并 emit 它，真正落属性的是主线程上的
    #: `_deliver()`（与 `ResourcesBridge.posted` 同一套）。
    posted = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._service: Any = None
        self._context: Any = None
        self._tasks: Any = None
        self._launcher: Any = None
        self._callbacks: Dict[str, Any] = {}
        self._config: Any = None

        #: 从哪个版本进来（空串 = "全部版本"入口）
        self._version_id = ""
        self._default_game_version: Optional[str] = None
        self._source_loader: Optional[str] = None
        #: 来源加载器的 API 兼容值（`compat_loader`，旧 `_search_loader`）
        self._search_loader: Optional[str] = None

        #: 当前标签页（结果页/详情页共用）
        self._tab = TAB_MODS
        self._states: Dict[str, Dict[str, Any]] = {
            key: _new_tab_state(None, None) for key in CLIENT_TABS
        }
        #: 游戏版本筛选候选（后台加载一次；空列表时下拉只有"全部版本"）
        self._game_versions: List[str] = []
        self._versions_loaded = False

        self._busy = False
        self._progress_text = ""
        self._progress_value = 0.0
        self._active_task = ""

        #: 详情页状态
        self._detail: Dict[str, Any] = {
            "state": "idle",
            "seq": 0,
            "error": "",
            "project": {},
            "versions": [],
            "selected": "",
            "deps": [],
            "deps_loading": False,
            "source": "modrinth",
            "tab": TAB_MODS,
        }

        #: 待确认目录的安装（资源包/光影）
        self._pending_install: Optional[Dict[str, Any]] = None

        self._handles: Dict[str, Any] = {}
        self._pending: List[Dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        self.posted.connect(self._deliver)

    # ─── 装配期注入 ─────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        """`main_qml.register_bridges` 在注册前调它：取服务 / 配置 / 任务调度器。

        **不在这里解析"来源版本"**（那是路由参数的活，见 `activate`）：
        装配早于 `routeParams` 赋值，提前猜一个版本就会盖掉路由给的版本
        —— 阶段 3.6 的第一条验收缺陷就是它（`16` §二十三）。
        """
        self._context = context
        self._service = self._service_of(context, "mod_browser")
        if self._service is None:
            logger.warning("模组浏览服务不可用：在线浏览页只会显示错误态")
            return
        self._tasks = getattr(context, "tasks", None)
        if self._tasks is None:
            logger.warning("上下文里没有 tasks（任务调度器）：在线搜索与安装将不可用")
        self._launcher = self._launcher_of(context)
        self._callbacks = self._build_callbacks(self._launcher)
        try:
            self._config = context.config if context is not None else None
        except Exception as e:  # noqa: BLE001 - 配置拿不到只影响 AI 搜索的可用性
            logger.warning("取配置失败（AI 搜索将不可用）: %s", e)

    @staticmethod
    def _service_of(context: Any, name: str) -> Any:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("在线浏览取服务 %s 失败: %s", name, e)
            return None

    @staticmethod
    def _launcher_of(context: Any) -> Any:
        """取核心启动器（安装目录解析要它的 `get_minecraft_dir` / 已装版本）。"""
        if context is None:
            return None
        try:
            return context.try_get("launcher")
        except Exception as e:  # noqa: BLE001
            logger.warning("取 launcher 失败: %s", e)
            return None

    @staticmethod
    def _build_callbacks(launcher: Any) -> Dict[str, Any]:
        """`resolve_install_dir` 要的两个回调 + AI 搜索的 token 回调。

        取不到就留空：安装目录会退到全局目录，AI 搜索会退到 `config.jdz_token`。
        """
        out: Dict[str, Any] = {}
        if launcher is None:
            return out
        for key, attr in (
            ("get_minecraft_dir", "get_minecraft_dir"),
            ("get_installed_versions", "get_installed_versions"),
            ("get_installed_version_ids", "get_installed_version_ids"),
            ("get_jdz_token", "get_jdz_token"),
        ):
            fn = getattr(launcher, attr, None)
            if callable(fn):
                out[key] = fn
        return out

    def detach(self) -> None:
        """退出/重建引擎：取消在跑的任务（否则回调会投递给正在销毁的对象）。"""
        self.cancel()

    # ─── 只读属性：上下文 ───────────────────────────────────

    @Property(bool, constant=True)
    def available(self) -> bool:
        """服务与调度器都在不在（缺席时页面走错误态，而不是绑一堆 undefined）。"""
        return self._service is not None

    @Property(str, notify=itemsChanged)
    def versionId(self) -> str:  # noqa: N802
        """从哪个版本进来的（空串 = "全部版本"入口）。"""
        return self._version_id

    @Property(str, notify=itemsChanged)
    def defaultGameVersion(self) -> str:  # noqa: N802
        """来源版本的游戏版本（筛选项的默认值；空串 = 不筛）。"""
        return str(self._default_game_version or "")

    @Property(str, notify=itemsChanged)
    def sourceLoader(self) -> str:  # noqa: N802
        """来源版本的加载器（页头那行信息用；空串 = 没识别出来）。"""
        return str(self._source_loader or "")

    @Property(bool, notify=itemsChanged)
    def aiAvailable(self) -> bool:  # noqa: N802
        """AI 搜索能不能用（= 配了 `jdz_token`）。"""
        token = self._ai_token()
        return bool(token)

    # ─── 只读属性：标签页 / 筛选 ─────────────────────────────

    @Property(str, notify=itemsChanged)
    def tab(self) -> str:
        return self._tab

    @Property("QVariantList", notify=itemsChanged)
    def tabs(self) -> List[Dict[str, Any]]:
        """页签条数据源：`[{key, labelKey, icon, active}]`。"""
        return [
            {
                "key": key,
                "labelKey": TAB_LABEL_KEYS[key],
                "icon": TAB_ICONS[key],
                "active": key == self._tab,
            }
            for key in CLIENT_TABS
        ]

    @Property(str, notify=itemsChanged)
    def query(self) -> str:
        return str(self._current()["query"])

    @Property(str, notify=itemsChanged)
    def gameVersion(self) -> str:
        """当前筛选的游戏版本（空串 = 全部）。"""
        return str(self._current()["game_version"] or "")

    @Property(str, notify=itemsChanged)
    def loader(self) -> str:
        """当前筛选的加载器（空串 = 全部；只有模组页签会用到）。"""
        return str(self._current()["loader"] or "")

    @Property("QVariantList", notify=itemsChanged)
    def gameVersionChoices(self) -> List[Dict[str, Any]]:  # noqa: N802
        """游戏版本下拉：第一项永远是「全部版本」（`value` 为空串）。"""
        rows = [{"text": _("mod_browser_all_versions"), "value": ""}]
        rows.extend({"text": v, "value": v} for v in self._game_versions)
        return rows

    @Property("QVariantList", notify=itemsChanged)
    def loaderChoices(self) -> List[Dict[str, Any]]:  # noqa: N802
        """加载器下拉：第一项永远是「全部加载器」（`value` 为空串）。"""
        return [{"text": _(key), "value": value or ""} for key, value in LOADER_CHOICES]

    # ─── 只读属性：结果 / 分页 ───────────────────────────────

    @Property("QVariantList", notify=itemsChanged)
    def items(self) -> List[Dict[str, Any]]:
        """当前页的卡片（已装饰：下载量文本 / 加载器 / 版本区间 / 来源名）。"""
        state = self._current()
        hits = state["hits"]
        page = int(state["page"] or 1)
        offset = (page - 1) * PAGE_SIZE
        return [self._decorate(item) for item in hits[offset : offset + PAGE_SIZE]]

    @Property(int, notify=itemsChanged)
    def page(self) -> int:
        return int(self._current()["page"] or 1)

    @Property(int, notify=itemsChanged)
    def pageCount(self) -> int:  # noqa: N802
        """页数（**按本地能翻的条目数**算，D-103 的口径）。"""
        return max(1, (self._browsable() + PAGE_SIZE - 1) // PAGE_SIZE)

    @Property(int, notify=itemsChanged)
    def total(self) -> int:
        """后端报的匹配总数（只用于"共找到 N 个"这类信息性提示）。"""
        return int(self._current()["total"] or 0)

    @Property(int, notify=itemsChanged)
    def browsable(self) -> int:
        """本地实际能翻到的条目数（D-103：分页与区间都以它为准）。"""
        return self._browsable()

    @Property(int, notify=itemsChanged)
    def rangeStart(self) -> int:  # noqa: N802
        """当前页第一条的序号（1 基；空列表时是 0）。"""
        if not self._current()["hits"]:
            return 0
        return (int(self._current()["page"] or 1) - 1) * PAGE_SIZE + 1

    @Property(int, notify=itemsChanged)
    def rangeEnd(self) -> int:  # noqa: N802
        """当前页最后一条的序号（不超过本地条目数）。"""
        state = self._current()
        if not state["hits"]:
            return 0
        return min((int(state["page"] or 1) - 1) * PAGE_SIZE + PAGE_SIZE, self._browsable())

    @Property(str, notify=itemsChanged)
    def loadState(self) -> str:  # noqa: N802
        return str(self._current()["state"])

    @Property(str, notify=itemsChanged)
    def loadError(self) -> str:  # noqa: N802
        return str(self._current()["error"])

    @Property(str, notify=itemsChanged)
    def emptyKey(self) -> str:  # noqa: N802
        """空态文案键（三种类型各一句，旧实现那三个）。"""
        return TAB_EMPTY_KEYS.get(self._tab, "mod_browser_no_results")

    @Property(str, notify=itemsChanged)
    def tabLabelKey(self) -> str:  # noqa: N802
        return TAB_LABEL_KEYS.get(self._tab, "mod_browser_title_all")

    # ─── 只读属性：AI ───────────────────────────────────────

    @Property(bool, notify=itemsChanged)
    def aiBusy(self) -> bool:  # noqa: N802
        """AI 关键词扩展进行中（按钮禁用 + 文案换成"正在优化"）。"""
        return bool(self._current()["ai"])

    @Property("QVariantList", notify=itemsChanged)
    def aiKeywords(self) -> List[str]:  # noqa: N802
        """上一次 AI 搜索实际用的关键词（普通搜索时是空列表）。"""
        return list(self._current()["keywords"])

    # ─── 只读属性：安装进度 ─────────────────────────────────

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=progressChanged)
    def progressText(self) -> str:  # noqa: N802
        return self._progress_text

    @Property(float, notify=progressChanged)
    def progressValue(self) -> float:  # noqa: N802
        """0..1；不确定进度时保持 0（页面显示成不确定进度条的样式）。"""
        return float(self._progress_value)

    @Property(bool, notify=progressChanged)
    def progressVisible(self) -> bool:  # noqa: N802
        return bool(self._busy and self._progress_text)

    # ─── 只读属性：详情页 ───────────────────────────────────

    @Property(str, notify=detailChanged)
    def detailState(self) -> str:  # noqa: N802
        return str(self._detail["state"])

    @Property(str, notify=detailChanged)
    def detailError(self) -> str:  # noqa: N802
        return str(self._detail["error"])

    @Property("QVariantMap", notify=detailChanged)
    def detailProject(self) -> Dict[str, Any]:  # noqa: N802
        """归一后的项目信息（`{}` = 还没加载）。"""
        return dict(self._detail["project"])

    @Property("QVariantList", notify=detailChanged)
    def detailVersions(self) -> List[Dict[str, Any]]:  # noqa: N802
        """版本/文件列表（归一后，已带上"游戏版本/加载器/大小"的显示文本）。"""
        return [self._decorate_version(v) for v in self._detail["versions"]]

    @Property(str, notify=detailChanged)
    def detailSelectedId(self) -> str:  # noqa: N802
        return str(self._detail["selected"])

    @Property("QVariantList", notify=detailChanged)
    def detailDeps(self) -> List[Dict[str, Any]]:  # noqa: N802
        """所选版本的依赖预览：`[{project_id, kind, name, kindKey}]`。"""
        return [
            {
                "project_id": str(d.get("project_id", "")),
                "kind": str(d.get("kind", "")),
                "name": str(d.get("name", "")),
                "kindKey": "browse_dep_" + str(d.get("kind", "")),
            }
            for d in self._detail["deps"]
        ]

    @Property(bool, notify=detailChanged)
    def detailDepsLoading(self) -> bool:  # noqa: N802
        return bool(self._detail["deps_loading"])

    @Property(str, notify=detailChanged)
    def detailTitle(self) -> str:  # noqa: N802
        return str(self._detail["project"].get("title", "") or "")

    @Property(str, notify=detailChanged)
    def detailTab(self) -> str:  # noqa: N802
        return str(self._detail["tab"])

    @Property(str, notify=detailChanged)
    def detailSource(self) -> str:  # noqa: N802
        return str(self._detail["source"])
    # ─── 槽：进入页面 ───────────────────────────────────────

    @Slot(str, str)
    def activate(self, routeVersion: str, routeLoader: str) -> None:  # noqa: N802
        """在线浏览页入场：记下"从哪个版本进来"，并准备筛选默认值。

        **不在这里搜索**（搜索是用户动作，见 :meth:`search` / :meth:`aiSearch`）。
        版本变了才重置三个标签页的状态 —— 从详情页返回时会再调一次，这时不能把已经
        拿到的结果清掉。
        """
        wanted = str(routeVersion or "").strip()
        if wanted == self._version_id and self._states[TAB_MODS]["state"] != "idle":
            self._publish()
            return
        self._version_id = wanted
        self._reset_context(str(routeLoader or ""))

    @Slot()
    def loadVersionChoices(self) -> None:  # noqa: N802
        """后台加载游戏版本候选（旧 `_load_version_options`；失败就是空列表）。"""
        if self._versions_loaded or self._tasks is None or self._service is None:
            return
        self._versions_loaded = True

        def work() -> List[str]:
            return list(self._service.game_version_options() or [])

        self._submit(work, {"kind": "versions"}, name=TASK_SEARCH + ".versions")

    # ─── 槽：标签页 / 筛选 ─────────────────────────────────

    @Slot(str)
    def setTab(self, tab: str) -> None:  # noqa: N802
        """换标签页。**不自动搜索** —— 旧实现在"该页没有任何内容"时会自动搜一次；
        新版由用户按「搜索」触发（页面一建就联网不是我们想要的）。"""
        if tab not in CLIENT_TABS:
            return
        self._tab = tab
        self._publish()

    @Slot(str)
    def setQuery(self, text: str) -> None:  # noqa: N802
        self._current()["query"] = str(text or "")
        self._publish()

    @Slot(str)
    def setGameVersion(self, value: str) -> None:  # noqa: N802
        self._current()["game_version"] = str(value or "").strip() or None
        self._publish()

    @Slot(str)
    def setLoader(self, value: str) -> None:  # noqa: N802
        self._current()["loader"] = str(value or "").strip() or None
        self._publish()

    # ─── 槽：搜索 ───────────────────────────────────────────

    @Slot()
    def search(self) -> None:
        """常规搜索（双源合并）。"""
        self._start_search(self._tab, ai=False)

    @Slot()
    def refresh(self) -> None:
        """刷新 = 重新搜一次（旧窗口没有这个入口，等价于再点一次搜索）。"""
        self._start_search(self._tab, ai=False)

    @Slot()
    def aiSearch(self) -> None:
        """AI 搜索：先扩词再逐词搜、合并去重按下载量排序（服务层编排）。

        三种边界与旧实现逐字一致：**查询为空**退化成常规搜索、**没有 token** 只提示、
        展开过程中按钮禁用并显示"正在优化"。
        """
        state = self._current()
        query = str(state["query"] or "").strip()
        if not query:
            self._start_search(self._tab, ai=False)
            return
        token = self._ai_token()
        if not token:
            self._notify(_("ai_search_login_required"), "warning")
            return
        self._start_search(self._tab, ai=True, token=token)

    @Slot(int)
    def goto(self, page: int) -> None:  # noqa: N802
        """翻页 —— **纯本地切片**（结果已整批取回，旧实现同一口径）。"""
        state = self._current()
        pages = max(1, (self._browsable() + PAGE_SIZE - 1) // PAGE_SIZE)
        target = max(1, min(int(page or 1), pages))
        if target == int(state["page"] or 1):
            return
        state["page"] = target
        self._publish()

    # ─── 槽：安装（结果卡片）────────────────────────────────

    @Slot(str, str, str)
    def installFromCard(self, source: str, projectId: str, title: str) -> None:  # noqa: N802
        """结果卡片上的「安装」：装最新兼容版本（旧实现的行为）。

        模组页签要求筛选里有游戏版本与加载器（旧实现的同一判定），资源包/光影
        先弹目录选择框（旧实现也是先问目录）。
        """
        self._begin_install(source, projectId, title, version_id=None)

    @Slot(str)
    def provideDirectory(self, path: str) -> None:  # noqa: N802
        """用户选好了安装目录：继续那次挂起的安装；空串 = 取消。"""
        pending = self._pending_install
        self._pending_install = None
        if pending is None:
            return
        target = str(path or "").strip()
        if not target:
            self._set_status("mod_browser_install_cancelled", {})
            return
        self._submit_install(**{**pending, "target_dir": target})

    @Slot("QUrl")
    def provideDirectoryUrl(self, url: Any) -> None:  # noqa: N802
        """目录选择框给的是 `url`（`selectedFolder`）：折成本地路径再走同一条路。

        为什么在 Python 侧折：QML 里 `String(url)` 会给出 `file:///D:/a%20b`，
        百分号编码与 UNC 路径都得处理 —— `QUrl.toLocalFile()` 一次做对
        （与 `ResourcesBridge.toLocalPaths` 同一理由）。
        """
        try:
            path = url.toLocalFile() if url is not None else ""
        except Exception as e:  # noqa: BLE001 - 折不出来按取消处理
            logger.warning("目录 URL 折算失败 (%r): %s", url, e)
            path = ""
        self.provideDirectory(str(path or ""))

    @Slot()
    def cancelDirectory(self) -> None:  # noqa: N802
        """目录选择框被关掉（等价于取消）。"""
        self._pending_install = None
        self._set_status("mod_browser_install_cancelled", {})

    # ─── 槽：详情页 ─────────────────────────────────────────

    @Slot(str, str, str, str)
    def openDetail(self, source: str, projectId: str, tab: str, title: str) -> None:  # noqa: N802
        """详情页入场：拉项目信息 + 版本列表（依赖预览等选中版本再拉）。

        ``title`` 是结果卡片带过来的显示名，先用它把页头填上，等网络回来再覆盖 ——
        否则详情页会先空一屏。
        """
        #: 详情也有世代号（与搜索的 D-93 同形）：页面入场时可能先发一个空 projectId
        #: 的请求（routeParams 尚未赋值），晚到的错误不能覆盖后发的正确请求。
        seq = int(self._detail.get("seq", 0) or 0) + 1
        self._detail.update(
            {
                "state": "loading",
                "seq": seq,
                "error": "",
                "project": {"title": title or "", "source": source, "project_id": projectId},
                "versions": [],
                "selected": "",
                "deps": [],
                "deps_loading": False,
                "source": source or "modrinth",
                "tab": tab if tab in CLIENT_TABS else TAB_MODS,
            }
        )
        self.detailChanged.emit()
        if self._service is None or self._tasks is None:
            self._detail.update({"state": "error", "error": "service_unavailable"})
            self.detailChanged.emit()
            return

        info = self._service.describe_version(self._version_id) if self._version_id else {}
        game_version = info.get("game_version") if info else None
        loader = self._search_loader if self._detail["tab"] == TAB_MODS else None

        def work() -> Dict[str, Any]:
            project = self._service.project_detail(source, projectId)
            versions = self._service.project_versions(source, projectId, game_version, loader)
            return {"project": project, "versions": versions}

        self._submit(
            work,
            {"kind": "detail", "source": source or "modrinth", "seq": seq},
            name=TASK_DETAIL,
        )

    @Slot(str)
    def selectVersion(self, versionId: str) -> None:  # noqa: N802
        """详情页换版本：记下选择并拉依赖预览（限量解析名字）。"""
        self._detail["selected"] = str(versionId or "")
        self._detail["deps"] = []
        self.detailChanged.emit()
        if not versionId or self._service is None or self._tasks is None:
            return
        self._detail["deps_loading"] = True
        self.detailChanged.emit()
        source = str(self._detail["source"])
        project = str(self._detail["project"].get("project_id", ""))
        info = self._service.describe_version(self._version_id) if self._version_id else {}
        game_version = info.get("game_version") if info else None
        loader = self._search_loader if self._detail["tab"] == TAB_MODS else None
        selected = str(versionId)

        def work() -> List[Dict[str, Any]]:
            return self._service.version_dependency_preview(
                source, project, selected, game_version, loader
            )

        self._submit(
            work,
            {"kind": "deps", "selected": selected},
            name=TASK_DETAIL + ".deps",
        )

    @Slot()
    def retryDetail(self) -> None:
        """详情页错误态的重试。"""
        detail = self._detail
        self.openDetail(
            str(detail["source"]),
            str(detail["project"].get("project_id", "")),
            str(detail["tab"]),
            str(detail["project"].get("title", "")),
        )

    @Slot()
    def installSelected(self) -> None:
        """详情页的「安装」：装**选中的那个版本**（没选就用最新兼容版本）。"""
        detail = self._detail
        project_id = str(detail["project"].get("project_id", ""))
        if not project_id:
            return
        self._begin_install(
            str(detail["source"]),
            project_id,
            str(detail["project"].get("title", "")),
            version_id=str(detail["selected"]) or None,
        )

    # ─── 槽：取消 ───────────────────────────────────────────

    @Slot()
    def cancel(self) -> None:
        """取消在跑的安装任务（搜索是一次性请求，取消了也只是丢弃结果）。"""
        handle = self._handles.pop(TASK_INSTALL, None)
        if handle is None:
            return
        try:
            handle.cancel()
        except Exception as e:  # noqa: BLE001 - 取消失败不该把退出流程拖下水
            logger.warning("取消在线安装任务失败: %s", e)

    # ─── 内部：上下文与快照 ─────────────────────────────────

    def _current(self) -> Dict[str, Any]:
        return self._states[self._tab]

    def _browsable(self) -> int:
        state = self._current()
        return browsable_count(None, state["hits"], int(state["total"] or 0))

    def _defaults_version(self) -> str:
        """拿不到路由版本时，用哪个版本来算筛选默认值（用户 2026-10-23 要求「自动填充」）。

        顺序与 3.6 资源页一致：**最近使用 → 第一个已安装**。
        为什么要这一步：从"浏览全部资源"进来时路由没有版本，筛选会停在
        「全部版本 / 全部加载器」，而用户期望的是"按我现在玩的版本先筛好"。
        """
        last = ""
        try:
            last = str(getattr(self._config, "last_launched_version", "") or "").strip()
        except Exception as e:  # noqa: BLE001 - 配置异常就当没有最近使用
            logger.warning("取最近使用版本失败: %s", e)
        ids: List[str] = []
        getter = self._callbacks.get("get_installed_version_ids")
        if callable(getter):
            try:
                ids = [str(v) for v in (getter() or []) if str(v).strip()]
            except Exception as e:  # noqa: BLE001 - 列不出来就只用最近使用
                logger.warning("列举已安装版本失败: %s", e)
        if last and (not ids or last in ids):
            return last
        return ids[0] if ids else ""

    def _reset_context(self, route_loader: str) -> None:
        """来源版本变了：重算默认筛选值并把三个标签页重置。

        默认筛选值照旧窗口 `__init__` 的规则：游戏版本取来源版本的 MC 版本，
        加载器取来源版本的加载器（兼容映射后仍必须是下拉里的合法值，否则按"全部"）。
        路由**没给**版本时退到"最近使用 / 第一个已安装"（见 :meth:`_defaults_version`）。
        """
        source = self._version_id or self._defaults_version()
        info: Dict[str, Any] = {}
        if self._service is not None and source:
            try:
                info = self._service.describe_version(source) or {}
            except Exception as e:  # noqa: BLE001 - 解析失败退化成"不筛"
                logger.warning("解析来源版本失败: %s", e)
        self._default_game_version = info.get("game_version")
        source_loader = str(info.get("loader") or route_loader or "")
        self._source_loader = source_loader
        self._search_loader = None
        if source_loader and self._service is not None:
            try:
                self._search_loader = self._service.compat_loader(source_loader)
            except Exception as e:  # noqa: BLE001
                logger.warning("加载器兼容映射失败: %s", e)
        default_loader = self._search_loader if self._search_loader in LOADER_VALUES else None
        self._states = {
            key: _new_tab_state(
                self._default_game_version, default_loader if key == TAB_MODS else None
            )
            for key in CLIENT_TABS
        }
        self._tab = TAB_MODS
        self._pending_install = None
        self._detail = {
            "state": "idle",
            "seq": 0,
            "error": "",
            "project": {},
            "versions": [],
            "selected": "",
            "deps": [],
            "deps_loading": False,
            "source": "modrinth",
            "tab": TAB_MODS,
        }
        self._publish()
        self.detailChanged.emit()

    def _publish(self) -> None:
        self.itemsChanged.emit()

    # ─── 内部：装饰（给 QML 显示用，QML 里不拼字符串）───────

    def _decorate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        source = str(item.get("source", "modrinth") or "modrinth")
        downloads = int(item.get("downloads", 0) or 0)
        games = list(item.get("game_versions", []) or [])
        loaders = list(item.get("loaders", []) or [])
        return {
            "project_id": str(item.get("project_id", "")),
            "source": source,
            "source_name": "Modrinth" if source == "modrinth" else "CurseForge",
            "title": str(item.get("title", "") or ""),
            "description": str(item.get("description", "") or ""),
            "author": str(item.get("author", "") or ""),
            "downloads": downloads,
            "downloads_text": format_downloads(downloads),
            "icon_url": str(item.get("icon_url", "") or ""),
            "loaders_text": loader_tags_text(loaders),
            "versions_text": _compress_versions(games),
            "type_icon": TAB_ICONS.get(self._tab, "mods"),
        }

    def _decorate_version(self, version: Dict[str, Any]) -> Dict[str, Any]:
        games = list(version.get("game_versions", []) or [])
        loaders = list(version.get("loaders", []) or [])
        size = int(version.get("size", 0) or 0)
        deps = list(version.get("dependencies", []) or [])
        return {
            "id": str(version.get("id", "")),
            "name": str(version.get("name", "") or version.get("filename", "")),
            "filename": str(version.get("filename", "")),
            "release_type": str(version.get("release_type", "release")),
            "game_versions_text": _compress_versions(games),
            "loaders_text": loader_tags_text(loaders),
            "size": size,
            "size_text": _format_size(size),
            "deps_count": len([d for d in deps if d.get("kind") in ("required", "optional")]),
        }

    # ─── 内部：搜索 ─────────────────────────────────────────

    def _start_search(self, tab: str, *, ai: bool, token: str = "") -> None:
        """发起一次搜索（普通或 AI），带着本标签页的新世代号。"""
        if self._service is None or self._tasks is None:
            self._set_status("mod_browser_search_failed_status", {"error": "service_unavailable"})
            return
        state = self._states[tab]
        state["seq"] = int(state["seq"] or 0) + 1
        seq = state["seq"]
        state["state"] = "loading"
        state["error"] = ""
        state["ai"] = bool(ai)
        state["keywords"] = []
        state["page"] = 1
        self._tab = tab
        self._publish()
        self._set_status("ai_search_optimizing" if ai else "mod_browser_searching", {})

        query = str(state["query"] or "")
        game_version = state["game_version"]
        loader = state["loader"] if tab == TAB_MODS else None

        def work() -> Any:
            if ai:
                return self._service.search_ai_merged_tab(tab, query, token, game_version, loader)
            return self._service.search_client_tab(tab, query, game_version, loader)

        self._submit(
            work,
            {"kind": "search", "tab": tab, "seq": seq, "ai": bool(ai)},
            name=TASK_SEARCH,
        )

    def _apply_search(self, payload: Dict[str, Any]) -> None:
        tab = str(payload.get("tab", TAB_MODS))
        state = self._states.get(tab)
        if state is None:
            return
        #: 世代守卫（D-93 的桥侧同形）：晚到的旧请求整批丢弃。
        if int(payload.get("seq") or -1) != int(state["seq"]):
            logger.debug("丢弃过期搜索结果: %s seq=%s", tab, payload.get("seq"))
            return
        ai = bool(payload.get("ai"))
        state["ai"] = False
        error = payload.get("error")
        if error:
            state["state"] = "error"
            state["error"] = str(error)
            state["hits"] = []
            state["total"] = 0
            if tab == self._tab:
                self._set_status("mod_browser_search_failed_status", {"error": str(error)})
            self._publish()
            return
        outcome = payload.get("result")
        if outcome is None:
            #: 未知标签页（旧实现的 ``else: return``）—— 什么都不做，保持原样
            state["state"] = "idle"
            self._publish()
            return
        state["hits"] = list(getattr(outcome, "hits", []) or [])
        state["total"] = int(getattr(outcome, "total_hits", 0) or 0)
        state["keywords"] = list(getattr(outcome, "keywords", []) or []) if ai else []
        state["page"] = 1
        state["state"] = "empty" if not state["hits"] else "ready"
        if tab == self._tab:
            if ai:
                kw_text = ", ".join(state["keywords"]) or str(state["query"] or "")
                self._set_status(
                    "ai_search_done", {"keywords": kw_text, "total": len(state["hits"])}
                )
            elif not state["hits"]:
                self._set_status("mod_browser_no_results", {})
            else:
                self._set_status("mod_browser_total_found", {"total": state["total"]})
        self._publish()

    def _apply_versions(self, payload: Dict[str, Any]) -> None:
        if payload.get("error"):
            logger.warning("加载游戏版本列表失败: %s", payload["error"])
            return
        self._game_versions = [str(v) for v in (payload.get("result") or [])]
        self._publish()

    # ─── 内部：详情页 ───────────────────────────────────────

    def _apply_detail(self, payload: Dict[str, Any]) -> None:
        #: 世代守卫：晚到的旧详情结果（典型是入场时空的 projectId）整批丢弃。
        if int(payload.get("seq") or -1) != int(self._detail.get("seq", 0) or 0):
            logger.debug("丢弃过期详情结果: seq=%s", payload.get("seq"))
            return
        if payload.get("error"):
            self._detail.update({"state": "error", "error": str(payload["error"])})
            self.detailChanged.emit()
            return
        result = payload.get("result") or {}
        project = dict(result.get("project") or {})
        versions = list(result.get("versions") or [])
        if not project:
            self._detail.update({"state": "error", "error": "project_not_found"})
            self.detailChanged.emit()
            return
        selected = versions[0]["id"] if versions else ""
        self._detail.update(
            {
                #: 没有任何兼容版本 → 空态（页面上不出现安装按钮；旧实现里这种项目
                #: 会一直停在"没有兼容版本"的错误提示上，这里用空态更准确）
                "state": "ready" if versions else "empty",
                "error": "",
                "project": project,
                "versions": versions,
                "selected": selected,
                "deps": [],
                "deps_loading": False,
            }
        )
        self.detailChanged.emit()
        if selected:
            self.selectVersion(selected)

    def _apply_deps(self, payload: Dict[str, Any]) -> None:
        if payload.get("error"):
            logger.warning("依赖预览失败: %s", payload["error"])
            self._detail["deps_loading"] = False
            self.detailChanged.emit()
            return
        if str(payload.get("selected", "")) != str(self._detail["selected"]):
            #: 用户又换了版本：这一批依赖已经不是当前选择的了
            return
        self._detail["deps"] = list(payload.get("result") or [])
        self._detail["deps_loading"] = False
        self.detailChanged.emit()

    # ─── 内部：安装 ─────────────────────────────────────────

    def _begin_install(
        self, source: str, project_id: str, title: str, version_id: Optional[str], tab: str = ""
    ) -> None:
        """安装入口：先做前置校验，再请页面弹目录选择框（旧实现三个标签页**都**问目录）。"""
        if self._service is None:
            return
        tab_key = tab or self._tab
        if tab_key in (None, ""):
            tab_key = TAB_MODS
        if tab_key not in CLIENT_TABS:
            return
        state = self._states[tab_key]
        if tab_key == TAB_MODS:
            #: 旧 `_install_mod` 的同一判定：没有游戏版本或加载器就装不了
            if not state["game_version"] or not state["loader"]:
                self._set_status("mod_browser_need_version_install", {})
                return
        default_dir = self._install_target(tab_key)
        self._pending_install = {
            "source": str(source or "modrinth"),
            "project_id": str(project_id),
            "title": str(title or ""),
            "version_id": version_id,
            "tab": tab_key,
        }
        self.directoryRequested.emit(tab_key, default_dir, QUrl.fromLocalFile(default_dir))

    def _install_target(self, tab: str) -> str:
        """默认安装目录 —— **当前浏览版本**的资源目录（用户 2026-10-23 反馈）。

        之前直接调 `resolve_install_dir(callbacks, ...)`，有两个问题：

        * 装配期核心还没注册（`context.try_get("launcher")` 是 None），回调是空字典，
          它于是退到 ``Path(".")/<sub>`` —— 相对路径，对话框根本不在版本目录上；
        * 就算回调在，它也只会按"已安装实例 + 筛选值"反查，从某个版本进来时并不保证
          落在那个版本自己的目录里。

        现在：**基准目录自己解析成绝对路径**（launcher → config → 仓库下 `.minecraft`），
        来源版本已知时直接用 `install_dir_for()`（隔离版本 → `versions/<id>/<sub>`），
        未知（"浏览全部资源"）才退回旧的反查逻辑。目录会在弹框前建出来 ——
        旧实现 `_ask_install_dir` 也是先 `mkdir` 再 `askdirectory`。
        """
        if self._service is None:
            return ""
        base = self._base_dir()
        try:
            if self._version_id:
                target = self._service.install_dir_for(base, self._version_id, tab)
            else:
                #: "浏览全部资源"没有来源版本：仍按"已安装实例 + 筛选值"反查，
                #: 但把基准目录补成**绝对路径**（回调缺失时它原本会返回相对的 `mods`）。
                callbacks = dict(self._launcher_callbacks())
                callbacks.setdefault("get_minecraft_dir", lambda: str(base))
                target = self._service.resolve_install_dir(
                    callbacks,
                    tab,
                    self._default_game_version,
                    self._version_id,
                    lambda t: self._states[t]["game_version"],
                    lambda t: self._states[t]["loader"],
                )
        except Exception as e:  # noqa: BLE001 - 解析失败退到全局子目录
            logger.warning("解析安装目录失败: %s", e)
            target = base / TAB_SUBDIRS.get(tab, "mods")
        #: 建出来再加进 url：目录不存在时文件选择框会停在上级，看起来像"没定位"
        try:
            Path(str(target)).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("创建安装目录失败 (%s): %s", target, e)
        return str(target)

    def _launcher_callbacks(self) -> Dict[str, Any]:
        """回调字典；装配期还没 launcher 时**在这里补一次**（懒解析）。

        `bind()` 早于启动链条完成，那时 `try_get("launcher")` 是 None —— 一次性抓死
        会让整个会话都拿不到 `.minecraft`（阶段 3.6 在资源页也踩过同类问题）。
        """
        if not self._callbacks:
            self._launcher = self._launcher or self._launcher_of(self._context)
            self._callbacks = self._build_callbacks(self._launcher)
        return self._callbacks

    def _base_dir(self) -> Path:
        """`.minecraft` 的**绝对**路径（launcher → config → 仓库下 `.minecraft`）。"""
        getter = self._launcher_callbacks().get("get_minecraft_dir")
        if callable(getter):
            try:
                return Path(os.path.abspath(str(getter())))
            except Exception as e:  # noqa: BLE001 - 回调坏了退到配置
                logger.warning("取 .minecraft 目录失败（回调）: %s", e)
        try:
            configured = getattr(self._config, "minecraft_dir", None)
            if configured:
                return Path(os.path.abspath(str(configured)))
        except Exception as e:  # noqa: BLE001
            logger.warning("取 .minecraft 目录失败（配置）: %s", e)
        return Path(os.path.abspath(".minecraft"))

    def _submit_install(
        self,
        *,
        source: str,
        project_id: str,
        title: str,
        version_id: Optional[str],
        tab: str,
        target_dir: str,
    ) -> None:
        if self._service is None or self._tasks is None:
            return
        state = self._states[tab]
        game_version = str(state["game_version"] or self._default_game_version or "")
        loader = str(state["loader"] or self._search_loader or "")
        if not game_version:
            self._set_status("mod_browser_need_version_install", {})
            return
        if tab == TAB_MODS and not loader:
            self._set_status("mod_browser_need_version_install", {})
            return

        self._busy = True
        self._active_task = TASK_INSTALL
        self._progress_text = _("mod_browser_fetching_version", title=title)
        self._progress_value = 0.0
        self.busyChanged.emit()
        self.progressChanged.emit()
        self._set_status("mod_browser_fetching_version", {"title": title})

        def on_status(message: str) -> None:
            #: 核心下载器的状态回调在**调用线程**里被调，只排队
            self._post({"kind": "progress", "text": str(message or "")})

        payload = {"kind": "install", "tab": tab, "title": title}

        def work() -> Any:
            return self._service.install_chosen_version(
                source,
                tab,
                project_id,
                title,
                version_id,
                game_version,
                loader or "",
                target_dir,
                status_callback=on_status,
            )

        self._submit(work, payload, name=TASK_INSTALL)

    def _apply_progress(self, payload: Dict[str, Any]) -> None:
        self._progress_text = str(payload.get("text", "") or "")
        self.progressChanged.emit()

    def _apply_install(self, payload: Dict[str, Any]) -> None:
        tab = str(payload.get("tab", self._tab))
        title = str(payload.get("title", "") or "")
        self._busy = False
        self._progress_text = ""
        self._progress_value = 0.0
        self._active_task = ""
        self.busyChanged.emit()
        self.progressChanged.emit()

        if payload.get("error"):
            self._set_status("mod_browser_install_error", {"error": str(payload["error"])})
            self._notify(_("mod_browser_install_error", error=str(payload["error"])), "error")
            return

        result = payload.get("result")
        ok = bool(result[0]) if isinstance(result, (tuple, list)) and result else False
        detail = result[1] if isinstance(result, (tuple, list)) and len(result) > 1 else ""
        names = list(result[2]) if isinstance(result, (tuple, list)) and len(result) > 2 else []
        if not ok:
            self._set_status("mod_browser_install_failed", {"error": str(detail)})
            self._notify(_("mod_browser_install_failed", error=str(detail)), "error")
            return

        if len(names) > 1:
            deps = ", ".join(str(n) for n in names[:-1])
            self._set_status("mod_browser_install_success_deps", {"title": title, "deps": deps})
        else:
            self._set_status("mod_browser_install_success", {"title": title})
        if tab == TAB_MODS and self._service is not None:
            try:
                self._service.trigger_achievements(len(names))
            except Exception as e:  # noqa: BLE001 - 成就失败不影响安装结果
                logger.warning("触发模组成就失败: %s", e)

    # ─── 内部：任务提交与投递 ───────────────────────────────

    def _submit(self, work: Any, payload: Dict[str, Any], *, name: str) -> Any:
        """提交一个后台任务；结果与错误都经 `posted` 排队回主线程。"""
        if self._tasks is None:
            self._post({**payload, "result": None, "error": "tasks_unavailable"})
            return None

        def on_done(result: Any) -> None:
            self._post({**payload, "result": result, "error": None})

        def on_error(error: Any) -> None:
            text = str(error)
            logger.warning("在线浏览任务 %s 失败: %s", name, text)
            self._post({**payload, "result": None, "error": text})

        try:
            handle = self._tasks.submit(work, name=name, on_done=on_done, on_error=on_error)
        except Exception as e:  # noqa: BLE001 - 提交失败按任务失败处理
            logger.error("提交在线浏览任务 %s 失败: %s", name, e)
            self._post({**payload, "result": None, "error": str(e)})
            return None
        self._handles[name] = handle
        return handle

    def _post(self, item: Dict[str, Any]) -> None:
        with self._pending_lock:
            self._pending.append(item)
        self.posted.emit()

    def _deliver(self) -> None:
        """主线程：把排队的投递逐条落到属性上（`posted` 的槽）。"""
        with self._pending_lock:
            batch = self._pending[:]
            self._pending.clear()
        for item in batch:
            kind = item.get("kind")
            try:
                if kind == "search":
                    self._apply_search(item)
                elif kind == "versions":
                    self._apply_versions(item)
                elif kind == "detail":
                    self._apply_detail(item)
                elif kind == "deps":
                    self._apply_deps(item)
                elif kind == "install":
                    self._apply_install(item)
                elif kind == "progress":
                    self._apply_progress(item)
            except Exception as e:  # noqa: BLE001 - 一条投递失败不该拖垮后面的
                logger.error("处理在线浏览投递失败 (%s): %s", kind, e)

    # ─── 内部：文案与提示 ───────────────────────────────────

    def _ai_token(self) -> Optional[str]:
        """AI 搜索的 token（旧回调 `get_jdz_token` → `config.jdz_token`）。"""
        getter = self._callbacks.get("get_jdz_token")
        if callable(getter):
            try:
                token = getter()
                if token:
                    return str(token)
            except Exception as e:  # noqa: BLE001
                logger.warning("取 AI token 失败: %s", e)
        if self._config is not None:
            try:
                token = getattr(self._config, "jdz_token", None)
                return str(token) if token else None
            except Exception as e:  # noqa: BLE001
                logger.warning("取 AI token 失败: %s", e)
        return None

    def _set_status(self, key: str, params: Dict[str, Any]) -> None:
        try:
            text = _(key, **params) if params else _(key)
        except Exception as e:  # noqa: BLE001 - 文案坏了不该影响功能
            logger.warning("翻译状态文案失败 (%s): %s", key, e)
            text = key
        self.statusMessage.emit(text, "info")

    def _notify(self, text: str, level: str) -> None:
        self.noticeRequested.emit(text, level)


def _compress_versions(games: List[str]) -> str:
    """版本区间压缩（`modrinth.compress_game_versions`；失败就原样拼接）。"""
    if not games:
        return ""
    try:
        from modrinth import compress_game_versions

        return str(compress_game_versions(list(games)) or "")
    except Exception:  # noqa: BLE001 - 纯展示，失败不该抛
        return ", ".join(str(g) for g in games)


def _format_size(size: int) -> str:
    """字节数 → 可读文本（复用资源页那份 `format_size`，避免两套口径）。"""
    if not size:
        return ""
    try:
        from services.resource_service import format_size

        return str(format_size(int(size)))
    except Exception:  # noqa: BLE001
        return ""


def install(engine: Any, name: str = QML_NAME) -> Any:
    """预留的同形入口（本桥不需要额外接线）。

    `ResourcesBridge` 有这个函数是因为它要接图片通道；本桥没有引擎侧的额外依赖，
    上下文属性由 `main_qml.register_bridges()` 统一注册。**保留这个名字**是为了让
    "阶段 3 的桥都有 `install(engine)`"这条读代码的心智模型不必分叉，
    调用形状与 3.6 完全一致（返回桥对象，找不到返回 None）。
    """
    return (getattr(engine, "_fmcl_bridges", None) or {}).get(name)


__all__ = ["ModBrowserBridge", "QML_NAME", "install"]
