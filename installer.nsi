; FMCL Windows Installer - NSIS Script
; 使用方法: makensis /DVERSION=x.x.x installer.nsi

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

Name "${PRODUCT_NAME} ${VERSION}"
OutFile "FMCL-Setup-${VERSION}.exe"
InstallDir "$PROGRAMFILES\${PRODUCT_NAME}"
InstallDirRegKey HKLM "${PRODUCT_DIR_REGKEY}" ""
ShowInstDetails show
ShowUnInstDetails show
RequestExecutionLevel admin

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

  ; 复制主程序
  File "dist\FMCL.exe"

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
  WriteRegStr HKLM "${PRODUCT_DIR_REGKEY}" "" "$INSTDIR\FMCL.exe"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayName" "$(^Name)"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "UninstallString" "$INSTDIR\uninst.exe"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "URLInfoAbout" "${PRODUCT_WEB_SITE}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKLM "${PRODUCT_UNINST_KEY}" "DisplayIcon" "$INSTDIR\FMCL.exe"
SectionEnd

Section "-7ZipCheck" SEC07Z
  SetOutPath "$INSTDIR"
  DetailPrint "检查 7-Zip 安装状态..."

  ReadRegStr $0 HKLM "SOFTWARE\7-Zip" "Path"
  ${If} $0 != ""
    IfFileExists "$0\7z.exe" 0 check_wow64
    DetailPrint "检测到 7-Zip 已安装: $0"
    Goto sevenz_done
  ${EndIf}

check_wow64:
  ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\7-Zip" "Path"
  ${If} $0 != ""
    IfFileExists "$0\7z.exe" 0 check_path
    DetailPrint "检测到 7-Zip 已安装 (WOW64): $0"
    Goto sevenz_done
  ${EndIf}

check_path:
  StrCpy $0 "$PROGRAMFILES\7-Zip\7z.exe"
  IfFileExists $0 sevenz_done
  StrCpy $0 "$PROGRAMFILES32\7-Zip\7z.exe"
  IfFileExists $0 sevenz_done
  StrCpy $0 "$PROGRAMFILES64\7-Zip\7z.exe"
  IfFileExists $0 sevenz_done

  DetailPrint "未检测到 7-Zip，正在从安装包中安装..."
  MessageBox MB_OK "FMCL 预下载功能需要 7-Zip 来解压资源包。$\n$\n点击确定后将自动安装 7-Zip（静默安装）。" /SD IDOK

  SetOutPath "$TEMP\FMCLauncher_7z"
  ${If} ${RunningX64}
    File "7z_installers\7z2409-x64.exe"
    StrCpy $2 "$TEMP\FMCLauncher_7z\7z2409-x64.exe"
  ${Else}
    File "7z_installers\7z2409.exe"
    StrCpy $2 "$TEMP\FMCLauncher_7z\7z2409.exe"
  ${EndIf}
  SetOutPath "$INSTDIR"

  DetailPrint "正在静默安装 7-Zip..."
  ExecWait '"$2" /S' $0

  RMDir /r "$TEMP\FMCLauncher_7z"

  Sleep 2000

  ReadRegStr $0 HKLM "SOFTWARE\7-Zip" "Path"
  ${If} $0 == ""
    ReadRegStr $0 HKLM "SOFTWARE\WOW6432Node\7-Zip" "Path"
  ${EndIf}

  ${If} $0 != ""
    DetailPrint "7-Zip 安装成功: $0"
  ${Else}
    DetailPrint "7-Zip 安装验证失败，请手动安装"
    MessageBox MB_ICONEXCLAMATION "7-Zip 安装可能未成功。$\n$\n请手动安装: https://7-zip.org/$\n$\nFMCL 仍可正常使用，但预下载功能需要 7-Zip 解压 RAR 文件。" /SD IDOK
  ${EndIf}

sevenz_done:
  DetailPrint "7-Zip 检查完成"
SectionEnd

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

  DeleteRegKey HKLM "${PRODUCT_UNINST_KEY}"
  DeleteRegKey HKLM "${PRODUCT_DIR_REGKEY}"
  SetAutoClose true
SectionEnd
