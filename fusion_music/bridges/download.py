"""下载控制器（QML 侧）：右键另存为、批量下载、任务列表与进度。

线程模型与 :mod:`app.bridges.update` 一致：**worker 只在核心层改字段**，这里靠
``QTimer`` 定时把快照搬进列表模型；状态变化则通过 :class:`_Emitter` 从工作线程发
信号投回主线程（Qt 会自动排队到接收者线程）。工作线程绝不直接碰 QML 对象。

列表模型按 ``id`` 对齐地做增量更新（而不是每次重置整表）：进度每秒要刷好几次，
整表 reset 会让 ``ListView`` 重建所有委托 —— 悬停状态丢失、滚动位置乱跳、
进度条从头再动画一遍。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    QAbstractListModel, QByteArray, QModelIndex, Property, QObject, Qt, QTimer, QUrl, Signal, Slot,
)

from .. import paths, shellopen
from ..core import downloader, naming
from ..core.downloader import DownloadItem, DownloadManager, DownloadOptions
from ..core.trackutil import as_dict

logger = logging.getLogger(__name__)

#: 进度刷新间隔（毫秒）：worker 只写字段，这里定时读快照
POLL_INTERVAL_MS = 300

#: 任务列表暴露给 QML 的角色（QML 里直接写名字即可）
_ROLE_KEYS: List[str] = [
    "id", "uid", "name", "singer", "album", "source", "sourceText", "cover",
    "quality", "qualityLabel", "qualityActual", "qualityActualLabel", "degraded",
    "state", "stateText", "active", "finished", "progress", "sizeText", "speedText",
    "error", "warning", "path", "fileName", "directory",
]

class _Emitter(QObject):
    """工作线程 → 主线程的投递点。"""

    event = Signal(str, object)   # kind, 任务快照


class DownloadListModel(QAbstractListModel):
    """下载任务列表模型（新的任务排在最前面）。"""

    countChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = []

    count = Property(int, lambda self: len(self._rows), notify=countChanged)

    def roleNames(self) -> Dict[int, QByteArray]:  # noqa: N802
        return {Qt.UserRole + i + 1: QByteArray(name) for i, name in enumerate(_ROLE_KEYS)}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        row = self._rows[index.row()]
        field = role - (Qt.UserRole + 1)
        if 0 <= field < len(_ROLE_KEYS):
            return row.get(_ROLE_KEYS[field])
        if role == Qt.DisplayRole:
            return row.get("name")
        return None

    def rows(self) -> List[Dict[str, Any]]:
        return list(self._rows)

    def update(self, snapshots: List[Dict[str, Any]]) -> None:
        """按 ``id`` 对齐更新；顺序或成员变了才整表重置。"""
        incoming = list(snapshots or [])
        if len(incoming) != len(self._rows) or any(
            incoming[i].get("id") != self._rows[i].get("id") for i in range(len(incoming))
        ):
            self.beginResetModel()
            self._rows = incoming
            self.endResetModel()
            self.countChanged.emit()
            return
        for i, row in enumerate(incoming):
            if row != self._rows[i]:
                self._rows[i] = row
                index = self.index(i, 0)
                self.dataChanged.emit(index, index)


class DownloadController(QObject):
    changed = Signal()                 # 任务列表 / 计数变了
    message = Signal(str)
    errorOccurred = Signal(str)
    summary = Signal(str)              # 一批任务结束时的汇总
    fileCompleted = Signal(str, object)  # 本地路径, 任务快照（「加入本地曲库」用）
    playRequested = Signal(object)     # 要播放刚下好的文件（曲目字典）

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._tasks = DownloadListModel(self)
        self._emitter = _Emitter(self)
        self._emitter.event.connect(self._on_event)
        self._manager = DownloadManager(
            config,
            concurrency=config.get("download.concurrency", 2),
            on_event=self._from_worker,
        )
        self._poll = QTimer(self)
        self._poll.setInterval(POLL_INTERVAL_MS)
        self._poll.timeout.connect(self.refresh)
        # 一批任务的结束统计（在没有任何进行中任务时汇总成一条通知）
        self._batch: Dict[str, int] = {}
        self._batch_error = ""
        self._batch_dir = ""
        self._clear_stale_parts()

    # ── 启动清理 ────────────────────────────────────────────

    def _clear_stale_parts(self) -> None:
        try:
            downloader.cleanup_stale_parts(downloader.default_directory(self._config))
        except Exception as e:  # pragma: no cover - 清理失败不该挡住启动
            logger.debug("清理未完成下载失败: %s", e)

    # ── 工作线程 → 主线程 ───────────────────────────────────

    def _from_worker(self, kind: str, item: DownloadItem) -> None:
        """（工作线程）只做一件事：把快照投回主线程。"""
        self._emitter.event.emit(str(kind), item.snapshot())

    @Slot(str, object)
    def _on_event(self, kind: str, snapshot: Dict[str, Any]) -> None:
        """（主线程）任务状态变化。"""
        if kind == "finished" and isinstance(snapshot, dict):
            state = str(snapshot.get("state") or "")
            self._batch[state] = self._batch.get(state, 0) + 1
            self._batch_dir = str(snapshot.get("directory") or "") or self._batch_dir
            if state == downloader.STATE_FAILED and not self._batch_error:
                self._batch_error = str(snapshot.get("error") or "")
            # 「完成后加入本地音乐」是逐次下载的选项（对话框里能改），
            # 所以按快照里的勾选决定，而不是回头读设置
            if state == downloader.STATE_DONE and snapshot.get("path") \
                    and snapshot.get("addToLibrary"):
                self.fileCompleted.emit(str(snapshot["path"]), snapshot)
        self.refresh()
        if not self._manager.active_count():
            self._flush_summary()

    def _flush_summary(self) -> None:
        counts, self._batch = self._batch, {}
        error, self._batch_error = self._batch_error, ""
        if not counts:
            return
        done = counts.get(downloader.STATE_DONE, 0)
        failed = counts.get(downloader.STATE_FAILED, 0)
        skipped = counts.get(downloader.STATE_SKIPPED, 0)
        canceled = counts.get(downloader.STATE_CANCELED, 0)
        parts: List[str] = []
        for count, label in ((done, "成功"), (failed, "失败"), (skipped, "跳过"), (canceled, "取消")):
            if count:
                parts.append(f"{label} {count} 首")
        where = self._batch_dir or downloader.default_directory(self._config)
        text = f"下载结束：{'，'.join(parts)}（{where}）"
        if failed and error:
            text += f"。首个失败原因：{error}"
        self.summary.emit(text)

    # ── 只读信息 ────────────────────────────────────────────

    @Property(QObject, constant=True)
    def tasks(self):  # noqa: N802
        """下载任务列表模型（QML ``ListView`` 直接用）。"""
        return self._tasks

    @Property("QVariant", notify=changed)
    def counts(self):  # noqa: N802
        return self._manager.counts()

    @Property(int, notify=changed)
    def activeCount(self) -> int:  # noqa: N802
        return self._manager.active_count()

    @Property(int, notify=changed)
    def finishedCount(self) -> int:  # noqa: N802
        counts = self._manager.counts()
        return counts["total"] - counts["active"]

    @Property(bool, constant=True)
    def tagsAvailable(self) -> bool:  # noqa: N802
        """能不能写标签 / 嵌封面（取决于有没有装 mutagen）。"""
        import importlib.util

        try:
            return importlib.util.find_spec("mutagen") is not None
        except (ImportError, ValueError):
            return False

    @Property(str, constant=True)
    def downloadDir(self) -> str:  # noqa: N802
        return downloader.default_directory(self._config)

    @Property("QVariantList", constant=True)
    def duplicateOptions(self):  # noqa: N802
        """同名文件处理的选项（与设置页同一份文案）。"""
        return downloader.duplicate_choices()

    @Slot("QVariant", result="QVariant")
    def qualityOptions(self, track) -> List[Dict[str, Any]]:  # noqa: N802
        """某首曲目可选的音质（含「该音源有没有这一档」与预计体积）。"""
        data = as_dict(track)
        return _quality_options(data or None)

    @Slot(str, result=str)
    def qualityLabel(self, quality: str) -> str:  # noqa: N802
        """音质档位 → 中文名（菜单标题里用）。"""
        if str(quality or "") == "auto":
            return "自动"
        return downloader.quality_label(quality)

    @Slot(result="QVariant")
    def defaults(self):  # noqa: N802
        """批量下载对话框的默认值（设置里的那一套）。"""
        options = self._manager.options()
        return {
            "quality": options.quality,
            "directory": options.directory,
            "directoryUrl": QUrl.fromLocalFile(str(options.directory)).toString(),
            "template": options.template,
            "writeTags": options.write_tags,
            "saveLyric": options.save_lyric,
            "embedCover": options.embed_cover,
            "duplicate": options.duplicate,
            "addToLibrary": options.add_to_library,
            "concurrency": self._manager.clamp_concurrency(
                self._config.get("download.concurrency", 2)
            ),
            "tagsAvailable": self.tagsAvailable,
        }

    @Slot("QVariant", result="QVariant")
    def estimateOptions(self, tracks) -> List[Dict[str, Any]]:  # noqa: N802
        """每个音质档位下这批曲目的总体积（对话框里逐档显示）。

        某一档在这首曲子上没有时，按**降级链上的第一档**估 —— 跟上真正下载时
        取到的档位一致，用户看到的预计体积才不会离谱。
        """
        prepared = _downloadable(tracks)
        out: List[Dict[str, Any]] = []
        for quality in [downloader.AUTO_QUALITY, *downloader.QUALITY_ORDER]:
            total = 0
            for track in prepared:
                for candidate in downloader.quality_ladder(quality):
                    size = downloader.estimate_size(track, candidate)
                    if size:
                        total += size
                        break
            name = (
                downloader.AUTO_QUALITY_LABEL
                if quality == downloader.AUTO_QUALITY
                else downloader.quality_label(quality)
            )
            out.append({
                "id": quality,
                "name": name,
                "count": len(prepared),
                "size": total,
                "sizeText": downloader.human_size(total) if total else "",
            })
        return out

    @Slot(str, result=str)
    def normalizeDir(self, url: str) -> str:  # noqa: N802
        """``file://`` URL（或手输路径）→ 本地路径。"""
        return paths.normalize_folder(url)

    # ── 另存为（单曲）──────────────────────────────────────

    @Slot("QVariant", str, result="QVariant")
    def saveAsHint(self, track, quality: str) -> Dict[str, str]:  # noqa: N802
        """另存为对话框的默认值：``{name, suffix, file, folder}``（后两个是 file:// URL）。"""
        data = as_dict(track)
        options = self._manager.options()
        stem = naming.suggested_stem(data, options.template)
        suffix = naming.quality_suffix(str(quality or options.quality))
        folder = self._save_dir()
        name = f"{stem}{suffix}"
        return {
            "name": name,
            "suffix": suffix.lstrip("."),
            "file": QUrl.fromLocalFile(str(folder / name)).toString(),
            "folder": QUrl.fromLocalFile(str(folder)).toString(),
        }

    @Slot("QVariant", str, str, result=bool)
    def saveAs(self, track, quality: str, url: str) -> bool:  # noqa: N802
        """单曲另存为：``url`` 是 QML ``FileDialog`` 给的 ``file://`` URL。"""
        data = as_dict(track)
        if not data:
            self.errorOccurred.emit("没有要下载的歌曲")
            return False
        target = paths.from_file_url(url)
        if not target:
            self.errorOccurred.emit("没有选择保存位置")
            return False
        if Path(target).is_dir():
            self.errorOccurred.emit("保存位置是一个文件夹，请指定文件名")
            return False
        options = self._manager.options(
            quality=str(quality or "") or None,
            # 另存为的对话框已经问过「要不要覆盖」，这里不再二次拦截
            duplicate="overwrite",
        )
        options.directory = str(Path(target).parent)
        options.target = target
        items = self._manager.enqueue([data], options)
        if not items:
            self.errorOccurred.emit("这首歌没有可下载的信息")
            return False
        self._remember_dir(str(Path(target).parent))
        self._start_polling()
        self.refresh()
        self.message.emit(f"开始下载：{Path(target).name}")
        return True

    # ── 批量下载 ────────────────────────────────────────────

    @Slot("QVariant", "QVariant", result=int)
    def enqueueBatch(self, tracks, values) -> int:  # noqa: N802
        """批量下载（对话框确认后调用），返回真正入队的曲目数。"""
        prepared = _downloadable(tracks)
        dropped = len(_as_list(tracks)) - len(prepared)
        if not prepared:
            self.errorOccurred.emit("选中的歌曲里没有可下载的（本地曲目无需下载）")
            return 0
        options = self._options_from(values)
        items = self._manager.enqueue(prepared, options)
        if not items:
            return 0
        self._remember_dir(options.directory)
        self._start_polling()
        self.refresh()
        message = f"已加入下载队列：{len(items)} 首"
        if dropped:
            message += f"（{dropped} 首本地曲目已跳过）"
        self.message.emit(message)
        return len(items)

    def _options_from(self, values: Any) -> DownloadOptions:
        """对话框的取值（QVariantMap）→ 下载选项，缺项一律用设置里的默认值。"""
        data = as_dict(values)
        options = self._manager.options(
            quality=str(data.get("quality") or "") or None,
            directory=str(data.get("directory") or "") or None,
            template=str(data.get("template") or "") or None,
            write_tags=_as_bool(data.get("writeTags"), None),
            save_lyric=_as_bool(data.get("saveLyric"), None),
            embed_cover=_as_bool(data.get("embedCover"), None),
            duplicate=str(data.get("duplicate") or "") or None,
            add_to_library=_as_bool(data.get("addToLibrary"), None),
        )
        return options

    # ── 任务控制 ────────────────────────────────────────────

    @Slot(int, result=bool)
    def cancel(self, item_id: int) -> bool:  # noqa: N802
        ok = self._manager.cancel(item_id)
        if ok:
            self.refresh()
        return ok

    @Slot(result=int)
    def cancelAll(self) -> int:  # noqa: N802
        count = self._manager.cancel_all()
        self.refresh()
        return count

    @Slot(int, result=bool)
    def retry(self, item_id: int) -> bool:  # noqa: N802
        ok = self._manager.retry(item_id)
        if ok:
            self._start_polling()
            self.refresh()
        return ok

    @Slot(int, result=bool)
    def removeTask(self, item_id: int) -> bool:  # noqa: N802
        ok = self._manager.remove(item_id)
        if ok:
            self.refresh()
        return ok

    @Slot(result=int)
    def retryFailed(self) -> int:  # noqa: N802
        count = sum(
            1 for item in self._manager.items()
            if item.state == downloader.STATE_FAILED and self._manager.retry(item.id)
        )
        if count:
            self._start_polling()
            self.refresh()
        return count

    @Slot(result=int)
    def clearFinished(self) -> int:  # noqa: N802
        count = self._manager.clear_finished()
        if count:
            self.refresh()
        return count

    @Slot()
    def refresh(self) -> None:  # noqa: N802
        self._tasks.update(self._manager.snapshot())
        self.changed.emit()
        if not self._manager.active_count():
            self._stop_polling()

    @Slot(bool)
    def setPolling(self, value: bool) -> None:  # noqa: N802
        """下载页可见时才轮询进度（不可见时没必要每 300ms 搬一次快照）。"""
        if value:
            self.refresh()
            self._start_polling()
        else:
            self._stop_polling()

    def _start_polling(self) -> None:
        if self._manager.active_count() and not self._poll.isActive():
            self._poll.start()

    def _stop_polling(self) -> None:
        if self._poll.isActive():
            self._poll.stop()

    # ── 打开文件 / 目录 ─────────────────────────────────────

    @Slot(str)
    def openPath(self, path: str) -> None:  # noqa: N802
        target = str(path or "").strip() or downloader.default_directory(self._config)
        try:
            shellopen.open_path(target)
        except OSError as e:
            logger.warning("打开路径失败 %s: %s", target, e)
            self.errorOccurred.emit(f"无法打开路径：{target}")

    @Slot(int)
    def playTask(self, item_id: int) -> None:  # noqa: N802
        """播放刚下好的文件（读文件标签造曲目，播放交给播放引擎）。"""
        from ..core.resolver import resolve_local_metadata

        item = self._manager.item(item_id)
        if item is None or not item.target:
            self.errorOccurred.emit("这个任务还没有落盘文件")
            return
        path = str(item.target)
        track = None
        try:
            track = resolve_local_metadata(path)
        except Exception as e:  # pragma: no cover - 读标签失败就用最小曲目信息
            logger.debug("读取下载文件失败 %s: %s", path, e)
        payload = track.to_dict() if track is not None else {
            "source": "local", "songmid": path, "path": path,
            "name": item.track.name or Path(path).stem, "singer": item.track.singer,
            "album": item.track.album, "cover": item.track.cover,
        }
        self.playRequested.emit(payload)

    @Slot(int)
    def showInFolder(self, item_id: int) -> None:  # noqa: N802
        """在资源管理器里定位到文件（Windows 用 ``/select,``，其它平台开父目录）。"""
        item = self._manager.item(item_id)
        if item is None or not item.target:
            self.openPath(downloader.default_directory(self._config))
            return
        path = str(item.target)
        try:
            shellopen.reveal_in_folder(path)
        except OSError as e:
            logger.warning("定位文件失败 %s: %s", path, e)
            self.errorOccurred.emit("无法在文件夹中定位这个文件")

    # ── 目录记忆 ────────────────────────────────────────────

    def _save_dir(self) -> Path:
        """另存为的起始目录：上次存过的 → 设置里的下载目录 → 程序目录/downloads。"""
        remembered = str(self._config.get("download.last_dir", "") or "").strip()
        for candidate in (remembered, downloader.default_directory(self._config)):
            if not candidate:
                continue
            path = Path(candidate).expanduser()
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError:
                continue
            if path.is_dir():
                return path
        return Path(paths.program_dir() / "downloads")

    def _remember_dir(self, directory: str) -> None:
        text = str(directory or "").strip()
        if not text:
            return
        try:
            self._config.set("download.last_dir", text)
        except Exception as e:  # pragma: no cover - 记不住不影响下载
            logger.debug("记住下载目录失败: %s", e)

    # ── 关闭 ────────────────────────────────────────────────

    def shutdown(self) -> None:
        """退出前取消所有任务并等工作线程收工（避免留下半截文件）。"""
        self._stop_polling()
        self._manager.shutdown()


# ──────────────────────────────────────────────────────────────
# 辅助
# ──────────────────────────────────────────────────────────────


def _as_list(value: Any) -> List[Any]:
    """QML 传来的列表统一成 Python list。

    ``@Slot(..., "QVariant")`` 收到的是 ``QVariantList``（Python list）还是
    ``QJSValue``（QML 里 ``property var tracks: []`` 这种真·JS 数组）取决于
    调用现场，实测两种都会遇到：直接 ``for`` 一个 QJSValue 会抛
    ``TypeError: 'QJSValue' object is not iterable``（而且报在 QML 那一行，
    很容易误判成界面写错了）。
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        return list(value)
    to_variant = getattr(value, "toVariant", None)
    if callable(to_variant):
        try:
            value = to_variant()
        except Exception:  # pragma: no cover - QJSValue 已失效
            return []
        return list(value) if isinstance(value, (list, tuple)) else []
    try:
        return list(value)
    except TypeError:
        return []


def _quality_options(track: Any) -> List[Dict[str, Any]]:
    """音质选项：先「自动」，再四档具体音质（带可用性与预计体积）。"""
    auto: Dict[str, Any] = dict(downloader.quality_choices()[0])
    auto.update({"available": True, "size": 0, "sizeText": ""})
    return [auto, *downloader.quality_options(track)]


def _downloadable(tracks: Any) -> List[Dict[str, Any]]:
    """筛选出真正能下载的曲目（去重、剔除本地与缺 ID 的）。"""
    out: List[Dict[str, Any]] = []
    seen = set()
    for value in _as_list(tracks):
        data = as_dict(value)
        if not data:
            continue
        source = str(data.get("source") or "")
        songmid = str(data.get("songmid") or "").strip()
        if source == "local" or not songmid:
            continue
        key = f"{source}:{songmid}"
        if key in seen:
            continue
        seen.add(key)
        out.append(data)
    return out





def _as_bool(value: Any, default: Optional[bool]) -> Optional[bool]:
    """三态开关：``None`` 表示「这次不改，用默认值」。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)
