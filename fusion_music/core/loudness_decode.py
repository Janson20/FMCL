"""把「一个可播放目标」解码成 PCM，供响度测算使用。

音量均衡需要 PCM，而 ``QMediaPlayer`` 不暴露 PCM —— 这里用 Qt 自带的
``QAudioDecoder``（ffmpeg 后端）解码，并且**由后端替我们降采样 + 转格式**：
请求 8 kHz / 立体声 / float32，后端就把重采样做掉了，纯 Python 的后续滤波
只需要处理 1/6 的样本量（实测见 ``tools/probe_audiodecoder.py``）。

两个要点：

* 之所以要**立体声**而不是先混单声道：BS.1770 是「各声道均方相加」，
  先混单再算会平白低 3 dB（见 :mod:`app.core.loudness` 的说明）。
* **解码器只在主线程创建与销毁**。这一点是被崩溃逼出来的：``QAudioDecoder``
  在工作线程里用过之后，进程退出时 Qt6Core 会直接 qFatal
  （Windows 上退出码 0xC0000409）。实测矩阵：主线程解码 + 主线程播放 = 正常；
  工作线程解码 + 不播放 = 正常；工作线程解码 + 主线程播放 = 必崩。
  所以解码改成**事件驱动**（``bufferReady`` 分片处理、按时间预算让出主线程），
  见 :mod:`app.core.loudness_service`，既不卡界面也不碰跨线程的坑。

本模块因此只留「与线程无关」的零件：配置解码器、把缓冲区喂进响度计、
以及两个辅助函数（兜底下载、读 ReplayGain 标签）。
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioBuffer, QAudioDecoder, QAudioFormat

from . import cache
from .loudness import ANALYSIS_RATE, LoudnessMeter

logger = logging.getLogger(__name__)

#: 解码出来的声道数。立体声是标准口径（见模块说明）
DECODE_CHANNELS = 2
#: 单次解码的墙钟超时（秒）：网络卡住时不能一直占着
DEFAULT_TIMEOUT_S = 180.0
#: 网络地址解码失败后，改用「带请求头下载一段再分析」的兜底体积上限
DEFAULT_FALLBACK_BYTES = 16 * 1024 * 1024
#: 每轮事件循环最多花在解码上的时间（秒）：让界面始终能喘气
DEFAULT_SLICE_SECONDS = 0.008


def make_decoder(target: str, *, rate: int = ANALYSIS_RATE,
                 channels: int = DECODE_CHANNELS) -> Optional[QAudioDecoder]:
    """建一个配置好的 ``QAudioDecoder``；目标不可用时返回 ``None``。"""
    if not target:
        return None
    src = str(target)
    if "://" not in src:
        path = Path(src)
        if not path.exists():
            logger.debug("要分析的音频不存在: %s", path)
            return None
        src = QUrl.fromLocalFile(str(path.resolve())).toString()

    fmt = QAudioFormat()
    fmt.setSampleRate(int(rate))
    fmt.setChannelCount(int(channels))
    fmt.setSampleFormat(QAudioFormat.Float)

    decoder = QAudioDecoder()
    decoder.setAudioFormat(fmt)
    decoder.setSource(QUrl(src))
    return decoder


def new_meter(*, rate: int = ANALYSIS_RATE,
              channels: int = DECODE_CHANNELS) -> LoudnessMeter:
    return LoudnessMeter(int(rate), channels=int(channels))


def feed_buffer(meter: LoudnessMeter, buf: QAudioBuffer) -> bool:
    """把一块解码缓冲区喂进响度计；返回是否真的喂进去了。

    后端给的是交错 float32：偶数下标是一个声道、奇数下标是另一个。
    """
    try:
        channels = max(1, int(buf.format().channelCount()))
        raw = bytes(buf.constData())
    except Exception as e:  # pragma: no cover - 后端异常不该打断整次分析
        logger.debug("读取解码缓冲区失败: %s", e)
        return False
    usable = len(raw) - (len(raw) % 4)          # float32 边界
    if usable <= 0:
        return False
    samples = memoryview(raw)[:usable].cast("f")
    if channels <= 1:
        meter.feed(samples.tolist())
    else:
        # >2 声道时只取前两个：K 加权对环绕声道另有增益，这里不需要
        meter.feed([samples[0::channels].tolist(), samples[1::channels].tolist()])
    return True


def download_partial(url: str, source: str, *, max_bytes: int = DEFAULT_FALLBACK_BYTES,
                     timeout: int = 30) -> Optional[str]:
    """带音源请求头下载 ``url`` 的前 ``max_bytes`` 字节到临时文件。

    这是**兜底路径**：部分音源的地址直接用解码器打不开（需要 Referer 之类的
    请求头，而走 Qt 网络栈时带不上），这时按音源下载一段再本地分析。
    返回临时文件路径（调用方负责删除），失败返回 ``None``。

    只用 ``requests``，不碰任何 Qt 对象，所以丢进普通线程里跑是安全的。
    """
    if not url:
        return None
    from .. import sources

    headers = sources.download_headers(source) or {}
    cookies = sources.download_cookies(source) or {}

    fd, tmp = tempfile.mkstemp(prefix="fmp_loudness_", suffix=".part")
    os.close(fd)
    path = Path(tmp)
    try:
        size = 0
        with cache.session().get(url, headers=headers, cookies=cookies,
                                timeout=timeout, stream=True) as resp:
            resp.raise_for_status()
            with open(path, "wb") as f:
                for chunk in resp.iter_content(65536):
                    if not chunk:
                        continue
                    f.write(chunk)
                    size += len(chunk)
                    if size >= max_bytes:
                        break
        if size <= 0:
            path.unlink(missing_ok=True)
            return None
        return str(path)
    except Exception as e:
        logger.debug("响度兜底下载失败 %s: %s", url, e)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def read_replaygain_tags(path: str) -> Optional[Tuple[float, float]]:
    """读本地音频文件里的 ReplayGain 标签，返回 ``(gain_db, peak)``。

    没有标签返回 ``None``。**只有设置里开了 ``audio.prefer_tags`` 才会被用到**：
    标签里的 ``REPLAYGAIN_TRACK_GAIN`` 是按 ReplayGain 2.0 的 -18 LUFS 参考写的，
    而默认目标是 -14 LUFS，因此换算时要补一个偏移；不同打标器口径不一，
    实测（解码分析）永远比标签更可信。
    """
    try:
        from mutagen import File as MutagenFile

        mf = MutagenFile(path)
        if mf is None:
            return None
        tags = getattr(mf, "tags", None)
        if not tags:
            return None

        def pick(*keys) -> str:
            for key in keys:
                try:
                    value = tags.get(key)
                except Exception:
                    value = None
                if value:
                    if isinstance(value, (list, tuple)):
                        value = value[0]
                    return str(value)
            return ""

        raw_gain = pick("REPLAYGAIN_TRACK_GAIN", "replaygain_track_gain",
                        "----:com.apple.iTunes:REPLAYGAIN_TRACK_GAIN")
        if not raw_gain:
            return None
        from .loudness_store import parse_replaygain_tag

        gain = parse_replaygain_tag(raw_gain)
        if gain is None:
            return None
        peak = parse_replaygain_tag(pick("REPLAYGAIN_TRACK_PEAK",
                                         "replaygain_track_peak")) or 0.0
        return (gain, peak)
    except Exception as e:
        logger.debug("读取 ReplayGain 标签失败 %s: %s", path, e)
        return None
