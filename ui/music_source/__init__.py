"""音乐源模块 - 在线音乐搜索与播放
从 LX Music (洛雪音乐助手) 迁移而来，支持多音源搜索与流媒体播放。

支持的音源:
    - kw: 酷我音乐
    - kg: 酷狗音乐
    - mg: 咪咕音乐
    - tx: QQ音乐
    - wy: 网易云音乐

使用示例:
    from ui.music_source import MUSIC_SOURCES, search_all

    results = search_all("七里香", limit=20)
"""

from ui.music_source.base import BaseMusicSource, MusicInfo, QualityLevel
from ui.music_source.kg import KuGouMusicSource
from ui.music_source.kw import KuWoMusicSource
from ui.music_source.mg import MiGuMusicSource
from ui.music_source.tx import QQMusicSource
from ui.music_source.wy import NetEaseMusicSource

MUSIC_SOURCES = {
    "kw": KuWoMusicSource(),
    "kg": KuGouMusicSource(),
    "mg": MiGuMusicSource(),
    "tx": QQMusicSource(),
    "wy": NetEaseMusicSource(),
}

SOURCE_META = [
    {"id": "kw", "name": "酷我音乐"},
    {"id": "kg", "name": "酷狗音乐"},
    {"id": "mg", "name": "咪咕音乐"},
    {"id": "tx", "name": "QQ音乐"},
    {"id": "wy", "name": "网易云音乐"},
]


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


__all__ = [
    "BaseMusicSource",
    "MusicInfo",
    "QualityLevel",
    "KuWoMusicSource",
    "KuGouMusicSource",
    "MiGuMusicSource",
    "QQMusicSource",
    "NetEaseMusicSource",
    "MUSIC_SOURCES",
    "SOURCE_META",
    "search_all",
]
