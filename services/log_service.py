"""启动器日志服务（阶段 3 任务 3.4：对照表 A-13 / A-18）。

## 它解决什么

旧 Tk 界面的"启动器日志"是侧边栏那块常驻文本框：`ui/app_base.py:881-914` 给
`logzero.logger` 挂了一个写进 `io.StringIO` 的 handler，再用 `after()` **轮询**把新行
搬进文本框，单行上限 `LOG_VIEW_MAX_LINES = 5000`（`ui/log_widget.py`）。

QML 侧原先**一条捕获链路都没有**：`qml/components/LogView.qml` 是现成的展示件
（阶段 2 任务 2.18 交付），但没有任何东西往它里面送行。本模块补的就是这一段：
一个 handler + 一个环形缓冲 + "有新行"的合并通知。

## 三条设计决定

1. **缓冲在服务层、格式化也在服务层**：行文本就按旧实现那一份
   `logging.Formatter("[%(asctime)s] %(message)s", datefmt="%H:%M:%S")` 产出，
   界面不做拼接（红线 2）。上限 5000 与 `ui/log_widget.py` 的常量同值。
2. **通知是合并的、且排到主线程**：日志行可能成千上万条（下载、AGENT），
   每条都发一次信号会把界面淹掉。这里用"dirty 标志 + 一次 dispatch"合并：
   在同一个事件循环回合里追加一万行，界面只收到**一次**通知。
   排主线程用 `AppContext` 的任务调度器（`Service.tasks.dispatch`）——
   服务层因此仍然零 UI 依赖（不 import PySide6）。
3. **导出优先导出磁盘上的日志文件**：`LogView` 里的缓冲是有上限的，磁盘上那份
   `latest.log` 才是完整记录（`main.setup_logging()` 落盘，目录不可写时回退
   `~/.fmcl/latest.log` —— A-18）。文件读不到时退回缓冲区内容，并如实说明来源。

## 与旧实现的差异（如实记录）

* 旧界面**没有**导出功能（只有"清空"）。导出是 M-Q1 之外的新增能力，
  03 章的 3.4 摘要里写明本页要"日志查看与导出"，故按新增实现并在对照表标注。
* 清空只清**界面缓冲**，不动磁盘文件（与旧实现一致：旧"清空日志"也只清文本框）。
"""

from __future__ import annotations

import logging
import os
import subprocess  # noqa: S404 - 打开日志目录要调系统文件管理器（旧实现同款）
import sys
from pathlib import Path
from typing import Any, Callable, List, Optional

from services.base import Service

#: 缓冲上限（与 `ui/log_widget.py:LOG_VIEW_MAX_LINES` 同值：5000 行）
LOG_VIEW_MAX_LINES = 5000

#: 行格式（逐字对齐 `ui/app_base.py:889`）
LOG_FORMAT = "[%(asctime)s] %(message)s"
LOG_DATE_FORMAT = "%H:%M:%S"

#: logzero 的 logger 名字（`logzero.logger` 就是它；实测 name == "logzero_default"）。
#: **不写成常量以外的取值**：`logzero.logger()` 在某些版本会另建 logger，
#: 而 `from logzero import logger` 拿到的始终是这一个。
LOGZERO_LOGGER_NAME = "logzero_default"


class LogBuffer:
    """定长环形缓冲（只留最近 `max_lines` 行）。

    为什么不用 `collections.deque(maxlen=…)`：`deque` 不能在 O(1) 内给出
    "快照切片"，而界面每次刷新都要整段读走（5000 行的拷贝不算贵，
    但 `list(deque)` 每次都要重建）。这里用普通 list + 批量裁剪，
    `snapshot()` 只拷一次。
    """

    def __init__(self, max_lines: int = LOG_VIEW_MAX_LINES) -> None:
        self.max_lines = max(1, int(max_lines))
        self._lines: List[str] = []
        self._dropped = 0

    def append(self, text: str) -> None:
        self._lines.append(str(text))
        if len(self._lines) > self.max_lines:
            # 一次裁干净，别每行都 del 一次（旧 `append_line` 也是成批裁）
            drop = len(self._lines) - self.max_lines
            del self._lines[:drop]
            self._dropped += drop

    def clear(self) -> None:
        """清空缓冲。**不**重置 `dropped` —— 那个计数是"曾经丢过多少行"的如实记录。"""
        self._lines = []

    def snapshot(self) -> List[str]:
        return list(self._lines)

    def __len__(self) -> int:
        return len(self._lines)

    @property
    def dropped(self) -> int:
        return self._dropped


class LogCaptureHandler(logging.Handler):
    """把日志记录送进 :class:`LogBuffer` 的 handler。

    `emit()` 里**不允许抛异常**：logging 模块对 handler 异常的处理是打一条
    "--- Logging error ---" 到 stderr，那条噪声会污染"界面日志是否干净"的判据。
    """

    def __init__(self, on_record: Callable[[str], None], level: int = logging.DEBUG) -> None:
        super().__init__(level=level)
        self._on_record = on_record
        self.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102 - logging.Handler 契约
        try:
            self._on_record(self.format(record))
        except Exception:  # noqa: BLE001 - 见类文档：handler 抛异常会往 stderr 打噪声
            pass


class LogService(Service):
    """启动器日志的捕获、缓冲、导出。"""

    name = "log"
    label = "启动器日志"

    def __init__(
        self,
        context: Any = None,
        *,
        config: Any = None,
        max_lines: int = LOG_VIEW_MAX_LINES,
        logger: Optional[logging.Logger] = None,
        opener: Optional[Callable[[str], Any]] = None,
    ) -> None:
        """
        Args:
            config: 配置对象（读 `log_file` 兜底路径用）。
            logger: 要挂 handler 的 logger。默认 `logging.getLogger(LOGZERO_LOGGER_NAME)`
                —— **必须**是 `logzero.logger` 那一个，旧实现挂的就是它。
            opener: 打开目录的实现（测试注入；默认按平台选 startfile/xdg-open/open）。
        """
        super().__init__(context)
        self._config = config
        self._logger = logger
        self._opener = opener
        self.buffer = LogBuffer(max_lines)
        self.handler = LogCaptureHandler(self._record)
        self._listener: Optional[Callable[[], None]] = None
        self._notify_pending = False
        self._attached = False

    # ─── 生命周期 ────────────────────────────────────────────

    def start(self) -> None:
        super().start()
        self.attach_logger()

    def stop(self) -> None:
        self.detach_logger()
        super().stop()

    def target_logger(self) -> logging.Logger:
        if self._logger is None:
            self._logger = logging.getLogger(LOGZERO_LOGGER_NAME)
        return self._logger

    def attach_logger(self) -> bool:
        """把捕获 handler 挂到目标 logger 上（幂等：重复调用不会重复挂）。

        **名字不能叫 `attach`**：`Service.attach(context)` 是基类的生命周期钩子，
        `AppContext._resolve()` 取出懒注册的服务后会调它。第一版把它覆盖成
        `attach(logger=None)`，结果是 `TypeError: attach() takes 1 positional argument
        but 2 were given` → 服务取不到 → 日志页永远"已捕获 0 / 0 行"
        （用户 2026-10-06 真机验收报的"日志没捕获"就是这个）。
        `tests/test_app_context_wiring.py::test_every_registered_service_resolves`
        现在会拦住这一类撞名。
        """
        logger = self.target_logger()
        if self.handler not in logger.handlers:
            logger.addHandler(self.handler)
            self._attached = True
            self.log.info("启动器日志捕获已挂载（缓冲上限 %d 行）", self.buffer.max_lines)
        return self._attached

    def detach_logger(self) -> bool:
        logger = self.target_logger()
        if self.handler in logger.handlers:
            logger.removeHandler(self.handler)
            self._attached = False
            return True
        return False

    @property
    def attached(self) -> bool:
        return self._attached

    # ─── 写入与通知 ──────────────────────────────────────────

    def _record(self, line: str) -> None:
        """handler 的回调（**可能在任意线程**）。只动缓冲 + 排一次通知。"""
        self.buffer.append(line)
        self._notify_later()

    def append(self, line: str) -> None:
        """手动追加一行（界面自己的"已就绪"之类；旧实现 `_append_log` 的等价物）。"""
        self._record(line)

    def set_listener(self, listener: Optional[Callable[[], None]]) -> None:
        """设置"有新行"的回调（桥接层用；服务本身不认识 QML）。"""
        self._listener = listener

    def _notify_later(self) -> None:
        """合并通知：一个事件循环回合内只排一次。"""
        if self._listener is None or self._notify_pending:
            return
        self._notify_pending = True
        try:
            # 有上下文就排到主线程（Qt 侧是 QueuedConnection 的调度器）；
            # 没有上下文（单元测试）时直接执行 —— 绝不能因为没上下文就丢掉通知。
            self.tasks.dispatch(self._flush_notify)
        except Exception:  # noqa: BLE001 - 没有上下文/调度器
            self._flush_notify()

    def _flush_notify(self) -> None:
        self._notify_pending = False
        listener = self._listener
        if listener is None:
            return
        try:
            listener()
        except Exception as e:  # noqa: BLE001 - 界面回调抛异常不该影响日志写入
            self.log.debug("日志监听回调失败: %s", e)

    # ─── 读取 ───────────────────────────────────────────────

    def lines(self) -> List[str]:
        return self.buffer.snapshot()

    def line_count(self) -> int:
        return len(self.buffer)

    def capacity(self) -> int:
        return self.buffer.max_lines

    def clear(self) -> int:
        """清空界面缓冲，返回清掉的行数（旧"清空日志"按钮）。"""
        count = len(self.buffer)
        self.buffer.clear()
        self.log.info("启动器日志缓冲已清空（%d 行）", count)
        self._notify_later()
        return count

    def text(self) -> str:
        """缓冲全文（导出回退路径与测试用）。行尾统一 `\\n`。"""
        return "".join(f"{line}\n" for line in self.buffer.snapshot())

    # ─── 落盘文件（A-18） ────────────────────────────────────

    def config_obj(self) -> Any:
        if self._config is not None:
            return self._config
        try:
            self._config = self.context.config
        except Exception:  # noqa: BLE001
            try:
                from config import config as root_config

                self._config = root_config
            except Exception:  # noqa: BLE001
                self._config = None
        return self._config

    def log_file(self) -> Optional[Path]:
        """当前日志文件的**真实**路径。

        先问 logzero 的文件 handler（`baseFilename` 就是 `setup_logging()` 最终选的
        那个路径：目录不可写时它已经回退到 `~/.fmcl/latest.log`，A-18）——
        这样不需要把 `main.py` 里那个局部变量再存一份，也就不会出现"两份路径"。
        """
        for handler in self.target_logger().handlers:
            name = getattr(handler, "baseFilename", None)
            if name:
                return Path(str(name))
        cfg = self.config_obj()
        path = getattr(cfg, "log_file", None) if cfg is not None else None
        return Path(str(path)) if path else None

    def log_dir(self) -> Optional[Path]:
        path = self.log_file()
        return path.parent if path is not None else None

    def log_file_size(self) -> int:
        path = self.log_file()
        try:
            return path.stat().st_size if path is not None and path.exists() else 0
        except OSError:
            return 0

    def export(self, dest: str) -> dict:
        """把日志另存到 `dest`。

        优先写**磁盘上的日志文件全文**（完整记录），读不到时退回缓冲区
        （界面里看到的那一份），并在 `source` 里说明用的是哪一份。
        返回 `{"ok", "path", "source", "lines", "bytes", "error"}`。
        """
        target = Path(str(dest or ""))
        result = {"ok": False, "path": str(target), "source": "", "lines": 0, "bytes": 0, "error": ""}
        if not str(dest or "").strip():
            result["error"] = "empty_path"
            return result

        payload = ""
        source = "file"
        path = self.log_file()
        try:
            if path is not None and path.exists() and path.stat().st_size > 0:
                payload = path.read_text(encoding="utf-8", errors="replace")
            else:
                payload = self.text()
                source = "buffer"
        except OSError as e:
            self.log.warning("读日志文件失败，改用缓冲区导出: %s", e)
            payload = self.text()
            source = "buffer"

        if not payload:
            result["error"] = "nothing_to_export"
            return result

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # newline=""：行尾原样写出，不让 Python 在 Windows 上把 \n 换掉
            with open(target, "w", encoding="utf-8", newline="") as fp:
                fp.write(payload)
        except (OSError, ValueError) as e:
            # ValueError 也要接：路径里带非法字符（例如 `\0`）时 `open()` 抛的是它，
            # 不是 OSError —— 第一版只接 OSError，于是"导出到一个坏路径"会**抛穿到界面**。
            self.log.error("导出日志失败: %s", e)
            result["error"] = str(e)
            return result

        result.update(
            {
                "ok": True,
                "source": source,
                "lines": payload.count("\n") + (0 if payload.endswith("\n") else 1),
                "bytes": len(payload.encode("utf-8")),
            }
        )
        self.log.info("日志已导出到 %s（%s，%d 字节）", target, source, result["bytes"])
        return result

    def open_dir(self) -> dict:
        """用系统文件管理器打开日志目录。返回 `{"ok", "path", "error"}`。"""
        directory = self.log_dir()
        result = {"ok": False, "path": str(directory) if directory else "", "error": ""}
        if directory is None:
            result["error"] = "log_dir_unavailable"
            return result
        opener = self._opener
        try:
            if opener is not None:
                opener(str(directory))
            else:
                _open_path(str(directory))
        except Exception as e:  # noqa: BLE001 - 打不开目录只报错，不抛给界面
            self.log.warning("打开日志目录失败: %s", e)
            result["error"] = str(e)
            return result
        result["ok"] = True
        return result

    # ─── 自检 ───────────────────────────────────────────────

    def describe(self) -> dict:
        info = super().describe()
        info.update(
            {
                "attached": self._attached,
                "lines": len(self.buffer),
                "capacity": self.buffer.max_lines,
                "dropped": self.buffer.dropped,
                "log_file": str(self.log_file() or ""),
            }
        )
        return info


def _open_path(path: str) -> None:
    """按平台用系统默认方式打开目录（与 `VersionService._open_path` 同一套分支）。"""
    if hasattr(os, "startfile"):  # Windows
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606 - 打开目录不是执行程序
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])  # noqa: S603,S607 - 固定命令 + 用户自己的目录
        return
    subprocess.Popen(["xdg-open", path])  # noqa: S603,S607


__all__ = [
    "LOG_DATE_FORMAT",
    "LOG_FORMAT",
    "LOG_VIEW_MAX_LINES",
    "LOGZERO_LOGGER_NAME",
    "LogBuffer",
    "LogCaptureHandler",
    "LogService",
]
