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
from launcher.bedrock.env import install_game_input, is_game_input_installed, repair_environment

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


def launch_gdk(
    version_dir: Path,
    args: str = "",
    notify: Optional[Callable[[str], None]] = None,
) -> subprocess.Popen:
    """启动 GDK 版本

    首次启动自动安装包内 GameInput 运行时（需要 UAC）
    """
    game_exe = version_dir / GDK_EXE
    if not game_exe.exists():
        raise BedrockLaunchError(f"未找到游戏主程序: {game_exe}（GDK 包可能未完整解包）")

    # 首次启动安装 GameInput（BedrockBoot 逻辑）
    if not is_game_input_installed():
        msi = version_dir / "Installers" / "GameInputRedist.msi"
        if not msi.exists():
            msi = next(version_dir.rglob("GameInputRedist.msi"), None)
        if msi is not None:
            if notify:
                notify("正在安装 GameInput 运行时（需要 UAC 确认）...")
            ok, err = install_game_input(msi)
            if not ok:
                raise BedrockLaunchError(err)
        else:
            logger.warning("未找到 GameInputRedist.msi，跳过 GameInput 安装")

    logger.info(f"启动 GDK 游戏: {game_exe} args={args!r}")
    if args:
        # 与 .NET ProcessStartInfo.Arguments 一致：整串附加到可执行文件后，由 Windows 解析
        cmdline = f'"{game_exe}" {args}'
        proc = subprocess.Popen(cmdline, cwd=str(version_dir), shell=True)
    else:
        proc = subprocess.Popen([str(game_exe)], cwd=str(version_dir))
    return proc


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
