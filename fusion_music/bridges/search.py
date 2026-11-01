"""多音源搜索控制器。"""

from __future__ import annotations

import logging
import math
import threading
from typing import Dict, List

try:
    from shiboken6 import isValid as _qt_valid
except Exception:  # pragma: no cover - 非 PySide 环境仅用于静态检查
    def _qt_valid(_obj):
        return True

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from ..core.models import Track, TrackListModel
from ..sources import DEFAULT_SEARCH_SOURCES, SOURCE_META, SOURCE_NAMES, get_source, search_one
from ..sources import netease

logger = logging.getLogger(__name__)

#: 搜索每页条数（与各音源 limits 的上限取小，界面分页按它算）
SEARCH_PAGE_SIZE = 20

class _Emitter(QObject):
    #: 单音源完成（seq, {source, results, total}）—— 每回来一个就刷一次列表。
    groupDone = Signal(int, object)
    suggestions = Signal(int, object)
    tops = Signal(int, object)

class SearchController(QObject):
    """并发搜索全部音源，并把结果按来源分页展示。"""

    resultsChanged = Signal()
    loadingChanged = Signal()
    #: 搜索状态（是否搜过 / 最后一次失败原因）变化。
    stateChanged = Signal()
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
        self._emitter.groupDone.connect(self._on_group_done)
        self._emitter.suggestions.connect(self._on_remote_suggestions)
        self._emitter.tops.connect(self._on_tops)

        self._model = TrackListModel()
        self._loading = False
        self._keyword = ""
        self._page = 1
        self._seq = 0
        self._current_source = "all"
        self._last_page_size = SEARCH_PAGE_SIZE
        self._by_source: Dict[str, List[Track]] = {}
        self._totals: Dict[str, int] = {}
        self._history: List[str] = list(config.get("search_history", []) or [])
        self._suggestions: List[Dict[str, str]] = []
        self._suggest_query = ""
        self._suggest_remote: List[str] = []
        self._suggest_cache: Dict[str, List[str]] = {}
        self._suggest_seq = 0
        self._has_more = False
        self._has_searched = False
        self._last_error = ""
        self._page_count = 1
        self._total_available = 0
        self._pending_sources: set[str] = set()
        self._alive = True
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

    @Property(bool, notify=stateChanged)
    def hasSearched(self) -> bool:  # noqa: N802
        """本次关键词是否已经有结果（成功或明确失败都算「搜过」）。"""
        return self._has_searched

    @Property(str, notify=stateChanged)
    def lastError(self) -> str:  # noqa: N802
        """最后一次搜索的机器可读状态码，翻译由桥负责。"""
        return self._last_error

    @Property(int, notify=resultsChanged)
    def pageCount(self) -> int:  # noqa: N802
        return self._page_count

    @Property(int, notify=resultsChanged)
    def totalCount(self) -> int:  # noqa: N802
        """各音源上报的总数之和；接口不报总数时为 0。"""
        return self._total_available

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
        tabs = [{
            "id": "all",
            "name": "全部",
            "nameKey": "music_source_all",
            "count": self._total_available or self._total_count(),
        }]
        for meta in SOURCE_META:
            sid = meta["id"]
            tracks = self._by_source.get(sid) or []
            # 当前选中的音源即使本页为空也要保留标签，否则用户会卡在一个
            # 已经从标签条上消失的音源里（切不回去，也看不出为什么空）。
            if not tracks and sid != self._current_source:
                continue
            tabs.append({
                "id": sid,
                "name": meta["short"],
                "nameKey": "music_source_" + sid,
                "count": int(self._totals.get(sid, len(tracks)) or len(tracks)),
            })
        return tabs

    def _total_count(self) -> int:
        return sum(len(v) for v in self._by_source.values())

    # ── 搜索 ────────────────────────────────────────────────

    @Slot(str)
    def search(self, keyword: str) -> None:  # noqa: A003
        keyword = (keyword or "").strip()
        if not keyword:
            self._last_error = "empty_keyword"
            self.stateChanged.emit()
            self.errorOccurred.emit("empty_keyword")
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
        self.goToPage(self._page + 1)

    @Slot()
    def prevPage(self) -> None:  # noqa: N802
        self.goToPage(self._page - 1)

    @Slot(int)
    def goToPage(self, page: int) -> None:  # noqa: N802
        """跳到指定页；页码夹在 [1, pageCount] 内，请求中不重复发。"""
        if not self._keyword or self._loading:
            return
        try:
            value = int(page)
        except (TypeError, ValueError):
            return
        value = max(1, min(value, self._page_count))
        if value == self._page:
            return
        self._page = value
        self._run()

    @Slot(str)
    def setSource(self, source_id: str) -> None:  # noqa: N802
        self._current_source = source_id or "all"
        self._apply_filter()
        self.sourceChanged.emit()
        self.resultsChanged.emit()

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
        self._has_searched = False
        self._last_error = ""
        self._page_count = 1
        self._total_available = 0
        self._loading = False
        self._top_artist = {}
        self._top_playlist = {}
        self.loadingChanged.emit()
        self.resultsChanged.emit()
        self.sourceChanged.emit()
        self.stateChanged.emit()
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

        self._has_searched = False
        self._last_error = ""
        self._loading = True
        self._by_source.clear()
        self._totals.clear()
        self._model.clear()
        self._has_more = False
        self._page_count = 1
        self._total_available = 0

        enabled = self._config.get("sources.enabled", None)
        ids: List[str] = []
        if isinstance(enabled, list) and enabled:
            ids = [s for s in enabled if s in SOURCE_NAMES]
        if not ids:
            ids = list(DEFAULT_SEARCH_SOURCES)
        self._pending_sources = set(ids)

        self.loadingChanged.emit()
        self.resultsChanged.emit()
        self.sourceChanged.emit()
        self.stateChanged.emit()
        if first_page:
            # 置顶卡片跟着关键词换，先清掉旧结果避免显示上一条关键词的歌手
            self._top_artist = {}
            self._top_playlist = {}
            self.topsChanged.emit()

        if not ids:
            self._finish_search(seq)
            return
        for sid in ids:
            threading.Thread(
                target=self._source_worker,
                args=(seq, sid, keyword, page),
                daemon=True,
                name="search-" + sid,
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
        if self._alive and _qt_valid(self._emitter):
            try:
                self._emitter.tops.emit(seq, payload)
            except RuntimeError:
                pass

    @Slot(int, object)
    def _on_tops(self, seq: int, payload) -> None:
        if not self._alive or seq != self._seq or not isinstance(payload, dict):
            return
        self._top_artist = dict(payload.get("artist") or {})
        self._top_playlist = dict(payload.get("playlist") or {})
        self.topsChanged.emit()

    def _source_worker(self, seq: int, source_id: str, keyword: str, page: int) -> None:
        """单个音源搜完就投递结果；不等其它音源。

        搜不到 / 抛异常都按空组投递，让「还有一个源没回来」的等待状态照常结束。
        """
        items = []
        total = 0
        try:
            src = get_source(source_id)
            if src is not None:
                items = search_one(source_id, keyword, page, SEARCH_PAGE_SIZE) or []
                total = int(getattr(src, "last_search_total", 0) or 0)
        except Exception as e:  # noqa: BLE001 - 单源失败不拖垮整次搜索
            logger.debug("[%s] 搜索失败: %s", source_id, e)
        if self._alive and _qt_valid(self._emitter):
            try:
                self._emitter.groupDone.emit(
                    seq, {"source": source_id, "results": items, "total": total}
                )
            except RuntimeError:
                pass

    @Slot(int, object)
    def _on_group_done(self, seq: int, payload) -> None:
        if not self._alive or seq != self._seq or not isinstance(payload, dict):
            return
        sid = str(payload.get("source") or "")
        if not sid:
            return
        tracks = [Track.from_music_info(mi) for mi in (payload.get("results") or [])]
        self._by_source[sid] = tracks
        self._totals[sid] = max(0, int(payload.get("total") or 0))
        self._pending_sources.discard(sid)

        self._last_page_size = SEARCH_PAGE_SIZE
        # 增量追加，不重设整个模型：每回来一个音源就落到列表尾部，
        # 也不会在 ListView 组装 delegate 时触发 DelegateModel::cancel。
        if self._current_source == "all" or self._current_source == sid:
            self._model.extend(tracks)
        self._recompute_pages(self._page)
        self.resultsChanged.emit()
        self.sourceChanged.emit()
        self.stateChanged.emit()

        if not self._pending_sources:
            self._finish_search(seq)

    def _finish_search(self, seq: int) -> None:
        if seq != self._seq:
            return
        self._loading = False
        self._has_searched = True
        self._last_error = "" if self._by_source else "no_results"
        # 接口不给总数时，用「是否有满页」判断还能不能翻页。
        if not self._totals or max(self._totals.values(), default=0) <= 0:
            self._has_more = any(
                len(v) >= SEARCH_PAGE_SIZE - 5 for v in self._by_source.values()
            )
        self._recompute_pages(self._page)
        self.loadingChanged.emit()
        self.resultsChanged.emit()
        self.sourceChanged.emit()
        self.stateChanged.emit()
        if not self._by_source:
            self.errorOccurred.emit("no_results")

    def _recompute_pages(self, page: int) -> None:
        """按各音源上报的总数算总页数；没有总数时按「还有下一页」递增。"""
        totals = [max(0, int(v or 0)) for v in self._totals.values()]
        max_total = max(totals) if totals else 0
        self._total_available = sum(totals)
        if max_total > 0:
            self._page_count = max(1, math.ceil(max_total / self._last_page_size))
        elif self._by_source:
            self._page_count = max(int(page), int(page) + (1 if self._has_more else 0))
        else:
            self._page_count = 1
        self._has_more = int(page) < self._page_count

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

    def shutdown(self) -> None:
        """窗口关闭时让在飞的搜索/联想结果作废并停掉防抖计时器。"""
        self._alive = False
        self._seq += 1
        self._suggest_seq += 1
        self._suggest_timer.stop()
        try:
            self._emitter.groupDone.disconnect()
            self._emitter.suggestions.disconnect()
            self._emitter.tops.disconnect()
        except (RuntimeError, TypeError):
            pass
