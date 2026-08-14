"""UWP 版游戏包安装：AppX 解压、清单修改、开发者模式注册

移植自 BedrockLauncher.Core (MIT)：
- AppX 包本质为 zip，解压到版本目录
- 删除 AppxSignature.p7x（签名文件会阻止开发模式注册）
- 修改 AppxManifest.xml（版本号 +1、DisplayName、runFullTrust 能力、扩展等）
- 写入 CustomCapability.SCCD
- 通过 PowerShell Add-AppxPackage -Register 注册（需开发者模式）
"""

import re
import subprocess
import zipfile
from pathlib import Path
from typing import Callable, Optional

from logzero import logger

SCCD_BASE64 = (
    "PD94bWwgdmVyc2lvbj0iMS4wIiBlbmNvZGluZz0idXRmLTgiPz4KPEN1c3RvbUNhcGFiaWxpdHlEZXNjcmlwdG9yIHhtbG5zPSJodHRwOi8vc2NoZW1hcy5taWNyb3NvZnQuY29tL2FwcHgvMjAxOC9zY2NkIiB4bWxuczpzPSJodHRwOi8vc2NoZW1hcy5taWNyb3NvZnQuY29tL2FwcHgvMjAxOC9zY2NkIj4KICA8Q3VzdG9tQ2FwYWJpbGl0aWVzPgogICAgPEN1c3RvbUNhcGFiaWxpdHkgTmFtZT0iTWljcm9zb2Z0LmNvcmVBcHBBY3RpdmF0aW9uXzh3ZWt5YjNkOGJid2UiPjwvQ3VzdG9tQ2FwYWJpbGl0eT4KICA8L0N1c3RvbUNhcGFiaWxpdGllcz4KICA8QXV0aG9yaXplZEVudGl0aWVzIEFsbG93QW55PSJ0cnVlIi8+CiAgPENhdGFsb2c+RkZGRjwvQ2F0YWxvZz4KPC9DdXN0b21DYXBhYmlsaXR5RGVzY3JpcHRvcj4="
)

# 清单中需要追加的扩展（对齐 ManifestEditor：loopback 规则 + 文件类型关联）
MANIFEST_EXTENSIONS = """
        <uap4:Extension Category="windows.loopbackAccessRules">
          <uap4:LoopbackAccessRules>
            <uap4:Rule Direction="out" PackageFamilyName="Microsoft.MEECC_8wekyb3d8bbwe" />
          </uap4:LoopbackAccessRules>
        </uap4:Extension>
        <uap:Extension Category="windows.fileTypeAssociation" EntryPoint="App2">
          <uap:FileTypeAssociation Name="mcperf">
            <uap:DisplayName>MCPERF</uap:DisplayName>
            <uap:InfoTip>Launch Minecraft and import world</uap:InfoTip>
            <uap:SupportedFileTypes>
              <uap:FileType>.MCPERF</uap:FileType>
            </uap:SupportedFileTypes>
          </uap:FileTypeAssociation>
        </uap:Extension>
        <uap:Extension Category="windows.fileTypeAssociation" EntryPoint="App2">
          <uap:FileTypeAssociation Name="mcshortcut">
            <uap:DisplayName>MCSHORTCUT</uap:DisplayName>
            <uap:InfoTip>Launch Minecraft and load world</uap:InfoTip>
            <uap:SupportedFileTypes>
              <uap:FileType>.MCSHORTCUT</uap:FileType>
            </uap:SupportedFileTypes>
          </uap:FileTypeAssociation>
        </uap:Extension>
        <uap:Extension Category="windows.fileTypeAssociation" EntryPoint="App2">
          <uap:FileTypeAssociation Name="mcpack">
            <uap:DisplayName>MCPACK</uap:DisplayName>
            <uap:InfoTip>Launch Minecraft and import resource pack</uap:InfoTip>
            <uap:SupportedFileTypes>
              <uap:FileType>.MCPACK</uap:FileType>
            </uap:SupportedFileTypes>
          </uap:FileTypeAssociation>
        </uap:Extension>
        <uap:Extension Category="windows.fileTypeAssociation" EntryPoint="App2">
          <uap:FileTypeAssociation Name="mcworld">
            <uap:DisplayName>MCWORLD</uap:DisplayName>
            <uap:InfoTip>Launch Minecraft and open world</uap:InfoTip>
            <uap:SupportedFileTypes>
              <uap:FileType>.MCWORLD</uap:FileType>
            </uap:SupportedFileTypes>
          </uap:FileTypeAssociation>
        </uap:Extension>
"""


class AppxError(RuntimeError):
    """AppX 安装错误"""


def extract_appx(package_path: Path, dest_dir: Path, progress_cb: Optional[Callable[[int, int], None]] = None) -> None:
    """解压 AppX（zip 格式）到目标目录，删除签名文件"""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(package_path) as zf:
            members = zf.infolist()
            total = len(members)
            for index, member in enumerate(members):
                if member.filename.endswith("/"):
                    continue
                target = dest_dir / member.filename
                if not str(target.resolve()).startswith(str(dest_dir.resolve())):
                    raise AppxError(f"解压路径越界: {member.filename}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    while True:
                        block = src.read(1024 * 256)
                        if not block:
                            break
                        dst.write(block)
                if progress_cb and (index % 20 == 0 or index == total - 1):
                    progress_cb(index + 1, total)
    except zipfile.BadZipFile as e:
        raise AppxError(f"AppX 包不是有效的 zip: {e}") from e
    signature = dest_dir / "AppxSignature.p7x"
    if signature.exists():
        signature.unlink()


def edit_manifest(version_dir: Path, game_name: str) -> None:
    """修改 AppxManifest.xml 以支持开发模式注册（对齐 ManifestEditor.EditManifest）"""
    manifest_path = version_dir / "AppxManifest.xml"
    if not manifest_path.exists():
        raise AppxError("版本目录中缺少 AppxManifest.xml")
    text = manifest_path.read_text(encoding="utf-8")

    # 1. 版本号 +1（保证 ForceUpdateFromAnyVersion 前的增量注册）
    version_match = re.search(r'Version="(\d+)\.(\d+)\.(\d+)\.(\d+)"', text)
    if version_match:
        parts = [int(v) for v in version_match.groups()]
        parts[3] += 1
        new_version = ".".join(str(v) for v in parts)
        text = re.sub(
            r'Version="(\d+)\.(\d+)\.(\d+)\.(\d+)"', f'Version="{new_version}"', text, count=1
        )

    # 2. 扩展块整体替换（移除原扩展，写入新扩展）
    text = re.sub(r"<Extensions\b[^>]*>.*?</Extensions>", " <Extensions>\r\n" + MANIFEST_EXTENSIONS + "\r\n      </Extensions>", text, count=1, flags=re.S)
    text = re.sub(r"<Extensions\s*/>", " <Extensions>\r\n" + MANIFEST_EXTENSIONS + "\r\n      </Extensions>", text, count=1)

    # 3. 能力修改：移除 rescap Capability / uap4 CustomCapability / DeviceCapability，
    #    追加 runFullTrust 与 coreAppActivation
    cap_match = re.search(r"<Capabilities>.*?</Capabilities>", text, flags=re.S)
    if cap_match:
        caps = cap_match.group(0)
        device_caps = re.findall(r"<DeviceCapability[^>]*/>", caps)
        caps = re.sub(r"<rescap:Capability[^>]*/>", "", caps)
        caps = re.sub(r"<uap4:CustomCapability[^>]*/>", "", caps)
        caps = re.sub(r"<DeviceCapability[^>]*/>", "", caps)
        caps = caps.replace("</Capabilities>", "")
        caps += '<rescap:Capability Name="runFullTrust" />\r\n'
        caps += '<uap4:CustomCapability Name="Microsoft.coreAppActivation_8wekyb3d8bbwe" />\r\n'
        if device_caps:
            caps += "\r\n".join(device_caps)
        else:
            caps += '<DeviceCapability Name="internetClient" />\r\n'
        caps += "</Capabilities>"
        text = text[: cap_match.start()] + caps + text[cap_match.end() :]

    # 4. 命名空间声明（IgnorableNamespaces + xmlns:rescap/uap4/uap10/desktop4）
    if "xmlns:rescap" not in text:
        text = re.sub(r"(<Package[^>]*?)(\s*>)", r'\1 xmlns:rescap="http://schemas.microsoft.com/appx/manifest/foundation/windows10/restrictedcapabilities" xmlns:uap4="http://schemas.microsoft.com/appx/manifest/uap/windows10/4" xmlns:uap10="http://schemas.microsoft.com/appx/manifest/uap/windows10/10" xmlns:desktop4="http://schemas.microsoft.com/appx/manifest/desktop/windows10/4"\2', text, count=1)
    if "IgnorableNamespaces" not in text:
        text = re.sub(
            r'(<Package[^>]*?)(\s*>)',
            r'\1 IgnorableNamespaces="uap uap4 uap10 rescap desktop4"\2',
            text,
            count=1,
        )

    # 5. TrustLevel=mediumIL + SupportsMultipleInstances + AppListEntry=none
    text = re.sub(r'<Application\b', '<Application uap10:TrustLevel="mediumIL" desktop4:SupportsMultipleInstances="true"', text, count=1)
    if re.search(r'AppListEntry="[^"]*"', text):
        text = re.sub(r'AppListEntry="[^"]*"', 'AppListEntry="none"', text, count=1)
    else:
        text = re.sub(
            r'(<uap:VisualElements\b[^>]*?)(\s*/?>)',
            r'\1 AppListEntry="none"\2',
            text,
            count=1,
        )

    # 6. DisplayName 改为版本名
    if re.search(r'(<uap:VisualElements[^>]*?DisplayName=")[^"]*"', text):
        text = re.sub(r'(<uap:VisualElements[^>]*?DisplayName=")[^"]*"', rf'\g<1>{_xml_escape(game_name)}"', text, count=1)

    manifest_path.write_text(text, encoding="utf-8")

    # 7. 写入 CustomCapability.SCCD
    sccd_path = version_dir / "CustomCapability.SCCD"
    import base64

    sccd_path.write_bytes(base64.b64decode(SCCD_BASE64))


def _xml_escape(value: str) -> str:
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def register_appx(version_dir: Path, timeout: int = 180) -> None:
    """以开发模式注册未打包的 AppX（对应 RegisterPackageAsync + DevelopmentMode）"""
    manifest = version_dir / "AppxManifest.xml"
    if not manifest.exists():
        raise AppxError("版本目录中缺少 AppxManifest.xml")
    cmd = (
        "Add-AppxPackage -Register "
        f"'{str(manifest)}' -ForceApplicationShutdown -ForceUpdateFromAnyVersion"
    )
    logger.info(f"注册 AppX: {manifest}")
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise AppxError("AppX 注册超时（3 分钟）") from e
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        logger.error(f"AppX 注册失败: {detail}")
        raise AppxError(f"AppX 注册失败: {detail[:300]}")
    logger.info("AppX 注册成功")


def is_package_installed(package_name: str) -> bool:
    """检查指定名称的 AppX 包是否已注册"""
    cmd = f"Get-AppxPackage -Name '{package_name}' | Select-Object -First 1"
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
        return proc.returncode == 0 and package_name.lower() in proc.stdout.lower()
    except Exception as e:
        logger.warning(f"检查 AppX 包失败: {e}")
        return False


def get_package_install_location(package_name: str) -> Optional[str]:
    """获取已注册 AppX 包的安装路径（未安装返回 None）"""
    cmd = f"(Get-AppxPackage -Name '{package_name}' | Select-Object -First 1).InstallLocation"
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
        location = (proc.stdout or "").strip()
        return location or None
    except Exception as e:
        logger.warning(f"查询 AppX 安装路径失败: {e}")
        return None


def remove_appx_package(package_name: str) -> bool:
    """移除已注册的 AppX 包（保留应用数据）"""
    cmd = f"Get-AppxPackage -Name '{package_name}' | Remove-AppxPackage -PreserveApplicationData"
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", cmd],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=180,
        )
        return proc.returncode == 0
    except Exception as e:
        logger.warning(f"移除 AppX 包失败: {e}")
        return False
