"""系统工具 - 版本资源列表、终端命令执行、启动器路径"""

import json
import subprocess
import threading
import os
from pathlib import Path
from typing import Dict, Callable, Optional
from logzero import logger

from ui.agent.tools.base import ToolInfo, CATEGORY_SYSTEM

# 高危命令前缀列表
DANGEROUS_PREFIXES = [
    "rm -rf ", "rm -fr ", "rm --no-preserve-root ",
    "del /s ", "del /q ", "del /f ", "rd /s ", "rd /q ",
    "format ", "diskpart",
    "mv /dev/null ",
    "dd if=/dev/zero ", "dd of=/dev/sda",
    "mkfs.", "fdisk ", "parted ", "gdisk ",
    "shutdown ", "poweroff ", "halt ",
    "shred ", "wipefs ", "blkdiscard ",
    "cryptsetup luksFormat ",
    ":(){ ", ":(){",
    "curl | bash", "wget -O- | sh",
    "sudo rm ", "sudo dd ", "sudo chmod ",
    "chmod -R 777 ", "chmod -R 000 ", "chown -R /",
    "iptables -F", "iptables -P DROP", "service iptables stop",
    "DROP TABLE ", "DROP DATABASE ", "TRUNCATE TABLE ", "DELETE FROM ",
    'psql -c "DROP ', 'mysql -e "DROP ',
    "redis-cli FLUSHALL", "mongorestore --drop",
    "git push --force", "git reset --hard",
    "kubectl delete namespace ",
    "terraform destroy", "aws s3 sync --delete",
    "docker run --privileged ", "docker run -v /:/host",
    "docker system prune -a --volumes",
    "systemctl disable ", "crontab -r", "killall -9 ",
    "mount ", "chattr +i ",
    "$(rm ", "`rm ",
    'bash -c "rm ', 'sh -c "rm ',
    'cmd /c "del ', 'powershell -Command "Remove-Item',
]

DANGEROUS_MARKER = "__DANGEROUS__"


def _build_system_tools() -> list:
    return [
        ToolInfo(
            name="list_version_resources",
            display_name="列出版本资源",
            description="获取指定 Minecraft 版本文件夹下的资源列表（模组/资源包/光影/地图）",
            parameters={
                "type": "object",
                "properties": {
                    "version_id": {
                        "type": "string",
                        "description": "Minecraft 版本文件夹/实例名称，如 1.20.1、1.20.1-forge-49.0.26",
                    },
                    "resource_type": {
                        "type": "string",
                        "enum": ["mods", "resourcepacks", "shaderpacks", "saves"],
                        "description": "资源类型：mods=模组, resourcepacks=资源包, shaderpacks=光影, saves=地图",
                    },
                },
                "required": ["version_id", "resource_type"],
            },
            category=CATEGORY_SYSTEM,
            execute=_list_version_resources,
            permission_action="list_version_resources",
        ),
        ToolInfo(
            name="exec_command",
            display_name="执行终端命令",
            description="在指定路径下执行终端命令。高危命令（如 rm -rf、dd、shutdown、DROP TABLE 等）需要用户手动确认后才会执行",
            parameters={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "执行命令的工作目录（绝对路径），不填则在启动器所在目录执行",
                    },
                    "command": {
                        "type": "string",
                        "description": "要执行的命令",
                    },
                },
                "required": ["command"],
            },
            category=CATEGORY_SYSTEM,
            execute=_exec_command,
            permission_action="exec_command",
        ),
        ToolInfo(
            name="get_launcher_path",
            display_name="获取启动器路径",
            description="获取启动器所在的目录路径",
            parameters={"type": "object", "properties": {}, "required": []},
            category=CATEGORY_SYSTEM,
            execute=_get_launcher_path,
            permission_action="get_launcher_path",
        ),
    ]


def _list_version_resources(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    resource_type = params.get("resource_type", "").strip()
    version_id = params.get("version_id", "").strip()

    if not version_id or not resource_type:
        return "错误: 缺少必要参数 (version_id, resource_type)"

    valid_types = {"mods", "resourcepacks", "shaderpacks", "saves"}
    if resource_type not in valid_types:
        return f"错误: resource_type 必须是 {', '.join(sorted(valid_types))} 之一"

    if "get_installed_versions" not in callbacks or "get_minecraft_dir" not in callbacks:
        return "错误: 无法获取游戏信息"

    installed = callbacks["get_installed_versions"]()
    if version_id not in installed:
        return f"错误: 版本 '{version_id}' 未安装。当前已安装: {', '.join(installed) if installed else '无'}"

    mc_dir = callbacks["get_minecraft_dir"]()
    game_dir = Path(mc_dir)

    if "-" in version_id:
        target_dir = game_dir / "versions" / version_id / resource_type
    else:
        target_dir = game_dir / resource_type

    type_labels = {"mods": "模组", "resourcepacks": "资源包", "shaderpacks": "光影", "saves": "地图"}
    label = type_labels.get(resource_type, resource_type)

    if not target_dir.exists():
        return f"版本 {version_id} 的{label}目录不存在: {target_dir}"

    try:
        items = sorted(os.listdir(str(target_dir)))
    except Exception as e:
        logger.error(f"列出资源目录失败: {e}")
        return f"❌ 无法读取目录: {target_dir}"

    if not items:
        return f"版本 {version_id} 的{label}目录为空"

    result = f"版本 {version_id} 的{label}列表 (共 {len(items)} 个):\n"
    result += f"目录: {target_dir}\n"
    for i, item in enumerate(items, 1):
        full_path = target_dir / item
        if full_path.is_file():
            size = full_path.stat().st_size
            if size >= 1048576:
                size_str = f"{size / 1048576:.1f} MB"
            elif size >= 1024:
                size_str = f"{size / 1024:.1f} KB"
            else:
                size_str = f"{size} B"
            result += f"  {i}. 📄 {item} ({size_str})\n"
        else:
            result += f"  {i}. 📁 {item}/\n"

    return result


def _is_dangerous_command(command: str) -> Optional[str]:
    for prefix in DANGEROUS_PREFIXES:
        if command.startswith(prefix):
            return prefix
    return None


def _exec_command(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    path = params.get("path", "").strip()
    command = params.get("command", "").strip()

    if not path:
        path = os.getcwd()
    if not command:
        return "错误: 缺少 command 参数"

    if not os.path.isdir(path):
        return f"错误: 路径不存在或不是目录: {path}"

    dangerous_prefix = _is_dangerous_command(command)
    if dangerous_prefix:
        logger.warning(f"[Agent] 检测到高危命令: '{command}' (匹配前缀: '{dangerous_prefix}')")
        payload = json.dumps({"path": path, "command": command}, ensure_ascii=False)
        return f"{DANGEROUS_MARKER}|{payload}"

    return _run_command(path, command)


def execute_dangerous_command(path: str, command: str) -> str:
    return _run_command(path, command)


def _run_command(path: str, command: str) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=path,
            capture_output=True,
            text=True,
            timeout=300,
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            if output:
                output += "\n"
            output += f"[stderr]\n{result.stderr}"
        if not output:
            output = f"(退出码: {result.returncode})"

        status = "✅ 命令执行成功" if result.returncode == 0 else f"⚠️ 命令执行完成 (退出码: {result.returncode})"
        return f"{status}\n路径: {path}\n命令: {command}\n\n输出:\n{output[:4000]}"
    except subprocess.TimeoutExpired:
        return f"⚠️ 命令执行超时 (超过300秒)\n路径: {path}\n命令: {command}"
    except Exception as e:
        logger.error(f"[Agent] exec_command 异常: {e}", exc_info=True)
        return f"❌ 命令执行失败: {e}\n路径: {path}\n命令: {command}"


def _get_launcher_path(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    return f"启动器所在路径: {os.getcwd()}"
