"""音乐音效引擎 - EQ均衡器 / 混响 / 变调 / 变速

基于 pydub + numpy/scipy 实现的离线音频预处理音效系统。
音频文件在播放前经过效果链处理后写入临时文件供 pygame 加载。

效果链顺序: 均衡器 → 混响 → 变调 → 变速
"""
import os
import tempfile
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Tuple
import threading

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
try:
    from pydub import AudioSegment
    from pydub.effects import speed_change
    _pydub_available = True
except ImportError:
    pass


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
    reverb_delay_ms: float = 60.0      # 延迟 (10-200ms)
    reverb_decay: float = 0.4          # 衰减 (0.1-0.9)
    reverb_wet_level: float = 0.3      # 湿信号比例 (0.0-1.0)

    # 变调
    pitch_enabled: bool = False
    pitch_semitones: float = 0.0       # 半音偏移 (-12~+12)

    # 变速
    speed_enabled: bool = False
    speed_rate: float = 1.0            # 播放速率 (0.5~2.0)

    # 声像
    pan_enabled: bool = False
    pan_value: float = 0.0             # -1.0(左) ~ 1.0(右)

    @property
    def has_any_enabled(self) -> bool:
        return (
            self.eq_enabled and any(abs(g) > 0.01 for g in self.eq_gains)
        ) or self.reverb_enabled or self.pitch_enabled or self.speed_enabled

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


class AudioEffectProcessor:
    """音频效果处理器

    将输入音频文件通过效果链处理后输出到临时文件。
    需要 pydub (ffmpeg) 和可选的 numpy/scipy。

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
        return _pydub_available

    def process(self, input_path: str, suffix: str = ".wav") -> Optional[str]:
        """处理音频文件，返回处理后临时文件路径

        Args:
            input_path: 输入音频文件路径
            suffix: 输出文件后缀

        Returns:
            处理后临时文件路径，或None（无效果或处理失败时返回原文件路径）
        """
        if not self.settings.has_any_enabled or not _pydub_available:
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
                data=filtered.tobytes(),
                sample_width=2,
                frame_rate=sample_rate,
                channels=audio.channels,
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

            dry_audio = audio
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
                data=mixed.tobytes(),
                sample_width=2,
                frame_rate=audio.frame_rate,
                channels=audio.channels,
            )
        except Exception as e:
            logger.debug(f"混响处理失败: {e}")
            return audio

    # ── 变调 / 变速 ──────────────────────────────────

    def _apply_pitch(self, audio: "AudioSegment") -> "AudioSegment":
        try:
            new_rate = int(audio.frame_rate * (2.0 ** (self.settings.pitch_semitones / 12.0)))
            return audio._spawn(audio.raw_data, overrides={
                "frame_rate": new_rate
            }).set_frame_rate(audio.frame_rate)
        except Exception:
            return audio

    def _apply_speed(self, audio: "AudioSegment", original_duration: int) -> "AudioSegment":
        try:
            return speed_change(audio, self.settings.speed_rate)
        except Exception:
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
        except Exception:
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
