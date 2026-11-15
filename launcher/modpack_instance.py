"""自定义实例的版本 JSON 落盘 —— 整合包安装共用的那一份实现。

## 为什么需要它

HMCL / MCBBS / CurseForge / 通用压缩包这几条安装路径都把 overrides 解到
``versions/<实例名>/``（``mods``、``config``、``resourcepacks`` …），却**从不写
``versions/<实例名>/<实例名>.json``**。于是同一个目录在三个地方都"不存在"：

* **已安装版本**：``find_version_json_in()`` 找不到 → 该行是 ``state="error"``，
  版本号/加载器一律显示不出来；
* **启动**：核心拿不到版本 JSON，直接报"版本 JSON 不存在"（即使资源都齐了）；
* **我的资源**：``has_mod_loader_from_json()`` 判不出加载器 → 只给「资源包 / 地图」
  两个页签，模组目录还会被解析到**全局** ``.minecraft/mods``（隔离目录里的模组看不见）。

``install_mrpack()`` 的自定义实例分支（2026-10-23）早就做了这件事，但它是**内联**
的，另外四个安装器都没跟上。这里把它抽成一份实现：复制来源版本的 JSON、改写 ``id``、
必要时把 client jar 放进实例目录 —— 五个安装器共用。

## 关于 ``jar`` 字段（容易写错的地方）

``minecraft_launcher_lib.command.get_libraries()`` 把 ``jar`` 当作**版本 ID**：
classpath 里拼的是 ``versions/<jar>/<jar>.jar``，**不是文件名**。所以不能把
``jar`` 写成 ``<实例名>.jar``（那会指向 ``versions/<实例名>.jar/`` 这个根本不存在的
版本，JVM 忽略该条目后原版类全部缺失）。本模块保证 ``jar`` 要么指向真实存在的 jar，
要么指向实例自己刚复制出来的 ``versions/<实例名>/<实例名>.jar``。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from logzero import logger

from version_utils import find_version_json_in, instance_targets_from_json

#: 继承链最大回溯深度（与 ``parse_instance_from_json`` 同值：Forge → 原版够用）。
MAX_INHERIT_DEPTH: int = 10

#: 实例图标文件名（官方 JSON 布局里与版本 JSON 同级）。
ICON_NAME: str = "icon.png"


def materialize_instance_version_json(
    minecraft_dir: Any, instance_name: str, source_version_id: str = "", *, source_json: Optional[Path] = None
) -> Optional[Path]:
    """用来源版本的 JSON 生成 ``versions/<实例名>/<实例名>.json``。

    来源可以是 ``versions/<id>/`` 里的一个已安装版本（``source_version_id``），也可以是
    一个**明确给出的 JSON 路径**（``source_json``，通用压缩包把整棵 ``.minecraft``
    解到实例目录里时用）。

    Args:
        minecraft_dir: ``.minecraft`` 目录路径
        instance_name: 实例名（= 版本 ID = 目录名）
        source_version_id: 来源版本 ID（加载器版本或原版版本）
        source_json: 直接指定来源 JSON（优先于 ``source_version_id``）

    Returns:
        实例版本 JSON 的路径；**目标已存在时原样返回**（不覆盖，见下）。
        来源拿不到、JSON 读不出、写盘失败时返回 ``None``（**不抛异常**：
        安装器各自已经有自己的失败清理与提示，不该被这里打断）。

    Note:
        目标 JSON 已存在时**一个字节都不改**：那可能是用户手工修好的，也可能是别的
        入口（例如 ``rename_instance()``）刚写的一份；安装流程没有资格覆盖它。
    """
    target_id = str(instance_name or "").strip()
    if not target_id:
        logger.warning("整合包实例名为空，跳过版本 JSON 落盘")
        return None

    root = Path(str(minecraft_dir))
    versions_dir = root / "versions"
    target_dir = versions_dir / target_id
    target_json = target_dir / f"{target_id}.json"

    try:
        if target_json.is_file():
            logger.info(f"实例版本 JSON 已存在，保持原样: {target_json}")
            return target_json
    except OSError as e:
        logger.warning(f"检查实例版本 JSON 失败 ({target_json}): {e}")
        return None

    src = (
        Path(source_json)
        if source_json is not None
        else find_version_json_in(versions_dir, str(source_version_id or "").strip())
    )
    if src is None or not src.is_file():
        logger.warning(
            f"找不到来源版本的 JSON，无法为实例 {target_id} 落盘"
            f"（来源版本={source_version_id!r} / 来源 JSON={source_json}）"
        )
        return None

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 - 坏 JSON 只影响这一个实例
        logger.warning(f"读取来源版本 JSON 失败 ({src}): {e}")
        return None
    if not isinstance(data, dict):
        logger.warning(f"来源版本 JSON 不是对象，跳过落盘: {src}")
        return None

    data["id"] = target_id
    _ensure_instance_jar(versions_dir, target_dir, target_id, data, Path(src).parent)

    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        target_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning(f"写入实例版本 JSON 失败 ({target_json}): {e}")
        return None

    _copy_icon(Path(src).parent, target_dir)
    logger.info(f"已为实例 {target_id} 落盘版本 JSON（来源 {Path(src).parent.name}）")
    return target_json


def find_installed_loader_version_id(
    minecraft_dir: Any, mc_version: str, loader_type: str, loader_version: str = ""
) -> Optional[str]:
    """在 ``versions/`` 里找出**刚装好的那个加载器版本**的目录名。

    为什么不猜 ``minecraft_launcher_lib`` 的命名规则：forge / neoforge / fabric /
    quilt 四种加载器的版本 ID 拼法各不相同，而且会随库版本变化；猜错就会把实例挂到
    一个不存在的版本上（那正是 CurseForge 路径原来 ``<实例>-<加载器>-<版本>`` 的下场）。
    这里直接读每个目录的 JSON，用与版本列表**同一套**解析取 ``(游戏版本, 加载器)``
    来比对（:func:`version_utils.instance_targets_from_json`）。

    Args:
        minecraft_dir: ``.minecraft`` 目录路径
        mc_version: 目标 Minecraft 版本（空串 = 不按游戏版本筛）
        loader_type: 加载器类型（``forge`` / ``neoforge`` / ``fabric`` / ``quilt`` …）
        loader_version: 加载器版本号；版本 ID 里含它的目录优先

    Returns:
        版本 ID（目录名）；没有匹配的返回 ``None``。
    """
    want_loader = str(loader_type or "").strip().lower()
    if not want_loader:
        return None
    want_game = str(mc_version or "").strip()
    want_loader_version = str(loader_version or "").strip()

    root = Path(str(minecraft_dir))
    try:
        entries = sorted(p for p in (root / "versions").iterdir() if p.is_dir())
    except OSError as e:
        logger.warning(f"列举版本目录失败 ({root / 'versions'}): {e}")
        return None

    best: Optional[str] = None
    for entry in entries:
        try:
            game_version, loader = instance_targets_from_json(entry.name, str(root))
        except Exception as e:  # noqa: BLE001 - 单个目录读坏了不影响其它候选
            logger.debug(f"解析版本失败，跳过 ({entry.name}): {e}")
            continue
        if str(loader or "").lower() != want_loader:
            continue
        if want_game and game_version != want_game:
            continue
        if want_loader_version and want_loader_version in entry.name:
            return entry.name
        if best is None:
            best = entry.name
    return best


def resolve_instance_source(
    minecraft_dir: Any, mc_version: str, loader_type: str = "", loader_version: str = ""
) -> str:
    """实例 JSON 该从哪个版本复制：**装过加载器就用加载器版本，否则用原版版本**。

    加载器版本目录名由 ``minecraft_launcher_lib`` 决定（四种加载器各不一样），所以走
    :func:`find_installed_loader_version_id` 反查；查不到就退回原版版本 —— 宁可实例
    以原版身份可用，也不要挂到一个不存在的版本 ID 上。

    Args:
        minecraft_dir: ``.minecraft`` 目录路径
        mc_version: 目标 Minecraft 版本
        loader_type: 加载器类型（空串 = 原版）
        loader_version: 加载器版本号

    Returns:
        来源版本 ID（**非空**：查不到时就是 ``mc_version``）。
    """
    base = str(mc_version or "").strip()
    want_loader = str(loader_type or "").strip().lower()
    if want_loader:
        found = find_installed_loader_version_id(minecraft_dir, base, want_loader, loader_version)
        if found:
            logger.info(f"实例将继承加载器版本: {found}")
            return found
        logger.warning(
            f"未在 versions/ 里找到 {want_loader} {loader_version}（MC {base}），" f"实例改继承原版版本 {base}"
        )
    return base


def _ensure_instance_jar(
    versions_dir: Path, target_dir: Path, target_id: str, data: Dict[str, Any], source_dir: Path
) -> None:
    """让实例的 classpath 落到一个**真实存在**的 client jar 上（就地改 ``data``）。

    顺序：

    1. ``jar`` 已经指向存在的 jar → 原样保留（例如 Forge 的 ``"jar": "1.20.1"``）；
    2. 来源目录里就有 jar → 复制成 ``<实例名>.jar``，``jar`` 指到实例自己
       （实例从此自包含：删掉来源版本也能启动）；
    3. 都没有 → 顺着 ``inheritsFrom`` 找第一个有 jar 的父版本，``jar`` 指过去;
    4. 还找不到 → 不动 ``jar``（启动时核心的 ``_ensure_version_jar`` 还有一道兜底）。
    """
    jar_id = str(data.get("jar") or "").strip()
    if jar_id:
        try:
            if (versions_dir / jar_id / f"{jar_id}.jar").is_file():
                return
        except OSError:
            pass

    source_jar = _source_jar(source_dir)
    if source_jar is not None:
        target_jar = target_dir / f"{target_id}.jar"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            if not target_jar.is_file():
                shutil.copy2(str(source_jar), str(target_jar))
            data["jar"] = target_id
            return
        except OSError as e:
            logger.warning(f"复制实例 JAR 失败 ({source_jar} → {target_jar}): {e}")

    inherited = _jar_version_in_inherit_chain(versions_dir, data)
    if inherited:
        data["jar"] = inherited


def _source_jar(source_dir: Path) -> Optional[Path]:
    """来源版本目录里的 client jar：先认同名文件，再认目录里唯一的 jar。"""
    direct = source_dir / f"{source_dir.name}.jar"
    try:
        if direct.is_file():
            return direct
        jars = sorted(source_dir.glob("*.jar"))
    except OSError:
        return None
    return jars[0] if len(jars) == 1 else None


def _jar_version_in_inherit_chain(versions_dir: Path, data: Dict[str, Any]) -> Optional[str]:
    """沿 ``inheritsFrom`` 找出第一个**有 jar 文件**的版本 ID。"""
    seen: set[str] = set()
    current = str(data.get("inheritsFrom") or "").strip()
    depth = 0
    while current and current not in seen and depth < MAX_INHERIT_DEPTH:
        seen.add(current)
        try:
            if (versions_dir / current / f"{current}.jar").is_file():
                return current
        except OSError:
            return None
        parent_json = find_version_json_in(versions_dir, current)
        if parent_json is None:
            return None
        try:
            parent = json.loads(parent_json.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - 父 JSON 坏了就到此为止
            return None
        if not isinstance(parent, dict):
            return None
        current = str(parent.get("inheritsFrom") or "").strip()
        depth += 1
    return None


def _copy_icon(source_dir: Path, target_dir: Path) -> None:
    """把来源版本的 ``icon.png`` 抄一份（目标已有就不动）。"""
    icon = source_dir / ICON_NAME
    target = target_dir / ICON_NAME
    try:
        if icon.is_file() and not target.is_file():
            target_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(icon), str(target))
    except OSError as e:
        logger.debug(f"复制实例图标失败 ({icon}): {e}")


__all__ = ["find_installed_loader_version_id", "materialize_instance_version_json"]
