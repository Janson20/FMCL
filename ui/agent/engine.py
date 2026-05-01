"""Tool 执行引擎 - 调度工具调用并返回结果"""

import json
import subprocess
import threading
from typing import Dict, Any, Callable, Optional
from logzero import logger


TOOL_REGISTRY: Dict[str, str] = {
    "get_available_versions": "获取可用版本列表",
    "get_installed_versions": "获取已安装版本列表",
    "install_version": "安装版本",
    "launch_game": "启动游戏",
    "search_mods": "搜索模组",
    "install_mod": "安装模组",
    "delete_version": "删除版本",
    "get_installed_servers": "获取已安装服务器列表",
    "start_server": "启动服务器",
    "delete_server_version": "删除服务器版本",
    "install_modpack": "安装整合包",
    "install_modpack_server": "整合包开服",
    "search_modpack": "搜索整合包",
    "download_modpack": "下载整合包",
}


def execute_tool(tool_name: str, params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """执行工具调用，返回供 AI 使用的文本结果"""
    logger.info(f"[Agent] 执行工具: {tool_name}, 参数: {params}")

    if tool_name == "get_available_versions":
        return _get_available_versions(callbacks)
    elif tool_name == "get_installed_versions":
        return _get_installed_versions(callbacks)
    elif tool_name == "install_version":
        return _install_version(params, callbacks)
    elif tool_name == "launch_game":
        return _launch_game(params, callbacks)
    elif tool_name == "search_mods":
        return _search_mods(params, callbacks)
    elif tool_name == "install_mod":
        return _install_mod(params, callbacks)
    elif tool_name == "delete_version":
        return _delete_version(params, callbacks)
    elif tool_name == "get_installed_servers":
        return _get_installed_servers(callbacks)
    elif tool_name == "start_server":
        return _start_server(params, callbacks)
    elif tool_name == "delete_server_version":
        return _delete_server_version(params, callbacks)
    elif tool_name == "install_modpack":
        return _install_modpack(params, callbacks)
    elif tool_name == "install_modpack_server":
        return _install_modpack_server(params, callbacks)
    elif tool_name == "search_modpack":
        return _search_modpack(params, callbacks)
    elif tool_name == "download_modpack":
        return _download_modpack(params, callbacks)
    else:
        return f"错误: 未知工具 '{tool_name}'"


def _get_available_versions(callbacks: Dict[str, Callable]) -> str:
    """获取可安装版本列表"""
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


def _get_installed_versions(callbacks: Dict[str, Callable]) -> str:
    """获取已安装版本列表"""
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
    """安装版本"""
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
        logger.error(f"[Agent] install_version 返回失败: version={version_id}, loader={mod_loader}")
        loader_hint = ""
        if mod_loader != "无":
            loader_hint = f"，可能是 {mod_loader} 暂不支持该版本"
        return f"❌ 安装失败: 版本 {version_id} 安装出错{loader_hint}，请检查版本号是否正确"


def _launch_game(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """启动游戏"""
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
                    for line in proc.stdout:
                        pass
                except Exception:
                    pass
            threading.Thread(target=_drain_pipe, daemon=True, name="AgentStdoutDrain").start()
            logger.info("[Agent] 已启动后台线程 drain stdout 管道，防止 IO 阻塞")
        return f"🚀 游戏已启动！版本: {target}"
    else:
        return f"❌ 启动失败: 版本 {version_id} 启动出错"


def _search_mods(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """在 Modrinth 搜索模组"""
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
    """安装模组"""
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
        import os
        from pathlib import Path
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


def _delete_version(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """删除已安装的客户端版本"""
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


def _get_installed_servers(callbacks: Dict[str, Callable]) -> str:
    """获取已安装服务器列表"""
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
    """启动服务器"""
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
                    for line in process.stdout:
                        pass
                except Exception:
                    pass
            threading.Thread(target=_drain_pipe, daemon=True, name="AgentServerStdoutDrain").start()
            logger.info("[Agent] 已启动后台线程 drain 服务器 stdout 管道，防止 IO 阻塞")
        return f"🚀 服务器 {version_id} 已启动！内存: {max_memory}，端口: 25565"
    else:
        return f"❌ 服务器启动失败: 版本 {version_id} 可能未安装或启动时出错"


def _delete_server_version(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """删除已安装的服务器版本"""
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


def _install_modpack(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """安装整合包"""
    if "get_mrpack_information" not in callbacks or "install_mrpack" not in callbacks:
        return "错误: 整合包安装功能不可用"

    mrpack_path = params.get("mrpack_path", "").strip()
    if not mrpack_path:
        return "错误: 缺少 mrpack_path 参数（.mrpack 文件的绝对路径）"

    import os
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
    """安装整合包作为服务器"""
    if "install_mrpack_server" not in callbacks or "get_mrpack_information" not in callbacks:
        return "错误: 整合包开服功能不可用"

    mrpack_path = params.get("mrpack_path", "").strip()
    if not mrpack_path:
        return "错误: 缺少 mrpack_path 参数（.mrpack 文件的绝对路径）"

    import os
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


def _search_modpack(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    """在 Modrinth 搜索整合包"""
    try:
        from modrinth import search_modpacks as modrinth_search, compress_game_versions

        query = params.get("query", "").strip()
        game_version = params.get("game_version", "").strip() or None

        result = modrinth_search(
            query=query,
            game_version=game_version,
            limit=10,
        )

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
    """从 Modrinth 下载整合包 .mrpack 文件"""
    try:
        from modrinth import download_modpack_file

        project_id = params.get("project_id", "").strip()
        game_version = params.get("game_version", "").strip() or None

        if not project_id:
            return "错误: 缺少 project_id 参数"

        success, result = download_modpack_file(
            project_id,
            game_version=game_version,
        )

        if success:
            import os
            filename = os.path.basename(result)
            return f"✅ 整合包下载成功！\n文件名: {filename}\n路径: {result}"
        else:
            return f"❌ 整合包下载失败: {result}"

    except Exception as e:
        logger.error(f"下载整合包失败: {e}")
        return f"❌ 下载整合包失败: {str(e)}"
