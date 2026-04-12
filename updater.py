"""自动更新模块

从 GitHub Release 检查更新，下载合适的安装包并执行静默安装。

流程：
1. 获取 GitHub latest release 信息
2. 对比当前版本与远程版本
3. 根据当前平台选择合适的安装包
4. 下载安装包到临时目录
5. 执行静默安装（/S 或 /silent）
"""

import os
import platform
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, Callable

import requests
from logzero import logger

# GitHub 仓库信息
GITHUB_OWNER = "Janson20"
GITHUB_REPO = "MCL"
GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"

# 当前版本（从 pyproject.toml 读取）
CURRENT_VERSION = "2.2.0"


def _load_current_version() -> str:
    """从 pyproject.toml 动态读取当前版本号"""
    try:
        import re
        pyproject_path = Path(__file__).parent / "pyproject.toml"
        if pyproject_path.exists():
            content = pyproject_path.read_text(encoding="utf-8")
            match = re.search(r'version\s*=\s*"([^"]+)"', content)
            if match:
                return match.group(1)
    except Exception as e:
        logger.debug(f"读取 pyproject.toml 版本号失败: {e}")
    return CURRENT_VERSION


def get_current_version() -> str:
    """获取当前版本号"""
    return _load_current_version()


def check_for_update() -> Optional[Dict[str, Any]]:
    """
    检查 GitHub Release 是否有新版本

    Returns:
        如果有新版本，返回 release 信息字典：
        {
            "version": "x.x.x",
            "html_url": "https://github.com/...",
            "body": "release notes",
            "assets": [{"name": "...", "browser_download_url": "...", "size": 12345}, ...]
        }
        如果没有新版本或检查失败，返回 None
    """
    try:
        current = get_current_version()
        logger.info(f"检查更新: 当前版本 {current}")

        resp = requests.get(GITHUB_API_URL, timeout=10)
        resp.raise_for_status()

        release = resp.json()
        tag_name = release.get("tag_name", "")

        # 去掉 'v' 前缀
        remote_version = tag_name.lstrip("v")

        if not remote_version:
            logger.warning("无法获取远程版本号")
            return None

        if _compare_versions(remote_version, current) <= 0:
            logger.info(f"当前已是最新版本 ({current} >= {remote_version})")
            return None

        # 有新版本
        assets = []
        for asset in release.get("assets", []):
            assets.append({
                "name": asset.get("name", ""),
                "browser_download_url": asset.get("browser_download_url", ""),
                "size": asset.get("size", 0),
            })

        result = {
            "version": remote_version,
            "html_url": release.get("html_url", ""),
            "body": release.get("body", ""),
            "assets": assets,
        }

        logger.info(f"发现新版本: {remote_version}")
        return result

    except requests.exceptions.Timeout:
        logger.warning("检查更新超时")
        return None
    except requests.exceptions.ConnectionError:
        logger.warning("检查更新失败: 无法连接到 GitHub")
        return None
    except Exception as e:
        logger.error(f"检查更新失败: {e}")
        return None


def _compare_versions(v1: str, v2: str) -> int:
    """
    比较两个语义化版本号

    Returns:
        >0 如果 v1 > v2
         0 如果 v1 == v2
        <0 如果 v1 < v2
    """
    def parse(v: str):
        parts = []
        for p in v.split("."):
            try:
                parts.append(int(p))
            except ValueError:
                parts.append(0)
        return parts

    p1 = parse(v1)
    p2 = parse(v2)
    # 补齐长度
    max_len = max(len(p1), len(p2))
    p1.extend([0] * (max_len - len(p1)))
    p2.extend([0] * (max_len - len(p2)))

    for a, b in zip(p1, p2):
        if a != b:
            return a - b
    return 0


def _get_platform_asset_pattern() -> Optional[str]:
    """
    根据当前平台返回匹配的安装包文件名模式

    Returns:
        匹配模式字符串，如 "Setup-x.x.x.exe" 或 None（不支持的平台）
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "windows":
        # Windows: MCL-Setup-x.x.x.exe
        return "Setup-"
    elif system == "darwin":
        if "arm" in machine or "aarch" in machine:
            # macOS Apple Silicon: MCL-x.x.x-mac-arm64.dmg
            return "-mac-arm64.dmg"
        else:
            # macOS Intel: MCL-x.x.x-mac-amd64.dmg
            return "-mac-amd64.dmg"
    elif system == "linux":
        # Linux: 优先 AppImage，其次 deb
        return "-linux-amd64"

    return None


def find_suitable_asset(assets: list) -> Optional[Dict[str, Any]]:
    """
    从 asset 列表中找到适合当前平台的安装包

    Args:
        assets: release assets 列表

    Returns:
        匹配的 asset 字典，或 None
    """
    system = platform.system().lower()
    machine = platform.machine().lower()

    candidates = []

    for asset in assets:
        name = asset.get("name", "").lower()

        if system == "windows":
            # Windows: 匹配 Setup-xxx.exe
            if "setup" in name and name.endswith(".exe"):
                candidates.append((10, asset))
            elif name.endswith(".exe") and "setup" not in name:
                candidates.append((5, asset))

        elif system == "darwin":
            if "arm" in machine or "aarch" in machine:
                if "arm64" in name and name.endswith(".dmg"):
                    candidates.append((10, asset))
            else:
                if "amd64" in name and name.endswith(".dmg"):
                    candidates.append((10, asset))
                elif "mac" in name and name.endswith(".dmg"):
                    candidates.append((5, asset))

        elif system == "linux":
            # 优先 AppImage
            if name.endswith(".appimage"):
                candidates.append((10, asset))
            elif name.endswith(".deb"):
                candidates.append((5, asset))

    if not candidates:
        return None

    # 按优先级排序，返回最高优先级的
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def download_update(
    asset: Dict[str, Any],
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> Optional[str]:
    """
    下载更新安装包

    Args:
        asset: release asset 字典
        progress_callback: 进度回调 (已下载字节数, 总字节数)

    Returns:
        下载文件的路径，或 None（失败时）
    """
    url = asset.get("browser_download_url", "")
    filename = asset.get("name", "update installer")
    total_size = asset.get("size", 0)

    if not url:
        logger.error("下载 URL 为空")
        return None

    try:
        # 保存到临时目录
        tmp_dir = tempfile.mkdtemp(prefix="mcl_update_")
        save_path = os.path.join(tmp_dir, filename)

        logger.info(f"开始下载更新: {filename}")

        resp = requests.get(url, stream=True, timeout=60)
        resp.raise_for_status()

        # 优先使用 Content-Length
        content_length = int(resp.headers.get("Content-Length", total_size))

        downloaded = 0
        chunk_size = 8192

        with open(save_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and content_length > 0:
                        progress_callback(downloaded, content_length)

        logger.info(f"更新下载完成: {save_path}")
        return save_path

    except Exception as e:
        logger.error(f"下载更新失败: {e}")
        return None


def install_update(file_path: str) -> bool:
    """
    执行静默安装更新

    Args:
        file_path: 安装包文件路径

    Returns:
        是否成功启动安装程序
    """
    system = platform.system().lower()
    path = Path(file_path)

    if not path.exists():
        logger.error(f"安装包不存在: {file_path}")
        return False

    try:
        if system == "windows":
            # NSIS 安装包支持 /S 静默安装
            # /S 大写是 NSIS 的标准静默安装参数
            cmd = [str(path), "/S"]
            logger.info(f"启动静默安装: {' '.join(cmd)}")

            # 使用 CREATE_NEW_PROCESS_GROUP 确保安装程序独立运行
            # 启动后立即返回，不等待安装完成
            subprocess.Popen(
                cmd,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                close_fds=True,
            )

        elif system == "darwin":
            # macOS: 打开 DMG 文件
            cmd = ["open", str(path)]
            logger.info(f"打开 DMG: {' '.join(cmd)}")
            subprocess.Popen(cmd, start_new_session=True)

        elif system == "linux":
            # Linux AppImage: 添加执行权限并运行
            if str(path).endswith(".AppImage"):
                os.chmod(str(path), 0o755)
                cmd = [str(path), "--appimage-extract-and-run"]
                logger.info(f"运行 AppImage: {' '.join(cmd)}")
                subprocess.Popen(cmd, start_new_session=True)
            elif str(path).endswith(".deb"):
                # deb 包需要 sudo 安装
                cmd = ["sudo", "dpkg", "-i", str(path)]
                logger.info(f"安装 deb 包: {' '.join(cmd)}")
                subprocess.Popen(cmd, start_new_session=True)
            else:
                logger.warning(f"不支持的 Linux 安装包格式: {path.suffix}")
                return False
        else:
            logger.warning(f"不支持的平台: {system}")
            return False

        logger.info("安装程序已启动，即将退出当前程序...")
        return True

    except Exception as e:
        logger.error(f"启动安装程序失败: {e}")
        return False


def perform_update(
    progress_callback: Optional[Callable[[int, int], None]] = None,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    """
    完整的更新流程：检查 -> 下载 -> 安装

    Args:
        progress_callback: 下载进度回调 (已下载, 总大小)
        status_callback: 状态文本回调

    Returns:
        (是否成功, 消息)
    """
    # 1. 检查更新
    if status_callback:
        status_callback("正在检查更新...")

    release_info = check_for_update()
    if not release_info:
        return False, "当前已是最新版本"

    # 2. 查找合适的安装包
    if status_callback:
        status_callback(f"发现新版本 {release_info['version']}，正在查找安装包...")

    asset = find_suitable_asset(release_info["assets"])
    if not asset:
        return False, f"未找到适合当前平台的安装包（{platform.system()} {platform.machine()}）"

    # 3. 下载
    if status_callback:
        status_callback(f"正在下载 {asset['name']}...")

    file_path = download_update(asset, progress_callback)
    if not file_path:
        return False, "下载更新失败"

    # 4. 安装
    if status_callback:
        status_callback("正在启动安装程序...")

    success = install_update(file_path)
    if success:
        return True, f"更新 {release_info['version']} 安装程序已启动，即将退出"

    return False, "启动安装程序失败"
