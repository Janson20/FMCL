#Requires -Version 5.1
<#
.SYNOPSIS
    从 third_party/ 中 pin 的源码编译 FluentUI QML 插件，安装到 third_party/_install。

.DESCRIPTION
    本脚本是「本项目自行编译第三方依赖」这一合规动作的载体：不下载任何上游二进制，
    只从 PROVENANCE.md 记录的 commit 编译出插件，并把产物的 SHA256 写入
    third_party/BUILD_PROVENANCE.md。

    首次使用前请准备 Qt 6 SDK、CMake，以及 ninja 或 Visual Studio 的 C++ 工具链。
    脚本会检查源码、Qt 模块与编译工具，并从 PATH 和常见安装位置查找命令。

    编码说明：本文件保存为 UTF-8 with BOM。Windows PowerShell 5.1 读取无 BOM 的
    UTF-8 文件时会按系统 ANSI 代码页解码，导致中文字符串损坏，甚至因字节序列
    吞掉引号而直接语法报错。请勿去掉 BOM。

    脚本最低要求 Windows PowerShell 5.1，也可在 PowerShell 7 下运行。

.PARAMETER QtDir
    Qt SDK 套件目录，例如 <Qt 安装根目录>/6.7.3/msvc2019_64。
    不传时取环境变量 QTDIR。两者都没有则报错并给出获取指引。

.PARAMETER BuildType
    Release 或 Debug。默认 Release。仅对单配置生成器（Ninja）生效。

.PARAMETER Generator
    CMake 生成器名。默认自动：有 ninja 用 Ninja，否则用 Visual Studio 生成器。

.PARAMETER Jobs
    并行编译任务数。默认取逻辑处理器数量。

.PARAMETER SkipInstall
    只编译不安装（不复制到最终 _install/qml；插件仍在 QML 模块输出目录）。

.PARAMETER BuildStaticLib
    编译静态库（target 名变为 fluentui，需 Qt >= 6.2，且需在应用里手动注册类型）。
    默认关，即编译共享库插件 fluentuiplugin。

.EXAMPLE
    $env:QTDIR = '<Qt 安装根目录>/6.7.3/msvc2019_64'
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_fluentui.ps1

.EXAMPLE
    # 只看脚本将要执行的步骤，不真正编译
    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_fluentui.ps1 -WhatIf

.NOTES
    需要 Qt 6 SDK（Core / Quick / Qml / Widgets / PrintSupport、Core5Compat、
    ShaderTools）、CMake，以及 ninja 或 Visual Studio 的 C++ 工具链。
    脚本会优先使用 PATH 中的 cmake 与 ninja；没有 ninja 时自动选择可用的
    Visual Studio 生成器。
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter()][string] $QtDir,
    [Parameter()][ValidateSet('Release', 'Debug')][string] $BuildType = 'Release',
    [Parameter()][string] $Generator,
    [Parameter()][int] $Jobs = 0,
    [Parameter()][switch] $SkipInstall,
    [Parameter()][switch] $BuildStaticLib
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$LASTEXITCODE = 0

# ============================================================================
# 路径常量（相对本脚本定位，与被调用时的工作目录无关）
# ============================================================================
$ScriptDir      = $PSScriptRoot
$RepoRoot       = Split-Path -Path $ScriptDir -Parent
$ThirdPartyDir  = Join-Path $RepoRoot 'third_party'
$SourceDir      = Join-Path $ThirdPartyDir 'FluentUI'
$BuildDir       = Join-Path $ThirdPartyDir '_build'
$StageDir       = Join-Path $ThirdPartyDir '_stage'
$InstallDir     = Join-Path $ThirdPartyDir '_install'
$PluginStageDir = Join-Path $InstallDir 'qml'
$ProvenanceFile = Join-Path $ThirdPartyDir 'BUILD_PROVENANCE.md'
$CMakeExe       = $null

# 上游 src/CMakeLists.txt 里 add_definitions(-DFLUENTUI_VERSION=1,7,7,0)
$ExpectedPluginVersion = '1.7.7'

# ============================================================================
# 输出 helper
# ============================================================================
function Write-Step { param([Parameter(Mandatory)][AllowEmptyString()][string] $M) Write-Host ''; Write-Host "==> $M" -ForegroundColor Cyan }
function Write-Ok   { param([Parameter(Mandatory)][AllowEmptyString()][string] $M) Write-Host "    [OK]   $M" -ForegroundColor Green }
function Write-Info { param([Parameter(Mandatory)][AllowEmptyString()][string] $M) Write-Host "    [info] $M" -ForegroundColor Gray }
function Write-Warn { param([Parameter(Mandatory)][AllowEmptyString()][string] $M) Write-Host "    [warn] $M" -ForegroundColor Yellow }

<# 以终止性错误结束脚本，非 0 退出。 #>
function Fail {
    param([Parameter(Mandatory)][string] $Message, [string[]] $Hint)
    Write-Host ''
    Write-Host "错误：$Message" -ForegroundColor Red
    if ($null -ne $Hint) { foreach ($l in $Hint) { Write-Host "      $l" -ForegroundColor Red } }
    Write-Host ''
    exit 1
}

<#
.SYNOPSIS
    运行外部命令；失败时以中文报错并非 0 退出。
.DESCRIPTION
    Windows PowerShell 5.1 下 $ErrorActionPreference = 'Stop' 会让原生命令写入
    stderr 的内容变成终止性错误，而 cmake/cl 会往 stderr 写警告与进度。
    因此这里把偏好局部降级为 Continue，只依据退出码判断成败。
#>
function Invoke-Native {
    param(
        [Parameter(Mandatory)][string] $FilePath,
        [Parameter(Mandatory)][string[]] $Arguments,
        [Parameter(Mandatory)][string] $FailureMessage
    )
    Write-Info "执行: $FilePath $($Arguments -join ' ')"
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $FilePath @Arguments
        $code = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $saved
    }
    if ($code -ne 0) {
        Fail -Message "$FailureMessage（退出码 $code）" -Hint @(
            "命令: $FilePath $($Arguments -join ' ')",
            '完整错误见上方输出。'
        )
    }
}

<# 捕获命令输出（单行）。失败时返回空串而不抛异常。 #>
function Get-NativeLine {
    param(
        [Parameter(Mandatory)][string] $FilePath,
        [Parameter(Mandatory)][string[]] $Arguments
    )
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $FilePath @Arguments 2>&1
    }
    finally {
        $ErrorActionPreference = $saved
    }
    return ($out | Out-String).Trim()
}

<# 在 PATH 与常见安装位置查找 cmake。 #>
function Find-CMake {
    $cmd = Get-Command cmake -ErrorAction SilentlyContinue
    if ($null -ne $cmd) { return $cmd.Source }
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LOCALAPPDATA)
    foreach ($root in $roots) {
        if ([string]::IsNullOrWhiteSpace($root)) { continue }
        $candidate = Join-Path $root 'CMake\bin\cmake.exe'
        if (Test-Path -LiteralPath $candidate) { return $candidate }
    }
    return $null
}

<# 查找 Visual Studio 的 vcvars64.bat；找不到时返回 $null。 #>
function Find-VcVars {
    $vsRoot = ${env:ProgramFiles(x86)}
    if ([string]::IsNullOrWhiteSpace($vsRoot)) { return $null }
    $vsWhere = Join-Path $vsRoot 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path -LiteralPath $vsWhere)) { return $null }
    $install = Get-NativeLine -FilePath $vsWhere -Arguments @(
        '-latest', '-products', '*', '-requires', 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64',
        '-property', 'installationPath'
    )
    if ([string]::IsNullOrWhiteSpace($install)) { return $null }
    $vcvars = Join-Path $install.Trim() 'VC\Auxiliary\Build\vcvars64.bat'
    if (Test-Path -LiteralPath $vcvars) { return $vcvars }
    return $null
}

<# 从 cmake --help 中选择主版本号最高的 Visual Studio 生成器。 #>
function Get-DefaultVisualStudioGenerator {
    param([Parameter(Mandatory)][string] $CMakePath)
    $help = Get-NativeLine -FilePath $CMakePath -Arguments @('--help')
    $bestMajor = -1
    $best = ''
    foreach ($m in [regex]::Matches($help, '(?m)^\s*\*?\s*Visual Studio\s+(\d+)\s+(\d{4})')) {
        $major = [int]$m.Groups[1].Value
        if ($major -gt $bestMajor) {
            $bestMajor = $major
            $best = "Visual Studio $($m.Groups[1].Value) $($m.Groups[2].Value)"
        }
    }
    if ($bestMajor -ge 0) { return $best }
    return $null
}

<#
.SYNOPSIS
    macOS / Linux 构建指引（预留分支，Windows 脚本不实现）。
#>
function Show-PlatformGuidance {
    param([Parameter(Mandatory)][ValidateSet('macOS', 'Linux')][string] $Platform)

    $macLines = @(
        '  1. 工具链：Xcode Command Line Tools（xcode-select --install）提供 clang/clang++，',
        '     不需要 vcvars；生成器用 Ninja，或用 Xcode（多配置生成器，需 --config）。',
        '  2. Qt 获取：',
        '       uv tool run --from aqtinstall aqt install-qt mac desktop 6.6.2 clang_64 -m qt5compat qtshadertools',
        '     注意 mac 的 arch 名是 clang_64（不是 macos）；要出 universal 二进制需另配 --arch。',
        '  3. 产物形态：插件是 .dylib（也可能被打包进 .framework），安装到 <prefix>/imports/FluentUI。',
        '  4. 关键坑：',
        '     - rpath：插件的 LC_RPATH 必须指向 Qt 的 Frameworks 目录，否则运行时 dlopen 失败；',
        '       打包用 macdeployqt，必要时用 install_name_tool -change 修正。',
        '     - 代码签名与公证：启用 Hardened Runtime 时自编译 .dylib 必须一并签名，否则被 Gatekeeper 拦截；',
        '       公证（notarization）需要 Apple Developer 账号，属于发布环节的额外成本。',
        '     - 上游 example/CMakeLists.txt 对 APPLE 用了独立的 APPLICATION_DIR_PATH',
        '       （.app/Contents/MacOS），若日后开启示例要注意输出路径差异。'
    )
    $linuxLines = @(
        '  1. 工具链：gcc/g++（建议 13 以上），生成器用 Ninja，无需 vcvars 之类的环境脚本。',
        '  2. Qt 获取（二选一）：',
        '       a. aqtinstall：',
        '          uv tool run --from aqtinstall aqt install-qt linux desktop 6.6.2 linux_gcc_64 -m qt5compat qtshadertools',
        '       b. 发行版包（版本可能偏旧，注意 qt_add_qml_module 需要 Qt >= 6.2）：',
        '          Debian/Ubuntu: qt6-base-dev qt6-declarative-dev qt6-5compat-dev',
        '          Fedora:        qt6-qtbase-devel qt6-qtdeclarative-devel qt6-qt5compat-devel',
        '  3. 产物形态：libfluentuiplugin.so（或去前缀的 fluentuiplugin.so）。',
        '     qmldir 里的 plugin 名必须与实际文件名一致，否则报',
        '     module "FluentUI" plugin "fluentuiplugin" not found。上游在 MINGW 下用',
        '     set_target_properties(... PREFIX "") 去掉 lib 前缀，Linux 下需自行确认。',
        '  4. 关键坑：',
        '     - 系统 Qt 与 aqt Qt 混用会让 Qt6Config.cmake 解析到错误版本，务必用 -DCMAKE_PREFIX_PATH 固定。',
        '     - 缺 OpenGL/X11 开发头文件时 Qt Quick 链接失败：需 libgl1-mesa-dev、libxkbcommon-dev 等。',
        '     - 自编译 .so 的 RPATH 与目标机 Qt 路径不匹配会导致运行时找不到依赖，',
        '       发布前用 readelf -d 检查 RUNPATH。'
    )

    if ($Platform -eq 'macOS') { $lines = $macLines } else { $lines = $linuxLines }

    Write-Host ''
    Write-Host "$Platform 构建分支尚未实现（预留）。实现时需要的差异：" -ForegroundColor Yellow
    foreach ($l in $lines) { Write-Host $l }
    Write-Host ''
    Write-Host '  本平台分支当前直接以非 0 退出，以免误以为构建成功。' -ForegroundColor Yellow
    Write-Host ''
}

# ============================================================================
# 步骤 1：平台判定
# ============================================================================
$isWinHost   = $true
$isMacHost   = $false
$isLinuxHost = $false
if ($PSVersionTable.PSVersion.Major -ge 6) {
    # $IsWindows / $IsMacOS / $IsLinux 只在 PowerShell 6+ 存在；
    # 5.1 下引用它们会被 StrictMode 判为未定义变量，所以必须放在版本判断里。
    $isWinHost   = [bool]$IsWindows
    $isMacHost   = [bool]$IsMacOS
    $isLinuxHost = [bool]$IsLinux
}

Write-Host ''
Write-Host 'FluentUI 插件构建（FMCL / third_party）' -ForegroundColor White
Write-Host "源码目录  : $SourceDir"
Write-Host "构建目录  : $BuildDir"
Write-Host "安装前缀  : $InstallDir"

if ($isMacHost) { Show-PlatformGuidance -Platform 'macOS'; exit 1 }
if ($isLinuxHost) { Show-PlatformGuidance -Platform 'Linux'; exit 1 }

# ============================================================================
# 步骤 2：源码与 CMake
# ============================================================================
Write-Step '检查前置条件'

if (-not (Test-Path -LiteralPath (Join-Path $SourceDir 'CMakeLists.txt'))) {
    Fail -Message "未找到 FluentUI 源码：$SourceDir" -Hint @(
        '请先按 PROVENANCE.md 中 pin 的 commit 拉取源码：',
        "    powershell -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $ThirdPartyDir 'fetch_sources.ps1')`"",
        '只校验不拉取：'
        "    powershell -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $ThirdPartyDir 'fetch_sources.ps1')`" -VerifyOnly"
    )
}
Write-Ok "找到源码树 $SourceDir"

$srcHead = Get-NativeLine -FilePath 'git' -Arguments @('-C', $SourceDir, 'rev-parse', 'HEAD')
if ([string]::IsNullOrWhiteSpace($srcHead)) {
    Write-Warn '无法读取源码树 HEAD（可能不是 git 仓库），来源不可校验，请谨慎继续'
    $srcHead = 'unknown'
}
else { Write-Info "源码 HEAD = $srcHead" }

if ([string]::IsNullOrWhiteSpace($CMakeExe)) {
    $CMakeExe = Find-CMake
}
if ([string]::IsNullOrWhiteSpace($CMakeExe)) {
    Fail -Message '未找到 cmake。' -Hint @(
        '请把 cmake 加入 PATH，或通过环境变量 / 安装器把它放到标准位置。',
        'Windows 可在 CMake 官网下载安装器；安装时勾选加入 PATH。'
    )
}
if (-not (Test-Path -LiteralPath $CMakeExe)) {
    Fail -Message "cmake 路径不存在：$CMakeExe" -Hint @('检查 PATH 中 cmake 的指向，或重新安装 CMake。')
}
$cmakeVersion = ((Get-NativeLine -FilePath $CMakeExe -Arguments @('--version')) -split "`r?`n")[0].Trim()
Write-Ok "cmake: $cmakeVersion"

# ============================================================================
# 步骤 3：定位 Qt SDK
# ============================================================================
Write-Step '定位 Qt SDK'

if ([string]::IsNullOrWhiteSpace($QtDir)) { $QtDir = $env:QTDIR }
if ([string]::IsNullOrWhiteSpace($QtDir)) {
    Fail -Message '未设置 Qt SDK 路径。' -Hint @(
        '请通过环境变量 QTDIR 或参数 -QtDir 指定 Qt SDK 套件目录。',
        '例如：',
        "    `$env:QTDIR = '<Qt 安装根目录>/6.7.3/msvc2019_64'",
        '',
        '可用 uv 运行 aqtinstall 安装所需版本与模块：',
        '    uv tool run --from aqtinstall aqt install-qt windows desktop 6.7.3 win64_msvc2019_64 -m qt5compat qtshadertools',
        '',
        '坑：不要把命令写成裸 aqt。PyPI 上名为 aqt 的包是 Anki 的同名工具，',
        '    不是 aqtinstall；裸 aqt 会解析到 anki 的包，报出与安装 Qt 无关的参数错误。',
        '    必须用 --from aqtinstall 显式指定发行包名。',
        '',
        '模块说明（注意 aqtinstall 模块名与 CMake 包名不同）：',
        '    aqt 的 qt5compat     -> CMake 包 Qt6Core5Compat，提供 QML 的 Qt5Compat.GraphicalEffects',
        '                            （上游 FluAcrylic.qml 与 FluClip.qml 直接 import）',
        '    aqt 的 qtshadertools -> CMake 包 Qt6ShaderTools，上游 README 与 CI 都列为必需',
        '上游 CI 还装了 qtmultimedia / qtimageformats / qt3d / qtspeech，那些只被 example',
        '演示程序用到；本脚本以 -DFLUENTUI_BUILD_EXAMPLES=OFF 关闭示例，无需安装。',
        '',
        '也可使用 Qt 官方在线安装器：https://download.qt.io/archive/online_installers/'
    )
}
if (-not (Test-Path -LiteralPath $QtDir)) {
    Fail -Message "QTDIR 指向的目录不存在：$QtDir" -Hint @(
        '检查 QTDIR 拼写；它应指向具体套件目录（如 ...\6.6.2\msvc2019_64），',
        '而不是 Qt 安装根目录或版本目录。'
    )
}
Write-Ok "QTDIR = $QtDir"

$qtCoreConfig = Get-ChildItem -LiteralPath $QtDir -Recurse -Filter 'Qt6Config.cmake' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($null -eq $qtCoreConfig) {
    Fail -Message "在 $QtDir 下找不到 Qt6Config.cmake，这不是可用的 Qt6 SDK 目录。" -Hint @(
        '请确认指向 Qt6 套件目录，且该套件为完整安装（未裁剪 cmake 配置）。',
        '若该目录只装了 Qt5，请改装 Qt6：上游 main 分支已只支持 Qt6。'
    )
}
Write-Info "Qt6Config.cmake: $($qtCoreConfig.FullName)"

# 取 Qt 版本号（尽力而为，取不到就是 unknown，不影响构建）。
# 实测 Qt 6.7.3 里 Qt6ConfigVersion.cmake 并不含 PACKAGE_VERSION 字面量，
# 真正的版本号在 Qt6ConfigVersionImpl.cmake（set(PACKAGE_VERSION "6.7.3")），
# 所以两个文件都要试。
$qtVersion = 'unknown'
$qtCmakeDir = Split-Path -Path $qtCoreConfig.FullName -Parent
foreach ($verFile in @('Qt6ConfigVersion.cmake', 'Qt6ConfigVersionImpl.cmake', 'Qt6ConfigVersionImpl.cmake.in')) {
    if ($qtVersion -ne 'unknown') { break }
    $vp = Join-Path $qtCmakeDir $verFile
    if (-not (Test-Path -LiteralPath $vp)) { continue }
    $m = Select-String -LiteralPath $vp -Pattern 'set\(PACKAGE_VERSION\s+"?([0-9][0-9.]*)' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $m) { $qtVersion = $m.Matches[0].Groups[1].Value }
}
if ($qtVersion -eq 'unknown') {
    # 退路：从套件路径里取形如 6.7.3 的版本段
    $mm = [regex]::Match($QtDir, '(\d+\.\d+(\.\d+)?)')
    if ($mm.Success) { $qtVersion = $mm.Groups[1].Value + ' (从路径推断)' }
}
Write-Ok "Qt 版本: $qtVersion"

# 检查上游 README 声明为必需的扩展模块。
# 注意：aqtinstall 的模块名与 CMake 包名不同，按 aqt 模块名去找
# <模块名>Config.cmake 会永远找不到，从而误报「模块缺失」。
#   aqt qt5compat     -> CMake 包 Qt6Core5Compat（提供 QML 模块 Qt5Compat.GraphicalEffects）
#   aqt qtshadertools -> CMake 包 Qt6ShaderTools
$moduleChecks = @(
    [pscustomobject]@{
        Aqt = 'qt5compat'
        Pkg = 'Qt6Core5Compat'
        Why = '提供 QML 的 Qt5Compat.GraphicalEffects（上游 FluAcrylic.qml / FluClip.qml 直接 import）'
    }
    [pscustomobject]@{
        Aqt = 'qtshadertools'
        Pkg = 'Qt6ShaderTools'
        Why = '上游 README 与 CI 都列为必需的工具模块'
    }
)
foreach ($mc in $moduleChecks) {
    $cf = Get-ChildItem -LiteralPath $QtDir -Recurse -Filter "$($mc.Pkg)Config.cmake" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $cf) {
        Write-Warn "未找到 CMake 包 $($mc.Pkg)（对应 aqtinstall 模块 $($mc.Aqt)）"
        Write-Warn "  原因：$($mc.Why)"
        Write-Warn "  补装：uv tool run --from aqtinstall aqt install-qt windows desktop <版本> win64_msvc2019_64 -m $($mc.Aqt)"
    }
    else { Write-Ok "$($mc.Pkg) 已安装（aqtinstall 模块 $($mc.Aqt)）" }
}

# 同时确认上游 find_package 要求的 Qt 组件都在
foreach ($comp in @('Core', 'Quick', 'Qml', 'Widgets', 'PrintSupport')) {
    $cf = Get-ChildItem -LiteralPath $QtDir -Recurse -Filter "Qt6${comp}Config.cmake" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -eq $cf) { Write-Warn "缺少 Qt6$comp（上游 find_package REQUIRED 组件）" }
    else { Write-Ok "Qt6$comp 已安装" }
}

# ============================================================================
# 步骤 4：MSVC 编译环境
# ============================================================================
Write-Step '检查 MSVC 编译环境'

$clCmd = Get-Command cl.exe -ErrorAction SilentlyContinue
if ($null -ne $clCmd) {
    Write-Ok "cl.exe 已在 PATH: $($clCmd.Source)"
}
else {
    Write-Warn 'cl.exe 不在 PATH 中。'
    $vcvars = Find-VcVars
    if ($null -ne $vcvars) {
        Write-Info "找到 vcvars64.bat: $vcvars"
    }
    else {
        Write-Info '未自动找到 vcvars64.bat。'
    }
    Write-Info ''
    Write-Info '需要先进入 MSVC 环境。cmd 里 call 出来的环境变量不会自动继承到 PowerShell，'
    Write-Info '因此要么在 PowerShell 里导入 vcvars 导出的变量，要么改用 Visual Studio 生成器'
    Write-Info '（VS 生成器会自行定位 MSVC 工具链，不需要 vcvars64.bat）：'
    Write-Info '    powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build_fluentui.ps1 -Generator "<Visual Studio 生成器名>"'
    Write-Info ''
    Write-Warn '若坚持用 Ninja，必须在已 call 过 vcvars64.bat 的 shell 中运行本脚本，'
    Write-Warn '否则 CMake 会报找不到 C/C++ 编译器。'
}

# ============================================================================
# 步骤 5：确定生成器
# ============================================================================
Write-Step '确定 CMake 生成器'

$ninjaCmd = Get-Command ninja -ErrorAction SilentlyContinue
if ([string]::IsNullOrWhiteSpace($Generator)) {
    if ($null -ne $ninjaCmd) {
        $Generator = 'Ninja'
        Write-Ok "检测到 ninja: $($ninjaCmd.Source)，使用 Ninja 生成器"
    }
    else {
        $Generator = Get-DefaultVisualStudioGenerator -CMakePath $CMakeExe
        if ([string]::IsNullOrWhiteSpace($Generator)) {
            Fail -Message 'ninja 不可用，且没有找到可用的 Visual Studio 生成器。' -Hint @(
                '安装 ninja：uv tool install ninja',
                '或安装 Visual Studio 的 C++ 桌面开发工作负载，再用 -Generator 指定生成器。',
                '可先用 cmake --help 查看当前环境可用的生成器名。'
            )
        }
        Write-Warn 'ninja 不可用，使用 Visual Studio 生成器。'
        Write-Info '两个选择：'
        Write-Info '  A. 用 VS 生成器（本脚本默认）：不需要 ninja，也不需要手工 call vcvars64.bat。'
        Write-Info '     注意 VS 是多配置生成器：必须用 cmake --build --config 指定配置，'
        Write-Info '     传给 CMake 的 -DCMAKE_BUILD_TYPE 会被忽略。'
        Write-Info '  B. 先安装 ninja 以缩短构建时间（可选）：'
        Write-Info '     uv tool install ninja      或      winget install Ninja-build.Ninja'
        Write-Info '     装完重跑本脚本即可自动切到 Ninja。'
        Write-Info '若自动选择的版本不符合需要，可用 -Generator 显式指定。'
    }
}
else {
    Write-Info "使用指定的生成器: $Generator"
    if ($Generator -ieq 'Ninja' -and $null -eq $ninjaCmd) {
        Fail -Message '指定了 Ninja 生成器，但 PATH 中找不到 ninja。' -Hint @(
            '安装方式：uv tool install ninja',
            '或不指定生成器，让脚本自动选择 Visual Studio 生成器。'
        )
    }
}

$isMultiConfig = ($Generator -like 'Visual Studio*') -or ($Generator -like 'Xcode*')
if ($Jobs -le 0) { $Jobs = [Environment]::ProcessorCount }
Write-Info "生成器=$Generator  多配置=$isMultiConfig  构建类型=$BuildType  并行=$Jobs"

# ============================================================================
# 步骤 6：CMake 配置
# ============================================================================
Write-Step 'CMake 配置'
New-Item -ItemType Directory -Force -Path $BuildDir | Out-Null
New-Item -ItemType Directory -Force -Path $PluginStageDir | Out-Null

# 关键：FLUENTUI_QML_PLUGIN_DIRECTORY 不指定时，上游会默认写到
# ${Qt6_DIR}/../../../qml/FluentUI，也就是直接污染共享的 Qt SDK 安装目录，
# 产物不在本项目控制范围内，无法哈希、无法追溯。必须显式指向本目录下的受控路径。
$configureArgs = @(
    '-S', $SourceDir
    '-B', $BuildDir
    '-G', $Generator
    "-DCMAKE_PREFIX_PATH=$QtDir"
    "-DCMAKE_INSTALL_PREFIX=$InstallDir"
    "-DFLUENTUI_QML_PLUGIN_DIRECTORY=$PluginStageDir\FluentUI"
    '-DFLUENTUI_BUILD_EXAMPLES=OFF'
    "-DFLUENTUI_BUILD_STATIC_LIB=$(if ($BuildStaticLib) { 'ON' } else { 'OFF' })"
)
if (-not $isMultiConfig) {
    $configureArgs += "-DCMAKE_BUILD_TYPE=$BuildType"
    if ($null -ne $clCmd) { $configureArgs += @('-DCMAKE_C_COMPILER=cl', '-DCMAKE_CXX_COMPILER=cl') }
}
else {
    $configureArgs += @('-A', 'x64')
}

if ($PSCmdlet.ShouldProcess($BuildDir, 'cmake 配置 FluentUI 插件')) {
    Invoke-Native -FilePath $CMakeExe -Arguments $configureArgs -FailureMessage 'CMake 配置失败'
}
else { Write-Info "(-WhatIf) 将执行: $CMakeExe $($configureArgs -join ' ')" }

# ============================================================================
# 步骤 7：编译
# ============================================================================
Write-Step '编译'
# 只编译插件 target；example 依赖 Svg / Network 等额外模块，已用
# -DFLUENTUI_BUILD_EXAMPLES=OFF 排除，这里再显式指定 target 双重保险。
$pluginTarget = if ($BuildStaticLib) { 'fluentui' } else { 'fluentuiplugin' }
$buildArgs = @('--build', $BuildDir, '--target', $pluginTarget, '--parallel', "$Jobs")
if ($isMultiConfig) { $buildArgs += @('--config', $BuildType) }

if ($PSCmdlet.ShouldProcess($BuildDir, "cmake 编译 $pluginTarget")) {
    Invoke-Native -FilePath $CMakeExe -Arguments $buildArgs -FailureMessage '编译失败'
}
else { Write-Info "(-WhatIf) 将执行: $CMakeExe $($buildArgs -join ' ')" }

# ============================================================================
# 步骤 8：安装
# ============================================================================
if (-not $SkipInstall) {
    Write-Step '安装到 third_party/_install'
    $installArgs = @('--install', $BuildDir, '--prefix', $InstallDir)
    if ($isMultiConfig) { $installArgs += @('--config', $BuildType) }

    if ($PSCmdlet.ShouldProcess($InstallDir, 'cmake 安装插件产物')) {
        Invoke-Native -FilePath $CMakeExe -Arguments $installArgs -FailureMessage '安装失败'
    }
    else { Write-Info "(-WhatIf) 将执行: $CMakeExe $($installArgs -join ' ')" }
}

# ============================================================================
# 步骤 7.5：把插件 DLL 放进 QML 模块目录
# ============================================================================
# Visual Studio 生成器把 fluentuiplugin.dll 放在 _build/src/Release（或 Debug），
# 而 QML 模块目录只拿到 qmldir/qmltypes 与 .lib/.exp。qmldir 里的 `plugin fluentuiplugin`
# 找不到 DLL 时，QML 引擎在 import FluentUI 处直接失败，整个 App.qml 起不来。
if (-not $WhatIfPreference) {
    $QmlPluginDir = Join-Path $PluginStageDir 'FluentUI'
    if (-not (Test-Path -LiteralPath (Join-Path $QmlPluginDir 'qmldir'))) {
        Fail -Message "QML 模块目录缺少 qmldir：$QmlPluginDir" -Hint @(
            '检查 -DFLUENTUI_QML_PLUGIN_DIRECTORY 是否指向 third_party/_install/qml/FluentUI。'
        )
    }
    $pluginCandidates = @()
    $pluginCandidates += @(Get-ChildItem -LiteralPath $QmlPluginDir -Filter 'fluentuiplugin.dll' -File -ErrorAction SilentlyContinue)
    $pluginCandidates += @(Get-ChildItem -LiteralPath $BuildDir -Recurse -Filter 'fluentuiplugin.dll' -File -ErrorAction SilentlyContinue)
    $plugin = @($pluginCandidates | Sort-Object LastWriteTime -Descending | Select-Object -First 1)
    if (@($plugin).Count -eq 0) {
        Fail -Message '编译结束但找不到 fluentuiplugin.dll（QML 模块目录与构建目录都已搜索）' -Hint @(
            "QML 目录: $QmlPluginDir"
            "构建目录: $BuildDir"
            '检查上方 cmake --build 是否真的产出了 fluentuiplugin target。'
        )
    }
    $pluginTarget = Join-Path $QmlPluginDir 'fluentuiplugin.dll'
    if ($plugin[0].FullName -ne $pluginTarget) {
        Write-Info "复制插件: $($plugin[0].FullName) -> $pluginTarget"
        Copy-Item -LiteralPath $plugin[0].FullName -Destination $pluginTarget -Force
    }
    if (-not (Test-Path -LiteralPath $pluginTarget)) {
        Fail -Message "插件没有落到 QML 模块目录：$pluginTarget"
    }
    Write-Ok "插件已就位: $pluginTarget ($((Get-Item -LiteralPath $pluginTarget).Length) bytes)"
}

# -WhatIf 下什么都没构建，直接收尾，避免把「空产物」误判为失败。
if ($WhatIfPreference) {
    Write-Host ''
    Write-Host '(-WhatIf) 演练结束：未执行任何构建，未生成产物清单。' -ForegroundColor Green
    exit 0
}

# ============================================================================
# 步骤 9：产物清单与 SHA256 -> third_party/BUILD_PROVENANCE.md
# ============================================================================
Write-Step '收集产物并计算 SHA256'

$binaryExt = @('.dll', '.so', '.dylib', '.lib', '.a', '.pdb')
$roots = @(@($InstallDir) | Where-Object { Test-Path -LiteralPath $_ })
if (@($roots).Count -eq 0) {
    Fail -Message '未找到任何产物目录（_stage 与 _install 都不存在）。' -Hint @(
        '若使用了 -SkipInstall，请检查 _stage 下是否生成了插件。',
        '若编译看似成功却无产物，检查 FLUENTUI_QML_PLUGIN_DIRECTORY 是否指向 third_party/_install/qml/FluentUI。'
    )
}

$artifacts = @()
foreach ($r in $roots) {
    $artifacts += @(Get-ChildItem -LiteralPath $r -Recurse -File -ErrorAction SilentlyContinue)
}
if (@($artifacts).Count -eq 0) {
    Fail -Message '产物目录存在但为空，构建结果不符合预期。' -Hint @('请检查上方 cmake 输出。')
}

$manifest = @()
foreach ($f in $artifacts) {
    $manifest += [pscustomobject]@{
        Path   = $f.FullName.Substring($ThirdPartyDir.Length).TrimStart('\')
        Size   = $f.Length
        Sha256 = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
}
$manifest = @($manifest | Sort-Object Path)
$binaries = @($manifest | Where-Object { $binaryExt -contains ([System.IO.Path]::GetExtension($_.Path)).ToLowerInvariant() })

Write-Host ''
Write-Host '  关键产物（二进制与模块元数据）：' -ForegroundColor White
foreach ($b in $binaries) {
    Write-Host ("    {0,-56} {1,10}  {2}" -f $b.Path, $b.Size, $b.Sha256)
}
Write-Host ("  其余 QML/资源/翻译文件 {0} 个，已一并记入清单。" -f (@($manifest).Count - @($binaries).Count))

$clVersion = 'unknown'
if ($null -ne $clCmd) { $clVersion = ((Get-NativeLine -FilePath 'cl.exe' -Arguments @()) -split "`r?`n")[0].Trim() }

$lines = New-Object System.Collections.Generic.List[string]
$lines.Add('# 构建产物来源记录（BUILD PROVENANCE）')
$lines.Add('')
$lines.Add('本文件由 `scripts/build_fluentui.ps1` 自动生成，用于满足项目规则')
$lines.Add('「第三方依赖由本项目自行编译并记录来源与哈希」。请勿手工编辑。')
$lines.Add('')
$lines.Add('## 构建环境')
$lines.Add('')
$lines.Add('| 项 | 值 |')
$lines.Add('| --- | --- |')
$lines.Add("| 生成时间 | $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss zzz') |")
$lines.Add('| 平台 | Windows |')
$lines.Add('| 源码仓库 | https://github.com/zhuzichu520/FluentUI |')
$lines.Add("| 源码 commit | ``$srcHead`` |")
$lines.Add("| 预期插件版本 | $ExpectedPluginVersion |")
$lines.Add("| Qt 版本 | $qtVersion |")
$lines.Add("| CMake 生成器 | ``$Generator`` |")
$lines.Add("| 构建类型 | ``$BuildType`` |")
$lines.Add("| CMake | $cmakeVersion |")
$lines.Add("| 编译器 | $clVersion |")
$lines.Add("| 静态库 | $(if ($BuildStaticLib) { 'ON' } else { 'OFF' }) |")
$lines.Add('')
$lines.Add('CMake 配置参数（路径以仓库根与 Qt SDK 代替）：')
$lines.Add('')
$lines.Add('```')
$lines.Add("cmake -S third_party/FluentUI -B third_party/_build -G `"$Generator`"")
$lines.Add('      -DCMAKE_PREFIX_PATH=<Qt SDK> -DCMAKE_INSTALL_PREFIX=third_party/_install')
$lines.Add('      -DFLUENTUI_QML_PLUGIN_DIRECTORY=third_party/_install/qml/FluentUI')
$lines.Add("      -DFLUENTUI_BUILD_EXAMPLES=OFF -DFLUENTUI_BUILD_STATIC_LIB=$(if ($BuildStaticLib) { 'ON' } else { 'OFF' })")
$lines.Add('```')
$lines.Add('')
$lines.Add("## 产物清单（共 $($manifest.Count) 个文件）")
$lines.Add('')
$lines.Add('| 相对路径 (third_party/) | 字节 | SHA256 |')
$lines.Add('| --- | ---: | --- |')
foreach ($mf in $manifest) { $lines.Add("| ``$($mf.Path)`` | $($mf.Size) | ``$($mf.Sha256)`` |") }
$lines.Add('')
$lines.Add('## 合规说明')
$lines.Add('')
$lines.Add('- 上述产物全部由本项目从 pin 的 commit 编译产生，未下载任何上游预编译二进制。')
$lines.Add('- 上游 GitHub Releases 的资产均为演示程序安装包，本项目未使用其中任何文件。')
$lines.Add('- 上游源码树 3rdparty/ 下签入的 OpenSSL 与 MinGW DLL 未参与构建、未被使用。')
$lines.Add('- 分发时须随包附带两个 MIT 许可全文，见 third_party/PROVENANCE.md 第 5 节。')
$lines.Add('')

# 用不带 BOM 的 UTF-8 写 markdown，避免某些工具把 BOM 当正文
[System.IO.File]::WriteAllLines($ProvenanceFile, $lines, (New-Object System.Text.UTF8Encoding($false)))
Write-Ok "已写入 $ProvenanceFile"
Write-Warn '注意：third_party/BUILD_PROVENANCE.md 当前被 .gitignore 忽略（规则 third_party/*）。'
Write-Warn '      若需入库，请由维护者在 .gitignore 追加一行：!third_party/BUILD_PROVENANCE.md'

Write-Host ''
Write-Host '全部完成。' -ForegroundColor Green
exit 0
