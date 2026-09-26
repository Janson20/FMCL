"""D-86 日志行数上限的回归测试。

这里**刻意用一个模拟 Tk 行语义的假控件**做主要断言：裁剪逻辑的核心是行号
算术，而 Tk 有一个反直觉的约定 —— ``Text`` 末尾总有一个用户无法删除的换行，
所以 ``index("end-1c")`` 的行号 = 内容行数 + 1。

这个 +1 不是推导出来的，是我用真实 ``CTkTextbox`` 写入 3 倍上限的行数时
**多删了一行**才发现的（首行成了第 10001 行而不是第 10000 行）。
假控件把这条约定固化下来，算术一旦再写错就会立刻失败。

另有一个真实控件用例：Tk 不可用（无显示环境）时 skip，而不是假装通过。
"""

from __future__ import annotations

import pytest

from ui.log_widget import LOG_VIEW_MAX_LINES, append_line, line_count


class FakeText:
    """模拟 Tk ``Text`` 的行语义。

    - 内容以换行结尾；
    - ``index("end-1c")`` 返回 ``"内容行数+1.0"``（Tk 末尾那个删不掉的换行）；
    - ``delete("1.0", "K.0")`` 删除第 1..K-1 行。
    """

    def __init__(self, fail_insert: bool = False):
        self.lines: list[str] = []
        self.fail_insert = fail_insert
        self.see_calls = 0

    def insert(self, index, text):
        if self.fail_insert:
            raise RuntimeError("invalid command name")  # 模拟控件已销毁
        assert index == "end", f"只应追加到末尾，收到 {index!r}"
        self.lines.extend(text.split("\n")[:-1])

    def index(self, spec):
        assert spec == "end-1c", spec
        return f"{len(self.lines) + 1}.0"

    def delete(self, start, end):
        assert start == "1.0", start
        k = int(str(end).split(".")[0])
        del self.lines[: k - 1]

    def see(self, index):
        self.see_calls += 1

    def dump(self) -> list[str]:
        return list(self.lines)


class TestAppendLine:
    def test_keeps_only_the_most_recent_lines(self):
        widget = FakeText()
        total = LOG_VIEW_MAX_LINES + 10
        removed = 0
        for i in range(total):
            removed += append_line(widget, f"line-{i}")

        assert len(widget.dump()) == LOG_VIEW_MAX_LINES
        assert removed == 10, f"应恰好删掉 10 行，实际 {removed}"
        assert widget.dump()[0] == "line-10", f"首行应为 line-10，实际 {widget.dump()[0]!r}"
        assert widget.dump()[-1] == f"line-{total - 1}"

    def test_does_not_trim_below_limit(self):
        widget = FakeText()
        for i in range(100):
            assert append_line(widget, f"x{i}") == 0
        assert len(widget.dump()) == 100
        assert line_count(widget) == 100

    def test_line_count_matches_content_lines(self):
        """``line_count`` 必须是内容行数，不能把 Tk 末尾那个换行也算进去。"""
        for n in (0, 1, 2, 7):
            widget = FakeText()
            for i in range(n):
                widget.insert("end", f"l{i}\n")
            assert line_count(widget) == n, f"{n} 行时 line_count 返回 {line_count(widget)}"

    def test_exactly_at_limit_is_not_trimmed(self):
        widget = FakeText()
        for i in range(LOG_VIEW_MAX_LINES):
            assert append_line(widget, f"l{i}") == 0
        assert len(widget.dump()) == LOG_VIEW_MAX_LINES

    def test_max_lines_zero_disables_trimming(self):
        widget = FakeText()
        for i in range(50):
            assert append_line(widget, f"l{i}", max_lines=0) == 0
        assert len(widget.dump()) == 50

    def test_custom_small_limit(self):
        widget = FakeText()
        for i in range(25):
            append_line(widget, f"l{i}", max_lines=10)
        assert widget.dump() == [f"l{i}" for i in range(15, 25)]

    def test_dead_widget_returns_minus_one_and_never_raises(self):
        widget = FakeText(fail_insert=True)
        assert append_line(widget, "boom") == -1

    def test_see_is_called_each_time(self):
        widget = FakeText()
        append_line(widget, "a")
        append_line(widget, "b")
        assert widget.see_calls == 2, "应始终滚动到底部"


@pytest.fixture(scope="module")
def tk_root():
    """整个模块共用一个 Tk root。

    为什么不用函数级 fixture：实测在同一个 Python 进程里 **destroy() 之后再
    new 一个 Tk() 会失败**（报 ``Can't find a usable tk.tcl ...``），表现为
    "两个真实控件用例里随机一个被跳过"。Tk 的脚本库搜索状态在一次销毁后不再
    可靠，所以每进程只建一次 root、每个用例只新建控件。

    Tk 真的不可用（无显示环境）时 skip，而不是假装通过。
    """
    ctk = pytest.importorskip("customtkinter")
    try:
        root = ctk.CTk()
    except Exception as e:  # noqa: BLE001 - 无显示环境 / Tcl 库缺失
        pytest.skip(f"Tk 不可用，跳过真实控件用例: {e}")
    root.withdraw()
    try:
        yield root
    finally:
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass


class TestRealWidget:
    """真实 CTkTextbox 的端到端验证。"""

    @pytest.fixture
    def widget(self, tk_root):
        ctk = pytest.importorskip("customtkinter")
        box = ctk.CTkTextbox(tk_root, width=200, height=100)
        box.pack()
        box.configure(state="disabled")
        try:
            yield box
        finally:
            try:
                box.destroy()
            except Exception:  # noqa: BLE001
                pass

    def test_real_widget_is_capped_and_keeps_the_newest(self, widget):
        total = LOG_VIEW_MAX_LINES + 250
        for i in range(total):
            widget.configure(state="normal")
            append_line(widget, f"row-{i}")
            widget.configure(state="disabled")

        content = widget.get("1.0", "end-1c").rstrip("\n").split("\n")
        assert len(content) == LOG_VIEW_MAX_LINES, f"实际 {len(content)} 行"
        assert content[0] == "row-250", f"首行 {content[0]!r}"
        assert content[-1] == f"row-{total - 1}"
        assert line_count(widget) == LOG_VIEW_MAX_LINES

    def test_real_widget_below_limit_untouched(self, widget):
        for i in range(120):
            widget.configure(state="normal")
            append_line(widget, f"row-{i}")
            widget.configure(state="disabled")
        content = widget.get("1.0", "end-1c").rstrip("\n").split("\n")
        assert len(content) == 120
        assert content[0] == "row-0", "未达上限时不该丢掉最早的行"
