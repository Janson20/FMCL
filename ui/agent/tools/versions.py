"""版本管理工具 - 获取/安装/删除/启动 Minecraft 版本"""

import threading
from typing import Dict, Callable
from logzero import logger

from ui.agent.tools.base import ToolInfo, CATEGORY_VERSION


def _build_version_tools() -> list:
    """构建版本管理相关工具列表"""
    return [
        ToolInfo(
            name="get_available_versions",
            display_name="获取可用版本列表",
            description="获取所有可安装的 Minecraft 版本列表（包括正式版和快照版）",
            parameters={"type": "object", "properties": {}, "required": []},
            category=CATEGORY_VERSION,
            execute=_get_available_versions,
            permission_action="get_available_versions",
        ),
        ToolInfo(
            name="get_installed_versions",
            display_name="获取已安装版本列表",
            description="获取本地已安装的 Minecraft 版本列表",
            parameters={"type": "object", "properties": {}, "required": []},
            category=CATEGORY_VERSION,
            execute=_get_installed_versions,
            permission_action="get_installed_versions",
        ),
        ToolInfo(
            name="install_version",
            display_name="安装版本",
            description="安装指定版本的 Minecraft，可选模组加载器（Forge/Fabric/NeoForge）",
            parameters={
                "type": "object",
                "properties": {
                    "version_id": {
                        "type": "string",
                        "description": "Minecraft 版本号，如 1.20.1、1.20.4、26.1",
                    },
                    "mod_loader": {
                        "type": "string",
                        "enum": ["无", "Forge", "Fabric", "NeoForge"],
                        "description": "模组加载器类型，不装则填'无'",
                    },
                },
                "required": ["version_id", "mod_loader"],
            },
            category=CATEGORY_VERSION,
            execute=_install_version,
            permission_action="install_version",
        ),
        ToolInfo(
            name="launch_game",
            display_name="启动游戏",
            description="启动指定版本的 Minecraft 游戏",
            parameters={
                "type": "object",
                "properties": {
                    "version_id": {
                        "type": "string",
                        "description": "要启动的版本ID，如 1.20.1、1.20.1-forge-49.0.26 等",
                    },
                },
                "required": ["version_id"],
            },
            category=CATEGORY_VERSION,
            execute=_launch_game,
            permission_action="launch_game",
        ),
        ToolInfo(
            name="delete_version",
            display_name="删除版本",
            description="删除本地已安装的 Minecraft 客户端版本",
            parameters={
                "type": "object",
                "properties": {
                    "version_id": {
                        "type": "string",
                        "description": "要删除的版本ID，如 1.20.1、1.20.1-forge-49.0.26 等",
                    },
                },
                "required": ["version_id"],
            },
            category=CATEGORY_VERSION,
            execute=_delete_version,
            permission_action="delete_version",
        ),
    ]


def _get_available_versions(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    if "get_available_versions" not in callbacks:
        return "错误: 获取版本列表功能不可用"

    versions = callbacks["get_available_versions"]()
    if not versions:
        return "无法获取版本列表，请检查网络连接"

    releases = [v for v in versions if v.get("type") == "release"]
    snapshots = [v for v in versions if v.get("type") == "snapshot"]

    latest_release = releases[0]["id"] if releases else "无"
    latest_snapshot = snapshots[0]["id"] if snapshots else "无"

    release_ids = [v["id"] for v in releases[:30]]
    snapshot_ids = [v["id"] for v in snapshots[:10]]

    result = f"最新正式版: {latest_release}\n"
    result += f"最新快照版: {latest_snapshot}\n\n"
    result += f"正式版列表 (共{len(releases)}个):\n"
    for rid in release_ids:
        result += f"  - {rid}\n"
    if snapshots:
        result += f"\n快照版列表 (共{len(snapshots)}个):\n"
        for sid in snapshot_ids:
            result += f"  - {sid}\n"

    return result


def _get_installed_versions(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    if "get_installed_versions" not in callbacks:
        return "错误: 获取已安装版本功能不可用"

    versions = callbacks["get_installed_versions"]()
    if not versions:
        return "本地没有任何已安装的版本"

    result = f"本地已安装 {len(versions)} 个版本:\n"
    for v in versions:
        result += f"  - {v}\n"
    return result


def _install_version(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    if "install_version" not in callbacks:
        return "错误: 安装版本功能不可用"

    version_id = params.get("version_id", "").strip()
    mod_loader = params.get("mod_loader", "无").strip()

    if not version_id:
        return "错误: 缺少 version_id 参数"

    try:
        success, installed_id = callbacks["install_version"](version_id, mod_loader)
    except Exception as e:
        logger.error(f"[Agent] install_version 异常: {e}", exc_info=True)
        return f"❌ 安装失败: 版本 {version_id} 安装过程出现异常 ({e})"

    if success:
        msg = f"✅ 安装成功！版本: {installed_id}"
        if mod_loader != "无":
            msg += f" (加载器: {mod_loader})"
        return msg
    else:
        loader_hint = ""
        if mod_loader != "无":
            loader_hint = f"，可能是 {mod_loader} 暂不支持该版本"
        return f"❌ 安装失败: 版本 {version_id} 安装出错{loader_hint}，请检查版本号是否正确"


def _launch_game(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    if "launch_game" not in callbacks:
        return "错误: 启动游戏功能不可用"

    version_id = params.get("version_id", "").strip()
    if not version_id:
        return "错误: 缺少 version_id 参数"

    try:
        success, target = callbacks["launch_game"](version_id)
    except Exception as e:
        logger.error(f"[Agent] launch_game 异常: {e}", exc_info=True)
        return f"❌ 启动失败: {e}"

    if success:
        proc = callbacks.get("get_game_process", lambda: None)()
        if proc and proc.stdout:
            def _drain_pipe():
                try:
                    for _ in proc.stdout:
                        pass
                except Exception:
                    pass
            threading.Thread(target=_drain_pipe, daemon=True, name="AgentStdoutDrain").start()
        return f"🚀 游戏已启动！版本: {target}"
    else:
        return f"❌ 启动失败: 版本 {version_id} 启动出错"


def _delete_version(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    if "remove_version" not in callbacks:
        return "错误: 删除版本功能不可用"

    version_id = params.get("version_id", "").strip()
    if not version_id:
        return "错误: 缺少 version_id 参数"

    try:
        success, result_id = callbacks["remove_version"](version_id)
    except Exception as e:
        logger.error(f"[Agent] delete_version 异常: {e}", exc_info=True)
        return f"❌ 删除失败: 版本 {version_id} 删除过程出现异常 ({e})"

    if success:
        return f"✅ 版本 {result_id} 已成功删除"
    else:
        return f"❌ 删除失败: 版本 {version_id} 可能未安装或删除时出错"
