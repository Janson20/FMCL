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
from logzero import logger


MODRINTH_API_BASE = "https://api.modrinth.com/v2"
MODRINTH_USER_AGENT = "MCL-MinecraftLauncher/1.0 (github.com/Janson20/MCL)"

# 缓存：每个 major 版本对应的所有 minor 版本号集合
# 如 {16: {0,1,2,3,4,5}, 20: {0,1,2,3,4,5,6}, 21: {0,1,2,...,11}}
_all_game_versions_cache: Optional[Dict[int, Set[int]]] = None


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


def _fetch_all_game_versions() -> Dict[int, Set[int]]:
    """
    从 Modrinth API 获取所有正式版游戏版本列表，按 major 分组

    Returns:
        {major: {minor1, minor2, ...}} 字典
        如 {16: {0,1,2,3,4,5}, 20: {0,1,2,3,4,5,6}}
    """
    global _all_game_versions_cache

    if _all_game_versions_cache is not None:
        return _all_game_versions_cache

    try:
        resp = requests.get(
            f"{MODRINTH_API_BASE}/tag/game_version",
            headers=_get_headers(),
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        # 只取 release 版本
        releases = [v["version"] for v in data if v.get("version_type") == "release"]

        cache: Dict[int, Set[int]] = {}
        for v in releases:
            parts = v.split(".")
            try:
                if len(parts) == 2 and parts[0] == "1":
                    major, minor = int(parts[1]), 0
                    cache.setdefault(major, set()).add(minor)
                elif len(parts) >= 3 and parts[0] == "1":
                    major, minor = int(parts[1]), int(parts[2])
                    cache.setdefault(major, set()).add(minor)
            except (ValueError, IndexError):
                continue

        _all_game_versions_cache = cache
        logger.debug(f"已缓存游戏版本列表: {len(cache)} 个 major 版本")
        return cache

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

    Args:
        version_id: 版本 ID

    Returns:
        游戏版本号 (如 "1.20.4") 或 None
    """
    import re

    version_lower = version_id.lower()

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
        match = re.search(r"(1\.\d+(?:\.\d+)*)", version_id)
        if match:
            return match.group(1)

        return None

    # Fabric 格式: fabric-loader-0.15.11-1.20.4 或 fabric-loader-0.16.9-1.21.4
    # 游戏版本在最后一个 1.X.X 位置
    if "fabric" in version_lower:
        matches = re.findall(r"(1\.\d+(?:\.\d+)*)", version_id)
        if matches:
            return matches[-1]

    # Forge 格式: 1.20.4-forge-49.0.26
    # 游戏版本在第一个 1.X.X 位置
    match = re.search(r"(1\.\d+(?:\.\d+)*)", version_id)
    if match:
        return match.group(1)

    return None


def compress_game_versions(versions: List[str]) -> str:
    """
    将游戏版本列表压缩为简洁的展示字符串

    规则 (基于 Modrinth 完整版本列表判断是否覆盖全版本):
    - 同一 major 下覆盖了所有 minor 版本 → 1.X.x
      如 1.16 有 0~5，模组支持全部 → "1.16.x"
    - 同一 major 下连续但不完整 → 用范围
      如 1.20 有 0~6，模组只支持 0,1,2 → "1.20-1.20.2"
    - 孤立版本原样显示，如 [1.21] → "1.21"

    Args:
        versions: 游戏版本号列表 (如 ["1.16", "1.16.1", ..., "1.21"])

    Returns:
        压缩后的版本展示字符串
    """
    if not versions:
        return ""

    # 获取 Modrinth 完整版本列表作为参考
    all_versions = _fetch_all_game_versions()

    # 解析版本号为 (major, minor) 元组，1.X → (X, 0), 1.X.Y → (X, Y)
    def _parse(v: str):
        parts = v.split(".")
        try:
            if len(parts) == 2 and parts[0] == "1":
                return (int(parts[1]), 0)
            elif len(parts) >= 3 and parts[0] == "1":
                return (int(parts[1]), int(parts[2]))
        except (ValueError, IndexError):
            pass
        return None

    # 按 major 分组
    groups: Dict[int, List[int]] = {}  # major -> [minor, ...]
    for v in versions:
        parsed = _parse(v)
        if parsed is None:
            continue
        major, minor = parsed
        groups.setdefault(major, []).append(minor)

    # 对每个 major 组生成展示文本
    parts = []
    for major in sorted(groups.keys()):
        minor_vals = sorted(set(groups[major]))
        # 该 major 在 Modrinth 上所有已知的 minor 版本
        all_minors = all_versions.get(major, set())

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
                # 覆盖了该 major 下所有已知 minor 版本 → 用 .x
                parts.append(f"{prefix}.x")
            else:
                # 连续但不完整，用范围
                start = f"{prefix}.{seg[0]}" if seg[0] != 0 else prefix
                end = f"{prefix}.{seg[-1]}"
                parts.append(f"{start}-{end}")

    # 合并连续的 .x 结果：1.16.x, 1.17.x, 1.18.x, 1.19.x → 1.16.x-1.19.x
    merged: List[str] = []
    i = 0
    while i < len(parts):
        if parts[i].endswith(".x"):
            start_major = int(parts[i].rstrip(".x").split(".")[-1])
            end_major = start_major
            j = i + 1
            while j < len(parts) and parts[j].endswith(".x"):
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

    return ", ".join(merged)
