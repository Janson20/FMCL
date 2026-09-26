"""网易云账号歌单（只读远程视图）的同步编排与分页数据准备。

对应宿主是 `ui/app_music.py` 的 `MusicPlayerMixin` 里 `_music_wy_*` 那一段：
`_music_wy_remote_key` / `_music_wy_sync_worker` / `_music_wy_tracks_worker` /
`_music_wy_apply_sync` / `_music_wy_apply_tracks` / `_music_wy_dispatcher_tick` /
`_music_render_wy_remote_songs` / `_music_wy_update_pager` / `_music_wy_go_page` /
`_music_show_wy_remote_playlist`。

## 只读约定（不能破）

远程歌单**不进 `PlaylistManager`**：下面三点是本轮硬约束，
`poc/_verify_1_4b_music.py` 第 8 组用 AST 机械核对。

1. 本模块**不 import** `services.music_playlist` / `ui.music_playlist`，因此碰不到
   `PlaylistManager`；
2. 本模块**不 import** `os` / `pathlib` / `json` / `tempfile`，也没有任何
   `open(...)` / `write*` 调用 —— 它不落盘，远程歌单只在内存里；
3. 侧边栏条目 id 的前缀约定（``REMOTE_PREFIX = "wy:"``）原样保留：界面靠
   ``playlist_id.startswith(_WY_REMOTE_PREFIX)`` 把它与本地歌单 id 区分开，
   前缀一改，本地歌单的右键菜单就会给远程条目挂上编辑动作。

## 切缝判据（与 1.4-A 同一条）

**这段代码是否 import GUI、或是否直接创建/销毁控件？** 是 → 留界面；否 → 进服务。
`poc/_probe_1_4b_attrs.py` 量过：`_music_wy_*` 这些属性**全都有范围外的读写点**
（`__init_music` 初始化、`_highlight_playlist_song` 与 `_music_do_sort` 写
`_music_wy_remote_page` / `_music_wy_remote_sort_mode`、`_music_prefetch_worker` 读
`_music_wy_queue`）。按 1.4-A 的规矩，**界面侧仍是唯一所有者**：本模块只在调用时
把这些值**当参数收进来**，把新值放在返回值里（`SyncApplyPlan` / `WyPagerPlan` /
`PageWindow` 这些数据类），界面逐条写回。因此本模块**无状态**。

## 服务不做什么

不弹窗、不起线程、不调 `after`、不碰控件、不 import 任何 GUI 栈、不 import `ui.*`、
不用 `ui.i18n` 的文案函数（侧边栏标题的"（同步失败）"后缀由界面拼，服务只交出
`sync_failed` 布尔）。事件队列的**消费**用生成器 `drain_events()` 交给界面 `for` —— 
生成器逐条 `get_nowait()`，处理某条事件抛异常时生成器随即被关闭，**剩余事件留在队列里**
（与原文"处理异常就跳出 while、下次 tick 再取"逐字等价，不会丢事件）。

## 离线可测性

音源后端走 `WyRemoteApi`：默认实现**每次调用**都读
``services.music_source.wy_is_logged_in`` / ``wy_get_user_playlists`` /
``wy_get_playlist_tracks`` 这些模块级全局名，所以
``monkeypatch.setattr(music_source, "wy_get_user_playlists", fake)`` 真的生效；
测试也可以直接传 ``api=FakeApi()``。两条路都不联网。
"""

from __future__ import annotations

import math
import queue
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from logzero import logger

from services import music_source

#: 远程歌单侧边栏条目 id 前缀（原文 `_WY_REMOTE_PREFIX = "wy:"`，**不可改**）
REMOTE_PREFIX = "wy:"
#: 远程歌单歌曲列表每页条数（原文 `_MUSIC_WY_PAGE_SIZE = 20`）
PAGE_SIZE = 20
#: 定期自动刷新歌单列表的间隔（毫秒，原文 `_WY_REMOTE_PERIODIC_MS = 10 * 60 * 1000`）
PERIODIC_MS = 10 * 60 * 1000
#: 主线程调度器轮询间隔（毫秒，原文 `self.after(200, ...)`）
DISPATCH_INTERVAL_MS = 200

# ── 同步 worker 的三态（原文 `state, data = "ok", []`）────────────────────
STATE_OK = "ok"
STATE_LOGGED_OUT = "logged_out"
STATE_ERROR = "error"

# ── 事件队列的种类（worker 投递 → 主线程分派）────────────────────────────
EVENT_SYNC = "sync"
EVENT_TRACKS = "tracks"
EVENT_PREFETCH = "prefetch"
#: 预取失败：**故意不设分派目标**（不设槽位，播完时走常规按需流程）
EVENT_PREFETCH_FAIL = "prefetch_fail"
#: 队列里可能出现过的全部种类（测试用来钉住协议面）
EVENT_KINDS = (EVENT_SYNC, EVENT_TRACKS, EVENT_PREFETCH, EVENT_PREFETCH_FAIL)

# ── `_music_wy_apply_sync` 的五种结论 ────────────────────────────────────
SYNC_STALE = "stale"  # 过期同步结果（已触发新的同步），丢弃
SYNC_LOGGED_OUT = "logged_out"  # 账号已退出：清空远程歌单与歌曲缓存
SYNC_ERROR = "error"  # 网络/接口失败：保留上次成功结果，只标记失败
SYNC_UNCHANGED = "unchanged"  # 列表无变化，不重建侧边栏
SYNC_UPDATE = "update"  # 有变化：更新列表并清理已删除歌单的缓存

# ── `_music_wy_apply_tracks` 的三种结论 ──────────────────────────────────
TRACKS_CACHE_ONLY = "cache_only"  # 用户已切换歌单：成功结果仅缓存
TRACKS_FAILED = "failed"  # None = 拉取失败
TRACKS_RENDER = "render"  # [] = 空歌单，也要渲染


class WyRemoteApi:
    """网易云远程歌单的后端调用（默认实现 = 真实音源）。

    所有方法都在**调用时**读 ``services.music_source`` 的模块级全局名，
    这样 monkeypatch 那些名字能真的换掉后端（1.4-A 在
    ``services/music_audio._winsdk_available`` 上踩过"补丁点不是被读的那个名字"的坑）。
    """

    def is_logged_in(self) -> bool:
        return bool(music_source.wy_is_logged_in())

    def get_user_playlists(self) -> Optional[List[dict]]:
        return music_source.wy_get_user_playlists()

    def get_playlist_tracks(self, playlist_id: str) -> Optional[List[Any]]:
        return music_source.wy_get_playlist_tracks(playlist_id)


#: 默认后端单例（无状态，可安全共享）
DEFAULT_API = WyRemoteApi()


def remote_key(pl_id: str) -> str:
    """远程歌单的侧边栏条目 id（与本地歌单 id 区分）。

    原文 ``f"{_WY_REMOTE_PREFIX}{pl_id}"``；界面侧的
    `_rebuild_playlist_sidebar` 靠这个前缀把远程条目从本地歌单的
    右键菜单/编辑动作里排除（见 `is_remote_key`）。
    """
    return f"{REMOTE_PREFIX}{pl_id}"


def is_remote_key(playlist_id: str) -> bool:
    """侧边栏条目 id 是否是远程歌单（原文 `playlist_id.startswith(_WY_REMOTE_PREFIX)`）。"""
    return bool(playlist_id) and playlist_id.startswith(REMOTE_PREFIX)


def strip_remote_key(playlist_id: str) -> str:
    """从侧边栏条目 id 取回远程歌单真实 id（原文 `playlist_id[len(前缀):]`）。"""
    return playlist_id[len(REMOTE_PREFIX):]


def entry_label(playlist: Mapping[str, Any]) -> str:
    """侧边栏远程歌单条目的显示名（原文 ``f"{pl['name']} ({pl['track_count']})"``）。"""
    return f"{playlist['name']} ({playlist['track_count']})"


def page_count(total: int, page_size: int = PAGE_SIZE) -> int:
    """总页数（原文 ``max(1, math.ceil(total / _MUSIC_WY_PAGE_SIZE))``）。

    注意空歌单也算 1 页 —— 这与"隐藏分页栏"是两件事，由 `pager_plan` 判定。
    """
    return max(1, math.ceil(total / page_size))


def clamp_page(page: int, pages: int) -> int:
    """把页码钳到有效范围（原文 ``if page > pages: page = pages``，只压下不抬上）。"""
    return pages if page > pages else page


@dataclass
class PageWindow:
    """一页的切片范围（原文 `_music_render_wy_remote_songs` 里那三行）。"""

    page: int
    pages: int
    start: int
    stop: int
    total: int


def page_window(total: int, page: int, page_size: int = PAGE_SIZE) -> PageWindow:
    """按当前页码算出该页在完整列表中的切片。

    页码超界会被钳到最后一页（切换歌单后回到第一页、删除歌曲后总页数变小，
    都可能让保存的页码越界）。
    """
    pages = page_count(total, page_size)
    page = clamp_page(page, pages)
    start = (page - 1) * page_size
    return PageWindow(page=page, pages=pages, start=start, stop=start + page_size, total=total)


@dataclass
class WyPagerPlan:
    """远程歌单分页栏的可见性与按钮状态（原文 `_music_wy_update_pager`）。"""

    #: True 表示 `frame.pack_forget()`（未打开远程歌单，或整单不超过一页）
    hidden: bool
    page: int
    pages: int
    prev_enabled: bool
    next_enabled: bool


def pager_plan(*, total: int, page: int, has_view: bool, page_size: int = PAGE_SIZE) -> WyPagerPlan:
    """远程歌单分页栏判定：未打开远程视图、或 <= 一页 → 隐藏；否则按页数钳页码。"""
    if not has_view or total <= page_size:
        return WyPagerPlan(
            hidden=True, page=page, pages=1, prev_enabled=False, next_enabled=False
        )
    pages = page_count(total, page_size)
    page = clamp_page(page, pages)
    return WyPagerPlan(
        hidden=False,
        page=page,
        pages=pages,
        prev_enabled=page > 1,
        next_enabled=page < pages,
    )


def go_page_target(*, page: int, current_page: int, total: int, page_size: int = PAGE_SIZE) -> Optional[int]:
    """远程歌单翻页的目标页码；非法（越界/就是当前页/未打开）返回 None。

    原文三条守卫：``page < 1 or page > pages or page == 当前页`` → 直接 return。
    注意这里**没有**"未打开远程视图"的守卫 —— 原文那一层在调用方
    （`_music_wy_go_page` 第一行判 `_music_wy_remote_view_id`）。
    """
    pages = page_count(total, page_size)
    if page < 1 or page > pages or page == current_page:
        return None
    return page


def cached_songs(cache: Mapping[str, Any], pl_id: str) -> Optional[Any]:
    """取已缓存的远程歌单歌曲。

    **区分 None 与 []**：``None`` = 没缓存过（要去后台拉），``[]`` = 空歌单且成功
    缓存过（直接渲染空列表，不要再请求）。所以只用 `get` 的存在性，不用真值判断。
    """
    return cache.get(pl_id)


def make_event(kind: str, *payload: Any) -> tuple:
    """构造一条投给主线程队列的事件（元组，第一项是种类）。"""
    return (kind, *payload)


def drain_events(event_queue: Any) -> Iterator[Any]:
    """逐条取空事件队列（生成器）。

    做成生成器而不是"先取完再返回列表"是**为了保行为**：处理某条事件抛异常时，
    界面那个 `for` 会立刻退出、生成器被关闭，**剩下的事件仍留在队列里**，
    下一次 tick 再来取 —— 与原文 ``while True: get_nowait() / handle()`` 一样。
    列表版会把剩下的事件从队列里摘掉却又不处理，等于丢事件。
    """
    while True:
        try:
            event = event_queue.get_nowait()
        except queue.Empty:
            return
        yield event


def sync_playlists(*, api: Optional[WyRemoteApi] = None) -> Tuple[str, List[dict]]:
    """检查登录态并拉取歌单列表（原文 `_music_wy_sync_worker` 的线程体）。

    Returns:
        ``(state, data)``：state ∈ {ok, logged_out, error}；
        未登录 → ``("logged_out", [])``；接口返回 None → ``("error", None)``；
        任何异常 → 记 warning 后 ``("error", [])``（原文 `data` 保持初始值 ``[]``）。
    """
    backend = DEFAULT_API if api is None else api
    state, data = STATE_OK, []
    try:
        if not backend.is_logged_in():
            state = STATE_LOGGED_OUT
        else:
            data = backend.get_user_playlists()
            if data is None:
                state = STATE_ERROR
    except Exception as e:
        logger.warning(f"网易云歌单同步失败: {e}")
        state = STATE_ERROR
    return state, data


def fetch_playlist_tracks(
    pl_id: str, *, api: Optional[WyRemoteApi] = None
) -> Optional[List[Any]]:
    """拉取远程歌单歌曲（原文 `_music_wy_tracks_worker` 的线程体）。

    失败（异常）→ 记 warning 后返回 None；None 与 [] 语义不同
    （None = 失败，[] = 空歌单），调用方据此决定显示"加载失败"还是空列表。
    """
    backend = DEFAULT_API if api is None else api
    try:
        return backend.get_playlist_tracks(pl_id)
    except Exception as e:
        logger.warning(f"获取网易云歌单歌曲失败 [{pl_id}]: {e}")
        return None


@dataclass
class SyncApplyPlan:
    """`_music_wy_apply_sync` 的结论（界面按 kind 分派，写回时机由界面掌握）。

    为什么不是"服务直接改状态"：`_music_wy_*` 全部属性在范围外还有读写点
    （见模块 docstring），按 1.4-A 的规矩界面侧仍是唯一所有者。这里只把
    **结论**交回去，界面按原文的分支顺序逐条写回 —— 顺序有意义：
    原文在 logged_out 分支里是"先清空当前查看的远程歌单（可能切回历史歌单）、
    再清空列表与缓存、最后置 sync_failed 并重建侧边栏"，中间那次
    `_music_show_playlist` 会让 `_rebuild_playlist_sidebar()` 看到**旧的**
    `_music_wy_sync_failed` 与**旧的**歌单列表，写回顺序一换侧边栏标题就变了。
    """

    kind: str
    #: 写回 `self._music_wy_sync_failed`
    sync_failed: bool = False
    #: 需要清空当前查看的远程歌单（view_id / view_songs）
    clear_view: bool = False
    #: 清空 view 之后要切回播放历史（原文条件是 `tab_mode == "playlist"`）
    fallback_to_history: bool = False
    #: 已从列表里消失的歌单 id（要清掉它们的歌曲缓存与加载标记）
    drop_ids: Set[str] = field(default_factory=set)


def plan_sync_apply(
    *,
    seq: int,
    sync_seq: int,
    state: str,
    playlists: Optional[List[dict]],
    current_playlists: Sequence[dict],
    view_id: Optional[str],
    tab_mode: str,
) -> SyncApplyPlan:
    """应用一次歌单列表同步结果的判定（逐条照搬原文 `_music_wy_apply_sync`）。"""
    if seq != sync_seq:
        return SyncApplyPlan(kind=SYNC_STALE)
    if state == STATE_LOGGED_OUT:
        clearing_view = bool(view_id)
        return SyncApplyPlan(
            kind=SYNC_LOGGED_OUT,
            sync_failed=False,
            clear_view=clearing_view,
            fallback_to_history=clearing_view and tab_mode == "playlist",
        )
    if state == STATE_ERROR:
        return SyncApplyPlan(kind=SYNC_ERROR, sync_failed=True)
    if current_playlists == playlists:
        return SyncApplyPlan(kind=SYNC_UNCHANGED, sync_failed=False)
    new_ids = {p["id"] for p in playlists or []}
    old_ids = {p["id"] for p in current_playlists}
    clearing_view = bool(view_id) and view_id not in new_ids
    return SyncApplyPlan(
        kind=SYNC_UPDATE,
        sync_failed=False,
        clear_view=clearing_view,
        fallback_to_history=clearing_view and tab_mode == "playlist",
        drop_ids=old_ids - new_ids,
    )


def tracks_action(pl_id: str, infos: Any, view_id: Optional[str]) -> str:
    """应用一次歌单歌曲拉取结果的判定（逐条照搬原文 `_music_wy_apply_tracks`）。

    * 拉取结果对应的歌单**不是**当前查看的那个 → ``TRACKS_CACHE_ONLY``
      （成功结果仅缓存，供下次点击直接使用；None 不缓存）；
    * 是当前查看的且结果是 None → ``TRACKS_FAILED``（显示"加载失败"，可再次点击重试）；
    * 是当前查看的且结果是列表（含空列表）→ ``TRACKS_RENDER``。
    """
    if view_id != pl_id:
        return TRACKS_CACHE_ONLY
    if infos is None:
        return TRACKS_FAILED
    return TRACKS_RENDER


# ══════════════════════════════════════════════════════════════════════
# 阶段 1 任务 1.4-C：把 1.4-B 留在这里的三个入口**接上界面调用方**
#
# 1.4-B 新增 `is_remote_key` / `strip_remote_key` / `entry_label` 时说得很清楚：
# "界面侧对应行（`_build_sidebar_item` / `_music_rebuild_wy_remote_sidebar`）
# 属 1.4-C，本轮不动"。本轮的实测结论见 `poc/_verify_1_4c_music.py` 第 12 条：
# 这三个函数在 1.4-C 之前**只有 `tests/test_music_online.py` 引用**，
# `ui/` 下一个调用方都没有（`ui/app_music.py` 当时仍在手写前缀与文案）。
# 现在界面侧那三行改成调用它们，"前缀约定的唯一真相"才真的只有一份。
# ══════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class RemoteSidebarItem:
    """侧边栏"网易云歌单"分组里一个条目的绘制参数。

    ``playlist_id`` 是**带前缀的侧边栏 key**（`remote_key` 的结果），
    界面据此与本地歌单 id 区分（`is_remote_key`）。
    """

    playlist_id: str
    text: str
    is_active: bool


def sidebar_items(remote_playlists: Sequence[Mapping[str, Any]], view_id: Optional[str]) -> List[RemoteSidebarItem]:
    """侧边栏"网易云歌单"分组的条目清单（原文 `_music_rebuild_wy_remote_sidebar` 的循环）。

    远程歌单**只读**：条目 id 一律带前缀、只用于点击查看，不进 `PlaylistManager`。
    """
    return [
        RemoteSidebarItem(
            playlist_id=remote_key(pl["id"]),
            text=entry_label(pl),
            is_active=(pl["id"] == view_id),
        )
        for pl in remote_playlists
    ]


def header_title(title: str, hint: str, failed: bool) -> str:
    """分组标题：最近一次同步失败时附上提示（原文 ``f"{title}（{hint}）"``）。

    ``title`` / ``hint`` 由界面侧用 `_()` 取好传进来 —— 服务不做 i18n。
    """
    return f"{title}（{hint}）" if failed else title


__all__ = [
    "DEFAULT_API",
    "DISPATCH_INTERVAL_MS",
    "EVENT_KINDS",
    "EVENT_PREFETCH",
    "EVENT_PREFETCH_FAIL",
    "EVENT_SYNC",
    "EVENT_TRACKS",
    "PAGE_SIZE",
    "PERIODIC_MS",
    "REMOTE_PREFIX",
    "STATE_ERROR",
    "STATE_LOGGED_OUT",
    "STATE_OK",
    "SYNC_ERROR",
    "SYNC_LOGGED_OUT",
    "SYNC_STALE",
    "SYNC_UNCHANGED",
    "SYNC_UPDATE",
    "TRACKS_CACHE_ONLY",
    "TRACKS_FAILED",
    "TRACKS_RENDER",
    "PageWindow",
    "RemoteSidebarItem",
    "SyncApplyPlan",
    "WyPagerPlan",
    "WyRemoteApi",
    "cached_songs",
    "clamp_page",
    "drain_events",
    "entry_label",
    "fetch_playlist_tracks",
    "go_page_target",
    "header_title",
    "is_remote_key",
    "make_event",
    "page_count",
    "page_window",
    "pager_plan",
    "plan_sync_apply",
    "remote_key",
    "sidebar_items",
    "strip_remote_key",
    "sync_playlists",
    "tracks_action",
]
