"""咪咕音乐 音源插件"""

import json
import logging
import time
from typing import List, Optional

from ui.music_source.base import BaseMusicSource, MusicInfo
from ui.music_source.utils import MG_DEVICE_ID, decode_name, format_singer, mg_create_sign

logger = logging.getLogger("music_source.mg")

MG_SEARCH_URL = "https://app.c.nf.migu.cn/MIGUM2.0/v1.0/content/search_all.do"
MG_LISTEN_URL = "https://app.c.nf.migu.cn/MIGUM3.0/v1.0/content/sub/listen.do"


class MiGuMusicSource(BaseMusicSource):
    source_id = "mg"
    source_name = "咪咕音乐"
    # 搜索接口 resultList 每页最多返回 20 条（pageSize 再大也被截断）
    limits = {"search": 20, "lyric": 1, "url": 1}

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
            # 新版接口歌曲列表键为 resultList（旧版为 result），每项是包含
            # 单个歌曲 dict 的 list（[song] 形式）；无结果时直接返回
            raw_list = song_data.get("resultList") or song_data.get("result") or []
            if not raw_list:
                return []
            self.set_search_total(song_data.get("totalCount"))
            return self._parse_search_result(raw_list)
        except Exception as e:
            logger.warning(f"咪咕搜索失败: {e}")
            return []

    def _parse_search_result(self, raw_list) -> List[MusicInfo]:
        results = []
        seen = set()
        for raw_item in raw_list or []:
            # 新版 resultList 每项为 [song]（单个 dict 的 list），旧版为 dict
            item = raw_item
            if isinstance(item, list):
                item = item[0] if item else None
            if not isinstance(item, dict):
                continue
            # songmid 取 18 位 contentId（listen.do 播放接口要求该 ID；旧版无此字段时回退 id）
            song_id = str(
                item.get("contentId")
                or item.get("id")
                or item.get("songId")
                or item.get("copyrightId")
                or ""
            )
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

                album_img = item.get("albumImgs") or item.get("imgItems") or []
                img = album_img[0].get("img", "") if album_img else ""

                play_count_raw = item.get("listenCount") or item.get("playCount") or item.get("listen_cnt") or 0
                try:
                    play_count = int(play_count_raw)
                except (TypeError, ValueError):
                    play_count = 0

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
                    play_count=play_count,
                    lrc=item.get("lyricUrl") or "",  # 搜索响应自带的歌词文件 URL
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

        # 新版 MIGUM3.0 listen.do 接口（老 product_info_resource.do 已失效），
        # 免费歌曲直接返回播放链接；VIP 歌曲返回 PE 参数格式错误 -> 触发跨源兜底
        params = {
            "contentId": info.songmid,
            "copyrightId": info.songmid,
            "netType": "01",
            "toneFlag": q_info.get("formatType", "PQ"),
            "resourceType": q_info.get("resourceType", "2"),
            "channel": "0",
            "ua": "Android_migu",
            "version": "5.0.1",
            "appId": "yyapp2",
            "deviceId": MG_DEVICE_ID,
        }
        try:
            ts = int(time.time() * 1000)
            # retries=1: URL 获取失败会立即触发跨源兜底换源，无需按搜索接口的标准重试
            resp = self.http_get(
                MG_LISTEN_URL,
                params=params,
                headers={
                    "sign": mg_create_sign(ts, ""),
                    "timestamp": str(ts),
                    "mode": "android",
                    "osVersion": "android 7.0",
                },
                timeout=10,
                retries=1,
            )
            data = resp.json()
            if str(data.get("code")) == "000000":
                listens = data.get("songListens") or []
                if listens:
                    url = listens[0].get("url", "")
                    if url:
                        return url
        except Exception as e:
            # 单个候选失败属兜底流程常态，降为 debug 避免刷屏（外层有汇总日志）
            logger.debug(f"咪咕获取URL失败 [{info.songmid}]: {e}")
        return None

    # ── 获取歌词 ─────────────────────────────────────

    def get_lyric(self, info: MusicInfo) -> Optional[str]:
        # 新版搜索响应自带歌词文件 URL（老 queryLrcBySongId.do 接口已失效）。
        # 咪咕歌词为 "@migu music@" 头 + 纯文本行，无时间戳，转为逐行 4 秒 LRC 时间轴。
        if not info.lrc:
            return None
        try:
            resp = self.http_get(info.lrc, timeout=10)
            text = resp.content.decode("utf-8", errors="replace")
            lines = []
            t = 0
            for line in text.splitlines():
                line = line.strip()
                if not line or line.startswith("@"):
                    continue
                m = int(t // 60)
                sec = t % 60
                lines.append(f"[{m:02d}:{sec:05.2f}]{line}")
                t += 4000
            if lines:
                return "\n".join(lines)
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
