"""QQ音乐 音源插件"""

import json
import logging
import random
import time
from typing import List, Optional

from ui.music_source.base import BaseMusicSource, MusicInfo
from ui.music_source.utils import decode_name, format_singer, tx_zzc_sign

logger = logging.getLogger("music_source.tx")

TX_SIGN_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"


class QQMusicSource(BaseMusicSource):
    source_id = "tx"
    source_name = "QQ音乐"

    # ── 搜索 ────────────────────────────────────────

    def search(self, keyword: str, page: int = 1, limit: int = 30) -> List[MusicInfo]:
        comm = {
            "ct": "11",
            "cv": "14090508",
            "v": "14090508",
            "tmeAppID": "qqmusic",
            "phonetype": "EBG-AN10",
            "deviceScore": "553.47",
            "devicelevel": "50",
            "newdevicelevel": "20",
            "rom": "HuaWei/EMOTION/EmotionUI_14.2.0",
            "os_ver": "12",
            "OpenUDID": "0",
            "OpenUDID2": "0",
            "QIMEI36": "0",
            "udid": "0",
            "chid": "0",
            "aid": "0",
            "oaid": "0",
            "taid": "0",
            "tid": "0",
            "wid": "0",
            "uid": "0",
            "sid": "0",
            "modeSwitch": "6",
            "teenMode": "0",
            "ui_mode": "2",
            "nettype": "1020",
            "v4ip": "",
        }
        req_data = {
            "module": "music.search.SearchCgiService",
            "method": "DoSearchForQQMusicMobile",
            "param": {
                "search_type": 0,
                "searchid": "".join(random.choices("0123456789", k=15)),
                "query": keyword,
                "page_num": page,
                "num_per_page": limit,
                "highlight": 0,
                "nqc_flag": 0,
                "multi_zhida": 0,
                "cat": 2,
                "grp": 1,
                "sin": 0,
                "sem": 0,
            },
        }
        payload = {"comm": comm, "req": req_data}
        try:
            signed = self._sign_request(payload)
            resp = self._session.post(TX_SIGN_URL, data=signed, timeout=15)
            body = resp.json()
            if body.get("code") != 0:
                logger.debug(f"QQ搜索返回错误码: {body.get('code')}")
                return []
            req_result = body.get("req", {})
            if req_result.get("code") != 0:
                return []
            data = req_result.get("data", {})
            body_data = data.get("body", {})
            songs = body_data.get("song", {})
            raw_list = songs.get("list", [])
            return self._parse_search_result(raw_list)
        except Exception as e:
            logger.warning(f"QQ音乐搜索失败: {e}")
            return []

    def _sign_request(self, payload: dict) -> bytes:
        """QQ音乐 zzc 签名请求"""
        raw = json.dumps(payload, separators=(",", ":"))
        sign = tx_zzc_sign(raw)
        return f"zzc_sign={sign}&{raw}".encode()

    def _parse_search_result(self, raw_list) -> List[MusicInfo]:
        results = []
        for item in raw_list or []:
            try:
                file_info = item.get("file", {})
                media_mid = file_info.get("media_mid", "")
                if not media_mid:
                    continue

                singers = item.get("singer", [])
                singer_names = [format_singer(decode_name(s.get("name", ""))) for s in singers]
                singer = "、".join(singer_names)

                types, _types = self._parse_types(file_info)
                interval = item.get("interval", 0)

                info = MusicInfo(
                    name=decode_name(item.get("title", item.get("name", ""))),
                    singer=singer,
                    source=self.source_id,
                    songmid=media_mid,
                    album_name=decode_name(
                        item.get("album", {}).get("name", "")
                        if isinstance(item.get("album"), dict)
                        else item.get("albumname", "")
                    ),
                    album_id=str(
                        item.get("album", {}).get("mid", "")
                        if isinstance(item.get("album"), dict)
                        else item.get("albummid", "")
                    ),
                    interval=interval,
                    types=types,
                    _types=_types,
                )
                results.append(info)
            except Exception as e:
                logger.debug(f"解析QQ歌曲失败: {e}")
                continue
        return results

    def _parse_types(self, file_info: dict):
        types = []
        _types = {}
        quality_map = [
            ("size_128mp3", "128k"),
            ("size_320mp3", "320k"),
            ("size_flac", "flac"),
            ("size_hires", "flac24bit"),
        ]
        for size_key, q_type in quality_map:
            size = file_info.get(size_key, 0)
            if isinstance(size, str):
                try:
                    size = int(size)
                except (ValueError, TypeError):
                    size = 0
            if size > 0:
                types.append({"type": q_type, "size": self.format_size(size)})
                _types[q_type] = {"size": self.format_size(size), "media_mid": file_info.get("media_mid", "")}
        return types, _types

    # ── 获取播放URL ─────────────────────────────────

    def get_music_url(self, info: MusicInfo, quality: str = "128k") -> Optional[str]:
        media_mid = info.songmid
        comm = {
            "ct": "11",
            "cv": "14090508",
            "v": "14090508",
            "tmeAppID": "qqmusic",
            "uid": "0",
            "sid": "0",
            "nettype": "1020",
        }
        req_data = {
            "module": "music.vkey.GetVkey",
            "method": "CgiGetVkey",
            "param": {
                "guid": str(random.randint(1000000000, 9999999999)),
                "songmid": [media_mid],
                "songtype": [0],
                "uin": "0",
                "loginflag": 1,
                "platform": "23",
            },
        }
        payload = {"comm": comm, "req": req_data}
        try:
            signed = self._sign_request(payload)
            resp = self._session.post(TX_SIGN_URL, data=signed, timeout=10)
            body = resp.json()
            midurlinfo = body.get("req", {}).get("data", {}).get("midurlinfo", [])
            if midurlinfo:
                purl = midurlinfo[0].get("purl", "")
                if purl:
                    return f"http://ws.stream.qqmusic.qq.com/{purl}"
        except Exception as e:
            logger.warning(f"QQ获取URL失败 [{info.songmid}]: {e}")
        return None

    # ── 获取歌词 ─────────────────────────────────────

    def get_lyric(self, info: MusicInfo) -> Optional[str]:
        comm = {
            "ct": "11",
            "cv": "14090508",
            "v": "14090508",
            "tmeAppID": "qqmusic",
            "uid": "0",
            "sid": "0",
            "nettype": "1020",
        }
        req_data = {
            "module": "music.musichallSong.PlayLyricInfo",
            "method": "GetPlayLyricInfo",
            "param": {"songMID": info.songmid, "plain": 1, "charset": "utf-8"},
        }
        payload = {"comm": comm, "req": req_data}
        try:
            signed = self._sign_request(payload)
            resp = self._session.post(TX_SIGN_URL, data=signed, timeout=10)
            body = resp.json()
            lyric = body.get("req", {}).get("data", {}).get("lyric", "")
            if lyric:
                return lyric
        except Exception as e:
            logger.warning(f"QQ获取歌词失败 [{info.songmid}]: {e}")
        return None

    # ── 获取封面 ─────────────────────────────────────

    def get_pic_url(self, info: MusicInfo) -> Optional[str]:
        return f"https://y.qq.com/music/photo_new/T002R300x300M000{info.songmid}.jpg"
