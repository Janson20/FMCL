"""本地音乐库持久化：歌单 / 我喜欢 / 播放历史 / 本地曲库。

全部落在程序目录的 ``data/`` 下，采用「版本号 + 原子写入 + 损坏备份」策略：
解析失败时把原文件改名为 ``*.corrupt`` 保留证据，而不是静默丢弃用户数据。
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from .. import jsonio, paths
from .models import Track, normalize_singers

def _migrate_singers(tracks: Iterable[Track]) -> List[Track]:
    """读盘时把歌手字段归一成「、」分隔。

    老曲库 / 老的收藏与历史里可能存着**未归一化**的本地标签（见
    :func:`app.core.models.normalize_singers`）：不修的话用户得为了这个重新扫一遍
    曲库，而且「我喜欢的音乐」「最近播放」里的本地歌同样会中招。
    归一化是幂等的（在线音源的字段本来就是「、」拼的），所以每次读都做一遍没关系。
    """
    out = []
    for track in tracks:
        fixed = normalize_singers(track.singer)
        if fixed != track.singer:
            track.singer = fixed
        out.append(track)
    return out

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

FAVORITES_ID = "__favorites__"
HISTORY_ID = "__history__"
MAX_HISTORY = 300

SORT_ADD_TIME_ASC = "add_time_asc"
SORT_ADD_TIME_DESC = "add_time_desc"
SORT_NAME_ASC = "name_asc"
SORT_NAME_DESC = "name_desc"
SORT_MODES = [SORT_ADD_TIME_ASC, SORT_ADD_TIME_DESC, SORT_NAME_ASC, SORT_NAME_DESC]

SORT_LABELS = {
    SORT_ADD_TIME_ASC: "添加时间 ↑",
    SORT_ADD_TIME_DESC: "添加时间 ↓",
    SORT_NAME_ASC: "名称 A→Z",
    SORT_NAME_DESC: "名称 Z→A",
}

try:  # 可选：中文按拼音排序
    from pypinyin import lazy_pinyin as _lazy_pinyin  # type: ignore
except Exception:  # pragma: no cover
    _lazy_pinyin = None

def name_sort_key(title: str) -> str:
    """名称排序键：有 pypinyin 时按拼音，否则退化为大小写不敏感的原串。"""
    t = (title or "").strip()
    if not t:
        return ""
    if _lazy_pinyin is not None:
        try:
            return "".join(_lazy_pinyin(t)).lower()
        except Exception:
            pass
    return t.lower()

# ──────────────────────────────────────────────────────────────
# 通用 JSON 读写
#
# 公开给同包的 session.py 复用（播放会话走的是同一套「原子写入 + 损坏备份」）。
# ──────────────────────────────────────────────────────────────

def atomic_write_json(path: Path, data: Any) -> None:
    """曲库 / 会话的写盘（缩进 1：人可读，又不至于把上千首歌撑成两倍体积）。

    实现在 :func:`app.jsonio.atomic_write_json`，这里只定死本模块的文件格式。
    """
    jsonio.atomic_write_json(path, data, indent=1)


def read_json(path: Path, default: Any) -> Any:
    """读 JSON；缺文件返回 ``default``，解析失败把原文件挪成 ``*.corrupt``。"""
    return jsonio.read_json(path, default)


# ──────────────────────────────────────────────────────────────
# 歌单
# ──────────────────────────────────────────────────────────────

@dataclass
class Playlist:
    id: str = ""
    name: str = ""
    songs: List[Track] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    sort_mode: str = SORT_ADD_TIME_DESC
    is_system: bool = False

    def __post_init__(self) -> None:
        if not self.id:
            self.id = "pl_" + uuid.uuid4().hex[:10]
        now = time.time()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    @property
    def count(self) -> int:
        return len(self.songs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "songs": [s.to_dict() for s in self.songs],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sort_mode": self.sort_mode,
            "is_system": self.is_system,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Playlist":
        return cls(
            id=str(data.get("id") or ""),
            name=str(data.get("name") or "未命名歌单"),
            songs=[Track.from_dict(d) for d in (data.get("songs") or [])],
            created_at=float(data.get("created_at") or 0),
            updated_at=float(data.get("updated_at") or 0),
            sort_mode=str(data.get("sort_mode") or SORT_ADD_TIME_DESC),
            is_system=bool(data.get("is_system")),
        )

class Library:
    """全部本地音乐数据的统一入口。"""

    def __init__(self, base_dir: Optional[Path] = None):
        root = Path(base_dir) if base_dir else paths.data_dir()
        self._root = root
        self._playlists_path = root / "playlists.json"
        self._history_path = root / "history.json"
        self._favorites_path = root / "favorites.json"
        self._local_path = root / "local.json"
        self._lock = threading.RLock()

        self._playlists: List[Playlist] = []
        self._history: List[Track] = []
        self._favorites: List[Track] = []
        self._local: List[Track] = []
        self._dirty: Dict[str, bool] = {}
        self._listeners: List[Callable[[str], None]] = []

    # ── 通知 ────────────────────────────────────────────────

    def add_listener(self, fn: Callable[[str], None]) -> None:
        self._listeners.append(fn)

    def _emit(self, what: str) -> None:
        for fn in list(self._listeners):
            try:
                fn(what)
            except Exception as e:
                logger.debug("歌单监听回调异常: %s", e)

    # ── 载入 / 保存 ─────────────────────────────────────────

    def load(self) -> None:
        with self._lock:
            raw = read_json(self._playlists_path, {"version": SCHEMA_VERSION, "playlists": []})
            self._playlists = [Playlist.from_dict(d) for d in (raw.get("playlists") or [])]
            # 自建歌单里也可能躺着未归一化的本地歌（迁移见 _migrate_singers）
            for playlist in self._playlists:
                playlist.songs = _migrate_singers(playlist.songs)
            if not any(p.id == FAVORITES_ID for p in self._playlists):
                self._playlists.insert(
                    0,
                    Playlist(
                        id=FAVORITES_ID,
                        name="我喜欢的音乐",
                        is_system=True,
                        sort_mode=SORT_ADD_TIME_DESC,
                    ),
                )

            self._history = _migrate_singers(
                Track.from_dict(d) for d in read_json(self._history_path, [])
            )
            self._favorites = _migrate_singers(
                Track.from_dict(d) for d in read_json(self._favorites_path, [])
            )
            self._local = _migrate_singers(
                Track.from_dict(d) for d in read_json(self._local_path, [])
            )

            # 「我喜欢」与独立文件保持一致（以文件为准）
            fav = self.favorites_playlist()
            if fav is not None:
                fav.songs = list(self._favorites)

            logger.info(
                "音乐库已载入: %d 个歌单 / %d 首收藏 / %d 条历史 / %d 首本地",
                len(self._playlists),
                len(self._favorites),
                len(self._history),
                len(self._local),
            )

    def save(self) -> None:
        with self._lock:
            atomic_write_json(
                self._playlists_path,
                {"version": SCHEMA_VERSION, "playlists": [p.to_dict() for p in self._playlists]},
            )
            atomic_write_json(self._history_path, [t.to_dict() for t in self._history])
            atomic_write_json(self._favorites_path, [t.to_dict() for t in self._favorites])
            atomic_write_json(self._local_path, [t.to_dict() for t in self._local])
            self._dirty.clear()

    def save_if_dirty(self) -> None:
        if self._dirty:
            self.save()

    def _touch(self, what: str = "") -> None:
        self._dirty[what or "all"] = True
        if what:
            self._emit(what)

    # ── 歌单 ────────────────────────────────────────────────

    def playlists(self) -> List[Playlist]:
        """用户歌单（不含系统歌单）。"""
        with self._lock:
            return [p for p in self._playlists if not p.is_system]

    def all_playlists(self) -> List[Playlist]:
        with self._lock:
            return list(self._playlists)

    def get_playlist(self, playlist_id: str) -> Optional[Playlist]:
        with self._lock:
            for p in self._playlists:
                if p.id == playlist_id:
                    return p
            return None

    def favorites_playlist(self) -> Optional[Playlist]:
        return self.get_playlist(FAVORITES_ID)

    def create_playlist(self, name: str) -> Playlist:
        with self._lock:
            pl = Playlist(name=(name or "新建歌单").strip() or "新建歌单")
            self._playlists.append(pl)
            self._touch("playlists")
            return pl

    def rename_playlist(self, playlist_id: str, new_name: str) -> bool:
        with self._lock:
            pl = self.get_playlist(playlist_id)
            if pl is None or pl.is_system:
                return False
            pl.name = (new_name or "").strip() or pl.name
            pl.updated_at = time.time()
            self._touch("playlists")
            return True

    def delete_playlist(self, playlist_id: str) -> bool:
        with self._lock:
            pl = self.get_playlist(playlist_id)
            if pl is None or pl.is_system:
                return False
            self._playlists.remove(pl)
            self._touch("playlists")
            return True

    def add_to_playlist(self, playlist_id: str, track: Track) -> bool:
        """加歌（自动去重），返回是否真的新增。"""
        with self._lock:
            pl = self.get_playlist(playlist_id)
            if pl is None:
                return False
            for existing in pl.songs:
                if existing.same_song(track):
                    return False
            track.added_at = int(time.time() * 1000)
            pl.songs.append(track)
            pl.updated_at = time.time()
            if playlist_id == FAVORITES_ID:
                self._favorites = list(pl.songs)
            self._sort_internal(pl)
            self._touch("playlists")
            return True

    def remove_from_playlist(self, playlist_id: str, uid: str) -> bool:
        with self._lock:
            pl = self.get_playlist(playlist_id)
            if pl is None:
                return False
            for i, s in enumerate(pl.songs):
                if s.uid == uid:
                    pl.songs.pop(i)
                    pl.updated_at = time.time()
                    if playlist_id == FAVORITES_ID:
                        self._favorites = list(pl.songs)
                    self._touch("playlists")
                    return True
            return False

    def move_in_playlist(self, playlist_id: str, src: int, dst: int) -> bool:
        with self._lock:
            pl = self.get_playlist(playlist_id)
            if pl is None:
                return False
            n = len(pl.songs)
            if not (0 <= src < n) or not (0 <= dst < n) or src == dst:
                return False
            pl.sort_mode = SORT_ADD_TIME_ASC  # 手动排序后切换为自定义顺序
            item = pl.songs.pop(src)
            pl.songs.insert(dst, item)
            pl.updated_at = time.time()
            self._touch("playlists")
            return True

    def clear_playlist(self, playlist_id: str) -> bool:
        with self._lock:
            pl = self.get_playlist(playlist_id)
            if pl is None or pl.is_system:
                return False
            pl.songs.clear()
            pl.updated_at = time.time()
            self._touch("playlists")
            return True

    def set_sort_mode(self, playlist_id: str, mode: str) -> bool:
        if mode not in SORT_MODES:
            return False
        with self._lock:
            pl = self.get_playlist(playlist_id)
            if pl is None:
                return False
            pl.sort_mode = mode
            self._sort_internal(pl)
            self._touch("playlists")
            return True

    def _sort_internal(self, pl: Playlist) -> None:
        if pl.sort_mode == SORT_ADD_TIME_ASC:
            pl.songs.sort(key=lambda s: s.added_at)
        elif pl.sort_mode == SORT_ADD_TIME_DESC:
            pl.songs.sort(key=lambda s: s.added_at, reverse=True)
        elif pl.sort_mode == SORT_NAME_ASC:
            pl.songs.sort(key=lambda s: name_sort_key(s.name))
        elif pl.sort_mode == SORT_NAME_DESC:
            pl.songs.sort(key=lambda s: name_sort_key(s.name), reverse=True)

    def playlists_containing(self, track: Track) -> List[str]:
        """返回包含该曲目的用户歌单 id 列表（跳过系统歌单）。"""
        with self._lock:
            out = []
            for pl in self._playlists:
                if pl.is_system:
                    continue
                if any(s.same_song(track) for s in pl.songs):
                    out.append(pl.id)
            return out

    # ── 我喜欢 ──────────────────────────────────────────────

    def is_favorite(self, track: Track) -> bool:
        with self._lock:
            return any(s.same_song(track) for s in self._favorites)

    def toggle_favorite(self, track: Track) -> bool:
        """切换收藏状态，返回切换后是否已收藏。"""
        with self._lock:
            for i, s in enumerate(self._favorites):
                if s.same_song(track):
                    self._favorites.pop(i)
                    pl = self.favorites_playlist()
                    if pl is not None:
                        pl.songs = list(self._favorites)
                    self._touch("favorites")
                    return False
            track.added_at = int(time.time() * 1000)
            self._favorites.insert(0, track)
            pl = self.favorites_playlist()
            if pl is not None:
                pl.songs = list(self._favorites)
            self._touch("favorites")
            return True

    def favorites(self) -> List[Track]:
        with self._lock:
            return list(self._favorites)

    # ── 播放历史 ────────────────────────────────────────────

    def history(self) -> List[Track]:
        with self._lock:
            return list(self._history)

    def record_play(self, track: Track) -> None:
        with self._lock:
            for i, s in enumerate(self._history):
                if s.same_song(track):
                    self._history.pop(i)
                    break
            track.added_at = int(time.time() * 1000)
            self._history.insert(0, track)
            del self._history[MAX_HISTORY:]
            self._touch("history")

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()
            self._touch("history")

    # ── 本地曲库 ────────────────────────────────────────────

    def local_tracks(self) -> List[Track]:
        with self._lock:
            return list(self._local)

    def set_local_tracks(self, tracks: Iterable[Track]) -> None:
        with self._lock:
            self._local = list(tracks)
            self._local.sort(key=lambda t: name_sort_key(t.name))
            self._touch("local")

    def local_folders(self) -> List[str]:
        return sorted({str(Path(t.path).parent) for t in self._local if t.path})

    # ── 统计 ────────────────────────────────────────────────

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "playlists": len([p for p in self._playlists if not p.is_system]),
                "favorites": len(self._favorites),
                "history": len(self._history),
                "local": len(self._local),
            }
