"""漫游（个性化推荐流）的纯逻辑：什么时候该续、理由怎么显示。

放在这里而不是塞进桥接层，是因为这里的判断不需要 Qt 也不需要联网，
可以直接进 `tests/test_core.py` 回归 —— 续歌的时机错了是个很难发现的 bug
（要么播着播着断了，要么疯了一样往队列里塞歌）。
"""

from __future__ import annotations

from typing import Optional

# 队列里还剩这么少（不含正在播的这首）就该续了
REFILL_THRESHOLD = 2
# 一次续多少首
REFILL_SIZE = 6
# 每批新推荐的理由最长留几个字（服务端偶尔会给出很长的文案）
REASON_MAX = 16

def needs_refill(
    active: bool,
    remaining: int,
    loading: bool,
    threshold: int = REFILL_THRESHOLD,
) -> bool:
    """要不要再取一批推荐续上。

    Args:
        active: 当前放的是不是漫游流里的歌（用户切去听别的就自动交棒）
        remaining: 播放队列里排在当前这首**之后**还有几首
        loading: 是否已经有一批在取了（避免并发重复取）
    """
    if not active or loading:
        return False
    return remaining <= max(0, int(threshold))

def clean_reason(raw: str) -> str:
    """把服务端给的推荐理由收拾成适合显示的一小截。"""
    text = " ".join(str(raw or "").split())
    if len(text) <= REASON_MAX:
        return text
    return text[: REASON_MAX - 1] + "…"

def pick_playable(tracks: Optional[list]) -> list:
    """过滤掉明显放不了的条目（无歌名 / 无 id）。"""
    out = []
    for track in tracks or []:
        if track is None:
            continue
        if not str(getattr(track, "name", "") or ""):
            continue
        if not str(getattr(track, "songmid", "") or ""):
            continue
        out.append(track)
    return out
