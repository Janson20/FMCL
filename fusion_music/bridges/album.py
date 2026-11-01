"""专辑页控制器：点任意位置的专辑名打开，展示专辑信息与曲目。

和歌手页同一套路（见 :mod:`app.bridges.detail`）。

**id 不能无条件信**：网易云的歌自带 ``album_id``，可以直接打开；但 QQ / 酷我 /
酷狗 / 咪咕的 ``album_id`` 是**别家平台的**编号，拿去网易云查很可能撞出一张
风马牛不相及的专辑。所以拿到 id 之后还要用专辑名核对一次，对不上就退回按名字搜。
"""

from __future__ import annotations

from typing import Dict

from ..sources import netease
from .detail import DetailPageController

class AlbumController(DetailPageController):
    """专辑详情页。"""

    noun = "专辑"
    list_key = "songs"

    def _fetch(self, page_id: str, name: str) -> Dict:
        page = netease.album_page(page_id, "") if page_id else {}
        if page and (not name or netease.same_title(page.get("name"), name)):
            return page
        # 没给 id / id 对不上（别的音源的编号）：按名字重新定位
        return netease.album_page("", name)
