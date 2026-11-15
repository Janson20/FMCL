"""WebSearch 工具 - 联网搜索

后端按可用性回退：
1. Bing Web Search API（配置了 BING_API_KEY）
2. Bing 网页搜索（免费，无需 Key）
3. DuckDuckGo HTML（免费后端，部分网络会超时）
"""

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, List, Optional

from logzero import logger

from services.agent.tools.base import CATEGORY_WEB, ToolInfo

# 默认 User-Agent
DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 FMCL/2.0"


def _build_web_search_tool() -> ToolInfo:
    return ToolInfo(
        name="web_search",
        display_name="联网搜索",
        description="搜索网络获取实时信息，用于获取知识截止日期之后的信息。支持自定义结果数量、搜索类型",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词"},
                "num": {
                    "type": "integer",
                    "description": "搜索结果数量（默认 5，最大 20）",
                    "maximum": 20,
                    "minimum": 1,
                },
            },
            "required": ["query"],
        },
        category=CATEGORY_WEB,
        execute=_web_search,
        permission_action="web_search",
    )


def _web_search(params: Dict[str, str], callbacks: Dict[str, Callable]) -> str:
    query = params.get("query", "").strip()
    if not query:
        return "错误: 缺少 query 参数"

    try:
        num = int(params.get("num", 5))
    except (TypeError, ValueError):
        num = 5
    num = min(max(num, 1), 20)

    bing_key = _get_bing_api_key(callbacks)
    if bing_key:
        return _search_bing(query, num, bing_key)

    results = _search_bing_html(query, num)
    if results:
        return _format_results(query, results, "Bing")

    # Bing 网页偶发被风控时再试 DuckDuckGo；部分网络下它可能超时，
    # 因此只作为最后兜底，并把真实失败原因带回给模型。
    return _search_duckduckgo(query, num)


def _get_bing_api_key(callbacks: Dict[str, Callable]) -> str:
    """从 callbacks 获取 Bing API Key"""
    if "get_config" in callbacks:
        config = callbacks["get_config"]()
        return config.get("bing_api_key", "") if isinstance(config, dict) else ""
    return ""


def _search_bing_html(query: str, num: int = 5) -> Optional[List[dict]]:
    # 免费 Bing 网页搜索；部分网络下 Bing 网页可达而 DuckDuckGo 会 ConnectTimeout。
    try:
        params = urllib.parse.urlencode({"q": query, "count": str(min(max(num, 1), 20)), "setlang": "zh-Hans"})
        req = urllib.request.Request(
            "https://www.bing.com/search?" + params,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            },
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            page = resp.read().decode("utf-8", errors="replace")

        results: List[dict] = []
        blocks = re.findall(r'<li[^>]*class="[^"]*\bb_algo\b[^"]*"[^>]*>.*?</li>', page, re.DOTALL | re.IGNORECASE)
        for block in blocks:
            title_match = re.search(
                r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', block, re.DOTALL | re.IGNORECASE
            )
            if not title_match:
                continue
            url = html.unescape(title_match.group(1)).strip()
            if url.startswith("/"):
                url = "https://www.bing.com" + url
            title = _clean_html(title_match.group(2))
            snippet_match = re.search(r"<p[^>]*>(.*?)</p>", block, re.DOTALL | re.IGNORECASE)
            snippet = _clean_html(snippet_match.group(1)) if snippet_match else ""
            if title and url:
                results.append({"title": title, "url": url, "snippet": snippet or "(无简介)"})
            if len(results) >= num:
                break
        return results
    except Exception as e:  # noqa: BLE001 - 网络失败交给上层回退
        logger.warning(f"Bing 网页搜索失败: {e}")
        return None


def _clean_html(value: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", value)).strip()


def _format_results(query: str, results: List[dict], backend: str) -> str:
    output = f"搜索: {query}\n找到 {len(results)} 条结果 ({backend}):\n\n"
    for i, item in enumerate(results, 1):
        output += f"{i}. {item['title']}\n"
        output += f"   URL: {item['url']}\n"
        output += f"   {item['snippet']}\n\n"
    return output


def _search_duckduckgo(query: str, num: int = 5) -> str:
    """使用 DuckDuckGo Instant Answer API 搜索（免费）"""
    try:
        # 使用 DuckDuckGo HTML 搜索
        url = "https://html.duckduckgo.com/html/?"
        params = urllib.parse.urlencode({"q": query, "kl": "us-en"})
        full_url = url + params

        req = urllib.request.Request(
            full_url,
            headers={
                "User-Agent": DEFAULT_UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
            },
            method="GET",
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # 简单的 HTML 解析提取结果
        results = _parse_duckduckgo_html(html, num)

        if not results:
            return f"未找到与 '{query}' 相关的结果"

        output = f"搜索: {query}\n找到 {len(results)} 条结果:\n\n"
        for i, r in enumerate(results, 1):
            output += f"{i}. {r['title']}\n"
            output += f"   URL: {r['url']}\n"
            output += f"   {r['snippet']}\n\n"

        return output

    except urllib.error.URLError as e:
        logger.error(f"DuckDuckGo 搜索失败: {e}")
        return f"❌ 搜索失败: 网络错误 ({e})"
    except Exception as e:
        logger.error(f"DuckDuckGo 搜索异常: {e}")
        return f"❌ 搜索失败: {str(e)}"


def _parse_duckduckgo_html(html: str, max_results: int) -> List[dict]:
    """解析 DuckDuckGo HTML 搜索结果"""
    results = []
    import re

    # 匹配每个结果块
    result_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>'
        r'.*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        re.DOTALL | re.IGNORECASE,
    )

    matches = result_pattern.findall(html)
    for url, title, snippet in matches[:max_results]:
        # 清理 HTML 标签
        title = re.sub(r"<[^>]+>", "", title).strip()
        snippet = re.sub(r"<[^>]+>", "", snippet).strip()
        # 清理 URL
        url = urllib.parse.unquote(url) if url else ""
        if url.startswith("//"):
            url = "https:" + url

        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet or "(无简介)"})

    return results


def _search_bing(query: str, num: int, api_key: str) -> str:
    """使用 Bing Web Search API 搜索"""
    try:
        url = "https://api.bing.microsoft.com/v7.0/search"
        params = urllib.parse.urlencode({"q": query, "count": str(min(num, 20)), "mkt": "zh-CN", "textFormat": "Raw"})
        full_url = f"{url}?{params}"

        req = urllib.request.Request(
            full_url, headers={"Ocp-Apim-Subscription-Key": api_key, "User-Agent": DEFAULT_UA}, method="GET"
        )

        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        webpages = data.get("webPages", {}).get("value", [])
        if not webpages:
            return f"未找到与 '{query}' 相关的结果"

        output = f"搜索: {query}\n找到 {len(webpages)} 条结果 (Bing):\n\n"
        for i, page in enumerate(webpages[:num], 1):
            output += f"{i}. {page.get('name', '')}\n"
            output += f"   URL: {page.get('url', '')}\n"
            output += f"   {page.get('snippet', '')}\n\n"

        return output

    except urllib.error.HTTPError as e:
        if e.code == 401:
            return "❌ Bing API Key 无效"
        logger.error(f"Bing 搜索 HTTP 错误: {e.code}")
        return f"❌ 搜索失败: HTTP {e.code}"
    except Exception as e:
        logger.error(f"Bing 搜索失败: {e}")
        return f"❌ 搜索失败: {str(e)}"
