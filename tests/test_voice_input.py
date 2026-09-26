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


# ─── 任务 1.15：UI 侧只剩门面与轮询，业务逻辑在 services/ ─────


def test_ui_shims_reexport_identical_objects():
    """转发 shim 必须是同一批对象（对照 tests/test_layering.py 的做法）。

    这三个文件是任务 1.15 从 ``ui/agent/voice/`` 整体搬到 ``services/voice/``
    的，原地留的 shim 只做再导出；一旦有人改成包装/子类化，这里立刻报错。
    """
    import services.voice as spkg
    import services.voice.models as smodels
    import services.voice.sensevoice as ssense
    import ui.agent.voice as upkg
    import ui.agent.voice.models as umodels
    import ui.agent.voice.sensevoice as usense

    for name in (
        "MODEL_DIR_NAME",
        "MODEL_ZIP",
        "MODEL_URLS",
        "MODEL_MANUAL_HINT_URL",
        "SUPPORTED_MODEL_ZIPS",
        "ENCODER_PATTERNS",
        "DECODER_PATTERNS",
        "TOKENIZER_NAMES",
        "model_root",
        "model_dir",
        "is_model_ready",
        "model_version",
    ):
        assert getattr(umodels, name) is getattr(smodels, name), f"ui.agent.voice.models.{name} 不是同一对象"

    for name in ("pick_providers", "NumPyMelExtractor", "SenseVoiceEncoder", "SenseVoiceDecoder", "SenseVoice"):
        assert getattr(usense, name) is getattr(ssense, name), f"ui.agent.voice.sensevoice.{name} 不是同一对象"

    assert upkg.SenseVoice is spkg.SenseVoice
    assert upkg.pick_providers is spkg.pick_providers


def test_ui_module_reexports_service_constants_and_listener():
    """``ui.agent.voice_input`` 里的状态常量/监听接口都指向服务层同一对象。"""
    import services.voice_service as voice_service
    import ui.agent.voice_input as voice_input

    for name in (
        "SAMPLE_RATE",
        "MAX_RECORD_SECONDS",
        "STATE_IDLE",
        "STATE_DOWNLOADING",
        "STATE_LOADING",
        "STATE_RECORDING",
        "STATE_RECOGNIZING",
    ):
        assert getattr(voice_input, name) is getattr(voice_service, name), f"{name} 不是同一对象"
    assert voice_input.VoiceInputListener is voice_service.VoiceInputListener
    assert voice_input.VoiceService is voice_service.VoiceService
    assert voice_input._POLL_INTERVAL_MS == 100, "轮询间隔属于界面侧，必须留在 ui/"


def test_manager_facade_delegates_to_the_service_singleton():
    """``VoiceInputManager`` 是无状态门面：所有调用落在同一个服务实例上。"""
    import services.voice_service as voice_service
    from ui.agent.voice_input import VoiceInputManager

    manager = VoiceInputManager.instance()
    assert manager is VoiceInputManager.instance(), "门面仍是单例（与搬家前语义一致）"
    assert manager.get_state() == "idle"
    assert manager.is_recording() is False
    assert manager.is_available() == voice_service.VoiceService.instance().is_available()

    listener = voice_service.VoiceInputListener()
    manager.register(listener)
    voice_service.VoiceService.instance()._broadcast(("final", "喂"), active_only=False)
    assert manager.poll_once(listener) == [("final", "喂")]
    manager.unregister(listener)
    assert manager.poll_once(listener) == []


def test_run_session_in_thread_reproduces_old_thread_shape():
    """界面侧注入的调度器 = 搬家前 start() 里那个 daemon 线程（同名、同 daemon）。"""
    import threading

    from ui.agent.voice_input import _run_session_in_thread

    seen = {}
    done = threading.Event()

    def target():
        seen["name"] = threading.current_thread().name
        seen["daemon"] = threading.current_thread().daemon
        done.set()

    _run_session_in_thread(target)
    assert done.wait(10), "调度器必须在后台线程里跑起来（不能阻塞调用方）"
    assert seen == {"name": "VoiceInput", "daemon": True}


def test_service_is_resolved_lazily_without_appcontext():
    """没有 AppContext 时退回进程级单例；有 context 时优先用注册的服务。"""
    import types

    import services.voice_service as voice_service
    from ui.agent.voice_input import _get_voice_service

    assert _get_voice_service(None) is voice_service.VoiceService.instance()

    provided = voice_service.VoiceService()
    owner = types.SimpleNamespace(context=types.SimpleNamespace(require=lambda name: provided))
    assert _get_voice_service(owner) is provided

    broken = types.SimpleNamespace(
        context=types.SimpleNamespace(require=lambda name: (_ for _ in ()).throw(RuntimeError("未注册")))
    )
    assert _get_voice_service(broken) is voice_service.VoiceService.instance()
