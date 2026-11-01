"""JSON / 字节的原子落盘与读取。

「先写临时文件再 `os.replace`」这件事原先有四份实现：配置、响度缓存、
更新标记、加密凭据，另外还有两份直接 `write_text` —— 其中更新标记那份
（`update_install.write_pending`）在最需要原子的地方反而不原子：写到一半
断电，下次启动读到半个 JSON，自动更新的成败就判不出来了。

落盘统一走 :func:`atomic_write_bytes`（同目录 mkstemp + replace），JSON 再包
一层 :func:`atomic_write_json`。不 fsync：配置是**每改一个开关就写一次**的，
每次 fsync 会把界面拖慢，而这几份文件丢了也只是回到默认值。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

__all__ = ["atomic_write_bytes", "atomic_write_json", "read_json"]


def atomic_write_bytes(path: str | os.PathLike, data: bytes) -> None:
    """原子写入字节：临时文件写满 → `os.replace` 顶上目标。

    临时文件必须与目标**同目录**：跨盘替换不是原子的（`os.replace` 会退化成
    复制），而 mkstemp 默认落在系统临时目录。
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(
    path: str | os.PathLike,
    data: Any,
    *,
    indent: Optional[int] = None,
    separators: Optional[tuple] = None,
) -> None:
    """原子写入 JSON（`indent` / `separators` 由调用方按文件用途挑）。

    配置为了可读性与手改友好用 `indent=2`；响度缓存动辄上万条，用紧凑格式。
    """
    text = json.dumps(data, ensure_ascii=False, indent=indent, separators=separators)
    atomic_write_bytes(path, text.encode("utf-8"))


def read_json(path: str | os.PathLike, default: Any) -> Any:
    """读 JSON；缺文件返回 `default`，解析失败把原文件挪成 `*.corrupt`。

    坏文件选择「改名保留」而不是删除：用户手改坏了的歌单 / 会话还有机会救回来，
    静默丢掉就成了数据丢失。
    """
    target = Path(path)
    if not target.exists():
        return default
    try:
        raw = target.read_text(encoding="utf-8")
        return json.loads(raw) if raw.strip() else default
    except Exception as e:
        logger.warning("读取 %s 失败，已备份为 .corrupt: %s", target.name, e)
        try:
            target.replace(target.with_suffix(target.suffix + ".corrupt"))
        except Exception:
            pass
        return default
