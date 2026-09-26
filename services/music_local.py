"""本地侧音乐逻辑（阶段 1 任务 1.4-C，形态 2：逻辑与界面切分）。

宿主是 `ui/app_music.py` 的 `MusicPlayerMixin` 里**本地侧**那批方法：本地歌单
CRUD / 排序 / 播放历史 / 侧边栏与歌曲行清单 / 播放全部 / 底栏取值。

## 切缝判据（与 1.4-A / 1.4-B 同一条）

> **这段代码是否 import GUI（customtkinter / tkinter / CTk*）？是否直接创建/销毁/配置
> 控件？是否 `self.after(...)` 排期、是否读 `winfo_exists()`？**

* 只做**计算、判定、状态机、持久化、字符串拼装、清单生成**的 → 搬到这里；
* 碰控件 / 排期 / 主线程回调的 → 留在 `ui/app_music.py`，那边只留
  "取值 → 调服务 → 把结果画出来"。

因此本模块零 UI：不 import tkinter/customtkinter/ui，不调 `after`，不建控件，
**不调 i18n**（所有显示文案由界面侧用 `_()` 取好后当参数传进来）。

## 与 `services/music_playlist.py` 的分工

歌单的**数据模型与持久化**（`PlaylistSong` / `Playlist` / `PlaylistManager`、去重规则、
排序实现、`music.json` 原子写盘）早在 1.4-A 就住在 `services/music_playlist.py`。
本模块只装**界面侧那层编排与文案拼装**：把"哪一步该判定、拼出来长什么样"从 Mixin 里
搬出来，供 Tk 与 QML 两条界面共用。**本模块不新增任何持久化**。

## 记录现状、疑为缺陷（**未改**，只搬）

1. `_music_show_playlist_context_menu` / `_music_add_to_playlist_menu` 的"没有歌单先
   创建"分支里，`msg = _("music_new_playlist")` 是一个**赋值后未被使用**的局部变量
   （原文如此）。它在界面侧，本轮逐字保留。
2. `_music_rename_playlist_dialog` 对 `PlaylistManager.rename_playlist` 的返回值
   **不作判断** —— 系统歌单改名失败也照常重建侧边栏并落盘。`apply_rename` 的返回值
   只表示"名字非空、确实发起了重命名"，不表示重命名成功（见该函数 docstring）。
3. `footer_plan` 的可见性条件是
   `(path or is_online_playing) and (is_playing or is_paused)`；在
   "在线播放中但 `_music_current_online_info` 为 None"时原文会拿 `None` 去查元数据
   （`os.path.basename(None)` 抛 TypeError）。本轮逐字保留这条路径。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from services.music_audio import format_time
from services.music_playlist import (
    HISTORY_PLAYLIST_ID,
    SORT_ADD_TIME_ASC,
    SORT_ADD_TIME_DESC,
    SORT_NAME_ASC,
    SORT_NAME_DESC,
    Playlist,
    PlaylistManager,
    PlaylistSong,
)

#: 系统歌单（播放历史）在侧边栏里的图标。**逐字保留原文的字符**（原文写死 `"🕐"`），
#: 本轮是等价重构，不在这里换图标 —— 换掉它属于行为变更。
SYSTEM_PLAYLIST_ICON = "🕐"

#: 底栏曲目文案的最大长度与截断后保留长度（原文 `if len(text) > 40: text = text[:38] + "..."`）
FOOTER_TEXT_MAX = 40
FOOTER_TEXT_KEEP = 38

#: 排序模式"同模式再点一下"的翻转表（原文 4 个 `if/elif` 链）
SORT_FLIP: Dict[str, str] = {
    SORT_ADD_TIME_DESC: SORT_ADD_TIME_ASC,
    SORT_ADD_TIME_ASC: SORT_ADD_TIME_DESC,
    SORT_NAME_ASC: SORT_NAME_DESC,
    SORT_NAME_DESC: SORT_NAME_ASC,
}

#: `_music_add_to_playlist_menu` 里"已在歌单中"的后缀（原文 `f"{pl.name} ✓"`）
ALREADY_ADDED_SUFFIX = " ✓"


# ══════════════════════════════════════════════════════════════════════
# 数据类：界面照着画，服务只算
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SidebarItem:
    """侧边栏一个条目的绘制参数。

    ``playlist_id`` 为 ``None`` 表示"全部歌曲"条目；以 ``wy:`` 开头的表示网易云
    远程歌单条目（前缀约定的唯一真相在 `services.music_wy_remote`）。
    """

    playlist_id: Optional[str]
    text: str
    is_active: bool


@dataclass(frozen=True)
class SidebarPlan:
    """本地侧边栏的重绘计划（"全部歌曲" + 用户歌单，按 `PlaylistManager` 顺序）。"""

    all_songs: SidebarItem
    playlists: List[SidebarItem] = field(default_factory=list)


@dataclass(frozen=True)
class SortPlan:
    """本地歌单排序结果：``mode`` 是实际生效的模式，``playlist`` 是需要重绘的歌单。"""

    mode: str
    playlist: Playlist


@dataclass(frozen=True)
class PlaylistRowPlan:
    """`_add_playlist_row`（"全部歌曲"列表行）的显示取值。"""

    name_text: str
    dur_text: str


@dataclass(frozen=True)
class SongRowPlan:
    """`_add_playlist_song_row`（歌单歌曲行）的显示取值。"""

    display_text: str
    dur_text: str
    source_tag: str
    show_remove: bool


@dataclass(frozen=True)
class AddSongOutcome:
    """添加歌曲到歌单的结果。

    ``playlist`` 非空表示"当前正在查看的就是被添加的那个歌单"，界面据此重绘歌曲列表。
    """

    added: bool
    playlist: Optional[Playlist] = None


@dataclass(frozen=True)
class PlaylistMenuItem:
    """"添加到歌单"菜单里的一行。``exists`` 为真时原文把按钮置灰。"""

    playlist_id: str
    text: str
    exists: bool


@dataclass(frozen=True)
class AddToPlaylistMenuPlan:
    """"添加到歌单"菜单的数据准备结果：构造好的歌单项 + 逐歌单的条目。

    **不含**"一个歌单都没有"的分支 —— 那一段是 `messagebox` + i18n 文案 +
    调 `_music_create_playlist_dialog`，全在界面侧；而且原文的判定在
    **创建菜单窗口之前**、读元数据在**之后**，把两者并进一次服务调用会改掉
    这个顺序（窗口弹出来的时机就变了）。
    """

    song: Optional[PlaylistSong] = None
    items: List[PlaylistMenuItem] = field(default_factory=list)


@dataclass(frozen=True)
class PlayAllTarget:
    """"播放全部"的目标：``context_songs`` 是"上下曲"要用的歌单上下文副本，
    ``index`` 是要播的索引，``-1`` 表示整张歌单没有可播歌曲。"""

    context_songs: List[PlaylistSong]
    index: int


@dataclass(frozen=True)
class FooterPlan:
    """底部迷你播放条（全局底栏）的绘制计划。``visible`` 为假时界面把整条隐藏。"""

    visible: bool
    text: str
    play_text: str


# ══════════════════════════════════════════════════════════════════════
# 歌单名与侧边栏文案
# ══════════════════════════════════════════════════════════════════════


def playlist_label(playlist: Playlist) -> str:
    """侧边栏歌单条目文案（原文 `_rebuild_playlist_sidebar` 里那两行）。

    系统歌单（播放历史）带图标前缀，普通歌单不带；两者都附歌曲数。
    """
    if playlist.is_system:
        return f"{SYSTEM_PLAYLIST_ICON} {playlist.name} ({playlist.song_count})"
    return f"{playlist.name} ({playlist.song_count})"


def sidebar_plan(manager: PlaylistManager, all_songs_text: str) -> SidebarPlan:
    """本地侧边栏的重绘计划。

    "全部歌曲"条目恒不选中（原文传 `is_active` 的默认值 `False`）；用户歌单条目按
    `manager.playlists` 顺序，命中 `current_playlist_id` 的选中。
    """
    current_id = manager.current_playlist_id
    items = [
        SidebarItem(
            playlist_id=pl.id,
            text=playlist_label(pl),
            is_active=(pl.id == current_id),
        )
        for pl in manager.playlists
    ]
    return SidebarPlan(
        all_songs=SidebarItem(playlist_id=None, text=all_songs_text, is_active=False),
        playlists=items,
    )


def normalize_name(raw_name: Any) -> Optional[str]:
    """歌单名归一化：去首尾空白。空白/``None`` → ``None``。

    原文两处都是 ``if not name or not name.strip(): return`` 再 ``name = name.strip()``；
    这里把"空名不生效"这条判定收进一个函数，新建与重命名共用。
    """
    name = (raw_name or "").strip()
    return name or None


def create_named_playlist(manager: PlaylistManager, raw_name: Any) -> Optional[Playlist]:
    """新建歌单并选为当前歌单；名字无效时返回 ``None``（界面直接返回、不弹任何东西）。"""
    name = normalize_name(raw_name)
    if name is None:
        return None
    pl = manager.create_playlist(name)
    manager.set_current_playlist(pl.id)
    return pl


def rename_target_name(manager: PlaylistManager, playlist_id: str) -> Optional[str]:
    """重命名对话框的初始值；``None`` 表示歌单不存在（原文 `if pl is None: return`）。"""
    pl = manager.get_playlist(playlist_id)
    return None if pl is None else pl.name


def apply_rename(manager: PlaylistManager, playlist_id: str, raw_name: Any) -> bool:
    """把对话框输入的名字应用到歌单。

    返回值是**"是否真的发起了重命名"**（名字非空）。对
    `PlaylistManager.rename_playlist` 的返回值**不作判断** —— 原文在系统歌单改名
    失败时也照常重建侧边栏并落盘，这里保持同一条路径（见模块 docstring 第 2 条）。
    """
    name = normalize_name(raw_name)
    if name is None:
        return False
    manager.rename_playlist(playlist_id, name)
    return True


def open_playlist(manager: PlaylistManager, playlist_id: str) -> Optional[Playlist]:
    """切入某个歌单：设为当前歌单并返回它；``None`` 表示歌单不存在。

    原文顺序是"先取、取不到就 return，取到了再 `set_current_playlist`"，
    这里保持同序 —— 歌单不存在时**不会**改动当前歌单。
    """
    pl = manager.get_playlist(playlist_id)
    if pl is None:
        return None
    manager.set_current_playlist(playlist_id)
    return pl


def remove_song_from_current(
    manager: PlaylistManager, song_index: int, readonly: bool = False
) -> Optional[Playlist]:
    """从**当前歌单**移除一首歌；返回需要重绘的歌单，``None`` = 没有发生移除。

    ``readonly=True`` 对应"正在查看网易云远程歌单"（原文 `if self._music_wy_remote_view_id: return`）：
    远程歌单只读，禁止编辑。
    """
    if readonly:
        return None
    pl = manager.get_current_playlist()
    if pl is None:
        return None
    if not manager.remove_song(pl.id, song_index):
        return None
    return pl


def add_song_to_playlist(
    manager: PlaylistManager,
    playlist_id: str,
    song_info: Any,
    is_online: bool = False,
    metadata: Optional[dict] = None,
) -> AddSongOutcome:
    """把歌曲加进指定歌单（去重规则在 `PlaylistManager.add_song`）。

    原文是在 `add_song` 成功**之后**才去查"当前歌单是不是被添加的这个"；这里同序 ——
    侧边栏重建不会改动 `current_playlist_id`，所以先后无差别（见测试
    `test_add_song_outcome_looks_up_current_after_add`）。
    """
    if is_online:
        song = PlaylistSong.from_online_info(song_info)
    else:
        song = PlaylistSong.from_local_file(song_info, metadata)
    if not manager.add_song(playlist_id, song):
        return AddSongOutcome(added=False)
    current = manager.get_current_playlist()
    if current is not None and current.id == playlist_id:
        return AddSongOutcome(added=True, playlist=current)
    return AddSongOutcome(added=True)


def add_to_playlist_menu_plan(
    manager: PlaylistManager,
    song_info: Any,
    is_online: bool = False,
    metadata: Optional[dict] = None,
) -> AddToPlaylistMenuPlan:
    """"添加到歌单"菜单的数据准备。

    逐歌单检查是否已存在（**不是**"任意歌单已存在"，否则一个歌单加了会全部显示已加 ——
    原文注释），已存在的条目文案加 `✓` 后缀并置灰。

    调用前界面已经确认至少存在一个歌单（那个分支是 messagebox + i18n，见数据类
    docstring）；这里只按 `manager.playlists` 现取一份来生成条目。
    """
    if is_online:
        song = PlaylistSong.from_online_info(song_info)
    else:
        song = PlaylistSong.from_local_file(song_info, metadata)
    items = []
    for pl in manager.playlists:
        exists = manager.is_song_in_playlist(pl.id, song)
        text = f"{pl.name}{ALREADY_ADDED_SUFFIX}" if exists else pl.name
        items.append(PlaylistMenuItem(playlist_id=pl.id, text=text, exists=exists))
    return AddToPlaylistMenuPlan(song=song, items=items)


# ══════════════════════════════════════════════════════════════════════
# 排序
# ══════════════════════════════════════════════════════════════════════

#: 内存排序用的临时 `Playlist` id（原文传的是远程歌单 key；它不参与排序，见 `sort_songs_in_place`）
TRANSIENT_PLAYLIST_ID = "__sort__"


def toggle_sort_mode(current_mode: str, mode: str) -> str:
    """同模式再点一次切换方向；不同模式则直接采用新模式。

    原文是**两处逐字重复**的 4 段 ``if/elif`` 链（本地歌单与远程歌单各一份），
    这里合并成一份。``mode`` 不在那 4 个模式里时原样返回（原文的链不命中任何分支）。
    """
    if current_mode != mode:
        return mode
    return SORT_FLIP.get(mode, mode)


def sort_songs_in_place(songs: List[PlaylistSong], mode: str) -> None:
    """按模式**就地**排序一个歌曲列表（远程歌单的内存排序用）。

    原文是 ``Playlist(id=..., songs=songs, sort_mode=mode)`` +
    ``PlaylistManager._sort_playlist_internal(transient)``：临时 `Playlist` 与 `songs`
    **共享同一个 list 对象**，所以 `_sort_playlist_internal` 排的就是 `songs` 本身。
    这里照抄这条共享关系 —— 换成 `list(songs)` 再排会让远程歌单的排序静默失效。
    """
    PlaylistManager._sort_playlist_internal(
        Playlist(id=TRANSIENT_PLAYLIST_ID, songs=songs, sort_mode=mode)
    )


def local_sort_plan(manager: PlaylistManager, mode: str) -> Optional[SortPlan]:
    """本地歌单排序；``None`` = 没有当前歌单（原文 ``if pl is None: return``）。

    对 `PlaylistManager.sort` 的返回值**不作判断** —— 原文也不判断（模式非法时它返回
    False，界面照旧刷新排序按钮与歌曲列表），这里保持同一条路径。
    """
    pl = manager.get_current_playlist()
    if pl is None:
        return None
    mode = toggle_sort_mode(pl.sort_mode, mode)
    manager.sort(pl.id, mode)
    return SortPlan(mode=mode, playlist=pl)


def remote_sort_plan(
    songs: Sequence[PlaylistSong], current_mode: str, mode: str
) -> Optional[str]:
    """远程歌单的内存排序；返回实际生效的模式，``None`` = 无需动作（空歌单）。

    远程歌单**只读、不落盘**：`sort_songs_in_place` 直接改内存列表，
    既不进 `PlaylistManager` 也不写 `music.json`。
    """
    if not songs:
        return None
    mode = toggle_sort_mode(current_mode, mode)
    sort_songs_in_place(songs, mode)
    return mode


# ══════════════════════════════════════════════════════════════════════
# 歌曲行的显示取值
# ══════════════════════════════════════════════════════════════════════

#: 歌名显示上限与截断后保留长度（原文两处都写死 `50` / `[:47]`）
ROW_TITLE_MAX = 50
ROW_TITLE_KEEP = 47


def playlist_row_plan(filepath: str, metadata: Optional[dict] = None) -> PlaylistRowPlan:
    """`_add_playlist_row`（"全部歌曲"列表行）的显示取值：``歌名 - 歌手`` + 时长。

    歌名超过 `ROW_TITLE_MAX` 字符时截到 `ROW_TITLE_KEEP` 再补 ``...``；
    时长为 0 时不显示（原文 ``dur_text = _format_time(duration) if duration else ""``）。
    """
    meta = metadata or {}
    title = meta.get("title", os.path.basename(filepath))
    duration = meta.get("duration", 0)
    artists = f" - {meta['artist']}" if meta.get("artist") else ""
    shown = title if len(title) <= ROW_TITLE_MAX else title[:ROW_TITLE_KEEP] + "..."
    return PlaylistRowPlan(
        name_text=f"{shown}{artists}",
        dur_text=format_time(duration) if duration else "",
    )


def song_row_plan(song: PlaylistSong, readonly: bool = False) -> SongRowPlan:
    """`_add_playlist_song_row`（歌单歌曲行）的显示取值。

    只有在线歌曲才显示时长与来源标记；``readonly=True``（网易云远程歌单只读）时
    不显示移除按钮。
    """
    is_online = song.source_type == "online"
    return SongRowPlan(
        display_text=song.get_display_text(max_title=ROW_TITLE_MAX),
        dur_text=format_time(song.online_interval) if (is_online and song.online_interval) else "",
        source_tag=song.online_source.upper() if is_online else "",
        show_remove=not readonly,
    )


# ══════════════════════════════════════════════════════════════════════
# 播放入口
# ══════════════════════════════════════════════════════════════════════


def play_index_target(playlist: Sequence[str], idx: int) -> Optional[str]:
    """`_play_from_index` 的越界判定与取值；``None`` = 索引非法。

    原文 ``if idx < 0 or idx >= len(self._music_playlist): return``。
    """
    if idx < 0 or idx >= len(playlist):
        return None
    return playlist[idx]


def play_all_target(
    songs: Sequence[PlaylistSong],
    remote: bool = False,
    path_exists: Optional[Callable[[str], bool]] = None,
) -> Optional[PlayAllTarget]:
    """"播放全部"要播的第一首；``None`` = 歌单为空（原文 ``if not pl.songs: return``）。

    * ``remote=True``（网易云远程歌单，只读）：只认在线歌曲；一首都没有时
      ``index == -1``（原文循环走完直接 return，什么都不播）；
    * ``remote=False``：本地歌曲要求文件存在，在线歌曲直接可播；同样可能 ``index == -1``。

    ``path_exists`` 是文件存在性判定的注入缝（默认 `os.path.exists`），
    测试不必碰真实磁盘。``context_songs`` 是**副本**（原文 ``list(pl.songs)``），
    供"上下曲"使用的歌单上下文。
    """
    if not songs:
        return None
    if path_exists is None:
        path_exists = os.path.exists
    context = list(songs)
    if remote:
        for idx, s in enumerate(songs):
            if s.source_type == "online":
                return PlayAllTarget(context_songs=context, index=idx)
        return PlayAllTarget(context_songs=context, index=-1)
    for idx, s in enumerate(songs):
        if s.source_type == "local" and path_exists(s.file_path):
            return PlayAllTarget(context_songs=context, index=idx)
        elif s.source_type == "online":
            return PlayAllTarget(context_songs=context, index=idx)
    return PlayAllTarget(context_songs=context, index=-1)


# ══════════════════════════════════════════════════════════════════════
# 播放历史
# ══════════════════════════════════════════════════════════════════════


def record_history_local(
    manager: PlaylistManager, filepath: str, metadata: Optional[dict] = None
) -> bool:
    """把本地歌曲写进播放历史（原文 `_music_record_play_history_local` 里那两行）。"""
    song = PlaylistSong.from_local_file(filepath, metadata)
    return manager.record_to_history(song)


def record_history_online(manager: PlaylistManager, online_info: Any) -> bool:
    """把在线歌曲写进播放历史；``online_info`` 不能转成 `PlaylistSong` 时返回 False。

    原文是 ``try: PlaylistSong.from_online_info(...) except Exception: return``，
    这里保持"任何异常都当作记录失败"（不区分异常类型）。
    """
    try:
        song = PlaylistSong.from_online_info(online_info)
    except Exception:
        return False
    return manager.record_to_history(song)


def history_refresh_target(
    manager: PlaylistManager, tab_mode: str
) -> Optional[Playlist]:
    """记录历史后该不该立刻重绘：只有"停在歌单标签页 **且** 正在看历史歌单"才重绘。"""
    if tab_mode != "playlist":
        return None
    current = manager.get_current_playlist()
    if current is None or current.id != HISTORY_PLAYLIST_ID:
        return None
    return current


# ══════════════════════════════════════════════════════════════════════
# 底部迷你播放条（全局底栏）
# ══════════════════════════════════════════════════════════════════════

#: 播放/暂停按钮字形（原文写死 `"⏸" if is_playing else "▶"`）。**逐字保留**，不换图标。
PLAY_GLYPH_PLAYING = "▶"
PLAY_GLYPH_PAUSED = "⏸"


def footer_plan(
    *,
    path: Optional[str],
    is_online_playing: bool,
    online_info: Any,
    is_playing: bool,
    is_paused: bool,
    read_metadata: Callable[[str], dict],
) -> FooterPlan:
    """底部迷你播放条的绘制计划（原文 `_update_music_footer`）。

    * ``visible`` 为假 → 界面把整条 `pack_forget`；
    * ``text`` 是 ``歌名 - 歌手``（在线取搜索结果信息、本地取元数据），超长截断；
    * ``play_text`` 是播放/暂停按钮的字形。

    ``read_metadata`` 只在**本地分支**被调用：原文也只在那个分支里查元数据
    （在线播放时 ``path`` 可能是 None，提前查会抛 ``os.path.basename(None)``）。
    注意 ``meta.get("title", os.path.basename(path))`` 的默认值是**先求值再传**的，
    所以即使元数据里有 title，``path`` 为 None 也一样抛 —— 本轮逐字保留这条路径。
    """
    play_text = PLAY_GLYPH_PAUSED if is_playing else PLAY_GLYPH_PLAYING
    if not ((path or is_online_playing) and (is_playing or is_paused)):
        return FooterPlan(visible=False, text="", play_text="")
    if is_online_playing and online_info:
        title = online_info.name
        artist = online_info.singer or ""
    else:
        meta = read_metadata(path)
        title = meta.get("title", os.path.basename(path))
        artist = meta.get("artist", "")
    text = title
    if artist:
        text = f"{title} - {artist}"
    if len(text) > FOOTER_TEXT_MAX:
        text = text[:FOOTER_TEXT_KEEP] + "..."
    return FooterPlan(visible=True, text=text, play_text=play_text)


__all__ = [
    "ALREADY_ADDED_SUFFIX",
    "AddSongOutcome",
    "AddToPlaylistMenuPlan",
    "FOOTER_TEXT_KEEP",
    "FOOTER_TEXT_MAX",
    "FooterPlan",
    "PLAY_GLYPH_PAUSED",
    "PLAY_GLYPH_PLAYING",
    "PlayAllTarget",
    "PlaylistMenuItem",
    "PlaylistRowPlan",
    "ROW_TITLE_KEEP",
    "ROW_TITLE_MAX",
    "SORT_FLIP",
    "SYSTEM_PLAYLIST_ICON",
    "SidebarItem",
    "SidebarPlan",
    "SongRowPlan",
    "SortPlan",
    "TRANSIENT_PLAYLIST_ID",
    "add_song_to_playlist",
    "add_to_playlist_menu_plan",
    "apply_rename",
    "create_named_playlist",
    "footer_plan",
    "history_refresh_target",
    "local_sort_plan",
    "normalize_name",
    "open_playlist",
    "play_all_target",
    "play_index_target",
    "playlist_label",
    "playlist_row_plan",
    "record_history_local",
    "record_history_online",
    "remote_sort_plan",
    "remove_song_from_current",
    "rename_target_name",
    "sidebar_plan",
    "song_row_plan",
    "sort_songs_in_place",
    "toggle_sort_mode",
]
