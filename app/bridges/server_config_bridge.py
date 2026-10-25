"""服务器配置编辑器桥 —— QML 侧的 ServerConfig 上下文属性（阶段 3 任务 3.11）。

链路：

    qml/pages/servers/ServerConfigPage.qml
        ↓ 取属性 / 调槽
    app/bridges/server_config_bridge.py（本模块，零业务规则）
        ↓ 转发
    services/server_config_service.py（草稿 / 校验 / 保存 / 原文）

桥只做四件事：把会话状态念给 QML 听、把用户动作转成会话调用、把服务异常翻成
当前语言、把脏标记同步给 Nav 的页面级离开守卫。分类规则、默认值、INT 范围、
写盘策略全部在服务层，这里一条都不判（迁移红线 2）。

线程：只在主线程用。配置读写是毫秒级短操作，和旧编辑器一样同步执行；没有
TaskRunner 任务，也没有 worker 回调。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices

from services.errors import ServiceError
from services.i18n_service import _ as _t
from services.server_config_schema import CATEGORIES
from services.server_config_service import ServerConfigSession, memory_choices
from services.server_service import server_dir_path

logger = logging.getLogger("app.bridges.server_config_bridge")

#: 配置编辑器路由（页面级离开守卫按这个 id 登记）。
CONFIG_ROUTE = "servers/detail/config"


class ServerConfigBridge(QObject):
    """上下文属性 ServerConfig。"""

    stateChanged = Signal()
    rowsChanged = Signal()
    dirtyChanged = Signal()
    errorsChanged = Signal()
    rawChanged = Signal()
    statusMessage = Signal(str, str)
    validationFailed = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._launcher: Any = None
        self._nav: Any = None
        self._servers: Any = None
        self._session: Optional[ServerConfigSession] = None
        self._active = False
        self._version = ""
        self._error = ""

    # ─── 装配 ───────────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        self._context = context
        self._ensure_launcher()

    def use_engine(self, engine: Any) -> None:
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        self._nav = registry.get("Nav")
        self._servers = registry.get("Servers")

    def _ensure_launcher(self) -> None:
        if self._launcher is not None or self._context is None:
            return
        try:
            self._launcher = self._context.try_get("launcher")
        except Exception as e:  # noqa: BLE001 - 启动链条没跑完时 launcher 可能还不在
            logger.debug("取 launcher 失败: %s", e)

    @staticmethod
    def _t(key: str, **params: Any) -> str:
        try:
            return _t(key, **params)
        except Exception:  # noqa: BLE001 - 文案失败不挡住动作
            return key

    def _tr(self, key: str, **params: Any) -> str:
        return self._t(key, **params)

    # ─── 只读状态 ───────────────────────────────────────────

    @Property(bool, constant=True)
    def available(self) -> bool:
        return True

    @Property(str, notify=stateChanged)
    def version(self) -> str:
        return self._version

    @Property(str, notify=stateChanged)
    def pageState(self) -> str:  # noqa: N802
        if self._session is None or not self._active:
            return "error"
        return "error" if self._error else "ready"

    @Property(str, notify=stateChanged)
    def errorText(self) -> str:  # noqa: N802
        if self._error:
            if self._session is not None and self._session.load_error:
                return self._t("server_config_load_failed", error=self._session.load_error)
            return self._error
        if not self._version:
            return self._t("server_no_version_selected")
        return ""

    @Property(str, notify=stateChanged)
    def serverPath(self) -> str:  # noqa: N802
        return str(self._session.server_dir) if self._session is not None else ""

    @Property(str, notify=stateChanged)
    def propertiesPath(self) -> str:  # noqa: N802
        return str(self._session.properties_path) if self._session is not None else ""

    @Property(bool, notify=stateChanged)
    def fileMissing(self) -> bool:  # noqa: N802
        return bool(self._session and self._session.file_missing)

    @Property(bool, notify=stateChanged)
    def running(self) -> bool:
        return bool(self._session and self._session.running)

    @Property(bool, notify=stateChanged)
    def eulaAgreed(self) -> bool:  # noqa: N802
        return bool(self._session and self._session.eula_agreed)

    @Property(str, notify=stateChanged)
    def category(self) -> str:
        return self._session.category if self._session is not None else "world"

    @Property(str, notify=stateChanged)
    def search(self) -> str:
        return self._session.search if self._session is not None else ""

    @Property(bool, notify=stateChanged)
    def searching(self) -> bool:
        return bool(self._session and self._session.searching)

    @Property(int, notify=stateChanged)
    def searchCount(self) -> int:  # noqa: N802
        return self._session.search_count(self._tr) if self._session is not None else 0

    @Property(str, notify=stateChanged)
    def scopeLabel(self) -> str:  # noqa: N802
        if self._session is None:
            return ""
        return self._session.scope_name(self._tr)

    @Property(str, notify=stateChanged)
    def memory(self) -> str:
        return self._session.memory if self._session is not None else "2G"

    @Property("QVariantList", notify=stateChanged)
    def memoryOptions(self) -> List[str]:  # noqa: N802
        return memory_choices(self.memory)

    @Property("QVariantList", constant=True)
    def categories(self) -> List[Dict[str, str]]:
        return [{"id": item["id"], "titleKey": item["i18n"]} for item in CATEGORIES]

    @Property(int, notify=dirtyChanged)
    def dirtyCount(self) -> int:  # noqa: N802
        return self._session.dirty_count() if self._session is not None else 0

    @Property(bool, notify=dirtyChanged)
    def dirty(self) -> bool:
        return self.dirtyCount > 0

    @Property("QVariantList", notify=rowsChanged)
    def rows(self) -> List[Dict[str, Any]]:
        return self._session.rows(self._tr) if self._session is not None else []

    @Property(int, notify=rowsChanged)
    def rowCount(self) -> int:  # noqa: N802
        return len(self.rows)

    @Property("QVariantMap", notify=errorsChanged)
    def errors(self) -> Dict[str, str]:
        if self._session is None:
            return {}
        return {key: self._error_text(error) for key, error in self._session.validation_errors().items()}

    @Property(str, notify=rawChanged)
    def rawText(self) -> str:  # noqa: N802
        return self._session.raw_text() if self._session is not None else ""

    # ─── 页面激活 ───────────────────────────────────────────

    @Slot(str)
    def activate(self, version: str) -> None:  # noqa: N802
        """页面装载：绑定版本并从磁盘读一次配置（同一版本重复调用不重读）。"""
        wanted = str(version or "").strip()
        if not wanted:
            self._fail(self._t("server_no_version_selected"))
            return
        if self._active and self._session is not None and wanted == self._version:
            return
        if not self._safe_version(wanted):
            self._fail(self._t("server_config_load_failed", error="invalid_version"))
            return
        self._ensure_launcher()
        root = self._server_root()
        if root is None:
            self._fail(self._t("server_config_load_failed", error="launcher_unavailable"))
            return
        self._version = wanted
        running = self._query_running(wanted)
        session = ServerConfigSession(server_dir_path(root, wanted), wanted, running=running)
        self._session = session
        self._active = True
        if not session.load():
            self._error = self._t("server_config_load_failed", error=session.load_error or "load_failed")
        else:
            self._error = ""
        self._sync_guard()
        self._emit_all()
        self.rawChanged.emit()
        if not self._error:
            self.refreshRuntime()

    @Slot(str, result=bool)
    def open(self, version: str) -> bool:  # noqa: N802
        """从详情页 / 列表行打开配置页。"""
        wanted = str(version or "").strip()
        if not self._safe_version(wanted):
            self.statusMessage.emit(self._t("server_no_version_selected"), "error")
            return False
        if self._nav is None:
            self.statusMessage.emit(self._t("server_config_load_failed", error="navigation_unavailable"), "error")
            return False
        try:
            return bool(self._nav.push(CONFIG_ROUTE, {"server": wanted}))
        except Exception as e:  # noqa: BLE001
            logger.warning("打开服务器配置页失败: %s", e)
            return False

    @Slot()
    def release(self) -> None:  # noqa: N802
        """页面销毁：撤销守卫并释放会话（下次进入重新读盘）。"""
        self._active = False
        self._session = None
        self._error = ""
        self._sync_guard()

    @Slot()
    def refreshLabels(self) -> None:  # noqa: N802
        """语言热切换后重建行快照（字段内文案跟着变）。"""
        self.rowsChanged.emit()
        self.errorsChanged.emit()
        self.stateChanged.emit()

    @Slot()
    def refreshRuntime(self) -> None:  # noqa: N802
        """刷新运行状态与 EULA（页面内 2 秒轮询调用）。"""
        if self._session is None:
            return
        running = self._query_running(self._version)
        eula = self._session.eula_agreed
        self._session.refresh_runtime(running)
        if eula != self._session.eula_agreed:
            self.rowsChanged.emit()
        self.stateChanged.emit()

    # ─── 分类 / 搜索 / 编辑 ─────────────────────────────────

    @Slot(str)
    def setCategory(self, category_id: str) -> None:  # noqa: N802
        if self._session is None:
            return
        if not self._session.set_category(category_id):
            return
        self.rowsChanged.emit()
        self.errorsChanged.emit()
        self.stateChanged.emit()

    @Slot(str)
    def setSearch(self, text: str) -> None:
        if self._session is None:
            return
        wanted = str(text or "")
        if wanted == self._session.search:
            return
        self._session.set_search(wanted)
        self.rowsChanged.emit()
        self.stateChanged.emit()

    @Slot(str, "QVariant", result=bool)
    def setValue(self, key: str, value: Any) -> bool:
        if self._session is None:
            return False
        if not self._session.set_value(key, value):
            return False
        self._after_edit()
        return True

    @Slot(str, result=bool)
    def setMemory(self, memory: str) -> bool:  # noqa: N802
        if self._session is None:
            return False
        if not self._session.set_memory(memory):
            return False
        self._after_edit()
        return True

    @Slot(str)
    def resetOne(self, key: str) -> None:  # noqa: N802
        if self._session is None:
            return
        if self._session.reset_one(key):
            self._after_edit(refresh_rows=True)
            self.statusMessage.emit(self._t("server_config_reset_one_done"), "info")

    @Slot(result=int)
    def resetAll(self) -> int:  # noqa: N802
        """把当前可见分类 / 搜索结果恢复默认（调用方先弹确认框）。"""
        if self._session is None:
            return 0
        count = self._session.reset_visible(self._tr)
        self._after_edit(refresh_rows=True)
        if count:
            self.statusMessage.emit(self._t("server_config_reset_done"), "info")
        return count

    # ─── 保存 / 原始文本 ─────────────────────────────────────

    @Slot(result="QVariantMap")
    def save(self) -> Dict[str, Any]:
        if self._session is None:
            return {"ok": False, "error": self._t("server_config_load_failed", error="no_session")}
        try:
            result = self._session.save()
        except Exception as e:  # noqa: BLE001 - 服务异常必须有可见出口
            message = self._t("server_config_save_failed", error=str(e))
            self.statusMessage.emit(message, "error")
            return {"ok": False, "error": message}

        if not result.get("ok"):
            first = result.get("error")
            if isinstance(first, dict):
                message = self._error_text(first)
                self.errorsChanged.emit()
                self.validationFailed.emit(message, str(first.get("key", "")))
                return {"ok": False, "validation": True, "error": message}
            message = self._t("server_config_save_failed", error=str(first or "write_failed"))
            self.statusMessage.emit(message, "error")
            return {"ok": False, "error": message}

        self._error = ""
        self._after_edit(refresh_rows=True, refresh_raw=True)
        self._notify_servers()
        warning = str(result.get("warning") or "")
        if warning:
            message = self._t("server_memory_save_failed", error=warning)
            self.statusMessage.emit(message, "warning")
        else:
            message = self._t("server_config_saved")
            self.statusMessage.emit(message, "success")
        return {"ok": True, "message": message, "warning": warning}

    @Slot(result="QVariantMap")
    def reload(self) -> Dict[str, Any]:
        if self._session is None:
            return {"ok": False, "error": self._t("server_config_load_failed", error="no_session")}
        ok = self._session.load()
        self._error = (
            "" if ok else self._t("server_config_load_failed", error=self._session.load_error or "load_failed")
        )
        self._sync_guard()
        self._emit_all()
        self.rawChanged.emit()
        if ok:
            self.refreshRuntime()
            self.statusMessage.emit(self._t("server_config_reloaded"), "info")
        else:
            self.statusMessage.emit(self._error, "error")
        return {"ok": bool(ok), "error": "" if ok else self._error}

    @Slot(str, result="QVariantMap")
    def writeRaw(self, text: str) -> Dict[str, Any]:
        if self._session is None:
            return {"ok": False, "error": self._t("server_config_load_failed", error="no_session")}
        try:
            self._session.write_raw(text)
        except ServiceError as e:
            detail = str(e.detail or e.message or "write_failed")
            message = self._t("server_config_save_failed", error=detail)
            self.statusMessage.emit(message, "error")
            return {"ok": False, "error": message}
        except Exception as e:  # noqa: BLE001 - 任何异常都要回到界面
            message = self._t("server_config_save_failed", error=str(e))
            self.statusMessage.emit(message, "error")
            return {"ok": False, "error": message}
        self._error = ""
        self._sync_guard()
        self._emit_all()
        self.rawChanged.emit()
        self._notify_servers()
        self.statusMessage.emit(self._t("server_config_raw_written"), "success")
        return {"ok": True, "message": self._t("server_config_raw_written")}

    @Slot()
    def discardDraft(self) -> None:  # noqa: N802
        """丢弃草稿（离开守卫确认 / 页面关闭确认后调用）。"""
        if self._session is None:
            self._sync_guard()
            return
        self._session.discard_changes()
        self._sync_guard(force=False)
        self.rowsChanged.emit()
        self.dirtyChanged.emit()
        self.errorsChanged.emit()
        self.stateChanged.emit()
        self.rawChanged.emit()

    @Slot()
    def openFolder(self) -> None:  # noqa: N802
        if self._session is None:
            return
        try:
            path = self._session.server_dir
            path.mkdir(parents=True, exist_ok=True)
            ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception as e:  # noqa: BLE001
            logger.warning("打开服务器目录失败: %s", e)
            ok = False
        if not ok:
            self.statusMessage.emit(self._t("rm_open_folder_failed", error=self.serverPath), "error")

    # ─── 内部 ───────────────────────────────────────────────

    @staticmethod
    def _safe_version(version: str) -> bool:
        value = str(version or "").strip()
        if not value or value in (".", ".."):
            return False
        return "/" not in value and "\\" not in value

    def _server_root(self) -> Optional[Path]:
        self._ensure_launcher()
        fn = getattr(self._launcher, "get_server_dir", None)
        if not callable(fn):
            return None
        try:
            return Path(str(fn()))
        except Exception as e:  # noqa: BLE001
            logger.warning("取服务器根目录失败: %s", e)
            return None

    def _query_running(self, version: str) -> bool:
        fn = getattr(self._launcher, "is_server_running", None)
        if not callable(fn):
            return False
        try:
            return bool(fn(version))
        except Exception:  # noqa: BLE001 - 运行状态只是提示
            return False

    def _fail(self, message: str) -> None:
        self._error = message
        self._active = False
        self._session = None
        self._version = ""
        self._sync_guard()
        self.stateChanged.emit()
        self.statusMessage.emit(message, "error")

    def _after_edit(self, *, refresh_rows: bool = False, refresh_raw: bool = False) -> None:
        self._sync_guard()
        self.dirtyChanged.emit()
        self.errorsChanged.emit()
        self.stateChanged.emit()
        if refresh_rows:
            self.rowsChanged.emit()
        if refresh_raw:
            self.rawChanged.emit()

    def _emit_all(self) -> None:
        self.stateChanged.emit()
        self.rowsChanged.emit()
        self.dirtyChanged.emit()
        self.errorsChanged.emit()

    def _sync_guard(self, force: Optional[bool] = None) -> None:
        if force is not None:
            dirty = bool(force)
        else:
            dirty = bool(self._active and self._session is not None and self._session.dirty_count() > 0)
        if self._nav is None:
            return
        setter = getattr(self._nav, "setRouteLeaveGuard", None)
        if not callable(setter):
            return
        try:
            setter(CONFIG_ROUTE, dirty)
        except Exception as e:  # noqa: BLE001 - 守卫接不上不该阻止编辑
            logger.warning("登记服务器配置页离开守卫失败: %s", e)

    def _notify_servers(self) -> None:
        if self._servers is None:
            return
        fn = getattr(self._servers, "refreshConfig", None)
        if not callable(fn):
            return
        try:
            fn(self._version)
        except Exception as e:  # noqa: BLE001
            logger.warning("通知服务器页刷新配置失败: %s", e)

    def _error_text(self, error: Dict[str, Any]) -> str:
        name = self._t(str(error.get("nameKey") or ""))
        if str(error.get("code")) == "range":
            return self._t("server_config_invalid_int", name=name, min=error.get("min", ""), max=error.get("max", ""))
        return self._t("server_config_invalid_int_plain", name=name)


__all__ = ["CONFIG_ROUTE", "ServerConfigBridge"]
