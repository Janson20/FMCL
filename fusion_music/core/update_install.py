"""自动更新的「落盘与替换」部分：暂存、生成替换脚本、启动、回滚。

Windows 上正在运行的 ``exe`` / ``DLL`` 是换不掉的，所以自动更新只能这样：
先把新版本**解压到暂存目录**（此时程序还在跑，用户看得见进度），
再生成一个 PowerShell 脚本、退出程序，由脚本等进程消失后替换文件并重启。

几条不能妥协的约束（都在这里实现）：

* **用户数据一个字节都不能动**：备份与覆盖都排除 ``data/``（默认数据目录
  就在程序目录里，一次「顺手删除多余文件」就等于清空用户的歌单与登录态）；
* **失败必须能回去**：覆盖前先整份备份程序文件（同样排除 ``data/``），
  替换过程出错就回滚并重新拉起旧版本；
* **暂存包要校验**：解压前检查 zip 成员路径（防目录穿越），解压后确认
  可执行文件真的在里面，宁可放弃更新也不覆盖一个空目录进去。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .updater import KIND_ONEDIR, KIND_ONEFILE, KIND_SOURCE
from .. import paths
from ..jsonio import atomic_write_json, read_json

logger = logging.getLogger(__name__)

#: 更新结果标记：更新前写入，下次启动据此判断「上次更新成没成」
PENDING_FILE = "pending.json"
#: 更新日志（脚本写的，出问题时让用户直接看这个）
LOG_NAME = "update.log"
#: 暂存目录前缀
STAGING_PREFIX = "staging-"
BACKUP_PREFIX = "backup-"
#: 保留几份备份（回滚用得上，但也不能把磁盘塞满）
KEEP_BACKUPS = 1
#: 标准可执行文件名（流水线产物里的名字）
STAGED_EXE_NAME = "FusionMusicPlayer.exe"
#: 脚本文件名
SCRIPT_NAME = "apply_update.ps1"


def log_file() -> Path:
    return paths.log_dir() / LOG_NAME


# ──────────────────────────────────────────────────────────────
# 可行性
# ──────────────────────────────────────────────────────────────


def can_auto_install(kind: str) -> Tuple[bool, str]:
    """能不能自动替换当前这份程序。返回 ``(能否, 不能的原因)``。"""
    if kind == KIND_SOURCE:
        return False, "源码运行时不支持自动更新，请到发布页查看新版本"
    if sys.platform != "win32":
        return False, "自动安装目前只支持 Windows，请到发布页手动下载"
    if not getattr(sys, "frozen", False):
        return False, "当前不是打包版本，请到发布页手动下载"
    target = paths.program_dir()
    if not paths.is_writable(target):
        return False, f"程序目录不可写（{target}），请手动下载后替换"
    return True, ""


# ──────────────────────────────────────────────────────────────
# 暂存
# ──────────────────────────────────────────────────────────────


def _safe_members(archive: zipfile.ZipFile, root: Path) -> List[zipfile.ZipInfo]:
    """把 zip 里的成员过滤成「解压后确实落在 root 之内」的那些。"""
    safe: List[zipfile.ZipInfo] = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/")
        if not name or name.endswith("/"):
            continue
        # 目录穿越 / 绝对路径一律拒绝
        if name.startswith("/") or ".." in Path(name).parts or ":" in name.split("/")[0]:
            logger.warning("跳过不安全的压缩包成员: %s", info.filename)
            continue
        destination = (root / name).resolve()
        try:
            destination.relative_to(root.resolve())
        except ValueError:
            logger.warning("跳过越界的压缩包成员: %s", info.filename)
            continue
        safe.append(info)
    return safe


def stage_zip(zip_path: Path, staging_dir: Path,
              progress: Optional[Callable[[int, int], None]] = None) -> Path:
    """把便携版压缩包解压到暂存目录，返回解压根。"""
    zip_path, staging_dir = Path(zip_path), Path(staging_dir)
    if not zip_path.exists():
        raise RuntimeError(f"下载的压缩包不存在：{zip_path}")
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path) as archive:
        members = _safe_members(archive, staging_dir)
        if not members:
            raise RuntimeError("压缩包里没有任何文件")
        total = len(members)
        for index, info in enumerate(members, 1):
            archive.extract(info, staging_dir)
            if progress is not None:
                progress(index, total)
    return staging_dir


def find_staged_exe(staging_dir: Path, preferred: str = STAGED_EXE_NAME) -> Optional[Path]:
    """在暂存目录里找可执行文件：先按标准名找，再退回「唯一的 exe」。"""
    staging_dir = Path(staging_dir)
    candidate = staging_dir / preferred
    if candidate.exists():
        return candidate
    exes = sorted(p for p in staging_dir.glob("*.exe") if p.is_file())
    return exes[0] if exes else None


def stage_release(asset_path: Path, kind: str, staging_dir: Path,
                  progress: Optional[Callable[[int, int], None]] = None) -> Path:
    """把下载好的资产准备成「可以直接覆盖过去」的目录。"""
    asset_path, staging_dir = Path(asset_path), Path(staging_dir)
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)

    if kind == KIND_ONEFILE:
        if not asset_path.exists():
            raise RuntimeError(f"下载的文件不存在：{asset_path}")
        target = staging_dir / STAGED_EXE_NAME
        shutil.copy2(asset_path, target)
        return staging_dir

    if kind == KIND_ONEDIR:
        stage_zip(asset_path, staging_dir, progress=progress)
        if find_staged_exe(staging_dir) is None:
            raise RuntimeError("压缩包里没有可执行文件，已放弃更新")
        return staging_dir

    raise RuntimeError("当前运行方式不支持自动更新")


# ──────────────────────────────────────────────────────────────
# 替换脚本
# ──────────────────────────────────────────────────────────────

#: PowerShell 模板。占位符用 @@名字@@，避免与 PS 自己的 ``$`` 变量打架。
_SCRIPT_TEMPLATE = r"""# Fusion Music Player 自动更新脚本
# 由程序生成（见 app/core/update_install.py）。它等旧进程退出后替换程序文件，
# 失败则回滚并重新拉起旧版本。用户数据目录 data 全程不碰。
param(
    [Parameter(Mandatory = $true)][int]$AppPid,
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [Parameter(Mandatory = $true)][string]$StagingDir,
    [Parameter(Mandatory = $true)][string]$ExeName,
    [Parameter(Mandatory = $true)][string]$BackupDir,
    [Parameter(Mandatory = $true)][string]$LogFile,
    [Parameter(Mandatory = $true)][string]$Mode,
    [Parameter(Mandatory = $true)][string]$DataDir
)

$ErrorActionPreference = 'Stop'

function Write-Log([string]$Message) {
    $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    try {
        # 日志目录可能还不存在（数据目录被挪过、或者第一次更新）：
        # 这一步失败的话后面所有诊断信息就全没了，所以先兜一次建目录
        $logDir = Split-Path -Parent $LogFile
        if ($logDir -and -not (Test-Path -LiteralPath $logDir)) {
            New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        }
        Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    } catch { }
}

function Invoke-Robocopy([string]$Source, [string]$Destination, [string[]]$Extra) {
    $arguments = @($Source, $Destination, '/E', '/R:2', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP')
    if ($Extra) { $arguments += $Extra }
    & robocopy @arguments | Out-Null
    if ($LASTEXITCODE -ge 8) {
        throw ("robocopy 失败（退出码 {0}）：{1} -> {2}" -f $LASTEXITCODE, $Source, $Destination)
    }
}

# 逐层比对「暂存目录」与「安装目录」：返回对不上的条目数。
# 只用 Join-Path 与名字拼路径，**不做任何字符串截断** —— 调用方传进来的可能是
# 8.3 短名（C:\Users\ADMINI~1\...），而 Get-ChildItem 返回长名，按长度截前缀
# 会得到一堆不存在的路径，把每个文件都误判成「没替换成功」。
function Test-StagedTree([string]$Source, [string]$Destination, [ref]$Checked, [ref]$Samples) {
    $missing = 0
    foreach ($item in Get-ChildItem -LiteralPath $Source -Force) {
        $target = Join-Path $Destination $item.Name
        if ($item.PSIsContainer) {
            if (-not (Test-Path -LiteralPath $target -PathType Container)) {
                $missing++
                if ($Samples.Value.Count -lt 5) { [void]$Samples.Value.Add($item.Name + '\') }
            } else {
                $missing += Test-StagedTree $item.FullName $target $Checked $Samples
            }
            continue
        }
        $Checked.Value++
        $bad = $false
        if (-not (Test-Path -LiteralPath $target -PathType Leaf)) {
            $bad = $true                       # 根本没落过去
        } elseif ((Get-Item -LiteralPath $target).Length -ne $item.Length) {
            $bad = $true                       # 落过去了但不是新版本
        }
        if ($bad) {
            $missing++
            if ($Samples.Value.Count -lt 5) { [void]$Samples.Value.Add($item.Name) }
        }
    }
    return $missing
}

Write-Log "=== 更新开始 ==="
Write-Log "pid=$AppPid mode=$Mode"
Write-Log "安装目录=$InstallDir"
Write-Log "暂存目录=$StagingDir"
Write-Log "备份目录=$BackupDir"

# 1. 等旧进程退出：运行中的 exe / DLL 换不掉
$deadline = (Get-Date).AddSeconds(90)
while (Get-Process -Id $AppPid -ErrorAction SilentlyContinue) {
    if ((Get-Date) -gt $deadline) {
        Write-Log "等待进程退出超时（90 秒），放弃更新"
        exit 1
    }
    Start-Sleep -Milliseconds 300
}
Start-Sleep -Milliseconds 800

$exePath = Join-Path $InstallDir $ExeName

# 0. 先确认暂存目录里真的有可执行文件：没有就直接退出，**一个字节都不动**。
#    只备份到一半才发现包是坏的，是最糟的结局（旧的被覆盖、新的又跑不起来）。
if (-not (Test-Path -LiteralPath (Join-Path $StagingDir '@@STAGED_EXE@@'))) {
    Write-Log "暂存目录里没有 '@@STAGED_EXE@@'，放弃更新（程序文件未做任何改动）"
    exit 1
}

$success = $false
try {
    # 2. 备份程序文件。排除 data 目录有两条理由：它是用户数据；
    #    而且备份目录本身通常就落在 data/updates 里 —— 不排除的话
    #    robocopy 会把自己的输出又当成输入，递归下去把磁盘写满。
    #    这里把「按名字排除 data」「按绝对路径排除数据目录」以及
    #    「排除备份目录与暂存目录」都写上：数据目录可以被配置到程序目录下的
    #    任何位置（FUSION_MUSIC_HOME），只靠名字挡不住。
    if (Test-Path -LiteralPath $BackupDir) { Remove-Item -LiteralPath $BackupDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
    Invoke-Robocopy $InstallDir $BackupDir @('/XD', 'data', $DataDir, $BackupDir, $StagingDir)
    Write-Log "已备份现有程序文件"

    # 3. 覆盖程序文件（同样排除 data）
    #    **必须带 /IS**：robocopy 默认会跳过「大小与时间戳都一样」的文件，
    #    而更新要的是「无条件用新版本覆盖」。本地测试时新旧文件常常同秒生成、
    #    大小也一致，于是被整片跳过、脚本却报成功 —— 用户下次启动还是旧程序。
    if ($Mode -eq 'onefile') {
        $newExe = Join-Path $StagingDir '@@STAGED_EXE@@'
        Copy-Item -LiteralPath $newExe -Destination $exePath -Force
    } else {
        Invoke-Robocopy $StagingDir $InstallDir @('/IS', '/XD', 'data', $DataDir)
        $stagedExe = Join-Path $StagingDir '@@STAGED_EXE@@'
        if ($ExeName -ne '@@STAGED_EXE@@') {
            Copy-Item -LiteralPath $stagedExe -Destination $exePath -Force
        }
    }
    Write-Log "程序文件已替换"

    # 3.5 校验：暂存里的文件是不是**真的**落到安装目录了。
    #     只看 robocopy 的退出码不够 —— 目标位置同名条目类型冲突时（例如新版里
    #     `_internal` 是目录、旧版里偏偏是个文件）它会「跳过」而不报错，
    #     脚本于是报成功，用户下次启动跑的还是旧程序。
    $checked = 0
    $samples = New-Object System.Collections.ArrayList
    $missing = Test-StagedTree $StagingDir $InstallDir ([ref]$checked) ([ref]$samples)
    if ($missing -gt 0) {
        throw ("有 {0} 个文件没有正确替换（例如 {1}），已判定更新失败" -f
               $missing, ($samples -join '、'))
    }
    Write-Log "替换结果已校验（共 $checked 个文件）"

    # 4. 拉起新版本
    Start-Process -FilePath $exePath -WorkingDirectory $InstallDir
    $success = $true
    Write-Log "已启动新版本，更新完成"
} catch {
    Write-Log ("更新失败：{0}" -f $_.Exception.Message)
    if (Test-Path -LiteralPath $BackupDir) {
        Write-Log "开始回滚…"
        try {
            Invoke-Robocopy $BackupDir $InstallDir @()
            Start-Process -FilePath $exePath -WorkingDirectory $InstallDir
            Write-Log "已回滚到旧版本并重新启动"
        } catch {
            Write-Log ("回滚也失败了：{0}" -f $_.Exception.Message)
        }
    }
} finally {
    if (-not $success) { Write-Log "=== 更新未完成 ===" }
}
"""


def build_script(staged_exe: str = STAGED_EXE_NAME) -> str:
    """生成替换脚本内容（纯函数，便于离线回归）。

    具体的目录、PID 之类都通过**命令行参数**传给脚本，不写死在脚本里：
    脚本是要在程序退出后才跑的，参数化之后出问题时能直接手动重放一遍。
    """
    return _SCRIPT_TEMPLATE.replace("@@STAGED_EXE@@", str(staged_exe))


def write_script(content: str, path: Optional[Path] = None) -> Path:
    """把脚本写到 ``data/updates/`` 下（不是程序目录：它不会被更新覆盖）。"""
    target = Path(path) if path else paths.updates_dir() / SCRIPT_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8-sig")   # 带 BOM：PS 5.1 才认 UTF-8
    return target


def launch_script(script_path: Path, *, kind: str, pid: int, install_dir: Path,
                  staging_dir: Path, exe_name: str, backup_dir: Path,
                  log_path: Path, data_dir: Path) -> None:
    """后台启动替换脚本（不等待），调用方随后应当立刻退出程序。"""
    if sys.platform != "win32":
        raise RuntimeError("自动安装目前只支持 Windows")
    args = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
        "-File", str(script_path),
        "-AppPid", str(int(pid)),
        "-InstallDir", str(install_dir),
        "-StagingDir", str(staging_dir),
        "-ExeName", str(exe_name),
        "-BackupDir", str(backup_dir),
        "-LogFile", str(log_path),
        "-Mode", str(kind),
        "-DataDir", str(data_dir),
    ]
    creation = 0
    for flag in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
        creation |= int(getattr(subprocess, flag, 0))
    subprocess.Popen(args, creationflags=creation, close_fds=True, cwd=str(paths.updates_dir()))


# ──────────────────────────────────────────────────────────────
# 更新结果标记与清理
# ──────────────────────────────────────────────────────────────


def write_pending(data: Dict[str, Any]) -> bool:
    """写下「正在更新」的标记。

    **必须原子**：这份标记是判断「上次更新到底成没成」的唯一依据，写到一半断电
    就会留下半个 JSON，下次启动读到坏文件、以为更新没做过（旧版本正是直接
    ``write_text``，见 app/jsonio.py 的说明）。
    """
    try:
        payload = dict(data)
        payload.setdefault("at", time.time())
        atomic_write_json(paths.updates_dir() / PENDING_FILE, payload)
        return True
    except OSError as e:
        logger.debug("写入更新标记失败: %s", e)
        return False


def read_pending() -> Optional[Dict[str, Any]]:
    data = read_json(paths.updates_dir() / PENDING_FILE, None)
    return data if isinstance(data, dict) else None


def clear_pending() -> None:
    try:
        (paths.updates_dir() / PENDING_FILE).unlink(missing_ok=True)
    except OSError:
        pass


def read_log_tail(lines: int = 12) -> str:
    """读更新日志的最后几行（界面里展示失败原因）。"""
    try:
        path = log_file()
        if not path.exists():
            return ""
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(content[-max(1, int(lines)):])
    except OSError:
        return ""


def prune_stale() -> None:
    """清掉旧的暂存目录与备份（保留最近 :data:`KEEP_BACKUPS` 份备份）。"""
    base = paths.updates_dir()
    if not base.exists():
        return
    for prefix, keep in ((STAGING_PREFIX, 0), (BACKUP_PREFIX, KEEP_BACKUPS)):
        try:
            items = sorted(
                (p for p in base.glob(prefix + "*") if p.is_dir()),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            continue
        for old in items[keep:]:
            shutil.rmtree(old, ignore_errors=True)


def cleanup_stale_downloads() -> None:
    """删掉上次没下完的 ``.part`` 残留。"""
    base = paths.updates_dir()
    if not base.exists():
        return
    for part in base.glob("*.part"):
        try:
            part.unlink()
        except OSError:
            pass
