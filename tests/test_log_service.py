"""`services/log_service.py` 的永久回归守卫（阶段 3 任务 3.4；对照表 A-13 / A-18）。

守四件事：

1. **环形缓冲有界**（上限 5000 行，与旧侧边栏 `ui/log_widget.py:LOG_VIEW_MAX_LINES` 同值）——
   D-86 那个"日志框只 append 从不删除"的坑不能再踩一次；
2. **handler 接线幂等、可摘**：重复 `attach()` 不会让同一行进两次；
3. **通知被合并**：同一个事件循环回合里追加一万行，监听方只被叫一次
   （界面侧就是靠这条才不会被日志淹掉）；
4. **导出与打开目录**：优先磁盘日志全文、读不到时退回缓冲区，并且在两种情况下
   都能给出"来源 + 行数 + 字节数"。

测试不碰真 `logzero` logger：每个用例自己造一个 logger，用完 `detach()`
（碰真的会把整场测试的日志都灌进缓冲，判据就没法看了）。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.log_service import (  # noqa: E402
    LOG_DATE_FORMAT,
    LOG_FORMAT,
    LOG_VIEW_MAX_LINES,
    LogBuffer,
    LogCaptureHandler,
    LogService,
)


class FakeConfig:
    def __init__(self, log_file: Path) -> None:
        self.log_file = log_file


@pytest.fixture()
def logger() -> logging.Logger:
    """独立的 logger（不传 `propagate=False` 的话会往 root 冒泡，日志会重复）。"""
    instance = logging.getLogger("fmcl-test-log-service")
    instance.handlers.clear()
    instance.propagate = False
    instance.setLevel(logging.DEBUG)
    return instance


@pytest.fixture()
def service(tmp_path: Path, logger: logging.Logger) -> LogService:
    log_file = tmp_path / "latest.log"
    svc = LogService(config=FakeConfig(log_file), logger=logger, opener=lambda path: None)
    svc.attach_logger()
    yield svc
    svc.detach_logger()


# ─── 缓冲 ──────────────────────────────────────────────────────


class TestBuffer:
    def test_capacity_matches_the_legacy_limit(self) -> None:
        assert LOG_VIEW_MAX_LINES == 5000, "与 ui/log_widget.py 的常量必须同值"

    def test_buffer_keeps_only_the_last_lines(self) -> None:
        buffer = LogBuffer(max_lines=3)
        for index in range(10):
            buffer.append(f"line-{index}")
        assert buffer.snapshot() == ["line-7", "line-8", "line-9"]
        assert len(buffer) == 3
        assert buffer.dropped == 7, "丢了多少行要如实记着（增量投递靠它算绝对序号）"

    def test_clear_keeps_the_dropped_counter(self) -> None:
        buffer = LogBuffer(max_lines=2)
        for index in range(5):
            buffer.append(str(index))
        buffer.clear()
        assert buffer.snapshot() == [] and len(buffer) == 0
        assert buffer.dropped == 3, "清空不该把'曾经丢过多少行'也抹掉"

    def test_capacity_is_at_least_one(self) -> None:
        assert LogBuffer(max_lines=0).max_lines == 1


# ─── handler 接线 ──────────────────────────────────────────────


class TestAttach:
    def test_attach_is_idempotent(self, service: LogService, logger: logging.Logger) -> None:
        service.attach_logger()
        service.attach_logger()
        logger.info("once")
        assert len([line for line in service.lines() if "once" in line]) == 1

    def test_lines_use_the_legacy_format(self, service: LogService, logger: logging.Logger) -> None:
        logger.info("hello %s", "world")
        line = service.lines()[-1]
        assert line.endswith("hello world")
        assert line.startswith("[") and line.count(":") >= 2, f"行格式应为 {LOG_FORMAT}"
        assert LOG_DATE_FORMAT == "%H:%M:%S"

    def test_detach_stops_capture(self, service: LogService, logger: logging.Logger) -> None:
        assert service.detach_logger() is True
        logger.info("after-detach")
        assert not any("after-detach" in line for line in service.lines())
        assert service.attached is False

    def test_handler_never_raises(self) -> None:
        """handler 抛异常会被 logging 打成 "--- Logging error ---" 噪声（污染日志判据）。"""
        handler = LogCaptureHandler(lambda _line: (_ for _ in ()).throw(RuntimeError("坏了")))
        handler.emit(logging.LogRecord("x", logging.INFO, __file__, 1, "msg", None, None))

    def test_manual_append(self, service: LogService) -> None:
        service.append("[FMCL] manual")
        assert service.lines()[-1] == "[FMCL] manual"

    def test_clear_returns_the_count(self, service: LogService, logger: logging.Logger) -> None:
        logger.info("a")
        logger.info("b")
        assert service.clear() >= 2
        assert service.line_count() == 0

    def test_buffer_is_bounded(self, logger: logging.Logger, tmp_path: Path) -> None:
        service = LogService(config=FakeConfig(tmp_path / "x.log"), logger=logger, max_lines=5)
        service.attach_logger()
        try:
            for index in range(20):
                logger.info("line-%d", index)
            assert service.line_count() == 5
            assert "line-19" in service.lines()[-1]
        finally:
            service.detach_logger()


# ─── 通知合并 ──────────────────────────────────────────────────


class TestNotification:
    def test_listener_is_called_once_per_burst(self, service: LogService, logger: logging.Logger) -> None:
        calls: List[int] = []

        class Dispatcher:
            def dispatch(self, fn: Any) -> bool:
                calls.append(1)  # 只记"排了几次"，不真的执行
                return True

        service.set_listener(lambda: calls.append(0))
        service._context = type("Ctx", (), {"tasks": Dispatcher()})()  # noqa: SLF001 - 注入调度器
        for index in range(1000):
            logger.info("burst-%d", index)
        assert len(calls) == 1, f"一个回合内应当只排一次通知，实际排了 {len(calls)} 次"
        assert service.line_count() == 1000

    def test_listener_runs_immediately_without_a_context(
        self, service: LogService, logger: logging.Logger
    ) -> None:
        """没有 AppContext（单测/独立进程）时也必须通知，否则界面永远不刷新。"""
        seen: List[str] = []
        service.set_listener(lambda: seen.append("tick"))
        logger.info("no-context")
        assert seen == ["tick"]

    def test_flush_clears_the_pending_flag(self, service: LogService) -> None:
        seen: List[str] = []
        service.set_listener(lambda: seen.append("tick"))
        service._notify_pending = True  # noqa: SLF001 - 手工造"已经排过一次"
        service._flush_notify()  # noqa: SLF001
        assert seen == ["tick"]
        assert service._notify_pending is False  # noqa: SLF001
        service._notify_later()  # noqa: SLF001 - 旗标清了才允许再排
        assert seen == ["tick", "tick"]


# ─── 落盘路径（A-18） ──────────────────────────────────────────


class TestLogFile:
    def test_prefers_the_file_handler_path(self, tmp_path: Path, logger: logging.Logger) -> None:
        """真实路径问 logzero 的文件 handler —— `main.setup_logging()` 的回退结果就在那儿。"""
        log_file = tmp_path / "from-handler.log"
        handler = logging.FileHandler(log_file, encoding="utf-8")
        logger.addHandler(handler)
        service = LogService(config=FakeConfig(tmp_path / "other.log"), logger=logger)
        try:
            assert service.log_file() == log_file
            assert service.log_dir() == tmp_path
        finally:
            logger.removeHandler(handler)
            handler.close()

    def test_falls_back_to_the_config_path(self, tmp_path: Path, logger: logging.Logger) -> None:
        config = FakeConfig(tmp_path / "config.log")
        service = LogService(config=config, logger=logger)
        assert service.log_file() == config.log_file

    def test_file_size_of_missing_file_is_zero(self, service: LogService) -> None:
        assert service.log_file_size() == 0


# ─── 导出与打开目录 ────────────────────────────────────────────


class TestExport:
    def test_export_prefers_the_file_on_disk(self, service: LogService, logger: logging.Logger) -> None:
        """磁盘上的日志是完整记录（界面缓冲有上限）—— 导出要给完整的。"""
        path = service.log_file()
        assert path is not None
        path.write_text("full-log-line-1\nfull-log-line-2\n", encoding="utf-8")
        target = path.parent / "exported.log"
        result = service.export(str(target))
        assert result["ok"] is True and result["source"] == "file"
        assert target.read_text(encoding="utf-8") == "full-log-line-1\nfull-log-line-2\n"
        assert result["lines"] == 2

    def test_export_falls_back_to_the_buffer(self, service: LogService, logger: logging.Logger) -> None:
        logger.info("buffered-line")
        target = service.log_dir() / "exported-buffer.log"
        assert not service.log_file().exists(), "前提：磁盘日志还不存在"
        result = service.export(str(target))
        assert result["ok"] is True and result["source"] == "buffer"
        assert "buffered-line" in target.read_text(encoding="utf-8")

    def test_export_reports_empty_buffer(self, service: LogService) -> None:
        result = service.export(str(service.log_dir() / "empty.log"))
        assert result["ok"] is False and result["error"] == "nothing_to_export"

    def test_export_rejects_a_blank_path(self, service: LogService, logger: logging.Logger) -> None:
        logger.info("x")
        result = service.export("   ")
        assert result["ok"] is False and result["error"] == "empty_path"

    def test_export_failure_is_reported(self, service: LogService, logger: logging.Logger) -> None:
        logger.info("x")
        result = service.export(str(service.log_dir() / "no" / "such" / "\0bad.log"))
        assert result["ok"] is False and result["error"]

    def test_export_writes_utf8_without_newline_translation(
        self, service: LogService, logger: logging.Logger
    ) -> None:
        logger.info("中文日志")
        target = service.log_dir() / "utf8.log"
        service.export(str(target))
        raw = target.read_bytes()
        assert "中文日志".encode() in raw
        assert b"\r\n" not in raw, "行尾不许被 Windows 的默认换行翻译改掉"


class TestOpenDir:
    def test_open_dir_calls_the_injected_opener(self, tmp_path: Path, logger: logging.Logger) -> None:
        opened: List[str] = []
        service = LogService(config=FakeConfig(tmp_path / "a.log"), logger=logger, opener=opened.append)
        result = service.open_dir()
        assert result["ok"] is True and opened == [str(tmp_path)]

    def test_open_dir_reports_failures(self, tmp_path: Path, logger: logging.Logger) -> None:
        def boom(_path: str) -> None:
            raise OSError("没有文件管理器")

        service = LogService(config=FakeConfig(tmp_path / "a.log"), logger=logger, opener=boom)
        result = service.open_dir()
        assert result["ok"] is False and "没有文件管理器" in result["error"]

    def test_open_dir_without_a_path(self, logger: logging.Logger) -> None:
        """配置里没有日志路径时要说清楚，而不是抛异常。"""

        class NoPath:
            log_file = None

        service = LogService(config=NoPath(), logger=logger, opener=lambda path: None)
        result = service.open_dir()
        assert result["ok"] is False and result["error"] == "log_dir_unavailable"


# ─── 生命周期与自检 ────────────────────────────────────────────


class TestLifecycle:
    def test_start_attaches_and_stop_detaches(self, logger: logging.Logger, tmp_path: Path) -> None:
        service = LogService(config=FakeConfig(tmp_path / "x.log"), logger=logger)
        service.start()
        assert service.attached is True
        service.stop()
        assert service.attached is False

    def test_describe_reports_state(self, service: LogService, logger: logging.Logger) -> None:
        logger.info("one")
        info = service.describe()
        assert info["attached"] is True
        assert info["capacity"] == LOG_VIEW_MAX_LINES
        assert info["lines"] >= 1
        assert info["log_file"].endswith("latest.log")

    def test_all_names_resolve(self) -> None:
        import services.log_service as module

        missing = [name for name in module.__all__ if not hasattr(module, name)]
        assert missing == []
