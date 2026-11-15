"""Fusion Minecraft Launcher - 主程序入口。

本文件只负责日志、CLI 模式与 QML 入口分发；界面装配在 `main_qml.py`。
"""

import os
import re
import sys
from pathlib import Path

import logzero
from logzero import logger

from config import config


def set_chinese_language():
    """
    启动时自动将 .minecraft/options.txt 中的语言设置改为中文
    查找 lang: 开头的行，将其改为 lang:zh_cn
    """
    options_file = config.minecraft_dir / "options.txt"

    if not options_file.exists():
        logger.info("options.txt 不存在，跳过语言设置")
        return

    try:
        content = options_file.read_text(encoding="utf-8")
        new_content, count = re.subn(r"^lang:.*$", "lang:zh_cn", content, flags=re.MULTILINE)

        if count > 0:
            options_file.write_text(new_content, encoding="utf-8")
            logger.info("已将游戏语言设置为中文 (zh_cn)")
        else:
            with open(options_file, "a", encoding="utf-8") as f:
                f.write("\nlang:zh_cn")
            logger.info("options.txt 中未找到 lang 配置，已追加 lang:zh_cn")

    except Exception as e:
        logger.error(f"设置游戏语言失败: {e}")


def setup_logging():
    """配置日志系统，如果默认日志目录不可写则回退到用户目录"""
    log_file = config.log_file
    log_dir = log_file.parent

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        test_file = log_dir / ".fmcl_write_test"
        test_file.touch()
        test_file.unlink()
    except (PermissionError, OSError):
        fallback_dir = Path.home() / ".fmcl"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        log_file = fallback_dir / "latest.log"
        logger.warning(f"日志目录 {log_dir} 不可写，回退到 {log_file}")

    logzero.logfile(str(log_file), encoding="utf-8")
    logzero.loglevel(config.log_level)
    logger.info("日志系统初始化完成")

    from structured_logger import slog

    structured_log_path = str(config.base_dir / "latest_structured.log")
    slog._log_path = structured_log_path


def _parse_cli_args():
    """解析命令行参数，支持以下 CLI 模式:

      python main.py login -name <username>
      python main.py -agent <指令>
      python main.py -A <指令>
      python main.py -A          (交互模式)

    Returns:
        ("agent", instruction) | ("login", (username, password)) | (None, None)
    """
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "login":
            username = None
            j = i + 1
            while j < len(args):
                if args[j] in ("-name", "--name"):
                    if j + 1 < len(args):
                        username = args[j + 1]
                        j += 2
                    else:
                        _print_cli_error("缺少用户名参数")
                        sys.exit(1)
                elif args[j] in ("-pwd", "--pwd", "-password", "--password"):
                    _print_cli_error("-pwd 参数已弃用（密码在进程列表中可见，不安全）")
                    _print_cli_error("请使用交互方式输入密码（程序会提示输入）")
                    if j + 1 < len(args):
                        j += 2
                    else:
                        j += 1
                else:
                    break
            if not username:
                _print_cli_error("用法: python main.py login -name <用户名>")
                sys.exit(1)
            return "login", (username, None)
        if arg in ("-A", "-agent", "--agent"):
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                return "agent", args[i + 1]
            return "agent", None
        i += 1
    return None, None


def _print_cli_error(msg: str):
    print(f"❌  {msg}", flush=True)


def _attach_console():
    """尝试附加到父进程控制台（打包后 GUI 子系统的 exe 需要此步骤）"""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        kernel32 = ctypes.windll.kernel32
        if not kernel32.AttachConsole(-1):
            kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8")
        sys.stdin = open("CONIN$", "r", encoding="utf-8")
    except Exception:
        pass


def run_agent_cli_mode(instruction=None):
    """以 CLI Agent 模式运行（无 GUI 依赖）"""
    _attach_console()
    from cli_agent import run_agent_cli

    try:
        run_agent_cli(instruction=instruction)
    except Exception as e:
        logger.error(f"Agent CLI 异常退出: {e}", exc_info=True)
    finally:
        sys.exit(0)


def run_login_mode(username: str, password: str | None):
    """以 CLI 模式登录净读 AI"""
    from cli_agent import run_login

    try:
        run_login(username, password)
    except Exception as e:
        logger.error(f"登录异常: {e}", exc_info=True)
    finally:
        sys.exit(0)


def run_gui(argv=None):
    """装配并运行 QML 界面，返回退出码。"""
    from main_qml import main as run_qml

    args = list(sys.argv[1:] if argv is None else argv)
    return int(run_qml(args, run_loop=True))


def main(argv=None):
    """兼容旧入口名：现在等价于 QML 入口。"""
    return run_gui(argv)


if __name__ == "__main__":
    mode, payload = _parse_cli_args()
    if mode == "login" and isinstance(payload, tuple):
        username, password = payload
        run_login_mode(username, password)
    elif mode == "agent":
        run_agent_cli_mode(payload)
    else:
        sys.exit(run_gui())
