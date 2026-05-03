"""Modrinth API 集成模块

提供模组/整合包搜索、版本查询和下载功能，基于 Modrinth V2 API。
API 文档: https://docs.modrinth.com/api/

功能:
- 搜索模组（支持关键词 + 游戏版本 + 加载器筛选）
- 获取模组详情
- 获取模组的特定版本文件
- 下载模组 jar 文件到 mods 目录
- 搜索整合包（支持关键词 + 游戏版本筛选）
- 获取整合包的版本文件
- 下载整合包 .mrpack 文件
"""

import hashlib
import time
from typing import List, Dict, Optional, Set, Tuple
from pathlib import Path

import requests
from logzero import logger
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

from structured_logger import slog


MODRINTH_API_BASE = "https://api.modrinth.com/v2"
MODRINTH_USER_AGENT = "FMCL-MinecraftLauncher/1.0 (github.com/Janson20/FMCL)"

DOWNLOAD_CHUNK_SIZE = 8192
# 重试配置
MAX_RETRIES = 3
RETRY_BACKOFF_FACTOR = 2
# 应重试的 HTTP 状态码
RETRY_STATUS_FORCELIST = [429, 500, 502, 503, 504]

# 缓存：旧格式版本号 {major: {minor1, minor2, ...}}
# 如 {16: {0,1,2,3,4,5}, 20: {0,1,2,3,4,5,6}, 21: {0,1,2,...,11}}
_legacy_versions_cache: Optional[Dict[int, Set[int]]] = None

# 缓存：新格式版本号 (YY.D.H) {yy: {d1, d2, ...}}
# 如 {26: {1, 2}, 27: {1}}
# 每个热修复版本 {yy: {d: {h1, h2, ...}}}
_new_versions_cache: Optional[Dict[int, Dict[int, Set[int]]]] = None

# 共享 Session（连接池 + 重试机制）
_shared_session: Optional[requests.Session] = None


def _get_headers() -> Dict[str, str]:
    """获取 API 请求头"""
    return {"User-Agent": MODRINTH_USER_AGENT}


def _get_session() -> requests.Session:
    """获取共享 Session（连接池 + 自动重试）

    使用连接池复用 TCP 连接，避免重复 TLS 握手。
    配置指数退避重试：失败后等待 2^retry 秒，最多 3 次。
    """
    global _shared_session
    if _shared_session is None:
        _shared_session = requests.Session()
        retry_strategy = Retry(
            total=MAX_RETRIES,
            backoff_factor=RETRY_BACKOFF_FACTOR,
            status_forcelist=RETRY_STATUS_FORCELIST,
            allowed_methods=["GET", "HEAD"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=20,
            pool_maxsize=50,
        )
        _shared_session.mount("https://", adapter)
        _shared_session.mount("http://", adapter)
        _shared_session.headers.update(_get_headers())
    return _shared_session


def _download_with_resume(
    url: str,
    file_path: Path,
    timeout: int = 120,
) -> bool:
    """支持断点续传和重试的文件下载

    流程：
    1. 检查本地已有文件大小，构造 Range 头请求续传
    2. 若服务器返回 206，以追加模式写入
    3. 若服务器不支持 Range（返回 200），从头下载
    4. 网络异常时指数退避重试

    Args:
        url: 下载 URL
        file_path: 保存路径
        timeout: 请求超时秒数

    Returns:
        是否下载成功
    """
    session = _get_session()
    downloaded = 0

    if file_path.exists():
        downloaded = file_path.stat().st_size

    last_error = None

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            headers = _get_headers()
            if downloaded > 0:
                headers["Range"] = f"bytes={downloaded}-"

            resp = session.get(url, headers=headers, timeout=timeout, stream=True)

            if downloaded > 0 and resp.status_code == 206:
                mode = "ab"
            else:
                if resp.status_code != 206:
                    downloaded = 0
                mode = "wb"

            resp.raise_for_status()

            total_size = int(resp.headers.get("Content-Length", 0)) + downloaded

            with open(str(file_path), mode) as f:
                for chunk in resp.iter_content(chunk_size=DOWNLOAD_CHUNK_SIZE):
                    if chunk:
                        f.write(chunk)

            logger.info(f"下载完成: {file_path.name} ({total_size} bytes)")
            return True

        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                requests.exceptions.ChunkedEncodingError) as e:
            last_error = e
            if file_path.exists():
                downloaded = file_path.stat().st_size
            if attempt <= MAX_RETRIES:
                wait = RETRY_BACKOFF_FACTOR ** attempt
                logger.warning(
                    f"下载中断 (尝试 {attempt}/{MAX_RETRIES + 1}): {e}，"
                    f"{wait} 秒后重试 (已下载 {downloaded} bytes)"
                )
                time.sleep(wait)
            else:
                logger.error(f"下载失败，已达最大重试次数: {e}")
                return False

        except requests.exceptions.RequestException as e:
            last_error = e
            if attempt <= MAX_RETRIES:
                wait = RETRY_BACKOFF_FACTOR ** attempt
                logger.warning(
                    f"下载请求异常 (尝试 {attempt}/{MAX_RETRIES + 1}): {e}，"
                    f"{wait} 秒后重试"
                )
                time.sleep(wait)
            else:
                logger.error(f"下载失败，已达最大重试次数: {e}")
                return False

    logger.error(f"下载失败: {last_error}")
    return False


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
        session = _get_session()
        resp = session.get(
            f"{MODRINTH_API_BASE}/search",
            params=params,
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


def search_resource_packs(
    query: str = "",
    game_version: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
) -> Dict:
    """
    搜索 Modrinth 上的资源包

    Args:
        query: 搜索关键词，为空时返回热门资源包
        game_version: 游戏版本筛选 (如 "1.20.4")
        offset: 分页偏移量
        limit: 每页数量 (最大 100)

    Returns:
        {
            "hits": [资源包信息列表],
            "offset": 当前偏移,
            "limit": 每页数量,
            "total_hits": 总结果数
        }
    """
    facets = []

    facets.append(["project_type:resourcepack"])

    if game_version:
        facets.append([f"versions:{game_version}"])

    params: Dict = {
        "offset": offset,
        "limit": limit,
        "index": "relevance",
    }

    if facets:
        import json
        params["facets"] = json.dumps(facets)

    if query:
        params["query"] = query

    logger.debug(
        f"Modrinth 资源包搜索: query='{query}', version={game_version}, "
        f"offset={offset}, facets={facets}"
    )

    try:
        session = _get_session()
        resp = session.get(
            f"{MODRINTH_API_BASE}/search",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        logger.info(
            f"Modrinth 资源包搜索: query='{query}', version={game_version}, "
            f"offset={offset}, 总结果={data.get('total_hits', 0)}"
        )
        return data

    except requests.exceptions.RequestException as e:
        logger.error(f"Modrinth 资源包搜索失败: {e}")
        return {"hits": [], "offset": offset, "limit": limit, "total_hits": 0}


def search_shaders(
    query: str = "",
    game_version: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
) -> Dict:
    """
    搜索 Modrinth 上的光影

    Args:
        query: 搜索关键词，为空时返回热门光影
        game_version: 游戏版本筛选 (如 "1.20.4")
        offset: 分页偏移量
        limit: 每页数量 (最大 100)

    Returns:
        {
            "hits": [光影信息列表],
            "offset": 当前偏移,
            "limit": 每页数量,
            "total_hits": 总结果数
        }
    """
    facets = []

    facets.append(["project_type:shader"])

    if game_version:
        facets.append([f"versions:{game_version}"])

    params: Dict = {
        "offset": offset,
        "limit": limit,
        "index": "relevance",
    }

    if facets:
        import json
        params["facets"] = json.dumps(facets)

    if query:
        params["query"] = query

    logger.debug(
        f"Modrinth 光影搜索: query='{query}', version={game_version}, "
        f"offset={offset}, facets={facets}"
    )

    try:
        session = _get_session()
        resp = session.get(
            f"{MODRINTH_API_BASE}/search",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        logger.info(
            f"Modrinth 光影搜索: query='{query}', version={game_version}, "
            f"offset={offset}, 总结果={data.get('total_hits', 0)}"
        )
        return data

    except requests.exceptions.RequestException as e:
        logger.error(f"Modrinth 光影搜索失败: {e}")
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
        session = _get_session()
        resp = session.get(
            f"{MODRINTH_API_BASE}/project/{project_id}/version",
            params=params,
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
    expected_hashes: Optional[Dict[str, str]] = None,
) -> Tuple[bool, str]:
    """
    下载模组文件，可选地校验文件哈希

    Args:
        download_url: 下载 URL
        save_path: 保存目录路径
        filename: 文件名
        expected_hashes: 期望的文件哈希 {算法: 哈希值}，如 {"sha1": "abc123", "sha512": "def456"}

    Returns:
        (是否成功, 保存路径或错误信息) 元组
    """
    try:
        save_dir = Path(save_path)
        save_dir.mkdir(parents=True, exist_ok=True)

        file_path = save_dir / filename

        if file_path.exists() and expected_hashes:
            for algorithm, expected in expected_hashes.items():
                try:
                    h = hashlib.new(algorithm)
                    with open(file_path, "rb") as f:
                        while chunk := f.read(65536):
                            h.update(chunk)
                    actual = h.hexdigest().lower()
                    expected = expected.lower()
                    if actual == expected:
                        logger.info(f"模组文件已存在且哈希匹配: {filename}")
                        return True, str(file_path)
                    logger.warning(f"模组文件哈希不匹配 {filename}，重新下载")
                    file_path.unlink()
                    break
                except (ValueError, OSError) as e:
                    logger.debug(f"验证已有文件哈希失败: {e}")
                    break
        elif file_path.exists() and not expected_hashes:
            logger.info(f"模组文件已存在，跳过: {file_path}")
            return True, str(file_path)

        logger.info(f"开始下载模组: {filename}")
        download_ok = _download_with_resume(download_url, file_path, timeout=120)

        if not download_ok:
            slog.error("mod_download_failed", filename=filename, error="下载失败，已达最大重试次数")
            return False, f"{filename} 下载失败"

        if expected_hashes:
            for algorithm, expected in expected_hashes.items():
                try:
                    h = hashlib.new(algorithm)
                    with open(file_path, "rb") as f:
                        while chunk := f.read(65536):
                            h.update(chunk)
                    actual = h.hexdigest().lower()
                    expected = expected.lower()
                    if actual != expected:
                        file_path.unlink()
                        logger.error(f"模组文件哈希校验失败 {filename}")
                        return False, f"{filename} 哈希校验失败"
                    logger.debug(f"哈希校验通过 ({algorithm}): {filename}")
                except (ValueError, OSError) as e:
                    logger.warning(f"无法校验哈希 {algorithm}: {e}")
                    break

        file_size = file_path.stat().st_size
        logger.info(f"模组下载完成: {filename} ({file_size} bytes)")
        slog.info("mod_download_complete", filename=filename, size_bytes=file_size)
        return True, str(file_path)

    except Exception as e:
        logger.error(f"下载模组失败: {e}")
        slog.error("mod_download_failed", filename=filename, error=str(e)[:200])
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
        session = _get_session()
        resp = session.get(
            f"{MODRINTH_API_BASE}/project/{project_id}",
            timeout=15,
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
        slog.warning("mod_no_compatible_version", mod_id=project_id, mod_title=mod_title,
                     game_version=game_version, loader=mod_loader)
        return False, f"{mod_title} 没有兼容的版本", []

    version = versions[0]
    mod_version_number = version.get("version_number", "")
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

    expected_hashes = primary_file.get("hashes")
    success, result = download_mod(download_url, mods_dir, filename, expected_hashes=expected_hashes)

    if success:
        installed_names.append(mod_title)
        msg = f"{mod_title} 安装成功"
        if skipped_deps:
            msg += f"（跳过不兼容依赖: {', '.join(skipped_deps)}）"
        slog.info("mod_install_decision", mod_id=project_id, mod_title=mod_title,
                  mod_version=mod_version_number, game_version=game_version, loader=mod_loader,
                  decision_reason="auto_match" if not skipped_deps else "auto_match_with_skipped_deps",
                  fallback_used=False, dependencies=[d.get("project_id", "") for d in dependencies if d.get("dependency_type") == "required"])
        return True, msg, installed_names
    else:
        slog.error("mod_install_failed", mod_id=project_id, mod_title=mod_title,
                   mod_version=mod_version_number, error=result)
        return False, f"{mod_title} 安装失败: {result}", installed_names


def install_resource_pack(
    project_id: str,
    game_version: str,
    resourcepacks_dir: str,
    status_callback=None,
) -> Tuple[bool, str]:
    """
    安装资源包（下载到 resourcepacks 目录）

    Args:
        project_id: Modrinth 项目 ID
        game_version: 游戏版本 (如 "1.20.6")
        resourcepacks_dir: resourcepacks 目录路径
        status_callback: 状态回调函数，接受 str 参数

    Returns:
        (是否成功, 结果消息 或 文件路径) 元组
    """
    project_info = get_project_info(project_id)
    title = project_info.get("title", project_id) if project_info else project_id

    versions = get_mod_versions(
        project_id,
        game_version=game_version,
    )

    if not versions:
        logger.warning(f"资源包 {title} 没有兼容的版本 ({game_version})")
        return False, f"{title} 没有兼容的版本"

    version = versions[0]
    version_number = version.get("version_number", "")
    files = version.get("files", [])

    primary_file = None
    for f in files:
        if f.get("primary", False):
            primary_file = f
            break
    if not primary_file and files:
        primary_file = files[0]

    if not primary_file:
        return False, f"{title} 没有可下载的文件"

    download_url = primary_file.get("url", "")
    filename = primary_file.get("filename", f"{title}.zip")

    if not download_url:
        return False, f"{title} 下载链接无效"

    if status_callback:
        status_callback(f"正在下载 {filename}...")

    expected_hashes = primary_file.get("hashes")
    success, result = download_mod(download_url, resourcepacks_dir, filename, expected_hashes=expected_hashes)

    if success:
        logger.info(f"资源包安装成功: {title} ({version_number}) -> {result}")
        return True, result
    else:
        logger.error(f"资源包安装失败: {result}")
        return False, f"{title} 安装失败: {result}"


def install_shader(
    project_id: str,
    game_version: str,
    shaderpacks_dir: str,
    status_callback=None,
) -> Tuple[bool, str]:
    """
    安装光影（下载到 shaderpacks 目录）

    Args:
        project_id: Modrinth 项目 ID
        game_version: 游戏版本 (如 "1.20.6")
        shaderpacks_dir: shaderpacks 目录路径
        status_callback: 状态回调函数，接受 str 参数

    Returns:
        (是否成功, 结果消息 或 文件路径) 元组
    """
    project_info = get_project_info(project_id)
    title = project_info.get("title", project_id) if project_info else project_id

    versions = get_mod_versions(
        project_id,
        game_version=game_version,
    )

    if not versions:
        logger.warning(f"光影 {title} 没有兼容的版本 ({game_version})")
        return False, f"{title} 没有兼容的版本"

    version = versions[0]
    version_number = version.get("version_number", "")
    files = version.get("files", [])

    primary_file = None
    for f in files:
        if f.get("primary", False):
            primary_file = f
            break
    if not primary_file and files:
        primary_file = files[0]

    if not primary_file:
        return False, f"{title} 没有可下载的文件"

    download_url = primary_file.get("url", "")
    filename = primary_file.get("filename", f"{title}.zip")

    if not download_url:
        return False, f"{title} 下载链接无效"

    if status_callback:
        status_callback(f"正在下载 {filename}...")

    expected_hashes = primary_file.get("hashes")
    success, result = download_mod(download_url, shaderpacks_dir, filename, expected_hashes=expected_hashes)

    if success:
        logger.info(f"光影安装成功: {title} ({version_number}) -> {result}")
        return True, result
    else:
        logger.error(f"光影安装失败: {result}")
        return False, f"{title} 安装失败: {result}"


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
        session = _get_session()
        resp = session.get(
            f"{MODRINTH_API_BASE}/tag/game_version",
            timeout=15,
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


# ═══════════════════════════════════════════════════════════════
# 整合包 (Modpack) API
# ═══════════════════════════════════════════════════════════════

def search_modpacks(
    query: str = "",
    game_version: Optional[str] = None,
    offset: int = 0,
    limit: int = 20,
) -> Dict:
    """
    搜索 Modrinth 上的整合包

    Args:
        query: 搜索关键词，为空时返回热门整合包
        game_version: 游戏版本筛选 (如 "1.20.4")
        offset: 分页偏移量
        limit: 每页数量 (最大 100)

    Returns:
        {
            "hits": [整合包信息列表],
            "offset": 当前偏移,
            "limit": 每页数量,
            "total_hits": 总结果数
        }
    """
    facets = []

    facets.append(["project_type:modpack"])

    if game_version:
        facets.append([f"versions:{game_version}"])

    params: Dict = {
        "offset": offset,
        "limit": limit,
        "index": "relevance",
    }

    if facets:
        import json
        params["facets"] = json.dumps(facets)

    if query:
        params["query"] = query

    logger.debug(
        f"Modrinth 整合包搜索: query='{query}', version={game_version}, "
        f"offset={offset}"
    )

    try:
        session = _get_session()
        resp = session.get(
            f"{MODRINTH_API_BASE}/search",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()

        logger.info(
            f"Modrinth 整合包搜索: query='{query}', version={game_version}, "
            f"offset={offset}, 总结果={data.get('total_hits', 0)}"
        )
        return data

    except requests.exceptions.RequestException as e:
        logger.error(f"Modrinth 整合包搜索失败: {e}")
        return {"hits": [], "offset": offset, "limit": limit, "total_hits": 0}


def get_modpack_versions(
    project_id: str,
    game_version: Optional[str] = None,
) -> List[Dict]:
    """
    获取整合包的版本列表

    返回的每个版本包含 files 列表，其中 .mrpack 文件通常是 primary 文件。

    Args:
        project_id: Modrinth 项目 ID
        game_version: 游戏版本筛选

    Returns:
        版本信息列表，每个版本包含 id, name, version_number, files 等
    """
    import json as _json

    params: Dict = {}

    if game_version:
        params["game_versions"] = _json.dumps([game_version])

    try:
        session = _get_session()
        resp = session.get(
            f"{MODRINTH_API_BASE}/project/{project_id}/version",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        versions = resp.json()

        logger.info(
            f"获取整合包 {project_id} 版本列表: {len(versions)} 个 "
            f"(game_version={game_version})"
        )
        return versions

    except requests.exceptions.RequestException as e:
        logger.error(f"获取整合包版本失败: {e}")
        return []


def download_modpack_file(
    project_id: str,
    game_version: Optional[str] = None,
    save_dir: Optional[str] = None,
    status_callback=None,
    version_data: Optional[Dict] = None,
) -> Tuple[bool, str]:
    """
    从 Modrinth 下载整合包的 .mrpack 文件

    自动获取最新兼容版本并下载主 .mrpack 文件。

    Args:
        project_id: Modrinth 项目 ID
        game_version: 游戏版本筛选 (None 表示不筛选)
        save_dir: 保存目录，默认保存到系统临时目录
        status_callback: 状态回调函数，接受 str 参数
        version_data: 指定版本数据字典。如果提供，直接使用该版本，
                      无需调用 API 获取版本列表。

    Returns:
        (是否成功, .mrpack 文件路径 或 错误信息) 元组
    """
    import tempfile

    if version_data is not None:
        versions = [version_data]
    else:
        if status_callback:
            status_callback(f"正在获取整合包 {project_id} 的版本信息...")

        versions = get_modpack_versions(project_id, game_version=game_version)

        if not versions:
            msg = f"整合包 {project_id} 没有可用的版本"
            logger.warning(msg)
            return False, msg

    version = versions[0]
    version_number = version.get("version_number", "未知")
    files = version.get("files", [])

    primary_file = None
    for f in files:
        if f.get("primary", False):
            primary_file = f
            break
    if not primary_file and files:
        for f in files:
            if f.get("filename", "").endswith(".mrpack"):
                primary_file = f
                break
    if not primary_file and files:
        primary_file = files[0]

    if not primary_file:
        msg = f"整合包 {project_id} 版本 {version_number} 没有可下载的文件"
        logger.warning(msg)
        return False, msg

    download_url = primary_file.get("url", "")
    filename = primary_file.get("filename", f"{project_id}.mrpack")

    if not download_url:
        msg = f"整合包 {project_id} 下载链接无效"
        logger.warning(msg)
        return False, msg

    if save_dir:
        target_dir = Path(save_dir)
    else:
        target_dir = Path(tempfile.gettempdir()) / "FMCL_modpack_downloads"

    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / filename

    if file_path.exists():
        logger.info(f"整合包文件已存在，跳过下载: {file_path}")
        if status_callback:
            status_callback(f"文件已存在: {filename}")
        return True, str(file_path)

    if status_callback:
        status_callback(f"正在下载 {filename}...")

    logger.info(f"开始下载整合包: {filename} ({download_url[:80]}...)")

    download_ok = _download_with_resume(download_url, file_path, timeout=300)

    if download_ok:
        file_size = file_path.stat().st_size
        logger.info(f"整合包下载完成: {filename} ({file_size} bytes)")
        slog.info("modpack_download_complete", filename=filename, size_bytes=file_size)
        return True, str(file_path)
    else:
        logger.error(f"下载整合包失败: {filename}")
        slog.error("modpack_download_failed", filename=filename, error="下载失败，已达最大重试次数")
        return False, f"{filename} 下载失败"


def _normalize_description(desc) -> str:
    """
    将 description 字段规范化为纯文本字符串。

    处理 fabric.mod.json 中 description 为本地化对象的情况：
    {"translate": "mod.xxx.desc", "fallback": "实际文本"}
    """
    if isinstance(desc, dict):
        result = desc.get("fallback", "") or desc.get("translate", "") or ""
        return " ".join(result.split()) if result else ""
    if isinstance(desc, str):
        return " ".join(desc.split())
    return " ".join(str(desc).split()) if desc else ""


def _normalize_author(author) -> str:
    """
    将 author 条目规范化为纯文本字符串。

    处理 fabric.mod.json 中 author 为对象的情况：
    {"name": "PlayerName", "contact": {...}}
    """
    if isinstance(author, str):
        return author
    if isinstance(author, dict):
        return author.get("name", "") or author.get("username", "") or ""
    return str(author)


def _normalize_author_list(authors) -> str:
    """
    将 authors 字段规范化为逗号分隔的作者字符串。

    支持字符串、列表、以及列表中的对象元素。
    """
    if isinstance(authors, str):
        return authors
    if isinstance(authors, (list, tuple)):
        names = [_normalize_author(a) for a in authors]
        return ", ".join(n for n in names if n)
    if authors:
        return str(authors)
    return ""


def extract_mod_metadata(jar_path: Path) -> Optional[Dict]:
    """
    从模组 jar 文件中提取元数据

    支持 Fabric (fabric.mod.json)、Forge (META-INF/mods.toml)、旧版 Forge (mcmod.info)

    Args:
        jar_path: jar 文件路径

    Returns:
        包含 name, modid, version, author, description, icon_base64 的字典，或 None
    """
    import json as _json_module
    import zipfile
    import base64

    try:
        import tomllib
    except ImportError:
        import tomli as tomllib

    if not jar_path.exists() or not jar_path.is_file():
        return None

    try:
        with zipfile.ZipFile(str(jar_path), "r") as zf:
            namelist = zf.namelist()

            mod_name = None
            mod_id = None
            mod_version = None
            mod_author = None
            mod_description = None
            icon_path_in_jar = None
            icon_base64 = None

            # ── 尝试 fabric.mod.json ──
            if "fabric.mod.json" in namelist:
                try:
                    with zf.open("fabric.mod.json") as f:
                        fabric_data = _json_module.load(f)
                    if isinstance(fabric_data, dict):
                        mod_name = fabric_data.get("name")
                        mod_id = fabric_data.get("id")
                        if not mod_version:
                            mod_version = str(fabric_data.get("version", "")) if fabric_data.get("version") else None
                        mod_description = _normalize_description(fabric_data.get("description"))
                        mod_author = _normalize_author_list(fabric_data.get("authors", []))
                        contributors = _normalize_author_list(fabric_data.get("contributors", []))
                        if mod_author and contributors:
                            mod_author = mod_author + ", " + contributors
                        elif contributors:
                            mod_author = contributors
                        icon_path_in_jar = fabric_data.get("icon")
                except Exception as e:
                    logger.debug(f"读取 fabric.mod.json 失败 ({jar_path.name}): {e}")

            # ── 尝试 META-INF/mods.toml (Forge/NeoForge) ──
            if "META-INF/mods.toml" in namelist:
                try:
                    with zf.open("META-INF/mods.toml") as f:
                        toml_data = tomllib.loads(f.read().decode("utf-8", errors="replace"))
                    mods_list = toml_data.get("mods", [])
                    if mods_list and isinstance(mods_list, list):
                        first_mod = mods_list[0]
                        if not mod_name:
                            mod_name = first_mod.get("displayName")
                        if not mod_id:
                            mod_id = first_mod.get("modId")
                        if not mod_version:
                            mod_version = str(first_mod.get("version", "")) if first_mod.get("version") else None
                        if not mod_description:
                            mod_description = _normalize_description(first_mod.get("description"))
                        if not mod_author:
                            mod_author = _normalize_author_list(first_mod.get("authors"))
                        if not icon_path_in_jar:
                            icon_path_in_jar = first_mod.get("logoFile")
                except Exception as e:
                    logger.debug(f"读取 META-INF/mods.toml 失败 ({jar_path.name}): {e}")

            # ── 尝试 mcmod.info (旧版 Forge) ──
            if "mcmod.info" in namelist and not (mod_id and mod_name):
                try:
                    with zf.open("mcmod.info") as f:
                        mcmod_data = _json_module.load(f)
                    if isinstance(mcmod_data, list) and mcmod_data:
                        info = mcmod_data[0]
                    elif isinstance(mcmod_data, dict):
                        info = mcmod_data.get("modList", [{}]) if isinstance(mcmod_data.get("modList"), list) else {}
                        if isinstance(info, list) and info:
                            info = info[0]
                        else:
                            info = {}
                    else:
                        info = {}
                    if not mod_name:
                        mod_name = info.get("name")
                    if not mod_id:
                        mod_id = info.get("modid")
                    if not mod_version:
                        mod_version = str(info.get("version", "")) if info.get("version") else None
                    if not mod_description:
                        mod_description = _normalize_description(info.get("description"))
                    if not mod_author:
                        authors_list = info.get("authorList", [])
                        if authors_list:
                            mod_author = _normalize_author_list(authors_list)
                        elif info.get("authors"):
                            mod_author = _normalize_author_list(info.get("authors"))
                        elif info.get("author"):
                            mod_author = _normalize_author(info.get("author"))
                    if not icon_path_in_jar:
                        icon_path_in_jar = info.get("logoFile")
                except Exception as e:
                    logger.debug(f"读取 mcmod.info 失败 ({jar_path.name}): {e}")

            # ── 回退：从文件名猜测 ──
            if not mod_name:
                mod_name = jar_path.stem

            # ── 提取图标 ──
            if icon_path_in_jar:
                icon_path_in_jar = icon_path_in_jar.lstrip("/")
                if icon_path_in_jar in namelist:
                    try:
                        with zf.open(icon_path_in_jar) as img_f:
                            img_data = img_f.read()
                            if img_data:
                                icon_base64 = base64.b64encode(img_data).decode("ascii")
                    except Exception as e:
                        logger.debug(f"提取图标失败 ({jar_path.name}): {e}")
                else:
                    # 尝试在 assets/ 下递归查找
                    for alt_name in namelist:
                        if alt_name.endswith(icon_path_in_jar) or icon_path_in_jar in alt_name:
                            try:
                                with zf.open(alt_name) as img_f:
                                    img_data = img_f.read()
                                    if img_data:
                                        icon_base64 = base64.b64encode(img_data).decode("ascii")
                                        break
                            except Exception:
                                pass

            return {
                "name": mod_name or jar_path.stem,
                "modid": mod_id or "",
                "version": mod_version or "",
                "author": mod_author or "",
                "description": mod_description or "",
                "icon_base64": icon_base64,
            }

    except (zipfile.BadZipFile, Exception) as e:
        logger.debug(f"无法读取 jar 文件 {jar_path.name}: {e}")
        return None


def extract_all_mods_metadata(mods_dir: Path, status_callback=None) -> List[Dict]:
    """
    批量提取模组目录中所有 jar 的元数据

    Args:
        mods_dir: 模组目录路径
        status_callback: 状态回调函数

    Returns:
        元数据列表，每项包含 name, modid, author, description, icon_base64, path, size, disabled
    """
    results = []
    if not mods_dir.exists():
        return results

    entries = sorted(mods_dir.iterdir())
    total = len([e for e in entries if e.is_file() and e.suffix.lower() in {".jar", ".zip"} or
                 (e.suffix.lower() == ".disabled" and len(e.suffixes) >= 2 and e.suffixes[-2].lower() in {".jar", ".zip"})])

    processed = 0
    for entry in entries:
        if not entry.is_file():
            continue

        is_disabled = entry.suffix.lower() == ".disabled"
        actual_ext = entry.suffixes[-2].lower() if is_disabled and len(entry.suffixes) >= 2 else entry.suffix.lower()
        if actual_ext not in {".jar", ".zip"}:
            continue

        processed += 1
        if status_callback:
            status_callback(processed, total)

        try:
            size = entry.stat().st_size
        except Exception:
            size = 0

        metadata = extract_mod_metadata(entry)
        if metadata is None:
            metadata = {
                "name": entry.name,
                "modid": "",
                "version": "",
                "author": "",
                "description": "",
                "icon_base64": None,
            }

        metadata["path"] = str(entry)
        metadata["size"] = size
        metadata["disabled"] = is_disabled
        metadata["filename"] = entry.name
        results.append(metadata)

    return results


def search_project_by_slug(slug: str) -> Optional[Dict]:
    """
    通过 slug（通常对应 modid）搜索 Modrinth 项目

    Modrinth API 支持直接通过 slug 访问项目详情，
    如 /v2/project/sodium 返回钠模组的信息。

    Args:
        slug: 项目 slug，通常为模组的 modid

    Returns:
        项目信息字典，包含 id, title, slug, versions 等；失败返回 None
    """
    try:
        session = _get_session()
        resp = session.get(
            f"{MODRINTH_API_BASE}/project/{slug}",
            timeout=15,
        )
        if resp.status_code == 404:
            logger.debug(f"未找到 slug={slug} 的 Modrinth 项目")
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.RequestException as e:
        logger.warning(f"搜索项目失败 (slug={slug}): {e}")
        return None


def get_project_latest_version(
    project_id: str,
    game_version: Optional[str] = None,
    mod_loader: Optional[str] = None,
    version_type: str = "release",
) -> Optional[Dict]:
    """
    获取项目的特定游戏版本和加载器的最新版本信息

    优先通过 API 参数筛选，API 返回的版本列表默认按日期降序排列，
    所以只需取第一个。

    Args:
        project_id: Modrinth 项目 ID
        game_version: 游戏版本 (如 "1.20.4")
        mod_loader: 模组加载器 (如 "fabric", "forge", "neoforge")
        version_type: 版本类型 ("release", "beta", "alpha")，默认 "release"

    Returns:
        最新版本信息字典，或 None
    """
    import json as _json

    params: Dict = {}

    if game_version:
        params["game_versions"] = _json.dumps([game_version])

    if mod_loader:
        params["loaders"] = _json.dumps([mod_loader])

    try:
        session = _get_session()
        resp = session.get(
            f"{MODRINTH_API_BASE}/project/{project_id}/version",
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        versions = resp.json()

        if not versions:
            logger.debug(f"项目 {project_id} 没有兼容版本 (game={game_version}, loader={mod_loader})")
            return None

        # 按 version_type 筛选
        for v in versions:
            if v.get("version_type") == version_type:
                return v

        # 如果没有匹配的 version_type，返回第一个（最新）
        return versions[0]

    except requests.exceptions.RequestException as e:
        logger.warning(f"获取项目 {project_id} 版本失败: {e}")
        return None


def compare_mod_versions(current: str, latest: str) -> int:
    """
    比较两个模组版本号

    支持 semver（如 1.0.0, 2.1.0-beta.1）和简单版本号。

    Args:
        current: 当前版本号
        latest: 最新版本号

    Returns:
        -1: current < latest (需要更新)
         0: current == latest (相同)
         1: current > latest (无需更新，当前更新)
         None: 无法比较
    """
    if not current or not latest:
        return None

    if current == latest:
        return 0

    def _parse_semver(v: str):
        """将版本号解析为 (主版本, 次版本, 修订号, 预发布标签)"""
        import re
        # 匹配: 数字.数字.数字[-标签] 或 数字.数字[-标签]
        match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?(?:[.\-](.+))?$", v.strip())
        if match:
            major = int(match.group(1))
            minor = int(match.group(2))
            patch = int(match.group(3)) if match.group(3) else 0
            prerelease = match.group(4) or ""
            return (major, minor, patch), prerelease
        return None

    current_parsed = _parse_semver(current)
    latest_parsed = _parse_semver(latest)

    if current_parsed and latest_parsed:
        cur_nums, cur_pre = current_parsed
        lat_nums, lat_pre = latest_parsed

        # 比较数字部分
        for c, l in zip(cur_nums, lat_nums):
            if c < l:
                return -1
            if c > l:
                return 1

        # 比较预发布标签
        if not cur_pre and not lat_pre:
            return 0
        if not cur_pre and lat_pre:
            return 1  # 当前是正式版，最新是预发布 → 无需更新
        if cur_pre and not lat_pre:
            return -1  # 当前是预发布，最新是正式版 → 需要更新
        if cur_pre < lat_pre:
            return -1
        if cur_pre > lat_pre:
            return 1
        return 0

    # 回退：按字符串字母序比较（比不比较要好）
    if current < latest:
        return -1
    elif current > latest:
        return 1
    return 0


def extract_zip_thumbnail(zip_path: Path, max_size: int = 64) -> Optional[str]:
    """
    从 zip 文件中提取预览缩略图（base64 编码）

    对于资源包：查找 pack.png
    对于光影：查找常见的预览图文件名
    同时也会尝试查找根目录下的任何 .png 文件作为回退。

    Args:
        zip_path: zip 文件路径
        max_size: 缩略图最大边长（像素）

    Returns:
        base64 编码的 PNG 图片数据，或 None
    """
    import base64
    import zipfile
    from io import BytesIO

    try:
        from PIL import Image
    except ImportError:
        return None

    if not zip_path.exists() or not zip_path.is_file():
        return None

    # 常见预览图文件名（按优先级排序）
    preview_names = [
        "pack.png",
        "preview.png",
        "preview.jpg",
        "preview.jpeg",
        "thumbnail.png",
        "icon.png",
        "screenshot.png",
        "banner.png",
    ]

    try:
        with zipfile.ZipFile(str(zip_path), "r") as zf:
            namelist = zf.namelist()

            # 先按优先级查找
            for name in preview_names:
                if name in namelist:
                    try:
                        with zf.open(name) as img_f:
                            img_data = img_f.read()
                            if img_data:
                                img = Image.open(BytesIO(img_data))
                                img.thumbnail((max_size, max_size), Image.LANCZOS)
                                buf = BytesIO()
                                img.save(buf, format="PNG")
                                return base64.b64encode(buf.getvalue()).decode("ascii")
                    except Exception:
                        continue

            # 回退：查找根目录下的任意 .png 文件
            root_pngs = sorted([
                n for n in namelist
                if n.lower().endswith(".png") and "/" not in n.rstrip("/")
            ])
            for png_name in root_pngs:
                try:
                    with zf.open(png_name) as img_f:
                        img_data = img_f.read()
                        if img_data:
                            img = Image.open(BytesIO(img_data))
                            img.thumbnail((max_size, max_size), Image.LANCZOS)
                            buf = BytesIO()
                            img.save(buf, format="PNG")
                            return base64.b64encode(buf.getvalue()).decode("ascii")
                except Exception:
                    continue

    except (zipfile.BadZipFile, Exception) as e:
        logger.debug(f"提取 zip 缩略图失败 ({zip_path.name}): {e}")

    return None
