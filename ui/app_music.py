"""ModernApp 音乐播放器 Mixin - 音乐标签页相关方法"""
import os
import sys
import json
import time
import platform
import threading
import tkinter.filedialog as filedialog
from pathlib import Path
from typing import List, Dict, Optional, Tuple

import customtkinter as ctk
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _

_pygame_import_error = None
try:
    import pygame
    import pygame.mixer as mixer
except ImportError as e:
    _pygame_import_error = e

_mutagen_import_error = None
try:
    from mutagen import File as MutagenFile
    from mutagen.mp3 import MP3
    from mutagen.flac import FLAC
    from mutagen.oggvorbis import OggVorbis
    from mutagen.mp4 import MP4
    from mutagen.id3 import ID3
except ImportError as e:
    _mutagen_import_error = e

_winsdk_import_error = None
if platform.system().lower() == "windows":
    try:
        import asyncio as _asyncio_for_smtc
        from winsdk.windows.media import SystemMediaTransportControls
        from winsdk.windows.media import SystemMediaTransportControlsButton
        from winsdk.windows.media import SystemMediaTransportControlsDisplayUpdater
        from winsdk.windows.media import MediaPlaybackStatus
        from winsdk.windows.storage.streams import RandomAccessStreamReference, InMemoryRandomAccessStream, DataWriter
        _winsdk_available = True
    except ImportError as e:
        _winsdk_import_error = e
        _winsdk_available = False
else:
    _winsdk_available = False
    _winsdk_import_error = "非 Windows 平台"

AUDIO_EXTENSIONS = {'.mp3', '.wav', '.flac', '.ogg', '.m4a', '.aac', '.wma', '.opus', '.aiff'}

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
    "vol_down": "ctrl+shift+pgdn",
    "vol_mute": "ctrl+shift+m",
}

_hotkey_import_error = None
try:
    import keyboard as _keyboard
    _keyboard_available = True
except ImportError as e:
    _keyboard_import_error = e
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

        if ext == '.mp3':
            if hasattr(audio, 'info') and hasattr(audio.info, 'length'):
                result["duration"] = audio.info.length
            if hasattr(audio, 'tags'):
                tags = audio.tags
                if tags:
                    result["title"] = _get_tag(tags, 'TIT2') or result["title"]
                    result["artist"] = _get_tag(tags, 'TPE1') or ""
                    result["album"] = _get_tag(tags, 'TALB') or ""
                    for tag_name in tags.keys():
                        if tag_name.startswith('APIC:'):
                            result["has_cover"] = True
                            result["cover_data"] = tags[tag_name].data
                            break
        elif ext == '.flac':
            flac = FLAC(filepath)
            if hasattr(flac, 'info') and hasattr(flac.info, 'length'):
                result["duration"] = flac.info.length
            if flac.tags:
                result["title"] = flac.tags.get('title', [result["title"]])[0] or result["title"]
                result["artist"] = flac.tags.get('artist', [""])[0]
                result["album"] = flac.tags.get('album', [""])[0]
            if flac.pictures:
                result["has_cover"] = True
                result["cover_data"] = flac.pictures[0].data
        elif ext == '.ogg':
            ogg = OggVorbis(filepath)
            if hasattr(ogg, 'info') and hasattr(ogg.info, 'length'):
                result["duration"] = ogg.info.length
            if ogg.tags:
                result["title"] = ogg.tags.get('title', [result["title"]])[0] or result["title"]
                result["artist"] = ogg.tags.get('artist', [""])[0]
                result["album"] = ogg.tags.get('album', [""])[0]
            for key in ogg:
                if key.startswith('cover') or key.startswith('metadata_block_picture'):
                    result["has_cover"] = True
                    result["cover_data"] = ogg[key][0] if isinstance(ogg[key], list) else ogg[key]
                    break
        elif ext == '.m4a' or ext == '.mp4':
            mp4 = MP4(filepath)
            if hasattr(mp4, 'info') and hasattr(mp4.info, 'length'):
                result["duration"] = mp4.info.length
            if mp4.tags:
                result["title"] = mp4.tags.get('\xa9nam', [result["title"]])[0] or result["title"]
                result["artist"] = mp4.tags.get('\xa9ART', [""])[0]
                result["album"] = mp4.tags.get('\xa9alb', [""])[0]
            if hasattr(mp4, 'covr') and mp4.covr:
                result["has_cover"] = True
                result["cover_data"] = bytes(mp4.covr[0])
        else:
            try:
                if hasattr(audio, 'info') and hasattr(audio.info, 'length'):
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
            return str(frame.text[0]) if hasattr(frame, 'text') else str(frame)
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
        self._music_metadata_cache: Dict[str, dict] = {}
        self._music_mini_mode: bool = False
        self._music_progress_timer_id = None
        self._music_init_done: bool = False
        self._music_hotkeys_registered: bool = False
        self._music_playlist_widgets: List[dict] = []
        self._music_smtc: _SMTCController = _SMTCController()
        self._music_smtc.set_parent(self)

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
                logger.info("pygame mixer 初始化完成")
            except Exception as e:
                logger.error(f"pygame mixer 初始化失败: {e}")
        self._load_music_state()
        if self._music_playlist:
            self._rebuild_playlist_ui()

    def _build_music_tab_content(self):
        self.__init_music()
        self._music_tab_content = ctk.CTkFrame(self.music_tab, fg_color="transparent")
        self._music_tab_content.pack(fill=ctk.BOTH, expand=True)

        self._music_main_frame = ctk.CTkFrame(self._music_tab_content, fg_color="transparent")
        self._music_main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        self._build_music_control_panel()
        self._build_music_playlist_panel()
        self._build_music_now_playing()
        self._build_music_mini_bar()
        self._music_mini_bar.pack_forget()

        self._init_music_lazy()
        self._register_hotkeys()

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
            top_row,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._music_song_count_label.pack(side=ctk.RIGHT, padx=(10, 0))

        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(fill=ctk.X, padx=12, pady=3)

        ctrl_row = ctk.CTkFrame(panel, fg_color="transparent")
        ctrl_row.pack(fill=ctk.X, padx=12, pady=(2, 8))

        play_btns = ctk.CTkFrame(ctrl_row, fg_color="transparent")
        play_btns.pack(side=ctk.LEFT)

        btn_cfg = {"width": 36, "height": 30, "font": ctk.CTkFont(size=14), "fg_color": COLORS["bg_light"], "hover_color": COLORS["accent"]}

        self._music_prev_btn = ctk.CTkButton(play_btns, text="⏮", command=self._music_prev, **btn_cfg)
        self._music_prev_btn.pack(side=ctk.LEFT, padx=2)

        self._music_play_btn = ctk.CTkButton(play_btns, text="▶", command=self._music_toggle_play, **btn_cfg)
        self._music_play_btn.pack(side=ctk.LEFT, padx=2)

        self._music_next_btn = ctk.CTkButton(play_btns, text="⏭", command=self._music_next, **btn_cfg)
        self._music_next_btn.pack(side=ctk.LEFT, padx=2)

        self._music_stop_btn = ctk.CTkButton(play_btns, text="⏹", command=self._music_stop, **btn_cfg)
        self._music_stop_btn.pack(side=ctk.LEFT, padx=2)

        self._music_mode_btn = ctk.CTkButton(
            ctrl_row, text="🔁",
            width=36, height=30,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._music_cycle_mode,
        )
        self._music_mode_btn.pack(side=ctk.LEFT, padx=(10, 0))
        self._update_mode_btn_text()

        vol_frame = ctk.CTkFrame(ctrl_row, fg_color="transparent")
        vol_frame.pack(side=ctk.RIGHT)

        self._music_mute_btn = ctk.CTkButton(
            vol_frame, text="🔊",
            width=30, height=30,
            font=ctk.CTkFont(size=14),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            command=self._music_toggle_mute,
        )
        self._music_mute_btn.pack(side=ctk.LEFT)

        self._music_vol_slider = ctk.CTkSlider(
            vol_frame,
            from_=0, to=100,
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
            progress_frame, text="0:00",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            width=40,
        )
        self._music_cur_label.pack(side=ctk.LEFT)

        self._music_progress_bar = ctk.CTkSlider(
            progress_frame,
            from_=0, to=100,
            command=self._music_seek,
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["accent_hover"],
        )
        self._music_progress_bar.set(0)
        self._music_progress_bar.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=5)

        self._music_end_label = ctk.CTkLabel(
            progress_frame, text="0:00",
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
            panel,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        self._music_now_label_sub.pack(padx=12, anchor=ctk.W, pady=(0, 8))

        self._theme_refs.append((self._music_prev_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_play_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_next_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_stop_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_mode_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))
        self._theme_refs.append((self._music_vol_slider, {"fg_color": "bg_light", "progress_color": "accent",
                                  "button_color": "text_primary", "button_hover_color": "accent_hover"}))
        self._theme_refs.append((self._music_progress_bar, {"fg_color": "bg_light", "progress_color": "accent",
                                  "button_color": "text_primary", "button_hover_color": "accent_hover"}))
        self._theme_refs.append((self._music_now_label_top, {"text_color": "text_primary"}))
        self._theme_refs.append((self._music_now_label_sub, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_cur_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_end_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_folder_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_folder_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_mini_toggle_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))
        self._theme_refs.append((self._music_song_count_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_control_panel, {"fg_color": "card_bg"}))

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
            list_frame,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
        )
        self._music_scroll.pack(fill=ctk.BOTH, expand=True, padx=8, pady=(5, 10))
        self._theme_refs.append((self._music_scroll, {"scrollbar_button_color": "bg_light"}))

    def _build_music_now_playing(self):
        self._music_cover_frame = ctk.CTkFrame(
            self._music_main_frame,
            fg_color=COLORS["card_bg"],
            corner_radius=12,
            width=200,
        )
        self._music_cover_frame.pack(side=ctk.RIGHT, fill=ctk.Y, padx=(10, 0))
        self._music_cover_frame.pack_propagate(False)

        self._music_cover_label = ctk.CTkLabel(
            self._music_cover_frame,
            text="🎵",
            font=ctk.CTkFont(size=60),
            text_color=COLORS["text_secondary"],
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
        self._music_cover_album.pack(pady=(0, 20))
        self._theme_refs.append((self._music_cover_frame, {"fg_color": "card_bg"}))
        self._theme_refs.append((self._music_cover_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_cover_artist, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_cover_album, {"text_color": "text_secondary"}))

    def _build_music_mini_bar(self):
        self._music_mini_bar = ctk.CTkFrame(self._music_tab_content, fg_color=COLORS["card_bg"], corner_radius=8, height=55)
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

        btn_cfg = {"width": 30, "height": 28, "font": ctk.CTkFont(size=12), "fg_color": COLORS["bg_light"], "hover_color": COLORS["accent"]}

        self._music_mini_prev = ctk.CTkButton(inner, text="⏮", command=self._music_prev, **btn_cfg)
        self._music_mini_prev.pack(side=ctk.LEFT, padx=1)

        self._music_mini_play = ctk.CTkButton(inner, text="▶", command=self._music_toggle_play, **btn_cfg)
        self._music_mini_play.pack(side=ctk.LEFT, padx=1)

        self._music_mini_next = ctk.CTkButton(inner, text="⏭", command=self._music_next, **btn_cfg)
        self._music_mini_next.pack(side=ctk.LEFT, padx=1)

        self._music_mini_vol = ctk.CTkSlider(
            inner,
            from_=0, to=100,
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
        self._theme_refs.append((self._music_mini_vol, {"fg_color": "bg_light", "progress_color": "accent",
                                  "button_color": "text_primary", "button_hover_color": "accent_hover"}))

    def _update_mode_btn_text(self):
        mode_texts = {
            PLAY_MODE_SEQUENTIAL: "➡",
            PLAY_MODE_LOOP_LIST: "🔁",
            PLAY_MODE_LOOP_SINGLE: "🔂",
            PLAY_MODE_RANDOM: "🔀",
        }
        if hasattr(self, '_music_mode_btn') and self._music_mode_btn.winfo_exists():
            self._music_mode_btn.configure(text=mode_texts.get(self._music_play_mode, "🔁"))

    def _update_now_playing_info(self):
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

    def _display_cover(self, cover_data: bytes):
        try:
            from PIL import Image, ImageTk
            import io
            image = Image.open(io.BytesIO(cover_data))
            image = image.resize((150, 150), Image.LANCZOS)
            photo = ImageTk.PhotoImage(image)
            self._music_cover_label.configure(image=photo, text="")
            self._music_cover_label._image = photo
        except Exception:
            self._music_cover_label.configure(text="🎵")

    def _get_current_file(self) -> Optional[str]:
        if 0 <= self._music_current_index < len(self._music_playlist):
            return self._music_playlist[self._music_current_index]
        return None

    def _get_metadata(self, filepath: str) -> dict:
        if filepath not in self._music_metadata_cache:
            self._music_metadata_cache[filepath] = _extract_audio_metadata(filepath)
        return self._music_metadata_cache[filepath]

    def _play_file(self, filepath: str, start_pos: float = 0):
        if _pygame_import_error is not None:
            logger.warning("pygame 不可用，无法播放")
            return
        try:
            mixer.music.load(filepath)
            mixer.music.set_volume(self._music_volume)
            mixer.music.play(start=start_pos if start_pos > 0 else 0)
            self._music_is_playing = True
            self._music_is_paused = False
            self._music_seek_offset = start_pos if start_pos > 0 else 0
            self._music_duration = self._get_metadata(filepath).get("duration", 0)
            self._update_play_btn_ui()
            self._update_now_playing_info()
            self._start_progress_poll()
            self._music_smtc.set_playing()
            self._highlight_current_in_list()
        except Exception as e:
            logger.error(f"播放失败: {filepath}: {e}")
            self._music_is_playing = False
            self._update_play_btn_ui()

    def _music_toggle_play(self):
        if not self._music_playlist:
            return
        if not self._music_is_playing and not self._music_is_paused:
            if self._music_current_index < 0:
                self._music_current_index = 0
            self._play_file(self._music_playlist[self._music_current_index], self._music_progress if self._music_progress > 0 else 0)
        elif self._music_is_paused:
            try:
                mixer.music.unpause()
                self._music_is_playing = True
                self._music_is_paused = False
                self._update_play_btn_ui()
                self._start_progress_poll()
                self._music_smtc.set_playing()
            except Exception as e:
                logger.error(f"恢复播放失败: {e}")
        elif self._music_is_playing:
            try:
                mixer.music.pause()
                self._music_is_playing = False
                self._music_is_paused = True
                self._update_play_btn_ui()
                self._stop_progress_poll()
                self._music_smtc.set_paused()
            except Exception as e:
                logger.error(f"暂停失败: {e}")

    def _music_stop(self):
        if _pygame_import_error is not None:
            return
        try:
            mixer.music.stop()
            mixer.music.unload()
        except Exception:
            pass
        self._music_is_playing = False
        self._music_is_paused = False
        self._music_progress = 0
        self._music_seek_offset = 0
        self._stop_progress_poll()
        self._update_play_btn_ui()
        self._music_progress_bar.set(0)
        self._music_cur_label.configure(text="0:00")
        self._music_smtc.set_stopped()

    def _music_prev(self):
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
        if not self._music_playlist or self._music_current_index < 0:
            return
        try:
            pos = (value / 100.0) * self._music_duration if self._music_duration > 0 else 0
            filepath = self._music_playlist[self._music_current_index]
            was_paused = self._music_is_paused
            mixer.music.stop()
            mixer.music.load(filepath)
            mixer.music.set_volume(self._music_volume)
            mixer.music.play(start=pos)
            if was_paused:
                mixer.music.pause()
            self._music_progress = pos
            self._music_seek_offset = pos
            if was_paused:
                self._stop_progress_poll()
            else:
                self._start_progress_poll()
        except Exception:
            pass

    def _music_set_volume(self, value: float):
        self._music_volume = value / 100.0
        if _pygame_import_error is None:
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
            self._music_volume = getattr(self, '_music_vol_before_mute', 0.7)
            self._music_vol_slider.set(int(self._music_volume * 100))
            self._music_mini_vol.set(int(self._music_volume * 100))
        if _pygame_import_error is None:
            try:
                mixer.music.set_volume(self._music_volume)
            except Exception:
                pass
        self._update_mute_btn_ui()

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

    def _music_toggle_mini_mode(self):
        self._music_mini_mode = not self._music_mini_mode
        if self._music_mini_mode:
            self._music_main_frame.pack_forget()
            self._music_mini_bar.pack(fill=ctk.X, padx=15, pady=(0, 15))
            self._music_mini_toggle_btn.configure(text=_("music_expand"))
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
            row, text=str(idx + 1),
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
            row, text=f"{t}{arts}",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        name_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(5, 5))

        if dur_text:
            dur_label = ctk.CTkLabel(
                row, text=dur_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["text_secondary"],
                width=35,
            )
            dur_label.pack(side=ctk.RIGHT)

        for child in [row, index_label, name_label]:
            child.bind("<Button-1>", lambda e, i=idx: self._play_from_index(i))
            child.bind("<Double-Button-1>", lambda e, i=idx: self._play_from_index(i))

        self._music_playlist_widgets.append({
            "frame": row,
            "name_label": name_label,
            "index": idx,
        })

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

    def _register_hotkeys(self):
        if self._music_hotkeys_registered:
            return
        if not _keyboard_available:
            logger.debug("keyboard 库不可用，全局热键已禁用")
            return
        try:
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
            logger.warning(f"注册全局热键失败: {e}")

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
        if hasattr(self, '_music_mini_vol'):
            self._music_mini_vol.set(new_vol)
        if _pygame_import_error is None:
            try:
                mixer.music.set_volume(self._music_volume)
            except Exception:
                pass
        self._update_mute_btn_ui()

    def _save_music_state_later(self):
        self.after(500, self._save_music_state)

    def _save_music_state(self):
        if not hasattr(self, '_music_init_done') or not self._music_init_done:
            return
        try:
            state = {
                "music_last_folder": self._music_last_folder,
                "music_current_index": self._music_current_index,
                "music_progress": self._music_progress,
                "music_volume": self._music_volume,
                "music_play_mode": PLAY_MODE_NAMES.get(self._music_play_mode, "loop_list"),
                "music_mini_mode": self._music_mini_mode,
            }
            if hasattr(self, 'callbacks') and "save_music_state" in self.callbacks:
                self.callbacks["save_music_state"](state)
        except Exception as e:
            logger.debug(f"保存音乐状态失败: {e}")

    def _load_music_state(self, _retry_count: int = 0):
        if not hasattr(self, 'callbacks'):
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

            if hasattr(self, '_music_vol_slider') and self._music_vol_slider.winfo_exists():
                self._music_vol_slider.set(int(self._music_volume * 100))
            if hasattr(self, '_music_mini_vol') and self._music_mini_vol.winfo_exists():
                self._music_mini_vol.set(int(self._music_volume * 100))
            self._update_mute_btn_ui()

            if folder and os.path.isdir(folder):
                self._music_last_folder = folder
                self._music_folder_label.configure(text=os.path.basename(folder) or folder)
                self._music_scan_folder_restore(folder)
        except Exception as e:
            logger.debug(f"加载音乐状态失败: {e}")

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
            if hasattr(self, '_music_mini_toggle_btn') and self._music_mini_toggle_btn.winfo_exists():
                self._music_mini_toggle_btn.configure(text=_("music_expand"))

    def _music_cleanup(self):
        self._music_stop()
        self._update_music_footer()
        self._unregister_hotkeys()
        self._save_music_state()

    def _update_music_footer(self):
        if not hasattr(self, '_music_footer_frame'):
            return
        path = self._get_current_file()
        if path and (self._music_is_playing or self._music_is_paused):
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
                for _w in [self._music_footer_frame, self._music_footer_label,
                            self._music_footer_prev, self._music_footer_play, self._music_footer_next]:
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
