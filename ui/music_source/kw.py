"""酷我音乐 音源插件"""

import json
import logging
import re
from typing import List, Optional

from ui.music_source.base import BaseMusicSource, MusicInfo, QualityLevel
from ui.music_source.utils import create_session, decode_name, format_singer

logger = logging.getLogger("music_source.kw")

KW_SEARCH_URL = "http://search.kuwo.cn/r.s"
KW_LYRIC_URL = "http://m.kuwo.cn/newh5/singles/songinfoandlrc"
KW_MUSIC_URL = "http://www.kuwo.cn/url"
KW_PIC_URL = "http://player.kuwo.cn/webmusic/sj/dtflagdate"

# N_MINFO 解析正则
_MINFO_RE = re.compile(r"level:(\w+),bitrate:(\d+),format:(\w+),size:([\w.]+)")


class KuWoMusicSource(BaseMusicSource):
    source_id = "kw"
    source_name = "酷我音乐"

    # ── 搜索 ────────────────────────────────────────

    def search(self, keyword: str, page: int = 1, limit: int = 30) -> List[MusicInfo]:
        params = {
            "client": "kt",
            "all": keyword,
            "pn": str(page - 1),
            "rn": str(limit),
            "uid": "794762570",
            "ver": "kwplayer_ar_9.2.2.1",
            "vipver": "1",
            "show_copyright_off": "1",
            "newver": "1",
            "ft": "music",
            "cluster": "0",
            "strategy": "2012",
            "encoding": "utf8",
            "rformat": "json",
            "vermerge": "1",
            "mobi": "1",
            "issubtitle": "1",
        }
        try:
            resp = self.http_get(KW_SEARCH_URL, params=params, timeout=15)
            raw_data = resp.json()
            return self._parse_search_result(raw_data)
        except Exception as e:
            logger.warning(f"酷我搜索失败: {e}")
            return []

    def _parse_search_result(self, raw_data) -> List[MusicInfo]:
        if not raw_data:
            return []
        # 酷我 r.s 接口新版返回 dict（歌曲在 abslist，TOTAL 为总数），旧版为 list
        if isinstance(raw_data, dict):
            items = raw_data.get("abslist") or []
            self.set_search_total(raw_data.get("TOTAL"))
        else:
            items = raw_data
            self.set_search_total(0)
        results = []
        for item in items:
            try:
                song_id = item.get("MUSICRID", "").replace("MUSIC_", "")
                if not song_id:
                    continue
                n_minfo = item.get("N_MINFO", "")
                if not n_minfo:
                    continue

                types, _types = self._parse_types(n_minfo)
                interval = int(item.get("DURATION", 0))

                info = MusicInfo(
                    name=decode_name(item.get("SONGNAME", "")),
                    singer=format_singer(decode_name(item.get("ARTIST", ""))),
                    source=self.source_id,
                    songmid=song_id,
                    album_name=decode_name(item.get("ALBUM", "")),
                    album_id=decode_name(item.get("ALBUMID", "")),
                    interval=interval if interval > 0 else 0,
                    types=types,
                    _types=_types,
                    play_count=self._parse_play_count(item),
                )
                results.append(info)
            except Exception as e:
                logger.debug(f"解析酷我歌曲失败: {e}")
                continue
        return results

    def _parse_types(self, n_minfo: str):
        types = []
        _types = {}
        for match in _MINFO_RE.finditer(n_minfo):
            level, bitrate, fmt, size = match.groups()
            type_map = {"4000": "flac24bit", "2000": "flac", "320": "320k", "128": "128k"}
            q = type_map.get(bitrate)
            if q:
                types.append({"type": q, "size": size})
                _types[q] = {"size": size}
        types.reverse()
        return types, _types

    @staticmethod
    def _parse_play_count(item: dict) -> int:
        """解析播放量（酷我搜索响应字段名不稳定，多键兜底）"""
        raw = item.get("PLAYCNT") or item.get("playCnt") or item.get("playCount") or 0
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    # ── 获取播放URL ─────────────────────────────────

    def get_music_url(self, info: MusicInfo, quality: str = "128k") -> Optional[str]:
        br_map = {"flac24bit": "4000kflac24bit", "flac": "2000kflac", "320k": "320kmp3", "128k": "128kmp3"}
        br = br_map.get(quality, "128kmp3")
        # 新版 antiserver 接口（旧 www.kuwo.cn/url 已下线返回 404），
        # 返回 JSON {"code": 200, "url": "..."}
        try:
            resp = self.http_get(
                "http://antiserver.kuwo.cn/anti.s",
                params={
                    "type": "convert_url3",
                    "rid": f"MUSIC_{info.songmid}",
                    "format": "mp3",
                    "response": "url",
                    "br": br,
                },
                timeout=10,
                retries=1,
            )
            data = resp.json()
            if isinstance(data, dict):
                url = data.get("url", "")
                if url:
                    return url
        except Exception as e:
            # 单个候选失败属兜底流程常态，降为 debug 避免刷屏（外层有汇总日志）
            logger.debug(f"酷我获取URL失败(antiserver) [{info.songmid}]: {e}")
        # 旧接口兜底
        try:
            resp = self.http_get(
                KW_MUSIC_URL,
                params={
                    "format": "mp3",
                    "rid": f"MUSIC_{info.songmid}",
                    "response": "url",
                    "type": "convert_url3",
                    "br": br,
                    "from": "web",
                },
                timeout=10,
                retries=1,
            )
            data = resp.json()
            url = data.get("url", "")
            if url:
                return url
        except Exception as e:
            logger.debug(f"酷我获取URL失败 [{info.songmid}]: {e}")
        return None

    # ── 获取歌词 ─────────────────────────────────────

    def get_lyric(self, info: MusicInfo) -> Optional[str]:
        try:
            resp = self.http_get(
                KW_LYRIC_URL, params={"musicId": info.songmid}, headers={"Referer": "http://m.kuwo.cn/"}, timeout=10
            )
            data = resp.json()
            lrc_list = (data.get("data") or {}).get("lrclist") or []
            if not lrc_list:
                return None
            lines = []
            song_name = decode_name((data.get("data") or {}).get("songinfo", {}).get("songName", ""))
            artist = decode_name((data.get("data") or {}).get("songinfo", {}).get("artist", ""))
            if song_name:
                lines.append(f"[ti:{song_name}]")
            if artist:
                lines.append(f"[ar:{artist}]")
            lines.append("[offset:0]")
            for item in lrc_list:
                try:
                    t = float(item.get("time", 0))
                except (TypeError, ValueError):
                    t = 0
                txt = decode_name(item.get("lineLyric", ""))
                m = int(t // 60)
                s = t % 60
                lines.append(f"[{m:02d}:{s:05.2f}]{txt}")
            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"酷我获取歌词失败 [{info.songmid}]: {e}")
            return None

    # ── 获取封面 ─────────────────────────────────────

    def get_pic_url(self, info: MusicInfo) -> Optional[str]:
        params = {"flag": "6", "rid": f"MUSIC_{info.songmid}"}
        try:
            resp = self.http_get(KW_PIC_URL, params=params, timeout=10)
            return resp.text.strip()
        except Exception as e:
            logger.debug(f"酷我获取封面失败 [{info.songmid}]: {e}")
            return None

    def get_download_headers(self):
        # 酷我 CDN 校验 Referer
        return {"Referer": "http://www.kuwo.cn/"}
