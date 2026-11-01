"""歌曲百科控制器：给展开播放页的「百科」标签页取数。

百科是网易云独有的数据（取数逻辑见 :func:`app.sources.netease.song_wiki`），
而且只有当用户真的翻到那一页时才用得着，所以这里做两件事：

* **懒加载** —— 界面把「面板展开 + 停在百科标签」通过 :meth:`SongWikiController.setActive`
  告诉它，满足条件才去取；否则每切一首歌都白跑一次请求（还要顺带搜一次歌曲、一次专辑）；
* **跟着当前曲目走** —— 接播放器的 ``trackChanged``，用 ``uid`` 判断手里的数据
  是不是当前这首歌的：不是就**立刻作废**（免得切过来的瞬间看到上一首的百科）
  再重取，并用请求序号守住乱序返回。

取不到不算错误：「网易云没收录这首歌」和「网络不通」在界面上都是空态，不值得
弹提示打断听歌 —— 与 :mod:`app.sources.netease` 里其它补充接口一样是尽力而为。
"""

from __future__ import annotations

import logging
import threading
from typing import Dict, List

from PySide6.QtCore import QObject, Property, Signal, Slot

from ..sources import netease

logger = logging.getLogger(__name__)

class _Emitter(QObject):
    """把工作线程的结果投递回主线程。"""

    loaded = Signal(int, str, object)

class SongWikiController(QObject):
    """「百科」标签页的数据与加载状态。"""

    changed = Signal()

    def __init__(self, player, parent=None):
        super().__init__(parent)
        self._player = player
        self._emitter = _Emitter(self)
        self._emitter.loaded.connect(self._on_loaded)

        self._rows: List[Dict[str, str]] = []
        self._loading = False
        self._active = False
        self._uid = ""          # 手上这份百科属于哪首曲目
        self._target_uid = ""   # 当前曲目（数据该属于谁）
        self._pending = ""      # 正在取的曲目
        self._seq = 0

    # ── 属性 ────────────────────────────────────────────────

    @Property("QVariantList", notify=changed)
    def rows(self):  # noqa: N802
        """要在面板里逐行显示的 ``[{"label", "value"}, …]``。"""
        return list(self._rows)

    @Property(bool, notify=changed)
    def loading(self) -> bool:
        return self._loading

    @Property(bool, notify=changed)
    def hasRows(self) -> bool:  # noqa: N802
        return bool(self._rows)

    @Property(str, notify=changed)
    def hint(self) -> str:
        """空态那一句话。"""
        if not self._target_uid:
            return "还没有在播放的歌"
        return "网易云的曲库里没有这首歌的百科"

    # ── 取数开关 ────────────────────────────────────────────

    @Slot(bool)
    def setActive(self, active: bool) -> None:  # noqa: N802
        """界面告知「百科标签页现在是否可见」，可见时才去取数。"""
        active = bool(active)
        if active == self._active:
            return
        self._active = active
        if active:
            self._maybe_load()

    def watch_player(self) -> None:
        """接上播放器：换歌时作废旧数据（必要时重取）。"""
        if self._player is None:
            return
        self._player.trackChanged.connect(self._on_track_changed)

    @Slot()
    def _on_track_changed(self) -> None:
        self._maybe_load()

    # ── 内部 ────────────────────────────────────────────────

    def _fields(self) -> Dict:
        """当前曲目里取数用得上的字段（顺便把「非网易云 id 不能当网易云 id 用」分清楚）。"""
        current = self._player.currentTrack if self._player is not None else None
        if not isinstance(current, dict):
            return {"uid": ""}
        source = str(current.get("source") or "")
        # 只有网易云曲目才有网易云的歌曲 / 专辑 id；QQ、酷我的数字 id 丢给网易云
        # 会撞出一首风马牛不相及的歌（专辑页那边踩过同样的坑），所以只用名字定位。
        is_wy = source == "wy"
        return {
            "uid": str(current.get("uid") or ""),
            "song_id": str(current.get("songmid") or "") if is_wy else "",
            "album_id": str(current.get("albumId") or "") if is_wy else "",
            "album": str(current.get("album") or ""),
            "name": str(current.get("name") or ""),
            "singer": str(current.get("singer") or ""),
            "interval": int(current.get("interval") or 0),
        }

    def _maybe_load(self) -> None:
        fields = self._fields()
        uid = str(fields.get("uid") or "")
        if uid != self._target_uid:
            # 换歌了：旧数据立刻作废。不这么做的话，切回百科标签的那一瞬间
            # 会先显示上一首的百科，然后才被新数据顶掉。
            self._target_uid = uid
            self._rows = []
            self._uid = ""
            self._loading = False
            self.changed.emit()
        if not self._active or not uid:
            return
        if uid == self._uid or uid == self._pending:
            return
        self._start(uid, fields)

    def _start(self, uid: str, fields: Dict) -> None:
        self._seq += 1
        seq = self._seq
        self._pending = uid
        self._loading = True
        self.changed.emit()
        threading.Thread(
            target=self._worker, args=(seq, uid, fields), daemon=True, name="song-wiki"
        ).start()

    def _worker(self, seq: int, uid: str, fields: Dict) -> None:
        try:
            page = netease.song_wiki(
                fields.get("song_id") or "",
                name=fields.get("name") or "",
                singer=fields.get("singer") or "",
                album_name=fields.get("album") or "",
                album_id=fields.get("album_id") or "",
                interval=int(fields.get("interval") or 0),
            )
        except Exception as e:
            logger.debug("获取歌曲百科失败: %s", e)
            page = {}
        self._emitter.loaded.emit(seq, uid, page)

    @Slot(int, str, object)
    def _on_loaded(self, seq: int, uid: str, page) -> None:
        if seq != self._seq:
            return  # 过期请求，丢弃
        self._pending = ""
        self._loading = False
        # 取数期间又换歌了：这份数据已经不是当前曲目的，同样丢掉
        if uid and uid == self._target_uid:
            rows = (page or {}).get("rows") if isinstance(page, dict) else None
            self._rows = [dict(r) for r in (rows or []) if isinstance(r, dict)]
            self._uid = uid
        self.changed.emit()
