"""MC 相关网站目录。

本模块只保留**键名与 URL**；名称、描述、标签的四种语言译文在
`ui/locales/*.json`（`links_tag_*` / `links_site_*_name` /
`links_site_*_desc`），页面用 `Tr.map[key]` 取当前语言。

部分站点会按网络或地区重定向，不能仅凭一次访问结果判断链接失效；URL 以站点
官方地址为准维护。

页面挂在「工具箱 → 相关网站」；桥是 `LinksBridge`。
服务层不碰 Qt、不读界面文案：`sites()` 返回普通字典列表。
"""

from __future__ import annotations

from typing import Any, Dict, List

from services.base import Service

#: MC 相关网站目录（旧 `ui/app_base.py:382-718` 的 29 条，逐条照抄）。
#: 名称/描述/标签走语言文件的键（页面用 `Tr.map[key]` 取译文），这里只放键名与 URL。
SITES: List[Dict[str, Any]] = [
    {
        "id": "mc_official",
        "name_key": "links_site_mc_official_name",
        "desc_key": "links_site_mc_official_desc",
        "tag_keys": [
            "links_tag_official",
            "links_tag_game_download",
            "links_tag_updates",
        ],
        "url": "https://www.minecraft.net/zh-hans",
    },
    {
        "id": "mc_cn_official",
        "name_key": "links_site_mc_cn_official_name",
        "desc_key": "links_site_mc_cn_official_desc",
        "tag_keys": [
            "links_tag_official",
            "links_tag_game_download",
            "links_tag_china_edition",
        ],
        "url": "http://mc.163.com",
    },
    {
        "id": "mc_wiki",
        "name_key": "links_site_mc_wiki_name",
        "desc_key": "links_site_mc_wiki_desc",
        "tag_keys": [
            "links_tag_wiki",
            "links_tag_knowledge_base",
            "links_tag_chinese",
        ],
        "url": "https://zh.minecraft.wiki",
    },
    {
        "id": "minebbs",
        "name_key": "links_site_minebbs_name",
        "desc_key": "links_site_minebbs_desc",
        "tag_keys": [
            "links_tag_forum",
            "links_tag_community",
            "links_tag_resource_download",
        ],
        "url": "https://www.minebbs.com",
    },
    {
        "id": "klpbbs",
        "name_key": "links_site_klpbbs_name",
        "desc_key": "links_site_klpbbs_desc",
        "tag_keys": [
            "links_tag_forum",
            "links_tag_community",
            "links_tag_chinese",
        ],
        "url": "https://klpbbs.com",
    },
    {
        "id": "curseforge",
        "name_key": "links_site_curseforge_name",
        "desc_key": "links_site_curseforge_desc",
        "tag_keys": [
            "links_tag_mods",
            "links_tag_resource_download",
            "links_tag_plugins",
        ],
        "url": "https://www.curseforge.com/minecraft",
    },
    {
        "id": "modrinth",
        "name_key": "links_site_modrinth_name",
        "desc_key": "links_site_modrinth_desc",
        "tag_keys": [
            "links_tag_mods",
            "links_tag_resource_download",
            "links_tag_modpacks",
        ],
        "url": "https://modrinth.com",
    },
    {
        "id": "planetminecraft",
        "name_key": "links_site_planetminecraft_name",
        "desc_key": "links_site_planetminecraft_desc",
        "tag_keys": [
            "links_tag_maps",
            "links_tag_skins",
            "links_tag_resource_download",
        ],
        "url": "https://www.planetminecraft.com",
    },
    {
        "id": "minecraftskins",
        "name_key": "links_site_minecraftskins_name",
        "desc_key": "links_site_minecraftskins_desc",
        "tag_keys": [
            "links_tag_skins",
            "links_tag_resource_download",
        ],
        "url": "https://www.minecraftskins.net",
    },
    {
        "id": "namemc",
        "name_key": "links_site_namemc_name",
        "desc_key": "links_site_namemc_desc",
        "tag_keys": [
            "links_tag_skins",
            "links_tag_lookup",
        ],
        "url": "https://namemc.com",
    },
    {
        "id": "chunkbase",
        "name_key": "links_site_chunkbase_name",
        "desc_key": "links_site_chunkbase_desc",
        "tag_keys": [
            "links_tag_tools",
            "links_tag_seed_lookup",
            "links_tag_chunk_analysis",
        ],
        "url": "https://www.chunkbase.com",
    },
    {
        "id": "education",
        "name_key": "links_site_education_name",
        "desc_key": "links_site_education_desc",
        "tag_keys": [
            "links_tag_official",
            "links_tag_education",
            "links_tag_resources",
        ],
        "url": "https://education.minecraft.net",
    },
    {
        "id": "minecraft_heads",
        "name_key": "links_site_minecraft_heads_name",
        "desc_key": "links_site_minecraft_heads_desc",
        "tag_keys": [
            "links_tag_decoration",
            "links_tag_resources",
            "links_tag_heads",
        ],
        "url": "https://www.minecraft-heads.com",
    },
    {
        "id": "amulet",
        "name_key": "links_site_amulet_name",
        "desc_key": "links_site_amulet_desc",
        "tag_keys": [
            "links_tag_tools",
            "links_tag_map_editing",
            "links_tag_world_conversion",
        ],
        "url": "https://gitcode.com/gh_mirrors/am/Amulet-Map-Editor",
    },
    {
        "id": "mcskin",
        "name_key": "links_site_mcskin_name",
        "desc_key": "links_site_mcskin_desc",
        "tag_keys": [
            "links_tag_skins",
            "links_tag_editing_tool",
            "links_tag_custom",
        ],
        "url": "https://mcskins.top",
    },
    {
        "id": "minecraft_shaders",
        "name_key": "links_site_minecraft_shaders_name",
        "desc_key": "links_site_minecraft_shaders_desc",
        "tag_keys": [
            "links_tag_shaders",
            "links_tag_graphics",
            "links_tag_rendering",
        ],
        "url": "https://minecraftshader.com",
    },
    {
        "id": "resource_packs",
        "name_key": "links_site_resource_packs_name",
        "desc_key": "links_site_resource_packs_desc",
        "tag_keys": [
            "links_tag_resource_packs",
            "links_tag_hd_patches",
            "links_tag_textures",
        ],
        "url": "https://resourcepack.net",
    },
    {
        "id": "mcpedl",
        "name_key": "links_site_mcpedl_name",
        "desc_key": "links_site_mcpedl_desc",
        "tag_keys": [
            "links_tag_bedrock",
            "links_tag_mobile",
            "links_tag_addons",
        ],
        "url": "https://mcpedl.com",
    },
    {
        "id": "minecraft_maps",
        "name_key": "links_site_minecraft_maps_name",
        "desc_key": "links_site_minecraft_maps_desc",
        "tag_keys": [
            "links_tag_maps",
            "links_tag_adventure",
            "links_tag_download",
        ],
        "url": "http://www.minecraftmaps.com",
    },
    {
        "id": "the_skindex",
        "name_key": "links_site_the_skindex_name",
        "desc_key": "links_site_the_skindex_desc",
        "tag_keys": [
            "links_tag_skins",
            "links_tag_editor",
            "links_tag_community",
        ],
        "url": "https://www.minecraftskins.com",
    },
    {
        "id": "minecraft_servers",
        "name_key": "links_site_minecraft_servers_name",
        "desc_key": "links_site_minecraft_servers_desc",
        "tag_keys": [
            "links_tag_servers",
            "links_tag_multiplayer",
            "links_tag_list",
        ],
        "url": "https://minecraftservers.org",
    },
    {
        "id": "mclists",
        "name_key": "links_site_mclists_name",
        "desc_key": "links_site_mclists_desc",
        "tag_keys": [
            "links_tag_servers",
            "links_tag_domestic",
            "links_tag_chinese_servers",
        ],
        "url": "https://www.mclists.cn",
    },
    {
        "id": "minecraft_tools",
        "name_key": "links_site_minecraft_tools_name",
        "desc_key": "links_site_minecraft_tools_desc",
        "tag_keys": [
            "links_tag_tools",
            "links_tag_crafting",
            "links_tag_calculator",
        ],
        "url": "https://minecraft.tools",
    },
    {
        "id": "nova_skins",
        "name_key": "links_site_nova_skins_name",
        "desc_key": "links_site_nova_skins_desc",
        "tag_keys": [
            "links_tag_skin_editing",
            "links_tag_wallpaper",
            "links_tag_tools",
        ],
        "url": "https://novaskin.me",
    },
    {
        "id": "mclo_gs",
        "name_key": "links_site_mclo_gs_name",
        "desc_key": "links_site_mclo_gs_desc",
        "tag_keys": [
            "links_tag_log_analysis",
            "links_tag_debugging",
            "links_tag_server_admin",
        ],
        "url": "https://mclo.gs",
    },
    {
        "id": "chunker",
        "name_key": "links_site_chunker_name",
        "desc_key": "links_site_chunker_desc",
        "tag_keys": [
            "links_tag_save_conversion",
            "links_tag_cross_platform",
            "links_tag_tools",
        ],
        "url": "https://chunker.app",
    },
    {
        "id": "minecraft_forge",
        "name_key": "links_site_minecraft_forge_name",
        "desc_key": "links_site_minecraft_forge_desc",
        "tag_keys": [
            "links_tag_forge",
            "links_tag_mod_loader",
            "links_tag_api",
        ],
        "url": "https://www.minecraftforge.net",
    },
    {
        "id": "fabric",
        "name_key": "links_site_fabric_name",
        "desc_key": "links_site_fabric_desc",
        "tag_keys": [
            "links_tag_fabric",
            "links_tag_mod_loader",
            "links_tag_performance",
        ],
        "url": "https://fabricmc.net",
    },
    {
        "id": "armor_trims",
        "name_key": "links_site_armor_trims_name",
        "desc_key": "links_site_armor_trims_desc",
        "tag_keys": [
            "links_tag_armor_trims",
            "links_tag_preview",
            "links_tag_mc_1_20_plus",
        ],
        "url": "https://www.armortrims.com",
    },
]


def sites() -> List[Dict[str, Any]]:
    """返回 29 条站点（深拷一层，调用方改列表不会污染常量）。"""
    return [
        {**site, "tag_keys": list(site["tag_keys"])}
        for site in SITES
    ]


class LinksService(Service):
    """相关网站目录服务（`name = "links"`）。"""

    name = "links"
    label = "相关网站"

    def sites(self) -> List[Dict[str, Any]]:
        """转发 :func:`sites`。"""
        return sites()


__all__ = ["SITES", "LinksService", "sites"]
