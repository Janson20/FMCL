"""音乐源模块 - 在线音乐搜索与播放
从 LX Music (洛雪音乐助手) 迁移而来，支持多音源搜索与流媒体播放。

支持的音源:
    - kw: 酷我音乐
    - kg: 酷狗音乐
    - mg: 咪咕音乐
    - tx: QQ音乐
    - wy: 网易云音乐
    - bili: 哔哩哔哩（仅参与跨源兜底，不显示在在线搜索主界面）

跨源兜底 (resolve_track):
    当前音源无法播放（VIP/无版权等）时，自动在其它音源搜索同款歌并获取可播放
    URL，类似 YesPlayMusic 的 UnblockNeteaseMusic 思路：不破解，而是换平台找同曲。

使用示例:
    from ui.music_source import MUSIC_SOURCES, search_all, resolve_track

    results = search_all("七里香", limit=20)
    fallback = resolve_track(results[0][1][0], quality="320k")
"""

import logging
from typing import Iterable, List, Optional, Tuple

from ui.music_source.base import BaseMusicSource, MusicInfo, QualityLevel, duration_matches
from ui.music_source.bili import BiliBiliMusicSource
from ui.music_source.kg import KuGouMusicSource
from ui.music_source.kw import KuWoMusicSource
from ui.music_source.mg import MiGuMusicSource
from ui.music_source.tx import QQMusicSource
from ui.music_source.wy import NetEaseMusicSource

logger = logging.getLogger("music_source")

MUSIC_SOURCES = {
    "kw": KuWoMusicSource(),
    "kg": KuGouMusicSource(),
    "mg": MiGuMusicSource(),
    "tx": QQMusicSource(),
    "wy": NetEaseMusicSource(),
    "bili": BiliBiliMusicSource(),
}

SOURCE_META = [
    {"id": "kw", "name": "酷我音乐"},
    {"id": "kg", "name": "酷狗音乐"},
    {"id": "mg", "name": "咪咕音乐"},
    {"id": "tx", "name": "QQ音乐"},
    {"id": "wy", "name": "网易云音乐"},
]

# 跨源兜底：每个音源搜索的候选条数
RESOLVE_SEARCH_LIMIT = 20
# 兜底音质尝试顺序（首选置顶，其余按此回退）
RESOLVE_QUALITY_ORDER = ["128k", "320k", "flac"]


def search_all(keyword: str, page: int = 1, limit: int = 30):
    """并发搜索所有音源，返回各源结果列表。

    Args:
        keyword: 搜索关键词
        page: 页码 (从1开始)
        limit: 每页数量

    Returns:
        list of dict: [{"source": "kw", "results": [...]}, ...]
    """
    import concurrent.futures

    results = []

    def _search_one(source_id):
        try:
            src = MUSIC_SOURCES.get(source_id)
            if not src:
                return None
            items = src.search(keyword, page, limit)
            return {"source": source_id, "results": items}
        except Exception:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(_search_one, s["id"]): s["id"] for s in SOURCE_META}
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                if result and result.get("results"):
                    results.append(result)
            except Exception:
                pass

    return results


def resolve_track(
    info: MusicInfo,
    quality: str = "128k",
    excluded_sources: Optional[Iterable[str]] = None,
    limit: int = RESOLVE_SEARCH_LIMIT,
) -> Optional[Tuple[MusicInfo, str]]:
    """跨源兜底解析：在其它音源中搜索同款歌并获取可播放 URL（YesPlayMusic UNM 风格）。

    流程:
        1. 排除 info.source 自身，对其余音源并发搜索 "歌名 歌手"
        2. 候选按时长校验过滤（与 info.interval 相差 > DURATION_TOLERANCE 秒的剔除，
           排除翻唱/伴奏/Live 等版本）
        3. 剩余候选按时长差升序，逐个尝试 get_music_url（音质按用户偏好 + 降级顺序）
        4. 第一个成功即返回，全部失败返回 None

    Args:
        info: 原音源播放失败的歌曲信息
        quality: 用户期望音质
        excluded_sources: 额外排除的音源 id 列表（默认仅排除 info.source）
        limit: 每个音源的候选搜索条数

    Returns:
        (匹配到的歌曲信息, 可播放URL) 或 None（所有音源均失败）
    """
    import concurrent.futures

    if info is None or not info.name:
        return None

    excluded = set(excluded_sources or [])
    excluded.add(info.source)
    source_ids = [sid for sid in MUSIC_SOURCES if sid not in excluded]
    if not source_ids:
        return None

    keyword = f"{info.name} {info.singer}".strip() or info.name
    quality_order = _quality_attempt_order(quality)

    def _try_source(source_id):
        src = MUSIC_SOURCES.get(source_id)
        if src is None:
            return None
        try:
            items = src.search(keyword, page=1, limit=limit)
        except Exception as e:
            logger.warning(f"[resolve] {source_id} 搜索失败: {e}")
            return None
        if not items:
            return None
        candidates = [i for i in items if duration_matches(i, info.interval)]
        if not candidates:
            return None
        candidates.sort(key=lambda i: abs(i.interval - info.interval))
        for cand in candidates:
            for q in quality_order:
                try:
                    url = src.get_music_url(cand, q)
                except Exception as e:
                    logger.warning(f"[resolve] {source_id} 获取URL失败 [{cand.name}]: {e}")
                    url = None
                if url:
                    return (cand, url)
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(source_ids)) as executor:
        futures = [executor.submit(_try_source, sid) for sid in source_ids]
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
            except Exception as e:
                logger.warning(f"[resolve] 兜底任务异常: {e}")
                result = None
            if result:
                return result
    return None


def _quality_attempt_order(preferred: str) -> List[str]:
    """生成兜底音质尝试顺序：用户偏好置顶，其余按 128k/320k/flac 顺序回退"""
    order = [q for q in RESOLVE_QUALITY_ORDER if q != preferred]
    return [preferred] + order if preferred in RESOLVE_QUALITY_ORDER else order


__all__ = [
    "BaseMusicSource",
    "MusicInfo",
    "QualityLevel",
    "duration_matches",
    "KuWoMusicSource",
    "KuGouMusicSource",
    "MiGuMusicSource",
    "QQMusicSource",
    "NetEaseMusicSource",
    "BiliBiliMusicSource",
    "MUSIC_SOURCES",
    "SOURCE_META",
    "search_all",
    "resolve_track",
]
