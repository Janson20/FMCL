"""Minecraft启动器 - Java运行时扫描模块

跨平台 (Windows / macOS / Linux) 扫描系统已安装的 Java 运行时，
报告版本、架构和元数据。支持为指定 Minecraft 版本推荐最佳 Java。

用法:
    from launcher.java_scanner import scan_all, recommend_for_mc
    javas = scan_all()
    best = recommend_for_mc(javas, "1.21.4")
"""

import os
import re
import subprocess
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Dict

from logzero import logger


# ──────────────────────────────────────────────
# Data model
# ──────────────────────────────────────────────

@dataclass
class JavaRuntime:
    path: str
    home: str
    major_version: int
    version_str: str
    is_jre: bool
    arch: str
    os_name: str
    source: str

    @property
    def display_name(self) -> str:
        kind = "JRE" if self.is_jre else "JDK"
        return f"Java {self.major_version} ({kind}) - {self.version_str} [{self.arch}] @ {self.home}"

    def can_run_minecraft(self, mc_version: str) -> bool:
        return self.major_version >= _min_java_for_mc(mc_version)


# ──────────────────────────────────────────────
# MC version -> minimum Java mapping
# ──────────────────────────────────────────────

_OLD_SNAPSHOT_RE = re.compile(r'^(\d+)w\d+\w?$')
_NEW_SNAPSHOT_RE = re.compile(r'^(\d+)\.(\d+)-snapshot-\d+$')


def _parse_mc_version(mc_version: str) -> tuple:
    version = mc_version.strip().lower()

    m = _NEW_SNAPSHOT_RE.match(version)
    if m:
        return (int(m.group(1)), int(m.group(2)), 998)

    m = _OLD_SNAPSHOT_RE.match(version)
    if m:
        return (int(m.group(1)), 999, 999)

    version = re.sub(r'[-–—].*$', '', version).strip()
    parts = version.split(".")
    nums = []
    for p in parts:
        m = re.match(r'(\d+)', p)
        nums.append(int(m.group(1)) if m else 0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def _min_java_for_mc(mc_version: str) -> int:
    ver = _parse_mc_version(mc_version)
    yy, minor, patch = ver
    is_snapshot = (patch >= 998)

    if yy >= 25:
        return max(25, yy - 1)

    if is_snapshot and yy < 25:
        if yy >= 22:
            return 17
        if yy >= 21:
            return 16
        return 8

    if ver >= (1, 22, 0):
        return 25
    if ver >= (1, 21, 4):
        return 25
    if ver >= (1, 20, 5):
        return 21
    if ver >= (1, 18, 0):
        return 17
    if ver >= (1, 17, 0):
        return 16
    return 8


# ──────────────────────────────────────────────
# Version string parser
# ──────────────────────────────────────────────

_JAVA_VERSION_PATTERNS = [
    re.compile(r'(?:openjdk|java)\s+version\s+"(\d+)\.(\d+)\.(\d+)[_\d]*[+]?(\S*)?"', re.IGNORECASE),
    re.compile(r'(?:openjdk|java)\s+version\s+"1\.(\d+)\.\d+[_\d]*"', re.IGNORECASE),
    re.compile(r'openjdk\s+version\s+"(\d+)\.(\d+)\.(\d+)"', re.IGNORECASE),
]


def _parse_java_version(version_output: str) -> Optional[tuple]:
    for pattern in _JAVA_VERSION_PATTERNS:
        m = pattern.search(version_output)
        if m:
            groups = m.groups()
            if len(groups) == 1:
                return (int(groups[0]), 0, 0, "")
            if len(groups) >= 3:
                major = int(groups[0])
                minor = int(groups[1])
                patch = int(groups[2])
                build = groups[3] if len(groups) > 3 and groups[3] else ""
                return (major, minor, patch, build)
    return None


# ──────────────────────────────────────────────
# Platform-agnostic Java info extractor
# ──────────────────────────────────────────────

def _get_java_info(path: str, source: str) -> Optional[JavaRuntime]:
    try:
        result = subprocess.run(
            [path, "-version"],
            capture_output=True,
            text=True,
            timeout=10
        )
        output = result.stderr or result.stdout
        version_info = _parse_java_version(output)
        if not version_info:
            return None

        major, minor, patch, build = version_info

        home = os.path.dirname(os.path.dirname(path))
        try:
            home_result = subprocess.run(
                [path, "-XshowSettings:properties", "-version"],
                capture_output=True, text=True, timeout=5
            )
            props = (home_result.stderr or home_result.stdout)
            jh_match = re.search(r'java\.home\s*=\s*(\S+)', props)
            if jh_match:
                home = jh_match.group(1)
        except Exception:
            pass

        is_jre = True
        jdk_indicators = [
            os.path.join(home, "bin", "javac"),
            os.path.join(home, "bin", "javac.exe"),
            os.path.join(home, "lib", "tools.jar"),
            os.path.join(home, "lib", "ct.sym"),
        ]
        if any(os.path.exists(p) for p in jdk_indicators):
            is_jre = False

        arch = _detect_arch(path, output)

        system = platform.system().lower()
        if system == "windows":
            os_name = "windows"
        elif system == "darwin":
            os_name = "mac"
        else:
            os_name = "linux"

        return JavaRuntime(
            path=path,
            home=home,
            major_version=major,
            version_str=f"{major}.{minor}.{patch}" + (f"+{build}" if build else ""),
            is_jre=is_jre,
            arch=arch,
            os_name=os_name,
            source=source,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError, OSError):
        return None


def _detect_arch(path: str, version_output: str) -> str:
    if "aarch64" in version_output or "arm64" in version_output:
        return "aarch64"
    if "amd64" in version_output or "x86_64" in version_output or "x64" in version_output:
        return "x64"
    if "i386" in version_output or "x86" in version_output or "32-Bit" in version_output:
        return "x86"

    try:
        if platform.system() == "Windows":
            path_lower = path.lower()
            if "aarch64" in path_lower or "arm64" in path_lower:
                return "aarch64"
            return "x64"
        elif platform.system() == "Darwin":
            result = subprocess.run(["file", path], capture_output=True, text=True, timeout=5)
            if "arm64" in result.stdout or "aarch64" in result.stdout:
                return "aarch64"
            return "x64"
        else:
            result = subprocess.run(["file", path], capture_output=True, text=True, timeout=5)
            if "aarch64" in result.stdout or "ARM aarch64" in result.stdout:
                return "aarch64"
            if "x86-64" in result.stdout:
                return "x64"
            if "80386" in result.stdout or "32-bit" in result.stdout:
                return "x86"
            return "x64"
    except Exception:
        return "x64"


# ─── Windows ─────────────────────────────────

def _scan_windows() -> List[JavaRuntime]:
    found = []
    import winreg

    registry_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Eclipse Foundation\JDK"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Eclipse Foundation\JRE"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Eclipse Adoptium\JDK"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Eclipse Adoptium\JRE"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\JDK"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\JRE"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\Java Development Kit"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\JavaSoft\Java Runtime Environment"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\JavaSoft\JDK"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\JavaSoft\JRE"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Eclipse Foundation\JDK"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Eclipse Foundation\JRE"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\GraalVM"),
    ]

    def _enum_reg_versions(hive, key_path):
        results = []
        try:
            key = winreg.OpenKey(hive, key_path, 0, winreg.KEY_READ)
            try:
                i = 0
                while True:
                    try:
                        subkey_name = winreg.EnumKey(key, i)
                        subkey_path = f"{key_path}\\{subkey_name}"
                        try:
                            sk = winreg.OpenKey(hive, subkey_path, 0, winreg.KEY_READ)
                            try:
                                java_home, _ = winreg.QueryValueEx(sk, "JavaHome")
                                results.append((subkey_name, java_home))
                            except FileNotFoundError:
                                pass
                            finally:
                                winreg.CloseKey(sk)
                        except OSError:
                            pass
                        i += 1
                    except OSError:
                        break
            finally:
                winreg.CloseKey(key)
        except (FileNotFoundError, OSError):
            pass
        return results

    seen_homes = set()

    for hive, key_path in registry_paths:
        for ver_name, java_home in _enum_reg_versions(hive, key_path):
            if java_home in seen_homes:
                continue
            seen_homes.add(java_home)
            java_exe = os.path.join(java_home, "bin", "java.exe")
            if os.path.exists(java_exe):
                info = _get_java_info(java_exe, f"registry:{key_path}")
                if info:
                    found.append(info)

    path_java = _find_on_path()
    if path_java and path_java not in seen_homes:
        java_exe = os.path.join(path_java, "bin", "java.exe") if os.path.isdir(path_java) else path_java
        if os.path.isfile(java_exe):
            info = _get_java_info(java_exe, "PATH")
            if info and info.home not in seen_homes:
                seen_homes.add(info.home)
                found.append(info)

    common_dirs = [
        os.environ.get("ProgramFiles", r"C:\Program Files"),
        os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
        os.environ.get("LOCALAPPDATA", ""),
    ]
    for base in common_dirs:
        if not base:
            continue
        for vendor in ["Java", "Eclipse Adoptium", "Eclipse Foundation", "GraalVM",
                        "Amazon Corretto", "Microsoft", "LibericaJDK", "Zulu"]:
            vendor_dir = os.path.join(base, vendor)
            if not os.path.isdir(vendor_dir):
                continue
            for entry in os.listdir(vendor_dir):
                jdk_dir = os.path.join(vendor_dir, entry)
                if not os.path.isdir(jdk_dir) or jdk_dir in seen_homes:
                    continue
                java_exe = os.path.join(jdk_dir, "bin", "java.exe")
                if os.path.exists(java_exe):
                    info = _get_java_info(java_exe, "filesystem")
                    if info:
                        seen_homes.add(info.home)
                        found.append(info)

    return found


# ─── macOS ───────────────────────────────────

def _scan_macos() -> List[JavaRuntime]:
    found = []
    seen_homes = set()

    try:
        result = subprocess.run(
            ["/usr/libexec/java_home", "-V"],
            capture_output=True, text=True, timeout=10
        )
        output = result.stderr or result.stdout
        for line in output.split("\n"):
            line = line.strip()
            m = re.search(r'(\d+(?:\.\d+)*)\s*\((\w+)\)\s*"[^"]*"\s*-\s*"[^"]*"\s*(/\S+)', line)
            if m:
                java_home = m.group(3)
                if java_home in seen_homes:
                    continue
                java_exe = os.path.join(java_home, "bin", "java")
                if os.path.exists(java_exe):
                    info = _get_java_info(java_exe, "java_home")
                    if info:
                        seen_homes.add(info.home)
                        found.append(info)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    jvm_dir = "/Library/Java/JavaVirtualMachines"
    if os.path.isdir(jvm_dir):
        for entry in os.listdir(jvm_dir):
            jdk_home = os.path.join(jvm_dir, entry, "Contents", "Home")
            if not os.path.isdir(jdk_home) or jdk_home in seen_homes:
                continue
            java_exe = os.path.join(jdk_home, "bin", "java")
            if os.path.exists(java_exe):
                info = _get_java_info(java_exe, "JVM directory")
                if info:
                    seen_homes.add(info.home)
                    found.append(info)

    user_paths = [
        os.path.expanduser("~/.sdkman/candidates/java"),
        "/opt/homebrew/opt",
        "/usr/local/opt",
        os.path.expanduser("~/Library/Java/JavaVirtualMachines"),
    ]
    for base in user_paths:
        if not os.path.isdir(base):
            continue
        for entry in os.listdir(base):
            candidate = os.path.join(base, entry)
            if not os.path.isdir(candidate) or entry == "current":
                continue
            java_exe = os.path.join(candidate, "bin", "java")
            if os.path.exists(java_exe) and candidate not in seen_homes:
                info = _get_java_info(java_exe, f"user:{base}")
                if info:
                    seen_homes.add(info.home)
                    found.append(info)

    path_java = _find_on_path()
    if path_java and path_java not in seen_homes:
        java_exe = os.path.join(path_java, "bin", "java") if os.path.isdir(path_java) else path_java
        if os.path.isfile(java_exe):
            info = _get_java_info(java_exe, "PATH")
            if info and info.home not in seen_homes:
                seen_homes.add(info.home)
                found.append(info)

    return found


# ─── Linux ───────────────────────────────────

def _scan_linux() -> List[JavaRuntime]:
    found = []
    seen_homes = set()

    try:
        result = subprocess.run(
            ["update-alternatives", "--list", "java"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            java_exe = line.strip()
            if java_exe and os.path.exists(java_exe):
                info = _get_java_info(java_exe, "update-alternatives")
                if info and info.home not in seen_homes:
                    seen_homes.add(info.home)
                    found.append(info)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    for jvm_base in ["/usr/lib/jvm", "/usr/lib64/jvm", "/usr/java", "/opt/java"]:
        if not os.path.isdir(jvm_base):
            continue
        for entry in os.listdir(jvm_base):
            jdk_home = os.path.join(jvm_base, entry)
            if not os.path.isdir(jdk_home) or jdk_home in seen_homes:
                continue
            java_exe = os.path.join(jdk_home, "bin", "java")
            if os.path.exists(java_exe):
                info = _get_java_info(java_exe, "jvm directory")
                if info:
                    seen_homes.add(info.home)
                    found.append(info)

    for sdkman_base in [
        os.path.expanduser("~/.sdkman/candidates/java"),
        "/root/.sdkman/candidates/java",
    ]:
        if not os.path.isdir(sdkman_base):
            continue
        for entry in os.listdir(sdkman_base):
            if entry == "current":
                continue
            jdk_home = os.path.join(sdkman_base, entry)
            if not os.path.isdir(jdk_home) or jdk_home in seen_homes:
                continue
            java_exe = os.path.join(jdk_home, "bin", "java")
            if os.path.exists(java_exe):
                info = _get_java_info(java_exe, "sdkman")
                if info:
                    seen_homes.add(info.home)
                    found.append(info)

    extra_paths = [
        "/app/jdk",
        "/snap/openjdk/current",
        os.path.expanduser("~/.local/share/java"),
    ]
    for base in extra_paths:
        if base == "/nix/store":
            if os.path.isdir(base):
                for entry in os.listdir(base):
                    if "jdk" in entry.lower() or "jre" in entry.lower() or "java" in entry.lower():
                        jdk_home = os.path.join(base, entry)
                        java_exe = os.path.join(jdk_home, "bin", "java")
                        if os.path.exists(java_exe) and jdk_home not in seen_homes:
                            info = _get_java_info(java_exe, "nix")
                            if info:
                                seen_homes.add(info.home)
                                found.append(info)
            continue
        if not os.path.isdir(base):
            continue
        for entry in os.listdir(base):
            candidate = os.path.join(base, entry)
            if not os.path.isdir(candidate) or candidate in seen_homes:
                continue
            java_exe = os.path.join(candidate, "bin", "java")
            if os.path.exists(java_exe):
                info = _get_java_info(java_exe, f"extra:{base}")
                if info:
                    seen_homes.add(info.home)
                    found.append(info)

    path_java = _find_on_path()
    if path_java and path_java not in seen_homes:
        java_exe = os.path.join(path_java, "bin", "java") if os.path.isdir(path_java) else path_java
        if os.path.isfile(java_exe):
            info = _get_java_info(java_exe, "PATH")
            if info and info.home not in seen_homes:
                seen_homes.add(info.home)
                found.append(info)

    return found


# ─── Shared: PATH scanner ────────────────────

def _find_on_path() -> Optional[str]:
    system = platform.system()
    java_name = "java.exe" if system == "Windows" else "java"

    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    seen = set()
    for p in path_dirs:
        if not p:
            continue
        java_path = os.path.join(p, java_name)
        if os.path.isfile(java_path) and java_path not in seen:
            seen.add(java_path)
            return java_path
    return None


# ─── JAVA_HOME environment variable ─────────

def _scan_java_home() -> List[JavaRuntime]:
    java_home = os.environ.get("JAVA_HOME")
    if not java_home:
        return []

    system = platform.system()
    java_name = "java.exe" if system == "Windows" else "java"
    java_exe = os.path.join(java_home, "bin", java_name)

    if os.path.exists(java_exe):
        info = _get_java_info(java_exe, "JAVA_HOME")
        return [info] if info else []
    return []


# ─── Minecraft runtime directory scanner ─────

def _scan_minecraft_runtime(minecraft_dir: str) -> List[JavaRuntime]:
    found = []
    runtime_dir = os.path.join(minecraft_dir, "runtime")
    if not os.path.isdir(runtime_dir):
        return found

    system = platform.system()
    java_name = "java.exe" if system == "Windows" else "java"

    for entry in os.listdir(runtime_dir):
        jvm_dir = os.path.join(runtime_dir, entry)
        if not os.path.isdir(jvm_dir):
            continue

        for root, dirs, files in os.walk(jvm_dir):
            if java_name in files:
                java_exe = os.path.join(root, java_name)
                info = _get_java_info(java_exe, "minecraft_runtime")
                if info:
                    found.append(info)
                break

    return found


# ──────────────────────────────────────────────
# Main scanner entry point
# ──────────────────────────────────────────────

def scan_all(minecraft_dir: Optional[str] = None) -> List[JavaRuntime]:
    system = platform.system()
    found = []

    found.extend(_scan_java_home())

    if system == "Windows":
        found.extend(_scan_windows())
    elif system == "Darwin":
        found.extend(_scan_macos())
    else:
        found.extend(_scan_linux())

    if minecraft_dir:
        found.extend(_scan_minecraft_runtime(minecraft_dir))

    seen = set()
    deduped = []
    for java in found:
        if java.home not in seen:
            seen.add(java.home)
            deduped.append(java)

    deduped.sort(key=lambda j: j.major_version)
    return deduped


# ──────────────────────────────────────────────
# MC version matching
# ──────────────────────────────────────────────

def recommend_for_mc(javas: List[JavaRuntime], mc_version: str) -> Optional[JavaRuntime]:
    min_java = _min_java_for_mc(mc_version)

    exact = [j for j in javas if j.major_version == min_java]
    if exact:
        return exact[0]

    near = [j for j in javas if min_java < j.major_version <= min_java + 4]
    if near:
        return min(near, key=lambda j: j.major_version)

    return None


def get_java_summary(javas: List[JavaRuntime]) -> List[Dict]:
    return [
        {
            "path": j.path,
            "home": j.home,
            "major_version": j.major_version,
            "version_str": j.version_str,
            "is_jre": j.is_jre,
            "arch": j.arch,
            "os": j.os_name,
            "source": j.source,
        }
        for j in javas
    ]
