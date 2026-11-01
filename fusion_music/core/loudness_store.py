"""响度测算的持久化缓存（纯数据，不依赖 Qt）。

键是曲目 ``uid``（形如 ``wy:1234567`` 或 ``local:D:\\Music\\a.flac``），
值是**测量值**而不是增益：响度、峰值、分析了多少秒。这样做的原因是增益
依赖设置（目标响度、预增益、是否允许抬升），换一档目标响度不必重算整库。

失败也会落盘（``ok=False``），但有 TTL —— 全曲库里总有几首解析不出地址或
解不了码，不记下来的话每次播放都会重试一遍、每次都白等几秒。

文件是整份重写的（跟会话、曲库一致），所以写入走「临时文件 + 原子替换」，
并且在条目数超过上限时按时间戳淘汰最旧的。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import loudness
from .loudness import AnalysisResult
from .. import paths
from ..jsonio import atomic_write_json

logger = logging.getLogger(__name__)

#: 缓存格式版本；与 :data:`app.core.loudness.ANALYSIS_VERSION` 一起决定有效期
CACHE_VERSION = 2

#: 失败条目多久之后允许重试（秒）
FAILURE_TTL_SECONDS = 24 * 3600
#: 条目上限，超出后按 ``at`` 淘汰最旧的（老机器的 data 目录也不该无限膨胀）
MAX_ENTRIES = 20000

#: 音源级兜底需要的样本数：太少的中位数没有代表性，宁可不用
MIN_SOURCE_SAMPLES = 5
#: 音源级兜底的夹紧范围（dB）
SOURCE_OFFSET_LIMIT_DB = 6.0

#: 测定方式
METHOD_DECODE = "decode"       # 解码后实测
METHOD_TAG = "tag"             # 读音频文件里的 ReplayGain 标签
METHOD_OFFSET = "offset"       # 按同音源中位增益兜底


@dataclass(frozen=True)
class Measurement:
    """一首曲子的响度测算结果。"""

    uid: str
    loudness_lufs: Optional[float]
    peak: float
    seconds: float = 0.0          # 实际分析了多少秒音频
    partial: bool = True          # 是否只分析了前一段（流媒体 / 提前中断）
    source: str = ""              # 音源 id，用于音源级兜底
    method: str = METHOD_DECODE
    ok: bool = True               # 是否成功测出响度
    at: float = 0.0

    @classmethod
    def from_result(cls, uid: str, result: AnalysisResult, *, source: str = "",
                    partial: bool = True, method: str = METHOD_DECODE) -> "Measurement":
        return cls(
            uid=str(uid),
            loudness_lufs=result.loudness_lufs,
            peak=float(result.peak),
            seconds=float(result.analyzed_seconds),
            partial=bool(partial),
            source=str(source or ""),
            method=str(method),
            ok=result.measurable,
            at=time.time(),
        )

    @classmethod
    def failure(cls, uid: str, *, source: str = "", method: str = METHOD_DECODE) -> "Measurement":
        return cls(
            uid=str(uid), loudness_lufs=None, peak=0.0, seconds=0.0,
            partial=True, source=str(source or ""), method=str(method),
            ok=False, at=time.time(),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lufs": self.loudness_lufs,
            "peak": round(float(self.peak), 6),
            "secs": round(float(self.seconds), 3),
            "partial": bool(self.partial),
            "source": self.source,
            "method": self.method,
            "ok": bool(self.ok),
            "at": round(float(self.at or time.time()), 3),
        }

    @classmethod
    def from_dict(cls, uid: str, data: Dict[str, Any]) -> Optional["Measurement"]:
        if not isinstance(data, dict):
            return None
        try:
            lufs = data.get("lufs")
            return cls(
                uid=str(uid),
                loudness_lufs=None if lufs is None else float(lufs),
                peak=float(data.get("peak", 0.0) or 0.0),
                seconds=float(data.get("secs", 0.0) or 0.0),
                partial=bool(data.get("partial", True)),
                source=str(data.get("source", "") or ""),
                method=str(data.get("method", METHOD_DECODE) or METHOD_DECODE),
                ok=bool(data.get("ok", True)),
                at=float(data.get("at", 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            return None


def gain_db_for(
    measurement: Optional[Measurement],
    *,
    target_lufs: float = loudness.DEFAULT_TARGET_LUFS,
    preamp_db: float = 0.0,
    allow_boost: bool = True,
    max_gain_db: float = loudness.DEFAULT_MAX_GAIN_DB,
    min_gain_db: float = loudness.DEFAULT_MIN_GAIN_DB,
) -> float:
    """测量值 → 增益（dB）。没有测量值、或测量失败时返回 0 dB。"""
    if measurement is None or not measurement.ok:
        return 0.0
    ceiling = (
        loudness.PARTIAL_PEAK_CEILING if measurement.partial else loudness.DEFAULT_PEAK_CEILING
    )
    return loudness.gain_db(
        measurement.loudness_lufs,
        measurement.peak,
        target_lufs=target_lufs,
        preamp_db=preamp_db,
        allow_boost=allow_boost,
        peak_ceiling=ceiling,
        max_gain_db=max_gain_db,
        min_gain_db=min_gain_db,
    )


class LoudnessStore:
    """响度缓存的读写与查询（线程安全）。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else paths.loudness_file()
        self._lock = threading.RLock()
        self._entries: Dict[str, Measurement] = {}
        self._dirty = False
        self.load()

    # ── 持久化 ──────────────────────────────────────────────

    def load(self) -> None:
        with self._lock:
            self._entries.clear()
            try:
                if not self._path.exists():
                    return
                raw = json.loads(self._path.read_text(encoding="utf-8") or "{}")
            except Exception as e:  # 缓存损坏不该阻塞启动
                logger.warning("响度缓存读取失败，将重建: %s", e)
                return
            if not isinstance(raw, dict) or int(raw.get("version", 0) or 0) != CACHE_VERSION:
                logger.info("响度缓存版本不符，已作废重建")
                return
            entries = raw.get("entries")
            if not isinstance(entries, dict):
                return
            for uid, data in entries.items():
                item = Measurement.from_dict(uid, data)
                if item is not None:
                    self._entries[str(uid)] = item

    def save(self, *, force: bool = False) -> bool:
        with self._lock:
            if not self._dirty and not force:
                return True
            self._prune_locked()
            payload = {
                "version": CACHE_VERSION,
                "analysis": loudness.ANALYSIS_VERSION,
                "entries": {uid: m.to_dict() for uid, m in self._entries.items()},
            }
            try:
                # 紧凑格式：上万条目的缓存，缩进会让文件大出一倍
                atomic_write_json(self._path, payload, separators=(",", ":"))
                self._dirty = False
                return True
            except Exception as e:
                logger.error("响度缓存保存失败: %s", e)
                return False

    # ── 查询 ────────────────────────────────────────────────

    def get(self, uid: str, *, retry_failed: bool = True) -> Optional[Measurement]:
        """取一条测量值。

        ``retry_failed=True``（默认）时，**过期的失败条目**会被当作「没有」，
        于是调用方会重新排一次分析；未过期的失败条目照常返回（避免反复重试）。
        """
        key = str(uid or "")
        if not key:
            return None
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return None
            if not item.ok and retry_failed and self._failure_expired(item):
                return None
            return item

    def put(self, measurement: Measurement) -> None:
        if measurement is None or not measurement.uid:
            return
        with self._lock:
            self._entries[measurement.uid] = measurement
            self._dirty = True

    def remove(self, uid: str) -> bool:
        with self._lock:
            if str(uid) in self._entries:
                del self._entries[str(uid)]
                self._dirty = True
                return True
            return False

    def clear(self) -> int:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._dirty = True
        self.save(force=True)
        return count

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def measured_count(self) -> int:
        with self._lock:
            return sum(1 for m in self._entries.values() if m.ok)

    def source_offset_db(self, source: str) -> float:
        """同音源已测曲目的**中位增益**，用来兜底测不出来的曲子。

        只在样本足够（:data:`MIN_SOURCE_SAMPLES`）时给值，并夹在
        ±:data:`SOURCE_OFFSET_LIMIT_DB` 之内。这是统计估计、不是测量值，
        调用方必须把它标成 ``METHOD_OFFSET``，别和实测混为一谈。
        """
        key = str(source or "")
        if not key:
            return 0.0
        values: List[float] = []
        with self._lock:
            for m in self._entries.values():
                if m.ok and m.source == key and m.loudness_lufs is not None:
                    values.append(gain_db_for(m))
        if len(values) < MIN_SOURCE_SAMPLES:
            return 0.0
        values.sort()
        mid = len(values) // 2
        median = values[mid] if len(values) % 2 else (values[mid - 1] + values[mid]) / 2.0
        return max(-SOURCE_OFFSET_LIMIT_DB, min(SOURCE_OFFSET_LIMIT_DB, median))

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            values = list(self._entries.values())
        measured = [m for m in values if m.ok]
        failed = [m for m in values if not m.ok]
        loudness_values = [m.loudness_lufs for m in measured if m.loudness_lufs is not None]
        size = 0
        try:
            size = self._path.stat().st_size if self._path.exists() else 0
        except OSError:
            size = 0
        return {
            "total": len(values),
            "measured": len(measured),
            "failed": len(failed),
            "bytes": size,
            "avgLoudness": (
                round(sum(loudness_values) / len(loudness_values), 2) if loudness_values else None
            ),
        }

    # ── 内部 ────────────────────────────────────────────────

    def _failure_expired(self, item: Measurement) -> bool:
        return (time.time() - float(item.at or 0.0)) > FAILURE_TTL_SECONDS

    def _prune_locked(self) -> None:
        if len(self._entries) <= MAX_ENTRIES:
            return
        ordered = sorted(self._entries.items(), key=lambda kv: kv[1].at)
        for uid, _m in ordered[: len(self._entries) - MAX_ENTRIES]:
            del self._entries[uid]
        logger.info("响度缓存超出上限，已淘汰旧条目至 %d 条", len(self._entries))


def parse_replaygain_tag(value: str) -> Optional[float]:
    """解析 ``"-7.25 dB"`` 这类 ReplayGain 标签值，返回 dB；解析不了返回 ``None``。"""
    text = str(value or "").strip().lower().replace("db", "").strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
