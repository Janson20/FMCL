"""基岩版版本库获取与下载链接解析模块

版本库来源与格式对齐 BedrockBoot（BedrockLauncher.Core）：
- 版本库: mcappx.com 的 bedrock.json（备用 Gitee / BMCBL 源），多源自动回退
- GDK 包: MetaData 内即为直链，可替换 xboxlive 镜像前缀加速
- UWP 包: MetaData 为 UpdateID（GUID），需向微软 Windows Update SOAP 接口换取下载链接
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
from logzero import logger

# xboxlive / tlu.dl 下载端点对非浏览器 UA 敏感（403/504），使用浏览器兼容 UA
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36 FMCL/2.11"
)

# 版本库源（多源自动回退，第一个成功的生效）
VERSION_DB_SOURCES: List[Dict[str, str]] = [
    {"name": "McAppx 源", "url": "https://data.mcappx.com/v2/bedrock.json"},
    {"name": "BedrockBoot 源", "url": "https://raw.giteeusercontent.com/minecraftyjq/bedrock-version-db/raw/main/data/bedrock.json"},
    {"name": "BMCBL 源 ①", "url": "https://mcappx.chlna6666.com"},
    {"name": "BMCBL 源 ②", "url": "https://api.chlna6666.com/api/v1/bedrock/mcappx"},
]

# GDK 包下载镜像（BedrockBoot SourceList.GameFileDownloadSource，router 为原始 URL 路径）
GDK_MIRROR_HOSTS: List[str] = [
    "assets1.xboxlive.cn",
    "assets2.xboxlive.cn",
    "assets1.xboxlive.com",
    "assets2.xboxlive.com",
    "xvcf1.xboxlive.com",
    "xvcf2.xboxlive.com",
    "d1.xboxlive.cn",
    "d2.xboxlive.cn",
    "d1.xboxlive.com",
    "d2.xboxlive.com",
]

# 微软 Windows Update SOAP 接口（UWP 链接解析）
SOAP_UPDATE_URI = "https://fe3.delivery.mp.microsoft.com/ClientWebService/client.asmx/secured"

SOAP_DEVICE_ATTRIBUTES = (
    "E:BranchReadinessLevel=CBB&amp;DchuNvidiaGrfxExists=1&amp;ProcessorIdentifier=Intel64%20Family%206"
    "%20Model%2063%20Stepping%202&amp;CurrentBranch=rs4_release&amp;DataVer_RS5=1942&amp;FlightRing=Retail"
    "&amp;AttrDataVer=57&amp;InstallLanguage=en-US&amp;DchuAmdGrfxExists=1&amp;OSUILocale=en-US"
    "&amp;InstallationType=Client&amp;FlightingBranchName=&amp;Version_RS5=10&amp;UpgEx_RS5=Green"
    "&amp;GStatus_RS5=2&amp;OSSkuId=48&amp;App=WU&amp;InstallDate=1529700913&amp;ProcessorManufacturer=GenuineIntel"
    "&amp;AppVer=10.0.17134.471&amp;OSArchitecture=AMD64&amp;UpdateManagementGroup=2&amp;IsDeviceRetailDemo=0"
    "&amp;HidOverGattReg=C%3A%5CWINDOWS%5CSystem32%5CDriverStore%5CFileRepository%5Chidbthle.inf_amd64_467f181075371c89"
    "%5CMicrosoft.Bluetooth.Profiles.HidOverGatt.dll&amp;IsFlightingEnabled=0&amp;DchuIntelGrfxExists=1"
    "&amp;TelemetryLevel=1&amp;DefaultUserRegion=244&amp;DeferFeatureUpdatePeriodInDays=365&amp;Bios=Unknown"
    "&amp;WuClientVer=10.0.17134.471&amp;PausedFeatureStatus=1&amp;Steam=URL%3Asteam%20protocol&amp;Free=8to16"
    "&amp;OSVersion=10.0.17134.472&amp;DeviceFamily=Windows.Desktop"
)

SOAP_BODY_TEMPLATE = """<s:Envelope xmlns:a="http://www.w3.org/2005/08/addressing" xmlns:s="http://www.w3.org/2003/05/soap-envelope">
\t<s:Header>
\t\t<a:Action s:mustUnderstand="1">http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService/GetExtendedUpdateInfo2</a:Action>
\t\t<a:MessageID>urn:uuid:5754a03d-d8d5-489f-b24d-efc31b3fd32d</a:MessageID>
\t\t<a:To s:mustUnderstand="1">https://fe3.delivery.mp.microsoft.com/ClientWebService/Client.asmx/secured</a:To>
\t\t<o:Security s:mustUnderstand="1" xmlns:o="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">
\t\t\t<Timestamp xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
\t\t\t\t<Created>{created}</Created>
\t\t\t\t<Expires>{expires}</Expires>
\t\t\t</Timestamp>
\t\t\t<wuws:WindowsUpdateTicketsToken wsu:id="ClientMSA" xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd" xmlns:wuws="http://schemas.microsoft.com/msus/2014/10/WindowsUpdateAuthorization">
\t\t\t\t<TicketType Name="AAD" Version="1.0" Policy="MBI_SSL">
\t\t\t\t</TicketType>
\t\t\t</wuws:WindowsUpdateTicketsToken>
\t\t</o:Security>
\t</s:Header>
\t<s:Body>
\t\t<GetExtendedUpdateInfo2 xmlns="http://www.microsoft.com/SoftwareDistribution/Server/ClientWebService">
\t\t\t<updateIDs>
\t\t\t\t<UpdateIdentity>
\t\t\t\t\t<UpdateID>{update_id}</UpdateID>
\t\t\t\t\t<RevisionNumber>1</RevisionNumber>
\t\t\t\t</UpdateIdentity>
\t\t\t</updateIDs>
\t\t\t<infoTypes>
\t\t\t\t<XmlUpdateFragmentType>FileUrl</XmlUpdateFragmentType>
\t\t\t</infoTypes>
\t\t\t<deviceAttributes>{device_attributes}</deviceAttributes>
\t\t</GetExtendedUpdateInfo2>
\t</s:Body>
</s:Envelope>"""

DB_CACHE_FILE = "version_db.json"
DB_CACHE_TTL_SECONDS = 6 * 3600


def _get(url: str, timeout: int = 30, **kwargs) -> requests.Response:
    """带 UA 的 GET 请求"""
    headers = {"User-Agent": USER_AGENT}
    headers.update(kwargs.pop("headers", {}))
    return requests.get(url, timeout=timeout, headers=headers, **kwargs)


def _extract_versions(data: Any) -> Optional[Dict[str, Any]]:
    """从各源响应中规范提取版本字典 {版本号: 条目}"""
    if not isinstance(data, dict):
        return None
    for key in ("From_mcappx.com", "versions", "data"):
        value = data.get(key)
        if isinstance(value, dict) and value:
            return value
    return data if data else None


def fetch_version_db(timeout: int = 30) -> Dict[str, Any]:
    """拉取基岩版版本库，多源自动回退"""
    last_error: Optional[Exception] = None
    for source in VERSION_DB_SOURCES:
        try:
            resp = _get(source["url"], timeout=timeout)
            resp.raise_for_status()
            versions = _extract_versions(resp.json())
            if not versions:
                raise ValueError("响应中未找到版本数据")
            logger.info(f"版本库拉取成功: {source['name']} ({len(versions)} 个版本)")
            return {"CreationTime": time.strftime("%Y-%m-%dT%H:%M:%S"), "Source": source["name"], "From_mcappx.com": versions}
        except Exception as e:
            last_error = e
            logger.warning(f"版本库源 {source['name']} 拉取失败: {e}")
    raise RuntimeError(f"所有版本库源均不可用: {last_error}")


def load_version_db(cache_dir: Path, refresh: bool = False) -> Dict[str, Any]:
    """读取版本库（带磁盘缓存，TTL 内不重复拉取）"""
    cache_file = cache_dir / DB_CACHE_FILE
    if not refresh and cache_file.exists():
        try:
            mtime = cache_file.stat().st_mtime
            if time.time() - mtime < DB_CACHE_TTL_SECONDS:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                if data.get("From_mcappx.com"):
                    return data
        except Exception as e:
            logger.warning(f"版本库缓存读取失败: {e}")
    try:
        data = fetch_version_db()
    except RuntimeError:
        if cache_file.exists():
            logger.warning("网络不可用，使用缓存版本库")
            return json.loads(cache_file.read_text(encoding="utf-8"))
        raise
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def get_build_info(db: Dict[str, Any], version_key: str) -> Optional[Dict[str, Any]]:
    """按版本号获取版本条目"""
    return db.get("From_mcappx.com", {}).get(version_key)


def find_variation(build_info: Dict[str, Any], arch: str = "x64") -> Optional[Dict[str, Any]]:
    """查找指定架构的变体（默认 x64）"""
    for variation in build_info.get("Variations", []):
        if variation.get("Arch") == arch:
            return variation
    return None


def resolve_download_url(build_info: Dict[str, Any], arch: str = "x64", timeout: int = 30) -> str:
    """解析版本包的下载链接

    GDK: MetaData[0] 即直链；UWP: 通过 Windows Update SOAP 接口换取链接
    """
    variation = find_variation(build_info, arch)
    if variation is None:
        raise ValueError(f"该版本没有 {arch} 架构的包")
    metadata = variation.get("MetaData") or []
    if not metadata:
        raise ValueError("该版本没有可用的下载元数据")
    update_id = metadata[-1]
    if update_id.lower().startswith("http"):
        return update_id
    return resolve_uwp_url(update_id, timeout=timeout)


def resolve_uwp_url(update_id: str, timeout: int = 30) -> str:
    """通过微软 Windows Update SOAP 接口用 UpdateID 换取 UWP 包下载链接"""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    body = SOAP_BODY_TEMPLATE.format(
        created=now.isoformat(),
        expires=(now + timedelta(minutes=5)).isoformat(),
        update_id=update_id,
        device_attributes=SOAP_DEVICE_ATTRIBUTES,
    )
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/soap+xml; charset=utf-8"}
    try:
        resp = requests.post(SOAP_UPDATE_URI, data=body.encode("utf-8"), headers=headers, timeout=timeout)
        resp.raise_for_status()
        text = resp.text
    except requests.exceptions.SSLError:
        # 该端点证书链依赖 Windows 信任库（BedrockBoot 经 .NET HttpClient 同样走系统库），
        # requests/certifi 校验失败时回退 PowerShell schannel 通道
        logger.warning("fe3 端点证书校验失败，回退 Windows 系统证书库通道")
        text = _soap_post_powershell(body, timeout)
    url = _parse_soap_download_url(text)
    if not url:
        raise RuntimeError(f"SOAP 接口未返回下载链接 (UpdateID: {update_id})")
    return url


def _soap_post_powershell(body: str, timeout: int = 30) -> str:
    """通过 PowerShell（schannel / Windows 证书库）发起 SOAP 请求"""
    import subprocess

    escaped_body = body.replace("'", "''")
    script = (
        "$ErrorActionPreference='Stop'; "
        "$body = @'\n" + escaped_body + "\n'@; "
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; "
        "$r = Invoke-WebRequest -Uri '" + SOAP_UPDATE_URI
        + "' -Method Post -ContentType 'application/soap+xml; charset=utf-8' -Body $body "
        + f"-UseBasicParsing -TimeoutSec {timeout}; "
        "$r.Content"
    )
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout + 60,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or "").strip()
        raise RuntimeError(f"PowerShell SOAP 请求失败: {detail[:300]}")
    return proc.stdout


def _parse_soap_download_url(soap_response: str) -> Optional[str]:
    """解析 SOAP 响应，取 FileUrl 类型的下载链接（复杂链接优先，取最后一条兜底）"""
    import re
    from html import unescape

    urls: List[str] = []
    for match in re.finditer(r"<Url[^>]*>(.*?)</Url>", soap_response, re.S):
        value = unescape(match.group(1)).strip()
        if value:
            urls.append(value)
    if not urls:
        return None
    for url in urls:
        if "?P1=" in url or "tlu.dl." in url or "&P2=" in url or "%3d" in url.lower() or len(url) > 150:
            return url
    return urls[-1]


def build_mirror_urls(original_url: str) -> List[str]:
    """基于原始 GDK 链接生成镜像链接列表（BedrockBoot 同款镜像池，原链接优先）"""
    urls = [original_url]
    try:
        from urllib.parse import urlparse

        parsed = urlparse(original_url)
        if not parsed.path:
            return urls
        router = parsed.path
        for host in GDK_MIRROR_HOSTS:
            candidate = f"http://{host}{router}"
            if candidate != original_url:
                urls.append(candidate)
    except Exception:
        pass
    return urls
