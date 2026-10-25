"""服务器模组 / 资源包本地管理桥 —— QML 侧的 `ServerMods`（阶段 3 任务 3.12）。

链路：

    qml/pages/servers/ServerModsPage.qml（模组路由 / 资源包路由共用）
        ↓ 取属性 / 调槽
    app/bridges/server_mods_bridge.py（本模块，零业务规则）
        ↓ 转发
    services/server_mods_session.py（目录 / 搜索 / 分页 / 元数据 / 更新）
        ↓ 委托
    services/resource_service.py（扫描 / 启停 / 删除 / 导出 / 筛选 / 更新检查）

线程约定与 ResourcesBridge 相同：长活全部走 TaskRunner，worker 只 `_post` 排队，
真正改状态的是主线程上的 `_deliver()`。缩略图通道由 Resources 在装配期注册，
本桥通过 `thumbnail_provider.installed(engine)` 复用同一个 provider。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from services.i18n_service import _
from services.server_mods_session import (
    KIND_MODS,
    KIND_ORDER,
    STATE_ERROR,
    STATE_IDLE,
    STATE_LOADING,
    ServerModsSession,
    disable_server_mods,
    server_mods_dir_for,
)
from services.server_session import launcher_callbacks

logger = logging.getLogger("app.bridges.server_mods_bridge")

QML_NAME = "ServerMods"

TASK_META = "server_mods.metadata"
TASK_FILTER = "server_mods.filter"
TASK_CHECK = "server_mods.check_updates"
TASK_BATCH = "server_mods.batch_update"
TASK_INSTALL = "server_mods.install"


class ServerModsBridge(QObject):
    """上下文属性 `ServerMods`。"""

    viewChanged = Signal()
    busyChanged = Signal()
    progressChanged = Signal()
    updatesChanged = Signal()
    statusMessage = Signal(str, str)
    noticeRequested = Signal(str, str)
    addFilesRequested = Signal()
    deleteRequested = Signal(str, str)
    #: 服务端筛选后仍有待人工确认的模组（条目列表，数量）。页面据此弹窗。
    unknownModsReview = Signal("QVariantList", int)
    #: 内部投递信号：worker 只排队并 emit 它。
    posted = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._service: Any = None
        self._launcher: Any = None
        self._tasks: Any = None
        self._engine: Any = None
        self._session: Optional[ServerModsSession] = None

        self._version = ""
        self._kind = KIND_MODS
        self._rows: List[Dict[str, Any]] = []
        self._page = 1
        self._page_count = 1
        self._total = 0
        self._state = STATE_IDLE
        self._error = ""
        self._search = ""
        self._busy = False
        self._progress: Dict[str, Any] = {}
        self._updates: List[Dict[str, Any]] = []
        self._checking = False

        self._handles: Dict[str, Any] = {}
        self._thumb_urls: Dict[str, str] = {}
        self._pending: List[Dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        self._closing = False
        self.posted.connect(self._deliver)

    # ─── 装配 ───────────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        self._context = context
        self._rebuild_session()

    def _rebuild_session(self) -> None:
        """取服务并造会话；启动链条尚未注入 launcher 时先记下，等一下再试。

        ``bind()`` 发生在启动链条之前（3.9 的 ``Startup.chainFinished`` 要晚得多），
        那时 ``launcher`` 往往还不在上下文里 —— 如果只在这里取一次，``get_server_dir``
        永远缺席，页面表现为“服务器目录解析失败”。所以 ``activate()`` 每次都会
        重新对齐一次 launcher。
        """
        self._service = self._service_of(self._context, "resource")
        self._launcher = self._service_of(self._context, "launcher")
        self._tasks = getattr(self._context, "tasks", None) if self._context is not None else None
        if self._service is None:
            logger.warning("资源服务不可用：服务器模组页只会显示错误态")
            self._session = None
            return
        self._session = ServerModsSession(
            self._service, callbacks=launcher_callbacks(self._launcher), tasks=self._tasks
        )

    @staticmethod
    def _service_of(context: Any, name: str) -> Any:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("服务器模组桥取服务 %s 失败: %s", name, e)
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

    # ─── 基础属性 ───────────────────────────────────────────

    @Property(bool, constant=True)
    def available(self) -> bool:
        return self._session is not None

    @Property(str, notify=viewChanged)
    def versionId(self) -> str:  # noqa: N802
        return self._version

    @Property(str, notify=viewChanged)
    def kind(self) -> str:
        return self._kind

    @Property(str, notify=viewChanged)
    def kindLabelKey(self) -> str:  # noqa: N802
        return self._session.kind_label_key if self._session is not None else "server_mods_title"

    @Property(str, notify=viewChanged)
    def kindDescKey(self) -> str:  # noqa: N802
        return self._session.kind_desc_key if self._session is not None else ""

    @Property(str, notify=viewChanged)
    def searchPlaceholderKey(self) -> str:  # noqa: N802
        return self._session.search_placeholder_key if self._session is not None else "rm_search_placeholder_mods"

    @Property(str, notify=viewChanged)
    def kindIcon(self) -> str:  # noqa: N802
        return self._session.kind_icon if self._session is not None else "mods"

    @Property(bool, notify=viewChanged)
    def isMods(self) -> bool:  # noqa: N802
        return self._kind == KIND_MODS

    @Property("QVariantList", notify=viewChanged)
    def rows(self) -> List[Dict[str, Any]]:
        return self._rows

    @Property(int, notify=viewChanged)
    def page(self) -> int:
        return self._page

    @Property(int, notify=viewChanged)
    def pageCount(self) -> int:  # noqa: N802
        return self._page_count

    @Property(int, notify=viewChanged)
    def total(self) -> int:
        return self._total

    @Property(str, notify=viewChanged)
    def loadState(self) -> str:  # noqa: N802
        return self._state

    @Property(str, notify=viewChanged)
    def loadError(self) -> str:  # noqa: N802
        return self._error

    @Property(str, notify=viewChanged)
    def search(self) -> str:
        return self._search

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=busyChanged)
    def checking(self) -> bool:
        return self._checking

    @Property(bool, notify=progressChanged)
    def progressVisible(self) -> bool:  # noqa: N802
        return bool(self._progress)

    @Property("QVariantMap", notify=progressChanged)
    def progress(self) -> Dict[str, Any]:
        return self._progress

    @Property("QVariantList", notify=updatesChanged)
    def updates(self) -> List[Dict[str, Any]]:
        return self._updates

    @Property(int, notify=updatesChanged)
    def updatesCount(self) -> int:  # noqa: N802
        return len(self._updates)

    # ─── 动作 ───────────────────────────────────────────────

    @Slot(str, str)
    def activate(self, version: str, kind: str) -> None:
        if self._context is not None:
            launcher = self._service_of(self._context, "launcher")
            if launcher is not self._launcher:
                self._rebuild_session()
        wanted_kind = str(kind or KIND_MODS)
        if wanted_kind not in KIND_ORDER:
            wanted_kind = KIND_MODS
        if self._session is None:
            self._apply_session()
            self._publish()
            return
        changed = self._session.activate(str(version or ""), wanted_kind)
        self._apply_session()
        if changed or self._state == STATE_IDLE:
            self._load()
        else:
            self._publish()

    @Slot()
    def refresh(self) -> None:
        self._load()

    @Slot(str)
    def setSearch(self, text: str) -> None:  # noqa: N802
        if self._session is None:
            return
        self._session.set_search(text)
        self._apply_session()
        self._publish()

    @Slot(int)
    def goto(self, page: int) -> None:
        if self._session is None:
            return
        self._session.goto(page)
        self._apply_session()
        self._publish()

    @Slot()
    def requestAddFiles(self) -> None:  # noqa: N802
        self.addFilesRequested.emit()

    @Slot("QVariantList", bool)
    def takeFiles(self, urls: Any, fromDrop: bool = False) -> None:  # noqa: N802
        paths = []
        for url in urls or []:
            try:
                path = url.toLocalFile() if hasattr(url, "toLocalFile") else str(url)
            except Exception:  # noqa: BLE001
                path = ""
            if path:
                paths.append(path)
        if not paths or self._session is None or self._tasks is None:
            self._set_status("rm_no_valid_files")
            return
        if self._busy:
            return
        self._busy = True
        self._progress = {"message": _("rm_install_count", count=0), "current": 0, "total": 0}
        self.busyChanged.emit()
        self.progressChanged.emit()
        session = self._session
        directory = session.directory()
        kind = session.kind

        def work() -> int:
            return session.install_files(paths, directory, kind)

        def done(count: Any) -> None:
            self._post({"kind": "install-done", "count": int(count or 0)})

        def failed(error: Any) -> None:
            self._post({"kind": "install-error", "error": str(error)})

        self._submit(work, TASK_INSTALL, done, failed)

    @Slot(str, bool)
    def toggle(self, path: str, disabled: bool) -> None:
        if self._session is None or not self._session.is_mods:
            return
        try:
            now_disabled = self._session.toggle_mod(str(path or ""), bool(disabled))
        except Exception as e:  # noqa: BLE001
            self._status("rm_operation_failed", {"error": str(e)}, "error")
            return
        self._status("mod_disabled_ok" if now_disabled else "mod_enabled_ok")
        self.refresh()

    @Slot(str, "QVariantList", result="QVariantMap")
    def disableMods(self, version: str, names: Any) -> Dict[str, Any]:  # noqa: N802
        """批量禁用待确认模组；供整合包弹窗与本地弹窗共用。

        只接受文件名，路径校验在 :func:`disable_server_mods` 里做。返回
        ``{"disabled": N, "failed": [文件名]}``，页面据此提示；不抛异常。
        """
        if self._service is None:
            return {"disabled": 0, "failed": [], "error": "service_unavailable"}
        #: 这个槽可能从整合包页调用，而本地页还没打开过 —— 启动链条完成后
        #: launcher 才在上下文里，这里懒取一次，不能只信 bind() 期缓存。
        if self._context is not None:
            launcher = self._service_of(self._context, "launcher")
            if launcher is not None:
                self._launcher = launcher
        callbacks = launcher_callbacks(self._launcher)
        try:
            directory = server_mods_dir_for(callbacks, version)
        except Exception as e:  # noqa: BLE001
            self._status("rm_operation_failed", {"error": str(e)}, "error")
            return {"disabled": 0, "failed": [], "error": str(e)}
        disabled, failed = disable_server_mods(self._service, directory, names)
        if self._version == str(version or "") and self._kind == KIND_MODS:
            self._load()
        if disabled > 0:
            self.noticeRequested.emit(_("mod_disabled_ok"), "success")
        if failed:
            self.noticeRequested.emit(_("rm_operation_failed", error=", ".join(failed[:5])), "error")
        return {"disabled": disabled, "failed": failed}

    @Slot(str, str)
    def requestDelete(self, path: str, name: str) -> None:  # noqa: N802
        self.deleteRequested.emit(str(path or ""), str(name or ""))

    @Slot(str)
    def deletePath(self, path: str) -> None:  # noqa: N802
        if self._session is None:
            return
        try:
            self._session.delete(str(path or ""))
        except Exception as e:  # noqa: BLE001
            self._status("rm_delete_failed", {"error": str(e)}, "error")
            return
        self._status("rm_deleted", {"name": str(path or "")})
        self.refresh()

    @Slot()
    def openFolder(self) -> None:  # noqa: N802
        if self._session is None:
            return
        try:
            self._session.open_folder()
        except Exception as e:  # noqa: BLE001
            self._status("rm_open_folder_failed", {"error": str(e)}, "error")

    @Slot()
    def exportList(self) -> None:  # noqa: N802
        if self._session is None or not self._session.is_mods:
            return
        text = self._session.export_text(
            _("mod_export_header", version=self._version), _("mod_enabled"), _("mod_disabled")
        )
        if not text or self._total <= 0:
            self._status("mod_export_no_mods", {}, "warning")
            return
        dialogs = self._bridge("Dialogs")
        ok = False
        if dialogs is not None:
            try:
                ok = bool(dialogs.set_clipboard(text))
            except Exception as e:  # noqa: BLE001
                logger.warning("写剪贴板失败: %s", e)
        if ok:
            self._status("mod_export_copied", {"count": self._total}, "success")
        else:
            self._status("rm_operation_failed", {"error": "clipboard"}, "error")

    # ─── 内部：加载 / 扫描 ──────────────────────────────────

    def _apply_session(self) -> None:
        if self._session is None:
            return
        self._version = self._session.version_id
        self._kind = self._session.kind
        self._search = self._session.search
        self._page = self._session.page
        self._page_count = self._session.page_count
        self._total = self._session.total
        self._state = self._session.state
        self._error = self._session.error
        self._rows = [self._decorate(row) for row in self._session.items]

    def _load(self) -> None:
        if self._session is None:
            return
        if not self._session.version_id:
            self._state = STATE_ERROR
            self._error = "server_not_selected"
            self._rows = []
            self._publish()
            return
        result = self._session.load()
        self._apply_session()
        self._publish()
        if self._session.is_mods and str(result.get("state")) == STATE_LOADING:
            if self._tasks is None:
                self._session.apply_scan_fallback()
                self._apply_session()
                self._publish()
                self._status("rm_operation_failed", {"error": "tasks_unavailable"}, "error")
            else:
                self._start_metadata()

    def _start_metadata(self) -> None:
        if self._session is None or self._tasks is None:
            return
        old = self._handles.get(TASK_META)
        if old is not None:
            try:
                old.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._busy = True
        self._progress = {"message": _("mod_loading_metadata"), "current": 0, "total": 0}
        self.busyChanged.emit()
        self.progressChanged.emit()
        session = self._session
        seq = session.version_id + "|" + session.kind
        directory = session.directory()

        def work(ctx: Any) -> Any:
            return session.metadata_task(ctx, directory)

        self._handles[TASK_META] = self._tasks.submit(
            work,
            name=TASK_META,
            on_progress=self._meta_progress,
            on_done=lambda rows: self._post({"kind": "metadata-done", "seq": seq, "rows": rows}),
            on_error=lambda err: self._post({"kind": "metadata-error", "seq": seq, "error": str(err)}),
            pass_context=True,
        )

    def _meta_progress(self, *args: Any, **kwargs: Any) -> None:
        current = int(kwargs.get("current", 0) or 0)
        total = int(kwargs.get("total", 0) or 0)
        self._post({"kind": "metadata-progress", "current": current, "total": total})

    def _decorate(self, item: Dict[str, Any]) -> Dict[str, Any]:
        row = dict(item)
        name = str(row.get("name") or row.get("filename") or "???")
        if row.get("disabled"):
            name += _("rm_disabled_suffix")
        row["display_name"] = name
        row["path"] = str(row.get("path", "") or "")
        row["filename"] = str(row.get("filename") or row.get("name") or "")
        row["size"] = str(row.get("size", "") or "")
        row["author"] = str(row.get("author", "") or "")
        row["description"] = str(row.get("description", "") or "")
        row["modid"] = str(row.get("modid", "") or "")
        row["version"] = str(row.get("version", "") or "")
        row["disabled"] = bool(row.get("disabled"))
        row["isMod"] = self._kind == KIND_MODS
        row["iconName"] = "mods" if row["isMod"] else "resourcepack"
        row["icon"] = self._icon_url(row)
        row.pop("icon_base64", None)
        return row

    def _icon_url(self, row: Dict[str, Any]) -> str:
        path = str(row.get("path", "") or "")
        cached = self._thumb_urls.get(path)
        if cached:
            return cached
        data = row.get("icon_base64")
        if not data:
            return ""
        provider = self._thumbnail_provider()
        if provider is None:
            return ""
        try:
            url = provider.register(path + "#icon", str(data))
        except Exception as e:  # noqa: BLE001
            logger.warning("登记服务器模组图标失败: %s", e)
            return ""
        if url:
            self._thumb_urls[path] = str(url)
            return str(url)
        return ""

    def _thumbnail_provider(self) -> Any:
        if self._engine is None:
            return None
        try:
            from app.bridges.thumbnail_provider import installed

            return installed(self._engine)
        except Exception:  # noqa: BLE001
            return None

    def _bridge(self, name: str) -> Any:
        if self._engine is None:
            return None
        return (getattr(self._engine, "_fmcl_bridges", None) or {}).get(name)

    def _publish(self) -> None:
        self.viewChanged.emit()

    def _set_status(self, key: str, params: Optional[Dict[str, Any]] = None, level: str = "info") -> None:
        try:
            text = _(key, **(params or {}))
        except Exception:  # noqa: BLE001
            text = key
        self.statusMessage.emit(text, level)

    def _status(self, key: str, params: Optional[Dict[str, Any]] = None, level: str = "info") -> None:
        self._set_status(key, params, level)

    def _post(self, item: Dict[str, Any]) -> None:
        if self._closing:
            return
        with self._pending_lock:
            self._pending.append(item)
        self.posted.emit()

    def _submit(self, work: Any, name: str, done: Any, failed: Any) -> Any:
        if self._tasks is None:
            failed("tasks_unavailable")
            return None
        try:
            handle = self._tasks.submit(work, name=name, on_done=done, on_error=failed)
        except Exception as e:  # noqa: BLE001
            failed(str(e))
            return None
        self._handles[name] = handle
        return handle

    # ─── 长活入口 ───────────────────────────────────────────

    @Slot()
    def filterClient(self) -> None:  # noqa: N802
        if self._session is None or not self._session.is_mods or self._tasks is None:
            return
        if self._busy:
            return
        self._busy = True
        self._progress = {"message": _("mod_filtering_status"), "current": 0, "total": 0}
        self.busyChanged.emit()
        self.progressChanged.emit()
        session = self._session
        directory = session.directory()

        def work(ctx: Any) -> Any:
            return session.filter_task(ctx, directory)

        self._handles[TASK_FILTER] = self._tasks.submit(
            work,
            name=TASK_FILTER,
            on_progress=self._filter_progress,
            on_done=lambda result: self._post({"kind": "filter-done", "result": result}),
            on_error=lambda err: self._post({"kind": "filter-error", "error": str(err)}),
            pass_context=True,
        )

    @Slot()
    def checkUpdates(self) -> None:  # noqa: N802
        if self._session is None or not self._session.is_mods or self._tasks is None:
            return
        if self._busy or self._checking:
            return
        prepared, error_key = self._session.prepare_update_check()
        if prepared is None:
            if error_key:
                self._status(error_key, {}, "warning")
            return
        mods, game_version, mod_loader = prepared
        self._checking = True
        self._busy = True
        self._progress = {
            "message": _("mod_checking_updates_progress", current=0, total=len(mods)),
            "current": 0,
            "total": len(mods),
        }
        self.busyChanged.emit()
        self.progressChanged.emit()

        def work(ctx: Any) -> Any:
            return self._session.check_updates_task(ctx, mods, game_version, mod_loader)

        self._handles[TASK_CHECK] = self._tasks.submit(
            work,
            name=TASK_CHECK,
            on_progress=self._check_progress,
            on_done=lambda result: self._post({"kind": "updates-done", "updates": result}),
            on_error=lambda err: self._post({"kind": "updates-error", "error": str(err)}),
            pass_context=True,
        )

    @Slot("QVariantList")
    def applyUpdates(self, modids: Any) -> None:  # noqa: N802
        if self._session is None or not self._session.is_mods or self._tasks is None:
            return
        wanted = [str(m) for m in (modids or []) if str(m)]
        if not wanted:
            return
        prepared, error_key = self._session.prepare_batch_update(wanted)
        if prepared is None:
            if error_key:
                self._status(error_key, {}, "warning")
            return
        wanted, game_version, mod_loader = prepared
        self._busy = True
        self._progress = {
            "message": _("mod_update_batch_progress", done=0, total=len(wanted)),
            "current": 0,
            "total": len(wanted),
        }
        self.busyChanged.emit()
        self.progressChanged.emit()
        self._set_status("mod_update_batch_starting", {"count": len(wanted)})
        session = self._session
        mods_dir = session.directory()

        def work(ctx: Any) -> Any:
            return session.batch_update_task(ctx, wanted, game_version, mod_loader, mods_dir)

        self._tasks.submit(
            work,
            name=TASK_BATCH,
            on_progress=self._batch_progress,
            on_done=lambda result: self._post({"kind": "batch-done", "result": result}),
            on_error=lambda err: self._post({"kind": "batch-error", "error": str(err)}),
            pass_context=True,
        )

    @Slot()
    def resetUpdates(self) -> None:  # noqa: N802
        self._updates = []
        self.updatesChanged.emit()

    def _filter_progress(self, *args: Any, **kwargs: Any) -> None:
        self._post(
            {
                "kind": "filter-progress",
                "current": int(kwargs.get("current", 0) or 0),
                "total": int(kwargs.get("total", 0) or 0),
            }
        )

    def _check_progress(self, *args: Any, **kwargs: Any) -> None:
        self._post(
            {
                "kind": "check-progress",
                "current": int(kwargs.get("current", 0) or 0),
                "total": int(kwargs.get("total", 0) or 0),
            }
        )

    def _batch_progress(self, *args: Any, **kwargs: Any) -> None:
        self._post(
            {
                "kind": "batch-progress",
                "current": int(kwargs.get("current", 0) or 0),
                "total": int(kwargs.get("total", 0) or 0),
            }
        )

    # ─── 内部：主线程落状态 ─────────────────────────────────

    def _deliver(self) -> None:
        with self._pending_lock:
            batch = self._pending[:]
            self._pending.clear()
        for item in batch:
            kind = item.get("kind")
            try:
                if kind == "metadata-progress":
                    self._apply_metadata_progress(item)
                elif kind == "metadata-done":
                    self._apply_metadata_done(item)
                elif kind == "metadata-error":
                    self._apply_metadata_error(item)
                elif kind == "install-done":
                    self._apply_install_done(item)
                elif kind == "install-error":
                    self._apply_install_error(item)
                elif kind == "filter-progress":
                    self._apply_filter_progress(item)
                elif kind == "filter-done":
                    self._apply_filter_done(item)
                elif kind == "filter-error":
                    self._apply_filter_error(item)
                elif kind == "check-progress":
                    self._apply_check_progress(item)
                elif kind == "updates-done":
                    self._apply_updates_done(item)
                elif kind == "updates-error":
                    self._apply_updates_error(item)
                elif kind == "batch-progress":
                    self._apply_batch_progress(item)
                elif kind == "batch-done":
                    self._apply_batch_done(item)
                elif kind == "batch-error":
                    self._apply_batch_error(item)
            except Exception as e:  # noqa: BLE001 - 一条失败不拖垮后续
                logger.error("处理服务器模组投递失败 (%s): %s", kind, e)

    def _session_current(self, seq: str) -> bool:
        if self._session is None:
            return False
        return seq == (self._session.version_id + "|" + self._session.kind)

    def _apply_metadata_progress(self, item: Dict[str, Any]) -> None:
        current = int(item.get("current", 0) or 0)
        total = int(item.get("total", 0) or 0)
        self._progress = {
            "message": _("mod_loading_progress", done=current, total=total),
            "current": current,
            "total": total,
        }
        self.progressChanged.emit()

    def _apply_metadata_done(self, item: Dict[str, Any]) -> None:
        if self._session is None or not self._session_current(str(item.get("seq", ""))):
            return
        self._busy = False
        self._progress = {}
        self._session.apply_metadata(item.get("rows") or [])
        self._apply_session()
        self.busyChanged.emit()
        self.progressChanged.emit()
        self._publish()

    def _apply_metadata_error(self, item: Dict[str, Any]) -> None:
        if self._session is None or not self._session_current(str(item.get("seq", ""))):
            return
        error = str(item.get("error", "") or "")
        self._busy = False
        self._progress = {}
        self._session.apply_scan_fallback()
        self._apply_session()
        self.busyChanged.emit()
        self.progressChanged.emit()
        self._publish()
        if "取消" not in error and "cancel" not in error.lower():
            self._status("rm_operation_failed", {"error": error}, "error")

    def _apply_install_done(self, item: Dict[str, Any]) -> None:
        count = int(item.get("count", 0) or 0)
        self._busy = False
        self._progress = {}
        self.busyChanged.emit()
        self.progressChanged.emit()
        if count > 0:
            self._status("rm_install_count", {"count": count}, "success")
            self._load()
        else:
            self._status("rm_no_valid_files", {}, "warning")

    def _apply_install_error(self, item: Dict[str, Any]) -> None:
        self._busy = False
        self._progress = {}
        self.busyChanged.emit()
        self.progressChanged.emit()
        self._status("rm_install_failed", {"error": str(item.get("error", "") or "")}, "error")

    def _apply_filter_progress(self, item: Dict[str, Any]) -> None:
        current = int(item.get("current", 0) or 0)
        total = int(item.get("total", 0) or 0)
        self._progress = {
            "message": (
                _("mod_filtering_status")
                if total <= 0
                else _("mod_checking_updates_progress", current=current, total=total)
            ),
            "current": current,
            "total": total,
        }
        self.progressChanged.emit()

    def _apply_filter_done(self, item: Dict[str, Any]) -> None:
        self._busy = False
        self._progress = {}
        self.busyChanged.emit()
        self.progressChanged.emit()
        result = item.get("result")
        unknown = self._session.unknown_rows(result) if self._session is not None else []
        if unknown:
            self.unknownModsReview.emit(unknown, len(unknown))
        self._load()

    def _apply_filter_error(self, item: Dict[str, Any]) -> None:
        self._busy = False
        self._progress = {}
        self.busyChanged.emit()
        self.progressChanged.emit()
        self._status("rm_operation_failed", {"error": str(item.get("error", "") or "")}, "error")

    def _apply_check_progress(self, item: Dict[str, Any]) -> None:
        current = int(item.get("current", 0) or 0)
        total = int(item.get("total", 0) or 0)
        self._progress = {
            "message": _("mod_checking_updates_progress", current=current, total=total),
            "current": current,
            "total": total,
        }
        self.progressChanged.emit()

    def _apply_updates_done(self, item: Dict[str, Any]) -> None:
        data = item.get("updates") or {}
        rows = []
        for modid, info in data.items():
            rows.append(
                {
                    "modid": str(modid),
                    "title": str(info.get("title") or modid),
                    "latest_version": str(info.get("latest_version") or ""),
                }
            )
        rows.sort(key=lambda r: r["title"].lower())
        self._updates = rows
        self._checking = False
        self._busy = False
        self._progress = {}
        self.busyChanged.emit()
        self.progressChanged.emit()
        self.updatesChanged.emit()
        if rows:
            self._status("mod_updates_available", {"count": len(rows)}, "success")
        else:
            self._status("mod_update_all_latest", {}, "info")

    def _apply_updates_error(self, item: Dict[str, Any]) -> None:
        self._checking = False
        self._busy = False
        self._progress = {}
        self.busyChanged.emit()
        self.progressChanged.emit()
        self._status("mod_update_check_failed", {"error": str(item.get("error", "") or "")}, "error")

    def _apply_batch_progress(self, item: Dict[str, Any]) -> None:
        current = int(item.get("current", 0) or 0)
        total = int(item.get("total", 0) or 0)
        self._progress = {
            "message": _("mod_update_batch_progress", done=current, total=total),
            "current": current,
            "total": total,
        }
        self.progressChanged.emit()

    def _apply_batch_done(self, item: Dict[str, Any]) -> None:
        result = item.get("result")
        success = int(result[0]) if isinstance(result, (tuple, list)) and result else 0
        failed = int(result[1]) if isinstance(result, (tuple, list)) and len(result) > 1 else 0
        self._busy = False
        self._progress = {}
        self._updates = []
        self.busyChanged.emit()
        self.progressChanged.emit()
        self.updatesChanged.emit()
        if failed > 0:
            self._status("mod_update_batch_done_partial", {"success": success, "failed": failed}, "warning")
        else:
            self._status("mod_update_batch_done", {"success": success}, "success")
        self._load()

    def _apply_batch_error(self, item: Dict[str, Any]) -> None:
        self._busy = False
        self._progress = {}
        self.busyChanged.emit()
        self.progressChanged.emit()
        self._status("mod_update_failed", {"error": str(item.get("error", "") or "")}, "error")
