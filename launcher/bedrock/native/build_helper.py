"""构建 GDK 认证注入辅助程序（BedrockGdkHelper）

需要 .NET 10 SDK（dotnet）。产物：launcher/bedrock/native/bin/BedrockGdkHelper/
"""

import shutil
import subprocess
import sys
from pathlib import Path

NATIVE_DIR = Path(__file__).resolve().parent
HELPER_DIR = NATIVE_DIR / "helper"
OUTPUT_DIR = NATIVE_DIR / "bin" / "BedrockGdkHelper"
HELPER_EXE = OUTPUT_DIR / "BedrockGdkHelper.exe"
REQUIRED_ASSETS = ("XUserLauncher.Core.dll",)


def check_assets() -> None:
    """校验认证注入组件是否就绪

    XUserLauncher.Core 为 BedrockBoot 官方闭源组件，合规原因不再随 FMCL
    分发，改为经用户同意后从官方 NuGet 按需下载（见 launcher/bedrock/components.py）。
    缺失时构建方应先用 components.download() 就绪组件。
    """
    missing = [name for name in REQUIRED_ASSETS if not (NATIVE_DIR / "assets" / name).is_file()]
    if missing:
        raise RuntimeError(
            "GDK 认证注入组件未下载：" + "、".join(missing) + "。"
            "请经用户同意后调用 launcher.bedrock.components.download() 完成下载。"
        )


def check_dotnet() -> bool:
    """检测 dotnet SDK 是否可用"""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        return False
    try:
        proc = subprocess.run(
            [dotnet, "--version"], capture_output=True, text=True, timeout=30
        )
        version = proc.stdout.strip()
        if not version.startswith("10."):
            print(f"警告: 需要 .NET 10 SDK，当前 {version}")
            return False
        return True
    except Exception:
        return False


def build(force: bool = False) -> Path:
    """构建 helper，返回 exe 路径；缺失或强制时重新构建"""
    check_assets()
    if not force and HELPER_EXE.exists():
        return HELPER_EXE
    if not check_dotnet():
        raise RuntimeError(
            "需要 .NET 10 SDK 才能构建 GDK 认证辅助程序，请先安装 "
            "https://dotnet.microsoft.com/download/dotnet/10.0"
        )
    proc = subprocess.run(
        ["dotnet", "publish", "-c", "Release", "-o", str(OUTPUT_DIR), "--nologo"],
        cwd=str(HELPER_DIR),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=600,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"构建 BedrockGdkHelper 失败: {detail[-400:]}")
    if not HELPER_EXE.exists():
        raise RuntimeError("构建完成但未找到 BedrockGdkHelper.exe")
    return HELPER_EXE


if __name__ == "__main__":
    try:
        path = build(force="--force" in sys.argv)
        print(f"OK: {path}")
    except RuntimeError as e:
        print(f"ERROR: {e}")
        sys.exit(1)
