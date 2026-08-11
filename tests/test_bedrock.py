"""基岩版模块单元测试（全部离线，不访问网络）"""

import io
import json
import struct
import threading
import zipfile
from pathlib import Path

import pytest

from launcher.bedrock import appx, launch, source, xvd
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


# ─── xvd.py：XVD 容器解析与提取 ──────────────────────────────


def _build_xvd_file(path: Path, files: dict):
    """构造一个未加密、无完整性校验的 XVD 容器 fixture

    files: {相对路径: bytes}；文件数据按段顺序分页存放
    """
    segments = []
    raw_files = {}
    for name, data in files.items():
        segments.append((name, len(data)))
        raw_files[name] = data

    # SegmentMetadata.bin
    header_length = 100
    paths_start = header_length + len(segments) * 16
    path_blobs = []
    entries = []
    for name, size in segments:
        raw = name.encode("utf-16-le")
        path_offset = len(b"".join(path_blobs))
        entries.append(struct.pack("<HHIQ", 0, len(raw) // 2, path_offset, size))
        path_blobs.append(raw)
    file_paths_length = len(b"".join(path_blobs))
    segment_meta = bytearray()
    segment_meta += struct.pack("<IIIIII", 0x5345474D, 1, 1, header_length, len(segments), file_paths_length)
    segment_meta += b"\x00" * 16  # PDUID
    segment_meta += b"\x00" * 60  # Unknown
    segment_meta += b"".join(entries)
    segment_meta += b"".join(path_blobs)
    segment_meta = bytes(segment_meta)

    # 用户数据包文件：游戏文件 + SegmentMetadata.bin（真实 XVD 中段清单随包携带）
    raw_files = {**files, "SegmentMetadata.bin": segment_meta}

    # 用户数据区（PackageFiles 类型）
    user_data = bytearray()
    # 条目偏移相对 0x10 头末尾：绝对偏移(0x10+4+520+4+528*n+acc) - 0x10
    entry_offset_base = 4 + 520 + 4 + 528 * len(raw_files)
    entry_blobs = []
    payload = bytearray()
    for name, data in raw_files.items():
        entry_blobs.append(
            struct.pack("<520sII", name.encode("utf-16-le"), len(data), entry_offset_base + len(payload))
        )
        payload += data
    ud_header_length = 0x10 + 4 + 520 + 4 + 528 * len(raw_files) + len(payload)
    user_data += struct.pack("<IIII", 0x10, 1, 0, 0)  # Length=头大小, Version, Type=PackageFiles, Unknown
    user_data += struct.pack("<I", 1)  # PackageFiles 版本
    user_data += b"\x00" * 520  # PackageFullName
    user_data += struct.pack("<I", len(raw_files))
    user_data += b"".join(entry_blobs)
    user_data += payload
    user_data = bytes(user_data)
    assert len(user_data) == ud_header_length

    user_data_pages = (len(user_data) + 0xFFF) // 0x1000
    xvc_pages = 1
    # XVC 数据区
    region_offset = 0x3000 + user_data_pages * 0x1000 + xvc_pages * 0x1000
    # 每段文件独占页（提取器按页推进），区域长度 = 各段页数之和
    region_length = sum((size + 0xFFF) // 0x1000 for _name, size in segments) * 0x1000
    xvc_data = bytearray(0x1000)
    # XvcInfo: ContentID 0x10 + KeyIds 0xC00 + Description 0x100
    struct.pack_into("<I", xvc_data, 0xD10, 1)  # Version
    struct.pack_into("<I", xvc_data, 0xD14, 1)  # RegionCount
    struct.pack_into("<I", xvc_data, 0xD3C, 1)  # UpdateSegmentCount
    struct.pack_into("<I", xvc_data, 0xD50, 0)  # RegionSpecifierCount
    # Region 0（起始于 0xDA8）
    struct.pack_into("<IHHII", xvc_data, 0xDA8, 1, 0xFFFF, 0, 0x10, 0)
    struct.pack_into("<QQ", xvc_data, 0xDA8 + 0x50, region_offset, region_length)
    # UpdateSegment（紧随 regions）
    struct.pack_into("<IQ", xvc_data, 0xDA8 + 0x80, region_offset // 0x1000, 0)
    xvc_data = bytes(xvc_data)

    # 文件数据区（分页，仅含段文件，不含用户数据包文件）
    page_data = bytearray()
    for _name, data in files.items():
        page_data += data
        page_data += b"\x00" * (-len(page_data) % 0x1000)
    page_data = bytes(page_data[:region_length])

    header = bytearray(0x1000)
    header[0x200:0x208] = b"MSXVD\0\0\0"
    struct.pack_into("<I", header, 0x208, 2 | 4)  # EncryptionDisabled | DataIntegrityDisabled
    struct.pack_into("<I", header, 0x20C, 1)  # FormatVersion
    struct.pack_into("<Q", header, 0x218, len(page_data))  # DriveSize
    struct.pack_into("<I", header, 0x280, 0)  # Kind = Fixed
    struct.pack_into("<I", header, 0x28C, len(user_data))  # UserDataLength
    struct.pack_into("<I", header, 0x290, len(xvc_data))  # XvcDataLength

    with open(path, "wb") as f:
        f.write(header)
        f.write(b"\x00" * (0x3000 - 0x1000))
        f.write(user_data)
        f.write(b"\x00" * (-len(user_data) % 0x1000))
        f.write(xvc_data)
        f.write(page_data)
    return path


def test_xvd_extract(tmp_path):
    files = {
        "Minecraft.Windows.exe": b"MZ" + b"\x00" * 4000,
        "data/dir/test.txt": b"hello-bedrock",
        "config.json": b'{"version": 1}',
    }
    pkg = _build_xvd_file(tmp_path / "test.insPack", files)
    out = tmp_path / "out"
    file_count = xvd.extract_gdk_package(pkg, out)
    assert file_count == len(files)
    for name, data in files.items():
        assert (out / name).read_bytes() == data


def test_xvd_header_offsets():
    data = bytearray(0x1000)
    struct.pack_into("<I", data, 0x460, 5)  # MutableDataPageCount
    struct.pack_into("<Q", data, 0xFE4, 0x1234)  # ResilientDataOffset
    header = xvd.XvdHeader(bytes(data))
    assert header.mutable_data_page_count == 5
    assert header.mutable_data_length == 0x5000
    assert header.resilient_data_offset == 0x1234


# ─── appx.py：AppX 解包与清单修改 ────────────────────────────

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
        # 仅写文件头，便于测试；UWP 解包用 zip 校验，这里直接构造 zip
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
