"""把曲目解析为「可播放的 URL 或本地文件」。

沿用 FMCL 的判定链，但适配流式播放：

1. 本地文件 → 校验存在与文件头
2. 在线曲目 → ``sources.get_music_url()``（内部已含同源音质降级）
3. 该音源需要额外请求头 / Cookie（如 B 站 Referer）→ 先下载到缓存再播
4. 仍失败 → ``sources.resolve_track()`` 跨源兜底（换平台找同曲）
5. 下载得到的文件再做**文件头 + 时长**双重校验，防止把 HTML 错误页或
   VIP 试听片段当成完整曲目（这两条校验直接取自 FMCL，是防坑的核心）
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import cache, naming
from .models import LOCAL_SOURCE, Track, normalize_singers
from .. import sources

logger = logging.getLogger(__name__)

#: 时长容差（判据取自 FMCL app_music.py:135-144）
_DURATION_TOLERANCE_RATIO = 0.2
_DURATION_TOLERANCE_MIN_SEC = 10

AUDIO_EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus", ".aiff", ".ape", ".mp4", ".m4s"
}

@dataclass
class Resolved:
    """解析结果。"""

    track: Track           # 实际拿去播放的曲目（兜底后可能换了音源）
    target: str            # 本地路径或 http(s) URL
    is_local: bool         # target 是否为本地文件
    quality: str           # 实际使用的音质档位
    fallback: bool = False  # 是否发生了跨源兜底
    requested: Optional[Track] = None  # 用户最初点播的曲目（兜底时为原曲）

    @property
    def play_url(self) -> str:
        if self.is_local:
            from PySide6.QtCore import QUrl

            return QUrl.fromLocalFile(self.target).toString()
        return self.target

    @property
    def display_track(self) -> Track:
        return self.requested or self.track

# ──────────────────────────────────────────────────────────────
# 校验（逐字沿用 FMCL 的判据）
# ──────────────────────────────────────────────────────────────

def validate_audio_file_header(filepath: str) -> bool:
    """校验音频文件头，拦截 HTML 错误页 / 空文件。

    判据与「下载落盘时按文件头纠正扩展名」是**同一张表**
    （:func:`app.core.naming.sniff_file_suffix`）—— 两边各写一份的话，迟早会出现
    「校验放行了却认不出格式」这种分叉。文件不存在 / 空文件 / 读不出来一律
    ``False``。
    """
    return bool(naming.sniff_file_suffix(filepath))

def validate_audio_duration(filepath: str, expected_seconds: int) -> bool:
    """校验实际时长与期望时长的偏差，拦截 VIP 试听片段。

    任一时长未知（<=0）时放行，避免误杀。
    """
    if expected_seconds <= 0:
        return True
    actual = probe_duration(filepath)
    if actual <= 0:
        return True
    tolerance = max(_DURATION_TOLERANCE_MIN_SEC, int(expected_seconds * _DURATION_TOLERANCE_RATIO))
    return abs(actual - expected_seconds) <= tolerance

def probe_duration(filepath: str) -> int:
    """用 mutagen 读取时长（秒），失败返回 0。"""
    try:
        from mutagen import File as MutagenFile

        mf = MutagenFile(filepath)
        if mf is not None and getattr(mf, "info", None) is not None:
            return int(getattr(mf.info, "length", 0) or 0)
    except Exception:
        pass
    return 0

# ──────────────────────────────────────────────────────────────
# 解析
# ──────────────────────────────────────────────────────────────

def resolve(
    track: Track,
    quality: str = "320k",
    *,
    allow_fallback: bool = True,
    exclude_sources: Optional[list] = None,
    cache_media: bool = False,
) -> Optional[Resolved]:
    """把曲目解析为可播放目标。"""
    if track is None:
        return None

    # ── 本地文件 ────────────────────────────────────────────
    if track.is_local or track.source == LOCAL_SOURCE:
        if not track.path or not os.path.exists(track.path):
            logger.warning("本地文件不存在: %s", track.path)
            return None
        return Resolved(
            track=track,
            target=track.path,
            is_local=True,
            quality=_local_quality(track.path),
            requested=track,
        )

    # ── 在线曲目 ────────────────────────────────────────────
    # 逐档尝试（而不是把降级丢给 sources.get_music_url）：只有这里知道**最后取到
    # 地址的是哪一档**，音质标签才不会写成"我想要的那一档"。
    info = track.to_music_info()
    url = ""
    actual = ""
    for candidate in sources.quality_order(info, quality, track.source):
        url = sources.get_music_url_exact(info, candidate, track.source) or ""
        if url:
            actual = candidate
            break
    if url:
        target = _maybe_localize(track, url, cache_media=cache_media, quality=actual)
        if target:
            return Resolved(
                track=track,
                target=target[0],
                is_local=target[1],
                quality=_effective_quality(track, quality, actual),
                requested=track,
            )
        logger.info("音源 %s 返回的地址无法使用，转入跨源兜底: %s", track.source, track.name)
    else:
        logger.info("音源 %s 无可用地址: %s - %s", track.source, track.name, track.singer)

    # ── 跨源兜底 ────────────────────────────────────────────
    if allow_fallback and track.name:
        result = sources.resolve_track(
            track.to_music_info(), quality, excluded_sources=exclude_sources
        )
        if result:
            info, fb_url, fb_quality = result
            fb_track = Track.from_music_info(info)
            fb_track.path = ""
            target = _maybe_localize(fb_track, fb_url, cache_media=True, quality=fb_quality)
            if target:
                return Resolved(
                    track=fb_track,
                    target=target[0],
                    is_local=target[1],
                    quality=_effective_quality(fb_track, quality, fb_quality),
                    fallback=True,
                    requested=track,
                )

    logger.warning("解析失败，所有音源均不可用: %s - %s", track.name, track.singer)
    return None

def _effective_quality(track: Track, requested: str, actual: str = "") -> str:
    """实际播放的音质档位。

    ``actual`` 是**真正取到播放地址的那一档**（见 :func:`resolve`）。以前这里直接
    回显请求值（``auto`` 时回显 ``track.best_quality``），于是服务端悄悄降级时
    界面还在报"无损" —— 实测非会员请求无损会拿到 ``level=exhigh``。
    """
    if actual:
        return actual
    if requested == "auto":
        try:
            return track.best_quality
        except Exception:
            return "128k"
    return requested

def _local_quality(path: str) -> str:
    ext = Path(path).suffix.lower()
    if ext == ".flac":
        return "flac"
    if ext == ".ape":
        return "flac"
    if ext in (".wav", ".aiff"):
        return "lossless"
    if ext in (".m4a", ".aac", ".opus"):
        return "320k"
    return "320k"

def _maybe_localize(
    track: Track, url: str, *, cache_media: bool, quality: str = ""
) -> Optional[Tuple[str, bool]]:
    """决定直接流式播放还是先下载到本地。

    返回 ``(目标, 是否本地文件)``；``None`` 表示该地址不可用。

    ``quality`` 是**真正取到地址的那一档**：它和曲目身份一起决定缓存键
    （见 :func:`app.core.cache.media_identity`），所以响度分析的预取下载与
    真正播放时的下载会命中同一份文件，而不是同一首歌各存一份。
    """
    if not url:
        return None
    headers = sources.download_headers(track.source)
    cookies = sources.download_cookies(track.source)

    # 需要自定义请求头 / Cookie 时 QMediaPlayer 无法直接带，改为先下载
    need_download = cache_media or bool(headers) or bool(cookies)

    if not need_download:
        # 无额外头要求的音源直接流式播放，起播快且支持拖动进度
        return (url, False)

    suffix = Path(url.split("?")[0]).suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        suffix = ".mp3"
    identity = cache.media_identity(track.source, track.songmid, quality)
    local = cache.cached_media(
        url, suffix=suffix, headers=headers, cookies=cookies, identity=identity
    )
    if not local:
        return None
    if not validate_audio_file_header(local):
        logger.warning("下载内容不是有效音频（可能是错误页）: %s", track.name)
        try:
            os.unlink(local)
        except OSError:
            pass
        return None
    if not validate_audio_duration(local, track.interval):
        logger.warning("下载内容时长异常（可能是试听片段）: %s", track.name)
        try:
            os.unlink(local)
        except OSError:
            pass
        return None
    cache.touch(local)
    return (local, True)

def resolve_local_metadata(path: str) -> Optional[Track]:
    """读取本地音频文件的标签，构造 :class:`Track`。

    封面优先用**文件里内嵌的那张**（读出来就写进封面缓存，见
    :func:`app.core.cache.store_cover_bytes`）；没有内嵌封面时留空，由扫描阶段
    的在线匹配去补（见 :mod:`app.core.localmatch`）。歌词一概不在这里碰 ——
    同目录 ``.lrc`` 播放时现读，在线匹配到的歌词也要到播放时才取。
    """
    p = Path(path)
    if not p.exists() or p.suffix.lower() not in AUDIO_EXTENSIONS:
        return None

    name = p.stem
    singer = ""
    album = ""
    duration = 0
    try:
        from mutagen import File as MutagenFile

        mf = MutagenFile(path, easy=True)
        if mf is not None:
            tags: Dict[str, object] = dict(mf.tags or {})
            name = _first(tags, ("title", "TIT2")) or name
            # 歌手要和在线音源一样归一成「、」分隔：标签里写着「洛天依/乐正绫」时
            # 界面拆不开，会被当成一个歌手（见 models.normalize_singers）
            singer = normalize_singers(_first(tags, ("artist", "TPE1")))
            album = _first(tags, ("album", "TALB")) or ""
            if getattr(mf, "info", None) is not None:
                duration = int(getattr(mf.info, "length", 0) or 0)
    except Exception as e:
        logger.debug("读取标签失败 %s: %s", path, e)

    return Track(
        source=LOCAL_SOURCE,
        songmid=str(p.resolve()),
        name=name,
        singer=singer,
        album=album,
        interval=duration,
        cover=_embedded_cover(path),
        path=str(p.resolve()),
    )

def _embedded_cover(path: str) -> str:
    """把音频文件里内嵌的封面写进封面缓存，返回 ``file://`` URL（没有返回空串）。

    识别不了 / 读不出来都当作「没有封面」，交给在线匹配，不打扰扫描。
    """
    try:
        data, mime = read_picture(path)
    except Exception as e:
        logger.debug("读取内嵌封面失败 %s: %s", path, e)
        return ""
    if not data:
        return ""
    return cache.store_cover_bytes(path, data, mime) or ""

def read_picture(path: str) -> Tuple[bytes, str]:
    """从音频文件里取第一张内嵌封面，返回 ``(数据, MIME)``；没有返回 ``(b"", "")``。

    mutagen 把封面放在三个不同的地方，只能挨个试：

    * FLAC / Ogg —— 文件对象上的 ``.pictures``
    * MP3 / WAV / AIFF —— ID3 的 ``APIC`` 帧
    * MP4 / M4A —— 标签里的 ``covr``

    注意这里用的是**非 easy** 的 ``MutagenFile``：``easy=True`` 会把 ID3 换成
    EasyID3、把 MP4 的标签换成 EasyMP4Tags，两者都不保留图片。所以文本标签在
    :func:`resolve_local_metadata` 里读一遍、这里为了图片再读一遍（mutagen 只解
    标签块，不解码音频，代价可接受）。
    """
    from mutagen import File as MutagenFile

    mf = MutagenFile(path)
    if mf is None:
        return b"", ""

    for picture in getattr(mf, "pictures", None) or []:
        data = getattr(picture, "data", None)
        if data:
            return bytes(data), str(getattr(picture, "mime", "") or "")

    tags = getattr(mf, "tags", None)
    if not tags:
        return b"", ""

    getter = getattr(tags, "getall", None)
    if callable(getter):
        for frame in getter("APIC") or []:
            data = getattr(frame, "data", None)
            if data:
                return bytes(data), str(getattr(frame, "mime", "") or "")

    try:
        covers = tags.get("covr") or []
    except Exception:
        covers = []
    for cover in covers:
        try:
            data = bytes(cover)
        except Exception:
            continue
        if data:
            # mutagen.mp4.MP4Cover.imageformat：13 = JPEG、14 = PNG
            mime = "image/png" if getattr(cover, "imageformat", None) == 14 else "image/jpeg"
            return data, mime
    return b"", ""

def _first(tags: Dict[str, object], keys) -> str:
    for k in keys:
        v = tags.get(k)
        if not v:
            continue
        if isinstance(v, (list, tuple)):
            if v:
                return str(v[0])
        else:
            return str(v)
    return ""

def scan_local_folder(folder: str, extensions=None) -> list:
    """递归扫描文件夹中的音频文件。"""
    exts = set(extensions or AUDIO_EXTENSIONS)
    root = Path(folder)
    if not root.exists():
        return []
    found = []
    for p in root.rglob("*"):
        try:
            if p.is_file() and p.suffix.lower() in exts:
                found.append(str(p))
        except OSError:
            continue
    found.sort()
    return found
