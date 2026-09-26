"""TkUIPort —— CustomTkinter 版 UI 能力端口（阶段 1 的现役实现）。

线程模型
--------
1. 构造必须发生在 UI 主线程：``__init__`` 用 ``threading.get_ident()`` 记下主线程 id。
2. 每个公开方法都可以从任意线程调用：

   - 主线程调用 -> 就地执行（避免"自己等自己"死锁）；
   - worker 线程调用 -> 把请求（含 ``threading.Event``）放进队列，由主线程的轮询
     循环 ``_drain()`` 消费；需要返回值的方法阻塞等待结果，不需要的直接入队返回。

3. 轮询由 ``root.after(poll_interval_ms, self._drain)`` 驱动，**只在主线程排期**。
   绝不使用"worker 线程直接调 ``root.after(0, ...)``"的写法 —— 那条路径在 Tk 上
   不可靠（见 AGENTS.md 的开发经验），本端口存在的首要理由就是替掉它。
4. ``_timer`` 既保存回调 id，也兼作"已有一次待执行的 drain"标记，因此对话框
   ``wait_window`` 造成的嵌套事件循环不会让定时器指数增长。
5. 提示类方法（``show_info`` / ``show_warning`` / ``show_error``）默认**即发即忘**
   （worker 入队后立刻返回）；传 ``blocking=True`` 时改为**入队后阻塞等待**，直到
   主线程把对话框关掉才返回 —— 用于"必须先让用户看到提示、再继续后面的动作"的
   调用方（例如 GTNH 兼容性提示要在游戏启动前展示）。主线程调用时两种取值都是
   就地执行，天然阻塞，不需要额外分支。

超时语义
--------
- ``confirm`` / ``ask_text`` / ``choose`` 的等待上限默认 30 秒（``timeout_s`` 可覆盖）。
- 超时**不抛异常**，按"用户没回答"处理：``confirm`` 返回 ``default``，
  ``ask_text`` / ``choose`` 返回 ``None``，并记 warning 日志。
- ``blocking=True`` 的提示类调用同样受 ``timeout_s`` 限制（默认 30 秒）。**取舍**：
  超时后 worker 继续执行，但**弹窗不会消失**（端口没有"撤销已显示对话框"的能力，
  对话框关不关由用户决定）。也就是宁可出现"用户还没读完，流程已经继续"，也绝不
  让 worker 线程永久卡死 —— 后者会拖住整个启动/联机流程且无法恢复。
- ``show_progress(modal=True)`` 的阻塞行为与原 ``wait_window`` 一致，**没有超时**
  （调用方必须保证最终调用 ``close_progress``，否则主线程会一直等下去）。
  传入 ``wait_event``（``threading.Event``）时改为"窗口关闭或该事件置位"二者先到
  为准 —— 后台任务结束时置位它，可杜绝"close 信号先于窗口到达被丢弃"造成的死等。

复用的现有对话框
----------------
- 提示 / 确认 / 文本输入：``ui.dialogs.show_alert``、``show_confirmation``、
  ``show_input_dialog``（密码输入 ui.dialogs 里没有，本文件按同样风格补一个掩码输入框）。
- 通知：``ui.dialogs.show_notification``（右下角 Toast，复用其排队与淡出逻辑）。
- ``choose``（多选项）与 ``show_progress``（进度窗口）ui.dialogs 里没有，本文件用
  customtkinter 自行实现，配色与圆角沿用 ``ui.dialogs`` 风格，**不使用渐变**。

销毁与降级
----------
- ``start()`` 之前 ``is_available()`` 为 False；``stop()`` 或界面被销毁后同样为 False。
- 界面销毁 / ``stop()`` 之后到达的请求一律安全丢弃，不把 ``TclError`` 抛给业务线程。
- ``root is None``（或端口尚未 start）时整体退化成 ``NullUIPort`` 行为：
  ``confirm`` 返回 ``default``，``ask_text`` 返回 ``None``，``choose`` 返回 ``default``，
  ``set_clipboard`` 返回 ``False``，弹窗类只写日志。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional, Sequence

import customtkinter as ctk

from app.ports import Choice, ProgressReport
from ui.constants import COLORS, FONT_FAMILY

logger = logging.getLogger(__name__)

#: 问答类方法的默认等待上限（秒）
DEFAULT_TIMEOUT_S = 30.0
#: 默认轮询间隔（毫秒）
DEFAULT_POLL_MS = 40
#: ``show_progress`` 认识的扩展关键字参数（其余会被忽略）
_PROGRESS_KWARGS = ("heading", "detail", "cancel_label", "on_cancel", "modal", "wait_event")
#: ``ProgressReport.level`` -> 状态文字颜色
_LEVEL_COLORS = {
    "success": COLORS["success"],
    "warning": COLORS["warning"],
    "error": COLORS["error"],
}


@dataclass
class _Request:
    """一次跨线程 UI 请求。``event is None`` 表示调用方不等结果。"""

    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)
    event: Optional[threading.Event] = None
    fallback: Any = None
    result: Any = None
    error: Optional[BaseException] = None


@dataclass
class _ProgressWindow:
    """进度窗口的部件句柄。"""

    win: Any
    title: str
    status: Any
    detail: Any
    bar: Any
    indeterminate: bool = False


class TkUIPort:
    """``app.ports.UIPort`` 的 CustomTkinter 实现（线程安全）。"""

    def __init__(
        self,
        root: Any,
        poll_interval_ms: int = DEFAULT_POLL_MS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._root = root
        self._poll_interval_ms = max(5, int(poll_interval_ms))
        self._timeout_s = float(timeout_s)
        self._main_thread_id = threading.get_ident()
        self._queue: "Queue[_Request]" = Queue()
        self._timer: Optional[str] = None
        self._started = False
        self._stopped = False
        self._available = False
        self._progress_window: Optional[_ProgressWindow] = None
        self.log = logging.getLogger("ui.ports_tk")

    # ─── 线程与生命周期 ─────────────────────────────────────

    def _on_main_thread(self) -> bool:
        return threading.get_ident() == self._main_thread_id

    def _ready(self) -> bool:
        """端口当前是否可处理请求（只读标志，任意线程安全）。"""
        return self._started and not self._stopped and self._available

    def is_available(self) -> bool:
        """界面是否已就绪。任意线程可调用，界面销毁后返回 False 而非抛异常。

        主线程调用时顺带探测界面是否还在（销毁后立刻变 False）；worker 线程只读
        标志位 —— 绝不在非主线程里调用 ``winfo_exists()`` 之类的 Tk 方法。
        """
        if not self._ready():
            return False
        if self._on_main_thread():
            try:
                if not self._root.winfo_exists():
                    self._available = False
            except Exception:  # noqa: BLE001 - 根窗口已销毁
                self._available = False
        return self._available

    def start(self) -> None:
        """启动轮询循环（必须由 UI 主线程调用）。"""
        if not self._on_main_thread():
            self.log.error("TkUIPort.start() 必须在 UI 主线程调用，已忽略")
            return
        if self._root is None:
            self.log.warning("TkUIPort: root 为 None，端口保持不可用（NullUIPort 行为）")
            return
        if self._stopped:
            self.log.warning("TkUIPort 已停止，无法重新启动")
            return
        if self._started:
            return
        self._started = True
        self._available = True
        self._schedule()
        self.log.info("TkUIPort 已启动（轮询 %dms，问答超时 %.0fs）", self._poll_interval_ms, self._timeout_s)

    def stop(self) -> None:
        """停止轮询并放行所有等待中的请求（任意线程可调用，不抛异常）。"""
        self._stopped = True
        self._available = False
        timer, self._timer = self._timer, None
        if timer is not None and self._root is not None and self._on_main_thread():
            try:
                self._root.after_cancel(timer)
            except Exception:  # noqa: BLE001 - 界面已销毁
                pass
        self._release_pending()
        self.log.info("TkUIPort 已停止")

    def _release_pending(self) -> None:
        """丢弃队列里的请求，让等待者立刻拿到兜底值而不是干等到超时。"""
        while True:
            try:
                req = self._queue.get_nowait()
            except Empty:
                return
            req.result = req.fallback
            if req.event is not None:
                req.event.set()

    # ─── 主线程轮询 ─────────────────────────────────────────

    def _schedule(self) -> None:
        """排下一次轮询；``_timer`` 非空即表示已有待执行项，避免重复排期。"""
        if self._stopped or self._timer is not None or self._root is None:
            return
        try:
            self._timer = self._root.after(self._poll_interval_ms, self._drain)
        except Exception as e:  # noqa: BLE001 - 界面已销毁
            self._timer = None
            self._available = False
            self.log.warning("界面已销毁，TkUIPort 停止轮询: %s", e)

    def _drain(self) -> None:
        """轮询体：处理一批待办请求，处理完再排下一次。"""
        self._timer = None
        if self._stopped or self._root is None:
            return
        try:
            if not self._root.winfo_exists():
                self._available = False
                return
        except Exception as e:  # noqa: BLE001 - 界面已销毁
            self._available = False
            self.log.warning("界面已销毁，TkUIPort 停止轮询: %s", e)
            return
        while True:
            try:
                req = self._queue.get_nowait()
            except Empty:
                break
            self._handle(req)
        self._schedule()

    def _handle(self, req: _Request) -> None:
        """在主线程执行一条请求；任何异常都不得打断轮询。"""
        try:
            req.result = getattr(self, "_do_" + req.kind)(**req.payload)
        except Exception as e:  # noqa: BLE001 - 单条请求失败不能连累调用方
            req.error = e
            self.log.error("处理 %s 请求失败: %s", req.kind, e, exc_info=True)
        finally:
            if req.event is not None:
                req.event.set()

    def _call(
        self,
        kind: str,
        timeout_s: Optional[float] = None,
        wait: bool = False,
        fallback: Any = None,
        **payload: Any,
    ) -> Any:
        """统一的调用入口：主线程就地执行，worker 线程入队等待。"""
        if not self._ready():
            self.log.debug("%s: 端口不可用，直接返回兜底值（NullUIPort 行为）", kind)
            return fallback
        if self._on_main_thread():
            try:
                return getattr(self, "_do_" + kind)(**payload)
            except Exception as e:  # noqa: BLE001 - 不把 TclError 抛给业务代码
                self.log.error("就地执行 %s 失败: %s", kind, e, exc_info=True)
                return fallback
        req = _Request(kind=kind, payload=payload, fallback=fallback)
        if wait:
            req.event = threading.Event()
        self._queue.put(req)
        if not wait or req.event is None:
            return None
        limit = self._timeout_s if timeout_s is None else float(timeout_s)
        if not req.event.wait(limit):
            self.log.warning("%s 等待界面响应超时（%.1fs），按「用户没回答」处理", kind, limit)
            return fallback
        if req.error is not None:
            return fallback
        return req.result

    # ─── UIPort 公开 API ────────────────────────────────────

    def _alert(self, title: str, message: str, kwargs: Dict[str, Any]) -> None:
        """提示类方法（info / warning / error）的共用实现。

        - 默认即发即忘：worker 线程入队后立刻返回（不阻塞业务线程）。
        - ``blocking=True``：worker 线程入队后阻塞，直到主线程把对话框关掉才返回；
          主线程自己调用时 ``_call`` 走"就地执行"分支，本来就阻塞，这里不需要
          额外分支。超时上限沿用 ``timeout_s``（默认 30 秒），超时后返回、弹窗留着
          （见模块 docstring 的"超时语义"取舍说明）。
        """
        self._call(
            "show_alert",
            timeout_s=kwargs.get("timeout_s"),
            wait=bool(kwargs.get("blocking", False)),
            fallback=None,
            title=title,
            message=message,
        )

    def show_info(self, title: str, message: str, **kwargs: Any) -> None:
        """信息提示；``blocking=True`` 时等用户关掉弹窗再返回。"""
        self._alert(title, message, kwargs)

    def show_warning(self, title: str, message: str, **kwargs: Any) -> None:
        """警告提示；``blocking=True`` 时等用户关掉弹窗再返回。"""
        self._alert(title, message, kwargs)

    def show_error(self, title: str, message: str, **kwargs: Any) -> None:
        """错误提示；``blocking=True`` 时等用户关掉弹窗再返回。"""
        self._alert(title, message, kwargs)

    def confirm(self, title: str, message: str, default: bool = False, **kwargs: Any) -> bool:
        return bool(
            self._call(
                "confirm",
                timeout_s=kwargs.get("timeout_s"),
                wait=True,
                fallback=default,
                title=title,
                message=message,
            )
        )

    def ask_text(
        self, title: str, prompt: str, initial: str = "", password: bool = False, **kwargs: Any
    ) -> Optional[str]:
        return self._call(
            "ask_text",
            timeout_s=kwargs.get("timeout_s"),
            wait=True,
            fallback=None,
            title=title,
            prompt=prompt,
            initial=initial,
            password=password,
        )

    def choose(
        self,
        title: str,
        prompt: str,
        options: Sequence[Choice],
        default: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[str]:
        if not self._ready():
            # 无界面场景与 NullUIPort 一致：返回 default
            self.log.info("[choose->%s] %s: %s（端口不可用）", default, title, prompt)
            return default
        return self._call(
            "choose",
            timeout_s=kwargs.get("timeout_s"),
            wait=True,
            fallback=None,
            title=title,
            prompt=prompt,
            options=list(options),
            default=default,
            hint=kwargs.get("hint", ""),
        )

    def show_progress(self, report: ProgressReport, **kwargs: Any) -> None:
        payload = {k: v for k, v in kwargs.items() if k in _PROGRESS_KWARGS}
        self._call("show_progress", report=report, **payload)

    def close_progress(self, title: Optional[str] = None) -> None:
        self._call("close_progress", title=title)

    def set_clipboard(self, text: str) -> bool:
        return bool(self._call("set_clipboard", wait=True, fallback=False, text=text))

    def notify(self, message: str, level: str = "info", **kwargs: Any) -> None:
        self._call("notify", message=message, level=level)

    # ─── 请求实现（只在主线程上执行） ───────────────────────

    def _do_show_alert(self, title: str, message: str) -> None:
        """复用 ui.dialogs.show_alert（注意其参数顺序是 message, title）。"""
        from ui.dialogs import show_alert

        show_alert(message, title=title)

    def _do_confirm(self, title: str, message: str) -> bool:
        """复用 ui.dialogs.show_confirmation（只有「确认 / 取消」两个按钮）。

        端口 ``confirm(default=...)`` 里的 ``default`` 只作为超时/无回答时的兜底
        返回值，不改变按钮顺序与样式。
        """
        from ui.dialogs import show_confirmation

        return bool(show_confirmation(message, title=title))

    def _do_ask_text(
        self, title: str, prompt: str, initial: str = "", password: bool = False
    ) -> Optional[str]:
        if password:
            return self._ask_password(title, prompt, initial)
        from ui.dialogs import show_input_dialog

        return show_input_dialog(self._root, title, prompt, initial)

    def _do_choose(
        self,
        title: str,
        prompt: str,
        options: Sequence[Choice],
        default: Optional[str] = None,
        hint: str = "",
    ) -> Optional[str]:
        """多选项对话框（ui.dialogs 无此能力），风格与 ui.dialogs 保持一致。"""
        result: List[Optional[str]] = [None]
        dialog = ctk.CTkToplevel(self._root)
        dialog.title(title)
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self._root)
        dialog.attributes("-topmost", True)
        try:
            dialog.grab_set()
        except Exception:
            pass

        w, h = 500, 200 if hint else 170
        dialog.geometry(self._centered_geometry(dialog, self._root, w, h))

        ctk.CTkLabel(
            dialog,
            text=prompt,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_primary"],
            wraplength=420,
            justify=ctk.CENTER,
        ).pack(pady=(30, 8))

        if hint:
            ctk.CTkLabel(
                dialog,
                text=hint,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                text_color=COLORS["text_secondary"],
                wraplength=420,
                justify=ctk.CENTER,
            ).pack(pady=(0, 18))

        def _finish(value: Optional[str]) -> None:
            result[0] = value
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()

        # default 对应的选项按主按钮渲染；没有 default 时第一个选项为主按钮
        values = [o.value for o in options]
        primary = values.index(default) if default in values else 0

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(6, 0))
        for index, option in enumerate(options):
            is_primary = index == primary
            ctk.CTkButton(
                btn_frame,
                text=option.label,
                width=120,
                height=32,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color=COLORS["accent"] if is_primary else COLORS["bg_medium"],
                hover_color=COLORS["accent_hover"] if is_primary else COLORS["card_border"],
                text_color=COLORS["text_primary"],
                state="disabled" if option.disabled else "normal",
                command=lambda v=option.value: _finish(v),
            ).pack(side=ctk.LEFT, padx=6)

        dialog.protocol("WM_DELETE_WINDOW", lambda: _finish(None))
        dialog.wait_window()
        return result[0]

    def _do_show_progress(
        self,
        report: ProgressReport,
        heading: Optional[str] = None,
        detail: Optional[str] = None,
        cancel_label: str = "取消",
        on_cancel: Optional[Callable[[], None]] = None,
        modal: bool = False,
        wait_event: Optional[threading.Event] = None,
    ) -> None:
        """显示或更新进度窗口。

        窗口在主线程第一次调用时创建（``heading`` / ``cancel_label`` / ``on_cancel``
        这些建窗参数也只在这次生效）；``report.title is None`` 表示标题沿用上一次。

        ``modal=True`` 时主线程阻塞等待（等价原来的进度窗写法）：这是 Tk 的嵌套
        事件循环，``after`` 轮询照常运行，所以 worker 线程的进度上报仍会被应用、
        取消按钮也仍然可点。

        ``wait_event`` 是"后台任务已结束"的事件：等待窗口关闭或该事件置位，二者
        先到为准。它专门用来防住一种死等 —— ``close_progress`` 若在窗口建立之前
        就被轮询消费掉（例如下载秒退），单靠 ``wait_window`` 会永远等不到关闭信号。
        """
        pw = self._progress_window
        if pw is None or not self._window_alive(pw.win):
            pw = self._build_progress_window(report, heading, cancel_label, on_cancel)
            self._progress_window = pw
        self._update_progress_window(pw, report, detail)
        if modal and self._window_alive(pw.win):
            self._wait_modal(pw, wait_event)

    def _wait_modal(self, pw: _ProgressWindow, wait_event: Optional[threading.Event]) -> None:
        """主线程模态等待：窗口关闭，或 ``wait_event`` 置位。"""
        try:
            if wait_event is None:
                pw.win.wait_window()
                return
            flag = ctk.BooleanVar(master=self._root, value=False)

            def _tick() -> None:
                if wait_event.is_set() or not self._window_alive(pw.win):
                    flag.set(True)
                    return
                try:
                    pw.win.after(50, _tick)
                except Exception:  # noqa: BLE001 - 窗口已销毁
                    flag.set(True)

            pw.win.after(50, _tick)
            self._root.wait_variable(flag)
        except Exception as e:  # noqa: BLE001 - 界面被销毁时不要连累调用方
            self.log.warning("进度窗口模态等待异常退出: %s", e)

    def _do_close_progress(self, title: Optional[str] = None) -> None:
        """关闭进度窗口；``title`` 不为 None 时只关闭标题匹配的那个窗口。"""
        pw = self._progress_window
        if pw is None:
            return
        if title is not None and title != pw.title:
            self.log.debug("close_progress(%s) 与当前进度窗口 %r 不匹配，已忽略", title, pw.title)
            return
        self._progress_window = None
        try:
            pw.win.grab_release()
        except Exception:
            pass
        try:
            if pw.win.winfo_exists():
                pw.win.destroy()
        except Exception as e:  # noqa: BLE001 - 界面已销毁
            self.log.debug("销毁进度窗口失败: %s", e)

    def _do_set_clipboard(self, text: str) -> bool:
        """写入剪贴板；失败返回 False，由调用方降级提示（不假装成功）。"""
        try:
            self._root.clipboard_clear()
            self._root.clipboard_append(text)
            self._root.update()  # 让 Tk 真正接管剪贴板所有权
            return True
        except Exception as e:  # noqa: BLE001 - 剪贴板可能被其他程序占用
            self.log.warning("写入剪贴板失败: %s", e)
            return False

    def _do_notify(self, message: str, level: str = "info") -> None:
        """复用 ui.dialogs 的右下角 Toast 通知（图标留空：项目规范不使用 emoji）。"""
        from ui import dialogs

        if getattr(dialogs, "_app_ref", None) is None:
            dialogs.set_app_reference(self._root)  # 仅补默认值，不覆盖已有引用
        notify_type = level if level in dialogs.NOTIFY_BORDER_COLORS else "info"
        dialogs.show_notification("", message, "", notify_type=notify_type)

    # ─── 部件构建辅助 ───────────────────────────────────────

    def _build_progress_window(
        self,
        report: ProgressReport,
        heading: Optional[str],
        cancel_label: str,
        on_cancel: Optional[Callable[[], None]],
    ) -> _ProgressWindow:
        """按原 predownload 进度窗的结构建窗：480x220、置顶、模态、居中。"""
        title = report.title or "进度"
        win = ctk.CTkToplevel(self._root)
        win.title(title)
        win.resizable(False, False)
        win.configure(fg_color=COLORS["bg_dark"])
        win.transient(self._root)
        win.attributes("-topmost", True)
        try:
            win.grab_set()
        except Exception:
            pass
        win.geometry(self._centered_geometry(win, self._root, 480, 220))

        ctk.CTkLabel(
            win,
            text=heading or title,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(pady=(25, 5))

        status = ctk.CTkLabel(
            win,
            text=report.message,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        status.pack(pady=(0, 10))

        bar = ctk.CTkProgressBar(
            win, width=420, height=8, progress_color=COLORS["accent"], fg_color=COLORS["bg_medium"]
        )
        bar.set(0.0)
        bar.pack(pady=(5, 5))

        detail = ctk.CTkLabel(
            win, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=COLORS["text_secondary"]
        )
        detail.pack(pady=(0, 10))

        if on_cancel is not None:
            ctk.CTkButton(
                win,
                text=cancel_label,
                width=140,
                height=30,
                font=ctk.CTkFont(family=FONT_FAMILY, size=11),
                fg_color=COLORS["bg_medium"],
                hover_color=COLORS["card_border"],
                text_color=COLORS["text_primary"],
                command=on_cancel,
            ).pack(pady=(0, 15))

        # 关闭按钮与原进度窗一致：只触发取消，窗口交给 close_progress 收尾；
        # 没有取消回调时直接关窗，避免出现关不掉的窗口
        win.protocol("WM_DELETE_WINDOW", on_cancel if on_cancel is not None else win.destroy)
        return _ProgressWindow(win=win, title=title, status=status, detail=detail, bar=bar)

    def _update_progress_window(
        self, pw: _ProgressWindow, report: ProgressReport, detail: Optional[str]
    ) -> None:
        """把一次 ProgressReport 应用到进度窗口上。"""
        if report.title:
            pw.title = report.title
            try:
                pw.win.title(report.title)
            except Exception:
                pass
        if report.message:
            pw.status.configure(
                text=report.message,
                text_color=_LEVEL_COLORS.get(report.level, COLORS["text_secondary"]),
            )
        if report.determinate:
            if pw.indeterminate:
                try:
                    pw.bar.stop()
                    pw.bar.configure(mode="determinate")
                except Exception:
                    pass
                pw.indeterminate = False
            pw.bar.set(report.fraction)
            pw.detail.configure(text=detail if detail is not None else f"{report.current} / {report.total}")
        else:
            if not pw.indeterminate:
                try:
                    pw.bar.configure(mode="indeterminate")
                    pw.bar.start()
                except Exception:
                    pass
                pw.indeterminate = True
            pw.detail.configure(text=detail or "")

    def _ask_password(self, title: str, prompt: str, initial: str = "") -> Optional[str]:
        """掩码文本输入框。

        ``ui.dialogs.show_input_dialog`` 只有明文输入，这里按同风格补一个 ``show="*"``
        的变体；密码内容**不做 strip**（空格可能是密码的一部分）。
        """
        result: List[Optional[str]] = [None]
        dialog = ctk.CTkToplevel(self._root)
        dialog.title(title)
        dialog.resizable(False, False)
        dialog.configure(fg_color=COLORS["bg_dark"])
        dialog.transient(self._root)
        try:
            dialog.grab_set()
        except Exception:
            pass
        dialog.geometry(self._centered_geometry(dialog, self._root, 420, 220))

        ctk.CTkLabel(
            dialog,
            text=prompt,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_secondary"],
            wraplength=370,
        ).pack(pady=(25, 10))

        entry = ctk.CTkEntry(
            dialog,
            show="*",
            font=ctk.CTkFont(family=FONT_FAMILY, size=14),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            width=350,
        )
        entry.insert(0, initial)
        entry.pack(pady=(0, 20))
        entry.focus_set()

        def _close() -> None:
            try:
                dialog.grab_release()
            except Exception:
                pass
            dialog.destroy()

        def _ok() -> None:
            result[0] = entry.get()
            _close()

        def _cancel() -> None:
            result[0] = None
            _close()

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack()
        ctk.CTkButton(
            btn_frame,
            text="确认",
            width=90,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=_ok,
        ).pack(side=ctk.LEFT, padx=10)
        ctk.CTkButton(
            btn_frame,
            text="取消",
            width=90,
            fg_color=COLORS["bg_medium"],
            hover_color=COLORS["card_border"],
            command=_cancel,
        ).pack(side=ctk.LEFT, padx=10)

        dialog.bind("<Return>", lambda _e: _ok())
        dialog.bind("<Escape>", lambda _e: _cancel())
        dialog.protocol("WM_DELETE_WINDOW", _cancel)
        dialog.wait_window()
        return result[0]

    def _centered_geometry(self, widget: Any, parent: Any, w: int, h: int) -> str:
        """优先相对父窗口居中，父窗口不可用（未映射）时退回屏幕居中。"""
        try:
            if parent is not None and parent.winfo_exists():
                pw, ph = parent.winfo_width(), parent.winfo_height()
                if pw > 1 and ph > 1:
                    x = parent.winfo_x() + (pw - w) // 2
                    y = parent.winfo_y() + (ph - h) // 2
                    return f"{w}x{h}+{x}+{y}"
        except Exception:
            pass
        try:
            x = (widget.winfo_screenwidth() - w) // 2
            y = (widget.winfo_screenheight() - h) // 2
            return f"{w}x{h}+{x}+{y}"
        except Exception:
            return f"{w}x{h}"

    @staticmethod
    def _window_alive(win: Any) -> bool:
        try:
            return bool(win is not None and win.winfo_exists())
        except Exception:
            return False

    def __repr__(self) -> str:  # pragma: no cover
        state = "ready" if self._ready() else ("stopped" if self._stopped else "idle")
        return f"<TkUIPort {state} pending={self._queue.qsize()}>"
