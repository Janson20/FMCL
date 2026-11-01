"""漫游控制器：一条持续生成的个性化推荐流。

跟歌手页 / 专辑页那种「点进去看一眼」的页面不同，漫游是**流**：

* ``refresh()`` 换一批（整条流换方向），``more()`` 追加一批（流继续往下走）；
* 从漫游页开始播放后，队列快放完时会**自动续上**（:mod:`app.core.roam` 决定时机），
  于是它永远不会停 —— 这就是「漫游」和「播放列表」的区别；
* 用户一旦去听别的歌（当前曲目不在漫游发出去的歌里），漫游自动交棒，
  不会偷偷往队列里塞歌。

推荐来源见 :func:`app.sources.netease.roam_batch`：登录时优先私人 FM。
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List, Set

from PySide6.QtCore import QObject, Property, Signal, Slot

from ..core import roam as roam_rules
from ..core.models import Track, TrackListModel
from ..sources import netease

logger = logging.getLogger(__name__)

# 首次进页面取多少首
INITIAL_SIZE = 15

class _Emitter(QObject):
    loaded = Signal(int, object)

class RoamController(QObject):
    """漫游流的取数与自动续歌。"""

    changed = Signal()
    errorOccurred = Signal(str)
    message = Signal(str)

    def __init__(self, config, player, parent=None):
        super().__init__(parent)
        self._config = config
        self._player = player
        self._emitter = _Emitter(self)
        self._emitter.loaded.connect(self._on_loaded)

        self._model = TrackListModel()
        self._loading = False
        self._seq = 0
        self._active = False
        # 漫游发出去过的歌（用来判断「用户还在听漫游吗」）
        self._served: Set[str] = set()
        self._append_mode = False
        self._requested = False

    # ── 属性 ────────────────────────────────────────────────

    @Property(QObject, constant=True)
    def model(self):
        return self._model

    @Property(bool, notify=changed)
    def loading(self) -> bool:
        return self._loading

    @Property(int, notify=changed)
    def count(self) -> int:
        return self._model.length()

    @Property(bool, notify=changed)
    def active(self) -> bool:
        """漫游还在续吗（正在放漫游流里的歌）。"""
        return self._active

    @Property(bool, notify=changed)
    def requested(self) -> bool:
        """已经取过至少一批了（页面用它避免每次切回来都重取）。"""
        return self._requested

    @Property(str, notify=changed)
    def hint(self) -> str:  # noqa: N802
        """给页面顶部显示的一句话。"""
        if self._active:
            return "漫游中 · 队列快放完时会自动续上"
        return "点「开始漫游」听这条流，它会一直往下续"

    # ── 取数 ────────────────────────────────────────────────

    @Slot()
    def refresh(self) -> None:
        """换一批（整条流换方向）。"""
        self._fetch(INITIAL_SIZE, append=False, reset=True)

    @Slot()
    def more(self) -> None:
        """追加一批（流继续往下走）。"""
        self._fetch(roam_rules.REFILL_SIZE, append=True)

    def _fetch(self, size: int, *, append: bool, reset: bool = False) -> None:
        if self._loading:
            return
        self._seq += 1
        seq = self._seq
        self._loading = True
        self._append_mode = append
        self._requested = True
        if reset:
            self._active = False
            self._served.clear()
        self.changed.emit()
        threading.Thread(
            target=self._worker, args=(seq, int(size)), daemon=True, name="roam"
        ).start()

    def _worker(self, seq: int, size: int) -> None:
        try:
            batch = netease.roam_batch(size)
        except Exception as e:
            logger.debug("漫游取数失败: %s", e)
            batch = []
        self._emitter.loaded.emit(seq, batch)

    @Slot(int, object)
    def _on_loaded(self, seq: int, batch) -> None:
        if seq != self._seq:
            return
        self._loading = False

        tracks: List[Track] = []
        for item in batch or []:
            info, reason = item if isinstance(item, tuple) else (item, "")
            tracks.append(self._to_track(info, reason))
        tracks = roam_rules.pick_playable(tracks)

        # 已经在列表里的不再重复塞（私人 FM 偶尔会回头给同一首）
        known = {t.uid for t in self._model.tracks()}
        fresh = [t for t in tracks if t.uid not in known]

        if self._append_mode:
            if fresh:
                self._model.extend(fresh)
            self.changed.emit()
            if fresh and self._active:
                self._enqueue(fresh)
            return

        self._model.set_tracks(fresh)
        self._served = {t.uid for t in fresh}
        self.changed.emit()
        if not fresh:
            self.errorOccurred.emit("没取到推荐，检查网络或先登录网易云账号")

    def _to_track(self, info, reason: str) -> Track:
        track = Track.from_music_info(info)
        track.reason = roam_rules.clean_reason(reason)
        return track

    # ── 播放 ────────────────────────────────────────────────

    @Slot(int)
    def playFrom(self, index: int = 0) -> None:
        """从第 index 首开始漫游（队列 = 当前这条流，之后会自动续）。"""
        tracks = self._model.tracks()
        if not tracks:
            self.errorOccurred.emit("漫游流还是空的，先换一批")
            return
        index = max(0, min(int(index), len(tracks) - 1))
        # 先把队列交给播放器，再打开漫游开关：换队列会先发 queueChanged，
        # 那一刻「当前曲目」还是上一首 —— 提前置 active 会被自己的交棒判断
        # 当成「用户去听别的歌了」，漫游刚开就关。
        self._player.playTrackInList([t.to_dict() for t in tracks], index)
        self._served = {t.uid for t in tracks}
        self._active = True
        self.changed.emit()

    @Slot()
    def start(self) -> None:
        self.playFrom(0)

    @Slot()
    def stop(self) -> None:
        """手动交棒（不再自动续歌）。"""
        if not self._active:
            return
        self._active = False
        self.changed.emit()
        self.message.emit("已停止漫游续歌")

    # ── 自动续歌 ────────────────────────────────────────────

    def watch_player(self) -> None:
        """接上播放器，队列快放完时续歌。"""
        if self._player is None:
            return
        self._player.trackChanged.connect(self._on_track_changed)
        self._player.queueChanged.connect(self._on_track_changed)

    @Slot()
    def _on_track_changed(self) -> None:
        if not self._active:
            return
        current = self._player.currentTrack if self._player is not None else None
        uid = str((current or {}).get("uid") or "") if isinstance(current, dict) else ""
        if uid and uid not in self._served:
            # 用户听的是别的歌，漫游交棒
            self._active = False
            self.changed.emit()
            return
        if not roam_rules.needs_refill(self._active, self._remaining(), self._loading):
            return
        self.more()

    def _remaining(self) -> int:
        try:
            return int(self._player.queueCount) - int(self._player.queueIndex) - 1
        except Exception:
            return 0

    def _enqueue(self, tracks: List[Track]) -> None:
        try:
            self._player.extendQueue([t.to_dict() for t in tracks])
        except Exception as e:
            logger.debug("漫游续歌入队失败: %s", e)

    # ── 杂项 ────────────────────────────────────────────────

    @Slot(result="QVariant")
    def stats(self):  # noqa: N802
        return {"count": self._model.length(), "active": self._active, "loading": self._loading}

    def _debug_state(self) -> Dict:
        return {
            "count": self._model.length(),
            "active": self._active,
            "loading": self._loading,
            "served": len(self._served),
        }
