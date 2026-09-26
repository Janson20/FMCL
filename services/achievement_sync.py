"""成就云同步模块 - Achievement Cloud Sync

下载/上传 achievements.db 到净读 API，支持冲突合并。
D-113（任务 1.24）：落盘/校验/记账三件事都改走 services/achievement_db.py。"""

from services.achievement_db import build_empty_db_bytes, dump_db_bytes, record_last_sync_time
import threading
import time
from pathlib import Path
from typing import Callable, Optional

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


def run_sync(
    token: str, db_path: Path, on_status: Optional[Callable[[str], None]] = None, engine: object = None
) -> bool:
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
                local_data = dump_db_bytes(db_path)  # D-113：checkpoint+关连接+校验表
        else:
            local_data = dump_db_bytes(db_path)  # 同上（无 engine 时不需要它的锁）

        if local_data is None:  # 校验不过（空壳/读不出）→ 绝不上传
            if on_status:
                on_status("syncing_failed")
            return False

        success = upload_db(token, local_data)
        if engine is not None and success:
            record_last_sync_time(engine, time.time())  # D-113：簿记失败不再抛出

        if on_status:
            on_status("sync_success" if success else "syncing_failed")
        return success
    else:
        if on_status:
            on_status("syncing_failed")
        return False


def reset_cloud_db(token: str) -> bool:
    """重置云存档（上传一份**结构完整**的空数据库）

    D-113 ②（任务 1.24）：旧实现新建 AchievementEngine 后直接 read_bytes()，而引擎是
    WAL 模式 —— 表定义还在 -wal 里没并回主文件，主文件只有 4096 字节头部。上传上去
    的就是这个空壳：云端下次下载它会合并失败（_do_merge_db 读不到表 → 返回 None →
    记"合并失败，仅上传本地数据"），于是"重置云存档"并不真的生效。

    现在：建临时库 → 把 -wal checkpoint 进主文件 → 显式关连接 → **断言要上传的字节**
    **里真的有那三张表**（校验不过就放弃上传，宁可不重置也不拿空壳覆盖云端）；
    临时目录连同 -wal/-shm 一起清理。细节与实测证据见 services/achievement_db.py。
    """
    data = build_empty_db_bytes()
    if data is None:
        logger.error("成就同步: 重置云存档失败 —— 生成的空库缺少表结构，已放弃上传")
        return False
    logger.info(f"成就同步: 重置云存档，上传 {len(data)} 字节的空库（含表结构）")
    return upload_db(token, data)
