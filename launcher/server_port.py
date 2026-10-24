"""服务器端口选择与 server.properties 回写（阶段 3 任务 3.9 验收反馈）。

旧 start_server 在启动前调用 _kill_process_on_port(25565) —— 把占用端口的
**别的进程**直接杀掉。用户要求改成“端口自动防冲突”：25565 被占用时顺延到一个
可用端口，并告诉用户实际端口；不要杀任何进程。

本模块只做三件事：

1. 从 server.properties 读 server-port（缺省 / 非法值都是 25565）；
2. 用 TCP bind 探测端口是否可用（不依赖 psutil / netstat，三平台一致）；
3. 需要换端口时用 write_server_properties 原地改一行（保留注释与顺序）。

不在这里起进程、不碰 UI、不读启动器配置；start_server 只调用
choose_server_port，失败了就按启动失败返回。
"""

from __future__ import annotations

import socket
from typing import Any, Optional, Tuple

from logzero import logger

from launcher.server_config import read_server_properties, write_server_properties

#: Minecraft 默认端口。
DEFAULT_PORT = 25565

#: 从 25565 起最多顺延多少个端口（100 个足够避开常见的多开场景）。
PORT_SCAN_LIMIT = 100


def parse_port(value: Any, default: int = DEFAULT_PORT) -> int:
    """把配置值转成合法端口；非法/越界一律回退 default。"""
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return port if 1 <= port <= 65535 else default


def read_server_port(server_dir: Any) -> int:
    """读取某个服务器配置的端口；文件不存在/读取失败/值非法时返回 25565。"""
    try:
        props = read_server_properties(server_dir)
    except Exception as e:  # noqa: BLE001 - 读不到就按默认端口
        logger.warning(f"读取服务器端口失败 ({server_dir}): {e}")
        return DEFAULT_PORT
    return parse_port(props.get("server-port"))


def is_port_available(port: int) -> bool:
    """TCP 端口是否可用（bind 成功即空闲；不设置 SO_REUSEADDR，避免误判）。"""
    if not (1 <= int(port) <= 65535):
        return False
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("", int(port)))
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass


def choose_server_port(
    server_dir: Any,
    *,
    preferred: Optional[int] = None,
    scan_limit: int = PORT_SCAN_LIMIT,
) -> Tuple[Optional[int], bool, str]:
    """为一次启动选端口；配置端口被占用时顺延并回写 server.properties。

    Returns:
        (端口, 是否改写了配置, 错误信息)。端口为 None 表示扫描范围内没有
        可用端口，错误信息可直接给日志。
    """
    configured = read_server_port(server_dir)
    start = parse_port(preferred, configured) if preferred is not None else configured

    candidates = []
    for port in (start, configured, *range(DEFAULT_PORT, DEFAULT_PORT + max(1, scan_limit))):
        if port not in candidates:
            candidates.append(port)

    for port in candidates:
        if not is_port_available(port):
            continue
        if port == configured:
            return port, False, ""
        success, error = write_server_properties(server_dir, {"server-port": str(port)})
        if not success:
            return None, False, error or "write_server_properties_failed"
        logger.info(f"服务器端口 {configured} 不可用，已自动改用 {port}: {server_dir}")
        return port, True, ""
    return None, False, f"no_free_port_in_{start}_{start + max(1, scan_limit) - 1}"


__all__ = [
    "DEFAULT_PORT",
    "PORT_SCAN_LIMIT",
    "choose_server_port",
    "is_port_available",
    "parse_port",
    "read_server_port",
]
