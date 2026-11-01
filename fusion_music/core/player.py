"""播放引擎。

替代 FMCL 的 ``pygame.mixer`` 后端（它必须先整首下载才能播放、seek 需要 reload、
没有 gapless）。这里用 ``QMediaPlayer`` + ``QAudioOutput`` 直接流式播放，
并保留 FMCL 的几条关键行为语义：

* 淡入淡出（20 步 × 50 ms ≈ 1 秒）
* 请求序号守卫，防止旧请求覆盖新播放
* 跨源兜底 + 失败提示
* 播放历史记录的是**用户点播的原曲**，不是兜底替换后的曲目
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from PySide6.QtCore import (
    QObject,
    QTimer,
    QUrl,
    Property,
    Signal,
    Slot,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

from . import cache
from . import session as play_session
from .lyrics import Lyrics
from .models import Track, format_duration, quality_short
from .queue import PlayMode, mode_from_name, PLAY_MODE_LABELS, PLAY_MODE_NAMES
from .trackutil import as_track
from .resolver import Resolved, resolve
from .store import Library
from ..config import Config

logger = logging.getLogger(__name__)

FADE_STEPS = 20
FADE_INTERVAL_MS = 50

#: 音量均衡增益的斜坡：分析结果可能在播放中途才到，直接跳变会有可闻的响度台阶
GAIN_RAMP_STEPS = 10
GAIN_RAMP_INTERVAL_MS = 40

#: 队列 / 曲目变化后隔多久把会话写盘（合并「播放全部」那种连着改好几次的操作）
SESSION_SAVE_DELAY_MS = 1500
#: 播放中每隔多久记一次进度。这只是掉电 / 被强杀的兜底：正常退出走
#: shutdown()，进度是准的。间隔没敢定太短 —— 会话是**整份重写**的，
#: 上千首的队列一次要写近 1MB / 上百毫秒，一分钟一次已经是能接受的上限。
SESSION_PROGRESS_MS = 60000

class _Emitter(QObject):
    """把工作线程的结果投递回主线程（Qt 会自动排队到接收者线程）。"""

    resolved = Signal(int, object)
    lyricReady = Signal(int, object)
    #: 预取分析用的解析结果（track, 播放地址, 是否本地文件）
    analysisReady = Signal(object, str, bool)

class PlayerEngine(QObject):
    """播放引擎，直接暴露给 QML。"""

    # ── 信号 ────────────────────────────────────────────────
    trackChanged = Signal()
    positionChanged = Signal()
    durationChanged = Signal()
    playingChanged = Signal()
    pausedChanged = Signal()
    modeChanged = Signal()
    volumeChanged = Signal()
    mutedChanged = Signal()
    queueChanged = Signal()
    favoriteChanged = Signal()
    favoriteStateChanged = Signal()
    lyricChanged = Signal()
    lyricIndexChanged = Signal()
    #: 当前行「唱到第几个字」（可带小数，见 Lyrics.char_progress）——逐字填充用
    lyricProgressChanged = Signal()
    loadingChanged = Signal()
    errorOccurred = Signal(str)
    statusMessage = Signal(str)
    fallbackUsed = Signal(str)
    seekableChanged = Signal()
    #: 音量均衡增益变化（曲目切换、或后台分析出结果之后）
    gainChanged = Signal()

    def __init__(self, config: Config, library: Library, loudness=None, parent=None):
        super().__init__(parent)
        self._config = config
        self._library = library
        #: 音量均衡服务（可为 None：测试或关闭该功能时全部按 0 dB 处理）
        self._loudness = loudness

        self._player = QMediaPlayer(self)
        self._output = QAudioOutput(self)
        self._player.setAudioOutput(self._output)

        self._queue = None  # 延迟导入避免循环
        from .queue import PlayQueue

        self._queue = PlayQueue(on_changed=self._on_queue_changed)

        self._current: Optional[Track] = None
        self._requested: Optional[Track] = None
        self._current_quality: str = "320k"
        self._fallback_source: str = ""
        # 媒体真的交给 QMediaPlayer 了吗。会话恢复出来的曲目只是「摆在播放栏上」，
        # 这时 _player 手上还没有任何 source，play() 不会出声（见 toggle()）
        self._media_ready = False
        # 会话恢复出来的进度，只对恢复的那一首生效（见 _take_resume_ms）
        self._resume_ms = 0
        self._resume_uid = ""

        self._playing = False
        self._paused = False
        self._loading = False
        self._seekable = False

        self._duration_ms = 0
        self._position_ms = 0
        self._pending_seek_ms: Optional[int] = None

        self._lyrics = Lyrics()
        self._lyric_index = -1
        self._lyric_progress = 0.0
        self._lyric_translation = ""
        self._lyric_roma = ""

        self._seq = 0
        self._emitter = _Emitter(self)
        self._emitter.resolved.connect(self._on_resolved)
        self._emitter.lyricReady.connect(self._on_lyric_ready)

        self._fade_timer = QTimer(self)
        self._fade_timer.setInterval(FADE_INTERVAL_MS)
        self._fade_step = 0
        self._fade_dir = 0
        self._fade_timer.timeout.connect(self._fade_tick)

        # ── 音量合成 ────────────────────────────────────────
        # 输出音量 = 用户音量 × 淡入淡出系数 × 音量均衡增益。
        # 三个因子各管各的：滑块只动第一个，淡入淡出只动第二个，
        # 均衡只动第三个 —— 于是「分析出结果时正在淡入」这类交叠也不会打架。
        self._user_volume = self._read_user_volume(config)
        self._fade_factor = 1.0
        self._gain_ratio = 1.0
        self._gain_target = 1.0
        self._gain_from = 1.0
        self._gain_step = 0
        self._gain_timer = QTimer(self)
        self._gain_timer.setInterval(GAIN_RAMP_INTERVAL_MS)
        self._gain_timer.timeout.connect(self._gain_tick)

        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(200)
        self._progress_timer.timeout.connect(self._poll_progress)

        # 会话落盘：队列 / 曲目 / 播放模式一变就重新计时，静下来 1.5 秒才写一次
        # （「播放全部」会连着改好几次队列，每次都写盘毫无意义）。
        self._session_timer = QTimer(self)
        self._session_timer.setSingleShot(True)
        self._session_timer.setInterval(SESSION_SAVE_DELAY_MS)
        self._session_timer.timeout.connect(self.saveSession)
        self._session_ticks = 0

        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_playback_state)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.seekableChanged.connect(self._on_seekable_changed)
        self._player.errorOccurred.connect(self._on_error)

        self._output.setMuted(bool(config.get("playback.muted", False)))
        self._apply_output_volume()
        self._queue.mode = mode_from_name(config.get("playback.mode", "loop_list"))

        if self._loudness is not None:
            # 后台分析出结果后，如果正是当前这首，就把增益平滑接上去
            self._loudness.measured.connect(self._on_loudness_measured)
            self._emitter.analysisReady.connect(self._on_analysis_ready)

    @staticmethod
    def _read_user_volume(config: Config) -> int:
        try:
            return max(0, min(100, int(config.get("playback.volume", 65) or 0)))
        except (TypeError, ValueError):
            return 65

    # ── QML 属性 ────────────────────────────────────────────

    @Property(QObject, constant=True)
    def queue(self):
        return self._queue

    @Property("QVariant", notify=trackChanged)
    def currentTrack(self):  # noqa: N802
        return self._current.to_dict() if self._current else None

    @Property(str, notify=trackChanged)
    def title(self) -> str:
        return self._current.name if self._current else ""

    @Property(str, notify=trackChanged)
    def artist(self) -> str:
        return self._current.singer if self._current else ""

    @Property(str, notify=trackChanged)
    def album(self) -> str:
        return self._current.album if self._current else ""

    @Property(str, notify=trackChanged)
    def albumId(self) -> str:  # noqa: N802
        return self._current.album_id if self._current else ""

    @Property(str, notify=trackChanged)
    def coverUrl(self) -> str:  # noqa: N802
        if not self._current or not self._current.cover:
            return ""
        try:
            return cache.cached_cover(self._current.cover) or ""
        except Exception:
            return ""

    @Property(str, notify=trackChanged)
    def sourceLabel(self) -> str:  # noqa: N802
        if not self._current:
            return ""
        if self._fallback_source:
            from ..sources import SOURCE_NAMES

            return f"已切换音源 · {SOURCE_NAMES.get(self._fallback_source, self._fallback_source)}"
        return self._current.source_text

    @Property(str, notify=trackChanged)
    def qualityLabel(self) -> str:  # noqa: N802
        """实际播放档位的短名（与列表角标同一张表，见 models.QUALITY_SHORT_LABELS）。"""
        return quality_short(self._current_quality)

    @Property(int, notify=positionChanged)
    def position(self) -> int:
        return self._position_ms

    @Property(int, notify=durationChanged)
    def duration(self) -> int:
        return self._duration_ms

    @Property(str, notify=positionChanged)
    def positionText(self) -> str:  # noqa: N802
        return format_duration(self._position_ms / 1000)

    @Property(str, notify=durationChanged)
    def durationText(self) -> str:  # noqa: N802
        return format_duration(self._duration_ms / 1000)

    @Property(bool, notify=playingChanged)
    def playing(self) -> bool:
        return self._playing

    @Property(bool, notify=pausedChanged)
    def paused(self) -> bool:
        return self._paused

    @Property(bool, notify=loadingChanged)
    def loading(self) -> bool:
        return self._loading

    @Property(bool, notify=seekableChanged)
    def seekable(self) -> bool:
        return self._seekable

    @Property(int, notify=volumeChanged)
    def volume(self) -> int:
        """**用户**音量（0~100）。

        注意不是 ``QAudioOutput`` 上的实际音量：那个值还被淡入淡出与
        音量均衡乘过，拿它当滑块位置会让界面自己乱跳。
        """
        return int(self._user_volume)

    @Property(str, notify=gainChanged)
    def gainLabel(self) -> str:  # noqa: N802
        """当前曲目的均衡增益说明（如 ``-3.5 dB（实测 · 响度 -10.5 LUFS · 全曲）``）。"""
        if self._loudness is None or self._current is None:
            return ""
        try:
            return str(self._loudness.describe(self._current) or "")
        except Exception:
            return ""

    @Property(bool, notify=mutedChanged)
    def muted(self) -> bool:
        return bool(self._output.isMuted())

    @Property(int, notify=modeChanged)
    def mode(self) -> int:
        return int(self._queue.mode)

    @Property(str, notify=modeChanged)
    def modeLabel(self) -> str:  # noqa: N802
        return PLAY_MODE_LABELS.get(self._queue.mode, "")

    @Property(str, notify=modeChanged)
    def modeName(self) -> str:  # noqa: N802
        return PLAY_MODE_NAMES.get(self._queue.mode, "loop_list")

    @Property(bool, notify=favoriteStateChanged)
    def isFavorite(self) -> bool:  # noqa: N802
        if not self._current:
            return False
        try:
            return self._library.is_favorite(self._current)
        except Exception:
            return False

    @Property("QVariantList", notify=lyricChanged)
    def lyricLines(self):  # noqa: N802
        return self._lyrics.to_dicts()

    @Property(bool, notify=lyricChanged)
    def hasLyrics(self) -> bool:  # noqa: N802
        return not self._lyrics.is_empty

    @Property(int, notify=lyricIndexChanged)
    def lyricIndex(self) -> int:  # noqa: N802
        return self._lyric_index

    @Property(float, notify=lyricProgressChanged)
    def lyricProgress(self) -> float:  # noqa: N802
        """当前行「唱到第几个字」——整数部分是已唱完的字数，小数部分是当前字唱了多少。

        界面拿它在「已唱色 / 未唱色」之间插值（见 NowPlayingPanel 的逐字填充）。
        没有逐字数据的行恒为 0，界面那边据此回落到整行高亮。
        """
        return self._lyric_progress

    # ── 队列镜像（供 QML 显示）─────────────────────────────

    @Property("QVariantList", notify=queueChanged)
    def queueItems(self):  # noqa: N802
        return [t.to_dict() for t in self._queue.tracks()]

    @Property(int, notify=queueChanged)
    def queueIndex(self) -> int:  # noqa: N802
        return self._queue.index

    @Property(int, notify=queueChanged)
    def queueCount(self) -> int:  # noqa: N802
        return self._queue.size

    # ── 播放控制 ────────────────────────────────────────────

    @Slot("QVariant")
    def playTrack(self, value):  # noqa: N802
        """播放单曲（不入队）：用「单曲」上下文替换队列。"""
        track = as_track(value)
        if track is None:
            return
        self._queue.set_tracks([track], 0)
        self._load_current(record_history=True)

    @Slot("QVariant", int)
    def playTrackInList(self, value, index: int):  # noqa: N802
        """以给定列表为上下文，从 ``index`` 开始播放。"""
        tracks = [as_track(t) for t in (value or [])]
        tracks = [t for t in tracks if t is not None]
        if not tracks:
            return
        self._queue.set_tracks(tracks, max(0, min(int(index), len(tracks) - 1)))
        self._load_current(record_history=True)

    @Slot("QVariant")
    def replaceQueue(self, value):  # noqa: N802
        """替换队列但**不**立即播放（用于「播放全部」前的准备）。"""
        tracks = [as_track(t) for t in (value or [])]
        self._queue.set_tracks([t for t in tracks if t is not None], 0)

    @Slot(int, "QVariant")
    def playQueueIndex(self, index: int, tracks=None):  # noqa: N802
        if tracks is not None:
            self.replaceQueue(tracks)
        if self._queue.set_index(int(index)):
            self._load_current(record_history=True)

    @Slot()
    def toggle(self) -> None:
        if self._current is None or not self._media_ready:
            # 恢复会话摆回来的曲目还没有媒体：这时 _player.play() 什么也不会发生，
            # 得走一次正常的加载（它会把恢复的进度也一起带上）
            if self._queue.size:
                self._load_current(record_history=True)
            else:
                self.errorOccurred.emit("播放列表为空")
            return
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._pause_with_fade()
        elif self._player.playbackState() == QMediaPlayer.PausedState:
            self._resume()
        else:
            self._player.play()
            self._fade_in()

    @Slot()
    def play(self) -> None:
        if self._current is None or not self._media_ready:
            if self._queue.size:
                self._load_current(record_history=True)
            return
        self._player.play()
        self._fade_in()

    @Slot()
    def pause(self) -> None:
        self._pause_with_fade()

    @Slot()
    def stop(self) -> None:
        self._fade_timer.stop()
        self._player.stop()
        self._progress_timer.stop()
        self._position_ms = 0
        self._playing = False
        self._paused = False
        self._fade_factor = 1.0
        self._apply_output_volume()
        self.positionChanged.emit()
        self.playingChanged.emit()
        self.pausedChanged.emit()

    @Slot()
    def next(self) -> None:
        idx = self._queue.next_index(manual=True)
        if idx is None:
            return
        self._queue.set_index(idx)
        self._load_current(record_history=True)

    @Slot()
    def previous(self) -> None:
        # 播放超过 5 秒时先回到本曲开头（与主流播放器一致）
        if self._position_ms > 5000 and self._seekable:
            self.seek(0)
            return
        idx = self._queue.prev_index()
        if idx is None:
            return
        self._queue.set_index(idx)
        self._load_current(record_history=True)

    @Slot(int)
    def seek(self, ms: int) -> None:
        ms = max(0, int(ms))
        if not self._seek_ready():
            # 媒体还没就绪（比如刚恢复的会话）：这时 setPosition() 会被后端静默
            # 丢掉，先记下来，等就绪了再跳（见 _apply_pending_seek）
            self._pending_seek_ms = ms
            # 用户自己拖过了，恢复出来的进度就此作废 —— 否则一按播放，
            # 他刚拖到的位置会被「上次听到哪儿」顶掉
            self._resume_uid = ""
            self._resume_ms = 0
            return
        self._player.setPosition(ms)
        self._position_ms = ms
        self.positionChanged.emit()

    @Slot(float)
    def seekRatio(self, ratio: float) -> None:  # noqa: N802
        if self._duration_ms > 0:
            self.seek(int(max(0.0, min(1.0, float(ratio))) * self._duration_ms))

    @Slot(int)
    def setVolume(self, value: int) -> None:  # noqa: N802
        v = max(0, min(100, int(value)))
        self._user_volume = v
        # 不打断正在进行的淡入淡出：滑块改的是「用户音量」这一项，
        # 淡入淡出乘的是另一项，两者本来就可以同时生效
        self._apply_output_volume()
        if v > 0 and self._output.isMuted():
            self._output.setMuted(False)
            self.mutedChanged.emit()
        self._config.set("playback.volume", v)
        self.volumeChanged.emit()

    @Slot()
    def toggleMute(self) -> None:  # noqa: N802
        muted = not self._output.isMuted()
        self._output.setMuted(muted)
        self._config.set("playback.muted", muted)
        self.mutedChanged.emit()

    @Slot()
    def cycleMode(self) -> None:  # noqa: N802
        order = [PlayMode.ORDER, PlayMode.LOOP_LIST, PlayMode.LOOP_SINGLE, PlayMode.SHUFFLE]
        cur = self._queue.mode
        nxt = order[(order.index(cur) + 1) % len(order)] if cur in order else PlayMode.LOOP_LIST
        self.setMode(int(nxt))

    @Slot(int)
    def setMode(self, value: int) -> None:  # noqa: N802
        try:
            mode = PlayMode(int(value))
        except (ValueError, TypeError):
            mode = PlayMode.LOOP_LIST
        self._queue.mode = mode
        self._config.set("playback.mode", PLAY_MODE_NAMES.get(mode, "loop_list"))
        self.modeChanged.emit()

    @Slot("QVariant")
    def playNextTrack(self, value) -> None:  # noqa: N802
        """把曲目插到「下一首播放」。"""
        track = as_track(value)
        if track is None or not track.name:
            return
        index = self._queue.insert_next(track)
        self.statusMessage.emit(f"「{track.name}」将在下一首播放")
        if self._current is None:
            self._queue.set_index(index)
            self._load_current(record_history=True)

    @Slot("QVariant")
    def appendToQueue(self, value) -> None:  # noqa: N802
        """把曲目追加到队列末尾。"""
        track = as_track(value)
        if track is None or not track.name:
            return
        index = self._queue.append(track)
        self.statusMessage.emit(f"已添加「{track.name}」到播放队列")
        if self._current is None:
            self._queue.set_index(index)
            self._load_current(record_history=True)

    @Slot("QVariant")
    def extendQueue(self, items) -> None:  # noqa: N802
        """批量追加到队列末尾。"""
        tracks = [as_track(t) for t in (items or [])]
        tracks = [t for t in tracks if t is not None and t.name]
        if not tracks:
            return
        self._queue.extend(tracks)
        self.statusMessage.emit(f"已添加 {len(tracks)} 首到播放队列")
        if self._current is None:
            self._queue.set_index(0)
            self._load_current(record_history=True)

    @Slot(int)
    def removeQueueIndex(self, index: int) -> None:  # noqa: N802
        self._queue.remove_at(int(index))

    @Slot(int)
    def playQueueAt(self, index: int) -> None:  # noqa: N802
        if self._queue.set_index(int(index)):
            self._load_current(record_history=True)

    @Slot()
    def clearQueue(self) -> None:
        self.stop()
        self._queue.clear()
        self._current = None
        self._requested = None
        self._set_track_gain(None)
        self.trackChanged.emit()

    @Slot("QVariant")
    def removeFromQueue(self, value) -> None:  # noqa: N802
        track = as_track(value)
        if track is None:
            return
        for i, t in enumerate(self._queue.tracks()):
            if t.uid == track.uid:
                self._queue.remove_at(i)
                return

    @Slot(int, int, result=bool)
    def moveQueueItem(self, src: int, dst: int) -> bool:  # noqa: N802
        return self._queue.move(int(src), int(dst))

    @Slot()
    def toggleFavorite(self) -> None:  # noqa: N802
        if self._current is None:
            return
        state = self._library.toggle_favorite(self._current)
        self._library.save()
        self.favoriteChanged.emit()
        self.favoriteStateChanged.emit()
        self.statusMessage.emit("已加入我喜欢的音乐" if state else "已从我喜欢的音乐移除")

    @Slot(result="QVariant")
    def nextTrackPreview(self):  # noqa: N802
        idx = self._queue.next_index(manual=True)
        t = self._queue.at(idx) if idx is not None else None
        return t.to_dict() if t else None

    # ── 内部：音量合成 ──────────────────────────────────────

    def _volume_ratio(self) -> float:
        """用户音量（线性）。"""
        return max(0.0, min(1.0, self._user_volume / 100.0))

    def _apply_output_volume(self) -> None:
        """把三个音量因子乘起来交给 ``QAudioOutput``。

        ``QAudioOutput`` 只接受 0.0~1.0，所以「均衡增益 + 用户音量」超过 1 时
        会被夹住 —— 这正是峰值防削波之外的最后一层保护（见 README 的已知限制：
        用户音量拉满时，需要抬升的曲目抬不到位）。
        """
        value = self._volume_ratio() * self._fade_factor * self._gain_ratio
        self._output.setVolume(max(0.0, min(1.0, value)))

    def _set_track_gain(self, track, *, ramp: bool = False) -> None:
        """接上当前曲目的均衡增益。

        ``ramp=True`` 时用短斜坡过渡（分析结果通常在中途才到，硬切会有
        明显的响度台阶）；换曲时不用斜坡 —— 那时正好在淡入，听不出来。
        """
        ratio = 1.0
        if self._loudness is not None and track is not None:
            try:
                ratio = float(self._loudness.gain_ratio(track))
            except Exception as e:
                logger.debug("读取均衡增益失败: %s", e)
                ratio = 1.0
        ratio = max(0.0, min(4.0, ratio))
        if abs(ratio - self._gain_target) < 1e-4:
            return
        self._gain_target = ratio
        if ramp and self._playing and self._fade_timer.isActive() is False:
            self._gain_from = self._gain_ratio
            self._gain_step = 0
            self._gain_timer.start()
        else:
            self._gain_timer.stop()
            self._gain_ratio = ratio
            self._apply_output_volume()
        self.gainChanged.emit()

    def _gain_tick(self) -> None:
        """增益斜坡：在 dB 域线性插值（倍数域线性会让听感先快后慢）。"""
        self._gain_step += 1
        ratio = min(1.0, self._gain_step / GAIN_RAMP_STEPS)
        start = max(1e-6, self._gain_from)
        end = max(1e-6, self._gain_target)
        self._gain_ratio = start * (end / start) ** ratio
        if self._gain_step >= GAIN_RAMP_STEPS:
            self._gain_timer.stop()
            self._gain_ratio = end
        self._apply_output_volume()

    @Slot(str, object)
    def _on_loudness_measured(self, uid: str, _measurement) -> None:
        """后台分析出结果：如果正是当前这首，把增益接上去。"""
        if self._current is None or self._current.uid != uid:
            return
        # 切歌瞬间可能正好在淡入，这时硬切也听不出来，但斜坡更稳妥
        self._set_track_gain(self._current, ramp=self._playing)
    # ── 内部：均衡分析 ──────────────────────────────────────

    def _request_analysis(self, track, target: str = "", is_local: bool = False) -> None:
        """排一次响度分析（当前曲目优先），并预取队列里的下一首。"""
        if self._loudness is None or track is None:
            return
        try:
            self._loudness.request(track, target=target, is_local=is_local, focus=True)
        except Exception as e:
            logger.debug("排入响度分析失败: %s", e)
        self._prefetch_next_analysis()

    def _prefetch_next_analysis(self) -> None:
        """预取「下一首」的响度：等它真的开始播时增益已经就绪，不用先响一下再压下去。

        在线曲目要先解析出播放地址 —— 那是网络请求，放到普通线程里做，
        解析完再回主线程排入分析（分析服务只在主线程碰 Qt 对象）。
        """
        if self._loudness is None:
            return
        try:
            idx = self._queue.next_index(auto_advance=True)
            track = self._queue.at(idx) if idx is not None else None
            if track is None:
                return
            path = str(getattr(track, "path", "") or "")
            if getattr(track, "is_local", False) and path:
                self._loudness.prefetch(track, target=path, is_local=True)
                return
            threading.Thread(
                target=self._analysis_resolve_worker, args=(track,),
                daemon=True, name="prefetch-analysis",
            ).start()
        except Exception as e:
            logger.debug("预取响度分析失败: %s", e)

    def _analysis_resolve_worker(self, track) -> None:
        """（工作线程）只做「曲目 → 播放地址」，不碰任何 Qt 对象。"""
        resolved = self._resolve_for_analysis(track)
        target, is_local = resolved if resolved else ("", False)
        self._emitter.analysisReady.emit(track, target, bool(is_local))

    @Slot(object, str, bool)
    def _on_analysis_ready(self, track, target: str, is_local: bool) -> None:
        """（主线程）预取解析回来了 → 排入分析。"""
        if self._loudness is None or track is None or not target:
            return
        try:
            self._loudness.prefetch(track, target=target, is_local=is_local)
        except Exception as e:
            logger.debug("预取排入失败: %s", e)

    def _resolve_for_analysis(self, track):
        """把曲目解析成可分析的目标（工作线程里调用）。

        只做「同源解析」不跨源兜底：兜底会去别的平台搜一遍，为了分析多打
        一轮请求不划算。已经在媒体缓存里的话会直接命中本地文件，那样连流量都省了。
        """
        if track is None:
            return None
        if getattr(track, "is_local", False) and getattr(track, "path", ""):
            return (str(track.path), True)
        quality = self._config.get("playback.quality", "320k") or "320k"
        cache_media = bool(self._config.get("storage.cache_media", False))
        try:
            resolved = resolve(track, quality, allow_fallback=False, cache_media=cache_media)
        except Exception as e:
            logger.debug("分析用解析失败 %s: %s", getattr(track, "uid", ""), e)
            return None
        if resolved is None:
            return None
        return (resolved.target, bool(resolved.is_local))

    # ── 内部：淡入淡出 ──────────────────────────────────────

    def _fade_in(self) -> None:
        if self._user_volume <= 0:
            return
        self._fade_factor = 0.0
        self._fade_step = 0
        self._fade_dir = 1
        self._apply_output_volume()
        self._fade_timer.start()

    def _pause_with_fade(self) -> None:
        if self._player.playbackState() != QMediaPlayer.PlayingState:
            return
        self._fade_step = FADE_STEPS
        self._fade_dir = -1
        self._fade_timer.start()

    def _fade_tick(self) -> None:
        if self._fade_dir > 0:
            self._fade_step += 1
            self._fade_factor = min(1.0, self._fade_step / FADE_STEPS)
            if self._fade_step >= FADE_STEPS:
                self._fade_timer.stop()
                self._fade_factor = 1.0
        else:
            self._fade_step -= 1
            self._fade_factor = max(0.0, self._fade_step / FADE_STEPS)
            if self._fade_step <= 0:
                self._fade_timer.stop()
                self._player.pause()
                self._fade_factor = 0.0
        self._apply_output_volume()

    def _next_pending_seek(self, track: Track) -> Optional[int]:
        """这次加载要从哪儿开始放。

        * 换了歌：上一首没兑现的那次 seek 作废，改用会话恢复的进度（如果有）；
        * 同一首歌重新加载（恢复会话后按播放、或加载中又点了一次播放）：
          保留用户刚拖到的位置，没拖过就用恢复出来的进度。
        """
        same_song = self._current is not None and self._current.uid == track.uid
        resume = self._take_resume_ms(track)
        if same_song and self._pending_seek_ms is not None:
            return self._pending_seek_ms
        return resume if resume > 0 else None

    def _take_resume_ms(self, track: Track) -> int:
        """取出（并消费）会话恢复时记下的进度，只认恢复的那一首。"""
        ms = self._resume_ms if (self._resume_uid and track.uid == self._resume_uid) else 0
        self._resume_uid = ""
        self._resume_ms = 0
        return ms

    def _load_current(self, *, record_history: bool = False) -> None:
        track = self._queue.current()
        if track is None:
            return

        # 先把「这次从哪儿开始放」定下来：_current 一旦换成新曲目，
        # 就分不清「同一首歌重新加载」和「换了歌」了（见 _next_pending_seek）
        pending_seek = self._next_pending_seek(track)

        self._seq += 1
        seq = self._seq

        self._fade_timer.stop()
        self._player.stop()
        self._progress_timer.stop()

        self._requested = track
        self._current = track
        self._fallback_source = ""
        self._media_ready = False
        self._duration_ms = int(track.interval or 0) * 1000
        self._position_ms = 0
        self._pending_seek_ms = pending_seek
        self._seekable = False
        self._lyrics.clear()
        self._lyric_index = -1
        self._lyric_progress = 0.0
        self._loading = True
        # 命中缓存时立刻就是正确的增益；没有缓存则先按 0 dB 起播，
        # 后台分析出结果后再用斜坡接上（见 _on_loudness_measured）
        self._set_track_gain(track)

        self.trackChanged.emit()
        self.durationChanged.emit()
        self.positionChanged.emit()
        self.lyricChanged.emit()
        self.lyricIndexChanged.emit()
        self.lyricProgressChanged.emit()
        self.loadingChanged.emit()
        self.seekableChanged.emit()
        self.favoriteChanged.emit()
        self.favoriteStateChanged.emit()

        if record_history and track:
            self._library.record_play(track)
            self._library.save_if_dirty()

        self._fetch_lyrics(seq, track)

        quality = self._config.get("playback.quality", "320k") or "320k"
        allow_fallback = bool(self._config.get("sources.fallback", True))
        cache_media = bool(self._config.get("storage.cache_media", False))

        threading.Thread(
            target=self._resolve_worker,
            args=(seq, track, quality, allow_fallback, cache_media),
            daemon=True,
            name="resolve-track",
        ).start()

    def _resolve_worker(self, seq: int, track: Track, quality: str,
                        allow_fallback: bool, cache_media: bool) -> None:
        try:
            result = resolve(
                track, quality, allow_fallback=allow_fallback, cache_media=cache_media
            )
        except Exception as e:
            logger.exception("解析曲目异常")
            result = e  # type: ignore[assignment]
        self._emitter.resolved.emit(seq, result)

    @Slot(int, object)
    def _on_resolved(self, seq: int, result: Any) -> None:
        if seq != self._seq:
            return  # 过期请求，丢弃

        if isinstance(result, Exception) or result is None:
            self._loading = False
            self.loadingChanged.emit()
            name = self._requested.name if self._requested else "当前曲目"
            self.errorOccurred.emit(f"无法播放「{name}」：所有音源均不可用")
            return

        resolved: Resolved = result
        self._current = resolved.track
        self._current_quality = resolved.quality
        self._fallback_source = resolved.track.source if resolved.fallback else ""
        if resolved.fallback:
            self.fallbackUsed.emit(resolved.track.source)

        self._player.setSource(QUrl(resolved.play_url))
        self._player.play()
        self._fade_in()
        self._progress_timer.start()
        self._media_ready = True

        self._loading = False
        self.loadingChanged.emit()
        self.trackChanged.emit()
        self.favoriteChanged.emit()
        self.favoriteStateChanged.emit()

        # 跨源兜底时 _current 换成了另一首，增益要跟着换；
        # 同时把「刚解析出来的地址」交给分析服务（在线曲目不必再解析一次）
        self._set_track_gain(self._current)
        self._request_analysis(self._current, target=resolved.target,
                               is_local=bool(resolved.is_local))

    # ── 内部：歌词 ──────────────────────────────────────────

    def _fetch_lyrics(self, seq: int, track: Track) -> None:
        if not track:
            return
        threading.Thread(
            target=self._lyric_worker, args=(seq, track), daemon=True, name="fetch-lyrics"
        ).start()

    def _lyric_worker(self, seq: int, track: Track) -> None:
        try:
            from ..sources import get_lyric
            from ..sources.netease import fetch_lyric_bundle, fetch_translation

            want_translation = bool(self._config.get("lyrics.show_translation", True))
            want_roma = bool(self._config.get("lyrics.show_romaji", False))
            want_word = bool(self._config.get("lyrics.dynamic", True))

            raw = ""
            translation = ""
            roma = ""
            word = ""
            if track.is_local and track.path:
                # 本地文件优先读同目录的同名 .lrc
                raw = _read_local_lrc(track.path) or ""

            # 去哪儿取歌词：在线曲目用自己的身份；本地文件用**扫描时在线匹配到的**
            # 那个身份（见 app/core/localmatch.py）。本地曲目没匹配上就没有在线身份，
            # 这时 source_id 会是 "local" 这种取不到歌词的音源，干脆不请求。
            source_id = track.source
            song_id = str(track.songmid or "")
            if track.is_local:
                source_id = str(track.match_source or "")
                song_id = str(track.match_songmid or "")

            if source_id == "wy" and song_id:
                # 网易云：逐行 / 翻译 / 罗马音 / **逐字**一次取回。老代码要跑两次
                # 请求（get_lyric + fetch_translation），而逐字歌词本来就藏在同一
                # 个接口里（带上 yv/ytv/yrv 即可，见 fetch_lyric_bundle）。
                bundle = fetch_lyric_bundle(
                    song_id,
                    want_translation=want_translation,
                    want_roma=want_roma,
                    want_word=want_word,
                )
                if bundle is not None:
                    if not raw:
                        raw = bundle.lrc
                    translation = bundle.translation
                    roma = bundle.roma
                    if want_word:
                        word = bundle.word

            if not raw and source_id and song_id:
                info = track.to_music_info()
                info.source = source_id
                info.songmid = song_id
                raw = get_lyric(info, source_id) or ""
                if raw and source_id == "wy" and not (translation or roma):
                    # 走到这儿说明 bundle 没成（网络抖动 / 接口变了），翻译只能单独再问
                    translation, roma = fetch_translation(
                        song_id, want_translation=want_translation, want_roma=want_roma
                    )
            self._emitter.lyricReady.emit(seq, (raw, translation, roma, word))
        except Exception as e:
            logger.debug("获取歌词失败: %s", e)
            self._emitter.lyricReady.emit(seq, ("", "", "", ""))

    @Slot(int, object)
    def _on_lyric_ready(self, seq: int, payload: Any) -> None:
        if seq != self._seq:
            return
        raw, translation, roma, word = _lyric_payload(payload)
        dynamic = bool(self._config.get("lyrics.dynamic", True))
        self._lyrics.clear()
        if raw:
            self._lyrics.parse(raw)
            if self._lyrics.lines:
                # 顺序要紧：翻译/罗马音按**行时间**配对，而 set_words 会把行时间
                # 对齐到 yrc 的（更贴近真实起唱点），先贴逐字的话翻译就配不上了
                if translation:
                    self._lyrics.set_translation(translation)
                if roma:
                    self._lyrics.set_roma(roma)
                if dynamic:
                    if word:
                        self._lyrics.set_words(word)
                    # 没有真逐字的行再摊一份伪动态（有些歌只有一两行有 yrc）
                    self._lyrics.apply_pseudo()
        self._lyric_index = -1
        self._lyric_progress = 0.0
        self.lyricChanged.emit()
        self.lyricIndexChanged.emit()
        self.lyricProgressChanged.emit()

    @Slot()
    def applyLyricSettings(self) -> None:  # noqa: N802
        """「设置 → 歌词」改了就立刻生效的那部分（目前只有逐字开关）。

        正在播的这首如果只拿到了逐行歌词，开关一打开就先把伪动态补上 ——
        否则用户点完开关看不见任何变化，会以为功能坏了。**不清除已有的逐字数据**：
        关掉再打开时真逐字还在，不用重新联网取一遍（真逐字要当前这首本来就有，
        在设置里打开才生效的那种情况，下一首自然会有）。
        """
        if self._lyrics.is_empty:
            return
        if bool(self._config.get("lyrics.dynamic", True)):
            if not any(line.words for line in self._lyrics.lines):
                if self._lyrics.apply_pseudo():
                    self.lyricChanged.emit()
                    self._lyric_progress = self._lyrics.char_progress(self._position_ms)
                    self.lyricProgressChanged.emit()

    # ── 内部：播放器回调 ────────────────────────────────────

    def _on_position(self, ms: int) -> None:
        self._position_ms = int(ms)
        self.positionChanged.emit()
        self._update_lyric_index()

    def _on_duration(self, ms: int) -> None:
        if ms > 0:
            self._duration_ms = int(ms)
            self.durationChanged.emit()
            if not self._seekable:
                self._seekable = True
                self.seekableChanged.emit()
            self._apply_pending_seek()

    def _apply_pending_seek(self) -> None:
        """兑现「加载期间挂起的那次 seek」（恢复会话的进度也从这条路进场）。

        必须等媒体真的就绪再跳：``setPosition()`` 在 ``LoadingMedia`` 阶段会被
        后端**静默丢掉** —— 实测（ffmpeg 后端 + 本地 wav）连 ``durationChanged``
        都已经发出来了，这一跳仍然不生效，播放还是从 0 开始，于是「上次听到
        哪儿」永远回不去。所以就绪前只是记着，等状态回调再来一次。
        """
        if self._pending_seek_ms is None or not self._seek_ready():
            return
        pos, self._pending_seek_ms = max(0, int(self._pending_seek_ms)), None
        self._player.setPosition(pos)
        self._position_ms = pos
        self.positionChanged.emit()

    def _seek_ready(self) -> bool:
        """媒体是否已经就绪到能跳转。"""
        if self._player.mediaStatus() not in (
            QMediaPlayer.LoadedMedia,
            QMediaPlayer.BufferingMedia,
            QMediaPlayer.BufferedMedia,
        ):
            return False
        try:
            return bool(self._player.isSeekable())
        except Exception:  # 后端没实现这个查询时按「就绪即可」
            return True

    def _on_playback_state(self, state) -> None:
        playing = state == QMediaPlayer.PlayingState
        paused = state == QMediaPlayer.PausedState
        changed = playing != self._playing or paused != self._paused
        self._playing, self._paused = playing, paused
        if playing:
            self._progress_timer.start()
        if changed:
            self.playingChanged.emit()
            self.pausedChanged.emit()

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.EndOfMedia:
            self._advance_auto()
        elif status == QMediaPlayer.InvalidMedia:
            if self._current is not None:
                self.errorOccurred.emit(
                    f"无法解码「{self._current.name}」，已跳过"
                )
                QTimer.singleShot(600, self._advance_auto)
        elif status in (
            QMediaPlayer.LoadedMedia,
            QMediaPlayer.BufferingMedia,
            QMediaPlayer.BufferedMedia,
        ):
            if not self._seekable:
                self._seekable = True
                self.seekableChanged.emit()
            # 就绪了：该兑现加载期间挂起的那次 seek 了
            self._apply_pending_seek()

    @Slot(bool)
    def _on_seekable_changed(self, seekable: bool) -> None:
        if seekable:
            self._apply_pending_seek()

    def _on_error(self, error, message: str) -> None:
        if error == QMediaPlayer.NoError:
            return
        logger.warning("播放错误: %s %s", error, message)
        name = self._current.name if self._current else ""
        self._loading = False
        self.loadingChanged.emit()
        self.errorOccurred.emit(f"播放失败：{name}（{message or '未知错误'}）")

    def _advance_auto(self) -> None:
        idx = self._queue.next_index(auto_advance=True)
        if idx is None:
            self.stop()
            self.statusMessage.emit("播放列表已结束")
            return
        self._queue.set_index(idx)
        self._load_current(record_history=True)

    def _update_lyric_index(self) -> None:
        if self._lyrics.is_empty:
            return
        idx = self._lyrics.index_at(self._position_ms)
        if idx != self._lyric_index:
            self._lyric_index = idx
            self.lyricIndexChanged.emit()
        # 逐字填充按位置连续推进：位置每变一次就重算一次。实测
        # QMediaPlayer.positionChanged 约 93 ms 一次，够界面把"正在唱的那个字"
        # 画成渐变；暂停时位置不动，填充自然停住。
        progress = self._lyrics.char_progress(self._position_ms)
        if abs(progress - self._lyric_progress) > 1e-3:
            self._lyric_progress = progress
            self.lyricProgressChanged.emit()

    def _poll_progress(self) -> None:
        if self._player.playbackState() == QMediaPlayer.StoppedState and not self._paused:
            self._progress_timer.stop()
            return
        # 播放中另外按间隔记一次进度：只靠退出时写盘的话，掉电 / 被强杀就全丢了
        self._session_ticks += 1
        if self._session_ticks * self._progress_timer.interval() >= SESSION_PROGRESS_MS:
            self._session_ticks = 0
            self.saveSession()

    def _resume(self) -> None:
        self._player.play()
        self._fade_in()

    # ── 队列通知 ────────────────────────────────────────────

    def _on_queue_changed(self) -> None:
        self.queueChanged.emit()
        self._session_timer.start()

    # ── 播放会话（退出时记下、下次启动摆回来）───────────────

    @Slot()
    def saveSession(self) -> None:  # noqa: N802
        """把在播曲目 / 队列 / 播放模式 / 进度写进会话文件。"""
        if not bool(self._config.get("playback.restore_session", True)):
            # 关掉恢复功能就顺手把旧会话删掉：下次开才是真的「干净启动」，
            # 而不是留个过期队列等用户哪天又打开这个开关时突然冒出来
            play_session.clear()
            return
        play_session.save(self._queue, self._position_ms)

    @Slot(result=bool)
    def restoreSession(self) -> bool:  # noqa: N802
        """把上次退出时的播放队列与在播曲目摆回来。

        **不自动出声**：恢复的是状态，不是播放动作 —— 程序一启动就轰一嗓子，
        比「少恢复一首歌」讨厌得多。按播放键即从上次的进度接着放。

        要在 QML 加载**之前**调用（见 ``Application.__init__``）：QML 是在
        建立绑定时读一次属性值的，先摆好状态，界面一起来就是对的。
        """
        if not bool(self._config.get("playback.restore_session", True)):
            return False

        saved = play_session.load()
        if saved is None:
            return False

        self._queue.load_dict(saved.queue_dict())
        track = self._queue.current()
        if track is None:
            return False

        self._current = track
        self._requested = track
        self._fallback_source = ""
        self._media_ready = False
        self._duration_ms = int(track.interval or 0) * 1000
        self._seekable = False
        # 上一次没兑现的 seek 一并作废（正常情况下这里是启动时，本来就该是空的）
        self._pending_seek_ms = None
        self._resume_ms = self._clamp_resume(saved.position_ms)
        self._resume_uid = track.uid if self._resume_ms > 0 else ""
        self._position_ms = self._resume_ms
        # 会话恢复出来的曲目还没解析地址，先把缓存里的均衡增益摆上
        # （没分析过的这首会在真正播放时才排入队列）
        self._set_track_gain(track)

        self.trackChanged.emit()
        self.durationChanged.emit()
        self.positionChanged.emit()
        self.favoriteChanged.emit()
        self.favoriteStateChanged.emit()
        logger.info(
            "已恢复上次的播放会话：%d 首，第 %d 首「%s」，进度 %.1f 秒",
            self._queue.size,
            self._queue.index + 1,
            track.name,
            self._resume_ms / 1000.0,
        )
        return True

    def _clamp_resume(self, position_ms: int) -> int:
        """上次的进度：关掉「记住进度」时一律从 0 开始。

        贴着末尾退出（听到最后一秒就关机）的情况也要夹一下 —— 否则恢复出来
        一按播放就立刻播完跳到下一首，看着像「这首被吞了」。
        """
        if not bool(self._config.get("playback.remember_progress", True)):
            return 0
        ms = max(0, int(position_ms or 0))
        if ms <= 0:
            return 0
        if self._duration_ms > 0:
            ms = min(ms, max(0, self._duration_ms - 1000))
        return ms

    # ── 生命周期 ────────────────────────────────────────────

    @Slot()
    def shutdown(self) -> None:
        # 先存会话再停播放器：stop() 可能把 position 归零，
        # 那样「上次听到哪儿」存下来永远是 0:00
        self._session_timer.stop()
        self.saveSession()
        self._fade_timer.stop()
        self._gain_timer.stop()
        self._progress_timer.stop()
        try:
            self._player.stop()
        except Exception:
            pass
        # 存的是**用户音量**，不是 QAudioOutput 上的实际音量（后者被
        # 淡入淡出与均衡增益乘过，存下来下次启动就跑了）
        self._config.set("playback.volume", int(self._user_volume))
        self._config.set("playback.muted", self.muted)


def _lyric_payload(payload: Any) -> tuple:
    """把歌词线程投递回来的载荷拆成 ``(逐行, 翻译, 罗马音, 逐字)``。

    载荷是元组；长度不对（旧格式 / 异常）时一律按空处理，不让界面拿到半个字段。
    """
    if not isinstance(payload, tuple):
        return ("", "", "", "")
    values = list(payload[:4])
    values += [""] * (4 - len(values))
    return tuple("" if v is None else v for v in values)


def _read_local_lrc(audio_path: str) -> Optional[str]:
    """读取同目录同名 ``.lrc`` 字幕文件（FMCL 完全不支持本地歌词）。"""
    try:
        from pathlib import Path

        p = Path(audio_path)
        for cand in (p.with_suffix(".lrc"), p.with_suffix(".LRC")):
            if cand.exists():
                for enc in ("utf-8", "utf-8-sig", "gbk", "big5"):
                    try:
                        return cand.read_text(encoding=enc)
                    except (UnicodeDecodeError, LookupError):
                        continue
    except Exception:
        pass
    return None
