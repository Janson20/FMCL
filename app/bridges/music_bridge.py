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

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from services.i18n_service import _

logger = logging.getLogger("app.bridges.music_bridge")

QML_NAME = "Music"

#: 原唱错标反馈外链（旧 ui/app_music.py 的 MUSIC_ORIGINAL_FEEDBACK_URL）。
MUSIC_ORIGINAL_FEEDBACK_URL = "https://doc.weixin.qq.com/forms/AKgAhAf7ABQAUoAtgbcAHkCNf0v0B41mf"

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
    desktopLyricChanged = Signal()
    baikeOriginalChanged = Signal()
    statusMessage = Signal(str, str)
    noticeRequested = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._host: Any = None
        self._base_dir: Path = Path.cwd()
        self._ready = False
        self._page = "discover"
        self._expanded = False
        self._closing = False
        self._last_error = ""
        self._engine: Any = None
        self._hotkeys: Any = None
        self._hotkeys_bound = False
        self._overlay: Any = None
        self._overlay_bound = False
        self._syncing_desktop_lyric = False
        self._achievement: Any = None
        self._modes_used: set[str] = set()

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
        self._achievement = None
        try:
            self._achievement = context.try_get("achievement") if context is not None else None
        except Exception as exc:  # noqa: BLE001 - 成就服务缺席不应让音乐页打不开
            logger.warning("取成就服务失败: %s", exc)

    def use_engine(self, engine: Any) -> None:
        self._engine = engine
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        self._hotkeys = registry.get("Hotkeys")
        self._overlay = registry.get("Overlay")

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
        player.resolveFailed.connect(self._on_resolve_failed)
        player.statusMessage.connect(self._on_player_status)
        player.fallbackUsed.connect(self._on_fallback)
        player.trackStarted.connect(self._on_track_started)
        player.modeChanged.connect(self._on_mode_changed)
        self._host.account.renewFinished.connect(self._on_renew_finished)
        self._host.library_controller.errorOccurred.connect(self._on_library_error)
        self._host.library_controller.message.connect(self._on_library_message)
        self._host.search.errorOccurred.connect(self._on_search_error)
        try:
            self._modes_used = set(self._host.config.get("playback.modes_used", []) or [])
        except (TypeError, ValueError):
            self._modes_used = set()
        if {"sequential", "loop_list", "loop_single", "shuffle"}.issubset(self._modes_used):
            self._check_achievement("music_mode_master")
        self._bind_overlay()
        self._restore_desktop_lyric()
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

    @Property(QObject, notify=readyChanged)
    def search(self):
        return self._host.search if self._host is not None else None

    @Property(QObject, notify=readyChanged)
    def discover(self):
        return self._host.discover if self._host is not None else None

    @Property(QObject, notify=readyChanged)
    def account(self):
        return self._host.account if self._host is not None else None

    @Property(bool, notify=desktopLyricChanged)
    def desktopLyricVisible(self) -> bool:  # noqa: N802
        if self._overlay is not None:
            try:
                return bool(self._overlay.lyricVisible)
            except Exception:  # noqa: BLE001 - 窗口还没建好时按配置读
                pass
        if self._host is None:
            return False
        return bool(self._host.config.get("lyrics.desktop_lyric", False))

    @Property(bool, notify=baikeOriginalChanged)
    def baikeOriginal(self) -> bool:  # noqa: N802
        if self._host is None:
            return True
        return bool(self._host.config.get("sources.baike_original", True))

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
        if value not in ("discover", "library", "local", "queue", "online") or value == self._page:
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

    @Slot(str, "QVariant", result=bool)
    def addToPlaylist(self, playlist_id: str, value) -> bool:  # noqa: N802
        """加入歌单；提示走 FMCL 的 i18n，不复用后端的中文 message。"""
        if self._host is None:
            return False
        try:
            added = self._host.library_controller.add_to_playlist(
                str(playlist_id or ""), value, notify=False
            )
        except Exception as exc:  # noqa: BLE001 - 歌单失败不能打崩主线程
            logger.warning("加入歌单失败: %s", exc)
            self.noticeRequested.emit(_("music_library_failed"), "error")
            return False
        self.statusMessage.emit(
            _("music_added_to_playlist") if added else _("music_already_in_playlist"),
            "info",
        )
        return bool(added)

    @Slot(str, result="QVariant")
    def createPlaylist(self, name: str):  # noqa: N802
        """新建歌单；成功返回 ``{id, name, count}``，失败返回 None。"""
        if self._host is None:
            return None
        value = str(name or "").strip()
        if not value:
            self.noticeRequested.emit(_("music_playlist_name_empty"), "error")
            return None
        try:
            playlist = self._host.library_controller.createPlaylist(value)
        except Exception as exc:  # noqa: BLE001 - 歌单失败不能打崩主线程
            logger.warning("新建歌单失败: %s", exc)
            self.noticeRequested.emit(_("music_library_failed"), "error")
            return None
        if playlist:
            self.statusMessage.emit(_("music_playlist_created").format(name=value), "info")
        return playlist

    @Slot(str, str, result=bool)
    def renamePlaylist(self, playlist_id: str, new_name: str) -> bool:  # noqa: N802
        if self._host is None:
            return False
        value = str(new_name or "").strip()
        if not value:
            self.noticeRequested.emit(_("music_playlist_name_empty"), "error")
            return False
        try:
            ok = self._host.library_controller.renamePlaylist(str(playlist_id or ""), value)
        except Exception as exc:  # noqa: BLE001
            logger.warning("重命名歌单失败: %s", exc)
            ok = False
        self.statusMessage.emit(
            _("music_playlist_renamed") if ok else _("music_system_playlist_readonly"),
            "info" if ok else "error",
        )
        return bool(ok)

    @Slot(str, result=bool)
    def deletePlaylist(self, playlist_id: str) -> bool:  # noqa: N802
        if self._host is None:
            return False
        try:
            ok = self._host.library_controller.deletePlaylist(str(playlist_id or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("删除歌单失败: %s", exc)
            ok = False
        self.statusMessage.emit(
            _("music_playlist_deleted") if ok else _("music_system_playlist_readonly"),
            "info" if ok else "error",
        )
        return bool(ok)

    @Slot(str, str, result=bool)
    def clearPlaylist(self, playlist_id: str) -> bool:  # noqa: N802
        if self._host is None:
            return False
        try:
            ok = self._host.library_controller.clearPlaylist(str(playlist_id or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("清空歌单失败: %s", exc)
            ok = False
        if ok:
            self.statusMessage.emit(_("music_playlist_cleared"), "info")
        return bool(ok)

    @Slot(str, "QVariant", result=bool)
    def removeFromPlaylist(self, playlist_id: str, value) -> bool:  # noqa: N802
        if self._host is None:
            return False
        try:
            ok = self._host.library_controller.removeTrack(str(playlist_id or ""), value)
        except Exception as exc:  # noqa: BLE001
            logger.warning("从歌单移除失败: %s", exc)
            ok = False
        if ok:
            self.statusMessage.emit(_("music_removed_from_playlist"), "info")
        return bool(ok)

    @Slot(str, str)
    def toggleSort(self, playlist_id: str, field: str) -> None:  # noqa: N802
        if self._host is None:
            return
        try:
            self._host.library_controller.toggleSort(str(field or ""))
        except Exception as exc:  # noqa: BLE001
            logger.warning("歌单排序失败: %s", exc)

    @Slot()
    def syncPlaylists(self) -> None:  # noqa: N802
        if self._host is None:
            return
        if not self._host.account.loggedIn:
            self.noticeRequested.emit(_("settings_wy_not_logged_in"), "error")
            return
        self._host.account.syncPlaylists()

    @Slot()
    def toggleDesktopLyric(self) -> None:  # noqa: N802
        """播放栏的桌面歌词开关；可见性变化由 Overlay 信号回写配置。"""
        if self._overlay is None:
            return
        try:
            self._overlay.toggleLyric()
        except Exception as exc:  # noqa: BLE001 - 悬浮窗失败不影响播放
            logger.warning("切换桌面歌词失败: %s", exc)
            self.noticeRequested.emit(_("music_desktop_lyric_failed"), "error")

    @Slot(bool)
    def setBaikeOriginal(self, enabled: bool) -> None:  # noqa: N802
        """设置页与在线搜索共用的百度百科原唱兜底开关。"""
        if self._host is None:
            return
        value = bool(enabled)
        self._host.config.set("sources.baike_original", value)
        self._host.config.save()
        try:
            self._host.apply_baike_original()
        except Exception as exc:  # noqa: BLE001
            logger.warning("同步百度百科原唱开关失败: %s", exc)
        self.baikeOriginalChanged.emit()

    @Slot()
    def openOriginalFeedback(self) -> None:  # noqa: N802
        """打开原唱错标/漏标反馈表单（旧界面同一个 URL）。"""
        try:
            QDesktopServices.openUrl(QUrl(MUSIC_ORIGINAL_FEEDBACK_URL))
        except Exception as exc:  # noqa: BLE001
            logger.warning("打开原唱反馈链接失败: %s", exc)
            self.noticeRequested.emit(_("music_original_feedback_failed"), "error")

    @Slot()
    def noteVolumeAdjustment(self) -> None:  # noqa: N802
        """由音量滑块释放/热键/静音调用；同一次拖动只记一次。"""
        self._trigger_achievement("music_volume_tweaker")

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

    # ── 桌面歌词 / 百度百科 / 成就 ──────────────────────────

    def _bind_overlay(self) -> None:
        if self._overlay is None or self._overlay_bound:
            return
        try:
            self._overlay.lyricVisibleChanged.connect(self._on_desktop_lyric_visibility_changed)
            self._overlay_bound = True
        except Exception as exc:  # noqa: BLE001 - 悬浮窗缺席时只影响这一个开关
            logger.warning("连接桌面歌词可见性失败: %s", exc)

    def _restore_desktop_lyric(self) -> None:
        if self._overlay is None or self._host is None:
            return
        want = bool(self._host.config.get("lyrics.desktop_lyric", False))
        if not want:
            return
        self._syncing_desktop_lyric = True
        try:
            self._overlay.showLyric()
        except Exception as exc:  # noqa: BLE001
            logger.warning("恢复桌面歌词失败: %s", exc)
        finally:
            self._syncing_desktop_lyric = False
        self.desktopLyricChanged.emit()

    @Slot()
    def _on_desktop_lyric_visibility_changed(self) -> None:
        if self._syncing_desktop_lyric or self._host is None:
            return
        try:
            visible = bool(self._overlay.lyricVisible) if self._overlay is not None else False
            self._host.config.set("lyrics.desktop_lyric", visible)
            self._host.config.save()
        except Exception as exc:  # noqa: BLE001 - 配置坏掉不该影响窗口
            logger.warning("保存桌面歌词可见性失败: %s", exc)
        self.desktopLyricChanged.emit()

    @Slot()
    def _on_track_started(self) -> None:
        self._trigger_achievement("music_first_play")
        self._trigger_achievement("music_play_count")

    @Slot()
    def _on_mode_changed(self) -> None:
        if self._host is None:
            return
        try:
            name = str(self._host.player.modeName or "")
        except Exception:  # noqa: BLE001
            return
        modes = set(self._modes_used)
        modes.add(name)
        if modes == self._modes_used:
            return
        self._modes_used = modes
        try:
            self._host.config.set("playback.modes_used", sorted(modes))
            self._host.config.save()
        except Exception as exc:  # noqa: BLE001
            logger.warning("保存播放模式历史失败: %s", exc)
        if {"sequential", "loop_list", "loop_single", "shuffle"}.issubset(modes):
            self._check_achievement("music_mode_master")

    @Slot(bool)
    def _on_renew_finished(self, ok: bool) -> None:
        key = "settings_wy_renew_success" if ok else "settings_wy_renew_failed"
        self.statusMessage.emit(_(key), "success" if ok else "error")

    def _achievement_engine(self) -> Any:
        service = self._achievement
        if service is None:
            return None
        try:
            getter = getattr(service, "engine", None)
            return getter() if callable(getter) else None
        except Exception as exc:  # noqa: BLE001 - 成就失败不影响播放
            logger.warning("取成就引擎失败: %s", exc)
            return None

    def _trigger_achievement(self, achievement_id: str, value: int = 1) -> bool:
        engine = self._achievement_engine()
        if engine is None:
            return False
        try:
            engine.update_progress(achievement_id, value=value, trigger_type="increment")
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("触发成就 %s 失败: %s", achievement_id, exc)
            return False

    def _check_achievement(self, achievement_id: str) -> bool:
        engine = self._achievement_engine()
        if engine is None:
            return False
        try:
            engine.check_and_unlock(achievement_id, True)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("检查成就 %s 失败: %s", achievement_id, exc)
            return False

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
                self.noteVolumeAdjustment()
            elif action == "music_vol_down":
                player.setVolume(max(0, player.volume - 5))
                self.noteVolumeAdjustment()
            elif action == "music_vol_mute":
                player.toggleMute()
                self.noteVolumeAdjustment()
        except Exception as exc:  # noqa: BLE001 - 热键回调不能把主线程打崩
            logger.warning("音乐热键 %s 执行失败: %s", action, exc)

    # ── 后端信号 → 人话 ─────────────────────────────────────

    @Slot(str)
    def _on_player_error(self, detail: str) -> None:
        logger.warning("音乐播放失败: %s", detail)
        self.noticeRequested.emit(_("music_playback_failed"), "error")

    @Slot(str)
    def _on_resolve_failed(self, name: str) -> None:
        logger.warning("音乐取流失败: %s", name)
        self.noticeRequested.emit(_("music_url_failed"), "error")

    @Slot(str)
    def _on_fallback(self, source_id: str) -> None:
        """跨源兜底成功后提示「实际从哪个音源播的」。"""
        source = _("music_source_" + str(source_id or ""))
        if not source or source == "music_source_" + str(source_id or ""):
            source = str(source_id or "")
        self.statusMessage.emit(_("music_fallback_status").format(source=source), "info")

    @Slot(str)
    def _on_search_error(self, code: str) -> None:
        """搜索控制器只发机器码；翻译与「无结果不弹错」在这里决定。"""
        if code == "no_results":
            return
        key = "music_search_enter_keyword" if code == "empty_keyword" else "music_search_failed"
        self.noticeRequested.emit(_(key), "error")

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
