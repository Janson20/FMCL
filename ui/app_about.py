"""ModernApp 关于 Mixin - 关于标签页（markdown→HTML 渲染）"""
import sys
from pathlib import Path

import customtkinter as ctk
import markdown
from tkinterweb import HtmlFrame

from ui.constants import COLORS
from ui.i18n import _

_TERMS_DARK_CSS = """
body {
    background-color: #16213e;
    color: #a0a0b0;
    font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
    font-size: 15px;
    line-height: 1.8;
    padding: 14px 20px;
    margin: 0;
}
h1 {
    color: #ffffff;
    font-size: 24px;
    font-weight: bold;
    border-bottom: 2px solid #e94560;
    padding-bottom: 8px;
    margin: 0 0 16px 0;
}
h2 {
    color: #ffffff;
    font-size: 18px;
    font-weight: bold;
    border-left: 4px solid #e94560;
    padding-left: 12px;
    margin: 24px 0 10px 0;
}
strong {
    color: #ffffff;
}
hr {
    border: none;
    border-top: 1px solid #2d3a5c;
    margin: 16px 0;
}
ul, ol {
    padding-left: 28px;
    margin: 8px 0;
}
li {
    margin-bottom: 6px;
    color: #a0a0b0;
}
a {
    color: #e94560;
    text-decoration: none;
}
a:hover {
    color: #ff6b81;
    text-decoration: underline;
}
p {
    margin: 6px 0;
}
code {
    background: #1a1a2e;
    color: #e94560;
    padding: 1px 5px;
    border-radius: 3px;
    font-family: "Consolas", "Courier New", monospace;
    font-size: 13px;
}
blockquote {
    border-left: 3px solid #e94560;
    margin: 8px 0;
    padding: 4px 12px;
    color: #c0c0d0;
    background: #1a1a2e;
    border-radius: 0 4px 4px 0;
}
pre {
    background: #1a1a2e;
    border: 1px solid #2d3a5c;
    border-radius: 4px;
    padding: 10px;
    overflow-x: auto;
}
pre code {
    background: none;
    padding: 0;
}
table {
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;
}
th, td {
    border: 1px solid #2d3a5c;
    padding: 6px 10px;
    text-align: left;
    color: #a0a0b0;
}
th {
    background: #1a1a2e;
    color: #ffffff;
    font-weight: bold;
}
"""


def _get_terms_md_path() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS) / "TERMS_OF_USE.md"
    return Path(__file__).parent.parent / "TERMS_OF_USE.md"


def _load_terms_md() -> str:
    path = _get_terms_md_path()
    if not path.exists():
        return _("about_terms_not_found")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return _("about_terms_not_found")


def _md_to_html(md_text: str) -> str:
    body_html = markdown.markdown(
        md_text,
        extensions=["extra", "codehilite", "fenced_code"],
        output_format="html5",
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><style>{_TERMS_DARK_CSS}</style></head>
<body>{body_html}</body>
</html>"""


def _build_terms_html_frame(parent, md_text: str = None) -> HtmlFrame:
    if md_text is None:
        md_text = _load_terms_md()
    html = _md_to_html(md_text)

    frame = HtmlFrame(
        parent,
        messages_enabled=False,
        images_enabled=False,
        forms_enabled=False,
        objects_enabled=False,
        javascript_enabled=False,
        dark_theme_enabled=True,
        vertical_scrollbar=True,
    )
    frame.load_html(html)
    return frame


class AboutTabMixin(object):
    """关于标签页 Mixin"""

    def _build_about_tab_content(self):
        content = ctk.CTkFrame(self.about_tab, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True)

        self._about_html_frame = _build_terms_html_frame(content)
        self._about_html_frame.pack(fill=ctk.BOTH, expand=True, padx=5, pady=(10, 10))
