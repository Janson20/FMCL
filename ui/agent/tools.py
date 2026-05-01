"""AGENT 工具定义 - 封装启动器功能供 AI 调用"""

from typing import List, Dict, Any, Optional
from logzero import logger


def get_tool_definitions() -> List[Dict]:
    """获取 OpenAI function-calling 格式的工具定义"""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_available_versions",
                "description": "获取所有可安装的 Minecraft 版本列表（包括正式版和快照版）",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_installed_versions",
                "description": "获取本地已安装的 Minecraft 版本列表",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "install_version",
                "description": "安装指定版本的 Minecraft，可选模组加载器（Forge/Fabric/NeoForge）",
                "parameters": {
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
            },
        },
        {
            "type": "function",
            "function": {
                "name": "launch_game",
                "description": "启动指定版本的 Minecraft 游戏",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "version_id": {
                            "type": "string",
                            "description": "要启动的版本ID，如 1.20.1、1.20.1-forge-49.0.26 等",
                        },
                    },
                    "required": ["version_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_mods",
                "description": "在 Modrinth 上搜索模组。不填 query 时返回默认热门模组",
                "parameters": {
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
            },
        },
        {
            "type": "function",
            "function": {
                "name": "install_mod",
                "description": "从 Modrinth 安装模组到指定 Minecraft 版本。需要先通过 get_installed_versions 确认目标版本已安装",
                "parameters": {
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
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_version",
                "description": "删除本地已安装的 Minecraft 客户端版本",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "version_id": {
                            "type": "string",
                            "description": "要删除的版本ID，如 1.20.1、1.20.1-forge-49.0.26 等",
                        },
                    },
                    "required": ["version_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_installed_servers",
                "description": "获取本地已安装的服务器版本列表",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "start_server",
                "description": "启动指定版本的 Minecraft 服务器",
                "parameters": {
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
            },
        },
        {
            "type": "function",
            "function": {
                "name": "delete_server_version",
                "description": "删除本地已安装的 Minecraft 服务器版本",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "version_id": {
                            "type": "string",
                            "description": "要删除的服务器版本号，如 1.21.4",
                        },
                    },
                    "required": ["version_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "install_modpack",
                "description": "安装 .mrpack 整合包文件",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mrpack_path": {
                            "type": "string",
                            "description": ".mrpack 整合包文件的绝对路径",
                        },
                    },
                    "required": ["mrpack_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "install_modpack_server",
                "description": "安装 .mrpack 整合包作为服务器",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mrpack_path": {
                            "type": "string",
                            "description": ".mrpack 整合包文件的绝对路径",
                        },
                    },
                    "required": ["mrpack_path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_modpack",
                "description": "在 Modrinth 上搜索整合包。不填 query 时返回默认热门整合包",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词，如 skyblock、RLCraft 等。留空则返回热门整合包",
                        },
                        "game_version": {
                            "type": "string",
                            "description": "Minecraft 版本号，如 1.20.1",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "download_modpack",
                "description": "从 Modrinth 下载整合包 .mrpack 文件，返回下载完成的文件路径。需要先从 search_modpack 获取 project_id",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "project_id": {
                            "type": "string",
                            "description": "Modrinth 整合包项目ID（从 search_modpack 结果中获得）",
                        },
                        "game_version": {
                            "type": "string",
                            "description": "Minecraft 版本号筛选，如 1.20.1",
                        },
                    },
                    "required": ["project_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_resource_packs",
                "description": "在 Modrinth 上搜索资源包。不填 query 时返回默认热门资源包",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词，如 Faithful、Default等。留空则返回热门资源包",
                        },
                        "game_version": {
                            "type": "string",
                            "description": "Minecraft 版本号，如 1.20.1",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "install_resource_pack",
                "description": "从 Modrinth 安装资源包到指定 Minecraft 版本的 resourcepacks 目录。需要先通过 get_installed_versions 确认目标版本已安装",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "version_id": {
                            "type": "string",
                            "description": "Minecraft 版本文件夹/实例名称，如 1.20.1、1.20.1-forge-49.0.26。资源包将安装到该版本的 resourcepacks 目录",
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
            },
        },
        {
            "type": "function",
            "function": {
                "name": "search_shaders",
                "description": "在 Modrinth 上搜索光影。不填 query 时返回默认热门光影",
                "parameters": {
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
            },
        },
        {
            "type": "function",
            "function": {
                "name": "install_shader",
                "description": "从 Modrinth 安装光影到指定 Minecraft 版本的 shaderpacks 目录。需要先通过 get_installed_versions 确认目标版本已安装",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "version_id": {
                            "type": "string",
                            "description": "Minecraft 版本文件夹/实例名称，如 1.20.1、1.20.1-forge-49.0.26。光影将安装到该版本的 shaderpacks 目录",
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
            },
        },
    ]


def get_system_prompt() -> str:
    """获取 AI 系统提示词"""
    return """你是一个 Minecraft 启动器智能助手，可以通过调用工具帮助用户管理 Minecraft 版本、模组、服务器和整合包。

## 可用工具
### 客户端管理
- get_available_versions: 获取可安装的版本列表
- get_installed_versions: 获取本地已安装版本
- install_version: 安装版本（支持 Forge/Fabric/NeoForge）
- delete_version: 删除已安装的客户端版本
- launch_game: 启动游戏

### 模组管理
- search_mods: 搜索 Modrinth 模组
- install_mod: 安装模组

### 服务器管理
- get_installed_servers: 获取已安装的服务器版本列表
- start_server: 启动服务器（可指定内存大小）
- delete_server_version: 删除已安装的服务器版本

### 整合包管理
- search_modpack: 在 Modrinth 搜索整合包
- download_modpack: 从 Modrinth 下载整合包 .mrpack（返回下载路径）
- install_modpack: 安装 .mrpack 整合包
- install_modpack_server: 安装 .mrpack 整合包作为服务器

### 资源包管理
- search_resource_packs: 搜索 Modrinth 资源包
- install_resource_pack: 安装资源包到指定版本

### 光影管理
- search_shaders: 搜索 Modrinth 光影
- install_shader: 安装光影到指定版本

## 工作流程
1. 分析用户需求，确定需要调用哪些工具
2. 按顺序调用工具，每次调用后分析结果
3. 如果信息不足，继续调用相关工具获取
4. 当需要用户选择时，给出清晰选项

## 重要规则
- 输出必须严格使用 XML 格式，无额外内容
- 安装模组前必须先获取已安装版本列表确认版本存在
- 搜索模组时建议指定游戏版本和加载器以获得更准确结果，不填 query 则返回热门模组
- 搜索整合包时不填 query 则返回热门整合包
- 启动游戏前需要确认版本确实已安装
- 删除版本前必须先用 get_installed_versions 确认版本存在
- 开服前必须先用 get_installed_servers 确认服务器版本已安装
- 删除服务器前必须先用 get_installed_servers 确认服务器版本存在
- 整合包安装需要用户提供 .mrpack 文件的绝对路径
- 整合包开服同样需要用户提供 .mrpack 文件的绝对路径
- 从 Modrinth 下载整合包：先用 search_modpack 搜索获取 project_id，再用 download_modpack 下载，下载完成后文件路径可用于 install_modpack 安装
- download_modpack 返回的路径可直接传给 install_modpack 使用
- 安装资源包和光影前必须先用 get_installed_versions 确认版本已安装
- install_resource_pack 和 install_shader 的 version_id 是版本文件夹/实例名称（如 1.20.1-forge-49.0.26），不是纯版本号。需要从 get_installed_versions 返回的完整版本ID中获取
- 当有多个匹配时，必须让用户选择
- 每次只调用一个工具，等待结果后再决定下一步
- 用友好、热情的语气回复

## 输出格式（必须 **严格使用** 以下 XML 格式，无额外内容）

如果调用工具：
<response>
  <thinking>为什么要调用这个工具</thinking>
  <message>告知用户正在做什么</message>
  <action type="tool_call">
    <tool>工具名称</tool>
    <params>
      <parameter name="参数名">参数值</parameter>
    </params>
  </action>
</response>

如果需要用户选择：
<response>
  <thinking>分析需要用户选择的选项</thinking>
  <message>请用户做出选择</message>
  <action type="await_choice">
    <options>
      <option value="选项值1">选项显示文本1</option>
      <option value="选项值2">选项显示文本2</option>
    </options>
  </action>
</response>

完成后：
<response>
  <thinking>总结完成的操作</thinking>
  <message>告知用户操作已完成</message>
  <action type="complete" />
</response>

## 参数说明
- <parameter> 标签的 name 属性和内容必须与工具定义的参数名完全一致
- 例如 install_version 需要 version_id 和 mod_loader 两个参数
- 如果某个参数不需要，也必须提供该标签，内容为空
- install_mod 的 version_id 是纯 Minecraft 版本号（如 1.20.1、26.1.2），不是完整版本ID
- install_mod 需要 mod_loader 参数来指定加载器（fabric/forge/neoforge）
- 安装模组前必须先用 get_installed_versions 确认版本已安装
- 搜索模组时 search_mods 需要指定 game_version 和 mod_loader
- start_server 的 max_memory 参数可选，默认 2G
- install_modpack 和 install_modpack_server 需要绝对路径
- search_modpack 的 game_version 参数可选，用于筛选特定版本
- download_modpack 的 project_id 来自 search_modpack 结果，game_version 可选
- install_resource_pack 需要 version_id（版本文件夹/实例名称）和 pack_name，project_id 可选
- install_shader 需要 version_id（版本文件夹/实例名称）和 shader_name，project_id 可选
- 安装资源包/光影时，version_id 必须从 get_installed_versions 返回的列表中精确匹配
"""
