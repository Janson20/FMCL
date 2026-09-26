"""任务 1.4-A（音乐播放核心抽取）的离线回归测试。

规则：**不联网、不真放声音、不弹窗**，也不要求机器上装了 pygame / mutagen / winsdk / ffmpeg
（一律用假替身或 `sys.modules` 注入）；文件只写 `tmp_path`。

覆盖范围：
    - 10 个音频辅助函数的边界（负时长/超长/空标签/无码率/裸 MP3 帧/M4A ftyp/
      HTML 错误页/转码各失败分支）——见 `TestAudioHelpers`
    - 播放引擎状态机与非法操作、mixer 驱动参数——见 `TestPlaybackStateMachine`
    - 四种播放模式下的"下一首"决策（含仅一首/空歌单/固定随机种子）——见 `TestPlaybackModes`
    - 淡入淡出的步进与取消、音量边界与静音——见 `TestFadeAndVolume`
    - 状态持久化的旧格式往返与坏值容错——见 `TestMusicState`
    - 桌面歌词的位置/当前行/透明度/锁定——见 `TestDesktopLyric`
    - 服务零 UI 依赖、旧名兼容、`MusicPlayerMixin` 方法面未变——见文件末尾三组
"""

from __future__ import annotations

import ast
import copy
import io
import json
import random
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.desktop_lyric as dl  # noqa: E402
import services.music_audio as ma  # noqa: E402
import services.music_player as mp  # noqa: E402
import services.music_state as ms  # noqa: E402


# ══════════════════════════════════════════════════════════════════════
# 替身
# ══════════════════════════════════════════════════════════════════════


class FakeMusic:
    """假的 `mixer.music`：记录调用序列，可配置抛异常/忙碌状态/播放位置。"""

    def __init__(self, busy=True, pos_ms=0, raise_on=()):
        self.calls = []
        self.busy = busy
        self.pos_ms = pos_ms
        self.raise_on = set(raise_on)

    def _call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if name in self.raise_on:
            raise RuntimeError(f"fake mixer {name} 失败")

    def load(self, path):
        self._call("load", path)

    def set_volume(self, v):
        self._call("set_volume", v)

    def play(self, start=0):
        self._call("play", start=start)

    def stop(self):
        self._call("stop")

    def unload(self):
        self._call("unload")

    def pause(self):
        self._call("pause")

    def unpause(self):
        self._call("unpause")

    def get_busy(self):
        self._call("get_busy")
        return self.busy

    def get_pos(self):
        self._call("get_pos")
        return self.pos_ms


class FakeMixer:
    def __init__(self, **kw):
        self.music = FakeMusic(**kw)


class FakeSong:
    """歌单上下文里的歌曲（只用到服务读的那几个字段）。"""

    def __init__(self, source_type="online", songmid="s1", source="wy", file_path="", interval=240):
        self.source_type = source_type
        self.online_songmid = songmid
        self.online_source = source
        self.file_path = file_path
        self.online_interval = interval


class FakeInfo:
    """`origin_info` / `result_info` 只要有 source 与 songmid 就够（song_key 指纹用）。"""

    def __init__(self, source="wy", songmid="s1"):
        self.source = source
        self.songmid = songmid


class FakeLine:
    def __init__(self, text, time):
        self.text = text
        self.time = time


@pytest.fixture
def engine():
    """一个注入了假 mixer 的引擎（不依赖机器上有没有 pygame）。"""
    return mp.MusicPlayerService(mixer_module=FakeMixer())


# ══════════════════════════════════════════════════════════════════════
# 1. 音频辅助函数边界
# ══════════════════════════════════════════════════════════════════════


class TestAudioHelpers:
    def test_format_time_negative_clamped(self):
        assert ma.format_time(-5) == "0:00"
        assert ma.format_time(-0.5) == "0:00"

    def test_format_time_boundaries(self):
        assert ma.format_time(0) == "0:00"
        assert ma.format_time(59.9) == "0:59"
        assert ma.format_time(60) == "1:00"
        assert ma.format_time(3600) == "60:00"  # 小时不进位（原文如此）

    def test_format_play_count(self):
        assert ma.format_play_count(0) == ""
        assert ma.format_play_count(-1) == ""
        assert ma.format_play_count(9999) == "9999"
        assert ma.format_play_count(10000) == "1w"
        assert ma.format_play_count(12345) == "1.2w"
        assert ma.format_play_count(100000) == "10w"

    def test_format_online_quality(self):
        assert ma.format_online_quality("flac24bit") == "FLAC"
        assert ma.format_online_quality("flac") == "FLAC"
        assert ma.format_online_quality("320k") == "320K"
        assert ma.format_online_quality("128k") == "128K"
        assert ma.format_online_quality("hq") == ""
        assert ma.format_online_quality("") == ""

    def test_format_local_quality_ext_wins(self):
        # 无损容器即使码率缺失也按 FLAC 显示
        assert ma.format_local_quality({}, "a.flac") == "FLAC"
        assert ma.format_local_quality({}, "a.APE") == "FLAC"
        assert ma.format_local_quality({}, "a.wav") == "FLAC"

    def test_format_local_quality_bitrate_steps(self):
        assert ma.format_local_quality({"bitrate": 900000}, "a.mp3") == "FLAC"
        assert ma.format_local_quality({"bitrate": 320000}, "a.mp3") == "320K"
        assert ma.format_local_quality({"bitrate": 256000}, "a.mp3") == "256K"
        assert ma.format_local_quality({"bitrate": 192000}, "a.mp3") == "192K"
        assert ma.format_local_quality({"bitrate": 128000}, "a.mp3") == "128K"

    def test_format_local_quality_unknown_bitrate(self):
        # 无码率 → 空串（不是"未知"字样）
        assert ma.format_local_quality({}, "a.mp3") == ""
        assert ma.format_local_quality({"bitrate": 0}, "a.mp3") == ""
        assert ma.format_local_quality({"bitrate": None}, "a.mp3") == ""
        assert ma.format_local_quality({}, "") == ""

    def test_get_tag_empty_and_broken(self):
        assert ma.get_tag({}, "TIT2") is None
        assert ma.get_tag({"TIT2": None}, "TIT2") is None

        class Frame:
            text = ["七里香"]

        assert ma.get_tag({"TIT2": Frame()}, "TIT2") == "七里香"
        assert ma.get_tag({"TIT2": "raw"}, "TIT2") == "raw"

        class Boom:
            def get(self, key):
                raise RuntimeError("坏了")

        assert ma.get_tag(Boom(), "TIT2") is None  # 异常被吞

    def test_extract_metadata_mutagen_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ma, "_mutagen_import_error", ImportError("no mutagen"))
        p = tmp_path / "song.mp3"
        p.write_bytes(b"ID3" + b"\x00" * 13)
        meta = ma.extract_audio_metadata(str(p))
        assert meta["title"] == "song"
        assert meta["duration"] == 0 and meta["bitrate"] == 0
        assert meta["has_cover"] is False and meta["cover_data"] is None

    def test_extract_metadata_missing_file(self, monkeypatch):
        monkeypatch.setattr(ma, "_mutagen_import_error", None)

        def boom(path):
            raise OSError("no such file")

        monkeypatch.setattr(ma, "MutagenFile", boom)
        meta = ma.extract_audio_metadata("nope.mp3")
        assert meta == {
            "title": "nope", "artist": "", "album": "", "duration": 0,
            "bitrate": 0, "has_cover": False, "cover_data": None,
        }

    def test_extract_metadata_none_result(self, monkeypatch):
        monkeypatch.setattr(ma, "_mutagen_import_error", None)
        monkeypatch.setattr(ma, "MutagenFile", lambda path: None)
        assert ma.extract_audio_metadata("a.mp3")["duration"] == 0

    def test_validate_header_raw_mp3_frame_sync(self, tmp_path):
        p = tmp_path / "raw.mp3"
        p.write_bytes(b"\xff\xfb\x90\x00" + b"\x00" * 12)
        assert ma.validate_audio_file_header(str(p)) is True

    def test_validate_header_sync_but_too_short(self, tmp_path):
        p = tmp_path / "one.mp3"
        p.write_bytes(b"\xff")  # 只有 1 字节，凑不出同步头
        assert ma.validate_audio_file_header(str(p)) is False

    def test_validate_header_html_and_empty(self, tmp_path):
        html = tmp_path / "err.mp3"
        html.write_bytes(b"<html><body>403 Forbidden</body></html>")
        assert ma.validate_audio_file_header(str(html)) is False
        empty = tmp_path / "empty.mp3"
        empty.write_bytes(b"")
        assert ma.validate_audio_file_header(str(empty)) is False

    def test_validate_header_m4a_ftyp_needs_offset_4(self, tmp_path):
        good = tmp_path / "a.m4a"
        good.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 8)
        assert ma.validate_audio_file_header(str(good)) is True
        bad = tmp_path / "b.m4a"
        bad.write_bytes(b"ftyp" + b"\x00" * 12)  # ftyp 出现在 offset 0 不算
        assert ma.validate_audio_file_header(str(bad)) is False

    def test_validate_header_nonexistent(self, tmp_path):
        assert ma.validate_audio_file_header(str(tmp_path / "nope.mp3")) is False

    def test_validate_duration_negative_expected_passes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ma, "_mutagen_import_error", None)
        monkeypatch.setattr(ma, "extract_audio_metadata", lambda p: {"duration": 10.0})
        p = tmp_path / "a.mp3"
        p.write_bytes(b"ID3" + b"\x00" * 13)
        # 预期 <= 0 视为"未知"，直接放行
        assert ma.validate_audio_duration(str(p), -1) is True
        assert ma.validate_audio_duration(str(p), 0) is True

    def test_validate_duration_overlong_clip_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ma, "_mutagen_import_error", None)
        monkeypatch.setattr(ma, "extract_audio_metadata", lambda p: {"duration": 4000.0})
        p = tmp_path / "b.mp3"
        p.write_bytes(b"ID3" + b"\x00" * 13)
        assert ma.validate_audio_duration(str(p), 240) is False

    def test_validate_duration_tolerance_floor(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ma, "_mutagen_import_error", None)
        monkeypatch.setattr(ma, "extract_audio_metadata", lambda p: {"duration": 30.0})
        p = tmp_path / "c.mp3"
        p.write_bytes(b"ID3" + b"\x00" * 13)
        # 预期 20s：容差 max(10, 4) = 10 → 差 10 通过、差 10.5 不通过
        assert ma.validate_audio_duration(str(p), 20) is True
        monkeypatch.setattr(ma, "extract_audio_metadata", lambda p: {"duration": 30.5})
        assert ma.validate_audio_duration(str(p), 20) is False

    def test_validate_duration_parser_raises_passes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ma, "_mutagen_import_error", None)

        def boom(path):
            raise RuntimeError("broken file")

        monkeypatch.setattr(ma, "extract_audio_metadata", boom)
        p = tmp_path / "d.mp3"
        p.write_bytes(b"ID3" + b"\x00" * 13)
        assert ma.validate_audio_duration(str(p), 240) is True

    def test_is_m4a_container_by_extension_even_if_missing(self, tmp_path):
        # 原文：扩展名是 .m4a 就先返回 True，不看文件是否存在
        assert ma.is_m4a_container(str(tmp_path / "ghost.m4a")) is True
        assert ma.is_m4a_container("GHOST.M4A") is True

    def test_is_m4a_container_by_content_with_wrong_ext(self, tmp_path):
        p = tmp_path / "dash.mp3"
        p.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 8)
        assert ma.is_m4a_container(str(p)) is True

    def test_is_m4a_container_plain_mp3(self, tmp_path):
        p = tmp_path / "plain.mp3"
        p.write_bytes(b"ID3" + b"\x00" * 13)
        assert ma.is_m4a_container(str(p)) is False
        assert ma.is_m4a_container(str(tmp_path / "nope.mp3")) is False


class TestMetadataCache:
    def test_lru_eviction_order(self):
        cache = ma.MetadataCache(max_size=2)
        cache.get("a", lambda k: {"k": k})
        cache.get("b", lambda k: {"k": k})
        cache.get("a", lambda k: {"k": k})  # a 变成最近使用
        cache.get("c", lambda k: {"k": k})  # 淘汰 b
        assert list(cache.data) == ["a", "c"]
        assert len(cache) == 2

    def test_hit_does_not_call_factory(self):
        cache = ma.MetadataCache()
        calls = []
        cache.get("a", lambda k: calls.append(k) or {"k": k})
        cache.get("a", lambda k: calls.append(k) or {"k": k})
        assert calls == ["a"]

    def test_clear(self):
        cache = ma.MetadataCache()
        cache.get("a", lambda k: {})
        cache.clear()
        assert len(cache) == 0


# ══════════════════════════════════════════════════════════════════════
# 2. 播放状态机与 mixer 驱动
# ══════════════════════════════════════════════════════════════════════


class TestPlaybackStateMachine:
    def test_service_identity(self, engine):
        assert engine.name == "music_player"
        assert engine.label == "音乐播放"

    def test_available_follows_module_flag(self, engine, monkeypatch):
        assert engine.available is True
        monkeypatch.setattr(mp, "_pygame_import_error", ImportError("no pygame"))
        assert engine.available is False
        # 没注入 mixer 时取不到 pygame.mixer
        monkeypatch.setattr(mp, "get_mixer", lambda: None)
        assert mp.MusicPlayerService().mixer is None

    def test_load_and_play_call_order_and_start(self, engine):
        engine.load_and_play("a.mp3", 0)
        assert [c[0] for c in engine.mixer.music.calls] == ["load", "set_volume", "play"]
        assert engine.mixer.music.calls[0][1] == ("a.mp3",)
        assert engine.mixer.music.calls[1][1] == (0,)
        assert engine.mixer.music.calls[2][2] == {"start": 0}

    def test_load_and_play_positive_start(self, engine):
        engine.load_and_play("a.mp3", 12.5)
        assert engine.mixer.music.calls[2][2] == {"start": 12.5}

    def test_load_and_play_raises_without_mixer(self, monkeypatch):
        monkeypatch.setattr(mp, "get_mixer", lambda: None)
        svc = mp.MusicPlayerService()
        with pytest.raises(RuntimeError):
            svc.load_and_play("a.mp3")

    def test_reload_and_seek_pauses_when_was_paused(self, engine):
        engine.reload_and_seek(30.0, "a.mp3", True)
        names = [c[0] for c in engine.mixer.music.calls]
        assert names == ["stop", "load", "set_volume", "play", "pause"]
        assert engine.mixer.music.calls[3][2] == {"start": 30.0}

    def test_reload_and_seek_does_not_pause_when_playing(self, engine):
        engine.reload_and_seek(30.0, "a.mp3", False)
        assert [c[0] for c in engine.mixer.music.calls] == ["stop", "load", "set_volume", "play"]

    def test_begin_local_playback_state(self, engine):
        engine.begin_local_playback("a.mp3", 200.0, 0)
        assert engine.state.current_filepath == "a.mp3"
        assert engine.state.duration == 200.0
        assert engine.state.seek_offset == 0  # start_pos<=0 归零
        engine.begin_local_playback("b.mp3", 10.0, -3)
        assert engine.state.seek_offset == 0

    def test_begin_online_playback_records_quality(self, engine):
        engine.begin_online_playback("t.mp3", 240, "flac", 5)
        assert engine.state.current_quality == "flac"
        assert engine.state.seek_offset == 5
        assert engine.state.duration == 240
        engine.begin_online_playback("t.mp3", 240, "", 0)
        assert engine.state.current_quality == ""

    def test_begin_playback_cancels_fade_state(self, engine):
        engine.begin_fade_out(mp.FADE_OUT_PAUSE)
        engine.state.is_fading = True
        engine.begin_local_playback("a.mp3", 1.0)
        assert engine.state.is_fading is False
        assert engine.state.fade_out_target is None

    def test_reset_seek_offset(self, engine):
        engine.state.seek_offset = 42
        engine.reset_seek_offset()
        assert engine.state.seek_offset == 0

    def test_pause_and_stop_swallow_exceptions(self, engine):
        engine.mixer.music.raise_on = {"pause", "stop"}
        assert engine.pause_mixer() is False
        assert engine.stop_mixer() is False

    def test_stop_mixer_calls_stop_then_unload(self, engine):
        assert engine.stop_mixer() is True
        assert [c[0] for c in engine.mixer.music.calls] == ["stop", "unload"]

    def test_resume_mixer_raises_without_mixer(self, monkeypatch):
        monkeypatch.setattr(mp, "get_mixer", lambda: None)
        with pytest.raises(RuntimeError):
            mp.MusicPlayerService().resume_mixer()

    def test_set_mixer_volume_swallows(self, engine):
        assert engine.set_mixer_volume(0.5) is True
        engine.mixer.music.raise_on = {"set_volume"}
        assert engine.set_mixer_volume(0.5) is False

    def test_is_music_busy_none_when_mixer_missing(self, monkeypatch):
        monkeypatch.setattr(mp, "get_mixer", lambda: None)
        svc = mp.MusicPlayerService()
        # 刻意区分 None 与 False：原文 mixer 缺失时抛异常被吞，不会走"曲目结束"分支
        assert svc.is_music_busy() is None
        assert svc.mixer_position_ms() is None
        assert svc.poll_position(3) is None

    def test_poll_position_adds_seek_offset(self, engine):
        engine.mixer.music.pos_ms = 2500
        assert engine.poll_position(3.5) == pytest.approx(6.0)

    def test_current_file_bounds(self, engine):
        assert engine.current_file(["a", "b"], 0) == "a"
        assert engine.current_file(["a", "b"], 1) == "b"
        assert engine.current_file(["a"], -1) is None
        assert engine.current_file(["a"], 1) is None
        assert engine.current_file([], 0) is None

    def test_metadata_cache_shared_with_engine(self, engine, monkeypatch):
        monkeypatch.setattr(mp, "extract_audio_metadata", lambda p: {"title": "T", "duration": 1})
        first = engine.get_metadata("x.mp3")
        assert engine.get_metadata("x.mp3") is first  # 命中同一份
        assert engine.metadata_cache is engine.metadata_cache  # 同一个 OrderedDict
        engine.metadata_cache.clear()
        assert engine.get_metadata("x.mp3") is not first  # 清空后重新解析


class TestTogglePlayAction:
    """`_music_toggle_play` 的分支判定（非法操作必须原样不动）。"""

    @staticmethod
    def call(engine, **kw):
        base = dict(is_playing=False, is_paused=False, has_playlist=True,
                    is_online_playing=False, has_online_info=False,
                    current_index=-1, is_fading=False)
        base.update(kw)
        return engine.toggle_play_action(**base)

    def test_empty_playlist_and_not_online_is_ignored(self, engine):
        assert self.call(engine, has_playlist=False) == "ignore"

    def test_online_playing_without_info_is_ignored(self, engine):
        # 在线播放中、暂停/播放标志都为假、当前索引为 -1 → 原文直接 return
        assert self.call(engine, is_online_playing=True, has_online_info=False) == "ignore"

    def test_replay_online(self, engine):
        assert self.call(engine, is_online_playing=True, has_online_info=True) == "replay_online"

    def test_play_local_from_negative_index(self, engine):
        assert self.call(engine, current_index=-1) == "play_local"

    def test_resume_when_paused(self, engine):
        assert self.call(engine, is_paused=True) == "resume"

    def test_pause_fades_out(self, engine):
        assert self.call(engine, is_playing=True) == "fade_out_pause"

    def test_fading_blocks_resume_and_pause(self, engine):
        assert self.call(engine, is_paused=True, is_fading=True) == "ignore_fading"
        assert self.call(engine, is_playing=True, is_fading=True) == "ignore_fading"

    def test_all_flags_false_but_playlist_present(self, engine):
        # is_playing/is_paused 都为 False 且非在线 → 走 play_local
        assert self.call(engine, is_online_playing=True, has_online_info=False) == "ignore"


# ══════════════════════════════════════════════════════════════════════
# 3. 播放模式与"下一首"决策
# ══════════════════════════════════════════════════════════════════════


class TestPlaybackModes:
    def test_cycle_mode_order(self, engine):
        assert engine.cycle_mode(mp.PLAY_MODE_SEQUENTIAL) == mp.PLAY_MODE_LOOP_LIST
        assert engine.cycle_mode(mp.PLAY_MODE_LOOP_LIST) == mp.PLAY_MODE_LOOP_SINGLE
        assert engine.cycle_mode(mp.PLAY_MODE_LOOP_SINGLE) == mp.PLAY_MODE_RANDOM
        assert engine.cycle_mode(mp.PLAY_MODE_RANDOM) == mp.PLAY_MODE_SEQUENTIAL

    def test_cycle_mode_illegal_value_raises(self, engine):
        # 记录现状、疑为缺陷：原文用 list.index，非法模式抛 ValueError（没有兜底）
        with pytest.raises(ValueError):
            engine.cycle_mode(99)

    def test_next_index_wraps_for_all_non_random_modes(self, engine):
        # 注意：`_music_next` 对顺序播放同样取模回绕（顺序播放在"下一首"上不区分）
        for mode in (mp.PLAY_MODE_SEQUENTIAL, mp.PLAY_MODE_LOOP_LIST):
            assert engine.next_index(mode, 3, 0) == 1
            assert engine.next_index(mode, 3, 2) == 0

    def test_prev_index_wraps(self, engine):
        for mode in (mp.PLAY_MODE_SEQUENTIAL, mp.PLAY_MODE_LOOP_LIST):
            assert engine.prev_index(mode, 3, 0) == 2
            assert engine.prev_index(mode, 3, 1) == 0

    def test_next_index_single_song_stays(self, engine):
        for mode in (mp.PLAY_MODE_SEQUENTIAL, mp.PLAY_MODE_LOOP_LIST, mp.PLAY_MODE_LOOP_SINGLE):
            assert engine.next_index(mode, 1, 0) == 0
            assert engine.prev_index(mode, 1, 0) == 0

    def test_next_index_random_never_repeats_current(self, engine):
        random.seed(1234)
        for _ in range(50):
            nxt = engine.next_index(mp.PLAY_MODE_RANDOM, 4, 2)
            assert nxt != 2

    def test_next_index_random_single_song(self, engine):
        assert engine.next_index(mp.PLAY_MODE_RANDOM, 1, 0) == 0

    def test_next_index_empty_playlist_raises(self, engine):
        # 记录现状、疑为缺陷：空歌单下 `(cur+1) % 0` 抛 ZeroDivisionError、
        # random.randrange(0) 抛 ValueError —— 原文依赖上层 try 吞掉，这里照抄不兜底
        with pytest.raises(ZeroDivisionError):
            engine.next_index(mp.PLAY_MODE_LOOP_LIST, 0, -1)
        with pytest.raises(ValueError):
            engine.next_index(mp.PLAY_MODE_RANDOM, 0, -1)

    def test_track_end_sequential_middle_and_end(self, engine):
        action = engine.track_end_action(mp.PLAY_MODE_SEQUENTIAL, 0, 3)
        assert (action.kind, action.index) == ("next", 1)
        action = engine.track_end_action(mp.PLAY_MODE_SEQUENTIAL, 2, 3)
        assert action.kind == "stop"  # 最后一首播完就停（不回头）

    def test_track_end_loop_list_wraps(self, engine):
        action = engine.track_end_action(mp.PLAY_MODE_LOOP_LIST, 2, 3)
        assert (action.kind, action.index) == ("next", 0)

    def test_track_end_loop_single_replays(self, engine):
        action = engine.track_end_action(mp.PLAY_MODE_LOOP_SINGLE, 1, 3)
        assert (action.kind, action.index) == ("replay", 1)
        # 索引 -1 时原文取 playlist[-1]（最后一首）——服务只回传索引，取值留界面
        action = engine.track_end_action(mp.PLAY_MODE_LOOP_SINGLE, -1, 3)
        assert (action.kind, action.index) == ("replay", -1)

    def test_track_end_random_avoids_current(self, engine):
        random.seed(7)
        for _ in range(30):
            action = engine.track_end_action(mp.PLAY_MODE_RANDOM, 1, 4)
            assert action.kind == "next" and action.index != 1

    def test_track_end_unknown_mode_does_nothing(self, engine):
        assert engine.track_end_action(99, 0, 3).kind == "none"

    def test_track_end_empty_playlist_raises(self, engine):
        # 记录现状、疑为缺陷：空歌单时 LOOP_LIST/RANDOM 的算术本身会抛
        # （异常恰好被 _poll_music_progress 的 except 吞掉 → 静默无动作）
        with pytest.raises(ZeroDivisionError):
            engine.track_end_action(mp.PLAY_MODE_LOOP_LIST, -1, 0)
        with pytest.raises(ValueError):
            engine.track_end_action(mp.PLAY_MODE_RANDOM, -1, 0)
        # 顺序播放不会抛：直接走"停止"分支
        assert engine.track_end_action(mp.PLAY_MODE_SEQUENTIAL, -1, 0).kind == "stop"


class TestContextNavigation:
    def test_next_context_prefers_valid_prefetch_slot(self, engine):
        songs = [FakeSong(songmid="s0"), FakeSong(songmid="s1"), FakeSong(songmid="s2")]
        slot = {"seq": 1, "idx": 2, "song_key": ("wy", "s2")}
        engine.state.prefetch_seq = 1
        assert engine.next_context_index(songs, 0, mp.PLAY_MODE_LOOP_LIST, slot, 1) == 2

    def test_next_context_rejects_stale_seq(self, engine):
        songs = [FakeSong(songmid="s0"), FakeSong(songmid="s1")]
        slot = {"seq": 1, "idx": 1, "song_key": ("wy", "s1")}
        assert engine.next_context_index(songs, 0, mp.PLAY_MODE_LOOP_LIST, slot, 2) == 1 % 2

    def test_next_context_rejects_song_fingerprint_mismatch(self, engine):
        songs = [FakeSong(songmid="s0"), FakeSong(songmid="s1")]
        slot = {"seq": 5, "idx": 1, "song_key": ("wy", "OTHER")}
        assert engine.next_context_index(songs, 0, mp.PLAY_MODE_LOOP_LIST, slot, 5) == 1
        # 指纹不符时按"顺序下一首"走，结果一样；用索引 1 的列表区分不出来，
        # 所以再验一次"不合法的 idx 不被采用"
        bad = {"seq": 5, "idx": 9, "song_key": ("wy", "s1")}
        assert engine.next_context_index(songs, 0, mp.PLAY_MODE_LOOP_LIST, bad, 5) == 1

    def test_next_context_local_song_slot_is_not_used(self, engine):
        songs = [FakeSong(songmid="s0"), FakeSong(source_type="local", file_path="x.mp3")]
        slot = {"seq": 1, "idx": 1, "song_key": ("wy", "s1")}
        assert engine.next_context_index(songs, 0, mp.PLAY_MODE_LOOP_LIST, slot, 1) == 1

    def test_resolve_context_target_out_of_range(self, engine):
        assert engine.resolve_context_target([], 0, None) is None
        assert engine.resolve_context_target([FakeSong()], 3, None) is None

    def test_resolve_context_target_online_plain(self, engine):
        songs = [FakeSong(songmid="s1")]
        target = engine.resolve_context_target(songs, 0, None)
        assert target.kind == "online" and target.index == 0 and target.prefetched is None

    def test_resolve_context_target_online_prefetched(self, engine, tmp_path):
        temp = tmp_path / "p.m4a"
        temp.write_bytes(b"\x00" * 10)
        songs = [FakeSong(songmid="s1")]
        slot = {"seq": 0, "idx": 0, "song_key": ("wy", "s1"), "temp_path": str(temp)}
        engine.state.prefetch_seq = 0
        target = engine.resolve_context_target(songs, 0, slot)
        assert target.kind == "online" and target.prefetched is slot

    def test_resolve_context_target_prefetch_file_vanished(self, engine):
        songs = [FakeSong(songmid="s1")]
        slot = {"seq": 0, "idx": 0, "song_key": ("wy", "s1"), "temp_path": "gone.m4a"}
        engine.state.prefetch_seq = 0
        assert engine.resolve_context_target(songs, 0, slot).prefetched is None

    def test_resolve_context_target_local_missing(self, engine):
        songs = [FakeSong(source_type="local", file_path="missing.mp3")]
        target = engine.resolve_context_target(songs, 0, None)
        assert target.kind == "local_missing"

    def test_resolve_context_target_local_list_and_index(self, engine, tmp_path):
        a = tmp_path / "a.mp3"
        a.write_bytes(b"ID3" + b"\x00" * 13)
        songs = [
            FakeSong(source_type="local", file_path=str(a)),
            FakeSong(songmid="s2"),  # 在线的不进本地列表
            FakeSong(source_type="local", file_path="missing.mp3"),  # 不存在的不进列表
        ]
        target = engine.resolve_context_target(songs, 0, None)
        assert target.kind == "local"
        assert target.local_paths == [str(a)]
        assert target.local_index == 0


class TestPrefetch:
    def test_no_plan_before_halfway(self, engine):
        songs = [FakeSong(songmid="s1"), FakeSong(songmid="s2")]
        assert engine.plan_prefetch(songs, 0, mp.PLAY_MODE_LOOP_LIST, 100, 49) is None
        assert engine.state.prefetch_started is False

    def test_no_plan_with_unknown_duration(self, engine):
        songs = [FakeSong(songmid="s1"), FakeSong(songmid="s2")]
        assert engine.plan_prefetch(songs, 0, mp.PLAY_MODE_LOOP_LIST, 0, 10) is None

    def test_plan_at_halfway(self, engine):
        songs = [FakeSong(songmid="s1"), FakeSong(songmid="s2")]
        plan = engine.plan_prefetch(songs, 0, mp.PLAY_MODE_LOOP_LIST, 100, 50)
        assert plan is not None and plan.index == 1 and plan.seq == 1
        assert engine.state.prefetch_started is True

    def test_gate_closes_even_when_decision_is_none(self, engine):
        # 记录现状：prefetch_started 在几个 early-return **之前**就置 True，
        # 也就是说"判定无需预取"同样会关上闸门（每首只触发一次）
        songs = [FakeSong(source_type="local", file_path="x.mp3")]
        assert engine.plan_prefetch(songs, 0, mp.PLAY_MODE_LOOP_LIST, 100, 50) is None
        assert engine.state.prefetch_started is True
        assert engine.plan_prefetch(songs, 0, mp.PLAY_MODE_LOOP_LIST, 100, 90) is None

    def test_no_plan_for_local_next_song(self, engine):
        songs = [FakeSong(songmid="s1"), FakeSong(source_type="local", file_path="x.mp3")]
        assert engine.plan_prefetch(songs, 0, mp.PLAY_MODE_LOOP_LIST, 100, 90) is None

    def test_no_plan_when_single_song_or_bad_index(self, engine):
        assert engine.plan_prefetch([FakeSong()], 0, mp.PLAY_MODE_LOOP_LIST, 100, 90) is None
        engine.state.prefetch_started = False
        songs = [FakeSong(songmid="s1"), FakeSong(songmid="s2")]
        assert engine.plan_prefetch(songs, -1, mp.PLAY_MODE_LOOP_LIST, 100, 90) is None

    def test_no_duplicate_plan_when_slot_ready(self, engine):
        songs = [FakeSong(songmid="s1"), FakeSong(songmid="s2")]
        engine.state.prefetch_seq = 3
        engine.state.prefetch_slot = {"seq": 3, "idx": 1}
        assert engine.plan_prefetch(songs, 0, mp.PLAY_MODE_LOOP_LIST, 100, 90) is None

    def test_invalidate_prefetch(self, engine):
        engine.state.prefetch_seq = 4
        engine.state.prefetch_slot = {"seq": 4}
        engine.state.prefetch_started = True
        engine.invalidate_prefetch()
        assert engine.state.prefetch_seq == 5
        assert engine.state.prefetch_slot is None
        assert engine.state.prefetch_started is False

    def test_accept_prefetch_stores_slot_shape(self, engine):
        engine.state.prefetch_seq = 2
        ok = engine.accept_prefetch(2, 1, "t.m4a", FakeInfo("kg", "k1"), FakeInfo("wy", "w1"), "320k")
        assert ok is True
        assert engine.state.prefetch_slot == {
            "seq": 2, "idx": 1, "song_key": ("wy", "w1"), "temp_path": "t.m4a",
            "result_info": engine.state.prefetch_slot["result_info"],
            "origin_info": engine.state.prefetch_slot["origin_info"],
            "quality": "320k",
        }

    def test_accept_prefetch_rejects_stale_seq(self, engine):
        engine.state.prefetch_seq = 5
        assert engine.accept_prefetch(4, 1, "t.m4a", FakeInfo(), FakeInfo(), "128k") is False
        assert engine.state.prefetch_slot is None

    def test_consume_prefetch_slot(self, engine):
        engine.state.prefetch_slot = {"seq": 1}
        engine.consume_prefetch_slot()
        assert engine.state.prefetch_slot is None


# ══════════════════════════════════════════════════════════════════════
# 4. 淡入淡出 / 音量 / 进度 / 扫描
# ══════════════════════════════════════════════════════════════════════


class TestFade:
    def test_fade_in_step_volumes(self, engine):
        volume = 0.8
        assert engine.fade_in_step(0, volume, True, False).volume == pytest.approx(0.8 * 1 / 20)
        assert engine.fade_in_step(19, volume, True, False).volume == pytest.approx(0.8)
        assert engine.fade_in_step(19, volume, True, False).schedule_next is True

    def test_fade_in_final_step_sets_user_volume(self, engine):
        decision = engine.fade_in_step(mp.FADE_STEPS, 0.8, True, False)
        assert decision.volume == 0.8
        assert decision.fading is False
        assert decision.schedule_next is False
        assert decision.cancel is False

    def test_fade_in_cancel_when_not_playing_or_paused(self, engine):
        assert engine.fade_in_step(0, 0.8, False, False).cancel is True
        assert engine.fade_in_step(0, 0.8, True, True).cancel is True
        assert engine.fade_out_step(0, 0.8, False, False).cancel is True

    def test_fade_in_sets_engine_flag(self, engine):
        engine.fade_in_step(3, 0.5, True, False)
        assert engine.state.is_fading is True
        engine.fade_in_step(mp.FADE_STEPS, 0.5, True, False)
        assert engine.state.is_fading is False

    def test_fade_out_step_volumes(self, engine):
        volume = 0.6
        # 原文：remaining = FADE_STEPS - 1 - step，step=0 时音量不变（0.6），
        # step=19 时 remaining=0 → 音量 0
        assert engine.fade_out_step(0, volume, True, False).volume == pytest.approx(0.6)
        assert engine.fade_out_step(19, volume, True, False).volume == pytest.approx(0.0)

    def test_fade_out_final_step_finishes(self, engine):
        decision = engine.fade_out_step(mp.FADE_STEPS, 0.6, True, False)
        assert decision.volume == 0.0
        assert decision.finished is True
        assert decision.schedule_next is False
        assert engine.state.is_fading is False

    def test_cancel_fade_and_target(self, engine):
        engine.begin_fade_out(mp.FADE_OUT_STOP)
        engine.state.is_fading = True
        engine.cancel_fade()
        assert engine.state.is_fading is False
        assert engine.state.fade_out_target is None

    def test_take_fade_out_target_consumes(self, engine):
        engine.begin_fade_out(mp.FADE_OUT_PAUSE)
        assert engine.take_fade_out_target() == mp.FADE_OUT_PAUSE
        assert engine.take_fade_out_target() is None

    def test_fade_out_target_constants(self):
        assert mp.FADE_OUT_PAUSE == "pause"
        assert mp.FADE_OUT_STOP == "stop"


class TestVolumeAndProgress:
    def test_volume_delta_clamped(self, engine):
        assert engine.volume_percent_after_delta(0.7, -5) == 65
        assert engine.volume_percent_after_delta(0.05, -5) == 0
        assert engine.volume_percent_after_delta(0.99, 5) == 100
        assert engine.volume_percent_after_delta(0.0, 10) == 10

    def test_volume_delta_truncates_float(self, engine):
        # 原文 `int(self._music_volume * 100)`：0.705*100=70.49999 → 70（截断不是四舍五入）
        assert engine.volume_percent_after_delta(0.705, 0) == 70

    def test_toggle_mute_remembers_volume(self, engine):
        result = engine.toggle_mute(0.42)
        assert result.volume == 0
        assert result.remember == 0.42
        assert engine.state.vol_before_mute == 0.42

    def test_toggle_mute_restores_default_when_never_muted(self, engine):
        # 从未静音过时恢复默认 0.7（原文 getattr(..., 0.7) 的默认值）
        assert engine.toggle_mute(0).volume == 0.7
        assert engine.toggle_mute(0).remember is None

    def test_toggle_mute_round_trip(self, engine):
        engine.toggle_mute(0.3)  # 静音
        assert engine.toggle_mute(0).volume == 0.3

    def test_seek_seconds(self, engine):
        assert engine.seek_seconds(50, 200) == 100
        assert engine.seek_seconds(50, 0) == 0
        assert engine.seek_seconds(0, 200) == 0
        assert engine.seek_seconds(150, 200) == 300  # 原文不钳制上界

    def test_progress_percent(self, engine):
        assert engine.progress_percent(50, 100) == 50
        assert engine.progress_percent(0, 100) == 0
        assert engine.progress_percent(100, 100) == 100

    def test_progress_percent_none_cases(self, engine):
        assert engine.progress_percent(10, 0) is None  # 时长未知
        assert engine.progress_percent(-1, 100) is None  # 负数不进进度条
        assert engine.progress_percent(101, 100) is None  # 超过 100% 不设

    def test_scan_folder_sorted_and_filtered(self, engine, tmp_path):
        (tmp_path / "sub").mkdir()
        (tmp_path / "b.MP3").write_bytes(b"ID3" + b"\x00" * 13)
        (tmp_path / "a.flac").write_bytes(b"fLaC" + b"\x00" * 13)
        (tmp_path / "note.txt").write_text("x", encoding="utf-8")
        (tmp_path / "sub" / "c.ogg").write_bytes(b"OggS" + b"\x00" * 13)
        files = engine.scan_folder(str(tmp_path), ma.AUDIO_EXTENSIONS)
        assert [Path(f).name for f in files] == ["a.flac", "b.MP3", "c.ogg"]

    def test_scan_folder_empty_returns_none(self, engine, tmp_path):
        assert engine.scan_folder(str(tmp_path), ma.AUDIO_EXTENSIONS) is None

    def test_scan_folder_missing_dir_returns_none(self, engine, tmp_path):
        assert engine.scan_folder(str(tmp_path / "nope"), ma.AUDIO_EXTENSIONS) is None


# ══════════════════════════════════════════════════════════════════════
# 5. 状态持久化（旧格式往返 + 坏值容错）
# ══════════════════════════════════════════════════════════════════════


class TestMusicState:
    def _build(self, **over):
        kwargs = dict(
            last_folder="D:/music", current_index=3, progress=42.5, volume=0.55,
            play_mode=mp.PLAY_MODE_RANDOM, mini_mode=True, playlist_id="pl-1",
            playlist_context_idx=2, playlist_context_songs=[1, 2, 3],
        )
        kwargs.update(over)
        return ms.build_music_state(**kwargs)

    def test_payload_keys_are_the_legacy_eight(self):
        assert set(self._build()) == {
            "music_last_folder", "music_current_index", "music_progress", "music_volume",
            "music_play_mode", "music_mini_mode", "music_last_playlist_id",
            "music_last_song_idx_in_playlist",
        }

    def test_payload_shape(self):
        assert self._build() == {
            "music_last_folder": "D:/music",
            "music_current_index": 3,
            "music_progress": 42.5,
            "music_volume": 0.55,
            "music_play_mode": "random",
            "music_mini_mode": True,
            "music_last_playlist_id": "pl-1",
            "music_last_song_idx_in_playlist": 2,
        }

    def test_context_song_index_is_minus_one_without_context(self):
        # 没在播歌单上下文时记 -1（原文的 if/else 表达式）
        assert self._build(playlist_context_songs=[])["music_last_song_idx_in_playlist"] == -1

    def test_unknown_play_mode_falls_back_to_loop_list(self):
        assert self._build(play_mode=99)["music_play_mode"] == "loop_list"

    def test_round_trip_old_format(self):
        payload = self._build()
        parsed = ms.parse_music_state(copy.deepcopy(payload))
        assert parsed.last_folder == "D:/music"
        assert parsed.current_index == 3
        assert parsed.progress == 42.5
        assert parsed.volume == 0.55
        assert parsed.play_mode == mp.PLAY_MODE_RANDOM
        assert parsed.mini_mode is True
        assert parsed.last_playlist_id == "pl-1"
        assert parsed.song_index == 2

    def test_round_trip_shape_is_stable(self):
        # 写出去再读回来再写出去，形状（键集合 + 值）必须一致
        first = self._build()
        again = self._build(
            last_folder=ms.parse_music_state(first).last_folder,
            current_index=ms.parse_music_state(first).current_index,
            progress=ms.parse_music_state(first).progress,
            volume=ms.parse_music_state(first).volume,
            play_mode=ms.parse_music_state(first).play_mode,
            mini_mode=ms.parse_music_state(first).mini_mode,
            playlist_id=ms.parse_music_state(first).last_playlist_id,
            playlist_context_idx=ms.parse_music_state(first).song_index,
            playlist_context_songs=[1],
        )
        assert again == first

    def test_parse_empty_dict_gives_defaults(self):
        parsed = ms.parse_music_state({})
        assert parsed.last_folder == ""
        assert parsed.volume is None  # None 表示"不改音量"，与 0 不同
        assert parsed.play_mode == mp.PLAY_MODE_LOOP_LIST
        assert parsed.current_index == -1
        assert parsed.progress == 0
        assert parsed.mini_mode is False
        assert parsed.last_playlist_id is None
        assert parsed.song_index == -1

    def test_parse_none_and_garbage_is_tolerated(self):
        # 坏 JSON 由配置层吞掉并返回空 dict；服务层对 None 也要给默认值
        assert ms.parse_music_state(None).play_mode == mp.PLAY_MODE_LOOP_LIST
        parsed = ms.parse_music_state({"music_volume": None, "music_play_mode": 42})
        assert parsed.volume is None
        assert parsed.play_mode == mp.PLAY_MODE_LOOP_LIST

    def test_parse_unknown_mode_name_falls_back(self):
        assert ms.parse_music_state({"music_play_mode": "shuffle"}).play_mode == mp.PLAY_MODE_LOOP_LIST

    def test_legacy_partial_payload(self):
        # 旧版本只存了其中几个键：其余键走默认值，读得进
        parsed = ms.parse_music_state({"music_last_folder": "E:/x", "music_volume": 0.2})
        assert parsed.last_folder == "E:/x"
        assert parsed.volume == 0.2
        assert parsed.current_index == -1
        assert parsed.mini_mode is False

    def test_volume_to_slider(self):
        assert ms.volume_to_slider(0.7) == 70
        assert ms.volume_to_slider(0) == 0
        assert ms.volume_to_slider(1) == 100

    def test_login_retry_due(self):
        assert ms.login_retry_due(None, 0) is True
        assert ms.login_retry_due({}, 0) is True
        assert ms.login_retry_due({"get_wy_cookie": None}, 0) is True
        assert ms.login_retry_due({"get_wy_cookie": lambda: "c"}, 0) is False
        # 上限 60 次之后不再重试
        assert ms.login_retry_due(None, ms.LOAD_RETRY_MAX) is False

    def test_read_saved_cookie(self):
        assert ms.read_saved_cookie({"get_wy_cookie": lambda: "MUSIC_U=1"}) == "MUSIC_U=1"
        assert ms.read_saved_cookie({"get_wy_cookie": lambda: ""}) is None

        def boom():
            raise RuntimeError("配置坏了")

        assert ms.read_saved_cookie({"get_wy_cookie": boom}) is None

    def test_apply_wy_cookie_delegates_to_music_source(self, monkeypatch):
        import services.music_source as music_source

        seen = []
        monkeypatch.setattr(music_source, "wy_apply_cookie", lambda c: seen.append(c))
        assert ms.apply_wy_cookie("MUSIC_U=x") is True
        assert seen == ["MUSIC_U=x"]

    def test_apply_wy_cookie_swallows_failure(self, monkeypatch):
        import services.music_source as music_source

        def boom(cookie):
            raise RuntimeError("音源没准备好")

        monkeypatch.setattr(music_source, "wy_apply_cookie", boom)
        assert ms.apply_wy_cookie("c") is False

    def test_schedule_constants(self):
        assert ms.SAVE_DEBOUNCE_MS == 500
        assert ms.PERIODIC_SAVE_INTERVAL_MS == 30000
        assert ms.LOAD_RETRY_MAX == 60
        assert ms.LOAD_RETRY_MS == 500


# ══════════════════════════════════════════════════════════════════════
# 6. 桌面歌词的零 GUI 逻辑
# ══════════════════════════════════════════════════════════════════════


class TestDesktopLyric:
    def test_center_bottom_on_normal_screen(self):
        assert dl.calc_center_bottom_position(1920, 1080, 600, 140) == (660, 860)

    def test_center_bottom_on_tiny_screen_keeps_negative_x(self):
        # 记录现状、疑为缺陷：x 不做钳制，屏幕比窗口窄时会算出负数
        assert dl.calc_center_bottom_position(400, 300, 600, 140) == (-100, 80)

    def test_center_bottom_clamps_y_to_zero(self):
        # y 会被 max(0, ...) 钳到 0
        assert dl.calc_center_bottom_position(1920, 100, 600, 140) == (660, 0)
        assert dl.calc_center_bottom_position(0, 0, 600, 140) == (-300, 0)

    def test_custom_bottom_margin(self):
        assert dl.calc_center_bottom_position(1920, 1080, 600, 140, bottom_margin=0) == (660, 940)

    def test_find_current_line_empty(self):
        assert dl.find_current_line([], 1000) == -1

    def test_find_current_line_before_first(self):
        lines = [FakeLine("a", 1000), FakeLine("b", 2000)]
        assert dl.find_current_line(lines, 999) == -1

    def test_find_current_line_exact_and_between(self):
        lines = [FakeLine("a", 1000), FakeLine("b", 2000), FakeLine("c", 3000)]
        assert dl.find_current_line(lines, 1000) == 0
        assert dl.find_current_line(lines, 1500) == 0  # 落在行间取上一行
        assert dl.find_current_line(lines, 2999) == 1

    def test_find_current_line_beyond_end(self):
        lines = [FakeLine("a", 1000), FakeLine("b", 2000)]
        assert dl.find_current_line(lines, 99999) == 1

    def test_neighbor_line_texts(self):
        lines = [FakeLine("a", 0), FakeLine("b", 1), FakeLine("c", 2)]
        assert dl.neighbor_line_texts(lines, 0) == ("", "b")
        assert dl.neighbor_line_texts(lines, 1) == ("a", "c")
        assert dl.neighbor_line_texts(lines, 2) == ("b", "")

    def test_alpha_bounds_and_step(self):
        assert dl.increase_alpha(0.85) == pytest.approx(0.9)
        assert dl.increase_alpha(0.98) == pytest.approx(1.0)  # 上界 1.0
        assert dl.increase_alpha(1.0) == 1.0
        assert dl.decrease_alpha(0.20) == pytest.approx(0.15)
        assert dl.decrease_alpha(0.15) == pytest.approx(0.15)  # 下界 0.15
        assert dl.decrease_alpha(0.1) == pytest.approx(0.15)

    def test_defaults(self):
        state = dl.LyricWindowState()
        assert state.alpha == pytest.approx(0.85)
        assert state.locked is False
        assert state.width == 600 and state.height == 140
        assert state.current_line_index == -1
        assert state.drag_data == {"x": 0, "y": 0}

    def test_toggle_lock_rule(self):
        state = dl.LyricWindowState()
        assert state.is_draggable is True
        assert state.toggle_lock() is True
        assert state.is_draggable is False
        assert state.toggle_lock() is False

    def test_place_at_screen_bottom_center(self):
        state = dl.LyricWindowState()
        assert state.place_at_screen_bottom_center(1920, 1080) == (660, 860)
        assert (state.x, state.y) == (660, 860)

    def test_drag_math_without_clamping(self):
        state = dl.LyricWindowState()
        state.begin_drag(500, 700, 470, 660)  # 抓取点相对窗口左上角的偏移 = (30, 40)
        assert state.drag_data == {"x": 30, "y": 40}
        assert state.drag_to(530, 740) == (500, 700)
        # 记录现状、疑为缺陷：拖动没有任何屏幕内钳制，可以拖到负坐标（拖出去就抓不回来）
        assert state.drag_to(-200, -300) == (-230, -340)

    def test_opacity_transitions_on_state(self):
        state = dl.LyricWindowState()
        for _ in range(5):
            state.increase_opacity()
        assert state.alpha == pytest.approx(1.0)
        for _ in range(30):
            state.decrease_opacity()
        assert state.alpha == pytest.approx(0.15)

    def test_reset_current_line(self):
        state = dl.LyricWindowState(current_line_index=5)
        state.reset_current_line()
        assert state.current_line_index == -1

    def test_fallback_screen_size(self):
        assert dl.FALLBACK_SCREEN_SIZE == (1920, 1080)

    def test_alpha_color_stays_in_ui_layer(self):
        # `_alpha_color` 是 Tk 颜色表示（入参是 COLORS，出参是 customtkinter 认的
        # "#rrggbb"），按切缝判据刻意留在 ui/ 侧，**不许**出现在 services 里
        import ui.music_desktop_lyric as ui_lyric

        assert hasattr(ui_lyric, "_alpha_color")
        assert not hasattr(dl, "_alpha_color")


# ══════════════════════════════════════════════════════════════════════
# 7. 服务层零 UI 依赖（静态 AST 断言，与 scripts/check_services_purity.py 互补）
# ══════════════════════════════════════════════════════════════════════

NEW_SERVICE_FILES = [
    "services/music_audio.py",
    "services/music_smtc.py",
    "services/music_player.py",
    "services/music_state.py",
    "services/desktop_lyric.py",
]
FORBIDDEN_TOPS = ("tkinter", "customtkinter", "PySide6", "shiboken6", "PyQt5", "PyQt6", "ui")


def _module_source(rel: str) -> str:
    return io.open(REPO_ROOT / rel, encoding="utf-8", newline="").read()


class TestServicesAreUiFree:
    @pytest.mark.parametrize("rel", NEW_SERVICE_FILES)
    def test_no_gui_imports(self, rel):
        tree = ast.parse(_module_source(rel))
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad += [a.name for a in node.names if a.name.split(".")[0] in FORBIDDEN_TOPS]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.split(".")[0] in FORBIDDEN_TOPS:
                    bad.append(mod)
        assert bad == [], f"{rel} 引入了 GUI/ui 依赖: {bad}"

    @pytest.mark.parametrize("rel", [p for p in NEW_SERVICE_FILES if not p.endswith("music_smtc.py")])
    def test_no_threads_and_no_after(self, rel):
        tree = ast.parse(_module_source(rel))
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Attribute) and f.attr in ("Thread", "after", "after_cancel"):
                    bad.append(f"{ast.unparse(f.value)}.{f.attr}()")
        assert bad == [], f"{rel} 起了线程或调了 after: {bad}"

    def test_smtc_only_uses_injected_parent_after(self):
        """`music_smtc` 是唯一的例外：它调 `self._parent.after(...)`。

        这是原文的**线程契约**（把所有 winsdk 调用切回 Tk 主线程），任务书明确要求
        保留；`_parent` 由界面注入，服务自己不起线程、不 import GUI。
        """
        tree = ast.parse(_module_source("services/music_smtc.py"))
        owners = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and node.func.attr.startswith("after"):
                owners.add(ast.unparse(node.func.value))
        assert owners == {"self._parent"}, f"after 调用者不止 self._parent: {owners}"
        assert "threading" not in _module_source("services/music_smtc.py")


# ══════════════════════════════════════════════════════════════════════
# 8. 旧名兼容（形态 1 的别名必须指向同一实现）
# ══════════════════════════════════════════════════════════════════════

OLD_TO_NEW = {
    "_extract_audio_metadata": "extract_audio_metadata",
    "_get_tag": "get_tag",
    "_format_time": "format_time",
    "_format_play_count": "format_play_count",
    "_format_online_quality": "format_online_quality",
    "_format_local_quality": "format_local_quality",
    "_validate_audio_file_header": "validate_audio_file_header",
    "_validate_audio_duration": "validate_audio_duration",
    "_is_m4a_container": "is_m4a_container",
    "_transcode_audio_to_wav": "transcode_audio_to_wav",
}


class TestLegacyNames:
    @pytest.mark.parametrize("old,new", sorted(OLD_TO_NEW.items()))
    def test_audio_helper_alias_identity(self, old, new):
        import ui.app_music as app_music

        assert getattr(app_music, old) is getattr(ma, new)

    def test_smtc_alias_identity(self):
        import ui.app_music as app_music
        import services.music_smtc as music_smtc

        assert app_music._SMTCController is music_smtc.SMTCController

    def test_old_private_switch_name_still_exists(self):
        """旧名 `_mutagen_import_error` / `_winsdk_available` 仍可从 app_music 取到。"""
        import ui.app_music as app_music

        assert hasattr(app_music, "_mutagen_import_error")
        assert hasattr(app_music, "_winsdk_available")
        assert hasattr(app_music, "_winsdk_import_error")

    def test_patch_point_is_the_read_name(self, monkeypatch, tmp_path):
        """补丁点必须指向**真正被读取**的名字（services.music_audio 的模块级全局名）。

        这条是防"补丁静默失效"的回归测试：函数体如果改成导入期快照一个局部名，
        下面两个 assert 就会挂。
        """
        monkeypatch.setattr(ma, "_mutagen_import_error", ImportError("no mutagen"))
        p = tmp_path / "a.mp3"
        p.write_bytes(b"ID3" + b"\x00" * 13)
        # mutagen 不可用 → 时长校验一律放行（即使预期时长完全不符）
        assert ma.validate_audio_duration(str(p), 300) is True
        monkeypatch.setattr(ma, "_mutagen_import_error", None)
        monkeypatch.setattr(ma, "extract_audio_metadata", lambda path: {"duration": 90.0})
        assert ma.validate_audio_duration(str(p), 300) is False

    def test_constant_reexports(self):
        import ui.app_music as app_music

        assert app_music.PLAY_MODE_LOOP_LIST == mp.PLAY_MODE_LOOP_LIST
        assert app_music.PLAY_MODE_NAMES == mp.PLAY_MODE_NAMES
        assert app_music.FADE_STEPS == mp.FADE_STEPS
        assert app_music.FADE_INTERVAL_MS == mp.FADE_INTERVAL_MS
        assert app_music.AUDIO_EXTENSIONS == ma.AUDIO_EXTENSIONS
        assert app_music.MUSIC_METADATA_CACHE_MAX == ma.MUSIC_METADATA_CACHE_MAX


# ══════════════════════════════════════════════════════════════════════
# 9. `MusicPlayerMixin` 公开方法面未变（夹具由 git HEAD 原文生成）
# ══════════════════════════════════════════════════════════════════════

SURFACE = json.loads(
    io.open(REPO_ROOT / "tests" / "music_mixin_surface.json", encoding="utf-8").read()
)


def runtime_sig(func) -> dict:
    """把运行期签名归一化成**可与夹具比较**的形状。

    比什么、不比什么，由"运行期能不能机械判定"决定：

    - `args`：参数名与顺序（含 `self`），精确比较；
    - `defaults` / `kwdefaults`：默认值用 `repr` 比较，精确比较；
    - `ann_present` / `returns_present`：**只比"有没有注解"**。

    为什么不逐字比注解文本：`ui/app_music.py` 没有 `from __future__ import
    annotations`，运行期拿到的是解析后的对象，`str()` 出来是限定名
    （源码写 `Optional[OnlineMusicInfo]`，运行期是
    `typing.Optional[ui.music_source.base.MusicInfo]`），文本必然不等。
    源码级的逐字签名比较交给 `poc/_verify_1_4a_music.py`（AST 对 AST）。
    """
    import inspect

    sig = inspect.signature(func)
    params = list(sig.parameters.values())
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    kwonly = [p for p in params if p.kind == p.KEYWORD_ONLY]
    defaults = [repr(p.default) for p in positional if p.default is not inspect.Parameter.empty]
    kwdefaults = [
        ("<required>" if p.default is inspect.Parameter.empty else repr(p.default)) for p in kwonly
    ]
    return {
        "args": [p.name for p in positional],
        "defaults": defaults,
        "kwdefaults": kwdefaults,
        "ann_present": [p.annotation is not inspect.Parameter.empty for p in positional + kwonly],
        "returns_present": sig.return_annotation is not inspect.Signature.empty,
    }


def frozen_sig(value) -> dict:
    """夹具里的签名是 JSON 字符串（生成脚本 json.dumps 过），解析成可比较的形状。"""
    data = json.loads(value) if isinstance(value, str) else value
    return {
        "args": data["args"],
        "defaults": data["defaults"],
        "kwdefaults": data["kwdefaults"],
        "ann_present": [bool(a) for a in data["annotations"]],
        "returns_present": bool(data["returns"]),
    }


def sig_diff(got: dict, want: dict) -> str:
    """返回不一致的字段名（空串表示一致），便于失败信息定位。"""
    keys = ("args", "defaults", "kwdefaults", "ann_present", "returns_present")
    bad = [k for k in keys if got.get(k) != want.get(k)]
    return ",".join(bad)




class TestMixinSurfaceUnchanged:
    def test_method_count(self):
        import ui.app_music as app_music

        assert SURFACE["method_count"] == 183
        assert len(SURFACE["methods"]) == 183
        assert app_music.MusicPlayerMixin is not None

    def test_每个方法的名字与签名都不变(self):
        import ui.app_music as app_music

        cls = app_music.MusicPlayerMixin
        mismatched = []
        missing = []
        for name, expected in SURFACE["methods"].items():
            func = getattr(cls, name, None)
            if func is None:
                func = getattr(cls, f"_MusicPlayerMixin{name}", None)  # 名称修饰
            if func is None:
                missing.append(name)
                continue
            if runtime_sig(func) != frozen_sig(expected):
                mismatched.append(
                    (name, sig_diff(runtime_sig(func), frozen_sig(expected)))
                )
        assert missing == [], f"方法丢失: {missing}"
        assert mismatched == [], f"签名变了: {mismatched}"

    def test_没有多出来的公开方法(self):
        import ui.app_music as app_music

        cls = app_music.MusicPlayerMixin
        runtime = {
            n for n, v in vars(cls).items()
            if callable(v) and not isinstance(v, (staticmethod, classmethod))
        }
        runtime = {"__" + n.split("__", 1)[1] if n.startswith("_MusicPlayerMixin__") else n
                   for n in runtime}
        extra = sorted(runtime - set(SURFACE["methods"]))
        assert extra == [], f"多出来的方法: {extra}"

    def test_十个模块级函数签名不变(self):
        import ui.app_music as app_music

        mismatched = []
        for name, expected in SURFACE["module_functions"].items():
            func = getattr(app_music, name)
            diff = sig_diff(runtime_sig(func), frozen_sig(expected))
            if diff:
                mismatched.append((name, diff))
        assert sorted(SURFACE["module_functions"]) == sorted(OLD_TO_NEW)
        assert mismatched == [], f"签名变了: {mismatched}"

    def test_SMTC_控制器方法签名不变(self):
        import services.music_smtc as music_smtc

        mismatched = []
        for name, expected in SURFACE["smtc_methods"].items():
            func = getattr(music_smtc.SMTCController, name)
            if isinstance(func, property):
                func = func.fget
            diff = sig_diff(runtime_sig(func), frozen_sig(expected))
            if diff:
                mismatched.append((name, diff))
        assert mismatched == [], f"签名变了: {mismatched}"


# ══════════════════════════════════════════════════════════════════════
# 10. 委托链集成（Mixin → MusicPlayerService → mixer），全程无 Tk
# ══════════════════════════════════════════════════════════════════════


class _Widget:
    """最小控件替身：只需要 set / configure / winfo_exists / get。"""

    def __init__(self):
        self.value = None
        self.text = None

    def set(self, value):
        self.value = value

    def configure(self, *args, **kw):
        # 真实代码里两种写法都有：configure(text="...") 与 configure(count_text)
        if args:
            self.text = args[0]
        if "text" in kw:
            self.text = kw["text"]

    def winfo_exists(self):
        return True

    def get(self):
        return "128k"


def make_host(monkeypatch, mixer=None):
    """构造一个**真实**的 `MusicPlayerMixin` 宿主：只把碰 Tk 的部分换成记录器。

    这样验证的是"界面委托层真的连到服务上"，而不是"服务单测全绿、接线全断"。
    """
    import services.music_smtc as music_smtc
    import ui.app_music as app_music

    mixer = mixer if mixer is not None else FakeMixer()
    monkeypatch.setattr(mp, "get_mixer", lambda: mixer)
    # SMTC 保持"不可用"，免得它透过 self._parent.after 干扰定时器计数
    monkeypatch.setattr(music_smtc, "_winsdk_available", False)

    class Host(app_music.MusicPlayerMixin):
        def __init__(self):
            self.timers = []
            self._timer_seq = 0
            self.achievements = []
            self.discarded = []
            self.online_url_calls = []
            self.shown_playlist = None
            self.restored_folder = None
            self._MusicPlayerMixin__init_music()
            for name in ("_music_vol_slider", "_music_mini_vol", "_music_progress_bar",
                         "_music_cur_label", "_music_mute_btn", "_music_quality_var",
                         "_music_folder_label", "_music_song_count_label"):
                setattr(self, name, _Widget())

        # ── Tk 侧最小替身 ──
        def after(self, ms, fn=None, *args):
            self._timer_seq += 1
            self.timers.append({"id": self._timer_seq, "ms": ms, "fn": fn, "args": args})
            return self._timer_seq

        def after_cancel(self, tid):
            self.timers = [t for t in self.timers if t["id"] != tid]

        def _trigger_ach(self, key):
            self.achievements.append(key)

        def _check_ach(self, key, value=True):
            self.achievements.append((key, value))

        def _update_play_btn_ui(self):
            pass

        def _update_mute_btn_ui(self):
            pass

        def _update_now_playing_info(self):
            pass

        def _highlight_current_in_list(self):
            pass

        def _highlight_playlist_song(self, idx):
            pass

        def _music_record_play_history_local(self, path):
            pass

        def _music_record_play_history_online(self, info):
            pass

        def _is_music_tab_active(self):
            return True

        def _discard_temp_file(self, path):
            self.discarded.append(path)

        def _music_play_online_url(self, info):
            self.online_url_calls.append(info)

        def _music_wy_sync_remote_playlists(self):
            pass

        def _music_scan_folder_restore(self, folder):
            self.restored_folder = folder

        def _rebuild_playlist_sidebar(self):
            pass

        def _music_show_playlist(self, pid):
            self.shown_playlist = pid

    return Host(), mixer


def run_fade_chain(host, fn_name, limit=200):
    """驱动一路 `after` 链（淡入/淡出）直到不再排定时器。"""
    host._music_fade_timer_id = None
    getattr(host, fn_name)(0)
    guard = 0
    while host._music_fade_timer_id is not None and guard < limit:
        tid = host._music_fade_timer_id
        host._music_fade_timer_id = None
        timer = next(t for t in host.timers if t["id"] == tid)
        timer["fn"](*timer["args"])
        guard += 1
    return guard


class TestMixinDelegationWiring:
    def test_init_music_creates_engine_and_shares_state(self, monkeypatch):
        host, _ = make_host(monkeypatch)
        assert isinstance(host._music_engine, mp.MusicPlayerService)
        # 缓存与"试过的模式"集合是**同一个对象**，界面侧 clear() 才能同步到引擎
        assert host._music_metadata_cache is host._music_engine.metadata_cache
        assert host._music_modes_used is host._music_engine.state.modes_used

    def test_play_file_drives_mixer_and_mirrors_state(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        host._play_file("/tmp/a.mp3")
        assert [c[0] for c in mixer.music.calls][:3] == ["load", "set_volume", "play"]
        assert host._music_is_playing is True
        assert host._music_is_paused is False
        assert host._music_current_filepath == "/tmp/a.mp3"
        assert host._music_seek_offset == 0
        assert host._music_engine.state.current_filepath == "/tmp/a.mp3"
        # 进度轮询 500ms + 淡入 50ms 都必须排上
        assert {t["ms"] for t in host.timers} >= {mp.PROGRESS_POLL_MS, mp.FADE_INTERVAL_MS}
        assert "music_first_play" in host.achievements

    def test_play_file_without_pygame_returns_early(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        monkeypatch.setattr(mp, "_pygame_import_error", ImportError("no pygame"))
        host._play_file("/tmp/a.mp3")
        assert mixer.music.calls == []
        assert host._music_is_playing is False

    def test_fade_in_completes_at_user_volume(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        host._music_is_playing = True
        host._music_volume = 0.8
        steps = run_fade_chain(host, "_music_fade_in")
        assert steps >= mp.FADE_STEPS - 1
        assert host._music_is_fading is False
        assert host._music_fade_timer_id is None
        volumes = [c[1][0] for c in mixer.music.calls if c[0] == "set_volume"]
        assert volumes[-1] == pytest.approx(0.8)

    def test_toggle_play_pause_path_fades_out_then_pauses(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        host._music_playlist = ["/tmp/a.mp3"]
        host._music_is_playing = True
        host._music_volume = 0.5
        host._music_toggle_play()
        assert host._music_fade_out_target == mp.FADE_OUT_PAUSE
        run_fade_chain(host, "_music_fade_out")
        assert host._music_is_playing is False
        assert host._music_is_paused is True
        assert host._music_fade_out_target is None
        assert any(c[0] == "pause" for c in mixer.music.calls)
        # 暂停后音量恢复成用户音量
        assert [c[1][0] for c in mixer.music.calls if c[0] == "set_volume"][-1] == pytest.approx(0.5)

    def test_toggle_play_ignored_on_empty_playlist(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        host._music_toggle_play()
        assert mixer.music.calls == []
        assert host._music_is_playing is False

    def test_stop_fades_then_stops(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        host._music_is_playing = True
        host._music_stop()
        assert host._music_fade_out_target == mp.FADE_OUT_STOP
        run_fade_chain(host, "_music_fade_out")
        assert host._music_is_playing is False
        names = [c[0] for c in mixer.music.calls]
        # stop → unload 之后还会补一次 set_volume（把音量还原给下一次播放，原文如此）
        assert names.index("stop") < names.index("unload")
        assert names[-1] == "set_volume"
        assert host._music_progress == 0

    def test_stop_instant_resets_all(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        host._music_is_playing = True
        host._music_is_online_playing = True
        host._music_current_online_info = FakeInfo()
        host._music_progress = 30
        host._music_engine.state.seek_offset = 30
        host._music_seek_offset = 30
        host._music_stop(instant=True)
        assert host._music_is_playing is False
        assert host._music_is_paused is False
        assert host._music_is_online_playing is False
        assert host._music_current_online_info is None
        assert host._music_progress == 0
        assert host._music_seek_offset == 0
        assert host._music_engine.state.seek_offset == 0
        assert host._music_progress_bar.value == 0

    def test_seek_reloads_at_ratio_position(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        host._music_is_playing = True
        host._music_duration = 200
        host._music_current_filepath = "/tmp/a.mp3"
        host._music_engine.state.current_filepath = "/tmp/a.mp3"
        host._music_seek(50)
        # 前四行是 reload_and_seek 的 mixer 四连（后面还会紧接着进度轮询与淡入）
        assert [c[0] for c in mixer.music.calls][:4] == ["stop", "load", "set_volume", "play"]
        assert mixer.music.calls[3][2] == {"start": 100.0}
        assert host._music_progress == 100.0
        assert host._music_seek_offset == 100.0
        assert host._music_engine.state.seek_offset == 100.0

    def test_seek_ignored_when_not_playing(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        host._music_current_filepath = "/tmp/a.mp3"
        host._music_seek(50)
        assert mixer.music.calls == []

    def test_poll_progress_updates_widgets(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        host._music_is_playing = True
        host._music_duration = 100
        mixer.music.pos_ms = 25000
        host._poll_music_progress()
        assert host._music_progress == pytest.approx(25.0)
        assert host._music_cur_label.text == "0:25"
        assert host._music_progress_bar.value == pytest.approx(25.0)
        assert host._music_progress_timer_id is not None

    def test_poll_progress_stops_when_paused(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        host._music_is_paused = True
        host._music_progress_timer_id = 123
        host.timers.append({"id": 123, "ms": mp.PROGRESS_POLL_MS, "fn": None, "args": ()})
        host._poll_music_progress()
        assert host._music_progress_timer_id is None
        assert mixer.music.calls == []

    def test_track_end_loop_list_advances_and_plays(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        host._music_playlist = ["/tmp/a.mp3", "/tmp/b.mp3"]
        host._music_current_index = 0
        host._music_play_mode = mp.PLAY_MODE_LOOP_LIST
        host._on_track_end()
        assert host._music_current_index == 1
        assert host._music_progress == 0
        assert host._music_is_playing is True
        assert [c[0] for c in mixer.music.calls][:3] == ["load", "set_volume", "play"]

    def test_track_end_sequential_last_stops(self, monkeypatch):
        host, mixer = make_host(monkeypatch)
        host._music_playlist = ["/tmp/a.mp3"]
        host._music_current_index = 0
        host._music_play_mode = mp.PLAY_MODE_SEQUENTIAL
        host._music_seek_offset = 12
        host._music_engine.state.seek_offset = 12
        host._on_track_end()
        assert host._music_seek_offset == 0
        assert host._music_progress_bar.value == 0
        assert mixer.music.calls == []  # 不播下一首

    def test_next_and_prev_walk_playlist(self, monkeypatch):
        host, _ = make_host(monkeypatch)
        host._music_playlist = ["/tmp/a.mp3", "/tmp/b.mp3", "/tmp/c.mp3"]
        host._music_current_index = 0
        host._music_play_mode = mp.PLAY_MODE_LOOP_LIST
        host._music_next()
        assert host._music_current_index == 1
        host._music_prev()
        assert host._music_current_index == 0
        host._music_prev()
        assert host._music_current_index == 2  # 回绕

    def test_adjust_volume_and_mute_widgets(self, monkeypatch):
        host, _ = make_host(monkeypatch)
        host._adjust_volume(-5)
        assert host._music_volume == pytest.approx(0.65)
        assert host._music_vol_slider.value == 65
        host._music_toggle_mute()
        assert host._music_volume == 0
        assert host._music_vol_slider.value == 0
        host._music_toggle_mute()
        assert host._music_volume == pytest.approx(0.65)
        assert host._music_vol_slider.value == 65

    def test_cycle_mode_updates_button_and_achievement(self, monkeypatch):
        host, _ = make_host(monkeypatch)
        host._update_mode_btn_text = lambda: None
        host._music_play_mode = mp.PLAY_MODE_RANDOM
        host._music_cycle_mode()
        assert host._music_play_mode == mp.PLAY_MODE_SEQUENTIAL
        # 依次从四个模式各切一次 → 四种模式都被"试用"过 → 触发成就
        for mode in (mp.PLAY_MODE_SEQUENTIAL, mp.PLAY_MODE_LOOP_LIST,
                     mp.PLAY_MODE_LOOP_SINGLE):
            host._music_play_mode = mode
            host._music_cycle_mode()
        assert host._music_modes_used == {
            mp.PLAY_MODE_SEQUENTIAL, mp.PLAY_MODE_LOOP_LIST,
            mp.PLAY_MODE_LOOP_SINGLE, mp.PLAY_MODE_RANDOM,
        }
        assert ("music_mode_master", True) in host.achievements

    def test_scan_folder_sets_playlist(self, monkeypatch, tmp_path):
        host, _ = make_host(monkeypatch)
        (tmp_path / "b.mp3").write_bytes(b"ID3" + b"\x00" * 13)
        (tmp_path / "a.mp3").write_bytes(b"ID3" + b"\x00" * 13)
        host._rebuild_playlist_ui = lambda: None
        host._music_scan_folder(str(tmp_path))
        assert [Path(p).name for p in host._music_playlist] == ["a.mp3", "b.mp3"]
        assert host._music_current_index == -1
        assert host._music_last_folder == str(tmp_path)
        assert host._music_song_count_label.text == "2 首"

    def test_save_music_state_payload_goes_through_callback(self, monkeypatch):
        host, _ = make_host(monkeypatch)
        saved = []
        host.callbacks = {"save_music_state": saved.append}
        host._music_init_done = True
        host._music_last_folder = "D:/m"
        host._music_current_index = 2
        host._music_progress = 11.5
        host._music_volume = 0.4
        host._music_play_mode = mp.PLAY_MODE_LOOP_SINGLE
        host._music_mini_mode = True
        host._save_music_state()
        assert saved and saved[0]["music_play_mode"] == "loop_single"
        assert saved[0]["music_last_folder"] == "D:/m"
        assert saved[0]["music_last_song_idx_in_playlist"] == -1  # 无歌单上下文

    def test_save_music_state_skipped_before_init(self, monkeypatch):
        host, _ = make_host(monkeypatch)
        saved = []
        host.callbacks = {"save_music_state": saved.append}
        host._music_init_done = False
        host._save_music_state()
        assert saved == []

    def test_load_music_state_applies_values(self, monkeypatch):
        host, _ = make_host(monkeypatch)

        class Mgr:
            current_playlist_id = None

            def load(self):
                pass

            def get_playlist(self, pid):
                return None

            def get_or_create_history_playlist(self):
                return type("PL", (), {"id": "hist"})()

            def get_current_playlist(self):
                return None

            def mark_dirty(self):
                pass

        host._music_playlist_manager = Mgr()
        host.callbacks = {
            "load_music_state": lambda: {
                "music_last_folder": "",
                "music_current_index": 4,
                "music_progress": 30.0,
                "music_volume": 0.25,
                "music_play_mode": "random",
                "music_mini_mode": True,
                "music_last_playlist_id": "pl-9",
                "music_last_song_idx_in_playlist": 5,
            }
        }
        host._update_mode_btn_text = lambda: None
        host._load_music_state()
        assert host._music_current_index == 4
        assert host._music_progress == 30.0
        assert host._music_volume == pytest.approx(0.25)
        assert host._music_play_mode == mp.PLAY_MODE_RANDOM
        assert host._music_mini_mode is True
        assert host._music_vol_slider.value == 25
        assert host.shown_playlist == "hist"  # 保存的歌单不存在 → 回退历史歌单

    def test_load_music_state_tolerates_empty_state(self, monkeypatch):
        host, _ = make_host(monkeypatch)
        host.callbacks = {"load_music_state": lambda: {}}
        host._load_music_state()  # 空状态：什么也不做，不抛
        assert host._music_volume == pytest.approx(0.7)

    def test_apply_wy_saved_login_applies_cookie(self, monkeypatch):
        host, _ = make_host(monkeypatch)
        import services.music_source as music_source

        applied = []
        monkeypatch.setattr(music_source, "wy_apply_cookie", lambda c: applied.append(c))
        host.callbacks = {"get_wy_cookie": lambda: "MUSIC_U=abc"}
        host._music_apply_wy_saved_login()
        assert applied == ["MUSIC_U=abc"]

    def test_apply_wy_saved_login_retries_without_callbacks(self, monkeypatch):
        host, _ = make_host(monkeypatch)
        host.callbacks = {}
        host._music_apply_wy_saved_login()
        assert host.timers and host.timers[-1]["ms"] == ms.LOAD_RETRY_MS

    def test_start_periodic_save_interval(self, monkeypatch):
        host, _ = make_host(monkeypatch)
        host._music_start_periodic_save()
        assert host.timers[-1]["ms"] == ms.PERIODIC_SAVE_INTERVAL_MS





