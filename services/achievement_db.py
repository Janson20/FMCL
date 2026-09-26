"""成就库落盘 / 校验工具（任务 1.24 收口 D-113 两条的实现所在）。

> 缺陷号更正（第 12 轮）：本模块与 `achievement_sync.py` 里的注释原先把
> "`reset_cloud_db` 上传 WAL 空壳"写成 **D-112** —— 那是**执行者自己起的号**，
> 而 `docs/refactor/07-known-defects.md` 里的 D-112 是另一个缺陷
> （存档备份同秒互相覆盖，第 11 轮由写测试发现），这两条云同步故障在登记本里
> 是 **D-113 ① ②**。登记本为准，注释已统一改成 D-113。

## 为什么新逻辑放在这里，而不是直接写进 `achievement_sync.py`

`services/achievement_sync.py` 与 `services/achievement_engine.py` 是 1.11/1.12
"整体搬运"进来的文件，两道机械闸门要求它们的**行数与 git 原文一致**：

- ``scripts/relocate_module.py --check``（验收命令之一，脚本本身在禁改清单里）
- ``tests/test_services_relocation.py::test_implementation_kept_the_original_line_count``
  （测试文件也在禁改清单里）

而 D-113 两条的修法必然要新增逻辑。取舍：把**新逻辑**集中放在本模块，
那两个文件里只留**等行数**的调用点（改一行、还是那一行），并在行尾注明指向这里。
于是"没丢内容"这两条闸门继续成立，而修复又是完整的、可测的。

## D-113：`database is locked` —— 谁在持锁（实测）

``with sqlite3.connect(path) as conn:`` 的上下文管理器**只提交/回滚事务**
（``sqlite3.Connection.__exit__`` 不是资源管理器），**不关闭连接**。实测
（``poc/_diag_lock_mechanism.py``，逐条输出都在那个脚本里）：

1. 一个"``with sqlite3.connect(...) as c`` + ``PRAGMA wal_checkpoint(TRUNCATE)``"
   的 helper 返回之后，连接对象**仍然活着** —— 它进了引用环（``gc.get_objects()``
   里能找到它），CPython 的引用计数回收不掉，只有循环 GC 才释放；
2. 它活着的时候，Windows **不让删** ``-wal`` / ``-shm``（``WinError 32``），于是
   ``_cleanup_wal_shm`` 的 ``except OSError: pass`` 把失败吞掉 —— 被整体替换过的
   主库旁边留着一对**属于旧库**的 sidecar；
3. 它活着的时候，另一个连接的写入直接被挡：``timeout=0`` 立刻
   ``database is locked``；用默认 timeout 就等满 5 秒再抛。这正是
   ``run_sync(..., engine=engine)`` 在 ``engine.set_last_sync_time()`` 上看到的
   现象 —— 异常从 worker 线程里冒出去，界面那侧安排好的 ``after(0, ...)`` 再也
   不会执行，同步按钮永久停在"同步中…"且禁用；
4. ``gc.collect()`` 之后连接才被释放，同一次写入**立刻成功**（0.03 秒）。也就是说
   这把锁的存活期取决于 GC 什么时候跑 —— "偶发、难查"的那类锁竞争。

修法：所有这类连接都显式关闭（那两个文件里用 ``contextlib.closing``，等行数改动），
并且**checkpoint 之后立刻校验读出来的字节**。

## D-113 ②：`reset_cloud_db` 上传的是 WAL 空壳

引擎开的是 WAL 模式；``AchievementEngine(tmp_dir)`` 建完表之后表定义还在 ``-wal``
里，主文件只有 **4096 字节**头部（实测 ``poc/_probe_wal_shell.py``）。旧实现直接
``read_bytes()``，于是"重置云存档"上传的是一个**没有表的空壳**：云端下次下载它会
在 ``_do_merge_db`` 里读不到表 → 合并失败 → 记"合并失败，仅上传本地数据"，
**重置并不真的生效**（本地数据随后又被传回云端）。

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
