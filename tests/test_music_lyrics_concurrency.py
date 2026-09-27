"""歌词解析的线程契约与边界语义回归（阶段 2 返工 E 组 WP1：D-10 / D-17 / D-18 / D-16）。

## 被钉住的契约

1. **D-10：一次解析 = 一份不可变快照 + 一次原子引用替换。**
   写侧是 worker 线程（`services.music_lyric_display.load_lyric`），读侧是主线程每
   100ms 一次的 `display_plan` → `get_line_at`。服务层不许有锁、不许 import threading
   （`tests/test_music_local.py::TestServicesAreUIFree::test_no_threading`），所以
   做法是"发布后不再被修改的快照"。可观测判据有两条，本文件都钉住：
   * 读者拿到的永远是一份**完整**版本（`len(parser.lines)` 只能是 0 或本版本行数，
     且同一份快照里所有行同版本）；
   * 已经交出去的 `parser.lines` **不会再被后续 parse()/clear()/合并翻译改动**。
2. **D-17：`get_line_at` 是纯函数。** 读路径不许写任何共享状态 —— 本文件直接比较
   调用前后的 `parser.__dict__`。
3. **D-18：`get_line_at` 的边界语义。** `lines` 按时间升序时，"二分之后再纠正下标"
   那段分支恒假（已删）；空 / 单行 / 时间戳相同 / 越界 / 乱序由本文件逐条钉住。
4. **D-16：逐字时序偏移是已知缺陷**，本轮按裁决不改行为，只钉一条会红的记号。

## 这些用例为什么不是空断言（把修复撤掉就会红）

* `TestSnapshotImmutability` 的三条**完全不依赖线程时序**：旧实现里 `parser.lines`
  返回的就是 `self._lines` 这个活 list（`parse()` 先 clear() 再 extend()、`clear()`
  直接清空它），且 `_merge_translation` 是在原位对象上写 `.translation` —— 于是
  "已发布的内容不变"必然失败。
* `TestReadPathIsPure` 同样确定：旧实现的 `get_line_at` 会往 `self._search_cache`
  写一个键，调用前后 `parser.__dict__` 必然不同。
* `TestConcurrentParseAndRead` 是真竞态：旧实现 clear() 与最后一次 extend() 之间必然
  存在 `len(lines) ∈ (0, 60)` 的窗口，读者的长度判据必然命中。
* 撤掉修复后的实测输出见 `poc/review/e_group/wp1_music_lyrics.md`
  （三个确定性用例 + 竞态用例都变红，恢复后全绿）。
"""

from __future__ import annotations

import threading

import pytest

import services.desktop_lyric as desktop_lyric
from services.music_lyrics import LyricParser

#: 每个版本的歌词行数。取 60 是为了让旧实现的 clear() ~ 填完之间有一个宽窗口。
LINES_PER_VERSION = 60

#: 版本号池：同一版的所有行带同一个前缀，读者据此校验"没有混版"。
VERSION_TAGS = ("v0", "v1", "v2", "v3")

#: 竞态用例里写者解析多少次（写固定轮次，不靠 sleep 计时）。
PARSE_ROUNDS = 600


def _lrc(tag: str, count: int = LINES_PER_VERSION) -> str:
    """造一份可自检的歌词：第 i 行时间 = i*100ms，文本自带版本号与下标。

    形如 ``[00:03.70]v1-37``；这样读者不知道"当前是哪一版"也能校验：
    对任意 ``t``，命中的行必须是**第 t//100 行**（所有版本的时间轴完全一致）。
    """
    return "\n".join(f"[00:{i // 10:02d}.{(i % 10) * 10:02d}]{tag}-{i}" for i in range(count))


def _expected_index(elapsed_ms: int) -> int:
    """时间轴固定：第 i 行在 i*100ms。"""
    return elapsed_ms // 100


def _split_version(text: str):
    """``"v1-37"`` → ``("v1", 37)``；格式不符返回 ``(text, None)``。"""
    tag, sep, num = text.rpartition("-")
    if not sep or not num.isdigit():
        return text, None
    return tag, int(num)


# ══════════════════════════════════════════════════════════════════════
# 1. D-10：已发布的快照不许再被改（确定性判据，不依赖线程时序）
# ══════════════════════════════════════════════════════════════════════


class TestSnapshotImmutability:
    def test_lines_is_an_immutable_tuple(self):
        """`parser.lines` 必须是不可变序列 —— D-10 的形态约定，也是"读侧稳定视图"的前提。"""
        parser = LyricParser()
        parser.parse(_lrc("v0"))
        assert isinstance(parser.lines, tuple)
        with pytest.raises(TypeError):
            parser.lines[0] = None  # type: ignore[index]

    def test_a_published_snapshot_survives_a_later_parse(self):
        """旧实现里 `parser.lines` 就是那个会被 clear()/extend() 就地重写的 list。"""
        parser = LyricParser()
        parser.parse(_lrc("v0"))
        published = parser.lines
        parser.parse(_lrc("v1"))
        assert [ln.text for ln in published] == [f"v0-{i}" for i in range(LINES_PER_VERSION)]
        assert [ln.text for ln in parser.lines] == [f"v1-{i}" for i in range(LINES_PER_VERSION)]

    def test_clear_does_not_empty_a_published_snapshot(self):
        """`load_lyric` 的真实路径是 clear() + parse()，旧实现的 clear() 会就地清空。"""
        parser = LyricParser()
        parser.parse(_lrc("v0"))
        published = parser.lines
        parser.clear()
        assert len(published) == LINES_PER_VERSION
        assert parser.lines == () and parser.is_parsed is False

    def test_set_translation_does_not_mutate_published_lines(self):
        """合并翻译走"重建行 + 换快照"：旧实现是在原 LyricLine 上直接写 `.translation`。

        代价（有意）：持有旧 `parser.lines` 的调用方要重新取一次才看得到翻译；
        `set_translation` / `set_roma` 今天零调用点，接线时注意这一点。
        """
        parser = LyricParser()
        parser.parse(_lrc("v0", 3))
        published = parser.lines
        parser.set_translation("[00:00.00]译0\n[00:00.20]译2")
        assert [ln.translation for ln in published] == ["", "", ""]
        assert [ln.translation for ln in parser.lines] == ["译0", "", "译2"]
        assert published[0] is not parser.lines[0]

    def test_parse_publishes_one_coherent_version_at_a_time(self):
        """同一份快照里的行必须全部来自同一版：版本号不许混。"""
        parser = LyricParser()
        parser.parse(_lrc("v0"))
        parser.parse(_lrc("v1"))
        assert {_split_version(ln.text)[0] for ln in parser.lines} == {"v1"}


# ══════════════════════════════════════════════════════════════════════
# 2. D-17：读路径是纯函数
# ══════════════════════════════════════════════════════════════════════


class TestReadPathIsPure:
    def test_get_line_at_does_not_write_any_state(self):
        """读路径写共享状态就是 D-17：旧实现会往 `_search_cache` 写一个键。"""
        parser = LyricParser()
        parser.parse(_lrc("v0"))
        before = dict(parser.__dict__)
        for elapsed in (0, 100, 1234, 2500, 5900, 999999):
            parser.get_line_at(elapsed)
        assert dict(parser.__dict__) == before, "get_line_at 改了共享状态（D-17 复发）"

    def test_get_line_at_is_repeatable_and_returns_the_same_object(self):
        parser = LyricParser()
        parser.parse(_lrc("v0"))
        first = parser.get_line_at(1234)
        assert first is not None
        for _ in range(5):
            assert parser.get_line_at(1234) is first

    def test_repeated_queries_never_return_a_stale_line(self):
        """连查两个相邻的轮询时刻，第二个不许沿用第一个的答案（D-17 的另一半危害）。

        旧实现的"缓存命中窗口"是 ``[cached_time, cached_time+100]``：它把**上一次查询的
        结果**当成了"接下来 100ms 内的正确结果"。而 100ms 恰好是 `POLL_ACTIVE_MS`，于是
        每到换行点，查询 t=100ms 会命中 t=0ms 那条缓存并返回**上一行**（撤掉修复后实测：
        ``assert 'v0-0' == 'v0-1'``）。这条用例把"边界必须精确"钉死，缓存的任何形式复发
        都会在这里红。
        """
        parser = LyricParser()
        parser.parse(_lrc("v0", 5))
        assert parser.get_line_at(0).text == "v0-0"
        assert parser.get_line_at(100).text == "v0-1"
        assert parser.get_line_at(200).text == "v0-2"
        assert parser.get_line_at(100).text == "v0-1"  # 倒着查也一样精确

    def test_the_removed_read_cache_is_really_gone(self):
        """"读路径共享 LRU"（`_search_cache` / `_cache_max`）必须不存在了。

        将来若为了性能要把索引塞回快照，那必须是**构造期算好的只读数据**，
        并保证上面那条"调用前后 __dict__ 不变"仍然绿。
        """
        parser = LyricParser()
        assert not hasattr(parser, "_search_cache")
        assert not hasattr(parser, "_cache_max")


# ══════════════════════════════════════════════════════════════════════
# 3. D-10：一边解析一边读（真竞态）
# ══════════════════════════════════════════════════════════════════════


class TestConcurrentParseAndRead:
    def test_reader_never_sees_a_half_built_version(self):
        """一个线程反复 clear()+parse()，另一个线程反复读：读者只能看到完整版本。

        断言与线程调度无关（任何交错都必须成立），所以它不会"偶尔绿"：
        * `len(parser.lines)` 只能是 0（还没解析 / 刚 clear）或 LINES_PER_VERSION；
        * 同一份快照里的行同版本；
        * 对任意 t，`get_line_at(t)` 命中行的下标必须是 t//100（时间轴固定）。
        """
        parser = LyricParser()
        versions = [_lrc(tag) for tag in VERSION_TAGS]
        failures: list = []
        reads = [0]
        start = threading.Barrier(2, timeout=15)
        done = threading.Event()

        def reader():
            try:
                start.wait()
                while not done.is_set():
                    reads[0] += 1
                    lines = parser.lines
                    if len(lines) not in (0, LINES_PER_VERSION):
                        failures.append(
                            f"读者看到中间态：len(lines)={len(lines)}，"
                            f"前几行={[ln.text for ln in list(lines)[:4]]}"
                        )
                        return
                    if len({_split_version(ln.text)[0] for ln in lines}) > 1:
                        failures.append(f"读者看到混版：{[ln.text for ln in list(lines)[:4]]}")
                        return
                    for elapsed in (0, 700, 2500, 5900):
                        current = parser.get_line_at(elapsed)
                        if current is None:
                            continue  # 只有"还没解析 / 已 clear"时才允许没有当前行
                        tag, index = _split_version(current.text)
                        want = _expected_index(elapsed)
                        if tag not in VERSION_TAGS or index != want or current.time != want * 100:
                            failures.append(
                                f"t={elapsed} 命中错行：{current.text!r} time={current.time}"
                                f"（应为第 {want} 行 = {want * 100}ms）"
                            )
                            return
            except Exception as exc:  # 旧实现的缓存迭代会在这里抛 RuntimeError
                failures.append(f"读者抛异常：{type(exc).__name__}: {exc}")

        def writer():
            try:
                start.wait()
                for n in range(PARSE_ROUNDS):
                    if n % 7 == 0:
                        parser.clear()  # 真实路径 load_lyric 就是 clear() + parse()
                    parser.parse(versions[n % len(versions)])
            except Exception as exc:
                failures.append(f"写者抛异常：{type(exc).__name__}: {exc}")
            finally:
                done.set()

        reader_thread = threading.Thread(target=reader, daemon=True)
        writer_thread = threading.Thread(target=writer, daemon=True)
        reader_thread.start()
        writer_thread.start()
        reader_thread.join(timeout=30)
        writer_thread.join(timeout=30)

        assert not reader_thread.is_alive() and not writer_thread.is_alive(), "线程没停下来"
        assert failures == [], failures[0] if failures else ""
        assert reads[0] >= PARSE_ROUNDS, f"读轮次太少，用例没真正交错：reads={reads[0]}"


# ══════════════════════════════════════════════════════════════════════
# 4. D-18：边界语义
# ══════════════════════════════════════════════════════════════════════


class TestGetLineAtBoundaries:
    def test_empty_lyrics_returns_none(self):
        parser = LyricParser()
        assert parser.get_line_at(0) is None
        assert parser.get_line_at(10_000) is None
        parser.parse("")  # 空文本 = 空快照
        assert parser.get_line_at(0) is None
        assert parser.lines == ()

    def test_before_the_first_line_returns_none(self):
        parser = LyricParser()
        parser.parse("[00:10.00]later")
        assert parser.get_line_at(0) is None
        assert parser.get_line_at(9999) is None
        assert parser.get_line_at(10_000).text == "later"

    def test_single_line_is_returned_at_and_after_its_timestamp(self):
        parser = LyricParser()
        parser.parse("[00:01.00]only")
        assert parser.get_line_at(999) is None
        assert parser.get_line_at(1000).text == "only"
        assert parser.get_line_at(10_000_000).text == "only"

    def test_exactly_on_a_boundary_takes_that_line_not_the_previous_one(self):
        parser = LyricParser()
        parser.parse(_lrc("v0", 5))
        for i in range(5):
            assert parser.get_line_at(i * 100).text == f"v0-{i}"
        assert parser.get_line_at(-1) is None  # 第一行之前没有行
        for i in range(1, 5):
            assert parser.get_line_at(i * 100 - 1).text == f"v0-{i - 1}"

    def test_after_the_last_line_keeps_returning_the_last_line(self):
        parser = LyricParser()
        parser.parse(_lrc("v0", 5))
        assert parser.get_line_at(400).text == "v0-4"
        assert parser.get_line_at(10 * 60 * 1000).text == "v0-4"

    def test_identical_timestamps_return_the_last_of_them(self):
        """时间戳全相同时二分取**上界**（最后一个 time <= t 的行）。

        与 `find_current_line` 一致；旧实现那段"二分之后再纠正下标"的分支在这里恒假，
        删掉它不改变本结果。
        """
        parser = LyricParser()
        parser.parse("[00:01.00]a\n[00:01.00]b\n[00:01.00]c")
        assert [ln.text for ln in parser.lines] == ["a", "b", "c"]
        assert parser.get_line_at(999) is None
        assert parser.get_line_at(1000).text == "c"
        assert parser.get_line_at(1001).text == "c"

    def test_out_of_order_timestamps_are_sorted_by_parse(self):
        parser = LyricParser()
        parser.parse("[00:03.00]c\n[00:01.00]a\n[00:02.00]b")
        assert [ln.text for ln in parser.lines] == ["a", "b", "c"]
        assert parser.get_line_at(0) is None
        assert parser.get_line_at(1000).text == "a"
        assert parser.get_line_at(2999).text == "b"
        assert parser.get_line_at(3000).text == "c"

    def test_the_two_binary_searches_keep_their_documented_divergence(self):
        """两处二分的"没命中"表示法不同，这是**有记录的分叉**（D-18），不是巧合：

        * `LyricParser.get_line_at` 返回 ``None``；
        * `services.desktop_lyric.find_current_line` 返回 ``-1``（它是索引函数）。

        阶段 3 若统一成一种表示法，请连同本用例一起改。
        """
        empty = LyricParser()
        assert empty.get_line_at(0) is None
        assert desktop_lyric.find_current_line((), 0) == -1

        parser = LyricParser()
        parser.parse("[00:10.00]later")
        assert parser.get_line_at(0) is None
        assert desktop_lyric.find_current_line(parser.lines, 0) == -1
        assert desktop_lyric.find_current_line(parser.lines, 60_000) == 0


# ══════════════════════════════════════════════════════════════════════
# 5. D-16：逐字时序偏移是**已知缺陷**，本轮只留记号
# ══════════════════════════════════════════════════════════════════════


class TestWordTimingKnownDefect:
    def test_word_timing_offset_records_the_status_quo_d16(self):
        """钉住**现状**（不是钉住"正确"）：`<100,200>你<400,300>好` 得到 [300, 700]。

        标准 LRC 逐字语义是"标签后的文本从 s 起算"，即应为 [100, 400] —— 现状整体晚
        一个 duration，这就是 D-16。今天 `words` / `is_word_based` / `end_time` 全仓零
        消费者，本轮按裁决**不改行为**，只留这条会红的记号：等阶段 3.18 做逐字（卡拉OK）
        渲染时改这里，并同时更新本断言。
        """
        parser = LyricParser()
        parser.parse("[00:00.00]<100,200>你<400,300>好")
        line = parser.lines[0]
        assert line.text == "你好"
        assert line.is_word_based is True
        assert [(w.text, w.start, w.duration) for w in line.words] == [
            ("你", 300, 300),  # 标准语义应为 100
            ("好", 700, 300),  # 标准语义应为 400
        ]
        assert line.end_time == 1000
