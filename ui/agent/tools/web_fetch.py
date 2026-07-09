"""WebFetch 工具 - 抓取网页内容

支持将网页转换为 Markdown / 纯文本 / HTML 格式。
使用 html.parser 做 HTML→文本转换，简单可靠无依赖。
"""

import re
import urllib.error
import urllib.request
from html.parser import HTMLParser
from typing import Callable, Dict, Optional

from logzero import logger

from ui.agent.tools.base import CATEGORY_WEB, ToolInfo

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 FMCL/2.0"
MAX_RESPONSE_BYTES = 5 * 1024 * 1024  # 5 MB
DEFAULT_TIMEOUT = 30
MAX_TIMEOUT = 120


class HTMLToText(HTMLParser):
    """HTML → 纯文本转换器"""

    def __init__(self):
        super().__init__()
        self._text: list = []
        self._skip_tags = {"script", "style", "noscript", "iframe", "svg", "head"}
        self._block_tags = {
            "p",
            "div",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "li",
            "br",
            "hr",
            "tr",
            "section",
            "article",
            "header",
            "footer",
            "nav",
            "main",
        }
        self._list_tags = {"ul", "ol", "dl"}
        self._current_tag = ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        self._current_tag = tag
        if tag in self._skip_tags:
            self._current_tag = tag
        elif tag in self._block_tags:
            if self._text and self._text[-1] != "\n":
                self._text.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self._block_tags:
            if self._text and self._text[-1] != "\n":
                self._text.append("\n")
        elif tag in self._list_tags:
            self._text.append("\n")
        self._current_tag = ""

    def handle_data(self, data):
        if self._current_tag in self._skip_tags:
            return
        text = data.strip()
        if text:
            self._text.append(text + " ")

    def get_text(self) -> str:
        return "".join(self._text).strip()


def _html_to_markdown(html: str) -> str:
    """简化的 HTML→Markdown 转换

    处理常见元素：标题、链接、加粗、列表项、图片等。
    """
    # 移除脚本和样式
    html = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<(iframe|svg)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)

    # 标题
    for level in range(6, 0, -1):
        pattern = re.compile(rf"<h{level}[^>]*>(.*?)</h{level}>", re.DOTALL | re.IGNORECASE)
        html = pattern.sub(lambda m: f"\n\n{'#' * level} {_strip_tags(m.group(1)).strip()}\n\n", html)

    # 段落
    html = re.sub(r"<p[^>]*>(.*?)</p>", r"\n\n\1\n\n", html, flags=re.DOTALL | re.IGNORECASE)
    # 换行
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)

    # 加粗和斜体
    html = re.sub(r"<(strong|b)[^>]*>(.*?)</\1>", r"**\2**", html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r"<(em|i)[^>]*>(.*?)</\1>", r"*\2*", html, flags=re.DOTALL | re.IGNORECASE)

    # 链接
    html = re.sub(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', r"[\2](\1)", html, flags=re.DOTALL | re.IGNORECASE)

    # 列表项
    html = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", html, flags=re.DOTALL | re.IGNORECASE)

    # 图片
    html = re.sub(r'<img[^>]*src="([^"]*)"[^>]*alt="([^"]*)"[^>]*/?>', r"![\2](\1)", html, flags=re.IGNORECASE)
    html = re.sub(r'<img[^>]*src="([^"]*)"[^>]*/?>', r"![](\\1)", html, flags=re.IGNORECASE)

    # 剩余标签去除
    html = _strip_tags(html)

    # 清理多余空白
    html = re.sub(r"\n{3,}", "\n\n", html)
    return html.strip()


def _strip_tags(html: str) -> str:
    """去除所有 HTML 标签"""
    return re.sub(r"<[^>]+>", "", html)


def _build_web_fetch_tool() -> ToolInfo:
    return ToolInfo(
        name="web_fetch",
        display_name="抓取网页",
        description="获取 HTTP/HTTPS URL 的内容，返回 Markdown/纯文本/HTML 格式。Markdown 为默认格式",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要获取的 HTTP 或 HTTPS URL"},
                "format": {
                    "type": "string",
                    "enum": ["text", "markdown", "html"],
                    "description": "返回格式：markdown（默认），text（纯文本），html（原始 HTML）",
                },
            },
            "required": ["url"],
        },
        category=CATEGORY_WEB,
        execute=_web_fetch,
        permission_action="web_fetch",
    )


def _web_fetch(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    url = params.get("url", "").strip()
    fmt = params.get("format", "markdown").strip()

    if not url:
        return "错误: 缺少 url 参数"
    if not url.startswith("http://") and not url.startswith("https://"):
        return "错误: URL 必须以 http:// 或 https:// 开头"

    if fmt not in ("text", "markdown", "html"):
        fmt = "markdown"

    timeout = DEFAULT_TIMEOUT
    try:
        timeout_val = int(params.get("timeout", DEFAULT_TIMEOUT))
        timeout = min(max(timeout_val, 5), MAX_TIMEOUT)
    except (ValueError, TypeError):
        pass

    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
            },
            method="GET",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content_type = resp.headers.get("Content-Type", "").split(";")[0].strip()

            # 只处理文本类型
            if "html" not in content_type and "text" not in content_type and "xml" not in content_type:
                # 对于非文本类型，返回摘要
                content_length = resp.headers.get("Content-Length", "未知")
                return f"URL: {url}\nContent-Type: {content_type}\nContent-Length: {content_length}\n\n此 URL 返回的是非文本内容 (Content-Type: {content_type})，无法显示其内容。如需查看，请在浏览器中打开。"

            raw_data = b""
            while len(raw_data) < MAX_RESPONSE_BYTES:
                chunk = resp.read(8192)
                if not chunk:
                    break
                raw_data += chunk

            truncated = len(raw_data) >= MAX_RESPONSE_BYTES
            html_content = raw_data.decode("utf-8", errors="replace")

            if fmt == "html":
                result = html_content
            elif fmt == "markdown":
                result = _html_to_markdown(html_content)
            else:
                parser = HTMLToText()
                parser.feed(html_content)
                result = parser.get_text()

            if truncated:
                result += "\n\n[内容已截断，超过 5MB 限制]"

            if not result.strip():
                return f"URL: {url}\n\n页面内容为空或无法解析"

            max_chars = 50000
            if len(result) > max_chars:
                result = result[:max_chars] + f"\n\n[内容已截断，超过 {max_chars} 字符限制]"

            output = f"URL: {url}\nContent-Type: {content_type}\n格式: {fmt}\n\n"
            output += result
            return output

    except urllib.error.HTTPError as e:
        logger.error(f"WebFetch HTTP 错误: {e.code} {url}")
        body = ""
        try:
            body = e.read().decode("utf-8", errors="ignore")[:200]
        except Exception:
            pass
        return f"❌ HTTP {e.code}: {body or '无详细信息'}"
    except urllib.error.URLError as e:
        logger.error(f"WebFetch 网络错误: {e}")
        return f"❌ 网络错误: 无法连接到 {url}"
    except Exception as e:
        logger.error(f"WebFetch 异常: {e}")
        return f"❌ 抓取失败: {str(e)}"
