"""GDK 版游戏包（XVD/MSIXVC 容器）解压

委托 .NET 辅助程序 BedrockXvdExtractor 执行（运行时 dotnet publish 构建），
底层为 MIT 库 BedrockLauncher.Core——与 BedrockBoot 的 GDK 安装解压完全同源，
CIK 密钥随官方 NuGet 包分发，本模块不内置、不提取任何密钥。
"""

import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from logzero import logger

from launcher.bedrock.native import build_helper


class ExtractorError(RuntimeError):
    """GDK 解压错误"""


def _build_extractor() -> Path:
    try:
        return build_helper.build_extractor()
    except RuntimeError as e:
        raise ExtractorError(str(e)) from e


def extract_gdk_package(
    package_path: Path,
    output_dir: Path,
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    stop_event: Optional[threading.Event] = None,
    game_type: str = "release",
) -> int:
    """解压 GDK 游戏包（.msixvc / .insPack）到指定目录，返回文件总数

    实时解析辅助程序输出（PROGRESS:cur/total:path），支持取消与失败诊断。
    """
    stop_event = stop_event or threading.Event()
    exe = _build_extractor()

    cmd = [str(exe), str(Path(package_path).resolve()), str(Path(output_dir).resolve()), game_type]
    logger.info(f"GDK 解压（.NET）: {package_path} -> {output_dir}")
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except OSError as e:
        raise ExtractorError(f"启动解压辅助程序失败: {e}") from e

    file_count = 0
    error_lines: list = []
    assert proc.stdout is not None
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        if line.startswith("PROGRESS:"):
            try:
                _, counts, fname = line.split(":", 2)
                cur, total = counts.split("/", 1)
                file_count = int(total)
                if progress_cb:
                    progress_cb(int(cur), int(total), fname)
            except (ValueError, IndexError):
                logger.warning(f"解压进度行解析失败: {line}")
        elif line.startswith("COUNT:"):
            try:
                file_count = int(line.split(":", 1)[1])
            except ValueError:
                pass
        elif line.startswith("ERROR") or line.startswith("Unhandled"):
            error_lines.append(line)
        else:
            logger.info(f"[XvdExtractor] {line}")

        if stop_event.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise ExtractorError("解压已取消")

    proc.wait(timeout=120)
    if stop_event.is_set():
        raise ExtractorError("解压已取消")
    if proc.returncode != 0:
        detail = "\n".join(error_lines[-5:]) or f"退出码 {proc.returncode}"
        raise ExtractorError(f"GDK 解压失败: {detail}")
    return file_count
