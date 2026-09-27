"""歌词解析模块 - LRC/逐字/翻译/罗马音歌词支持

参考 LX Music 的歌词处理逻辑，支持:
    - 标准 LRC 格式歌词 (行级时间标签)
    - 逐字歌词 (字级时间标签 <start,duration>)
    - 翻译歌词 (td: offset 匹配)
    - 罗马音歌词 (rd: offset 匹配)
    - 标签解析 (ti/ar/al/offset/by)

使用示例:
    from services.music_lyrics import LyricParser, LyricLine

    parser = LyricParser()
    parser.parse(raw_lrc_text)
    current_line = parser.get_line_at(55000)  # 获取55秒处的歌词行
"""

import logging
import re
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("music_lyrics")


@dataclass
class LyricWord:
    """单个字/词的逐字歌词信息"""

    text: str = ""  # 文本
    start: int = 0  # 起始时间 (毫秒)
    duration: int = 0  # 持续时间 (毫秒)

    def __repr__(self):
        return f"LyricWord({self.text!r}, {self.start}ms, +{self.duration}ms)"


@dataclass
class LyricLine:
    """一行歌词"""

    text: str = ""  # 原始文本
    time: int = 0  # 起始时间 (毫秒)
    translation: str = ""  # 翻译文本
    roma: str = ""  # 罗马音
    # D-16（**已知时序偏移，本轮只留记号、不改行为**）：`_parse_words` 让 `<s,d>` 之后的
    # 文本从 `s+d` 起算而不是 `s`，逐字时序整体晚一个 duration；而 `words` / `is_word_based`
    # / `end_time` 今天全仓零消费者，所以等阶段 3.18 的逐字（卡拉OK）渲染时一起修。
    words: List[LyricWord] = field(default_factory=list)  # 逐字歌词列表
    is_word_based: bool = False  # 是否为逐字歌词

    @property
    def end_time(self) -> int:
        """估算本行结束时间 (毫秒)"""
        if self.words:
            last_word = self.words[-1]
            return last_word.start + last_word.duration
        return self.time + 5000  # 默认每行5秒

    def __repr__(self):
        return f"LyricLine([{self.time}ms] {self.text!r})"


@dataclass(frozen=True)
class LyricSnapshot:
    """一次解析的完整结果，**发布之后不再被修改**（D-10 的无锁快照）。

    不加锁：服务层零并发原语是红线（`services/` 里连 `import threading` 都会被
    `tests/test_music_local.py::TestServicesAreUIFree` 判红）。写侧（worker 线程）
    只写局部变量、最后 `self._snapshot = 新快照` 一次性替换（引用赋值本身原子），
    所以读侧一次取到整份快照 —— 读不到 clear() 之后、填完之前的中间态（旧写法会让
    读者读到空列表或只剩前几行，症状是歌词瞬间消失/跳错行）；`lines` 是元组、`tags`
    是局部 dict 的副本、`_merge_*` 走"重建行 + 换快照"，所以发布出去的那一份永远不会
    再被任何人改（元组还顺手挡住了外部的 append / clear）。
    """
    lines: Tuple[LyricLine, ...] = ()
    tags: Dict[str, str] = field(default_factory=dict)
    offset_ms: int = 0
    is_parsed: bool = False


class LyricParser:
    """LRC 歌词解析器

    支持标准 LRC 格式，包括:
        - [mm:ss.xx] 或 [mm:ss] 时间标签
        - <start,duration> 逐字时间标签
        - [ti:] [ar:] [al:] [offset:] 元数据标签
    """

    # 正则: 行级时间标签 [mm:ss.xx]
    _TIME_TAG_RE = re.compile(r"\[(\d{1,3}):(\d{2})(?:\.(\d{1,3}))?\]")
    # 正则: 逐字时间标签 <start,duration>
    _WORD_TAG_RE = re.compile(r"<(-?\d+),(-?\d+)(?:,-?\d+)?>")
    # 正则: 元数据标签 [ti:|ar:|al:|offset:|by:]
    _META_TAG_RE = re.compile(r"\[(ti|ar|al|offset|by|kuwo|tool):\s*(.*?)\s*\]", re.IGNORECASE)

    def __init__(self, offset_ms: int = 0):
        # 解析结果只挂在这一个引用上：读者拿到的永远是"某一版完整快照"（D-10）。
        self._snapshot: LyricSnapshot = LyricSnapshot(offset_ms=offset_ms)

    # ─── 解析 ────────────────────────────────────────

    def parse(self, raw_text: Optional[str]) -> bool:
        """解析原始 LRC 文本

        写路径只碰局部变量，解析完一次性换掉 `self._snapshot`（D-10），读者看不到半成品。

        Args:
            raw_text: LRC格式歌词文本

        Returns:
            是否解析成功
        """
        if not raw_text or not raw_text.strip():
            self._snapshot = LyricSnapshot()  # 空歌词：发布空快照（等价于原来的三个 clear）
            return False

        try:
            lines_raw = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            tags: Dict[str, str] = {}
            # 第一遍: 收集标签（写进局部 dict，解析成功后才随快照一起发布）
            for line in lines_raw:
                line = line.strip()
                if not line:
                    continue
                self._collect_tags(line, tags)

            # 全局偏移
            offset_str = tags.get("offset", "0")
            try:
                offset_ms = int(offset_str)
            except (ValueError, TypeError):
                offset_ms = 0

            # 第二遍: 解析歌词行（同样只写局部列表）
            lines: List[LyricLine] = []
            for line in lines_raw:
                line = line.strip()
                if not line:
                    continue
                lines.extend(self._parse_line(line, offset_ms))

            # 按时间排序 —— get_line_at 的二分依赖"按 time 升序"这个不变式（见 D-18）
            lines.sort(key=lambda l: l.time)
            self._snapshot = LyricSnapshot(
                lines=tuple(lines), tags=tags, offset_ms=offset_ms, is_parsed=True
            )
            logger.debug(f"歌词解析完成: {len(lines)} 行")
            return True

        except Exception as e:
            logger.error(f"歌词解析失败: {e}")
            self._snapshot = LyricSnapshot()  # 失败也发布"空的完整快照"，不留半成品
            return False

    def _collect_tags(self, line: str, tags: Dict[str, str]):
        """收集元数据标签（写进调用方给的局部 dict，发布前不碰 self 上的状态）"""
        for match in self._META_TAG_RE.finditer(line):
            tag_name = match.group(1).lower()
            tag_value = match.group(2)
            tags[tag_name] = tag_value

    def _parse_line(self, line: str, global_offset_ms: int) -> List[LyricLine]:
        """解析单行歌词 (可能包含多个时间标签 -> 生成多行)"""
        result: List[LyricLine] = []

        # 提取所有行级时间标签
        time_matches = list(self._TIME_TAG_RE.finditer(line))
        if not time_matches:
            return result

        # 提取文本 (去除所有标签后的剩余部分)
        text_without_tags = line
        for match in time_matches:
            text_without_tags = text_without_tags.replace(match.group(0), "", 1)

        # 也去除逐字标签以获取纯文本
        plain_text = self._WORD_TAG_RE.sub("", text_without_tags).strip()

        # 提取逐字信息
        words = self._parse_words(text_without_tags) if self._WORD_TAG_RE.search(text_without_tags) else []

        for tm_match in time_matches:
            try:
                minutes = int(tm_match.group(1))
                seconds = int(tm_match.group(2))
                ms_str = tm_match.group(3) or "0"
                milliseconds = int(ms_str.ljust(3, "0")[:3])
                time_ms = (minutes * 60 + seconds) * 1000 + milliseconds
                time_ms += global_offset_ms
                if time_ms < 0:
                    time_ms = 0

                lyric_line = LyricLine(text=plain_text, time=time_ms, words=words, is_word_based=len(words) > 0)
                result.append(lyric_line)
            except (ValueError, TypeError):
                continue

        return result

    def _parse_words(self, text: str) -> List[LyricWord]:
        """解析逐字时间标签"""
        words = []
        last_end = 0
        # 按顺序提取非标签文本和标签
        parts = re.split(r"(<-?\d+,-?\d+(?:,-?\d+)?>)", text)
        for part in parts:
            if not part:
                continue
            if part.startswith("<"):
                # 时间标签
                match = self._WORD_TAG_RE.match(part)
                if match:
                    try:
                        start = int(match.group(1))
                        duration = int(match.group(2))
                        if start < 0:
                            start = last_end
                        # D-16：这里 + 下面的 `word_start = last_end` 一起让"标签后文本"
                        # 整体晚一个 duration（标准语义是 `<s,d>` 后的文本从 s 起算）。
                        # 无消费者，本轮只留记号，等阶段 3.18 逐字渲染一起修。
                        last_end = start + duration
                    except (ValueError, TypeError):
                        pass
            else:
                # 文本
                char_text = part.strip()
                if not char_text:
                    continue

                # 为每个字符创建LyricWord (若没有对应的时间标签则用默认值)
                word_start = last_end
                for ch in char_text:
                    words.append(LyricWord(text=ch, start=word_start, duration=300))  # 默认每个字300ms
                    word_start += 300
                last_end = word_start

        # 合并连续同时间标签的字
        if len(words) > 1:
            merged = []
            current = LyricWord(text=words[0].text, start=words[0].start, duration=words[0].duration)
            for i in range(1, len(words)):
                if words[i].start == current.start:
                    current.text += words[i].text
                    current.duration = max(current.duration, words[i].duration)
                else:
                    merged.append(current)
                    current = LyricWord(text=words[i].text, start=words[i].start, duration=words[i].duration)
            merged.append(current)
            words = merged

        return words

    # ─── 查询 ────────────────────────────────────────

    def get_line_at(self, elapsed_ms: int) -> Optional[LyricLine]:
        """获取指定时间点的当前歌词行

        **纯读函数（D-17）**：开头取一次本地快照引用，之后只读它，不写任何共享状态。
        原写法往共享 LRU 缓存写 `cache[elapsed_ms]`、命中判定还迭代整个 OrderedDict，
        跨线程时 `cache.items()` 撞上 worker 的 `cache.clear()` 会抛 RuntimeError
        （被界面侧的 `except Exception: pass` 吞掉，症状是偶发少一帧歌词）；缓存收益
        本来就小（读侧 100ms 一次、二分 300 行仅 9 次比较、降频档必然不命中），故删除。

        Args:
            elapsed_ms: 已播放时间 (毫秒)

        Returns:
            当前歌词行或None
        """
        lines = self._snapshot.lines  # 一次取到整份快照：读到的必是某一版完整歌词
        if not lines:
            return None

        # 二分查找: 找到最后一个 time <= elapsed_ms 的行；不变式是 lines 按 time 升序
        # （parse() 发布前已排序），退出时必有 lines[result_idx].time <= elapsed_ms <
        # lines[result_idx+1].time —— 结果即答案，所以 D-18 那段"再 +1"的越界纠正分支
        # 恒假（只有 D-10 的竞态能命中），本轮删除。
        lo, hi = 0, len(lines) - 1
        result_idx = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if lines[mid].time <= elapsed_ms:
                result_idx = mid
                lo = mid + 1
            else:
                hi = mid - 1

        if result_idx < 0:
            return None

        return lines[result_idx]

    def get_next_line(self, elapsed_ms: int) -> Optional[LyricLine]:
        """获取下一行歌词 (用于预加载)"""
        lines = self._snapshot.lines  # 与 get_line_at 同一份；换掉则 line is current 不匹配
        current = self.get_line_at(elapsed_ms)
        if not current:
            return lines[0] if lines else None
        for i, line in enumerate(lines):
            if line is current and i + 1 < len(lines):
                return lines[i + 1]
        return None

    def set_translation(self, raw_tlrc: Optional[str]):
        """设置翻译歌词 (通过时间标签匹配)"""
        if not raw_tlrc or not self._snapshot.lines:
            return
        try:
            tlrc_parser = LyricParser()
            tlrc_parser.parse(raw_tlrc)
            self._merge_translation(tlrc_parser.lines)
        except Exception as e:
            logger.debug(f"翻译歌词加载失败: {e}")

    def set_roma(self, raw_rlrc: Optional[str]):
        """设置罗马音歌词"""
        if not raw_rlrc or not self._snapshot.lines:
            return
        try:
            rlrc_parser = LyricParser()
            rlrc_parser.parse(raw_rlrc)
            self._merge_roma(rlrc_parser.lines)
        except Exception as e:
            logger.debug(f"罗马音歌词加载失败: {e}")

    def _merge_texts(self, field_name: str, other_lines: Sequence[LyricLine]):
        """把翻译/罗马音按时间并进副文本字段，再一次性换快照（重建行对象，不就地改，见 D-10）"""
        if not other_lines:
            return
        text_map = {line.time: line.text for line in other_lines}
        lines = tuple(
            replace(line, **{field_name: text_map[line.time]}) if line.time in text_map else line
            for line in self._snapshot.lines
        )
        self._snapshot = replace(self._snapshot, lines=lines)

    def _merge_translation(self, tlrc_lines: Sequence[LyricLine]):
        """将翻译文本合并到主歌词行 (通过时间标签匹配)"""
        self._merge_texts("translation", tlrc_lines)

    def _merge_roma(self, rlrc_lines: Sequence[LyricLine]):
        """将罗马音合并到主歌词行"""
        self._merge_texts("roma", rlrc_lines)

    # ─── 属性 ────────────────────────────────────────

    @property
    def lines(self) -> Tuple[LyricLine, ...]:
        return self._snapshot.lines

    @property
    def tags(self) -> Dict[str, str]:
        return self._snapshot.tags

    @property
    def is_parsed(self) -> bool:
        return self._snapshot.is_parsed

    @property
    def title(self) -> str:
        return self._snapshot.tags.get("ti", "")

    @property
    def artist(self) -> str:
        return self._snapshot.tags.get("ar", "")

    @property
    def album(self) -> str:
        return self._snapshot.tags.get("al", "")

    def clear(self):
        """清除所有已解析数据（发布空快照，不就地清空已发布的那一份）"""
        self._snapshot = LyricSnapshot()
