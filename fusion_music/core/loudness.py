"""响度测算（纯函数 + 流式计量器，不依赖 Qt / numpy）。

「音量均衡」的算法核心，实现 EBU R128 / ITU-R BS.1770-4 的口径：

1. **K 加权**：两级 biquad 串联 —— RLB 高通（38.1 Hz）+ 高频搁架（+4 dB @ 1.68 kHz）。
   系数用**模拟原型 + 预畸变双线性变换**在任意采样率上现算（与 libebur128
   同一套公式），因此在 48 kHz 上与标准附录给的系数逐位吻合，在解码器给的
   8 kHz 上同样成立。
2. **分块**：400 ms 一块、100 ms 一步（75% 重叠）。这里按「100 ms 段求和 +
   4 段滑窗」实现，不需要环形缓冲，内存与曲目长度无关。
3. **门限**：先过 -70 LUFS 绝对门限，再按「已过门限块的平均响度 − 10 LU」过相对门限
   （相对门限必须**在 dB 域**取平均，这是 BS.1770 最容易写反的一处）。
4. **积分响度** = -0.691 + 10·log10(过门限块的均方平均)。

多声道按标准「各声道均方相加」处理：立体声要解码成两个声道分别算再相加，
**不能**先混成单声道再算 —— 那样会平白低 3 dB（第一版就是这么错的，
所以解码侧统一请求立体声，见 :mod:`app.core.loudness_service`）。

分析结果是**流式**的：随时可以取当前结果，所以「只分析前 90 秒」与
「整曲分析」走的是同一条路径，中断也拿得到有意义的值。

纯 Python、不碰 Qt 与文件，是为了能在 ``tests/test_core.py`` 里离线回归：
响度算错不会崩，只会让「均衡」变成「忽大忽小」，界面上看不出来。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple, Union

#: 缓存里的测算版本。算法或口径变了就 +1，老缓存自动作废（见 loudness_store）
ANALYSIS_VERSION = 1

#: 目标响度（LUFS）。-14 是流媒体通行的口径
DEFAULT_TARGET_LUFS = -14.0

#: 绝对门限（BS.1770-4）
ABSOLUTE_GATE_LUFS = -70.0
#: 相对门限：低于「过门限块平均响度 - 10 LU」的块不计入
RELATIVE_GATE_LU = 10.0
#: 校准常数（BS.1770-4 的 -0.691 dB）
CALIBRATION_DB = -0.691

BLOCK_SECONDS = 0.4
BLOCK_STEP_SECONDS = 0.1          # 400ms 块 / 100ms 步进 = 75% 重叠
#: 短于一个完整块的曲子（铃声、试听片段）至少要有这么长才勉强能测
MIN_BLOCK_SECONDS = 0.1

#: 解码后分析的采样率。8 kHz 已覆盖 K 加权的关注频段，
#: 纯 Python 的逐样本滤波成本也随之降到 1/6（见 tools/probe_loudness_cost.py）
ANALYSIS_RATE = 8000

Biquad = Tuple[float, float, float, float, float]   # b0 b1 b2 a1 a2（已归一化）
Samples = Union[Sequence[float], Sequence[Sequence[float]]]

#: 样本量级的上界，用来把 NaN / inf 一并挡掉（合法样本远小于它）
_SANE_LIMIT = 1e30

# ── K 加权的模拟原型参数（与标准 48kHz 系数等价的解析设计）──────
_PRE_FILTER = (1681.974450955533, 3.999843853973347, 0.7071752369554196)
#: 搁架的指数是标准实现里拟合出来的常数，不是 0.5
_PRE_FILTER_VB_EXP = 0.4996667741545416
_RLB_FILTER = (38.13547087602444, 0.5003270373238773)


def high_shelf_coeffs(rate: int, f0: float, gain_db: float, q: float,
                      vb_exp: float = _PRE_FILTER_VB_EXP) -> Biquad:
    """BS.1770 的一级滤波器（高频搁架），预畸变双线性变换。"""
    k = math.tan(math.pi * f0 / rate)
    vh = 10.0 ** (gain_db / 20.0)
    vb = vh ** vb_exp
    a0 = 1.0 + k / q + k * k
    return (
        (vh + vb * k / q + k * k) / a0,
        2.0 * (k * k - vh) / a0,
        (vh - vb * k / q + k * k) / a0,
        2.0 * (k * k - 1.0) / a0,
        (1.0 - k / q + k * k) / a0,
    )


def high_pass_coeffs(rate: int, f0: float, q: float) -> Biquad:
    """BS.1770 的二级滤波器（RLB 高通）：b 恒为 [1, -2, 1]。"""
    k = math.tan(math.pi * f0 / rate)
    a0 = 1.0 + k / q + k * k
    return (
        1.0,
        -2.0,
        1.0,
        2.0 * (k * k - 1.0) / a0,
        (1.0 - k / q + k * k) / a0,
    )


def k_weighting_coeffs(rate: int) -> Tuple[Biquad, Biquad]:
    """K 加权的两级 biquad，按 ``(一级, 二级)`` 返回。"""
    if rate <= 0:
        raise ValueError("采样率必须为正")
    # 采样率过低时把截止点拉回奈奎斯特以内，避免 tan() 发散
    nyquist = rate / 2.0
    rlb_f0 = min(_RLB_FILTER[0], nyquist * 0.5)
    pre_f0 = min(_PRE_FILTER[0], nyquist * 0.9)
    return (
        high_pass_coeffs(rate, rlb_f0, _RLB_FILTER[1]),
        high_shelf_coeffs(rate, pre_f0, _PRE_FILTER[1], _PRE_FILTER[2]),
    )


def run_biquad(samples: Sequence[float], coeffs: Biquad) -> List[float]:
    """跑一级 biquad（Direct Form I），用于测试与离线验证。"""
    b0, b1, b2, a1, a2 = coeffs
    x1 = x2 = y1 = y2 = 0.0
    out: List[float] = [0.0] * len(samples)
    i = 0
    for x in samples:
        y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
        x2 = x1
        x1 = x
        y2 = y1
        y1 = y
        out[i] = y
        i += 1
    return out


def k_weight(samples: Sequence[float], rate: int) -> List[float]:
    """整段 K 加权（两级串联）。"""
    data: Sequence[float] = samples
    for coeffs in k_weighting_coeffs(rate):
        data = run_biquad(data, coeffs)
    return list(data)


def as_channels(samples: Samples) -> List[Sequence[float]]:
    """把入参规整成「声道列表」。

    传扁平样本序列当作单声道，传序列的序列当作多声道 —— 两种都接受，
    是为了让调用方（离线测试 / 流式解码器）不必关心这里的约定。
    """
    if samples is None:
        return []
    seq = list(samples)
    if not seq:
        return []
    first = seq[0]
    if isinstance(first, (int, float)):
        return [seq]
    return [list(ch) for ch in seq]


def sample_peak(samples: Sequence[float]) -> float:
    """样本峰值（绝对值最大），非有限值一律忽略。"""
    peak = 0.0
    for v in samples:
        if not -_SANE_LIMIT < v < _SANE_LIMIT:
            continue
        a = -v if v < 0 else v
        if a > peak:
            peak = a
    return peak


# ──────────────────────────────────────────────────────────────
# 流式响度计量
# ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AnalysisResult:
    """一次响度测算的结果。

    ``loudness_lufs`` 为 ``None`` 表示**测不出来**（静音 / 太短 / 全是无效样本）——
    这时不该硬算一个增益出来，调用方按 0 dB 处理。
    """

    loudness_lufs: Optional[float]
    peak: float
    analyzed_seconds: float
    blocks: int = 0
    gated_blocks: int = 0

    @property
    def measurable(self) -> bool:
        return self.loudness_lufs is not None


class _Channel:
    """单声道的滤波器状态 + 100ms 段能量累加。"""

    __slots__ = ("x1", "x2", "y1", "y2", "u1", "u2", "v1", "v2",
                 "segments", "cur", "count", "peak")

    def __init__(self) -> None:
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0
        self.u1 = self.u2 = self.v1 = self.v2 = 0.0
        self.segments: List[float] = []
        self.cur = 0.0
        self.count = 0
        self.peak = 0.0

    def push(self, data: Sequence[float], stage1: Biquad, stage2: Biquad,
             step: int) -> None:
        """过滤一段样本并累加段能量。

        两级 biquad 与累加写在同一个紧循环里（整个分析唯一的热点），
        状态全部搬到局部变量，循环外再写回。
        """
        b0, b1, b2, a1, a2 = stage1
        c0, c1, c2, d1, d2 = stage2
        x1, x2, y1, y2 = self.x1, self.x2, self.y1, self.y2
        u1, u2, v1, v2 = self.u1, self.u2, self.v1, self.v2
        cur, count, peak = self.cur, self.count, self.peak
        segments = self.segments
        for x in data:
            # NaN / inf 就地归零，免得污染后续所有样本
            if not -_SANE_LIMIT < x < _SANE_LIMIT:
                x = 0.0
            else:
                a = -x if x < 0 else x
                if a > peak:
                    peak = a
            y = b0 * x + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2 = x1
            x1 = x
            y2 = y1
            y1 = y
            v = c0 * y + c1 * u1 + c2 * u2 - d1 * v1 - d2 * v2
            u2 = u1
            u1 = y
            v2 = v1
            v1 = v
            cur += v * v
            count += 1
            if count == step:
                segments.append(cur)
                cur = 0.0
                count = 0
        self.x1, self.x2, self.y1, self.y2 = x1, x2, y1, y2
        self.u1, self.u2, self.v1, self.v2 = u1, u2, v1, v2
        self.cur, self.count, self.peak = cur, count, peak


class LoudnessMeter:
    """流式响度计：喂样本 → 随时取门限积分响度。

    分块按「100 ms 段求和、4 段滑窗成 400 ms 块」实现：段是逐样本累加的，
    块只在取值时按前缀和滑窗算一次，因此内存与曲目长度无关，中断在任意
    位置都拿得到有效结果（流媒体只分析前几十秒时就是这么用的）。
    """

    def __init__(self, rate: int = ANALYSIS_RATE, channels: int = 1) -> None:
        if rate <= 0:
            raise ValueError("采样率必须为正")
        self._rate = int(rate)
        self._step = max(1, int(round(BLOCK_STEP_SECONDS * rate)))
        self._size = max(1, int(round(BLOCK_SECONDS * rate)))
        #: 一个块由几个 100ms 段拼成（标准是 4）
        self._per_block = max(1, int(round(self._size / self._step)))
        self._stage1, self._stage2 = k_weighting_coeffs(self._rate)
        self._channels: List[_Channel] = []
        for _ in range(max(1, int(channels))):
            self._channels.append(_Channel())
        self._samples = 0

    # ── 喂数据 ──────────────────────────────────────────────

    def feed(self, samples: Samples) -> None:
        """喂一段样本（单声道序列，或「每声道一个序列」的列表）。"""
        chans = as_channels(samples)
        if not chans:
            return
        while len(self._channels) < len(chans):
            self._channels.append(_Channel())
        for ch, data in zip(self._channels, chans):
            ch.push(data, self._stage1, self._stage2, self._step)
        self._samples += len(chans[0])

    # ── 取值 ────────────────────────────────────────────────

    @property
    def analyzed_seconds(self) -> float:
        return self._samples / float(self._rate)

    @property
    def peak(self) -> float:
        """已分析样本里的峰值（多声道取最大）。"""
        return max((c.peak for c in self._channels), default=0.0)

    def block_loudness(self) -> List[float]:
        """每个 400 ms 块的响度（dB），按标准顺序（每 100 ms 一块）。"""
        sums = self._block_sums()
        if not sums:
            return []
        return [CALIBRATION_DB + 10.0 * math.log10(z) for z in sums if z > 0.0]

    def loudness(self) -> Optional[float]:
        """门限积分响度（LUFS）；测不出来返回 ``None``。"""
        blocks, gated = self._gate(self._block_sums())
        if not blocks:
            return self._short_loudness()
        if not gated:
            return None
        mean_square = sum(gated) / len(gated)
        if mean_square <= 0.0:
            return None
        return CALIBRATION_DB + 10.0 * math.log10(mean_square)

    def result(self) -> AnalysisResult:
        blocks, gated = self._gate(self._block_sums())
        return AnalysisResult(
            loudness_lufs=self.loudness(),
            peak=self.peak,
            analyzed_seconds=self.analyzed_seconds,
            blocks=len(blocks),
            gated_blocks=len(gated),
        )

    # ── 内部 ────────────────────────────────────────────────

    @staticmethod
    def _gate(energies: List[float]) -> Tuple[List[float], List[float]]:
        """两道门限，返回 ``(过绝对门限的块, 再过相对门限的块)``（都是均方值）。

        **相对门限要在能量域取平均**，不是把各块的 dB 值拿来算术平均 ——
        BS.1770-4 的式 5/6 是「先对各块均方求平均，再换算成 dB 减 10 LU」。
        这两者在「响一段 + 轻一段」的曲子上能差 4 LU 以上（实测过），
        会让相对门限要么形同虚设、要么把正常段落也砍掉。
        """
        if not energies:
            return [], []
        absolute = 10.0 ** ((ABSOLUTE_GATE_LUFS - CALIBRATION_DB) / 10.0)
        kept = [z for z in energies if z >= absolute]
        if not kept:
            return energies, []
        mean_square = sum(kept) / len(kept)
        if mean_square <= 0.0:
            return energies, []
        # 相对门限（dB）：过绝对门限块的平均响度 - 10 LU
        relative = CALIBRATION_DB + 10.0 * math.log10(mean_square) - RELATIVE_GATE_LU
        gated = [z for z in kept if CALIBRATION_DB + 10.0 * math.log10(z) > relative]
        return energies, gated

    def _block_sums(self) -> List[float]:
        """各块的均方和（跨声道相加，再除以块内样本数）。"""
        summed: Optional[List[float]] = None
        for ch in self._channels:
            segs = ch.segments
            n = len(segs) - self._per_block + 1
            if n <= 0:
                continue
            # 前缀和 → 滑窗（段数很少：每秒 10 个）
            prefix = [0.0] * (len(segs) + 1)
            acc = 0.0
            for i, value in enumerate(segs):
                acc += value
                prefix[i + 1] = acc
            window = [prefix[i + self._per_block] - prefix[i] for i in range(n)]
            if summed is None:
                summed = window
            else:
                if len(window) < len(summed):
                    summed = summed[:len(window)]
                summed = [a + b for a, b in zip(summed, window)]
        if not summed:
            return []
        size = float(self._per_block * self._step)
        return [s / size for s in summed]

    def _short_loudness(self) -> Optional[float]:
        """不足一个完整块时的兜底：把已有样本当成一块来测。

        只在「至少 MIN_BLOCK_SECONDS 长」时给值，且这是一次性的近似 ——
        比不上标准的分块门限，但总好过把 200 ms 的铃声判成「测不出来」。

        注意要把**已经收尾的整段**（``segments``）和**没满一段的零头**
        （``cur`` / ``count``）一起算进去：200 ms 的信号正好凑满两个 100 ms 段，
        零头是空的，只看零头会得出「一个样本都没有」的错误结论。
        """
        if self._samples < int(MIN_BLOCK_SECONDS * self._rate):
            return None
        total = 0.0
        counted = 0
        for ch in self._channels:
            energy = sum(ch.segments) + ch.cur
            samples = len(ch.segments) * self._step + ch.count
            if samples <= 0:
                continue
            total += energy / samples
            counted += 1
        if counted == 0 or total <= 0.0:
            return None
        return CALIBRATION_DB + 10.0 * math.log10(total)


def analyze(samples: Samples, rate: int = ANALYSIS_RATE) -> AnalysisResult:
    """一次性测算（离线测试与本地文件的便捷入口）。"""
    chans = as_channels(samples)
    meter = LoudnessMeter(rate, channels=max(1, len(chans)))
    meter.feed(chans if len(chans) > 1 else (chans[0] if chans else []))
    return meter.result()


# ──────────────────────────────────────────────────────────────
# 增益
# ──────────────────────────────────────────────────────────────

#: 增益的默认边界：抬升太多会把「轻的曲子」推成另一种失真，衰减太多同理
DEFAULT_MAX_GAIN_DB = 12.0
DEFAULT_MIN_GAIN_DB = -24.0
#: 峰值上限：-1 dBFS。留一点余量给重采样 / 采样间峰值（这里只算样本峰值）
DEFAULT_PEAK_CEILING = 0.891
#: 只分析了前一段（流媒体）时更保守的峰值上限：没分析到的部分可能更响
PARTIAL_PEAK_CEILING = 0.708


def db_to_ratio(db: float) -> float:
    """dB → 线性倍数。"""
    return 10.0 ** (float(db) / 20.0)


def ratio_to_db(ratio: float) -> float:
    """线性倍数 → dB（非正数按 -120 dB 处理，避免 math domain error）。"""
    r = float(ratio)
    return 20.0 * math.log10(r) if r > 0 else -120.0


def gain_db(
    loudness_lufs: Optional[float],
    peak: float,
    *,
    target_lufs: float = DEFAULT_TARGET_LUFS,
    preamp_db: float = 0.0,
    allow_boost: bool = True,
    peak_ceiling: float = DEFAULT_PEAK_CEILING,
    max_gain_db: float = DEFAULT_MAX_GAIN_DB,
    min_gain_db: float = DEFAULT_MIN_GAIN_DB,
) -> float:
    """把「这首曲子有多响」换算成「该加多少 dB」。

    三道约束，按顺序施加：

    1. **目标响度**：``target - loudness (+ preamp)``；``allow_boost=False`` 时不允许为正；
    2. **防削波**：增益后的样本峰值不得超过 ``peak_ceiling``，即最多只能加
       ``20·log10(peak_ceiling / peak)`` dB；
    3. **上下限**：夹进 ``[min_gain_db, max_gain_db]``。

    测不出响度（``loudness_lufs is None``）时返回 0 dB —— 宁可不处理，
    也不要凭峰值瞎猜一个增益。峰值未知（<=0）时按「随时可能满刻度」处理，只允许衰减。
    """
    if loudness_lufs is None:
        return 0.0
    try:
        want = float(target_lufs) - float(loudness_lufs) + float(preamp_db)
    except (TypeError, ValueError):
        return 0.0
    if not allow_boost:
        want = min(want, 0.0)

    try:
        p = float(peak)
    except (TypeError, ValueError):
        p = 0.0
    if p > 0.0 and peak_ceiling > 0.0:
        want = min(want, 20.0 * math.log10(peak_ceiling / p))
    else:
        want = min(want, 0.0)

    return max(float(min_gain_db), min(float(max_gain_db), want))


def describe_gain(db: float) -> str:
    """给界面看的增益描述。"""
    try:
        value = float(db)
    except (TypeError, ValueError):
        value = 0.0
    if abs(value) < 0.05:
        return "不调整"
    return f"{value:+.1f} dB"
