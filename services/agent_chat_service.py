"""AGENT 对话服务（阶段 3 任务 3.22）。

职责边界：

- **不碰 Qt、不弹窗、不起线程**。流式循环在本模块里跑，事件通过调用方注入的
  `emit` 回调送出；需要用户点头时调用注入的 `confirm`（它会阻塞到用户作答）。
- 会话持久化、工具执行、权限判定、技能/系统提示词全部复用 1.14 已迁移的
  `services.agent.*`，本模块只补上"QML 版循环"的编排。
- 不把 QML 对象带进服务：事件里只有 dict/list/str/int/bool，worker 线程可以
  安全地把它交给桥的排队信号。

**与旧 Tk 循环的差异**（都是有意的）：

1. 旧 `run_loop` 以 `sink` 为界面接口；QML 没有 Tk 控件，这里改为事件流。
2. 旧代码在一次模型回复里可能串行执行多个 tool_calls；这里照旧支持，界面用
   独立工具卡分别跟踪。
3. 流式失败回退非流式只在**没有收到任何文本**时发生，避免同一段回答重复两遍。
4. 停止是协作式的：每个 SSE 事件、每次工具执行前检查 cancel_event；已生成的
   半截回答仍会落盘，不会丢。
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from logzero import logger

from services.agent.models import get_default_model, get_model_by_id
from services.agent.permission import check_permission, init_permission_manager
from services.agent.providers.jingdu import JingduProvider
from services.agent.session import AgentSession
from services.agent.tool_registry import get_registry
from services.agent.tools.files import FILE_EDIT_MARKER
from services.agent.tools.system import DANGEROUS_MARKER, execute_dangerous_command
from services.agent.tools.todo_write import load_todos
from services.agent.tools.user import ASK_USER_MARKER
from services.agent_markdown import parse_markdown
from services.base import Service

#: 与旧 `AgentChatView.MAX_ITERATIONS` 一致。
MAX_ITERATIONS = 50

#: 工具结果在 UI 中保留的最大字符数（完整结果仍在会话 JSON 里）。
UI_RESULT_LIMIT = 4000

#: 流式文本推送节拍（秒）。流式阶段只推纯文本，最终答案才解析一次 Markdown；
#: 每个 token 都发事件 / 每帧都重建富文本，会让 QML 在长回答时明显卡顿。
STREAM_UPDATE_INTERVAL = 0.12
#: 兼容旧名字（思考流也复用这个节拍）。
PARSE_INTERVAL = STREAM_UPDATE_INTERVAL


class AgentChatError(Exception):
    """对话层可预期的失败（未登录、无模型等），由桥翻译成界面文案。"""

    def __init__(self, kind: str, message: str = "") -> None:
        super().__init__(message or kind)
        self.kind = kind
        self.message = message or kind


def _trigger_agent_ach(achievement_id: str, value: int = 1) -> None:
    """复用旧 AGENT 的成就触发器（保持与 Tk 版同一个引擎入口）。"""
    try:
        from services.achievement_engine import get_achievement_engine

        engine = get_achievement_engine()
        if engine:
            engine.update_progress(achievement_id, value=value)
    except Exception:
        pass


def _safe_json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _result_failed(text: str) -> bool:
    value = str(text or "").lstrip()
    return value.startswith(("错误", "❌", "[ERROR]", "Error:", "错误:"))


def _tool_preview(text: str, limit: int = UI_RESULT_LIMIT) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[:limit] + "\n..."


def _message_text(message: Dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # OpenAI 新格式可能是 [{type:"text", text:"..."}]；旧代码只处理字符串，
        # 这里做一次无损失的降级，保证历史不会被读成 "[object Object]"。
        parts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _tool_call_map(messages: List[Dict]) -> Dict[str, Dict]:
    mapping: Dict[str, Dict] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id") or "")
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            if call_id:
                mapping[call_id] = {
                    "id": call_id,
                    "name": str(function.get("name") or ""),
                    "arguments": str(function.get("arguments") or "{}"),
                }
    return mapping


class AgentChatService(Service):
    """QML AGENT 的会话 / 流式 / 工具编排服务。"""

    name = "agent_chat"
    label = "AI 对话"
    requires = ("agent",)

    def __init__(self, context=None) -> None:
        super().__init__(context)
        self._config_ready = False
        self._save_lock = threading.Lock()

    # ─── 基础 ────────────────────────────────────────────────

    def _agent(self):
        if self._context is None:
            from services.agent_service import AgentService

            return AgentService()
        return self.context.try_get("agent")

    def ensure_config(self) -> None:
        """初始化 Agent 配置（只做一次；失败不致命，走默认配置）。"""
        if self._config_ready:
            return
        try:
            agent = self._agent()
            if agent is not None:
                path = ""
                try:
                    path = str(getattr(self.context.config, "config_file", "") or "")
                except Exception:
                    path = ""
                agent.init_config(path or os.path.join(os.getcwd(), "config.json"))
                try:
                    init_permission_manager(agent.get_config().permissions)
                except Exception as e:  # noqa: BLE001
                    logger.warning("[AgentChat] 初始化权限规则失败: %s", e)
            self._config_ready = True
        except Exception as e:  # noqa: BLE001
            logger.warning("[AgentChat] 初始化配置失败: %s", e)
            self._config_ready = True

    def reload_permissions(self) -> None:
        cfg = self.agent_config()
        try:
            init_permission_manager(cfg.permissions if cfg is not None else None)
        except Exception as e:  # noqa: BLE001
            logger.warning("[AgentChat] 重载权限规则失败: %s", e)

    def system_prompt(self) -> str:
        self.ensure_config()
        agent = self._agent()
        if agent is not None:
            try:
                return str(agent.system_prompt_text() or "")
            except Exception as e:  # noqa: BLE001
                logger.warning("[AgentChat] 组装系统提示词失败: %s", e)
        return ""

    def agent_config(self):
        """当前 AgentConfig（桥与设置 UI 共用；失败返回 None）。"""
        self.ensure_config()
        agent = self._agent()
        try:
            return agent.get_config() if agent is not None else None
        except Exception as e:  # noqa: BLE001
            logger.warning("[AgentChat] 读取 Agent 配置失败: %s", e)
            return None

    def provider_id(self) -> str:
        cfg = self.agent_config()
        pid = str(getattr(cfg, "active_provider", "jingdu") or "jingdu")
        return pid if pid in ("jingdu", "openai", "anthropic", "custom") else "jingdu"

    def provider_ready(self, provider_id: str = "") -> bool:
        pid = provider_id or self.provider_id()
        if pid == "jingdu":
            return bool(self.token())
        cfg = self.agent_config()
        pc = cfg.providers.get(pid) if cfg is not None else None
        return bool(pc and pc.api_key)

    def model_id(self) -> str:
        """当前 provider 的模型；没有显式配置时取该 provider 的默认模型。"""
        pid = self.provider_id()
        cfg = self.agent_config()
        configured = ""
        if cfg is not None:
            configured = str((cfg.active_models or {}).get(pid, "") or "")
        if configured:
            info = get_model_by_id(configured)
            if pid == "custom" or (info is not None and info.provider_id == pid):
                return configured
        default = get_default_model(pid)
        if default is not None:
            return default.id
        if pid == "custom" and cfg is not None:
            pc = cfg.providers.get("custom")
            values = list(getattr(pc, "custom_models", []) or []) if pc else []
            if values:
                return str(values[0])
        return "deepseek-v4-flash"

    def model_label(self) -> str:
        model = self.model_id()
        info = get_model_by_id(model)
        if info is None:
            return model
        return str(getattr(info, "name", "") or model)

    def provider_label(self) -> str:
        return {"jingdu": "净读 AI", "openai": "OpenAI", "anthropic": "Anthropic", "custom": "自定义"}.get(
            self.provider_id(), self.provider_id()
        )

    def thinking_supported(self) -> bool:
        info = get_model_by_id(self.model_id())
        return bool(info and info.supports_reasoning)

    def thinking_enabled(self) -> bool:
        info = get_model_by_id(self.model_id())
        default = bool(getattr(info, "thinking_default", False)) if info is not None else False
        cfg = self.agent_config()
        try:
            return bool((cfg.thinking or {}).get(self.model_id(), default))
        except Exception:
            return default

    def reasoning_effort(self) -> str:
        cfg = self.agent_config()
        try:
            value = str((cfg.reasoning_efforts or {}).get(self.model_id(), "") or "")
        except Exception:
            value = ""
        return value or ("max" if self.provider_id() == "jingdu" else "high")

    @staticmethod
    def effort_options(provider_id: str) -> List[str]:
        return {
            "jingdu": ["high", "max"],
            "openai": ["none", "low", "medium", "high", "xhigh"],
            "anthropic": ["low", "medium", "high", "xhigh", "max"],
            "custom": ["low", "medium", "high"],
        }.get(provider_id, ["medium", "high"])

    def token(self) -> str:
        if self._context is None:
            return ""
        try:
            return str(getattr(self.context.config, "jdz_token", "") or "").strip()
        except Exception:
            return ""

    def logged_in(self) -> bool:
        """当前 provider 是否可发请求（净读看 Token，其它 provider 看 API Key）。"""
        return self.provider_ready()

    # ─── 会话 ────────────────────────────────────────────────

    def new_session(self) -> AgentSession:
        self.ensure_config()
        return AgentSession.create_new(
            provider_id=self.provider_id(), model_id=self.model_id(), system_prompt=self.system_prompt()
        )

    def list_sessions(self) -> List[Dict]:
        try:
            rows = AgentSession.list_all()
        except Exception as e:  # noqa: BLE001
            logger.warning("[AgentChat] 读取会话列表失败: %s", e)
            return []
        return [dict(row) for row in rows if isinstance(row, dict)]

    def load_session(self, session_id: str) -> Optional[AgentSession]:
        if not str(session_id or "").strip():
            return None
        try:
            return AgentSession.load(str(session_id))
        except Exception as e:  # noqa: BLE001
            logger.warning("[AgentChat] 加载会话失败: %s", e)
            return None

    def save_session(self, session: AgentSession) -> None:
        if session is None:
            return
        try:
            with self._save_lock:
                session.save()
        except Exception as e:  # noqa: BLE001
            logger.warning("[AgentChat] 保存会话失败: %s", e)

    def delete_session(self, session_id: str) -> bool:
        try:
            AgentSession.delete(str(session_id))
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[AgentChat] 删除会话失败: %s", e)
            return False

    def rename_session(self, session_id: str, title: str) -> bool:
        text = str(title or "").strip()
        if not text:
            return False
        session = self.load_session(session_id)
        if session is None:
            return False
        session.title = text[:50]
        self.save_session(session)
        return True

    def append_user(self, session: AgentSession, text: str) -> bool:
        """追加用户消息并触发首次对话成就；返回是否首次添加用户消息。"""
        if session is None:
            return False
        had_user = any(m.get("role") == "user" for m in session.messages if isinstance(m, dict))
        session.add_message({"role": "user", "content": str(text)})
        if not session.title:
            session.set_title(str(text))
        if not had_user:
            _trigger_agent_ach("agent_first_chat")
        return not had_user

    def prepare_retry(self, session: AgentSession) -> str:
        """删掉最后一条助手回复及其工具结果，保留最后一条用户消息。

        返回最后一条用户文本；没有可重试的用户消息时返回空串。
        """
        if session is None:
            return ""
        messages = list(session.messages)
        last_user = -1
        for index, message in enumerate(messages):
            if isinstance(message, dict) and message.get("role") == "user":
                last_user = index
        if last_user < 0:
            return ""
        user_text = _message_text(messages[last_user])
        # 保留 system + 最后一条 user 之前的内容 + 这条 user；删除其后的回答/工具。
        session.messages = messages[: last_user + 1]
        session.updated_at = time.time()
        return user_text

    def history(self, session: AgentSession) -> List[Dict]:
        """把 AgentSession 的消息转成 QML 消息卡列表。"""
        if session is None:
            return []
        calls = _tool_call_map(session.messages)
        messages: List[Dict] = []
        tool_cards: Dict[str, Dict] = {}
        for index, raw in enumerate(session.messages):
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or "")
            if role == "system":
                continue
            content = _message_text(raw)
            if role == "user":
                messages.append(
                    {
                        "id": "m%d" % index,
                        "role": "user",
                        "content": content,
                        "blocks": parse_markdown(content),
                        "thinking": "",
                        "streaming": False,
                    }
                )
            elif role == "assistant":
                thinking = str(raw.get("_thinking") or "")
                if content or thinking:
                    messages.append(
                        {
                            "id": "m%d" % index,
                            "role": "assistant",
                            "content": content,
                            "blocks": parse_markdown(content),
                            "thinking": thinking,
                            "streaming": False,
                        }
                    )
                for call in raw.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    call_id = str(call.get("id") or "")
                    function = call.get("function") if isinstance(call.get("function"), dict) else {}
                    card = {
                        "id": "t%s" % (call_id or ("%d_%d" % (index, len(tool_cards)))),
                        "role": "tool",
                        "toolCallId": call_id,
                        "name": str(function.get("name") or ""),
                        "args": str(function.get("arguments") or "{}"),
                        "result": "",
                        "status": "pending",
                        "toolHint": "",
                    }
                    messages.append(card)
                    if call_id:
                        tool_cards[call_id] = card
            elif role == "tool":
                call_id = str(raw.get("tool_call_id") or "")
                card = tool_cards.get(call_id)
                info = calls.get(call_id, {})
                if card is None:
                    card = {
                        "id": "t%s" % (call_id or ("tool%d" % index)),
                        "role": "tool",
                        "toolCallId": call_id,
                        "name": str(info.get("name") or "tool"),
                        "args": str(info.get("arguments") or "{}"),
                        "result": "",
                        "status": "pending",
                        "toolHint": "",
                    }
                    messages.append(card)
                    if call_id:
                        tool_cards[call_id] = card
                card["result"] = _tool_preview(content)
                if "用户取消" in content or "被权限策略禁止" in content:
                    card["status"] = "denied"
                else:
                    card["status"] = "error" if _result_failed(content) else "done"
        return messages

    def todos(self, session_id: str) -> List[Dict]:
        if not session_id:
            return []
        try:
            return [dict(item) for item in load_todos(session_id) if isinstance(item, dict)]
        except Exception as e:  # noqa: BLE001
            logger.warning("[AgentChat] 读取待办失败: %s", e)
            return []

    def estimate_tokens(self, session: AgentSession) -> int:
        try:
            return int(session.estimate_tokens()) if session is not None else 0
        except Exception:
            return 0

    # ─── Provider / 工具回调 ────────────────────────────────

    def build_provider(self):
        """按当前 provider 建实例；净读走账号 Token，其它 provider 走 API Key。"""
        pid = self.provider_id()
        thinking = bool(self.thinking_enabled())
        effort = self.reasoning_effort() if thinking else ""
        try:
            if pid == "jingdu":
                token = self.token()
                if not token:
                    raise AgentChatError("no_token")
                return JingduProvider(api_key=token, thinking_enabled=thinking, reasoning_effort=effort)
            cfg = self.agent_config()
            pc = cfg.providers.get(pid) if cfg is not None else None
            if pc is None or not pc.api_key:
                raise AgentChatError("no_provider", pid)
            from services.agent_service import build_provider as _build_provider

            provider = _build_provider(pid, pc, thinking, effort)
            if provider is None:
                raise AgentChatError("no_provider", pid)
            return provider
        except AgentChatError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AgentChatError("provider", str(e)) from e

    def build_callbacks(self, launcher: Any, session: AgentSession) -> Dict[str, Callable]:
        """组装工具回调：核心启动器优先，缺失时给安全降级。

        **必须在这里懒取 launcher**：桥的 bind() 早于启动链完成，那时上下文里还没有
        `launcher`（启动流程把核心实例注册进去是随后的事）。如果只在绑定时抓一次，
        所有版本/服务器/整合包工具都会退化成空列表 —— 用户看到的就是
        “获取可用版本返回空”。
        """
        if launcher is None and self.attached:
            try:
                launcher = self.try_get("launcher")
            except Exception as e:  # noqa: BLE001
                logger.debug("[AgentChat] 懒取 launcher 失败: %s", e)
        callbacks: Dict[str, Callable] = {}
        try:
            from services.modpack_service import launcher_callbacks

            callbacks.update(launcher_callbacks(launcher))
        except Exception as e:  # noqa: BLE001
            logger.warning("[AgentChat] 读取整合包回调失败: %s", e)

        names = (
            "get_available_versions",
            "get_installed_versions",
            "install_version",
            "launch_game",
            "remove_version",
            "get_installed_servers",
            "start_server",
            "remove_server",
            "get_minecraft_dir",
            "get_game_process",
        )
        for name in names:
            fn = getattr(launcher, name, None)
            if callable(fn):
                callbacks[name] = fn

        if "get_minecraft_dir" not in callbacks:
            try:
                base = str(getattr(self.context.config, "minecraft_dir", "") or "")
            except Exception:
                base = ""
            callbacks["get_minecraft_dir"] = lambda base=base: base
        if "get_installed_versions" not in callbacks:
            callbacks["get_installed_versions"] = lambda: []
        if "get_available_versions" not in callbacks:
            callbacks["get_available_versions"] = lambda: []
        if "get_installed_servers" not in callbacks:
            callbacks["get_installed_servers"] = lambda: []

        callbacks["get_current_session_id"] = lambda: session.id if session is not None else ""
        try:
            agent = self._agent()
            key = agent.bing_api_key() if agent is not None else ""
        except Exception:
            key = ""
        callbacks["get_config"] = lambda key=key: {"bing_api_key": key}
        return callbacks

    # ─── 流式循环 ────────────────────────────────────────────

    def run_turn(
        self,
        session: AgentSession,
        emit: Callable[[Dict], None],
        cancel_event: threading.Event,
        confirm: Callable[[str, Dict], Dict],
        callbacks: Optional[Dict[str, Callable]] = None,
    ) -> None:
        """跑一轮完整的"用户消息 → 流式回答 → 工具循环"。

        调用方负责把用户消息先写进 session（`append_user`）。本方法返回时
        会话已落盘；异常通过 `{"type":"error"}` 事件送出，不向 worker 外抛。
        """
        if session is None:
            emit({"type": "error", "message": "session unavailable"})
            return
        provider = self.build_provider()
        registry = get_registry()
        cbs = callbacks if callbacks is not None else self.build_callbacks(None, session)
        model = self.model_id()
        session.provider_id = self.provider_id()
        session.model_id = model
        started = time.monotonic()
        total_usage: Dict[str, Any] = {}

        def safe_emit(event: Dict) -> None:
            if not isinstance(event, dict):
                return
            try:
                emit(event)
            except Exception as e:  # noqa: BLE001 - 界面已销毁也不能打断落盘
                logger.debug("[AgentChat] 事件投递失败: %s", e)

        def cancelled() -> bool:
            try:
                return bool(cancel_event and cancel_event.is_set())
            except Exception:
                return False

        logger.info("[AgentChat] 开始对话: session=%s model=%s", session.id, model)
        try:
            for iteration in range(1, MAX_ITERATIONS + 1):
                if cancelled():
                    safe_emit({"type": "stopped"})
                    return
                if iteration > 1:
                    _trigger_agent_ach("agent_multi_turn")
                if iteration % 10 == 0 and session.estimate_tokens() > 60000:
                    session.compact()
                    safe_emit({"type": "compacted", "tokens": session.estimate_tokens()})

                tools = registry.get_definitions()
                safe_emit({"type": "assistant_start"})
                text, thinking, tool_calls = self._stream_once(
                    session=session, provider=provider, model=model, tools=tools, emit=safe_emit, cancelled=cancelled
                )
                if cancelled():
                    # 半截回答也要留下；用户点停止不是异常。
                    if text or thinking:
                        self._append_assistant(session, text, thinking, [])
                    self.save_session(session)
                    safe_emit({"type": "stopped"})
                    return

                if text or thinking or tool_calls:
                    self._append_assistant(session, text, thinking, tool_calls)
                    self.save_session(session)

                if not tool_calls:
                    session.updated_at = time.time()
                    self.save_session(session)
                    safe_emit(
                        {
                            "type": "done",
                            "tokens": session.estimate_tokens(),
                            "elapsed_ms": int((time.monotonic() - started) * 1000),
                            "usage": total_usage,
                        }
                    )
                    _trigger_agent_ach("agent_nlp_master")
                    return

                for call in tool_calls:
                    if cancelled():
                        safe_emit({"type": "stopped"})
                        return
                    self._run_tool_call(session, call, registry, cbs, safe_emit, confirm, cancelled)
                    self.save_session(session)
                # 工具结果已写回会话，进入下一轮模型调用。
            safe_emit({"type": "error", "message": "达到最大工具循环次数（%d）" % MAX_ITERATIONS})
        except AgentChatError as e:
            safe_emit({"type": "error", "kind": e.kind, "message": e.message})
        except Exception as e:  # noqa: BLE001
            logger.error("[AgentChat] 对话循环异常: %s", e, exc_info=True)
            safe_emit({"type": "error", "kind": "unknown", "message": str(e)})
        finally:
            # 任何路径都保存一次；停止/异常时半截消息也不丢。
            try:
                self.save_session(session)
            except Exception:
                pass

    def _stream_once(
        self,
        session: AgentSession,
        provider: Any,
        model: str,
        tools: List[Dict],
        emit: Callable,
        cancelled: Callable[[], bool],
    ) -> Tuple[str, str, List[Dict]]:
        """跑一次流式请求；失败且没有任何文本时回退非流式。"""
        text = ""
        thinking = ""
        tool_calls: Dict[str, Dict] = {}
        seen_error = ""
        last_parse = 0.0
        last_thinking = 0.0

        def emit_text(delta: str) -> None:
            nonlocal last_parse
            now = time.monotonic()
            if now - last_parse >= STREAM_UPDATE_INTERVAL:
                last_parse = now
                # 流式阶段**不解析 Markdown**：只把纯文本推给界面。最终答案/停止时
                # 才解析一次 blocks，避免每个节拍重建整棵富文本树。
                emit({"type": "text_delta", "text": delta, "content": text})
            # 未到解析节拍时**不投递事件**：以前每个 token 都发一条只有 text 的
            # text_delta，桥每次都会整体替换消息模型，QML 在高速输出时被刷到无响应。
            # 文本会在下一次 blocks 事件或最终的强制 blocks 事件里补齐。

        try:
            stream = provider.stream_chat(messages=session.messages, tools=tools, model=model)
            for event in stream:
                if cancelled():
                    break
                if not isinstance(event, dict):
                    continue
                kind = str(event.get("type") or "")
                if kind == "text_delta":
                    delta = str(event.get("text") or "")
                    if delta:
                        text += delta
                        emit_text(delta)
                elif kind == "thinking_delta":
                    delta = str(event.get("text") or "")
                    if delta:
                        thinking += delta
                        now = time.monotonic()
                        if now - last_thinking >= PARSE_INTERVAL:
                            last_thinking = now
                            emit({"type": "thinking_delta", "text": "", "content": thinking})
                elif kind == "tool_call_start":
                    call_id = str(event.get("tool_call_id") or "")
                    if call_id:
                        tool_calls[call_id] = {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        }
                elif kind == "tool_call_name":
                    call_id = str(event.get("tool_call_id") or "")
                    call = tool_calls.setdefault(
                        call_id, {"id": call_id, "type": "function", "function": {"name": "", "arguments": ""}}
                    )
                    call["function"]["name"] = str(event.get("tool_name") or "")
                elif kind == "tool_call_args":
                    call_id = str(event.get("tool_call_id") or "")
                    call = tool_calls.setdefault(
                        call_id, {"id": call_id, "type": "function", "function": {"name": "", "arguments": ""}}
                    )
                    call["function"]["arguments"] += str(event.get("tool_args") or "")
                elif kind == "tool_call_complete":
                    call = event.get("tool_call")
                    if isinstance(call, dict) and call.get("id"):
                        tool_calls[str(call["id"])] = call
                elif kind == "usage":
                    usage = event.get("usage")
                    if isinstance(usage, dict):
                        emit({"type": "usage", "usage": usage})
                elif kind == "error":
                    seen_error = str(event.get("message") or "stream error")
                    break
                elif kind == "done":
                    continue
        except Exception as e:  # noqa: BLE001
            seen_error = str(e)
            logger.warning("[AgentChat] 流式异常: %s", e)

        if cancelled():
            if text:
                emit({"type": "text_delta", "text": "", "content": text, "blocks": parse_markdown(text)})
            return text, thinking, []

        # 流式失败且没有拿到任何文本：旧行为要求回退非流式。
        if seen_error and not text and not tool_calls:
            logger.info("[AgentChat] 流式失败，回退非流式: %s", seen_error)
            try:
                response = provider.chat(messages=session.messages, tools=tools, model=model)
                content = _message_text(response if isinstance(response, dict) else {})
                if content:
                    text = content
                    emit({"type": "text_delta", "text": content, "content": content, "blocks": parse_markdown(content)})
                for call in (response or {}).get("tool_calls") or []:
                    if isinstance(call, dict) and call.get("id"):
                        tool_calls[str(call["id"])] = call
                seen_error = ""
            except Exception as e:  # noqa: BLE001
                seen_error = str(e)

        if seen_error and not text and not tool_calls:
            emit({"type": "stream_error", "message": seen_error})
            raise AgentChatError("stream_error", seen_error)
        if seen_error:
            # 已经收到半截回答：保留它，但让界面知道这不是完整成功（不要显示
            # “回答完成”）。会话仍会落盘，用户可以直接重发。
            emit({"type": "stream_error", "message": seen_error, "partial": True})

        if not text and not thinking and not tool_calls:
            if seen_error:
                raise AgentChatError("stream_error", seen_error)
            raise AgentChatError("empty_response", "empty response")

        if thinking:
            emit({"type": "thinking_delta", "text": "", "content": thinking})
        if text:
            emit({"type": "text_delta", "text": "", "content": text, "blocks": parse_markdown(text)})
        return text, thinking, list(tool_calls.values())

    def _append_assistant(self, session: AgentSession, text: str, thinking: str, tool_calls: List[Dict]) -> None:
        message: Dict[str, Any] = {"role": "assistant", "content": text}
        if tool_calls:
            message["tool_calls"] = tool_calls
        if thinking:
            message["_thinking"] = thinking
        session.add_message(message)

    def _run_tool_call(
        self,
        session: AgentSession,
        call: Dict,
        registry: Any,
        callbacks: Dict[str, Callable],
        emit: Callable,
        confirm: Callable[[str, Dict], Dict],
        cancelled: Callable[[], bool],
    ) -> None:
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = str(function.get("name") or "")
        call_id = str(call.get("id") or "")
        raw_args = function.get("arguments") or "{}"
        try:
            params = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except (TypeError, ValueError, json.JSONDecodeError):
            params = {}
        if not isinstance(params, dict):
            params = {}

        emit({"type": "tool_start", "toolCallId": call_id, "name": name, "args": _safe_json(params)})
        if cancelled():
            return

        effect = "allow"
        try:
            effect = str(check_permission(name) or "allow")
        except Exception as e:  # noqa: BLE001
            logger.warning("[AgentChat] 权限判定失败，按 allow 处理: %s", e)

        if effect == "deny":
            result = "工具 %s 被权限策略禁止" % name
            self._tool_done(session, emit, call_id, name, result, "denied")
            return

        if effect == "ask" and name == "exec_command":
            result_text = registry.execute(name, params, callbacks)
            if result_text.startswith(DANGEROUS_MARKER):
                payload = self._marker_payload(result_text, DANGEROUS_MARKER)
                request = {
                    "kind": "dangerous",
                    "name": name,
                    "path": str(payload.get("path") or ""),
                    "command": str(payload.get("command") or ""),
                    "toolCallId": call_id,
                }
                answer = confirm("dangerous", request)
                if not answer.get("accepted"):
                    result = "用户取消了命令执行\n路径: %s\n命令: %s" % (request["path"], request["command"])
                    self._tool_done(session, emit, call_id, name, result, "denied")
                    return
                _trigger_agent_ach("agent_terminal_warrior")
                result = execute_dangerous_command(request["path"], request["command"])
                self._tool_done(session, emit, call_id, name, result)
                return
            self._tool_done(session, emit, call_id, name, result_text)
            return

        if effect == "ask" and name in ("write_file", "replace_in_file", "delete_file"):
            result_text = registry.execute(name, params, callbacks)
            if result_text.startswith(FILE_EDIT_MARKER):
                payload = self._marker_payload(result_text, FILE_EDIT_MARKER)
                data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                op_type = str(payload.get("op") or "")
                request = {
                    "kind": "file_edit",
                    "name": name,
                    "op": op_type,
                    "path": str(data.get("filePath") or ""),
                    "summary": str(payload.get("summary") or ""),
                    "toolCallId": call_id,
                }
                answer = confirm("file_edit", request)
                if not answer.get("accepted"):
                    result = "用户取消了文件操作\n类型: %s\n文件: %s" % (op_type, request["path"])
                    self._tool_done(session, emit, call_id, name, result, "denied")
                    return
                result = self.apply_file_edit(data, op_type)
                self._tool_done(session, emit, call_id, name, result)
                return
            self._tool_done(session, emit, call_id, name, result_text)
            return

        # 其余工具（含 ask_user）先执行；ask_user 的标记由下面拦截。
        result_text = registry.execute(name, params, callbacks)
        if name == "ask_user" and result_text.startswith(ASK_USER_MARKER):
            payload_text = result_text[len(ASK_USER_MARKER) + 1 :]
            try:
                questions = json.loads(payload_text)
            except (TypeError, ValueError, json.JSONDecodeError):
                questions = []
            if isinstance(questions, list) and questions:
                answer = confirm("ask_user", {"kind": "ask_user", "questions": questions, "toolCallId": call_id})
                answers = answer.get("answers") if isinstance(answer.get("answers"), list) else []
                if not answers:
                    answers = ["用户未回答"]
                result = "用户回答: %s" % ", ".join(str(item) for item in answers)
                emit({"type": "ask_answered", "answers": [str(item) for item in answers]})
                self._tool_done(session, emit, call_id, name, result)
                return
        self._tool_done(session, emit, call_id, name, result_text)

    def _tool_done(
        self, session: AgentSession, emit: Callable, call_id: str, name: str, result: str, status: str = ""
    ) -> None:
        text = str(result or "")
        session.add_message({"role": "tool", "tool_call_id": call_id, "content": text})
        emit(
            {
                "type": "tool_result",
                "toolCallId": call_id,
                "name": name,
                "result": _tool_preview(text),
                "status": status or ("error" if _result_failed(text) else "done"),
            }
        )
        emit({"type": "todos", "items": self.todos(session.id)})

    @staticmethod
    def _marker_payload(text: str, marker: str) -> Dict:
        try:
            rest = text[len(marker) + 1 :]
            payload = json.loads(rest)
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def apply_file_edit(confirm_data: Dict, op_type: str) -> str:
        # 与旧 AgentService.apply_file_edit 同一实现；本服务不 import GUI。
        file_path = str(confirm_data.get("filePath") or "")
        try:
            if op_type in ("write", "replace"):
                parent = os.path.dirname(file_path)
                if parent and not os.path.isdir(parent):
                    os.makedirs(parent, exist_ok=True)
                content = str(confirm_data.get("newText") or "")
                with open(file_path, "w", encoding="utf-8") as handle:
                    handle.write(content)
                if op_type == "replace":
                    replacements = confirm_data.get("replacements", 1)
                    return "已替换 %s 处: %s" % (replacements, file_path)
                return "已写入文件: %s" % file_path
            if op_type == "delete":
                os.remove(file_path)
                return "已删除文件: %s" % file_path
            return "未知操作类型: %s" % op_type
        except Exception as e:  # noqa: BLE001
            return "文件操作失败: %s" % e


__all__ = ["AgentChatError", "AgentChatService", "MAX_ITERATIONS"]
