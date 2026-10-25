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

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from logzero import logger

from services.base import Service
from services.browse_common import (
    AI_HITS_PER_KEYWORD,
    MODPACK_VERSION_PAGE_SIZE,
    SearchOutcome,
    split_result,
)

#: 整合包的两个安装去向（旧实现是两个独立窗口：客户端安装 / 服务器安装）。
TARGET_CLIENT = "client"
TARGET_SERVER = "server"
TARGETS: Tuple[str, ...] = (TARGET_CLIENT, TARGET_SERVER)

#: 版本选择器一次追加多少条（旧 `ModpackBrowserWindow.VERSION_PAGE_SIZE = 50`）。
VERSION_PAGE_SIZE: int = MODPACK_VERSION_PAGE_SIZE

#: 格式 → 显示名的固定部分（品牌名不翻译）；`key` 非空时由界面翻这个键。
FORMAT_LABELS: Dict[str, Tuple[str, str]] = {
    "mrpack": ("Modrinth", ""),
    "multimc": ("MultiMC", ""),
    "curseforge": ("CurseForge", ""),
    "hmcl": ("HMCL", ""),
    "mcbbs": ("MCBBS", ""),
    "compress": ("", "mp_format_compress"),
    "launcher_pack": ("", "mp_format_launcher_pack"),
}

#: 六个元数据解析回调（`load_pack_info` 的 `callbacks` 只需要这几个键）。
INFO_CALLBACKS: Tuple[str, ...] = (
    "get_mrpack_information",
    "get_multimc_pack_info",
    "get_cf_pack_info",
    "get_hmcl_pack_info",
    "get_mcbbs_pack_info",
    "get_compress_pack_info",
)

#: 客户端/服务端安装入口（`call_install_entry` / `call_server_install_entry` 用）。
INSTALL_CALLBACKS: Tuple[str, ...] = (
    "install_modpack",
    "install_mrpack",
    "install_multimc_pack",
    "install_mrpack_server",
)

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
    instance_name: Optional[str] = None,
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
    name = str(instance_name or "").strip()
    if "install_modpack" in callbacks:
        # 新版统一入口
        kwargs: Dict[str, Any] = {"optional_file_ids": optional_files if optional_files else None}
        if name:
            kwargs["instance_name"] = name
        success, result = callbacks["install_modpack"](mrpack_path, **kwargs)
    else:
        # 回退：旧版格式分发
        kwargs = {"optional_files": optional_files}
        if name:
            kwargs["instance_name"] = name
        if pack_format == "multimc" and "install_multimc_pack" in callbacks:
            success, result = callbacks["install_multimc_pack"](mrpack_path, **kwargs)
        else:
            success, result = callbacks["install_mrpack"](mrpack_path, **kwargs)
    return success, result


def call_server_install_entry(
    callbacks: Dict[str, Any],
    mrpack_path: str,
    optional_files: List[str],
    server_name: Optional[str],
    mods_filter_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Tuple[bool, Any]:
    """服务端安装入口（原 ``ModpackServerWindow._do_install`` 里那次调用，逐字）。

    只有一条路：``callbacks["install_mrpack_server"]``；没有统一入口也没有格式分发
    （服务端窗口只收 ``.mrpack``）。异常同样原样上抛。

    ``mods_filter_callback`` 是阶段 3.12 新增的**可选**出口：核心完成服务端模组
    筛选后把可序列化结果交给界面（待人工确认的模组要弹窗）。传 None 时与旧行为
    逐字一致。
    """
    kwargs: Dict[str, Any] = {"optional_files": optional_files, "server_name": server_name}
    if mods_filter_callback is not None:
        kwargs["mods_filter_callback"] = mods_filter_callback
    return callbacks["install_mrpack_server"](mrpack_path, **kwargs)


def progress_percent(progress_data: Optional[Dict[str, Any]]) -> float:
    """``current / max`` 的百分比（原两个安装窗口 ``_poll_progress`` 里的同一行，逐字）。

    ``max`` 缺失/为 0 时按 1 算（``max(data.get("max", 1), 1)``），因此不会除零；
    ``data`` 为 ``None`` 时按空字典处理（原文是 ``mp.get("mrpack", {})``，
    拿到的可能确实是 ``{}``）。
    """
    data = progress_data or {}
    return (data.get("current", 0) / max(data.get("max", 1), 1)) * 100


def progress_snapshot(progress_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把核心的 launcher._mp_progress 归一成界面可直接绑定的快照（纯函数）。

    旧界面在 _poll_progress 里直接读这个字典；QML 侧不能跨对象读私有字段，
    所以桥每 200ms 取一次快照、经信号投递。字段口径逐字对齐旧实现：

    * overall 是 0..1 的比例（核心写的是 overall / 100.0），这里再夹一次
      到 0..1，避免历史数据把进度条画到框外；
    * 两路百分比走同一个 :func:`progress_percent`，因此 max=0 不会除零；
    * phase / status_text / loader_label / format_name 原样带出，
      文案翻译仍由桥做。
    """
    data = progress_data or {}
    try:
        overall = float(data.get("overall", 0) or 0)
    except (TypeError, ValueError):
        overall = 0.0
    overall = max(0.0, min(overall, 1.0))
    mp_data = data.get("mrpack")
    mc_data = data.get("vanilla")
    mp = mp_data if isinstance(mp_data, dict) else {}
    mc = mc_data if isinstance(mc_data, dict) else {}
    return {
        "phase": str(data.get("phase", "") or ""),
        "overall": overall,
        "status": str(data.get("status_text", "") or ""),
        "format_name": str(data.get("format_name", "") or ""),
        "loader_label": str(data.get("loader_label", "") or ""),
        "mrpack_pct": min(100.0, max(0.0, progress_percent(mp))),
        "mrpack_label": str(mp.get("label", "") or ""),
        "vanilla_pct": min(100.0, max(0.0, progress_percent(mc))),
        "vanilla_label": str(mc.get("label", "") or ""),
    }


def launcher_callbacks(launcher: Any) -> Dict[str, Any]:
    """从核心启动器实例取元数据/安装入口（取不到的键不放进字典）。

    为什么不让桥自己拼：六个格式解析器分散在五个 mixin 上，漏一个的表现是
    "某种整合包一选就报 KeyError"，而这种漏法在界面上看不出原因。这里用一份
    清单集中取，新增格式时只改 :data:`INFO_CALLBACKS` 一处。
    """
    out: Dict[str, Any] = {}
    if launcher is None:
        return out
    for name in INFO_CALLBACKS + INSTALL_CALLBACKS:
        fn = getattr(launcher, name, None)
        if callable(fn):
            out[name] = fn
    return out


#: Windows 文件名非法字符（实例名最终就是 versions/ 下的目录名）。
INVALID_INSTANCE_CHARS = '<>:"/\\|?*'

#: Windows 保留设备名（不区分大小写，带扩展名也算）。
_WINDOWS_RESERVED = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def default_instance_name(pack_name: str) -> str:
    """整合包名 → 默认实例名：非法字符换成下划线，空名回落 modpack。"""
    text = re.sub(r'[<>:"/\\\\|?*]', "_", str(pack_name or "")).strip()
    text = text.rstrip(". ")
    if len(text) > 100:
        text = text[:100].rstrip()
    return text or "modpack"


def validate_instance_name(name: str, installed: Optional[List[str]] = None) -> str:
    """校验实例名；合法返回空串，否则返回 i18n 键（rename_instance_*）。

    规则与核心的重命名/安装目录一致：非空、不含 Windows 非法字符与控制字符、
    不以点/空格结尾、不是 . / .. / 保留设备名；重名按不区分大小写判断。
    """
    text = str(name or "").strip()
    if not text or len(text) > 100:
        return "rename_instance_invalid"
    if any(ch in INVALID_INSTANCE_CHARS or ord(ch) < 32 for ch in text):
        return "rename_instance_invalid"
    if text in (".", "..") or text.endswith((" ", ".")):
        return "rename_instance_invalid"
    if text.split(".", 1)[0].lower() in _WINDOWS_RESERVED:
        return "rename_instance_invalid"
    existing = {str(item or "").strip().casefold() for item in (installed or []) if str(item or "").strip()}
    if text.casefold() in existing:
        return "rename_instance_exists"
    return ""


def installed_instance_ids(launcher: Any) -> List[str]:
    """已安装版本 ID（冲突检查用）；取不到时退化成空列表。"""
    if launcher is None:
        return []
    getter = getattr(launcher, "get_installed_version_ids", None)
    if not callable(getter):
        return []
    try:
        return sorted(str(item) for item in (getter() or []) if str(item).strip())
    except Exception as e:  # noqa: BLE001 - 冲突检查失败不应该挡住安装
        logger.warning("列举已安装实例失败: %s", e)
        return []


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _component_name(component: Any) -> str:
    """组件条目的显示名（旧三个渲染分支的同一行 name / uid / "?"）。"""
    if isinstance(component, dict):
        return _text(component.get("name") or component.get("uid") or "?")
    return _text(component)


def describe_pack_info(info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """把六种格式的元数据归一成页面要的一份描述（纯函数，不翻译）。

    旧 ModpackInstallWindow._show_mrpack_info 里有四条分支：MultiMC 显示组件、
    mrpack 显示可选文件复选、CurseForge 显示必装/可选计数、其余显示组件清单。
    这里把这四条分支的**数据部分**收拢，界面只按 kind 决定画法：

    * kind == "optional"：可勾选（mrpack 的 optionalFiles）；
    * kind == "list"：只读组件清单（MultiMC / HMCL / MCBBS / 压缩包）；
    * kind == "cf"：只显示 required_count / optional_count。

    mc_version 的取值顺序与旧实现一致：先 mc_version，再
    minecraftVersion（**不是** or —— 显式给了空串就按空串显示"未知"）。
    """
    data = info or {}
    pack_format = _text(data.get("format") or "mrpack")
    if "mc_version" in data:
        mc_version = _text(data.get("mc_version"))
    else:
        mc_version = _text(data.get("minecraftVersion"))

    loader_type = _text(data.get("loader_type"))
    loader_version = _text(data.get("loader_version"))
    loader_text = ""
    #: 旧实现只在这三种格式上拼加载器（mrpack/HMCL/压缩包的元数据没有这个字段）。
    if pack_format in ("multimc", "curseforge", "mcbbs") and loader_type:
        loader_text = f"{loader_type} {loader_version}".strip()

    optional_files = [_text(v) for v in (data.get("optionalFiles") or []) if _text(v)]
    raw_components = data.get("components") or []
    components: List[Dict[str, Any]] = []
    if pack_format == "mrpack":
        components = [{"name": name, "selectable": True} for name in optional_files]
    elif pack_format in ("multimc", "mcbbs", "hmcl", "compress", "launcher_pack"):
        components = [
            {"name": _component_name(comp), "selectable": False}
            for comp in raw_components
            if _component_name(comp)
        ]

    try:
        required_count = int(data.get("required_files", 0) or 0)
    except (TypeError, ValueError):
        required_count = 0
    try:
        optional_count = int(data.get("optional_files", 0) or 0)
    except (TypeError, ValueError):
        optional_count = 0

    return {
        "format": pack_format,
        "name": _text(data.get("name")),
        "summary": _text(data.get("summary")),
        "description": _text(data.get("description")),
        "mc_version": mc_version,
        "loader_type": loader_type,
        "loader_version": loader_version,
        "loader_text": loader_text,
        "optional_files": optional_files,
        "components": components,
        "required_count": required_count,
        "optional_count": optional_count,
    }


def format_label(pack_format: str) -> Dict[str, str]:
    """格式 → {"text": 品牌名, "key": i18n 键}（二者最多一个非空）。"""
    text, key = FORMAT_LABELS.get(_text(pack_format), ("", ""))
    return {"text": text, "key": key}


def trigger_achievement(achievement_id: str) -> bool:
    """触发一条成就；引擎不可用时静默返回 False（旧两个窗口的 _trigger_ach）。"""
    try:
        from achievement_engine import get_achievement_engine

        engine = get_achievement_engine()
        if engine:
            engine.update_progress(achievement_id, value=1, trigger_type="increment")
            return True
    except Exception as e:  # noqa: BLE001 - 成就失败绝不影响安装结果
        logger.warning("触发成就 %s 失败: %s", achievement_id, e)
    return False


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
        instance_name: Optional[str] = None,
    ) -> Tuple[bool, Any]:
        """客户端安装入口分发（原 ``_do_install`` 的调用与结果判定）。"""
        return call_install_entry(callbacks, mrpack_path, optional_files, pack_format, instance_name)

    @staticmethod
    def call_server_install_entry(
        callbacks: Dict[str, Any],
        mrpack_path: str,
        optional_files: List[str],
        server_name: Optional[str],
        mods_filter_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Tuple[bool, Any]:
        """服务端安装入口（原服务端 ``_do_install`` 的调用与结果判定）。"""
        return call_server_install_entry(
            callbacks, mrpack_path, optional_files, server_name, mods_filter_callback
        )

    @staticmethod
    def progress_percent(progress_data: Optional[Dict[str, Any]]) -> float:
        """``current / max`` 百分比（原两个 ``_poll_progress`` 里的同一行）。"""
        return progress_percent(progress_data)

    @staticmethod
    def progress_snapshot(progress_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """``_mp_progress`` → QML 可绑定的快照（见模块级同名函数）。"""
        return progress_snapshot(progress_data)

    @staticmethod
    def launcher_callbacks(launcher: Any) -> Dict[str, Any]:
        """从核心启动器取六个解析器与四个安装入口。"""
        return launcher_callbacks(launcher)

    @staticmethod
    def describe_pack_info(info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        """六种格式的元数据 → 页面描述（纯函数）。"""
        return describe_pack_info(info)

    @staticmethod
    def format_label(pack_format: str) -> Dict[str, str]:
        """格式 → 品牌名或 i18n 键。"""
        return format_label(pack_format)

    @staticmethod
    def trigger_achievement(achievement_id: str) -> bool:
        """触发一条成就（旧两个窗口的 ``_trigger_ach``）。"""
        return trigger_achievement(achievement_id)


__all__ = [
    "FORMAT_LABELS",
    "INFO_CALLBACKS",
    "INSTALL_CALLBACKS",
    "ModpackService",
    "TARGETS",
    "TARGET_CLIENT",
    "TARGET_SERVER",
    "VERSION_PAGE_SIZE",
    "build_sorted_version_list",
    "call_install_entry",
    "call_server_install_entry",
    "describe_pack_info",
    "detect_pack_format",
    "download_modpack_version",
    "fetch_modpack_versions",
    "format_label",
    "launcher_callbacks",
    "load_info_compress",
    "load_info_curseforge",
    "load_info_hmcl",
    "load_info_mcbbs",
    "load_info_mrpack",
    "load_info_multimc",
    "load_pack_info",
    "progress_percent",
    "progress_snapshot",
    "search_ai_modpacks",
    "search_modpacks",
    "trigger_achievement",
]
