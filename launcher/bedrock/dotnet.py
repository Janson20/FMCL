""".NET 10 SDK 检测与官方下载直链

GDK 版下载安装需要 .NET 10 SDK（运行时 dotnet publish 构建解压器），
缺失时引导用户从微软官方渠道下载对应架构的 SDK 安装包。
"""

import os
import platform
import shutil
import subprocess
from pathlib import Path
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


def _dotnet_candidates() -> list:
    """dotnet CLI 候选路径：PATH 优先，附带常见安装位置兜底

    SDK 安装后若 FMCL 已在运行（PATH 未刷新），仅靠 shutil.which 会漏检。
    """
    candidates = []
    dotnet = shutil.which("dotnet")
    if dotnet:
        candidates.append(dotnet)
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if not base:
            continue
        probe = Path(base) / "dotnet" / "dotnet.exe"
        if probe.is_file() and str(probe) not in candidates:
            candidates.append(str(probe))
    return candidates


def _registry_has_sdk10() -> bool:
    """注册表兜底：SDK 安装器（32 位进程）把版本子键写在 32 位视图（WOW6432Node）"""
    try:
        import winreg
    except ImportError:
        return False
    roots = (
        r"SOFTWARE\dotnet\Setup\InstalledVersions\x64\sdk",
        r"SOFTWARE\dotnet\Setup\InstalledVersions\x86\sdk",
    )
    for root in roots:
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root, 0, winreg.KEY_READ | view) as key:
                    index = 0
                    while True:
                        try:
                            name = winreg.EnumKey(key, index)
                        except OSError:
                            break
                        if name.startswith("10."):
                            return True
                        index += 1
            except OSError:
                continue
    return False


def _filesystem_has_sdk10() -> bool:
    """文件系统兜底：%ProgramFiles%\dotnet\sdk\10.* 存在即视为已装"""
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if not base:
            continue
        sdk_dir = Path(base) / "dotnet" / "sdk"
        try:
            if any(p.is_dir() and p.name.startswith("10.") for p in sdk_dir.iterdir()):
                return True
        except OSError:
            continue
    return False


def has_sdk10() -> bool:
    """检测系统是否安装了 .NET 10 SDK

    检测顺序：dotnet CLI（--list-sdks）→ 注册表 InstalledVersions → 文件系统。
    PATH 未刷新（安装 SDK 后 FMCL 已处于运行状态）时由后两者兜底。
    """
    for dotnet in _dotnet_candidates():
        try:
            proc = subprocess.run(
                [dotnet, "--list-sdks"],
                capture_output=True,
                text=True,
                timeout=_RUNTIME_LIST_TIMEOUT,
            )
            if any(
                line.strip().startswith(SDK_MAJOR_MINOR)
                for line in (proc.stdout or "").splitlines()
            ):
                return True
        except Exception as e:
            logger.debug(f"检测 .NET 10 SDK 失败 ({dotnet}): {e}")
    return _registry_has_sdk10() or _filesystem_has_sdk10()


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
