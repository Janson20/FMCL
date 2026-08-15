"""构建 .NET 辅助程序（BedrockGdkHelper / BedrockXvdExtractor）

需要 .NET 10 SDK（dotnet）。产物：launcher/bedrock/native/bin/<项目名>/
"""

import shutil
import subprocess
import sys
from pathlib import Path

NATIVE_DIR = Path(__file__).resolve().parent
OUTPUT_ROOT = NATIVE_DIR / "bin"

HELPER_DIR = NATIVE_DIR / "helper"
HELPER_OUTPUT_DIR = OUTPUT_ROOT / "BedrockGdkHelper"
HELPER_EXE = HELPER_OUTPUT_DIR / "BedrockGdkHelper.exe"
REQUIRED_ASSETS = ("XUserLauncher.Core.dll",)

EXTRACTOR_DIR = NATIVE_DIR / "extractor"
EXTRACTOR_OUTPUT_DIR = OUTPUT_ROOT / "BedrockXvdExtractor"
EXTRACTOR_EXE = EXTRACTOR_OUTPUT_DIR / "BedrockXvdExtractor.exe"

DOTNET_MIN_VERSION = "10."


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
    """检测 dotnet SDK 是否可用（版本 >= 10）"""
    dotnet = shutil.which("dotnet")
    if not dotnet:
        return False
    try:
        proc = subprocess.run(
            [dotnet, "--version"], capture_output=True, text=True, timeout=30
        )
        version = proc.stdout.strip()
        if not version.startswith(DOTNET_MIN_VERSION):
            print(f"警告: 需要 .NET 10 SDK，当前 {version}")
            return False
        return True
    except Exception:
        return False


def _publish(project_dir: Path, output_dir: Path, exe_path: Path, exe_name: str, force: bool) -> Path:
    """通用 dotnet publish 流程"""
    if not check_dotnet():
        raise RuntimeError(
            f"需要 .NET 10 SDK 才能构建 {exe_name}，请先安装 "
            "https://dotnet.microsoft.com/download/dotnet/10.0"
        )
    proc = subprocess.run(
        ["dotnet", "publish", "-c", "Release", "-o", str(output_dir), "--nologo"],
        cwd=str(project_dir),
        capture_output=True,
        text=True,
        errors="replace",
        timeout=600,
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
    if not force and HELPER_EXE.exists():
        return HELPER_EXE
    return _publish(HELPER_DIR, HELPER_OUTPUT_DIR, HELPER_EXE, "BedrockGdkHelper", force)


def build_extractor(force: bool = False) -> Path:
    """构建 GDK XVD 解压辅助程序（BedrockXvdExtractor，MIT 库 BedrockLauncher.Core）"""
    if not force and EXTRACTOR_EXE.exists():
        return EXTRACTOR_EXE
    return _publish(EXTRACTOR_DIR, EXTRACTOR_OUTPUT_DIR, EXTRACTOR_EXE, "BedrockXvdExtractor", force)


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
