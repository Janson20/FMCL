""".NET 10 SDK 检测与官方下载直链

GDK 版下载安装需要 .NET 10 SDK（运行时 dotnet publish 构建解压器），
缺失时引导用户从微软官方渠道下载对应架构的 SDK 安装包。
"""

import os
import platform
import shutil
import subprocess
from typing import Optional

import requests
from logzero import logger

from launcher.bedrock.source import USER_AGENT

SDK_CHANNEL = "10.0"
SDK_MAJOR_MINOR = "10."
# 官方渠道元数据与直链模板
RELEASES_METADATA_URL = (
    f"https://dotnetcli.blob.core.windows.net/dotnet/release-metadata/{SDK_CHANNEL}/releases.json"
)
SDK_INSTALLER_URL = (
    "https://builds.dotnet.microsoft.com/dotnet/Sdk/{version}/dotnet-sdk-{version}-win-{arch}.exe"
)
SDK_DOWNLOAD_PAGE = "https://dotnet.microsoft.com/en-us/download/dotnet/10.0"

_REQUEST_TIMEOUT = 30
_ARCH_MAP = {"AMD64": "x64", "ARM64": "arm64", "X86": "x86"}
_RUNTIME_LIST_TIMEOUT = 30


class DotnetError(RuntimeError):
    """.NET 检测/下载链接错误"""


def has_sdk10() -> bool:
    """检测系统是否安装了 .NET 10 SDK（dotnet --list-sdks 首行版本以 10. 开头）"""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        return False
    try:
        proc = subprocess.run(
            [dotnet, "--list-sdks"],
            capture_output=True,
            text=True,
            timeout=_RUNTIME_LIST_TIMEOUT,
        )
        return any(
            line.strip().startswith(SDK_MAJOR_MINOR)
            for line in (proc.stdout or "").splitlines()
        )
    except Exception as e:
        logger.debug(f"检测 .NET 10 SDK 失败: {e}")
        return False


def detect_arch() -> str:
    """检测系统架构（x64 / arm64 / x86）"""
    arch = os.environ.get("PROCESSOR_ARCHITECTURE", "").upper()
    if arch in _ARCH_MAP:
        return _ARCH_MAP[arch]
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86", "i386", "i686"):
        return "x86"
    return "x64"


def sdk_download_url(arch: Optional[str] = None) -> str:
    """生成当前架构的 .NET 10 SDK 官方下载直链（releases.json 取最新稳定版）"""
    arch = arch or detect_arch()
    try:
        resp = requests.get(
            RELEASES_METADATA_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=_REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        version = resp.json().get("latest-sdk", "")
        if not version or not version.startswith(SDK_MAJOR_MINOR):
            raise DotnetError(f"releases.json 缺少合法 latest-sdk: {version!r}")
        return SDK_INSTALLER_URL.format(version=version, arch=arch)
    except (requests.RequestException, ValueError, KeyError) as e:
        raise DotnetError(f"获取 .NET SDK 下载链接失败: {e}") from e
