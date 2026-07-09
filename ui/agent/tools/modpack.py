"""整合包管理工具 - 搜索/下载/安装/开服 .mrpack 整合包"""

import os
from typing import Callable, Dict

from logzero import logger

from ui.agent.tools.base import CATEGORY_MODPACK, ToolInfo


def _build_modpack_tools() -> list:
    return [
        ToolInfo(
            name="search_modpack",
            display_name="搜索整合包",
            description="在 Modrinth 上搜索整合包。不填 query 时返回默认热门整合包",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如 skyblock、RLCraft 等。留空则返回热门整合包",
                    },
                    "game_version": {"type": "string", "description": "Minecraft 版本号，如 1.20.1"},
                },
                "required": [],
            },
            category=CATEGORY_MODPACK,
            execute=_search_modpack,
            permission_action="search_modpack",
        ),
        ToolInfo(
            name="download_modpack",
            display_name="下载整合包",
            description="从 Modrinth 下载整合包 .mrpack 文件，返回下载完成的文件路径。需要先从 search_modpack 获取 project_id",
            parameters={
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "Modrinth 整合包项目ID（从 search_modpack 结果中获得）",
                    },
                    "game_version": {"type": "string", "description": "Minecraft 版本号筛选，如 1.20.1"},
                },
                "required": ["project_id"],
            },
            category=CATEGORY_MODPACK,
            execute=_download_modpack,
            permission_action="download_modpack",
        ),
        ToolInfo(
            name="install_modpack",
            display_name="安装整合包",
            description="安装 .mrpack 整合包文件",
            parameters={
                "type": "object",
                "properties": {"mrpack_path": {"type": "string", "description": ".mrpack 整合包文件的绝对路径"}},
                "required": ["mrpack_path"],
            },
            category=CATEGORY_MODPACK,
            execute=_install_modpack,
            permission_action="install_modpack",
        ),
        ToolInfo(
            name="install_modpack_server",
            display_name="整合包开服",
            description="安装 .mrpack 整合包作为服务器",
            parameters={
                "type": "object",
                "properties": {"mrpack_path": {"type": "string", "description": ".mrpack 整合包文件的绝对路径"}},
                "required": ["mrpack_path"],
            },
            category=CATEGORY_MODPACK,
            execute=_install_modpack_server,
            permission_action="install_modpack_server",
        ),
    ]


def _search_modpack(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    try:
        from modrinth import compress_game_versions
        from modrinth import search_modpacks as modrinth_search

        query = params.get("query", "").strip()
        game_version = params.get("game_version", "").strip() or None

        result = modrinth_search(query=query, game_version=game_version, limit=10)

        hits = result.get("hits", [])
        if not hits:
            return f"未找到与 '{query}' 相关的整合包"

        output = f"找到 {result.get('total_hits', len(hits))} 个整合包:\n"
        for i, mp in enumerate(hits, 1):
            title = mp.get("title", "未知")
            project_id = mp.get("project_id", "未知")
            author = mp.get("author", "未知")
            description = mp.get("description", "")
            if len(description) > 120:
                description = description[:120] + "..."
            downloads = mp.get("downloads", 0)
            versions = mp.get("versions", [])
            version_str = compress_game_versions(versions) if versions else "未知"
            output += (
                f"\n{i}. {title}\n"
                f"   项目ID: {project_id}\n"
                f"   作者: {author}\n"
                f"   下载量: {downloads}\n"
                f"   版本: {version_str}\n"
                f"   简介: {description}\n"
            )
        return output
    except Exception as e:
        logger.error(f"搜索整合包失败: {e}")
        return f"❌ 搜索整合包失败: {str(e)}"


def _download_modpack(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    try:
        from modrinth import download_modpack_file

        project_id = params.get("project_id", "").strip()
        game_version = params.get("game_version", "").strip() or None

        if not project_id:
            return "错误: 缺少 project_id 参数"

        success, result = download_modpack_file(project_id, game_version=game_version)

        if success:
            filename = os.path.basename(result)
            return f"✅ 整合包下载成功！\n文件名: {filename}\n路径: {result}"
        else:
            return f"❌ 整合包下载失败: {result}"
    except Exception as e:
        logger.error(f"下载整合包失败: {e}")
        return f"❌ 下载整合包失败: {str(e)}"


def _install_modpack(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    if "get_mrpack_information" not in callbacks or "install_mrpack" not in callbacks:
        return "错误: 整合包安装功能不可用"

    mrpack_path = params.get("mrpack_path", "").strip()
    if not mrpack_path:
        return "错误: 缺少 mrpack_path 参数（.mrpack 文件的绝对路径）"

    if not os.path.isfile(mrpack_path):
        return f"错误: 文件不存在: {mrpack_path}"

    try:
        info = callbacks["get_mrpack_information"](mrpack_path)
    except Exception as e:
        return f"❌ 无法读取整合包信息: {e}"

    pack_name = info.get("name", "未知")
    mc_version = info.get("minecraftVersion", "未知")

    try:
        success, result = callbacks["install_mrpack"](mrpack_path)
    except Exception as e:
        logger.error(f"[Agent] install_modpack 异常: {e}", exc_info=True)
        return f"❌ 整合包安装失败: {e}"

    if success:
        return f"✅ 整合包安装成功！\n名称: {pack_name}\nMinecraft 版本: {mc_version}\n启动版本: {result}"
    else:
        return f"❌ 整合包安装失败: {result}"


def _install_modpack_server(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    if "install_mrpack_server" not in callbacks or "get_mrpack_information" not in callbacks:
        return "错误: 整合包开服功能不可用"

    mrpack_path = params.get("mrpack_path", "").strip()
    if not mrpack_path:
        return "错误: 缺少 mrpack_path 参数（.mrpack 文件的绝对路径）"

    if not os.path.isfile(mrpack_path):
        return f"错误: 文件不存在: {mrpack_path}"

    try:
        info = callbacks["get_mrpack_information"](mrpack_path)
    except Exception as e:
        return f"❌ 无法读取整合包信息: {e}"

    pack_name = info.get("name", "未知")
    mc_version = info.get("minecraftVersion", "未知")

    try:
        success, server_name = callbacks["install_mrpack_server"](mrpack_path)
    except Exception as e:
        logger.error(f"[Agent] install_modpack_server 异常: {e}", exc_info=True)
        return f"❌ 整合包服务器安装失败: {e}"

    if success:
        return f"✅ 整合包服务器安装成功！\n名称: {pack_name}\nMinecraft 版本: {mc_version}\n服务器名: {server_name}"
    else:
        return f"❌ 整合包服务器安装失败: {server_name}"
