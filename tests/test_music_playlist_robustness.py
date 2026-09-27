"""歌单持久化与去重的健壮性用例（E 组 WP2：D-20 / D-22 的回归守卫，缺陷号见
``poc/review/d_group/defect_disposition.md``）。

## 为什么单开一个文件、而且直接 ``import services.music_playlist``

* ``tests/test_music_playlist.py`` 是按**文件路径**加载 ``ui/music_playlist.py`` 那个转发 shim 的，
  它拿到的模块对象不是实现模块本身（shim 的 ``sys.modules[__name__] = _impl`` 只对 ``import``
  路径生效）。纯行为断言无所谓，但本文件要 ``monkeypatch.setattr(mp, "get_music_data_path", ...)``
  —— 打在 shim 的命名空间上会**静默失效**，那正是 shim 文档里记着的坑。
* 回退根目录必须能重定向，否则"目录不可写"的用例会真的往开发机的
  ``%LOCALAPPDATA%\\FMCL\\data`` 里写 music.json。

## 这些用例为什么不是空断言

把 ``services/music_playlist.py`` 的修复撤掉（``git stash`` 该文件）再跑本文件：
``TestDataDirFallback`` 的 4 条会全部报错 —— 三条在 ``FileExistsError`` / ``OSError`` 上失败，
一条在"没有回退"的断言上失败；``TestDedupKeyComputesPathOnce`` 的 3 条会在
``normpath`` 调用次数（8 次 vs 1 次）上失败。实测输出见
``poc/review/e_group/wp2_music_playlist.md``。
"""

import logging
import os
from pathlib import Path

import config
import services.music_playlist as mp

#: 用于"歌单里没有这首歌"的候选路径（本地绝对路径，不会与测试里造的路径撞上）
ABSENT_PATH = "/music/definitely-not-in-playlist.mp3"

LOGGER_NAME = "services.music_playlist"


def _block_directory(path: Path) -> None:
    """用一个**同名文件**占住"本该是目录"的路径，让 mkdir 必然失败

    不用"只读目录"：Windows 上目录的只读属性对大多数 API 无效，而"路径被文件占住"
    不需要管理员权限、在所有平台都稳定复现，也正好覆盖真实的"父目录建不出来"。
    """
    path.write_text("占位文件：这个路径本该是目录", encoding="utf-8")


def _redirect_fallback_root(monkeypatch, root: Path) -> None:
    """把"用户数据目录"（回退根）重定向到 tmp_path，避免污染开发机的真实目录

    环境变量与 config.py 的辅助函数两个方向都堵上：前者覆盖"将来回退改回自己算"的情形，
    后者保证与本机平台无关（Windows 读 %LOCALAPPDATA%，macOS/Linux 走 home）。
    """
    monkeypatch.setenv("LOCALAPPDATA", str(root))
    monkeypatch.setattr(config, "_get_user_data_dir", lambda: root, raising=False)


def _messages(caplog, level: int) -> list:
    """取出达到 ``level`` 的日志消息（本模块只有这一条日志通道）。"""
    return [r.getMessage() for r in caplog.records if r.levelno >= level]


class TestDataDirFallback:
    """D-20：目录建不出来 / 不可写时必须回退 + 打日志，而不是把异常抛给会吞掉它的调用方"""

    def test_primary_blocked_falls_back_and_warns(self, tmp_path, monkeypatch, caplog):
        """``./data`` 被同名文件占住：不抛异常、回退到用户数据目录、留下 warning"""
        monkeypatch.chdir(tmp_path)
        cwd = Path.cwd()
        primary = cwd / "data"
        _block_directory(primary)
        user_root = tmp_path / "userdata"
        _redirect_fallback_root(monkeypatch, user_root)

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            resolved = mp.get_music_data_dir()

        assert resolved == user_root / "data", "没有回退到用户数据目录"
        assert resolved.is_dir(), "回退目录没有被建出来"
        assert primary.is_file(), "原路径应保持'被文件占住'的样子（用例的前提）"
        warnings = _messages(caplog, logging.WARNING)
        assert warnings, "回退没有留下任何日志 —— 用户只会看到'歌单静默不落盘'"
        assert any(str(primary) in m and str(user_root / "data") in m for m in warnings), warnings

    def test_no_failure_keeps_primary_and_stays_silent(self, tmp_path, monkeypatch, caplog):
        """没有故障时不许回退、也不许刷日志（否则"回退"会退化成"总是正常"）"""
        monkeypatch.chdir(tmp_path)
        _redirect_fallback_root(monkeypatch, tmp_path / "userdata")

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            resolved = mp.get_music_data_dir()

        assert resolved == Path.cwd() / "data"
        assert resolved.is_dir()
        assert caplog.records == [], f"正常路径不该产生日志：{_messages(caplog, logging.DEBUG)}"

    def test_unwritable_both_ways_does_not_raise_and_keeps_dirty(self, tmp_path, monkeypatch, caplog):
        """两个目录都不可写：``save_if_dirty`` 不抛、留 ERROR、且**不许**冒充已保存"""
        monkeypatch.chdir(tmp_path)
        _block_directory(Path.cwd() / "data")
        user_root = tmp_path / "userdata"
        user_root.mkdir()
        _block_directory(user_root / "data")
        _redirect_fallback_root(monkeypatch, user_root)

        mgr = mp.PlaylistManager()
        mgr.create_playlist("写不进去的歌单")

        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            mgr.save_if_dirty()  # 撤掉修复：这里会抛 FileExistsError，异常冒到 Tk 回调里被吞

        assert mgr._dirty is True, "没落盘就不能清脏标记，否则再也不会重试"
        errors = _messages(caplog, logging.ERROR)
        assert any("不可写" in m for m in errors), errors
        assert any("落盘失败" in m for m in errors), errors

    def test_playlist_really_lands_in_the_fallback_dir(self, tmp_path, monkeypatch, caplog):
        """正例：回退之后歌单必须**真的**写进回退目录，并能被下一次 ``load()`` 读回来"""
        monkeypatch.chdir(tmp_path)
        _block_directory(Path.cwd() / "data")
        user_root = tmp_path / "userdata"
        _redirect_fallback_root(monkeypatch, user_root)

        mgr = mp.PlaylistManager()
        pl = mgr.create_playlist("落盘歌单")
        mgr.add_song(pl.id, mp.PlaylistSong(source_type="local", file_path="/a.mp3", display_title="A"))

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            mgr.save_if_dirty()

        assert mgr._dirty is False, "写成功了就该清脏标记"
        written = user_root / "data" / "music.json"
        assert written.is_file(), "歌单没有落到回退目录里（正是 D-20 的用户可见症状）"
        assert "落盘歌单" in written.read_text(encoding="utf-8")

        restored = mp.PlaylistManager()
        restored.load()
        assert [p.name for p in restored.user_playlists] == ["落盘歌单"]
        assert len(restored.user_playlists[0].songs) == 1
        assert _messages(caplog, logging.WARNING), "回退路径必须能在日志里看见"

    def test_load_and_save_survive_path_resolution_failure(self, tmp_path, monkeypatch, caplog):
        """路径**解析**本身失败（cwd 被删 / 目录异常）同样不能抛给 Tk 回调"""

        def boom():
            raise OSError("cwd 已被删除")

        monkeypatch.setattr(mp, "get_music_data_path", boom)
        mgr = mp.PlaylistManager()

        with caplog.at_level(logging.ERROR, logger=LOGGER_NAME):
            mgr.load()  # 不抛：按"没有歌单文件"处理，历史歌单仍在
            mgr.create_playlist("只存在内存里")
            mgr.save_if_dirty()  # 不抛：脏标记保留，等下次重试

        assert mgr.get_playlist(mp.HISTORY_PLAYLIST_ID) is not None
        assert mgr._dirty is True
        assert len(_messages(caplog, logging.ERROR)) == 2, _messages(caplog, logging.ERROR)


class TestDedupKeyComputesPathOnce:
    """D-22：候选歌的 ``normpath`` 必须在循环外算一次 —— 用**调用次数**证明，而不是读代码"""

    @staticmethod
    def _count_normpath(monkeypatch):
        """统计 ``os.path.normpath`` 的入参（模块里就是通过这个全局名调用的）"""
        calls = []
        real = os.path.normpath

        def counted(path):
            calls.append(path)
            return real(path)

        monkeypatch.setattr(os.path, "normpath", counted)
        return calls

    @staticmethod
    def _manager_with_local_songs(count: int):
        mgr = mp.PlaylistManager()
        pl = mgr.create_playlist("P")
        paths = [f"/music/t{i}.mp3" for i in range(count)]
        for path in paths:
            mgr.add_song(pl.id, mp.PlaylistSong(source_type="local", file_path=path))
        return mgr, pl, paths

    def test_is_song_in_playlist_normalizes_the_candidate_once(self, monkeypatch):
        mgr, pl, paths = self._manager_with_local_songs(8)
        calls = self._count_normpath(monkeypatch)

        candidate = mp.PlaylistSong(source_type="local", file_path=ABSENT_PATH)
        assert mgr.is_song_in_playlist(pl.id, candidate) is False

        assert calls.count(ABSENT_PATH) == 1, f"候选歌被规范化了 {calls.count(ABSENT_PATH)} 次：{calls}"
        assert sorted(calls) == sorted(paths + [ABSENT_PATH]), calls

    def test_add_song_normalizes_the_candidate_once(self, monkeypatch):
        mgr, pl, paths = self._manager_with_local_songs(8)
        calls = self._count_normpath(monkeypatch)

        candidate = mp.PlaylistSong(source_type="local", file_path=ABSENT_PATH)
        assert mgr.add_song(pl.id, candidate) is True

        assert calls.count(ABSENT_PATH) == 1, f"候选歌被规范化了 {calls.count(ABSENT_PATH)} 次：{calls}"
        assert sorted(calls) == sorted(paths + [ABSENT_PATH]), calls

    def test_record_to_history_normalizes_the_candidate_once(self, monkeypatch):
        """播放历史是热路径（每播一首走一次），同样不能逐首重算候选歌的路径

        历史里放满 8 首：撤掉修复时这个用例会数到 8 次而不是 1 次。
        """
        mgr, _pl, _paths = self._manager_with_local_songs(8)
        history_paths = [f"/music/h{i}.mp3" for i in range(8)]
        for path in history_paths:
            mgr.record_to_history(mp.PlaylistSong(source_type="local", file_path=path))
        calls = self._count_normpath(monkeypatch)

        song = mp.PlaylistSong(source_type="local", file_path=ABSENT_PATH)
        assert mgr.record_to_history(song) is True

        # 去重只看历史歌单（8 首 + 候选歌各一次）
        assert calls.count(ABSENT_PATH) == 1, f"候选歌被规范化了 {calls.count(ABSENT_PATH)} 次：{calls}"
        assert sorted(calls) == sorted(history_paths + [ABSENT_PATH]), calls

    def test_query_helpers_normalize_the_query_path_once(self, monkeypatch):
        """``is_song_in_any_playlist`` / ``get_playlist_names_for_song`` 同样只算一次查询路径"""
        mgr, _pl, paths = self._manager_with_local_songs(8)
        calls = self._count_normpath(monkeypatch)

        assert mgr.is_song_in_any_playlist(file_path=ABSENT_PATH) is False
        assert calls.count(ABSENT_PATH) == 1, f"查询路径被规范化了 {calls.count(ABSENT_PATH)} 次：{calls}"
        assert sorted(calls) == sorted(paths + [ABSENT_PATH]), calls

        calls.clear()
        assert mgr.get_playlist_names_for_song(file_path=ABSENT_PATH) == []
        assert calls.count(ABSENT_PATH) == 1, f"查询路径被规范化了 {calls.count(ABSENT_PATH)} 次：{calls}"
        assert sorted(calls) == sorted(paths + [ABSENT_PATH]), calls


class TestDedupSemanticsUnchanged:
    """D-22 只是把键提到循环外：判定语义必须逐条不变（含混杂类型、未知类型、空参数）"""

    def test_unknown_source_type_is_never_a_match(self):
        """未知 ``source_type`` 不算同一首 —— 两边键都是 ``None`` 时**不能**判等"""
        mgr = mp.PlaylistManager()
        pl = mgr.create_playlist("P")
        assert mgr.add_song(pl.id, mp.PlaylistSong(source_type="usb", file_path="/x.mp3")) is True
        assert mgr.add_song(pl.id, mp.PlaylistSong(source_type="usb", file_path="/x.mp3")) is True
        assert len(pl.songs) == 2, "旧写法对未知类型不进入任何分支，同样不判重"
        assert mgr.is_song_in_playlist(pl.id, mp.PlaylistSong(source_type="usb", file_path="/x.mp3")) is False

    def test_local_and_online_never_cross_match(self):
        mgr = mp.PlaylistManager()
        pl = mgr.create_playlist("P")
        assert mgr.add_song(pl.id, mp.PlaylistSong(source_type="local", file_path="kw")) is True
        online = mp.PlaylistSong(source_type="online", online_source="kw", online_songmid="")
        assert mgr.add_song(pl.id, online) is True
        assert len(pl.songs) == 2
        assert mgr.is_song_in_playlist(pl.id, mp.PlaylistSong(source_type="local", file_path="kw")) is True
        assert mgr.is_song_in_playlist(pl.id, mp.PlaylistSong(source_type="local", file_path="/kw")) is False

    def test_local_paths_are_still_normalized(self):
        """不同写法指向同一文件时仍算同一首（``normpath`` 语义没有被优化掉）"""
        mgr = mp.PlaylistManager()
        pl = mgr.create_playlist("P")
        assert mgr.add_song(pl.id, mp.PlaylistSong(source_type="local", file_path="music//a.mp3")) is True
        assert mgr.add_song(pl.id, mp.PlaylistSong(source_type="local", file_path="music/a.mp3")) is False
        assert mgr.is_song_in_any_playlist(file_path="music/./a.mp3") is True

    def test_query_helpers_ignore_empty_arguments(self):
        """空参数不参与判定（旧写法里的 ``if file_path`` / ``if online_source`` 守卫）"""
        mgr = mp.PlaylistManager()
        pl = mgr.create_playlist("P")
        mgr.add_song(pl.id, mp.PlaylistSong(source_type="online", online_source="kw", online_songmid="m1"))

        assert mgr.is_song_in_any_playlist() is False
        assert mgr.get_playlist_names_for_song() == []
        assert mgr.is_song_in_any_playlist(online_source="kw", online_songmid="m1") is True
        assert mgr.get_playlist_names_for_song(online_source="kw", online_songmid="m1") == ["P"]
        assert mgr.is_song_in_any_playlist(online_source="kw", online_songmid="m2") is False
        assert mgr.is_song_in_any_playlist(file_path=ABSENT_PATH) is False

    def test_system_playlists_are_excluded_from_the_query_helpers(self):
        mgr = mp.PlaylistManager()
        mgr.record_to_history(mp.PlaylistSong(source_type="local", file_path="/a.mp3"))
        assert mgr.is_song_in_any_playlist(file_path="/a.mp3") is False
        assert mgr.get_playlist_names_for_song(file_path="/a.mp3") == []

    def test_matches_still_agrees_with_the_dedup_key(self):
        """``matches`` 改用同一套键之后，四类组合的返回值必须与旧写法一致"""
        local_a = mp.PlaylistSong(source_type="local", file_path="/a.mp3")
        local_a2 = mp.PlaylistSong(source_type="local", file_path="/a.mp3")
        local_b = mp.PlaylistSong(source_type="local", file_path="/b.mp3")
        online_1 = mp.PlaylistSong(source_type="online", online_source="kw", online_songmid="abc")
        online_2 = mp.PlaylistSong(source_type="online", online_source="kw", online_songmid="abc")
        online_3 = mp.PlaylistSong(source_type="online", online_source="kw", online_songmid="xyz")
        unknown = mp.PlaylistSong(source_type="usb", file_path="/a.mp3")

        assert local_a.matches(local_a2) is True
        assert local_a.matches(local_b) is False
        assert online_1.matches(online_2) is True
        assert online_1.matches(online_3) is False
        assert local_a.matches(online_1) is False
        assert unknown.matches(unknown) is False, "未知类型既不等于别人，也不等于自己"
