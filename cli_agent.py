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
from ui.agent.provider import AIProvider
from ui.agent.tools import get_tool_definitions, get_system_prompt
from ui.agent.engine import execute_tool
from ui.agent.xml_parser import ParsedResponse


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


def _process_once(
    messages: List[Dict],
    provider: AIProvider,
    callbacks: Dict,
    user_input: str,
) -> List[Dict]:
    """执行一轮 Agent 循环，返回更新后的消息列表"""
    messages.append({"role": "user", "content": user_input})

    max_iterations = 10
    max_format_retries = 3
    iteration = 0
    format_error_count = 0

    while iteration < max_iterations:
        iteration += 1

        try:
            response_text = provider.chat(
                messages=messages,
                tools=get_tool_definitions(),
            )
        except Exception as e:
            _print_error(f"API 调用失败: {e}")
            break

        if not response_text or not response_text.strip():
            format_error_count += 1
            if format_error_count >= max_format_retries:
                _print_error("AI 多次返回空内容，请检查 Token 是否有效")
                break
            messages.append({
                "role": "user",
                "content": "你返回了空内容，请按 XML 格式回复",
            })
            continue

        parsed = ParsedResponse.parse(response_text)
        messages.append({"role": "assistant", "content": response_text})

        if parsed.is_tool_call():
            format_error_count = 0
            tool_name = parsed.tool_name
            tool_params = parsed.tool_params

            if not tool_name:
                messages.append({
                    "role": "user",
                    "content": "你返回的 XML 格式不完整（缺少 <tool> 标签），请严格按格式重新回复",
                })
                continue

            _print_tool(tool_name, tool_params)
            result_text = execute_tool(tool_name, tool_params, callbacks)
            _print_tool_result(result_text)
            _print_divider()

            messages.append({
                "role": "user",
                "content": f"工具 {tool_name} 执行结果:\n{result_text}",
            })

        elif parsed.is_await_choice():
            format_error_count = 0
            options = parsed.options
            if options:
                for i, opt in enumerate(options, 1):
                    _print(f"  \033[96m{i}. {opt['label']}\033[0m")

                try:
                    choice = input("  请输入选项编号: ").strip()
                    idx = int(choice) - 1
                    if 0 <= idx < len(options):
                        selected_value = options[idx]["value"]
                        selected_label = options[idx]["label"]
                        _print_user(f"选择了: {selected_label}")
                        messages.append({"role": "user", "content": f"我选择: {selected_value}"})
                        _print_divider()
                        continue  # 将选择发送给 AI 并继续循环
                    else:
                        _print_error("无效选项")
                        break
                except (ValueError, EOFError, KeyboardInterrupt):
                    _print()
                    break
            break

        elif parsed.is_complete():
            if parsed.message:
                _print_assistant(parsed.message)
            _print_divider()
            break

        else:
            format_error_count += 1
            if format_error_count >= max_format_retries:
                _print_error("AI 未按格式回复，已停止")
                break
            messages.append({
                "role": "user",
                "content": "请严格按 XML 格式回复。需要调用工具时用 <action type=\"tool_call\"><tool>工具名</tool><params><parameter name=\"参数名\">参数值</parameter></params></action>，不需要调用工具时用 <action type=\"complete\" />",
            })

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

    provider = AIProvider.from_config(token)
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
