"""下载产物的附加写入：歌词边车、标签、内嵌封面。

这些都不是「下载成功」的必需品 —— 音频已经落盘之后再写标签失败，绝不该把文件删掉，
所以每个函数只在**真的没写成**时抛 :class:`WriteError`，由下载引擎记成
「部分成功」并在界面提示。

歌词默认写成 UTF-8 **带 BOM** 的 ``.lrc``：中文播放器（Windows Media Player、
老版本 foobar2000）按本地代码页读无 BOM 的 UTF-8 会串码，带 BOM 则一律认成 UTF-8，
而现代播放器（网易云、foobar2000 1.x、PotPlayer）两种都吃。

封面要嵌进标签得按容器分别处理（三处不同的 API）：ID3 的 ``APIC``、FLAC 的
``pictures``、MP4 的 ``covr``；ASF/WMA 的 ``WM/Picture`` 结构复杂且各家实现不一，
**有意不嵌**（写文本标签照旧），免得写出别的播放器认不了的块。
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .trackutil import field

logger = logging.getLogger(__name__)

#: ``.lrc`` 的编码：带 BOM 的 UTF-8
LYRIC_ENCODING = "utf-8-sig"
#: ID3 与 Vorbis 里的封面类型：3 = 封面（front cover）
_PICTURE_TYPE_COVER = 3


class WriteError(Exception):
    """可预期的写入失败（消息可以直接显示给用户）。"""


# ──────────────────────────────────────────────────────────────
# 歌词边车
# ──────────────────────────────────────────────────────────────


def lyric_path(audio_path: str) -> Path:
    """音频文件对应的 ``.lrc`` 路径（同目录同名）。"""
    path = Path(audio_path)
    return path.with_suffix(".lrc")


def write_lyric(audio_path: str, text: str, *, bom: bool = True) -> str:
    """把歌词写到同目录同名 ``.lrc``，返回落盘路径。"""
    content = str(text or "").strip()
    if not content:
        raise WriteError("这首歌唱源没有返回歌词")
    target = lyric_path(audio_path)
    encoding = LYRIC_ENCODING if bom else "utf-8"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".part")
        tmp.write_text(content + "\n", encoding=encoding, newline="\n")
        os.replace(tmp, target)
    except OSError as e:
        logger.debug("写入歌词失败 %s: %s", target, e)
        raise WriteError(f"歌词写入失败：{e}") from e
    return str(target)


# ──────────────────────────────────────────────────────────────
# 标签
# ──────────────────────────────────────────────────────────────


def tag_values(track: Any, track_number: int = 0) -> Dict[str, str]:
    """曲目 → 标签键值（只带确实有值的字段）。"""
    values: Dict[str, str] = {}
    name = _text(track, "name")
    singer = _text(track, "singer")
    album = _text(track, "album")
    if name:
        values["title"] = name
    if singer:
        values["artist"] = singer
    if album:
        values["album"] = album
        # 专辑歌手与曲目歌手一致时补一个：不少播放器只按 albumartist 分专辑
        values["albumartist"] = singer or album
    year = publish_year(track)
    if year:
        values["date"] = year
    if int(track_number or 0) > 0:
        values["tracknumber"] = str(int(track_number))
    return values


def publish_year(track: Any) -> str:
    """发布时间（毫秒时间戳）→ 年份字符串；拿不到返回空串。"""
    raw = field(track, "publish_time")
    try:
        stamp = int(raw or 0)
    except (TypeError, ValueError):
        return ""
    if stamp <= 0:
        return ""
    # 秒级时间戳（10 位）也认，免得把 2024 年算成 1970 年
    if stamp < 100000000000:
        stamp *= 1000
    try:
        return str(time.localtime(stamp / 1000).tm_year)
    except (OverflowError, OSError, ValueError):
        return ""


def embed_tags(
    path: str,
    track: Any,
    *,
    cover: Optional[Tuple[bytes, str]] = None,
    track_number: int = 0,
) -> None:
    """把标题 / 歌手 / 专辑 / 年份 / 音轨号写进文件，可选嵌入封面。

    写不进去一律抛 :class:`WriteError`（``mutagen`` 没装、格式认不出来、文件损坏）。
    """
    values = tag_values(track, track_number=track_number)
    if not values and not cover:
        return
    if values:
        _write_text_tags(path, values)
    if cover:
        _embed_cover(path, cover)


def _write_text_tags(path: str, values: Dict[str, str]) -> None:
    """写文本标签，按容器分两条路。

    不能一概用 ``easy=True``：mutagen 的 easy 包装只覆盖 MP3 / MP4 / TrueAudio，
    WAVE 与 AIFF 返回的是带 ID3 标签的原始对象 —— 那时 ``audio["title"] = [...]``
    会被当成「往 ID3 里塞一个名叫 title 的帧」，实测报
    ``['测试专辑'] not a Frame instance``。所以 ID3 容器直接写帧，其余走 easy 键。
    """
    try:
        from mutagen import File as MutagenFile
        from mutagen.id3 import ID3, TALB, TDRC, TIT2, TPE1, TPE2, TRCK
    except ImportError as e:  # pragma: no cover - 依赖运行环境
        raise WriteError("未安装 mutagen，跳过标签写入") from e

    try:
        audio = MutagenFile(path)
    except Exception as e:
        raise WriteError(f"读取标签失败：{e}") from e
    if audio is None:
        raise WriteError("这个音频格式不认识，跳过标签写入")

    try:
        if audio.tags is None:
            audio.add_tags()
        if isinstance(audio.tags, ID3):
            frames = {
                "title": TIT2, "artist": TPE1, "album": TALB,
                "albumartist": TPE2, "date": TDRC, "tracknumber": TRCK,
            }
            for key, value in values.items():
                frame = frames.get(key)
                if frame is None:
                    continue
                audio.tags.delall(frame.__name__)
                audio.tags.add(frame(encoding=3, text=[value]))
            audio.save()
            return
        _write_easy_tags(path, values)
    except WriteError:
        raise
    except Exception as e:
        raise WriteError(f"写入标签失败：{e}") from e


#: ASF/WMA 用的是自己的键名（easy 包装不管它）
_ASF_KEYS = {
    "title": "Title",
    "artist": "Author",
    "album": "WM/AlbumTitle",
    "albumartist": "WM/AlbumArtist",
    "date": "WM/Year",
    "tracknumber": "WM/TrackNumber",
}


def _write_easy_tags(path: str, values: Dict[str, str]) -> None:
    """Vorbis / MP4 / ASF 这些容器：按各自的键名写。"""
    from mutagen import File as MutagenFile

    audio = MutagenFile(path, easy=True) or MutagenFile(path)
    if audio is None:
        raise WriteError("这个音频格式不认识，跳过标签写入")
    if audio.tags is None:
        audio.add_tags()
    is_asf = type(audio).__name__ == "ASF"
    for key, value in values.items():
        audio[_ASF_KEYS.get(key, key) if is_asf else key] = [value]
    audio.save()


def _embed_cover(path: str, cover: Tuple[bytes, str]) -> None:
    """把封面嵌进标签；不支持的容器直接跳过（不算失败）。"""
    data, suffix = prepare_embedded_cover(*cover)
    if not data:
        return
    mime = _image_mime(suffix)
    try:
        from mutagen import File as MutagenFile

        audio = MutagenFile(path)
    except Exception as e:
        logger.debug("读取媒体文件失败，跳过封面嵌入 %s: %s", path, e)
        return
    if audio is None:
        return

    try:
        if _embed_flac_picture(audio, data, mime):
            audio.save()
            return
        if _embed_id3_apic(audio, data, mime):
            audio.save()
            return
        if _embed_mp4_cover(audio, data, suffix):
            audio.save()
            return
        logger.debug("该容器不支持内嵌封面，跳过：%s", path)
    except Exception as e:
        # 封面嵌入失败不该让整首歌算失败：文本标签已经写好了
        logger.debug("嵌入封面失败 %s: %s", path, e)


def _embed_flac_picture(audio: Any, data: bytes, mime: str) -> bool:
    """FLAC / OggVorbis：FLAC 用 ``add_picture``，Ogg 用 base64 的图片块。"""
    add_picture = getattr(audio, "add_picture", None)
    if callable(add_picture):
        from mutagen.flac import Picture

        clear = getattr(audio, "clear_pictures", None)
        if callable(clear):
            clear()
        picture = Picture()
        picture.type = _PICTURE_TYPE_COVER
        picture.mime = mime
        picture.desc = "Cover"
        picture.data = data
        add_picture(picture)
        return True

    if type(audio).__name__ in ("OggVorbis", "OggOpus"):
        import base64

        from mutagen.flac import Picture

        picture = Picture()
        picture.type = _PICTURE_TYPE_COVER
        picture.mime = mime
        picture.desc = "Cover"
        picture.data = data
        if audio.tags is None:
            return False
        audio["metadata_block_picture"] = [base64.b64encode(picture.write()).decode("ascii")]
        return True
    return False


def _embed_id3_apic(audio: Any, data: bytes, mime: str) -> bool:
    """MP3 / WAV / AIFF：ID3 的 ``APIC`` 帧。"""
    tags = getattr(audio, "tags", None)
    add = getattr(tags, "add", None)
    if tags is None or not callable(add):
        return False
    try:
        from mutagen.id3 import APIC
    except ImportError:  # pragma: no cover - mutagen 精简安装
        return False
    try:
        tags.delall("APIC")
    except Exception:
        pass
    tags.add(APIC(encoding=3, mime=mime, type=_PICTURE_TYPE_COVER, desc="Cover", data=data))
    return True


def _embed_mp4_cover(audio: Any, data: bytes, suffix: str) -> bool:
    """MP4 / M4A：``covr`` 原子。"""
    if type(audio).__name__ != "MP4":
        return False
    from mutagen.mp4 import MP4Cover

    fmt = MP4Cover.FORMAT_PNG if str(suffix).lower() == ".png" else MP4Cover.FORMAT_JPEG
    audio["covr"] = [MP4Cover(data, imageformat=fmt)]
    return True


def _image_mime(suffix: str) -> str:
    return {
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(str(suffix or "").lower(), "image/jpeg")


# ──────────────────────────────────────────────────────────────
# 内嵌封面的体积控制
# ──────────────────────────────────────────────────────────────

#: 内嵌封面的体积上限（超过就压缩）与最长边。实测网易云有些单曲封面是
#: **7 MB 的 PNG** —— 原样嵌进去，一首 320K 的歌体积要白涨三分之一，
#: 而播放器显示内嵌图根本用不到 2000px。
EMBED_COVER_MAX_BYTES = 1_500_000
EMBED_COVER_MAX_EDGE = 1200
#: 压缩后的 JPEG 质量（88 在肉眼无差与体积之间取得比较稳的平衡）
EMBED_COVER_QUALITY = 88


def prepare_embedded_cover(
    data: bytes,
    suffix: str,
    *,
    max_bytes: int = EMBED_COVER_MAX_BYTES,
    max_edge: int = EMBED_COVER_MAX_EDGE,
) -> Tuple[bytes, str]:
    """把要**内嵌**的封面压到合适体积，返回 ``(数据, 扩展名)``。

    小图原样返回（不动它）；超过 ``max_bytes`` 的用 Qt 缩到 ``max_edge`` 并转成
    JPEG。压缩失败、或压完反而更大时一律退回原图 —— 内嵌封面是锦上添花，
    不该让整个下载失败。
    """
    if not data or len(data) <= max(0, int(max_bytes)):
        return data, suffix
    try:
        from PySide6.QtCore import QBuffer, QByteArray, Qt
        from PySide6.QtGui import QImage

        image = QImage.fromData(QByteArray(data))
        if image.isNull():
            return data, suffix
        if max(image.width(), image.height()) > max_edge > 0:
            image = image.scaled(
                max_edge, max_edge, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        buffer = QBuffer()
        buffer.open(QBuffer.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "JPEG", EMBED_COVER_QUALITY):
            return data, suffix
        packed = bytes(buffer.data())
        if 0 < len(packed) < len(data):
            return packed, ".jpg"
    except Exception as e:  # pragma: no cover - 依赖 Qt 图像插件
        logger.debug("压缩内嵌封面失败，改用原图: %s", e)
    return data, suffix


# ──────────────────────────────────────────────────────────────
# 封面取回
# ──────────────────────────────────────────────────────────────


def fetch_cover(track: Any) -> Optional[Tuple[bytes, str]]:
    """取封面的原始字节与扩展名（失败返回 ``None``，不抛异常）。

    直接复用「保存封面」那条链路：它会先摘掉 CDN 的缩略图参数拿原图，
    再按文件头纠正扩展名。
    """
    from . import covers

    try:
        return covers.resolve_cover(track)
    except covers.CoverError as e:
        logger.debug("取封面失败（不影响下载）: %s", e)
        return None
    except Exception as e:  # pragma: no cover - 兜底
        logger.debug("取封面异常（不影响下载）: %s", e)
        return None


def _text(track: Any, key: str) -> str:
    return str(field(track, key) or "").strip()
