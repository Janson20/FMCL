"""播放队列与播放模式。

相对 FMCL 的改进：FMCL 维护「文件夹路径列表 + 歌单上下文列表」两套下标，
自然播完时只看前者，导致在歌单里播本地歌会跳到文件夹的下一首
（``app_music.py:2504-2530``）。这里收敛为**单一队列**，语义统一。

随机模式也改为真正的洗牌序列（FMCL 的「重掷骰 + 撞当前则 +1」会重复播放）。
"""

from __future__ import annotations

import random
from enum import IntEnum
from typing import Callable, List, Optional

from .models import Track


class PlayMode(IntEnum):
    ORDER = 0        # 顺序播放：播到末尾即停
    LOOP_LIST = 1    # 列表循环（默认）
    LOOP_SINGLE = 2  # 单曲循环
    SHUFFLE = 3      # 随机播放（洗牌，不重复）


PLAY_MODE_NAMES = {
    PlayMode.ORDER: "sequential",
    PlayMode.LOOP_LIST: "loop_list",
    PlayMode.LOOP_SINGLE: "loop_single",
    PlayMode.SHUFFLE: "shuffle",
}

PLAY_MODE_LABELS = {
    PlayMode.ORDER: "顺序播放",
    PlayMode.LOOP_LIST: "列表循环",
    PlayMode.LOOP_SINGLE: "单曲循环",
    PlayMode.SHUFFLE: "随机播放",
}

_NAME_TO_MODE = {v: k for k, v in PLAY_MODE_NAMES.items()}


def mode_from_name(name: str, default: PlayMode = PlayMode.LOOP_LIST) -> PlayMode:
    return _NAME_TO_MODE.get((name or "").strip().lower(), default)


def mode_from_int(value: int) -> PlayMode:
    try:
        return PlayMode(int(value))
    except (ValueError, TypeError):
        return PlayMode.LOOP_LIST


class PlayQueue:
    """单一播放队列。

    负责维护曲目顺序、当前下标、随机顺序，以及「下一首 / 上一首」的选取策略。
    不负责真正的解码播放。
    """

    def __init__(self, on_changed: Optional[Callable[[], None]] = None):
        self._tracks: List[Track] = []
        self._index: int = -1
        self._mode: PlayMode = PlayMode.LOOP_LIST
        self._changed = on_changed
        # 随机模式下的播放顺序（存的是 _tracks 的下标）
        self._shuffle_order: List[int] = []
        self._shuffle_pos: int = -1
        # 已播放过的下标（供「上一首」回溯真实历史）
        self._history: List[int] = []

    # ── 属性 ────────────────────────────────────────────────

    @property
    def mode(self) -> PlayMode:
        return self._mode

    @mode.setter
    def mode(self, value: PlayMode) -> None:
        if value == self._mode:
            return
        self._mode = value
        if value == PlayMode.SHUFFLE:
            self._rebuild_shuffle(start_at_current=True)
        self._notify()

    @property
    def index(self) -> int:
        return self._index

    @property
    def size(self) -> int:
        return len(self._tracks)

    @property
    def is_empty(self) -> bool:
        return not self._tracks

    def tracks(self) -> List[Track]:
        return list(self._tracks)

    def current(self) -> Optional[Track]:
        if 0 <= self._index < len(self._tracks):
            return self._tracks[self._index]
        return None

    def at(self, index: int) -> Optional[Track]:
        if 0 <= index < len(self._tracks):
            return self._tracks[index]
        return None

    # ── 变更 ────────────────────────────────────────────────

    def _notify(self) -> None:
        if self._changed:
            self._changed()

    def set_tracks(self, tracks: List[Track], start_index: int = 0) -> None:
        self._tracks = list(tracks)
        self._history.clear()
        self._index = max(0, min(start_index, len(self._tracks) - 1)) if self._tracks else -1
        self._rebuild_shuffle(start_at_current=True)
        self._notify()

    def append(self, track: Track) -> int:
        self._tracks.append(track)
        if self._mode == PlayMode.SHUFFLE:
            self._shuffle_order.append(len(self._tracks) - 1)
        if self._index < 0 and self._tracks:
            self._index = 0
        self._notify()
        return len(self._tracks) - 1

    def extend(self, tracks: List[Track]) -> None:
        start = len(self._tracks)
        self._tracks.extend(tracks)
        if self._mode == PlayMode.SHUFFLE:
            self._shuffle_order.extend(range(start, len(self._tracks)))
        if self._index < 0 and self._tracks:
            self._index = 0
        self._notify()

    def insert(self, index: int, track: Track) -> int:
        index = max(0, min(index, len(self._tracks)))
        self._tracks.insert(index, track)
        # 插入位置之后的所有下标都要 +1
        self._shuffle_order = [i + 1 if i >= index else i for i in self._shuffle_order]
        if self._mode == PlayMode.SHUFFLE:
            self._shuffle_order.insert(0, index)
        if self._index >= index:
            self._index += 1
        self._rebuild_shuffle_if_needed()
        self._notify()
        return index

    def insert_next(self, track: Track) -> int:
        """插入到「下一首播放」。"""
        if self._index < 0:
            return self.append(track)
        return self.insert(self._index + 1, track)

    def remove_at(self, index: int) -> Optional[Track]:
        if not (0 <= index < len(self._tracks)):
            return None
        removed = self._tracks.pop(index)
        self._shuffle_order = [i - 1 if i > index else i for i in self._shuffle_order if i != index]
        if index < self._index:
            self._index -= 1
        elif index == self._index:
            self._index = min(self._index, len(self._tracks) - 1)
        self._history = [i for i in self._history if i != index]
        self._history = [i - 1 if i > index else i for i in self._history]
        self._rebuild_shuffle_if_needed()
        self._notify()
        return removed

    def move(self, src: int, dst: int) -> bool:
        n = len(self._tracks)
        if not (0 <= src < n) or not (0 <= dst < n) or src == dst:
            return False
        item = self._tracks.pop(src)
        self._tracks.insert(dst, item)
        # 同步当前下标
        if self._index == src:
            self._index = dst
        elif src < self._index <= dst:
            self._index -= 1
        elif dst <= self._index < src:
            self._index += 1
        self._rebuild_shuffle(start_at_current=True)
        self._notify()
        return True

    def clear(self) -> None:
        self._tracks.clear()
        self._shuffle_order.clear()
        self._history.clear()
        self._index = -1
        self._notify()

    def set_index(self, index: int, *, record_history: bool = True) -> bool:
        if not (0 <= index < len(self._tracks)):
            return False
        if self._index != index and 0 <= self._index < len(self._tracks):
            if record_history:
                self._history.append(self._index)
                if len(self._history) > 500:
                    del self._history[:100]
        self._index = index
        if self._mode == PlayMode.SHUFFLE and index in self._shuffle_order:
            self._shuffle_pos = self._shuffle_order.index(index)
        self._notify()
        return True

    # ── 随机顺序 ────────────────────────────────────────────

    def _rebuild_shuffle(self, *, start_at_current: bool = False) -> None:
        n = len(self._tracks)
        self._shuffle_order = list(range(n))
        random.shuffle(self._shuffle_order)
        if start_at_current and 0 <= self._index < n:
            try:
                pos = self._shuffle_order.index(self._index)
                self._shuffle_order.insert(0, self._shuffle_order.pop(pos))
            except ValueError:
                pass
        self._shuffle_pos = 0 if self._shuffle_order else -1

    def _rebuild_shuffle_if_needed(self) -> None:
        if self._mode == PlayMode.SHUFFLE and len(self._shuffle_order) != len(self._tracks):
            self._rebuild_shuffle(start_at_current=True)

    # ── 选取下一首 / 上一首 ─────────────────────────────────

    def next_index(self, *, manual: bool = False, auto_advance: bool = False) -> Optional[int]:
        """计算下一首的下标。

        Args:
            manual: 用户点击「下一首」——四种模式都回绕。
            auto_advance: 当前曲自然播完——顺序模式在末尾返回 ``None``，
                单曲循环返回当前下标。
        """
        n = len(self._tracks)
        if n == 0:
            return None
        if n == 1:
            return 0 if (manual or auto_advance) else None

        if self._mode == PlayMode.LOOP_SINGLE and auto_advance:
            return self._index if self._index >= 0 else 0

        if self._mode == PlayMode.SHUFFLE:
            if len(self._shuffle_order) != n:
                self._rebuild_shuffle(start_at_current=True)
            if self._shuffle_pos + 1 >= len(self._shuffle_order):
                # 一轮播完：重新洗牌，并避免立刻重复上一首
                last = self._shuffle_order[self._shuffle_pos] if self._shuffle_pos >= 0 else -1
                self._rebuild_shuffle()
                if len(self._shuffle_order) > 1 and self._shuffle_order[0] == last:
                    self._shuffle_order.append(self._shuffle_order.pop(0))
                self._shuffle_pos = -1
            self._shuffle_pos += 1
            return self._shuffle_order[self._shuffle_pos]

        cur = self._index if self._index >= 0 else 0
        nxt = cur + 1
        if nxt >= n:
            if self._mode == PlayMode.ORDER and auto_advance:
                return None
            nxt = 0
        return nxt

    def prev_index(self, *, manual: bool = True) -> Optional[int]:
        """计算上一首的下标：优先回溯真实播放历史。"""
        n = len(self._tracks)
        if n == 0:
            return None
        if n == 1:
            return 0

        if manual and self._history:
            while self._history:
                idx = self._history.pop()
                if 0 <= idx < n and idx != self._index:
                    return idx

        if self._mode == PlayMode.SHUFFLE:
            if self._shuffle_pos > 0:
                self._shuffle_pos -= 1
                return self._shuffle_order[self._shuffle_pos]
            return self._shuffle_order[-1] if self._shuffle_order else 0

        cur = self._index if self._index >= 0 else 0
        return (cur - 1) % n

    def advance(self, *, index: int) -> None:
        """提交一次切换（由播放引擎在真正开始播放前调用）。"""
        self.set_index(index)

    # ── 序列化 ──────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "mode": PLAY_MODE_NAMES.get(self._mode, "loop_list"),
            "index": self._index,
            "tracks": [t.to_dict() for t in self._tracks],
        }

    def load_dict(self, data: Optional[dict]) -> None:
        if not data:
            return
        self._mode = mode_from_name(data.get("mode", "loop_list"))
        tracks = [Track.from_dict(d) for d in data.get("tracks") or []]
        idx = int(data.get("index", 0) or 0)
        self.set_tracks(tracks, idx if tracks else 0)
