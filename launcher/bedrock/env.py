"""基岩版运行环境检测与自动修复

对齐 BedrockLauncher.Core / BedrockBoot：
- Windows 开发者模式（UWP 注册必需，注册表 AllowDevelopmentWithoutDevLicense）
- Gaming Services（UWP 启动必需，缺失时打开微软商店页面）
- VC++ 运行时（x64 原生 + UWP VCLibs）
- GameInput（GDK 首次启动必需，msiexec 安装包内 MSI）
"""

import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional, Tuple

from logzero import logger

# Gaming Services 的微软商店产品 ID（BedrockBoot 使用同一页面）
GAMING_SERVICES_STORE_URL = "ms-windows-store://pdp/?ProductId=9MWPM2CQNLHN"
GAMING_SERVICES_PACKAGE = "Microsoft.GamingServices"

VC_RUNTIME_URL = "https://aka.ms/vc14/vc_redist.x64.exe"

_ENV_LOCK = threading.Lock()


def _run_powershell(command: str, timeout: int = 60, run_as_admin: bool = False) -> subprocess.CompletedProcess:
    """执行 PowerShell 命令"""
    if run_as_admin:
        full = f"Start-Process powershell -Verb RunAs -Wait -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-Command', '{command}')"
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", full],
            capture_output=True,
            text=True,
            timeout=timeout + 60,
        )
        return proc
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ─── 开发者模式 ──────────────────────────────────────────────


def is_developer_mode_enabled() -> bool:
    """检查 Windows 开发者模式是否开启"""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock"
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AllowDevelopmentWithoutDevLicense")
            return value != 0
    except (OSError, FileNotFoundError):
        return False


def enable_developer_mode() -> Tuple[bool, str]:
    """尝试自动开启开发者模式（需要 UAC 提权）"""
    logger.info("尝试自动开启 Windows 开发者模式...")
    try:
        proc = _run_powershell(
            "Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock' "
            "-Name 'AllowDevelopmentWithoutDevLicense' -Value 1 -Type DWord",
            run_as_admin=True,
        )
        if proc.returncode == 0 and is_developer_mode_enabled():
            logger.info("开发者模式已开启")
            return True, ""
        return False, "自动开启失败（可能被用户取消或权限不足）"
    except Exception as e:
        return False, f"自动开启失败: {e}"


def ensure_developer_mode(notify: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
    """确保开发者模式开启，缺失时自动修复"""
    if is_developer_mode_enabled():
        return True, ""
    if notify:
        notify("开发者模式未开启，正在尝试自动开启...")
    return enable_developer_mode()


# ─── Gaming Services ─────────────────────────────────────────


def is_gaming_services_installed() -> bool:
    """检查 Gaming Services 是否安装"""
    proc = _run_powershell(f"Get-AppxPackage {GAMING_SERVICES_PACKAGE}", timeout=30)
    return proc.returncode == 0 and GAMING_SERVICES_PACKAGE.lower() in proc.stdout.lower()


def install_gaming_services() -> None:
    """打开微软商店 Gaming Services 页面引导用户安装"""
    subprocess.Popen(["cmd", "/c", "start", "", GAMING_SERVICES_STORE_URL], shell=False)


# ─── VC++ 运行时 ─────────────────────────────────────────────


def _has_vc_win32() -> bool:
    """检查原生 VC++ 2015-2022 x64 运行时"""
    try:
        import winreg

        for sub in (r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64", r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, sub) as key:
                    winreg.QueryValueEx(key, "Installed")
                    return True
            except OSError:
                continue
        return False
    except Exception:
        return False


def _has_vc_uwp() -> bool:
    """检查 UWP VCLibs 运行时"""
    proc = _run_powershell("Get-AppxPackage -Name 'Microsoft.VCLibs*'", timeout=30)
    return proc.returncode == 0 and "Microsoft.VCLibs" in proc.stdout


def is_vc_runtime_installed() -> Tuple[bool, bool]:
    """返回 (UWP VCLibs 是否安装, 原生 VC 是否安装)"""
    return _has_vc_uwp(), _has_vc_win32()


def install_vc_runtime(progress_cb: Optional[Callable[[str], None]] = None) -> bool:
    """下载并静默安装 VC++ x64 运行时（需要 UAC）"""
    import requests

    from launcher.bedrock.source import USER_AGENT

    if progress_cb:
        progress_cb("正在下载 VC++ 运行时...")
    dest = Path.home() / "FMCL_bedrock_vc_redist.x64.exe"
    try:
        resp = requests.get(VC_RUNTIME_URL, headers={"User-Agent": USER_AGENT}, timeout=60, stream=True)
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
    except Exception as e:
        logger.error(f"VC 运行时下载失败: {e}")
        return False
    if progress_cb:
        progress_cb("正在安装 VC++ 运行时（可能需要 UAC 确认）...")
    try:
        proc = subprocess.run(
            [str(dest), "/install", "/quiet", "/norestart"],
            capture_output=True,
            timeout=600,
        )
        return proc.returncode == 0
    except Exception as e:
        logger.error(f"VC 运行时安装失败: {e}")
        return False
    finally:
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass


# ─── GameInput ──────────────────────────────────────────────


def is_game_input_installed() -> bool:
    """检查 GameInput 运行时（MSI 产品 64d0ccb1-329e-d507-0886-47e53d59ae21）

    通过注册表查询，避免 Win32_Product 慢查询
    """
    try:
        import winreg

        # MSI 注册表格式：GUID 前三组字节反转、去大括号、全大写
        msi_key = r"SOFTWARE\Classes\Installer\Products\B1CCD0649E3207D5088647E53D59AE21"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, msi_key) as key:
            winreg.QueryValueEx(key, "ProductName")
            return True
    except OSError:
        return False
    except Exception:
        return False


def install_game_input(msi_path: Path) -> Tuple[bool, str]:
    """通过 msiexec 静默安装 GameInput 运行时（需要 UAC）"""
    if not msi_path.exists():
        return False, f"未找到 GameInput 安装包: {msi_path}"
    logger.info(f"安装 GameInput: {msi_path}")
    try:
        proc = subprocess.run(
            ["msiexec.exe", "/i", str(msi_path), "/qb"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        if proc.returncode == 0:
            logger.info("GameInput 安装成功")
            return True, ""
        return False, f"GameInput 安装失败，错误码 {proc.returncode}"
    except Exception as e:
        return False, f"GameInput 安装失败: {e}"


# ─── 统一检测 ────────────────────────────────────────────────


class EnvReport:
    """环境检测结果"""

    def __init__(self) -> None:
        self.developer_mode = False
        self.gaming_services = False
        self.vc_uwp = False
        self.vc_win32 = False
        self.game_input = False

    def __str__(self) -> str:
        return (
            f"EnvReport(developer_mode={self.developer_mode}, gaming_services={self.gaming_services}, "
            f"vc_uwp={self.vc_uwp}, vc_win32={self.vc_win32}, game_input={self.game_input})"
        )


def check_environment() -> EnvReport:
    """全面检测基岩版运行环境（线程安全，可并发执行检测）"""
    report = EnvReport()
    results: dict = {}

    def _check(name: str, func: Callable[[], bool]) -> None:
        try:
            results[name] = func()
        except Exception as e:
            logger.warning(f"环境检测 {name} 失败: {e}")
            results[name] = False

    threads = [
        threading.Thread(target=_check, args=("dev", is_developer_mode_enabled)),
        threading.Thread(target=_check, args=("gs", is_gaming_services_installed)),
        threading.Thread(target=_check, args=("vc", lambda: is_vc_runtime_installed())),
        threading.Thread(target=_check, args=("gi", is_game_input_installed)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=90)

    report.developer_mode = bool(results.get("dev", False))
    report.gaming_services = bool(results.get("gs", False))
    vc_result = results.get("vc", (False, False))
    if isinstance(vc_result, tuple) and len(vc_result) == 2:
        report.vc_uwp = bool(vc_result[0])
        report.vc_win32 = bool(vc_result[1])
    report.game_input = bool(results.get("gi", False))
    return report


def repair_environment(
    report: Optional[EnvReport] = None,
    need_uwp: bool = True,
    notify: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    """自动修复缺失环境（互斥执行，避免并发冲突）

    Args:
        report: 检测结果（None 时重新检测）
        need_uwp: 是否需要 UWP 相关环境（开发者模式、GameService）
        notify: 状态回调
    """
    with _ENV_LOCK:
        report = report or check_environment()
        if need_uwp and not report.developer_mode:
            ok, msg = ensure_developer_mode(notify)
            if not ok:
                return False, f"需要开发者模式: {msg}"
        if need_uwp and not report.gaming_services:
            if notify:
                notify("检测到 Gaming Services 缺失，正在打开微软商店...")
            install_gaming_services()
            return False, "已打开微软商店，请安装 Gaming Services 后重试"
        if not report.vc_uwp or not report.vc_win32:
            if notify:
                notify("检测到 VC++ 运行时缺失，正在自动安装...")
            if not install_vc_runtime(notify):
                return False, "VC++ 运行时安装失败，请手动安装后重试"
        return True, ""
