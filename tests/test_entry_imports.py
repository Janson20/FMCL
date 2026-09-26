"""入口模块必须永远可导入（D-106 的永久守卫）。

背景：``cli_agent.py`` 曾经写着 ``from ui.agent.tools.system import ASK_USER_MARKER, ...``，
而 ``ASK_USER_MARKER`` 一直定义在 ``tools/user.py``。这是**从错误的模块导入一个名字**，
静态看不出来、单元测试也覆盖不到（没人 import 它），结果是
``cli_agent.py`` / ``agent_cli.py`` 完全无法导入，``main.py`` 的 ``-agent`` 交互模式
与 ``login`` 子命令整条不可用 —— 一个 100% 坏掉的功能，静默存在了很久。

这类缺陷的通解是「把入口真的导入一遍」。这里用**一个子进程**把清单里的模块全部导入，
收集失败项后一次性断言，既覆盖完整又不至于为每个模块起一次解释器。

为什么放子进程：这些模块在导入期会读配置、加载密钥文件、注册日志 handler，
在 pytest 进程里导入会污染其他测试；子进程能拿到干净的失败信息（真实异常类型）。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 无界面环境下必须可导入的模块。新增根模块 / 服务模块时请追加到这里。
HEADLESS_IMPORTABLE = (
    # CLI 与入口（D-106 的实际受害者）
    "cli_agent",
    "agent_cli",
    "main",
    # 根业务模块
    "config",
    "mirror",
    "downloader",
    "download_config",
    "modrinth",
    "curseforge",
    "updater",
    "validation",
    "version_utils",
    "secure_storage",
    "structured_logger",
    "backup_manager",
    "achievement_defs",
    "achievement_engine",
    "achievement_sync",
    "plugin_manager",
    # 服务层
    "services",
    "services.base",
    "services.errors",
    "services.monitor_service",
    "services.crash_service",
    "services.voice_service",
    "services.tool_service",
    "services.server_service",
    "services.online_service",
    "services.agent_service",
    "services.music_effects",
    "services.music_lyrics",
    "services.music_playlist",
    "services.music_risk_captcha",
    "services.music_source",
    "services.theme_service",
    "services.i18n_service",
    "services.user_agent",
    "services.palette",
    # app 层
    "app.context",
    "app.tasks",
    "app.ports",
    "app.events",
    # launcher
    "launcher",
    "launcher.core",
    "launcher.account",
    "launcher.predownload",
)

_CHILD = """
import importlib, sys
failed = {}
for name in sys.argv[1:]:
    try:
        importlib.import_module(name)
    except BaseException as exc:      # noqa: BLE001 - 任何异常都算失败
        failed[name] = type(exc).__name__ + ": " + str(exc)
if failed:
    for name, err in failed.items():
        print("FAIL " + name + " -> " + err)
    sys.exit(1)
print("IMPORT-OK " + str(len(sys.argv) - 1))
"""


@pytest.fixture(scope="module")
def import_report() -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", _CHILD, *HEADLESS_IMPORTABLE],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def test_all_entry_modules_import(import_report: subprocess.CompletedProcess) -> None:
    """清单里每个模块都必须能在无界面子进程里导入成功。"""
    assert import_report.returncode == 0, (
        "以下模块导入失败（子进程 stderr 尾部）：\n"
        f"{import_report.stdout}\n{import_report.stderr[-2000:]}"
    )
    assert f"IMPORT-OK {len(HEADLESS_IMPORTABLE)}" in import_report.stdout


def test_cli_agent_imports_the_markers_from_their_real_home() -> None:
    """D-106 定点回归：三个名字必须来自真正定义它们的模块。

    只断言"能导入"是不够的 —— 若有人把 ``ASK_USER_MARKER`` 从 ``tools/system.py``
    改成 re-export，导入仍然成功但这个测试会提醒他：该名字的**归属**是 ``tools/user.py``。
    """
    prog = (
        "import cli_agent, services.agent.tools.system as system, "
        "services.agent.tools.user as user;"
        "assert cli_agent.ASK_USER_MARKER is user.ASK_USER_MARKER;"
        "assert cli_agent.DANGEROUS_MARKER is system.DANGEROUS_MARKER;"
        "assert cli_agent.execute_dangerous_command is system.execute_dangerous_command;"
        "print('MARKERS-OK')"
    )
    r = subprocess.run(
        [sys.executable, "-c", prog],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert r.returncode == 0 and "MARKERS-OK" in r.stdout, r.stderr[-2000:]


def test_agent_cli_entrypoint_is_reachable() -> None:
    """``agent_cli.py`` 必须能拿到 ``run_agent_cli``（它是 ``main.py`` 的另一条入口）。"""
    prog = "import agent_cli; assert callable(agent_cli.run_agent_cli); print('ENTRY-OK')"
    r = subprocess.run(
        [sys.executable, "-c", prog],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert r.returncode == 0 and "ENTRY-OK" in r.stdout, r.stderr[-2000:]
