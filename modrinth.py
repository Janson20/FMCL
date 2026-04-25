"""Modrinth API 集成模块

提供模组搜索、版本查询和下载功能，基于 Modrinth V2 API。
API 文档: https://docs.modrinth.com/api/

功能:
- 搜索模组（支持关键词 + 游戏版本 + 加载器筛选）
- 获取模组详情
- 获取模组的特定版本文件
- 下载模组 jar 文件到 mods 目录
"""

from typing import List, Dict, Optional, Set, Tuple
from pathlib import Path

import requests
import urllib3
from logzero import logger

# 禁用 SSL 证书验证警告
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


MODRINTH_API_BASE = "https://api.modrinth.com/v2"
MODRINTH_USER_AGENT = "FMCL-MinecraftLauncher/1.0 (github.com/Janson20/FMCL)"

# 缓存：旧格式版本号 {major: {minor1, minor2, ...}}
# 如 {16: {0,1,2,3,4,5}, 20: {0,1,2,3,4,5,6}, 21: {0,1,2,...,11}}
_legacy_versions_cache: Optional[Dict[int, Set[int]]] = None

# 缓存：新格式版本号 (YY.D.H) {yy: {d1, d2, ...}}
# 如 {26: {1, 2}, 27: {1}}
# 每个热修复版本 {yy: {d: {h1, h2, ...}}}
_new_versions_cache: Optional[Dict[int, Dict[int, Set[int]]]] = None


def _get_headers() -> Dict[str, str]:
    """获取 API 请求头"""
    return {"User-Agent": MODRINTH_USER_AGENT}


def search_mods(
    query: str = "",
    game_version: Optional[str] = None,
    mod_loader: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
) -> Dict:
    """
    搜索 Modrinth 上的模组

    Args:
        query: 搜索关键词，为空时返回热门模组
        game_version: 游戏版本筛选 (如 "1.20.4")
        mod_loader: 模组加载器筛选 ("fabric", "forge", "neoforge")
        offset: 分页偏移量
        limit: 每页数量 (最大 100)

    Returns:
        {
            "hits": [模组信息列表],
            "offset": 当前偏移,
            "limit": 每页数量,
            "total_hits": 总结果数
        }
    """
    facets = []

    # 项目类型：仅模组
    facets.append(["project_type:mod"])

    # 游戏版本筛选
    if game_version:
        facets.append([f"versions:{game_version}"])

    # 加载器筛选
    if mod_loader:
        facets.append([f"categories:{mod_loader}"])

    params: Dict = {
        "offset": offset,
        "limit": limit,
        "index": "relevance",  # 排序方式：相关度
    }

    if facets:
        # facets 是嵌套数组的 JSON 字符串
        import json
        params["facets"] = json.dumps(facets)

    if query:
        params["query"] = query

    logger.debug(
        f"Modrinth 搜索参数: query='{query}', version={game_version}, "
        f"loader={mod_loader}, offset={offset}, facets={facets}"
    )

    try:
        resp = requests.get(
            f"{MODRINTH_API_BASE}/search",
            params=params,
            headers=_get_headers(),
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()

        logger.info(
            f"Modrinth 搜索: query='{query}', version={game_version}, "
            f"loader={mod_loader}, offset={offset}, 总结果={data.get('total_hits', 0)}"
        )
        return data

    except requests.exceptions.RequestException as e:
        logger.error(f"Modrinth 搜索失败: {e}")
        return {"hits": [], "offset": offset, "limit": limit, "total_hits": 0}


def get_mod_versions(
    project_id: str,
    game_version: Optional[str] = None,
    mod_loader: Optional[str] = None,
) -> List[Dict]:
    """
    获取模组的版本列表

    Args:
        project_id: Modrinth 项目 ID (如 "P7dR8mSH" for Fabric API)
        game_version: 游戏版本筛选
        mod_loader: 加载器筛选

    Returns:
        版本信息列表，每个版本包含 id, name, version_number, files 等
    """
    import json as _json

    params: Dict = {}

    if game_version:
        params["game_versions"] = _json.dumps([game_version])

    if mod_loader:
        params["loaders"] = _json.dumps([mod_loader])

    try:
        resp = requests.get(
            f"{MODRINTH_API_BASE}/project/{project_id}/version",
            params=params,
            headers=_get_headers(),
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()
        versions = resp.json()

        logger.info(
            f"获取模组 {project_id} 版本列表: {len(versions)} 个 "
            f"(game_version={game_version}, loader={mod_loader})"
        )
        return versions

    except requests.exceptions.RequestException as e:
        logger.error(f"获取模组版本失败: {e}")
        return []


def download_mod(
    download_url: str,
    save_path: str,
    filename: str,
) -> Tuple[bool, str]:
    """
    下载模组文件

    Args:
        download_url: 下载 URL
        save_path: 保存目录路径
        filename: 文件名

    Returns:
        (是否成功, 保存路径或错误信息) 元组
    """
    try:
        save_dir = Path(save_path)
        save_dir.mkdir(parents=True, exist_ok=True)

        file_path = save_dir / filename

        # 如果文件已存在，跳过下载
        if file_path.exists():
            logger.info(f"模组文件已存在，跳过: {file_path}")
            return True, str(file_path)

        logger.info(f"开始下载模组: {filename}")
        resp = requests.get(
            download_url,
            headers=_get_headers(),
            timeout=60,
            stream=True,
            verify=False,
        )
        resp.raise_for_status()

        total_size = int(resp.headers.get("Content-Length", 0))
        downloaded = 0

        with open(str(file_path), "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

        logger.info(f"模组下载完成: {filename} ({downloaded}/{total_size} bytes)")
        return True, str(file_path)

    except Exception as e:
        logger.error(f"下载模组失败: {e}")
        return False, str(e)


def get_project_info(project_id: str) -> Optional[Dict]:
    """
    获取 Modrinth 项目基本信息

    Args:
        project_id: Modrinth 项目 ID

    Returns:
        项目信息字典，包含 title, id 等；失败返回 None
    """
    try:
        resp = requests.get(
            f"{MODRINTH_API_BASE}/project/{project_id}",
            headers=_get_headers(),
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.warning(f"获取项目信息失败 {project_id}: {e}")
        return None


def install_mod_with_deps(
    project_id: str,
    game_version: str,
    mod_loader: str,
    mods_dir: str,
    installed_files: Optional[set] = None,
    status_callback=None,
) -> Tuple[bool, str, List[str]]:
    """
    安装模组及其 required 依赖（递归）

    对于每个 required 依赖：
    - 如果依赖有指定 version_id，直接使用该版本
    - 否则按 game_version + mod_loader 查找兼容版本
    - 如果找不到兼容版本（如只有 Fabric 版本但需要 NeoForge），跳过该依赖
    - embedded 类型依赖已内嵌，不单独安装
    - incompatible 类型依赖跳过

    Args:
        project_id: Modrinth 项目 ID
        game_version: 游戏版本 (如 "1.20.6")
        mod_loader: 模组加载器 (如 "neoforge")
        mods_dir: mods 目录路径
        installed_files: 已安装/已处理的 project_id 集合（防止循环依赖）
        status_callback: 状态回调函数，接受 str 参数

    Returns:
        (是否成功, 结果消息, 安装的模组名称列表) 元组
    """
    if installed_files is None:
        installed_files = set()

    # 防止循环依赖
    if project_id in installed_files:
        return True, "已安装", []

    installed_files.add(project_id)

    installed_names: List[str] = []

    # 获取项目信息（用于显示名称）
    project_info = get_project_info(project_id)
    mod_title = project_info.get("title", project_id) if project_info else project_id

    # 获取兼容版本
    versions = get_mod_versions(
        project_id,
        game_version=game_version,
        mod_loader=mod_loader,
    )

    if not versions:
        logger.warning(f"模组 {mod_title} 没有兼容的版本 ({game_version}+{mod_loader})")
        return False, f"{mod_title} 没有兼容的版本", []

    version = versions[0]
    files = version.get("files", [])

    # 找主文件
    primary_file = None
    for f in files:
        if f.get("primary", False):
            primary_file = f
            break
    if not primary_file and files:
        primary_file = files[0]

    if not primary_file:
        return False, f"{mod_title} 没有可下载的文件", []

    download_url = primary_file.get("url", "")
    filename = primary_file.get("filename", f"{mod_title}.jar")

    if not download_url:
        return False, f"{mod_title} 下载链接无效", []

    # 先处理 required 依赖（递归安装）
    dependencies = version.get("dependencies", [])
    skipped_deps: List[str] = []

    for dep in dependencies:
        dep_type = dep.get("dependency_type", "")
        dep_project_id = dep.get("project_id")

        # embedded 依赖已内嵌，incompatible 依赖不应安装
        if dep_type in ("embedded", "incompatible"):
            continue

        # 只处理 required 依赖（optional 跳过）
        if dep_type != "required":
            continue

        if not dep_project_id:
            continue

        # 如果依赖已处理过，跳过
        if dep_project_id in installed_files:
            continue

        # 尝试安装依赖
        if status_callback:
            status_callback(f"正在安装依赖 {dep_project_id}...")

        dep_success, dep_msg, dep_names = install_mod_with_deps(
            dep_project_id,
            game_version=game_version,
            mod_loader=mod_loader,
            mods_dir=mods_dir,
            installed_files=installed_files,
            status_callback=status_callback,
        )

        if dep_success:
            installed_names.extend(dep_names)
        else:
            # 依赖安装失败（如没有兼容版本），记录跳过
            dep_info = get_project_info(dep_project_id)
            dep_name = dep_info.get("title", dep_project_id) if dep_info else dep_project_id
            skipped_deps.append(dep_name)
            logger.warning(
                f"跳过依赖 {dep_name}: {dep_msg}"
            )

    # 下载模组本身
    if status_callback:
        status_callback(f"正在下载 {filename}...")

    success, result = download_mod(download_url, mods_dir, filename)

    if success:
        installed_names.append(mod_title)
        msg = f"{mod_title} 安装成功"
        if skipped_deps:
            msg += f"（跳过不兼容依赖: {', '.join(skipped_deps)}）"
        return True, msg, installed_names
    else:
        return False, f"{mod_title} 安装失败: {result}", installed_names


def _is_new_version_format(version: str) -> bool:
    """
    判断是否为新版本命名格式 (YY.D 或 YY.D.H)

    新格式从 2026 年开始使用，版本号不以 "1." 开头。
    旧格式: 1.X, 1.X.Y (如 1.21, 1.21.1)
    新格式: YY.D, YY.D.H (如 26.1, 26.1.1)

    Args:
        version: 版本号字符串

    Returns:
        是否为新版本格式
    """
    if version.startswith("1."):
        return False
    parts = version.split(".")
    if len(parts) < 2:
        return False
    # 新格式第一部分是两位年份，第二部分是更新序号
    try:
        yy = int(parts[0])
        # 年份应在合理范围内 (26+)
        return yy >= 26
    except (ValueError, IndexError):
        return False


def _fetch_all_game_versions() -> Dict[int, Set[int]]:
    """
    从 Modrinth API 获取所有正式版游戏版本列表，按 major 分组

    同时支持旧格式 (1.X.Y) 和新格式 (YY.D.H)：
    - 旧格式返回 {major: {minor1, minor2, ...}} 如 {16: {0,1,2,3,4,5}}
    - 新格式缓存到 _new_versions_cache: {yy: {d: {h1, h2, ...}}} 如 {26: {1: {0,1}}}

    Returns:
        {major: {minor1, minor2, ...}} 字典（旧格式）
    """
    global _legacy_versions_cache, _new_versions_cache

    if _legacy_versions_cache is not None:
        return _legacy_versions_cache

    try:
        resp = requests.get(
            f"{MODRINTH_API_BASE}/tag/game_version",
            headers=_get_headers(),
            timeout=15,
            verify=False,
        )
        resp.raise_for_status()
        data = resp.json()

        # 只取 release 版本
        releases = [v["version"] for v in data if v.get("version_type") == "release"]

        legacy_cache: Dict[int, Set[int]] = {}
        new_cache: Dict[int, Dict[int, Set[int]]] = {}

        for v in releases:
            if _is_new_version_format(v):
                # 新格式: YY.D 或 YY.D.H
                parts = v.split(".")
                try:
                    yy = int(parts[0])
                    d = int(parts[1])
                    h = int(parts[2]) if len(parts) >= 3 else 0
                    new_cache.setdefault(yy, {}).setdefault(d, set()).add(h)
                except (ValueError, IndexError):
                    continue
            else:
                # 旧格式: 1.X 或 1.X.Y
                parts = v.split(".")
                try:
                    if len(parts) == 2 and parts[0] == "1":
                        major, minor = int(parts[1]), 0
                        legacy_cache.setdefault(major, set()).add(minor)
                    elif len(parts) >= 3 and parts[0] == "1":
                        major, minor = int(parts[1]), int(parts[2])
                        legacy_cache.setdefault(major, set()).add(minor)
                except (ValueError, IndexError):
                    continue

        _legacy_versions_cache = legacy_cache
        _new_versions_cache = new_cache
        logger.debug(
            f"已缓存游戏版本列表: {len(legacy_cache)} 个旧格式 major 版本, "
            f"{len(new_cache)} 个新格式年份版本"
        )
        return legacy_cache

    except requests.exceptions.RequestException as e:
        logger.warning(f"获取游戏版本列表失败，将使用回退逻辑: {e}")
        return {}


def parse_mod_loader_from_version(version_id: str) -> Optional[str]:
    """
    从版本 ID 中解析模组加载器类型

    Args:
        version_id: 版本 ID (如 "1.20.4-forge-49.0.26", "fabric-loader-0.15.11-1.20.4")

    Returns:
        加载器类型 ("fabric", "forge", "neoforge") 或 None
    """
    version_lower = version_id.lower()

    if "fabric" in version_lower:
        return "fabric"
    elif "neoforge" in version_lower:
        return "neoforge"
    elif "forge" in version_lower:
        return "forge"

    return None


def parse_game_version_from_version(version_id: str) -> Optional[str]:
    """
    从版本 ID 中提取游戏版本号

    支持的格式:
    - Forge: 1.20.4-forge-49.0.26
    - Fabric: fabric-loader-0.15.11-1.20.4
    - NeoForge: neoforge-20.6.139 (loader version 中 20.6 → 1.20.6)
    - NeoForge (带前缀): 1.20.6-neoforge-20.6.139
    - 新格式: 26.1-forge-xxx, 26.1.1-fabric-xxx, 26.1-snapshot-1, 26.1-pre-1, 26.1-rc-1

    新版本命名规则 (2026年起):
    - 主要更新: YY.D (如 26.1)
    - 热修复: YY.D.H (如 26.1.1)
    - 快照: YY.D-snapshot-N (如 26.1-snapshot-1)
    - 预发布版: YY.D-pre-N (如 26.1-pre-1)
    - 发布候选: YY.D-rc-N (如 26.1-rc-1)

    Args:
        version_id: 版本 ID

    Returns:
        游戏版本号 (如 "1.20.4", "26.1", "26.1.1") 或 None
    """
    import re

    version_lower = version_id.lower()

    # ── 新格式版本处理 ──
    # 新格式: YY.D 或 YY.D.H，后面可能跟 -forge-xxx, -fabric-xxx, -snapshot-N 等
    # 匹配 YY.D.H- 或 YY.D- (YY >= 26)
    new_format_match = re.match(r"^(\d{2,})\.(\d+)(?:\.(\d+))?", version_id)
    if new_format_match:
        yy_str = new_format_match.group(1)
        d_str = new_format_match.group(2)
        h_str = new_format_match.group(3)
        try:
            yy = int(yy_str)
            if yy >= 26:  # 新格式年份从 26 开始
                d = int(d_str)
                if h_str is not None:
                    return f"{yy}.{d}.{h_str}"
                else:
                    return f"{yy}.{d}"
        except ValueError:
            pass

    # NeoForge 特殊处理：版本 ID 可能是 neoforge-{loader_version}，没有 MC 版本前缀
    # loader_version 格式如 20.6.139，前两部分 20.6 对应 MC 1.20.6
    if "neoforge" in version_lower:
        # 优先从 loader version 推算（标准格式 neoforge-{major}.{minor}.{patch}）
        # 这样可以正确处理 neoforge-21.0.167 → 1.21, neoforge-20.6.139 → 1.20.6
        neoforge_match = re.search(r"neoforge-(\d+)\.(\d+)\.(\d+)", version_lower)
        if neoforge_match:
            major = neoforge_match.group(1)
            minor = neoforge_match.group(2)
            # minor 为 0 时，MC 版本是 1.{major}（如 21.0.x → 1.21）
            # 否则是 1.{major}.{minor}（如 20.6.x → 1.20.6）
            if minor == "0":
                return f"1.{major}"
            else:
                return f"1.{major}.{minor}"

        # 回退：尝试标准格式 {mc_version}-neoforge-{loader_version}
        # 优先尝试新格式 (YY.D.H，YY >= 26)
        new_match = re.search(r"(\d{2,}\.\d+(?:\.\d+)*)", version_id)
        if new_match:
            candidate = new_match.group(1)
            parts = candidate.split(".")
            if len(parts) >= 2 and int(parts[0]) >= 26:
                return candidate
        # 回退旧格式 (1.X.Y)
        match = re.search(r"(1\.\d+(?:\.\d+)*)", version_id)
        if match:
            return match.group(1)

        return None

    # Fabric 格式: fabric-loader-0.15.11-1.20.4 或 fabric-loader-0.16.9-1.21.4
    # 也支持新格式: fabric-loader-0.16.0-26.1.1
    # 游戏版本在最后一个版本号位置
    if "fabric" in version_lower:
        # 优先尝试新格式 (YY.D.H，YY >= 26)
        new_matches = re.findall(r"(\d{2,}\.\d+(?:\.\d+)*)", version_id)
        if new_matches:
            candidate = new_matches[-1]
            parts = candidate.split(".")
            if len(parts) >= 2 and int(parts[0]) >= 26:
                return candidate
        # 回退旧格式 (1.X.Y)
        matches = re.findall(r"(1\.\d+(?:\.\d+)*)", version_id)
        if matches:
            return matches[-1]

    # Forge 格式: 1.20.4-forge-49.0.26 或 26.1-forge-1.0.0
    # 新格式以版本号开头时已在上方第 524 行处理，此处为回退
    # 优先尝试新格式 (YY.D.H，YY >= 26)
    new_match = re.search(r"(\d{2,}\.\d+(?:\.\d+)*)", version_id)
    if new_match:
        candidate = new_match.group(1)
        parts = candidate.split(".")
        if len(parts) >= 2 and int(parts[0]) >= 26:
            return candidate
    # 回退旧格式 (1.X.Y)
    match = re.search(r"(1\.\d+(?:\.\d+)*)", version_id)
    if match:
        return match.group(1)

    return None


def compress_game_versions(versions: List[str]) -> str:
    """
    将游戏版本列表压缩为简洁的展示字符串

    规则 (基于 Modrinth 完整版本列表判断是否覆盖全版本):

    旧格式 (1.X.Y):
    - 同一 major 下覆盖了所有 minor 版本 → 1.X.x
      如 1.16 有 0~5，模组支持全部 → "1.16.x"
    - 同一 major 下连续但不完整 → 用范围
      如 1.20 有 0~6，模组只支持 0,1,2 → "1.20-1.20.2"
    - 孤立版本原样显示，如 [1.21] → "1.21"

    新格式 (YY.D.H):
    - 同一 YY.D 下覆盖了所有 H 版本 → YY.D.x
      如 26.1 有 0~3，模组支持全部 → "26.1.x"
    - 同一 YY.D 下连续但不完整 → 用范围
      如 26.1 有 0~3，模组只支持 0,1 → "26.1-26.1.1"
    - 孤立版本原样显示，如 26.1 → "26.1"
    - 连续的 YY.D.x 可合并为范围：26.1.x-26.3.x

    Args:
        versions: 游戏版本号列表 (如 ["1.16", "1.16.1", ..., "1.21", "26.1", "26.1.1"])

    Returns:
        压缩后的版本展示字符串
    """
    if not versions:
        return ""

    # 获取 Modrinth 完整版本列表作为参考
    all_legacy_versions = _fetch_all_game_versions()

    # 解析旧格式版本号为 (major, minor) 元组，1.X → (X, 0), 1.X.Y → (X, Y)
    def _parse_legacy(v: str):
        parts = v.split(".")
        try:
            if len(parts) == 2 and parts[0] == "1":
                return (int(parts[1]), 0)
            elif len(parts) >= 3 and parts[0] == "1":
                return (int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            pass
        return None

    # 解析新格式版本号为 (yy, d, h) 元组，YY.D → (YY, D, 0), YY.D.H → (YY, D, H)
    def _parse_new(v: str):
        if not _is_new_version_format(v):
            return None
        parts = v.split(".")
        try:
            yy = int(parts[0])
            d = int(parts[1])
            h = int(parts[2]) if len(parts) >= 3 else 0
            return (yy, d, h)
        except (ValueError, IndexError):
            return None

    # 分离旧格式和新格式版本
    legacy_versions = []
    new_versions = []
    for v in versions:
        if _is_new_version_format(v):
            new_versions.append(v)
        else:
            legacy_versions.append(v)

    parts = []

    # ── 处理旧格式版本 ──
    legacy_groups: Dict[int, List[int]] = {}  # major -> [minor, ...]
    for v in legacy_versions:
        parsed = _parse_legacy(v)
        if parsed is None:
            continue
        major, minor = parsed
        legacy_groups.setdefault(major, []).append(minor)

    for major in sorted(legacy_groups.keys()):
        minor_vals = sorted(set(legacy_groups[major]))
        all_minors = all_legacy_versions.get(major, set())

        # 找连续段
        segments: List[List[int]] = []
        current_seg = [minor_vals[0]]
        for i in range(1, len(minor_vals)):
            if minor_vals[i] == minor_vals[i - 1] + 1:
                current_seg.append(minor_vals[i])
            else:
                segments.append(current_seg)
                current_seg = [minor_vals[i]]
        segments.append(current_seg)

        for seg in segments:
            prefix = f"1.{major}"
            if len(seg) == 1:
                if seg[0] == 0:
                    parts.append(prefix)
                else:
                    parts.append(f"{prefix}.{seg[0]}")
            elif all_minors and set(seg) == all_minors:
                parts.append(f"{prefix}.x")
            else:
                start = f"{prefix}.{seg[0]}" if seg[0] != 0 else prefix
                end = f"{prefix}.{seg[-1]}"
                parts.append(f"{start}-{end}")

    # 合并连续的旧格式 .x 结果：1.16.x, 1.17.x → 1.16.x-1.17.x
    merged: List[str] = []
    i = 0
    while i < len(parts):
        if parts[i].endswith(".x") and parts[i].startswith("1."):
            start_major = int(parts[i].rstrip(".x").split(".")[-1])
            end_major = start_major
            j = i + 1
            while j < len(parts) and parts[j].endswith(".x") and parts[j].startswith("1."):
                next_major = int(parts[j].rstrip(".x").split(".")[-1])
                if next_major == end_major + 1:
                    end_major = next_major
                    j += 1
                else:
                    break
            if end_major > start_major:
                merged.append(f"1.{start_major}.x-1.{end_major}.x")
            else:
                merged.append(parts[i])
            i = j
        else:
            merged.append(parts[i])
            i += 1

    # ── 处理新格式版本 ──
    new_groups: Dict[int, Dict[int, List[int]]] = {}  # yy -> {d: [h, ...]}
    for v in new_versions:
        parsed = _parse_new(v)
        if parsed is None:
            continue
        yy, d, h = parsed
        new_groups.setdefault(yy, {}).setdefault(d, []).append(h)

    new_parts = []
    for yy in sorted(new_groups.keys()):
        d_vals = sorted(new_groups[yy].keys())

        for d in d_vals:
            h_vals = sorted(set(new_groups[yy][d]))
            # 该 YY.D 下所有已知的 H 版本
            all_h: Set[int] = set()
            if _new_versions_cache and yy in _new_versions_cache and d in _new_versions_cache[yy]:
                all_h = _new_versions_cache[yy][d]

            if len(h_vals) == 1:
                if h_vals[0] == 0:
                    new_parts.append(f"{yy}.{d}")
                else:
                    new_parts.append(f"{yy}.{d}.{h_vals[0]}")
            elif all_h and set(h_vals) == all_h:
                new_parts.append(f"{yy}.{d}.x")
            else:
                # 连续热修复版本用范围
                start = f"{yy}.{d}.{h_vals[0]}" if h_vals[0] != 0 else f"{yy}.{d}"
                end = f"{yy}.{d}.{h_vals[-1]}"
                new_parts.append(f"{start}-{end}")

    # 合并连续的新格式 YY.D.x 结果：26.1.x, 26.2.x → 26.1.x-26.2.x
    new_merged: List[str] = []
    i = 0
    while i < len(new_parts):
        item = new_parts[i]
        # 检查是否为新格式 .x 结尾
        if item.endswith(".x") and not item.startswith("1."):
            dot_parts = item.rstrip(".x").split(".")
            if len(dot_parts) == 2:
                try:
                    start_yy, start_d = int(dot_parts[0]), int(dot_parts[1])
                    end_yy, end_d = start_yy, start_d
                    j = i + 1
                    while j < len(new_parts):
                        next_item = new_parts[j]
                        if next_item.endswith(".x") and not next_item.startswith("1."):
                            next_dot_parts = next_item.rstrip(".x").split(".")
                            if len(next_dot_parts) == 2:
                                next_yy, next_d = int(next_dot_parts[0]), int(next_dot_parts[1])
                                if next_yy == end_yy and next_d == end_d + 1:
                                    end_yy, end_d = next_yy, next_d
                                    j += 1
                                    continue
                        break
                    if end_d > start_d or end_yy > start_yy:
                        new_merged.append(f"{start_yy}.{start_d}.x-{end_yy}.{end_d}.x")
                    else:
                        new_merged.append(item)
                    i = j
                except (ValueError, IndexError):
                    new_merged.append(item)
                    i += 1
            else:
                new_merged.append(item)
                i += 1
        else:
            new_merged.append(item)
            i += 1

    # 合并旧格式和新格式结果
    result = merged + new_merged
    return ", ".join(result)
