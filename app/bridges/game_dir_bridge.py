"""游戏目录桥 —— QML 侧的 `GameDirs` 上下文属性。

## 它是什么

设置页的「游戏目录」分区与顶栏的目录选择器都读这一个桥：

* 列举内置（程序安装目录、官方启动器目录）与用户添加的目录；
* 添加 / 重命名 / 移除 / 切换；
* 切换成功后通知**依赖当前目录**的页面桥刷新（首页、版本、资源、服务器、存档、
  基岩版、在线浏览），避免用户看到上一个目录的数据。

## 分工（与仓库其他桥一致）

业务规则全在 `services/game_dir_service.py`；本桥只做四件事：
把服务返回值变成 QML 属性/信号、翻译 i18n 键、把 QML 动作转成服务调用、
在切换后广播刷新。**一句业务规则都不写**。

## 线程

所有槽都由 QML 主线程调用；服务方法同步且短时。`Tr.languageChanged` 也由
主线程发出（语言切换只改内存字典），因此这里的刷新不需要跨线程调度。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot

from services.i18n_service import _
from services.game_dir_service import GameDirService

logger = logging.getLogger("app.bridges.game_dir_bridge")

#: 切换目录后需要重读数据的桥（名字与 `main_qml.CONTEXT_BRIDGES` 一致）。
DEPENDENT_BRIDGES = (
    "Home",
    "Versions",
    "Resources",
    "Servers",
    "Backups",
    "Bedrock",
    "ModBrowser",
)


class GameDirBridge(QObject):
    """上下文属性 `GameDirs`。"""

    #: 目录列表（名称/路径/是否当前）变了。
    dirsChanged = Signal()
    #: 当前目录变了（顶栏选择器据此复位）。
    activeChanged = Signal()
    #: 状态条文案（**已翻译**；由 QML 转给 `Shell.setStatus`）。
    statusMessage = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._service: Optional[GameDirService] = None
        self._engine: Any = None
        self._dirs: List[Dict[str, Any]] = []
        self._active_id = ""
        self._active_path = ""
        self._active_name = ""

    # ─── 装配 ───────────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        if context is None:
            return
        try:
            self._service = context.try_get("game_dir")
        except Exception as e:  # noqa: BLE001 - 取不到服务时页面走空列表，不崩
            logger.warning("取游戏目录服务失败: %s", e)
            self._service = None
        self.refresh()

    def use_engine(self, engine: Any) -> None:
        """记下引擎（切换后按名字找依赖桥广播刷新），并接上语言切换刷新。"""
        if engine is not None:
            self._engine = engine
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        tr = registry.get("Tr")
        signal = getattr(tr, "languageChanged", None) if tr is not None else None
        if signal is not None:
            try:
                signal.connect(self._on_language_changed)
            except Exception as e:  # noqa: BLE001 - 接不上只会让内置目录名短暂不跟随语言
                logger.warning("接 Tr.languageChanged 失败: %s", e)

    # ─── 只读属性 ───────────────────────────────────────────

    @Property(bool, constant=True)
    def available(self) -> bool:
        return self._service is not None

    @Property("QVariantList", notify=dirsChanged)
    def dirs(self) -> List[Dict[str, Any]]:
        return [dict(row) for row in self._dirs]

    @Property("QVariantList", notify=dirsChanged)
    def dirChoices(self) -> List[Dict[str, Any]]:
        """顶栏下拉的轻量模型（只有 id 与显示名）。"""
        return [{"id": row["id"], "name": row["name"], "path": row["path"]} for row in self._dirs]

    @Property(str, notify=activeChanged)
    def activeId(self) -> str:  # noqa: N802
        return self._active_id

    @Property(str, notify=activeChanged)
    def activePath(self) -> str:  # noqa: N802
        return self._active_path

    @Property(str, notify=activeChanged)
    def activeName(self) -> str:  # noqa: N802
        return self._active_name

    # ─── 槽：刷新与操作 ─────────────────────────────────────

    @Slot(result=bool)
    def refresh(self) -> bool:  # noqa: N802
        if self._service is None:
            self._dirs = []
            self._active_id = ""
            self._active_path = ""
            self._active_name = ""
            self.dirsChanged.emit()
            self.activeChanged.emit()
            return False
        try:
            rows = self._service.list_dirs()
        except Exception as e:  # noqa: BLE001
            logger.error("读取游戏目录列表失败: %s", e)
            rows = []
        decorated = [self._decorate(row) for row in rows]
        active = next((row for row in decorated if row["active"]), None)
        self._dirs = decorated
        self._active_id = str(active["id"]) if active else ""
        self._active_path = str(active["path"]) if active else ""
        self._active_name = str(active["name"]) if active else ""
        self.dirsChanged.emit()
        self.activeChanged.emit()
        return True

    @Slot(str, str, result="QVariantMap")
    def add(self, name: str, path: str) -> Dict[str, Any]:
        if self._service is None:
            return self._failure("game_dir_service_missing")
        result = self._service.add(name, path)
        if result.get("ok"):
            self.refresh()
            self.statusMessage.emit(_("game_dir_add_ok", name=self._label_of(result.get("entry"))), "success")
            return {"ok": True, "error": ""}
        return self._failure_result(result)

    @Slot(str, str, result="QVariantMap")
    def rename(self, dirId: str, name: str) -> Dict[str, Any]:
        if self._service is None:
            return self._failure("game_dir_service_missing")
        result = self._service.rename(dirId, name)
        if result.get("ok"):
            self.refresh()
            self.statusMessage.emit(_("game_dir_rename_ok", name=self._label_of(result.get("entry"))), "success")
            return {"ok": True, "error": ""}
        return self._failure_result(result)

    @Slot(str, result="QVariantMap")
    def remove(self, dirId: str) -> Dict[str, Any]:
        if self._service is None:
            return self._failure("game_dir_service_missing")
        row = self._row(dirId)
        result = self._service.remove(dirId)
        if result.get("ok"):
            self.refresh()
            name = str(row.get("name") or "") if row else ""
            self.statusMessage.emit(_("game_dir_remove_ok", name=name), "success")
            if result.get("was_active"):
                self._notify_dependents()
            return {"ok": True, "error": ""}
        return self._failure_result(result)

    @Slot(str, result=bool)
    def activate(self, dirId: str) -> bool:  # noqa: N802
        if self._service is None:
            self.statusMessage.emit(_("game_dir_service_missing"), "error")
            return False
        row = self._row(dirId)
        result = self._service.activate(dirId)
        if result.get("ok"):
            self.refresh()
            if result.get("changed"):
                self._notify_dependents()
                self.statusMessage.emit(_("game_dir_switch_ok", name=self._label_of(row)), "success")
            return True
        self.statusMessage.emit(self._error_text(result), "error")
        # 失败也要发一次：顶栏下拉已经把显示值改成了用户选的那一项，
        # 不重算绑定就会停在"看得见但没生效"的假状态。
        self.activeChanged.emit()
        return False

    @Slot(str, result=str)
    def fileUrl(self, path: str) -> str:  # noqa: N802
        """目录路径 → file:// URL（QML 用 Qt.openUrlExternally 打开）。"""
        text = str(path or "").strip()
        if not text:
            return ""
        try:
            return QUrl.fromLocalFile(text).toString()
        except Exception as e:  # noqa: BLE001 - 非法路径只让"打开"失效
            logger.warning("转换目录 URL 失败 (%s): %s", text, e)
            return ""

    @Slot(str, result=str)
    def localPath(self, url: str) -> str:  # noqa: N802
        """file:// URL → 本地路径（FolderDialog.selectedFolder 用）。"""
        text = str(url or "").strip()
        if not text:
            return ""
        try:
            local = QUrl(text).toLocalFile()
        except Exception as e:  # noqa: BLE001
            logger.warning("转换文件 URL 失败 (%s): %s", text, e)
            return ""
        return local or ""

    # ─── 内部：装饰与广播 ───────────────────────────────────

    def _decorate(self, row: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(row)
        item["name"] = self._label_of(item)
        item["kindLabel"] = _(str(item.get("kindKey") or "game_dir_kind_custom"))
        item["subtitle"] = str(item.get("path") or "")
        return item

    @staticmethod
    def _label_of(row: Optional[Dict[str, Any]]) -> str:
        if not row:
            return ""
        key = str(row.get("nameKey") or "")
        if key:
            return _(key)
        return str(row.get("name") or row.get("path") or "")

    def _row(self, dir_id: Any) -> Optional[Dict[str, Any]]:
        wanted = str(dir_id or "").strip()
        for row in self._dirs:
            if row["id"] == wanted:
                return row
        return None

    def _failure_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        text = self._error_text(result)
        self.statusMessage.emit(text, "error")
        return {"ok": False, "error": text}

    @staticmethod
    def _failure(key: str) -> Dict[str, Any]:
        return {"ok": False, "error": _(key)}

    @staticmethod
    def _error_text(result: Dict[str, Any]) -> str:
        key = str(result.get("error_key") or "")
        if not key:
            return ""
        params = result.get("error_params") or {}
        try:
            return _(key, **params)
        except Exception:  # noqa: BLE001 - 参数不匹配时退回键名，至少不是空白
            return key

    def _notify_dependents(self) -> None:
        registry = getattr(self._engine, "_fmcl_bridges", None) if self._engine is not None else None
        if not isinstance(registry, dict):
            return
        for name in DEPENDENT_BRIDGES:
            bridge = registry.get(name)
            handler = getattr(bridge, "onGameDirChanged", None) if bridge is not None else None
            if not callable(handler):
                continue
            try:
                handler()
            except Exception as e:  # noqa: BLE001 - 单个页面刷新失败不该影响切换本身
                logger.warning("通知 %s 刷新游戏目录失败: %s", name, e)

    @Slot()
    def _on_language_changed(self) -> None:
        self.refresh()


__all__ = ["DEPENDENT_BRIDGES", "GameDirBridge"]
