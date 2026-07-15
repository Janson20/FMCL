"""模组分类器 — 分类编排入口

提供 classify_mods() 和 classify_mods_in_directory() 两个核心入口。
支持并发分类并返回结构化的结果。
"""

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from logzero import logger

from launcher.mod_classifier.metadata import read_jar_metadata
from launcher.mod_classifier.query import lookup_modrinth
from launcher.mod_classifier.rules import classify_local
from launcher.mod_classifier.shared import CATEGORY_LABELS, Classification, ModMeta


class ClassificationResult:
    """一批模组的分类结果"""

    def __init__(self, rows: List[Dict]):
        self.rows = rows

    @property
    def server_keep(self) -> List[Dict]:
        """应保留在服务端的模组"""
        return [r for r in self.rows if r["category"] == "server-keep"]

    @property
    def client_only(self) -> List[Dict]:
        """纯客户端模组"""
        return [r for r in self.rows if r["category"] == "client-only"]

    @property
    def unknown(self) -> List[Dict]:
        """无法确定的模组"""
        return [r for r in self.rows if r["category"] == "unknown"]

    @property
    def summary(self) -> str:
        """返回可读的统计摘要"""
        total = len(self.rows)
        sk = len(self.server_keep)
        co = len(self.client_only)
        un = len(self.unknown)
        return (
            f"共 {total} 个模组: "
            f"{CATEGORY_LABELS['server-keep']} {sk} 个, "
            f"{CATEGORY_LABELS['client-only']} {co} 个, "
            f"{CATEGORY_LABELS['unknown']} {un} 个"
        )

    def print_report(self) -> str:
        """生成完整的分类报告文本"""
        lines = [f"📋 模组分类报告", "=" * 40]
        lines.append(f"总计: {len(self.rows)} 个模组\n")

        for category, label in [
            ("server-keep", "✅ 服务端保留"),
            ("client-only", "❌ 纯客户端"),
            ("unknown", "⚠️  待确认"),
        ]:
            items = [r for r in self.rows if r["category"] == category]
            if not items:
                continue
            lines.append(f"\n--- {label} ({len(items)} 个) ---")
            for r in items:
                src = r.get("source", "?")
                reason = r.get("reason", "")
                lines.append(f"  {r['file_name']}  [{src}] {reason}")

        return "\n".join(lines)


def _classify_single(jar_path: Path, use_online: bool, cache: Dict, lock: threading.Lock) -> Dict:
    """单个 jar 的分类流程：本地 → 远程"""
    file_name = jar_path.name

    # 跳过非 .jar 文件
    if not file_name.lower().endswith(".jar"):
        return {
            "file_name": file_name,
            "file_path": str(jar_path),
            "category": "unknown",
            "source": "skip",
            "reason": "非 jar 文件",
        }

    # 已 disabled 的直接跳过
    if file_name.endswith(".disabled"):
        return {
            "file_name": file_name,
            "file_path": str(jar_path),
            "category": "skip",
            "source": "skip",
            "reason": "已禁用",
        }

    # 1. 读取元数据
    meta = read_jar_metadata(jar_path)

    # 2. 本地分类
    local_result = classify_local(meta)
    if local_result.category != "unknown":
        return _to_dict(meta, local_result)

    # 3. 远程查询（Modrinth）
    if use_online:
        try:
            remote_result = lookup_modrinth(meta, cache)
            if remote_result and remote_result.category != "unknown":
                return _to_dict(meta, remote_result)
        except Exception as e:
            logger.debug(f"Modrinth 查询失败 [{file_name}]: {e}")

    return _to_dict(meta, local_result)


def _to_dict(meta: ModMeta, cls: Classification) -> Dict:
    return {
        "file_name": meta.file_name,
        "file_path": meta.file_path,
        "mod_id": meta.mod_id,
        "mod_name": meta.mod_name,
        "loader": meta.loader,
        "category": cls.category,
        "source": cls.source,
        "reason": cls.reason,
        "evidence_url": cls.evidence_url,
        "jar_status": meta.jar_status,
        "jar_issue": meta.jar_issue,
    }


def classify_mods(
    jar_files: Sequence[Path],
    *,
    use_online: bool = True,
    max_workers: int = 5,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> ClassificationResult:
    """批量分类模组

    Args:
        jar_files: .jar 文件路径列表
        use_online: 是否使用 Modrinth API 在线查询（用于补全本地无法确定的模组）
        max_workers: 并发线程数
        progress_callback: 进度回调 (completed, total)

    Returns:
        ClassificationResult 对象
    """
    total = len(jar_files)
    results: List[Optional[Dict]] = [None] * total
    completed = 0
    lock = threading.Lock()
    cache: Dict[str, str] = {}

    def _finish(idx: int, row: Dict):
        nonlocal completed
        results[idx] = row
        completed += 1
        if progress_callback:
            progress_callback(completed, total)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {}
        for i, jp in enumerate(jar_files):
            future = executor.submit(_classify_single, jp, use_online, cache, lock)
            futures[future] = i

        for future in as_completed(futures):
            idx = futures[future]
            try:
                row = future.result()
                _finish(idx, row)
            except Exception as e:
                _finish(
                    idx,
                    {
                        "file_name": jar_files[idx].name,
                        "file_path": str(jar_files[idx]),
                        "mod_id": "",
                        "mod_name": "",
                        "loader": "unknown",
                        "category": "unknown",
                        "source": "error",
                        "reason": str(e)[:120],
                        "evidence_url": "",
                        "jar_status": "error",
                        "jar_issue": str(e)[:120],
                    },
                )

    return ClassificationResult([r for r in results if r is not None])


def classify_mods_in_directory(
    mods_dir: str,
    *,
    use_online: bool = True,
    max_workers: int = 5,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> ClassificationResult:
    """分类指定目录中的所有 .jar 文件

    Args:
        mods_dir: mods 目录路径
        use_online: 是否使用 Modrinth API 在线查询
        max_workers: 并发线程数
        progress_callback: 进度回调 (completed, total)

    Returns:
        ClassificationResult 对象
    """
    dir_path = Path(mods_dir)
    if not dir_path.is_dir():
        return ClassificationResult([])

    jar_files = sorted(dir_path.glob("*.jar"))
    return classify_mods(jar_files, use_online=use_online, max_workers=max_workers, progress_callback=progress_callback)


def filter_server_mods(
    mods_dir: str,
    *,
    use_online: bool = True,
    max_workers: int = 5,
    dry_run: bool = False,
    progress_callback: Optional[Callable[[int, int, str], None]] = None,
) -> ClassificationResult:
    """筛选并整理服务端模组目录

    对 mods 目录中的 .jar 文件进行分类：
    - server-keep: 保留不变
    - client-only: 重命名为 .jar.disabled（不删除，方便恢复）
    - unknown: 保留不变但记录到报告

    Args:
        mods_dir: mods 目录路径
        use_online: 是否启用在线查询
        max_workers: 并发线程数
        dry_run: 仅预览，不实际修改文件
        progress_callback: 进度回调 (completed, total, filename)

    Returns:
        ClassificationResult 对象
    """

    def _progress(completed: int, total: int):
        if progress_callback:
            progress_callback(completed, total, "")

    result = classify_mods_in_directory(
        mods_dir, use_online=use_online, max_workers=max_workers, progress_callback=_progress
    )

    # 处理纯客户端模组
    disabled_count = 0
    for row in result.client_only:
        jar_path = Path(row["file_path"])
        if not jar_path.exists():
            continue
        disabled_path = jar_path.with_name(jar_path.name + ".disabled")
        if dry_run:
            logger.info(f"[DRY RUN] 禁用客户端模组: {jar_path.name}")
        else:
            try:
                os.rename(str(jar_path), str(disabled_path))
                row["file_path"] = str(disabled_path)
                logger.info(f"已禁用客户端模组: {jar_path.name} → {disabled_path.name}")
                disabled_count += 1
                if progress_callback:
                    progress_callback(0, 0, f"已禁用: {jar_path.name}")
            except Exception as e:
                logger.warning(f"禁用模组失败 [{jar_path.name}]: {e}")

    if disabled_count > 0:
        logger.info(f"共禁用 {disabled_count} 个纯客户端模组")

    return result
