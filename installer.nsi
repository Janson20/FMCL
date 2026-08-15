; FMCL Windows Installer - NSIS Script
; 使用方法: makensis /DVERSION=x.x.x installer.nsi           (x64)
;          makensis /DVERSION=x.x.x /DARCH=x86 installer.nsi (x86)

Unicode true

!define PRODUCT_NAME "FMCL"
!define PRODUCT_PUBLISHER "FMCL Team"
!define PRODUCT_WEB_SITE "https://github.com/Janson20/FMCL"
!define PRODUCT_DIR_REGKEY "Software\Microsoft\Windows\CurrentVersion\App Paths\FMCL.exe"
!define PRODUCT_UNINST_KEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\${PRODUCT_NAME}"

; 如果没有传入 VERSION，使用默认值
!ifndef VERSION
  !define VERSION "2.0.2"
!endif

; 架构（CI 传入 /DARCH=x86；默认 x64）。决定内嵌的 .NET SDK 与 7-Zip 位宽
!ifndef ARCH
  !define ARCH "x64"
!endif

; 主程序构建产物文件名（x86 构建产物为 FMCL-x86.exe，统一安装为 FMCL.exe）
!ifndef FCL_EXE
  !define FCL_EXE "FMCL.exe"
!endif

; 不带 .NET SDK 的 x64 精简版（CI 传入 /DNO_DOTNET_SDK，供自动更新默认下载）
!ifdef NO_DOTNET_SDK
  OutFile "FMCL-Setup-${VERSION}-without-dotnetsdk.exe"
!else if "${ARCH}" == "x86"
  OutFile "FMCL-Setup-${VERSION}-x86.exe"
!else
  OutFile "FMCL-Setup-${VERSION}.exe"
!endif

Name "${PRODUCT_NAME} ${VERSION}"
; 按当前用户安装到 %LOCALAPPDATA%\Programs\FMCL
; 安装到 Program Files 需要管理员权限，且普通用户无法在安装目录写入
; .minecraft / config.json 等数据，导致启动器必须以管理员身份运行
; （issue #10）。改为用户目录后安装与使用均无需管理员权限。
InstallDir "$LOCALAPPDATA\Programs\FMCL"
InstallDirRegKey HKCU "${PRODUCT_DIR_REGKEY}" ""
ShowInstDetails show
ShowUnInstDetails show
RequestExecutionLevel user

; 现代UI
!include "MUI2.nsh"
!include "LogicLib.nsh"
!include "x64.nsh"

!define MUI_ABORTWARNING
!define MUI_ICON "${NSISDIR}\Contrib\Graphics\Icons\modern-install.ico"
!define MUI_UNICON "${NSISDIR}\Contrib\Graphics\Icons\modern-uninstall.ico"

; 页面
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_WELCOME
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH

!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

Section "MainSection" SEC01
  SetOutPath "$INSTDIR"
  SetOverwrite on

  ; 复制主程序（x86 构建产物为 FMCL-x86.exe，统一安装为 FMCL.exe）
  File "/oname=FMCL.exe" "dist\${FCL_EXE}"

  ; 创建 .minecraft 目录
  CreateDirectory "$INSTDIR\.minecraft"

  ; 创建快捷方式
  CreateShortCut "$DESKTOP\FMCL.lnk" "$INSTDIR\FMCL.exe"
  CreateDirectory "$SMPROGRAMS\${PRODUCT_NAME}"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\FMCL.lnk" "$INSTDIR\FMCL.exe"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall.lnk" "$INSTDIR\uninst.exe"
SectionEnd

Section -AdditionalIcons
  WriteIniStr "$INSTDIR\${PRODUCT_NAME}.url" "InternetShortcut" "URL" "${PRODUCT_WEB_SITE}"
  CreateShortCut "$SMPROGRAMS\${PRODUCT_NAME}\Website.lnk" "$INSTDIR\${PRODUCT_NAME}.url"
SectionEnd

Section -Post
  WriteUninstaller "$INSTDIR\uninst.exe"
  WriteRegStr HKCU "${PRODUCT_DIR_REGKEY}" "" "$INSTDIR\FMCL.exe"
  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "DisplayName" "$(^Name)"
  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\uninst.exe"
  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "URLInfoAbout" "${PRODUCT_WEB_SITE}"
  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "${PRODUCT_UNINST_KEY}" "DisplayIcon" "$INSTDIR\FMCL.exe"
SectionEnd

Section "-7ZipCheck" SEC07Z
  SetOutPath "$INSTDIR"
  DetailPrint "检查 7-Zip 安装状态..."

  ; 先检查 64 位注册表视图（仅 64 位系统）
  ${If} ${RunningX64}
    SetRegView 64
    ReadRegStr $0 HKLM "SOFTWARE\7-Zip" "Path"
    SetRegView 32
    ${If} $0 != ""
      IfFileExists "$0\7z.exe" 0 check_user_reg
      DetailPrint "检测到 7-Zip 已安装 (64-bit): $0"
      Goto sevenz_done
    ${EndIf}
  ${EndIf}

check_user_reg:
  ; 检查按用户安装的 7-Zip（HKCU 注册表视图）
  ReadRegStr $0 HKCU "SOFTWARE\7-Zip" "Path"
  ${If} $0 != ""
    IfFileExists "$0\7z.exe" 0 check_32bit_reg
    DetailPrint "检测到 7-Zip 已安装 (user): $0"
    Goto sevenz_done
  ${EndIf}

check_32bit_reg:
  ; 检查 32 位注册表视图（32 位 NSIS 默认视图，自动对应 WOW6432Node）
  ReadRegStr $0 HKLM "SOFTWARE\7-Zip" "Path"
  ${If} $0 != ""
    IfFileExists "$0\7z.exe" 0 check_path
    DetailPrint "检测到 7-Zip 已安装 (32-bit): $0"
    Goto sevenz_done
  ${EndIf}

check_path:
  StrCpy $0 "$PROGRAMFILES\7-Zip\7z.exe"
  IfFileExists $0 sevenz_done
  StrCpy $0 "$PROGRAMFILES32\7-Zip\7z.exe"
  IfFileExists $0 sevenz_done
  StrCpy $0 "$PROGRAMFILES64\7-Zip\7z.exe"
  IfFileExists $0 sevenz_done
  StrCpy $0 "$LOCALAPPDATA\Programs\7-Zip\7z.exe"
  IfFileExists $0 sevenz_done

  DetailPrint "未检测到 7-Zip，正在从安装包中安装..."
  MessageBox MB_OK "FMCL 预下载功能需要 7-Zip 来解压资源包。$\n$\n点击确定后将自动安装 7-Zip（静默安装）。" /SD IDOK

  SetOutPath "$TEMP\FMCLauncher_7z"
  !if "${ARCH}" == "x86"
    File "7z_installers\7z2602.exe"
    StrCpy $2 "$TEMP\FMCLauncher_7z\7z2602.exe"
  !else
    File "7z_installers\7z2602-x64.exe"
    StrCpy $2 "$TEMP\FMCLauncher_7z\7z2602-x64.exe"
  !endif
  SetOutPath "$INSTDIR"

  DetailPrint "正在静默安装 7-Zip..."
  ExecWait '"$2" /S' $1

  RMDir /r "$TEMP\FMCLauncher_7z"

  ${If} $1 != 0
    DetailPrint "7-Zip 安装程序执行失败 (exit code: $1)"
    MessageBox MB_ICONEXCLAMATION "7-Zip 安装失败 (错误码: $1)。$\n$\n请手动安装: https://7-zip.org/$\n$\nFMCL 仍可正常使用，但预下载功能需要 7-Zip 解压 RAR 文件。" /SD IDOK
    Goto sevenz_done
  ${EndIf}

  Sleep 2000

  ; 安装后验证 — 同样需要检查两种注册表视图与用户安装路径
  ${If} ${RunningX64}
    SetRegView 64
    ReadRegStr $0 HKLM "SOFTWARE\7-Zip" "Path"
    SetRegView 32
    ${If} $0 != ""
      IfFileExists "$0\7z.exe" 0 verify_user_reg
      DetailPrint "7-Zip 安装成功 (64-bit): $0"
      Goto sevenz_done
    ${EndIf}
  ${EndIf}

verify_user_reg:
  ReadRegStr $0 HKCU "SOFTWARE\7-Zip" "Path"
  ${If} $0 != ""
    IfFileExists "$0\7z.exe" 0 verify_32bit_reg
    DetailPrint "7-Zip 安装成功 (user): $0"
    Goto sevenz_done
  ${EndIf}

verify_32bit_reg:
  ReadRegStr $0 HKLM "SOFTWARE\7-Zip" "Path"
  ${If} $0 != ""
    IfFileExists "$0\7z.exe" 0 verify_path
    DetailPrint "7-Zip 安装成功 (32-bit): $0"
    Goto sevenz_done
  ${EndIf}

verify_path:
  StrCpy $0 "$PROGRAMFILES\7-Zip\7z.exe"
  IfFileExists $0 verify_found
  StrCpy $0 "$PROGRAMFILES32\7-Zip\7z.exe"
  IfFileExists $0 verify_found
  StrCpy $0 "$PROGRAMFILES64\7-Zip\7z.exe"
  IfFileExists $0 verify_found
  StrCpy $0 "$LOCALAPPDATA\Programs\7-Zip\7z.exe"
  IfFileExists $0 verify_found

  DetailPrint "7-Zip 安装验证失败，请手动安装"
  MessageBox MB_ICONEXCLAMATION "7-Zip 安装可能未成功。$\n$\n请手动安装: https://7-zip.org/$\n$\nFMCL 仍可正常使用，但预下载功能需要 7-Zip 解压 RAR 文件。" /SD IDOK
  Goto sevenz_done

verify_found:
  DetailPrint "7-Zip 安装成功: $0"

sevenz_done:
  DetailPrint "7-Zip 检查完成"
SectionEnd

; ─── .NET 10 SDK 检测 ───────────────────────────────────────
; 输出: $0 = 1 已检测到 .NET 10 SDK，0 未检测到
; 检测顺序：文件系统（%ProgramFiles%\dotnet\sdk\10.*）→ 注册表双视图
; （SDK 安装器是 32 位进程，版本子键写在 WOW6432Node 即 32 位视图下）
; 精简版（NO_DOTNET_SDK）不内嵌 SDK，整段跳过
!ifndef NO_DOTNET_SDK
Function CheckDotnetSdk10
  StrCpy $0 0
  ; 1) 文件系统（支持通配符）：x64 / x86 安装位置全覆盖
  IfFileExists "$PROGRAMFILES64\dotnet\sdk\10.*" sdk_found
  IfFileExists "$PROGRAMFILES32\dotnet\sdk\10.*" sdk_found
  IfFileExists "$PROGRAMFILES\dotnet\sdk\10.*" sdk_found
  ; 2) 注册表：先 32 位视图（WOW6432Node，SDK 安装器写入处）
  SetRegView 32
  StrCpy $1 0
  ${Do}
    EnumRegKey $2 HKLM "SOFTWARE\dotnet\Setup\InstalledVersions\x64\sdk" $1
    StrCmp $2 "" check_sdk_reg_x86_32
    StrCpy $3 $2 3
    StrCmp $3 "10." sdk_found
    IntOp $1 $1 + 1
  ${Loop}
check_sdk_reg_x86_32:
  StrCpy $1 0
  ${Do}
    EnumRegKey $2 HKLM "SOFTWARE\dotnet\Setup\InstalledVersions\x86\sdk" $1
    StrCmp $2 "" check_sdk_reg_x64_64
    StrCpy $3 $2 3
    StrCmp $3 "10." sdk_found
    IntOp $1 $1 + 1
  ${Loop}
check_sdk_reg_x64_64:
  ; 3) 注册表：64 位视图兜底
  SetRegView 64
  StrCpy $1 0
  ${Do}
    EnumRegKey $2 HKLM "SOFTWARE\dotnet\Setup\InstalledVersions\x64\sdk" $1
    StrCmp $2 "" check_sdk_reg_x86_64
    StrCpy $3 $2 3
    StrCmp $3 "10." sdk_found
    IntOp $1 $1 + 1
  ${Loop}
check_sdk_reg_x86_64:
  StrCpy $1 0
  ${Do}
    EnumRegKey $2 HKLM "SOFTWARE\dotnet\Setup\InstalledVersions\x86\sdk" $1
    StrCmp $2 "" sdk_not_found
    StrCpy $3 $2 3
    StrCmp $3 "10." sdk_found
    IntOp $1 $1 + 1
  ${Loop}
sdk_found:
  StrCpy $0 1
sdk_not_found:
  SetRegView 32
FunctionEnd

Section "-DotnetSdkCheck" SECDOTNET
  SetOutPath "$TEMP\FMCLauncher_dotnet"
  DetailPrint "检查 .NET 10 SDK 安装状态..."

  Call CheckDotnetSdk10
  ${If} $0 = 1
    DetailPrint "已检测到 .NET 10 SDK"
    Goto dotnet_done
  ${EndIf}

  ; 未检测到：询问是否静默安装内嵌的 SDK（需要 UAC 提权）
  MessageBox MB_YESNO "未检测到 .NET 10 SDK。$\n$\n基岩版 GDK 功能需要它，是否现在静默安装？（约 200MB，需要管理员权限）" IDNO dotnet_done

  !if "${ARCH}" == "x86"
    File "dotnet_sdk\dotnet-sdk-win-x86.exe"
    StrCpy $4 "$TEMP\FMCLauncher_dotnet\dotnet-sdk-win-x86.exe"
  !else
    File "dotnet_sdk\dotnet-sdk-win-x64.exe"
    StrCpy $4 "$TEMP\FMCLauncher_dotnet\dotnet-sdk-win-x64.exe"
  !endif

  DetailPrint "正在安装 .NET 10 SDK（请在 UAC 弹窗中确认）..."
  ; 同步等待安装完成并获取退出码（PowerShell Start-Process -Verb RunAs -Wait 传播退出码）
  ; 注意：$$ = 字面 $，$\' = 字面单引号（NSIS 转义序列）
  ExecWait '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -Command "& { $$p = Start-Process -FilePath $\'$4$\' -ArgumentList $\'/install /quiet /norestart$\' -Verb RunAs -Wait -PassThru; exit $$p.ExitCode }"' $1

  ; 安装完成后复查（文件系统/注册表）
  Call CheckDotnetSdk10
  ${If} $0 = 1
    DetailPrint ".NET 10 SDK 安装成功"
    MessageBox MB_OK ".NET 10 SDK 安装成功。$\n$\n现在可以使用基岩版 GDK 功能了。" /SD IDOK
  ${Else}
    DetailPrint ".NET 10 SDK 安装失败 (exit code: $1)"
    MessageBox MB_ICONEXCLAMATION ".NET 10 SDK 安装未成功（退出码: $1）。$\n$\n安装日志位于 %TEMP%\dd_*.log$\n$\nFMCL 其余功能不受影响，GDK 版可在启动器中另行引导下载安装。" /SD IDOK
  ${EndIf}

dotnet_done:
  SetOutPath "$INSTDIR"
SectionEnd
!endif

Section Uninstall
  Delete "$INSTDIR\${PRODUCT_NAME}.url"
  Delete "$INSTDIR\uninst.exe"
  Delete "$INSTDIR\FMCL.exe"

  Delete "$SMPROGRAMS\${PRODUCT_NAME}\Uninstall.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\Website.lnk"
  Delete "$SMPROGRAMS\${PRODUCT_NAME}\FMCL.lnk"
  Delete "$DESKTOP\FMCL.lnk"

  RMDir "$SMPROGRAMS\${PRODUCT_NAME}"
  RMDir /r "$INSTDIR\.minecraft"
  RMDir "$INSTDIR"

  DeleteRegKey HKCU "${PRODUCT_UNINST_KEY}"
  DeleteRegKey HKCU "${PRODUCT_DIR_REGKEY}"
  SetAutoClose true
SectionEnd
