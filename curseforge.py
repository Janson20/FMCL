"""CurseForge API 集成模块

提供模组/整合包搜索、版本查询和下载功能，基于 CurseForge Core API v1。
API 文档: https://docs.curseforge.com/

参考 PCL-CE: ModComp.cs 中的 CurseForge 交互逻辑
  - CompLoaderType 枚举 (Forge=1, Fabric=4, Quilt=5, NeoForge=6)
  - CompFileStatus 枚举 (Release=1, Beta=2, Alpha=3)
  - classId 映射 (Mod=6, Modpack=4471, ResourcePack=12, Shader=6552)
  - 搜索参数: sortField=2 (Popularity), sortOrder=desc

功能:
- 搜索模组（支持关键词 + 游戏版本 + 加载器筛选）
- 获取模组详情
- 获取文件列表与下载 URL
- 下载模组/整合包/资源包/光影文件
"""

import hashlib
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests
from logzero import logger
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from structured_logger import slog
from version_utils import compare_versions, parse_semver

from download_config import DOWNLOAD_POOL_MAXSIZE, DOWNLOAD_POOL_SIZE

# ══════════════════════════════════════════════════════════════════════
# API 配置
# ══════════════════════════════════════════════════════════════════════

CURSEFORGE_API_BASE = "https://api.curseforge.com/v1"
CURSEFORGE_GAME_ID = 432  # Minecraft
CURSEFORGE_USER_AGENT = "FMCL-MinecraftLauncher/1.0 (github.com/Janson20/FMCL)"

# 从环境变量或构建时嵌入的 secrets 中读取 API Key
# （参考 PCL-CE: SecretDictionary 编译时注入机制）
# CurseForge 现已强制要求 API Key，未设置时将无法使用 CurseForge 功能
# 申请地址: https://console.curseforge.com/

# 1) 优先尝试构建时嵌入的 key（PyInstaller 打包时由 CI 生成 _build_secrets.py）
_BUILTIN_API_KEY = ""
try:
    from _build_secrets import BUILD_CURSEFORGE_API_KEY  # type: ignore

    _BUILTIN_API_KEY = BUILD_CURSEFORGE_API_KEY or ""
except (ImportError, ModuleNotFoundError, AttributeError):
    pass

# 2) 运行时环境变量可覆盖嵌入的 key（方便开发者调试）
CURSEFORGE_API_KEY = os.environ.get("CURSEFORGE_API_KEY", "") or _BUILTIN_API_KEY

# ══════════════════════════════════════════════════════════════════════
# PCL-CE 风格的类型映射
# ══════════════════════════════════════════════════════════════════════

# 模组加载器类型 → CurseForge API 值 (参考 PCL-CE: CompLoaderType)
LOADER_TYPE_MAP: Dict[int, str] = {0: None, 1: "forge", 4: "fabric", 5: "quilt", 6: "neoforge"}  # Any
LOADER_NAME_TO_ID: Dict[str, int] = {v: k for k, v in LOADER_TYPE_MAP.items() if v}

# classId 映射 (参考 PCL-CE: CompType → classId)
CLASS_ID_MAP: Dict[str, int] = {
    "mod": 6,
    "modpack": 4471,
    "resourcepack": 12,
    "shader": 6552,
    "world": 17,
    "datapack": 4546,
}

# 文件状态 (参考 PCL-CE: CompFileStatus)
FILE_STATUS_MAP: Dict[int, str] = {1: "release", 2: "beta", 3: "alpha"}

# 搜索排序 (参考 PCL-CE: CompSortType)
SORT_FIELD_MAP: Dict[str, int] = {
    "popularity": 2,  # Popularity (对应 Downloads=3)
    "downloads": 6,  # TotalDownloads
    "name": 4,  # Name
    "updated": 1,  # LastUpdated
}

# 重试配置
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 2
RETRY_STATUS_FORCELIST = [429, 500, 502, 503, 504]

# 共享 Session
_shared_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    """获取共享 Session（连接池 + 重试 + API Key 认证）"""
    global _shared_session
    if _shared_session is None:
        retry = Retry(
            total=MAX_RETRIES,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=RETRY_STATUS_FORCELIST,
            allowed_methods=["GET", "POST"],
        )
        adapter = HTTPAdapter(
            max_retries=retry, pool_connections=DOWNLOAD_POOL_SIZE, pool_maxsize=DOWNLOAD_POOL_MAXSIZE
        )
        _shared_session = requests.Session()
        _shared_session.mount("https://", adapter)
        _shared_session.mount("http://", adapter)

        # CurseForge 现已强制要求 API Key（参考 PCL-CE: SecretDictionary）
        # 申请地址: https://console.curseforge.com/
        if CURSEFORGE_API_KEY:
            _shared_session.headers["x-api-key"] = CURSEFORGE_API_KEY
        else:
            logger.warning(
                "未设置 CURSEFORGE_API_KEY，CurseForge 功能将不可用（申请: https://console.curseforge.com/）"
            )

    _shared_session.headers["User-Agent"] = CURSEFORGE_USER_AGENT
    return _shared_session


def is_configured() -> bool:
    """检查 CurseForge API Key 是否已配置（CurseForge 现已强制要求 API Key）"""
    return bool(CURSEFORGE_API_KEY)


# ══════════════════════════════════════════════════════════════════════
# 底层 HTTP 请求
# ══════════════════════════════════════════════════════════════════════


def _download_file(download_url: str, file_path: Path, timeout: int = 120) -> bool:
    """下载文件到指定路径（支持断点续传）"""
    headers = {}
    existing_size = 0
    if file_path.exists():
        existing_size = file_path.stat().st_size
        if existing_size > 0:
            headers["Range"] = f"bytes={existing_size}-"

    try:
        session = _get_session()
        resp = session.get(download_url, headers=headers, stream=True, timeout=timeout)

        if resp.status_code == 416:
            logger.debug(f"文件已完整下载，跳过: {file_path.name}")
            return True

        if resp.status_code not in (200, 206):
            logger.warning(f"下载失败 (HTTP {resp.status_code}): {download_url[:200]}")
            return False

        total = existing_size + int(resp.headers.get("Content-Length", 0))
        mode = "ab" if resp.status_code == 206 else "wb"
        downloaded = existing_size if mode == "ab" else 0

        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, mode) as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

        logger.debug(f"下载完成: {file_path.name} ({downloaded} bytes)")
        return True

    except requests.exceptions.RequestException as e:
        logger.warning(f"下载异常 ({download_url[:200]}): {e}")
        return False


# ══════════════════════════════════════════════════════════════════════
# 搜索
# ══════════════════════════════════════════════════════════════════════


def _build_search_facets(
    game_version: Optional[str] = None, mod_loader: Optional[str] = None, class_id: Optional[int] = None
) -> Dict[str, str]:
    """构建 CurseForge 搜索参数"""
    params: Dict[str, str] = {}
    params["gameId"] = str(CURSEFORGE_GAME_ID)

    if class_id:
        params["classId"] = str(class_id)

    if game_version:
        params["gameVersion"] = game_version

    if mod_loader:
        loader_id = LOADER_NAME_TO_ID.get(mod_loader)
        if loader_id:
            params["modLoaderType"] = str(loader_id)

    return params


def search_mods(
    query: str = "",
    game_version: Optional[str] = None,
    mod_loader: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
    sort: str = "popularity",
) -> Dict:
    """
    搜索 CurseForge 模组

    参考 PCL-CE: ModComp.CurseForgeSearch
    搜索参数: gameId=432, classId=6, sortField=2(Popularity)

    Args:
        query: 搜索关键词
        game_version: 游戏版本 (如 "1.20.4")
        mod_loader: 模组加载器 (如 "forge", "fabric", "neoforge")
        offset: 偏移量
        limit: 返回数量
        sort: 排序方式 ("popularity", "downloads", "name", "updated")

    Returns:
        {"data": [...], "pagination": {"totalCount": int, "index": int, "pageSize": int}}
    """
    params = _build_search_facets(game_version, mod_loader, CLASS_ID_MAP["mod"])
    params["searchFilter"] = query.strip()
    params["sortField"] = str(SORT_FIELD_MAP.get(sort, 2))
    params["sortOrder"] = "desc"
    params["index"] = str(offset)
    params["pageSize"] = str(min(limit, 50))

    try:
        session = _get_session()
        resp = session.get(f"{CURSEFORGE_API_BASE}/mods/search", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.warning(f"CurseForge 搜索失败: {e}")
        return {"data": [], "pagination": {"totalCount": 0, "index": offset, "pageSize": limit}}


def search_modpacks(query: str = "", game_version: Optional[str] = None, offset: int = 0, limit: int = 20) -> Dict:
    """搜索 CurseForge 整合包 (classId=4471)"""
    return search_mods(query, game_version, None, offset, limit, "popularity")


def search_resource_packs(
    query: str = "", game_version: Optional[str] = None, offset: int = 0, limit: int = 20
) -> Dict:
    """搜索 CurseForge 资源包 (classId=12)"""
    params = _build_search_facets(game_version, None, CLASS_ID_MAP["resourcepack"])
    params["searchFilter"] = query.strip()
    params["sortField"] = str(SORT_FIELD_MAP["popularity"])
    params["sortOrder"] = "desc"
    params["index"] = str(offset)
    params["pageSize"] = str(min(limit, 50))

    try:
        session = _get_session()
        resp = session.get(f"{CURSEFORGE_API_BASE}/mods/search", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.warning(f"CurseForge 资源包搜索失败: {e}")
        return {"data": [], "pagination": {"totalCount": 0}}


def search_shaders(query: str = "", game_version: Optional[str] = None, offset: int = 0, limit: int = 20) -> Dict:
    """搜索 CurseForge 光影 (classId=6552)"""
    params = _build_search_facets(game_version, None, CLASS_ID_MAP["shader"])
    params["searchFilter"] = query.strip()
    params["sortField"] = str(SORT_FIELD_MAP["popularity"])
    params["sortOrder"] = "desc"
    params["index"] = str(offset)
    params["pageSize"] = str(min(limit, 50))

    try:
        session = _get_session()
        resp = session.get(f"{CURSEFORGE_API_BASE}/mods/search", params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.warning(f"CurseForge 光影搜索失败: {e}")
        return {"data": [], "pagination": {"totalCount": 0}}


# ══════════════════════════════════════════════════════════════════════
# 项目信息
# ══════════════════════════════════════════════════════════════════════


def get_project_info(project_id: int) -> Optional[Dict]:
    """
    获取 CurseForge 项目详情

    返回包含: id, name, slug, summary, downloadCount, logo, authors,
    categories, links, latestFiles, latestFilesIndexes 等

    Args:
        project_id: CurseForge 项目 ID (数字)

    Returns:
        项目详情字典，或 None
    """
    try:
        session = _get_session()
        resp = session.get(f"{CURSEFORGE_API_BASE}/mods/{project_id}", timeout=15)
        resp.raise_for_status()
        result = resp.json()
        return result.get("data", result)
    except requests.exceptions.RequestException as e:
        logger.warning(f"获取 CurseForge 项目 {project_id} 失败: {e}")
        return None


def get_projects_batch(project_ids: List[int]) -> List[Dict]:
    """批量获取 CurseForge 项目详情 (POST /v1/mods)"""
    if not project_ids:
        return []

    try:
        session = _get_session()
        resp = session.post(f"{CURSEFORGE_API_BASE}/mods", json={"modIds": project_ids}, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        return result.get("data", [])
    except requests.exceptions.RequestException as e:
        logger.warning(f"批量获取 CurseForge 项目失败: {e}")
        return []


# ══════════════════════════════════════════════════════════════════════
# 版本与文件
# ══════════════════════════════════════════════════════════════════════


def _parse_loader_types(mod: Dict) -> List[str]:
    """从 CurseForge mod 数据中提取支持的加载器类型列表"""
    loaders = set()
    for file_data in mod.get("latestFiles") or []:
        for loader_id in file_data.get("modLoaderTypes") or []:
            name = LOADER_TYPE_MAP.get(loader_id)
            if name:
                loaders.add(name)
    # 也检查 latestFilesIndexes 中没有加载器信息的文件，尝试从分类推断
    for cat in mod.get("categories") or []:
        cat_name = (cat.get("name") or "").lower()
        if "forge" in cat_name and "neo" not in cat_name:
            loaders.add("forge")
        if "fabric" in cat_name:
            loaders.add("fabric")
        if "neoforge" in cat_name:
            loaders.add("neoforge")
        if "quilt" in cat_name:
            loaders.add("quilt")
    return list(loaders)


def _parse_game_versions(mod: Dict) -> List[str]:
    """从 CurseForge mod 数据中提取支持的 MC 版本列表

    参考 PCL-CE: 通过 latestFiles.gameVersions 和 latestFilesIndexes.gameVersion 收集
    """
    versions = set()
    for file_data in mod.get("latestFiles") or []:
        for v in file_data.get("gameVersions") or []:
            if _is_mc_version(v):
                versions.add(v)
    for idx in mod.get("latestFilesIndexes") or []:
        v = idx.get("gameVersion", "")
        if _is_mc_version(v):
            versions.add(v)
    return sorted(versions, key=lambda v: _version_sort_key(v), reverse=True)


def _is_mc_version(v: str) -> bool:
    """检查字符串是否是 Minecraft 版本号"""
    from version_utils import is_legacy_version_format, is_mc_snapshot_version, is_new_version_format

    return bool(is_legacy_version_format(v) or is_new_version_format(v) or is_mc_snapshot_version(v))


def _version_sort_key(v: str) -> tuple:
    """版本号排序键"""
    from version_utils import version_to_drop

    drop = version_to_drop(v, allow_snapshot=True)
    return (drop, v)


def get_project_files(
    project_id: int, game_version: Optional[str] = None, mod_loader: Optional[str] = None, limit: int = 50
) -> List[Dict]:
    """
    获取 CurseForge 项目文件列表

    参考 PCL-CE: 从 latestFiles + latestFilesIndexes 收集文件
    这里的 files 包含 fileId, fileName, downloadUrl, releaseType, gameVersions 等

    Args:
        project_id: CurseForge 项目 ID
        game_version: 筛选游戏版本
        mod_loader: 筛选加载器
        limit: 最大返回数量

    Returns:
        文件信息列表
    """
    info = get_project_info(project_id)
    if not info:
        return []

    files = []
    loader_id = LOADER_NAME_TO_ID.get(mod_loader) if mod_loader else None
    seen_ids = set()

    # latestFiles 包含文件名、downloadUrl、modLoaderTypes 等完整信息
    for f in info.get("latestFiles") or []:
        file_id = f.get("id")
        if file_id in seen_ids:
            continue
        seen_ids.add(file_id)

        # 加载器筛选
        if loader_id is not None:
            file_loaders = f.get("modLoaderTypes") or []
            if file_loaders and loader_id not in file_loaders:
                continue

        # 游戏版本筛选
        if game_version:
            file_versions = f.get("gameVersions") or []
            if file_versions and game_version not in file_versions:
                continue

        files.append(
            {
                "id": file_id,
                "project_id": project_id,
                "display_name": f.get("displayName", ""),
                "file_name": f.get("fileName", ""),
                "download_url": f.get("downloadUrl", ""),
                "version": f.get("displayName", ""),
                "release_type": FILE_STATUS_MAP.get(f.get("releaseType", 1), "release"),
                "file_date": f.get("fileDate", ""),
                "game_versions": f.get("gameVersions", []),
                "loaders": [LOADER_TYPE_MAP.get(lt, str(lt)) for lt in (f.get("modLoaderTypes") or [])],
                "download_count": f.get("downloadCount", 0),
                "hashes": {h.get("algo", 1): h.get("value", "") for h in (f.get("hashes") or [])},
                "source": "curseforge",
            }
        )

        if len(files) >= limit:
            break

    return files


def get_latest_version(
    project_id: int, game_version: Optional[str] = None, mod_loader: Optional[str] = None, version_type: str = "release"
) -> Optional[Dict]:
    """
    获取 CurseForge 项目的最佳兼容文件（PCL-CE 风格）

    策略: 按 latestFiles 顺序（最新优先），匹配 game_version + mod_loader，
    匹配 version_type (releaseType: 1=Release, 2=Beta, 3=Alpha)

    Args:
        project_id: CurseForge 项目 ID
        game_version: 游戏版本
        mod_loader: 模组加载器
        version_type: 文件类型 ("release", "beta", "alpha")

    Returns:
        最佳兼容文件信息字典，或 None
    """
    files = get_project_files(project_id, game_version, mod_loader, limit=100)
    if not files:
        return None

    # 优先匹配 version_type
    for f in files:
        if f.get("release_type") == version_type:
            return f

    # fallback: release 优先
    if version_type != "release":
        for f in files:
            if f.get("release_type") == "release":
                return f

    # 最终 fallback
    return files[0] if files else None


def get_file_download_url(project_id: int, file_id: int) -> Optional[str]:
    """
    获取 CurseForge 文件的下载 URL

    POST /v1/mods/{modId}/files/{fileId}/download-url
    返回临时下载链接

    Args:
        project_id: 项目 ID
        file_id: 文件 ID

    Returns:
        下载 URL 字符串，或 None
    """
    try:
        session = _get_session()
        resp = session.post(f"{CURSEFORGE_API_BASE}/mods/{project_id}/files/{file_id}/download-url", timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", "")
    except requests.exceptions.RequestException as e:
        logger.warning(f"获取 CurseForge 下载 URL 失败 (mod={project_id}, file={file_id}): {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# 下载与安装
# ══════════════════════════════════════════════════════════════════════


def download_file(
    project_id: int,
    file_id: int,
    save_dir: str,
    filename: Optional[str] = None,
    expected_hash: Optional[str] = None,
    sha1: bool = True,
) -> Tuple[bool, str]:
    """
    下载 CurseForge 文件

    两步流程:
    1. POST /mods/{id}/files/{fileId}/download-url → 获取临时下载链接
    2. 下载文件到指定目录

    Args:
        project_id: CurseForge 项目 ID
        file_id: 文件 ID
        save_dir: 保存目录
        filename: 指定文件名（可选，默认从响应中读取或从 URL 推断）
        expected_hash: 期望的文件哈希值（sha1 或 md5）
        sha1: True=sha1, False=md5

    Returns:
        (success, file_path_or_error)
    """
    # 1. 获取下载 URL
    download_url = get_file_download_url(project_id, file_id)
    if not download_url:
        return False, "无法获取下载链接"

    # 2. 确定文件名
    if not filename:
        filename = download_url.split("/")[-1].split("?")[0]

    save_path = Path(save_dir) / filename

    # 3. 哈希校验
    if save_path.exists():
        if expected_hash:
            actual = _compute_hash(save_path, sha=sha1)
            if actual and actual.lower() == expected_hash.lower():
                logger.info(f"CurseForge 文件已存在且哈希匹配，跳过: {filename}")
                return True, str(save_path)
        else:
            logger.debug(f"CurseForge 文件已存在（无哈希校验），跳过: {filename}")

    # 4. 下载
    success = _download_file(download_url, save_path)
    if success:
        return True, str(save_path)
    return False, f"下载失败: {filename}"


def _compute_hash(file_path: Path, sha: bool = True) -> Optional[str]:
    """计算文件哈希"""
    try:
        h = hashlib.sha1() if sha else hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


def install_mod(
    project_id: int,
    game_version: Optional[str] = None,
    mod_loader: Optional[str] = None,
    mods_dir: str = ".",
    version_type: str = "release",
) -> Tuple[bool, str]:
    """
    安装 CurseForge 模组到指定目录

    自动获取最新兼容版本并下载。

    Args:
        project_id: CurseForge 项目 ID
        game_version: 游戏版本
        mod_loader: 模组加载器
        mods_dir: mods 目录
        version_type: 版本类型

    Returns:
        (success, message)
    """
    file_info = get_latest_version(project_id, game_version, mod_loader, version_type)
    if not file_info:
        return False, f"未找到兼容版本 (game={game_version}, loader={mod_loader})"

    file_id = file_info["id"]
    filename = file_info.get("file_name", "")
    hashes = file_info.get("hashes", {})
    expected_hash = hashes.get(1)  # SHA-1 (algo=1)

    return download_file(project_id, file_id, mods_dir, filename, expected_hash, sha1=True)


# ══════════════════════════════════════════════════════════════════════
# 版本比较（委托到 version_utils）
# ══════════════════════════════════════════════════════════════════════


def compare_versions_curse(current: str, latest: str) -> Optional[int]:
    """比较两个 CurseForge 模组版本号，委托到 version_utils"""
    if not current or not latest:
        return None
    if current.strip() == latest.strip():
        return 0
    try:
        return compare_versions(current, latest)
    except Exception:
        if current < latest:
            return -1
        elif current > latest:
            return 1
        return 0


# ══════════════════════════════════════════════════════════════════════
# 更新检测辅助
# ══════════════════════════════════════════════════════════════════════


def get_latest_version_for_update(
    project_id: int, game_version: Optional[str] = None, mod_loader: Optional[str] = None, version_type: str = "release"
) -> Optional[Dict]:
    """
    获取用于更新检测的版本信息（PCL-CE 风格多策略回退）

    策略:
    1. 精确 game_version + mod_loader + version_type
    2. 放宽 loader，仅 game_version
    3. 取最新文件（无筛选）

    Args:
        project_id: 项目 ID
        game_version: 游戏版本
        mod_loader: 加载器类型
        version_type: 文件类型

    Returns:
        版本信息字典，或 None
    """
    # 策略 1: 精确匹配
    result = get_latest_version(project_id, game_version, mod_loader, version_type)
    if result:
        return result

    # 策略 2: 放宽 loader
    if mod_loader:
        result = get_latest_version(project_id, game_version, None, version_type)
        if result:
            return result

    # 策略 3: 取最新
    if game_version or mod_loader:
        result = get_latest_version(project_id, None, None, version_type)
        if result:
            return result

    return None


# ══════════════════════════════════════════════════════════════════════
# 项目搜索（通过 slug / modid）
# ══════════════════════════════════════════════════════════════════════


def search_project_by_slug(slug: str, class_id: Optional[int] = None) -> Optional[Dict]:
    """
    通过 slug 搜索 CurseForge 项目

    GET /v1/mods/search?gameId=432&slug=xxx

    Args:
        slug: 项目 slug
        class_id: 分类 ID (可选，用于验证)

    Returns:
        项目信息字典，或 None
    """
    params: Dict[str, str] = {"gameId": str(CURSEFORGE_GAME_ID), "slug": slug}
    if class_id:
        params["classId"] = str(class_id)

    try:
        session = _get_session()
        resp = session.get(f"{CURSEFORGE_API_BASE}/mods/search", params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("data", [])
        return results[0] if results else None
    except requests.exceptions.RequestException as e:
        logger.warning(f"搜索 CurseForge slug={slug} 失败: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════
# 搜索结果标准化（用于双源合并）
# ══════════════════════════════════════════════════════════════════════


def normalize_search_result(cf_result: Dict) -> Dict:
    """
    将 CurseForge API 返回的项目格式化为与 Modrinth 兼容的标准化格式

    标准化字段:
    - project_id: str (CurseForge 数字 ID 转字符串)
    - title: 项目名称
    - description: 简介
    - author: 作者名
    - downloads: 下载量
    - icon_url: 图标 URL
    - slug: slug
    - game_versions: MC 版本列表
    - loaders: 加载器类型列表
    - source: "curseforge"
    - raw: 原始数据
    """
    title = cf_result.get("name", "")
    logo = cf_result.get("logo", {}) or {}
    authors = cf_result.get("authors", []) or []

    return {
        "project_id": str(cf_result.get("id", "")),
        "title": title,
        "description": cf_result.get("summary", ""),
        "author": authors[0].get("name", "") if authors else "",
        "downloads": cf_result.get("downloadCount", 0),
        "icon_url": (logo.get("thumbnailUrl") or logo.get("url") or ""),
        "slug": cf_result.get("slug", ""),
        "game_versions": _parse_game_versions(cf_result),
        "loaders": _parse_loader_types(cf_result),
        "source": "curseforge",
        "raw": cf_result,
    }


# ══════════════════════════════════════════════════════════════════════
# 双源合并搜索（Modrinth + CurseForge）
# ══════════════════════════════════════════════════════════════════════


def normalize_modrinth_result(mr_hit: Dict) -> Dict:
    """将 Modrinth 搜索结果归一化为统一格式"""
    return {
        "project_id": mr_hit.get("project_id", "") or mr_hit.get("slug", ""),
        "title": mr_hit.get("title", ""),
        "description": mr_hit.get("description", ""),
        "author": mr_hit.get("author", ""),
        "downloads": mr_hit.get("downloads", 0),
        "icon_url": mr_hit.get("icon_url", ""),
        "slug": mr_hit.get("slug", ""),
        "game_versions": mr_hit.get("versions", []) or [],
        "loaders": mr_hit.get("categories", []) or [],
        "source": "modrinth",
        "raw": mr_hit,
    }


def merge_and_rank(
    modrinth_results: List[Dict], curseforge_results: List[Dict], dedup_threshold: float = 0.8
) -> List[Dict]:
    """
    合并两个源的结果，同名资源优先取下载量高的平台，然后按下载量降序排列

    去重策略（参考 PCL-CE: ModComp 的 CompProject 合并逻辑）:
    1. 按 slug/title 归一化后去重
    2. 同名资源对比 downloadCount，保留高下载量的
    3. 结果按 downloads 降序排列

    Args:
        modrinth_results: Modrinth 搜索结果（已归一化）
        curseforge_results: CurseForge 搜索结果（已归一化）
        dedup_threshold: 名称相似度阈值（暂未实现，保留接口）

    Returns:
        合并排序后的结果列表
    """
    merged: Dict[str, Dict] = {}
    all_items = list(modrinth_results) + list(curseforge_results)

    for item in all_items:
        key = _dedup_key(item)

        if key in merged:
            existing = merged[key]
            # 同名资源：保留下载量更高的
            if item["downloads"] > existing["downloads"]:
                merged[key] = item
        else:
            merged[key] = item

    # 按下载量降序
    ranked = sorted(merged.values(), key=lambda x: x["downloads"], reverse=True)
    logger.info(
        f"双源合并: Modrinth={len(modrinth_results)}, CurseForge={len(curseforge_results)}, " f"去重后={len(ranked)}"
    )
    return ranked


def _dedup_key(item: Dict) -> str:
    """生成去重键（slug 优先，title 回退）"""
    slug = (item.get("slug") or "").lower().strip()
    if slug:
        return slug
    title = (item.get("title") or "").lower().strip()
    # 去除非字母数字字符
    import re

    return re.sub(r"[^a-z0-9]", "", title)


def _fetch_modrinth_batch(
    search_fn, query: str, game_version: Optional[str], mod_loader: Optional[str] = None, target_count: int = 200
) -> tuple:
    """
    从 Modrinth 批量拉取搜索结果（支持多页）

    Modrinth API 每页最大 100 条，本函数自动翻页拉取至 target_count 条。
    使用顺序请求（非并发），避免与 Tkinter 主线程冲突。

    Args:
        search_fn: search_mods / search_resource_packs / search_shaders
        query: 搜索关键词
        game_version: 游戏版本
        mod_loader: 模组加载器（仅模组搜索使用）
        target_count: 目标拉取数量

    Returns:
        (all_hits: list, total_hits: int)  — total_hits 为 API 返回的真实总数
    """
    page_size = 100  # Modrinth 单页上限
    max_pages = 5  # 最多拉 5 页（500 条）

    def _call_search(offset_val):
        if mod_loader is not None:
            return search_fn(query, game_version, mod_loader, offset=offset_val, limit=page_size)
        else:
            return search_fn(query, game_version, offset=offset_val, limit=page_size)

    # 第一页
    first = _call_search(0)
    all_hits = list(first.get("hits", []))
    total_hits = first.get("total_hits", 0)

    # 顺序翻页拉取
    for page_idx in range(1, max_pages):
        if len(all_hits) >= target_count:
            break
        if len(all_hits) < page_size * page_idx:
            # 上一页不足 page_size，说明已拉完
            break

        result = _call_search(page_size * page_idx)
        page_hits = result.get("hits", [])
        if not page_hits:
            break
        all_hits.extend(page_hits)
        if result.get("total_hits", 0) > total_hits:
            total_hits = result.get("total_hits", 0)

    return all_hits, total_hits


def _fetch_curseforge_batch(
    search_fn, query: str, game_version: Optional[str], mod_loader: Optional[str] = None, target_count: int = 100
) -> list:
    """
    从 CurseForge 批量拉取搜索结果（支持多页）

    CurseForge API 每页最大 50 条，本函数自动翻页拉取至 target_count 条。

    Args:
        search_fn: search_mods / search_resource_packs / search_shaders
        query: 搜索关键词
        game_version: 游戏版本
        mod_loader: 模组加载器
        target_count: 目标拉取数量

    Returns:
        原始 CF 结果列表（未经 normalize）
    """
    page_size = 50  # CF 单页上限
    all_data = []

    # 第一页
    if mod_loader is not None:
        first = search_fn(query, game_version, mod_loader, offset=0, limit=page_size)
    else:
        first = search_fn(query, game_version, offset=0, limit=page_size)
    data = first.get("data", []) or []
    all_data.extend(data)

    if len(all_data) >= target_count or len(data) < page_size:
        return all_data

    # 第二页
    if mod_loader is not None:
        second = search_fn(query, game_version, mod_loader, offset=page_size, limit=page_size)
    else:
        second = search_fn(query, game_version, offset=page_size, limit=page_size)
    data2 = second.get("data", []) or []
    all_data.extend(data2)

    return all_data


def unified_search_mods(
    query: str = "",
    game_version: Optional[str] = None,
    mod_loader: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
    sort: str = "downloads",
    include_curseforge: bool = True,
) -> Dict:
    """
    双源合并搜索模组（Modrinth + CurseForge）

    默认同时查询两个源，同名资源优先取下载量高的平台，
    最终按下载量降序排列后分页返回。

    Args:
        query: 搜索关键词
        game_version: 游戏版本
        mod_loader: 模组加载器
        offset: 偏移量（分页）
        limit: 每页数量
        sort: 排序方式 ("downloads", "popularity", "relevance")
        include_curseforge: 是否包含 CurseForge 结果（需设置 CURSEFORGE_API_KEY）

    Returns:
        {"hits": [...], "total_hits": int, "offset": int, "limit": int,
         "sources": {"modrinth": int, "curseforge": int}}
    """
    from modrinth import search_mods as mr_search

    # 1. Modrinth 批量拉取（最多 200 条，支持多页）
    mr_hits, mr_total = _fetch_modrinth_batch(mr_search, query, game_version, mod_loader, target_count=200)
    mr_normalized = [normalize_modrinth_result(h) for h in mr_hits]

    # 2. CurseForge 批量拉取（最多 100 条，支持多页）
    cf_normalized = []
    if include_curseforge and is_configured():
        cf_data = _fetch_curseforge_batch(search_mods, query, game_version, mod_loader, target_count=100)
        for cf_item in cf_data:
            cf_normalized.append(normalize_search_result(cf_item))

    # 3. 合并去重排序
    merged = merge_and_rank(mr_normalized, cf_normalized)

    # 4. 使用 Modrinth 的真实 total_hits 作为总数参考
    #    （因为 Modrinth 通常是数据量更大的源）
    total = mr_total if mr_total > 0 else len(merged)
    page = merged[offset : offset + limit]

    return {
        "hits": page,
        "total_hits": total,
        "offset": offset,
        "limit": limit,
        "sources": {"modrinth": len(mr_normalized), "curseforge": len(cf_normalized)},
    }


def unified_search_resource_packs(
    query: str = "",
    game_version: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
    include_curseforge: bool = True,
) -> Dict:
    """双源合并搜索资源包"""
    from modrinth import search_resource_packs as mr_search

    mr_hits, mr_total = _fetch_modrinth_batch(mr_search, query, game_version, None, target_count=200)
    mr_normalized = [normalize_modrinth_result(h) for h in mr_hits]

    cf_normalized = []
    if include_curseforge and is_configured():
        cf_data = _fetch_curseforge_batch(search_resource_packs, query, game_version, None, target_count=100)
        for cf_item in cf_data:
            cf_normalized.append(normalize_search_result(cf_item))

    merged = merge_and_rank(mr_normalized, cf_normalized)
    total = mr_total if mr_total > 0 else len(merged)
    page = merged[offset : offset + limit]

    return {
        "hits": page,
        "total_hits": total,
        "offset": offset,
        "limit": limit,
        "sources": {"modrinth": len(mr_normalized), "curseforge": len(cf_normalized)},
    }


def unified_search_shaders(
    query: str = "",
    game_version: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
    include_curseforge: bool = True,
) -> Dict:
    """双源合并搜索光影"""
    from modrinth import search_shaders as mr_search

    mr_hits, mr_total = _fetch_modrinth_batch(mr_search, query, game_version, None, target_count=200)
    mr_normalized = [normalize_modrinth_result(h) for h in mr_hits]

    cf_normalized = []
    if include_curseforge and is_configured():
        cf_data = _fetch_curseforge_batch(search_shaders, query, game_version, None, target_count=100)
        for cf_item in cf_data:
            cf_normalized.append(normalize_search_result(cf_item))

    merged = merge_and_rank(mr_normalized, cf_normalized)
    total = mr_total if mr_total > 0 else len(merged)
    page = merged[offset : offset + limit]

    return {
        "hits": page,
        "total_hits": total,
        "offset": offset,
        "limit": limit,
        "sources": {"modrinth": len(mr_normalized), "curseforge": len(cf_normalized)},
    }


# ══════════════════════════════════════════════════════════════════════
# 双源更新检测
# ══════════════════════════════════════════════════════════════════════


def check_update_dual_source(
    modid: str, mod_name: str, current_version: str, game_version: str, mod_loader: str
) -> Optional[Dict]:
    """
    双源检查模组更新（Modrinth + CurseForge）

    同时在两个平台查找最新版本，取版本号最新的作为更新目标。

    Args:
        modid: 模组 ID (slug)
        mod_name: 模组名称（备用查找）
        current_version: 当前版本号
        game_version: 游戏版本
        mod_loader: 加载器类型

    Returns:
        {
            "modid": str, "project_id": str, "source": str,
            "latest_version": str, "current_version": str,
            "mod_name": str, "download_url": str
        }
        或 None（无需更新或无法检测）
    """
    from modrinth import compare_mod_versions
    from modrinth import get_latest_version_by_slug as mr_latest

    best_result = None
    best_version = current_version

    # 1. Modrinth 查询
    mr = mr_latest(slug=modid, game_version=game_version, mod_loader=mod_loader)
    if mr:
        mr_proj_id, version_info = mr
        latest_ver = version_info.get("version_number", "")
        if latest_ver and compare_mod_versions(current_version, latest_ver) is not None:
            cmp = compare_mod_versions(current_version, latest_ver)
            if cmp is not None and cmp < 0:
                # 需要更新
                if compare_mod_versions(best_version, latest_ver) is not None:
                    if compare_mod_versions(best_version, latest_ver) < 0:
                        best_version = latest_ver
                        files = version_info.get("files", [])
                        primary = next((f for f in files if f.get("primary")), files[0] if files else None)
                        best_result = {
                            "modid": modid,
                            "project_id": mr_proj_id,
                            "source": "modrinth",
                            "latest_version": latest_ver,
                            "current_version": current_version,
                            "mod_name": mod_name,
                            "download_url": primary.get("url", "") if primary else "",
                            "filename": primary.get("filename", "") if primary else "",
                            "version_info": version_info,
                        }

    # 2. CurseForge 查询
    if is_configured():
        try:
            cf_project = search_project_by_slug(modid)
            if cf_project:
                cf_id = cf_project.get("id")
                if cf_id:
                    cf_version = get_latest_version_for_update(cf_id, game_version=game_version, mod_loader=mod_loader)
                    if cf_version:
                        cf_ver = cf_version.get("version", "") or cf_version.get("display_name", "")
                        if cf_ver and compare_mod_versions(current_version, cf_ver) is not None:
                            cmp = compare_mod_versions(current_version, cf_ver)
                            if cmp is not None and cmp < 0:
                                if compare_mod_versions(best_version, cf_ver) is not None:
                                    if compare_mod_versions(best_version, cf_ver) < 0:
                                        best_version = cf_ver
                                        best_result = {
                                            "modid": modid,
                                            "project_id": str(cf_id),
                                            "source": "curseforge",
                                            "latest_version": cf_ver,
                                            "current_version": current_version,
                                            "mod_name": mod_name,
                                            "download_url": cf_version.get("download_url", ""),
                                            "filename": cf_version.get("file_name", ""),
                                            "version_info": cf_version,
                                        }
        except Exception as e:
            logger.debug(f"CurseForge 更新检测异常 ({modid}): {e}")

    return best_result
