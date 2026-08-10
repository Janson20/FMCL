"""QQ音乐 音源插件"""

import json
import logging
import random
import time
import zlib
from typing import List, Optional

from ui.music_source.base import BaseMusicSource, MusicInfo
from ui.music_source.utils import decode_name, format_singer, tx_zzc_sign

logger = logging.getLogger("music_source.tx")

TX_SIGN_URL = "https://u.y.qq.com/cgi-bin/musicu.fcg"
# 经典免签名接口（zzc 签名接口在部分网络环境下被风控拦截返回 500001）
TX_CLASSIC_SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
TX_EXPRESS_URL = "https://c.y.qq.com/base/fcgi-bin/fcg_music_express_mobile3.fcg"
TX_LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"

# 各音质对应 CDN 文件名前缀与扩展名
_TX_FILE_PREFIX = {"flac24bit": "F000", "flac": "F000", "320k": "M500", "128k": "C400"}


class QQMusicSource(BaseMusicSource):
    source_id = "tx"
    source_name = "QQ音乐"

    # ── 搜索 ────────────────────────────────────────

    def search(self, keyword: str, page: int = 1, limit: int = 30) -> List[MusicInfo]:
        try:
            resp = self.http_get(
                TX_CLASSIC_SEARCH_URL,
                params={
                    "p": page,
                    "n": limit,
                    "w": keyword,
                    "format": "json",
                    "inCharset": "utf-8",
                    "outCharset": "utf-8",
                },
                timeout=15,
            )
            body = resp.json()
            data = body.get("data") or {}
            song = data.get("song") or {}
            self.set_search_total(song.get("totalnum"))
            raw_list = song.get("list") or []
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
                songmid = item.get("songmid", "")
                media_mid = item.get("media_mid") or songmid
                if not songmid:
                    continue

                singers = [format_singer(decode_name(s.get("name", ""))) for s in (item.get("singer") or [])]
                singer = "、".join(singers)

                types, _types = self._parse_types(item, media_mid)
                interval = item.get("interval", 0)

                info = MusicInfo(
                    name=decode_name(item.get("songname", item.get("name", ""))),
                    singer=singer,
                    source=self.source_id,
                    songmid=songmid,
                    album_name=decode_name(item.get("albumname", "")),
                    album_id=str(item.get("albummid", "")),
                    interval=interval,
                    types=types,
                    _types=_types,
                )
                results.append(info)
            except Exception as e:
                logger.debug(f"解析QQ歌曲失败: {e}")
                continue
        return results

    def _parse_types(self, item: dict, media_mid: str):
        types = []
        _types = {}
        quality_map = [
            ("size128", "128k"),
            ("size320", "320k"),
            ("sizeflac", "flac"),
            ("sizehires", "flac24bit"),
        ]
        for size_key, q_type in quality_map:
            size = item.get(size_key, 0)
            if isinstance(size, str):
                try:
                    size = int(size)
                except (ValueError, TypeError):
                    size = 0
            if size > 0:
                types.append({"type": q_type, "size": self.format_size(size)})
                _types[q_type] = {"size": self.format_size(size), "media_mid": media_mid}
        return types, _types

    # ── 获取播放URL ─────────────────────────────────

    def get_music_url(self, info: MusicInfo, quality: str = "128k") -> Optional[str]:
        media_mid = (info._types.get(quality) or {}).get("media_mid") or info.songmid
        prefix = _TX_FILE_PREFIX.get(quality, "C400")
        ext = ".flac" if prefix == "F000" else (".mp3" if prefix == "M500" else ".m4a")
        filename = f"{prefix}{media_mid}{ext}"
        guid = str(random.randint(1000000000, 9999999999))

        def _parse_express(body: bytes) -> Optional[str]:
            # express 接口返回 5 字节前缀 + zlib 压缩 JSON（部分路径为明文 JSON）
            raw = body[5:] if body[:5] == b"\x00" * 5 else body
            try:
                payload = zlib.decompress(raw)
            except Exception:
                payload = raw
            try:
                j = json.loads(payload.decode("utf-8", errors="replace"))
            except Exception:
                return None
            items = (j.get("data") or {}).get("items") or []
            if not items:
                return None
            vkey = items[0].get("vkey") or ""
            fname = items[0].get("filename") or ""
            if vkey and fname and "error" not in str(vkey).lower():
                return f"http://ws.stream.qqmusic.qq.com/{fname}?vkey={vkey}&guid={guid}&uin=0&fromtag=66"
            return None

        # 1. c.y.qq.com express 免签名接口（u.y.qq.com 签名接口在部分网络被风控）
        try:
            resp = self._session.post(
                TX_EXPRESS_URL,
                data={
                    "g_tk": 5381,
                    "loginUin": 0,
                    "hostUin": 0,
                    "format": "json",
                    "inCharset": "utf-8",
                    "outCharset": "utf-8",
                    "notice": 0,
                    "platform": "yqq.json",
                    "needNewCode": 0,
                    "cid": 205361747,
                    "uin": 0,
                    "songmid": media_mid,
                    "filename": filename,
                    "guid": guid,
                    "songtype": 0,
                },
                headers={"Referer": "https://y.qq.com/"},
                timeout=10,
            )
            url = _parse_express(resp.content)
            if url:
                return url
        except Exception as e:
            # 单个候选失败属兜底流程常态，降为 debug 避免刷屏（外层有汇总日志）
            logger.debug(f"QQ获取URL失败(express) [{info.songmid}]: {e}")

        # 2. 签名 GetVkey 兜底
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
                "guid": guid,
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
            logger.debug(f"QQ获取URL失败 [{info.songmid}]: {e}")
        return None

    # ── 获取歌词 ─────────────────────────────────────

    def get_lyric(self, info: MusicInfo) -> Optional[str]:
        # 新版免签名歌词接口
        try:
            resp = self.http_get(
                TX_LYRIC_URL,
                params={"songmid": info.songmid, "format": "json", "nobase64": "1"},
                headers={"Referer": "https://y.qq.com/"},
                timeout=10,
            )
            j = resp.json()
            if j.get("retcode") == 0:
                lyric = j.get("lyric") or ""
                if lyric:
                    return lyric
        except Exception as e:
            logger.debug(f"QQ获取歌词失败(新接口) [{info.songmid}]: {e}")
        # 旧签名接口兜底
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
