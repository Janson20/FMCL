"""对话框宿主（DialogHost）—— QML 界面与 ``QtUIPort`` 之间的窄接口（阶段 2 任务 2.3）。

## 为什么要有这一层

``QtUIPort`` 是**服务层看得见的端口**（``app.ports.UIPort``）：它会在 worker 线程里被
调用（例如预下载线程弹确认框），因此必须自己切回主线程。真正"把对话框画出来"的是
QML（阶段 2.13 的 ``Dialogs``）。两者之间用本模块的 ``DialogHost`` 协议隔开：

    worker 线程 --> QtUIPort（队列 + QTimer）--> DialogHost（主线程）--> QML

好处是 ``app/bridges/ui_port_qt.py`` 不必知道任何 QML/QObject 细节，测试里也能用一个
纯 Python 的 ``RecordingDialogHost`` 把整条链路（含跨线程）跑起来。

## 线程纪律（硬性，契约红线 3）

``DialogHost`` 的**全部方法都只在 Qt 主线程被调用**：它们由 ``QtUIPort`` 的 QTimer
轮询体（或主线程上的一次就地调用）驱动。宿主的 ``QObject`` 实现因此可以放心读写
QML 属性，不需要自己做线程切换。``reply(...)`` 由宿主在用户作答后调用，**可能在
之后的某一轮事件循环里**（异步），端口用 ``threading.Event`` 把结果交回调用线程。

## 红线

宿主里**不许写业务规则**（迁移红线 2）：它只做"把 payload 变成对话框"和"把用户的
答案交回 reply"两件事。任何判断（版本比较、路径拼装、重试策略）都属于 ``services/``。
端口已经把该用的兜底值算好放在 ``payload['fallback']`` 里，宿主无法处理的请求直接
``reply(payload.get('fallback'))`` 即可 —— 不要自己猜兜底值。

## payload 形状（冻结；2.13 的 QML 宿主按此实现）

``present(kind, payload, reply)`` 的 kind 与 payload：

| kind | payload 键 |
|------|-----------|
| ``info`` / ``warning`` / ``error`` | ``title`` ``message`` ``level`` ``blocking`` ``fallback=None`` |
| ``confirm`` | ``title`` ``message`` ``default``(bool) ``fallback``(等于 default) |
| ``ask_text`` | ``title`` ``prompt`` ``initial`` ``password``(bool) ``fallback=None`` |
| ``choose`` | ``title`` ``prompt`` ``options``(list[Choice]) ``default`` ``hint`` ``fallback=None`` |

``options`` 里是 ``app.ports.Choice`` 对象（不是 dict），宿主按 ``value`` / ``label`` /
``description`` / ``disabled`` / ``data`` 自己转成 QML 形状。

``show_progress(payload)``：``report``(ProgressReport) ``title`` ``heading`` ``detail``
``cancel_label`` ``on_cancel``。``title`` 是端口解析后的当前标题（``report.title`` 为
None 时沿用上一次），首次建窗用它。

``notify(payload)``：``message`` ``level``（取值见 ``NOTIFY_LEVELS``，端口已把未知
级别归一化成 ``info``）。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple, runtime_checkable

logger = logging.getLogger(__name__)

#: ``present()`` 认识的全部 kind
KIND_INFO = "info"
KIND_WARNING = "warning"
KIND_ERROR = "error"
KIND_CONFIRM = "confirm"
KIND_ASK_TEXT = "ask_text"
KIND_CHOOSE = "choose"
PRESENT_KINDS: Tuple[str, ...] = (KIND_INFO, KIND_WARNING, KIND_ERROR, KIND_CONFIRM, KIND_ASK_TEXT, KIND_CHOOSE)

#: 提示类 kind（不需要作答，只展示）
ALERT_KINDS: Tuple[str, ...] = (KIND_INFO, KIND_WARNING, KIND_ERROR)

#: ``notify()`` 的合法级别（与 ``ui/dialogs.py`` 的 NotifyType 同一套）
NOTIFY_LEVELS: Tuple[str, ...] = ("info", "success", "warning", "error")

#: payload 里一定存在的兜底值键
FALLBACK_KEY = "fallback"

#: 宿主作答用的回调类型
Reply = Callable[[Any], None]


@runtime_checkable
class DialogHost(Protocol):
    """QML 侧对话框能力的宿主接口。实现方在主线程上被调用（见模块 docstring）。"""

    def is_available(self) -> bool:
        """宿主当前能否显示对话框（QML 引擎已就绪、窗口还在）。"""
        ...

    def present(self, kind: str, payload: Dict[str, Any], reply: Reply) -> None:
        """显示一个问答类对话框。

        Args:
            kind: ``PRESENT_KINDS`` 之一。
            payload: 见模块 docstring 的表格，一定含 ``fallback`` 键。
            reply: 用户作答后调用，**可能异步**（QML 对话框天生非阻塞）。
                无法处理的 kind 也必须回 ``reply(payload.get('fallback'))``，
                否则调用方只能干等到超时。
        """
        ...

    def show_progress(self, payload: Dict[str, Any]) -> None:
        """显示或更新进度对话框（端口只会有一个进度会话）。"""
        ...

    def close_progress(self) -> None:
        """关闭当前进度对话框；端口只在标题匹配时调用。"""
        ...

    def notify(self, payload: Dict[str, Any]) -> None:
        """弹一条通知（Toast）。"""
        ...

    def set_clipboard(self, text: str) -> bool:
        """写入剪贴板；失败返回 False，由调用方降级提示（不假装成功）。"""
        ...


class NullDialogHost:
    """无界面宿主：问答类请求**立即**用兜底值回答，其余只写日志。

    用途：无界面场景（CLI、pytest、启动早期）把它塞给 ``QtUIPort``，端口的所有调用
    都会立刻拿到 ``NullUIPort`` 语义的答案。``is_available()`` 恒为 False ——
    "没有界面"就该让服务层走静默降级路径（``app.context.ui_ready`` 依赖这一点）。
    """

    def __init__(self, name: str = "NullDialogHost") -> None:
        self._name = name
        self.log = logging.getLogger("app.bridges.dialog_host.null")

    def is_available(self) -> bool:
        return False

    def present(self, kind: str, payload: Dict[str, Any], reply: Reply) -> None:
        fallback = payload.get(FALLBACK_KEY)
        self.log.info("[%s->%r] %s", kind, fallback, payload.get("title") or payload.get("prompt") or "")
        reply(fallback)

    def show_progress(self, payload: Dict[str, Any]) -> None:
        report = payload.get("report")
        self.log.debug("[progress] %s %s", payload.get("title"), getattr(report, "message", ""))

    def close_progress(self) -> None:
        self.log.debug("[progress-close]")

    def notify(self, payload: Dict[str, Any]) -> None:
        self.log.info("[notify:%s] %s", payload.get("level", "info"), payload.get("message", ""))

    def set_clipboard(self, text: str) -> bool:
        self.log.debug("[clipboard-refused] %d chars", len(text))
        return False

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self._name}>"


class _NoReply:
    """脚本哨兵：表示"收到请求但故意不回答"（用来测超时）。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover
        return "NO_REPLY"


#: 预设脚本用这个值表示"故意不回答"
NO_REPLY: Any = _NoReply()

#: 内部哨兵：脚本里没写这个 kind 时的默认行为（用 payload 的兜底值回答）
_UNSET: Any = object()


class RecordingDialogHost:
    """测试用宿主：记录收到的每一个请求，并按预设脚本自动作答。

    典型用法::

        RecordingDialogHost(answers={"confirm": True})        # 自动答 True
        RecordingDialogHost(answers={"choose": "lite"})       # 自动答 "lite"
        RecordingDialogHost(auto_reply=False)                 # 谁都不答 -> 触发超时
        RecordingDialogHost(answers={"ask_text": NO_REPLY})   # 只有这一类不答

    脚本值可以是字面量、``(payload) -> value`` 的可调用对象、或 ``NO_REPLY``；
    没写到的 kind 按"端口给的兜底值"回答（与 ``NullDialogHost`` 一致）。

    要模拟"宿主异步作答"时用 ``auto_reply=False``，再由测试线程或定时器调用
    ``reply_latest(value)`` / ``reply_kind(kind, value)``。

    ``available`` 与 ``clipboard`` 是两个独立开关：测试替身不自作聪明地联动
    （与 ``app.ports.RecordingUIPort`` 的约定一致）。
    """

    def __init__(
        self,
        *,
        available: bool = True,
        clipboard: bool = True,
        auto_reply: bool = True,
        answers: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._available = bool(available)
        self._clipboard = bool(clipboard)
        self._auto_reply = bool(auto_reply)
        self._answers: Dict[str, Any] = dict(answers or {})
        #: 收到的 ``present()`` 请求；每项含 kind / payload / replied / answer / reply
        self.requests: List[Dict[str, Any]] = []
        self.progress: List[Dict[str, Any]] = []
        self.notifications: List[Dict[str, Any]] = []
        self.clipboard_texts: List[str] = []
        self.closed: int = 0
        self.log = logging.getLogger("app.bridges.dialog_host.recording")

    # ─── 开关与脚本 ─────────────────────────────────────────

    def set_available(self, value: bool) -> None:
        self._available = bool(value)

    def set_auto_reply(self, value: bool) -> None:
        self._auto_reply = bool(value)

    def set_answer(self, kind: str, value: Any) -> None:
        self._answers[kind] = value

    # ─── DialogHost ─────────────────────────────────────────

    def is_available(self) -> bool:
        return self._available

    def present(self, kind: str, payload: Dict[str, Any], reply: Reply) -> None:
        record: Dict[str, Any] = {
            "kind": kind,
            "payload": dict(payload),
            "reply": reply,
            "replied": False,
            "answer": None,
        }
        self.requests.append(record)
        if not self._auto_reply:
            return
        script = self._answers.get(kind, _UNSET)
        if script is NO_REPLY:
            return
        if script is _UNSET:
            value = payload.get(FALLBACK_KEY)
        elif callable(script):
            value = script(payload)
        else:
            value = script
        self._do_reply(record, value)

    def show_progress(self, payload: Dict[str, Any]) -> None:
        self.progress.append(dict(payload))

    def close_progress(self) -> None:
        self.closed += 1

    def notify(self, payload: Dict[str, Any]) -> None:
        self.notifications.append(dict(payload))

    def set_clipboard(self, text: str) -> bool:
        self.clipboard_texts.append(text)
        return self._clipboard

    # ─── 断言与驱动辅助 ─────────────────────────────────────

    def kinds(self) -> List[str]:
        return [r["kind"] for r in self.requests]

    def find(self, kind: str) -> List[Dict[str, Any]]:
        return [r for r in self.requests if r["kind"] == kind]

    def pending(self) -> List[Dict[str, Any]]:
        """还没被回答的请求（按到达顺序）。"""
        return [r for r in self.requests if not r["replied"]]

    def reply_latest(self, value: Any) -> bool:
        """回答最后一个待答请求；没有待答请求时返回 False。"""
        waiting = self.pending()
        if not waiting:
            return False
        self._do_reply(waiting[-1], value)
        return True

    def reply_kind(self, kind: str, value: Any) -> int:
        """回答指定 kind 的全部待答请求，返回回答条数。"""
        waiting = [r for r in self.pending() if r["kind"] == kind]
        for record in waiting:
            self._do_reply(record, value)
        return len(waiting)

    def clear(self) -> None:
        self.requests.clear()
        self.progress.clear()
        self.notifications.clear()
        self.clipboard_texts.clear()
        self.closed = 0

    # ─── 内部 ───────────────────────────────────────────────

    def _do_reply(self, record: Dict[str, Any], value: Any) -> None:
        record["replied"] = True
        record["answer"] = value
        record["reply"](value)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<RecordingDialogHost available={self._available} requests={len(self.requests)} "
            f"pending={len(self.pending())} progress={len(self.progress)}>"
        )


__all__ = [
    "ALERT_KINDS",
    "DialogHost",
    "FALLBACK_KEY",
    "KIND_ASK_TEXT",
    "KIND_CHOOSE",
    "KIND_CONFIRM",
    "KIND_ERROR",
    "KIND_INFO",
    "KIND_WARNING",
    "NOTIFY_LEVELS",
    "NO_REPLY",
    "NullDialogHost",
    "PRESENT_KINDS",
    "RecordingDialogHost",
    "Reply",
]
