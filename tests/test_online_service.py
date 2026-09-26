"""联机服务（阶段 1 任务 1.5）的单元测试。

覆盖被搬进 ``services/online_service.py`` 的**纯逻辑**：EasyTier 输出的批处理解析、
成员列表维护、大厅码格式校验、端口转发命令拼装、局域网广播报文解析、EasyTier 启动
参数构造、陶瓦协议的帧收发，以及"服务在无 ``AppContext`` 时可独立实例化"。

**全部离线**：不启动 EasyTier、不发任何 HTTP 请求、不 bind 真实端口、不做真实端口
转发、不起协议服务。外部程序一律通过构造期注入缝（``popen`` / ``run`` / ``urlopen``
/ ``relay_nodes``）换成假对象，套接字一律用 :class:`FakeSock`。

界面侧的行为等价另有两条更强的证据（本文件只补服务层自身的边界）：

- ``poc/_verify_online_extraction.py``：AST 逐节点比对 + 逐字节生成式核对 + 完整性
- ``poc/probe_online_service.py``：旧实现 vs 新实现 76 项同输入对照

.. warning::
    **刻意不测** ``EasyTierManager._cleanup_stale_versions``（D-83，
    ``docs/refactor/07-known-defects.md``）：它会 ``shutil.rmtree`` 掉版本根下所有
    非当前版本目录 —— 破坏性、无提示、无备份。任务 1.5 只搬家不改逻辑，这里**不写
    用例去钉住这个行为**（前两轮有执行者钉住过缺陷行为，修缺陷时不得不回头改用例）。
"""

from __future__ import annotations

import json
import secrets
import struct
from pathlib import Path
from typing import List

import pytest

from services.online_service import (
    AD_RE,
    EASYTIER_VERSION,
    HOST_VIRTUAL_IP,
    LOOPBACK,
    MOTD_RE,
    BroadcastListener,
    EasyTierManager,
    GameWatcher,
    LobbyCodeGenerator,
    LobbyInfo,
    LobbyState,
    McBroadcastSimulator,
    McPing,
    OnlineService,
    ScaffoldingClient,
    ScaffoldingServer,
    TcpPortForwarder,
    format_member_lines,
    is_platform_supported,
    lobby_state_transition,
    parse_broadcast_message,
    parse_lobby_code,
    parse_mc_port,
)
from ui.app_online import OnlineTabMixin, _get_online_service


# ─── 假对象（形状对齐真实对手方，但绝不碰网络/进程）─────────────


class FakeStdout:
    """``subprocess.Popen(text=True).stdout`` 的形状。"""

    def __init__(self, lines: List[str]):
        self._lines = list(lines)

    def readline(self):
        return self._lines.pop(0) if self._lines else ""


class FakeProc:
    def __init__(self, lines=(), returncode=0, pid=4242):
        self.stdout = FakeStdout(list(lines))
        self._returncode = returncode
        self.pid = pid

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        return self._returncode


class FakeCompleted:
    """``subprocess.run`` 的返回值。"""

    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeSock:
    """可脚本化的套接字：``recv`` 按预置分片吐出，``sendall`` 记录。"""

    def __init__(self, chunks: List[bytes] | None = None):
        self._chunks = list(chunks or [])
        self.sent = bytearray()
        self.closed = False

    def sendall(self, data: bytes):
        self.sent.extend(data)

    def recv(self, n: int) -> bytes:
        while self._chunks:
            head = self._chunks[0]
            if len(head) <= n:
                self._chunks.pop(0)
                return head
            self._chunks[0] = head[n:]
            return head[:n]
        return b""

    def close(self):
        self.closed = True


@pytest.fixture()
def easytier_dir(tmp_path: Path) -> Path:
    """一个"已安装 EasyTier"的假目录（只要三个文件在，precheck 就通过）。"""
    base = tmp_path / "easytier-windows-x86_64"
    base.mkdir(parents=True, exist_ok=True)
    for name in ("easytier-core.exe", "easytier-cli.exe", "Packet.dll"):
        (base / name).write_bytes(b"MZ-fake")
    return base


def make_manager(base: Path, **kwargs) -> EasyTierManager:
    """造一个完全离线的管理器：中继节点预置，不起预取线程。"""
    kwargs.setdefault("relay_nodes", ["tcp://relay-a:11010", "tcp://relay-b:11010"])
    return EasyTierManager(base, **kwargs)


def frame(type_str: str, body: bytes = b"") -> bytes:
    """按陶瓦协议拼一帧：``len(type) | type | len(body) | body``。"""
    t = type_str.encode("utf-8")
    return struct.pack(">B", len(t)) + t + struct.pack(">I", len(body)) + body


# ═══ 服务对象本身 ══════════════════════════════════════════


def test_service_instantiates_without_app_context():
    """服务在**没有 AppContext**时必须能独立实例化并使用（阶段 1 的 Tk 界面就是这么取的）。"""
    service = OnlineService()
    assert service.attached is False
    assert service.started is False
    assert service.name == "online"
    assert service.describe() == {
        "name": "online",
        "label": "联机",
        "class": "OnlineService",
        "requires": [],
        "started": False,
        "attached": False,
    }
    # 不碰 self.ui / self.tasks / self.config 也能干活
    assert service.parse_mc_port("25565") == (25565, "25565")
    assert service.parse_lobby_code("nope") is None
    assert isinstance(service.create_manager(Path("."), relay_nodes=[]), EasyTierManager)


def test_service_works_through_module_level_getter():
    """界面侧的惰性取服务：无 context 时自造并缓存，有 context 时优先用它注册的那个。"""

    class FakeCtx:
        def __init__(self, service):
            self._service = service

        def try_get(self, name):
            return self._service if name == "online" else None

    owner = type("Owner", (), {})()
    first = _get_online_service(owner)
    assert isinstance(first, OnlineService)
    assert _get_online_service(owner) is first, "应缓存到 owner 上，而不是每次新建"

    registered = OnlineService()
    with_ctx = type("Owner2", (), {"context": FakeCtx(registered)})()
    assert _get_online_service(with_ctx) is registered

    assert _get_online_service(None).name == "online", "owner 为 None 时也要能用"


# ═══ EasyTier 输出（日志）解析 ═════════════════════════════


def test_read_output_batches_twenty_lines_and_flushes_tail(easytier_dir):
    """输出按 20 行一批投递：空行被丢掉、首尾空白被 strip、最后不足一批也要 flush。"""
    lines = [f"line-{i}\n" for i in range(45)] + ["\n", "  padded  ", "\n"]
    manager = make_manager(easytier_dir)
    got: List[str] = []
    exited: List[int] = []
    manager._process = FakeProc(lines, returncode=7)
    manager._running = True

    manager._read_output(got.append, exited.append)

    assert [len(batch.splitlines()) for batch in got] == [20, 20, 6]
    assert got[0].splitlines()[0] == "line-0"
    assert got[-1].splitlines()[-1] == "padded", "首尾空白必须被 strip 掉"
    assert "\n\n" not in "\n".join(got), "空行不参与投递"
    assert exited == [7]
    assert manager._running is False and manager._process is None


def test_read_output_empty_stream_still_reports_exit(easytier_dir):
    """进程没有任何输出时也要恰好投递一次退出回调。"""
    manager = make_manager(easytier_dir)
    got: List[str] = []
    exited: List[int] = []
    manager._process = FakeProc([], returncode=0)
    manager._running = True

    manager._read_output(got.append, exited.append)

    assert got == []
    assert exited == [0]


def test_read_output_without_callbacks_is_silent(easytier_dir):
    """两个回调都传 None 时不得抛异常（界面可能没接线）。"""
    manager = make_manager(easytier_dir)
    manager._process = FakeProc(["a\n"], returncode=1)
    manager._running = True
    manager._read_output(None, None)
    assert manager._process is None


def test_read_output_callback_exception_does_not_escape(easytier_dir):
    """输出回调抛异常不得打断读取（原实现的 ``except Exception: pass`` 逐字保留）。"""

    def boom(_payload):
        raise RuntimeError("界面炸了")

    manager = make_manager(easytier_dir)
    manager._process = FakeProc(["a\n"], returncode=2)
    manager._running = True
    exited: List[int] = []
    manager._read_output(boom, exited.append)
    assert exited == [2], "回调异常后仍要走到 finally 投递退出"


# ═══ 成员列表维护 ══════════════════════════════════════════


def test_sort_player_list_puts_host_first():
    profiles = [{"kind": "GUEST", "name": "g"}, {"kind": "HOST", "name": "h"}, {"name": "no-kind"}]
    assert [p["name"] for p in OnlineService.sort_player_list(profiles)] == ["h", "g", "no-kind"]
    assert OnlineService.sort_player_list([]) == []


def test_format_member_lines_renders_icons_and_vendor():
    lines = format_member_lines([
        {"name": "Host", "vendor": "FMCL 1", "kind": "HOST"},
        {"name": "Guest", "vendor": "HMCL", "kind": "GUEST"},
        {},
    ])
    assert lines == ["👑 Host · FMCL 1", "👤 Guest · HMCL", "👤 ? · ?"]
    assert format_member_lines([]) == [], "空列表由界面侧补『无成员』文案"


def test_scaffolding_server_tracks_guests_and_dedups():
    """``c:player_ping`` 的新访客才触发回调；重复上报只刷新 last_seen。"""
    server = ScaffoldingServer(25565, "Host")
    changes: List[list] = []
    server.on_profiles_changed = changes.append
    body = json.dumps({"name": "Guest", "machine_id": "m-1", "vendor": "HMCL"}).encode()

    assert server._handle_player_ping(body) == (0, b"")
    assert len(changes) == 1, "新成员要通知一次"
    assert server._handle_player_ping(body) == (0, b"")
    assert len(changes) == 1, "同一 machine_id 重复上报不再通知"

    names = [(p["name"], p["kind"]) for p in server.all_profiles]
    assert names == [("Host", "HOST"), ("Guest", "GUEST")], "主机永远排在最前"
    assert all(p["machine_id"] for p in server.all_profiles)


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b"null",
        b'{"name": "x"}',  # 缺 machine_id / vendor
        b'{"name": "x", "machine_id": "", "vendor": "v"}',
    ],
)
def test_scaffolding_server_rejects_bad_ping(body):
    server = ScaffoldingServer(25565, "Host")
    assert server._handle_player_ping(body) == (32, b"")
    assert len(server.all_profiles) == 1, "非法上报不得进入成员表"


def test_scaffolding_server_rejects_host_machine_id_conflict():
    """访客冒用主机的 machine_id 必须被拒（否则成员表里会出现两个『主机』）。"""
    server = ScaffoldingServer(25565, "Host")
    body = json.dumps({"name": "Fake", "machine_id": server._host_mid, "vendor": "v"}).encode()
    assert server._handle_player_ping(body) == (32, b"")
    assert len(server.all_profiles) == 1


def test_scaffolding_server_player_timeout_constant():
    """超时清理的阈值/周期是改造前就有的常量，搬家不得改动。"""
    assert ScaffoldingServer._PLAYER_TIMEOUT == 10.0
    assert ScaffoldingServer._CLEANUP_INTERVAL == 5.0
    assert ScaffoldingServer._SUPPORTED_PROTOCOLS == (
        "c:ping",
        "c:protocols",
        "c:server_port",
        "c:player_ping",
        "c:player_profiles_list",
    )


def test_lobby_state_transition_is_idempotent_and_defaults_to_idle():
    assert lobby_state_transition(None, LobbyState.CONNECTED) is LobbyState.CONNECTED
    assert lobby_state_transition(LobbyState.IDLE, LobbyState.IDLE) is LobbyState.IDLE
    assert lobby_state_transition(LobbyState.CONNECTED, LobbyState.LEAVING) is LobbyState.LEAVING


def test_is_platform_supported_matches_windows_only():
    import platform as platform_mod

    assert is_platform_supported() == (platform_mod.system().lower() == "windows")


# ═══ 大厅码格式校验 ════════════════════════════════════════


def test_generate_lobby_code_shape():
    for _ in range(50):
        lobby = OnlineService.generate_lobby()
        payload = lobby.full_code[len(LobbyCodeGenerator.FULL_CODE_PREFIX):]
        assert len(lobby.full_code) == LobbyCodeGenerator.CODE_LENGTH
        assert lobby.full_code.startswith("U/")
        assert [len(part) for part in payload.split("-")] == [4, 4, 4, 4]
        assert lobby.network_name == "scaffolding-mc-" + payload[:9]
        assert lobby.network_secret == payload[10:]
        assert lobby.minecraft_port == 0 and lobby.is_host is False
        assert LobbyCodeGenerator.try_parse(lobby.full_code) is not None, "自己生成的码必须能被自己解析"


def test_try_parse_accepts_lowercase_but_not_surrounding_space():
    """大小写不敏感；**但首尾空白不被容忍**（``len(upper) != CODE_LENGTH`` 直接拒绝）。

    记录现状：界面侧在调用前自己 ``.strip()``（``_on_join_lobby`` 的 ``entry.get().strip()``），
    所以这条"解析器不 strip"的行为在界面上看不出来；这里按原文钉住。
    """
    code = LobbyCodeGenerator.generate().full_code
    parsed = LobbyCodeGenerator.try_parse(code.lower())
    assert parsed is not None and parsed.full_code == code
    assert LobbyCodeGenerator.try_parse("  " + code + "  ") is None
    assert LobbyCodeGenerator.try_parse(code) is not None


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "I",
        "U/",
        "U/ABCD-EFGH-JKLM-NPQ",          # 少一位
        "U/ABCD-EFGH-JKLM-NPQRX",        # 多一位
        "U/ABCD_EFGH_JKLM_NPQR",         # 分隔符不对
        "U/ABCD-EFGH-JKLM-NPQ!",         # 非法字符
        "ABCD-EFGH-JKLM-NPQR",           # 没有 U/ 前缀
    ],
)
def test_try_parse_rejects_malformed(bad):
    assert LobbyCodeGenerator.try_parse(bad) is None


def test_try_parse_rejects_wrong_checksum_modulo():
    """编码时特意把值对齐到 7 的倍数；把最后一位改掉必须解析失败。"""
    code = LobbyCodeGenerator.generate().full_code
    chars = LobbyCodeGenerator.CHARS
    last = code[-1]
    mutated = code[:-1] + chars[(chars.index(last) + 1) % len(chars)]
    parsed = LobbyCodeGenerator.try_parse(mutated)
    assert parsed is None or parsed.full_code != code
    assert parsed is None, "校验位不对时必须拒绝（改造前就是这么严）"


def test_parse_lobby_code_falls_back_to_terracotta():
    terracotta = "ABCDE-FGHIJ-KLMNO-PQRST-UVWXY"
    fmcl = LobbyCodeGenerator.generate().full_code
    assert parse_lobby_code(fmcl) is not None
    assert parse_lobby_code("") is None
    assert parse_lobby_code("totally-bogus") is None
    # 陶瓦码要么被 try_parse 直接拒绝、要么被第二级接住，结果不能是异常
    assert parse_lobby_code(terracotta) is None or parse_lobby_code(terracotta).network_name.startswith(
        ("terracotta-mc-", "scaffolding-mc-")
    )


def test_try_parse_terracotta_validates_checksum_and_port_lower_bound():
    """陶瓦码：25 位、5 段、第 25 位是前 24 位的和校验，端口 <100 一律拒绝。"""
    same = LobbyCodeGenerator.try_parse_terracotta("0000-0000-0000-0000-00000")
    assert same is None, "全 0 的校验和虽然自洽，但端口 0 < 100 必须拒绝"
    assert LobbyCodeGenerator.try_parse_terracotta("AAAAA-AAAAA-AAAAA-AAAAA-AAAAA") is None
    assert LobbyCodeGenerator.try_parse_terracotta("1234") is None
    assert LobbyCodeGenerator.try_parse_terracotta("") is None


def test_lobby_info_repr_shape():
    lobby = LobbyInfo("U/ABCD-EFGH-JKLM-NPQR", "scaffolding-mc-ABCD-EFGH", "JKLM-NPQR")
    assert repr(lobby) == "LobbyInfo(full_code='U/ABCD-EFGH-JKLM-NPQR', network_name='scaffolding-mc-ABCD-EFGH')"
    assert LobbyInfo.__slots__ == ("full_code", "network_name", "network_secret", "minecraft_port", "is_host")


# ═══ 端口转发命令拼装 ══════════════════════════════════════


def test_add_port_forward_builds_four_rules(easytier_dir, monkeypatch):
    """4 条规则 = tcp/udp × 本机 IPv4/IPv6，全部指向同一个随机本地端口，目标是 ip:port。"""
    monkeypatch.setattr("services.online_service._get_random_port", lambda: 45678)
    calls: List[List[str]] = []

    def fake_run(args, **kwargs):
        calls.append(list(args))
        return FakeCompleted(0, "ok", "")

    manager = make_manager(easytier_dir, run=fake_run)
    manager._running = True
    manager._rpc_port = 45678

    assert manager.add_port_forward("10.114.51.41", 40000) == 45678
    assert len(calls) == 4
    assert [c[5] for c in calls] == ["tcp", "udp", "tcp", "udp"]
    assert [c[6] for c in calls] == ["127.0.0.1:45678", "127.0.0.1:45678", "[::]:45678", "[::]:45678"]
    for call in calls:
        assert call[0].endswith("easytier-cli.exe")
        assert call[1:5] == ["--rpc-portal", "127.0.0.1:45678", "port-forward", "add"]
        assert call[7] == "10.114.51.41:40000"


def test_add_port_forward_requires_two_successes(easytier_dir, monkeypatch):
    """判定门槛是『4 条里至少 2 条成功』，与改造前一致。"""
    monkeypatch.setattr("services.online_service._get_random_port", lambda: 45678)

    def make_run(ok_count: int):
        state = {"i": 0}

        def fake_run(args, **kwargs):
            state["i"] += 1
            return FakeCompleted(0 if state["i"] <= ok_count else 1, "", "boom")

        return fake_run

    for ok_count, expected in ((1, None), (2, 45678), (4, 45678)):
        manager = make_manager(easytier_dir, run=make_run(ok_count))
        manager._running = True
        manager._rpc_port = 45678
        assert manager.add_port_forward("10.0.0.1", 1) == expected, f"{ok_count}/4 成功时"


def test_add_port_forward_is_noop_when_not_running(easytier_dir):
    """未运行时不得执行任何外部命令。"""
    calls: List[List[str]] = []
    manager = make_manager(easytier_dir, run=lambda args, **kw: (calls.append(args), FakeCompleted())[1])
    assert manager.add_port_forward("1.2.3.4", 5) is None
    manager._running = True  # 但 rpc_port 仍是 0
    assert manager.add_port_forward("1.2.3.4", 5) is None
    assert calls == []


# ═══ EasyTier 启动参数构造 ══════════════════════════════════


def _launch_capture(base: Path, monkeypatch, **launch_kwargs):
    """在**注入之后**才建管理器 —— 注入缝是构造期取的，建早了会绑到真实 ``Popen``。"""
    spawned: List[List[str]] = []
    monkeypatch.setattr("services.online_service._get_random_port", lambda: 45678)
    monkeypatch.setattr("services.online_service.ScaffoldingServer", FakeScfServer)
    monkeypatch.setattr(secrets, "token_hex", lambda n: "deadbeefdeadbeef"[: n * 2])
    manager = make_manager(base, popen=lambda args, **kwargs: (spawned.append(list(args)), FakeProc(pid=99))[1])
    rc = manager.launch(**launch_kwargs)
    return rc, (spawned[0] if spawned else [])


class FakeScfServer:
    """只为让主机路径拿到一个确定的上报端口。"""

    started_port = 40000

    def __init__(self, mc_port, player_name="Host"):
        self.mc_port, self.player_name = mc_port, player_name
        self.on_profiles_changed = None

    def start(self):
        return self.started_port

    def stop(self):
        pass


def _lobby(mc_port=25565, is_host=True):
    return LobbyInfo("U/ABCD-EFGH-JKLM-NPQR", "scaffolding-mc-ABCD-EFGH", "JKLM-NPQR", mc_port, is_host)


def test_launch_host_arguments(easytier_dir, monkeypatch):
    """主机参数：固定开关 + 虚拟 IP + scaffolding 主机名 + 两条白名单 + 两个监听 + 中继。"""
    rc, argv = _launch_capture(easytier_dir, monkeypatch, lobby=_lobby(25565, True), as_host=True, player_name="Host")

    assert rc == 0
    assert argv[0].endswith("easytier-core.exe")
    head = argv[1:14]
    assert head == [
        "--no-tun", "--multi-thread", "--enable-kcp-proxy", "--enable-quic-proxy", "--use-smoltcp",
        "--disable-sym-hole-punching", "--disable-ipv6", "--encryption-algorithm", "aes-gcm",
        "--default-protocol", "tcp", "--compression", "zstd",
    ]
    assert argv[argv.index("--network-name") + 1] == "scaffolding-mc-ABCD-EFGH"
    assert argv[argv.index("--network-secret") + 1] == "JKLM-NPQR"
    assert argv[argv.index("--machine-id") + 1] == OnlineService.machine_id()
    assert argv[argv.index("--rpc-portal") + 1] == "127.0.0.1:45678"
    assert argv[argv.index("--private-mode") + 1] == "true"
    assert "--p2p-only" in argv
    assert "--latency-first" in argv
    assert argv[argv.index("-i") + 1] == HOST_VIRTUAL_IP
    assert argv[argv.index("--hostname") + 1] == "scaffolding-mc-server-40000"
    assert argv.count("--tcp-whitelist") == 2 and argv.count("--udp-whitelist") == 2
    assert "40000" in argv and "25565" in argv
    assert argv.count("-l") == 2 and argv[argv.index("-l") + 1] == "tcp://0.0.0.0:0"
    assert argv.count("-p") == 8, "2 个注入的动态节点 + 6 个静态兜底"


def test_launch_host_whitelists_only_scf_port_when_mc_port_zero(easytier_dir, monkeypatch):
    rc, argv = _launch_capture(easytier_dir, monkeypatch, lobby=_lobby(0, True), as_host=True, player_name="Host")
    assert rc == 0
    assert argv.count("--tcp-whitelist") == 1 and argv.count("--udp-whitelist") == 1
    assert argv[argv.index("--tcp-whitelist") + 1] == "40000"
    assert "0" not in argv[argv.index("--tcp-whitelist") + 1: argv.index("--tcp-whitelist") + 2]


def test_launch_guest_arguments(easytier_dir, monkeypatch):
    """访客参数：``-d`` + 随机 hostname + 白名单 0 + 两个监听，且**没有** ``-i``。"""
    rc, argv = _launch_capture(easytier_dir, monkeypatch, lobby=_lobby(0, False), as_host=False)

    assert rc == 0
    assert "-d" in argv and "-i" not in argv
    assert argv[argv.index("--hostname") + 1] == "deadbeefdeadbeef"
    assert argv[argv.index("--tcp-whitelist") + 1] == "0"
    assert argv[argv.index("--udp-whitelist") + 1] == "0"
    assert argv.count("-l") == 2
    assert argv.count("-p") == 8


def test_launch_latency_first_switch(easytier_dir, monkeypatch):
    _, argv_on = _launch_capture(easytier_dir, monkeypatch, lobby=_lobby(), as_host=True, latency_first=True)
    _, argv_off = _launch_capture(easytier_dir, monkeypatch, lobby=_lobby(), as_host=True, latency_first=False)
    assert "--latency-first" in argv_on
    assert "--latency-first" not in argv_off
    assert len(argv_on) == len(argv_off) + 1


def test_launch_fails_when_not_installed(tmp_path, monkeypatch):
    spawned: List[List[str]] = []
    monkeypatch.setattr(
        "services.online_service.subprocess.Popen",
        lambda args, **kwargs: (spawned.append(args), FakeProc())[1],
    )
    manager = make_manager(tmp_path, run=lambda *a, **k: FakeCompleted())
    assert manager.is_installed is False
    assert manager.launch(_lobby(), as_host=True) == 1
    assert spawned == [], "没装好就不能起进程"


def test_launch_refuses_when_already_running(easytier_dir):
    manager = make_manager(easytier_dir)
    manager._running = True
    manager._process = FakeProc(returncode=None)  # poll() 返回 None = 仍在跑
    assert manager.is_running is True
    assert manager.launch(_lobby(), as_host=True) == 1


def test_launch_primes_relay_cache_without_starting_prefetch_thread(easytier_dir):
    """注入 ``relay_nodes`` 后不得起后台预取线程（离线可测，也不会偷偷联网）。"""
    manager = make_manager(easytier_dir, relay_nodes=["tcp://dyn:1"])
    assert manager._relay_nodes_cache == ["tcp://dyn:1"]
    assert manager._relay_prefetch_started is False
    manager._ensure_relay_prefetch()
    assert manager._relay_prefetch_started is False
    assert manager._resolve_relay_nodes()[:1] == ["tcp://dyn:1"]


def test_discover_host_parses_peer_list(easytier_dir):
    """peer 列表里找 ``scaffolding-mc-server-<port>`` 主机，非法端口/空 ipv4 都跳过。"""
    payload = json.dumps([
        {"hostname": "someone-else", "ipv4": "10.0.0.9"},
        {"hostname": "scaffolding-mc-server-not-a-port", "ipv4": "1.2.3.4"},
        {"hostname": "scaffolding-mc-server-40000", "ipv4": ""},
        {"hostname": "scaffolding-mc-server-40000", "ipv4": "10.114.51.41"},
    ])
    manager = make_manager(easytier_dir, run=lambda args, **kw: FakeCompleted(0, payload, ""))
    manager._running = True
    manager._rpc_port = 45678
    assert manager.discover_host(timeout=0.5) == ("10.114.51.41", 40000)


def test_discover_host_returns_none_when_not_running(easytier_dir):
    calls: List[List[str]] = []
    manager = make_manager(easytier_dir, run=lambda args, **kw: (calls.append(args), FakeCompleted())[1])
    assert manager.discover_host(timeout=0.5) is None
    assert calls == []


# ═══ 局域网广播报文解析 ════════════════════════════════════


@pytest.mark.parametrize(
    "message,expected",
    [
        ("[MOTD]A Minecraft Server[/MOTD][AD]25565[/AD]", (25565, "A Minecraft Server")),
        ("[MOTD]乱码 §e大厅[/MOTD][AD]5000[/AD]", (5000, "乱码 §e大厅")),
        ("[AD]12345[/AD]", (12345, "Unknown")),          # 没有 MOTD 用 Unknown 兜底
        ("[MOTD][/MOTD][AD]25565[/AD]", (25565, "")),    # 空 MOTD 就是空串
        ("[MOTD]only motd[/MOTD]", None),                # AD 缺失即丢弃
        ("garbage", None),
        ("", None),
        ("[MOTD]a[/MOTD][MOTD]b[/MOTD][AD]7[/AD]", (7, "a")),  # 取第一个 MOTD
    ],
)
def test_parse_broadcast_message(message, expected):
    assert parse_broadcast_message(message) == expected


def test_parse_broadcast_message_defaults_are_the_class_regexes():
    """默认正则必须与 ``BroadcastListener`` 的类属性是同一对象（搬家前是同一个正则）。"""
    assert parse_broadcast_message.__defaults__ == (MOTD_RE, AD_RE)
    assert BroadcastListener.MOTD_RE is MOTD_RE
    assert BroadcastListener.AD_RE is AD_RE
    assert AD_RE.search("[AD]12[/AD]").group(1) == "12"


def test_read_varint_and_write_varint_roundtrip():
    from services.online_service import _read_varint, _write_varint

    for value in (0, 1, 127, 128, 25565, 767, 2 ** 21, 2 ** 31 - 1):
        buf = bytearray()
        _write_varint(buf, value)
        assert _read_varint(FakeSock([bytes(buf)])) == value


# ═══ 陶瓦协议的帧收发 ══════════════════════════════════════


def test_scaffolding_server_handles_ping_and_port():
    server = ScaffoldingServer(25565, "Host")
    assert server._handle_request("c:ping", b"fingerprint") == (0, b"fingerprint")
    assert server._handle_request("c:server_port", b"") == (0, struct.pack(">H", 25565))
    protocols = server._handle_request("c:protocols", b"")[1].decode("ascii").split("\0")
    assert protocols == list(ScaffoldingServer._SUPPORTED_PROTOCOLS)
    assert server._handle_request("c:unknown", b"") == (255, b"Requested protocol hasn't been implemented.")


def test_scaffolding_server_frame_parsing_handles_partial_and_packed_frames():
    """一帧不全时不得消费缓冲区；两帧粘在一起时必须连着都处理掉。"""
    server = ScaffoldingServer(25565, "Host")
    client = FakeSock()
    full = frame("c:ping", b"fp")

    buf = bytearray(full[:3])
    assert server._try_process_frame(client, buf) is False
    assert bytes(buf) == full[:3], "不完整的帧不能动缓冲区"

    buf = bytearray(full + frame("c:server_port"))
    processed = 0
    while server._try_process_frame(client, buf):
        processed += 1
    assert processed == 2 and bytes(buf) == b""
    assert len(client.sent) == 2 * (1 + 4) + 2 + 2, "两帧响应：c:ping 回显 2 字节 + 端口 2 字节"


@pytest.mark.parametrize(
    "bad",
    [
        b"",                                       # 空
        bytes([0]),                                # 类型长度 0
        bytes([129]) + b"x" * 4,                   # 类型长度 > 128
        bytes([3]) + b"abc",                       # 头部还没凑齐
        struct.pack(">B", 3) + b"abc" + struct.pack(">I", 70000),  # body 长度越界
    ],
)
def test_scaffolding_server_rejects_malformed_frames(bad):
    server = ScaffoldingServer(25565, "Host")
    buf = bytearray(bad)
    assert server._try_process_frame(FakeSock(), buf) is False
    assert bytes(buf) == bad, "畸形帧不得被消费"


def test_scaffolding_client_builds_request_frames():
    """请求帧 = ``len(type)|type|len(body)|body``；dict 体按 UTF-8 JSON 编码。"""
    client = ScaffoldingClient("127.0.0.1", 40000, "Guest", "m-guest", "v")
    client._running = True
    client._sock = FakeSock([b"\x00" + struct.pack(">I", 2), b"hi"])

    assert client._send_request("c:ping", b"fp") == (0, b"hi")
    sent = bytes(client._sock.sent)
    assert sent[:1] == bytes([6]) and sent[1:7] == b"c:ping"
    assert struct.unpack(">I", sent[7:11])[0] == 2 and sent[11:] == b"fp"

    client._sock = FakeSock([b"\x00" + struct.pack(">I", 0)])
    client._send_request("c:player_ping", {"name": "Guest"})
    sent = bytes(client._sock.sent)
    offset = 1 + len(b"c:player_ping") + 4
    assert json.loads(sent[offset:].decode("utf-8")) == {"name": "Guest"}


@pytest.mark.parametrize("body", [5, "text", object()])
def test_scaffolding_client_rejects_unsupported_body_types(body):
    client = ScaffoldingClient("127.0.0.1", 40000, "Guest", "m-guest", "v")
    client._running = True
    client._sock = FakeSock([b"\x00" + struct.pack(">I", 0)])
    with pytest.raises(TypeError):
        client._send_request("c:ping", body)


def test_scaffolding_client_request_requires_connection():
    client = ScaffoldingClient("127.0.0.1", 40000, "Guest", "m-guest", "v")
    with pytest.raises(ConnectionError):
        client._send_request("c:ping", None)


def test_scaffolding_client_fetch_profiles_only_notifies_on_change():
    profiles = [
        {"name": "Host", "machine_id": "m-host", "vendor": "v", "kind": "HOST"},
        {"name": "Guest", "machine_id": "m-guest", "vendor": "v", "kind": "GUEST"},
    ]

    def payload(items):
        body = json.dumps(items, ensure_ascii=False).encode()
        return [b"\x00" + struct.pack(">I", len(body)), body]

    client = ScaffoldingClient("127.0.0.1", 40000, "Guest", "m-guest", "v")
    client._running = True
    seen: List[list] = []
    client.on_player_list_changed = seen.append

    for items in (profiles, profiles, profiles + [{"name": "T", "machine_id": "m-3", "vendor": "v", "kind": "GUEST"}]):
        client._sock = FakeSock(payload(items))
        client._fetch_profiles()

    assert [[p["machine_id"] for p in batch] for batch in seen] == [
        ["m-host", "m-guest"],
        ["m-host", "m-guest", "m-3"],
    ]
    assert client.profiles[-1]["name"] == "T"


# ═══ 主句端口解析 ══════════════════════════════════════════


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("", (25565, "25565")),          # 空串回落到 25565
        ("   ", (25565, "25565")),
        ("25565", (25565, "25565")),
        (" 25565 ", (25565, "25565")),
        ("1", (1, "1")),
        ("65535", (65535, "65535")),
        ("0", (None, "0")),              # 越界
        ("65536", (None, "65536")),
        ("-1", (None, "-1")),
        ("abc", (None, "abc")),
        ("25 565", (None, "25 565")),
        ("0x10", (None, "0x10")),
        ("+5", (5, "+5")),               # int() 接受正号，改造前就是这样
    ],
)
def test_parse_mc_port(raw, expected):
    assert parse_mc_port(raw) == expected


# ═══ 界面薄委托与别名语义 ══════════════════════════════════


def test_ui_reexports_identical_objects():
    """``ui.app_online.X is services.online_service.X``：旧路径打补丁/做 is 判定与搬家前一致。"""
    import services.online_service as svc
    import ui.app_online as ui_mod

    for name in (
        "EasyTierManager", "ScaffoldingServer", "ScaffoldingClient", "LobbyCodeGenerator",
        "LobbyInfo", "LobbyState", "McPing", "McBroadcastSimulator", "BroadcastListener",
        "GameWatcher", "TcpPortForwarder", "OnlineService",
    ):
        assert getattr(ui_mod, name) is getattr(svc, name), name
    assert ui_mod.EASYTIER_VERSION == svc.EASYTIER_VERSION == EASYTIER_VERSION
    assert ui_mod.LOOPBACK == svc.LOOPBACK == LOOPBACK


def test_ui_mixin_kept_every_public_method():
    """公开方法名与签名一律不变（薄委托只换内部实现）。"""
    expected = {
        "_build_online_tab_content", "_build_online_unsupported_tab", "_init_online_state",
        "_build_online_control_panel", "_build_online_env_section", "_build_online_discover_section",
        "_set_lobby_state", "_on_discover_worlds", "_on_world_discovered", "_build_online_create_section",
        "_build_online_join_section", "_build_online_lobby_section", "_build_online_tips_section",
        "_build_online_compat_section", "_build_online_output_panel", "_append_online_log",
        "_set_online_status", "_update_env_easytier_label", "_run_online_thread", "_get_display_name",
        "_check_logged_in", "_get_login_error", "_start_member_poll", "_poll_members_loop",
        "_stop_member_poll", "_refresh_members", "_on_members_changed", "_on_server_shutdown_detected",
        "_on_setup_environment", "_on_create_lobby", "_on_host_network_ready", "_on_host_game_stopped",
        "_on_join_lobby", "_setup_guest_connection", "_on_leave_lobby", "_reset_lobby_state",
        "_on_easytier_exited", "_on_copy_code", "_online_service",
    }
    actual = {n for n in vars(OnlineTabMixin) if not n.startswith("__")}
    assert expected <= actual, f"丢了方法：{sorted(expected - actual)}"


def test_ui_refresh_members_renders_through_service():
    """``_refresh_members`` 的可见文本与改造前一致（含延迟行与『无成员』占位）。"""

    class FakeLabel:
        def __init__(self):
            self.text = None

        def configure(self, **kw):
            self.text = kw["text"]

    class FakeClient:
        def __init__(self, profiles, latency=0, connected=True):
            self._profiles, self.latency_ms, self.is_connected = profiles, latency, connected

        @property
        def profiles(self):
            return list(self._profiles)

    def render(is_host, profiles, latency=0, connected=True):
        label = FakeLabel()
        fake = type("FakeSelf", (), {})()
        fake._is_host = is_host
        fake._scf_client = FakeClient(profiles, latency, connected)
        fake._et_manager = type("M", (), {"_scf_server": type("S", (), {"all_profiles": profiles})()})()
        fake._online_members_label = label
        OnlineTabMixin._refresh_members(fake)
        return label.text

    host_view = render(True, [
        {"name": "Host", "vendor": "FMCL 1", "kind": "HOST"},
        {"name": "Guest", "vendor": "HMCL", "kind": "GUEST"},
    ])
    assert host_view == "👑 Host · FMCL 1\n👤 Guest · HMCL"

    assert render(False, [{"name": "Host", "vendor": "v", "kind": "HOST"}], latency=42).endswith("\n⏱ 42ms")
    from ui.i18n import _ as tr

    assert render(False, [], latency=0) == tr("online_no_members"), "空列表要落到『无成员』文案"
    assert render(False, [], latency=0, connected=False) is None, "未连接时早退，不写标签"


def test_ui_set_lobby_state_delegates_to_state_machine():
    fake = type("FakeSelf", (), {})()
    OnlineTabMixin._set_lobby_state(fake, LobbyState.IDLE)
    assert fake._lobby_state is LobbyState.IDLE, "首次调用要先补一个 IDLE 兜底"
    OnlineTabMixin._set_lobby_state(fake, LobbyState.CONNECTED)
    assert fake._lobby_state is LobbyState.CONNECTED
    OnlineTabMixin._set_lobby_state(fake, LobbyState.CONNECTED)
    assert fake._lobby_state is LobbyState.CONNECTED


def test_factories_return_the_moved_classes():
    """服务工厂产出的对象类型必须还是搬家前那些类（不是包装）。"""
    service = OnlineService()
    assert isinstance(service.create_broadcast_listener(True), BroadcastListener)
    assert isinstance(service.create_broadcast_simulator(), McBroadcastSimulator)
    assert isinstance(service.create_ping("127.0.0.1", 1), McPing)
    assert isinstance(service.create_game_watcher(1), GameWatcher)
    assert isinstance(service.create_tcp_forwarder(1, "127.0.0.1", 2), TcpPortForwarder)
    assert isinstance(service.create_scaffolding_client("127.0.0.1", 1, "n", "m", "v"), ScaffoldingClient)
    assert TcpPortForwarder.MAX_CONNECTIONS == 10
    assert service.vendor().startswith("FMCL ")
    assert service.easytier_base_dir().name == "easytier-windows-x86_64"
    assert GameWatcher._CHECK_INTERVAL == 15.0 and GameWatcher._INITIAL_DELAY == 5.0


def test_download_uses_injected_opener_and_url_list(tmp_path, monkeypatch):
    """下载源与 HTTP 入口都可注入：全程离线，走完『测速 → 下载 → 解压 → 落位』。"""
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in ("easytier-core.exe", "easytier-cli.exe", "Packet.dll"):
            zf.writestr("nested/" + name, b"MZ-fake")
    payload = buf.getvalue()

    calls: List[str] = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n=None):
            return payload if n is None else payload[:n]

    def opener(req, timeout=None):
        calls.append(getattr(req, "full_url", req))
        return Resp()

    monkeypatch.setattr("services.online_service.EASYTIER_DOWNLOAD_URLS", ["https://fake/{version}.zip"])
    base = tmp_path / "FMCL" / "EasyTier" / EASYTIER_VERSION / "easytier-windows-x86_64"
    manager = make_manager(base, urlopen=opener)
    progress: List[str] = []

    assert manager.download(on_progress=progress.append) is True
    # 两次请求：先测速探活（Range 探针），再真正下载
    assert calls == [f"https://fake/{EASYTIER_VERSION}.zip"] * 2
    assert sorted(p.name for p in base.iterdir()) == ["Packet.dll", "easytier-cli.exe", "easytier-core.exe"]
    assert manager.is_installed is True
    assert progress == [
        "正在测速选择最快的下载镜像...",
        f"正在下载 EasyTier v{EASYTIER_VERSION}...",
        "正在解压 EasyTier...",
    ]


def test_download_returns_false_when_all_mirrors_fail(tmp_path, monkeypatch):
    def dead(req, timeout=None, **kw):
        raise OSError("down")

    monkeypatch.setattr("services.online_service.EASYTIER_DOWNLOAD_URLS", ["https://fake/{version}.zip"])
    base = tmp_path / "EasyTier" / EASYTIER_VERSION / "easytier-windows-x86_64"
    manager = make_manager(base, urlopen=dead)
    assert manager.download() is False
    assert manager.is_installed is False


def test_urllib_is_not_touched_when_dependencies_are_injected(tmp_path, monkeypatch):
    """注入缝必须真的生效：把全局 ``urlopen`` 换成会炸的实现也不该被碰到。"""
    import urllib.request

    def boom(*a, **k):
        raise AssertionError("注入生效时不得走全局 urlopen")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr("services.online_service.EASYTIER_DOWNLOAD_URLS", ["https://fake/{version}.zip"])
    base = tmp_path / "EasyTier" / EASYTIER_VERSION / "easytier-windows-x86_64"

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, n=None):
            return b"not-a-zip"

    manager = make_manager(base, urlopen=lambda req, timeout=None, **kw: Resp())
    assert manager.download() is False, "坏 zip 要返回 False 而不是抛异常"
    assert manager._speed_test_download_urls(["https://x"], "ua", lambda req, timeout=None: Resp()) == ["https://x"]



