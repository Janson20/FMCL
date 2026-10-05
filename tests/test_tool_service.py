"""阶段 1 任务 1.6「工具服务」抽取的永久回归守卫。

``ui/app_tools.py``（``ToolsTabMixin``，2172 行 / 57 个方法）里 8 个工具的
算法与 IO 已搬进 ``services/tool_service.py``：垃圾清理、端口检测、哈希计算、
坐标转换、每日运势、MC 冷知识、MC 知识问答、多线程下载器。

这里守住的五件事：

1. **服务零 UI 依赖、可脱离 AppContext 独立实例化**（阶段 1 的核心诉求）；
2. 8 个工具的核心算法与边界行为（空输入 / 非法输入 / 超大输入 / 路径不存在 /
   权限失败 / 损坏文件 / 取消）；
3. 全部**离线**：网络走注入的假 socket 与假 HTTP 会话，文件只碰 ``tmp_path``，
   绝不真删用户文件、绝不真连网；
4. 界面侧不再重复实现这些逻辑，且常量/工具函数在两侧是**同一个对象**；
5. 搬运中查明的既有缺陷行为被显式钉住，且**逐条标注**
   「记录现状、疑为缺陷」—— 修缺陷时必须一并更新这些用例。

搬运方式见 ``poc/build_app_tools.py``（按行区间拼接，未列清单的行不可能被改动）
与 ``poc/probe_tool_parity.py``（旧实现 vs 新实现的同输入对照，87 项全过）。
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import random
import struct
import time
from pathlib import Path

import pytest

from services import tool_service as S
from services.errors import InvalidArgument, ServiceNotAvailable
from services.i18n_service import _

REPO_ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════
# 假外部依赖（全部离线）
# ═══════════════════════════════════════════════════════════════════


class FakeSocket:
    """可编程的假 socket：记录发出的字节，按脚本吐回响应。"""

    def __init__(self, *, fail_on_connect=None, incoming=b"", fail_on_close=False, fail_on_recv=None):
        self.fail_on_connect = fail_on_connect
        self.fail_on_close = fail_on_close
        self.fail_on_recv = fail_on_recv
        self._incoming = bytearray(incoming)
        self.sent = bytearray()
        self.timeout = None
        self.shutdown_called = False
        self.closed = False
        self.connected_to = None

    def settimeout(self, value):
        self.timeout = value

    def connect(self, addr):
        self.connected_to = addr
        if self.fail_on_connect:
            raise OSError(self.fail_on_connect)

    def shutdown(self, how):
        self.shutdown_called = True

    def sendall(self, data):
        self.sent.extend(data)

    def recv(self, n):
        if self.fail_on_recv:
            raise OSError(self.fail_on_recv)
        chunk = bytes(self._incoming[:n])
        del self._incoming[:n]
        return chunk

    def close(self):
        self.closed = True
        if self.fail_on_close:
            raise OSError("close 失败")


def socket_factory_returning(sock):
    """返回一个"永远产出同一个假 socket"的工厂。"""

    def _factory(*args, **kwargs):
        return sock

    return _factory


def mc_status_stream(payload: dict) -> bytes:
    """拼出真实协议形状的 Status Response：长度 / 包 id / 字符串长度 / JSON。"""
    body = json.dumps(payload).encode("utf-8")
    buf = bytearray()
    S._mc_write_varint(buf, len(body))  # 包长度
    S._mc_write_varint(buf, 0x00)  # 包 id
    S._mc_write_varint(buf, len(body))  # 字符串长度
    buf.extend(body)
    return bytes(buf)


class FakeResponse:
    def __init__(self, body: bytes, headers=None, status=200):
        self._body = body
        self.headers = headers or {}
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i : i + chunk_size]

    def close(self):
        pass


class FakeSession:
    """假 HTTP 会话：``head`` 报长度，``get`` 按 Range 切片。"""

    def __init__(self, body: bytes, *, announce_length=True, status=200, fail_on_head=False):
        self.body = body
        self.announce_length = announce_length
        self.status = status
        self.fail_on_head = fail_on_head
        self.head_calls = []
        self.get_calls = []

    def head(self, url, headers=None, timeout=None):
        self.head_calls.append((url, dict(headers or {}), timeout))
        if self.fail_on_head:
            raise RuntimeError("HEAD 失败")
        return FakeResponse(b"", {"Content-Length": str(len(self.body))} if self.announce_length else {}, self.status)

    def get(self, url, headers=None, stream=False, timeout=None):
        hdrs = dict(headers or {})
        self.get_calls.append((url, hdrs, timeout))
        rng = hdrs.get("Range")
        if rng:
            a, _, b = rng.split("=", 1)[1].partition("-")
            start, end = int(a), int(b)
            if end < start:
                return FakeResponse(b"", {}, self.status)
            return FakeResponse(self.body[start : end + 1], {}, self.status)
        return FakeResponse(self.body, {}, self.status)


# ═══════════════════════════════════════════════════════════════════
# 0. 服务基础：能独立实例化、零 UI 依赖
# ═══════════════════════════════════════════════════════════════════


def test_service_instantiates_without_app_context():
    """阶段 1 的 Tk 界面没有 AppContext，服务必须能裸构造。"""
    svc = S.ToolService()
    assert svc.name == "tool"
    assert svc.label == "工具箱"
    assert svc.attached is False


def test_service_context_raises_when_not_attached():
    svc = S.ToolService()
    with pytest.raises(ServiceNotAvailable):
        svc.context


def test_service_methods_work_without_app_context(tmp_path):
    """不碰 self.ui / self.tasks / self.config，全部方法都要能在无 context 下跑。"""
    svc = S.ToolService()
    assert svc.format_size(2048) == "2.0 KB"
    assert svc.parse_coordinate(" 12 ") == 12
    assert svc.is_protected_system_dir(str(tmp_path)) is False
    assert svc.convert_coordinates(8, 64, -8, "nether").rx == 1
    assert svc.daily_fortune("2026-09-26").value >= 0
    assert svc.fact_of_the_day("2026-09-26") < len(S.MC_FACTS)
    assert isinstance(svc.scan_junk_files(str(tmp_path), 1), S.JunkScanResult)


def test_service_module_has_no_ui_imports():
    """静态再确认一次（scripts/check_services_purity.py 是权威检查）。"""
    src = (REPO_ROOT / "services" / "tool_service.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    banned = {
        "tkinter",
        "customtkinter",
        "tkinterdnd2",
        "tkinterweb",
        "tkhtmlview",
        "PySide6",
        "shiboken6",
        "ui",
    }
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.split(".")[0] in banned]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in banned:
                found.append(node.module)
    assert found == []


def test_service_has_no_widget_or_dialog_calls():
    """服务不得弹窗、不得碰控件（只用注入端口时也要显式检查一遍）。"""
    src = (REPO_ROOT / "services" / "tool_service.py").read_text(encoding="utf-8")
    for token in ("messagebox", "CTkLabel", "CTkButton", "clipboard_append", "filedialog"):
        assert token not in src, f"服务里出现了界面专用标识符 {token}"


# ═══════════════════════════════════════════════════════════════════
# 1. 通用：_format_size
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("count", "expected"),
    [
        (0, "0 B"),
        (1023, "1023 B"),
        (-1, "-1 B"),  # 记录现状：负数走第一个分支，不去做合理性校验
        (1024, "1.0 KB"),
        (1024 * 1024 - 1, "1024.0 KB"),
        (1024 * 1024, "1.0 MB"),
        (1024**3 - 1, "1024.0 MB"),
        (1024**3, "1.00 GB"),
        (5 * 1024**3 + 512 * 1024**2, "5.50 GB"),
    ],
)
def test_format_size_boundaries(count, expected):
    assert S._format_size(count) == expected


# ═══════════════════════════════════════════════════════════════════
# 2. 垃圾清理
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(("text", "expected"), [("5", 5), ("  7 ", 7), ("0", 1), ("-3", 1), ("1", 1), ("10**6", None)])
def test_parse_max_depth(text, expected):
    if expected is None:
        with pytest.raises(InvalidArgument):
            S.parse_max_depth(text)
    else:
        assert S.parse_max_depth(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "5.5", "None", "一"])
def test_parse_max_depth_invalid(text):
    with pytest.raises(InvalidArgument):
        S.parse_max_depth(text)


def test_parse_max_depth_ignores_huge_value():
    """超大深度不该被截断（原实现只做 max(1, x)）。"""
    assert S.parse_max_depth("999999999") == 999999999


def test_is_protected_system_dir(tmp_path):
    root = os.environ.get("SystemRoot", r"C:\Windows")
    assert S.is_protected_system_dir(root) is True
    assert S.is_protected_system_dir(os.path.join(root, "System32")) is True
    assert S.is_protected_system_dir(root + "10") is False  # 前缀陷阱
    assert S.is_protected_system_dir(str(tmp_path)) is False


def test_relative_path_from_normal(tmp_path):
    assert S.relative_path_from(str(tmp_path / "a" / "b.log"), str(tmp_path)) == os.path.join("a", "b.log")
    assert S.relative_path_from(str(tmp_path), str(tmp_path)) == "."


def test_relative_path_from_falls_back_on_value_error(monkeypatch):
    """跨盘符时 os.path.relpath 抛 ValueError，原实现回退成原路径。"""

    def _boom(*a, **k):
        raise ValueError("path is on mount 'D:', start on mount 'C:'")

    monkeypatch.setattr(S.os.path, "relpath", _boom)
    assert S.relative_path_from(r"D:\x\y.log", r"C:\base") == r"D:\x\y.log"


def make_junk_tree(root: Path):
    (root / "logs" / "deep" / "deeper").mkdir(parents=True)
    files = {
        "a.log": 10,
        "b.tmp": 200,
        "keep.txt": 5,
        "logs/c.log": 30,
        "logs/deep/d.tmp": 40,
        "logs/deep/deeper/e.log": 50,
    }
    for rel, size in files.items():
        (root / rel).write_bytes(b"x" * size)
    return files


def test_scan_junk_files_collects_and_sorts(tmp_path):
    make_junk_tree(tmp_path)
    result = S.scan_junk_files(str(tmp_path), 5)
    assert result.base == os.path.abspath(str(tmp_path))
    assert [os.path.basename(fp) for fp, _ in result.files] == ["b.tmp", "e.log", "d.tmp", "c.log", "a.log"]
    assert result.total_size == 10 + 200 + 30 + 40 + 50
    assert all(fp.endswith((".log", ".tmp")) for fp, _ in result.files)


@pytest.mark.parametrize(
    ("depth", "expected"),
    [
        # 记录现状：剪枝发生在"收集完本层文件之后"，所以 depth=1 也会收下 logs/ 里的第一层文件
        (1, {"a.log", "b.tmp", "c.log"}),
        (2, {"a.log", "b.tmp", "c.log", "d.tmp"}),
        (3, {"a.log", "b.tmp", "c.log", "d.tmp", "e.log"}),
        (9, {"a.log", "b.tmp", "c.log", "d.tmp", "e.log"}),
    ],
)
def test_scan_junk_files_respects_depth(tmp_path, depth, expected):
    make_junk_tree(tmp_path)
    result = S.scan_junk_files(str(tmp_path), depth)
    names = {os.path.basename(fp) for fp, _ in result.files}
    assert names == expected


def test_scan_junk_files_missing_dir_returns_empty(tmp_path):
    result = S.scan_junk_files(str(tmp_path / "不存在"), 5)
    assert result.files == [] and result.total_size == 0


def test_scan_junk_files_empty_dir(tmp_path):
    result = S.scan_junk_files(str(tmp_path), 5)
    assert result.files == [] and result.total_size == 0


def test_scan_junk_files_getsize_failure_counts_as_zero(tmp_path, monkeypatch):
    """权限失败 / 文件消失时 os.path.getsize 抛 OSError，原实现按 0 计。"""
    make_junk_tree(tmp_path)
    real = os.path.getsize

    def _flaky(path):
        if str(path).endswith("b.tmp"):
            raise PermissionError("拒绝访问")
        return real(path)

    monkeypatch.setattr(S.os.path, "getsize", _flaky)
    result = S.scan_junk_files(str(tmp_path), 5)
    sizes = {os.path.basename(fp): size for fp, size in result.files}
    assert sizes["b.tmp"] == 0
    assert sizes["a.log"] == 10
    assert result.total_size == 10 + 30 + 40 + 50


def test_scan_junk_files_skips_system_root(tmp_path, monkeypatch):
    """扫描时不得进入 %SystemRoot%。"""
    make_junk_tree(tmp_path)
    real_walk = os.walk
    entered = []

    def _walk(top, *a, **k):
        for root, dirs, files in real_walk(top, *a, **k):
            entered.append(root)
            yield root, dirs, files

    monkeypatch.setattr(S.os, "walk", _walk)
    monkeypatch.setattr(S, "is_protected_system_dir", lambda p: os.path.basename(p) == "deep")
    result = S.scan_junk_files(str(tmp_path), 5)
    names = {os.path.basename(fp) for fp, _ in result.files}
    assert "d.tmp" not in names and "e.log" not in names  # deep 被剪掉
    assert "c.log" in names


def test_scan_junk_files_large_tree(tmp_path):
    """超大输入：500 个文件。"""
    for i in range(500):
        (tmp_path / f"f{i}.log").write_bytes(b"x" * (i % 7))
    result = S.scan_junk_files(str(tmp_path), 3)
    assert len(result.files) == 500
    assert [s for _f, s in result.files] == sorted((i % 7 for i in range(500)), reverse=True)


def test_group_junk_files_orders_by_group_total(tmp_path):
    make_junk_tree(tmp_path)
    scan = S.scan_junk_files(str(tmp_path), 5)
    groups = S.group_junk_files(scan.base, scan.files)
    assert [g.display for g in groups] == [
        tmp_path.name,
        os.path.join("logs", "deep", "deeper"),
        os.path.join("logs", "deep"),
        "logs",
    ]
    assert sum(len(g.files) for g in groups) == len(scan.files)


def test_group_junk_files_is_stable_on_ties(tmp_path):
    """组内总大小相同时保持首次出现顺序（sorted 稳定）。"""
    files = [(str(tmp_path / "b" / "x.log"), 10), (str(tmp_path / "a" / "y.log"), 10)]
    groups = S.group_junk_files(str(tmp_path), files)
    assert [os.path.basename(g.dir) for g in groups] == ["b", "a"]


def test_group_junk_files_empty():
    assert S.group_junk_files("/base", []) == []


def test_junk_dir_display(tmp_path):
    assert S.junk_dir_display(".", str(tmp_path)) == tmp_path.name
    assert S.junk_dir_display("sub/dir", str(tmp_path)) == "sub/dir"
    # 记录现状：根目录是盘符（rstrip 后 basename 为空）时回退成完整路径
    assert S.junk_dir_display(".", "D:\\") == "D:\\"


def test_delete_junk_files_counts(tmp_path):
    paths = [tmp_path / "a.log", tmp_path / "b.tmp"]
    for i, p in enumerate(paths):
        p.write_bytes(b"x" * (10 * (i + 1)))
    result = S.delete_junk_files([("k", str(p), 10 * (i + 1)) for i, p in enumerate(paths)])
    assert (result.deleted, result.failed, result.deleted_size) == (2, 0, 30)
    assert not any(p.exists() for p in paths)


def test_delete_junk_files_partial_failure(tmp_path):
    """不存在的文件只计 failed，不打断其余删除（原实现吞掉 OSError）。"""
    ok = tmp_path / "ok.log"
    ok.write_bytes(b"x" * 4)
    result = S.delete_junk_files([("k", str(tmp_path / "missing.log"), 99), ("k", str(ok), 4)])
    assert (result.deleted, result.failed, result.deleted_size) == (1, 1, 4)
    assert not ok.exists()


def test_delete_junk_files_permission_failure(tmp_path, monkeypatch):
    target = tmp_path / "locked.log"
    target.write_bytes(b"x")
    real_remove = os.remove

    def _denied(path):
        if str(path).endswith("locked.log"):
            raise PermissionError("拒绝访问")
        return real_remove(path)

    monkeypatch.setattr(S.os, "remove", _denied)
    result = S.delete_junk_files([("k", str(target), 1)])
    assert (result.deleted, result.failed) == (0, 1)
    assert target.exists()  # 绝不真删


def test_delete_junk_files_empty_selection():
    result = S.delete_junk_files([])
    assert (result.deleted, result.failed, result.deleted_size) == (0, 0, 0)


# ═══════════════════════════════════════════════════════════════════
# 3. 端口检测
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(("text", "expected"), [("1", 1), ("25565", 25565), (" 443 ", 443), ("65535", 65535)])
def test_parse_port_valid(text, expected):
    assert S.parse_port(text) == expected


@pytest.mark.parametrize("text", ["0", "65536", "-1", "abc", "", "  ", "80.5"])
def test_parse_port_invalid(text):
    with pytest.raises(InvalidArgument):
        S.parse_port(text)


def test_parse_port_range_check_is_optional():
    """记录现状（疑为缺陷）：_on_test_port 做范围校验、_on_peek_server 不做。"""
    assert S.parse_port("99999", validate_range=False) == 99999
    with pytest.raises(InvalidArgument):
        S.parse_port("99999")


def test_probe_port_open():
    sock = FakeSocket()
    result = S.probe_port("127.0.0.1", 25565, socket_factory=socket_factory_returning(sock))
    assert (result.host, result.port, result.status, result.error) == ("127.0.0.1", 25565, "open", None)
    assert result.latency_ms >= 0
    assert sock.connected_to == ("127.0.0.1", 25565)
    assert sock.timeout == 5
    assert sock.shutdown_called is True
    assert sock.closed is True


def test_probe_port_closed():
    sock = FakeSocket(fail_on_connect="connection refused")
    result = S.probe_port("example.invalid", 80, socket_factory=socket_factory_returning(sock))
    assert result.status == "closed"
    assert result.error == "connection refused"
    assert sock.closed is True


def test_probe_port_close_failure_is_swallowed():
    sock = FakeSocket()
    sock.fail_on_close = True
    result = S.probe_port("127.0.0.1", 1, socket_factory=socket_factory_returning(sock))
    assert result.status == "open"


def test_probe_port_timeout_is_injectable():
    sock = FakeSocket()
    S.probe_port("h", 1, socket_factory=socket_factory_returning(sock), timeout=0.25)
    assert sock.timeout == 0.25


def test_probe_port_result_is_a_tuple():
    """PortProbeResult 是 tuple 子类，界面里 `for h, p, st, lat, e in ...` 的拆包不能坏。"""
    r = S.probe_port("h", 1, socket_factory=socket_factory_returning(FakeSocket()))
    h, p, st, lat, err = r
    assert (h, p, st, err) == ("h", 1, "open", None)
    assert lat == r.latency_ms


def test_peek_server_parses_status():
    payload = {
        "description": {"text": "A Minecraft Server"},
        "version": {"name": "1.20.1"},
        "players": {"online": 3, "max": 20},
    }
    sock = FakeSocket(incoming=mc_status_stream(payload))
    result = S.peek_server("localhost", 25565, socket_factory=socket_factory_returning(sock))
    assert result.error is None
    assert result.info == payload
    assert result.latency_ms >= 0
    assert sock.closed is True


def test_peek_server_handshake_bytes():
    """握手包：协议号 767、主机名、端口、下一状态 1，随后是 0x01 0x00 状态请求。"""
    sock = FakeSocket(incoming=mc_status_stream({"description": "x"}))
    S.peek_server("mc.example", 25565, socket_factory=socket_factory_returning(sock))
    sent = bytes(sock.sent)
    assert sent.endswith(b"\x01\x00")
    # 端口以 big-endian 出现在包里
    assert struct.pack(">H", 25565) in sent
    assert b"mc.example" in sent
    # 协议号 767 的 varint 是 0xFF 0x05
    assert b"\xff\x05" in sent


def test_peek_server_connection_failure():
    sock = FakeSocket(fail_on_connect="timed out")
    result = S.peek_server("localhost", 25565, socket_factory=socket_factory_returning(sock))
    assert result.info is None
    assert result.error == "timed out"


def test_peek_server_closed_stream_reports_error():
    """服务端直接断开：_mc_read_varint 抛 ConnectionError，被收敛成 error 文本。"""
    sock = FakeSocket(incoming=b"")
    result = S.peek_server("localhost", 25565, socket_factory=socket_factory_returning(sock))
    assert result.info is None
    assert result.error == "Connection closed"


def test_peek_server_bad_json_reports_error():
    body = b"not json"
    buf = bytearray()
    S._mc_write_varint(buf, len(body))
    S._mc_write_varint(buf, 0)
    S._mc_write_varint(buf, len(body))
    buf.extend(body)
    sock = FakeSocket(incoming=bytes(buf))
    result = S.peek_server("localhost", 25565, socket_factory=socket_factory_returning(sock))
    assert result.info is None
    assert result.error is not None


def test_mc_varint_roundtrip():
    for value in (0, 1, 127, 128, 255, 767, 16383, 16384, 2147483647):
        buf = bytearray()
        S._mc_write_varint(buf, value)
        decoded = 0
        shift = 0
        remaining = bytearray(buf)
        while True:
            byte = remaining.pop(0)
            decoded |= (byte & 0x7F) << shift
            if not (byte & 0x80):
                break
            shift += 7
        assert decoded == value


def test_mc_read_varint_raises_on_closed_stream():
    with pytest.raises(ConnectionError):
        S._mc_read_varint(FakeSocket(incoming=b""))


# ═══════════════════════════════════════════════════════════════════
# 4. 服务器信息解析
# ═══════════════════════════════════════════════════════════════════


def test_summarize_server_info_full():
    info = {
        "description": {"text": "§aHello", "extra": [{"text": " World"}]},
        "version": {"name": "1.20.1"},
        "players": {"online": 5, "max": 10, "sample": [{"name": "alice"}, "bob"]},
    }
    s = S.summarize_server_info(info)
    # 记录现状：extra 非空时**顶替** text（不是拼接），所以 "Hello" 不出现
    assert s.desc_text == "World"
    assert s.version_name == "1.20.1"
    assert (s.online, s.max_players) == (5, 10)
    assert s.sample_text == "alice | bob"


def test_summarize_server_info_uses_text_when_extra_is_empty():
    """记录现状：只把 "§" 符号本身去掉，颜色代码的字母会留在 MOTD 里。"""
    s = S.summarize_server_info({"description": {"text": "§aHello", "extra": []}})
    assert s.desc_text == "aHello"


def test_summarize_server_info_extra_with_unusable_entries_keeps_text():
    """extra 里全是无法识别的类型时 parts 为空，回退到 text。"""
    s = S.summarize_server_info({"description": {"text": "Kept", "extra": [1, 2]}})
    assert s.desc_text == "Kept"


def test_summarize_server_info_plain_string_description():
    s = S.summarize_server_info({"description": "plain", "version": "1.19"})
    assert s.desc_text == "plain"
    assert s.version_name == "1.19"


def test_summarize_server_info_missing_fields():
    s = S.summarize_server_info({})
    assert s.desc_text == _("tool_peek_no_motd")
    assert s.version_name == "?"
    assert (s.online, s.max_players) == (0, 0)
    assert s.sample_text == ""


def test_summarize_server_info_non_dict_players():
    s = S.summarize_server_info({"description": "x", "players": "garbage"})
    assert (s.online, s.max_players) == (0, 0)


def test_summarize_server_info_truncates_long_motd():
    s = S.summarize_server_info({"description": "y" * 200})
    assert len(s.desc_text) == 63
    assert s.desc_text.endswith("...")


def test_summarize_server_info_motd_boundary_not_truncated():
    s = S.summarize_server_info({"description": "y" * 60})
    assert s.desc_text == "y" * 60


def test_summarize_server_info_caps_sample_list():
    info = {"players": {"sample": [{"name": f"p{i}"} for i in range(12)]}}
    s = S.summarize_server_info(info)
    assert s.sample_text.endswith(" ... +4")
    assert s.sample_text.count(" | ") == 7


def test_summarize_server_info_sample_without_names_is_empty():
    """sample 里全是无法识别的类型时，界面不会多渲染一行。"""
    s = S.summarize_server_info({"players": {"sample": [1, 2, 3]}})
    assert s.sample_text == ""


def test_decode_favicon_data_uri():
    import base64

    raw = b"\x89PNG\r\n\x1a\n"
    payload = "data:image/png;base64," + base64.b64encode(raw).decode()
    assert S.decode_favicon_data(payload) == raw


def test_decode_favicon_data_bare_base64():
    import base64

    assert S.decode_favicon_data(base64.b64encode(b"abc").decode()) == b"abc"


def test_decode_favicon_data_invalid():
    import binascii

    with pytest.raises(binascii.Error):
        S.decode_favicon_data("!!!not base64!!!")


# ═══════════════════════════════════════════════════════════════════
# 5. 坐标转换
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("text", "expected"),
    [("12", 12), (" -7 ", -7), ("+9", 9), ("0", 0), ("3.5", 0), ("abc", 0), ("", 0), ("  ", 0), ("1e3", 0)],
)
def test_parse_coordinate(text, expected):
    assert S.parse_coordinate(text) == expected


@pytest.mark.parametrize(
    ("x", "z", "rx", "rz"),
    [(80, 80, 10, 10), (-80, -80, -10, -10), (-1, -1, -1, -1), (7, 7, 0, 0), (0, 0, 0, 0)],
)
def test_convert_coordinates_to_nether_uses_floor_division(x, z, rx, rz):
    """记录现状：// 是向下取整，-1 // 8 == -1 而不是 0。"""
    r = S.convert_coordinates(x, 64, z, "nether")
    assert (r.rx, r.rz) == (rx, rz)
    assert r.y == 64


def test_convert_coordinates_to_overworld():
    r = S.convert_coordinates(10, 70, -10, "overworld")
    assert (r.rx, r.y, r.rz) == (80, 70, -80)


def test_convert_coordinates_unknown_target_falls_back_to_overworld():
    """记录现状：target 不是 "nether" 就走 else 分支。"""
    assert S.convert_coordinates(1, 2, 3, "whatever").rx == 8


def test_convert_coordinates_desc_is_localized():
    """说明文本随方向走不同的 i18n 键。

    断言方式对"语料是否已加载"都成立：未加载时 ``_()`` 逐键返回键名本身
    （单跑本文件的情况），加载后返回渲染好的文案（跑全套件的情况，
    别的用例会 ``init_i18n``）。用同一个 ``_()`` 现算期望值即可两边都覆盖。
    """
    nether = S.convert_coordinates(8, 64, 8, "nether").desc
    overworld = S.convert_coordinates(8, 64, 8, "overworld").desc
    assert nether == _("tool_coord_result_nether", x=8, y=64, z=8, rx=1, rz=1)
    assert overworld == _("tool_coord_result_overworld", x=8, y=64, z=8, rx=64, rz=64)
    assert nether != overworld


# ═══════════════════════════════════════════════════════════════════
# 6. Hash 计算器
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("label", ["MD5", "SHA1", "SHA256", "SHA512"])
def test_hash_file_matches_hashlib(tmp_path, label):
    data = b"FMCL" * 1000
    path = tmp_path / "sample.bin"
    path.write_bytes(data)
    expected = hashlib.new(S.HASH_ALGORITHMS[label], data).hexdigest()
    assert S.hash_file(str(path), label) == expected


def test_hash_file_empty_file(tmp_path):
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    assert S.hash_file(str(path), "SHA256") == hashlib.sha256(b"").hexdigest()


def test_hash_file_chunks_large_input(tmp_path):
    """超大输入：1.5 MB 跨多个 64KB 分块。"""
    data = bytes(range(256)) * 6144  # 1.5 MB
    path = tmp_path / "big.bin"
    path.write_bytes(data)
    assert S.hash_file(str(path), "SHA256") == hashlib.sha256(data).hexdigest()


def test_hash_file_missing_path(tmp_path):
    with pytest.raises(FileNotFoundError):
        S.hash_file(str(tmp_path / "nope.bin"), "SHA256")


def test_hash_file_directory_path(tmp_path):
    with pytest.raises(OSError):
        S.hash_file(str(tmp_path), "SHA256")


def test_hash_file_permission_failure(tmp_path, monkeypatch):
    path = tmp_path / "locked.bin"
    path.write_bytes(b"x")

    def _denied(*a, **k):
        raise PermissionError("拒绝访问")

    monkeypatch.setattr("builtins.open", _denied)
    with pytest.raises(PermissionError):
        S.hash_file(str(path), "SHA256")


def test_hash_file_unknown_algorithm_raises_key_error(tmp_path):
    """记录现状：算法键不存在时原样抛 KeyError（界面只有 4 个单选按钮，路径不可达）。"""
    path = tmp_path / "x.bin"
    path.write_bytes(b"x")
    with pytest.raises(KeyError):
        S.hash_file(str(path), "CRC32")


def test_hash_algorithms_table():
    assert S.HASH_ALGORITHMS == {"MD5": "md5", "SHA1": "sha1", "SHA256": "sha256", "SHA512": "sha512"}


# ═══════════════════════════════════════════════════════════════════
# 7. 每日运势
# ═══════════════════════════════════════════════════════════════════


def test_daily_fortune_is_deterministic():
    a = S.daily_fortune("2026-09-26")
    b = S.daily_fortune("2026-09-26")
    assert a == b
    assert a.today == "2026-09-26"


def test_daily_fortune_value_in_range_and_level_consistent():
    for day in range(1, 29):
        for month in range(1, 13):
            f = S.daily_fortune(f"2026-{month:02d}-{day:02d}")
            assert 0 <= f.value <= 100
            if f.value <= 20:
                assert f.level_key == "tool_fortune_terrible"
            elif f.value <= 40:
                assert f.level_key == "tool_fortune_bad"
            elif f.value <= 60:
                assert f.level_key == "tool_fortune_normal"
            elif f.value <= 80:
                assert f.level_key == "tool_fortune_good"
            elif f.value <= 95:
                assert f.level_key == "tool_fortune_great"
            else:
                assert f.level_key == "tool_fortune_legendary"
            assert f.emoji in {"💀", "😟", "😐", "😊", "🌟", "👑"}


def test_daily_fortune_covers_all_six_levels():
    seen = {S.daily_fortune(f"20{y:02d}-{m:02d}-{d:02d}").level_key for y in range(0, 100) for m in (1, 6, 12) for d in (1, 15, 28)}
    assert seen == {
        "tool_fortune_terrible",
        "tool_fortune_bad",
        "tool_fortune_normal",
        "tool_fortune_good",
        "tool_fortune_great",
        "tool_fortune_legendary",
    }


def test_daily_fortune_defaults_to_today():
    from datetime import date

    assert S.daily_fortune().today == date.today().isoformat()


# ═══════════════════════════════════════════════════════════════════
# 8. MC 冷知识
# ═══════════════════════════════════════════════════════════════════


def test_mc_facts_table():
    assert len(S.MC_FACTS) == 40
    assert all(isinstance(x, str) and x for x in S.MC_FACTS)
    assert len(set(S.MC_FACTS)) == len(S.MC_FACTS)


def test_fact_of_the_day_is_deterministic_and_in_range():
    for day in range(1, 29):
        idx = S.fact_of_the_day(f"2026-09-{day:02d}")
        assert 0 <= idx < len(S.MC_FACTS)
        assert idx == S.fact_of_the_day(f"2026-09-{day:02d}")


def test_fact_of_the_day_uses_injected_pool():
    pool = ["a", "b", "c"]
    seed = int(hashlib.sha256(b"fmcl_fact_2026-01-01").hexdigest(), 16)
    assert S.fact_of_the_day("2026-01-01", pool) == seed % 3
    assert S.fact_of_the_day("2026-01-01") == seed % len(S.MC_FACTS)


def test_fact_of_the_day_reaches_every_index():
    seen = {S.fact_of_the_day(f"20{y:02d}-{m:02d}-{d:02d}") for y in range(0, 100) for m in (1, 7) for d in (1, 10, 20)}
    assert seen == set(range(len(S.MC_FACTS)))


def test_fact_of_the_day_empty_pool_is_zerodivision():
    """记录现状：题库为空时取模抛 ZeroDivisionError（服务不做兜底）。"""
    with pytest.raises(ZeroDivisionError):
        S.fact_of_the_day("2026-01-01", [])


def test_random_fact_index_with_seeded_rng():
    rng = random.Random(42)
    idx = S.random_fact_index(rng=rng)
    assert 0 <= idx < len(S.MC_FACTS)
    assert idx == random.Random(42).randint(0, len(S.MC_FACTS) - 1)


def test_random_fact_index_single_item_pool():
    assert S.random_fact_index(["only"], rng=random.Random(1)) == 0


def test_random_fact_index_empty_pool_raises():
    """记录现状：空题库时 randint(0, -1) 抛 ValueError。"""
    with pytest.raises(ValueError):
        S.random_fact_index([], rng=random.Random(1))


# ═══════════════════════════════════════════════════════════════════
# 9. MC 知识问答
# ═══════════════════════════════════════════════════════════════════


def test_quiz_messages_shape():
    messages = S.quiz_messages()
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == S.QUIZ_SYSTEM_PROMPT
    assert messages[1]["content"] == S.QUIZ_USER_PROMPT
    assert "JSON" in S.QUIZ_SYSTEM_PROMPT


def test_quiz_messages_returns_fresh_list():
    a, b = S.quiz_messages(), S.quiz_messages()
    assert a == b and a is not b


def test_parse_quiz_response_extracts_array_from_prose():
    content = '好的，这是题目：\n[{"id": 1, "question": "q"}]\n希望有帮助'
    assert S.parse_quiz_response(content) == [{"id": 1, "question": "q"}]


def test_parse_quiz_response_takes_outermost_slice():
    """记录现状、疑为缺陷：取第一个 "[" 到最后一个 "]" 的切片。

    因此 AI 只要在 JSON 数组**之后**再写一个字面量 ``[`` 或 ``]``，
    切片就会跨到正文里，``json.loads`` 直接抛 ``JSONDecodeError``。
    """
    with pytest.raises(json.JSONDecodeError):
        S.parse_quiz_response('[{"a": 1}] 中间 [{"b": 2}]')
    assert S.parse_quiz_response('前言 [{"a": 1}] 结尾没有方括号') == [{"a": 1}]


@pytest.mark.parametrize("content", ["[]", "没有数组", "[", "]", "abc]def[ghi"])
def test_parse_quiz_response_invalid_shapes(content):
    with pytest.raises(InvalidArgument):
        S.parse_quiz_response(content)


def test_parse_quiz_response_non_list_json():
    with pytest.raises(InvalidArgument):
        S.parse_quiz_response('{"a": 1}')


def test_parse_quiz_response_malformed_json_propagates():
    """记录现状：切片不是合法 JSON 时 json.JSONDecodeError 原样抛出。"""
    with pytest.raises(json.JSONDecodeError):
        S.parse_quiz_response("[{不是 json}]")


def test_generate_quiz_passes_exact_messages():
    seen = {}

    def _chat(messages):
        seen["messages"] = messages
        return {"content": '[{"id": 1}]'}

    assert S.generate_quiz("token", chat=_chat) == [{"id": 1}]
    assert seen["messages"] == S.quiz_messages()


def test_generate_quiz_without_token():
    def _chat(messages):  # pragma: no cover - 不该被调用
        raise AssertionError("无 token 时不应调用 AI")

    with pytest.raises(InvalidArgument) as e:
        S.generate_quiz("", chat=_chat)
    assert str(e.value) == _("tool_quiz_not_logged_in")


def test_generate_quiz_empty_content():
    with pytest.raises(InvalidArgument) as e:
        S.generate_quiz("token", chat=lambda m: {"content": ""})
    assert str(e.value) == "AI 返回内容为空"


def test_generate_quiz_missing_content_key():
    with pytest.raises(InvalidArgument):
        S.generate_quiz("token", chat=lambda m: {})


def test_generate_quiz_propagates_transport_error():
    def _chat(messages):
        raise ConnectionError("网络不可达")

    with pytest.raises(ConnectionError):
        S.generate_quiz("token", chat=_chat)


def test_check_choice_answer():
    assert S.check_choice_answer(" A. 选项 ", "A. 选项") is True
    assert S.check_choice_answer("B. 选项", "A. 选项") is False


def test_check_choice_answer_is_case_sensitive():
    """记录现状、疑为缺陷：选择题判分大小写敏感，与填空题口径不一致。"""
    assert S.check_choice_answer("a. 选项", "A. 选项") is False


def test_check_text_answer_is_case_insensitive():
    """记录现状、疑为缺陷：填空题判分大小写不敏感，与选择题口径不一致。"""
    assert S.check_text_answer("a. 选项", "A. 选项") is True
    assert S.check_text_answer("  A. 选项  ", "A. 选项") is True
    assert S.check_text_answer("B. 选项", "A. 选项") is False


def test_load_quiz_questions_missing_file_returns_none(tmp_path):
    """文件不存在返回 None —— 调用方据此保持现有题目不动（与原实现一致）。"""
    assert S.load_quiz_questions(tmp_path / "nope.json") is None


def test_load_quiz_questions_roundtrip(tmp_path):
    path = tmp_path / "quiz.json"
    questions = [{"id": 1, "question": "中文题", "options": ["A", "B"], "answer": "A"}]
    S.save_quiz_questions(path, questions)
    assert S.load_quiz_questions(path) == questions


def test_save_quiz_questions_is_not_ascii_escaped(tmp_path):
    path = tmp_path / "quiz.json"
    S.save_quiz_questions(path, [{"q": "中文"}])
    assert "中文" in path.read_text(encoding="utf-8")


def test_save_quiz_questions_creates_parent_dirs(tmp_path):
    path = tmp_path / "a" / "b" / "quiz.json"
    S.save_quiz_questions(path, [])
    assert path.exists()


@pytest.mark.parametrize("content", ['{ 不是 json', '{"not": "a list"}', '"just a string"'])
def test_load_quiz_questions_bad_content_returns_empty(tmp_path, content):
    path = tmp_path / "quiz.json"
    path.write_text(content, encoding="utf-8")
    assert S.load_quiz_questions(path) == []


def test_save_quiz_questions_swallows_write_errors(tmp_path, monkeypatch):
    """记录现状、疑为缺陷：写文件失败只记日志、静默返回，调用方看不出失败。"""
    path = tmp_path / "quiz.json"

    def _denied(*a, **k):
        raise PermissionError("磁盘只读")

    monkeypatch.setattr("builtins.open", _denied)
    S.save_quiz_questions(path, [{"id": 1}])  # 不抛异常
    assert not path.exists()


def test_save_quiz_questions_mkdir_error_propagates(tmp_path, monkeypatch):
    """记录现状、疑为缺陷：mkdir 在 try 之外，建目录失败会抛出去（与写失败不同）。"""

    def _denied(*a, **k):
        raise PermissionError("拒绝访问")

    monkeypatch.setattr(S.Path, "mkdir", _denied)
    with pytest.raises(PermissionError):
        S.save_quiz_questions(tmp_path / "sub" / "quiz.json", [])


def test_reindex_questions():
    questions = [{"a": 1}, {"a": 2}, {"a": 3}]
    out = S.reindex_questions(questions)
    assert [q["id"] for q in out] == [1, 2, 3]
    assert out is not questions
    assert all(out[i] is questions[i] for i in range(3))  # 就地改的原对象
    assert [q["id"] for q in S.reindex_questions([{"a": 1}], start=7)] == [7]


def test_reindex_questions_empty():
    assert S.reindex_questions([]) == []


def test_merge_generated_questions_uses_max_int_id():
    existing = [{"id": 3}, {"id": "x"}, {"id": 7}, {"noid": 1}]
    new = [{"a": 1}, {"a": 2}]
    merged = S.merge_generated_questions(existing, new)
    assert [q["id"] for q in merged] == [8, 9]
    assert [q.get("id") for q in existing] == [3, "x", 7, None]  # 不改动既有题库
    assert merged[0] is new[0]


def test_merge_generated_questions_empty_existing():
    merged = S.merge_generated_questions([], [{"a": 1}])
    assert [q["id"] for q in merged] == [1]


def test_merge_generated_questions_bool_id_counts_as_int():
    """记录现状：bool 是 int 的子类，所以 True 会被当成 id=1。"""
    merged = S.merge_generated_questions([{"id": True}], [{"a": 1}])
    assert [q["id"] for q in merged] == [2]


def test_drop_question():
    questions = [{"id": 1}, {"id": 2}, {"id": 3}]
    S.drop_question(questions, 1)
    assert [q["id"] for q in questions] == [1, 3]


@pytest.mark.parametrize("index", [2, 5, 99])
def test_drop_question_out_of_range_is_noop(index):
    questions = [{"id": 1}, {"id": 2}]
    S.drop_question(questions, index)
    assert [q["id"] for q in questions] == [1, 2]


def test_drop_question_negative_index_pops_from_end():
    """记录现状：`index < len(...)` 对负数成立，所以 pop(-1) 会删掉最后一题。"""
    questions = [{"id": 1}, {"id": 2}]
    S.drop_question(questions, -1)
    assert [q["id"] for q in questions] == [1]


def test_drop_question_empty_list():
    questions: list = []
    S.drop_question(questions, 0)
    assert questions == []


# ═══════════════════════════════════════════════════════════════════
# 10. 多线程下载器（全部走假 HTTP 会话，不联网）
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://a/b/c.bin", "c.bin"),
        ("http://a/b/c.bin?token=1", "c.bin"),
        # 记录现状：只按 "?" 切，URL fragment 不会被剥掉（真实下载里很少见）
        ("http://a/b/c.bin#frag", "c.bin#frag"),
        ("http://a/", "downloaded_file"),
        ("", "downloaded_file"),
        ("http://a/b/", "downloaded_file"),
    ],
)
def test_filename_from_url(url, expected):
    assert S.filename_from_url(url) == expected


def test_resolve_download_target_empty_save_path(tmp_path):
    assert S.resolve_download_target("http://a/b/c.bin", "", str(tmp_path)) == str(tmp_path / "c.bin")


def test_resolve_download_target_directory(tmp_path):
    assert S.resolve_download_target("http://a/b/c.bin", str(tmp_path), str(tmp_path)) == str(tmp_path / "c.bin")


def test_resolve_download_target_explicit_file(tmp_path):
    target = str(tmp_path / "renamed.bin")
    assert S.resolve_download_target("http://a/b/c.bin", target, str(tmp_path)) == target


def test_resolve_download_target_nonexistent_path_treated_as_file(tmp_path):
    """记录现状：save_path 不是目录就当成文件名（哪怕父目录不存在）。"""
    target = str(tmp_path / "nope" / "x.bin")
    assert S.resolve_download_target("http://a/b/c.bin", target, str(tmp_path)) == target


def test_ensure_directory_creates_nested(tmp_path):
    target = tmp_path / "a" / "b"
    S.ensure_directory(str(target))
    assert target.is_dir()
    S.ensure_directory(str(target))  # 已存在时幂等


def test_ensure_directory_error_propagates(tmp_path, monkeypatch):
    def _denied(*a, **k):
        raise PermissionError("拒绝访问")

    monkeypatch.setattr(S.os, "makedirs", _denied)
    with pytest.raises(PermissionError):
        S.ensure_directory(str(tmp_path / "a"))


def test_download_multi_segmented(tmp_path):
    body = bytes(range(256)) * 16  # 4096 B
    session = FakeSession(body)
    events = []
    dest = tmp_path / "out.bin"
    outcome = S.download_multi(
        "http://example.test/file.bin",
        str(dest),
        threads=4,
        session=session,
        on_progress=lambda d, t, s: events.append((d, t, s)),
    )
    assert outcome.status == "success"
    assert outcome.path == str(dest)
    assert outcome.size == len(body)
    assert dest.read_bytes() == body
    assert not list(tmp_path.glob("*.part*"))
    assert events and events[-1][0] == len(body)
    assert all(t == len(body) for _d, t, _s in events)
    assert session.head_calls == [("http://example.test/file.bin", {"User-Agent": "FMCL/2.0"}, 15)]
    ranges = [h.get("Range") for _u, h, _t in session.get_calls]
    assert ranges == ["bytes=0-1023", "bytes=1024-2047", "bytes=2048-3071", "bytes=3072-4095"]
    assert all(t == 60 for _u, _h, t in session.get_calls)


def test_download_multi_single_stream_fallback(tmp_path):
    body = b"no content-length here" * 10
    session = FakeSession(body, announce_length=False)
    dest = tmp_path / "out.bin"
    outcome = S.download_multi("http://example.test/file.bin", str(dest), session=session)
    assert outcome.status == "success"
    assert dest.read_bytes() == body
    assert outcome.size == len(body)
    # 单流回退用 timeout=30，且不带 Range
    assert [(h.get("Range"), t) for _u, h, t in session.get_calls] == [(None, 30)]


def test_download_multi_progress_hook_is_optional(tmp_path):
    body = b"x" * 128
    session = FakeSession(body)
    outcome = S.download_multi("http://example.test/f", str(tmp_path / "o.bin"), session=session)
    assert outcome.status == "success"


def test_download_multi_reports_progress_even_without_a_clock_tick(tmp_path, monkeypatch):
    """D-154：**一个时钟刻度都没跨过**的下载也必须报进度。

    这条是该缺陷的钉子。原实现把 ``emit`` 写在 ``if elapsed > 0:`` 里面 —— 那层本意只是
    给"算速率"的除法防零，却把进度回调也一起挡在了里面：整个下载若在**同一个时钟刻度内**
    跑完（``elapsed == 0.0``），就一次回调都不发，界面进度条全程不动而文件其实已经下好。
    Windows 上 ``time.time()`` 的步长约 0.5 ms，内存/局域网的小文件很容易撞上
    （实证：自然时钟下 5000 B 负载 5/120 次、3 B 负载 3/120 次；`poc/_probe_tool_progress_tick.py`）。

    判据故意用**冻结时钟**，而不是"多跑几次碰运气"：``time.time()`` 恒返回同一个值
    ⇒ ``elapsed == 0.0`` 必然成立 ⇒ 这是确定性用例，不会变成偶发红灯
    （同一条机制此前也让 `test_download_multi_segmented` 有约 0.5% 的假红概率）。
    """
    body = bytes(range(256)) * 16  # 4096 B，与上面分段用例同量级
    events = []

    class _FrozenTime:
        """``time.time()`` 恒返回同一个值 = 整个下载没跨过任何一个时钟刻度。"""

        def time(self):
            return 1000.0

        def __getattr__(self, name):  # 其余接口（sleep / perf_counter 之类）保持真的
            return getattr(time, name)

    monkeypatch.setattr(S, "time", _FrozenTime())
    dest = tmp_path / "frozen.bin"
    S.download_multi(
        "http://example.test/file.bin",
        str(dest),
        threads=4,
        session=FakeSession(body),
        on_progress=lambda d, t, s: events.append((d, t, s)),
    )

    assert dest.read_bytes() == body
    assert events, "亚时钟刻度的下载一次进度回调都没发（D-154 回归：emit 又被 `if elapsed > 0` 挡住了）"
    assert events[-1][0] == len(body), f"最后一条进度的已完成字节不是总大小: {events[-1]}"
    assert all(t == len(body) for _d, t, _s in events), f"总大小报错了: {events}"
    assert all(s == 0.0 for _d, _t, s in events), (
        f"没跨过时钟刻度时速率应当给 0.0（不要拿 0 去除）: {events}"
    )


def test_download_multi_smaller_than_thread_count(tmp_path):
    """记录现状：total_size < threads 时 part_size 为 0，会出现 `bytes=0--1` 段，但结果仍正确。"""
    body = b"abc"
    session = FakeSession(body)
    dest = tmp_path / "o.bin"
    outcome = S.download_multi("http://example.test/f", str(dest), threads=8, session=session)
    assert outcome.status == "success"
    assert dest.read_bytes() == body
    assert not list(tmp_path.glob("*.part*"))


def test_download_multi_large_payload(tmp_path):
    """超大输入：2 MB。"""
    body = bytes(random.Random(7).randrange(256) for _ in range(2 * 1024 * 1024))
    session = FakeSession(body)
    dest = tmp_path / "big.bin"
    outcome = S.download_multi("http://example.test/big", str(dest), threads=4, session=session)
    assert outcome.status == "success"
    assert dest.read_bytes() == body
    assert not list(tmp_path.glob("*.part*"))


def test_download_multi_cancel_removes_parts(tmp_path):
    body = b"x" * 100
    session = FakeSession(body)
    dest = tmp_path / "o.bin"
    outcome = S.download_multi(
        "http://example.test/f",
        str(dest),
        session=session,
        cancel_check=lambda: True,
    )
    assert outcome.status == "cancelled"
    assert not dest.exists()
    assert not list(tmp_path.glob("*.part*"))


def test_download_multi_http_error_propagates(tmp_path):
    body = b"x" * 10
    session = FakeSession(body, status=500)
    dest = tmp_path / "o.bin"
    with pytest.raises(RuntimeError):
        S.download_multi("http://example.test/f", str(dest), session=session)
    assert not dest.exists()


def test_download_multi_head_failure_propagates(tmp_path):
    session = FakeSession(b"", fail_on_head=True)
    with pytest.raises(RuntimeError):
        S.download_multi("http://example.test/f", str(tmp_path / "o.bin"), session=session)


def test_download_multi_user_agent_is_injectable(tmp_path):
    body = b"y" * 64
    session = FakeSession(body)
    S.download_multi("http://example.test/f", str(tmp_path / "o.bin"), user_agent="UA/9", session=session)
    assert all(h.get("User-Agent") == "UA/9" for _u, h, _t in session.get_calls)
    assert session.head_calls[0][1]["User-Agent"] == "UA/9"


# ═══════════════════════════════════════════════════════════════════
# 11. 与界面层的接线（界面只留薄委托）
# ═══════════════════════════════════════════════════════════════════


def test_ui_mixin_delegates_to_service_without_app_context():
    """阶段 1 的 Tk 界面还没有 AppContext，Mixin 必须能自己造一个服务并缓存。"""
    from ui.app_tools import ToolsTabMixin

    host = ToolsTabMixin()
    svc = host._tool_service()
    assert isinstance(svc, S.ToolService)
    assert host._tool_service() is svc  # 缓存，不是每次新建


def test_ui_mixin_prefers_service_from_context():
    from ui.app_tools import ToolsTabMixin

    sentinel = S.ToolService()

    class Ctx:
        def try_get(self, name):
            return sentinel if name == "tool" else None

    class Host(ToolsTabMixin):
        pass

    host = Host()
    host.context = Ctx()
    assert host._tool_service() is sentinel


def test_ui_mixin_falls_back_when_context_lookup_fails():
    from ui.app_tools import ToolsTabMixin

    class Ctx:
        def try_get(self, name):
            raise RuntimeError("上下文还没起来")

    host = ToolsTabMixin()
    host.context = Ctx()
    assert isinstance(host._tool_service(), S.ToolService)


def test_ui_shares_the_same_objects_with_service():
    """题库 / 提示词 / 工具函数在两侧必须是**同一个对象**，不是副本。"""
    import ui.app_tools as ui_tools

    assert ui_tools.ToolsTabMixin._MC_FACTS is S.MC_FACTS
    assert ui_tools.ToolsTabMixin._QUIZ_SYSTEM_PROMPT is S.QUIZ_SYSTEM_PROMPT
    assert ui_tools._format_size is S._format_size
    assert ui_tools._mc_write_varint is S._mc_write_varint
    assert ui_tools._mc_read_varint is S._mc_read_varint


def test_ui_keeps_all_public_method_names():
    """搬运不得改变界面对外可见的方法集合（只新增 _tool_service）。"""
    import ui.app_tools as ui_tools

    expected = {
        "_build_tools_tab_content",
        "_make_tool_card",
        "_build_tool_clean_junk",
        "_on_clean_junk",
        "_on_delete_selected_junk",
        "_is_protected_system_dir",
        "_build_tool_daily_fortune",
        "_on_check_fortune",
        "_build_tool_coordinate_converter",
        "_on_convert_coord",
        "_parse_coord",
        "_build_tool_hash_calculator",
        "_on_calc_hash",
        "_build_tool_port_checker",
        "_on_test_port",
        "_on_peek_server",
        "_display_server_info",
        "_build_tool_minecraft_facts",
        "_on_new_fact",
        "_on_random_fact",
        "_build_tool_minecraft_quiz",
        "_on_quiz_generate",
        "_on_quiz_next",
        "_on_quiz_select",
        "_on_quiz_submit_text",
        "_quiz_call_ai_generate",
        "_build_tool_multi_download",
        "_on_start_download",
        "_get_minecraft_dir_for_tools",
        "_get_download_threads_for_tools",
        "_tool_service",
    }
    assert expected <= set(dir(ui_tools.ToolsTabMixin))


def test_ui_no_longer_contains_the_moved_algorithms():
    """算法不能在界面文件里留副本（否则下次修复只改一边）。"""
    src = (REPO_ROOT / "ui" / "app_tools.py").read_text(encoding="utf-8")
    for token in (
        "os.walk(",
        "hashlib.new(",
        "randint(",
        "threading.Thread(target=_dl_part",
        "base64.b64decode(",
        "socket.socket(",
        "sha256(",
    ):
        assert token not in src, f"界面文件里仍有算法副本：{token}"
    # 但服务里必须都有
    svc = (REPO_ROOT / "services" / "tool_service.py").read_text(encoding="utf-8")
    for token in (
        "os.walk(",
        "hashlib.new(",
        "randint(",
        "threading.Thread(target=_dl_part",
        "base64.b64decode(",
        "socket.socket(",
        "sha256(",
    ):
        assert token in svc, f"服务里缺少算法：{token}"

