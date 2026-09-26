"""任务 1.4-B（音乐在线侧服务化）的离线回归测试。

规则：**不联网、不真下载、不真放声音、不开窗口、不弹窗**，也不要求机器上有
pygame / mutagen / winsdk / ffmpeg（一律用假替身）；文件只写 `tmp_path`。
网络入口一律注入替身：`DownloadContext(http_get=...)` / `fetch_cover_bytes(http_get=...)` /
`WyRemoteApi` 替身 / `monkeypatch.setattr(music_source, "MUSIC_SOURCES", ...)`。

覆盖范围（与任务书的验收清单逐条对应）：
    - 在线搜索编排（准入、单源搜索与异常降级、结果归一化、分页切片与页码钳制）
      —— `TestSearchPlan` / `TestRunSourceSearch` / `TestSummarizeSearch` / `TestPagerPlan`
    - 自动音质解析（各档位与回退顺序、音质信息补齐）—— `TestAutoQuality*`
    - 下载回退（候选顺序、逐源失败后继续、文件头/时长校验、全部失败）—— `TestFetchOnlineSong*`
    - 临时文件命名与清理（含异常路径不残留）—— `TestDownloadToTemp` / `TestTempFileRules`
    - 网易云远程歌单（事件流、分页、只读约定）—— `TestWyRemote*`
    - 封面获取的数据准备 —— `TestFetchCoverBytes`
    - 服务零 UI 依赖 + 可脱离 AppContext 构造 —— 文件末尾三组

**关于任务书里的一处前提偏差**（详见 `poc/_verify_1_4b_music.py` 的报告）：
任务书说在线搜索编排包含「`search_all` 的调用与结果归一化、去重」。实测
`ui/app_music.py` 只在导入行出现过 `search_all`，**从未调用**：真正的在线检索是
**单音源**的（`src.search()` 打在 `_music_selected_source` 上），多音源只出现在
**播放期的跨源兜底** `resolve_track()`（1.4 已搬进 `services/music_source/`，
见 `tests/test_music_fallback.py::TestResolveTrack`）。因此本文件测的是真实存在的
那段（单源搜索 + 兜底多源），没有为不存在的代码造测试。
"""

from __future__ import annotations

import ast
import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.music_audio as ma  # noqa: E402
import services.music_download as md  # noqa: E402
import services.music_online as mo  # noqa: E402
import services.music_source as ms  # noqa: E402
import services.music_wy_remote as wy  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# 替身
# ══════════════════════════════════════════════════════════════════════


class FakeInfo:
    """音源搜索结果条目（只用到服务读的那几个字段，字段名与 `MusicInfo` 一致）。"""

    def __init__(
        self,
        name="七里香",
        singer="周杰伦",
        source="kw",
        songmid="m1",
        interval=240,
        types=None,
        qualities=None,
        play_count=0,
        is_original=False,
        original_name="",
        img="",
        album_name="",
    ):
        self.name = name
        self.singer = singer
        self.source = source
        self.songmid = songmid
        self.interval = interval
        #: 可用音质列表（真实 `MusicInfo.types` 是 `List[Dict]`，每项形如 {"type": "320k"}）
        if types is not None:
            self.types = list(types)
        elif qualities is not None:
            self.types = [{"type": q} for q in qualities]
        else:
            self.types = []
        self.play_count = play_count
        self.is_original = is_original
        self.original_name = original_name
        self.img = img
        self.album_name = album_name


class FakeSource:
    """音源替身：记录调用序列，可配置抛异常/可播放 URL/可用音质。"""

    def __init__(
        self,
        source_id="kw",
        items=None,
        total=0,
        urls=None,
        qualities=None,
        search_error=None,
        url_error=None,
        best_error=None,
        headers=None,
        cookies=None,
        source_name=None,
        limits=None,
    ):
        self.source_id = source_id
        self.items = list(items) if items is not None else []
        self.last_search_total = total
        self.urls = dict(urls or {})
        self.types = [{"type": q} for q in (qualities or [])]
        self.search_error = search_error
        self.url_error = url_error
        self.best_error = best_error
        self.headers_value = headers
        self.cookies_value = cookies
        if source_name is not None:
            self.source_name = source_name
        self.limits = dict(limits) if limits is not None else {"search": 30, "lyric": 1, "url": 1}
        self.search_calls = []
        self.url_calls = []
        self.best_calls = []

    def search(self, keyword, page=1, limit=30):
        self.search_calls.append((keyword, page, limit))
        if self.search_error is not None:
            raise self.search_error
        return list(self.items)

    def get_music_url(self, info, quality="128k"):
        self.url_calls.append((info, quality))
        if self.url_error is not None:
            raise self.url_error
        return self.urls.get(quality)

    def get_best_quality(self, info, preferred="128k"):
        self.best_calls.append((info, preferred))
        if self.best_error is not None:
            raise self.best_error
        # 与 services/music_source/base.py 的真实实现同语义：
        # 取 info.types 里声明过的档位，preferred 优先，否则按 QualityLevel.ALL 从高到低；
        # 一个档位都没有 -> "128k"（**不是** preferred 本身）
        available = {t.get("type") for t in info.types}
        if not available:
            return "128k"
        if preferred in available:
            return preferred
        for quality in ("flac24bit", "flac", "320k", "128k"):
            if quality in available:
                return quality
        return "128k"

    def get_download_headers(self):
        if isinstance(self.headers_value, Exception):
            raise self.headers_value
        return dict(self.headers_value or {})

    def get_download_cookies(self):
        if isinstance(self.cookies_value, Exception):
            raise self.cookies_value
        return self.cookies_value


class FakeResponse:
    """`requests.get` 的响应替身。"""

    def __init__(self, content=b"", content_type="audio/mpeg", status_code=200, chunks=None):
        self.content = content
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self._chunks = list(chunks) if chunks is not None else [content] if content else []

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=65536):
        for chunk in self._chunks:
            yield chunk


def make_getter(content=b"\x00" * 64, content_type="audio/mpeg", status_code=200, calls=None):
    """造一个假的 `requests.get`：记录调用参数，返回固定响应。"""

    def _get(url, **kwargs):
        if calls is not None:
            calls.append((url, kwargs))
        return FakeResponse(content, content_type, status_code)

    return _get


def make_ctx(tmp_path=None, temp_files=None, **kwargs):
    """造一个下载上下文（默认音源表/HTTP/转码都用替身，绝不联网）。"""
    return md.DownloadContext(
        temp_files=list(temp_files) if temp_files is not None else [],
        http_get=kwargs.pop("http_get", make_getter()),
        transcoder=kwargs.pop("transcoder", lambda path: None),
        **kwargs,
    )


@pytest.fixture(autouse=True)
def offline_guard(monkeypatch):
    """硬闸门：本文件**任何**测试都不许走真实网络。

    1.4-B 的硬要求是"全部离线"。这里把三处真实网络入口换成"一调就炸"，
    于是"忘了注入替身"会立刻变红，而不是悄悄打一圈网络 —— 第一版就是这么漏的：
    `fetch_online_song` 有几条用例没注入 `resolve`，`resolve_fallback` 便调了真实的
    `music_source.resolve_track`，真的去搜了一圈 QQ 音乐/B站。

    被测代码里的**补丁点**与这里一致：`services.music_online.requests` /
    `services.music_download.requests` 是同一个 `requests` 模块；
    `WyRemoteApi` 的默认后端也是在调用时读 `services.music_source.wy_*`。
    """

    def _forbidden(*args, **kwargs):
        raise AssertionError("测试禁止联网：请注入替身（sources / http_get / resolve / api）")

    monkeypatch.setattr(mo.requests, "get", _forbidden)
    monkeypatch.setattr(md.requests, "get", _forbidden)
    monkeypatch.setattr(ms, "resolve_track", _forbidden)
    monkeypatch.setattr(ms, "search_all", _forbidden)
    monkeypatch.setattr(ms, "wy_is_logged_in", _forbidden)
    monkeypatch.setattr(ms, "wy_get_user_playlists", _forbidden)
    monkeypatch.setattr(ms, "wy_get_playlist_tracks", _forbidden)
    return _forbidden


# ══════════════════════════════════════════════════════════════════════
# 1. 搜索准入与页码守卫
# ══════════════════════════════════════════════════════════════════════


class TestSourceLookup:
    def test_get_source_reads_live_music_sources(self, monkeypatch):
        """补丁点必须是**真正被读的那个名字**：`services.music_source.MUSIC_SOURCES`。"""
        fake = FakeSource("kw")
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"kw": fake})
        assert mo.get_source("kw") is fake
        assert mo.get_source("nope") is None

    def test_get_source_explicit_table_wins(self):
        fake = FakeSource("kw")
        assert mo.get_source("kw", sources={"kw": fake}) is fake
        assert mo.get_source("kw", sources={}) is None

    def test_search_page_size_from_source_limits(self):
        """每页条数按音源服务端限制（网易云每页最多 20 条那类）。"""
        src = FakeSource("wy", limits={"search": 20})
        assert mo.search_page_size("wy", sources={"wy": src}) == 20

    def test_search_page_size_missing_source_falls_back(self):
        assert mo.search_page_size("nope", sources={}) == mo.DEFAULT_SEARCH_PAGE_SIZE
        assert mo.search_page_size("nope", sources={}, default=7) == 7

    def test_search_page_size_missing_limit_key_uses_default(self):
        # 记录现状：limits 里没有 "search" 键就走默认值（原文 `limits.get("search", 30)`）
        src = FakeSource("kw", limits={})
        assert mo.search_page_size("kw", sources={"kw": src}) == mo.DEFAULT_SEARCH_PAGE_SIZE

    def test_get_source_total_missing_source_is_zero(self):
        assert mo.get_source_total("nope", sources={}) == 0
        assert mo.get_source_total("kw", sources={"kw": FakeSource("kw", total=42)}) == 42


class TestSearchPlan:
    def test_busy_rejects_new_request(self):
        assert mo.plan_search(page=1, busy=True, source_id="kw", sources={}) is None

    def test_page_below_one_rejected(self):
        assert mo.plan_search(page=0, busy=False, source_id="kw", sources={}) is None
        assert mo.plan_search(page=-3, busy=False, source_id="kw", sources={}) is None

    def test_plan_carries_page_and_page_size(self):
        src = FakeSource("wy", limits={"search": 20})
        plan = mo.plan_search(page=3, busy=False, source_id="wy", sources={"wy": src})
        assert plan is not None
        assert plan.page == 3
        assert plan.page_size == 20
        # 新请求未返回前不沿用旧音源的页数，且立刻进入请求态
        assert plan.reset_total_pages is True
        assert plan.busy is True

    def test_first_page_constant(self):
        assert mo.FIRST_SEARCH_PAGE == 1


class TestGoPage:
    def test_normal_target(self):
        assert mo.go_page_target(
            page=2, current_page=1, busy=False, keyword="七里香", has_results=True
        ) == 2

    def test_rejected_when_busy(self):
        assert mo.go_page_target(
            page=2, current_page=1, busy=True, keyword="a", has_results=True
        ) is None

    def test_rejected_when_page_below_one(self):
        assert mo.go_page_target(
            page=0, current_page=1, busy=False, keyword="a", has_results=True
        ) is None

    def test_rejected_without_keyword(self):
        assert mo.go_page_target(
            page=2, current_page=1, busy=False, keyword="", has_results=True
        ) is None

    def test_rejected_on_same_page_with_results(self):
        assert mo.go_page_target(
            page=1, current_page=1, busy=False, keyword="a", has_results=True
        ) is None

    def test_same_page_allowed_when_results_empty(self):
        # 记录现状：当前页没有结果时，重新点同一页会再发一次请求（原文如此）
        assert mo.go_page_target(
            page=1, current_page=1, busy=False, keyword="a", has_results=False
        ) == 1

    def test_neighbor_page(self):
        assert mo.neighbor_page(1, -1) == 0
        assert mo.neighbor_page(1, +1) == 2
        assert mo.neighbor_page(5, -1) == 4


class TestNormalizeKeyword:
    def test_strips_whitespace(self):
        assert mo.normalize_keyword("  七里香  ") == "七里香"

    def test_blank_becomes_empty(self):
        assert mo.normalize_keyword("   ") == ""
        assert mo.normalize_keyword("") == ""
        assert mo.normalize_keyword(None) == ""


# ══════════════════════════════════════════════════════════════════════
# 2. 单音源搜索与异常降级
# ══════════════════════════════════════════════════════════════════════


class TestRunSourceSearch:
    def test_returns_source_items(self):
        items = [FakeInfo(songmid="a"), FakeInfo(songmid="b")]
        src = FakeSource("kw", items=items)
        got = mo.run_source_search("kw", "七里香", 2, 30, sources={"kw": src})
        assert got == items
        assert src.search_calls == [("七里香", 2, 30)]

    def test_missing_source_returns_empty(self):
        assert mo.run_source_search("nope", "k", 1, 30, sources={}) == []

    def test_source_exception_degrades_to_empty(self):
        src = FakeSource("kw", search_error=RuntimeError("接口 500"))
        assert mo.run_source_search("kw", "k", 1, 30, sources={"kw": src}) == []

    def test_empty_result_is_not_an_error(self):
        assert mo.run_source_search("kw", "k", 1, 30, sources={"kw": FakeSource("kw")}) == []


class TestSummarizeSearch:
    def _src(self, total):
        return FakeSource("wy", total=total)

    def test_total_pages_from_source_total(self):
        out = mo.summarize_search(
            [FakeInfo()] * 20, source_id="wy", page=1, page_size=20,
            sources={"wy": self._src(45)},
        )
        assert out.total_pages == 3
        assert out.has_more is True
        assert out.status == mo.STATUS_RESULTS
        assert out.count == 20

    def test_last_page_has_no_more(self):
        out = mo.summarize_search(
            [FakeInfo()] * 5, source_id="wy", page=3, page_size=20,
            sources={"wy": self._src(45)},
        )
        assert out.total_pages == 3
        assert out.has_more is False

    def test_total_pages_minimum_one(self):
        out = mo.summarize_search(
            [], source_id="wy", page=1, page_size=20, sources={"wy": self._src(0)},
        )
        # 音源没给总数 -> 0（未知），不是 1
        assert out.total_pages == 0

    def test_heuristic_full_page_implies_more(self):
        out = mo.summarize_search(
            [FakeInfo()] * 30, source_id="kw", page=1, page_size=30,
            sources={"kw": FakeSource("kw", total=0)},
        )
        assert out.total_pages == 0
        assert out.has_more is True

    def test_heuristic_partial_page_is_last(self):
        out = mo.summarize_search(
            [FakeInfo()] * 7, source_id="kw", page=1, page_size=30,
            sources={"kw": FakeSource("kw", total=0)},
        )
        assert out.has_more is False

    def test_empty_first_page_status(self):
        out = mo.summarize_search(
            [], source_id="kw", page=1, page_size=30,
            sources={"kw": FakeSource("kw", total=0)},
        )
        assert out.status == mo.STATUS_EMPTY
        assert out.count == 0

    def test_empty_later_page_status(self):
        out = mo.summarize_search(
            [], source_id="kw", page=4, page_size=30,
            sources={"kw": FakeSource("kw", total=0)},
        )
        assert out.status == mo.STATUS_NO_MORE

    def test_explicit_total_overrides_source(self):
        """显式 total 优先（供 QML 侧/测试直接喂数，不去读音源表）。"""
        out = mo.summarize_search([], source_id="nope", page=1, page_size=20, total=41, sources={})
        assert out.total_pages == 3
        assert out.has_more is True


class TestPagerPlan:
    def test_pages_from_total_pages(self):
        plan = mo.pager_plan(current_page=2, total_pages=5, has_more=True, busy=False)
        assert plan.pages == [1, 2, 3, 4, 5]
        assert plan.current_page == 2
        assert plan.prev_enabled is True
        assert plan.next_enabled is True

    def test_heuristic_guesses_next_page(self):
        # 音源没给总数（total_pages=0）但满页 -> 只画到当前页 + 1
        plan = mo.pager_plan(current_page=2, total_pages=0, has_more=True, busy=False)
        assert plan.pages == [1, 2, 3]

    def test_last_known_page_only(self):
        plan = mo.pager_plan(current_page=3, total_pages=0, has_more=False, busy=False)
        assert plan.pages == [1, 2, 3]
        assert plan.next_enabled is False

    def test_first_page_prev_disabled(self):
        plan = mo.pager_plan(current_page=1, total_pages=4, has_more=True, busy=False)
        assert plan.prev_enabled is False

    def test_busy_disables_both_buttons(self):
        plan = mo.pager_plan(current_page=2, total_pages=4, has_more=True, busy=True)
        assert plan.prev_enabled is False
        assert plan.next_enabled is False
        # 忙碌不影响页码集合（原文只影响按钮状态）
        assert plan.pages == [1, 2, 3, 4]

    def test_last_page_next_disabled(self):
        plan = mo.pager_plan(current_page=4, total_pages=4, has_more=False, busy=False)
        assert plan.next_enabled is False


# ══════════════════════════════════════════════════════════════════════
# 3. 搜索结果行的显示取值
# ══════════════════════════════════════════════════════════════════════


class TestSearchRowPlan:
    def test_index_is_one_based(self):
        assert mo.search_row_plan(0, FakeInfo()).index_text == "1"
        assert mo.search_row_plan(29, FakeInfo()).index_text == "30"

    def test_display_name_and_singer(self):
        plan = mo.search_row_plan(0, FakeInfo(name="七里香", singer="周杰伦"))
        assert plan.display == "七里香 - 周杰伦"

    def test_display_without_singer(self):
        plan = mo.search_row_plan(0, FakeInfo(name="七里香", singer=""))
        assert plan.display == "七里香"

    def test_name_kept_at_35_chars(self):
        name = "字" * 35
        plan = mo.search_row_plan(0, FakeInfo(name=name))
        assert plan.name_text == name  # 恰好 35 不截断

    def test_name_truncated_at_36_chars(self):
        name = "字" * 36
        plan = mo.search_row_plan(0, FakeInfo(name=name))
        assert plan.name_text == "字" * 33 + "..."
        assert len(plan.name_text) == 36

    def test_original_tag_generic_key(self):
        plan = mo.search_row_plan(0, FakeInfo(is_original=True))
        assert plan.is_original is True
        assert plan.tag_key == mo.KEY_ORIGINAL_TAG
        assert plan.tag_params == {}

    def test_original_tag_with_name(self):
        plan = mo.search_row_plan(0, FakeInfo(is_original=True, original_name="原唱"))
        assert plan.tag_key == mo.KEY_ORIGINAL_TAG_WITH_NAME
        assert plan.tag_params == {"name": "原唱"}

    def test_non_original_has_no_tag(self):
        plan = mo.search_row_plan(0, FakeInfo(is_original=False, original_name="原唱"))
        assert plan.is_original is False
        assert plan.tag_key == ""

    def test_duration_text_hidden_when_zero(self):
        assert mo.search_row_plan(0, FakeInfo(interval=0)).dur_text == ""
        assert mo.search_row_plan(0, FakeInfo(interval=240)).dur_text == "4:00"

    def test_play_count_text_hidden_when_zero(self):
        assert mo.search_row_plan(0, FakeInfo(play_count=0)).play_text == ""
        assert mo.search_row_plan(0, FakeInfo(play_count=999)).play_text == "999"
        assert mo.search_row_plan(0, FakeInfo(play_count=10000)).play_text == "1w"
        assert mo.search_row_plan(0, FakeInfo(play_count=12345)).play_text == "1.2w"

    def test_source_label_uppercased(self):
        assert mo.search_row_plan(0, FakeInfo(source="kw")).source_text == "KW"
        assert mo.search_row_plan(0, FakeInfo(source="wy")).source_text == "WY"


# ══════════════════════════════════════════════════════════════════════
# 4. 自动音质解析与音质信息补齐
# ══════════════════════════════════════════════════════════════════════


class TestAutoQuality:
    def _src(self, qualities):
        return FakeSource("wy", qualities=qualities)

    @pytest.mark.parametrize(
        "qualities,expected",
        [
            (["flac24bit", "flac", "320k", "128k"], "flac24bit"),
            (["flac", "320k", "128k"], "flac"),
            (["320k", "128k"], "320k"),
            (["128k"], "128k"),
            # 只声明了 flac24bit 之外的可用档位时，preferred 命中就返回 preferred
            (["flac24bit", "128k"], "flac24bit"),
        ],
    )
    def test_picks_highest_available(self, qualities, expected):
        info = FakeInfo(source="wy", qualities=qualities)
        got = mo.resolve_auto_quality(info, sources={"wy": self._src(qualities)})
        assert got == expected

    def test_preferred_is_flac24bit(self):
        src = self._src(["128k"])
        mo.resolve_auto_quality(FakeInfo(source="wy", qualities=["128k"]), sources={"wy": src})
        assert src.best_calls[-1][1] == mo.AUTO_QUALITY_PREFERRED == "flac24bit"

    def test_no_types_returns_128k(self):
        # 记录现状：一个档位都没声明时真实实现返回 "128k"（不是 preferred 本身）
        info = FakeInfo(source="wy", qualities=[])
        got = mo.resolve_auto_quality(info, sources={"wy": self._src([])})
        assert got == "128k" == mo.FALLBACK_QUALITY

    def test_types_without_matching_level_returns_128k(self):
        # 记录现状：声明了未知档位名（真实音源不会这样，但容错分支存在）
        info = FakeInfo(source="wy", types=[{"type": "unknown"}])
        assert mo.resolve_auto_quality(info, sources={"wy": self._src([])}) == "128k"

    def test_none_info_returns_128k(self):
        assert mo.resolve_auto_quality(None) == mo.FALLBACK_QUALITY == "128k"

    def test_unknown_source_returns_128k(self):
        assert mo.resolve_auto_quality(FakeInfo(source="zzz"), sources={}) == "128k"

    def test_source_exception_returns_128k(self):
        src = FakeSource("wy", best_error=RuntimeError("接口炸了"))
        assert mo.resolve_auto_quality(FakeInfo(source="wy"), sources={"wy": src}) == "128k"


class TestAutoQualityBackfill:
    def test_types_present_skips_backfill_search(self):
        src = FakeSource("wy")
        info = FakeInfo(source="wy", qualities=["320k"], songmid="m1")
        got_info, quality = mo.resolve_auto_quality_with_backfill(info, sources={"wy": src})
        assert got_info is info
        assert quality == "320k"
        assert src.search_calls == []  # 音质信息齐 -> 一次搜索都不发

    def test_missing_types_searches_and_matches_songmid(self):
        richer = FakeInfo(source="wy", songmid="m1", qualities=["flac", "128k"])
        other = FakeInfo(source="wy", songmid="m9", qualities=["flac24bit"])
        src = FakeSource("wy", items=[other, richer])
        info = FakeInfo(source="wy", songmid="m1", qualities=[])
        got_info, quality = mo.resolve_auto_quality_with_backfill(info, sources={"wy": src})
        assert got_info is richer
        assert quality == "flac"
        assert src.search_calls == [("七里香 周杰伦", 1, mo.BACKFILL_SEARCH_LIMIT)]

    def test_missing_types_no_match_keeps_original(self):
        other = FakeInfo(source="wy", songmid="m9", qualities=["flac"])
        src = FakeSource("wy", items=[other])
        info = FakeInfo(source="wy", songmid="m1", qualities=[])
        got_info, quality = mo.resolve_auto_quality_with_backfill(info, sources={"wy": src})
        assert got_info is info
        assert quality == "128k"  # 原 info 没档位 -> 真实实现回 128k

    def test_match_without_types_is_ignored(self):
        # 记录现状：补齐只认「songmid 相同**且**有 types」的候选
        candidate = FakeInfo(source="wy", songmid="m1", qualities=[])
        src = FakeSource("wy", items=[candidate])
        info = FakeInfo(source="wy", songmid="m1", qualities=[])
        got_info, _ = mo.resolve_auto_quality_with_backfill(info, sources={"wy": src})
        assert got_info is info

    def test_backfill_search_exception_keeps_original(self):
        src = FakeSource("wy", search_error=RuntimeError("搜索炸了"))
        info = FakeInfo(source="wy", songmid="m1", qualities=[])
        got_info, quality = mo.resolve_auto_quality_with_backfill(info, sources={"wy": src})
        assert got_info is info
        assert quality == "128k"

    def test_none_info(self):
        got_info, quality = mo.resolve_auto_quality_with_backfill(None)
        assert got_info is None
        assert quality == "128k"

    def test_keyword_falls_back_to_name(self):
        src = FakeSource("wy", items=[])
        info = FakeInfo(source="wy", singer="", qualities=[])
        mo.resolve_auto_quality_with_backfill(info, sources={"wy": src})
        assert src.search_calls == [("七里香", 1, mo.BACKFILL_SEARCH_LIMIT)]


class TestBackfillTargetsCurrent:
    def test_identity_required(self):
        results = [FakeInfo()]
        assert mo.backfill_targets_current(results, results) is True

    def test_equal_but_distinct_list_is_rejected(self):
        # 记录现状：判的是**对象身份**，不是内容相等 —— 重新搜索会换列表对象，
        # 旧回填必须被丢掉，否则会把新页面的行重新渲染成旧结果
        assert mo.backfill_targets_current([FakeInfo()], [FakeInfo()]) is False

    def test_none_never_matches(self):
        assert mo.backfill_targets_current(None, [FakeInfo()]) is False


class TestStreamReadyAction:
    def test_stale_when_seq_differs(self):
        assert mo.stream_ready_action(1, 2, "/tmp/a.mp3") == mo.STREAM_STALE

    def test_play_when_path_present(self):
        assert mo.stream_ready_action(2, 2, "/tmp/a.mp3") == mo.STREAM_PLAY

    def test_failed_when_path_missing(self):
        assert mo.stream_ready_action(2, 2, None) == mo.STREAM_FAILED
        assert mo.stream_ready_action(2, 2, "") == mo.STREAM_FAILED


# ══════════════════════════════════════════════════════════════════════
# 5. 封面取字节
# ══════════════════════════════════════════════════════════════════════


class TestFetchCoverBytes:
    def test_200_returns_bytes(self):
        calls = []
        data = mo.fetch_cover_bytes(
            "http://x/cover.jpg", http_get=make_getter(b"JPEGDATA", "image/jpeg", 200, calls)
        )
        assert data == b"JPEGDATA"
        assert calls[0][0] == "http://x/cover.jpg"
        assert calls[0][1]["timeout"] == 10

    def test_non_200_returns_none(self):
        assert mo.fetch_cover_bytes("http://x/c", http_get=make_getter(b"x", status_code=404)) is None

    def test_exception_returns_none(self):
        def boom(url, **kwargs):
            raise OSError("断网")

        assert mo.fetch_cover_bytes("http://x/c", http_get=boom) is None

    def test_patch_point_is_module_level_requests(self, monkeypatch):
        """补丁点必须指向**真正被读取**的名字（services.music_online.requests）。"""
        monkeypatch.setattr(mo.requests, "get", make_getter(b"BYTES"))
        assert mo.fetch_cover_bytes("http://x/c") == b"BYTES"

    def test_default_user_agent_is_set(self):
        calls = []
        mo.fetch_cover_bytes("http://x/c", http_get=make_getter(b"y", calls=calls))
        assert calls[0][1]["headers"]["User-Agent"] == "Mozilla/5.0"


# ══════════════════════════════════════════════════════════════════════
# 6. 正在播放区的取值
# ══════════════════════════════════════════════════════════════════════


class TestNowPlayingPlan:
    def test_online_mode_fields(self):
        info = FakeInfo(name="七里香", singer="周杰伦", album_name="七里香", interval=299,
                        img="http://x/c.jpg")
        plan = mo.now_playing_plan(online=True, online_info=info, current_quality="320k")
        assert plan.mode == mo.NOW_PLAYING_ONLINE
        assert plan.quality_text == "320K"
        assert plan.title == "七里香"
        assert plan.artist == "周杰伦"
        assert plan.album == "七里香"
        assert plan.duration == 299
        assert plan.sub_text == "周杰伦 - 七里香"
        assert plan.mini_text == "七里香 · 320K"
        assert plan.cover_url == "http://x/c.jpg"
        assert plan.cover_bytes is None

    def test_online_without_album(self):
        info = FakeInfo(singer="周杰伦", album_name="")
        plan = mo.now_playing_plan(online=True, online_info=info, current_quality="flac")
        assert plan.sub_text == "周杰伦"
        assert plan.quality_text == "FLAC"

    def test_online_without_artist(self):
        info = FakeInfo(singer="", album_name="专辑")
        plan = mo.now_playing_plan(online=True, online_info=info, current_quality="128k")
        assert plan.sub_text == "专辑"

    def test_online_unknown_quality_text_empty(self):
        plan = mo.now_playing_plan(online=True, online_info=FakeInfo(singer=""), current_quality="")
        assert plan.quality_text == ""
        # 音质文案为空时 mini 标题不拼 " · "
        assert plan.mini_text == plan.title

    def test_online_cover_url_empty_when_missing(self):
        plan = mo.now_playing_plan(online=True, online_info=FakeInfo(img=""))
        assert plan.cover_url == ""

    def test_empty_mode(self):
        plan = mo.now_playing_plan(online=False, local_path="")
        assert plan.mode == mo.NOW_PLAYING_EMPTY
        assert plan.quality_text == ""
        assert plan.title == ""

    def test_local_mode_uses_tags(self, tmp_path):
        path = str(tmp_path / "a.mp3")
        meta = {"title": "本地歌", "artist": "某人", "album": "某专辑", "duration": 200,
                "bitrate": 320000, "has_cover": True, "cover_data": b"IMG"}
        plan = mo.now_playing_plan(online=False, local_path=path, local_meta=meta)
        assert plan.mode == mo.NOW_PLAYING_LOCAL
        assert plan.title == "本地歌"
        assert plan.artist == "某人"
        assert plan.album == "某专辑"
        assert plan.duration == 200
        assert plan.sub_text == "某人 - 某专辑"
        assert plan.mini_text == "本地歌 · 320K"
        assert plan.cover_bytes == b"IMG"

    def test_local_mode_title_defaults_to_filename(self, tmp_path):
        path = str(tmp_path / "贝多芬.mp3")
        plan = mo.now_playing_plan(online=False, local_path=path, local_meta={})
        # 记录现状：元数据里没有 title 时用**含扩展名的文件名**（原文 `os.path.basename`，
        # 不是 splitext —— 真实 extract_audio_metadata 总会填 title，所以这条只在缺标签
        # 且元数据被替换的路径上可见）
        assert plan.title == "贝多芬.mp3"
        assert plan.quality_text == ""

    def test_local_without_cover(self, tmp_path):
        plan = mo.now_playing_plan(
            online=False, local_path=str(tmp_path / "a.mp3"),
            local_meta={"has_cover": False, "cover_data": b"IMG"},
        )
        assert plan.cover_bytes is None

    def test_local_flac_extension_is_lossless(self, tmp_path):
        plan = mo.now_playing_plan(online=False, local_path=str(tmp_path / "a.flac"), local_meta={})
        assert plan.quality_text == "FLAC"


# ══════════════════════════════════════════════════════════════════════
# 7. 下载：响应判定、后缀、临时文件命名
# ══════════════════════════════════════════════════════════════════════


@pytest.fixture
def tempdir(monkeypatch, tmp_path):
    """把 `tempfile.mkstemp` 落到 `tmp_path`（其余参数原样透传）。

    这样下载测试造出来的临时文件全在 pytest 的临时目录里、跑完自动清掉，
    不会往系统临时目录里丢垃圾；被测代码走的仍是真实的 mkstemp 调用。
    """
    real = md.tempfile.mkstemp

    def _mkstemp(*args, **kwargs):
        kwargs["dir"] = str(tmp_path)
        return real(*args, **kwargs)

    monkeypatch.setattr(md.tempfile, "mkstemp", _mkstemp)
    return tmp_path


class TestDownloadResponders:
    @pytest.mark.parametrize(
        "content_type,expected",
        [
            ("text/html; charset=utf-8", True),
            ("text/plain", True),
            ("application/json", True),
            ("audio/mpeg", False),
            ("audio/flac", False),
            ("application/octet-stream", False),
            ("", False),
        ],
    )
    def test_non_audio_response(self, content_type, expected):
        assert md.is_non_audio_response(content_type) is expected

    @pytest.mark.parametrize(
        "url,content_type,expected",
        [
            ("http://x/a", "audio/flac", ".flac"),
            ("http://x/a.FLAC", "audio/mpeg", ".flac"),
            ("http://x/a", "audio/ogg", ".ogg"),
            ("http://x/a.ogg", "audio/mpeg", ".ogg"),
            ("http://x/a", "audio/x-m4a", ".m4a"),
            ("http://x/a.m4a", "audio/mpeg", ".m4a"),
            ("http://x/a.m4s?x=1", "audio/mpeg", ".m4a"),
            ("http://x/a", "audio/mpeg", ".mp3"),
            # 原文注释点明的坑：带查询参数时 endswith 会失效，urlparse 取 path 才对
            ("http://x/a.flac?token=1", "audio/mpeg", ".flac"),
        ],
    )
    def test_pick_extension(self, url, content_type, expected):
        assert md.pick_extension(url, content_type) == expected

    def test_safe_temp_name_filters_punctuation(self):
        # 中文算 alnum，保留；斜杠/冒号被丢掉
        assert md.safe_temp_name("七里香/周杰伦:live") == "七里香周杰伦live"

    def test_safe_temp_name_keeps_allowed_punctuation(self):
        assert md.safe_temp_name("a-b_c.d e") == "a-b_c.d e"

    def test_safe_temp_name_truncates_at_50(self):
        assert len(md.safe_temp_name("x" * 80)) == 50

    def test_safe_temp_name_empty(self):
        assert md.safe_temp_name("") == ""


# ══════════════════════════════════════════════════════════════════════
# 8. 下载到临时文件（含转码与数量裁剪）
# ══════════════════════════════════════════════════════════════════════


class TestDownloadToTemp:
    def test_success_writes_file_and_registers(self, tempdir):
        ctx = make_ctx(http_get=make_getter(b"AUDIODATA", "audio/mpeg"))
        path = md.download_to_temp("http://x/a.mp3", "七里香", ctx=ctx)
        assert path is not None
        assert Path(path).exists()
        assert Path(path).read_bytes() == b"AUDIODATA"
        assert ctx.temp_files == [path]

    def test_filename_uses_prefix_and_safe_hint(self, tempdir):
        ctx = make_ctx(http_get=make_getter(b"X"))
        path = md.download_to_temp("http://x/a.mp3", "七里香/周杰伦", ctx=ctx)
        name = Path(path).name
        assert name.startswith("fmcl_")
        assert "七里香周杰伦" in name

    def test_extension_from_content_type(self, tempdir):
        ctx = make_ctx(http_get=make_getter(b"X", "audio/flac"))
        assert md.download_to_temp("http://x/a", ctx=ctx).endswith(".flac")

    def test_html_response_rejected_without_touching_disk(self, tempdir):
        ctx = make_ctx(http_get=make_getter(b"<html>404</html>", "text/html"))
        assert md.download_to_temp("http://x/a", ctx=ctx) is None
        assert ctx.temp_files == []
        assert list(tempdir.iterdir()) == []

    def test_json_response_rejected(self, tempdir):
        ctx = make_ctx(http_get=make_getter(b"{}", "application/json"))
        assert md.download_to_temp("http://x/a", ctx=ctx) is None
        assert ctx.temp_files == []

    def test_empty_body_removes_file_and_does_not_register(self, tempdir):
        ctx = make_ctx(http_get=make_getter(b""))
        assert md.download_to_temp("http://x/a", ctx=ctx) is None
        assert ctx.temp_files == []
        assert list(tempdir.iterdir()) == []

    def test_http_error_returns_none(self, tempdir):
        ctx = make_ctx(http_get=make_getter(b"X", status_code=500))
        assert md.download_to_temp("http://x/a", ctx=ctx) is None
        assert ctx.temp_files == []

    def test_transport_exception_returns_none(self, tempdir):
        def boom(url, **kwargs):
            raise OSError("断网")

        ctx = make_ctx(http_get=boom)
        assert md.download_to_temp("http://x/a", ctx=ctx) is None
        assert ctx.temp_files == []

    def test_extra_headers_and_cookies_are_forwarded(self, tempdir):
        calls = []
        ctx = make_ctx(http_get=make_getter(b"X", calls=calls))
        md.download_to_temp("http://x/a", "n", {"Referer": "http://b"}, {"buvid": "1"}, ctx=ctx)
        assert calls[0][1]["headers"]["Referer"] == "http://b"
        assert calls[0][1]["headers"]["User-Agent"] == md.DOWNLOAD_USER_AGENT
        assert calls[0][1]["cookies"] == {"buvid": "1"}
        assert calls[0][1]["timeout"] == md.DOWNLOAD_TIMEOUT
        assert calls[0][1]["stream"] is True

    def test_transcode_success_replaces_original(self, tempdir):
        converted = tempdir / "converted.wav"
        converted.write_bytes(b"WAV")

        def fake_transcode(path):
            return str(converted)

        ctx = make_ctx(http_get=make_getter(b"M4A"), transcoder=fake_transcode)
        path = md.download_to_temp("http://x/a.m4a", "歌", ctx=ctx)
        assert path == str(converted)
        assert ctx.temp_files == [str(converted)]
        # 原始 m4a 已从磁盘与列表里都清掉
        leftovers = [p.name for p in tempdir.iterdir() if p.name != "converted.wav"]
        assert leftovers == []

    def test_transcode_failure_keeps_original(self, tempdir):
        ctx = make_ctx(http_get=make_getter(b"M4A"), transcoder=lambda path: None)
        path = md.download_to_temp("http://x/a.m4a", "歌", ctx=ctx)
        assert path is not None
        assert path.endswith(".m4a")
        assert ctx.temp_files == [path]

    def test_trim_happens_when_not_transcoded(self, tempdir):
        ctx = make_ctx(http_get=make_getter(b"X"), transcoder=lambda path: None)
        paths = [md.download_to_temp(f"http://x/{i}.mp3", "n", ctx=ctx) for i in range(12)]
        assert len(ctx.temp_files) == md.MAX_TEMP_FILES == 10
        assert ctx.temp_files == paths[-10:]
        # 被裁掉的旧文件从磁盘上也没了
        assert not Path(paths[0]).exists()
        assert not Path(paths[1]).exists()
        assert Path(paths[-1]).exists()

    def test_no_trim_when_transcoded(self, tempdir):
        """记录现状：数量裁剪只发生在**未转码**的分支（原文 while 在 return converted 之后）。"""
        counter = iter(range(100))

        def fake_transcode(path):
            out = tempdir / f"wav_{next(counter)}.wav"
            out.write_bytes(b"WAV")
            return str(out)

        ctx = make_ctx(http_get=make_getter(b"X"), transcoder=fake_transcode)
        for i in range(12):
            md.download_to_temp(f"http://x/{i}.m4a", "n", ctx=ctx)
        # 每条都是「追加原文件 -> 移除原文件 -> 追加转码结果」，原文件被移走，
        # 所以列表长度恰好 12（超过 10 也不裁）
        assert len(ctx.temp_files) == 12

    def test_custom_chunk_size_is_used(self, tempdir, monkeypatch):
        chunks = []
        original_iter = FakeResponse.iter_content

        def spy(self, chunk_size=65536):
            chunks.append(chunk_size)
            yield from original_iter(self, chunk_size)

        monkeypatch.setattr(FakeResponse, "iter_content", spy)
        ctx = make_ctx(http_get=make_getter(b"A" * 10))
        ctx.chunk_size = 3
        md.download_to_temp("http://x/a", ctx=ctx)
        assert chunks == [3]


# ══════════════════════════════════════════════════════════════════════
# 9. 临时文件的裁剪 / 丢弃 / 清理规则
# ══════════════════════════════════════════════════════════════════════


class TestTempFileRules:
    def test_trim_removes_from_disk_and_list(self, tmp_path):
        files = []
        for i in range(4):
            p = tmp_path / f"f{i}.mp3"
            p.write_bytes(b"x")
            files.append(str(p))
        snapshot = list(files)
        removed = md.trim_temp_files(files, limit=2)
        assert removed == snapshot[:2]
        assert files == snapshot[2:]
        assert not (tmp_path / "f0.mp3").exists()
        assert (tmp_path / "f3.mp3").exists()

    def test_trim_noop_below_limit(self, tmp_path):
        p = tmp_path / "a.mp3"
        p.write_bytes(b"x")
        files = [str(p)]
        assert md.trim_temp_files(files, limit=10) == []
        assert files == [str(p)]

    def test_trim_tolerates_missing_file(self, tmp_path):
        # 记录现状：磁盘上已不存在的条目只从列表里摘掉，不抛异常
        files = [str(tmp_path / "gone.mp3"), str(tmp_path / "gone2.mp3")]
        snapshot = list(files)
        removed = md.trim_temp_files(files, limit=0)
        assert removed == snapshot
        assert files == []

    def test_discard_removes_file_and_entry(self, tmp_path):
        p = tmp_path / "a.mp3"
        p.write_bytes(b"x")
        files = [str(p)]
        md.discard_temp_file(str(p), files)
        assert not p.exists()
        assert files == []

    def test_discard_tolerates_missing_file(self, tmp_path):
        missing = str(tmp_path / "gone.mp3")
        md.discard_temp_file(missing)  # 不抛
        files = ["other"]
        md.discard_temp_file(missing, files)
        assert files == ["other"]

    def test_discard_without_list(self, tmp_path):
        p = tmp_path / "a.mp3"
        p.write_bytes(b"x")
        md.discard_temp_file(str(p), None)
        assert not p.exists()

    def test_cleanup_removes_all_and_clears_list(self, tmp_path):
        files = []
        for i in range(3):
            p = tmp_path / f"f{i}.mp3"
            p.write_bytes(b"x")
            files.append(str(p))
        files.append(str(tmp_path / "never_existed.mp3"))
        md.cleanup_temp_files(files)
        assert files == []
        assert list(tmp_path.iterdir()) == []

    def test_cleanup_swallows_removal_errors(self, monkeypatch, tmp_path):
        p = tmp_path / "a.mp3"
        p.write_bytes(b"x")
        files = [str(p)]

        def boom(path):
            raise OSError("被占用")

        monkeypatch.setattr(md.os, "remove", boom)
        md.cleanup_temp_files(files)
        # 记录现状：删不掉也照样清空列表（原文最后一行是 .clear()，与删除结果无关）
        assert files == []


# ══════════════════════════════════════════════════════════════════════
# 10. 音源附加请求头 / cookies / 显示名
# ══════════════════════════════════════════════════════════════════════


class TestDownloadSourceMeta:
    def test_headers_from_source(self):
        src = FakeSource("bili", headers={"Referer": "http://b"})
        assert md.get_download_headers("bili", sources={"bili": src}) == {"Referer": "http://b"}

    def test_headers_default_empty_dict(self):
        src = FakeSource("kw", headers=None)
        assert md.get_download_headers("kw", sources={"kw": src}) == {}

    def test_headers_missing_source(self):
        assert md.get_download_headers("nope", sources={}) == {}

    def test_headers_source_exception(self):
        src = FakeSource("kw", headers=RuntimeError("接口炸了"))
        assert md.get_download_headers("kw", sources={"kw": src}) == {}

    def test_cookies_from_source(self):
        src = FakeSource("bili", cookies={"buvid": "1"})
        assert md.get_download_cookies("bili", sources={"bili": src}) == {"buvid": "1"}

    def test_cookies_none_is_preserved(self):
        """None 与 {} 语义不同（原文返回 None），不能混成空 dict。"""
        src = FakeSource("kw", cookies=None)
        assert md.get_download_cookies("kw", sources={"kw": src}) is None

    def test_cookies_empty_dict_is_preserved(self):
        src = FakeSource("kw", cookies={})
        assert md.get_download_cookies("kw", sources={"kw": src}) == {}

    def test_cookies_missing_source(self):
        assert md.get_download_cookies("nope", sources={}) is None

    def test_cookies_source_exception(self):
        src = FakeSource("kw", cookies=RuntimeError("接口炸了"))
        assert md.get_download_cookies("kw", sources={"kw": src}) is None

    def test_source_display_name_uses_source_name(self):
        src = FakeSource("bili", source_name="哔哩哔哩")
        assert md.source_display_name("bili", sources={"bili": src}) == "哔哩哔哩"

    def test_source_display_name_falls_back_to_id(self):
        # 记录现状：音源缺失 -> 状态栏提示里显示的是 **id 本身**，不是空串
        assert md.source_display_name("nope", sources={}) == "nope"
        assert md.source_display_name("kw", sources={"kw": FakeSource("kw")}) == "kw"


# ══════════════════════════════════════════════════════════════════════
# 11. 跨源兜底解析与下载
# ══════════════════════════════════════════════════════════════════════


class TestResolveFallback:
    def test_calls_live_resolve_track(self, monkeypatch):
        """补丁点是 `services.music_source.resolve_track`（导入期不取快照）。"""
        sentinel = (FakeInfo(), "http://u")
        monkeypatch.setattr(ms, "resolve_track", lambda info, quality: sentinel)
        assert md.resolve_fallback(FakeInfo(), "320k") == sentinel

    def test_explicit_resolver_wins(self, monkeypatch):
        def forbidden(*a, **k):
            raise AssertionError("传了显式 resolver 就不该再调 music_source.resolve_track")

        monkeypatch.setattr(ms, "resolve_track", forbidden)
        info = FakeInfo()
        assert md.resolve_fallback(info, "320k", resolver=lambda i, q: (i, "u")) == (info, "u")

    def test_exception_returns_none(self):
        def boom(info, quality):
            raise RuntimeError("兜底炸了")

        assert md.resolve_fallback(FakeInfo(), "320k", resolver=boom) is None

    def test_none_from_resolver(self):
        assert md.resolve_fallback(FakeInfo(), "320k", resolver=lambda i, q: None) is None


class TestDownloadFallbackResult:
    def _ctx(self, content=b"AUDIO", **kw):
        return md.DownloadContext(
            temp_files=[],
            http_get=make_getter(content, **kw),
            transcoder=lambda path: None,
        )

    def test_success_notifies_source(self, tempdir, monkeypatch):
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        ctx = self._ctx()
        noticed = []
        fb_info = FakeInfo(source="kg", name="同款歌")
        result = md.download_fallback_result(
            (fb_info, "http://u"), "320k", ctx=ctx, notify_source=noticed.append
        )
        assert result is not None
        path, info, quality = result
        assert info is fb_info
        assert quality == "320k"
        assert Path(path).read_bytes() == b"AUDIO"
        assert noticed == ["kg"]

    def test_bad_header_discards_file_and_skips_notify(self, tempdir, monkeypatch):
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: False)
        called = []
        monkeypatch.setattr(
            md.music_audio, "validate_audio_duration",
            lambda p, d: called.append("duration") or True,
        )
        ctx = self._ctx()
        noticed = []
        assert md.download_fallback_result(
            (FakeInfo(), "http://u"), "128k", ctx=ctx, notify_source=noticed.append
        ) is None
        assert noticed == []
        assert ctx.temp_files == []
        assert list(tempdir.iterdir()) == []
        # 文件头不过就不查时长（原文是 and 短路）
        assert called == []

    def test_duration_mismatch_discards_file(self, tempdir, monkeypatch):
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: False)
        ctx = self._ctx()
        noticed = []
        assert md.download_fallback_result(
            (FakeInfo(), "http://u"), "128k", ctx=ctx, notify_source=noticed.append
        ) is None
        assert noticed == []
        assert ctx.temp_files == []
        assert list(tempdir.iterdir()) == []

    def test_download_failure_returns_none(self, tempdir):
        ctx = md.DownloadContext(temp_files=[], http_get=make_getter(b"", status_code=403))
        assert md.download_fallback_result((FakeInfo(), "http://u"), "128k", ctx=ctx) is None
        assert ctx.temp_files == []

    def test_quality_is_the_requested_one(self, tempdir, monkeypatch):
        """记录现状：兜底最终命中的档位无法精确得知，展示沿用用户请求的档位。"""
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        ctx = self._ctx()
        _, _, quality = md.download_fallback_result((FakeInfo(), "http://u"), "flac", ctx=ctx)
        assert quality == "flac"


class TestTryDownloadFromSource:
    def _ctx(self, sources, content=b"AUDIO", **kw):
        return md.DownloadContext(
            temp_files=[],
            http_get=make_getter(content, **kw),
            transcoder=lambda path: None,
            sources=sources,
        )

    def _patch_validators(self, monkeypatch, header=True, duration=True):
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: header)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: duration)

    def test_missing_source(self, tempdir):
        ctx = self._ctx({})
        info = FakeInfo(source="zzz")
        assert md.try_download_from_source(info, "320k", ctx=ctx) == (None, None, "320k")

    def test_success_returns_path_url_quality(self, tempdir, monkeypatch):
        self._patch_validators(monkeypatch)
        src = FakeSource("kw", urls={"320k": "http://u/320"})
        ctx = self._ctx({"kw": src})
        path, url, quality = md.try_download_from_source(FakeInfo(source="kw"), "320k", ctx=ctx)
        assert Path(path).read_bytes() == b"AUDIO"
        assert url == "http://u/320"
        assert quality == "320k"
        assert [q for _, q in src.url_calls] == ["320k"]

    def test_quality_fallback_order(self, tempdir, monkeypatch):
        """请求档位拿不到 URL 时按 flac -> 320k -> 128k 逐档试，命中即停。"""
        self._patch_validators(monkeypatch)
        src = FakeSource("kw", urls={"320k": "http://u/320"})
        ctx = self._ctx({"kw": src})
        _, url, quality = md.try_download_from_source(FakeInfo(source="kw"), "128k", ctx=ctx)
        assert url == "http://u/320"
        assert quality == "320k"
        assert [q for _, q in src.url_calls] == ["128k", "flac", "320k"]

    def test_skips_the_requested_quality_on_retry(self, tempdir, monkeypatch):
        self._patch_validators(monkeypatch)
        # 请求 flac 拿不到 URL；只有 128k 可用
        src = FakeSource("kw", urls={"128k": "http://u/128"})
        ctx = self._ctx({"kw": src})
        _, url, quality = md.try_download_from_source(FakeInfo(source="kw"), "flac", ctx=ctx)
        assert url == "http://u/128"
        assert quality == "128k"
        # flac 已经失败过一次，回退循环里不再重复请求同一个档位
        assert [q for _, q in src.url_calls] == ["flac", "320k", "128k"]

    def test_all_qualities_fail(self, tempdir, monkeypatch):
        self._patch_validators(monkeypatch)
        src = FakeSource("kw", urls={})
        ctx = self._ctx({"kw": src})
        info = FakeInfo(source="kw")
        assert md.try_download_from_source(info, "320k", ctx=ctx) == (None, None, "320k")

    def test_url_exception_is_swallowed(self, tempdir, monkeypatch):
        self._patch_validators(monkeypatch)
        src = FakeSource("kw", url_error=RuntimeError("接口炸了"))
        ctx = self._ctx({"kw": src})
        info = FakeInfo(source="kw")
        # 记录现状：取 URL 抛异常时返回值里的音质是**原始档位**（不是回退档位）
        assert md.try_download_from_source(info, "320k", ctx=ctx) == (None, None, "320k")

    def test_download_failure_returns_url_and_quality(self, tempdir, monkeypatch):
        self._patch_validators(monkeypatch)
        src = FakeSource("kw", urls={"320k": "http://u/320"})
        ctx = self._ctx({"kw": src}, content=b"<html>", content_type="text/html")
        path, url, quality = md.try_download_from_source(FakeInfo(source="kw"), "320k", ctx=ctx)
        assert path is None
        assert url == "http://u/320"  # 记录现状：下载失败时 URL 与档位仍然回传
        assert quality == "320k"

    def test_bad_header_discards_temp_file(self, tempdir, monkeypatch):
        self._patch_validators(monkeypatch, header=False)
        src = FakeSource("kw", urls={"320k": "http://u/320"})
        ctx = self._ctx({"kw": src})
        path, url, quality = md.try_download_from_source(FakeInfo(source="kw"), "320k", ctx=ctx)
        assert path is None
        assert ctx.temp_files == []  # 已从列表里移出
        assert list(tempdir.iterdir()) == []  # 已从磁盘删掉，不残留

    def test_duration_mismatch_discards_temp_file(self, tempdir, monkeypatch):
        self._patch_validators(monkeypatch, header=True, duration=False)
        src = FakeSource("kw", urls={"320k": "http://u/320"})
        ctx = self._ctx({"kw": src})
        info = FakeInfo(source="kw", interval=240)
        assert md.try_download_from_source(info, "320k", ctx=ctx)[0] is None
        assert ctx.temp_files == []
        assert list(tempdir.iterdir()) == []

    def test_headers_and_cookies_reach_the_request(self, tempdir, monkeypatch):
        self._patch_validators(monkeypatch)
        calls = []
        src = FakeSource(
            "bili", urls={"128k": "http://u/128"},
            headers={"Referer": "http://b"}, cookies={"buvid": "9"},
        )
        ctx = md.DownloadContext(
            temp_files=[],
            http_get=make_getter(b"AUDIO", calls=calls),
            transcoder=lambda path: None,
            sources={"bili": src},
        )
        md.try_download_from_source(FakeInfo(source="bili"), "128k", ctx=ctx)
        assert calls[0][1]["headers"]["Referer"] == "http://b"
        assert calls[0][1]["cookies"] == {"buvid": "9"}


# ══════════════════════════════════════════════════════════════════════
# 12. 取流 + 跨源兜底的编排（逐源失败后继续、全部失败时的返回）
# ══════════════════════════════════════════════════════════════════════


class TestFetchOnlineSong:
    def _ctx(self, sources, content=b"AUDIO"):
        return md.DownloadContext(
            temp_files=[],
            http_get=make_getter(content),
            transcoder=lambda path: None,
            sources=sources,
        )

    def test_none_info_returns_immediately(self):
        got = md.fetch_online_song(None, "320k", risk_retry=lambda *a: None, ctx=make_ctx())
        assert got == (None, None, "320k")

    def test_primary_source_success_skips_fallback(self, tempdir, monkeypatch):
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        src = FakeSource("kw", urls={"320k": "http://u/320"})
        resolved = []
        info = FakeInfo(source="kw")
        path, got_info, quality = md.fetch_online_song(
            info, "320k", risk_retry=lambda *a: None, ctx=self._ctx({"kw": src}),
            resolve=lambda i, q: resolved.append(("resolve", q)),
        )
        assert Path(path).exists()
        assert got_info is info
        assert quality == "320k"
        assert resolved == []  # 主源成功 -> 一次兜底都不试

    def test_primary_fails_then_fallback_succeeds(self, tempdir, monkeypatch):
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        primary = FakeSource("kw", urls={})  # 主源一个档位都拿不到
        fb_info = FakeInfo(source="kg")
        results = []

        def fake_download(fb, quality, **kwargs):
            results.append((fb, quality))
            return "/tmp/fb.mp3", fb[0], quality

        info = FakeInfo(source="kw")
        got_path, got_info, got_quality = md.fetch_online_song(
            info, "320k", risk_retry=lambda *a: None, ctx=self._ctx({"kw": primary}),
            resolve=lambda i, q: (fb_info, "http://u/fb"),
            download_fallback=fake_download,
        )
        assert got_path == "/tmp/fb.mp3"
        assert got_info is fb_info
        assert got_quality == "320k"
        assert results == [((fb_info, "http://u/fb"), "320k")]

    def test_all_sources_fail_returns_none_with_original_info(self, tempdir, monkeypatch):
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        primary = FakeSource("kw", urls={})
        info = FakeInfo(source="kw", name="绝版歌", singer="某人")
        path, got_info, quality = md.fetch_online_song(
            info, "320k", risk_retry=lambda *a: None, ctx=self._ctx({"kw": primary}),
            resolve=lambda i, q: None,
        )
        assert (path, got_info, quality) == (None, info, "320k")

    def test_risk_retry_is_attempted_after_fallback_failure(self, tempdir, monkeypatch):
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        primary = FakeSource("kw", urls={})
        info = FakeInfo(source="kw")
        seen = []
        fallback_info = FakeInfo(source="bili")

        def risk_retry(i, q, fbq):
            seen.append((i, q, fbq))
            return ("/tmp/risk.mp3", fallback_info, q)

        got = md.fetch_online_song(
            info, "320k", "auto", risk_retry=risk_retry, ctx=self._ctx({"kw": primary}),
            resolve=lambda i, q: (FakeInfo(source="kg"), "http://u/kg"),
            download_fallback=lambda fb, q, **kw: None,  # 兜底下载失败 -> 走风控
        )
        assert got == ("/tmp/risk.mp3", fallback_info, "320k")
        assert seen == [(info, "320k", "auto")]

    def test_risk_retry_receives_quality_when_fallback_quality_none(self, tempdir, monkeypatch):
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        primary = FakeSource("kw", urls={})
        seen = []
        md.fetch_online_song(
            FakeInfo(source="kw"), "128k", None, risk_retry=lambda i, q, fbq: seen.append(fbq),
            ctx=self._ctx({"kw": primary}), resolve=lambda i, q: None,
        )
        assert seen == ["128k"]

    def test_risk_retry_result_returned_when_truthy(self, tempdir, monkeypatch):
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        primary = FakeSource("kw", urls={})
        info = FakeInfo(source="kw")
        out = md.fetch_online_song(
            info, "320k", risk_retry=lambda *a: ("/tmp/r.mp3", info, "320k"),
            ctx=self._ctx({"kw": primary}), resolve=lambda i, q: None,
        )
        assert out == ("/tmp/r.mp3", info, "320k")

    def test_try_download_injection_is_respected(self, tempdir):
        info = FakeInfo(source="kw")
        out = md.fetch_online_song(
            info, "320k", risk_retry=lambda *a: None, ctx=self._ctx({}),
            try_download=lambda i, q: ("/tmp/bypass.mp3", "u", q),
        )
        # 注入生效：没走到真实 try_download_from_source（音源表是空的，真实路径必然失败）
        assert out == ("/tmp/bypass.mp3", info, "320k")


class FakeBiliSource(FakeSource):
    """B站音源替身：风控三连（take_pending_risk / validate_risk / set_gaia_vtoken）。"""

    def __init__(self, risk=None, grisk="grisk-1", risk_error=None, **kwargs):
        super().__init__("bili", **kwargs)
        self.risk = risk
        self.grisk = grisk
        self.risk_error = risk_error
        self.validate_calls = []
        self.vtoken = None

    def take_pending_risk(self):
        if self.risk_error is not None:
            raise self.risk_error
        return self.risk

    def validate_risk(self, token, challenge, seccode, validate):
        self.validate_calls.append((token, challenge, seccode, validate))
        return self.grisk

    def set_gaia_vtoken(self, grisk_id):
        self.vtoken = grisk_id


RISK = {"gt": "GT", "challenge": "CH", "token": "TK"}
CAPTCHA_RESULT = {
    "geetest_challenge": "gc",
    "geetest_seccode": "gs",
    "geetest_validate": "gv",
}


class TestBiliRiskRetry:
    def _ctx(self, sources):
        return md.DownloadContext(
            temp_files=[], http_get=make_getter(), transcoder=lambda path: None, sources=sources
        )

    def test_no_bili_source_returns_none(self):
        assert md.try_bili_risk_retry(FakeInfo(), "320k", ctx=self._ctx({})) is None

    def test_take_pending_risk_exception_returns_none(self):
        src = FakeBiliSource(risk_error=RuntimeError("接口炸了"))
        assert md.try_bili_risk_retry(FakeInfo(), "320k", ctx=self._ctx({"bili": src})) is None

    def test_no_pending_risk_returns_none(self):
        src = FakeBiliSource(risk=None)
        assert md.try_bili_risk_retry(FakeInfo(), "320k", ctx=self._ctx({"bili": src})) is None

    def test_flow_returns_none_closes_dialog(self):
        src = FakeBiliSource(risk=RISK)
        closed = []
        assert md.try_bili_risk_retry(
            FakeInfo(), "320k", ctx=self._ctx({"bili": src}),
            run_flow=lambda *a, **k: None,
            open_dialog=lambda ref, ev: None,
            close_dialog=lambda ref: closed.append(ref),
        ) is None
        assert len(closed) == 1
        assert src.validate_calls == []  # 验证没通过就不换 grisk_id

    def test_flow_exception_still_closes_dialog(self):
        src = FakeBiliSource(risk=RISK)
        closed = []

        def boom(*a, **k):
            raise RuntimeError("验证流程炸了")

        assert md.try_bili_risk_retry(
            FakeInfo(), "320k", ctx=self._ctx({"bili": src}), run_flow=boom,
            open_dialog=lambda ref, ev: None,
            close_dialog=lambda ref: closed.append(ref),
        ) is None
        assert len(closed) == 1
        assert src.validate_calls == []

    def test_validate_risk_returns_none(self):
        src = FakeBiliSource(risk=RISK, grisk=None)
        assert md.try_bili_risk_retry(
            FakeInfo(), "320k", ctx=self._ctx({"bili": src}),
            run_flow=lambda *a, **k: dict(CAPTCHA_RESULT),
        ) is None
        assert src.vtoken is None

    def test_success_sets_vtoken_and_retries_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        payload = tmp_path / "audio.mp3"
        payload.write_bytes(b"AUDIO")
        src = FakeBiliSource(risk=RISK, grisk="grisk-9")
        fb_info = FakeInfo(source="kg")
        called = []

        out = md.try_bili_risk_retry(
            FakeInfo(source="kw"), "320k", "auto", ctx=self._ctx({"bili": src}),
            run_flow=lambda *a, **k: dict(CAPTCHA_RESULT),
            resolve=lambda i, q: called.append(("resolve", q)) or (fb_info, "http://u"),
            download_fallback=lambda fb, q, **kw: called.append(("download", q))
            or (str(payload), fb[0], q),
        )
        assert out == (str(payload), fb_info, "320k")
        assert src.vtoken == "grisk-9"
        assert src.validate_calls == [("TK", "gc", "gs", "gv")]
        assert called == [("resolve", "auto"), ("download", "320k")]

    def test_dialog_sink_order_matches_original(self, tmp_path):
        src = FakeBiliSource(risk=RISK)
        events = []

        def open_dialog(ref, ev):
            events.append("open")
            ref["dialog"] = "DLG"

        def update_dialog(ref, text):
            events.append(("update", text))

        def close_dialog(ref):
            events.append(("close", ref.get("dialog")))

        def run_flow(gt, challenge, on_status=None, stop_event=None):
            events.append(("flow", gt, challenge))
            on_status("正在打开浏览器...")
            events.append("flow_end")
            return dict(CAPTCHA_RESULT)

        md.try_bili_risk_retry(
            FakeInfo(), "320k", ctx=self._ctx({"bili": src}), run_flow=run_flow,
            open_dialog=open_dialog, update_dialog=update_dialog, close_dialog=close_dialog,
        )
        # 开窗在起验证流程之前；关窗在 finally；状态文字在流程中回传
        assert events == [
            "open",
            ("flow", "GT", "CH"),
            ("update", "正在打开浏览器..."),
            "flow_end",
            ("close", "DLG"),
        ]

    def test_same_stop_event_is_shared(self):
        src = FakeBiliSource(risk=RISK)
        seen = {}

        def open_dialog(ref, ev):
            seen["open"] = ev

        def run_flow(gt, challenge, on_status=None, stop_event=None):
            seen["flow"] = stop_event
            return dict(CAPTCHA_RESULT)

        md.try_bili_risk_retry(
            FakeInfo(), "320k", ctx=self._ctx({"bili": src}), run_flow=run_flow,
            open_dialog=open_dialog,
        )
        assert seen["open"] is seen["flow"]
        # 默认是 threading.Event 的实例（服务里只造对象、不起线程）
        assert hasattr(seen["open"], "set") and hasattr(seen["open"], "is_set")

    def test_no_fallback_after_validation(self):
        src = FakeBiliSource(risk=RISK)
        assert md.try_bili_risk_retry(
            FakeInfo(), "320k", ctx=self._ctx({"bili": src}),
            run_flow=lambda *a, **k: dict(CAPTCHA_RESULT),
            resolve=lambda i, q: None,
        ) is None
        assert src.vtoken == "grisk-1"  # grisk_id 已经换到了，只是兜底没找到同款歌


# ══════════════════════════════════════════════════════════════════════
# 13. 网易云远程歌单：id 前缀、分页、同步事件流
# ══════════════════════════════════════════════════════════════════════


def real_info(name="七里香", singer="周杰伦", source="wy", songmid="m1", interval=240, types=None):
    """**真正的** `MusicInfo`。

    `PlaylistSong.from_online_info` 会做 `isinstance(i, MusicInfo)` 校验，`FakeInfo`
    跨不过去（这也正是 `ui.music_source` 必须用 `sys.modules` 别名而不是复制名字的原因：
    两份模块对象会产生两个 `MusicInfo` 类）。凡是走到 `from_online_info` 的用例都用它。
    """
    from services.music_source.base import MusicInfo

    return MusicInfo(
        name=name, singer=singer, source=source, songmid=songmid, interval=interval,
        types=list(types or []),
    )


class FakeWyApi:
    """`WyRemoteApi` 的替身（不联网）。"""

    def __init__(self, logged_in=True, playlists=None, tracks=None,
                 login_error=None, playlists_error=None, tracks_error=None):
        self._logged_in = logged_in
        self._playlists = playlists
        self._tracks = tracks
        self.login_error = login_error
        self.playlists_error = playlists_error
        self.tracks_error = tracks_error
        self.track_ids = []

    def is_logged_in(self):
        if self.login_error is not None:
            raise self.login_error
        return self._logged_in

    def get_user_playlists(self):
        if self.playlists_error is not None:
            raise self.playlists_error
        return self._playlists

    def get_playlist_tracks(self, playlist_id):
        if self.tracks_error is not None:
            raise self.tracks_error
        self.track_ids.append(playlist_id)
        return self._tracks


class TestWyRemoteKey:
    def test_prefix_is_wy_colon(self):
        assert wy.REMOTE_PREFIX == "wy:"

    def test_remote_key(self):
        assert wy.remote_key("12345") == "wy:12345"

    def test_is_remote_key(self):
        assert wy.is_remote_key("wy:1") is True
        assert wy.is_remote_key("local-1") is False
        assert wy.is_remote_key("") is False

    def test_strip_remote_key(self):
        assert wy.strip_remote_key("wy:12345") == "12345"

    def test_entry_label(self):
        assert wy.entry_label({"id": "1", "name": "我喜欢的音乐", "track_count": 12}) == (
            "我喜欢的音乐 (12)"
        )

    def test_page_size_and_period(self):
        assert wy.PAGE_SIZE == 20
        assert wy.PERIODIC_MS == 10 * 60 * 1000
        assert wy.DISPATCH_INTERVAL_MS == 200


class TestWyRemotePaging:
    def test_page_count_minimum_one(self):
        assert wy.page_count(0) == 1
        assert wy.page_count(1) == 1
        assert wy.page_count(20) == 1
        assert wy.page_count(21) == 2
        assert wy.page_count(40) == 2
        assert wy.page_count(41) == 3

    def test_clamp_page_only_pulls_down(self):
        assert wy.clamp_page(5, 2) == 2
        assert wy.clamp_page(1, 2) == 1
        assert wy.clamp_page(0, 2) == 0  # 记录现状：只压下不抬上（0 不会被抬到 1）

    def test_page_window_normal(self):
        window = wy.page_window(45, 2)
        assert (window.page, window.pages, window.start, window.stop) == (2, 3, 20, 40)

    def test_page_window_clamps_overflow(self):
        window = wy.page_window(25, 9)
        assert window.page == 2
        assert window.pages == 2
        assert (window.start, window.stop) == (20, 40)

    def test_page_window_first_page(self):
        window = wy.page_window(45, 1)
        assert (window.start, window.stop) == (0, 20)

    def test_page_window_last_page_partial(self):
        window = wy.page_window(45, 3)
        assert (window.start, window.stop) == (40, 60)

    def test_pager_hidden_without_view(self):
        plan = wy.pager_plan(total=100, page=1, has_view=False)
        assert plan.hidden is True
        assert plan.prev_enabled is False and plan.next_enabled is False

    def test_pager_hidden_when_single_page(self):
        plan = wy.pager_plan(total=20, page=1, has_view=True)
        assert plan.hidden is True

    def test_pager_visible_and_clamped(self):
        plan = wy.pager_plan(total=45, page=9, has_view=True)
        assert plan.hidden is False
        assert plan.pages == 3
        assert plan.page == 3
        assert plan.prev_enabled is True
        assert plan.next_enabled is False

    def test_pager_first_page(self):
        plan = wy.pager_plan(total=45, page=1, has_view=True)
        assert plan.prev_enabled is False
        assert plan.next_enabled is True

    def test_go_page_target_normal(self):
        assert wy.go_page_target(page=2, current_page=1, total=45) == 2

    def test_go_page_target_rejects_out_of_range(self):
        # 45 首 -> 3 页（每页 20）
        assert wy.go_page_target(page=0, current_page=1, total=45) is None
        assert wy.go_page_target(page=3, current_page=1, total=45) == 3
        assert wy.go_page_target(page=4, current_page=1, total=45) is None

    def test_go_page_target_rejects_same_page(self):
        assert wy.go_page_target(page=2, current_page=2, total=45) is None

    def test_cached_songs_distinguishes_none_from_empty(self):
        assert wy.cached_songs({}, "p1") is None
        assert wy.cached_songs({"p1": []}, "p1") == []
        assert wy.cached_songs({"p1": ["a"]}, "p1") == ["a"]


class TestWyRemoteSync:
    def test_logged_out_state(self):
        api = FakeWyApi(logged_in=False)
        assert wy.sync_playlists(api=api) == (wy.STATE_LOGGED_OUT, [])

    def test_ok_state_returns_playlists(self):
        playlists = [{"id": "1", "name": "p", "track_count": 1}]
        api = FakeWyApi(playlists=playlists)
        assert wy.sync_playlists(api=api) == (wy.STATE_OK, playlists)

    def test_none_playlists_is_error_state(self):
        api = FakeWyApi(playlists=None)
        state, data = wy.sync_playlists(api=api)
        assert state == wy.STATE_ERROR
        assert data is None  # 记录现状：error 态带的是 None（不是 []）

    def test_login_exception_is_error_state(self):
        api = FakeWyApi(login_error=RuntimeError("接口炸了"))
        assert wy.sync_playlists(api=api) == (wy.STATE_ERROR, [])

    def test_playlists_exception_is_error_state(self):
        api = FakeWyApi(playlists_error=RuntimeError("接口炸了"))
        assert wy.sync_playlists(api=api) == (wy.STATE_ERROR, [])

    def test_state_constants(self):
        assert (wy.STATE_OK, wy.STATE_LOGGED_OUT, wy.STATE_ERROR) == ("ok", "logged_out", "error")

    def test_default_api_is_used_when_not_injected(self, monkeypatch):
        """默认后端也走注入缝：换掉 `DEFAULT_API` 就能离线（autouse guard 会拦真网络）。"""
        playlists = [{"id": "9", "name": "p", "track_count": 3}]
        monkeypatch.setattr(wy, "DEFAULT_API", FakeWyApi(playlists=playlists))
        assert wy.sync_playlists() == (wy.STATE_OK, playlists)

    def test_fetch_tracks_ok(self):
        api = FakeWyApi(tracks=[FakeInfo(songmid="a")])
        got = wy.fetch_playlist_tracks("p1", api=api)
        assert len(got) == 1
        assert api.track_ids == ["p1"]

    def test_fetch_tracks_empty_playlist_is_not_failure(self):
        api = FakeWyApi(tracks=[])
        assert wy.fetch_playlist_tracks("p1", api=api) == []

    def test_fetch_tracks_failure_returns_none(self):
        api = FakeWyApi(tracks_error=RuntimeError("接口炸了"))
        assert wy.fetch_playlist_tracks("p1", api=api) is None


class TestWyPlanSyncApply:
    def _plan(self, **kw):
        base = dict(
            seq=1, sync_seq=1, state=wy.STATE_OK, playlists=[],
            current_playlists=[], view_id=None, tab_mode="local",
        )
        base.update(kw)
        return wy.plan_sync_apply(**base)

    def test_stale_when_seq_differs(self):
        assert self._plan(seq=1, sync_seq=2).kind == wy.SYNC_STALE

    def test_logged_out_clears_view_only_when_viewing(self):
        plan = self._plan(state=wy.STATE_LOGGED_OUT, view_id=None)
        assert plan.kind == wy.SYNC_LOGGED_OUT
        assert plan.sync_failed is False
        assert plan.clear_view is False

    def test_logged_out_with_view_clears_it(self):
        plan = self._plan(state=wy.STATE_LOGGED_OUT, view_id="p1", tab_mode="local")
        assert plan.clear_view is True
        assert plan.fallback_to_history is False  # 没在歌单标签页就不切历史

    def test_logged_out_falls_back_to_history_on_playlist_tab(self):
        plan = self._plan(state=wy.STATE_LOGGED_OUT, view_id="p1", tab_mode="playlist")
        assert plan.clear_view is True
        assert plan.fallback_to_history is True

    def test_error_marks_failed(self):
        plan = self._plan(state=wy.STATE_ERROR)
        assert plan.kind == wy.SYNC_ERROR
        assert plan.sync_failed is True

    def test_unchanged_when_list_identical(self):
        same = [{"id": "1", "name": "p", "track_count": 1}]
        plan = self._plan(playlists=same, current_playlists=list(same))
        assert plan.kind == wy.SYNC_UNCHANGED
        assert plan.sync_failed is False

    def test_update_drops_removed_playlist_caches(self):
        old = [{"id": "1", "name": "a", "track_count": 1}, {"id": "2", "name": "b", "track_count": 2}]
        new = [{"id": "2", "name": "b", "track_count": 2}, {"id": "3", "name": "c", "track_count": 3}]
        plan = self._plan(playlists=new, current_playlists=old)
        assert plan.kind == wy.SYNC_UPDATE
        assert plan.drop_ids == {"1"}
        assert plan.clear_view is False

    def test_update_clears_deleted_view(self):
        old = [{"id": "1", "name": "a", "track_count": 1}]
        new = [{"id": "2", "name": "b", "track_count": 2}]
        plan = self._plan(playlists=new, current_playlists=old, view_id="1", tab_mode="playlist")
        assert plan.clear_view is True
        assert plan.fallback_to_history is True

    def test_update_keeps_existing_view(self):
        old = [{"id": "1", "name": "a", "track_count": 1}]
        new = [{"id": "1", "name": "改名了", "track_count": 1}]
        plan = self._plan(playlists=new, current_playlists=old, view_id="1", tab_mode="playlist")
        assert plan.kind == wy.SYNC_UPDATE
        assert plan.clear_view is False


class TestWyTracksAction:
    def test_other_playlist_is_cache_only(self):
        assert wy.tracks_action("p1", [FakeInfo()], "p2") == wy.TRACKS_CACHE_ONLY

    def test_current_playlist_failure(self):
        assert wy.tracks_action("p1", None, "p1") == wy.TRACKS_FAILED

    def test_current_playlist_empty_list_renders(self):
        assert wy.tracks_action("p1", [], "p1") == wy.TRACKS_RENDER

    def test_current_playlist_with_songs_renders(self):
        assert wy.tracks_action("p1", [FakeInfo()], "p1") == wy.TRACKS_RENDER

    def test_no_view_is_cache_only(self):
        assert wy.tracks_action("p1", [FakeInfo()], None) == wy.TRACKS_CACHE_ONLY


class TestWyEvents:
    def test_make_event_is_plain_tuple(self):
        assert wy.make_event(wy.EVENT_SYNC, 1, "ok", []) == ("sync", 1, "ok", [])
        assert wy.make_event(wy.EVENT_TRACKS, "p1", None) == ("tracks", "p1", None)
        assert wy.make_event(wy.EVENT_PREFETCH, 1, 2, 3, 4, 5, 6) == ("prefetch", 1, 2, 3, 4, 5, 6)

    def test_event_kinds(self):
        assert set(wy.EVENT_KINDS) == {"sync", "tracks", "prefetch", "prefetch_fail"}

    def test_drain_empty_queue_yields_nothing(self):
        assert list(wy.drain_events(__import__("queue").Queue())) == []

    def test_drain_yields_in_fifo_order(self):
        q = __import__("queue").Queue()
        q.put(("sync", 1))
        q.put(("tracks", 2))
        assert list(wy.drain_events(q)) == [("sync", 1), ("tracks", 2)]
        assert q.empty()

    def test_partial_consumption_keeps_rest_queued(self):
        """生成器语义：提前 break 时剩下的条目**留在队列里**（下一次 tick 还能取到）。

        这条钉住的是"处理某条事件抛异常"的路径：界面那个 for 会立刻退出，
        用列表版 `drain_events` 会把剩下的条目摘掉却不处理 —— 等于丢事件。
        """
        q = __import__("queue").Queue()
        q.put(("sync", 1))
        q.put(("tracks", 2))
        q.put(("sync", 3))
        seen = []
        for event in wy.drain_events(q):
            seen.append(event)
            if len(seen) == 1:
                break
        assert seen == [("sync", 1)]
        assert list(wy.drain_events(q)) == [("tracks", 2), ("sync", 3)]


# ══════════════════════════════════════════════════════════════════════
# 14. 服务零 UI 依赖 / 零线程 / 零定时器（静态 AST 断言）
# ══════════════════════════════════════════════════════════════════════

NEW_SERVICE_FILES = [
    "services/music_online.py",
    "services/music_download.py",
    "services/music_wy_remote.py",
]
FORBIDDEN_TOPS = ("tkinter", "customtkinter", "PySide6", "shiboken6", "PyQt5", "PyQt6", "ui")


def _source(rel: str) -> str:
    return io.open(REPO_ROOT / rel, encoding="utf-8", newline="").read()


class TestServicesAreUiFree:
    @pytest.mark.parametrize("rel", NEW_SERVICE_FILES)
    def test_no_gui_imports(self, rel):
        tree = ast.parse(_source(rel))
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad += [a.name for a in node.names if a.name.split(".")[0] in FORBIDDEN_TOPS]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.split(".")[0] in FORBIDDEN_TOPS:
                    bad.append(mod)
        assert bad == [], f"{rel} 引入了 GUI/ui 依赖: {bad}"

    @pytest.mark.parametrize("rel", NEW_SERVICE_FILES)
    def test_no_threads_and_no_after(self, rel):
        """服务不起线程、不调 after/after_cancel（线程与定时器都留在界面侧）。"""
        tree = ast.parse(_source(rel))
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute) and f.attr in ("Thread", "after", "after_cancel"):
                    bad.append(f"{ast.unparse(f.value)}.{f.attr}()")
        assert bad == [], f"{rel} 起了线程或调了 after: {bad}"

    @pytest.mark.parametrize("rel", NEW_SERVICE_FILES)
    def test_no_i18n_usage(self, rel):
        """文案函数只能在界面调：服务不得使用 `_(...)` / `ui.i18n`。"""
        tree = ast.parse(_source(rel))
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_":
                bad.append(f"line {node.lineno}: _(...)")
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("ui.i18n"):
                bad.append(f"line {node.lineno}: from ui.i18n")
        assert bad == [], f"{rel} 直接用了 i18n 文案函数: {bad}"

    @pytest.mark.parametrize("rel", NEW_SERVICE_FILES)
    def test_no_tk_widget_calls(self, rel):
        """不得出现任何控件调用（pack/configure/destroy/winfo_exists 之类）。"""
        tree = ast.parse(_source(rel))
        widget_attrs = {"pack", "pack_forget", "configure", "winfo_exists", "destroy",
                        "grid", "place", "cget"}
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr in widget_attrs:
                bad.append(f"line {node.lineno}: {ast.unparse(node.func)}")
        assert bad == [], f"{rel} 碰了控件: {bad}"

    def test_services_layer_purity_script_is_green_on_these_files(self):
        """与 `scripts/check_services_purity.py` 互补：这三个文件里一个 ui.* 都没有。"""
        for rel in NEW_SERVICE_FILES:
            assert "import ui" not in _source(rel)
            assert "from ui" not in _source(rel)


# ══════════════════════════════════════════════════════════════════════
# 15. 网易云远程歌单的「只读、不落盘」约定（静态 AST 断言）
# ══════════════════════════════════════════════════════════════════════


class TestWyRemoteReadOnly:
    def test_does_not_import_playlist_manager(self):
        """远程歌单不进 PlaylistManager（因此永不落盘、永不参与本地编辑）。"""
        tree = ast.parse(_source("services/music_wy_remote.py"))
        mods = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods.append(node.module or "")
        assert not any("music_playlist" in m for m in mods), mods
        assert not any("app_music" in m for m in mods), mods

    def test_does_not_touch_the_filesystem(self):
        """不 import 任何文件/序列化模块，也没有 open(...) 调用。"""
        tree = ast.parse(_source("services/music_wy_remote.py"))
        mods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                mods.add((node.module or "").split(".")[0])
        assert mods & {"os", "pathlib", "json", "tempfile", "shutil", "pickle", "sqlite3"} == set(), mods
        bad = [
            ast.unparse(n.func)
            for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "open"
        ]
        assert bad == [], bad

    def test_prefix_convention_is_unchanged(self):
        """前缀约定原样保留：界面靠它把远程条目从本地歌单的编辑动作里排除。"""
        src = _source("services/music_wy_remote.py")
        assert 'REMOTE_PREFIX = "wy:"' in src
        app = _source("ui/app_music.py")
        assert "_WY_REMOTE_PREFIX = wy_remote.REMOTE_PREFIX" in app
        assert "startswith(_WY_REMOTE_PREFIX)" in app


# ══════════════════════════════════════════════════════════════════════
# 16. 可脱离 AppContext 构造（服务不注册进 AppContext 也能用）
# ══════════════════════════════════════════════════════════════════════


class TestConstructibleWithoutContext:
    def test_download_context_defaults(self):
        ctx = md.DownloadContext()
        assert ctx.temp_files == []
        assert ctx.sources is None
        assert ctx.http_get is None
        assert ctx.transcoder is None
        assert ctx.timeout == md.DOWNLOAD_TIMEOUT
        assert ctx.chunk_size == md.DOWNLOAD_CHUNK_SIZE
        assert ctx.max_temp_files == md.MAX_TEMP_FILES

    def test_download_context_accepts_only_a_list(self):
        shared = ["/tmp/a.mp3"]
        ctx = md.DownloadContext(temp_files=shared)
        assert ctx.temp_files is shared
        md.discard_temp_file("/tmp/a.mp3", ctx.temp_files)
        assert shared == []

    def test_wy_api_default_singleton_is_constructible(self):
        assert isinstance(wy.DEFAULT_API, wy.WyRemoteApi)
        assert wy.WyRemoteApi() is not wy.DEFAULT_API  # 每次构造是新对象，但无状态

    def test_module_level_functions_need_no_service_object(self):
        """三个模块的核心入口都是纯函数/数据类，不需要 AppContext 或实例。"""
        assert mo.normalize_keyword(" a ") == "a"
        assert mo.pager_plan(current_page=1, total_pages=1, has_more=False, busy=False).pages == [1]
        assert md.safe_temp_name("a/b") == "ab"
        assert md.is_non_audio_response("text/html") is True
        assert wy.remote_key("1") == "wy:1"
        assert wy.page_count(20) == 1
        assert wy.tracks_action("p", [], "p") == wy.TRACKS_RENDER

    def test_no_service_base_dependency(self):
        """这三个模块刻意不继承 `services.base.Service`：没有上下文也能直接用。"""
        for rel in NEW_SERVICE_FILES:
            assert "services.base" not in _source(rel), rel

    def test_public_api_is_declared(self):
        """`__all__` 与模块里的公开名一致（防"搬了但没导出"）。"""
        for module in (mo, md, wy):
            assert module.__all__, module.__name__
            for name in module.__all__:
                assert hasattr(module, name), f"{module.__name__}.{name} 不在模块里"
            public = {
                n for n in vars(module)
                if not n.startswith("_") and getattr(vars(module)[n], "__module__", "") == module.__name__
            }
            missing = sorted(public - set(module.__all__))
            assert missing == [], f"{module.__name__} 有公开名没写进 __all__: {missing}"


# ══════════════════════════════════════════════════════════════════════
# 17. 委托链集成（Mixin → services → 替身），全程无 Tk
# ══════════════════════════════════════════════════════════════════════


class _Widget:
    """最小控件替身：只需要 set / configure / cget / get / winfo_exists / pack。"""

    def __init__(self, entry="", exists=True):
        self.text = None
        self.value = None
        self.state = None
        self.image = None
        self.entry = entry
        self.exists = exists
        self.calls = []

    def set(self, value):
        self.value = value
        self.calls.append(("set", value))

    def configure(self, *args, **kw):
        self.calls.append(("configure", args, kw))
        if args:
            self.text = args[0]
        if "text" in kw:
            self.text = kw["text"]
        if "state" in kw:
            self.state = kw["state"]
        if "image" in kw:
            self.image = kw["image"]

    def cget(self, key):
        return getattr(self, key, "")

    def winfo_exists(self):
        return self.exists

    def get(self):
        return self.entry

    def pack(self, **kw):
        self.calls.append(("pack", kw))

    def pack_forget(self):
        self.calls.append(("pack_forget",))

    def update_idletasks(self):
        self.calls.append(("update_idletasks",))


#: 在线侧会碰到的控件，一律换成记录器
_ONLINE_WIDGETS = (
    "_music_search_status", "_music_search_btn", "_music_search_entry",
    "_music_quality_tag", "_music_now_label_top", "_music_now_label_sub",
    "_music_mini_title", "_music_end_label", "_music_cover_label",
    "_music_cover_artist", "_music_cover_album", "_music_progress_bar",
    "_music_cur_label", "_music_song_count_label",
    "_music_pager_prev", "_music_pager_next", "_music_pager_label", "_music_pager_page_box",
    "_music_wy_pager_page_label", "_music_wy_pager_prev", "_music_wy_pager_next",
)


class FakeCtkWidget:
    """`customtkinter` 控件的替身（只记录构造参数与 pack/bind）。"""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.calls = []

    def pack(self, **kwargs):
        self.calls.append(("pack", kwargs))

    def pack_forget(self):
        self.calls.append(("pack_forget",))

    def bind(self, *args):
        self.calls.append(("bind", args))


class FakeCtk:
    """把 `customtkinter` 模块整体换成记录器。

    这样 `_music_rebuild_pager` / `_music_add_search_row` 这类**真的建控件**的委托方法
    也能在无窗口环境下跑完（测试不建 Tk root、不开窗口），同时把 key 与参数留下来查。
    """

    X = "x"
    Y = "y"
    BOTH = "both"
    LEFT = "left"
    RIGHT = "right"
    W = "w"
    NORMAL = "normal"
    DISABLED = "disabled"

    def __init__(self):
        self.created = []

    def _make(self, kind):
        def factory(*args, **kwargs):
            widget = FakeCtkWidget(*args, **kwargs)
            self.created.append((kind, widget))
            return widget

        return factory

    def __getattr__(self, name):
        if name.startswith("CTk"):
            return self._make(name)
        raise AttributeError(name)


class FakeMgr:
    """`PlaylistManager` 的替身（只用到远程视图切回历史歌单那两行）。"""

    def __init__(self):
        self.history = type("PL", (), {"id": "hist"})()

    def get_or_create_history_playlist(self):
        return self.history

    def get_current_playlist(self):
        return None


def make_host(monkeypatch):
    """构造一个**真实**的 `MusicPlayerMixin` 宿主：碰 Tk 的部分换成记录器。

    这样验证的是"界面委托层真的连到服务上"，而不是"服务单测全绿、接线全断"。
    """
    import services.music_smtc as music_smtc
    import ui.app_music as app_music

    # SMTC 保持"不可用"，免得它透过 self._parent.after 干扰定时器计数
    monkeypatch.setattr(music_smtc, "_winsdk_available", False)
    fake_ctk = FakeCtk()
    monkeypatch.setattr(app_music, "ctk", fake_ctk)

    class Host(app_music.MusicPlayerMixin):
        def __init__(self):
            self.timers = []
            self._timer_seq = 0
            self.statuses = []
            self.rendered_rows = []
            self.rendered_lists = []
            self.play_online_calls = []
            self.cover_urls = []
            self.cover_data = []
            self.wy_loading = []
            self.wy_failed = []
            self.wy_fetch = []
            self.shown_playlist = None
            self.threads = []
            self.fake_ctk = fake_ctk
            self._MusicPlayerMixin__init_music()
            # 下载编排的替身：不转码；HTTP 也换成假响应，绝不联网
            self._music_download_ctx.http_get = lambda url, **kw: FakeResponse(b"AUDIO")
            self._music_download_ctx.transcoder = lambda path: None
            self._music_playlist_manager = FakeMgr()
            for name in _ONLINE_WIDGETS:
                setattr(self, name, _Widget())
            self._music_search_entry.entry = "七里香"
            self._music_pager_frame = object()
            self._music_wy_pager_frame = _Widget()
            self._music_playlist_widgets = []
            self._theme_refs = []
            # 标签页切换相关（`_music_switch_to_playlist_tab` 真的会被调到，不替身化）
            self._music_online_scroll = _Widget()
            for name in (
                "_music_mini_bar", "_music_main_frame", "_music_online_frame",
                "_music_playlist_frame", "_music_playlist_tab_btn",
                "_music_local_tab_btn", "_music_online_tab_btn", "_music_playlist_scroll",
            ):
                setattr(self, name, _Widget())
            self._stop_search_loading = lambda: None

        # ── Tk 侧最小替身 ──
        def after(self, ms, fn=None, *args):
            self._timer_seq += 1
            self.timers.append({"id": self._timer_seq, "ms": ms, "fn": fn, "args": args})
            return self._timer_seq

        def after_cancel(self, tid):
            self.timers = [t for t in self.timers if t["id"] != tid]

        def set_status(self, message, level="info"):
            self.statuses.append((message, level))

        def _trigger_ach(self, key):
            pass

        def _check_ach(self, key, value=True):
            pass

        def _music_render_search_rows(self, results):
            self.rendered_rows.append(list(results))

        def _rebuild_playlist_song_list(self, playlist, readonly=False):
            self.rendered_lists.append((playlist, readonly))

        def _rebuild_playlist_sidebar(self):
            pass

        def _update_sort_buttons(self, mode):
            pass

        def _music_show_playlist(self, pid):
            self.shown_playlist = pid

        # `_music_switch_to_playlist_tab` / `_music_show_wy_remote_playlist` /
        # `_music_render_wy_remote_songs` 都跑真实实现（它们正是本轮改过的方法）。

        def _music_show_wy_remote_loading(self, pl_id):
            self.wy_loading.append(pl_id)

        def _music_show_wy_remote_failed(self, pl_id):
            self.wy_failed.append(pl_id)

        def _music_wy_fetch_remote_songs(self, pl_id):
            self.wy_fetch.append(pl_id)

        def _music_on_prefetch_ready(self, *args):
            pass

        def _play_online_file(self, path, info, pos=0, history_origin=None, quality=""):
            self.play_online_calls.append((path, info, pos, history_origin, quality))

        def _display_cover(self, data):
            self.cover_data.append(data)

        # 注意：`_fetch_and_display_online_cover` 与 `_update_now_playing_info`
        # **不**在替身里 —— 它们正是本轮要验证的委托方法，必须跑真实实现。

        def _music_refresh_copy_btn_state(self):
            pass

    return Host()


class FakeThread:
    """`threading.Thread` 的替身：只记录，不真起线程（避免测试与线程赛跑）。"""

    def __init__(self, target=None, args=(), daemon=None, **kwargs):
        self.target = target
        self.args = args
        self.daemon = daemon

    def start(self):
        return None

    def run_now(self):
        return self.target(*self.args)


@pytest.fixture
def fake_threads(monkeypatch):
    """把 `threading.Thread` 换成记录器，返回已 start 的记录（可 `run_now()` 立刻跑）。"""
    import ui.app_music as app_music

    started = []

    class Recorder(FakeThread):
        def start(self):
            started.append(self)

    monkeypatch.setattr(app_music.threading, "Thread", Recorder)
    return started


class TestMixinDownloadWiring:
    def test_download_ctx_shares_the_ui_temp_file_list(self, monkeypatch):
        host = make_host(monkeypatch)
        # 与 1.4-A 采纳同一个 _music_metadata_cache 是同一手法：同一个对象
        assert host._music_download_ctx.temp_files is host._music_temp_files

    def test_download_to_temp_registers_into_ui_list(self, monkeypatch, tempdir):
        host = make_host(monkeypatch)
        path = host._music_download_to_temp("http://u/a.mp3", "七里香")
        assert path is not None
        assert path in host._music_temp_files
        assert Path(path).exists()
        assert Path(path).name.startswith("fmcl_")

    def test_discard_temp_file_removes_from_ui_list(self, monkeypatch, tempdir):
        host = make_host(monkeypatch)
        path = host._music_download_to_temp("http://u/a.mp3", "歌")
        host._discard_temp_file(path)
        assert host._music_temp_files == []
        assert not Path(path).exists()

    def test_cleanup_temp_files_clears_ui_list(self, monkeypatch, tempdir):
        host = make_host(monkeypatch)
        path = host._music_download_to_temp("http://u/a.mp3", "歌")
        host._music_cleanup_temp_files()
        assert host._music_temp_files == []
        assert not Path(path).exists()

    def test_try_download_from_source_wiring(self, monkeypatch, tempdir):
        host = make_host(monkeypatch)
        src = FakeSource("kw", urls={"320k": "http://u/320"})
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"kw": src})
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        path, url, quality = host._try_download_from_source(FakeInfo(source="kw"), "320k")
        assert Path(path).read_bytes() == b"AUDIO"
        assert (url, quality) == ("http://u/320", "320k")
        assert path in host._music_temp_files

    def test_get_download_headers_and_cookies_wiring(self, monkeypatch):
        host = make_host(monkeypatch)
        src = FakeSource("bili", headers={"Referer": "http://b"}, cookies={"buvid": "1"})
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"bili": src})
        assert host._music_get_download_headers("bili") == {"Referer": "http://b"}
        assert host._music_get_download_cookies("bili") == {"buvid": "1"}
        assert host._music_get_download_headers("nope") == {}
        assert host._music_get_download_cookies("nope") is None

    def test_resolve_fallback_wiring(self, monkeypatch):
        host = make_host(monkeypatch)
        info = FakeInfo(source="kw")
        monkeypatch.setattr(ms, "resolve_track", lambda i, q: (i, "http://u"))
        assert host._resolve_fallback(info, "320k") == (info, "http://u")

    def test_resolve_fallback_swallows_exception(self, monkeypatch):
        host = make_host(monkeypatch)

        def boom(i, q):
            raise RuntimeError("兜底炸了")

        monkeypatch.setattr(ms, "resolve_track", boom)
        assert host._resolve_fallback(FakeInfo(), "320k") is None

    def test_download_fallback_result_wiring(self, monkeypatch, tempdir):
        host = make_host(monkeypatch)
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        fb = FakeInfo(source="kg", name="同款歌")
        out = host._download_fallback_result((fb, "http://u/fb"), "320k")
        assert out is not None
        path, info, quality = out
        assert info is fb
        assert quality == "320k"
        assert path in host._music_temp_files
        # "已切换到其它音源"的提示走 after 切主线程（文案的 i18n 与 after 都在界面）
        assert host.timers[-1]["ms"] == 0
        host.timers[-1]["fn"]()
        assert len(host.statuses) == 1
        assert host.statuses[0][1] == "info"

    def test_notify_fallback_source_uses_source_display_name(self, monkeypatch):
        host = make_host(monkeypatch)
        import ui.app_music as app_music

        seen = []
        monkeypatch.setattr(app_music, "_", lambda key, **kw: seen.append((key, kw)) or "MSG")
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"kg": FakeSource("kg", source_name="酷狗音乐")})
        host._notify_fallback_source("kg")
        host.timers[-1]["fn"]()
        # 音源显示名从服务来，i18n 文案与 after 切主线程留在界面
        assert seen == [("music_fallback_status", {"source": "酷狗音乐"})]
        assert host.statuses == [("MSG", "info")]

    def test_notify_fallback_source_falls_back_to_id(self, monkeypatch):
        host = make_host(monkeypatch)
        import ui.app_music as app_music

        seen = []
        monkeypatch.setattr(app_music, "_", lambda key, **kw: seen.append((key, kw)) or "MSG")
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {})
        host._notify_fallback_source("kg")
        host.timers[-1]["fn"]()
        assert seen == [("music_fallback_status", {"source": "kg"})]

    def test_fetch_online_song_all_sources_fail(self, monkeypatch, tempdir):
        host = make_host(monkeypatch)
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"kw": FakeSource("kw", urls={})})
        monkeypatch.setattr(ms, "resolve_track", lambda i, q: None)
        info = FakeInfo(source="kw")
        assert host._fetch_online_song(info, "320k") == (None, info, "320k")

    def test_fetch_online_song_primary_success(self, monkeypatch, tempdir):
        host = make_host(monkeypatch)
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"kw": FakeSource("kw", urls={"320k": "http://u/320"})})
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        info = FakeInfo(source="kw")
        path, got_info, quality = host._fetch_online_song(info, "320k")
        assert Path(path).exists()
        assert got_info is info
        assert quality == "320k"

    def test_fetch_online_song_uses_cross_source_fallback(self, monkeypatch, tempdir):
        host = make_host(monkeypatch)
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"kw": FakeSource("kw", urls={})})
        fb = FakeInfo(source="kg")
        monkeypatch.setattr(ms, "resolve_track", lambda i, q: (fb, "http://u/fb"))
        monkeypatch.setattr(md.music_audio, "validate_audio_file_header", lambda p: True)
        monkeypatch.setattr(md.music_audio, "validate_audio_duration", lambda p, d: True)
        path, got_info, quality = host._fetch_online_song(FakeInfo(source="kw"), "320k", "auto")
        assert got_info is fb
        assert quality == "320k"
        assert path in host._music_temp_files


class TestMixinSearchWiring:
    def test_do_search_reads_entry_and_normalizes(self, monkeypatch):
        host = make_host(monkeypatch)
        calls = []
        host._music_start_search = lambda page: calls.append(page)
        host._music_search_entry.entry = "  七里香  "
        host._music_do_search()
        assert host._music_search_keyword == "七里香"
        assert calls == [mo.FIRST_SEARCH_PAGE]

    def test_do_search_ignores_blank_entry(self, monkeypatch):
        host = make_host(monkeypatch)
        calls = []
        host._music_start_search = lambda page: calls.append(page)
        host._music_search_entry.entry = "   "
        host._music_do_search()
        assert calls == []
        assert host._music_search_keyword == ""

    def test_start_search_busy_returns_early(self, monkeypatch, fake_threads):
        host = make_host(monkeypatch)
        host._music_search_busy = True
        host._music_start_search(3)
        assert fake_threads == []
        assert host._music_search_page == 1  # 没被改

    def test_start_search_illegal_page_returns_early(self, monkeypatch, fake_threads):
        host = make_host(monkeypatch)
        host._music_start_search(0)
        assert fake_threads == []

    def test_start_search_plans_page_size_and_starts_thread(self, monkeypatch, fake_threads):
        host = make_host(monkeypatch)
        src = FakeSource("wy", limits={"search": 20})
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"wy": src})
        host._music_selected_source = "wy"
        host._music_search_keyword = "七里香"
        host._music_start_search(2)
        assert host._music_search_page == 2
        assert host._music_search_page_size == 20  # 按音源服务端限制
        assert host._music_search_busy is True
        assert host._music_search_total_pages == 0
        assert host._music_search_seq == 1
        assert host._music_search_btn.state == "disabled"
        assert len(fake_threads) == 1
        assert fake_threads[0].args == ("七里香", 2, 1)

    def test_online_search_thread_missing_source_yields_empty(self, monkeypatch):
        host = make_host(monkeypatch)
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {})
        host._music_selected_source = "zzz"
        host._music_search_seq = 5
        host._music_online_search_thread("七里香", 1, 5)
        assert host.timers[-1]["ms"] == 0
        host.timers[-1]["fn"]()
        assert host.rendered_rows == [[]]

    def test_online_search_thread_source_error_yields_empty(self, monkeypatch):
        host = make_host(monkeypatch)
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"kw": FakeSource("kw", search_error=RuntimeError("炸"))})
        host._music_selected_source = "kw"
        host._music_search_seq = 1
        host._music_online_search_thread("七里香", 1, 1)
        host.timers[-1]["fn"]()
        assert host.rendered_rows == [[]]

    def test_online_search_thread_passes_results_through(self, monkeypatch):
        host = make_host(monkeypatch)
        items = [FakeInfo(songmid="a"), FakeInfo(songmid="b")]
        src = FakeSource("kw", items=items)
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"kw": src})
        host._music_selected_source = "kw"
        host._music_search_page_size = 30
        host._music_search_seq = 1
        host._music_online_search_thread("七里香", 1, 1)
        host.timers[-1]["fn"]()
        assert host.rendered_rows == [items]
        assert src.search_calls == [("七里香", 1, 30)]

    def test_rebuild_search_results_page_math_from_source_total(self, monkeypatch):
        host = make_host(monkeypatch)
        src = FakeSource("wy", total=45)
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"wy": src})
        host._music_selected_source = "wy"
        host._music_search_page_size = 20
        host._music_search_page = 1
        host._music_rebuild_search_results([FakeInfo()] * 20)
        assert host._music_search_busy is False
        assert host._music_search_total_pages == 3
        assert host._music_search_has_more is True
        assert host._music_search_btn.state == "normal"
        assert len(host.rendered_rows) == 1

    def test_rebuild_search_results_heuristic_without_total(self, monkeypatch):
        host = make_host(monkeypatch)
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"kw": FakeSource("kw", total=0)})
        host._music_selected_source = "kw"
        host._music_search_page_size = 30
        host._music_search_page = 1
        host._music_rebuild_search_results([FakeInfo()] * 30)
        assert host._music_search_total_pages == 0
        assert host._music_search_has_more is True

    def test_rebuild_search_results_stale_seq_is_dropped(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_search_seq = 3
        host._music_search_busy = True
        host._music_rebuild_search_results([FakeInfo()], seq=2)
        assert host.rendered_rows == []
        assert host._music_search_busy is True  # 过期请求不改状态

    def test_rebuild_pager_pages_and_button_states(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_search_total_pages = 4
        host._music_search_has_more = True
        host._music_search_page = 2
        host._music_rebuild_pager()
        assert [w["page"] for w in host._music_pager_widgets] == [1, 2, 3, 4]
        assert host._music_pager_prev.state == "normal"
        assert host._music_pager_next.state == "normal"

    def test_rebuild_pager_busy_disables_buttons(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_search_total_pages = 4
        host._music_search_page = 2
        host._music_search_busy = True
        host._music_rebuild_pager()
        assert host._music_pager_prev.state == "disabled"
        assert host._music_pager_next.state == "disabled"
        # 页码按钮仍然画满（忙碌只影响按钮状态）
        assert [w["page"] for w in host._music_pager_widgets] == [1, 2, 3, 4]

    def test_rebuild_pager_without_frame_returns_early(self, monkeypatch):
        host = make_host(monkeypatch)
        del host._music_pager_frame
        host._music_rebuild_pager()
        assert host._music_pager_widgets == []

    def test_search_go_page_guards(self, monkeypatch):
        host = make_host(monkeypatch)
        started = []
        host._music_start_search = started.append
        host._music_search_keyword = "七里香"

        host._music_search_busy = True
        host._music_search_go_page(2)
        assert started == []

        host._music_search_busy = False
        host._music_search_keyword = ""
        host._music_search_go_page(2)
        assert started == []

        host._music_search_keyword = "七里香"
        host._music_search_results = [FakeInfo()]
        host._music_search_page = 1
        host._music_search_go_page(1)  # 同页且有结果 -> 不发
        assert started == []

        host._music_search_go_page(2)
        assert started == [2]

    def test_search_prev_and_next_page_arithmetic(self, monkeypatch):
        host = make_host(monkeypatch)
        started = []
        host._music_start_search = started.append
        host._music_search_keyword = "k"
        host._music_search_page = 3
        host._music_search_next_page()
        assert started == [4]
        host._music_search_prev_page()
        assert started == [4, 2]

    def test_original_backfill_identity_guard(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_online_frame = _Widget()
        results = [FakeInfo()]
        host._music_search_results = results
        host._music_apply_original_backfill(results)  # 同一对象 -> 渲染
        assert len(host.rendered_rows) == 1
        host._music_apply_original_backfill([FakeInfo()])  # 不同对象 -> 丢弃
        assert len(host.rendered_rows) == 1

    def test_resolve_auto_quality_wiring(self, monkeypatch):
        host = make_host(monkeypatch)
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"wy": FakeSource("wy")})
        assert host._music_resolve_auto_quality(FakeInfo(source="wy", qualities=["320k"])) == "320k"
        assert host._music_resolve_auto_quality(None) == "128k"

    def test_resolve_auto_quality_async_wiring(self, monkeypatch):
        host = make_host(monkeypatch)
        richer = FakeInfo(source="wy", songmid="m1", qualities=["flac"])
        src = FakeSource("wy", items=[richer])
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"wy": src})
        info = FakeInfo(source="wy", songmid="m1", qualities=[])
        got_info, quality = host._music_resolve_auto_quality_async(info)
        assert got_info is richer
        assert quality == "flac"

    def test_resolve_auto_quality_async_none(self, monkeypatch):
        host = make_host(monkeypatch)
        assert host._music_resolve_auto_quality_async(None) == (None, "128k")


class TestMixinStreamWiring:
    def test_stale_seq_dropped(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_stream_seq = 2
        host._music_on_stream_ready(1, "/tmp/a.mp3", FakeInfo())
        assert host.play_online_calls == []

    def test_play_path(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_stream_seq = 2
        info = FakeInfo()
        host._music_on_stream_ready(2, "/tmp/a.mp3", info, None, "320k")
        assert host.play_online_calls == [("/tmp/a.mp3", info, 0, None, "320k")]
        assert host._music_search_status.text == ""

    def test_failed_path_sets_status(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_stream_seq = 2
        host._music_on_stream_ready(2, None, FakeInfo())
        assert host.play_online_calls == []
        assert host._music_search_status.text is not None

    def test_fetch_cover_uses_service_and_after(self, monkeypatch, fake_threads):
        host = make_host(monkeypatch)
        monkeypatch.setattr(mo.requests, "get", make_getter(b"JPEG", "image/jpeg", 200))
        host._fetch_and_display_online_cover("http://x/c.jpg")
        assert len(fake_threads) == 1
        fake_threads[0].run_now()
        assert host.timers[-1]["ms"] == 0
        host.timers[-1]["fn"]()
        assert host.cover_data == [b"JPEG"]

    def test_fetch_cover_non_200_does_not_schedule(self, monkeypatch, fake_threads):
        host = make_host(monkeypatch)
        monkeypatch.setattr(mo.requests, "get", make_getter(b"x", status_code=404))
        host._fetch_and_display_online_cover("http://x/c.jpg")
        fake_threads[0].run_now()
        assert host.timers == []
        assert host.cover_data == []


class TestMixinNowPlayingWiring:
    def test_online_branch(self, monkeypatch):
        host = make_host(monkeypatch)
        info = FakeInfo(name="七里香", singer="周杰伦", album_name="专辑", interval=299)
        host._music_is_online_playing = True
        host._music_current_online_info = info
        host._music_current_quality = "320k"
        fetched = []
        host._fetch_and_display_online_cover = fetched.append
        host._music_smtc = type("S", (), {"update_now_playing": lambda self, *a: fetched.append(a)})()
        host._update_now_playing_info()
        assert host._music_quality_tag.text == "320K"
        assert host._music_now_label_top.text == "七里香"
        assert host._music_now_label_sub.text == "周杰伦 - 专辑"
        assert host._music_mini_title.text == "七里香 · 320K"
        assert host._music_now_title == "七里香"
        assert host._music_now_artist == "周杰伦"

    def test_online_branch_without_cover_url(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_is_online_playing = True
        host._music_current_online_info = FakeInfo(img="")
        host._music_current_quality = "flac"
        fetched = []
        host._fetch_and_display_online_cover = fetched.append
        host._music_smtc = type("S", (), {"update_now_playing": lambda self, *a: None})()
        host._update_now_playing_info()
        assert fetched == []

    def test_empty_branch(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_playlist = []
        host._music_current_index = -1
        host._update_now_playing_info()
        assert host._music_progress_bar.value == 0
        assert host._music_cur_label.text == "0:00"
        assert host._music_end_label.text == "0:00"
        assert host._music_now_title == ""
        assert host._music_quality_tag.text == ""

    def test_local_branch(self, monkeypatch, tmp_path):
        host = make_host(monkeypatch)
        path = str(tmp_path / "a.mp3")
        host._get_current_file = lambda: path
        host._get_metadata = lambda p: {
            "title": "本地歌", "artist": "某人", "album": "专辑", "duration": 200,
            "bitrate": 320000, "has_cover": True, "cover_data": b"IMG",
        }
        host._update_now_playing_info()
        assert host._music_now_label_top.text == "本地歌"
        assert host._music_now_label_sub.text == "某人 - 专辑"
        assert host._music_end_label.text == "3:20"
        assert host.cover_data == [b"IMG"]
        assert host._music_quality_tag.text == "320K"

    def test_local_branch_without_cover(self, monkeypatch, tmp_path):
        host = make_host(monkeypatch)
        path = str(tmp_path / "a.mp3")
        host._get_current_file = lambda: path
        host._get_metadata = lambda p: {"title": "T", "duration": 0, "has_cover": False}
        host._update_now_playing_info()
        assert host.cover_data == []
        assert host._music_cover_label.text == "🎵"


class TestMixinSearchRowWiring:
    def test_row_uses_plan_values_and_i18n_key(self, monkeypatch):
        host = make_host(monkeypatch)
        import ui.app_music as app_music

        seen = []
        monkeypatch.setattr(app_music, "_", lambda key, **kw: seen.append((key, kw)) or "TAG")
        info = FakeInfo(name="字" * 40, singer="周杰伦", source="kw", interval=240,
                        play_count=12345, is_original=True, original_name="原唱")
        host._music_add_search_row(4, info)
        assert seen == [("music_original_tag_with_name", {"name": "原唱"})]
        labels = [w for kind, w in host.fake_ctk.created if kind == "CTkLabel"]
        texts = [w.kwargs.get("text") for w in labels]
        assert "字" * 33 + "..." + " - 周杰伦" in texts  # 歌名截断 33 + "..."
        assert "5" in texts  # 序号 idx + 1
        assert "4:00" in texts
        assert "1.2w" in texts
        assert "KW" in texts
        assert "视频" not in texts
        assert len(host._music_search_widgets) == 1
        assert host._music_search_widgets[0]["index"] == 4

    def test_row_without_original_tag(self, monkeypatch):
        host = make_host(monkeypatch)
        import ui.app_music as app_music

        seen = []
        monkeypatch.setattr(app_music, "_", lambda key, **kw: seen.append((key, kw)) or "TAG")
        host._music_add_search_row(0, FakeInfo(is_original=False))
        assert seen == []  # 非原唱 -> 一次 i18n 都不调
        host.fake_ctk.created.clear()
        host._music_add_search_row(0, FakeInfo(is_original=True))
        assert seen == [("music_original_tag", {})]


class TestMixinWyRemoteWiring:
    def _api(self, monkeypatch, **kwargs):
        api = FakeWyApi(**kwargs)
        monkeypatch.setattr(wy, "DEFAULT_API", api)
        return api

    def test_remote_key_uses_service_prefix(self, monkeypatch):
        host = make_host(monkeypatch)
        assert host._music_wy_remote_key("123") == "wy:123"

    def test_sync_worker_puts_sync_event(self, monkeypatch):
        host = make_host(monkeypatch)
        playlists = [{"id": "1", "name": "p", "track_count": 2}]
        self._api(monkeypatch, playlists=playlists)
        host._music_wy_sync_worker(7)
        assert host._music_wy_queue.get_nowait() == ("sync", 7, "ok", playlists)

    def test_sync_worker_logged_out_event(self, monkeypatch):
        host = make_host(monkeypatch)
        self._api(monkeypatch, logged_in=False)
        host._music_wy_sync_worker(1)
        assert host._music_wy_queue.get_nowait() == ("sync", 1, "logged_out", [])

    def test_sync_worker_error_event(self, monkeypatch):
        host = make_host(monkeypatch)
        self._api(monkeypatch, playlists_error=RuntimeError("炸"))
        host._music_wy_sync_worker(2)
        assert host._music_wy_queue.get_nowait() == ("sync", 2, "error", [])

    def test_tracks_worker_puts_tracks_event(self, monkeypatch):
        host = make_host(monkeypatch)
        infos = [FakeInfo(songmid="a")]
        self._api(monkeypatch, tracks=infos)
        host._music_wy_tracks_worker("p1")
        assert host._music_wy_queue.get_nowait() == ("tracks", "p1", infos)

    def test_tracks_worker_failure_puts_none(self, monkeypatch):
        host = make_host(monkeypatch)
        self._api(monkeypatch, tracks_error=RuntimeError("炸"))
        host._music_wy_tracks_worker("p1")
        assert host._music_wy_queue.get_nowait() == ("tracks", "p1", None)

    def test_apply_tracks_renders_when_viewing(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_view_id = "p1"
        host._music_wy_loading_ids.add("p1")
        host._music_wy_apply_tracks("p1", [real_info(songmid="a")])
        assert "p1" not in host._music_wy_loading_ids
        assert len(host._music_wy_remote_cache["p1"]) == 1
        assert len(host.rendered_lists) == 1
        assert host.rendered_lists[0][1] is True  # readonly=True（远程歌单禁止编辑）

    def test_apply_tracks_caches_only_when_switched_away(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_view_id = "p2"
        host._music_wy_apply_tracks("p1", [real_info(songmid="a")])
        assert "p1" in host._music_wy_remote_cache
        assert host.rendered_lists == []
        assert host.wy_failed == []

    def test_apply_tracks_none_is_not_cached(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_view_id = "p2"
        host._music_wy_apply_tracks("p1", None)
        assert host._music_wy_remote_cache == {}

    def test_apply_tracks_failure_shows_failed(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_view_id = "p1"
        host._music_wy_apply_tracks("p1", None)
        assert host.wy_failed == ["p1"]

    def test_apply_sync_stale_is_dropped(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_sync_seq = 5
        host._music_wy_sync_busy = True
        host._music_wy_apply_sync(4, "ok", [{"id": "1", "name": "p", "track_count": 1}])
        assert host._music_wy_remote_playlists == []
        assert host._music_wy_sync_busy is True

    def test_apply_sync_logged_out_clears_everything(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_sync_busy = True
        host._music_wy_sync_failed = True
        host._music_wy_remote_view_id = "p1"
        host._music_wy_remote_view_songs = ["s"]
        host._music_wy_remote_playlists = [{"id": "p1", "name": "a", "track_count": 1}]
        host._music_wy_remote_cache["p1"] = ["s"]
        host._music_wy_loading_ids.add("p1")
        host._music_tab_mode = "playlist"
        host._music_wy_sync_seq = 1  # 同步序号对得上，才不是"过期结果"
        host._music_wy_apply_sync(1, "logged_out", [])
        assert host._music_wy_sync_busy is False
        assert host._music_wy_sync_failed is False
        assert host._music_wy_remote_view_id is None
        assert host._music_wy_remote_view_songs == []
        assert host._music_wy_remote_playlists == []
        assert host._music_wy_remote_cache == {}
        assert host._music_wy_loading_ids == set()
        assert host.shown_playlist == "hist"  # 在歌单标签页 -> 切回历史

    def test_apply_sync_error_keeps_previous_list(self, monkeypatch):
        host = make_host(monkeypatch)
        old = [{"id": "1", "name": "a", "track_count": 1}]
        host._music_wy_remote_playlists = list(old)
        host._music_wy_sync_seq = 1  # 同步序号对得上，才不是"过期结果"
        host._music_wy_apply_sync(1, "error", None)
        assert host._music_wy_sync_failed is True
        assert host._music_wy_remote_playlists == old

    def test_apply_sync_unchanged(self, monkeypatch):
        host = make_host(monkeypatch)
        same = [{"id": "1", "name": "a", "track_count": 1}]
        host._music_wy_remote_playlists = list(same)
        host._music_wy_sync_failed = True
        host._music_wy_sync_seq = 1  # 同步序号对得上，才不是"过期结果"
        host._music_wy_apply_sync(1, "ok", list(same))
        assert host._music_wy_sync_failed is False
        assert host._music_wy_remote_playlists == same

    def test_apply_sync_update_drops_removed_caches(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_playlists = [{"id": "1", "name": "a", "track_count": 1}]
        host._music_wy_remote_cache["1"] = ["s"]
        host._music_wy_loading_ids.add("1")
        new = [{"id": "2", "name": "b", "track_count": 2}]
        host._music_wy_sync_seq = 1  # 同步序号对得上，才不是"过期结果"
        host._music_wy_apply_sync(1, "ok", new)
        assert host._music_wy_remote_playlists == new
        assert host._music_wy_remote_cache == {}
        assert host._music_wy_loading_ids == set()

    def test_apply_sync_update_switches_back_to_history(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_playlists = [{"id": "1", "name": "a", "track_count": 1}]
        host._music_wy_remote_view_id = "1"
        host._music_wy_remote_view_songs = ["s"]
        host._music_tab_mode = "playlist"
        host._music_wy_sync_seq = 1  # 同步序号对得上，才不是"过期结果"
        host._music_wy_apply_sync(1, "ok", [{"id": "2", "name": "b", "track_count": 2}])
        assert host._music_wy_remote_view_id is None
        assert host.shown_playlist == "hist"

    def test_update_pager_hidden_for_short_playlist(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_view_id = "p1"
        host._music_wy_remote_view_songs = list(range(20))
        host._music_wy_update_pager()
        assert ("pack_forget",) in host._music_wy_pager_frame.calls

    def test_update_pager_visible_and_clamped(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_view_id = "p1"
        host._music_wy_remote_view_songs = list(range(45))
        host._music_wy_remote_page = 9
        host._music_wy_update_pager()
        assert host._music_wy_remote_page == 3
        assert host._music_wy_pager_prev.state == "normal"
        assert host._music_wy_pager_next.state == "disabled"
        assert ("pack", {"fill": "x", "pady": (4, 0)}) in host._music_wy_pager_frame.calls

    def test_update_pager_hidden_without_view(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_view_id = None
        host._music_wy_remote_view_songs = list(range(45))
        host._music_wy_update_pager()
        assert ("pack_forget",) in host._music_wy_pager_frame.calls

    def test_render_remote_songs_slices_the_page(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_page = 2
        songs = list(range(45))
        host._music_render_wy_remote_songs("p1", songs)
        assert host._music_wy_remote_view_songs == songs
        assert host._music_wy_remote_page == 2
        assert len(host.rendered_lists) == 1
        playlist, readonly = host.rendered_lists[0]
        assert readonly is True
        assert playlist.songs == songs[20:40]

    def test_render_remote_songs_clamps_overflow_page(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_page = 9
        host._music_render_wy_remote_songs("p1", list(range(25)))
        assert host._music_wy_remote_page == 2

    def test_wy_go_page_guards(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_view_id = "p1"
        host._music_wy_remote_view_songs = list(range(45))
        host._music_wy_remote_page = 1

        host._music_wy_remote_view_id = None
        host._music_wy_go_page(2)
        assert host.rendered_lists == []

        host._music_wy_remote_view_id = "p1"
        host._music_wy_go_page(9)  # 越界
        assert host.rendered_lists == []
        host._music_wy_go_page(1)  # 同页
        assert host.rendered_lists == []
        host._music_wy_go_page(3)
        assert host._music_wy_remote_page == 3
        assert len(host.rendered_lists) == 1

    def test_show_remote_playlist_uses_cache(self, monkeypatch):
        host = make_host(monkeypatch)
        cached = [real_info(songmid="a")]
        host._music_wy_remote_cache["p1"] = cached
        host._music_show_wy_remote_playlist("p1")
        assert host._music_wy_remote_view_id == "p1"
        assert host._music_wy_remote_page == 1
        assert host._music_tab_mode == "playlist"
        assert len(host.rendered_lists) == 1
        assert host.wy_fetch == []

    def test_show_remote_playlist_empty_cache_still_renders(self, monkeypatch):
        """空歌单成功缓存 [] 也要直接渲染，不能再去后台拉（None 与 [] 语义不同）。"""
        host = make_host(monkeypatch)
        host._music_wy_remote_cache["p1"] = []
        host._music_show_wy_remote_playlist("p1")
        assert len(host.rendered_lists) == 1
        assert host.wy_fetch == []
        assert host.wy_loading == []

    def test_show_remote_playlist_missing_cache_triggers_fetch(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_show_wy_remote_playlist("p1")
        assert host.wy_loading == ["p1"]
        assert host.wy_fetch == ["p1"]
        assert host.rendered_lists == []

    def test_dispatcher_tick_handles_events_and_reschedules(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_remote_view_id = "p1"
        host._music_wy_sync_seq = 1
        host._music_wy_queue.put(wy.make_event(wy.EVENT_TRACKS, "p1", [real_info(songmid="a")]))
        host._music_wy_queue.put(wy.make_event(wy.EVENT_SYNC, 1, "error", None))
        host._music_wy_dispatcher_tick()
        assert host._music_wy_queue.empty()
        assert len(host.rendered_lists) == 1  # tracks 事件被处理
        assert host._music_wy_sync_failed is True  # sync error 事件被处理
        assert host.timers[-1]["ms"] == wy.DISPATCH_INTERVAL_MS
        assert host.timers[-1]["fn"] == host._music_wy_dispatcher_tick

    def test_dispatcher_tick_ignores_unknown_event_kind(self, monkeypatch):
        host = make_host(monkeypatch)
        host._music_wy_queue.put(wy.make_event(wy.EVENT_PREFETCH_FAIL, "p1"))
        host._music_wy_dispatcher_tick()
        assert host._music_wy_queue.empty()
        assert host.timers[-1]["ms"] == wy.DISPATCH_INTERVAL_MS

    def test_switch_to_playlist_tab_restores_cached_remote_view(self, monkeypatch):
        host = make_host(monkeypatch)
        for name in ("_music_mini_bar", "_music_main_frame", "_music_online_frame",
                     "_music_playlist_frame", "_music_playlist_tab_btn",
                     "_music_local_tab_btn", "_music_online_tab_btn", "_music_playlist_scroll"):
            setattr(host, name, _Widget())
        host._stop_search_loading = lambda: None
        host._music_wy_remote_view_id = "p1"
        host._music_wy_remote_cache["p1"] = []
        host._music_switch_to_playlist_tab()
        assert host._music_tab_mode == "playlist"
        assert len(host.rendered_lists) == 1
        assert host.wy_fetch == []
