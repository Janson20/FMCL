"""音量均衡的后台分析服务。

职责：把「哪首曲子要分析」排成一条队列，解码 + 测算，结果落进
:class:`~app.core.loudness_store.LoudnessStore`，再通知播放器把增益接上去。

**为什么是事件驱动而不是工作线程**
------------------------------------
第一版把解码放进 ``QThread``，结果发现一个必崩的组合（实测矩阵）：

============================  ==========================  ========
解码所在线程                   主线程是否用 QMediaPlayer     进程退出
============================  ==========================  ========
主线程                        是                          正常
工作线程                      否                          正常
工作线程                      是                          **0xC0000409**
============================  ==========================  ========

故障模块是 Qt6Core.dll，属于 Qt 的多媒体后端在跨线程使用后于退出时
``qFatal``。与其和 Qt 的线程亲和性缠斗，不如顺着它：``QAudioDecoder``
本来**就是异步的**（解码在它自己的内部线程里跑，只在就绪时发
``bufferReady``），于是这里改成在主线程里驱动它，并且：

* 每收到缓冲区只做「读 + 喂给响度计」，一轮最多花
  :data:`~app.core.loudness_decode.DEFAULT_SLICE_SECONDS` 秒（实测纯 Python
  K 加权 4M 样本/秒，8 kHz 立体声一秒音频只要 4 ms），花完就
  ``singleShot(0)`` 让出事件循环 —— 界面不会卡，也不会跨线程碰 Qt 对象；
* 队列、取消、预取都退化成普通的状态流转，不再需要 ``QThread`` 生命周期管理。

唯一还在用线程的地方是「兜底下载」（某些音源的地址带不上请求头，解码器打不开，
只能先下一段）：那条路径只用 ``requests``，不碰 Qt，放普通线程里是安全的。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Dict, Optional

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from . import loudness_decode as decode_ops
from .loudness import db_to_ratio, describe_gain
from .loudness_store import (METHOD_DECODE, METHOD_OFFSET, METHOD_TAG, LoudnessStore,
                            Measurement, gain_db_for)
from ..config import Config

logger = logging.getLogger(__name__)

#: 缓存落盘的合并窗口（秒）：连着分析好几首时不必每次都写盘
SAVE_DEBOUNCE_MS = 8000
#: ReplayGain 2.0 标签的参考响度（读标签时用它换算曲目响度）
REPLAYGAIN_REFERENCE_LUFS = -18.0


@dataclass
class _Job:
    """一条待分析的任务。"""

    uid: str
    track: Any                      # app.core.models.Track
    target: str = ""                # 已解析的播放地址（本地曲目就是文件路径）
    is_local: bool = False
    source: str = ""
    focus: bool = False
    aborted: bool = False


class _Emitter(QObject):
    """「兜底下载」那个普通线程 → 主线程的投递（Qt 会自动排队到接收者线程）。"""

    downloaded = Signal(object, str)


class LoudnessService(QObject):
    """音量均衡分析服务，挂在播放引擎旁边（全部在主线程里跑）。"""

    #: 某首曲子的测量值就绪（uid, Measurement）
    measured = Signal(str, object)
    #: 缓存统计变化（设置页刷新用）
    changed = Signal()

    def __init__(self, config: Config, store: Optional[LoudnessStore] = None,
                 parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._store = store if store is not None else LoudnessStore()

        self._queue: Deque[_Job] = deque()
        self._pending: Dict[str, _Job] = {}
        self._active: Optional[_Job] = None
        self._focus_uid = ""

        # 当前解码
        self._decoder = None
        self._meter = None
        self._complete = False          # 是否解到了文件末尾（区分「前一段」）
        self._limit_hit = False
        self._decode_error = ""
        self._drained = True            # 是否已经把当前可读缓冲区抽干
        self._fallback_tried = False
        self._temp_file = ""

        self._emitter = _Emitter(self)
        self._emitter.downloaded.connect(self._on_fallback_downloaded)

        self._slice_timer = QTimer(self)
        self._slice_timer.setSingleShot(True)
        self._slice_timer.timeout.connect(self._drain)

        self._timeout_timer = QTimer(self)
        self._timeout_timer.setSingleShot(True)
        self._timeout_timer.timeout.connect(self._on_decode_timeout)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(SAVE_DEBOUNCE_MS)
        self._save_timer.timeout.connect(self.flush)

    # ── 生命周期 ────────────────────────────────────────────

    def start(self) -> None:
        """开始消费队列（幂等）。"""
        self._pump()

    def shutdown(self) -> None:
        """停止一切分析并落盘（退出时调用）。"""
        self._stop_decoder()
        for job in list(self._queue) + list(self._pending.values()):
            job.aborted = True
        self._queue.clear()
        self._pending.clear()
        self.flush()

    def flush(self) -> None:
        """把缓存写盘（退出与定时器都走这里）。"""
        try:
            self._store.save()
        except Exception as e:  # pragma: no cover
            logger.debug("响度缓存写盘失败: %s", e)

    @property
    def store(self) -> LoudnessStore:
        return self._store

    @property
    def busy(self) -> bool:
        """当前是否有正在解码的曲目。"""
        return self._active is not None

    # ── 配置 ────────────────────────────────────────────────

    def _opt(self, key: str, default: Any) -> Any:
        value = self._config.get(f"audio.{key}", default)
        return default if value is None else value

    @property
    def enabled(self) -> bool:
        """音量均衡是否开启（关掉时一切查询都返回 0 dB）。"""
        return bool(self._opt("equalize", True))

    def gain_options(self) -> Dict[str, Any]:
        """当前设置下的增益参数（缓存存的是测量值，增益按设置现算）。"""
        return {
            "target_lufs": float(self._opt("target_lufs", -14.0)),
            "preamp_db": float(self._opt("preamp_db", 0.0)),
            "allow_boost": bool(self._opt("allow_boost", True)),
            "max_gain_db": float(self._opt("max_gain_db", 12.0)),
            "min_gain_db": float(self._opt("min_gain_db", -24.0)),
        }

    # ── 查询 ────────────────────────────────────────────────

    def gain_db(self, track: Any) -> float:
        """这首曲子该加多少 dB。没有测量值 → 0 dB。"""
        if track is None or not self.enabled:
            return 0.0
        uid = str(getattr(track, "uid", "") or "")
        if not uid:
            return 0.0
        # 失败条目也要认（下面用音源中位数兜底），所以这里不要求重试
        item = self._store.get(uid, retry_failed=False)
        if item is not None and item.ok:
            return gain_db_for(item, **self.gain_options())
        if item is not None and bool(self._opt("source_offset", True)):
            offset = self._store.source_offset_db(str(getattr(track, "source", "") or ""))
            if offset:
                return offset
        return 0.0

    def gain_ratio(self, track: Any) -> float:
        """线性增益倍数（0 dB → 1.0）。"""
        return db_to_ratio(self.gain_db(track))

    def describe(self, track: Any) -> str:
        """给界面看的一行说明：增益来自哪种测算。"""
        if track is None:
            return ""
        uid = str(getattr(track, "uid", "") or "")
        item = self._store.get(uid, retry_failed=False)
        if item is not None and item.ok:
            method = {METHOD_DECODE: "实测", METHOD_TAG: "标签",
                      METHOD_OFFSET: "音源估计"}.get(item.method, item.method)
            span = "全曲" if not item.partial else f"前 {item.seconds:.0f} 秒"
            return (f"{describe_gain(gain_db_for(item, **self.gain_options()))}"
                    f"（{method} · 响度 {item.loudness_lufs:.1f} LUFS · {span}）")
        if item is not None and bool(self._opt("source_offset", True)):
            offset = self._store.source_offset_db(str(getattr(track, "source", "") or ""))
            if offset:
                return f"{describe_gain(offset)}（音源估计）"
        return ""

    def stats(self) -> Dict[str, Any]:
        data = self._store.stats()
        data["pending"] = len(self._pending)
        data["analyzing"] = self._active.uid if self._active else ""
        data["enabled"] = self.enabled
        return data

    # ── 排队 ────────────────────────────────────────────────

    def request(self, track: Any, *, target: str = "", is_local: bool = False,
                focus: bool = False) -> bool:
        """排入一次分析；返回是否真的排上了（已测过 / 关掉功能时返回 False）。"""
        if track is None or not self.enabled:
            return False
        uid = str(getattr(track, "uid", "") or "")
        if not uid:
            return False
        if self._store.get(uid) is not None:
            return False                     # 已有有效测量值（含未过期的失败）

        job = _Job(
            uid=uid,
            track=track,
            target=str(target or ""),
            is_local=bool(is_local or getattr(track, "is_local", False)),
            source=str(getattr(track, "source", "") or ""),
            focus=bool(focus),
        )
        running = self._pending.get(uid)
        if running is not None:
            # 已经在排队：补上刚拿到的地址（预取时可能先排队后解析）
            if job.target and not running.target:
                running.target = job.target
                running.is_local = job.is_local
            if focus:
                self._focus(uid)
            return False
        if focus:
            self._focus(uid)
        self._pending[uid] = job
        self._queue.append(job)
        self._pump()
        return True

    def prefetch(self, track: Any, *, target: str = "", is_local: bool = False) -> bool:
        """把接下来要播的那首排进队列（普通优先级）。"""
        if not self.enabled or not bool(self._opt("prefetch_next", True)):
            return False
        return self.request(track, target=target, is_local=is_local, focus=False)

    def _focus(self, uid: str) -> None:
        """把焦点切到 ``uid``：其它待办（含正在解码的那首）一律作废。

        用户切歌之后，上一首的分析结果已经没有意义了；不打断的话它还要白占
        好几秒的 CPU 与带宽。
        """
        if self._focus_uid == uid:
            return
        self._focus_uid = uid
        for other_uid, job in self._pending.items():
            if other_uid != uid:
                job.aborted = True
                self._queue.remove(job) if job in self._queue else None
        self._pending = {uid: self._pending[uid]} if uid in self._pending else {}
        active = self._active
        if active is not None and active.uid != uid:
            active.aborted = True
            self._stop_decoder()             # 立刻放手，接着排新任务
            self._active = None
            self._pump()

    # ── 队列驱动（全部在主线程）─────────────────────────────

    def _pump(self) -> None:
        if self._active is not None:
            return
        while self._queue:
            job = self._queue.popleft()
            if self._pending.get(job.uid) is job:
                del self._pending[job.uid]
            if job.aborted:
                continue
            self._start_job(job)
            return

    def _start_job(self, job: _Job) -> None:
        self._active = job
        self._cancel_timers()
        self._complete = False
        self._limit_hit = False
        self._decode_error = ""
        self._drained = True
        self._fallback_tried = False

        track = job.track
        path = str(getattr(track, "path", "") or "")
        is_local_file = bool(path) and os.path.isfile(path)

        # ① 本地文件：可选的 ReplayGain 标签捷径（省一次解码）
        if is_local_file and bool(self._opt("prefer_tags", False)):
            tags = decode_ops.read_replaygain_tags(path)
            if tags is not None:
                gain, peak = tags
                self._finish_job(Measurement(
                    uid=job.uid,
                    loudness_lufs=REPLAYGAIN_REFERENCE_LUFS - float(gain),
                    peak=float(peak),
                    seconds=0.0,
                    partial=False,
                    source=job.source or "local",
                    method=METHOD_TAG,
                    ok=True,
                    at=time.time(),
                ))
                return

        # ② 定目标：本地文件直接用文件，在线曲目用播放引擎给过来的地址
        target, is_local = job.target, job.is_local
        if not target and is_local_file:
            target, is_local = path, True
        if not target:
            logger.debug("没有可分析的目标: %s", job.uid)
            self._finish_job(Measurement.failure(job.uid, source=job.source))
            return

        decoder = decode_ops.make_decoder(target)
        if decoder is None:
            self._finish_job(Measurement.failure(job.uid, source=job.source))
            return

        self._decoder = decoder
        self._meter = decode_ops.new_meter()
        self._is_local_target = bool(is_local)
        decoder.bufferReady.connect(self._on_buffer_ready)
        decoder.finished.connect(self._on_decoder_finished)
        decoder.error.connect(self._on_decoder_error)
        timeout_ms = int(float(self._opt("decode_timeout", decode_ops.DEFAULT_TIMEOUT_S)) * 1000)
        self._timeout_timer.start(max(5000, timeout_ms))
        try:
            decoder.start()
        except Exception as e:  # pragma: no cover - 后端异常不该拖垮界面
            logger.debug("启动解码失败 %s: %s", job.uid, e)
            self._stop_decoder()
            self._finish_job(Measurement.failure(job.uid, source=job.source))

    # ── 解码回调（主线程）───────────────────────────────────

    @Slot()
    def _on_buffer_ready(self) -> None:
        if not self._drained:
            return                       # 已经排了让出，等那一轮把队列抽干
        self._drain()

    def _drain(self) -> None:
        """把当前可读的缓冲区抽干；超过时间预算就让出事件循环。"""
        decoder, meter = self._decoder, self._meter
        if decoder is None or meter is None:
            return
        job = self._active
        if job is None or job.aborted:
            self._stop_decoder()
            if job is not None:
                self._finish_job(None)
            else:
                self._pump()
            return

        limit = max(0.0, float(self._opt("analysis_seconds", 0) or 0))
        budget_end = time.perf_counter() + float(
            self._opt("slice_seconds", decode_ops.DEFAULT_SLICE_SECONDS) or
            decode_ops.DEFAULT_SLICE_SECONDS
        )
        while True:
            try:
                buf = decoder.read()
            except Exception as e:  # pragma: no cover
                logger.debug("读取解码缓冲区异常: %s", e)
                break
            if buf is None or not buf.isValid():
                break
            decode_ops.feed_buffer(meter, buf)
            if limit > 0.0 and meter.analyzed_seconds >= limit:
                self._limit_hit = True
                self._stop_decoder()
                return self._finish_current()
            if time.perf_counter() >= budget_end:
                # 预算用完了：让出一轮，界面才有机会处理别的事件
                self._drained = False
                self._slice_timer.start(0)
                return
        self._drained = True

    @Slot()
    def _on_decoder_finished(self) -> None:
        self._complete = True
        self._finish_current()

    @Slot()
    def _on_decoder_error(self) -> None:
        # 尾巴上有垃圾字节（FLAC 很常见）也会报错，但样本已经拿到了；
        # 最终算不算失败看有没有解出东西，见 _finish_current
        try:
            self._decode_error = str(self._decoder.errorString() or "")
        except Exception:
            self._decode_error = "解码错误"

    def _on_decode_timeout(self) -> None:
        job = self._active
        if job is None:
            return
        logger.info("响度分析超时: %s", job.uid)
        self._decode_error = self._decode_error or "解码超时"
        self._stop_decoder()
        self._finish_current()

    # ── 结果 ────────────────────────────────────────────────

    def _finish_current(self) -> None:
        """当前解码告一段落：出结果，或者转去兜底下载。"""
        job, meter = self._active, self._meter
        self._stop_decoder()
        if job is None:
            self._pump()
            return
        if job.aborted:
            self._finish_job(None)
            return

        if meter is None or meter.analyzed_seconds <= 0.0:
            # 一个样本都没解出来：在线地址多半是需要请求头，转兜底下载
            if not self._is_local_target and not self._fallback_tried and not job.aborted:
                self._fallback_tried = True
                self._start_fallback_download(job)
                return
            logger.info("响度测不出来: %s（%s）", job.uid, self._decode_error or "没有音频")
            self._finish_job(Measurement.failure(job.uid, source=job.source))
            return

        result = meter.result()
        if not result.measurable:
            if not self._is_local_target and not self._fallback_tried and not job.aborted:
                self._fallback_tried = True
                self._start_fallback_download(job)
                return
            logger.info("响度测不出来（静音或过短）: %s", job.uid)
            self._finish_job(Measurement.failure(job.uid, source=job.source))
            return

        self._finish_job(Measurement.from_result(
            job.uid, result,
            source=job.source,
            # 配了时长上限、或没解到末尾（截断的下载）都算「只分析了前一段」
            partial=bool(self._limit_hit or not self._complete),
            method=METHOD_DECODE,
        ))

    def _start_fallback_download(self, job: _Job) -> None:
        """在线地址直连解不开 → 带音源请求头下一段再分析（普通线程，只跑 requests）。"""
        logger.info("直连解码失败（%s），改用下载后分析: %s",
                    self._decode_error or "未知原因", job.uid)
        target = job.target
        source = job.source

        def worker() -> None:
            path = ""
            try:
                path = decode_ops.download_partial(target, source) or ""
            except Exception as e:  # pragma: no cover
                logger.debug("兜底下载异常: %s", e)
            self._emitter.downloaded.emit(job, path)

        threading.Thread(target=worker, daemon=True, name="loudness-fallback").start()

    @Slot(object, str)
    def _on_fallback_downloaded(self, job: Any, path: str) -> None:
        if not isinstance(job, _Job) or self._active is not job:
            if path:
                self._drop_temp(path)
            return
        if job.aborted or not path:
            self._finish_job(Measurement.failure(job.uid, source=job.source))
            return
        self._temp_file = path
        decoder = decode_ops.make_decoder(path)
        if decoder is None:
            self._finish_job(Measurement.failure(job.uid, source=job.source))
            return
        self._decoder = decoder
        self._meter = decode_ops.new_meter()
        self._is_local_target = True
        self._complete = False
        self._decode_error = ""
        decoder.bufferReady.connect(self._on_buffer_ready)
        decoder.finished.connect(self._on_decoder_finished)
        decoder.error.connect(self._on_decoder_error)
        self._timeout_timer.start(int(decode_ops.DEFAULT_TIMEOUT_S * 1000))
        try:
            decoder.start()
        except Exception as e:  # pragma: no cover
            logger.debug("兜底解码启动失败: %s", e)
            self._stop_decoder()
            self._finish_job(Measurement.failure(job.uid, source=job.source))

    def _finish_job(self, measurement: Optional[Measurement]) -> None:
        job, self._active = self._active, None
        self._meter = None
        self._cancel_timers()
        self._drop_temp(self._temp_file)
        self._temp_file = ""
        if measurement is not None and job is not None and not job.aborted:
            self._store.put(measurement)
            self._save_timer.start()
            self.measured.emit(job.uid, measurement)
            self.changed.emit()
        self._pump()

    def _stop_decoder(self) -> None:
        decoder, self._decoder = self._decoder, None
        if decoder is None:
            return
        for signal in (decoder.bufferReady, decoder.finished, decoder.error):
            try:
                signal.disconnect()
            except Exception:
                pass
        try:
            decoder.stop()
        except Exception:
            pass

    def _cancel_timers(self) -> None:
        self._timeout_timer.stop()
        self._slice_timer.stop()

    @staticmethod
    def _drop_temp(path: str) -> None:
        if not path:
            return
        try:
            os.unlink(path)
        except OSError:
            pass

    # ── 缓存管理 ────────────────────────────────────────────

    @Slot()
    def clear(self) -> None:
        """清空响度缓存（下次播放会重新分析）。"""
        count = self._store.clear()
        self.changed.emit()
        logger.info("已清空响度缓存（%d 条）", count)
