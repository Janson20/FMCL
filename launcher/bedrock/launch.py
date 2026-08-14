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
    install_game_input,
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

    game_exe = version_dir / GDK_EXE
    if not game_exe.exists():
        raise BedrockLaunchError(f"未找到游戏主程序: {game_exe}（GDK 包可能未完整解压）")

    # 解压版 exe 与官方构建哈希一致（mcappx 同源），可直接启动。
    # 历史崩溃（0x4ab8027）根因是解压器对 0 字节段不推进页偏移，
    # 导致 MGE 标记文件之后的全部文件数据错位（见 xvd.py），
    # 修复后解压版可稳定运行。若系统存在官方版目录仍优先使用（兜底）。
    try:
        official_dir = find_official_minecraft_dir()
        if official_dir is not None and (official_dir / GDK_EXE).exists():
            game_exe = official_dir / GDK_EXE
            logger.info(f"使用官方版游戏 exe: {game_exe}")
            if notify:
                notify("检测到官方版 Minecraft，使用官方构建启动...")
        else:
            logger.info("未找到官方版 Minecraft（商店版/Xbox 版），使用解压版 exe 启动（已修复 0 字节段错位，可稳定运行）")
    except Exception as e:
        logger.warning(f"查找官方版游戏失败（继续使用解压版）: {e}")

    # 首次启动安装 GameInput（对齐 BedrockBoot：安装失败不阻断启动）
    if not is_game_input_installed():
        msi = version_dir / "Installers" / "GameInputRedist.msi"
        if not msi.exists():
            msi = next(version_dir.rglob("GameInputRedist.msi"), None)
        if msi is not None:
            if notify:
                notify("正在安装 GameInput 运行时（需要 UAC 确认）...")
            ok, err = install_game_input(msi)
            if not ok:
                logger.warning(f"GameInput 安装失败，继续启动游戏: {err}")
        else:
            logger.warning("未找到 GameInputRedist.msi，跳过 GameInput 安装")

    logger.info(f"启动 GDK 游戏: {game_exe} args={args!r}")
    if args:
        # 与 .NET ProcessStartInfo.Arguments 一致：整串附加到可执行文件后，由 Windows 解析
        cmdline = f'"{game_exe}" {args}'
        proc = subprocess.Popen(cmdline, cwd=str(version_dir), shell=True)
    else:
        proc = subprocess.Popen([str(game_exe)], cwd=str(version_dir))
    return proc.pid


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
            subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False)
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
