"""基岩版游戏包多线程下载与 MD5 校验"""

import hashlib
import threading
import time
from pathlib import Path
from typing import Callable, List, Optional

import requests
from logzero import logger

from launcher.bedrock.source import USER_AGENT

DEFAULT_THREADS = 8
CHUNK_SIZE = 1024 * 256


class DownloadError(RuntimeError):
    """下载失败"""


def _download_part(
    url: str,
    start: int,
    end: int,
    part_file: Path,
    stop_event: threading.Event,
    timeout: int,
) -> None:
    """下载单个分片到 part 文件"""
    headers = {"User-Agent": USER_AGENT, "Range": f"bytes={start}-{end}"}
    with requests.get(url, headers=headers, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        part_file.parent.mkdir(parents=True, exist_ok=True)
        with open(part_file, "wb") as f:
            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                if stop_event.is_set():
                    raise DownloadError("下载已取消")
                if chunk:
                    f.write(chunk)


def download_file(
    urls: List[str],
    dest: Path,
    progress_cb: Optional[Callable[[int, int], None]] = None,
    threads: int = DEFAULT_THREADS,
    stop_event: Optional[threading.Event] = None,
    timeout: int = 30,
) -> Path:
    """多线程下载文件，URL 列表按顺序回退（镜像）

    Args:
        urls: 候选下载地址列表（依次尝试）
        dest: 目标文件路径
        progress_cb: 进度回调 (已下载字节, 总字节)
        threads: 分片线程数
        stop_event: 取消事件
        timeout: 单请求超时（秒）
    """
    stop_event = stop_event or threading.Event()
    last_error: Optional[Exception] = None
    for url in urls:
        try:
            return _download_from_url(url, dest, progress_cb, threads, stop_event, timeout)
        except Exception as e:
            last_error = e
            logger.warning(f"下载源 {url} 失败: {e}")
    raise DownloadError(f"所有下载源均失败: {last_error}")


def _download_from_url(
    url: str,
    dest: Path,
    progress_cb: Optional[Callable[[int, int], None]],
    threads: int,
    stop_event: threading.Event,
    timeout: int,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)

    head_headers = {"User-Agent": USER_AGENT}
    resp = requests.head(url, headers=head_headers, timeout=timeout, allow_redirects=True)
    resp.raise_for_status()
    total = int(resp.headers.get("Content-Length", 0) or 0)
    if total <= 0:
        raise DownloadError(f"无法获取文件大小: {url}")

    part_size = max(1, total // threads)
    ranges = []
    for i in range(threads):
        start = i * part_size
        end = start + part_size - 1 if i < threads - 1 else total - 1
        ranges.append((start, end))

    part_files = [dest.with_name(f"{dest.name}.part{i}") for i in range(threads)]
    errors: List[Exception] = []
    lock = threading.Lock()

    def _worker(start: int, end: int, part_file: Path) -> None:
        try:
            _download_part(url, start, end, part_file, stop_event, timeout)
        except Exception as e:
            with lock:
                errors.append(e)

    workers = []
    for start, end in ranges:
        worker = threading.Thread(target=_worker, args=(start, end, part_files[len(workers)]))
        workers.append(worker)
        worker.start()

    while any(w.is_alive() for w in workers):
        if stop_event.is_set():
            raise DownloadError("下载已取消")
        current = sum(f.stat().st_size for f in part_files if f.exists())
        if progress_cb:
            progress_cb(min(current, total), total)
        time.sleep(0.1)

    if errors:
        raise DownloadError(f"下载分片失败: {errors[0]}")

    with open(dest, "wb") as out:
        for part_file in part_files:
            with open(part_file, "rb") as pf:
                while True:
                    block = pf.read(CHUNK_SIZE)
                    if not block:
                        break
                    out.write(block)
            part_file.unlink(missing_ok=True)

    if dest.stat().st_size != total:
        raise DownloadError(f"文件大小不匹配: 期望 {total}, 实际 {dest.stat().st_size}")
    if progress_cb:
        progress_cb(total, total)
    logger.info(f"下载完成: {dest.name} ({total} 字节)")
    return dest


def check_md5(file_path: Path, expected: Optional[str], chunk_size: int = 1024 * 1024) -> bool:
    """校验文件 MD5（expected 为空时跳过校验）"""
    if not expected:
        return True
    expected = expected.strip().lower()
    if not expected:
        return True
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            hasher.update(block)
    actual = hasher.hexdigest().lower()
    if actual != expected:
        logger.error(f"MD5 校验失败: 期望 {expected}, 实际 {actual} ({file_path.name})")
        return False
    return True


def verify_variation_md5(file_path: Path, variation: Optional[dict]) -> bool:
    """校验变体条目中声明的 MD5"""
    md5s = []
    if variation and variation.get("MD5"):
        md5s.append(variation["MD5"])
    if not md5s:
        return True
    hasher = hashlib.md5()
    with open(file_path, "rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            hasher.update(block)
    actual = hasher.hexdigest().lower()
    for md5 in md5s:
        if md5.strip().lower() == actual:
            return True
    logger.error(f"MD5 校验失败: 期望 {md5s}, 实际 {actual} ({file_path.name})")
    return False
