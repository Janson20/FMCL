"""封面 / 歌词 / 音频文件缓存。

全部落在程序目录 ``data/cache/`` 下，键为 URL 的 SHA-1 前 16 位，
附带最小可用的磁盘配额清理。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Dict, Optional, Tuple

from ..lazy import requests  # 延迟导入：requests 本体要 0.4 秒，见 app/lazy.py
from .. import paths
from ..units import human_size

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_session_lock = threading.Lock()
_session: Optional[requests.Session] = None

def session() -> requests.Session:
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            s.headers.update({"User-Agent": DEFAULT_UA})
            _session = s
        return _session

def key_for(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]

def _download(url: str, dest: Path, headers: Optional[Dict[str, str]] = None,
              cookies: Optional[Dict[str, str]] = None, timeout: int = 30) -> bool:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(dest.parent), suffix=".part")
        os.close(fd)
        tmp_path = Path(tmp)
        with session().get(
            url, headers=headers or {}, cookies=cookies or {}, timeout=timeout, stream=True
        ) as resp:
            resp.raise_for_status()
            size = 0
            with open(tmp_path, "wb") as f:
                for chunk in resp.iter_content(65536):
                    if chunk:
                        f.write(chunk)
                        size += len(chunk)
        if size <= 0:
            tmp_path.unlink(missing_ok=True)
            return False
        os.replace(tmp_path, dest)
        return True
    except Exception as e:
        logger.debug("下载失败 %s: %s", url, e)
        try:
            Path(tmp).unlink(missing_ok=True)  # type: ignore[possibly-undefined]
        except Exception:
            pass
        return False

def fetch_cached(
    url: str,
    subdir: str,
    suffix: str = "",
    headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """下载并缓存到 ``data/cache/<subdir>/``，返回本地路径。"""
    if not url:
        return None
    if url.startswith("file://"):
        return url
    if not suffix:
        suffix = Path(url.split("?")[0]).suffix or ".bin"
    base = {
        "cover": paths.cover_cache_dir,
        "lyric": paths.lyric_cache_dir,
        "media": paths.media_cache_dir,
    }.get(subdir)
    if base is None:
        base = paths.cache_dir() / subdir
    dest = base() / f"{key_for(url)}{suffix}"
    if dest.exists() and dest.stat().st_size > 0:
        return str(dest)
    if _download(url, dest, headers, cookies):
        return str(dest)
    return None

def cached_cover(url: str) -> Optional[str]:
    """封面缓存；返回 ``file://`` URL 供 QML Image 直接使用。"""
    p = fetch_cached(url, "cover")
    if not p:
        return None
    from PySide6.QtCore import QUrl

    return QUrl.fromLocalFile(p).toString()

#: 内嵌封面的 MIME 白名单（只认 QML 直接画得出来的那几种）
_COVER_SUFFIX = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
}

def store_cover_bytes(key_source: str, data: bytes, mime: str = "") -> Optional[str]:
    """把**从本地文件标签里读出来的封面**写进封面缓存，返回 ``file://`` URL。

    缓存键取自文件路径而不是 URL —— 同一个文件反复扫描不会堆出好几份；
    内容变了（长度不一样）就覆盖重写。MIME 认不出来时按 JPEG 存，
    QML 的 ``Image`` 是看文件头解码的，后缀只影响观感。
    """
    if not data:
        return None
    suffix = _COVER_SUFFIX.get(str(mime or "").strip().lower(), ".jpg")
    dest = paths.cover_cache_dir() / f"local-{key_for(str(key_source))}{suffix}"
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or dest.stat().st_size != len(data):
            tmp = dest.with_name(dest.name + ".part")
            tmp.write_bytes(data)
            os.replace(tmp, dest)
    except OSError as e:
        logger.debug("写入内嵌封面失败 %s: %s", key_source, e)
        return None

    from PySide6.QtCore import QUrl

    return QUrl.fromLocalFile(str(dest)).toString()

def cached_lyric(url: str) -> Optional[str]:
    return fetch_cached(url, "lyric", suffix=".lrc")

def media_identity(source: str, songmid: str, quality: str = "") -> str:
    """音频缓存的**稳定身份**：``source:songmid:quality``。

    为什么不能拿播放地址当键：网易云的地址里嵌着**当前时间戳**
    （``http://m701.music.126.net/20261006125032/…``），同一首歌每次解析出来的
    地址都不一样 —— 按 URL 缓存等于每次都新建一份。实测（2026-10）用户的媒体
    缓存 2.27 GB / 140 个文件里有 44 组内容完全重复：响度分析预取下载一次、
    真正播放又下载一次，重播还会再存一份，而老键**一次都命中不了**。

    拿不到身份（没有 songmid）时返回空串，调用方退回按 URL 缓存。
    """
    source = str(source or "").strip()
    songmid = str(songmid or "").strip()
    if not source or not songmid:
        return ""
    return f"{source}:{songmid}:{str(quality or '').strip() or 'auto'}"

def cached_media(url: str, suffix: str = "", headers=None, cookies=None,
                 identity: str = "") -> Optional[str]:
    """下载并缓存音频文件，返回本地路径。

    ``identity`` 见 :func:`media_identity`：传了它，同一首歌（同一档音质）无论
    地址怎么变都只占一份；旧版本按 URL 存下的那份会被**改名认领**过来，不白丢。
    不传就还是按 URL 存（拿不到曲目身份时的兜底）。
    """
    if not url:
        return None
    base = paths.media_cache_dir()
    if not suffix:
        suffix = Path(url.split("?")[0]).suffix or ".bin"
    dest = base / f"{key_for(identity or url)}{suffix}"
    if dest.exists() and dest.stat().st_size > 0:
        touch(str(dest))
        return str(dest)
    if identity:
        # 老缓存是按播放地址存的：地址恰好没变（同一个时间戳）时直接认领
        legacy = base / f"{key_for(url)}{suffix}"
        if legacy.exists() and legacy.stat().st_size > 0:
            try:
                os.replace(legacy, dest)
                touch(str(dest))
                return str(dest)
            except OSError as e:
                logger.debug("认领旧媒体缓存失败 %s: %s", legacy, e)
    if _download(url, dest, headers, cookies):
        return str(dest)
    return None

# ──────────────────────────────────────────────────────────────
# 配额清理
# ──────────────────────────────────────────────────────────────

def _content_digest(path: Path, chunk: int = 1 << 20) -> Optional[str]:
    """文件内容的 SHA-1（分块读，几 GB 的大文件也不会把内存吃掉）。"""
    digest = hashlib.sha1()
    try:
        with open(path, "rb") as f:
            while True:
                block = f.read(chunk)
                if not block:
                    break
                digest.update(block)
    except OSError as e:
        logger.debug("读取缓存文件失败 %s: %s", path, e)
        return None
    return digest.hexdigest()

def _last_used(path: Path) -> float:
    try:
        st = path.stat()
        return max(st.st_atime, st.st_mtime)
    except OSError:
        return 0.0

def dedupe_media() -> Tuple[int, int]:
    """删掉媒体缓存里**内容完全相同**的重复文件，返回 ``(删除数, 释放字节数)``。

    这是收拾旧账用的：老版本按播放地址当键（见 :func:`media_identity`），
    同一首歌会被存好几份。现在改成按曲目身份缓存之后不会再有新的重复，
    但已经堆在盘上的那批得清一次。

    只对**大小相同**的文件算内容摘要，不会去哈希整个缓存目录；保留访问时间
    最新的那一份（它最可能是当前在用的那个键）。播放器正打开着的文件在
    Windows 上删不掉，跳过即可。
    """
    base = paths.media_cache_dir()
    if not base.exists():
        return (0, 0)

    groups: Dict[int, list] = {}
    for path in base.rglob("*"):
        try:
            if not path.is_file() or path.name.endswith(".part"):
                continue
            groups.setdefault(path.stat().st_size, []).append(path)
        except OSError:
            continue

    removed = 0
    freed = 0
    for size, files in groups.items():
        if size <= 0 or len(files) < 2:
            continue
        seen: Dict[str, Path] = {}
        for path in sorted(files, key=_last_used, reverse=True):
            digest = _content_digest(path)
            if digest is None:
                continue
            if digest not in seen:
                seen[digest] = path
                continue
            try:
                freed += path.stat().st_size
                path.unlink()
                removed += 1
            except OSError as e:
                logger.debug("删除重复缓存失败 %s: %s", path, e)
    if removed:
        logger.info("媒体缓存去重：删除 %d 个重复文件，释放 %s", removed, human_size(freed))
    return (removed, freed)

def directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return 0
    for p in path.rglob("*"):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total

def trim_cache(limit_mb: int, subdir: str = "media") -> int:
    """按 LRU 淘汰，返回删除的文件数。"""
    base = {
        "cover": paths.cover_cache_dir,
        "lyric": paths.lyric_cache_dir,
        "media": paths.media_cache_dir,
    }.get(subdir, lambda: paths.cache_dir() / subdir)()
    if not base.exists():
        return 0
    limit = max(0, int(limit_mb)) * 1024 * 1024
    files = []
    for p in base.rglob("*"):
        try:
            if p.is_file():
                st = p.stat()
                files.append((st.st_atime, st.st_size, p))
        except OSError:
            pass
    total = sum(f[1] for f in files)
    if total <= limit:
        return 0
    files.sort(key=lambda x: x[0])
    removed = 0
    for _atime, size, p in files:
        if total <= limit:
            break
        try:
            p.unlink()
            total -= size
            removed += 1
        except OSError:
            pass
    if removed:
        logger.info("已清理 %s 缓存 %d 个文件", subdir, removed)
    return removed

def clear_cache(subdir: Optional[str] = None) -> None:
    base = paths.cache_dir() if subdir is None else paths.cache_dir() / subdir
    if base.exists():
        shutil.rmtree(base, ignore_errors=True)
    paths.ensure_dirs()

def cache_stats() -> Dict[str, int]:
    return {
        "cover": directory_size(paths.cover_cache_dir()),
        "lyric": directory_size(paths.lyric_cache_dir()),
        "media": directory_size(paths.media_cache_dir()),
    }

def touch(path: str) -> None:
    """刷新访问时间，避免刚用过的缓存被 LRU 淘汰。"""
    try:
        os.utime(path, None)
    except OSError:
        pass
