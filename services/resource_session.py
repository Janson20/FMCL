"""「我的资源」页面的会话状态机（阶段 3 任务 3.6）。

## 为什么要有这个文件

旧窗口 ``ui/windows/resource_manager.py`` 里真正的业务不是"画控件"，而是**一串会话状态**：
当前标签页、扫描缓存（按目录 mtime 复用）、搜索词、页码与**页缓存**、模组元数据的
异步加载与进度、更新检查的结果字典、更新检查的互斥标志。这一坨状态与"什么时候重置"
的规则（换标签页要清搜索、重置页码、清页缓存；搜索要重置页码；模组标签页**永不**用
扫描缓存）如果照旧留在界面里，QML 侧就得用 JS 再实现一遍 —— 那等于把业务抄第二份。

所以这里把"会话"整个搬进服务层：**桥只负责把本对象的属性念给 QML 听**。

## 与旧实现的关系（逐条对照）

============  ==========================================  ==============================
旧窗口里的东西  本模块的落点                                  语义
============  ==========================================  ==============================
``_tab_var``   :attr:`ResourceSession.tab`                   同名四类型键
``_scan_cache`` :attr:`ResourceSession._scan_cache`          ``rtype -> (items, mtime)``，逐字
``_search_text`` :attr:`ResourceSession.search`              ``strip().lower()`` 在服务层
``_current_page`` :attr:`ResourceSession.page`               1 基；换页只重算切片
``_page_cache`` **不搬**                                      它是"缓存 CTk 控件对象"的
                                                            加速手段（键 = 类型_页码，
                                                            值是 widget 列表）—— QML 侧
                                                            由 ListView 复用委托，没有
                                                            对应的东西可缓存
``_mod_metadata`` :attr:`ResourceSession._mod_metadata`      模组分支的扫描结果
``_update_info`` :attr:`ResourceSession.updates`             更新检查结果 ``modid -> info``
``_update_checking`` :attr:`ResourceSession.checking`        检查互斥
============  ==========================================  ==============================

## 三条"照抄别改"的旧约定

1. **模组标签页不走扫描缓存**：旧 ``_refresh_current_list`` 一进 ``mods`` 分支就
   ``_scan_cache.pop("mods")`` 并**立刻**去提元数据（异步），其余三类才比对 mtime。
   本模块照抄：``mods`` 每次 ``refresh`` 都重扫 + 重提。
2. **"目录不存在"与"目录空"现在是同一条路**：目录不存在就**建出来**再按空目录扫
   （用户 2026-10-07 裁决，见 :meth:`ResourceSession._ensure_resource_dir`），
   所以两种情况的界面表现一致 —— 都是空态 + 「{label} 文件夹为空」。
   旧实现把它分成 ``rm_folder_not_exist``（带路径）与 ``rm_folder_empty``（带类型名）
   两种文案，而前者只在"用户从没打开过这个文件夹"时出现，属于别扭而非信息。
3. **页码与总数的文案来自"过滤后的条数"**，不是后端 ``total_hits``（D-91/D-103 同型
   缺陷在本窗口**不存在**，已逐处核对）。

## 撤销（取消）

``load_mods`` / ``check_updates`` 这类长活走 ``self.tasks.submit``，
取消由 :class:`app.tasks.TaskContext` 的协作式 ``cancel_event`` 负责 —— 与阶段 3.5
的账号登录同一套。**没有**在服务里自己建线程。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from services.resource_service import (
    DEFAULT_PAGE_SIZE,
    RESOURCE_FOLDERS,
    ResourceService,
    filter_items,
    filter_mods,
    normalize_search,
    page_items_key,
    paginate,
    scan_cache_usable,
    thumbnail_candidates,
)

#: 四个标签页的键与顺序（旧 ``RESOURCE_TYPES`` 的顺序：模组 / 资源包 / 地图 / 光影）。
TAB_KEYS: Tuple[str, ...] = ("mods", "resourcepacks", "saves", "shaderpacks")

#: 标签页 → i18n 键（旧 ``_get_resource_label`` 的映射，逐字）。
TAB_LABEL_KEYS: Dict[str, str] = {
    "mods": "resource_mods",
    "resourcepacks": "resource_packs",
    "saves": "resource_maps",
    "shaderpacks": "resource_shaders",
}

#: 标签页 → 说明文案键（旧 ``_get_resource_desc`` 的映射，逐字）。
TAB_DESC_KEYS: Dict[str, str] = {
    "mods": "rm_mods_desc",
    "resourcepacks": "rm_resourcepacks_desc",
    "saves": "rm_saves_desc",
    "shaderpacks": "rm_shaderpacks_desc",
}

#: 标签页 → 搜索框占位符键（旧 ``_switch_tab`` 里那张表，逐字）。
TAB_SEARCH_KEYS: Dict[str, str] = {
    "mods": "rm_search_placeholder_mods",
    "resourcepacks": "rm_search_placeholder_resourcepacks",
    "saves": "rm_search_placeholder_saves",
    "shaderpacks": "rm_search_placeholder_shaderpacks",
}

#: 列表状态（桥直接把 ``load_state`` 交给 ``FmPage.contentState``）。
STATE_READY = "ready"
STATE_EMPTY = "empty"
STATE_LOADING = "loading"
STATE_ERROR = "error"


def resource_label_key(resource_type: str) -> str:
    """标签页 → 类型名键；未知类型原样返回（旧 ``labels.get(rtype, rtype)``）。"""
    return TAB_LABEL_KEYS.get(resource_type, resource_type)


def resource_desc_key(resource_type: str) -> str:
    """标签页 → 说明键；未知类型返回空串（旧 ``descs.get(rtype, "")``）。"""
    return TAB_DESC_KEYS.get(resource_type, "")


def search_placeholder_key(resource_type: str) -> str:
    """标签页 → 搜索占位键；未知类型退化成模组那条（旧 ``.get(tab, "…mods")``）。"""
    return TAB_SEARCH_KEYS.get(resource_type, "rm_search_placeholder_mods")


def resolve_launcher(service: Any, context: Any = None) -> Any:
    """取核心启动器实例（拿不到就返回 ``None``，让调用方走降级路径）。

    ⚠ **必须能接一个 `context`**：`Service.context` 只在服务被 `AppContext` 注册之后
    才可用，而调用方（`ResourcesBridge.bind`）手里本来就有那个 context。只读
    `service.context` 的话，任何"服务还没注册就装配界面"的路径（含全部单测）
    都会静默拿到 ``None`` —— 症状是版本选择器空着、模组元数据一条都不出来，
    而且**不报错**（阶段 3.6 实测踩过：探针里 `modCount` 永远是 0）。
    """
    source = context
    if source is None and service is not None and getattr(service, "attached", False):
        source = getattr(service, "_context", None)
    if source is None:
        return None
    try:
        return source.try_get("launcher")
    except Exception as e:  # noqa: BLE001 - 上下文不可用 = 没有核心
        _log().warning("取 launcher 失败: %s", e)
        return None


def installed_version_ids(launcher: Any = None, config: Any = None) -> List[str]:
    """已安装版本 ID 列表（版本选择器的选项来源），**失败一律退化成空列表**。

    为什么允许两条来源：``launcher`` 是权威（它就是"安装了什么"的真相），但启动
    链条没跑完时它是 ``None``；这时退回 ``config.get_versions_dir()`` 列举目录名
    （与 ``VersionService._scan_folders`` 的降级同一个思路）。
    """
    if launcher is not None:
        try:
            ids = launcher.get_installed_version_ids()
            return sorted(str(v) for v in (ids or []) if str(v).strip())
        except Exception as e:  # noqa: BLE001
            _log().warning("列举已安装版本失败: %s", e)
    try:
        versions_dir = Path(config.get_versions_dir())
        return sorted(p.name for p in versions_dir.iterdir() if p.is_dir())
    except Exception:  # noqa: BLE001 - 目录不存在/没权限都算"没有版本"
        return []


def resolve_version_id(
    requested: str,
    available: Sequence[str],
    last_launched: str = "",
) -> str:
    """决定"这一屏看哪个版本"（纯函数，便于单测）。

    顺序：**路由参数（若在已安装列表里）→ 最近使用版本（若在列表里）→ 列表第一个**。
    列表为空时返回 ``requested`` 或空串（页面会落到"没有版本"的空态）。

    ⚠ 为什么要检查"在已安装列表里"：路由参数来自版本页，理论上一定存在；但用户可以
    在另一个窗口把版本删掉，然后按返回键回到本页 —— 那时拿一个不存在的 ID 去拼
    ``versions/<id>/mods`` 会得到一个"不存在"的空目录，比直接退回一个真实版本更难懂。
    """
    ids = [str(v) for v in available if str(v).strip()]
    wanted = str(requested or "").strip()
    recent = str(last_launched or "").strip()
    if not ids:
        # 一个版本都没装：**没有可管理的版本**。把请求值留住只会让页面显示一个不存在
        # 的版本号、并让 `hasVersion` 变成假绿 —— 所以一律返回空串，页面走
        # "没有版本"的空态（`mod_browser_need_version_install`）。
        return ""
    if wanted and wanted in ids:
        return wanted
    if recent and recent in ids:
        return recent
    return ids[0]


def _log() -> logging.Logger:
    return logging.getLogger("services.resource_session")


class ResourceSession:
    """一个版本、四个标签页的会话状态（页面级单例，由 :class:`ResourceService` 造）。

    **不持有任何 Qt 对象**（红线 2）；所有回调都是普通 Python 可调用对象，
    因此可以完全脱离界面单测。
    """

    #: 每页条数（旧 ``self._page_size = 10``）。
    page_size: int = DEFAULT_PAGE_SIZE

    def __init__(
        self,
        service: ResourceService,
        *,
        config: Any = None,
        callbacks: Optional[Dict[str, Any]] = None,
        version_id: str = "",
        launcher: Any = None,
        tasks: Any = None,
    ) -> None:
        self._service = service
        self._config = config
        self._callbacks = dict(callbacks or {})
        self._launcher = launcher
        #: 任务调度器。**显式传进来**（而不是 `service.tasks`）：`Service.tasks` 要
        #: `AppContext`，而本模块允许脱离上下文单测（`services/resource_service.py`
        #: 头部的约定）。
        self._tasks = tasks

        self._version_id = ""
        self._tab = TAB_KEYS[0]
        self._search = ""
        self._page = 1
        self._scan_cache: Dict[str, Tuple[List[Dict[str, Any]], float]] = {}
        self._mod_metadata: List[Dict[str, Any]] = []
        self._items: List[Dict[str, Any]] = []
        self._visible: List[Dict[str, Any]] = []
        self._total = 0
        self._pages = 1
        self._state = STATE_EMPTY
        self._error = ""
        self._pending_meta: List[Dict[str, Any]] = []
        self._thumb_generation = 0
        self._updates: Dict[str, Dict[str, Any]] = {}
        self._checking = False
        self.set_version(version_id)

    # ─── 版本 ───────────────────────────────────────────────

    @property
    def version_id(self) -> str:
        return self._version_id

    def available_versions(self) -> List[str]:
        """版本选择器的候选（每次都重查：装完新版本不必重开页面）。"""
        if self._launcher is None:
            self._launcher = resolve_launcher(self._service)
        return installed_version_ids(self._launcher, self._config)

    def set_version(self, version_id: str) -> None:
        """切换版本：换版本等于换一整套目录，**所有缓存作废**。

        两处与旧实现**刻意不同**：

        * 旧窗口一个实例只服务一个版本，缓存里没有版本这一维；这里有，所以换版本
          必须整片作废（mtime 比对跨版本没有意义）。
        * ``version_id`` 允许为空串 = **全局 ``.minecraft`` 目录**（用户 2026-10-07
          裁决加的选择器选项，见 :meth:`resource_dir`）。
        * 换完之后如果**当前页签在这个版本里不合法**（原版版本没有"模组/光影"），
          自动落到第一个合法页签 —— 否则会停在一个空列表上，用户以为"没资源"。
        """
        self._version_id = str(version_id or "").strip()
        self._scan_cache.clear()
        self._mod_metadata = []
        self._items = []
        self._visible = []
        self._total = 0
        self._pages = 1
        self._page = 1
        self._search = ""
        self._state = STATE_EMPTY
        self._error = ""
        allowed = self.allowed_tabs()
        if self._tab not in allowed:
            self._tab = allowed[0] if allowed else TAB_KEYS[0]

    @property
    def has_version(self) -> bool:
        return bool(self._version_id)

    # ─── 标签页 / 搜索 / 分页 ───────────────────────────────

    @property
    def tab(self) -> str:
        return self._tab

    def set_tab(self, tab: str) -> None:
        """换标签页：**清搜索、重置页码、清页缓存、清空列表**（旧 ``_switch_tab``）。

        注意旧实现把 ``_search_entry.delete(0, "end")`` 也做了 —— 搜索框会被清空，
        所以这里连 ``_search`` 一起清，界面上的输入框跟着这个值走。
        """
        name = str(tab or "").strip()
        if name not in TAB_KEYS or name == self._tab:
            return
        self._tab = name
        self._search = ""
        self._page = 1
        self._items = []
        self._visible = []
        self._total = 0
        self._pages = 1
        self._state = STATE_EMPTY
        self._error = ""

    @property
    def search(self) -> str:
        return self._search

    def set_search(self, text: str) -> Optional[dict]:
        """改搜索词：重置页码并按新词重算（旧 ``_on_search``）。

        返回 ``None`` 表示"没变化，不用重算"；否则返回一个和 :meth:`refresh` 同形的
        结果字典（模组分支在**已加载**的元数据上过滤，不会重新解压 jar）。
        """
        normalized = normalize_search(text or "")
        if normalized == self._search:
            return None
        self._search = normalized
        self._page = 1
        return self.repage()

    def repage(self) -> dict:
        """只重算"过滤 + 分页"，不重新扫描（旧 ``_render_mod_list`` / ``_render_filtered_list``）。"""
        source = self._mod_metadata if self._tab == "mods" else self._items
        filtered = filter_mods(source, self._search) if self._tab == "mods" else filter_items(source, self._search)
        self._visible = list(filtered)
        self._total = len(self._visible)
        page_items, self._page, self._pages = paginate(self._visible, self._page, self.page_size)
        result = self._result(page_items)
        if not self._visible:
            # 空来源 vs 搜不到，是两个不同的文案（旧实现也是分开的）
            if source:
                result["status_key"] = "mod_search_no_results" if self._tab == "mods" else "rm_search_no_results"
            else:
                result["status_key"] = "mod_folder_empty" if self._tab == "mods" else "rm_folder_empty"
                result["status_params"] = {"label_key": resource_label_key(self._tab)}
        return result

    @property
    def page(self) -> int:
        return self._page

    @property
    def page_count(self) -> int:
        return self._pages

    @property
    def total(self) -> int:
        return self._total

    @property
    def load_state(self) -> str:
        return self._state

    @property
    def error(self) -> str:
        return self._error

    @property
    def items(self) -> List[Dict[str, Any]]:
        """当前页条目（旧 ``_filtered_items[start:end]``）。"""
        page_items, _, _ = paginate(self._visible, self._page, self.page_size)
        return page_items

    @property
    def items_key(self) -> Tuple[str, ...]:
        """当前页的去重键（给缩略图代际判定用；旧 ``page_items_key``）。"""
        return page_items_key(self.items)

    def goto(self, page: int) -> dict:
        """翻页（旧 ``_on_prev_page`` / ``_on_next_page``）。"""
        self._page = int(page or 1)
        page_items, self._page, self._pages = paginate(self._visible, self._page, self.page_size)
        return self._result(page_items)

    # ─── 目录 ───────────────────────────────────────────────

    @property
    def minecraft_dir(self) -> Path:
        """这一屏的基准目录（``.minecraft``）。**一定是绝对路径**。

        为什么必须绝对（2026-10-07 第四次验收实测）：三条来源里
        ``callbacks["get_minecraft_dir"]``（核心未就绪时的降级路径）给的是**相对**的
        ``.minecraft``，而 ``config.minecraft_dir`` 给的是绝对路径。基准目录若随来源
        变性质，同一屏里"资源目录解析"与"缓存比对"就会指向两个不同的 ``.minecraft``：

        * 版本隔离目录被拼成 ``.minecraft\\versions\\<版本>\\<类型>``（相对**进程工作
          目录**），目录于是被建到仓库根/安装目录下，而不是用户真正那个 ``.minecraft``；
        * 更糟的是那个相对路径**又恰好存在**，于是 `_ensure_resource_dir` 认为
          "已经有了"、什么都不做 —— 用户看到的现象正是**"自动新建文件夹没做"**。

        相对路径的处理顺序（**先判定再采信**，与
        ``resource_service.minecraft_dir`` 的"回调优先"保持一致）：

        1. 回调给的路径归一成绝对后**确实存在** → 用它（旧行为，回调优先）；
        2. 否则退到 ``config.minecraft_dir``（核心没起来时它就是唯一权威的绝对路径）；
        3. 再否则退到服务层的兜底。

        第 2 步是关键：相对路径是**相对进程工作目录**解释的，它存不存在本身就是判据
        —— 存在说明"回调是对的、只是写法相对"，不存在说明"这是核心未就绪时的降级值、
        该换 config 那条路"，而不是去工作目录下凭空造一个 ``.minecraft``。
        """
        getter = self._callbacks.get("get_minecraft_dir")
        if callable(getter):
            try:
                candidate = Path(os.path.abspath(str(getter())))
            except Exception as e:  # noqa: BLE001 - 回调坏了就退到配置那条路
                _log().warning("取 .minecraft 目录失败（回调）: %s", e)
                candidate = None
            if candidate is not None:
                if candidate.is_dir():
                    return candidate
                _log().warning(
                    "回调给的 .minecraft 不存在（%s），改用配置里的目录", candidate
                )
        if self._config is not None:
            try:
                return Path(os.path.abspath(str(self._config.minecraft_dir)))
            except Exception as e:  # noqa: BLE001 - 配置坏了退化成仓库下的 .minecraft
                _log().warning("取 .minecraft 目录失败: %s", e)
        return Path(os.path.abspath(str(self._service.minecraft_dir(self._callbacks))))

    def resource_dir(self, resource_type: str = "") -> Path:
        """当前标签页（或指定类型）的资源目录。非法类型抛 ``KeyError``（照旧）。

        **``version_id`` 为空串时就是这个文件夹的全局目录**（``.minecraft/<folder>``）——
        也就是旧窗口从"原版版本"进来时看到的那一个。阶段 3.6 用户裁决（2026-10-07）：
        没选版本（选择器停在「全局 .minecraft」）时不该报"未指定游戏版本"，
        而是照旧读全局目录。
        """
        base = self.minecraft_dir
        if not self._version_id:
            return base / RESOURCE_FOLDERS[resource_type or self._tab]
        #: ⚠ **基准目录只解析一次**（就是上面那个 `base`），加载器判定也用同一个 base。
        #: 不能再调 `resource_dir_for(callbacks, …)` —— 那条路会自己再解析一次
        #: `.minecraft`，与 `base` 可能算出不同结果（核心未就绪时回调给的是相对路径，
        #: 而 `self.minecraft_dir` 走 `config.minecraft_dir` 是绝对的），于是资源目录被
        #: 解析到进程工作目录下的 `.minecraft`：目录建错地方、而"建过没有"的判断又
        #: 恰好为真，用户看到的就是"自动新建文件夹没做"（2026-10-07 验收实测）。
        return self._service.resource_dir_in(
            base,
            self._version_id,
            resource_type or self._tab,
            has_loader=self._service.has_mod_loader(self._version_id, base),
        )

    def has_mod_loader(self) -> bool:
        """当前版本有没有装模组加载器（决定"模组/光影"页签给不给）。

        全局目录（``version_id`` 为空）按"能给全"处理：那里本来就可能有旧版本留下的
        ``mods``。
        """
        if not self._version_id:
            return True
        return bool(self._service.has_mod_loader(self._version_id, self.minecraft_dir))

    def allowed_tabs(self) -> List[str]:
        """这一屏允许显示哪几个页签（原版只给资源包与地图 —— 用户 2026-10-07 裁决）。"""
        return list(self._service.allowed_types(self._version_id, self.minecraft_dir))

    # ─── 扫描 ───────────────────────────────────────────────

    def refresh(self, *, force: bool = False) -> dict:
        """重扫当前标签页（旧 ``_refresh_current_list``）。

        ``mods`` 分支**只完成"目录扫描"这一步**：元数据要走 :meth:`load_mods`
        （异步）。这是与旧实现唯一的结构性差异 —— 旧实现是在同一个方法里
        起线程再回来渲染，这里拆成"扫描（同步、快）"与"提取（异步、慢）"两步，
        因为桥需要先知道"目录里有没有东西"才能决定显示空态还是加载态。
        """
        self._page = 1
        self._error = ""
        if self._tab == "mods":
            self._scan_cache.pop("mods", None)
            return self._scan_mods(force=force)
        return self._scan_plain(force=force)

    def _ensure_resource_dir(self, resource_dir: Path) -> None:
        """资源目录不存在就建出来（用户 2026-10-07 裁决）。

        旧实现里 ``mkdir`` 只出现在两处：``open_resource_folder``（点「打开文件夹」）与
        ``install_resource``（装东西时）。**单纯打开资源管理器不建目录** —— 于是第一次进
        一个还没有 ``resourcepacks`` 的版本，界面显示"文件夹不存在: <路径>"。
        用户 2026-10-07 反馈后核对确认：这两类目录本来就是本页在管，建空目录没有副作用。

        **建完继续按"空目录"走**（不回退成独立的错误状态）：空态文案因此落到
        「{label} 文件夹为空」那句 + 拖拽提示。``rm_folder_not_exist`` 在新界面上不会再
        出现，但键仍保留（服务层的目录解析仍可能给出一个建不出来的路径，例如只读盘）。
        """
        try:
            resource_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            #: 建不出来（只读盘 / 权限）不是致命错误：继续按"空目录"走，扫描会返回空列表
            _log().warning("创建资源目录失败 (%s): %s", resource_dir, e)

    def _scan_plain(self, *, force: bool = False) -> dict:
        """非模组三类：带 mtime 缓存（旧 ``_refresh_current_list`` 的后半段）。"""
        resource_dir = self.resource_dir()
        if not force:
            cached = self._scan_cache.get(self._tab)
            if cached is not None and scan_cache_usable(cached[1], resource_dir):
                self._items = list(cached[0])
                if not self._items:
                    return self._empty("rm_folder_empty", {"label_key": resource_label_key(self._tab)})
                return self.repage()

        if not resource_dir.exists():
            #: 目录不存在就建出来，然后**按空目录继续**（用户 2026-10-07 裁决）。
            #: 旧实现只在"打开文件夹"与"安装文件"时 mkdir，单纯打开资源管理器不建 ——
            #: 于是第一次进一个还没有 resourcepacks 的版本会看到"文件夹不存在: <路径>"。
            self._ensure_resource_dir(resource_dir)

        try:
            items = self._service.scan_resources(resource_dir, self._tab)
        except Exception as e:  # noqa: BLE001 - 扫描失败退化成空态 + 错误文案
            _log().error("扫描资源目录失败 (%s): %s", resource_dir, e)
            self._items = []
            return self._fail(str(e))

        self._items = list(items)
        try:
            self._scan_cache[self._tab] = (list(items), resource_dir.stat().st_mtime)
        except Exception:  # noqa: BLE001 - stat 失败只是这次不缓存（旧实现同样吞掉）
            self._scan_cache.pop(self._tab, None)

        if not self._items:
            return self._empty("rm_folder_empty", {"label_key": resource_label_key(self._tab)})
        return self.repage()

    def _scan_mods(self, *, force: bool = False) -> dict:
        """模组分支的"目录扫描"（不碰元数据）。"""
        resource_dir = self.resource_dir()
        if not resource_dir.exists():
            #: 同 `_scan_plain`：不存在就建，然后按空目录走（`mod_folder_empty`）。
            self._ensure_resource_dir(resource_dir)
        try:
            items = self._service.scan_resources(resource_dir, "mods")
        except Exception as e:  # noqa: BLE001
            _log().error("扫描模组目录失败 (%s): %s", resource_dir, e)
            self._items = []
            return self._fail(str(e))
        self._items = list(items)
        self._mod_metadata = []
        if not self._items:
            return self._empty("mod_folder_empty", {})
        # 目录里有东西：先给"正在读元数据"的加载态（旧实现的 `mod_loading_metadata`）
        return self._result([], STATE_LOADING)

    @property
    def mods_dir(self) -> Path:
        """模组目录（元数据提取的输入；与 :meth:`resource_dir` 同一处解析）。"""
        return self.resource_dir("mods")

    @property
    def tasks(self) -> Any:
        """任务调度器（显式传入的优先；否则退回 `service.tasks`，那条路要 `AppContext`）。"""
        if self._tasks is not None:
            return self._tasks
        return self._service.tasks

    # ─── 模组元数据（异步）──────────────────────────────────

    def load_mods(
        self,
        *,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Any], None]] = None,
    ) -> Any:
        """异步提取模组元数据；返回任务句柄（``wait()`` 可等）。

        ``mods_dir`` 在**主线程**先算好再进任务 —— 旧实现的 ``_load_metadata`` 里
        自己算目录，路径解析依赖回调，放线程里没有任何好处却多一处竞态。

        ``on_done`` / ``on_error`` 直接转给 ``TaskRunner.submit``（回调在**主线程**
        被调，见 ``app/tasks.py`` 的回调契约）—— 桥用它们把结果投递回 QML 侧。
        """
        mods_dir = self.mods_dir
        return self.tasks.submit(
            self._load_mods_sync,
            mods_dir,
            name="resource.mods_metadata",
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error,
            #: ⚠ `pass_context=True` **必须给**：任务函数要拿 `ctx`（报进度 + 取
            #: 协作式取消事件）。不给的话 `TaskRunner` 不会把 context 传进来 ——
            #: 症状是任务根本没被提交（`fn(*args)` 少参数直接抛 TypeError，
            #: 而那个异常走的是 on_error，页面上表现为"模组列表永远空白、
            #: 也没有任何提示"）。阶段 3.6 实测踩过。
            pass_context=True,
        )

    def _load_mods_sync(self, ctx: Any, mods_dir: Path) -> List[Dict[str, Any]]:
        results = self._service.extract_mods_metadata(
            mods_dir,
            status_callback=lambda done, total: ctx.progress(done, total, ""),
            cancel_event=ctx.cancel_event,
        )
        return list(results or [])

    def apply_mods_metadata(self, results: Sequence[Dict[str, Any]]) -> dict:
        """元数据到手：记下、重算、触发那两条成就（旧 ``_on_mod_metadata_loaded``）。"""
        self._mod_metadata = list(results or [])
        if self._mod_metadata:
            self._service.trigger_achievements(len(self._mod_metadata), self.minecraft_dir)
        return self.repage()

    def apply_mods_scan_fallback(self) -> dict:
        """**取消元数据提取之后**的降级渲染：用目录扫描的结果铺出模组列表。

        为什么需要它：模组页签的可见行来源是异步提取的 ``_mod_metadata``，而
        :meth:`_scan_mods` 对非空目录只返回"加载态 + 空列表"。取消之后那份元数据
        永远不会到来，如果什么都不做，这一屏只能停在"正在读取模组信息…"；
        重提一次又把"取消"变成了"重跑"。所以这里把扫描阶段已经拿到的目录条目
        落成可见行 —— 用户至少能看到文件名，并能对它们做删除 / 启停 / 翻页。

        行形状与 ``modrinth.extract_all_mods_metadata`` 里"读不出来的 jar"那条
        回退**逐字一致**（只有文件名，没有 modid / 版本 / 图标），所以界面不需要
        第二套渲染分支，后续的搜索 / 分页 / 导出也都按同一份数据处理。

        **刻意不触发成就**：这一屏没有真元数据，触发 ``modder_first_mod`` 会记少
        （与 :meth:`apply_mods_metadata` 的差别只有这一条）。
        """
        if self._tab != "mods":
            return self.repage()
        self._mod_metadata = [
            {
                "name": str(item.get("name", "") or ""),
                "modid": "",
                "version": "",
                "author": "",
                "description": "",
                "icon_base64": None,
                "path": str(item.get("path", "") or ""),
                "size": item.get("size", ""),
                "disabled": bool(item.get("disabled")),
                "filename": str(item.get("name", "") or ""),
            }
            for item in self._items
        ]
        return self.repage()

    # ─── 缩略图 ─────────────────────────────────────────────

    def thumbnails_wanted(self) -> List[Dict[str, Any]]:
        """本页需要异步取缩略图的条目（旧 ``_refresh_current_list`` 里那段收集）。"""
        return thumbnail_candidates(self._items, self._tab)

    def bump_thumbnail_generation(self) -> int:
        """"重新加载"一次就换代（D-92）：晚到的回调不再抬高新一轮的计数。"""
        self._thumb_generation += 1
        return self._thumb_generation

    @property
    def thumbnail_generation(self) -> int:
        return self._thumb_generation

    # ─── 更新检查 / 批量更新 ────────────────────────────────

    @property
    def updates(self) -> Dict[str, Dict[str, Any]]:
        return dict(self._updates)

    @property
    def checking(self) -> bool:
        return self._checking

    def prepare_update_check(self) -> Tuple[Optional[Any], str]:
        """检查更新的前置校验（旧 ``_check_mod_updates`` 开头那三段）。

        返回 ``(句柄入参, 失败键)``：``失败键`` 非空表示**不该发起请求**，
        直接把它当状态栏文案；否则 ``句柄入参`` 是 ``(mods, game_version, mod_loader)``。
        """
        game_version, mod_loader = self._service.update_targets(self._version_id)
        if not game_version:
            return None, "mod_update_unknown_version"
        mods = self._service.updatable_mods(self._mod_metadata)
        if not mods:
            return None, "mod_update_no_modid"
        return (mods, game_version, mod_loader), ""

    def begin_update_check(self) -> None:
        self._updates = {}
        self._checking = True

    def check_updates(
        self,
        mods: Sequence[Dict[str, Any]],
        game_version: str,
        mod_loader: Optional[str],
        *,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_done: Optional[Callable[[Any], None]] = None,
        on_error: Optional[Callable[[Any], None]] = None,
    ) -> Any:
        """异步双源检查；结果用 :meth:`update_check_result` 取（旧 ``_update_info``）。"""
        return self.tasks.submit(
            lambda ctx: self._service.check_updates(
                mods,
                game_version,
                mod_loader,
                on_progress=lambda done, total: ctx.progress(done, total, ""),
                on_update=self._collect_update,
            ),
            name="resource.check_updates",
            on_progress=on_progress,
            on_done=on_done,
            on_error=on_error,
            pass_context=True,
        )

    def _collect_update(self, modid: str, info: Dict[str, Any]) -> None:
        self._updates[str(modid)] = dict(info)

    def update_check_result(self, found: int, total: int) -> Tuple[str, Dict[str, Any]]:
        """检查完毕：给状态栏文案（旧 ``_on_update_check_done`` 的两个分支）。"""
        self._checking = False
        if found > 0:
            return "mod_updates_available", {"count": found}
        return "mod_up_to_date", {"total": total}

    def update_check_failed(self, error: str) -> Tuple[str, Dict[str, Any]]:
        self._checking = False
        return "mod_update_check_failed", {"error": error}

    def prepare_batch_update(self, modids: Sequence[str]) -> Tuple[Optional[Any], str]:
        """批量更新的前置校验（旧 ``_batch_update_mods`` 开头）。

        ⚠ 旧实现的判断是 ``not game_version or not mod_loader``（少了 ``mod_loader``
        就直接失败），检查更新那条只判了 ``game_version``。**这个不一致照抄**。
        """
        game_version, mod_loader = self._service.update_targets(self._version_id)
        if not game_version or not mod_loader:
            return None, "mod_browser_unknown_loader"
        wanted = [str(m) for m in modids]
        if not wanted:
            return None, ""
        return (wanted, game_version, mod_loader), ""

    def batch_update(
        self,
        modids: Sequence[str],
        game_version: str,
        mod_loader: Optional[str],
        *,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_done: Optional[Callable[[Any], None]] = None,
    ) -> Any:
        return self.tasks.submit(
            lambda ctx: self._service.batch_update_mods(
                modids,
                self._updates,
                game_version,
                mod_loader,
                on_progress=lambda done, total: ctx.progress(done, total, ""),
            ),
            name="resource.batch_update",
            on_progress=on_progress,
            on_done=on_done,
            pass_context=True,
        )

    def finish_batch(self) -> None:
        """批量更新收尾（旧 ``_on_batch_done`` 清 ``_update_info``）。"""
        self._updates = {}

    # ─── 文件操作（薄委托，异常留给界面成文案）──────────────

    def toggle_mod(self, path: str, is_disabled: bool) -> str:
        return self._service.toggle_mod(path, is_disabled)

    def delete(self, path: str) -> None:
        self._service.delete_path(path)

    def install(self, src_path: str, resource_type: str = "") -> Any:
        target = str(resource_type or self._tab)
        return self._service.install_resource(src_path, target, lambda: self.resource_dir(target))

    def open_folder(self) -> None:
        self._service.open_resource_folder(self.resource_dir())

    def export_text(self, header: str) -> str:
        """导出模组列表文本（标题行由界面用 i18n 拼好传进来，服务层不碰文案）。"""
        return self._service.client_mod_list_text(self._mod_metadata, header)

    @property
    def mod_count(self) -> int:
        return len(self._mod_metadata)

    # ─── 内部 ───────────────────────────────────────────────

    def _result(self, page_items: Sequence[Dict[str, Any]], state: str = "") -> dict:
        """把"当前页切片"包成一次结果。

        ``state`` 留空时按 ``_visible`` 推断（有内容 = ready，没内容 = empty）——
        这是绝大多数情况。**要显式指定状态的调用方必须用这个参数**：本方法原先
        无条件重算 ``_state``，把 `_scan_mods` 设好的 `loading` 覆盖成 `empty`
        （阶段 3.6 实测踩到：模组页签永远进不了"正在读取模组信息…"，
        连带元数据任务也没被起来，症状是"模组列表永远空白"）。
        """
        self._state = state or (STATE_READY if self._visible else STATE_EMPTY)
        status_key = "rm_list_count" if (self._visible and not state) else ""
        return {
            "items": list(page_items),
            "page": self._page,
            "page_count": self._pages,
            "total": self._total,
            "state": self._state,
            "error": self._error,
            "status_key": status_key,
            "status_params": {
                "count": self._total,
                "page": self._page,
                "total_pages": self._pages,
                "label_key": resource_label_key(self._tab),
            }
            if status_key
            else {},
        }

    def _empty(self, status_key: str, params: Dict[str, Any]) -> dict:
        self._visible = []
        self._total = 0
        self._pages = 1
        self._page = 1
        result = self._result([], STATE_EMPTY)
        result["status_key"] = status_key
        result["status_params"] = dict(params)
        return result

    def _fail(self, error: str) -> dict:
        """扫描失败：**错误态** + 一句错误文案（状态经 `_result` 的 `state` 参数给定）。"""
        self._visible = []
        self._total = 0
        self._pages = 1
        self._error = str(error)
        result = self._result([], STATE_ERROR)
        result["error"] = self._error
        result["status_key"] = "rm_operation_failed"
        result["status_params"] = {"error": str(error)}
        return result


__all__ = [
    "ResourceSession",
    "TAB_KEYS",
    "TAB_LABEL_KEYS",
    "TAB_DESC_KEYS",
    "TAB_SEARCH_KEYS",
    "STATE_READY",
    "STATE_EMPTY",
    "STATE_LOADING",
    "STATE_ERROR",
    "installed_version_ids",
    "resolve_launcher",
    "resolve_version_id",
    "resource_label_key",
    "resource_desc_key",
    "search_placeholder_key",
]
