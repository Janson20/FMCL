"""模组浏览服务 —— 客户端「模组浏览」与服务端「服务器模组浏览」两个窗口里那些
**本质是逻辑、只是恰好写在界面类里**的代码（阶段 1 任务 1.8-B）。

来源文件：

- ``ui/windows/mod_browser.py``（1245 行 / 45 个方法）—— 模组 / 资源包 / 光影三个标签页；
- ``ui/windows/server_mod_browser.py``（514 行 / 18 个方法）—— 只列服务端可用的模组。

## 切缝判据（只有一条）

**这段代码是否 import GUI、或是否直接创建/销毁控件。** 是 → 留在界面；
否 → 搬进本模块。界面效果一律通过调用方传进来的 sink
（``status_callback``）或返回值完成；服务自身不 import 任何 GUI、不弹窗、
不 ``after``、不 import ``ui.*``、不 import ``ui.i18n``。

## 五条落地规则（与 ``services/resource_service.py`` / ``services/agent_service.py`` 同一风格）

1. **异常不翻译**：服务只做「网络 / 纯计算」，成功返回结果，失败**原样抛出**。
   界面侧 ``try/except`` 的**边界与原文逐字相同**（原文 ``try`` 里同时包着
   搜索调用、``logger.error``、``after`` 状态栏）。
2. **i18n 文案一律留界面**：本模块里**没有** ``_("...")``。少数必须在服务内部
   才能判定的分支（"服务端 AI 搜索没有返回关键词"、"安装入口先试统一入口还是
   回退旧格式分发"）改成**返回值/结构化结果**，由界面选文案。
3. **线程**：本模块**不起任何线程**。原实现把 ``_do_tab_search`` 等丢进
   ``threading.Thread`` 的那一层留在界面（原样）；服务函数在**调用线程**里同步执行。
   ``status_callback`` 因此也在调用线程里被回调 —— 界面侧的 lambda 照原文
   自己 ``after(0, ...)`` 切回主线程，契约写在每个 sink 参数上。
4. **界面状态不动**：``_tab_states``（含 D-91 的纯值镜像、D-93 的 ``_req_seq``
   世代号、``_cached_hits`` / ``_ai_cached_hits`` 缓存）留在界面，本模块只提供
   操作它们的纯函数（``resolve_install_dir`` / ``match_installed_instance`` …）。
   于是 ``tests/test_mod_browser_staleness.py`` 的 19 条 D-93 守卫一行都不用改。
5. **两份实现不合并**：客户端与服务端窗口的同名功能**约定不同**
   （客户端是双源 + ``limit=300`` 本地分页 + 带依赖安装，服务端是单源 +
   ``limit=PAGE_SIZE`` 后端分页 + 无依赖列表），逐字确认不等价，因此**各自保留**，
   不合并成"看起来更干净"的一份（与 ``resource_service`` 的做法一致）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from logzero import logger

from services.base import Service
from services.browse_common import (
    AI_HITS_PER_KEYWORD,
    BROWSE_BATCH_LIMIT,
    MOD_LOADER_COMPAT_MAP,
    SearchOutcome,
    compat_loader,
    split_result,
)

# ═══════════════════════════════════════════════════════════════════
# 常量：标签页 / 子目录 / 加载器
# ═══════════════════════════════════════════════════════════════════

#: 三个标签页的键（原 ``ModBrowserWindow.TAB_MODS`` 等三个类属性）。
#: 界面类继续保留这三个类属性（值指向本模块常量），因为它们是**状态字典的键**。
TAB_MODS: str = "mods"
TAB_RESOURCE_PACKS: str = "resourcepacks"
TAB_SHADERS: str = "shaders"

#: 客户端模组浏览窗口支持的标签页（顺序与界面构建顺序一致）。
#:
#: 界面侧用它做"这个 tab_key 认不认识"的前置判断 —— 原文 ``_do_tab_search``
#: 对未知标签页是 ``else: return``（**什么都不做**，连筛选值都不去读），
#: 那条分支既然存在就照原样保住（否则未知键会先在 ``_get_selected_version``
#: 上抛 ``KeyError``，被同一个 ``except`` 兜成错误态 —— 可见行为就变了）。
CLIENT_TABS: Tuple[str, ...] = (TAB_MODS, TAB_RESOURCE_PACKS, TAB_SHADERS)

#: 标签页 → ``.minecraft`` 下的安装子目录（原 ``_resolve_install_dir`` 里的 ``subdirs``）。
INSTALL_SUBDIRS: Dict[str, str] = {
    TAB_MODS: "mods",
    TAB_RESOURCE_PACKS: "resourcepacks",
    TAB_SHADERS: "shaderpacks",
}

#: 服务端「服务端模组」窗口判定"要版本隔离"的加载器关键字（原 ``_get_mods_dir`` 里那行，逐字）。
SERVER_LOADER_MARKERS: Tuple[str, ...] = ("forge", "fabric", "neoforge")


# ═══════════════════════════════════════════════════════════════════
# 在线搜索编排（客户端：双源；服务端：单源）
# ═══════════════════════════════════════════════════════════════════


def search_client_tab(
    tab_key: str,
    query: str,
    game_version: Optional[str],
    mod_loader: Optional[str],
) -> Optional[SearchOutcome]:
    """双源（Modrinth + CurseForge）搜索一个标签页（原 ``ModBrowserWindow._do_tab_search`` 的 ``try`` 块）。

    逐字搬出，只有三处形式变化：

    - ``from curseforge import unified_search_mods`` 等三个懒导入 → 一个 ``import curseforge``
      后按属性调用。**必须**按属性调用：``tests/test_mod_browser_staleness.py`` 用
      ``monkeypatch.setattr(curseforge, "unified_search_mods", ...)`` 换掉这三个入口，
      若改成 ``from ... import`` 会在 import 时就把函数对象绑定死，替身失效。
    - ``state["current_query"]`` / ``self._get_selected_version(tab_key)`` /
      ``self._get_selected_loader(tab_key)`` → 入参（读界面状态留在界面）。
    - 三个 ``result.get(...)`` 归一化 → :func:`services.browse_common.split_result`。

    返回 ``None`` 表示"这个标签页没有对应后端"（原文的 ``else: return``：
    **什么都不做**，既不渲染也不报错）。
    """
    import curseforge

    if tab_key == TAB_MODS:
        result = curseforge.unified_search_mods(
            query=query,
            game_version=game_version,
            mod_loader=mod_loader,
            offset=0,
            limit=BROWSE_BATCH_LIMIT,  # 请求大批量以触发多页拉取
        )
    elif tab_key == TAB_RESOURCE_PACKS:
        result = curseforge.unified_search_resource_packs(
            query=query, game_version=game_version, offset=0, limit=BROWSE_BATCH_LIMIT
        )
    elif tab_key == TAB_SHADERS:
        result = curseforge.unified_search_shaders(
            query=query, game_version=game_version, offset=0, limit=BROWSE_BATCH_LIMIT
        )
    else:
        return None

    return split_result(result)


def search_ai_merged_tab(
    tab_key: str,
    query: str,
    token: str,
    game_version: Optional[str],
    mod_loader: Optional[str],
) -> SearchOutcome:
    """AI 关键词扩展 + 多词合并搜索（原 ``ModBrowserWindow._do_ai_search`` 的调用，逐字）。

    关键词扩展、逐词搜索、按 ``project_id`` 去重、按下载量排序、单词失败只告警
    这五件事**本来就在** ``modrinth.ai_merged_search`` 里，所以这里只是编排入口。
    ``mod_loader`` 由界面按原文的 ``if tab_key == self.TAB_MODS else None`` 决定后传入。
    """
    import modrinth

    result = modrinth.ai_merged_search(
        query=query,
        token=token,
        search_type=tab_key,
        game_version=game_version,
        mod_loader=mod_loader,
        max_per_keyword=AI_HITS_PER_KEYWORD,
    )
    return split_result(result)


def search_server_page(
    query: str,
    game_version: Optional[str],
    mod_loader: Optional[str],
    offset: int,
    limit: int,
) -> SearchOutcome:
    """服务端模组**单页**搜索（原 ``ServerModBrowserWindow._do_search`` 的调用，逐字）。

    与客户端版的差别（**不合并的理由**）：单源（只有 Modrinth）、
    ``offset`` 用界面持有的当前偏移、``limit`` 是一页而不是 300
    —— 即"后端分页"而不是"本地缓存分页"。
    """
    import modrinth

    result = modrinth.search_server_mods(
        query=query,
        game_version=game_version,
        mod_loader=mod_loader,
        offset=offset,
        limit=limit,
    )
    return split_result(result)


def search_server_ai(
    query: str,
    token: str,
    game_version: Optional[str],
    mod_loader: Optional[str],
    per_keyword: int = AI_HITS_PER_KEYWORD,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """服务端模组的 AI 搜索（原 ``ServerModBrowserWindow._do_ai_search`` 的内层编排，逐字）。

    返回 ``(hits, keywords)``：

    - ``keywords`` 为空列表 → 原文走 ``after(0, lambda: self._set_status(_("mod_browser_no_results")))``
      然后 ``return``（**不渲染**）。界面据此判分支。
    - ``hits`` 已按下载量降序、按 ``project_id`` 去重。

    与客户端版的差别（**不合并的理由**）：这里用的是 ``ai_expand_search_keywords`` +
    逐词 ``search_server_mods`` 的**内联**实现（``modrinth.ai_merged_search`` 不含
    "只搜服务端模组"这条路），因此去重/排序/失败聚合必须在本函数里重写一遍。
    """
    import modrinth

    keywords = modrinth.ai_expand_search_keywords(query, token)
    if not keywords:
        return [], []

    seen_ids: set = set()
    merged: List[Dict[str, Any]] = []

    for kw in keywords:
        try:
            result = modrinth.search_server_mods(
                query=kw, game_version=game_version, mod_loader=mod_loader, offset=0, limit=per_keyword
            )
            hits = result.get("hits", [])
            for hit in hits:
                pid = hit.get("project_id", "")
                if pid and pid not in seen_ids:
                    seen_ids.add(pid)
                    merged.append(hit)
        except Exception as e:
            logger.warning(f"AI搜索关键词 '{kw}' 失败: {e}")

    merged.sort(key=lambda h: h.get("downloads", 0), reverse=True)
    return merged, list(keywords)


# ═══════════════════════════════════════════════════════════════════
# 安装目标解析与已装匹配
# ═══════════════════════════════════════════════════════════════════


def resolve_install_dir(
    callbacks: Dict[str, Any],
    tab_key: str,
    default_game_version: Optional[str],
    version_id: str,
    get_selected_version: Callable[[str], Optional[str]],
    get_selected_loader: Callable[[str], Optional[str]],
) -> str:
    """解析安装目标目录（原 ``ModBrowserWindow._resolve_install_dir``，逐字）。

    依据当前筛选的游戏版本/加载器，在已安装版本中查找匹配实例：

    - 命中 → ``.minecraft/versions/<实例文件夹>/mods|resourcepacks|shaderpacks``
    - 未命中或未指定筛选 → ``.minecraft`` 根目录下的全局资源目录

    ``get_selected_version`` / ``get_selected_loader`` 是**界面侧的两个 getter**
    （D-91：它们读的是主线程维护的纯值镜像，不是 Tk 变量）。传 getter 而不是值，
    是为了让"先算 target、再匹配实例"的顺序与原文逐字一致。
    """
    mc_dir = Path(".")
    if "get_minecraft_dir" in callbacks:
        mc_dir = Path(callbacks["get_minecraft_dir"]())

    sub = INSTALL_SUBDIRS.get(tab_key, "mods")

    target_version = get_selected_version(tab_key) or default_game_version
    target_loader = get_selected_loader(tab_key) if tab_key == TAB_MODS else None

    folder = match_installed_instance(callbacks, version_id, target_version, target_loader)
    if folder:
        return str(mc_dir / "versions" / folder / sub)
    return str(mc_dir / sub)


def match_installed_instance(
    callbacks: Dict[str, Any],
    version_id: str,
    game_version: Optional[str],
    mod_loader: Optional[str],
) -> Optional[str]:
    """在已安装版本中查找与目标版本/加载器匹配的实例文件夹名（原 ``_match_installed_instance``，逐字）。

    优先级:

    1. 窗口来源版本自身（filter 与来源一致时直接命中）
    2. 版本+加载器精确匹配的实例
    3. 仅版本匹配的实例（偏好带加载器的实例）

    ``callbacks["get_installed_versions"]`` 缺失时用 ``lambda: []``（原文如此）；
    取版本列表抛异常时**只告警并当作空列表**（原文的 ``except``），
    其余异常一律向上冒。
    """
    if not game_version:
        return None

    try:
        installed = callbacks.get("get_installed_versions", lambda: [])()
    except Exception as e:
        logger.warning(f"获取已安装版本失败: {e}")
        installed = []

    exact: List[str] = []
    version_only: List[str] = []
    for inst in installed:
        folder = getattr(inst, "folder_name", "") or ""
        if not folder or getattr(inst, "vanilla_name", "") != game_version:
            continue
        if mod_loader:
            if getattr(inst, "loader_type", None) == mod_loader:
                exact.append(folder)
        else:
            version_only.append(folder)

    if exact:
        if version_id in exact:
            return version_id
        return exact[0]
    if version_only:
        # 偏好带加载器的实例（资源按实例目录存放）
        for inst in installed:
            if getattr(inst, "folder_name", "") in version_only and getattr(inst, "has_loader", False):
                return inst.folder_name
        return version_only[0]
    return None


def server_mods_dir(callbacks: Dict[str, Any], version_id: str) -> str:
    """服务端模组目录（原 ``ServerModBrowserWindow._get_mods_dir``，逐字）。

    含**建目录**这个副作用（原文就是 ``mkdir(parents=True, exist_ok=True)``）。
    与 ``services/resource_service.server_mods_dir`` 是**不同窗口的不同约定**：
    这里用 ``version_id.lower()`` 里的加载器关键字决定是否版本隔离，
    且版本目录名直接用 ``version_id``（resource_service 那份是按目录名参数化的）。
    """
    server_dir = Path(".")
    if "get_server_dir" in callbacks:
        server_dir = Path(callbacks["get_server_dir"]())

    v = version_id.lower()
    if any(loader in v for loader in SERVER_LOADER_MARKERS):
        mods_dir = server_dir / version_id / "mods"
    else:
        mods_dir = server_dir / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    return str(mods_dir)


# ═══════════════════════════════════════════════════════════════════
# 下载 / 安装编排
# ═══════════════════════════════════════════════════════════════════
#
# 这里只搬**"调用哪个下载器、入参怎么摆、返回什么"**；`_set_tab_status`、
# `after(0, ...)`、`_trigger_ach`、`logger.info/error` 这些界面/日志动作留在界面。
# 原文的 `try/except Exception as e:` 同时包着"取筛选值 → 调用安装 → 写状态栏"，
# 服务让异常**原样向上冒**，于是那个 `except` 的边界与覆盖范围一字不改。


def install_mod(
    project_id: str,
    title: str,
    source: str,
    game_version: str,
    mod_loader: str,
    mods_dir: str,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, Any, List[str]]:
    """下载并安装一个客户端模组（原 ``ModBrowserWindow._install_mod`` 的调用，逐字）。

    返回 ``(success, result, installed_names)``。

    两条分支（**不合并的理由**）：

    - ``source == "curseforge"`` → ``curseforge.install_mod(int(project_id), ...)``，
      它**不返回**已装文件名列表，原文用 ``[title] if success else []`` 顶替；
    - 其余（含 ``"modrinth"``）→ ``modrinth.install_mod_with_deps(...)``，带依赖递归。

    ``status_callback`` 在**调用线程**里被下载器回调（契约同原文：
    界面侧自己 ``after(0, ...)``）。
    """
    if source == "curseforge":
        from curseforge import install_mod as cf_install

        success, result = cf_install(
            int(project_id), game_version=game_version, mod_loader=mod_loader, mods_dir=mods_dir
        )
        installed_names = [title] if success else []
    else:
        from modrinth import install_mod_with_deps

        success, result, installed_names = install_mod_with_deps(
            project_id,
            game_version=game_version,
            mod_loader=mod_loader,
            mods_dir=mods_dir,
            status_callback=status_callback,
        )

    return success, result, installed_names


def install_resource_pack(
    project_id: str,
    game_version: str,
    resourcepacks_dir: str,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, Any]:
    """下载并安装一个资源包（原 ``ModBrowserWindow._install_resource_pack`` 的调用，逐字）。"""
    from modrinth import install_resource_pack as mr_install

    return mr_install(
        project_id,
        game_version=game_version,
        resourcepacks_dir=resourcepacks_dir,
        status_callback=status_callback,
    )


def install_shader(
    project_id: str,
    game_version: str,
    shaderpacks_dir: str,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, Any]:
    """下载并安装一个光影包（原 ``ModBrowserWindow._install_shader`` 的调用，逐字）。"""
    from modrinth import install_shader as mr_install

    return mr_install(
        project_id,
        game_version=game_version,
        shaderpacks_dir=shaderpacks_dir,
        status_callback=status_callback,
    )


def server_install_mod(
    project_id: str,
    game_version: str,
    mod_loader: str,
    mods_dir: str,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, Any, List[str]]:
    """下载并安装一个服务端模组（原 ``ServerModBrowserWindow._install_mod`` 的调用，逐字）。

    与客户端版的差别（**不合并的理由**）：只有这一条分支（``install_mod_with_deps``，
    没有 ``source`` 参数、没有 curseforge 分支），筛选值取窗口来源版本而不是标签页筛选。
    """
    from modrinth import install_mod_with_deps

    return install_mod_with_deps(
        project_id,
        game_version=game_version,
        mod_loader=mod_loader,
        mods_dir=mods_dir,
        status_callback=status_callback,
    )


# ═══════════════════════════════════════════════════════════════════
# 详情页：项目信息 / 版本列表 / 依赖预览 / 按版本安装（阶段 3.7）
# ═══════════════════════════════════════════════════════════════════
#
# 这三样旧窗口**没有**（卡片直接装最新兼容版本）。用户 2026-10-17 裁决
# 「三页 + 详情页本轮一并做」，于是详情页需要它们。
#
# 两个源的字段形状不同（Modrinth：title/description/icon_url/version_number；
# CurseForge：name/summary/logo/displayName），全部在这一层归一成**同一套键**，
# 界面侧不判来源。

#: 依赖关系里"会真的被装进来"的两种。``embedded`` 已内嵌、``incompatible`` 跳过、
#: ``tool`` 不是内容依赖 —— 详情页只把前两种摆出来（与安装时的实际行为对齐）。
INSTALLABLE_RELATIONS: Tuple[str, ...] = ("required", "optional")

#: 依赖预览里最多**联网解析**多少个名字。超出的只显示 ID ——
#: 详情页一次几十个依赖时不该发几十个请求。
DEPENDENCY_NAME_LIMIT: int = 8


def normalize_project(source: str, raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把两个源的项目详情归一成同一套键（``{}`` 表示拿不到）。"""
    if not raw:
        return {}
    if source == "curseforge":
        logo = raw.get("logo", {}) or {}
        authors = raw.get("authors", []) or []
        return {
            "source": "curseforge",
            "project_id": str(raw.get("id", "")),
            "title": raw.get("name", "") or "",
            "description": raw.get("summary", "") or "",
            "author": authors[0].get("name", "") if authors else "",
            "downloads": int(raw.get("downloadCount", 0) or 0),
            "icon_url": (logo.get("thumbnailUrl") or logo.get("url") or ""),
            "slug": raw.get("slug", "") or "",
            "game_versions": [],
            "loaders": [],
        }
    return {
        "source": source or "modrinth",
        "project_id": str(raw.get("id", "") or raw.get("project_id", "")),
        "title": raw.get("title", "") or "",
        "description": raw.get("description", "") or "",
        "author": raw.get("author", "") or "",
        "downloads": int(raw.get("downloads", 0) or 0),
        "icon_url": raw.get("icon_url", "") or "",
        "slug": raw.get("slug", "") or "",
        "game_versions": list(raw.get("game_versions", []) or []),
        "loaders": list(raw.get("loaders", []) or []),
    }


def project_detail(source: str, project_id: str) -> Dict[str, Any]:
    """项目详情（归一化）。拿不到返回 ``{}``（界面走错误态）。"""
    if source == "curseforge":
        import curseforge

        try:
            return normalize_project("curseforge", curseforge.get_project_info(int(project_id)))
        except (TypeError, ValueError) as e:
            logger.warning(f"CurseForge 项目 ID 非法 ({project_id}): {e}")
            return {}
    import modrinth

    return normalize_project("modrinth", modrinth.get_project_info(project_id))


def normalize_versions(source: str, raw_versions: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """把两个源的版本/文件列表归一成同一套键（详情页"版本选择"的数据源）。

    归一后的键：

    - ``id``：Modrinth 的 version id / CurseForge 的 file id（字符串）
    - ``name``：给人看的名字（``version_number`` / CurseForge 的 ``display_name``）
    - ``game_versions`` / ``loaders`` / ``filename`` / ``download_url``
    - ``release_type``（release / beta / alpha）
    - ``size``：字节数（拿不到是 0）
    - ``dependencies``：``[{"project_id": str, "kind": str}]``
    """
    out: List[Dict[str, Any]] = []
    for raw in raw_versions or []:
        if not isinstance(raw, dict):
            continue
        deps = []
        for dep in raw.get("dependencies", []) or []:
            if not isinstance(dep, dict):
                continue
            kind = str(dep.get("kind") or dep.get("dependency_type") or "required")
            pid = str(dep.get("project_id", "") or "")
            if pid:
                deps.append({"project_id": pid, "kind": kind})
        files = raw.get("files", []) or []
        primary = next((f for f in files if isinstance(f, dict) and f.get("primary")), None)
        if primary is None and files:
            primary = files[0] if isinstance(files[0], dict) else None
        primary = primary or {}
        out.append(
            {
                "id": str(raw.get("id", "") or ""),
                "name": str(raw.get("version_number") or raw.get("display_name") or raw.get("file_name") or ""),
                "game_versions": list(raw.get("game_versions", []) or []),
                "loaders": list(raw.get("loaders", []) or []),
                "filename": str(primary.get("filename") or raw.get("file_name") or ""),
                "download_url": str(primary.get("url") or raw.get("download_url") or ""),
                "release_type": str(raw.get("release_type", "") or raw.get("version_type", "") or "release"),
                "size": int(primary.get("size", 0) or raw.get("size", 0) or 0),
                "dependencies": deps,
            }
        )
    return out


def project_versions(
    source: str,
    project_id: str,
    game_version: Optional[str] = None,
    mod_loader: Optional[str] = None,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    """项目的版本/文件列表（归一化）。"""
    if source == "curseforge":
        import curseforge

        try:
            raw = curseforge.get_project_files(
                int(project_id), game_version=game_version, mod_loader=mod_loader, limit=limit
            )
        except (TypeError, ValueError) as e:
            logger.warning(f"CurseForge 项目 ID 非法 ({project_id}): {e}")
            return []
        return normalize_versions("curseforge", raw)
    import modrinth

    return normalize_versions(
        "modrinth", modrinth.get_mod_versions(project_id, game_version=game_version, mod_loader=mod_loader)
    )


def version_dependency_preview(
    source: str,
    project_id: str,
    version_id: str,
    game_version: Optional[str] = None,
    mod_loader: Optional[str] = None,
    name_limit: int = DEPENDENCY_NAME_LIMIT,
) -> List[Dict[str, Any]]:
    """某个版本的依赖预览：``[{project_id, kind, name}]``（只含会装进来的两种）。

    名称解析**限量**（``name_limit``）：先是 Modrinth 的逐个 ``get_project_info``，
    CurseForge 走一次 ``get_projects_batch``。解析不到的留空串，界面显示 ID。
    """
    versions = project_versions(source, project_id, game_version, mod_loader, limit=200)
    chosen = next((v for v in versions if v["id"] == str(version_id)), None)
    if chosen is None:
        #: 选中的版本不在筛选结果里 —— 退到不筛再找一次（与安装路径同一条规矩）
        versions = project_versions(source, project_id, limit=200)
        chosen = next((v for v in versions if v["id"] == str(version_id)), None)
    if chosen is None:
        return []

    deps = [d for d in chosen.get("dependencies", []) if d.get("kind") in INSTALLABLE_RELATIONS]
    names: Dict[str, str] = {}
    if deps:
        ids = [d["project_id"] for d in deps[: max(0, name_limit)]]
        if source == "curseforge":
            import curseforge

            try:
                numeric = [int(i) for i in ids]
            except (TypeError, ValueError):
                numeric = []
            if numeric:
                try:
                    for row in curseforge.get_projects_batch(numeric) or []:
                        names[str(row.get("id", ""))] = row.get("name", "") or ""
                except Exception as e:  # noqa: BLE001 - 名字解析失败不影响预览
                    logger.warning(f"解析依赖名称失败: {e}")
        else:
            import modrinth

            for pid in ids:
                try:
                    info = modrinth.get_project_info(pid)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"解析依赖名称失败 ({pid}): {e}")
                    continue
                if info:
                    names[pid] = info.get("title", "") or ""
    return [
        {"project_id": d["project_id"], "kind": d["kind"], "name": names.get(d["project_id"], "")}
        for d in deps
    ]


def describe_version(version_id: Optional[str]) -> Dict[str, Optional[str]]:
    """来源版本的游戏版本与加载器（旧窗口 ``__init__`` 里那两行 ``parse_*``）。

    空 ``version_id``（"全部版本"入口）→ 两个都 ``None``。
    """
    if not version_id:
        return {"game_version": None, "loader": None}
    from modrinth import parse_game_version_from_version, parse_mod_loader_from_version

    try:
        return {
            "game_version": parse_game_version_from_version(version_id),
            "loader": parse_mod_loader_from_version(version_id),
        }
    except Exception as e:  # noqa: BLE001 - 解析失败退化成"不筛"
        logger.warning(f"解析来源版本失败 ({version_id}): {e}")
        return {"game_version": None, "loader": None}


def install_dir_for(mc_dir: Path, version_id: Optional[str], tab_key: str) -> Path:
    """**从某个版本进来时**的安装目录（阶段 3.7，用户 2026-10-23 反馈）。

    与旧 ``resolve_install_dir`` 的区别：那条路靠"已安装实例列表 + 筛选值"反查，
    拿不到列表（核心没起来）时会退到 ``Path(".")/<sub>`` —— 相对的、也不是当前版本的目录
    （实测：从 ``1.20.4-forge`` 进来，对话框定位在 ``mods``）。这里直接用**已知的来源版本**：

    * 该版本装了模组加载器 → ``.minecraft/versions/<id>/<sub>``（版本隔离，与 3.6 资源页一致）；
    * 没装加载器（原版）→ ``.minecraft/<sub>``（游戏本来就写全局目录）。

    ``mc_dir`` 必须是**绝对路径**（调用方负责解析），否则又会拼出相对的影子目录。
    """
    sub = INSTALL_SUBDIRS.get(tab_key, "mods")
    base = Path(mc_dir)
    if not version_id:
        return base / sub
    try:
        from version_utils import has_mod_loader_from_json

        isolated = bool(has_mod_loader_from_json(str(version_id), str(base)))
    except Exception as e:  # noqa: BLE001 - 判不出来就按隔离处理（用户预期的那个目录）
        logger.warning(f"判定版本隔离失败 ({version_id}): {e}")
        isolated = True
    if isolated:
        return base / "versions" / str(version_id) / sub
    return base / sub


def trigger_achievements(installed_count: int) -> None:
    """模组安装成功后的成就（旧 ``_trigger_ach`` 的两处调用，逐字搬来）。

    - 装了不止一个（含依赖）→ ``modder_dependency_expert`` +1
    - 只要成功 → ``modder_first_mod`` 按**安装个数**累加

    成就引擎不可用时**一律吞掉**（旧实现就是 ``except Exception: pass``）：
    成就系统坏了不该把安装结果变成失败。
    """
    if installed_count <= 0:
        return
    try:
        from achievement_engine import get_achievement_engine

        engine = get_achievement_engine()
        if engine is None:
            return
        if installed_count > 1:
            engine.update_progress("modder_dependency_expert", value=1, trigger_type="increment")
        engine.update_progress("modder_first_mod", value=installed_count, trigger_type="increment")
    except Exception as e:  # noqa: BLE001 - 见 docstring
        logger.debug(f"触发模组成就失败: {e}")


def game_version_options() -> List[str]:
    """游戏版本筛选的候选（旧 ``_load_version_options`` 用的同一个来源）。"""
    try:
        from modrinth import get_all_game_versions

        return list(get_all_game_versions())
    except Exception as e:  # noqa: BLE001 - 拿不到就是空列表（界面只给"全部版本"）
        logger.debug(f"加载游戏版本列表失败: {e}")
        return []


def install_chosen_version(
    source: str,
    tab_key: str,
    project_id: str,
    title: str,
    version_id: Optional[str],
    game_version: str,
    mod_loader: str,
    target_dir: str,
    status_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, Any, List[str]]:
    """按**指定版本**安装（详情页的安装按钮；``version_id`` 为空 = 最新兼容版本）。

    返回 ``(success, result, installed_names)`` —— 与 :func:`install_mod` 同形，
    界面侧两条安装路径可以共用一套结果处理。

    资源包 / 光影走 Modrinth 还是 CurseForge，由 ``source`` 决定。
    **旧实现只能装 Modrinth 的资源包/光影**（CurseForge 的结果点安装会因为
    数字 ID 在 Modrinth 查不到而失败）；这里按来源分流，等于顺手修掉那条
    从来装不上的路径，记入对照表"行为变更/改进"。
    """
    if tab_key == TAB_MODS:
        if source == "curseforge":
            import curseforge

            ok, result = curseforge.install_mod(
                int(project_id),
                game_version=game_version,
                mod_loader=mod_loader,
                mods_dir=target_dir,
                file_id=int(version_id) if version_id else None,
            )
            return ok, result, ([title] if ok else [])
        import modrinth

        return modrinth.install_mod_with_deps(
            project_id,
            game_version=game_version,
            mod_loader=mod_loader,
            mods_dir=target_dir,
            status_callback=status_callback,
            version_id=version_id,
        )

    if source == "curseforge":
        import curseforge

        if not version_id:
            return False, "CurseForge 资源包/光影需要先选一个文件版本", []
        files = curseforge.get_project_files(int(project_id), game_version=game_version, limit=200)
        chosen = next((f for f in files if str(f.get("id", "")) == str(version_id)), None)
        if chosen is None:
            return False, "未找到指定文件", []
        ok, result = curseforge.download_file(
            int(project_id),
            int(chosen["id"]),
            target_dir,
            chosen.get("file_name", ""),
            (chosen.get("hashes") or {}).get(1),
            sha1=True,
            preferred_url=chosen.get("download_url", ""),
        )
        return ok, result, ([title] if ok else [])

    import modrinth

    if tab_key == TAB_RESOURCE_PACKS:
        ok, result = modrinth.install_resource_pack(
            project_id,
            game_version=game_version,
            resourcepacks_dir=target_dir,
            status_callback=status_callback,
            version_id=version_id,
        )
    else:
        ok, result = modrinth.install_shader(
            project_id,
            game_version=game_version,
            shaderpacks_dir=target_dir,
            status_callback=status_callback,
            version_id=version_id,
        )
    return ok, result, ([title] if ok else [])


# ═══════════════════════════════════════════════════════════════════
# 服务对象
# ═══════════════════════════════════════════════════════════════════


class ModBrowserService(Service):
    """模组浏览业务服务：客户端 / 服务端两个模组浏览窗口的共用入口。

    约定（与 ``services/resource_service.py`` / ``services/monitor_service.py`` 一致）：

    - 构造期只调 ``Service.__init__``，**不读盘、不连网、不建线程**；
      可脱离 ``AppContext`` 单独实例化 —— ``attached is False`` 时下面所有方法
      都照常工作（它们不碰 ``self.context``）。
    - 需要与界面交互时由调用方把 sink / 回调传进来，服务只调用它。
    - 服务之间不互相 import（本模块只依赖 ``services.browse_common`` 与
      根模块 ``modrinth`` / ``curseforge``，它们本来就是零 GUI 的）。

    下面每个方法都是**薄委托**（一行转调同名模块级函数），这样纯逻辑可以脱离
    服务对象单测，界面侧也只需要一个稳定的入口名。
    """

    name = "mod_browser"
    label = "模组浏览"

    #: 特殊加载器兼容映射（界面类的同名类属性直接引用它，避免两份数据漂移）。
    MOD_LOADER_COMPAT_MAP: Dict[str, str] = MOD_LOADER_COMPAT_MAP

    # ─── 在线搜索 ───────────────────────────────────────────

    @staticmethod
    def search_client_tab(
        tab_key: str,
        query: str,
        game_version: Optional[str],
        mod_loader: Optional[str],
    ) -> Optional[SearchOutcome]:
        """双源搜索一个标签页（未知标签页返回 ``None``）。"""
        return search_client_tab(tab_key, query, game_version, mod_loader)

    @staticmethod
    def search_ai_merged_tab(
        tab_key: str,
        query: str,
        token: str,
        game_version: Optional[str],
        mod_loader: Optional[str],
    ) -> SearchOutcome:
        """AI 关键词扩展 + 合并搜索（``hits`` / ``keywords``）。"""
        return search_ai_merged_tab(tab_key, query, token, game_version, mod_loader)

    @staticmethod
    def search_server_page(
        query: str,
        game_version: Optional[str],
        mod_loader: Optional[str],
        offset: int,
        limit: int,
    ) -> SearchOutcome:
        """服务端模组单页搜索（后端分页口径）。"""
        return search_server_page(query, game_version, mod_loader, offset, limit)

    @staticmethod
    def search_server_ai(
        query: str,
        token: str,
        game_version: Optional[str],
        mod_loader: Optional[str],
        per_keyword: int = AI_HITS_PER_KEYWORD,
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        """服务端模组 AI 搜索；``keywords`` 为空表示"没有关键词可搜"。"""
        return search_server_ai(query, token, game_version, mod_loader, per_keyword)

    @staticmethod
    def compat_loader(mod_loader: Optional[str]) -> Optional[str]:
        """加载器兼容映射（原 ``_search_loader`` 属性体）。"""
        return compat_loader(mod_loader)

    # ─── 安装目标解析 ───────────────────────────────────────

    @staticmethod
    def resolve_install_dir(
        callbacks: Dict[str, Any],
        tab_key: str,
        default_game_version: Optional[str],
        version_id: str,
        get_selected_version: Callable[[str], Optional[str]],
        get_selected_loader: Callable[[str], Optional[str]],
    ) -> str:
        """安装目标目录（原 ``_resolve_install_dir``）。"""
        return resolve_install_dir(
            callbacks,
            tab_key,
            default_game_version,
            version_id,
            get_selected_version,
            get_selected_loader,
        )

    @staticmethod
    def match_installed_instance(
        callbacks: Dict[str, Any],
        version_id: str,
        game_version: Optional[str],
        mod_loader: Optional[str],
    ) -> Optional[str]:
        """已安装实例匹配（原 ``_match_installed_instance``）。"""
        return match_installed_instance(callbacks, version_id, game_version, mod_loader)

    @staticmethod
    def server_mods_dir(callbacks: Dict[str, Any], version_id: str) -> str:
        """服务端模组目录（原服务端 ``_get_mods_dir``，**会建目录**）。"""
        return server_mods_dir(callbacks, version_id)

    # ─── 下载 / 安装 ────────────────────────────────────────

    @staticmethod
    def install_mod(
        project_id: str,
        title: str,
        source: str,
        game_version: str,
        mod_loader: str,
        mods_dir: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, Any, List[str]]:
        """客户端模组安装（``(success, result, installed_names)``）。"""
        return install_mod(project_id, title, source, game_version, mod_loader, mods_dir, status_callback)

    @staticmethod
    def install_resource_pack(
        project_id: str,
        game_version: str,
        resourcepacks_dir: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, Any]:
        """资源包安装（``(success, result)``）。"""
        return install_resource_pack(project_id, game_version, resourcepacks_dir, status_callback)

    @staticmethod
    def install_shader(
        project_id: str,
        game_version: str,
        shaderpacks_dir: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, Any]:
        """光影安装（``(success, result)``）。"""
        return install_shader(project_id, game_version, shaderpacks_dir, status_callback)

    @staticmethod
    def server_install_mod(
        project_id: str,
        game_version: str,
        mod_loader: str,
        mods_dir: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, Any, List[str]]:
        """服务端模组安装（``(success, result, installed_names)``）。"""
        return server_install_mod(project_id, game_version, mod_loader, mods_dir, status_callback)

    # ─── 详情页（阶段 3.7）───────────────────────────────────

    @staticmethod
    def project_detail(source: str, project_id: str) -> Dict[str, Any]:
        """项目详情（归一化；``{}`` = 拿不到）。"""
        return project_detail(source, project_id)

    @staticmethod
    def project_versions(
        source: str,
        project_id: str,
        game_version: Optional[str] = None,
        mod_loader: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """版本 / 文件列表（归一化）。"""
        return project_versions(source, project_id, game_version, mod_loader, limit)

    @staticmethod
    def version_dependency_preview(
        source: str,
        project_id: str,
        version_id: str,
        game_version: Optional[str] = None,
        mod_loader: Optional[str] = None,
        name_limit: int = DEPENDENCY_NAME_LIMIT,
    ) -> List[Dict[str, Any]]:
        """某个版本的依赖预览。"""
        return version_dependency_preview(
            source, project_id, version_id, game_version, mod_loader, name_limit
        )

    @staticmethod
    def game_version_options() -> List[str]:
        """游戏版本筛选候选。"""
        return game_version_options()

    @staticmethod
    def install_dir_for(mc_dir: Path, version_id: Optional[str], tab_key: str) -> Path:
        """从某个版本进来时的安装目录（版本隔离，见模块级函数说明）。"""
        return install_dir_for(mc_dir, version_id, tab_key)

    @staticmethod
    def describe_version(version_id: Optional[str]) -> Dict[str, Optional[str]]:
        """来源版本的游戏版本与加载器（筛选默认值 / 安装参数都靠它）。

        ⚠ 这个薄委托**漏过一次**（阶段 3.7，2026-10-23）：模块级函数早就写好了，
        但类上没加同名方法，于是桥调 `self._service.describe_version(...)` 抛
        `AttributeError` 被 `except` 吞成"解析失败" —— 症状是**游戏版本筛选永远是
        「全部版本」**（加载器还能从路由参数拿到，所以只有版本空着，更难看出来）。
        桥测试当时用的是假服务（自己带这个方法），所以没抓到。
        """
        return describe_version(version_id)

    @staticmethod
    def trigger_achievements(installed_count: int) -> None:
        """安装成功后的成就（两个 ``modder_*``）。"""
        return trigger_achievements(installed_count)

    @staticmethod
    def install_chosen_version(
        source: str,
        tab_key: str,
        project_id: str,
        title: str,
        version_id: Optional[str],
        game_version: str,
        mod_loader: str,
        target_dir: str,
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, Any, List[str]]:
        """按指定版本安装（``version_id`` 为空 = 最新兼容版本）。"""
        return install_chosen_version(
            source, tab_key, project_id, title, version_id,
            game_version, mod_loader, target_dir, status_callback,
        )


__all__ = [
    "CLIENT_TABS",
    "DEPENDENCY_NAME_LIMIT",
    "INSTALL_SUBDIRS",
    "INSTALLABLE_RELATIONS",
    "MOD_LOADER_COMPAT_MAP",
    "ModBrowserService",
    "SERVER_LOADER_MARKERS",
    "TAB_MODS",
    "TAB_RESOURCE_PACKS",
    "TAB_SHADERS",
    "compat_loader",
    "describe_version",
    "game_version_options",
    "install_chosen_version",
    "install_dir_for",
    "install_mod",
    "install_resource_pack",
    "install_shader",
    "match_installed_instance",
    "normalize_project",
    "normalize_versions",
    "project_detail",
    "project_versions",
    "resolve_install_dir",
    "search_ai_merged_tab",
    "search_client_tab",
    "search_server_ai",
    "search_server_page",
    "server_install_mod",
    "server_mods_dir",
    "version_dependency_preview",
]
