"""输入验证模块 - 防止命令注入和路径穿越攻击"""
import ipaddress
import os
import re
from pathlib import Path
from typing import Optional

from logzero import logger


# 版本ID允许的字符：字母、数字、点、短横线、下划线
VERSION_ID_PATTERN = re.compile(r'^[a-zA-Z0-9._\-]+$')

# 内存大小格式：数字 + G/M (大小写不敏感)
MEMORY_PATTERN = re.compile(r'^\d+\s*[GgMm]$')

# IP/域名格式
IP_PATTERN = re.compile(
    r'^(\d{1,3}\.){3}\d{1,3}$'
    r'|'
    r'^[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?)*$'
)


def validate_version_id(version_id: str) -> bool:
    """验证版本ID是否合法（防止路径穿越）

    Args:
        version_id: 版本ID字符串

    Returns:
        是否合法
    """
    if not version_id:
        return False
    if '..' in version_id:
        return False
    if version_id.startswith('/') or version_id.startswith('\\'):
        return False
    if VERSION_ID_PATTERN.match(version_id):
        return True
    return False


def validate_server_ip(ip: str) -> bool:
    """验证服务器IP地址是否合法

    Args:
        ip: 服务器IP地址或域名

    Returns:
        是否合法
    """
    if not ip:
        return True  # 空IP是合法的（表示不指定）
    return bool(IP_PATTERN.match(ip))


def validate_server_port(port: int) -> bool:
    """验证服务器端口号是否合法

    Args:
        port: 端口号

    Returns:
        是否合法
    """
    return isinstance(port, int) and 1 <= port <= 65535


def validate_memory(memory_str: str) -> bool:
    """验证内存大小字符串是否合法

    Args:
        memory_str: 内存大小字符串，如 "2G", "4096M"

    Returns:
        是否合法
    """
    if not memory_str:
        return False
    return bool(MEMORY_PATTERN.match(memory_str.strip()))


def safe_path_join(base_dir: Path, user_input: str) -> Optional[Path]:
    """安全的路径拼接，防止路径穿越

    Args:
        base_dir: 基础目录
        user_input: 用户输入的子路径

    Returns:
        安全的完整路径，如果检测到路径穿越则返回 None
    """
    if not user_input:
        return None
    try:
        resolved = (base_dir.resolve() / user_input).resolve()
        base_resolved = base_dir.resolve()
        if not str(resolved).startswith(str(base_resolved)):
            logger.warning(f"检测到路径穿越尝试: {user_input}")
            return None
        return resolved
    except (ValueError, OSError) as e:
        logger.error(f"路径解析错误: {e}")
        return None


def sanitize_filename(filename: str) -> str:
    """清理文件名，移除不安全字符

    Args:
        filename: 原始文件名

    Returns:
        清理后的文件名
    """
    unsafe_chars = r'[<>:"/\\|?*]'
    sanitized = re.sub(unsafe_chars, '_', filename)
    sanitized = sanitized.strip('. ')
    return sanitized or '_'
