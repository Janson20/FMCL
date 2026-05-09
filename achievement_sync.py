"""成就云同步模块 - Achievement Cloud Sync

下载/上传 achievements.db 到净读 API，支持冲突合并。
"""

import time
import sqlite3
import threading
from pathlib import Path
from typing import Optional, Callable

import requests
from logzero import logger

STORAGE_API = "https://jingdu.qzz.io/api/db/storage"
MAX_SIZE = 1 * 1024 * 1024
RATE_LIMIT_WINDOW = 3600
MAX_REQUESTS_PER_WINDOW = 30

_request_times: list[float] = []
_request_lock = threading.Lock()


def _check_rate_limit() -> bool:
    now = time.time()
    with _request_lock:
        global _request_times
        _request_times = [t for t in _request_times if now - t < RATE_LIMIT_WINDOW]
        if len(_request_times) >= MAX_REQUESTS_PER_WINDOW:
            return False
        _request_times.append(now)
        return True


def _make_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "FMCL": "true",
        "User-Agent": "FMCL/1.0 (Minecraft Launcher; achievement-sync)",
    }


def download_db(token: str) -> Optional[bytes]:
    """GET /api/db/storage - 下载当前用户的成就数据库

    Returns:
        数据库文件 bytes，失败返回 None
    """
    if not _check_rate_limit():
        logger.warning("成就同步: 频率限制, 跳过下载")
        return None

    try:
        resp = requests.get(STORAGE_API, headers=_make_headers(token), timeout=15)
        if resp.status_code == 200:
            data = resp.content
            if len(data) > MAX_SIZE:
                logger.warning(f"成就同步: 下载文件过大 ({len(data)} bytes), 已忽略")
                return None
            logger.info(f"成就同步: 下载成功 ({len(data)} bytes)")
            return data
        elif resp.status_code == 404:
            logger.info("成就同步: 服务器无存档")
            return None
        else:
            logger.warning(f"成就同步: 下载失败 HTTP {resp.status_code}")
            return None
    except requests.RequestException as e:
        logger.warning(f"成就同步: 下载异常: {e}")
        return None


def upload_db(token: str, data: bytes) -> bool:
    """POST /api/db/storage - 上传成就数据库（覆盖旧文件，multipart/form-data）

    Args:
        token: 认证令牌
        data: .db 文件内容

    Returns:
        是否上传成功
    """
    if not _check_rate_limit():
        logger.warning("成就同步: 频率限制, 跳过上传")
        return False

    if len(data) > MAX_SIZE:
        logger.warning(f"成就同步: 上传文件过大 ({len(data)} bytes), 已忽略")
        return False

    try:
        resp = requests.post(
            STORAGE_API,
            headers=_make_headers(token),
            files={"file": ("achievements.db", data, "application/octet-stream")},
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.info(f"成就同步: 上传成功 ({len(data)} bytes)")
            return True
        else:
            body = resp.text[:200]
            logger.warning(f"成就同步: 上传失败 HTTP {resp.status_code}: {body}")
            return False
    except requests.RequestException as e:
        logger.warning(f"成就同步: 上传异常: {e}")
        return False


def _cleanup_wal_shm(db_path: Path):
    wal_path = db_path.with_suffix(db_path.suffix + "-wal")
    shm_path = db_path.with_suffix(db_path.suffix + "-shm")
    for p in (wal_path, shm_path):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def run_sync(token: str, db_path: Path,
             on_status: Optional[Callable[[str], None]] = None,
             engine: object = None) -> bool:
    """执行完整同步流程: 下载 → 合并 → 上传

    当传入 engine 时，所有数据库操作通过 engine._lock 串行化，
    避免与 AchievementEngine 的其他操作产生锁冲突。

    Args:
        token: 认证令牌
        db_path: 本地 .db 文件路径
        on_status: 状态回调
        engine: AchievementEngine 实例（可选），传入后自动处理锁同步和 last_sync_time

    Returns:
        是否同步成功（无远程数据时仅上传也算成功）
    """
    if on_status:
        on_status("syncing_download")

    remote_data = download_db(token)

    if remote_data is not None:
        if on_status:
            on_status("syncing_merge")

        from achievement_engine import _do_merge_db

        if engine is not None:
            with engine._lock:
                merged = _do_merge_db(db_path, remote_data)
                if merged is not None:
                    db_path.parent.mkdir(parents=True, exist_ok=True)
                    db_path.write_bytes(merged)
                    _cleanup_wal_shm(db_path)
                    logger.info("成就同步: 合并完成")
                else:
                    logger.warning("成就同步: 合并失败，仅上传本地数据")
                    remote_data = None
        else:
            merged = _do_merge_db(db_path, remote_data)
            if merged is not None:
                db_path.parent.mkdir(parents=True, exist_ok=True)
                db_path.write_bytes(merged)
                _cleanup_wal_shm(db_path)
                logger.info("成就同步: 合并完成")
            else:
                logger.warning("成就同步: 合并失败，仅上传本地数据")
                remote_data = None

    if on_status:
        on_status("syncing_upload")

    if db_path.exists():
        if engine is not None:
            with engine._lock:
                with sqlite3.connect(str(db_path)) as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                local_data = db_path.read_bytes()
        else:
            with sqlite3.connect(str(db_path)) as conn:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            local_data = db_path.read_bytes()

        success = upload_db(token, local_data)

        if engine is not None and success:
            engine.set_last_sync_time(time.time())

        if on_status:
            on_status("sync_success" if success else "syncing_failed")
        return success
    else:
        if on_status:
            on_status("syncing_failed")
        return False


def reset_cloud_db(token: str) -> bool:
    """重置云存档（上传空数据库）"""
    from achievement_engine import AchievementEngine
    import tempfile

    temp_dir = Path(tempfile.mkdtemp())
    temp_db = temp_dir / "achievements.db"
    try:
        engine = AchievementEngine(temp_dir)
        data = temp_db.read_bytes()
        return upload_db(token, data)
    finally:
        try:
            temp_db.unlink(missing_ok=True)
            temp_dir.rmdir()
        except Exception:
            pass
