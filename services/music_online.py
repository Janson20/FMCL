"""在线搜索编排、自动音质解析与取流完成判定（阶段 1 任务 1.4-B，形态 2：逻辑与界面切分）。

对应宿主是 `ui/app_music.py` 的 `MusicPlayerMixin` 里**在线检索/取流**那一段：
`_music_do_search` / `_music_start_search` / `_music_search_go_page` /
`_music_online_search_thread` / `_music_rebuild_search_results` / `_music_rebuild_pager` /
`_music_add_search_row` / `_music_apply_original_backfill` / `_music_resolve_auto_quality`
/ `_music_resolve_auto_quality_async` / `_music_on_stream_ready` /
`_fetch_and_display_online_cover` / `_update_now_playing_info`。

## 切缝判据（与 1.4-A 同一条）

**这段代码是否 import GUI、或是否直接创建/销毁控件？** 是 → 留界面；否 → 进服务。
`poc/_probe_1_4b_attrs.py` 量过：`_music_search_*` / `_music_pager_*` / `_music_now_*`
这些属性**全都有范围外的读写点**（多数是 `__init_music`，还有 `_music_play_online_from_index`
读 `_music_search_results`、`_music_play_playlist_all` 读 `_music_wy_remote_view_songs` 等）。
按 1.4-A 定下的规矩，**界面侧仍是唯一所有者**：本模块只在调用时把它们**当参数收进来**，
把新值放在**返回值**里（`SearchPlan` / `SearchOutcome` / `PagerPlan` / `SearchRowPlan` /
`NowPlayingPlan` 这些数据类），界面逐条写回自己的属性。因此本模块**无状态**：
没有一个模块级可变全局，同一个函数被两个窗口调用也不会串。

## 服务不做什么

不弹窗、不起线程、不调 `after`、不碰控件、不 import 任何 GUI 栈、不 import `ui.*`、
不用 `ui.i18n` 的文案函数。要界面做的事一律走返回值；文案只交出
**i18n key + 参数**（`SearchRowPlan.tag_key` / `SearchOutcome.status`），由界面调 `_()`。
`poc/_verify_1_4b_music.py` 第 6 组用 AST 机械核对这条。

## 离线可测性

音源表**不**在导入期快照，而是每次调用经 `get_source()` 读
``services.music_source.MUSIC_SOURCES`` 这个**模块级全局名**（`ui.music_source` 是它的
转发 shim，两者是同一个模块对象）。所以下面两种补丁都真的生效：

* ``monkeypatch.setattr(music_source, "MUSIC_SOURCES", {"kw": fake})``
* ``monkeypatch.setattr(music_source, "resolve_track", fake)``

另外每个入口都留了显式注入缝（``sources`` / ``http_get``），默认值是真实实现，
行为与原文一字不差，测试传假对象即可全程离线。
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import requests
from logzero import logger

from services import music_audio, music_source

# ── 搜索参数（原文写死的字面量）──────────────────────────────────────────
#: 音源未声明 `limits["search"]` 时的每页条数（原文 `src.limits.get("search", 30) if src else 30`）
DEFAULT_SEARCH_PAGE_SIZE = 30
#: 首次搜索的页码（原文 `self._music_start_search(1)`）
FIRST_SEARCH_PAGE = 1

# ── 自动音质 ─────────────────────────────────────────────────────────────
#: 自动模式期望的最高档位（原文 `src.get_best_quality(online_info, "flac24bit")`）
AUTO_QUALITY_PREFERRED = "flac24bit"
#: 无法解析时的兜底档位（原文三处写死 "128k"）
FALLBACK_QUALITY = "128k"
#: 音质信息补齐时重搜同款歌的候选条数（原文 `src.search(keyword, page=1, limit=10)`）
BACKFILL_SEARCH_LIMIT = 10

# ── 搜索结果行（原文 `_music_add_search_row` 里写死的排版常量）────────────
#: 歌名超过这个长度就截断（原文 `if len(info.name) <= 35`）
ROW_NAME_MAX = 35
#: 截断时保留的字符数（原文 `info.name[:33] + "..."`）
ROW_NAME_KEEP = 33
#: i18n key：原唱徽章
KEY_ORIGINAL_TAG = "music_original_tag"
#: i18n key：带回原曲名的原唱徽章
KEY_ORIGINAL_TAG_WITH_NAME = "music_original_tag_with_name"

# ── 搜索状态栏的三种形态（文案与 key 由界面决定，本模块只判定形态）────────
STATUS_RESULTS = "results"  # 有结果：显示 "N 首"
STATUS_NO_MORE = "no_more"  # 非首页但无结果：显示 "没有更多结果"
STATUS_EMPTY = "empty"  # 首页无结果：显示 "没有搜索结果"

# ── 取流完成后的动作 ─────────────────────────────────────────────────────
STREAM_STALE = "stale"  # 过期请求（用户已切换播放目标），丢弃
STREAM_PLAY = "play"  # 有临时文件 → 播放
STREAM_FAILED = "failed"  # 无临时文件 → 提示取流失败

# ── 正在播放区（`_update_now_playing_info`）的三种形态 ───────────────────
NOW_PLAYING_ONLINE = "online"
NOW_PLAYING_EMPTY = "empty"
NOW_PLAYING_LOCAL = "local"


def get_source(source_id: str, *, sources: Optional[Mapping[str, Any]] = None) -> Any:
    """取音源单例；不存在返回 None。

    默认实现**每次调用**都读 ``services.music_source.MUSIC_SOURCES``（模块级全局名），
    所以 monkeypatch 那个名字能真的换掉音源表 —— 不在导入期快照成一个局部名。
    """
    table = music_source.MUSIC_SOURCES if sources is None else sources
    return table.get(source_id)


def get_source_total(source_id: str, *, sources: Optional[Mapping[str, Any]] = None) -> int:
    """音源最近一次搜索声明的结果总数（原文 `src.last_search_total if src else 0`）。"""
    src = get_source(source_id, sources=sources)
    return src.last_search_total if src else 0


def search_page_size(
    source_id: str, *, default: int = DEFAULT_SEARCH_PAGE_SIZE,
    sources: Optional[Mapping[str, Any]] = None,
) -> int:
    """每页条数按当前音源的**服务端限制**（如网易云每页最多 20 条）。

    原文：``src.limits.get("search", 30) if src else 30``。音源缺失或 `limits`
    异常时按原文一样让异常冒出去（外层 `_music_start_search` 的调用点无 try）。
    """
    src = get_source(source_id, sources=sources)
    if not src:
        return default
    return src.limits.get("search", default)


def normalize_keyword(text: Optional[str]) -> str:
    """搜索框取值 → 关键词（原文 `self._music_search_entry.get().strip()`）。"""
    return (text or "").strip()


@dataclass
class SearchPlan:
    """一次搜索请求的准入结果（原文 `_music_start_search` 里"判定 + 取每页条数"那段）。"""

    page: int
    page_size: int
    #: 新请求未返回前不沿用旧音源的页数（原文 `self._music_search_total_pages = 0`）
    reset_total_pages: bool = True
    #: 进入请求态（原文 `self._music_search_busy = True`）
    busy: bool = True


def plan_search(
    *, page: int, busy: bool, source_id: str,
    sources: Optional[Mapping[str, Any]] = None,
) -> Optional[SearchPlan]:
    """`_music_start_search` 的准入判定：忙碌或页码非法 → None（原文直接 return）。

    通过时给出页码与**当前音源**的每页条数。
    """
    if busy:
        return None
    if page < 1:
        return None
    return SearchPlan(
        page=page,
        page_size=search_page_size(source_id, sources=sources),
    )


def go_page_target(
    *, page: int, current_page: int, busy: bool, keyword: str, has_results: bool
) -> Optional[int]:
    """`_music_search_go_page` 的四条守卫（逐条照原文顺序）：

    1. 页码 < 1 或搜索进行中 → 拒绝；
    2. 没有关键词 → 拒绝；
    3. 目标页就是当前页且当前有结果 → 拒绝（避免重复请求）；
    """
    if page < 1 or busy:
        return None
    if not keyword:
        return None
    if page == current_page and has_results:
        return None
    return page


def neighbor_page(page: int, delta: int) -> int:
    """上一页/下一页的目标页码（原文 `self._music_search_page ± 1`）。"""
    return page + delta


def run_source_search(
    source_id: str, keyword: str, page: int, page_size: int,
    *, sources: Optional[Mapping[str, Any]] = None,
) -> List[Any]:
    """单音源搜索（原文 `_music_online_search_thread` 的线程体）。

    音源缺失 → 空结果；**任何异常都吞掉并记 warning**（原文行为：搜索失败不弹错，
    只把结果当空列表交给主线程）。日志文本与原文逐字一致。
    """
    try:
        src = get_source(source_id, sources=sources)
        if src:
            return src.search(keyword, page=page, limit=page_size)
        return []
    except Exception as e:
        logger.warning(f"在线搜索失败 [{source_id}]: {e}")
        return []


@dataclass
class SearchOutcome:
    """搜索结果的页数/状态判定（原文 `_music_rebuild_search_results` 的前半段）。"""

    #: 音源提供总数时一次性算出的总页数；0 表示音源没给总数
    total_pages: int
    #: 是否可能存在下一页
    has_more: bool
    #: 状态栏形态：STATUS_RESULTS / STATUS_NO_MORE / STATUS_EMPTY
    status: str
    #: 结果条数（status == STATUS_RESULTS 时用于 "N 首" 文案）
    count: int


def summarize_search(
    results: Sequence[Any], *, source_id: str, page: int, page_size: int,
    total: Optional[int] = None, sources: Optional[Mapping[str, Any]] = None,
) -> SearchOutcome:
    """结果页数/状态判定。

    原文规则：
    * 音源提供总数（``last_search_total > 0``）→
      ``total_pages = max(1, ceil(total / page_size))``，
      ``has_more = page < total_pages``（精确，不靠满页启发式）；
    * 否则 ``total_pages = 0``（未知），``has_more = len(results) >= page_size``
      （满页即推测还有下一页）；
    * 状态栏：有结果 → "N 首"；无结果且 ``page > 1`` → "没有更多结果"；否则 → "没有搜索结果"。
    """
    if total is None:
        total = get_source_total(source_id, sources=sources)
    count = len(results)
    if total > 0:
        total_pages = max(1, math.ceil(total / page_size))
        has_more = page < total_pages
    else:
        total_pages = 0
        has_more = count >= page_size
    if count:
        status = STATUS_RESULTS
    elif page > 1:
        status = STATUS_NO_MORE
    else:
        status = STATUS_EMPTY
    return SearchOutcome(total_pages=total_pages, has_more=has_more, status=status, count=count)


@dataclass
class PagerPlan:
    """分页栏的页码集合与翻页按钮可用性（原文 `_music_rebuild_pager`）。"""

    pages: List[int]
    current_page: int
    prev_enabled: bool
    next_enabled: bool


def pager_plan(
    *, current_page: int, total_pages: int, has_more: bool, busy: bool
) -> PagerPlan:
    """重建分页栏的判定：

    * 音源提供总数 → 按总页数生成全部页码；
    * 否则满页时**推测下一页存在**，只画到 ``current + 1``；
    * 都不满足 → 只画当前页；
    * 上一页在 ``cur > 1 且不忙`` 时可用；下一页在 ``has_more 且不忙`` 时可用。
    """
    if total_pages > 0:
        last = total_pages
    elif has_more:
        last = current_page + 1
    else:
        last = current_page
    return PagerPlan(
        pages=list(range(1, last + 1)),
        current_page=current_page,
        prev_enabled=current_page > 1 and not busy,
        next_enabled=has_more and not busy,
    )


@dataclass
class SearchRowPlan:
    """搜索结果一行的**取值**（原文 `_music_add_search_row` 里"算文本"的那几行）。

    控件本身（Frame / Label / Button、绑定、主题登记）全部留在界面；
    这里只把要显示什么算出来，顺带把两个 i18n key 交回去（文案函数只能在界面调）。
    """

    index_text: str
    is_original: bool
    name_text: str
    display: str
    #: 原唱徽章的 i18n key；非原唱时为 ""
    tag_key: str = ""
    #: 徽章文案参数（仅 KEY_ORIGINAL_TAG_WITH_NAME 时非空）
    tag_params: Dict[str, Any] = field(default_factory=dict)
    dur_text: str = ""
    play_text: str = ""
    source_text: str = ""


def search_row_plan(idx: int, info: Any) -> SearchRowPlan:
    """搜索结果行的显示取值（逐条照搬原文 `_music_add_search_row`）。

    * 序号：``idx + 1``；
    * 歌名超过 35 字 → 保留 33 字 + "..."；
    * ``歌名 - 歌手``（无歌手时只有歌名）；
    * 原唱徽章：有原曲名时带名字，否则用通用文案（两个 key 分别返回）；
    * 时长：``format_time(interval)``，interval 为 0/None 时为 ""（不显示）；
    * 播放量：``format_play_count(play_count)``，音源未提供时为 ""（不显示）；
    * 音源标签：``source.upper()``。
    """
    name = info.name
    name_text = name if len(name) <= ROW_NAME_MAX else name[:ROW_NAME_KEEP] + "..."
    display = f"{name_text} - {info.singer}" if info.singer else name_text
    is_original = bool(info.is_original)
    tag_key = ""
    tag_params: Dict[str, Any] = {}
    if is_original:
        if info.original_name:
            tag_key = KEY_ORIGINAL_TAG_WITH_NAME
            tag_params = {"name": info.original_name}
        else:
            tag_key = KEY_ORIGINAL_TAG
    return SearchRowPlan(
        index_text=str(idx + 1),
        is_original=is_original,
        name_text=name_text,
        display=display,
        tag_key=tag_key,
        tag_params=tag_params,
        dur_text=music_audio.format_time(info.interval) if info.interval else "",
        play_text=music_audio.format_play_count(info.play_count),
        source_text=info.source.upper(),
    )


def backfill_targets_current(results: Any, current_results: Any) -> bool:
    """百度百科原唱回填的**身份**判定（原文 `if results is not self._music_search_results`）。

    必须是**同一个对象**才算"结果仍是当前展示的搜索页"：回填是原地改 `OnlineMusicInfo`
    字段，列表被重新赋值（新的一次搜索）后对象身份就变了。
    """
    return results is current_results


def stream_ready_action(seq: int, current_seq: int, temp_path: Any) -> str:
    """取流完成回调的动作判定（原文 `_music_on_stream_ready`）。

    请求序号不等于当前序号 → `STREAM_STALE`（用户已切换播放目标，丢弃过期结果）；
    有临时文件 → `STREAM_PLAY`；否则 → `STREAM_FAILED`。
    """
    if seq != current_seq:
        return STREAM_STALE
    return STREAM_PLAY if temp_path else STREAM_FAILED


def resolve_auto_quality(
    online_info: Any, *, sources: Optional[Mapping[str, Any]] = None
) -> str:
    """自动音质：取当前账号在该歌曲上可用的最高音质。

    音源搜索结果的 types/maxbr 由服务器按当前账号返回（免费用户最高 128k、
    音乐包 320k、黑胶 VIP 无损、SVIP 母带，登录与否直接影响可用音质），
    自动模式即从高到低选第一个可用的。

    `online_info` 为 None 或音源不存在 → `128k`；音源抛异常 → 记 debug 后 `128k`。
    """
    if online_info is None:
        return FALLBACK_QUALITY
    src = get_source(online_info.source, sources=sources)
    if src is None:
        return FALLBACK_QUALITY
    try:
        return src.get_best_quality(online_info, AUTO_QUALITY_PREFERRED)
    except Exception as e:
        logger.debug(f"自动音质解析失败，回退 128k: {e}")
        return FALLBACK_QUALITY


def resolve_auto_quality_with_backfill(
    online_info: Any, *, sources: Optional[Mapping[str, Any]] = None
) -> Tuple[Any, str]:
    """后台解析自动音质（含音质信息补齐）。

    歌单/播放历史恢复的歌曲由 PlaylistSong 重建，旧数据可能缺少可用音质信息
    （types/_types）。此时先向音源重新搜索同款歌（按 songmid 匹配）补齐，再取最高
    可用音质 —— 否则自动解析只能回退 128k，且 URL 获取缺少 hash 等详情。

    Returns:
        (补齐后的歌曲信息, 解析出的音质档位)
    """
    info = online_info
    if info is None:
        return info, FALLBACK_QUALITY
    if info.types:
        return info, resolve_auto_quality(info, sources=sources)
    src = get_source(info.source, sources=sources)
    if src is not None:
        try:
            keyword = f"{info.name} {info.singer}".strip() or info.name
            items = src.search(keyword, page=1, limit=BACKFILL_SEARCH_LIMIT)
            for it in items or []:
                if it.songmid == info.songmid and it.types:
                    info = it
                    break
        except Exception as e:
            logger.debug(f"自动音质信息补齐失败 [{info.source}]: {e}")
    return info, resolve_auto_quality(info, sources=sources)


def fetch_cover_bytes(url: str, *, http_get: Optional[Any] = None,
                      timeout: int = 10, user_agent: str = "Mozilla/5.0") -> Optional[bytes]:
    """在线封面图取字节（原文 `_fetch_and_display_online_cover` 的线程体）。

    ``HTTP 200`` → 返回 ``resp.content``；否则返回 None（原文只有 200 才回调界面）；
    任何异常都吞掉返回 None（原文 `except Exception: pass`）。

    ``http_get`` 为 None 时读**调用时刻**的 ``requests.get``（模块级全局名），
    所以 ``monkeypatch.setattr(music_online.requests, "get", fake)`` 或直接传
    ``http_get=fake`` 都生效。
    """
    try:
        getter = requests.get if http_get is None else http_get
        resp = getter(url, timeout=timeout, headers={"User-Agent": user_agent})
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


@dataclass
class NowPlayingPlan:
    """正在播放区的显示取值（原文 `_update_now_playing_info` 里"算文本"的那几段）。

    形态由调用方按 `mode` 分派；控件写入、SMTC 上报、复制按钮状态刷新都留在界面。
    """

    mode: str
    quality_text: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0
    sub_text: str = ""
    mini_text: str = ""
    #: 在线歌曲的封面 URL（有则界面异步取图）
    cover_url: str = ""
    #: 本地歌曲的内嵌封面字节（有则界面同步显示）
    cover_bytes: Optional[bytes] = None


def now_playing_plan(
    *,
    online: bool,
    online_info: Any = None,
    current_quality: str = "",
    local_path: str = "",
    local_meta: Optional[Dict[str, Any]] = None,
) -> NowPlayingPlan:
    """正在播放区的取值规则（逐条照搬原文 `_update_now_playing_info`）。

    * 音质标签：在线歌曲按**实际获取到的音质档位**，本地歌曲按文件真实码率；
    * 在线分支：标题取歌名、副标题 ``歌手 - 专辑``（无歌手时只有专辑）、
      mini 标题在音质文案非空时拼 ``标题 · 音质``；封面走 URL 异步取；
    * 空分支：没有任何当前文件 → 标题/歌手/专辑清空，时长 0；
    * 本地分支：标题缺省取文件名，专辑/歌手取标签，封面取内嵌字节。
    """
    if online:
        quality_text = music_audio.format_online_quality(current_quality or "")
    else:
        quality_text = (
            music_audio.format_local_quality(local_meta, local_path) if local_path else ""
        )

    if online:
        oi = online_info
        title = oi.name
        artist = oi.singer or ""
        album = oi.album_name or ""
        duration = oi.interval
        sub_text = artist
        if album:
            sub_text = f"{artist} - {album}" if artist else album
        return NowPlayingPlan(
            mode=NOW_PLAYING_ONLINE,
            quality_text=quality_text,
            title=title,
            artist=artist,
            album=album,
            duration=duration,
            sub_text=sub_text,
            mini_text=f"{title} · {quality_text}" if quality_text else title,
            cover_url=oi.img or "",
        )

    if not local_path:
        return NowPlayingPlan(mode=NOW_PLAYING_EMPTY, quality_text=quality_text)

    meta = local_meta or {}
    title = meta.get("title", os.path.basename(local_path))
    artist = meta.get("artist", "")
    album = meta.get("album", "")
    duration = meta.get("duration", 0)
    sub_text = artist
    if album:
        sub_text = f"{artist} - {album}" if artist else album
    cover_bytes = meta.get("cover_data") if meta.get("has_cover") else None
    return NowPlayingPlan(
        mode=NOW_PLAYING_LOCAL,
        quality_text=quality_text,
        title=title,
        artist=artist,
        album=album,
        duration=duration,
        sub_text=sub_text,
        mini_text=f"{title} · {quality_text}" if quality_text else title,
        cover_bytes=cover_bytes,
    )


__all__ = [
    "AUTO_QUALITY_PREFERRED",
    "BACKFILL_SEARCH_LIMIT",
    "DEFAULT_SEARCH_PAGE_SIZE",
    "FALLBACK_QUALITY",
    "FIRST_SEARCH_PAGE",
    "KEY_ORIGINAL_TAG",
    "KEY_ORIGINAL_TAG_WITH_NAME",
    "NOW_PLAYING_EMPTY",
    "NOW_PLAYING_LOCAL",
    "NOW_PLAYING_ONLINE",
    "NowPlayingPlan",
    "PagerPlan",
    "ROW_NAME_KEEP",
    "ROW_NAME_MAX",
    "STATUS_EMPTY",
    "STATUS_NO_MORE",
    "STATUS_RESULTS",
    "STREAM_FAILED",
    "STREAM_PLAY",
    "STREAM_STALE",
    "SearchOutcome",
    "SearchPlan",
    "SearchRowPlan",
    "backfill_targets_current",
    "fetch_cover_bytes",
    "get_source",
    "get_source_total",
    "go_page_target",
    "neighbor_page",
    "normalize_keyword",
    "now_playing_plan",
    "pager_plan",
    "plan_search",
    "resolve_auto_quality",
    "resolve_auto_quality_with_backfill",
    "run_source_search",
    "search_page_size",
    "search_row_plan",
    "stream_ready_action",
    "summarize_search",
]
