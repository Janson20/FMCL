"""退出收尾接线（D-146）与"处理后文件时长"基准（D-19）的回归用例。

## D-146：为什么必须有这个文件
`ui/app_music.py:_music_cleanup()` 曾经是**死代码** —— 全仓 `grep _music_cleanup`
只有它自己的定义（`ui/app.py` / `main.py` / `app/` 都没有调用点），于是函数体里那句
`# 退出前强制写盘` 从来没有执行过：歌单只在 30s 周期保存
（`PERIODIC_SAVE_INTERVAL_MS`）里落盘，"加了几首歌然后 30 秒内退出"就可能丢改动。

## 断言为什么不是空的（本文件的三条钉子）
1. **正向**：走**真实**的退出链路 —— `on_closing()`（`main.py:474` 注册给
   `WM_DELETE_WINDOW` 的正是它）→ `ModernAppBase.destroy()`（真实实现）
   → `hasattr(self, "_music_stop")` → `_music_stop` → `_music_cleanup()`；
   断言磁盘上真的多出 `music.json`、音效/下载临时文件真的被删、周期保存定时器真的被停。
   唯一的替身是链尾的 `ctk.CTk.destroy`（真实调用需要一个 Tcl 解释器），
   替换后仍断言它被调用过 —— 也就是链路确实跑到了最后一环。
2. **反向（变异守卫）**：同一张宿主，只把 `_running` 留成 `True`
   （= 普通停止 / 切歌，不是退出）→ 上面那些副作用**一件都不能发生**。
   谁把收尾改成无条件执行，这条会红。
3. **幂等**：退出链路跑两次 → `_music_cleanup()` 仍然只跑一次。
   它内部会再调一次 `_music_stop(instant=True)`，没有一次性闸门就会无限递归。

## D-19：钉的是"基准跟得上实际在播的文件"
变速后播放的是处理过的临时文件，`poll_position()` 报的是它的秒数；
duration 若仍取原文件时长，进度条/seek/预取判据就整体偏掉（2 倍速下预取永不触发）。
`TestPlaybackDurationBasis` 钉纯函数的取值优先级，
`TestDurationReachesEngineAndPrefetch` 钉"这个值真的传到了引擎、并且预取判据跟着变"。

跑法::

    .venv\\Scripts\\python.exe -X utf8 -m pytest tests/test_music_cleanup_wiring.py -q
"""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any, Dict

import customtkinter as ctk
import pytest

import services.music_player as mp
import services.music_playlist as mpl
import services.music_smtc as music_smtc
import ui.app_music as app_music
from services.music_effects import AudioEffectProcessor, EffectSettings
from ui.app_base import ModernAppBase
from ui.app_handlers import EventHandlerMixin
from ui.app_music import MusicPlayerMixin

# ══════════════════════════════════════════════════════════════════════
# 最小替身
# ══════════════════════════════════════════════════════════════════════


class _Widget:
    """Tk 控件的最小替身：只实现停止/退出路径会碰到的方法。"""

    def __init__(self):
        self.text = ""
        self.value: Any = None
        self.configures: list = []

    def set(self, value):
        self.value = value

    def get(self):
        return self.value

    def configure(self, **kw):
        self.configures.append(kw)
        if "text" in kw:
            self.text = kw["text"]

    def winfo_exists(self):
        return True

    def winfo_ismapped(self):
        return True


class _FakeMixer:
    """假 mixer：只记录调用，绝不初始化真实音频设备。

    方法面按 `services.music_player` 会碰到的那些给（与 `tests/test_music_service.py`
    的 `FakeMusic` 同形，但本文件只需要"别抛异常 + 能看出播的是哪个文件"）。
    """

    class _Music:
        def __init__(self):
            self.calls: list = []
            self.pos_ms = 0

        def _call(self, name, *args, **kw):
            self.calls.append((name, args, kw))

        def load(self, path):
            self._call("load", path)

        def set_volume(self, v):
            self._call("set_volume", v)

        def play(self, start=0):
            self._call("play", start=start)

        def stop(self):
            self._call("stop")

        def unload(self):
            self._call("unload")

        def pause(self):
            self._call("pause")

        def unpause(self):
            self._call("unpause")

        def get_busy(self):
            self._call("get_busy")
            return False

        def get_pos(self):
            self._call("get_pos")
            return self.pos_ms

    def __init__(self):
        self.music = _FakeMixer._Music()


class _RecordingThread:
    """替换 `threading.Thread`：只登记"有人要起后台预取线程"，不真的起。"""

    started: list = []

    def __init__(self, target=None, args=(), daemon=False, **kw):
        self.target = target
        self.args = args
        self.daemon = daemon
        _RecordingThread.started.append(self)

    def start(self):
        pass


class _Host(EventHandlerMixin, MusicPlayerMixin, ModernAppBase):
    """最小宿主：不建 Tk，只把退出/播放链路会读到的属性与控件凑出来。

    刻意保留**真实**的 `ModernAppBase.destroy()` 与 `MusicPlayerMixin` 的全部实现：
    本文件要证明的是"退出链路真的走到了写盘"，如果把 `destroy` 也换成替身，
    就变成自己测自己了。MRO 与 `ui/app.py:23-37` 的相对顺序一致
    （`EventHandlerMixin` 在 `MusicPlayerMixin` 之前，两者都在 `ModernAppBase` 之前）。

    `tk = None` 是必需的：本宿主没有跑 `Tk.__init__`，而 `tkinter.Misc.__getattr__`
    会退化成 `getattr(self.tk, name)` —— 没有 `self.tk` 时它会自己递归自己
    （`_update_music_footer` 里的 `hasattr(self, "_music_footer_frame")` 就能把它打爆）。
    给它一个非 Tcl 的 `tk`，缺属性就正常抛 `AttributeError`（= "这个部件还没建"）。
    """

    tk = None

    def __init__(self, callbacks: Dict[str, Any]):
        self.timers: Dict[int, Any] = {}
        self.cancelled: list = []
        self._timer_seq = 0
        self.achievements: list = []
        self.callbacks = callbacks
        self._MusicPlayerMixin__init_music()
        self._music_init_done = True
        self._running = True
        self._music_quality_var = _Widget()
        self._music_quality_var.set("320k")
        self._music_progress_bar = _Widget()
        self._music_cur_label = _Widget()
        for name in (
            "_music_vol_slider",
            "_music_mini_vol",
            "_music_mute_btn",
            "_music_folder_label",
            "_music_song_count_label",
        ):
            setattr(self, name, _Widget())

    # ── Tk 侧最小替身（与 tests/test_music_service.py 的 Host 同一套办法）──

    def after(self, ms, fn=None, *args):
        self._timer_seq += 1
        self.timers[self._timer_seq] = {"ms": ms, "fn": fn, "args": args}
        return self._timer_seq

    def after_cancel(self, tid):
        self.cancelled.append(tid)
        self.timers.pop(tid, None)

    def _update_play_btn_ui(self):
        pass

    def _update_mute_btn_ui(self):
        pass

    def _update_now_playing_info(self):
        pass

    def _highlight_current_in_list(self):
        pass

    def _music_record_play_history_local(self, path):
        pass

    def _music_record_play_history_online(self, info):
        pass

    def _is_music_tab_active(self):
        return True

    def _rebuild_playlist_sidebar(self):
        pass

    def _trigger_ach(self, key):
        self.achievements.append(key)

    def _check_ach(self, key, value=True):
        self.achievements.append((key, value))


@pytest.fixture
def exit_setup(monkeypatch, tmp_path):
    """一张"已初始化、正在退出"的最小宿主；歌单写盘落到 tmp_path，不碰仓库 `data/`。"""
    data_path = tmp_path / "data" / "music.json"
    # 歌单落盘路径来自服务模块级函数，这里换成临时路径，
    # 避免用例在仓库里写 data/music.json（那是用户数据）。
    monkeypatch.setattr(mpl, "get_music_data_path", lambda: data_path)
    monkeypatch.setattr(music_smtc, "_winsdk_available", False)
    # 同一个 mixer 实例返回给所有调用方，这样用例能回看"到底让 mixer 加载了哪个文件"
    mixer = _FakeMixer()
    monkeypatch.setattr(mp, "get_mixer", lambda: mixer)
    saved: list = []
    host = _Host({"save_music_state": saved.append})
    host._music_playlist_manager.create_playlist("E组退出写盘")
    tk_destroyed: list = []
    # 链尾替身：真实 `ctk.CTk.destroy()` 需要一个 Tcl 解释器。
    # 换掉之后仍断言它被调用过，保证"链路跑到了最后一环"这一点没有被放过。
    monkeypatch.setattr(ctk.CTk, "destroy", lambda self: tk_destroyed.append(self))
    return SimpleNamespace(
        host=host, data_path=data_path, saved=saved, tk_destroyed=tk_destroyed, mixer=mixer, tmp=tmp_path
    )


# ══════════════════════════════════════════════════════════════════════
# D-146：退出路径必须真的收尾
# ══════════════════════════════════════════════════════════════════════


class TestExitPathFlushesPlaylist:
    def test_退出链路会强制写盘并清临时文件(self, exit_setup, monkeypatch):
        s = exit_setup
        # 关键前提：写盘必须在 **pygame 不可用**时也发生 —— 这条钉住钩子的**位置**
        # （在 `_music_stop` 顶部 `if not self._music_engine.available: return` 之前）。
        # pygame 不可用的环境里歌单照样需要落盘，早退不能把收尾一起跳掉。
        monkeypatch.setattr(mp, "_pygame_import_error", ImportError("no pygame"))
        fx = s.tmp / "fmcl_fx_1.wav"
        dl = s.tmp / "fmcl_dl_1.mp3"
        fx.write_bytes(b"RIFF")
        dl.write_bytes(b"ID3")
        s.host._music_effects_processed_files.append(str(fx))
        s.host._music_temp_files.append(str(dl))
        s.host._music_periodic_save_id = s.host.after(30_000, lambda: None)

        assert not s.data_path.exists()  # 收尾之前磁盘上什么都没有

        s.host.on_closing()  # main.py:474 注册给 WM_DELETE_WINDOW 的正是这个处理器

        assert s.data_path.exists(), "退出后歌单没有落盘：D-146 又回来了（_music_cleanup 没有调用点）"
        text = s.data_path.read_text(encoding="utf-8")
        assert "E组退出写盘" in text, "落盘的 music.json 里没有刚建的歌单"
        # 播放状态也走了一次（_save_music_state → callbacks["save_music_state"]）
        assert s.saved, "退出时没有回写播放状态"
        assert s.saved[0]["music_play_mode"] == "loop_list"
        # 临时文件：音效处理产物 + 下载缓存，两条清理都真的跑了
        assert not fx.exists(), "音效临时文件没有被清理"
        assert not dl.exists(), "下载临时文件没有被清理"
        # 周期保存的 after 链被停掉，不再有 30s 后写盘的残留定时器
        assert s.host._music_periodic_save_id is None
        assert s.tk_destroyed, "退出链路没有走到 ctk.CTk.destroy()"

    def test_有_pygame_时退出也一样写盘且部件仍在(self, exit_setup):
        """pygame 可用时的正常分支：收尾那一刻**控件还活着**（就是它必须在这里的原因）。"""
        s = exit_setup
        if mp._pygame_import_error is not None:
            pytest.skip("本机没有 pygame，无法走播放分支")
        s.host.on_closing()
        assert s.data_path.exists()
        # `_music_stop(instant=True)` 的完整体要写进度条与时间标签；
        # 这两条能过，说明收尾发生在"窗口部件已被销毁"之前（destroy 的收尾段）。
        assert s.host._music_progress_bar.value == 0
        assert s.host._music_cur_label.text == "0:00"

    def test_普通停止不会写盘(self, exit_setup):
        """反向守卫：`_running` 仍是 True（按了停止/切歌）→ 一个副作用都不许有。"""
        s = exit_setup
        fx = s.tmp / "fmcl_fx_2.wav"
        fx.write_bytes(b"RIFF")
        s.host._music_effects_processed_files.append(str(fx))
        s.host._music_periodic_save_id = s.host.after(30_000, lambda: None)
        timer_id = s.host._music_periodic_save_id

        s.host._music_stop(instant=True)  # 不是退出：没有 destroy()

        assert not s.data_path.exists(), "普通停止也写盘了：收尾判据失效（应该是 `_running` 为假）"
        assert s.saved == []
        assert fx.exists(), "普通停止就删了音效临时文件"
        assert s.host._music_periodic_save_id == timer_id, "普通停止停掉了周期保存"
        assert s.tk_destroyed == []

    def test_退出收尾只做一次且不会递归(self, exit_setup, monkeypatch):
        """`_music_cleanup()` 内部会再调 `_music_stop`，一次性闸门必须挡住第二次。"""
        s = exit_setup
        calls: list = []
        real = s.host._music_cleanup

        def spy():
            calls.append(1)
            real()

        monkeypatch.setattr(s.host, "_music_cleanup", spy)
        s.host._running = False
        s.host._music_stop(instant=True)
        s.host._music_stop(instant=True)

        assert len(calls) == 1, f"退出收尾跑了 {len(calls)} 次（应当只有一次，否则会无限递归）"


# ══════════════════════════════════════════════════════════════════════
# D-19：时长基准 = 实际播放的那个文件
# ══════════════════════════════════════════════════════════════════════


def _reader(table):
    """一个"读文件时长"的替身：按路径查表，查不到返回 0（模拟 mutagen 读不出来）。"""

    def read(path):
        return table.get(path, 0)

    return read


class TestPlaybackDurationBasis:
    """纯函数 `_music_playback_duration` 的取值优先级（不需要 Tk、不需要 mutagen）。"""

    def test_没有处理生效就原样返回原时长(self):
        settings = EffectSettings(speed_enabled=True, speed_rate=2.0)
        read = _reader({})
        assert app_music._music_playback_duration(240.0, settings, "a.mp3", "a.mp3", read) == 240.0
        # 连读都不该读：这条路是"音效全关"的默认路径，不能平白多一次 I/O
        assert app_music._music_playback_duration(240.0, settings, "a.mp3", "", read) == 240.0

    def test_优先读处理后文件的实际时长(self):
        settings = EffectSettings(speed_enabled=True, speed_rate=2.0)
        read = _reader({"fx.wav": 137.5})
        got = app_music._music_playback_duration(240.0, settings, "a.mp3", "fx.wav", read)
        assert got == pytest.approx(137.5), "有实际文件可读时，就不该再用折算值"

    def test_读不到时按倍速折算(self):
        settings = EffectSettings(speed_enabled=True, speed_rate=2.0)
        # 读不出来（文件被清掉 / 容器不被支持）→ 原长 / rate
        assert app_music._music_playback_duration(240.0, settings, "a.mp3", "fx.wav", _reader({})) == pytest.approx(
            120.0
        )

    def test_读时长抛异常也降级到折算(self):
        settings = EffectSettings(speed_enabled=True, speed_rate=1.5)

        def boom(path):
            raise OSError("文件没了")

        assert app_music._music_playback_duration(300.0, settings, "a.mp3", "fx.wav", boom) == pytest.approx(200.0)

    def test_只有变速会改长度(self):
        """变调走 `_spawn().set_frame_rate()` 往返，长度不变 —— 不许把它算进折算。"""
        pitch_only = EffectSettings(pitch_enabled=True, pitch_semitones=5.0)
        assert app_music._music_playback_duration(240.0, pitch_only, "a.mp3", "fx.wav", _reader({})) == 240.0
        # 变速开关关着、也没处理过：同理不折算
        speed_off = EffectSettings(speed_enabled=False, speed_rate=2.0)
        assert app_music._music_playback_duration(240.0, speed_off, "a.mp3", "fx.wav", _reader({})) == 240.0


class TestDurationReachesEngineAndPrefetch:
    """D-19 的接线端：这个值必须真的传进 `begin_*_playback`，并且预取判据跟着走。"""

    @pytest.fixture
    def playing_setup(self, exit_setup, monkeypatch):
        """一张 2.0 倍速播放中的宿主：原文件 240s，处理结果 120s。"""
        s = exit_setup
        s.original = s.tmp / "a.mp3"
        s.processed = s.tmp / "fmcl_fx_9.wav"
        s.original.write_bytes(b"ID3")
        s.processed.write_bytes(b"RIFF")
        if mp._pygame_import_error is not None:
            pytest.skip("本机没有 pygame，无法走播放分支")
        monkeypatch.setattr(s.host, "_get_metadata", lambda p: {"duration": 240.0 if p == str(s.original) else 120.0})
        s.host._music_effects = AudioEffectProcessor(EffectSettings(speed_enabled=True, speed_rate=2.0))
        monkeypatch.setattr(s.host._music_effects, "process", lambda p, suffix=".wav": str(s.processed))
        return s

    def test_2倍速下进度基准是处理后时长(self, playing_setup):
        s = playing_setup
        s.host._play_file(str(s.original))

        # 引擎状态是唯一真相，界面属性只是它的镜像
        assert s.host._music_engine.state.duration == pytest.approx(120.0)
        assert s.host._music_duration == pytest.approx(120.0)
        # 播的确实是处理后的文件（这一条是前提，不是结论）：
        # `begin_local_playback` 的第一个参数仍是原文件路径（它是"显示/歌词用"的那个），
        # 真正交给 mixer 的是 processed_path，所以要看 mixer 的 load 调用。
        assert ("load", (str(s.processed),), {}) in s.mixer.music.calls
        # 进度条读点是 `progress_percent(pos, duration)`：120s 的曲子在 90s 处应当是 75%
        assert s.host._music_engine.progress_percent(90.0, s.host._music_duration) == pytest.approx(75.0)

    def test_2倍速下预取判据会被触发(self, playing_setup, monkeypatch):
        s = playing_setup
        s.host._play_file(str(s.original))
        assert s.host._music_duration == pytest.approx(120.0)

        s.host._music_playlist_context_songs = [
            SimpleNamespace(source_type="online", online_songmid="s1", online_source="wy"),
            SimpleNamespace(source_type="online", online_songmid="s2", online_source="wy"),
        ]
        s.host._music_playlist_context_idx = 0
        s.host._music_play_mode = mp.PLAY_MODE_LOOP_LIST
        # 2 倍速下播放位置最多只能走到 120s；取 61s = 处理后半程，但按原时长 240s 只算 25%
        s.host._music_progress = 61.0

        _RecordingThread.started = []
        monkeypatch.setattr(app_music.threading, "Thread", _RecordingThread)
        s.host._music_maybe_prefetch_next()
        assert _RecordingThread.started, "2 倍速下预取没有被触发（duration 又退回原文件时长了）"
        assert s.host._music_engine.state.prefetch_started is True

        # 反证：同一个 progress，把基准换回原文件时长 → 判据为假，不会触发。
        # 这一步保证上面那条不是"无论怎样都会过"的空断言。
        s.host._music_invalidate_prefetch()
        s.host._music_duration = 240.0
        _RecordingThread.started = []
        s.host._music_maybe_prefetch_next()
        assert _RecordingThread.started == [], "原时长基准下也触发了预取，说明这条断言没测到东西"

    def test_在线播放的基准也来自处理后时长(self, playing_setup):
        s = playing_setup
        info = SimpleNamespace(interval=240.0, name="n", singer="s")
        s.host._play_online_file(str(s.original), info, quality="320k")
        assert s.host._music_engine.state.duration == pytest.approx(120.0)
        assert s.host._music_duration == pytest.approx(120.0)
        assert s.host._music_engine.state.current_quality == "320k"
