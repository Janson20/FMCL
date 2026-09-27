"""歌单管理模块 - PlaylistSong, Playlist, PlaylistManager

提供歌单的创建、管理、持久化和排序功能。
本地歌曲和在线歌曲均可收藏到歌单中。
"""

import json
import logging
import os
import platform
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# 高性能 JSON 序列化（与 config.py 保持一致）
try:
    import orjson as _json_mod

    def _json_dumps(obj, indent: int = 2) -> str:
        opts = _json_mod.OPT_INDENT_2 if indent == 2 else 0
        return _json_mod.dumps(obj, option=opts).decode("utf-8")

    def _json_loads(data: bytes | str) -> Any:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return _json_mod.loads(data)

except ImportError:
    import json as _json_mod  # type: ignore[no-redef]

    def _json_dumps(obj, indent: int = 2) -> str:  # type: ignore[misc]
        return _json_mod.dumps(obj, indent=indent, ensure_ascii=False)

    def _json_loads(data: bytes | str) -> Any:  # type: ignore[misc]
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        return _json_mod.loads(data)


# 本模块的日志器：services 层不得依赖界面，日志一律走标准库 logging
_logger = logging.getLogger(__name__)


# ─── 常量 ──────────────────────────────────────────────────

HISTORY_PLAYLIST_ID = "__history__"
"""系统播放历史歌单的固定 ID"""

MAX_HISTORY = 200
"""播放历史最大条目数"""


# ─── 平台相关路径 ─────────────────────────────────────────


def _writable_data_dir(primary: Path) -> Path:
    """返回一个**可写**的歌单数据目录：primary 不可写时回退到用户数据目录（D-20）

    与 config.py 的策略同构（判可写性 → 不可写就换 `_get_user_data_dir()`）：原来这里的 `mkdir`
    是裸调，"装到 Program Files / 只读盘 / 路径被同名文件占住"都会直接抛，而两个可达调用方
    （`load` / `save`）都把异常吞掉 —— 表现为"歌单改了、重启就没了"，而 `config.json` 照常落盘。
    回退与彻底失败都必须留下日志，否则用户看不到任何迹象。

    复用 config.py 的辅助而不是再抄一份判据（`services/backup_manager.py` 对 `_json_*` 已有同样
    先例）；延迟导入是为了不在音乐模块的导入期把整个 launcher 配置栈拉起来。
    """
    from config import _get_user_data_dir, _is_writable_dir

    if _is_writable_dir(primary):
        return primary
    fallback = _get_user_data_dir() / "data"
    if _is_writable_dir(fallback):
        _logger.warning("歌单数据目录不可写（%s），已回退到 %s", primary, fallback)
        return fallback
    _logger.error("歌单数据目录与回退目录都不可写，歌单本次不会落盘：%s", primary)
    return primary


def get_music_data_dir() -> Path:
    """获取音乐数据目录，遵循 XDG Base Directory 规范

    - Linux: ~/.local/share/fmcl/data/
    - Windows/macOS: ./data/

    返回的目录一定**试过**创建；primary 建不出来或不可写时按 config.py 同构的回退策略
    换到用户数据目录（见 `_writable_data_dir`），成功回退会打 warning。
    """
    if platform.system().lower() == "linux":
        home = Path.home()
        xdg_data_home = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
        data_dir = xdg_data_home / "fmcl" / "data"
    else:
        data_dir = Path.cwd() / "data"
    return _writable_data_dir(data_dir)


def get_music_data_path() -> Path:
    """获取 music.json 的完整路径"""
    return get_music_data_dir() / "music.json"


# ─── 数据模型 ──────────────────────────────────────────────


# 排序模式常量
SORT_ADD_TIME_ASC = "add_time_asc"
SORT_ADD_TIME_DESC = "add_time_desc"
SORT_NAME_ASC = "name_asc"
SORT_NAME_DESC = "name_desc"

SORT_MODES = [SORT_ADD_TIME_ASC, SORT_ADD_TIME_DESC, SORT_NAME_ASC, SORT_NAME_DESC]


@dataclass
class PlaylistSong:
    """歌单中的单曲条目，支持本地文件和在线歌曲"""

    # 来源类型
    source_type: str = "local"  # "local" | "online"

    # 本地歌曲路径（source_type="local" 时有效）
    file_path: str = ""

    # 在线歌曲信息（source_type="online" 时有效）
    online_source: str = ""  # "kw"/"kg"/"mg"/"tx"/"wy"
    online_songmid: str = ""  # 歌曲ID
    online_name: str = ""  # 歌曲名
    online_singer: str = ""  # 歌手名
    online_album: str = ""  # 专辑名
    online_interval: int = 0  # 时长(秒)
    online_img: str = ""  # 封面图URL
    # 可用音质列表与详情（来自搜索结果的 types/_types，用于恢复播放时
    # 的自动音质解析与 URL 获取；缺失时播放端会重新搜索补齐）
    online_types: List[Dict] = field(default_factory=list)  # [{"type": "flac", ...}, ...]
    online_type_detail: Dict = field(default_factory=dict)  # {"flac": {...hash/media_mid...}, ...}

    # 显示字段
    display_title: str = ""
    display_artist: str = ""

    # 排序字段
    added_at: float = 0.0  # 加入时间戳

    # 唯一标识（用于在歌单中精确匹配）
    #
    # 注意：`_id` 是**磁盘格式的一部分** —— `to_dict()` 遍历 `fields(self)`，所以它会写进
    # music.json（复核当日实测 124/124 条记录都带 `_id`），`from_dict()` 也按字段名读回，
    # 而 tests/test_music_playlist.py 的往返用例**钉住了这个现状**。阶段 3.16 换歌单格式时
    # 才动它：届时要把 `_id` 正式化成公开字段，并**带迁移方案**（旧文件里的 `_id` 必须能读回来），
    # 不能只是把这里删掉。
    _id: str = ""

    def __post_init__(self):
        if not self._id:
            self._id = str(uuid.uuid4())[:8]
        if not self.added_at:
            self.added_at = time.time()
        if not self.display_title:
            self.display_title = (
                self.online_name or os.path.splitext(os.path.basename(self.file_path))[0] if self.file_path else ""
            )

    def to_dict(self) -> dict:
        """序列化为字典

        连私有字段 `_id` 一起写出去（它是磁盘格式的一部分，测试钉住了现状；
        阶段 3.16 换格式时要带迁移方案，见 `PlaylistSong._id` 处的说明）。
        """
        result = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if value is not None:
                result[f.name] = value
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "PlaylistSong":
        """从字典反序列化"""
        valid_keys = {f.name for f in fields(cls)}
        clean = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**clean)

    @classmethod
    def from_local_file(cls, filepath: str, metadata: dict = None) -> "PlaylistSong":
        """从本地文件路径创建歌单项"""
        meta = metadata or {}
        title = meta.get("title", os.path.splitext(os.path.basename(filepath))[0])
        artist = meta.get("artist", "")
        return cls(
            source_type="local", file_path=filepath, display_title=title, display_artist=artist, added_at=time.time()
        )

    @classmethod
    def from_online_info(cls, info) -> "PlaylistSong":
        """从在线搜索的 MusicInfo 创建歌单项"""
        from services.music_source.base import MusicInfo  # defer import to avoid cycle

        if not isinstance(info, MusicInfo):
            raise TypeError("expected MusicInfo instance")

        return cls(
            source_type="online",
            online_source=info.source,
            online_songmid=info.songmid,
            online_name=info.name,
            online_singer=info.singer,
            online_album=info.album_name,
            online_interval=info.interval,
            online_img=info.img,
            online_types=[dict(t) for t in (info.types or [])],
            online_type_detail=dict(info._types or {}),
            display_title=info.name,
            display_artist=info.singer,
            added_at=time.time(),
        )

    def get_display_text(self, max_title: int = 50) -> str:
        """获取显示文本（歌名 - 歌手）"""
        parts = [self.display_title]
        if self.display_artist:
            parts.append(self.display_artist)
        text = " - ".join(parts)
        if len(text) > max_title:
            text = text[: max_title - 3] + "..."
        return text

    def get_sort_key_name(self) -> str:
        """获取用于歌名排序的键值"""
        return (self.display_title or "").lower()

    def matches(self, other: "PlaylistSong") -> bool:
        """判断两首歌是否相同（用于去重）"""
        key = _song_key(self)
        return key is not None and key == _song_key(other)


@dataclass
class Playlist:
    """歌单"""

    id: str = ""  # 唯一标识
    name: str = ""  # 歌单名
    songs: List[PlaylistSong] = field(default_factory=list)  # 歌曲列表
    created_at: float = 0.0  # 创建时间戳
    updated_at: float = 0.0  # 最后修改时间戳
    sort_mode: str = SORT_ADD_TIME_DESC  # 当前排序模式
    is_system: bool = False  # 系统内置歌单（不可删除/重命名）

    def __post_init__(self):
        if not self.id:
            self.id = "pl_" + str(uuid.uuid4())[:8]
        now = time.time()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict:
        """序列化为字典"""
        result = {
            "id": self.id,
            "name": self.name,
            "songs": [s.to_dict() for s in self.songs],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "sort_mode": self.sort_mode,
        }
        if self.is_system:
            result["is_system"] = True
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "Playlist":
        """从字典反序列化"""
        songs = [PlaylistSong.from_dict(s) for s in data.get("songs", [])]
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            songs=songs,
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            sort_mode=data.get("sort_mode", SORT_ADD_TIME_DESC),
            is_system=data.get("is_system", False),
        )

    @property
    def song_count(self) -> int:
        """歌单歌曲数量"""
        return len(self.songs)


# ─── 去重比对键 ────────────────────────────────────────────


def _song_key(song: "PlaylistSong"):
    """生成"是否同一首歌"的比对键：本地歌用规范化路径，在线歌用音源 + 歌曲 ID

    D-22：原来四处去重判定各自在内层循环里重算 `os.path.normpath`。现在把候选歌的键**算一次**、
    循环里只比元组，判定语义逐字不变 —— 本地对本地比路径、在线对在线比音源+ID，混杂类型与
    未知类型都不算同一首（未知类型返回 `None`，调用方据此跳过）。
    """
    if song.source_type == "local":
        return ("local", os.path.normpath(song.file_path))
    if song.source_type == "online":
        return ("online", song.online_source, song.online_songmid)
    return None


def _query_keys(file_path: str, online_source: str, online_songmid: str) -> set:
    """生成"查这首歌"的比对键集合（空参数不参与判定），键的形状与 `_song_key` 一致

    D-22：`os.path.normpath(file_path)` 只算一次，而不是在歌单里逐首重算。
    """
    local_key = ("local", os.path.normpath(file_path)) if file_path else None
    online_key = ("online", online_source, online_songmid) if online_source else None
    return {k for k in (local_key, online_key) if k is not None}


# ─── 歌单管理器 ────────────────────────────────────────────


class PlaylistManager:
    """歌单管理器 - CRUD + 排序 + 持久化"""

    def __init__(self):
        self._playlists: List[Playlist] = []
        self._current_playlist_id: Optional[str] = None
        self._dirty: bool = False

    # ── 属性 ──

    @property
    def playlists(self) -> List[Playlist]:
        return list(self._playlists)

    @property
    def user_playlists(self) -> List[Playlist]:
        """返回用户自建歌单（排除系统歌单）"""
        return [pl for pl in self._playlists if not pl.is_system]

    @property
    def current_playlist_id(self) -> Optional[str]:
        return self._current_playlist_id

    def get_current_playlist(self) -> Optional[Playlist]:
        """获取当前选中的歌单"""
        if not self._current_playlist_id:
            return None
        return self.get_playlist(self._current_playlist_id)

    def is_system_playlist(self, playlist_id: str) -> bool:
        """判断是否为系统内置歌单"""
        pl = self.get_playlist(playlist_id)
        return pl is not None and pl.is_system

    # ── CRUD ──

    def create_playlist(self, name: str) -> Playlist:
        """创建新歌单"""
        pl = Playlist(name=name)
        self._playlists.append(pl)
        self.mark_dirty()
        return pl

    def delete_playlist(self, playlist_id: str) -> bool:
        """删除歌单（系统歌单不可删除）"""
        pl = self.get_playlist(playlist_id)
        if pl is None or pl.is_system:
            return False
        for i, p in enumerate(self._playlists):
            if p.id == playlist_id:
                self._playlists.pop(i)
                if self._current_playlist_id == playlist_id:
                    self._current_playlist_id = None
                self.mark_dirty()
                return True
        return False

    def rename_playlist(self, playlist_id: str, new_name: str) -> bool:
        """重命名歌单（系统歌单不可重命名）"""
        pl = self.get_playlist(playlist_id)
        if pl is None or pl.is_system:
            return False
        pl.name = new_name
        pl.updated_at = time.time()
        self.mark_dirty()
        return True

    def get_playlist(self, playlist_id: str) -> Optional[Playlist]:
        """获取指定歌单"""
        for pl in self._playlists:
            if pl.id == playlist_id:
                return pl
        return None

    def set_current_playlist(self, playlist_id: str):
        """切换当前选中歌单"""
        self._current_playlist_id = playlist_id
        self.mark_dirty()

    # ── 播放历史 ──

    def get_or_create_history_playlist(self) -> Playlist:
        """获取或创建系统播放历史歌单"""
        pl = self.get_playlist(HISTORY_PLAYLIST_ID)
        if pl is None:
            pl = Playlist(id=HISTORY_PLAYLIST_ID, name="播放历史", is_system=True, sort_mode=SORT_ADD_TIME_DESC)
            self._playlists.insert(0, pl)  # 放在最前
        return pl

    def record_to_history(self, song: PlaylistSong) -> bool:
        """记录一首歌到播放历史（去重 + 上限裁剪）"""
        pl = self.get_or_create_history_playlist()
        # 去重：如果已存在，移除旧的（比对键只算一次，见 _song_key）
        key = _song_key(song)
        for i, existing in enumerate(pl.songs):
            if key is not None and _song_key(existing) == key:
                pl.songs.pop(i)
                break
        # 添加在最前面
        song.added_at = time.time()
        pl.songs.insert(0, song)
        pl.updated_at = time.time()
        # 裁剪上限
        if len(pl.songs) > MAX_HISTORY:
            pl.songs = pl.songs[:MAX_HISTORY]
        self.mark_dirty()
        return True

    # ── 歌曲操作 ──

    def add_song(self, playlist_id: str, song: PlaylistSong) -> bool:
        """添加歌曲到歌单，返回是否成功"""
        pl = self.get_playlist(playlist_id)
        if pl is None:
            return False
        # 去重：同一首本地文件或在线歌曲不重复添加（候选歌的比对键只算一次，见 _song_key）
        key = _song_key(song)
        if key is not None and any(_song_key(existing) == key for existing in pl.songs):
            return False
        pl.songs.append(song)
        pl.updated_at = time.time()
        self._sort_playlist_internal(pl)
        self.mark_dirty()
        return True

    def remove_song(self, playlist_id: str, song_index: int) -> bool:
        """从歌单移除指定索引的歌曲（系统歌单不可编辑）"""
        pl = self.get_playlist(playlist_id)
        if pl is None or pl.is_system or song_index < 0 or song_index >= len(pl.songs):
            return False
        pl.songs.pop(song_index)
        pl.updated_at = time.time()
        self.mark_dirty()
        return True

    def remove_song_by_id(self, playlist_id: str, song_id: str) -> bool:
        """通过歌曲 ID 从歌单移除"""
        pl = self.get_playlist(playlist_id)
        if pl is None:
            return False
        for i, s in enumerate(pl.songs):
            if s._id == song_id:
                pl.songs.pop(i)
                pl.updated_at = time.time()
                self.mark_dirty()
                return True
        return False

    def clear_playlist(self, playlist_id: str) -> bool:
        """清空歌单（系统歌单不可清空）"""
        pl = self.get_playlist(playlist_id)
        if pl is None or pl.is_system:
            return False
        pl.songs.clear()
        pl.updated_at = time.time()
        self.mark_dirty()
        return True

    def is_song_in_playlist(self, playlist_id: str, song: "PlaylistSong") -> bool:
        """判断歌曲是否已在指定歌单中（与 add_song 去重逻辑一致）"""
        pl = self.get_playlist(playlist_id)
        if pl is None:
            return False
        # 与 add_song 用同一套比对键（D-22：候选歌的 normpath 不在循环里重算）
        key = _song_key(song)
        return key is not None and any(_song_key(existing) == key for existing in pl.songs)

    def is_song_in_any_playlist(self, file_path: str = "", online_source: str = "", online_songmid: str = "") -> bool:
        """检查歌曲是否已在某个歌单中（用于 UI 显示"已收藏"状态）"""
        keys = _query_keys(file_path, online_source, online_songmid)
        for pl in self._playlists:
            if pl.is_system:
                continue  # 系统歌单不参与"已收藏"判断
            if any(_song_key(s) in keys for s in pl.songs):
                return True
        return False

    def get_playlist_names_for_song(
        self, file_path: str = "", online_source: str = "", online_songmid: str = ""
    ) -> List[str]:
        """返回包含该歌曲的所有歌单名称列表（用于 UI 显示）"""
        names = []
        keys = _query_keys(file_path, online_source, online_songmid)
        for pl in self._playlists:
            if pl.is_system:
                continue
            if any(_song_key(s) in keys for s in pl.songs):
                names.append(pl.name)
        return names

    # ── 排序 ──

    def sort(self, playlist_id: str, mode: str) -> bool:
        """对歌单排序，mode: add_time_asc/add_time_desc/name_asc/name_desc"""
        pl = self.get_playlist(playlist_id)
        if pl is None:
            return False
        if mode not in SORT_MODES:
            return False
        pl.sort_mode = mode
        self._sort_playlist_internal(pl)
        self.mark_dirty()
        return True

    @staticmethod
    def _sort_playlist_internal(pl: Playlist):
        """内部排序方法"""
        mode = pl.sort_mode
        if mode == SORT_ADD_TIME_ASC:
            pl.songs.sort(key=lambda s: s.added_at)
        elif mode == SORT_ADD_TIME_DESC:
            pl.songs.sort(key=lambda s: s.added_at, reverse=True)
        elif mode == SORT_NAME_ASC:
            pl.songs.sort(key=lambda s: _name_sort_key(s.display_title))
        elif mode == SORT_NAME_DESC:
            pl.songs.sort(key=lambda s: _name_sort_key(s.display_title), reverse=True)

    def get_playable_songs(self, playlist_id: str) -> List[PlaylistSong]:
        """获取歌单中的可播放歌曲（过滤掉不存在的本地文件）"""
        pl = self.get_playlist(playlist_id)
        if pl is None:
            return []
        results = []
        for s in pl.songs:
            if s.source_type == "local" and not os.path.exists(s.file_path):
                continue
            results.append(s)
        return results

    # ── 脏标记 ──

    def mark_dirty(self):
        """标记歌单数据已变更，等待定时落盘"""
        self._dirty = True

    def save_if_dirty(self, path: Optional[Path] = None):
        """仅在数据有变更时执行落盘（由定时器调用）"""
        if self._dirty:
            self.save(path)

    # ── 持久化 ──

    def load(self, path: Optional[Path] = None):
        """从文件加载歌单数据

        路径解析也在 try 里（D-20）：解析失败（cwd 被删、目录异常）不该把异常抛给
        Tk 回调 —— 调用方会把它吞掉，表现为"歌单凭空空了"。
        """
        if path is None:
            try:
                path = get_music_data_path()
            except Exception as e:
                _logger.error("歌单数据路径解析失败，本次不加载：%s: %s", type(e).__name__, e)
                path = None

        if path is None or not path.exists():
            self._playlists.clear()
            self._current_playlist_id = None
            # 确保历史歌单始终存在
            self.get_or_create_history_playlist()
            return

        try:
            data = path.read_bytes()
            parsed = _json_loads(data)
            self._playlists.clear()
            for pl_data in parsed.get("playlists", []):
                pl = Playlist.from_dict(pl_data)
                self._playlists.append(pl)
            self._current_playlist_id = parsed.get("current_playlist_id", None)
            # 确保历史歌单始终存在
            self.get_or_create_history_playlist()
        except Exception:
            try:
                corrupt_path = path.with_suffix(".json.corrupt")
                path.rename(corrupt_path)
            except Exception:
                pass
            self._playlists.clear()
            self._current_playlist_id = None
            self.get_or_create_history_playlist()

    def save(self, path: Optional[Path] = None):
        """保存歌单数据到文件（原子写入）

        任何失败都只**记日志**、不抛给调用方，并保留脏标记等下次重试（D-20）：两个可达
        调用点（周期落盘、退出前清理）都在 Tk 回调的 try 里，抛出去等于"歌单静默不落盘"。
        """
        if path is None:
            try:
                path = get_music_data_path()
            except Exception as e:
                _logger.error("歌单数据路径解析失败，本次未落盘：%s: %s", type(e).__name__, e)
                return

        data = {
            "version": 1,
            "playlists": [pl.to_dict() for pl in self._playlists],
            "current_playlist_id": self._current_playlist_id,
        }

        content = _json_dumps(data)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(".tmp")
            tmp_path.write_text(content, encoding="utf-8")
            tmp_path.rename(path)
        except Exception:
            # 原子写失败（临时文件占了名字 / 跨设备改名等）才退化成直接写
            try:
                path.write_text(content, encoding="utf-8")
            except Exception as e:
                _logger.error("歌单落盘失败，改动留在内存里等下次重试：%s: %s", type(e).__name__, e)
                return
        self._dirty = False


# ─── 排序辅助函数 ─────────────────────────────────────────


def _name_sort_key(title: str) -> str:
    """生成歌名排序键

    优先使用 pypinyin 做中文拼音首字母排序，
    回退到 locale.strxfrm，最后回退到 str.lower。
    """
    if not title:
        return ""

    try:
        import pypinyin

        if any("\u4e00" <= c <= "\u9fff" for c in title):
            result = []
            for char in title:
                if "\u4e00" <= char <= "\u9fff":
                    py = pypinyin.pinyin(char, style=pypinyin.Style.FIRST_LETTER)
                    if py and py[0]:
                        result.append(py[0][0].lower())
                    else:
                        result.append(char.lower())
                else:
                    result.append(char.lower())
            return "".join(result)
    except ImportError:
        pass

    try:
        import locale

        return locale.strxfrm(title.lower())
    except Exception:
        pass

    return title.lower()
