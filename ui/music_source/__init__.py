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
from ui.music_source.bili import BiliBiliMusicSource, RiskControlError
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
# 兜底音质尝试顺序（从高到低：各音源对不可用音质返回 None，
# 顺序决定最终拿到的音质，必须高音质在前，避免默认 128k）
RESOLVE_QUALITY_ORDER = ["flac", "320k", "128k"]


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
        quality: 期望音质（"auto" 表示自动选择跨源可用的最高音质；
                 具体档位则用户档位优先，其余按高到低回退）
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
        except RiskControlError as e:
            # 风控需用户交互验证，先跳过该源（UI 层会弹验证码并在完成后重试）
            logger.debug(f"[resolve] {source_id} 触发风控: {e}")
            return None
        except Exception as e:
            # 单源失败属兜底常态，降为 debug 避免刷屏（外层有汇总日志）
            logger.debug(f"[resolve] {source_id} 搜索失败: {e}")
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
                    logger.debug(f"[resolve] {source_id} 获取URL失败 [{cand.name}]: {e}")
                    url = None
                if url:
                    return (cand, url)
        return None

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=len(source_ids))
    try:
        futures = [executor.submit(_try_source, sid) for sid in source_ids]
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
            except Exception as e:
                logger.debug(f"[resolve] 兜底任务异常: {e}")
                result = None
            if result:
                # 找到可用音源立即返回；慢源线程仍可能阻塞在重试中，
                # 不能等它们（shutdown(wait=True) 会拖慢整个兜底流程）
                executor.shutdown(wait=False, cancel_futures=True)
                return result
    finally:
        executor.shutdown(wait=False)
    return None


def _quality_attempt_order(preferred: str) -> List[str]:
    """生成兜底音质尝试顺序

    - "auto"（自动选择）：从高到低尝试 flac -> 320k -> 128k
    - 具体档位：用户档位优先，其余按高到低回退
    （各音源对不可用音质返回 None，顺序即最终拿到的音质，
    高音质必须在前，否则跨源兜底会默认命中 128k）
    """
    if preferred == "auto":
        return list(RESOLVE_QUALITY_ORDER)
    order = [q for q in RESOLVE_QUALITY_ORDER if q != preferred]
    return [preferred] + order if preferred in RESOLVE_QUALITY_ORDER else order


# ═══════════════ 网易云账号登录（VIP 播放/歌词） ═══════════════
#
# 扫码登录流程与 YesPlayMusic 一致（官方 weapi 接口），登录后网易云音源
# 可播放 VIP 歌曲并获取 VIP 歌词。登录 Cookie 由 UI 层加密持久化，
# 启动时通过 wy_apply_cookie 恢复到单例音源会话。

def _wy_source() -> Optional[NetEaseMusicSource]:
    """获取网易云音源单例（登录接口操作对象）"""
    return MUSIC_SOURCES.get("wy")


def wy_login_qr_key() -> Optional[str]:
    """获取网易云扫码登录 unikey（二维码内容需要的内容）"""
    src = _wy_source()
    if src is None:
        return None
    return src.login_qr_key()


def wy_login_qr_check(key: str) -> dict:
    """查询网易云扫码登录状态: 800 过期 / 801 等待 / 802 待确认 / 803 成功（含 cookie）"""
    src = _wy_source()
    if src is None:
        return {}
    return src.login_qr_check(key)


def wy_apply_cookie(cookie_str: str) -> bool:
    """将登录 Cookie 字符串应用到网易云音源会话

    Args:
        cookie_str: Set-Cookie 格式字符串（含 'HTTPOnly' 等属性标记亦可）
    Returns:
        是否应用成功（含有效 cookie 项）
    """
    src = _wy_source()
    if src is None or not cookie_str:
        return False
    try:
        src.apply_cookie_str(cookie_str)
        return True
    except Exception as e:
        logger.warning(f"应用网易云登录 Cookie 失败: {e}")
        return False


def wy_get_cookie_str() -> str:
    """导出当前网易云音源会话中的音乐域名 cookie 字符串"""
    src = _wy_source()
    if src is None:
        return ""
    return src.get_cookie_str()


def wy_clear_cookie() -> None:
    """清除网易云音源会话的登录 Cookie（保留基础 cookie）"""
    src = _wy_source()
    if src is not None:
        try:
            src.clear_cookies()
        except Exception as e:
            logger.warning(f"清除网易云登录 Cookie 失败: {e}")


def wy_is_logged_in() -> bool:
    """网易云音源当前是否处于登录状态"""
    src = _wy_source()
    if src is None:
        return False
    try:
        return src.is_logged_in()
    except Exception:
        return False


def wy_fetch_profile() -> Optional[dict]:
    """获取网易云当前登录用户信息，失败返回 None

    Returns:
        {"nickname": str, "avatar_url": str, "user_id": int}
    """
    src = _wy_source()
    if src is None:
        return None
    return src.fetch_login_profile()


def wy_get_user_playlists() -> Optional[List[dict]]:
    """获取网易云当前登录用户创建的歌单列表（含「我喜欢的音乐」）

    Returns:
        [{"id": str, "name": str, "track_count": int, "cover_url": str}, ...]
        未登录/失败返回 None
    """
    src = _wy_source()
    if src is None:
        return None
    return src.get_user_playlists()


def wy_get_playlist_tracks(playlist_id: str) -> Optional[List[MusicInfo]]:
    """获取网易云歌单完整歌曲列表（需登录，失败返回 None）"""
    src = _wy_source()
    if src is None:
        return None
    return src.get_playlist_tracks(playlist_id)


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
