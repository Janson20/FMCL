"""网易云音乐 音源插件"""

import datetime
import json
import logging
import random
import re
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

from ui.music_source.base import BaseMusicSource, MusicInfo
from ui.music_source.utils import decode_name, format_singer, wy_eapi, wy_weapi

logger = logging.getLogger("music_source.wy")

WY_EAPI_BASE = "https://interface3.music.163.com/eapi"
WY_API_BASE = "https://music.163.com/weapi"

# 歌名末尾的括号版本后缀（Live/伴奏/翻唱/中文版等标注），分组判定原唱时忽略。
# 仅剥离"含版本词"的括号：真实歌名一部分的括号（如 幹物女(WeiWei)、
# 逃避现实 (feat.洛天依)）不属于版本后缀，不剥离，避免原版被误判"不干净"。
_VERSION_WORDS = (
    "live", "cover", "伴奏", "翻唱", "翻自", "原唱", "remix", "重混",
    "重置", "重录", "官方", "official", "纯音乐", "钢琴", "吉他", "现场",
    "demo", "inst", "instrumental", "中文版", "日文版", "粤语版", "国语版",
    "日语", "填词", "合唱版", "男声版", "女声版", "mv", "清唱", "版",
)
_VERSION_SUFFIX_RE = re.compile(
    r"[\s\u3000]*[（(\[]"
    r"[^（）()\[\]］]*?(?:"
    + "|".join(_VERSION_WORDS)
    + r")[^（）()\[\]］]*[）)\]][\s\u3000]*$",
    re.IGNORECASE,
)
# 歌手名首尾标点（"周杰伦-"、"BEYOND." 等），规范化时去除
_ARTIST_EDGE_RE = re.compile(r"^[\s\-\.\,、'‘’。·]+|[\s\-\.\,、'‘’。·]+$")
# 歌手名中的括号别名（如 "冯沁苑(买辣椒也用券)" -> "买辣椒也用券"）
_PAREN_ALIAS_RE = re.compile(r"[（(]([^（）()]*)[）)]")

# 争议歌曲策展表（规范化歌名 -> 原唱）：算法无法从网易数据判定原唱时，用此表修正。
# 适用场景:
#   1. 大众熟知版本不是原唱的老歌（如《突然的自我》大众熟知伍佰版，原唱黄小琥）
#   2. 原唱版本发布日期为平台占位日期（如 2018-01-01）被 _is_valid_date 过滤，
#      或翻唱上传日期早于原版，导致算法误判翻唱为原唱（如《异样的风暴中心》）
#   3. 原唱版本发布日期缺失（N/A），算法在簇内按热度/日期兜底选中翻唱
#      （如《烂掉的白月光》选了热度第一的摸之鱼版）
#   4. 同名歌在结果中仅 1 个版本（无法组簇比较）或全为孤立锚点簇
#      （如《加班的人啊》《弑神者》），策展兜底直接置顶
# 修正方式:
#   原唱版本在结果页内 -> 置顶其最早的有效版本；
#   原唱版本缺失 -> 保持最热版本置顶，徽章显示真实原唱名。
CURATED_ORIGINALS = {
    "突然的自我": "黄小琥",
    "月亮代表我的心": "陈芬兰",
    "冬天里的一把火": "高凌风",
    "对面的女孩看过来": "阿牛",
    "再回首": "苏芮",
    "弯弯的月亮": "陈汝佳",
    "耶利亚女郎": "刘文正",
    "不必太在意": "蓝心湄",
    "明天你是否依然爱我": "王芷蕾",
    "后来": "Kiroro",
    "九儿": "胡莎莎",
    "普通disco": "洛天依、言和",
    "霜雪千年": "洛天依、乐正绫",
    "大碗宽面": "吴亦凡",
    "病名为爱": "镜音铃、镜音连",
    "七里香": "周杰伦",
    "异样的风暴中心": "杉田朗、洛天依",
    "人是猫": "张卡斯、洛天依",
    "我是秦始皇": "WOVOP、洛天依",
    "加班的人啊": "墨老板、洛天依Official",
    "弑神者": "洛天依Official、QGRay",
    "逃避现实 (feat.洛天依)": "QGRay、洛天依",
    "烂掉的白月光": "洛天依、乐正绫、路明熹",
    "一梦千宵": "苏逸_Suyi、洛天依Official",
    "写给我第一个喜欢的女孩的歌": "ilem、洛天依Official",
    "白鸟过河滩": "ilem、洛天依Official",
    "童年": "张艾嘉",
    "幹物女(weiwei)": "Z新豪、洛天依、乐正绫",
    "上山岗": "ilem、洛天依Official",
}

# 同名不同曲表：多首互不相关的歌曲同名（如《蝴蝶》陶喆版与洛天依版、
# 《哑巴》刘维版与Z新豪版、《千秋不负》妄尘组与洛天依Official版、
# 《超能力》邓紫棋版与后海大鲨鱼版），算法无法判定用户搜索意图，
# 命中时对整组结果不标记任何原唱。
NO_ORIGINAL_SONGS = {
    "蝴蝶",
    "哑巴",
    "千秋不负",
    "超能力",
}


class NetEaseMusicSource(BaseMusicSource):
    source_id = "wy"
    source_name = "网易云音乐"

    def __init__(self):
        super().__init__()
        self._session.headers.update({"Referer": "https://music.163.com", "Origin": "https://music.163.com"})
        # 设置 cookie (防止CSRF)
        self._session.cookies.set("os", "pc", domain=".music.163.com")
        self._session.cookies.set("appver", "2.10.6", domain=".music.163.com")
        self._session.cookies.set("channel", "netease", domain=".music.163.com")
        self._session.cookies.set(
            "_ntes_nuid", "".join(random.choices("0123456789abcdef", k=32)), domain=".music.163.com"
        )
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
            results = self._parse_search_result(raw_list)
            try:
                self._mark_original(results, keyword)
            except Exception as e:
                logger.warning(f"网易标记原唱失败: {e}")
            return results
        except Exception as e:
            logger.warning(f"网易云搜索失败: {e}")
            return []

    def _parse_search_result(self, raw_list) -> List[MusicInfo]:
        results = []
        for item in raw_list or []:
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

                origin = simple.get("originSongSimpleData") or {}
                origin_artists = (
                    [format_singer(decode_name(a.get("name", ""))) for a in origin.get("artists", [])]
                    if origin
                    else []
                )

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
                    publish_time=int(simple.get("publishTime") or 0),
                    origin_song_id=str(origin.get("songId") or "") if origin else "",
                    origin_artists=origin_artists,
                    fee=int((simple.get("privilege") or {}).get("fee") or 0),
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

    # ── 原唱识别与置顶 ──────────────────────────────

    def _mark_original(self, results: List[MusicInfo], keyword: str) -> None:
        """在搜索结果中识别原唱并置顶

        规则:
            1. 按规范化歌名（忽略 Live/伴奏/翻唱版等括号版本后缀）分组
            2. 组内按 originSongSimpleData 拆分为同曲簇（同名不同曲的
               版本互不干扰，如费玉清版与汪峰版《春天里》）
            3. 簇内优先取被翻唱引用的锚点版本（原曲确实在结果中时），
               避免翻唱上传日期早于原版造成的误判
            4. 发布日期为 1 月 1 日（占位日期）或早于 1975 年的视为无效
            5. 候选无有效歌曲发布时间时，查专辑发布日期兜底（同样过滤假日期）
            6. 专辑日期也没有时，按热度顺序（结果列表顺序）取第一条
               干净同名歌曲作为最终兜底
            7. 多个簇大小相同时，优先有被引用锚点的簇，再按候选日期最早者
            8. 搜索词是歌手名且至少 2 条结果命中（歌手搜索场景）时，歌名命中
               不足 2 条才不标记；歌名与歌手同名且歌名命中较多时（如《非人哉》），
               仍按歌曲搜索处理
            9. 搜索词中包含某歌手名（含括号别名，如 冯沁苑(买辣椒也用券)）
               且至少 2 条结果命中时，原唱必须属于该歌手（避免把翻唱置顶）；
               若该约束导致全部候选被过滤（歌手名恰为歌名一部分的误伤，
               如"我是初音未来"含"初音未来"），回退忽略约束重算
            10. 同名不同曲表（NO_ORIGINAL_SONGS）命中的搜索词，无法判定
               用户意图，不标记任何原唱
            11. 仅将原唱前移置顶并打上原唱标签，其余结果保持热度排序不变
        """
        if len(results) < 2:
            return
        kw = self._normalize_keyword(keyword)
        if not kw:
            return
        # 同名不同曲表命中：多首互不相关的同名歌曲，不标记原唱
        if self._group_key(kw) in NO_ORIGINAL_SONGS:
            return
        # 歌手搜索场景（搜索词是歌手名且歌名命中不足 2 条），不标记原唱；
        # 歌名命中较多时是歌曲搜索（如《非人哉》歌名与乐队同名），继续标记
        singer_hits = sum(kw in self._singer_artists(info) for info in results)
        if singer_hits >= 2 and sum(self._group_key(info.name) == kw for info in results) < 2:
            return

        # 搜索词中包含、且至少 2 条结果命中的歌手名 → 原唱必须属于该歌手
        artist_count = {}
        for info in results:
            for a in self._singer_artists(info):
                if len(a) >= 2 and a in kw:
                    artist_count[a] = artist_count.get(a, 0) + 1
        kw_artists = {a for a, c in artist_count.items() if c >= 2}

        groups = {}
        for info in results:
            groups.setdefault(self._group_key(info.name), []).append(info)

        # 每个同名组的同曲簇分析
        group_clusters = {}
        for key, group in groups.items():
            if len(group) < 2:
                continue
            for cluster in self._clusters(group):
                if len(cluster) < 2:
                    continue
                # 簇内无锚点版本（原曲不在结果页内）时成员全是翻唱，
                # 无法从簇内数据判定原唱，不参与候选（如《暗号》origin簇）
                if not any(not info.origin_song_id for info in cluster):
                    continue
                clean = [info for info in cluster if self._is_clean_name(info.name)]
                if not clean:
                    continue
                ref_clean = [
                    info for info in self._referenced_anchors(cluster)
                    if self._is_clean_name(info.name)
                ]
                group_clusters.setdefault(key, []).append((cluster, clean, ref_clean))

        # 无有效歌曲发布时间的簇需查专辑发布日期
        need_album_ids = set()
        for metas in group_clusters.values():
            for _, clean, ref_clean in metas:
                timed = [i for i in clean if self._is_valid_date(i.publish_time)]
                ref_timed = [i for i in ref_clean if self._is_valid_date(i.publish_time)]
                if not timed and not ref_timed:
                    for info in clean:
                        if info.album_id and info.album_id != "0":
                            need_album_ids.add(info.album_id)
        album_times = self._fetch_album_publish_times(need_album_ids) if need_album_ids else {}

        candidates = []
        for key, metas in group_clusters.items():
            chosen = self._group_choice(metas, kw_artists, album_times)
            # kw_artists 过滤误伤回退：搜索词含的歌手名恰为歌名一部分
            # （如"我是初音未来"含"初音未来"）时，全部候选被过滤则忽略约束重算
            if chosen is None and kw_artists:
                chosen = self._group_choice(metas, set(), album_times)
            if chosen is None:
                continue
            # 歌名与搜索词匹配的组优先置顶（避免同名搜索里别的歌抢位）
            name_match = 0 if kw and key in kw else 1
            candidates.append((name_match, chosen))

        if candidates:
            candidates.sort(
                key=lambda x: (
                    x[0],
                    0 if self._is_valid_date(x[1].publish_time) else 1,
                    x[1].publish_time,
                )
            )
            original = candidates[0][1]
        else:
            # 策展歌无算法候选时：置顶最热干净版本，由徽章修正原唱名
            original = None
            for info in results:
                if self._group_key(info.name) in CURATED_ORIGINALS and self._is_clean_name(info.name):
                    original = info
                    break
        if original is None:
            return
        original.is_original = True
        # 策展表修正：原唱在结果页内则置顶其版本，缺失则徽章显示真实原唱名
        curated = CURATED_ORIGINALS.get(self._group_key(original.name))
        if curated:
            curated_norm = self._normalize_keyword(curated)
            if curated_norm and curated_norm not in self._singer_artists(original):
                pinned = self._curated_pin(results, original, curated_norm)
                if pinned:
                    original.is_original = False
                    original = pinned
                    original.is_original = True
                else:
                    original.original_name = curated
        results.remove(original)
        results.insert(0, original)

    def _curated_pin(self, results: List[MusicInfo], original: MusicInfo, curated_norm: str) -> Optional[MusicInfo]:
        """策展原唱在结果页内时，返回其最早的有效版本（无有效日期则取可用版本）"""
        group_key = self._group_key(original.name)
        target = re.sub(r"[、/]", "", curated_norm)
        matches = []
        for info in results:
            if self._group_key(info.name) != group_key or not self._is_clean_name(info.name):
                continue
            singer_compact = re.sub(r"[、/]", "", info.singer.lower())
            if target in singer_compact:
                matches.append(info)
        if not matches:
            return None
        matches.sort(key=lambda x: (0 if self._is_valid_date(x.publish_time) else 1, x.publish_time))
        return matches[0]

    def _group_choice(self, metas, kw_artists: set, album_times: dict) -> Optional[MusicInfo]:
        """组内多簇竞争选原唱候选

        metas: [(cluster, clean, ref_clean), ...]
        kw_artists 非空时，候选必须属于其中某个歌手（搜索词含歌手名的约束）；
        有效被引用锚点日期最早者优先，其次簇大小 (size, referenced, 有效日期, -日期)。
        全部候选被约束过滤时返回 None，由调用方决定是否回退重算。
        """
        best = None  # 兜底: 簇大小优先 (size, referenced, valid, -date)
        best_ref = None  # 有效被引用锚点竞争: 按被引用锚点日期最早者
        for cluster, clean, ref_clean in metas:
            candidate, eff, referenced, ref_eff = self._cluster_candidate(
                cluster, clean, ref_clean, album_times
            )
            if candidate is None:
                continue
            if kw_artists and not (self._singer_artists(candidate) & kw_artists):
                continue
            score = (
                len(cluster),
                referenced,
                0 if self._is_valid_date(eff) else 1,
                -eff if eff > 0 else 0,
            )
            if best is None or score > best[0]:
                best = (score, candidate)
            # 有有效日期被引用锚点的簇：按被引用锚点日期最早者优先
            if ref_eff and self._is_valid_date(ref_eff):
                if best_ref is None or ref_eff < best_ref[1]:
                    best_ref = (candidate, ref_eff)
        if best_ref:
            return best_ref[0]
        if best:
            return best[1]
        return None

    def _cluster_candidate(self, cluster, clean, ref_clean, album_times):
        """簇内原唱候选：被引用锚点 > 全部干净候选 > 专辑日期 > 热度顺序

        返回 (候选, 生效日期, 是否有被引用锚点, 被引用锚点的最早有效日期)。
        fee=8（无版权/盗版条目）降级仅作用于被引用锚点池：
        翻唱上传日期可能早于原版，需依赖 fee 区分；专辑/日期池中
        原唱本身也可能是 fee=8（如洛天依系作品），不能降级。
        """
        ref_timed = [i for i in ref_clean if self._is_valid_date(i.publish_time)]
        if ref_timed:
            c = self._pick_best(ref_timed)
            return c, c.publish_time, True, c.publish_time
        ref_eff = 0
        for i in ref_clean:
            if self._is_valid_date(i.publish_time):
                ref_eff = i.publish_time
                break
        timed = [i for i in clean if self._is_valid_date(i.publish_time)]
        if timed:
            timed.sort(key=lambda x: x.publish_time)
            return timed[0], timed[0].publish_time, bool(ref_clean), ref_eff
        alb = [
            (info, album_times.get(info.album_id, 0))
            for info in clean
            if self._is_valid_date(album_times.get(info.album_id, 0))
        ]
        if alb:
            alb.sort(key=lambda x: x[1])
            return alb[0][0], alb[0][1], bool(ref_clean), ref_eff
        return clean[0], 0, bool(ref_clean), ref_eff

    @staticmethod
    def _pick_best(items: List[MusicInfo]) -> MusicInfo:
        """fee!=8（无版权/盗版条目）优先，其次发布日期最早"""
        items = sorted(items, key=lambda x: (1 if x.fee == 8 else 0, x.publish_time))
        return items[0]

    def _clusters(self, group: List[MusicInfo]) -> List[List[MusicInfo]]:
        """将同名组按 originSongSimpleData 拆分为真正同曲的簇

        - 无 origin 的成员是锚点，各自成簇
        - origin 指向组内锚点的成员挂到该锚点簇
        - origin 未在组内的成员按 origin id 自成一簇
        - 歌手家族（锚点歌手 / origin 原曲歌手）相同的簇合并，
          处理原唱多个录音版本（如王菲《红豆》1998/1999/2009）被拆开的情况
        """
        clusters = {}
        anchors = {}
        for info in group:
            if info.origin_song_id:
                continue
            key = "anchor:" + info.songmid
            clusters.setdefault(key, []).append(info)
            anchors[info.songmid] = key
        for info in group:
            if not info.origin_song_id:
                continue
            key = anchors.get(info.origin_song_id) or ("origin:" + info.origin_song_id)
            clusters.setdefault(key, []).append(info)

        families = {}
        for key, members in clusters.items():
            fam = set()
            if key.startswith("origin:"):
                for a in members:
                    fam |= {self._sanitize_artist(x) for x in a.origin_artists if x}
            else:
                for m in members:
                    fam |= self._singer_artists(m)
            families[key] = fam

        merged = {}
        for key, members in clusters.items():
            fam = families[key]
            target = None
            for mk in list(merged):
                if fam & families[mk]:
                    target = mk
                    break
            if target is None:
                merged[key] = list(members)
            else:
                merged[target].extend(members)
                families[target] |= fam

        return list(merged.values())

    def _referenced_anchors(self, cluster: List[MusicInfo]) -> List[MusicInfo]:
        """簇内被翻唱引用的锚点版本（原曲确实在搜索结果中的版本）"""
        anchor_ids = {info.songmid for info in cluster if not info.origin_song_id}
        referenced = {info.origin_song_id for info in cluster if info.origin_song_id in anchor_ids}
        return [info for info in cluster if not info.origin_song_id and info.songmid in referenced]

    @staticmethod
    def _is_valid_date(ts: int) -> bool:
        """发布日期有效性（本地时区，与显示一致）

        无效日期:
            1. 早于 1975 年（占位假日期，如韩红《天路》1971-01-01）
            2. 2000 年后的 1 月 1 日（平台占位日期，如蔡明希 2015-01-01、
               费翔 2000-01-01）；2000 年前的 1 月 1 日多为真实发行
               （朴树《New Boy》1999-01-01、黄仲昆 1994-01-01）
        """
        if not ts or ts <= 0:
            return False
        try:
            dt = datetime.datetime.fromtimestamp(ts / 1000)
        except (OSError, OverflowError, ValueError):
            return False
        if dt.year < 1975:
            return False
        if dt.month == 1 and dt.day == 1 and dt.year >= 2000:
            return False
        return True

    def _fetch_album_publish_times(self, album_ids: set) -> dict:
        """批量获取专辑发布时间 (毫秒时间戳)，用于歌曲发布时间缺失时的兜底"""
        if not album_ids:
            return {}
        result = {}

        def fetch(aid):
            try:
                resp = self._eapi_post(f"/api/album/{aid}", {})
                album = resp.get("album") or {}
                pt = int(album.get("publishTime") or 0)
                if pt > 0:
                    result[aid] = pt
            except Exception:
                pass

        try:
            with ThreadPoolExecutor(max_workers=6) as ex:
                list(ex.map(fetch, album_ids))
        except Exception as e:
            logger.warning(f"网易获取专辑发布时间失败: {e}")
            return {}
        if result:
            logger.debug(f"网易专辑发布时间获取: {len(result)}/{len(album_ids)}")
        return result

    @staticmethod
    def _singer_artists(info: MusicInfo) -> set:
        """规范化歌手名集合（含括号别名，如 冯沁苑(买辣椒也用券) -> 冯沁苑(买辣椒也用券)、买辣椒也用券）"""
        artists = set()
        for raw in info.singer.split("、"):
            if not raw:
                continue
            artists.add(NetEaseMusicSource._sanitize_artist(raw))
            for m in _PAREN_ALIAS_RE.finditer(raw):
                alias = NetEaseMusicSource._sanitize_artist(m.group(1))
                if alias:
                    artists.add(alias)
        return artists

    @staticmethod
    def _sanitize_artist(name: str) -> str:
        """规范化歌手名（小写 + 去除首尾标点，如 \"周杰伦-\" -> \"周杰伦\"）"""
        text = (name or "").strip().lower()
        return _ARTIST_EDGE_RE.sub("", text)

    @staticmethod
    def _strip_version_suffix(name: str) -> str:
        """反复去除歌名末尾的括号版本后缀，如 \"七里香 (钢琴版) [原唱: 周杰伦]\" -> \"七里香\" """
        text = name or ""
        while True:
            stripped = _VERSION_SUFFIX_RE.sub("", text)
            if stripped == text:
                return stripped
            text = stripped

    @classmethod
    def _group_key(cls, name: str) -> str:
        """规范化歌名分组键（忽略末尾括号版本后缀）"""
        text = cls._strip_version_suffix(name).strip().lower()
        return text or cls._normalize_keyword(name)

    @classmethod
    def _is_clean_name(cls, name: str) -> bool:
        """歌名是否无版本后缀（Live/伴奏/翻唱版等）"""
        return cls._strip_version_suffix(name) == (name or "")

    @staticmethod
    def _normalize_keyword(text: str) -> str:
        """规范化关键词（去首尾空白 + 小写）"""
        return (text or "").strip().lower()

    # ── 获取播放URL ─────────────────────────────────

    def get_music_url(self, info: MusicInfo, quality: str = "128k") -> Optional[str]:
        song_id = info.songmid
        br_map = {"flac24bit": "hires", "flac": "999000", "320k": "320000", "128k": "128000"}
        br = br_map.get(quality, "128000")

        # 使用 eapi 接口获取播放URL
        url = "/api/song/enhance/player/url"
        data = {"ids": f"[{song_id}]", "br": int(br)}
        try:
            resp = self._eapi_post(url, data)
            urls = resp.get("data", [])
            if urls and urls[0].get("url"):
                play_url = urls[0]["url"]
                return play_url
        except Exception as e:
            # 单个候选失败属兜底流程常态，降为 debug 避免刷屏（外层有汇总日志）
            logger.debug(f"网易获取URL失败 [{info.songmid}]: {e}")

        # 回退到 weapi
        try:
            weapi_url = "/song/enhance/player/url/v1"
            weapi_data = {"ids": f"[{song_id}]", "level": quality, "encodeType": "aac"}
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
        data = {"id": info.songmid, "lv": -1, "tv": -1, "rv": -1}
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

    # ── 账号登录（扫码登录，支持 VIP 歌曲播放与 VIP 歌词） ──
    #
    # 登录流程（与 YesPlayMusic 一致，采用官方扫码接口）:
    #   1. login_qr_key()   获取一次性 unikey（eapi /api/login/qrcode/unikey）
    #   2. 用 unikey 生成二维码内容 https://music.163.com/login?codekey=<unikey>
    #   3. login_qr_check() 轮询扫码状态: 800 已过期 / 801 等待扫码 /
    #      802 已扫码待确认 / 803 授权成功（响应携带登录 Cookie）
    #   4. 将 Cookie 应用到会话（apply_cookie_str），并持久化以便下次启动恢复
    #
    # 说明: 扫码接口走 eapi（与搜索等核心功能同一通道，兼容性最好）；
    # 部分环境 weapi 域名会被风控拦截（空响应），eapi 不受影响。

    WY_LOGIN_QR_BASE = "https://music.163.com/login?codekey="
    # 需要保留的基础 cookie（登录 Cookie 清除时不清除它们）
    _BASE_COOKIE_NAMES = {"os", "appver", "channel", "_ntes_nuid", "__remember_me"}
    # Set-Cookie 属性分段（解析 Cookie 字符串时跳过，避免污染会话）
    _COOKIE_ATTR_NAMES = {
        "expires", "max-age", "domain", "path", "secure", "samesite",
        "httponly", "priority", "partitioned",
    }

    def login_qr_key(self) -> Optional[str]:
        """获取扫码登录 unikey，失败返回 None"""
        try:
            resp = self._eapi_post("/api/login/qrcode/unikey", {"type": 1})
            unikey = resp.get("unikey")
            if resp.get("code") == 200 and unikey:
                return unikey
            logger.debug(f"网易获取登录 unikey 失败: code={resp.get('code')}")
        except Exception as e:
            logger.warning(f"网易获取登录 unikey 异常: {e}")
        return None

    def login_qr_check(self, key: str) -> dict:
        """查询扫码登录状态

        803 成功时登录 Cookie 可能位于响应体 cookie 字段，也可能仅存在于
        Set-Cookie 响应头（不同接口实现），本方法将两种情况合并返回；
        若响应体含 cookies 字典（name->value），一并转换。

        Returns:
            {code, cookie?}: 800 已过期 / 801 等待扫码 / 802 已扫码待确认 /
            803 授权成功（含 cookie 字段）
        """
        signed = wy_eapi("/api/login/qrcode/client/login", {"key": key, "type": 1})
        resp = self._session.post(f"{WY_EAPI_BASE}/api/login/qrcode/client/login", data=signed, timeout=15)
        try:
            result = resp.json()
        except ValueError:
            result = {}
        if not isinstance(result, dict):
            result = {}
        if not result.get("cookie"):
            cookies_dict = result.get("cookies")
            if isinstance(cookies_dict, dict) and cookies_dict:
                result["cookie"] = "; ".join(f"{k}={v}" for k, v in cookies_dict.items())
            else:
                header_cookie = resp.headers.get("Set-Cookie") if resp.headers is not None else None
                if header_cookie:
                    result["cookie"] = header_cookie
        return result

    def apply_cookie_str(self, cookie_str: str) -> None:
        """将 Set-Cookie 格式字符串解析并应用到会话（支持 'HTTPOnly' 等属性标记）

        如 "MUSIC_U=xxx; Max-Age=15552000; Expires=...; Path=/; Domain=.music.163.com; HTTPOnly"
        仅取 key=value 对，跳过属性标记（无 '=' 或属于 Expires/Max-Age/Domain 等属性名）。
        """
        if not cookie_str:
            return
        applied = 0
        for part in cookie_str.split(";"):
            part = part.strip()
            if not part or "=" not in part:
                continue
            name, _, value = part.partition("=")
            name = name.strip()
            value = value.strip().strip('"')
            if not name or not value:
                continue
            if name.lower() in self._COOKIE_ATTR_NAMES:
                continue
            try:
                self._session.cookies.set(name, value, domain=".music.163.com")
                applied += 1
            except Exception as e:
                logger.debug(f"网易设置 cookie 失败 [{name}]: {e}")
        if applied:
            logger.info(f"网易云登录 Cookie 已应用: {applied} 项")

    def get_cookie_str(self) -> str:
        """导出会话中的音乐域名 cookie（用于持久化保存，登录退出后失效）"""
        parts = []
        for cookie in self._session.cookies:
            domain = cookie.domain or ""
            if "music.163.com" not in domain:
                continue
            parts.append(f"{cookie.name}={cookie.value}")
        return "; ".join(parts)

    def is_logged_in(self) -> bool:
        """是否已登录（存在非空 MUSIC_U cookie）"""
        for cookie in self._session.cookies:
            if cookie.name == "MUSIC_U" and cookie.value:
                return True
        return False

    def clear_cookies(self) -> None:
        """清除登录 Cookie（保留 os/appver 等基础 cookie）"""
        cleared = 0
        for cookie in list(self._session.cookies):
            if cookie.name in self._BASE_COOKIE_NAMES:
                continue
            try:
                self._session.cookies.clear(
                    domain=cookie.domain or ".music.163.com",
                    path=cookie.path or "/",
                    name=cookie.name,
                )
                cleared += 1
            except Exception:
                pass
        if cleared:
            logger.info(f"网易云登录 Cookie 已清除: {cleared} 项")

    def fetch_login_profile(self) -> Optional[dict]:
        """获取当前登录用户信息（未登录/失败返回 None）

        Returns:
            {"nickname": str, "avatar_url": str, "user_id": int,
             "vip_type": int, "has_music_package": bool}
            vip_type 取值（服务器按账号返回）:
                0 免费用户 / 1 音乐包 / 11 黑胶VIP / 20 黑胶SVIP
            音乐包还可能以 vipRights.associator 形式返回（部分账号）。
        """
        try:
            resp = self._eapi_post("/api/nuser/account/get", {})
            if resp.get("code") == 200 and resp.get("profile"):
                profile = resp["profile"]
                rights = profile.get("vipRights") or {}
                return {
                    "nickname": profile.get("nickname", ""),
                    "avatar_url": profile.get("avatarUrl", ""),
                    "user_id": profile.get("userId", 0),
                    "vip_type": int(profile.get("vipType") or 0),
                    "has_music_package": bool(rights.get("associator")),
                }
            logger.debug(f"网易获取登录用户信息失败: code={resp.get('code')}")
        except Exception as e:
            logger.warning(f"网易获取登录用户信息异常: {e}")
        return None

    # ── 加密请求辅助 ────────────────────────────────

    def _eapi_post(self, path: str, data: dict) -> dict:
        """发送 eapi 加密请求"""
        signed = wy_eapi(path, data)
        resp = self._session.post(f"{WY_EAPI_BASE}{path}", data=signed, timeout=15)
        return resp.json()

    def _weapi_post(self, path: str, data: dict) -> dict:
        """发送 weapi 加密请求"""
        signed = wy_weapi(data)
        resp = self._session.post(f"{WY_API_BASE}{path}", data=signed, timeout=15)
        return resp.json()
