"""音乐播放引擎的状态机与纯计算（阶段 1 任务 1.4-A，形态 2/3：逻辑与界面切分）。

对应宿主是 `ui/app_music.py` 的 `MusicPlayerMixin`（原 5116 行、183 个方法）。
本轮只抽**播放引擎**这一块：播放/暂停/停止/上下曲/定位/音量/静音、淡入淡出的步进
计算、预取判定、进度换算、播放模式语义、播放结束的"下一首"决策、目录扫描。
在线搜索/下载回退/歌单 CRUD/歌词轮询/音效面板/热键仍留在界面侧（任务 1.4-B）。

## 切缝判据（穷举过，不是拍的）

先用 `poc/_probe_music_attrs.py` 把 `self._music_*` 每个属性的**读写方法集合**列出来，
再按"所有读写点是否都在本轮范围内"分类：

* **全在范围内**的（`_music_seek_offset` / `_music_duration` / `_music_current_filepath` /
  `_music_current_quality` / `_music_is_fading` / `_music_fade_out_target` /
  `_music_prefetch_seq` / `_music_prefetch_slot` / `_music_prefetch_started` /
  `_music_vol_before_mute` / `_music_modes_used` / `_music_metadata_cache`）
  → 交给本服务的 `EngineState` **独占**，界面侧不再自己算，只镜像一份供旧读者使用。
* **有范围外读写点**的（`_music_is_playing` / `_music_is_paused` / `_music_volume` /
  `_music_progress` / `_music_current_index` / `_music_play_mode` / `_music_playlist` /
  `_music_playlist_context_*` / `_music_is_online_playing` / `_music_current_online_info`）
  → **界面侧仍是唯一所有者**，服务只在调用时把这些值**当参数收进来**、把新值
  **放在返回值里**。理由：这些属性被 100+ 个本轮范围外的方法直接读写
  （例如 `_update_play_btn_ui` 写 `_music_is_playing`、`_play_from_index` 写
  `_music_current_index`、`_load_music_state` 写 `_music_play_mode`），
  把所有权搬走等于要求同时改写那些方法 —— 那是 1.4-B 的工作量，
  而且会让"公开行为 100% 不变"这条硬约束失去可验证性。

## 服务不做什么

不弹窗、不起线程、不调 `after`、不碰控件、不 import 任何 GUI 栈、不 import `ui.*`、
不用 `ui.i18n` 的文案函数。需要界面做的事一律通过**返回值**（`FadeStep` /
`TrackEndAction` / `PrefetchPlan` 这些数据类）交给调用方。

## mixer 依赖怎么注入

pygame 的降级探测与原文同形状（模块级 `_pygame_import_error`），并且**必须住在
模块级全局名上**：`available` 在**调用时**读它，所以
`monkeypatch.setattr(music_player, "_pygame_import_error", ImportError("x"))`
能真的把引擎切成"无 pygame"模式。取 mixer 走 `get_mixer()` 函数而不是导入期常量，
测试可以直接换成假 mixer —— 不用装 pygame、不用真放声音。

引擎实例由 `MusicPlayerMixin.__init_music` 直接 `MusicPlayerService()` 构造
（**不注册进 AppContext**：注册需要改 `app/**`，那是禁改文件；`Service` 基类本身
就支持单独实例化做单元测试）。因此在没有 AppContext 的场景下不要调用
`publish` / `ui` / `tasks` 这些需要 context 的成员。
"""

from __future__ import annotations

import os
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from logzero import logger

from services.base import Service
from services.music_audio import MetadataCache, extract_audio_metadata

# ── pygame / mixer 降级探测（与原文同形状，保持模块级全局名）──────────────
_pygame_import_error = None
try:
    import pygame
    import pygame.mixer as mixer
except ImportError as e:
    _pygame_import_error = e


def get_mixer():
    """取 mixer 模块；pygame 不可用时返回 None。

    做成函数是为了可替换：测试用
    ``monkeypatch.setattr(music_player, "get_mixer", lambda: fake_mixer)``
    就能注入替身，无需真的安装 pygame 或播放声音。
    """
    if _pygame_import_error is not None:
        return None
    return mixer


# ── 播放模式语义（原文写在 ui/app_music.py 模块顶层，现以此为唯一真相）──────
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

#: `_music_cycle_mode` 的循环顺序（原文写死为这个列表字面量）
PLAY_MODE_ORDER = (
    PLAY_MODE_SEQUENTIAL,
    PLAY_MODE_LOOP_LIST,
    PLAY_MODE_LOOP_SINGLE,
    PLAY_MODE_RANDOM,
)

#: 模式名 → 模式号（原文 `_load_music_state` 里的 `{v: k for k, v in PLAY_MODE_NAMES.items()}`）
PLAY_MODE_IDS = {v: k for k, v in PLAY_MODE_NAMES.items()}

#: 集齐全部模式才解锁的成就判据用的总数（原文写死 `>= 4`）
ALL_PLAY_MODES = len(PLAY_MODE_ORDER)

# ── 淡入淡出参数 ──────────────────────────────────────────────────────────
FADE_STEPS = 20
FADE_INTERVAL_MS = 50
FADE_OUT_PAUSE = "pause"
FADE_OUT_STOP = "stop"

# ── 进度轮询参数（原文 `_poll_music_progress` 里写死 1000 / 500）────────────
PROGRESS_POLL_MS = 500
PROGRESS_POLL_IDLE_MS = 1000

# ── 预取触发比例（原文写死 `self._music_progress < self._music_duration * 0.5`）──
PREFETCH_START_RATIO = 0.5

# ── 音量 ─────────────────────────────────────────────────────────────────
DEFAULT_VOLUME = 0.7
VOLUME_MIN_PERCENT = 0
VOLUME_MAX_PERCENT = 100


@dataclass
class EngineState:
    """播放引擎**独占**的那部分状态（见模块 docstring 的切缝判据）。

    界面侧仍保留同名属性（`_music_seek_offset` 等）并在每次委托后镜像本对象，
    这样范围外的旧读者（例如 `_poll_music_progress` 读 `_music_seek_offset`）
    拿到的值与搬家前一致。
    """

    seek_offset: float = 0.0
    duration: float = 0.0
    current_filepath: Optional[str] = None
    current_quality: str = ""
    is_fading: bool = False
    fade_out_target: Optional[str] = None
    prefetch_seq: int = 0
    prefetch_slot: Optional[dict] = None
    prefetch_started: bool = False
    vol_before_mute: float = DEFAULT_VOLUME
    modes_used: set = field(default_factory=set)


@dataclass
class FadeStep:
    """一次淡入/淡出步进的计算结果（界面侧照着它做事，自己不起定时器算数）。"""

    #: 要设置的音量；``None`` 表示本次不设音量（仅取消或仅收尾）
    volume: Optional[float] = None
    #: 执行后 `_music_is_fading` 的值
    fading: bool = False
    #: 是否还要排下一次定时器（`after(FADE_INTERVAL_MS, ...)`）
    schedule_next: bool = False
    #: 是否应当整体取消淡入淡出（界面调 `_music_cancel_fade`）
    cancel: bool = False
    #: 淡出是否已走完（界面调 `_music_execute_fade_out_target`）
    finished: bool = False


@dataclass
class TrackEndAction:
    """一曲自然结束后的动作。``kind`` ∈ replay / next / stop / none。"""

    kind: str
    index: int = -1


@dataclass
class PrefetchPlan:
    """预取计划：界面侧据此起线程（服务自己不建线程）。"""

    seq: int
    index: int
    song: Any


@dataclass
class MuteResult:
    """静音切换结果。``remember`` 非 None 时，界面应把它记进"静音前的音量"。"""

    volume: float
    remember: Optional[float] = None


@dataclass
class ContextTarget:
    """歌单上下文里"该播哪一首"的解析结果。

    ``kind`` ∈
        - ``local``：本地歌曲，``local_paths`` / ``local_index`` 已算好
        - ``local_missing``：本地文件不存在，界面按原文跳到下一首
        - ``online``：在线歌曲；``prefetched`` 非 None 时优先消费预取结果
    """

    kind: str
    index: int
    song: Any = None
    local_paths: Optional[List[str]] = None
    local_index: int = 0
    prefetched: Optional[dict] = None


# ══════════════════════════════════════════════════════════════════════
# 服务本体
# ══════════════════════════════════════════════════════════════════════


class MusicPlayerService(Service):
    """播放引擎：状态机 + 决策 + mixer 驱动（零 UI、零线程、零定时器）。"""

    name = "music_player"
    label = "音乐播放"

    def __init__(self, context=None, mixer_module=None) -> None:
        super().__init__(context)
        self.state = EngineState()
        self._metadata = MetadataCache()
        self._injected_mixer = mixer_module

    # ── 基础设施 ──────────────────────────────────────────────────────

    @property
    def mixer(self):
        """注入的 mixer 优先；否则取模块级 pygame.mixer（不可用时为 None）。"""
        if self._injected_mixer is not None:
            return self._injected_mixer
        return get_mixer()

    @property
    def available(self) -> bool:
        """pygame 是否可用（= 原文的 `_pygame_import_error is None`）。

        刻意在**调用时**读模块级全局名：这样 monkeypatch 改
        `services.music_player._pygame_import_error` 能立刻生效。
        """
        return _pygame_import_error is None

    @property
    def metadata_cache(self) -> "Any":
        """元数据缓存（同一个 OrderedDict 对象，界面侧可采纳并直接 `.clear()`）。"""
        return self._metadata.data

    def get_metadata(self, filepath: str) -> dict:
        """取曲目元数据（LRU 缓存 + `services.music_audio.extract_audio_metadata`）。"""
        return self._metadata.get(filepath, extract_audio_metadata)

    def clear_metadata_cache(self) -> None:
        self._metadata.clear()

    def _music_obj(self):
        """取 `mixer.music`；不可用时返回 None（调用方按"原文的异常被吞掉"处理）。"""
        m = self.mixer
        return None if m is None else m.music

    def _require_music(self):
        """取 `mixer.music`，不可用时抛 RuntimeError（原文此处会 NameError 并被吞）。"""
        music = self._music_obj()
        if music is None:
            raise RuntimeError("pygame mixer 不可用")
        return music

    def set_mixer_volume(self, volume: float) -> bool:
        """设置 mixer 音量；异常吞掉（原文每个调用点都是 try/except pass）。"""
        music = self._music_obj()
        if music is None:
            return False
        try:
            music.set_volume(volume)
            return True
        except Exception:
            return False

    def is_music_busy(self) -> Optional[bool]:
        """`mixer.music.get_busy()`；不可用时返回 **None**（不是 False）。

        区分 None 与 False 是刻意的：原文里 mixer 缺失会在这一行抛异常，
        被 `_poll_music_progress` 的 try 吞掉，于是"曲目结束"分支**不会**被走到。
        界面侧按 `is False` 判断即可复现同一语义。
        """
        music = self._music_obj()
        if music is None:
            return None
        try:
            return bool(music.get_busy())
        except Exception:
            return None

    def mixer_position_ms(self) -> Optional[float]:
        """`mixer.music.get_pos()`（毫秒，-1 表示尚未开始计时）；不可用时 None。"""
        music = self._music_obj()
        if music is None:
            return None
        try:
            return music.get_pos()
        except Exception:
            return None

    # ── 播放（mixer 驱动；状态迁移在下方"决策"区）─────────────────────

    def load_and_play(self, path: str, start_pos: float = 0.0) -> None:
        """mixer 三连：load → set_volume(0) → play（顺序与原文一致）。

        刻意**不吞异常**：原文把这三行和后续状态更新、Tk 调用放在同一个
        ``try`` 里，失败时记 `播放失败: {filepath}: {e}` 并把播放态复位。
        这里抛出，让调用方那段 try 保持原样。
        """
        music = self._require_music()
        music.load(path)
        music.set_volume(0)
        music.play(start=start_pos if start_pos > 0 else 0)

    def resume_mixer(self) -> None:
        """恢复播放：unpause → set_volume(0)（原文 resume 分支的两行）。"""
        music = self._require_music()
        music.unpause()
        music.set_volume(0)

    def pause_mixer(self) -> bool:
        """暂停 mixer（异常吞掉，原文 try/except pass）。"""
        music = self._music_obj()
        if music is None:
            return False
        try:
            music.pause()
            return True
        except Exception:
            return False

    def stop_mixer(self) -> bool:
        """停止并卸载（原文 `stop()` + `unload()`，异常吞掉）。"""
        music = self._music_obj()
        if music is None:
            return False
        try:
            music.stop()
            music.unload()
            return True
        except Exception:
            return False

    def reload_and_seek(self, seconds: float, filepath: str, was_paused: bool) -> None:
        """重载文件到指定位置（原文 `_music_seek` 的 mixer 四行）。

        原文注释：``set_pos`` 不重置 ``get_pos`` 计时器，所以必须 reload。
        """
        music = self._require_music()
        music.stop()
        music.load(filepath)
        music.set_volume(0)
        music.play(start=seconds)
        if was_paused:
            music.pause()

    # ── 状态迁移（引擎独占状态；纯函数式地返回结果）───────────────────

    def begin_local_playback(self, filepath: str, duration: float, start_pos: float = 0.0) -> None:
        """记录一次本地播放开始（原文 `_play_file` 成功分支里的赋值）。"""
        self.state.seek_offset = start_pos if start_pos > 0 else 0
        self.state.current_filepath = filepath
        self.state.duration = duration
        self._cancel_fade_state()

    def begin_online_playback(
        self, filepath: str, duration: float, quality: str, start_pos: float = 0.0
    ) -> None:
        """记录一次在线播放开始（原文 `_play_online_file` 成功分支里的赋值）。"""
        self.state.seek_offset = start_pos if start_pos > 0 else 0
        self.state.current_filepath = filepath
        self.state.duration = duration
        self.state.current_quality = quality or ""
        self._cancel_fade_state()

    def reset_seek_offset(self) -> None:
        """把 seek 偏移复位（原文若干分支里的 `self._music_seek_offset = 0`）。"""
        self.state.seek_offset = 0

    def current_file(self, playlist: Sequence[str], index: int) -> Optional[str]:
        """当前索引对应的文件（原文 `_get_current_file`）。越界返回 None。"""
        if 0 <= index < len(playlist):
            return playlist[index]
        return None

    def seek_seconds(self, value: float, duration: float) -> float:
        """进度条百分比 → 秒（原文 `_music_seek` 的第一行算式）。"""
        return (value / 100.0) * duration if duration > 0 else 0

    # ── 播放/暂停切换的动作判定 ────────────────────────────────────────

    def toggle_play_action(
        self,
        *,
        is_playing: bool,
        is_paused: bool,
        has_playlist: bool,
        is_online_playing: bool,
        has_online_info: bool,
        current_index: int,
        is_fading: bool,
    ) -> str:
        """"按一下播放/暂停"该做什么（原文 `_music_toggle_play` 的全部分支）。

        返回的动作名与原文分支逐条对应（顺序也一致，因为原文是 if/elif 链）：

        - ``ignore``：空歌单且没有在线播放，或三种播放态都不成立 → 直接 return
        - ``replay_online``：没在播也没暂停，且正在播在线歌 —— 重播当前在线歌曲
        - ``play_local``：从当前索引（``-1`` 时置 0）开始本地播放
          （原文在"在线播放中但连 info 都没有"时提前 return，这一条也归到 ``ignore``）
        - ``ignore_fading``：淡入淡出进行中时按暂停/恢复都不响应
        - ``resume``：暂停中恢复播放
        - ``fade_out_pause``：播放中 → 淡出后暂停

        界面侧负责 mixer 调用、`after` 与控件更新。
        """
        if not has_playlist and not is_online_playing:
            return "ignore"
        if not is_playing and not is_paused:
            if is_online_playing and has_online_info:
                return "replay_online"
            if current_index < 0 and is_online_playing:
                return "ignore"
            return "play_local"
        if is_paused:
            return "ignore_fading" if is_fading else "resume"
        if is_playing:
            return "ignore_fading" if is_fading else "fade_out_pause"
        return "ignore"

    # ── 淡入淡出 ─────────────────────────────────────────────────────

    def _cancel_fade_state(self) -> None:
        self.state.is_fading = False
        self.state.fade_out_target = None

    def cancel_fade(self) -> None:
        """取消淡入淡出的**状态部分**（`after_cancel` 由界面侧做）。"""
        self._cancel_fade_state()

    def begin_fade_out(self, target: str) -> None:
        """记下淡出走完后要做什么（原文 `self._music_fade_out_target = "pause"/"stop"`）。"""
        self.state.fade_out_target = target

    def take_fade_out_target(self) -> Optional[str]:
        """取走并清空淡出目标（原文 `_music_execute_fade_out_target` 的头两行）。"""
        target = self.state.fade_out_target
        self.state.fade_out_target = None
        return target

    def fade_in_step(self, step: int, volume: float, is_playing: bool, is_paused: bool) -> FadeStep:
        """淡入一步的**数值与状态**（原文 `_music_fade_in`，定时器留在界面侧）。

        原文分支：
          - 未播放或已暂停 → 取消淡入淡出；
          - ``step >= FADE_STEPS`` → 音量设为用户音量，结束；
          - 否则 → 音量设为 ``volume * (step+1) / FADE_STEPS``，继续排定时器。
        """
        if not is_playing or is_paused:
            return FadeStep(cancel=True)
        if step >= FADE_STEPS:
            self.state.is_fading = False
            return FadeStep(volume=volume, fading=False, schedule_next=False)
        vol = volume * (step + 1) / FADE_STEPS
        self.state.is_fading = True
        return FadeStep(volume=vol, fading=True, schedule_next=True)

    def fade_out_step(self, step: int, volume: float, is_playing: bool, is_paused: bool) -> FadeStep:
        """淡出一步的数值与状态（原文 `_music_fade_out`）。

        原文 ``FADE_STEPS > 1`` 的兜底与 ``max(0, vol)`` 都逐字保留；
        走完最后一步时把音量设为 0 并置 ``finished``（界面接着执行淡出目标）。
        """
        if not is_playing or is_paused:
            return FadeStep(cancel=True)
        if step >= FADE_STEPS:
            self.state.is_fading = False
            return FadeStep(volume=0.0, fading=False, schedule_next=False, finished=True)
        remaining = FADE_STEPS - 1 - step
        vol = volume * remaining / (FADE_STEPS - 1) if FADE_STEPS > 1 else 0
        self.state.is_fading = True
        return FadeStep(volume=max(0, vol), fading=True, schedule_next=True)

    # ── 音量 / 静音 ──────────────────────────────────────────────────

    def volume_percent_after_delta(self, volume: float, delta: int) -> int:
        """音量增减后的整百分比（原文 `_adjust_volume` 第一行，含 0..100 钳制）。"""
        return max(VOLUME_MIN_PERCENT, min(VOLUME_MAX_PERCENT, int(volume * 100) + delta))

    def toggle_mute(self, volume: float) -> MuteResult:
        """静音切换（原文 `_music_toggle_mute` 的分支与"静音前音量"记忆规则）。"""
        if volume > 0:
            self.state.vol_before_mute = volume
            return MuteResult(volume=0.0, remember=volume)
        return MuteResult(volume=self.state.vol_before_mute)

    # ── 进度 ─────────────────────────────────────────────────────────

    def poll_position(self, seek_offset: float) -> Optional[float]:
        """当前播放位置（秒）= ``get_pos()/1000 + seek_offset``（原文算式）。

        mixer 不可用返回 None —— 原文此处会抛异常并被所在 try 吞掉。
        """
        raw = self.mixer_position_ms()
        if raw is None:
            return None
        return raw / 1000.0 + seek_offset

    def progress_percent(self, pos: float, duration: float) -> Optional[float]:
        """进度条百分比；None 表示"不更新进度条"。

        原文：``if self._music_duration > 0:`` 才算，且只在 ``0 <= pct <= 100`` 时 set。
        """
        if duration <= 0:
            return None
        pct = (pos / duration) * 100
        if 0 <= pct <= 100:
            return pct
        return None

    # ── 播放模式 ─────────────────────────────────────────────────────

    def cycle_mode(self, play_mode: int) -> int:
        """切到下一个播放模式（原文 `_music_cycle_mode` 前三行）。

        ``PLAY_MODE_ORDER.index(play_mode)`` 在非法模式下抛 ValueError —— 原文
        同样是 ``list.index``，此处不额外兜底（记录现状、疑为缺陷）。
        """
        idx = PLAY_MODE_ORDER.index(play_mode)
        return PLAY_MODE_ORDER[(idx + 1) % len(PLAY_MODE_ORDER)]

    # ── 上下曲决策 ───────────────────────────────────────────────────

    def next_index(self, play_mode: int, count: int, current_index: int) -> int:
        """非歌单上下文时的"下一首"索引（原文 `_music_next` 的后半段）。"""
        if play_mode == PLAY_MODE_RANDOM:
            random.seed()
            new_idx = random.randrange(count)
            if count > 1 and new_idx == current_index:
                new_idx = (new_idx + 1) % count
            return new_idx
        return (current_index + 1) % count

    def prev_index(self, play_mode: int, count: int, current_index: int) -> int:
        """"上一首"索引（原文 `_music_prev`；上下文与普通歌单共用同一算法）。"""
        if play_mode == PLAY_MODE_RANDOM:
            random.seed()
            new_idx = random.randrange(count)
            if count > 1 and new_idx == current_index:
                new_idx = (new_idx + 1) % count
            return new_idx
        return (current_index - 1) % count

    def next_context_index(
        self,
        songs: Sequence,
        current_index: int,
        play_mode: int,
        prefetch_slot: Optional[dict],
        prefetch_seq: int,
    ) -> int:
        """歌单上下文里的"下一首"索引（含"预取已就绪则直接切过去"的判定）。

        原文 `_music_next` 的前半段：预取槽位需要
        ``序号有效 + 索引合法 + 歌曲指纹一致`` 三条同时成立才算数。
        """
        n = len(songs)
        slot = prefetch_slot
        prefetched = (
            slot is not None
            and slot.get("seq") == prefetch_seq
            and 0 <= slot.get("idx", -1) < n
        )
        if prefetched:
            target = songs[slot["idx"]]
            if target.source_type != "online" or slot.get("song_key") != (
                target.online_source,
                target.online_songmid,
            ):
                prefetched = False
        if prefetched:
            return slot["idx"]
        if play_mode == PLAY_MODE_RANDOM:
            random.seed()
            new_idx = random.randrange(n)
            if n > 1 and new_idx == current_index:
                new_idx = (new_idx + 1) % n
            return new_idx
        return (current_index + 1) % n

    def track_end_action(self, play_mode: int, current_index: int, count: int) -> TrackEndAction:
        """一曲播放自然结束后的动作（原文 `_on_track_end` 的四个模式分支）。

        记录现状、疑为缺陷：歌单为空时原文的算术本身就会抛 ——
        ``LOOP_LIST`` 的 ``(idx + 1) % 0`` 抛 ZeroDivisionError、
        ``RANDOM`` 的 ``random.randrange(0)`` 抛 ValueError、
        ``LOOP_SINGLE`` 的 ``playlist[-1]`` 在空列表上抛 IndexError。
        异常恰好被 `_poll_music_progress` 的 ``except Exception: pass`` 吞掉，
        所以表现是"静默什么都不发生"。这里**照抄同一套算术**，不额外兜底。
        """
        if play_mode == PLAY_MODE_LOOP_SINGLE:
            return TrackEndAction("replay", current_index)
        if play_mode == PLAY_MODE_SEQUENTIAL:
            if current_index + 1 < count:
                return TrackEndAction("next", current_index + 1)
            return TrackEndAction("stop")
        if play_mode == PLAY_MODE_LOOP_LIST:
            return TrackEndAction("next", (current_index + 1) % count)
        if play_mode == PLAY_MODE_RANDOM:
            random.seed()
            new_idx = random.randrange(count)
            if count > 1 and new_idx == current_index:
                new_idx = (new_idx + 1) % count
            return TrackEndAction("next", new_idx)
        return TrackEndAction("none")

    # ── 预取 ─────────────────────────────────────────────────────────

    def invalidate_prefetch(self) -> None:
        """使在途与已完成的预取全部失效（原文 `_music_invalidate_prefetch` 三行）。"""
        self.state.prefetch_seq += 1
        self.state.prefetch_slot = None
        self.state.prefetch_started = False

    def plan_prefetch(
        self,
        songs: Sequence,
        current_index: int,
        play_mode: int,
        duration: float,
        progress: float,
    ) -> Optional[PrefetchPlan]:
        """播放过半时决定要不要预取下一首（原文 `_music_maybe_prefetch_next` 的判定）。

        返回 None 表示"不必预取"；返回计划时界面侧负责读 Tk 音质变量、起线程。
        **注意**：``prefetch_started`` 在原文里是"每首只触发一次"的闸门，
        它在这几个 early-return **之前**就被置 True —— 也就是说"判定为无需预取"
        同样会关上闸门。这里逐字保留（否则会把预取重试次数变得不同）。
        """
        if self.state.prefetch_started:
            return None
        if not songs:
            return None
        # 播放过半才触发（太早预取可能因用户切歌浪费流量）
        if duration <= 0 or progress < duration * PREFETCH_START_RATIO:
            return None
        self.state.prefetch_started = True
        n = len(songs)
        cur = current_index
        if n <= 1 or cur < 0:
            return None
        # 下一首索引（与 _music_next 播放模式逻辑保持一致，预取后直接复用）
        if play_mode == PLAY_MODE_RANDOM:
            random.seed()
            nidx = random.randrange(n)
            if nidx == cur:
                nidx = (nidx + 1) % n
        else:
            nidx = (cur + 1) % n
        if nidx == cur:
            return None
        song = songs[nidx]
        if song.source_type != "online":
            return None  # 本地文件加载快，无需预取
        slot = self.state.prefetch_slot
        if slot is not None and slot.get("seq") == self.state.prefetch_seq and slot.get("idx") == nidx:
            return None  # 该歌曲的预取已就绪，无需重复预取
        self.state.prefetch_seq += 1
        return PrefetchPlan(seq=self.state.prefetch_seq, index=nidx, song=song)

    def accept_prefetch(
        self,
        seq: int,
        idx: int,
        temp_path: str,
        result_info,
        origin_info,
        quality: str,
    ) -> bool:
        """预取下载完成（主线程）：序号仍有效则存进槽位，否则返回 False（丢弃临时文件）。

        槽位字段与原文逐字一致：``seq / idx / song_key / temp_path / result_info /
        origin_info / quality``；``song_key`` 用 ``(source, songmid)`` 指纹。
        """
        if seq != self.state.prefetch_seq:
            return False
        self.state.prefetch_slot = {
            "seq": seq,
            "idx": idx,
            "song_key": (origin_info.source, origin_info.songmid),
            "temp_path": temp_path,
            "result_info": result_info,
            "origin_info": origin_info,
            "quality": quality,
        }
        return True

    def valid_prefetch_slot(self, slot: Optional[dict], idx: int, song) -> bool:
        """判断上下文播放时能否直接消费预取结果（原文那段 6 条件 and 链）。

        条件：序号有效 + 索引匹配 + 歌曲指纹一致 + 临时文件仍在。
        """
        return bool(
            slot is not None
            and slot.get("seq") == self.state.prefetch_seq
            and slot.get("idx") == idx
            and slot.get("song_key") == (song.online_source, song.online_songmid)
            and slot.get("temp_path")
            and os.path.exists(slot["temp_path"])
        )

    def consume_prefetch_slot(self) -> None:
        """消费掉预取槽位（原文 `self._music_prefetch_slot = None`）。"""
        self.state.prefetch_slot = None

    # ── 歌单上下文推进 ───────────────────────────────────────────────

    def resolve_context_target(
        self,
        songs: Sequence,
        idx: int,
        prefetch_slot: Optional[dict],
    ) -> Optional[ContextTarget]:
        """歌单上下文里"该播哪一首"（原文 `_play_playlist_context_song` 的解析部分）。

        返回 None 表示索引越界（原文直接 return）。本地/在线的分支、本地文件缺失
        要跳过下一首、以及本地路径列表与索引的算法都在这里；界面侧只做 Tk 侧的
        高亮、递归跳歌与 mixer 播放。
        """
        if idx < 0 or idx >= len(songs):
            return None
        song = songs[idx]
        if song.source_type == "local":
            if not os.path.exists(song.file_path):
                return ContextTarget("local_missing", idx, song=song)
            # 构建本地文件列表供 _play_file 使用
            local_paths = [
                s.file_path for s in songs if s.source_type == "local" and os.path.exists(s.file_path)
            ]
            try:
                local_index = local_paths.index(song.file_path)
            except ValueError:
                local_index = 0
            return ContextTarget("local", idx, song=song, local_paths=local_paths, local_index=local_index)
        # 优先消费预取结果（序号 + 索引 + 歌曲指纹 + 文件存在校验），放完秒播
        if self.valid_prefetch_slot(prefetch_slot, idx, song):
            return ContextTarget("online", idx, song=song, prefetched=prefetch_slot)
        return ContextTarget("online", idx, song=song)

    # ── 目录扫描 ─────────────────────────────────────────────────────

    def scan_folder(self, folder: str, extensions) -> Optional[List[str]]:
        """递归枚举音频文件并按文件名（小写）排序（原文 `_music_scan_folder` 前半段）。

        返回 None 表示"扫描异常或没有音频文件"，两种情况原文都直接 return
        （异常路径会记一条 error 日志，这里照记）。
        """
        files = []
        try:
            for root, dirs, filenames in os.walk(folder):
                for fname in filenames:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in extensions:
                        files.append(os.path.join(root, fname))
        except Exception as e:
            logger.error(f"扫描文件夹失败: {folder}: {e}")
            return None
        if not files:
            return None
        files.sort(key=lambda f: os.path.basename(f).lower())
        return files


__all__ = [
    "ALL_PLAY_MODES",
    "ContextTarget",
    "DEFAULT_VOLUME",
    "EngineState",
    "FADE_INTERVAL_MS",
    "FADE_OUT_PAUSE",
    "FADE_OUT_STOP",
    "FADE_STEPS",
    "FadeStep",
    "MusicPlayerService",
    "MuteResult",
    "PLAY_MODE_IDS",
    "PLAY_MODE_LOOP_LIST",
    "PLAY_MODE_LOOP_SINGLE",
    "PLAY_MODE_NAMES",
    "PLAY_MODE_ORDER",
    "PLAY_MODE_RANDOM",
    "PLAY_MODE_SEQUENTIAL",
    "PREFETCH_START_RATIO",
    "PROGRESS_POLL_IDLE_MS",
    "PROGRESS_POLL_MS",
    "PrefetchPlan",
    "TrackEndAction",
    "get_mixer",
]
