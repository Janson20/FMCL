"""文件名清洗、命名模板与扩展名纠正。

封面保存（:mod:`app.core.covers`）与歌曲下载（:mod:`app.core.downloader`）是两条
互不相干的链路，但有三件事一模一样：把任意文本变成 Windows 能用的文件名、别让
同名文件互相覆盖、按**文件头**而不是地址纠正扩展名。原先这些只服务封面、写在
``covers.py`` 里，再加一份下载用的迟早会走样，所以抽到这里两边共用。

音频后缀的判据单独放在这里（而不是各个调用方各写一套）：网易云给的是 ``.mp3``
结尾的地址却可能回 m4a，B 站的 dash 音轨根本没有后缀，只有魔术字节说了算。
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .trackutil import field

#: Windows 文件名里非法的字符（含控制字符）
_ILLEGAL_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")
#: Windows 保留设备名：这些名字（带扩展名也算）不能当文件名
_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
#: 文件名主干长度上限（加上后缀与序号也不会顶到文件系统的 255 字节）
MAX_STEM_LENGTH = 120
#: 模板替换后要抹掉的边缘分隔符（歌手为空时「歌手 - 歌名」会剩下一个孤零零的「 - 」）
_EDGE_CHARS = " \t-—–_·.,"
#: 支持的同名策略（与设置界面、配置项共用一份）
DUPLICATE_MODES = ("rename", "skip", "overwrite")

#: 音频文件头 → 扩展名（顺序有意义：先精确匹配，再按帧同步猜）
_AUDIO_MAGIC = (
    (b"ID3", ".mp3"),
    (b"fLaC", ".flac"),
    (b"OggS", ".ogg"),
    (b"MAC ", ".ape"),
    (b"wvpk", ".wv"),
    (b"\x30\x26\xb2\x75", ".wma"),
)
#: 无损档位落盘时先按 .flac 猜（真格式下载完再按文件头纠正）
_LOSSLESS_QUALITIES = ("flac", "flac24bit")


# ──────────────────────────────────────────────────────────────
# 文件名清洗
# ──────────────────────────────────────────────────────────────


def sanitize_filename(name: str, fallback: str = "未命名") -> str:
    """把任意文本清洗成 Windows 能用的文件名主干（不含扩展名）。"""
    text = _ILLEGAL_CHARS.sub("_", str(name or ""))
    text = _WHITESPACE.sub(" ", text).strip()
    # Windows 不允许文件名以点或空格结尾（"歌名." 会被静默截断）
    text = text.rstrip(". ")
    if not text:
        text = fallback
    if text.split(".")[0].lower() in _RESERVED_STEMS:
        text = "_" + text
    if len(text) > MAX_STEM_LENGTH:
        text = text[:MAX_STEM_LENGTH].rstrip(". ")
    return text or fallback


#: 命名模板里支持的占位符：{singer} {name} {album} {source} {index}
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def format_filename(template: str, values: Dict[str, Any], fallback: str = "未命名") -> str:
    """按模板拼文件名主干。

    未知占位符替换成空串（而不是原样留下 ``{foo}`` —— 花括号会被清洗成下划线，
    用户看到的是一串莫名其妙的 ``_foo_``）。拼完先抹掉两端的连接符，
    ``{singer}`` 为空时 ``"{singer} - {name}"`` 得到的是歌名本身，而不是「 - 歌名」。
    """
    text = str(template or "").strip() or "{name}"

    def _replace(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key == "index":
            index = values.get("index")
            return "" if not index else str(index)
        value = values.get(key)
        return "" if value is None else str(value)

    text = _PLACEHOLDER.sub(_replace, text)
    text = _WHITESPACE.sub(" ", text)
    text = text.strip(_EDGE_CHARS)
    return sanitize_filename(text, fallback)


def track_values(track: Any, index: int = 0) -> Dict[str, Any]:
    """曲目 → 模板占位符取值（``Track`` 与 QML 传来的字典都能吃）。"""
    from ..sources import SOURCE_NAMES

    source = str(field(track, "source") or "")
    return {
        "name": str(field(track, "name") or "").strip(),
        "singer": str(field(track, "singer") or "").strip(),
        "album": str(field(track, "album") or "").strip(),
        "source": SOURCE_NAMES.get(source, source),
        "index": int(index or 0),
    }


def suggested_stem(
    track: Any,
    template: str = "{singer} - {name}",
    index: int = 0,
    *,
    fallback: str = "",
) -> str:
    """曲目 + 模板 → 文件名主干（缺歌名时退化成 ``fallback`` /「未命名」，不是空串）。

    ``fallback`` 给「调用方有更合适的缺省名」时用：存封面要叫「封面」，下载要叫
    「未命名」—— 但拼法与清洗规则必须是同一套，否则两边又会长出两套。
    """
    values = track_values(track, index=index)
    name = str(fallback or values.get("name") or values.get("singer") or "未命名")
    return format_filename(template, values, fallback=name)


# ──────────────────────────────────────────────────────────────
# 同名处理
# ──────────────────────────────────────────────────────────────


def resolve_target(path: Path, mode: str = "rename", limit: int = 999) -> Optional[Path]:
    """按同名策略决定最终落盘路径。

    * ``overwrite`` —— 原样返回（覆盖）；
    * ``skip`` —— 目标已存在返回 ``None``，调用方据此判「跳过」；
    * 其它（含非法值）—— 自动加 ``(1)`` ``(2)`` …，全被占满时用时间戳兜底。
    """
    target = Path(path)
    mode = str(mode or "").strip().lower()
    if mode == "overwrite":
        return target
    if not target.exists():
        return target
    if mode == "skip":
        return None
    stem, suffix = target.stem, target.suffix
    for i in range(1, max(1, int(limit)) + 1):
        candidate = target.with_name(f"{stem} ({i}){suffix}")
        if not candidate.exists():
            return candidate
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return target.with_name(f"{stem} ({stamp}){suffix}")


# ──────────────────────────────────────────────────────────────
# 扩展名
# ──────────────────────────────────────────────────────────────


def sniff_audio_suffix(head: bytes) -> str:
    """按文件头判断音频格式，返回扩展名；认不出来返回空串。"""
    if not head:
        return ""
    for magic, suffix in _AUDIO_MAGIC:
        if head.startswith(magic):
            return suffix
    if len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if len(head) >= 8 and head[4:8] == b"ftyp":
        return ".m4a"
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        # MPEG 帧同步头：0xFFF1 / 0xFFF9 是 ADTS AAC，其余按 mp3 处理
        if head[1] in (0xF1, 0xF9):
            return ".aac"
        return ".mp3"
    return ""


def sniff_file_suffix(path: str, size: int = 16) -> str:
    """读文件头若干字节判断格式（读不出来返回空串）。"""
    try:
        with open(path, "rb") as f:
            head = f.read(max(8, int(size)))
    except OSError:
        return ""
    return sniff_audio_suffix(head)


def quality_suffix(quality: str) -> str:
    """音质档位 → 猜测的扩展名（无损给 .flac，其余给 .mp3）。"""
    return ".flac" if str(quality or "").strip().lower() in _LOSSLESS_QUALITIES else ".mp3"


def correct_suffix(path: Path, suffix: str) -> Path:
    """把扩展名纠正成文件头认出来的那个（认不出来就原样返回）。"""
    target = Path(path)
    want = str(suffix or "").strip().lower()
    if not want:
        return target
    if target.suffix.lower() == want:
        return target
    return target.with_suffix(want)
