"""阶段 1 任务 1.14「Agent 服务」抽取的永久回归守卫。

`ui/agent/` 下本来是一个自洽的小世界（30 个模块、约 8.1k 行纯逻辑）。本轮把它
**整体搬进 `services/agent/`**（形态 1，`scripts/relocate_module.py` 登记并逐文件
校验），并把"逻辑与界面混在一起"的那部分切进 `services/agent_service.py`
（形态 2）：AI 处理循环、流式事件消费、工具调用与权限判定、系统提示词拼接、
配置读写、技能加载、provider 构造。

这里守住七件事：

1. **旧路径是真别名**：`ui.agent.X is services.agent.X`（12 个模块 + 17 个子模块）；
2. **循环体逐字等价**：新服务的 `run_loop` 与 `git show HEAD` 里原方法的函数体
   在 `self.` → `sink.` 之后**逐节点相同**；
3. **sink 契约完整**：服务里出现的每一个 `sink.<名字>`，界面类上都必须真的有
   —— 这条守卫能抓住"搬代码时漏了某个 `_append_*`/`_handle_*`"；
4. 权限三级判定（allow / deny / ask）与通配符规则；
5. 配置读写（含 `os.getcwd()` 相对路径的真实落盘往返）；
6. 技能加载（SKILL.md 扫描、描述提取、上下文文本）；
7. 工具注册与调用 + **AI 处理循环的六条路径**，全部**离线**：
   模型调用走注入的假 provider（吐出脚本化 SSE 事件），工具走假注册表，
   文件只碰 `tmp_path`，绝不真连网、绝不真弹窗、绝不真起线程。

搬运中查明的既有缺陷行为被显式钉住，并逐条标注「记录现状、疑为缺陷」。
"""

from __future__ import annotations

import ast
import importlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import _baseline

REPO_ROOT = Path(__file__).resolve().parent.parent

import services.agent_service as SVC  # noqa: E402
from services.agent import config as agent_config  # noqa: E402
from services.agent import permission as agent_permission  # noqa: E402
from services.agent import skill as agent_skill  # noqa: E402
from services.agent.session import AgentSession  # noqa: E402
from services.agent.tool_registry import ToolRegistry, get_registry  # noqa: E402
from services.agent.tools.base import ToolInfo  # noqa: E402
from services.errors import ServiceNotAvailable  # noqa: E402


#: 搬家完成之后**已登记**的行数变化：``{新文件相对路径: (行数增量, 理由)}``。
#:
#: 与 `tests/test_services_relocation.py::REGISTERED_LINE_DELTAS` 同一套路：
#: 判据没有放松 —— 不在表里的文件仍然必须与 git 原文行数**完全一致**；
#: 每条例外都要写清归属与原因，而且**必须真的命中**（见下面测试末尾的过期检查）。
REGISTERED_LINE_DELTAS: dict[str, tuple[int, str]] = {
    "services/agent/tools/system.py": (
        1,
        "第 12 轮：读子进程文本输出的调用补 `errors=\"replace\"`。`subprocess.run(..., text=True)` "
        "不指定 encoding 时用 `locale.getpreferredencoding(False)`，而项目自己的文档命令是 "
        "`python -X utf8 -m pytest` —— UTF-8 模式下子进程按 OEM 码页输出的中文会在 "
        "subprocess 的 reader 线程里抛 UnicodeDecodeError（实测 byte 0xd0）。补这个 kwarg "
        "让那处调用多占一行。",
    ),
    "services/agent/engine.py": (
        1,
        "同上（agent 执行 shell 命令的那条路径）。",
    ),
}


# ═══════════════════════════════════════════════════════════════════
# 假外部依赖（全部离线：无网络、无 Tk、无线程）
# ═══════════════════════════════════════════════════════════════════


class FakeVar:
    """替 `ctk.BooleanVar` / `ctk.StringVar`（只用到 .get()）。"""

    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class FakeLabel:
    """替 `ctk.CTkLabel`（只用到 .configure(text=...)）。"""

    def __init__(self):
        self.text = None

    def configure(self, **kwargs):
        if "text" in kwargs:
            self.text = kwargs["text"]


class FakeProvider:
    """脚本化假 provider：按 `turns` 依次吐出事件流；可指定抛异常。"""

    def __init__(self, turns, stream_raises=False, chat_response=None, chat_raises=False,
                 stream_fail_times=0):
        self.turns = list(turns)
        self.stream_raises = stream_raises
        self.stream_fail_times = stream_fail_times
        self.chat_response = chat_response or {"role": "assistant", "content": "非流式回答"}
        self.chat_raises = chat_raises
        self.stream_calls = 0
        self.chat_calls = 0
        self.last_stream_kwargs = None
        self.thinking_enabled = None
        self.reasoning_effort = None

    def stream_chat(self, messages=None, tools=None, model=""):
        self.stream_calls += 1
        self.last_stream_kwargs = {"messages": messages, "tools": tools, "model": model}
        if self.stream_raises or self.stream_fail_times > 0:
            if self.stream_fail_times > 0:
                self.stream_fail_times -= 1
            raise RuntimeError("stream 不可用（测试用）")
        events = self.turns.pop(0) if self.turns else [{"type": "done"}]
        return iter(events)

    def chat(self, messages=None, tools=None, model=""):
        self.chat_calls += 1
        if self.chat_raises:
            raise RuntimeError("非流式也失败（测试用）")
        return self.chat_response


class FakeRegistry:
    """假工具注册表：`results` 是工具名 → 返回文本的映射。"""

    def __init__(self, results=None, definitions=None):
        self.results = dict(results or {})
        self.definitions = definitions if definitions is not None else [{"type": "function", "function": {"name": "fake"}}]
        self.calls = []

    def get_definitions(self):
        return self.definitions

    def execute(self, name, params, callbacks):
        self.calls.append({"name": name, "params": params})
        return self.results.get(name, f"工具 {name} 的默认结果")


class FakeSink:
    """替 `AgentChatView`：实现服务循环用到的全部 sink 成员。

    刻意与真实视图保持**同构**：`_handle_stream_events` / `_execute_tool_call`
    同样转发给服务（和 `agent_chat.py` 里的薄委托一模一样），这样测的是真实接线。
    `after` 直接内联执行（Tk 里是排到主线程），保证断言顺序确定。
    """

    MAX_ITERATIONS = 3

    def __init__(self, registry=None, session=None, provider=None, callbacks=None, max_iterations=3):
        self.MAX_ITERATIONS = max_iterations
        self._ai_processing = False
        self._session = session if session is not None else AgentSession.create_new(system_prompt="sys")
        self._registry = registry if registry is not None else FakeRegistry()
        self._provider = provider
        self._callbacks = callbacks or {}
        self._thinking_var = FakeVar(True)
        self._effort_var = FakeVar("high")
        self._streaming_block = None
        self._message_blocks = []
        self._pending_dangerous = None
        self._pending_ask_user = None
        self._pending_file_edit = None
        self._token_label = FakeLabel()
        # ── 观测记录 ──
        self.after_calls = []
        self.system_messages = []
        self.dividers = 0
        self.tool_starts = []
        self.tool_results = []
        self.render_calls = []
        self.done_notifies = 0
        self.failed_notifies = []
        self.saved_sessions = []
        self.reset_button_calls = 0
        self.todo_refreshes = 0
        self.session_list_refreshes = 0
        self.dialog_calls = []

    # ── 视图既有成员（名字与 agent_chat.py 一致）──

    def after(self, delay, fn=None, *args):
        self.after_calls.append(delay)
        if fn is not None:
            fn(*args)

    def _get_active_model(self):
        return ("jingdu", "deepseek-v4-flash")

    def _handle_stream_events(self, stream_gen):
        return SVC.handle_stream_events(stream_gen, self)

    def _handle_non_stream_response(self, response):
        content = response.get("content", "")
        if content:
            self._message_blocks.append({"role": "assistant", "content": content})
        if response.get("tool_calls"):
            self._session.add_message(response)

    def _execute_tool_call(self, tc):
        return SVC.execute_tool_call(tc, self)

    def _schedule_render(self, force=False):
        self.render_calls.append(force)

    def _append_system_message(self, text):
        self.system_messages.append(text)

    def _append_divider(self):
        self.dividers += 1

    def _append_tool_start(self, name):
        self.tool_starts.append(name)

    def _append_tool_result(self, name, summary):
        self.tool_results.append((name, summary))

    def _refresh_todos(self):
        self.todo_refreshes += 1

    def _refresh_session_list(self):
        self.session_list_refreshes += 1

    def _reset_send_button(self):
        self.reset_button_calls += 1

    def _show_dangerous_dialog(self, path, command):
        self.dialog_calls.append(("dangerous", path, command))

    def _show_file_edit_dialog(self, confirm_data, op_type, summary):
        self.dialog_calls.append(("file_edit", op_type, summary))

    def _show_ask_user_dialog(self, questions):
        self.dialog_calls.append(("ask_user", questions))

    # ── 任务 1.14 新增的三个 sink 钩子（真实视图里同样存在）──

    def notify_ai_task_done(self):
        self.done_notifies += 1

    def notify_ai_task_failed(self, err):
        self.failed_notifies.append(err)

    def save_session_async(self, session):
        """真实视图在这里起线程；测试里同步落盘（仍然落在 tmp_path，不碰用户目录）。"""
        self.saved_sessions.append(session)
        session.save()


def _tool_call_event(call_id, name, arguments="{}"):
    return [
        {"type": "tool_call_start", "tool_call_id": call_id},
        {"type": "tool_call_name", "tool_name": name},
        {"type": "tool_call_args", "tool_args": arguments},
    ]


def _assistant_tool_calls(events):
    """把 start/name/args 三个事件拼成 loop 期望的 tool_calls 结构。"""
    calls = {}
    order = []
    for ev in events:
        if ev["type"] == "tool_call_start":
            calls[ev["tool_call_id"]] = {
                "id": ev["tool_call_id"],
                "type": "function",
                "function": {"name": "", "arguments": ""},
            }
            order.append(ev["tool_call_id"])
        elif ev["type"] == "tool_call_name":
            calls[order[-1]]["function"]["name"] = ev["tool_name"]
        elif ev["type"] == "tool_call_args":
            calls[order[-1]]["function"]["arguments"] += ev["tool_args"]
    return [calls[i] for i in order]


# ═══════════════════════════════════════════════════════════════════
# 一、形态 1 的机械守卫：真别名 / shim 声明 / 零 UI 依赖 / 分层例外清零
# ═══════════════════════════════════════════════════════════════════


MOVED_MODULES = [
    ("ui.agent.models", "services.agent.models", "ui/agent/models.py", "services/agent/models.py"),
    ("ui.agent.stream", "services.agent.stream", "ui/agent/stream.py", "services/agent/stream.py"),
    ("ui.agent.provider", "services.agent.provider", "ui/agent/provider.py", "services/agent/provider.py"),
    ("ui.agent.session", "services.agent.session", "ui/agent/session.py", "services/agent/session.py"),
    ("ui.agent.system_prompt", "services.agent.system_prompt",
     "ui/agent/system_prompt.py", "services/agent/system_prompt.py"),
    ("ui.agent.permission", "services.agent.permission",
     "ui/agent/permission.py", "services/agent/permission.py"),
    ("ui.agent.config", "services.agent.config", "ui/agent/config.py", "services/agent/config.py"),
    ("ui.agent.skill", "services.agent.skill", "ui/agent/skill.py", "services/agent/skill.py"),
    ("ui.agent.engine", "services.agent.engine", "ui/agent/engine.py", "services/agent/engine.py"),
    ("ui.agent.tool_registry", "services.agent.tool_registry",
     "ui/agent/tool_registry.py", "services/agent/tool_registry.py"),
    # 整包搬家：包里只剩 __init__.py 做别名登记，子模块 shim 文件由工具删除
    ("ui.agent.providers", "services.agent.providers",
     "ui/agent/providers/__init__.py", "services/agent/providers/__init__.py"),
    ("ui.agent.tools", "services.agent.tools", "ui/agent/tools/__init__.py", "services/agent/tools/__init__.py"),
]

#: (旧模块名, 新模块名, 新文件相对于 git 原文的路径)
PACKAGE_SUBMODULES = {
    "ui.agent.providers": (
        ("anthropic", "ui/agent/providers/anthropic.py"),
        ("custom", "ui/agent/providers/custom.py"),
        ("jingdu", "ui/agent/providers/jingdu.py"),
        ("openai", "ui/agent/providers/openai.py"),
    ),
    "ui.agent.tools": (
        ("base", "ui/agent/tools/base.py"),
        ("files", "ui/agent/tools/files.py"),
        ("modpack", "ui/agent/tools/modpack.py"),
        ("mods", "ui/agent/tools/mods.py"),
        ("resources", "ui/agent/tools/resources.py"),
        ("server", "ui/agent/tools/server.py"),
        ("skill", "ui/agent/tools/skill.py"),
        ("system", "ui/agent/tools/system.py"),
        ("todo_write", "ui/agent/tools/todo_write.py"),
        ("user", "ui/agent/tools/user.py"),
        ("versions", "ui/agent/tools/versions.py"),
        ("web_fetch", "ui/agent/tools/web_fetch.py"),
        ("web_search", "ui/agent/tools/web_search.py"),
    ),
}


@pytest.mark.parametrize("old_mod,new_mod,old_path,new_path", MOVED_MODULES)
def test_old_path_is_the_same_module_object(old_mod, new_mod, old_path, new_path):
    """核心不变式：`import 旧路径` 与 `import 新路径` 必须是**同一个模块对象**。

    这比"同一批对象"更强：它同时覆盖读、写、打补丁与 `is` 判定。
    """
    assert importlib.import_module(old_mod) is importlib.import_module(new_mod)
    assert Path(importlib.import_module(new_mod).__file__).name == Path(new_path).name


@pytest.mark.parametrize("old_mod,new_mod,old_path,new_path", MOVED_MODULES)
def test_shim_files_declare_the_alias(old_mod, new_mod, old_path, new_path):
    """旧路径的 shim 必须显式写 `sys.modules[__name__] = _impl`（真别名，不是复制）。"""
    text = io.open(REPO_ROOT / old_path, encoding="utf-8").read()
    assert "sys.modules[__name__] = _impl" in text, f"{old_path} 没有声明别名"


@pytest.mark.parametrize("pkg,subs", sorted(PACKAGE_SUBMODULES.items()))
def test_package_submodules_are_aliased(pkg, subs):
    """整包搬家时子模块也要别名，否则会以旧包名把实现再加载一次（两份类对象）。"""
    new_pkg = pkg.replace("ui.agent", "services.agent")
    for sub, _old_path in subs:
        old = importlib.import_module(f"{pkg}.{sub}")
        new = importlib.import_module(f"{new_pkg}.{sub}")
        assert old is new, f"{pkg}.{sub} 与 {new_pkg}.{sub} 不是同一个模块对象"


@pytest.mark.parametrize("pkg,subs", sorted(PACKAGE_SUBMODULES.items()))
def test_moved_submodules_kept_the_original_line_count(pkg, subs):
    """子模块内容整体搬走、没有删改：行数与 git 原文一致（**已登记的偏差**除外）。

    搬家**完成之后**带任务号的缺陷修复会合法地改变行数，这类偏差逐条登记在
    ``REGISTERED_LINE_DELTAS`` 里（带任务号与理由）。判据没有放松：
    不在登记表里的文件仍然必须与原文**完全一致**。
    """
    new_pkg = pkg.replace("ui.agent", "services.agent")
    for sub, old_rel in subs:
        original = _baseline.git_show(old_rel)
        assert original.strip(), f"基线提交里找不到 {old_rel} 的原文"
        new_rel = old_rel.replace("ui/agent/", "services/agent/")
        moved = (REPO_ROOT / new_rel).read_text(encoding="utf-8")
        delta, why = REGISTERED_LINE_DELTAS.get(new_rel, (0, ""))
        assert len(moved.splitlines()) == len(original.splitlines()) + delta, (
            f"{sub} 行数变了：原文 {len(original.splitlines())} / 现在 {len(moved.splitlines())}"
            + (f"（已登记偏差 +{delta}：{why}）" if delta else "（内容被删改了？）")
        )


def test_registered_line_deltas_are_still_needed():
    """反向守卫：登记项必须**仍然对得上现实**，否则说明登记该删了。

    做法是"从 git 原文与当前文件**现算**一遍增量"（不依赖别的测试是否跑过），
    与 `tests/test_services_relocation.py` 的同名守卫同一套路。
    """
    for new_rel, (delta, why) in sorted(REGISTERED_LINE_DELTAS.items()):
        assert why.strip(), f"{new_rel}: 登记项没有理由"
        old_rel = new_rel.replace("services/agent/", "ui/agent/")
        original = _baseline.git_show(old_rel)
        moved = (REPO_ROOT / new_rel).read_text(encoding="utf-8")
        real = len(moved.splitlines()) - len(original.splitlines())
        assert real == delta, (
            f"{new_rel}: 登记的增量 {delta} != 现实 {real}"
            "（登记过期了？还是又改了代码却没更新登记表？）"
        )


def test_patching_through_old_path_reaches_implementation():
    """通过旧路径打补丁必须落到实现模块上（shim 退化成复制时这条会失败）。"""
    old = importlib.import_module("ui.agent.permission")
    new = importlib.import_module("services.agent.permission")
    original = new.DEFAULT_RULES
    sentinel = ["__probe__"]
    try:
        old.DEFAULT_RULES = sentinel
        assert new.DEFAULT_RULES is sentinel
    finally:
        new.DEFAULT_RULES = original


def _purity_module():
    spec = importlib.util.spec_from_file_location(
        "_fmcl_purity_agent_test", REPO_ROOT / "scripts" / "check_services_purity.py"
    )
    purity = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = purity
    spec.loader.exec_module(purity)
    return purity


def test_every_moved_agent_file_is_ui_free():
    """搬进 services/agent/ 的每个文件都必须零 UI 依赖（用分层校验器同一套规则）。"""
    purity = _purity_module()
    rule = next(r for r in purity.RULES if r.group == "services")
    bad = []
    roots = [p for p in (REPO_ROOT / "services" / "agent").rglob("*.py")]
    for path in sorted(roots):
        report = purity.check_file(path, rule)
        if not report.ok:
            bad.append("\n".join(v.render() for v in report.violations))
    assert roots, "services/agent/ 下没找到任何 .py"
    assert bad == [], "\n".join(bad)


def test_agent_service_module_is_ui_free():
    purity = _purity_module()
    rule = next(r for r in purity.RULES if r.group == "services")
    report = purity.check_file(REPO_ROOT / "services" / "agent_service.py", rule)
    assert report.ok, "\n".join(v.render() for v in report.violations)


def test_known_violations_is_now_empty_and_modrinth_talks_to_services():
    """任务 1.14 的硬指标：`modrinth.py` 那条已登记例外必须被删掉。

    先确认这个文件里**不再有任何** `ui.agent` 引用，再确认登记表真的空了
    （而不是把路径改一改继续留着）。
    """
    modrinth = (REPO_ROOT / "modrinth.py").read_text(encoding="utf-8")
    assert "ui.agent" not in modrinth
    assert "from services.agent.providers.jingdu import JingduProvider" in modrinth

    purity = _purity_module()
    assert purity.KNOWN_VIOLATIONS == ()
    assert purity.main([]) == 0, "分层检查必须整仓通过（0 未登记 / 0 已登记例外）"


# ═══════════════════════════════════════════════════════════════════
# 二、「只搬家不改逻辑」的机械证明 + sink 契约
# ═══════════════════════════════════════════════════════════════════


def _git_text(path: str) -> str:
    """取**重构前基线提交**里的原文（不是 ``HEAD`` —— 理由见 `tests/_baseline.py`）。"""
    return _baseline.git_show(path)


def _func_node(src: str, class_name, func_name):
    tree = ast.parse(src)
    scope = tree.body
    if class_name:
        scope = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name).body
    for node in scope:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            return node
    raise LookupError(f"{class_name}.{func_name} 未找到")


def _body_src(node) -> str:
    """函数体的归一化源码（去掉签名与 docstring），便于逐节点比较。"""
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return ast.unparse(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])))


#: 允许的差异：(原文片段, 新文片段)。逐条写死，不做模糊匹配。
#: 三条的理由：服务不弹窗（通知改钩子）、服务不自己起线程（落盘改钩子）。
_ALLOWED_DIFFS = [
    ("show_notification('🤖', _('notify_ai_task_done'), '', notify_type='success')",
     "sink.notify_ai_task_done()"),
    ("show_notification('🤖', _('notify_ai_task_failed'), str(e)[:50], notify_type='error')",
     "sink.notify_ai_task_failed(str(e)[:50])"),
    ("threading.Thread(target=lambda s=sess: s.save(), daemon=True).start()",
     "sink.save_session_async(sess)"),
]


@pytest.mark.parametrize("old_name,new_name", [
    ("_process_ai_loop", "run_loop"),
    ("_handle_stream_events", "handle_stream_events"),
    ("_execute_tool_call", "execute_tool_call"),
])
def test_loop_bodies_are_verbatim_after_self_to_sink(old_name, new_name):
    """把 `self.` 机械替换成 `sink.` 之后，新服务的函数体与原文**逐节点相同**。

    这是"只搬家不改逻辑"最直接的证据：任何一处逻辑改动（多一个分支、少一个
    `return`、顺序换了）都会让 AST 不等。
    """
    old_chat = _git_text("ui/agent/agent_chat.py")
    new_svc = (REPO_ROOT / "services" / "agent_service.py").read_text(encoding="utf-8")

    expected = _body_src(_func_node(old_chat, "AgentChatView", old_name))
    for old_snip, new_snip in _ALLOWED_DIFFS:
        expected = expected.replace(old_snip, new_snip)
    expected = expected.replace("self.", "sink.")

    actual = _body_src(_func_node(new_svc, None, new_name))
    assert actual == expected


@pytest.mark.parametrize("old_name,new_name,param_lines", [
    ("_get_active_model", "resolve_active_model",
     ["provider_name = self._provider_var.get()", "model_name = self._model_var.get()"]),
    ("_apply_file_edit", "apply_file_edit", []),
])
def test_pure_helpers_are_verbatim(old_name, new_name, param_lines):
    """纯函数同样逐节点一致；唯一允许的改写是"读控件变量的两行变成入参"。"""
    old_chat = _git_text("ui/agent/agent_chat.py")
    new_svc = (REPO_ROOT / "services" / "agent_service.py").read_text(encoding="utf-8")

    expected = _body_src(_func_node(old_chat, "AgentChatView", old_name))
    for line in param_lines:
        expected = "\n".join(ln for ln in expected.splitlines() if ln.strip() != line)
    actual = _body_src(_func_node(new_svc, None, new_name))
    assert actual == expected


def test_agent_service_never_starts_threads_or_imports_gui():
    """服务不自己起线程：`agent_service.py` 里不得出现 threading / tkinter。"""
    src = (REPO_ROOT / "services" / "agent_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert "threading" not in imported
    assert "tkinter" not in imported
    assert "customtkinter" not in imported


def _sink_members_used_by_service() -> set:
    """扫出服务里所有 `sink.<名字>`（即服务对界面适配对象的全部要求）。"""
    src = (REPO_ROOT / "services" / "agent_service.py").read_text(encoding="utf-8")
    names = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "sink":
            names.add(node.attr)
    return names


def test_every_sink_member_exists_on_the_real_view():
    """服务用到的每个 sink 成员，`AgentChatView` 上都必须真的有。

    这条守卫抓的是"搬代码时漏搬一个方法"（例如 `_handle_non_stream_response`
    忘了留在视图里）—— 这类错误在假 sink 的测试里看不出来，只有回到真实类上才暴露。
    """
    used = _sink_members_used_by_service()
    assert used, "没有扫到任何 sink 成员，抽取大概没生效"

    from ui.agent.agent_chat import AgentChatView  # noqa: PLC0415 - 只在需要时导入 Tk 栈

    chat_src = (REPO_ROOT / "ui" / "agent" / "agent_chat.py").read_text(encoding="utf-8")
    missing = []
    for name in sorted(used):
        # 方法（含从 CTkFrame 继承的 after）与类属性
        if hasattr(AgentChatView, name):
            continue
        # 实例属性（在 __init__ / 各方法里 self.xxx = ... 建立）
        if f"self.{name}" not in chat_src:
            missing.append(name)
    assert missing == [], f"服务要求 sink 提供这些成员，但 AgentChatView 没有：{missing}"


def test_view_public_methods_delegate_to_the_service():
    """界面侧的三个循环方法必须是薄委托（保留原名与签名）。"""
    chat_src = (REPO_ROOT / "ui" / "agent" / "agent_chat.py").read_text(encoding="utf-8")
    view = next(x for x in ast.parse(chat_src).body if isinstance(x, ast.ClassDef) and x.name == "AgentChatView")
    methods = {n.name: n for n in view.body if isinstance(n, ast.FunctionDef)}

    for name in ("_process_ai_loop", "_handle_stream_events", "_execute_tool_call",
                 "_apply_file_edit", "_get_active_model"):
        body = _body_src(methods[name])
        assert "self._agent_service." in body, f"{name} 没有委托给服务"
        # 委托体应当只有一条 return/调用语句（薄）
        assert len(body.splitlines()) <= 2, f"{name} 的委托体过长：{body}"


# ═══════════════════════════════════════════════════════════════════
# 三、权限三级判定（allow / deny / ask）
# ═══════════════════════════════════════════════════════════════════


def test_default_rules_are_the_three_tier_set():
    """默认规则：4 个危险工具 ask，其余 allow。顺序敏感（命中第一条即停）。"""
    pm = agent_permission.PermissionManager()
    assert pm.check("exec_command") == "ask"
    assert pm.check("write_file") == "ask"
    assert pm.check("replace_in_file") == "ask"
    assert pm.check("delete_file") == "ask"
    assert pm.check("read_file") == "allow"
    assert pm.check("随便一个不存在的工具") == "allow"


def test_ask_rule_must_come_before_the_allow_wildcard():
    """规则按顺序匹配：allow 通配符排在前面会把后面的 ask 全部吃掉。

    记录现状、疑为缺陷：`add_rule` 一律**追加到末尾**，所以"给某个工具加 ask"
    在默认规则下永远不生效（`*/* -> allow` 已经先命中）。这里钉住现状，
    不修改行为 —— 修的时候记得回来改这条用例。
    """
    pm = agent_permission.PermissionManager()
    pm.add_rule("my_tool", "*", "ask")
    assert pm.check("my_tool") == "allow"  # 现状：通配符先生效

    # 反过来，deny 放在通配符之前是生效的
    pm2 = agent_permission.PermissionManager(
        rules=[agent_permission.PermissionRule(action="danger", resource="*", effect="deny"),
               agent_permission.PermissionRule(action="*", resource="*", effect="allow")]
    )
    assert pm2.check("danger") == "deny"
    assert pm2.check("other") == "allow"


def test_deny_wins_and_remove_rule_reverts_to_wildcard():
    pm = agent_permission.PermissionManager(
        rules=[agent_permission.PermissionRule(action="write_file", resource="*", effect="deny"),
               agent_permission.PermissionRule(action="*", resource="*", effect="allow")]
    )
    assert pm.check("write_file") == "deny"
    pm.remove_rule("write_file", "*")
    assert pm.check("write_file") == "allow"


def test_rule_resource_matching_is_exact_or_star():
    pm = agent_permission.PermissionManager(
        rules=[agent_permission.PermissionRule(action="exec_command", resource="/tmp/a", effect="deny"),
               agent_permission.PermissionRule(action="*", resource="*", effect="allow")]
    )
    assert pm.check("exec_command", "/tmp/a") == "deny"
    assert pm.check("exec_command", "/tmp/b") == "allow"


def test_empty_rule_list_falls_back_to_ask():
    """无任何规则命中时返回 ask（默认询问）——`check` 的兜底分支。"""
    pm = agent_permission.PermissionManager(rules=[])
    assert pm.check("exec_command") == "ask"


def test_load_rules_ignores_unknown_effects_and_to_config_roundtrips(monkeypatch):
    monkeypatch.setattr(agent_permission, "_permission_manager", None)
    pm = agent_permission.PermissionManager(rules=[])
    pm.load_rules([{"action": "a", "resource": "*", "effect": "allow"},
                   {"action": "b", "resource": "*", "effect": "乱写的效果"},
                   {}])
    rules = pm.get_rules()
    assert {"action": "a", "resource": "*", "effect": "allow"} in rules
    assert all(r["action"] != "b" for r in rules), "非法 effect 必须被忽略"
    # {} 会按 (action="*", resource="*", effect="allow") 落一条
    assert {"action": "*", "resource": "*", "effect": "allow"} in rules

    dumped = pm.to_config()
    assert dumped == {"agent_permissions": rules}
    rebuilt = agent_permission.PermissionManager.from_config(dumped)
    assert rebuilt.get_rules() == rules

    # 全局单例入口：同一个对象
    assert agent_permission.get_permission_manager() is agent_permission.get_permission_manager()


def test_init_permission_manager_appends_custom_rules_after_the_defaults(monkeypatch):
    """记录现状、疑为缺陷：`init_permission_manager(custom_rules)` 是以
    `PermissionManager()`（= 默认规则）起步再 `load_rules` 追加的，于是自定义规则
    永远排在末尾的 `*/* -> allow` **之后**，等于**加不进去**。

    这是既有行为，本轮只搬家不改；修这个缺陷时要一并改掉这条断言。
    """
    monkeypatch.setattr(agent_permission, "_permission_manager", None)
    agent_permission.init_permission_manager([{"action": "my_new_tool", "resource": "*", "effect": "deny"}])
    assert agent_permission.check_permission("my_new_tool") == "allow"  # 现状：deny 被通配符吃掉
    loaded = agent_permission.get_permission_manager().get_rules()
    assert loaded[-1] == {"action": "my_new_tool", "resource": "*", "effect": "deny"}
    assert loaded[0] == {"action": "exec_command", "resource": "*", "effect": "ask"}, "默认规则仍在前"


# ═══════════════════════════════════════════════════════════════════
# 四、配置读写（真实落盘到 tmp_path，绝不碰用户 config.json）
# ═══════════════════════════════════════════════════════════════════


def test_agent_config_defaults_and_roundtrip():
    cfg = agent_config.AgentConfig()
    assert cfg.active_provider == "jingdu"
    assert cfg.max_iterations == 50
    assert cfg.stream_enabled is True
    assert cfg.compact_auto is True
    assert cfg.get_active_model() == ("jingdu", "")

    cfg.set_active_model("openai", "gpt-5.6-sol")
    assert cfg.active_provider == "openai"
    assert cfg.get_active_model() == ("openai", "gpt-5.6-sol")

    cfg.set_provider_config("openai", agent_config.ProviderConfig(enabled=True, api_key="sk-x", api_url="http://a"))
    assert cfg.is_provider_ready("openai") is True
    assert cfg.is_provider_ready("anthropic") is False
    assert cfg.is_provider_ready("jingdu") is True  # 净读 AI 恒定可用

    data = cfg.to_dict()
    again = agent_config.AgentConfig.from_dict(data)
    assert again.to_dict() == data
    assert agent_config.AgentConfig.from_dict(None).active_provider == "jingdu"


def test_get_provider_config_creates_on_demand_and_keeps_existing():
    cfg = agent_config.AgentConfig()
    pc = cfg.get_provider_config("custom")
    assert pc.api_key == ""
    pc.api_key = "k"
    assert cfg.get_provider_config("custom").api_key == "k"


def test_config_save_and_load_through_a_real_file(tmp_path, monkeypatch):
    """`save_to_file` / `load_from_file` 的落盘往返：只写 `agent` 段、保留其余键。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text(
        json.dumps({"player_name": "Steve", "agent": {"active_provider": "anthropic"}}), encoding="utf-8"
    )

    cfg = agent_config.AgentConfig.load_from_file()
    assert cfg.active_provider == "anthropic"

    cfg.bing_api_key = "bing-key"
    cfg.set_provider_config("custom", agent_config.ProviderConfig(api_key="c-k", custom_models=["m1"]))
    cfg.save_to_file()

    raw = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert raw["player_name"] == "Steve", "保存时必须保留主配置的其它键"
    assert raw["agent"]["providers"]["custom"]["custom_models"] == ["m1"]
    # 记录现状、疑为缺陷：`save_to_file` 写出的 agent 段**不含 bing_api_key**
    # （`to_dict` / `from_dict` 都有它，落盘这一段却漏了），所以重载后它回到 ""。
    assert "bing_api_key" not in raw["agent"]

    reloaded = agent_config.AgentConfig.load_from_file()
    assert reloaded.providers["custom"].api_key == "c-k"
    assert reloaded.active_provider == "anthropic"
    assert reloaded.bing_api_key == ""  # 记录现状、疑为缺陷：上述漏字段的直接后果


def test_config_load_tolerates_broken_json(tmp_path, monkeypatch):
    """记录现状、疑为缺陷：配置损坏时 `load_from_file` **静默**返回默认值（只剩日志）。"""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.json").write_text("{ 这不是 json", encoding="utf-8")
    cfg = agent_config.AgentConfig.load_from_file()
    assert cfg.active_provider == "jingdu"
    assert cfg.providers == {}


def test_config_missing_file_returns_defaults(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert agent_config.AgentConfig.load_from_file().active_provider == "jingdu"


def test_global_config_singleton_and_service_wrappers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent_config, "_agent_config", None)

    agent_config.init_agent_config()
    first = agent_config.get_agent_config()
    assert agent_config.get_agent_config() is first, "全局配置必须是同一份实例"

    svc = SVC.AgentService()
    assert svc.get_config() is first
    assert svc.bing_api_key() == ""
    first.bing_api_key = "abc"
    assert svc.bing_api_key() == "abc"
    svc.save_config()
    assert (tmp_path / "config.json").exists()
    assert svc.provider_config("nope") is None


# ═══════════════════════════════════════════════════════════════════
# 五、技能加载（SKILL.md 扫描）
# ═══════════════════════════════════════════════════════════════════


def _make_skill(root: Path, name: str, first_line: str, extra_files=()):
    d = root / "data" / "agent" / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"# {first_line}\n\n正文内容\n", encoding="utf-8")
    for rel in extra_files:
        f = d / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", encoding="utf-8")
    return d


def test_skills_dir_is_created_under_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = Path(agent_skill._get_skills_dir())
    assert d.is_dir()
    assert d == tmp_path / "data" / "agent" / "skills"


def test_load_all_skills_offline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_skill(tmp_path, "alpha", "第一个技能", extra_files=["notes.md", "sub/deep.txt"])
    _make_skill(tmp_path, "beta", "第二个技能")
    # 干扰项：没有 SKILL.md 的目录、以及一个普通文件
    (tmp_path / "data" / "agent" / "skills" / "empty_dir").mkdir()
    (tmp_path / "data" / "agent" / "skills" / "loose.md").write_text("x", encoding="utf-8")

    skills = agent_skill.load_all_skills()
    by_name = {s.name: s for s in skills}
    assert set(by_name) == {"alpha", "beta"}
    assert by_name["alpha"].description == "第一个技能"
    assert by_name["alpha"].content.startswith("# 第一个技能")
    assert "SKILL.md" not in by_name["alpha"].files
    assert sorted(by_name["alpha"].files) == sorted([os.path.join("sub", "deep.txt"), "notes.md"])
    assert by_name["beta"].files == []

    text = agent_skill.get_skills_context_text()
    assert "## 可用技能" in text
    assert "- **alpha**: 第一个技能" in text
    assert "- **beta**: 第二个技能" in text


def test_load_all_skills_empty_when_no_skills(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert agent_skill.load_all_skills() == []
    assert agent_skill.get_skills_context_text() == ""


def test_get_skill_by_name_and_prompt_text(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = _make_skill(tmp_path, "alpha", "第一个技能", extra_files=["notes.md"])

    skill = agent_skill.get_skill_by_name("alpha")
    assert skill is not None
    assert skill.directory == str(d)
    prompt = skill.to_prompt_text()
    assert '<skill_content name="alpha">' in prompt
    assert "# Skill: alpha" in prompt
    assert "<file>notes.md</file>" in prompt
    assert prompt.rstrip().endswith("</skill_content>")

    assert agent_skill.get_skill_by_name("不存在") is None


def test_skill_missing_skill_md_returns_none(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data" / "agent" / "skills" / "hollow").mkdir(parents=True)
    assert agent_skill.get_skill_by_name("hollow") is None
    assert agent_skill.load_all_skills() == []


def test_skill_service_wrappers(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _make_skill(tmp_path, "alpha", "第一个技能")
    svc = SVC.AgentService()
    assert [s.name for s in svc.load_skills()] == ["alpha"]
    assert svc.get_skill("alpha").description == "第一个技能"
    assert svc.skills_dir().endswith(os.path.join("data", "agent", "skills"))
    assert "## 可用技能" in svc.skills_context_text()


def test_system_prompt_text_is_the_old_concatenation():
    """`system_prompt_text()` 必须等于原文的 `get_system_prompt() + get_skills_context_text()`。"""
    from services.agent.skill import get_skills_context_text
    from services.agent.system_prompt import get_system_prompt

    assert SVC.system_prompt_text() == get_system_prompt() + get_skills_context_text()


# ═══════════════════════════════════════════════════════════════════
# 六、纯函数（原 AgentChatView / AgentMixin 里的逐字搬迁）
# ═══════════════════════════════════════════════════════════════════


def _provider_display_name(pid):
    from services.agent.models import get_provider_names

    return next((p["name"] for p in get_provider_names() if p["id"] == pid), None)


def test_resolve_active_model_maps_display_name_to_id():
    from services.agent.models import get_model_catalog

    flash = next(m for m in get_model_catalog() if m.id == "deepseek-v4-flash")
    jingdu = _provider_display_name("jingdu")
    assert SVC.resolve_active_model(jingdu, flash.name) == ("jingdu", "deepseek-v4-flash")

    custom = _provider_display_name("custom")
    if custom:
        # custom 分支：模型名原样回传（用户自己填的模型 id）
        assert SVC.resolve_active_model(custom, "my-local-model") == ("custom", "my-local-model")


def test_resolve_active_model_falls_back_for_unknown_names():
    assert SVC.resolve_active_model("不存在的供应商", "不存在的模型") == ("jingdu", "deepseek-v4-flash")
    jingdu = _provider_display_name("jingdu")
    assert SVC.resolve_active_model(jingdu, "不存在的模型") == ("jingdu", "deepseek-v4-flash")


def test_resolve_provider_id_defaults_to_jingdu():
    assert SVC.resolve_provider_id(_provider_display_name("openai")) == "openai"
    assert SVC.resolve_provider_id("查无此名") == "jingdu"


@pytest.mark.parametrize("pid,cls_name", [
    ("jingdu", "JingduProvider"),
    ("openai", "OpenAIProvider"),
    ("anthropic", "AnthropicProvider"),
    ("custom", "CustomProvider"),
])
def test_build_provider_branches(pid, cls_name):
    pc = agent_config.ProviderConfig(api_key="k", api_url="http://example.invalid/v1", custom_models=["m"])
    provider = SVC.build_provider(pid, pc, thinking_enabled=True, reasoning_effort="high")
    assert type(provider).__name__ == cls_name
    assert provider.api_key == "k"


def test_build_provider_returns_none_without_api_key():
    """没有 API Key 时返回 None —— 与原文"不覆盖 self._provider"等价。"""
    assert SVC.build_provider("openai", agent_config.ProviderConfig()) is None
    assert SVC.build_provider("openai", None) is None
    assert SVC.build_provider("未知 pid", agent_config.ProviderConfig(api_key="k")) is None


def test_jingdu_provider_helper_matches_the_mixin_construction():
    """`AgentMixin` 里那条构造路径（只传 api_key）被逐字保留，**没有**被"统一"成 build_provider。"""
    provider = SVC.AgentService().jingdu_provider("token-1")
    assert type(provider).__name__ == "JingduProvider"
    assert provider.api_key == "token-1"


def test_apply_file_edit_write_replace_delete(tmp_path):
    target = tmp_path / "sub" / "a.txt"

    out = SVC.apply_file_edit({"filePath": str(target), "newText": "hello"}, "write")
    assert out == f"已写入文件: {target}"
    assert target.read_text(encoding="utf-8") == "hello"

    out = SVC.apply_file_edit({"filePath": str(target), "newText": "hi", "replacements": 2}, "replace")
    assert out == f"已替换 2 处: {target}"
    assert target.read_text(encoding="utf-8") == "hi"

    out = SVC.apply_file_edit({"filePath": str(target)}, "delete")
    assert out == f"已删除文件: {target}"
    assert not target.exists()

    assert SVC.apply_file_edit({"filePath": str(target)}, "未知操作").startswith("未知操作类型:")
    assert SVC.apply_file_edit({"filePath": str(target)}, "delete").startswith("文件操作失败:")


def test_build_callbacks_copies_and_injects():
    base = {"get_jdz_token": lambda: "t"}
    session_id = lambda: "sid"  # noqa: E731
    cfg = lambda: {"bing_api_key": "b"}  # noqa: E731

    cbs = SVC.build_callbacks(base, session_id, cfg)
    assert cbs is not base
    assert base == {"get_jdz_token": base["get_jdz_token"]}, "不得就地修改调用方传进来的字典"
    assert cbs["get_jdz_token"]() == "t"
    assert cbs["get_current_session_id"]() == "sid"
    assert cbs["get_config"]() == {"bing_api_key": "b"}

    # base 为 None 时也不炸
    assert set(SVC.build_callbacks(None, session_id, cfg)) == {"get_current_session_id", "get_config"}


def test_fetch_credits():
    assert SVC.fetch_credits(lambda: {"ai_credits": 7}) == 7
    assert SVC.fetch_credits(lambda: {"ai_credits": 0}) == 0
    assert SVC.fetch_credits(lambda: None) is None
    assert SVC.fetch_credits(lambda: {}) is None


# ═══════════════════════════════════════════════════════════════════
# 七、工具注册与调用
# ═══════════════════════════════════════════════════════════════════


def test_registry_is_a_singleton_and_registers_builtins():
    assert ToolRegistry() is ToolRegistry()
    reg = get_registry()
    names = reg.get_tool_names()
    for expected in ("get_installed_versions", "install_version", "exec_command",
                     "ask_user", "skill", "todo_write", "web_fetch", "web_search", "read_file"):
        assert expected in names, f"内置工具 {expected} 未注册"
    assert reg.get("不存在的工具") is None
    assert reg.has_tool("exec_command") is True
    assert reg.get_by_category("system"), "至少要有 system 分类的工具"


def test_tool_definitions_are_openai_function_calling_shape():
    defs = get_registry().get_definitions()
    assert defs
    for d in defs:
        assert d["type"] == "function"
        fn = d["function"]
        assert fn["name"] and isinstance(fn["name"], str)
        assert isinstance(fn["description"], str)
        assert isinstance(fn["parameters"], dict)
    assert len(defs) == len(get_registry().get_all())


def test_execute_unknown_tool_returns_error_text():
    assert get_registry().execute("查无此工具", {}, {}) == "错误: 未知工具 '查无此工具'"


def test_execute_real_tool_with_fake_callbacks():
    """真实工具 + 假回调：`get_installed_versions` 完全离线可跑。"""
    text = get_registry().execute(
        "get_installed_versions", {}, {"get_installed_versions": lambda: [{"id": "1.20.1", "type": "release"}]}
    )
    assert "1.20.1" in text


def test_execute_swallows_tool_exceptions_into_error_text():
    """记录现状、疑为缺陷：工具抛异常时 `execute` 返回"❌ ..."字符串而不是抛出，
    调用方无法区分"工具失败"与"工具正常返回了这段文本"（只在开头 emoji 上区分）。"""
    reg = get_registry()
    boom = ToolInfo(name="_t_boom", description="d", parameters={}, category="system",
                    permission_action="_t_boom", execute=lambda params, cbs: (_ for _ in ()).throw(RuntimeError("炸了")))
    reg.register(boom)
    try:
        out = reg.execute("_t_boom", {}, {})
        assert out.startswith("❌ 工具 '_t_boom' 执行失败: 炸了")
    finally:
        reg.unregister("_t_boom")


def test_execute_with_result_detects_ask_user_marker():
    reg = get_registry()
    tool = ToolInfo(
        name="_t_ask", description="d", parameters={}, category="user", permission_action="_t_ask",
        execute=lambda params, cbs: "__ASK_USER__|" + json.dumps([{"question": "q", "options": []}]),
    )
    reg.register(tool)
    try:
        result = reg.execute_with_result("_t_ask", {}, {})
        assert result.needs_user_confirm == "ask_user"
        assert result.confirm_data["questions"][0]["question"] == "q"
        assert result.success is False
    finally:
        reg.unregister("_t_ask")


def test_execute_with_result_detects_legacy_ask_user_and_dangerous_marker():
    reg = get_registry()
    legacy = ToolInfo(name="_t_legacy", description="d", parameters={}, category="user", permission_action="_t_legacy",
                      execute=lambda params, cbs: "__ASK_USER__|请选择|" + json.dumps(["A", "B"]))
    danger = ToolInfo(name="_t_danger", description="d", parameters={}, category="system", permission_action="_t_danger",
                      execute=lambda params, cbs: "__DANGEROUS__|" + json.dumps({"path": "/p", "command": "rm -rf"}))
    reg.register(legacy)
    reg.register(danger)
    try:
        r1 = reg.execute_with_result("_t_legacy", {}, {})
        assert r1.confirm_data["questions"][0]["question"] == "请选择"
        assert [o["label"] for o in r1.confirm_data["questions"][0]["options"]] == ["A", "B"]

        r2 = reg.execute_with_result("_t_danger", {}, {})
        assert r2.needs_user_confirm == "dangerous_command"
        assert r2.confirm_data == {"path": "/p", "command": "rm -rf"}
    finally:
        reg.unregister("_t_legacy")
        reg.unregister("_t_danger")


def test_success_flag_uses_the_chinese_error_prefix():
    """记录现状、疑为缺陷：成功与否靠**文本前缀**判断（"❌"/"错误"）。"""
    reg = get_registry()
    bad = ToolInfo(name="_t_bad", description="d", parameters={}, category="system", permission_action="_t_bad",
                   execute=lambda params, cbs: "错误: 参数不对")
    reg.register(bad)
    try:
        assert reg.execute_with_result("_t_bad", {}, {}).success is False
    finally:
        reg.unregister("_t_bad")


def test_service_tool_wrappers():
    svc = SVC.AgentService()
    assert svc.registry() is get_registry()
    assert len(svc.tool_definitions()) == len(get_registry().get_definitions())
    assert svc.execute_tool("查无此工具", {}, {}).startswith("错误: 未知工具")
    assert "1.20.1" in svc.execute_tool(
        "get_installed_versions", {}, {"get_installed_versions": lambda: [{"id": "1.20.1", "type": "release"}]}
    )


# ═══════════════════════════════════════════════════════════════════
# 八、AI 处理循环（六条路径，全部离线且无线程）
# ═══════════════════════════════════════════════════════════════════


def _run(tmp_path, monkeypatch, sink):
    """把 cwd 切到 tmp_path（会话/待办都落在临时目录），再跑一次循环。"""
    monkeypatch.chdir(tmp_path)
    SVC.run_loop(sink)
    return sink


def test_loop_single_turn_without_tools(tmp_path, monkeypatch):
    provider = FakeProvider([[
        {"type": "text_delta", "text": "你好"},
        {"type": "usage", "usage": {"total_tokens": 42}},
        {"type": "done"},
    ]])
    sink = _run(tmp_path, monkeypatch, FakeSink(provider=provider))

    assert provider.stream_calls == 1
    assert provider.last_stream_kwargs["model"] == "deepseek-v4-flash"
    assert sink._message_blocks[-1]["content"] == "你好"
    assert sink._session.messages[-1] == {"role": "assistant", "content": "你好"}
    assert sink.done_notifies == 1
    assert sink.failed_notifies == []
    assert sink.dividers == 2  # done 事件一次 + 任务完成一次
    assert sink.saved_sessions == [sink._session]
    assert sink.reset_button_calls == 1
    assert sink.todo_refreshes == 1
    assert sink.session_list_refreshes == 1
    assert sink._ai_processing is False
    assert sink._token_label.text.startswith("Token ~")
    assert sink.render_calls[-1] is True, "流式结束后必须强制渲染一次"
    assert (tmp_path / "data" / "agent" / f"{sink._session.id}.json").exists()


def test_loop_executes_tool_calls_then_finishes(tmp_path, monkeypatch):
    registry = FakeRegistry(results={"read_file": "文件内容"})
    provider = FakeProvider([
        _tool_call_event("call_1", "read_file", '{"filePath": "a.txt"}') + [{"type": "done"}],
        [{"type": "text_delta", "text": "读完了"}, {"type": "done"}],
    ])
    sink = _run(tmp_path, monkeypatch, FakeSink(registry=registry, provider=provider))

    assert provider.stream_calls == 2
    assert registry.calls == [{"name": "read_file", "params": {"filePath": "a.txt"}}]
    assert sink.tool_starts == ["read_file"]
    assert sink.tool_results == [("read_file", "文件内容")]
    assert sink.done_notifies == 1
    roles = [m["role"] for m in sink._session.messages]
    assert roles[-3:] == ["assistant", "tool", "assistant"]
    assert sink._session.messages[-3]["tool_calls"][0]["function"]["name"] == "read_file"
    assert sink._session.messages[-2]["content"] == "文件内容"


def test_loop_waits_for_user_on_dangerous_command(tmp_path, monkeypatch):
    payload = "__DANGEROUS__|" + json.dumps({"path": "/p", "command": "rm -rf /"})
    registry = FakeRegistry(results={"exec_command": payload})
    provider = FakeProvider([_tool_call_event("c1", "exec_command", '{"command": "rm -rf /"}') + [{"type": "done"}]])
    sink = _run(tmp_path, monkeypatch, FakeSink(registry=registry, provider=provider))

    assert sink.dialog_calls == [("dangerous", "/p", "rm -rf /")]
    assert sink._pending_dangerous == ("/p", "rm -rf /", "c1")
    # WAIT_USER 路径：只落盘，不重置 UI（确认回调会再起一个循环）
    assert sink.saved_sessions == [sink._session]
    assert sink.reset_button_calls == 0
    assert sink._ai_processing is True
    assert sink.done_notifies == 0
    assert provider.stream_calls == 1


def test_execute_tool_call_denied_by_policy(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        agent_permission, "_permission_manager",
        agent_permission.PermissionManager(rules=[
            agent_permission.PermissionRule(action="danger_tool", resource="*", effect="deny"),
            agent_permission.PermissionRule(action="*", resource="*", effect="allow"),
        ]),
    )
    registry = FakeRegistry(results={"danger_tool": "不该被调到"})
    sink = FakeSink(registry=registry)

    assert SVC.execute_tool_call(
        {"id": "c9", "function": {"name": "danger_tool", "arguments": "{}"}}, sink
    ) is True
    assert registry.calls == [], "deny 必须早于工具执行"
    assert sink.system_messages == ["🔒 工具 danger_tool 被策略禁止"]
    assert sink._session.messages[-1]["content"] == "工具 danger_tool 被权限策略禁止"


def test_execute_tool_call_ask_path_falls_through_when_not_a_marker(tmp_path, monkeypatch):
    """ask + exec_command，但工具返回的不是危险标记 → 走普通结果路径（返回 True）。"""
    monkeypatch.chdir(tmp_path)
    registry = FakeRegistry(results={"exec_command": "命令已执行"})
    sink = FakeSink(registry=registry)

    assert SVC.execute_tool_call(
        {"id": "c2", "function": {"name": "exec_command", "arguments": '{"command": "ls"}'}}, sink
    ) is True
    assert sink._pending_dangerous is None
    assert sink.tool_results == [("exec_command", "命令已执行")]


def test_execute_tool_call_ask_user_marker_waits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    questions = [{"question": "选哪个", "options": []}]
    registry = FakeRegistry(results={"ask_user": "__ASK_USER__|" + json.dumps(questions)})
    sink = FakeSink(registry=registry)

    out = SVC.execute_tool_call(
        {"id": "c3", "function": {"name": "ask_user", "arguments": "{}"}}, sink
    )
    assert out == "WAIT_USER"
    assert sink._pending_ask_user == (questions, "c3")
    assert sink.dialog_calls == [("ask_user", questions)]


def test_execute_tool_call_bad_json_arguments_becomes_empty_params(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    registry = FakeRegistry(results={"read_file": "ok"})
    sink = FakeSink(registry=registry)

    SVC.execute_tool_call({"id": "c4", "function": {"name": "read_file", "arguments": "{坏 json"}}, sink)
    assert registry.calls == [{"name": "read_file", "params": {}}]


def test_loop_stops_on_stream_error_event(tmp_path, monkeypatch):
    provider = FakeProvider([[
        {"type": "text_delta", "text": "半句"},
        {"type": "error", "message": "上游 500"},
    ]])
    sink = _run(tmp_path, monkeypatch, FakeSink(provider=provider))

    assert sink.system_messages == ["❌ 上游 500"]
    assert sink.done_notifies == 0
    assert sink.reset_button_calls == 1
    assert provider.stream_calls == 1


def test_loop_falls_back_to_non_stream_when_stream_start_fails(tmp_path, monkeypatch):
    provider = FakeProvider([], stream_fail_times=1,
                            chat_response={"role": "assistant", "content": "非流式回答"})
    sink = _run(tmp_path, monkeypatch, FakeSink(provider=provider))

    assert provider.stream_calls == 2  # 第一次失败 → 回退；第二轮重新尝试流式
    assert provider.chat_calls == 1
    assert sink._message_blocks[0]["content"] == "非流式回答"


def test_loop_reports_failure_when_both_paths_fail(tmp_path, monkeypatch):
    provider = FakeProvider([], stream_raises=True, chat_raises=True)
    sink = _run(tmp_path, monkeypatch, FakeSink(provider=provider))

    assert sink.system_messages == ["AI 调用失败: 非流式也失败（测试用）"]
    assert sink.done_notifies == 0
    assert sink._ai_processing is False


def test_loop_breaks_when_no_provider(tmp_path, monkeypatch):
    sink = _run(tmp_path, monkeypatch, FakeSink(provider=None))
    assert sink.system_messages == ["未配置 AI 提供商"]
    assert sink._ai_processing is False
    assert sink.reset_button_calls == 1


def test_loop_honours_max_iterations(tmp_path, monkeypatch):
    """模型每轮都要调工具 → 循环必须被 MAX_ITERATIONS 截断，而不是无限转。"""
    turns = [_tool_call_event(f"c{i}", "read_file", "{}") + [{"type": "done"}] for i in range(10)]
    registry = FakeRegistry(results={"read_file": "内容"})
    provider = FakeProvider(turns)
    sink = _run(tmp_path, monkeypatch, FakeSink(registry=registry, provider=provider, max_iterations=3))

    assert provider.stream_calls == 3
    assert len(registry.calls) == 3
    assert sink.done_notifies == 0  # 从未走到"任务完成"
    assert sink.failed_notifies == []
    roles = [m["role"] for m in sink._session.messages]
    assert roles.count("assistant") == 3 and roles.count("tool") == 3


def test_loop_compacts_long_context_every_ten_iterations(tmp_path, monkeypatch):
    """`iteration % 10 == 0 且 token > 60000` 时才压缩 —— 这里只验它不误触发。"""
    turns = [_tool_call_event(f"c{i}", "read_file", "{}") + [{"type": "done"}] for i in range(5)]
    session = AgentSession.create_new(system_prompt="sys")
    calls = []
    session.compact = lambda *a, **k: calls.append(1)
    sink = _run(tmp_path, monkeypatch, FakeSink(registry=FakeRegistry(), provider=FakeProvider(turns),
                                                session=session, max_iterations=5))
    assert calls == []


def test_loop_reports_exception_from_the_body(tmp_path, monkeypatch):
    """循环体内部抛异常 → 记日志 + 走失败通知，且 `finally` 仍会收尾。"""
    class BoomRegistry(FakeRegistry):
        def get_definitions(self):
            raise RuntimeError("注册表坏了")

    sink = _run(tmp_path, monkeypatch, FakeSink(registry=BoomRegistry(), provider=FakeProvider([])))
    assert sink.failed_notifies == ["注册表坏了"]
    assert sink._ai_processing is False
    assert sink.reset_button_calls == 1
    assert sink.saved_sessions == [sink._session]


def test_run_loop_forwards_thinking_flags_to_provider(tmp_path, monkeypatch):
    """循环每轮把界面上的思考模式/推理强度同步给 provider（原文行为）。"""
    provider = FakeProvider([[{"type": "done"}]])
    sink = FakeSink(provider=provider)
    sink._thinking_var = FakeVar(True)
    sink._effort_var = FakeVar("max")
    _run(tmp_path, monkeypatch, sink)
    assert provider.thinking_enabled is True
    assert provider.reasoning_effort == "max"


def test_service_run_loop_is_the_same_function(tmp_path, monkeypatch):
    """服务的三个循环入口都是模块级函数的转发（同一对象）。"""
    svc = SVC.AgentService()
    assert svc.run_loop.__func__ is not None
    monkeypatch.chdir(tmp_path)
    provider = FakeProvider([[{"type": "text_delta", "text": "hi"}, {"type": "done"}]])
    sink = FakeSink(provider=provider)
    svc.run_loop(sink)
    assert sink.done_notifies == 1


# ═══════════════════════════════════════════════════════════════════
# 九、AgentService 作为服务对象的契约
# ═══════════════════════════════════════════════════════════════════


def test_agent_service_is_constructible_without_a_context():
    svc = SVC.AgentService()
    assert svc.name == "agent"
    assert svc.label == "AI 助手"
    assert svc.attached is False
    assert svc.started is False
    svc.start()
    assert svc.started is True
    svc.stop()
    assert svc.started is False
    assert svc.describe()["class"] == "AgentService"


def test_agent_service_context_access_raises_without_app_context():
    svc = SVC.AgentService()
    with pytest.raises(ServiceNotAvailable):
        _ = svc.context


def test_handle_stream_events_ignores_non_dict_events(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sink = FakeSink()
    sink._streaming_block = {"role": "assistant", "content": "", "thinking": "", "tag": ""}
    content, tool_calls, needs_break = SVC.handle_stream_events(
        iter(["不是字典", 42, {"type": "text_delta", "text": "a"}, None]), sink
    )
    assert (content, tool_calls, needs_break) == ("a", [], False)
    assert sink._streaming_block["content"] == "a"


def test_handle_stream_events_replaces_tool_call_on_complete(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    sink = FakeSink()
    final = {"id": "x", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
    events = [
        {"type": "tool_call_start", "tool_call_id": "x"},
        {"type": "tool_call_name", "tool_name": "read_file"},
        {"type": "tool_call_args", "tool_args": "{}"},
        {"type": "tool_call_complete", "tool_call": final},
    ]
    content, tool_calls, needs_break = SVC.handle_stream_events(iter(events), sink)
    assert tool_calls == [final]
    assert sink.tool_starts == ["read_file"]
    assert needs_break is False
