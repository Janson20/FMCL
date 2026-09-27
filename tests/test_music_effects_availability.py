"""D-147 / D-25：音效链的"可观测性"与时长折算的离线回归测试。

规则：**不联网、不装任何东西、不开窗口、不需要机器上有 ffmpeg**。
"有 ffmpeg"那条路径不去装 ffmpeg，而是往 PATH 里放一个假的 `ffmpeg.bat`
（`shutil.which` 在 Windows 上按 PATHEXT 认得 `.bat`），只有"真的解码/导出"那一步
用假对象代替 —— 于是被验的是本模块自己的探测与门禁逻辑，不是替身的行为。

覆盖与判据（每条都写清"为什么这条不是空断言"）：

1. 可用性状态自洽：`FFMPEG_PATH` ↔ `ffmpeg_available()` ↔ `AudioEffectProcessor.available`
   ↔ `unavailable_reason()` 四者不能互相矛盾。
2. 探测真的在读 PATH：正向（注入假 ffmpeg 后 `_find_ffmpeg()` 必须找到它）与反向
   （PATH 指向空目录时必须找不到）两条一起，才能排除"恒真/恒假"的桩。
3. **缺 ffmpeg 时要吵**（D-147）：不抛异常、返回原文件路径、不留临时文件，并且
   日志里有一条能定位的话 —— 以前这里是**完全没有日志**的静默早退
   （`if not has_any_enabled or not _pydub_available: return input_path`）。
4. **有依赖时真的处理**：把 ffmpeg / pydub 都假出来，验证 `process()` 走到解码 →
   变速 → 导出并产出临时文件。第 3 条与第 4 条互为反面 —— "永远返回原文件"的桩会在
   第 4 条红，"永远尝试处理"的桩会在第 3 条红，所以两条都不是空断言。
5. D-25：`_apply_speed` 的 `original_duration` 必须**真的被读**（长度不符要留日志、
   长度相符不许留日志），且它的长度口径与 `effective_duration()` 完全一致。
6. D-25 的产出：`effective_duration()` / `playback_duration()` 的折算与边界
   （倍率 0 / 负数 / 1.0 / 没有处理文件 / 变速开关关着），这是 D-19 修法要用的入口。
7. **判据本身有分辨力**：`TestTheGateIsLoadBearing` 把 D-147 的两个现场（"永远跳过"、
   "静默跳过"）在内存里还原成变异版本并复跑第 3、4 条的判据 —— 缺陷版本会红，
   于是"这不是空断言"是跑出来的结论，而不是报告里的一句话。
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import types
import wave
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import services.music_effects as me  # noqa: E402

#: 本模块的日志器名（`services/music_effects.py:logger = logging.getLogger("music_effects")`）。
LOGGER_NAME = "music_effects"


def _messages(caplog, level: int = logging.WARNING) -> list:
    """取出本级及以上的日志文本（断言失败时直接打印出来，省得再复跑一次）。"""
    return [r.getMessage() for r in caplog.records if r.levelno >= level]


def _write_tiny_wav(path: Path, ms: int = 100) -> Path:
    """写一个**真的**能用标准库解开的 WAV（8kHz 单声道静音）。

    为什么不用假的空文件：第 3 条要证明"返回原文件"是因为 ffmpeg 缺失，
    而不是因为输入文件根本不存在或读不了 —— 输入必须是合法音频。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * (8 * ms))  # 8 帧/毫秒 @ 8kHz
    return path


def _write_fake_ffmpeg(dirpath: Path) -> Path:
    """在 dirpath 下放一个假的 ffmpeg.bat（探测用，**永远不会被执行**。

    本文件不调用外部进程：假 ffmpeg 只用来让 `shutil.which("ffmpeg")` 命中。
    """
    fake = dirpath / "ffmpeg.bat"
    fake.write_text("@echo off\r\nrem 测试替身：只用于 PATH 探测，不会被调用\r\nexit /b 0\r\n", encoding="utf-8")
    return fake


class _FakeSegment:
    """假 AudioSegment：只实现 `process()` 与 `_apply_speed()` 真正用到的那几处。

    `from_file` / `__len__` / `export` / `_spawn` / `set_frame_rate` 就是全部接触面 ——
    如果实现端换了接触面，这里会立刻报 AttributeError，而不是悄悄变成空断言。
    """

    def __init__(self, duration_ms: int = 1000, rate: int = 8000, channels: int = 1):
        self._duration_ms = duration_ms
        self.frame_rate = rate
        self.channels = channels

    @classmethod
    def from_file(cls, path, *args, **kwargs):
        return cls()

    def __len__(self) -> int:
        return self._duration_ms

    def export(self, out_path, format="wav"):  # noqa: A002 - 与 pydub 同名形参
        Path(out_path).write_bytes(b"fx")  # 内容标记：证明真的写了处理后的音频
        return out_path

    def _spawn(self, data, overrides=None):
        return self

    def set_frame_rate(self, rate):
        return self


def _fake_speed_change(segment, rate):
    """模拟 pydub 的变速语义：长度 = 原长度 / 倍率（这正是 D-19 的折算依据）。"""
    return _FakeSegment(duration_ms=int(len(segment) / rate))


@pytest.fixture
def fake_deps(monkeypatch, tmp_path):
    """把"依赖齐全"这件事造出来：假 ffmpeg 路径 + pydub 可用 + 假的 AudioSegment。

    注意 `raising=False`：本环境里 `_pydub_available` 实测是 False（pydub 0.25.1 里
    没有 `speed_change`，见报告 D-148），所以模块级名字 `AudioSegment` / `speed_change`
    可能根本就不存在，属性得先建出来。
    """
    monkeypatch.setattr(me, "FFMPEG_PATH", str(_write_fake_ffmpeg(tmp_path)))
    monkeypatch.setattr(me, "_pydub_available", True)
    monkeypatch.setattr(me, "AudioSegment", _FakeSegment, raising=False)
    monkeypatch.setattr(me, "speed_change", _fake_speed_change, raising=False)
    return me.FFMPEG_PATH


class TestAvailabilityState:
    """第 1、2 条：状态自洽 + 探测真的在看 PATH。"""

    def test_ffmpeg_path_and_predicate_agree(self):
        assert me.ffmpeg_available() is (me.FFMPEG_PATH is not None)
        assert me.FFMPEG_PATH is None or isinstance(me.FFMPEG_PATH, str)

    def test_reason_is_empty_exactly_when_processor_is_available(self):
        processor = me.AudioEffectProcessor()
        reason = me.unavailable_reason()
        assert (reason == "") is processor.available, (reason, processor.available)
        if not processor.available:
            assert reason.strip(), "不可用却给不出原因 —— 用户没法定位"

    def test_find_ffmpeg_finds_an_injected_ffmpeg(self, tmp_path, monkeypatch):
        """正向：PATH 里有假 ffmpeg 时，探测必须找到它（走的是真的 `shutil.which`）。"""
        fake = _write_fake_ffmpeg(tmp_path)
        monkeypatch.setenv("PATH", str(tmp_path))
        # 先证"注入生效"：否则这条用例可能因为 PATH 根本没改而假绿
        found_by_which = shutil.which("ffmpeg") or shutil.which("avconv")
        assert found_by_which is not None, "PATH 注入没生效"
        assert os.path.normcase(found_by_which) == os.path.normcase(str(fake))
        assert os.path.normcase(me._find_ffmpeg() or "") == os.path.normcase(str(fake))

    def test_find_ffmpeg_returns_none_on_an_empty_path(self, tmp_path, monkeypatch):
        """反向：PATH 指向空目录时必须找不到，且**导入期缓存的**值不会被悄悄改掉。

        后半句是给界面用的判据：用户运行中装好 ffmpeg 需要重启启动器才生效
        （探测只在导入时做一次）。
        """
        before = me.FFMPEG_PATH
        monkeypatch.chdir(tmp_path)  # 避免"当前目录里恰好有 ffmpeg"这种假命中
        monkeypatch.setenv("PATH", str(tmp_path))
        assert me._find_ffmpeg() is None
        assert me.FFMPEG_PATH == before


class TestMissingFFmpegIsLoud:
    """第 3 条（D-147 的核心判据）：缺依赖不许静默。"""

    def test_absent_ffmpeg_degrades_and_logs(self, tmp_path, monkeypatch, caplog):
        if me.ffmpeg_available():
            pytest.skip("本机 PATH 里有 ffmpeg，'缺失'这条路径不适用")
        src = _write_tiny_wav(tmp_path / "in.wav")
        processor = me.AudioEffectProcessor(me.EffectSettings(speed_enabled=True, speed_rate=1.5))
        settings_before = processor.settings.to_dict()
        assert processor.available is False, "前提不成立：这条用例只验'不可用'路径"

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            out = processor.process(str(src))

        assert out == str(src), f"缺 ffmpeg 时应降级为播原文件，实际 {out!r}"
        assert processor._temp_files == [], "跳过时不该留下临时文件"
        assert processor.settings.to_dict() == settings_before, "跳过不许改用户设置"
        messages = _messages(caplog)
        assert any("ffmpeg" in m and "跳过" in m for m in messages), f"日志不可定位: {messages}"

    def test_missing_dependency_names_the_actual_cause(self, tmp_path, monkeypatch, caplog):
        """同一路径的补充判据：原因里要点名缺的那个依赖，而不是一句"失败了"。"""
        if me.ffmpeg_available():
            pytest.skip("本机 PATH 里有 ffmpeg，'缺失'这条路径不适用")
        src = _write_tiny_wav(tmp_path / "in.wav")
        processor = me.AudioEffectProcessor(me.EffectSettings(reverb_enabled=True))
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            processor.process(str(src))
        reason = me.unavailable_reason()
        assert "ffmpeg 不在 PATH" in reason
        assert any(reason in m for m in _messages(caplog)), _messages(caplog)

    def test_no_effects_enabled_stays_quiet(self, tmp_path, caplog):
        """对照：用户没开任何音效时不该刷日志（"静默"只对"他开了却没反应"才叫缺陷）。"""
        src = _write_tiny_wav(tmp_path / "in.wav")
        processor = me.AudioEffectProcessor()
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            assert processor.process(str(src)) == str(src)
        assert _messages(caplog) == [], "音效全关时不该有任何 WARNING"


class TestAvailablePathIsExercised:
    """第 4 条：依赖齐全时 `process()` 必须真的解码 → 变速 → 导出（与第 3 条互为反面）。"""

    def test_processing_produces_a_temp_file(self, fake_deps, tmp_path, caplog):
        src = _write_tiny_wav(tmp_path / "in.wav")
        processor = me.AudioEffectProcessor(me.EffectSettings(speed_enabled=True, speed_rate=2.0))
        assert processor.available is True
        try:
            with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
                out = processor.process(str(src))
            messages = _messages(caplog)
            assert out != str(src), f"依赖齐全时不该降级成原文件；日志: {messages}"
            assert Path(out).exists() and Path(out).read_bytes() == b"fx"
            assert processor._temp_files == [out], "处理产物必须登记，否则没人清理"
            assert not [m for m in messages if "跳过" in m], messages
            assert not [m for m in messages if "失败" in m], f"处理路径不该有失败日志: {messages}"
        finally:
            processor.cleanup()
        assert not Path(out).exists(), "cleanup() 必须真的删掉临时文件"

    def test_default_settings_never_touch_the_decoder(self, fake_deps, tmp_path, caplog):
        """对照：开关全关时即使依赖齐全也不该解码（省掉整曲 CPU，见 D-13 的异步化前提）。"""
        src = _write_tiny_wav(tmp_path / "in.wav")
        processor = me.AudioEffectProcessor()
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            assert processor.process(str(src)) == str(src)
        assert processor._temp_files == []
        assert _messages(caplog) == []


class TestSpeedUsesOriginalDuration:
    """第 5 条（D-25）：`_apply_speed` 的 `original_duration` 必须真的被读。"""

    def test_length_mismatch_is_logged(self, monkeypatch, caplog):
        monkeypatch.setattr(me, "speed_change", lambda audio, rate: _FakeSegment(duration_ms=999), raising=False)
        processor = me.AudioEffectProcessor(me.EffectSettings(speed_enabled=True, speed_rate=2.0))
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            out = processor._apply_speed(_FakeSegment(duration_ms=4000), 4000)
        assert len(out) == 999, "变速结果要原样返回（校验只留日志，不偷偷改结果）"
        assert any("变速后长度与预期不符" in m for m in _messages(caplog)), _messages(caplog)

    def test_matching_length_is_not_logged(self, monkeypatch, caplog):
        """反面：长度符合 `original / rate` 时一句都不许刷（否则这条校验会被忽略掉）。"""
        monkeypatch.setattr(me, "speed_change", _fake_speed_change, raising=False)
        processor = me.AudioEffectProcessor(me.EffectSettings(speed_enabled=True, speed_rate=2.0))
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            out = processor._apply_speed(_FakeSegment(duration_ms=4000), 4000)
        assert len(out) == 2000
        assert not [m for m in _messages(caplog) if "长度与预期不符" in m]

    @pytest.mark.parametrize("rate", [0.5, 1.5, 2.0])
    def test_speed_result_length_matches_effective_duration(self, rate, monkeypatch, caplog):
        """长度口径必须与 `effective_duration()` 一致 —— 两处口径打架时进度条会整段跑偏。"""
        monkeypatch.setattr(me, "speed_change", _fake_speed_change, raising=False)
        processor = me.AudioEffectProcessor(me.EffectSettings(speed_enabled=True, speed_rate=rate))
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            out = processor._apply_speed(_FakeSegment(duration_ms=4000), 4000)
        assert len(out) == pytest.approx(me.effective_duration(4000.0, rate), abs=1)
        assert not [m for m in _messages(caplog) if "长度与预期不符" in m], _messages(caplog)

    def test_speed_failure_keeps_audio_and_logs(self, monkeypatch, caplog):
        """D-147 的同族要求：单效果失败也不许静默（以前是 `except Exception: return audio`）。"""

        def _boom(audio, rate):
            raise RuntimeError("speed_change 不可用")

        monkeypatch.setattr(me, "speed_change", _boom, raising=False)
        processor = me.AudioEffectProcessor(me.EffectSettings(speed_enabled=True, speed_rate=2.0))
        original = _FakeSegment(duration_ms=4000)
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            out = processor._apply_speed(original, 4000)
        assert out is original, "失败时必须退回未变速的音频（降级语义）"
        assert any("变速处理失败" in m for m in _messages(caplog)), _messages(caplog)


class TestDurationConversion:
    """第 6 条（D-25 的产出）：D-19 要用的折算入口。"""

    @pytest.mark.parametrize(
        "original,rate,expect",
        [
            (200.0, 2.0, 100.0),
            (200.0, 0.5, 400.0),
            (200.0, 1.5, 200.0 / 1.5),
            (200.0, 1.0, 200.0),
            (200.0, 1.0005, 200.0),  # 与 process() 里 "|rate-1| <= 0.001 才生效" 的口径一致
            (200.0, 0.0, 200.0),  # 非法倍率：宁可等于原值，也不能变成 0 / inf / 负数
            (200.0, -1.0, 200.0),
            (0.0, 2.0, 0.0),
            (-5.0, 2.0, -5.0),  # 原时长非正（元数据坏掉）：不许折算出一个更荒唐的值
        ],
    )
    def test_effective_duration(self, original, rate, expect):
        assert me.effective_duration(original, rate) == pytest.approx(expect)

    def test_playback_duration_only_converts_when_speed_took_effect(self):
        speed_on = me.EffectSettings(speed_enabled=True, speed_rate=2.0)
        assert me.playback_duration(240.0, speed_on, "/tmp/fmcl_fx_a.wav", "/tmp/a.mp3") == pytest.approx(120.0)
        # 没有产出处理文件（全关 / 没有 ffmpeg / 处理失败降级）→ 长度就是原文件长度
        assert me.playback_duration(240.0, speed_on, "/tmp/a.mp3", "/tmp/a.mp3") == pytest.approx(240.0)
        assert me.playback_duration(240.0, speed_on, "", "/tmp/a.mp3") == pytest.approx(240.0)
        # 变速开关关着 → 就算传了处理文件也不折算（EQ/混响/变调都不改长度）
        assert me.playback_duration(240.0, me.EffectSettings(), "/tmp/fx.wav", "/tmp/a.mp3") == pytest.approx(240.0)

    def test_online_and_local_get_the_same_treatment(self):
        """本地与在线两条播放路径在这件事上没有区别（D-19 的调用点是两处）。"""
        settings = me.EffectSettings(speed_enabled=True, speed_rate=0.5)
        local = me.playback_duration(180, settings, "/tmp/fx.wav", "/tmp/local.mp3")
        online = me.playback_duration(180, settings, "/tmp/fx.wav", "/tmp/online.mp3")
        assert local == online == pytest.approx(360.0)


class TestPydubImportDiagnostics:
    """D-147 的"可定位"：pydub 不可用时必须能看出缺的是哪个符号。"""

    def test_pydub_flag_matches_the_real_import(self):
        """`_pydub_available` 必须等于"真的 import 到了那两个名字"，不许假阴/假阳。"""
        try:
            from pydub import AudioSegment  # noqa: F401
            from pydub.effects import speed_change  # noqa: F401

            expected = True
        except ImportError:
            expected = False
        assert me._pydub_available is expected

    def test_missing_symbol_is_named_in_the_reason(self):
        """实测 pydub 0.25.1 的 `pydub.effects` 里没有 `speed_change`（报告 D-148）。

        修好之后这条会自动跳过 —— 它钉的是"诊断内容"，不是"缺陷必须存在"。
        """
        if me._pydub_available:
            pytest.skip("本环境 pydub 完整，D-148 的诊断判据不适用")
        assert "speed_change" in me._pydub_error, me._pydub_error
        assert "pydub" in me.unavailable_reason()


# ══════════════════════════════════════════════════════════════════════
# D-147 的第 1 条要求：可选的外部依赖必须**写在纸上**，不能只活在代码里
# ══════════════════════════════════════════════════════════════════════


class TestDependencyIsDeclared:
    """为什么也做成用例：这条缺陷的原始形态就是「没有任何地方写着音效链需要 ffmpeg」。

    文档很容易被下一个人顺手删掉，而删掉之后**不会有任何东西报错** —— 所以这里钉两件
    事实：pyproject 与 README 里都有说明，且都写明了探测方式（判据与实现同源）。
    """

    def test_pyproject_declares_the_optional_external_dependency(self):
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        assert "ffmpeg" in text, "pyproject 里没写「音效处理需要外部 ffmpeg」（D-147）"
        assert "shutil.which" in text, "pyproject 里没给出探测方式（D-147 要求写明怎么探测）"
        assert "pydub" in text, "依赖表被动过？pydub 必须仍然在里面"

    def test_readme_environment_section_mentions_ffmpeg(self):
        text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert "## 环境要求" in text, "README 的章节结构变了，本用例要跟着更新"
        section = text.split("## 环境要求", 1)[1].split("\n## ", 1)[0]
        assert "ffmpeg" in section, "README「环境要求」一节没写可选的 ffmpeg 依赖（D-147）"
        assert "shutil.which" in section, "README 里没写探测方式（D-147）"


# ══════════════════════════════════════════════════════════════════════
# 闸门本身要有分辨力（"这条断言不是空的"要能被复跑，而不是只在报告里说一句）
# ══════════════════════════════════════════════════════════════════════

_IMPL_PATH = REPO_ROOT / "services" / "music_effects.py"

#: 造出来的变异模块名，模块级 teardown 时从 `sys.modules` 里摘掉，不污染其它测试文件。
_MUTANTS: list = []


@pytest.fixture(scope="module", autouse=True)
def _drop_mutants_after_the_module():
    yield
    for name in _MUTANTS:
        sys.modules.pop(name, None)
    _MUTANTS.clear()


def _load_mutated_module(replacements, tag: str):
    """把实现源码做几处文本替换后当成**另一个模块**在内存里执行。

    为什么要这么测：只有"缺陷版本会红"的断言才不是空断言。这里把 D-147 的两个现场
    （"永远跳过" 与 "静默跳过"）还原出来，验证本文件对应的判据确实会判它们红。
    全程在内存里做，**不写仓库里的任何文件**；替换点找不到就直接失败，避免实现改了
    之后这条元测试悄悄退化成"什么都没变异"。
    """
    src = _IMPL_PATH.read_text(encoding="utf-8")
    for old, new in replacements:
        assert old in src, f"变异点没找到（实现改了？需要同步更新本元测试）: {old!r}"
        src = src.replace(old, new, 1)
    name = f"_wp3_mutant_{tag}"
    mod = types.ModuleType(name)
    mod.__file__ = str(_IMPL_PATH)
    # 必须登记进 sys.modules：`dataclasses._is_type()` 会按 `cls.__module__` 反查模块
    # 命名空间并直接取 `.__dict__`，不登记时造 @dataclass 当场 AttributeError（实测）。
    sys.modules[name] = mod
    _MUTANTS.append(name)
    exec(compile(src, str(_IMPL_PATH), "exec"), mod.__dict__)
    return mod


def _run_process(mod, src_path: Path, speed_rate: float = 2.0):
    """在给定模块（真实现或变异版本）上跑一次 `process()`，返回 (结果, WARNING 文本列表)。

    直接挂一个 handler 而不是用 caplog：变异模块是运行期造出来的，这样两条路径
    （真实现与变异版本）用的是完全相同的观测方式。
    """
    processor = mod.AudioEffectProcessor(mod.EffectSettings(speed_enabled=True, speed_rate=speed_rate))
    records = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Collect(level=logging.WARNING)
    target = logging.getLogger(LOGGER_NAME)
    target.addHandler(handler)
    try:
        out = processor.process(str(src_path))
    finally:
        target.removeHandler(handler)
        processor.cleanup()
    return out, records


class TestTheGateIsLoadBearing:
    """把 D-147 的两个现场还原成变异版本，验证本文件的判据会红。"""

    def test_mutant_that_always_skips_breaks_the_processing_judge(self, tmp_path):
        """变异 1：守卫恒真（"设了没反应"）→ 依赖齐全也不处理。

        本文件里对应的判据是 `TestAvailablePathIsExercised` 的 `out != str(src)`：
        它在变异版上必然为假 —— 所以那条断言不是空的。
        """
        mutant = _load_mutated_module([("if not self.available:", "if True:")], "always_skip")
        mutant.FFMPEG_PATH = "/definitely/not/ffmpeg"
        mutant._pydub_available = True
        mutant.AudioSegment = _FakeSegment
        mutant.speed_change = _fake_speed_change
        src = _write_tiny_wav(tmp_path / "in.wav")
        out, messages = _run_process(mutant, src)
        assert mutant.unavailable_reason() == "", "变异版自认为可用，才轮到'却还是跳过'这个矛盾"
        assert out == str(src), "变异版把处理整个跳过了 —— 这正是'设了没反应'"
        assert any("跳过" in m for m in messages), messages

    def test_mutant_that_skips_silently_breaks_the_log_judge(self, tmp_path):
        """变异 2：把跳过那条 warning 降成 debug（= 缺陷当年的静默）。

        本文件里对应的判据是 `TestMissingFFmpegIsLoud` 里
        `any("ffmpeg" in m and "跳过" in m for m in messages)`：变异版一条 WARNING
        都不留，所以那条断言也不是空的。
        """
        mutant = _load_mutated_module(
            [('logger.warning("音效处理被跳过', 'logger.debug("音效处理被跳过')], "silent_skip"
        )
        mutant.FFMPEG_PATH = None
        mutant._pydub_available = False
        src = _write_tiny_wav(tmp_path / "in.wav")
        out, messages = _run_process(mutant, src)
        assert mutant.unavailable_reason() != "", "前提：变异版确实处在'依赖缺失'状态"
        assert out == str(src)
        assert messages == [], f"变异版不该留下任何 WARNING（静默正是被修掉的那件事）: {messages}"
