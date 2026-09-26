"""``services/browse_common.py`` 的离线单测（阶段 1 任务 1.8-B）。

覆盖任务书里点名的边界：下载量格式化（0 / 负数 / 9999 / 10000 / 大数 / None）、
分页（页数 / 越界钳制 / 空列表 / **本地条目数口径**）、结果去重排序与来源统计、
权限分档、加载器兼容映射。

全部离线：不联网、不建窗口、不依赖机器上有游戏目录。
"""

from __future__ import annotations

import pytest

from services import browse_common
from services.browse_common import (
    SearchOutcome,
    browsable_count,
    clamp_page,
    compat_loader,
    dedupe_hits_by_project_id,
    format_downloads,
    loader_tags,
    loader_tags_text,
    merge_sources,
    normalize_search,
    page_slice,
    paginate,
    permission_counts,
    permission_summary,
    sort_hits_by_downloads,
    split_result,
    tag_bar_display,
    total_pages,
)


class TestFormatDownloads:
    """下载量格式化：与三个窗口里逐字相同的那份实现比对边界。"""

    @pytest.mark.parametrize(
        "count,expected",
        [
            (0, "0"),
            (-5, "-5"),
            (1, "1"),
            (999, "999"),
            # 记录现状、疑为缺陷：9999/1000 是 9.999，``:.1f`` 四舍五入成 "10.0"，
            # 于是 9999 与 10000 显示成同一个字符串（原文如此，本轮只搬家不改）。
            (9999, "10.0K"),
            (10000, "10.0K"),
            (999_999, "1000.0K"),
            (1_000_000, "1.0M"),
            (123_456_789, "123.5M"),
        ],
    )
    def test_boundaries(self, count: int, expected: str) -> None:
        assert format_downloads(count) == expected

    def test_float_input_still_formats(self) -> None:
        """后端偶尔给 float（原文的比较/除法一样能跑，不改语义）。"""
        assert format_downloads(1500.0) == "1.5K"

    def test_none_raises_type_error(self) -> None:
        """记录现状、疑为缺陷：``None`` 会在 ``count >= 1_000_000`` 上抛 TypeError。

        原文如此（``item.get("downloads", 0)`` 只在"后端显式给了 None"时才会漏进来），
        本轮只搬家不改行为，因此这里**钉住异常**而不是钉住兜底值。
        """
        with pytest.raises(TypeError):
            format_downloads(None)


class TestPagination:
    @pytest.mark.parametrize(
        "total,page_size,expected",
        [(0, 10, 1), (1, 10, 1), (10, 10, 1), (11, 10, 2), (30, 10, 3), (300, 10, 30), (0, 1, 1)],
    )
    def test_total_pages(self, total: int, page_size: int, expected: int) -> None:
        assert total_pages(total, page_size) == expected

    @pytest.mark.parametrize("page,pages,expected", [(0, 3, 1), (1, 3, 1), (3, 3, 3), (99, 3, 3), (-4, 3, 1)])
    def test_clamp_page(self, page: int, pages: int, expected: int) -> None:
        assert clamp_page(page, pages) == expected

    def test_paginate_grid_matches_resource_and_server_service(self) -> None:
        """与 ``resource_service`` / ``server_service`` 的同名函数**逐点**一致。

        三份实现刻意不互相 import（各服务只服务自己的界面域），所以用这条测试
        钉住它们不随时间漂移 —— 与 ``services/resource_service.py`` 里那条注释
        说的是同一套办法。
        """
        from services.resource_service import paginate as resource_paginate
        from services.server_service import paginate as server_paginate

        for length in range(0, 25):
            items = list(range(length))
            for page in range(-2, 6):
                for page_size in (1, 3, 10):
                    mine = paginate(items, page, page_size)
                    assert mine == resource_paginate(items, page, page_size)
                    assert mine == server_paginate(items, page, page_size)

    def test_page_slice_matches_offset_windows(self) -> None:
        items = list(range(30))
        assert page_slice(items, 0, 10) == list(range(0, 10))
        assert page_slice(items, 20, 10) == list(range(20, 30))
        # 越界偏移：与切片语义一致（尾部不足就短页、超出就是空页），不钳制
        assert page_slice(items, 25, 10) == list(range(25, 30))
        assert page_slice(items, 40, 10) == []
        assert page_slice([], 0, 10) == []


class TestBrowsableCount:
    """D-103 的"本地条目数"口径（原 ``ModBrowserWindow._browsable_hits``）。"""

    def test_prefers_ai_cache(self) -> None:
        assert browsable_count([1, 2, 3], [1, 2], 1500) == 3

    def test_falls_back_to_normal_cache(self) -> None:
        assert browsable_count(None, list(range(30)), 1500) == 30

    def test_falls_back_to_backend_total_when_never_searched(self) -> None:
        assert browsable_count(None, None, 1500) == 1500

    def test_empty_cache_is_not_none(self) -> None:
        """空缓存（``[]``）与"还没搜过"（``None``）必须区分：前者是 0 条。"""
        assert browsable_count(None, [], 1500) == 0
        assert browsable_count([], None, 1500) == 0

    def test_backend_total_is_never_used_when_cache_exists(self) -> None:
        """核心反例：后端报 1500，但本地只有 300 条时分页必须按 300 算。"""
        assert browsable_count(None, list(range(300)), 1500) == 300


class TestSearchOutcome:
    def test_split_result_reads_three_keys(self) -> None:
        outcome = split_result({"hits": [{"a": 1}], "total_hits": 7, "sources": {"modrinth": 3}})
        assert outcome.hits == [{"a": 1}]
        assert outcome.total_hits == 7
        assert outcome.sources == {"modrinth": 3}
        assert outcome.keywords == []
        assert outcome.as_publish_args() == ([{"a": 1}], 7, {"modrinth": 3})

    def test_split_result_defaults_match_original_get_calls(self) -> None:
        """字段缺失时的三个默认值必须与原文 ``result.get(k, 默认)`` 一致。"""
        outcome = split_result({})
        assert outcome.hits == []
        assert outcome.total_hits == 0
        assert outcome.sources == {}

    def test_split_result_keeps_keywords(self) -> None:
        outcome = split_result({"hits": [], "total_hits": 0, "keywords": ["a", "b"]})
        assert outcome.keywords == ["a", "b"]

    def test_keywords_none_becomes_empty_list(self) -> None:
        assert split_result({"keywords": None}).keywords == []

    def test_split_result_on_none_raises_attribute_error(self) -> None:
        """记录现状、疑为缺陷：原文全部走 ``result.get``，``None`` 会 AttributeError。

        所有调用点都在 ``try/except`` 里且真实后端只返回字典或抛异常，
        因此这里照原文让 ``AttributeError`` 冒出来，不做静默兜底。
        """
        with pytest.raises(AttributeError):
            split_result(None)

    def test_outcome_is_frozen(self) -> None:
        outcome = SearchOutcome(hits=[], total_hits=0, sources={})
        with pytest.raises(Exception):
            outcome.total_hits = 5  # type: ignore[misc]


class TestResultHelpers:
    def test_sort_by_downloads_is_descending_and_missing_key_counts_as_zero(self) -> None:
        hits = [{"downloads": 5}, {"downloads": 100}, {"title": "no-downloads"}, {"downloads": 5}]
        assert [h.get("downloads", 0) for h in sort_hits_by_downloads(hits)] == [100, 5, 5, 0]

    def test_sort_does_not_mutate_input(self) -> None:
        hits = [{"downloads": 1}, {"downloads": 2}]
        sort_hits_by_downloads(hits)
        assert hits[0]["downloads"] == 1

    def test_dedupe_by_project_id_keeps_first_and_drops_empty_pid(self) -> None:
        hits = [
            {"project_id": "a", "title": "first"},
            {"project_id": "b"},
            {"project_id": "a", "title": "second"},
            {"title": "missing"},
            {"project_id": ""},
        ]
        out = dedupe_hits_by_project_id(hits)
        assert [h.get("title") for h in out] == ["first", None]

    def test_dedupe_accepts_shared_seen_set_across_batches(self) -> None:
        """服务端 AI 搜索跨关键词去重时复用一个 ``seen`` 集合（原文如此）。"""
        seen: set = set()
        first = dedupe_hits_by_project_id([{"project_id": "a"}, {"project_id": "b"}], seen)
        second = dedupe_hits_by_project_id([{"project_id": "b"}, {"project_id": "c"}], seen)
        assert len(first) == 2 and [h["project_id"] for h in second] == ["c"]

    def test_merge_sources_adds_same_keys_and_skips_empty(self) -> None:
        assert merge_sources({"modrinth": 3}, {"modrinth": 2, "curseforge": 1}) == {
            "modrinth": 5,
            "curseforge": 1,
        }
        assert merge_sources(None, {}, {"a": 1}) == {"a": 1}
        assert merge_sources() == {}


class TestLoaderAndTagHelpers:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, None),
            ("fabric", "fabric"),
            ("legacyfabric", "fabric"),
            ("cleanroom", "forge"),
            ("quilt", "quilt"),
        ],
    )
    def test_compat_loader(self, raw, expected) -> None:
        assert compat_loader(raw) == expected

    def test_compat_map_is_the_single_source_of_truth(self) -> None:
        assert browse_common.MOD_LOADER_COMPAT_MAP == {"legacyfabric": "fabric", "cleanroom": "forge"}

    def test_loader_tags_only_whitelisted_and_in_order(self) -> None:
        assert loader_tags(["quilt", "magic", "fabric"]) == ["quilt", "fabric"]
        assert loader_tags(["magic"]) == []

    def test_loader_tags_text_capitalizes_or_empty(self) -> None:
        assert loader_tags_text(["fabric", "forge"]) == "Fabric | Forge"
        assert loader_tags_text([]) == ""
        assert loader_tags_text(["magic"]) == ""

    def test_tag_bar_display_truncates_to_four_chars(self) -> None:
        assert tag_bar_display("优化") == "优化"
        assert tag_bar_display("abcdefg") == "abcd"
        assert tag_bar_display("") == ""

    def test_normalize_search_strips_only(self) -> None:
        """与资源窗口的 ``.strip().lower()`` **不同**：这里不改大小写。"""
        assert normalize_search("  AbC  ") == "AbC"
        assert normalize_search(None) == ""
        assert normalize_search("") == ""


class TestPermissionHelpers:
    def test_counts_and_summary(self) -> None:
        perms = ["network.socket", "filesystem.write", "unknown.permission", "network.socket"]
        high, medium, low = permission_counts(perms)
        # 重复项与未知项都计入"低"（原文是扣减法）
        assert (high, medium, low) == (2, 1, 1)
        assert permission_summary(perms) == "| H:2 M:1 L:1"

    def test_summary_skips_zero_counters(self) -> None:
        assert permission_summary(["network.socket"]) == "| H:1"
        assert permission_summary(["filesystem.write"]) == "| M:1"
        assert permission_summary(["whatever"]) == "| L:1"

    def test_empty_permissions_give_empty_summary(self) -> None:
        assert permission_counts([]) == (0, 0, 0)
        assert permission_summary([]) == ""
