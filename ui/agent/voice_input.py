"""语音输入模块 - 基于 SenseVoice-Small 离线语音识别模型

首次使用自动从 GitHub Release 下载模型（约 225-414MB）到 <base_dir>/models/voice/。
模型与推理均跨平台（DirectML/CUDA/CPU 自适应），不依赖 Windows 专用 API。

使用流程: 点击开始录音 → 停止后自动识别 → 结果填入输入框

线程模型:
- VoiceInputManager 单例管理录音生命周期，可被多个输入框共享
- 工作线程: sounddevice 采集 16kHz float32 单声道音频，
  停止后用 SenseVoice 一次性识别
- 所有事件通过线程安全队列分发；UI 侧由 VoiceMicButton 在主线程轮询，
  工作线程绝不直接操作 Tk 组件
"""

import os
import queue
import shutil
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

import customtkinter as ctk
from logzero import logger

from ui.agent.voice.models import (
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
from ui.constants import COLORS, FONT_FAMILY, USER_AGENT
from ui.dialogs import show_notification
from ui.i18n import _

try:
    import numpy as np
    import sounddevice as sd

    _HAVE_AUDIO_DEPS = True
except Exception:
    _HAVE_AUDIO_DEPS = False

SAMPLE_RATE = 16000
MAX_RECORD_SECONDS = 120  # 最长录音时长（秒），超时自动停止
_POLL_INTERVAL_MS = 100

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
    from ui.agent.voice.sensevoice import SenseVoice

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


class VoiceInputManager:
    """语音输入管理器（单例）- 管理模型下载、录音与识别生命周期"""

    _instance: Optional["VoiceInputManager"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
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

    @classmethod
    def instance(cls) -> "VoiceInputManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = VoiceInputManager()
            return cls._instance

    # ─── 状态与监听注册 ────────────────────────────────────────

    def get_state(self) -> str:
        with self._state_lock:
            return self._state

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
        self.start(listener)

    def start(self, listener: VoiceInputListener) -> None:
        if self.get_state() != STATE_IDLE:
            return
        with self._listener_lock:
            self._active_listener = listener
        self._stop_event = threading.Event()
        self._worker = threading.Thread(target=self._record_worker, daemon=True, name="VoiceInput")
        self._worker.start()

    def stop(self) -> None:
        """请求停止录音（识别与结果由工作线程异步完成）"""
        self._stop_event.set()

    # ─── 工作线程 ───────────────────────────────────────────────

    def _record_worker(self) -> None:
        """工作线程：确保模型就绪 → 采集音频 → 停止后识别"""
        try:
            if not self._ensure_model():
                return
            self._set_state(STATE_LOADING, _("voice_loading_model"))
            if self._engine is None:
                self._engine = _load_engine()
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
                with sd.InputStream(
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
            self.start(pending)

    # ─── 模型下载 ───────────────────────────────────────────────

    def _ensure_model(self) -> bool:
        """确保模型存在：不存在则自动下载并解压"""
        if is_model_ready():
            return True

        self._set_state(STATE_DOWNLOADING, _("voice_downloading_model"))
        d = model_dir()
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
        if not is_model_ready():
            hint = _("voice_error_model_manual", url=MODEL_MANUAL_HINT_URL, path=str(model_dir()))
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
        d = model_dir()
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
            if not is_model_ready(tmp):
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


class VoiceMicButton(ctk.CTkButton, VoiceInputListener):
    """麦克风按钮 - 挂载到输入框旁，点击开始/停止录音，识别结果填入输入框

    录音中按钮变红显示"录音中"，再次点击停止；其他输入框的按钮也会同步
    显示录音状态，点击可将录音转移到该输入框。
    """

    def __init__(self, parent, entry, height: int = 34, width: int = 72, **kwargs):
        self._entry = entry
        self._recording = False
        self._busy = False
        self._poll_id: Optional[str] = None
        super().__init__(
            parent,
            text=_("voice_btn_idle"),
            width=width,
            height=height,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            command=self._on_click,
            **kwargs,
        )
        manager = VoiceInputManager.instance()
        ok, reason = manager.is_available()
        if not ok:
            self.configure(state=ctk.DISABLED, text=_("voice_btn_unavailable"), width=110)
        manager.register(self)
        self._apply_idle_style()
        self._schedule_poll()

    # ─── 轮询事件（主线程）────────────────────────────────────

    def _schedule_poll(self) -> None:
        if self._poll_id is None and self.winfo_exists():
            self._poll_id = self.after(_POLL_INTERVAL_MS, self._poll_once)

    def _poll_once(self) -> None:
        self._poll_id = None
        try:
            for event in VoiceInputManager.instance().drain_events(self):
                self._handle_event(event)
        except Exception as e:
            logger.debug(f"[Voice] 事件处理异常: {e}")
        self._schedule_poll()

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind == "state":
            self.on_voice_state(event[1], event[2] if len(event) > 2 else "")
        elif kind == "progress":
            self.on_voice_progress(event[1], event[2] if len(event) > 2 else "")
        elif kind == "final":
            self.on_voice_final(event[1])
        elif kind == "error":
            self.on_voice_error(event[1])

    # ─── 事件处理 ───────────────────────────────────────────────

    def on_voice_state(self, state: str, message: str = "") -> None:
        if state == STATE_RECORDING:
            self._recording = True
            self._busy = False
            self.configure(
                state=ctk.NORMAL,
                text=_("voice_btn_stop"),
                width=110,
                fg_color=COLORS["error"],
                hover_color=COLORS["error"],
                text_color="white",
            )
        elif state == STATE_RECOGNIZING:
            # _recording 保持 True：识别完成后 final 仍需送达本按钮
            self._busy = True
            self.configure(state=ctk.DISABLED, width=120, fg_color=COLORS["bg_medium"])
            self.configure(text=_("voice_recognizing")[:6])
        elif state in (STATE_DOWNLOADING, STATE_LOADING):
            self._recording = False
            self._busy = True
            self.configure(state=ctk.DISABLED, width=120, fg_color=COLORS["bg_medium"])
            self.configure(text=(message or _("voice_loading_model"))[:9])
        elif state == STATE_IDLE:
            self._recording = False
            self._busy = False
            self.configure(state=ctk.NORMAL)
            self._apply_idle_style()

    def on_voice_progress(self, percent: float, message: str = "") -> None:
        if self._busy:
            self.configure(text=f"{message[:6] or _('voice_downloading_model')} {percent:.0f}%")

    def on_voice_final(self, text: str) -> None:
        if not self._recording:
            return  # final 只送达发起录音的那个按钮
        text = text.strip()
        if not text:
            return
        entry = self._entry
        if entry is None or not entry.winfo_exists():
            return
        current = entry.get()
        if current:
            text = current.rstrip() + (" " if not current.endswith((" ", "\n")) else "") + text
        entry.delete(0, ctk.END)
        entry.insert(0, text)
        try:
            entry.focus_set()
            entry.icursor(len(text))
        except Exception:
            pass

    def on_voice_error(self, message: str) -> None:
        self._recording = False
        self._busy = False
        self.configure(state=ctk.NORMAL)
        self._apply_idle_style()
        try:
            show_notification(_("voice_error_title"), message, notify_type="error")
        except Exception:
            logger.error(f"[Voice] 错误: {message}")

    # ─── 样式 ───────────────────────────────────────────────────

    def _apply_idle_style(self) -> None:
        self.configure(
            text=_("voice_btn_idle"),
            width=72,
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["bg_light"],
            text_color=COLORS["text_primary"],
        )

    def refresh_theme(self) -> None:
        """主题切换后重新应用当前状态的颜色（保持录音/忙碌状态）"""
        if self._recording and not self._busy:
            self.configure(fg_color=COLORS["error"], hover_color=COLORS["error"])
        elif self._busy:
            self.configure(fg_color=COLORS["bg_medium"])
        else:
            self._apply_idle_style()

    # ─── 生命周期 ───────────────────────────────────────────────

    def _on_click(self) -> None:
        manager = VoiceInputManager.instance()
        ok, reason = manager.is_available()
        if not ok:
            self.on_voice_error(reason)
            return
        try:
            manager.toggle(self)
        except Exception as e:
            logger.error(f"[Voice] 切换录音失败: {e}")
            self.on_voice_error(_("voice_error_unknown", err=str(e)))

    def destroy(self) -> None:
        try:
            VoiceInputManager.instance().unregister(self)
        except Exception:
            pass
        if self._poll_id is not None:
            try:
                self.after_cancel(self._poll_id)
            except Exception:
                pass
            self._poll_id = None
        super().destroy()
