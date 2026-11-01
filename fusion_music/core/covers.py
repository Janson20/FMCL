"""封面保存：把当前曲目的封面落到用户选定的文件。

封面在这个程序里有三种来路，保存时要分别对付：

* **在线 URL**（网易云 / QQ / 酷我 / 酷狗 / 咪咕 / B站的封面地址）——走封面缓存下载；
* **本地文件内嵌图**——扫描时就写进封面缓存了，``cover`` 是 ``file://`` URL；
* **压根没有**（本地曲目没匹配上在线身份）——直接告诉用户没有封面可存。

两个容易踩的点：

1. **CDN 的缩略图参数**：网易云的 ``?param=200y200``、B站的 ``@320w_320h.webp``
   后缀都能让封面小一圈。存封面要的是原图，所以先把这类参数摘掉再下载
   （摘完下不动就退回原地址，绝不因为洁癖让用户存不到图）。
2. **文件头才是真相**：网易云的地址结尾是 ``.jpg`` 而内容可能是 webp，
   用户也可能手打 ``.png``。落盘前一律按文件头纠正扩展名，
   否则存出来的文件双击打不开。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import cache, naming
from .naming import sanitize_filename as _sanitize_filename
from .trackutil import as_dict
from ..paths import from_file_url

logger = logging.getLogger(__name__)

#: 文件名清洗与歌曲下载共用一套规则（见 :mod:`app.core.naming`）。


def sanitize_filename(name: str, fallback: str = "封面") -> str:
    """把任意文本清洗成 Windows 能用的文件名主干（封面默认叫「封面」）。"""
    return _sanitize_filename(name, fallback)


#: 保存失败时能讲清楚原因的异常（界面直接把这句话弹给用户）
class CoverError(Exception):
    """保存封面过程中的可预期失败。"""


#: 会被当成「缩略图参数」摘掉的查询键（只摘这些，别的参数原样留着 ——
#: 有些 CDN 的参数带签名，乱摘会 403）
_RESIZE_PARAMS = frozenset(
    {"param", "thumbnail", "thumb", "w", "h", "width", "height", "size", "quality"}
)
#: 允许出现在文件名里的图片后缀（判断 URL 时不区分大小写）
_IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".avif")
#: 扩展名等价表：判断「用户给的扩展名对不对」时用
_SUFFIX_EQUIV = {
    ".jpg": (".jpg", ".jpeg"),
    ".jpeg": (".jpg", ".jpeg"),
    ".png": (".png",),
    ".webp": (".webp",),
    ".gif": (".gif",),
    ".bmp": (".bmp",),
}

_BILIBILI_SUFFIX = re.compile(r"@[^/]*$")


def suggest_stem(track: Any) -> str:
    """默认文件名主干：``歌手 - 歌名``（缺哪边就用哪边，都没有叫「封面」）。

    拼法与下载同源（:func:`app.core.naming.suggested_stem`），只是缺省名不同 ——
    存封面不该叫「未命名.mp3」那种名字。
    """
    return naming.suggested_stem(as_dict(track), "{singer} - {name}", fallback="封面")


def suggest_suffix(track: Any) -> str:
    """默认扩展名：从封面地址猜一个（真下载下来后还会按文件头纠正）。"""
    info = as_dict(track)
    url = str(info.get("cover") or "")
    if url.startswith("file:"):
        url = from_file_url(url)
    suffix = Path(url.split("?")[0].split("#")[0]).suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        return ".jpg" if suffix == ".jpeg" else suffix
    return ".jpg"


def original_url(url: str) -> str:
    """尽量把「缩略图地址」还原成原图地址（还原不了就原样返回）。"""
    text = str(url or "").strip()
    if not text:
        return ""
    base, _, fragment = text.partition("#")
    path, _, query = base.partition("?")
    kept = [
        pair
        for pair in query.split("&")
        if pair and pair.split("=", 1)[0].lower() not in _RESIZE_PARAMS
    ]
    # B站 / 部分 CDN 把尺寸写在路径后缀里：xxx.jpg@320w_320h.webp
    trimmed = _BILIBILI_SUFFIX.sub("", path)
    if trimmed != path and Path(trimmed).suffix.lower() in _IMAGE_SUFFIXES:
        path = trimmed
    rebuilt = path + (("?" + "&".join(kept)) if kept else "")
    return rebuilt + (("#" + fragment) if fragment else "")


def detect_suffix(data: bytes) -> str:
    """按文件头判断图片格式，返回扩展名；认不出来返回空串。"""
    if not data:
        return ""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return ".gif"
    if data.startswith(b"BM"):
        return ".bmp"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ""


def resolve_cover(track: Any) -> Tuple[bytes, str]:
    """取回封面字节与它的真实格式，返回 ``(数据, 扩展名)``。

    失败一律抛 :class:`CoverError`，消息是可以直接给用户看的中文。
    """
    info = as_dict(track)
    url = str(info.get("cover") or "").strip()
    if not url:
        url = cover_url_from_source(info) or ""
    if not url:
        raise CoverError("这首歌没有可保存的封面")

    if url.startswith("file:"):
        path = from_file_url(url)
        try:
            data = Path(path).read_bytes()
        except OSError as e:
            raise CoverError(f"读取封面文件失败：{e}") from e
        if not data:
            raise CoverError("封面文件是空的")
        return data, detect_suffix(data)

    tried: list[str] = []
    for candidate in (original_url(url), url):
        if not candidate or candidate in tried:
            continue
        tried.append(candidate)
        local = cache.fetch_cached(candidate, "cover")
        if not local:
            continue
        try:
            data = Path(local).read_bytes()
        except OSError as e:
            logger.debug("读取封面缓存失败 %s: %s", local, e)
            continue
        if data:
            return data, detect_suffix(data)
    raise CoverError("封面下载失败，请检查网络后重试")


def cover_url_from_source(info: Dict[str, Any]) -> Optional[str]:
    """曲目本身没带封面地址时，回头问一次音源接口（本地未匹配的曲目问不到）。"""
    source = str(info.get("source") or "")
    songmid = str(info.get("songmid") or "")
    if not source or not songmid or source == "local":
        return None
    try:
        from ..sources import get_pic_url
        from .models import Track

        track = Track.from_dict(info)
        track_info = track.to_music_info()
        track_info.source = source
        track_info.songmid = songmid
        url = get_pic_url(track_info, source)
        return str(url) if url else None
    except Exception as e:
        logger.debug("问音源要封面失败 %s: %s", songmid, e)
        return None


def save_cover(track: Any, destination: str) -> Tuple[str, int]:
    """把封面写到 ``destination``，返回 ``(最终路径, 字节数)``。

    最终路径可能与用户给的不一样：扩展名会按**文件头**纠正（用户手打 ``.png``
    而图其实是 jpg 时，写出来的就得是 ``.jpg``，否则双击打不开）。
    同目录先写 ``*.part`` 再原子替换，中途失败不会留下半个文件。
    """
    target = Path(from_file_url(str(destination or "").strip()))
    if not str(target) or str(target) == ".":
        raise CoverError("没有选择保存位置")
    if target.is_dir():
        raise CoverError("保存位置是一个文件夹，请指定文件名")

    data, suffix = resolve_cover(track)
    if suffix:
        allowed = _SUFFIX_EQUIV.get(suffix, (suffix,))
        if target.suffix.lower() not in allowed:
            target = target.with_suffix(suffix)
    elif not target.suffix:
        target = target.with_suffix(".jpg")
    if not target.name or target.name.startswith("."):
        raise CoverError("文件名不合法")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        tmp.write_bytes(data)
        os.replace(tmp, target)
    except OSError as e:
        logger.warning("写入封面失败 %s: %s", target, e)
        raise CoverError(f"写入失败：{e}") from e
    return str(target), len(data)
