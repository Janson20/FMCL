"""联机服务（阶段 1 任务 1.5）—— EasyTier 组网与陶瓦大厅的纯逻辑。

从 ``ui/app_online.py``（2672 行、12 个类、147 个方法）**逐字搬运**而来。留在界面
文件里的是控件构建、``after`` 调度、弹窗、剪贴板与日志框投递；搬进来的是这些：

- **EasyTier 生命周期**：``EasyTierManager``（检测 / 测速选镜像 / 下载解压 / 参数
  拼装 / 启动停止 / 输出读取 / 中继节点 / 端口转发 / peer 发现）
- **陶瓦（Scaffolding）大厅协议**：``ScaffoldingServer``（服务端帧处理与成员表）、
  ``ScaffoldingClient``（客户端握手 / 心跳 / 成员列表）
- **大厅码**：``LobbyCodeGenerator``（FMCL ``U/`` 码与陶瓦 25 位码的编解码校验）、
  ``LobbyInfo``、``LobbyState``
- **局域网发现**：``BroadcastListener``（MOTD/AD 报文解析）、``McBroadcastSimulator``
- **MC 协议**：``McPing``（Server List Ping）、``_write_varint`` / ``_read_varint``
- **进程与端口**：``GameWatcher``、``TcpPortForwarder``、``_get_random_port``

设计约定（与 ``services/server_service.py`` / ``services/tool_service.py`` 一致）：

- **零 UI 依赖**：不 import tkinter / customtkinter / ``ui.*``（由
  ``scripts/check_services_purity.py`` 强制）。原 ``_get_vendor`` 里的
  ``_get_fmcl_version`` 改从 ``services.user_agent`` 取 —— 那是**同一个函数对象**
  （``ui/constants.py`` 就是 ``from services.user_agent import _get_fmcl_version``），
  见报告"必要改动"第 1 条。
- **不弹窗、不碰控件**：需要提示用户时由调用方（界面）自己做，服务只回传
  布尔 / ``None`` / 退出码。
- **失败表达方式沿用改造前**：``False`` / ``None`` / ``0`` 哨兵，不新增异常类型
  （见 ``services/errors.py`` 的阶段 1 约定）。
- **线程**：``ScaffoldingServer`` / ``ScaffoldingClient`` / ``BroadcastListener`` /
  ``McBroadcastSimulator`` / ``TcpPortForwarder`` 的内部线程是**改造前就有**的，
  原样保留；``EasyTierManager.stop_async`` 同理。界面侧的
  ``_run_online_thread``（起线程 + ``after`` 回主线程）也原样留在界面文件里。

``OnlineService()`` 构造期只保存参数、不读盘不联网，**不需要 AppContext** 即可
独立实例化（阶段 1 的 Tk 界面就是这么取的）。

.. note::
    日志用 ``logzero.logger`` 而不是 ``logging.getLogger(__name__)``，这是**刻意的**：
    界面侧 ``ui/app_base.py`` 把日志框写入器挂在 ``logzero.logger`` 上，
    换成模块自己的 logger 会让联机日志从界面日志框里消失。

.. warning::
    任务 1.5 搬家时这里留了**两处既有缺陷的现场**（``_cleanup_stale_versions`` 的
    破坏性删除 D-83、``_read_output`` 里跨线程直进 UI 的输出回调 D-90）。
    任务 1.24 的处置：

    - **D-83 已修正**（有意的行为变更）：破坏性的自动删除**没有了**，
      换成"**只读**清单 :meth:`EasyTierManager.stale_version_dirs` + 只删显式传入
      路径的 :meth:`EasyTierManager.remove_stale_versions`"。``download()`` 不再删
      任何东西，把清单留在 :attr:`EasyTierManager.last_stale_version_dirs` 上交回
      调用方，由界面在主线程问过用户之后才删；没人确认 → 一个目录都不删。
    - **D-107 已修正**：``_flatten_extracted_dir`` 只在子目录**已空**时才删，
      非空则保留并记日志（旧实现无论如何都递归删掉）。
    - **D-90 仍未修**（投递端在 ``ui/app_online.py``，不归服务层）：服务只负责
      "如实在调用线程里回调 on_output"，跨线程投递由界面侧负责。
"""

import enum
import hashlib
import json
import os
import platform
import re
import secrets
import shutil
import socket
import struct
import subprocess
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from logzero import logger

from services.base import Service
from services.user_agent import _get_fmcl_version

# 官方版 EasyTier（与 PCL CE 同款 2.6.4），协议与陶瓦生态定制版（v2.5.0-terracotta.2，仅可见性改动）互通。
# 下载时会对全部镜像做测速（range 探活），选择最快可达的镜像下载。
EASYTIER_VERSION = "2.6.4"
EASYTIER_DOWNLOAD_URLS = [
    "https://staticassets.naids.com/resources/pclce/static/easytier/easytier-windows-x86_64-v{version}.zip",
    "https://s3.pysio.online/pcl2-ce/static/easytier/easytier-windows-x86_64-v{version}.zip",
    "https://easytier.jingdu.qzz.io/download/v{version}/easytier-windows-x86_64-v{version}.zip",
    "https://github.com/EasyTier/EasyTier/releases/download/v{version}/easytier-windows-x86_64-v{version}.zip",
]
_EASYTIER_SPEED_TEST_TIMEOUT = 6.0

#: 「看起来像 EasyTier 版本号目录」的名字形态（D-83 的判据之一）。
#: 认 `2.6.4` / `v2.6.4` / `2.5.0-terracotta.2`，不认 `mytools` / `backup-2024`。
_VERSION_DIR_NAME_RE = re.compile(r"^v?\d+(?:\.\d+)*(?:[-+][0-9A-Za-z][0-9A-Za-z.\-]*)?$")

#: 判据之二的向下扫描层数：目录自身 + 一层直接子目录。
#: 真实布局是 ``EasyTier/<版本>/easytier-windows-x86_64/easytier-core.exe`` —— 两层够用；
#: 不做全树递归（既慢，又会把"恰好含同名文件"的无关目录卷进来）。
_EASYTIER_CORE_NAME = "easytier-core.exe"

HOST_VIRTUAL_IP = "10.114.51.41"
MC_MULTICAST_GROUP = ("224.0.2.60", 4445)
MC_MULTICAST_GROUP_V6 = ("ff75:230::60", 4445)
LOOPBACK = "127.0.0.1"

#: 局域网广播报文里的 MOTD / 端口标记。原来是 ``BroadcastListener`` 的类属性，
#: 搬成模块级常量以便 :func:`parse_broadcast_message` 复用；类上保留同名别名，
#: 因此 ``BroadcastListener.MOTD_RE`` 依旧存在且与这两个是同一对象。
MOTD_RE = re.compile(r"\[MOTD\](.*?)\[/MOTD\]")
AD_RE = re.compile(r"\[AD\](\d+)\[/AD\]")


class LobbyState(enum.Enum):
    IDLE = "idle"
    INITIALIZING = "initializing"
    INITIALIZED = "initialized"
    DISCOVERING = "discovering"
    CREATING = "creating"
    JOINING = "joining"
    CONNECTED = "connected"
    LEAVING = "leaving"
    ERROR = "error"


def _get_vendor() -> str:
    try:
        fmcl_ver = _get_fmcl_version()
    except Exception:
        fmcl_ver = "unknown"
    return f"FMCL {fmcl_ver}, EasyTier {EASYTIER_VERSION}"


def _get_random_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((LOOPBACK, 0))
        return s.getsockname()[1]


def _get_machine_id() -> str:
    raw = (platform.node() + os.environ.get("USERNAME", "")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:16]


def _get_easytier_base_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    return Path(local_app_data) / "FMCL" / "EasyTier" / EASYTIER_VERSION / "easytier-windows-x86_64"


class LobbyInfo:
    __slots__ = ("full_code", "network_name", "network_secret", "minecraft_port", "is_host")

    def __init__(
        self, full_code: str, network_name: str, network_secret: str, minecraft_port: int = 0, is_host: bool = False
    ):
        self.full_code = full_code
        self.network_name = network_name
        self.network_secret = network_secret
        self.minecraft_port = minecraft_port
        self.is_host = is_host

    def __repr__(self) -> str:
        return f"LobbyInfo(full_code={self.full_code!r}, network_name={self.network_name!r})"


class LobbyCodeGenerator:
    CHARS = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    FULL_CODE_PREFIX = "U/"
    NETWORK_NAME_PREFIX = "scaffolding-mc-"
    BASE_VAL = 34
    DATA_LENGTH = 16
    HYPHEN_COUNT = 3
    PAYLOAD_LENGTH = DATA_LENGTH + HYPHEN_COUNT
    CODE_LENGTH = PAYLOAD_LENGTH + len(FULL_CODE_PREFIX)

    _ENCODING_MAX_VALUE = pow(BASE_VAL, DATA_LENGTH)
    _CHAR_TO_VALUE: Dict[str, int] = {}

    @classmethod
    def _init_char_map(cls):
        if cls._CHAR_TO_VALUE:
            return
        for i, ch in enumerate(cls.CHARS):
            cls._CHAR_TO_VALUE[ch] = i
            cls._CHAR_TO_VALUE[ch.lower()] = i
        cls._CHAR_TO_VALUE["I"] = 1
        cls._CHAR_TO_VALUE["i"] = 1
        cls._CHAR_TO_VALUE["O"] = 0
        cls._CHAR_TO_VALUE["o"] = 0

    @classmethod
    def generate(cls) -> LobbyInfo:
        cls._init_char_map()
        random_bytes = secrets.token_bytes(16)
        random_value = int.from_bytes(random_bytes, "big")
        value_in_range = random_value % cls._ENCODING_MAX_VALUE
        remainder = value_in_range % 7
        valid_value = value_in_range - remainder
        return cls._encode(valid_value)

    @classmethod
    def _encode(cls, value: int) -> LobbyInfo:
        temp_chars = []
        val = value
        for _ in range(cls.DATA_LENGTH):
            temp_chars.append(cls.CHARS[val % cls.BASE_VAL])
            val //= cls.BASE_VAL
        payload = (
            "".join(temp_chars[0:4])
            + "-"
            + "".join(temp_chars[4:8])
            + "-"
            + "".join(temp_chars[8:12])
            + "-"
            + "".join(temp_chars[12:16])
        )
        full_code = cls.FULL_CODE_PREFIX + payload
        network_name = cls.NETWORK_NAME_PREFIX + payload[:9]
        network_secret = payload[10:]
        return LobbyInfo(full_code=full_code, network_name=network_name, network_secret=network_secret)

    @classmethod
    def try_parse(cls, input_str: str) -> Optional[LobbyInfo]:
        cls._init_char_map()
        if not input_str or not input_str.upper().startswith(cls.FULL_CODE_PREFIX):
            return None
        upper = input_str.upper()
        if len(upper) != cls.CODE_LENGTH:
            return None
        payload = upper[len(cls.FULL_CODE_PREFIX) :]
        values = []
        for i, ch in enumerate(payload):
            if ch == "-":
                if i not in (4, 9, 14):
                    return None
                continue
            if ch not in cls._CHAR_TO_VALUE:
                return None
            values.append(cls._CHAR_TO_VALUE[ch])
        if len(values) != cls.DATA_LENGTH:
            return None
        value = 0
        for v in reversed(values):
            value = value * cls.BASE_VAL + v
        if value % 7 != 0:
            return None
        network_name = cls.NETWORK_NAME_PREFIX + payload[:9]
        network_secret = payload[10:]
        return LobbyInfo(full_code=upper, network_name=network_name, network_secret=network_secret)

    @classmethod
    def try_parse_terracotta(cls, input_str: str) -> Optional[LobbyInfo]:
        cls._init_char_map()
        code = input_str.strip().upper()
        if not code or len(code) < 9:
            return None

        code = code.replace("I", "1").replace("O", "0")
        sections = code.split("-")
        if len(sections) != 5:
            return None

        code_str = code.replace("-", "")
        if len(code_str) != 25:
            return None

        value = 0
        checking = 0
        for i in range(24):
            ch = code_str[i]
            if ch not in cls._CHAR_TO_VALUE:
                return None
            j = cls._CHAR_TO_VALUE[ch]
            value += j * pow(cls.BASE_VAL, i)
            checking = (checking + j) % cls.BASE_VAL

        if code_str[24] not in cls._CHAR_TO_VALUE:
            return None
        if checking != cls._CHAR_TO_VALUE[code_str[24]]:
            return None

        port = value % 65536
        if port < 100:
            return None

        network_name = "terracotta-mc-" + code_str[:15].lower()
        network_secret = code_str[15:25].lower()
        return LobbyInfo(full_code=code, network_name=network_name, network_secret=network_secret, minecraft_port=port)


class ScaffoldingServer:
    """轻量级 Scaffolding 信令服务器，兼容 PCL-CE / Terracotta(HMCL) 客户端"""

    _SUPPORTED_PROTOCOLS = ("c:ping", "c:protocols", "c:server_port", "c:player_ping", "c:player_profiles_list")

    _PLAYER_TIMEOUT = 10.0
    _CLEANUP_INTERVAL = 5.0

    def __init__(self, mc_port: int, player_name: str = "Host"):
        self._mc_port = mc_port
        self._port: int = 0
        self._server: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._cleanup_thread: Optional[threading.Thread] = None
        self._guests: Dict[str, dict] = {}
        self._guests_lock = threading.Lock()
        self._host_mid = _get_machine_id()
        vendor = _get_vendor()
        self._host_profile = {"name": player_name, "machine_id": self._host_mid, "vendor": vendor, "kind": "HOST"}
        self.on_profiles_changed: Optional[Callable[[list], None]] = None

    @property
    def port(self) -> int:
        return self._port

    @property
    def all_profiles(self) -> list:
        with self._guests_lock:
            return [self._host_profile] + list(self._guests.values())

    def start(self) -> int:
        if self._running:
            return self._port
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind(("0.0.0.0", 0))
            self._server.listen(16)
            self._port = self._server.getsockname()[1]
            self._running = True
            self._thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._thread.start()
            self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
            self._cleanup_thread.start()
            logger.info(f"Scaffolding server started on port {self._port}")
            return self._port
        except Exception as e:
            logger.error(f"Failed to start Scaffolding server: {e}")
            self._server = None
            return 0

    def stop(self):
        self._running = False
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=3)
        with self._guests_lock:
            self._guests.clear()
        logger.info("Scaffolding server stopped")

    def _accept_loop(self):
        while self._running and self._server:
            try:
                self._server.settimeout(1.0)
                client, addr = self._server.accept()
                threading.Thread(target=self._handle_client, args=(client, addr), daemon=True).start()
            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    time.sleep(0.1)

    def _cleanup_loop(self):
        while self._running:
            try:
                time.sleep(self._CLEANUP_INTERVAL)
                if not self._running:
                    break

                now = time.monotonic()
                removed = []
                with self._guests_lock:
                    for mid, guest in list(self._guests.items()):
                        last_seen = guest.get("last_seen", 0)
                        if now - last_seen > self._PLAYER_TIMEOUT:
                            removed.append(guest)
                            del self._guests[mid]

                if removed:
                    for guest in removed:
                        logger.info("ScaffoldingServer: player '%s' timed out and was removed", guest.get("name", "?"))
                    self._notify_profiles_changed()
            except Exception as e:
                if self._running:
                    logger.warning(f"ScaffoldingServer cleanup error: {e}")

    def _notify_profiles_changed(self):
        cb = self.on_profiles_changed
        if cb:
            try:
                cb(self.all_profiles)
            except Exception as e:
                logger.error(f"ScaffoldingServer profiles_changed callback error: {e}")

    def _handle_client(self, client: socket.socket, addr):
        try:
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            buf = bytearray()
            while self._running:
                try:
                    data = client.recv(65536)
                    if not data:
                        break
                    buf.extend(data)
                    while self._try_process_frame(client, buf):
                        pass
                except socket.timeout:
                    continue
                except Exception:
                    break
        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass

    def _try_process_frame(self, client: socket.socket, buf: bytearray) -> bool:
        if len(buf) < 1:
            return False
        type_len = buf[0]
        if type_len == 0 or type_len > 128:
            return False
        header_size = 1 + type_len + 4
        if len(buf) < header_size:
            return False
        type_str = buf[1 : 1 + type_len].decode("utf-8")
        body_len = struct.unpack(">I", buf[1 + type_len : header_size])[0]
        if body_len > 65536:
            return False
        total_size = header_size + body_len
        if len(buf) < total_size:
            return False
        body = bytes(buf[header_size:total_size])
        del buf[:total_size]

        status, resp_body = self._handle_request(type_str, body)
        self._send_response(client, status, resp_body)
        return True

    @staticmethod
    def _send_response(client: socket.socket, status: int, body: bytes):
        header = struct.pack(">BI", status & 0xFF, len(body))
        try:
            client.sendall(header + body)
        except Exception:
            pass

    def _handle_request(self, type_str: str, body: bytes) -> tuple:
        try:
            if type_str == "c:player_ping":
                return self._handle_player_ping(body)
            elif type_str == "c:player_profiles_list":
                return self._handle_player_profiles_list()
            elif type_str == "c:server_port":
                return self._handle_server_port()
            elif type_str == "c:protocols":
                protocols = "\0".join(self._SUPPORTED_PROTOCOLS).encode("ascii")
                return 0, protocols
            elif type_str == "c:ping":
                return 0, bytes(body)
            else:
                logger.debug(f"Unknown Scaffolding request: {type_str}")
                return 255, b"Requested protocol hasn't been implemented."
        except Exception as e:
            logger.debug(f"Scaffolding handler error ({type_str}): {e}")
            return 1, b""

    def _handle_player_ping(self, body: bytes) -> tuple:
        try:
            info = json.loads(body)
            if not info or not isinstance(info, dict):
                return 32, b""
            name = info.get("name")
            mid = info.get("machine_id")
            vendor = info.get("vendor")
            if not name or not mid or not vendor:
                return 32, b""
            if mid == self._host_mid:
                logger.warning("ScaffoldingServer: guest machine_id conflicts with host, rejected")
                return 32, b""
            mid_existed = False
            with self._guests_lock:
                mid_existed = mid in self._guests
                now = time.monotonic()
                guest_info = {
                    "name": name,
                    "machine_id": mid,
                    "vendor": vendor,
                    "kind": "GUEST",
                    "last_seen": now,
                }
                self._guests[mid] = guest_info
            if not mid_existed:
                logger.info("ScaffoldingServer: new player '%s' connected", name)
                self._notify_profiles_changed()
        except Exception:
            return 32, b""
        return 0, b""

    def _handle_player_profiles_list(self) -> tuple:
        body = json.dumps(self.all_profiles, ensure_ascii=False).encode("utf-8")
        return 0, body

    def _handle_server_port(self) -> tuple:
        port_bytes = struct.pack(">H", self._mc_port & 0xFFFF)
        return 0, port_bytes


class ScaffoldingClient:
    _HEARTBEAT_INTERVAL = 5.0
    _MAX_HEARTBEAT_FAILURES = 3

    def __init__(self, host: str, port: int, player_name: str, machine_id: str, vendor: str):
        self._host = host
        self._port = port
        self._player_name = player_name
        self._machine_id = machine_id
        self._vendor = vendor
        self._sock: Optional[socket.socket] = None
        self._lock = threading.Lock()
        self._running = False
        self._heartbeat_thread: Optional[threading.Thread] = None
        self._profiles: List[dict] = []
        self._consecutive_failures = 0
        self._last_latency_ms: int = 0
        self.on_server_shutdown: Optional[Callable[[], None]] = None
        self.on_player_list_changed: Optional[Callable[[list], None]] = None
        self.on_heartbeat: Optional[Callable[[list, int], None]] = None
        self._last_machine_ids: set = set()

    @property
    def profiles(self) -> list:
        with self._lock:
            return list(self._profiles)

    @property
    def latency_ms(self) -> int:
        return self._last_latency_ms

    @property
    def is_connected(self) -> bool:
        return self._running and self._sock is not None

    def connect(self) -> bool:
        if self._running:
            return True
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._sock.settimeout(10)
            self._sock.connect((self._host, self._port))
            self._sock.settimeout(30)
            self._running = True
            self._consecutive_failures = 0
            logger.info(f"ScaffoldingClient connected to {self._host}:{self._port}")

            if not self._verify_server():
                raise ConnectionError("Server did not pass Scaffolding handshake (c:ping fingerprint)")

            self._send_ping()

            self._heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            self._heartbeat_thread.start()
            logger.info("ScaffoldingClient heartbeat started")
            return True
        except Exception as e:
            logger.error(f"ScaffoldingClient connect failed: {e}")
            self._running = False
            self._close_socket()
            return False

    def _verify_server(self) -> bool:
        fingerprint = secrets.token_bytes(16)
        try:
            status, body = self._send_request("c:ping", fingerprint)
            if status != 0 or body != fingerprint:
                logger.warning(
                    f"ScaffoldingClient: server fingerprint mismatch "
                    f"(status={status}, body_len={len(body)})"
                )
                return False
            return True
        except Exception as e:
            logger.warning(f"ScaffoldingClient: server verification failed: {e}")
            return False

    def disconnect(self):
        self._running = False
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            self._heartbeat_thread.join(timeout=3)
        self._heartbeat_thread = None
        self._close_socket()
        with self._lock:
            self._profiles.clear()
        self._last_machine_ids.clear()
        self._last_latency_ms = 0
        self.on_player_list_changed = None
        self.on_server_shutdown = None
        self.on_heartbeat = None
        logger.info("ScaffoldingClient disconnected")

    def get_server_port(self, retries: int = 3, delay: float = 2.0) -> Optional[int]:
        for attempt in range(retries):
            try:
                status, body = self._send_request("c:server_port", None)
                if status != 0:
                    logger.warning(f"ScaffoldingClient c:server_port returned status {status}")
                elif body and len(body) >= 2:
                    return struct.unpack(">H", body[:2])[0]
            except Exception as e:
                logger.warning(f"ScaffoldingClient get_server_port failed: {e}")
            if attempt < retries - 1:
                time.sleep(delay)
        return None

    def _send_ping(self):
        body = {"name": self._player_name, "machine_id": self._machine_id, "vendor": self._vendor}
        self._send_request("c:player_ping", body)

    def _fetch_profiles(self):
        try:
            _, body = self._send_request("c:player_profiles_list", None)
            if body:
                profiles = json.loads(body.decode("utf-8"))
                with self._lock:
                    self._profiles = profiles if isinstance(profiles, list) else []
                current_ids = {p.get("machine_id", "") for p in self._profiles if p.get("machine_id")}
                if current_ids != self._last_machine_ids:
                    self._last_machine_ids = current_ids
                    if self.on_player_list_changed:
                        try:
                            self.on_player_list_changed(self._profiles)
                        except Exception as e:
                            logger.error(f"ScaffoldingClient player_list_changed callback error: {e}")
        except Exception as e:
            logger.warning(f"ScaffoldingClient fetch profiles failed: {e}")

    def _heartbeat_loop(self):
        while self._running:
            try:
                time.sleep(self._HEARTBEAT_INTERVAL)
                if not self._running:
                    break
                t0 = time.monotonic()
                self._send_ping()
                self._fetch_profiles()
                self._last_latency_ms = int((time.monotonic() - t0) * 1000)
                if self.on_heartbeat:
                    try:
                        self.on_heartbeat(self._profiles, self._last_latency_ms)
                    except Exception as e:
                        logger.error(f"ScaffoldingClient heartbeat callback error: {e}")
                self._consecutive_failures = 0
            except Exception as e:
                self._consecutive_failures += 1
                logger.warning(
                    f"ScaffoldingClient heartbeat error ({self._consecutive_failures}/{self._MAX_HEARTBEAT_FAILURES}): {e}"
                )
                if self._consecutive_failures >= self._MAX_HEARTBEAT_FAILURES:
                    logger.warning("ScaffoldingClient: server appears to have shut down")
                    self._running = False
                    if self.on_server_shutdown:
                        try:
                            self.on_server_shutdown()
                        except Exception as cb_e:
                            logger.error(f"ScaffoldingClient server_shutdown callback error: {cb_e}")
                    break
                if not self._running:
                    break
                time.sleep(1)

    def _send_request(self, type_str: str, body: Any) -> Tuple[int, bytes]:
        if not self._sock or not self._running:
            raise ConnectionError("Not connected")

        type_bytes = type_str.encode("utf-8")
        if len(type_bytes) > 255:
            raise ValueError("Request type too long")

        if isinstance(body, dict):
            body_bytes = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif body is None:
            body_bytes = b""
        elif isinstance(body, bytes):
            body_bytes = body
        elif isinstance(body, (bytearray, memoryview)):
            body_bytes = bytes(body)
        else:
            raise TypeError(f"Unsupported request body type: {type(body)}")

        header = struct.pack(">B", len(type_bytes)) + type_bytes + struct.pack(">I", len(body_bytes))
        frame = header + body_bytes

        with self._lock:
            self._sock.sendall(frame)

            raw = bytearray()
            while len(raw) < 5:
                chunk = self._sock.recv(5 - len(raw))
                if not chunk:
                    raise ConnectionError("Connection closed")
                raw.extend(chunk)

            status = raw[0]
            body_len = struct.unpack(">I", raw[1:5])[0]

            body_data = bytearray()
            while len(body_data) < body_len:
                chunk = self._sock.recv(body_len - len(body_data))
                if not chunk:
                    raise ConnectionError("Connection closed")
                body_data.extend(chunk)

            return status, bytes(body_data)

    def _close_socket(self):
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None


class EasyTierManager:
    def __init__(
        self,
        base_dir: Path,
        *,
        popen: Optional[Callable[..., Any]] = None,
        run: Optional[Callable[..., Any]] = None,
        urlopen: Optional[Callable[..., Any]] = None,
        relay_nodes: Optional[List[str]] = None,
    ):
        # 外部依赖全部**可注入**（默认就是改造前用的那两个真实实现），
        # 便于离线测试：不启动 easytier-core.exe、不发任何 HTTP 请求。
        # relay_nodes 非 None 时直接充当已预取的动态节点缓存，
        # 从而不会触发 _ensure_relay_prefetch 的后台线程。
        self._popen = popen if popen is not None else subprocess.Popen
        self._run = run if run is not None else subprocess.run
        self._urlopen = urlopen if urlopen is not None else urllib.request.urlopen
        self._base_dir = base_dir
        self._core_path = base_dir / "easytier-core.exe"
        self._cli_path = base_dir / "easytier-cli.exe"
        self._packet_dll = base_dir / "Packet.dll"
        self._process: Optional[subprocess.Popen] = None
        self._rpc_port: int = 0
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._scf_server: Optional[ScaffoldingServer] = None
        self._relay_nodes_lock = threading.Lock()
        self._relay_nodes_cache: Optional[List[str]] = (
            list(relay_nodes) if relay_nodes is not None else None
        )
        self._relay_prefetch_started = False
        #: 上一次 ``download()`` 之后算出来的"看起来是 EasyTier 旧版本"的目录清单。
        #: **服务不删它们**（D-83）：由调用方（界面，主线程）问过用户之后再调
        #: :meth:`remove_stale_versions`。没有调用方的确认就一个都不会被删。
        self.last_stale_version_dirs: List[Path] = []
        #: 上一次 :meth:`remove_stale_versions` 里删失败的项（路径 -> 原因）。
        #: 失败同时会写 error 日志 —— 这个字段只是让调用方也能拿到，不是唯一出口。
        self.last_remove_failures: List[Tuple[Path, str]] = []

    def _ensure_relay_prefetch(self):
        if self._relay_nodes_cache is None:
            with self._relay_nodes_lock:
                if self._relay_prefetch_started:
                    return
                self._relay_prefetch_started = True
            threading.Thread(target=self._prefetch_relay_nodes, daemon=True).start()

    def _prefetch_relay_nodes(self):
        dynamic = self._fetch_dynamic_nodes(self._urlopen)
        with self._relay_nodes_lock:
            self._relay_nodes_cache = dynamic
        if not dynamic:
            logger.warning(
                "EasyTier public relay nodes are currently unavailable; "
                "cross-network connections may fail until nodes recover"
            )
        else:
            logger.info(f"Relay node prefetch completed with {len(dynamic)} dynamic nodes")

    @property
    def is_installed(self) -> bool:
        return self._core_path.exists() and self._cli_path.exists() and self._packet_dll.exists()

    @property
    def is_running(self) -> bool:
        return self._running and self._process is not None and self._process.poll() is None

    @property
    def pid(self) -> Optional[int]:
        return self._process.pid if self._process else None

    @property
    def rpc_port(self) -> int:
        return self._rpc_port

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def precheck(self) -> int:
        if not self.is_installed:
            logger.error("EasyTier 不存在或不完整")
            return 1
        return 0

    @staticmethod
    def _speed_test_download_urls(urls: List[str], ua: str, opener=None) -> List[str]:
        """并发对各镜像发起 Range 探活+测速，按首块到达耗时升序返回可用镜像；
        全部不可用时原序返回（交由 download 逐个重试并报错）。"""
        results: Dict[int, Tuple[Optional[float], str]] = {}
        lock = threading.Lock()

        def probe(index: int, url: str):
            try:
                start = time.monotonic()
                req = urllib.request.Request(url, headers={"User-Agent": ua, "Range": "bytes=0-2047"})
                with (opener or urllib.request.urlopen)(req, timeout=_EASYTIER_SPEED_TEST_TIMEOUT) as resp:
                    resp.read(2048)
                elapsed = time.monotonic() - start
                with lock:
                    results[index] = (elapsed, url)
            except Exception as e:
                logger.debug(f"Speed test failed for {url}: {e}")
                with lock:
                    results[index] = (None, url)

        threads = [
            threading.Thread(target=probe, args=(i, url), daemon=True) for i, url in enumerate(urls)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=_EASYTIER_SPEED_TEST_TIMEOUT + 3)

        alive = [(elapsed, url) for elapsed, url in results.values() if elapsed is not None]
        if not alive:
            logger.warning("No EasyTier download mirror responded to speed test; trying in order")
            return list(urls)
        alive.sort(key=lambda item: item[0])
        logger.info("EasyTier mirror speed ranking: " + "; ".join(f"{url} ({elapsed:.2f}s)" for elapsed, url in alive))
        return [url for _, url in alive]

    def download(self, on_progress=None) -> bool:
        # D-83（任务 1.24）：本次下载**不再删除任何目录**。清空上一次的清单，
        # 装完只算/只回传"看起来是旧版本"的清单（见 stale_version_dirs 的判据）。
        self.last_stale_version_dirs = []
        self.last_remove_failures = []
        self._base_dir.mkdir(parents=True, exist_ok=True)
        version = EASYTIER_VERSION
        urls = [u.format(version=version) for u in EASYTIER_DOWNLOAD_URLS]
        zip_path = self._base_dir / f"easytier-windows-x86_64-{version}.zip"
        downloaded = False
        last_error = None

        ua = f"FMCL/{_get_fmcl_version()} (Windows; {platform.machine()})"
        if on_progress:
            on_progress("正在测速选择最快的下载镜像...")
        url_order = self._speed_test_download_urls(urls, ua, self._urlopen)
        for url in url_order:
            try:
                logger.info(f"Downloading EasyTier from {url}")
                if on_progress:
                    on_progress(f"正在下载 EasyTier v{version}...")
                req = urllib.request.Request(url, headers={"User-Agent": ua})
                with self._urlopen(req, timeout=60) as resp:
                    zip_path.write_bytes(resp.read())
                downloaded = True
                break
            except Exception as e:
                last_error = str(e)
                logger.warning(f"Failed to download from {url}: {e}")
                continue

        if not downloaded:
            logger.error(f"All EasyTier download mirrors failed: {last_error}")
            return False

        try:
            if on_progress:
                on_progress("正在解压 EasyTier...")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(str(self._base_dir))
            zip_path.unlink(missing_ok=True)

            if not self.is_installed:
                self._flatten_extracted_dir()
                if not self.is_installed:
                    logger.error("EasyTier extraction succeeded but files are missing")
                    return False

            self.last_stale_version_dirs = self.stale_version_dirs()
            if self.last_stale_version_dirs:
                names = ", ".join(str(p) for p in self.last_stale_version_dirs)
                logger.info(
                    f"发现 {len(self.last_stale_version_dirs)} 个 EasyTier 旧版本目录，"
                    f"等界面确认后再删（服务不自动删）: {names}"
                )
            logger.info("EasyTier installed successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to extract EasyTier: {e}")
            zip_path.unlink(missing_ok=True)
            return False

    def _flatten_extracted_dir(self):
        """把 zip 里多套的一层子目录里的三个文件搬到 ``_base_dir``。

        任务 1.24 修正（D-107）：**只在子目录搬空之后才删它**。旧实现不问内容、
        无条件 ``shutil.rmtree(sub_path, ignore_errors=True)`` —— 这一层子目录里
        **任何没被搬走的东西**（同一个 zip 带的 DLL、说明文档、附加工具……）都会被
        一起静默销毁。现在：搬完还有东西就**原样保留**，并把"里面还剩什么"写进日志。
        """
        target_files = {"easytier-core.exe", "easytier-cli.exe", "Packet.dll"}
        for entry in self._base_dir.iterdir():
            if entry.is_dir():
                sub_path = entry
                for target in target_files:
                    src = sub_path / target
                    if src.exists():
                        try:
                            shutil.move(str(src), str(self._base_dir / target))
                        except Exception as e:
                            logger.warning(f"Failed to move {target}: {e}")

                # D-107：先看空不空，再决定删不删。删失败也不再静默 —— 记 warning，
                # 目录留着（留着最多是占点空间，删错了是数据丢失）。
                try:
                    leftovers = sorted(p.name for p in sub_path.iterdir())
                except OSError as e:
                    logger.warning(f"无法列出解压子目录 {sub_path}，已保留不删: {e}")
                    return
                if leftovers:
                    shown = ", ".join(leftovers[:20])
                    more = "" if len(leftovers) <= 20 else f" 等 {len(leftovers)} 项"
                    logger.info(
                        f"解压子目录 {sub_path} 里还有本次未搬走的内容（{shown}{more}），"
                        f"按 D-107 修正保留不删"
                    )
                else:
                    try:
                        sub_path.rmdir()
                    except OSError as e:
                        logger.warning(f"解压子目录已空但删除失败（保留）: {sub_path} —— {e}")
                break

    # ─── D-83：旧版本目录的"算清单 / 删清单"（**不再自动删**）─────────────
    #
    # 旧实现 `_cleanup_stale_versions` 会 shutil.rmtree 掉版本根下**所有**非当前
    # 版本目录：无提示、无备份、失败也静默（ignore_errors=True），而唯一调用点在
    # download() 里跑于 worker 线程（不能就地弹确认框）。任务 1.24 按
    # docs/refactor/09-phase1-execution-log.md §11.3 定的方向拆成两个函数：
    # 服务只"算清单"和"删显式传入的清单"，"要不要删"由界面在主线程问用户。

    @staticmethod
    def _looks_like_version_dir_name(name: str) -> bool:
        """目录名是否是版本号形态（D-83 判据之一）。"""
        return bool(_VERSION_DIR_NAME_RE.match(name))

    def _has_easytier_core(self, path: Path) -> bool:
        """目录自身或它的**直接子目录**里有没有 ``easytier-core.exe``（判据之二）。"""
        candidates = [path]
        try:
            candidates.extend(child for child in path.iterdir() if child.is_dir())
        except OSError:
            return False
        for candidate in candidates:
            try:
                if (candidate / _EASYTIER_CORE_NAME).is_file():
                    return True
            except OSError:
                continue
        return False

    def stale_version_dirs(self) -> List[Path]:
        """列出"看起来是 EasyTier 旧版本目录"的项。**纯读取：一个字节都不删。**

        真实布局（本机实测）::

            %LOCALAPPDATA%\\FMCL\\EasyTier\\<版本>\\easytier-windows-x86_64\\
                easytier-core.exe / easytier-cli.exe / Packet.dll

        也就是说"版本根"是 ``self._base_dir.parent.parent``（``…/FMCL/EasyTier``），
        它的**直接子目录**才是版本目录。入列判据 —— 下列**任一**成立：

        1. 目录名是版本号形态：``2.6.4`` / ``v2.6.4`` / ``2.5.0-terracotta.2``
           （正则 ``_VERSION_DIR_NAME_RE``）；或
        2. 目录自身或它的**一层直接子目录**里有 ``easytier-core.exe``
           （覆盖"zip 多套一层""用户手工解压进来"这类情况）。

        两条判据之外的一切（``backup`` / ``我的东西`` / 名字像版本但里面是别的东西
        且没有 easytier-core.exe）**一律不入列**——这是刻意保守：漏掉一个旧版本目录
        只是多占几 MB，误删一个用户目录是不可逆的数据丢失。

        另外三条硬保护：

        - 当前版本的目录（``name == EASYTIER_VERSION``）永不入列；
        - 当前安装目录 ``self._base_dir`` 自身、以及它的任何祖先永不入列；
        - 任何 ``OSError``（权限、路径过长、目录被删）都降级成"这一项不算"并记
          warning，绝不抛给调用方（它可能正跑在 worker 线程里）。
        """
        versions_root = self._base_dir.parent.parent
        try:
            if not versions_root.is_dir():
                return []
            entries = sorted(versions_root.iterdir())
        except OSError as e:
            logger.warning(f"EasyTier 版本根目录不可读，按「没有旧版本」处理: {versions_root} —— {e}")
            return []

        stale: List[Path] = []
        for entry in entries:
            try:
                if not entry.is_dir():
                    continue
                if entry.name == EASYTIER_VERSION:
                    continue
                if entry == self._base_dir or self._base_dir.is_relative_to(entry):
                    continue
                if not (self._looks_like_version_dir_name(entry.name) or self._has_easytier_core(entry)):
                    continue
            except OSError as e:
                logger.warning(f"检查 EasyTier 版本根下的目录失败，已跳过: {entry} —— {e}")
                continue
            stale.append(entry)
        return stale

    def remove_stale_versions(self, paths: Iterable[Path]) -> int:
        """删除**显式传入**的目录，返回成功删除的个数。

        与旧 ``_cleanup_stale_versions`` 的三点不同：

        - **不自己算清单**：只删调用方给的那些路径（服务不做"猜哪些该删"的决定）；
        - **不用 ``ignore_errors=True``**：每个路径逐个记日志（成功 info / 失败
          error），失败既写进 :attr:`last_remove_failures` 也不当成功计数；
        - **有拒绝条件**：路径不是目录、或它是当前安装目录 ``self._base_dir`` 的
          自身/祖先 → 拒绝并记 error，不算成功。

        其余异常不吞：``shutil.rmtree`` 抛出的非 ``OSError`` 会原样向上冒泡。
        """
        removed = 0
        failures: List[Tuple[Path, str]] = []
        for raw in paths:
            path = Path(raw)
            try:
                if path == self._base_dir or self._base_dir.is_relative_to(path):
                    raise ValueError("拒绝删除当前 EasyTier 安装目录或其祖先")
                if not path.is_dir():
                    raise FileNotFoundError("不是一个已存在的目录")
                shutil.rmtree(str(path))  # 刻意不传 ignore_errors：失败必须被看见
            except (OSError, ValueError) as e:
                reason = f"{type(e).__name__}: {e}"
                failures.append((path, reason))
                logger.error(f"删除 EasyTier 旧版本目录失败（未删除）: {path} —— {reason}")
                continue
            removed += 1
            logger.info(f"已删除 EasyTier 旧版本目录: {path}")
        self.last_remove_failures = failures
        return removed

    def _resolve_relay_nodes(self) -> list:
        """解析中继节点。此方法绝不阻塞调用线程：
        动态节点列表通过后台预取缓存获得，未就绪时仅使用内置 fallback。"""
        self._ensure_relay_prefetch()
        with self._relay_nodes_lock:
            dynamic_nodes = self._relay_nodes_cache
        nodes = []
        if dynamic_nodes:
            nodes.extend(dynamic_nodes)
        nodes.extend(["https://etnode.zkitefly.eu.org/node1", "https://etnode.zkitefly.eu.org/node2"])
        nodes.extend(["https://etnode.zkitefly.eu.org/-node1", "https://etnode.zkitefly.eu.org/-node2"])
        nodes.extend(["tcp://public.easytier.top:11010", "tcp://public2.easytier.cn:54321"])
        return nodes

    @staticmethod
    def _fetch_dynamic_nodes(opener=None) -> list:
        """获取公共中继节点，与陶瓦生态（HMCL/FCL）使用同一节点源：
        优先 terracotta 生态节点列表，随后回退 EasyTier uptime API。"""
        for source, fetcher in (
            ("terracotta node list", lambda: EasyTierManager._fetch_terracotta_nodes(opener)),
            ("uptime API", lambda: EasyTierManager._fetch_uptime_nodes(opener)),
        ):
            nodes = fetcher()
            if nodes:
                logger.info(f"Got {len(nodes)} dynamic relay nodes from {source}")
                return nodes
        return []

    @staticmethod
    def _fetch_terracotta_nodes(opener=None) -> list:
        try:
            req = urllib.request.Request(
                "https://terracotta.glavo.site/nodes",
                headers={"User-Agent": "FMCL/2.0"},
            )
            with (opener or urllib.request.urlopen)(req, timeout=6) as resp:
                data = json.loads(resp.read())
            if not isinstance(data, list):
                return []
            nodes = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                url = (item.get("url") or "").strip()
                if url and (url.startswith("tcp://") or url.startswith("udp://") or url.startswith("https://")):
                    nodes.append(url)
            return nodes
        except Exception as e:
            logger.debug(f"Failed to fetch terracotta node list: {e}")
            return []

    @staticmethod
    def _fetch_uptime_nodes(opener=None) -> list:
        try:
            req = urllib.request.Request(
                "https://uptime.easytier.cn/api/nodes?page=1&per_page=50&is_active=true",
                headers={"User-Agent": "FMCL/2.0"},
            )
            with (opener or urllib.request.urlopen)(req, timeout=8) as resp:
                data = json.loads(resp.read())
            items = data.get("data", {}).get("items", [])
            nodes = []
            for item in items[:10]:
                if item.get("is_active", False) is False:
                    continue
                if item.get("allow_relay", False) is False:
                    continue
                url = item.get("address", "")
                url = (url or "").strip()
                if url and (url.startswith("tcp://") or url.startswith("udp://") or url.startswith("https://")):
                    nodes.append(url)
            return nodes
        except Exception as e:
            logger.debug(f"Failed to fetch uptime node list: {e}")
            return []

    def launch(
        self,
        lobby: LobbyInfo,
        as_host: bool,
        on_output=None,
        on_exited=None,
        player_name: str = "Host",
        latency_first: bool = True,
    ) -> int:
        if self._running:
            logger.warning("EasyTier is already running")
            return 1

        if self.precheck() != 0:
            return 1

        self._rpc_port = _get_random_port()

        args = [
            str(self._core_path),
            "--no-tun",
            "--multi-thread",
            "--enable-kcp-proxy",
            "--enable-quic-proxy",
            "--use-smoltcp",
            "--disable-sym-hole-punching",
            "--disable-ipv6",
            "--encryption-algorithm",
            "aes-gcm",
            "--default-protocol",
            "tcp",
            "--compression",
            "zstd",
            "--network-name",
            lobby.network_name,
            "--network-secret",
            lobby.network_secret,
            "--machine-id",
            _get_machine_id(),
            "--rpc-portal",
            f"127.0.0.1:{self._rpc_port}",
            "--private-mode",
            "true",
            "--p2p-only",
        ]

        if latency_first:
            args.append("--latency-first")

        if as_host:
            self._scf_server = ScaffoldingServer(lobby.minecraft_port, player_name)
            scf_port = self._scf_server.start()
            if scf_port <= 0:
                logger.error("ScaffoldingServer failed to start, aborting")
                return 1
            logger.info(f"ScaffoldingServer started on port {scf_port}")
            args.extend(["-i", HOST_VIRTUAL_IP, "--hostname", f"scaffolding-mc-server-{scf_port}"])
            if lobby.minecraft_port > 0:
                args.extend(
                    [
                        "--tcp-whitelist",
                        str(scf_port),
                        "--udp-whitelist",
                        str(scf_port),
                        "--tcp-whitelist",
                        str(lobby.minecraft_port),
                        "--udp-whitelist",
                        str(lobby.minecraft_port),
                    ]
                )
            else:
                args.extend(["--tcp-whitelist", str(scf_port), "--udp-whitelist", str(scf_port)])
            args.extend(["-l", "tcp://0.0.0.0:0", "-l", "udp://0.0.0.0:0"])
        else:
            args.extend(
                [
                    "-d",
                    "--hostname",
                    secrets.token_hex(8),
                    "--tcp-whitelist",
                    "0",
                    "--udp-whitelist",
                    "0",
                    "-l",
                    "tcp://0.0.0.0:0",
                    "-l",
                    "udp://0.0.0.0:0",
                ]
            )

        relay_nodes = self._resolve_relay_nodes()
        for relay in relay_nodes:
            args.extend(["-p", relay])

        try:
            self._process = self._popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                cwd=str(self._base_dir),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._running = True
            logger.info(f"EasyTier launched with PID {self._process.pid}")
            self._reader_thread = threading.Thread(target=self._read_output, args=(on_output, on_exited), daemon=True)
            self._reader_thread.start()
            return 0
        except Exception as e:
            logger.error(f"Failed to launch EasyTier: {e}")
            self._running = False
            self._process = None
            self._cleanup_scf()
            return 1

    def _read_output(self, on_output, on_exited):
        # ⚠ 记录现状、疑为缺陷（D-90，docs/refactor/07-known-defects.md）：
        # 本方法由 launch() 起的 **reader 线程**执行，而界面侧传进来的
        # on_output 最终会走到 OnlineTabMixin._append_online_log —— 那个函数
        # 内部调 winfo_exists()/after()，等于工作线程直接进 UI 函数
        # （Tk 下不可靠，Qt 下会直接崩）。任务 1.5 只搬家不改逻辑，
        # 因此这里**原样保留**：投递仍由界面侧负责，服务只负责解析与批处理。
        proc = self._process
        if proc is None:
            return
        batch = []
        batch_size = 20

        def flush_batch():
            if batch and on_output:
                payload = "\n".join(batch)
                batch.clear()
                try:
                    on_output(payload)
                except Exception:
                    pass

        try:
            for line in iter(proc.stdout.readline, ""):
                if proc.poll() is not None and not line:
                    break
                stripped = line.strip()
                if stripped:
                    batch.append(stripped)
                    if len(batch) >= batch_size:
                        flush_batch()
        except Exception:
            pass
        finally:
            flush_batch()
            exit_code = proc.poll() if proc else -1
            self._running = False
            self._process = None
            if on_exited:
                on_exited(exit_code if exit_code is not None else -1)

    def stop(self):
        if not self._running or self._process is None:
            self._cleanup_scf()
            return
        logger.info(f"Stopping EasyTier (PID: {self._process.pid})")
        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait(timeout=3)
        except Exception as e:
            logger.warning(f"Error stopping EasyTier: {e}")
        finally:
            self._running = False
            self._process = None
            self._rpc_port = 0
            self._cleanup_scf()

    def stop_async(self):
        """非阻塞停止：在后台线程执行 stop()，避免阻塞 Tk 主线程。"""
        threading.Thread(target=self.stop, daemon=True).start()

    def add_port_forward(self, target_ip: str, target_port: int) -> Optional[int]:
        if not self._running or self._rpc_port == 0:
            return None
        local_port = _get_random_port()
        rules = [
            ("tcp", f"127.0.0.1:{local_port}"),
            ("udp", f"127.0.0.1:{local_port}"),
            ("tcp", f"[::]:{local_port}"),
            ("udp", f"[::]:{local_port}"),
        ]

        for attempt in range(3):
            success_count = 0
            for proto, local_addr in rules:
                try:
                    result = self._run(
                        [
                            str(self._cli_path),
                            "--rpc-portal",
                            f"127.0.0.1:{self._rpc_port}",
                            "port-forward",
                            "add",
                            proto,
                            local_addr,
                            f"{target_ip}:{target_port}",
                        ],
                        capture_output=True,
                        text=True,
                        errors="replace",
                        timeout=10,
                        cwd=str(self._base_dir),
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                    if result.returncode == 0:
                        success_count += 1
                    else:
                        err_msg = result.stderr.strip() or result.stdout.strip()
                        logger.warning(f"Port forward {proto} returned {result.returncode}: {err_msg}")
                except subprocess.TimeoutExpired:
                    logger.warning(f"Port forward {proto} timed out")
                except Exception as e:
                    logger.warning(f"Failed to add {proto} port forward: {e}")

            if success_count >= 2:
                logger.info(
                    f"Port forward to {target_ip}:{target_port} established "
                    f"({success_count}/4 rules, local_port={local_port}, attempt={attempt + 1})"
                )
                return local_port

            logger.warning(f"Port forward retry {attempt + 1}/3: only {success_count}/4 rules succeeded")
            if attempt < 2:
                time.sleep(3)

        logger.error(f"Port forward to {target_ip}:{target_port} failed after 3 attempts")
        return None

    def discover_host(self, timeout: float = 40.0) -> Optional[Tuple[str, int]]:
        if not self._running or self._rpc_port == 0:
            return None

        started = time.monotonic()
        sleep_before = min(timeout, 2.0)
        last_peer_count = -1
        while time.monotonic() - started < timeout:
            time.sleep(sleep_before)
            sleep_before = 2.0
            if not self._running or self._rpc_port == 0:
                return None
            try:
                proc = self._run(
                    [str(self._cli_path), "--rpc-portal", f"127.0.0.1:{self._rpc_port}", "-o", "json", "peer"],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=8,
                    cwd=str(self._base_dir),
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if proc.returncode != 0:
                    err_msg = proc.stderr.strip() or proc.stdout.strip()
                    logger.debug(f"peer command returned {proc.returncode}: {err_msg}")
                    continue
                output = proc.stdout + proc.stderr
                if not output.strip():
                    continue
                peers = json.loads(output)
                if not isinstance(peers, list):
                    continue
                if len(peers) != last_peer_count:
                    last_peer_count = len(peers)
                    logger.info(f"discover_host: {len(peers)} peers visible on the network")
                for peer in peers:
                    hostname = peer.get("hostname", "")
                    if hostname.startswith("scaffolding-mc-server-"):
                        port_str = hostname[len("scaffolding-mc-server-") :]
                        try:
                            scf_port = int(port_str)
                        except ValueError:
                            logger.warning(f"Invalid scf port in hostname: {hostname}")
                            continue
                        host_ip = peer.get("ipv4", "")
                        if not host_ip:
                            logger.warning(
                                "discover_host: host found but ipv4 is empty, "
                                "waiting for address assignment"
                            )
                            continue
                        logger.info(f"Discovered host: {host_ip}:{scf_port}")
                        return (host_ip, scf_port)
            except (subprocess.TimeoutExpired, json.JSONDecodeError, Exception) as e:
                logger.debug(f"discover_host retry: {e}")
        logger.warning("discover_host timed out")
        return None

    def _cleanup_scf(self):
        if self._scf_server:
            self._scf_server.stop()
            self._scf_server = None


class McBroadcastSimulator:
    def __init__(self):
        self._socket: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self, description: str, local_port: int):
        if self._running:
            return
        packet = f"[MOTD]{description}[/MOTD][AD]{local_port}[/AD]".encode("utf-8")
        try:
            self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._running = True
            self._thread = threading.Thread(target=self._broadcast_loop, args=(packet,), daemon=True)
            self._thread.start()
            logger.info(f"MC broadcast simulator started on port {local_port}")
        except Exception as e:
            logger.error(f"Failed to start MC broadcast simulator: {e}")
            self._running = False
            self._socket = None

    def _broadcast_loop(self, packet: bytes):
        addr = (LOOPBACK, MC_MULTICAST_GROUP[1])
        while self._running and self._socket:
            try:
                self._socket.sendto(packet, addr)
                time.sleep(1.5)
            except Exception:
                time.sleep(5)

    def stop(self):
        self._running = False
        if self._socket:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        logger.info("MC broadcast simulator stopped")


class McPing:
    """轻量级 Minecraft 服务器 Ping"""

    _PROTOCOL_VERSION = 767
    _DEFAULT_TIMEOUT = 3.0

    def __init__(self, host: str, port: int = 25565):
        self._host = host
        self._port = port

    def ping(self) -> Optional[Dict[str, Any]]:
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(self._DEFAULT_TIMEOUT)
            sock.connect((self._host, self._port))

            host_bytes = self._host.encode("utf-8")
            handshake = bytearray()
            _write_varint(handshake, 0x00)
            _write_varint(handshake, self._PROTOCOL_VERSION)
            _write_varint(handshake, len(host_bytes))
            handshake.extend(host_bytes)
            handshake.extend(struct.pack(">H", self._port))
            _write_varint(handshake, 1)

            packet = bytearray()
            _write_varint(packet, len(handshake))
            packet.extend(handshake)
            sock.sendall(packet)

            sock.sendall(b"\x01\x00")

            _read_varint(sock)
            _read_varint(sock)
            length = _read_varint(sock)
            data = bytearray()
            while len(data) < length:
                chunk = sock.recv(length - len(data))
                if not chunk:
                    break
                data.extend(chunk)
            sock.close()

            response = json.loads(data.decode("utf-8"))
            return response
        except Exception:
            return None


class BroadcastListener:
    """局域网 Minecraft 世界发现监听器"""

    #: 与模块级常量同一对象（解析逻辑已抽成 :func:`parse_broadcast_message`）
    MOTD_RE = MOTD_RE
    AD_RE = AD_RE

    def __init__(self, receive_local_only: bool = True):
        self._receive_local_only = receive_local_only
        self._sock: Optional[socket.socket] = None
        self._sock_v6: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.on_receive: Optional[Callable[[str, int], None]] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        logger.info("BroadcastListener started")

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
        if self._sock_v6:
            try:
                self._sock_v6.close()
            except Exception:
                pass
            self._sock_v6 = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        logger.info("BroadcastListener stopped")

    def _listen_loop(self):
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock.bind(("0.0.0.0", MC_MULTICAST_GROUP[1]))
            mreq = struct.pack("=4s4s", socket.inet_aton(MC_MULTICAST_GROUP[0]), socket.inet_aton("0.0.0.0"))
            self._sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except Exception:
            self._sock = None

        try:
            self._sock_v6 = socket.socket(socket.AF_INET6, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            self._sock_v6.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._sock_v6.bind(("::", MC_MULTICAST_GROUP_V6[1]))
            mreq_v6 = socket.inet_pton(socket.AF_INET6, MC_MULTICAST_GROUP_V6[0]) + struct.pack("@I", 0)
            self._sock_v6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_JOIN_GROUP, mreq_v6)
        except Exception:
            self._sock_v6 = None

        while self._running:
            try:
                if self._sock:
                    self._sock.settimeout(1.0)
                data, addr = None, None
                if self._sock:
                    try:
                        data, addr = self._sock.recvfrom(65536)
                    except socket.timeout:
                        pass
                if data is None and self._sock_v6:
                    try:
                        self._sock_v6.settimeout(0.5)
                        data, addr = self._sock_v6.recvfrom(65536)
                        if ":" in addr[0]:
                            addr = ("127.0.0.1", addr[1])
                    except socket.timeout:
                        pass
                if data is None:
                    continue

                message = data.decode("utf-8", errors="replace")
                parsed = parse_broadcast_message(message, self.MOTD_RE, self.AD_RE)
                if parsed is None:
                    continue
                port, motd = parsed
                if self._receive_local_only:
                    local_ips = _get_local_ips()
                    if addr[0] != "127.0.0.1" and addr[0] not in local_ips:
                        continue
                if self.on_receive:
                    try:
                        self.on_receive(port, motd)
                    except Exception as e:
                        logger.debug(f"BroadcastListener callback error: {e}")
            except Exception as e:
                if self._running:
                    logger.debug(f"BroadcastListener error: {e}")
                    time.sleep(1)


class GameWatcher:
    """监控主机 MC 实例是否仍在运行"""

    _CHECK_INTERVAL = 15.0
    _INITIAL_DELAY = 5.0

    def __init__(self, port: int):
        self._port = port
        self._running = False
        self._timer: Optional[threading.Timer] = None
        self.on_game_stopped: Optional[Callable[[], None]] = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._schedule_next()
        logger.info(f"GameWatcher started for port {self._port}")

    def stop(self):
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        logger.info("GameWatcher stopped")

    def _schedule_next(self):
        if not self._running:
            return
        self._timer = threading.Timer(
            self._CHECK_INTERVAL if hasattr(self, "_first_check_done") else self._INITIAL_DELAY, self._check
        )
        self._timer.daemon = True
        self._timer.start()
        self._first_check_done = True

    def _check(self):
        if not self._running:
            return
        pinger = McPing(LOOPBACK, self._port)
        result = pinger.ping()
        if result is None:
            logger.warning(f"GameWatcher: MC instance on port {self._port} appears to have stopped")
            self._running = False
            if self.on_game_stopped:
                try:
                    self.on_game_stopped()
                except Exception as e:
                    logger.error(f"GameWatcher callback error: {e}")
            return
        self._schedule_next()


def _write_varint(buf: bytearray, value: int):
    while True:
        byte = value & 0x7F
        value >>= 7
        if value != 0:
            byte |= 0x80
        buf.append(byte)
        if value == 0:
            break


def _read_varint(sock: socket.socket) -> int:
    value = 0
    shift = 0
    while True:
        data = sock.recv(1)
        if not data:
            raise ConnectionError("Connection closed")
        byte = data[0]
        value |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            break
        shift += 7
    return value


def _get_local_ips() -> List[str]:
    ips = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ips.append(info[4][0])
    except Exception:
        pass
    ips.append("127.0.0.1")
    return ips


class TcpPortForwarder:
    MAX_CONNECTIONS = 10

    def __init__(self, listen_port: int, target_host: str, target_port: int):
        self._listen_port = listen_port
        self._target_host = target_host
        self._target_port = target_port
        self._server: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._connections: List[socket.socket] = []
        self._lock = threading.Lock()

    def start(self):
        if self._running:
            return
        try:
            self._server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server.bind((LOOPBACK, self._listen_port))
            self._server.listen(5)
            self._running = True
            self._thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._thread.start()
            logger.info(
                f"TCP forwarder started: {LOOPBACK}:{self._listen_port} -> {self._target_host}:{self._target_port}"
            )
        except Exception as e:
            logger.error(f"Failed to start TCP forwarder: {e}")
            self._running = False
            self._server = None

    def _accept_loop(self):
        while self._running and self._server:
            try:
                self._server.settimeout(1.0)
                client, addr = self._server.accept()
                with self._lock:
                    if len(self._connections) >= self.MAX_CONNECTIONS:
                        client.close()
                        continue
                    self._connections.append(client)
                threading.Thread(target=self._forward, args=(client,), daemon=True).start()
            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    time.sleep(0.1)

    def _forward(self, client: socket.socket):
        remote = None
        try:
            client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            remote = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            remote.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            remote.settimeout(10)
            remote.connect((self._target_host, self._target_port))
            remote.settimeout(None)

            def _pump(src, dst):
                try:
                    while True:
                        data = src.recv(65536)
                        if not data:
                            break
                        dst.sendall(data)
                except Exception:
                    pass
                finally:
                    try:
                        src.close()
                    except Exception:
                        pass
                    try:
                        dst.shutdown(socket.SHUT_RDWR)
                    except Exception:
                        pass
                    try:
                        dst.close()
                    except Exception:
                        pass

            t1 = threading.Thread(target=_pump, args=(client, remote), daemon=True)
            t2 = threading.Thread(target=_pump, args=(remote, client), daemon=True)
            t1.start()
            t2.start()
            t1.join(timeout=300)
            t2.join(timeout=300)
        except Exception:
            pass
        finally:
            try:
                client.close()
            except Exception:
                pass
            if remote:
                try:
                    remote.close()
                except Exception:
                    pass
            with self._lock:
                if client in self._connections:
                    self._connections.remove(client)

    def stop(self):
        self._running = False
        if self._server:
            try:
                self._server.close()
            except Exception:
                pass
            self._server = None
        with self._lock:
            for conn in self._connections:
                try:
                    conn.close()
                except Exception:
                    pass
            self._connections.clear()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        logger.info("TCP forwarder stopped")


def _sort_player_list(profiles: list) -> list:
    host_list = [p for p in profiles if p.get("kind") == "HOST"]
    guest_list = [p for p in profiles if p.get("kind") != "HOST"]
    return host_list + guest_list


# ═══════════════════════════════════════════════════════════════════════
# 以下为**新增**代码（非搬运）：把原先埋在界面方法体里的纯逻辑切出来，
# 以及联机服务对象本身。逐条来源见报告"切缝"一节。
# ═══════════════════════════════════════════════════════════════════════


def parse_mc_port(raw: str) -> Tuple[Optional[int], str]:
    """把"主机端口"输入框的文本解析成 ``(端口, 用于报错的文本)``。

    逐字对应 ``OnlineTabMixin._on_create_lobby`` 原先内联的那段：空串回落到
    ``"25565"``；非整数或不在 ``1..65535`` 内时端口为 ``None``，调用方据此报
    ``online_invalid_port``。返回的第二个值是**回落之后**的文本 —— 原实现的错误
    消息用的就是它（``_("online_invalid_port", port=port_str)``）。
    """
    port_str = raw.strip()
    if not port_str:
        port_str = "25565"
    try:
        mc_port = int(port_str)
        if mc_port < 1 or mc_port > 65535:
            raise ValueError
    except ValueError:
        return None, port_str
    return mc_port, port_str


def parse_lobby_code(input_str: str) -> Optional[LobbyInfo]:
    """先按 FMCL ``U/`` 码解析，失败再按陶瓦 25 位码解析；都不行返回 ``None``。

    逐字对应 ``OnlineTabMixin._on_join_lobby`` 里原先的两连判。
    """
    lobby = LobbyCodeGenerator.try_parse(input_str)
    if lobby is None:
        lobby = LobbyCodeGenerator.try_parse_terracotta(input_str)
    return lobby


def format_member_lines(profiles: list) -> List[str]:
    """把成员档案渲染成"每行一条"的文本（不含"无成员"占位）。

    逐字对应 ``OnlineTabMixin._refresh_members`` 里原先的循环。图标字面量
    （``👑`` / ``👤``）与分隔符 ``" · "`` **原样保留** —— 界面可见行为不得改变。
    空列表返回空列表；"无成员"是 i18n 文案，仍由界面侧补。
    """
    lines = []
    for p in profiles:
        kind = p.get("kind", "?")
        name = p.get("name", "?")
        vendor = p.get("vendor", "?")
        icon = "👑" if kind == "HOST" else "👤"
        lines.append(f"{icon} {name} · {vendor}")
    return lines


def parse_broadcast_message(
    message: str, motd_re: Any = MOTD_RE, ad_re: Any = AD_RE
) -> Optional[Tuple[int, str]]:
    """解析一条局域网世界广播报文，返回 ``(端口, MOTD)``；没有 ``[AD]`` 段则返回 ``None``。

    逐字对应 ``BroadcastListener._listen_loop`` 原先内联的那四行：先取 MOTD、再取
    AD，**AD 缺失即丢弃**（哪怕 MOTD 在），MOTD 缺失时用 ``"Unknown"`` 兜底。
    两个正则默认取模块级常量（与 ``BroadcastListener.MOTD_RE`` / ``.AD_RE`` 同一对象），
    留参数只是为了测试时能直接喂正则。
    """
    motd_match = motd_re.search(message)
    ad_match = ad_re.search(message)
    if not ad_match:
        return None
    port = int(ad_match.group(1))
    return port, motd_match.group(1) if motd_match else "Unknown"


def lobby_state_transition(old: Optional[LobbyState], new: LobbyState) -> LobbyState:
    """大厅状态机的转移函数：返回**应当写入**的当前状态。

    逐字对应 ``OnlineTabMixin._set_lobby_state``：状态没变时不记日志、原样返回
    （原实现是直接 ``return`` 不赋值 —— 赋的还是同一个值，对外不可见）；
    变了就按 ``"Lobby state: a -> b"`` 记一条 info，再返回新状态。
    """
    if old is None:
        old = LobbyState.IDLE
    if old == new:
        return old
    logger.info(f"Lobby state: {old.value} -> {new.value}")
    return new


def is_platform_supported() -> bool:
    """联机页只在 Windows 可用（逐字对应 ``_build_online_tab_content`` 的首个判断）。"""
    return platform.system().lower() == "windows"


# ═══════════════════════════════════════════════════════════════════════
# 服务对象
# ═══════════════════════════════════════════════════════════════════════


class OnlineService(Service):
    """联机服务（``name = "online"``）。

    每个方法都是上文模块级函数 / 类的薄转发。保留实例方法这一层，是为了让界面侧
    统一写 ``self._online_service().xxx(...)``：阶段 2 接上 ``AppContext`` 之后只要
    把实例换成注册表里的那一个，调用点不用动。

    **不访问** ``self.ui`` / ``self.tasks`` / ``self.config``：本服务不弹窗，
    也不自己调度后台线程 —— 线程由界面侧的 ``_run_online_thread`` 起（与改造前
    一致），``EasyTierManager.start`` / ``ScaffoldingClient.connect`` 的内部线程
    是它们本来就有的。
    """

    name = "online"
    label = "联机"

    # ─── 纯逻辑转发 ──────────────────────────────────────────

    @staticmethod
    def parse_mc_port(raw: str) -> Tuple[Optional[int], str]:
        """转发 :func:`parse_mc_port`。"""
        return parse_mc_port(raw)

    @staticmethod
    def parse_lobby_code(input_str: str) -> Optional[LobbyInfo]:
        """转发 :func:`parse_lobby_code`（FMCL ``U/`` 码 → 陶瓦 25 位码）。"""
        return parse_lobby_code(input_str)

    @staticmethod
    def generate_lobby() -> LobbyInfo:
        """生成一个新大厅（转发 :meth:`LobbyCodeGenerator.generate`）。"""
        return LobbyCodeGenerator.generate()

    @staticmethod
    def format_member_lines(profiles: list) -> List[str]:
        """转发 :func:`format_member_lines`。"""
        return format_member_lines(profiles)

    @staticmethod
    def sort_player_list(profiles: list) -> list:
        """转发 :func:`_sort_player_list`（主机在前、访客在后）。"""
        return _sort_player_list(profiles)

    @staticmethod
    def parse_broadcast_message(
        message: str, motd_re: Any = None, ad_re: Any = None
    ) -> Optional[Tuple[int, str]]:
        """转发 :func:`parse_broadcast_message`（正则省略时用模块级默认值）。"""
        if motd_re is None or ad_re is None:
            return parse_broadcast_message(message)
        return parse_broadcast_message(message, motd_re, ad_re)

    @staticmethod
    def lobby_state_transition(old: Optional[LobbyState], new: LobbyState) -> LobbyState:
        """转发 :func:`lobby_state_transition`。"""
        return lobby_state_transition(old, new)

    @staticmethod
    def is_platform_supported() -> bool:
        """转发 :func:`is_platform_supported`。"""
        return is_platform_supported()

    # ─── 路径 / 标识 / 端口 ──────────────────────────────────

    @staticmethod
    def easytier_base_dir() -> Path:
        """转发 :func:`_get_easytier_base_dir`。"""
        return _get_easytier_base_dir()

    @staticmethod
    def machine_id() -> str:
        """转发 :func:`_get_machine_id`。"""
        return _get_machine_id()

    @staticmethod
    def vendor() -> str:
        """转发 :func:`_get_vendor`。"""
        return _get_vendor()

    @staticmethod
    def get_random_port() -> int:
        """转发 :func:`_get_random_port`（本地 bind 到一个空闲端口，不联网）。"""
        return _get_random_port()

    # ─── 工厂（界面侧据此拿具体实现，阶段 2 换成依赖注入）────

    @staticmethod
    def create_manager(base_dir: Optional[Path] = None, **kwargs: Any) -> EasyTierManager:
        """建一个 EasyTier 管理器；``base_dir`` 省略时用本机默认安装目录。

        ``kwargs`` 透传给 :class:`EasyTierManager`（``popen`` / ``run`` /
        ``urlopen`` / ``relay_nodes`` 四个注入缝）。
        """
        if base_dir is None:
            base_dir = _get_easytier_base_dir()
        return EasyTierManager(base_dir, **kwargs)

    @staticmethod
    def create_broadcast_listener(receive_local_only: bool = True) -> BroadcastListener:
        """建一个局域网世界发现监听器（转发 :class:`BroadcastListener`）。"""
        return BroadcastListener(receive_local_only)

    @staticmethod
    def create_broadcast_simulator() -> McBroadcastSimulator:
        """建一个 MC 广播模拟器（转发 :class:`McBroadcastSimulator`）。"""
        return McBroadcastSimulator()

    @staticmethod
    def create_ping(host: str, port: int = 25565) -> McPing:
        """建一个 MC Ping 器（转发 :class:`McPing`）。"""
        return McPing(host, port)

    @staticmethod
    def create_game_watcher(port: int) -> GameWatcher:
        """建一个"主机 MC 是否还在跑"的看门狗（转发 :class:`GameWatcher`）。"""
        return GameWatcher(port)

    @staticmethod
    def create_tcp_forwarder(
        listen_port: int, target_host: str, target_port: int
    ) -> TcpPortForwarder:
        """建一个本地 TCP 转发器（转发 :class:`TcpPortForwarder`）。"""
        return TcpPortForwarder(listen_port, target_host, target_port)

    @staticmethod
    def create_scaffolding_client(
        host: str, port: int, player_name: str, machine_id: str, vendor: str
    ) -> ScaffoldingClient:
        """建一个陶瓦信令客户端（转发 :class:`ScaffoldingClient`）。"""
        return ScaffoldingClient(host, port, player_name, machine_id, vendor)


__all__ = [
    "EASYTIER_DOWNLOAD_URLS",
    "EASYTIER_VERSION",
    "HOST_VIRTUAL_IP",
    "LOOPBACK",
    "MC_MULTICAST_GROUP",
    "MC_MULTICAST_GROUP_V6",
    "MOTD_RE",
    "AD_RE",
    "BroadcastListener",
    "EasyTierManager",
    "GameWatcher",
    "LobbyCodeGenerator",
    "LobbyInfo",
    "LobbyState",
    "McBroadcastSimulator",
    "McPing",
    "OnlineService",
    "ScaffoldingClient",
    "ScaffoldingServer",
    "TcpPortForwarder",
    "format_member_lines",
    "is_platform_supported",
    "lobby_state_transition",
    "parse_broadcast_message",
    "parse_lobby_code",
    "parse_mc_port",
]
