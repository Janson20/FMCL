"""服务端模组在线浏览桥 —— QML 侧的 `ServerModBrowser`（阶段 3 任务 3.12 / L-51）。

链路：

    qml/pages/servers/ServerModBrowserPage.qml
        ↓ 取属性 / 调槽
    app/bridges/server_mod_browser_bridge.py（本模块，零业务规则）
        ↓ 转发
    services/mod_browser_service.py（search_server_page / search_server_ai / server_install_mod）
        ↓ 委托
    modrinth.py（单源 Modrinth，服务端专用搜索与依赖递归安装）

旧窗口 ``ui/windows/server_mod_browser.py`` 没有请求世代守卫（D-93 只加在客户端
浏览窗口上），连打两次搜索时晚返回的旧请求会覆盖新结果。本桥补一个只增不减的
`_seq`：晚到的结果整批丢弃 —— 这是新界面相对旧窗口的**修正**，对照表
L-51 记为本次修正。

线程约定：搜索 / AI / 安装全部走 TaskRunner；worker 只 `_post` 排队，
`_deliver` 在主线程落属性。
"""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from services.browse_common import SERVER_SEARCH_PAGE_SIZE, format_downloads, loader_tags_text
from services.i18n_service import _
from services.mod_browser_service import ModBrowserService, compat_loader
from services.server_mods_session import detect_server_game_version, detect_server_loader

logger = logging.getLogger("app.bridges.server_mod_browser_bridge")

QML_NAME = "ServerModBrowser"

TASK_SEARCH = "server_mods.search"
TASK_AI = "server_mods.ai_search"
TASK_INSTALL = "server_mods.online_install"

PAGE_SIZE = SERVER_SEARCH_PAGE_SIZE

STATE_IDLE = "idle"
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_EMPTY = "empty"
STATE_ERROR = "error"


def _compress_versions(games: Any) -> str:
    """游戏版本列表 → 紧凑显示串（与 3.7 浏览页同一口径的简化版）。"""
    values = [str(v) for v in (games or []) if str(v)]
    if not values:
        return ""
    if len(values) <= 3:
        return ", ".join(values)
    return f"{values[0]}, {values[1]} … +{len(values) - 2}"


class ServerModBrowserBridge(QObject):
    """上下文属性 `ServerModBrowser`。"""

    itemsChanged = Signal()
    busyChanged = Signal()
    statusMessage = Signal(str, str)
    noticeRequested = Signal(str, str)
    installFinished = Signal(str, bool, str)
    posted = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._service: Optional[ModBrowserService] = None
        self._launcher: Any = None
        self._tasks: Any = None
        self._engine: Any = None
        self._callbacks: Dict[str, Any] = {}

        self._version = ""
        self._server_dir = ""
        self._game_version = ""
        self._loader = ""
        self._query = ""
        self._items: List[Dict[str, Any]] = []
        self._total = 0
        self._offset = 0
        self._state = STATE_IDLE
        self._error = ""
        self._searching = False
        self._ai_busy = False
        self._ai_keywords: List[str] = []
        self._ai_hits: Optional[List[Dict[str, Any]]] = None
        self._installing = False
        self._progress_text = ""
        self._handles: Dict[str, Any] = {}
        self._seq = 0
        self._pending: List[Dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        self._closing = False
        self.posted.connect(self._deliver)

    # ─── 装配 ───────────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        self._context = context
        self._service = self._service_of(context, "mod_browser")
        self._launcher = self._service_of(context, "launcher")
        self._tasks = getattr(context, "tasks", None) if context is not None else None
        self._refresh_callbacks()
        if self._service is None:
            logger.warning("模组浏览服务不可用：服务端在线浏览页只会显示错误态")

    def _refresh_callbacks(self) -> None:
        if self._launcher is None:
            return
        for name in ("get_jdz_token", "get_server_dir"):
            fn = getattr(self._launcher, name, None)
            if callable(fn):
                self._callbacks[name] = fn

    @staticmethod
    def _service_of(context: Any, name: str) -> Any:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("服务端浏览桥取服务 %s 失败: %s", name, e)
            return None

    def use_engine(self, engine: Any) -> None:
        self._engine = engine

    def detach(self) -> None:
        self._closing = True
        for handle in list(self._handles.values()):
            try:
                handle.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._handles.clear()

    # ─── 属性 ───────────────────────────────────────────────

    @Property(bool, constant=True)
    def available(self) -> bool:
        return self._service is not None

    @Property(str, notify=itemsChanged)
    def versionId(self) -> str:  # noqa: N802
        return self._version

    @Property(str, notify=itemsChanged)
    def gameVersion(self) -> str:  # noqa: N802
        return self._game_version

    @Property(str, notify=itemsChanged)
    def loader(self) -> str:
        return self._loader

    @Property(bool, notify=itemsChanged)
    def canInstall(self) -> bool:  # noqa: N802
        return bool(self._game_version and self._loader)

    @Property(str, notify=itemsChanged)
    def query(self) -> str:
        return self._query

    @Property("QVariantList", notify=itemsChanged)
    def items(self) -> List[Dict[str, Any]]:
        return self._items

    @Property(int, notify=itemsChanged)
    def page(self) -> int:
        return self._offset // PAGE_SIZE + 1

    @Property(int, notify=itemsChanged)
    def pageCount(self) -> int:  # noqa: N802
        return max(1, math.ceil(self._total / PAGE_SIZE))

    @Property(int, notify=itemsChanged)
    def total(self) -> int:
        return self._total

    @Property(int, constant=True)
    def pageSize(self) -> int:  # noqa: N802
        return PAGE_SIZE

    @Property(int, notify=itemsChanged)
    def rangeStart(self) -> int:  # noqa: N802
        return self._offset + 1 if self._items else 0

    @Property(int, notify=itemsChanged)
    def rangeEnd(self) -> int:  # noqa: N802
        return self._offset + len(self._items) if self._items else 0

    @Property(str, notify=itemsChanged)
    def loadState(self) -> str:  # noqa: N802
        return self._state

    @Property(str, notify=itemsChanged)
    def loadError(self) -> str:  # noqa: N802
        return self._error

    @Property(bool, notify=busyChanged)
    def searching(self) -> bool:
        return self._searching

    @Property(bool, notify=busyChanged)
    def aiBusy(self) -> bool:  # noqa: N802
        return self._ai_busy

    @Property(bool, notify=busyChanged)
    def installing(self) -> bool:
        return self._installing

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._searching or self._ai_busy or self._installing

    @Property(bool, notify=busyChanged)
    def aiAvailable(self) -> bool:  # noqa: N802
        fn = self._callbacks.get("get_jdz_token")
        if not callable(fn):
            return False
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001
            return False

    @Property("QVariantList", notify=itemsChanged)
    def aiKeywords(self) -> List[str]:  # noqa: N802
        return self._ai_keywords

    @Property(str, notify=busyChanged)
    def progressText(self) -> str:  # noqa: N802
        return self._progress_text

    @Property(bool, notify=busyChanged)
    def progressVisible(self) -> bool:  # noqa: N802
        return bool(self._progress_text)

    # ─── 动作 ───────────────────────────────────────────────

    @Slot(str)
    def activate(self, version: str) -> None:
        if self._context is not None:
            launcher = self._service_of(self._context, "launcher")
            if launcher is not None and launcher is not self._launcher:
                self._launcher = launcher
                self._refresh_callbacks()
        wanted = str(version or "").strip()
        if wanted == self._version and self._state != STATE_IDLE:
            return
        self._version = wanted
        self._server_dir = self._resolve_server_dir(wanted)
        self._game_version = detect_server_game_version(wanted)
        self._loader = detect_server_loader(wanted, self._server_dir)
        self._query = ""
        self._items = []
        self._total = 0
        self._offset = 0
        self._state = STATE_IDLE
        self._error = ""
        self._ai_keywords = []
        self._ai_hits = None
        self.itemsChanged.emit()

    @Slot(str)
    def search(self, query: str) -> None:
        if self._installing or self._ai_busy:
            return
        self._query = str(query or "")
        self._offset = 0
        self._ai_hits = None
        self._ai_keywords = []
        self._searching = True
        self._state = STATE_LOADING
        self._error = ""
        self.busyChanged.emit()
        self.itemsChanged.emit()
        self._start_search()

    @Slot()
    def aiSearch(self) -> None:  # noqa: N802
        if self._ai_busy or self._installing or self._searching or self._tasks is None:
            return
        fn = self._callbacks.get("get_jdz_token")
        token = ""
        if callable(fn):
            try:
                token = str(fn() or "")
            except Exception:  # noqa: BLE001
                token = ""
        if self._service is None:
            return
        if not token:
            self._status("ai_search_login_required", {}, "warning")
            return
        query = self._query
        if not query.strip():
            return
        self._ai_busy = True
        self._ai_hits = None
        self._state = STATE_LOADING
        self._error = ""
        self.busyChanged.emit()
        self.itemsChanged.emit()
        self._set_status("ai_search_optimizing")
        seq = self._next_seq()

        def work() -> Any:
            return self._service.search_server_ai(query, token, self._game_version or None, self._compat_loader())

        self._handles[TASK_AI] = self._tasks.submit(
            work,
            name=TASK_AI,
            on_done=lambda result: self._post({"kind": "ai-done", "seq": seq, "result": result}),
            on_error=lambda err: self._post({"kind": "search-error", "seq": seq, "error": str(err)}),
        )

    @Slot(int)
    def goto(self, page: int) -> None:
        if self._installing or self._ai_busy:
            return
        wanted = max(1, min(int(page or 1), self.pageCount))
        self._offset = (wanted - 1) * PAGE_SIZE
        if self._ai_hits is not None:
            self._items = [self._decorate(hit) for hit in self._ai_hits[self._offset : self._offset + PAGE_SIZE]]
            self.itemsChanged.emit()
            return
        self._searching = True
        self._state = STATE_LOADING
        self.busyChanged.emit()
        self.itemsChanged.emit()
        self._start_search()

    @Slot(str, str)
    def install(self, projectId: str, title: str) -> None:  # noqa: N802
        if self._installing:
            return
        project = str(projectId or "")
        name = str(title or "")
        if not project or self._service is None:
            return
        if not self._game_version or not self._loader:
            self._status("mod_browser_unknown_loader", {}, "warning")
            return
        if not self._server_dir:
            self._status("mod_browser_need_version_install", {}, "warning")
            return
        if self._tasks is None:
            self._status("mod_browser_install_error", {"error": "tasks_unavailable"}, "error")
            return
        mods_dir = Path(self._server_dir) / "mods"
        try:
            mods_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self._status("mod_browser_install_error", {"error": str(e)}, "error")
            return
        self._installing = True
        self._progress_text = _("mod_browser_fetching_version", title=name)
        self.busyChanged.emit()
        self._set_status("mod_browser_fetching_version", {"title": name})
        seq = self._next_seq()

        def work(ctx: Any) -> Any:
            return self._service.server_install_mod(
                project,
                self._game_version,
                self._compat_loader(),
                str(mods_dir),
                status_callback=lambda msg: ctx.status(str(msg)),
            )

        self._handles[TASK_INSTALL] = self._tasks.submit(
            work,
            name=TASK_INSTALL,
            on_status=self._install_status,
            on_done=lambda result: self._post({"kind": "install-done", "seq": seq, "title": name, "result": result}),
            on_error=lambda err: self._post({"kind": "install-error", "seq": seq, "error": str(err)}),
            pass_context=True,
        )

    @Slot()
    def refresh(self) -> None:
        if self._query or self._state != STATE_IDLE:
            self._searching = True
            self._state = STATE_LOADING
            self.busyChanged.emit()
            self.itemsChanged.emit()
            self._start_search()

    # ─── 内部：目标解析 ─────────────────────────────────────

    def _resolve_server_dir(self, version: str) -> str:
        fn = self._callbacks.get("get_server_dir")
        if not callable(fn) or not version:
            return ""
        try:
            return str(Path(str(fn())) / version)
        except Exception as e:  # noqa: BLE001
            logger.warning("解析服务器目录失败: %s", e)
            return ""

    def _compat_loader(self) -> Optional[str]:
        return compat_loader(self._loader)

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    # ─── 内部：搜索 ─────────────────────────────────────────

    def _start_search(self) -> None:
        if self._service is None or self._tasks is None:
            self._searching = False
            self._state = STATE_ERROR
            self._error = "service_unavailable"
            self.busyChanged.emit()
            self.itemsChanged.emit()
            return
        seq = self._next_seq()
        self._set_status("mod_browser_searching")
        query = self._query
        game_version = self._game_version or None
        loader = self._compat_loader()
        offset = self._offset

        def work() -> Any:
            return self._service.search_server_page(query, game_version, loader, offset, PAGE_SIZE)

        self._handles[TASK_SEARCH] = self._tasks.submit(
            work,
            name=TASK_SEARCH,
            on_done=lambda result: self._post({"kind": "search-done", "seq": seq, "offset": offset, "result": result}),
            on_error=lambda err: self._post({"kind": "search-error", "seq": seq, "error": str(err)}),
        )

    def _install_status(self, *args: Any, **kwargs: Any) -> None:
        message = str(kwargs.get("message", "") or "")
        if message:
            self._post({"kind": "install-status", "text": message})

    # ─── 内部：装饰 ─────────────────────────────────────────

    def _decorate(self, hit: Dict[str, Any]) -> Dict[str, Any]:
        source = str(hit.get("source", "modrinth") or "modrinth")
        downloads = 0
        try:
            downloads = int(hit.get("downloads", 0) or 0)
        except (TypeError, ValueError):
            downloads = 0
        categories = list(hit.get("categories", []) or [])
        loaders = list(hit.get("loaders", []) or [])
        if not loaders:
            loaders = [c for c in categories if c in ("forge", "fabric", "neoforge", "quilt")]
        games = list(hit.get("game_versions", []) or hit.get("versions", []) or [])
        return {
            "project_id": str(hit.get("project_id", "") or ""),
            "source": source,
            "source_name": "Modrinth" if source == "modrinth" else "CurseForge",
            "title": str(hit.get("title", "") or _("mod_browser_unknown")),
            "description": str(hit.get("description", "") or ""),
            "downloads": downloads,
            "downloads_text": format_downloads(downloads),
            "icon_url": str(hit.get("icon_url", "") or ""),
            "loaders_text": loader_tags_text(loaders),
            "versions_text": _compress_versions(games),
            "type_icon": "mods",
        }

    # ─── 内部：投递 / 状态 ─────────────────────────────────

    def _post(self, item: Dict[str, Any]) -> None:
        if self._closing:
            return
        with self._pending_lock:
            self._pending.append(item)
        self.posted.emit()

    def _deliver(self) -> None:
        with self._pending_lock:
            batch = self._pending[:]
            self._pending.clear()
        for item in batch:
            kind = item.get("kind")
            try:
                if kind == "search-done":
                    self._apply_search_done(item)
                elif kind == "search-error":
                    self._apply_search_error(item)
                elif kind == "ai-done":
                    self._apply_ai_done(item)
                elif kind == "install-status":
                    self._apply_install_status(item)
                elif kind == "install-done":
                    self._apply_install_done(item)
                elif kind == "install-error":
                    self._apply_install_error(item)
            except Exception as e:  # noqa: BLE001
                logger.error("处理服务端模组浏览投递失败 (%s): %s", kind, e)

    def _is_current(self, seq: Any) -> bool:
        return int(seq or -1) == int(self._seq)

    def _apply_search_done(self, item: Dict[str, Any]) -> None:
        if not self._is_current(item.get("seq")):
            return
        outcome = item.get("result")
        hits = list(getattr(outcome, "hits", []) or [])
        total = int(getattr(outcome, "total_hits", 0) or 0)
        self._searching = False
        self._items = [self._decorate(hit) for hit in hits]
        self._total = total
        self._state = STATE_READY if hits else STATE_EMPTY
        self._error = ""
        self.busyChanged.emit()
        self.itemsChanged.emit()
        if self._query:
            self._set_status(
                "mod_browser_result_range", {"start": self._offset + 1, "end": self._offset + len(hits), "total": total}
            )
        else:
            self._set_status("mod_browser_total_found", {"total": total})

    def _apply_search_error(self, item: Dict[str, Any]) -> None:
        if not self._is_current(item.get("seq")):
            return
        self._searching = False
        self._ai_busy = False
        self._state = STATE_ERROR
        self._error = str(item.get("error", "") or "")
        self._items = []
        self.busyChanged.emit()
        self.itemsChanged.emit()
        self._set_status("mod_browser_search_failed_status", {"error": self._error}, "error")

    def _apply_ai_done(self, item: Dict[str, Any]) -> None:
        if not self._is_current(item.get("seq")):
            return
        merged, keywords = item.get("result") or ([], [])
        self._ai_busy = False
        self._ai_keywords = [str(k) for k in (keywords or [])]
        self.busyChanged.emit()
        if not self._ai_keywords:
            self._items = []
            self._total = 0
            self._state = STATE_EMPTY
            self.itemsChanged.emit()
            self._set_status("mod_browser_no_results", {}, "warning")
            return
        self._ai_hits = list(merged or [])
        self._total = len(self._ai_hits)
        self._offset = 0
        self._items = [self._decorate(hit) for hit in self._ai_hits[:PAGE_SIZE]]
        self._state = STATE_READY if self._items else STATE_EMPTY
        self.itemsChanged.emit()
        self._set_status("ai_search_done", {"keywords": ", ".join(self._ai_keywords), "total": self._total}, "success")

    def _apply_install_status(self, item: Dict[str, Any]) -> None:
        self._progress_text = str(item.get("text", "") or "")
        self.busyChanged.emit()

    def _apply_install_done(self, item: Dict[str, Any]) -> None:
        if not self._is_current(item.get("seq")):
            return
        result = item.get("result")
        success = bool(result[0]) if isinstance(result, (tuple, list)) and result else False
        detail = str(result[1]) if isinstance(result, (tuple, list)) and len(result) > 1 else ""
        names = list(result[2]) if isinstance(result, (tuple, list)) and len(result) > 2 else []
        title = str(item.get("title", "") or "")
        self._installing = False
        self._progress_text = ""
        self.busyChanged.emit()
        if success:
            if len(names) > 1:
                deps = ", ".join(str(n) for n in names[:-1])
                self._status("mod_browser_install_success_deps", {"title": title, "deps": deps}, "success")
            else:
                self._status("mod_browser_install_success", {"title": title}, "success")
            self.noticeRequested.emit(_("mod_browser_install_success", title=title), "success")
        else:
            self._status("mod_browser_install_failed", {"error": detail}, "error")
            self.noticeRequested.emit(_("mod_browser_install_failed", error=detail), "error")
        self.installFinished.emit(title, success, detail)

    def _apply_install_error(self, item: Dict[str, Any]) -> None:
        if not self._is_current(item.get("seq")):
            return
        error = str(item.get("error", "") or "")
        self._installing = False
        self._progress_text = ""
        self.busyChanged.emit()
        self._status("mod_browser_install_error", {"error": error}, "error")
        self.noticeRequested.emit(_("mod_browser_install_error", error=error), "error")

    def _set_status(self, key: str, params: Optional[Dict[str, Any]] = None, level: str = "info") -> None:
        try:
            text = _(key, **(params or {}))
        except Exception:  # noqa: BLE001
            text = key
        self.statusMessage.emit(text, level)

    def _status(self, key: str, params: Optional[Dict[str, Any]] = None, level: str = "info") -> None:
        self._set_status(key, params, level)
