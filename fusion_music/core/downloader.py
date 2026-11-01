"""歌曲下载引擎。

设计要点（每一条都对应线上真实会踩的坑）
------------------------------------------
* **逐级降档取地址**：用户选了某一档就用 ``sources.get_music_url_exact()`` 只试那一档，
  失败再往**下**试（128k 起步），不做静默升级 —— 与播放链路同源，但方向不同：
  播放要「尽量好听」，下载要「用户要什么给什么，给不了就告诉他实际是什么」。
* **拿到地址不等于拿到歌**：未登录 / 非会员请求无损会拿到 128K 甚至 30 秒试听片段，
  CDN 出错时回的是一页 HTML。落盘前一律过 ``resolver`` 的文件头 + 时长双重校验，
  这正是播放链路用来防「试听片段当完整曲目」的判据。
* **原子落盘**：先写 ``<目标名>.part``，校验通过才改名。失败、取消、校验不过
  一律删掉半成品 —— 绝不能留下半个文件被本地扫描扫进曲库。
* **扩展名按文件头纠正**：B 站的 dash 音轨没有后缀、网易云的地址写着 ``.mp3``
  却可能回 m4a，只有魔术字节说了算（见 :mod:`app.core.naming`）。
* **worker 绝不碰 Qt**：线程只改 :class:`DownloadItem` 上的字段，界面侧由
  ``QTimer`` 轮询快照 + 少量信号投递（与 :mod:`app.bridges.update` 同一套模式）。
* **取消是协作式的**：每个任务一个 :class:`threading.Event`，下载循环每读一块检查一次。
"""

from __future__ import annotations

import logging
import os
import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import download_writer, naming
from .models import LOCAL_SOURCE, Track
from .trackutil import as_track, field
from ..lazy import requests
from ..sources.base import QualityLevel
from .. import paths
from ..units import human_size, to_int

logger = logging.getLogger(__name__)

#: 音质档位（高 → 低）。用 ``QualityLevel.ALL`` 这一份，别处不再抄一遍。
QUALITY_ORDER: Tuple[str, ...] = tuple(QualityLevel.ALL)

# ── 任务状态 ────────────────────────────────────────────────────
STATE_QUEUED = "queued"
STATE_RESOLVING = "resolving"
STATE_DOWNLOADING = "downloading"
STATE_WRITING = "writing"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_CANCELED = "canceled"
STATE_SKIPPED = "skipped"

ACTIVE_STATES = (STATE_QUEUED, STATE_RESOLVING, STATE_DOWNLOADING, STATE_WRITING)
FINISHED_STATES = (STATE_DONE, STATE_FAILED, STATE_CANCELED, STATE_SKIPPED)

STATE_TEXTS: Dict[str, str] = {
    STATE_QUEUED: "排队中",
    STATE_RESOLVING: "解析地址",
    STATE_DOWNLOADING: "下载中",
    STATE_WRITING: "写入信息",
    STATE_DONE: "已完成",
    STATE_FAILED: "失败",
    STATE_CANCELED: "已取消",
    STATE_SKIPPED: "已跳过",
}

# ── 音质 ────────────────────────────────────────────────────────
#: 「自动」档：不指定档位，按该曲目可用的最高档取（见 :func:`quality_ladder`）
AUTO_QUALITY = "auto"
AUTO_QUALITY_LABEL = "自动（按可用最高音质）"

QUALITY_LABELS: Dict[str, str] = {
    "flac24bit": "Hi-Res 母带",
    "flac": "无损 FLAC",
    "320k": "高品质 320K",
    "128k": "标准 128K",
}

#: 同名文件处理策略的中文名（键与 :data:`app.core.naming.DUPLICATE_MODES` 一致）
DUPLICATE_LABELS: Dict[str, str] = {
    "rename": "自动加序号（1）",
    "skip": "跳过已存在的",
    "overwrite": "覆盖同名文件",
}

#: 估算体积用的码率（kbps）。无损按实测常见值取，只用于「预计体积」与磁盘预检查。
QUALITY_BITRATES: Dict[str, int] = {"flac24bit": 2300, "flac": 900, "320k": 320, "128k": 128}

#: 下载分块大小（64 KB：进度刷新够细，系统调用又不多）
CHUNK_SIZE = 64 * 1024
#: 单次下载的重试次数与退避基数（秒）
DOWNLOAD_ATTEMPTS = 3
RETRY_BACKOFF = 1.5
#: 连接 / 读取超时（秒）：大文件读得慢是正常的，所以读超时给得宽
CONNECT_TIMEOUT = 15
READ_TIMEOUT = 60
#: 进度里「速度」的刷新间隔（秒）
SPEED_WINDOW = 0.5


class DownloadError(Exception):
    """可预期的下载失败（消息可以直接显示给用户）。"""


class _Canceled(Exception):
    """内部信号：用户取消了任务。"""


class _UrlExpired(Exception):
    """内部信号：地址被拒（403/404/410），重试同一个地址没有意义，得重新解析。"""


# ──────────────────────────────────────────────────────────────
# 音质与体积
# ──────────────────────────────────────────────────────────────


def quality_ladder(requested: str) -> List[str]:
    """请求档位 → 尝试顺序：先本档，再逐级**向下**。

    ``auto`` 表示「尽力取最高」，按 flac24bit → flac → 320k → 128k 全试。
    非法档位按 ``auto`` 处理（配置被手改坏时不该什么都下不了）。
    """
    value = str(requested or "").strip().lower()
    if value not in QUALITY_ORDER:
        return list(QUALITY_ORDER)
    return list(QUALITY_ORDER[QUALITY_ORDER.index(value):])


def quality_label(quality: str) -> str:
    return QUALITY_LABELS.get(str(quality or "").strip().lower(), str(quality or ""))


def quality_choices(include_auto: bool = True) -> List[Dict[str, str]]:
    """给下拉框用的音质选项（``[{id, name}]``）。

    设置页的「默认音质」与下载对话框的档位菜单都从这里取 —— 原先两张表各写一份
    文案，改一处忘一处就会让两处显示不同的名字。
    """
    out: List[Dict[str, str]] = []
    if include_auto:
        out.append({"id": AUTO_QUALITY, "name": AUTO_QUALITY_LABEL})
    out.extend({"id": quality, "name": QUALITY_LABELS[quality]} for quality in QUALITY_ORDER)
    return out


def duplicate_choices() -> List[Dict[str, str]]:
    """同名文件处理的选项（设置页与下载对话框共用一份文案）。"""
    return [{"id": mode, "name": DUPLICATE_LABELS[mode]} for mode in naming.DUPLICATE_MODES]


def parse_size(value: Any) -> int:
    """音源给的体积 → 字节数。

    各家的写法完全不统一：酷我给的是字节数字符串，QQ / 酷狗给的是 ``"3.2M"``
    这类人话，网易云干脆不给。认不出来返回 0。
    """
    if value is None or isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().replace(" ", "")
    if not text:
        return 0
    unit = text[-1].upper()
    factor = {"B": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3}.get(unit)
    if factor is not None:
        text = text[:-1]
    else:
        factor = 1
    try:
        return max(0, int(float(text) * factor))
    except (TypeError, ValueError):
        return 0


def estimate_size(track: Any, quality: str) -> int:
    """估算这首曲子在该档位下的体积（字节）；拿不准返回 0。

    优先用音源给的**准确体积**（有就用，误差 0），否则按码率 × 时长估。
    """
    wanted = str(quality or "").strip().lower()
    for entry in _types_of(track):
        if str(entry.get("type") or "").strip().lower() != wanted:
            continue
        size = parse_size(entry.get("size"))
        if size > 0:
            return size
    try:
        interval = int(field(track, "interval") or 0)
    except (TypeError, ValueError):
        interval = 0
    if interval <= 0:
        return 0
    bitrate = QUALITY_BITRATES.get(wanted, 320)
    return int(interval * bitrate * 1000 / 8)


def quality_options(track: Any = None) -> List[Dict[str, Any]]:
    """给界面用的音质选项（含「该音源有没有这一档」与预计体积）。"""
    available = {
        str(entry.get("type") or "").strip().lower() for entry in _types_of(track)
    }
    out: List[Dict[str, Any]] = []
    for quality in QUALITY_ORDER:
        size = estimate_size(track, quality) if track is not None else 0
        out.append(
            {
                "id": quality,
                "name": QUALITY_LABELS[quality],
                "available": (quality in available) if available else True,
                "size": size,
                "sizeText": human_size(size) if size else "",
            }
        )
    return out


def _types_of(track: Any) -> List[Dict[str, Any]]:
    """取可用音质列表（``Track`` / ``MusicInfo`` / QML 字典都能吃）。"""
    raw = field(track, "types")
    if raw is None:
        raw = field(track, "type_detail")
    if isinstance(raw, dict):
        return [{"type": key, **(value if isinstance(value, dict) else {})}
                for key, value in raw.items()]
    if isinstance(raw, (list, tuple)):
        return [dict(entry) for entry in raw if isinstance(entry, dict)]
    return []


# ──────────────────────────────────────────────────────────────
# 任务选项与任务本身
# ──────────────────────────────────────────────────────────────


class DownloadOptions:
    """一次下载的选项（对话框里选的那些）。"""

    __slots__ = ("quality", "directory", "template", "write_tags", "save_lyric",
                 "embed_cover", "duplicate", "add_to_library", "target")

    def __init__(
        self,
        quality: str = "320k",
        directory: str = "",
        template: str = "{singer} - {name}",
        write_tags: bool = True,
        save_lyric: bool = True,
        embed_cover: bool = True,
        duplicate: str = "rename",
        add_to_library: bool = False,
        target: str = "",
    ) -> None:
        self.quality = str(quality or "320k")
        self.directory = str(directory or "")
        self.template = str(template or "{singer} - {name}")
        self.write_tags = bool(write_tags)
        self.save_lyric = bool(save_lyric)
        self.embed_cover = bool(embed_cover)
        self.duplicate = str(duplicate or "rename").strip().lower()
        if self.duplicate not in naming.DUPLICATE_MODES:
            self.duplicate = "rename"
        self.add_to_library = bool(add_to_library)
        #: 「另存为」指定的**完整落盘路径**（空 = 按模板在下载目录里命名）
        self.target = str(target or "")

    @classmethod
    def from_config(cls, config: Any, **overrides: Any) -> "DownloadOptions":
        """设置里的默认值 + 本次覆盖（``overrides`` 里为 ``None`` 的项忽略）。"""
        values: Dict[str, Any] = {
            "quality": str(config.get("download.quality", "320k") or "320k"),
            "directory": default_directory(config),
            "template": str(config.get("download.filename_template", "{singer} - {name}")
                            or "{singer} - {name}"),
            "write_tags": bool(config.get("download.write_tags", True)),
            "save_lyric": bool(config.get("download.save_lyric", True)),
            "embed_cover": bool(config.get("download.embed_cover", True)),
            "duplicate": str(config.get("download.duplicate", "rename") or "rename"),
            "add_to_library": bool(config.get("download.add_to_library", False)),
        }
        for key, value in (overrides or {}).items():
            if value is not None and key in values:
                values[key] = value
        return cls(**values)

    def snapshot(self) -> Dict[str, Any]:
        return {name: getattr(self, name) for name in self.__slots__}


def default_directory(config: Any = None) -> str:
    """默认下载目录：设置里配的，否则程序目录下的 ``downloads``。"""
    configured = ""
    if config is not None:
        configured = str(config.get("storage.download_dir", "") or "").strip()
    return configured or str(paths.program_dir() / "downloads")


class DownloadItem:
    """一条下载任务的可变状态：worker 写，界面读 :meth:`snapshot`。"""

    __slots__ = ("id", "track", "options", "index", "quality_actual", "state", "target",
                 "received", "total", "speed", "error", "warning", "fallback",
                 "created_at", "started_at", "finished_at", "cancel_event",
                 "_last_tick", "_last_bytes")

    def __init__(self, item_id: int, track: Track, options: DownloadOptions, index: int = 0):
        self.id = int(item_id)
        self.track = track
        self.options = options
        self.index = int(index or 0)
        self.quality_actual = ""
        self.state = STATE_QUEUED
        self.target = ""
        self.received = 0
        self.total = 0
        self.speed = 0.0
        self.error = ""
        self.warning = ""
        self.fallback = False
        self.created_at = time.time()
        self.started_at = 0.0
        self.finished_at = 0.0
        self.cancel_event = threading.Event()
        self._last_tick = 0.0
        self._last_bytes = 0

    # ── 只读信息 ────────────────────────────────────────────

    @property
    def uid(self) -> str:
        return self.track.uid

    @property
    def quality(self) -> str:
        return self.options.quality

    @property
    def name(self) -> str:
        return self.track.name

    @property
    def singer(self) -> str:
        return self.track.singer

    @property
    def canceled(self) -> bool:
        return self.cancel_event.is_set()

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def finished(self) -> bool:
        return self.state in FINISHED_STATES

    @property
    def progress(self) -> float:
        if self.state == STATE_DONE:
            return 1.0
        if self.total <= 0:
            return 0.0
        return max(0.0, min(1.0, self.received / float(self.total)))

    # ── 状态变更（都由 worker 调用）────────────────────────

    def begin_run(self) -> None:
        """开始（或重试）一次下载：清掉上一次的痕迹，换一个全新的取消事件。"""
        self.cancel_event = threading.Event()
        self.state = STATE_QUEUED
        self.received = 0
        self.total = 0
        self.speed = 0.0
        self.error = ""
        self.warning = ""
        self.quality_actual = ""
        self.fallback = False
        self.target = ""
        self.started_at = 0.0
        self.finished_at = 0.0
        self._last_tick = 0.0
        self._last_bytes = 0

    def request_cancel(self) -> None:
        self.cancel_event.set()

    def tick(self, received: int, total: int = -1) -> None:
        """更新进度（``total <= 0`` 表示不知道总大小，保持不变）。"""
        self.received = max(0, int(received))
        if total and total > 0:
            self.total = int(total)
        now = time.monotonic()
        if self._last_tick <= 0:
            self._last_tick, self._last_bytes = now, self.received
            return
        elapsed = now - self._last_tick
        if elapsed >= SPEED_WINDOW:
            self.speed = max(0.0, (self.received - self._last_bytes) / elapsed)
            self._last_tick, self._last_bytes = now, self.received

    # ── 给界面的快照 ────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """给 QML 的只读快照（字段名是 QML 侧直接读的，改这里要看 QML）。"""
        actual = self.quality_actual or self.options.quality
        return {
            "id": self.id,
            "uid": self.uid,
            "name": self.track.name,
            "singer": self.track.singer,
            "album": self.track.album,
            "source": self.track.source,
            "sourceText": self.track.source_text,
            "cover": self.track.cover,
            "isLocal": self.track.source == LOCAL_SOURCE,
            "quality": self.options.quality,
            "qualityLabel": quality_label(self.options.quality),
            "qualityActual": self.quality_actual,
            "qualityActualLabel": quality_label(actual),
            # 实际拿到的档位与请求的不一致（降到下一档了）——界面要标出来
            "degraded": bool(self.quality_actual) and self.quality_actual != self.options.quality,
            "state": self.state,
            "stateText": STATE_TEXTS.get(self.state, self.state),
            "active": self.active,
            "finished": self.finished,
            "progress": round(self.progress, 4),
            "received": self.received,
            "total": self.total,
            "sizeText": (f"{human_size(self.received)} / {human_size(self.total)}"
                         if self.total > 0 else human_size(self.received)),
            "speedText": (f"{human_size(self.speed)}/s" if self.speed > 0 else ""),
            "error": self.error,
            "warning": self.warning,
            "path": self.target,
            "fileName": Path(self.target).name if self.target else "",
            "directory": str(Path(self.target).parent) if self.target else self.options.directory,
            "fallback": self.fallback,
            # 「完成后加入本地音乐」是**逐次下载**的选项（对话框里能改），
            # 所以界面要看这一份，而不是回头读设置
            "addToLibrary": self.options.add_to_library,
        }


# ──────────────────────────────────────────────────────────────
# 下载会话
# ──────────────────────────────────────────────────────────────

_session_lock = threading.Lock()
_session: Optional[requests.Session] = None


def download_session() -> requests.Session:
    """下载专用会话（浏览器 UA）。

    不复用音源自己的 ``Session``：那里面带着搜索接口要的头与状态，而下载需要的
    是「每个音源自己的 Referer / Cookie」—— 这些逐任务附带（B 站的 buvid3 必须与
    地址里的签名配套），会话本身共用一份就够了。
    """
    global _session
    with _session_lock:
        if _session is None:
            from ..sources.utils import create_session

            _session = create_session()
        return _session


def cleanup_stale_parts(directory: str) -> int:
    """删掉下载目录里残留的 ``*.part``（上次异常退出留下的半成品）。

    只在启动时调用：那一刻不可能有正在写的任务，所以不按时间判断 —— 留着它们只会
    让用户看到一个「永远下不完」的文件。
    """
    removed = 0
    try:
        candidates = list(Path(str(directory or "")).glob("*.part"))
    except OSError:
        return 0
    for path in candidates:
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    if removed:
        logger.info("清理了 %d 个未完成的下载文件（*.part）", removed)
    return removed


# ──────────────────────────────────────────────────────────────
# 引擎
# ──────────────────────────────────────────────────────────────


class DownloadManager:
    """下载队列 + 工作线程池。

    ``on_event(kind, item)`` 会在**工作线程**里被调用（``kind`` 为 ``queued`` /
    ``state`` / ``finished``），接收方自己负责切回主线程（Qt 信号自动排队）。
    """

    def __init__(
        self,
        config: Any = None,
        *,
        concurrency: int = 2,
        on_event: Optional[Callable[[str, "DownloadItem"], None]] = None,
    ) -> None:
        self._config = config
        self._lock = threading.RLock()
        self._rename_lock = threading.Lock()
        self._items: "Dict[int, DownloadItem]" = {}
        self._order: List[int] = []
        self._queue: "queue.Queue[Optional[DownloadItem]]" = queue.Queue()
        self._workers: List[threading.Thread] = []
        self._shutdown = threading.Event()
        self._seq = 0
        self._on_event = on_event
        self._concurrency = self.clamp_concurrency(concurrency)

    # ── 配置 ────────────────────────────────────────────────

    @staticmethod
    def clamp_concurrency(value: Any, default: int = 2) -> int:
        """并发数夹到 1~6：太高会被音源风控，太低则批量下载慢得难受。"""
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(1, min(6, number))

    def set_concurrency(self, value: Any) -> int:
        self._concurrency = self.clamp_concurrency(value, self._concurrency)
        self._ensure_workers()
        return self._concurrency

    def options(self, **overrides: Any) -> DownloadOptions:
        """按设置里的默认值构造选项（``overrides`` 里为 ``None`` 的项忽略）。"""
        if self._config is None:
            values = {"directory": default_directory(None)}
            values.update({k: v for k, v in overrides.items() if v is not None})
            return DownloadOptions(**values)
        return DownloadOptions.from_config(self._config, **overrides)

    # ── 入队与查询 ──────────────────────────────────────────

    def enqueue(self, tracks: Iterable[Any], options: DownloadOptions) -> List[DownloadItem]:
        """把若干曲目排进队列，返回新建的任务（顺序与传入一致）。"""
        new_items: List[DownloadItem] = []
        prepared = [
            t for t in (as_track(value, strict=True) for value in tracks or []) if t is not None
        ]
        with self._lock:
            for index, track in enumerate(prepared, start=1):
                self._seq += 1
                item = DownloadItem(self._seq, track, options, index=index)
                self._items[item.id] = item
                self._order.append(item.id)
                new_items.append(item)
        if not new_items:
            return []
        self._ensure_workers()
        for item in new_items:
            self._queue.put(item)
            self._emit("queued", item)
        return new_items

    def items(self) -> List[DownloadItem]:
        """按入队顺序返回（老的在前面）。"""
        with self._lock:
            return [self._items[i] for i in self._order if i in self._items]

    def item(self, item_id: Any) -> Optional[DownloadItem]:
        try:
            key = int(item_id)
        except (TypeError, ValueError):
            return None
        with self._lock:
            return self._items.get(key)

    def snapshot(self) -> List[Dict[str, Any]]:
        """给界面的任务快照，**新的在前面**（列表从上往下就是最近的）。"""
        return [item.snapshot() for item in reversed(self.items())]

    def counts(self) -> Dict[str, int]:
        items = self.items()
        counts: Dict[str, int] = {state: 0 for state in STATE_TEXTS}
        counts["total"] = len(items)
        counts["active"] = 0
        for item in items:
            counts[item.state] = counts.get(item.state, 0) + 1
            if item.active:
                counts["active"] += 1
        return counts

    def active_count(self) -> int:
        return self.counts()["active"]

    # ── 控制 ────────────────────────────────────────────────

    def cancel(self, item_id: Any) -> bool:
        item = self.item(item_id)
        if item is None or not item.active:
            return False
        item.request_cancel()
        if item.state == STATE_QUEUED:
            # 还没被 worker 取走：直接落状态，等它拿到时一眼就知道该跳过
            self._finish(item, STATE_CANCELED, "已取消")
        return True

    def cancel_all(self) -> int:
        return sum(1 for item in self.items() if self.cancel(item.id))

    def retry(self, item_id: Any) -> bool:
        item = self.item(item_id)
        if item is None or not item.finished or item.state == STATE_DONE:
            return False
        item.begin_run()
        self._ensure_workers()
        self._queue.put(item)
        self._emit("queued", item)
        return True

    def remove(self, item_id: Any) -> bool:
        """从列表里移除一条**已结束**的任务（进行中的不许删）。"""
        item = self.item(item_id)
        if item is None or not item.finished:
            return False
        with self._lock:
            self._items.pop(item.id, None)
            if item.id in self._order:
                self._order.remove(item.id)
        return True

    def clear_finished(self) -> int:
        return sum(1 for item in self.items() if self.remove(item.id))

    def shutdown(self, timeout: float = 6.0) -> None:
        """退出前调用：取消所有任务、唤醒并等 worker 收工。"""
        self._shutdown.set()
        for item in self.items():
            item.request_cancel()
        with self._lock:
            workers, self._workers = list(self._workers), []
        for _ in workers:
            self._queue.put(None)
        budget = max(0.1, float(timeout) / max(1, len(workers)))
        for thread in workers:
            thread.join(timeout=budget)

    # ── 线程池 ──────────────────────────────────────────────

    def _ensure_workers(self) -> None:
        with self._lock:
            if self._shutdown.is_set():
                return
            alive = [t for t in self._workers if t.is_alive()]
            while len(alive) < self._concurrency:
                thread = threading.Thread(
                    target=self._worker, name=f"download-{len(alive) + 1}", daemon=True
                )
                thread.start()
                alive.append(thread)
            self._workers = alive

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            try:
                if item is None:
                    return
                if self._shutdown.is_set():
                    self._finish(item, STATE_CANCELED, "程序退出，已取消")
                    continue
                if item.canceled or item.state == STATE_CANCELED:
                    self._finish(item, STATE_CANCELED, "已取消")
                    continue
                self._run_item(item)
            except _Canceled:
                self._finish(item, STATE_CANCELED, "已取消")
            except DownloadError as e:
                self._finish(item, STATE_FAILED, str(e))
            except Exception as e:  # pragma: no cover - 兜底，别把线程里的异常吞掉
                logger.exception("下载任务异常: %s", item.track if item else "")
                if item is not None:
                    self._finish(item, STATE_FAILED, f"内部错误：{e}")
            finally:
                self._queue.task_done()

    # ── 单个任务 ────────────────────────────────────────────

    def _run_item(self, item: DownloadItem) -> None:
        track = item.track
        if track.source == LOCAL_SOURCE or not str(track.songmid or "").strip():
            raise DownloadError("本地曲目无需下载")

        directory = self._prepare_directory(item.options.directory)
        explicit = str(item.options.target or "").strip()
        if explicit:
            # 「另存为」：用户已经指定了完整路径，同名策略交给对话框（已确认覆盖）
            guess = Path(explicit)
            duplicate = "overwrite"
        else:
            stem = naming.suggested_stem(track, item.options.template, index=item.index)
            guess = directory / (stem + naming.quality_suffix(item.options.quality))
            duplicate = item.options.duplicate
        if duplicate == "skip" and guess.exists():
            self._finish(item, STATE_SKIPPED, "同名文件已存在")
            return

        part = guess.with_name(guess.name + ".part")
        item.started_at = time.time()
        try:
            self._set_state(item, STATE_RESOLVING)
            info = track.to_music_info()
            url, quality, note = self._resolve_url(item, info)
            item.quality_actual = quality
            item.warning = note
            if item.canceled:
                raise _Canceled
            self._set_state(item, STATE_DOWNLOADING)
            self._check_disk_space(directory, item)
            self._download_with_retries(item, info, url, part)
            if item.canceled:
                raise _Canceled
            self._validate(item, part)
            self._set_state(item, STATE_WRITING)
            final = self._finalize(item, part, duplicate)
            if final is None:
                self._finish(item, STATE_SKIPPED, "同名文件已存在")
                return
            item.target = str(final)
            try:
                size = os.path.getsize(final)
                item.received = item.total = size
            except OSError:
                pass
            self._write_extras(item, final)
            item.finished_at = time.time()
            self._finish(item, STATE_DONE, "")
        finally:
            # 走到这里说明要么已经改名成功（part 不存在），要么中途失败 / 取消：
            # 一律清掉半成品，绝不留下会被本地扫描当成歌曲的残缺文件
            _unlink(part)

    def _prepare_directory(self, value: str) -> Path:
        directory = Path(str(value or "").strip() or default_directory(self._config))
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise DownloadError(f"下载目录不可用：{e}") from e
        if not directory.is_dir():
            raise DownloadError("下载目录不可用：目标不是文件夹")
        probe = directory / f".fusion_write_probe_{os.getpid()}"
        try:
            probe.write_bytes(b"")
            probe.unlink(missing_ok=True)
        except OSError as e:
            raise DownloadError(f"下载目录不可写：{e}") from e
        return directory

    def _check_disk_space(self, directory: Path, item: DownloadItem) -> None:
        needed = estimate_size(item.track, item.quality_actual or item.options.quality)
        if needed <= 0:
            return
        try:
            free = int(shutil.disk_usage(str(directory)).free)
        except OSError:
            return
        if free < needed * 1.05:
            raise DownloadError(
                f"磁盘空间不足（需要约 {human_size(needed)}，可用 {human_size(free)}）"
            )

    def _resolve_url(self, item: DownloadItem, info: Any) -> Tuple[str, str, str]:
        """按「本档 → 逐级向下」取地址，返回 ``(地址, 实际档位, 降级说明)``。"""
        from .. import sources

        requested = item.options.quality
        for quality in quality_ladder(requested):
            if item.canceled:
                raise _Canceled
            url = sources.get_music_url_exact(info, quality, item.track.source)
            if url:
                note = ""
                if quality != requested:
                    note = (f"该音源没有{quality_label(requested)}，"
                            f"实际下载的是{quality_label(quality)}")
                return str(url), quality, note
        raise DownloadError("该音源没有可用地址（可能没有版权或需要会员）")

    def _download_with_retries(self, item: DownloadItem, info: Any, url: str, part: Path) -> None:
        last_error = ""
        for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
            if item.canceled:
                raise _Canceled
            try:
                self._download_stream(item, url, part)
                return
            except _Canceled:
                raise
            except _UrlExpired as e:
                if attempt >= DOWNLOAD_ATTEMPTS:
                    raise DownloadError(f"音源拒绝下载（{e}），地址可能已失效") from e
                # 地址里通常带着时间戳签名：换一个再试，重试同一个没有意义
                url, quality, note = self._resolve_url(item, info)
                item.quality_actual = quality
                item.warning = note
            except requests.RequestException as e:
                last_error = _describe_network_error(e)
                if attempt >= DOWNLOAD_ATTEMPTS:
                    raise DownloadError(last_error) from e
            except OSError as e:
                raise DownloadError(_describe_os_error(e)) from e
            if item.canceled:
                raise _Canceled
            time.sleep(RETRY_BACKOFF * attempt)
        raise DownloadError(last_error or "下载失败")

    def _download_stream(self, item: DownloadItem, url: str, part: Path) -> None:
        from .. import sources

        headers = sources.download_headers(item.track.source)
        cookies = sources.download_cookies(item.track.source)
        part.parent.mkdir(parents=True, exist_ok=True)
        with download_session().get(
            url,
            headers=headers,
            cookies=cookies,
            stream=True,
            timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
            allow_redirects=True,
        ) as resp:
            status = int(resp.status_code)
            # 403 / 404 基本都是签名过期或已下架，重试同一个地址没用
            if status in (401, 403, 404, 410, 416):
                raise _UrlExpired(f"HTTP {status}")
            if status >= 400:
                raise requests.HTTPError(f"HTTP {status}", response=resp)
            total = to_int(resp.headers.get("Content-Length"), minimum=0)
            item.tick(0, total)
            received = 0
            with open(part, "wb") as handle:
                for chunk in resp.iter_content(CHUNK_SIZE):
                    if item.canceled:
                        raise _Canceled
                    if not chunk or not isinstance(chunk, (bytes, bytearray)):
                        continue
                    handle.write(chunk)
                    received += len(chunk)
                    item.tick(received, total)
        if received <= 0:
            raise DownloadError("音源返回了空内容")

    def _validate(self, item: DownloadItem, part: Path) -> None:
        from . import resolver

        path = str(part)
        if not resolver.validate_audio_file_header(path):
            raise DownloadError("拿到的不是音频文件（音源可能返回了错误页）")
        if not resolver.validate_audio_duration(path, item.track.interval):
            raise DownloadError("时长与歌曲不符（多半是试听片段，需要会员或已下架）")

    def _finalize(self, item: DownloadItem, part: Path, duplicate: str) -> Optional[Path]:
        """按文件头纠正扩展名，再按同名策略落成最终文件；``None`` 表示跳过。"""
        suffix = (naming.sniff_file_suffix(str(part))
                  or naming.quality_suffix(item.quality_actual or item.options.quality))
        text = str(part)
        guess = Path(text[: -len(".part")]) if text.endswith(".part") else Path(text)
        corrected = naming.correct_suffix(guess, suffix)
        with self._rename_lock:
            final = naming.resolve_target(corrected, duplicate)
            if final is None:
                return None
            try:
                final.parent.mkdir(parents=True, exist_ok=True)
                os.replace(part, final)
            except OSError as e:
                raise DownloadError(_describe_os_error(e)) from e
        return final

    def _write_extras(self, item: DownloadItem, final: Path) -> None:
        """写标签 / 内嵌封面 / 歌词边车：失败只记进 ``warning``，不动已落盘的音频。"""
        notes: List[str] = []
        if item.options.write_tags:
            cover = download_writer.fetch_cover(item.track.to_dict()) if item.options.embed_cover else None
            try:
                download_writer.embed_tags(str(final), item.track, cover=cover)
            except download_writer.WriteError as e:
                notes.append(str(e))
        if item.options.save_lyric:
            try:
                text = self._fetch_lyric(item)
                if text:
                    download_writer.write_lyric(str(final), text)
                else:
                    notes.append("音源没有返回歌词")
            except download_writer.WriteError as e:
                notes.append(str(e))
        if notes:
            item.warning = "；".join([item.warning] + notes) if item.warning else "；".join(notes)

    @staticmethod
    def _fetch_lyric(item: DownloadItem) -> str:
        from .. import sources

        try:
            return str(sources.get_lyric(item.track.to_music_info(), item.track.source) or "")
        except Exception as e:  # pragma: no cover - 音源异常一律当「没有歌词」
            logger.debug("取歌词失败 %s: %s", item.track.name, e)
            return ""

    # ── 状态与事件 ──────────────────────────────────────────

    def _set_state(self, item: DownloadItem, state: str) -> None:
        item.state = state
        self._emit("state", item)

    def _finish(self, item: DownloadItem, state: str, message: str) -> None:
        item.state = state
        item.speed = 0.0
        item.finished_at = item.finished_at or time.time()
        if state == STATE_FAILED:
            item.error = message
        elif message:
            item.warning = "；".join([item.warning, message]) if item.warning else message
        self._emit("finished", item)

    def _emit(self, kind: str, item: DownloadItem) -> None:
        handler = self._on_event
        if handler is None:
            return
        try:
            handler(kind, item)
        except Exception as e:  # pragma: no cover - 回调炸了不该影响下载
            logger.debug("下载事件回调异常: %s", e)


# ──────────────────────────────────────────────────────────────
# 辅助
# ──────────────────────────────────────────────────────────────


def _unlink(path: Path) -> None:
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _describe_os_error(error: OSError) -> str:
    import errno

    if getattr(error, "errno", None) == errno.ENOSPC:
        return "磁盘空间不足，写入失败"
    if isinstance(error, PermissionError):
        return "没有写入权限，请换一个下载目录"
    return f"写入失败：{error.strerror or error}"


def _describe_network_error(error: Exception) -> str:
    """网络异常 → 能读懂的中文原因。"""
    text = str(error)
    lowered = text.lower()
    if isinstance(error, requests.Timeout) or "timed out" in lowered:
        return "下载超时（已重试 3 次）"
    if "getaddrinfo" in lowered or "name or service not known" in lowered or "max retries" in lowered:
        return "网络连接失败，请检查网络或代理设置"
    if "connection" in lowered and ("reset" in lowered or "aborted" in lowered):
        return "连接被中断（已重试 3 次）"
    detail = f"{error.__class__.__name__} {text}".strip()
    return f"下载失败：{detail[:120]}"
