"""插件市场模块 - 从 GitHub 仓库获取插件列表并下载安装

数据流:
    1. 从 GitHub raw 获取 index.json
    2. 本地缓存 + TTL 过期机制
    3. 搜索/筛选/排序在本地完成
    4. 安装时从 GitHub API 下载插件源码 → 本地打包 .fmpl → PluginManager 安装
"""

import hashlib
import json
import os
import tempfile
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable

import requests
from logzero import logger


# GitHub 仓库配置
DEFAULT_REPO = "Janson20/FMCL-Plugins"
DEFAULT_BRANCH = "main"
INDEX_URL_TEMPLATE = "https://raw.githubusercontent.com/{repo}/{branch}/index.json"
GITHUB_API_CONTENTS = "https://api.github.com/repos/{repo}/contents/{path}"
GITHUB_API_RAW = "https://raw.githubusercontent.com/{repo}/{branch}/{path}"

REQUEST_TIMEOUT = 20
INDEX_CACHE_TTL = 3600  # 1 小时缓存
USER_AGENT = "FMCL-MinecraftLauncher/2.0 (github.com/Janson20/FMCL)"


class PluginMarket:
    """插件市场 - 从 GitHub 获取插件列表并提供搜索下载"""

    def __init__(
        self,
        cache_dir: Path,
        repo: str = DEFAULT_REPO,
        branch: str = DEFAULT_BRANCH,
    ):
        """
        Args:
            cache_dir: 缓存目录 (如 plugins/cache/)
            repo: GitHub 仓库全名
            branch: 仓库分支
        """
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._repo = repo
        self._branch = branch
        self._index_cache_path = self._cache_dir / "market_index.json"
        self._index_data: Optional[dict] = None
        self._plugin_list: List[dict] = []
        self._lock = threading.RLock()

    # ── 获取插件列表 ──

    def fetch_index(self, force: bool = False) -> Tuple[List[dict], str]:
        """获取插件市场索引

        Args:
            force: 是否强制刷新（忽略缓存）

        Returns:
            (plugin_list, error_message)
        """
        with self._lock:
            # 检查缓存
            if not force and self._is_cache_valid():
                try:
                    raw = self._index_cache_path.read_text(encoding="utf-8")
                    data = json.loads(raw)
                    plugins = data.get("plugins", [])
                    self._plugin_list = plugins
                    return plugins, ""
                except Exception as e:
                    logger.warning(f"读取缓存索引失败，将重新获取: {e}")

            # 从 GitHub 获取
            url = INDEX_URL_TEMPLATE.format(repo=self._repo, branch=self._branch)
            logger.info(f"正在获取插件索引: {url}")

            try:
                resp = requests.get(
                    url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "application/json",
                    },
                    timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                err_msg = f"获取插件索引失败: {e}"
                logger.error(err_msg)
                # 返回缓存数据作为降级方案
                if self._is_cache_valid():
                    logger.warning("网络失败，使用缓存索引")
                    raw = self._index_cache_path.read_text(encoding="utf-8")
                    data = json.loads(raw)
                else:
                    return self._plugin_list, err_msg

            # 移除校验错误字段（不向客户端暴露）
            data.pop("_validation_errors", None)

            # 写入缓存
            data["_cached_at"] = datetime.now(timezone.utc).isoformat()
            self._index_cache_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            plugins = data.get("plugins", [])
            self._plugin_list = plugins
            logger.info(f"插件索引更新: {len(plugins)} 个插件")
            return plugins, ""

    def search(self, query: str = "", tags: Optional[List[str]] = None) -> List[dict]:
        """搜索/筛选插件

        Args:
            query: 搜索关键词（匹配 name + id + description）
            tags: 按标签筛选

        Returns:
            匹配的插件列表
        """
        results = list(self._plugin_list)

        if tags:
            tag_set = set(tags)
            results = [
                p for p in results
                if tag_set.intersection(set(p.get("tags", [])))
            ]

        if query:
            q_lower = query.lower()
            filtered = []
            for p in results:
                name = p.get("name", "").lower()
                pid = p.get("id", "").lower()
                desc = p.get("description", {})
                desc_text = ""
                for lang in ("zh_CN", "en_US"):
                    if lang in desc:
                        desc_text += desc[lang].lower() + " "
                author = p.get("author", "").lower()

                if (
                    q_lower in name
                    or q_lower in pid
                    or q_lower in desc_text
                    or q_lower in author
                ):
                    filtered.append(p)

            results = filtered

        # 默认按名称排序
        results.sort(key=lambda p: p.get("name", "").lower())
        return results

    def sort_by(self, plugins: List[dict], key: str = "name", reverse: bool = False) -> List[dict]:
        """排序插件列表

        Args:
            plugins: 插件列表
            key: 排序字段 (name, author, version)
            reverse: 是否倒序
        """
        if key == "name":
            return sorted(plugins, key=lambda p: p.get("name", "").lower(), reverse=reverse)
        if key == "author":
            return sorted(plugins, key=lambda p: p.get("author", "").lower(), reverse=reverse)
        return plugins

    # ── 下载与安装 ──

    def download_plugin(
        self,
        plugin_id: str,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Tuple[Optional[str], str]:
        """从 GitHub 下载插件源码，打包为 .fmpl 临时文件

        Args:
            plugin_id: 插件 ID
            progress_callback: 进度回调 (stage_name, current, total)

        Returns:
            (临时 .fmpl 文件路径, 错误信息)
        """
        # 1. 获取插件目录文件列表
        path = f"plugins/{plugin_id}"
        if progress_callback:
            progress_callback("listing", 0, 1)

        files = self._list_github_dir(path)
        if not files:
            return None, f"插件 '{plugin_id}' 在仓库中不存在或无法访问"

        # 2. 下载所有文件到临时目录
        total = len(files)
        tmp_dir = Path(tempfile.mkdtemp(prefix=f"fmcl_plugin_{plugin_id}_"))

        try:
            for i, file_info in enumerate(files):
                file_path = file_info["path"]
                download_url = file_info["download_url"]
                if not download_url:
                    continue

                if progress_callback:
                    progress_callback("downloading", i + 1, total)

                # 下载单个文件
                resp = requests.get(
                    download_url,
                    headers={"User-Agent": USER_AGENT},
                    timeout=REQUEST_TIMEOUT,
                )
                resp.raise_for_status()

                # 计算相对路径并保存
                rel_path = file_path.replace(f"{path}/", "")
                target = tmp_dir / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(resp.content)

            # 3. 校验 plugin.json 存在
            if not (tmp_dir / "plugin.json").exists():
                return None, "下载的插件缺少 plugin.json"

            # 4. 打包为 .fmpl
            if progress_callback:
                progress_callback("packing", 0, 1)

            fmpl_path = self._cache_dir / f"{plugin_id}.fmpl"
            self._create_fmpl(tmp_dir, fmpl_path)

            logger.info(f"插件 {plugin_id} 下载完成: {fmpl_path}")

            if progress_callback:
                progress_callback("done", 1, 1)

            return str(fmpl_path), ""

        except requests.RequestException as e:
            return None, f"下载失败: {e}"
        except Exception as e:
            return None, f"打包失败: {e}"
        finally:
            # 清理临时目录
            import shutil
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)

    def get_plugin_detail(self, plugin_id: str) -> Optional[dict]:
        """获取单个插件的详细信息

        Args:
            plugin_id: 插件 ID

        Returns:
            插件信息 dict，或 None
        """
        for p in self._plugin_list:
            if p.get("id") == plugin_id:
                return p
        return None

    def get_available_tags(self) -> Dict[str, str]:
        """获取可用标签分类"""
        if self._index_data is None:
            return {}
        return self._index_data.get("categories", {})

    # ── 内部方法 ──

    def _is_cache_valid(self) -> bool:
        """检查本地缓存是否有效"""
        if not self._index_cache_path.exists():
            return False
        try:
            raw = self._index_cache_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            cached_at_str = data.get("_cached_at", "")
            if not cached_at_str:
                return False
            cached_at = datetime.fromisoformat(cached_at_str)
            age = (datetime.now(timezone.utc) - cached_at).total_seconds()
            return age < INDEX_CACHE_TTL
        except Exception:
            return False

    def _list_github_dir(self, path: str) -> List[dict]:
        """通过 GitHub API 列出目录内容

        Args:
            path: 仓库中的相对路径

        Returns:
            [{path, download_url, type, ...}]
        """
        results = []
        url = GITHUB_API_CONTENTS.format(repo=self._repo, path=path)

        try:
            resp = requests.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/vnd.github.v3+json",
                },
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            contents = resp.json()

            if isinstance(contents, list):
                for item in contents:
                    if item.get("type") == "file":
                        results.append({
                            "path": item["path"],
                            "download_url": item.get("download_url", ""),
                            "size": item.get("size", 0),
                        })
                    elif item.get("type") == "dir":
                        # 递归获取子目录
                        results.extend(self._list_github_dir(item["path"]))
        except Exception as e:
            logger.warning(f"列出 GitHub 目录失败 ({path}): {e}")

        return results

    def _create_fmpl(self, src_dir: Path, dest_path: Path):
        """将插件源码目录打包为 .fmpl 文件 (zip)"""
        if dest_path.exists():
            dest_path.unlink()

        with zipfile.ZipFile(
            dest_path, "w",
            compression=zipfile.ZIP_DEFLATED,
        ) as zf:
            for file_path in sorted(src_dir.rglob("*")):
                if file_path.is_file():
                    arcname = str(file_path.relative_to(src_dir))
                    zf.write(file_path, arcname)
