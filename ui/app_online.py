"""ModernApp 联机 Mixin - 陶瓦联机标签页相关方法"""
import os
import re
import io
import sys
import json
import time
import socket
import struct
import secrets
import hashlib
import zipfile
import shutil
import subprocess
import threading
import platform
import urllib.request
import urllib.error
from pathlib import Path
from typing import Optional, List, Dict, Tuple

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY, _get_fmcl_version
from ui.i18n import _


EASYTIER_VERSION = "2.5.0"
EASYTIER_BASE_URL = "https://github.com/EasyTier/EasyTier/releases/download/v{version}/easytier-windows-x86_64-v{version}.zip"
EASYTIER_DOWNLOAD_MIRRORS = [
    "https://staticassets.naids.com/resources/pclce/static/easytier/easytier-windows-x86_64-v{version}.zip",
    "https://s3.pysio.online/pcl2-ce/static/easytier/easytier-windows-x86_64-v{version}.zip",
]

HOST_VIRTUAL_IP = "10.114.51.41"
MC_MULTICAST_GROUP = ("224.0.2.60", 4445)
LOOPBACK = "127.0.0.1"


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

    def __init__(self, full_code: str, network_name: str, network_secret: str,
                 minecraft_port: int = 0, is_host: bool = False):
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
        cls._CHAR_TO_VALUE['I'] = 1
        cls._CHAR_TO_VALUE['i'] = 1
        cls._CHAR_TO_VALUE['O'] = 0
        cls._CHAR_TO_VALUE['o'] = 0

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
            "".join(temp_chars[0:4]) + "-"
            + "".join(temp_chars[4:8]) + "-"
            + "".join(temp_chars[8:12]) + "-"
            + "".join(temp_chars[12:16])
        )
        full_code = cls.FULL_CODE_PREFIX + payload
        network_name = cls.NETWORK_NAME_PREFIX + payload[:9]
        network_secret = payload[10:]
        return LobbyInfo(
            full_code=full_code,
            network_name=network_name,
            network_secret=network_secret,
        )

    @classmethod
    def try_parse(cls, input_str: str) -> Optional[LobbyInfo]:
        cls._init_char_map()
        if not input_str or not input_str.upper().startswith(cls.FULL_CODE_PREFIX):
            return None
        upper = input_str.upper()
        if len(upper) != cls.CODE_LENGTH:
            return None
        payload = upper[len(cls.FULL_CODE_PREFIX):]
        values = []
        for i, ch in enumerate(payload):
            if ch == '-':
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
        return LobbyInfo(
            full_code=upper,
            network_name=network_name,
            network_secret=network_secret,
        )


class ScaffoldingServer:
    """轻量级 Scaffolding 信令服务器，响应 PCL-CE 客户端请求"""

    def __init__(self, mc_port: int, player_name: str = "Host"):
        self._mc_port = mc_port
        self._port: int = 0
        self._server: Optional[socket.socket] = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._guests: Dict[str, dict] = {}
        self._host_mid = _get_machine_id()
        vendor = _get_vendor()
        self._host_profile = {
            "name": player_name,
            "machine_id": self._host_mid,
            "vendor": vendor,
            "kind": "HOST",
        }

    @property
    def port(self) -> int:
        return self._port

    @property
    def all_profiles(self) -> list:
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
        self._guests.clear()
        logger.info("Scaffolding server stopped")

    def _accept_loop(self):
        while self._running and self._server:
            try:
                self._server.settimeout(1.0)
                client, addr = self._server.accept()
                threading.Thread(
                    target=self._handle_client,
                    args=(client, addr),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except Exception:
                if self._running:
                    time.sleep(0.1)

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
        type_str = buf[1:1 + type_len].decode("utf-8")
        body_len = struct.unpack(">I", buf[1 + type_len:header_size])[0]
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
                return 0, b""
            elif type_str == "c:ping":
                return 0, b""
            else:
                logger.debug(f"Unknown Scaffolding request: {type_str}")
                return 0, b""
        except Exception as e:
            logger.debug(f"Scaffolding handler error ({type_str}): {e}")
            return 1, b""

    def _handle_player_ping(self, body: bytes) -> tuple:
        try:
            info = json.loads(body)
            if info and isinstance(info, dict):
                mid = info.get("machine_id", "")
                if mid and mid != self._host_mid:
                    self._guests[mid] = {
                        "name": info.get("name", "Unknown"),
                        "machine_id": mid,
                        "vendor": info.get("vendor", "unknown"),
                        "kind": "GUEST",
                    }
        except Exception:
            pass
        return 0, b""

    def _handle_player_profiles_list(self) -> tuple:
        body = json.dumps(self.all_profiles, ensure_ascii=False).encode("utf-8")
        return 0, body

    def _handle_server_port(self) -> tuple:
        port_bytes = struct.pack(">H", self._mc_port & 0xFFFF)
        return 0, port_bytes


class EasyTierManager:
    def __init__(self, base_dir: Path):
        self._base_dir = base_dir
        self._core_path = base_dir / "easytier-core.exe"
        self._cli_path = base_dir / "easytier-cli.exe"
        self._packet_dll = base_dir / "Packet.dll"
        self._process: Optional[subprocess.Popen] = None
        self._rpc_port: int = 0
        self._reader_thread: Optional[threading.Thread] = None
        self._running = False
        self._scf_server: Optional[ScaffoldingServer] = None

    @property
    def is_installed(self) -> bool:
        return (self._core_path.exists()
                and self._cli_path.exists()
                and self._packet_dll.exists())

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

    def download(self, on_progress=None) -> bool:
        self._base_dir.mkdir(parents=True, exist_ok=True)
        version = EASYTIER_VERSION
        urls = [EASYTIER_BASE_URL.format(version=version)] + [
            m.format(version=version) for m in EASYTIER_DOWNLOAD_MIRRORS
        ]
        zip_path = self._base_dir / f"easytier-windows-x86_64-v{version}.zip"
        downloaded = False
        last_error = None

        for url in urls:
            try:
                logger.info(f"Downloading EasyTier from {url}")
                if on_progress:
                    on_progress(f"正在下载 EasyTier v{version}...")
                urllib.request.urlretrieve(url, str(zip_path))
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

            logger.info("EasyTier installed successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to extract EasyTier: {e}")
            zip_path.unlink(missing_ok=True)
            return False

    def _flatten_extracted_dir(self):
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
                try:
                    shutil.rmtree(str(sub_path), ignore_errors=True)
                except Exception:
                    pass
                break

    def _resolve_relay_nodes(self) -> list:
        nodes = []
        dynamic_nodes = self._fetch_dynamic_nodes()
        if dynamic_nodes:
            nodes.extend(dynamic_nodes)
        nodes.extend([
            "https://etnode.zkitefly.eu.org/node1",
            "https://etnode.zkitefly.eu.org/node2",
        ])
        nodes.extend([
            "tcp://public.easytier.top:11010",
            "tcp://public2.easytier.cn:54321",
        ])
        return nodes

    @staticmethod
    def _fetch_dynamic_nodes() -> list:
        try:
            req = urllib.request.Request(
                "https://uptime.easytier.cn/api/nodes?page=1&per_page=50&is_active=true",
                headers={"User-Agent": "FMCL/2.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            items = data.get("data", {}).get("items", [])
            nodes = []
            for item in items[:10]:
                url = item.get("url", "")
                if url and (url.startswith("tcp://") or url.startswith("udp://") or url.startswith("https://")):
                    nodes.append(url)
            if nodes:
                logger.info(f"Got {len(nodes)} dynamic relay nodes from uptime API")
            return nodes
        except Exception as e:
            logger.debug(f"Failed to fetch dynamic relay nodes: {e}")
            return []

    def launch(self, lobby: LobbyInfo, as_host: bool, on_output=None,
               on_exited=None, player_name: str = "Host") -> int:
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
            "--encryption-algorithm", "aes-gcm",
            "--default-protocol", "tcp",
            "--compression", "zstd",
            "--network-name", lobby.network_name,
            "--network-secret", lobby.network_secret,
            "--machine-id", _get_machine_id(),
            "--rpc-portal", f"127.0.0.1:{self._rpc_port}",
            "--private-mode", "true",
            "--p2p-only",
        ]

        if as_host:
            self._scf_server = ScaffoldingServer(lobby.minecraft_port, player_name)
            scf_port = self._scf_server.start()
            if scf_port <= 0:
                logger.error("ScaffoldingServer failed to start, aborting")
                return 1
            logger.info(f"ScaffoldingServer started on port {scf_port}")
            args.extend([
                "-i", HOST_VIRTUAL_IP,
                "--hostname", f"scaffolding-mc-server-{scf_port}",
            ])
            if lobby.minecraft_port > 0:
                args.extend([
                    "--tcp-whitelist", str(scf_port),
                    "--udp-whitelist", str(scf_port),
                    "--tcp-whitelist", str(lobby.minecraft_port),
                    "--udp-whitelist", str(lobby.minecraft_port),
                ])
            else:
                args.extend([
                    "--tcp-whitelist", str(scf_port),
                    "--udp-whitelist", str(scf_port),
                ])
            args.extend([
                "-l", "tcp://0.0.0.0:0",
                "-l", "udp://0.0.0.0:0",
            ])
        else:
            args.extend([
                "-d",
                "--hostname", secrets.token_hex(8),
                "--tcp-whitelist", "0",
                "--udp-whitelist", "0",
                "-l", "tcp://0.0.0.0:0",
                "-l", "udp://0.0.0.0:0",
            ])

        relay_nodes = self._resolve_relay_nodes()
        for relay in relay_nodes:
            args.extend(["-p", relay])

        try:
            self._process = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(self._base_dir),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self._running = True
            logger.info(f"EasyTier launched with PID {self._process.pid}")
            self._reader_thread = threading.Thread(
                target=self._read_output,
                args=(on_output, on_exited),
                daemon=True,
            )
            self._reader_thread.start()
            return 0
        except Exception as e:
            logger.error(f"Failed to launch EasyTier: {e}")
            self._running = False
            self._process = None
            self._cleanup_scf()
            return 1

    def _read_output(self, on_output, on_exited):
        proc = self._process
        if proc is None:
            return
        try:
            for line in iter(proc.stdout.readline, ""):
                if proc.poll() is not None and not line:
                    break
                stripped = line.strip()
                if stripped and on_output:
                    on_output(stripped)
        except Exception:
            pass
        finally:
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
        for proto, local_addr in rules:
            try:
                subprocess.run(
                    [
                        str(self._cli_path),
                        "--rpc-portal", f"127.0.0.1:{self._rpc_port}",
                        "port-forward", "add",
                        proto,
                        local_addr,
                        f"{target_ip}:{target_port}",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=str(self._base_dir),
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception as e:
                logger.warning(f"Failed to add {proto} port forward: {e}")
        return local_port

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
            self._thread = threading.Thread(
                target=self._broadcast_loop,
                args=(packet,),
                daemon=True,
            )
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
            logger.info(f"TCP forwarder started: {LOOPBACK}:{self._listen_port} -> {self._target_host}:{self._target_port}")
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
                threading.Thread(
                    target=self._forward,
                    args=(client,),
                    daemon=True,
                ).start()
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


class OnlineTabMixin(object):
    """联机标签页 Mixin - 陶瓦联机功能"""

    def _build_online_tab_content(self):
        if platform.system().lower() != "windows":
            self._build_online_unsupported_tab()
            return

        content = ctk.CTkFrame(self.online_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        top_bar = ctk.CTkFrame(content, fg_color="transparent")
        top_bar.pack(fill=ctk.X, pady=(0, 10))

        self._online_title_label = ctk.CTkLabel(
            top_bar,
            text=_("online_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self._online_title_label.pack(anchor=ctk.W)

        self._online_desc_label = ctk.CTkLabel(
            top_bar,
            text=_("online_description"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            text_color=COLORS["text_secondary"],
            wraplength=1100,
            justify=ctk.LEFT,
            anchor=ctk.W,
        )
        self._online_desc_label.pack(anchor=ctk.W, pady=(5, 0))

        main_container = ctk.CTkFrame(content, fg_color="transparent")
        main_container.pack(fill=ctk.BOTH, expand=True)

        self._build_online_output_panel(main_container)
        self._build_online_control_panel(main_container)

        self._theme_refs.append((self._online_title_label, {"text_color": "text_primary"}))
        self._theme_refs.append((self._online_desc_label, {"text_color": "text_secondary"}))

        self._et_manager: Optional[EasyTierManager] = None
        self._lobby_info: Optional[LobbyInfo] = None
        self._is_host: bool = False
        self._broadcast_sim: Optional[McBroadcastSimulator] = None
        self._tcp_forwarder: Optional[TcpPortForwarder] = None
        self._local_mc_port: int = 0
        self._public_address: Optional[str] = None
        self._member_poll_id: Optional[str] = None

        self.after(200, self._init_online_state)

    def _build_online_unsupported_tab(self):
        content = ctk.CTkFrame(self.online_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        ctk.CTkLabel(
            content,
            text=_("online_unsupported_platform"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15),
            text_color=COLORS["text_secondary"],
            wraplength=600,
            justify=ctk.CENTER,
        ).pack(expand=True)

    def _init_online_state(self):
        base_dir = _get_easytier_base_dir()
        self._et_manager = EasyTierManager(base_dir)
        if self._et_manager.is_installed:
            self._append_online_log("[FMCL] EasyTier v" + EASYTIER_VERSION + " " + _("online_et_ready"))
            self._update_env_easytier_label(_("online_et_ready"), "success")
        else:
            self._append_online_log("[FMCL] " + _("online_et_not_found"))
            self._update_env_easytier_label(_("online_et_not_found"), "error")

    def _build_online_control_panel(self, parent):
        self._online_control_frame = ctk.CTkScrollableFrame(
            parent,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        self._online_control_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 10))

        self._build_online_env_section(self._online_control_frame)
        self._build_online_create_section(self._online_control_frame)
        self._build_online_join_section(self._online_control_frame)
        self._build_online_lobby_section(self._online_control_frame)
        self._build_online_tips_section(self._online_control_frame)
        self._build_online_compat_section(self._online_control_frame)

        self._theme_refs.append((self._online_control_frame, {"scrollbar_button_color": "bg_light"}))

    def _build_online_env_section(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_env_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_env_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(4, 8))

        et_row = ctk.CTkFrame(inner, fg_color="transparent")
        et_row.pack(fill=ctk.X, pady=(0, 8))

        ctk.CTkLabel(
            et_row,
            text="📦",
            font=ctk.CTkFont(size=14),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT, padx=(0, 4))

        self._online_env_et_label = ctk.CTkLabel(
            et_row,
            text=_("online_et_checking"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._online_env_et_label.pack(side=ctk.LEFT)

        self._online_env_setup_btn = ctk.CTkButton(
            inner,
            text=_("online_env_setup_btn"),
            height=36,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_setup_environment,
        )
        self._online_env_setup_btn.pack(fill=ctk.X, pady=(0, 0))

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_env_et_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._online_env_setup_btn, {"fg_color": "accent", "hover_color": "accent_hover"}))

    def _build_online_create_section(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_create_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_create_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(4, 0))

        ctk.CTkLabel(
            inner,
            text=_("online_port_label"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(10, 0))

        self._online_port_entry = ctk.CTkEntry(
            inner,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=_("online_port_placeholder"),
        )
        self._online_port_entry.pack(fill=ctk.X, pady=(4, 0))
        self._online_port_entry.insert(0, "25565")

        self._online_create_btn = ctk.CTkButton(
            inner,
            text=_("online_create_lobby"),
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["success"],
            hover_color="#27ae60",
            command=self._on_create_lobby,
        )
        self._online_create_btn.pack(fill=ctk.X, pady=(12, 0))

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_port_entry, {"fg_color": "bg_medium", "border_color": "card_border"}))
        self._theme_refs.append((self._online_create_btn, {"fg_color": "success"}))

    def _build_online_join_section(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_join_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_join_desc"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(4, 0))

        ctk.CTkLabel(
            inner,
            text=_("online_lobby_code_label"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(10, 0))

        self._online_lobby_code_entry = ctk.CTkEntry(
            inner,
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_medium"],
            border_color=COLORS["card_border"],
            placeholder_text=_("online_lobby_code_placeholder"),
        )
        self._online_lobby_code_entry.pack(fill=ctk.X, pady=(4, 0))

        self._online_join_btn = ctk.CTkButton(
            inner,
            text=_("online_join_lobby"),
            height=38,
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._on_join_lobby,
        )
        self._online_join_btn.pack(fill=ctk.X, pady=(12, 0))

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_lobby_code_entry, {"fg_color": "bg_medium", "border_color": "card_border"}))
        self._theme_refs.append((self._online_join_btn, {"fg_color": "accent", "hover_color": "accent_hover"}))

    def _build_online_lobby_section(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_lobby_info_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        self._online_lobby_code_display_label = ctk.CTkLabel(
            inner,
            text=_("online_no_lobby"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["accent"],
            wraplength=340,
        )
        self._online_lobby_code_display_label.pack(anchor=ctk.W, pady=(8, 0))

        self._online_lobby_status_label = ctk.CTkLabel(
            inner,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._online_lobby_status_label.pack(anchor=ctk.W, pady=(4, 0))

        self._online_members_label = ctk.CTkLabel(
            inner,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        )
        self._online_members_label.pack(anchor=ctk.W, pady=(4, 0))

        btn_frame = ctk.CTkFrame(inner, fg_color="transparent")
        btn_frame.pack(fill=ctk.X, pady=(10, 0))

        self._online_copy_code_btn = ctk.CTkButton(
            btn_frame,
            text=_("online_copy_code"),
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._on_copy_code,
        )
        self._online_copy_code_btn.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 4))

        self._online_leave_btn = ctk.CTkButton(
            btn_frame,
            text=_("online_leave_lobby"),
            height=32,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            fg_color=COLORS["error"],
            hover_color="#c0392b",
            text_color=COLORS["text_primary"],
            command=self._on_leave_lobby,
        )
        self._online_leave_btn.pack(side=ctk.RIGHT)
        self._online_leave_btn.configure(state=ctk.DISABLED)

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_lobby_code_display_label, {"text_color": "accent"}))
        self._theme_refs.append((self._online_lobby_status_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._online_copy_code_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))
        self._theme_refs.append((self._online_leave_btn, {"fg_color": "error", "text_color": "text_primary"}))

    def _build_online_tips_section(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_tips_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_tips_content"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(4, 0))

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))

    def _build_online_compat_section(self, parent):
        card = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        card.pack(fill=ctk.X, padx=5, pady=5)

        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill=ctk.X, padx=15, pady=12)

        ctk.CTkLabel(
            inner,
            text=_("online_compat_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(anchor=ctk.W)

        ctk.CTkLabel(
            inner,
            text=_("online_compat_content"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
            wraplength=340,
            justify=ctk.LEFT,
        ).pack(anchor=ctk.W, pady=(4, 0))

        self._theme_refs.append((card, {"fg_color": "card_bg", "border_color": "card_border"}))

    def _build_online_output_panel(self, parent):
        self._online_output_frame = ctk.CTkFrame(parent, fg_color=COLORS["card_bg"], corner_radius=12, border_width=1, border_color=COLORS["card_border"])
        self._online_output_frame.pack(side=ctk.RIGHT, fill=ctk.BOTH, expand=True, padx=(0, 0))

        frame = self._online_output_frame

        title_frame = ctk.CTkFrame(frame, fg_color="transparent", height=40)
        title_frame.pack(fill=ctk.X, padx=15, pady=(12, 0))
        title_frame.pack_propagate(False)

        ctk.CTkLabel(
            title_frame,
            text=_("online_output_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=16, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self._online_status_label = ctk.CTkLabel(
            title_frame,
            text=_("online_et_stopped"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._online_status_label.pack(side=ctk.RIGHT)

        ctk.CTkLabel(
            title_frame,
            text=_("online_log_note"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, pady=(2, 0))

        ctk.CTkFrame(frame, fg_color=COLORS["card_border"], height=1).pack(
            fill=ctk.X, padx=15, pady=(8, 5)
        )

        self._online_log_text = ctk.CTkTextbox(
            frame,
            font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=COLORS["bg_dark"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
            activate_scrollbars=True,
            wrap=ctk.WORD,
            spacing3=0,
        )
        self._online_log_text.pack(fill=ctk.BOTH, expand=True, padx=10, pady=(0, 10))
        self._online_log_text.configure(state=ctk.DISABLED)

        self._append_online_log("[FMCL] " + _("status_ready"))

        self._theme_refs.append((self._online_output_frame, {"fg_color": "card_bg", "border_color": "card_border"}))
        self._theme_refs.append((self._online_status_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._online_log_text, {"fg_color": "bg_dark", "border_color": "card_border", "text_color": "text_primary"}))

    def _append_online_log(self, message: str):
        def _do_append():
            self._online_log_text.configure(state=ctk.NORMAL)
            self._online_log_text.insert(ctk.END, message + "\n")
            self._online_log_text.see(ctk.END)
            self._online_log_text.configure(state=ctk.DISABLED)
        if self.winfo_exists():
            self.after(0, _do_append)

    def _set_online_status(self, message: str, color_key: str = "text_secondary"):
        def _do_set():
            self._online_status_label.configure(text=message, text_color=COLORS.get(color_key, COLORS["text_secondary"]))
        if self.winfo_exists():
            self.after(0, _do_set)

    def _update_env_easytier_label(self, text: str, color_key: str = "text_secondary"):
        def _do():
            self._online_env_et_label.configure(text=text, text_color=COLORS.get(color_key, COLORS["text_secondary"]))
        if self.winfo_exists():
            self.after(0, _do)

    def _run_online_thread(self, target, args=(), on_done=None, on_error=None):
        def wrapper():
            try:
                result = target(*args)
                if on_done:
                    self.after(0, lambda: on_done(result))
            except Exception as e:
                if on_error:
                    self.after(0, lambda: on_error(str(e)))
                else:
                    self.after(0, lambda: self._append_online_log(f"[FMCL] Error: {e}"))
        threading.Thread(target=wrapper, daemon=True).start()

    def _get_display_name(self) -> str:
        if "get_jdz_username" in self.callbacks:
            name = self.callbacks["get_jdz_username"]()
            if name:
                return name
        return "Host"

    def _check_logged_in(self) -> bool:
        return not self._get_login_error()

    def _get_login_error(self) -> str:
        if "get_jdz_username" in self.callbacks and self.callbacks["get_jdz_username"]():
            return ""
        if "get_jdz_token" in self.callbacks and self.callbacks["get_jdz_token"]():
            return _("online_login_expired")
        return _("online_need_login")

    def _start_member_poll(self):
        self._refresh_members()
        self._member_poll_id = self.after(5000, self._poll_members_loop)

    def _poll_members_loop(self):
        if not self._et_manager or not self._et_manager._scf_server:
            self._member_poll_id = None
            return
        self._refresh_members()
        self._member_poll_id = self.after(5000, self._poll_members_loop)

    def _stop_member_poll(self):
        if self._member_poll_id:
            self.after_cancel(self._member_poll_id)
            self._member_poll_id = None

    def _refresh_members(self):
        if not self._et_manager or not self._et_manager._scf_server:
            return
        profiles = self._et_manager._scf_server.all_profiles
        lines = []
        for p in profiles:
            kind = p.get("kind", "?")
            vendor = p.get("vendor", "?")
            icon = "👑" if kind == "HOST" else "👤"
            lines.append(f"{icon} {kind} · {vendor}")
        if not lines:
            lines.append(_("online_no_members"))
        self._online_members_label.configure(text="\n".join(lines))

    def _on_setup_environment(self):
        if self._et_manager is None:
            self._et_manager = EasyTierManager(_get_easytier_base_dir())

        self._online_env_setup_btn.configure(text=_("online_env_setup_start"), state=ctk.DISABLED)
        self._append_online_log("[FMCL] " + _("online_env_setup_start"))

        def _setup():
            try:
                ok = self._et_manager.download(
                    on_progress=lambda msg: self.after(0, lambda: self._append_online_log(f"[FMCL] {msg}"))
                )
                return ok
            except Exception as e:
                logger.error(f"EasyTier setup failed: {e}")
                return False

        def on_done(ok):
            if ok:
                self._update_env_easytier_label(_("online_et_ready"), "success")
                self._online_env_setup_btn.configure(
                    text="✅ " + _("online_env_setup_done"),
                    state=ctk.DISABLED,
                    fg_color=COLORS["success"],
                )
                self._append_online_log("[FMCL] " + _("online_env_setup_done"))
                self.set_status(_("online_env_setup_done"), "success")
            else:
                self._update_env_easytier_label(_("online_et_not_found"), "error")
                self._online_env_setup_btn.configure(
                    text=_("online_env_setup_retry"),
                    state=ctk.NORMAL,
                    fg_color=COLORS["accent"],
                )
                self._append_online_log("[FMCL] " + _("online_env_setup_failed"))
                self.set_status(_("online_env_setup_failed"), "warning")

        def on_error(err):
            self._update_env_easytier_label(_("online_et_not_found"), "error")
            self._online_env_setup_btn.configure(
                text=_("online_env_setup_retry"),
                state=ctk.NORMAL,
                fg_color=COLORS["accent"],
            )
            self._append_online_log("[FMCL] " + str(err))

        self._run_online_thread(_setup, on_done=on_done, on_error=on_error)

    def _on_create_lobby(self):
        login_err = self._get_login_error()
        if login_err:
            self._append_online_log("[FMCL] " + login_err)
            self.set_status(login_err, "warning")
            return

        if self._et_manager is None or not self._et_manager.is_installed:
            self._append_online_log("[FMCL] " + _("online_et_not_found"))
            self.set_status(_("online_et_not_found"), "warning")
            return

        if self._et_manager.is_running:
            self._append_online_log("[FMCL] " + _("online_et_already_running"))
            self.set_status(_("online_et_already_running"), "warning")
            return

        port_str = self._online_port_entry.get().strip()
        if not port_str:
            port_str = "25565"
        try:
            mc_port = int(port_str)
            if mc_port < 1 or mc_port > 65535:
                raise ValueError
        except ValueError:
            self._append_online_log("[FMCL] " + _("online_invalid_port", port=port_str))
            self.set_status(_("online_invalid_port", port=port_str), "error")
            return

        self._online_create_btn.configure(state=ctk.DISABLED)
        self._online_join_btn.configure(state=ctk.DISABLED)
        self._append_online_log("[FMCL] " + _("online_creating_lobby"))

        def _create():
            lobby = LobbyCodeGenerator.generate()
            lobby.minecraft_port = mc_port
            lobby.is_host = True
            return lobby

        def on_done(lobby):
            self._lobby_info = lobby
            self._is_host = True
            self._local_mc_port = mc_port

            self._online_lobby_code_display_label.configure(
                text=lobby.full_code, text_color=COLORS["success"]
            )
            self._online_lobby_status_label.configure(text="")
            self._online_leave_btn.configure(state=ctk.NORMAL)

            result = self._et_manager.launch(
                lobby,
                as_host=True,
                on_output=lambda line: self._append_online_log(line),
                on_exited=lambda code: self.after(0, lambda: self._on_easytier_exited(code)),
                player_name=self._get_display_name(),
            )

            if result == 0:
                self._set_online_status(_("online_et_running", pid=self._et_manager.pid), "success")
                self._append_online_log("[FMCL] " + _("online_lobby_created", code=lobby.full_code))
                self.set_status(_("online_lobby_created", code=lobby.full_code), "success")
                self._start_member_poll()
            else:
                self._append_online_log("[FMCL] " + _("online_launch_failed"))
                self.set_status(_("online_launch_failed"), "error")
                self._reset_lobby_state()
                self._online_create_btn.configure(state=ctk.NORMAL)
                self._online_join_btn.configure(state=ctk.NORMAL)

        def on_error(err):
            self._append_online_log("[FMCL] " + _("online_create_failed", error=err))
            self.set_status(_("online_create_failed", error=err), "error")
            self._online_create_btn.configure(state=ctk.NORMAL)
            self._online_join_btn.configure(state=ctk.NORMAL)

        self._run_online_thread(_create, on_done=on_done, on_error=on_error)

    def _on_join_lobby(self):
        login_err = self._get_login_error()
        if login_err:
            self._append_online_log("[FMCL] " + login_err)
            self.set_status(login_err, "warning")
            return

        if self._et_manager is None or not self._et_manager.is_installed:
            self._append_online_log("[FMCL] " + _("online_et_not_found"))
            self.set_status(_("online_et_not_found"), "warning")
            return

        if self._et_manager.is_running:
            self._append_online_log("[FMCL] " + _("online_et_already_running"))
            self.set_status(_("online_et_already_running"), "warning")
            return

        code = self._online_lobby_code_entry.get().strip()
        if not code:
            self._append_online_log("[FMCL] " + _("online_empty_code"))
            self.set_status(_("online_empty_code"), "warning")
            return

        lobby = LobbyCodeGenerator.try_parse(code)
        if lobby is None:
            self._append_online_log("[FMCL] " + _("online_invalid_code"))
            self.set_status(_("online_invalid_code"), "warning")
            return

        self._online_create_btn.configure(state=ctk.DISABLED)
        self._online_join_btn.configure(state=ctk.DISABLED)
        self._append_online_log("[FMCL] " + _("online_joining_lobby", code=lobby.full_code))

        def _join():
            return lobby

        def on_done(lobby):
            self._lobby_info = lobby
            self._is_host = False

            result = self._et_manager.launch(
                lobby,
                as_host=False,
                on_output=lambda line: self._append_online_log(line),
                on_exited=lambda code: self.after(0, lambda: self._on_easytier_exited(code)),
            )

            if result == 0:
                self._set_online_status(_("online_et_connecting"), "warning")
                self._online_lobby_code_display_label.configure(
                    text=lobby.full_code, text_color=COLORS["accent"]
                )
                self._online_lobby_status_label.configure(
                    text=_("online_waiting_network"), text_color=COLORS["warning"]
                )
                self._online_leave_btn.configure(state=ctk.NORMAL)
                self._append_online_log("[FMCL] " + _("online_joined_lobby", code=lobby.full_code))
                self._append_online_log("[FMCL] " + _("online_waiting_network"))
                self.set_status(_("online_joined_lobby", code=lobby.full_code), "success")

                self.after(3000, self._setup_joiner_forward)
            else:
                self._append_online_log("[FMCL] " + _("online_launch_failed"))
                self.set_status(_("online_launch_failed"), "error")
                self._reset_lobby_state()
                self._online_create_btn.configure(state=ctk.NORMAL)
                self._online_join_btn.configure(state=ctk.NORMAL)

        def on_error(err):
            self._append_online_log("[FMCL] " + _("online_join_failed", error=err))
            self.set_status(_("online_join_failed", error=err), "error")
            self._online_create_btn.configure(state=ctk.NORMAL)
            self._online_join_btn.configure(state=ctk.NORMAL)

        self._run_online_thread(_join, on_done=on_done, on_error=on_error)

    def _setup_joiner_forward(self):
        if not self._et_manager or not self._et_manager.is_running:
            self._append_online_log("[FMCL] " + _("online_forward_failed_et"))
            return

        if self._is_host:
            return

        self._append_online_log("[FMCL] " + _("online_setting_up_forward"))

        def _setup():
            local_port = self._et_manager.add_port_forward(HOST_VIRTUAL_IP, 25565)
            return local_port

        def on_done(local_port):
            if local_port is None:
                self._append_online_log("[FMCL] " + _("online_forward_failed"))
                self._online_lobby_status_label.configure(
                    text=_("online_forward_failed"), text_color=COLORS["error"]
                )
                return

            self._local_mc_port = local_port
            self._online_lobby_status_label.configure(
                text=_("online_forward_ready", port=local_port), text_color=COLORS["success"]
            )
            self._append_online_log("[FMCL] " + _("online_forward_ready", port=local_port))

            tcp_forward_port = _get_random_port()
            self._tcp_forwarder = TcpPortForwarder(
                tcp_forward_port, LOOPBACK, local_port
            )
            self._tcp_forwarder.start()

            desc = f"§eFMCL 大厅 - {self._get_display_name()}"

            self._broadcast_sim = McBroadcastSimulator()
            self._broadcast_sim.start(desc, tcp_forward_port)

            self._set_online_status(_("online_et_running", pid=self._et_manager.pid), "success")
            self._append_online_log(
                "[FMCL] " + _("online_forward_complete", local_port=tcp_forward_port)
            )
            self.set_status(_("online_forward_complete", local_port=tcp_forward_port), "success")

        def on_error(err):
            self._append_online_log("[FMCL] " + _("online_forward_failed_detail", error=err))
            self._online_lobby_status_label.configure(
                text=_("online_forward_failed_detail", error=err), text_color=COLORS["error"]
            )

        self._run_online_thread(_setup, on_done=on_done, on_error=on_error)

    def _on_leave_lobby(self):
        self._stop_member_poll()
        self._append_online_log("[FMCL] " + _("online_leaving_lobby"))

        if self._broadcast_sim:
            self._broadcast_sim.stop()
            self._broadcast_sim = None

        if self._tcp_forwarder:
            self._tcp_forwarder.stop()
            self._tcp_forwarder = None

        if self._et_manager:
            self._et_manager.stop()

        self._reset_lobby_state()
        self._set_online_status(_("online_et_stopped"), "text_secondary")
        self._append_online_log("[FMCL] " + _("online_left_lobby"))
        self.set_status(_("online_left_lobby"), "info")

    def _reset_lobby_state(self):
        self._stop_member_poll()
        self._lobby_info = None
        self._is_host = False
        self._local_mc_port = 0
        self._public_address = None
        self._online_lobby_code_display_label.configure(
            text=_("online_no_lobby"), text_color=COLORS["accent"]
        )
        self._online_lobby_status_label.configure(text="")
        self._online_leave_btn.configure(state=ctk.DISABLED)
        self._online_create_btn.configure(state=ctk.NORMAL)
        self._online_join_btn.configure(state=ctk.NORMAL)

    def _on_easytier_exited(self, exit_code: int):
        self._append_online_log(f"[FMCL] EasyTier exited with code {exit_code}")
        self._set_online_status(_("online_et_stopped"), "text_secondary")
        if self._broadcast_sim:
            self._broadcast_sim.stop()
            self._broadcast_sim = None
        if self._tcp_forwarder:
            self._tcp_forwarder.stop()
            self._tcp_forwarder = None
        self._reset_lobby_state()
        self.set_status(_("online_et_exited", exit_code=exit_code), "warning")

    def _on_copy_code(self):
        if not self._lobby_info:
            return
        try:
            import pyperclip
            pyperclip.copy(self._lobby_info.full_code)
            self.set_status(_("online_copy_success", code=self._lobby_info.full_code), "success")
        except Exception as e:
            self.set_status(_("copy_failed", error=str(e)), "error")