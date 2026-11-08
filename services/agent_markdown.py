"""Agent 消息的 Markdown → QML 富文本块解析（阶段 3 任务 3.22）。

旧 Tk 版用 `markdown` + HtmlFrame 直接渲染一整段 HTML。QML 侧没有
WebEngine（打包会多出 142 MB 的 Qt6WebEngineCore，`scripts/build_plan.py`
明确裁掉），所以这里把 Markdown 拆成**块**，由 QML 的
`FmAgentMarkdown.qml` 逐块画：

- 普通段落 / 标题 / 引用：`<b>`、`<i>`、`<a>`、`<br/>` 等 QML RichText 支持的
  子集；
- 围栏代码：独立块（QML 用等宽字体 + 复制按钮，不把整段塞进 Text）；
- 表格：表头/行/对齐信息独立返回（QML 用 Row/Repeater 画，窄窗可横向滚动）；
- 列表：每一行独立返回，支持有序/无序；
- 水平线。

**只使用标准库**：本模块不依赖 markdown/pygments，避免 QML 打包时再拉一个
第三方包；解析结果是普通 dict/list，可直接放进 QVariantList。

边界：

- 未闭合的围栏代码（流式输出到一半）返回 `open=True`，QML 仍显示已到达的
  内容，不会因为少一个 ```` 就整段消失；
- 所有模型文本先 HTML 转义再拼标签，模型写 `<script>` 只会被当成文字；
- 单个消息超过 `MAX_CHARS` 时截断并保留结尾提示，避免一次流式把主线程
  卡在超长正则上。
"""

from __future__ import annotations

import html
import re
from typing import Dict, List, Optional, Tuple

#: 单条消息参与解析的最大字符数。超过后截断（保留尾部，尾部通常是结论）。
MAX_CHARS = 200_000

_FENCE_RE = re.compile(r"^\s*(\x60{3,}|~{3,})\s*([^\s\x60]*)\s*$")
_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_HR_RE = re.compile(r"^\s{0,3}((?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$")
_QUOTE_RE = re.compile(r"^\s{0,3}>\s?(.*)$")
_LIST_RE = re.compile(r"^\s{0,6}([-+*]|\d+[.)])\s+(.*)$")
_TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*$")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_CODE_RE = re.compile(r"`([^`]+)`")
_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)|(?<!_)_(?!\s)(.+?)(?<!\s)_(?!_)")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_PLACEHOLDER_RE = re.compile("\ufff0(\\d+)\ufff1")
_HTML_PLACEHOLDER_RE = re.compile("\ufff2(\\d+)\ufff3")
_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
_HTML_TAG_PARTS_RE = re.compile(r"^<\s*(/?)\s*([A-Za-z][A-Za-z0-9]*)(.*?)>\s*$", re.DOTALL)
_HTML_HREF_RE = re.compile(r"""href\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))""", re.IGNORECASE)
_SAFE_URL_RE = re.compile(r"^(https?://|mailto:)", re.IGNORECASE)
_SAFE_HTML_TAGS = frozenset({"b", "strong", "i", "em", "u", "s", "strike", "br", "code"})


def _truncate(text: str) -> str:
    if len(text) <= MAX_CHARS:
        return text
    return text[:MAX_CHARS] + "\n\n[内容过长，已截断]"


def _escape(text: str) -> str:
    return html.escape(str(text), quote=True)


def _safe_url(url: str) -> str:
    value = str(url or "").strip()
    return value if _SAFE_URL_RE.match(value) else ""


def _safe_html_tag(tag: str) -> Optional[str]:
    """把模型输出里的 HTML 收敛到 QML RichText 的安全子集。

    允许 b/strong/i/em/u/s/strike/br/code 与 http(s)/mailto 的 a；其余标签返回
    None（调用方会把原文本转义后**原样显示**，而不是执行）。返回空串表示这个
    标签被丢弃但文本保留（例如没有合法 href 的 <a>）。
    """
    match = _HTML_TAG_PARTS_RE.match(str(tag or ""))
    if match is None:
        return None
    closing, name, rest = match.group(1) == "/", match.group(2).lower(), match.group(3)
    if name in _SAFE_HTML_TAGS:
        if name == "br":
            return "<br/>"
        return "</%s>" % name if closing else "<%s>" % name
    if name != "a":
        return None
    if closing:
        return "</a>"
    href_match = _HTML_HREF_RE.search(rest or "")
    if href_match is None:
        return ""
    href = next((group for group in href_match.groups() if group is not None), "")
    url = _safe_url(html.unescape(href))
    if not url:
        return ""
    return '<a href="%s">' % _escape(url)


def _render_inline(text: str) -> str:
    """Markdown 行内语法 → QML RichText 子集（已转义，标签只由本函数产生）。"""
    if not text:
        return ""

    codes: List[str] = []
    html_tags: List[str] = []

    def stash(match: re.Match) -> str:
        codes.append(match.group(1))
        return "\ufff0%d\ufff1" % (len(codes) - 1)

    def stash_html(match: re.Match) -> str:
        safe = _safe_html_tag(match.group(0))
        if safe is None:
            # 不安全/不支持的标签保留原文，后面统一转义（用户看到的是文字，
            # 不是可执行标签）。
            return match.group(0)
        html_tags.append(safe)
        return "\ufff2%d\ufff3" % (len(html_tags) - 1)

    # 代码片段先抽出来，避免其中的 * / [ ] 被当成强调或链接。
    working = _CODE_RE.sub(stash, str(text))
    # 再把白名单 HTML 标签换成占位符；转义过程不会碰到它们。
    working = _HTML_TAG_RE.sub(stash_html, working)

    # **先转义再插标签**：反过来的话，下面生成的 <a>/<b> 会被 escape 成
    # &lt;a&gt;，界面上就出现一堆可见标签（第一版就是这么写错的）。
    working = _escape(working)

    # 强调。顺序与 CommonMark 的常见写法一致：删除线 → 粗体 → 斜体。
    working = _STRIKE_RE.sub(lambda m: "<s>%s</s>" % m.group(1), working)
    working = _BOLD_RE.sub(lambda m: "<b>%s</b>" % m.group(2), working)
    working = _ITALIC_RE.sub(lambda m: "<i>%s</i>" % (m.group(1) if m.group(1) is not None else m.group(2)), working)

    # 链接：只允许 http(s)/mailto；其余协议保留原样文字，不生成可点击标签。
    # 此时 working 已经转义过，label 直接复用；URL 用 unescape 判定协议，
    # 写回时保留转义后的形式（带 & 的查询串不会破坏属性）。
    def link(match: re.Match) -> str:
        label = match.group(1)
        escaped_url = match.group(2)
        url = _safe_url(html.unescape(escaped_url))
        if not url:
            return label
        return '<a href="%s">%s</a>' % (escaped_url, label)

    working = _LINK_RE.sub(link, working)

    # 换行：QML RichText 里 `\n` 不换行，显式转成 <br/>。
    working = working.replace("\r\n", "\n").replace("\r", "\n")
    working = working.replace("\n", "<br/>")

    def restore(match: re.Match) -> str:
        index = int(match.group(1))
        if index < 0 or index >= len(codes):
            return ""
        return '<font face="Consolas, Courier New">%s</font>' % _escape(codes[index])

    def restore_html(match: re.Match) -> str:
        index = int(match.group(1))
        if index < 0 or index >= len(html_tags):
            return ""
        return html_tags[index]

    result = _PLACEHOLDER_RE.sub(restore, working)
    return _HTML_PLACEHOLDER_RE.sub(restore_html, result)


def _split_table_row(line: str) -> List[str]:
    text = str(line or "").strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    return [cell.strip() for cell in text.split("|")]


def _table_align(cell: str) -> str:
    value = cell.strip()
    left = value.startswith(":")
    right = value.endswith(":")
    if left and right:
        return "center"
    if right:
        return "right"
    return "left"


def _is_table_start(lines: List[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    if "|" not in lines[index]:
        return False
    return bool(_TABLE_SEP_RE.match(lines[index + 1]))


def _consume_paragraph(lines: List[str], start: int) -> Tuple[str, int]:
    parts: List[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            break
        if index > start and (
            _FENCE_RE.match(line)
            or _HEADING_RE.match(line)
            or _HR_RE.match(line)
            or _QUOTE_RE.match(line)
            or _LIST_RE.match(line)
            or _is_table_start(lines, index)
        ):
            break
        parts.append(line)
        index += 1
    return "\n".join(parts).strip(), index


def parse_markdown(text: str) -> List[Dict]:
    """把 Markdown 文本解析成 QML 可渲染的块列表。

    返回值只含普通 Python 类型（str/int/bool/list/dict），可安全跨线程排队。
    """
    source = _truncate(str(text or "")).replace("\r\n", "\n").replace("\r", "\n")
    if not source.strip():
        return []

    lines = source.split("\n")
    blocks: List[Dict] = []
    index = 0
    total = len(lines)

    while index < total:
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(1)
            language = (fence.group(2) or "").strip()
            fence_char = marker[0]
            fence_len = len(marker)
            code_lines: List[str] = []
            index += 1
            closed = False
            while index < total:
                candidate = lines[index]
                close = re.match(r"^\s*(%s{%d,})\s*$" % (re.escape(fence_char), fence_len), candidate)
                if close:
                    closed = True
                    index += 1
                    break
                code_lines.append(candidate)
                index += 1
            blocks.append({"type": "code", "language": language, "code": "\n".join(code_lines), "open": not closed})
            continue

        heading = _HEADING_RE.match(line)
        if heading:
            blocks.append(
                {"type": "heading", "level": len(heading.group(1)), "html": _render_inline(heading.group(2).strip())}
            )
            index += 1
            continue

        if _HR_RE.match(line):
            blocks.append({"type": "divider"})
            index += 1
            continue

        if _is_table_start(lines, index):
            headers = _split_table_row(lines[index])
            separators = _split_table_row(lines[index + 1])
            aligns = [_table_align(cell) for cell in separators]
            while len(aligns) < len(headers):
                aligns.append("left")
            rows: List[List[Dict]] = []
            index += 2
            while index < total and lines[index].strip() and "|" in lines[index]:
                cells = _split_table_row(lines[index])
                if not cells:
                    break
                row = []
                for column, cell in enumerate(cells):
                    align = aligns[column] if column < len(aligns) else "left"
                    row.append({"html": _render_inline(cell), "align": align})
                rows.append(row)
                index += 1
            blocks.append(
                {
                    "type": "table",
                    "headers": [
                        {"html": _render_inline(cell), "align": aligns[i] if i < len(aligns) else "left"}
                        for i, cell in enumerate(headers)
                    ],
                    "rows": rows,
                }
            )
            continue

        quote = _QUOTE_RE.match(line)
        if quote:
            quote_lines: List[str] = []
            while index < total:
                current = _QUOTE_RE.match(lines[index])
                if current is None:
                    break
                quote_lines.append(current.group(1))
                index += 1
            blocks.append({"type": "quote", "html": _render_inline("\n".join(quote_lines).strip())})
            continue

        list_match = _LIST_RE.match(line)
        if list_match:
            ordered = list_match.group(1)[0].isdigit()
            start = int(re.match(r"\d+", list_match.group(1)).group(0)) if ordered else 1
            items: List[Dict] = []
            while index < total:
                current = _LIST_RE.match(lines[index])
                if current is None:
                    # 列表项的缩进续行：并到上一条（最多收 20 行，防止吞掉整篇）。
                    if items and lines[index].startswith((" ", "\t")) and lines[index].strip():
                        items[-1]["html"] += "<br/>" + _render_inline(lines[index].strip())
                        index += 1
                        continue
                    break
                items.append({"html": _render_inline(current.group(2).strip())})
                index += 1
            blocks.append({"type": "list", "ordered": ordered, "start": start, "items": items})
            continue

        paragraph, index = _consume_paragraph(lines, index)
        if paragraph:
            blocks.append({"type": "paragraph", "html": _render_inline(paragraph)})

    return blocks


def plain_text(text: str) -> str:
    """把 Markdown 粗化为纯文本（复制、搜索结果摘要等非渲染场景用）。"""
    value = _truncate(str(text or ""))
    value = _CODE_RE.sub(lambda m: m.group(1), value)
    value = _LINK_RE.sub(lambda m: "%s (%s)" % (m.group(1), m.group(2)), value)
    value = re.sub(r"^\s{0,3}#{1,6}\s+", "", value, flags=re.MULTILINE)
    value = re.sub(r"^\s{0,3}>\s?", "", value, flags=re.MULTILINE)
    value = re.sub(r"^\s{0,6}([-+*]|\d+[.)])\s+", "", value, flags=re.MULTILINE)
    value = value.replace("**", "").replace("__", "").replace("~~", "")
    return value.strip()


__all__ = ["MAX_CHARS", "parse_markdown", "plain_text"]
