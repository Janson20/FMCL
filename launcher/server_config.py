"""Minecraft 服务器配置文件读写工具

为「🖥 开服」标签页的服务器配置编辑器提供底层文件操作：
- server.properties：读取、合并写回（保留注释与未知键）
- eula.txt：读取 / 写入 EULA 同意状态
- .fmcl_server.json：每个服务器独立的启动配置（如最大内存）

所有函数都是纯文件操作，不依赖 UI，可被窗口、启动器与 AI 工具复用。
"""

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from logzero import logger

# 每个服务器独立的启动配置文件（放在服务器目录内）
LAUNCH_CONFIG_FILENAME = ".fmcl_server.json"

# 新装服务器使用的 server.properties 默认内容
# 顺序与 FMCL 安装服务器时生成的文件一致，便于人工对照
DEFAULT_SERVER_PROPERTIES: Dict[str, str] = {
    "enable-jmx-monitoring": "false",
    "rcon.port": "25575",
    "level-seed": "",
    "gamemode": "survival",
    "enable-command-block": "false",
    "enable-query": "false",
    "generator-settings": "{}",
    "enforce-secure-profile": "false",
    "level-name": "world",
    "motd": "FMCL Server",
    "query.port": "25565",
    "pvp": "true",
    "generate-structures": "true",
    "max-chained-neighbor-updates": "1000000",
    "difficulty": "easy",
    "network-compression-threshold": "256",
    "max-tick-time": "60000",
    "require-resource-pack": "false",
    "use-native-transport": "true",
    "max-players": "20",
    "online-mode": "false",
    "enable-status": "true",
    "allow-flight": "false",
    "initial-disabled-packs": "",
    "broadcast-rcon-to-ops": "true",
    "view-distance": "10",
    "server-ip": "",
    "resource-pack-prompt": "",
    "allow-nether": "true",
    "server-port": "25565",
    "enable-rcon": "false",
    "sync-chunk-writes": "true",
    "op-permission-level": "4",
    "prevent-proxy-connections": "false",
    "hide-online-players": "false",
    "resource-pack": "",
    "entity-broadcast-range-percentage": "100",
    "simulation-distance": "10",
    "rcon.password": "",
    "player-idle-timeout": "0",
    "force-gamemode": "false",
    "rate-limit": "0",
    "hardcore": "false",
    "white-list": "false",
    "broadcast-console-to-ops": "true",
    "spawn-npcs": "true",
    "spawn-animals": "true",
    "function-permission-level": "2",
    "initial-enabled-packs": "vanilla",
    "level-type": "minecraft\\:normal",
    "text-filtering-config": "",
    "spawn-monsters": "true",
    "enforce-whitelist": "false",
    "spawn-protection": "16",
    "resource-pack-sha1": "",
    "max-world-size": "29999984",
}


def _clean_value(value: Any) -> str:
    """把值规范成可以安全写入 properties 的单行字符串"""
    text = "" if value is None else str(value)
    return text.replace("\r", " ").replace("\n", " ").strip()


def parse_properties(text: str) -> Dict[str, str]:
    """解析 server.properties 文本，返回 {键: 值}（保持文件中的顺序）

    忽略空行与 # / ! 开头的注释行；键值分隔符支持 = 与 :。
    """
    result: Dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
        elif ":" in line:
            key, value = line.split(":", 1)
        else:
            continue
        key = key.strip()
        if key:
            result[key] = value.strip()
    return result


def format_properties(values: Dict[str, str]) -> str:
    """把 {键: 值} 渲染成 server.properties 文本"""
    lines = ["#Minecraft server properties"]
    for key, value in values.items():
        lines.append(f"{key}={_clean_value(value)}")
    return "\n".join(lines) + "\n"


def get_server_dir(server_root: Any, version_id: str) -> Path:
    """由服务器根目录与版本 ID 得到单个服务器的目录"""
    return Path(server_root) / version_id


def get_server_properties_path(server_dir: Any) -> Path:
    """获取某个服务器的 server.properties 路径"""
    return Path(server_dir) / "server.properties"


def get_launch_config_path(server_dir: Any) -> Path:
    """获取某个服务器的独立启动配置文件路径"""
    return Path(server_dir) / LAUNCH_CONFIG_FILENAME


def read_server_properties(server_dir: Any) -> Dict[str, str]:
    """读取某个服务器的 server.properties；文件不存在或出错时返回空字典"""
    path = get_server_properties_path(server_dir)
    try:
        if not path.exists():
            return {}
        return parse_properties(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        logger.error(f"读取 server.properties 失败 ({path}): {e}")
        return {}


def write_server_properties(server_dir: Any, values: Dict[str, str]) -> Tuple[bool, str]:
    """把 {键: 值} 合并写回 server.properties

    只更新 values 中出现的键：
    - 文件中已有的同名键 → 原地替换（注释、空行、顺序都保留）
    - 文件中没有的键 → 追加到文件末尾
    - 文件中存在但 values 里没有的键 → 原样保留

    Args:
        server_dir: 单个服务器的目录
        values: 需要写入的键值对

    Returns:
        (是否成功, 错误信息)
    """
    path = get_server_properties_path(server_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        existing_lines: List[str] = []
        if path.exists():
            existing_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()

        cleaned = {str(k).strip(): _clean_value(v) for k, v in values.items() if str(k).strip()}

        written: set = set()
        out_lines: List[str] = []
        for raw_line in existing_lines:
            line = raw_line.strip()
            if line and not line.startswith(("#", "!")):
                if "=" in line:
                    key = line.split("=", 1)[0].strip()
                elif ":" in line:
                    key = line.split(":", 1)[0].strip()
                else:
                    key = ""
                if key and key in cleaned:
                    out_lines.append(f"{key}={cleaned[key]}")
                    written.add(key)
                    continue
            out_lines.append(raw_line)

        missing = [k for k in cleaned if k not in written]
        if missing:
            while out_lines and not out_lines[-1].strip():
                out_lines.pop()
            if out_lines:
                out_lines.append("")
            out_lines.append("# --- 由 FMCL 服务器配置编辑器写入 ---")
            for key in missing:
                out_lines.append(f"{key}={cleaned[key]}")

        text = "\n".join(out_lines).strip("\n") + "\n"

        # 先写临时文件再替换，避免中途失败损坏原文件
        tmp_path = path.with_name(path.name + ".tmp")
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(str(tmp_path), str(path))
        logger.info(f"server.properties 已保存: {path}（更新 {len(written)} 项，新增 {len(missing)} 项）")
        return True, ""
    except Exception as e:
        logger.error(f"保存 server.properties 失败 ({path}): {e}")
        return False, str(e)


def read_eula(server_dir: Any) -> bool:
    """读取 eula.txt，判断是否已同意 EULA"""
    path = Path(server_dir) / "eula.txt"
    try:
        if not path.exists():
            return False
        content = path.read_text(encoding="utf-8", errors="replace").lower()
        return "eula=true" in content.replace(" ", "")
    except Exception as e:
        logger.error(f"读取 eula.txt 失败: {e}")
        return False


def write_eula(server_dir: Any, agreed: bool) -> Tuple[bool, str]:
    """写入 eula.txt"""
    path = Path(server_dir) / "eula.txt"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "#By changing the setting below to TRUE you are indicating your agreement to our EULA.\n"
            f"eula={'true' if agreed else 'false'}\n",
            encoding="utf-8",
        )
        return True, ""
    except Exception as e:
        logger.error(f"写入 eula.txt 失败: {e}")
        return False, str(e)


def read_launch_config(server_dir: Any) -> Dict[str, Any]:
    """读取某个服务器的独立启动配置（.fmcl_server.json）"""
    path = get_launch_config_path(server_dir)
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning(f"读取服务器启动配置失败 ({path}): {e}")
        return {}


def write_launch_config(server_dir: Any, config: Dict[str, Any]) -> Tuple[bool, str]:
    """写入某个服务器的独立启动配置"""
    path = get_launch_config_path(server_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return True, ""
    except Exception as e:
        logger.error(f"写入服务器启动配置失败 ({path}): {e}")
        return False, str(e)


def get_server_launch_memory(server_dir: Any) -> Optional[str]:
    """获取某个服务器单独设置的最大内存（未设置时返回 None）"""
    value = read_launch_config(server_dir).get("max_memory")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def set_server_launch_memory(server_dir: Any, memory: Optional[str]) -> Tuple[bool, str]:
    """设置（或清除）某个服务器单独的最大内存"""
    config = read_launch_config(server_dir)
    if memory and str(memory).strip():
        config["max_memory"] = str(memory).strip()
    else:
        config.pop("max_memory", None)
    return write_launch_config(server_dir, config)


def ensure_server_properties(server_dir: Any, values: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
    """确保 server.properties 存在；不存在时用默认值创建"""
    path = get_server_properties_path(server_dir)
    if path.exists():
        return True, ""
    return write_server_properties(server_dir, values if values is not None else DEFAULT_SERVER_PROPERTIES)
