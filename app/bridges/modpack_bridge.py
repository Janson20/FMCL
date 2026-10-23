"""整合包桥 —— QML 侧的 `Modpack` 上下文属性（阶段 3 任务 3.8，对照表 L-52 ~ L-54）。

## 它在链路的哪一环

    qml/pages/versions/ModpackPage.qml          客户端安装 / 服务器开服（两种模式共用一页）
    qml/pages/versions/ModpackBrowserPage.qml   在线浏览 + 版本选择 + 下载（旧三个窗口里的浏览器）
        ↓ 取属性 / 调槽
    app/bridges/modpack_bridge.py（本模块，**零业务规则**）
        ↓ 转发
    services/modpack_service.py（搜索 / 版本分组 / 六格式元数据 / 安装入口 / 进度快照）
        ↓ 委托
    modrinth.py + launcher/mrpack.py 等五个 mixin

旧实现是三扇独立 Tk 窗口（浏览 703 行 / 安装 638 行 / 开服 479 行），
阶段 1 已把"搜索、分组、格式识别、安装入口、百分比口径"搬进服务层，所以本任务的
正文是**桥 + 两页 QML + 四条路由**。

## 用户裁决（2026-10-23）

* **一条安装页承担客户端与服务器两种模式**：两者的文件选择、信息展示、可选组件、
  进度与结果完全同形，只有"安装入口 + 服务器名 + 成功文案"三处不同。照旧拆两个
  页面会让这 80% 的界面复制两份（3.7 已经因为"搜索页和结果页重复"返工过一次）。
* **浏览器仍是独立页**：旧实现是模态子窗口，新界面用 `versions/modpack/browse` /
  `servers/modpack/browse` 两层路由表达，面包屑能正确回到调用它的安装页。
* **服务器只收 .mrpack**（旧 `ModpackServerWindow._select_file` 的文件类型就是如此）；
  客户端页才收 .mrpack / .zip 六种格式。
* 验证走精简档（桥测试 + 一次性 QML 探针 + 截图 + 全量 pytest + 五道闸门）。

## 线程与世代

所有网络/安装动作都走 `context.tasks.submit`。回调可能落在工作线程，因此
**只排队 + emit `posted`**，真正落属性的是主线程上的 `_deliver()`。搜索、版本列表、
元数据各有一个只增不减的 `seq`（3.7 的 D-93 桥侧同形），晚到的旧结果整批丢弃。

安装进度不能读旧 Tk 那个 `launcher._mp_progress` 的"最后一帧"就完事：核心在安装
开始时**替换**整个字典，所以这里记住调用前的对象身份，只有看到新字典才开始投递
快照。这样上一次安装留下的 `phase=done` 不会让新进度条一开局就满格。
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from services.browse_common import DEFAULT_PAGE_SIZE, format_downloads, normalize_search, page_slice, total_pages
from services.i18n_service import _
from services.modpack_service import (
    TARGET_CLIENT,
    TARGET_SERVER,
    TARGETS,
    VERSION_PAGE_SIZE,
    default_instance_name,
    installed_instance_ids,
    launcher_callbacks,
    validate_instance_name,
)

logger = logging.getLogger("app.bridges.modpack_bridge")

#: 契约里的 QML 注册名。
QML_NAME = "Modpack"

#: 后台任务名。
TASK_SEARCH = "modpack.search"
TASK_VERSIONS = "modpack.versions"
TASK_INFO = "modpack.info"
TASK_DOWNLOAD = "modpack.download"
TASK_INSTALL = "modpack.install"

#: 每页条数（旧 `ModpackBrowserWindow.PAGE_SIZE = 10`）。
PAGE_SIZE = DEFAULT_PAGE_SIZE

#: 状态值（页面内部用；不直接交给 FmPage —— 安装页的工具区永远可操作）。
STATE_IDLE = "idle"
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_EMPTY = "empty"
STATE_ERROR = "error"

#: 浏览页的两种视图：结果列表 / 某个整合包的版本列表（旧实现是子窗口）。
VIEW_RESULTS = "results"
VIEW_VERSIONS = "versions"

#: 安装进度轮询周期（旧两个窗口都是 `after(200, ...)`）。
PROGRESS_POLL_MS = 200


def _new_search_state() -> Dict[str, Any]:
    return {
        "query": "",
        "page": 1,
        "hits": [],
        "ai_hits": None,
        "total": 0,
        "state": STATE_IDLE,
        "error": "",
        "ai": False,
        "keywords": [],
        "seq": 0,
    }


def _new_versions_state() -> Dict[str, Any]:
    return {
        "view": VIEW_RESULTS,
        "state": STATE_IDLE,
        "error": "",
        "project_id": "",
        "title": "",
        "all": [],
        "raw": {},
        "shown": 0,
        "total": 0,
        "seq": 0,
    }


def _new_file_state() -> Dict[str, Any]:
    return {"path": "", "state": STATE_IDLE, "error": "", "raw": {}, "info": {}, "seq": 0}


class ModpackBridge(QObject):
    """上下文属性 `Modpack`。"""

    #: 搜索结果 / 分页 / AI 关键词变了。
    searchChanged = Signal()
    #: 浏览器视图 / 版本列表 / 下载状态变了。
    versionsChanged = Signal()
    #: 当前模式的文件、元数据、可选组件变了。
    fileChanged = Signal()
    #: 忙碌、进度、结果变了。
    installChanged = Signal()
    #: 状态条文案（**已翻译**）。
    statusMessage = Signal(str, str)
    #: 一条提示（错误 / 下载完成），页面转成 Toast。
    noticeRequested = Signal(str, str)
    #: 浏览器下载完成，参数 = 目标模式 / 文件路径。页面据此返回安装页。
    fileReady = Signal(str, str)
    #: 安装结束，参数 = 目标模式 / 是否成功 / 结果文本。
    installFinished = Signal(str, bool, str)
    #: 服务器装好了（参数 = 服务器目录名）—— 3.9 的服务器列表据此刷新；
    #: 现在还没有服务器桥，先把这个口子留出来（旧实现是 `_refresh_parent_server_list`）。
    serverInstalled = Signal(str)
    #: 内部投递信号：worker 只排队并 emit 它。
    posted = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._service: Any = None
        self._context: Any = None
        self._tasks: Any = None
        self._launcher: Any = None
        self._callbacks: Dict[str, Any] = {}
        self._config: Any = None

        self._mode = TARGET_CLIENT
        self._target = TARGET_CLIENT
        self._search = _new_search_state()
        self._versions = _new_versions_state()
        self._files: Dict[str, Dict[str, Any]] = {key: _new_file_state() for key in TARGETS}
        self._pending_files: Dict[str, str] = {key: "" for key in TARGETS}
        self._selected: Dict[str, List[str]] = {key: [] for key in TARGETS}
        self._server_names: Dict[str, str] = {key: "" for key in TARGETS}
        self._naming = False
        self._instance_name = ""
        self._instance_error = ""
        self._installed_ids: List[str] = []

        self._busy = False
        self._downloading = False
        self._install_state = ""
        self._progress = 0.0
        self._progress_text = ""
        self._mrpack_text = ""
        self._vanilla_text = ""
        self._result_text = ""
        self._result_level = ""
        self._install_target = TARGET_CLIENT

        self._versions_bridge: Any = None
        self._handles: Dict[str, Any] = {}
        self._pending: List[Dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        self._progress_stop = threading.Event()
        self._closing = False
        self.posted.connect(self._deliver)

    # ─── 装配期注入 ─────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        """取服务 / 任务调度器 / 核心启动器 / 配置；拿不到的项只降级，不挡住装配。"""
        self._context = context
        self._service = self._service_of(context, "modpack")
        if self._service is None:
            logger.warning("整合包服务不可用：整合包页只会显示错误态")
        self._tasks = getattr(context, "tasks", None)
        if self._tasks is None:
            logger.warning("上下文里没有 tasks：整合包搜索/安装将不可用")
        self._launcher = self._launcher_of(context)
        self._callbacks = launcher_callbacks(self._launcher)
        try:
            self._config = context.config if context is not None else None
        except Exception as e:  # noqa: BLE001 - 配置坏了只影响 AI 搜索
            logger.warning("取配置失败（AI 搜索将不可用）: %s", e)

    @staticmethod
    def _service_of(context: Any, name: str) -> Any:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("整合包桥取服务 %s 失败: %s", name, e)
            return None

    @staticmethod
    def _launcher_of(context: Any) -> Any:
        if context is None:
            return None
        try:
            return context.try_get("launcher")
        except Exception as e:  # noqa: BLE001
            logger.warning("取 launcher 失败: %s", e)
            return None

    def use_engine(self, engine: Any) -> None:
        """接上 `Versions` 桥：客户端装完后刷新版本列表（旧 `_refresh_versions`）。"""
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        self._versions_bridge = registry.get("Versions")

    def detach(self) -> None:
        """退出：停掉进度轮询，取消排队/在跑的下载与查询任务（安装本身不硬停）。"""
        self._closing = True
        self._progress_stop.set()
        for name, handle in list(self._handles.items()):
            if name == TASK_INSTALL:
                continue
            try:
                handle.cancel()
            except Exception as e:  # noqa: BLE001
                logger.warning("取消整合包任务 %s 失败: %s", name, e)
        self._handles.clear()

    # ─── 基础属性 ───────────────────────────────────────────

    @Property(bool, constant=True)
    def available(self) -> bool:
        return self._service is not None

    @Property(str, notify=fileChanged)
    def mode(self) -> str:
        return self._mode

    @Property(str, notify=versionsChanged)
    def target(self) -> str:
        """浏览器这次下载给谁（client / server）。"""
        return self._target

    @Slot(str)
    def activate(self, mode: str) -> None:
        """安装页入场：定模式，并接住浏览器刚下载完的文件。"""
        wanted = str(mode or "")
        if wanted not in TARGETS:
            wanted = TARGET_CLIENT
        changed = wanted != self._mode
        self._mode = wanted
        self._target = wanted
        if changed:
            #: 切模式时不要带着另一个模式的"安装完成/失败"结果过去：它会把服务器页
            #: 的下半屏染成上一次客户端安装的进度卡（实测）。安装中则保留，免得
            #: 进度被切页抹掉。
            if not self._busy:
                self._install_target = wanted
                self._install_state = ""
                self._progress = 0.0
                self._progress_text = ""
                self._mrpack_text = ""
                self._vanilla_text = ""
                self._result_text = ""
                self._result_level = ""
                self._reset_naming()
                self.installChanged.emit()
            self.fileChanged.emit()
        self._claim_pending_file()

    @Slot(str)
    def activateBrowser(self, target: str) -> None:  # noqa: N802
        """浏览页入场：只定下载去向，**不碰**安装页正在编辑的文件。"""
        wanted = str(target or "")
        if wanted not in TARGETS:
            wanted = TARGET_CLIENT
        if wanted != self._target:
            self._target = wanted
            self.versionsChanged.emit()

    def _claim_pending_file(self) -> None:
        path = self._pending_files.get(self._mode, "")
        if not path:
            return
        self._pending_files[self._mode] = ""
        self.setFilePath(path)

    # ─── 搜索：属性 ─────────────────────────────────────────

    @Property(str, notify=searchChanged)
    def query(self) -> str:
        return str(self._search["query"])

    @Property("QVariantList", notify=searchChanged)
    def items(self) -> List[Dict[str, Any]]:
        if self._search["ai_hits"] is None:
            #: 普通搜索：后端已经按 offset/limit 给了这一页，不能再切一次。
            return [self._decorate_hit(hit) for hit in self._search["hits"]]
        page = int(self._search["page"] or 1)
        return [self._decorate_hit(hit) for hit in page_slice(self._search["ai_hits"], (page - 1) * PAGE_SIZE, PAGE_SIZE)]

    @Property(int, notify=searchChanged)
    def page(self) -> int:
        return int(self._search["page"] or 1)

    @Property(int, notify=searchChanged)
    def pageCount(self) -> int:  # noqa: N802
        source = self._visible_hits()
        if self._search["ai_hits"] is not None:
            return total_pages(len(source), PAGE_SIZE)
        return total_pages(int(self._search["total"] or 0), PAGE_SIZE)

    @Property(int, notify=searchChanged)
    def total(self) -> int:
        return int(self._search["total"] or 0)

    @Property(str, notify=searchChanged)
    def loadState(self) -> str:  # noqa: N802
        return str(self._search["state"])

    @Property(str, notify=searchChanged)
    def loadError(self) -> str:  # noqa: N802
        return str(self._search["error"])

    @Property(bool, notify=searchChanged)
    def aiBusy(self) -> bool:  # noqa: N802
        return bool(self._search["ai"])

    @Property("QVariantList", notify=searchChanged)
    def aiKeywords(self) -> List[str]:  # noqa: N802
        return list(self._search["keywords"])

    @Property(bool, notify=searchChanged)
    def aiAvailable(self) -> bool:  # noqa: N802
        return bool(self._ai_token())

    @Property(int, notify=searchChanged)
    def rangeStart(self) -> int:  # noqa: N802
        if not self._visible_hits():
            return 0
        return (int(self._search["page"] or 1) - 1) * PAGE_SIZE + 1

    @Property(int, notify=searchChanged)
    def rangeEnd(self) -> int:  # noqa: N802
        source = self._visible_hits()
        if not source:
            return 0
        if self._search["ai_hits"] is not None:
            total = len(source)
        else:
            total = int(self._search["total"] or 0)
        return min(int(self._search["page"] or 1) * PAGE_SIZE, max(total, len(source)))

    @Slot(str)
    def setQuery(self, text: str) -> None:  # noqa: N802
        self._search["query"] = normalize_search(text)
        self.searchChanged.emit()

    @Slot()
    def search(self) -> None:
        self._start_search(ai=False)

    @Slot()
    def aiSearch(self) -> None:  # noqa: N802
        query = normalize_search(self._search["query"])
        if not query:
            self._start_search(ai=False)
            return
        token = self._ai_token()
        if not token:
            self._notify(_("ai_search_login_required"), "warning")
            return
        self._start_search(ai=True, token=token)

    @Slot(int)
    def goto(self, page: int) -> None:  # noqa: N802
        pages = self.pageCount
        target = max(1, min(int(page or 1), pages))
        if target == int(self._search["page"] or 1):
            return
        self._search["page"] = target
        if self._search["ai_hits"] is not None:
            self.searchChanged.emit()
            return
        #: 普通搜索按旧实现 offset/limit 向后端取页；AI 结果已整批在本地，只切片。
        self._start_search(ai=False, keep_page=True)

    def _visible_hits(self) -> List[Dict[str, Any]]:
        cached = self._search["ai_hits"]
        return list(cached) if cached is not None else list(self._search["hits"])


    # ─── 浏览器：结果 / 版本视图 ─────────────────────────────

    @Property(str, notify=versionsChanged)
    def browserView(self) -> str:  # noqa: N802
        return str(self._versions["view"])

    @Property(str, notify=versionsChanged)
    def versionsTitle(self) -> str:  # noqa: N802
        return str(self._versions["title"])

    @Property(str, notify=versionsChanged)
    def versionsState(self) -> str:  # noqa: N802
        return str(self._versions["state"])

    @Property(str, notify=versionsChanged)
    def versionsError(self) -> str:  # noqa: N802
        return str(self._versions["error"])

    @Property("QVariantList", notify=versionsChanged)
    def versionRows(self) -> List[Dict[str, Any]]:  # noqa: N802
        rows = self._versions["all"]
        shown = min(int(self._versions["shown"] or 0), len(rows))
        return [self._decorate_version_row(row) for row in rows[:shown]]

    @Property(int, notify=versionsChanged)
    def versionsShown(self) -> int:  # noqa: N802
        return int(self._versions["shown"] or 0)

    @Property(int, notify=versionsChanged)
    def versionsRowTotal(self) -> int:  # noqa: N802
        return len(self._versions["all"])

    @Property(int, notify=versionsChanged)
    def versionsTotal(self) -> int:  # noqa: N802
        return int(self._versions["total"] or 0)

    @Property(int, notify=versionsChanged)
    def versionsShownActual(self) -> int:  # noqa: N802
        """已显示的真实版本条数（不含分组头；旧实现把分组头也算进 shown）。"""
        shown = int(self._versions["shown"] or 0)
        return sum(1 for row in self._versions["all"][:shown] if row.get("_header") is None)

    @Property(bool, notify=versionsChanged)
    def versionsCanLoadMore(self) -> bool:  # noqa: N802
        return int(self._versions["shown"] or 0) < len(self._versions["all"])

    @Slot(str, str)
    def openVersions(self, projectId: str, title: str) -> None:  # noqa: N802
        """点结果卡片：拉这个整合包的全部版本，切到版本视图。"""
        if self._service is None or self._tasks is None:
            return
        seq = int(self._versions["seq"] or 0) + 1
        self._versions.update(
            {
                "view": VIEW_VERSIONS,
                "state": STATE_LOADING,
                "error": "",
                "project_id": str(projectId or ""),
                "title": str(title or ""),
                "all": [],
                "raw": {},
                "shown": 0,
                "total": 0,
                "seq": seq,
            }
        )
        self.versionsChanged.emit()
        self._set_status_key("mp_browser_fetching_versions", {"title": str(title or "")})
        pid = str(projectId or "")

        def work() -> List[Dict[str, Any]]:
            return list(self._service.fetch_modpack_versions(pid) or [])

        self._submit(work, {"kind": "versions", "seq": seq}, name=TASK_VERSIONS)

    @Slot()
    def backToResults(self) -> None:  # noqa: N802
        """版本视图返回结果列表（不重新搜索）。"""
        self._versions["view"] = VIEW_RESULTS
        self.versionsChanged.emit()

    @Slot()
    def loadMoreVersions(self) -> None:  # noqa: N802
        """每次追加 50 条（含分组头，旧实现逐字如此）。"""
        total = len(self._versions["all"])
        shown = int(self._versions["shown"] or 0)
        if shown >= total:
            return
        self._versions["shown"] = min(shown + VERSION_PAGE_SIZE, total)
        self.versionsChanged.emit()

    @Slot()
    def retryVersions(self) -> None:  # noqa: N802
        """版本列表失败态的重试（重新拉同一个项目）。"""
        self.openVersions(str(self._versions["project_id"]), str(self._versions["title"]))

    @Slot(str)
    def downloadVersion(self, versionId: str) -> None:  # noqa: N802
        """下载选中的版本文件；成功后把路径交给安装页（旧实现的回调）。"""
        if self._downloading or self._service is None or self._tasks is None:
            return
        version_data = self._versions["raw"].get(str(versionId or ""))
        if not isinstance(version_data, dict):
            self._notify(_("mp_browser_download_error", error=""), "error")
            return
        self._downloading = True
        self._versions["error"] = ""
        self.versionsChanged.emit()
        self.installChanged.emit()
        title = str(self._versions["title"] or "")
        version_number = str(version_data.get("version_number", "") or "")
        self._set_status_key("mp_browser_downloading", {"title": title, "version": version_number})
        pid = str(self._versions["project_id"] or "")
        target = str(self._target or TARGET_CLIENT)

        def on_status(message: str) -> None:
            self._post({"kind": "download-status", "text": str(message or "")})

        def work() -> Any:
            return self._service.download_modpack_version(pid, version_data, status_callback=on_status)

        self._submit(work, {"kind": "download", "target": target}, name=TASK_DOWNLOAD)

    # ─── 文件 / 元数据 ──────────────────────────────────────

    def _file(self) -> Dict[str, Any]:
        return self._files[self._mode]

    @Property(str, notify=fileChanged)
    def filePath(self) -> str:  # noqa: N802
        return str(self._file()["path"])

    @Property(str, notify=fileChanged)
    def fileName(self) -> str:  # noqa: N802
        path = str(self._file()["path"])
        return os.path.basename(path) if path else ""

    @Property(bool, notify=fileChanged)
    def hasFile(self) -> bool:  # noqa: N802
        return bool(self._file()["path"])

    @Property(str, notify=fileChanged)
    def fileState(self) -> str:  # noqa: N802
        return str(self._file()["state"])

    @Property(str, notify=fileChanged)
    def fileError(self) -> str:  # noqa: N802
        return str(self._file()["error"])

    @Property("QVariantMap", notify=fileChanged)
    def packInfo(self) -> Dict[str, Any]:  # noqa: N802
        return dict(self._file()["info"])

    @Property(str, notify=fileChanged)
    def packName(self) -> str:  # noqa: N802
        return str(self._file()["info"].get("name") or _("mp_unknown_modpack"))

    @Property(str, notify=fileChanged)
    def mcVersionText(self) -> str:  # noqa: N802
        version = str(self._file()["info"].get("mc_version") or "")
        return _("mp_mc_version", version=version) if version else _("mp_unknown_version")

    @Property(str, notify=fileChanged)
    def formatText(self) -> str:  # noqa: N802
        pack_format = str(self._file()["info"].get("format") or "")
        if not pack_format:
            return ""
        if self._service is not None:
            label = self._service.format_label(pack_format)
        else:
            label = {"text": pack_format, "key": ""}
        key = str(label.get("key") or "")
        if key:
            return _(key)
        return str(label.get("text") or pack_format)

    @Property(str, notify=fileChanged)
    def loaderText(self) -> str:  # noqa: N802
        return str(self._file()["info"].get("loader_text") or "")

    @Property("QVariantList", notify=fileChanged)
    def components(self) -> List[Dict[str, Any]]:
        """可选/只读组件；selected 只对可勾选项有意义。"""
        chosen = set(self._selected.get(self._mode, []))
        rows: List[Dict[str, Any]] = []
        for item in self._file()["info"].get("components") or []:
            name = str(item.get("name", "") or "")
            rows.append(
                {
                    "name": name,
                    "selectable": bool(item.get("selectable")),
                    "selected": name in chosen if item.get("selectable") else False,
                }
            )
        return rows

    @Property("QVariantList", notify=fileChanged)
    def selectedComponents(self) -> List[str]:  # noqa: N802
        return list(self._selected.get(self._mode, []))

    @Property(str, notify=fileChanged)
    def serverName(self) -> str:  # noqa: N802
        return str(self._server_names.get(self._mode, ""))

    @Slot(str)
    def setServerName(self, text: str) -> None:  # noqa: N802
        value = str(text or "").strip()
        if value == self._server_names.get(self._mode, ""):
            return
        self._server_names[self._mode] = value
        self.fileChanged.emit()

    @Slot("QUrl")
    def setFileUrl(self, url: Any) -> None:  # noqa: N802
        """FileDialog 给的是 url；折成本地路径（QUrl.toLocalFile 处理 UNC/百分号）。"""
        try:
            path = url.toLocalFile() if url is not None else ""
        except Exception as e:  # noqa: BLE001
            logger.warning("文件 URL 折算失败 (%r): %s", url, e)
            path = ""
        self.setFilePath(str(path or ""))

    @Slot(str)
    def setFilePath(self, path: str) -> None:  # noqa: N802
        """选定文件：清掉旧元数据并后台解析（六种格式的探测在服务层）。"""
        wanted = os.path.abspath(str(path or "")) if str(path or "").strip() else ""
        state = self._file()
        if wanted == state["path"] and state["state"] in (STATE_LOADING, STATE_READY):
            return
        state.update({"path": wanted, "state": STATE_IDLE, "error": "", "raw": {}, "info": {}, "seq": 0})
        self._selected[self._mode] = []
        self._reset_naming()
        self.fileChanged.emit()
        self.installChanged.emit()
        if wanted:
            self._load_info(wanted)

    @Slot()
    def clearFile(self) -> None:  # noqa: N802
        self._select_path("")

    @Slot()
    def retryInfo(self) -> None:  # noqa: N802
        path = str(self._file()["path"])
        if path:
            self._load_info(path)

    @Slot(str)
    def toggleComponent(self, name: str) -> None:  # noqa: N802
        """勾选/取消一个可选组件（只有 mrpack 的 optionalFiles 会走到）。"""
        value = str(name or "")
        if not value:
            return
        chosen = list(self._selected.get(self._mode, []))
        if value in chosen:
            chosen.remove(value)
        else:
            chosen.append(value)
        self._selected[self._mode] = chosen
        self.fileChanged.emit()

    # ─── 安装：属性 ─────────────────────────────────────────

    @Property(bool, notify=installChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=versionsChanged)
    def downloading(self) -> bool:
        return self._downloading

    @Property(bool, notify=installChanged)
    def canInstall(self) -> bool:  # noqa: N802
        return bool(
            self._service is not None
            and self._tasks is not None
            and self._file()["info"]
            and not self._busy
            and not self._downloading
            and not self._naming
            and self._install_state != "done"
        )

    @Property(bool, notify=installChanged)
    def naming(self) -> bool:
        """是否停在「给实例命名」这一步（客户端安装）。"""
        return bool(self._naming)

    @Property(str, notify=installChanged)
    def instanceName(self) -> str:  # noqa: N802
        return self._instance_name

    @Property(str, notify=installChanged)
    def instanceNameError(self) -> str:  # noqa: N802
        """已翻译的错误（空串 = 可点下一步）。"""
        key = str(self._instance_error or "")
        if not key:
            return ""
        try:
            if key == "rename_instance_exists":
                return _("rename_instance_exists", name=self._instance_name)
            return _(key)
        except Exception:  # noqa: BLE001 - 文案失败不挡住安装
            return key

    @Property(bool, notify=installChanged)
    def instanceNameValid(self) -> bool:  # noqa: N802
        return bool(self._naming and self._instance_name and not self._instance_error)

    @Property(str, notify=installChanged)
    def installState(self) -> str:  # noqa: N802
        return self._install_state

    @Property(float, notify=installChanged)
    def progress(self) -> float:
        return float(self._progress)

    @Property(str, notify=installChanged)
    def progressText(self) -> str:  # noqa: N802
        return self._progress_text

    @Property(str, notify=installChanged)
    def mrpackText(self) -> str:  # noqa: N802
        return self._mrpack_text

    @Property(str, notify=installChanged)
    def vanillaText(self) -> str:  # noqa: N802
        return self._vanilla_text

    @Property(str, notify=installChanged)
    def resultText(self) -> str:  # noqa: N802
        return self._result_text

    @Property(str, notify=installChanged)
    def resultLevel(self) -> str:  # noqa: N802
        return self._result_level

    @Property(str, notify=installChanged)
    def installTarget(self) -> str:  # noqa: N802
        return self._install_target


    # ─── 安装：命名步骤 ─────────────────────────────────────

    def _reset_naming(self) -> None:
        self._naming = False
        self._instance_name = ""
        self._instance_error = ""

    def _validate_name(self) -> None:
        self._instance_error = validate_instance_name(self._instance_name, self._installed_ids)

    def _installed(self) -> List[str]:
        if self._launcher is None:
            self._launcher = self._launcher_of(self._context)
        return installed_instance_ids(self._launcher)

    @Slot()
    def beginInstallNaming(self) -> None:  # noqa: N802
        """正式安装前：默认用整合包名，先过命名/重名这一关。"""
        if self._mode == TARGET_SERVER:
            #: 服务器有独立的「服务器名称」输入框，不套用实例命名步骤。
            self.install()
            return
        if self._busy or self._downloading:
            return
        info = self._file()["info"]
        if not info:
            self._notify(_("mp_invalid_file"), "error")
            return
        self._installed_ids = self._installed()
        self._instance_name = default_instance_name(str(info.get("name") or ""))
        self._naming = True
        self._validate_name()
        self.installChanged.emit()

    @Slot()
    def backToInfo(self) -> None:  # noqa: N802
        self._reset_naming()
        self.installChanged.emit()

    @Slot(str)
    def setInstanceName(self, text: str) -> None:  # noqa: N802
        value = str(text or "")
        if value == self._instance_name:
            return
        self._instance_name = value
        self._validate_name()
        self.installChanged.emit()

    # ─── 安装：动作 ─────────────────────────────────────────

    @Slot()
    def install(self) -> None:
        """提交安装任务（客户端 / 服务器由当前模式决定）。"""
        state = self._file()
        if self._busy or self._downloading:
            return
        if not state["info"] or not state["path"]:
            self._notify(_("mp_invalid_file"), "error")
            return
        if self._service is None or self._tasks is None:
            self._notify(_("mp_read_error", error="service_unavailable"), "error")
            return
        target = self._mode
        path = str(state["path"])
        pack_format = str(state["info"].get("format") or "mrpack")
        optional = list(self._selected.get(target, []))
        server_name = str(self._server_names.get(target, "") or "")
        instance_name = ""
        if target == TARGET_CLIENT:
            if not self._installed_ids:
                self._installed_ids = self._installed()
            if not str(self._instance_name or "").strip():
                self._instance_name = default_instance_name(str(state["info"].get("name") or ""))
            self._validate_name()
            if self._instance_error:
                self._naming = True
                self.installChanged.emit()
                self._notify(self.instanceNameError, "error")
                return
            instance_name = self._instance_name.strip()
            self._naming = False
        callbacks = self._callbacks or launcher_callbacks(self._launcher)
        if self._launcher is None:
            self._launcher = self._launcher_of(self._context)
            callbacks = launcher_callbacks(self._launcher)
        self._callbacks = callbacks
        if target == TARGET_SERVER:
            if not callbacks.get("install_mrpack_server"):
                self._notify(_("mp_server_install_error", error="install_callback_unavailable"), "error")
                return
        elif not callbacks.get("install_modpack") and not callbacks.get("install_mrpack"):
            self._notify(_("mp_install_error", error="install_callback_unavailable"), "error")
            return

        self._busy = True
        self._install_target = target
        self._install_state = "installing"
        self._progress = 0.0
        self._progress_text = _("mp_installing")
        self._mrpack_text = ""
        self._vanilla_text = ""
        self._result_text = ""
        self._result_level = ""
        self.installChanged.emit()
        self._set_status_key("mp_installing", {})

        #: 记录调用前的字典身份：核心开始安装时会整体替换它。只有看到新对象才投递，
        #: 否则会把上一次安装留下的 phase=done 当成这一次的进度。
        before = getattr(self._launcher, "_mp_progress", None)
        self._progress_stop = threading.Event()
        stop = self._progress_stop

        def poll_progress() -> None:
            last: Any = None
            while not stop.wait(PROGRESS_POLL_MS / 1000.0):
                if self._closing:
                    return
                current = getattr(self._launcher, "_mp_progress", None)
                if current is None or current is before:
                    continue
                try:
                    snapshot = self._service.progress_snapshot(current)
                except Exception as e:  # noqa: BLE001 - 进度只是观感，坏了不拖垮安装
                    logger.warning("整合包进度快照失败: %s", e)
                    continue
                if snapshot != last:
                    last = snapshot
                    self._post({"kind": "mp-progress", "snapshot": snapshot})

        threading.Thread(target=poll_progress, name="modpack-progress", daemon=True).start()

        def work() -> Any:
            try:
                if target == TARGET_SERVER:
                    return self._service.call_server_install_entry(
                        callbacks, path, optional, server_name or None
                    )
                return self._service.call_install_entry(
                    callbacks, path, optional, pack_format, instance_name=instance_name
                )
            finally:
                stop.set()

        self._submit(work, {"kind": "install", "target": target}, name=TASK_INSTALL)

    @Slot()
    def resetInstall(self) -> None:  # noqa: N802
        """清掉结果（重新选文件 / 重试前用；不动已选文件）。"""
        self._install_state = ""
        self._progress = 0.0
        self._progress_text = ""
        self._mrpack_text = ""
        self._vanilla_text = ""
        self._result_text = ""
        self._result_level = ""
        self.installChanged.emit()


    # ─── 内部：文件 / 元数据 ────────────────────────────────

    def _select_path(self, path: str) -> None:
        state = self._file()
        state.update({"path": str(path or ""), "state": STATE_IDLE, "error": "", "raw": {}, "info": {}, "seq": 0})
        self._selected[self._mode] = []
        self._reset_naming()
        self.fileChanged.emit()
        self.installChanged.emit()

    def _load_info(self, path: str) -> None:
        if self._service is None or self._tasks is None:
            self._file().update({"state": STATE_ERROR, "error": "service_unavailable"})
            self.fileChanged.emit()
            return
        state = self._file()
        seq = int(state["seq"] or 0) + 1
        state.update({"state": STATE_LOADING, "error": "", "seq": seq})
        self.fileChanged.emit()
        callbacks = self._callbacks or launcher_callbacks(self._launcher)
        if self._launcher is None:
            self._launcher = self._launcher_of(self._context)
            callbacks = launcher_callbacks(self._launcher)
        self._callbacks = callbacks
        mode = self._mode

        def work() -> Dict[str, Any]:
            info = self._service.load_pack_info(callbacks, path)
            return {"raw": info, "info": self._service.describe_pack_info(info)}

        self._submit(work, {"kind": "info", "seq": seq, "path": path, "mode": mode}, name=TASK_INFO)

    def _apply_info(self, payload: Dict[str, Any]) -> None:
        mode = str(payload.get("mode", self._mode))
        state = self._files.get(mode)
        if state is None:
            return
        if int(payload.get("seq") or -1) != int(state["seq"] or 0):
            return
        if payload.get("error"):
            state.update({"state": STATE_ERROR, "error": str(payload["error"]), "raw": {}, "info": {}})
            if mode == self._mode:
                self.fileChanged.emit()
                self.installChanged.emit()
            return
        result = payload.get("result") or {}
        state.update({"state": STATE_READY, "error": "", "raw": dict(result.get("raw") or {}), "info": dict(result.get("info") or {})})
        self._selected[mode] = []
        if mode == self._mode:
            self.fileChanged.emit()
            self.installChanged.emit()

    # ─── 内部：搜索 ─────────────────────────────────────────

    def _start_search(self, *, ai: bool, token: str = "", keep_page: bool = False) -> None:
        if self._service is None or self._tasks is None:
            self._search.update({"state": STATE_ERROR, "error": "service_unavailable"})
            self.searchChanged.emit()
            return
        state = self._search
        page = int(state["page"] or 1) if keep_page else 1
        seq = int(state["seq"] or 0) + 1
        state.update(
            {
                "state": STATE_LOADING,
                "error": "",
                "ai": bool(ai),
                "keywords": [],
                "page": page,
                "ai_hits": None,
                "seq": seq,
            }
        )
        self.searchChanged.emit()
        self._set_status_key("ai_search_optimizing" if ai else "mod_browser_searching", {})
        query = str(state["query"] or "")

        def work() -> Any:
            if ai:
                return self._service.search_ai_modpacks(query, token)
            return self._service.search_modpacks(query, (page - 1) * PAGE_SIZE, PAGE_SIZE)

        self._submit(
            work,
            {"kind": "search", "seq": seq, "ai": bool(ai), "query": query, "page": page},
            name=TASK_SEARCH,
        )

    def _apply_search(self, payload: Dict[str, Any]) -> None:
        state = self._search
        if int(payload.get("seq") or -1) != int(state["seq"] or 0):
            return
        state["ai"] = False
        if payload.get("error"):
            state.update({"state": STATE_ERROR, "error": str(payload["error"]), "hits": [], "ai_hits": None, "total": 0})
            self._set_status_key("mod_browser_search_failed_status", {"error": str(payload["error"])})
            self.searchChanged.emit()
            return
        result = payload.get("result")
        if bool(payload.get("ai")):
            hits = list(result[0]) if isinstance(result, (tuple, list)) and result else []
            keywords = list(result[1]) if isinstance(result, (tuple, list)) and len(result) > 1 else []
            total = len(hits)
            state.update({"hits": [], "ai_hits": hits, "page": 1})
        else:
            hits = list(getattr(result, "hits", []) or [])
            total = int(getattr(result, "total_hits", 0) or 0)
            keywords = []
            state.update({"hits": hits, "ai_hits": None, "page": int(payload.get("page") or 1)})
        state.update(
            {
                "total": total,
                "keywords": keywords,
                "state": STATE_EMPTY if not self._visible_hits() else STATE_READY,
            }
        )
        if bool(payload.get("ai")):
            kw_text = ", ".join(keywords) or str(payload.get("query") or "")
            self._set_status_key("ai_search_done", {"keywords": kw_text, "total": len(hits)})
        elif not hits:
            self._set_status_key("mp_browser_no_results_status", {})
        else:
            self._set_status_key("mp_browser_total_found", {"total": total})
        self.searchChanged.emit()

    # ─── 内部：版本列表 / 下载 ──────────────────────────────

    def _apply_versions(self, payload: Dict[str, Any]) -> None:
        state = self._versions
        if int(payload.get("seq") or -1) != int(state["seq"] or 0):
            return
        if payload.get("error"):
            state.update({"state": STATE_ERROR, "error": str(payload["error"]), "all": [], "raw": {}, "shown": 0})
            self.versionsChanged.emit()
            return
        versions = list(payload.get("result") or [])
        if not versions:
            state.update({"state": STATE_EMPTY, "error": "", "all": [], "raw": {}, "shown": 0, "total": 0})
            self._set_status_key("mp_browser_no_versions", {"title": str(state["title"] or "")})
            self.versionsChanged.emit()
            return
        unknown = _("mp_browser_unknown_version")
        if self._service is not None:
            all_sorted = list(self._service.build_sorted_version_list(versions, unknown))
        else:
            all_sorted = versions
        raw = {str(v.get("id", "")): v for v in versions if isinstance(v, dict) and v.get("id")}
        state.update(
            {
                "state": STATE_READY,
                "error": "",
                "all": all_sorted,
                "raw": raw,
                "shown": min(VERSION_PAGE_SIZE, len(all_sorted)),
                "total": len(versions),
            }
        )
        self._set_status_key("mp_browser_versions_found", {"count": len(versions)})
        self.versionsChanged.emit()

    def _apply_download(self, payload: Dict[str, Any]) -> None:
        self._downloading = False
        self.installChanged.emit()
        target = str(payload.get("target", self._target))
        if payload.get("error"):
            self._set_status_key("mp_browser_download_error", {"error": str(payload["error"])})
            self._notify(_("mp_browser_download_error", error=str(payload["error"])), "error")
            self.versionsChanged.emit()
            return
        result = payload.get("result")
        ok = bool(result[0]) if isinstance(result, (tuple, list)) and result else False
        detail = str(result[1]) if isinstance(result, (tuple, list)) and len(result) > 1 else ""
        if not ok:
            self._set_status_key("mp_browser_download_error", {"error": detail})
            self._notify(_("mp_browser_download_error", error=detail), "error")
            self.versionsChanged.emit()
            return
        path = detail
        self._pending_files[target] = path
        self._set_status_key("mp_browser_download_done", {"filename": os.path.basename(path)})
        self._notify(_("mp_browser_download_done", filename=os.path.basename(path)), "success")
        self.versionsChanged.emit()
        self.fileReady.emit(target, path)

    def _apply_download_status(self, payload: Dict[str, Any]) -> None:
        text = str(payload.get("text", "") or "")
        if text:
            self.statusMessage.emit(text, "info")

    # ─── 内部：安装结果 ─────────────────────────────────────

    def _apply_install(self, payload: Dict[str, Any]) -> None:
        target = str(payload.get("target", self._install_target))
        if payload.get("error"):
            self._finish_install(False, str(payload["error"]), target)
            return
        result = payload.get("result")
        ok = bool(result[0]) if isinstance(result, (tuple, list)) and result else False
        detail = str(result[1]) if isinstance(result, (tuple, list)) and len(result) > 1 else ""
        self._finish_install(ok, detail, target)

    def _finish_install(self, success: bool, detail: str, target: str) -> None:
        self._busy = False
        self._naming = False
        self._install_state = "done" if success else "failed"
        self._progress = 1.0 if success else self._progress
        self._progress_text = _("mp_install_done") if success else _("mp_install_failed_status")
        if success:
            if target == TARGET_SERVER:
                self._result_text = _("mp_server_install_done_msg", result=detail)
                notice = _("notify_modpack_server_installed")
            else:
                self._result_text = _("mp_install_done_msg", result=detail)
                notice = _("notify_modpack_installed")
            self._result_level = "success"
            if self._service is not None:
                self._service.trigger_achievement("server_modpack_server" if target == TARGET_SERVER else "modder_lazy")
            if target == TARGET_CLIENT and self._versions_bridge is not None:
                refresh = getattr(self._versions_bridge, "refresh", None)
                if callable(refresh):
                    try:
                        refresh()
                    except Exception as e:  # noqa: BLE001 - 刷新失败不影响安装结果
                        logger.warning("刷新版本列表失败: %s", e)
            self._notify(notice, "success")
            if target == TARGET_SERVER:
                self.serverInstalled.emit(str(detail or ""))
        else:
            if target == TARGET_SERVER:
                self._result_text = _("mp_server_install_error", error=detail)
            else:
                self._result_text = _("mp_install_error", error=detail)
            self._result_level = "error"
            self._notify(_("mp_install_failed_status"), "error")
        self.installChanged.emit()
        self.installFinished.emit(target, bool(success), detail)

    def _apply_progress(self, payload: Dict[str, Any]) -> None:
        snapshot = payload.get("snapshot") or {}
        target = self._install_target
        self._progress = float(snapshot.get("overall", 0.0) or 0.0)
        phase = str(snapshot.get("phase", "") or "")
        status = str(snapshot.get("status", "") or "")
        loader = str(snapshot.get("loader_label", "") or "")
        if phase == "detected":
            self._progress_text = _("mp_format_detected", name=str(snapshot.get("format_name", "") or "?"))
        elif phase == "loader":
            key = "mp_server_loader_status" if target == TARGET_SERVER else "mp_loader_status"
            self._progress_text = _(key, name=loader)
        else:
            self._progress_text = status or _("mp_installing")
        mp_pct = float(snapshot.get("mrpack_pct", 0.0) or 0.0)
        mp_label = str(snapshot.get("mrpack_label", "") or "")
        mc_pct = float(snapshot.get("vanilla_pct", 0.0) or 0.0)
        mc_label = str(snapshot.get("vanilla_label", "") or "")
        self._mrpack_text = (
            _("mp_prog_mrpack_label", pct=f"{mp_pct:.0f}", label=mp_label) if mp_pct > 0 or mp_label else ""
        )
        self._vanilla_text = (
            _("mp_prog_vanilla_label", pct=f"{mc_pct:.0f}", label=mc_label) if mc_pct > 0 or mc_label else ""
        )
        self.installChanged.emit()


    # ─── 内部：任务提交 / 投递 ──────────────────────────────

    def _submit(self, work: Any, payload: Dict[str, Any], *, name: str) -> Any:
        if self._tasks is None:
            self._post({**payload, "result": None, "error": "tasks_unavailable"})
            return None

        def on_done(result: Any) -> None:
            self._post({**payload, "result": result, "error": None})

        def on_error(error: Any) -> None:
            text = str(error)
            logger.warning("整合包任务 %s 失败: %s", name, text)
            self._post({**payload, "result": None, "error": text})

        try:
            handle = self._tasks.submit(work, name=name, on_done=on_done, on_error=on_error)
        except Exception as e:  # noqa: BLE001 - 提交失败按任务失败处理
            logger.error("提交整合包任务 %s 失败: %s", name, e)
            self._post({**payload, "result": None, "error": str(e)})
            return None
        self._handles[name] = handle
        return handle

    def _post(self, item: Dict[str, Any]) -> None:
        if self._closing:
            return
        with self._pending_lock:
            self._pending.append(item)
        self.posted.emit()

    def _deliver(self) -> None:
        """主线程：把队列里的投递逐条落到属性上。"""
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
                elif kind == "info":
                    self._apply_info(item)
                elif kind == "download":
                    self._apply_download(item)
                elif kind == "download-status":
                    self._apply_download_status(item)
                elif kind == "mp-progress":
                    self._apply_progress(item)
                elif kind == "install":
                    self._apply_install(item)
            except Exception as e:  # noqa: BLE001 - 一条失败不拖垮后面的
                logger.error("处理整合包投递失败 (%s): %s", kind, e)

    # ─── 内部：展示数据 ─────────────────────────────────────

    @staticmethod
    def _decorate_hit(hit: Dict[str, Any]) -> Dict[str, Any]:
        downloads = hit.get("downloads", 0)
        try:
            downloads_text = format_downloads(int(downloads or 0))
        except (TypeError, ValueError):
            downloads_text = "0"
        return {
            "project_id": str(hit.get("project_id", "") or ""),
            "title": str(hit.get("title", "") or _("mp_browser_unknown_modpack")),
            "description": str(hit.get("description", "") or ""),
            "icon_url": str(hit.get("icon_url", "") or ""),
            "downloads_text": downloads_text,
            "versions_text": _compress_versions(list(hit.get("versions") or [])),
        }

    def _decorate_version_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        header = row.get("_header")
        if header is not None:
            count = int(row.get("_count", 0) or 0)
            return {
                "kind": "header",
                "header": str(header),
                "count": count,
                "countText": _("mp_browser_version_count", count=count),
            }
        version_number = str(row.get("version_number", "") or _("mp_browser_unknown_version"))
        return {
            "kind": "version",
            "id": str(row.get("id", "") or ""),
            "version_number": version_number,
            "date": str(row.get("date_published", "") or "")[:10],
            "game_versions": str(", ".join(str(v) for v in (row.get("game_versions") or []))),
            "downloadable": bool(row.get("files")),
        }

    # ─── 内部：文案 / token ─────────────────────────────────

    def _ai_token(self) -> Optional[str]:
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

    def _set_status_key(self, key: str, params: Dict[str, Any]) -> None:
        try:
            text = _(key, **params) if params else _(key)
        except Exception as e:  # noqa: BLE001 - 文案坏了不该影响功能
            logger.warning("翻译状态文案失败 (%s): %s", key, e)
            text = key
        self.statusMessage.emit(text, "info")

    def _notify(self, text: str, level: str) -> None:
        self.noticeRequested.emit(str(text), str(level))


def _compress_versions(games: List[str]) -> str:
    if not games:
        return ""
    try:
        from modrinth import compress_game_versions

        return str(compress_game_versions(list(games)) or "")
    except Exception:  # noqa: BLE001 - 纯展示，失败不该抛
        return ", ".join(str(g) for g in games)


def install(engine: Any, name: str = QML_NAME) -> Any:
    """同形入口（与 3.6/3.7 的桥保持一致）；上下文属性由 main_qml 统一注册。"""
    return (getattr(engine, "_fmcl_bridges", None) or {}).get(name)


__all__ = ["ModpackBridge", "QML_NAME", "install"]

