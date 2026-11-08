"""AGENT 对话 QML 桥（阶段 3 任务 3.22）。

链路：

    qml/pages/agent/AgentPage.qml
        -> Agent.send / stop / newSession / selectSession / answerApproval ...
    app/bridges/agent_bridge.py（本模块）
        -> services/agent_chat_service.py（会话 / 流式 / 工具循环）
        -> services/agent_*（模型、工具、会话存储）
        -> services/voice_service.py（语音按钮）

线程约定：

- 一轮对话在 TaskRunner 的 worker 线程里跑；worker 只通过 `posted.emit` 把
  普通字典送回主线程，真正改 QObject 属性、写剪贴板、弹确认框都在主线程。
- 工具确认采用"worker 阻塞 + 主线程放行"：服务里的 `confirm` 会等一个
  `threading.Event`，QML 的 answerApproval 只负责填答案并 set 事件。这样
  流式循环可以原地暂停，不需要把工具状态机拆成两套。
- 停止时先 set cancel_event，再放行还在等待的确认（按拒绝处理）；worker 自己
  检查取消并落盘半截回答。页面销毁/退出链同样先放行，避免 worker 永久挂住。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot

from services.agent_chat_service import AgentChatError, AgentChatService
from services.i18n_service import _

logger = logging.getLogger("app.bridges.agent_bridge")

#: 语音事件轮询间隔（主线程）。VoiceService 本身是队列式接口，不主动发 Qt 信号。
VOICE_POLL_MS = 100


class _VoiceListener:
    """VoiceService 的监听器：只把事件转成桥的排队信号，不碰界面。"""

    def __init__(self, post) -> None:
        self._post = post

    def on_voice_state(self, state: str, message: str = "") -> None:
        self._post({"kind": "voice", "event": "state", "state": state, "message": message})

    def on_voice_progress(self, percent: float, message: str = "") -> None:
        self._post({"kind": "voice", "event": "progress", "percent": percent, "message": message})

    def on_voice_final(self, text: str) -> None:
        self._post({"kind": "voice", "event": "final", "text": text})

    def on_voice_error(self, message: str) -> None:
        self._post({"kind": "voice", "event": "error", "message": message})


class AgentBridge(QObject):
    """上下文属性 Agent。"""

    changed = Signal()
    #: 仅更新正在流式输出的那条消息（index, content, blocks, thinking）；
    #: 不触发 ListView 整体重排。
    streamUpdated = Signal(int, str, "QVariantList", str)
    noticeRequested = Signal(str, str)
    voiceText = Signal(str)
    posted = Signal("QVariantMap")

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._service: Optional[AgentChatService] = None
        self._tasks: Any = None
        self._config: Any = None
        self._account: Any = None
        self._launcher: Any = None
        self._closing = False
        self._seq = 0
        self._active_handle: Any = None
        self._cancel_event: Optional[threading.Event] = None
        self._gate_lock = threading.Lock()
        self._gate: Optional[Dict[str, Any]] = None
        self._gate_seq = 0
        self._current: Any = None
        self._messages: List[Dict[str, Any]] = []
        self._sessions: List[Dict[str, Any]] = []
        self._todos: List[Dict[str, Any]] = []
        self._busy = False
        self._waiting = False
        self._status_text = ""
        self._status_level = "info"
        self._error_text = ""
        self._logged_in = False
        self._credits = 0
        self._token_estimate = 0
        self._stream_id = ""
        self._stream_index = -1
        self._stream_failed = False
        self._message_seq = 0
        self._pending_approval: Dict[str, Any] = {}
        self._voice_service: Any = None
        self._voice_listener: Optional[_VoiceListener] = None
        self._voice_state = "idle"
        self._voice_error = ""
        self._voice_timer = QTimer(self)
        self._voice_timer.setInterval(VOICE_POLL_MS)
        self._voice_timer.timeout.connect(self._poll_voice)

        self.posted.connect(self._deliver, Qt.QueuedConnection)

    # ─── 装配 / 生命周期 ────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        self._context = context
        self._tasks = getattr(context, "tasks", None) if context is not None else None
        try:
            self._config = context.config if context is not None else None
        except Exception as e:  # noqa: BLE001
            logger.warning("取配置失败（AGENT 将不可用）: %s", e)
            self._config = None
        if context is not None:
            for name in ("agent_chat", "jingdu_account", "launcher"):
                try:
                    value = context.try_get(name)
                except Exception as e:  # noqa: BLE001
                    logger.warning("取 %s 失败: %s", name, e)
                    value = None
                if name == "agent_chat":
                    self._service = value
                elif name == "jingdu_account":
                    self._account = value
                else:
                    self._launcher = value
        if self._service is None:
            try:
                self._service = AgentChatService(context)
            except Exception as e:  # noqa: BLE001
                logger.warning("创建 AGENT 对话服务失败: %s", e)
                self._service = None
        self._refresh_login()
        self._refresh_sessions()
        self._open_initial_session()
        self._refresh_credits()
        self.changed.emit()

    def detach(self) -> None:
        """退出链：取消在跑的对话，放行确认等待，注销语音监听。"""
        self._closing = True
        try:
            if self._cancel_event is not None:
                self._cancel_event.set()
        except Exception:
            pass
        self._release_gate({"accepted": False, "answers": [], "cancelled": True})
        try:
            if self._active_handle is not None:
                self._active_handle.cancel()
        except Exception:
            pass
        self._active_handle = None
        self._voice_timer.stop()
        if self._voice_listener is not None and self._voice_service is not None:
            try:
                self._voice_service.unregister(self._voice_listener)
            except Exception:
                pass
        self._voice_listener = None
        self._busy = False
        self._waiting = False

    # ─── 只读属性 ───────────────────────────────────────────

    @Property(bool, notify=changed)
    def available(self) -> bool:
        return self._service is not None

    @Property(bool, notify=changed)
    def loggedIn(self) -> bool:  # noqa: N802
        return self._logged_in

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=changed)
    def waiting(self) -> bool:
        return self._waiting

    @Property(bool, notify=changed)
    def hasApproval(self) -> bool:  # noqa: N802
        return bool(self._pending_approval)

    @Property("QVariantMap", notify=changed)
    def pendingApproval(self) -> Dict[str, Any]:  # noqa: N802
        return dict(self._pending_approval)

    @Property(str, notify=changed)
    def statusText(self) -> str:  # noqa: N802
        return self._status_text

    @Property(str, notify=changed)
    def statusLevel(self) -> str:  # noqa: N802
        return self._status_level

    @Property(str, notify=changed)
    def errorText(self) -> str:  # noqa: N802
        return self._error_text

    @Property(str, notify=changed)
    def currentSessionId(self) -> str:  # noqa: N802
        return str(getattr(self._current, "id", "") or "")

    @Property(str, notify=changed)
    def currentTitle(self) -> str:  # noqa: N802
        return str(getattr(self._current, "title", "") or "")

    @Property("QVariantList", notify=changed)
    def messages(self) -> List[Dict[str, Any]]:
        return list(self._messages)

    @Property("QVariantList", notify=changed)
    def sessions(self) -> List[Dict[str, Any]]:
        return list(self._sessions)

    @Property("QVariantList", notify=changed)
    def todos(self) -> List[Dict[str, Any]]:
        return list(self._todos)

    @Property(int, notify=changed)
    def tokenEstimate(self) -> int:  # noqa: N802
        return int(self._token_estimate)

    @Property(int, notify=changed)
    def credits(self) -> int:
        return int(self._credits)

    @Property(str, notify=changed)
    def modelLabel(self) -> str:  # noqa: N802
        if self._service is None:
            return ""
        try:
            return self._service.model_label()
        except Exception:
            return ""

    @Property(bool, notify=changed)
    def canRetry(self) -> bool:  # noqa: N802
        if self._busy or not self._logged_in or not self._messages:
            return False
        last = self._messages[-1]
        return str(last.get("role") or "") == "assistant" and not bool(last.get("streaming"))

    @Property(str, notify=changed)
    def voiceState(self) -> str:  # noqa: N802
        return self._voice_state

    @Property(str, notify=changed)
    def voiceError(self) -> str:  # noqa: N802
        return self._voice_error

    # ─── 会话 ───────────────────────────────────────────────

    def _refresh_login(self) -> None:
        self._logged_in = bool(self._service is not None and self._service.logged_in())
        if self._logged_in and not self._credits and self._account is not None:
            try:
                info = self._account.cached_info()
                if isinstance(info, dict):
                    self._credits = int(info.get("ai_credits", 0) or 0)
            except Exception:
                pass

    def _refresh_sessions(self) -> None:
        if self._service is None:
            self._sessions = []
            return
        try:
            rows = self._service.list_sessions()
        except Exception as e:  # noqa: BLE001
            logger.warning("读取会话列表失败: %s", e)
            rows = []
        out: List[Dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if not str(item.get("title") or "").strip():
                item["title"] = _("agent_session_untitled")
            out.append(item)
        self._sessions = out

    def _open_initial_session(self) -> None:
        # 与旧 Tk 版一致：启动时先给一个**新会话**，历史会话留在左栏供切换。
        # 不自动打开最后一个会话 —— 否则“我只是重新打开启动器”就会被塞进
        # 上一轮的上下文，且页面首屏会随本地历史数据变化（视觉回归也不稳定）。
        if self._service is None:
            return
        self._current = self._service.new_session()
        self._refresh_messages()

    def _refresh_messages(self) -> None:
        if self._service is None or self._current is None:
            self._messages = []
            self._todos = []
            self._token_estimate = 0
            return
        self._messages = self._service.history(self._current)
        self._todos = self._service.todos(self._current.id)
        self._token_estimate = self._service.estimate_tokens(self._current)

    @Slot()
    def clearError(self) -> None:  # noqa: N802
        self._error_text = ""
        if self._status_level == "error":
            self._status_text = ""
            self._status_level = "info"
        self.changed.emit()

    @Slot()
    def refresh(self) -> None:  # noqa: N802
        if self._closing:
            return
        self._refresh_login()
        self._refresh_sessions()
        if self._logged_in and self._credits <= 0:
            self._refresh_credits()
        if self._current is None:
            self._open_initial_session()
        elif not self._busy:
            # 页面重新可见时只刷新列表/登录态；不重载当前会话，避免把用户正在
            # 阅读的滚动位置和半截内容打断。
            self._todos = self._service.todos(self._current.id) if self._service else []
        self.changed.emit()

    @Slot()
    def newSession(self) -> None:  # noqa: N802
        if self._service is None or self._busy:
            return
        if self._current is not None:
            self._service.save_session(self._current)
        self._current = self._service.new_session()
        self._messages = []
        self._todos = []
        self._token_estimate = 0
        self._error_text = ""
        self._status_text = _("agent_new_session_created")
        self._status_level = "info"
        self._refresh_sessions()
        self.changed.emit()

    @Slot(str)
    def selectSession(self, session_id: str) -> None:  # noqa: N802
        if self._service is None or self._busy:
            return
        if str(session_id) == self.currentSessionId:
            return
        loaded = self._service.load_session(str(session_id))
        if loaded is None:
            self._error_text = _("agent_session_load_failed")
            self._status_text = self._error_text
            self._status_level = "error"
            self.changed.emit()
            return
        if self._current is not None:
            self._service.save_session(self._current)
        self._current = loaded
        self._refresh_messages()
        self._error_text = ""
        self._status_text = _("agent_session_loaded")
        self._status_level = "info"
        self.changed.emit()

    @Slot(str, str)
    def renameSession(self, session_id: str, title: str) -> None:  # noqa: N802
        if self._service is None:
            return
        if self._service.rename_session(str(session_id), str(title)):
            self._refresh_sessions()
            if str(session_id) == self.currentSessionId:
                self._current = self._service.load_session(str(session_id)) or self._current
                self._refresh_messages()
            self._status_text = _("agent_session_renamed")
            self._status_level = "success"
            self.changed.emit()

    @Slot(str)
    def deleteSession(self, session_id: str) -> None:  # noqa: N802
        if self._service is None or self._busy:
            return
        self._service.delete_session(str(session_id))
        self._refresh_sessions()
        if str(session_id) == self.currentSessionId:
            self._current = None
            self._open_initial_session()
        self._status_text = _("agent_session_deleted")
        self._status_level = "info"
        self.changed.emit()

    # ─── 发送 / 停止 / 重试 ─────────────────────────────────

    @Slot(str)
    def send(self, text: str) -> None:  # noqa: N802
        value = str(text or "").strip()
        if not value or self._closing:
            return
        if self._service is None:
            self._fail(_("agent_service_unavailable"))
            return
        if self._busy:
            self._status_text = _("agent_wait_for_reply")
            self._status_level = "warning"
            self.changed.emit()
            return
        if not self._logged_in:
            self._fail(_("agent_no_token_hint"))
            return
        if self._current is None:
            self._current = self._service.new_session()
        try:
            self._service.append_user(self._current, value)
            self._messages = self._service.history(self._current)
            self._token_estimate = self._service.estimate_tokens(self._current)
        except Exception as e:  # noqa: BLE001
            logger.warning("追加用户消息失败: %s", e)
            self._fail(_("agent_processing_error", error=str(e)[:300]))
            return
        self._start_turn()

    @Slot(result=bool)
    def retryLast(self) -> bool:  # noqa: N802
        if self._service is None or self._busy or not self._logged_in:
            return False
        if self._current is None:
            return False
        try:
            text = self._service.prepare_retry(self._current)
        except Exception as e:  # noqa: BLE001
            self._fail(_("agent_processing_error", error=str(e)[:300]))
            return False
        if not text:
            self._status_text = _("agent_nothing_to_retry")
            self._status_level = "warning"
            self.changed.emit()
            return False
        self._messages = self._service.history(self._current)
        self._token_estimate = self._service.estimate_tokens(self._current)
        self._start_turn()
        return True

    @Slot(str, result=bool)
    def quickAsk(self, text: str) -> bool:  # noqa: N802
        """顶栏快速提问。成功接受返回 true；失败时错误已经写进状态条。"""
        value = str(text or "").strip()
        if not value:
            return False
        if self._busy:
            self._status_text = _("agent_wait_for_reply")
            self._status_level = "warning"
            self.changed.emit()
            return False
        if not self._logged_in:
            self._fail(_("agent_no_token_hint"))
            return False
        self.send(value)
        return True

    def _start_turn(self) -> None:
        if self._service is None or self._current is None:
            return
        if self._tasks is None:
            self._fail(_("agent_task_unavailable"))
            return
        self._seq += 1
        seq = self._seq
        self._busy = True
        self._waiting = False
        self._pending_approval = {}
        self._stream_failed = False
        self._stream_index = -1
        self._error_text = ""
        self._status_text = _("agent_thinking")
        self._status_level = "info"
        self._cancel_event = threading.Event()
        cancel_event = self._cancel_event
        self.changed.emit()
        try:
            self._active_handle = self._tasks.submit(
                self._turn_work,
                self._current,
                seq,
                cancel_event,
                name="agent.turn",
                on_done=lambda result, seq=seq: self._finish_turn(seq, result),
                on_error=lambda error, seq=seq: self._finish_error(seq, error),
            )
        except Exception as e:  # noqa: BLE001
            self._busy = False
            self._active_handle = None
            self._cancel_event = None
            self._fail(_("agent_processing_error", error=str(e)[:300]))

    def _turn_work(self, session: Any, seq: int, cancel_event: threading.Event) -> Dict[str, Any]:
        """worker：跑一轮对话，返回最终快照（不碰任何 Qt 对象）。"""
        service = self._service
        if service is None:
            raise AgentChatError("service", "service unavailable")
        callbacks = service.build_callbacks(self._launcher, session)

        def emit(event: Dict[str, Any]) -> None:
            self._post({"kind": "event", "seq": seq, "event": event})

        def confirm(kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
            return self._confirm(seq, kind, payload)

        service.run_turn(session, emit=emit, cancel_event=cancel_event, confirm=confirm, callbacks=callbacks)
        return {
            "seq": seq,
            "messages": service.history(session),
            "todos": service.todos(session.id),
            "tokens": service.estimate_tokens(session),
            "title": str(getattr(session, "title", "") or ""),
        }

    @Slot()
    def stop(self) -> None:
        if not self._busy:
            return
        if self._cancel_event is not None:
            self._cancel_event.set()
        self._waiting = False
        self._pending_approval = {}
        self._release_gate({"accepted": False, "answers": [], "cancelled": True})
        self._status_text = _("agent_stopping")
        self._status_level = "warning"
        self.changed.emit()

    # ─── 工具确认 ───────────────────────────────────────────

    def _confirm(self, seq: int, kind: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """worker 里调用：投递确认请求并等待主线程作答。"""
        if self._closing or (self._cancel_event is not None and self._cancel_event.is_set()):
            return {"accepted": False, "answers": [], "cancelled": True}
        self._gate_seq += 1
        gate_id = "p%d-%d" % (seq, self._gate_seq)
        event = threading.Event()
        gate: Dict[str, Any] = {"id": gate_id, "event": event, "result": None}
        with self._gate_lock:
            self._gate = gate
        request = dict(payload or {})
        request["id"] = gate_id
        request["kind"] = kind
        self._post({"kind": "approval", "seq": seq, "id": gate_id, "request": request})
        while not event.wait(0.2):
            if self._closing or (self._cancel_event is not None and self._cancel_event.is_set()):
                self._release_gate({"accepted": False, "answers": [], "cancelled": True})
                break
        with self._gate_lock:
            result = gate.get("result")
        if not isinstance(result, dict):
            result = {"accepted": False, "answers": [], "cancelled": True}
        return result

    def _release_gate(self, result: Dict[str, Any]) -> None:
        with self._gate_lock:
            gate = self._gate
            self._gate = None
        if gate is None:
            return
        gate["result"] = dict(result or {})
        try:
            gate["event"].set()
        except Exception:
            pass

    @Slot(str, bool, "QVariantList")
    def answerApproval(self, gate_id: str, accepted: bool, answers: List[Any]) -> None:  # noqa: N802
        pending = self._pending_approval
        if not pending or str(pending.get("id") or "") != str(gate_id):
            return
        cleaned = [str(item) for item in (answers or []) if str(item).strip()]
        if not accepted:
            cleaned = []
        self._pending_approval = {}
        self._waiting = False
        self._release_gate({"accepted": bool(accepted), "answers": cleaned})
        self._status_text = _("agent_thinking")
        self._status_level = "info"
        self.changed.emit()

    # ─── 事件投递（worker → 主线程）────────────────────────

    def _post(self, payload: Dict[str, Any]) -> None:
        if self._closing:
            return
        try:
            self.posted.emit(payload)
        except RuntimeError:
            pass

    def _deliver(self, payload: Dict[str, Any]) -> None:
        if self._closing:
            return
        kind = str(payload.get("kind") or "")
        if kind == "approval":
            seq = int(payload.get("seq", 0) or 0)
            if seq != self._seq:
                self._release_gate({"accepted": False, "answers": [], "cancelled": True})
                return
            request = dict(payload.get("request") or {})
            self._pending_approval = request
            self._waiting = True
            self._status_text = _("agent_permission_waiting")
            self._status_level = "warning"
            self.changed.emit()
            return
        if kind == "event":
            seq = int(payload.get("seq", 0) or 0)
            if seq != self._seq:
                return
            event = payload.get("event")
            if isinstance(event, dict):
                self._apply_event(event)
            return
        if kind == "voice":
            self._apply_voice(payload)
            return

    def _apply_event(self, event: Dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        if kind == "assistant_start":
            self._append_stream_message()
            self._status_text = _("agent_thinking")
            self._status_level = "info"
            self.changed.emit()
            return

        if kind in ("text_delta", "thinking_delta"):
            self._update_stream(event, thinking=(kind == "thinking_delta"))
            return

        if kind == "tool_start":
            call_id = str(event.get("toolCallId") or "")
            name = str(event.get("name") or "tool")
            self._finish_streaming()
            card = {
                "id": "t%s" % (call_id or ("live%d" % self._message_seq)),
                "role": "tool",
                "toolCallId": call_id,
                "name": name,
                "args": str(event.get("args") or "{}"),
                "result": "",
                "status": "pending",
            }
            self._messages = list(self._messages) + [card]
            self._status_text = _("agent_tool_running", name=name)
            self._status_level = "info"
            self.changed.emit()
            return

        if kind == "tool_result":
            call_id = str(event.get("toolCallId") or "")
            self._update_tool_result(call_id, event)
            self._todos = self._service.todos(self._current.id) if self._service and self._current else self._todos
            self.changed.emit()
            return

        if kind == "todos":
            items = event.get("items")
            self._todos = [dict(item) for item in items if isinstance(item, dict)] if isinstance(items, list) else []
            self.changed.emit()
            return

        if kind == "usage":
            usage = event.get("usage")
            if isinstance(usage, dict):
                try:
                    total = int(usage.get("total_tokens", 0) or 0)
                except Exception:
                    total = 0
                if total > 0:
                    self._token_estimate = total
                    self.changed.emit()
            return

        if kind in ("done", "stopped", "compacted"):
            self._finish_streaming()
            if kind == "done":
                if not self._stream_failed:
                    self._status_text = _("agent_done")
                    self._status_level = "success"
            elif kind == "stopped":
                self._status_text = _("agent_stopped")
                self._status_level = "info"
            self.changed.emit()
            return

        if kind in ("error", "stream_error"):
            self._finish_streaming()
            self._stream_failed = True
            message = str(event.get("message") or "")
            # 这里**不能**走 _fail：_fail 会把 busy 清掉，而 worker 还没结束
            # （它正在落盘/收尾）。那会允许用户在上一轮还没结束时再发一条，
            # 两个 turn 同时写同一个会话。真正的收尾在 _finish_turn/_finish_error。
            self._error_text = self._friendly_error(message)
            self._status_text = self._error_text
            self._status_level = "error"
            self.changed.emit()
            return

    def _append_stream_message(self) -> None:
        self._message_seq += 1
        self._stream_id = "live%d" % self._message_seq
        message = {
            "id": self._stream_id,
            "role": "assistant",
            "content": "",
            "blocks": [],
            "thinking": "",
            "streaming": True,
        }
        self._stream_index = len(self._messages)
        self._messages = list(self._messages) + [message]

    def _update_stream(self, event: Dict[str, Any], thinking: bool) -> None:
        if not self._stream_id:
            self._append_stream_message()
            self.changed.emit()
        index = self._stream_index
        if index < 0 or index >= len(self._messages):
            return
        message = dict(self._messages[index])
        if str(message.get("id") or "") != self._stream_id:
            return
        if thinking:
            content = event.get("content")
            if content is None:
                content = str(message.get("thinking") or "") + str(event.get("text") or "")
            message["thinking"] = str(content or "")
        else:
            content = event.get("content")
            if content is None:
                content = str(message.get("content") or "") + str(event.get("text") or "")
            message["content"] = str(content or "")
            blocks = event.get("blocks")
            if isinstance(blocks, list):
                message["blocks"] = blocks
        # 原地替换同一条消息；**不重新赋值 self._messages**，ListView 就不会把
        # 整段历史的消息委托全部销毁重建。QML 只收 streamUpdated 更新当前气泡。
        self._messages[index] = message
        self.streamUpdated.emit(
            index,
            str(message.get("content") or ""),
            message.get("blocks") if isinstance(message.get("blocks"), list) else [],
            str(message.get("thinking") or ""),
        )

    def _finish_streaming(self) -> None:
        if not self._stream_id:
            return
        index = self._stream_index
        if 0 <= index < len(self._messages) and str(self._messages[index].get("id") or "") == self._stream_id:
            message = dict(self._messages[index])
            message["streaming"] = False
            self._messages[index] = message
            self._messages = list(self._messages)
        self._stream_id = ""
        self._stream_index = -1

    def _update_tool_result(self, call_id: str, event: Dict[str, Any]) -> None:
        new_messages = list(self._messages)
        found = False
        for index, message in enumerate(new_messages):
            if str(message.get("role") or "") != "tool":
                continue
            if str(message.get("toolCallId") or "") != call_id:
                continue
            updated = dict(message)
            updated["result"] = str(event.get("result") or "")
            updated["status"] = str(event.get("status") or "done")
            new_messages[index] = updated
            found = True
            break
        if not found:
            new_messages.append(
                {
                    "id": "t%s" % call_id,
                    "role": "tool",
                    "toolCallId": call_id,
                    "name": str(event.get("name") or "tool"),
                    "args": "{}",
                    "result": str(event.get("result") or ""),
                    "status": str(event.get("status") or "done"),
                }
            )
        self._messages = new_messages

    def _finish_turn(self, seq: int, result: Any) -> None:
        if seq != self._seq:
            return
        self._busy = False
        self._waiting = False
        self._pending_approval = {}
        self._stream_id = ""
        self._stream_index = -1
        self._active_handle = None
        self._cancel_event = None
        if isinstance(result, dict):
            messages = result.get("messages")
            if isinstance(messages, list):
                self._messages = messages
            todos = result.get("todos")
            if isinstance(todos, list):
                self._todos = todos
            try:
                self._token_estimate = int(result.get("tokens", self._token_estimate) or 0)
            except Exception:
                pass
        self._refresh_sessions()
        if self._status_level != "error" and self._status_text == _("agent_stopping"):
            self._status_text = _("agent_stopped")
            self._status_level = "info"
        self.changed.emit()

    def _finish_error(self, seq: int, error: Any) -> None:
        if seq != self._seq:
            return
        self._busy = False
        self._waiting = False
        self._pending_approval = {}
        self._stream_id = ""
        self._stream_index = -1
        self._active_handle = None
        self._cancel_event = None
        # 失败时把会话从磁盘重读一次：worker 可能在半截回答里落过盘，
        # 页面上应该看到已经保存下来的那部分，而不是永远停在占位块。
        try:
            if self._service is not None and self._current is not None:
                loaded = self._service.load_session(self._current.id)
                if loaded is not None:
                    self._current = loaded
                    self._messages = self._service.history(loaded)
                    self._todos = self._service.todos(loaded.id)
                    self._token_estimate = self._service.estimate_tokens(loaded)
        except Exception:
            pass
        self._fail(self._friendly_error(error))

    def _fail(self, message: str) -> None:
        self._error_text = str(message or "")
        self._status_text = self._error_text
        self._status_level = "error"
        self._busy = False
        self._waiting = False
        self._pending_approval = {}
        self._release_gate({"accepted": False, "answers": [], "cancelled": True})
        self.changed.emit()

    def _friendly_error(self, error: Any) -> str:
        if isinstance(error, AgentChatError):
            if error.kind == "no_token":
                return _("agent_no_token_hint")
            text = error.message or error.kind
        else:
            text = str(error or "")
        lowered = text.lower()
        if "401" in lowered or "token expired" in lowered or "unauthorized" in lowered:
            self._expire_login()
            return _("netread_token_expired")
        return _("agent_processing_error", error=text[:500])

    def _expire_login(self) -> None:
        self._logged_in = False
        if self._account is not None:
            try:
                self._account.logout()
            except Exception as e:  # noqa: BLE001
                logger.warning("清理过期净读 Token 失败: %s", e)
        try:
            self.noticeRequested.emit(_("netread_token_expired"), "error")
        except Exception:
            pass

    # ─── 剪贴板 / 积分 / 语音 ───────────────────────────────

    @Slot(str, result=bool)
    def copyText(self, text: str) -> bool:  # noqa: N802
        value = str(text or "")
        if not value:
            return False
        try:
            from PySide6.QtGui import QGuiApplication

            clipboard = QGuiApplication.clipboard()
            if clipboard is None:
                return False
            clipboard.setText(value)
        except Exception as e:  # noqa: BLE001
            logger.warning("写剪贴板失败: %s", e)
            return False
        self._status_text = _("agent_copied")
        self._status_level = "success"
        self.changed.emit()
        return True

    @Slot(str, result=bool)
    def copyMessage(self, message_id: str) -> bool:  # noqa: N802
        target = str(message_id or "")
        for message in self._messages:
            if str(message.get("id") or "") != target:
                continue
            text = message.get("result") if message.get("role") == "tool" else message.get("content")
            return self.copyText(str(text or ""))
        return False

    def _refresh_credits(self) -> None:
        if self._account is None or self._tasks is None or not self._logged_in:
            return
        token = ""
        try:
            token = str(self._account.current_token() or "")
        except Exception:
            token = ""
        if not token:
            return
        try:
            self._tasks.submit(
                self._credits_work,
                token,
                name="agent.credits",
                on_done=self._apply_credits,
                on_error=lambda _error: None,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("积分刷新任务提交失败: %s", e)

    def _credits_work(self, token: str) -> int:
        try:
            info = self._account.fetch_user_info(token) if self._account is not None else None
        except Exception:
            return -1
        if not isinstance(info, dict):
            return -1
        try:
            return int(info.get("ai_credits", 0) or 0)
        except Exception:
            return -1

    def _apply_credits(self, value: Any) -> None:
        try:
            credits = int(value)
        except Exception:
            return
        if credits >= 0:
            self._credits = credits
            self.changed.emit()

    @Slot()
    def toggleVoice(self) -> None:  # noqa: N802
        if self._closing:
            return
        if self._voice_service is None and self._context is not None:
            try:
                self._voice_service = self._context.try_get("voice")
            except Exception as e:  # noqa: BLE001
                logger.warning("取语音服务失败: %s", e)
        service = self._voice_service
        if service is None:
            self._voice_state = "unavailable"
            self._voice_error = _("voice_btn_unavailable")
            self.changed.emit()
            return
        ok, reason = service.is_available()
        if not ok:
            self._voice_state = "unavailable"
            self._voice_error = str(reason or _("voice_btn_unavailable"))
            self.changed.emit()
            self.noticeRequested.emit(self._voice_error, "warning")
            return
        try:
            from services.voice.models import is_model_ready

            if not is_model_ready():
                self._voice_state = "unavailable"
                self._voice_error = _("agent_voice_model_missing")
                self.changed.emit()
                return
        except Exception as e:  # noqa: BLE001
            self._voice_state = "unavailable"
            self._voice_error = _("voice_error_unknown", err=str(e))
            self.changed.emit()
            return
        if self._voice_listener is None:
            self._voice_listener = _VoiceListener(self._post)
            try:
                service.register(self._voice_listener)
            except Exception as e:  # noqa: BLE001
                logger.warning("注册语音监听失败: %s", e)
                self._voice_state = "unavailable"
                self._voice_error = _("voice_btn_unavailable")
                self.changed.emit()
                return
        try:
            service.set_session_runner(lambda fn: self._tasks.submit(fn, name="agent.voice"))
        except Exception:
            pass
        try:
            service.toggle(self._voice_listener)
        except Exception as e:  # noqa: BLE001
            self._voice_state = "unavailable"
            self._voice_error = _("voice_error_unknown", err=str(e))
            self.changed.emit()
            return
        self._voice_error = ""
        self._voice_state = "recording" if service.get_state() == "recording" else "loading"
        self._voice_timer.start()
        self.changed.emit()

    def _poll_voice(self) -> None:
        if self._voice_service is None or self._voice_listener is None:
            self._voice_timer.stop()
            return
        try:
            events = self._voice_service.poll_once(self._voice_listener)
        except Exception as e:  # noqa: BLE001
            logger.warning("轮询语音事件失败: %s", e)
            self._voice_timer.stop()
            return
        for event in events:
            if not isinstance(event, tuple) or not event:
                continue
            event_type = str(event[0] or "")
            values = list(event[1:])
            if event_type == "state":
                self._apply_voice({"kind": "voice", "event": "state", "state": values[0] if values else "idle"})
            elif event_type == "final":
                self._apply_voice({"kind": "voice", "event": "final", "text": values[0] if values else ""})
            elif event_type == "error":
                self._apply_voice({"kind": "voice", "event": "error", "message": values[0] if values else ""})
            elif event_type == "progress":
                self._apply_voice({"kind": "voice", "event": "progress", "percent": values[0] if values else 0})

    def _apply_voice(self, payload: Dict[str, Any]) -> None:
        kind = str(payload.get("event") or "")
        if kind == "state":
            state = str(payload.get("state") or "idle")
            mapping = {
                "recording": "recording",
                "recognizing": "recognizing",
                "loading": "loading",
                "downloading": "loading",
                "idle": "idle",
            }
            self._voice_state = mapping.get(state, state)
            if self._voice_state == "idle":
                self._voice_timer.stop()
            self.changed.emit()
            return
        if kind == "final":
            text = str(payload.get("text") or "")
            self._voice_state = "idle"
            self._voice_error = ""
            self._voice_timer.stop()
            if text:
                self.voiceText.emit(text)
                self._status_text = _("agent_voice_inserted")
                self._status_level = "success"
            self.changed.emit()
            return
        if kind == "error":
            self._voice_state = "idle"
            self._voice_error = str(payload.get("message") or _("voice_error_unknown", err=""))
            self._voice_timer.stop()
            self.noticeRequested.emit(self._voice_error, "error")
            self.changed.emit()
            return
        if kind == "progress":
            self._voice_state = "loading"
            self.changed.emit()
