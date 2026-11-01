"""数值的人话化：体积换算与「字符串转整数」。

这两件事原先在六个地方各写了一遍 —— 下载任务列表、更新包大小、缓存统计、
封面保存提示、背景图信息、下载速度 —— 单位取整的口径一旦分叉，同一块磁盘
在两个页面里就会显示成两个数（"1500 B" 与 "1.5 KB"）。留一份在这里，其余
模块直接 import。

本模块不依赖 Qt / 不碰网络，纯函数。
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

__all__ = ["human_size", "to_int"]

#: 体积单位（1024 进制）。最后一项兜住再大的数，不会出现 "1024.0 MB"
_SIZE_UNITS: Sequence[str] = ("B", "KB", "MB", "GB", "TB")


def human_size(size: Any, *, zero_text: str = "0 B") -> str:
    """字节数 → 人话。

    `zero_text` 给「拿不到大小」与「小于等于 0」两种情形用：更新页那边不知道
    远端大小时要说「未知大小」，而下载任务里 0 字节确实就是 `0 B`。
    """
    try:
        value = float(size)
    except (TypeError, ValueError):
        return zero_text
    if value <= 0:
        return zero_text
    for unit in _SIZE_UNITS:
        if value < 1024 or unit == _SIZE_UNITS[-1]:
            # 字节不带小数（"512.0 B" 看着别扭），其余保留一位
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} {_SIZE_UNITS[-1]}"


def to_int(value: Any, default: int = 0, *, minimum: Optional[int] = None) -> int:
    """尽量把任意值转成 int，转不动就用 `default`。

    * 认 `"123"` / `"123.0"`（HTTP 头与 JSON 里两种都见过）；
    * `minimum` 用来夹下界（`Content-Length` 这类字段不该出现负数，
      夹成 0 比抛异常合适）。
    """
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        number = int(default)
    if minimum is not None and number < minimum:
        number = int(minimum)
    return number
