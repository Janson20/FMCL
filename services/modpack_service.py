"""整合包服务 —— 「整合包浏览」「整合包安装」「整合包开服」三个窗口里那些
**本质是逻辑、只是恰好写在界面类里**的代码（阶段 1 任务 1.8-B）。

来源文件：

- ``ui/windows/modpack_browser.py``（676 行 / 24 个方法）—— 在线搜索、版本列表、下载；
- ``ui/windows/modpack_install.py``（624 行 / 23 个方法）—— 六种格式识别 + 客户端安装；
- ``ui/windows/modpack_server.py``（434 行 / 13 个方法）—— 同上的服务端安装入口。

## 切缝判据（只有一条）

**这段代码是否 import GUI、或是否直接创建/销毁控件。** 是 → 留在界面；
否 → 搬进本模块。服务自身不 import 任何 GUI、不弹窗、不 ``after``、
不 import ``ui.*``、不 import ``ui.i18n``。

## 落地规则

1. **异常不翻译**：``load_pack_info`` 把"格式不支持"照样抛 ``ValueError``，
   ``download_modpack_version`` 把下载器抛的异常原样向上冒 —— 界面侧那两个
   ``try/except`` 的边界与原文逐字相同。
2. **i18n 文案一律留界面**：本模块里**没有** ``_("...")``。两处必须在服务内
   才能判定的分支改成参数/返回值：
   - ``build_sorted_version_list`` 的"未知版本"占位文案由调用方传
     ``unknown_version_label``（原文是 ``_("mp_browser_unknown_version")``）；
   - ``call_install_entry`` 的"走统一入口还是回退旧格式分发"由返回值表达。
3. **线程**：本模块**不起任何线程**。原文把 ``_load_mrpack_info`` / ``_do_install``
   丢进 ``threading.Thread`` 的那一层留在界面（原样）。 ``status_callback``
   在**调用线程**里被下载器回调，界面侧照原文自己 ``after(0, ...)``。
4. **界面状态不动**：``_mrpack_path`` / ``_mrpack_info`` / ``_optional_var_map``
   （Tk ``BooleanVar``！）/ ``_launcher_instance`` / ``_polling`` 留在界面；
   ``_optional_var_map`` 的取值（``var.get()``）是 Tk 变量读取，**必须**留界面。
5. **进度轮询留界面**：``_poll_progress`` 里有 ``after(200, ...)`` 与控件
   ``configure``，整段留在界面；只有纯计算 ``progress_percent`` 搬出来，
   供 QML 侧复用同一份百分比口径。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from logzero import logger

from services.base import Service
from services.browse_common import AI_HITS_PER_KEYWORD, SearchOutcome, split_result

# ═══════════════════════════════════════════════════════════════════
# 在线搜索 / 版本列表
# ═══════════════════════════════════════════════════════════════════


def search_modpacks(query: str, offset: int, limit: int) -> SearchOutcome:
    """整合包单页搜索（原 ``ModpackBrowserWindow._do_search`` 的调用，逐字）。

    ``modrinth.search_modpacks`` 是唯一来源（整合包没有双源合并入口），
    所以这里只做"调用 + 结果归一化 + 来源统计的兜底"。
    """
    import modrinth

    result = modrinth.search_modpacks(query=query, offset=offset, limit=limit)
    return split_result(result)


def search_ai_modpacks(query: str, token: str) -> Tuple[List[Dict[str, Any]], List[str]]:
    """AI 关键词扩展 + 合并搜索整合包 → ``(hits, keywords)``（原 ``ModpackBrowserWindow._do_ai_search`` 的调用，逐字）。

    ``search_type="modpacks"`` 与 ``max_per_keyword=30`` 是这里的两个约定；
    关键词为空时 ``ai_merged_search`` 返回 ``{"hits": [], "total_hits": 0, "keywords": []}``，
    界面照原文去渲染空结果（**不是**"没有关键词就直接返回"那条分支 ——
    只有服务端模组窗口才有那个分支）。
    """
    import modrinth

    result = modrinth.ai_merged_search(
        query=query, token=token, search_type="modpacks", max_per_keyword=AI_HITS_PER_KEYWORD
    )
    outcome = split_result(result)
    return outcome.hits, outcome.keywords


def fetch_modpack_versions(project_id: str) -> List[Dict[str, Any]]:
    """取整合包的全部版本（原 ``ModpackBrowserWindow._fetch_versions_and_pick`` 的取数部分，逐字）。

    失败**原样抛出**：原文在 ``except`` 里把 ``获取版本列表失败: {e}`` 显示到状态栏
    （记录现状、疑为缺陷：这条文案**硬编码中文**，没走 i18n —— 本轮只搬家不改文案，
    它留在界面侧原样保留）。
    """
    import modrinth

    return modrinth.get_modpack_versions(project_id)


def build_sorted_version_list(versions: List[Dict[str, Any]], unknown_version_label: str) -> List[Dict[str, Any]]:
    """把版本列表按 MC 版本分组、组内按发布时间倒序，并插入分组头（原 ``_build_sorted_version_list``，逐字）。

    分组头是 ``{"_header": mc_label, "_count": n}`` 这种**伪条目**，
    界面渲染时靠 ``_header`` 是否存在来分辨（原样保留）。

    ``unknown_version_label`` 对应原文的 ``_("mp_browser_unknown_version")`` ——
    服务层不 import i18n，所以由界面把**已经翻译好的文本**传进来。

    排序键 ``_mc_sort_key``：按 ``"."`` 切成整数元组倒序；切不动时退化成 ``(0,)``
    （原文的 ``except (ValueError, IndexError)``，逐字）。
    """

    def _mc_sort_key(mc_label: str) -> tuple:
        parts = mc_label.split(".")
        try:
            return tuple(int(p) for p in parts)
        except (ValueError, IndexError):
            return (0,)

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for v in versions:
        game_versions = v.get("game_versions", [])
        label = game_versions[0] if game_versions else unknown_version_label
        grouped.setdefault(label, []).append(v)

    group_order = sorted(grouped.keys(), key=_mc_sort_key, reverse=True)

    for mc_label in group_order:
        grouped[mc_label].sort(key=lambda v: v.get("date_published", ""), reverse=True)

    result: List[Dict[str, Any]] = []
    for mc_label in group_order:
        result.append({"_header": mc_label, "_count": len(grouped[mc_label])})
        result.extend(grouped[mc_label])

    return result


def download_modpack_version(
    project_id: str,
    version_data: Dict[str, Any],
    status_callback: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, Any]:
    """下载一个整合包版本 → ``(success, result)``（原 ``ModpackBrowserWindow._download_version`` 的调用，逐字）。

    原文在这里传的 ``_status`` 会**先**自己发一条
    ``mp_browser_downloading`` 状态（那行 i18n 留在界面），再把它当回调传下去 ——
    服务的契约因此是"调用线程里被回调"，界面侧照旧 ``after(0, ...)``。

    失败``(False, 错误文本)``与异常**都不翻译**：原文对前者显示
    ``mp_browser_download_error``、对后者也显示同一个键，两者都在界面侧。
    """
    from modrinth import download_modpack_file

    return download_modpack_file(project_id, version_data=version_data, status_callback=status_callback)


# ═══════════════════════════════════════════════════════════════════
# 六种格式识别与元数据解析
# ═══════════════════════════════════════════════════════════════════
#
# 六个 ``_load_info_*`` 方法的本体就是"调一个 callbacks 里注入的解析器、
# 打上 ``format`` 标记、返回字典" —— 零 GUI，因此整体搬出；
# 窗口侧保留**同名同签名**的薄委托（`_load_info_mrpack(self, path)` 等），
# 免得任何既有调用点/子类/探针找不到方法。


def load_info_mrpack(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
    """Modrinth ``.mrpack``（原 ``_load_info_mrpack``，逐字）。"""
    info = callbacks["get_mrpack_information"](path)
    info["format"] = "mrpack"
    return info


def load_info_multimc(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
    """MultiMC / Prism 导出包（原 ``_load_info_multimc``，逐字）。"""
    info = callbacks["get_multimc_pack_info"](path)
    info["format"] = "multimc"
    return info


def load_info_curseforge(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
    """CurseForge ``manifest.json`` 包（原 ``_load_info_curseforge``，逐字）。"""
    info = callbacks["get_cf_pack_info"](path)
    info["format"] = "curseforge"
    return info


def load_info_hmcl(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
    """HMCL 整合包（原 ``_load_info_hmcl``，逐字）。"""
    info = callbacks["get_hmcl_pack_info"](path)
    info["format"] = "hmcl"
    return info


def load_info_mcbbs(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
    """MCBBS 整合包（原 ``_load_info_mcbbs``，逐字）。"""
    info = callbacks["get_mcbbs_pack_info"](path)
    info["format"] = "mcbbs"
    return info


def load_info_compress(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
    """通用压缩包 / 启动器整合包（原 ``_load_info_compress``，逐字）。

    ``info.get("format", "compress")``：解析器自己认出了 ``launcher_pack`` 时保留它
    （原文如此，不是笔误）。
    """
    info = callbacks["get_compress_pack_info"](path)
    info["format"] = info.get("format", "compress")
    return info


def detect_pack_format(path: str):
    """探测整合包格式（原 ``_load_mrpack_info`` 的前两步，逐字）。

    返回 ``launcher.modpack_types.ModpackDetectionResult``。
    顺带打一条原文就有的日志（``[Modpack Install] 检测格式: ...``）。
    """
    from launcher.modpack_types import detect_modpack_archive

    detection = detect_modpack_archive(path)
    logger.info(f"[Modpack Install] 检测格式: {detection.format_name}")
    return detection


def load_pack_info(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
    """探测格式 → 按格式解析元数据（原 ``ModpackInstallWindow._load_mrpack_info`` 的中段，逐字）。

    六个分支的映射与原文逐字一致（``LAUNCHER_PACK`` 与 ``GENERIC`` 都走
    ``load_info_compress``）。格式不在表里时抛 ``ValueError("不支持的整合包格式: ...")``
    —— 与原文同一句话、同一个异常类型，**由界面侧的 ``except`` 转成弹窗**。

    ``ModpackType`` 的导入刻意留在函数内（原文也是如此）：``launcher.modpack_types``
    会 ``import zipfile`` 并读盘，放进模块级导入会让"只是想 import 服务"的
    调用方也被拖上。
    """
    from launcher.modpack_types import ModpackType

    detection = detect_pack_format(path)

    info_loaders: Dict[str, Callable[[Dict[str, Any], str], Dict[str, Any]]] = {
        ModpackType.MODRINTH: load_info_mrpack,
        ModpackType.MULTIMC: load_info_multimc,
        ModpackType.CURSEFORGE: load_info_curseforge,
        ModpackType.HMCL: load_info_hmcl,
        ModpackType.MCBBS: load_info_mcbbs,
        ModpackType.LAUNCHER_PACK: load_info_compress,
        ModpackType.GENERIC: load_info_compress,
    }

    loader = info_loaders.get(detection.pack_type)
    if loader is None:
        raise ValueError(f"不支持的整合包格式: {detection.format_name}")

    return loader(callbacks, path)


# ═══════════════════════════════════════════════════════════════════
# 安装调用与结果判定
# ═══════════════════════════════════════════════════════════════════


def call_install_entry(
    callbacks: Dict[str, Any],
    mrpack_path: str,
    optional_files: List[str],
    pack_format: str = "mrpack",
) -> Tuple[bool, Any]:
    """客户端安装入口分发（原 ``ModpackInstallWindow._do_install`` 里那个 ``try`` 块，逐字）。

    两条路：

    1. ``callbacks["install_modpack"]`` 存在 → **统一入口**，可选文件以
       ``optional_file_ids=`` 传（空列表时传 ``None``，原文如此）；
    2. 否则回退旧格式分发：``format == "multimc"`` 且回调存在 → ``install_multimc_pack``，
       其余一律 ``install_mrpack``（带 ``optional_files=``）。

    ``pack_format`` 由界面算好传入（原文读的是 ``self._mrpack_info``，那是界面状态）。
    回调抛的异常**原样向上冒**，界面侧的 ``except`` 会把它转成
    ``_on_install_done(False, str(e))``。
    """
    if "install_modpack" in callbacks:
        # 新版统一入口
        success, result = callbacks["install_modpack"](
            mrpack_path, optional_file_ids=optional_files if optional_files else None
        )
    else:
        # 回退：旧版格式分发
        if pack_format == "multimc" and "install_multimc_pack" in callbacks:
            success, result = callbacks["install_multimc_pack"](mrpack_path, optional_files=optional_files)
        else:
            success, result = callbacks["install_mrpack"](mrpack_path, optional_files=optional_files)
    return success, result


def call_server_install_entry(
    callbacks: Dict[str, Any],
    mrpack_path: str,
    optional_files: List[str],
    server_name: Optional[str],
) -> Tuple[bool, Any]:
    """服务端安装入口（原 ``ModpackServerWindow._do_install`` 里那次调用，逐字）。

    只有一条路：``callbacks["install_mrpack_server"]``；没有统一入口也没有格式分发
    （服务端窗口只收 ``.mrpack``）。异常同样原样上抛。
    """
    return callbacks["install_mrpack_server"](
        mrpack_path, optional_files=optional_files, server_name=server_name
    )


def progress_percent(progress_data: Optional[Dict[str, Any]]) -> float:
    """``current / max`` 的百分比（原两个安装窗口 ``_poll_progress`` 里的同一行，逐字）。

    ``max`` 缺失/为 0 时按 1 算（``max(data.get("max", 1), 1)``），因此不会除零；
    ``data`` 为 ``None`` 时按空字典处理（原文是 ``mp.get("mrpack", {})``，
    拿到的可能确实是 ``{}``）。
    """
    data = progress_data or {}
    return (data.get("current", 0) / max(data.get("max", 1), 1)) * 100


# ═══════════════════════════════════════════════════════════════════
# 服务对象
# ═══════════════════════════════════════════════════════════════════


class ModpackService(Service):
    """整合包服务：浏览 / 安装 / 开服三个窗口的共用入口。

    约定（与 ``services/resource_service.py`` 一致）：

    - 构造期只调 ``Service.__init__``，**不读盘、不连网、不建线程**；
      可脱离 ``AppContext`` 单独实例化。
    - 需要与界面交互时由调用方把 sink / 回调传进来；服务不 import 任何 GUI。
    - ``callbacks`` 一律由界面把 ``self.callbacks`` 传进来（服务不持有它）。

    下面每个方法都是**薄委托**。
    """

    name = "modpack"
    label = "整合包"

    # ─── 在线搜索 / 版本列表 ────────────────────────────────

    @staticmethod
    def search_modpacks(query: str, offset: int, limit: int) -> SearchOutcome:
        """整合包单页搜索（原 ``_do_search``）。"""
        return search_modpacks(query, offset, limit)

    @staticmethod
    def search_ai_modpacks(query: str, token: str) -> Tuple[List[Dict[str, Any]], List[str]]:
        """AI 合并搜索整合包（原 ``_do_ai_search`` 的调用）。"""
        return search_ai_modpacks(query, token)

    @staticmethod
    def fetch_modpack_versions(project_id: str) -> List[Dict[str, Any]]:
        """整合包全部版本（原 ``_fetch_versions_and_pick`` 的取数部分）。"""
        return fetch_modpack_versions(project_id)

    @staticmethod
    def build_sorted_version_list(
        versions: List[Dict[str, Any]], unknown_version_label: str
    ) -> List[Dict[str, Any]]:
        """按 MC 版本分组 + 组内倒序 + 插分组头（原 ``_build_sorted_version_list``）。"""
        return build_sorted_version_list(versions, unknown_version_label)

    @staticmethod
    def download_modpack_version(
        project_id: str,
        version_data: Dict[str, Any],
        status_callback: Optional[Callable[[str], None]] = None,
    ) -> Tuple[bool, Any]:
        """下载整合包版本（原 ``_download_version`` 的调用）。"""
        return download_modpack_version(project_id, version_data, status_callback)

    # ─── 格式识别与元数据 ───────────────────────────────────

    @staticmethod
    def detect_pack_format(path: str):
        """探测整合包格式（原 ``_load_mrpack_info`` 前两步）。"""
        return detect_pack_format(path)

    @staticmethod
    def load_pack_info(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
        """探测 + 六分支解析（原 ``_load_mrpack_info`` 的整体编排）。"""
        return load_pack_info(callbacks, path)

    @staticmethod
    def load_info_mrpack(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
        """Modrinth ``.mrpack`` 元数据（原 ``_load_info_mrpack``）。"""
        return load_info_mrpack(callbacks, path)

    @staticmethod
    def load_info_multimc(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
        """MultiMC 包元数据（原 ``_load_info_multimc``）。"""
        return load_info_multimc(callbacks, path)

    @staticmethod
    def load_info_curseforge(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
        """CurseForge 包元数据（原 ``_load_info_curseforge``）。"""
        return load_info_curseforge(callbacks, path)

    @staticmethod
    def load_info_hmcl(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
        """HMCL 包元数据（原 ``_load_info_hmcl``）。"""
        return load_info_hmcl(callbacks, path)

    @staticmethod
    def load_info_mcbbs(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
        """MCBBS 包元数据（原 ``_load_info_mcbbs``）。"""
        return load_info_mcbbs(callbacks, path)

    @staticmethod
    def load_info_compress(callbacks: Dict[str, Any], path: str) -> Dict[str, Any]:
        """通用压缩包元数据（原 ``_load_info_compress``）。"""
        return load_info_compress(callbacks, path)

    # ─── 安装编排 ───────────────────────────────────────────

    @staticmethod
    def call_install_entry(
        callbacks: Dict[str, Any],
        mrpack_path: str,
        optional_files: List[str],
        pack_format: str = "mrpack",
    ) -> Tuple[bool, Any]:
        """客户端安装入口分发（原 ``_do_install`` 的调用与结果判定）。"""
        return call_install_entry(callbacks, mrpack_path, optional_files, pack_format)

    @staticmethod
    def call_server_install_entry(
        callbacks: Dict[str, Any],
        mrpack_path: str,
        optional_files: List[str],
        server_name: Optional[str],
    ) -> Tuple[bool, Any]:
        """服务端安装入口（原服务端 ``_do_install`` 的调用与结果判定）。"""
        return call_server_install_entry(callbacks, mrpack_path, optional_files, server_name)

    @staticmethod
    def progress_percent(progress_data: Optional[Dict[str, Any]]) -> float:
        """``current / max`` 百分比（原两个 ``_poll_progress`` 里的同一行）。"""
        return progress_percent(progress_data)


__all__ = [
    "ModpackService",
    "build_sorted_version_list",
    "call_install_entry",
    "call_server_install_entry",
    "detect_pack_format",
    "download_modpack_version",
    "fetch_modpack_versions",
    "load_info_compress",
    "load_info_curseforge",
    "load_info_hmcl",
    "load_info_mcbbs",
    "load_info_mrpack",
    "load_info_multimc",
    "load_pack_info",
    "progress_percent",
    "search_ai_modpacks",
    "search_modpacks",
]
