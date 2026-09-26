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

**任务 1.15 起本文件只剩界面部分**：录音 / VAD 前置 / 识别调用 / 模型下载与
导入等业务逻辑整体搬到了 ``services/voice_service.py``（``VoiceService``，
服务名 ``"voice"``）。本文件保留三样东西：

1. ``VoiceMicButton`` —— Tk 控件、``after`` 轮询、事件到控件状态的映射；
2. ``_POLL_INTERVAL_MS`` 与"什么时候开始/停止轮询"（服务层没有任何 Tk 概念，
   轮询完全由界面决定）；
3. ``_run_session_in_thread`` —— 把服务"要跑的那次会话"放进 daemon 线程，
   线程由**界面侧**创建并注入给服务（``VoiceService.set_session_runner``），
   服务自己不起线程。

``VoiceInputManager`` 现在是一个**无状态薄委托**门面：公开方法名与签名逐字
保持，方法体一律转发给服务。服务实例经 ``_get_voice_service()`` 惰性取得 ——
优先用 ``owner.context`` 里注册的 ``"voice"`` 服务，没有 AppContext 时退回
进程级单例（阶段 1 的 Tk 界面尚未接 AppContext，走的就是后者）。
"""

import threading
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import customtkinter as ctk
from logzero import logger

from app.context import current_context
from services.voice_service import (
    MAX_RECORD_SECONDS,  # 兼容再导出：界面侧不再直接使用
    SAMPLE_RATE,  # 兼容再导出：界面侧不再直接使用
    STATE_DOWNLOADING,
    STATE_IDLE,
    STATE_LOADING,
    STATE_RECOGNIZING,
    STATE_RECORDING,
    VoiceInputListener,
    VoiceService,
)
from ui.constants import COLORS, FONT_FAMILY
from ui.dialogs import show_notification
from ui.i18n import _

_POLL_INTERVAL_MS = 100


def _run_session_in_thread(run: Callable[[], None]) -> None:
    """服务注入用的调度器：每次录音一个 daemon 线程

    与搬移前 ``VoiceInputManager.start()`` 里那两行逐字等价
    （``threading.Thread(target=self._record_worker, daemon=True, name="VoiceInput")``
    然后 ``start()``）：线程名、daemon 标志、只允许一个录音会话的不变式都不变。
    区别只在于"创建线程"这件事现在归界面层做（服务不自己起线程）。
    """
    threading.Thread(target=run, daemon=True, name="VoiceInput").start()


def _get_voice_service(owner: object = None) -> VoiceService:
    """惰性取得语音服务实例（**没有 AppContext 也能工作**）

    1. 优先用装配层挂上来的上下文：``getattr(owner, "context", None)`` 里注册的
       ``"voice"`` 服务（阶段 1 的 Tk 界面尚未接 AppContext，这条分支目前不会
       命中；阶段 2 接通后自动生效）；
    2. 取不到就退回进程级单例 ``VoiceService.instance()``。**必须是单例**：
       ``register(listener)`` 与 ``poll_once(listener)`` 若落在不同实例上，
       录音事件就永远送不到控件（搬移前 ``VoiceInputManager.instance()`` 也是
       单例语义，所有麦克风按钮共享同一份录音状态）。

    退回单例时顺带把界面侧的会话调度器注入进去（幂等）。
    """
    context = getattr(owner, "context", None) or current_context()
    if context is not None:
        try:
            service = context.require("voice")
        except Exception as e:  # noqa: BLE001 - 未注册/端口异常一律退回单例
            logger.debug(f"[Voice] 从 context 取 voice 服务失败，退回单例: {e}")
        else:
            if isinstance(service, VoiceService):
                return service
    service = VoiceService.instance()
    service.set_session_runner(_run_session_in_thread)
    return service


class VoiceInputManager:
    """语音输入管理器（单例门面）- 转发到 ``services.voice_service.VoiceService``

    保留这个类（而不是让调用方直接改用 ``VoiceService``）是为了不动既有调用点：
    ``ui/app_base.py:166``、``ui/agent/agent_chat.py:928``（只用 ``VoiceMicButton``）、
    ``ui/windows/launcher_settings.py:2050``（``import_model_zip``）与
    ``tests/test_voice_input.py``（``_extract_model``）。本类不持有任何状态，
    每个方法都是一行转发。
    """

    _instance: Optional["VoiceInputManager"] = None
    _instance_lock = threading.Lock()

    def _service(self) -> VoiceService:
        """取服务实例（每次现取，保证与 AppContext 接通后立刻生效）"""
        return _get_voice_service(self)

    @classmethod
    def instance(cls) -> "VoiceInputManager":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = VoiceInputManager()
            return cls._instance

    # ─── 状态与监听注册（转发）─────────────────────────────────

    def get_state(self) -> str:
        return self._service().get_state()

    def is_recording(self) -> bool:
        return self._service().is_recording()

    def is_available(self) -> Tuple[bool, str]:
        """检查语音输入所需的依赖是否可用"""
        return self._service().is_available()

    def register(self, listener: VoiceInputListener) -> None:
        self._service().register(listener)

    def unregister(self, listener: VoiceInputListener) -> None:
        self._service().unregister(listener)

    def drain_events(self, listener: VoiceInputListener) -> List[tuple]:
        """主线程轮询：取出该监听者的事件队列"""
        return self._service().drain_events(listener)

    def poll_once(self, listener: VoiceInputListener) -> List[tuple]:
        """主线程"取一次结果"（转发；调用时机由界面决定）"""
        return self._service().poll_once(listener)

    def current_text(self) -> str:
        """最近一次识别出的文本"""
        return self._service().current_text()

    # ─── 对外操作（转发）───────────────────────────────────────

    def toggle(self, listener: VoiceInputListener) -> None:
        """点击麦克风按钮：空闲则开始录音，录音中则停止并识别"""
        self._service().toggle(listener)

    def start(self, listener: VoiceInputListener) -> None:
        """开始录音（线程由注入的调度器创建，见 _run_session_in_thread）"""
        self._service().begin_session(listener)

    def stop(self) -> None:
        """请求停止录音（识别与结果由工作线程异步完成）"""
        self._service().stop()

    def import_model_zip(self, zip_path: str) -> str:
        """从本地模型压缩包导入模型（同步阻塞，供设置窗口后台线程调用）"""
        return self._service().import_model_zip(zip_path)

    @staticmethod
    def _extract_model(zip_path: Path, dest: Path) -> None:
        """解压模型 zip（转发；tests/test_voice_input.py 直接调用这个静态入口）"""
        VoiceService._extract_model(zip_path, dest)


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
            for event in VoiceInputManager.instance().poll_once(self):
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
