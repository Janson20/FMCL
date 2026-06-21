"""模组管理工具 - 搜索/安装 Modrinth 模组"""

import os
from pathlib import Path
from typing import Dict, Callable
from logzero import logger

from ui.agent.tools.base import ToolInfo, CATEGORY_MOD


def _build_mod_tools() -> list:
    return [
        ToolInfo(
            name="search_mods",
            display_name="搜索模组",
            description="在 Modrinth 上搜索模组。不填 query 时返回默认热门模组",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如 sodium、jei 等。留空则返回热门模组",
                    },
                    "game_version": {
                        "type": "string",
                        "description": "Minecraft 版本号，如 1.20.1",
                    },
                    "mod_loader": {
                        "type": "string",
                        "enum": ["fabric", "forge", "neoforge"],
                        "description": "模组加载器",
                    },
                },
                "required": [],
            },
            category=CATEGORY_MOD,
            execute=_search_mods,
            permission_action="search_mods",
        ),
        ToolInfo(
            name="install_mod",
            display_name="安装模组",
            description="从 Modrinth 安装模组到指定 Minecraft 版本。需要先通过 get_installed_versions 确认目标版本已安装",
            parameters={
                "type": "object",
                "properties": {
                    "version_id": {
                        "type": "string",
                        "description": "Minecraft 版本号，如 1.20.1、26.1.2（不要传入 fabric-loader-xxx 这样的完整版本ID）",
                    },
                    "mod_loader": {
                        "type": "string",
                        "enum": ["fabric", "forge", "neoforge"],
                        "description": "模组加载器",
                    },
                    "mod_name": {
                        "type": "string",
                        "description": "模组名称，如 Sodium、JEI 等",
                    },
                    "mod_project_id": {
                        "type": "string",
                        "description": "Modrinth 项目ID（如果已知）",
                    },
                },
                "required": ["version_id", "mod_loader", "mod_name"],
            },
            category=CATEGORY_MOD,
            execute=_install_mod,
            permission_action="install_mod",
        ),
    ]


def _search_mods(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    try:
        from modrinth import search_mods as modrinth_search

        query = params.get("query", "").strip()
        game_version = params.get("game_version", "").strip() or None
        mod_loader = params.get("mod_loader", "").strip() or None

        result = modrinth_search(
            query=query,
            game_version=game_version,
            mod_loader=mod_loader,
            limit=10,
        )

        hits = result.get("hits", [])
        if not hits:
            return f"未找到与 '{query}' 相关的模组"

        output = f"找到 {result.get('total_hits', len(hits))} 个模组:\n"
        for i, mod in enumerate(hits, 1):
            title = mod.get("title", "未知")
            project_id = mod.get("project_id", "未知")
            author = mod.get("author", "未知")
            description = mod.get("description", "")
            if len(description) > 100:
                description = description[:100] + "..."
            versions = mod.get("versions", [])
            version_str = f" (支持 {len(versions)} 个版本)" if versions else ""
            output += (
                f"\n{i}. {title}\n"
                f"   项目ID: {project_id}\n"
                f"   作者: {author}\n"
                f"   简介: {description}\n"
                f"   版本: {version_str}\n"
            )

        return output
    except Exception as e:
        logger.error(f"搜索模组失败: {e}")
        return f"❌ 搜索模组失败: {str(e)}"


def _install_mod(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    try:
        from modrinth import install_mod_with_deps, search_mods as modrinth_search

        version_id = params.get("version_id", "").strip()
        mod_loader = params.get("mod_loader", "").strip().lower()
        mod_name = params.get("mod_name", "").strip()
        mod_project_id = params.get("mod_project_id", "").strip()

        if not version_id or not mod_name or not mod_loader:
            return "错误: 缺少必要参数 (version_id, mod_loader, mod_name)"

        if "get_installed_versions" not in callbacks or "get_minecraft_dir" not in callbacks:
            return "错误: 无法获取游戏信息"

        installed = callbacks["get_installed_versions"]()

        target_full_version = None
        for v in installed:
            if v == version_id or v.startswith(version_id + "-") or v.endswith("-" + version_id):
                if mod_loader in v.lower():
                    target_full_version = v
                    break

        if not target_full_version:
            for v in installed:
                if v == version_id or v.startswith(version_id + "-") or v.endswith("-" + version_id):
                    target_full_version = v
                    break

        if not target_full_version:
            return f"错误: 版本 '{version_id}' 未安装，请先安装该版本"

        mc_dir = callbacks["get_minecraft_dir"]()
        game_dir = Path(mc_dir)

        if "-" in target_full_version:
            mods_dir = str(game_dir / "versions" / target_full_version / "mods")
        else:
            mods_dir = str(game_dir / "mods")

        os.makedirs(mods_dir, exist_ok=True)

        if not mod_project_id:
            search_result = modrinth_search(
                query=mod_name,
                game_version=version_id,
                mod_loader=mod_loader,
                limit=5,
            )
            hits = search_result.get("hits", [])
            if not hits:
                return f"未找到模组 '{mod_name}'"

            mod_project_id = hits[0].get("project_id", "")
            if not mod_project_id:
                return f"无法获取模组 '{mod_name}' 的项目ID"

        ok, msg, names = install_mod_with_deps(
            project_id=mod_project_id,
            game_version=version_id,
            mod_loader=mod_loader,
            mods_dir=mods_dir,
            status_callback=lambda s: None,
        )

        if ok:
            return f"✅ 模组安装成功: {', '.join(names)}"
        else:
            return f"❌ 模组安装失败: {msg}"
    except Exception as e:
        logger.error(f"安装模组失败: {e}")
        return f"❌ 模组安装失败: {str(e)}"
