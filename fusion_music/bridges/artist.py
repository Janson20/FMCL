"""歌手页控制器：点任意位置的歌手名打开，展示歌手信息与热门歌曲。

歌手名可能来自任何音源（QQ / 酷我 / 酷狗 / 咪咕 / 本地文件标签），它们都没有
网易云的歌手 id，所以这里统一**按名字定位**歌手：先搜同名歌手，再用详情接口
取热门歌曲。搜索置顶卡片那种已经拿到 id 的入口直接走 id，省一次搜索。

通用部分（开关状态、请求序号、找不到时的提示）在 :mod:`app.bridges.detail`。
"""

from __future__ import annotations

from typing import Dict

from ..sources import netease
from .detail import DetailPageController

class ArtistController(DetailPageController):
    """歌手详情页。"""

    noun = "歌手"
    list_key = "hot_songs"

    def _fetch(self, page_id: str, name: str) -> Dict:
        return netease.artist_page(page_id, name)
