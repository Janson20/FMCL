"""成就库落盘 / 校验工具。

## 职责

- 构造"结构完整"的空数据库字节（:func:`build_empty_db_bytes`）；
- 校验数据库字节里确实有三张表（:func:`dump_db_bytes`）；
- 记录最后同步时间（:func:`record_last_sync_time`）。

## SQLite 连接与锁

``with sqlite3.connect(path) as conn:`` 的上下文管理器**只提交/回滚事务**
（``sqlite3.Connection.__exit__`` 不是资源管理器），**不关闭连接**。连接对象
进入引用环后要等循环 GC 才释放；它活着的时候：

1. Windows **不让删** ``-wal`` / ``-shm``（``WinError 32``）；
2. 另一个连接的写入会被挡：``timeout=0`` 立刻 ``database is locked``，
   默认 timeout 则等满 5 秒再抛。

因此所有这类连接都显式关闭（用 ``contextlib.closing``），
并且 **checkpoint 之后立刻校验读出来的字节**。

## WAL 与 `reset_cloud_db`

引擎开的是 WAL 模式；``AchievementEngine(tmp_dir)`` 建完表之后表定义还在 ``-wal``
里，主文件只有 **4096 字节**头部。直接 ``read_bytes()`` 会把一个**没有表的空壳**
上传，云端下次下载它会在 ``_do_merge_db`` 里读不到表 → 合并失败 →
"重置云存档"并不真的生效（本地数据随后又被传回云端）。

修法：先 checkpoint 把 ``-wal`` 并回主文件、显式关连接，再读字节，并**断言读出来的
字节里真的有那三张表**；``dump_db_bytes`` 校验不过就返回 ``None``，调用方放弃上传
（宁可这次同步失败，也不拿空壳去覆盖云端的用户数据）。
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from typing import Optional, Sequence

from logzero import logger

#: 成就库必须有的三张表（`AchievementEngine._init_db` 建的那三张）。
REQUIRED_TABLES: tuple = ("achievement_progress", "achievement_unlocks", "achievement_state")

#: ``_wal_checkpoint`` 失败时的兜底说明（只在日志里用）
_WAL_SUFFIXES = ("-wal", "-shm")


def db_bytes_have_tables(data: bytes, required_tables: Sequence[str] = REQUIRED_TABLES) -> bool:
    """把这些字节当成一个 SQLite 库打开时，``required_tables`` 是不是都在。

    D-113 ② 的关键断言：**上传之前必须验内容**。4096 字节的 WAL 空壳同样以
    ``b"SQLite format 3\\x00"`` 开头、同样能被 ``sqlite3.connect`` 打开 —— 只是
    里面一张表都没有。所以"是不是 SQLite 文件"拦不住它，必须查表。
    """
    import shutil

    tmp_dir = Path(tempfile.mkdtemp())
    probe = tmp_dir / "probe.db"
    try:
        probe.write_bytes(data)
        conn = sqlite3.connect(str(probe))
        try:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master").fetchall()}
        finally:
            conn.close()
        missing = [t for t in required_tables if t not in names]
        if missing:
            logger.warning(f"成就同步: 数据库字节里缺少表 {missing}（只有 {sorted(names)}）")
            return False
        return True
    except (sqlite3.Error, OSError) as e:
        logger.warning(f"成就同步: 校验数据库字节失败: {type(e).__name__}: {e}")
        return False
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def dump_db_bytes(db_path: Path, required_tables: Sequence[str] = REQUIRED_TABLES) -> Optional[bytes]:
    """把库**完整**落盘后读出字节：checkpoint 进主文件 + 显式关连接 + 校验表存在。

    返回 ``None`` 表示"这个库不能用来上传"（读失败，或缺必需的表）——调用方应当
    放弃这次同步，而不是拿它去覆盖云端。
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except sqlite3.Error as e:
        logger.warning(f"成就同步: WAL checkpoint 失败: {type(e).__name__}: {e}")
    finally:
        conn.close()  # 必须显式关：留着的连接会一直持有 WAL 的读锁与 -wal 文件句柄

    try:
        data = db_path.read_bytes()
    except OSError as e:
        logger.warning(f"成就同步: 读取数据库失败: {type(e).__name__}: {e}")
        return None

    if required_tables and not db_bytes_have_tables(data, required_tables):
        logger.error(
            f"成就同步: {db_path} 读出来的 {len(data)} 字节里没有必需的表 {list(required_tables)}，拒绝上传"
        )
        return None
    return data


def record_last_sync_time(engine: object, timestamp: float) -> bool:
    """把"上次同步时间"写进引擎。**失败只记 warning，不向上抛**（D-113 的收尾）。

    这一步只是簿记：上传已经成功，没有任何理由因为它失败就把整次同步变成异常 ——
    而那个异常会从 worker 线程里冒出去，让界面侧的 ``after(0, ...)`` 永不执行
    （同步按钮永久停在"同步中…"、且禁用）。
    """
    try:
        engine.set_last_sync_time(timestamp)
        return True
    except sqlite3.Error as e:
        logger.warning(f"成就同步: 写入 last_sync_time 失败（同步本身已成功）: {e}")
        return False


def build_empty_db_bytes(db_dir: Optional[Path] = None) -> Optional[bytes]:
    """造一份**结构完整、没有任何进度**的成就库，返回它的字节（失败返回 ``None``）。

    ``db_dir`` 为 ``None`` 时自己建临时目录，**并在返回前把它连同 ``-wal`` / ``-shm``
    一起清理掉**（``rmdir`` 对非空目录会失败，旧实现那个 ``temp_dir.rmdir()`` 实际
    上一直被 ``except Exception: pass`` 吞掉，临时目录就此残留）。
    """
    from services.achievement_engine import AchievementEngine

    own_dir = db_dir is None
    temp_dir = Path(tempfile.mkdtemp()) if own_dir else Path(db_dir)
    temp_db = temp_dir / AchievementEngine.DB_FILENAME
    try:
        # 构造期副作用就是建表（`_init_db` 里的连接已经显式关闭）
        AchievementEngine(temp_dir)
        return dump_db_bytes(temp_db)
    finally:
        if own_dir:
            for path in (temp_db, *(temp_db.with_name(temp_db.name + s) for s in _WAL_SUFFIXES)):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                temp_dir.rmdir()
            except OSError:
                pass


__all__ = [
    "REQUIRED_TABLES",
    "build_empty_db_bytes",
    "db_bytes_have_tables",
    "dump_db_bytes",
    "record_last_sync_time",
]
