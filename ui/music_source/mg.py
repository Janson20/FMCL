"""咪咕音乐 音源插件"""

import json
import logging
import time
from typing import List, Optional

from ui.music_source.base import BaseMusicSource, MusicInfo
from ui.music_source.utils import MG_DEVICE_ID, decode_name, format_singer, mg_create_sign

logger = logging.getLogger("music_source.mg")

MG_SEARCH_URL = "https://app.c.nf.migu.cn/MIGUM2.0/v1.0/content/search_all.do"
MG_LYRIC_URL = "https://app.c.nf.migu.cn/MIGUM2.0/v1.0/content/queryLrcBySongId.do"
MG_MUSIC_URL = "https://app.c.nf.migu.cn/MIGUM2.0/v1.0/content/product_info_resource.do"


class MiGuMusicSource(BaseMusicSource):
    source_id = "mg"
    source_name = "咪咕音乐"

    # ── 搜索 ────────────────────────────────────────

    def search(self, keyword: str, page: int = 1, limit: int = 20) -> List[MusicInfo]:
        timestamp = int(time.time() * 1000)
        sign = mg_create_sign(timestamp, keyword)
        params = {
            "ua": "Android_migu",
            "version": "5.0.1",
            "text": keyword,
            "pageNo": str(page),
            "pageSize": str(limit),
            "searchSwitch": json.dumps(
                {"song": 1, "album": 0, "singer": 0, "tagSong": 0, "mvSong": 0, "songlist": 0, "bestShow": 1}
            ),
            "isCopyright": "1",
            "isCorrect": "1",
            "sort": "0",
        }
        headers = {
            "sign": sign,
            "timestamp": str(timestamp),
            "appId": "yyapp2",
            "mode": "android",
            "ua": "Android_migu",
            "version": "6.9.4",
            "osVersion": "android 7.0",
            "deviceId": MG_DEVICE_ID,
        }
        try:
            resp = self.http_get(MG_SEARCH_URL, params=params, headers=headers, timeout=15)
            raw = resp.json()
            song_data = raw.get("songResultData", {})
            if song_data.get("code") != "0":
                return []
            songs = song_data.get("result", [])
            return self._parse_search_result(songs)
        except Exception as e:
            logger.warning(f"咪咕搜索失败: {e}")
            return []

    def _parse_search_result(self, raw_list) -> List[MusicInfo]:
        results = []
        seen = set()
        for item in raw_list or []:
            song_id = str(item.get("id", item.get("songId", item.get("contentId", ""))))
            if not song_id or song_id in seen:
                continue
            seen.add(song_id)

            try:
                types, _types = self._parse_types(item)
                duration = self._parse_duration(item)
                singers = item.get("singers", item.get("singer", []))
                if isinstance(singers, list):
                    singer_names = [format_singer(decode_name(s.get("name", ""))) for s in singers]
                    singer = "、".join(singer_names)
                else:
                    singer = format_singer(decode_name(str(singers)))

                album_img = item.get("albumImgs", [])
                img = album_img[0].get("img", "") if album_img else ""

                info = MusicInfo(
                    name=decode_name(item.get("name", item.get("songName", ""))),
                    singer=singer,
                    source=self.source_id,
                    songmid=song_id,
                    album_name=decode_name(
                        item.get("albums", [{}])[0].get("name", "")
                        if item.get("albums")
                        else item.get("albumName", item.get("album", ""))
                    ),
                    album_id=str(item.get("albumId", "")),
                    interval=duration,
                    img=img,
                    types=types,
                    _types=_types,
                )
                results.append(info)
            except Exception as e:
                logger.debug(f"解析咪咕歌曲失败: {e}")
                continue
        return results

    def _parse_types(self, item: dict):
        types = []
        _types = {}
        rate_formats = item.get("newRateFormats", item.get("rateFormats", []))
        if not rate_formats:
            return types, _types
        for fmt in rate_formats:
            fmt_type = fmt.get("formatType", "")
            if fmt_type == "PQ":
                types.append({"type": "128k"})
                _types["128k"] = {"formatType": "PQ", "resourceType": fmt.get("resourceType", "")}
            elif fmt_type == "HQ":
                types.append({"type": "320k"})
                _types["320k"] = {"formatType": "HQ", "resourceType": fmt.get("resourceType", "")}
            elif fmt_type == "SQ":
                types.append({"type": "flac"})
                _types["flac"] = {"formatType": "SQ", "resourceType": fmt.get("resourceType", "")}
            elif fmt_type == "ZQ":
                types.append({"type": "flac24bit"})
                _types["flac24bit"] = {"formatType": "ZQ", "resourceType": fmt.get("resourceType", "")}
        return types, _types

    def _parse_duration(self, item: dict) -> int:
        dur = item.get("duration", item.get("length", item.get("auditionsLength", 0)))
        if isinstance(dur, str):
            try:
                return int(dur)
            except (ValueError, TypeError):
                return 0
        return int(dur) if dur else 0

    # ── 获取播放URL ─────────────────────────────────

    def get_music_url(self, info: MusicInfo, quality: str = "128k") -> Optional[str]:
        q_info = info._types.get(quality, {})
        if not q_info:
            # 尝试任意可用音质
            for q in ["128k", "320k", "flac", "flac24bit"]:
                if q in info._types:
                    q_info = info._types[q]
                    break
            if not q_info:
                return None

        params = {
            "ua": "Android_migu",
            "version": "5.0.1",
            "copyrightId": info.songmid,
            "resourceType": q_info.get("resourceType", "E"),
            "formatType": q_info.get("formatType", "PQ"),
        }
        try:
            resp = self.http_get(MG_MUSIC_URL, params=params, timeout=10)
            data = resp.json()
            url = data.get("data", {}).get("url", data.get("resource", [{}])[0].get("url", ""))
            if url:
                return url
        except Exception as e:
            logger.warning(f"咪咕获取URL失败 [{info.songmid}]: {e}")
        return None

    # ── 获取歌词 ─────────────────────────────────────

    def get_lyric(self, info: MusicInfo) -> Optional[str]:
        try:
            resp = self.http_get(
                MG_LYRIC_URL,
                params={"songId": info.songmid, "ua": "Android_migu", "version": "5.0.1", "formatType": "LRC"},
                headers={
                    "appId": "yyapp2",
                    "mode": "android",
                    "ua": "Android_migu",
                    "version": "6.9.4",
                    "osVersion": "android 7.0",
                    "deviceId": MG_DEVICE_ID,
                },
                timeout=10,
            )
            data = resp.json()
            if data.get("code") == "0":
                lrc = data.get("data", {}).get("lrcLyric", "")
                if lrc:
                    return lrc
        except Exception as e:
            logger.warning(f"咪咕获取歌词失败 [{info.songmid}]: {e}")
        return None

    # ── 获取封面 ─────────────────────────────────────

    def get_pic_url(self, info: MusicInfo) -> Optional[str]:
        if info.img:
            return info.img
        try:
            resp = self.http_get(
                "https://app.c.nf.migu.cn/MIGUM2.0/v1.0/content/resourceinfo.do",
                params={"ua": "Android_migu", "version": "5.0.1", "needImage": "1", "copyrightId": info.songmid},
                timeout=10,
            )
            data = resp.json()
            imgs = data.get("data", {}).get("albumImgs", [])
            if imgs:
                return imgs[0].get("img", "")
        except Exception as e:
            logger.debug(f"咪咕获取封面失败 [{info.songmid}]: {e}")
        return None
