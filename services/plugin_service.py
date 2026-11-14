"""插件服务 —— 插件管理与插件市场（阶段 3 任务 3.27）。

## 它管什么

- **生命周期**：按 `plugins/` 目录建 `PluginManager`、扫描、恢复启用状态、
  发 `APP_STARTUP`，退出时发 `APP_SHUTDOWN`（对照表 A-20）。
- **插件列表 / 摘要 / 权限分组**：管理器页与设置页要的**数据**（不含任何文案）。
- **安装 / 启停 / 卸载 / 授权**：从 .fmpl 安装、启用、禁用、卸载、按清单授权。
- **市场**：索引拉取、搜索 + 标签筛选、更新检查、安装 / 更新 / 启用 —— 复用
  `services.plugin_browser_service` 的编排，不重写一份。
- **UI API v2（3.27 新增）**：采集 `ui.page.register` 的页面声明、校验
  `qml_url` 落在插件目录内、把 v1（tkinter）界面插件标成"界面不受支持"。
- **页面数据通道**：`call_api` 调插件导出的 API，`UI 事件` 回调把插件推送转给桥。

## 它不管什么

不 import Qt / 不碰 QML / 不 import `ui.*`、不翻译任何文案（错误一律是
**机器码**或原始异常文本，由桥翻成人话）。页面状态（当前页签、搜索词、进度）
全在桥里；本模块只给纯数据与操作结果。

## 线程

`PluginManager` 自带可重入锁；安装/更新里的网络下载会长时间占住调用线程，
因此桥把它们放进 `TaskRunner`。**插件 Python 代码（on_enable 等）与插件页面
路由的采集都在主线程**——插件可能注册 v2 页面，桥需要在主线程拿到结果再登记
导航路由。
"""

from __future__ import annotations

import json
import logging
import threading
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from services.base import Service

logger = logging.getLogger("services.plugin")

#: 从文件安装时的错误码（桥据此选 i18n 键；服务里不出现任何界面文案）。
ERROR_INVALID_FMPL = "invalid_fmpl"
ERROR_INVALID_MANIFEST = "invalid_manifest"
ERROR_READ_FAILED = "read_failed"
ERROR_NOT_READY = "not_ready"
ERROR_NO_MARKET = "market_unavailable"

#: 页面 id 只允许"路径安全"的短标识（路由 id 由 owner + 它拼出来）。
_PAGE_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


def _as_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_manifest_from_fmpl(path: Path) -> Tuple[Dict[str, Any], str]:
    """读 .fmpl（zip）里的 plugin.json。返回 `(data, error_code)`。"""
    if not path.is_file():
        return {}, ERROR_READ_FAILED
    try:
        with zipfile.ZipFile(path, "r") as archive:
            if "plugin.json" not in archive.namelist():
                return {}, ERROR_INVALID_FMPL
            data = json.loads(archive.read("plugin.json").decode("utf-8"))
    except (zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError, OSError):
        return {}, ERROR_INVALID_FMPL
    if not isinstance(data, dict):
        return {}, ERROR_INVALID_MANIFEST
    if not str(data.get("id") or "").strip():
        return {}, ERROR_INVALID_MANIFEST
    return data, ""


def permission_details(permissions: List[str]) -> List[Dict[str, Any]]:
    """权限字符串 → 展示用条目（risk 由 `plugin_manager.permissions` 判定）。

    返回顺序：高 → 中 → 低；同档按权限名排序。未知权限归"低"（与旧卡片的
    扣减法口径一致），label_key 回退成 `plugin_permission_<id>`。
    """
    from plugin_manager.permissions import (
        PermissionRiskLevel,
        PluginPermission,
        get_permission_display_key,
        get_permission_risk,
    )

    order = {PermissionRiskLevel.HIGH: 0, PermissionRiskLevel.MEDIUM: 1, PermissionRiskLevel.LOW: 2}
    items: List[Tuple[int, str, Dict[str, Any]]] = []
    seen = set()
    for raw in permissions:
        text = str(raw or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        try:
            perm = PluginPermission(text)
            label_key = get_permission_display_key(perm)
            risk = get_permission_risk(perm).value
            rank = order.get(get_permission_risk(perm), 2)
        except ValueError:
            label_key = f"plugin_permission_{text}"
            risk = PermissionRiskLevel.LOW.value
            rank = 2
        items.append((rank, text, {"id": text, "label_key": label_key, "risk": risk}))
    items.sort(key=lambda entry: (entry[0], entry[1]))
    return [entry[2] for entry in items]


def permission_groups(permissions: List[str], granted: Optional[Dict[str, bool]] = None) -> List[Dict[str, Any]]:
    """权限条目 → 按风险分组的展示结构（权限确认弹窗用）。

    `granted` 是 `{权限名: 是否已授权}`；不给就全部按未授权展示（安装前）。
    """
    flags = granted or {}
    groups: List[Dict[str, Any]] = []
    for risk, label_key in (
        ("high", "plugin_permission_risk_high"),
        ("medium", "plugin_permission_risk_medium"),
        ("low", "plugin_permission_risk_low"),
    ):
        items = []
        for item in permission_details(permissions):
            if item["risk"] != risk:
                continue
            entry = dict(item)
            entry["granted"] = bool(flags.get(item["id"], False))
            items.append(entry)
        if items:
            groups.append({"risk": risk, "label_key": label_key, "items": items})
    return groups


class PluginService(Service):
    """插件服务。QML 名 `Plugins`（桥）背后的全部业务。"""

    name = "plugin"
    label = "插件"

    def __init__(self, context: Any = None, plugins_root: Optional[Path] = None) -> None:
        super().__init__(context)
        self._root = Path(plugins_root) if plugins_root is not None else None
        self._lock = threading.RLock()
        self._manager: Any = None
        self._ready = False
        self._init_error = ""
        self._pages: List[Dict[str, Any]] = []
        self._page_errors: List[Dict[str, str]] = []
        self._unsupported: List[str] = []
        self._update_info: Dict[str, dict] = {}
        self._notify_handler: Optional[Callable[[str, str, str, str], None]] = None
        self._ui_event_handler: Optional[Callable[[str, str, Any], None]] = None

    # ── 生命周期 ─────────────────────────────────────────────

    def start(self) -> None:
        """只标记启动。**不在 `start_all()` 里扫描插件**。

        服务表是在建 QML 引擎之前 `start_all()` 的，那时窗口还没出来；扫描要
        import 各插件模块、还可能触发插件自己的 on_enable，放在那里等于让启动
        画面等一长段"看不见的工作"。真正的初始化由桥在事件循环起来后调用
        :meth:`initialize`（见 `app/bridges/plugin_bridge.py`）。
        """
        self._started = True

    def stop(self) -> None:
        """退出清理：发 `APP_SHUTDOWN`（对照表 A-20 的第一步）。

        不在这里 `disable_plugin` 全部插件：旧实现也不这么做，插件自己监听
        shutdown 钩子收尾；强制禁用会让"退出前落盘"变成另一条路径。
        """
        manager = self._manager
        if manager is not None:
            try:
                from plugin_manager.base import HookPoint

                manager.emit(HookPoint.APP_SHUTDOWN)
            except Exception as e:  # noqa: BLE001 - 退出链不许被打断
                logger.warning("插件 APP_SHUTDOWN 钩子失败: %s", e)
        self._started = False

    def initialize(self) -> bool:
        """建 `PluginManager` → 扫描 → APP_STARTUP → 采集 v2 页面。幂等。"""
        with self._lock:
            if self._ready:
                return True
            try:
                from plugin_manager import PluginManager
                from plugin_manager.base import HookPoint

                root = self._root if self._root is not None else Path(self.config.base_dir) / "plugins"
                manager = PluginManager(root)
                manager.set_notify_callback(self._on_notify)
                manager.set_ui_event_callback(self._on_ui_event)
                manager.scan()
                manager.emit(HookPoint.APP_STARTUP)
                self._manager = manager
                self._ready = True
                self._init_error = ""
            except Exception as e:  # noqa: BLE001 - 插件系统坏了也要能进界面
                self._init_error = f"{type(e).__name__}: {e}"
                logger.error("插件系统初始化失败: %s", e, exc_info=True)
                return False
        self.refresh_pages()
        logger.info("插件系统已初始化：%s 个插件", len(self.plugins()))
        return True

    # ── 查询 ────────────────────────────────────────────────

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def init_error(self) -> str:
        return self._init_error

    @property
    def manager(self) -> Any:
        return self._manager

    @property
    def pages(self) -> List[Dict[str, Any]]:
        return [dict(page) for page in self._pages]

    @property
    def unsupported_plugins(self) -> List[str]:
        return list(self._unsupported)

    @property
    def page_errors(self) -> List[Dict[str, str]]:
        return [dict(item) for item in self._page_errors]

    def summary(self) -> Dict[str, int]:
        if not self._ready:
            return {"enabled": 0, "total": 0, "pages": 0, "unsupported": 0}
        plugins = self.plugins()
        return {
            "enabled": sum(1 for item in plugins if item["state"] in ("enabled", "running")),
            "total": len(plugins),
            "pages": len(self._pages),
            "unsupported": len(self._unsupported),
        }

    def plugins(self) -> List[Dict[str, Any]]:
        """管理器页的插件卡片数据（描述按语言给整份字典，QML 自己选）。"""
        manager = self._manager
        if not self._ready or manager is None:
            return []
        meta = manager.get_all_plugin_meta()
        page_counts: Dict[str, int] = {}
        for page in self._pages:
            page_counts[page["owner"]] = page_counts.get(page["owner"], 0) + 1
        out: List[Dict[str, Any]] = []
        for pid, info in meta.items():
            manifest = info.get("manifest") or {}
            update = self._update_info.get(pid) or {}
            permissions = list(manifest.get("permissions") or [])
            out.append(
                {
                    "id": pid,
                    "name": str(manifest.get("name") or pid),
                    "version": str(manifest.get("version") or ""),
                    "author": str(manifest.get("author") or ""),
                    "description": manifest.get("description") or {},
                    "state": str(info.get("state") or "scanned"),
                    "error": str(info.get("error") or ""),
                    "permissions": permissions,
                    "permission_details": permission_details(permissions),
                    "ui_api": manager.get_ui_api(pid),
                    "ui_pages": page_counts.get(pid, 0),
                    "ui_unsupported": pid in self._unsupported,
                    "has_update": bool(update.get("has_update")),
                    "latest_version": str(update.get("latest") or ""),
                }
            )
        out.sort(key=lambda item: (item["name"].lower(), item["id"]))
        return out

    def permission_groups(self, plugin_id: str) -> List[Dict[str, Any]]:
        """某个插件声明的权限（带已授权标记），供权限弹窗/管理使用。"""
        manager = self._manager
        if not self._ready or manager is None:
            return []
        manifest = manager.get_manifest(plugin_id)
        if manifest is None:
            return []
        state = manager.get_permission_state(plugin_id)
        flags: Dict[str, bool] = {}
        if state is not None:
            for item in permission_details(list(manifest.permissions)):
                try:
                    from plugin_manager.permissions import PluginPermission

                    flags[item["id"]] = state.is_granted(PluginPermission(item["id"]))
                except ValueError:
                    continue
        return permission_groups(list(manifest.permissions), flags)

    def has_ungranted(self, plugin_id: str) -> bool:
        """清单里声明的权限是否还有未授权的。

        只判**声明过的**权限：权限状态会给全部 12 条权限都建条目，用
        `get_ungranted_permissions()` 会把没声明的也算进来 —— 任何插件都会被
        判成"需要再确认一次"。也没有状态（全新安装）时按"全部未授权"处理。
        """
        manager = self._manager
        if not self._ready or manager is None:
            return False
        manifest = manager.get_manifest(plugin_id)
        if manifest is None:
            return False
        from plugin_manager.permissions import PluginPermission

        state = manager.get_permission_state(plugin_id)
        for raw in list(manifest.permissions):
            try:
                perm = PluginPermission(str(raw))
            except ValueError:
                continue
            if state is None or not state.is_granted(perm):
                return True
        return False

    # ── 安装 / 启停 / 卸载 / 授权 ────────────────────────────

    def inspect_package(self, path: str) -> Tuple[Dict[str, Any], str]:
        data, error = _read_manifest_from_fmpl(Path(path))
        if error:
            return {}, error
        return {
            "id": str(data.get("id") or ""),
            "name": str(data.get("name") or data.get("id") or ""),
            "version": str(data.get("version") or ""),
            "author": str(data.get("author") or ""),
            "permissions": list(data.get("permissions") or []),
            "ui_api": _as_int(data.get("ui_api"), 1),
            "description": data.get("description") or {},
            "path": str(path),
        }, ""

    def rescan(self) -> List[Dict[str, Any]]:
        """重新扫描 `plugins/installed/`（管理器"刷新"按钮）并刷新页面表。

        `scan()` 只读清单、不 import 插件模块，因此在主线程调用是安全的；
        它会按持久化状态恢复启用（与启动时同一套逻辑）。
        """
        manager = self._manager
        if not self._ready or manager is None:
            return []
        try:
            manager.scan()
        except Exception as e:  # noqa: BLE001 - 刷新失败保留旧列表，状态条报错
            logger.error("重新扫描插件失败: %s", e)
            raise
        self.refresh_pages()
        return self.plugins()

    def install_from_file(self, path: str, plugin_id: str) -> Tuple[bool, str]:
        manager = self._manager
        if not self._ready or manager is None:
            return False, ERROR_NOT_READY
        # 页面表**不在这里**刷新：安装可能跑在 worker 线程，而页面声明回调
        # （ui.page.register）属于插件代码，必须在主线程触发。桥在 on_done
        # 里调 refresh_pages()（3.27 的线程契约，见模块头）。
        ok, message = manager.install_from_file(str(path), str(plugin_id))
        return bool(ok), str(message or "")

    def grant_permissions(self, plugin_id: str) -> Tuple[bool, str]:
        """按清单逐项授权（含高风险，`always=True`）——用户已在弹窗里整体批准。

        旧实现只调 `grant_manifest_permissions`，高风险权限永远拿不到，带
        `core.launch_hook` 的插件（例如游戏时长统计）装完启用会失败。这里把
        "用户点过批准"这个事实显式落成授权，修掉那条链路。
        """
        manager = self._manager
        if not self._ready or manager is None:
            return False, ERROR_NOT_READY
        manifest = manager.get_manifest(plugin_id)
        if manifest is None:
            return False, "plugin_not_found"
        from plugin_manager.permissions import PermissionRiskLevel, PluginPermission, get_permission_risk

        granted = 0
        for raw in list(manifest.permissions):
            try:
                perm = PluginPermission(str(raw))
            except ValueError:
                logger.warning("插件 %s 声明了未知权限 %r，已跳过", plugin_id, raw)
                continue
            manager.grant_permission(plugin_id, perm, always=(get_permission_risk(perm) is PermissionRiskLevel.HIGH))
            granted += 1
        return True, ""

    def enable(self, plugin_id: str) -> Tuple[bool, str]:
        manager = self._manager
        if not self._ready or manager is None:
            return False, ERROR_NOT_READY
        ok, message = manager.enable_plugin(plugin_id)
        if ok:
            self.refresh_pages()
        return bool(ok), str(message or "")

    def disable(self, plugin_id: str) -> Tuple[bool, str]:
        manager = self._manager
        if not self._ready or manager is None:
            return False, ERROR_NOT_READY
        ok, message = manager.disable_plugin(plugin_id)
        if ok:
            self.refresh_pages()
        return bool(ok), str(message or "")

    def uninstall(self, plugin_id: str) -> Tuple[bool, str]:
        manager = self._manager
        if not self._ready or manager is None:
            return False, ERROR_NOT_READY
        state = manager.get_plugin_state(plugin_id)
        if state is not None and state.value in ("enabled", "running"):
            manager.disable_plugin(plugin_id)
        ok, message = manager.uninstall_plugin(plugin_id)
        if ok:
            self.refresh_pages()
        return bool(ok), str(message or "")

    # ── 市场 ────────────────────────────────────────────────

    def market(self) -> Any:
        manager = self._manager
        if not self._ready or manager is None:
            return None
        try:
            return manager.init_market()
        except Exception as e:  # noqa: BLE001 - 市场不可用只影响市场页
            logger.warning("插件市场初始化失败: %s", e)
            return None

    def market_available(self) -> bool:
        return self.market() is not None

    def market_index(self, force: bool = False) -> Tuple[List[dict], str]:
        market = self.market()
        if market is None:
            return [], ERROR_NO_MARKET
        from services.plugin_browser_service import fetch_market_index

        return fetch_market_index(market, force)

    def market_tags(self) -> Dict[str, str]:
        market = self.market()
        if market is None:
            return {}
        from services.plugin_browser_service import available_tags

        return available_tags(market)

    def market_search(self, query: str, tag: Optional[str]) -> List[dict]:
        market = self.market()
        if market is None:
            return []
        from services.plugin_browser_service import filter_plugins

        return filter_plugins(market, query, tag)

    def check_updates(self) -> Dict[str, dict]:
        manager = self._manager
        market = self.market()
        if not self._ready or manager is None or market is None:
            self._update_info = {}
            return {}
        from services.plugin_browser_service import check_market_updates

        try:
            self._update_info = check_market_updates(manager, market) or {}
        except Exception as e:  # noqa: BLE001 - 更新检查失败不该挡住插件管理
            logger.warning("检查插件更新失败: %s", e)
            self._update_info = {}
        return dict(self._update_info)

    def market_install(
        self,
        plugin_id: str,
        permissions: List[str],
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Tuple[bool, str]:
        manager = self._manager
        market = self.market()
        if not self._ready or manager is None:
            return False, ERROR_NOT_READY
        if market is None:
            return False, ERROR_NO_MARKET
        from services.plugin_browser_service import install_plugin

        # 同 install_from_file：不在 worker 里采集页面，桥在 on_done 里做。
        ok, message = install_plugin(
            manager,
            market,
            plugin_id,
            list(permissions or []),
            progress_callback=progress_callback,
            grant_callback=self.grant_permissions,
        )
        return bool(ok), str(message or "")

    def market_update(
        self,
        plugin_id: str,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Tuple[bool, str]:
        manager = self._manager
        if not self._ready or manager is None:
            return False, ERROR_NOT_READY
        from services.plugin_browser_service import update_plugin_from_market

        ok, message = update_plugin_from_market(manager, plugin_id, progress_callback)
        if ok:
            self._update_info.pop(plugin_id, None)
        # 页面采集同样留给主线程（见 install_from_file 的说明）。
        return bool(ok), str(message or "")

    def market_enable(self, plugin_id: str) -> Tuple[bool, str]:
        manager = self._manager
        if not self._ready or manager is None:
            return False, ERROR_NOT_READY
        from services.plugin_browser_service import enable_installed_plugin

        ok, message = enable_installed_plugin(manager, plugin_id, before_enable=self.grant_permissions)
        if ok:
            self.refresh_pages()
        return bool(ok), str(message or "")

    def market_lookup(self, plugin_id: str) -> Tuple[bool, str]:
        manager = self._manager
        if not self._ready or manager is None:
            return False, ""
        from services.plugin_browser_service import installed_lookup

        return installed_lookup(manager, plugin_id)

    # ── v2 页面 / 数据通道 ──────────────────────────────────

    def refresh_pages(self) -> List[Dict[str, Any]]:
        """重新采集 v2 页面声明（启用/禁用/安装后调用）。返回新页面表。"""
        manager = self._manager
        if not self._ready or manager is None:
            self._pages = []
            self._unsupported = []
            self._page_errors = []
            return []
        from plugin_manager.base import HookPoint
        from plugin_manager.permissions import PluginPermission

        pages: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []
        legacy: List[str] = []
        meta = manager.get_all_plugin_meta()
        for pid, info in meta.items():
            manifest = info.get("manifest") or {}
            # 判定一：清单声明 ui_api<2 且申请了 ui.extend —— 即使当前没启用也能
            # 断定它的界面用的是旧 Tk 协议（不必等 on_enable 注册钩子才发现）。
            permissions = list(manifest.get("permissions") or [])
            if manager.get_ui_api(pid) < 2 and "ui.extend" in permissions:
                legacy.append(pid)
                continue
            # 判定二：已启用的插件确实注册了 v1 的 UI 钩子（清单可能没写 ui.extend）。
            try:
                if manager.has_legacy_ui(pid):
                    legacy.append(pid)
            except Exception as e:  # noqa: BLE001 - 单个插件判断失败只记日志
                logger.warning("读取插件 %s 的 UI 扩展状态失败: %s", pid, e)
        try:
            registrations = manager.emit(HookPoint.UI_PAGE_REGISTER) or []
        except Exception as e:  # noqa: BLE001 - 采集失败按"没有页面"处理
            logger.error("采集插件页面失败: %s", e)
            registrations = []
        for pid, payload in registrations:
            info = meta.get(pid) or {}
            ui_api = manager.get_ui_api(pid)
            if ui_api < 2:
                errors.append({"owner": pid, "reason": "ui_api_legacy"})
                continue
            if manager.check_permission(pid, PluginPermission.UI_EXTEND) != "granted":
                errors.append({"owner": pid, "reason": "ui_extend_not_granted"})
                continue
            entries = payload if isinstance(payload, (list, tuple)) else [payload]
            for entry in entries:
                page, reason = self._normalize_page(pid, entry)
                if page is None:
                    errors.append({"owner": pid, "reason": reason or "invalid_page"})
                else:
                    pages.append(page)
        owners = {page["owner"] for page in pages}
        unsupported = sorted(set(legacy) - owners)
        self._pages = pages
        self._page_errors = errors
        self._unsupported = unsupported
        return [dict(page) for page in pages]

    def _normalize_page(self, plugin_id: str, entry: Any) -> Tuple[Optional[Dict[str, Any]], str]:
        manager = self._manager
        manifest = manager.get_manifest(plugin_id) if manager is not None else None
        if manifest is None:
            return None, "plugin_not_found"
        if not isinstance(entry, dict):
            return None, "page_not_dict"
        local_id = str(entry.get("id") or "").strip()
        if not local_id or len(local_id) > 64 or any(ch not in _PAGE_ID_CHARS for ch in local_id):
            return None, "invalid_page_id"
        root = Path(manifest.install_path).resolve() if manifest.install_path else None
        if root is None or not root.is_dir():
            return None, "plugin_dir_missing"
        raw = entry.get("qml_url", entry.get("qml", ""))
        raw_text = str(raw or "").strip()
        if not raw_text:
            return None, "missing_qml_url"
        path = self._resolve_qml_path(raw, raw_text, root)
        if path is None:
            return None, "invalid_qml_url"
        # 顺序要紧：先判"在插件目录内"，再判存在 —— 顺序反了会先去 stat
        # 一个插件目录外的路径（路径来自插件代码，不该获得探测文件系统的能力）。
        if root not in path.parents:
            return None, "qml_outside_plugin"
        if path.suffix.lower() != ".qml" or not path.is_file():
            return None, "qml_not_found"
        title = str(entry.get("title") or manifest.name or local_id).strip()[:64] or local_id
        return {
            "owner": plugin_id,
            "id": f"plugin/{plugin_id}/{local_id}",
            "local_id": local_id,
            "title": title,
            "icon": str(entry.get("icon") or "").strip(),
            "order": _as_int(entry.get("order"), 100),
            "qml_path": str(path),
            "plugin_dir": str(root),
        }, ""

    @staticmethod
    def _resolve_qml_path(raw: Any, raw_text: str, root: Path) -> Optional[Path]:
        if isinstance(raw, Path) or not raw_text.startswith("file:"):
            candidate = Path(raw_text)
            if candidate.is_absolute():
                return candidate.resolve()
            return (root / candidate).resolve()
        parsed = urlparse(raw_text)
        target = unquote(parsed.path or "")
        if target.startswith("/") and len(target) > 2 and target[2] == ":":
            target = target[1:]
        if not target:
            return None
        return Path(target).resolve()

    def call_api(self, plugin_id: str, name: str, args: Any = None) -> Any:
        """调用插件导出的 API（v2 页面取数通道）。

        `args` 为 dict → 关键字参数；list/tuple → 位置参数；None → 无参。
        异常原样抛给桥（桥会转成状态条错误并记日志）。
        """
        manager = self._manager
        if not self._ready or manager is None:
            raise RuntimeError(ERROR_NOT_READY)
        api = manager.get_plugin_api(plugin_id, str(name))
        if not callable(api):
            raise LookupError(f"插件 {plugin_id} 未导出 API: {name!r}")
        if args is None:
            return api()
        if isinstance(args, dict):
            return api(**args)
        if isinstance(args, (list, tuple)):
            return api(*args)
        return api(args)

    def set_notify_handler(self, handler: Optional[Callable[[str, str, str, str], None]]) -> None:
        self._notify_handler = handler

    def set_ui_event_handler(self, handler: Optional[Callable[[str, str, Any], None]]) -> None:
        self._ui_event_handler = handler

    def _on_notify(self, plugin_id: str, title: str, message: str, level: str) -> None:
        handler = self._notify_handler
        if handler is None:
            return
        try:
            handler(plugin_id, title, message, level)
        except Exception as e:  # noqa: BLE001 - 通知失败不打断插件
            logger.warning("插件通知回调失败 (%s): %s", plugin_id, e)

    def _on_ui_event(self, plugin_id: str, name: str, payload: Any) -> None:
        handler = self._ui_event_handler
        if handler is None:
            return
        try:
            handler(plugin_id, name, payload)
        except Exception as e:  # noqa: BLE001 - 页面事件失败不打断插件
            logger.warning("插件页面事件回调失败 (%s/%s): %s", plugin_id, name, e)


__all__ = [
    "ERROR_INVALID_FMPL",
    "ERROR_INVALID_MANIFEST",
    "ERROR_NO_MARKET",
    "ERROR_NOT_READY",
    "ERROR_READ_FAILED",
    "PluginService",
    "permission_details",
    "permission_groups",
]
