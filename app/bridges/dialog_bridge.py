"""对话框宿主（QML 版 ``Dialogs``）—— 阶段 2 任务 2.13。

## 它在链路的哪一环

``DialogHost``（``app/bridges/dialog_host.py``）是"把对话框画出来"的窄接口；
``QtUIPort``（``app/bridges/ui_port_qt.py``）是服务层看得见的端口。本模块是
``DialogHost`` 的 **QML 实现**，把请求交给 ``qml/components/dialogs/**`` 与
``qml/components/ToastHost.qml``：

    worker 线程 --> QtUIPort（队列 + QTimer + 嵌套事件循环）
                      --> DialogBridge（主线程，本模块）
                            --> QML 宿主（DialogHost.qml / ToastHost.qml）
                                  --> 用户作答 --> submitDialog/cancelDialog --> reply(...)

## 线程纪律（硬性，契约红线 3）

``is_available`` / ``present`` / ``show_progress`` / ``close_progress`` / ``notify`` /
``set_clipboard`` 这六个 ``DialogHost`` 方法**只在 Qt 主线程被调用** —— 这是
``QtUIPort`` 保证的：它的队列轮询体（``_drain``）在主线程上跑，worker 的请求先入队，
由轮询体在主线程里交给宿主；主线程直接调用时也是就地执行。所以本模块**不需要**自己做
跨线程封送，可以放心 emit 信号、碰 QTimer 与 QML。

即便如此，本模块仍对"万一被 worker 调到"做兜底：``present`` 立刻用 payload 的兜底值
收尾、``set_clipboard`` 直接返回 False（写剪贴板必须在主线程），都**不碰**任何 Qt 对象。
判据是 ``QThread.currentThread() == QCoreApplication.instance().thread()``
（与 ``ui_port_qt.QtUIPort._on_main_thread`` 同一个判据，没有 Qt 应用时退回"构造它的线程"）。

## 就绪握手：``markReady()`` / ``markClosing()``

``is_available()`` 必须回答"QML 窗口现在能不能弹窗"，否则启动早期（QML 还没加载）
``QtUIPort`` 会把请求丢进一个不存在的界面。做法是让 **QML 宿主自己说话**：

* ``DialogHost.qml`` / ``ToastHost.qml`` 在 ``Component.onCompleted`` 里调
  ``Dialogs.markReady()``；
* 在 ``Component.onDestruction`` 里调 ``Dialogs.markClosing()``。

``markClosing()`` 会**放行所有还在等待的调用方**（用兜底值收尾）、停掉 Toast 定时器并
清空队列、丢弃进度会话 —— 这就是 A-16/M-45「退出时清理队列」在 Qt 侧的落点。
``main_qml.py`` 的退出链由任务 2.14 负责，本模块只提供槽与这个自动握手。

只靠握手不够稳：如果引擎被销毁而 QML 没走到 ``onDestruction``，``available`` 会一直是
True。所以 ``is_available()`` 在主线程上还会顺带看一眼 ``engine.rootObjects()``
（引擎已注入时），空了就报 False —— 与 ``QtUIPort`` 每轮重新问宿主是同一个思路。

## 不写业务规则（契约第三节红线 4）

文案、兜底值、要不要阻塞、重试策略全在调用方（服务层已经用 ``_()`` 翻译好文案，
端口已经算好 ``payload['fallback']``）。本模块只做两件事：**把 payload 变成 QML 能吃的
形状**、**把用户的答案交回 reply**。唯一的"判断"是 kind → 组件的映射与参数归一化。

## kind / payload（照 ``dialog_host.py`` 顶部的契约表实现）

| kind | 本模块额外补进 QML payload 的键 |
|------|--------------------------------|
| ``info`` / ``warning`` / ``error`` | ``icon``（按 level 取图标名，仅作默认值） |
| ``confirm`` | ``default``(bool) 原样透出（默认按钮提示） |
| ``ask_text`` | ``initial`` / ``password`` |
| ``choose`` | ``options``（``Choice`` 对象 → dict）、``default``、``hint`` |

共同的键：``id`` ``kind`` ``title`` ``message`` ``prompt`` ``level`` ``blocking``
``fallback``。``fallback`` **原样透出**：QML 侧不许自己猜兜底值，答不上来就把这个值回填。

**未知 kind 也照样发给 QML**，由 QML 宿主决定 —— 它没有对应组件时立刻回
``payload['fallback']``（``DialogHost.qml`` 的 ``cancelDialog`` 分支）。这条是"绝对不能让
调用方干等到超时"的兜底：本模块在不可用/非主线程时也已经用兜底值收尾了，两处都有测试。

## Toast：为什么 Python 管寿命、QML 管淡出

* **超时与上限在本模块**（``QTimer`` + ``MAX_TOASTS``）：``activeToasts()`` 是诊断与
  退出清理的真值来源，必须与界面无关地成立；
* **淡入淡出在 QML**：PySide 侧发 ``toastsChanged``，``ToastHost.qml`` 对账
  ``Dialogs.activeToasts()``，发现"桥里没了的"就先淡出再销毁自己的委托 ——
  这样"淡出动画"和"数据真值"各归其位，不需要两个定时器抢同一个 4500ms。

## 进度会话：与端口的标题语义对齐

端口只维护**一个**进度会话，``report.title`` 为 None 表示"沿用上一个"，``close_progress``
只在标题匹配时才真的关（``ui_port_qt._do_close_progress``）。本模块的簿记与之一致：
首次 ``show_progress`` 记住标题，之后的 ``title`` 为空就沿用；``heading`` /
``cancel_label`` / ``on_cancel`` 是**建窗参数，只在首次生效**（与 ``ui/ports_tk.py``
的 ``_build_progress_window`` 逐字对齐）。``cancelProgress()`` 只转调 ``on_cancel``，
**不自己关窗** —— 旧实现也是"点了取消由任务自己收尾"。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from PySide6.QtCore import Property, QCoreApplication, QObject, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

from app.bridges.dialog_host import (
    ALERT_KINDS,
    FALLBACK_KEY,
    KIND_ASK_TEXT,
    KIND_CHOOSE,
    KIND_CONFIRM,
    KIND_ERROR,
    KIND_INFO,
    KIND_WARNING,
    NOTIFY_LEVELS,
    PRESENT_KINDS,
    Reply,
)

logger = logging.getLogger(__name__)

#: 契约第四节冻结的 QML 注册名（入口 `register_bridges` 注册成上下文属性）。
QML_NAME = "Dialogs"

#: kind → QML 组件名。**真正的选择在 `qml/components/dialogs/DialogHost.qml` 里**
#: （QML 才是"有哪些组件"的权威），这份表只用于诊断输出与文档。
KIND_COMPONENT: Dict[str, str] = {
    KIND_INFO: "MessageDialog",
    KIND_WARNING: "MessageDialog",
    KIND_ERROR: "MessageDialog",
    KIND_CONFIRM: "ConfirmDialog",
    KIND_ASK_TEXT: "TextInputDialog",
    KIND_CHOOSE: "ChoiceDialog",
}

#: Toast 默认显示时长（毫秒）。与旧界面 `ui/dialogs.py:show_notification` 的 4500 一致。
DEFAULT_TOAST_DURATION_MS = 4500
#: Toast 时长下限（防止调用方传 0 把队列刷成空转）。
MIN_TOAST_DURATION_MS = 300
#: 同屏最多几条 Toast。旧实现**没有上限**（`ui/dialogs.py:98-110` 只管往上叠），
#: 6 条之后就会跑出窗口上沿；这里按"最多 6 条 + 挤掉最旧的一条"处理，理由见
#: `qml/components/ToastHost.qml` 与 `poc/dialog_e2e_2_13.txt`。
MAX_TOASTS = 6
#: Toast 尺寸 / 间距 / 边距（像素）。取值抄自旧界面 `ui/dialogs.py:61`：280x72、
#: 间距 8、距窗口右下角 16。QML 侧读这些常量做排版，测试据此断言堆叠方向。
TOAST_WIDTH = 280
TOAST_HEIGHT = 72
TOAST_GAP = 8
TOAST_MARGIN = 16

#: 按键求答案的 kind（用于诊断输出）。
_REPLY_KINDS = frozenset({KIND_CONFIRM, KIND_ASK_TEXT, KIND_CHOOSE})


@dataclass
class _Dialog:
    """一条待答的对话框请求。``replied`` 是"只认第一次作答"的哨兵。"""

    id: int
    kind: str
    payload: Dict[str, Any]
    reply: Reply
    replied: bool = False
    answer: Any = None


@dataclass
class _Progress:
    """当前进度会话（同时只有一个，与端口的语义一致）。"""

    id: int
    title: str = ""
    heading: str = ""
    cancel_label: str = ""
    on_cancel: Optional[Callable[[], None]] = None
    revision: int = 0
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _Toast:
    """一条 Toast。``timer`` 是本模块拥有的到期定时器（到期即从队列里摘掉）。"""

    id: int
    message: str
    title: str = ""
    subtitle: str = ""
    level: str = "info"
    icon: str = ""
    duration_ms: int = DEFAULT_TOAST_DURATION_MS
    timer: Optional[QTimer] = None


class DialogBridge(QObject):
    """上下文属性 ``Dialogs``：``DialogHost`` 的 QML 实现。

    线程前提：**只许在主线程使用**（见模块文档）。本类不起线程、不做跨线程封送。
    """

    #: 有新的问答请求（``present``）。payload 见模块文档的表格，含 ``id``。
    dialogRequested = Signal("QVariantMap")
    #: 某条问答请求结束了（用户作答 / 兜底收尾 / 窗口销毁放行）。QML 据此移除弹窗。
    dialogClosed = Signal(int)
    #: 进度会话变化（首次创建或每次 ``show_progress`` 上报）。
    progressChanged = Signal("QVariantMap")
    #: 进度会话结束。
    progressClosed = Signal()
    #: 新来一条 Toast。
    toastRequested = Signal("QVariantMap")
    #: Toast 队列变化（新增 / 到期移除 / 被挤掉 / 清空）。QML 据此对账。
    toastsChanged = Signal()
    #: 问答队列变化（``dialogs`` 属性的 NOTIFY；`dialogRequested` 带参数，不适合当 NOTIFY）。
    dialogsChanged = Signal()
    #: 就绪状态变化（QML 宿主握手 / 窗口销毁）。
    availabilityChanged = Signal()

    def __init__(self, engine: Optional[Any] = None, parent: Optional[QObject] = None) -> None:
        """
        Args:
            engine: QML 引擎。``None`` 时只靠 ``markReady()`` 握手判断可用性；
                注入后 ``is_available()`` 会额外确认 ``engine.rootObjects()`` 还在。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._engine = engine
        self._attached = False
        self._main_thread_id = threading.get_ident()
        self._dialogs: Dict[int, _Dialog] = {}
        self._toasts: List[_Toast] = []
        self._progress: Optional[_Progress] = None
        self._next_id_value = 0
        self._evicted = 0
        self._expired = 0
        self.log = logging.getLogger("app.bridges.dialog_bridge")

    # ─── 线程与生命周期 ─────────────────────────────────────

    def _on_main_thread(self) -> bool:
        """当前线程是否为 Qt 主线程（判据与 ``QtUIPort._on_main_thread`` 一致）。"""
        app = QCoreApplication.instance()
        if app is not None:
            try:
                return QThread.currentThread() == app.thread()
            except RuntimeError:  # C++ 对象已析构（进程收尾时可能出现）
                pass
        return threading.get_ident() == self._main_thread_id

    def use_engine(self, engine: Any) -> None:
        """由装配方显式注入 QML 引擎（``main_qml.register_bridges`` 会调）。

        只用于 ``is_available()`` 的"引擎还在不在"复核；不注入也能工作
        （那时完全靠 ``markReady()`` / ``markClosing()`` 握手）。
        """
        if engine is None:
            return
        self._engine = engine

    @Slot()
    def markReady(self) -> None:
        """QML 宿主已就绪（``Component.onCompleted`` 调用）。幂等。

        **关闭之后再来一次也算数**：引擎重建 / 根组件重载时，新宿主会重新握手。
        （``QtUIPort.stop()`` 是永久的，那是端口自己的纪律；宿主这边"窗口又回来了"
        就该重新可用。）
        """
        if self._attached:
            return
        self._attached = True
        self.log.info("QML 对话框宿主已就绪，Dialogs 可用")
        self.availabilityChanged.emit()

    @Slot()
    def markClosing(self) -> None:
        """QML 宿主即将销毁（``Component.onDestruction`` 调用）：放行所有等待者。"""
        was = self._attached
        self._attached = False
        self._release_all("QML 宿主销毁")
        if was:
            self.availabilityChanged.emit()

    def is_available(self) -> bool:
        """QML 窗口现在能不能弹窗（``QtUIPort`` 每轮都会问一次）。

        * 没握过手（QML 还没加载）→ False，端口整体退化为 ``NullUIPort`` 行为；
        * 走过 ``onDestruction`` → False（直到新宿主重新 ``markReady()``）；
        * 注入过引擎且 ``rootObjects()`` 已空 → False（引擎被销毁但 QML 没走到析构）。
        """
        if not self._attached:
            return False
        engine = self._engine
        if engine is not None and self._on_main_thread():
            try:
                if not engine.rootObjects():
                    return False
            except RuntimeError:  # 引擎的 C++ 对象已析构
                return False
            except Exception as e:  # noqa: BLE001 - 引擎替身不认这个 API 时按"还在"处理
                self.log.debug("探测 engine.rootObjects() 失败（%s），按引擎还在处理", e)
        return True

    # ─── DialogHost：问答类 ─────────────────────────────────

    def present(self, kind: str, payload: Dict[str, Any], reply: Reply) -> None:
        """把一次问答请求交给 QML 宿主；用户作答后由 QML 回调 ``submitDialog``。

        无法显示时**立刻**用 ``payload['fallback']`` 收尾 —— 绝不让调用方干等到超时。
        """
        data = dict(payload) if payload else {}
        if FALLBACK_KEY not in data:
            self.log.debug("present(%s) 的 payload 没有 fallback 键，按 None 处理", kind)
            data[FALLBACK_KEY] = None

        if not self._on_main_thread():
            self.log.error("present(%s) 从非主线程被调用，按兜底值收尾（不碰 QML）", kind)
            self._answer(reply, data.get(FALLBACK_KEY), kind)
            return
        if not self.is_available():
            self.log.info("present(%s)：QML 宿主不可用，按兜底值 %r 收尾", kind, data.get(FALLBACK_KEY))
            self._answer(reply, data.get(FALLBACK_KEY), kind)
            return

        if kind not in PRESENT_KINDS:
            # 不在这里拦死：阶段 3 会加新 kind，QML 宿主才是"有哪些组件"的权威。
            # 它认不出来会立刻回兜底值（DialogHost.qml 的未知 kind 分支，有测试钉住）。
            self.log.warning("present(%s) 的 kind 不在 PRESENT_KINDS 里，交给 QML 宿主决定", kind)

        record = _Dialog(id=self._next_id(), kind=str(kind or ""), payload=data, reply=reply)
        self._dialogs[record.id] = record
        self.dialogsChanged.emit()
        self.dialogRequested.emit(self._dialog_payload(record))

    def show_progress(self, payload: Dict[str, Any]) -> None:
        """显示或更新进度对话框（端口只会有一个进度会话）。"""
        data = dict(payload) if payload else {}
        if not self._on_main_thread() or not self.is_available():
            self.log.debug("show_progress：QML 宿主不可用，进度请求已忽略")
            return
        report = data.get("report")
        if report is None:
            self.log.warning("show_progress 的 payload 缺少 report，已忽略")
            return

        session = self._progress
        if session is None:
            # heading / cancel_label / on_cancel 是建窗参数，只在首次生效（与 Tk 版一致）
            session = _Progress(
                id=self._next_id(),
                title=str(data.get("title") or ""),
                heading=str(data.get("heading") or ""),
                cancel_label=str(data.get("cancel_label") or ""),
                on_cancel=data.get("on_cancel"),
            )
            self._progress = session
        elif data.get("title") and str(data["title"]) != session.title:
            self.log.debug("进度标题从 %r 变为 %r（端口只维护一个会话，沿用同一个窗口）", session.title, data["title"])
            session.title = str(data["title"])

        session.revision += 1
        session.data = self._progress_payload(session, report, data)
        self.progressChanged.emit(session.data)

    def close_progress(self) -> None:
        """关闭当前进度对话框；端口只在标题匹配时调用（标题匹配在端口里判）。"""
        if not self._on_main_thread():
            self.log.error("close_progress 从非主线程被调用，已忽略（不碰 QML）")
            return
        session = self._progress
        if session is None:
            self.log.debug("close_progress：当前没有进度会话，已忽略")
            return
        self._progress = None
        self.progressClosed.emit()

    # ─── DialogHost：Toast 与剪贴板 ─────────────────────────

    def notify(self, payload: Dict[str, Any]) -> None:
        """弹一条 Toast（右下角、向上堆叠、超时自动消失）。"""
        data = dict(payload) if payload else {}
        if not self._on_main_thread() or not self.is_available():
            self.log.debug("notify：QML 宿主不可用，丢弃一条通知：%s", data.get("message", ""))
            return
        message = str(data.get("message") or "")
        if not message:
            self.log.warning("notify 收到空消息，已忽略")
            return
        level = str(data.get("level") or "info")
        if level not in NOTIFY_LEVELS:
            self.log.debug("notify 收到未知级别 %r，已按 info 处理", level)
            level = "info"

        toast = _Toast(
            id=self._next_id(),
            message=message,
            title=str(data.get("title") or ""),
            subtitle=str(data.get("subtitle") or ""),
            level=level,
            icon=str(data.get("icon") or ""),
            duration_ms=self._duration_of(data.get("duration_ms")),
        )
        self._toasts.append(toast)
        self._start_timer(toast)
        self.toastRequested.emit(self._toast_payload(toast))
        self.toastsChanged.emit()
        self._enforce_cap()

    def set_clipboard(self, text: str) -> bool:
        """写入剪贴板；失败返回 False（调用方据此降级提示，不假装成功）。

        ``QGuiApplication.clipboard()`` 必须在主线程调用，所以非主线程一律拒绝。
        """
        if not self._on_main_thread():
            self.log.warning("set_clipboard 只能在主线程调用，已拒绝（返回 False）")
            return False
        app = QGuiApplication.instance()
        if app is None:
            self.log.debug("没有 QGuiApplication，无法写剪贴板")
            return False
        try:
            clipboard = app.clipboard()
            if clipboard is None:
                return False
            clipboard.setText(str(text))
            return True
        except Exception as e:  # noqa: BLE001 - 剪贴板可能被别的程序占用
            self.log.warning("写入剪贴板失败: %s", e)
            return False

    # ─── QML 回填答案的槽 ───────────────────────────────────

    @Slot(int, "QVariant", result=bool)
    def submitDialog(self, dialogId: int, value: Any) -> bool:  # noqa: N802 - QML 槽名
        """QML 弹窗作答时调用（确定 / 选项 / 输入框的内容）。

        **重复作答只认第一次**：``QtUIPort`` 那边也做了同样的保护，但本模块要能
        独立成立 —— 引擎可能在同一个事件循环里把两个 ``clicked`` 都排进来。
        """
        record = self._dialogs.get(int(dialogId))
        if record is None:
            self.log.debug("submitDialog(%s) 找不到待答请求（可能已经被回答过）", dialogId)
            return False
        return self._finish(record, value)

    @Slot(int, result=bool)
    def cancelDialog(self, dialogId: int) -> bool:  # noqa: N802
        """"答不上来"：用 ``payload['fallback']`` 收尾。

        调用它的两种情形：**宿主没有这个 kind 的组件**（绝不能因此让调用方等到超时）、
        以及 ``ask_text`` / ``choose`` 的取消 / Esc / 关闭按钮（两者的答案都是兜底值
        ``None``）。``confirm`` 的"取消"是 **False** 而不是兜底值，由
        ``ConfirmDialog.qml`` 显式 ``submitDialog(id, false)``。
        """
        record = self._dialogs.get(int(dialogId))
        if record is None:
            self.log.debug("cancelDialog(%s) 找不到待答请求（可能已经被回答过）", dialogId)
            return False
        if record.kind not in PRESENT_KINDS:
            self.log.warning(
                "QML 宿主没有 %r 对应的组件，按兜底值 %r 收尾（不让调用方干等到超时）",
                record.kind,
                record.payload.get(FALLBACK_KEY),
            )
        return self._finish(record, record.payload.get(FALLBACK_KEY))

    @Slot(result=bool)
    def cancelProgress(self) -> bool:  # noqa: N802
        """用户点了进度对话框的「取消」：只转调 ``on_cancel``，**不自己关窗**。

        与旧实现 ``ui/ports_tk.py:_build_progress_window`` 一致：取消按钮的 command
        就是 ``on_cancel``（典型实现是给任务置一个 ``threading.Event``），窗口何时关闭
        由任务自己调 ``close_progress`` 决定。重复点按只重复转调，不改变本模块状态。
        """
        if not self._on_main_thread():
            self.log.error("cancelProgress 从非主线程被调用，已忽略")
            return False
        session = self._progress
        if session is None or session.on_cancel is None:
            self.log.debug("cancelProgress：当前进度会话没有取消回调")
            return False
        try:
            session.on_cancel()
        except Exception as e:  # noqa: BLE001 - 取消回调抛异常不能连累界面
            self.log.error("进度取消回调抛异常: %s", e, exc_info=True)
            return False
        self.log.info("进度取消已转交给调用方（标题 %r）", session.title)
        return True

    @Slot(int, result=bool)
    def dismissToast(self, toastId: int) -> bool:  # noqa: N802
        """提前摘掉一条 Toast（用户点了一下）。到期移除走本模块自己的定时器。"""
        for index, toast in enumerate(self._toasts):
            if toast.id == int(toastId):
                self._drop_toast(index, "用户关闭")
                return True
        return False

    @Slot()
    def clearToasts(self) -> None:  # noqa: N802
        """清空 Toast 队列（退出时由退出链调用；``main_qml`` 的退出链是任务 2.14）。

        这是 A-16 / M-45「退出时清理队列」在 Qt 侧的落点：旧实现
        ``ui/dialogs.py:cleanup_toast_queue`` 被 ``ui/app_base.py:81-83`` 在窗口关闭时
        调用。**本模块不自己去挂 QCoreApplication.aboutToQuit** —— 退出顺序属于装配
        （2.14），这里只保证"调了就干净"。
        """
        if not self._on_main_thread():
            self.log.error("clearToasts 从非主线程被调用，已忽略")
            return
        if not self._toasts:
            return
        count = len(self._toasts)
        self._stop_all_timers()
        self._toasts.clear()
        self.log.info("Toast 队列已清空（%d 条）", count)
        self.toastsChanged.emit()

    # ─── QML 只读属性（带 NOTIFY，绑定用） ──────────────────

    @Property("QVariantList", notify=dialogsChanged)
    def dialogs(self) -> List[Dict[str, Any]]:
        """待答的对话框（与 :meth:`pendingDialogs` 同一份数据；绑定请用本属性）。"""
        return self.pendingDialogs()

    @Property("QVariantList", notify=toastsChanged)
    def toasts(self) -> List[Dict[str, Any]]:
        """当前 Toast 队列（与 :meth:`activeToasts` 同一份数据）。"""
        return self.activeToasts()

    @Property(bool, notify=availabilityChanged)
    def available(self) -> bool:
        """QML 宿主是否就绪（``is_available()`` 的属性形态，供界面显示降级提示）。"""
        return self.is_available()

    @Property(int, notify=dialogsChanged)
    def dialogCount(self) -> int:  # noqa: N802
        return len(self._dialogs)

    @Property(int, notify=toastsChanged)
    def toastCount(self) -> int:  # noqa: N802
        return len(self._toasts)

    # ─── 诊断 ───────────────────────────────────────────────

    @Slot(result="QVariantList")
    def pendingDialogs(self) -> List[Dict[str, Any]]:
        """还没被回答的请求（按到达顺序）。供测试与诊断。"""
        return [
            {
                "id": record.id,
                "kind": record.kind,
                "title": str(record.payload.get("title") or ""),
                "message": str(record.payload.get("message") or ""),
                "prompt": str(record.payload.get("prompt") or ""),
                "level": str(record.payload.get("level") or record.kind),
                "fallback": record.payload.get(FALLBACK_KEY),
                "needs_reply": record.kind in _REPLY_KINDS or record.kind not in PRESENT_KINDS,
            }
            for record in self._dialogs.values()
        ]

    @Slot(result="QVariantList")
    def activeToasts(self) -> List[Dict[str, Any]]:
        """当前 Toast（按到达顺序，第 0 项在**最下面** —— 旧实现的堆叠方向）。"""
        return [self._toast_payload(toast) for toast in self._toasts]

    @Slot(result="QVariantMap")
    def progressState(self) -> Dict[str, Any]:
        """当前进度会话的快照；没有会话时返回空 map。"""
        session = self._progress
        return dict(session.data) if session is not None else {}

    @Slot(result="QVariantMap")
    def describe(self) -> Dict[str, Any]:
        """状态快照（供 ``--dump-services`` / 诊断用，可直接 ``json.dumps``）。"""
        return {
            "class": type(self).__name__,
            "qml_name": QML_NAME,
            "available": bool(self.is_available()),
            "attached": bool(self._attached),
            "pending_dialogs": len(self._dialogs),
            "toasts": len(self._toasts),
            "toasts_expired": int(self._expired),
            "toasts_evicted": int(self._evicted),
            "progress_title": self._progress.title if self._progress is not None else None,
            "progress_revision": self._progress.revision if self._progress is not None else 0,
            "max_toasts": MAX_TOASTS,
        }

    def __repr__(self) -> str:  # pragma: no cover
        state = "ready" if self.is_available() else "idle"
        return f"<DialogBridge {state} dialogs={len(self._dialogs)} toasts={len(self._toasts)}>"

    # ─── 内部：请求收尾 ─────────────────────────────────────

    def _next_id(self) -> int:
        """请求 id：对话框 / Toast / 进度共用一个自增序列（QML 只当令牌用）。"""
        self._next_id_value += 1
        return self._next_id_value

    def _answer(self, reply: Optional[Reply], value: Any, kind: str) -> None:
        """安全调用 reply —— 调用方抛异常不该把宿主带崩（端口那边靠返回值兜底）。"""
        if reply is None:
            return
        try:
            reply(value)
        except Exception as e:  # noqa: BLE001
            self.log.error("%s 的回填回调抛异常: %s", kind, e, exc_info=True)

    def _finish(self, record: _Dialog, value: Any) -> bool:
        """收尾一条请求：先摘队列、再通知 QML，最后才 reply（先到先得）。"""
        if record.replied:
            self.log.debug("%s#%d 收到重复回答 %r，已忽略后一次", record.kind, record.id, value)
            return False
        record.replied = True
        record.answer = value
        self._dialogs.pop(record.id, None)
        self.dialogClosed.emit(record.id)
        self.dialogsChanged.emit()
        self.log.debug("%s#%d -> %r", record.kind, record.id, value)
        self._answer(record.reply, value, record.kind)
        return True

    def _release_all(self, reason: str) -> None:
        """放行所有还在等待的调用方（窗口销毁 / 退出）：一律用各自的兜底值。"""
        waiting = list(self._dialogs.values())
        self._dialogs.clear()
        for record in waiting:
            if record.replied:
                continue
            record.replied = True
            record.answer = record.payload.get(FALLBACK_KEY)
            self.dialogClosed.emit(record.id)
            self._answer(record.reply, record.answer, record.kind)
        if waiting:
            self.log.info("%s：放行 %d 条待答请求（各自用兜底值收尾）", reason, len(waiting))
        self.dialogsChanged.emit()
        self._stop_all_timers()
        if self._toasts:
            self._toasts.clear()
            self.toastsChanged.emit()
        if self._progress is not None:
            self._progress = None
            self.progressClosed.emit()

    # ─── 内部：payload 组装 ─────────────────────────────────

    def _dialog_payload(self, record: _Dialog) -> Dict[str, Any]:
        """把 ``present`` 的 payload 折成 QML 直接能吃的形状。"""
        data = record.payload
        kind = record.kind
        # 提示类的 level 归一化与端口 `_do_show_alert` 一致（未知值按 info）；
        # 问答类没有 level，用 kind 本身当"样式名"给 QML 用。
        level = str(data.get("level") or kind)
        if kind in ALERT_KINDS and level not in ALERT_KINDS:
            level = KIND_INFO
        return {
            "id": record.id,
            "kind": kind,
            "title": str(data.get("title") or ""),
            "message": str(data.get("message") or ""),
            "prompt": str(data.get("prompt") or ""),
            "level": level,
            "blocking": bool(data.get("blocking", False)),
            "default": data.get("default"),
            "initial": str(data.get("initial") or ""),
            "password": bool(data.get("password", False)),
            "hint": str(data.get("hint") or ""),
            "options": self._options_payload(data.get("options")),
            "fallback": data.get(FALLBACK_KEY),
            "icon": str(data.get("icon") or ""),
        }

    @staticmethod
    def _options_payload(options: Any) -> List[Dict[str, Any]]:
        """``Choice`` 对象列表 → QML 能吃的 dict 列表。

        契约明说 ``options`` 里是 ``Choice`` 对象而不是 dict，宿主自己转 —— 这里就是
        那个转换点（**只做字段搬运，不做任何筛选/排序**：那是业务）。
        """
        out: List[Dict[str, Any]] = []
        for item in options or []:
            if isinstance(item, dict):
                out.append(dict(item))
                continue
            out.append(
                {
                    "value": getattr(item, "value", ""),
                    "label": getattr(item, "label", ""),
                    "description": getattr(item, "description", ""),
                    "disabled": bool(getattr(item, "disabled", False)),
                    "data": dict(getattr(item, "data", {}) or {}),
                }
            )
        return out

    def _progress_payload(self, session: _Progress, report: Any, data: Dict[str, Any]) -> Dict[str, Any]:
        """把一次 ``ProgressReport`` 折成 QML 的进度数据。

        ``detail`` 的兜底文案与旧实现 ``ui/ports_tk.py:_update_progress_window`` 逐字一致：
        确定进度时是 ``"当前 / 总量"``，不确定进度时是调用方给的 ``detail`` 或空串。
        """
        determinate = bool(getattr(report, "determinate", False))
        current = int(getattr(report, "current", 0) or 0)
        total = int(getattr(report, "total", 0) or 0)
        detail = data.get("detail")
        text = (str(detail) if detail is not None else f"{current} / {total}") if determinate else str(detail or "")
        return {
            "id": session.id,
            "revision": session.revision,
            "title": session.title,
            "heading": session.heading or session.title,
            "message": str(getattr(report, "message", "") or ""),
            "detail": text,
            "level": str(getattr(report, "level", "info") or "info"),
            "determinate": determinate,
            "fraction": float(getattr(report, "fraction", 0.0) or 0.0),
            "current": current,
            "total": total,
            "cancellable": session.on_cancel is not None,
            "cancelLabel": session.cancel_label,
        }

    @staticmethod
    def _toast_payload(toast: _Toast) -> Dict[str, Any]:
        """Toast 的对外形状。

        ``message`` 是端口给的正文（旧实现把它当**标题行**渲染，
        ``ui/ports_tk.py:_do_notify`` → ``show_notification(icon="", message, subtitle="")``）；
        ``title`` / ``subtitle`` / ``icon`` 是可选扩展（成就 Toast 那类调用方会用到），
        缺省时 QML 按 level 取图标、把 ``message`` 当唯一一行。
        """
        return {
            "id": toast.id,
            "message": toast.message,
            "title": toast.title,
            "subtitle": toast.subtitle,
            "level": toast.level,
            "icon": toast.icon,
            "duration_ms": toast.duration_ms,
        }

    # ─── 内部：Toast 定时器与上限 ───────────────────────────

    def _duration_of(self, raw: Any) -> int:
        """归一化 ``duration_ms``（未知值退回默认，且不低于下限）。"""
        if raw is None:
            return DEFAULT_TOAST_DURATION_MS
        try:
            value = int(raw)
        except (TypeError, ValueError):
            self.log.debug("notify 收到非数值的 duration_ms=%r，按默认 %d 处理", raw, DEFAULT_TOAST_DURATION_MS)
            return DEFAULT_TOAST_DURATION_MS
        return max(MIN_TOAST_DURATION_MS, value)

    def _start_timer(self, toast: _Toast) -> None:
        """给一条 Toast 排到期定时器（由本对象持有，队列清空时会停掉）。"""
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(toast.duration_ms)
        timer.timeout.connect(lambda: self._expire_toast(toast.id))
        toast.timer = timer
        timer.start()

    def _expire_toast(self, toastId: int) -> None:
        """到期：从队列摘掉并通知 QML（QML 收到通知后才开始淡出）。"""
        for index, toast in enumerate(self._toasts):
            if toast.id == toastId:
                self._expired += 1
                self._drop_toast(index, "到期")
                return

    def _drop_toast(self, index: int, reason: str) -> None:
        """移除第 index 条 Toast（停掉它的定时器）。"""
        toast = self._toasts.pop(index)
        self._stop_timer(toast)
        self.log.debug("Toast#%d 已移除（%s），剩余 %d 条", toast.id, reason, len(self._toasts))
        self.toastsChanged.emit()

    def _enforce_cap(self) -> None:
        """超过 :data:`MAX_TOASTS` 时挤掉**最旧**的一条（决策理由见 ``ToastHost.qml``）。"""
        dropped = False
        while len(self._toasts) > MAX_TOASTS:
            oldest = self._toasts[0]
            self._evicted += 1
            self._stop_timer(oldest)
            self._toasts.pop(0)
            dropped = True
            self.log.info("Toast 已满 %d 条，挤掉最旧的 Toast#%d（%r）", MAX_TOASTS, oldest.id, oldest.message)
        if dropped:
            self.toastsChanged.emit()

    @staticmethod
    def _stop_timer(toast: _Toast) -> None:
        if toast.timer is not None:
            try:
                toast.timer.stop()
            except RuntimeError:  # C++ 对象已析构
                pass
            toast.timer = None

    def _stop_all_timers(self) -> None:
        for toast in self._toasts:
            self._stop_timer(toast)


__all__ = [
    "DEFAULT_TOAST_DURATION_MS",
    "DialogBridge",
    "KIND_COMPONENT",
    "MAX_TOASTS",
    "MIN_TOAST_DURATION_MS",
    "QML_NAME",
    "TOAST_GAP",
    "TOAST_HEIGHT",
    "TOAST_MARGIN",
    "TOAST_WIDTH",
]
