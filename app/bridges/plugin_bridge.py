"""插件桥 —— QML 侧的 `Plugins` 上下文属性（阶段 3 任务 3.27）。

## 它是什么

「设置 → 插件」分区、插件管理器页、插件市场页的**全部界面状态**都在这里：
插件卡片（状态/版本/权限/UI 页面数/更新提示）、市场索引与分页、忙碌与进度、
通知与页面事件。业务在 `services/plugin_service.py`；本桥只做三件事：

1. **延迟初始化**：`PluginManager` 的扫描要 import 各插件模块，不能在
   `build_context().start_all()` 里做（那时窗口还没出来）。桥在事件循环起来
   之后（生产装配在启动流程开跑后排一个 300ms 的 QTimer）再初始化，启动画面照常先画。
2. **切线程**：安装/更新/检查更新都是长活，走 `TaskRunner`；worker 只向
   `TaskContext.progress()` 报进度，所有属性只有主线程改。
3. **页面路由**：把服务采集到的 v2 页面登记成 Nav 路由，并在启用/禁用/卸载后
   重新登记（`Nav.unregister_plugin_routes` + `add_plugin_route`）。

## 数据通道（v2）

- 页面取数：`Plugins.call(owner, api, args)` → 插件用 `manager.export_api` 导出的函数。
- 插件推送：`PluginBase.emit_ui_event(name, payload)` → 本桥 `pageEvent(owner, name, payload)`。
- 权限：页面必须在清单里声明 `ui.extend`（低风险，安装时确认）。

## 线程前提

QML 槽都在主线程；插件触发 Ui 事件与通知可能在任意线程。信号发射交给 Qt 的
队列连接（QML 接收方在主线程），桥不直接改任何属性。
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from app.bridges.nav_bridge import current_nav, qml_root
from services.browse_common import permission_summary
from services.i18n_service import _
from services.plugin_service import (
    ERROR_INVALID_FMPL,
    ERROR_INVALID_MANIFEST,
    ERROR_NO_MARKET,
    ERROR_NOT_READY,
    ERROR_READ_FAILED,
)
from services.plugin_service import permission_groups as build_permission_groups

logger = logging.getLogger("app.bridges.plugin_bridge")

#: 市场每页条数（M-31：每页 10，与旧 `PluginBrowserWindow.PAGE_SIZE` 一致）。
MARKET_PAGE_SIZE = 10


def _unwrap_qml_value(value: Any) -> Any:
    """QML 的 JS 值（QJSValue）→ Python 值。

    QML 调 `Plugins.call(owner, api, {})` 时，`@Slot(..., "QVariant")` 拿到的
    不是 dict 而是 `QJSValue`（实测对象、数组、标量都是）；不还原就会把整个
    JS 值当成一个位置实参交给插件。这里只认 `toVariant` 方法名，桥本来就依赖 Qt。
    """
    to_variant = getattr(value, "toVariant", None)
    if not callable(to_variant):
        return value
    try:
        return to_variant()
    except Exception:  # noqa: BLE001 - QJSValue 已失效时按原值走，让调用处报错
        return value


class _SyncTaskContext:
    """没有 TaskRunner 时的替身（测试/探针直接调桥）。`progress` 是空操作。"""

    @staticmethod
    def progress(**kwargs: Any) -> None:
        return None


#: 服务层机器码 → i18n 键。
_ERROR_KEYS = {
    ERROR_INVALID_FMPL: "plugin_invalid_fmpl",
    ERROR_INVALID_MANIFEST: "plugin_invalid_manifest",
    ERROR_READ_FAILED: "plugin_invalid_fmpl",
    ERROR_NOT_READY: "plugin_not_initialized",
    ERROR_NO_MARKET: "plugin_market_unavailable",
}


class PluginBridge(QObject):
    """上下文属性 `Plugins`。"""

    #: 状态条文案（已翻译；页面转给 `Shell.setStatus`）。
    statusMessage = Signal(str, str)
    #: 插件通知（plugin_id, title, message, level）；QML 转成 Toast。
    notified = Signal(str, str, str, str)
    #: 插件页面推送（owner, event, payload）。
    pageEvent = Signal(str, str, "QVariant")

    pluginsChanged = Signal()
    summaryChanged = Signal()
    pagesChanged = Signal()
    stateChanged = Signal()
    busyChanged = Signal()
    marketChanged = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._service: Any = None
        self._tasks: Any = None
        self._engine: Any = None
        self._closing = False

        self._initialized = False
        self._initializing = False
        self._init_error = ""

        self._plugins: List[Dict[str, Any]] = []
        self._summary: Dict[str, int] = {"enabled": 0, "total": 0, "pages": 0, "unsupported": 0}
        self._by_id: Dict[str, Dict[str, Any]] = {}

        self._busy = False
        self._busy_text = ""
        self._progress = -1.0
        self._busy_name = ""

        self._market_items: List[Dict[str, Any]] = []
        self._market_filtered: List[Dict[str, Any]] = []
        self._market_tags: Dict[str, str] = {}
        self._market_query = ""
        self._market_tag = ""
        self._market_page = 1
        self._market_loading = False
        self._market_error = ""
        self._market_loaded = False

        self._page_signature: tuple = ()
        self._page_owners: set = set()
        self._handles: List[Any] = []

    # ── 装配期注入 ─────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        """`main_qml.register_bridges` 在注册前调它：取插件服务与任务调度器。"""
        self._context = context
        self._tasks = getattr(context, "tasks", None) if context is not None else None
        self._service = None
        if context is not None:
            try:
                self._service = context.try_get("plugin")
            except Exception as e:  # noqa: BLE001 - 取不到不该让页面打不开
                logger.warning("插件服务取用失败: %s", e)
        if self._service is not None:
            self._service.set_notify_handler(self._on_notify)
            self._service.set_ui_event_handler(self._on_ui_event)

    def use_engine(self, engine: Any) -> None:
        """缓存引擎（只存引用）。

        插件**不在这里自动初始化**：`register_bridges()` 是测试与探针也走的路径，
        自动扫描会把开发机上真实安装的插件拉进每个 UI 测试。生产装配在
        `main_qml.assemble(start_startup=True)` 里显式调 `initialize()`；页面首次
        进入也会调（幂等）。
        """
        self._engine = engine

    # ── 初始化 / 快照 ──────────────────────────────────────

    @Property(bool, constant=True)
    def available(self) -> bool:
        return self._service is not None

    @Property(bool, notify=stateChanged)
    def initialized(self) -> bool:
        return self._initialized

    @Property(str, notify=stateChanged)
    def initError(self) -> str:  # noqa: N802 - QML 属性名
        return self._init_error

    @Slot()
    def initialize(self) -> None:  # noqa: N802 - QML 槽名
        """建插件管理器并采集页面；幂等（页面每次进入都可以调）。"""
        if self._initialized or self._initializing:
            return
        if self._service is None:
            self._init_error = "not_ready"
            self.stateChanged.emit()
            self._status_error(ERROR_NOT_READY)
            return
        self._initializing = True
        try:
            ok = bool(self._service.initialize())
        except Exception as e:  # noqa: BLE001 - 初始化失败要能在界面上看见
            logger.error("插件初始化异常: %s", e, exc_info=True)
            ok = False
            self._init_error = str(e)
        finally:
            self._initializing = False
        if not ok:
            self._init_error = self._init_error or str(self._service.init_error or "not_ready")
            self.stateChanged.emit()
            self._status_error(ERROR_NOT_READY)
            return
        self._initialized = True
        self._register_pages()
        self._emit_snapshot()
        self.stateChanged.emit()

    @Property("QVariantList", notify=pluginsChanged)
    def plugins(self) -> List[Dict[str, Any]]:
        return self._plugins

    @Property("QVariantMap", notify=summaryChanged)
    def summary(self) -> Dict[str, int]:
        return self._summary

    @Property("QVariantList", notify=pagesChanged)
    def pages(self) -> List[Dict[str, Any]]:
        if self._service is None or not self._initialized:
            return []
        return self._service.pages

    @Property(int, notify=marketChanged)
    def updateCount(self) -> int:  # noqa: N802 - QML 属性名
        return sum(1 for item in self._plugins if item.get("has_update"))

    def _emit_snapshot(self) -> None:
        if self._service is not None and self._initialized and self._service.ready:
            self._plugins = self._service.plugins()
            self._summary = self._service.summary()
        else:
            self._plugins = []
            self._summary = {"enabled": 0, "total": 0, "pages": 0, "unsupported": 0}
        self._by_id = {item["id"]: item for item in self._plugins}
        self.pluginsChanged.emit()
        self.summaryChanged.emit()
        if self._market_loaded:
            self.marketChanged.emit()

    # ── 页面路由（v2） ─────────────────────────────────────

    def _nav(self) -> Any:
        if self._engine is not None:
            bridges = getattr(self._engine, "_fmcl_bridges", {}) or {}
            nav = bridges.get("Nav")
            if nav is not None:
                return nav
        try:
            return current_nav()
        except Exception:  # noqa: BLE001 - 没有 Nav 只是没有插件页
            return None

    def _safe_icon(self, name: Any) -> str:
        text = str(name or "").strip()
        if not text:
            return "plugin"
        try:
            if (qml_root() / "assets" / "icons" / f"{text}.svg").is_file():
                return text
        except Exception:  # noqa: BLE001 - 图标探测失败按兜底图标
            pass
        return "plugin"

    def _refresh_pages(self) -> None:
        """在主线程重新采集页面（安装/更新完成后由 on_done 调）。"""
        if self._service is None or not self._initialized:
            return
        try:
            self._service.refresh_pages()
        except Exception as e:  # noqa: BLE001 - 采集失败保留旧页面
            logger.warning("刷新插件页面表失败: %s", e)

    def _register_pages(self) -> None:
        if self._service is None or not self._initialized:
            return
        try:
            pages = self._service.pages
        except Exception as e:  # noqa: BLE001 - 页面表坏了不影响管理器
            logger.warning("读取插件页面表失败: %s", e)
            return
        signature = tuple(
            (p.get("id"), p.get("qml_path"), p.get("title"), p.get("icon"), p.get("order"))
            for p in pages
        )
        if signature == self._page_signature:
            return
        nav = self._nav()
        if nav is None:
            logger.warning("Nav 桥不可用，插件页面不会出现在导航里")
            return
        for owner in sorted(self._page_owners):
            try:
                nav.unregister_plugin_routes(owner)
            except Exception as e:  # noqa: BLE001 - 撤销失败继续登记
                logger.warning("撤销插件 %s 的页面路由失败: %s", owner, e)
        self._page_owners = set()
        for page in pages:
            try:
                url = QUrl.fromLocalFile(str(page["qml_path"])).toString()
                ok = nav.add_plugin_route(
                    str(page["id"]),
                    str(page["title"]),
                    url,
                    str(page["owner"]),
                    icon=self._safe_icon(page.get("icon")),
                    order=int(page.get("order") or 100),
                )
                if ok:
                    self._page_owners.add(str(page["owner"]))
            except Exception as e:  # noqa: BLE001 - 单页登记失败只影响那一页
                logger.warning("登记插件页面 %r 失败: %s", page.get("id"), e)
        self._page_signature = signature
        self.pagesChanged.emit()

    # ── 状态条 / 忙碌 ───────────────────────────────────────

    def _status_error(self, message: Any) -> None:
        text = str(message or "").strip()
        key = _ERROR_KEYS.get(text)
        if key:
            self.statusMessage.emit(_(key), "error")
            return
        self.statusMessage.emit(_("error") + ": " + (text or "?"), "error")

    def _begin_busy(self, text: str) -> None:
        self._busy = True
        self._busy_text = str(text or "")
        self._progress = -1.0
        self.busyChanged.emit()

    def _end_busy(self) -> None:
        self._busy = False
        self._busy_text = ""
        self._progress = -1.0
        self.busyChanged.emit()

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property(str, notify=busyChanged)
    def busyText(self) -> str:  # noqa: N802 - QML 属性名
        return self._busy_text

    @Property(float, notify=busyChanged)
    def progress(self) -> float:
        return self._progress

    def _on_progress(self, task_id: int = 0, name: str = "", current: int = 0, total: int = 0,
                     message: str = "", level: str = "info", title: str = "") -> None:
        if not self._busy:
            return
        try:
            self._progress = (float(current) / float(total)) if total else -1.0
        except (TypeError, ValueError):
            self._progress = -1.0
        label = self._busy_name or self._busy_text
        if total > 0:
            self._busy_text = _("plugin_market_downloading_progress", name=label, cur=current, total=total)
        self.busyChanged.emit()

    # ── 管理器操作 ─────────────────────────────────────────

    @Slot()
    def refreshPlugins(self) -> None:  # noqa: N802 - QML 槽名
        """重新扫描插件目录，然后异步检查更新（旧管理器的刷新语义）。"""
        if not self._initialized:
            self.initialize()
            return
        if self._busy:
            return
        try:
            self._service.rescan()
        except Exception as e:  # noqa: BLE001 - 扫描失败保留旧快照
            logger.error("刷新插件列表失败: %s", e)
            self._status_error(str(e))
            return
        self._register_pages()
        self._emit_snapshot()
        self.checkUpdates()

    @Slot()
    def checkUpdates(self) -> None:  # noqa: N802 - QML 槽名
        """异步检查市场更新（失败只报状态条，不改插件状态）。"""
        if not self._initialized or self._service is None or self._busy:
            return
        self._begin_busy(_("plugin_checking_updates"))
        self._busy_name = ""

        def work() -> Any:
            return self._service.check_updates()

        def done(info: Any) -> None:
            self._end_busy()
            self._emit_snapshot()
            pending = 0
            if isinstance(info, dict):
                pending = sum(1 for item in info.values() if item.get("has_update"))
            if pending:
                self.statusMessage.emit(_("plugin_market_updates_available", count=pending), "warning")
            if self._market_loaded:
                self.marketChanged.emit()

        def failed(error: Any) -> None:
            self._end_busy()
            self._status_error(error)

        self._submit(work, "plugin_updates", done, failed)

    @Slot(str, result="QVariantMap")
    def inspectPackage(self, path: str) -> Dict[str, Any]:  # noqa: N802 - QML 槽名
        """读 .fmpl 清单（安装前的权限确认用）。失败返回 ok=false + error 码。"""
        if self._service is None or not self._service.ready:
            return {"ok": False, "error": ERROR_NOT_READY}
        try:
            data, error = self._service.inspect_package(path)
        except Exception as e:  # noqa: BLE001 - 坏包不能把页面打崩
            logger.warning("读取插件包失败: %s", e)
            return {"ok": False, "error": str(e)}
        if error:
            return {"ok": False, "error": error}
        data["ok"] = True
        data["permission_groups"] = build_permission_groups(list(data.get("permissions") or []))
        return data

    @Slot(str, str)
    def installFromFile(self, path: str, pluginId: str) -> None:  # noqa: N802 - QML 槽名
        """从 .fmpl 安装并授权（权限弹窗已在 QML 侧确认过）。"""
        if not self._initialized or self._service is None or self._busy:
            return
        target = str(path or "").strip()
        pid = str(pluginId or "").strip()
        if not target or not pid:
            self._status_error(ERROR_INVALID_FMPL)
            return
        name = pid
        self._begin_busy(_("installing"))
        self._busy_name = name

        def work() -> Any:
            return self._service.install_from_file(target, pid)

        def done(result: Any) -> None:
            ok, message = result if isinstance(result, tuple) else (False, str(result))
            if not ok:
                self._end_busy()
                self._status_error(message)
                return
            try:
                self._service.grant_permissions(pid)
            except Exception as e:  # noqa: BLE001 - 授权失败仍按安装成功报告
                logger.warning("安装后授权失败 (%s): %s", pid, e)
            self._end_busy()
            self._refresh_pages()
            self._register_pages()
            self._emit_snapshot()
            display = self._by_id.get(pid, {}).get("name") or name
            self.statusMessage.emit(_("plugin_install_success", name=display), "success")

        def failed(error: Any) -> None:
            self._end_busy()
            self._status_error(error)

        self._submit(work, "plugin_install_file", done, failed)

    @Slot(str, result="QVariantList")
    def permissionGroups(self, pluginId: str) -> List[Dict[str, Any]]:  # noqa: N802 - QML 槽名
        if self._service is None or not self._initialized:
            return []
        try:
            return self._service.permission_groups(pluginId)
        except Exception as e:  # noqa: BLE001 - 弹窗数据失败按空表
            logger.warning("读取插件权限失败 (%s): %s", pluginId, e)
            return []

    @Slot(str, result=bool)
    def hasUngranted(self, pluginId: str) -> bool:  # noqa: N802 - QML 槽名
        if self._service is None or not self._initialized:
            return False
        try:
            return bool(self._service.has_ungranted(pluginId))
        except Exception:  # noqa: BLE001
            return False

    @Slot(str)
    def grantPermissions(self, pluginId: str) -> None:  # noqa: N802 - QML 槽名
        if self._service is None or not self._initialized:
            return
        try:
            ok, message = self._service.grant_permissions(pluginId)
        except Exception as e:  # noqa: BLE001
            self._status_error(e)
            return
        if ok:
            self.statusMessage.emit(_("plugin_permission_granted"), "success")
            self._emit_snapshot()
        else:
            self._status_error(message)

    @Slot(str, bool)
    def setEnabled(self, pluginId: str, enabled: bool) -> None:  # noqa: N802 - QML 槽名
        if self._service is None or not self._initialized:
            return
        name = self._by_id.get(pluginId, {}).get("name") or pluginId
        try:
            if enabled:
                ok, message = self._service.enable(pluginId)
            else:
                ok, message = self._service.disable(pluginId)
        except Exception as e:  # noqa: BLE001
            self._status_error(e)
            return
        if not ok:
            self._status_error(message)
            return
        self._register_pages()
        self._emit_snapshot()
        if enabled:
            self.statusMessage.emit(_("plugin_state_enabled") + ": " + name, "success")
        else:
            self.statusMessage.emit(_("plugin_state_disabled") + ": " + name, "success")

    @Slot(str)
    def uninstall(self, pluginId: str) -> None:  # noqa: N802 - QML 槽名
        if self._service is None or not self._initialized or self._busy:
            return
        name = self._by_id.get(pluginId, {}).get("name") or pluginId
        try:
            ok, message = self._service.uninstall(pluginId)
        except Exception as e:  # noqa: BLE001
            self._status_error(e)
            return
        if not ok:
            self._status_error(message)
            return
        self._register_pages()
        self._emit_snapshot()
        self.statusMessage.emit(_("plugin_uninstall_success", name=name), "success")

    # ── 市场 ────────────────────────────────────────────────

    @Property("QVariantList", notify=marketChanged)
    def marketItems(self) -> List[Dict[str, Any]]:  # noqa: N802 - QML 属性名
        return self._market_page_items()

    @Property(int, notify=marketChanged)
    def marketTotal(self) -> int:  # noqa: N802 - QML 属性名
        return len(self._market_filtered)

    @Property(int, notify=marketChanged)
    def marketPage(self) -> int:  # noqa: N802 - QML 属性名
        return self._market_page

    @Property(int, notify=marketChanged)
    def marketPageCount(self) -> int:  # noqa: N802 - QML 属性名
        return max(1, int(math.ceil(len(self._market_filtered) / float(MARKET_PAGE_SIZE))))

    @Property("QVariantList", notify=marketChanged)
    def marketTags(self) -> List[Dict[str, str]]:  # noqa: N802 - QML 属性名
        return [{"id": key, "label": value} for key, value in self._market_tags.items()]

    @Property(bool, notify=marketChanged)
    def marketLoading(self) -> bool:  # noqa: N802 - QML 属性名
        return self._market_loading

    @Property(str, notify=marketChanged)
    def marketError(self) -> str:  # noqa: N802 - QML 属性名
        return self._market_error

    @Property(bool, notify=marketChanged)
    def marketLoaded(self) -> bool:  # noqa: N802 - QML 属性名
        return self._market_loaded

    @Property(str, notify=marketChanged)
    def marketQuery(self) -> str:  # noqa: N802 - QML 属性名
        return self._market_query

    @Property(str, notify=marketChanged)
    def marketTag(self) -> str:  # noqa: N802 - QML 属性名
        return self._market_tag

    def _market_page_items(self) -> List[Dict[str, Any]]:
        start = (self._market_page - 1) * MARKET_PAGE_SIZE
        sliced = self._market_filtered[start:start + MARKET_PAGE_SIZE]
        return [self._decorate_market(item) for item in sliced]

    def _decorate_market(self, item: Dict[str, Any]) -> Dict[str, Any]:
        pid = str(item.get("id") or "")
        installed = False
        state = ""
        try:
            installed, state = self._service.market_lookup(pid) if self._service else (False, "")
        except Exception:  # noqa: BLE001 - 已装判定失败按未装展示
            installed, state = False, ""
        current = self._by_id.get(pid, {})
        update = current.get("has_update", False)
        out = dict(item)
        out["installed"] = bool(installed)
        out["installed_state"] = str(state or "")
        out["installed_version"] = str(current.get("version") or "")
        out["has_update"] = bool(update)
        out["latest_version"] = str(current.get("latest_version") or item.get("version") or "")
        permissions = list(item.get("permissions") or [])
        out["permission_summary"] = permission_summary(permissions)
        #: 安装前的权限弹窗数据：市场条目不是已装插件，不能走 permissionGroups(pid)。
        out["permission_groups"] = build_permission_groups(permissions)
        return out

    @Slot(bool)
    def loadMarket(self, force: bool) -> None:  # noqa: N802 - QML 槽名
        """拉取市场索引（force=True 忽略缓存 —— 修 M-Q7"点刷新没反应"）。"""
        if self._service is None:
            return
        if self._market_loading:
            return
        self._market_loading = True
        self._market_error = ""
        self.marketChanged.emit()

        def work() -> Any:
            return self._service.market_index(bool(force))

        def done(result: Any) -> None:
            items, error = (result if isinstance(result, tuple) else (result, ""))
            self._market_items = list(items or [])
            self._market_error = str(error or "")
            self._market_loading = False
            self._market_loaded = True
            try:
                self._market_tags = self._service.market_tags() if self._market_items else {}
            except Exception:  # noqa: BLE001 - 标签只是筛选辅助
                self._market_tags = {}
            self._market_page = 1
            self._apply_market_filter()
            self.marketChanged.emit()
            if self._market_error and not self._market_items:
                self._status_error(self._market_error)

        def failed(error: Any) -> None:
            self._market_loading = False
            self._market_error = str(error or "")
            self.marketChanged.emit()
            self._status_error(error)

        self._submit(work, "plugin_market_index", done, failed)

    def _apply_market_filter(self) -> None:
        if self._service is None:
            self._market_filtered = []
            return
        try:
            self._market_filtered = self._service.market_search(self._market_query, self._market_tag or None)
        except Exception as e:  # noqa: BLE001 - 过滤失败按无结果
            logger.warning("插件市场筛选失败: %s", e)
            self._market_filtered = []

    @Slot(str)
    def setMarketQuery(self, query: str) -> None:  # noqa: N802 - QML 槽名
        text = str(query or "")
        if text == self._market_query:
            return
        self._market_query = text
        self._apply_market_filter()
        self._market_page = 1
        self.marketChanged.emit()

    @Slot(str)
    def setMarketTag(self, tag: str) -> None:  # noqa: N802 - QML 槽名
        text = str(tag or "")
        if text == self._market_tag:
            return
        self._market_tag = text
        self._apply_market_filter()
        self._market_page = 1
        self.marketChanged.emit()

    @Slot(int)
    def setMarketPage(self, page: int) -> None:  # noqa: N802 - QML 槽名
        target = max(1, min(self.marketPageCount, int(page)))
        if target == self._market_page:
            return
        self._market_page = target
        self.marketChanged.emit()

    def _market_item(self, plugin_id: str) -> Optional[Dict[str, Any]]:
        for item in self._market_items:
            if str(item.get("id") or "") == plugin_id:
                return item
        return None

    @Slot(str)
    def installMarket(self, pluginId: str) -> None:  # noqa: N802 - QML 槽名
        if self._service is None or not self._initialized or self._busy:
            return
        item = self._market_item(pluginId)
        if item is None:
            self._status_error(ERROR_NO_MARKET)
            return
        name = str(item.get("name") or pluginId)
        permissions = list(item.get("permissions") or [])
        self._begin_busy(_("plugin_market_downloading", name=name))
        self._busy_name = name

        def work(ctx: Any) -> Any:
            return self._service.market_install(
                pluginId, permissions,
                lambda stage, cur, total: ctx.progress(current=cur, total=total, message=str(stage)),
            )

        def done(result: Any) -> None:
            ok, message = result if isinstance(result, tuple) else (False, str(result))
            self._end_busy()
            if not ok:
                self._status_error(message)
                return
            self._refresh_pages()
            self._register_pages()
            self._emit_snapshot()
            self.marketChanged.emit()
            self.statusMessage.emit(_("plugin_install_success", name=name), "success")

        def failed(error: Any) -> None:
            self._end_busy()
            self._status_error(error)

        self._submit(work, "plugin_market_install", done, failed, with_progress=True)

    @Slot(str)
    def updateMarket(self, pluginId: str) -> None:  # noqa: N802 - QML 槽名
        if self._service is None or not self._initialized or self._busy:
            return
        name = self._by_id.get(pluginId, {}).get("name") or pluginId
        self._begin_busy(_("plugin_updating_progress", name=name))
        self._busy_name = name

        def work(ctx: Any) -> Any:
            return self._service.market_update(
                pluginId,
                lambda stage, cur, total: ctx.progress(current=cur, total=total, message=str(stage)),
            )

        def done(result: Any) -> None:
            ok, message = result if isinstance(result, tuple) else (False, str(result))
            self._end_busy()
            if not ok:
                self._status_error(message)
                return
            self._refresh_pages()
            self._register_pages()
            self._emit_snapshot()
            self.marketChanged.emit()
            self.statusMessage.emit(_("plugin_update_success", name=name), "success")

        def failed(error: Any) -> None:
            self._end_busy()
            self._status_error(error)

        self._submit(work, "plugin_market_update", done, failed, with_progress=True)

    @Slot(str)
    def enableMarket(self, pluginId: str) -> None:  # noqa: N802 - QML 槽名
        if self._service is None or not self._initialized or self._busy:
            return
        try:
            ok, message = self._service.market_enable(pluginId)
        except Exception as e:  # noqa: BLE001
            self._status_error(e)
            return
        if not ok:
            self._status_error(message)
            return
        self._register_pages()
        self._emit_snapshot()
        self.marketChanged.emit()
        self.statusMessage.emit(_("plugin_state_enabled"), "success")

    # ── 数据通道 ───────────────────────────────────────────

    @Slot(str, str, result="QVariant")
    @Slot(str, str, "QVariant", result="QVariant")
    def call(self, pluginId: str, name: str, args: Any = None) -> Any:  # noqa: N802
        """调用插件导出的 API（v2 页面取数）。异常转成状态条错误并返回 null。

        例：`Plugins.call("com.example.plugin", "get_stats", {})`；无参也可以
        省略第三个参数（`Plugins.call(owner, "get_stats")`）。QML 的对象 / 数组
        会先还原成 dict / list。插件侧用
        `self._manager.export_api(id, "get_stats", fn)` 导出。
        """
        if self._service is None or not self._initialized:
            return None
        try:
            return self._service.call_api(pluginId, name, _unwrap_qml_value(args))
        except Exception as e:  # noqa: BLE001 - 页面取数失败只报错，不让 QML 抛
            logger.warning("插件 API 调用失败 (%s/%s): %s", pluginId, name, e)
            self._status_error(e)
            return None

    # ── 服务回调 ────────────────────────────────────────────

    def _on_notify(self, plugin_id: str, title: str, message: str, level: str) -> None:
        self.notified.emit(str(plugin_id), str(title), str(message), str(level))

    def _on_ui_event(self, plugin_id: str, name: str, payload: Any) -> None:
        self.pageEvent.emit(str(plugin_id), str(name), payload)

    # ── 任务 / 收尾 ─────────────────────────────────────────

    def _submit(self, work: Callable[[], Any], name: str, done: Callable[[Any], None],
                failed: Callable[[Any], None], with_progress: bool = False) -> None:
        if self._closing:
            return
        if self._tasks is None:
            try:
                done(work(_SyncTaskContext()) if with_progress else work())
            except Exception as e:  # noqa: BLE001 - 没调度器时退化成同步
                failed(e)
            return
        def guarded_done(result: Any) -> None:
            if not self._closing:
                done(result)

        def guarded_failed(error: Any) -> None:
            if not self._closing:
                failed(error)

        kwargs: Dict[str, Any] = {"name": name, "on_done": guarded_done, "on_error": guarded_failed}
        if with_progress:
            kwargs["on_progress"] = self._on_progress
            kwargs["pass_context"] = True
        try:
            handle = self._tasks.submit(work, **kwargs)
        except Exception as e:  # noqa: BLE001 - 提交失败也要收尾
            guarded_failed(e)
            return
        try:
            self._handles = [item for item in self._handles if not getattr(item, "done", None) or not item.done]
            self._handles.append(handle)
        except Exception:  # noqa: BLE001 - 句柄簿记失败不影响任务
            pass

    @Slot()
    def shutdown(self) -> None:  # noqa: N802 - QML/装配方调用
        """退出清理：发 APP_SHUTDOWN 并断开服务回调（对照表 A-20 第一步）。"""
        self._closing = True
        for handle in list(self._handles):
            try:
                handle.cancel()
            except Exception:  # noqa: BLE001 - 取消失败只是任务多跑一会儿
                pass
        self._handles.clear()
        if self._service is not None:
            try:
                self._service.set_notify_handler(None)
                self._service.set_ui_event_handler(None)
                self._service.stop()
            except Exception as e:  # noqa: BLE001 - 退出链不许被打断
                logger.warning("插件服务收尾失败: %s", e)

    def describe(self) -> Dict[str, Any]:
        return {
            "available": self.available,
            "initialized": self._initialized,
            "plugins": len(self._plugins),
            "pages": len(self._page_owners),
            "busy": self._busy,
            "market_loaded": self._market_loaded,
            "market_total": len(self._market_filtered),
        }


__all__ = ["MARKET_PAGE_SIZE", "PluginBridge"]
