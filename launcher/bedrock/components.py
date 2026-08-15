"""GDK 认证闭源组件按需下载

XUserLauncher.Core（内部嵌入 XUserHook.dll 资源）为 BedrockBoot 官方发布的
闭源组件，随 FMCL 源码/安装包分发违反其授权要求，因此改为运行时按需下载：
- 来源：BedrockBoot 官方 NuGet 包 xuserlauncher.core（发布者 Round-Studio）
- 时机：首次使用 GDK 认证注入启动且组件缺失时，经用户明确同意后下载
- 落盘：launcher/bedrock/native/assets/XUserLauncher.Core.dll（该目录不入库）
- 校验：包内 lib/ 下提取 XUserLauncher.Core.dll，检查 PE 文件头（MZ）
"""

import io
import json
import os
import zipfile
from pathlib import Path
from typing import Callable, Optional

import requests
from logzero import logger

from launcher.bedrock.source import USER_AGENT

PACKAGE_ID = "xuserlauncher.core"
# NuGet flatcontainer：版本列表 / 包文件下载（官方分发渠道）
FLATCONTAINER_INDEX_URL = f"https://api.nuget.org/v3-flatcontainer/{PACKAGE_ID}/index.json"
FLATCONTAINER_NUPKG_URL = (
    "https://api.nuget.org/v3-flatcontainer/{id}/{version}/{id}.{version}.nupkg"
)
PACKAGE_AUTHORS = ("Round-Studio",)

ASSETS_DIR = Path(__file__).resolve().parent / "native" / "assets"
TARGET_FILENAME = "XUserLauncher.Core.dll"
_DLL_IN_PACKAGE = "XUserLauncher.Core.dll"

_REQUEST_TIMEOUT = 60


class ComponentError(RuntimeError):
    """认证组件下载错误"""


def asset_path() -> Path:
    """组件落盘路径"""
    return ASSETS_DIR / TARGET_FILENAME


def is_ready() -> bool:
    """组件是否已就绪"""
    try:
        return asset_path().is_file() and asset_path().stat().st_size > 0
    except OSError:
        return False


def _get(url: str) -> bytes:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as e:
        raise ComponentError(f"请求失败 [{url}]: {e}") from e


def _latest_version() -> str:
    """从官方 NuGet flatcontainer 获取最新稳定版本号"""
    raw = _get(FLATCONTAINER_INDEX_URL)
    try:
        payload = json.loads(raw)
        # flatcontainer 版本索引格式: {"versions": ["1.0.0.1", ...]}
        versions = [v for v in payload.get("versions", []) if not any(c in v for c in "-+")]
    except (ValueError, AttributeError, KeyError) as e:
        raise ComponentError(f"解析组件版本列表失败: {e}") from e
    if not versions:
        raise ComponentError(f"组件 {PACKAGE_ID} 无可用稳定版本")
    return versions[-1]


def _extract_dll(nupkg: bytes) -> bytes:
    """从 nupkg 中提取 lib/ 下的目标 DLL，校验 PE 头"""
    try:
        with zipfile.ZipFile(io.BytesIO(nupkg)) as zf:
            candidates = [
                name for name in zf.namelist()
                if name.startswith("lib/") and name.endswith("/" + _DLL_IN_PACKAGE)
            ]
            if not candidates:
                raise ComponentError(f"组件包内未找到 {_DLL_IN_PACKAGE}（lib/ 目录缺失）")
            # 优先取 TFM 等级最高（路径段最多）的 lib 目录
            candidates.sort(key=lambda n: len(n.split("/")), reverse=True)
            data = zf.read(candidates[0])
    except zipfile.BadZipFile as e:
        raise ComponentError(f"组件包不是有效的 zip/nupkg: {e}") from e
    if not data.startswith(b"MZ") or len(data) < 0x100:
        raise ComponentError("提取的组件文件不是有效的 PE 可执行文件")
    return data


def download(
    progress_cb: Optional[Callable[[int, int, str], None]] = None,
    status_cb: Optional[Callable[[str], None]] = None,
    force: bool = False,
) -> Path:
    """下载并安装认证组件（已就绪且非强制时直接返回现有文件）

    步骤：查询官方版本 → 下载 nupkg → 提取 DLL → 原子落盘到 assets 目录
    """
    if not force and is_ready():
        return asset_path()

    def status(text: str) -> None:
        logger.info(f"[components] {text}")
        if status_cb:
            status_cb(text)

    status("正在查询组件官方版本...")
    version = _latest_version()
    nupkg_url = FLATCONTAINER_NUPKG_URL.format(id=PACKAGE_ID, version=version)
    status(f"正在下载认证组件 v{version}（来自 BedrockBoot 官方 NuGet）...")
    nupkg = _get(nupkg_url)
    status("正在解包组件...")
    dll_data = _extract_dll(nupkg)
    if progress_cb:
        progress_cb(1, 1, "组件下载完成")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    target = asset_path()
    tmp = target.with_suffix(".dll.tmp")
    try:
        tmp.write_bytes(dll_data)
        os.replace(tmp, target)
    except OSError as e:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise ComponentError(f"写入组件文件失败: {e}") from e
    status(f"认证组件 v{version} 已就绪")
    return target
