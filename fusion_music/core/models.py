"""曲目数据模型与 QML 列表模型。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, Property, Qt, Signal, Slot

from ..sources.base import MusicInfo

LOCAL_SOURCE = "local"

def format_duration(seconds: int | float) -> str:
    """秒 → ``m:ss``。"""
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "0:00"
    if s <= 0:
        return "0:00"
    if s >= 3600:
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}"
    m, sec = divmod(s, 60)
    return f"{m}:{sec:02d}"

#: 音质档位的**短名**：列表角标、播放栏那一颗标签用。
#: 下载对话框里的长名（"无损 FLAC" / "高品质 320K"）在 app/core/downloader.py ——
#: 两者用途不同（一个是空间紧的角标，一个是菜单项），但短名只能有一份：
#: 以前 QML 里还硬编码过一套，播放栏叫「高品」、列表角标叫「320K」。
QUALITY_SHORT_LABELS = {
    "flac24bit": "Hi-Res",
    "flac": "无损",
    "320k": "320K",
    "128k": "标准",
    "lossless": "无损",
    "auto": "自动",
}


def quality_short(quality: str) -> str:
    """档位 → 短名（认不出来的档位原样返回）。"""
    text = str(quality or "").strip().lower()
    return QUALITY_SHORT_LABELS.get(text, text)


def source_display(source: str) -> str:
    from ..sources import SOURCE_NAMES

    if source == LOCAL_SOURCE:
        return "本地"
    return SOURCE_NAMES.get(source, source)

def normalize_singers(raw: str) -> str:
    """把歌手字段里的多个名字归一成「、」分隔（与在线音源保持一致）。

    在线各源都会过 ``sources/utils.py:format_singer`` —— 它把 ``&`` ``;`` ``/``
    换成「、」（``ui/ArtistNames.js`` 的注释也把这条当成不变量）。**本地文件是
    mutagen 直接读出来的原始标签，一直少了这一步**：标签里写着 ``洛天依/乐正绫``
    时，界面按「、」去拆、拆出来还是**一个**歌手 —— 看起来是一串、看不出是两个，点进去
    还会去找「洛天依/乐正绫」这个不存在的歌手（界面弹「没找到歌手」）。

    代价要知道：``AC/DC`` 这种名字里带斜杠的乐队同样会被拆开。这是**全应用既有
    的取舍**（网易云那边的 ``format_singer`` 早就这么干了），本地跟着统一，
    不去加「看着像乐队就不拆」的特例 —— 那只会让两边的行为更不一致。
    """
    from ..sources.utils import format_singer

    return format_singer(str(raw or ""))

@dataclass
class Track:
    """统一曲目模型（在线音源 + 本地文件共用）。"""

    source: str = ""
    songmid: str = ""
    name: str = ""
    singer: str = ""
    album: str = ""
    album_id: str = ""
    interval: int = 0
    cover: str = ""
    # 在线音源附加字段
    fee: int = 0
    publish_time: int = 0
    is_original: bool = False
    original_name: str = ""
    origin_song_id: str = ""
    origin_artists: List[str] = field(default_factory=list)
    types: List[Dict] = field(default_factory=list)
    type_detail: Dict[str, Any] = field(default_factory=dict)
    play_count: int = 0
    # 本地文件字段
    path: str = ""
    # 本地文件**在线匹配**到的身份（封面与歌词是从哪个音源的哪首歌来的，
    # 见 app/core/localmatch.py）。在线曲目留空，此时它自己的 source/songmid 就是身份。
    match_source: str = ""
    match_songmid: str = ""
    # 元信息
    added_at: int = 0
    # 推荐理由（「你关注的音乐人新歌」这种）。只用于展示，不进 to_dict，
    # 免得被写进歌单 / 收藏 / 缓存里。
    reason: str = ""

    # ── 派生属性 ────────────────────────────────────────────

    @property
    def uid(self) -> str:
        return f"{self.source}:{self.songmid}"

    @property
    def is_local(self) -> bool:
        return self.source == LOCAL_SOURCE

    @property
    def duration_text(self) -> str:
        return format_duration(self.interval)

    @property
    def source_text(self) -> str:
        return source_display(self.source)

    @property
    def quality_list(self) -> List[str]:
        """可用音质档位（低 → 高）。"""
        return [str(t.get("type")) for t in (self.types or []) if t.get("type")]

    @property
    def best_quality(self) -> str:
        qs = self.quality_list
        from ..sources.base import QualityLevel

        for q in QualityLevel.ALL:
            if q in qs:
                return q
        return "128k"

    @property
    def quality_short(self) -> str:
        """最佳档位的短名（列表角标用）。"""
        return quality_short(self.best_quality)

    @property
    def display_name(self) -> str:
        """带原唱/版本标记的展示名。"""
        if self.original_name and self.original_name not in self.name:
            return f"{self.name}（原唱：{self.original_name}）"
        return self.name

    # ── 转换 ────────────────────────────────────────────────

    @classmethod
    def from_music_info(cls, mi: MusicInfo) -> "Track":
        return cls(
            source=mi.source,
            songmid=str(mi.songmid),
            name=mi.name,
            singer=mi.singer,
            album=mi.album_name or "",
            album_id=str(mi.album_id or ""),
            interval=int(mi.interval or 0),
            cover=mi.img or "",
            fee=int(mi.fee or 0),
            publish_time=int(mi.publish_time or 0),
            is_original=bool(mi.is_original),
            original_name=mi.original_name or "",
            origin_song_id=str(mi.origin_song_id or ""),
            origin_artists=list(mi.origin_artists or []),
            types=list(mi.types or []),
            type_detail=dict(mi._types or {}),
            play_count=int(getattr(mi, "play_count", 0) or 0),
        )

    def to_music_info(self) -> MusicInfo:
        return MusicInfo(
            name=self.name,
            singer=self.singer,
            source=self.source,
            songmid=str(self.songmid),
            album_name=self.album,
            album_id=str(self.album_id or ""),
            interval=int(self.interval or 0),
            img=self.cover or None,
            types=list(self.types or []),
            _types=dict(self.type_detail or {}),
            publish_time=int(self.publish_time or 0),
            is_original=bool(self.is_original),
            origin_song_id=str(self.origin_song_id or ""),
            origin_artists=list(self.origin_artists or []),
            fee=int(self.fee or 0),
            original_name=self.original_name or "",
            play_count=int(self.play_count or 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        """字典表示（含 QML 需要的派生字段）。

        QML 侧（播放队列等）会直接读取字典，因此这里把 ``uid`` / ``durationText``
        这类派生属性一并带上；:meth:`from_dict` 会忽略这些多余键。

        ``reason`` 是随用随弃的展示字段（漫游流的推荐理由），故意不带出去 ——
        它会被写进歌单 / 收藏 / 历史，下次打开就是一句过期的推荐理由。
        """
        d = asdict(self)
        d.pop("reason", None)
        d.update(
            {
                "uid": self.uid,
                "durationText": self.duration_text,
                "sourceText": self.source_text,
                "isLocal": self.is_local,
                "bestQuality": self.best_quality,
                "qualityShort": self.quality_short,
                "displayName": self.display_name,
            }
        )
        return d

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "Track":
        if not data:
            return cls()
        allowed = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in allowed})

    def same_song(self, other: "Track") -> bool:
        """同一音源下的同一首歌。"""
        return (
            self.source == other.source
            and str(self.songmid) == str(other.songmid)
            and bool(self.songmid)
        )

def role_names() -> List[bytes]:
    return [
        b"uid",
        b"source",
        b"songmid",
        b"name",
        b"singer",
        b"album",
        b"albumId",
        b"interval",
        b"durationText",
        b"cover",
        b"sourceText",
        b"isLocal",
        b"isOriginal",
        b"originalName",
        b"fee",
        b"qualities",
        b"bestQuality",
        b"path",
        b"playCount",
        b"displayName",
        b"reason",
        b"qualityShort",
    ]

_ROLE_KEYS = [r.decode() for r in role_names()]

class TrackListModel(QAbstractListModel):
    """供 QML ``ListView`` / ``Repeater`` 使用的曲目列表模型。"""

    countChanged = Signal()

    def __init__(self, tracks: Optional[Iterable[Track]] = None, parent=None):
        super().__init__(parent)
        self._tracks: List[Track] = list(tracks or [])

    # `count` 必须是 Qt 属性，QML 侧 `model.count` 才能拿到值并保持响应
    count = Property(int, lambda self: len(self._tracks), notify=countChanged)

    # ── Qt 接口 ─────────────────────────────────────────────

    def roleNames(self) -> Dict[int, QByteArray]:  # noqa: N802
        return {Qt.UserRole + i + 1: QByteArray(name) for i, name in enumerate(_ROLE_KEYS)}

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._tracks)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._tracks)):
            return None
        t = self._tracks[index.row()]
        field_index = role - (Qt.UserRole + 1)
        if 0 <= field_index < len(_ROLE_KEYS):
            key = _ROLE_KEYS[field_index]
            if key == "uid":
                return t.uid
            if key == "durationText":
                return t.duration_text
            if key == "sourceText":
                return t.source_text
            if key == "isLocal":
                return t.is_local
            if key == "isOriginal":
                return t.is_original
            if key == "originalName":
                return t.original_name
            if key == "albumId":
                return t.album_id
            if key == "qualities":
                return t.quality_list
            if key == "bestQuality":
                return t.best_quality
            if key == "displayName":
                return t.display_name
            if key == "qualityShort":
                return t.quality_short
            return getattr(t, key, None)
        if role == Qt.DisplayRole:
            return t.name
        return None

    # ── 变更通知 ────────────────────────────────────────────

    def _reset(self) -> None:
        self.beginResetModel()
        self.endResetModel()
        self.countChanged.emit()

    # ── Python API ──────────────────────────────────────────

    def tracks(self) -> List[Track]:
        return list(self._tracks)

    def track_at(self, row: int) -> Optional[Track]:
        if 0 <= row < len(self._tracks):
            return self._tracks[row]
        return None

    def length(self) -> int:
        """Python 侧的取长度接口（QML 侧请用 ``count`` 属性）。"""
        return len(self._tracks)

    def set_tracks(self, tracks: Iterable[Track]) -> None:
        self._reset_with(list(tracks))

    def _reset_with(self, tracks: List[Track]) -> None:
        self.beginResetModel()
        self._tracks = tracks
        self.endResetModel()
        self.countChanged.emit()

    def append(self, track: Track) -> int:
        row = len(self._tracks)
        self.beginInsertRows(QModelIndex(), row, row)
        self._tracks.append(track)
        self.endInsertRows()
        self.countChanged.emit()
        return row

    def extend(self, tracks: Iterable[Track]) -> None:
        items = list(tracks)
        if not items:
            return
        row = len(self._tracks)
        self.beginInsertRows(QModelIndex(), row, row + len(items) - 1)
        self._tracks.extend(items)
        self.endInsertRows()
        self.countChanged.emit()

    def insert(self, row: int, track: Track) -> None:
        row = max(0, min(row, len(self._tracks)))
        self.beginInsertRows(QModelIndex(), row, row)
        self._tracks.insert(row, track)
        self.endInsertRows()
        self.countChanged.emit()

    def remove_at(self, row: int) -> None:
        if not (0 <= row < len(self._tracks)):
            return
        self.beginRemoveRows(QModelIndex(), row, row)
        self._tracks.pop(row)
        self.endRemoveRows()
        self.countChanged.emit()

    def remove_uid(self, uid: str) -> bool:
        for i, t in enumerate(self._tracks):
            if t.uid == uid:
                self.remove_at(i)
                return True
        return False

    def move(self, src: int, dst: int) -> None:
        n = len(self._tracks)
        if not (0 <= src < n) or not (0 <= dst < n) or src == dst:
            return
        self.beginMoveRows(QModelIndex(), src, src, QModelIndex(), dst if dst < src else dst + 1)
        item = self._tracks.pop(src)
        self._tracks.insert(dst, item)
        self.endMoveRows()

    def clear(self) -> None:
        self._reset_with([])

    def index_of_uid(self, uid: str) -> int:
        for i, t in enumerate(self._tracks):
            if t.uid == uid:
                return i
        return -1

    def index_of_song(self, track: Track) -> int:
        for i, t in enumerate(self._tracks):
            if t.same_song(track):
                return i
        return -1

    def contains(self, track: Track) -> bool:
        return self.index_of_song(track) >= 0

    def to_dicts(self) -> List[Dict[str, Any]]:
        return [t.to_dict() for t in self._tracks]

    def load_dicts(self, data: Optional[Iterable[Dict[str, Any]]]) -> None:
        self._reset_with([Track.from_dict(d) for d in (data or [])])

    # ── QML 可调用 ──────────────────────────────────────────

    @Slot(int, result="QVariant")
    def get(self, row: int):
        t = self.track_at(row)
        return t.to_dict() if t else None

    @Slot(result="QVariantList")
    def allItems(self):  # noqa: N802
        """整个列表的字典数组（QML 侧用于「播放全部」等批量操作）。"""
        return [t.to_dict() for t in self._tracks]

    @Slot(result="QVariantList")
    def uids(self):  # noqa: N802
        """整个列表的 uid 数组（多选的「全选」与失效清理用，比 allItems 轻得多）。"""
        return [t.uid for t in self._tracks]

    @Slot(int, result="QVariantList")
    def itemsFrom(self, row: int):  # noqa: N802
        """从指定行开始的字典数组。"""
        row = max(0, min(int(row), len(self._tracks)))
        return [t.to_dict() for t in self._tracks[row:]]
