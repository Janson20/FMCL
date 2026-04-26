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
                "description": "在 Modrinth 上搜索模组",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "搜索关键词，如 sodium、jei 等",
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
                    "required": ["query"],
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
    ]


def get_system_prompt() -> str:
    """获取 AI 系统提示词"""
    return """你是一个 Minecraft 启动器智能助手，可以通过调用工具帮助用户管理 Minecraft 版本和模组。

## 可用工具
- get_available_versions: 获取可安装的版本列表
- get_installed_versions: 获取本地已安装版本
- install_version: 安装版本（支持 Forge/Fabric/NeoForge）
- launch_game: 启动游戏
- search_mods: 搜索 Modrinth 模组
- install_mod: 安装模组

## 工作流程
1. 分析用户需求，确定需要调用哪些工具
2. 按顺序调用工具，每次调用后分析结果
3. 如果信息不足，继续调用相关工具获取
4. 当需要用户选择时，给出清晰选项

## 重要规则
- 安装模组前必须先获取已安装版本列表确认版本存在
- 搜索模组时必须指定游戏版本和加载器
- 启动游戏前需要确认版本确实已安装
- 当有多个匹配时，必须让用户选择
- 每次只调用一个工具，等待结果后再决定下一步
- 用友好、热情的语气回复

## 输出格式（必须严格使用以下 XML 格式）

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
"""
