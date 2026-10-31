"""存档备份桥 —— QML 侧的 Backups（阶段 3 任务 3.14）。

链路：

    qml/pages/backups/BackupsPage.qml
        -> 取属性 / 调槽
    app/bridges/backup_bridge.py（本模块，零业务规则）
        -> 转发
    services/backup_session.py（世界 / 备份列表 / 设置草稿 / 任务入口）
        -> 委托
    services/backup_manager.py（真正的 zip / 索引 / 恢复 / 删除 / 导出）

线程约定与 3.6 ~ 3.13 相同：长活全部走 TaskRunner，worker 只 _post 排队，
真正改状态的是主线程 _deliver()。桥自己不做文件操作、不拼业务判断。
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from functools import partial
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Qt, Signal, Slot

from services.backup_session import COMPRESS_LEVELS, MAX_OPTIONS, STATE_ERROR, STATE_IDLE, STATE_LOADING, BackupSession
from services.i18n_service import _

logger = logging.getLogger("app.bridges.backup_bridge")

QML_NAME = "Backups"

TASK_SCAN = "backup.scan_worlds"
TASK_CREATE = "backup.create"
TASK_RESTORE = "backup.restore"
TASK_DELETE = "backup.delete"
TASK_VERIFY = "backup.verify"
TASK_EXPORT = "backup.export"


class BackupBridge(QObject):
    """上下文属性 Backups。"""

    viewChanged = Signal()
    settingsChanged = Signal()
    busyChanged = Signal()
    progressChanged = Signal()
    statusMessage = Signal(str, str)
    noticeRequested = Signal(str, str)
    settingsSaved = Signal()
    posted = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._config: Any = None
        self._engine: Any = None
        self._achievement: Any = None
        self._tasks: Any = None
        self._session: Optional[BackupSession] = None

        self._worlds: List[Dict[str, Any]] = []
        self._worlds_state = STATE_IDLE
        self._worlds_error = ""
        self._worlds_loaded = False
        self._selected_world = ""
        self._backups: List[Dict[str, Any]] = []
        self._backups_state = STATE_IDLE
        self._backups_error = ""
        self._focused_backup = ""
        self._wanted_world = ""

        self._busy = False
        self._progress: Dict[str, Any] = {}
        self._auto_launch = False
        self._auto_exit = False
        self._settings: Dict[str, Any] = {}
        self._settings_dirty = False
        self._backup_root = ""

        self._scan_token = 0
        self._op_token = 0
        self._handles: Dict[str, Any] = {}
        self._pending: List[Dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        self._closing = False
        self.posted.connect(self._deliver, Qt.QueuedConnection)

    # ─── 装配 ───────────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        self._context = context
        self._config = getattr(context, "config", None) if context is not None else None
        self._achievement = self._service_of("achievement")
        self._tasks = getattr(context, "tasks", None) if context is not None else None
        if self._config is None:
            logger.warning("配置不可用：存档备份页只会显示错误态")
            self._session = None
        else:
            self._session = BackupSession(self._config, achievement=self._achievement)
        self._worlds_loaded = False
        self._sync()

    def _service_of(self, name: str) -> Any:
        if self._context is None:
            return None
        try:
            return self._context.try_get(name)
        except Exception as e:  # noqa: BLE001 - 单个服务缺失不该让整页打不开
            logger.warning("备份桥取服务 %s 失败: %s", name, e)
            return None

    def use_engine(self, engine: Any) -> None:
        """拿到 QML 引擎后接上 Tr 的语言信号，让设置下拉在热切换后重取文案。"""
        self._engine = engine
        tr = (getattr(engine, "_fmcl_bridges", None) or {}).get("Tr")
        if tr is not None and hasattr(tr, "languageChanged"):
            try:
                tr.languageChanged.connect(self._on_language_changed)
            except Exception as e:  # noqa: BLE001
                logger.warning("连接语言切换信号失败: %s", e)

    def _on_language_changed(self) -> None:
        #: 选项文案在属性里现取；通知 QML 重新读一遍，页面随后会重设下拉索引。
        self.viewChanged.emit()
        self.settingsChanged.emit()

    def detach(self) -> None:
        self._closing = True
        with self._pending_lock:
            self._pending.clear()
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

    @Property("QVariantList", notify=viewChanged)
    def worlds(self) -> List[Dict[str, Any]]:
        return self._worlds

    @Property(int, notify=viewChanged)
    def worldCount(self) -> int:  # noqa: N802
        return len(self._worlds)

    @Property(str, notify=viewChanged)
    def worldsState(self) -> str:  # noqa: N802
        return self._worlds_state

    @Property(str, notify=viewChanged)
    def worldsError(self) -> str:  # noqa: N802
        return self._worlds_error

    @Property(str, notify=viewChanged)
    def selectedWorld(self) -> str:  # noqa: N802
        return self._selected_world

    @Property(bool, notify=viewChanged)
    def hasSelection(self) -> bool:  # noqa: N802
        return bool(self._selected_world)

    @Property("QVariantList", notify=viewChanged)
    def backups(self) -> List[Dict[str, Any]]:
        return self._backups

    @Property(int, notify=viewChanged)
    def backupCount(self) -> int:  # noqa: N802
        return len(self._backups)

    @Property(str, notify=viewChanged)
    def backupsState(self) -> str:  # noqa: N802
        return self._backups_state

    @Property(str, notify=viewChanged)
    def backupsError(self) -> str:  # noqa: N802
        return self._backups_error

    @Property(str, notify=viewChanged)
    def focusedBackupId(self) -> str:  # noqa: N802
        return self._focused_backup

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=progressChanged)
    def progressVisible(self) -> bool:  # noqa: N802
        return bool(self._progress)

    @Property("QVariantMap", notify=progressChanged)
    def progress(self) -> Dict[str, Any]:
        return self._progress

    @Property(bool, notify=viewChanged)
    def autoLaunch(self) -> bool:  # noqa: N802
        return self._auto_launch

    @Property(bool, notify=viewChanged)
    def autoExit(self) -> bool:  # noqa: N802
        return self._auto_exit

    @Property("QVariantMap", notify=settingsChanged)
    def settings(self) -> Dict[str, Any]:
        return self._settings

    @Property(bool, notify=settingsChanged)
    def settingsDirty(self) -> bool:  # noqa: N802
        return self._settings_dirty

    @Property(str, notify=viewChanged)
    def backupRoot(self) -> str:  # noqa: N802
        return self._backup_root

    @Property(str, notify=settingsChanged)
    def zipFilter(self) -> str:  # noqa: N802
        return _("backup_zip_filter") + " (*.zip)"

    @Property("QVariantList", notify=settingsChanged)
    def compressOptions(self) -> List[Dict[str, Any]]:  # noqa: N802
        return [{"value": value, "label": _("backup_settings_compress_%d" % value)} for value in COMPRESS_LEVELS]

    @Property("QVariantList", notify=settingsChanged)
    def maxOptions(self) -> List[Dict[str, Any]]:  # noqa: N802
        return [
            {"value": value, "label": str(value) if value > 0 else _("backup_settings_max_unlimited")}
            for value in MAX_OPTIONS
        ]

    @Property("QVariantList", notify=settingsChanged)
    def restoreOptions(self) -> List[Dict[str, Any]]:  # noqa: N802
        return [
            {"value": "rename", "label": _("backup_settings_restore_rename_opt")},
            {"value": "overwrite", "label": _("backup_settings_restore_overwrite_opt")},
            {"value": "trash", "label": _("backup_settings_restore_trash_opt")},
        ]

    # ─── 页面入口 ───────────────────────────────────────────

    @Slot(str, str)
    def activate(self, world: str, backup: str) -> None:
        """页面进入 / 路由参数变化时调用；只负责定位，不重复扫描。"""
        wanted = str(world or "").strip()
        self._wanted_world = wanted
        self._focused_backup = str(backup or "").strip()
        if self._session is None:
            self._sync()
            return
        if wanted and not self._busy:
            self._session.select_world(wanted)
            self._selected_world = self._session.selected_world
        if not self._worlds_loaded and not self._busy:
            self.refresh()
        else:
            self._sync()

    @Slot()
    def refresh(self) -> None:
        if self._session is None or self._busy:
            return
        self._scan_token += 1
        token = self._scan_token
        self._busy = True
        if not self._worlds:
            self._worlds_state = STATE_LOADING
            self._worlds_error = ""
            self.viewChanged.emit()
        self._progress = {"message": _("backup_scanning"), "current": 0, "total": 0, "indeterminate": True}
        self.busyChanged.emit()
        self.progressChanged.emit()
        session = self._session

        if self._tasks is None:
            result = session.scan_worlds()
            self._apply_scan(token, result)
            return

        def work() -> Any:
            return session.scan_worlds()

        def done(result: Any) -> None:
            self._post({"kind": "scan-done", "token": token, "result": result})

        def failed(error: Any) -> None:
            self._post({"kind": "scan-error", "token": token, "error": str(error)})

        try:
            self._handles[TASK_SCAN] = self._tasks.submit(work, name=TASK_SCAN, on_done=done, on_error=failed)
        except Exception as e:  # noqa: BLE001
            failed(e)

    def _apply_scan(self, token: int, result: Dict[str, Any]) -> None:
        if token != self._scan_token or self._closing or self._session is None:
            return
        self._session.apply_worlds(result)
        self._worlds_loaded = True
        self._busy = False
        self._progress = {}
        self.busyChanged.emit()
        self.progressChanged.emit()
        if self._wanted_world and self._session.selected_world != self._wanted_world:
            self._session.select_world(self._wanted_world)
        # 路由里的世界只定位一次；否则用户后来手动选别的世界，刷新会被拉回去。
        self._wanted_world = ""
        if result.get("ok"):
            self._set_status("backup_worlds_found", {"count": len(self._session.worlds)}, "success")
        else:
            self._set_status("backup_status_failed", {"msg": str(result.get("error", ""))}, "error")
        self._sync()

    @Slot(str)
    def selectWorld(self, name: str) -> None:  # noqa: N802
        if self._session is None or self._busy:
            return
        self._wanted_world = ""
        changed = self._session.select_world(str(name or ""))
        self._selected_world = self._session.selected_world
        if changed:
            self._set_status("backup_world_selected", {"world": self._selected_world}, "info")
        self._sync()

    @Slot()
    def refreshBackups(self) -> None:  # noqa: N802
        if self._session is None or self._busy:
            return
        self._session.reload_backups()
        self._sync()

    # ─── 备份操作 ───────────────────────────────────────────

    @Slot(str)
    def createBackup(self, note: str) -> None:  # noqa: N802
        if self._session is None or self._busy or not self._selected_world:
            if self._session is not None and not self._selected_world:
                self._set_status("backup_no_world_selected", {}, "warning")
            return
        token = self._begin_op("backup_creating", {"world": self._selected_world})
        session = self._session
        world = self._selected_world

        def work(ctx: Any) -> Any:
            return session.create_backup_task(ctx, world, str(note or "").strip())

        def done(result: Any) -> None:
            self._post({"kind": "create-done", "token": token, "result": result})

        def failed(error: Any) -> None:
            self._post({"kind": "op-error", "token": token, "error": str(error)})

        self._submit(work, TASK_CREATE, done, failed, pass_context=True, with_progress=True, token=token)

    @Slot(str)
    def restoreBackup(self, entry_id: str) -> None:  # noqa: N802
        if self._session is None or self._busy or not self._selected_world:
            return
        token = self._begin_op("backup_restoring", {"world": self._selected_world})
        session = self._session
        world = self._selected_world

        def work(ctx: Any) -> Any:
            return session.restore_backup_task(ctx, world, str(entry_id or ""))

        def done(result: Any) -> None:
            self._post({"kind": "restore-done", "token": token, "result": result})

        def failed(error: Any) -> None:
            self._post({"kind": "op-error", "token": token, "error": str(error)})

        self._submit(work, TASK_RESTORE, done, failed, pass_context=True, with_progress=True, token=token)

    @Slot(str)
    def deleteBackup(self, entry_id: str) -> None:  # noqa: N802
        if self._session is None or self._busy or not self._selected_world:
            return
        token = self._begin_op("backup_delete", {})
        session = self._session
        world = self._selected_world

        def work() -> Any:
            return session.delete_backup_task(world, str(entry_id or ""))

        def done(result: Any) -> None:
            self._post({"kind": "delete-done", "token": token, "result": result})

        def failed(error: Any) -> None:
            self._post({"kind": "op-error", "token": token, "error": str(error)})

        self._submit(work, TASK_DELETE, done, failed)

    @Slot(str)
    def verifyBackup(self, entry_id: str) -> None:  # noqa: N802
        if self._session is None or self._busy or not self._selected_world:
            return
        token = self._begin_op("backup_verify", {})
        session = self._session
        world = self._selected_world

        def work() -> Any:
            return session.verify_backup_task(world, str(entry_id or ""))

        def done(result: Any) -> None:
            self._post({"kind": "verify-done", "token": token, "result": result})

        def failed(error: Any) -> None:
            self._post({"kind": "op-error", "token": token, "error": str(error)})

        self._submit(work, TASK_VERIFY, done, failed)

    @Slot(str, str)
    def exportBackup(self, entry_id: str, dest_path: str) -> None:  # noqa: N802
        if self._session is None or self._busy or not self._selected_world:
            return
        path = str(dest_path or "").strip()
        if not path:
            return
        if not path.lower().endswith(".zip"):
            path += ".zip"
        token = self._begin_op("backup_export", {})
        session = self._session
        world = self._selected_world

        def work() -> Any:
            return session.export_backup_task(world, str(entry_id or ""), path)

        def done(result: Any) -> None:
            self._post({"kind": "export-done", "token": token, "result": result})

        def failed(error: Any) -> None:
            self._post({"kind": "op-error", "token": token, "error": str(error)})

        self._submit(work, TASK_EXPORT, done, failed)

    @Slot()
    def openFolder(self) -> None:  # noqa: N802
        if self._session is None:
            return
        result = self._session.open_folder()
        if not result.get("ok"):
            self._set_status("backup_folder_open_failed", {"error": str(result.get("error", ""))}, "error")

    # ─── 设置 ───────────────────────────────────────────────

    @Slot()
    def openSettings(self) -> None:  # noqa: N802
        if self._session is None:
            return
        self._session.begin_settings()
        self._sync_settings(force=True)

    @Slot()
    def closeSettings(self) -> None:  # noqa: N802
        if self._session is None:
            return
        self._session.cancel_settings()
        self._sync_settings(force=True)

    @Slot(str, "QVariant")
    def setSettingsField(self, field: str, value: Any) -> None:  # noqa: N802
        if self._session is None:
            return
        if self._session.set_settings_field(str(field or ""), value):
            self._sync_settings()

    @Slot()
    def resetSettingsPath(self) -> None:  # noqa: N802
        if self._session is None:
            return
        self._session.reset_settings_path()
        self._sync_settings()

    @Slot("QVariant")
    def takeSettingsDirectory(self, url: Any) -> None:  # noqa: N802
        if self._session is None:
            return
        path = self._local_path(url)
        if not path:
            return
        self._session.set_settings_field("path", path)
        self._sync_settings()

    @Slot()
    def saveSettings(self) -> None:  # noqa: N802
        if self._session is None:
            return
        result = self._session.save_settings()
        self._sync()
        if not result.get("ok"):
            code = str(result.get("error", "") or "")
            if code == "path_not_directory":
                message = _("backup_settings_path_invalid")
            elif code == "config_save_failed":
                message = _("backup_settings_save_failed", error=_("backup_settings_save_config_failed"))
            else:
                message = _("backup_settings_save_failed", error=str(result.get("detail", "") or code))
            self.statusMessage.emit(message, "error")
            return
        self._set_status("backup_settings_save_success", {}, "success")
        self.noticeRequested.emit(_("backup_settings_save_success"), "success")
        self.settingsSaved.emit()

    # ─── 自动备份开关 ──────────────────────────────────────

    @Slot(bool)
    def setAutoLaunch(self, enabled: bool) -> None:  # noqa: N802
        self._set_auto("launch", enabled)

    @Slot(bool)
    def setAutoExit(self, enabled: bool) -> None:  # noqa: N802
        self._set_auto("exit", enabled)

    def _set_auto(self, which: str, enabled: bool) -> None:
        if self._session is None:
            return
        result = self._session.set_auto(which, enabled)
        self._sync()
        if not result.get("ok"):
            self._set_status("backup_settings_save_failed", {"error": _("backup_settings_save_config_failed")}, "error")

    # ─── 内部：状态同步 ─────────────────────────────────────

    def _sync(self) -> None:
        session = self._session
        if session is None:
            self._worlds = []
            self._worlds_state = STATE_ERROR
            self._worlds_error = "unavailable"
            self._backups = []
            self._backups_state = STATE_ERROR
            self._backups_error = "unavailable"
            self._settings = {}
            self._settings_dirty = False
            self.viewChanged.emit()
            self.settingsChanged.emit()
            return

        self._worlds = [
            {
                "name": str(item.get("name", "") or ""),
                "isIsolated": bool(item.get("is_isolated", False)),
                "iconName": "lock" if item.get("is_isolated") else "world",
            }
            for item in session.worlds
        ]
        self._worlds_state = session.worlds_state
        self._worlds_error = session.worlds_error
        self._selected_world = session.selected_world
        self._backups = [self._decorate(entry) for entry in session.backups]
        self._backups_state = session.backups_state
        self._backups_error = session.backups_error
        self._auto_launch = session.auto_launch
        self._auto_exit = session.auto_exit
        self._backup_root = session.backup_root
        if self._focused_backup and all(row.get("id") != self._focused_backup for row in self._backups):
            self._focused_backup = ""
        self.viewChanged.emit()
        self._sync_settings()

    def _sync_settings(self, force: bool = False) -> None:
        if self._session is None:
            return
        new_settings = self._session.settings
        new_dirty = self._session.settings_dirty
        if force or new_settings != self._settings or new_dirty != self._settings_dirty:
            self._settings = new_settings
            self._settings_dirty = new_dirty
            self.settingsChanged.emit()

    def _decorate(self, entry: Any) -> Dict[str, Any]:
        raw_time = str(getattr(entry, "timestamp", "") or "")
        dt: Optional[datetime] = None
        if raw_time:
            try:
                dt = datetime.fromisoformat(raw_time)
            except (TypeError, ValueError):
                dt = None
        if dt is None:
            time_text = _("backup_unknown_time")
            full_time = ""
        else:
            if dt.tzinfo is not None:
                dt = dt.astimezone()
            time_text = dt.strftime("%m-%d %H:%M")
            full_time = dt.strftime("%Y-%m-%d %H:%M:%S")
        note = str(getattr(entry, "note", "") or "")
        version = str(getattr(entry, "game_version", "") or "")
        return {
            "id": str(getattr(entry, "id", "") or ""),
            "worldName": str(getattr(entry, "world_name", "") or ""),
            "timeText": time_text,
            "fullTime": full_time,
            "sizeText": str(getattr(entry, "size_display", "") or ""),
            "version": version,
            "versionText": version if version else "-",
            "note": note,
            "noteText": note if note else _("backup_no_note"),
            "fileName": str(getattr(entry, "file_name", "") or ""),
        }

    # ─── 内部：任务 / 队列 ─────────────────────────────────

    def _begin_op(self, message_key: str, params: Dict[str, Any]) -> int:
        self._op_token += 1
        token = self._op_token
        self._busy = True
        message = message_key
        try:
            message = _(message_key, **(params or {}))
        except Exception:  # noqa: BLE001
            pass
        self._progress = {"message": message, "current": 0, "total": 0, "fraction": 0, "indeterminate": True}
        self.busyChanged.emit()
        self.progressChanged.emit()
        return token

    def _finish_op(self, token: int) -> bool:
        if token != self._op_token:
            return False
        self._busy = False
        self._progress = {}
        self.busyChanged.emit()
        self.progressChanged.emit()
        return True

    def _submit(
        self,
        work: Any,
        name: str,
        done: Any,
        failed: Any,
        *,
        pass_context: bool = False,
        with_progress: bool = False,
        token: int = 0,
    ) -> None:
        if self._tasks is None:
            failed("tasks_unavailable")
            return
        kwargs: Dict[str, Any] = {}
        if with_progress:
            kwargs["on_progress"] = partial(self._on_progress, token)
        try:
            self._handles[name] = self._tasks.submit(
                work, name=name, on_done=done, on_error=failed, pass_context=pass_context, **kwargs
            )
        except Exception as e:  # noqa: BLE001
            failed(e)

    def _on_progress(self, token: int, *args: Any, **kwargs: Any) -> None:
        current = int(kwargs.get("current", 0) or 0)
        total = int(kwargs.get("total", 0) or 0)
        fraction = (float(current) / float(total)) if total > 0 else 0.0
        self._post(
            {
                "kind": "op-progress",
                "token": token,
                "current": current,
                "total": total,
                "fraction": fraction,
                "indeterminate": total <= 0,
            }
        )

    def _post(self, item: Dict[str, Any]) -> None:
        if self._closing:
            return
        with self._pending_lock:
            self._pending.append(item)
        self.posted.emit()

    def _deliver(self) -> None:
        while True:
            with self._pending_lock:
                if not self._pending:
                    return
                item = self._pending.pop(0)
            if self._closing:
                return
            kind = str(item.get("kind", ""))
            token = int(item.get("token", 0) or 0)
            if kind == "scan-done":
                if token == self._scan_token:
                    result = item.get("result", {}) or {}
                    if isinstance(result, dict):
                        self._apply_scan(token, result)
            elif kind == "scan-error":
                if token == self._scan_token:
                    self._busy = False
                    self._progress = {}
                    self._worlds_state = STATE_ERROR
                    self._worlds_error = str(item.get("error", "") or "")
                    self.busyChanged.emit()
                    self.progressChanged.emit()
                    self._set_status("backup_status_failed", {"msg": self._worlds_error}, "error")
                    self._sync()
            elif kind == "op-progress":
                if token == self._op_token:
                    self._progress = {
                        "message": self._progress.get("message", ""),
                        "current": int(item.get("current", 0) or 0),
                        "total": int(item.get("total", 0) or 0),
                        "fraction": float(item.get("fraction", 0) or 0),
                        "indeterminate": bool(item.get("indeterminate", True)),
                    }
                    self.progressChanged.emit()
            elif kind == "op-error":
                if self._finish_op(token):
                    self._set_status("backup_status_failed", {"msg": str(item.get("error", ""))}, "error")
                    self.noticeRequested.emit(_("backup_status_failed", msg=str(item.get("error", ""))), "error")
                    self._sync()
            elif kind in ("create-done", "restore-done", "delete-done", "verify-done", "export-done"):
                if not self._finish_op(token):
                    continue
                self._handle_result(kind, item.get("result", {}) or {})

    def _handle_result(self, kind: str, result: Dict[str, Any]) -> None:
        if self._session is None:
            return
        ok = bool(result.get("ok"))
        message = str(result.get("message", "") or "")
        if kind == "create-done":
            if ok:
                self._session.apply_create_result(result)
                self._selected_world = self._session.selected_world
                self._sync()
                self._set_status("backup_status_success", {"world": self._selected_world}, "success")
                self.noticeRequested.emit(_("notify_backup_done"), "success")
            else:
                self._set_status("backup_status_failed", {"msg": message}, "error")
                self.noticeRequested.emit(_("notify_backup_failed"), "error")
                self._sync()
        elif kind == "restore-done":
            if ok:
                self._session.apply_restore_result(result)
                self._sync()
                self._set_status("backup_restore_status_success", {"world": self._selected_world}, "success")
                if str(result.get("effective_mode", "")) == "rename":
                    requested = str(getattr(self._config, "backup_restore_mode", "") or "")
                    if requested == "trash":
                        self.noticeRequested.emit(_("backup_restore_trash_fallback"), "warning")
                        return
                self.noticeRequested.emit(_("notify_restore_done"), "success")
            else:
                self._set_status("backup_restore_status_failed", {"msg": message}, "error")
                self.noticeRequested.emit(_("notify_restore_failed"), "error")
                self._sync()
        elif kind == "delete-done":
            if ok:
                if self._focused_backup and all(
                    str(row.get("id", "")) != self._focused_backup for row in self._session.backups
                ):
                    self._focused_backup = ""
                self._session.reload_backups()
                self._sync()
                self._set_status("backup_delete_status_success", {}, "success")
            else:
                self._set_status("backup_delete_status_failed", {"msg": message}, "error")
                self._sync()
        elif kind == "verify-done":
            if ok:
                self._set_status("backup_verify_ok", {}, "success")
                self.noticeRequested.emit(_("backup_verify_ok"), "success")
            else:
                self._set_status("backup_verify_failed", {"error": message}, "error")
                self.noticeRequested.emit(_("backup_verify_failed", error=message), "error")
        elif kind == "export-done":
            if ok:
                self._session.apply_export_result(result)
                self._sync()
                self._set_status("backup_export_status_success", {}, "success")
                self.noticeRequested.emit(_("backup_export_status_success"), "success")
            else:
                self._set_status("backup_export_status_failed", {"msg": message}, "error")
                self.noticeRequested.emit(_("backup_export_status_failed", msg=message), "error")
                self._sync()

    # ─── 内部：文案 / 路径 ─────────────────────────────────

    def _local_path(self, url: Any) -> str:
        try:
            if hasattr(url, "toLocalFile"):
                return str(url.toLocalFile() or "")
        except Exception:  # noqa: BLE001
            pass
        text = str(url or "")
        if text.startswith("file:"):
            from urllib.parse import unquote, urlparse

            try:
                path = unquote(urlparse(text).path)
                if os.name == "nt" and len(path) > 2 and path[0] == "/" and path[2] == ":":
                    path = path[1:]
                return path
            except Exception:  # noqa: BLE001
                return ""
        return text

    def _set_status(self, key: str, params: Dict[str, Any], level: str = "info") -> None:
        try:
            text = _(key, **(params or {}))
        except Exception:  # noqa: BLE001
            text = key
        self.statusMessage.emit(text, level)
