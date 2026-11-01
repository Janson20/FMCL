"""发现页控制器：推荐歌单 / 排行榜 / 新歌 / 每日推荐 / 网易云账号歌单。"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List

from PySide6.QtCore import QObject, Property, Signal, Slot

from ..core.models import Track, TrackListModel
from ..sources import netease

logger = logging.getLogger(__name__)

class _Emitter(QObject):
    loaded = Signal(str, object)
    detailLoaded = Signal(str, object)

class DiscoverController(QObject):
    """发现页数据。所有接口都尽力而为，失败时返回空列表并由 UI 降级。"""

    dataChanged = Signal()
    loadingChanged = Signal()
    detailChanged = Signal()
    playlistChanged = Signal()
    errorOccurred = Signal(str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._emitter = _Emitter(self)
        self._emitter.loaded.connect(self._on_loaded)
        self._emitter.detailLoaded.connect(self._on_detail_loaded)

        self._recommend: List[Dict] = []
        self._toplists: List[Dict] = []
        self._new_songs_model = TrackListModel()
        self._daily_model = TrackListModel()
        self._remote_model = TrackListModel()
        self._detail_model = TrackListModel()
        self._loading = False
        self._detail_loading = False
        self._detail_id = ""
        self._detail_meta: Dict = {}
        self._loaded_once = False

    # ── 属性 ────────────────────────────────────────────────

    @Property(QObject, constant=True)
    def newSongsModel(self):  # noqa: N802
        return self._new_songs_model

    @Property(QObject, constant=True)
    def dailyModel(self):  # noqa: N802
        return self._daily_model

    @Property(QObject, constant=True)
    def remoteModel(self):  # noqa: N802
        return self._remote_model

    @Property(QObject, constant=True)
    def detailModel(self):  # noqa: N802
        return self._detail_model

    @Property("QVariantList", notify=dataChanged)
    def recommendPlaylists(self):  # noqa: N802
        return list(self._recommend)

    @Property("QVariantList", notify=dataChanged)
    def toplists(self):  # noqa: N802
        return list(self._toplists)

    @Property(bool, notify=loadingChanged)
    def loading(self) -> bool:
        return self._loading

    @Property(bool, notify=detailChanged)
    def detailLoading(self) -> bool:  # noqa: N802
        return self._detail_loading

    @Property(str, notify=detailChanged)
    def detailId(self) -> str:  # noqa: N802
        return self._detail_id

    @Property("QVariant", notify=detailChanged)
    def detailMeta(self):  # noqa: N802
        return dict(self._detail_meta)

    @Property(bool, notify=dataChanged)
    def hasDaily(self) -> bool:  # noqa: N802
        return self._daily_model.length() > 0

    # ── 加载 ────────────────────────────────────────────────

    @Slot()
    def load(self) -> None:
        if self._loading:
            return
        self._loading = True
        self.loadingChanged.emit()
        threading.Thread(target=self._load_worker, daemon=True, name="discover").start()

    @Slot()
    def reload(self) -> None:
        self._loaded_once = False
        self.load()

    def _load_worker(self) -> None:
        result: Dict[str, object] = {}
        try:
            result["recommend"] = netease.recommend_playlists(30)
        except Exception as e:
            logger.debug("推荐歌单加载失败: %s", e)
            result["recommend"] = []
        try:
            result["toplists"] = netease.toplists()
        except Exception as e:
            logger.debug("排行榜加载失败: %s", e)
            result["toplists"] = []
        try:
            result["new_songs"] = netease.new_songs(18)
        except Exception as e:
            logger.debug("新歌加载失败: %s", e)
            result["new_songs"] = []
        try:
            result["daily"] = netease.daily_recommend()
        except Exception as e:
            logger.debug("每日推荐加载失败: %s", e)
            result["daily"] = []
        self._emitter.loaded.emit("discover", result)

    @Slot(str, object)
    def _on_loaded(self, _kind: str, result) -> None:
        self._loading = False
        self.loadingChanged.emit()
        if not isinstance(result, dict):
            return
        self._recommend = list(result.get("recommend") or [])
        self._toplists = list(result.get("toplists") or [])
        self._new_songs_model.set_tracks(
            [Track.from_music_info(mi) for mi in (result.get("new_songs") or [])]
        )
        self._daily_model.set_tracks(
            [Track.from_music_info(mi) for mi in (result.get("daily") or [])]
        )
        self._loaded_once = True
        self.dataChanged.emit()
        if not self._recommend and not self._toplists:
            self.errorOccurred.emit("发现页数据加载失败，请检查网络后重试")

    # ── 歌单详情 ────────────────────────────────────────────

    @Slot(str, str)
    def openPlaylist(self, playlist_id: str, name: str = "") -> None:  # noqa: N802
        playlist_id = str(playlist_id or "").strip()
        if not playlist_id:
            return
        self._detail_id = playlist_id
        self._detail_meta = {"id": playlist_id, "name": name}
        self._detail_loading = True
        self._detail_model.clear()
        self.detailChanged.emit()
        threading.Thread(
            target=self._detail_worker, args=(playlist_id,), daemon=True, name="playlist-detail"
        ).start()

    def _detail_worker(self, playlist_id: str) -> None:
        meta: Dict = {}
        tracks: List[Track] = []
        try:
            meta = netease.playlist_meta(playlist_id)
        except Exception as e:
            logger.debug("歌单信息加载失败: %s", e)
        try:
            tracks = [Track.from_music_info(mi) for mi in netease.playlist_tracks(playlist_id)]
        except Exception as e:
            logger.debug("歌单歌曲加载失败: %s", e)
        self._emitter.detailLoaded.emit(playlist_id, {"meta": meta, "tracks": tracks})

    @Slot(str, object)
    def _on_detail_loaded(self, playlist_id: str, payload) -> None:
        if playlist_id != self._detail_id:
            return
        self._detail_loading = False
        if isinstance(payload, dict):
            meta = payload.get("meta") or {}
            tracks = payload.get("tracks") or []
            if meta:
                self._detail_meta = dict(meta)
            self._detail_model.set_tracks(tracks)
        self.detailChanged.emit()
        if self._detail_model.length() == 0:
            self.errorOccurred.emit("该歌单没有可显示的歌曲（部分歌单需要登录后才能读取）")

    @Slot()
    def closePlaylist(self) -> None:  # noqa: N802
        self._detail_id = ""
        self._detail_meta = {}
        self._detail_model.clear()
        self._detail_loading = False
        self.detailChanged.emit()
