"""资源桥 —— QML 侧的 `Resources` 上下文属性（阶段 3 任务 3.6，对照表 L-55）。

## 它在链路的哪一环

    qml/pages/resources/ResourcesPage.qml
        ↓ 取属性 / 调槽
    app/bridges/resources_bridge.py（本模块，**零业务规则**）
        ↓ 转发
    services/resource_session.py（会话状态机：标签页 / 缓存 / 分页 / 更新检查）
        ↓ 委托
    services/resource_service.py（扫描 / 启停 / 删除 / 安装 / 缩略图 / 双源检查）

桥只做三件事：**把会话的属性念给 QML 听**、**把 QML 的动作转成会话调用**、
**把后台任务的进度回调变成 Qt 信号**。"mtime 缓存能不能复用""目录不存在算不算空态"
"页码越界怎么钳"一条都不在这里判（迁移红线 2）。

## 版本选择器（本页在两个入口之间共用）

旧实现只有 `versions/<id>/mods` 一个入口（从版本页的齿轮进来），窗口自带 `version_id`。
新界面的左侧一级导航 `resources/mods` **没有版本上下文**，所以页面顶部放一个版本
选择器（2026-10-06 用户裁决）。本桥的 `activate(routeVersion)` 负责"路由给的版本优先、
否则最近使用版本、否则第一个已安装版本"（判定在
`services/resource_session.py::resolve_version_id`）：

* 从版本页进来 → 路由参数就是那个版本，选择器显示它；
* 从一级导航进来 → 用 `config.last_launched_version`，再退回列表第一个；
* 一个版本都没装 → `hasVersion=false`，页面显示"没有版本"的空态（而不是空目录）。

## 线程约定

模组元数据提取、更新检查、批量更新都走 `ResourceService.tasks.submit`，任务的
`on_progress` / `on_done` / `on_error` **在工作线程上**被回调，所以它们**只排队 +
emit `posted`**（`_deliver` 住在主线程，跨线程 emit 是队列投递）。这与
`AccountsBridge.progressPosted` 是同一套手法 —— 契约第六节决策 10 禁止
`QMetaObject.invokeMethod`（var 签名不匹配会静默失败），见闸门 R7。

## 与旧实现的差异（详见对照表 L-55 与 `16` §二十二）

* **启用/禁用文案统一**：旧窗口模组卡片用 `resource_enable` / `resource_disable`，
  普通资源行用 `rm_enable` / `rm_disable`（同义两套键）。新界面统一走后者
  （用户 2026-10-06 裁决），对照表记「行为变更」。
* **导出模组列表照旧只写剪贴板**（不落盘）：这是对照表 L-Q7 的既有待裁决项，
  本轮**不擅自改**，行为与旧实现逐字一致。
* **拖拽安装**保留（旧实现同时注册了"空态放置区"与"列表"两处 `<<Drop>>`）。
* **页缓存去掉**：旧实现的 `_page_cache` 缓存的是 CTk 控件对象；QML 的 ListView
  自己复用委托，没有对应物可缓存（见 `resource_session.py` 头部对照表）。

## 2026-10-07 用户验收反馈改的三处（`16` §二十三）

1. **版本不再在 `bind()` 期就自动定成"列表第一个"**：`bind()` 早于 `routeParams`
   赋值，于是从版本页齿轮进来时桥已经"选好了版本"，`activate(路由版本)` 里那句
   "和当前一样就跳过"永远命中 —— **路由版本被忽略，从哪个版本进来都是同一个**。
   现在版本一律由 `activate()` 定（路由参数 → 最近使用 → 第一个已安装）。
2. **选择器多了「全局 .minecraft」这一项**（`versionId == ""`）：没版本上下文时
   读 `.minecraft/<folder>`（旧窗口从原版版本进来看到的就是它），而不是报
   "未指定游戏版本"。
3. **页签按模组加载器过滤**：原版版本只给「资源包 / 地图」（用户裁决，行为变更）。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from services.i18n_service import _
from services.resource_session import (
    TAB_DESC_KEYS,
    TAB_KEYS,
    TAB_LABEL_KEYS,
    TAB_SEARCH_KEYS,
    resolve_launcher,
    resolve_version_id,
)

logger = logging.getLogger("app.bridges.resources_bridge")

#: 契约第四节冻结的 QML 注册名（入口 `main_qml.CONTEXT_BRIDGES` 注册成上下文属性）。
QML_NAME = "Resources"

#: 后台任务名（取消时要按名字找到句柄）。
TASK_META = "resource.mods_metadata"
TASK_CHECK = "resource.check_updates"
TASK_BATCH = "resource.batch_update"

#: 「全局 `.minecraft` 目录」这一项的取值（**空串**，与"没选版本"是同一个状态）。
#: 写成常量是为了让"它是显式选项"这件事在代码里有名字 —— `resolve_version_id()`
#: 会把空串当成"没给版本"而替换成某个已安装版本，所以 **`setVersion("")` 这条
#: 路**（用户主动选了全局目录）在调它之前必须先拦一道；而 `activate("")` 那条路
#: （路由没给版本）**要**走替换，两条路的差别见 `_select_version`。
VERSION_GLOBAL = ""

#: 标签页 → 兜底图标名（`qml/assets/icons/*.svg` 里**存在**的名字）。
#: 取值刻意与左侧导航的图标一致（`nav_bridge` 里 resources 域用的就是 `mods`）。
TAB_ICONS: Dict[str, str] = {
    "mods": "mods",
    "resourcepacks": "resourcepack",
    "saves": "saves",
    "shaderpacks": "shaderpack",
}


class ResourcesBridge(QObject):
    """上下文属性 `Resources`。"""

    #: 列表/页码/计数/标签页/版本等**一次快照**里的东西变了。
    itemsChanged = Signal()
    #: 模组元数据进度文案变了（`modProgressText`）。
    progressChanged = Signal()
    #: 忙（加载元数据 / 检查更新 / 批量更新）状态变了。
    busyChanged = Signal()
    #: 更新检查结果变了（`updates` / `updatesCount`）。
    updatesChanged = Signal()
    #: 缩略图缓存变了（**单独一条信号**：十张图到齐不该让列表重建十次）。
    thumbnailsChanged = Signal()
    #: 状态条文案（**已翻译**；页面转给 `Shell.setStatus`）。
    statusMessage = Signal(str, str)
    #: 一条提示（安装结果 / 删除结果 / 更新完毕）—— 页面按 level 转成 Toast。
    noticeRequested = Signal(str, str)
    #: 要求页面弹出多选文件对话框（`fileFilter` 是过滤器）。
    addFilesRequested = Signal()
    #: 折好本地路径之后的"要安装这些文件"（见 `toLocalPaths`；QML 只负责转发）。
    filesChosen = Signal("QVariantList")
    #: 要求确认删除（参数 = 路径 / 名字）。
    deleteRequested = Signal(str, str)
    #: **内部投递信号**：worker 线程只排队并 emit 它，真正落属性的是主线程上的
    #: `_deliver()`（与 `AccountsBridge.progressPosted` 同一套，见模块文档）。
    posted = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._service: Any = None
        self._session: Any = None
        self._launcher: Any = None
        self._context: Any = None
        self._engine: Any = None

        #: 快照（QML 只读这些；每次 `sessionChanged` 之后重新读）
        self._version = ""
        self._version_ids: List[str] = []
        self._tab = TAB_KEYS[0]
        self._items: List[Dict[str, Any]] = []
        self._page = 1
        self._page_count = 1
        self._total = 0
        self._load_state = "empty"
        self._error = ""
        self._search = ""

        self._busy = False
        self._cancellable = False
        self._progress: Dict[str, Any] = {}
        self._mods_loaded = 0
        self._updates: List[Dict[str, Any]] = []
        self._thumbnails: Dict[str, str] = {}
        self._revision = 0
        #: **进页面之后有没有真的扫过一遍**（见 `activate` 的说明）：
        #: `bind()` 只解析版本不扫描，所以第一次 `activate` 必须扫。
        self._scanned_once = False
        #: 上一次取消是"例行作废"还是"用户按的取消"（见 `cancel_metadata`）。
        self._suppress_fallback = False

        #: 后台任务句柄（只按名字存；`Tasks` 自己持有任务，桥不负责生命周期）
        self._handles: Dict[str, Any] = {}
        #: 缩略图：在途去重集合 / 去重键 → 路径 / 任务序号 / 图片通道（见 `_thumbnail_source`）
        self._inflight: set = set()
        self._pending_thumbs: Dict[str, str] = {}
        self._thumb_tasks = 0
        self._thumbnail_provider: Any = None

        #: worker → 主线程的队列（只在这两个方法里碰，见模块文档"线程约定"）。
        self._pending: List[Dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        self.posted.connect(self._deliver)

    # ─── 装配期注入 ─────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        """`main_qml.register_bridges` 在注册前调它：取资源服务并造一个会话。"""
        self._context = context
        self._service = self._service_of(context, "resource")
        if self._service is None:
            logger.warning("资源服务不可用：资源页只会显示错误态")
            return
        self._launcher = resolve_launcher(self._service, context)
        config = None
        try:
            config = context.config if context is not None else None
        except Exception as e:  # noqa: BLE001 - 配置拿不到不影响列表（只是版本默认值退化）
            logger.warning("取配置失败（版本默认值将退化）: %s", e)
        try:
            self._session = self._service.new_session(
                self._callbacks(),
                version_id="",
                config=config,
                launcher=self._launcher,
                #: 调度器**显式传**：桥手里的 context 就是 AppContext，直接给它，
                #: 不依赖"服务已经被注册"这个隐含前提（`Service._context` 只在
                #: `AppContext.register()` 之后才有值，装配早于注册时会是 None）。
                tasks=self._tasks_of(context),
            )
        except Exception as e:  # noqa: BLE001 - 会话造不出来就是"页面不可用"
            logger.error("创建资源会话失败: %s", e)
            self._session = None
            return
        #: **刻意不在这里定版本**（2026-10-07 用户验收反馈的根因就在这里）：
        #: `bind()` 跑在 `PageStack.pushFrame()` 之前，那时 `routeParams` 还没赋值，
        #: 于是这里只能"猜"一个默认版本；等页面 `Component.onCompleted` 调
        #: `activate(路由版本)` 时，桥已经认定"版本选好了"，那句"和当前一样就跳过"
        #: 永远命中 → **从哪个版本进来都看第一个版本**。
        #: 现在只把版本列表准备好，真正的版本由 `activate()` 一次定死。
        self._refresh_version_choices()

    @staticmethod
    def _tasks_of(context: Any) -> Any:
        """取任务调度器（任务 3.6）。

        为什么要**容错**地取：`AppContext.tasks` 是普通属性，但测试与诊断用的上下文
        替身不一定有它；真跑起来时缺调度器的后果是"模组元数据 / 更新检查 / 批量更新
        全部静默不执行"（页面上表现为永远转圈），所以这里取不到就记一条 warning，
        别让它变成无声的失败。
        """
        tasks = getattr(context, "tasks", None)
        if tasks is None:
            logger.warning("上下文里没有 tasks（任务调度器）：资源页的后台任务将不可用")
        return tasks

    def use_engine(self, engine: Any) -> None:
        """接上引擎（只为了按注册名拿到 `Dialogs` 的剪贴板口）。"""
        self._engine = engine

    def detach(self) -> None:
        """退出/重建引擎：把所有后台任务取消掉（句柄在 `tasks` 里，桥不持有）。"""
        self.cancel()

    def _callbacks(self) -> Dict[str, Any]:
        """给会话的目录回调：核心能取到就用核心，取不到会话会退回 `config.minecraft_dir`。"""
        launcher = self._launcher
        callbacks: Dict[str, Any] = {}
        if launcher is not None:
            getters = {
                "get_minecraft_dir": "get_minecraft_dir",
                "get_installed_versions": "get_installed_versions",
            }
            for key, attr in getters.items():
                fn = getattr(launcher, attr, None)
                if callable(fn):
                    callbacks[key] = fn
        return callbacks

    @staticmethod
    def _service_of(context: Any, name: str) -> Any:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("资源页取服务 %s 失败: %s", name, e)
            return None

    # ─── 只读属性 ───────────────────────────────────────────

    @Property(bool, constant=True)
    def available(self) -> bool:
        """服务与会话都在不在（缺席时页面走错误态，而不是绑一堆 undefined）。"""
        return self._service is not None and self._session is not None

    @Property(int, notify=itemsChanged)
    def revision(self) -> int:  # noqa: N802
        """快照版本号。QML 的 `model` 绑不上"内部列表"时用它强制重读。"""
        return self._revision

    @Property(str, notify=itemsChanged)
    def versionId(self) -> str:  # noqa: N802
        """当前版本；**空串 = 全局 `.minecraft` 目录**那一项。"""
        return self._version

    @Property("QVariantList", notify=itemsChanged)
    def versionChoices(self) -> List[Dict[str, Any]]:  # noqa: N802
        """版本选择器的选项：`[{"text": 显示名, "value": id}, …]`（`FmComboBox` 的 model）。

        **第一项永远是「全局 .minecraft」**（`value` 为空串）：它的含义是"四个资源目录
        都取 `.minecraft/<folder>`"，也就是旧窗口从原版版本进来时看到的那一个
        （用户 2026-10-07 裁决）。
        """
        rows = [{"text": _("resource_scope_global"), "value": ""}]
        rows.extend({"text": vid, "value": vid} for vid in self._version_ids)
        return rows

    @Property(bool, notify=itemsChanged)
    def hasVersion(self) -> bool:  # noqa: N802
        """有没有可用于**版本隔离**的版本（空串/全局那一项不算）。

        只用于"一个版本都没装"的提示语，**不再决定整页空态** —— 没装版本时照样可以
        看全局目录（见 `versionChoices`）。
        """
        return bool(self._version)

    @Property(bool, notify=itemsChanged)
    def globalScope(self) -> bool:  # noqa: N802
        """当前看的是不是全局 `.minecraft` 目录（页面据此显示一句说明）。"""
        return not self._version

    @Property(str, constant=True)
    def globalHintKey(self) -> str:  # noqa: N802
        """全局目录那句说明的 i18n 键。"""
        return "resource_scope_global_hint"

    @Property("QVariantList", notify=itemsChanged)
    def allowedTabs(self) -> List[str]:  # noqa: N802
        """这一屏允许显示的页签键（原版版本没有"模组/光影" —— 用户 2026-10-07 裁决）。"""
        if self._session is None:
            return list(TAB_KEYS)
        try:
            return list(self._session.allowed_tabs())
        except Exception as e:  # noqa: BLE001 - 判不出来就给全部的（保守）
            logger.warning("取可用资源类型失败: %s", e)
            return list(TAB_KEYS)

    @Property(str, notify=itemsChanged)
    def tab(self) -> str:
        return self._tab

    @Property("QVariantList", notify=itemsChanged)
    def tabs(self) -> List[Dict[str, Any]]:  # noqa: N802
        """页签条的数据源：`[{"key", "labelKey", "active"}, …]`。

        **按当前版本过滤**：原版版本只给「资源包 / 地图」（`allowedTabs`）。
        """
        allowed = self.allowedTabs
        return [
            {"key": key, "labelKey": TAB_LABEL_KEYS[key], "active": key == self._tab}
            for key in TAB_KEYS
            if key in allowed
        ]

    @Property(str, notify=itemsChanged)
    def tabLabelKey(self) -> str:  # noqa: N802
        """当前标签页的类型名键（`rm_folder_empty` 的 `{label}` 参数、选择框标题都用它）。"""
        return TAB_LABEL_KEYS.get(self._tab, self._tab)

    @Property(str, notify=itemsChanged)
    def tabDescKey(self) -> str:  # noqa: N802
        return TAB_DESC_KEYS.get(self._tab, "")

    @Property(str, notify=itemsChanged)
    def tabIcon(self) -> str:  # noqa: N802
        """当前标签页的图标名（`qml/assets/icons/*.svg` 里存在的名字）。

        **必须由桥给**：标签页的键（`resourcepacks` / `shaderpacks`）不是图标名，
        页头把它们直接塞给 `FmIcon` 会找不到 svg 并刷 `Failed to get image from
        provider` 警告（阶段 3.6 探针实测）。
        """
        return TAB_ICONS.get(self._tab, "mods")

    @Property(str, notify=itemsChanged)
    def searchPlaceholderKey(self) -> str:  # noqa: N802
        return TAB_SEARCH_KEYS.get(self._tab, "rm_search_placeholder_mods")

    @Property(str, notify=itemsChanged)
    def search(self) -> str:
        return self._search

    @Property("QVariantList", notify=itemsChanged)
    def items(self) -> List[Dict[str, Any]]:
        """当前页条目（已按标签页补好显示字段，见 `_decorate`）。"""
        return self._items

    @Property(int, notify=itemsChanged)
    def page(self) -> int:
        return self._page

    @Property(int, notify=itemsChanged)
    def pageCount(self) -> int:  # noqa: N802
        return self._page_count

    @Property(int, notify=itemsChanged)
    def total(self) -> int:
        return self._total

    @Property(str, notify=itemsChanged)
    def loadState(self) -> str:  # noqa: N802
        """`FmPage.contentState` 直接吃它：ready / empty / loading / error。"""
        return self._load_state

    @Property(str, notify=itemsChanged)
    def loadError(self) -> str:  # noqa: N802
        return self._error

    @Property(bool, notify=itemsChanged)
    def isMods(self) -> bool:  # noqa: N802
        """模组标签页（决定"导出列表""检查更新"两颗按钮的可见性）。"""
        return self._tab == "mods"

    @Property(bool, notify=itemsChanged)
    def noVersion(self) -> bool:  # noqa: N802
        """**一个版本都没装、也没在看全局目录** —— 只有这种情况下空态才该说"先装一个版本"。

        为什么要单列一条：空态标题原先写死成"还没有已安装的版本"，可"文件夹不存在"
        （用户截图里状态栏那句话）与"文件夹是空的"也是空态，于是**标题和真实原因对不上**
        （2026-10-07 用户第二次反馈）。真正的判据是"有没有版本可选"：
        `_version_ids` 为空 = 选择器里只有「全局 .minecraft」那一项。
        """
        return not self._version_ids and not self._version

    @Property(str, notify=itemsChanged)
    def emptyFolderKey(self) -> str:  # noqa: N802
        """"这个版本的资源目录是空的"用的键（带 `{label}`，由页面补参数）。"""
        return "rm_folder_empty"

    @Property(int, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property("QVariantMap", notify=progressChanged)
    def progress(self) -> Dict[str, Any]:
        """最近一条进度：`{"message", "current", "total"}`（模组元数据提取用）。"""
        return self._progress

    @Property(bool, notify=progressChanged)
    def progressVisible(self) -> bool:  # noqa: N802
        return bool(self._progress.get("message"))

    @Property(int, notify=itemsChanged)
    def modCount(self) -> int:  # noqa: N802
        """已加载元数据的模组条数（导出列表的"空不空"判据、成就的计数来源）。"""
        return self._mods_loaded

    @Property("QVariantList", notify=updatesChanged)
    def updates(self) -> List[Dict[str, Any]]:
        """可更新的模组（已排序，见 `_collect_updates`）：`modid` `mod_name`
        `current_version` `latest_version` `selected`。"""
        return self._updates

    @Property(int, notify=updatesChanged)
    def updatesCount(self) -> int:  # noqa: N802
        return len(self._updates)

    @Property("QVariantMap", notify=thumbnailsChanged)
    def thumbnails(self) -> Dict[str, str]:
        """缩略图缓存：`路径 -> 图片源`（资源包 / 光影的预览图）。"""
        return self._thumbnails

    # ─── 常量：文案键与文件过滤器 ───────────────────────────

    @Property(str, constant=True)
    def titleKey(self) -> str:  # noqa: N802
        """页面标题键（旧 `rm_title` 带 `{version}`，版本由页面拼）。"""
        return "rm_title"

    @Property(str, constant=True)
    def readyKey(self) -> str:  # noqa: N802
        return "rm_status_ready"

    @Property(str, constant=True)
    def dropHintKey(self) -> str:  # noqa: N802
        return "rm_drop_hint"

    @Property(str, constant=True)
    def disabledSuffixKey(self) -> str:  # noqa: N802
        return "rm_disabled_suffix"

    @Property(str, constant=True)
    def nonStandardMapKey(self) -> str:  # noqa: N802
        return "rm_non_standard_map"

    @Property(str, constant=True)
    def enableKey(self) -> str:  # noqa: N802
        """启用/禁用文案（**统一走 `rm_*`**，见模块文档"与旧实现的差异"）。"""
        return "rm_enable"

    @Property(str, constant=True)
    def disableKey(self) -> str:  # noqa: N802
        return "rm_disable"

    @Property(str, constant=True)
    def deleteConfirmTitleKey(self) -> str:  # noqa: N802
        return "rm_delete_confirm_title"

    @Property(str, constant=True)
    def deleteConfirmMsgKey(self) -> str:  # noqa: N802
        return "rm_delete_confirm_msg"

    @Property(str, constant=True)
    def updateDialogTitleKey(self) -> str:  # noqa: N802
        return "mod_update_dialog_title"

    @Property(str, constant=True)
    def updateDialogHintKey(self) -> str:  # noqa: N802
        return "mod_update_dialog_hint"

    @Property(str, constant=True)
    def updateDialogHeaderKey(self) -> str:  # noqa: N802
        """`mod_update_dialog_header`（带 `{count}`）。"""
        return "mod_update_dialog_header"

    @Property(str, constant=True)
    def updateAllKey(self) -> str:  # noqa: N802
        return "mod_update_all"

    @Property(str, constant=True)
    def updateSelectedKey(self) -> str:  # noqa: N802
        return "mod_update_selected"

    @Property(str, constant=True)
    def updateCancelKey(self) -> str:  # noqa: N802
        return "mod_update_cancel"

    @Property(str, constant=True)
    def noVersionKey(self) -> str:  # noqa: N802
        """**一个版本都没装**时的空态文案。

        2026-10-07 用户反馈后改：原先复用 `mod_browser_need_version_install`
        （「未指定游戏版本，无法安装。请在已安装版本中打开资源浏览后安装。」）——
        那是给**安装流程**用的话，摆在"我的资源"里既不准确也吓人（用户截图里的就是
        它）。现在是一句"还没有已安装的版本，先装一个再回来管理"。
        """
        return "resource_no_version_hint"

    @Property(str, notify=itemsChanged)
    def fileFilter(self) -> str:  # noqa: N802
        """当前标签页的文件对话框过滤器（旧 `_select_file_install` 那两行）。

        形如 `"JAR 文件 (*.jar *.jar.disabled);;所有文件 (*.*)"`（i18n 后的类型名）。
        """
        label = _(TAB_LABEL_KEYS.get(self._tab, self._tab))
        exts = self._extensions()
        patterns = " ".join(f"*{ext}" for ext in exts)
        return f"{label} ({patterns});;{_('all_files')} (*.*)"

    def _extensions(self) -> List[str]:
        try:
            from services.resource_service import RESOURCE_EXTENSIONS

            return sorted(RESOURCE_EXTENSIONS.get(self._tab, set()))
        except Exception as e:  # noqa: BLE001 - 取不到扩展名 = 不过滤
            logger.warning("取资源扩展名失败: %s", e)
            return []

    # ─── 槽：进入 / 版本 / 标签页 / 搜索 / 分页 ─────────────

    @Slot(str)
    def activate(self, routeVersion: str) -> None:  # noqa: N802 - QML 槽名
        """页面出现时调它：`routeVersion` 来自路由参数（一级导航进来时是空串）。

        **本方法是"这一屏看哪个版本"的唯一权威**（2026-10-07 用户验收反馈后改）：

        * `bind()` 期**不**定版本（那时 `routeParams` 还没赋值，只能猜 —— 一猜就
          把路由版本盖掉了，症状是"从哪个版本进来都看第一个"）；
        * 这里按 **路由参数 → 最近使用 → 第一个已安装 → 全局目录** 定一次；
        * `routeVersion` 为空串的含义是"**路由没给版本**"（一级导航进来），
          **不是**"用户选了全局目录" —— 后者只由 :meth:`setVersion` 表达，
          所以这里照旧走 `resolve_version_id` 的兜底链，只有"一个版本都没装"
          时才会落到全局目录（见 `_select_version` 的 `explicit_global`）；
        * 路由参数与当前不同就**一定**重选（哪怕当前值看起来"也合法"）；
        * 一次真实扫描由 `_scanned_once` 保证（`bind()` 只准备列表、不扫）。
        """
        if self._session is None:
            self._publish(force_scan=True)
            return
        requested = str(routeVersion or "").strip()
        if requested and requested != self._version:
            self._select_version(requested, scan=True)
            return
        if not self._scanned_once:
            self._select_version(requested, scan=True)
            return
        self._publish(force_scan=True)

    @Slot(str)
    def setVersion(self, versionId: str) -> None:  # noqa: N802
        """用户在页内换版本（选择器的 `value`；空串 = 「全局 .minecraft」那一项）。

        `setVersion("")` 是**合法操作**，而且与 `activate("")` 语义相反：这里是
        "用户主动选了全局目录"（`explicit_global=True`），那边是"路由没给版本"。
        """
        target = str(versionId or "").strip()
        if target == self._version:
            return
        self._select_version(target, scan=True, explicit_global=(target == VERSION_GLOBAL))

    @Slot(str)
    def setTab(self, tab: str) -> None:  # noqa: N802
        """换标签页：清搜索、重置页码、重扫（旧 `_switch_tab`）。

        **没有"相同页签就早退"这条捷径**：页签相同也意味着"重新点了一次这一栏"，
        旧实现同样会重扫（`_switch_tab` 无条件走到 `_refresh_current_list`）。
        早退还会让"先清搜索、再重扫"的顺序失效（阶段 3.6 单测抓到过：
        重复点当前页签时搜索框没被清掉）。
        """
        if self._session is None:
            return
        self._session.set_tab(tab)
        #: 立刻对齐页签快照（`tab` / `isMods` / `tabLabelKey` / `tabs` / 行的装饰）。
        #: 不能只等 `_apply_result`：那次重扫要是抛了，错误态也会按旧页签念属性。
        self._tab = self._session.tab
        self._search = self._session.search
        self._thumbnails = {}
        self._mods_loaded = 0
        self._updates = []
        self.updatesChanged.emit()
        self._reload(force=True)

    @Slot(str)
    def setSearch(self, text: str) -> None:  # noqa: N802
        """搜索（模组页在已加载的元数据上过滤，不会重新解压 jar）。"""
        if self._session is None:
            return
        result = self._session.set_search(text)
        if result is None:
            return
        self._search = self._session.search
        self._apply_result(result)
        self._start_thumbnails()

    @Slot(int)
    def goto(self, page: int) -> None:  # noqa: N802
        """翻页（不重扫，只重算切片）。"""
        if self._session is None:
            return
        self._apply_result(self._session.goto(page))
        self._start_thumbnails()

    @Slot()
    def refresh(self) -> None:  # noqa: N802
        """强制重扫当前标签页（页面上的刷新按钮 / 重试按钮）。"""
        if self._session is None:
            return
        self._thumbnails = {}
        self._mods_loaded = 0
        self._reload(force=True)

    @Slot()
    def retry(self) -> None:  # noqa: N802
        """错误态的"重试"（与 `refresh` 同义，单独给一条槽是为了页面语义清楚）。"""
        self.refresh()

    # ─── 槽：模组项操作 ─────────────────────────────────────

    @Slot(str, bool)
    def toggleMod(self, path: str, isDisabled: bool) -> None:  # noqa: N802
        """启用 / 禁用（改文件名后缀）。"""
        if self._session is None:
            return
        try:
            name = self._session.toggle_mod(path, bool(isDisabled))
        except Exception as e:  # noqa: BLE001 - 失败只报一句，界面不崩
            self._fail("rm_operation_failed", {"error": str(e)})
            return
        self._set_status("rm_enabled_status" if isDisabled else "rm_disabled_status", {"name": name})
        self._reload(force=False)

    @Slot(str, str)
    def requestDelete(self, path: str, name: str) -> None:  # noqa: N802
        """要求确认删除（页面弹 `FmConfirmDialog` 之类的确认框）。"""
        if not self._session or not path:
            return
        self.deleteRequested.emit(str(path), str(name or path))

    @Slot(str)
    def confirmDelete(self, path: str) -> None:  # noqa: N802
        """确认删除（旧 `_delete_resource` 的 yes 分支）。"""
        if self._session is None:
            return
        name = ""
        for item in self._items:
            if str(item.get("path", "")) == str(path):
                name = str(item.get("name", ""))
                break
        try:
            self._session.delete(path)
        except Exception as e:  # noqa: BLE001
            self._fail("rm_delete_failed", {"error": str(e)})
            return
        self._set_status("rm_deleted", {"name": name or str(path)})
        self._reload(force=False)

    @Slot()
    def requestAddFiles(self) -> None:  # noqa: N802
        """打开"选择文件安装"（页面弹多选文件对话框，过滤器用 `fileFilter`）。"""
        if self._session is None:
            return
        self.addFilesRequested.emit()

    @Slot("QVariantList")
    def submitFiles(self, paths: Any) -> None:  # noqa: N802
        """选好文件之后的安装（旧 `_select_file_install` 的循环体）。

        计数与三条结果文案与旧实现一一对应：`rm_install_count`（成功 N 个）/
        `rm_no_resources_installed`（一个都没装）/ 逐个失败时 `_report_install_result`
        那四条（已存在 / 地图已存在 / 不支持 / 安装失败）。
        """
        if self._session is None:
            return
        files = self.toLocalPaths(paths)
        if not files:
            return
        accepted = 0
        installed = 0
        for one in files:
            if not self._accepted_for_drop(one):
                continue
            accepted += 1
            try:
                result = self._session.install(one)
            except Exception as e:  # noqa: BLE001 - 安装失败只报一句
                self._fail("rm_install_failed", {"error": str(e)})
                continue
            if self._report_install(result):
                installed += 1
        if installed > 0:
            self._set_status("rm_install_count", {"count": installed})
            self._notify(_("notify_resource_done"), "success")
            self._reload(force=False)
        else:
            #: 旧实现在这里报 `rm_no_resources_installed`（**不判"认不认得"**）
            self._set_status("rm_no_resources_installed", {})

    @Slot("QVariantList")
    def dropFiles(self, paths: Any) -> None:  # noqa: N802
        """拖拽放下（`DropArea.onDropped` 调它）。**只收认得的类型**。

        与文件对话框的差别照抄旧实现：拖拽先过一道 `accept_drop_entry`
        （扩展名 / 目录判定），不认的直接跳过；一个都没装时报 `rm_no_valid_files`
        （旧 `_on_drop` 的 else 分支），而不是 `rm_no_resources_installed`。
        """
        if self._session is None:
            return
        files = self.toLocalPaths(paths)
        if not files:
            return
        accepted = 0
        installed = 0
        for one in files:
            if not self._accepted_for_drop(one):
                continue
            accepted += 1
            try:
                result = self._session.install(one)
            except Exception as e:  # noqa: BLE001
                self._fail("rm_install_failed", {"error": str(e)})
                continue
            if self._report_install(result):
                installed += 1
        if installed > 0:
            self._set_status("rm_install_count", {"count": installed})
            self._notify(_("notify_resource_done"), "success")
            self._reload(force=False)
        else:
            self._set_status("rm_no_valid_files" if accepted == 0 else "rm_no_resources_installed", {})

    def _accepted_for_drop(self, path: str) -> bool:
        """这一条能不能按当前标签页装（旧 `_on_drop` 里那道判定）。

        判定函数本身在服务层（`accept_drop_entry`）；它抛异常时按"不认"处理 ——
        拖进来一个坏路径不该让整个放下动作中断。
        """
        try:
            return bool(self._service.accept_drop_entry(path, self._session.tab))
        except Exception as e:  # noqa: BLE001 - 判定坏了按"不认"处理
            logger.debug("拖拽判定失败 (%s): %s", path, e)
            return False

    @Slot()
    def openFolder(self) -> None:  # noqa: N802
        """在系统文件管理器里打开当前标签页的资源目录（旧 `_open_folder`）。"""
        if self._session is None:
            return
        try:
            self._session.open_folder()
        except Exception as e:  # noqa: BLE001
            self._fail("rm_open_folder_failed", {"error": str(e)})

    @Slot("QVariantList", result="QVariantList")
    def toLocalPaths(self, items: Any) -> List[str]:  # noqa: N802 - QML 槽名
        """把 `url` / 路径串折成**本地路径**（文件对话框与拖拽共用的唯一入口）。

        为什么在 Python 侧做而不是让 QML 切字符串：QML 的 `String(url)` 给出
        `file:///D:/a%20b/c.jar`，而**UNC 路径**（`file://server/share/x.jar`，网络
        共享或部分虚拟盘符）在被"砍掉前两个斜杠"的写法下会丢掉前导 `//`
        （阶段 3.6 探针实测，见 `docs/refactor/16-phase3-execution-log.md` §二十二）。
        `QUrl.toLocalFile()`
        一次处理完协议、百分号解码与 UNC 三种情况，没有自己写正则的必要。

        非 `file:` 的输入（已经是本地路径的串）原样返回 —— 拖拽与文件对话框给的都是
        `url`，但桥的槽也可能被测试或将来别处用路径串直接调。
        """
        out: List[str] = []
        for raw in list(items or []):
            text = raw.toString() if isinstance(raw, QUrl) else str(raw or "")
            text = text.strip()
            if not text:
                continue
            if text.startswith("file:"):
                local = QUrl(text).toLocalFile()
                out.append(local or text)
            else:
                out.append(text)
        return out

    @Slot("QVariantList", bool)
    def takeFiles(self, items: Any, drop: bool = False) -> None:  # noqa: N802 - QML 槽名
        """QML 选好/拖好文件后的**唯一入口**：先折本地路径，再按来源分派。

        为什么需要它：文件对话框给的 `selectedFiles` 与 `DropArea` 给的 `drop.urls`
        都是 `url` 列表，而折路径这件事**必须用 `QUrl.toLocalFile()`**（QML 里
        "砍掉 `file://`" 的写法会在 UNC 路径上丢前导 `//`，见 `toLocalPaths`）。
        折完之后的**后续语义两条路不同**（旧实现就是两条路，只是当年一个来自
        `filedialog`、一个来自 `event.data`）：

        * `drop=False`（文件对话框）→ `submitFiles`：不判扩展名（文件类型过滤器已经
          筛过，且地图允许选目录），"一个都没装上"报 `rm_no_resources_installed`；
        * `drop=True`（拖拽）→ `dropFiles`：先过 `accept_drop_entry`，全不认时报
          `rm_no_valid_files`。

        来源由 QML 显式给（它知道自己是从哪个回调来的），桥不猜。
        """
        files = self.toLocalPaths(items)
        if not files:
            return
        if drop:
            self.dropFiles(files)
        else:
            self.submitFiles(files)

    @Slot()
    def exportList(self) -> None:  # noqa: N802
        """导出模组列表 —— **只写剪贴板，不落盘**（旧行为；对照表 L-Q7 的待裁决项）。

        标题行由界面层用 i18n 拼好（服务层不 import 文案），与旧实现
        `f"=== {_('mod_export_header', version=…)} ===\\n"` 逐字一致。
        剪贴板走 `Dialogs.set_clipboard`（已带"非主线程拒绝 + 失败返回 False"），
        本槽由 QML 在主线程调用，所以正常路径一定成功。
        """
        if self._session is None:
            return
        if not self._mods_loaded:
            self._set_status("mod_export_empty", {})
            return
        header = "=== " + _("mod_export_header", version=self._version) + " ===\n"
        try:
            text = self._session.export_text(header)
        except Exception as e:  # noqa: BLE001
            self._fail("rm_operation_failed", {"error": str(e)})
            return
        dialogs = self._bridge("Dialogs")
        ok = False
        if dialogs is not None:
            try:
                ok = bool(dialogs.set_clipboard(text))
            except Exception as e:  # noqa: BLE001 - 剪贴板被别的程序占用
                logger.warning("写剪贴板失败: %s", e)
        if ok:
            self._set_status("mod_export_copied", {"count": self._mods_loaded})
        else:
            #: 剪贴板写不进去（被别的程序占用是常态）。文案复用**已经在用的**
            #: `rm_operation_failed`（"操作失败: {error}"）—— 刻意不新增 i18n 键：
            #: 上一版这里引用了一个 4 个语言文件里都不存在的 `clipboard_failed`，
            #: i18n 闸门与分层测试当场判红（阶段 3.6 收尾）。
            #: 错误详情是一个**技术标识**（不是给人看的句子），与
            #: `_set_status` 透出核心层中文状态串同属"阶段 4 统一收口"那一批。
            self._fail("rm_operation_failed", {"error": "clipboard"})

    # ─── 槽：更新 ───────────────────────────────────────────

    @Slot()
    def checkUpdates(self) -> None:  # noqa: N802
        """检查模组更新（双源、8 并发；结果经 `updates` 给页面弹对话框）。"""
        if self._session is None or self._session.checking:
            return
        payload, failure = self._session.prepare_update_check()
        if payload is None:
            if failure:
                self._set_status(failure, {})
            return
        mods, game_version, mod_loader = payload
        self._session.begin_update_check()
        self._updates = []
        self.updatesChanged.emit()
        self._set_busy(True, cancellable=False)
        self._set_status("mod_checking_updates_progress", {"current": 0, "total": len(mods)})
        try:
            handle = self._session.check_updates(
                mods,
                game_version,
                mod_loader,
                on_progress=self._update_progress,
                on_done=self._update_done,
                on_error=self._update_error,
            )
        except Exception as e:  # noqa: BLE001 - 提交失败也要把忙标志收回来
            self._update_error(e)
            return
        self._remember(TASK_CHECK, handle)

    @Slot("QVariantList")
    def applyUpdates(self, modids: Any) -> None:  # noqa: N802
        """批量更新选中的模组（旧 `_batch_update_mods`）。"""
        if self._session is None:
            return
        wanted = [str(m) for m in (modids or [])]
        if not wanted:
            return
        payload, failure = self._session.prepare_batch_update(wanted)
        if payload is None:
            if failure:
                self._fail("mod_update_failed", {"error": _(failure)})
            return
        ids, game_version, mod_loader = payload
        self._set_busy(True, cancellable=False)
        self._set_status("mod_update_batch_starting", {"count": len(ids)})
        try:
            handle = self._session.batch_update(
                ids, game_version, mod_loader, on_progress=self._batch_progress, on_done=self._batch_done
            )
        except Exception as e:  # noqa: BLE001
            logger.error("批量更新提交失败: %s", e)
            self._batch_done((0, len(ids)))
            return
        self._remember(TASK_BATCH, handle)

    @Slot()
    def cancel(self) -> None:  # noqa: N802
        """取消正在跑的后台任务（真取消：协作式 `cancel_event`）。

        只取消**可取消的那一类**（模组元数据提取）—— 更新检查/批量更新在旧实现里
        也没有取消，且半途取消批量更新会留下"旧文件已删、新文件没下"的中间态
        （服务层 docstring 里记着这条既有缺陷），所以**不提供**那两颗的取消。
        """
        handle = self._handles.get(TASK_META)
        if handle is None:
            return
        try:
            if not self._handle_done(handle):
                handle.cancel()
        except Exception as e:  # noqa: BLE001
            logger.debug("取消模组元数据任务失败: %s", e)

    # ─── 内部：会话 → 快照 ──────────────────────────────────

    def _bridge(self, name: str) -> Any:
        """按注册名取另一个桥（`Dialogs` 的剪贴板口）。取不到返回 None。"""
        registry = getattr(self._engine, "_fmcl_bridges", None) if self._engine is not None else None
        if not isinstance(registry, dict):
            return None
        return registry.get(name)

    def _remember(self, name: str, handle: Any) -> None:
        if handle is not None:
            self._handles[name] = handle

    @staticmethod
    def _handle_done(handle: Any) -> bool:
        """句柄是否已结束。

        注意 `TaskHandle.done` / `TaskHandle.cancelled` 是**属性**（`app/tasks.py`），
        写成 `handle.done()` 会抛 `TypeError: 'bool' object is not callable` ——
        而调用点外面就是 `except Exception`（取消失败不许让界面崩），于是取消变成
        **静默空操作**：事件永远不置位、任务照跑、半份结果照铺（阶段 3.6 单测
        `test_cancel_stops_the_metadata_task_without_publishing_half_a_list` 抓到过：
        `assert 4 == 0`）。所以这两条判定收在这两个小工具里，一处写对。
        """
        try:
            return bool(handle.done)
        except Exception:  # noqa: BLE001 - 非 TaskHandle 的替身：按"没结束"处理
            return False

    @staticmethod
    def _handle_cancelled(handle: Any) -> bool:
        """句柄背后的取消事件是否已置位（同上：**属性**，不是方法）。"""
        try:
            return bool(handle.cancelled)
        except Exception:  # noqa: BLE001
            return False

    def _select_version(self, requested: str, *, scan: bool, explicit_global: bool = False) -> None:
        """定下"这一屏看哪个版本"并（可选）重扫。

        ``explicit_global=True``（= `setVersion("")`）时，空串是**用户在选择器里
        主动选了「全局 .minecraft」**，不能被 `resolve_version_id` 当成"没给版本"
        而替换成某个已安装版本 —— 所以先拦一道。

        ``explicit_global=False``（= `activate()`）时空串的含义是"**路由没给版本**"
        （一级导航进来），必须继续走 `resolve_version_id` 的兜底链：最近使用 →
        第一个已安装 → 一个都没装时才是全局目录。**这两条路的空串不是一回事**：
        曾经把 `activate` 也接到这条短路判断上，症状是"一级导航进来永远看全局目录、
        `last_launched_version` 再也用不上"（2026-10-07 验收反馈修复后的回归）。
        """
        available = self._session.available_versions()
        self._version_ids = list(available)
        if explicit_global:
            target = VERSION_GLOBAL
        else:
            target = resolve_version_id(requested, available, self._last_launched())
        self._session.set_version(target)
        self._version = self._session.version_id
        self._tab = self._session.tab
        self._search = self._session.search
        self._thumbnails = {}
        self._mods_loaded = 0
        self._updates = []
        self.updatesChanged.emit()
        if scan:
            self._reload(force=True)
        else:
            self._publish()

    def _last_launched(self) -> str:
        try:
            return str(getattr(self._context.config, "last_launched_version", "") or "")
        except Exception:  # noqa: BLE001 - 配置异常退化成"没有最近使用版本"
            return ""

    def _refresh_version_choices(self) -> None:
        """装配期只做两件事：**准备选择器的候选** + 把当前值放到"全局目录"上。

        **刻意不解析、不扫描**（`bind()` 里调）：那时 `routeParams` 还没赋值，任何
        "猜一个版本"都会让 `activate(路由版本)` 里那句"和当前一样就跳过"命中 ——
        2026-10-07 用户报的"从哪个版本进来都是第一个"就是这么来的。
        真正的版本由 `activate()` 一次定死。
        """
        if self._session is None:
            return
        try:
            self._version_ids = list(self._session.available_versions())
        except Exception as e:  # noqa: BLE001 - 列表拿不到就是"只有全局那一项"
            logger.warning("取已安装版本列表失败: %s", e)
            self._version_ids = []
        self._version = VERSION_GLOBAL
        self._session.set_version(VERSION_GLOBAL)
        self._tab = self._session.tab
        self._search = self._session.search
        #: 装配期**没有扫描过**（见 `activate` 的 `_scanned_once`）：这一步只准备列表，
        #: 真正的版本与扫描都由第一次 `activate(routeVersion)` 定。
        self._scanned_once = False
        self._publish()

    def _reload(self, *, force: bool, metadata: bool = True) -> None:
        """重扫当前标签页并把结果发出去；模组页顺带起元数据任务。

        ``metadata=False`` 是**取消之后**那一轮的专用档（见 `_apply_metadata_done`）：
        照常扫目录，但不起新的元数据任务，改用会话的降级渲染把目录列表铺出来。
        """
        if self._session is None:
            return
        self.cancel_metadata()
        try:
            result = self._session.refresh(force=force)
        except Exception as e:  # noqa: BLE001 - 扫描抛异常 = 错误态（页面能重试）
            logger.error("扫描资源目录失败: %s", e)
            self._load_state = "error"
            self._error = str(e)
            self._items = []
            self._total = 0
            self._page = 1
            self._page_count = 1
            self._set_status("rm_operation_failed", {"error": str(e)})
            self._publish()
            return
        if self._session.tab == "mods" and self._session.load_state == "loading":
            if not metadata:
                #: 取消之后的降级：`_scan_mods` 对非空目录只给"加载态 + 空列表"，
                #: 所以这里换成"只有文件名的模组行"，否则页面会永远转圈
                self._apply_result(self._session.apply_mods_scan_fallback())
                return
            self._apply_result(result)
            self._start_metadata()
        else:
            self._apply_result(result)
            self._start_thumbnails()

    def cancel_metadata(self) -> None:
        """取消上一轮的模组元数据任务（换版本/换页签/重扫时调）。

        这是**例行作废**（马上就有新一轮），所以置 `_suppress_fallback`：那一轮回来时
        不要再退回"只有文件名的目录列表"，否则它会和紧接着的新一轮抢 `loadState`
        （阶段 3.6 冒烟走查：切到 empty 之后自己跳回 ready）。
        """
        handle = self._handles.pop(TASK_META, None)
        if handle is None:
            return
        try:
            if not self._handle_done(handle):
                self._suppress_fallback = True
                handle.cancel()
        except Exception as e:  # noqa: BLE001
            logger.debug("取消上一轮元数据任务失败: %s", e)

    def _apply_result(self, result: Dict[str, Any]) -> None:
        """把会话的一次结果落成快照并广播。

        注意**先对齐标签页再装饰条目**：`_decorate` 用 `self._tab` 决定 `isMod` /
        `iconName`，而"换页签"这件事只发生在会话里 —— 不对齐的话 `setTab` 之后
        桥还在按旧页签念属性（`tab` / `isMods` / `tabLabelKey` / `tabs` 全是旧的，
        行也按旧页签装饰），阶段 3.6 单测抓到过：`assert 'mods' == 'resourcepacks'`。
        """
        if not isinstance(result, dict):
            return
        if self._session is not None:
            self._tab = self._session.tab
        self._items = [self._decorate(item) for item in result.get("items", [])]
        self._page = int(result.get("page", 1) or 1)
        self._page_count = int(result.get("page_count", 1) or 1)
        self._total = int(result.get("total", 0) or 0)
        self._load_state = str(result.get("state", "empty"))
        self._error = str(result.get("error", ""))
        self._scanned_once = True
        key = str(result.get("status_key", "") or "")
        if key:
            self._set_status(key, result.get("status_params", {}))
        self._publish()

    def _publish(self, *, force_scan: bool = False) -> None:
        """广播一次快照（`force_scan` 时顺带把会话状态也念一遍）。"""
        if self._session is not None and force_scan:
            self._version = self._session.version_id
            self._tab = self._session.tab
            self._search = self._session.search
            self._items = [self._decorate(item) for item in self._session.items]
            self._page = self._session.page
            self._page_count = self._session.page_count
            self._total = self._session.total
            self._load_state = self._session.load_state
            self._error = self._session.error
            self._start_thumbnails()
        self._revision += 1
        self.itemsChanged.emit()

    def _decorate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        """给条目补好**显示用**的字段（QML 里不再拼字符串）。

        `display_name` = 名字 + 旧实现那两个后缀（`rm_disabled_suffix` /
        `rm_non_standard_map`），`path_key` 是缩略图缓存键，`has_thumbnail` 决定
        列表委托是画预览图还是画兜底图标。
        """
        row = dict(item)
        name = str(row.get("name") or row.get("filename") or "???")
        #: 两个后缀是**独立**的（旧实现里地图那条是 `if is_dir and not has_level_dat`
        #: 单独加的，不是 `elif`）—— 所以禁用中的非标准地图两个后缀都有
        if row.get("disabled"):
            name += _("rm_disabled_suffix")
        if row.get("is_dir") and not row.get("has_level_dat"):
            name += _("rm_non_standard_map")
        row["display_name"] = name
        path = str(row.get("path", ""))
        row["path"] = path
        row["path_key"] = path
        #: 图片通道的两条来源合流到同一个字段：模组的 jar 内图标与资源包/光影的预览图
        #: （都注册在 `image://fmcl-thumb/` 下，见 `_register_mod_icons` 与 `_start_thumbnails`）。
        #: `icon_base64` 是**几十 KB 的裸 base64**，绝不能直接递给 QML 的 `Image`
        #: （`Image` 只认 URL），所以这里立刻换成注册后的 URL，原始字段丢掉。
        row["thumbnail"] = self._thumbnails.get(path, "")
        row["has_thumbnail"] = bool(row["thumbnail"])
        row.pop("icon_base64", None)
        row["icon"] = row["thumbnail"]
        row["author"] = str(row.get("author", "") or "")
        row["description"] = str(row.get("description", "") or "")
        row["modid"] = str(row.get("modid", "") or "")
        row["version"] = str(row.get("version", "") or "")
        row["size"] = str(row.get("size", "") or "")
        row["filename"] = str(row.get("filename", "") or "")
        #: 界面用它决定"画模组卡片还是画一行"以及"点启用/禁用按钮"；
        #: 服务层的 `disabled` / `is_dir` 语义原样保留（界面不再自己推断）
        row["isMod"] = self._tab == "mods"
        row["isDir"] = bool(row.get("is_dir"))
        #: 卡片/行左侧的兜底图标（按标签页与状态给一个**存在的**图标名）。
        #: 两个字段是刻意分开的：`icon` 是"要让 QML **画出来的图**"（jar 内图标，
        #: 空串 = 没有），`iconName` 是"没有图时画哪个 `FmIcon`"。合成一个字段会
        #: 让 QML 分不清该画图还是画图标（探针实测过 `Cannot open: …/resourcepack`）。
        row["iconName"] = TAB_ICONS.get(self._tab, "file") if row["isMod"] else (
            "folder" if row["isDir"] else "file"
        )
        return row

    def _set_busy(self, busy: bool, *, cancellable: bool) -> None:
        changed = busy != self._busy or cancellable != self._cancellable
        self._busy = busy
        self._cancellable = cancellable
        if changed:
            self.busyChanged.emit()
            self.progressChanged.emit()

    # ─── 内部：模组元数据 ───────────────────────────────────

    def _start_metadata(self) -> None:
        """起"读模组元数据"任务（旧 `_refresh_mod_list` 的那个线程）。"""
        self._set_busy(True, cancellable=True)
        self._progress = {"message": _("mod_loading_metadata"), "current": 0, "total": 0}
        self.progressChanged.emit()
        try:
            handle = self._session.load_mods(
                on_progress=self._metadata_progress, on_done=self._metadata_done, on_error=self._metadata_error
            )
        except Exception as e:  # noqa: BLE001
            self._metadata_error(e)
            return
        self._remember(TASK_META, handle)

    def _metadata_progress(
        self,
        task_id: int,
        name: str,
        current: int,
        total: int,
        message: str = "",
        level: str = "info",
        title: Optional[str] = None,
    ) -> None:
        """**worker 线程**：`TaskRunner` 的 `on_progress` 契约（见 `app/tasks.py`）。

        注意签名**必须**是这一串：`TaskContext.progress()` 是**按关键字**回调的
        （`task_id=` / `name=` / `current=` / `total=` / …）。写成 `(done, total)`
        会被 `TaskRunner._safe_call` 静默吞掉（`TypeError: got an unexpected keyword
        argument 'task_id'`），症状是页面上那条进度条永远停在"正在读取模组信息…"
        的 0/0 —— 阶段 3.6 单测抓到过（`tests/test_resources_session_bridge.py`）。
        """
        self._post({"kind": "meta_progress", "done": int(current or 0), "total": int(total or 0)})

    def _metadata_done(self, results: Any) -> None:
        """任务函数正常返回（**取消也算正常返回**，见 `_apply_metadata_done`）。"""
        handle = self._handles.get(TASK_META)
        cancelled = self._handle_cancelled(handle) if handle is not None else False
        self._post({"kind": "meta_done", "results": list(results or []), "cancelled": cancelled})

    def _metadata_error(self, error: Any) -> None:
        self._post({"kind": "meta_error", "error": str(error)})

    def _apply_metadata_progress(self, record: Dict[str, Any]) -> None:
        self._progress = {
            "message": _("mod_loading_progress", done=record.get("done", 0), total=record.get("total", 0)),
            "current": int(record.get("done", 0) or 0),
            "total": int(record.get("total", 0) or 0),
        }
        self.progressChanged.emit()

    def _apply_metadata_done(self, record: Dict[str, Any]) -> None:
        """元数据到手：重算列表 + 触发成就（旧 `_on_mod_metadata_loaded`）。

        **取消**也走这条路（`TaskRunner` 把取消当成一次正常结束），所以这里先看
        句柄状态：取消时**不**把半份结果铺到界面上（用户已经离开这一页或换了页签），
        也不触发成就（半份结果会让 `modder_first_mod` 记少）。
        """
        handle = self._handles.pop(TASK_META, None)
        cancelled = bool(record.get("cancelled"))
        if not cancelled and handle is not None:
            #: 句柄状态优先：`_metadata_done` 是在任务线程上读的，取消可能刚好在
            #: 那之后、这条事件被主线程处理之前发生 —— 补一次更准的判定
            cancelled = self._handle_cancelled(handle)
        self._set_busy(False, cancellable=False)
        self._progress = {}
        self.progressChanged.emit()
        if cancelled:
            #: 取消之后列表停在"加载中"会卡住页面，所以退回一次**真实重扫**；
            #: 但这一轮**不再重提元数据**（`metadata=False`）：立刻重提等于把
            #: "取消"变成"重跑"，用户按下的取消按钮就永远停不下来（阶段 3.6
            #: 单测 `test_cancel_...` 抓到过：取消一轮之后 `modCount` 又是 4）。
            #:
            #: "还要不要重扫"由 `_suppress_fallback` 决定：**我们自己取消**的那一轮
            #: （`cancel_metadata()`，即换页签/换版本/重扫前的例行作废）什么都不做 ——
            #: 紧接着就有一轮新的扫描和新的元数据任务，这里再扫一次纯粹是白干，
            #: 而且那一轮会把 `loadState` 又推成别的值（冒烟走查里表现为"切到 empty
            #: 之后自己跳回 ready"，`page.resources.states` 判红）。
            #: 只有**用户按的取消**（`cancel()`）才退回"只有文件名的目录列表"。
            if not self._suppress_fallback:
                try:
                    self._reload(force=True, metadata=False)
                except Exception as e:  # noqa: BLE001 - 重扫失败也不该把异常抛给事件循环
                    logger.debug("取消后重扫失败: %s", e)
                    self._load_state = "empty"
                    self._publish()
            self._suppress_fallback = False
            return
        results = record.get("results", [])
        self._mods_loaded = len(results)
        #: 模组自带图标（jar 里的 `icon_base64`）也走同一条图片通道。放在**主线程**做：
        #: 一页 10 个、每个 48px，解码开销可以忽略；放 worker 也就省几毫秒，却要多一套
        #: 投递（元数据本身已经在 worker 上解过一遍了）。
        self._register_mod_icons(results)
        try:
            self._apply_result(self._session.apply_mods_metadata(results))
        except Exception as e:  # noqa: BLE001 - 元数据应用失败退化成"目录空"
            logger.error("应用模组元数据失败: %s", e)
            self._load_state = "error"
            self._error = str(e)
            self._publish()

    def _register_mod_icons(self, results: Any) -> None:
        """把模组自带的图标（`icon_base64`）注册进图片通道，键 = `路径#icon`。

        与资源包预览图共用 `image://fmcl-thumb/<id>`（同一份缓存与 LRU）。**没有**
        图片通道（`install()` 没被调用）时整段跳过 —— 界面退化成兜底 `FmIcon`。
        """
        if self._thumbnail_provider is None:
            return
        for item in list(results or []):
            data = item.get("icon_base64")
            path = str(item.get("path", ""))
            if not data or not path:
                continue
            try:
                source = self._thumbnail_provider.register(path + "#icon", str(data))
            except Exception as e:  # noqa: BLE001 - 单个图标注册失败不影响列表
                logger.debug("注册模组图标失败 (%s): %s", path, e)
                continue
            if source:
                self._thumbnails[path] = source

    def _apply_metadata_error(self, record: Dict[str, Any]) -> None:
        self._handles.pop(TASK_META, None)
        self._set_busy(False, cancellable=False)
        self._progress = {}
        self.progressChanged.emit()
        logger.error("提取模组元数据失败: %s", record.get("error", ""))
        self._fail("rm_operation_failed", {"error": str(record.get("error", ""))})

    # ─── 内部：更新 ─────────────────────────────────────────

    def _update_progress(
        self,
        task_id: int,
        name: str,
        current: int,
        total: int,
        message: str = "",
        level: str = "info",
        title: Optional[str] = None,
    ) -> None:
        """**worker 线程**：签名同 `_metadata_progress`（`TaskRunner` 的进度契约）。"""
        self._post({"kind": "check_progress", "current": int(current or 0), "total": int(total or 0)})

    def _apply_check_progress(self, record: Dict[str, Any]) -> None:
        self._set_status(
            "mod_checking_updates_progress",
            {"current": record.get("current", 0), "total": record.get("total", 0)},
        )

    def _update_done(self, found: Any) -> None:
        self._post({"kind": "check_done", "found": int(found or 0)})

    def _update_error(self, error: Any) -> None:
        self._post({"kind": "check_error", "error": str(error)})

    def _apply_check_done(self, record: Dict[str, Any]) -> None:
        self._handles.pop(TASK_CHECK, None)
        self._set_busy(False, cancellable=False)
        if self._session is None:
            return
        self._updates = self._collect_updates()
        self.updatesChanged.emit()
        total = int(record.get("total", 0) or 0) or len(self._updates)
        key, params = self._session.update_check_result(int(record.get("found", 0) or 0), total)
        self._set_status(key, params)

    def _apply_check_error(self, record: Dict[str, Any]) -> None:
        self._handles.pop(TASK_CHECK, None)
        self._set_busy(False, cancellable=False)
        error = str(record.get("error", ""))
        if self._session is not None:
            key, params = self._session.update_check_failed(error)
            self._set_status(key, params)
        else:
            self._fail("mod_update_check_failed", {"error": error})

    def _collect_updates(self) -> List[Dict[str, Any]]:
        """把会话的 `{modid: info}` 变成对话框要的列表（排序与旧实现逐字一致）。

        排序键是 `mod_name`（旧实现 `sorted(..., key=lambda x: x[1].get("mod_name", x[0]))`）。
        **不给勾选状态**：旧实现的 `BooleanVar(value=True)` 与复选框都建在对话框里，
        勾选是对话框自己的状态（`ResourceUpdateDialog.checked`），桥不该替它记一份。
        """
        if self._session is None:
            return []
        raw = self._session.updates
        rows = []
        for modid, info in raw.items():
            rows.append(
                {
                    "modid": str(modid),
                    "mod_name": str(info.get("mod_name", modid) or modid),
                    "current_version": str(info.get("current_version", "") or ""),
                    "latest_version": str(info.get("latest_version", "") or ""),
                    "mod_path": str(info.get("mod_path", "") or ""),
                }
            )
        rows.sort(key=lambda r: r["mod_name"])
        return rows

    def _batch_progress(
        self,
        task_id: int,
        name: str,
        current: int,
        total: int,
        message: str = "",
        level: str = "info",
        title: Optional[str] = None,
    ) -> None:
        """**worker 线程**：签名同 `_metadata_progress`（`TaskRunner` 的进度契约）。"""
        self._post({"kind": "batch_progress", "done": int(current or 0), "total": int(total or 0)})

    def _apply_batch_progress(self, record: Dict[str, Any]) -> None:
        self._set_status(
            "mod_update_batch_progress",
            {"done": record.get("done", 0), "total": record.get("total", 0)},
        )

    def _batch_done(self, result: Any) -> None:
        success, failed = (0, 0)
        if isinstance(result, (tuple, list)) and len(result) >= 2:
            success, failed = int(result[0] or 0), int(result[1] or 0)
        self._post({"kind": "batch_done", "success": success, "failed": failed})

    def _apply_batch_done(self, record: Dict[str, Any]) -> None:
        self._handles.pop(TASK_BATCH, None)
        self._set_busy(False, cancellable=False)
        success = int(record.get("success", 0) or 0)
        failed = int(record.get("failed", 0) or 0)
        if self._session is not None:
            self._session.finish_batch()
        self._updates = []
        self.updatesChanged.emit()
        if failed == 0:
            self._set_status("mod_update_batch_done", {"success": success})
            self._notify(_("notify_resource_done"), "success")
        else:
            self._set_status("mod_update_batch_done_partial", {"success": success, "failed": failed})
            self._notify(_("notify_resource_partial", success=success, failed=failed), "warning")
        self._thumbnails = {}
        self._reload(force=False)

    # ─── 内部：缩略图 ───────────────────────────────────────

    def _start_thumbnails(self) -> None:
        """给本页的资源包 / 光影取预览图（旧 `_refresh_current_list` 后半段）。

        去重（`claim_thumbnail`）与代际判定（`thumbnail_is_current`）都在服务层，
        线程池用 `ResourceService.tasks`（**不再自己起线程**：旧实现自带一个
        4 线程的 `TaskRunner`，新架构里任务调度器是全局的）。
        """
        if self._session is None:
            return
        wanted = self._session.thumbnails_wanted()
        if not wanted:
            return
        generation = self._session.bump_thumbnail_generation()
        for item in wanted:
            path = str(item.get("path", ""))
            if not path or path in self._thumbnails:
                continue
            key = self._service.claim_thumbnail(self._inflight, path)
            if key is None:
                continue
            self._thumb_tasks += 1
            try:
                self._service.tasks.submit(
                    self._load_thumbnail,
                    path,
                    name=f"resource.thumb.{self._thumb_tasks}",
                    on_done=lambda data, p=path, g=generation, k=key: self._post(
                        {"kind": "thumb_done", "path": p, "data": data, "generation": g, "key": k}
                    ),
                    on_error=lambda err, p=path, k=key: self._post(
                        {"kind": "thumb_error", "path": p, "error": str(err), "key": k}
                    ),
                )
            except Exception as e:  # noqa: BLE001 - 提交失败就放弃这张图（不影响列表）
                logger.debug("提交缩略图任务失败 (%s): %s", path, e)
                self._service.release_thumbnail(self._inflight, key)
                self._pending_thumbs.pop(key, None)
                continue
            self._pending_thumbs[key] = path

    def _load_thumbnail(self, path: str) -> Optional[str]:
        """任务函数（worker 线程）：解 zip 取预览图，返回**已注册的图片源**。

        这里**不能**调 `ctx.raise_if_cancelled()`：取消是给"用户主动放弃"用的，
        缩略图在列表刷新换代时只是**过时**，代际判定会丢掉它 —— 而它的结果是
        可复用的一张图，丢掉太浪费。旧实现同样不取消缩略图。
        """
        return self._register_thumbnail(path)

    def _register_thumbnail(self, path: str) -> Optional[str]:
        """取图并注册给 QML（返回可直接塞进 `Image.source` 的字符串）。

        **这是本任务唯一一处需要"图片通道"的地方**：服务层的 `load_thumbnail`
        返回 base64，而 QML 的 `Image` 不认 data URI（实测见 `16` §二十二），
        所以走 image provider。具体通道由 `_thumbnail_source()` 决定 ——
        它是**唯一**的决策点，换实现只改那一处。
        """
        data = self._service.load_thumbnail(Path(path), 64)
        if not data:
            return None
        return self._thumbnail_source(path, data)

    def _thumbnail_source(self, path: str, base64_data: str) -> Optional[str]:
        """把 base64 变成 QML 能用的图片源（**唯一的图片通道决策点**）。

        通道是 `app/bridges/thumbnail_provider.py` 的 `QQuickImageProvider`
        （`image://fmcl-thumb/<id>`）；`install()` 没被装配期调用时
        `_thumbnail_provider` 是 None，此时返回 None —— 界面退化成兜底图标，
        与"这张 zip 里没有预览图"是同一种表现，不会报错。
        """
        provider = self._thumbnail_provider
        if provider is None:
            return None
        try:
            return provider.register(path, base64_data)
        except Exception as e:  # noqa: BLE001 - 注册失败退化成"没有预览图"
            logger.debug("注册缩略图失败 (%s): %s", path, e)
            return None

    def _apply_thumbnail_done(self, record: Dict[str, Any]) -> None:
        self._apply_thumbnail(record, ok=True)

    def _apply_thumbnail_error(self, record: Dict[str, Any]) -> None:
        self._apply_thumbnail(record, ok=False)

    def _apply_thumbnail(self, record: Dict[str, Any], *, ok: bool) -> None:
        """缩略图回家：换代过期的直接丢，否则进缓存（旧 `_apply_thumbnail`，D-92/D-114）。"""
        key = str(record.get("key", ""))
        path = str(record.get("path", ""))
        self._pending_thumbs.pop(key, None)
        if self._service is not None and key:
            self._service.release_thumbnail(self._inflight, key)
        if self._session is None or not self._service.thumbnail_is_current(
            int(record.get("generation", 0) or 0), self._session.thumbnail_generation
        ):
            return
        data = record.get("data")
        if ok and data:
            self._thumbnails[path] = str(data)
        self._apply_thumbnails_to_items()
        self._revision += 1
        self.thumbnailsChanged.emit()

    def _apply_thumbnails_to_items(self) -> None:
        for row in self._items:
            path = str(row.get("path", ""))
            source = self._thumbnails.get(path, "")
            row["thumbnail"] = source
            row["has_thumbnail"] = bool(source)

    # ─── 内部：状态 / 提示 / 投递 ───────────────────────────

    def _report_install(self, result: Any) -> bool:
        """安装结果 → 状态栏文案；返回是否算成功（旧 `_report_install_result`）。"""
        outcome = str(getattr(result, "outcome", "") or "")
        name = str(getattr(result, "name", "") or "")
        ext = str(getattr(result, "ext", "") or "")
        error = str(getattr(result, "error", "") or "")
        if outcome == "exists":
            self._set_status("rm_file_exists", {"name": name})
        elif outcome == "map_exists":
            self._set_status("rm_map_exists", {"name": name})
        elif outcome == "unsupported":
            self._set_status("rm_unsupported_format", {"ext": ext})
        elif outcome == "failed":
            self._set_status("rm_install_failed", {"error": error})
        return bool(getattr(result, "ok", False))

    def _set_status(self, key: str, params: Optional[Dict[str, Any]] = None) -> None:
        """状态栏：把 i18n 键翻成当前语言的一句话（参数里的 `label_key` 也要翻）。"""
        data = dict(params or {})
        label_key = str(data.pop("label_key", "") or "")
        if label_key:
            data["label"] = _(label_key)
        try:
            text = _(str(key), **data)
        except Exception as e:  # noqa: BLE001 - 缺参也不能让界面炸（键名本身就是线索）
            logger.warning("翻译状态文案 %s 失败: %s", key, e)
            text = str(key)
        self.statusMessage.emit(text, "info")

    def _fail(self, key: str, params: Optional[Dict[str, Any]] = None) -> None:
        """失败：状态栏 + Toast 都要有（"失败别静默"是 3.5 的验收反馈）。"""
        data = dict(params or {})
        label_key = str(data.pop("label_key", "") or "")
        if label_key:
            data["label"] = _(label_key)
        try:
            text = _(str(key), **data)
        except Exception as e:  # noqa: BLE001
            logger.warning("翻译失败文案 %s 失败: %s", key, e)
            text = str(key)
        self.statusMessage.emit(text, "error")
        self._notify(text, "error")

    def _notify(self, message: str, level: str) -> None:
        self.noticeRequested.emit(str(message), str(level))

    def _post(self, record: Dict[str, Any]) -> None:
        """worker 线程 → 主线程（只排队 + emit，见模块文档"线程约定"）。"""
        try:
            with self._pending_lock:
                self._pending.append(dict(record))
            self.posted.emit()
        except Exception as e:  # noqa: BLE001 - 投递失败也不能让任务线程崩
            logger.debug("投递资源事件失败: %s", e)

    @Slot()
    def _deliver(self) -> None:
        """主线程：把排队的事件逐条落成属性 / 信号。"""
        with self._pending_lock:
            records = self._pending
            self._pending = []
        for record in records:
            kind = str(record.get("kind", ""))
            try:
                handler = self._DELIVER.get(kind)
                if handler is None:
                    logger.debug("收到未知的资源事件: %s", kind)
                    continue
                handler(self, record)
            except Exception as e:  # noqa: BLE001 - 单条事件坏了不该拖垮后面的
                logger.warning("处理资源事件 %s 失败: %s", kind, e)

    #: 事件分发表（`kind` → 处理函数）。写成类属性是为了让"有哪些事件"一眼看全，
    #: 也避免 `_deliver` 里长一条 if/elif 链。赋值语句在本模块末尾。
    _DELIVER: Dict[str, Any] = {}


ResourcesBridge._DELIVER = {
    "meta_progress": ResourcesBridge._apply_metadata_progress,
    "meta_done": ResourcesBridge._apply_metadata_done,
    "meta_error": ResourcesBridge._apply_metadata_error,
    "check_progress": ResourcesBridge._apply_check_progress,
    "check_done": ResourcesBridge._apply_check_done,
    "check_error": ResourcesBridge._apply_check_error,
    "batch_progress": ResourcesBridge._apply_batch_progress,
    "batch_done": ResourcesBridge._apply_batch_done,
    "thumb_done": ResourcesBridge._apply_thumbnail_done,
    "thumb_error": ResourcesBridge._apply_thumbnail_error,
}


def install(engine: Any, name: str = QML_NAME) -> Any:
    """装配期把缩略图图片通道接到 `Resources` 桥与引擎上（**在 `engine.load()` 之前**）。

    为什么要单独一步而不是让桥在 `bind()` 里自己找：`register_bridges()` 调
    `bind()` 的时机**早于** `main_qml` 注册图片提供者那几行（`bind()` 在循环里、
    提供者在循环之后），所以 `bind()` 期问引擎要提供者一定是 None。
    这里显式接一次，注册顺序就不再是个隐含前提。

    返回桥对象（没装成返回 None）—— 与 `icon_provider.install()` 一样，
    失败**不抛**：没有预览图这一件事不该挡住启动。
    """
    try:
        from app.bridges.thumbnail_provider import install as install_thumb_provider

        provider = install_thumb_provider(engine)
    except Exception as e:  # noqa: BLE001 - 通道装不上 = 界面用兜底图标
        logger.warning("注册缩略图 provider 失败（资源包/光影将只显示兜底图标）: %s", e)
        return None
    bridge = (getattr(engine, "_fmcl_bridges", None) or {}).get(name)
    if bridge is None:
        logger.warning("没有找到 %s 桥，缩略图通道未接线", name)
        return None
    bridge._thumbnail_provider = provider
    return bridge


__all__ = ["QML_NAME", "ResourcesBridge", "install"]

