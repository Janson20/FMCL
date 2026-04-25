"""结构化日志模块 - 输出 JSONL 格式的结构化日志

用法:
    from structured_logger import StructuredLogger

    slog = StructuredLogger()
    slog.info("version_installed", version="1.20.4", duration=12.5, success=True)
    slog.error("launch_failed", version="1.20.4-forge", error="OutOfMemoryError")
"""
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from logzero import logger


class StructuredLogger:
    """结构化日志记录器，输出 JSONL 格式（每行一条 JSON 记录）"""

    def __init__(self, log_path: Optional[str] = None):
        """
        初始化结构化日志记录器

        Args:
            log_path: 日志文件路径，默认为 config.base_dir / "latest_structured.log"
        """
        self._lock = threading.Lock()
        self._log_path: Optional[str] = log_path
        self._file_handle = None

    def _ensure_handle(self) -> Optional[Any]:
        """延迟打开文件句柄"""
        if self._file_handle is not None:
            return self._file_handle

        log_path = self._log_path
        if log_path is None:
            try:
                from config import config
                log_path = str(config.base_dir / "latest_structured.log")
            except Exception:
                log_path = "latest_structured.log"

        try:
            self._file_handle = open(log_path, "a", encoding="utf-8")
            self._log_path = log_path
        except Exception as e:
            logger.error(f"无法打开结构化日志文件 {log_path}: {e}")
            return None

        return self._file_handle

    def _write(self, level: str, event: str, **kwargs: Any) -> None:
        """写入一条结构化日志"""
        record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "event": event,
        }
        if kwargs:
            record["data"] = kwargs

        line = json.dumps(record, ensure_ascii=False, default=str)

        with self._lock:
            handle = self._ensure_handle()
            if handle:
                try:
                    handle.write(line + "\n")
                    handle.flush()
                except Exception as e:
                    logger.error(f"写入结构化日志失败: {e}")

    def debug(self, event: str, **kwargs: Any) -> None:
        """记录 DEBUG 级别结构化日志"""
        self._write("DEBUG", event, **kwargs)

    def info(self, event: str, **kwargs: Any) -> None:
        """记录 INFO 级别结构化日志"""
        self._write("INFO", event, **kwargs)

    def warning(self, event: str, **kwargs: Any) -> None:
        """记录 WARNING 级别结构化日志"""
        self._write("WARNING", event, **kwargs)

    def error(self, event: str, **kwargs: Any) -> None:
        """记录 ERROR 级别结构化日志"""
        self._write("ERROR", event, **kwargs)

    def close(self) -> None:
        """关闭日志文件句柄"""
        with self._lock:
            if self._file_handle:
                try:
                    self._file_handle.close()
                except Exception:
                    pass
                self._file_handle = None


# 全局单例
slog = StructuredLogger()
