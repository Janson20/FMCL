"""ModernApp 音乐播放器 Mixin - 音乐标签页相关方法"""

import json
import os
import platform
import shutil
import sys
import tempfile
import threading
import time
import tkinter.filedialog as filedialog
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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
from ui.music_source import MUSIC_SOURCES, SOURCE_META, search_all
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
        self._music_current_online_info: Optional[OnlineMusicInfo] = None
        self._music_is_online_playing: bool = False
        self._music_current_filepath: Optional[str] = None  # 当前播放的文件路径（本地/在线临时文件）
        self._music_temp_files: List[str] = []  # 缓存的临时文件列表
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
        if self._music_playlist:
            self._rebuild_playlist_ui()
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

        self._music_now_label_top = ctk.CTkLabel(
            panel,
            text=_("music_no_track"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self._music_now_label_top.pack(padx=12, anchor=ctk.W, pady=(0, 2))

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

        self._music_quality_var = ctk.StringVar(value="128k")
        for q_text, q_val in [("128K", "128k"), ("320K", "320k"), ("FLAC", "flac")]:
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
            self._music_mini_title.configure(text=title)
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
        self._music_mini_title.configure(text=title)

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

    def _play_online_file(self, filepath: str, online_info: OnlineMusicInfo, start_pos: float = 0):
        """播放在线缓存的临时文件"""
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
            self._music_record_play_history_online(online_info)
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
            )
            self._music_play_online_url(info)

    def _highlight_playlist_song(self, target_idx: int):
        """高亮歌单歌曲列表中指定索引的行"""
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
            # 在线歌曲优先用 set_pos，无需重载文件
            if self._music_is_online_playing:
                try:
                    mixer.music.set_pos(pos)
                    self._music_progress = pos
                    self._music_seek_offset = pos
                    if was_paused:
                        self._stop_progress_poll()
                    else:
                        self._start_progress_poll()
                    return
                except Exception:
                    pass  # set_pos 不支持该格式，回退到重载
            # 重载文件到指定位置
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

        # 高亮当前选中
        self._highlight_sidebar_selection()

    def _build_sidebar_item(self, playlist_id: Optional[str], text: str, is_active: bool = False) -> dict:
        """构建单个侧边栏条目"""
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

        for w in self._music_playlist_sidebar_widgets:
            frame = w.get("frame")
            if not frame or not frame.winfo_exists():
                continue
            pid = w.get("playlist_id")
            if pid is None:
                # "全部歌曲" — 仅在本地标签页时高亮
                frame.configure(fg_color="transparent")
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

    def _rebuild_playlist_song_list(self, pl: Playlist):
        """渲染歌单中的歌曲列表"""
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
            self._add_playlist_song_row(idx, song)

    def _add_playlist_song_row(self, idx: int, song: "PlaylistSong"):
        """渲染歌单中的单行歌曲"""
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

        # 移除按钮
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
        pl = self._music_playlist_manager.get_current_playlist()
        if pl is None:
            return
        if self._music_playlist_manager.remove_song(pl.id, song_index):
            self._rebuild_playlist_song_list(pl)
            self._rebuild_playlist_sidebar()
            self._save_music_state_later()

    def _music_do_sort(self, mode: str):
        """执行歌单排序"""
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

    def _music_record_play_history_online(self, online_info):
        """记录在线歌曲到播放历史"""
        if not hasattr(self, "_music_playlist_manager"):
            return
        try:
            song = PlaylistSong.from_online_info(online_info)
        except Exception:
            return
        self._music_playlist_manager.record_to_history(song)

    # ── 播放全部按钮 ──

    def _music_play_playlist_all(self):
        """播放当前歌单的所有可播放歌曲（支持本地/在线混合）"""
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
                "music_last_song_idx_in_playlist": self._music_playlist_context_idx if self._music_playlist_context_songs else -1,
            }
            if hasattr(self, "callbacks") and "save_music_state" in self.callbacks:
                self.callbacks["save_music_state"](state)
            # 标记歌单为脏，由后台定时器统一写入磁盘，避免频繁 I/O 卡顿
            if hasattr(self, "_music_playlist_manager"):
                self._music_playlist_manager.mark_dirty()
        except Exception as e:
            logger.debug(f"保存音乐状态失败: {e}")

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
                            s.file_path
                            for s in pl.songs
                            if s.source_type == "local" and os.path.exists(s.file_path)
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
        self._music_search_btn.configure(state="disabled", text="...")
        self._music_search_status.configure(text=_("music_loading_url"))
        threading.Thread(target=self._music_online_search_thread, args=(keyword,), daemon=True).start()

    def _music_online_search_thread(self, keyword: str):
        try:
            source_id = self._music_selected_source
            src = MUSIC_SOURCES.get(source_id)
            if src:
                results = src.search(keyword, page=1, limit=30)
            else:
                results = []
        except Exception as e:
            logger.warning(f"在线搜索失败 [{source_id}]: {e}")
            results = []
        self.after(0, lambda: self._music_rebuild_search_results(results))

    def _music_rebuild_search_results(self, results):
        self._music_search_results = results
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
        count = len(results)
        song_count_key = "music_song_count"
        count_text = _(song_count_key, count=count)
        if count_text == song_count_key:
            count_text = f"{count} 首"
        self._music_search_status.configure(text=count_text if count > 0 else _("music_search_no_results"))
        self._music_search_btn.configure(state="normal", text=_("music_search_btn"))

    def _music_add_search_row(self, idx: int, info: OnlineMusicInfo):
        row = ctk.CTkFrame(self._music_online_scroll, fg_color="transparent", height=32)
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
        quality_badge = " ".join(t.get("type", "") for t in info.types[:2])
        display = f"{name_text} - {info.singer}" if info.singer else name_text
        name_label = ctk.CTkLabel(
            row,
            text=display,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        name_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(5, 5))

        dur_text = _format_time(info.interval) if info.interval else ""
        if dur_text:
            ctk.CTkLabel(
                row,
                text=dur_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["text_secondary"],
                width=40,
            ).pack(side=ctk.RIGHT)

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

        for child in [row, index_label, name_label]:
            child.bind("<Button-1>", lambda e, i=idx: self._music_play_online_from_index(i))
            child.bind("<Double-Button-1>", lambda e, i=idx: self._music_play_online_from_index(i))

        self._music_search_widgets.append({"frame": row, "name_label": name_label, "index": idx})

    def _music_play_online_from_index(self, idx: int):
        if idx < 0 or idx >= len(self._music_search_results):
            return
        self._music_playlist_context_songs = []  # 退出歌单上下文
        self._music_playlist_context_idx = -1
        self._music_play_online_url(self._music_search_results[idx])

    def _music_play_online_url(self, online_info: OnlineMusicInfo):
        """触发在线歌曲播放：获取URL -> 下载到临时文件 -> 播放"""
        self._music_stop(instant=True)
        self._music_search_status.configure(text=_("music_loading_url"))
        quality = self._music_quality_var.get()

        def _fetch_and_play():
            result_path = None
            result_info = online_info
            app = self  # 捕获主应用引用，避免线程间 self 丢失
            tried_sources = [online_info.source]
            for source_id in tried_sources:
                try:
                    src = MUSIC_SOURCES.get(source_id)
                    if not src:
                        continue
                    url = src.get_music_url(online_info, quality)
                    if not url:
                        for fallback_q in ["128k", "320k", "flac"]:
                            if fallback_q != quality:
                                url = src.get_music_url(online_info, fallback_q)
                                if url:
                                    break
                    if not url:
                        logger.warning(f"无法获取播放URL [{source_id}]: {online_info.name}")
                        continue
                    result_path = app._music_download_to_temp(url, online_info.name)
                    if result_path:
                        break
                except Exception as e:
                    logger.warning(f"获取在线URL失败 [{source_id}]: {e}")
                    continue

            app.after(0, lambda tp=result_path, oi=result_info: app._music_on_stream_ready(tp, oi))

        threading.Thread(target=_fetch_and_play, daemon=True).start()

    def _music_on_stream_ready(self, temp_path: Optional[str], online_info: OnlineMusicInfo):
        """流媒体文件下载完成回调"""
        self._music_search_status.configure(text="")
        if temp_path:
            self._play_online_file(temp_path, online_info, 0)
        else:
            self._music_search_status.configure(text=_("music_url_failed"))

    def _music_download_to_temp(self, url: str, name_hint: str = "") -> Optional[str]:
        """下载在线音频流到临时文件"""
        try:
            resp = requests.get(
                url,
                timeout=30,
                stream=True,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            )
            resp.raise_for_status()
            ext = ".mp3"
            content_type = resp.headers.get("Content-Type", "")
            if "flac" in content_type or url.endswith(".flac"):
                ext = ".flac"
            elif "ogg" in content_type or url.endswith(".ogg"):
                ext = ".ogg"
            elif "m4a" in content_type or url.endswith(".m4a"):
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
        """停止搜索加载状态"""
        if hasattr(self, "_music_search_btn") and self._music_search_btn.winfo_exists():
            self._music_search_btn.configure(state="normal", text=_("music_search_btn"))
        if hasattr(self, "_music_search_status") and self._music_search_status.winfo_exists():
            self._music_search_status.configure(text="")

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
