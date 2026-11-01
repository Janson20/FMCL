"""用系统默认程序打开文件 / 目录，或在文件管理器里定位文件。

原先三处各写一遍（下载页的「打开文件夹」、设置页的「打开数据目录」、
更新页的「查看更新日志」），三个分支（Windows / macOS / 其它）也跟着抄了三遍。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["open_path", "reveal_in_folder"]


def open_path(target: str | os.PathLike) -> None:
    """用系统默认程序打开 `target`（文件或目录）。失败抛 :class:`OSError`。

    Windows 用 `os.startfile`（它走 ShellExecute，和双击行为一致）；macOS /
    Linux 没有等价物，只能调命令行工具，且不等待 —— 打开一个文件夹不该让
    调用线程陪着阻塞。
    """
    text = str(target or "").strip()
    if not text:
        raise OSError("没有要打开的路径")
    if sys.platform == "win32":
        os.startfile(text)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", text])
    else:
        subprocess.Popen(["xdg-open", text])


def reveal_in_folder(path: str | os.PathLike) -> None:
    """在文件管理器里定位到某个文件（而不是只把它的目录打开）。

    Windows 的 `explorer /select,` 是唯一的「选中」入口；其它平台没有统一
    做法，退回打开父目录 —— 比什么都不做要好。
    """
    target = Path(str(path or ""))
    if not str(target):
        raise OSError("没有要定位的文件")
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", os.path.normpath(str(target))])
    else:
        open_path(target.parent)
