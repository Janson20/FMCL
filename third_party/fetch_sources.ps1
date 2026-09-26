#Requires -Version 5.1
<#
.SYNOPSIS
    按 PROVENANCE.md 中 pin 的 commit 幂等拉取第三方源码树。

.DESCRIPTION
    为满足项目规则「第三方依赖要么来自官方包管理器，要么由本项目自行编译并记录来源
    与哈希」，本脚本把上游仓库按固定 commit 拉到 third_party/ 受控目录。

    行为（幂等，可反复执行）：
      1. 目标目录不存在  -> git clone --recursive <URL> <路径>
      2. 目标目录已存在  -> git fetch --all --tags --prune
                            git checkout --detach <pin 的 commit>
      3. 两种情况都执行  -> git submodule update --init --recursive
      4. 最后校验实际 HEAD 是否等于 pin 的 commit，不一致则非 0 退出。

    关于本地改动：脚本以「源码树必须与 pin 的 commit 完全一致」为前提，因此只要
    工作区不干净（无论 HEAD 是否已在 pin 上）都会拒绝继续。确需丢弃时显式加 -Force。

    编码说明：本文件保存为 UTF-8 with BOM。Windows PowerShell 5.1 读取无 BOM 的
    UTF-8 文件时会按系统 ANSI 代码页（本机为 GB2312）解码，导致中文字符串损坏，
    甚至因字节序列吞掉引号而直接语法报错。请勿去掉 BOM。

    运行环境说明：本机只安装了 Windows PowerShell 5.1，没有 PowerShell 7（pwsh）。
    下面的示例用 powershell.exe；若日后装了 PowerShell 7，把 powershell.exe 换成
    pwsh 即可，脚本本身在两者下都能运行。

.PARAMETER Repository
    只处理指定仓库，可取值：FluentUI、PySide6-FluentUI-QML。省略则处理全部。

.PARAMETER Force
    工作区存在本地改动时，丢弃它们（git reset --hard + git clean -fdx）后再 checkout。

.PARAMETER VerifyOnly
    只校验现有源码树的 HEAD 是否等于 pin 的 commit，不做任何拉取。

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1 -Repository FluentUI

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1 -VerifyOnly

.EXAMPLE
    # 丢弃本地改动并恢复到 pin 的干净状态（不可恢复）
    powershell -NoProfile -ExecutionPolicy Bypass -File third_party/fetch_sources.ps1 -Force
#>
[CmdletBinding()]
param(
    [Parameter()]
    [ValidateSet('FluentUI', 'PySide6-FluentUI-QML')]
    [string[]] $Repository,

    [Parameter()]
    [switch] $Force,

    [Parameter()]
    [switch] $VerifyOnly
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$LASTEXITCODE = 0

# ============================================================================
# pin 的 commit 集中定义在这里。升级上游版本时，先改 PROVENANCE.md 的表格，
# 再同步改这里，两者必须一致。
# ============================================================================
$PinnedCommits = [ordered]@{
    'FluentUI'             = '7e33a2f672d18239ef49ab960075b1137a1d18e7'
    'PySide6-FluentUI-QML' = '617986a17c9850ecb365bc02713433abab79c7aa'
}

$Repositories = @(
    [pscustomobject]@{
        Name    = 'FluentUI'
        Url     = 'https://github.com/zhuzichu520/FluentUI'
        Purpose = 'C++ Qt QML 组件库，编译出 fluentuiplugin 插件'
        Commit  = $PinnedCommits['FluentUI']
    }
    [pscustomobject]@{
        Name    = 'PySide6-FluentUI-QML'
        Url     = 'https://github.com/zhuzichu520/PySide6-FluentUI-QML'
        Purpose = 'PySide6 封装层与 QML 资源源码'
        Commit  = $PinnedCommits['PySide6-FluentUI-QML']
    }
)

# third_party/ 就是本脚本所在目录，与被调用时的工作目录无关。
$ThirdPartyDir = $PSScriptRoot

# ============================================================================
# 输出 helper
# ============================================================================
function Write-Step { param([Parameter(Mandatory)][string] $M) Write-Host ''; Write-Host "==> $M" -ForegroundColor Cyan }
function Write-Ok   { param([Parameter(Mandatory)][string] $M) Write-Host "    [OK]   $M" -ForegroundColor Green }
function Write-Info { param([Parameter(Mandatory)][string] $M) Write-Host "    [info] $M" -ForegroundColor Gray }
function Write-Warn { param([Parameter(Mandatory)][string] $M) Write-Host "    [warn] $M" -ForegroundColor Yellow }

<# 以终止性错误结束脚本，非 0 退出。 #>
function Fail {
    param([Parameter(Mandatory)][string] $Message, [string[]] $Hint)
    Write-Host ''
    Write-Host "错误：$Message" -ForegroundColor Red
    if ($null -ne $Hint) { foreach ($line in $Hint) { Write-Host "      $line" -ForegroundColor Red } }
    Write-Host ''
    exit 1
}

<#
.SYNOPSIS
    运行 git，返回输出与退出码，不抛异常。
.DESCRIPTION
    Windows PowerShell 5.1 下 $ErrorActionPreference = 'Stop' 会让原生命令写入
    stderr 的内容变成终止性错误，而 git 的进度输出正是走 stderr。
    因此这里把偏好局部降级为 Continue，只依据退出码判断成败。
#>
function Invoke-GitRaw {
    param(
        [Parameter(Mandatory)][string[]] $Arguments,
        [Parameter()][string] $WorkDir
    )
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        if ([string]::IsNullOrWhiteSpace($WorkDir)) {
            $out = & git @Arguments 2>&1
        }
        else {
            $out = & git -C $WorkDir @Arguments 2>&1
        }
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $saved
    }
    return [pscustomobject]@{ Output = @($out); Code = $code }
}

<# 运行 git，失败时抛出带中文说明的异常。 #>
function Invoke-Git {
    param(
        [Parameter(Mandatory)][string] $RepoPath,
        [Parameter(Mandatory)][string[]] $Arguments,
        [Parameter(Mandatory)][string] $FailureMessage
    )
    $r = Invoke-GitRaw -Arguments $Arguments -WorkDir $RepoPath
    if ($r.Code -ne 0) {
        $detail = ($r.Output | Out-String).Trim()
        throw "$FailureMessage`n        命令: git -C `"$RepoPath`" $($Arguments -join ' ')`n        退出码: $($r.Code)`n        输出: $detail"
    }
    return $r.Output
}

<# 取 git 单行输出并去空白。 #>
function Get-GitLine {
    param(
        [Parameter(Mandatory)][string] $RepoPath,
        [Parameter(Mandatory)][string[]] $Arguments,
        [Parameter(Mandatory)][string] $FailureMessage
    )
    return ((Invoke-Git -RepoPath $RepoPath -Arguments $Arguments -FailureMessage $FailureMessage) | Out-String).Trim()
}

<# 检查前置条件：git 可用、受控目录存在。 #>
function Assert-Prerequisites {
    $gitCmd = Get-Command -Name 'git' -CommandType Application -ErrorAction SilentlyContinue
    if ($null -eq $gitCmd) {
        Fail -Message '未找到 git，无法拉取源码。' -Hint @(
            '请先安装 Git for Windows（https://git-scm.com/download/win）并确保它在 PATH 中。',
            '验证：git --version'
        )
    }
    Write-Info "git: $($gitCmd.Source)"

    if (-not (Test-Path -LiteralPath $ThirdPartyDir -PathType Container)) {
        Fail -Message "受控目录不存在：$ThirdPartyDir" -Hint @(
            'third_party/ 目录应当已经存在（PROVENANCE.md 与 README.md 就在其中）。',
            '请确认仓库完整性：git status --porcelain -- third_party/'
        )
    }
}

<# 确认目标路径是 git 仓库，且 remote.origin.url 与预期一致。 #>
function Assert-ExistingRepoIsUsable {
    param(
        [Parameter(Mandatory)][pscustomobject] $Repo,
        [Parameter(Mandatory)][string] $TargetPath
    )
    if (-not (Test-Path -LiteralPath (Join-Path $TargetPath '.git'))) {
        Fail -Message "目标路径已存在，但它不是一个 git 仓库：$TargetPath" -Hint @(
            "预期它是 `"$($Repo.Url)`" 的克隆。",
            '请清空该目录后重跑本脚本，例如：',
            "    Remove-Item -LiteralPath `"$TargetPath`" -Recurse -Force",
            '不要手工往这个路径里放源码副本，那会破坏来源可验证性。'
        )
    }

    $actualUrl = Get-GitLine -RepoPath $TargetPath -Arguments @('config', '--get', 'remote.origin.url') `
        -FailureMessage '无法读取 remote.origin.url'

    if ($actualUrl -ne $Repo.Url) {
        Write-Warn 'remote.origin.url 与预期不一致'
        Write-Warn "  预期: $($Repo.Url)"
        Write-Warn "  实际: $actualUrl"
        Fail -Message '现有克隆的来源不是 PROVENANCE.md 记录的上游仓库。' -Hint @(
            '为避免把非权威来源的代码当成上游，脚本拒绝继续。',
            '如果确认这是你自己的 fork 镜像，请手工处理该目录，或删除后重跑本脚本。'
        )
    }
    Write-Info "remote.origin.url = $actualUrl"
}

<# 处理单个仓库。 #>
function Invoke-RepositorySync {
    param(
        [Parameter(Mandatory)][pscustomobject] $Repo,
        [Parameter(Mandatory)][bool] $ForceCheckout,
        [Parameter(Mandatory)][bool] $VerifyOnlyMode
    )

    $targetPath = Join-Path $ThirdPartyDir $Repo.Name
    $pinned = $Repo.Commit
    $shortPin = $pinned.Substring(0, 12)

    Write-Step "$($Repo.Name)  ->  $targetPath"
    Write-Info "上游: $($Repo.Url)"
    Write-Info "用途: $($Repo.Purpose)"
    Write-Info "pin : $pinned"

    # ---- 分支 1：目录不存在 -> clone --------------------------------------
    if (-not (Test-Path -LiteralPath $targetPath)) {
        if ($VerifyOnlyMode) {
            Fail -Message "源码树不存在，无法校验：$targetPath" -Hint @('去掉 -VerifyOnly 以执行拉取。')
        }

        Write-Info '目录不存在，执行 clone --recursive ...'
        # --recursive 在当前 pin 的 commit 上是空操作（上游 .gitmodules 为 0 字节、
        # 树中无 gitlink），保留它是为了防御上游未来重新引入子模块。
        $r = Invoke-GitRaw -Arguments @('clone', '--recursive', $Repo.Url, $targetPath)
        if ($r.Code -ne 0) {
            Fail -Message "git clone 失败（退出码 $($r.Code)）。" -Hint @(
                "上游 URL: $($Repo.Url)",
                '常见原因：无网络、代理未配置、目标路径被占用或权限不足。',
                "若克隆到一半失败，请删除残留目录后重试：Remove-Item -LiteralPath `"$targetPath`" -Recurse -Force"
            )
        }
        Write-Ok 'clone 完成'
    }
    # ---- 分支 2：目录已存在 -> fetch + checkout ---------------------------
    else {
        Write-Info '目录已存在，按幂等路径处理'
        Assert-ExistingRepoIsUsable -Repo $Repo -TargetPath $targetPath

        $currentHead = Get-GitLine -RepoPath $targetPath -Arguments @('rev-parse', 'HEAD') `
            -FailureMessage '无法读取当前 HEAD'
        Write-Info "当前 HEAD = $currentHead"

        # 注意：脏检查必须在「已在 pin 上」这个短路之前做。否则只要 HEAD 恰好等于
        # pin，源码树即使被本地改过也会被静默接受，而「自行编译 + 记录来源」这条
        # 合规链条正是靠「源码树与上游 commit 完全一致」成立的。
        $dirty = -not [string]::IsNullOrWhiteSpace(
            ((Invoke-Git -RepoPath $targetPath -Arguments @('status', '--porcelain') `
                -FailureMessage '无法读取工作区状态') | Out-String))
        $atPin = $currentHead -ieq $pinned

        if ($dirty) {
            Write-Warn '工作区不干净：源码树已被本地改动，构建结果将无法与上游 commit 对应'
        }

        if ($VerifyOnlyMode) {
            Write-Info '(-VerifyOnly) 跳过拉取'
            if ($dirty) { Write-Warn '上述改动未处理；去掉 -VerifyOnly 并加 -Force 可恢复干净状态' }
        }
        elseif ($dirty -and -not $ForceCheckout) {
            Fail -Message "工作区存在本地改动，拒绝继续：$targetPath" -Hint @(
                'third_party/ 下的源码树必须与 PROVENANCE.md 记录的 commit 完全一致，',
                '否则「自行编译并记录来源与哈希」这条合规链条断裂。',
                '为避免覆盖你的工作，脚本不会自动丢弃改动。请先自行查看：',
                "    git -C `"$targetPath`" status",
                "    git -C `"$targetPath`" diff",
                '确认可以丢弃后执行：',
                "    powershell -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -Repository $($Repo.Name) -Force",
                '-Force 会执行 git reset --hard 与 git clean -fdx，不可恢复。'
            )
        }
        elseif ($atPin -and -not $dirty) {
            Write-Ok '已在 pin 的 commit 上且工作区干净，跳过 fetch/checkout'
        }
        else {
            if (-not $atPin) {
                # 浅克隆无法 checkout 任意历史 commit，先补齐历史。
                $isShallow = Get-GitLine -RepoPath $targetPath `
                    -Arguments @('rev-parse', '--is-shallow-repository') `
                    -FailureMessage '无法判断是否为浅克隆'
                if ($isShallow -eq 'true') {
                    Write-Warn '检测到浅克隆，先执行 fetch --unshallow 补齐历史'
                    Invoke-Git -RepoPath $targetPath -Arguments @('fetch', '--unshallow', '--tags') `
                        -FailureMessage 'git fetch --unshallow 失败' | Out-Null
                }

                Write-Info '执行 git fetch --all --tags --prune ...'
                Invoke-Git -RepoPath $targetPath -Arguments @('fetch', '--all', '--tags', '--prune') `
                    -FailureMessage 'git fetch 失败（检查网络、代理与仓库访问权限）' | Out-Null
                Write-Ok 'fetch 完成'
            }
            else {
                Write-Ok 'HEAD 已在 pin 的 commit 上，无需 fetch'
            }

            if ($dirty) {
                Write-Warn '-Force 已启用，丢弃本地改动（reset --hard + clean -fdx）'
                Invoke-Git -RepoPath $targetPath -Arguments @('reset', '--hard') `
                    -FailureMessage 'git reset --hard 失败' | Out-Null
                Invoke-Git -RepoPath $targetPath -Arguments @('clean', '-fdx') `
                    -FailureMessage 'git clean -fdx 失败' | Out-Null
            }

            if (-not $atPin) {
                Write-Info "checkout --detach $shortPin ..."
                Invoke-Git -RepoPath $targetPath -Arguments @('checkout', '--detach', $pinned) `
                    -FailureMessage 'git checkout 到 pin 的 commit 失败（该 commit 可能已从上游移除）' | Out-Null
                Write-Ok 'checkout 完成'
            }
        }
    }

    # ---- 子模块初始化（两种分支都做） -------------------------------------
    if (-not $VerifyOnlyMode) {
        Write-Info 'submodule update --init --recursive ...'
        Invoke-Git -RepoPath $targetPath -Arguments @('submodule', 'update', '--init', '--recursive') `
            -FailureMessage 'git submodule update 失败' | Out-Null

        $subStatus = Invoke-Git -RepoPath $targetPath -Arguments @('submodule', 'status', '--recursive') `
            -FailureMessage '无法读取子模块状态'
        if ([string]::IsNullOrWhiteSpace(($subStatus | Out-String))) {
            Write-Ok '无子模块（上游 .gitmodules 为 0 字节、树中无 gitlink）'
        }
        else {
            Write-Ok '子模块已就绪'
            foreach ($line in $subStatus) { Write-Info "  $line" }
        }
    }

    # ---- 校验实际 HEAD ----------------------------------------------------
    $finalHead = Get-GitLine -RepoPath $targetPath -Arguments @('rev-parse', 'HEAD') `
        -FailureMessage '无法读取最终 HEAD'
    if ($finalHead -ine $pinned) {
        Fail -Message "HEAD 校验失败：实际 $finalHead，期望 $pinned" -Hint @(
            "仓库: $targetPath",
            'PROVENANCE.md 记录的 commit 与实际检出的 commit 不一致。',
            '请检查脚本顶部的 $PinnedCommits 是否与 PROVENANCE.md 同步。'
        )
    }
    Write-Ok "HEAD 校验通过: $finalHead"
}

# ============================================================================
# 主流程
# ============================================================================
Write-Host ''
Write-Host '第三方源码拉取（FMCL / third_party）' -ForegroundColor White
Write-Host "受控目录: $ThirdPartyDir"

Assert-Prerequisites

$targets = $Repositories
if ($null -ne $Repository -and $Repository.Count -gt 0) {
    $targets = @($Repositories | Where-Object { $Repository -contains $_.Name })
    if ($targets.Count -eq 0) {
        $names = ($Repositories | ForEach-Object { $_.Name }) -join ', '
        Fail -Message "-Repository 未匹配到任何仓库。" -Hint @("可用取值：$names")
    }
}

foreach ($repo in $targets) {
    try {
        Invoke-RepositorySync -Repo $repo -ForceCheckout ([bool]$Force) -VerifyOnlyMode ([bool]$VerifyOnly)
    }
    catch {
        Fail -Message $_.Exception.Message -Hint @(
            "仓库: $($repo.Name)",
            "路径: $(Join-Path $ThirdPartyDir $repo.Name)",
            '排查建议：确认网络与代理；确认 git 可用；确认路径未被其他进程占用。'
        )
    }
}

Write-Host ''
Write-Host "全部完成：$($targets.Count) 个仓库已就位并校验通过。" -ForegroundColor Green
Write-Host '下一步（如确需编译插件）：' -ForegroundColor White
Write-Host "    `$env:QTDIR = 'D:\Qt\6.7.3\msvc2019_64'   # 本项目实测路径；版本须与 PySide6 内置 Qt 一致"
Write-Host '    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_fluentui.ps1'
Write-Host ''
Write-Host '注：插件二进制不入库，由 scripts/build_fluentui.ps1 在本机编译，哈希记录见 third_party/BUILD_PROVENANCE.md'
Write-Host ''
exit 0
