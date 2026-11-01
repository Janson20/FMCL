"""从 GitHub Releases 检查并获取更新（纯逻辑 + 网络层，不依赖 Qt）。

自动更新这件事，风险几乎全在「下载了什么」和「替换了什么」这两步上，
所以这里把可判定的部分全部抽成**纯函数**（版本比较、资产选择、摘要校验），
网络只负责取 JSON 与字节流，替换动作在 :mod:`app.core.update_install` 里。

几条硬性约束：

* 只认 ``https://`` 与 GitHub 的域名，**不接受** Release 里指向别处的地址；
* 下载完必须与服务端给的 ``digest``（``sha256:...``）对上才允许安装；
  GitHub 没给摘要时直接**放弃自动安装**，降级成「打开发布页」——
  宁可不自动，也不装一个没校验过的可执行文件；
* 版本比较按 ``major.minor.patch``，预发布（``1.2.0-beta.1``）永远小于同号正式版。
"""

from __future__ import annotations

import hashlib
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..lazy import requests  # 延迟导入：requests 本体要 0.4 秒，见 app/lazy.py
from ..units import human_size as _human_size

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
#: 本项目的仓库（写死在这里：更新源不应该是用户可随手改的输入）
DEFAULT_REPO = "Janson20/FusionMusicPlayer"
#: 允许下载的域名。GitHub 会把资产重定向到 objects.githubusercontent.com /
#: release-assets.githubusercontent.com，这几个是它自己的 CDN
ALLOWED_HOSTS = (
    "github.com",
    "api.github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "codeload.github.com",
)
USER_AGENT = "FusionMusicPlayer-Updater/1.0 (+https://github.com/Janson20/FusionMusicPlayer)"

#: 资产名模板（见 .github/workflows/release.yml）
ASSET_NAME_TEMPLATES = {
    "onedir": "FusionMusicPlayer-{version}-win-x64.zip",
    "onefile": "FusionMusicPlayer-{version}-win-x64.exe",
}

VERSION_RE = re.compile(r"^\s*v?(\d+)\.(\d+)\.(\d+)(?:[-+]([0-9A-Za-z.\-]+))?\s*$")
DIGEST_RE = re.compile(r"^sha256:([0-9a-fA-F]{64})$")

#: 发行形态
KIND_ONEDIR = "onedir"      # 便携版（目录 + _internal）
KIND_ONEFILE = "onefile"    # 单文件 exe
KIND_SOURCE = "source"      # 源码运行（不做自动安装）


class UpdaterError(Exception):
    """更新流程中可以直接展示给用户的错误。"""


# ──────────────────────────────────────────────────────────────
# 版本
# ──────────────────────────────────────────────────────────────


def parse_version(text: str) -> Optional[Tuple[int, int, int, str]]:
    """解析 ``v1.2.3`` / ``1.2.3-beta.1`` / ``0.0.0-dev``。

    返回 ``(major, minor, patch, 预发布标识)``；预发布标识为空串表示正式版。
    无法识别返回 ``None`` —— 调用方据此判定「这不是一个可比较的版本」。
    """
    match = VERSION_RE.match(str(text or ""))
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    return (int(major), int(minor), int(patch), (pre or "").lower())


def compare_versions(a: str, b: str) -> Optional[int]:
    """比较两个版本号：``a`` 更新返回 1，相同返回 0，更旧返回 -1。

    任一侧解析不了返回 ``None``（调用方据此放弃自动更新，而不是猜）。
    预发布永远小于同号正式版：``1.2.0-rc1 < 1.2.0``。
    """
    left, right = parse_version(a), parse_version(b)
    if left is None or right is None:
        return None
    if left[:3] != right[:3]:
        return 1 if left[:3] > right[:3] else -1
    left_pre, right_pre = left[3], right[3]
    if left_pre == right_pre:
        return 0
    if not left_pre:
        return 1        # 正式版 > 预发布
    if not right_pre:
        return -1
    return 1 if left_pre > right_pre else -1


def is_newer(candidate: str, current: str) -> bool:
    """``candidate`` 是否比 ``current`` 新（解析不了时一律返回 False）。"""
    return compare_versions(candidate, current) == 1


# ──────────────────────────────────────────────────────────────
# 发布与资产
# ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ReleaseAsset:
    """一个发布资产。"""

    name: str
    url: str
    size: int = 0
    digest: str = ""

    @property
    def sha256(self) -> str:
        """从 ``sha256:...`` 里取出十六进制摘要；没有或格式不对返回空串。"""
        match = DIGEST_RE.match(str(self.digest or "").strip())
        return match.group(1).lower() if match else ""

    @property
    def size_text(self) -> str:
        return human_size(self.size)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "size": self.size, "digest": self.digest,
                "url": self.url}


@dataclass(frozen=True)
class ReleaseInfo:
    """一次发布。"""

    version: str
    tag: str
    name: str = ""
    body: str = ""
    html_url: str = ""
    published_at: str = ""
    prerelease: bool = False
    assets: Tuple[ReleaseAsset, ...] = field(default_factory=tuple)

    def asset_for(self, kind: str, version: str = "") -> Optional[ReleaseAsset]:
        return pick_asset(self.assets, kind, version or self.version)


def human_size(size: int) -> str:
    """字节数 → 人类可读（下载按钮上要显示）。

    换算与别处同一份（:func:`app.units.human_size`），只有「拿不到」的措辞是本页
    特有的：更新包大小不知道时写 "0 B"，用户会以为包是空的。
    """
    return _human_size(size, zero_text="未知大小")


def pick_asset(assets, kind: str, version: str = "") -> Optional[ReleaseAsset]:
    """按发行形态挑出该下载哪个文件。

    先按流水线的命名规则精确匹配，匹配不到再按后缀 / 关键字退让 ——
    这样即使哪天改名了，也还能挑到「对的形态」，而不是随便下第一个。
    """
    items = [a for a in (assets or []) if isinstance(a, ReleaseAsset)]
    if not items:
        return None
    if kind == KIND_SOURCE:
        return None

    wanted = ASSET_NAME_TEMPLATES.get(kind, "")
    if wanted and version:
        exact = wanted.format(version=str(version).lstrip("vV"))
        for asset in items:
            if asset.name == exact:
                return asset

    for asset in items:
        name = asset.name.lower()
        if kind == KIND_ONEFILE:
            if name.endswith(".exe"):
                return asset
        elif kind == KIND_ONEDIR:
            if name.endswith(".zip") and "win-x64" in name:
                return asset
    return None


def parse_release(payload: Dict[str, Any]) -> Optional[ReleaseInfo]:
    """GitHub 的 release JSON → :class:`ReleaseInfo`；缺关键字段返回 ``None``。"""
    if not isinstance(payload, dict):
        return None
    tag = str(payload.get("tag_name") or "").strip()
    parsed = parse_version(tag)
    if parsed is None:
        return None
    version = f"{parsed[0]}.{parsed[1]}.{parsed[2]}" + (f"-{parsed[3]}" if parsed[3] else "")
    assets: List[ReleaseAsset] = []
    for raw in payload.get("assets") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        url = str(raw.get("browser_download_url") or "").strip()
        if not name or not url:
            continue
        if not url_allowed(url):
            logger.warning("忽略指向站外的发布资产: %s -> %s", name, url)
            continue
        try:
            size = int(raw.get("size") or 0)
        except (TypeError, ValueError):
            size = 0
        assets.append(ReleaseAsset(name=name, url=url, size=size,
                                   digest=str(raw.get("digest") or "")))
    return ReleaseInfo(
        version=version,
        tag=tag,
        name=str(payload.get("name") or tag),
        body=str(payload.get("body") or ""),
        html_url=str(payload.get("html_url") or "").strip(),
        published_at=str(payload.get("published_at") or ""),
        prerelease=bool(payload.get("prerelease", False)),
        assets=tuple(assets),
    )


def url_allowed(url: str) -> bool:
    """下载地址是否可信：必须是 https，且落在 GitHub 自己的域名下。"""
    from urllib.parse import urlparse

    try:
        parts = urlparse(str(url or ""))
    except ValueError:
        return False
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    return any(host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_HOSTS)


def release_notes_excerpt(body: str, limit: int = 900) -> str:
    """把发布说明压成界面能用的一段（去掉相对链接、截断）。"""
    text = str(body or "").strip()
    if not text:
        return ""
    lines = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("|"):          # 下载表格在应用里没意义（按钮就够了）
            continue
        if line.startswith(">"):
            continue
        lines.append(line)
    text = "\n".join(lines).strip()
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


# ──────────────────────────────────────────────────────────────
# 发行形态
# ──────────────────────────────────────────────────────────────


def install_kind() -> str:
    """当前这份程序是哪种形态：``onedir`` / ``onefile`` / ``source``。

    PyInstaller 6 的 onedir 把运行时放在 exe 同级的 ``_internal``，
    onefile 则解到 ``%TEMP%\\_MEIxxxxxx``；两者的 ``sys._MEIPASS`` 位置不同，
    正好可以据此区分（不能只看 ``frozen``，两种形态替换方式完全不一样）。
    """
    if not getattr(sys, "frozen", False):
        return KIND_SOURCE
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        try:
            bundle = Path(meipass)
            exe_dir = Path(sys.executable).resolve().parent
            if bundle.name.startswith("_MEI") and bundle.parent != exe_dir:
                return KIND_ONEFILE
        except Exception:  # pragma: no cover - 路径异常时按目录版处理
            pass
    return KIND_ONEDIR


# ──────────────────────────────────────────────────────────────
# 摘要
# ──────────────────────────────────────────────────────────────


def sha256_file(path: Path, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def digest_matches(path: Path, digest: str) -> bool:
    """文件摘要是否与 ``sha256:...`` 一致（摘要为空一律 False）。"""
    match = DIGEST_RE.match(str(digest or "").strip())
    if not match:
        return False
    try:
        return sha256_file(Path(path)).lower() == match.group(1).lower()
    except OSError as e:
        logger.debug("计算摘要失败 %s: %s", path, e)
        return False


# ──────────────────────────────────────────────────────────────
# 网络
# ──────────────────────────────────────────────────────────────


def build_session(proxy: str = "") -> requests.Session:
    """带 UA 的会话（GitHub API 要求 UA，缺失会直接 403）。"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    return session


def fetch_latest(repo: str = DEFAULT_REPO, *, session: Optional[requests.Session] = None,
                 proxy: str = "", timeout: int = 15,
                 include_prerelease: bool = False) -> ReleaseInfo:
    """取最新发布。失败一律抛 :class:`UpdaterError`（消息可直接展示）。"""
    client = session or build_session(proxy)
    if include_prerelease:
        url = f"{GITHUB_API}/repos/{repo}/releases?per_page=10"
    else:
        url = f"{GITHUB_API}/repos/{repo}/releases/latest"
    try:
        resp = client.get(url, timeout=timeout)
    except requests.RequestException as e:
        raise UpdaterError(f"无法连接 GitHub：{e.__class__.__name__}") from e

    if resp.status_code == 404:
        raise UpdaterError("找不到发布信息（仓库地址或版本可能有问题）")
    if resp.status_code == 403 and "rate limit" in resp.text.lower():
        raise UpdaterError("GitHub 接口访问次数已达上限，请稍后再试")
    if resp.status_code >= 400:
        raise UpdaterError(f"GitHub 返回错误：HTTP {resp.status_code}")

    try:
        payload = resp.json()
    except ValueError as e:
        raise UpdaterError("GitHub 返回的内容无法解析") from e

    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict) and not item.get("draft"):
                release = parse_release(item)
                if release is not None:
                    return release
        raise UpdaterError("没有找到可用的发布版本")

    release = parse_release(payload)
    if release is None:
        raise UpdaterError("发布信息里没有可用的版本号")
    return release


def download(
    asset: ReleaseAsset,
    dest_dir: Path,
    *,
    session: Optional[requests.Session] = None,
    proxy: str = "",
    timeout: int = 30,
    progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Path:
    """下载资产到 ``dest_dir``，校验摘要后原子改名，返回落盘路径。

    ``progress(已下载, 总大小)`` 会被周期性回调（在工作线程里）；
    ``should_cancel()`` 返回 True 时中断并删除半成品。
    """
    if not url_allowed(asset.url):
        raise UpdaterError("下载地址不在允许的域名内，已拒绝")
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / asset.name
    part = dest_dir / (asset.name + ".part")

    digest = hashlib.sha256()
    total = int(asset.size or 0)
    received = 0
    try:
        client = session or build_session(proxy)
        with client.get(asset.url, timeout=timeout, stream=True,
                        allow_redirects=True) as resp:
            if resp.status_code >= 400:
                raise UpdaterError(f"下载失败：HTTP {resp.status_code}")
            try:
                total = int(resp.headers.get("Content-Length") or total or 0)
            except (TypeError, ValueError):
                pass
            with open(part, "wb") as f:
                for chunk in resp.iter_content(256 * 1024):
                    if not block_ok(chunk):
                        continue
                    if should_cancel is not None and should_cancel():
                        raise UpdaterError("已取消下载")
                    f.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    if progress is not None:
                        progress(received, total)
    except UpdaterError:
        part.unlink(missing_ok=True)
        raise
    except requests.RequestException as e:
        part.unlink(missing_ok=True)
        raise UpdaterError(f"下载中断：{e.__class__.__name__}") from e
    except OSError as e:
        part.unlink(missing_ok=True)
        raise UpdaterError(f"写入失败：{e}") from e

    if received <= 0:
        part.unlink(missing_ok=True)
        raise UpdaterError("下载内容为空")
    expected = asset.sha256
    if not expected:
        part.unlink(missing_ok=True)
        raise UpdaterError("发布信息没有提供校验摘要，已放弃自动更新")
    if digest.hexdigest().lower() != expected:
        part.unlink(missing_ok=True)
        raise UpdaterError("下载内容校验失败（SHA-256 不匹配），已删除")

    part.replace(target)
    return target


def block_ok(chunk: Any) -> bool:
    """``iter_content`` 偶尔会给出空块或非字节对象。"""
    return bool(chunk) and isinstance(chunk, (bytes, bytearray))
