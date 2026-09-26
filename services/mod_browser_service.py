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


__all__ = [
    "CLIENT_TABS",
    "INSTALL_SUBDIRS",
    "MOD_LOADER_COMPAT_MAP",
    "ModBrowserService",
    "SERVER_LOADER_MARKERS",
    "TAB_MODS",
    "TAB_RESOURCE_PACKS",
    "TAB_SHADERS",
    "compat_loader",
    "install_mod",
    "install_resource_pack",
    "install_shader",
    "match_installed_instance",
    "resolve_install_dir",
    "search_ai_merged_tab",
    "search_client_tab",
    "search_server_ai",
    "search_server_page",
    "server_install_mod",
    "server_mods_dir",
]
