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
import threading
import time
from pathlib import Path
from typing import Any, Optional, Tuple

from PySide6.QtCore import Property, QObject, Qt, QUrl, Signal, Slot
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


class _SmtcParent(QObject):
    """`SMTCController` 的 after(0, ...) 适配器（K-19）。

    winsdk 的媒体键事件在 COM 线程触发；信号发到本对象后按 QueuedConnection
    回到 Qt 主线程。`SMTCController` 只要求 ``after`` 这个口子，不依赖 Qt。
    """

    _invoke = Signal(object)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._invoke.connect(self._run, Qt.QueuedConnection)

    def after(self, _ms: int, fn: Any, *args: Any) -> None:
        self._invoke.emit(lambda: fn(*args) if args else fn())

    @Slot(object)
    def _run(self, fn: Any) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - SMTC 回调失败不能影响播放
            logger.warning("SMTC 主线程回调失败: %s", exc)


class MusicBridge(QObject):
    """上下文属性 Music。"""

    readyChanged = Signal()
    pageChanged = Signal()
    expandedChanged = Signal()
    desktopLyricChanged = Signal()
    baikeOriginalChanged = Signal()
    statusMessage = Signal(str, str)
    noticeRequested = Signal(str, str)
    #: B站风控验证弹窗 / 状态（K-30）。
    biliRiskChanged = Signal()
    biliRiskStatusChanged = Signal()
    biliRiskFailed = Signal(str)
    riskRetryRequested = Signal()

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
        self._smtc: Any = None
        self._smtc_parent: Optional[_SmtcParent] = None
        self._last_smtc_progress_at = 0.0
        self._risk_running = False
        self._risk_visible = False
        self._risk_status = ""
        self._risk_stop: Optional[threading.Event] = None
        self._risk_thread: Optional[threading.Thread] = None
        self.riskRetryRequested.connect(self._on_risk_retry, Qt.QueuedConnection)

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
        player.resolveFailed.connect(self._check_bili_risk)
        player.errorOccurred.connect(self._check_bili_risk)
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
        self._init_smtc()
        self._bind_overlay()
        self._restore_desktop_lyric()
        self._register_hotkeys()
        if self._host.migration.get("migrated"):
            counts = self._host.migration.get("counts") or {}
            self.statusMessage.emit(
                _("music_migrated").format(playlists=counts.get("playlists", 0)), "info"
            )

    # ── SMTC（K-19）────────────────────────────────────────

    def _init_smtc(self) -> None:
        """Windows 系统媒体面板：元数据 / 播放状态 / 进度 / 媒体键。

        控制器本身在 `services/music_smtc.py`；这里只做 Qt 线程适配与信号接线。
        控制器用隐藏的静音 MediaPlayer 提供系统会话（Win32 下
        `get_for_current_view()` 不可用，见 services/music_smtc.py 文件头）；
        winsdk 缺席或会话创建失败时全部降级为空操作。
        """
        if self._smtc is not None or self._host is None:
            return
        try:
            from services.music_smtc import SMTCController
        except Exception as exc:  # noqa: BLE001 - 缺 winsdk 只影响 SMTC
            logger.debug("SMTC 不可用: %s", exc)
            return
        player = self._host.player
        self._smtc_parent = _SmtcParent(self)
        self._smtc = SMTCController()
        self._smtc.set_parent(self._smtc_parent)
        self._smtc.initialize({
            "play": player.play,
            "pause": player.pause,
            "stop": player.stop,
            "next": player.next,
            "previous": player.previous,
            "seek_forward": lambda: self._smtc_seek(10_000),
            "seek_back": lambda: self._smtc_seek(-10_000),
        })
        player.trackChanged.connect(self._sync_smtc_metadata)
        player.trackStarted.connect(self._sync_smtc_metadata)
        player.playingChanged.connect(self._sync_smtc_status)
        player.pausedChanged.connect(self._sync_smtc_status)
        player.positionChanged.connect(self._sync_smtc_progress)
        player.durationChanged.connect(self._sync_smtc_progress)
        self._sync_smtc_metadata()

    def _smtc_seek(self, delta_ms: int) -> None:
        player = self.player
        if player is None or not player.seekable:
            return
        try:
            target = max(0, min(int(player.duration), int(player.position) + int(delta_ms)))
            player.seek(target)
        except Exception as exc:  # noqa: BLE001
            logger.debug("SMTC seek 失败: %s", exc)

    def _smtc_cover_bytes(self) -> Optional[bytes]:
        player = self.player
        if player is None:
            return None
        url = str(player.coverUrl or "")
        if not url or url.startswith(("http://", "https://", "qrc:")):
            return None
        path = QUrl(url).toLocalFile() if url.startswith("file:") else url
        try:
            return Path(path).read_bytes()
        except OSError:
            return None

    def _sync_smtc_metadata(self) -> None:
        if self._smtc is None:
            return
        player = self.player
        title = str(player.title if player is not None else "")
        if not title:
            self._smtc.clear()
            return
        self._smtc.update_now_playing(
            title,
            str(player.artist or ""),
            str(player.album or ""),
            self._smtc_cover_bytes(),
        )
        self._last_smtc_progress_at = 0.0
        self._sync_smtc_status()
        self._sync_smtc_progress()

    def _sync_smtc_status(self) -> None:
        if self._smtc is None:
            return
        player = self.player
        try:
            if player is not None and player.playing:
                self._smtc.set_playing()
            elif player is not None and player.paused:
                self._smtc.set_paused()
            else:
                self._smtc.set_stopped()
        except Exception as exc:  # noqa: BLE001
            logger.debug("SMTC 状态同步失败: %s", exc)

    def _sync_smtc_progress(self) -> None:
        if self._smtc is None:
            return
        player = self.player
        if player is None or int(player.duration) <= 0:
            return
        now = time.monotonic()
        # 秒级节流：QMediaPlayer 的 position 大约 93ms 一条，不能每条都打 COM。
        if now - self._last_smtc_progress_at < 1.0 and int(player.position) < int(player.duration) - 1000:
            return
        try:
            self._smtc.set_progress(int(player.position), int(player.duration))
            self._last_smtc_progress_at = now
        except Exception as exc:  # noqa: BLE001
            logger.debug("SMTC 进度同步失败: %s", exc)

    # ── B站风控（K-30）──────────────────────────────────────

    @Property(bool, notify=biliRiskChanged)
    def biliRiskVisible(self) -> bool:  # noqa: N802
        return self._risk_visible

    @Property(str, notify=biliRiskStatusChanged)
    def biliRiskStatus(self) -> str:  # noqa: N802
        return self._risk_status

    def _check_bili_risk(self, *_args: Any) -> None:
        """B站兜底被风控时，取走 pending risk 并启动浏览器验证。"""
        if self._risk_running or self._host is None:
            return
        try:
            from fusion_music import sources

            risk = sources.take_pending_risk()
        except Exception as exc:  # noqa: BLE001 - 风控检查不能影响播放
            logger.debug("读取 B站风控参数失败: %s", exc)
            return
        if not risk:
            return
        self._risk_running = True
        self._risk_visible = True
        self._risk_status = _("music_risk_hint")
        self.biliRiskChanged.emit()
        self.biliRiskStatusChanged.emit()
        self._risk_thread = threading.Thread(
            target=self._run_bili_risk, args=(risk,), daemon=True, name="bili-risk"
        )
        self._risk_thread.start()

    def _run_bili_risk(self, risk: dict) -> None:
        from fusion_music import sources
        from services.music_risk_captcha import run_captcha_flow

        stop = threading.Event()
        self._risk_stop = stop
        try:
            src = sources.get_source("bili")
            if src is None:
                self.biliRiskFailed.emit(_("music_risk_failed"))
                return
            result = run_captcha_flow(
                str(risk.get("gt") or ""),
                str(risk.get("challenge") or ""),
                stop_event=stop,
            )
            if not result:
                self.biliRiskFailed.emit(_("music_risk_failed"))
                return
            grisk_id = src.validate_risk(
                str(risk.get("token") or ""),
                str(result.get("geetest_challenge") or ""),
                str(result.get("geetest_seccode") or ""),
                str(result.get("geetest_validate") or ""),
            )
            if not grisk_id:
                self.biliRiskFailed.emit(_("music_risk_failed"))
                return
            src.set_gaia_vtoken(str(grisk_id))
            self.riskRetryRequested.emit()
        except Exception as exc:  # noqa: BLE001 - 验证失败不抛到线程外
            logger.warning("B站风控验证失败: %s", exc)
            self.biliRiskFailed.emit(_("music_risk_failed"))
        finally:
            self._risk_running = False
            self._risk_stop = None
            self._risk_visible = False
            self._risk_status = ""
            self.biliRiskChanged.emit()
            self.biliRiskStatusChanged.emit()

    @Slot()
    def cancelBiliRisk(self) -> None:  # noqa: N802
        """用户取消：停本地回调服务；浏览器页面自行关闭。"""
        if self._risk_stop is not None:
            self._risk_stop.set()

    @Slot()
    def _on_risk_retry(self) -> None:
        if self._host is None:
            return
        try:
            self._host.player.retryCurrent()
        except Exception as exc:  # noqa: BLE001
            logger.warning("B站风控通过后重试失败: %s", exc)

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
        if self._risk_stop is not None:
            self._risk_stop.set()
        if self._smtc is not None:
            try:
                self._smtc.clear()
                self._smtc.dispose()
            except Exception as exc:  # noqa: BLE001 - 退出链不许被打断
                logger.debug("SMTC 清理失败: %s", exc)
            self._smtc = None
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
