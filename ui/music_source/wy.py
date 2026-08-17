"""网易云音乐 音源插件"""

import datetime
import json
import logging
import random
import re
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Tuple
from urllib.parse import urlencode

import requests

from ui.music_source.base import BaseMusicSource, MusicInfo
from ui.music_source.utils import decode_name, format_singer, wy_eapi, wy_weapi

logger = logging.getLogger("music_source.wy")

WY_EAPI_BASE = "https://interface3.music.163.com/eapi"
WY_API_BASE = "https://music.163.com/weapi"
# weapi 加密体的备用域名前缀：部分接口（如 /user/playlist、老版
# /playlist/detail）在 /weapi/ 前缀下返回空响应体，须改用 /api/ 前缀
# 且参数放 URL 查询串（与 NeteaseCloudMusicApi 同路由）
WY_API_ALT_BASE = "https://music.163.com/api"

# 歌名末尾的括号版本后缀（Live/伴奏/翻唱/中文版等标注），分组判定原唱时忽略。
# 仅剥离"含版本词"的括号：真实歌名一部分的括号（如 幹物女(WeiWei)、
# 逃避现实 (feat.洛天依)）不属于版本后缀，不剥离，避免原版被误判"不干净"。
_VERSION_WORDS = (
    "live", "cover", "伴奏", "翻唱", "翻自", "原唱", "remix", "重混",
    "重置", "重录", "官方", "official", "纯音乐", "钢琴", "吉他", "现场",
    "demo", "inst", "instrumental", "中文版", "日文版", "粤语版", "国语版",
    "日语", "填词", "合唱版", "男声版", "女声版", "mv", "清唱", "版",
    "vocaloid", "翻调",
)
_VERSION_SUFFIX_RE = re.compile(
    r"[\s\u3000]*[（(\[]"
    r"[^（）()\[\]］]*?(?:"
    + "|".join(_VERSION_WORDS)
    + r")[^（）()\[\]］]*[）)\]][\s\u3000]*$",
    re.IGNORECASE,
)
# 歌名开头的【标签】前缀（如【星尘】尘降、【诗岸翻唱】落花后日谈），
# 分组时剥离；含版本词的标签视为版本标记（【翻唱】/【伴奏】/【英文版】等）
_TAG_PREFIX_RE = re.compile(r"^【[^】]*】[\s\u3000]*")
_VERSION_TAG_RE = re.compile(
    r"^【[^】]*?(?:" + "|".join(_VERSION_WORDS) + r")[^】]*】"
)
# 歌手名首尾标点（"周杰伦-"、"BEYOND." 等），规范化时去除
_ARTIST_EDGE_RE = re.compile(r"^[\s\-\.\,、'‘’。·]+|[\s\-\.\,、'‘’。·]+$")
# 歌手名中的括号别名（如 "冯沁苑(买辣椒也用券)" -> "买辣椒也用券"）
_PAREN_ALIAS_RE = re.compile(r"[（(]([^（）()]*)[）)]")

# 原唱歌曲 ID 策展表（网易平台原唱版本的歌曲 ID，平铺集合）：
# 与网易云平台一致的"ID 级判定"——搜索结果中出现这些 ID 对应的歌曲
# 即视为原唱（平台官方做法：同一歌名的多个官方版本都标原唱，如《脑壳疼》
# 的 洛天依、ilem 版与 洛天依Official、ilem 版）。
# 适用场景:
#   1. 算法无法判定原唱的歌（无 origin 引用/占位日期/同名同曲多版本竞争），
#      直接指定平台原唱版本 ID（如《苍蝇》《大吉》《脑壳疼》）
#   2. 原唱版本不在歌名搜索结果页内，但平台有音源（如《再回首》苏芮版、
#      《耶利亚女郎》刘文正版、《后来》原曲 Kiroro《未来へ》），
#      搜到这些版本时全局匹配标原唱（跨歌名匹配，如搜"未来へ"标原唱）
# 规则: 命中 ID 的全部版本置顶（保持原热度顺序）并标原唱标签。
CURATED_ORIGINAL_IDS = {
    "477936261",  # 深夜诗人 - 洛天依Official、言和、ilem
    "3392513819",  # 苍蝇 - ilem、赤羽
    "3392513823",  # 大吉 - ilem、洛天依Official
    "2716916349",  # 脑壳疼 - 洛天依、ilem
    "2748721770",  # 脑壳疼 - 洛天依Official、ilem
    "3392501133",  # 一样 - ilem、洛天依Official
    "5283447",  # 冬天里的一把火 - 高凌风
    "520564190",  # 弯弯的月亮 - 陈汝佳
    "259710",  # 不必太在意 - 蓝心湄
    "301577",  # 明天你是否依然爱我 - 王芷蕾
    "29739000",  # 九儿 - 胡莎莎
    "3365424126",  # 我是秦始皇 - WOVOP、洛天依
    "3392500306",  # 写给我第一个喜欢的女孩的歌 - ilem、洛天依Official
    "33705517",  # 飞跃乌托邦 - 乐正绫、动点
    "3403313136",  # 我们正驶向黎明 - 言和
    "459831628",  # 万神纪 - 海鲜面、星尘
    "2631318598",  # 霜雪千年 - 洛天依、乐正绫
    "535915977",  # 异样的风暴中心 - 杉田朗、洛天依
    "3395708493",  # 人是猫 - 张卡斯、洛天依
    "3417872225",  # 加班的人啊 - 墨老板、洛天依Official
    "2034615993",  # 弑神者 - 洛天依Official、QGRay
    "2033056779",  # 逃避现实 (feat.洛天依) - QGRay、洛天依
    "2626464399",  # 烂掉的白月光 - 洛天依、乐正绫、路明熹
    "1913873767",  # 一梦千宵 - 苏逸_Suyi、洛天依Official
    "3392512962",  # 白鸟过河滩 - ilem、洛天依Official
    "34775318",  # 幹物女(WeiWei) - Z新豪、洛天依、乐正绫
    "3392513818",  # 上山岗 - ilem、洛天依Official
    "3408812844",  # 众音絮响 - 天崎默、洛天依、乐正绫
    "3408811234",  # 他的四季 - 天崎默、洛天依、乐正绫
    "2621707544",  # 逃！ - 乐正绫、Mara
    "3408812842",  # 卡诺与时间塔的吟歌 - 天崎默、洛天依、言和
    "1966826240",  # 在下，言和 feat.言和 - iKz、言和
    "3417726249",  # 无需夏天 - Melody_Fall、星尘、洛天依
    "3340764179",  # 再见了，我以恨意为燃料的人生... - Calia-林焰、星尘
    "3418366045",  # 伴行 - 朔时、鲨潜、诗岸、星尘
    "2161506239",  # 只有春天，禁止入内 - wukino、星尘infinity
    "2656798595",  # 100%矛盾集合体 - MaxXing、星尘infinity
    "2051762956",  # 人偶之梦 (星尘Infinity 2022Ver.) - 星尘
    "2727925602",  # 落花后日谈 - 乌托邦P、星尘
    "2716065032",  # 孤独症侯群 - Calia-林焰、星尘infinity
    "3409748037",  # 有一天我会放弃音乐 - grassP、星尘、诗岸
    "1921741824",  # 巫山云 (星尘Infinity Version) - 旅行的蜗牛、星尘
    "3355532615",  # 彼岸花（诗岸&ナツメイツキ） - しょりん、诗岸、ナツメイツキ
    "2705295646",  # 请不要带我走。 - 奥莉安多幻想曲、诗岸
    "2728468840",  # 惊蛰正中央 - 诗岸、歌爱ユキ、立入禁止
    "1409603530",  # 青鸟衔风 - 忘川风华录、海伊、诗岸
    "2680109503",  # 虚构义 - MOCKER44.、诗岸
    "3417341746",  # 毁了我吧 - mayauzz、诗岸
    "3415055899",  # 我已见过夏天 - 见过夏天P、星尘、诗岸
    "3384372607",  # 遗书（诗岸） - しょりん、诗岸
    "3365367154",  # 因为今天就要死去（feat.诗岸） - 啰嗦、诗岸
    "3357617299",  # 我们终会在大地深处重逢 (feat. 诗岸) - 神经罐头、诗岸
    "2717577716",  # 避春讳 - 穗小黎、诗岸
    "2101145263",  # 如果只转身后退就能回到那个夏天？ - 诗岸
    "287511",  # 再回首 - 苏芮
    "118997",  # 耶利亚女郎 - 刘文正
    "327429",  # 童年 - 张艾嘉
    "60409",  # 对面的女孩看过来 - 阿牛
    "22746049",  # 未来へ（后来 原曲） - Kiroro
    "505474379",  # 病名は愛だった - Neru、鏡音レン、鏡音リン、z'5
    "548648148",  # 病名は愛だった - Neru、鏡音レン、鏡音リン、z'5
    "429460239",  # 世末歌者 - 乐正绫、COP
}

# 原唱补全置顶表（规范化歌名 -> 平台原唱版本歌曲 ID）：搜索词规范化歌名
# 命中且该 ID 不在搜索结果页内时（如搜"病名为爱（中文版）"结果页全是
# 中文翻唱，日语原唱 病名は愛だった 不在其中），拉取原唱版本信息插入
# 结果顶部并标原唱（CURATED_ORIGINAL_IDS 跨歌名匹配的加强版：原唱不在
# 结果页内也置顶）；仅第一页生效，避免翻页时重复插入。
PIN_ORIGINAL_SONGS = {
    "病名为爱": "548648148",  # 病名は愛だった - Neru、鏡音レン、鏡音リン、z'5
}

# 徽章原唱表（规范化歌名 -> 原唱）：算法候选不可靠时，置顶结果页最热
# 干净版本并由徽章显示真实原唱名（is_original + original_name）。
# 适用场景:
#   1. 无音源原唱表：网易平台没有原唱版本录音的歌曲，搜索结果页内全是
#      翻唱/大众熟知版本，算法只能置顶最热版本，由徽章显示真实原唱名
#      （原唱版本缺失，无法用 ID 制修正，如《突然的自我》黄小琥）
#   2. 原唱版本不在结果页：平台有原唱录音但搜索页内搜不到（如《弯弯的月亮》
#      陈汝佳、《明天你是否依然爱我》王芷蕾），算法会误把更火的大众熟知版
#      （刘欢/童安格）标为原唱，须用徽章覆盖
# 命中时直接置顶最热干净版本并打徽章（先于算法候选与特别规则）；若该版本的
# 歌手本身就是原唱（结果页恰好有原唱版本），则只标原唱不画蛇添足打徽章。
NO_SOURCE_ORIGINALS = {
    "突然的自我": "黄小琥",
    "月亮代表我的心": "陈芬兰",
    "普通disco": "洛天依、言和",
    "大碗宽面": "吴亦凡",
    "弯弯的月亮": "陈汝佳",
    "明天你是否依然爱我": "王芷蕾",
    "冬天里的一把火": "高凌风",
}

# 同名不同曲表：多首互不相关的歌曲同名（如《蝴蝶》陶喆版与洛天依版、
# 《哑巴》刘维版与Z新豪版、《千秋不负》妄尘组与洛天依Official版、
# 《超能力》邓紫棋版与后海大鲨鱼版、《Side By Side》Kay Starr版与言和版、
# 《自由落体》Winky诗版/FREEFALL版/梁咏琪版等、《彼岸花》洛天依/王菲/周深版、
# 《Babel》Gustavo Bravetti/Califair版等），算法无法判定用户搜索意图，
# 命中时对整组结果不标记任何原唱。
NO_ORIGINAL_SONGS = {
    "蝴蝶",
    "哑巴",
    "千秋不负",
    "超能力",
    "side by side",
    "自由落体",
    "彼岸花",
    "babel",
}

# 特别规则：封茗囧菌、洛少爷、双笙（陈元汐）以翻唱虚拟歌手曲目为主，
# 搜索结果中同时出现歌手含这些歌手的歌曲与歌手含虚拟歌手的同名歌曲时，
# 只标虚拟歌手版本为原唱置顶——有"洛天依Official"只标全部 Official 版本，
# 否则标歌手里含虚拟歌手且发表最早的一个（见 _mark_original 规则 17）。
_COVER_ARTISTS = {"封茗囧菌", "洛少爷", "双笙（陈元汐）"}  # 以翻唱为主的歌手（规范化后）
# 虚拟歌手（规范化后）：规则 17 的"原唱方"，如《普通disco》原唱为
# 洛天依、言和，搜索出现翻唱歌手版本时只标虚拟歌手版本为原唱。
_VOCAL_SYNTH_BASES = {
    "洛天依",
    "言和",
    "乐正绫",
    "乐正龙牙",
    "徵羽摩柯",
    "墨清弦",
    "星尘",
    "海伊",
    "诗岸",
    "苍穹",
    "赤羽",
    "星尘minus",
    "牧心",
    "艾可",
    "心华",
    "初音未来",
}
# 虚拟歌手中仅洛天依有官方账号（洛天依Official）
_VOCAL_SYNTH_OFFICIALS = {"洛天依official"}
_VOCAL_SYNTH_ARTISTS = _VOCAL_SYNTH_BASES | _VOCAL_SYNTH_OFFICIALS

# 规则 17 排除表（规范化歌名）：结果中出现的虚拟歌手版本并非原唱的歌曲
# （如《世末歌者》原唱为乐正绫，洛天依Official 版是官方翻唱；《芒种》
# 同名不同曲，2016 年乐正绫版与 2019 年音阙诗听版互不相干，规则 17
# 会误把乐正绫版当洛少爷翻唱的原唱），结果中同时出现翻唱歌手版本与
# 虚拟歌手版本时，禁止规则 17 把虚拟歌手版置顶。
# 正常场景由 CURATED_ORIGINAL_IDS 标乐正绫原版，此表兜底防止原版
# 不在结果页时误判。
_COVER_RULE_EXCLUDED_SONGS = {"世末歌者", "芒种"}

# ── 百度百科原唱兜底 ─────────────────────────────────
#
# 算法无法判定原唱（无 origin 引用/无有效日期/结果页内无原唱版本）时，
# 异步查询百度百科词条的「原唱」卡片字段回填：优先在结果中匹配该歌手的
# 版本标原唱置顶，匹配不到则对最热干净版本打徽章（同 NO_SOURCE_ORIGINALS
# 行为）。查询在后台线程执行，不阻塞搜索返回；结果由 UI 层注册的回调刷新。
# 实现移植自独立工具 baike_singer.py（百度百科移动版页面解析）。


class _BaikeOriginalLookup:
    """百度百科原唱歌手查询客户端（异步兜底用）

    解析策略（参考独立工具 baike_singer.py）:
        1. 直接访问词条页 /item/<歌名>，解析 __NEXT_DATA__ 义项数据
           定位同名歌曲词条（义项描述含歌曲/演唱等关键词的候选）
        2. /search/word 搜索接口兜底（被安全验证限流时自动跳过）
        3. 候选词条逐个抓取，找到含「原唱」卡片字段的即返回

    线程安全: 每次 lookup() 创建独立 Session（requests.Session 非线程安全，
    参考工具官方建议多线程场景各线程独立实例）；实例级搜索接口健康标记
    由锁保护。
    """

    WAP_BAIKE_BASE = "https://wapbaike.baidu.com"
    _UA = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 "
        "Mobile/15E148 Safari/604.1"
    )
    # 词条页信息卡（index_cardName__xxx 字段名 / index_cardValue__xxx 值）
    _CARD_ITEM_RE = re.compile(
        r'<div class="index_cardName__[^"]*">([^<]+)</div>'
        r'\s*<div class="index_cardValue__[^"]*">(.*?)</div>',
        re.DOTALL,
    )
    _TAG_RE = re.compile(r"<[^>]+>")
    _WS_RE = re.compile(r"\s+")
    _NEXT_DATA_RE = re.compile(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        re.DOTALL,
    )
    _ITEM_LINK_RE = re.compile(r'href="(/item/[^"]*)"[^>]*>(.*?)</a>', re.DOTALL)
    _SONG_KEYWORDS = ("歌曲", "演唱", "歌手", "音乐", "歌", "曲")
    _SONG_CLASSIFY = ("音乐作品", "歌曲", "音乐")
    _CAPTCHA_PATHS = ("captcha", "anticrawl")

    def __init__(
        self,
        timeout: float = 10,
        max_retries: int = 1,
        request_interval: float = 0.5,
        max_candidates: int = 4,
    ) -> None:
        self.timeout = timeout
        self.max_retries = max_retries
        self.request_interval = request_interval
        self.max_candidates = max_candidates
        self._search_healthy = True
        self._lock = threading.Lock()

    def lookup(self, name: str) -> Optional[str]:
        """按歌名查询原唱歌手，无词条/被拦截/网络失败返回 None（异常内部消化）"""
        if not name or not name.strip():
            return None
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": self._UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            }
        )
        try:
            return self._extract_by_name(name.strip(), session)
        except Exception as e:
            logger.debug(f"百度百科原唱查询异常 [{name}]: {e}")
            return None

    def _fetch(self, session: requests.Session, url: str) -> Optional[requests.Response]:
        last_error = None
        for attempt in range(self.max_retries):
            try:
                resp = session.get(url, timeout=self.timeout, allow_redirects=True)
                if resp.status_code == 200 and resp.text:
                    if self.request_interval:
                        time.sleep(self.request_interval)
                    return resp
                last_error = f"HTTP {resp.status_code}"
            except requests.RequestException as exc:
                last_error = exc
            if attempt < self.max_retries - 1:
                time.sleep(0.5)
        logger.debug(f"百度百科抓取失败 ({url}): {last_error}")
        return None

    def _is_captcha(self, resp: requests.Response) -> bool:
        path = urllib.parse.urlparse(resp.url).path.lower()
        if any(p in path for p in self._CAPTCHA_PATHS):
            return True
        return "安全验证" in resp.text[:2000]

    @staticmethod
    def _parse_next_data(html: str) -> Optional[dict]:
        match = _BaikeOriginalLookup._NEXT_DATA_RE.search(html)
        if not match:
            return None
        try:
            data = json.loads(match.group(1))
            return data["props"]["pageProps"]["pageData"]
        except (ValueError, KeyError, TypeError):
            return None

    @classmethod
    def _find_original_singer(cls, html: str) -> Optional[Tuple[str, str]]:
        """在词条页信息卡中查找「原唱」字段，返回 (字段名, 歌手文本)"""
        cards: List[Tuple[str, str]] = []
        for match in cls._CARD_ITEM_RE.finditer(html):
            name = match.group(1).strip()
            value = re.sub(r"</(?:span|a)>", "/", match.group(2))
            text = cls._WS_RE.sub(" ", cls._TAG_RE.sub("", value)).strip().strip("/")
            if name and text:
                cards.append((name, text.replace(" ", "")))
        exact = [c for c in cards if c[0] == "原唱"]
        contains = [c for c in cards if "原唱" in c[0]]
        for name, text in exact or contains:
            return name, text
        return None

    @staticmethod
    def _is_song_lemma(candidate: dict) -> bool:
        classify = " ".join(candidate.get("classify") or [])
        desc = candidate.get("lemmaDesc") or ""
        return any(k in classify for k in _BaikeOriginalLookup._SONG_CLASSIFY) or any(
            k in desc for k in _BaikeOriginalLookup._SONG_KEYWORDS
        )

    def _item_candidates(
        self, session: requests.Session, name: str
    ) -> Tuple[str, Optional[requests.Response], List[Tuple[str, str, str]]]:
        """直接访问 /item/<歌名> 词条页，返回 (词条名, 响应, 同名歌曲义项候选)"""
        url = self.WAP_BAIKE_BASE + "/item/" + urllib.parse.quote(name)
        resp = self._fetch(session, url)
        if resp is None:
            return name, None, []
        page = self._parse_next_data(resp.text) or {}
        title = page.get("lemmaTitle") or name
        current_id = page.get("lemmaId")
        candidates: List[Tuple[str, str, str]] = []
        for lemma in (page.get("navigation") or {}).get("lemmas") or []:
            if lemma.get("lemmaId") == current_id:
                continue
            if lemma.get("lemmaTitle") and self._is_song_lemma(lemma):
                candidates.append(
                    (
                        self.WAP_BAIKE_BASE
                        + "/item/"
                        + urllib.parse.quote(lemma["lemmaTitle"])
                        + "/" + str(lemma["lemmaId"]),
                        lemma["lemmaTitle"],
                        lemma.get("lemmaDesc") or "",
                    )
                )
        return title, resp, candidates

    def _search_candidates(
        self, session: requests.Session, name: str
    ) -> Optional[List[Tuple[str, str, str]]]:
        with self._lock:
            healthy = self._search_healthy
        if not healthy:
            return None
        resp = self._fetch(
            session, self.WAP_BAIKE_BASE + "/search/word?word=" + urllib.parse.quote(name)
        )
        if resp is None:
            return None
        if self._is_captcha(resp):
            with self._lock:
                self._search_healthy = False
            return None
        path = urllib.parse.urlparse(resp.url).path
        if path.startswith("/item/"):
            return [(resp.url, urllib.parse.unquote(path.split("/")[2]), "")]
        candidates: List[Tuple[str, str, str]] = []
        seen = set()
        for match in self._ITEM_LINK_RE.finditer(resp.text):
            url = self.WAP_BAIKE_BASE + match.group(1)
            if url in seen:
                continue
            seen.add(url)
            title = self._WS_RE.sub(" ", self._TAG_RE.sub("", match.group(2))).strip()
            if not title or not any(k in title for k in self._SONG_KEYWORDS):
                continue
            candidates.append((url, title, ""))
        return candidates

    def _try_candidates(
        self, session: requests.Session, candidates: List[Tuple[str, str, str]], visited: set
    ) -> Optional[Tuple[str, str]]:
        tried = 0
        for url, title, desc in candidates:
            if url in visited:
                continue
            visited.add(url)
            if tried >= self.max_candidates:
                break
            tried += 1
            resp = self._fetch(session, url)
            if resp is None:
                continue
            result = self._find_original_singer(resp.text)
            if result is not None:
                return title, result[1]
        return None

    def _extract_by_name(self, name: str, session: requests.Session) -> Optional[str]:
        visited: set = set()
        title, resp, candidates = self._item_candidates(session, name)
        if resp is not None:
            if self._is_captcha(resp):
                logger.debug(f"百度百科词条页被安全验证拦截: {name}")
                return None
            result = self._find_original_singer(resp.text)
            if result is not None:
                return result[1]
        if candidates:
            found = self._try_candidates(session, candidates, visited)
            if found is not None:
                return found[1]
        candidates = self._search_candidates(session, name)
        if candidates:
            found = self._try_candidates(session, candidates, visited)
            if found is not None:
                return found[1]
        return None


class NetEaseMusicSource(BaseMusicSource):
    source_id = "wy"
    source_name = "网易云音乐"
    # 搜索接口 /api/search/song/list/page 服务端硬性限制每页最多返回 20 条
    # （limit>20 一律截断为 20），UI 按此数值作为该音源的每页条数
    limits = {"search": 20, "lyric": 1, "url": 1}

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
        # ── 百度百科原唱兜底状态 ──
        self._baike_enabled = True  # 开关（由 UI 层同步，见 set_baike_enabled）
        self._original_fallback_callback = None  # 异步回填回调（UI 层注册）
        self._baike_lookup = _BaikeOriginalLookup()
        # 内存缓存：规范化歌名 -> 原唱名（空串 = 负缓存，避免重复查询）
        self._baike_cache = {}
        # 进行中的查询：规范化歌名 -> 等待回填的结果列表（同歌多页并发去重）
        self._baike_pending: dict = {}
        self._baike_lock = threading.Lock()
        # 原唱补全置顶缓存：歌曲 ID -> 原始歌曲响应列表（避免重复请求）
        self._pin_original_cache: dict = {}

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
            self.set_search_total((resp.get("data") or {}).get("totalCount"))
            results = self._parse_search_result(raw_list)
            try:
                self._mark_original(results, keyword, page)
            except Exception as e:
                logger.warning(f"网易标记原唱失败: {e}")
            # 算法无原唱候选时，异步触发百度百科兜底（不阻塞搜索返回）
            self._maybe_baike_fallback(results, keyword)
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
        maxbr = int(priv.get("maxbr") or 0)
        # 无 privilege（eapi 歌单详情/老格式接口）时从音质子对象的 br/bitrate 反推
        if not maxbr:
            for key in ("sq", "sqMusic", "h", "hMusic", "m", "mMusic", "l", "lMusic"):
                sub = item.get(key) or {}
                br = int(sub.get("br") or sub.get("bitrate") or 0)
                if br > maxbr:
                    maxbr = br

        # flac (SQ)
        if maxbr >= 999000:
            sq = item.get("sq") or item.get("sqMusic") or {}
            if sq:
                types.append({"type": "flac"})
                _types["flac"] = {"id": sq.get("id", item.get("id", ""))}

        # 320k (HQ)
        if maxbr >= 320000:
            hq = item.get("h") or item.get("hMusic") or {}
            if hq:
                types.append({"type": "320k"})
                _types["320k"] = {"id": hq.get("id", item.get("id", ""))}

        # 128k
        low = item.get("l") or item.get("lMusic") or {}
        if low:
            types.append({"type": "128k"})
            _types["128k"] = {"id": low.get("id", item.get("id", ""))}

        types.reverse()
        return types, _types

    # ── 原唱识别与置顶 ──────────────────────────────

    def _mark_original(self, results: List[MusicInfo], keyword: str, page: int = 1) -> None:
        """在搜索结果中识别原唱并置顶

        规则:
            1. 按规范化歌名（忽略 Live/伴奏/翻唱版等括号版本后缀）分组
            2. 组内按 originSongSimpleData 拆分为同曲簇（同名不同曲的
               版本互不干扰，如费玉清版与汪峰版《春天里》）
            3. 簇内优先取被翻唱引用的锚点版本（原曲确实在结果中时），
               避免翻唱上传日期早于原版造成的误判
            4. 被引用锚点存在但日期无效（平台占位日期/缺失）时仍优先：
               翻唱声明引用的就是原曲，锚点日期不可靠不代表不是原唱
            5. 簇内含无 origin 引用的干净锚点（原曲家族上传版本）时，
               优先于有有效日期的翻唱成员（如《稻香》周杰伦版本
               日期缺失而 AI 翻唱有日期）
            6. 发布日期为 1 月 1 日（占位日期）或早于 1975 年的视为无效
            7. 候选无有效歌曲发布时间时，查专辑发布日期兜底（同样过滤假日期）
            8. 专辑日期也没有时，按热度顺序（结果列表顺序）取第一条
               干净同名歌曲作为最终兜底
            9. 多个簇大小相同时，优先有被引用锚点的簇，再按候选日期最早者
            10. 搜索词是歌手名且至少 2 条结果命中（歌手搜索场景）时，歌名命中
                不足 2 条才不标记；歌名与歌手同名且歌名命中较多时（如《非人哉》），
                仍按歌曲搜索处理
            11. 搜索词中包含某歌手名（含括号别名，如 冯沁苑(买辣椒也用券)）
                且至少 2 条结果命中时，原唱必须属于该歌手（避免把翻唱置顶）；
                若该约束导致全部候选被过滤（歌手名恰为歌名一部分的误伤，
                如"我是初音未来"含"初音未来"），回退忽略约束重算
            12. 同名不同曲表（NO_ORIGINAL_SONGS）命中的搜索词，无法判定
                用户意图，不标记任何原唱
            13. 原唱 ID 策展表（CURATED_ORIGINAL_IDS）全局匹配：搜索结果
                中出现平台原唱 ID 的歌曲，全部置顶（保持原热度顺序）并标
                原唱标签（平台官方做法，支持同一歌名多原唱）
            14. 徽章原唱表（NO_SOURCE_ORIGINALS）命中（无原唱音源或原唱版本
                不在结果页）：直接置顶最热干净版本，由徽章显示真实原唱名，
                先于算法候选与特别规则（避免更火的大众熟知版被误标，如
                《弯弯的月亮》刘欢版）；其余无算法候选的歌曲不标记
            15. 搜索词精确命中某组（含版本词括号，如 彼岸花（诗岸&ナツメイツキ））
                时只允许该组产生候选，避免模糊匹配的同名不同曲抢位
            16. 仅将原唱前移置顶并打上原唱标签，其余结果保持热度排序不变
            17. 翻唱歌手规则：结果中同时出现歌手含"封茗囧菌/洛少爷/双笙（陈元汐）"
                的歌曲与歌手含虚拟歌手（洛天依/言和/乐正绫等，见
                _VOCAL_SYNTH_ARTISTS）的同名歌曲时，翻唱歌手从原唱候选
                剔除，只标虚拟歌手版本：有"洛天依Official"（仅洛天依有
                官方账号）只标全部 Official 版本，否则标歌手里含虚拟歌手
                且发表最早的一个（两首须为不同歌曲，歌手集合互斥防合唱误判）
            18. 原唱补全置顶（PIN_ORIGINAL_SONGS）：搜索词规范化歌名命中且
                原唱版本不在结果页内时（如搜"病名为爱（中文版）"结果页
                全是中文翻唱），拉取原唱版本插入结果顶部并标原唱；
                仅第一页生效，避免翻页重复插入
        """
        if len(results) < 1:
            return
        kw = self._normalize_keyword(keyword)
        if not kw:
            return
        # 同名不同曲表命中：多首互不相关的同名歌曲，不标记原唱
        if self._group_key(kw) in NO_ORIGINAL_SONGS:
            return
        # 原唱 ID 全局匹配：结果中出现策展原唱 ID 的歌曲，全部置顶并标原唱
        matched = [info for info in results if info.songmid in CURATED_ORIGINAL_IDS]
        if matched:
            for info in matched:
                info.is_original = True
            results[:] = matched + [info for info in results if info.songmid not in CURATED_ORIGINAL_IDS]
            return
        # 原唱补全置顶：搜索词规范化歌名命中补全表且原唱版本不在结果页时
        # （如搜"病名为爱（中文版）"结果页全是中文翻唱），拉取原唱版本
        # 插入结果顶部并标原唱；仅第一页生效，避免翻页时重复插入
        if page <= 1:
            pin_id = PIN_ORIGINAL_SONGS.get(self._group_key(kw))
            if pin_id and not any(i.songmid == pin_id for i in results):
                pinned = self._fetch_pin_original(pin_id)
                if pinned is not None:
                    pinned.is_original = True
                    results.insert(0, pinned)
                    return
        # 歌手搜索场景（搜索词是歌手名且歌名命中不足 2 条），不标记原唱；
        # 歌名命中较多时是歌曲搜索（如《非人哉》歌名与乐队同名），继续标记
        singer_hits = sum(kw in self._singer_artists(info) for info in results)
        if singer_hits >= 2 and sum(self._group_key(info.name) == kw for info in results) < 2:
            return

        # 徽章原唱表命中（无音源/原唱版本不在结果页）：置顶最热干净版本，
        # 由徽章显示真实原唱名。先于算法候选与特别规则，避免把更火的
        # 大众熟知版误标为原唱（如《弯弯的月亮》刘欢版、《明天你是否依然
        # 爱我》童安格版）；命中版本的歌手本身就是原唱时不打徽章。
        badge_target = None
        badge_original = ""
        for info in results:
            key = self._group_key(info.name)
            if key in NO_SOURCE_ORIGINALS and self._is_clean_name(info.name):
                badge_target, badge_original = info, NO_SOURCE_ORIGINALS[key]
                break
        if badge_target is not None:
            badge_norm = self._normalize_keyword(badge_original)
            if badge_norm and badge_norm not in self._singer_artists(badge_target):
                badge_target.original_name = badge_original
            badge_target.is_original = True
            results.remove(badge_target)
            results.insert(0, badge_target)
            return

        # 17. 特别规则：翻唱歌手（封茗囧菌/洛少爷/双笙（陈元汐））翻唱
        # 虚拟歌手曲目 -> 虚拟歌手版为原唱。
        # 结果中同时存在歌手含翻唱歌手的歌曲（翻唱）与歌手含虚拟歌手
        # （洛天依/言和/乐正绫等，见 _VOCAL_SYNTH_ARTISTS）的同名歌曲时，
        # 翻唱歌手从原唱候选剔除，只标虚拟歌手版本：
        #   1. 存在歌手含"洛天依Official"的版本 -> 只标全部 Official 版本
        #   2. 否则 -> 只标歌手里含虚拟歌手（非 Official）且发表最早的一个
        # 两首须为不同歌曲：各自歌手集合互斥，避免合唱被同时计入。
        artist_sets = [(info, self._singer_artists(info)) for info in results]
        original_hits = [
            info
            for info, artists in artist_sets
            if (artists & _VOCAL_SYNTH_ARTISTS) and not (artists & _COVER_ARTISTS)
        ]
        cover_hits = [
            info
            for info, artists in artist_sets
            if (artists & _COVER_ARTISTS) and not (artists & _VOCAL_SYNTH_ARTISTS)
        ]
        if original_hits and cover_hits:
            # 排除表命中（如《世末歌者》原唱是乐正绫，洛天依Official 版是
            # 官方翻唱而非原唱）时，规则 17 不适用
            if any(self._group_key(i.name) in _COVER_RULE_EXCLUDED_SONGS for i in original_hits):
                original_hits = []
        if original_hits and cover_hits:
            shared_keys = {self._group_key(i.name) for i in original_hits} & {
                self._group_key(i.name) for i in cover_hits
            }
            original_hits = [i for i in original_hits if self._group_key(i.name) in shared_keys]
            if original_hits:
                official_hits = [
                    i for i in original_hits if self._singer_artists(i) & _VOCAL_SYNTH_OFFICIALS
                ]
                if official_hits:
                    # 有 Official 版本：只标全部 Official 版本
                    matched = official_hits
                else:
                    # 无 Official 版本：标歌手里含虚拟歌手且发表最早的一个
                    # （发布日期无效/缺失时按结果热度序取第一个）
                    timed = [i for i in original_hits if self._is_valid_date(i.publish_time)]
                    if timed:
                        matched = [min(timed, key=lambda i: i.publish_time)]
                    else:
                        matched = [original_hits[0]]
                for info in matched:
                    info.is_original = True
                results[:] = matched + [i for i in results if i not in matched]
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

        # 搜索词精确命中某组时，只允许该组产生候选（模糊匹配的同名歌不参与）
        exact_group = groups.get(kw)
        restrict_exact = bool(exact_group) and any(self._is_clean_name(i.name) for i in exact_group)

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
            if restrict_exact and key != kw:
                continue
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
            # 有效日期优先（如《千本樱（中文版）》搜索中，有发布日期的
            # 日文原版 千本桜 胜过无日期的 remix 簇），再按歌名匹配度、日期
            candidates.sort(
                key=lambda x: (
                    0 if self._is_valid_date(x[1].publish_time) else 1,
                    x[0],
                    x[1].publish_time,
                )
            )
            original = candidates[0][1]
        else:
            # 无算法候选：不标记（跟随平台，原唱不在结果页内的歌不标原唱）
            original = None
        if original is None:
            return
        original.is_original = True
        results.remove(original)
        results.insert(0, original)

    # ── 原唱补全置顶（原唱不在结果页时拉取插入） ──────

    def _fetch_pin_original(self, song_id: str) -> Optional[MusicInfo]:
        """拉取补全置顶的原唱版本信息（实例级缓存原始响应，每次搜索重新解析为新对象）

        请求失败（网络/接口异常或歌曲下架）返回 None，调用方保持原结果不动。
        """
        cached = self._pin_original_cache.get(song_id)
        if cached is None:
            try:
                resp = self._eapi_post(
                    "/api/v3/song/detail", {"c": json.dumps([{"id": int(song_id)}])}
                )
                songs = resp.get("songs") or []
                if not songs:
                    return None
                self._pin_original_cache[song_id] = songs
                if len(self._pin_original_cache) > 50:
                    self._pin_original_cache.pop(next(iter(self._pin_original_cache)))
                cached = songs
            except Exception as e:
                logger.debug(f"网易拉取补全原唱失败 [{song_id}]: {e}")
                return None
        parsed = self._parse_song_detail_songs(cached)
        return parsed[0] if parsed else None

    # ── 百度百科原唱兜底（异步） ─────────────────────

    def set_baike_enabled(self, enabled: bool) -> None:
        """设置百度百科原唱兜底开关（False 时不再触发新的百科查询）"""
        self._baike_enabled = bool(enabled)

    def is_baike_enabled(self) -> bool:
        """百度百科原唱兜底是否开启"""
        return self._baike_enabled

    def set_original_fallback_callback(self, callback) -> None:
        """注册异步回填回调（UI 层调用）

        百科查询完成并回填 results 后在后台线程触发该回调，
        回调需自行将 Tk 操作转回主线程（如 self.after(0, ...)）。
        """
        self._original_fallback_callback = callback

    def _maybe_baike_fallback(self, results: List[MusicInfo], keyword: str) -> None:
        """算法无原唱候选时触发百度百科兜底（仅当开关开启且 UI 已注册回调）

        排除场景（与 _mark_original 一致的判定）:
            - 已有原唱标记的结果
            - 同名不同曲表命中的搜索词（无法判定用户意图）
            - 歌手搜索场景（搜索词是歌手名且歌名命中不足 2 条）
        """
        if not self._baike_enabled:
            return
        if self._original_fallback_callback is None:
            return
        if not results or any(info.is_original for info in results):
            return
        kw = self._normalize_keyword(keyword)
        if not kw or self._group_key(kw) in NO_ORIGINAL_SONGS:
            return
        singer_hits = sum(kw in self._singer_artists(info) for info in results)
        if singer_hits >= 2 and sum(self._group_key(info.name) == kw for info in results) < 2:
            return
        # 查询名：取结果中首条干净歌名（剥离版本后缀），否则用搜索词
        query_name = ""
        for info in results:
            if self._is_clean_name(info.name):
                query_name = self._strip_version_suffix(info.name).strip()
                break
        if not query_name:
            query_name = kw
        if not query_name:
            return
        self._spawn_baike_lookup(query_name, results)

    def _spawn_baike_lookup(self, query_name: str, results: List[MusicInfo]) -> None:
        """按规范化歌名去重后启动后台查询线程（缓存命中则直接回填并通知 UI）

        同一歌名并发多次搜索（翻页/重复搜索）时只发一次百科请求，
        其余结果列表挂到 pending 队列，查询完成后统一回填通知
        （UI 回调自行校验列表是否仍为当前展示页）。
        """
        key = self._normalize_keyword(query_name)
        with self._baike_lock:
            if key in self._baike_cache:
                cached = self._baike_cache[key]
                inflight = False
            elif key in self._baike_pending:
                self._baike_pending[key].append(results)
                return
            else:
                self._baike_pending[key] = [results]
                cached, inflight = None, True
        if not inflight:
            if cached:
                self._notify_baike_backfill(results, cached)
            return

        def _worker():
            try:
                singer = self._baike_lookup.lookup(query_name)
            except Exception as e:
                logger.debug(f"百度百科原唱查询异常 [{query_name}]: {e}")
                singer = None
            with self._baike_lock:
                pending = self._baike_pending.pop(key, [])
                # 负结果也缓存（空串），避免同一首歌重复触发百科请求
                self._baike_cache[key] = singer or ""
                if len(self._baike_cache) > 500:
                    self._trim_baike_cache()
            if not singer:
                logger.debug(f"百度百科未找到原唱: {query_name}")
                return
            for lst in pending:
                self._notify_baike_backfill(lst, singer)

        threading.Thread(
            target=_worker, daemon=True, name=f"BaikeOriginal-{key[:16]}"
        ).start()

    def _notify_baike_backfill(self, results: List[MusicInfo], singer: str) -> None:
        """应用百科回填并通知 UI 刷新（缓存命中/后台查询完成后共用）"""
        try:
            self._apply_baike_result(results, singer)
        except Exception as e:
            logger.debug(f"百度百科原唱回填异常: {e}")
            return
        callback = self._original_fallback_callback
        if callback is not None:
            try:
                callback(results)
            except Exception as e:
                logger.debug(f"百度百科原唱回填回调异常: {e}")

    def _trim_baike_cache(self) -> None:
        """淘汰最旧条目（dict 保持插入顺序，弹出最早插入的键）"""
        while len(self._baike_cache) > 500:
            self._baike_cache.pop(next(iter(self._baike_cache)))

    def _apply_baike_result(self, results: List[MusicInfo], singer: str) -> None:
        """按百科原唱名回填结果（须持有列表独占权，调用方保证不在主线程遍历）

        1. 拆分规范化百科原唱名（/、等分隔符 + 括号别名）
        2. 结果中歌手匹配百科原唱的全部干净版本标原唱并前移置顶（保持热度序）
        3. 无匹配时对最热干净版本打徽章显示原唱名（同 NO_SOURCE_ORIGINALS 行为）
        """
        if not singer or not results:
            return
        expect: set = set()
        for part in re.split(r"[、/|,，;；]", singer):
            name = self._sanitize_artist(part)
            if len(name) >= 2:
                expect.add(name)
            for m in _PAREN_ALIAS_RE.finditer(part):
                alias = self._sanitize_artist(m.group(1))
                if len(alias) >= 2:
                    expect.add(alias)
        if not expect:
            return
        matched = []
        for info in results:
            if self._is_clean_name(info.name) and (self._singer_artists(info) & expect):
                matched.append(info)
        if matched:
            for info in matched:
                info.is_original = True
            results[:] = matched + [info for info in results if info not in matched]
            return
        for info in results:
            if self._is_clean_name(info.name):
                info.is_original = True
                info.original_name = singer
                results.remove(info)
                results.insert(0, info)
                return

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
        """簇内原唱候选：被引用锚点 > 无 origin 干净锚点 > 全部干净候选 > 专辑日期 > 热度顺序

        返回 (候选, 生效日期, 是否有被引用锚点, 被引用锚点的最早有效日期)。
        被引用锚点分支中 fee=8（无版权/盗版条目）降级：
        翻唱上传日期可能早于原版，需依赖 fee 区分；锚点/专辑/日期池中
        原唱本身也可能是 fee=8（如洛天依系作品），不能降级。
        """
        ref_timed = [i for i in ref_clean if self._is_valid_date(i.publish_time)]
        if ref_timed:
            c = self._pick_best(ref_timed)
            return c, c.publish_time, True, c.publish_time
        if ref_clean:
            # 被引用锚点存在但日期无效（占位日期如 2014-01-01 / 缺失）时仍优先：
            # 翻唱的 originSongSimpleData 声明的就是原曲，锚点日期不可靠不代表不是原唱
            c = sorted(
                ref_clean,
                key=lambda x: (1 if x.fee == 8 else 0, 0 if self._is_valid_date(x.publish_time) else 1, x.publish_time),
            )[0]
            return c, c.publish_time, True, 0
        ref_eff = 0
        for i in ref_clean:
            if self._is_valid_date(i.publish_time):
                ref_eff = i.publish_time
                break
        anchors = [i for i in clean if not i.origin_song_id]
        if anchors:
            # 簇内含无 origin 引用的干净锚点（原曲家族的上传版本）时优先于翻唱成员：
            # 翻唱可能引用页外原曲（origin 不在结果中）或翻唱上传日期早于原版，
            # 锚点版本就是结果页内最近似原曲的版本
            c = sorted(
                anchors,
                key=lambda x: (1 if x.fee == 8 else 0, 0 if self._is_valid_date(x.publish_time) else 1, x.publish_time),
            )[0]
            return c, c.publish_time, True, ref_eff
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
        """规范化歌名分组键（忽略末尾括号版本后缀与开头【标签】前缀）"""
        text = cls._strip_version_suffix(name).strip().lower()
        text = _TAG_PREFIX_RE.sub("", text)
        return text or cls._normalize_keyword(name)

    @classmethod
    def _is_clean_name(cls, name: str) -> bool:
        """歌名是否无版本后缀（Live/伴奏/翻唱版等）且开头标签不含版本词

        如 【星尘】尘降 干净（标签是歌手名），【翻唱】xxx、【英文版】xxx 不干净。
        """
        text = (name or "").strip()
        stripped = cls._strip_version_suffix(text)
        if _VERSION_TAG_RE.match(stripped):
            return False
        return stripped == text

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

    # ── 账号歌单（歌单列表同步，只读，不落盘） ──────────
    #
    # 实测（2026-08 登录态）: weapi 前缀（music.163.com/weapi/...）对
    # /user/playlist 与 /playlist/detail 返回空响应体，eapi 通道与
    # music.163.com/api/...（weapi 加密体 + URL 查询参数）可用。

    def get_user_playlists(self) -> Optional[List[dict]]:
        """获取当前登录用户创建的歌单列表（含「我喜欢的音乐」）

        与 YesPlayMusic 一致: limit=2000 上限，仅保留
        creator.userId == 当前登录用户 的歌单（排除收藏/订阅的）。

        Returns:
            [{"id": str, "name": str, "track_count": int, "cover_url": str}, ...]
            保持服务端返回顺序（首项通常为「我喜欢的音乐」）；
            未登录/失败返回 None
        """
        profile = self.fetch_login_profile()
        if not profile or not profile.get("user_id"):
            return None
        uid = int(profile["user_id"])
        data = {"uid": uid, "limit": 2000, "offset": 0}
        resp = None
        # 候选通道: eapi（最稳定）→ weapi + URL 查询参数（/api/ 前缀）
        for kind in ("eapi", "weapi"):
            try:
                if kind == "eapi":
                    resp = self._eapi_post("/api/user/playlist", data)
                else:
                    resp = self._weapi_post(
                        "/user/playlist", data, query={"uid": uid, "limit": 2000, "offset": 0}, base=WY_API_ALT_BASE
                    )
            except Exception as e:
                logger.debug(f"网易获取用户歌单异常 ({kind}): {e}")
                continue
            code = resp.get("code", 200)
            if code != 200 or not resp.get("playlist"):
                logger.debug(f"网易获取用户歌单失败: code={code} ({kind})")
                continue
            break
        else:
            logger.warning("网易获取用户歌单失败: 全部路由均不可用")
            return None
        try:
            result = []
            for pl in resp.get("playlist") or []:
                try:
                    creator = pl.get("creator") or {}
                    if int(creator.get("userId") or 0) != uid:
                        # 仅同步本人创建的歌单（收藏/订阅的歌单不显示）
                        continue
                    result.append(
                        {
                            "id": str(pl.get("id", "")),
                            "name": decode_name(pl.get("name", "")),
                            "track_count": int(pl.get("trackCount") or 0),
                            "cover_url": pl.get("coverImgUrl", ""),
                        }
                    )
                except Exception:
                    continue
            return result
        except Exception as e:
            logger.warning(f"网易获取用户歌单异常: {e}")
            return None

    def get_playlist_tracks(self, playlist_id: str) -> Optional[List[MusicInfo]]:
        """获取歌单完整歌曲列表（需登录）

        通道（逐个尝试，直到取得有效歌单数据）:
            1. eapi /api/v6/playlist/detail（现代接口，登录后 tracks 完整、
               trackIds 始终完整，歌曲为新格式含音质）
            2. eapi /api/playlist/detail（老格式，result.tracks 登录后完整）
            3. weapi /api/playlist/detail?id=...&s=8（老格式，参数在 URL）
        tracks 不完整时（如未登录/接口截断）用 trackIds 分批
        /api/v3/song/detail 补齐（YesPlayMusic 同策略）。

        Args:
            playlist_id: 歌单 ID

        Returns:
            歌曲信息列表；失败返回 None
        """
        if not playlist_id:
            return None
        pid = int(playlist_id)
        playlist = None
        # (通道, path, data, query, base)
        candidates = [
            ("eapi", "/api/v6/playlist/detail", {"id": pid, "n": 100000, "s": 8}, None, None),
            ("eapi", "/api/playlist/detail", {"id": pid, "s": 8}, None, None),
            ("weapi", "/playlist/detail", {"id": pid, "s": 8}, {"id": pid, "s": 8}, WY_API_ALT_BASE),
        ]
        for kind, path, data, query, base in candidates:
            try:
                if kind == "eapi":
                    resp = self._eapi_post(path, data)
                else:
                    resp = self._weapi_post(path, data, query=query, base=base)
            except Exception as e:
                logger.debug(f"网易获取歌单详情异常 ({kind}/{path}): {e}")
                continue
            if resp.get("code") != 200:
                logger.debug(f"网易获取歌单详情失败: code={resp.get('code')} ({kind}/{path})")
                continue
            pl = resp.get("playlist") or resp.get("result") or {}
            if pl:
                playlist = pl
                break
        if playlist is None:
            logger.warning(f"网易获取歌单详情失败 [{playlist_id}]: 全部路由均不可用")
            return None
        try:
            track_count = int(playlist.get("trackCount") or 0)
            tracks = playlist.get("tracks") or []
            if track_count > 0 and len(tracks) >= track_count:
                return self._parse_song_detail_songs(tracks)
            # tracks 不完整：trackIds（新格式）→ 分批 /api/v3/song/detail 补齐
            track_ids = [str(t.get("id", "")) for t in (playlist.get("trackIds") or []) if t.get("id")]
            if track_ids:
                raw_songs = []
                for i in range(0, len(track_ids), 1000):
                    batch = [{"id": int(tid)} for tid in track_ids[i : i + 1000]]
                    try:
                        detail = self._eapi_post("/api/v3/song/detail", {"c": json.dumps(batch)})
                    except Exception as e:
                        logger.debug(f"网易补齐歌单歌曲失败: {e}")
                        detail = {}
                    raw_songs.extend(detail.get("songs") or [])
                if raw_songs:
                    return self._parse_song_detail_songs(raw_songs)
            # 无 trackIds（老格式）或补齐失败：返回已有的 tracks（尽力而为）
            return self._parse_song_detail_songs(tracks)
        except Exception as e:
            logger.warning(f"网易获取歌单歌曲异常 [{playlist_id}]: {e}")
            return None

    def _parse_song_detail_songs(self, raw_songs) -> List[MusicInfo]:
        """解析歌曲详情数组为 MusicInfo 列表

        兼容两种字段格式:
            - 新格式: ar / al / dt / h / l / sq / privilege（v3/v6 详情接口）
            - 老格式: artists / album / duration / hMusic / lMusic / sqMusic
              （/api/playlist/detail 的 result.tracks、老版 /api/song/detail）
        """
        results = []
        for item in raw_songs or []:
            try:
                raw_artists = item.get("ar") or item.get("artists") or []
                singers = [format_singer(decode_name(s.get("name", ""))) for s in raw_artists]
                album = item.get("al") or item.get("album") or {}
                interval_raw = item.get("dt") or item.get("duration") or 0
                interval = interval_raw // 1000 if interval_raw else 0
                types, _types = self._parse_types(item)
                origin = item.get("originSongSimpleData") or {}
                info = MusicInfo(
                    name=decode_name(item.get("name", "")),
                    singer="、".join(singers),
                    source=self.source_id,
                    songmid=str(item.get("id", "")),
                    album_name=decode_name(album.get("name", "") if album else ""),
                    album_id=str(album.get("id", "") if album else ""),
                    interval=interval,
                    img=album.get("picUrl", "") if album else "",
                    types=types,
                    _types=_types,
                    publish_time=int(item.get("publishTime") or 0),
                    origin_song_id=str(origin.get("songId") or "") if origin else "",
                    fee=int(item.get("fee") or 0),
                )
                results.append(info)
            except Exception as e:
                logger.debug(f"解析网易歌单歌曲失败: {e}")
                continue
        return results

    # ── 加密请求辅助 ────────────────────────────────

    def _eapi_post(self, path: str, data: dict) -> dict:
        """发送 eapi 加密请求"""
        signed = wy_eapi(path, data)
        resp = self._session.post(f"{WY_EAPI_BASE}{path}", data=signed, timeout=15)
        return resp.json()

    def _weapi_post(self, path: str, data: dict, query: Optional[dict] = None, base: str = "") -> dict:
        """发送 weapi 加密请求

        Args:
            path: 接口路径
            data: 加密参数
            query: 附加到 URL 的查询参数（部分接口要求参数在 URL 中）
            base: 请求域名前缀，默认 WY_API_BASE（/weapi/）；
                  个别接口（/user/playlist 等）须用 /api/ 前缀（WY_API_ALT_BASE）
        """
        url = (base or WY_API_BASE) + path
        if query:
            url = f"{url}?{urlencode(query)}"
        signed = wy_weapi(data)
        resp = self._session.post(url, data=signed, timeout=15)
        return resp.json()
