"""在线音源注册表与跨源兜底。

本包的 ``base.py`` / ``utils.py`` / ``wy.py`` / ``kw.py`` / ``kg.py`` / ``mg.py`` /
``tx.py`` / ``bili.py`` 直接取自 FMCL（GPL-3.0-only）的 ``ui/music_source/``，
仅把包内 import 改为相对导入，加密与解析逻辑保持逐字不变。

与 FMCL 的差异（有意为之）：
* **惰性实例化**：FMCL 在 import 时构造全部 6 个音源，而 ``BiliBiliMusicSource``
  的构造函数会同步请求 ``https://www.bilibili.com``（timeout=12），导致启动卡顿。
  这里改为按需创建。
* **搜索顺序稳定**：``search_all`` 按 :data:`SOURCE_META` 顺序返回，便于 UI 稳定分页。
"""

from __future__ import annotations

import concurrent.futures
import logging
import threading
from typing import Dict, Iterable, List, Optional, Tuple

from .base import BaseMusicSource, MusicInfo, QualityLevel, duration_matches

logger = logging.getLogger("fusion.sources")

# ── 音源元数据（顺序即 UI 展示顺序，网易云优先）────────────────
SOURCE_META: List[Dict[str, str]] = [
    {"id": "wy", "name": "网易云音乐", "short": "网易云"},
    {"id": "tx", "name": "QQ音乐", "short": "QQ"},
    {"id": "kw", "name": "酷我音乐", "short": "酷我"},
    {"id": "kg", "name": "酷狗音乐", "short": "酷狗"},
    {"id": "mg", "name": "咪咕音乐", "short": "咪咕"},
    {"id": "bili", "name": "哔哩哔哩", "short": "B站"},
]

SOURCE_NAMES: Dict[str, str] = {m["id"]: m["name"] for m in SOURCE_META}

# 默认参与「在线搜索主界面」的音源（bili 只参与跨源兜底，与 FMCL 一致）
DEFAULT_SEARCH_SOURCES: Tuple[str, ...] = ("wy", "tx", "kw", "kg", "mg")
FALLBACK_ONLY_SOURCES: Tuple[str, ...] = ("bili",)

# 跨源兜底：每个音源搜索的候选条数
RESOLVE_SEARCH_LIMIT = 20
# 兜底音质尝试顺序（高音质在前，否则会默认命中 128k）。
# Hi-Res 母带要排在最前：设置里能选「Hi-Res 母带」、"auto" 也应当尽力取最高，
# 缺了它这一档就永远试不到（会静默退成无损）。
RESOLVE_QUALITY_ORDER = ["flac24bit", "flac", "320k", "128k"]

_BUILDERS = {
    # 网易云用 app/sources/netease.py 里的子类：它修正了上游把 vipType 读在
    # profile 上的问题（实际在 account 上，导致 VIP/SVIP 显示成普通用户）
    "wy": ("netease", "NetEaseSource"),
    "tx": ("tx", "QQMusicSource"),
    "kw": ("kw", "KuWoMusicSource"),
    "kg": ("kg", "KuGouMusicSource"),
    "mg": ("mg", "MiGuMusicSource"),
    "bili": ("bili", "BiliBiliMusicSource"),
}

_instances: Dict[str, BaseMusicSource] = {}
_lock = threading.RLock()


def get_source(source_id: str) -> Optional[BaseMusicSource]:
    """按需创建并缓存音源实例（绝不重复实例化）。"""
    with _lock:
        src = _instances.get(source_id)
        if src is not None:
            return src
        spec = _BUILDERS.get(source_id)
        if spec is None:
            return None
        module_name, class_name = spec
        try:
            import importlib

            module = importlib.import_module(f".{module_name}", __package__)
            cls = getattr(module, class_name)
            src = cls()
            _instances[source_id] = src
            logger.debug("音源已加载: %s", source_id)
            return src
        except Exception as e:
            logger.warning("音源 %s 加载失败: %s", source_id, e)
            return None


def available_sources() -> List[str]:
    return [sid for sid in _BUILDERS]


def is_source_available(source_id: str) -> bool:
    return get_source(source_id) is not None


def search_one(
    source_id: str, keyword: str, page: int = 1, limit: int = 30
) -> List[MusicInfo]:
    """单音源搜索，异常一律吞掉返回空列表。"""
    src = get_source(source_id)
    if src is None:
        return []
    try:
        page_size = int(src.limits.get("search", limit) or limit)
        return src.search(keyword, page, min(limit, page_size)) or []
    except Exception as e:
        logger.debug("[%s] 搜索失败: %s", source_id, e)
        return []


def search_all(
    keyword: str,
    page: int = 1,
    limit: int = 30,
    source_ids: Optional[Iterable[str]] = None,
) -> List[Dict]:
    """并发搜索多个音源。

    Returns:
        ``[{"source": "wy", "results": [MusicInfo, ...], "total": int}, ...]``
        顺序与 :data:`SOURCE_META` 一致。
    """
    if not keyword or not keyword.strip():
        return []
    ids = list(source_ids) if source_ids else list(DEFAULT_SEARCH_SOURCES)
    ids = [sid for sid in ids if sid in _BUILDERS]
    if not ids:
        return []

    out: Dict[str, List[MusicInfo]] = {}
    totals: Dict[str, int] = {}

    def _run(sid: str):
        items = search_one(sid, keyword, page, limit)
        src = get_source(sid)
        total = int(getattr(src, "last_search_total", 0) or 0) if src else 0
        return sid, items, total

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, len(ids))) as ex:
        for fut in concurrent.futures.as_completed([ex.submit(_run, s) for s in ids]):
            try:
                sid, items, total = fut.result()
            except Exception:
                continue
            if items:
                out[sid] = items
                totals[sid] = total

    ordered = [m["id"] for m in SOURCE_META if m["id"] in out]
    return [{"source": sid, "results": out[sid], "total": totals.get(sid, 0)} for sid in ordered]


def quality_order(
    info: MusicInfo, quality: str = "320k", source_id: Optional[str] = None
) -> List[str]:
    """这首歌在该音源上应该按什么顺序试音质（首项 = 首选档位）。

    单独暴露出来是为了让调用方**自己掌握降级过程**：只有它才知道最后真正取到
    地址的是哪一档，界面上的音质标签才不会写成"我想要的那一档"。
    """
    src = get_source(source_id or info.source)
    if src is None:
        return _quality_attempt_order(quality)
    try:
        best = src.get_best_quality(info, quality)
    except Exception:
        best = quality
    return _quality_attempt_order(best)


def get_music_url_exact(
    info: MusicInfo, quality: str = "320k", source_id: Optional[str] = None
) -> Optional[str]:
    """只按**指定档位**取地址，不做降级（降级交给 :func:`quality_order` 的调用方）。"""
    src = get_source(source_id or info.source)
    if src is None:
        return None
    try:
        return src.get_music_url(info, quality)
    except Exception as e:
        logger.debug("[%s] 取播放地址失败 (%s): %s", info.source, quality, e)
        return None


def get_music_url(
    info: MusicInfo, quality: str = "320k", source_id: Optional[str] = None
) -> Optional[str]:
    """获取指定歌曲在指定音源上的播放 URL（内部按档位逐级降级）。"""
    for q in quality_order(info, quality, source_id):
        url = get_music_url_exact(info, q, source_id)
        if url:
            return url
    return None


def get_lyric(info: MusicInfo, source_id: Optional[str] = None) -> Optional[str]:
    src = get_source(source_id or info.source)
    if src is None:
        return None
    try:
        return src.get_lyric(info)
    except Exception as e:
        logger.debug("[%s] 取歌词失败: %s", info.source, e)
        return None


def get_pic_url(info: MusicInfo, source_id: Optional[str] = None) -> Optional[str]:
    src = get_source(source_id or info.source)
    if src is None:
        return None
    try:
        return src.get_pic_url(info)
    except Exception as e:
        logger.debug("[%s] 取封面失败: %s", info.source, e)
        return None


def download_headers(source_id: str) -> Dict[str, str]:
    src = get_source(source_id)
    if src is None:
        return {}
    try:
        return dict(src.get_download_headers() or {})
    except Exception:
        return {}


def download_cookies(source_id: str) -> Dict[str, str]:
    src = get_source(source_id)
    if src is None:
        return {}
    try:
        return dict(src.get_download_cookies() or {})
    except Exception:
        return {}


def _quality_attempt_order(preferred: str) -> List[str]:
    """生成音质尝试顺序（高音质在前，避免兜底命中 128k）。"""
    if preferred == "auto":
        return list(RESOLVE_QUALITY_ORDER)
    if preferred in RESOLVE_QUALITY_ORDER:
        return [preferred] + [q for q in RESOLVE_QUALITY_ORDER if q != preferred]
    return list(RESOLVE_QUALITY_ORDER)


def resolve_track(
    info: MusicInfo,
    quality: str = "320k",
    excluded_sources: Optional[Iterable[str]] = None,
    limit: int = RESOLVE_SEARCH_LIMIT,
) -> Optional[Tuple[MusicInfo, str, str]]:
    """跨源兜底：在其它音源中搜索同款歌并取回可播放 URL。

    返回 ``(MusicInfo, url, 实际音质档位)``；实际档位是**取到地址的那一档**，
    不是请求的那一档（调用方要拿它显示音质）。

    流程（与 FMCL 完全一致）:
        1. 排除 ``info.source``，对其余音源并发搜索 ``"歌名 歌手"``
        2. 候选按时长过滤（相差 > 15 秒视为翻唱/伴奏/Live 而剔除）
        3. 剩余候选按时长差升序，逐个尝试 ``get_music_url``（音质按偏好降级）
        4. 第一个成功即返回，全部失败返回 ``None``
    """
    if info is None or not info.name:
        return None

    excluded = set(excluded_sources or [])
    excluded.add(info.source)
    source_ids = [sid for sid in _BUILDERS if sid not in excluded]
    if not source_ids:
        return None

    keyword = f"{info.name} {info.singer}".strip() or info.name
    order = _quality_attempt_order(quality)

    def _try_source(source_id: str):
        src = get_source(source_id)
        if src is None:
            return None
        try:
            items = src.search(keyword, page=1, limit=limit)
        except Exception as e:
            logger.debug("[resolve] %s 搜索失败: %s", source_id, e)
            return None
        if not items:
            return None
        candidates = [i for i in items if duration_matches(i, info.interval)]
        if not candidates:
            return None
        candidates.sort(key=lambda i: abs(i.interval - info.interval))
        for cand in candidates:
            for q in order:
                try:
                    url = src.get_music_url(cand, q)
                except Exception as e:
                    logger.debug("[resolve] %s 取地址失败 [%s]: %s", source_id, cand.name, e)
                    url = None
                if url:
                    return (cand, url, q)
        return None

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(source_ids))
    try:
        futures = [executor.submit(_try_source, sid) for sid in source_ids]
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
            except Exception as e:
                logger.debug("[resolve] 兜底任务异常: %s", e)
                result = None
            if result:
                executor.shutdown(wait=False, cancel_futures=True)
                logger.info("[resolve] 兜底成功: %s -> %s", info, result[0])
                return result
    finally:
        executor.shutdown(wait=False)
    logger.info("[resolve] 兜底失败: %s", info)
    return None


# ══════════════════ 网易云账号（VIP 播放 / 歌词 / 歌单） ══════════════════


def wy_source():
    """网易云音源单例（登录相关操作的入口）。"""
    return get_source("wy")


def wy_login_qr_key() -> Optional[str]:
    src = wy_source()
    return src.login_qr_key() if src else None


def wy_login_qr_check(key: str) -> dict:
    src = wy_source()
    return src.login_qr_check(key) if src else {}


def wy_apply_cookie(cookie_str: str) -> bool:
    src = wy_source()
    if src is None or not cookie_str:
        return False
    try:
        src.apply_cookie_str(cookie_str)
        return True
    except Exception as e:
        logger.warning("应用网易云登录 Cookie 失败: %s", e)
        return False


def wy_get_cookie_str() -> str:
    src = wy_source()
    return src.get_cookie_str() if src else ""


def wy_clear_cookie() -> None:
    src = wy_source()
    if src is not None:
        try:
            src.clear_cookies()
        except Exception as e:
            logger.warning("清除网易云登录 Cookie 失败: %s", e)


def wy_is_logged_in() -> bool:
    src = wy_source()
    if src is None:
        return False
    try:
        return src.is_logged_in()
    except Exception:
        return False


def wy_fetch_profile() -> Optional[dict]:
    src = wy_source()
    return src.fetch_login_profile() if src else None


def wy_get_user_playlists() -> Optional[List[dict]]:
    src = wy_source()
    return src.get_user_playlists() if src else None


def wy_get_playlist_tracks(playlist_id: str) -> Optional[List[MusicInfo]]:
    src = wy_source()
    return src.get_playlist_tracks(playlist_id) if src else None


__all__ = [
    "BaseMusicSource",
    "MusicInfo",
    "QualityLevel",
    "duration_matches",
    "SOURCE_META",
    "SOURCE_NAMES",
    "DEFAULT_SEARCH_SOURCES",
    "get_source",
    "available_sources",
    "is_source_available",
    "search_one",
    "search_all",
    "get_music_url",
    "get_music_url_exact",
    "quality_order",
    "get_lyric",
    "get_pic_url",
    "download_headers",
    "download_cookies",
    "resolve_track",
    "wy_source",
    "wy_login_qr_key",
    "wy_login_qr_check",
    "wy_apply_cookie",
    "wy_get_cookie_str",
    "wy_clear_cookie",
    "wy_is_logged_in",
    "wy_fetch_profile",
    "wy_get_user_playlists",
    "wy_get_playlist_tracks",
]
