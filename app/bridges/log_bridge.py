"""日志桥 —— QML 侧的 `Logs` 上下文属性（阶段 3 任务 3.4；对照表 A-13 / A-18）。

## 它是什么

日志页要三样东西：**实时行流、清空、导出/打开目录**。行流是"服务缓冲 → 增量 → QML"
这一条：`LogService` 把日志行收进环形缓冲（上限 5000，与旧侧边栏同值），
并在**主线程**上回调本桥一次（同一个事件循环回合内的上万行合并成一次）；
本桥算出"上次之后新增了哪些行"，用 `linesAppended` 发给页面，
页面调 `LogView.appendLines(...)` 追加（`qml/components/LogView.qml` 的既有 API）。

## 为什么是"增量 + 快照"两条路

`LogView` 是**命令式追加**的组件（阶段 2 任务 2.18 定的契约）。页面第一次出现时
缓冲里可能已经攒了几百行（启动日志），所以另给一个 `snapshot()`：
一次性把当前全部行交出去，并把"已交付"游标推到末尾 —— 之后只走增量。
两条路都动同一份游标，不会重复投递。

## 游标怎么算（缓冲会裁剪，不能用"已发条数"）

缓冲有上限，老行会被丢掉，所以"已发 N 条"这个说法在裁剪之后就错了。
这里用**绝对序号**：`LogBuffer.dropped` 是"已经丢掉多少行"，于是缓冲里第 i 行
的绝对序号是 `dropped + i`。游标记绝对序号，裁剪发生时自动对齐。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from services.i18n_service import _
from services.log_service import LogService

logger = logging.getLogger("app.bridges.log_bridge")


class LogBridge(QObject):
    """上下文属性 `Logs`。"""

    #: 新增了若干行（参数 = 行文本列表，**只含上次之后的新行**）。
    linesAppended = Signal("QVariantList")
    #: 缓冲被清空（页面据此清 `LogView`）。
    linesCleared = Signal()
    #: 行数 / 上限变了（状态文本用）。
    statsChanged = Signal()
    #: 导出完成（参数 = 服务返回的结果字典）。
    exported = Signal(dict)
    #: 状态条文案（**已翻译**）。
    statusMessage = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._service: Optional[LogService] = None
        #: 已交付到的**绝对序号**（缓冲里第 i 行的绝对序号 = dropped + i）
        self._cursor = 0

    # ─── 装配期注入 ─────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        """`main_qml.register_bridges` 在注册前调它：取日志服务并挂上"有新行"回调。"""
        self._service = self._service_of(context, "log")
        if self._service is None:
            logger.warning("日志服务不可用：日志页只会显示错误态")
            return
        # 服务是**懒注册**的（`AppContext.register_lazy`），因此 `start_all()` 那一轮
        # 并不会跑到它的 `start()`。这里显式挂一次捕获 handler（幂等）——
        # 不挂的话界面里一个字都看不到，而且不会有任何报错。
        try:
            self._service.attach_logger()
        except Exception as e:  # noqa: BLE001 - 挂不上只是看不到日志，不该挡住页面
            logger.warning("挂载日志捕获失败: %s", e)
        self._service.set_listener(self._on_lines)
        self._cursor = self._absolute_end()

    @staticmethod
    def _service_of(context: Any, name: str) -> Optional[LogService]:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001 - 取不到不该让整页打不开
            logger.warning("日志页取服务 %s 失败: %s", name, e)
            return None

    # ─── 行流 ───────────────────────────────────────────────

    def _absolute_end(self) -> int:
        """缓冲末尾的绝对序号（= 至今一共产生过多少行）。"""
        if self._service is None:
            return 0
        return int(self._service.buffer.dropped) + len(self._service.buffer)

    def _take_delta(self) -> List[str]:
        """取出上次之后的新行，并把游标推到头。"""
        if self._service is None:
            return []
        dropped = int(self._service.buffer.dropped)
        end = dropped + len(self._service.buffer)
        start = max(self._cursor, dropped)
        if start >= end:
            self._cursor = end
            return []
        lines = self._service.buffer.snapshot()
        delta = lines[start - dropped:]
        self._cursor = end
        return delta

    def _on_lines(self) -> None:
        """服务的回调（已经排到主线程）。只发增量，不碰 QML 引擎以外的对象。"""
        delta = self._take_delta()
        if not delta:
            return
        self.linesAppended.emit(delta)
        self.statsChanged.emit()

    @Slot(result="QVariantList")
    def snapshot(self) -> List[str]:
        """当前全部行（页面首次出现时用），并把游标推到头。"""
        if self._service is None:
            return []
        lines = self._service.lines()
        self._cursor = self._absolute_end()
        return lines

    @Property(int, notify=statsChanged)
    def lineCount(self) -> int:  # noqa: N802
        return self._service.line_count() if self._service is not None else 0

    @Property(int, constant=True)
    def capacity(self) -> int:
        return self._service.capacity() if self._service is not None else 0

    @Property(bool, constant=True)
    def available(self) -> bool:
        return self._service is not None

    @Property(str, notify=statsChanged)
    def logFile(self) -> str:  # noqa: N802
        if self._service is None:
            return ""
        path = self._service.log_file()
        return str(path) if path is not None else ""

    # ─── 动作 ───────────────────────────────────────────────

    @Slot(result=int)
    def clearLog(self) -> int:  # noqa: N802
        """清空界面缓冲（**不动磁盘上的日志文件**，与旧"清空日志"一致）。"""
        if self._service is None:
            return 0
        count = self._service.clear()
        self._cursor = self._absolute_end()
        self.linesCleared.emit()
        self.statsChanged.emit()
        self.statusMessage.emit(_("settings_log_cleared", count=count), "info")
        return count

    @Slot(str, result="QVariantMap")
    def exportLog(self, dest: str) -> Dict[str, Any]:  # noqa: N802
        """导出日志到 `dest`（优先磁盘全文，读不到时退回缓冲区）。"""
        if self._service is None:
            return {"ok": False, "error": "service_unavailable"}
        result = self._service.export(dest)
        if result.get("ok"):
            self.statusMessage.emit(_("backup_export_success", path=result.get("path", "")), "success")
        else:
            self.statusMessage.emit(_("backup_export_failed", error=result.get("error", "")), "error")
        self.exported.emit(dict(result))
        return result

    @Slot(result=bool)
    def openLogDir(self) -> bool:  # noqa: N802
        """用系统文件管理器打开日志目录。"""
        if self._service is None:
            return False
        result = self._service.open_dir()
        if result.get("ok"):
            self.statusMessage.emit(result.get("path", ""), "info")
        else:
            self.statusMessage.emit(
                _("version_open_folder_failed", error=result.get("error", "")), "error"
            )
        return bool(result.get("ok"))

    # ─── 自检 ───────────────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        return {
            "available": self._service is not None,
            "lineCount": self.lineCount,
            "capacity": self.capacity,
            "cursor": self._cursor,
            "logFile": self.logFile,
        }


__all__ = ["LogBridge"]
