"""曲目的取值与形态归一：`Track` / `MusicInfo` / 字典 / JSON 串 / QJSValue。

QML 会把一首歌以三种形态之一递进 Python：`QVariantMap`（dict）、
`QJSValue`（QML 里的 `property var`）、或者干脆是 JSON 串；
核心层自己又用 `Track` 与 `MusicInfo`。于是「取某个字段」这件事在
naming / downloader / download_writer / covers / bridges 里各写过一遍，
连「字典转 Track」都分成了三份，其中一份还漏了 QJSValue 解包。

这里只做**形态归一**，不做业务判断：能不能播、要不要下载由调用方决定。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .models import Track

__all__ = ["field", "as_dict", "as_track"]


def _unwrap_variant(value: Any) -> Any:
    """`QJSValue` → 真正的 Python 值（其它类型原样返回）。

    不 import Qt：只认 `toVariant` 这个方法名。测试与纯 Python 调用点里
    根本不存在 QJSValue，能少一条对 Qt 的硬依赖。
    """
    if value is None or isinstance(value, (dict, list, tuple, str, Track)):
        return value
    to_variant = getattr(value, "toVariant", None)
    if callable(to_variant):
        try:
            return to_variant()
        except Exception:  # pragma: no cover - QJSValue 可能已失效
            return None
    return value


def field(source: Any, key: str) -> Any:
    """从 `Track` / `MusicInfo` / 字典里取字段（取不到返回 `None`）。

    QML 传过来的永远是字典，内部调用传的是对象 —— 两者都得吃。
    """
    if isinstance(source, dict):
        return source.get(key)
    return getattr(source, key, None)


def as_dict(track: Any) -> Dict[str, Any]:
    """任意曲目形态 → 字典（认不出来返回空字典）。

    字典原样复制（调用方可能改动）；对象走 `to_dict()`；JSON 串先解析。
    复制而不是返回原对象，是为了不让调用方无意中改到播放器手里的那份。
    """
    value = _unwrap_variant(track)
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    for attr in ("to_dict", "toDict"):
        method = getattr(value, attr, None)
        if callable(method):
            try:
                data = method()
            except Exception:  # pragma: no cover - 兜底，曲目对象不该抛
                continue
            if isinstance(data, dict):
                return dict(data)
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except (ValueError, TypeError):
            return {}
        return dict(data) if isinstance(data, dict) else {}
    return {}


def as_track(value: Any, *, strict: bool = False) -> Optional[Track]:
    """任意曲目形态 → `Track`；认不出来返回 `None`。

    `strict=True` 时还要求「像一首歌」（有歌名或曲目 id）—— 下载队列用这个：
    入队的空壳任务永远下不下来，不如在门口就挡住。默认宽松（与播放引擎一致：
    只要形状对就收，歌名对不对由业务层判）。
    """
    value = _unwrap_variant(value)
    if isinstance(value, Track):
        return value if (not strict or value.songmid or value.name) else None
    data = as_dict(value) if isinstance(value, dict) or isinstance(value, str) else None
    if data is not None:
        track = Track.from_dict(data)
        if strict and not (track.songmid or track.name):
            return None
        return track
    if hasattr(value, "songmid") and hasattr(value, "name"):
        try:
            return Track.from_music_info(value)
        except Exception:  # pragma: no cover - 畸形 MusicInfo 不该炸调用方
            return None
    return None
