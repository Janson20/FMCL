"""资源服务 —— 「我的资源」（客户端）与「服务器资源」两个窗口里那些**本质是逻辑、
只是恰好写在界面类里**的代码（阶段 1 任务 1.8-A）。

来源文件：``ui/windows/resource_manager.py``（1950 行 / 51 方法）与
``ui/windows/server_resource_manager.py``（871 行 / 27 方法）。

## 切缝判据（只有一条）

**这段代码是否 import GUI、或是否直接创建/销毁控件。** 是 → 留在界面；
否 → 搬进本模块。界面效果一律通过调用方传进来的 sink
（``on_progress`` / ``on_update`` / ``refresh``）或返回值完成；
服务自身不 import 任何 GUI、不弹窗、不 ``after``、不写剪贴板。

## 五条落地规则（与 ``services/agent_service.py`` 同一风格）

1. **异常不翻译**：服务只做「文件系统 / 网络 / 纯计算」，成功返回结果，
   失败**原样抛出**（``rename`` / ``copy2`` / ``rmtree`` 抛的还是原来那个异常）。
   诊断日志与提示文案留在界面侧 —— 这样界面方法里那个 ``try/except`` 的
   **边界与原文逐字相同**（原文 ``try`` 里同时包着文件操作、状态栏、列表刷新）。
2. **i18n 文案一律留界面**：本模块里**没有** ``_("...")``，也不 import ``ui.i18n``。
   少数必须在服务内部才能判定"为什么没做成"的地方（安装时的"已存在 / 格式不支持"）
   返回结构化结果 :class:`InstallResult`，由界面把 ``outcome`` 映射成文案 ——
   这样 ``scripts/check_i18n.py`` 仍能在界面文件里看到**字面量键**。
   反面做法（服务传字符串键、界面写 ``_(key)``）会让那些键从
   「键存在性 / 占位符一致 / 调用点缺参」三项静态检查里**静默掉出去**，
   正是该脚本 docstring 里点名的"覆盖范围随重构缩水"。
3. **线程**：本模块**没有任何脱离调用的线程**。``ThreadPoolExecutor`` 只在一次
   **同步调用内部**创建、并在同一次调用内 join 完（原实现就是这样，并发度
   8 / 8 / 4 也照抄）；把这次同步调用丢到后台的那个 ``threading.Thread``、
   以及 ``TaskRunner`` 缩略图池，仍然由界面起（原样）。
4. **界面状态不动**：``_page_cache`` / ``_scan_cache`` / ``_update_info`` /
   ``_thumb_generation`` / ``_thumb_inflight`` 这些**界面实例上的状态**留在界面，
   本模块只提供操作它们的纯函数（``claim_thumbnail`` / ``scan_cache_usable`` /
   ``paginate`` …）—— 于是 ``poc/probe_thumbnail_pool.py`` 与
   ``tests/test_thread_safety_fixes.py`` 里既有的"假窗口 + 真实方法"测试
   一行都不用改，它们继续守着 D-92。
5. **两份实现不合并**：客户端与服务端的同名功能**约定不同**（见
   :func:`toggle_mod` 与 :func:`toggle_server_mod`、:func:`client_mod_list_text`
   与 :func:`server_mod_list_text`、:meth:`ResourceService.check_updates` 与
   :meth:`ResourceService.collect_updates`），逐字确认不等价，因此**各自保留**，
   不合并成"看起来更干净"的一份。
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from logzero import logger

from services.base import Service

# ═══════════════════════════════════════════════════════════════════
# 常量：目录名 / 扩展名 / 并发度 / 分页
# ═══════════════════════════════════════════════════════════════════

#: 资源类型 → ``.minecraft`` 下的子目录名。
#:
#: 服务层不得 ``import ui.constants``，所以这里是 ``ui/constants.py`` 里
#: ``RESOURCE_TYPES[*]["folder"]`` 的**数据镜像**（只有目录名，没有 emoji 文案）。
#: ``tests/test_resource_service.py`` 有一条测试钉住两者不漂移。
RESOURCE_FOLDERS: Dict[str, str] = {
    "mods": "mods",
    "resourcepacks": "resourcepacks",
    "saves": "saves",
    "shaderpacks": "shaderpacks",
}

#: 资源类型 → 可安装的扩展名（``RESOURCE_TYPES[*]["extensions"]`` 的数据镜像）。
#: ``mods`` 里的 ``".disabled"`` 是原文就有的（禁用态文件的后缀也算可安装）。
RESOURCE_EXTENSIONS: Dict[str, Set[str]] = {
    "mods": {".jar", ".zip", ".disabled"},
    "resourcepacks": {".zip"},
    "saves": {".zip"},
    "shaderpacks": {".zip"},
}

#: 缩略图解压的并发上限（**D-92 守卫**：原实现"每个 zip 一个线程"，大型整合包会
#: 瞬间上百；阶段 1.22 用有界池封到 4，抽服务时原样保住这个数）。
#: ``ui/windows/resource_manager.py`` 的 ``_THUMB_WORKERS`` 直接引用本常量。
THUMB_WORKERS: int = 4

#: 客户端更新检查的并发度（原 ``ThreadPoolExecutor(max_workers=8)``）。
UPDATE_CHECK_WORKERS: int = 8
#: 客户端批量更新的并发度（原 ``ThreadPoolExecutor(max_workers=8)``）。
BATCH_UPDATE_WORKERS: int = 8
#: 服务端批量更新的并发度（原 ``ThreadPoolExecutor(max_workers=4)``，与客户端不同）。
SERVER_BATCH_UPDATE_WORKERS: int = 4

#: 两个窗口的每页条数（原 ``self._page_size: int = 10``）。
DEFAULT_PAGE_SIZE: int = 10

#: 资源包/光影里"值得异步取缩略图"的压缩包扩展名。
THUMBNAIL_ARCHIVE_EXTS: Tuple[str, ...] = (".zip",)

#: 服务端模组文件名被判定为"已禁用"的后缀。
SERVER_DISABLED_SUFFIX: str = ".disabled"


# ═══════════════════════════════════════════════════════════════════
# 结果类型
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class InstallResult:
    """一次安装尝试的结果（界面据此选文案，服务不碰 i18n）。

    ``outcome`` 取值与原文的四个分支一一对应：

    ==============  ====================================================
    outcome         原文对应的分支
    ==============  ====================================================
    ``installed``   复制/解压成功，返回 True
    ``exists``      ``dst.exists()``（模组/资源包/光影重名）→ 返回 False
    ``map_exists``  地图目录已存在（文件夹或 zip 同名）→ 返回 False
    ``unsupported`` 地图文件既不是目录也不是 ``.zip`` → 返回 False
    ``failed``      抛异常被 ``except`` 兜住 → 返回 False
    ==============  ====================================================
    """

    ok: bool
    outcome: str = "installed"
    #: ``exists`` / ``map_exists`` 时的文件名或地图名（原文用于提示文案）
    name: str = ""
    #: ``unsupported`` 时的扩展名
    ext: str = ""
    #: ``failed`` 时的异常文本
    error: str = ""


# ═══════════════════════════════════════════════════════════════════
# 成就触发（原两个模块级函数，逐字搬出；仅懒导入保留原路径）
# ═══════════════════════════════════════════════════════════════════
#
# 记录现状、疑为缺陷（**不属于本轮范围，故不改**）：任务下发时提到"两个文件里
# 各有一份 ``_trigger_ach``/``_check_ach``，行为可能不同" —— 实测
# ``ui/windows/server_resource_manager.py`` **没有**任何模块级函数（AST 探针
# ``poc/_verify_1_8a_resource.py`` 的 ``--facts`` 会打印这一点），
# 所以只有一份，不存在"两份要不要合并"的问题。
#
# 懒导入刻意**保留原路径** ``achievement_engine``（根模块，现已是
# ``services/achievement_engine`` 的别名 shim，``is`` 同一对象）：
# ``services/agent_service.py`` 当初改指了 ``services.achievement_engine``，
# 但两者等价，这里选"零改动"。


def _trigger_ach(achievement_id: str, value: int = 1, trigger_type: str = "increment"):
    try:
        from achievement_engine import get_achievement_engine

        engine = get_achievement_engine()
        if engine:
            engine.update_progress(achievement_id, value=value, trigger_type=trigger_type)
    except Exception:
        pass


def _check_ach(achievement_id: str, condition: bool):
    try:
        from achievement_engine import get_achievement_engine

        engine = get_achievement_engine()
        if engine:
            engine.check_and_unlock(achievement_id, condition)
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════
# 目录解析（原 ResourceManagerWindow._get_minecraft_dir / _has_mod_loader /
#          _get_resource_dir，ServerResourceManagerWindow._get_mods_dir）
# ═══════════════════════════════════════════════════════════════════


def minecraft_dir(callbacks: Dict[str, Any]) -> Path:
    """获取当前版本的 .minecraft 目录（原 ``ResourceManagerWindow._get_minecraft_dir``）。

    ``self.callbacks`` → 入参 ``callbacks``，其余逐字。
    """
    if "get_minecraft_dir" in callbacks:
        return Path(callbacks["get_minecraft_dir"]())
    return Path(".") / ".minecraft"


def has_mod_loader(version_id: str, mc_dir: Path) -> bool:
    """判断版本是否安装了模组加载器（需要版本隔离）。

    原 ``ResourceManagerWindow._has_mod_loader``：``self._get_minecraft_dir()``
    → 入参 ``mc_dir``，其余（含懒导入、依赖的 ``version_utils`` 函数）逐字。
    """
    from version_utils import has_mod_loader_from_json

    return has_mod_loader_from_json(version_id, str(mc_dir))


def resource_dir(version_id: str, mc_dir: Path, resource_type: str) -> Path:
    """获取指定资源类型的目录，仅模组加载器版本使用版本隔离目录。

    原 ``ResourceManagerWindow._get_resource_dir``：三处 ``self.`` 换成入参。

    ⚠ 原文**没有**任何副作用（不建目录）。``resource_type`` 不在
    :data:`RESOURCE_FOLDERS` 里时抛 ``KeyError`` —— 与原文
    ``RESOURCE_TYPES[resource_type]["folder"]`` 完全一样。这不是笔误，而是
    **D-109 的根因**：旧 ``_check_full_house`` 曾拿 ``"datapacks"`` 来问，
    于是它每次都抛、每次都被那层 ``except Exception: pass`` 吞掉，
    导致成就 ``modder_full_house`` 从引入起就没触发过。
    该缺陷已由任务下发者裁决并修正（见 :func:`check_full_house`），
    这个 ``KeyError`` 语义本身**保持不变**（它只是"没有这个资源类型"）。
    """
    folder_name: str = RESOURCE_FOLDERS[resource_type]

    # 版本隔离：仅当版本安装了模组加载器时，才使用隔离目录
    # 原版客户端虽然 versions/{版本名}/ 也存在（含 jar/json），
    # 但启动时未设置 gameDirectory，游戏资源（saves 等）仍在全局目录
    if has_mod_loader(version_id, mc_dir):
        version_dir = mc_dir / "versions" / version_id / folder_name
        logger.info(f"使用版本隔离目录: {version_dir}")
        return version_dir

    # 回退：全局 .minecraft/{folder}/
    global_dir = mc_dir / folder_name
    logger.info(f"使用全局目录: {global_dir}")
    return global_dir


def server_mods_dir(callbacks: Dict[str, Any], version_id: str) -> Path:
    """服务端模组目录（原 ``ServerResourceManagerWindow._get_mods_dir``）。

    ``self.callbacks`` → ``callbacks``、``self.version_id`` → ``version_id``，
    其余逐字（**含建目录这个副作用**：原文就是 ``mkdir(parents=True, exist_ok=True)``）。

    约定：版本 ID 里出现 ``forge`` / ``fabric`` / ``neoforge`` 之一时用
    ``{server_dir}/{version_id}/mods``（子串匹配，因此 ``neoforge`` 也会命中
    ``forge``），否则用 ``{server_dir}/mods``。回调缺失时 ``server_dir = Path(".")``。
    """
    server_dir = Path(".")
    if "get_server_dir" in callbacks:
        server_dir = Path(callbacks["get_server_dir"]())

    v = version_id.lower()
    if any(loader in v for loader in ("forge", "fabric", "neoforge")):
        mods_dir = server_dir / version_id / "mods"
    else:
        mods_dir = server_dir / "mods"
    mods_dir.mkdir(parents=True, exist_ok=True)
    return mods_dir


def resource_extensions(resource_type: str) -> Set[str]:
    """资源类型可安装的扩展名（原 ``RESOURCE_TYPES[rtype]["extensions"]``）。

    键不存在时抛 ``KeyError``（与原文一致）。
    """
    return RESOURCE_EXTENSIONS[resource_type]


def parse_drop_paths(raw: str) -> List[str]:
    """解析 tkinterdnd2 的 ``event.data``（原 ``_on_drop`` 的前 18 行，逐字）。

    tkinterdnd2 传递的路径可能用 ``{}`` 包裹且以空格分隔，因此**不能**直接
    ``split()``。花括号不成对时 ``str.index`` 抛 ``ValueError`` —— 原文如此，
    这里照旧让它抛（``_on_drop`` 不回滚，Tk 打印 traceback）。
    """
    # 处理 Windows 路径格式
    if raw.startswith("{"):
        files = []
        i = 0
        while i < len(raw):
            if raw[i] == "{":
                end = raw.index("}", i)
                files.append(raw[i + 1 : end])
                i = end + 2
            else:
                parts = raw[i:].split()
                files.extend(parts)
                break
    else:
        files = raw.split()
    return files


def accept_drop_entry(fpath: str, resource_type: str) -> bool:
    """拖拽条目是否该交给安装流程（原 ``_on_drop`` 里那两个分支条件，逐字）。

    条件一：路径存在且后缀在扩展名白名单里；
    条件二：路径存在、是目录、且当前标签页是地图（地图存档可以是文件夹）。
    """
    p = Path(fpath)
    ext_filter = RESOURCE_EXTENSIONS[resource_type]
    if p.exists() and p.suffix.lower() in ext_filter:
        return True
    if p.exists() and p.is_dir() and resource_type == "saves":
        return True
    return False


def format_size(size: int) -> str:
    """格式化文件大小（原 ``ResourceManagerWindow._format_size``，逐字）。"""
    if size < 1024:
        return f"{size} B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    else:
        return f"{size / (1024 * 1024):.1f} MB"


def dir_has_content(directory: Path) -> bool:
    """目录是否非空（原 ``ResourceManagerWindow._dir_has_content``，逐字）。

    ``any(directory.iterdir())`` 判的是"有没有条目"（``Path`` 恒为真），
    隐藏文件也算内容 —— 与原文一致。
    """
    try:
        if not directory.exists():
            return False
        return any(directory.iterdir())
    except Exception:
        return False


def check_full_house(mc_dir: Path) -> None:
    """「全家福」成就检查。

    ⚠ **本函数不是逐字搬家**：它是 **D-109 的修正版接线**（唯一的语义改动）。

    原文（``ResourceManagerWindow._check_full_house``）的判定是"五种资源目录都非空"，
    其中 ``self._get_resource_dir("datapacks")`` 必然 ``KeyError``
    （``RESOURCE_TYPES`` 只有 4 个键），又被同一函数里的 ``except Exception: pass``
    吞掉 → ``_check_ach("modder_full_house", ...)`` **从引入那次提交起就没执行过**。

    该缺陷已由任务下发者**裁决并修好工具函数**（``version_utils.scan_installed_loaders``
    / ``has_all_mod_loaders``，见 ``docs/refactor/07-known-defects.md`` 的 D-109 节、
    ``docs/refactor/04-parity-matrix.md`` 的 L-Q5、``docs/refactor/09-phase1-execution-log.md``
    §20.3）：按 4 种语言的成就文案「安装 Forge、Fabric、NeoForge 各一个版本」实现。
    但**接线一直悬空** —— 它挂在 ``_check_full_house`` 上，而那个函数正是本轮搬进
    本模块的对象。若这里仍"逐字保留现状"，等于把一条**已被裁决为要修**的缺陷
    固化进服务层，并让新增回归测试
    ``tests/test_achievement_wiring.py::test_d109_full_house_does_not_query_a_nonexistent_resource_folder``
    失败。因此本轮补上这条接线（详见报告的「必要改动」与「偏差」两节）。

    参数由原来的 ``(mods_metadata, version_id, mc_dir)`` 收敛为 ``mc_dir``：
    新判定只看 ``versions/`` 里装过哪些加载器，与 ``mods/`` 里有多少模组无关。
    ``try/except`` 照旧保留（成就系统坏了不该把界面拖下水）。
    """
    try:
        from version_utils import has_all_mod_loaders

        _check_ach("modder_full_house", has_all_mod_loaders(str(mc_dir)))
    except Exception:
        pass


def open_resource_folder(path: Path) -> None:
    """客户端「打开文件夹」：建目录 + ``os.startfile``（原 ``_open_folder``，逐字）。

    **刻意不与服务端版合并**：原客户端实现只有 Windows 分支，在 macOS/Linux 上
    ``os.startfile`` 不存在 → ``AttributeError`` → 被 ``_open_folder`` 的
    ``except`` 兜住并提示"打开文件夹失败"。合并成平台分支会**顺手修掉**这个缺陷，
    本轮只搬家，因此保留两份（另一份见 :func:`open_server_mods_folder`）。
    **记录现状、疑为缺陷**：macOS/Linux 上客户端资源管理器的"打开文件夹"必然失败
    （``Makefile`` 里有 ``build-dmg`` / ``build-deb`` 目标，所以这并非空想）。
    """
    path.mkdir(parents=True, exist_ok=True)
    os.startfile(str(path))


def open_server_mods_folder(path: Path) -> None:
    """服务端「打开文件夹」：建目录 + 平台分支（原 ``_open_folder``，逐字）。

    原文那两行 ``if not path.exists(): path.mkdir(parents=True, exist_ok=True)``
    是**冗余**的（``_get_mods_dir`` 刚建过），照抄不改。
    """
    if not path.exists():
        path.mkdir(parents=True, exist_ok=True)

    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


# ═══════════════════════════════════════════════════════════════════
# 扫描（原 ResourceManagerWindow._scan_resources）
# ═══════════════════════════════════════════════════════════════════


def scan_resources(resource_dir: Path, resource_type: str) -> List[Dict]:
    """扫描资源目录（原 ``ResourceManagerWindow._scan_resources``，逐字）。

    唯一改动：``self._format_size(size)`` → :func:`format_size`；
    ``RESOURCE_TYPES[resource_type]["extensions"]`` → :data:`RESOURCE_EXTENSIONS`。

    约定（照抄原文，别改）：

    - ``saves``：只认**目录**（且名字不以 ``.`` 开头），额外带
      ``has_level_dat``（``{dir}/level.dat`` 是否存在）；
    - 其余类型：只认**文件**，禁用态由后缀 ``.disabled`` 识别，
      真实扩展名取「倒数第二个后缀」（``foo.jar.disabled`` → ``.jar``）；
      扩展名白名单同时匹配真实扩展名与完整后缀
      （所以 ``foo.disabled`` 也会被列出来，因为 ``.disabled`` 在白名单里）；
    - 排序：``sorted(entries)``（``Path`` 之间的字典序，**不是**按 mtime）；
    - 扫描整体包在 ``try/except`` 里，失败只记日志、返回已收集的部分
      （**记录现状、疑为缺陷**：``iterdir`` 失败时界面看到的是"空目录"）。
    """
    items = []
    try:
        entries = list(resource_dir.iterdir())
        logger.info(f"目录 {resource_dir} 共有 {len(entries)} 个条目")
        if resource_type == "saves":
            # 地图是文件夹
            for entry in sorted(entries):
                if entry.is_dir() and not entry.name.startswith("."):
                    # 检查是否是有效的地图存档
                    level_dat = entry / "level.dat"
                    items.append(
                        {
                            "name": entry.name,
                            "path": str(entry),
                            "is_dir": True,
                            "has_level_dat": level_dat.exists(),
                        }
                    )
        else:
            # 模组/资源包/光影是文件
            ext_filter = RESOURCE_EXTENSIONS[resource_type]
            for entry in sorted(entries):
                if not entry.is_file():
                    continue
                # 检查文件扩展名：支持 .jar 和 .jar.disabled 等格式
                is_disabled = entry.suffix.lower() == ".disabled"
                actual_ext = (
                    entry.suffixes[-2].lower() if is_disabled and len(entry.suffixes) >= 2 else entry.suffix.lower()
                )
                if actual_ext in ext_filter or entry.suffix.lower() in ext_filter:
                    # 文件大小
                    try:
                        size = entry.stat().st_size
                        size_str = format_size(size)
                    except Exception:
                        size_str = "?"
                    items.append(
                        {
                            "name": entry.name,
                            "path": str(entry),
                            "is_dir": False,
                            "size": size_str,
                            "disabled": is_disabled,
                        }
                    )
    except Exception as e:
        logger.error(f"扫描资源目录失败: {e}")

    return items


def scan_cache_usable(cached_mtime: float, resource_dir: Path) -> bool:
    """目录 mtime 未变则可复用上一次的扫描结果。

    原 ``_refresh_current_list`` 里那段内联判断（逐字等价改写）：

    ``if resource_dir.exists(): try: if stat().st_mtime == cached_mtime: 用缓存``
    —— 目录不存在或 ``stat()`` 抛异常时都**落到完整扫描**，与这里返回 False 一致。
    """
    if not resource_dir.exists():
        return False
    try:
        return resource_dir.stat().st_mtime == cached_mtime
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════
# 搜索 / 过滤 / 分页（纯列表运算，界面只负责把结果渲染成控件）
# ═══════════════════════════════════════════════════════════════════


def normalize_search(raw: str) -> str:
    """搜索词规范化（原 ``_on_search`` 的 ``.strip().lower()``，逐字）。

    原文读的是 ``self._search_entry.get()``（控件），控件读取留在界面层。
    """
    return raw.strip().lower()


def filter_mods(mods: Sequence[Dict], search_text: str) -> Sequence[Dict]:
    """模组列表的关键字过滤（原两个窗口 ``_render_mod_list`` 里的列表推导，逐字）。

    匹配字段：``name`` / ``modid`` / ``author`` / ``description`` / ``filename``，
    全部**转小写后子串匹配**；``search_text`` 为空时**原样返回同一个对象**
    （原文就是 ``filtered = self._mod_metadata``，不是拷贝 —— 界面后续把它赋给
    ``_filtered_items``，两边共享引用是既有行为）。
    """
    if search_text:
        return [
            m
            for m in mods
            if search_text in m.get("name", "").lower()
            or search_text in m.get("modid", "").lower()
            or search_text in m.get("author", "").lower()
            or search_text in m.get("description", "").lower()
            or search_text in m.get("filename", "").lower()
        ]
    return mods


def filter_items(items: Sequence[Dict], search_text: str) -> Sequence[Dict]:
    """非模组标签页的关键字过滤（原 ``_render_filtered_list``，逐字）。

    只匹配 ``name`` 一个字段（与 :func:`filter_mods` **故意不同**，别合并）。
    """
    if search_text:
        return [item for item in items if search_text in item.get("name", "").lower()]
    return items


def total_pages(total: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
    """总页数，至少 1 页（原文的 ``max(1, ceil(total / page_size))``，逐字）。"""
    return max(1, (total + page_size - 1) // page_size)


def clamp_page(page: int, pages: int) -> int:
    """页码钳制（原 ``if self._current_page > total_pages: self._current_page = total_pages``）。

    上界与原文一致；下界的 ``max(1, ...)`` 是**防御性的**：所有调用点传给界面的
    页码都 ≥1，原文也没有下界钳制，因此对可达输入两者完全等价。
    """
    return max(1, min(page, pages))


def paginate(
    items: Sequence[Any], page: int, page_size: int
) -> Tuple[List[Any], int, int]:
    """分页计算，返回 ``(当前页条目, 钳制后的页码, 总页数)``。

    与 ``services/server_service.py`` 的同名函数**同名同形同语义**
    （连"页码越界时钳到 ``[1, 总页数]``、空列表也算 1 页"都一致）——
    刻意不跨服务 import：资源服务不该依赖服务器服务。``tests/test_resource_service.py``
    有一条测试在输入网格上比对两者，防止两份实现随时间漂移。
    """
    pages = total_pages(len(items), page_size)
    current = clamp_page(page, pages)
    start = (current - 1) * page_size
    end = min(start + page_size, len(items))
    return list(items[start:end]), current, pages


def page_items_key(page_items: Sequence[Dict]) -> Tuple[str, ...]:
    """页面缓存比对键（原 ``_render_current_page`` 里那行元组推导，逐字）。

    ``path`` 优先、退化到 ``name``；两者都没有时用空串（原文如此）。
    """
    return tuple(item.get("path", item.get("name", "")) for item in page_items)


# ═══════════════════════════════════════════════════════════════════
# 缩略图（**只搬"取图/去重/代际判定"，线程池与 CTkImage 留界面**）
# ═══════════════════════════════════════════════════════════════════


def thumbnail_key(path: Any) -> str:
    """缩略图去重键（原 ``_load_thumbnail_async`` 开头那 4 行，逐字）。

    ``resolve()`` 在路径异常时会抛（例如 Windows 上路径过长），
    此时退化成原样字符串 —— 去重仍然可用。
    """
    zip_path = Path(path)
    try:
        return str(zip_path.resolve())
    except Exception:
        return str(zip_path)


def claim_thumbnail(inflight: Set[str], path: Any) -> Optional[str]:
    """在途去重：返回去重键；返回 ``None`` 表示该路径已在队列/执行中。

    ``inflight`` 由界面持有（原 ``self._thumb_inflight``）—— 本函数只操作集合，
    不持有状态，因此可脱离界面单测。
    """
    key = thumbnail_key(path)
    if key in inflight:
        return None
    inflight.add(key)
    return key


def release_thumbnail(inflight: Set[str], key: str) -> None:
    """摘掉在途标记（原 ``self._thumb_inflight.discard(key)``）。

    **成功与失败都要摘**（原实现在 ``_apply_thumbnail`` 开头无条件 discard），
    否则一次失败会让该路径永远不再尝试。
    """
    inflight.discard(key)


def thumbnail_is_current(generation: int, current_generation: int) -> bool:
    """代际校验：列表已"重新加载"时返回 False（原 ``generation != self._thumb_generation``）。

    过期结果**仍然写缓存**（不浪费已完成解压），只是不碰界面、不抬高计数。
    """
    return generation == current_generation


def thumbnail_candidates(items: Sequence[Dict], resource_type: str) -> List[Dict]:
    """需要异步取缩略图的条目（原 ``_refresh_current_list`` 里的 zip_items 收集，逐字）。

    条件：标签页是资源包/光影、条目不是目录、后缀在
    :data:`THUMBNAIL_ARCHIVE_EXTS` 里、且还没有缓存（``_thumbnail`` 为空）。
    """
    zip_items = []
    for item in items:
        if resource_type in ("resourcepacks", "shaderpacks") and not item.get("is_dir"):
            ext = Path(item["path"]).suffix.lower()
            if ext in THUMBNAIL_ARCHIVE_EXTS and not item.get("_thumbnail"):
                zip_items.append(item)
    return zip_items


def load_thumbnail(zip_path: Path, max_size: int) -> Optional[str]:
    """从 zip 里取预览图并转成 base64（原 ``_load_thumbnail_async`` 的 ``_load`` 内层，逐字）。

    返回 base64 字符串或 ``None``；异常由调用方（``TaskRunner``）的 ``on_error`` 处理。
    取消检查（``ctx.raise_if_cancelled()``）留在界面侧的任务函数里 —— 那是
    ``TaskContext`` 的协议，属于调度层。
    """
    from modrinth import extract_zip_thumbnail

    return extract_zip_thumbnail(zip_path, max_size=max_size)


# ═══════════════════════════════════════════════════════════════════
# 删除 / 启停 / 安装 / 导入（文件系统操作；"问用户"与"提示文案"留界面）
# ═══════════════════════════════════════════════════════════════════


def delete_path(path: Any) -> None:
    """删除文件或目录（原两个窗口的 ``_delete_resource`` / ``_delete_mod`` 内核，逐字）。

    目录走 ``shutil.rmtree``（**不可恢复**，且原实现不校验传入的是不是资源目录 ——
    **记录现状、疑为缺陷**：``path`` 直接来自条目字典，没有"必须在资源目录内"的
    断言）、文件走 ``unlink``。失败**原样抛出**，由界面决定文案。
    """
    import shutil  # 延迟导入

    p = Path(path)
    if p.is_dir():
        shutil.rmtree(str(p))
    else:
        p.unlink()


def toggle_mod(path: Any, is_disabled: bool) -> str:
    """客户端模组启用/禁用，返回**操作后的文件名**。

    原 ``ResourceManagerWindow._toggle_mod``，逐字搬出，只把
    ``self._set_status(_(...))`` 与 ``self._refresh_current_list()`` 留在界面。
    成功路径的两条日志（``logzero`` 与 ``structured_logger``）跟着操作走，
    因为它们要打印**新文件名**（就是本函数的返回值）。

    **约定（照抄原文，客户端专用）**：

    - 禁用 = 在文件名后**追加** ``.disabled``（``Path(str(p) + ".disabled")``）；
    - 启用 = ``Path.with_suffix("")`` 去掉**最后一个后缀**
      （``foo.jar.disabled`` → ``foo.jar``）；
    - 与 :func:`toggle_server_mod` 的约定**不同**（后者用字符串切片 ``[:-9]``），
      两者**不合并**。

    失败**原样抛出**（原文在界面侧统一 ``except`` 成"操作失败: {error}"）。
    """
    from structured_logger import slog

    p = Path(path)
    mod_name = p.name
    if is_disabled:
        # 启用：移除 .disabled 后缀
        new_path = p.with_suffix("")
        p.rename(new_path)
        logger.info(f"模组已启用: {p.name} -> {new_path.name}")
        slog.info("mod_enabled", mod_name=mod_name, new_name=new_path.name)
    else:
        # 禁用：添加 .disabled 后缀
        new_path = Path(str(p) + ".disabled")
        p.rename(new_path)
        logger.info(f"模组已禁用: {p.name} -> {new_path.name}")
        slog.info("mod_disabled", mod_name=mod_name, new_name=new_path.name)
    return new_path.name


def toggle_server_mod(filepath: str, disabled: bool) -> bool:
    """服务端模组启用/禁用，返回**操作后的 disabled 状态**。

    原 ``ServerResourceManagerWindow._toggle_mod`` 的改名内核，逐字搬出
    （``self._refresh_mod_list()`` / ``self._set_status(...)`` 留界面）。
    前置守卫（``filepath`` 为空、``src.exists()`` 为假就什么都不做）留在界面，
    因为它是"这个按钮该不该有反应"的界面判断。

    **约定（照抄原文，服务端专用）**：

    - 禁用 = 文件名后追加 ``.disabled``；
    - 启用 = 若以 ``.disabled`` 结尾则**切掉最后 9 个字符**
      （不是 ``with_suffix("")``：``foo.jar.disabled`` → ``foo.jar``，
      但 ``foo.disabled.disabled`` → ``foo.disabled``，与客户端版在
      "多重后缀"上的表现可能不同，故不合并）。

    失败**原样抛出**（原文在界面侧 ``except`` 成 ``f"切换模组状态失败: {e}"``）。
    """
    src = Path(filepath)
    if disabled:
        dst_name = src.name
        if dst_name.endswith(SERVER_DISABLED_SUFFIX):
            dst_name = dst_name[: -len(SERVER_DISABLED_SUFFIX)]
        dst = src.parent / dst_name
        new_disabled = False
    else:
        dst = src.parent / (src.name + SERVER_DISABLED_SUFFIX)
        new_disabled = True

    src.rename(dst)
    return new_disabled


def install_save(src: Path, saves_dir: Path) -> InstallResult:
    """安装地图存档：zip 自动解压 / 文件夹直接复制。

    原 ``ResourceManagerWindow._install_save``，逐字搬出；四处
    ``self._set_status(_(...)); return False`` → ``return InstallResult(False, <outcome>)``。

    **没有** ``try/except``（原文也没有）—— 异常原样抛出，由
    :func:`install_resource` 的 ``except`` 兜住（与原文的层次一致）。

    解压约定（照抄原文）：zip 顶层有 ``level.dat`` → 整体解压到
    ``saves/{zip 主名}/``；否则找第一个以 ``level.dat`` 结尾的成员，
    把其所在子目录的内容**去掉前缀**后逐个写到目标目录；
    都找不到就整体解压。**记录现状、疑为缺陷**：原文算出的
    ``top_entries``（第 1300 行）从头到尾没被使用。
    """
    import shutil  # 延迟导入
    import zipfile

    if src.is_dir():
        # 文件夹直接复制到 saves/地图名/
        dst = saves_dir / src.name
        if dst.exists():
            logger.warning(f"地图已存在: {dst}")
            return InstallResult(False, "map_exists", name=src.name)
        shutil.copytree(str(src), str(dst))
        logger.info(f"地图安装成功(文件夹): {src.name} -> {dst}")
        return InstallResult(True)

    elif src.suffix.lower() == ".zip":
        # zip 文件解压到 saves/地图名/ 下
        # 地图名 = zip 文件名去掉扩展名
        map_name = src.stem
        dst = saves_dir / map_name
        if dst.exists():
            logger.warning(f"地图已存在: {dst}")
            return InstallResult(False, "map_exists", name=map_name)

        with zipfile.ZipFile(str(src), "r") as zf:
            namelist = zf.namelist()
            # 检查zip内部结构：可能是直接包含level.dat，也可能有一层包装目录
            # 情况1: zip内顶层就有 level.dat -> 解压到 saves/地图名/
            # 情况2: zip内有一个子目录包含 level.dat -> 解压该子目录到 saves/地图名/
            top_entries = [n for n in namelist if "/" not in n.rstrip("/") or n.count("/") == 0]
            has_root_level_dat = any(n == "level.dat" for n in namelist)

            if has_root_level_dat:
                # 直接解压所有内容到 dst
                zf.extractall(str(dst))
            else:
                # 查找包含 level.dat 的子目录
                level_dat_entries = [n for n in namelist if n.endswith("level.dat")]
                if level_dat_entries:
                    # 取 level.dat 所在的子目录名
                    sub_dir = level_dat_entries[0].rsplit("level.dat", 1)[0].rstrip("/")
                    # 解压该子目录的内容到 dst
                    for member in zf.namelist():
                        if member.startswith(sub_dir + "/"):
                            # 去掉子目录前缀，提取到 dst
                            relative = member[len(sub_dir) + 1 :]
                            if not relative:
                                continue
                            target = dst / relative
                            if member.endswith("/"):
                                target.mkdir(parents=True, exist_ok=True)
                            else:
                                target.parent.mkdir(parents=True, exist_ok=True)
                                with zf.open(member) as src_file:
                                    with open(str(target), "wb") as dst_file:
                                        dst_file.write(src_file.read())
                else:
                    # 没找到 level.dat，直接全部解压
                    zf.extractall(str(dst))

        logger.info(f"地图安装成功(zip): {src.name} -> {dst}")
        return InstallResult(True)

    else:
        logger.warning(f"不支持的地图格式: {src.suffix}")
        return InstallResult(False, "unsupported", ext=src.suffix)


def install_resource(src_path: str, resource_type: str, resolve_dir: Callable[[], Path]) -> InstallResult:
    """安装资源文件到对应目录（原 ``ResourceManagerWindow._install_resource``，逐字）。

    ``resolve_dir`` 是**延迟求值的目录解析回调**：原文
    ``resource_dir = self._get_resource_dir(resource_type)`` 位于 ``try`` **内部**，
    传 ``Path`` 进来会把求值挪到 ``try`` 外面（拿到非法 ``resource_type`` 时
    异常边界就变了）；传 callable 才能保住边界。
    界面侧传 ``lambda: self._get_resource_dir(resource_type)``。

    三处 "已存在 / 格式不支持" 的提示改由返回值表达（见 :class:`InstallResult`），
    界面照旧把它们写进状态栏（原文那次写入会被调用方紧接着**覆盖**掉，见报告）。
    """
    import shutil  # 延迟导入：仅资源管理窗口使用

    try:
        resource_dir = resolve_dir()
        resource_dir.mkdir(parents=True, exist_ok=True)

        src = Path(src_path)

        if resource_type == "saves":
            return install_save(src, resource_dir)
        else:
            dst = resource_dir / src.name
            if dst.exists():
                logger.warning(f"资源已存在: {dst}")
                return InstallResult(False, "exists", name=src.name)
            shutil.copy2(str(src), str(dst))
            logger.info(f"资源安装成功: {src.name} -> {dst}")
            return InstallResult(True)

    except Exception as e:
        logger.error(f"安装资源失败: {e}")
        return InstallResult(False, "failed", error=str(e))


def install_server_mods(paths: Sequence[str], mods_dir: Path) -> int:
    """把选中的文件复制进服务端 mods 目录，返回成功个数。

    原 ``ServerResourceManagerWindow._select_file_install`` 的循环体，逐字搬出
    （``filedialog`` 与状态栏留界面）。只接受 ``.jar`` / ``.zip``；
    **单个文件失败只记日志、继续下一个**（原文如此，不中断整批）。
    """
    import shutil

    installed = 0
    for fpath in paths:
        p = Path(fpath)
        if p.suffix.lower() in (".jar", ".zip"):
            try:
                shutil.copy2(str(p), str(mods_dir / p.name))
                installed += 1
            except Exception as e:
                logger.error(f"复制模组文件失败: {e}")
    return installed


def write_text_file(path: str, content: str) -> None:
    """写文本文件（原服务端 ``_export_mod_list`` 里那两行，逐字）。

    ``encoding="utf-8"``、不传 ``newline=``（换行按平台默认）—— 与原文一致。
    """
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ═══════════════════════════════════════════════════════════════════
# 导出清单（文本生成；剪贴板/文件对话框留界面）
# ═══════════════════════════════════════════════════════════════════


def client_mod_list_text(mods: Sequence[Dict], header: str) -> str:
    """客户端「导出模组列表」的分享文本（原 ``_export_mod_list`` 的文本生成，逐字）。

    ``header`` 由界面用 i18n 拼好（原文是
    ``f"=== {_('mod_export_header', version=self.version_id)} ===\\n"``，
    **带结尾换行**）—— 服务层不 import ``ui.i18n``，所以这一行作为入参传进来；
    ``"\\n".join`` 之后仍然是原文那个"标题后有一个空行"的格式。

    **记录现状、疑为缺陷**：禁用标记是硬编码英文 ``" [Disabled]"``
    （同一份列表里的其它文案都走了 i18n）。
    """
    lines = []
    lines.append(header)

    for i, mod in enumerate(mods, 1):
        name = mod.get("name", mod.get("filename", "???"))
        modid = mod.get("modid", "-")
        version = mod.get("version", "-")
        disabled = " [Disabled]" if mod.get("disabled") else ""
        lines.append(f"{i}. {name}{disabled}")
        lines.append(f"   modid: {modid}  |  version: {version}")

    lines.append(f"\nTotal: {len(mods)} mods")
    return "\n".join(lines)


def server_mod_list_text(
    mods: Sequence[Dict], header: str, enabled_label: str, disabled_label: str
) -> str:
    """服务端「导出模组列表」的文本（原 ``_export_mod_list`` 的文本生成，逐字）。

    与客户端版**完全不同**（分隔符 ``" - "``、带 ``ID:``、带启用状态、
    ``modid``/``version`` 为空时整段不出现、没有 Total 行），因此不合并。

    ``header`` / ``enabled_label`` / ``disabled_label`` 由界面传入
    （原文分别取 ``_("mod_export_header")`` / ``_("mod_enabled")`` / ``_("mod_disabled")``）。
    """
    lines = [header]
    for m in mods:
        name = m.get("name", m.get("filename", ""))
        modid = m.get("modid", "")
        version = m.get("version", "")
        disabled = m.get("disabled", False)
        status = disabled_label if disabled else enabled_label
        parts = [name]
        if modid:
            parts.append(f"ID: {modid}")
        if version:
            parts.append(version)
        parts.append(status)
        lines.append(" - ".join(parts))
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════
# 更新检测编排（双源 Modrinth + CurseForge）
# ═══════════════════════════════════════════════════════════════════
#
# ⚠ 客户端版与服务端版的编排**确实不同**，逐字确认过，因此各自保留：
#
# ==================  ==============================  ==============================
#                     客户端 ``check_updates``         服务端 ``collect_updates``
# ==================  ==============================  ==============================
# 并发                 ``ThreadPoolExecutor(8)``        串行 for
# 进度时机             每个模组结束时（``finally``）      每个模组结束时（for 尾部）
# 结果字典键            ``mod_name`` / ``mod_path``       ``title``
#                       ``current_version``               （无 current_version/mod_path）
# 未用导入             ``modrinth.compare_mod_versions``  （无）
# ==================  ==============================  ==============================
#
# 把两份"合并成一份干净实现"会同时改变并发度、进度时序与 ``_update_info`` 的键名，
# 界面侧两个 ``_show_update_dialog`` 读的键也各不相同 —— 所以不合并。


def update_targets(version_id: str) -> Tuple[Optional[str], Optional[str]]:
    """从版本 ID 解出 ``(game_version, mod_loader)``。

    原两个 ``_check_mod_updates`` 开头那 4 行（懒导入 + 两个解析函数），逐字搬出。
    注意 ``mod_loader`` 过了一层 :func:`version_utils.resolve_search_loader`
    （把 ``legacyfabric`` 之类映射成 API 认识的名字），照抄。
    """
    from modrinth import parse_game_version_from_version, parse_mod_loader_from_version
    from version_utils import resolve_search_loader

    game_version = parse_game_version_from_version(version_id)
    mod_loader = resolve_search_loader(parse_mod_loader_from_version(version_id))
    return game_version, mod_loader


def updatable_mods(mods: Sequence[Dict]) -> List[Dict]:
    """可检查更新的模组：有 ``modid`` 且**未禁用**（原两个窗口里那行列表推导，逐字）。"""
    return [m for m in mods if m.get("modid") and not m.get("disabled")]


def check_updates(
    mods: Sequence[Dict],
    game_version: str,
    mod_loader: Optional[str],
    workers: int = UPDATE_CHECK_WORKERS,
    on_progress: Optional[Callable[[int, int], None]] = None,
    on_update: Optional[Callable[[str, Dict], None]] = None,
) -> int:
    """客户端模组更新检查（原 ``_check_mod_updates`` 里 ``_do_check`` 的内层编排，逐字）。

    三处界面动作换成 sink：

    - ``self.after(0, lambda c=checked[0]: self._set_status(...))`` → ``on_progress(checked[0], total)``
      （仍在锁内调用、顺序不变；界面侧在 sink 里 ``after``，并把参数绑进默认参数）
    - ``self._update_info[result["modid"]] = {...}`` → ``on_update(modid, {...})``
      （结果字典的 5 个键与原文逐字一致，**不含 ``source``**）
    - ``self.after(0, lambda: self._on_update_check_done(...))`` 留在界面

    返回**发现更新的模组数**（原文的 ``updates_found[0]``）。
    异常**不在这里兜**：原文的 ``try/except`` 在外层 ``_do_check`` 上（那里要
    ``self.after(0, ...)`` 回主线程报错），因此留在界面。
    """
    import threading as _threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from curseforge import check_update_dual_source
    from modrinth import compare_mod_versions  # noqa: F401 - 原文导入了但从未使用（记录现状、疑为缺陷）

    lock = _threading.Lock()
    checked = [0]
    updates_found = [0]
    total = len(mods)

    def _check_one(mod):
        modid = mod["modid"]
        current_version = mod.get("version", "")

        try:
            # 双源检查（Modrinth + CurseForge），取最新版本
            update = check_update_dual_source(
                modid=modid,
                mod_name=mod.get("name", modid),
                current_version=current_version,
                game_version=game_version,
                mod_loader=mod_loader,
            )
            if not update:
                return None

            return {
                "modid": modid,
                "project_id": update["project_id"],
                "source": update.get("source", "modrinth"),
                "latest_version": update["latest_version"],
                "current_version": update["current_version"],
                "mod_name": mod.get("name", modid),
                "mod_path": mod.get("path", ""),
            }
        except Exception as e:
            logger.debug(f"检查模组更新失败 ({modid}): {e}")
            return None
        finally:
            with lock:
                checked[0] += 1
                if on_progress is not None:
                    on_progress(checked[0], total)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_check_one, mod): mod for mod in mods}
        for future in as_completed(futures):
            result = future.result()
            if result:
                updates_found[0] += 1
                if on_update is not None:
                    on_update(
                        result["modid"],
                        {
                            "project_id": result["project_id"],
                            "latest_version": result["latest_version"],
                            "current_version": result["current_version"],
                            "mod_name": result["mod_name"],
                            "mod_path": result["mod_path"],
                        },
                    )

    return updates_found[0]


def collect_updates(
    mods: Sequence[Dict],
    game_version: str,
    mod_loader: Optional[str],
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Dict]:
    """服务端模组更新检查（原 ``_do_check_updates``，逐字搬出）。

    与客户端版的差异见本小节开头的对照表。``self._update_info[modid] = {...}``
    → 收集进返回值（键 ``latest_version`` / ``project_id`` / ``source`` / ``title``，
    与原文逐字一致）；``self.after(0, lambda ...: self._set_status(...))`` →
    ``on_progress(progress, total)``。

    单个模组失败只 ``logger.debug`` 并继续（原文如此）。
    """
    from curseforge import check_update_dual_source

    update_info: Dict[str, Dict] = {}
    for i, mod in enumerate(mods):
        modid = mod.get("modid", "")
        try:
            update = check_update_dual_source(
                modid=modid,
                mod_name=mod.get("name", ""),
                current_version=mod.get("version", ""),
                game_version=game_version,
                mod_loader=mod_loader,
            )
            if update:
                update_info[modid] = {
                    "latest_version": update["latest_version"],
                    "project_id": update["project_id"],
                    "source": update.get("source", "modrinth"),
                    "title": mod.get("name", ""),
                }
        except Exception as e:
            logger.debug(f"检查模组 {modid} 更新失败: {e}")
        progress = i + 1
        total = len(mods)
        if on_progress is not None:
            on_progress(progress, total)

    return update_info


def batch_update_mods(
    modids: Sequence[str],
    update_info: Dict[str, Dict],
    game_version: str,
    mod_loader: Optional[str],
    workers: int = BATCH_UPDATE_WORKERS,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[int, int]:
    """客户端批量更新（原 ``_batch_update_mods`` 里 ``_download_one`` + ``_run_batch``，逐字）。

    返回 ``(成功数, 失败数)``；``on_progress(done, total)`` 对应原文 ``finally`` 里的
    ``self.after(0, lambda d=done[0]: self._set_status(...))``。
    ``self._update_info.get(modid)`` → 入参 ``update_info``（原文读的就是它）。

    记录现状、疑为缺陷（照抄不改）：删除旧文件那段
    ``try: old_path.unlink() except Exception: pass`` 会**先删后下**，
    下载失败时旧模组已经没了；``download_mod`` 返回的第二个值（``_dl_result``）
    被丢弃。
    """
    import threading as _threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from modrinth import download_mod, get_project_latest_version

    lock = _threading.Lock()
    done = [0]
    success_count = [0]
    fail_count = [0]

    def _download_one(modid):
        info = update_info.get(modid)
        if not info:
            return False, modid

        project_id = info["project_id"]
        mod_name = info["mod_name"]
        mod_path = info["mod_path"]

        try:
            # 复用已缓存的 latest_version 信息，直接获取该版本的 files
            version = get_project_latest_version(project_id, game_version=game_version, mod_loader=mod_loader)
            if not version:
                return False, mod_name

            files = version.get("files", [])
            primary = next((f for f in files if f.get("primary")), None) or (files[0] if files else None)
            if not primary:
                return False, mod_name

            download_url = primary.get("url", "")
            filename = primary.get("filename", f"{mod_name}.jar")
            if not download_url:
                return False, mod_name

            mods_dir = str(Path(mod_path).parent)

            # 删除旧文件
            try:
                old_path = Path(mod_path)
                if old_path.exists():
                    old_path.unlink()
            except Exception:
                pass

            hashes = primary.get("hashes")
            dl_success, _dl_result = download_mod(download_url, mods_dir, filename, expected_hashes=hashes)

            if dl_success:
                logger.info(f"模组更新完成: {mod_name} → {version.get('version_number','?')}")
                return True, mod_name
            else:
                return False, mod_name

        except Exception as e:
            logger.error(f"更新模组失败 ({mod_name}): {e}")
            return False, mod_name
        finally:
            with lock:
                done[0] += 1
                if on_progress is not None:
                    on_progress(done[0], len(modids))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_download_one, mid): mid for mid in modids}
        for future in as_completed(futures):
            ok, name = future.result()
            if ok:
                success_count[0] += 1
            else:
                fail_count[0] += 1

    return success_count[0], fail_count[0]


def server_batch_update_mods(
    modids: Sequence[str],
    mods_dir: Any,
    game_version: str,
    mod_loader: Optional[str],
    workers: int = SERVER_BATCH_UPDATE_WORKERS,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> Tuple[int, int]:
    """服务端批量更新（原服务端 ``_batch_update_mods`` 里 ``_update_one``，逐字搬出）。

    与客户端版**不同**：并发度 4（不是 8）、用 ``get_mod_versions(...)[0]``
    取最新版（不是 ``get_project_latest_version``）、不删旧文件、
    ``project_id`` 直接用 ``modid``、不传 ``expected_hashes``。返回 ``(成功数, 失败数)``。

    原文用 ``as_completed`` 只是为了等全部结束（循环体是 ``pass``），这里照抄。
    """
    import threading as _threading
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from modrinth import download_mod, get_mod_versions

    lock = _threading.Lock()
    done = [0]
    success_count = [0]
    fail_count = [0]

    def _update_one(modid: str):
        try:
            versions = get_mod_versions(project_id=modid, game_version=game_version, mod_loader=mod_loader)
            if not versions:
                with lock:
                    fail_count[0] += 1
                return
            latest = versions[0]
            file_url = None
            file_name = None
            for v in latest.get("files", []):
                if v.get("primary"):
                    file_url = v.get("url")
                    file_name = v.get("filename")
                    break
            if not file_url:
                files = latest.get("files", [])
                if files:
                    file_url = files[0].get("url")
                    file_name = files[0].get("filename")
            if file_url and file_name:
                ok = download_mod(file_url, mods_dir, file_name)
                if ok:
                    with lock:
                        success_count[0] += 1
                else:
                    with lock:
                        fail_count[0] += 1
            else:
                with lock:
                    fail_count[0] += 1
        except Exception as e:
            logger.error(f"批量更新模组失败 ({modid}): {e}")
            with lock:
                fail_count[0] += 1
        finally:
            with lock:
                done[0] += 1
                d = done[0]
                t = len(modids)
                if on_progress is not None:
                    on_progress(d, t)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(_update_one, mid): mid for mid in modids}
        for f in as_completed(futures):
            pass

    return success_count[0], fail_count[0]


# ═══════════════════════════════════════════════════════════════════
# 仅客户端模组判定（规则本身在 launcher/mod_classifier/，本来就是零 GUI）
# ═══════════════════════════════════════════════════════════════════


def filter_client_mods(mods_dir: str, use_online: bool = True, dry_run: bool = False, progress_callback=None):
    """识别并（默认真的）禁用纯客户端模组，返回分类结果对象。

    原 ``ServerResourceManagerWindow._do_filter_client_mods``：判定规则**不在**
    本模块 —— 它在 ``launcher/mod_classifier/``（``rules.classify_local`` 的
    本地规则 + ``query.lookup_modrinth`` 的在线核实 + ``pipeline.filter_server_mods``
    的编排），本来就没有 GUI 依赖。这里只做调用与返回值透传，
    ``after`` / 通知 / 按钮状态全部留在界面。

    ``dry_run=False`` 意味着**真的会改文件名**（禁用纯客户端模组）—— 与原文一致。
    """
    from launcher.mod_classifier import filter_server_mods

    return filter_server_mods(mods_dir, use_online=use_online, dry_run=dry_run, progress_callback=progress_callback)


# ═══════════════════════════════════════════════════════════════════
# 服务对象
# ═══════════════════════════════════════════════════════════════════


class ResourceService(Service):
    """资源业务服务：「我的资源」与「服务器资源」两个窗口的共用入口。

    约定（与 ``services/monitor_service.py`` / ``services/online_service.py`` 一致）：

    - 构造期只调 ``Service.__init__``，**不读盘、不连网、不建线程**；
      可脱离 ``AppContext`` 单独实例化 —— ``attached is False`` 时下面所有方法
      都照常工作（它们不碰 ``self.context``）。
    - 需要与界面交互时由调用方把 sink / 回调传进来，服务只调用它；
      服务自身不 import 任何 GUI。
    - 服务之间不互相 import（本模块只依赖根模块 ``modrinth`` / ``curseforge`` /
      ``version_utils`` / ``structured_logger`` / ``launcher.mod_classifier``，
      它们本来就是零 GUI 的）。

    下面每个方法都是**薄委托**（一行转调同名模块级函数），这样：
    纯逻辑可以脱离服务对象单测，界面侧也只需要一个稳定的入口名。
    """

    name = "resource"
    label = "资源管理"

    #: 缩略图并发上限（D-92 守卫）；界面侧的 ``_THUMB_WORKERS`` 直接引用它。
    THUMB_WORKERS: int = THUMB_WORKERS

    # ─── 目录解析 ───────────────────────────────────────────

    @staticmethod
    def minecraft_dir(callbacks: Dict[str, Any]) -> Path:
        """当前版本的 ``.minecraft`` 目录（原 ``_get_minecraft_dir``）。"""
        return minecraft_dir(callbacks)

    @staticmethod
    def has_mod_loader(version_id: str, mc_dir: Path) -> bool:
        """版本是否装了模组加载器（原 ``_has_mod_loader``）。"""
        return has_mod_loader(version_id, mc_dir)

    @staticmethod
    def resource_dir(version_id: str, mc_dir: Path, resource_type: str) -> Path:
        """资源目录（原 ``_get_resource_dir``）；非法类型抛 ``KeyError``。"""
        return resource_dir(version_id, mc_dir, resource_type)

    @staticmethod
    def resource_dir_for(callbacks: Dict[str, Any], version_id: str, resource_type: str) -> Path:
        """一次到位：``callbacks`` → ``.minecraft`` → 资源目录（界面最常用的入口）。"""
        return resource_dir(version_id, minecraft_dir(callbacks), resource_type)

    @staticmethod
    def server_mods_dir(callbacks: Dict[str, Any], version_id: str) -> Path:
        """服务端模组目录（原服务端 ``_get_mods_dir``，**会建目录**）。"""
        return server_mods_dir(callbacks, version_id)

    @staticmethod
    def resource_extensions(resource_type: str) -> Set[str]:
        """资源类型的扩展名白名单（原 ``RESOURCE_TYPES[rtype]["extensions"]``）。"""
        return resource_extensions(resource_type)

    @staticmethod
    def parse_drop_paths(raw: str) -> List[str]:
        """解析拖拽事件里的路径串（原 ``_on_drop`` 前 18 行）。"""
        return parse_drop_paths(raw)

    @staticmethod
    def accept_drop_entry(fpath: str, resource_type: str) -> bool:
        """拖拽条目是否该交给安装流程（原 ``_on_drop`` 的两个分支条件）。"""
        return accept_drop_entry(fpath, resource_type)

    # ─── 扫描 ───────────────────────────────────────────────

    @staticmethod
    def scan_resources(resource_dir: Path, resource_type: str) -> List[Dict]:
        """扫描资源目录（原 ``_scan_resources``）。"""
        return scan_resources(resource_dir, resource_type)

    @staticmethod
    def scan_cache_usable(cached_mtime: float, resource_dir: Path) -> bool:
        """目录 mtime 未变则可复用扫描缓存（原 ``_refresh_current_list`` 里的内联判断）。"""
        return scan_cache_usable(cached_mtime, resource_dir)

    @staticmethod
    def format_size(size: int) -> str:
        """``1024`` → ``"1.0 KB"``（原 ``_format_size``）。"""
        return format_size(size)

    @staticmethod
    def dir_has_content(directory: Path) -> bool:
        """目录是否有内容（原 ``_dir_has_content``）。"""
        return dir_has_content(directory)

    @staticmethod
    def extract_mods_metadata(mods_dir: Path, status_callback=None) -> List[Dict]:
        """读取 ``mods/`` 下每个 jar 的元数据（原 ``_refresh_mod_list`` 里那两行）。

        ``status_callback(done, total)`` 由调用方在 **worker 线程**里被回调 ——
        界面侧照原文那样 ``after(0, ...)`` 切回主线程。
        """
        from modrinth import extract_all_mods_metadata

        return extract_all_mods_metadata(mods_dir, status_callback=status_callback)

    # ─── 搜索 / 过滤 / 分页 ─────────────────────────────────

    @staticmethod
    def normalize_search(raw: str) -> str:
        """搜索词规范化（原 ``.strip().lower()``）。"""
        return normalize_search(raw)

    @staticmethod
    def filter_mods(mods: Sequence[Dict], search_text: str) -> Sequence[Dict]:
        """模组关键字过滤（5 个字段，小写子串）。"""
        return filter_mods(mods, search_text)

    @staticmethod
    def filter_items(items: Sequence[Dict], search_text: str) -> Sequence[Dict]:
        """非模组标签页过滤（只看 ``name``）。"""
        return filter_items(items, search_text)

    @staticmethod
    def total_pages(total: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
        """总页数（至少 1）。"""
        return total_pages(total, page_size)

    @staticmethod
    def clamp_page(page: int, pages: int) -> int:
        """页码钳制到 ``[1, pages]``。"""
        return clamp_page(page, pages)

    @staticmethod
    def paginate(items: Sequence[Any], page: int, page_size: int) -> Tuple[List[Any], int, int]:
        """``(当前页条目, 页码, 总页数)``。"""
        return paginate(items, page, page_size)

    @staticmethod
    def page_items_key(page_items: Sequence[Dict]) -> Tuple[str, ...]:
        """页面缓存比对键（``path`` 优先，退化到 ``name``）。"""
        return page_items_key(page_items)

    # ─── 启停 / 删除 / 安装 ─────────────────────────────────

    @staticmethod
    def delete_path(path: Any) -> None:
        """删除文件或目录（原 ``_delete_resource`` / ``_delete_mod`` 的内核）。"""
        delete_path(path)

    @staticmethod
    def toggle_mod(path: Any, is_disabled: bool) -> str:
        """客户端模组启停（``.disabled`` 后缀约定），返回新文件名。"""
        return toggle_mod(path, is_disabled)

    @staticmethod
    def toggle_server_mod(filepath: str, disabled: bool) -> bool:
        """服务端模组启停（``.disabled`` 后缀约定），返回操作后的禁用状态。"""
        return toggle_server_mod(filepath, disabled)

    @staticmethod
    def install_resource(src_path: str, resource_type: str, resolve_dir: Callable[[], Path]) -> InstallResult:
        """安装资源（复制 / 解压；"已存在"等判定在这里）。"""
        return install_resource(src_path, resource_type, resolve_dir)

    @staticmethod
    def install_save(src: Path, saves_dir: Path) -> InstallResult:
        """安装地图存档（文件夹复制 / zip 解压）。"""
        return install_save(src, saves_dir)

    @staticmethod
    def install_server_mods(paths: Sequence[str], mods_dir: Path) -> int:
        """把文件复制进服务端 mods 目录，返回成功个数。"""
        return install_server_mods(paths, mods_dir)

    @staticmethod
    def write_text_file(path: str, content: str) -> None:
        """UTF-8 写文本（导出清单落盘）。"""
        write_text_file(path, content)

    @staticmethod
    def open_resource_folder(path: Path) -> None:
        """客户端「打开文件夹」（Windows 专用，原文如此）。"""
        open_resource_folder(path)

    @staticmethod
    def open_server_mods_folder(path: Path) -> None:
        """服务端「打开文件夹」（Windows / macOS / Linux 三分支）。"""
        open_server_mods_folder(path)

    # ─── 导出 ───────────────────────────────────────────────

    @staticmethod
    def client_mod_list_text(mods: Sequence[Dict], header: str) -> str:
        """客户端模组列表分享文本（``header`` 由界面用 i18n 拼好）。"""
        return client_mod_list_text(mods, header)

    @staticmethod
    def server_mod_list_text(
        mods: Sequence[Dict], header: str, enabled_label: str, disabled_label: str
    ) -> str:
        """服务端模组列表文本（三个文案由界面用 i18n 拼好）。"""
        return server_mod_list_text(mods, header, enabled_label, disabled_label)

    # ─── 成就 ───────────────────────────────────────────────

    @staticmethod
    def check_full_house(mc_dir: Path) -> None:
        """「全家福」成就检查（D-109 修正版：按加载器盘点）。

        **记录现状已被修正**：原实现查 ``"datapacks"`` 目录必然 ``KeyError``，
        成就从不触发；此处按任务下发者的 D-109 裁决改成
        「Forge / Fabric / NeoForge 各装过一个版本」
        （``version_utils.has_all_mod_loaders``）。详见模块级 :func:`check_full_house`。
        """
        check_full_house(mc_dir)

    # ─── 更新检测 / 批量更新 ────────────────────────────────

    @staticmethod
    def update_targets(version_id: str) -> Tuple[Optional[str], Optional[str]]:
        """``version_id`` → ``(game_version, mod_loader)``。"""
        return update_targets(version_id)

    @staticmethod
    def updatable_mods(mods: Sequence[Dict]) -> List[Dict]:
        """有 ``modid`` 且未禁用的模组。"""
        return updatable_mods(mods)

    @staticmethod
    def check_updates(
        mods: Sequence[Dict],
        game_version: str,
        mod_loader: Optional[str],
        workers: int = UPDATE_CHECK_WORKERS,
        on_progress: Optional[Callable[[int, int], None]] = None,
        on_update: Optional[Callable[[str, Dict], None]] = None,
    ) -> int:
        """客户端更新检查（并发 8），返回发现更新的模组数。"""
        return check_updates(mods, game_version, mod_loader, workers, on_progress, on_update)

    @staticmethod
    def collect_updates(
        mods: Sequence[Dict],
        game_version: str,
        mod_loader: Optional[str],
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Dict[str, Dict]:
        """服务端更新检查（串行），返回 ``{modid: {...}}``。"""
        return collect_updates(mods, game_version, mod_loader, on_progress)

    @staticmethod
    def batch_update_mods(
        modids: Sequence[str],
        update_info: Dict[str, Dict],
        game_version: str,
        mod_loader: Optional[str],
        workers: int = BATCH_UPDATE_WORKERS,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[int, int]:
        """客户端批量更新（并发 8），返回 ``(成功, 失败)``。"""
        return batch_update_mods(modids, update_info, game_version, mod_loader, workers, on_progress)

    @staticmethod
    def server_batch_update_mods(
        modids: Sequence[str],
        mods_dir: Any,
        game_version: str,
        mod_loader: Optional[str],
        workers: int = SERVER_BATCH_UPDATE_WORKERS,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> Tuple[int, int]:
        """服务端批量更新（并发 4），返回 ``(成功, 失败)``。"""
        return server_batch_update_mods(modids, mods_dir, game_version, mod_loader, workers, on_progress)

    # ─── 仅客户端模组 ───────────────────────────────────────

    @staticmethod
    def filter_client_mods(mods_dir: str, use_online: bool = True, dry_run: bool = False, progress_callback=None):
        """识别并禁用纯客户端模组（规则在 ``launcher/mod_classifier/``）。"""
        return filter_client_mods(mods_dir, use_online, dry_run, progress_callback)

    # ─── 缩略图（状态与线程池都在界面侧，这里只有纯函数）─────

    @staticmethod
    def thumbnail_key(path: Any) -> str:
        """缩略图去重键。"""
        return thumbnail_key(path)

    @staticmethod
    def claim_thumbnail(inflight: Set[str], path: Any) -> Optional[str]:
        """在途去重：``None`` 表示已在途。"""
        return claim_thumbnail(inflight, path)

    @staticmethod
    def release_thumbnail(inflight: Set[str], key: str) -> None:
        """摘掉在途标记。"""
        release_thumbnail(inflight, key)

    @staticmethod
    def thumbnail_is_current(generation: int, current_generation: int) -> bool:
        """代际校验。"""
        return thumbnail_is_current(generation, current_generation)

    @staticmethod
    def thumbnail_candidates(items: Sequence[Dict], resource_type: str) -> List[Dict]:
        """需要异步取缩略图的条目。"""
        return thumbnail_candidates(items, resource_type)

    @staticmethod
    def load_thumbnail(zip_path: Path, max_size: int) -> Optional[str]:
        """从 zip 取预览图（base64）；由 ``TaskRunner`` 的 worker 调用。"""
        return load_thumbnail(zip_path, max_size)
