"""QtUIPort —— ``app.ports.UIPort`` 的 Qt/QML 实现（阶段 2 任务 2.3）。

与 ``ui/ports_tk.py:TkUIPort`` **逐个方法对齐**（契约 ``docs/refactor/11`` 第 5.3 节），
只换底座：Tk 的 ``after`` 轮询加 ``wait_window`` 换成 ``QTimer`` 轮询加 ``QEventLoop``。

## 线程模型

1. 构造与 ``start()`` 必须在 Qt 主线程（拥有 ``QCoreApplication`` 的那条线程）；
   ``start()`` 在别的线程被调用时只记 error 日志并忽略（照抄 Tk 版的这条纪律）。
2. 每个公开方法都可以从任意线程调用：

   - 主线程调用 -> 就地执行（避免"自己等自己"的死锁）；
   - worker 线程调用 -> 请求（含 ``threading.Event``）入队，由主线程的 ``QTimer``
     轮询体 ``_drain()`` 消费；需要返回值的方法阻塞等待结果，不需要的直接入队返回。

3. 轮询**只在主线程排期**；worker 线程绝不触碰 QTimer 或任何 QObject（红线 3）。

## 为什么主线程等待必须用嵌套 QEventLoop

主线程上等宿主作答时**不能**用 ``event.wait()``：那样 Qt 就不再派发事件 —— 宿主的
QML 对话框永远送不回答案（必然超时），排空队列的 QTimer 也不再触发。所以这里用
``QEventLoop.exec()`` 开一个嵌套事件循环（Tk 版 ``wait_window`` 的等价物）：期间
QTimer 照常工作，worker 的请求仍会被处理、取消按钮仍然可点。进入嵌套循环前会补一次
``_schedule()`` —— 如果这次模态请求是 worker 发起的（``_drain`` 正在栈上、定时器已经
用掉了），不补这一次排期，worker 的 ``close_progress`` 就会永远排队等不到处理。

## 超时语义

- ``confirm`` / ``ask_text`` / ``choose`` 的等待上限默认 30 秒（与 Tk 版
  ``DEFAULT_TIMEOUT_S`` 一致），可用 ``timeout_s`` 覆盖。
- 超时**不抛异常**，按"用户没回答"处理：``confirm`` 返回 ``default``，
  ``ask_text`` / ``choose`` 返回 ``None``，并记 warning 日志。
- ``blocking=True`` 的提示类调用同样受 ``timeout_s`` 限制。
- 超时后调用方继续执行，但**弹窗不会消失**（端口没有"撤销已显示对话框"的能力，
  与 Tk 版同一取舍）：宁可"用户还没读完、流程已经继续"，也绝不让 worker 永久卡死。
- ``show_progress(modal=True)`` 与 Tk 版一样**没有超时**：调用方必须保证最终调用
  ``close_progress``（或传入 ``wait_event``，由后台任务置位来放行）。

## 与 ``TkUIPort`` 的差异（其余逐条对齐）

1. 不做 Tk 那种"打开即模态"的隐式阻塞：主线程调用 ``show_info`` 这类**非阻塞**
   请求会立刻返回（QML 对话框天生异步）。只有显式要求阻塞（``blocking=True`` /
   ``modal=True``）或问答类方法才会等待。
2. 主线程上的等待同样受 ``timeout_s`` 限制（Tk 的 ``wait_window`` 没有超时）——
   宁可返回兜底值，也不让主线程永久挂住整个界面。
3. 降级路径直接委托给内部的 ``NullUIPort``，返回值与日志与无界面场景逐字一致。
4. ``show_progress`` 的 ``cancel_label`` 默认空串（由 QML 宿主按 i18n 兜底），
   不在 Python 里硬编码中文（红线 10）。
5. 除 Tk 版也有的 ``__repr__`` 外另提供 ``describe()``（供 ``--dump-services`` /
   崩溃报告使用，风格与 ``services/base.py`` 一致）。

## 销毁与降级

- ``start()`` 之前 ``is_available()`` 为 False；``stop()`` 之后同样为 False。
- 未注入宿主（``host is None``）时 ``start()`` 只记 warning，端口保持不可用。
- 不可用时整体退化成 ``NullUIPort`` 行为：``confirm`` 返回 ``default``、
  ``ask_text`` 返回 ``None``、``choose`` 返回 ``default``、``set_clipboard`` 返回
  ``False``，弹窗类只写日志。
- ``stop()`` 会放行所有还在等待的调用方（队列里的，以及已经交给宿主但还没被回答
  的），让它们立刻拿到兜底值而不是干等到超时。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from queue import Empty, Queue
from typing import Any, Callable, Dict, List, Optional, Sequence

from PySide6.QtCore import QCoreApplication, QEventLoop, QThread, QTimer

from app.bridges.dialog_host import (
    FALLBACK_KEY,
    KIND_ASK_TEXT,
    KIND_CHOOSE,
    KIND_CONFIRM,
    KIND_ERROR,
    KIND_INFO,
    KIND_WARNING,
    NOTIFY_LEVELS,
    DialogHost,
    Reply,
)
from app.ports import Choice, NullUIPort, ProgressReport

logger = logging.getLogger(__name__)

#: 问答类方法的默认等待上限（秒）；与 ``ui/ports_tk.py`` 的 DEFAULT_TIMEOUT_S 对齐
DEFAULT_TIMEOUT_S = 30.0
#: 默认轮询间隔（毫秒）
DEFAULT_POLL_MS = 50
#: 轮询间隔下限（防止有人传 0 把主线程打满）
MIN_POLL_MS = 5
#: ``show_progress`` 认识的扩展关键字参数（其余会被忽略，与 Tk 版一致）
PROGRESS_KWARGS = ("heading", "detail", "cancel_label", "on_cancel", "modal", "wait_event")
#: 需要宿主作答的请求种类（其余是即发即忘）
REPLY_KINDS = frozenset({"show_alert", "confirm", "ask_text", "choose"})
#: 提示类方法的 level（与宿主 kind 同一套字符串）
ALERT_LEVELS = (KIND_INFO, KIND_WARNING, KIND_ERROR)


@dataclass
class _Request:
    """一次跨线程 UI 请求。``event is None`` 表示调用方不等结果。"""

    kind: str
    payload: Dict[str, Any] = field(default_factory=dict)
    event: Optional[threading.Event] = None
    fallback: Any = None
    result: Any = None
    error: Optional[BaseException] = None
    replied: bool = False
    reply: Optional[Reply] = None


class QtUIPort:
    """``app.ports.UIPort`` 的 Qt/QML 实现（线程安全）。

    Args:
        host: 对话框宿主（``app.bridges.dialog_host.DialogHost``）。``None`` 表示
            没有界面，端口整体退化为 ``NullUIPort`` 行为。
        poll_interval_ms: 主线程轮询周期（毫秒）；测试里调小可以加快收敛。
        timeout_s: 问答类方法的默认等待上限（秒），与 Tk 版一致默认 30 秒。
    """

    def __init__(
        self,
        host: Optional[DialogHost] = None,
        *,
        poll_interval_ms: int = DEFAULT_POLL_MS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._host = host
        self._poll_interval_ms = max(MIN_POLL_MS, int(poll_interval_ms))
        self._timeout_s = float(timeout_s)
        #: 没有 QCoreApplication 时的主线程判定依据（正常路径由 Qt 自己回答）
        self._main_thread_id = threading.get_ident()
        self._queue: "Queue[_Request]" = Queue()
        self._lock = threading.Lock()
        self._inflight: List[_Request] = []
        self._timer: Optional[QTimer] = None
        self._scheduled = False
        self._started = False
        self._stopped = False
        self._host_available = False
        self._null = NullUIPort(name="QtUIPort(降级)")
        self._progress_title: Optional[str] = None
        self._progress_done: Optional[threading.Event] = None
        self._modal_loop: Optional[QEventLoop] = None
        self.log = logging.getLogger("app.bridges.ui_port_qt")

    # ─── 线程与生命周期 ─────────────────────────────────────

    def _on_main_thread(self) -> bool:
        """当前线程是否为 Qt 主线程（拥有 ``QCoreApplication`` 的那条线程）。

        优先问 Qt：``QThread.currentThread()`` 在跨线程时与 ``app.thread()`` 不等，
        这正是我们要的判断。没有 QCoreApplication（例如纯逻辑单测）时退回
        "构造端口的那条线程"。
        """
        app = QCoreApplication.instance()
        if app is not None:
            try:
                return QThread.currentThread() == app.thread()
            except RuntimeError:  # C++ 对象已析构（进程收尾时可能出现）
                pass
        return threading.get_ident() == self._main_thread_id

    def _host_ready(self) -> bool:
        """问宿主是否可用。**只允许在主线程调用**（宿主是 QObject）。"""
        host = self._host
        if host is None:
            return False
        try:
            return bool(host.is_available())
        except Exception as e:  # noqa: BLE001 - 宿主探测失败一律按不可用处理
            self.log.warning("宿主 is_available() 抛异常，按不可用处理: %s", e)
            return False

    def _ready(self) -> bool:
        """端口当前是否可处理请求（任意线程安全，只读缓存标志）。"""
        return self._started and not self._stopped and self._host_available

    def is_available(self) -> bool:
        """界面是否已就绪。任意线程可调用，宿主消失后返回 False 而非抛异常。

        主线程调用时顺带探测宿主（QML 窗口被销毁后立刻变 False）；worker 线程只读
        缓存标志 —— 绝不在非主线程里碰宿主的 QObject。
        """
        if not self._started or self._stopped or self._host is None:
            return False
        if self._on_main_thread():
            self._host_available = self._host_ready()
        return bool(self._host_available)

    def start(self) -> None:
        """启动轮询循环（必须由 Qt 主线程调用）。"""
        if not self._on_main_thread():
            self.log.error("QtUIPort.start() 必须在 UI 主线程调用，已忽略")
            return
        if self._host is None:
            self.log.warning("QtUIPort: 未注入 DialogHost，端口保持不可用（NullUIPort 行为）")
            return
        if self._stopped:
            self.log.warning("QtUIPort 已停止，无法重新启动")
            return
        if self._started:
            return
        self._host_available = self._host_ready()
        self._started = True
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(self._drain)
        self._timer = timer
        self._schedule()
        self.log.info(
            "QtUIPort 已启动（轮询 %dms，问答超时 %.0fs，宿主 %s）",
            self._poll_interval_ms,
            self._timeout_s,
            type(self._host).__name__,
        )

    def stop(self) -> None:
        """停止轮询并放行所有等待中的请求（任意线程可调用，不抛异常）。"""
        self._stopped = True
        self._host_available = False
        # 只有主线程才碰 QTimer（红线 3）；worker 线程调用 stop() 时留着那一次
        # 待触发的轮询，它会自己发现 _stopped 并停止排期
        if self._timer is not None and self._on_main_thread():
            try:
                self._timer.stop()
            except RuntimeError:  # C++ 对象已析构
                pass
        self._scheduled = False
        self._release_pending()
        self._release_inflight()
        # 主线程正处于嵌套等待时直接退出它；其他线程只改标志，让嵌套循环的
        # QTimer 自己发现 _stopped 后退出（不跨线程碰 QEventLoop）。
        if self._on_main_thread() and self._modal_loop is not None:
            try:
                self._modal_loop.quit()
            except RuntimeError:
                pass
        self.log.info("QtUIPort 已停止")

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

    def _release_inflight(self) -> None:
        """放行"已经交给宿主、但宿主还没回答"的请求（界面销毁时的收尾）。"""
        with self._lock:
            waiting = list(self._inflight)
            self._inflight.clear()
        for req in waiting:
            if req.replied:
                continue
            req.replied = True
            req.result = req.fallback
            if req.event is not None:
                req.event.set()

    def _mark_inflight(self, req: _Request, active: bool) -> None:
        with self._lock:
            if active:
                self._inflight.append(req)
                return
            try:
                self._inflight.remove(req)
            except ValueError:
                pass

    # ─── 主线程轮询 ─────────────────────────────────────────

    def _schedule(self) -> None:
        """排下一次轮询；``_scheduled`` 为真即表示已有待执行项，避免重复排期。"""
        if self._stopped or self._scheduled or self._timer is None:
            return
        self._scheduled = True
        try:
            self._timer.start(self._poll_interval_ms)
        except RuntimeError as e:  # C++ 对象已析构
            self._scheduled = False
            self._host_available = False
            self.log.warning("Qt 定时器不可用，QtUIPort 停止轮询: %s", e)

    def _drain(self) -> None:
        """轮询体（只在主线程跑）：处理一批待办请求，处理完再排下一次。"""
        self._scheduled = False
        if self._stopped:
            return
        # 宿主可能已经被 QML 侧销毁（引擎重建 / 窗口关闭），每一轮都重新问一次
        self._host_available = self._host_ready()
        while True:
            try:
                req = self._queue.get_nowait()
            except Empty:
                break
            self._handle(req)
        self._schedule()

    def _new_request(self, kind: str, payload: Dict[str, Any], *, wait: bool, fallback: Any) -> _Request:
        req = _Request(kind=kind, payload=dict(payload), fallback=fallback)
        if wait:
            req.event = threading.Event()
        req.reply = self._make_reply(req)
        return req

    def _make_reply(self, req: _Request) -> Reply:
        """构造交给宿主的 ``reply``：先到先得，重复回答只记日志。"""

        def reply(value: Any) -> None:
            if req.replied:
                self.log.debug("%s 收到重复回答，已忽略后一次", req.kind)
                return
            req.replied = True
            req.result = value
            self._mark_inflight(req, False)  # 请求收尾了，不再需要 stop() 兜底放行
            if req.event is not None:
                req.event.set()

        return reply

    def _handle(self, req: _Request) -> None:
        """在主线程执行一条请求；任何异常都不得打断轮询。"""
        self._mark_inflight(req, True)
        try:
            handler = getattr(self, "_do_" + req.kind)
            if req.kind in REPLY_KINDS:
                # 结果由宿主的 reply 写进 req.result，绝不能拿处理函数的返回值覆盖它
                # （host.present() 返回 None，覆盖一次就等于所有答案都丢了）
                handler(reply=req.reply, **req.payload)
            else:
                req.result = handler(**req.payload)
        except Exception as e:  # noqa: BLE001 - 单条请求失败不能连累调用方
            if not req.replied:  # 已经拿到答案的请求不该因为收尾异常被降级
                req.error = e
            self.log.error("处理 %s 请求失败: %s", req.kind, e, exc_info=True)
        finally:
            if req.kind not in REPLY_KINDS or req.replied or req.error is not None:
                # 同步类请求就地收尾；问答类请求要等宿主 reply（或出错）才算收尾，
                # 期间留在 _inflight 里，好让 stop() 能把它放行
                self._mark_inflight(req, False)
            if req.event is not None and req.kind not in REPLY_KINDS:
                req.event.set()

    def _call(
        self, kind: str, timeout_s: Optional[float] = None, wait: bool = False, fallback: Any = None, **payload: Any
    ) -> Any:
        """统一的调用入口：主线程就地执行，worker 线程入队等待。"""
        if not self._ready():
            self.log.debug("%s: 端口不可用，直接返回兜底值（NullUIPort 行为）", kind)
            return fallback
        req = self._new_request(kind, payload, wait=wait, fallback=fallback)
        if self._on_main_thread():
            return self._call_inline(req, timeout_s)
        self._queue.put(req)
        if not wait or req.event is None:
            return None
        limit = self._timeout_s if timeout_s is None else float(timeout_s)
        if not req.event.wait(limit):
            self.log.warning("%s 等待界面响应超时（%.1fs），按「用户没回答」处理", kind, limit)
            self._mark_inflight(req, False)
            return fallback
        if req.error is not None:
            return fallback
        return req.result

    def _call_inline(self, req: _Request, timeout_s: Optional[float]) -> Any:
        """主线程就地执行一条请求；宿主异步作答时用嵌套事件循环等待。"""
        self._handle(req)
        if req.error is not None:
            return req.fallback
        if req.event is None or req.event.is_set():
            return req.result
        limit = self._timeout_s if timeout_s is None else float(timeout_s)
        self._enter_modal_loop(req.event, timeout_s=limit)
        if req.event.is_set():
            return req.result
        self.log.warning("%s 等待界面响应超时（%.1fs），按「用户没回答」处理", req.kind, limit)
        self._mark_inflight(req, False)  # 调用方已经走了，不必再等 stop() 兜底放行
        return req.fallback

    # ─── 嵌套事件循环 ───────────────────────────────────────

    def _enter_modal_loop(
        self, event: threading.Event, *, wait_event: Optional[threading.Event] = None, timeout_s: Optional[float] = None
    ) -> bool:
        """在主线程进入嵌套事件循环，直到 event / wait_event 置位或被 ``stop()``。

        Returns:
            True 表示等到了答案（event 或 wait_event 已置位）；False 表示超时、
            端口被停止、或没有 QCoreApplication —— 调用方据此回退到兜底值。

        循环里必须让 QTimer 继续跑，否则宿主没有机会把答案送回来；进入前补一次
        ``_schedule()`` 是为了保证"这次模态请求由 worker 发起"时队列仍在排空
        （见模块 docstring 的说明）。
        """
        if QCoreApplication.instance() is None:
            self.log.warning("没有 QCoreApplication，无法通过嵌套事件循环等待宿主作答")
            return False
        if self._modal_loop is not None:
            self.log.warning("已有嵌套等待在进行，新的等待会叠加（调用方可能重复弹窗）")
        self._schedule()
        loop = QEventLoop()
        self._modal_loop = loop
        ticker = QTimer()
        ticker.setInterval(self._poll_interval_ms)  # 轮询而非直接 quit：只认自己的事件

        def _tick() -> None:
            if event.is_set() or self._stopped:
                loop.quit()
            elif wait_event is not None and wait_event.is_set():
                loop.quit()

        ticker.timeout.connect(_tick)
        ticker.start()
        deadline: Optional[QTimer] = None
        if timeout_s is not None:
            deadline = QTimer()
            deadline.setSingleShot(True)
            deadline.timeout.connect(loop.quit)
            deadline.start(max(1, int(float(timeout_s) * 1000)))
        try:
            loop.exec()
        except Exception as e:  # noqa: BLE001 - 界面拆除时不要连累调用方
            self.log.warning("嵌套事件循环异常退出: %s", e)
        finally:
            ticker.stop()
            if deadline is not None:
                deadline.stop()
            self._modal_loop = None
        return bool(event.is_set() or (wait_event is not None and wait_event.is_set()))

    # ─── UIPort 公开 API ────────────────────────────────────

    def _degrade(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """端口不可用时按 ``NullUIPort`` 语义回答（连日志一起），两条路径不会漂移。"""
        self.log.debug("%s: 端口不可用，退化为 NullUIPort 行为", name)
        return getattr(self._null, name)(*args, **kwargs)

    def _alert(self, level: str, title: str, message: str, kwargs: Dict[str, Any]) -> None:
        """提示类方法（info / warning / error）的共用实现。

        默认即发即忘（worker 入队后立刻返回）；``blocking=True`` 时改为等宿主作答
        （用户关掉弹窗）或超时 —— 用于"必须先让用户看到、再继续后面的动作"的调用方
        （例如 GTNH 兼容性提示要在游戏启动前展示）。主线程调用时同样用嵌套事件循环
        等待，与 Tk 版"提示框是模态的"时序一致；区别只在于这里只有调用方**显式**
        要求阻塞时才等。
        """
        blocking = bool(kwargs.get("blocking", False))
        self._call(
            "show_alert",
            timeout_s=kwargs.get("timeout_s"),
            wait=blocking,
            fallback=None,
            title=title,
            message=message,
            level=level,
            blocking=blocking,
        )

    def show_info(self, title: str, message: str, **kwargs: Any) -> None:
        """信息提示；``blocking=True`` 时等用户关掉弹窗再返回。"""
        if not self._ready():
            return self._degrade("show_info", title, message, **kwargs)
        self._alert(KIND_INFO, title, message, kwargs)

    def show_warning(self, title: str, message: str, **kwargs: Any) -> None:
        """警告提示；``blocking=True`` 时等用户关掉弹窗再返回。"""
        if not self._ready():
            return self._degrade("show_warning", title, message, **kwargs)
        self._alert(KIND_WARNING, title, message, kwargs)

    def show_error(self, title: str, message: str, **kwargs: Any) -> None:
        """错误提示；``blocking=True`` 时等用户关掉弹窗再返回。"""
        if not self._ready():
            return self._degrade("show_error", title, message, **kwargs)
        self._alert(KIND_ERROR, title, message, kwargs)

    def confirm(self, title: str, message: str, default: bool = False, **kwargs: Any) -> bool:
        """确认框；``default`` 既作默认按钮提示，也作超时/无回答时的返回值。"""
        if not self._ready():
            return bool(self._degrade("confirm", title, message, default, **kwargs))
        return bool(
            self._call(
                "confirm",
                timeout_s=kwargs.get("timeout_s"),
                wait=True,
                fallback=default,
                title=title,
                message=message,
                default=default,
            )
        )

    def ask_text(
        self, title: str, prompt: str, initial: str = "", password: bool = False, **kwargs: Any
    ) -> Optional[str]:
        """文本输入；``password=True`` 时宿主应按掩码显示，且结果不做 strip。"""
        if not self._ready():
            return self._degrade("ask_text", title, prompt, initial, password, **kwargs)
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
        self, title: str, prompt: str, options: Sequence[Choice], default: Optional[str] = None, **kwargs: Any
    ) -> Optional[str]:
        """多选项选择；超时/无回答返回 ``None``，端口不可用时返回 ``default``。"""
        if not self._ready():
            # 无界面场景与 NullUIPort 一致：返回 default（而不是超时语义的 None）
            return self._degrade("choose", title, prompt, options, default, **kwargs)
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
        """显示/更新进度对话框；只认 ``PROGRESS_KWARGS`` 里的扩展参数。"""
        if not self._ready():
            self._degrade("show_progress", report, **kwargs)
            return
        payload = {k: v for k, v in kwargs.items() if k in PROGRESS_KWARGS}
        self._call("show_progress", report=report, **payload)

    def close_progress(self, title: Optional[str] = None) -> None:
        """关闭进度对话框；``title`` 不为 None 时只关闭标题匹配的那个。"""
        if not self._ready():
            self._degrade("close_progress", title)
            return
        self._call("close_progress", title=title)

    def set_clipboard(self, text: str) -> bool:
        """写入剪贴板；失败返回 False，由调用方降级提示（不假装成功）。"""
        if not self._ready():
            return bool(self._degrade("set_clipboard", text))
        return bool(self._call("set_clipboard", wait=True, fallback=False, text=text))

    def notify(self, message: str, level: str = "info", **kwargs: Any) -> None:
        """弹一条通知（Toast）；未知级别按 ``info`` 处理（与 Tk 版一致）。"""
        if not self._ready():
            self._degrade("notify", message, level, **kwargs)
            return
        self._call("notify", message=message, level=level)

    # ─── 请求实现（只在主线程上执行） ───────────────────────

    def _present(self, kind: str, payload: Dict[str, Any], reply: Optional[Reply]) -> None:
        """统一的宿主投递口：宿主或 reply 缺位都不至于抛异常（理论上到不了）。"""
        host = self._host
        if host is None:
            self.log.warning("宿主缺位，%s 请求直接按兜底值收尾", kind)
            if reply is not None:
                reply(payload.get(FALLBACK_KEY))
            return
        host.present(kind, payload, reply if reply is not None else (lambda _value: None))

    def _do_show_alert(
        self, title: str, message: str, level: str = KIND_INFO, blocking: bool = False, reply: Optional[Reply] = None
    ) -> None:
        """提示类：交给宿主展示；``reply`` 由宿主在弹窗关闭后调用（可异步）。"""
        safe_level = level if level in ALERT_LEVELS else KIND_INFO
        payload = {
            "title": title,
            "message": message,
            "level": safe_level,
            "blocking": bool(blocking),
            FALLBACK_KEY: None,
        }
        self._present(safe_level, payload, reply)

    def _do_confirm(self, title: str, message: str, default: bool = False, reply: Optional[Reply] = None) -> None:
        """确认框。

        与 Tk 版一致：``default`` 只作为超时/无回答时的兜底返回值与"默认按钮"提示，
        **不**改变按钮顺序与样式；按钮顺序与高亮由宿主决定。
        """
        payload = {"title": title, "message": message, "default": bool(default), FALLBACK_KEY: bool(default)}
        self._present(KIND_CONFIRM, payload, reply)

    def _do_ask_text(
        self, title: str, prompt: str, initial: str = "", password: bool = False, reply: Optional[Reply] = None
    ) -> None:
        """文本输入；``password=True`` 时宿主按掩码显示（掩码由宿主实现）。"""
        payload = {"title": title, "prompt": prompt, "initial": initial, "password": bool(password), FALLBACK_KEY: None}
        self._present(KIND_ASK_TEXT, payload, reply)

    def _do_choose(
        self,
        title: str,
        prompt: str,
        options: Sequence[Choice],
        default: Optional[str] = None,
        hint: str = "",
        reply: Optional[Reply] = None,
    ) -> None:
        """多选项对话框；``options`` 原样（``Choice`` 对象列表）交给宿主。"""
        payload = {
            "title": title,
            "prompt": prompt,
            "options": list(options),
            "default": default,
            "hint": hint,
            FALLBACK_KEY: None,
        }
        self._present(KIND_CHOOSE, payload, reply)

    def _do_show_progress(
        self,
        report: ProgressReport,
        heading: Optional[str] = None,
        detail: Optional[str] = None,
        cancel_label: str = "",
        on_cancel: Optional[Callable[[], None]] = None,
        modal: bool = False,
        wait_event: Optional[threading.Event] = None,
    ) -> None:
        """显示或更新进度对话框。

        端口只维护**一个**进度会话：``report.title`` 非空时记住标题（之后的
        ``report.title is None`` 表示沿用），``close_progress`` 结束会话。
        ``heading`` / ``cancel_label`` / ``on_cancel`` 是建窗参数，宿主只在首次
        创建时使用它们（与 Tk 版一致）。

        ``modal=True`` 时在主线程用嵌套事件循环等待，放行条件是"标题匹配的
        ``close_progress``"或 ``wait_event`` 置位 —— 与 Tk 版 ``wait_window``
        等价，且**没有超时**（调用方必须保证收尾）。
        """
        if self._progress_done is None:
            self._progress_done = threading.Event()
        if report.title:
            self._progress_title = report.title
        payload = {
            "report": report,
            "title": self._progress_title,
            "heading": heading,
            "detail": detail,
            "cancel_label": cancel_label,
            "on_cancel": on_cancel,
        }
        host = self._host
        if host is None:
            self.log.warning("宿主缺位，进度请求已忽略")
        else:
            host.show_progress(payload)
        if not modal:
            return
        if not self._on_main_thread():  # 防御：嵌套事件循环只能在主线程开
            self.log.warning("show_progress(modal=True) 只能在主线程等待，已按非模态处理")
            return
        self._enter_modal_loop(self._progress_done, wait_event=wait_event, timeout_s=None)

    def _do_close_progress(self, title: Optional[str] = None) -> None:
        """关闭进度对话框；``title`` 不为 None 时只关闭标题匹配的那个。"""
        if self._progress_title is None:
            self.log.debug("当前没有进度会话，close_progress(%s) 已忽略", title)
            return
        if title is not None and title != self._progress_title:
            self.log.debug("close_progress(%s) 与当前进度 %r 不匹配，已忽略", title, self._progress_title)
            return
        done, self._progress_done = self._progress_done, None
        self._progress_title = None
        host = self._host
        if host is not None:
            host.close_progress()
        if done is not None:
            done.set()

    def _do_set_clipboard(self, text: str) -> bool:
        """写入剪贴板；失败返回 False，由调用方降级提示（不假装成功）。"""
        host = self._host
        if host is None:
            return False
        try:
            return bool(host.set_clipboard(text))
        except Exception as e:  # noqa: BLE001 - 剪贴板可能被别的程序占用或宿主不支持
            self.log.warning("写入剪贴板失败: %s", e)
            return False

    def _do_notify(self, message: str, level: str = "info") -> None:
        """弹一条通知；未知级别按 ``info`` 处理（Tk 版同样按 info 兜底）。"""
        normalized = level if level in NOTIFY_LEVELS else "info"
        if normalized != level:
            self.log.debug("notify 收到未知级别 %r，已按 info 处理", level)
        host = self._host
        if host is not None:
            host.notify({"message": message, "level": normalized})

    # ─── 诊断 ───────────────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        """端口状态快照（供 ``--dump-services`` / 崩溃报告用，可直接 json.dumps）。"""
        return {
            "class": type(self).__name__,
            "started": bool(self._started),
            "stopped": bool(self._stopped),
            "available": bool(self.is_available()),
            "host": type(self._host).__name__ if self._host is not None else None,
            "host_available": bool(self._host_available),
            "poll_interval_ms": self._poll_interval_ms,
            "timeout_s": self._timeout_s,
            "pending": self._queue.qsize(),
            "inflight": len(self._inflight),
            "progress_title": self._progress_title,
            "modal_waiting": self._modal_loop is not None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        state = "ready" if self._ready() else ("stopped" if self._stopped else "idle")
        host = type(self._host).__name__ if self._host is not None else "None"
        return f"<QtUIPort {state} host={host} pending={self._queue.qsize()}>"


__all__ = ["DEFAULT_POLL_MS", "DEFAULT_TIMEOUT_S", "QtUIPort"]
