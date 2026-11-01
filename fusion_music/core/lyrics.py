"""LRC 歌词解析 + 逐字（动态）歌词。

三个正则与时间计算规则逐字沿用 FMCL ``ui/music_lyrics.py``，保证与既有行为一致：

* ``[mm:ss.xx]`` 时间标签，毫秒位不足 3 位时右侧补零（``.5`` → ``500``）
* 一行带多个时间标签时展开为多行
* 全局 ``[offset:]`` 在最后叠加，负值钳到 0
* 支持 ``<start,dur>字`` 形式的逐字歌词

相对 FMCL 的增强：
* 翻译（tlyric）与罗马音（romalrc）**真正接上**——FMCL 解析了却从不显示；
* 配对同时支持「时间戳精确相等」与「±150 ms 容差」两种情形；
* 提供 :meth:`Lyrics.index_at` 供 QML 歌词列表高亮，内部带缓存。

逐字歌词（本文件新增）
----------------------
两条来源，界面上是同一件事：

1. **真逐字（网易云 yrc）**：``[行起始ms,行时长ms](字起始ms,字时长ms,标记)字…``。
   实测（2026-10）拿到 yrc 的比例约三成，取不到就得有兜底 —— 否则「动态歌词」
   在一大半歌上是彻底不动的。
2. **伪动态**：只有逐行时间戳时，把整行按字拆开、按「到下一行的间隔」把填充
   摊到每个字上（:meth:`Lyrics.apply_pseudo`）。它不是真的演唱时序，但配合
   逐字填充的观感，比整行突然亮起自然得多。

两条来源都归一到 :class:`LyricWord`：除了时间，还带**在该行文本里的字符区间**
（``char_start`` / ``char_end``），界面靠它把「唱到第几个字」换算成着色范围。
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_TIME_TAG_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:[.:](\d{1,3}))?\]")
_WORD_TAG_RE = re.compile(r"<(-?\d+),(-?\d+)(?:,-?\d+)?>")
_META_TAG_RE = re.compile(r"\[(ti|ar|al|offset|by|kuwo|tool|length):\s*(.*?)\s*\]", re.IGNORECASE)

#: 网易云 yrc 的**行**头：``[行起始ms,行时长ms]``（与 ``[mm:ss.xx]`` 不冲突，后者带冒号）
_YRC_HEAD_RE = re.compile(r"^\[(\d{1,9}),(\d{1,9})\]")
#: 网易云 yrc 的**字**标记：``(字起始ms,字时长ms,标记)``。三个数字是硬要求 ——
#: 歌词正文里本来就可能出现 ``(123,456)`` 这种括号，宽松匹配会把正文吃掉。
_YRC_WORD_RE = re.compile(r"\((\d{1,9}),(\d{1,9}),(-?\d+)\)")

# 翻译/罗马音配对的时间容差（毫秒）
PAIR_TOLERANCE_MS = 150
# 把 yrc 的字时间贴到逐行歌词上时的容差。**必须比 PAIR_TOLERANCE_MS 宽得多**：
# 同一条歌词在两个接口里的行时间本来就对不齐（实测 lrc 的 [00:13.47] 对应 yrc 的
# [13010,…]，差 460 ms），但文本一致时不会认错行 —— 有文本核对兜底。
WORD_PAIR_TOLERANCE_MS = 1500
# 无逐字信息时一行的默认持续时间
DEFAULT_LINE_MS = 5000

# ── 伪动态的参数（都可用 :meth:`Lyrics.apply_pseudo` 覆盖）────────────
#: 每个「权重 1.0」的字占多少毫秒。中文演唱大约 4~5 字/秒
PSEUDO_PER_CHAR_MS = 220.0
#: 一行最少摊多久（很短的语气词行不至于一闪而过）
PSEUDO_MIN_SPAN_MS = 800
#: 一行最多摊多久。长间奏后面那句要是按整个间隔摊，填充会慢得像坏掉了
PSEUDO_MAX_SPAN_MS = 8000.0


@dataclass
class LyricWord:
    """逐字歌词的一个片段。

    ``char_start`` / ``char_end`` 是它在**所属行文本**里的字符区间（左闭右开），
    界面靠它把「唱到第几个字」换算成着色范围。逐字片段与行文本是一起构造的，
    所以区间一定落在文本之内（见 :func:`_trim_line_words`）。
    """

    text: str
    start: int  # 毫秒
    duration: int  # 毫秒
    char_start: int = 0
    char_end: int = 0

    @property
    def end(self) -> int:
        return self.start + self.duration


@dataclass
class LyricLine:
    text: str
    time: int  # 毫秒
    translation: str = ""
    roma: str = ""
    words: List[LyricWord] = field(default_factory=list)
    #: 逐字数据的来源：``""``（无）/ ``"api"``（真逐字）/ ``"pseudo"``（按行时间摊出来的）
    word_source: str = ""

    @property
    def is_word_based(self) -> bool:
        return bool(self.words)

    @property
    def end_time(self) -> int:
        if self.words:
            return self.words[-1].end
        return self.time + DEFAULT_LINE_MS

    @property
    def has_sub(self) -> bool:
        return bool(self.translation or self.roma)

    @property
    def sub_text(self) -> str:
        return self.translation or self.roma


class Lyrics:
    """解析后的歌词集合。"""

    def __init__(self) -> None:
        self.lines: List[LyricLine] = []
        self.tags: Dict[str, str] = {}
        self.offset_ms: int = 0
        self._times: List[int] = []
        self._cache_key: int = -10_000
        self._cache_index: int = -1

    # ── 解析 ────────────────────────────────────────────────

    def clear(self) -> None:
        self.lines.clear()
        self.tags.clear()
        self.offset_ms = 0
        self._times.clear()
        self._cache_key = -10_000
        self._cache_index = -1

    @property
    def is_empty(self) -> bool:
        return not self.lines

    def parse(self, raw_text: Optional[str]) -> bool:
        """解析 LRC 文本，返回是否解析出至少一行。"""
        self.clear()
        if not raw_text:
            return False

        # 第一遍：收集元信息标签
        for line in raw_text.splitlines():
            for m in _META_TAG_RE.finditer(line):
                self.tags[m.group(1).lower()] = m.group(2)
        try:
            self.offset_ms = int(float(self.tags.get("offset", "0") or 0))
        except (TypeError, ValueError):
            self.offset_ms = 0

        # 第二遍：解析正文
        for line in raw_text.splitlines():
            self.lines.extend(self._parse_line(line))

        self.lines.sort(key=lambda l: l.time)
        self._times = [l.time for l in self.lines]
        self._cache_key = -10_000
        self._cache_index = -1
        return bool(self.lines)

    def _parse_line(self, line: str) -> List[LyricLine]:
        out: List[LyricLine] = []
        line = line.strip()
        if not line:
            return out
        # 网易云 yrc（逐字）行走单独一条路：它的行头是 [起始ms,时长ms]，正文由
        # (字起始,字时长,标记)字 组成，与 [mm:ss.xx] 那套完全不是一回事
        yrc = _parse_yrc_line(line, self.offset_ms)
        if yrc is not None:
            return yrc
        time_matches = list(_TIME_TAG_RE.finditer(line))
        if not time_matches:
            return out

        stripped = line
        for m in time_matches:
            stripped = stripped.replace(m.group(0), "", 1)

        words: List[LyricWord] = []
        text = _WORD_TAG_RE.sub("", stripped).strip()
        if _WORD_TAG_RE.search(stripped):
            # 逐字片段与行文本必须**同一份字符串**：字段时间好办，字符区间不好办 ——
            # 先按 tag 拆出片段（含空格片段），文本就是它们的拼接，再一起裁掉首尾空白
            parsed = _parse_words(stripped)
            if parsed:
                text, words = _trim_line_words("".join(w.text for w in parsed), parsed)
        if not text:
            return out

        for tm in time_matches:
            try:
                minutes = int(tm.group(1))
                seconds = int(tm.group(2))
                ms_str = tm.group(3) or "0"
                milliseconds = int(ms_str.ljust(3, "0")[:3])
            except (TypeError, ValueError):
                continue
            t = (minutes * 60 + seconds) * 1000 + milliseconds + self.offset_ms
            out.append(
                LyricLine(
                    text=text,
                    time=max(0, t),
                    words=[
                        LyricWord(
                            w.text,
                            max(0, w.start + self.offset_ms),
                            w.duration,
                            w.char_start,
                            w.char_end,
                        )
                        for w in words
                    ],
                    word_source="api" if words else "",
                )
            )
        return out

    # ── 翻译 / 罗马音 ───────────────────────────────────────

    def set_translation(self, raw: Optional[str]) -> None:
        self._merge(raw, "translation")

    def set_roma(self, raw: Optional[str]) -> None:
        self._merge(raw, "roma")

    def _merge(self, raw: Optional[str], attr: str) -> None:
        if not raw or not self.lines:
            return
        sub = Lyrics()
        sub.parse(raw)
        if not sub.lines:
            return
        # 翻译/罗马音常常不带自己的 [offset:]，此时沿用主歌词的偏移，
        # 否则整段会整体错位而配不上。
        shift = self.offset_ms - sub.offset_ms
        if shift:
            for line in sub.lines:
                line.time = max(0, line.time + shift)
        lookup = {l.time: l.text for l in sub.lines}
        sub_times = sorted(lookup)
        for line in self.lines:
            text = lookup.get(line.time)
            if not text:
                text = _nearest(lookup, sub_times, line.time, PAIR_TOLERANCE_MS) or ""
            if text:
                setattr(line, attr, text)

    # ── 逐字 ────────────────────────────────────────────────

    def set_words(self, raw: Optional[str]) -> int:
        """把一段逐字歌词（yrc）贴到**已经解析好的行**上，返回贴上的行数。

        为什么是「贴」而不是「换成逐字歌词」：行文本与翻译/罗马音都已经按逐行
        歌词排好了，逐字只补时间。而且这两份东西的行时间**本来就对不齐**
        （实测差 400+ ms），所以配对规则是「时间最接近 **且** 文本一致」——
        文本核对保证不会把别的行的时间贴错，容差因此可以放得很宽。

        贴上时顺手把行时间换成 yrc 的（更贴近真实起唱点，见 WORD_PAIR_TOLERANCE_MS）。
        """
        if not raw or not self.lines:
            return 0
        sub = Lyrics()
        sub.parse(raw)
        candidates = [l for l in sub.lines if l.words]
        if not candidates:
            return 0
        shift = self.offset_ms - sub.offset_ms
        if shift:
            for line in candidates:
                line.time = max(0, line.time + shift)
        lookup: Dict[int, LyricLine] = {}
        for line in candidates:
            lookup.setdefault(line.time, line)
        times = sorted(lookup)

        matched = 0
        for line in self.lines:
            if line.words:
                continue  # 已经有逐字（比如整份歌词就是 yrc）就不动它
            cand = lookup.get(line.time)
            if cand is None or _norm_text(cand.text) != _norm_text(line.text):
                # 时间戳正好相等但文本对不上（同一时刻的不同版本歌词）也要继续找
                cand = _nearest(lookup, times, line.time, WORD_PAIR_TOLERANCE_MS)
            if cand is None or _norm_text(cand.text) != _norm_text(line.text):
                continue
            adopted = _adopt_words(cand.text, cand.words, line.text)
            if not adopted:
                continue
            line.words = adopted
            line.word_source = "api"
            line.time = cand.time  # yrc 的行时间更准，顺带对齐高亮
            matched += 1
        if matched:
            self._resort()
        return matched

    def apply_pseudo(
        self,
        *,
        per_char_ms: float = PSEUDO_PER_CHAR_MS,
        min_span_ms: int = PSEUDO_MIN_SPAN_MS,
        max_span_ms: float = PSEUDO_MAX_SPAN_MS,
    ) -> int:
        """给**没有逐字信息**的行按逐行时间摊出伪动态，返回处理的行数。

        摊法：整行的可用时长取「到下一行的间隔」与「字数 × 每字时长」的较小值，
        并夹在 ``min_span_ms``~``max_span_ms`` 之间（长间奏不会把填充拖成蜗牛，
        短句也不会一闪而过）。字与字之间按权重分（汉字 1.0、拉丁字母 0.5、
        标点 0.4、空格 0.15），于是中英混排的推进速度看着是均匀的。

        可重复调用：已经有逐字的行一律跳过，不会覆盖真数据。
        """
        made = 0
        total_lines = len(self.lines)
        for i, line in enumerate(self.lines):
            if line.words or not line.text.strip():
                continue
            start = line.time
            if i + 1 < total_lines:
                gap = self.lines[i + 1].time - start
            else:
                gap = DEFAULT_LINE_MS
            weights = [_char_weight(ch) for ch in line.text]
            total = sum(weights) or 1.0
            natural = max(float(min_span_ms), total * float(per_char_ms))
            span = min(float(gap) if gap > 0 else float(DEFAULT_LINE_MS), natural, float(max_span_ms))
            span = max(1.0, span)

            words: List[LyricWord] = []
            acc = 0.0
            for idx, ch in enumerate(line.text):
                share = span * (weights[idx] / total)
                w_start = start + int(round(acc))
                w_end = start + int(round(acc + share))
                words.append(
                    LyricWord(
                        text=ch,
                        start=max(0, w_start),
                        duration=max(0, w_end - w_start),
                        char_start=idx,
                        char_end=idx + 1,
                    )
                )
                acc += share
            line.words = words
            line.word_source = "pseudo"
            made += 1
        return made

    def _resort(self) -> None:
        """行时间被逐字对齐改过之后重建索引（缓存一并作废）。"""
        self.lines.sort(key=lambda l: l.time)
        self._times = [l.time for l in self.lines]
        self._cache_key = -10_000
        self._cache_index = -1

    # ── 查询 ────────────────────────────────────────────────

    def index_at(self, elapsed_ms: int) -> int:
        """返回当前应高亮的行下标，无匹配返回 ``-1``。"""
        if not self.lines:
            return -1
        if elapsed_ms < self.lines[0].time:
            return -1
        # 缓存命中判定必须用「下一行的起始时间」而不是 end_time：
        # end_time 在一行只有 5 秒默认时长时会把后续几秒都算作同一行。
        i = self._cache_index
        if 0 <= i < len(self.lines) and self.lines[i].time <= elapsed_ms:
            nxt = self.lines[i + 1].time if i + 1 < len(self.lines) else None
            if nxt is None or elapsed_ms < nxt:
                return i
        idx = bisect.bisect_right(self._times, elapsed_ms) - 1
        if idx < 0:
            return -1
        self._cache_key = elapsed_ms
        self._cache_index = idx
        return idx

    def line_at(self, elapsed_ms: int) -> Optional[LyricLine]:
        idx = self.index_at(elapsed_ms)
        return self.lines[idx] if idx >= 0 else None

    def window(self, elapsed_ms: int, before: int = 2, after: int = 6) -> List[Tuple[int, LyricLine]]:
        """返回当前行前后的一段歌词（供 UI 局部刷新）。"""
        idx = self.index_at(elapsed_ms)
        if idx < 0:
            return [(i, l) for i, l in enumerate(self.lines[: max(0, after)])]
        lo = max(0, idx - before)
        hi = min(len(self.lines), idx + after + 1)
        return [(i, self.lines[i]) for i in range(lo, hi)]

    def char_progress(self, elapsed_ms: int) -> float:
        """当前行「唱到第几个字」——可带小数（0.0 ~ 行文本长度）。

        界面拿它把逐字填充画出来：整数部分是已唱完的字数，小数部分用来说明
        **当前这个字唱了多少**（用来在「已唱色 / 未唱色」之间插值）。

        行与行之间的空档里返回值会停在整数上（前一个字已唱完、后一个字还没开始），
        于是填充不会在间奏里继续往前爬。
        """
        idx = self.index_at(elapsed_ms)
        if idx < 0:
            return 0.0
        line = self.lines[idx]
        if not line.words:
            return 0.0
        t = int(elapsed_ms)
        for word in line.words:
            if t < word.start:
                return float(word.char_start)
            if word.duration <= 0:
                if t == word.start:
                    return float(word.char_end)
                continue
            if t < word.start + word.duration:
                frac = (t - word.start) / float(word.duration)
                span = word.char_end - word.char_start
                return float(word.char_start) + span * max(0.0, min(1.0, frac))
        return float(len(line.text))

    def to_dicts(self) -> List[Dict[str, object]]:
        return [
            {
                "text": l.text,
                "time": l.time,
                "translation": l.translation,
                "roma": l.roma,
                "sub": l.sub_text,
                # 界面据此决定这一行要不要走逐字填充（没有逐字数据的行保持整行高亮）
                "dynamic": l.is_word_based,
            }
            for l in self.lines
        ]


def _nearest(lookup: Dict[int, Any], times: List[int], target: int, tolerance: int) -> Optional[Any]:
    """在容差范围内取时间最接近的一条（同一时刻不算最近，找的是「擦边」匹配）。

    翻译 / 罗马音与逐字歌词都要做这件事，只是链表里的值一个是文本、一个是整行；
    查找规则必须一致，所以只留这一份实现。
    """
    pos = bisect.bisect_left(times, target)
    best: Optional[Any] = None
    best_delta = tolerance + 1
    for i in (pos - 1, pos):
        if 0 <= i < len(times):
            delta = abs(times[i] - target)
            if delta <= tolerance and delta < best_delta:
                best_delta = delta
                best = lookup[times[i]]
    return best


def _parse_words(text: str) -> List[LyricWord]:
    """解析 ``<start,duration>字`` 形式的逐字歌词。

    与 FMCL 的差异有两点，都是为了逐字填充能真的用上：

    * 用标记里**自带的时长**（原来一律按 300 ms 估，唱快唱慢都不对）；
    * 记下每个片段在整行文本里的字符区间（供界面定位）。

    **时间语义沿用 FMCL**：标记里写多少就是多少，不叠加行起始时间。这个格式
    目前没有任何音源在用（网易云的逐字走 yrc，见 :func:`_parse_yrc_line`），
    保持原样是为了不与 vendored 代码产生行为分叉。
    """
    parts = re.split(r"(<-?\d+,-?\d+(?:,-?\d+)?>)", text)
    raw: List[Tuple[int, int, str]] = []  # (start, duration, text)
    last_start = 0
    last_duration = 0
    for part in parts:
        m = _WORD_TAG_RE.fullmatch(part)
        if m:
            last_start = int(m.group(1))
            try:
                last_duration = int(m.group(2))
            except (TypeError, ValueError):
                last_duration = 0
            continue
        if not part:
            continue
        raw.append((last_start, max(0, last_duration), part))

    words: List[LyricWord] = []
    char_pos = 0
    for start, duration, seg in raw:
        words.append(
            LyricWord(
                text=seg,
                start=start,
                duration=duration,
                char_start=char_pos,
                char_end=char_pos + len(seg),
            )
        )
        char_pos += len(seg)
    return _finalize_words(words)


def _parse_yrc_line(line: str, offset_ms: int) -> Optional[List[LyricLine]]:
    """解析网易云 yrc 的一行；不是 yrc 行返回 ``None``。

    格式：``[行起始ms,行时长ms](字起始ms,字时长ms,标记)字(…)字…``。
    正文里最后的那个字常常**没有**自己的标记（``…(19170,390,0)去``），
    它的时长按「行起始 + 行时长」倒推。
    """
    head = _YRC_HEAD_RE.match(line)
    if head is None:
        return None
    line_start = int(head.group(1))
    line_duration = int(head.group(2))
    rest = line[head.end():]
    tokens = list(_YRC_WORD_RE.finditer(rest))
    if not tokens:
        # 只有行头没有逐字标记：退化成一行普通歌词（制作人名单这类行就是这样）
        text = rest.strip()
        if not text:
            return []
        return [LyricLine(text=text, time=max(0, line_start + offset_ms))]

    lead = rest[: tokens[0].start()]
    line_end = line_start + line_duration if line_duration > 0 else None
    segments: List[Tuple[int, int, str]] = []
    for i, tok in enumerate(tokens):
        seg_end = tokens[i + 1].start() if i + 1 < len(tokens) else len(rest)
        text = rest[tok.end():seg_end]
        start = int(tok.group(1))
        duration = int(tok.group(2))
        nxt = int(tokens[i + 1].group(1)) if i + 1 < len(tokens) else None
        if duration <= 0:
            if nxt is not None and nxt > start:
                duration = nxt - start
            elif line_end is not None and line_end > start:
                duration = line_end - start
            else:
                duration = 0
        # 不越过下一个字的起点 / 行尾：越过了就会有两个字同时"正在唱"
        limit = nxt if nxt is not None else line_end
        if limit is not None and start + duration > limit:
            duration = max(0, limit - start)
        segments.append((start, duration, text))

    words: List[LyricWord] = []
    char_pos = 0
    if lead:
        # 行头与第一个字标记之间的残留文本（正常为空，防一手）
        words.append(LyricWord(text=lead, start=line_start, duration=0,
                               char_start=0, char_end=len(lead)))
        char_pos = len(lead)
    for start, duration, text in segments:
        if not text:
            # 空片段不占字符：行尾那个带时长的标记后面本来就没有字（`(16890,900,0) `
            # 的尾空格已经被 _parse_line 的 strip 吃掉了），留着会变出一个
            # char_start == char_end 的幽灵字，界面上算进度时会跳过它
            continue
        words.append(
            LyricWord(
                text=text,
                start=max(0, start + offset_ms),
                duration=duration,
                char_start=char_pos,
                char_end=char_pos + len(text),
            )
        )
        char_pos += len(text)

    text = "".join(w.text for w in words)
    if not text.strip():
        return []
    text, words = _trim_line_words(text, words)
    if not words:
        return []
    return [LyricLine(text=text, time=max(0, line_start + offset_ms), words=words,
                      word_source="api")]


def _finalize_words(words: List[LyricWord]) -> List[LyricWord]:
    """合并起点相同的相邻片段，并把没有时长的片段用下一个片段的起点补上。"""
    merged: List[LyricWord] = []
    for w in words:
        if merged and merged[-1].start == w.start and merged[-1].char_end == w.char_start:
            merged[-1].text += w.text
            merged[-1].char_end = w.char_end
            merged[-1].duration = max(merged[-1].duration, w.duration)
        else:
            merged.append(w)
    for i, w in enumerate(merged):
        if w.duration <= 0 and i + 1 < len(merged) and merged[i + 1].start > w.start:
            w.duration = merged[i + 1].start - w.start
    return merged


def _trim_line_words(text: str, words: List[LyricWord]) -> Tuple[str, List[LyricWord]]:
    """去掉行首尾空白，并把逐字片段的字符区间跟着平移 / 收窄。

    尾随空格在居中排版里会把整行挤偏，而行首空格更会让所有字符区间错位 ——
    所以文本与区间必须一起改，不能只 strip 文本。
    """
    stripped = text.strip()
    if stripped == text:
        return text, words
    lead = len(text) - len(text.lstrip())
    if not stripped:
        return "", []
    out: List[LyricWord] = []
    for w in words:
        cs = max(0, w.char_start - lead)
        ce = min(len(stripped), w.char_end - lead)
        if ce <= cs:
            continue
        out.append(LyricWord(stripped[cs:ce], w.start, w.duration, cs, ce))
    return stripped, out


def _adopt_words(source_text: str, words: List[LyricWord], target_text: str) -> List[LyricWord]:
    """把 ``source_text`` 上的逐字片段搬到 ``target_text`` 上。

    **两份文本常常只差空白**：网易云 yrc 会在字与字之间塞进带时长的空格
    （``…(14340,30,0) (14370,480,0)主…``），而逐行 LRC 里没有 —— 实测
    「从不主动示弱」在 yrc 里是 9 个字符、在 LRC 里是 6 个。直接拿下标搬会整体
    错位（第 3 个字显示成第 5 个），所以按「非空白字符依次对齐」重算区间；
    只要有一个字符对不上就整行放弃（宁可没有逐字，也不要错位的逐字）。
    """
    mapping: List[Optional[int]] = []
    j = 0
    target_len = len(target_text)
    for ch in source_text:
        if ch.isspace():
            mapping.append(None)
            continue
        while j < target_len and target_text[j].isspace():
            j += 1
        if j >= target_len or target_text[j] != ch:
            return []
        mapping.append(j)
        j += 1

    out: List[LyricWord] = []
    for w in words:
        idxs = [
            mapping[i]
            for i in range(max(0, w.char_start), min(w.char_end, len(mapping)))
            if mapping[i] is not None
        ]
        if not idxs:
            continue
        cs = min(idxs)
        ce = max(idxs) + 1
        out.append(
            LyricWord(text=target_text[cs:ce], start=w.start, duration=w.duration,
                      char_start=cs, char_end=ce)
        )
    return out


def _norm_text(text: str) -> str:
    """比对两份歌词的文本时用的归一化：只去空白，不动标点与大小写。"""
    return "".join(str(text or "").split())


def _is_wide_char(code: int) -> bool:
    """全角/宽字符（中日韩、假名、谚文等）：排版宽度约等于两个拉丁字母。"""
    return (
        0x1100 <= code <= 0x115F
        or 0x2E80 <= code <= 0x303E
        or 0x3041 <= code <= 0x33FF
        or 0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xA000 <= code <= 0xA4CF
        or 0xAC00 <= code <= 0xD7A3
        or 0xF900 <= code <= 0xFAFF
        or 0xFE30 <= code <= 0xFE4F
        or 0xFF00 <= code <= 0xFF60
        or 0xFFE0 <= code <= 0xFFE6
        or 0x20000 <= code <= 0x3FFFD
    )


def _char_weight(ch: str) -> float:
    """伪动态里一个字的权重（相对演唱时长）。"""
    if ch.isspace():
        return 0.15
    if _is_wide_char(ord(ch)):
        return 1.0
    if ch.isalnum():
        return 0.5
    return 0.4


def parse_lyrics(raw: Optional[str], translation: Optional[str] = None,
                 roma: Optional[str] = None) -> Lyrics:
    """便捷入口：一次性解析主歌词 + 翻译 + 罗马音。"""
    ly = Lyrics()
    ly.parse(raw)
    if translation:
        ly.set_translation(translation)
    if roma:
        ly.set_roma(roma)
    return ly
