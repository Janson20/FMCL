"""音乐音效引擎 - EQ均衡器 / 混响 / 变调 / 变速

基于 pydub + numpy/scipy 实现的离线音频预处理音效系统。
音频文件在播放前经过效果链处理后写入临时文件供 pygame 加载。

效果链顺序: 均衡器 → 混响 → 变调 → 变速

外部依赖（D-147）：**音效处理需要外部 ffmpeg 可执行文件**。pydub 只是 Python 侧的容器，
真正解码/编码的是 ffmpeg 进程；它不是 PyPI 包，所以不在 pyproject 的依赖表里（本项目也
不随附来源不明的二进制），缺失时整条音效链降级为"播原文件"。探测方式就是
`shutil.which("ffmpeg")`，结果见 `FFMPEG_PATH` / `ffmpeg_available()`；每次跳过都会打一条
能定位的日志，界面用 `AudioEffectProcessor.available` 或 `ffmpeg_available()` 查询。
"""

import logging
import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger("music_effects")

_numpy_available = False
try:
    import numpy as np

    _numpy_available = True
except ImportError:
    pass

_scipy_available = False
try:
    from scipy import signal as scipy_signal

    _scipy_available = True
except ImportError:
    pass

_pydub_available = False
#: pydub 导入失败的原因（"" = 导入成功）。为什么留原因而不只留布尔：整条音效链以前是
#: 静默失效的（用户看到的是"设了没反应"），把缺哪个符号写进日志与 `unavailable_reason()`
#: 才能一眼定位。实测 pydub 0.25.1 的 `pydub.effects` 里没有 `speed_change`，
#: 所以本模块在**所有**环境里都是 `_pydub_available = False`（见报告 D-148）。
_pydub_error = ""
try:
    from pydub import AudioSegment
    from pydub.effects import speed_change

    _pydub_available = True
except ImportError as e:
    _pydub_error = f"{type(e).__name__}: {e}"


def _find_ffmpeg() -> Optional[str]:
    """在 PATH 里找外部 ffmpeg（找不到再试 avconv —— pydub 认这两个名字）。"""
    return shutil.which("ffmpeg") or shutil.which("avconv")


#: 外部 ffmpeg 可执行文件路径；None = 探测失败（音效链整体不可用，见 D-147）。
#: 进程启动时探测一次：运行中才装好 ffmpeg 的话要重启启动器才会被看到。
FFMPEG_PATH: Optional[str] = _find_ffmpeg()


def ffmpeg_available() -> bool:
    """外部 ffmpeg 在不在 —— 界面与测试都问这个布尔，不要自己再去查 PATH（D-147）。"""
    return FFMPEG_PATH is not None


def unavailable_reason() -> str:
    """音效链今天为什么跑不了（"" = 可用）。**只给日志与诊断**，界面文案走 i18n。"""
    reasons = []
    if not _pydub_available:
        reasons.append(f"pydub 不可用（{_pydub_error}）")
    if not ffmpeg_available():
        reasons.append("ffmpeg 不在 PATH（见 README 的环境要求）")
    return "；".join(reasons)


# EQ 预设: 10 段频率中心 (Hz)
EQ_FREQS = [31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]

# EQ 增益范围
EQ_GAIN_MIN = -15.0
EQ_GAIN_MAX = 15.0

# 变调范围 (半音)
PITCH_MIN = -12.0
PITCH_MAX = 12.0

# 变速范围 (倍率)
SPEED_MIN = 0.5
SPEED_MAX = 2.0


@dataclass
class EffectSettings:
    """音效设置数据结构"""

    # EQ
    eq_enabled: bool = False
    eq_gains: List[float] = field(default_factory=lambda: [0.0] * 10)

    # 混响相关
    reverb_enabled: bool = False
    reverb_delay_ms: float = 60.0  # 延迟 (10-200ms)
    reverb_decay: float = 0.4  # 衰减 (0.1-0.9)
    reverb_wet_level: float = 0.3  # 湿信号比例 (0.0-1.0)

    # 变调
    pitch_enabled: bool = False
    pitch_semitones: float = 0.0  # 半音偏移 (-12~+12)

    # 变速
    speed_enabled: bool = False
    speed_rate: float = 1.0  # 播放速率 (0.5~2.0)

    # 声像
    pan_enabled: bool = False
    pan_value: float = 0.0  # -1.0(左) ~ 1.0(右)

    @property
    def has_any_enabled(self) -> bool:
        return (
            (self.eq_enabled and any(abs(g) > 0.01 for g in self.eq_gains))
            or self.reverb_enabled
            or self.pitch_enabled
            or self.speed_enabled
        )

    def to_dict(self) -> dict:
        return {
            "eq_enabled": self.eq_enabled,
            "eq_gains": self.eq_gains,
            "reverb_enabled": self.reverb_enabled,
            "reverb_delay_ms": self.reverb_delay_ms,
            "reverb_decay": self.reverb_decay,
            "reverb_wet_level": self.reverb_wet_level,
            "pitch_enabled": self.pitch_enabled,
            "pitch_semitones": self.pitch_semitones,
            "speed_enabled": self.speed_enabled,
            "speed_rate": self.speed_rate,
            "pan_enabled": self.pan_enabled,
            "pan_value": self.pan_value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EffectSettings":
        s = cls()
        if data:
            s.eq_enabled = data.get("eq_enabled", False)
            s.eq_gains = data.get("eq_gains", [0.0] * 10)
            s.reverb_enabled = data.get("reverb_enabled", False)
            s.reverb_delay_ms = data.get("reverb_delay_ms", 60.0)
            s.reverb_decay = data.get("reverb_decay", 0.4)
            s.reverb_wet_level = data.get("reverb_wet_level", 0.3)
            s.pitch_enabled = data.get("pitch_enabled", False)
            s.pitch_semitones = data.get("pitch_semitones", 0.0)
            s.speed_enabled = data.get("speed_enabled", False)
            s.speed_rate = data.get("speed_rate", 1.0)
            s.pan_enabled = data.get("pan_enabled", False)
            s.pan_value = data.get("pan_value", 0.0)
        return s


def effective_duration(original_duration: float, speed_rate: float) -> float:
    """按变速倍率把**原文件时长**折算成播放时长（秒）—— D-19 的进度基准。

    只有变速会改长度（pydub 的变速 = 改采样率且不重采样，长度因此变成 original/rate）；
    EQ 与混响不改长度，变调是 `_spawn` + `set_frame_rate` 的一次往返、长度也不变。
    倍率非法（<=0）或等于 1.0、以及原时长本就非正时原样返回 —— 界面侧的时长宁可等于原值，
    也不能变成负数或 0（那会让进度条与 seek 出现除零/倒退）。
    """
    if original_duration <= 0 or not speed_rate or speed_rate <= 0 or abs(speed_rate - 1.0) <= 0.001:
        return float(original_duration)
    return float(original_duration) / float(speed_rate)


def playback_duration(
    original_duration: float, settings: EffectSettings, processed_path: str, input_path: str
) -> float:
    """这次播放**实际**该用的时长（秒）—— D-19 的调用点用它，而不是原文件时长。

    `processed_path == input_path` 表示这次没有产出处理文件（音效全关 / 没有 ffmpeg /
    处理失败降级），此时长度就是原文件长度；否则按 `effective_duration()` 折算。
    进度条百分比、拖动定位、预取阈值三处读的是同一个时长值，改对这一处等于同时修三处。
    """
    if not processed_path or processed_path == input_path or not settings.speed_enabled:
        return float(original_duration)
    return effective_duration(original_duration, settings.speed_rate)


class AudioEffectProcessor:
    """音频效果处理器

    将输入音频文件通过效果链处理后输出到临时文件。
    依赖：pydub + **外部 ffmpeg**（真正做解码/编码的进程），以及可选的 numpy/scipy
    （EQ 需要两者，混响只需要 numpy）。ffmpeg 缺失时整体降级为播原文件（D-147），
    可用性用 `available` 查询；处理失败一律返回原文件路径，播放不会因此中断。

    使用示例:
        processor = AudioEffectProcessor()
        processor.settings.eq_enabled = True
        processor.settings.eq_gains[3] = 5.0   # 250Hz +5dB
        output_path = processor.process("input.mp3")
    """

    def __init__(self, settings: Optional[EffectSettings] = None):
        self.settings = settings or EffectSettings()
        self._temp_files: List[str] = []

    @property
    def available(self) -> bool:
        """音效链今天能不能真的跑起来：pydub 装好 **且** 外部 ffmpeg 找得到（D-147）。

        界面拿它决定"音效面板是否可用 / 是否显示不可用提示"，不要自己 `shutil.which`。
        """
        return _pydub_available and ffmpeg_available()

    # D-13（本轮只记录，不改行为）：下面的 process() 是**主线程同步**调用的 —— 整曲解码
    # + 滤波 + 导出，用户一旦打开任一音效开关，切歌就会卡住界面数秒（有 ffmpeg 的机器上
    # 才会发生）。已定方案 A：投给 app/tasks.py 的 TaskRunner 异步处理，**排期阶段 3.18**。
    # 异步化时必须同时保住三件事，否则会退化成更糟的形态：
    #   1) 设置快照：worker 拿到的是调用瞬间的 EffectSettings 副本，不能读共享可变设置；
    #   2) 临时文件所有权：谁 mkstemp 谁登记 _temp_files，清理只在拥有者线程里做；
    #   3) 失败降级：任何异常都返回原文件路径让播放继续（就是下面 except 里的语义）。
    def process(self, input_path: str, suffix: str = ".wav") -> Optional[str]:
        """处理音频文件，返回处理后临时文件路径

        Args:
            input_path: 输入音频文件路径
            suffix: 输出文件后缀

        Returns:
            处理后临时文件路径，或None（无效果或处理失败时返回原文件路径）
        """
        if not self.settings.has_any_enabled:
            return input_path
        if not self.available:
            # D-147：依赖缺失以前是"静默返回原文件"，用户看到的是"设了没反应"。
            logger.warning("音效处理被跳过（改播原文件）: %s —— %s", input_path, unavailable_reason())
            return input_path

        try:
            audio = AudioSegment.from_file(input_path)
            original_duration = len(audio)

            # 1. 均衡器 (EQ)
            if self.settings.eq_enabled and _numpy_available and _scipy_available:
                audio = self._apply_eq(audio)

            # 2. 混响
            if self.settings.reverb_enabled and _numpy_available:
                audio = self._apply_reverb(audio)

            # 3. 变调 (需 ffmpeg)
            if self.settings.pitch_enabled and abs(self.settings.pitch_semitones) > 0.01:
                audio = self._apply_pitch(audio)

            # 4. 变速
            if self.settings.speed_enabled and abs(self.settings.speed_rate - 1.0) > 0.001:
                audio = self._apply_speed(audio, original_duration)

            # 5. 声像
            if self.settings.pan_enabled:
                audio = self._apply_pan(audio)

            # 写入临时文件
            fd, temp_path = tempfile.mkstemp(suffix=suffix, prefix="fmcl_fx_")
            os.close(fd)
            audio.export(temp_path, format=suffix.lstrip("."))
            self._temp_files.append(temp_path)
            return temp_path

        except Exception as e:
            logger.warning(f"音效处理失败: {e}")
            return input_path

    # ── EQ 均衡器 ────────────────────────────────────

    def _apply_eq(self, audio: "AudioSegment") -> "AudioSegment":
        """应用10段图形均衡器"""
        try:
            samples = np.array(audio.get_array_of_samples(), dtype=np.float32)
            if audio.channels == 2:
                samples = samples.reshape((-1, 2))
            sample_rate = audio.frame_rate

            # 串联多个双二阶滤波器
            filtered = samples.copy()
            nyquist = sample_rate / 2.0
            for i, gain_db in enumerate(self.settings.eq_gains):
                if abs(gain_db) < 0.05:
                    continue
                freq = EQ_FREQS[i]
                if freq >= nyquist:
                    continue
                # 使用参数均衡器: 中心频率 / Q=1.0 / 增益
                Q = 1.0
                w0 = 2.0 * np.pi * freq / sample_rate
                alpha = np.sin(w0) / (2.0 * Q)
                A = 10.0 ** (gain_db / 40.0)

                b0 = 1.0 + alpha * A
                b1 = -2.0 * np.cos(w0)
                b2 = 1.0 - alpha * A
                a0 = 1.0 + alpha / A
                a1 = -2.0 * np.cos(w0)
                a2 = 1.0 - alpha / A

                b = np.array([b0 / a0, b1 / a0, b2 / a0])
                a = np.array([1.0, a1 / a0, a2 / a0])

                if audio.channels == 2:
                    left = scipy_signal.lfilter(b, a, filtered[:, 0])
                    right = scipy_signal.lfilter(b, a, filtered[:, 1])
                    filtered = np.column_stack((left, right))
                else:
                    filtered = scipy_signal.lfilter(b, a, filtered)

            # 限制幅度
            filtered = np.clip(filtered, -32768, 32767)
            filtered = filtered.astype(np.int16)

            # 重建 AudioSegment
            new_audio = AudioSegment(
                data=filtered.tobytes(), sample_width=2, frame_rate=sample_rate, channels=audio.channels
            )
            return new_audio
        except Exception as e:
            logger.debug(f"EQ处理失败: {e}")
            return audio

    # ── 混响 ─────────────────────────────────────────

    def _apply_reverb(self, audio: "AudioSegment") -> "AudioSegment":
        """简单延迟混响"""
        try:
            delay_ms = self.settings.reverb_delay_ms
            decay = max(0.01, min(0.95, self.settings.reverb_decay))
            wet = max(0.0, min(1.0, self.settings.reverb_wet_level))

            delay_samples = int(delay_ms * audio.frame_rate / 1000.0)

            if audio.channels == 2:
                raw = np.array(audio.get_array_of_samples(), dtype=np.float32).reshape((-1, 2))
                wet_signal = np.zeros_like(raw)
                wet_signal[delay_samples:] = raw[:-delay_samples] * decay
                mixed = raw * (1.0 - wet) + wet_signal * wet
            else:
                raw = np.array(audio.get_array_of_samples(), dtype=np.float32)
                wet_signal = np.zeros_like(raw)
                wet_signal[delay_samples:] = raw[:-delay_samples] * decay
                mixed = raw * (1.0 - wet) + wet_signal * wet

            mixed = np.clip(mixed, -32768, 32767).astype(np.int16)
            return AudioSegment(
                data=mixed.tobytes(), sample_width=2, frame_rate=audio.frame_rate, channels=audio.channels
            )
        except Exception as e:
            logger.debug(f"混响处理失败: {e}")
            return audio

    # ── 变调 / 变速 ──────────────────────────────────

    def _apply_pitch(self, audio: "AudioSegment") -> "AudioSegment":
        # 长度不变：_spawn 改采样率后立刻 set_frame_rate 还原，是一次往返。
        # D-19 的时长折算只认变速，别把变调也算进去。
        try:
            new_rate = int(audio.frame_rate * (2.0 ** (self.settings.pitch_semitones / 12.0)))
            return audio._spawn(audio.raw_data, overrides={"frame_rate": new_rate}).set_frame_rate(audio.frame_rate)
        except Exception as e:
            logger.warning(f"变调处理失败，改播未变调的音频: {e}")
            return audio

    def _apply_speed(self, audio: "AudioSegment", original_duration: int) -> "AudioSegment":
        """变速，并用 original_duration 校验长度（D-25：这个形参以前传进来没人读）。

        为什么值得校验：变速后的**实际长度**就是 D-19 的进度基准，一旦它与
        ``original_duration / rate`` 差得多，进度条、拖动定位、预取会一起跑偏 ——
        所以这里留一条可定位的日志；真正的折算交给 `effective_duration()`，
        本函数只处理音频、不改设置。
        """
        try:
            changed = speed_change(audio, self.settings.speed_rate)
            expected = int(round(original_duration / self.settings.speed_rate))
            if abs(len(changed) - expected) > max(50, int(expected * 0.01)):
                logger.warning(
                    "变速后长度与预期不符: 原 %dms / 速率 %.3f → 期望 %dms，实际 %dms",
                    original_duration, self.settings.speed_rate, expected, len(changed),
                )
            return changed
        except Exception as e:
            logger.warning(f"变速处理失败，改播未变速的音频: {e}")
            return audio

    # ── 声像 ─────────────────────────────────────────

    def _apply_pan(self, audio: "AudioSegment") -> "AudioSegment":
        if audio.channels != 2:
            return audio
        try:
            pan = max(-1.0, min(1.0, self.settings.pan_value))
            left_gain = min(1.0, 1.0 - pan) if pan < 0 else 1.0
            right_gain = min(1.0, 1.0 + pan) if pan > 0 else 1.0
            if pan < 0:
                right_gain = max(0.0, right_gain)
            else:
                left_gain = max(0.0, left_gain)
            return audio.apply_gain_stereo(left_gain, right_gain)
        except Exception as e:
            logger.warning(f"声像处理失败，改播未做声像的音频: {e}")
            return audio

    # ── 清理 ─────────────────────────────────────────

    def cleanup(self):
        for fp in self._temp_files:
            try:
                if os.path.exists(fp):
                    os.remove(fp)
            except Exception:
                pass
        self._temp_files.clear()

    def reset(self):
        self.settings = EffectSettings()
        self.cleanup()
