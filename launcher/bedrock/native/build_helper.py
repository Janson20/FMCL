"""构建 .NET 辅助程序（BedrockGdkHelper / BedrockXvdExtractor）

需要 .NET 10 SDK（dotnet）。
- 源码模式: 产物输出到 launcher/bedrock/native/bin/<项目名>/
- 打包模式: 产物输出到 <数据目录>/local/native-bin/（_MEIPASS 为临时目录，
  进程退出即清空，不能作为产物落盘位置）
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

NATIVE_DIR = Path(__file__).resolve().parent

HELPER_DIR = NATIVE_DIR / "helper"
EXTRACTOR_DIR = NATIVE_DIR / "extractor"
REQUIRED_ASSETS = ("XUserLauncher.Core.dll",)

DOTNET_MIN_VERSION = "10."


def _runtime_root() -> Path:
    """持久运行时根目录：源码模式为 native 目录，打包模式为数据目录/local"""
    if getattr(sys, "frozen", False):
        local_appdata = os.environ.get("LOCALAPPDATA")
        base = Path(local_appdata) / "FMCL" if local_appdata else Path.cwd()
        return base / "local"
    return NATIVE_DIR


def _helper_output_dir() -> Path:
    root = _runtime_root()
    if getattr(sys, "frozen", False):
        return root / "native-bin" / "BedrockGdkHelper"
    return root / "bin" / "BedrockGdkHelper"


def _extractor_output_dir() -> Path:
    root = _runtime_root()
    if getattr(sys, "frozen", False):
        return root / "native-bin" / "BedrockXvdExtractor"
    return root / "bin" / "BedrockXvdExtractor"


def _helper_exe() -> Path:
    return _helper_output_dir() / "BedrockGdkHelper.exe"


def _extractor_exe() -> Path:
    return _extractor_output_dir() / "BedrockXvdExtractor.exe"


def check_assets() -> None:
    """校验认证注入组件是否就绪

    XUserLauncher.Core 为 BedrockBoot 官方闭源组件，合规原因不再随 FMCL
    分发，改为经用户同意后从官方 NuGet 按需下载（见 launcher/bedrock/components.py）。
    缺失时构建方应先用 components.download() 就绪组件。
    """
    from launcher.bedrock import components

    if components.is_ready():
        return
    raise RuntimeError(
        "GDK 认证注入组件未下载：XUserLauncher.Core.dll。"
        "请经用户同意后调用 launcher.bedrock.components.download() 完成下载。"
    )


def _find_dotnet() -> Optional[str]:
    """查找 dotnet CLI：PATH 优先，附带常见安装位置兜底（PATH 未刷新时）"""
    dotnet = shutil.which("dotnet")
    if dotnet:
        return dotnet
    for env_name in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_name)
        if not base:
            continue
        probe = Path(base) / "dotnet" / "dotnet.exe"
        if probe.is_file():
            return str(probe)
    return None


def check_dotnet() -> bool:
    """检测 dotnet SDK 是否可用（版本 >= 10）"""
    dotnet = _find_dotnet()
    if not dotnet:
        return False
    try:
        proc = subprocess.run(
            [dotnet, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        version = proc.stdout.strip()
        if not version.startswith(DOTNET_MIN_VERSION):
            print(f"警告: 需要 .NET 10 SDK，当前 {version}")
            return False
        return True
    except Exception:
        return False


def _publish(project_dir: Path, output_dir: Path, exe_path: Path, exe_name: str) -> Path:
    """通用 dotnet publish 流程"""
    dotnet = _find_dotnet()
    if not dotnet or not check_dotnet():
        raise RuntimeError(
            f"需要 .NET 10 SDK 才能构建 {exe_name}，请先安装 "
            "https://dotnet.microsoft.com/download/dotnet/10.0"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [dotnet, "publish", "-c", "Release", "-o", str(output_dir), "--nologo"],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=600,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"构建 {exe_name} 失败: {detail[-400:]}")
    if not exe_path.exists():
        raise RuntimeError(f"构建完成但未找到 {exe_path.name}")
    return exe_path


def build(force: bool = False) -> Path:
    """构建 GDK 认证注入辅助程序（BedrockGdkHelper）"""
    check_assets()
    exe_path = _helper_exe()
    if not force and exe_path.exists():
        return exe_path
    return _publish(HELPER_DIR, _helper_output_dir(), exe_path, "BedrockGdkHelper")


def build_extractor(force: bool = False) -> Path:
    """构建 GDK XVD 解压辅助程序（BedrockXvdExtractor，MIT 库 BedrockLauncher.Core）"""
    exe_path = _extractor_exe()
    if not force and exe_path.exists():
        return exe_path
    return _publish(EXTRACTOR_DIR, _extractor_output_dir(), exe_path, "BedrockXvdExtractor")


if __name__ == "__main__":
    try:
        force = "--force" in sys.argv
        if "--extractor" in sys.argv:
            path = build_extractor(force=force)
        else:
            path = build(force=force)
        print(f"OK: {path}")
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
