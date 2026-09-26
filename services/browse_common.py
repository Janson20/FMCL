"""在线浏览类窗口共用的**纯逻辑**（阶段 1 任务 1.8-B）。

适用窗口（六个里的四个"浏览"窗口）：

- ``ui/windows/mod_browser.py``（客户端模组 / 资源包 / 光影，双源）
- ``ui/windows/server_mod_browser.py``（服务端模组，单源 Modrinth）
- ``ui/windows/modpack_browser.py``（整合包）
- ``ui/windows/plugin_browser.py``（插件市场）

## 为什么单独一个模块

这四处的**分页口径、下载量格式化、来源统计、结果去重排序**是逐字相同的
（``_format_downloads`` 在三个窗口里是同一份实现；``_browsable_hits`` 的
"本地条目数口径"是 D-103 的守卫）。抽出来是为了让 QML 界面不必再抄第三份，
而不是为了"看起来整齐"：**没有**把模组/整合包/插件各自的业务（安装目标解析、
格式识别、插件权限）塞进来 —— 那些留在各自的专有服务里。

## 与 ``services/resource_service.py`` 的分页函数关系

``total_pages`` / ``clamp_page`` / ``paginate`` 与 ``services/resource_service.py``
以及 ``services/server_service.py`` 里的同名函数**同名同形同语义**，但**刻意
不互相 import**：三个服务分别服务不同的界面域，跨服务 import 会把依赖图拧成
一张网。``tests/test_browse_common.py`` 有一条网格比对测试钉住三份实现不漂移
（与 ``services/resource_service.py`` 里那条注释说的是同一套办法）。

## 迁移约定

- 本模块只依赖标准库 + ``logzero``；不 import 任何 GUI、不 import ``ui.*``、
  **不 import ``ui.i18n``**（文案一律由调用方拼好传进来）。
- 每个函数都注明它原来是哪个窗口的哪一段，改动一律写清"为什么"。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: 四个浏览窗口的每页条数（各窗口原有的 ``PAGE_SIZE = 10``）。
DEFAULT_PAGE_SIZE: int = 10

#: ``mod_browser`` 三个标签页一次拉取的条数（原 ``limit=300``，"请求大批量以触发多页拉取"）。
BROWSE_BATCH_LIMIT: int = 300

#: 服务端模组窗口的每页条数（原 ``PAGE_SIZE = 10``，与上面同值但语义独立）。
SERVER_SEARCH_PAGE_SIZE: int = 10

#: AI 搜索每个关键词抓取的条数（原 ``max_per_keyword=30``）。
AI_HITS_PER_KEYWORD: int = 30

#: 整合包版本选择器一次渲染多少条（原 ``ModpackBrowserWindow.VERSION_PAGE_SIZE``）。
MODPACK_VERSION_PAGE_SIZE: int = 50

#: 被当成"加载器标签"展示的 CurseForge 分类名（原三个窗口里的同一行白名单）。
LOADER_TAGS: Tuple[str, ...] = ("forge", "fabric", "neoforge", "quilt")

#: 特殊模组加载器 → API 可识别的等效加载器（原类属性 ``MOD_LOADER_COMPAT_MAP``）。
MOD_LOADER_COMPAT_MAP: Dict[str, str] = {"legacyfabric": "fabric", "cleanroom": "forge"}

#: 插件权限 → 风险档（原 ``_create_plugin_card`` 里那两个内联元组，逐字）。
HIGH_RISK_PERMISSIONS: Tuple[str, ...] = ("network.socket", "core.launch_hook", "core.process")
MEDIUM_RISK_PERMISSIONS: Tuple[str, ...] = (
    "filesystem.write",
    "core.download",
    "core.version",
    "data.settings",
)


# ═══════════════════════════════════════════════════════════════════
# 结果包
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class SearchOutcome:
    """一次搜索的结果（对应原文从 ``result`` 字典里取出的三个键）。

    字段与 ``result.get(...)`` 的三个键一一对应，取值与原文逐字一致：
    ``hits`` 用 ``[]``、``total_hits`` 用 ``0``、``sources`` 用 ``{}`` 兜底
    （与 ``_do_tab_search`` / ``_do_search`` 里的默认值相同）。
    """

    hits: List[Dict[str, Any]] = field(default_factory=list)
    total_hits: int = 0
    sources: Dict[str, Any] = field(default_factory=dict)
    #: AI 搜索专用：本次实际使用的关键词（普通搜索恒为空列表）。
    keywords: List[str] = field(default_factory=list)

    def as_publish_args(self) -> Tuple[List[Dict[str, Any]], int, Dict[str, Any]]:
        """``(hits, total_hits, sources)`` —— 便于界面直接喂给原有的发布回调。"""
        return self.hits, self.total_hits, self.sources


def split_result(result: Any) -> SearchOutcome:
    """把后端返回的字典归一成 :class:`SearchOutcome`（原各窗口里的三行取值，逐字）。

    ``result`` 为 ``None`` 或不是字典时**不抛异常**：原文一律是
    ``result.get(...)``，字典有 ``.get``、``None`` 没有 —— 但所有调用点都在
    ``try/except`` 里且真实后端只会返回字典或抛异常，因此这里让
    ``AttributeError`` 照常向上冒（与原文同形），不做静默兜底。
    """
    return SearchOutcome(
        hits=result.get("hits", []),
        total_hits=result.get("total_hits", 0),
        sources=result.get("sources", {}),
        keywords=list(result.get("keywords", []) or []),
    )


# ═══════════════════════════════════════════════════════════════════
# 结果纯逻辑：格式化 / 去重 / 排序 / 来源统计
# ═══════════════════════════════════════════════════════════════════


def format_downloads(count: int) -> str:
    """下载量格式化（原三个窗口里逐字相同的 ``_format_downloads``，逐字）。

    记录现状、疑为缺陷：``count`` 为 ``None`` 时 ``count >= 1_000_000`` 抛
    ``TypeError``（原文如此）—— 后端返回的 ``downloads`` 缺失时用的是
    ``item.get("downloads", 0)``，因此这条路径只在"后端显式给了 None"时可达。
    本轮只搬家不改行为，故不在这里吞掉 ``None``。
    """
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    elif count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


def sort_hits_by_downloads(hits: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按下载量降序（原服务端 AI 搜索里那行 ``merged.sort(...)``，逐字）。

    ``list(...)`` 只影响"入参是元组"的情形：原调用点传的都是新构造的 list，
    因此就地排序与返回新列表对可达输入完全等价。
    """
    ordered = list(hits)
    ordered.sort(key=lambda h: h.get("downloads", 0), reverse=True)
    return ordered


def dedupe_hits_by_project_id(
    hits: Iterable[Dict[str, Any]], seen: Optional[set] = None
) -> List[Dict[str, Any]]:
    """按 ``project_id`` 去重，保持首次出现的顺序（原服务端 AI 搜索的 ``seen_ids`` 循环）。

    ``project_id`` 为空串的条目**被丢弃**（原文的 ``if pid and pid not in seen_ids``），
    与"字段缺失"的差别在这里是可见行为，故逐字保留。
    """
    seen_ids = set() if seen is None else seen
    merged: List[Dict[str, Any]] = []
    for hit in hits:
        pid = hit.get("project_id", "")
        if pid and pid not in seen_ids:
            seen_ids.add(pid)
            merged.append(hit)
    return merged


def merge_sources(*maps: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """合并多份来源统计（同名键相加）。

    用于"两个源各自返回一份 ``sources``"的场景；``curseforge.unified_search_*``
    已经把两个源合并在一个字典里（``{"modrinth": n, "curseforge": m}``），
    那种情况下本函数是直通。``None`` 与缺失的源都跳过。
    """
    merged: Dict[str, int] = {}
    for mapping in maps:
        if not mapping:
            continue
        for key, value in mapping.items():
            merged[key] = merged.get(key, 0) + value
    return merged


def compat_loader(mod_loader: Optional[str]) -> Optional[str]:
    """加载器兼容映射（原 ``ModBrowserWindow._search_loader`` 属性体，逐字）。

    CurseForge / Modrinth API 不识别 ``legacyfabric`` / ``cleanroom``，
    搜索与安装都改用等效加载器；``None`` 直通 ``None``。
    """
    if mod_loader is None:
        return None
    return MOD_LOADER_COMPAT_MAP.get(mod_loader, mod_loader)


def loader_tags(categories: Sequence[str]) -> List[str]:
    """从分类里挑出加载器标签（原三个窗口里那行列表推导 + 白名单，逐字）。"""
    return [c for c in categories if c in LOADER_TAGS]


def loader_tags_text(categories: Sequence[str]) -> str:
    """加载器标签的显示文本（原 ``" | ".join(c.capitalize() for c in loader_tags)``）。

    没有命中白名单时返回空串（原文是不往 ``tag_parts`` 里追加任何东西）。
    """
    tags = loader_tags(categories)
    if not tags:
        return ""
    return " | ".join(c.capitalize() for c in tags)


def tag_bar_display(tag_name: str) -> str:
    """标签筛选栏的按钮文本（原 ``_update_tag_bar`` 里的 ``tag_name[:4]``，逐字）。"""
    return tag_name[:4]


def permission_counts(permissions: Sequence[str]) -> Tuple[int, int, int]:
    """``(高, 中, 低)`` 三档权限数量（原 ``_create_plugin_card`` 里那三行，逐字）。

    "低"是**扣减法**算出来的（``len(permissions) - high - medium``），
    因此重复项与未知权限都计入"低" —— 与原文一致。
    """
    high_count = sum(1 for p in permissions if p in HIGH_RISK_PERMISSIONS)
    medium_count = sum(1 for p in permissions if p in MEDIUM_RISK_PERMISSIONS)
    low_count = len(permissions) - high_count - medium_count
    return high_count, medium_count, low_count


def permission_summary(permissions: Sequence[str]) -> str:
    """权限摘要文本（原 ``| H:1 M:2 L:3`` 的拼装，逐字；无权限返回空串）。"""
    high_count, medium_count, low_count = permission_counts(permissions)
    perm_parts = []
    if high_count:
        perm_parts.append(f"H:{high_count}")
    if medium_count:
        perm_parts.append(f"M:{medium_count}")
    if low_count:
        perm_parts.append(f"L:{low_count}")
    if not perm_parts:
        return ""
    return f"| {' '.join(perm_parts)}"


# ═══════════════════════════════════════════════════════════════════
# 搜索词 / 分页（D-103 口径）
# ═══════════════════════════════════════════════════════════════════


def normalize_search(text: Optional[str]) -> str:
    """搜索词规范化（原各窗口里的 ``.strip()``，逐字）。

    注意：``mod_browser`` / ``modpack_browser`` / ``server_mod_browser`` 用的是
    ``entry.get().strip()``（不去引号、不改大小写），与资源窗口的
    ``.strip().lower()`` **不同**，因此不复用 ``resource_service.normalize_search``。
    """
    return (text or "").strip()


def total_pages(total: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
    """总页数，至少 1 页（原文的 ``max(1, ceil(total / page_size))``，逐字）。"""
    return max(1, (total + page_size - 1) // page_size)


def clamp_page(page: int, pages: int) -> int:
    """页码钳制（原 ``if self._current_page > total_pages: ... = total_pages``）。"""
    return max(1, min(page, pages))


def paginate(items: Sequence[Any], page: int, page_size: int) -> Tuple[List[Any], int, int]:
    """分页计算，返回 ``(当前页条目, 钳制后的页码, 总页数)``。

    与 ``services/resource_service.py`` / ``services/server_service.py`` 的同名函数
    同名同形同语义（空列表也算 1 页、页码越界钳到 ``[1, 总页数]``）。
    """
    pages = total_pages(len(items), page_size)
    current = clamp_page(page, pages)
    start = (current - 1) * page_size
    end = min(start + page_size, len(items))
    return list(items[start:end]), current, pages


def page_slice(items: Sequence[Any], offset: int, page_size: int) -> List[Any]:
    """按**偏移量**取一页（原 ``cached[offset : offset + PAGE_SIZE]``，逐字）。

    浏览类窗口用的是 offset 而不是页码（``current_offset``），所以这里与
    :func:`paginate` 并存：前者给"已知本地缓存、只要切片"的路径用，
    后者给"按页码渲染 + 需要总页数"的路径用。
    """
    return list(items[offset : offset + page_size])


def browsable_count(
    ai_cached_hits: Optional[Sequence[Any]],
    cached_hits: Optional[Sequence[Any]],
    total_hits: int,
) -> int:
    """**本地能翻到的**条目数（阶段 1.23 修正 D-103，原 ``ModBrowserWindow._browsable_hits``）。

    缺陷：请求用 ``limit=300`` 一次拉一批，而后端在 ``total_hits`` 里报的是
    **匹配总数**（可能上千）。原实现用 ``total_hits`` 算页数，于是缓存只有
    300 条时分页却显示"1 / 150"：翻到缓存尾部就是**空页但页码仍在**。

    优先级与原文逐字一致：AI 缓存 → 常规缓存 → 退回后端总数（还没搜过时保持旧行为）。
    """
    cached = ai_cached_hits
    if cached is None:
        cached = cached_hits
    if cached is None:
        return total_hits
    return len(cached)


__all__ = [
    "AI_HITS_PER_KEYWORD",
    "BROWSE_BATCH_LIMIT",
    "DEFAULT_PAGE_SIZE",
    "HIGH_RISK_PERMISSIONS",
    "LOADER_TAGS",
    "MEDIUM_RISK_PERMISSIONS",
    "MODPACK_VERSION_PAGE_SIZE",
    "MOD_LOADER_COMPAT_MAP",
    "SERVER_SEARCH_PAGE_SIZE",
    "SearchOutcome",
    "browsable_count",
    "clamp_page",
    "compat_loader",
    "dedupe_hits_by_project_id",
    "format_downloads",
    "loader_tags",
    "loader_tags_text",
    "merge_sources",
    "normalize_search",
    "page_slice",
    "paginate",
    "permission_counts",
    "permission_summary",
    "sort_hits_by_downloads",
    "split_result",
    "tag_bar_display",
    "total_pages",
]
