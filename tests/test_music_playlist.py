"""歌单管理模块测试 - PlaylistSong, Playlist, PlaylistManager"""
import importlib
import os
import sys
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock

import pytest

# ── 绕过 ui/__init__.py（会导入 customtkinter 等 GUI 依赖）──
# 直接将 module 从文件加载，不触发 package __init__
_spec = importlib.util.spec_from_file_location(
    "ui.music_playlist",
    os.path.join(os.path.dirname(__file__), "..", "ui", "music_playlist.py"),
    submodule_search_locations=[],
)
mp = importlib.util.module_from_spec(_spec)
sys.modules["ui.music_playlist"] = mp
_spec.loader.exec_module(mp)

# 导出测试目标
PlaylistSong = mp.PlaylistSong
Playlist = mp.Playlist
PlaylistManager = mp.PlaylistManager
get_music_data_dir = mp.get_music_data_dir
get_music_data_path = mp.get_music_data_path
SORT_ADD_TIME_ASC = mp.SORT_ADD_TIME_ASC
SORT_ADD_TIME_DESC = mp.SORT_ADD_TIME_DESC
SORT_NAME_ASC = mp.SORT_NAME_ASC
SORT_NAME_DESC = mp.SORT_NAME_DESC
_name_sort_key = mp._name_sort_key
HISTORY_PLAYLIST_ID = mp.HISTORY_PLAYLIST_ID


class TestPlaylistSong:
    def test_create_local_song(self):
        song = PlaylistSong(source_type="local", file_path="/tmp/test.mp3", display_title="Test Song")
        assert song.source_type == "local"
        assert song.file_path == "/tmp/test.mp3"
        assert song.display_title == "Test Song"
        assert song._id != ""
        assert song.added_at > 0

    def test_to_dict_from_dict_roundtrip(self):
        original = PlaylistSong(
            source_type="online",
            online_source="kw",
            online_songmid="ABC123",
            online_name="起风了",
            online_singer="买辣椒也用券",
            online_interval=320,
            display_title="起风了",
            display_artist="买辣椒也用券",
            added_at=1234567890.0,
            _id="test001",
        )
        data = original.to_dict()
        restored = PlaylistSong.from_dict(data)
        assert restored._id == "test001"
        assert restored.online_source == "kw"
        assert restored.online_songmid == "ABC123"
        assert restored.online_name == "起风了"
        assert restored.added_at == 1234567890.0

    def test_to_dict_ignores_invalid_keys(self):
        data = {"source_type": "local", "file_path": "/a.mp3", "invalid_key": "should_be_ignored"}
        song = PlaylistSong.from_dict(data)
        assert song.source_type == "local"

    def test_get_display_text(self):
        song = PlaylistSong(display_title="Song", display_artist="Artist")
        assert song.get_display_text() == "Song - Artist"

    def test_get_display_text_no_artist(self):
        song = PlaylistSong(display_title="Song")
        assert song.get_display_text() == "Song"

    def test_from_local_file_accepts_metadata(self):
        song = PlaylistSong.from_local_file("/tmp/test.mp3", {"title": "My Song", "artist": "Me"})
        assert song.source_type == "local"
        assert song.file_path == "/tmp/test.mp3"
        assert song.display_title == "My Song"
        assert song.display_artist == "Me"
        assert song.added_at > 0

    def test_from_local_file_no_metadata(self):
        song = PlaylistSong.from_local_file("/tmp/test.mp3")
        assert song.display_title == "test"

    def test_get_sort_key_name(self):
        song = PlaylistSong(display_title="Hello")
        assert song.get_sort_key_name() == "hello"


class TestPlaylist:
    def test_create_playlist(self):
        pl = Playlist(name="我的收藏")
        assert pl.name == "我的收藏"
        assert pl.id.startswith("pl_")
        assert pl.songs == []
        assert pl.created_at > 0
        assert pl.song_count == 0

    def test_to_dict_from_dict_roundtrip(self):
        songs = [
            PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A", _id="s1"),
            PlaylistSong(source_type="online", online_source="kw", online_songmid="B", display_title="B", _id="s2"),
        ]
        pl = Playlist(id="pl_test", name="Test", songs=songs, sort_mode=SORT_NAME_ASC)
        data = pl.to_dict()
        restored = Playlist.from_dict(data)
        assert restored.id == "pl_test"
        assert restored.name == "Test"
        assert len(restored.songs) == 2
        assert restored.songs[0]._id == "s1"
        assert restored.songs[1]._id == "s2"
        assert restored.sort_mode == SORT_NAME_ASC

    def test_empty_playlist_from_dict(self):
        data = {"id": "pl_empty", "name": "Empty", "songs": []}
        pl = Playlist.from_dict(data)
        assert pl.songs == []
        assert pl.song_count == 0


class TestPlaylistManager:
    def test_create_and_get_playlist(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        assert mgr.get_playlist(pl.id) is pl
        assert pl in mgr.playlists

    def test_delete_playlist(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        mgr.set_current_playlist(pl.id)
        assert mgr.delete_playlist(pl.id) is True
        assert mgr.get_playlist(pl.id) is None
        assert mgr.current_playlist_id is None

    def test_delete_nonexistent_playlist(self):
        mgr = PlaylistManager()
        assert mgr.delete_playlist("nonexistent") is False

    def test_rename_playlist(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Old")
        assert mgr.rename_playlist(pl.id, "New") is True
        assert pl.name == "New"

    def test_rename_nonexistent(self):
        mgr = PlaylistManager()
        assert mgr.rename_playlist("nonexistent", "X") is False

    def test_set_current_playlist(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        mgr.set_current_playlist(pl.id)
        assert mgr.current_playlist_id == pl.id
        assert mgr.get_current_playlist() is pl

    def test_add_song(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        song = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A")
        assert mgr.add_song(pl.id, song) is True
        assert len(pl.songs) == 1

    def test_add_song_duplicate_local(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        s1 = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A")
        s2 = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A")
        assert mgr.add_song(pl.id, s1) is True
        assert mgr.add_song(pl.id, s2) is False
        assert len(pl.songs) == 1

    def test_add_song_duplicate_online(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        s1 = PlaylistSong(source_type="online", online_source="kw", online_songmid="abc", display_title="A")
        s2 = PlaylistSong(source_type="online", online_source="kw", online_songmid="abc", display_title="B")
        assert mgr.add_song(pl.id, s1) is True
        assert mgr.add_song(pl.id, s2) is False
        assert len(pl.songs) == 1

    def test_add_song_to_nonexistent(self):
        mgr = PlaylistManager()
        assert mgr.add_song("nonexistent", PlaylistSong()) is False

    def test_remove_song_by_index(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        mgr.add_song(pl.id, PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A"))
        assert mgr.remove_song(pl.id, 0) is True
        assert len(pl.songs) == 0

    def test_remove_song_out_of_range(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        assert mgr.remove_song(pl.id, 0) is False

    def test_remove_song_by_id(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        song = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A")
        mgr.add_song(pl.id, song)
        assert mgr.remove_song_by_id(pl.id, song._id) is True
        assert len(pl.songs) == 0

    def test_clear_playlist(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        mgr.add_song(pl.id, PlaylistSong(source_type="local", file_path="/a.mp3"))
        mgr.add_song(pl.id, PlaylistSong(source_type="local", file_path="/b.mp3"))
        assert mgr.clear_playlist(pl.id) is True
        assert len(pl.songs) == 0

    def test_is_song_in_any_playlist(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        mgr.add_song(pl.id, PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A"))
        assert mgr.is_song_in_any_playlist(file_path="/a.mp3") is True
        assert mgr.is_song_in_any_playlist(file_path="/b.mp3") is False

    def test_get_playlist_names_for_song(self):
        mgr = PlaylistManager()
        pl1 = mgr.create_playlist("Playlist1")
        pl2 = mgr.create_playlist("Playlist2")
        mgr.add_song(pl1.id, PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A"))
        mgr.add_song(pl2.id, PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A"))
        names = mgr.get_playlist_names_for_song(file_path="/a.mp3")
        assert "Playlist1" in names
        assert "Playlist2" in names

    def test_sort_add_time_asc(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        s1 = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A", added_at=100.0)
        s2 = PlaylistSong(source_type="local", file_path="/b.mp3", display_title="B", added_at=200.0)
        mgr.add_song(pl.id, s1)
        mgr.add_song(pl.id, s2)
        mgr.sort(pl.id, SORT_ADD_TIME_ASC)
        assert pl.songs[0].added_at <= pl.songs[1].added_at

    def test_sort_add_time_desc(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        s1 = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A", added_at=100.0)
        s2 = PlaylistSong(source_type="local", file_path="/b.mp3", display_title="B", added_at=200.0)
        mgr.add_song(pl.id, s1)
        mgr.add_song(pl.id, s2)
        mgr.sort(pl.id, SORT_ADD_TIME_DESC)
        assert pl.songs[0].added_at >= pl.songs[1].added_at

    def test_sort_name_asc(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        s1 = PlaylistSong(source_type="local", file_path="/b.mp3", display_title="Banana")
        s2 = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="Apple")
        mgr.add_song(pl.id, s1)
        mgr.add_song(pl.id, s2)
        mgr.sort(pl.id, SORT_NAME_ASC)
        titles = [s.display_title for s in pl.songs]
        assert titles == ["Apple", "Banana"]

    def test_sort_name_desc(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        s1 = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="Apple")
        s2 = PlaylistSong(source_type="local", file_path="/b.mp3", display_title="Banana")
        mgr.add_song(pl.id, s1)
        mgr.add_song(pl.id, s2)
        mgr.sort(pl.id, SORT_NAME_DESC)
        titles = [s.display_title for s in pl.songs]
        assert titles == ["Banana", "Apple"]

    def test_sort_order_on_add(self):
        """默认按 add_time_desc 排序"""
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        s1 = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A")
        time.sleep(0.01)
        s2 = PlaylistSong(source_type="local", file_path="/b.mp3", display_title="B")
        mgr.add_song(pl.id, s1)
        mgr.add_song(pl.id, s2)
        assert pl.songs[0].display_title == "B"
        assert pl.songs[1].display_title == "A"
    def test_save_and_load(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("SavedTest")
        mgr.add_song(pl.id, PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A"))
        mgr.set_current_playlist(pl.id)

        with NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            tmp_path = Path(f.name)

        try:
            mgr.save(tmp_path)

            mgr2 = PlaylistManager()
            mgr2.load(tmp_path)
            # 1 user playlist + 1 history playlist
            assert len(mgr2.user_playlists) == 1
            assert mgr2.user_playlists[0].name == "SavedTest"
            assert len(mgr2.user_playlists[0].songs) == 1
            assert mgr2.current_playlist_id == pl.id
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_load_nonexistent_file(self):
        mgr = PlaylistManager()
        mgr.load(Path("/tmp/nonexistent_test_file.json"))
        # Even with no file, history playlist should exist
        assert len(mgr.playlists) >= 1
        assert mgr.get_playlist(HISTORY_PLAYLIST_ID) is not None
        assert mgr.current_playlist_id is None

    def test_get_playable_songs_filters_missing_local(self):
        mgr = PlaylistManager()
        pl = mgr.create_playlist("Test")
        mgr.add_song(pl.id, PlaylistSong(source_type="local", file_path="/definitely/does/not/exist.mp3"))
        mgr.add_song(pl.id, PlaylistSong(source_type="online", online_source="kw", online_songmid="abc", display_title="Online"))
        songs = mgr.get_playable_songs(pl.id)
        assert len(songs) == 1
        assert songs[0].source_type == "online"

    def test_get_current_playlist_when_none(self):
        mgr = PlaylistManager()
        assert mgr.get_current_playlist() is None


class TestPathFunctions:
    def test_get_music_data_dir_creates(self):
        path = get_music_data_dir()
        assert isinstance(path, Path)
        assert path.exists()

    def test_get_music_data_path(self):
        path = get_music_data_path()
        assert isinstance(path, Path)
        assert path.name == "music.json"


class TestNameSortKey:
    def test_english_title(self):
        assert _name_sort_key("Apple") < _name_sort_key("Banana")

    def test_empty_title(self):
        assert _name_sort_key("") == ""

    def test_case_insensitive(self):
        assert _name_sort_key("apple") == _name_sort_key("Apple")

    def test_chinese_title_fallback(self):
        """即使没有 pypinyin，排序也不应该报错"""
        key = _name_sort_key("中文歌曲")
        assert isinstance(key, str)


class TestPlaylistHistory:
    def test_get_or_create_history(self):
        mgr = PlaylistManager()
        pl = mgr.get_or_create_history_playlist()
        assert pl.id == HISTORY_PLAYLIST_ID
        assert pl.is_system is True
        assert pl in mgr.playlists

    def test_history_is_system(self):
        mgr = PlaylistManager()
        pl = mgr.get_or_create_history_playlist()
        assert mgr.is_system_playlist(pl.id) is True

    def test_history_cannot_be_deleted(self):
        mgr = PlaylistManager()
        pl = mgr.get_or_create_history_playlist()
        assert mgr.delete_playlist(pl.id) is False
        assert mgr.get_playlist(pl.id) is not None

    def test_history_cannot_be_renamed(self):
        mgr = PlaylistManager()
        pl = mgr.get_or_create_history_playlist()
        assert mgr.rename_playlist(pl.id, "New Name") is False
        assert pl.name != "New Name"

    def test_record_to_history(self):
        mgr = PlaylistManager()
        song = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A")
        assert mgr.record_to_history(song) is True
        pl = mgr.get_or_create_history_playlist()
        assert len(pl.songs) == 1
        assert pl.songs[0]._id == song._id

    def test_record_to_history_dedup(self):
        mgr = PlaylistManager()
        s1 = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A")
        s2 = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A")
        mgr.record_to_history(s1)
        mgr.record_to_history(s2)
        pl = mgr.get_or_create_history_playlist()
        assert len(pl.songs) == 1  # dedup
        assert pl.songs[0]._id == s2._id  # updated to latest

    def test_record_to_history_max(self):
        mgr = PlaylistManager()
        for i in range(mp.MAX_HISTORY + 10):
            s = PlaylistSong(source_type="local", file_path=f"/test_{i}.mp3", display_title=f"Song {i}")
            mgr.record_to_history(s)
        pl = mgr.get_or_create_history_playlist()
        assert len(pl.songs) == mp.MAX_HISTORY

    def test_record_to_history_online(self):
        mgr = PlaylistManager()
        song = PlaylistSong(source_type="online", online_source="kw", online_songmid="abc", display_title="Online")
        assert mgr.record_to_history(song) is True
        pl = mgr.get_or_create_history_playlist()
        assert len(pl.songs) == 1

    def test_history_persists_across_load_save(self):
        mgr = PlaylistManager()
        song = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A")
        mgr.record_to_history(song)

        with NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            tmp_path = Path(f.name)
        try:
            mgr.save(tmp_path)
            mgr2 = PlaylistManager()
            mgr2.load(tmp_path)
            pl = mgr2.get_or_create_history_playlist()
            assert len(pl.songs) == 1
            assert pl.is_system is True
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_history_auto_created_on_load(self):
        mgr = PlaylistManager()
        with NamedTemporaryFile(suffix=".json", delete=False, mode="w") as f:
            tmp_path = Path(f.name)
        try:
            # Empty file, no history
            tmp_path.write_text('{"version":1,"playlists":[],"current_playlist_id":null}', encoding="utf-8")
            mgr.load(tmp_path)
            pl = mgr.get_or_create_history_playlist()
            assert pl is not None
            assert pl.is_system is True
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_user_playlists_excludes_history(self):
        mgr = PlaylistManager()
        mgr.get_or_create_history_playlist()
        mgr.create_playlist("My Playlist")
        assert len(mgr.user_playlists) == 1
        assert mgr.user_playlists[0].name == "My Playlist"


class TestSystemPlaylistProtection:
    def test_system_playlist_reject_clear(self):
        mgr = PlaylistManager()
        pl = mgr.get_or_create_history_playlist()
        s = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A")
        mgr.record_to_history(s)
        assert mgr.clear_playlist(pl.id) is False
        assert len(pl.songs) == 1

    def test_system_playlist_reject_remove_song(self):
        mgr = PlaylistManager()
        pl = mgr.get_or_create_history_playlist()
        s = PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A")
        mgr.record_to_history(s)
        assert mgr.remove_song(pl.id, 0) is False
        assert len(pl.songs) == 1

    def test_playlist_song_matches(self):
        s1 = PlaylistSong(source_type="local", file_path="/a.mp3")
        s2 = PlaylistSong(source_type="local", file_path="/a.mp3")
        s3 = PlaylistSong(source_type="local", file_path="/b.mp3")
        assert s1.matches(s2) is True
        assert s1.matches(s3) is False

        o1 = PlaylistSong(source_type="online", online_source="kw", online_songmid="abc")
        o2 = PlaylistSong(source_type="online", online_source="kw", online_songmid="abc")
        o3 = PlaylistSong(source_type="online", online_source="kw", online_songmid="xyz")
        assert o1.matches(o2) is True
        assert o1.matches(o3) is False
        assert s1.matches(o1) is False
