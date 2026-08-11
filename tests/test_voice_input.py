"""语音输入模块测试 - 模型路径与解压逻辑（纯逻辑，不依赖麦克风）"""

import sys
import types
import zipfile

import pytest


def _patch_config(monkeypatch, tmp_path):
    """将 config 模块指向临时目录（patch sys.modules）"""
    fake_config = types.SimpleNamespace(base_dir=tmp_path)
    fake_mod = types.SimpleNamespace(config=fake_config)
    monkeypatch.setitem(sys.modules, "config", fake_mod)


def test_model_dir_location(monkeypatch, tmp_path):
    """模型目录应位于 <base_dir>/models/voice/sensevoice"""
    from ui.agent.voice import models as models_mod

    _patch_config(monkeypatch, tmp_path)
    assert models_mod.model_dir() == tmp_path / "models" / "voice" / "sensevoice"


def test_is_model_ready(monkeypatch, tmp_path):
    """模型文件齐全时 is_model_ready 返回 True"""
    from ui.agent.voice import models as models_mod

    _patch_config(monkeypatch, tmp_path)
    d = tmp_path / "models" / "voice" / "sensevoice"
    d.mkdir(parents=True)
    assert models_mod.is_model_ready() is False

    (d / "SenseVoice-Encoder.int8.onnx").write_bytes(b"e")
    (d / "SenseVoice-CTC.int8.onnx").write_bytes(b"c")
    (d / "tokenizer.bpe.model").write_bytes(b"t")
    assert models_mod.is_model_ready() is True


def test_is_model_ready_fp16(monkeypatch, tmp_path):
    """fp16 命名同样被识别为就绪"""
    from ui.agent.voice import models as models_mod

    _patch_config(monkeypatch, tmp_path)
    d = tmp_path / "models" / "voice" / "sensevoice"
    d.mkdir(parents=True)
    (d / "SenseVoice-Encoder.fp16.onnx").write_bytes(b"e")
    (d / "SenseVoice-CTC.fp16.onnx").write_bytes(b"c")
    (d / "tokenizer.bpe.model").write_bytes(b"t")
    assert models_mod.is_model_ready() is True


def test_extract_model_flat(tmp_path):
    """平铺结构 zip 解压"""
    from ui.agent.voice_input import VoiceInputManager

    zip_path = tmp_path / "model.zip"
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("SenseVoice-Encoder.int8.onnx", b"enc")
        zf.writestr("SenseVoice-CTC.int8.onnx", b"ctc")
        zf.writestr("tokenizer.bpe.model", b"tok")
    VoiceInputManager._extract_model(zip_path, dest)
    assert (dest / "SenseVoice-Encoder.int8.onnx").read_bytes() == b"enc"
    assert (dest / "SenseVoice-CTC.int8.onnx").read_bytes() == b"ctc"
    assert (dest / "tokenizer.bpe.model").read_bytes() == b"tok"


def test_extract_model_nested(tmp_path):
    """嵌套目录结构 zip 解压（SenseVoice-Small/Sensevoice-Small-ONNX/...）"""
    from ui.agent.voice_input import VoiceInputManager

    zip_path = tmp_path / "model.zip"
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("SenseVoice-Small/Sensevoice-Small-ONNX/SenseVoice-Encoder.fp16.onnx", b"enc")
        zf.writestr("SenseVoice-Small/Sensevoice-Small-ONNX/SenseVoice-CTC.fp16.onnx", b"ctc")
        zf.writestr("SenseVoice-Small/Sensevoice-Small-ONNX/tokenizer.bpe.model", b"tok")
    VoiceInputManager._extract_model(zip_path, dest)
    assert (dest / "SenseVoice-Encoder.fp16.onnx").read_bytes() == b"enc"
    assert (dest / "SenseVoice-CTC.fp16.onnx").read_bytes() == b"ctc"
    assert (dest / "tokenizer.bpe.model").read_bytes() == b"tok"


def test_extract_model_missing_file(tmp_path):
    """缺少必要文件时抛出异常"""
    from ui.agent.voice_input import VoiceInputManager

    zip_path = tmp_path / "model.zip"
    dest = tmp_path / "out"
    dest.mkdir()
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("SenseVoice-Encoder.int8.onnx", b"enc")
    with pytest.raises(RuntimeError):
        VoiceInputManager._extract_model(zip_path, dest)
