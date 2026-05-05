"""Minecraft启动器 - Java安装模块

提供各平台、各架构的 JDK 下载链接和包管理器安装命令，
替换 minecraft-launcher-lib 自带的 Java runtime 下载机制。

支持的 JDK 版本: 8, 17, 21, 25
支持的平台: Windows, macOS, Linux
支持的架构: x64, aarch64

用法:
    from launcher.java_install import get_download_url, get_install_command
    url = get_download_url(21, "windows", "x64")
    cmd = get_install_command(21)
"""

import platform
import subprocess
from typing import Optional, Tuple


# ──────────────────────────────────────────────
# JDK 下载链接 (Microsoft OpenJDK / Adoptium)
# ──────────────────────────────────────────────

_JDK_DOWNLOADS = {
    8: {
        "windows": {
            "x64": "https://aka.ms/download-jdk/microsoft-jdk-8-windows-x64.zip",
            "aarch64": "https://aka.ms/download-jdk/microsoft-jdk-8-windows-aarch64.zip",
        },
        "mac": {
            "x64": "https://aka.ms/download-jdk/microsoft-jdk-8-macOS-x64.tar.gz",
            "aarch64": "https://aka.ms/download-jdk/microsoft-jdk-8-macOS-aarch64.tar.gz",
        },
        "linux": {
            "x64": "https://aka.ms/download-jdk/microsoft-jdk-8-linux-x64.tar.gz",
            "aarch64": "https://aka.ms/download-jdk/microsoft-jdk-8-linux-aarch64.tar.gz",
        },
    },
    17: {
        "windows": {
            "x64": "https://aka.ms/download-jdk/microsoft-jdk-17-windows-x64.zip",
            "aarch64": "https://aka.ms/download-jdk/microsoft-jdk-17-windows-aarch64.zip",
        },
        "mac": {
            "x64": "https://aka.ms/download-jdk/microsoft-jdk-17-macOS-x64.tar.gz",
            "aarch64": "https://aka.ms/download-jdk/microsoft-jdk-17-macOS-aarch64.tar.gz",
        },
        "linux": {
            "x64": "https://aka.ms/download-jdk/microsoft-jdk-17-linux-x64.tar.gz",
            "aarch64": "https://aka.ms/download-jdk/microsoft-jdk-17-linux-aarch64.tar.gz",
        },
    },
    21: {
        "windows": {
            "x64": "https://aka.ms/download-jdk/microsoft-jdk-21-windows-x64.msi",
            "aarch64": "https://aka.ms/download-jdk/microsoft-jdk-21-windows-aarch64.msi",
        },
        "mac": {
            "x64": "https://aka.ms/download-jdk/microsoft-jdk-21-macOS-x64.tar.gz",
            "aarch64": "https://aka.ms/download-jdk/microsoft-jdk-21-macOS-aarch64.tar.gz",
        },
        "linux": {
            "x64": "https://aka.ms/download-jdk/microsoft-jdk-21-linux-x64.tar.gz",
            "aarch64": "https://aka.ms/download-jdk/microsoft-jdk-21-linux-aarch64.tar.gz",
        },
    },
    25: {
        "windows": {
            "x64": "https://aka.ms/download-jdk/microsoft-jdk-25-windows-x64.msi",
            "aarch64": "https://aka.ms/download-jdk/microsoft-jdk-25-windows-aarch64.msi",
        },
        "mac": {
            "x64": "https://aka.ms/download-jdk/microsoft-jdk-25-macOS-x64.tar.gz",
            "aarch64": "https://aka.ms/download-jdk/microsoft-jdk-25-macOS-aarch64.tar.gz",
        },
        "linux": {
            "x64": None,
            "aarch64": None,
        },
    },
}

_ADOPTIUM_LATEST_URL = "https://adoptium.net/temurin/releases/?version={version}"


# ──────────────────────────────────────────────
# 包管理器安装命令
# ──────────────────────────────────────────────

def _get_windows_install_cmd(jdk_version: int) -> str:
    return f"winget install Microsoft.OpenJDK.{jdk_version}"


def _get_macos_install_cmd(jdk_version: int) -> str:
    if jdk_version == 25:
        return "brew install openjdk"
    return f"brew install openjdk@{jdk_version}"


def _get_linux_install_cmd(jdk_version: int) -> Optional[str]:
    if jdk_version == 25:
        return None
    pkg_map = {8: "openjdk-8-jdk", 17: "openjdk-17-jdk", 21: "openjdk-21-jdk"}
    pkg = pkg_map.get(jdk_version)
    if not pkg:
        return None
    return f"sudo apt update && sudo apt install {pkg}"


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────

def get_download_url(jdk_version: int, os_name: Optional[str] = None, arch: Optional[str] = None) -> Optional[str]:
    os_name = os_name or _get_current_os()
    arch = arch or _get_current_arch()

    version_data = _JDK_DOWNLOADS.get(jdk_version, {})
    os_data = version_data.get(os_name, {})
    url = os_data.get(arch)

    if url:
        return url

    if jdk_version == 25 and os_name == "linux":
        return _ADOPTIUM_LATEST_URL.format(version=25)

    return None


def get_install_command(jdk_version: int, os_name: Optional[str] = None) -> Optional[str]:
    os_name = os_name or _get_current_os()

    if os_name == "windows":
        return _get_windows_install_cmd(jdk_version)
    elif os_name == "mac":
        return _get_macos_install_cmd(jdk_version)
    elif os_name == "linux":
        return _get_linux_install_cmd(jdk_version)
    return None


def get_macos_symlink_cmd(jdk_version: int) -> str:
    v = "" if jdk_version == 25 else f"@{jdk_version}"
    return (
        f"sudo ln -sfn $(brew --prefix)/opt/openjdk{v}/libexec/openjdk.jdk "
        f"/Library/Java/JavaVirtualMachines/openjdk-{jdk_version}.jdk"
    )


def get_java_install_guidance(jdk_version: int) -> dict:
    os_name = _get_current_os()
    arch = _get_current_arch()

    guidance = {
        "jdk_version": jdk_version,
        "os": os_name,
        "arch": arch,
        "download_url": get_download_url(jdk_version, os_name, arch),
        "install_command": get_install_command(jdk_version, os_name),
        "has_package_manager": get_install_command(jdk_version, os_name) is not None,
    }

    if os_name == "mac":
        guidance["macos_symlink_cmd"] = get_macos_symlink_cmd(jdk_version)

    return guidance


def _get_current_os() -> str:
    system = platform.system().lower()
    if system == "windows":
        return "windows"
    elif system == "darwin":
        return "mac"
    return "linux"


def _get_current_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("arm64", "aarch64"):
        return "aarch64"
    if machine in ("amd64", "x86_64"):
        return "x64"
    return "x64"


def install_java_via_winget(jdk_version: int) -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            ["winget", "install", f"Microsoft.OpenJDK.{jdk_version}", "--accept-source-agreements"],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0:
            return True, f"JDK {jdk_version} installed successfully via winget"
        return False, result.stderr or result.stdout or "Unknown error"
    except FileNotFoundError:
        return False, "winget not found on this system"
    except subprocess.TimeoutExpired:
        return False, "winget installation timed out"
    except Exception as e:
        return False, str(e)
