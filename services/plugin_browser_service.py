"""插件市场服务 —— 「插件市场浏览」窗口里那些**本质是逻辑、只是恰好写在界面类里**
的代码（阶段 1 任务 1.8-B）。

来源文件：``ui/windows/plugin_browser.py``（686 行 / 25 个方法）。

## 切缝判据（只有一条）

**这段代码是否 import GUI、或是否直接创建/销毁控件。** 是 → 留在界面；
否 → 搬进本模块。服务自身不 import 任何 GUI、不弹窗、不 ``after``、
不 import ``ui.*``、不 import ``ui.i18n``。

## 关于 ``plugin_manager/`` 与 ``market``

两者都**本来就是零 GUI 的根包/对象**，所以本模块直接把它们当**注入进来的依赖**
使用（``pm`` = ``PluginManager`` 实例，``market`` = ``PluginMarket`` 实例），
既不 import 具体类、也不持有它们 —— 于是服务可以脱离窗口单测
（喂假 ``pm`` / 假 ``market``），窗口侧也不必先构造真插件市场。

## 落地规则

1. **异常不翻译**：``install_plugin`` / ``update_plugin_from_market`` 让
   ``market`` / ``pm`` 抛的异常原样向上冒，界面侧 worker 函数里那个
   ``try/except`` 的边界与原文逐字相同（``logger.error`` + ``_on_install_failed``
   都在界面）。
2. **i18n 文案一律留界面**：本模块里**没有** ``_("...")``。原 ``_install_plugin``
   里"下载中 / 下载进度"两条状态文案是在 **worker 线程里**用 ``after(0, ...)``
   发的，因此搬出来的只有"下载 + 安装 + 授权 + 启用"这条调用链；
   ``progress_callback`` 由界面传入并**在调用线程里被回调**（契约同原文）。
3. **线程**：本模块**不起任何线程**。原实现那三个
   ``threading.Thread(target=..., daemon=True).start()`` 留在界面（原样）。
4. **界面状态不动**：``_plugins`` / ``_filtered`` / ``_current_page`` /
   ``_searching`` / ``_installing_ids`` / ``_updating_ids`` / ``_update_info`` /
   ``_active_tag`` 留在界面；本模块只提供操作它们的纯函数。
5. **现状缺陷照抄不改**：``_check_updates_async`` 的 worker **直接写**
   ``self._update_info`` 再 ``after`` 渲染（与 D-93 同族的竞态），
   原文如此，本轮只搬家 —— 见 :func:`check_market_updates` 的说明。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from logzero import logger

from services.base import Service
from services.browse_common import normalize_search, total_pages

# ═══════════════════════════════════════════════════════════════════
# 索引 / 过滤 / 分页
# ═══════════════════════════════════════════════════════════════════


def fetch_market_index(market: Any, force: bool = False) -> Tuple[List[dict], str]:
    """拉取插件市场索引 → ``(plugins, error)``（原 ``_async_load_index`` 里 ``_load`` 的函数体，逐字）。

    ``market.fetch_index(force=...)`` 自己会走缓存/网络，返回
    ``(plugins, error)``；这里**不做任何兜底** —— 原文是把 ``error`` 直接显示到状态栏。
    """
    return market.fetch_index(force=force)


def installed_versions_map(pm: Any) -> Dict[str, str]:
    """已装插件的 ``{id: version}``（原 ``_check_updates_async`` 里那行，逐字）。"""
    return pm.get_installed_versions_map()


def check_market_updates(pm: Any, market: Any) -> Dict[str, dict]:
    """检查已装插件的可更新项 → ``{plugin_id: {...}}``（原 ``_check_updates_async`` 的 ``_check`` 内核，逐字）。

    记录现状、疑为缺陷：原文这个函数跑在 worker 线程里，返回后**直接写界面实例上的
    ``self._update_info``**，再 ``after(0, ...)`` 去渲染 —— 与 D-93 是同一族竞态
    （晚返回的旧检查会覆盖新结果）。本轮只搬家不改行为，因此这里返回结果，
    由界面照原文把"写字段 + 渲染"留在主线程回调里（原文就是 ``after`` 之后才用）。
    """
    return market.check_updates(installed_versions_map(pm))


def available_tags(market: Any) -> Dict[str, str]:
    """标签筛选栏的数据源 ``{tag_key: tag_name}``（原 ``_update_tag_bar`` 里那行，逐字）。

    空字典时原文 ``pack_forget()`` 整条隐藏 —— 那是控件动作，留界面。
    """
    return market.get_available_tags()


def filter_plugins(market: Any, query: str, active_tag: Optional[str]) -> List[dict]:
    """应用"搜索词 + 标签"筛选（原 ``_apply_filter`` 的取数三行，逐字）。

    ``query`` 由界面从控件**原样**读出来传入，规范化（``(x or "").strip()``）
    在这里做 —— 口径进服务层，界面只负责"读控件"。
    ``tags`` 的构造与原文一致：选中标签 → 单元素列表，否则 ``None``。
    """
    tags = [active_tag] if active_tag else None
    return market.search(query=normalize_search(query), tags=tags)


def update_count(update_info: Dict[str, dict]) -> int:
    """可更新插件数（原 ``_update_status_summary`` 里那行，逐字）。

    ``info["has_update"]`` 是**硬下标**（原文如此）：缺这个键会 ``KeyError``，
    这里照旧不兜底 —— 上游 ``market.check_updates`` 保证每个条目都带这个键。
    """
    return sum(1 for info in update_info.values() if info["has_update"])


def page_after_delta(current_page: int, delta: int, total_items: int, page_size: int) -> Optional[int]:
    """翻页后的目标页；越界返回 ``None``（原 ``_change_page`` 的判定，逐字）。

    原文： ``new_page = self._current_page + delta``，
    ``if 0 <= new_page < (len(self._filtered) + PAGE_SIZE - 1) // PAGE_SIZE`` ——
    右边界与 :func:`services.browse_common.total_pages` 完全同式
    （空列表时 1 页，因此 ``delta=-1`` 在第 1 页返回 ``None``）。
    """
    new_page = current_page + delta
    if 0 <= new_page < total_pages(total_items, page_size):
        return new_page
    return None


def installed_lookup(pm: Any, pid: str) -> Tuple[bool, str]:
    """插件是否已装 + 其状态值（原 ``_create_plugin_card`` 里那 6 行，逐字）。

    返回 ``(installed, state_value)``；``state_value`` 是 ``PluginState.value``
    （``"enabled"`` / ``"disabled"`` …），未装或取不到状态时是空串。
    """
    installed = pid in (pm.get_all_plugin_meta() or {})
    installed_state = ""
    if installed:
        state = pm.get_plugin_state(pid)
        if state:
            installed_state = state.value
    return installed, installed_state


# ═══════════════════════════════════════════════════════════════════
# 安装 / 更新 / 启用编排
# ═══════════════════════════════════════════════════════════════════
#
# 三个函数都返回 ``(ok, msg)``：``ok`` 决定界面显示哪条状态文案，
# ``msg`` 是原文直接塞进状态栏/错误提示的那个字符串（服务不翻译）。


def install_plugin(
    pm: Any,
    market: Any,
    pid: str,
    permissions: List[str],
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
) -> Tuple[bool, str]:
    """下载 + 安装 + 授权 + 加载 + 启用（原 ``_install_plugin`` 里 ``_download_and_install`` 的调用链，逐字）。

    逐字要点：

    - 下载失败（``error`` 非空）**直接返回** ``(False, error)``，不再走安装；
    - 安装成功后才授权（``permissions`` 为空时**不调用**
      ``grant_manifest_permissions``）、加载、启用；
    - 授权/加载/启用的返回值**一律不看**（原文如此：只有
      ``install_from_file`` 的 ``ok`` 决定成败）。

    记录现状、疑为缺陷：``load_plugin`` / ``enable_plugin`` 失败时界面仍报"安装成功"
    （它们的 ``(ok, msg)`` 被丢弃），本轮只搬家不改行为。

    ``progress_callback(stage, cur, tot)`` 由 ``market.download_plugin`` 在
    **调用线程**里回调；界面侧照原文自己 ``after(0, ...)`` 切回主线程。
    """
    fmpl_path, error = market.download_plugin(pid, progress_callback=progress_callback)

    if error:
        return False, error

    ok, msg = pm.install_from_file(fmpl_path, pid)

    if ok:
        # 授权权限
        if permissions:
            pm.grant_manifest_permissions(pid)
        # 启用
        pm.load_plugin(pid)
        pm.enable_plugin(pid)

    return ok, msg


def update_plugin_from_market(
    pm: Any,
    pid: str,
    progress_callback: Optional[Callable[[str, int, int], None]] = None,
) -> Tuple[bool, str]:
    """从市场更新已装插件（原 ``_update_plugin`` 里 ``_do_update`` 的那次调用，逐字）。

    与安装路径的差别（**不合并的理由**）：只调 ``pm.update_plugin_from_market``
    一次，进度回调由 ``pm`` 自己往市场透传。
    """
    return pm.update_plugin_from_market(pid, progress_callback=progress_callback)


def enable_target(pm: Any, pid: str) -> Tuple[Dict[str, Any], List[str]]:
    """启用前置查询 → ``(manifest, permissions)``（原 ``_enable_plugin`` 开头 6 行，逐字）。

    权限对话框本身（``PluginPermissionDialog``）留在界面 —— 它是控件；
    这里只把"要拿哪些数据"搬出来。
    """
    meta = pm.get_all_plugin_meta().get(pid, {})
    manifest_data = meta.get("manifest", {})
    permissions: List[str] = []
    if manifest_data:
        permissions = manifest_data.get("permissions", [])
    return manifest_data, permissions


def has_ungranted_permissions(pm: Any, pid: str) -> bool:
    """是否存在未授权权限（原 ``_enable_plugin`` 里那两行条件，逐字）。"""
    perm_state = pm.get_permission_state(pid)
    return bool(perm_state and perm_state.get_ungranted_permissions())


def enable_installed_plugin(pm: Any, pid: str) -> Tuple[bool, str]:
    """加载 + 启用已装插件（原 ``_enable_plugin`` 的 ``try/except`` 内核，逐字）。

    ``load_plugin`` 或 ``enable_plugin`` 抛异常时**不向上冒**：原文在这里
    ``except`` 住并转成 ``ok, msg = False, str(e)``（与安装路径不同），
    因此本函数也照原文吞掉并返回，日志文案逐字保留。
    """
    try:
        pm.load_plugin(pid)
        ok, msg = pm.enable_plugin(pid)
    except Exception as e:
        logger.error(f"启用插件失败 ({pid}): {e}")
        ok, msg = False, str(e)
    return ok, msg


# ═══════════════════════════════════════════════════════════════════
# 服务对象
# ═══════════════════════════════════════════════════════════════════


class PluginBrowserService(Service):
    """插件市场服务：「插件市场浏览」窗口的入口。

    约定（与 ``services/resource_service.py`` 一致）：

    - 构造期只调 ``Service.__init__``，**不读盘、不连网、不建线程**；
      可脱离 ``AppContext`` 单独实例化。
    - ``pm`` / ``market`` 由调用方每次传进来（服务不持有），
      因此可以不构造真实插件系统就单测。
    - 服务不 import 任何 GUI，也不 import ``plugin_manager.*``。

    下面每个方法都是**薄委托**。
    """

    name = "plugin_browser"
    label = "插件市场"

    # ─── 索引 / 过滤 / 分页 ─────────────────────────────────

    @staticmethod
    def fetch_market_index(market: Any, force: bool = False) -> Tuple[List[dict], str]:
        """拉取市场索引（原 ``_async_load_index`` 的 ``_load``）。"""
        return fetch_market_index(market, force)

    @staticmethod
    def installed_versions_map(pm: Any) -> Dict[str, str]:
        """已装插件版本表（原 ``_check_updates_async`` 里那行）。"""
        return installed_versions_map(pm)

    @staticmethod
    def check_market_updates(pm: Any, market: Any) -> Dict[str, dict]:
        """检查更新（原 ``_check_updates_async`` 的 ``_check``）。"""
        return check_market_updates(pm, market)

    @staticmethod
    def available_tags(market: Any) -> Dict[str, str]:
        """标签筛选栏数据源（原 ``_update_tag_bar`` 里那行）。"""
        return available_tags(market)

    @staticmethod
    def filter_plugins(market: Any, query: str, active_tag: Optional[str]) -> List[dict]:
        """搜索 + 标签过滤（原 ``_apply_filter`` 的取数三行）。"""
        return filter_plugins(market, query, active_tag)

    @staticmethod
    def update_count(update_info: Dict[str, dict]) -> int:
        """可更新插件数（原 ``_update_status_summary`` 里那行）。"""
        return update_count(update_info)

    @staticmethod
    def page_after_delta(current_page: int, delta: int, total_items: int, page_size: int) -> Optional[int]:
        """翻页目标页；越界返回 ``None``（原 ``_change_page`` 的判定）。"""
        return page_after_delta(current_page, delta, total_items, page_size)

    @staticmethod
    def installed_lookup(pm: Any, pid: str) -> Tuple[bool, str]:
        """已装状态查询（原 ``_create_plugin_card`` 里那 6 行）。"""
        return installed_lookup(pm, pid)

    # ─── 安装 / 更新 / 启用 ─────────────────────────────────

    @staticmethod
    def install_plugin(
        pm: Any,
        market: Any,
        pid: str,
        permissions: List[str],
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Tuple[bool, str]:
        """下载 + 安装 + 授权 + 启用（原 ``_install_plugin`` 的调用链）。"""
        return install_plugin(pm, market, pid, permissions, progress_callback)

    @staticmethod
    def update_plugin_from_market(
        pm: Any,
        pid: str,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Tuple[bool, str]:
        """从市场更新（原 ``_update_plugin`` 的那次调用）。"""
        return update_plugin_from_market(pm, pid, progress_callback)

    @staticmethod
    def enable_target(pm: Any, pid: str) -> Tuple[Dict[str, Any], List[str]]:
        """启用前置查询 ``(manifest, permissions)``（原 ``_enable_plugin`` 开头 6 行）。"""
        return enable_target(pm, pid)

    @staticmethod
    def has_ungranted_permissions(pm: Any, pid: str) -> bool:
        """是否存在未授权权限（原 ``_enable_plugin`` 里那两行条件）。"""
        return has_ungranted_permissions(pm, pid)

    @staticmethod
    def enable_installed_plugin(pm: Any, pid: str) -> Tuple[bool, str]:
        """加载 + 启用（原 ``_enable_plugin`` 的 ``try/except`` 内核）。"""
        return enable_installed_plugin(pm, pid)


__all__ = [
    "PluginBrowserService",
    "available_tags",
    "check_market_updates",
    "enable_installed_plugin",
    "enable_target",
    "fetch_market_index",
    "filter_plugins",
    "has_ungranted_permissions",
    "install_plugin",
    "installed_lookup",
    "installed_versions_map",
    "page_after_delta",
    "update_count",
    "update_plugin_from_market",
]
