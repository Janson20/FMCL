"""资源工具 - 资源包和光影搜索/安装"""

import os
from pathlib import Path
from typing import Dict, Callable
from logzero import logger

from ui.agent.tools.base import ToolInfo, CATEGORY_RESOURCE


def _build_resource_tools() -> list:
    return [
        ToolInfo(
            name="search_resource_packs",
            display_name="搜索资源包",
            description="在 Modrinth 上搜索资源包。不填 query 时返回默认热门资源包",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如 Faithful、Default 等。留空则返回默认热门资源包",
                    },
                    "game_version": {
                        "type": "string",
                        "description": "Minecraft 版本号，如 1.20.1",
                    },
                },
                "required": [],
            },
            category=CATEGORY_RESOURCE,
            execute=_search_resource_packs,
            permission_action="search_resource_packs",
        ),
        ToolInfo(
            name="install_resource_pack",
            display_name="安装资源包",
            description="从 Modrinth 安装资源包到指定 Minecraft 版本的 resourcepacks 目录。需要先通过 get_installed_versions 确认目标版本已安装",
            parameters={
                "type": "object",
                "properties": {
                    "version_id": {
                        "type": "string",
                        "description": "Minecraft 版本文件夹/实例名称，如 1.20.1、1.20.1-forge-49.0.26",
                    },
                    "pack_name": {
                        "type": "string",
                        "description": "资源包名称，如 Faithful、Default 3D 等",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Modrinth 项目ID（如果已知）",
                    },
                },
                "required": ["version_id", "pack_name"],
            },
            category=CATEGORY_RESOURCE,
            execute=_install_resource_pack,
            permission_action="install_resource_pack",
        ),
        ToolInfo(
            name="search_shaders",
            display_name="搜索光影",
            description="在 Modrinth 上搜索光影。不填 query 时返回默认热门光影",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如 BSL、Complementary 等。留空则返回热门光影",
                    },
                    "game_version": {
                        "type": "string",
                        "description": "Minecraft 版本号，如 1.20.1",
                    },
                },
                "required": [],
            },
            category=CATEGORY_RESOURCE,
            execute=_search_shaders,
            permission_action="search_shaders",
        ),
        ToolInfo(
            name="install_shader",
            display_name="安装光影",
            description="从 Modrinth 安装光影到指定 Minecraft 版本的 shaderpacks 目录。需要先通过 get_installed_versions 确认目标版本已安装",
            parameters={
                "type": "object",
                "properties": {
                    "version_id": {
                        "type": "string",
                        "description": "Minecraft 版本文件夹/实例名称，如 1.20.1、1.20.1-forge-49.0.26",
                    },
                    "shader_name": {
                        "type": "string",
                        "description": "光影名称，如 BSL、Complementary Shaders 等",
                    },
                    "project_id": {
                        "type": "string",
                        "description": "Modrinth 项目ID（如果已知）",
                    },
                },
                "required": ["version_id", "shader_name"],
            },
            category=CATEGORY_RESOURCE,
            execute=_install_shader,
            permission_action="install_shader",
        ),
    ]


def _search_resource_packs(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    try:
        from modrinth import search_resource_packs as modrinth_search

        query = params.get("query", "").strip()
        game_version = params.get("game_version", "").strip() or None

        result = modrinth_search(query=query, game_version=game_version, limit=10)

        hits = result.get("hits", [])
        if not hits:
            return f"未找到与 '{query}' 相关的资源包"

        output = f"找到 {result.get('total_hits', len(hits))} 个资源包:\n"
        for i, rp in enumerate(hits, 1):
            title = rp.get("title", "未知")
            project_id = rp.get("project_id", "未知")
            author = rp.get("author", "未知")
            description = rp.get("description", "")
            if len(description) > 100:
                description = description[:100] + "..."
            downloads = rp.get("downloads", 0)
            output += (
                f"\n{i}. {title}\n"
                f"   项目ID: {project_id}\n"
                f"   作者: {author}\n"
                f"   下载量: {downloads}\n"
                f"   简介: {description}\n"
            )
        return output
    except Exception as e:
        logger.error(f"搜索资源包失败: {e}")
        return f"❌ 搜索资源包失败: {str(e)}"


def _install_resource_pack(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    try:
        from modrinth import install_resource_pack, search_resource_packs as modrinth_search

        version_id = params.get("version_id", "").strip()
        pack_name = params.get("pack_name", "").strip()
        project_id = params.get("project_id", "").strip()

        if not version_id or not pack_name:
            return "错误: 缺少必要参数 (version_id, pack_name)"

        if "get_installed_versions" not in callbacks or "get_minecraft_dir" not in callbacks:
            return "错误: 无法获取游戏信息"

        installed = callbacks["get_installed_versions"]()
        if version_id not in installed:
            return f"错误: 版本 '{version_id}' 未安装。当前已安装: {', '.join(installed) if installed else '无'}"

        mc_dir = callbacks["get_minecraft_dir"]()
        game_dir = Path(mc_dir)

        if "-" in version_id:
            rp_dir = str(game_dir / "versions" / version_id / "resourcepacks")
        else:
            rp_dir = str(game_dir / "resourcepacks")

        os.makedirs(rp_dir, exist_ok=True)

        if not project_id:
            search_result = modrinth_search(query=pack_name, limit=5)
            hits = search_result.get("hits", [])
            if not hits:
                return f"未找到资源包 '{pack_name}'"
            project_id = hits[0].get("project_id", "")
            if not project_id:
                return f"无法获取资源包 '{pack_name}' 的项目ID"

        ok, result = install_resource_pack(
            project_id=project_id,
            game_version=version_id.split("-")[0],
            resourcepacks_dir=rp_dir,
            status_callback=lambda s: None,
        )

        if ok:
            return f"✅ 资源包安装成功: {pack_name} -> {rp_dir}"
        else:
            return f"❌ 资源包安装失败: {result}"
    except Exception as e:
        logger.error(f"安装资源包失败: {e}")
        return f"❌ 安装资源包失败: {str(e)}"


def _search_shaders(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    try:
        from modrinth import search_shaders as modrinth_search

        query = params.get("query", "").strip()
        game_version = params.get("game_version", "").strip() or None

        result = modrinth_search(query=query, game_version=game_version, limit=10)

        hits = result.get("hits", [])
        if not hits:
            return f"未找到与 '{query}' 相关的光影"

        output = f"找到 {result.get('total_hits', len(hits))} 个光影:\n"
        for i, sd in enumerate(hits, 1):
            title = sd.get("title", "未知")
            project_id = sd.get("project_id", "未知")
            author = sd.get("author", "未知")
            description = sd.get("description", "")
            if len(description) > 100:
                description = description[:100] + "..."
            downloads = sd.get("downloads", 0)
            output += (
                f"\n{i}. {title}\n"
                f"   项目ID: {project_id}\n"
                f"   作者: {author}\n"
                f"   下载量: {downloads}\n"
                f"   简介: {description}\n"
            )
        return output
    except Exception as e:
        logger.error(f"搜索光影失败: {e}")
        return f"❌ 搜索光影失败: {str(e)}"


def _install_shader(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    try:
        from modrinth import install_shader, search_shaders as modrinth_search

        version_id = params.get("version_id", "").strip()
        shader_name = params.get("shader_name", "").strip()
        project_id = params.get("project_id", "").strip()

        if not version_id or not shader_name:
            return "错误: 缺少必要参数 (version_id, shader_name)"

        if "get_installed_versions" not in callbacks or "get_minecraft_dir" not in callbacks:
            return "错误: 无法获取游戏信息"

        installed = callbacks["get_installed_versions"]()
        if version_id not in installed:
            return f"错误: 版本 '{version_id}' 未安装。当前已安装: {', '.join(installed) if installed else '无'}"

        mc_dir = callbacks["get_minecraft_dir"]()
        game_dir = Path(mc_dir)

        if "-" in version_id:
            sd_dir = str(game_dir / "versions" / version_id / "shaderpacks")
        else:
            sd_dir = str(game_dir / "shaderpacks")

        os.makedirs(sd_dir, exist_ok=True)

        if not project_id:
            search_result = modrinth_search(query=shader_name, limit=5)
            hits = search_result.get("hits", [])
            if not hits:
                return f"未找到光影 '{shader_name}'"
            project_id = hits[0].get("project_id", "")
            if not project_id:
                return f"无法获取光影 '{shader_name}' 的项目ID"

        ok, result = install_shader(
            project_id=project_id,
            game_version=version_id.split("-")[0],
            shaderpacks_dir=sd_dir,
            status_callback=lambda s: None,
        )

        if ok:
            return f"✅ 光影安装成功: {shader_name} -> {sd_dir}"
        else:
            return f"❌ 光影安装失败: {result}"
    except Exception as e:
        logger.error(f"安装光影失败: {e}")
        return f"❌ 安装光影失败: {str(e)}"
