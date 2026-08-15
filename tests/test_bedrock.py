"""基岩版模块单元测试（全部离线，不访问网络）"""

import io
import json
import struct
import threading
import zipfile
from pathlib import Path

import pytest

from launcher.bedrock import appx, launch, source
from launcher.bedrock.download import check_md5, download_file


# ─── source.py：版本库与链接解析 ──────────────────────────────

SAMPLE_DB = {
    "CreationTime": "2026-08-08T19:38:51",
    "From_mcappx.com": {
        "1.21.120.20": {
            "Type": "Preview",
            "BuildType": "UWP",
            "ID": "1.21.12020",
            "Date": "2025-09-03",
            "Variations": [
                {
                    "Arch": "x64",
                    "OSbuild": "19041",
                    "MetaData": ["c1872dc3-ddf8-4a91-844b-8dca1b681324"],
                    "MD5": "f7f59aabf5cc8c4e0d88ef2f95fe1df5",
                }
            ],
        },
        "1.21.120.21": {
            "Type": "Preview",
            "BuildType": "GDK",
            "ID": "1.21.12021",
            "Date": "2025-09-09",
            "Variations": [
                {
                    "Arch": "x64",
                    "OSbuild": "18362",
                    "MetaData": ["http://assets1.xboxlive.cn/12/abc.msixvc"],
                    "MD5": "36b590a36446b1cfc677bd1fbfbed04d",
                }
            ],
        },
    },
}


def test_get_build_info_and_variation():
    info = source.get_build_info(SAMPLE_DB, "1.21.120.21")
    assert info is not None
    assert info["BuildType"] == "GDK"
    variation = source.find_variation(info, "x64")
    assert variation is not None
    assert variation["MD5"] == "36b590a36446b1cfc677bd1fbfbed04d"
    assert source.find_variation(info, "arm") is None


def test_resolve_gdk_url():
    info = source.get_build_info(SAMPLE_DB, "1.21.120.21")
    url = source.resolve_download_url(info, "x64")
    assert url.startswith("http://assets1.xboxlive.cn/")


def test_build_mirror_urls():
    urls = source.build_mirror_urls("http://assets1.xboxlive.cn/12/abc.msixvc")
    assert urls[0] == "http://assets1.xboxlive.cn/12/abc.msixvc"
    # 原链接即首个镜像主机，去重后为全部主机数
    assert len(urls) == len(source.GDK_MIRROR_HOSTS)
    assert len(set(urls)) == len(urls)
    assert any("assets2.xboxlive.com" in u for u in urls)
    assert all(u.endswith("/12/abc.msixvc") for u in urls)


def test_soap_parse_complex_url():
    resp = (
        "<s:Envelope><s:Body>"
        '<GetExtendedUpdateInfo2Response xmlns="http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService">'
        "<FileUrl><Url>https://tlu.dl.delivery.mp.microsoft.com/filestreamingservice/files/abc?P1=123&amp;P2=456</Url>"
        "<Url>https://tlu.dl.delivery.mp.microsoft.com/simple.msixvc</Url>"
        "</FileUrl>"
        "</GetExtendedUpdateInfo2Response></s:Body></s:Envelope>"
    )
    url = source._parse_soap_download_url(resp)
    assert url is not None
    assert "?P1=123&P2=456" in url


def test_soap_parse_last_fallback():
    resp = (
        "<s:Envelope><s:Body>"
        '<GetExtendedUpdateInfo2Response xmlns="http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService">'
        "<FileUrl><Url>https://simple.example.com/a.msixvc</Url><Url>https://fallback.example.com/b.msixvc</Url>"
        "</FileUrl>"
        "</GetExtendedUpdateInfo2Response></s:Body></s:Envelope>"
    )
    assert source._parse_soap_download_url(resp) == "https://fallback.example.com/b.msixvc"


def test_version_db_cache(tmp_path):
    db_file = tmp_path / source.DB_CACHE_FILE
    db_file.write_text(json.dumps(SAMPLE_DB), encoding="utf-8")
    data = source.load_version_db(tmp_path)
    assert "1.21.120.20" in data["From_mcappx.com"]


def test_fetch_version_db_fallback(monkeypatch):
    import requests

    calls = {"count": 0}

    def fake_get(url, timeout=30, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise requests.ConnectionError("offline")
        resp = requests.Response()
        resp.status_code = 200
        resp._content = json.dumps(SAMPLE_DB).encode("utf-8")
        return resp

    monkeypatch.setattr(source, "_get", fake_get)
    data = source.fetch_version_db()
    assert calls["count"] == 2
    assert "From_mcappx.com" in data


# ─── download.py：多线程下载与 MD5 ────────────────────────────


class _RangeServer:
    """本地 Range 请求支持的文件服务器（单线程、阻塞式）"""

    def __init__(self, data: bytes):
        import http.server
        import threading

        self.data = data
        self._server = None

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_HEAD(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()

            def do_GET(self):
                range_header = self.headers.get("Range")
                if range_header and range_header.startswith("bytes="):
                    start_s, _, end_s = range_header[6:].partition("-")
                    start = int(start_s)
                    end = int(end_s) if end_s else len(data) - 1
                    chunk = data[start : end + 1]
                    self.send_response(206)
                    self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
                    self.send_header("Content-Length", str(len(chunk)))
                    self.end_headers()
                    self.wfile.write(chunk)
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server = server
        self.port = server.server_address[1]
        self.thread = threading.Thread(target=server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/test.bin"

    def close(self):
        self._server.shutdown()
        self._server.server_close()


def test_download_file_multithread(tmp_path):
    import hashlib

    payload = bytes(range(256)) * 5000  # 1.28 MB
    server = _RangeServer(payload)
    try:
        dest = tmp_path / "pkg.insPack"
        progress_log = []
        download_file([server.url], dest, progress_cb=lambda cur, total: progress_log.append((cur, total)), threads=4)
        assert dest.read_bytes() == payload
        assert progress_log[-1] == (len(payload), len(payload))
        md5 = hashlib.md5(payload).hexdigest()
        assert check_md5(dest, md5)
        assert not check_md5(dest, "00000000000000000000000000000000")
    finally:
        server.close()


def test_download_mirror_fallback(tmp_path):
    payload = b"fallback-test-data" * 100
    server = _RangeServer(payload)
    try:
        dest = tmp_path / "pkg2.insPack"
        download_file(["http://127.0.0.1:1/nope.bin", server.url], dest, threads=2)
        assert dest.read_bytes() == payload
    finally:
        server.close()


# ─── dotnet.py：.NET 10 SDK 检测与下载直链 ─────────────────

class _FakeSubprocessResult:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.stderr = ""


def _patch_dotnet_env(monkeypatch, stdout: str = ""):
    """把 dotnet 检测环境替换为可控假实现"""
    from launcher.bedrock import dotnet

    monkeypatch.setattr(dotnet.shutil, "which", lambda _name: "C:\\dotnet\\dotnet.exe")
    monkeypatch.setattr(dotnet.subprocess, "run", lambda *_a, **_k: _FakeSubprocessResult(stdout))
    return dotnet


def test_dotnet_has_sdk10(monkeypatch):
    """SDK 列表含 10.x 行时返回 True"""
    dotnet = _patch_dotnet_env(monkeypatch, "10.0.102 [C:\\Program Files\\dotnet\\sdk]\n")
    assert dotnet.has_sdk10() is True


def test_dotnet_has_sdk10_installed(monkeypatch):
    dotnet = _patch_dotnet_env(monkeypatch, "10.0.102 [C:\\Program Files\\dotnet\\sdk]\n9.0.100 [C:\\Program Files\\dotnet\\sdk]\n")
    assert dotnet.has_sdk10() is True


def test_dotnet_has_sdk10_missing(monkeypatch):
    """只有旧版本 SDK 时返回 False"""
    dotnet = _patch_dotnet_env(monkeypatch, "9.0.100 [C:\\Program Files\\dotnet\\sdk]\n")
    assert dotnet.has_sdk10() is False


def test_dotnet_has_sdk10_no_dotnet(monkeypatch):
    """系统没有 dotnet 时返回 False"""
    from launcher.bedrock import dotnet

    monkeypatch.setattr(dotnet.shutil, "which", lambda _name: None)
    assert dotnet.has_sdk10() is False


def test_dotnet_detect_arch(monkeypatch):
    from launcher.bedrock import dotnet

    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")
    assert dotnet.detect_arch() == "x64"
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "ARM64")
    assert dotnet.detect_arch() == "arm64"
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "X86")
    assert dotnet.detect_arch() == "x86"
    monkeypatch.delenv("PROCESSOR_ARCHITECTURE")
    assert dotnet.detect_arch() in ("x64", "arm64", "x86")


def test_dotnet_sdk_download_url(monkeypatch):
    """直链按架构与最新版本拼接（官方 builds CDN）"""
    from launcher.bedrock import dotnet

    class FakeResp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"latest-sdk": "10.0.102"}

    monkeypatch.setattr(dotnet.requests, "get", lambda *a, **k: FakeResp())
    url = dotnet.sdk_download_url(arch="x64")
    assert url == "https://builds.dotnet.microsoft.com/dotnet/Sdk/10.0.102/dotnet-sdk-10.0.102-win-x64.exe"
    assert "arm64" in dotnet.sdk_download_url(arch="arm64")


def test_dotnet_sdk_download_url_error(monkeypatch):
    """网络失败时抛出明确错误"""
    from launcher.bedrock import dotnet

    def boom(*_a, **_k):
        raise dotnet.requests.RequestException("network down")

    monkeypatch.setattr(dotnet.requests, "get", boom)
    with pytest.raises(dotnet.DotnetError, match="下载链接"):
        dotnet.sdk_download_url()


# ─── extractor.py：GDK 解压（.NET 委托） ───────────────────

class _FakeExtractorProc:
    """模拟 BedrockXvdExtractor 进程：固定输出行 + 退出码"""

    def __init__(self, lines, returncode=0):
        self.stdout = iter(lines)
        self.returncode = returncode
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def _patch_extractor(monkeypatch, proc: _FakeExtractorProc, exe: Path):
    """替换 extractor 的构建与进程启动"""
    from launcher.bedrock import extractor

    monkeypatch.setattr(extractor.build_helper, "build_extractor", lambda force=False: exe)
    monkeypatch.setattr(extractor.subprocess, "Popen", lambda *_a, **_k: proc)
    return extractor


def test_extractor_progress_and_count(tmp_path, monkeypatch):
    """进度行解析与 COUNT 汇总、参数传递（game_type）"""
    exe = tmp_path / "BedrockXvdExtractor.exe"
    exe.write_bytes(b"MZ")
    proc = _FakeExtractorProc(
        ["PROGRESS:1/3:file1.bin", "PROGRESS:2/3:dir/file2.bin", "COUNT:3"],
        returncode=0,
    )
    extractor = _patch_extractor(monkeypatch, proc, exe)
    progress: list = []

    count = extractor.extract_gdk_package(
        tmp_path / "pkg.insPack",
        tmp_path / "out",
        progress_cb=lambda cur, total, fname: progress.append((cur, total, fname)),
        game_type="preview",
    )
    assert count == 3
    assert progress == [(1, 3, "file1.bin"), (2, 3, "dir/file2.bin")]


def test_extractor_failure(tmp_path, monkeypatch):
    """非零退出码时报出 stderr 错误内容"""
    exe = tmp_path / "BedrockXvdExtractor.exe"
    exe.write_bytes(b"MZ")
    proc = _FakeExtractorProc(["ERROR: 解密失败，包损坏"], returncode=1)
    extractor = _patch_extractor(monkeypatch, proc, exe)
    with pytest.raises(extractor.ExtractorError, match="解密失败"):
        extractor.extract_gdk_package(tmp_path / "pkg", tmp_path / "out")


def test_extractor_cancel(tmp_path, monkeypatch):
    """stop_event 置位时终止进程并报错"""
    import threading

    exe = tmp_path / "BedrockXvdExtractor.exe"
    exe.write_bytes(b"MZ")
    proc = _FakeExtractorProc(["PROGRESS:1/3:a.bin", "PROGRESS:2/3:b.bin", "PROGRESS:3/3:c.bin"])
    extractor = _patch_extractor(monkeypatch, proc, exe)
    stop = threading.Event()
    stop.set()
    with pytest.raises(extractor.ExtractorError, match="取消"):
        extractor.extract_gdk_package(tmp_path / "pkg", tmp_path / "out", stop_event=stop)
    assert proc.terminated or proc.killed


def test_extractor_build_failure(tmp_path, monkeypatch):
    """构建辅助程序失败时包装为 ExtractorError"""
    from launcher.bedrock import extractor

    def boom(_force=False):
        raise RuntimeError("需要 .NET 10 SDK")

    monkeypatch.setattr(extractor.build_helper, "build_extractor", boom)
    with pytest.raises(extractor.ExtractorError, match=".NET 10 SDK"):
        extractor.extract_gdk_package(tmp_path / "pkg", tmp_path / "out")


def test_build_helper_runtime_root_source(monkeypatch):
    """源码模式：构建产物输出到 native 目录"""
    from launcher.bedrock.native import build_helper

    monkeypatch.setattr(build_helper.sys, "frozen", False, raising=False)
    assert build_helper._runtime_root() == build_helper.NATIVE_DIR


def test_build_helper_runtime_root_frozen(monkeypatch, tmp_path):
    """打包模式：构建产物输出到数据目录/local（_MEIPASS 是临时目录，不能落盘）"""
    from launcher.bedrock.native import build_helper

    monkeypatch.setattr(build_helper.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert build_helper._runtime_root() == tmp_path / "FMCL" / "local"
    assert build_helper._extractor_exe() == tmp_path / "FMCL" / "local" / "native-bin" / "BedrockXvdExtractor" / "BedrockXvdExtractor.exe"


def test_components_assets_dir_frozen(monkeypatch, tmp_path):
    """打包模式：认证组件落盘到数据目录/local/bedrock-components"""
    from launcher.bedrock import components

    monkeypatch.setattr(components.sys, "frozen", True, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert components._assets_dir() == tmp_path / "FMCL" / "local" / "bedrock-components"


# ─── components.py：闭源认证组件按需下载 ──────────────────

def _fake_nupkg(dll_data: bytes = b"MZfake-dll" * 16) -> bytes:
    """构造内存中的假 nupkg（zip 格式，lib 目录下含目标 DLL）"""
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("lib/net8.0/XUserLauncher.Core.dll", dll_data)
        zf.writestr("lib/net8.0/XUserLauncher.Core.pdb", b"pdb")
    return buf.getvalue()


def test_components_is_ready_missing(tmp_path, monkeypatch):
    """组件缺失时 is_ready 为 False"""
    from launcher.bedrock import components

    monkeypatch.setattr(components, "ASSETS_DIR", tmp_path)
    assert not components.is_ready()
    (tmp_path / components.TARGET_FILENAME).write_bytes(b"MZ" + b"\x00" * 512)
    assert components.is_ready()


def test_components_latest_version(monkeypatch):
    """版本列表解析：取最新稳定版（跳过带 -/+ 的预发布版本）"""
    from launcher.bedrock import components

    monkeypatch.setattr(
        components, "_get",
        lambda url: b'{"versions": ["1.0.0.1", "1.0.0.2-beta", "1.0.0.3"]}',
    )
    assert components._latest_version() == "1.0.0.3"


def test_components_latest_version_no_stable(monkeypatch):
    """无稳定版本时明确报错"""
    from launcher.bedrock import components

    monkeypatch.setattr(components, "_get", lambda url: b'{"versions": ["1.0.0.3-beta"]}')
    with pytest.raises(components.ComponentError, match="稳定版本"):
        components._latest_version()


def test_components_extract_dll():
    """nupkg 提取：取 lib 下 TFM 等级最高的 DLL，校验 PE 头"""
    from launcher.bedrock import components

    dll = b"MZ\x90\x00" * 256
    nupkg = _fake_nupkg(dll)
    assert components._extract_dll(nupkg) == dll


def test_components_extract_dll_missing_entry():
    """包内无 lib/XUserLauncher.Core.dll 时报错"""
    from launcher.bedrock import components

    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("lib/net8.0/Other.dll", b"MZxxxx")
    with pytest.raises(components.ComponentError, match="未找到"):
        components._extract_dll(buf.getvalue())


def test_components_extract_dll_bad_pe():
    """提取结果不是 PE 文件时报错"""
    from launcher.bedrock import components

    with pytest.raises(components.ComponentError, match="PE"):
        components._extract_dll(_fake_nupkg(b"not a dll content at all"))


def test_components_download_flow(tmp_path, monkeypatch):
    """完整下载流程：查询版本 → 下载 nupkg → 提取落盘（原子写入）"""
    from launcher.bedrock import components

    dll_data = b"MZreal-component" + b"\x00" * 256
    nupkg = _fake_nupkg(dll_data)

    def fake_get(url: str) -> bytes:
        if url.endswith("index.json"):
            return b'{"versions": ["1.0.0.3"]}'
        if url.endswith("1.0.0.3.nupkg"):
            return nupkg
        raise AssertionError(f"未预期的 URL: {url}")

    monkeypatch.setattr(components, "_get", fake_get)
    monkeypatch.setattr(components, "ASSETS_DIR", tmp_path)
    target = components.download()
    assert target.read_bytes() == dll_data
    # 已就绪后重复调用直接返回，不再请求网络
    monkeypatch.setattr(
        components, "_get",
        lambda url: (_ for _ in ()).throw(AssertionError("不应再次请求网络")),
    )
    assert components.download() == target


def test_components_download_network_error(monkeypatch, tmp_path):
    """网络失败时抛出明确错误"""
    from launcher.bedrock import components

    def boom(url: str) -> bytes:
        raise components.ComponentError("请求失败 [url]: 网络不可用")

    monkeypatch.setattr(components, "_get", boom)
    monkeypatch.setattr(components, "ASSETS_DIR", tmp_path)
    with pytest.raises(components.ComponentError, match="网络不可用"):
        components.download()


def test_is_valid_msi(tmp_path):
    """MSI 有效性校验：OLE 复合文档头判定（mcappx 包错配文本应判无效）"""
    from launcher.bedrock.env import is_valid_msi

    msi = tmp_path / "GameInputRedist.msi"
    msi.write_bytes(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1" + b"\x00" * 64)
    assert is_valid_msi(msi)
    msi.write_bytes(b"Realms Plus \xe6\x98\xaf\xe8\xae\xa2\xe9\x98\x85\xe6\x9c\x8d\xe5\x8a\xa1")
    assert not is_valid_msi(msi)


def test_is_xbox_signed_in(tmp_path, monkeypatch):
    """Xbox 登录检测：IdentityProvider 缓存/注册表条目；崩溃日志不误判"""
    from launcher.bedrock import env as env_mod

    fake_local = tmp_path / "LocalAppData"
    fake_local.mkdir()
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    # 隔离真实系统的 IdentityCRL KeyCache（避免本机已登录的凭据干扰测试）
    test_keycache = r"Software\IdentityCRL\KeyCache\__fmcl_test__"
    monkeypatch.setattr(env_mod, "IDENTITY_CRL_KEYCACHE", test_keycache)
    assert not env_mod.is_xbox_signed_in()  # 无任何身份

    # IdentityProvider 空目录不算登录
    idp = fake_local / "Packages" / env_mod.XBOX_IDP_PACKAGE / "LocalState"
    idp.mkdir(parents=True)
    assert not env_mod.is_xbox_signed_in()
    # 有数据才算
    (idp / "auth.bin").write_bytes(b"xbox-token")
    assert env_mod.is_xbox_signed_in()
    idp.unlink() if False else None
    import shutil

    shutil.rmtree(idp)
    # 注册表条目（模拟 IdentityCRL TokenCache 含 xbox 条目）
    import winreg

    test_key = r"Software\IdentityCRL\TokenCache"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, test_key) as key:
        winreg.CreateKey(key, "xboxlive.com")
    monkeypatch.setattr(env_mod, "IDENTITY_CRL_PATH", test_key)
    try:
        assert env_mod.is_xbox_signed_in()
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, test_key + r"\xboxlive.com")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, test_key)
        except OSError:
            pass
    monkeypatch.delenv("LOCALAPPDATA")
    assert not env_mod.is_xbox_signed_in()


def test_msa_device_code_flow(monkeypatch):
    """微软账户设备码 OAuth：请求代码 → 轮询（mock 网络端点）"""
    from launcher.bedrock import msauth

    calls = []

    poll_count = [0]

    def fake_post(url, data=None, headers=None, timeout=30):
        fields = dict(data or {})
        calls.append(fields.get("grant_type") or fields.get("response_type") or "unknown")
        if fields.get("response_type") == "device_code":
            return {
                "device_code": "DEV123",
                "user_code": "ABC12",
                "verification_uri": "https://www.microsoft.com/link",
                "interval": 1,
                "expires_in": 900,
            }
        if fields.get("device_code") == "DEV123":
            poll_count[0] += 1
            if poll_count[0] < 2:
                return {"error": "authorization_pending"}
            return {"access_token": "AT-123", "refresh_token": "RT-1", "expires_in": 3600}
        return {"access_token": "AT-123", "refresh_token": "RT-1", "expires_in": 3600}

    monkeypatch.setattr(msauth, "_post_form", fake_post)
    device = msauth.request_device_code()
    assert device["user_code"] == "ABC12"
    assert msauth.MSA_CLIENT_ID == "0000000048183522"
    token = msauth.poll_device_code(device["device_code"], interval=0)
    assert token["access_token"] == "AT-123"
    assert calls[0] == "device_code"
    assert poll_count[0] == 2


def test_msa_refresh_token(monkeypatch):
    """refresh_token 刷新 access_token"""
    from launcher.bedrock import msauth

    def fake_post(url, data=None, headers=None, timeout=30):
        assert data["grant_type"] == "refresh_token"
        return {"access_token": "NEW-AT", "refresh_token": "NEW-RT"}

    monkeypatch.setattr(msauth, "_post_form", fake_post)
    result = msauth.refresh_access_token("RT-OLD")
    assert result["access_token"] == "NEW-AT"


def test_msa_credential_cache_roundtrip(tmp_path, monkeypatch):
    """凭证缓存：保存→读取 往返一致（加密存储）"""
    from launcher.bedrock import msauth

    monkeypatch.setattr(msauth, "set_cache_dir", lambda d: msauth._set_cache_dir_for_test(tmp_path))

    # 手动保存（不走 set_cache_dir 全局状态，避免影响其他测试）
    msauth._cache_dir = tmp_path
    ok = msauth.save_credentials("AT-123", "RT-456")
    assert ok
    creds = msauth.load_credentials()
    assert creds.get("access_token") == "AT-123"
    assert creds.get("refresh_token") == "RT-456"
    assert creds.get("saved_at", 0) > 0
    # 缓存文件本身不含明文
    raw = (tmp_path / msauth.CACHE_FILE_NAME).read_text(encoding="utf-8")
    assert "AT-123" not in raw
    assert "RT-456" not in raw
    # 清理
    msauth.clear_credentials()
    assert msauth.load_credentials() == {}


def test_msa_cached_token_refresh(monkeypatch, tmp_path):
    """缓存凭证存在时自动刷新并返回新 access_token"""
    from launcher.bedrock import msauth

    msauth._cache_dir = tmp_path
    msauth.save_credentials("OLD-AT", "RT-789")

    def fake_post(url, data=None, headers=None, timeout=30):
        assert data["grant_type"] == "refresh_token"
        return {"access_token": "FRESH-AT", "refresh_token": "FRESH-RT"}

    monkeypatch.setattr(msauth, "_post_form", fake_post)
    token = msauth.get_cached_access_token()
    assert token == "FRESH-AT"
    # 刷新成功后缓存被更新
    creds = msauth.load_credentials()
    assert creds.get("access_token") == "FRESH-AT"
    assert creds.get("refresh_token") == "FRESH-RT"


def test_msa_cached_token_missing(tmp_path, monkeypatch):
    """无缓存凭证时返回空字符串"""
    from launcher.bedrock import msauth

    msauth._cache_dir = tmp_path
    assert msauth.get_cached_access_token() == ""


def test_msa_cache_v2_encrypted(tmp_path):
    """v2 格式：凭证必须加密存储，明文 token 不会误判为已加密"""
    from launcher.bedrock import msauth
    import json

    msauth._cache_dir = tmp_path
    # 用真实的 MSA 风格 base64 token（gAAAA 开头，会被 is_encrypted 误判）
    fake_at = "EwDoA+pvBAAUKods63Ys1fGlwiccIFJ+qE1hANsAAVXpfBiOam83eqSz1deRe7pooNBj"
    fake_rt = "gAAAAABqfcKsSjqBjaI3JcpWY6oXOrYcO3Yo8XZAFB-RX51IIQRrMrnzbLv7QLkI1ulxTxGf"
    ok = msauth.save_credentials(fake_at, fake_rt)
    assert ok
    # 缓存文件必须是密文（不含明文 token）
    raw = (tmp_path / msauth.CACHE_FILE_NAME).read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data.get("format_version") == 2
    assert fake_at not in raw
    assert fake_rt not in raw
    # 读取还原
    creds = msauth.load_credentials()
    assert creds.get("access_token") == fake_at
    assert creds.get("refresh_token") == fake_rt


def test_msa_cache_v1_plaintext_migrate(tmp_path):
    """旧版 v1 缓存（明文误存）：读取可用并自动迁移为 v2 加密"""
    from launcher.bedrock import msauth
    import json

    msauth._cache_dir = tmp_path
    fake_at = "EwDoA+pvBAAUKods63Ys1fGlwiccIFJ+qE1hANsAAVXpfBiOam83eqSz1deRe7pooNBj"
    fake_rt = "gAAAAABqfcKsSjqBjaI3JcpWY6oXOrYcO3Yo8XZAFB-RX51IIQRrMrnzbLv7QLkI1ulxTxGf"
    cache_file = tmp_path / msauth.CACHE_FILE_NAME
    cache_file.write_text(
        json.dumps({"access_token": fake_at, "refresh_token": fake_rt, "saved_at": 1786626732}),
        encoding="utf-8",
    )
    creds = msauth.load_credentials()
    assert creds.get("access_token") == fake_at
    assert creds.get("refresh_token") == fake_rt
    # 已迁移为 v2（加密）
    raw = cache_file.read_text(encoding="utf-8")
    assert '"format_version": 2' in raw
    assert fake_at not in raw


def test_msa_cache_v2_corrupt_dropped(tmp_path):
    """v2 格式损坏字段（无法解密）应被丢弃，避免用坏 token 启动"""
    from launcher.bedrock import msauth
    import json

    msauth._cache_dir = tmp_path
    cache_file = tmp_path / msauth.CACHE_FILE_NAME
    cache_file.write_text(
        json.dumps(
            {
                "format_version": 2,
                "access_token": "gAAAAABcorrupted-corrupted-corrupted",
                "refresh_token": "gAAAAABcorrupted-corrupted-corrupted",
                "saved_at": 1786626732,
            }
        ),
        encoding="utf-8",
    )
    creds = msauth.load_credentials()
    # 损坏字段被丢弃（saved_at 元数据保留），无有效 token → 重新登录
    assert "access_token" not in creds
    assert "refresh_token" not in creds
    assert msauth.get_cached_access_token() == ""


# ─── appx.py：AppX 解压与清单修改 ────────────────────────────

SAMPLE_MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Package xmlns="http://schemas.microsoft.com/appx/manifest/foundation/windows10" xmlns:mp="http://schemas.microsoft.com/appx/manifest/foundation/windows10/2015/5" xmlns:uap="http://schemas.microsoft.com/appx/manifest/uap/windows10" xmlns:uap3="http://schemas.microsoft.com/appx/manifest/uap/windows10/3" IgnorableNamespaces="uap uap3">
  <Identity Name="Microsoft.MinecraftUWP" Publisher="CN=Microsoft Corporation" Version="1.21.20.1" />
  <Properties><DisplayName>Minecraft for Windows</DisplayName></Properties>
  <Capabilities>
    <rescap:Capability Name="something" />
    <DeviceCapability Name="internetClient" />
  </Capabilities>
  <Applications>
    <Application Id="App" Executable="Minecraft.Windows.exe" EntryPoint="App">
      <uap:VisualElements DisplayName="Minecraft for Windows" Square150x150Logo="Assets/logo.png" AppListEntry="default">
        <uap:SplashScreen Image="Assets/splash.png" />
      </uap:VisualElements>
      <Extensions>
        <uap3:Extension Category="windows.appExtension" />
      </Extensions>
    </Application>
  </Applications>
</Package>
"""


def test_extract_appx(tmp_path):
    pkg = tmp_path / "mc.appx"
    with zipfile.ZipFile(pkg, "w") as zf:
        zf.writestr("AppxManifest.xml", SAMPLE_MANIFEST)
        zf.writestr("Assets/logo.png", b"png-data")
        zf.writestr("AppxSignature.p7x", b"sig")
    out = tmp_path / "ver"
    appx.extract_appx(pkg, out)
    assert (out / "AppxManifest.xml").exists()
    assert not (out / "AppxSignature.p7x").exists()
    assert (out / "Assets" / "logo.png").read_bytes() == b"png-data"


def test_edit_manifest(tmp_path):
    version_dir = tmp_path / "ver"
    version_dir.mkdir()
    (version_dir / "AppxManifest.xml").write_text(SAMPLE_MANIFEST, encoding="utf-8")
    appx.edit_manifest(version_dir, "我的基岩版")
    text = (version_dir / "AppxManifest.xml").read_text(encoding="utf-8")
    assert 'Version="1.21.20.2"' in text
    assert 'DisplayName="我的基岩版"' in text
    assert 'AppListEntry="none"' in text
    assert "runFullTrust" in text
    assert "Microsoft.coreAppActivation" in text
    assert "loopbackAccessRules" in text
    assert "mcworld" in text
    assert "desktop4:SupportsMultipleInstances" in text
    assert (version_dir / "CustomCapability.SCCD").exists()
    import base64

    expected = base64.b64decode(appx.SCCD_BASE64)
    assert (version_dir / "CustomCapability.SCCD").read_bytes() == expected


def test_build_uri():
    assert launch._build_uri("") == "minecraft://launch"
    assert launch._build_uri("minecraft://creator/?Editor=true") == "minecraft://launch?Editor=true"
    assert launch._build_uri("minecraft://creator") == "minecraft://launch?creator=true"
    assert launch._build_uri("a=1 b=2") == "minecraft://launch?a=1&b=2"
    assert launch._build_uri("raw-args") == "minecraft://launch?args=raw-args"


# ─── BedrockManager：安装与卸载 ──────────────────────────────


def test_manager_install_and_remove(tmp_path, monkeypatch):
    from launcher.bedrock import BedrockManager, TYPE_NORMALIZE
    from launcher.bedrock.download import DownloadError

    db_file = tmp_path / "bedrock_versions" / source.DB_CACHE_FILE
    db_file.parent.mkdir(parents=True)
    db_file.write_text(json.dumps(SAMPLE_DB), encoding="utf-8")

    manager = BedrockManager(tmp_path / "bedrock_versions")
    version = "1.21.120.20"

    def fake_download_file(urls, dest, progress_cb=None, threads=8, stop_event=None, timeout=30):
        payload = b"MZ-uwp-package"
        # 仅写文件头，便于测试；UWP 解压用 zip 校验，这里直接构造 zip
        with zipfile.ZipFile(dest, "w") as zf:
            zf.writestr("AppxManifest.xml", SAMPLE_MANIFEST)
        return dest

    def fake_verify(file_path, variation):
        return True

    monkeypatch.setattr("launcher.bedrock.resolve_download_url", lambda build_info, arch="x64": "http://fake.example.com/mc.appx")
    monkeypatch.setattr("launcher.bedrock.download_file", fake_download_file)
    monkeypatch.setattr("launcher.bedrock.verify_variation_md5", fake_verify)
    monkeypatch.setattr(
        "launcher.bedrock.appx.extract_appx",
        lambda pkg, dst, progress_cb=None: (dst.mkdir(parents=True, exist_ok=True), (dst / "AppxManifest.xml").write_text(SAMPLE_MANIFEST, encoding="utf-8")),
    )
    monkeypatch.setattr("launcher.bedrock.appx.register_appx", lambda dst, timeout=180: None)
    monkeypatch.setattr("launcher.bedrock.env.ensure_developer_mode", lambda notify=None: (True, ""))

    info = manager.install_version(version, name="我的基岩测试")
    assert info["name"] == "我的基岩测试"
    assert info["version"] == version
    assert info["build_type"] == "UWP"

    installed = manager.get_installed_versions()
    assert len(installed) == 1
    assert installed[0]["name"] == "我的基岩测试"

    # 重复安装应报错
    with pytest.raises(RuntimeError):
        manager.install_version(version, name="我的基岩测试")

    manager.remove_version("我的基岩测试")
    assert manager.get_installed_versions() == []
