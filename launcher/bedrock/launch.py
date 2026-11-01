"""基岩版游戏启动逻辑

对齐 BedrockBoot EasyLauncher / BedrockLauncher.Core LaunchGameAsync：
- GDK: 直接运行版本目录下的 Minecraft.Windows.exe
- UWP: 确认包已注册（未注册或路径不一致时重新注册），
       通过 shell:appsFolder 激活 + minecraft://launch 协议传参
"""

import subprocess
from pathlib import Path
from typing import Callable, Optional

from logzero import logger

from launcher.bedrock.appx import get_package_install_location, register_appx
from launcher.bedrock.env import (
    find_official_minecraft_dir,
    is_game_input_installed,
    is_xbox_signed_in,
    open_xbox_app,
    repair_environment,
)

PACKAGE_FAMILIES = {
    "release": "Microsoft.MinecraftUWP_8wekyb3d8bbwe",
    "preview": "Microsoft.MinecraftWindowsBeta_8wekyb3d8bbwe",
    "beta": "Microsoft.MinecraftWindowsBeta_8wekyb3d8bbwe",
}
PACKAGE_NAMES = {
    "release": "Microsoft.MinecraftUWP",
    "preview": "Microsoft.MinecraftWindowsBeta",
    "beta": "Microsoft.MinecraftWindowsBeta",
}
APP_ENTRY_IDS = {
    "release": "Microsoft.MinecraftUWP_8wekyb3d8bbwe!App",
    "preview": "Microsoft.MinecraftWindowsBeta_8wekyb3d8bbwe!App",
    "beta": "Microsoft.MinecraftWindowsBeta_8wekyb3d8bbwe!App",
}

GDK_EXE = "Minecraft.Windows.exe"
#: 所选 GDK 版本启动后观察多久；窗口内直接退出视为启动失败，回退已安装 Xbox 版本。
LAUNCH_GRACE_SECONDS = 5.0


class BedrockLaunchError(RuntimeError):
    """基岩版启动错误"""


def _build_uri(args: str) -> str:
    """将启动参数转换为 minecraft://launch? 查询串（对齐 LaunchGameAsync 的 URI 构建）"""
    if not args:
        return "minecraft://launch"
    launch_args = args.strip()
    query = ""
    if launch_args.lower().startswith("minecraft://"):
        q_index = launch_args.find("?")
        if q_index >= 0 and q_index < len(launch_args) - 1:
            query = launch_args[q_index + 1 :]
        else:
            path = launch_args[len("minecraft://") :].strip("/")
            if path:
                query = f"{path}=true"
    elif "=" in launch_args and " " not in launch_args:
        query = launch_args
    elif "=" in launch_args and " " in launch_args:
        pairs = [arg for arg in launch_args.split(" ") if "=" in arg]
        if pairs:
            query = "&".join(pairs)
        else:
            from urllib.parse import quote

            query = f"args={quote(launch_args)}"
    else:
        from urllib.parse import quote

        query = f"args={quote(launch_args)}"
    return f"minecraft://launch?{query}" if query else "minecraft://launch"


def prepare_environment(
    build_type: str,
    notify: Optional[Callable[[str], None]] = None,
) -> None:
    """启动前环境检测与自动修复

    Args:
        build_type: "UWP" 或 "GDK"
    """
    need_uwp = build_type == "UWP"
    ok, msg = repair_environment(need_uwp=need_uwp, notify=notify)
    if not ok:
        raise BedrockLaunchError(msg)


def _launch_gdk_injected(
    version_dir: Path,
    access_token: str,
    args: str = "",
    notify: Optional[Callable[[str], None]] = None,
) -> int:
    """认证注入启动 GDK 版本（BedrockBoot 多用户模式方案）：

    1. 构建 BedrockGdkHelper（.NET 10，首次自动编译）
    2. helper 完成：XUserHook.dll PE 注入 + Xbox 认证链（XBL/XSTS/SISU）+
       挂起启动 + 命名管道传认证
    """
    from launcher.bedrock.env import is_xgameruntime_installed, open_xgameruntime_store
    from launcher.bedrock.native.build_helper import build

    # XUserHook 依赖系统 XboxGamingRuntime（xgameruntime.dll）安装 QueryApiImpl hook
    if not is_xgameruntime_installed():
        open_xgameruntime_store()
        raise BedrockLaunchError(
            "GDK 认证注入需要 xgameruntime.dll 系统组件，当前系统未安装。\n"
            "请任选一种方式安装后重试：\n"
            "1. 在已打开的商店页面搜索 XboxGamingRuntime 并安装\n"
            "2. 安装 Microsoft.GamingServices（Gaming Services）或任意 Xbox/Game Pass 的 GDK 游戏，"
            "系统会自动部署该组件"
        )

    if notify:
        notify("正在构建/准备认证注入组件...")
    try:
        helper = build()
    except RuntimeError as e:
        raise BedrockLaunchError(str(e)) from e

    cmd = [str(helper), str(version_dir), access_token]
    if args:
        cmd.append(args)
    logger.info(f"认证注入启动 GDK: {version_dir.name}")
    if notify:
        notify("正在执行 Xbox 认证并注入游戏...")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    game_pid: Optional[int] = None
    error_lines: list = []
    if proc.stdout:
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            logger.info(f"[GdkHelper] {line}")
            if line.startswith("GAME_PID:"):
                try:
                    game_pid = int(line.split(":", 1)[1])
                except ValueError:
                    pass
            elif line.startswith("ERROR"):
                error_lines.append(line)
    proc.wait(timeout=120)
    if proc.returncode != 0:
        detail = "\n".join(error_lines[-5:]) or f"退出码 {proc.returncode}"
        raise BedrockLaunchError(f"认证注入启动失败: {detail}")
    if game_pid is None:
        raise BedrockLaunchError("认证注入未返回游戏进程 ID")
    logger.info(f"GDK 游戏已启动（注入认证），PID: {game_pid}")
    return game_pid


def _official_launch_candidate() -> Optional[tuple]:
    """已安装 Xbox/商店版的可启动候选；找不到返回 None。

    只在所选版本缺失或启动失败后调用：正常路径不该为了一个兜底去跑
    PowerShell / 扫所有盘。
    """
    try:
        official_dir = find_official_minecraft_dir()
    except Exception as e:  # noqa: BLE001 - 查不到官方版不阻断所选版本
        logger.warning(f"查找官方版游戏失败: {e}")
        return None
    if official_dir is None:
        return None
    official_exe = official_dir / GDK_EXE
    if not official_exe.exists():
        return None
    return official_exe, official_dir, "已安装 Xbox 版本"


def launch_gdk(
    version_dir: Path,
    args: str = "",
    notify: Optional[Callable[[str], None]] = None,
    msa_access_token: str = "",
) -> int:
    """启动 GDK 版本，返回游戏进程 PID

    GDK 版依赖 Xbox 认证（XblSignInSilently）：
    - 系统已有 Xbox 身份（Xbox 应用/Game Bar 登录过）→ 直接启动
    - 否则需提供 msa_access_token → 认证注入启动（BedrockBoot 方案）
    首次启动自动安装包内 GameInput 运行时（需要 UAC）
    """
    if not is_xbox_signed_in():
        if not msa_access_token:
            if notify:
                notify("GDK 版需要 Xbox 登录，正在打开 Xbox 应用...")
            open_xbox_app()
            raise BedrockLaunchError(
                "GDK 版启动需要 Xbox 登录（XblSignInSilently 认证），当前系统未检测到 Xbox 身份。"
                "请任选一种方式：\n"
                "1. 在打开的 Xbox 应用中登录（如闪退请用方式 2/3）\n"
                "2. 按 Win+G 打开 Xbox Game Bar，点击头像登录\n"
                "3. 使用 FMCL 的微软账户登录（启动器内引导）"
            )
        return _launch_gdk_injected(version_dir, msa_access_token, args, notify)

    selected_exe = version_dir / GDK_EXE

    # 用户明确选择的版本优先；只有它缺失/启动失败时才去查并回退已安装 Xbox 版本。
    # 官方目录查找要走 PowerShell/扫盘，正常启动不应为它付这笔时间。
    attempts: list = []
    if selected_exe.exists():
        attempts.append((selected_exe, version_dir, "所选版本"))
    else:
        official = _official_launch_candidate()
        if official is None:
            raise BedrockLaunchError(f"未找到游戏主程序: {selected_exe}（GDK 包可能未完整解压）")
        logger.warning(f"所选版本缺少主程序，回退已安装 Xbox 版本: {selected_exe}")
        attempts.append(official)

    # GameInput 运行时在版本安装时已自动安装；启动时仅做缺失提示，不再跑 MSI
    # （缺失时游戏可能无法启动，但安装时已尽量补齐；这里不阻断启动）
    if not is_game_input_installed():
        if notify:
            notify("GameInput 运行时未安装，GDK 版可能无法启动（可重装版本补齐）...")
        logger.warning("GameInput 未安装，GDK 版可能无法启动")

    errors: list = []
    while attempts:
        game_exe, workdir, label = attempts.pop(0)
        try:
            logger.info(f"启动 {label} GDK 游戏: {game_exe} args={args!r}")
            if args:
                # 与 .NET ProcessStartInfo.Arguments 一致：整串附加到可执行文件后，由 Windows 解析
                cmdline = f'"{game_exe}" {args}'
                proc = subprocess.Popen(
                    cmdline, cwd=str(workdir), shell=True, creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:
                proc = subprocess.Popen(
                    [str(game_exe)], cwd=str(workdir), creationflags=subprocess.CREATE_NO_WINDOW
                )
        except Exception as e:
            errors.append(f"{label}: {e}")
            logger.warning(f"{label} 启动失败: {e}")
        else:
            # 观察窗口内直接退出 = 启动失败（例如 exe 依赖缺失/构建不匹配），回退。
            try:
                early_code = proc.wait(timeout=LAUNCH_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                if label != "所选版本" and notify:
                    notify(f"所选版本启动失败，已回退到{label}启动")
                return proc.pid
            errors.append(f"{label}: 进程立即退出（代码 {early_code}）")
            logger.warning(f"{label} 启动后立即退出，代码 {early_code}")

        if label == "所选版本":
            official = _official_launch_candidate()
            if official is not None and official[0].resolve() != selected_exe.resolve():
                attempts.append(official)
                logger.info("所选版本启动失败，回退到已安装 Xbox 版本")
                if notify:
                    notify("所选版本启动失败，正在回退到已安装 Xbox 版本...")

    raise BedrockLaunchError("GDK 启动失败: " + ("; ".join(errors) or "没有可用版本"))


def launch_uwp(
    version_dir: Path,
    game_type: str,
    args: str = "",
    notify: Optional[Callable[[str], None]] = None,
) -> None:
    """启动 UWP 版本（注册 + 激活）"""
    package_name = PACKAGE_NAMES.get(game_type.lower())
    package_family = PACKAGE_FAMILIES.get(game_type.lower())
    app_entry = APP_ENTRY_IDS.get(game_type.lower())
    if not package_name or not package_family or not app_entry:
        raise BedrockLaunchError(f"未知的游戏类型: {game_type}")

    manifest = version_dir / "AppxManifest.xml"
    if not manifest.exists():
        raise BedrockLaunchError("版本目录中缺少 AppxManifest.xml，请重新安装")

    # 已注册包路径与当前版本目录不一致时重新注册（BedrockBoot 的 twice_launch 逻辑）
    installed_path = get_package_install_location(package_name)
    need_register = installed_path is None or Path(installed_path).resolve() != version_dir.resolve()
    if need_register:
        if notify:
            notify("正在注册游戏包...")
        register_appx(version_dir)

    # 通过 shell:appsFolder 激活（Launcher.LaunchUriAsync 的等价实现）
    if notify:
        notify("正在启动游戏...")
    subprocess.Popen(["explorer.exe", f"shell:appsFolder\\{app_entry}"])

    # 传递启动参数（minecraft://launch 协议）
    if args:
        try:
            uri = _build_uri(args)
            subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False, creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception as e:
            logger.warning(f"传递启动参数失败（不影响启动）: {e}")
    logger.info(f"UWP 游戏已激活: {app_entry} family={package_family}")


def find_game_process(game_type: str) -> Optional[int]:
    """查找正在运行的基岩版游戏进程（返回 PID）"""
    import psutil

    names = ["Minecraft.Windows", "Minecraft.Win10.DX11"]
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            name = (proc.info.get("name") or "").lower()
            for target in names:
                if name.startswith(target.lower()):
                    return proc.info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None
