"""音频文件解析与本地音频工具（阶段 1 任务 1.4-A，形态 1：整体搬家）。

这 10 个函数原先住在 `ui/app_music.py`（5116 行）的模块顶层，实测**零 GUI 触点**：
不 import 任何 GUI 栈、不碰控件、不调用 `after`、不使用 `ui.i18n` 的 `_()`
（`format_*` 返回的 "FLAC"/"320K"/"1.2w" 是技术标签，不是界面文案）。

## 函数体为什么逐字一致

除两处**机械改写**外，函数体与 `git show HEAD:ui/app_music.py` 的原文逐字节相同：

1. 定义名去掉前导下划线（`_format_time` → `format_time`），成为公开 API；
2. 函数体内 4 处对旧私有名的调用改为新公开名（`_get_tag` × 3、`_extract_audio_metadata` × 1）。

等价性由 `poc/_verify_1_4a_music.py` 用 AST 逐节点核对（把新名机械替换回旧名后
必须与原文完全相同），不是靠人眼。

## 降级开关为什么留在这里、且必须是"模块级全局名"

`tests/test_music_fallback.py` 靠 monkeypatch 打这些开关来测降级分支。
搬家前补丁点在 `ui.app_music`，搬家后**真正被读取的名字**变成了本模块的全局名，
所以补丁点随实现一起搬到 `services.music_audio`（该测试文件已同步改指，断言未变）。

函数体里一律以**模块级全局名**读取开关（`_mutagen_import_error`、`_winsdk_available`）
与模块（`shutil`、`subprocess`），不要写成"导入时快照进局部变量"——那样
`monkeypatch.setattr(services.music_audio, "_mutagen_import_error", ...)` 会静默失效。

`_winsdk_available` 的探测块住在 `services/music_smtc.py`（SMTC 媒体控制与转码
共用同一份探测结果），这里用 `from ... import` 拿到**本模块自己的绑定**，
于是 `services.music_audio._winsdk_available` 才是转码真正读的那个名字。
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from collections import OrderedDict
from typing import Dict, Optional

from logzero import logger

#: Windows 媒体基础（Media Foundation）是否可用。真正的探测在 services/music_smtc.py，
#: 这里只是取一份本模块的绑定，供 `transcode_audio_to_wav` 判断原生转码分支能否走。
from services.music_smtc import _winsdk_available  # noqa: F401

_mutagen_import_error = None
try:
    from mutagen import File as MutagenFile
    from mutagen.flac import FLAC
    from mutagen.id3 import ID3
    from mutagen.mp3 import MP3
    from mutagen.mp4 import MP4
    from mutagen.oggvorbis import OggVorbis
except ImportError as e:
    _mutagen_import_error = e

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus", ".aiff"}

MUSIC_METADATA_CACHE_MAX = 200

# ── 在线音频下载校验 ──────────────────────────────────
# 文件头魔数 -> 扩展名（用于识别 HTML 错误页/空文件等无效响应）
_AUDIO_FILE_MAGIC = (
    (b"ID3", ".mp3"),  # MP3 (ID3v2)
    (b"fLaC", ".flac"),  # FLAC
    (b"OggS", ".ogg"),  # OGG/Opus
    (b"RIFF", ".wav"),  # WAV
)
_M4A_FTYP_MAGIC = b"ftyp"  # M4A/MP4: 前4字节为 box 大小，offset 4 处为 ftyp
# 时长校验：实际时长与预期相差比例容差 + 最小容差（秒），防 VIP 试听片段等截断文件
_DURATION_TOLERANCE_RATIO = 0.2
_DURATION_TOLERANCE_MIN_SEC = 10

# ── 音质显示 ─────────────────────────────────────────
# 无损容器（即使码率字段缺失也按无损显示）
_LOSSLESS_EXTENSIONS = {".flac", ".ape", ".wav", ".aiff", ".alac"}


class MetadataCache:
    """曲目元数据 LRU 缓存（原先内嵌在 `MusicPlayerMixin._get_metadata` 里）。

    放在本模块而不是 `music_player.py` 的理由：它只依赖"路径 → 元数据 dict"这一件事，
    容量上限 `MUSIC_METADATA_CACHE_MAX` 就定义在上面，而 `music_player.py` 的职责是
    播放状态机。缓存本身是纯数据结构（不碰 mixer、不碰界面），放哪都不会引入 UI 依赖。

    刻意暴露 `data`（一个 `OrderedDict`）：界面侧仍有旧代码直接 `self._music_metadata_cache.clear()`
    （`_music_scan_folder_restore`，本轮范围外），让界面**采纳同一个 OrderedDict 对象**
    比让两边各持一份、再靠同步保持一致要可靠得多。
    """

    def __init__(self, max_size: int = MUSIC_METADATA_CACHE_MAX) -> None:
        self.max_size = max_size
        self.data: OrderedDict = OrderedDict()

    def get(self, key, factory):
        """命中则移到末尾并返回；未命中则调用 factory 生成、按容量上限淘汰后返回。"""
        if key in self.data:
            self.data.move_to_end(key)
            return self.data[key]
        value = factory(key)
        self.data[key] = value
        while len(self.data) > self.max_size:
            self.data.popitem(last=False)
        return value

    def clear(self) -> None:
        self.data.clear()

    def __len__(self) -> int:
        return len(self.data)


def extract_audio_metadata(filepath: str) -> Dict[str, any]:
    result = {
        "title": os.path.splitext(os.path.basename(filepath))[0],
        "artist": "",
        "album": "",
        "duration": 0,
        "bitrate": 0,
        "has_cover": False,
        "cover_data": None,
    }
    if _mutagen_import_error is not None:
        return result
    try:
        audio = MutagenFile(filepath)
        if audio is None:
            return result
        ext = os.path.splitext(filepath)[1].lower()

        # 通用码率/时长提取（各格式均有 info.bitrate）
        try:
            if hasattr(audio, "info"):
                info = audio.info
                if hasattr(info, "bitrate"):
                    result["bitrate"] = int(getattr(info, "bitrate", 0) or 0)
                if hasattr(info, "length"):
                    result["duration"] = info.length
        except Exception:
            pass

        if ext == ".mp3":
            if hasattr(audio, "info") and hasattr(audio.info, "length"):
                result["duration"] = audio.info.length
            if hasattr(audio, "tags"):
                tags = audio.tags
                if tags:
                    result["title"] = get_tag(tags, "TIT2") or result["title"]
                    result["artist"] = get_tag(tags, "TPE1") or ""
                    result["album"] = get_tag(tags, "TALB") or ""
                    for tag_name in tags.keys():
                        if tag_name.startswith("APIC:"):
                            result["has_cover"] = True
                            result["cover_data"] = tags[tag_name].data
                            break
        elif ext == ".flac":
            flac = FLAC(filepath)
            if hasattr(flac, "info") and hasattr(flac.info, "length"):
                result["duration"] = flac.info.length
            if flac.tags:
                result["title"] = flac.tags.get("title", [result["title"]])[0] or result["title"]
                result["artist"] = flac.tags.get("artist", [""])[0]
                result["album"] = flac.tags.get("album", [""])[0]
            if flac.pictures:
                result["has_cover"] = True
                result["cover_data"] = flac.pictures[0].data
        elif ext == ".ogg":
            ogg = OggVorbis(filepath)
            if hasattr(ogg, "info") and hasattr(ogg.info, "length"):
                result["duration"] = ogg.info.length
            if ogg.tags:
                result["title"] = ogg.tags.get("title", [result["title"]])[0] or result["title"]
                result["artist"] = ogg.tags.get("artist", [""])[0]
                result["album"] = ogg.tags.get("album", [""])[0]
            for key in ogg:
                if key.startswith("cover") or key.startswith("metadata_block_picture"):
                    result["has_cover"] = True
                    result["cover_data"] = ogg[key][0] if isinstance(ogg[key], list) else ogg[key]
                    break
        elif ext == ".m4a" or ext == ".mp4":
            mp4 = MP4(filepath)
            if hasattr(mp4, "info") and hasattr(mp4.info, "length"):
                result["duration"] = mp4.info.length
            if mp4.tags:
                result["title"] = mp4.tags.get("\xa9nam", [result["title"]])[0] or result["title"]
                result["artist"] = mp4.tags.get("\xa9ART", [""])[0]
                result["album"] = mp4.tags.get("\xa9alb", [""])[0]
            if hasattr(mp4, "covr") and mp4.covr:
                result["has_cover"] = True
                result["cover_data"] = bytes(mp4.covr[0])
        else:
            try:
                if hasattr(audio, "info") and hasattr(audio.info, "length"):
                    result["duration"] = audio.info.length
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"读取音频元数据失败: {filepath}: {e}")
    return result

def get_tag(tags, tag_id: str) -> Optional[str]:
    try:
        frame = tags.get(tag_id)
        if frame:
            return str(frame.text[0]) if hasattr(frame, "text") else str(frame)
    except Exception:
        pass
    return None

def format_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"

def format_play_count(count: int) -> str:
    """播放量格式化：<1万 显示完整数字，>=1万 显示 x.xw（整数时省略小数，如 12345→1.2w、10000→1w）"""
    if count <= 0:
        return ""
    if count < 10000:
        return str(count)
    text = f"{count / 10000.0:.1f}".rstrip("0").rstrip(".")
    return f"{text}w"

def format_online_quality(quality: str) -> str:
    """在线播放音质标签：音源实际获取到的音质档位"""
    return {
        "flac24bit": "FLAC",
        "flac": "FLAC",
        "320k": "320K",
        "128k": "128K",
    }.get(quality or "", "")

def format_local_quality(meta: dict, filepath: str = "") -> str:
    """本地播放音质标签：按文件实际码率/格式判定

    Returns:
        "FLAC" / "320K" / "256K" / "192K" / "128K"，未知（码率缺失）返回空串
    """
    ext = os.path.splitext(filepath or "")[1].lower()
    if ext in _LOSSLESS_EXTENSIONS:
        return "FLAC"
    bitrate = int(meta.get("bitrate") or 0)
    if bitrate >= 900000:
        return "FLAC"
    kbps = bitrate // 1000
    if kbps >= 320:
        return "320K"
    if kbps >= 256:
        return "256K"
    if kbps >= 192:
        return "192K"
    if kbps >= 128:
        return "128K"
    return ""

def validate_audio_file_header(filepath: str) -> bool:
    """校验文件头是否为有效音频（防 HTML 错误页/空文件伪装成音频）"""
    try:
        with open(filepath, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    if not head:
        return False
    for magic, _ext in _AUDIO_FILE_MAGIC:
        if head.startswith(magic):
            return True
    # MP3 裸帧同步 (0xFF Ex)
    if len(head) >= 2 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return True
    # M4A/MP4: offset 4 处为 ftyp box
    if len(head) >= 8 and head[4:8] == _M4A_FTYP_MAGIC:
        return True
    return False

def validate_audio_duration(filepath: str, expected_seconds: int) -> bool:
    """校验音频实际时长与预期是否一致（防 VIP 试听片段/截断文件）

    双方时长任一未知（mutagen 不可用/解析失败/预期未知）时放行。
    """
    if expected_seconds <= 0 or _mutagen_import_error is not None:
        return True
    try:
        meta = extract_audio_metadata(filepath)
    except Exception:
        return True
    actual = meta.get("duration", 0)
    if actual <= 0:
        return True
    tolerance = max(_DURATION_TOLERANCE_MIN_SEC, expected_seconds * _DURATION_TOLERANCE_RATIO)
    return abs(actual - expected_seconds) <= tolerance

def is_m4a_container(filepath: str) -> bool:
    """检测文件是否为 MP4/AAC 容器（ftyp box）

    不依赖扩展名：B站 dash URL 带查询参数时文件可能被命名为 .mp3，
    但内容是 AAC（SDL_mixer 无法解码）。
    """
    if filepath.lower().endswith(".m4a"):
        return True
    try:
        with open(filepath, "rb") as f:
            head = f.read(8)
        return len(head) >= 8 and head[4:8] == _M4A_FTYP_MAGIC
    except OSError:
        return False

def transcode_audio_to_wav(filepath: str) -> Optional[str]:
    """将 m4a/m4s（AAC 容器）转码为 wav 供 pygame 播放。

    pygame 的 SDL_mixer 不支持 MP4/AAC 容器（B站 dash 音频流与部分平台
    音源是 m4a）。优先用 Windows Media Foundation（winsdk，系统原生无
    外部依赖），回退系统 ffmpeg。失败返回 None（调用方保留原文件）。

    Args:
        filepath: 音频文件路径（按文件头检测 MP4 容器，不依赖扩展名）

    Returns:
        转码后的 wav 文件路径，无需转码或失败时为 None
    """
    if not is_m4a_container(filepath):
        return None

    def _finish_ok(wav_path: str) -> Optional[str]:
        if os.path.getsize(wav_path) > 0:
            return wav_path
        try:
            os.remove(wav_path)
        except Exception:
            pass
        return None

    # 1. Windows Media Foundation 原生转码
    if _winsdk_available:
        try:
            import asyncio

            from winsdk.windows.media.mediaproperties import AudioEncodingQuality, MediaEncodingProfile
            from winsdk.windows.media.transcoding import MediaTranscoder
            from winsdk.windows.storage import StorageFile

            async def _transcode(src_path: str, dst_path: str) -> bool:
                source = await StorageFile.get_file_from_path_async(src_path)
                open(dst_path, "wb").close()  # MF 要求目标文件已存在
                dest = await StorageFile.get_file_from_path_async(dst_path)
                transcoder = MediaTranscoder()
                profile = MediaEncodingProfile.create_wav(AudioEncodingQuality.HIGH)
                prep = await transcoder.prepare_file_transcode_async(source, dest, profile)
                if not prep.can_transcode:
                    return False
                await prep.transcode_async()
                return True

            fd, wav_path = tempfile.mkstemp(suffix=".wav", prefix="fmcl_conv_")
            os.close(fd)
            try:
                ok = asyncio.run(_transcode(filepath, wav_path))
            except Exception:
                ok = False
            if ok:
                result = _finish_ok(wav_path)
                if result:
                    logger.info(f"Media Foundation 转码成功: {filepath} -> {result}")
                    return result
        except Exception as e:
            logger.warning(f"Media Foundation 转码失败: {e}")

    # 2. ffmpeg 回退
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        try:
            fd, out_path = tempfile.mkstemp(suffix=".wav", prefix="fmcl_conv_")
            os.close(fd)
            os.remove(out_path)
            proc = subprocess.run(
                [ffmpeg, "-y", "-i", filepath, "-acodec", "pcm_s16le", out_path],
                capture_output=True,
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if proc.returncode == 0:
                result = _finish_ok(out_path)
                if result:
                    logger.info(f"ffmpeg 转码成功: {filepath} -> {result}")
                    return result
            try:
                os.remove(out_path)
            except Exception:
                pass
        except Exception as e:
            logger.warning(f"ffmpeg 转码失败: {e}")

    logger.warning(f"m4a 转码不可用（无 Media Foundation/ffmpeg），保留原文件: {filepath}")
    return None

__all__ = [
    "AUDIO_EXTENSIONS",
    "MetadataCache",
    "MUSIC_METADATA_CACHE_MAX",
    "extract_audio_metadata",
    "format_local_quality",
    "format_online_quality",
    "format_play_count",
    "format_time",
    "get_tag",
    "is_m4a_container",
    "transcode_audio_to_wav",
    "validate_audio_duration",
    "validate_audio_file_header",
]
