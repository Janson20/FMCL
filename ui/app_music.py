"""ModernApp 音乐播放器 Mixin - 音乐标签页相关方法"""

import json
import math
import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter.filedialog as filedialog
import webbrowser
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import customtkinter as ctk
import requests
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _
from ui.music_desktop_lyric import DesktopLyricWindow
from ui.music_effects import (
    EQ_FREQS,
    EQ_GAIN_MAX,
    EQ_GAIN_MIN,
    PITCH_MAX,
    PITCH_MIN,
    SPEED_MAX,
    SPEED_MIN,
    AudioEffectProcessor,
    EffectSettings,
)
from ui.music_lyrics import LyricLine, LyricParser
from ui.music_playlist import (
    HISTORY_PLAYLIST_ID,
    SORT_ADD_TIME_ASC,
    SORT_ADD_TIME_DESC,
    SORT_NAME_ASC,
    SORT_NAME_DESC,
    Playlist,
    PlaylistManager,
    PlaylistSong,
)
from ui.music_risk_captcha import run_captcha_flow
from ui.music_source import MUSIC_SOURCES, SOURCE_META, resolve_track, search_all
from ui.music_source.base import MusicInfo as OnlineMusicInfo

_pygame_import_error = None
try:
    import pygame
    import pygame.mixer as mixer
except ImportError as e:
    _pygame_import_error = e

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

_winsdk_import_error = None
if platform.system().lower() == "windows":
    try:
        import asyncio as _asyncio_for_smtc

        from winsdk.windows.media import (
            MediaPlaybackStatus,
            SystemMediaTransportControls,
            SystemMediaTransportControlsButton,
            SystemMediaTransportControlsDisplayUpdater,
        )
        from winsdk.windows.storage.streams import DataWriter, InMemoryRandomAccessStream, RandomAccessStreamReference

        _winsdk_available = True
    except ImportError as e:
        _winsdk_import_error = e
        _winsdk_available = False
else:
    _winsdk_available = False
    _winsdk_import_error = "非 Windows 平台"

AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus", ".aiff"}

# 网易云账号歌单（侧边栏同步，只读）:
# 条目 id 前缀（区分本地歌单与远程歌单，远程歌单不进入 PlaylistManager，
# 因此永不落盘、永不参与本地歌单的任何编辑操作）
_WY_REMOTE_PREFIX = "wy:"
# 远程歌单歌曲列表每页条数（超过 20 首时按页展示）
_MUSIC_WY_PAGE_SIZE = 20
# 定期自动刷新歌单列表的间隔（毫秒）
_WY_REMOTE_PERIODIC_MS = 10 * 60 * 1000

PLAY_MODE_SEQUENTIAL = 0
PLAY_MODE_LOOP_LIST = 1
PLAY_MODE_LOOP_SINGLE = 2
PLAY_MODE_RANDOM = 3

PLAY_MODE_NAMES = {
    PLAY_MODE_SEQUENTIAL: "sequential",
    PLAY_MODE_LOOP_LIST: "loop_list",
    PLAY_MODE_LOOP_SINGLE: "loop_single",
    PLAY_MODE_RANDOM: "random",
}

DEFAULT_HOTKEYS = {
    "play_pause": "ctrl+shift+space",
    "prev": "ctrl+shift+left",
    "next": "ctrl+shift+right",
    "stop": "ctrl+shift+down",
    "vol_up": "ctrl+shift+up",
    "vol_down": "ctrl+shift+page down",
    "vol_mute": "ctrl+shift+m",
}

FADE_STEPS = 20
FADE_INTERVAL_MS = 50

MUSIC_METADATA_CACHE_MAX = 200

MUSIC_ORIGINAL_FEEDBACK_URL = "https://doc.weixin.qq.com/forms/AKgAhAf7ABQAUoAtgbcAHkCNf0v0B41mf"

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

_hotkey_import_error = None
try:
    import keyboard as _keyboard

    _keyboard_available = True
except Exception as e:
    _hotkey_import_error = e
    _keyboard_available = False


def _extract_audio_metadata(filepath: str) -> Dict[str, any]:
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
                    result["title"] = _get_tag(tags, "TIT2") or result["title"]
                    result["artist"] = _get_tag(tags, "TPE1") or ""
                    result["album"] = _get_tag(tags, "TALB") or ""
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


def _get_tag(tags, tag_id: str) -> Optional[str]:
    try:
        frame = tags.get(tag_id)
        if frame:
            return str(frame.text[0]) if hasattr(frame, "text") else str(frame)
    except Exception:
        pass
    return None


def _format_time(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}:{s:02d}"


def _format_play_count(count: int) -> str:
    """播放量格式化：<1万 显示完整数字，>=1万 显示 x.xw（整数时省略小数，如 12345→1.2w、10000→1w）"""
    if count <= 0:
        return ""
    if count < 10000:
        return str(count)
    text = f"{count / 10000.0:.1f}".rstrip("0").rstrip(".")
    return f"{text}w"


# ── 音质显示 ─────────────────────────────────────────
# 无损容器（即使码率字段缺失也按无损显示）
_LOSSLESS_EXTENSIONS = {".flac", ".ape", ".wav", ".aiff", ".alac"}


def _format_online_quality(quality: str) -> str:
    """在线播放音质标签：音源实际获取到的音质档位"""
    return {
        "flac24bit": "FLAC",
        "flac": "FLAC",
        "320k": "320K",
        "128k": "128K",
    }.get(quality or "", "")


def _format_local_quality(meta: dict, filepath: str = "") -> str:
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


def _validate_audio_file_header(filepath: str) -> bool:
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


def _validate_audio_duration(filepath: str, expected_seconds: int) -> bool:
    """校验音频实际时长与预期是否一致（防 VIP 试听片段/截断文件）

    双方时长任一未知（mutagen 不可用/解析失败/预期未知）时放行。
    """
    if expected_seconds <= 0 or _mutagen_import_error is not None:
        return True
    try:
        meta = _extract_audio_metadata(filepath)
    except Exception:
        return True
    actual = meta.get("duration", 0)
    if actual <= 0:
        return True
    tolerance = max(_DURATION_TOLERANCE_MIN_SEC, expected_seconds * _DURATION_TOLERANCE_RATIO)
    return abs(actual - expected_seconds) <= tolerance


def _is_m4a_container(filepath: str) -> bool:
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


def _transcode_audio_to_wav(filepath: str) -> Optional[str]:
    """将 m4a/m4s（AAC 容器）转码为 wav 供 pygame 播放。

    pygame 的 SDL_mixer 不支持 MP4/AAC 容器（B站 dash 音频流与部分平台
    音源是 m4a）。优先用 Windows Media Foundation（winsdk，系统原生无
    外部依赖），回退系统 ffmpeg。失败返回 None（调用方保留原文件）。

    Args:
        filepath: 音频文件路径（按文件头检测 MP4 容器，不依赖扩展名）

    Returns:
        转码后的 wav 文件路径，无需转码或失败时为 None
    """
    if not _is_m4a_container(filepath):
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


class _SMTCController:
    def __init__(self):
        self._smtc = None
        self._callbacks: Dict[str, callable] = {}
        self._initialized = False
        self._parent = None

    @property
    def available(self) -> bool:
        return _winsdk_available

    def set_parent(self, parent):
        self._parent = parent

    def initialize(self, callbacks: Dict[str, callable]):
        self._callbacks = callbacks

    def update_now_playing(self, title: str, artist: str, album: str, cover_data: Optional[bytes] = None):
        if not self.available or not self._parent:
            return
        self._parent.after(0, lambda: self._update_now_playing_main(title, artist, album, cover_data))

    def _update_now_playing_main(self, title: str, artist: str, album: str, cover_data: Optional[bytes] = None):
        try:
            import asyncio

            async def _update():
                smtc = SystemMediaTransportControls.get_for_current_view()
                updater = smtc.display_updater
                updater.type = 3
                props = updater.music_properties
                props.title = title or ""
                props.artist = artist or ""
                props.album_title = album or ""
                if cover_data:
                    try:
                        thumbnail = await self._create_thumbnail_stream(cover_data)
                        if thumbnail:
                            updater.thumbnail = thumbnail
                    except Exception:
                        pass
                updater.update()
                smtc.playback_status = 4
                smtc.is_play_enabled = True
                smtc.is_pause_enabled = True
                smtc.is_next_enabled = True
                smtc.is_previous_enabled = True
                smtc.is_stop_enabled = True

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_update())
                else:
                    loop.run_until_complete(_update())
            except RuntimeError:
                asyncio.run(_update())
        except Exception:
            pass

    async def _create_thumbnail_stream(self, cover_data: bytes):
        try:
            from io import BytesIO

            from PIL import Image

            image = Image.open(BytesIO(cover_data))
            image = image.resize((300, 300), Image.LANCZOS)
            buf = BytesIO()
            image.save(buf, format="PNG")
            png_data = buf.getvalue()
        except Exception:
            png_data = cover_data
        try:
            stream = InMemoryRandomAccessStream()
            writer = DataWriter(stream.get_output_stream_at(0))
            writer.write_bytes(list(png_data))
            await writer.store_async()
            await writer.flush_async()
            stream.seek(0)
            return RandomAccessStreamReference.create_from_stream(stream)
        except Exception:
            return None

    def set_playing(self):
        if not self.available or not self._parent:
            return
        self._parent.after(0, self._set_status_main, 4)

    def set_paused(self):
        if not self.available or not self._parent:
            return
        self._parent.after(0, self._set_status_main, 5)

    def set_stopped(self):
        if not self.available or not self._parent:
            return
        self._parent.after(0, self._set_status_main, 2)

    def _set_status_main(self, status: int):
        try:
            import asyncio

            async def _update():
                smtc = SystemMediaTransportControls.get_for_current_view()
                smtc.playback_status = status

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_update())
                else:
                    loop.run_until_complete(_update())
            except RuntimeError:
                asyncio.run(_update())
        except Exception:
            pass

    def clear(self):
        if not self.available or not self._parent:
            return
        self._parent.after(0, self._clear_main)

    def _clear_main(self):
        try:
            import asyncio

            async def _clear():
                smtc = SystemMediaTransportControls.get_for_current_view()
                smtc.display_updater.clear_all()
                smtc.playback_status = 0

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.ensure_future(_clear())
                else:
                    loop.run_until_complete(_clear())
            except RuntimeError:
                asyncio.run(_clear())
        except Exception:
            pass


class MusicPlayerMixin(object):
    def __init_music(self):
        self._music_playlist: List[str] = []
        self._music_current_index: int = -1
        self._music_is_playing: bool = False
        self._music_is_paused: bool = False
        self._music_volume: float = 0.7
        self._music_play_mode: int = PLAY_MODE_LOOP_LIST
        self._music_last_folder: str = ""
        self._music_progress: float = 0
        self._music_seek_offset: float = 0
        self._music_duration: float = 0
        self._music_metadata_cache: OrderedDict = OrderedDict()
        self._music_mini_mode: bool = False
        self._music_progress_timer_id = None
        self._music_init_done: bool = False
        self._music_hotkeys_registered: bool = False
        self._music_warmup_hook = None
        self._music_playlist_widgets: List[dict] = []
        self._music_smtc: _SMTCController = _SMTCController()
        self._music_smtc.set_parent(self)
        self._music_fade_timer_id = None
        self._music_is_fading = False
        self._music_fade_out_target: Optional[str] = None
        self._music_modes_used: set = set()
        # ── 歌单管理 ──
        self._music_playlist_manager: PlaylistManager = PlaylistManager()
        # ── 在线搜索状态 ──
        self._music_tab_mode: str = "local"  # "local" | "online"
        self._music_search_results: List[OnlineMusicInfo] = []
        self._music_search_widgets: List[dict] = []
        self._music_search_thread_id = None
        self._music_selected_source: str = "kw"
        self._music_search_keyword: str = ""
        self._music_search_page: int = 1  # 当前搜索页码（从 1 开始）
        self._music_search_page_size: int = 30  # 每页搜索条数
        self._music_search_has_more: bool = False  # 当前页是否满页（可能存在下一页）
        self._music_search_total_pages: int = 0  # 搜索结果总页数（音源提供总数时一次性算出，0=未知）
        self._music_search_busy: bool = False  # 搜索请求进行中标记
        self._music_search_seq: int = 0  # 搜索请求序号（防旧请求覆盖新请求）
        self._music_pager_widgets: List[dict] = []  # 分页页码按钮组件列表
        self._music_current_online_info: Optional[OnlineMusicInfo] = None
        self._music_is_online_playing: bool = False
        self._music_current_filepath: Optional[str] = None  # 当前播放的文件路径（本地/在线临时文件）
        self._music_current_quality: str = ""  # 在线播放实际获取到的音质档位（128k/320k/flac）
        self._music_temp_files: List[str] = []  # 缓存的临时文件列表
        self._music_stream_seq: int = 0  # 在线播放请求序号（防旧线程覆盖新请求）
        # ── 歌词状态 ──
        self._music_lyric_parser: LyricParser = LyricParser()
        self._music_lyric_lines: List[LyricLine] = []
        self._music_show_lyric_translation: bool = True
        self._music_show_lyric_roma: bool = False
        self._music_desktop_lyric: Optional[DesktopLyricWindow] = None
        self._music_lyric_poll_id = None
        # ── 音效状态 ──
        self._music_effects = AudioEffectProcessor()
        self._music_effects_processed_files: List[str] = []  # 效果处理产生的临时文件
        # ── 定时保存 ──
        self._music_periodic_save_id = None
        # ── 歌单上下文（播歌单中的歌曲时记录，供上下曲使用） ──
        self._music_playlist_context_songs: List[PlaylistSong] = []
        self._music_playlist_context_idx: int = -1
        # ── 网易云账号歌单同步（只读，不落盘） ──
        self._music_wy_remote_playlists: List[dict] = []  # [{id, name, track_count, cover_url}]
        self._music_wy_remote_cache: Dict[str, List[PlaylistSong]] = {}  # 歌单id -> 歌曲（内存缓存）
        self._music_wy_remote_view_id: Optional[str] = None  # 当前查看的远程歌单 id
        self._music_wy_remote_view_songs: List[PlaylistSong] = []  # 当前查看的远程歌单歌曲
        self._music_wy_loading_ids: Set[str] = set()  # 歌曲加载中的歌单 id（防重复请求）
        self._music_wy_sync_busy: bool = False  # 歌单列表同步进行中
        self._music_wy_sync_seq: int = 0  # 同步请求序号（防旧结果覆盖新请求）
        self._music_wy_sync_failed: bool = False  # 最近一次列表同步是否失败
        self._music_wy_remote_sort_mode: str = SORT_ADD_TIME_DESC  # 远程歌单内存排序模式
        self._music_wy_remote_page: int = 1  # 远程歌单当前页码（每页 _MUSIC_WY_PAGE_SIZE 首）
        self._music_wy_queue: "queue.Queue" = queue.Queue()  # 后台线程 -> 主线程事件队列
        self._music_wy_dispatcher_id = None
        self._music_wy_periodic_id = None

    def _init_music_lazy(self):
        if self._music_init_done:
            return
        self._music_init_done = True
        if _pygame_import_error is not None:
            logger.warning(f"pygame 导入失败: {_pygame_import_error}")
        else:
            try:
                pygame.init()
                mixer.init()
                try:
                    mixer.music.set_volume(0)
                    mixer.music.set_volume(self._music_volume)
                except Exception:
                    pass
                logger.info("pygame mixer 初始化完成")
            except Exception as e:
                logger.error(f"pygame mixer 初始化失败: {e}")
        self._load_music_state()
        self._music_apply_wy_saved_login()
        if self._music_playlist:
            self._rebuild_playlist_ui()
        # 启动网易云账号歌单同步调度器与定期刷新（已登录时生效，未登录自动跳过）
        try:
            self._music_wy_dispatcher_id = self.after(200, self._music_wy_dispatcher_tick)
            self._music_wy_start_periodic()
        except Exception as e:
            logger.debug(f"启动网易云歌单同步调度失败: {e}")
        # 启动定时保存（30 秒间隔，避免频繁写盘）
        self._music_start_periodic_save()

    def _build_music_tab_content(self):
        self.__init_music()
        self._music_tab_content = ctk.CTkFrame(self.music_tab, fg_color="transparent")
        self._music_tab_content.pack(fill=ctk.BOTH, expand=True)

        # 子标签页切换栏
        self._build_music_source_tabs()

        # 本地音乐主框架
        self._music_main_frame = ctk.CTkFrame(self._music_tab_content, fg_color="transparent")
        self._build_music_control_panel()
        self._build_music_playlist_panel()
        self._build_music_now_playing()
        self._build_music_mini_bar()
        self._music_mini_bar.pack_forget()

        # 在线搜索框架
        self._music_online_frame = ctk.CTkFrame(self._music_tab_content, fg_color="transparent")
        self._build_music_online_panel()

        # 歌单标签页
        self._build_music_playlist_tab_panel()

        self._music_main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        self._init_music_lazy()
        self.after(500, self._register_hotkeys)

    def _build_music_control_panel(self):
        panel = ctk.CTkFrame(self._music_main_frame, fg_color=COLORS["card_bg"], corner_radius=12)
        panel.pack(fill=ctk.X, pady=(0, 10))
        self._music_control_panel = panel

        top_row = ctk.CTkFrame(panel, fg_color="transparent")
        top_row.pack(fill=ctk.X, padx=12, pady=(12, 5))

        ctk.CTkLabel(
            top_row,
            text=_("music_open_folder"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self._music_folder_btn = ctk.CTkButton(
            top_row,
            text="📂",
            width=35,
            height=30,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["accent"],
            command=self._music_open_folder,
        )
        self._music_folder_btn.pack(side=ctk.LEFT, padx=(8, 0))

        self._music_folder_label = ctk.CTkLabel(
            top_row,
            text=_("music_no_folder"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._music_folder_label.pack(side=ctk.LEFT, padx=(10, 0))

        self._music_mini_toggle_btn = ctk.CTkButton(
            top_row,
            text=_("music_mini_mode"),
            width=80,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._music_toggle_mini_mode,
        )
        self._music_mini_toggle_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        self._music_song_count_label = ctk.CTkLabel(
            top_row, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=COLORS["text_secondary"]
        )
        self._music_song_count_label.pack(side=ctk.RIGHT, padx=(10, 0))

        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(fill=ctk.X, padx=12, pady=3)

        ctrl_row = ctk.CTkFrame(panel, fg_color="transparent")
        ctrl_row.pack(fill=ctk.X, padx=12, pady=(2, 8))

        play_btns = ctk.CTkFrame(ctrl_row, fg_color="transparent")
        play_btns.pack(side=ctk.LEFT)

        btn_cfg = {
            "width": 36,
            "height": 30,
            "font": ctk.CTkFont(size=14),
            "fg_color": COLORS["bg_light"],
            "hover_color": COLORS["accent"],
        }

        self._music_prev_btn = ctk.CTkButton(play_btns, text="⏮", command=self._music_prev, **btn_cfg)
        self._music_prev_btn.pack(side=ctk.LEFT, padx=2)

        self._music_play_btn = ctk.CTkButton(play_btns, text="▶", command=self._music_toggle_play, **btn_cfg)
        self._music_play_btn.pack(side=ctk.LEFT, padx=2)

        self._music_next_btn = ctk.CTkButton(play_btns, text="⏭", command=self._music_next, **btn_cfg)
        self._music_next_btn.pack(side=ctk.LEFT, padx=2)

        self._music_stop_btn = ctk.CTkButton(play_btns, text="⏹", command=self._music_stop, **btn_cfg)
        self._music_stop_btn.pack(side=ctk.LEFT, padx=2)

        self._music_mode_btn = ctk.CTkButton(
            ctrl_row,
            text="🔁",
            width=36,
            height=30,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._music_cycle_mode,
        )
        self._music_mode_btn.pack(side=ctk.LEFT, padx=(10, 0))
        self._update_mode_btn_text()

        # 音效按钮
        self._music_fx_btn = ctk.CTkButton(
            ctrl_row,
            text="🎛",
            width=36,
            height=30,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._music_open_fx_panel,
        )
        self._music_fx_btn.pack(side=ctk.LEFT, padx=(4, 0))

        vol_frame = ctk.CTkFrame(ctrl_row, fg_color="transparent")
        vol_frame.pack(side=ctk.RIGHT)

        self._music_mute_btn = ctk.CTkButton(
            vol_frame,
            text="🔊",
            width=30,
            height=30,
            font=ctk.CTkFont(size=14),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            command=self._music_toggle_mute,
        )
        self._music_mute_btn.pack(side=ctk.LEFT)

        self._music_vol_slider = ctk.CTkSlider(
            vol_frame,
            from_=0,
            to=100,
            width=100,
            command=self._music_set_volume,
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["accent_hover"],
        )
        self._music_vol_slider.set(int(self._music_volume * 100))
        self._music_vol_slider.pack(side=ctk.LEFT, padx=(5, 0))

        progress_frame = ctk.CTkFrame(panel, fg_color="transparent")
        progress_frame.pack(fill=ctk.X, padx=12, pady=(0, 8))

        self._music_cur_label = ctk.CTkLabel(
            progress_frame,
            text="0:00",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            width=40,
        )
        self._music_cur_label.pack(side=ctk.LEFT)

        self._music_progress_bar = ctk.CTkSlider(
            progress_frame,
            from_=0,
            to=100,
            command=self._music_seek,
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["accent_hover"],
        )
        self._music_progress_bar.set(0)
        self._music_progress_bar.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=5)

        self._music_end_label = ctk.CTkLabel(
            progress_frame,
            text="0:00",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            width=40,
        )
        self._music_end_label.pack(side=ctk.RIGHT)

        now_title_row = ctk.CTkFrame(panel, fg_color="transparent")
        now_title_row.pack(padx=12, anchor=ctk.W, pady=(0, 2))

        self._music_now_label_top = ctk.CTkLabel(
            now_title_row,
            text=_("music_no_track"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self._music_now_label_top.pack(side=ctk.LEFT)

        # 当前播放音质标签（FLAC/320K/192K/128K）
        self._music_quality_tag = ctk.CTkLabel(
            now_title_row,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
            text_color=COLORS["accent"],
        )
        self._music_quality_tag.pack(side=ctk.LEFT, padx=(8, 0))

        self._music_now_label_sub = ctk.CTkLabel(
            panel, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=COLORS["text_secondary"]
        )
        self._music_now_label_sub.pack(padx=12, anchor=ctk.W, pady=(0, 8))

        self._theme_refs.append((self._music_prev_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_play_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_next_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_stop_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_mode_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))
        self._theme_refs.append(
            (
                self._music_vol_slider,
                {
                    "fg_color": "bg_light",
                    "progress_color": "accent",
                    "button_color": "text_primary",
                    "button_hover_color": "accent_hover",
                },
            )
        )
        self._theme_refs.append(
            (
                self._music_progress_bar,
                {
                    "fg_color": "bg_light",
                    "progress_color": "accent",
                    "button_color": "text_primary",
                    "button_hover_color": "accent_hover",
                },
            )
        )
        self._theme_refs.append((self._music_now_label_top, {"text_color": "text_primary"}))
        self._theme_refs.append((self._music_quality_tag, {"text_color": "accent"}))
        self._theme_refs.append((self._music_now_label_sub, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_cur_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_end_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_folder_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_folder_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_mini_toggle_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))
        self._theme_refs.append((self._music_song_count_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_control_panel, {"fg_color": "card_bg"}))
        self._theme_refs.append((self._music_fx_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))

    def _build_music_playlist_panel(self):
        list_frame = ctk.CTkFrame(self._music_main_frame, fg_color=COLORS["card_bg"], corner_radius=12)
        list_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 10))
        self._music_list_frame = list_frame

        header = ctk.CTkFrame(list_frame, fg_color="transparent", height=35)
        header.pack(fill=ctk.X, padx=12, pady=(12, 5))
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text=_("music_playlist"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self._music_scroll = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        self._music_scroll.pack(fill=ctk.BOTH, expand=True, padx=8, pady=(5, 10))
        self._theme_refs.append((self._music_scroll, {"scrollbar_button_color": "bg_light"}))

    def _build_music_playlist_tab_panel(self):
        """构建歌单标签页 — 左侧侧边栏 + 右侧歌曲列表 + 排序控件"""
        self._music_playlist_frame = ctk.CTkFrame(self._music_tab_content, fg_color="transparent")

        main = ctk.CTkFrame(self._music_playlist_frame, fg_color=COLORS["card_bg"], corner_radius=12)
        main.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        # ── 左侧：歌单侧边栏 ──
        sidebar_frame = ctk.CTkFrame(main, fg_color="transparent", width=160)
        sidebar_frame.pack(side=ctk.LEFT, fill=ctk.Y, padx=(8, 4), pady=10)
        sidebar_frame.pack_propagate(False)

        ctk.CTkLabel(
            sidebar_frame,
            text=_("music_playlists"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, padx=8, pady=(0, 6))

        self._music_playlist_sidebar = ctk.CTkScrollableFrame(
            sidebar_frame, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"], height=200
        )
        self._music_playlist_sidebar.pack(fill=ctk.BOTH, expand=True, padx=2)

        self._music_new_playlist_btn = ctk.CTkButton(
            sidebar_frame,
            text=_("music_new_playlist"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["accent"],
            height=28,
            command=self._music_create_playlist_dialog,
        )
        self._music_new_playlist_btn.pack(fill=ctk.X, padx=6, pady=(6, 0))

        separator = ctk.CTkFrame(main, fg_color=COLORS["card_border"], width=1)
        separator.pack(side=ctk.LEFT, fill=ctk.Y, padx=2, pady=10)

        # ── 右侧：歌曲列表区域 ──
        right_frame = ctk.CTkFrame(main, fg_color="transparent")
        right_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(4, 8), pady=10)

        # 排序控件
        sort_frame = ctk.CTkFrame(right_frame, fg_color="transparent", height=28)
        sort_frame.pack(fill=ctk.X, pady=(0, 4))
        sort_frame.pack_propagate(False)
        self._music_sort_frame = sort_frame

        sort_label_font = ctk.CTkFont(family=FONT_FAMILY, size=11)

        self._music_sort_add_time_btn = ctk.CTkButton(
            sort_frame,
            text=_("music_sort_add_time") + " ▼",
            font=sort_label_font,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            height=24,
            width=100,
            command=lambda: self._music_do_sort(SORT_ADD_TIME_DESC),
        )
        self._music_sort_add_time_btn.pack(side=ctk.LEFT, padx=(0, 4))

        self._music_sort_name_btn = ctk.CTkButton(
            sort_frame,
            text=_("music_sort_name") + " ▲",
            font=sort_label_font,
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            height=24,
            width=80,
            command=lambda: self._music_do_sort(SORT_NAME_ASC),
        )
        self._music_sort_name_btn.pack(side=ctk.LEFT)

        # 播放全部按钮
        self._music_play_all_btn = ctk.CTkButton(
            sort_frame,
            text="▶ " + _("music_play_all"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            height=24,
            width=100,
            command=self._music_play_playlist_all,
        )
        self._music_play_all_btn.pack(side=ctk.RIGHT)

        # 歌单歌曲列表
        self._music_playlist_scroll = ctk.CTkScrollableFrame(
            right_frame, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        self._music_playlist_scroll.pack(fill=ctk.BOTH, expand=True)

        # ── 网易云远程歌单分页栏（超过 20 首时按页展示，默认隐藏） ──
        pager = ctk.CTkFrame(right_frame, fg_color="transparent", height=30)
        pager.pack(fill=ctk.X, pady=(4, 0))
        pager.pack_propagate(False)

        pager_btn_cfg = {
            "font": ctk.CTkFont(family=FONT_FAMILY, size=11),
            "fg_color": COLORS["bg_light"],
            "hover_color": COLORS["accent"],
            "height": 24,
            "width": 90,
        }

        self._music_wy_pager_prev = ctk.CTkButton(
            pager,
            text=_("music_page_prev"),
            command=lambda: self._music_wy_go_page(self._music_wy_remote_page - 1),
            **pager_btn_cfg,
        )
        self._music_wy_pager_prev.pack(side=ctk.LEFT)

        self._music_wy_pager_page_label = ctk.CTkLabel(
            pager,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._music_wy_pager_page_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        self._music_wy_pager_next = ctk.CTkButton(
            pager,
            text=_("music_page_next"),
            command=lambda: self._music_wy_go_page(self._music_wy_remote_page + 1),
            **pager_btn_cfg,
        )
        self._music_wy_pager_next.pack(side=ctk.RIGHT)

        self._music_wy_pager_frame = pager
        self._music_wy_pager_frame.pack_forget()
        self._theme_refs.append((self._music_wy_pager_prev, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_wy_pager_next, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_wy_pager_page_label, {"text_color": "text_secondary"}))

        self._theme_refs.append((self._music_new_playlist_btn, {"fg_color": "bg_light", "hover_color": "accent"}))

        # 初始化状态
        self._music_playlist_sidebar_widgets: List[dict] = []

    def _build_music_now_playing(self):
        self._music_cover_frame = ctk.CTkFrame(
            self._music_main_frame, fg_color=COLORS["card_bg"], corner_radius=12, width=200
        )
        self._music_cover_frame.pack(side=ctk.RIGHT, fill=ctk.Y, padx=(10, 0))
        self._music_cover_frame.pack_propagate(False)

        self._music_cover_label = ctk.CTkLabel(
            self._music_cover_frame, text="🎵", font=ctk.CTkFont(size=60), text_color=COLORS["text_secondary"]
        )
        self._music_cover_label.pack(pady=(30, 10))

        self._music_cover_artist = ctk.CTkLabel(
            self._music_cover_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._music_cover_artist.pack(pady=(0, 5))

        self._music_cover_album = ctk.CTkLabel(
            self._music_cover_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        self._music_cover_album.pack(pady=(0, 10))
        self._theme_refs.append((self._music_cover_frame, {"fg_color": "card_bg"}))
        self._theme_refs.append((self._music_cover_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_cover_artist, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_cover_album, {"text_color": "text_secondary"}))

        # 歌词显示区域（封面下方）
        self._lyric_current_label = ctk.CTkLabel(
            self._music_cover_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["accent"],
            wraplength=180,
            justify="center",
        )
        self._lyric_current_label.pack(pady=(0, 4))

        self._lyric_trans_label = ctk.CTkLabel(
            self._music_cover_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            wraplength=180,
            justify="center",
        )
        self._lyric_trans_label.pack()
        self._theme_refs.append((self._lyric_current_label, {"text_color": "accent"}))
        self._theme_refs.append((self._lyric_trans_label, {"text_color": "text_secondary"}))

    def _build_music_mini_bar(self):
        self._music_mini_bar = ctk.CTkFrame(
            self._music_tab_content, fg_color=COLORS["card_bg"], corner_radius=8, height=55
        )
        self._music_mini_bar.pack_propagate(False)

        inner = ctk.CTkFrame(self._music_mini_bar, fg_color="transparent")
        inner.pack(fill=ctk.BOTH, expand=True, padx=10, pady=5)

        self._music_mini_title = ctk.CTkLabel(
            inner,
            text=_("music_no_track"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self._music_mini_title.pack(side=ctk.LEFT, padx=(0, 10))

        btn_cfg = {
            "width": 30,
            "height": 28,
            "font": ctk.CTkFont(size=12),
            "fg_color": COLORS["bg_light"],
            "hover_color": COLORS["accent"],
        }

        self._music_mini_prev = ctk.CTkButton(inner, text="⏮", command=self._music_prev, **btn_cfg)
        self._music_mini_prev.pack(side=ctk.LEFT, padx=1)

        self._music_mini_play = ctk.CTkButton(inner, text="▶", command=self._music_toggle_play, **btn_cfg)
        self._music_mini_play.pack(side=ctk.LEFT, padx=1)

        self._music_mini_next = ctk.CTkButton(inner, text="⏭", command=self._music_next, **btn_cfg)
        self._music_mini_next.pack(side=ctk.LEFT, padx=1)

        self._music_mini_vol = ctk.CTkSlider(
            inner,
            from_=0,
            to=100,
            width=80,
            command=self._music_set_volume,
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["accent_hover"],
        )
        self._music_mini_vol.set(int(self._music_volume * 100))
        self._music_mini_vol.pack(side=ctk.RIGHT, padx=(5, 0))

        ctk.CTkButton(
            inner,
            text=_("music_expand"),
            width=60,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._music_toggle_mini_mode,
        ).pack(side=ctk.RIGHT, padx=(5, 0))

        self._theme_refs.append((self._music_mini_bar, {"fg_color": "card_bg"}))
        self._theme_refs.append((self._music_mini_title, {"text_color": "text_primary"}))
        self._theme_refs.append((self._music_mini_prev, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_mini_play, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_mini_next, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append(
            (
                self._music_mini_vol,
                {
                    "fg_color": "bg_light",
                    "progress_color": "accent",
                    "button_color": "text_primary",
                    "button_hover_color": "accent_hover",
                },
            )
        )

    # ═══════════════ 子标签页切换栏 ═══════════════

    def _build_music_source_tabs(self):
        tab_bar = ctk.CTkFrame(self._music_tab_content, fg_color="transparent", height=32)
        tab_bar.pack(fill=ctk.X, padx=15, pady=(10, 0))
        tab_bar.pack_propagate(False)
        self._music_source_tab_bar = tab_bar

        btn_cfg = {
            "height": 28,
            "font": ctk.CTkFont(family=FONT_FAMILY, size=12),
            "fg_color": COLORS["bg_light"],
            "hover_color": COLORS["accent"],
        }

        self._music_local_tab_btn = ctk.CTkButton(
            tab_bar, text=_("music_tab_local"), width=100, command=self._music_switch_to_local, **btn_cfg
        )
        self._music_local_tab_btn.pack(side=ctk.LEFT, padx=(0, 4))

        self._music_online_tab_btn = ctk.CTkButton(
            tab_bar, text=_("music_tab_online"), width=100, command=self._music_switch_to_online, **btn_cfg
        )
        self._music_online_tab_btn.pack(side=ctk.LEFT)

        self._music_playlist_tab_btn = ctk.CTkButton(
            tab_bar, text=_("music_tab_playlist"), width=100, command=self._music_switch_to_playlist_tab, **btn_cfg
        )
        self._music_playlist_tab_btn.pack(side=ctk.LEFT, padx=(4, 0))

        self._theme_refs.append((self._music_local_tab_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_online_tab_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_playlist_tab_btn, {"fg_color": "bg_light", "hover_color": "accent"}))

    def _music_switch_to_local(self):
        self._music_tab_mode = "local"
        self._music_online_frame.pack_forget()
        self._music_playlist_frame.pack_forget()
        if self._music_mini_mode:
            self._music_mini_bar.pack(fill=ctk.X, padx=15, pady=(0, 15))
        else:
            self._music_main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)
        self._music_local_tab_btn.configure(fg_color=COLORS["accent"])
        self._music_online_tab_btn.configure(fg_color=COLORS["bg_light"])
        self._music_playlist_tab_btn.configure(fg_color=COLORS["bg_light"])
        self._stop_search_loading()
        # 全部歌曲视图：清除远程歌单查看状态（侧边栏高亮与分页栏随之失效）
        self._music_wy_remote_view_id = None
        self._music_wy_remote_view_songs = []
        self._music_wy_update_pager()
        # 刷新全部歌曲列表
        self._rebuild_playlist_ui()

    def _music_switch_to_online(self):
        self._music_tab_mode = "online"
        self._music_mini_bar.pack_forget()
        self._music_main_frame.pack_forget()
        self._music_playlist_frame.pack_forget()
        self._music_online_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)
        self._music_online_tab_btn.configure(fg_color=COLORS["accent"])
        self._music_local_tab_btn.configure(fg_color=COLORS["bg_light"])
        self._music_playlist_tab_btn.configure(fg_color=COLORS["bg_light"])

    def _music_switch_to_playlist_tab(self):
        """切换到歌单标签页"""
        self._music_tab_mode = "playlist"
        self._music_mini_bar.pack_forget()
        self._music_main_frame.pack_forget()
        self._music_online_frame.pack_forget()
        self._music_playlist_frame.pack(fill=ctk.BOTH, expand=True)
        self._music_playlist_tab_btn.configure(fg_color=COLORS["accent"])
        self._music_local_tab_btn.configure(fg_color=COLORS["bg_light"])
        self._music_online_tab_btn.configure(fg_color=COLORS["bg_light"])
        self._stop_search_loading()
        self._rebuild_playlist_sidebar()

    # ═══════════════ 在线搜索面板 ═══════════════

    def _build_music_online_panel(self):
        # 搜索栏
        search_bar = ctk.CTkFrame(self._music_online_frame, fg_color=COLORS["card_bg"], corner_radius=12)
        search_bar.pack(fill=ctk.X, pady=(0, 10))

        search_inner = ctk.CTkFrame(search_bar, fg_color="transparent")
        search_inner.pack(fill=ctk.X, padx=12, pady=10)

        self._music_search_entry = ctk.CTkEntry(
            search_inner,
            placeholder_text=_("music_search_placeholder"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
        )
        self._music_search_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._music_search_entry.bind("<Return>", lambda e: self._music_do_search())

        self._music_search_btn = ctk.CTkButton(
            search_inner,
            text=_("music_search_btn"),
            width=80,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._music_do_search,
        )
        self._music_search_btn.pack(side=ctk.LEFT)

        # 音源选择行
        source_row = ctk.CTkFrame(search_bar, fg_color="transparent")
        source_row.pack(fill=ctk.X, padx=12, pady=(0, 8))

        ctk.CTkLabel(
            source_row,
            text=_("music_source_select") + ": ",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT)

        self._music_source_buttons = {}
        for meta in SOURCE_META:
            btn = ctk.CTkButton(
                source_row,
                text=meta["name"],
                width=70,
                height=24,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                fg_color=COLORS["bg_light"],
                hover_color=COLORS["accent"],
                command=lambda s=meta["id"]: self._music_select_source(s),
            )
            btn.pack(side=ctk.LEFT, padx=(4, 0))
            self._music_source_buttons[meta["id"]] = btn
            self._theme_refs.append((btn, {"fg_color": "bg_light", "hover_color": "accent"}))

        self._music_select_source(self._music_selected_source)

        # 音质选择
        quality_row = ctk.CTkFrame(search_bar, fg_color="transparent")
        quality_row.pack(fill=ctk.X, padx=12, pady=(0, 8))

        ctk.CTkLabel(
            quality_row,
            text=_("music_quality_label") + ": ",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT)

        self._music_quality_var = ctk.StringVar(value="auto")
        for q_text, q_val in [
            (_("music_quality_auto"), "auto"),
            ("128K", "128k"),
            ("320K", "320k"),
            ("FLAC", "flac"),
        ]:
            ctk.CTkRadioButton(
                quality_row,
                text=q_text,
                variable=self._music_quality_var,
                value=q_val,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
                text_color=COLORS["text_primary"],
            ).pack(side=ctk.LEFT, padx=(8, 0))

        # 桌面歌词按钮
        self._music_dlrc_btn = ctk.CTkButton(
            quality_row,
            text=_("music_desktop_lyric"),
            width=80,
            height=24,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._music_toggle_desktop_lyric,
        )
        self._music_dlrc_btn.pack(side=ctk.RIGHT)
        self._theme_refs.append((self._music_dlrc_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))

        # 原唱错标/漏标反馈按钮
        self._music_original_feedback_btn = ctk.CTkButton(
            quality_row,
            text=_("music_original_feedback"),
            height=24,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color="transparent",
            hover_color=COLORS["card_border"],
            text_color=COLORS["accent"],
            command=lambda: webbrowser.open(MUSIC_ORIGINAL_FEEDBACK_URL),
        )
        self._music_original_feedback_btn.pack(side=ctk.RIGHT, padx=(8, 8))
        self._theme_refs.append(
            (self._music_original_feedback_btn, {"fg_color": "transparent", "hover_color": "card_border"})
        )

        self._theme_refs.append((search_bar, {"fg_color": "card_bg"}))

        # 搜索结果列表
        result_frame = ctk.CTkFrame(self._music_online_frame, fg_color=COLORS["card_bg"], corner_radius=12)
        result_frame.pack(fill=ctk.BOTH, expand=True)
        self._music_online_result_frame = result_frame

        result_header = ctk.CTkFrame(result_frame, fg_color="transparent", height=30)
        result_header.pack(fill=ctk.X, padx=12, pady=(10, 5))
        result_header.pack_propagate(False)

        ctk.CTkLabel(
            result_header,
            text=_("music_playlist"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self._music_search_status = ctk.CTkLabel(
            result_header, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=COLORS["text_secondary"]
        )
        self._music_search_status.pack(side=ctk.RIGHT)

        self._music_online_scroll = ctk.CTkScrollableFrame(
            result_frame, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        self._music_online_scroll.pack(fill=ctk.BOTH, expand=True, padx=8, pady=(5, 10))
        self._theme_refs.append((self._music_online_scroll, {"scrollbar_button_color": "bg_light"}))
        self._theme_refs.append((result_frame, {"fg_color": "card_bg"}))

        # 分页栏（上一页 / 页码按钮 / 下一页）
        pager_frame = ctk.CTkFrame(result_frame, fg_color="transparent", height=46)
        pager_frame.pack(fill=ctk.X, padx=12, pady=(0, 8))
        pager_frame.pack_propagate(False)
        self._music_pager_frame = pager_frame

        pager_btn_cfg = {
            "height": 24,
            "font": ctk.CTkFont(family=FONT_FAMILY, size=10),
            "fg_color": COLORS["bg_light"],
            "hover_color": COLORS["accent"],
        }

        page_prev_key = "music_page_prev"
        page_prev_text = _(page_prev_key)
        if page_prev_text == page_prev_key:
            page_prev_text = "◀ 上一页"
        self._music_pager_prev = ctk.CTkButton(
            pager_frame, text=page_prev_text, width=78, command=self._music_search_prev_page, **pager_btn_cfg
        )
        self._music_pager_prev.pack(side=ctk.LEFT, padx=(0, 4))

        # 页码按钮横向滚动容器（音源总数多时页数可达上百，超出窗口宽度可横向滚动）
        self._music_pager_page_box = ctk.CTkScrollableFrame(
            pager_frame,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
            orientation="horizontal",
            height=40,
        )
        self._music_pager_page_box.pack(side=ctk.LEFT, fill=ctk.X, expand=True)
        self._theme_refs.append((self._music_pager_page_box, {"scrollbar_button_color": "bg_light"}))

        page_next_key = "music_page_next"
        page_next_text = _(page_next_key)
        if page_next_text == page_next_key:
            page_next_text = "下一页 ▶"
        self._music_pager_next = ctk.CTkButton(
            pager_frame, text=page_next_text, width=78, command=self._music_search_next_page, **pager_btn_cfg
        )
        self._music_pager_next.pack(side=ctk.RIGHT, padx=(4, 0))

        self._music_pager_label = ctk.CTkLabel(
            pager_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        self._music_pager_label.pack(side=ctk.RIGHT, padx=(0, 8))

        self._theme_refs.append((self._music_pager_prev, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_pager_next, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_pager_label, {"text_color": "text_secondary"}))
        self._music_rebuild_pager()

    def _music_select_source(self, source_id: str):
        self._music_selected_source = source_id
        for sid, btn in self._music_source_buttons.items():
            btn.configure(fg_color=COLORS["accent"] if sid == source_id else COLORS["bg_light"])

    def _update_mode_btn_text(self):
        mode_texts = {
            PLAY_MODE_SEQUENTIAL: "➡",
            PLAY_MODE_LOOP_LIST: "🔁",
            PLAY_MODE_LOOP_SINGLE: "🔂",
            PLAY_MODE_RANDOM: "🔀",
        }
        if hasattr(self, "_music_mode_btn") and self._music_mode_btn.winfo_exists():
            self._music_mode_btn.configure(text=mode_texts.get(self._music_play_mode, "🔁"))

    def _update_now_playing_info(self):
        # 音质标签：在线歌曲按实际获取到的音质档位，本地歌曲按文件真实码率
        if self._music_is_online_playing and self._music_current_online_info:
            quality_text = _format_online_quality(getattr(self, "_music_current_quality", "") or "")
        else:
            cur_path = self._get_current_file()
            quality_text = _format_local_quality(self._get_metadata(cur_path), cur_path) if cur_path else ""
        self._music_quality_tag.configure(text=quality_text)

        # 在线播放优先
        if self._music_is_online_playing and self._music_current_online_info:
            oi = self._music_current_online_info
            title = oi.name
            artist = oi.singer or ""
            album = oi.album_name or ""
            duration = oi.interval

            self._music_now_label_top.configure(text=title)
            sub_text = artist
            if album:
                sub_text = f"{artist} - {album}" if artist else album
            self._music_now_label_sub.configure(text=sub_text)
            self._music_mini_title.configure(text=f"{title} · {quality_text}" if quality_text else title)
            self._music_end_label.configure(text=_format_time(duration))
            self._music_cover_label.configure(text="🎵")
            self._music_cover_artist.configure(text=artist)
            self._music_cover_album.configure(text=album)

            if oi.img:
                self._fetch_and_display_online_cover(oi.img)
            self._music_smtc.update_now_playing(title, artist, album, None)
            return

        path = self._get_current_file()
        if not path:
            self._music_now_label_top.configure(text=_("music_no_track"))
            self._music_now_label_sub.configure(text="")
            self._music_cover_label.configure(text="🎵")
            self._music_cover_artist.configure(text="")
            self._music_cover_album.configure(text="")
            self._music_mini_title.configure(text=_("music_no_track"))
            self._music_progress_bar.set(0)
            self._music_cur_label.configure(text="0:00")
            self._music_end_label.configure(text="0:00")
            return

        meta = self._get_metadata(path)
        title = meta.get("title", os.path.basename(path))
        artist = meta.get("artist", "")
        album = meta.get("album", "")
        duration = meta.get("duration", 0)

        self._music_now_label_top.configure(text=title)
        sub_text = artist
        if album:
            sub_text = f"{artist} - {album}" if artist else album
        self._music_now_label_sub.configure(text=sub_text)
        self._music_mini_title.configure(text=f"{title} · {quality_text}" if quality_text else title)

        self._music_end_label.configure(text=_format_time(duration))

        if meta.get("has_cover") and meta.get("cover_data"):
            self._display_cover(meta["cover_data"])
        else:
            self._music_cover_label.configure(text="🎵")

        self._music_cover_artist.configure(text=artist if artist else "")
        self._music_cover_album.configure(text=album if album else "")

        cover_bytes = meta.get("cover_data") if meta.get("has_cover") else None
        self._music_smtc.update_now_playing(title, artist, album, cover_bytes)

    def _fetch_and_display_online_cover(self, url: str):
        """异步获取在线封面图并显示"""
        app = self

        def _fetch():
            try:
                resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code == 200:
                    app.after(0, lambda d=resp.content: app._display_cover(d))
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _display_cover(self, cover_data: bytes):
        try:
            import io

            from PIL import Image

            image = Image.open(io.BytesIO(cover_data))
            cover_size = (150, 150)
            ctk_image = ctk.CTkImage(light_image=image, dark_image=image, size=cover_size)
            self._music_cover_label.configure(image=ctk_image, text="")
            self._music_cover_label._image = ctk_image
        except Exception:
            self._music_cover_label.configure(text="🎵")

    def _get_current_file(self) -> Optional[str]:
        if 0 <= self._music_current_index < len(self._music_playlist):
            return self._music_playlist[self._music_current_index]
        return None

    def _get_metadata(self, filepath: str) -> dict:
        if filepath in self._music_metadata_cache:
            self._music_metadata_cache.move_to_end(filepath)
            return self._music_metadata_cache[filepath]

        meta = _extract_audio_metadata(filepath)
        self._music_metadata_cache[filepath] = meta
        while len(self._music_metadata_cache) > MUSIC_METADATA_CACHE_MAX:
            self._music_metadata_cache.popitem(last=False)
        return meta

    def _play_file(self, filepath: str, start_pos: float = 0):
        if _pygame_import_error is not None:
            logger.warning("pygame 不可用，无法播放")
            return
        self._music_cancel_fade()
        self._stop_lyric_poll()
        # 应用音效处理
        processed_path = filepath
        if self._music_effects.settings.has_any_enabled:
            try:
                fx_path = self._music_effects.process(filepath)
                if fx_path and fx_path != filepath:
                    self._music_effects_processed_files.append(fx_path)
                    processed_path = fx_path
            except Exception:
                pass
        try:
            mixer.music.load(processed_path)
            mixer.music.set_volume(0)
            mixer.music.play(start=start_pos if start_pos > 0 else 0)
            self._music_is_playing = True
            self._music_is_paused = False
            self._music_seek_offset = start_pos if start_pos > 0 else 0
            self._music_current_filepath = filepath
            self._music_duration = self._get_metadata(filepath).get("duration", 0)
            self._update_play_btn_ui()
            self._update_now_playing_info()
            self._start_progress_poll()
            self._start_lyric_poll()
            self._music_smtc.set_playing()
            self._highlight_current_in_list()
            self._music_fade_in()
            self._trigger_ach("music_first_play")
            self._trigger_ach("music_play_count")
            self._music_record_play_history_local(filepath)
        except Exception as e:
            logger.error(f"播放失败: {filepath}: {e}")
            self._music_is_playing = False
            self._update_play_btn_ui()

    def _play_online_file(
        self,
        filepath: str,
        online_info: OnlineMusicInfo,
        start_pos: float = 0,
        history_origin: Optional[OnlineMusicInfo] = None,
        quality: str = "",
    ):
        """播放在线缓存的临时文件

        Args:
            filepath: 临时音频文件路径
            online_info: 实际播放的歌曲信息（跨源兜底后可能被替换）
            start_pos: 起始播放位置（秒）
            history_origin: 用户点播的原始歌曲（兜底时历史记录用原歌曲）
            quality: 实际获取到的音质档位（128k/320k/flac，用于显示）
        """
        if _pygame_import_error is not None:
            return
        self._music_cancel_fade()
        self._stop_lyric_poll()
        # 应用音效处理
        processed_path = filepath
        if self._music_effects.settings.has_any_enabled:
            try:
                fx_path = self._music_effects.process(filepath)
                if fx_path and fx_path != filepath:
                    self._music_effects_processed_files.append(fx_path)
                    processed_path = fx_path
            except Exception:
                pass
        try:
            mixer.music.load(processed_path)
            mixer.music.set_volume(0)
            mixer.music.play(start=start_pos if start_pos > 0 else 0)
            self._music_is_playing = True
            self._music_is_paused = False
            self._music_is_online_playing = True
            self._music_current_online_info = online_info
            self._music_current_quality = quality or ""
            self._music_seek_offset = start_pos if start_pos > 0 else 0
            self._music_current_filepath = filepath
            self._music_duration = online_info.interval
            self._update_play_btn_ui()
            self._update_now_playing_info()
            self._start_progress_poll()
            self._fetch_and_start_lyric(online_info)
            self._music_smtc.set_playing()
            self._music_fade_in()
            self._trigger_ach("music_first_play")
            self._trigger_ach("music_play_count")
            # 播放历史记录用户点播的原始歌曲（兜底播放时记录的也是原歌曲）
            self._music_record_play_history_online(history_origin or online_info)
        except Exception as e:
            logger.error(f"在线播放失败: {e}")
            self._music_is_playing = False
            self._music_is_online_playing = False
            self._update_play_btn_ui()

    def _music_cancel_fade(self):
        if self._music_fade_timer_id is not None:
            self.after_cancel(self._music_fade_timer_id)
            self._music_fade_timer_id = None
        self._music_is_fading = False
        self._music_fade_out_target = None

    def _music_fade_in(self, step: int = 0):
        if not self._music_is_playing or self._music_is_paused:
            self._music_cancel_fade()
            return
        if step >= FADE_STEPS:
            try:
                mixer.music.set_volume(self._music_volume)
            except Exception:
                pass
            self._music_is_fading = False
            self._music_fade_timer_id = None
            return
        vol = self._music_volume * (step + 1) / FADE_STEPS
        try:
            mixer.music.set_volume(vol)
        except Exception:
            pass
        self._music_is_fading = True
        self._music_fade_timer_id = self.after(FADE_INTERVAL_MS, lambda: self._music_fade_in(step + 1))

    def _music_fade_out(self, step: int = 0):
        if not self._music_is_playing or self._music_is_paused:
            self._music_cancel_fade()
            return
        if step >= FADE_STEPS:
            try:
                mixer.music.set_volume(0)
            except Exception:
                pass
            self._music_is_fading = False
            self._music_fade_timer_id = None
            self._music_execute_fade_out_target()
            return
        remaining = FADE_STEPS - 1 - step
        vol = self._music_volume * remaining / (FADE_STEPS - 1) if FADE_STEPS > 1 else 0
        try:
            mixer.music.set_volume(max(0, vol))
        except Exception:
            pass
        self._music_is_fading = True
        self._music_fade_timer_id = self.after(FADE_INTERVAL_MS, lambda: self._music_fade_out(step + 1))

    def _music_execute_fade_out_target(self):
        target = self._music_fade_out_target
        self._music_fade_out_target = None
        if target == "pause":
            try:
                mixer.music.pause()
            except Exception:
                pass
            self._music_is_playing = False
            self._music_is_paused = True
            self._update_play_btn_ui()
            self._music_smtc.set_paused()
            try:
                mixer.music.set_volume(self._music_volume)
            except Exception:
                pass
        elif target == "stop":
            try:
                mixer.music.stop()
                mixer.music.unload()
            except Exception:
                pass
            self._music_is_playing = False
            self._music_is_paused = False
            self._music_progress = 0
            self._music_seek_offset = 0
            self._update_play_btn_ui()
            self._music_progress_bar.set(0)
            self._music_cur_label.configure(text="0:00")
            self._music_smtc.set_stopped()
            try:
                mixer.music.set_volume(self._music_volume)
            except Exception:
                pass

    def _music_toggle_play(self):
        if not self._music_playlist and not self._music_is_online_playing:
            return
        if not self._music_is_playing and not self._music_is_paused:
            if self._music_is_online_playing and self._music_current_online_info:
                # 重播当前在线歌曲
                self._music_play_online_url(self._music_current_online_info)
                return
            if self._music_current_index < 0:
                if self._music_is_online_playing:
                    return
                self._music_current_index = 0
            self._play_file(
                self._music_playlist[self._music_current_index], self._music_progress if self._music_progress > 0 else 0
            )
        elif self._music_is_paused:
            if self._music_is_fading:
                return
            try:
                mixer.music.unpause()
                mixer.music.set_volume(0)
                self._music_is_playing = True
                self._music_is_paused = False
                self._update_play_btn_ui()
                self._start_progress_poll()
                self._start_lyric_poll()
                self._music_smtc.set_playing()
                self._music_fade_in()
            except Exception as e:
                logger.error(f"恢复播放失败: {e}")
        elif self._music_is_playing:
            if self._music_is_fading:
                return
            self._music_fade_out_target = "pause"
            self._stop_progress_poll()
            self._stop_lyric_poll()
            self._music_fade_out()

    def _music_stop(self, instant: bool = False):
        if _pygame_import_error is not None:
            return
        self._music_cancel_fade()
        if not instant and self._music_is_playing and not self._music_is_paused:
            self._music_fade_out_target = "stop"
            self._stop_progress_poll()
            self._stop_lyric_poll()
            self._music_fade_out()
            return
        try:
            mixer.music.stop()
            mixer.music.unload()
        except Exception:
            pass
        self._music_is_playing = False
        self._music_is_paused = False
        self._music_is_online_playing = False
        self._music_current_online_info = None
        self._music_progress = 0
        self._music_seek_offset = 0
        self._stop_progress_poll()
        self._stop_lyric_poll()
        self._update_play_btn_ui()
        self._music_progress_bar.set(0)
        self._music_cur_label.configure(text="0:00")
        self._music_smtc.set_stopped()

    def _play_playlist_context_song(self, idx: int):
        """播歌单上下文中指定索引的歌曲（支持本地/在线混合）"""
        songs = self._music_playlist_context_songs
        if idx < 0 or idx >= len(songs):
            return
        song = songs[idx]
        self._music_playlist_context_idx = idx
        self._highlight_playlist_song(idx)
        if song.source_type == "local":
            if not os.path.exists(song.file_path):
                # 跳过不存在的本地文件，播下一首
                self._play_playlist_context_song((idx + 1) % len(songs))
                return
            # 构建本地文件列表供 _play_file 使用
            local_paths = [s.file_path for s in songs if s.source_type == "local" and os.path.exists(s.file_path)]
            self._music_playlist = local_paths
            try:
                self._music_current_index = local_paths.index(song.file_path)
            except ValueError:
                self._music_current_index = 0
            self._music_progress = 0
            self._play_file(song.file_path)
        else:
            from ui.music_source.base import MusicInfo

            info = MusicInfo(
                name=song.online_name,
                singer=song.online_singer,
                source=song.online_source,
                songmid=song.online_songmid,
                album_name=song.online_album,
                interval=song.online_interval,
                img=song.online_img,
                # 恢复加入歌单时的可用音质信息（自动音质解析与 URL 获取依赖）
                types=[dict(t) for t in (song.online_types or [])],
                _types=dict(song.online_type_detail or {}),
            )
            self._music_play_online_url(info)

    def _highlight_playlist_song(self, target_idx: int):
        """高亮歌单歌曲列表中指定索引的行"""
        # 网易云远程歌单（分页）：目标歌曲不在当前页时自动翻页
        if getattr(self, "_music_wy_remote_view_id", None):
            if target_idx < 0:
                return
            page = target_idx // _MUSIC_WY_PAGE_SIZE + 1
            if page != self._music_wy_remote_page and self._music_wy_remote_view_songs:
                self._music_wy_remote_page = page
                self._music_render_wy_remote_songs(
                    self._music_wy_remote_view_id, self._music_wy_remote_view_songs
                )
            for w in self._music_playlist_widgets:
                try:
                    f = w.get("frame")
                    if not f or not f.winfo_exists():
                        continue
                    if w.get("real_index") == target_idx:
                        f.configure(fg_color=COLORS["accent"])
                    else:
                        f.configure(fg_color="transparent")
                except Exception:
                    pass
            return
        for w in self._music_playlist_widgets:
            try:
                f = w.get("frame")
                if not f or not f.winfo_exists():
                    continue
                if w.get("index") == target_idx:
                    f.configure(fg_color=COLORS["accent"])
                else:
                    f.configure(fg_color="transparent")
            except Exception:
                pass

    def _music_prev(self):
        if self._music_playlist_context_songs:
            n = len(self._music_playlist_context_songs)
            if n == 0:
                return
            if self._music_play_mode == PLAY_MODE_RANDOM:
                import random

                random.seed()
                new_idx = random.randrange(n)
                if n > 1 and new_idx == self._music_playlist_context_idx:
                    new_idx = (new_idx + 1) % n
            else:
                new_idx = (self._music_playlist_context_idx - 1) % n
            self._play_playlist_context_song(new_idx)
            return
        if not self._music_playlist:
            return
        if self._music_play_mode == PLAY_MODE_RANDOM:
            import random

            random.seed()
            new_idx = random.randrange(len(self._music_playlist))
            if len(self._music_playlist) > 1 and new_idx == self._music_current_index:
                new_idx = (new_idx + 1) % len(self._music_playlist)
            self._music_current_index = new_idx
        else:
            self._music_current_index = (self._music_current_index - 1) % len(self._music_playlist)
        self._music_progress = 0
        self._play_file(self._music_playlist[self._music_current_index])

    def _music_next(self):
        if self._music_playlist_context_songs:
            n = len(self._music_playlist_context_songs)
            if n == 0:
                return
            if self._music_play_mode == PLAY_MODE_RANDOM:
                import random

                random.seed()
                new_idx = random.randrange(n)
                if n > 1 and new_idx == self._music_playlist_context_idx:
                    new_idx = (new_idx + 1) % n
            else:
                new_idx = (self._music_playlist_context_idx + 1) % n
            self._play_playlist_context_song(new_idx)
            return
        if not self._music_playlist:
            return
        if self._music_play_mode == PLAY_MODE_RANDOM:
            import random

            random.seed()
            new_idx = random.randrange(len(self._music_playlist))
            if len(self._music_playlist) > 1 and new_idx == self._music_current_index:
                new_idx = (new_idx + 1) % len(self._music_playlist)
            self._music_current_index = new_idx
        else:
            self._music_current_index = (self._music_current_index + 1) % len(self._music_playlist)
        self._music_progress = 0
        self._play_file(self._music_playlist[self._music_current_index])

    def _music_seek(self, value: float):
        if not self._music_is_playing and not self._music_is_paused:
            return
        if not self._music_current_filepath:
            return
        self._music_cancel_fade()
        try:
            pos = (value / 100.0) * self._music_duration if self._music_duration > 0 else 0
            was_paused = self._music_is_paused
            # 重载文件到指定位置（set_pos 不重置 get_pos 计时器，必须 reload）
            mixer.music.stop()
            mixer.music.load(self._music_current_filepath)
            mixer.music.set_volume(0)
            mixer.music.play(start=pos)
            if was_paused:
                mixer.music.pause()
            self._music_progress = pos
            self._music_seek_offset = pos
            if was_paused:
                self._stop_progress_poll()
            else:
                self._start_progress_poll()
                self._music_fade_in()
        except Exception:
            pass

    def _music_set_volume(self, value: float):
        self._music_volume = value / 100.0
        if _pygame_import_error is None and not self._music_is_fading:
            try:
                mixer.music.set_volume(self._music_volume)
            except Exception:
                pass
        self._update_mute_btn_ui()

    def _music_toggle_mute(self):
        if self._music_volume > 0:
            self._music_vol_before_mute = self._music_volume
            self._music_volume = 0
            self._music_vol_slider.set(0)
            self._music_mini_vol.set(0)
        else:
            self._music_volume = getattr(self, "_music_vol_before_mute", 0.7)
            self._music_vol_slider.set(int(self._music_volume * 100))
            self._music_mini_vol.set(int(self._music_volume * 100))
        if _pygame_import_error is None and not self._music_is_fading:
            try:
                mixer.music.set_volume(self._music_volume)
            except Exception:
                pass
        self._update_mute_btn_ui()
        self._trigger_ach("music_volume_tweaker")

    def _update_mute_btn_ui(self):
        if self._music_volume == 0:
            self._music_mute_btn.configure(text="🔇")
        else:
            self._music_mute_btn.configure(text="🔊")

    def _update_play_btn_ui(self):
        if self._music_is_playing and not self._music_is_paused:
            self._music_play_btn.configure(text="⏸")
            self._music_mini_play.configure(text="⏸")
        else:
            self._music_play_btn.configure(text="▶")
            self._music_mini_play.configure(text="▶")
        self._update_music_footer()

    def _start_progress_poll(self):
        self._stop_progress_poll()
        self._poll_music_progress()

    def _stop_progress_poll(self):
        if self._music_progress_timer_id is not None:
            self.after_cancel(self._music_progress_timer_id)
            self._music_progress_timer_id = None

    def _poll_music_progress(self):
        if not self._music_is_playing or self._music_is_paused:
            self._stop_progress_poll()
            return
        if not self._is_music_tab_active():
            self._music_progress_timer_id = self.after(1000, self._poll_music_progress)
            return
        try:
            if mixer.music.get_busy():
                elapsed = mixer.music.get_pos() / 1000.0
                pos = elapsed + self._music_seek_offset
                self._music_progress = pos
                cur_text = _format_time(pos)
                self._music_cur_label.configure(text=cur_text)
                if self._music_duration > 0:
                    pct = (pos / self._music_duration) * 100
                    if 0 <= pct <= 100:
                        self._music_progress_bar.set(pct)
            if not mixer.music.get_busy() and self._music_is_playing:
                self._on_track_end()
        except Exception:
            pass
        self._music_progress_timer_id = self.after(500, self._poll_music_progress)

    # ═══════════════ 歌词轮询 ═══════════════

    def _start_lyric_poll(self):
        self._stop_lyric_poll()
        self._poll_lyric_progress()

    def _stop_lyric_poll(self):
        if self._music_lyric_poll_id is not None:
            self.after_cancel(self._music_lyric_poll_id)
            self._music_lyric_poll_id = None

    def _poll_lyric_progress(self):
        if not self._music_is_playing or self._music_is_paused:
            self._stop_lyric_poll()
            return
        if not self._is_music_tab_active() and not (self._music_desktop_lyric and self._music_desktop_lyric.is_visible):
            self._music_lyric_poll_id = self.after(300, self._poll_lyric_progress)
            return
        try:
            elapsed_ms = int(self._music_progress * 1000)
            self._update_lyric_display(elapsed_ms)
            if self._music_desktop_lyric and self._music_desktop_lyric.is_visible:
                self._music_desktop_lyric.update_progress(elapsed_ms)
        except Exception:
            pass
        self._music_lyric_poll_id = self.after(100, self._poll_lyric_progress)

    def _update_lyric_display(self, elapsed_ms: int):
        """更新内嵌歌词显示"""
        if not hasattr(self, "_lyric_current_label") or not self._lyric_current_label:
            return
        current = self._music_lyric_parser.get_line_at(elapsed_ms)
        if current is None:
            self._lyric_current_label.configure(text="")
            if hasattr(self, "_lyric_trans_label"):
                self._lyric_trans_label.configure(text="")
            return
        self._lyric_current_label.configure(text=current.text)
        trans = ""
        if self._music_show_lyric_translation and current.translation:
            trans = current.translation
        elif self._music_show_lyric_roma and current.roma:
            trans = current.roma
        if hasattr(self, "_lyric_trans_label"):
            self._lyric_trans_label.configure(text=trans)

    def _fetch_and_start_lyric(self, online_info: OnlineMusicInfo):
        """获取歌词并开始解析"""
        app = self

        def _fetch():
            try:
                src = MUSIC_SOURCES.get(online_info.source)
                if not src:
                    return
                lrc_text = src.get_lyric(online_info)
                if not lrc_text:
                    return
                app._music_lyric_parser.clear()
                app._music_lyric_parser.parse(lrc_text)
                app.after(0, app._start_lyric_poll)
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _is_music_tab_active(self):
        try:
            current = self.tabview.get()
            target = _("tab_music")
            return current == target
        except Exception:
            return False

    def _on_track_end(self):
        self._music_is_playing = False
        self._stop_progress_poll()
        self._stop_lyric_poll()
        if self._music_is_online_playing:
            self._music_is_online_playing = False
            self._music_current_online_info = None
            # 如果正在播歌单中的在线歌曲，自动切到下一首
            if self._music_playlist_context_songs:
                self._music_next()
                return
            self._update_play_btn_ui()
            self._music_seek_offset = 0
            self._music_progress_bar.set(0)
            self._music_cur_label.configure(text="0:00")
            self._music_smtc.set_stopped()
            return
        if self._music_play_mode == PLAY_MODE_LOOP_SINGLE:
            self._play_file(self._music_playlist[self._music_current_index])
        elif self._music_play_mode == PLAY_MODE_SEQUENTIAL:
            if self._music_current_index + 1 < len(self._music_playlist):
                self._music_current_index += 1
                self._music_progress = 0
                self._play_file(self._music_playlist[self._music_current_index])
            else:
                self._update_play_btn_ui()
                self._music_seek_offset = 0
                self._music_progress_bar.set(0)
                self._music_cur_label.configure(text="0:00")
                self._music_smtc.set_stopped()
        elif self._music_play_mode == PLAY_MODE_LOOP_LIST:
            self._music_current_index = (self._music_current_index + 1) % len(self._music_playlist)
            self._music_progress = 0
            self._play_file(self._music_playlist[self._music_current_index])
        elif self._music_play_mode == PLAY_MODE_RANDOM:
            import random

            random.seed()
            new_idx = random.randrange(len(self._music_playlist))
            if len(self._music_playlist) > 1 and new_idx == self._music_current_index:
                new_idx = (new_idx + 1) % len(self._music_playlist)
            self._music_current_index = new_idx
            self._music_progress = 0
            self._play_file(self._music_playlist[self._music_current_index])

    def _music_cycle_mode(self):
        modes = [PLAY_MODE_SEQUENTIAL, PLAY_MODE_LOOP_LIST, PLAY_MODE_LOOP_SINGLE, PLAY_MODE_RANDOM]
        idx = modes.index(self._music_play_mode)
        self._music_play_mode = modes[(idx + 1) % len(modes)]
        self._update_mode_btn_text()
        self._music_modes_used.add(self._music_play_mode)
        if len(self._music_modes_used) >= 4:
            self._check_ach("music_mode_master", True)

    def _music_toggle_mini_mode(self):
        self._music_mini_mode = not self._music_mini_mode
        if self._music_mini_mode:
            self._music_main_frame.pack_forget()
            self._music_mini_bar.pack(fill=ctk.X, padx=15, pady=(0, 15))
            self._music_mini_toggle_btn.configure(text=_("music_expand"))
            self._trigger_ach("music_mini_mode")
        else:
            self._music_mini_bar.pack_forget()
            self._music_main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)
            self._music_mini_toggle_btn.configure(text=_("music_mini_mode"))
        self._save_music_state_later()

    def _music_open_folder(self):
        folder = filedialog.askdirectory(title=_("music_select_folder"))
        if not folder:
            return
        self._music_scan_folder(folder)

    def _music_scan_folder(self, folder: str):
        files = []
        try:
            for root, dirs, filenames in os.walk(folder):
                for fname in filenames:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in AUDIO_EXTENSIONS:
                        files.append(os.path.join(root, fname))
        except Exception as e:
            logger.error(f"扫描文件夹失败: {folder}: {e}")
            return
        if not files:
            return
        files.sort(key=lambda f: os.path.basename(f).lower())
        self._music_stop()
        self._music_playlist = files
        self._music_current_index = -1
        self._music_playlist_context_songs = []  # 退出歌单上下文
        self._music_playlist_context_idx = -1
        self._music_last_folder = folder
        self._music_metadata_cache.clear()
        self._music_folder_label.configure(text=os.path.basename(folder) or folder)
        count_text = _("music_song_count", count=len(files))
        if count_text == "music_song_count":
            count_text = f"{len(files)} 首"
        self._music_song_count_label.configure(count_text)
        self._rebuild_playlist_ui()
        self._save_music_state_later()

    def _rebuild_playlist_ui(self):
        for w in self._music_playlist_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_playlist_widgets.clear()
        for idx, filepath in enumerate(self._music_playlist):
            self._add_playlist_row(idx, filepath)
        self._highlight_current_in_list()

    def _add_playlist_row(self, idx: int, filepath: str):
        meta = self._get_metadata(filepath)
        title = meta.get("title", os.path.basename(filepath))
        duration = meta.get("duration", 0)
        dur_text = _format_time(duration) if duration else ""

        row = ctk.CTkFrame(self._music_scroll, fg_color="transparent", height=32)
        row.pack(fill=ctk.X, pady=1)

        index_label = ctk.CTkLabel(
            row,
            text=str(idx + 1),
            width=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        index_label.pack(side=ctk.LEFT)

        arts = ""
        if meta.get("artist"):
            arts = f" - {meta['artist']}"
        t = title if len(title) <= 50 else title[:47] + "..."
        name_label = ctk.CTkLabel(
            row,
            text=f"{t}{arts}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        name_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(5, 5))

        if dur_text:
            dur_label = ctk.CTkLabel(
                row,
                text=dur_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["text_secondary"],
                width=35,
            )
            dur_label.pack(side=ctk.RIGHT)

        # 添加到歌单按钮
        add_btn = ctk.CTkButton(
            row,
            text="➕",
            width=22,
            height=22,
            font=ctk.CTkFont(size=9),
            fg_color="transparent",
            hover_color=COLORS["accent"],
            text_color=COLORS["text_secondary"],
            command=lambda fp=filepath: self._music_add_to_playlist_menu(fp, is_online=False),
        )
        add_btn.pack(side=ctk.RIGHT, padx=(0, 2))

        for child in [row, index_label, name_label]:
            child.bind("<Button-1>", lambda e, i=idx: self._play_from_index(i))
            child.bind("<Double-Button-1>", lambda e, i=idx: self._play_from_index(i))

        self._music_playlist_widgets.append({"frame": row, "name_label": name_label, "index": idx})

    def _play_from_index(self, idx: int):
        if idx < 0 or idx >= len(self._music_playlist):
            return
        self._music_current_index = idx
        self._music_progress = 0
        self._play_file(self._music_playlist[idx])
        self._save_music_state_later()

    def _highlight_current_in_list(self):
        for w in self._music_playlist_widgets:
            try:
                f = w.get("frame")
                if not f or not f.winfo_exists():
                    continue
                label = w.get("name_label")
                if w.get("index") == self._music_current_index:
                    f.configure(fg_color=COLORS["accent"])
                    if label:
                        label.configure(text_color=COLORS["text_primary"])
                else:
                    f.configure(fg_color="transparent")
                    if label:
                        label.configure(text_color=COLORS["text_primary"])
            except Exception:
                pass

    # ═══════════════ 歌单管理 ─────────────────────

    def _rebuild_playlist_sidebar(self):
        """重建左侧歌单侧边栏"""
        if not hasattr(self, "_music_playlist_sidebar"):
            return
        # 清除旧组件
        for w in self._music_playlist_sidebar_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_playlist_sidebar_widgets.clear()

        # ── "全部歌曲" 条目 ──
        all_songs_item = self._build_sidebar_item(None, _("music_all_songs"))
        self._music_playlist_sidebar_widgets.append(all_songs_item)

        # 分隔线
        sep = ctk.CTkFrame(self._music_playlist_sidebar, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, padx=12, pady=4)
        self._music_playlist_sidebar_widgets.append({"frame": sep})

        # ── 用户歌单列表 ──
        mgr = self._music_playlist_manager
        current_id = mgr.current_playlist_id

        for pl in mgr.playlists:
            if pl.is_system:
                # 系统歌单特殊显示
                icon = "🕐"
                display_name = f"{icon} {pl.name} ({pl.song_count})"
            else:
                display_name = f"{pl.name} ({pl.song_count})"
            item = self._build_sidebar_item(pl.id, display_name, is_active=(pl.id == current_id))
            self._music_playlist_sidebar_widgets.append(item)

        # ── 网易云账号歌单（只读同步，不落盘，禁止编辑） ──
        self._music_rebuild_wy_remote_sidebar()

        # 高亮当前选中
        self._highlight_sidebar_selection()

    def _music_rebuild_wy_remote_sidebar(self):
        """重建侧边栏「网易云歌单」分组（只读，无右键菜单）"""
        remote = self._music_wy_remote_playlists
        if not remote:
            return
        # 分隔线
        sep = ctk.CTkFrame(self._music_playlist_sidebar, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, padx=12, pady=4)
        self._music_playlist_sidebar_widgets.append({"frame": sep})
        # 分组标题（最近一次同步失败时附加提示）
        title = _("music_wy_playlists")
        if self._music_wy_sync_failed:
            title = f"{title}（{_('music_wy_sync_failed')}）"
        header = ctk.CTkLabel(
            self._music_playlist_sidebar,
            text=title,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        header.pack(anchor=ctk.W, padx=10, pady=(4, 2))
        self._music_playlist_sidebar_widgets.append({"frame": header})
        # 远程歌单条目（只读：不绑定右键菜单，禁止编辑）
        for pl in remote:
            display_name = f"{pl['name']} ({pl['track_count']})"
            item = self._build_sidebar_item(
                self._music_wy_remote_key(pl["id"]),
                display_name,
                is_active=(pl["id"] == self._music_wy_remote_view_id),
            )
            self._music_playlist_sidebar_widgets.append(item)

    def _build_sidebar_item(
        self, playlist_id: Optional[str], text: str, is_active: bool = False
    ) -> dict:
        """构建单个侧边栏条目

        playlist_id 以 _WY_REMOTE_PREFIX 开头时为网易云远程歌单条目
        （只读查看，无右键菜单，禁止编辑）。
        """
        active_bg = COLORS["bg_light"]
        normal_bg = "transparent"
        frame = ctk.CTkFrame(
            self._music_playlist_sidebar, fg_color=active_bg if is_active else normal_bg, corner_radius=6, height=30
        )
        frame.pack(fill=ctk.X, pady=1)
        frame.pack_propagate(False)

        label = ctk.CTkLabel(
            frame,
            text=text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        label.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=10)

        ctx = {"frame": frame, "label": label, "playlist_id": playlist_id}

        # 点击切换到歌单
        click_targets = [frame, label]
        if playlist_id is None:
            # 全部歌曲 → 切换到本地标签页
            for t in click_targets:
                t.bind("<Button-1>", lambda e: self._music_switch_to_local())
                t.bind("<Double-Button-1>", lambda e: self._music_switch_to_local())
        elif playlist_id.startswith(_WY_REMOTE_PREFIX):
            # 远程歌单条目：只读查看，不绑定右键菜单（禁止编辑）
            real_id = playlist_id[len(_WY_REMOTE_PREFIX):]
            for t in click_targets:
                t.bind("<Button-1>", lambda e, pid=real_id: self._music_show_wy_remote_playlist(pid))
                t.bind("<Double-Button-1>", lambda e, pid=real_id: self._music_show_wy_remote_playlist(pid))
        else:
            for t in click_targets:
                t.bind("<Button-1>", lambda e, pid=playlist_id: self._music_show_playlist(pid))
                t.bind("<Double-Button-1>", lambda e, pid=playlist_id: self._music_show_playlist(pid))
                # 右键菜单（仅用户歌单）
                t.bind("<Button-3>", lambda e, pid=playlist_id: self._music_show_playlist_context_menu(e, pid))

        return ctx

    def _highlight_sidebar_selection(self):
        """高亮侧边栏当前选中项"""
        mgr = self._music_playlist_manager
        selected_id = mgr.current_playlist_id
        wy_view_id = getattr(self, "_music_wy_remote_view_id", None)
        wy_key = self._music_wy_remote_key(wy_view_id) if wy_view_id else None

        for w in self._music_playlist_sidebar_widgets:
            frame = w.get("frame")
            if not frame or not frame.winfo_exists():
                continue
            pid = w.get("playlist_id")
            if pid is None:
                # "全部歌曲" — 仅在本地标签页时高亮
                frame.configure(fg_color="transparent")
            elif wy_key is not None and pid == wy_key:
                frame.configure(fg_color=COLORS["bg_light"])
            elif pid == selected_id:
                frame.configure(fg_color=COLORS["bg_light"])
            else:
                frame.configure(fg_color="transparent")

    def _music_show_playlist_context_menu(self, event, playlist_id: str):
        """显示歌单右键菜单"""
        pl = self._music_playlist_manager.get_playlist(playlist_id)
        if pl is None:
            return

        menu = ctk.CTkToplevel(self)
        menu.title("")
        menu.geometry(f"+{event.x_root}+{event.y_root}")
        menu.overrideredirect(True)
        menu.configure(fg_color=COLORS["card_bg"])
        menu.lift()
        menu.focus_force()

        btn_cfg = {
            "font": ctk.CTkFont(family=FONT_FAMILY, size=11),
            "fg_color": "transparent",
            "hover_color": COLORS["bg_light"],
            "text_color": COLORS["text_primary"],
            "anchor": "w",
            "height": 28,
        }

        ctk.CTkButton(
            menu,
            text=_("music_rename_playlist"),
            command=lambda: self._music_rename_playlist_dialog(playlist_id) or menu.destroy(),
            **btn_cfg,
        ).pack(fill=ctk.X, padx=4, pady=2)
        ctk.CTkButton(
            menu,
            text=_("music_delete_playlist"),
            command=lambda: self._music_delete_playlist_confirm(playlist_id) or menu.destroy(),
            **btn_cfg,
        ).pack(fill=ctk.X, padx=4, pady=2)

        # 如果是系统歌单，禁用编辑按钮
        if pl.is_system:
            for child in menu.winfo_children():
                try:
                    child.configure(state=ctk.DISABLED, text_color=COLORS["text_secondary"])
                except Exception:
                    pass

        def _close_menu(e=None):
            try:
                menu.destroy()
            except Exception:
                pass

        menu.bind("<FocusOut>", _close_menu)
        menu.bind("<Escape>", _close_menu)
        menu.after(5000, _close_menu)

    def _music_create_playlist_dialog(self):
        """弹出新建歌单对话框"""
        from ui.dialogs import show_input_dialog

        name = show_input_dialog(
            parent=self, title=_("music_new_playlist"), prompt=_("music_playlist_name_placeholder"), initial_value=""
        )
        if not name or not name.strip():
            return
        name = name.strip()
        mgr = self._music_playlist_manager
        pl = mgr.create_playlist(name)
        mgr.set_current_playlist(pl.id)
        self._music_show_playlist(pl.id)
        self._save_music_state_later()

    def _music_rename_playlist_dialog(self, playlist_id: str):
        """弹出重命名歌单对话框"""
        from ui.dialogs import show_input_dialog

        pl = self._music_playlist_manager.get_playlist(playlist_id)
        if pl is None:
            return

        name = show_input_dialog(
            parent=self,
            title=_("music_rename_playlist"),
            prompt=_("music_playlist_name_placeholder"),
            initial_value=pl.name,
        )
        if not name or not name.strip():
            return
        name = name.strip()
        self._music_playlist_manager.rename_playlist(playlist_id, name)
        self._rebuild_playlist_sidebar()
        self._save_music_state_later()

    def _music_delete_playlist_confirm(self, playlist_id: str):
        """确认删除歌单"""
        import tkinter.messagebox as messagebox

        pl = self._music_playlist_manager.get_playlist(playlist_id)
        if pl is None:
            return

        msg = _("music_confirm_delete_playlist", name=pl.name)
        if msg == "music_confirm_delete_playlist":
            msg = f"确定要删除歌单「{pl.name}」吗？"

        if not messagebox.askyesno(_("music_delete_playlist"), msg):
            return

        mgr = self._music_playlist_manager
        mgr.delete_playlist(playlist_id)
        self._rebuild_playlist_sidebar()
        self._save_music_state_later()

    def _music_show_playlist(self, playlist_id: str):
        """在歌单标签页中显示指定歌单"""
        # 先确保在歌单标签页
        if self._music_tab_mode != "playlist":
            self._music_switch_to_playlist_tab()
        # 切换到本地歌单：清除远程歌单查看状态（防止两处同时高亮）
        self._music_wy_remote_view_id = None
        self._music_wy_remote_view_songs = []
        self._music_wy_update_pager()

        mgr = self._music_playlist_manager
        pl = mgr.get_playlist(playlist_id)
        if pl is None:
            return

        mgr.set_current_playlist(playlist_id)

        # 恢复该歌单的排序模式
        self._update_sort_buttons(pl.sort_mode)

        # 渲染歌曲列表
        self._rebuild_playlist_song_list(pl)
        self._rebuild_playlist_sidebar()

    def _rebuild_playlist_song_list(self, pl: Playlist, readonly: bool = False):
        """渲染歌单中的歌曲列表（readonly=True 时禁止编辑：无移除按钮、无右键菜单）"""
        # 清除旧列表
        for w in self._music_playlist_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_playlist_widgets.clear()

        if not pl.songs:
            # 空歌单占位
            empty_label = ctk.CTkLabel(
                self._music_playlist_scroll,
                text=_("music_playlist_empty"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
            )
            empty_label.pack(pady=30)
            self._music_playlist_widgets.append({"frame": empty_label})
            return

        for idx, song in enumerate(pl.songs):
            self._add_playlist_song_row(idx, song, readonly=readonly)

    def _add_playlist_song_row(self, idx: int, song: "PlaylistSong", readonly: bool = False):
        """渲染歌单中的单行歌曲（readonly=True 时无移除按钮）"""
        row = ctk.CTkFrame(self._music_playlist_scroll, fg_color="transparent", height=32)
        row.pack(fill=ctk.X, pady=1)

        # 序号
        ctk.CTkLabel(
            row,
            text=str(idx + 1),
            width=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT)

        # 显示的文本
        display_text = song.get_display_text(max_title=50)
        name_label = ctk.CTkLabel(
            row,
            text=display_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        name_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(5, 5))

        # 时长标记
        if song.source_type == "online" and song.online_interval:
            ctk.CTkLabel(
                row,
                text=_format_time(song.online_interval),
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["text_secondary"],
                width=35,
            ).pack(side=ctk.RIGHT, padx=(0, 2))

        # 来源标记
        if song.source_type == "online":
            ctk.CTkLabel(
                row,
                text=song.online_source.upper(),
                font=ctk.CTkFont(family=FONT_FAMILY, size=8),
                text_color=COLORS["accent"],
                width=28,
            ).pack(side=ctk.RIGHT, padx=(0, 2))

        # 移除按钮（只读歌单不显示：禁止编辑）
        if not readonly:
            remove_btn = ctk.CTkButton(
                row,
                text="✕",
                width=22,
                height=22,
                font=ctk.CTkFont(size=9),
                fg_color="transparent",
                hover_color=COLORS["accent"],
                text_color=COLORS["text_secondary"],
                command=lambda si=idx: self._music_remove_song_from_playlist(si),
            )
            remove_btn.pack(side=ctk.RIGHT, padx=(0, 4))

        # 点击播放
        self._bind_playlist_song_click(row, name_label, idx)

        self._music_playlist_widgets.append({"frame": row, "name_label": name_label, "index": idx})

    def _bind_playlist_song_click(self, row, label, idx: int):
        """绑定歌单歌曲点击事件"""
        # 网易云远程歌单（只读，分页）：绑定完整歌曲列表中的真实索引
        if self._music_wy_remote_view_id:
            songs = self._music_wy_remote_view_songs
            real_idx = (self._music_wy_remote_page - 1) * _MUSIC_WY_PAGE_SIZE + idx
            if real_idx >= len(songs):
                return

            def _play_remote(e=None):
                self._music_playlist_context_songs = list(songs)
                self._play_playlist_context_song(real_idx)

            for t in [row, label]:
                t.bind("<Button-1>", _play_remote)
                t.bind("<Double-Button-1>", _play_remote)
            return
        pl = self._music_playlist_manager.get_current_playlist()
        if pl is None or idx >= len(pl.songs):
            return
        song = pl.songs[idx]

        def _play(e=None):
            # 保存歌单上下文，供上下曲使用
            self._music_playlist_context_songs = list(pl.songs)
            self._play_playlist_context_song(idx)

        for t in [row, label]:
            t.bind("<Button-1>", _play)
            t.bind("<Double-Button-1>", _play)

    def _music_remove_song_from_playlist(self, song_index: int):
        """从当前歌单中移除歌曲"""
        if self._music_wy_remote_view_id:
            return  # 远程歌单只读，禁止编辑
        pl = self._music_playlist_manager.get_current_playlist()
        if pl is None:
            return
        if self._music_playlist_manager.remove_song(pl.id, song_index):
            self._rebuild_playlist_song_list(pl)
            self._rebuild_playlist_sidebar()
            self._save_music_state_later()

    def _music_do_sort(self, mode: str):
        """执行歌单排序"""
        # 网易云远程歌单（只读）：仅内存内排序，不落盘；同模式再次点击切换方向
        if self._music_wy_remote_view_id:
            songs = self._music_wy_remote_view_songs
            if not songs:
                return
            if self._music_wy_remote_sort_mode == mode:
                if mode == SORT_ADD_TIME_DESC:
                    mode = SORT_ADD_TIME_ASC
                elif mode == SORT_ADD_TIME_ASC:
                    mode = SORT_ADD_TIME_DESC
                elif mode == SORT_NAME_ASC:
                    mode = SORT_NAME_DESC
                elif mode == SORT_NAME_DESC:
                    mode = SORT_NAME_ASC
            self._music_wy_remote_sort_mode = mode
            transient = Playlist(id=self._music_wy_remote_key(self._music_wy_remote_view_id), songs=songs, sort_mode=mode)
            PlaylistManager._sort_playlist_internal(transient)
            self._update_sort_buttons(mode)
            self._music_render_wy_remote_songs(self._music_wy_remote_view_id, songs)
            return
        mgr = self._music_playlist_manager
        pl = mgr.get_current_playlist()
        if pl is None:
            return

        # 如果当前排序模式相同，切换方向
        if pl.sort_mode == mode:
            if mode == SORT_ADD_TIME_DESC:
                mode = SORT_ADD_TIME_ASC
            elif mode == SORT_ADD_TIME_ASC:
                mode = SORT_ADD_TIME_DESC
            elif mode == SORT_NAME_ASC:
                mode = SORT_NAME_DESC
            elif mode == SORT_NAME_DESC:
                mode = SORT_NAME_ASC

        mgr.sort(pl.id, mode)
        self._update_sort_buttons(mode)
        self._rebuild_playlist_song_list(pl)
        self._save_music_state_later()

    def _update_sort_buttons(self, mode: str):
        """更新排序按钮状态"""
        add_time_active = mode in (SORT_ADD_TIME_ASC, SORT_ADD_TIME_DESC)
        name_active = mode in (SORT_NAME_ASC, SORT_NAME_DESC)

        if mode == SORT_ADD_TIME_DESC:
            self._music_sort_add_time_btn.configure(text=_("music_sort_add_time") + " ▼", fg_color=COLORS["accent"])
        elif mode == SORT_ADD_TIME_ASC:
            self._music_sort_add_time_btn.configure(text=_("music_sort_add_time") + " ▲", fg_color=COLORS["accent"])
        else:
            self._music_sort_add_time_btn.configure(text=_("music_sort_add_time"), fg_color=COLORS["bg_light"])

        if mode == SORT_NAME_ASC:
            self._music_sort_name_btn.configure(text=_("music_sort_name") + " ▲", fg_color=COLORS["accent"])
        elif mode == SORT_NAME_DESC:
            self._music_sort_name_btn.configure(text=_("music_sort_name") + " ▼", fg_color=COLORS["accent"])
        else:
            self._music_sort_name_btn.configure(text=_("music_sort_name"), fg_color=COLORS["bg_light"])

    def _music_add_to_playlist_menu(self, song_info, is_online: bool = False):
        """弹出"添加到歌单"菜单"""
        mgr = self._music_playlist_manager
        playlists = mgr.playlists
        if not playlists:
            # 没有歌单，提示先创建
            import tkinter.messagebox as messagebox

            msg = _("music_new_playlist")
            if messagebox.askyesno(_("music_new_playlist"), "还没有歌单，是否创建一个？"):
                self._music_create_playlist_dialog()
            return

        # 创建临时右键菜单
        menu = ctk.CTkToplevel(self)
        menu.title("")
        menu.overrideredirect(True)
        menu.configure(fg_color=COLORS["card_bg"])
        menu.lift()
        menu.focus_force()

        btn_cfg = {
            "font": ctk.CTkFont(family=FONT_FAMILY, size=11),
            "fg_color": "transparent",
            "hover_color": COLORS["bg_light"],
            "text_color": COLORS["text_primary"],
            "anchor": "w",
            "height": 26,
        }

        for pl in playlists:
            # 检查是否已存在
            if is_online:
                exists = mgr.is_song_in_any_playlist(
                    online_source=song_info.source if is_online else "",
                    online_songmid=song_info.songmid if is_online else "",
                )
            else:
                exists = mgr.is_song_in_any_playlist(file_path=song_info if not is_online else "")

            display_text = pl.name
            if exists:
                display_text = f"{pl.name} ✓"

            btn = ctk.CTkButton(
                menu,
                text=display_text,
                command=lambda pid=pl.id: self._music_add_song_to_playlist(pid, song_info, is_online) or menu.destroy(),
                **btn_cfg,
            )
            btn.pack(fill=ctk.X, padx=4, pady=1)

            if exists:
                btn.configure(state="disabled")

        # 自动定位
        try:
            x = self.winfo_pointerx()
            y = self.winfo_pointery()
            menu.geometry(f"+{x}+{y}")
        except Exception:
            pass

        def _close_menu(e=None):
            try:
                menu.destroy()
            except Exception:
                pass

        menu.bind("<FocusOut>", _close_menu)
        menu.bind("<Escape>", _close_menu)
        menu.after(5000, _close_menu)

    def _music_add_song_to_playlist(self, playlist_id: str, song_info, is_online: bool = False):
        """将歌曲添加到指定歌单"""
        mgr = self._music_playlist_manager

        if is_online:
            song = PlaylistSong.from_online_info(song_info)
        else:
            meta = self._get_metadata(song_info)
            song = PlaylistSong.from_local_file(song_info, meta)

        if mgr.add_song(playlist_id, song):
            self._rebuild_playlist_sidebar()
            # 如果当前正在查看该歌单，刷新列表
            current = mgr.get_current_playlist()
            if current and current.id == playlist_id:
                self._rebuild_playlist_song_list(current)
            self._save_music_state_later()

    # ── 播放历史记录 ──

    def _music_record_play_history_local(self, filepath: str):
        """记录本地歌曲到播放历史"""
        if not hasattr(self, "_music_playlist_manager"):
            return
        meta = self._get_metadata(filepath)
        song = PlaylistSong.from_local_file(filepath, meta)
        self._music_playlist_manager.record_to_history(song)
        self._music_refresh_history_ui()

    def _music_record_play_history_online(self, online_info):
        """记录在线歌曲到播放历史"""
        if not hasattr(self, "_music_playlist_manager"):
            return
        try:
            song = PlaylistSong.from_online_info(online_info)
        except Exception:
            return
        self._music_playlist_manager.record_to_history(song)
        self._music_refresh_history_ui()

    def _music_refresh_history_ui(self):
        """记录历史后刷新 UI（当前正停留在历史歌单视图时立即重绘）"""
        try:
            if self._music_tab_mode != "playlist":
                return
            mgr = self._music_playlist_manager
            current = mgr.get_current_playlist()
            if current is None or current.id != HISTORY_PLAYLIST_ID:
                return
            self._rebuild_playlist_song_list(current)
        except Exception:
            pass

    # ═══════════════ 网易云账号歌单（只读同步，不落盘） ═══════════════
    #
    # 登录网易云账号后，将账号创建的歌单同步到侧边栏「网易云歌单」分组。
    # 远程歌单只保存在内存（列表 + 歌曲缓存），不进入 PlaylistManager，
    # 因此永不写入 music.json、永不参与本地歌单的增删改（禁止编辑）。

    def _music_wy_remote_key(self, pl_id: str) -> str:
        """远程歌单的侧边栏条目 id（与本地歌单 id 区分）"""
        return f"{_WY_REMOTE_PREFIX}{pl_id}"

    def _music_wy_sync_remote_playlists(self):
        """同步网易云登录账号创建的歌单列表（后台线程，结果经队列回主线程）

        未登录时清空远程歌单与歌曲缓存；同步失败保留上次成功结果并标记提示。
        """
        if not hasattr(self, "_music_wy_queue") or self._music_wy_sync_busy:
            return
        self._music_wy_sync_busy = True
        self._music_wy_sync_seq += 1
        seq = self._music_wy_sync_seq
        threading.Thread(target=self._music_wy_sync_worker, args=(seq,), daemon=True).start()

    def _music_wy_sync_worker(self, seq: int):
        """后台线程：检查登录态并拉取歌单列表（绝不触碰 Tk）"""
        state, data = "ok", []
        try:
            from ui.music_source import wy_get_user_playlists, wy_is_logged_in

            if not wy_is_logged_in():
                state = "logged_out"
            else:
                data = wy_get_user_playlists()
                if data is None:
                    state = "error"
        except Exception as e:
            logger.warning(f"网易云歌单同步失败: {e}")
            state = "error"
        self._music_wy_queue.put(("sync", seq, state, data))

    def _music_wy_dispatcher_tick(self):
        """主线程调度器：统一处理后台线程投递的事件（worker 不直接触碰 Tk）"""
        if not hasattr(self, "_music_wy_queue"):
            return
        try:
            while True:
                try:
                    event = self._music_wy_queue.get_nowait()
                except queue.Empty:
                    break
                self._music_wy_handle_event(event)
        except Exception as e:
            logger.debug(f"网易云歌单事件处理异常: {e}")
        try:
            self._music_wy_dispatcher_id = self.after(200, self._music_wy_dispatcher_tick)
        except Exception:
            pass

    def _music_wy_handle_event(self, event):
        kind = event[0]
        try:
            if kind == "sync":
                self._music_wy_apply_sync(event[1], event[2], event[3])
            elif kind == "tracks":
                self._music_wy_apply_tracks(event[1], event[2])
        except Exception as e:
            logger.debug(f"网易云歌单事件执行异常: {e}")

    def _music_wy_apply_sync(self, seq: int, state: str, playlists: List[dict]):
        """主线程应用歌单列表同步结果"""
        if seq != self._music_wy_sync_seq:
            return  # 过期同步结果（已触发新的同步），丢弃
        self._music_wy_sync_busy = False
        if state == "logged_out":
            # 账号已退出（设置页退出/登录失效）：清空远程歌单与歌曲缓存
            if self._music_wy_remote_view_id:
                self._music_wy_remote_view_id = None
                self._music_wy_remote_view_songs = []
                if self._music_tab_mode == "playlist":
                    history = self._music_playlist_manager.get_or_create_history_playlist()
                    self._music_show_playlist(history.id)
            self._music_wy_remote_playlists = []
            self._music_wy_remote_cache.clear()
            self._music_wy_loading_ids.clear()
            self._music_wy_sync_failed = False
            self._rebuild_playlist_sidebar()
            return
        if state == "error":
            # 网络/接口失败：保留上次成功结果，侧边栏标题标记同步失败
            self._music_wy_sync_failed = True
            return
        self._music_wy_sync_failed = False
        if self._music_wy_remote_playlists == playlists:
            return  # 无变化，不重建侧边栏
        old_ids = {p["id"] for p in self._music_wy_remote_playlists}
        new_ids = {p["id"] for p in playlists}
        # 清除已不在列表中的歌单的歌曲缓存与加载标记
        for pid in old_ids - new_ids:
            self._music_wy_remote_cache.pop(pid, None)
            self._music_wy_loading_ids.discard(pid)
        self._music_wy_remote_playlists = playlists
        # 当前查看的远程歌单已被删除：切回播放历史
        if self._music_wy_remote_view_id and self._music_wy_remote_view_id not in new_ids:
            self._music_wy_remote_view_id = None
            self._music_wy_remote_view_songs = []
            if self._music_tab_mode == "playlist":
                history = self._music_playlist_manager.get_or_create_history_playlist()
                self._music_show_playlist(history.id)
        self._rebuild_playlist_sidebar()

    def _music_show_wy_remote_playlist(self, pl_id: str):
        """在歌单标签页显示网易云远程歌单（只读）"""
        if self._music_tab_mode != "playlist":
            self._music_switch_to_playlist_tab()
        self._music_wy_remote_view_id = pl_id
        self._music_wy_remote_page = 1  # 切换歌单后回到第一页
        self._update_sort_buttons(self._music_wy_remote_sort_mode)
        cached = self._music_wy_remote_cache.get(pl_id)
        if cached is not None:
            # 缓存命中（含空歌单成功缓存 []）：直接渲染
            self._music_render_wy_remote_songs(pl_id, cached)
        else:
            self._music_show_wy_remote_loading(pl_id)
            self._music_wy_fetch_remote_songs(pl_id)
        self._rebuild_playlist_sidebar()

    def _music_wy_fetch_remote_songs(self, pl_id: str):
        """后台拉取远程歌单歌曲（已在加载中则跳过）"""
        if pl_id in self._music_wy_loading_ids:
            return
        self._music_wy_loading_ids.add(pl_id)
        threading.Thread(target=self._music_wy_tracks_worker, args=(pl_id,), daemon=True).start()

    def _music_wy_tracks_worker(self, pl_id: str):
        """后台线程：拉取歌单歌曲（绝不触碰 Tk）"""
        try:
            from ui.music_source import wy_get_playlist_tracks

            infos = wy_get_playlist_tracks(pl_id)
        except Exception as e:
            logger.warning(f"获取网易云歌单歌曲失败 [{pl_id}]: {e}")
            infos = None
        self._music_wy_queue.put(("tracks", pl_id, infos))

    def _music_wy_apply_tracks(self, pl_id: str, infos):
        """主线程应用歌单歌曲拉取结果（None=失败，[]=空歌单）"""
        self._music_wy_loading_ids.discard(pl_id)
        if self._music_wy_remote_view_id != pl_id:
            # 用户已切换歌单：成功结果仅缓存，供下次点击直接使用
            if infos is not None:
                self._music_wy_remote_cache[pl_id] = [PlaylistSong.from_online_info(i) for i in infos]
            return
        if infos is None:
            self._music_show_wy_remote_failed(pl_id)
            return
        songs = [PlaylistSong.from_online_info(i) for i in infos]
        self._music_wy_remote_cache[pl_id] = songs
        self._music_render_wy_remote_songs(pl_id, songs)

    def _music_show_wy_remote_loading(self, pl_id: str):
        """远程歌单加载中占位"""
        for w in self._music_playlist_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_playlist_widgets.clear()
        self._music_wy_remote_view_songs = []
        self._music_wy_update_pager()
        label = ctk.CTkLabel(
            self._music_playlist_scroll,
            text=_("music_wy_playlist_loading"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        label.pack(pady=30)
        self._music_playlist_widgets.append({"frame": label})

    def _music_show_wy_remote_failed(self, pl_id: str):
        """远程歌单加载失败提示（再次点击侧边栏条目可重试）"""
        for w in self._music_playlist_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_playlist_widgets.clear()
        self._music_wy_remote_view_songs = []
        self._music_wy_update_pager()
        label = ctk.CTkLabel(
            self._music_playlist_scroll,
            text=_("music_wy_playlist_load_failed"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        label.pack(pady=30)
        self._music_playlist_widgets.append({"frame": label})

    def _music_render_wy_remote_songs(self, pl_id: str, songs: List[PlaylistSong]):
        """渲染远程歌单当前页歌曲列表（只读，禁止编辑，超过 20 首分页）"""
        self._music_wy_remote_view_songs = songs
        total = len(songs)
        pages = max(1, math.ceil(total / _MUSIC_WY_PAGE_SIZE))
        if self._music_wy_remote_page > pages:
            self._music_wy_remote_page = pages
        start = (self._music_wy_remote_page - 1) * _MUSIC_WY_PAGE_SIZE
        page_songs = songs[start : start + _MUSIC_WY_PAGE_SIZE]
        transient = Playlist(id=self._music_wy_remote_key(pl_id), name="", songs=page_songs)
        self._rebuild_playlist_song_list(transient, readonly=True)
        # 记录每行在完整歌单中的真实索引（高亮/自动翻页依赖）
        for w in self._music_playlist_widgets:
            if "index" in w and "real_index" not in w:
                w["real_index"] = start + w["index"]
        self._music_wy_update_pager()

    def _music_wy_update_pager(self):
        """更新远程歌单分页栏（<=20 首或非远程视图时隐藏）"""
        frame = getattr(self, "_music_wy_pager_frame", None)
        if frame is None or not frame.winfo_exists():
            return
        if not self._music_wy_remote_view_id:
            frame.pack_forget()
            return
        songs = self._music_wy_remote_view_songs
        total = len(songs)
        if total <= _MUSIC_WY_PAGE_SIZE:
            frame.pack_forget()
            return
        pages = max(1, math.ceil(total / _MUSIC_WY_PAGE_SIZE))
        if self._music_wy_remote_page > pages:
            self._music_wy_remote_page = pages
        frame.pack(fill=ctk.X, pady=(4, 0))
        self._music_wy_pager_page_label.configure(
            text=_("music_wy_playlist_page", page=self._music_wy_remote_page, total=pages)
        )
        self._music_wy_pager_prev.configure(state="normal" if self._music_wy_remote_page > 1 else "disabled")
        self._music_wy_pager_next.configure(state="normal" if self._music_wy_remote_page < pages else "disabled")

    def _music_wy_go_page(self, page: int):
        """远程歌单翻页（仅内存内展示切换，不影响完整播放列表）"""
        if not self._music_wy_remote_view_id:
            return
        songs = self._music_wy_remote_view_songs
        pages = max(1, math.ceil(len(songs) / _MUSIC_WY_PAGE_SIZE))
        if page < 1 or page > pages or page == self._music_wy_remote_page:
            return
        self._music_wy_remote_page = page
        self._music_render_wy_remote_songs(self._music_wy_remote_view_id, songs)

    def _music_wy_start_periodic(self):
        """启动网易云歌单定期刷新（未登录时同步逻辑自动跳过，开销可忽略）"""
        if self._music_wy_periodic_id is not None:
            return
        self._music_wy_periodic_id = self.after(_WY_REMOTE_PERIODIC_MS, self._music_wy_periodic_tick)

    def _music_wy_periodic_tick(self):
        self._music_wy_periodic_id = None
        try:
            self._music_wy_sync_remote_playlists()
        except Exception as e:
            logger.debug(f"网易云歌单定时同步异常: {e}")
        self._music_wy_start_periodic()

    # ── 播放全部按钮 ──

    def _music_play_playlist_all(self):
        """播放当前歌单的所有可播放歌曲（支持本地/在线混合）"""
        # 网易云远程歌单：播放远程歌曲（全部为在线歌曲）
        if self._music_wy_remote_view_id:
            songs = self._music_wy_remote_view_songs
            if not songs:
                return
            self._music_playlist_context_songs = list(songs)
            for idx, s in enumerate(songs):
                if s.source_type == "online":
                    self._play_playlist_context_song(idx)
                    return
            return
        mgr = self._music_playlist_manager
        pl = mgr.get_current_playlist()
        if pl is None or not pl.songs:
            return
        # 保存歌单上下文，供上下曲使用
        self._music_playlist_context_songs = list(pl.songs)
        # 找到第一首可播歌曲
        for idx, s in enumerate(pl.songs):
            if s.source_type == "local" and os.path.exists(s.file_path):
                self._play_playlist_context_song(idx)
                return
            elif s.source_type == "online":
                self._play_playlist_context_song(idx)
                return

    # ═══════════════ 注册热键 ─────────────────────

    def _register_hotkeys(self):
        if self._music_hotkeys_registered:
            return
        if not _keyboard_available:
            logger.debug("keyboard 库不可用，全局热键已禁用")
            return

        def _do_register():
            try:
                self._music_warmup_hook = _keyboard.hook(lambda e: None)
                time.sleep(0.1)
                _keyboard.add_hotkey(DEFAULT_HOTKEYS["play_pause"], self._music_hotkey_play_pause)
                _keyboard.add_hotkey(DEFAULT_HOTKEYS["prev"], self._music_hotkey_prev)
                _keyboard.add_hotkey(DEFAULT_HOTKEYS["next"], self._music_hotkey_next)
                _keyboard.add_hotkey(DEFAULT_HOTKEYS["stop"], self._music_hotkey_stop)
                _keyboard.add_hotkey(DEFAULT_HOTKEYS["vol_up"], self._music_hotkey_vol_up)
                _keyboard.add_hotkey(DEFAULT_HOTKEYS["vol_down"], self._music_hotkey_vol_down)
                _keyboard.add_hotkey(DEFAULT_HOTKEYS["vol_mute"], self._music_hotkey_vol_mute)
                self._music_hotkeys_registered = True
                logger.info("音乐播放全局热键已注册")
            except Exception as e:
                # Linux 下 keyboard 库通常需要 root 且全局热键不可靠，降级为 debug 避免噪音
                global _keyboard_available
                _keyboard_available = False
                if sys.platform == "win32":
                    logger.warning(f"注册全局热键失败: {e}")
                else:
                    logger.debug(f"当前平台不支持全局热键，已跳过: {e}")

        threading.Thread(target=_do_register, daemon=True).start()

    def _unregister_hotkeys(self):
        if not self._music_hotkeys_registered:
            return
        if not _keyboard_available:
            return
        try:
            _keyboard.remove_hotkey(DEFAULT_HOTKEYS["play_pause"])
            _keyboard.remove_hotkey(DEFAULT_HOTKEYS["prev"])
            _keyboard.remove_hotkey(DEFAULT_HOTKEYS["next"])
            _keyboard.remove_hotkey(DEFAULT_HOTKEYS["stop"])
            _keyboard.remove_hotkey(DEFAULT_HOTKEYS["vol_up"])
            _keyboard.remove_hotkey(DEFAULT_HOTKEYS["vol_down"])
            _keyboard.remove_hotkey(DEFAULT_HOTKEYS["vol_mute"])
            if self._music_warmup_hook is not None:
                self._music_warmup_hook()
                self._music_warmup_hook = None
            self._music_hotkeys_registered = False
            logger.info("音乐播放全局热键已注销")
        except Exception:
            pass

    def _music_hotkey_play_pause(self):
        self.after(0, self._music_toggle_play)

    def _music_hotkey_prev(self):
        self.after(0, self._music_prev)

    def _music_hotkey_next(self):
        self.after(0, self._music_next)

    def _music_hotkey_stop(self):
        self.after(0, self._music_stop)

    def _music_hotkey_vol_up(self):
        self.after(0, lambda: self._adjust_volume(5))

    def _music_hotkey_vol_down(self):
        self.after(0, lambda: self._adjust_volume(-5))

    def _music_hotkey_vol_mute(self):
        self.after(0, self._music_toggle_mute)

    def _adjust_volume(self, delta: int):
        new_vol = max(0, min(100, int(self._music_volume * 100) + delta))
        self._music_volume = new_vol / 100.0
        self._music_vol_slider.set(new_vol)
        if hasattr(self, "_music_mini_vol"):
            self._music_mini_vol.set(new_vol)
        if _pygame_import_error is None and not self._music_is_fading:
            try:
                mixer.music.set_volume(self._music_volume)
            except Exception:
                pass
        self._update_mute_btn_ui()
        self._trigger_ach("music_volume_tweaker")

    def _save_music_state_later(self):
        self.after(500, self._save_music_state)

    def _save_music_state(self):
        if not hasattr(self, "_music_init_done") or not self._music_init_done:
            return
        try:
            state = {
                "music_last_folder": self._music_last_folder,
                "music_current_index": self._music_current_index,
                "music_progress": self._music_progress,
                "music_volume": self._music_volume,
                "music_play_mode": PLAY_MODE_NAMES.get(self._music_play_mode, "loop_list"),
                "music_mini_mode": self._music_mini_mode,
                "music_last_playlist_id": self._music_playlist_manager.current_playlist_id,
                "music_last_song_idx_in_playlist": (
                    self._music_playlist_context_idx if self._music_playlist_context_songs else -1
                ),
            }
            if hasattr(self, "callbacks") and "save_music_state" in self.callbacks:
                self.callbacks["save_music_state"](state)
            # 标记歌单为脏，由后台定时器统一写入磁盘，避免频繁 I/O 卡顿
            if hasattr(self, "_music_playlist_manager"):
                self._music_playlist_manager.mark_dirty()
        except Exception as e:
            logger.debug(f"保存音乐状态失败: {e}")

    def _music_apply_wy_saved_login(self, _retry_count: int = 0):
        """将已保存的网易云音乐登录 Cookie 应用到网易云音源会话

        登录后网易云音源可播放 VIP 歌曲并获取 VIP 歌词；
        Cookie 由设置页扫码登录生成，加密持久化在配置中。
        启动早期 callbacks 尚未就绪（主窗口先以空 dict 创建），
        与 _load_music_state 相同：就绪前定时重试。
        """
        if not hasattr(self, "callbacks") or not self.callbacks:
            if _retry_count < 60:
                self.after(500, lambda: self._music_apply_wy_saved_login(_retry_count + 1))
            return
        get_fn = self.callbacks.get("get_wy_cookie")
        if not get_fn:
            if _retry_count < 60:
                self.after(500, lambda: self._music_apply_wy_saved_login(_retry_count + 1))
            return
        try:
            cookie = get_fn()
        except Exception as e:
            logger.debug(f"读取网易云登录 Cookie 失败: {e}")
            return
        if not cookie:
            return
        try:
            from ui.music_source import wy_apply_cookie

            wy_apply_cookie(cookie)
            logger.info("已应用网易云音乐登录 Cookie")
        except Exception as e:
            logger.warning(f"应用网易云音乐登录 Cookie 失败: {e}")
        # 登录恢复成功后同步网易云账号歌单（未登录时由同步逻辑自动清空）
        try:
            self._music_wy_sync_remote_playlists()
        except Exception as e:
            logger.debug(f"启动网易云歌单同步失败: {e}")

    def _load_music_state(self, _retry_count: int = 0):
        if not hasattr(self, "callbacks"):
            return
        load_fn = self.callbacks.get("load_music_state")
        if not load_fn:
            if _retry_count < 60:
                self.after(500, lambda: self._load_music_state(_retry_count + 1))
            return
        try:
            state = load_fn()
            if not state:
                return
            folder = state.get("music_last_folder", "")
            vol = state.get("music_volume", None)
            mode = state.get("music_play_mode", "loop_list")
            self._music_current_index = state.get("music_current_index", -1)
            self._music_progress = state.get("music_progress", 0)
            self._music_mini_mode = state.get("music_mini_mode", False)

            if vol is not None:
                self._music_volume = float(vol)

            mode_map = {v: k for k, v in PLAY_MODE_NAMES.items()}
            self._music_play_mode = mode_map.get(mode, PLAY_MODE_LOOP_LIST)
            self._update_mode_btn_text()

            if hasattr(self, "_music_vol_slider") and self._music_vol_slider.winfo_exists():
                self._music_vol_slider.set(int(self._music_volume * 100))
            if hasattr(self, "_music_mini_vol") and self._music_mini_vol.winfo_exists():
                self._music_mini_vol.set(int(self._music_volume * 100))
            self._update_mute_btn_ui()

            if folder and os.path.isdir(folder):
                self._music_last_folder = folder
                self._music_folder_label.configure(text=os.path.basename(folder) or folder)
                self._music_scan_folder_restore(folder)
            # 加载歌单数据
            if hasattr(self, "_music_playlist_manager"):
                self._music_playlist_manager.load()
                self._rebuild_playlist_sidebar()
                # 自动切换到上次打开的歌单，若不存在则回退到播放历史
                saved_pl_id = state.get("music_last_playlist_id")
                target_id = None
                if saved_pl_id and self._music_playlist_manager.get_playlist(saved_pl_id):
                    target_id = saved_pl_id
                else:
                    history = self._music_playlist_manager.get_or_create_history_playlist()
                    target_id = history.id
                self._music_show_playlist(target_id)
                # 恢复歌单中的歌曲位置和进度
                pl = self._music_playlist_manager.get_current_playlist()
                saved_song_idx = state.get("music_last_song_idx_in_playlist", -1)
                if pl and 0 <= saved_song_idx < len(pl.songs):
                    self._music_playlist_context_songs = list(pl.songs)
                    self._music_playlist_context_idx = saved_song_idx
                    song = pl.songs[saved_song_idx]
                    self._music_progress = state.get("music_progress", 0)
                    # 同步 _music_playlist 供本地播放使用
                    if song.source_type == "local" and os.path.exists(song.file_path):
                        local_paths = [
                            s.file_path for s in pl.songs if s.source_type == "local" and os.path.exists(s.file_path)
                        ]
                        if local_paths:
                            self._music_playlist = local_paths
                            try:
                                self._music_current_index = local_paths.index(song.file_path)
                            except ValueError:
                                pass
                    self._highlight_playlist_song(saved_song_idx)
        except Exception as e:
            logger.debug(f"加载音乐状态失败: {e}")

    # ═══════════════ 启动器就绪后恢复播放状态 ═══════════════

    def _music_on_launcher_ready(self):
        """启动器核心初始化完成后调用，从配置中恢复音乐播放状态"""
        self._load_music_state()

    # ═══════════════ 定时保存 ═══════════════

    def _music_start_periodic_save(self):
        """启动后台定时保存（每 30 秒检查脏标记并落盘）"""
        self._music_periodic_save_id = self.after(30000, self._music_periodic_save_tick)

    def _music_periodic_save_tick(self):
        try:
            if hasattr(self, "_music_playlist_manager"):
                self._music_playlist_manager.save_if_dirty()
        except Exception:
            pass
        self._music_start_periodic_save()

    def _music_stop_periodic_save(self):
        if self._music_periodic_save_id is not None:
            self.after_cancel(self._music_periodic_save_id)
            self._music_periodic_save_id = None

    def _music_scan_folder_restore(self, folder: str):
        files = []
        try:
            for root, dirs, filenames in os.walk(folder):
                for fname in filenames:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in AUDIO_EXTENSIONS:
                        files.append(os.path.join(root, fname))
        except Exception:
            return
        if not files:
            return
        files.sort(key=lambda f: os.path.basename(f).lower())
        self._music_playlist = files
        self._music_metadata_cache.clear()
        count_text = _("music_song_count", count=len(files))
        if count_text == "music_song_count":
            count_text = f"{len(files)} 首"
        self._music_song_count_label.configure(count_text)
        self._rebuild_playlist_ui()
        self._update_mode_btn_text()

        if self._music_mini_mode:
            self._music_main_frame.pack_forget()
            self._music_mini_bar.pack(fill=ctk.X, padx=15, pady=(0, 15))
            if hasattr(self, "_music_mini_toggle_btn") and self._music_mini_toggle_btn.winfo_exists():
                self._music_mini_toggle_btn.configure(text=_("music_expand"))

    # ═══════════════ 在线搜索逻辑 ═══════════════

    def _music_do_search(self):
        keyword = self._music_search_entry.get().strip()
        if not keyword:
            return
        self._music_search_keyword = keyword
        self._music_start_search(1)

    def _music_start_search(self, page: int):
        """发起搜索请求（页码从 1 开始），带忙碌标记与请求序号防并发覆盖"""
        if self._music_search_busy:
            return
        if page < 1:
            return
        self._music_search_page = page
        self._music_search_busy = True
        self._music_search_total_pages = 0  # 新请求未返回前不沿用旧音源的页数
        # 每页条数按当前音源的服务端限制（如网易云每页最多 20 条）
        src = MUSIC_SOURCES.get(self._music_selected_source)
        self._music_search_page_size = src.limits.get("search", 30) if src else 30
        self._music_search_seq += 1
        seq = self._music_search_seq
        self._music_search_btn.configure(state="disabled", text="...")
        self._music_search_status.configure(text=_("music_loading_url"))
        self._music_rebuild_pager()
        threading.Thread(
            target=self._music_online_search_thread, args=(self._music_search_keyword, page, seq), daemon=True
        ).start()

    def _music_online_search_thread(self, keyword: str, page: int, seq: int):
        try:
            source_id = self._music_selected_source
            src = MUSIC_SOURCES.get(source_id)
            if src:
                results = src.search(keyword, page=page, limit=self._music_search_page_size)
            else:
                results = []
        except Exception as e:
            logger.warning(f"在线搜索失败 [{source_id}]: {e}")
            results = []
        self.after(0, lambda: self._music_rebuild_search_results(results, seq))

    def _music_rebuild_search_results(self, results, seq: Optional[int] = None):
        if seq is not None and seq != self._music_search_seq:
            return  # 过期请求（用户已重新搜索/翻页），丢弃
        self._music_search_busy = False
        self._music_search_results = results
        # 音源提供总数时一次性算出总页数；否则按满页启发式判断下一页
        src = MUSIC_SOURCES.get(self._music_selected_source)
        total = src.last_search_total if src else 0
        if total > 0:
            self._music_search_total_pages = max(1, math.ceil(total / self._music_search_page_size))
            self._music_search_has_more = self._music_search_page < self._music_search_total_pages
        else:
            self._music_search_total_pages = 0
            self._music_search_has_more = len(results) >= self._music_search_page_size
        for w in self._music_search_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_search_widgets.clear()
        for idx, info in enumerate(results):
            self._music_add_search_row(idx, info)
        if results:
            count = len(results)
            song_count_key = "music_song_count"
            count_text = _(song_count_key, count=count)
            if count_text == song_count_key:
                count_text = f"{count} 首"
            self._music_search_status.configure(text=count_text)
        elif self._music_search_page > 1:
            # 非首页但无结果：已到最后一页
            no_more_key = "music_search_no_more"
            no_more_text = _(no_more_key)
            if no_more_text == no_more_key:
                no_more_text = "没有更多结果"
            self._music_search_status.configure(text=no_more_text)
        else:
            self._music_search_status.configure(text=_("music_search_no_results"))
        self._music_search_btn.configure(state="normal", text=_("music_search_btn"))
        self._music_rebuild_pager()

    def _music_search_go_page(self, page: int):
        """跳转到指定页码"""
        if page < 1 or self._music_search_busy:
            return
        if not self._music_search_keyword:
            return
        if page == self._music_search_page and self._music_search_results:
            return
        self._music_start_search(page)

    def _music_search_prev_page(self):
        self._music_search_go_page(self._music_search_page - 1)

    def _music_search_next_page(self):
        self._music_search_go_page(self._music_search_page + 1)

    def _music_rebuild_pager(self):
        """重建分页栏：全部页码按钮（一次性按总页数生成）+ 上一页/下一页状态"""
        if not hasattr(self, "_music_pager_frame"):
            return
        cur = self._music_search_page
        busy = self._music_search_busy

        # 音源提供总数时直接生成全部页码；否则满页时推测下一页存在，逐页追加
        if self._music_search_total_pages > 0:
            last = self._music_search_total_pages
        elif self._music_search_has_more:
            last = cur + 1
        else:
            last = cur
        pages = list(range(1, last + 1))

        for w in self._music_pager_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_pager_widgets.clear()

        for p in pages:
            btn = ctk.CTkButton(
                self._music_pager_page_box,
                text=str(p),
                width=30,
                height=24,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                fg_color=COLORS["accent"] if p == cur else COLORS["bg_light"],
                hover_color=COLORS["accent"],
                command=lambda pg=p: self._music_search_go_page(pg),
            )
            btn.pack(side=ctk.LEFT, padx=2)
            self._music_pager_widgets.append({"frame": btn, "page": p})
            self._theme_refs.append((btn, {"fg_color": "bg_light", "hover_color": "accent"}))

        # 自动横向滚动到当前页（按钮宽度一致，按位置比例估算）
        try:
            box = self._music_pager_page_box
            box.update_idletasks()
            if len(pages) > 1:
                box._parent_canvas.xview_moveto((cur - 1) / (len(pages) - 1))
        except Exception:
            pass

        self._music_pager_prev.configure(state=ctk.NORMAL if (cur > 1 and not busy) else ctk.DISABLED)
        self._music_pager_next.configure(
            state=ctk.NORMAL if (self._music_search_has_more and not busy) else ctk.DISABLED
        )

        page_key = "music_search_page"
        page_text = _(page_key, page=cur)
        if page_text == page_key:
            page_text = f"第 {cur} 页"
        self._music_pager_label.configure(text=page_text)

    def _music_add_search_row(self, idx: int, info: OnlineMusicInfo):
        is_original = info.is_original
        row = ctk.CTkFrame(
            self._music_online_scroll,
            fg_color=COLORS["bg_light"] if is_original else "transparent",
            height=32,
        )
        row.pack(fill=ctk.X, pady=1)

        index_label = ctk.CTkLabel(
            row,
            text=str(idx + 1),
            width=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        index_label.pack(side=ctk.LEFT)

        name_text = info.name if len(info.name) <= 35 else info.name[:33] + "..."
        display = f"{name_text} - {info.singer}" if info.singer else name_text
        name_wrap = ctk.CTkFrame(row, fg_color="transparent")
        name_wrap.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(5, 5))
        name_label = ctk.CTkLabel(
            name_wrap,
            text=display,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        name_label.pack(side=ctk.LEFT)

        if is_original:
            tag_text = _("music_original_tag")
            if info.original_name:
                tag_text = _("music_original_tag_with_name", name=info.original_name)
            ctk.CTkLabel(
                name_wrap,
                text=tag_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["warning"],
            ).pack(side=ctk.LEFT, padx=(6, 0))

        dur_text = _format_time(info.interval) if info.interval else ""
        if dur_text:
            ctk.CTkLabel(
                row,
                text=dur_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["text_secondary"],
                width=40,
            ).pack(side=ctk.RIGHT)

        # 播放量（音源未提供时为 0，不显示）
        play_text = _format_play_count(info.play_count)
        if play_text:
            ctk.CTkLabel(
                row,
                text=play_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["text_secondary"],
                width=44,
            ).pack(side=ctk.RIGHT, padx=(0, 4))

        source_label = ctk.CTkLabel(
            row,
            text=info.source.upper(),
            font=ctk.CTkFont(family=FONT_FAMILY, size=8),
            text_color=COLORS["accent"],
            width=28,
        )
        source_label.pack(side=ctk.RIGHT, padx=(0, 4))

        # 添加到歌单按钮
        add_btn = ctk.CTkButton(
            row,
            text="➕",
            width=22,
            height=22,
            font=ctk.CTkFont(size=9),
            fg_color="transparent",
            hover_color=COLORS["accent"],
            text_color=COLORS["text_secondary"],
            command=lambda oi=info: self._music_add_to_playlist_menu(oi, is_online=True),
        )
        add_btn.pack(side=ctk.RIGHT, padx=(0, 2))

        for child in [row, index_label, name_wrap, name_label]:
            child.bind("<Button-1>", lambda e, i=idx: self._music_play_online_from_index(i))
            child.bind("<Double-Button-1>", lambda e, i=idx: self._music_play_online_from_index(i))

        self._music_search_widgets.append({"frame": row, "name_label": name_label, "index": idx})

    def _music_play_online_from_index(self, idx: int):
        if idx < 0 or idx >= len(self._music_search_results):
            return
        self._music_playlist_context_songs = []  # 退出歌单上下文
        self._music_playlist_context_idx = -1
        self._music_play_online_url(self._music_search_results[idx])

    def _music_resolve_auto_quality(self, online_info: OnlineMusicInfo) -> str:
        """自动音质：取当前账号在该歌曲上可用的最高音质

        音源搜索结果的 types/maxbr 由服务器按当前账号返回（免费用户
        最高 128k、音乐包 320k、黑胶VIP 无损、SVIP 母带，登录与否
        直接影响可用音质），自动模式即从高到低选第一个可用的。
        """
        if online_info is None:
            return "128k"
        src = MUSIC_SOURCES.get(online_info.source)
        if src is None:
            return "128k"
        try:
            return src.get_best_quality(online_info, "flac24bit")
        except Exception as e:
            logger.debug(f"自动音质解析失败，回退 128k: {e}")
            return "128k"

    def _music_resolve_auto_quality_async(self, online_info: OnlineMusicInfo) -> Tuple[OnlineMusicInfo, str]:
        """后台线程解析自动音质（含音质信息补齐）

        歌单/播放历史恢复的歌曲由 PlaylistSong 重建，旧数据可能缺少
        可用音质信息（types/_types）。此时先向音源重新搜索同款歌
        （按 songmid 匹配）补齐，再取最高可用音质——否则自动解析
        只能回退 128k，且 URL 获取缺少 hash 等详情。

        Returns:
            (补齐后的歌曲信息, 解析出的音质档位)
        """
        info = online_info
        if info is None:
            return info, "128k"
        if info.types:
            return info, self._music_resolve_auto_quality(info)
        src = MUSIC_SOURCES.get(info.source)
        if src is not None:
            try:
                keyword = f"{info.name} {info.singer}".strip() or info.name
                items = src.search(keyword, page=1, limit=10)
                for it in items or []:
                    if it.songmid == info.songmid and it.types:
                        info = it
                        break
            except Exception as e:
                logger.debug(f"自动音质信息补齐失败 [{info.source}]: {e}")
        return info, self._music_resolve_auto_quality(info)

    def _music_play_online_url(self, online_info: OnlineMusicInfo):
        """触发在线歌曲播放：获取URL -> 下载到临时文件 -> 播放。

        原音源获取 URL 失败、或下载文件无效（HTML 错误页/试听片段）时，
        自动跨源兜底：在其它音源搜索同款歌并播放（YesPlayMusic UNM 风格）。
        """
        self._music_stop(instant=True)
        self._music_stream_seq += 1
        seq = self._music_stream_seq
        self._music_search_status.configure(text=_("music_loading_url"))
        # 用户原始选择（可能为 "auto"）：跨源兜底按此决定音质尝试顺序，
        # "auto" 时兜底从最高音质开始尝试，避免默认命中 128k
        raw_quality = self._music_quality_var.get()

        def _fetch_and_play():
            app = self  # 捕获主应用引用，避免线程间 self 丢失
            play_info = online_info
            play_quality = raw_quality
            if raw_quality == "auto":
                # 歌单/历史恢复的歌曲可能缺少音质信息（types），
                # 在后台线程重新搜索补齐后再解析自动音质，避免默认 128k
                play_info, play_quality = app._music_resolve_auto_quality_async(online_info)
            result_path, result_info, result_quality = app._fetch_online_song(play_info, play_quality, raw_quality)
            app.after(0, lambda: app._music_on_stream_ready(seq, result_path, result_info, online_info, result_quality))

        threading.Thread(target=_fetch_and_play, daemon=True).start()

    def _fetch_online_song(
        self, online_info: OnlineMusicInfo, quality: str, fallback_quality: Optional[str] = None
    ) -> Tuple[Optional[str], OnlineMusicInfo, str]:
        """获取在线歌曲并下载到临时文件，失败时自动跨源兜底。

        Args:
            online_info: 用户点播的歌曲信息
            quality: 原音源实际尝试的音质（"auto" 已解析为具体档位）
            fallback_quality: 跨源兜底的音质选择（可为 "auto"，表示
                从最高可用音质开始尝试；None 时沿用 quality）

        Returns:
            (临时文件路径, 实际播放的歌曲信息, 实际音质)：
            全部失败时路径为 None（info 保持原值）
        """
        result_path = None
        result_info = online_info
        result_quality = quality
        if online_info is None:
            return None, result_info, result_quality

        result_path, _, result_quality = self._try_download_from_source(online_info, quality)
        if result_path:
            return result_path, result_info, result_quality

        fallback_q = fallback_quality or quality
        # 原音源失败 -> 跨源兜底（仅尝试一次，不递归）
        logger.info(f"原音源不可用 [{online_info.source}]: {online_info.name} - {online_info.singer}，开始跨源兜底")
        fallback = self._resolve_fallback(online_info, fallback_q)
        if fallback:
            result = self._download_fallback_result(fallback, quality)
            if result:
                return result

        # B站触发风控时：弹验证码让用户手动完成，通过后带 grisk_id 自动重试兜底
        risk_retry = self._music_try_bili_risk_retry(online_info, quality, fallback_q)
        if risk_retry:
            return risk_retry

        # 全部音源均失败：汇总一条日志（单源失败细节已在 resolve_track 内降为 debug）
        logger.warning(f"跨源兜底失败，所有音源均不可用: {online_info.name} - {online_info.singer} [{online_info.source}]")
        return None, result_info, result_quality

    def _resolve_fallback(self, online_info: OnlineMusicInfo, quality: str) -> Optional[Tuple[OnlineMusicInfo, str]]:
        """跨源兜底解析：返回 (匹配歌曲, 播放URL) 或 None"""
        try:
            return resolve_track(online_info, quality)
        except Exception as e:
            logger.warning(f"跨源兜底解析失败: {e}")
            return None

    def _download_fallback_result(
        self, fallback: Tuple[OnlineMusicInfo, str], quality: str
    ) -> Optional[Tuple[str, OnlineMusicInfo, str]]:
        """下载兜底结果并校验，成功返回 (临时文件路径, 实际歌曲信息, 音质)

        跨源兜底内部会尝试多个音质，无法精确得知最终命中档位，
        此处沿用用户请求的音质用于显示。
        """
        fb_info, fb_url = fallback
        logger.info(f"跨源兜底命中 [{fb_info.source}]: {fb_info.name} - {fb_info.singer}")
        result_path = self._music_download_to_temp(
            fb_url,
            fb_info.name,
            extra_headers=self._music_get_download_headers(fb_info.source),
            extra_cookies=self._music_get_download_cookies(fb_info.source),
        )
        if result_path:
            if _validate_audio_file_header(result_path) and _validate_audio_duration(result_path, fb_info.interval):
                self._notify_fallback_source(fb_info.source)
                return result_path, fb_info, quality
            self._discard_temp_file(result_path)
        return None

    def _music_try_bili_risk_retry(
        self, online_info: OnlineMusicInfo, quality: str, fallback_quality: Optional[str] = None
    ) -> Optional[Tuple[str, OnlineMusicInfo, str]]:
        """B站风控验证：弹窗提示 + 浏览器滑块验证，通过后带 grisk_id 自动重试兜底。

        Returns:
            验证通过且兜底成功: (临时文件路径, 实际歌曲信息, 音质)；否则 None
        """
        src = MUSIC_SOURCES.get("bili")
        if src is None:
            return None
        try:
            risk = src.take_pending_risk()
        except Exception:
            return None
        if not risk:
            return None

        dialog_ref: Dict[str, object] = {}
        stop_event = threading.Event()

        def on_status(text: str):
            self.after(0, lambda: self._music_update_risk_dialog(dialog_ref, text))

        self.after(0, lambda: self._music_open_risk_dialog(dialog_ref, stop_event))
        try:
            result = run_captcha_flow(risk["gt"], risk["challenge"], on_status=on_status, stop_event=stop_event)
        except Exception as e:
            logger.warning(f"风控验证流程异常: {e}")
            result = None
        finally:
            self.after(0, lambda: self._music_close_risk_dialog(dialog_ref))
        if not result:
            return None

        grisk_id = src.validate_risk(
            risk["token"],
            result["geetest_challenge"],
            result["geetest_seccode"],
            result["geetest_validate"],
        )
        if not grisk_id:
            logger.warning("B站风控验证未通过")
            return None
        src.set_gaia_vtoken(grisk_id)
        logger.info("B站风控验证通过，自动重试跨源兜底")
        fallback = self._resolve_fallback(online_info, fallback_quality or quality)
        if not fallback:
            return None
        return self._download_fallback_result(fallback, quality)

    def _music_open_risk_dialog(self, dialog_ref: Dict[str, object], stop_event: threading.Event):
        """打开风控验证提示窗口"""
        try:
            dlg = ctk.CTkToplevel(self)
            dlg.title(_("music_risk_title"))
            dlg.geometry("400x190")
            dlg.resizable(False, False)
            dlg.transient(self)
            dlg.attributes("-topmost", True)

            ctk.CTkLabel(
                dlg,
                text=_("music_risk_title"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
                text_color=COLORS["text_primary"],
            ).pack(pady=(20, 6))

            label = ctk.CTkLabel(
                dlg,
                text=_("music_risk_hint"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
                wraplength=350,
                justify="center",
            )
            label.pack(padx=20, pady=(0, 14))

            def on_close():
                stop_event.set()
                try:
                    dlg.destroy()
                except Exception:
                    pass

            dlg.protocol("WM_DELETE_WINDOW", on_close)
            ctk.CTkButton(
                dlg,
                text=_("music_risk_cancel"),
                width=100,
                height=28,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color=COLORS["bg_light"],
                hover_color=COLORS["card_border"],
                command=on_close,
            ).pack(pady=(0, 16))
            dialog_ref["dialog"] = dlg
            dialog_ref["label"] = label
            self._theme_refs.append((label, {"text_color": "text_secondary"}))
        except Exception as e:
            logger.warning(f"打开风控验证窗口失败: {e}")
            dialog_ref["dialog"] = None

    def _music_update_risk_dialog(self, dialog_ref: Dict[str, object], text: str):
        dlg = dialog_ref.get("dialog")
        label = dialog_ref.get("label")
        if dlg is not None and dlg.winfo_exists() and label is not None:
            try:
                label.configure(text=text)
            except Exception:
                pass

    def _music_close_risk_dialog(self, dialog_ref: Dict[str, object]):
        dlg = dialog_ref.get("dialog")
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:
                pass

    def _notify_fallback_source(self, source_id: str):
        """左下角状态栏提示已切换到其它音源播放"""
        src = MUSIC_SOURCES.get(source_id)
        name = getattr(src, "source_name", None) or source_id
        message = _("music_fallback_status", source=name)
        self.after(0, lambda: self.set_status(message, "info"))

    def _music_get_download_headers(self, source_id: str) -> Dict:
        """获取音源下载所需的附加请求头（如 B站 upos CDN 的 Referer）"""
        src = MUSIC_SOURCES.get(source_id)
        if src is not None:
            try:
                return src.get_download_headers()
            except Exception:
                pass
        return {}

    def _music_get_download_cookies(self, source_id: str) -> Optional[Dict]:
        """获取音源下载所需的附加 cookies（如 B站 dash URL 的 buvid 一致性）"""
        src = MUSIC_SOURCES.get(source_id)
        if src is not None:
            try:
                return src.get_download_cookies()
            except Exception:
                pass
        return None

    def _try_download_from_source(self, online_info: OnlineMusicInfo, quality: str) -> Tuple[Optional[str], Optional[str], str]:
        """尝试从指定音源获取 URL 并下载，校验文件有效后返回 (临时文件路径, 实际URL, 实际音质)。"""
        src = MUSIC_SOURCES.get(online_info.source)
        if src is None:
            return None, None, quality
        url = None
        actual_quality = quality
        try:
            url = src.get_music_url(online_info, quality)
            if not url:
                # 请求档位失败时按高到低回退（各音源对不可用音质返回 None）
                for fallback_q in ["flac", "320k", "128k"]:
                    if fallback_q != quality:
                        url = src.get_music_url(online_info, fallback_q)
                        if url:
                            actual_quality = fallback_q
                            break
        except Exception as e:
            logger.warning(f"获取在线URL失败 [{online_info.source}]: {e}")
        if not url:
            logger.warning(f"无法获取播放URL [{online_info.source}]: {online_info.name}")
            return None, None, quality
        temp_path = self._music_download_to_temp(
            url,
            online_info.name,
            extra_headers=self._music_get_download_headers(online_info.source),
            extra_cookies=self._music_get_download_cookies(online_info.source),
        )
        if not temp_path:
            return None, url, actual_quality
        # 文件头 + 时长双重校验：无效文件视为获取失败（触发跨源兜底）
        if not _validate_audio_file_header(temp_path):
            logger.warning(f"下载文件无效（非音频文件头）[{online_info.source}]: {online_info.name}")
            self._discard_temp_file(temp_path)
            return None, url, actual_quality
        if not _validate_audio_duration(temp_path, online_info.interval):
            logger.warning(f"下载文件为试听/截断片段 [{online_info.source}]: {online_info.name}")
            self._discard_temp_file(temp_path)
            return None, url, actual_quality
        return temp_path, url, actual_quality

    def _discard_temp_file(self, temp_path: str):
        """删除无效的临时文件并移出缓存列表"""
        try:
            os.remove(temp_path)
        except Exception:
            pass
        if temp_path in self._music_temp_files:
            self._music_temp_files.remove(temp_path)

    def _music_on_stream_ready(
        self,
        seq: int,
        temp_path: Optional[str],
        online_info: OnlineMusicInfo,
        origin_info: Optional[OnlineMusicInfo] = None,
        quality: str = "",
    ):
        """流媒体文件下载完成回调（带请求序号守卫，旧请求不覆盖新播放）

        Args:
            seq: 播放请求序号
            temp_path: 下载好的临时文件路径
            online_info: 实际播放的歌曲信息（可能是跨源兜底后的）
            origin_info: 用户点播的原始歌曲信息（兜底时用于播放历史记录）
            quality: 实际获取到的音质档位（128k/320k/flac，用于显示）
        """
        if seq != self._music_stream_seq:
            return  # 用户已切换播放目标，丢弃过期结果
        self._music_search_status.configure(text="")
        if temp_path:
            self._play_online_file(temp_path, online_info, 0, history_origin=origin_info, quality=quality)
        else:
            self._music_search_status.configure(text=_("music_url_failed"))

    def _music_download_to_temp(
        self,
        url: str,
        name_hint: str = "",
        extra_headers: Optional[Dict] = None,
        extra_cookies: Optional[Dict] = None,
    ) -> Optional[str]:
        """下载在线音频流到临时文件

        Args:
            url: 音频流地址
            name_hint: 临时文件名提示
            extra_headers: 附加请求头（如 B站 upos CDN 需要 Referer）
            extra_cookies: 附加 cookies（如 B站 dash URL 的 buvid 与 cookie 一致性校验）
        """
        try:
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
            if extra_headers:
                headers.update(extra_headers)
            resp = requests.get(url, timeout=30, stream=True, headers=headers, cookies=extra_cookies)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            # 快速失败：HTML 错误页/非音频响应不浪费带宽
            if "text/html" in content_type or content_type.startswith("text/") or "json" in content_type:
                logger.warning(f"下载响应非音频（Content-Type: {content_type}）: {url[:120]}")
                return None
            ext = ".mp3"
            # 用 urlparse 取路径判断后缀：播放 URL 常带查询参数，endswith 会失效
            url_path = urlparse(url).path.lower()
            if "flac" in content_type or url_path.endswith(".flac"):
                ext = ".flac"
            elif "ogg" in content_type or url_path.endswith(".ogg"):
                ext = ".ogg"
            elif "m4a" in content_type or url_path.endswith(".m4a") or url_path.endswith(".m4s"):
                ext = ".m4a"
            safe_name = "".join(c for c in name_hint if c.isalnum() or c in "._- ")[:50]
            fd, temp_path = tempfile.mkstemp(suffix=ext, prefix=f"fmcl_{safe_name}_")
            os.close(fd)
            with open(temp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            if os.path.getsize(temp_path) == 0:
                os.remove(temp_path)
                return None
            self._music_temp_files.append(temp_path)
            # pygame 的 SDL_mixer 不支持 m4a 容器（B站 dash/部分平台音源）：
            # 下载后自动转码为 wav，转码失败则保留原文件交由 pygame 尝试
            converted = _transcode_audio_to_wav(temp_path)
            if converted:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                if temp_path in self._music_temp_files:
                    self._music_temp_files.remove(temp_path)
                self._music_temp_files.append(converted)
                return converted
            # 限制临时文件数量
            while len(self._music_temp_files) > 10:
                old = self._music_temp_files.pop(0)
                try:
                    os.remove(old)
                except Exception:
                    pass
            return temp_path
        except Exception as e:
            logger.warning(f"下载音频流失败: {e}")
            return None

    def _music_cleanup_temp_files(self):
        """清理所有缓存的临时文件"""
        for fp in self._music_temp_files:
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception:
                pass
        self._music_temp_files.clear()

    def _stop_search_loading(self):
        """停止搜索加载状态（切换标签页时调用）"""
        if hasattr(self, "_music_search_busy"):
            self._music_search_busy = False
        if hasattr(self, "_music_search_btn") and self._music_search_btn.winfo_exists():
            self._music_search_btn.configure(state="normal", text=_("music_search_btn"))
        if hasattr(self, "_music_search_status") and self._music_search_status.winfo_exists():
            self._music_search_status.configure(text="")
        if hasattr(self, "_music_pager_frame"):
            self._music_rebuild_pager()

    # ═══════════════ 桌面歌词管理 ═══════════════

    def _music_toggle_desktop_lyric(self):
        if self._music_desktop_lyric and self._music_desktop_lyric.is_visible:
            self._music_hide_desktop_lyric()
            self._music_dlrc_btn.configure(fg_color=COLORS["bg_light"])
        else:
            self._music_show_desktop_lyric()
            self._music_dlrc_btn.configure(fg_color=COLORS["accent"])

    def _music_show_desktop_lyric(self):
        """显示桌面歌词窗口"""
        if not self._music_desktop_lyric:
            try:
                self._music_desktop_lyric = DesktopLyricWindow(self)
            except Exception as e:
                logger.warning(f"创建桌面歌词窗口失败: {e}")
                return
        if self._music_lyric_parser.is_parsed:
            self._music_desktop_lyric.set_lyric_lines(self._music_lyric_parser.lines)
        self._music_desktop_lyric.show_lyric()
        self._start_lyric_poll()

    def _music_hide_desktop_lyric(self):
        if self._music_desktop_lyric:
            self._music_desktop_lyric.hide_lyric()

    def _music_destroy_desktop_lyric(self):
        if self._music_desktop_lyric:
            self._music_desktop_lyric.destroy_lyric()
            self._music_desktop_lyric = None

    # ═══════════════ 音效面板 ═══════════════

    def _music_open_fx_panel(self):
        """打开音效设置面板"""
        if hasattr(self, "_music_fx_window") and self._music_fx_window and self._music_fx_window.winfo_exists():
            self._music_fx_window.lift()
            self._music_fx_window.focus_force()
            return
        self._music_fx_window = ctk.CTkToplevel(self)
        self._music_fx_window.title("音效设置")
        self._music_fx_window.geometry("420x520")
        self._music_fx_window.resizable(False, False)
        self._music_fx_window.configure(fg_color=COLORS["card_bg"])
        self._music_fx_window.protocol("WM_DELETE_WINDOW", self._music_close_fx_panel)
        self._music_fx_window.grab_set()

        main = ctk.CTkFrame(self._music_fx_window, fg_color="transparent")
        main.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        self._build_fx_eq_section(main)
        self._build_fx_reverb_section(main)
        self._build_fx_pitch_section(main)
        self._build_fx_speed_section(main)

        # 底部: 重置按钮
        ctk.CTkButton(
            main,
            text=_("music_cache_clear"),
            width=100,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["accent"],
            command=self._music_reset_fx,
        ).pack(pady=(15, 0))

        self._music_fx_window.after(100, lambda: self._music_fx_window.focus_force())

    def _music_close_fx_panel(self):
        if hasattr(self, "_music_fx_window") and self._music_fx_window:
            try:
                self._music_fx_window.grab_release()
                self._music_fx_window.destroy()
            except Exception:
                pass
            self._music_fx_window = None

    def _build_fx_eq_section(self, parent):
        s = self._music_effects.settings
        label_font = ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold")

        eq_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_dark"], corner_radius=8)
        eq_frame.pack(fill=ctk.X, pady=(0, 8))

        header = ctk.CTkFrame(eq_frame, fg_color="transparent")
        header.pack(fill=ctk.X, padx=10, pady=(8, 5))

        eq_enable_var = ctk.BooleanVar(value=s.eq_enabled)
        ctk.CTkCheckBox(
            header,
            text=_("music_eq_enable"),
            variable=eq_enable_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda: self._music_on_eq_toggle(eq_enable_var.get()),
        ).pack(side=ctk.LEFT)

        ctk.CTkLabel(header, text=_("music_eq"), font=label_font, text_color=COLORS["text_primary"]).pack(
            side=ctk.LEFT, padx=(10, 0)
        )

        # EQ 滑块
        eq_sliders_frame = ctk.CTkFrame(eq_frame, fg_color="transparent")
        eq_sliders_frame.pack(fill=ctk.X, padx=10, pady=(5, 10))

        self._music_eq_sliders = []
        for i, freq in enumerate(EQ_FREQS):
            col_frame = ctk.CTkFrame(eq_sliders_frame, fg_color="transparent")
            col_frame.pack(side=ctk.LEFT, expand=True, padx=1)

            slider = ctk.CTkSlider(
                col_frame,
                from_=EQ_GAIN_MIN,
                to=EQ_GAIN_MAX,
                width=16,
                height=120,
                orientation="vertical",
                command=lambda v, idx=i: self._music_on_eq_change(idx, v),
                fg_color=COLORS["bg_light"],
                progress_color=COLORS["accent"],
                button_color=COLORS["text_primary"],
            )
            slider.set(s.eq_gains[i])
            slider.pack()
            self._music_eq_sliders.append(slider)

            ctk.CTkLabel(
                col_frame,
                text=str(freq) if freq >= 1000 else f"{freq}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=7),
                text_color=COLORS["text_secondary"],
            ).pack()

            ctk.CTkLabel(
                col_frame,
                text=f"{s.eq_gains[i]:+.0f}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=7),
                text_color=COLORS["text_secondary"],
            ).pack()

    def _build_fx_reverb_section(self, parent):
        s = self._music_effects.settings
        label_font = ctk.CTkFont(family=FONT_FAMILY, size=11)

        rv_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_dark"], corner_radius=8)
        rv_frame.pack(fill=ctk.X, pady=(0, 8))

        header = ctk.CTkFrame(rv_frame, fg_color="transparent")
        header.pack(fill=ctk.X, padx=10, pady=(8, 5))

        rv_enable_var = ctk.BooleanVar(value=s.reverb_enabled)
        ctk.CTkCheckBox(
            header,
            text=_("music_reverb"),
            variable=rv_enable_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda: self._music_on_reverb_toggle(rv_enable_var.get()),
        ).pack(side=ctk.LEFT)

        # Delay
        row1 = ctk.CTkFrame(rv_frame, fg_color="transparent")
        row1.pack(fill=ctk.X, padx=10, pady=(0, 3))
        ctk.CTkLabel(row1, text="Delay", font=label_font, text_color=COLORS["text_secondary"]).pack(side=ctk.LEFT)
        self._music_reverb_delay_label = ctk.CTkLabel(
            row1, text=f"{s.reverb_delay_ms:.0f}ms", font=label_font, text_color=COLORS["text_secondary"]
        )
        self._music_reverb_delay_label.pack(side=ctk.RIGHT)
        delay_slider = ctk.CTkSlider(
            rv_frame,
            from_=10,
            to=200,
            height=14,
            command=lambda v: self._music_on_reverb_delay(v),
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
        )
        delay_slider.set(s.reverb_delay_ms)
        delay_slider.pack(fill=ctk.X, padx=10, pady=(0, 3))
        self._music_reverb_delay_slider = delay_slider

        # Decay
        row2 = ctk.CTkFrame(rv_frame, fg_color="transparent")
        row2.pack(fill=ctk.X, padx=10, pady=(0, 3))
        ctk.CTkLabel(row2, text="Decay", font=label_font, text_color=COLORS["text_secondary"]).pack(side=ctk.LEFT)
        self._music_reverb_decay_label = ctk.CTkLabel(
            row2, text=f"{s.reverb_decay:.1f}", font=label_font, text_color=COLORS["text_secondary"]
        )
        self._music_reverb_decay_label.pack(side=ctk.RIGHT)
        decay_slider = ctk.CTkSlider(
            rv_frame,
            from_=0.1,
            to=0.9,
            height=14,
            command=lambda v: self._music_on_reverb_decay(v),
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
        )
        decay_slider.set(s.reverb_decay)
        decay_slider.pack(fill=ctk.X, padx=10, pady=(0, 3))
        self._music_reverb_decay_slider = decay_slider

        # Wet Level
        row3 = ctk.CTkFrame(rv_frame, fg_color="transparent")
        row3.pack(fill=ctk.X, padx=10, pady=(0, 8))
        ctk.CTkLabel(row3, text="Wet", font=label_font, text_color=COLORS["text_secondary"]).pack(side=ctk.LEFT)
        self._music_reverb_wet_label = ctk.CTkLabel(
            row3, text=f"{s.reverb_wet_level:.1f}", font=label_font, text_color=COLORS["text_secondary"]
        )
        self._music_reverb_wet_label.pack(side=ctk.RIGHT)
        wet_slider = ctk.CTkSlider(
            rv_frame,
            from_=0.0,
            to=1.0,
            height=14,
            command=lambda v: self._music_on_reverb_wet(v),
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
        )
        wet_slider.set(s.reverb_wet_level)
        wet_slider.pack(fill=ctk.X, padx=10, pady=(0, 3))
        self._music_reverb_wet_slider = wet_slider

    def _build_fx_pitch_section(self, parent):
        s = self._music_effects.settings
        label_font = ctk.CTkFont(family=FONT_FAMILY, size=11)

        pitch_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_dark"], corner_radius=8)
        pitch_frame.pack(fill=ctk.X, pady=(0, 8))

        header = ctk.CTkFrame(pitch_frame, fg_color="transparent")
        header.pack(fill=ctk.X, padx=10, pady=(8, 5))

        pt_enable_var = ctk.BooleanVar(value=s.pitch_enabled)
        ctk.CTkCheckBox(
            header,
            text=_("music_pitch"),
            variable=pt_enable_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda: self._music_on_pitch_toggle(pt_enable_var.get()),
        ).pack(side=ctk.LEFT)

        self._music_pitch_label = ctk.CTkLabel(
            header, text=f"{s.pitch_semitones:+.1f} semitones", font=label_font, text_color=COLORS["text_secondary"]
        )
        self._music_pitch_label.pack(side=ctk.RIGHT)

        pitch_slider = ctk.CTkSlider(
            pitch_frame,
            from_=PITCH_MIN,
            to=PITCH_MAX,
            height=14,
            command=lambda v: self._music_on_pitch_change(v),
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
        )
        pitch_slider.set(s.pitch_semitones)
        pitch_slider.pack(fill=ctk.X, padx=10, pady=(0, 8))
        self._music_pitch_slider = pitch_slider

    def _build_fx_speed_section(self, parent):
        s = self._music_effects.settings
        label_font = ctk.CTkFont(family=FONT_FAMILY, size=11)

        speed_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_dark"], corner_radius=8)
        speed_frame.pack(fill=ctk.X)

        header = ctk.CTkFrame(speed_frame, fg_color="transparent")
        header.pack(fill=ctk.X, padx=10, pady=(8, 5))

        sp_enable_var = ctk.BooleanVar(value=s.speed_enabled)
        ctk.CTkCheckBox(
            header,
            text=_("music_pitch_label"),
            variable=sp_enable_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda: self._music_on_speed_toggle(sp_enable_var.get()),
        ).pack(side=ctk.LEFT)

        self._music_speed_label = ctk.CTkLabel(
            header, text=f"{s.speed_rate:.2f}x", font=label_font, text_color=COLORS["text_secondary"]
        )
        self._music_speed_label.pack(side=ctk.RIGHT)

        speed_slider = ctk.CTkSlider(
            speed_frame,
            from_=SPEED_MIN,
            to=SPEED_MAX,
            height=14,
            command=lambda v: self._music_on_speed_change(v),
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
        )
        speed_slider.set(s.speed_rate)
        speed_slider.pack(fill=ctk.X, padx=10, pady=(0, 8))
        self._music_speed_slider = speed_slider

    # ── 音效回调 ──

    def _music_on_eq_toggle(self, enabled: bool):
        self._music_effects.settings.eq_enabled = enabled

    def _music_on_eq_change(self, idx: int, value: float):
        self._music_effects.settings.eq_gains[idx] = value

    def _music_on_reverb_toggle(self, enabled: bool):
        self._music_effects.settings.reverb_enabled = enabled

    def _music_on_reverb_delay(self, value: float):
        self._music_effects.settings.reverb_delay_ms = value
        if hasattr(self, "_music_reverb_delay_label"):
            self._music_reverb_delay_label.configure(text=f"{value:.0f}ms")

    def _music_on_reverb_decay(self, value: float):
        self._music_effects.settings.reverb_decay = value
        if hasattr(self, "_music_reverb_decay_label"):
            self._music_reverb_decay_label.configure(text=f"{value:.1f}")

    def _music_on_reverb_wet(self, value: float):
        self._music_effects.settings.reverb_wet_level = value
        if hasattr(self, "_music_reverb_wet_label"):
            self._music_reverb_wet_label.configure(text=f"{value:.1f}")

    def _music_on_pitch_toggle(self, enabled: bool):
        self._music_effects.settings.pitch_enabled = enabled

    def _music_on_pitch_change(self, value: float):
        self._music_effects.settings.pitch_semitones = value
        if hasattr(self, "_music_pitch_label"):
            self._music_pitch_label.configure(text=f"{value:+.1f} semitones")

    def _music_on_speed_toggle(self, enabled: bool):
        self._music_effects.settings.speed_enabled = enabled

    def _music_on_speed_change(self, value: float):
        self._music_effects.settings.speed_rate = value
        if hasattr(self, "_music_speed_label"):
            self._music_speed_label.configure(text=f"{value:.2f}x")

    def _music_reset_fx(self):
        """重置所有音效"""
        s = self._music_effects.settings
        s.eq_enabled = False
        s.eq_gains = [0.0] * 10
        s.reverb_enabled = False
        s.reverb_delay_ms = 60.0
        s.reverb_decay = 0.4
        s.reverb_wet_level = 0.3
        s.pitch_enabled = False
        s.pitch_semitones = 0.0
        s.speed_enabled = False
        s.speed_rate = 1.0

        # 更新UI滑块
        if hasattr(self, "_music_eq_sliders"):
            for sl in self._music_eq_sliders:
                sl.set(0)
        if hasattr(self, "_music_reverb_delay_slider"):
            self._music_reverb_delay_slider.set(60)
        if hasattr(self, "_music_reverb_decay_slider"):
            self._music_reverb_decay_slider.set(0.4)
        if hasattr(self, "_music_reverb_wet_slider"):
            self._music_reverb_wet_slider.set(0.3)
        if hasattr(self, "_music_pitch_slider"):
            self._music_pitch_slider.set(0)
        if hasattr(self, "_music_speed_slider"):
            self._music_speed_slider.set(1.0)

    def _music_cleanup_fx_files(self):
        """清理音效处理产生的临时文件"""
        for fp in self._music_effects_processed_files:
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception:
                pass
        self._music_effects_processed_files.clear()
        self._music_effects.cleanup()

    # ═══════════════ 清理 ═══════════════

    def _music_cleanup(self):
        self._music_stop(instant=True)
        self._stop_lyric_poll()
        self._update_music_footer()
        self._unregister_hotkeys()
        self._music_stop_periodic_save()
        self._save_music_state()
        # 退出前强制写盘
        if hasattr(self, "_music_playlist_manager"):
            self._music_playlist_manager.save()
        self._music_cleanup_temp_files()
        self._music_cleanup_fx_files()
        self._music_destroy_desktop_lyric()

    def _update_music_footer(self):
        if not hasattr(self, "_music_footer_frame"):
            return
        path = self._get_current_file()
        if (path or self._music_is_online_playing) and (self._music_is_playing or self._music_is_paused):
            if self._music_is_online_playing and self._music_current_online_info:
                oi = self._music_current_online_info
                title = oi.name
                artist = oi.singer or ""
                text = title
                if artist:
                    text = f"{title} - {artist}"
            else:
                meta = self._get_metadata(path)
                title = meta.get("title", os.path.basename(path))
                artist = meta.get("artist", "")
                text = title
                if artist:
                    text = f"{title} - {artist}"
            if len(text) > 40:
                text = text[:38] + "..."
            self._music_footer_label.configure(text=text)
            if not self._music_footer_frame.winfo_ismapped():
                self._music_footer_frame.pack(side=ctk.LEFT, expand=True)
                self._music_footer_label.pack(side=ctk.RIGHT, padx=(0, 5))
                self._music_footer_next.pack(side=ctk.RIGHT, padx=1)
                self._music_footer_play.pack(side=ctk.RIGHT, padx=1)
                self._music_footer_prev.pack(side=ctk.RIGHT, padx=1)
            self._music_footer_play.configure(text="⏸" if self._music_is_playing else "▶")
        else:
            try:
                for _w in [
                    self._music_footer_frame,
                    self._music_footer_label,
                    self._music_footer_prev,
                    self._music_footer_play,
                    self._music_footer_next,
                ]:
                    _w.pack_forget()
            except Exception:
                pass

    def _on_footer_music_toggle(self):
        self._music_toggle_play()
        self._update_music_footer()

    def _on_footer_music_prev(self):
        self._music_prev()
        self._update_music_footer()

    def _on_footer_music_next(self):
        self._music_next()
        self._update_music_footer()
