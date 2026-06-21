"""FMCL Agent CLI - 通过命令行使用 AGENT 智能助手 / 登录净读 AI

用法:
    python main.py login -name <用户名> -pwd <密码>
    python main.py login -name <用户名>          (交互输入密码)
    python main.py -agent <自然语言指令>
    python main.py -A <自然语言指令>
    python main.py -A                    # 进入交互模式
"""

import sys
from typing import List, Dict, Optional

from logzero import logger

from config import config
from secure_storage import encrypt_token
from ui.agent.providers.jingdu import JingduProvider
from ui.agent.tool_registry import get_registry, get_tool_definitions
from ui.agent.system_prompt import get_system_prompt
from ui.agent.tools.system import DANGEROUS_MARKER, ASK_USER_MARKER, execute_dangerous_command


def _print(text: str = ""):
    print(text, flush=True)


def _print_user(text: str):
    _print(f"\033[92m🧑  {text}\033[0m")


def _print_assistant(text: str):
    _print(f"\033[94m🤖  {text}\033[0m")


def _print_tool(name: str, params: dict):
    import json
    _print(f"\033[93m🔧  调用工具: {name}({json.dumps(params, ensure_ascii=False)})\033[0m")


def _print_tool_result(result: str):
    for line in result.split("\n"):
        _print(f"\033[90m    {line}\033[0m")


def _print_error(text: str):
    _print(f"\033[91m❌  {text}\033[0m")


def _print_system(text: str):
    _print(f"\033[90m⚙   {text}\033[0m")


def _print_divider():
    _print("─" * 50)


def _print_welcome():
    _print()
    _print("\033[1m  FMCL Agent CLI\033[0m")
    _print("  输入自然语言指令管理 Minecraft，输入 /quit 退出")
    _print()


def _get_callbacks() -> Dict:
    """构建 CLI 模式下 Agent 需要的回调字典"""
    from launcher import MinecraftLauncher

    launcher = MinecraftLauncher(config)
    callbacks = launcher.get_callbacks()
    logger.info(f"[CLI Agent] 回调初始化完成，包含 {len(callbacks)} 个回调")
    return callbacks


def _confirm_dangerous_command(path: str, command: str) -> bool:
    _print(f"\033[91m⚠️  高危命令警告!\033[0m")
    _print(f"   路径: {path}")
    _print(f"   命令: {command}")
    _print(f"\033[91m   此命令可能造成不可逆的系统损坏\033[0m")
    try:
        choice = input("   确认执行? (输入 yes 确认, 其他取消): ").strip()
        return choice.lower() == "yes"
    except (EOFError, KeyboardInterrupt):
        _print()
        return False


def _process_once(
    messages: List[Dict],
    provider: JingduProvider,
    callbacks: Dict,
    user_input: str,
) -> List[Dict]:
    """执行一轮 Agent 循环，返回更新后的消息列表"""
    messages.append({"role": "user", "content": user_input})

    max_iterations = 50
    max_empty_retries = 3
    iteration = 0
    empty_content_count = 0

    while iteration < max_iterations:
        iteration += 1

        try:
            response_msg = provider.chat(
                messages=messages,
                tools=get_tool_definitions(),
            )
        except Exception as e:
            _print_error(f"API 调用失败: {e}")
            break

        tool_calls = response_msg.get("tool_calls", [])
        content = response_msg.get("content", "")

        if not content and not tool_calls:
            empty_content_count += 1
            if empty_content_count >= max_empty_retries:
                _print_error("AI 多次返回空内容，请检查 Token 是否有效")
                break
            messages.append({
                "role": "user",
                "content": "你返回了空内容，请继续",
            })
            continue

        empty_content_count = 0

        messages.append(response_msg)

        if content:
            _print_assistant(content)

        if tool_calls:
            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", "")
                tool_call_id = tc.get("id", "")

                import json as _json
                try:
                    tool_params = _json.loads(func.get("arguments", "{}"))
                except (_json.JSONDecodeError, TypeError):
                    tool_params = {}

                if not tool_name:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": "错误: 工具名为空",
                    })
                    continue

                _print_tool(tool_name, tool_params)
                result_text = get_registry().execute(tool_name, tool_params, callbacks)

                if result_text.startswith(DANGEROUS_MARKER):
                    parts = result_text.split("|", 2)
                    exec_path = parts[1]
                    exec_command = parts[2]
                    if _confirm_dangerous_command(exec_path, exec_command):
                        _print_system(f"用户确认执行高危命令: {exec_command}")
                        result_text = execute_dangerous_command(exec_path, exec_command)
                    else:
                        _print_system(f"用户取消了高危命令: {exec_command}")
                        result_text = f"⚠️ 用户取消了命令执行\n路径: {exec_path}\n命令: {exec_command}"

                if result_text.startswith(ASK_USER_MARKER):
                    parts = result_text.split("|", 2)
                    question = parts[1]
                    options_json = parts[2] if len(parts) > 2 else "[]"
                    import json as _json2
                    try:
                        options = _json2.loads(options_json)
                    except (_json2.JSONDecodeError, TypeError):
                        options = []

                    _print(f"\033[96m🤔 {question}\033[0m")
                    if options:
                        for i, opt in enumerate(options, 1):
                            _print(f"  \033[96m{i}. {opt}\033[0m")

                    try:
                        reply = input("\033[92m> \033[0m").strip()
                    except (EOFError, KeyboardInterrupt):
                        _print()
                        reply = ""

                    if options:
                        try:
                            idx = int(reply) - 1
                            if 0 <= idx < len(options):
                                reply = options[idx]
                        except ValueError:
                            pass

                    if not reply:
                        reply = "（用户未回复）"

                    result_text = f"用户的回复: {reply}"

                _print_tool_result(result_text)
                _print_divider()

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_text,
                })

            continue

        else:
            _print_divider()
            break

    return messages


def run_agent_cli(instruction: Optional[str] = None):
    """运行 Agent CLI 模式

    Args:
        instruction: 初始指令，为 None 则进入交互模式
    """
    token = config.jdz_token
    if not token:
        _print_error("未配置净读 AI Token，请先在 GUI 模式下登录净读 AI 账号")
        _print_system("或在 config.json 中设置 jdz_token 字段")
        sys.exit(1)

    _print_system("正在初始化 Agent...")
    try:
        callbacks = _get_callbacks()
    except Exception as e:
        _print_error(f"初始化启动器失败: {e}")
        sys.exit(1)

    provider = JingduProvider(api_key=token)
    _print_system("Agent 就绪")

    messages: List[Dict] = [
        {"role": "system", "content": get_system_prompt()},
    ]

    if instruction:
        _print_user(instruction)
        _print_divider()
        messages = _process_once(messages, provider, callbacks, instruction)
        return

    # 交互模式
    _print_welcome()
    while True:
        try:
            user_input = input("\033[92m> \033[0m").strip()
        except (EOFError, KeyboardInterrupt):
            _print()
            break

        if not user_input:
            continue
        if user_input.lower() in ("/quit", "/exit", "/q"):
            break
        if user_input.lower() == "/clear":
            messages = [{"role": "system", "content": get_system_prompt()}]
            _print_system("对话已清空")
            continue

        _print_user(user_input)
        _print_divider()
        messages = _process_once(messages, provider, callbacks, user_input)

    _print_system("再见!")


def run_login(username: str, password: str | None):
    """通过命令行登录净读 AI 并保存 Token

    Args:
        username: 净读 AI 用户名
        password: 密码，为 None 则在终端提示输入（不回显）
    """
    import getpass
    import json
    import urllib.request
    import urllib.error

    if not password:
        try:
            password = getpass.getpass("请输入密码: ")
        except (EOFError, KeyboardInterrupt):
            _print()
            return
    if not password:
        _print_error("密码不能为空")
        return

    _print_system(f"正在登录净读 AI (用户: {username})...")
    try:
        req_data = json.dumps({"username": username, "password": password}).encode("utf-8")
        req = urllib.request.Request(
            "https://jingdu.qzz.io/api/auth/login",
            data=req_data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "FMCL/1.0 (Minecraft Launcher; crash-analyzer)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        token = result.get("token")
        if not token:
            _print_error("登录失败: 未获取到 Token")
            return

        config.jdz_token = token
        config.save_config()
        _print("\033[92m✅  净读 AI 登录成功，Token 已保存\033[0m")
        _print_system("现在可以使用 -A 或 -agent 进入 Agent CLI 模式")

    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")
        except Exception:
            pass
        _print_error(f"登录失败: HTTP {e.code} - {body[:100]}")
    except Exception as e:
        _print_error(f"登录失败: {e}")
