"""任务 1.4-C（音乐**本地侧**服务化）的离线回归测试。

规则：**不联网、不真下载、不真放声音、不开窗口、不弹窗**，也不要求机器上有
pygame / mutagen / winsdk / ffmpeg（一律用假替身）；文件只写 `tmp_path`。
任务书明确要求"不要改已有的 tests/test_music_online.py"，所以 1.4-C 新增/改动的
服务行为全部钉在这里。

覆盖范围（与任务书的五个簇逐条对应）：

1. **本地歌单 CRUD / 排序 / 播放历史 / 侧边栏与行清单 / 播放全部 / 底栏**
   —— `TestSidebarPlan` / `TestPlaylistName` / `TestOpenPlaylist` / `TestRemoveSong`
   / `TestSort` / `TestRowPlans` / `TestAddSong` / `TestAddToPlaylistMenuPlan`
   / `TestPlayAll` / `TestHistory` / `TestFooterPlan`
2. **歌词轮询与显示** —— `TestLyricDisplayPlan` / `TestLyricPollPlan` / `TestLoadLyric`
3. **音效面板接线** —— `TestEffectsText` / `TestResetEffectSettings` / `TestCleanupTempFiles`
4. **全局热键** —— `TestHotkeys`
5. **桌面歌词 / 网易云远程侧边栏（1.4-B 留的入口）** —— `TestDesktopLyricHelpers`
   / `TestWyRemoteSidebar`
6. **服务层零 UI（AST 判定，不是字符串搜索）** —— `TestServicesAreUIFree`

另外两组**结构性**断言：`TestDelegationWiring`（界面确实调到了新服务）与
`TestBlockedByStaleGuard`（记录"is_remote_key 没能接上界面"的现状与原因）。
"""

from __future__ import annotations

import ast
import io
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.desktop_lyric as dl  # noqa: E402
import services.music_effects as me  # noqa: E402
import services.music_effects_panel as mep  # noqa: E402
import services.music_hotkeys as mh  # noqa: E402
import services.music_local as ml  # noqa: E402
import services.music_lyric_display as mld  # noqa: E402
import services.music_lyrics as mly  # noqa: E402
import services.music_playlist as mp  # noqa: E402
import services.music_wy_remote as wy  # noqa: E402
from services.music_source.base import MusicInfo  # noqa: E402


def _new_manager(*names) -> mp.PlaylistManager:
    """建一个干净的 PlaylistManager（不碰磁盘：`PlaylistManager()` 本身不读文件）。"""
    mgr = mp.PlaylistManager()
    for name in names:
        mgr.create_playlist(name)
    return mgr


def _online_info(name="七里香", singer="周杰伦", source="kw", songmid="m1", interval=240):
    return MusicInfo(name=name, singer=singer, source=source, songmid=songmid, interval=interval)


def _local_song(path="/tmp/does-not-exist.mp3", title="本地歌", artist="某人"):
    return mp.PlaylistSong.from_local_file(path, {"title": title, "artist": artist})


def _online_song(interval=240, source="kw", songmid="m1", name="在线歌", singer="歌手"):
    return mp.PlaylistSong.from_online_info(
        _online_info(name=name, singer=singer, source=source, songmid=songmid, interval=interval)
    )


# ══════════════════════════════════════════════════════════════════════
# 1. 侧边栏
# ══════════════════════════════════════════════════════════════════════


class TestSidebarPlan:
    def test_plain_playlist_label(self):
        pl = mp.Playlist(name="我的歌单")
        assert ml.playlist_label(pl) == "我的歌单 (0)"

    def test_system_playlist_label_keeps_icon(self):
        """系统歌单带图标前缀。图标逐字保留原文（本轮是等价重构，不换图标）。"""
        pl = mp.Playlist(id=mp.HISTORY_PLAYLIST_ID, name="播放历史", is_system=True)
        assert ml.playlist_label(pl) == f"{ml.SYSTEM_PLAYLIST_ICON} 播放历史 (0)"
        assert ml.SYSTEM_PLAYLIST_ICON == "🕐"

    def test_label_counts_songs(self):
        pl = mp.Playlist(name="A", songs=[_local_song("/a.mp3"), _local_song("/b.mp3")])
        assert ml.playlist_label(pl) == "A (2)"

    def test_all_songs_item_is_never_active(self):
        mgr = _new_manager("A")
        mgr.set_current_playlist(mgr.playlists[0].id)
        plan = ml.sidebar_plan(mgr, "全部歌曲")
        assert plan.all_songs.playlist_id is None
        assert plan.all_songs.text == "全部歌曲"
        assert plan.all_songs.is_active is False

    def test_active_flag_follows_current_playlist(self):
        mgr = _new_manager("A", "B")
        first, second = mgr.playlists
        mgr.set_current_playlist(second.id)
        plan = ml.sidebar_plan(mgr, "全部歌曲")
        assert [i.playlist_id for i in plan.playlists] == [first.id, second.id]
        assert [i.is_active for i in plan.playlists] == [False, True]

    def test_no_current_playlist_means_nothing_active(self):
        mgr = _new_manager("A")
        plan = ml.sidebar_plan(mgr, "全部歌曲")
        assert [i.is_active for i in plan.playlists] == [False]

    def test_empty_manager_yields_only_all_songs(self):
        plan = ml.sidebar_plan(mp.PlaylistManager(), "全部歌曲")
        assert plan.playlists == []
        assert plan.all_songs.text == "全部歌曲"

    def test_system_playlist_keeps_manager_order(self):
        """系统歌单也是 manager 顺序里的一员（原文不重排）。"""
        mgr = _new_manager("A")
        mgr.get_or_create_history_playlist()
        plan = ml.sidebar_plan(mgr, "全部歌曲")
        assert [i.text for i in plan.playlists][0].startswith(ml.SYSTEM_PLAYLIST_ICON)


# ══════════════════════════════════════════════════════════════════════
# 2. 新建 / 重命名
# ══════════════════════════════════════════════════════════════════════


class TestPlaylistName:
    @pytest.mark.parametrize("raw", ["", "   ", "\t\n", None])
    def test_normalize_rejects_blank(self, raw):
        assert ml.normalize_name(raw) is None

    @pytest.mark.parametrize("raw,expect", [("  A  ", "A"), ("A B", "A B"), ("  中文  ", "中文")])
    def test_normalize_strips(self, raw, expect):
        assert ml.normalize_name(raw) == expect

    def test_create_rejects_blank_without_side_effects(self):
        mgr = _new_manager("A")
        mgr.set_current_playlist(mgr.playlists[0].id)
        before = mgr.current_playlist_id
        assert ml.create_named_playlist(mgr, "   ") is None
        assert len(mgr.playlists) == 1
        assert mgr.current_playlist_id == before

    def test_create_strips_and_selects(self):
        mgr = _new_manager()
        pl = ml.create_named_playlist(mgr, "  新歌单  ")
        assert pl is not None and pl.name == "新歌单"
        assert mgr.current_playlist_id == pl.id

    def test_rename_target_name_missing_playlist(self):
        assert ml.rename_target_name(_new_manager(), "nope") is None

    def test_rename_target_name_returns_initial_value(self):
        mgr = _new_manager("旧名字")
        assert ml.rename_target_name(mgr, mgr.playlists[0].id) == "旧名字"

    def test_apply_rename_rejects_blank(self):
        mgr = _new_manager("旧名字")
        pid = mgr.playlists[0].id
        assert ml.apply_rename(mgr, pid, "  ") is False
        assert mgr.get_playlist(pid).name == "旧名字"

    def test_apply_rename_strips(self):
        mgr = _new_manager("旧名字")
        pid = mgr.playlists[0].id
        assert ml.apply_rename(mgr, pid, "  新名字 ") is True
        assert mgr.get_playlist(pid).name == "新名字"

    def test_apply_rename_ignores_manager_result(self):
        """钉住现状：系统歌单改名失败，`apply_rename` 仍然返回 True。

        原文对 `PlaylistManager.rename_playlist` 的返回值**不作判断** ——
        改名失败也照常重建侧边栏并落盘。这里保持同一条路径，
        所以 True 的含义是"确实发起了重命名"，不是"重命名成功"。
        """
        mgr = _new_manager()
        system = mgr.get_or_create_history_playlist()
        assert ml.apply_rename(mgr, system.id, "改个名") is True
        assert mgr.get_playlist(system.id).name == "播放历史"

    def test_apply_rename_on_missing_playlist_returns_true(self):
        """同上：歌单不存在时原文照样调 `rename_playlist`（返回 False 被忽略）。"""
        assert ml.apply_rename(_new_manager(), "nope", "x") is True


# ══════════════════════════════════════════════════════════════════════
# 3. 切换 / 移除
# ══════════════════════════════════════════════════════════════════════


class TestOpenPlaylist:
    def test_missing_playlist_does_not_change_current(self):
        mgr = _new_manager("A")
        mgr.set_current_playlist(mgr.playlists[0].id)
        before = mgr.current_playlist_id
        assert ml.open_playlist(mgr, "nope") is None
        assert mgr.current_playlist_id == before

    def test_existing_playlist_becomes_current(self):
        mgr = _new_manager("A", "B")
        pid = mgr.playlists[1].id
        pl = ml.open_playlist(mgr, pid)
        assert pl is not None and pl.id == pid
        assert mgr.current_playlist_id == pid


class TestRemoveSong:
    def _manager_with_songs(self):
        mgr = _new_manager("A")
        pid = mgr.playlists[0].id
        mgr.add_song(pid, _local_song("/a.mp3"))
        mgr.add_song(pid, _local_song("/b.mp3"))
        mgr.set_current_playlist(pid)
        return mgr, pid

    def test_readonly_is_a_noop(self):
        mgr, pid = self._manager_with_songs()
        assert ml.remove_song_from_current(mgr, 0, readonly=True) is None
        assert len(mgr.get_playlist(pid).songs) == 2

    def test_no_current_playlist(self):
        mgr = _new_manager("A")
        assert ml.remove_song_from_current(mgr, 0) is None

    def test_index_out_of_range(self):
        mgr, pid = self._manager_with_songs()
        assert ml.remove_song_from_current(mgr, 99) is None
        assert ml.remove_song_from_current(mgr, -1) is None
        assert len(mgr.get_playlist(pid).songs) == 2

    def test_removes_and_returns_playlist_to_repaint(self):
        mgr, pid = self._manager_with_songs()
        pl = ml.remove_song_from_current(mgr, 0)
        assert pl is not None and pl.id == pid
        assert len(pl.songs) == 1

    def test_system_playlist_cannot_be_edited(self):
        mgr = _new_manager()
        system = mgr.get_or_create_history_playlist()
        system.songs.append(_local_song("/a.mp3"))
        mgr.set_current_playlist(system.id)
        assert ml.remove_song_from_current(mgr, 0) is None
        assert len(system.songs) == 1


# ══════════════════════════════════════════════════════════════════════
# 4. 排序
# ══════════════════════════════════════════════════════════════════════


class TestSort:
    @pytest.mark.parametrize(
        "current,clicked,expect",
        [
            (mp.SORT_ADD_TIME_DESC, mp.SORT_ADD_TIME_DESC, mp.SORT_ADD_TIME_ASC),
            (mp.SORT_ADD_TIME_ASC, mp.SORT_ADD_TIME_ASC, mp.SORT_ADD_TIME_DESC),
            (mp.SORT_NAME_ASC, mp.SORT_NAME_ASC, mp.SORT_NAME_DESC),
            (mp.SORT_NAME_DESC, mp.SORT_NAME_DESC, mp.SORT_NAME_ASC),
            (mp.SORT_ADD_TIME_DESC, mp.SORT_NAME_ASC, mp.SORT_NAME_ASC),
            (mp.SORT_NAME_ASC, mp.SORT_ADD_TIME_DESC, mp.SORT_ADD_TIME_DESC),
        ],
    )
    def test_toggle(self, current, clicked, expect):
        assert ml.toggle_sort_mode(current, clicked) == expect

    def test_toggle_unknown_mode_is_passthrough(self):
        """原文的 if/elif 链不命中任何分支 —— 未知模式原样返回。"""
        assert ml.toggle_sort_mode("weird", "weird") == "weird"

    def test_flip_table_covers_exactly_four_modes(self):
        assert set(ml.SORT_FLIP) == {
            mp.SORT_ADD_TIME_ASC,
            mp.SORT_ADD_TIME_DESC,
            mp.SORT_NAME_ASC,
            mp.SORT_NAME_DESC,
        }

    def test_local_sort_plan_without_current_playlist(self):
        assert ml.local_sort_plan(mp.PlaylistManager(), mp.SORT_NAME_ASC) is None

    def test_local_sort_plan_flips_when_same_mode(self):
        mgr = _new_manager("A")
        pl = mgr.playlists[0]
        mgr.set_current_playlist(pl.id)
        plan = ml.local_sort_plan(mgr, mp.SORT_ADD_TIME_DESC)
        assert plan is not None
        assert plan.mode == mp.SORT_ADD_TIME_ASC
        assert plan.playlist is pl
        assert pl.sort_mode == mp.SORT_ADD_TIME_ASC

    def test_local_sort_plan_switches_to_new_mode(self):
        mgr = _new_manager("A")
        pl = mgr.playlists[0]
        mgr.set_current_playlist(pl.id)
        plan = ml.local_sort_plan(mgr, mp.SORT_NAME_DESC)
        assert plan is not None and plan.mode == mp.SORT_NAME_DESC
        assert pl.sort_mode == mp.SORT_NAME_DESC

    def test_local_sort_plan_actually_reorders(self):
        mgr = _new_manager("A")
        pid = mgr.playlists[0].id
        mgr.add_song(pid, mp.PlaylistSong.from_local_file("/b.mp3", {"title": "B"}))
        mgr.add_song(pid, mp.PlaylistSong.from_local_file("/a.mp3", {"title": "A"}))
        mgr.set_current_playlist(pid)
        plan = ml.local_sort_plan(mgr, mp.SORT_NAME_ASC)
        assert plan is not None
        assert [s.display_title for s in plan.playlist.songs] == ["A", "B"]

    def test_remote_sort_plan_empty_is_none(self):
        assert ml.remote_sort_plan([], mp.SORT_NAME_ASC, mp.SORT_NAME_ASC) is None

    def test_remote_sort_plan_sorts_the_same_list_object(self):
        """**关键**：远程歌单排序必须就地改传进来的那个 list（原文共享同一个对象）。"""
        songs = [
            mp.PlaylistSong.from_local_file("/b.mp3", {"title": "B"}),
            mp.PlaylistSong.from_local_file("/a.mp3", {"title": "A"}),
        ]
        same = songs
        mode = ml.remote_sort_plan(songs, mp.SORT_ADD_TIME_DESC, mp.SORT_NAME_ASC)
        assert mode == mp.SORT_NAME_ASC
        assert songs is same
        assert [s.display_title for s in same] == ["A", "B"]

    def test_remote_sort_plan_flips(self):
        songs = [_local_song("/a.mp3")]
        assert ml.remote_sort_plan(songs, mp.SORT_NAME_ASC, mp.SORT_NAME_ASC) == mp.SORT_NAME_DESC

    def test_remote_sort_plan_does_not_touch_playlist_manager(self):
        mgr = _new_manager("A")
        before = mgr.playlists[0].sort_mode
        songs = [_local_song("/a.mp3")]
        ml.remote_sort_plan(songs, mp.SORT_ADD_TIME_DESC, mp.SORT_NAME_ASC)
        assert mgr.playlists[0].sort_mode == before

    def test_sort_songs_in_place_unknown_mode_is_noop(self):
        songs = [_local_song("/b.mp3"), _local_song("/a.mp3")]
        order = list(songs)
        ml.sort_songs_in_place(songs, "no-such-mode")
        assert songs == order


# ══════════════════════════════════════════════════════════════════════
# 5. 行的显示取值
# ══════════════════════════════════════════════════════════════════════


class TestRowPlans:
    def test_title_falls_back_to_raw_basename(self):
        """原文的兜底是 `os.path.basename`（**带扩展名**），不是去扩展名的曲名。

        去扩展名那条路走的是 `PlaylistSong.from_local_file`
        （`os.path.splitext(...)[0]`），与这条兜底不是同一件事。
        """
        plan = ml.playlist_row_plan("/music/曲子.mp3", {})
        assert plan.name_text == "曲子.mp3"
        assert plan.dur_text == ""

    def test_artist_is_appended(self):
        plan = ml.playlist_row_plan("/music/a.mp3", {"title": "歌", "artist": "某人"})
        assert plan.name_text == "歌 - 某人"

    def test_duration_is_formatted(self):
        plan = ml.playlist_row_plan("/music/a.mp3", {"duration": 90})
        assert plan.dur_text == "1:30"

    def test_duration_zero_is_blank(self):
        assert ml.playlist_row_plan("/music/a.mp3", {"duration": 0}).dur_text == ""

    def test_title_truncation_keeps_47_plus_ellipsis(self):
        long_title = "字" * 60
        plan = ml.playlist_row_plan("/music/a.mp3", {"title": long_title})
        assert plan.name_text == "字" * ml.ROW_TITLE_KEEP + "..."
        assert len(plan.name_text) == ml.ROW_TITLE_KEEP + 3

    def test_title_exactly_at_limit_is_not_truncated(self):
        title = "字" * ml.ROW_TITLE_MAX
        assert ml.playlist_row_plan("/a.mp3", {"title": title}).name_text == title

    def test_truncation_happens_before_artist_suffix(self):
        """原文先截歌名、再拼" - 歌手"：截断长度只看歌名。"""
        plan = ml.playlist_row_plan("/a.mp3", {"title": "字" * 60, "artist": "某人"})
        assert plan.name_text == "字" * ml.ROW_TITLE_KEEP + "... - 某人"

    def test_none_metadata_is_tolerated(self):
        assert ml.playlist_row_plan("/music/a.mp3", None).name_text == "a.mp3"

    def test_local_song_row_has_no_duration_or_tag(self):
        plan = ml.song_row_plan(_local_song("/a.mp3", title="本地歌", artist="某人"))
        assert plan.display_text == "本地歌 - 某人"
        assert plan.dur_text == ""
        assert plan.source_tag == ""
        assert plan.show_remove is True

    def test_readonly_hides_remove_button(self):
        assert ml.song_row_plan(_local_song(), readonly=True).show_remove is False

    def test_online_song_row_has_duration_and_upper_tag(self):
        plan = ml.song_row_plan(_online_song(interval=185, source="wy"))
        assert plan.dur_text == "3:05"
        assert plan.source_tag == "WY"

    def test_online_song_without_interval_has_no_duration(self):
        assert ml.song_row_plan(_online_song(interval=0)).dur_text == ""

    def test_online_song_without_source_still_has_empty_tag(self):
        """原文只判 source_type=="online"，没判 online_source 是否为空。"""
        assert ml.song_row_plan(_online_song(source="")).source_tag == ""


# ══════════════════════════════════════════════════════════════════════
# 6. 添加歌曲 / 添加到歌单菜单
# ══════════════════════════════════════════════════════════════════════


class TestAddSong:
    def test_add_local_song(self):
        mgr = _new_manager("A")
        pid = mgr.playlists[0].id
        outcome = ml.add_song_to_playlist(mgr, pid, "/music/a.mp3", False, {"title": "歌"})
        assert outcome.added is True
        assert len(mgr.get_playlist(pid).songs) == 1
        assert mgr.get_playlist(pid).songs[0].display_title == "歌"

    def test_duplicate_is_rejected(self):
        mgr = _new_manager("A")
        pid = mgr.playlists[0].id
        ml.add_song_to_playlist(mgr, pid, "/music/a.mp3", False, {})
        outcome = ml.add_song_to_playlist(mgr, pid, "/music/a.mp3", False, {})
        assert outcome.added is False
        assert len(mgr.get_playlist(pid).songs) == 1

    def test_unwritable_metadata_is_not_read_for_online_songs(self):
        """在线分支不该去碰本地元数据（`metadata=None` 也不会被用到）。"""
        mgr = _new_manager("A")
        pid = mgr.playlists[0].id
        outcome = ml.add_song_to_playlist(mgr, pid, _online_info(), True, None)
        assert outcome.added is True
        assert mgr.get_playlist(pid).songs[0].source_type == "online"

    def test_playlist_returned_only_when_it_is_current(self):
        mgr = _new_manager("A", "B")
        first, second = mgr.playlists
        outcome = ml.add_song_to_playlist(mgr, first.id, "/a.mp3", False, {})
        assert outcome.added is True and outcome.playlist is None

        mgr.set_current_playlist(first.id)
        outcome = ml.add_song_to_playlist(mgr, first.id, "/b.mp3", False, {})
        assert outcome.playlist is not None and outcome.playlist.id == first.id
        assert second.id != outcome.playlist.id

    def test_missing_playlist_is_not_added(self):
        mgr = _new_manager("A")
        outcome = ml.add_song_to_playlist(mgr, "nope", "/a.mp3", False, {})
        assert outcome.added is False and outcome.playlist is None


class TestAddToPlaylistMenuPlan:
    def test_items_carry_exists_flag_and_check_suffix(self):
        mgr = _new_manager("A", "B")
        first, second = mgr.playlists
        mgr.add_song(second.id, mp.PlaylistSong.from_local_file("/a.mp3", {}))
        plan = ml.add_to_playlist_menu_plan(mgr, "/a.mp3", False, {})
        by_id = {i.playlist_id: i for i in plan.items}
        assert by_id[first.id].exists is False
        assert by_id[first.id].text == "A"
        assert by_id[second.id].exists is True
        assert by_id[second.id].text == "B" + ml.ALREADY_ADDED_SUFFIX

    def test_existence_is_checked_per_playlist(self):
        """一个歌单里已存在，不该让别的歌单也显示"已加"。"""
        mgr = _new_manager("A", "B")
        mgr.add_song(mgr.playlists[0].id, mp.PlaylistSong.from_local_file("/a.mp3", {}))
        plan = ml.add_to_playlist_menu_plan(mgr, "/a.mp3", False, {})
        assert [i.exists for i in plan.items] == [True, False]

    def test_online_branch_builds_online_song(self):
        mgr = _new_manager("A")
        plan = ml.add_to_playlist_menu_plan(mgr, _online_info(), True, None)
        assert plan.song is not None and plan.song.source_type == "online"
        assert plan.items[0].exists is False

    def test_no_playlists_yields_no_items(self):
        """空歌单的分支留在界面侧（messagebox + i18n），服务只负责条目生成。"""
        plan = ml.add_to_playlist_menu_plan(mp.PlaylistManager(), "/a.mp3", False, {})
        assert plan.items == []


# ══════════════════════════════════════════════════════════════════════
# 7. 播放入口
# ══════════════════════════════════════════════════════════════════════


class TestPlayIndexTarget:
    @pytest.mark.parametrize("idx", [-1, 2, 99])
    def test_out_of_range(self, idx):
        assert ml.play_index_target(["/a.mp3", "/b.mp3"], idx) is None

    @pytest.mark.parametrize("idx,expect", [(0, "/a.mp3"), (1, "/b.mp3")])
    def test_in_range(self, idx, expect):
        assert ml.play_index_target(["/a.mp3", "/b.mp3"], idx) == expect

    def test_empty_playlist(self):
        assert ml.play_index_target([], 0) is None


class TestPlayAll:
    def test_empty_playlist_is_none(self):
        assert ml.play_all_target([]) is None
        assert ml.play_all_target([], remote=True) is None

    def test_local_playlist_skips_missing_files(self):
        songs = [_local_song("/missing.mp3"), _local_song("/here.mp3")]
        target = ml.play_all_target(songs, path_exists=lambda p: p == "/here.mp3")
        assert target is not None and target.index == 1

    def test_local_playlist_takes_first_online_song(self):
        songs = [_online_song(songmid="m1"), _online_song(songmid="m2")]
        target = ml.play_all_target(songs, path_exists=lambda p: False)
        assert target is not None and target.index == 0

    def test_local_playlist_with_nothing_playable(self):
        target = ml.play_all_target([_local_song("/missing.mp3")], path_exists=lambda p: False)
        assert target is not None and target.index == -1

    def test_remote_playlist_only_accepts_online_songs(self):
        songs = [_local_song("/x.mp3"), _online_song(songmid="m9")]
        target = ml.play_all_target(songs, remote=True, path_exists=lambda p: True)
        assert target is not None and target.index == 1

    def test_remote_playlist_without_online_songs(self):
        target = ml.play_all_target([_local_song("/x.mp3")], remote=True, path_exists=lambda p: True)
        assert target is not None and target.index == -1

    def test_context_songs_is_a_copy(self):
        songs = [_local_song("/a.mp3")]
        target = ml.play_all_target(songs, path_exists=lambda p: True)
        assert target is not None
        assert target.context_songs == songs
        assert target.context_songs is not songs
        target.context_songs.append(_local_song("/b.mp3"))
        assert len(songs) == 1


# ══════════════════════════════════════════════════════════════════════
# 8. 播放历史
# ══════════════════════════════════════════════════════════════════════


class TestHistory:
    def test_records_local_song_into_history(self):
        mgr = _new_manager()
        assert ml.record_history_local(mgr, "/music/a.mp3", {"title": "歌"}) is True
        history = mgr.get_playlist(mp.HISTORY_PLAYLIST_ID)
        assert history is not None and len(history.songs) == 1
        assert history.songs[0].display_title == "歌"

    def test_repeated_local_song_moves_to_front_without_duplicating(self):
        mgr = _new_manager()
        ml.record_history_local(mgr, "/a.mp3", {"title": "A"})
        ml.record_history_local(mgr, "/b.mp3", {"title": "B"})
        ml.record_history_local(mgr, "/a.mp3", {"title": "A"})
        titles = [s.display_title for s in mgr.get_playlist(mp.HISTORY_PLAYLIST_ID).songs]
        assert titles == ["A", "B"]

    def test_online_info_that_is_not_musicinfo_is_rejected(self):
        mgr = _new_manager()
        assert ml.record_history_online(mgr, object()) is False
        assert mgr.get_playlist(mp.HISTORY_PLAYLIST_ID) is None or (
            mgr.get_playlist(mp.HISTORY_PLAYLIST_ID).songs == []
        )

    def test_records_online_song(self):
        mgr = _new_manager()
        assert ml.record_history_online(mgr, _online_info()) is True
        songs = mgr.get_playlist(mp.HISTORY_PLAYLIST_ID).songs
        assert len(songs) == 1 and songs[0].source_type == "online"

    @pytest.mark.parametrize("tab_mode", ["local", "online", ""])
    def test_refresh_target_none_outside_playlist_tab(self, tab_mode):
        mgr = _new_manager()
        mgr.get_or_create_history_playlist()
        mgr.set_current_playlist(mp.HISTORY_PLAYLIST_ID)
        assert ml.history_refresh_target(mgr, tab_mode) is None

    def test_refresh_target_none_when_viewing_another_playlist(self):
        mgr = _new_manager("A")
        mgr.get_or_create_history_playlist()
        mgr.set_current_playlist(mgr.playlists[-1].id)
        assert ml.history_refresh_target(mgr, "playlist") is None

    def test_refresh_target_is_history_playlist(self):
        mgr = _new_manager()
        mgr.get_or_create_history_playlist()
        mgr.set_current_playlist(mp.HISTORY_PLAYLIST_ID)
        target = ml.history_refresh_target(mgr, "playlist")
        assert target is not None and target.id == mp.HISTORY_PLAYLIST_ID

    def test_refresh_target_without_current_playlist(self):
        mgr = _new_manager()
        mgr.get_or_create_history_playlist()
        assert ml.history_refresh_target(mgr, "playlist") is None


# ══════════════════════════════════════════════════════════════════════
# 9. 底部迷你播放条
# ══════════════════════════════════════════════════════════════════════


class TestFooterPlan:
    def test_invisible_when_stopped(self):
        plan = ml.footer_plan(
            path="/a.mp3",
            is_online_playing=False,
            online_info=None,
            is_playing=False,
            is_paused=False,
            read_metadata=lambda p: {"title": "不该被读到"},
        )
        assert plan.visible is False
        assert plan.text == ""
        assert plan.play_text == ""

    def test_invisible_without_path_and_without_online(self):
        plan = ml.footer_plan(
            path=None,
            is_online_playing=False,
            online_info=None,
            is_playing=True,
            is_paused=False,
            read_metadata=lambda p: {},
        )
        assert plan.visible is False

    def test_online_playing_uses_search_info(self):
        plan = ml.footer_plan(
            path=None,
            is_online_playing=True,
            online_info=_online_info(name="七里香", singer="周杰伦"),
            is_playing=True,
            is_paused=False,
            read_metadata=lambda p: pytest.fail("在线分支不该读本地元数据"),
        )
        assert plan.visible is True
        assert plan.text == "七里香 - 周杰伦"
        assert plan.play_text == ml.PLAY_GLYPH_PAUSED

    def test_online_without_singer_has_no_dash(self):
        plan = ml.footer_plan(
            path=None,
            is_online_playing=True,
            online_info=_online_info(name="纯音乐", singer=""),
            is_playing=True,
            is_paused=False,
            read_metadata=lambda p: pytest.fail("在线分支不该读本地元数据"),
        )
        assert plan.text == "纯音乐"

    def test_local_playing_reads_metadata_and_paused_glyph(self):
        seen = []

        def read(path):
            seen.append(path)
            return {"title": "本地歌", "artist": "某人"}

        plan = ml.footer_plan(
            path="/a.mp3",
            is_online_playing=False,
            online_info=None,
            is_playing=False,
            is_paused=True,
            read_metadata=read,
        )
        assert plan.visible is True
        assert plan.text == "本地歌 - 某人"
        assert plan.play_text == ml.PLAY_GLYPH_PLAYING
        assert seen == ["/a.mp3"]

    def test_local_without_artist_has_no_dash(self):
        plan = ml.footer_plan(
            path="/a.mp3",
            is_online_playing=False,
            online_info=None,
            is_playing=True,
            is_paused=False,
            read_metadata=lambda p: {"title": "本地歌"},
        )
        assert plan.text == "本地歌"

    def test_truncation_keeps_38_plus_ellipsis(self):
        plan = ml.footer_plan(
            path="/a.mp3",
            is_online_playing=False,
            online_info=None,
            is_playing=True,
            is_paused=False,
            read_metadata=lambda p: {"title": "字" * 60},
        )
        assert plan.text == "字" * ml.FOOTER_TEXT_KEEP + "..."
        assert len(plan.text) == ml.FOOTER_TEXT_KEEP + 3

    def test_truncation_applies_after_artist_join(self):
        plan = ml.footer_plan(
            path="/a.mp3",
            is_online_playing=False,
            online_info=None,
            is_playing=True,
            is_paused=False,
            read_metadata=lambda p: {"title": "字" * 39, "artist": "歌手"},
        )
        assert plan.text == ("字" * 39 + " - 歌手")[: ml.FOOTER_TEXT_KEEP] + "..."

    def test_online_playing_without_info_raises_like_the_original(self):
        """钉住现状（记录现状、疑为缺陷，**未改**）。

        可见性条件是 `(path or is_online_playing) and (is_playing or is_paused)`；
        "在线播放中但 `_music_current_online_info` 为 None" 会落到本地分支去查元数据，
        而 `path` 此时可能是 None。`meta.get("title", os.path.basename(path))` 的默认值
        是**先求值再传**的，所以这里一定抛 `TypeError`（原文同样抛）。

        本轮是等价重构：服务照抄这条路径、不替原文兜底。真要去修，属于行为变更，
        得单独一轮（报告里列在"行为变更：无"之外的缺陷清单）。
        """
        seen = []

        def read(path):
            seen.append(path)
            return {"title": "x"}

        with pytest.raises(TypeError):
            ml.footer_plan(
                path=None,
                is_online_playing=True,
                online_info=None,
                is_playing=True,
                is_paused=False,
                read_metadata=read,
            )
        assert seen == [None]

    def test_metadata_default_title_uses_raw_basename(self):
        """`meta.get("title", basename)` 的默认值：元数据里没有 title 时用**带扩展名**的文件名。"""
        plan = ml.footer_plan(
            path="/music/曲子.mp3",
            is_online_playing=False,
            online_info=None,
            is_playing=True,
            is_paused=False,
            read_metadata=lambda p: {},
        )
        assert plan.text == "曲子.mp3"

    def test_glyph_constants(self):
        assert (ml.PLAY_GLYPH_PLAYING, ml.PLAY_GLYPH_PAUSED) == ("▶", "⏸")


# ══════════════════════════════════════════════════════════════════════
# 10. 歌词：显示取值 / 轮询 / 取词
# ══════════════════════════════════════════════════════════════════════


def _parser(lrc_lines):
    parser = mly.LyricParser()
    parser.parse("\n".join(lrc_lines))
    return parser


class TestLyricDisplayPlan:
    def test_no_current_line_gives_two_blank_strings(self):
        plan = mld.display_plan(
            mly.LyricParser(), 1000, show_translation=True, show_roma=True
        )
        assert plan == mld.LyricDisplayPlan(text="", trans_text="")

    def test_prefers_translation(self):
        parser = _parser(["[00:01.00]hello"])
        parser.lines[0].translation = "你好"
        parser.lines[0].roma = "ni hao"
        plan = mld.display_plan(parser, 1500, show_translation=True, show_roma=True)
        assert plan == mld.LyricDisplayPlan(text="hello", trans_text="你好")

    def test_falls_back_to_roma_when_translation_disabled(self):
        parser = _parser(["[00:01.00]hello"])
        parser.lines[0].translation = "你好"
        parser.lines[0].roma = "ni hao"
        plan = mld.display_plan(parser, 1500, show_translation=False, show_roma=True)
        assert plan.trans_text == "ni hao"

    def test_falls_back_to_roma_when_translation_missing(self):
        parser = _parser(["[00:01.00]hello"])
        parser.lines[0].roma = "ni hao"
        plan = mld.display_plan(parser, 1500, show_translation=True, show_roma=True)
        assert plan.trans_text == "ni hao"

    def test_both_disabled_gives_empty_subtext(self):
        parser = _parser(["[00:01.00]hello"])
        parser.lines[0].translation = "你好"
        parser.lines[0].roma = "ni hao"
        plan = mld.display_plan(parser, 1500, show_translation=False, show_roma=False)
        assert plan == mld.LyricDisplayPlan(text="hello", trans_text="")

    def test_before_first_line_is_blank(self):
        parser = _parser(["[00:10.00]later"])
        assert mld.display_plan(parser, 0, show_translation=True, show_roma=False).text == ""


class TestLyricPollPlan:
    @pytest.mark.parametrize(
        "playing,paused,expect",
        [(False, False, True), (False, True, True), (True, True, True), (True, False, False)],
    )
    def test_should_stop_polling(self, playing, paused, expect):
        assert mld.should_stop_polling(is_playing=playing, is_paused=paused) is expect

    def test_idle_when_tab_and_desktop_both_hidden(self):
        plan = mld.poll_plan(tab_active=False, desktop_visible=False, progress=1.0)
        assert plan.delay_ms == mld.POLL_IDLE_MS == 300
        assert plan.elapsed_ms is None

    @pytest.mark.parametrize("tab,desktop", [(True, False), (False, True), (True, True)])
    def test_active_when_either_visible(self, tab, desktop):
        plan = mld.poll_plan(tab_active=tab, desktop_visible=desktop, progress=1.25)
        assert plan.delay_ms == mld.POLL_ACTIVE_MS == 100
        assert plan.elapsed_ms == 1250

    def test_progress_zero_still_yields_elapsed_zero(self):
        """进度 0 必须给出 `elapsed_ms=0`（而不是 None）——否则首行歌词永远不显示。"""
        plan = mld.poll_plan(tab_active=True, desktop_visible=False, progress=0.0)
        assert plan.elapsed_ms == 0

    def test_elapsed_truncates_like_int(self):
        assert mld.poll_plan(
            tab_active=True, desktop_visible=False, progress=9.999
        ).elapsed_ms == 9999


class _FakeLyricSource:
    def __init__(self, text="[00:01.00]hi", error=None):
        self.text = text
        self.error = error
        self.calls = 0

    def get_lyric(self, info):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.text


class TestLoadLyric:
    def test_missing_source_returns_false(self):
        parser = mly.LyricParser()
        assert mld.load_lyric(parser, _online_info(source="nope"), {}) is False
        assert parser.is_parsed is False

    def test_empty_lyric_returns_false_and_leaves_parser_alone(self):
        parser = _parser(["[00:01.00]old"])
        source = _FakeLyricSource(text="")
        assert mld.load_lyric(parser, _online_info(), {"kw": source}) is False
        assert [l.text for l in parser.lines] == ["old"]

    def test_exception_is_swallowed(self):
        parser = mly.LyricParser()
        source = _FakeLyricSource(error=RuntimeError("炸"))
        assert mld.load_lyric(parser, _online_info(), {"kw": source}) is False

    def test_successful_load_parses(self):
        parser = mly.LyricParser()
        source = _FakeLyricSource(text="[00:01.00]hello")
        assert mld.load_lyric(parser, _online_info(), {"kw": source}) is True
        assert parser.is_parsed is True
        assert [l.text for l in parser.lines] == ["hello"]

    def test_load_replaces_previous_lyrics(self):
        parser = _parser(["[00:01.00]old"])
        source = _FakeLyricSource(text="[00:02.00]new")
        assert mld.load_lyric(parser, _online_info(), {"kw": source}) is True
        assert [l.text for l in parser.lines] == ["new"]

    def test_sources_default_falls_back_to_service_table(self, monkeypatch):
        import services.music_source as ms

        source = _FakeLyricSource(text="[00:03.00]from-table")
        monkeypatch.setattr(ms, "MUSIC_SOURCES", {"kw": source})
        parser = mly.LyricParser()
        assert mld.load_lyric(parser, _online_info()) is True
        assert [l.text for l in parser.lines] == ["from-table"]


# ══════════════════════════════════════════════════════════════════════
# 11. 桌面歌词 / 网易云侧边栏（1.4-B 留的入口）
# ══════════════════════════════════════════════════════════════════════


class _FakeLyricWindow:
    def __init__(self, visible):
        self._visible = visible

    @property
    def is_visible(self):
        return self._visible


class TestDesktopLyricHelpers:
    def test_no_window_means_should_become_visible(self):
        assert dl.target_visible(None) is True

    def test_invisible_window_should_become_visible(self):
        assert dl.target_visible(_FakeLyricWindow(False)) is True

    def test_visible_window_should_hide(self):
        assert dl.target_visible(_FakeLyricWindow(True)) is False

    def test_ready_lines_before_parse(self):
        assert dl.ready_lines(mly.LyricParser()) is None

    def test_ready_lines_returns_the_parser_list_itself(self):
        """必须是 `parser.lines` 本身：窗口存的是同一个 list（原文如此）。"""
        parser = _parser(["[00:01.00]a"])
        lines = dl.ready_lines(parser)
        assert lines is parser.lines


class TestWyRemoteSidebar:
    def test_sidebar_items_prefix_and_label(self):
        remote = [{"id": "1", "name": "我喜欢的音乐", "track_count": 12}]
        items = wy.sidebar_items(remote, view_id=None)
        assert len(items) == 1
        assert items[0].playlist_id == wy.remote_key("1") == "wy:1"
        assert items[0].text == wy.entry_label(remote[0]) == "我喜欢的音乐 (12)"
        assert items[0].is_active is False
        assert wy.is_remote_key(items[0].playlist_id) is True
        assert wy.strip_remote_key(items[0].playlist_id) == "1"

    def test_sidebar_items_marks_current_view(self):
        remote = [
            {"id": "1", "name": "A", "track_count": 1},
            {"id": "2", "name": "B", "track_count": 2},
        ]
        items = wy.sidebar_items(remote, view_id="2")
        assert [i.is_active for i in items] == [False, True]

    def test_sidebar_items_order_follows_input(self):
        remote = [{"id": str(i), "name": f"P{i}", "track_count": i} for i in range(5)]
        items = wy.sidebar_items(remote, view_id=None)
        assert [i.text for i in items] == [f"P{i} ({i})" for i in range(5)]

    def test_empty_remote_list(self):
        assert wy.sidebar_items([], view_id="1") == []

    def test_header_title_suffix_only_when_failed(self):
        assert wy.header_title("网易云歌单", "同步失败", False) == "网易云歌单"
        assert wy.header_title("网易云歌单", "同步失败", True) == "网易云歌单（同步失败）"


# ══════════════════════════════════════════════════════════════════════
# 12. 音效：显示文案 / 重置 / 临时文件
# ══════════════════════════════════════════════════════════════════════


class TestEffectsText:
    @pytest.mark.parametrize("value,expect", [(0.0, "+0"), (3.4, "+3"), (-2.6, "-3"), (15.0, "+15")])
    def test_eq_gain_text(self, value, expect):
        assert mep.eq_gain_text(value) == expect

    @pytest.mark.parametrize("value,expect", [(60.0, "60ms"), (62.4, "62ms"), (199.9, "200ms")])
    def test_reverb_delay_text(self, value, expect):
        assert mep.reverb_delay_text(value) == expect

    @pytest.mark.parametrize("value,expect", [(0.4, "0.4"), (0.45, "0.5"), (0.1, "0.1")])
    def test_reverb_decay_text(self, value, expect):
        assert mep.reverb_decay_text(value) == expect

    @pytest.mark.parametrize("value,expect", [(0.3, "0.3"), (1.0, "1.0")])
    def test_reverb_wet_text(self, value, expect):
        assert mep.reverb_wet_text(value) == expect

    @pytest.mark.parametrize(
        "value,expect", [(0.0, "+0.0 semitones"), (-2.25, "-2.2 semitones"), (12.0, "+12.0 semitones")]
    )
    def test_pitch_text(self, value, expect):
        assert mep.pitch_text(value) == expect

    @pytest.mark.parametrize("value,expect", [(1.0, "1.00x"), (1.5, "1.50x"), (0.5, "0.50x")])
    def test_speed_text(self, value, expect):
        assert mep.speed_text(value) == expect


class TestResetEffectSettings:
    def test_resets_the_ten_fields(self):
        s = me.EffectSettings(
            eq_enabled=True,
            eq_gains=[5.0] * 10,
            reverb_enabled=True,
            reverb_delay_ms=180.0,
            reverb_decay=0.8,
            reverb_wet_level=0.9,
            pitch_enabled=True,
            pitch_semitones=7.0,
            speed_enabled=True,
            speed_rate=1.75,
        )
        mep.reset_effect_settings(s)
        assert s.eq_enabled is False
        assert s.eq_gains == [0.0] * 10
        assert s.reverb_enabled is False
        assert s.reverb_delay_ms == 60.0
        assert s.reverb_decay == 0.4
        assert s.reverb_wet_level == 0.3
        assert s.pitch_enabled is False
        assert s.pitch_semitones == 0.0
        assert s.speed_enabled is False
        assert s.speed_rate == 1.0

    def test_pan_is_deliberately_untouched(self):
        """原文的 `_music_reset_fx` 没碰声像，本函数也不碰。"""
        s = me.EffectSettings(pan_enabled=True, pan_value=-0.75)
        mep.reset_effect_settings(s)
        assert s.pan_enabled is True
        assert s.pan_value == -0.75

    def test_eq_gains_is_rebound_to_a_new_list(self):
        """原文 `s.eq_gains = [0.0] * 10` 是**重新绑定**，旧引用看到的是旧值。"""
        s = me.EffectSettings()
        old = s.eq_gains
        mep.reset_effect_settings(s)
        assert s.eq_gains is not old
        assert old == [0.0] * 10

    def test_same_object_is_mutated(self):
        s = me.EffectSettings(speed_rate=1.9)
        mep.reset_effect_settings(s)
        assert s.speed_rate == 1.0


class TestCleanupTempFiles:
    def test_removes_existing_files(self, tmp_path):
        keep = tmp_path / "keep.txt"
        keep.write_text("x", encoding="utf-8")
        doomed = tmp_path / "doomed.txt"
        doomed.write_text("x", encoding="utf-8")
        paths = [str(doomed), str(tmp_path / "missing.txt")]
        mep.cleanup_temp_files(paths)
        assert not doomed.exists()
        assert keep.exists()

    def test_clears_the_same_list_object(self):
        paths = ["/definitely/missing"]
        same = paths
        mep.cleanup_temp_files(paths)
        assert paths is same
        assert paths == []

    def test_swallows_removal_errors(self, tmp_path):
        """删不掉的路径（例如目录）不该抛出去，且仍然清空列表。"""
        directory = tmp_path / "adir"
        directory.mkdir()
        paths = [str(directory)]
        mep.cleanup_temp_files(paths)
        assert paths == []
        assert directory.exists()

    def test_empty_list_is_a_noop(self):
        paths = []
        mep.cleanup_temp_files(paths)
        assert paths == []


# ══════════════════════════════════════════════════════════════════════
# 13. 全局热键
# ══════════════════════════════════════════════════════════════════════


class _FakeKeyboard:
    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def add_hotkey(self, combo, handler):
        self.calls.append(("add", combo))
        if self.fail_on == ("add", combo):
            raise RuntimeError("注册失败")

    def remove_hotkey(self, combo):
        self.calls.append(("remove", combo))
        if self.fail_on == ("remove", combo):
            raise RuntimeError("注销失败")


class TestHotkeys:
    def test_table_is_the_original_one(self):
        assert mh.DEFAULT_HOTKEYS == {
            "play_pause": "ctrl+shift+space",
            "prev": "ctrl+shift+left",
            "next": "ctrl+shift+right",
            "stop": "ctrl+shift+down",
            "vol_up": "ctrl+shift+up",
            "vol_down": "ctrl+shift+page down",
            "vol_mute": "ctrl+shift+m",
        }

    def test_action_order_matches_dict_order(self):
        assert mh.HOTKEY_ACTIONS == tuple(mh.DEFAULT_HOTKEYS)

    def test_volume_step(self):
        assert mh.VOLUME_HOTKEY_STEP == 5

    def test_hotkey_combos(self):
        assert mh.hotkey_combos() == [
            "ctrl+shift+space",
            "ctrl+shift+left",
            "ctrl+shift+right",
            "ctrl+shift+down",
            "ctrl+shift+up",
            "ctrl+shift+page down",
            "ctrl+shift+m",
        ]

    def test_register_all_uses_declared_order(self):
        backend = _FakeKeyboard()
        handlers = {action: (lambda a=action: a) for action in mh.HOTKEY_ACTIONS}
        mh.register_all(backend, handlers)
        assert backend.calls == [
            ("add", mh.DEFAULT_HOTKEYS[action]) for action in mh.HOTKEY_ACTIONS
        ]

    def test_register_all_passes_the_matching_handler(self):
        """每个组合键拿到的必须是它自己那个动作的回调（不是最后一个）。"""
        seen = []
        backend = _FakeKeyboard()

        def make(action):
            def handler():
                seen.append(action)

            return handler

        handlers = {a: make(a) for a in mh.HOTKEY_ACTIONS}
        mh.register_all(backend, handlers)
        for action in mh.HOTKEY_ACTIONS:
            handlers[action]()
        assert seen == list(mh.HOTKEY_ACTIONS)

    def test_unregister_all_uses_declared_order(self):
        backend = _FakeKeyboard()
        mh.unregister_all(backend)
        assert backend.calls == [
            ("remove", mh.DEFAULT_HOTKEYS[action]) for action in mh.HOTKEY_ACTIONS
        ]

    def test_register_all_does_not_swallow_backend_errors(self):
        """降级策略在界面侧：服务不吞异常（原文整段包在界面自己的 try 里）。"""
        backend = _FakeKeyboard(fail_on=("add", mh.DEFAULT_HOTKEYS["next"]))
        handlers = {a: (lambda: None) for a in mh.HOTKEY_ACTIONS}
        with pytest.raises(RuntimeError):
            mh.register_all(backend, handlers)
        # 前两个注册成功、第三个抛了，后面的不再执行
        assert backend.calls == [
            ("add", mh.DEFAULT_HOTKEYS["play_pause"]),
            ("add", mh.DEFAULT_HOTKEYS["prev"]),
            ("add", mh.DEFAULT_HOTKEYS["next"]),
        ]

    def test_unregister_all_stops_at_first_error(self):
        backend = _FakeKeyboard(fail_on=("remove", mh.DEFAULT_HOTKEYS["stop"]))
        with pytest.raises(RuntimeError):
            mh.unregister_all(backend)
        assert backend.calls == [
            ("remove", mh.DEFAULT_HOTKEYS[action]) for action in ("play_pause", "prev", "next", "stop")
        ]

    def test_app_music_reexports_the_same_table(self):
        import ui.app_music as app_music

        assert app_music.DEFAULT_HOTKEYS is mh.DEFAULT_HOTKEYS


# ══════════════════════════════════════════════════════════════════════
# 14. 服务层零 UI（AST 判定，不是字符串搜索）
# ══════════════════════════════════════════════════════════════════════

SERVICE_FILES = [
    "services/music_local.py",
    "services/music_hotkeys.py",
    "services/music_lyric_display.py",
    "services/music_effects_panel.py",
    "services/desktop_lyric.py",
    "services/music_wy_remote.py",
    # 下面两个是 1.4-A 的整体搬家模块（非空行数被 test_services_relocation.py 钉死在
    # git 原文），1.4-C 对它们**只读不写**；一起扫零 UI 是顺带的免费回归。
    "services/music_lyrics.py",
    "services/music_effects.py",
]

#: 必须声明非空 `__all__` 的模块（1.4-C 新增 / 追加过公开面的那几个）。
#: `music_lyrics.py` / `music_effects.py` 是整体搬家模块、1.4-A 就没有 `__all__`，
#: 本轮也不许往里面加东西，所以不在这张表里。
DECLARED_ALL_FILES = [
    "services/music_local.py",
    "services/music_hotkeys.py",
    "services/music_lyric_display.py",
    "services/music_effects_panel.py",
    "services/desktop_lyric.py",
    "services/music_wy_remote.py",
]

FORBIDDEN_MODULES = ("tkinter", "customtkinter", "tkinterdnd2", "tkinterweb", "PySide6", "shiboken6", "PyQt5", "PyQt6")
FORBIDDEN_CALL_ATTRS = ("pack", "configure", "winfo_exists", "winfo_ismapped", "after", "after_cancel", "grab_set", "protocol")

#: 允许出现 `import threading` 的服务模块 → 理由。**每条都必须真的命中**
#: （命中 0 次即该模块其实没有 threading，登记过期 → 判失败），照抄 1.4-B 的
#: "白名单不许变成垃圾场"规则。
THREADING_ALLOWED = {
    "services/music_effects.py": (
        "1.4-A 时期就在的 `import threading`，而且**从未被使用**：实测该文件 AST 里"
        "没有任何 `threading.X` 引用，是个遗留的死导入。1.4-C 对它是只读的"
        "（本轮追加放在新模块 music_effects_panel.py 里），没有新增线程；"
        "删掉这个死导入是与行为无关的清理，留给另开一轮。"
    ),
}


def _module_src(rel: str) -> str:
    return io.open(REPO_ROOT / rel, encoding="utf-8-sig", newline="").read()


def _imported_tops(rel: str) -> set:
    mods = set()
    for node in ast.walk(ast.parse(_module_src(rel))):
        if isinstance(node, ast.Import):
            mods |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mods.add((node.module or "").split(".")[0])
    return mods


class TestServicesAreUIFree:
    @pytest.mark.parametrize("rel", SERVICE_FILES)
    def test_no_gui_imports(self, rel):
        mods = _imported_tops(rel)
        assert mods & set(FORBIDDEN_MODULES) == set(), (rel, mods)
        assert "ui" not in mods, (rel, mods)

    @pytest.mark.parametrize("rel", SERVICE_FILES)
    def test_no_widget_or_timer_calls(self, rel):
        bad = []
        for node in ast.walk(ast.parse(_module_src(rel))):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in FORBIDDEN_CALL_ATTRS:
                    bad.append(f"{ast.unparse(node.func)}()")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id.startswith(("CTk", "Tk", "Toplevel")):
                    bad.append(f"{node.func.id}()")
        assert bad == [], (rel, bad)

    @pytest.mark.parametrize("rel", SERVICE_FILES)
    def test_no_threading(self, rel):
        has_threading = "threading" in _imported_tops(rel)
        if has_threading:
            assert rel in THREADING_ALLOWED, f"{rel} 新引入了 threading（服务零线程）"
            assert THREADING_ALLOWED[rel].strip(), rel
        else:
            # 反向守卫：登记了理由的模块必须真的还有 threading，否则登记已过期
            assert rel not in THREADING_ALLOWED, f"{rel} 的 threading 登记已过期（命中 0 次）"

    @pytest.mark.parametrize("rel", SERVICE_FILES)
    def test_no_i18n_calls(self, rel):
        """i18n 属于界面层：服务里不许出现 `_(...)`（AST 查 Call 的 func 名，不查字符串）。"""
        bad = [
            node.lineno
            for node in ast.walk(ast.parse(_module_src(rel)))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_"
        ]
        assert bad == [], (rel, bad)

    @pytest.mark.parametrize("rel", DECLARED_ALL_FILES)
    def test_public_names_are_declared(self, rel):
        """每个 1.4-C 碰过的服务模块都要有非空 `__all__`，且不与实际定义脱节。"""
        import importlib

        mod = importlib.import_module(rel.replace("/", ".")[:-3])
        declared = list(getattr(mod, "__all__", []))
        assert declared, rel
        missing = [n for n in declared if not hasattr(mod, n)]
        assert missing == [], (rel, missing)


# ══════════════════════════════════════════════════════════════════════
# 15. 结构性：界面确实调到了新服务
# ══════════════════════════════════════════════════════════════════════


class TestDelegationWiring:
    """1.4-C 的 37 个已委托方法必须真的引用到对应服务（不是只改了注释）。"""

    #: 方法名 → 该方法源码里必须出现的服务名（AST 的 Name 引用）
    EXPECTED = {
        "_rebuild_playlist_sidebar": "music_local",
        "_music_rebuild_wy_remote_sidebar": "wy_remote",
        "_music_create_playlist_dialog": "music_local",
        "_music_rename_playlist_dialog": "music_local",
        "_music_show_playlist": "music_local",
        "_music_remove_song_from_playlist": "music_local",
        "_music_do_sort": "music_local",
        "_music_add_song_to_playlist": "music_local",
        "_music_add_to_playlist_menu": "music_local",
        "_music_record_play_history_local": "music_local",
        "_music_record_play_history_online": "music_local",
        "_music_refresh_history_ui": "music_local",
        "_play_from_index": "music_local",
        "_add_playlist_row": "music_local",
        "_add_playlist_song_row": "music_local",
        "_music_play_playlist_all": "music_local",
        "_update_music_footer": "music_local",
        "_poll_lyric_progress": "music_lyric_display",
        "_update_lyric_display": "music_lyric_display",
        "_fetch_and_start_lyric": "music_lyric_display",
        "_music_toggle_desktop_lyric": "desktop_lyric",
        "_music_show_desktop_lyric": "desktop_lyric",
        "_build_fx_eq_section": "music_effects_panel",
        "_build_fx_reverb_section": "music_effects_panel",
        "_build_fx_pitch_section": "music_effects_panel",
        "_build_fx_speed_section": "music_effects_panel",
        "_music_on_reverb_delay": "music_effects_panel",
        "_music_on_reverb_decay": "music_effects_panel",
        "_music_on_reverb_wet": "music_effects_panel",
        "_music_on_pitch_change": "music_effects_panel",
        "_music_on_speed_change": "music_effects_panel",
        "_music_reset_fx": "music_effects_panel",
        "_music_cleanup_fx_files": "music_effects_panel",
        "_register_hotkeys": "music_hotkeys",
        "_unregister_hotkeys": "music_hotkeys",
        "_music_hotkey_vol_up": "music_hotkeys",
        "_music_hotkey_vol_down": "music_hotkeys",
    }

    def _methods(self):
        tree = ast.parse(_module_src("ui/app_music.py"))
        cls = next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MusicPlayerMixin"
        )
        return {
            n.name: n
            for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def test_expected_set_size(self):
        assert len(self.EXPECTED) == 37

    @pytest.mark.parametrize("name,service", sorted(EXPECTED.items()))
    def test_method_references_its_service(self, name, service):
        node = self._methods()[name]
        refs = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
        assert service in refs, f"{name} 没有引用 {service}"

    def test_module_imports_all_new_services(self):
        imported = set()
        for node in ast.walk(ast.parse(_module_src("ui/app_music.py"))):
            if isinstance(node, ast.Import):
                imported |= {a.asname or a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                imported |= {a.asname or a.name for a in node.names}
        assert {
            "music_local",
            "music_hotkeys",
            "music_lyric_display",
            "music_effects_panel",
            "desktop_lyric",
        } <= imported

    def test_method_surface_is_unchanged(self):
        methods = self._methods()
        assert len(methods) == 183

    def test_shims_are_the_same_module_objects(self):
        import importlib

        pairs = [
            ("ui.music_playlist", "services.music_playlist"),
            ("ui.music_lyrics", "services.music_lyrics"),
            ("ui.music_effects", "services.music_effects"),
        ]
        for old, new in pairs:
            assert importlib.import_module(old) is importlib.import_module(new), old


# ══════════════════════════════════════════════════════════════════════
# 16. 记录现状：一条被 1.4-B 守卫挡住的接线
# ══════════════════════════════════════════════════════════════════════


class TestBlockedByStaleGuard:
    """`is_remote_key` / `strip_remote_key` **没能**接上界面，原因见下。

    1.4-B 留的这两个入口本该在 1.4-C 接上 `_build_sidebar_item`，但
    `tests/test_music_online.py::TestWyRemoteReadOnly::test_prefix_convention_is_unchanged`
    （第 2019 行）用**源码字符串断言**钉死了旧写法：

        assert "startswith(_WY_REMOTE_PREFIX)" in app

    任务书同时要求"不引入新的失败"与"不要改已有的 tests/test_music_online.py"，
    所以本轮**保持原样**，并在报告里列为未完成项。下面两条断言把这个现状钉住：
    谁把那条字符串断言松掉，这两条就会红，提醒他去接线。

    **判据走 AST，不走字符串搜索** —— 本模块头部注释里就写着
    `is_remote_key/strip_remote_key`，字符串搜索会把注释当调用方（实测踩过）。
    """

    def _app_tree(self):
        return ast.parse(_module_src("ui/app_music.py"))

    def _method(self, name):
        tree = self._app_tree()
        cls = next(
            n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "MusicPlayerMixin"
        )
        return next(n for n in cls.body if getattr(n, "name", None) == name)

    def test_ui_still_uses_the_inline_prefix_check(self):
        node = self._method("_build_sidebar_item")
        prefix_calls = [
            n
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "startswith"
            and any(
                isinstance(a, ast.Name) and a.id == "_WY_REMOTE_PREFIX" for a in n.args
            )
        ]
        slice_reads = [
            n
            for n in ast.walk(node)
            if isinstance(n, ast.Subscript)
            and isinstance(n.slice, ast.Slice)
            and "_WY_REMOTE_PREFIX" in ast.unparse(n.slice)
        ]
        assert len(prefix_calls) == 1, ast.dump(node) if not prefix_calls else "多处前缀判定"
        assert len(slice_reads) == 1

    def test_service_entries_have_no_ui_caller_yet(self):
        tree = self._app_tree()
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        assert "is_remote_key" not in attrs
        assert "strip_remote_key" not in attrs
        # entry_label 已经接上了（走 wy_remote.sidebar_items）
        assert "sidebar_items" in attrs

    def test_the_three_entries_exist_in_the_service(self):
        """它们本身在服务里没被删——只是界面还没接（报告里的未完成项）。"""
        for name in ("is_remote_key", "strip_remote_key", "entry_label"):
            assert name in wy.__all__
            assert callable(getattr(wy, name))
