"""酷狗音乐 音源插件"""

import logging
from typing import List, Optional

from ui.music_source.base import BaseMusicSource, MusicInfo
from ui.music_source.utils import decode_name

logger = logging.getLogger("music_source.kg")

KG_SEARCH_URL = "https://songsearch.kugou.com/song_search_v2"
KG_LYRIC_URL = "https://lyrics.kugou.com/search"
KG_LYRIC_DOWNLOAD = "https://lyrics.kugou.com/download"
KG_PIC_URL = "https://imge.kugou.com/stdmusic/{size}/{hash}.png"


class KuGouMusicSource(BaseMusicSource):
    source_id = "kg"
    source_name = "酷狗音乐"

    # ── 搜索 ────────────────────────────────────────

    def search(self, keyword: str, page: int = 1, limit: int = 30) -> List[MusicInfo]:
        params = {
            "keyword": keyword,
            "page": str(page),
            "pagesize": str(limit),
            "userid": "0",
            "clientver": "",
            "platform": "WebFilter",
            "filter": "2",
            "iscorrection": "1",
            "privilege_filter": "0",
            "area_code": "1",
        }
        try:
            resp = self.http_get(KG_SEARCH_URL, params=params, timeout=15)
            body = resp.json()
            lists = body.get("data", {}).get("lists", [])
            return self._parse_search_result(lists)
        except Exception as e:
            logger.warning(f"酷狗搜索失败: {e}")
            return []

    def _parse_search_result(self, raw_list) -> List[MusicInfo]:
        results = []
        seen = set()
        for item in raw_list or []:
            try:
                song_id = str(item.get("Audioid", ""))
                file_hash = item.get("FileHash", "")
                key = f"{song_id}_{file_hash}"
                if key in seen:
                    continue
                seen.add(key)

                types, _types = self._parse_types(item)
                interval = item.get("Duration", 0)

                info = MusicInfo(
                    name=decode_name(item.get("SongName", "")),
                    singer=self._format_singers(item.get("Singers", [])),
                    source=self.source_id,
                    songmid=song_id,
                    album_name=decode_name(item.get("AlbumName", "")),
                    album_id=str(item.get("AlbumID", "")),
                    interval=interval,
                    types=types,
                    _types=_types,
                )
                results.append(info)

                # 处理 Grp 子项 (同一首歌的不同版本)
                for child in item.get("Grp", []):
                    child_key = f"{child.get('Audioid', '')}_{child.get('FileHash', '')}"
                    if child_key in seen:
                        continue
                    seen.add(child_key)
                    child_types, child_types_dict = self._parse_types(child)
                    child_interval = child.get("Duration", 0)
                    child_info = MusicInfo(
                        name=decode_name(child.get("SongName", "")),
                        singer=self._format_singers(child.get("Singers", [])),
                        source=self.source_id,
                        songmid=str(child.get("Audioid", "")),
                        album_name=decode_name(child.get("AlbumName", "")),
                        album_id=str(child.get("AlbumID", "")),
                        interval=child_interval,
                        types=child_types,
                        _types=child_types_dict,
                    )
                    results.append(child_info)

            except Exception as e:
                logger.debug(f"解析酷狗歌曲失败: {e}")
                continue
        return results

    def _parse_types(self, item: dict):
        types = []
        _types = {}
        quality_map = [
            ("FileSize", "FileHash", "128k"),
            ("HQFileSize", "HQFileHash", "320k"),
            ("SQFileSize", "SQFileHash", "flac"),
            ("ResFileSize", "ResFileHash", "flac24bit"),
        ]
        for size_key, hash_key, q_type in quality_map:
            size = item.get(size_key, 0)
            if isinstance(size, str):
                try:
                    size = int(size)
                except (ValueError, TypeError):
                    size = 0
            if size > 0:
                types.append({"type": q_type, "size": self.format_size(size)})
                _types[q_type] = {"size": self.format_size(size), "hash": item.get(hash_key, "")}
        return types, _types

    def _format_singers(self, singers: list) -> str:
        if not singers:
            return ""
        return "、".join(decode_name(s.get("name", "")) for s in singers if s.get("name"))

    # ── 获取播放URL ─────────────────────────────────

    def get_music_url(self, info: MusicInfo, quality: str = "128k") -> Optional[str]:
        hash_val = info._types.get(quality, {}).get("hash", "")
        if not hash_val:
            return None
        try:
            # 先获取 key
            key_resp = self.http_get(
                "https://trackerc.kugou.com/i/",
                params={"cmd": "4", "hash": hash_val, "key": info.songmid, "pid": "1", "acceptMp3": "1"},
                headers={"Referer": "https://www.kugou.com/"},
                timeout=10,
            )
            key_data = key_resp.json()
            key = key_data.get("key", "")
            if key:
                return f"https://kugou.com/yy/index.php?r=play/getdata&hash={hash_val}&key={key}"
        except Exception as e:
            logger.warning(f"酷狗获取URL失败 [{info.songmid}]: {e}")
        return None

    # ── 获取歌词 ─────────────────────────────────────

    def get_lyric(self, info: MusicInfo) -> Optional[str]:
        hash_val = info._types.get("128k", {}).get("hash", "")
        if not hash_val:
            return None
        try:
            resp = self.http_get(
                KG_LYRIC_URL,
                params={
                    "keyword": f"{info.name} {info.singer}",
                    "hash": hash_val,
                    "timelength": str(info.interval * 1000) if info.interval else "0",
                    "clientver": "",
                },
                timeout=10,
            )
            data = resp.json()
            candidates = data.get("data", {}).get("lists", []) if data.get("data") else data.get("candidates", [])
            if not candidates:
                candidates = data.get("candidates", [])
            if not candidates:
                return None
            first = candidates[0] if isinstance(candidates, list) else candidates
            lyric_id = first.get("id") if isinstance(first, dict) else None
            accesskey = first.get("accesskey") if isinstance(first, dict) else None
            if not lyric_id or not accesskey:
                return None
            lrc_resp = self.http_get(KG_LYRIC_DOWNLOAD, params={"id": lyric_id, "accesskey": accesskey}, timeout=10)
            lrc_data = lrc_resp.json()
            content = lrc_data.get("content", "")
            if content:
                return content
        except Exception as e:
            logger.warning(f"酷狗获取歌词失败 [{info.songmid}]: {e}")
        return None

    # ── 获取封面 ─────────────────────────────────────

    def get_pic_url(self, info: MusicInfo) -> Optional[str]:
        hash_val = info._types.get("128k", {}).get("hash", "")
        if not hash_val:
            return None
        try:
            img_resp = self.http_get(
                "https://kugou.com/yy/index.php", params={"r": "play/getdata", "hash": hash_val}, timeout=10
            )
            data = img_resp.json()
            img_url = data.get("data", {}).get("img", "")
            if img_url:
                return img_url
        except Exception:
            pass
        return KG_PIC_URL.format(size="120", hash=hash_val)
