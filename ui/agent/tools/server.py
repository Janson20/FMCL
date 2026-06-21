"""服务器管理工具 - 获取/安装/启动/删除 Minecraft 服务器"""

import threading
from typing import Dict, Callable
from logzero import logger

from ui.agent.tools.base import ToolInfo, CATEGORY_SERVER


def _build_server_tools() -> list:
    return [
        ToolInfo(
            name="get_installed_servers",
            display_name="获取已安装服务器列表",
            description="获取本地已安装的服务器版本列表",
            parameters={"type": "object", "properties": {}, "required": []},
            category=CATEGORY_SERVER,
            execute=_get_installed_servers,
            permission_action="get_installed_servers",
        ),
        ToolInfo(
            name="start_server",
            display_name="启动服务器",
            description="启动指定版本的 Minecraft 服务器",
            parameters={
                "type": "object",
                "properties": {
                    "version_id": {
                        "type": "string",
                        "description": "服务器版本号，如 1.21.4 或 1.20.1-forge-xxx 等",
                    },
                    "max_memory": {
                        "type": "string",
                        "description": "最大内存，如 2G、4G、8G，默认 2G",
                    },
                },
                "required": ["version_id"],
            },
            category=CATEGORY_SERVER,
            execute=_start_server,
            permission_action="start_server",
        ),
        ToolInfo(
            name="delete_server_version",
            display_name="删除服务器版本",
            description="删除本地已安装的 Minecraft 服务器版本",
            parameters={
                "type": "object",
                "properties": {
                    "version_id": {
                        "type": "string",
                        "description": "要删除的服务器版本号，如 1.21.4",
                    },
                },
                "required": ["version_id"],
            },
            category=CATEGORY_SERVER,
            execute=_delete_server_version,
            permission_action="delete_server_version",
        ),
    ]


def _get_installed_servers(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    if "get_installed_servers" not in callbacks:
        return "错误: 获取已安装服务器功能不可用"

    servers = callbacks["get_installed_servers"]()
    if not servers:
        return "本地没有任何已安装的服务器"

    result = f"本地已安装 {len(servers)} 个服务器:\n"
    for s in servers:
        result += f"  - {s}\n"
    return result


def _start_server(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    if "start_server" not in callbacks:
        return "错误: 启动服务器功能不可用"

    version_id = params.get("version_id", "").strip()
    max_memory = params.get("max_memory", "2G").strip()

    if not version_id:
        return "错误: 缺少 version_id 参数"

    try:
        success, process = callbacks["start_server"](version_id, max_memory)
    except Exception as e:
        logger.error(f"[Agent] start_server 异常: {e}", exc_info=True)
        return f"❌ 服务器启动失败: {e}"

    if success:
        if process and process.stdout:
            def _drain_pipe():
                try:
                    for _ in process.stdout:
                        pass
                except Exception:
                    pass
            threading.Thread(target=_drain_pipe, daemon=True, name="AgentServerStdoutDrain").start()
        return f"🚀 服务器 {version_id} 已启动！内存: {max_memory}，端口: 25565"
    else:
        return f"❌ 服务器启动失败: 版本 {version_id} 可能未安装或启动时出错"


def _delete_server_version(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    if "remove_server" not in callbacks:
        return "错误: 删除服务器版本功能不可用"

    version_id = params.get("version_id", "").strip()
    if not version_id:
        return "错误: 缺少 version_id 参数"

    try:
        success, result_id = callbacks["remove_server"](version_id)
    except Exception as e:
        logger.error(f"[Agent] delete_server_version 异常: {e}", exc_info=True)
        return f"❌ 删除服务器失败: {version_id} 删除过程出现异常 ({e})"

    if success:
        return f"✅ 服务器版本 {result_id} 已成功删除"
    else:
        return f"❌ 删除服务器失败: 版本 {version_id} 可能未安装或删除时出错"
