"""``services/voice_service.py`` 单元测试 —— 纯逻辑，绝不真录音 / 真下载 / 真推理

覆盖四类要求：

1. **降级路径**：音频组件（numpy / sounddevice）与推理组件（onnxruntime /
   sentencepiece）缺失、模型文件缺失、模型下载失败、解压后仍不完整；
2. **纯函数**：CTC 折叠、分段拼接、特征提取形状、分块切片的边界；
3. **模型导入的边界**：文件不存在、扩展名不对、内容不对、正常导入并替换旧模型；
4. **服务可独立实例化**：没有 AppContext 也能构造、查询状态、轮询事件，
   且**自己不创建线程**。

约定：外部依赖一律走构造函数注入口（``model_dir_fn`` / ``is_model_ready_fn`` /
``engine_factory`` / ``stream_factory``）或 monkeypatch 模块级实现；
"跑一轮录音"用假音频流 + 假引擎同步驱动，不起线程、不碰硬件、不联网。
"""

from __future__ import annotations

import shutil
import sys
import threading
import types
import zipfile
from pathlib import Path
from typing import Any, List

import numpy as np
import pytest

import services.voice_service as voice_service
from services.base import Service
from services.errors import ServiceNotAvailable
from services.voice.models import MODEL_MANUAL_HINT_URL, MODEL_ZIP, is_model_ready
from services.voice.sensevoice import (
    NumPyMelExtractor,
    SenseVoice,
    SenseVoiceDecoder,
    SenseVoiceEncoder,
)
from services.voice_service import (
    MAX_RECORD_SECONDS,
    SAMPLE_RATE,
    STATE_DOWNLOADING,
    STATE_IDLE,
    STATE_LOADING,
    STATE_RECOGNIZING,
    STATE_RECORDING,
    VoiceInputListener,
    VoiceService,
    _load_engine,
    _voice_engine_available,
)


class RecordingListener(VoiceInputListener):
    """把服务推来的事件按 (kind, ...) 记下来，便于断言顺序与内容。"""

    def __init__(self) -> None:
        self.events: List[tuple] = []

    def on_voice_state(self, state: str, message: str = "") -> None:
        self.events.append(("state", state, message))

    def on_voice_progress(self, percent: float, message: str = "") -> None:
        self.events.append(("progress", percent, message))

    def on_voice_final(self, text: str) -> None:
        self.events.append(("final", text))

    def on_voice_error(self, message: str) -> None:
        self.events.append(("error", message))

    def kinds(self) -> List[str]:
        return [e[0] for e in self.events]


def _make_model_zip(path: Path, suffix: str = ".int8.onnx", nested: bool = False) -> Path:
    """造一个合法的模型压缩包（不联网）。"""
    prefix = "SenseVoice-Small/Sensevoice-Small-ONNX/" if nested else ""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(prefix + f"SenseVoice-Encoder{suffix}", b"enc")
        zf.writestr(prefix + f"SenseVoice-CTC{suffix}", b"ctc")
        zf.writestr(prefix + "tokenizer.bpe.model", b"tok")
    return path


def _patch_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """把 ``config.base_dir`` 指到临时目录，返回默认模型目录。

    与 ``tests/test_voice_input.py`` 用的是同一招：模型路径本来就经
    ``config.base_dir`` 解析，因此**不需要**动生产代码即可做到"不碰真模型目录"。
    """
    fake_config = types.SimpleNamespace(base_dir=tmp_path)
    monkeypatch.setitem(sys.modules, "config", types.SimpleNamespace(config=fake_config))
    return tmp_path / "models" / "voice" / "sensevoice"


# ─── 1. 无 AppContext 也能独立实例化 ────────────────────────


def test_service_instantiates_without_appcontext():
    """服务可以独立构造：没有 AppContext 时状态查询/轮询接口照常可用。"""
    service = VoiceService()
    assert isinstance(service, Service)
    assert service.attached is False
    assert service.started is False
    assert service.get_state() == STATE_IDLE
    assert service.is_recording() is False
    assert service.current_text() == ""
    assert service.poll_once(RecordingListener()) == []


def test_service_only_touches_context_when_asked():
    """服务自身的方法不依赖 AppContext（只有基类的 ui/tasks/config 才需要）。"""
    service = VoiceService()
    with pytest.raises(ServiceNotAvailable):
        _ = service.context
    # 下面这些调用都不会抛：说明 is_available / 状态查询都不碰 context
    service.is_available()
    service.is_recording()
    service.current_text()
    service.stop()


def test_service_declares_name_and_label():
    assert VoiceService.name == "voice"
    assert VoiceService.label
    assert VoiceService().describe()["name"] == "voice"


def test_service_is_a_process_singleton():
    """单例语义与搬家前的 VoiceInputManager.instance() 一致。"""
    assert VoiceService.instance() is VoiceService.instance()
    assert VoiceService() is not VoiceService.instance()


def test_service_never_creates_threads(monkeypatch):
    """服务不自己起线程：把它的 threading 换成"禁止 Thread"的替身。"""

    def _forbidden(*args, **kwargs):  # pragma: no cover - 触发即为失败
        raise AssertionError("服务不应创建线程")

    proxy = types.SimpleNamespace(
        Thread=_forbidden, Event=threading.Event, Lock=threading.Lock, RLock=threading.RLock
    )
    monkeypatch.setattr(voice_service, "threading", proxy)
    service = VoiceService(is_model_ready_fn=lambda model_path=None: True)
    listener = RecordingListener()
    service.register(listener)
    assert service.begin_session(listener) is True  # 状态转移照做
    assert service._session_runner is None
    assert service.get_state() == STATE_IDLE  # 没人跑会话主体 → 状态没变
    service.stop()
    service.unregister(listener)


# ─── 2. 组件缺失 / 模型缺失的降级路径 ───────────────────────


def test_engine_available_when_components_present():
    ok, reason = _voice_engine_available()
    assert ok is True and reason == ""


def test_engine_unavailable_when_audio_deps_missing(monkeypatch):
    monkeypatch.setattr(voice_service, "_HAVE_AUDIO_DEPS", False)
    ok, reason = _voice_engine_available()
    assert ok is False
    assert reason == voice_service._("voice_error_missing_deps")
    assert VoiceService().is_available() == (False, reason)


def test_engine_unavailable_when_onnxruntime_missing(monkeypatch):
    """onnxruntime 装不上（x86 win32 轮子缺失）时要降级而不是崩。"""
    monkeypatch.setitem(sys.modules, "onnxruntime", None)
    ok, reason = _voice_engine_available()
    assert ok is False and reason == voice_service._("voice_error_missing_deps")


def test_engine_unavailable_when_sentencepiece_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentencepiece", None)
    ok, reason = _voice_engine_available()
    assert ok is False and reason == voice_service._("voice_error_missing_deps")


def test_model_files_missing_then_ready(monkeypatch, tmp_path):
    """模型文件缺失/齐全时 ``is_model_ready`` 的两种结论（真实现，不联网）。"""
    model_dir = _patch_config(monkeypatch, tmp_path)
    model_dir.mkdir(parents=True)
    assert is_model_ready() is False
    (model_dir / "SenseVoice-Encoder.int8.onnx").write_bytes(b"e")
    (model_dir / "SenseVoice-CTC.int8.onnx").write_bytes(b"c")
    assert is_model_ready() is False  # 还缺 tokenizer
    (model_dir / "tokenizer.bpe.model").write_bytes(b"t")
    assert is_model_ready() is True


def test_ensure_model_download_failure_broadcasts_error(tmp_path):
    """下载失败（离线）→ 广播 voice_error_download 并返回 False，不抛异常。"""
    service = VoiceService(
        model_dir_fn=lambda: tmp_path,
        is_model_ready_fn=lambda model_path=None: False,
    )
    listener = RecordingListener()
    service.register(listener)
    service._active_listener = listener

    def _offline(url, dest):
        raise RuntimeError("模拟离线")

    service._download = _offline  # type: ignore[method-assign]
    assert service._ensure_model() is False
    events = service.poll_once(listener)
    assert [e[0] for e in events] == ["state", "error"]
    assert events[0][1] == STATE_DOWNLOADING
    assert events[1][1] == voice_service._("voice_error_download", err="模拟离线")


def test_ensure_model_success_extracts_from_local_zip(tmp_path):
    """下载成功后真解压（zip 由测试本地造，不走网络）。"""
    src = tmp_path / "src"
    src.mkdir()
    zip_path = _make_model_zip(src / MODEL_ZIP)

    def _copy(url, dest):
        shutil.copyfile(zip_path, dest)

    def _ready(model_path=None):
        d = Path(model_path) if model_path is not None else tmp_path
        return bool(list(d.glob("SenseVoice-Encoder*.onnx"))) and (d / "tokenizer.bpe.model").exists()

    service = VoiceService(model_dir_fn=lambda: tmp_path, is_model_ready_fn=_ready)
    service._download = _copy  # type: ignore[method-assign]
    assert service._ensure_model() is True
    assert (tmp_path / "SenseVoice-Encoder.int8.onnx").read_bytes() == b"enc"
    assert (tmp_path / "SenseVoice-CTC.int8.onnx").read_bytes() == b"ctc"
    assert (tmp_path / "tokenizer.bpe.model").read_bytes() == b"tok"
    assert not (tmp_path / MODEL_ZIP).exists(), "下载的 zip 应在 finally 里删掉"


def test_ensure_model_hints_manual_import_when_still_incomplete(tmp_path):
    """解压成功但文件仍不齐 → 提示手动导入渠道（含 URL 与目录）。"""
    src = tmp_path / "src"
    src.mkdir()
    zip_path = _make_model_zip(src / MODEL_ZIP)
    model_dir = tmp_path / "model_dir"
    model_dir.mkdir()

    def _copy(url, dest):
        shutil.copyfile(zip_path, dest)

    service = VoiceService(
        model_dir_fn=lambda: model_dir,
        is_model_ready_fn=lambda model_path=None: False,  # 永远不齐
    )
    listener = RecordingListener()
    service.register(listener)
    service._active_listener = listener
    service._download = _copy  # type: ignore[method-assign]
    assert service._ensure_model() is False
    errors = [e[1] for e in service.poll_once(listener) if e[0] == "error"]
    assert errors == [
        voice_service._("voice_error_model_manual", url=MODEL_MANUAL_HINT_URL, path=str(model_dir))
    ]


# ─── 3. 模型导入的边界 ──────────────────────────────────────


def test_import_model_zip_rejects_missing_file(tmp_path):
    service = VoiceService(model_dir_fn=lambda: tmp_path)
    with pytest.raises(RuntimeError) as err:
        service.import_model_zip(str(tmp_path / "不存在.zip"))
    assert str(err.value) == voice_service._("voice_import_invalid_file")


def test_import_model_zip_rejects_non_zip(tmp_path):
    bogus = tmp_path / "model.tar.gz"
    bogus.write_bytes(b"not a zip")
    service = VoiceService(model_dir_fn=lambda: tmp_path)
    with pytest.raises(RuntimeError) as err:
        service.import_model_zip(str(bogus))
    assert str(err.value) == voice_service._("voice_import_invalid_file")


def test_import_model_zip_rejects_wrong_package(tmp_path):
    """是个 zip 但内容不对（缺必要文件）→ voice_import_wrong_package。"""
    bad_zip = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad_zip, "w") as zf:
        zf.writestr("readme.txt", b"nothing useful")
    model_dir = tmp_path / "models"
    service = VoiceService(model_dir_fn=lambda: model_dir)
    with pytest.raises(RuntimeError) as err:
        service.import_model_zip(str(bad_zip))
    assert str(err.value) == voice_service._("voice_import_wrong_package")
    assert not (model_dir / ".import_tmp").exists(), "临时目录必须被清掉"
    assert [p.name for p in model_dir.iterdir()] == [], "内容不对时不得留下任何文件"


def test_import_model_zip_success_replaces_old_model(monkeypatch, tmp_path):
    """正常导入：返回版本标识、解压到位、旧模型文件被替换。"""
    model_dir = _patch_config(monkeypatch, tmp_path)
    model_dir.mkdir(parents=True)
    # 先放一份旧模型（fp16），导入 int8 后它必须消失
    (model_dir / "SenseVoice-Encoder.fp16.onnx").write_bytes(b"old")
    (model_dir / "SenseVoice-CTC.fp16.onnx").write_bytes(b"old")
    (model_dir / "tokenizer.bpe.model").write_bytes(b"old")
    zip_path = _make_model_zip(tmp_path / "Sensevoice-Small-ONNX-int8.zip", suffix=".int8.onnx", nested=True)

    service = VoiceService()  # 用默认实现，只是 config 指向了 tmp
    assert service.import_model_zip(str(zip_path)) == "int8"
    assert (model_dir / "SenseVoice-Encoder.int8.onnx").read_bytes() == b"enc"
    assert not (model_dir / "SenseVoice-Encoder.fp16.onnx").exists()
    assert not (model_dir / ".import_tmp").exists()
    assert is_model_ready() is True


# ─── 4. 文本后处理（纯函数）─────────────────────────────────


def _bare_decoder() -> SenseVoiceDecoder:
    """不加载 ONNX 的解码器实例（只喂纯函数逻辑）。"""
    decoder = object.__new__(SenseVoiceDecoder)
    decoder.input_dtype = np.float32
    return decoder


def test_decode_all_collapses_repeats_and_drops_blank():
    """CTC 贪心折叠：连续重复帧合成一个字、blank 丢弃、▁ 变空格、带时间戳。

    ``topk_indices`` 的前 ``PROMPT_LEN``(=4) 帧是 prompt，解码时整段跳过，
    切片后帧号从 0 重新计数。另外注意：blank 只是"被丢弃"，**不会**把它
    两侧同 id 合并（这是 CTC 的语义）。
    """
    pieces = {0: "<blank>", 5: "\u2581你", 6: "\u2581", 7: "好"}
    sp = types.SimpleNamespace(id_to_piece=lambda i: pieces[i])

    decoder = _bare_decoder()
    topk = np.array([[9, 9, 9, 9, 5, 5, 0, 5, 7, 6, 6, 0]], dtype=np.int64)[:, :, np.newaxis]
    decoder.session = types.SimpleNamespace(run=lambda *a, **k: (None, topk))
    results = decoder.decode_all(np.zeros((1, 12, 512), np.float32), sp, T_valid=8)
    assert [r["text"] for r in results] == [" 你", " 你", "好", " "]
    assert [r["start"] for r in results] == [0.0, 0.18, 0.24, 0.3]

    collapsed = _bare_decoder()
    topk2 = np.array([[9, 9, 9, 9, 5, 5, 5, 7, 7]], dtype=np.int64)[:, :, np.newaxis]
    collapsed.session = types.SimpleNamespace(run=lambda *a, **k: (None, topk2))
    results2 = collapsed.decode_all(np.zeros((1, 9, 512), np.float32), sp, T_valid=5)
    assert [r["text"] for r in results2] == [" 你", "好"]  # 连续 5,5,5 折成一个
    assert [r["start"] for r in results2] == [0.0, 0.18]


def test_decode_all_skips_whitespace_only_pieces_except_space():
    """纯空白但又不是空格的片段被丢弃（只有 ``" "`` 会保留）。"""
    decoder = _bare_decoder()
    topk = np.array([[0, 0, 0, 0, 1, 1, 2, 2]], dtype=np.int64)[:, :, np.newaxis]
    decoder.session = types.SimpleNamespace(run=lambda *a, **k: (None, topk))
    sp = types.SimpleNamespace(id_to_piece=lambda i: {1: "  ", 2: "\u2581"}[i])
    results = decoder.decode_all(np.zeros((1, 8, 4), np.float32), sp, T_valid=4)
    assert [r["text"] for r in results] == [" "]


def test_construct_prompt_uses_metadata():
    encoder = object.__new__(SenseVoiceEncoder)
    encoder.lid_dict = {"auto": 0, "zh": 3}
    encoder.itn_dict = {"withitn": 14, "woitn": 15}
    assert encoder.construct_prompt("zh", True).tolist() == [[3, 1, 2, 14]]
    assert encoder.construct_prompt("auto", False).tolist() == [[0, 1, 2, 15]]
    assert encoder.construct_prompt("不存在", True).tolist() == [[0, 1, 2, 14]]  # 未知语言回退 0


def test_merge_results_empty_and_single():
    """分段拼接的边界：空输入、单段、空段。"""
    tool = object.__new__(SenseVoice)
    assert tool._merge_results([], 5.0) == ""
    assert tool._merge_results([[]], 5.0) == ""
    assert tool._merge_results([[{"text": "你好", "start": 0.0}]], 5.0) == "你好"
    assert tool._merge_results([[], []], 5.0) == ""


def test_merge_results_dedups_overlap_text():
    """两段重叠时按 SequenceMatcher 对齐去重：匹配区间中点处切分。

    注意（**记录现状，不是期望值**）：切分点落在"跨越中点的那一片"上时，
    该片会被两侧同时丢弃。这里第二段里 ``"不错"`` 正是这一片，于是它整段
    消失 —— 详见交付报告里记的既有缺陷（本任务不修）。
    """
    tool = object.__new__(SenseVoice)
    first = [
        {"text": "今天", "start": 0.0},
        {"text": "天气", "start": 0.06},
        {"text": "不错", "start": 0.12},
    ]
    second = [
        {"text": "天气", "start": 0.10},
        {"text": "不错", "start": 0.16},
        {"text": "适合", "start": 0.22},
        {"text": "出门", "start": 0.28},
    ]
    merged = tool._merge_results([first, second], 5.0)
    assert merged == "今天天气适合出门"  # 现状：重叠部分只留一份，但丢了"不错"
    assert merged.count("天气") == 1


def test_merge_results_keeps_disjoint_segments():
    """完全没有公共子串时两段都要保留（按时间先后拼接）。"""
    tool = object.__new__(SenseVoice)
    first = [{"text": "甲乙丙", "start": 0.0}]
    second = [{"text": "丁戊己", "start": 100.0}]
    assert tool._merge_results([first, second], 5.0) == "甲乙丙丁戊己"


def test_merge_results_disjoint_but_has_common_substring_loses_text():
    """**记录现状**：两段其实不重叠，但有偶然公共子串时会被当成重叠去重。

    ``"前半段"`` / ``"后半段"`` 共享 ``"半段"``，SequenceMatcher 匹配到它之后
    两侧各自切在中点外，结果两边都被切光 → 返回空串。
    这是既有实现的真实行为（本任务只搬家不改逻辑），列入交付报告的缺陷清单。
    """
    tool = object.__new__(SenseVoice)
    first = [{"text": "前半段", "start": 0.0}]
    second = [{"text": "后半段", "start": 100.0}]
    assert tool._merge_results([first, second], 5.0) == ""


# ─── 5. 特征提取 / 音频切片（纯函数部分；本模块没有 VAD）────


def test_mel_extractor_shape_is_lfr_560():
    """LFR 特征形状 (T, 560)：T = (mel 帧数 + 5) // 6，1 秒 16k 音频 → 17 帧。"""
    extractor = NumPyMelExtractor()
    out = extractor.extract(np.zeros(SAMPLE_RATE, dtype=np.float32))
    assert out.shape == (17, 560)
    assert out.dtype == np.float32
    assert bool(np.isfinite(out).all())
    longer = extractor.extract(np.zeros(SAMPLE_RATE * 2, dtype=np.float32))
    assert longer.shape[1] == 560 and longer.shape[0] > out.shape[0]


class _ChunkProbe(SenseVoice):
    """只观察分块边界的探针：不加载模型、不推理。"""

    def __init__(self, frames: int) -> None:
        self.frontend = types.SimpleNamespace(
            extract=lambda audio: np.zeros((frames, 560), dtype=np.float32)
        )
        self.itn = True
        self.slices: List[tuple] = []

    def _recognize_lfr(self, chunk_lfr, lid, itn, offset_sec):
        self.slices.append((int(chunk_lfr.shape[0]), offset_sec))
        return []


def test_recognize_slices_audio_with_stride_and_overlap():
    """40s 一块、5s 重叠 → 步长 = 666-83 = 583 帧；尾块截断且不重复计算。"""
    probe = _ChunkProbe(frames=700)
    assert probe.recognize(np.zeros(1000, dtype=np.float32)) == ""
    assert probe.slices[0] == (666, 0.0)
    assert probe.slices[1][0] == 700 - 583  # 尾块只有剩下的 117 帧
    assert probe.slices[1][1] == pytest.approx(583 * 6 * 0.01)
    assert len(probe.slices) == 2, "超过总长之后必须 break，不能无限切"

    small = _ChunkProbe(frames=100)
    small.recognize(np.zeros(1000, dtype=np.float32))
    assert small.slices == [(100, 0.0)]


def test_audio_constants_are_unchanged():
    assert SAMPLE_RATE == 16000
    assert MAX_RECORD_SECONDS == 120
    assert SenseVoice.CHUNK_SEC == 40 and SenseVoice.OVERLAP_SEC == 5


# ─── 6. 状态 / 事件 / 轮询接口 ──────────────────────────────


def test_poll_once_returns_events_and_tracks_current_text():
    service = VoiceService()
    listener = RecordingListener()
    service.register(listener)
    service._active_listener = listener
    service._broadcast(("final", "你好世界"), active_only=True)
    service._broadcast(("final", "再会"), active_only=True)
    events = service.poll_once(listener)
    assert [e[0] for e in events] == ["final", "final"]
    assert service.current_text() == "再会"  # 取最后一次
    assert service.poll_once(listener) == []  # 取过就没了


def test_poll_once_unknown_listener_returns_empty():
    service = VoiceService()
    assert service.poll_once(RecordingListener()) == []
    assert service.drain_events(RecordingListener()) == []


def test_is_recording_follows_state():
    service = VoiceService()
    assert service.is_recording() is False
    service._set_state(STATE_RECORDING, voice_service._("voice_recording"))
    assert service.is_recording() is True
    service._set_state(STATE_RECOGNIZING)
    assert service.is_recording() is False
    assert service.get_state() == STATE_RECOGNIZING


def test_begin_session_returns_false_when_busy():
    """非空闲状态一律不开始新会话（与搬家前 start() 的提前返回一致）。"""
    service = VoiceService()
    listener = RecordingListener()
    service.register(listener)
    for busy in (STATE_DOWNLOADING, STATE_LOADING, STATE_RECORDING, STATE_RECOGNIZING):
        service._set_state(busy)
        assert service.begin_session(listener) is False
        assert service._active_listener is None


def test_toggle_ignores_clicks_while_preparing_or_recognizing():
    service = VoiceService()
    listener = RecordingListener()
    service.register(listener)
    for busy in (STATE_DOWNLOADING, STATE_LOADING, STATE_RECOGNIZING):
        service._set_state(busy)
        service.poll_once(listener)  # 先把状态事件取走
        service.toggle(listener)
        assert service.get_state() == busy
        assert service._active_listener is None
        assert service.poll_once(listener) == [], "忙碌期点击不得产生任何新事件"


def test_toggle_stops_recording_and_hands_over_to_other_listener():
    """录音中点另一个输入框：先停当前录音，并把它记为待开始（pending）。"""
    service = VoiceService(is_model_ready_fn=lambda model_path=None: True)
    first, second = RecordingListener(), RecordingListener()
    service.register(first)
    service.register(second)
    service._active_listener = first
    service._set_state(STATE_RECORDING)

    service.toggle(first)  # 同一个按钮 → 只是停
    assert service._stop_event.is_set()
    assert service._pending_listener is None

    service._stop_event = threading.Event()
    service._set_state(STATE_RECORDING)
    service.toggle(second)  # 另一个按钮 → 停 + 记 pending
    assert service._stop_event.is_set()
    assert service._pending_listener is second


def test_finish_session_starts_pending_listener_via_runner():
    """会话收尾时把录音移交给 pending 的输入框（经注入的 runner，不起线程）。"""
    service = VoiceService(is_model_ready_fn=lambda model_path=None: True)
    first, second = RecordingListener(), RecordingListener()
    service.register(first)
    service.register(second)
    scheduled: List[Any] = []
    service.set_session_runner(lambda fn: scheduled.append(fn))
    service._active_listener = first
    service._pending_listener = second
    service._set_state(STATE_RECORDING)

    service._finish_session()
    assert service.get_state() == STATE_IDLE
    assert service._active_listener is second  # 已切换到新输入框
    assert len(scheduled) == 1 and scheduled[0] == service.run_session


def test_broadcast_from_worker_threads_loses_nothing():
    """事件队列是线程安全的：4 个线程各推 200 条，一条都不能丢。"""
    service = VoiceService()
    listener = RecordingListener()
    service.register(listener)

    def spam():
        for i in range(200):
            service._broadcast(("progress", float(i), "x"))

    threads = [threading.Thread(target=spam) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert len(service.drain_events(listener)) == 800


def test_unregister_stops_delivery_and_clears_active_listener():
    service = VoiceService()
    listener = RecordingListener()
    service.register(listener)
    service._active_listener = listener
    service.unregister(listener)
    assert service._active_listener is None
    service._broadcast(("final", "x"), active_only=True)
    assert service.poll_once(listener) == []


def test_extract_model_is_a_staticmethod_on_the_service():
    """UI 门面转发的 ``_extract_model`` 就是服务上的同一个静态方法。"""
    assert callable(VoiceService._extract_model)
    assert isinstance(VoiceService.__dict__["_extract_model"], staticmethod)


def test_session_runner_injection_is_optional():
    service = VoiceService()
    calls: List[Any] = []
    service.set_session_runner(lambda fn: calls.append(fn))
    assert service._session_runner is not None
    service.set_session_runner(None)
    assert service._session_runner is None


def test_load_engine_needs_a_ready_model_dir(monkeypatch, tmp_path):
    """引擎加载依赖真实的模型目录：目录里没有模型时构造会失败。

    只验证"失败方向"（不加载真实模型、不联网）；服务把它收敛成错误事件。
    """
    _patch_config(monkeypatch, tmp_path)
    with pytest.raises(Exception):
        _load_engine()
