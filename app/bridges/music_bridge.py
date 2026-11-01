"""音乐桥（阶段 3.16）—— QML 上下文属性 ``Music``。

页面：``qml/pages/music/MusicPage.qml``
后端：``fusion_music/``（从 tmp/FusionMusicPlayer 移植的播放核心）
数据：``<base>/data/music/``，旧 ``data/music.json`` 只读迁移。

桥只做生命周期、属性转发、剪贴板、通知与全局热键接线；播放/曲库规则全在
fusion_music.core。首次进入音乐页才建后端（QMediaPlayer 与曲库读取不拖慢启动）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional, Tuple

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QGuiApplication

from services.i18n_service import _

logger = logging.getLogger("app.bridges.music_bridge")

QML_NAME = "Music"

#: 旧界面的 7 组音乐热键（名字统一加 music_ 前缀，避免与监控热键撞名）。
#: 静音改成 Ctrl+Shift+Alt+M：Ctrl+Shift+M 已经被监控悬浮窗占用，两个回调同时
#: 触发会让用户按一次键既静音又开关监控。
MUSIC_HOTKEYS: Tuple[Tuple[str, str, str], ...] = (
    ("music_play_pause", "ctrl+shift+space", "音乐：播放/暂停"),
    ("music_prev", "ctrl+shift+left", "音乐：上一首"),
    ("music_next", "ctrl+shift+right", "音乐：下一首"),
    ("music_stop", "ctrl+shift+down", "音乐：停止"),
    ("music_vol_up", "ctrl+shift+up", "音乐：音量 +5"),
    ("music_vol_down", "ctrl+shift+page down", "音乐：音量 -5"),
    ("music_vol_mute", "ctrl+shift+alt+m", "音乐：静音切换"),
)


class MusicBridge(QObject):
    """上下文属性 Music。"""

    readyChanged = Signal()
    pageChanged = Signal()
    expandedChanged = Signal()
    statusMessage = Signal(str, str)
    noticeRequested = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._host: Any = None
        self._base_dir: Path = Path.cwd()
        self._ready = False
        self._page = "local"
        self._expanded = False
        self._closing = False
        self._last_error = ""
        self._engine: Any = None
        self._hotkeys: Any = None
        self._hotkeys_bound = False

    # ── 装配 ────────────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        self._context = context
        config = getattr(context, "config", None) if context is not None else None
        base = getattr(config, "base_dir", None)
        if base:
            self._base_dir = Path(str(base))
        else:
            self._base_dir = Path.cwd()
        # 测试/探针隔离：不传到产品设置里，只在显式设了环境变量时覆盖数据根。
        override = os.environ.get("FMCL_MUSIC_BASE_DIR")
        if override:
            self._base_dir = Path(override)

    def use_engine(self, engine: Any) -> None:
        self._engine = engine
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        self._hotkeys = registry.get("Hotkeys")

    @Slot()
    def ensureReady(self) -> None:  # noqa: N802 - QML 槽名
        """由音乐页在 Component.onCompleted 调用；重复调用无副作用。"""
        if self._ready or self._closing:
            return
        try:
            from fusion_music.host import MusicHost

            self._host = MusicHost(self._base_dir)
        except Exception as exc:  # noqa: BLE001 - 后端失败只影响音乐页
            self._last_error = str(exc)
            logger.exception("音乐后端初始化失败")
            self.noticeRequested.emit(_("music_backend_failed"), "error")
            return
        self._ready = True
        self._last_error = ""
        self.readyChanged.emit()
        player = self._host.player
        player.errorOccurred.connect(self._on_player_error)
        player.statusMessage.connect(self._on_player_status)
        self._host.library_controller.errorOccurred.connect(self._on_library_error)
        self._host.library_controller.message.connect(self._on_library_message)
        self._register_hotkeys()
        if self._host.migration.get("migrated"):
            counts = self._host.migration.get("counts") or {}
            self.statusMessage.emit(
                _("music_migrated").format(playlists=counts.get("playlists", 0)), "info"
            )

    # ── 属性 ────────────────────────────────────────────────

    @Property(bool, notify=readyChanged)
    def ready(self) -> bool:
        return self._ready

    @Property(QObject, notify=readyChanged)
    def player(self):
        return self._host.player if self._host is not None else None

    @Property(QObject, notify=readyChanged)
    def library(self):
        return self._host.library_controller if self._host is not None else None

    @Property(str, notify=readyChanged)
    def errorText(self) -> str:  # noqa: N802
        return self._last_error

    @Property(str, constant=True)
    def dataDir(self) -> str:  # noqa: N802
        return str(self._base_dir / "data" / "music")

    @Property(str, notify=pageChanged)
    def page(self) -> str:
        return self._page

    @Slot(str)
    def setPage(self, page: str) -> None:  # noqa: N802
        value = str(page or "").strip()
        if value not in ("local", "queue") or value == self._page:
            return
        self._page = value
        self.pageChanged.emit()

    @Property(bool, notify=expandedChanged)
    def expanded(self) -> bool:
        return self._expanded

    @Slot()
    def toggleExpanded(self) -> None:  # noqa: N802
        self.setExpanded(not self._expanded)

    @Slot(bool)
    def setExpanded(self, value: bool) -> None:  # noqa: N802
        value = bool(value)
        if value == self._expanded:
            return
        self._expanded = value
        self.expandedChanged.emit()

    # ── 动作 ────────────────────────────────────────────────

    @Slot(str)
    def copyText(self, text: str) -> None:  # noqa: N802
        value = str(text or "")
        if not value:
            return
        try:
            QGuiApplication.clipboard().setText(value)
            self.statusMessage.emit(_("music_copied"), "info")
        except Exception as exc:  # noqa: BLE001
            logger.warning("写入剪贴板失败: %s", exc)
            self.noticeRequested.emit(_("copy_failed").format(error=exc), "error")

    @Slot()
    def scan(self) -> None:  # noqa: N802
        if self._host is not None:
            self._host.scan()

    @Slot()
    def shutdown(self) -> None:
        self._closing = True
        host, self._host = self._host, None
        if host is not None:
            host.shutdown()
        self._ready = False
        self.readyChanged.emit()

    @Slot()
    def detach(self) -> None:
        self.shutdown()

    # ── 全局热键 ────────────────────────────────────────────

    def _register_hotkeys(self) -> None:
        if self._hotkeys_bound or self._hotkeys is None:
            return
        self._hotkeys_bound = True
        try:
            self._hotkeys.triggered.connect(self._on_hotkey)
        except Exception as exc:  # noqa: BLE001
            logger.warning("连接音乐热键信号失败: %s", exc)
        for name, combo, description in MUSIC_HOTKEYS:
            try:
                self._hotkeys.register(name, combo, description)
            except Exception as exc:  # noqa: BLE001 - 无键盘/无权限时只降级
                logger.warning("注册音乐热键 %s 失败: %s", name, exc)

    @Slot(str)
    def _on_hotkey(self, name: str) -> None:
        if not self._ready or self._host is None:
            return
        player = self._host.player
        action = str(name or "")
        try:
            if action == "music_play_pause":
                player.toggle()
            elif action == "music_prev":
                player.previous()
            elif action == "music_next":
                player.next()
            elif action == "music_stop":
                player.stop()
            elif action == "music_vol_up":
                player.setVolume(min(100, player.volume + 5))
            elif action == "music_vol_down":
                player.setVolume(max(0, player.volume - 5))
            elif action == "music_vol_mute":
                player.toggleMute()
        except Exception as exc:  # noqa: BLE001 - 热键回调不能把主线程打崩
            logger.warning("音乐热键 %s 执行失败: %s", action, exc)

    # ── 后端信号 → 人话 ─────────────────────────────────────

    @Slot(str)
    def _on_player_error(self, detail: str) -> None:
        logger.warning("音乐播放失败: %s", detail)
        self.noticeRequested.emit(_("music_playback_failed"), "error")

    @Slot(str)
    def _on_player_status(self, detail: str) -> None:
        logger.debug("音乐状态: %s", detail)

    @Slot(str)
    def _on_library_error(self, detail: str) -> None:
        logger.warning("音乐库失败: %s", detail)
        self.noticeRequested.emit(_("music_library_failed"), "error")

    @Slot(str)
    def _on_library_message(self, detail: str) -> None:
        logger.debug("音乐库提示: %s", detail)
