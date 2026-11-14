"""Windows SMTC（系统媒体控制）集成（阶段 1 任务 1.4-A，形态 1：整体搬家）。

## 为什么用隐藏的静音 MediaPlayer 当 SMTC 提供者

winsdk 的 SystemMediaTransportControls.get_for_current_view() 只对 UWP
CoreWindow 有效；FMCL 是 Win32 桌面程序，实测直接抛
OSError: [WinError -2147023496] 无效的窗口句柄，_initialized 永远是假，
元数据/媒体键/进度全是空操作。

桌面程序的正路是 ISystemMediaTransportControlsInterop::GetForWindow(hwnd)，
但本仓库锁定的 winsdk==1.0.0b10 没有投影这个 interop 接口；为了一个接口
再引一套 WinRT 运行时并不划算。可验证的替代是：创建一个隐藏、静音、循环的
MediaPlayer，用它的 system_media_transport_controls 作为系统会话。实测
Windows 11 26200 上：系统媒体面板能读到标题/歌手，媒体键能触发
button_pressed，update_timeline_properties 能推进度。静音播放器只占一条
静音音频流，不参与 FMCL 的出声链路（出声仍由 QMediaPlayer 负责）。

## 线程契约（搬走时原样保留，务必看清）

类名里带 _main 后缀的方法假定自己在主线程上执行：所有公开入口先做
self._parent.after(0, ...) 把活儿切回 Qt 主线程，然后才碰 winsdk 的 COM
对象。_parent 由界面侧 set_parent(self) 注入（通常是主窗口），after
由界面控件提供——本模块自己不起线程、不调 after，只"要求"调用方在主线程
上调用它。媒体键回调来自 COM 线程，同样经 after 切回主线程。

## 为什么 _winsdk_available / _winsdk_import_error 住在这里

探测块只有一个（platform.system() == "windows" → try import winsdk），
搬到这里之后它成为唯一真相；旧 Tk 的 music_audio 转码链已于 4.2 删除，
QML 侧只通过 MusicBridge 使用本模块。

公开名去掉了前导下划线（_SMTCController → SMTCController），
旧 Tk 界面里的同名别名随 4.2 一起退役。
"""

from __future__ import annotations

import logging
import os
import platform
import struct
import tempfile
import wave
from datetime import timedelta
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

_winsdk_import_error = None
if platform.system().lower() == "windows":
    try:
        from winsdk.windows.foundation import Uri
        from winsdk.windows.media import (
            MediaPlaybackType,
            SystemMediaTransportControlsTimelineProperties,
        )
        from winsdk.windows.media.core import MediaSource
        from winsdk.windows.media.playback import MediaPlayer
        from winsdk.windows.storage.streams import (
            DataWriter,
            InMemoryRandomAccessStream,
            RandomAccessStreamReference,
        )

        _winsdk_available = True
    except ImportError as e:
        _winsdk_import_error = e
        _winsdk_available = False
else:
    _winsdk_available = False
    _winsdk_import_error = "非 Windows 平台"

#: MediaPlaybackStatus 的取值（不要再用旧实现的 4/5 魔法数：4 是 PAUSED）。
_STATUS_STOPPED = 2
_STATUS_PLAYING = 3
_STATUS_PAUSED = 4

#: 隐藏提供者的静音 WAV：1 秒、8 kHz、单声道、16 bit，循环播放。
_SILENCE_SECONDS = 1
_SILENCE_RATE = 8000
_SILENCE_NAME = f"fmcl_smtc_silence_{os.getpid()}.wav"


class SMTCController:
    def __init__(self):
        self._smtc = None
        self._player = None
        self._callbacks: Dict[str, callable] = {}
        self._initialized = False
        self._parent = None
        self._button_token = None
        self._silence_path: Optional[Path] = None
        self._install_attempted = False

    @property
    def available(self) -> bool:
        return _winsdk_available

    def set_parent(self, parent):
        self._parent = parent

    def initialize(self, callbacks: Dict[str, callable]):
        """登记媒体键回调。

        **不在这里建提供者**：隐藏 MediaPlayer 会占一条静音音频流，音乐页只是
        被打开、还没有歌时没必要起它；第一首歌真正开始播放时由
        _update_now_playing_main 懒建。
        """
        self._callbacks = dict(callbacks or {})

    # ── 隐藏提供者 ──────────────────────────────────────────

    def _ensure_silence_file(self) -> Path:
        """写一份 1 秒静音 WAV（临时目录；已存在则复用）。"""
        path = Path(tempfile.gettempdir()) / _SILENCE_NAME
        if path.is_file() and path.stat().st_size > 44:
            self._silence_path = path
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        frames = struct.pack("<h", 0) * (_SILENCE_RATE * _SILENCE_SECONDS)
        with wave.open(str(path), "wb") as fp:
            fp.setnchannels(1)
            fp.setsampwidth(2)
            fp.setframerate(_SILENCE_RATE)
            fp.writeframes(frames)
        self._silence_path = path
        return path

    def _install_handlers_main(self):
        if not self.available or self._player is not None or self._install_attempted:
            return
        self._install_attempted = True
        player = None
        try:
            path = self._ensure_silence_file()
            player = MediaPlayer()
            player.is_looping_enabled = True
            player.volume = 0.0
            player.source = MediaSource.create_from_uri(Uri(path.as_uri()))
            player.play()
            smtc = player.system_media_transport_controls
            if smtc is None:
                raise RuntimeError("MediaPlayer 没有返回 SystemMediaTransportControls")
            token = smtc.add_button_pressed(self._on_button_pressed)
            smtc.display_updater.type = MediaPlaybackType.MUSIC
            smtc.is_enabled = True
            self._player = player
            self._smtc = smtc
            self._button_token = token
            self._initialized = True
        except Exception as exc:  # noqa: BLE001 - SMTC 失败不能影响播放
            logger.warning("SMTC 初始化失败（媒体面板/媒体键将不可用）: %s", exc)
            self._smtc = None
            self._player = None
            self._initialized = False
            self._button_token = None
            if player is not None:
                try:
                    player.close()
                except Exception:  # noqa: BLE001
                    pass

    def _ensure_provider_playing(self) -> None:
        """静音提供者被媒体键暂停后，下一次播放/换歌时把它拉回播放。"""
        player = self._player
        if player is None:
            return
        try:
            session = player.playback_session
            state = int(session.playback_state) if session is not None else 0
            if state != _STATUS_PLAYING:
                player.play()
        except Exception:  # noqa: BLE001 - 拉不起来就保持现状
            pass

    def _on_button_pressed(self, _sender, args):
        try:
            code = int(args.button)
        except Exception:
            return
        action = {
            0: "play",
            1: "pause",
            2: "stop",
            4: "seek_forward",
            5: "seek_back",
            6: "next",
            7: "previous",
        }.get(code, "")
        callback = self._callbacks.get(action) if action else None
        if not callable(callback) or not self._parent:
            return
        try:
            self._parent.after(0, callback)
        except Exception:  # noqa: BLE001
            pass

    # ── 元数据 / 状态 / 进度 ────────────────────────────────

    def update_now_playing(self, title: str, artist: str, album: str, cover_data: Optional[bytes] = None):
        if not self.available or not self._parent:
            return
        self._parent.after(0, lambda: self._update_now_playing_main(title, artist, album, cover_data))

    def _update_now_playing_main(self, title: str, artist: str, album: str, cover_data: Optional[bytes] = None):
        if self._smtc is None:
            self._install_handlers_main()
        smtc = self._smtc
        if smtc is None:
            return
        try:
            import asyncio

            async def _update():
                self._ensure_provider_playing()
                updater = smtc.display_updater
                updater.type = MediaPlaybackType.MUSIC
                props = updater.music_properties
                props.title = title or ""
                props.artist = artist or ""
                props.album_title = album or ""
                if cover_data:
                    try:
                        thumbnail = await self._create_thumbnail_stream(cover_data)
                        if thumbnail:
                            updater.thumbnail = thumbnail
                    except Exception:  # noqa: BLE001 - 封面失败不影响文字
                        pass
                updater.update()
                smtc.playback_status = _STATUS_PLAYING
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
        except Exception:  # noqa: BLE001 - 元数据失败不能影响播放
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
        self._parent.after(0, self._set_status_main, _STATUS_PLAYING)

    def set_paused(self):
        if not self.available or not self._parent:
            return
        self._parent.after(0, self._set_status_main, _STATUS_PAUSED)

    def set_stopped(self):
        if not self.available or not self._parent:
            return
        self._parent.after(0, self._set_status_main, _STATUS_STOPPED)

    def set_progress(self, position_ms: int, duration_ms: int):
        """同步系统媒体面板的进度（K-19；单位毫秒）。

        调用方按秒级节流，避免 QMediaPlayer 每 ~93ms 的 position 信号把 COM
        调用打爆。
        """
        if not self.available or not self._parent:
            return
        try:
            position = max(0, int(position_ms))
            duration = max(0, int(duration_ms))
        except (TypeError, ValueError):
            return
        if duration <= 0:
            return
        self._parent.after(0, self._set_progress_main, position, duration)

    def _set_progress_main(self, position_ms: int, duration_ms: int):
        smtc = self._smtc
        if smtc is None:
            return
        try:
            timeline = SystemMediaTransportControlsTimelineProperties()
            duration = max(0, int(duration_ms))
            timeline.start_time = timedelta(0)
            timeline.end_time = timedelta(milliseconds=duration)
            timeline.position = timedelta(milliseconds=max(0, min(int(position_ms), duration)))
            timeline.min_seek_time = timedelta(0)
            timeline.max_seek_time = timedelta(milliseconds=duration)
            smtc.update_timeline_properties(timeline)
        except Exception:  # noqa: BLE001
            pass

    def dispose(self):
        """移除媒体键监听并关掉隐藏提供者（退出链调用）。"""
        token = self._button_token
        if not self._parent:
            return
        self._button_token = None
        self._parent.after(0, self._remove_handlers_main, token)

    def _remove_handlers_main(self, token):
        smtc, player = self._smtc, self._player
        self._smtc = None
        self._player = None
        self._initialized = False
        self._install_attempted = True
        if smtc is not None:
            try:
                if token is not None:
                    smtc.remove_button_pressed(token)
            except Exception:  # noqa: BLE001
                pass
            try:
                smtc.is_enabled = False
            except Exception:  # noqa: BLE001
                pass
        if player is not None:
            try:
                player.pause()
            except Exception:  # noqa: BLE001
                pass
            try:
                player.close()
            except Exception:  # noqa: BLE001
                pass
        path = self._silence_path
        self._silence_path = None
        if path is not None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    def _set_status_main(self, status: int):
        smtc = self._smtc
        if smtc is None:
            return
        try:
            if status == _STATUS_PLAYING:
                self._ensure_provider_playing()
            smtc.playback_status = status
        except Exception:  # noqa: BLE001
            pass

    def clear(self):
        if not self.available or not self._parent:
            return
        self._parent.after(0, self._clear_main)

    def _clear_main(self):
        smtc = self._smtc
        if smtc is None:
            return
        try:
            smtc.display_updater.clear_all()
            smtc.playback_status = _STATUS_STOPPED
        except Exception:  # noqa: BLE001
            pass


__all__ = ["SMTCController"]
