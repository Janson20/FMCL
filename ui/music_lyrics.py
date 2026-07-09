"""歌词解析模块 - LRC/逐字/翻译/罗马音歌词支持

参考 LX Music 的歌词处理逻辑，支持:
    - 标准 LRC 格式歌词 (行级时间标签)
    - 逐字歌词 (字级时间标签 <start,duration>)
    - 翻译歌词 (td: offset 匹配)
    - 罗马音歌词 (rd: offset 匹配)
    - 标签解析 (ti/ar/al/offset/by)

使用示例:
    from ui.music_lyrics import LyricParser, LyricLine

    parser = LyricParser()
    parser.parse(raw_lrc_text)
    current_line = parser.get_line_at(55000)  # 获取55秒处的歌词行
"""

import logging
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

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
        self._lines: List[LyricLine] = []
        self._tags: Dict[str, str] = {}
        self._offset_ms: int = offset_ms
        self._is_parsed: bool = False
        # 缓存: 已搜索过的时间 -> 行索引
        self._search_cache: OrderedDict = OrderedDict()
        self._cache_max: int = 50

    # ─── 解析 ────────────────────────────────────────

    def parse(self, raw_text: Optional[str]) -> bool:
        """解析原始 LRC 文本

        Args:
            raw_text: LRC格式歌词文本

        Returns:
            是否解析成功
        """
        self._lines.clear()
        self._tags.clear()
        self._search_cache.clear()

        if not raw_text or not raw_text.strip():
            self._is_parsed = False
            return False

        try:
            lines_raw = raw_text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            # 第一遍: 收集标签
            for line in lines_raw:
                line = line.strip()
                if not line:
                    continue
                self._collect_tags(line)

            # 全局偏移
            offset_str = self._tags.get("offset", "0")
            try:
                self._offset_ms = int(offset_str)
            except (ValueError, TypeError):
                self._offset_ms = 0

            # 第二遍: 解析歌词行
            for line in lines_raw:
                line = line.strip()
                if not line:
                    continue
                parsed = self._parse_line(line, self._offset_ms)
                if parsed:
                    self._lines.extend(parsed)

            # 按时间排序
            self._lines.sort(key=lambda l: l.time)
            self._is_parsed = True
            logger.debug(f"歌词解析完成: {len(self._lines)} 行")
            return True

        except Exception as e:
            logger.error(f"歌词解析失败: {e}")
            self._is_parsed = False
            return False

    def _collect_tags(self, line: str):
        """收集元数据标签"""
        for match in self._META_TAG_RE.finditer(line):
            tag_name = match.group(1).lower()
            tag_value = match.group(2)
            self._tags[tag_name] = tag_value

        # 也检查简单格式 [tag:value]
        if line.startswith("[ti:") or line.startswith("[ar:") or line.startswith("[al:"):
            pass  # 已由正则处理

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

        通过二分查找找到 elapsed_ms 所在的行。

        Args:
            elapsed_ms: 已播放时间 (毫秒)

        Returns:
            当前歌词行或None
        """
        if not self._lines:
            return None

        # 检查缓存
        cache = self._search_cache
        for cached_time, cached_idx in cache.items():
            if cached_time <= elapsed_ms <= cached_time + 100:
                return self._lines[cached_idx] if 0 <= cached_idx < len(self._lines) else None

        # 二分查找: 找到最后一个 time <= elapsed_ms 的行
        lo, hi = 0, len(self._lines) - 1
        result_idx = -1
        while lo <= hi:
            mid = (lo + hi) // 2
            if self._lines[mid].time <= elapsed_ms:
                result_idx = mid
                lo = mid + 1
            else:
                hi = mid - 1

        if result_idx < 0:
            return None

        # 检查是否已越过该行的结束时间
        current = self._lines[result_idx]
        if result_idx + 1 < len(self._lines):
            next_line = self._lines[result_idx + 1]
            if elapsed_ms >= next_line.time:
                result_idx += 1
                current = next_line

        # 缓存
        cache[elapsed_ms] = result_idx
        while len(cache) > self._cache_max:
            cache.popitem(last=False)

        return current

    def get_next_line(self, elapsed_ms: int) -> Optional[LyricLine]:
        """获取下一行歌词 (用于预加载)"""
        current = self.get_line_at(elapsed_ms)
        if not current:
            return self._lines[0] if self._lines else None
        for i, line in enumerate(self._lines):
            if line is current and i + 1 < len(self._lines):
                return self._lines[i + 1]
        return None

    def set_translation(self, raw_tlrc: Optional[str]):
        """设置翻译歌词 (通过时间标签匹配)"""
        if not raw_tlrc or not self._lines:
            return
        try:
            tlrc_parser = LyricParser()
            tlrc_parser.parse(raw_tlrc)
            self._merge_translation(tlrc_parser.lines)
        except Exception as e:
            logger.debug(f"翻译歌词加载失败: {e}")

    def set_roma(self, raw_rlrc: Optional[str]):
        """设置罗马音歌词"""
        if not raw_rlrc or not self._lines:
            return
        try:
            rlrc_parser = LyricParser()
            rlrc_parser.parse(raw_rlrc)
            self._merge_roma(rlrc_parser.lines)
        except Exception as e:
            logger.debug(f"罗马音歌词加载失败: {e}")

    def _merge_translation(self, tlrc_lines: List[LyricLine]):
        """将翻译文本合并到主歌词行 (通过时间标签匹配)"""
        if not tlrc_lines:
            return
        tlrc_map = {line.time: line.text for line in tlrc_lines}
        for line in self._lines:
            translated = tlrc_map.get(line.time)
            if translated:
                line.translation = translated

    def _merge_roma(self, rlrc_lines: List[LyricLine]):
        """将罗马音合并到主歌词行"""
        if not rlrc_lines:
            return
        rlrc_map = {line.time: line.text for line in rlrc_lines}
        for line in self._lines:
            roma = rlrc_map.get(line.time)
            if roma:
                line.roma = roma

    # ─── 属性 ────────────────────────────────────────

    @property
    def lines(self) -> List[LyricLine]:
        return self._lines

    @property
    def tags(self) -> Dict[str, str]:
        return self._tags

    @property
    def is_parsed(self) -> bool:
        return self._is_parsed

    @property
    def title(self) -> str:
        return self._tags.get("ti", "")

    @property
    def artist(self) -> str:
        return self._tags.get("ar", "")

    @property
    def album(self) -> str:
        return self._tags.get("al", "")

    def clear(self):
        """清除所有已解析数据"""
        self._lines.clear()
        self._tags.clear()
        self._search_cache.clear()
        self._is_parsed = False
