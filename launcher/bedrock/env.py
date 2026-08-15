"""基岩版运行环境检测与自动修复

对齐 BedrockLauncher.Core / BedrockBoot：
- Windows 开发者模式（UWP 注册必需，注册表 AllowDevelopmentWithoutDevLicense）
- Gaming Services（UWP 启动必需，缺失时优先 winget 自动安装，失败回退微软商店页面）
- VC++ 运行时（x64 原生 + UWP VCLibs）
- GameInput（GDK 必需，msiexec 安装包内 MSI，版本安装时自动补齐）
- 官方 exe 兜底（解压版 exe 与官方同源；存在官方版时优先使用/校验替换）
"""

import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from logzero import logger

# Gaming Services 的微软商店产品 ID（BedrockBoot 使用同一页面）
GAMING_SERVICES_STORE_URL = "ms-windows-store://pdp/?ProductId=9MWPM2CQNLHN"
GAMING_SERVICES_PACKAGE = "Microsoft.GamingServices"
# winget msstore 源仅接受商店产品 ID 作为包 ID
GAMING_SERVICES_WINGET_ID = "9MWPM2CQNLHN"

VC_RUNTIME_URL = "https://aka.ms/vc14/vc_redist.x64.exe"

_ENV_LOCK = threading.Lock()


def _run_powershell(command: str, timeout: int = 60, run_as_admin: bool = False) -> subprocess.CompletedProcess:
    """执行 PowerShell 命令（errors=replace 防止 GBK 解码崩溃）"""
    if run_as_admin:
        full = f"Start-Process powershell -Verb RunAs -Wait -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-Command', '{command}')"
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", full],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout + 60,
        )
        return proc
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        errors="replace",
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


def _is_appx_installed(package_name: str) -> bool:
    """检查指定 AppX 包是否已安装"""
    proc = _run_powershell(f"Get-AppxPackage -Name '{package_name}'", timeout=30)
    return proc.returncode == 0 and package_name.lower() in proc.stdout.lower()


def is_gaming_services_installed() -> bool:
    """检查 Gaming Services 是否安装"""
    return _is_appx_installed(GAMING_SERVICES_PACKAGE)


def _run_winget(args: List[str], timeout: int = 600) -> Tuple[int, str]:
    """执行 winget（隐藏窗口），返回 (返回码, 输出文本)

    返回码约定：winget 不可用时返回 -1，超时返回 -2，其他异常返回 -3。
    """
    try:
        proc = subprocess.run(
            ["winget.exe", *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except FileNotFoundError:
        return -1, "winget 未安装（缺少 应用安装程序）"
    except subprocess.TimeoutExpired:
        return -2, f"winget 执行超时（{timeout} 秒）"
    except Exception as e:
        return -3, str(e)
    return proc.returncode, (proc.stdout + "\n" + proc.stderr).strip()


def install_gaming_services(
    notify: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    """通过 winget（微软商店源）自动安装 Gaming Services

    使用商店产品 ID（9MWPM2CQNLHN）作为 winget 包 ID；
    安装成功后轮询 Get-AppxPackage 确认注册生效。

    Returns:
        (是否成功, 说明)；失败时返回失败原因，由调用方决定兜底策略。
    """
    if is_gaming_services_installed():
        return True, "Gaming Services 已安装"
    if notify:
        notify("检测到 Gaming Services 未安装，正在通过 winget 自动安装...")
    code, output = _run_winget(
        [
            "install",
            "--id", GAMING_SERVICES_WINGET_ID,
            "--source", "msstore",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--silent",
            "--disable-interactivity",
        ],
        timeout=600,
    )
    if code == 0:
        # winget 返回成功但包可能尚未注册，轮询确认
        for _ in range(6):
            if is_gaming_services_installed():
                logger.info("Gaming Services 安装成功")
                return True, ""
            time.sleep(2)
        return True, "winget 安装已提交，包注册可能稍有延迟"
    # 已安装（winget 返回 0x8A150011）视为成功
    if is_gaming_services_installed():
        return True, "Gaming Services 已安装"
    detail = (output or f"winget 返回码 {code}").splitlines()[-3:]
    logger.warning(f"winget 安装 Gaming Services 失败: {' | '.join(detail)}")
    return False, f"winget 自动安装失败: {' | '.join(detail)[:300]}"


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


MSI_OLE_MAGIC = b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"


def is_valid_msi(path: Path) -> bool:
    """校验文件是否为有效 MSI（OLE 复合文档头）"""
    try:
        with open(path, "rb") as f:
            return f.read(8) == MSI_OLE_MAGIC
    except OSError:
        return False


def install_game_input(msi_path: Path) -> Tuple[bool, str]:
    """通过 msiexec 静默安装 GameInput 运行时（需要 UAC 提权，对齐 BedrockBoot runas）

    注意：部分第三方重打包包（如 mcappx）中 GameInputRedist.msi 内容被
    错配为其他文件（Realms Plus 文本），此类包直接跳过安装。
    """
    if not msi_path.exists():
        return False, f"未找到 GameInput 安装包: {msi_path}"
    if not is_valid_msi(msi_path):
        logger.warning(f"GameInputRedist.msi 内容无效（非 MSI 格式，疑似打包错配），跳过安装: {msi_path}")
        return False, "包内 GameInput 文件无效（可能是第三方打包错配），已跳过安装"
    logger.info(f"安装 GameInput: {msi_path}")
    script = (
        f"$p = Start-Process msiexec.exe -ArgumentList '/i','\"{msi_path}\"','/qb' "
        "-Verb RunAs -Wait -PassThru; exit $p.ExitCode"
    )
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=660,
        )
        if proc.returncode == 0:
            logger.info("GameInput 安装成功")
            return True, ""
        return False, f"GameInput 安装失败，错误码 {proc.returncode}（可能被用户取消或权限不足）"
    except subprocess.TimeoutExpired:
        return False, "GameInput 安装超时（11 分钟）"
    except Exception as e:
        return False, f"GameInput 安装失败: {e}"


# ─── Xbox 登录状态 ─────────────────────────────────────────

XBOX_IDP_PACKAGE = "Microsoft.XboxIdentityProvider_8wekyb3d8bbwe"
XBOX_APP_PACKAGE = "Microsoft.XboxApp_8wekyb3d8bbwe"
IDENTITY_CRL_PATH = r"Software\Microsoft\IdentityCRL\TokenCache"
IDENTITY_CRL_KEYCACHE = r"Software\Microsoft\IdentityCRL\KeyCache"


def is_xbox_signed_in() -> bool:
    """检查系统是否存在 Xbox 身份（GDK 游戏 XblSignInSilently 可用）

    指标（对齐 BedrockBoot XboxLoginStatusChecker）：
    1. HKCU\\Software\\Microsoft\\IdentityCRL\\TokenCache 含 Xbox/live.com 相关条目
    2. XboxIdentityProvider 的登录缓存目录有数据
    3. IdentityCRL\\KeyCache 存在有效的 Xbox 认证凭据（StrongCredentialKey，
       新 Xbox 应用/GamingApp 登录后写在这里）
    注意：不能检查 XboxApp 的 LocalState（崩溃日志会误判）
    """
    # 1. IdentityCRL TokenCache（BedrockBoot CheckWindowsTokenCache 同款）
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IDENTITY_CRL_PATH) as key:
            sub_count = winreg.QueryInfoKey(key)[0]
            for i in range(sub_count):
                name = winreg.EnumKey(key, i).lower()
                if any(k in name for k in ("xbox", "live.com", "xbl", "xsts")):
                    return True
    except OSError:
        pass
    # 2. XboxIdentityProvider 登录缓存
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        idp = Path(local_appdata) / "Packages" / XBOX_IDP_PACKAGE / "LocalState"
        if idp.exists():
            try:
                for entry in idp.rglob("*"):
                    if entry.is_file() and entry.stat().st_size > 0:
                        return True
            except OSError:
                pass
    # 3. IdentityCRL KeyCache（Xbox 认证凭据，新 Xbox 应用登录后写入）
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, IDENTITY_CRL_KEYCACHE) as key:
            sub_count = winreg.QueryInfoKey(key)[0]
            for i in range(sub_count):
                name = winreg.EnumKey(key, i)
                with winreg.OpenKey(key, name) as sub:
                    try:
                        winreg.QueryValueEx(sub, "StrongCredentialKey")
                        return True
                    except OSError:
                        continue
    except OSError:
        pass
    return False


def open_xbox_app() -> None:
    """打开 Xbox 应用引导用户登录微软账户"""
    subprocess.Popen(
        ["explorer.exe", f"shell:appsFolder\\{XBOX_APP_PACKAGE}!Microsoft.XboxApp"],
        shell=False,
    )


def is_xgameruntime_installed() -> bool:
    """检查 GDK 游戏运行时（xgameruntime.dll）是否可用

    XUserHook 需要加载 xgameruntime.dll 以 hook QueryApiImpl 注入认证。
    该组件由以下任一途径提供（任意一个安装即可）：
    - Microsoft.XboxGamingRuntime 包（随商店 GDK 游戏自动安装）
    - Microsoft.GamingServices 包（Gaming Services，其 DLL 同样部署到 System32）
    最可靠指标：System32 下存在 xgameruntime.dll。
    """
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "xgameruntime.dll"
    if system32.is_file() and system32.stat().st_size > 0:
        return True
    proc = _run_powershell(
        "Get-AppxPackage -Name 'Microsoft.XboxGamingRuntime','Microsoft.GamingServices'", timeout=30
    )
    if proc.returncode == 0 and any(
        name in proc.stdout for name in ("XboxGamingRuntime", "GamingServices")
    ):
        return True
    return False


def open_xgameruntime_store() -> None:
    """打开微软商店相关页面引导用户安装 GDK 游戏运行时"""
    subprocess.Popen(["explorer.exe", "https://www.microsoft.com/store/search?query=XboxGamingRuntime"], shell=False)


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
            ok, msg = install_gaming_services(notify)
            if not ok:
                # 兜底：打开微软商店页面引导用户手动安装
                subprocess.Popen(["cmd", "/c", "start", "", GAMING_SERVICES_STORE_URL], shell=False)
                return False, f"{msg}；已打开微软商店页面，请安装 Gaming Services 后重试"
            if notify:
                notify("Gaming Services 已安装")
        if not report.vc_uwp or not report.vc_win32:
            if notify:
                notify("检测到 VC++ 运行时缺失，正在自动安装...")
            if not install_vc_runtime(notify):
                return False, "VC++ 运行时安装失败，请手动安装后重试"
        return True, ""


# ─── 官方版目录查找（解压版 exe 与官方同源，官方目录作为兜底数据源）──────

MC_PACKAGE_NAMES = ("Microsoft.MinecraftUWP", "Microsoft.MinecraftWindowsBeta")
# Xbox 应用游戏安装目录（可自定义位置，如 D:\XboxGames）
XBOX_GAMES_DIR_NAMES = ("XboxGames",)


def find_official_minecraft_dir() -> Optional[Path]:
    """查找官方版 Minecraft（商店版/Xbox 版）的安装目录

    解压版 exe 与官方构建哈希一致（mcappx 同源），可直接启动；历史崩溃
    （0x4ab8027）根因是解压器对 0 字节段不推进页偏移导致数据错位（.NET
    解压器已修复）。此函数作为兜底：返回官方版游戏根目录（含 Minecraft.Windows.exe），
    找不到返回 None。

    查找顺序：
    1. 已注册 AppX 包（Microsoft.MinecraftUWP / Beta）的 InstallLocation
    2. Xbox 应用游戏安装目录（C:/D:/... 盘根的 XboxGames\\<GUID>\\Content）
    """
    # 1. 已注册 AppX 包
    try:
        for pkg_name in MC_PACKAGE_NAMES:
            proc = _run_powershell(
                f"(Get-AppxPackage -Name '{pkg_name}' | Select-Object -First 1).InstallLocation", timeout=30
            )
            loc = (proc.stdout or "").strip()
            if loc and (Path(loc) / "Minecraft.Windows.exe").exists():
                return Path(loc)
    except Exception as e:
        logger.warning(f"查找商店版 Minecraft 失败: {e}")

    # 2. Xbox 应用游戏目录（各盘根 XboxGames\\<GUID>\\Content）
    try:
        for drive in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            root = Path(f"{drive}:\\")
            if not root.exists():
                continue
            for games_dir_name in XBOX_GAMES_DIR_NAMES:
                games_dir = root / games_dir_name
                if not games_dir.is_dir():
                    continue
                for entry in games_dir.iterdir():
                    content = entry / "Content"
                    if (content / "Minecraft.Windows.exe").exists():
                        return content
    except Exception as e:
        logger.warning(f"查找 Xbox 游戏目录失败: {e}")

    return None


def ensure_official_gdk_exe(
    version_dir: Path,
    notify: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    """确保 GDK 解压目录的 Minecraft.Windows.exe 为官方构建

    解压版 exe 与官方构建哈希一致（mcappx 同源），此函数仅作校验兜底：
    若系统存在官方版（商店版/Xbox 版）且 exe 大小不一致，则复制替换。

    Returns:
        (是否成功, 说明)
    """
    game_exe = version_dir / "Minecraft.Windows.exe"
    if not game_exe.exists():
        return False, "缺少 Minecraft.Windows.exe"

    official_dir = find_official_minecraft_dir()
    if official_dir is None:
        return False, "未找到官方版 Minecraft（商店版/Xbox 版），解压版 exe 可能不稳定"

    official_exe = official_dir / "Minecraft.Windows.exe"
    if not official_exe.exists():
        return False, f"官方版目录缺少 exe: {official_exe}"

    # 已是最新官方 exe 则跳过
    try:
        if game_exe.stat().st_size == official_exe.stat().st_size:
            return True, "解压版 exe 已与官方版一致"
    except OSError:
        pass

    if notify:
        notify("检测到官方版 Minecraft，正在复制官方 exe 修复启动...")
    try:
        # 官方 exe 在 WindowsApps/Xbox 目录受保护，需提权复制
        import shutil

        proc = _run_powershell(
            f"Copy-Item '{official_exe}' '{game_exe}' -Force",
            timeout=120,
            run_as_admin=True,
        )
        if proc.returncode == 0 and game_exe.stat().st_size == official_exe.stat().st_size:
            logger.info(f"已用官方 exe 替换: {official_exe} -> {game_exe}")
            return True, "已用官方 exe 替换"
        # 提权失败则尝试直接复制（解压目录通常可写）
        shutil.copy2(official_exe, game_exe)
        if game_exe.stat().st_size == official_exe.stat().st_size:
            logger.info(f"已用官方 exe 替换（直接复制）: {game_exe}")
            return True, "已用官方 exe 替换"
        return False, "复制官方 exe 失败"
    except Exception as e:
        logger.warning(f"复制官方 exe 失败: {e}")
        return False, f"复制官方 exe 失败: {e}"
