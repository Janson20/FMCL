"""Windows SMTC（系统媒体控制）集成（阶段 1 任务 1.4-A，形态 1：整体搬家）。

`_SMTCController` 原先住在 `ui/app_music.py:456-597`，实测**零 GUI 控件**：它不
import 任何 GUI 栈、不碰控件、不读 Tk 变量，只调用 `winsdk` 的
`SystemMediaTransportControls` 把"正在播放什么/什么状态"喂给 Windows。

## 线程契约（搬走时原样保留，务必看清）

类名里带 `_main` 后缀的四个方法（`_update_now_playing_main` / `_set_status_main` /
`_clear_main`，以及它们的调用者）**假定自己在主线程上执行**：原实现的所有公开入口
首先做 `self._parent.after(0, ...)` 把活儿切回 Tk 主线程，然后才碰 winsdk 的 COM
对象。搬进 `services/` 只是换了个文件，**这个契约一字未改**：
`_parent` 仍然由界面侧 `set_parent(self)` 注入（通常是主窗口），
`after` 仍然由界面控件提供——本模块自己不起线程、不调 `after`，只"要求"调用方
在主线程上调用它。

## 为什么 `_winsdk_available` / `_winsdk_import_error` 住在这里

探测块只有一个（`platform.system() == "windows"` → try import winsdk），
搬到这里之后它成为**唯一真相**：SMTC 自己读它，`services/music_audio.py` 的
`transcode_audio_to_wav` 也 `from services.music_smtc import _winsdk_available`
取一份本模块绑定来读（于是 `services.music_audio._winsdk_available` 是转码
真正读的那个名字，测试补丁点见 `tests/test_music_fallback.py` 顶部说明）。

公开名去掉了前导下划线（`_SMTCController` → `SMTCController`），
`ui/app_music.py` 里保留 `_SMTCController` 别名，旧名不会突然消失。
"""

from __future__ import annotations

import platform
from typing import Dict, Optional

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


class SMTCController:
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


__all__ = ["SMTCController"]
