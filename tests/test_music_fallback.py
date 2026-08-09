"""跨源兜底（resolve_track）与在线音频下载校验测试

覆盖:
    - duration_matches 时长匹配判定
    - _quality_attempt_order 兜底音质尝试顺序
    - resolve_track 跨源兜底：排除原音源/时长过滤/候选排序/音质降级/全失败
    - _validate_audio_file_header 文件头魔数校验
    - _validate_audio_duration 试听片段时长校验

运行: pytest tests/test_music_fallback.py -v
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ui.app_music as app_music  # noqa: E402
import ui.music_source as ms  # noqa: E402

MusicInfo = ms.MusicInfo


class FakeSource:
    """模拟音源：可配置搜索结果与 URL 映射，记录调用参数"""

    def __init__(self, source_id, results=None, url_map=None):
        self.source_id = source_id
        self.results = results or []
        self.url_map = url_map
        self.search_calls = []
        self.url_calls = []

    def search(self, keyword, page=1, limit=30):
        self.search_calls.append((keyword, page, limit))
        return self.results

    def get_music_url(self, info, quality="128k"):
        self.url_calls.append((info.songmid, quality))
        if self.url_map is not None:
            return self.url_map.get((info.songmid, quality))
        return "http://example.com/audio.mp3"


def make_info(name="七里香", singer="周杰伦", source="wy", songmid="1", interval=240):
    return MusicInfo(name=name, singer=singer, source=source, songmid=songmid, interval=interval)


@pytest.fixture
def patch_sources(monkeypatch):
    """替换 MUSIC_SOURCES 为可注入的假音源"""

    def _apply(sources: dict):
        fakes = {s["id"]: FakeSource(s["id"]) for s in ms.SOURCE_META}
        fakes.update(sources)
        monkeypatch.setattr(ms, "MUSIC_SOURCES", fakes)
        return fakes

    return _apply


# ═══════════════ duration_matches ═══════════════


class TestDurationMatches:
    def test_within_tolerance(self):
        assert ms.duration_matches(make_info(interval=240), 245) is True
        assert ms.duration_matches(make_info(interval=240), 225) is True
        assert ms.duration_matches(make_info(interval=240), 240) is True

    def test_beyond_tolerance(self):
        assert ms.duration_matches(make_info(interval=240), 180) is False
        assert ms.duration_matches(make_info(interval=240), 300) is False

    def test_unknown_duration_passes(self):
        assert ms.duration_matches(make_info(interval=0), 240) is True
        assert ms.duration_matches(make_info(interval=240), 0) is True
        assert ms.duration_matches(make_info(interval=0), 0) is True

    def test_custom_tolerance(self):
        assert ms.duration_matches(make_info(interval=240), 200, tolerance=40) is True
        assert ms.duration_matches(make_info(interval=240), 150, tolerance=40) is False


# ═══════════════ _quality_attempt_order ═══════════════


class TestQualityAttemptOrder:
    def test_preferred_first(self):
        assert ms._quality_attempt_order("320k") == ["320k", "128k", "flac"]
        assert ms._quality_attempt_order("flac") == ["flac", "128k", "320k"]

    def test_unknown_preferred_falls_back(self):
        assert ms._quality_attempt_order("hq") == ["128k", "320k", "flac"]

    def test_default_quality(self):
        assert ms._quality_attempt_order("128k") == ["128k", "320k", "flac"]


# ═══════════════ resolve_track ═══════════════


class TestResolveTrack:
    def test_none_info_returns_none(self, patch_sources):
        patch_sources({})
        assert ms.resolve_track(None) is None

    def test_no_name_returns_none(self, patch_sources):
        patch_sources({})
        assert ms.resolve_track(make_info(name="")) is None

    def test_primary_source_excluded(self, patch_sources):
        """原音源不参与兜底搜索"""
        target = make_info(source="wy", songmid="1")
        fakes = patch_sources(
            {
                "wy": FakeSource("wy", results=[make_info(source="wy", songmid="1")]),
                "kg": FakeSource("kg", results=[make_info(source="kg", songmid="k1", interval=238)]),
            }
        )
        result = ms.resolve_track(target, "320k")
        assert result is not None
        info, url = result
        assert info.source == "kg"
        assert fakes["wy"].search_calls == []  # 原音源未参与

    def test_keyword_contains_name_and_singer(self, patch_sources):
        target = make_info(source="wy", name="七里香", singer="周杰伦")
        fakes = patch_sources({"kg": FakeSource("kg", results=[])})
        ms.resolve_track(target)
        assert fakes["kg"].search_calls[0][0] == "七里香 周杰伦"

    def test_duration_filter_excludes_cover(self, patch_sources):
        """时长差异过大的翻唱/伴奏被过滤"""
        target = make_info(source="wy", interval=240)
        cover = make_info(source="kg", songmid="cover", interval=150)
        original = make_info(source="kg", songmid="orig", interval=238)
        fakes = patch_sources({"kg": FakeSource("kg", results=[cover, original])})
        result = ms.resolve_track(target)
        assert result is not None
        assert result[0].songmid == "orig"
        called_ids = [c[0] for c in fakes["kg"].url_calls]
        assert "cover" not in called_ids

    def test_candidates_sorted_by_duration_diff(self, patch_sources):
        """候选按时长差升序尝试：先试时长最接近的"""
        target = make_info(source="wy", interval=240)
        far = make_info(source="kg", songmid="far", interval=245)  # 差5
        near = make_info(source="kg", songmid="near", interval=238)  # 差2
        fakes = patch_sources({"kg": FakeSource("kg", results=[far, near])})
        result = ms.resolve_track(target)
        assert result is not None
        assert fakes["kg"].url_calls[0][0] == "near"

    def test_quality_fallback_within_source(self, patch_sources):
        """用户音质拿不到时按顺序降级"""
        target = make_info(source="wy", songmid="1")
        cand = make_info(source="kg", songmid="k1")
        url_map = {("k1", "128k"): "http://example.com/k1.mp3"}
        fakes = patch_sources({"kg": FakeSource("kg", results=[cand], url_map=url_map)})
        result = ms.resolve_track(target, quality="320k")
        assert result == (cand, "http://example.com/k1.mp3")
        assert [q for _, q in fakes["kg"].url_calls] == ["320k", "128k"]

    def test_returns_first_successful_source(self, patch_sources):
        """多个音源中只要有一个成功即返回（失败的被跳过）"""
        target = make_info(source="wy", songmid="1")

        class FailingSource(FakeSource):
            def get_music_url(self, info, quality="128k"):
                self.url_calls.append((info.songmid, quality))
                return None

        fakes = patch_sources(
            {
                "kw": FailingSource("kw", results=[make_info(source="kw", songmid="w1")]),
                "tx": FakeSource("tx", results=[make_info(source="tx", songmid="t1")]),
            }
        )
        result = ms.resolve_track(target)
        assert result is not None
        assert result[0].source == "tx"
        assert fakes["tx"].url_calls  # tx 被尝试过

    def test_search_exception_skips_source(self, patch_sources):
        """搜索抛异常的源被跳过，不影响其它源"""

        class BrokenSource(FakeSource):
            def search(self, keyword, page=1, limit=30):
                raise ConnectionError("network down")

        target = make_info(source="wy", songmid="1")
        fakes = patch_sources(
            {
                "kw": BrokenSource("kw"),
                "mg": FakeSource("mg", results=[make_info(source="mg", songmid="m1")]),
            }
        )
        result = ms.resolve_track(target)
        assert result is not None
        assert result[0].source == "mg"

    def test_all_fail_returns_none(self, patch_sources):
        target = make_info(source="wy", songmid="1")
        empty = FakeSource("kg", results=[])
        no_url = FakeSource("tx", results=[make_info(source="tx", songmid="t1")], url_map={})
        patch_sources({"kg": empty, "tx": no_url})
        assert ms.resolve_track(target) is None

    def test_excluded_sources_param(self, patch_sources):
        """手动排除指定音源"""
        target = make_info(source="wy", songmid="1")
        fakes = patch_sources(
            {"kg": FakeSource("kg", results=[make_info(source="kg", songmid="k1")])}
        )
        result = ms.resolve_track(target, excluded_sources=["kg"])
        assert result is None
        assert fakes["kg"].search_calls == []


# ═══════════════ 下载校验 ═══════════════


class TestValidateAudioFileHeader:
    def _write(self, tmp_path, name, data: bytes) -> str:
        p = tmp_path / name
        p.write_bytes(data)
        return str(p)

    def test_id3_mp3(self, tmp_path):
        path = self._write(tmp_path, "a.mp3", b"ID3\x04\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00")
        assert app_music._validate_audio_file_header(path) is True

    def test_raw_mp3_frame(self, tmp_path):
        path = self._write(tmp_path, "b.mp3", b"\xff\xfb\x90\x00" + b"\x00" * 12)
        assert app_music._validate_audio_file_header(path) is True

    def test_flac(self, tmp_path):
        path = self._write(tmp_path, "c.flac", b"fLaC" + b"\x00" * 12)
        assert app_music._validate_audio_file_header(path) is True

    def test_ogg(self, tmp_path):
        path = self._write(tmp_path, "d.ogg", b"OggS\x00\x02" + b"\x00" * 10)
        assert app_music._validate_audio_file_header(path) is True

    def test_wav(self, tmp_path):
        path = self._write(tmp_path, "e.wav", b"RIFF" + b"\x00" * 12)
        assert app_music._validate_audio_file_header(path) is True

    def test_m4a_ftyp(self, tmp_path):
        path = self._write(tmp_path, "f.m4a", b"\x00\x00\x00\x18" + b"ftypM4A " + b"\x00" * 8)
        assert app_music._validate_audio_file_header(path) is True

    def test_html_rejected(self, tmp_path):
        path = self._write(tmp_path, "g.mp3", b"<html><body>error</body></html>")
        assert app_music._validate_audio_file_header(path) is False

    def test_empty_rejected(self, tmp_path):
        path = self._write(tmp_path, "h.mp3", b"")
        assert app_music._validate_audio_file_header(path) is False

    def test_nonexistent_rejected(self, tmp_path):
        assert app_music._validate_audio_file_header(str(tmp_path / "nope.mp3")) is False


class TestValidateAudioDuration:
    def test_within_tolerance(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app_music, "_mutagen_import_error", None)
        monkeypatch.setattr(app_music, "_extract_audio_metadata", lambda p: {"duration": 233.0})
        p = tmp_path / "a.mp3"
        p.write_bytes(b"ID3" + b"\x00" * 13)
        # 240s 预期: 差 7s <= max(10, 48) -> 通过
        assert app_music._validate_audio_duration(str(p), 240) is True

    def test_trial_clip_rejected(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app_music, "_mutagen_import_error", None)
        monkeypatch.setattr(app_music, "_extract_audio_metadata", lambda p: {"duration": 90.0})
        p = tmp_path / "b.mp3"
        p.write_bytes(b"ID3" + b"\x00" * 13)
        # 300s 预期: 差 210s > 60s -> 试听片段
        assert app_music._validate_audio_duration(str(p), 300) is False
        # 240s 预期: 差 150s > 48s -> 试听片段
        assert app_music._validate_audio_duration(str(p), 240) is False

    def test_unknown_duration_passes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app_music, "_mutagen_import_error", None)
        monkeypatch.setattr(app_music, "_extract_audio_metadata", lambda p: {"duration": 0.0})
        p = tmp_path / "c.mp3"
        p.write_bytes(b"ID3" + b"\x00" * 13)
        assert app_music._validate_audio_duration(str(p), 240) is True
        assert app_music._validate_audio_duration(str(p), 0) is True

    def test_mutagen_unavailable_passes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app_music, "_mutagen_import_error", ImportError("no mutagen"))
        p = tmp_path / "d.mp3"
        p.write_bytes(b"ID3" + b"\x00" * 13)
        assert app_music._validate_audio_duration(str(p), 300) is True

    def test_parse_exception_passes(self, monkeypatch, tmp_path):
        monkeypatch.setattr(app_music, "_mutagen_import_error", None)

        def _boom(path):
            raise RuntimeError("broken file")

        monkeypatch.setattr(app_music, "_extract_audio_metadata", _boom)
        p = tmp_path / "e.mp3"
        p.write_bytes(b"ID3" + b"\x00" * 13)
        assert app_music._validate_audio_duration(str(p), 240) is True


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
