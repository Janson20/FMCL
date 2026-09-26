"""AGENT 服务 —— `ui/agent/` 里"逻辑与界面混在一起"的那部分。

任务 1.14 的交付面分两层：

1. **形态 1（整体搬家）**：`ui/agent/` 下 30 个零 UI 依赖的模块已由
   `scripts/relocate_module.py` 逐字搬进 `services/agent/`，旧路径留**真别名**
   shim（`ui.agent.models is services.agent.models`）。见该脚本 MOVES 的任务 1.14 段。
2. **形态 2（切缝）**：本模块 —— `AgentChatView` / `AgentMixin` 里那些**本质是逻辑、
   只是恰好写在界面类里**的代码。搬走的判据只有一条：**它不 import 任何 GUI、
   不直接创建/销毁控件**。界面效果一律通过调用方传进来的 ``sink`` 对象完成。

## 切缝在哪（三条线）

- **纯函数**（零参数依赖）：`system_prompt_text`、`build_callbacks`、
  `resolve_active_model`、`resolve_provider_id`、`build_provider`、`apply_file_edit`、
  `load_provider_configs`、`fetch_credits`。这些从 `AgentChatView` / `AgentMixin`
  里逐字搬出，参数就是原来读的那些 `self.xxx`。
- **AI 处理循环**：`run_loop` / `handle_stream_events` / `execute_tool_call`
  —— 原 `AgentChatView._process_ai_loop` / `_handle_stream_events` /
  `_execute_tool_call` 的**逐字搬迁**，只做一处机械替换：`self.` → `sink.`。
  即循环体里的每一次界面动作（`after` / `_append_*` / `_schedule_render` /
  `_token_label` …）都改成"调用注入进来的 sink 的同名成员"。
- **留在界面**：控件创建与布局、Markdown 渲染、三个确认弹窗
  （危险命令 / 文件编辑 / ask_user）、线程启动。服务**不弹窗、不自己起线程**：
  原来循环里的两次 `show_notification(...)` 改成调用 `sink.notify_ai_task_done()`
  / `sink.notify_ai_task_failed(err)`；跑循环的 `threading.Thread` 仍由视图起。

## 为什么 sink 就是 `AgentChatView` 本身

因为要**保住既有行为**：循环里几十处界面交互的时序（先 `_append_*` 再
`_schedule_render`、`after(0, ...)` 切主线程、`finally` 里的收尾顺序）都是现网行为，
一旦重新设计成一个"干净的"回调接口，就必须逐条重新论证等价性，风险远大于收益。
`self.` → `sink.` 是**纯文本替换**，可以用脚本机械复核（见
`poc/probe_agent_service_parity.py`），并且把 sink 换成假对象就能**完全离线**跑循环。

服务自身对 GUI 的依赖为 0：`scripts/check_services_purity.py` 会持续守住这一点。
"""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from logzero import logger

from services.agent import config as agent_config
from services.agent import skill as agent_skill
from services.agent import system_prompt as agent_system_prompt
from services.agent import tool_registry as agent_tool_registry
from services.agent.config import ProviderConfig, get_agent_config, init_agent_config
from services.agent.models import get_default_model, get_model_catalog, get_models_by_provider, get_provider_names
from services.agent.permission import check_permission
from services.agent.provider import BaseProvider
from services.agent.providers.anthropic import AnthropicProvider
from services.agent.providers.custom import CustomProvider
from services.agent.providers.jingdu import JingduProvider
from services.agent.providers.openai import OpenAIProvider
from services.agent.session import AgentSession
from services.agent.tools.files import FILE_EDIT_MARKER
from services.agent.tools.system import DANGEROUS_MARKER
from services.agent.tools.user import ASK_USER_MARKER
from services.base import Service


# ═══════════════════════════════════════════════════════════════════
# 从 agent_chat.py / agent_mixin.py 逐字搬出的纯逻辑
# ═══════════════════════════════════════════════════════════════════


def _trigger_agent_ach(achievement_id: str, value: int = 1):
    """原 `ui/agent/agent_chat.py:67`，逐字搬出。

    唯一改动：延迟导入的路径由根模块 `achievement_engine` 改指
    `services.achievement_engine`（根模块现在只是别名 shim，直接指服务层可少一跳）。
    """
    try:
        from services.achievement_engine import get_achievement_engine

        engine = get_achievement_engine()
        if engine:
            engine.update_progress(achievement_id, value=value)
    except Exception:
        pass


def system_prompt_text() -> str:
    """系统提示词 + 技能上下文。

    原实现把这一行在 `AgentChatView` 里写了 4 遍（`:709`、`:1461`、`:1466`、`:1490`），
    这里收敛成一处，拼接顺序与逐字结果不变。
    """
    return agent_system_prompt.get_system_prompt() + agent_skill.get_skills_context_text()


def build_callbacks(
    base: Optional[Dict[str, Callable]],
    get_session_id: Callable[[], str],
    get_config: Callable[[], dict],
) -> Dict[str, Callable]:
    """注入 `get_current_session_id` / `get_config` 两个回调（原 `agent_mixin.py` 的 2 处）。

    原写法：``cbs = dict(self.callbacks); cbs["get_current_session_id"] = ...;
    cbs["get_config"] = ...`` —— 逐字等价。
    """
    cbs = dict(base) if base else {}
    cbs["get_current_session_id"] = get_session_id
    cbs["get_config"] = get_config
    return cbs


def resolve_active_model(provider_name: str, model_name: str) -> Tuple[str, str]:
    """原 `AgentChatView._get_active_model`（`:1873-1886`）逐字搬出。

    `self._provider_var.get()` / `self._model_var.get()` 改为两个入参。
    """
    provider_map = {}
    for p in get_provider_names():
        provider_map[p["name"]] = p["id"]
    pid = provider_map.get(provider_name, "jingdu")
    if pid == "custom":
        return pid, model_name
    for m in get_model_catalog():
        if m.name == model_name and m.provider_id == pid:
            return pid, m.id
    return pid, "deepseek-v4-flash"


def resolve_provider_id(choice: str) -> str:
    """显示名 → provider id（原 `_on_provider_changed` 的前 4 行，逐字）。"""
    provider_map = {}
    for p in get_provider_names():
        provider_map[p["name"]] = p["id"]
    return provider_map.get(choice, "jingdu")


def build_provider(
    pid: str,
    pc: Optional[agent_config.ProviderConfig],
    thinking_enabled: bool = False,
    reasoning_effort: str = "",
) -> Optional[BaseProvider]:
    """按 provider id 造 provider 实例（原 `_on_provider_changed` 的四个分支，逐字）。

    原实现直接给 `self._provider` 赋值；这里返回实例，由调用方赋值 —— 这是本函数
    与原文唯一的结构差异。返回 None 表示"该 provider 没有 API Key"（与原文
    "什么都不做"等价：原实现此时也不会覆盖 `self._provider`）。

    记录现状、疑为缺陷：原文里 `pc.api_url` 在 `pc` 为 None 时会退化成 ""，
    但 `pc.custom_models if pc else None` 对 CustomProvider 传的是 None 而非 []，
    下面照抄不改。
    """
    if pid == "jingdu":
        if pc and pc.api_key:
            return JingduProvider(
                api_key=pc.api_key,
                api_url=pc.api_url if pc else "",
                thinking_enabled=thinking_enabled,
                reasoning_effort=reasoning_effort,
            )
    elif pid == "openai":
        if pc and pc.api_key:
            return OpenAIProvider(
                api_key=pc.api_key, api_url=pc.api_url if pc else "", reasoning_effort=reasoning_effort
            )
    elif pid == "anthropic":
        if pc and pc.api_key:
            return AnthropicProvider(
                api_key=pc.api_key, api_url=pc.api_url if pc else "", reasoning_effort=reasoning_effort
            )
    elif pid == "custom":
        if pc and pc.api_key:
            return CustomProvider(
                api_key=pc.api_key, api_url=pc.api_url if pc else "", custom_models=pc.custom_models if pc else None
            )
    return None


def apply_file_edit(confirm_data: dict, op_type: str) -> str:
    """原 `AgentChatView._apply_file_edit`（`:1835-1856`）逐字搬出，零参数改写。"""
    file_path = confirm_data.get("filePath", "")
    try:
        if op_type == "write" or op_type == "replace":
            # 确保父目录存在
            parent = os.path.dirname(file_path)
            if parent and not os.path.isdir(parent):
                os.makedirs(parent, exist_ok=True)
            content = confirm_data.get("newText", "")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            if op_type == "replace":
                replacements = confirm_data.get("replacements", 1)
                return f"已替换 {replacements} 处: {file_path}"
            return f"已写入文件: {file_path}"
        elif op_type == "delete":
            os.remove(file_path)
            return f"已删除文件: {file_path}"
        return f"未知操作类型: {op_type}"
    except Exception as e:
        return f"文件操作失败: {e}"


def load_provider_configs() -> Dict[str, Any]:
    """原 `AgentMixin._sync_agent_status` 里那段"尝试加载其他 Provider 配置"（`:99-105`）。

    记录现状、疑为缺陷：原文循环体里只有一句 `pass`（注释说
    "Provider 配置在 agent_chat 内部通过 _on_provider_changed 处理"），
    所以这个循环**什么都没做**，只是白读一遍配置。这里照抄行为（返回配置对象
    供调用方决定），不改变副作用。
    """
    try:
        config = agent_config.get_agent_config()
        for pid in ["openai", "anthropic", "custom"]:
            pc = config.providers.get(pid)
            if pc and pc.api_key:
                # Provider 配置在 agent_chat 内部通过 _on_provider_changed 处理
                pass
        return config
    except Exception as e:
        logger.error(f"[Agent] 加载 Provider 配置失败: {e}")
        return None


def fetch_credits(fetch_jdz_user_info: Callable[[], Any]) -> Optional[int]:
    """取 AI 积分余额（原 `AgentMixin._refresh_agent_credits` 的内部线程体，逐字）。

    原实现在这里 `threading.Thread(...).start()`；服务**不自己起线程**，
    因此只保留"取值"这一步，调度交给调用方（视图用现有线程，阶段 2 换 TaskRunner）。
    """
    info = fetch_jdz_user_info()
    if info:
        return info.get("ai_credits", 0)
    return None


# ═══════════════════════════════════════════════════════════════════
# AI 处理循环（原 AgentChatView 的三个方法，逐字搬迁，self. → sink.）
# ═══════════════════════════════════════════════════════════════════
#
# `sink` 需要提供（原 `self.` 的全部成员）：MAX_ITERATIONS、_ai_processing、
# _session、_registry、_provider、_callbacks、_thinking_var、_effort_var、
# _streaming_block、_message_blocks、_pending_dangerous、_pending_ask_user、
# _pending_file_edit、_token_label，以及方法 after / _get_active_model /
# _handle_stream_events / _handle_non_stream_response / _execute_tool_call /
# _schedule_render / _append_system_message / _append_divider / _append_tool_start /
# _append_tool_result / _refresh_todos / _refresh_session_list / _reset_send_button /
# _show_dangerous_dialog / _show_file_edit_dialog / _show_ask_user_dialog，
# 外加两个**新增的**通知钩子 notify_ai_task_done / notify_ai_task_failed（见模块 docstring）。


def run_loop(sink) -> None:
    """原 `AgentChatView._process_ai_loop`（`:1512-1628`）逐字搬迁。

    与原文的全部差异（机械可复核，见 `poc/probe_agent_service_parity.py`）：
    `self.` → `sink.`；两次 `show_notification(...)` → `sink.notify_ai_task_done()` /
    `sink.notify_ai_task_failed(str(e)[:50])`。
    """
    max_iterations = sink.MAX_ITERATIONS
    iteration = 0
    empty_count = 0
    logger.info("[Agent] === AI 处理循环开始（流式模式）===")
    sink._ai_processing = True

    try:
        while iteration < max_iterations:
            iteration += 1
            if iteration > 1:
                _trigger_agent_ach("agent_multi_turn")
            logger.info(f"[Agent] --- 迭代 {iteration}/{max_iterations} ---")

            # 自动压缩
            if iteration % 10 == 0 and sink._session.estimate_tokens() > 60000:
                sink._session.compact()

            # 流式调用
            tools = sink._registry.get_definitions()
            provider_id, model_id = sink._get_active_model()
            model_name = model_id or "deepseek-v4-flash"

            provider = sink._provider
            if provider is None:
                sink.after(0, lambda: sink._append_system_message("未配置 AI 提供商"))
                break

            try:
                # 同步思考模式设置
                if hasattr(provider, "thinking_enabled"):
                    provider.thinking_enabled = sink._thinking_var.get()
                if hasattr(provider, "reasoning_effort"):
                    provider.reasoning_effort = sink._effort_var.get() if sink._thinking_var.get() else ""
                stream_gen = provider.stream_chat(messages=sink._session.messages, tools=tools, model=model_name)
            except Exception as e:
                logger.error(f"[Agent] 启动流式调用失败: {e}")
                # 回退到非流式
                try:
                    response = provider.chat(messages=sink._session.messages, tools=tools, model=model_name)
                    sink._handle_non_stream_response(response)
                except Exception as e2:
                    sink.after(0, lambda e=str(e2): sink._append_system_message(f"AI 调用失败: {e}"))
                    break
                continue

            # 处理流式事件 - 先创建流式块
            sink._streaming_block = {"role": "assistant", "content": "", "thinking": "", "tag": ""}
            content, tool_calls, needs_break = sink._handle_stream_events(stream_gen)
            # 完成流式渲染：将流式块固定为永久消息块
            streaming_thinking = ""
            if sink._streaming_block:
                streaming_thinking = sink._streaming_block.get("thinking", "")
                if sink._streaming_block.get("content") or streaming_thinking:
                    sink._message_blocks.append(sink._streaming_block)
            sink._streaming_block = None
            # 流式结束，强制立即渲染最终内容
            sink._schedule_render(force=True)

            if needs_break:
                return  # 等待用户确认

            # 追加 assistant 消息（保留思考过程供历史回放）
            if content or tool_calls:
                msg = {"role": "assistant", "content": content}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                if streaming_thinking:
                    msg["_thinking"] = streaming_thinking
                sink._session.add_message(msg)

            # 执行工具调用
            if tool_calls:
                logger.info(f"[Agent] 模型请求调用 {len(tool_calls)} 个工具")
                all_ok = True
                for tc in tool_calls:
                    result = sink._execute_tool_call(tc)
                    if result is False:
                        all_ok = False
                        break
                    elif isinstance(result, str) and result == "WAIT_USER":
                        sink._session.save()
                        return

                # 每次工具调用后立即刷新 Todo 面板
                sink.after(0, sink._refresh_todos)

                if not all_ok:
                    continue

                # 不在循环中保存，最终由 finally 统一保存
                continue
            else:
                logger.info("[Agent] 任务完成")
                sink.after(0, sink._append_divider)
                sink.notify_ai_task_done()
                _trigger_agent_ach("agent_nlp_master")
                break

    except Exception as e:
        logger.error(f"[Agent] 处理循环异常: {e}", exc_info=True)
        sink.notify_ai_task_failed(str(e)[:50])
    finally:
        wait_user = bool(sink._pending_dangerous or sink._pending_ask_user or sink._pending_file_edit)
        logger.info(f"[Agent] === AI 处理循环结束 === (wait_user={wait_user})")
        if wait_user:
            # 等待用户确认时，保存会话但不重置 UI（确认回调会启动新循环）
            sess = sink._session
            sink.save_session_async(sess)
        else:
            sink._ai_processing = False
            sink.after(0, sink._reset_send_button)
            sess = sink._session
            sink.save_session_async(sess)
            sink.after(0, sink._refresh_todos)
            sink.after(0, sink._refresh_session_list)
            sink.after(0, lambda: sink._token_label.configure(text=f"Token ~{sink._session.estimate_tokens():,}"))


def handle_stream_events(stream_gen, sink) -> tuple:
    """处理流式事件 - 更新 _streaming_block + 调度渲染

    原 `AgentChatView._handle_stream_events`（`:1630-1699`）逐字搬迁，`self.` → `sink.`。

    Returns:
        (content_text, tool_calls_list, needs_break)
    """
    accumulated_text = ""
    tool_calls = []
    current_tool_call = None

    try:
        for event in stream_gen:
            if not isinstance(event, dict):
                continue

            event_type = event.get("type", "")

            if event_type == "text_delta":
                text = event.get("text", "")
                accumulated_text += text
                if sink._streaming_block:
                    sink._streaming_block["content"] = accumulated_text
                    sink._schedule_render()

            elif event_type == "thinking_delta":
                thinking_text = event.get("text", "")
                if sink._streaming_block:
                    sink._streaming_block["thinking"] = sink._streaming_block.get("thinking", "") + thinking_text
                    sink._schedule_render()

            elif event_type == "tool_call_start":
                tc_id = event.get("tool_call_id", "")
                current_tool_call = {"id": tc_id, "type": "function", "function": {"name": "", "arguments": ""}}
                tool_calls.append(current_tool_call)

            elif event_type == "tool_call_name":
                name = event.get("tool_name", "")
                if current_tool_call:
                    current_tool_call["function"]["name"] = name
                sink._append_tool_start(name)

            elif event_type == "tool_call_args":
                if current_tool_call:
                    current_tool_call["function"]["arguments"] += event.get("tool_args", "")

            elif event_type == "tool_call_complete":
                tc = event.get("tool_call", {})
                for i, t in enumerate(tool_calls):
                    if t["id"] == tc.get("id"):
                        tool_calls[i] = tc
                        break

            elif event_type == "usage":
                usage = event.get("usage", {})
                total = usage.get("total_tokens", 0)
                sink.after(0, lambda t=total: sink._token_label.configure(text=f"Token: {t}"))

            elif event_type == "done":
                sink._append_divider()

            elif event_type == "error":
                err_msg = event.get("message", "未知错误")
                sink._append_system_message(f"❌ {err_msg}")
                return accumulated_text, tool_calls, True

    except Exception as e:
        logger.error(f"[Agent] 流式处理异常: {e}")
        sink._append_system_message(f"流式错误: {e}")

    return accumulated_text, tool_calls, False


def execute_tool_call(tc: Dict, sink):
    """执行单个工具调用

    原 `AgentChatView._execute_tool_call`（`:1710-1783`）逐字搬迁，`self.` → `sink.`。
    `check_permission` 现在直接来自 `services.agent.permission`（同名同签名）。
    """
    func = tc.get("function", {})
    tool_name = func.get("name", "")
    tool_call_id = tc.get("id", "")

    try:
        tool_params = json.loads(func.get("arguments", "{}"))
    except (json.JSONDecodeError, TypeError):
        tool_params = {}

    logger.info(f"[Agent] 工具调用: {tool_name}")

    # 权限检查
    effect = check_permission(tool_name)
    if effect == "deny":
        sink._append_system_message(f"🔒 工具 {tool_name} 被策略禁止")
        sink._session.add_message(
            {"role": "tool", "tool_call_id": tool_call_id, "content": f"工具 {tool_name} 被权限策略禁止"}
        )
        return True
    if effect == "ask" and tool_name == "exec_command":
        result_text = sink._registry.execute(tool_name, tool_params, sink._callbacks)
        if result_text.startswith(DANGEROUS_MARKER):
            try:
                rest = result_text[len(DANGEROUS_MARKER) + 1 :]
                payload = json.loads(rest)
                path = payload["path"]
                command = payload["command"]
                sink._pending_dangerous = (path, command, tool_call_id)
                sink.after(0, lambda p=path, c=command: sink._show_dangerous_dialog(p, c))
                return "WAIT_USER"
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"[Agent] DANGEROUS_MARKER 解析失败: {e}")
        sink._session.add_message({"role": "tool", "tool_call_id": tool_call_id, "content": result_text})
        sink._append_tool_result(tool_name, result_text[:300])
        return True

    if effect == "ask" and tool_name in ("write_file", "replace_in_file", "delete_file"):
        result_text = sink._registry.execute(tool_name, tool_params, sink._callbacks)
        if result_text.startswith(FILE_EDIT_MARKER):
            try:
                rest = result_text[len(FILE_EDIT_MARKER) + 1 :]
                payload = json.loads(rest)
                confirm_data = payload["data"]
                op_type = payload["op"]
                summary = payload.get("summary", "")
                sink._pending_file_edit = (confirm_data, op_type, tool_call_id)
                sink.after(0, lambda c=confirm_data, o=op_type, s=summary: sink._show_file_edit_dialog(c, o, s))
                return "WAIT_USER"
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"[Agent] FILE_EDIT_MARKER 解析失败: {e}, result头部: {result_text[:200]}")
        sink._session.add_message({"role": "tool", "tool_call_id": tool_call_id, "content": result_text})
        sink._append_tool_result(tool_name, result_text[:300])
        return True

    # 执行工具
    result_text = sink._registry.execute(tool_name, tool_params, sink._callbacks)

    # 检测 ask_user
    if result_text.startswith(ASK_USER_MARKER):
        try:
            rest = result_text[len(ASK_USER_MARKER) + 1 :]
            questions = json.loads(rest)
            if isinstance(questions, list):
                sink._pending_ask_user = (questions, tool_call_id)
                sink.after(0, lambda q=questions: sink._show_ask_user_dialog(q))
                return "WAIT_USER"
        except json.JSONDecodeError:
            pass

    sink._session.add_message({"role": "tool", "tool_call_id": tool_call_id, "content": result_text})
    sink._append_tool_result(tool_name, result_text[:300])
    return True


# ═══════════════════════════════════════════════════════════════════
# 服务对象
# ═══════════════════════════════════════════════════════════════════


class AgentService(Service):
    """AGENT 业务服务：配置 / 权限 / 技能 / 工具 / 循环 的统一入口。

    约定（与 `services/monitor_service.py`、`services/server_service.py` 一致）：

    - 构造期只做赋值，不读盘、不连网、不建线程；可脱离 `AppContext` 单独实例化
      （`AgentService()` 在 pytest 里就能用）。
    - 需要与用户交互时，由调用方把界面适配对象（sink）传进来，服务只调用它；
      服务自身不 import 任何 GUI。
    - 需要后台线程时，由调用方调度（`run_loop` 必须在 worker 线程里跑，
      视图负责起线程）。

    记录现状、疑为缺陷：`ui/agent/` 原本用**模块级全局单例**保存配置与权限
    （`_agent_config` / `_permission_manager`），本服务保留这层全局语义
    （方法直接转调 `services.agent.config` / `permission` 的模块级函数），
    没有改成实例级状态 —— 改成实例级会让"设置页改了配置、AGENT 页读不到"。
    """

    name = "agent"
    label = "AI 助手"

    # ─── 系统提示词 / 技能 ──────────────────────────────────

    def system_prompt_text(self) -> str:
        """系统提示词 + 技能上下文（视图里 4 处拼接的收敛点）。"""
        return system_prompt_text()

    def skills_context_text(self) -> str:
        return agent_skill.get_skills_context_text()

    def load_skills(self) -> list:
        return agent_skill.load_all_skills()

    def get_skill(self, name: str):
        return agent_skill.get_skill_by_name(name)

    def skills_dir(self) -> str:
        return agent_skill._get_skills_dir()

    # ─── 配置 ────────────────────────────────────────────────

    def init_config(self, config_path: str = "") -> None:
        """初始化 Agent 配置（原 `init_agent_config`）。"""
        init_agent_config(config_path)

    def get_config(self) -> "agent_config.AgentConfig":
        """取全局 Agent 配置（原 `get_agent_config`）。"""
        return get_agent_config()

    def save_config(self, config_path: str = "") -> None:
        """保存 Agent 配置（原 `save_agent_config`：`get_agent_config().save_to_file()`）。"""
        get_agent_config().save_to_file(config_path)

    def bing_api_key(self) -> str:
        """供 web_search 等工具使用（原 `AgentMixin._get_agent_cfg`）。

        原实现把整个取值包在 try/except 里、失败返回 `{}`；这里保持"失败返回空串"
        的等价语义，调用方（视图）仍按老样子组装成 `{"bing_api_key": ...}`。
        """
        try:
            return get_agent_config().bing_api_key
        except Exception:
            return ""

    def provider_config(self, pid: str) -> Optional[ProviderConfig]:
        return self.get_config().providers.get(pid)

    def load_provider_configs(self):
        return load_provider_configs()

    # ─── 权限（三级判定）────────────────────────────────────

    def check_permission(self, action: str, resource: str = "*"):
        """三级权限判定：allow / deny / ask（规则按顺序匹配，命中即停）。"""
        return check_permission(action, resource)

    def permission_rules(self) -> list:
        from services.agent.permission import get_permission_manager

        return get_permission_manager().get_rules()

    # ─── 模型 / Provider ────────────────────────────────────

    def resolve_active_model(self, provider_name: str, model_name: str) -> Tuple[str, str]:
        return resolve_active_model(provider_name, model_name)

    def resolve_provider_id(self, choice: str) -> str:
        return resolve_provider_id(choice)

    def build_provider(
        self,
        pid: str,
        pc: Optional[ProviderConfig],
        thinking_enabled: bool = False,
        reasoning_effort: str = "",
    ) -> Optional[BaseProvider]:
        return build_provider(pid, pc, thinking_enabled, reasoning_effort)

    def jingdu_provider(self, api_key: str) -> JingduProvider:
        """净读 AI provider（原 `AgentMixin._sync_agent_status` 的构造方式，逐字）。

        记录现状、疑为缺陷：这里**刻意**不复用 `build_provider` —— 原文建
        `JingduProvider` 时既没传 `api_url` 也没传 `thinking_enabled` /
        `reasoning_effort`，与"模型下拉框切换 provider"那条路径的行为并不一致。
        本次只搬家不改逻辑，因此两条路径都照抄原样，保留这个不一致。
        """
        return JingduProvider(api_key=api_key)

    def models_of(self, provider_id: str) -> list:
        return get_models_by_provider(provider_id)

    def default_model(self, provider_id: str):
        return get_default_model(provider_id)

    # ─── 工具注册与执行 ─────────────────────────────────────

    def registry(self):
        """全局工具注册表（单例）。"""
        return agent_tool_registry.get_registry()

    def tool_definitions(self) -> List[dict]:
        return agent_tool_registry.get_tool_definitions()

    def execute_tool(self, name: str, params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
        return agent_tool_registry.get_registry().execute(name, params, callbacks)

    # ─── 会话 ────────────────────────────────────────────────

    def create_session(self, provider_id: str = "jingdu", model_id: str = "deepseek-v4-flash") -> AgentSession:
        """新建会话：系统提示词固定走 `system_prompt_text()`（与原文一致）。"""
        return AgentSession.create_new(
            provider_id=provider_id, model_id=model_id, system_prompt=self.system_prompt_text()
        )

    def list_sessions(self) -> list:
        return AgentSession.list_all()

    def load_session(self, session_id: str):
        return AgentSession.load(session_id)

    def delete_session(self, session_id: str) -> None:
        AgentSession.delete(session_id)

    # ─── 循环 / 流式 / 工具调用 ─────────────────────────────

    def run_loop(self, sink) -> None:
        """跑一次 AI 处理循环（必须在 worker 线程里调用；视图负责起线程）。"""
        run_loop(sink)

    def handle_stream_events(self, stream_gen, sink) -> tuple:
        return handle_stream_events(stream_gen, sink)

    def execute_tool_call(self, tc: Dict, sink):
        return execute_tool_call(tc, sink)

    def apply_file_edit(self, confirm_data: dict, op_type: str) -> str:
        return apply_file_edit(confirm_data, op_type)

    def fetch_credits(self, fetch_jdz_user_info: Callable[[], Any]) -> Optional[int]:
        return fetch_credits(fetch_jdz_user_info)

    def build_callbacks(
        self,
        base: Optional[Dict[str, Callable]],
        get_session_id: Callable[[], str],
        get_config: Callable[[], dict],
    ) -> Dict[str, Callable]:
        return build_callbacks(base, get_session_id, get_config)
