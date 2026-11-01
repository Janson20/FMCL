"""多音源搜索控制器。"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from ..core.models import Track, TrackListModel
from ..sources import SOURCE_META, SOURCE_NAMES, search_all
from ..sources import netease

logger = logging.getLogger(__name__)

class _Emitter(QObject):
    done = Signal(int, object)
    suggestions = Signal(int, object)
    tops = Signal(int, object)

class SearchController(QObject):
    """并发搜索全部音源，并把结果按来源分页展示。"""

    resultsChanged = Signal()
    loadingChanged = Signal()
    errorOccurred = Signal(str)
    sourceChanged = Signal()
    historyChanged = Signal()
    suggestionsChanged = Signal()
    topsChanged = Signal()

    MAX_HISTORY = 24
    #: 联想最多显示几条（含本地历史命中的那几条）
    SUGGEST_LIMIT = 8
    #: 本地历史最多贡献几条 —— 联想词是主角，历史只是"打第一个字就有东西可点"
    SUGGEST_HISTORY_LIMIT = 3
    #: 输入停下多久才去问接口。打字过程中每个字符发一次请求，既慢又容易被限流
    SUGGEST_DEBOUNCE_MS = 260
    #: 联想结果的小缓存条数（退格、改一个字时不用重新请求）
    SUGGEST_CACHE_LIMIT = 64

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._emitter = _Emitter(self)
        self._emitter.done.connect(self._on_done)
        self._emitter.suggestions.connect(self._on_remote_suggestions)
        self._emitter.tops.connect(self._on_tops)

        self._model = TrackListModel()
        self._loading = False
        self._keyword = ""
        self._page = 1
        self._seq = 0
        self._current_source = "all"
        self._last_page_size = 20
        self._by_source: Dict[str, List[Track]] = {}
        self._totals: Dict[str, int] = {}
        self._history: List[str] = list(config.get("search_history", []) or [])
        self._suggestions: List[Dict[str, str]] = []
        self._suggest_query = ""
        self._suggest_remote: List[str] = []
        self._suggest_cache: Dict[str, List[str]] = {}
        self._suggest_seq = 0
        self._has_more = False
        self._top_artist: Dict = {}
        self._top_playlist: Dict = {}

        # 防抖：输入框每敲一下都会调 suggest()，只有停下来才真的发请求
        self._suggest_timer = QTimer(self)
        self._suggest_timer.setSingleShot(True)
        self._suggest_timer.setInterval(self.SUGGEST_DEBOUNCE_MS)
        self._suggest_timer.timeout.connect(self._request_suggestions)

    # ── 属性 ────────────────────────────────────────────────

    @Property(QObject, constant=True)
    def model(self):
        return self._model

    @Property(bool, notify=loadingChanged)
    def loading(self) -> bool:
        return self._loading

    @Property(str, notify=resultsChanged)
    def keyword(self) -> str:
        return self._keyword

    @Property(int, notify=resultsChanged)
    def page(self) -> int:
        return self._page

    @Property(str, notify=sourceChanged)
    def currentSource(self) -> str:  # noqa: N802
        return self._current_source

    @Property(int, notify=resultsChanged)
    def resultCount(self) -> int:  # noqa: N802
        return self._model.length()

    @Property(bool, notify=resultsChanged)
    def hasResults(self) -> bool:  # noqa: N802
        return self._model.length() > 0

    @Property(bool, notify=resultsChanged)
    def hasMore(self) -> bool:  # noqa: N802
        return self._has_more

    @Property("QVariantList", notify=historyChanged)
    def history(self):  # noqa: N802
        return list(self._history)

    @Property("QVariantList", notify=suggestionsChanged)
    def suggestions(self):  # noqa: N802
        """搜索联想列表：``[{keyword, from}]``，``from`` 是 ``history`` / ``suggest``。

        界面靠 ``from`` 决定行首的小图标（时钟 / 放大镜），也便于将来分开排版。
        """
        return [dict(item) for item in self._suggestions]

    @Property("QVariant", notify=topsChanged)
    def topArtist(self):  # noqa: N802
        """搜索结果置顶的歌手卡片（「全部」标签页显示）。"""
        return dict(self._top_artist)

    @Property("QVariant", notify=topsChanged)
    def topPlaylist(self):  # noqa: N802
        """搜索结果置顶的歌单卡片。"""
        return dict(self._top_playlist)

    @Property("QVariantList", notify=sourceChanged)
    def sourceTabs(self):  # noqa: N802
        tabs = [{"id": "all", "name": "全部", "count": self._total_count()}]
        for meta in SOURCE_META:
            sid = meta["id"]
            if sid not in self._by_source or not self._by_source[sid]:
                continue
            tabs.append(
                {"id": sid, "name": meta["short"], "count": len(self._by_source[sid])}
            )
        return tabs

    def _total_count(self) -> int:
        return sum(len(v) for v in self._by_source.values())

    # ── 搜索 ────────────────────────────────────────────────

    @Slot(str)
    def search(self, keyword: str) -> None:  # noqa: A003
        keyword = (keyword or "").strip()
        if not keyword:
            self.errorOccurred.emit("请输入搜索关键词")
            return
        # 真发起搜索了就把联想收掉：防抖计时器停掉、在飞的请求作废，
        # 否则结果都出来了联想框还在下面杵着
        self.clearSuggestions()
        self._keyword = keyword
        self._page = 1
        self._push_history(keyword)
        self._run()

    @Slot()
    def nextPage(self) -> None:  # noqa: N802
        if not self._keyword or self._loading:
            return
        self._page += 1
        self._run()

    @Slot()
    def prevPage(self) -> None:  # noqa: N802
        if not self._keyword or self._loading or self._page <= 1:
            return
        self._page -= 1
        self._run()

    @Slot(str)
    def setSource(self, source_id: str) -> None:  # noqa: N802
        self._current_source = source_id or "all"
        self._apply_filter()
        self.sourceChanged.emit()

    @Slot()
    def retry(self) -> None:
        if self._keyword:
            self._run()

    @Slot()
    def clear(self) -> None:
        self._seq += 1
        self.clearSuggestions()
        self._keyword = ""
        self._page = 1
        self._by_source.clear()
        self._totals.clear()
        self._model.clear()
        self._has_more = False
        self._loading = False
        self._top_artist = {}
        self._top_playlist = {}
        self.loadingChanged.emit()
        self.resultsChanged.emit()
        self.sourceChanged.emit()
        self.topsChanged.emit()

    @Slot(str)
    def removeHistory(self, keyword: str) -> None:  # noqa: N802
        if keyword in self._history:
            self._history.remove(keyword)
            self._config.set("search_history", self._history)
            self.historyChanged.emit()

    @Slot()
    def clearHistory(self) -> None:  # noqa: N802
        self._history.clear()
        self._config.set("search_history", [])
        self.historyChanged.emit()

    def _push_history(self, keyword: str) -> None:
        if keyword in self._history:
            self._history.remove(keyword)
        self._history.insert(0, keyword)
        del self._history[self.MAX_HISTORY:]
        self._config.set("search_history", self._history)
        self.historyChanged.emit()

    def _run(self) -> None:
        self._seq += 1
        seq = self._seq
        page = self._page
        keyword = self._keyword
        first_page = page == 1

        self._loading = True
        self.loadingChanged.emit()
        if first_page:
            # 置顶卡片跟着关键词换，先清掉旧结果避免显示上一条关键词的歌手
            self._top_artist = {}
            self._top_playlist = {}
            self.topsChanged.emit()
        threading.Thread(
            target=self._worker, args=(seq, keyword, page), daemon=True, name="search"
        ).start()
        if first_page:
            threading.Thread(
                target=self._top_worker, args=(seq, keyword), daemon=True, name="search-top"
            ).start()

    def _top_worker(self, seq: int, keyword: str) -> None:
        """置顶的歌手 / 歌单卡片（只有网易云有这类「歌手实体」）。"""
        payload: Dict = {}
        try:
            artists = netease.search_artists(keyword, 5)
            if artists:
                payload["artist"] = netease.pick_artist(artists, keyword)
        except Exception as e:
            logger.debug("置顶歌手搜索失败: %s", e)
        try:
            playlists = netease.search_playlists(keyword, 1)
            if playlists:
                payload["playlist"] = playlists[0]
        except Exception as e:
            logger.debug("置顶歌单搜索失败: %s", e)
        self._emitter.tops.emit(seq, payload)

    @Slot(int, object)
    def _on_tops(self, seq: int, payload) -> None:
        if seq != self._seq or not isinstance(payload, dict):
            return
        self._top_artist = dict(payload.get("artist") or {})
        self._top_playlist = dict(payload.get("playlist") or {})
        self.topsChanged.emit()

    def _worker(self, seq: int, keyword: str, page: int) -> None:
        try:
            enabled = self._config.get("sources.enabled", None)
            ids = None
            if isinstance(enabled, list) and enabled:
                ids = [s for s in enabled if s in SOURCE_NAMES]
            groups = search_all(keyword, page=page, limit=20, source_ids=ids)
        except Exception as e:
            logger.exception("搜索失败")
            groups = []
            self._emitter.done.emit(seq, {"error": str(e)})
            return
        self._emitter.done.emit(seq, {"groups": groups, "page": page})

    @Slot(int, object)
    def _on_done(self, seq: int, payload) -> None:
        if seq != self._seq:
            return
        self._loading = False
        self.loadingChanged.emit()

        if not isinstance(payload, dict) or "groups" not in payload:
            self.errorOccurred.emit("搜索失败，请检查网络连接")
            return

        page = int(payload.get("page") or 1)
        groups = payload.get("groups") or []
        self._by_source.clear()
        self._totals.clear()
        any_total = 0
        for g in groups:
            sid = g.get("source") or ""
            tracks = [Track.from_music_info(mi) for mi in (g.get("results") or [])]
            if sid and tracks:
                self._by_source[sid] = tracks
                total = int(g.get("total") or 0)
                self._totals[sid] = total
                any_total = max(any_total, total)

        self._last_page_size = 20
        if any_total > 0:
            self._has_more = page * self._last_page_size < any_total
        else:
            # 接口不提供总数时，只要还有任意音源返回满页就认为可能有下一页
            self._has_more = any(len(v) >= 15 for v in self._by_source.values())

        self._apply_filter()
        self.resultsChanged.emit()
        self.sourceChanged.emit()

        if not self._by_source:
            self.errorOccurred.emit(f"没有找到「{self._keyword}」的相关结果")

    def _apply_filter(self) -> None:
        if self._current_source == "all":
            merged: List[Track] = []
            for meta in SOURCE_META:
                merged.extend(self._by_source.get(meta["id"], []))
            # 网易云优先已在 sourceTabs 顺序里体现；「全部」页按音源顺序拼接
            self._model.set_tracks(merged)
        else:
            self._model.set_tracks(self._by_source.get(self._current_source, []))

    # ── 搜索联想（网易云「猜你想搜」+ 本地历史）─────────────

    @Slot(str)
    def suggest(self, text: str) -> None:
        """输入变化时调用：先给本地历史的即时命中，再防抖去问网易云。

        UI 在 ``onTextEdited`` 里调它（程序改 ``text`` 不会触发，所以点联想词
        回填不会自己再弹一次）。
        """
        query = (text or "").strip()
        self._suggest_query = query
        self._suggest_timer.stop()
        if not query:
            self._suggest_remote = []
            self._publish_suggestions()
            return

        cached = self._suggest_cache.get(query)
        if cached is not None:
            self._suggest_remote = list(cached)
            self._publish_suggestions()
            return

        # 历史命中的先顶上（本地、不联网，打第一个字就有东西可点）
        self._suggest_remote = []
        self._publish_suggestions()
        self._suggest_timer.start()

    @Slot()
    def clearSuggestions(self) -> None:  # noqa: N802
        """收起联想（搜索发起、按 Esc、点空白处都走这里）。"""
        self._suggest_timer.stop()
        self._suggest_seq += 1        # 让在飞的请求作废
        self._suggest_query = ""
        self._suggest_remote = []
        self._publish_suggestions()

    def _request_suggestions(self) -> None:
        query = self._suggest_query
        if not query:
            return
        self._suggest_seq += 1
        seq = self._suggest_seq
        threading.Thread(
            target=self._suggest_worker, args=(seq, query), daemon=True, name="search-suggest"
        ).start()

    def _suggest_worker(self, seq: int, query: str) -> None:
        try:
            items = netease.search_suggest(query, limit=self.SUGGEST_LIMIT)
        except Exception as e:  # pragma: no cover - netease 内部已吞异常，这里兜底
            logger.debug("搜索联想失败: %s", e)
            items = []
        self._emitter.suggestions.emit(seq, {"query": query, "items": items})

    @Slot(int, object)
    def _on_remote_suggestions(self, seq: int, payload) -> None:
        if seq != self._suggest_seq or not isinstance(payload, dict):
            return
        query = str(payload.get("query") or "")
        if query != self._suggest_query:
            return                     # 用户又敲了别的字，这份结果已经过期
        items = [str(x) for x in (payload.get("items") or [])]
        self._remember_suggestions(query, items)
        self._suggest_remote = items
        self._publish_suggestions()

    def _remember_suggestions(self, query: str, items: List[str]) -> None:
        """记住这一条查询的联想结果（有上限，退格回去不用重问）。"""
        try:
            self._suggest_cache[query] = list(items)
            while len(self._suggest_cache) > self.SUGGEST_CACHE_LIMIT:
                self._suggest_cache.pop(next(iter(self._suggest_cache)))
        except Exception as e:  # pragma: no cover - 缓存坏了不该影响联想
            logger.debug("缓存联想结果失败: %s", e)

    def _publish_suggestions(self) -> None:
        """把「本地历史命中 + 接口联想词」合成一份给界面（历史在前，去重）。"""
        query = self._suggest_query.lower()
        merged: List[Dict[str, str]] = []
        seen = set()
        if query:
            for item in self._history:
                if len(merged) >= self.SUGGEST_HISTORY_LIMIT:
                    break
                if query in item.lower() and item not in seen:
                    seen.add(item)
                    merged.append({"keyword": item, "from": "history"})
        for item in self._suggest_remote:
            if len(merged) >= self.SUGGEST_LIMIT:
                break
            if item and item not in seen:
                seen.add(item)
                merged.append({"keyword": item, "from": "suggest"})
        if merged == self._suggestions:
            return
        self._suggestions = merged
        self.suggestionsChanged.emit()

    @Slot(str, int, result="QVariant")
    def resultAt(self, source_id: str, index: int):  # noqa: N802
        tracks = self._by_source.get(source_id) or []
        if 0 <= index < len(tracks):
            return tracks[index].to_dict()
        return None
