"""网易云音乐 音源插件"""
import json
import logging
import random
from typing import List, Optional

from ui.music_source.base import BaseMusicSource, MusicInfo
from ui.music_source.utils import (
    decode_name, format_singer, wy_eapi, wy_weapi,
)

logger = logging.getLogger("music_source.wy")

WY_EAPI_BASE = "https://interface3.music.163.com/eapi"
WY_API_BASE = "https://music.163.com/weapi"


class NetEaseMusicSource(BaseMusicSource):
    source_id = "wy"
    source_name = "网易云音乐"

    def __init__(self):
        super().__init__()
        self._session.headers.update({
            "Referer": "https://music.163.com",
            "Origin": "https://music.163.com",
        })
        # 设置 cookie (防止CSRF)
        self._session.cookies.set("os", "pc", domain=".music.163.com")
        self._session.cookies.set("appver", "2.10.6", domain=".music.163.com")
        self._session.cookies.set("channel", "netease", domain=".music.163.com")
        self._session.cookies.set("_ntes_nuid", "".join(random.choices("0123456789abcdef", k=32)), domain=".music.163.com")
        self._session.cookies.set("MUSIC_U", "", domain=".music.163.com")
        self._session.cookies.set("__remember_me", "true", domain=".music.163.com")

    # ── 搜索 ────────────────────────────────────────

    def search(self, keyword: str, page: int = 1, limit: int = 30) -> List[MusicInfo]:
        url = "/api/search/song/list/page"
        data = {
            "keyword": keyword,
            "needCorrect": "1",
            "channel": "typing",
            "offset": limit * (page - 1),
            "scene": "normal",
            "total": "true" if page == 1 else "false",
            "limit": limit,
        }
        try:
            resp = self._eapi_post(url, data)
            if resp.get("code") != 200:
                logger.debug(f"网易搜索返回错误码: {resp.get('code')}")
                return []
            raw_list = (resp.get("data") or {}).get("resources") or []
            return self._parse_search_result(raw_list)
        except Exception as e:
            logger.warning(f"网易云搜索失败: {e}")
            return []

    def _parse_search_result(self, raw_list) -> List[MusicInfo]:
        results = []
        for item in (raw_list or []):
            try:
                base = item.get("baseInfo", {})
                simple = base.get("simpleSongData", {})
                if not simple:
                    continue

                priv = simple.get("privilege", {})
                singers = simple.get("ar", [])
                singer_names = [format_singer(decode_name(s.get("name", ""))) for s in singers]
                singer = "、".join(singer_names)

                types, _types = self._parse_types(simple)
                album = simple.get("al", {})
                interval_raw = simple.get("dt", 0)
                interval = interval_raw // 1000 if interval_raw else 0

                info = MusicInfo(
                    name=decode_name(simple.get("name", "")),
                    singer=singer,
                    source=self.source_id,
                    songmid=str(simple.get("id", "")),
                    album_name=decode_name(album.get("name", "") if album else ""),
                    album_id=str(album.get("id", "") if album else ""),
                    interval=interval,
                    img=album.get("picUrl", "") if album else "",
                    types=types,
                    _types=_types,
                )
                results.append(info)
            except Exception as e:
                logger.debug(f"解析网易歌曲失败: {e}")
                continue
        return results

    def _parse_types(self, item: dict):
        types = []
        _types = {}
        priv = item.get("privilege", {})
        maxbr = priv.get("maxbr", 0)

        # flac (SQ)
        if maxbr >= 999000:
            sq = item.get("sq", {})
            if sq:
                types.append({"type": "flac"})
                _types["flac"] = {"id": sq.get("id", item.get("id", ""))}

        # 320k (HQ)
        if maxbr >= 320000:
            hq = item.get("h", {})
            if hq:
                types.append({"type": "320k"})
                _types["320k"] = {"id": hq.get("id", item.get("id", ""))}

        # 128k
        low = item.get("l", {})
        if low:
            types.append({"type": "128k"})
            _types["128k"] = {"id": low.get("id", item.get("id", ""))}

        types.reverse()
        return types, _types

    # ── 获取播放URL ─────────────────────────────────

    def get_music_url(self, info: MusicInfo, quality: str = "128k") -> Optional[str]:
        song_id = info.songmid
        br_map = {"flac24bit": "hires", "flac": "999000", "320k": "320000", "128k": "128000"}
        br = br_map.get(quality, "128000")

        # 使用 eapi 接口获取播放URL
        url = "/api/song/enhance/player/url"
        data = {
            "ids": f"[{song_id}]",
            "br": int(br),
        }
        try:
            resp = self._eapi_post(url, data)
            urls = resp.get("data", [])
            if urls and urls[0].get("url"):
                play_url = urls[0]["url"]
                return play_url
        except Exception as e:
            logger.warning(f"网易获取URL失败 [{info.songmid}]: {e}")

        # 回退到 weapi
        try:
            weapi_url = "/song/enhance/player/url/v1"
            weapi_data = {
                "ids": f"[{song_id}]",
                "level": quality,
                "encodeType": "aac",
            }
            resp2 = self._weapi_post(weapi_url, weapi_data)
            urls2 = resp2.get("data", [])
            if urls2 and urls2[0].get("url"):
                return urls2[0]["url"]
        except Exception:
            pass

        return None

    # ── 获取歌词 ─────────────────────────────────────

    def get_lyric(self, info: MusicInfo) -> Optional[str]:
        url = "/api/song/lyric"
        data = {
            "id": info.songmid,
            "lv": -1,
            "tv": -1,
            "rv": -1,
        }
        try:
            resp = self._eapi_post(url, data)
            if resp.get("code") == 200:
                lrc_data = resp.get("lrc") or resp.get("data", {}).get("lrc", {})
                if isinstance(lrc_data, dict):
                    lyric = lrc_data.get("lyric", "")
                else:
                    lyric = lrc_data
                if lyric:
                    return lyric
        except Exception as e:
            logger.warning(f"网易获取歌词失败 [{info.songmid}]: {e}")
        return None

    # ── 获取封面 ─────────────────────────────────────

    def get_pic_url(self, info: MusicInfo) -> Optional[str]:
        if info.img:
            return info.img
        song_id = info.songmid
        try:
            url = "/song/detail"
            data = {"ids": f"[{song_id}]"}
            resp = self._weapi_post(url, data)
            songs = resp.get("songs", [])
            if songs:
                al = songs[0].get("al", {})
                pic_url = al.get("picUrl", "")
                if pic_url:
                    return pic_url
        except Exception:
            pass
        return f"https://music.163.com/api/song/enhance/player/url?id={song_id}"

    # ── 加密请求辅助 ────────────────────────────────

    def _eapi_post(self, path: str, data: dict) -> dict:
        """发送 eapi 加密请求"""
        signed = wy_eapi(path, data)
        resp = self._session.post(
            f"{WY_EAPI_BASE}{path}",
            data=signed,
            timeout=15,
        )
        return resp.json()

    def _weapi_post(self, path: str, data: dict) -> dict:
        """发送 weapi 加密请求"""
        signed = wy_weapi(data)
        resp = self._session.post(
            f"{WY_API_BASE}{path}",
            data=signed,
            timeout=15,
        )
        return resp.json()
