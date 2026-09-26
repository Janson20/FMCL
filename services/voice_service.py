"""语音输入服务 —— 录音 / 识别 / 模型管理（与界面完全无关）

实现整体搬自 ``ui/agent/voice_input.py`` 的 ``VoiceInputManager``：录音会话
状态机、监听者事件队列、模型下载/解压/导入、引擎加载与识别调用。
搬移后本模块遵守 ``services/`` 的三条硬性约束：

1. **不 import 任何 GUI 库**（tkinter / customtkinter / PySide6 / ``ui.*``）；
2. **不自己创建线程**：``begin_session()`` 只做状态转移，并把阻塞的会话主体
   交给调用方注入的调度器（``set_session_runner()``，与
   ``app.tasks.TaskRunner.set_scheduler()`` 同形）。阶段 1 由
   ``ui/agent/voice_input.py`` 注入"每次录音一个 daemon 线程"这一既有方式，
   阶段 2 换成 Qt 线程池时本模块一行都不用改；
3. **不弹窗、不碰界面**：所有提示都以 ``("state"/"progress"/"final"/"error", ...)``
   事件投递给监听者，由界面层决定怎么展示。本模块因此不需要 ``UIPort``
   （``self.ui``）—— 搬移前 ``VoiceInputManager`` 也从不直接弹窗。

线程模型（与搬移前逐字一致）：

- 采集与推理跑在调度器给的线程里；
- 状态 / 帧缓冲 / 事件队列分别由 ``threading.Lock`` / ``queue.Queue`` 保护；
- 界面在主线程轮询 ``poll_once(listener)``，工作线程绝不触碰界面对象
  （AGENTS.md：worker 线程永远不许碰 Tk）。

可注入的依赖（便于单测与阶段 2 的 QML 侧复用）::

    VoiceService(
        model_dir_fn=...,        # 默认 services.voice.models.model_dir
        is_model_ready_fn=...,   # 默认 services.voice.models.is_model_ready
        engine_factory=...,      # 默认本模块 _load_engine
        stream_factory=...,      # 默认 sounddevice.InputStream
    )

不注入时，"取依赖"这一步与搬移前的模块级调用完全等价（见交付报告"必要改动"）。
"""

import os
import queue
import shutil
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from logzero import logger

from services.base import Service
from services.i18n_service import _
from services.user_agent import USER_AGENT
from services.voice.models import (
    ENCODER_PATTERNS,
    DECODER_PATTERNS,
    TOKENIZER_NAMES,
    MODEL_MANUAL_HINT_URL,
    MODEL_URLS,
    MODEL_ZIP,
    is_model_ready,
    model_dir,
    model_version,
)

try:
    import numpy as np
    import sounddevice as sd

    _HAVE_AUDIO_DEPS = True
except Exception:
    _HAVE_AUDIO_DEPS = False

SAMPLE_RATE = 16000
MAX_RECORD_SECONDS = 120  # 最长录音时长（秒），超时自动停止

STATE_IDLE = "idle"
STATE_DOWNLOADING = "downloading"
STATE_LOADING = "loading"
STATE_RECORDING = "recording"
STATE_RECOGNIZING = "recognizing"


def _voice_engine_available() -> Tuple[bool, str]:
    """检查识别引擎依赖是否可用"""
    if not _HAVE_AUDIO_DEPS:
        return False, _("voice_error_missing_deps")
    try:
        import onnxruntime  # noqa: F401
        import sentencepiece  # noqa: F401
    except Exception:
        return False, _("voice_error_missing_deps")
    return True, ""


def _load_engine() -> Optional[object]:
    """延迟加载 SenseVoice 引擎（模型目录必须已就绪）"""
    from services.voice.sensevoice import SenseVoice

    return SenseVoice(str(model_dir()))


class VoiceInputListener:
    """语音输入事件监听接口（回调均在 Tk 主线程执行）"""

    def on_voice_state(self, state: str, message: str = "") -> None:
        raise NotImplementedError

    def on_voice_progress(self, percent: float, message: str = "") -> None:
        raise NotImplementedError

    def on_voice_final(self, text: str) -> None:
        raise NotImplementedError

    def on_voice_error(self, message: str) -> None:
        raise NotImplementedError


class VoiceService(Service):
    """语音输入服务 - 管理模型下载、录音与识别生命周期（进程级单例）"""

    #: AppContext 注册名
    name = "voice"
    #: 人类可读名称（日志 / 任务面板）
    label = "语音输入"

    _instance: Optional["VoiceService"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        context=None,
        *,
        model_dir_fn: Optional[Callable[[], Path]] = None,
        is_model_ready_fn: Optional[Callable[..., bool]] = None,
        engine_factory: Optional[Callable[[], Optional[object]]] = None,
        stream_factory: Optional[Callable[..., object]] = None,
    ) -> None:
        super().__init__(context)
        self._state: str = STATE_IDLE
        self._state_lock = threading.Lock()
        self._listener_queues: dict = {}
        self._listener_lock = threading.Lock()
        self._active_listener: Optional[VoiceInputListener] = None
        self._pending_listener: Optional[VoiceInputListener] = None
        self._session_frames: List[np.ndarray] = []
        self._session_frames_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._engine: Optional[object] = None
        #: 最近一次识别结果，只被 current_text() 读取（见 poll_once）
        self._last_text: str = ""
        #: "怎么跑一次录音会话"由调用方注入，服务自己不创建线程
        self._session_runner: Optional[Callable[[Callable[[], None]], None]] = None
        # ── 可注入依赖：None 表示用搬移前的模块级实现 ──
        self._model_dir_fn = model_dir_fn
        self._is_model_ready_fn = is_model_ready_fn
        self._engine_factory = engine_factory or _load_engine
        self._stream_factory = stream_factory

    @classmethod
    def instance(cls) -> "VoiceService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = VoiceService()
            return cls._instance

    # ─── 可注入依赖的取值入口（未注入时 == 搬移前的模块级调用）───

    def _model_dir(self) -> Path:
        """模型目录（默认 ``services.voice.models.model_dir()``）"""
        return self._model_dir_fn() if self._model_dir_fn is not None else model_dir()

    def _is_model_ready(self, model_path: Optional[Path] = None) -> bool:
        """模型是否完整（默认 ``services.voice.models.is_model_ready``）

        ``fn(model_path)`` 与搬移前的 ``is_model_ready()`` /
        ``is_model_ready(tmp)`` 逐个等价：该函数的签名本就是
        ``is_model_ready(model_path=None)``，传 None 即走默认目录。
        """
        fn = self._is_model_ready_fn or is_model_ready
        return fn(model_path)

    def _make_engine(self) -> Optional[object]:
        """创建识别引擎（默认本模块 ``_load_engine``）"""
        return self._engine_factory()

    def _open_stream(self, **kwargs):
        """打开音频输入流（默认 ``sounddevice.InputStream``，参数原样透传）"""
        factory = self._stream_factory or sd.InputStream
        return factory(**kwargs)

    def set_session_runner(self, runner: Optional[Callable[[Callable[[], None]], None]]) -> None:
        """注入"怎么跑一次录音会话"的调度器（服务本身不创建线程）

        与 ``app.tasks.TaskRunner.set_scheduler()`` 同形：服务只决定
        "要跑什么"（``run_session``），"怎么跑"由调用方注入 ——
        阶段 1 是 ``threading.Thread(..., daemon=True, name="VoiceInput")``
        （与搬移前完全一致），阶段 2 可以换成 Qt 线程池或 ``TaskRunner``。

        为 ``None`` 时 ``begin_session()`` 只做状态转移，调用方必须自己
        调用 ``run_session()``（服务不会偷偷起线程）。
        """
        self._session_runner = runner

    # ─── 状态与监听注册 ────────────────────────────────────────

    def get_state(self) -> str:
        with self._state_lock:
            return self._state

    def is_recording(self) -> bool:
        """当前是否正在录音（纯查询，不改变任何状态）"""
        return self.get_state() == STATE_RECORDING

    def is_available(self) -> Tuple[bool, str]:
        """检查语音输入所需的依赖是否可用"""
        return _voice_engine_available()

    def register(self, listener: VoiceInputListener) -> None:
        with self._listener_lock:
            if listener not in self._listener_queues:
                self._listener_queues[listener] = queue.Queue()

    def unregister(self, listener: VoiceInputListener) -> None:
        with self._listener_lock:
            self._listener_queues.pop(listener, None)
            if self._active_listener is listener:
                self._active_listener = None

    def drain_events(self, listener: VoiceInputListener) -> List[tuple]:
        """主线程轮询：取出该监听者的事件队列"""
        q = self._listener_queues.get(listener)
        if q is None:
            return []
        events: List[tuple] = []
        try:
            while True:
                events.append(q.get_nowait())
        except queue.Empty:
            pass
        return events

    def poll_once(self, listener: VoiceInputListener) -> List[tuple]:
        """主线程"取一次结果"：非阻塞取走该监听者的全部待处理事件

        这是服务对界面暴露的轮询接口 —— **何时调用由界面决定**（阶段 1 是
        ``widget.after(100, ...)``；阶段 2 可以换成信号驱动）。服务里没有
        ``after`` / ``winfo_exists`` 这类 Tk 概念。
        """
        events = self.drain_events(listener)
        for event in events:
            if event and event[0] == "final":
                self._last_text = event[1]
        return events

    def current_text(self) -> str:
        """最近一次识别出的文本（还没有结果时为空串）"""
        return self._last_text

    def _broadcast(self, event: tuple, active_only: bool = False) -> None:
        with self._listener_lock:
            targets = [self._active_listener] if active_only else list(self._listener_queues)
        for listener in targets:
            q = self._listener_queues.get(listener)
            if q is not None:
                try:
                    q.put_nowait(event)
                except queue.Full:
                    try:
                        q.get_nowait()
                    except queue.Empty:
                        pass
                    q.put_nowait(event)

    def _set_state(self, state: str, message: str = "") -> None:
        with self._state_lock:
            self._state = state
        logger.info(f"[Voice] 状态切换: {state} {message}")
        self._broadcast(("state", state, message))

    # ─── 对外操作 ───────────────────────────────────────────────

    def toggle(self, listener: VoiceInputListener) -> None:
        """点击麦克风按钮：空闲则开始录音，录音中则停止并识别"""
        state = self.get_state()
        if state in (STATE_DOWNLOADING, STATE_LOADING, STATE_RECOGNIZING):
            return  # 正在准备/识别，忽略点击
        if state == STATE_RECORDING:
            with self._listener_lock:
                is_active = self._active_listener is listener
            self.stop()
            if not is_active:
                # 点击了另一个输入框的按钮：先停掉当前录音，再为新输入框开始
                with self._listener_lock:
                    self._pending_listener = listener
            return
        self.begin_session(listener)

    def begin_session(self, listener: VoiceInputListener) -> bool:
        """开始一次录音会话（状态转移 + 交给注入的调度器跑阻塞主体）

        Returns:
            True 表示会话已开始；False 表示当前状态不是空闲
            （与搬移前 ``start()`` 的提前返回一致）。
        """
        if self.get_state() != STATE_IDLE:
            return False
        with self._listener_lock:
            self._active_listener = listener
        self._stop_event = threading.Event()
        self._spawn_session()
        return True

    def _spawn_session(self) -> None:
        """把阻塞的 ``run_session`` 交给注入的调度器执行（本服务不创建线程）"""
        runner = self._session_runner
        if runner is None:
            self.log.warning(
                "未注入会话调度器（set_session_runner），录音会话不会自动开始；"
                "调用方需自行执行 run_session()"
            )
            return
        runner(self.run_session)

    def stop(self) -> None:
        """请求停止录音（识别与结果由工作线程异步完成）"""
        self._stop_event.set()

    def run_session(self) -> None:
        """跑完一次录音会话（**阻塞**；由调用方在线程/线程池里调度）"""
        self._record_worker()

    # ─── 工作线程 ───────────────────────────────────────────────

    def _record_worker(self) -> None:
        """工作线程：确保模型就绪 → 采集音频 → 停止后识别"""
        try:
            if not self._ensure_model():
                return
            self._set_state(STATE_LOADING, _("voice_loading_model"))
            if self._engine is None:
                self._engine = self._make_engine()
            self._session_frames.clear()

            self._set_state(STATE_RECORDING, _("voice_recording"))
            record_start = time.time()

            def audio_callback(indata, frames, time_info, status):
                if status:
                    logger.debug(f"[Voice] 录音状态: {status}")
                with self._session_frames_lock:
                    self._session_frames.append(indata.copy())
                # 超时自动停止
                if time.time() - record_start > MAX_RECORD_SECONDS:
                    self._stop_event.set()

            try:
                with self._open_stream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    dtype="float32",
                    blocksize=int(0.05 * SAMPLE_RATE),
                    callback=audio_callback,
                ):
                    while not self._stop_event.is_set():
                        time.sleep(0.05)
            except sd.PortAudioError:
                logger.error("[Voice] 未找到可用的麦克风设备")
                self._broadcast(("error", _("voice_error_no_mic")), active_only=True)
                return
            except Exception as e:
                logger.error(f"[Voice] 录音流异常: {e}", exc_info=True)
                self._broadcast(("error", _("voice_error_unknown", err=str(e))), active_only=True)
                return

            # 识别阶段
            self._set_state(STATE_RECOGNIZING, _("voice_recognizing"))
            with self._session_frames_lock:
                frames = list(self._session_frames)
                self._session_frames.clear()
            if frames:
                audio = np.concatenate(frames, axis=0).reshape(-1)
                text = self._engine.recognize(audio)
                if text:
                    logger.info(f"[Voice] 识别完成: {text[:80]}")
                    self._broadcast(("final", text), active_only=True)
        except Exception as e:
            logger.error(f"[Voice] 录音过程异常: {e}", exc_info=True)
            self._broadcast(("error", _("voice_error_unknown", err=str(e))), active_only=True)
        finally:
            self._finish_session()

    def _finish_session(self) -> None:
        """收尾：清除活动监听者并处理输入框切换"""
        with self._listener_lock:
            self._active_listener = None
        self._set_state(STATE_IDLE)
        # 录音中点击了另一个输入框的按钮：立即为新输入框开始
        with self._listener_lock:
            pending = self._pending_listener
            self._pending_listener = None
        if pending is not None:
            self.begin_session(pending)

    # ─── 模型下载 / 导入 ───────────────────────────────────────

    def _ensure_model(self) -> bool:
        """确保模型存在：不存在则自动下载并解压"""
        if self._is_model_ready():
            return True

        self._set_state(STATE_DOWNLOADING, _("voice_downloading_model"))
        d = self._model_dir()
        d.mkdir(parents=True, exist_ok=True)
        zip_path = d / MODEL_ZIP
        downloaded = False
        last_error = ""
        for url in MODEL_URLS:
            try:
                self._download(url, zip_path)
                downloaded = True
                break
            except Exception as e:
                last_error = str(e)
                logger.warning(f"[Voice] 模型下载失败 {url}: {e}")
                zip_path.unlink(missing_ok=True)
        if not downloaded:
            self._broadcast(("error", _("voice_error_download", err=last_error)), active_only=True)
            return False

        self._set_state(STATE_DOWNLOADING, _("voice_extracting_model"))
        try:
            self._extract_model(zip_path, d)
        except Exception as e:
            logger.error(f"[Voice] 模型解压失败: {e}")
            self._broadcast(("error", _("voice_error_model", err=str(e))), active_only=True)
            return False
        finally:
            try:
                zip_path.unlink(missing_ok=True)
            except Exception:
                pass
        if not self._is_model_ready():
            hint = _("voice_error_model_manual", url=MODEL_MANUAL_HINT_URL, path=str(self._model_dir()))
            self._broadcast(("error", hint), active_only=True)
            return False
        return True

    @staticmethod
    def _extract_model(zip_path: Path, dest: Path) -> None:
        """解压模型 zip 到模型目录（兼容两种目录结构，递归查找目标文件）"""
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
            targets: dict = {}
            for name in names:
                base = name.rsplit("/", 1)[-1]
                if any(Path(base).match(p) for p in ENCODER_PATTERNS):
                    targets.setdefault("encoder", name)
                elif any(Path(base).match(p) for p in DECODER_PATTERNS):
                    targets.setdefault("decoder", name)
                elif base in TOKENIZER_NAMES:
                    targets.setdefault("tokenizer", name)
            if len(targets) < 3:
                raise RuntimeError("模型压缩包缺少必要文件")
            for kind, name in targets.items():
                target = dest / name.rsplit("/", 1)[-1]
                with zf.open(name) as src, open(target, "wb") as dst:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        dst.write(chunk)

    def import_model_zip(self, zip_path: str) -> str:
        """从本地模型压缩包导入模型（同步阻塞，供设置窗口后台线程调用）

        校验压缩包内容并原子替换模型目录中的旧模型。

        Args:
            zip_path: 本地 zip 文件路径

        Returns:
            导入的模型版本标识 (fp16 / int8)

        Raises:
            RuntimeError: 压缩包无效、不是受支持的模型包或解压失败
        """
        zip_path = Path(zip_path)
        if not zip_path.exists() or zip_path.suffix.lower() != ".zip":
            raise RuntimeError(_("voice_import_invalid_file"))
        d = self._model_dir()
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / ".import_tmp"
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        try:
            try:
                self._extract_model(zip_path, tmp)
            except Exception as e:
                logger.warning(f"[Voice] 模型导入内容校验失败: {e}")
                raise RuntimeError(_("voice_import_wrong_package")) from e
            if not self._is_model_ready(tmp):
                raise RuntimeError(_("voice_import_wrong_package"))
            # 原子替换旧模型文件
            for old in list(d.glob("SenseVoice-*.onnx")) + [d / "tokenizer.bpe.model"]:
                try:
                    old.unlink()
                except Exception:
                    pass
            for f in list(tmp.iterdir()):
                shutil.move(str(f), str(d / f.name))
            logger.info(f"[Voice] 模型导入成功: {zip_path}")
            return model_version()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _download(self, url: str, dest: Path) -> None:
        """下载文件并实时上报进度"""
        req = urllib.request.Request(url, headers={"User-Agent": str(USER_AGENT)})
        tmp = dest.with_suffix(dest.suffix + ".part")
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length") or 0)
            received = 0
            with open(tmp, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    received += len(chunk)
                    if total:
                        percent = received * 100.0 / total
                        self._broadcast(("progress", percent, _("voice_downloading_model")))
        if tmp.exists():
            os.replace(tmp, dest)
