"""任务 1.24（阶段 1 收口）的守卫：D-112 / D-113（成就云同步的两处用户可见故障）。

**全部离线**：HTTP 一律用假 requests；SQLite 真落盘在 ``tmp_path``；
不起子进程、不联网、不碰用户真实的成就库。

两条缺陷与"改前/改后"：

- **D-113**：``run_sync(..., engine=engine)`` 走合并分支后撞
  ``sqlite3.OperationalError: database is locked``。真正持锁的是
  ``achievement_engine._do_merge_db`` 里那条**从未被关闭**的连接（
  ``with sqlite3.connect(...)`` 只提交事务、不关连接，它在 WAL 模式下做的
  checkpoint 之后对主库保持读锁，而 ``run_sync`` 随后整体替换了主库文件并删掉
  ``-wal``/``-shm``）。表现：worker 线程抛异常 → 界面的 ``after(0, ...)`` 永不执行
  → 同步按钮永久停在"同步中…"且禁用。
- **D-112**：``reset_cloud_db`` 上传的是 WAL 下的 **4096 字节空壳**（表定义还在
  ``-wal`` 里）→ 云端下次下载它合并失败 → "重置云存档"并不真的生效。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import requests

from services import achievement_engine as engine_mod
from services import achievement_db
from services import achievement_sync as sync_mod
from services.achievement_engine import AchievementEngine, _do_merge_db

REQUIRED = ("achievement_progress", "achievement_unlocks", "achievement_state")


# ═══════════════════════════════════════════════════════════════════
# 夹具：假 HTTP + 限流复位 + SQLite 快照
# ═══════════════════════════════════════════════════════════════════


class FakeResponse:
    def __init__(self, status_code=200, content=b"", text=""):
        self.status_code, self.content, self.text = status_code, content, text


class FakeRequests:
    """只实现被用到的那两个函数；默认 404（云端没有存档）。"""

    RequestException = requests.RequestException

    def __init__(self):
        self.posted: list = []
        self.get_calls: list = []
        self.remote: bytes = b""
        self.get_status: int = 404

    def get(self, url, headers=None, timeout=None):
        self.get_calls.append(url)
        if self.remote:
            return FakeResponse(200, content=self.remote)
        return FakeResponse(self.get_status)

    def post(self, url, headers=None, files=None, timeout=None):
        self.posted.append(files["file"][1])
        return FakeResponse(201)


@pytest.fixture
def http(monkeypatch):
    fake = FakeRequests()
    monkeypatch.setattr(sync_mod, "requests", fake)
    saved = list(sync_mod._request_times)
    sync_mod._request_times.clear()
    try:
        yield fake
    finally:
        sync_mod._request_times[:] = saved


@pytest.fixture
def engine(tmp_path):
    """真引擎（真 SQLite），库落在 tmp_path。"""
    previous = engine_mod._engine_instance
    created = engine_mod.init_achievement_engine(tmp_path)
    try:
        yield created
    finally:
        engine_mod._engine_instance = previous


def snapshot_db_bytes(path: Path) -> bytes:
    """checkpoint + 显式关闭之后读字节（否则拿到的是 WAL 空壳）。"""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
    return path.read_bytes()


def remote_with_progress(tmp_path: Path, achievement_id: str) -> bytes:
    """造一份"云端已有进度"的库字节。"""
    other = AchievementEngine(tmp_path / "cloud")
    other.update_progress(achievement_id)
    return snapshot_db_bytes(other._db_path)


class TrackingConnection(sqlite3.Connection):
    """登记创建点，便于断言"有没有连接泄漏"。"""

    registry: list = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.was_closed = False
        TrackingConnection.registry.append(self)

    def close(self):
        self.was_closed = True
        return super().close()


@pytest.fixture
def track_connections(monkeypatch):
    """把 sqlite3.connect 换成会登记的版本（只在本用例内生效）。"""
    real_connect = sqlite3.connect
    TrackingConnection.registry = []

    def wrapper(database, *args, **kwargs):
        kwargs["factory"] = TrackingConnection
        return real_connect(database, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", wrapper)
    return TrackingConnection


# ═══════════════════════════════════════════════════════════════════
# D-113：合并分支不再抛锁错误
# ═══════════════════════════════════════════════════════════════════


class TestNoMoreDatabaseLocked:
    def test_merge_branch_with_engine_succeeds(self, engine, http, tmp_path):
        """★ 改前：这条路径抛 ``database is locked``（复现见 poc/_probe_ach_sync_lock.py）。

        改后：合并 + 上传 + 记录同步时间全部完成，返回 True，一次异常都没有。
        """
        http.remote = remote_with_progress(tmp_path, "gamer_first_launch")
        assert engine.get_progress("gamer_first_launch") is None

        from services.achievement_service import AchievementService

        assert AchievementService().sync_with_engine(engine, "T") is True
        assert engine.get_progress("gamer_first_launch") is not None, "合并结果必须落下"
        assert engine.get_last_sync_time() is not None, "改前正是在这一步抛锁错误"

    def test_second_sync_after_cloud_has_data_also_succeeds(self, engine, http, tmp_path):
        """第二次同步（云端已有存档）也要成功 —— 这就是用户看到"卡在同步中"的那一次。"""
        http.remote = remote_with_progress(tmp_path, "gamer_first_launch")
        from services.achievement_service import AchievementService

        svc = AchievementService()
        assert svc.sync_with_engine(engine, "T") is True
        http.remote = snapshot_db_bytes(engine._db_path)  # 云端现在有这份
        assert svc.sync_with_engine(engine, "T") is True

    def test_merge_db_leaves_no_connection_open(self, tmp_path, track_connections):
        """根因守卫：``_do_merge_db`` 结束后**没有任何连接是开着的**。

        旧写法两条 ``with sqlite3.connect(...) as conn`` 都不关闭连接；其中主库那条
        在 WAL 模式下持着读锁，正是"database is locked"的来源。
        """
        local = AchievementEngine(tmp_path / "local")
        local.update_progress("gamer_first_launch")
        remote_bytes = remote_with_progress(tmp_path, "gamer_last_launch")

        merged = _do_merge_db(local._db_path, remote_bytes)

        assert merged is not None
        leaked = [c for c in track_connections.registry if not c.was_closed]
        assert leaked == [], f"有 {len(leaked)} 条连接没关（会持有 SQLite 读锁）"

    def test_engine_init_db_leaves_no_connection_open(self, tmp_path, track_connections):
        """``AchievementEngine._init_db`` 同样不许泄漏连接（它活了整个进程）。"""
        AchievementEngine(tmp_path / "engine")
        leaked = [c for c in track_connections.registry if not c.was_closed]
        assert leaked == [], f"_init_db 泄漏了 {len(leaked)} 条连接"

    def test_bookkeeping_failure_does_not_fail_the_sync(self, engine, http, monkeypatch):
        """★ 改前：``set_last_sync_time`` 一抛异常，整次同步就带着异常冲出 worker 线程。

        改后：簿记失败只记 warning，上传成功仍然返回 True（上传已经发生了）。
        """
        from services.achievement_service import AchievementService

        def boom(self, timestamp):
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(AchievementEngine, "set_last_sync_time", boom)
        assert AchievementService().sync_with_engine(engine, "T") is True
        assert len(http.posted) == 1, "库确实已经上传过"

    def test_status_callback_still_reports_success(self, engine, http, tmp_path):
        """on_status 的最后一条应当是 sync_success（界面据此恢复按钮）。"""
        http.remote = remote_with_progress(tmp_path, "gamer_first_launch")
        seen: list = []
        assert sync_mod.run_sync("T", engine._db_path, on_status=seen.append, engine=engine) is True
        assert seen[0] == "syncing_download"
        assert seen[-1] == "sync_success"
        assert "syncing_merge" in seen


# ═══════════════════════════════════════════════════════════════════
# D-112：上传的库必须真的有表
# ═══════════════════════════════════════════════════════════════════


class TestResetCloudUploadsARealDb:
    def test_uploaded_bytes_have_the_three_tables(self, http):
        """★ 改前：上传的是 4096 字节的 WAL 空壳（一张表都没有）。

        改后：checkpoint + 关连接之后才读字节，并且**断言**上传内容里真的有表。
        """
        assert sync_mod.reset_cloud_db("TOKEN") is True
        assert len(http.posted) == 1
        data = http.posted[0]
        assert data.startswith(b"SQLite format 3\x00")
        assert len(data) > 4096, f"还是空壳（{len(data)} 字节）"
        assert achievement_db.db_bytes_have_tables(data)
        assert achievement_db.db_bytes_have_tables(data, REQUIRED)

    def test_uploaded_db_is_empty_but_usable(self, http, tmp_path):
        """上传的库必须是"结构齐全但没有进度"。"""
        assert sync_mod.reset_cloud_db("TOKEN") is True
        landed = tmp_path / "uploaded.db"
        landed.write_bytes(http.posted[0])
        conn = sqlite3.connect(str(landed))
        try:
            names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master").fetchall()}
            rows = conn.execute("SELECT COUNT(*) FROM achievement_progress").fetchone()[0]
        finally:
            conn.close()
        assert set(REQUIRED) <= names, names
        assert rows == 0

    def test_uploaded_bytes_can_be_downloaded_and_merged(self, http, engine):
        """★ "重置后云端内容可被再次下载合并" —— 这就是改前做不到的那一步。"""
        assert sync_mod.reset_cloud_db("TOKEN") is True
        uploaded = http.posted[0]
        engine.update_progress("gamer_first_launch")

        merged = _do_merge_db(engine._db_path, uploaded)

        assert merged is not None, "改前的空壳在这一步返回 None（'合并失败，仅上传本地数据'）"
        landed = engine._db_path.parent / "merged.db"
        landed.write_bytes(merged)
        conn = sqlite3.connect(str(landed))
        try:
            kept = conn.execute(
                "SELECT COUNT(*) FROM achievement_progress WHERE achievement_id = ?",
                ("gamer_first_launch",),
            ).fetchone()[0]
        finally:
            conn.close()
        assert kept == 1, "本地进度不能被空库冲掉"

    def test_old_shell_bytes_are_rejected(self, tmp_path):
        """对照实验：把"表还在 -wal 里"的库直接读字节，就是那个空壳。"""
        path = tmp_path / "shell.db"
        conn = sqlite3.connect(str(path))
        conn.execute("PRAGMA journal_mode=WAL")
        for table in REQUIRED:
            conn.execute(f"CREATE TABLE {table} (k TEXT)")
        conn.commit()
        shell = path.read_bytes()
        assert len(shell) == 4096, f"复现空壳失败：{len(shell)} 字节"
        assert achievement_db.db_bytes_have_tables(shell) is False

        rescued = sync_mod.dump_db_bytes(path)
        assert rescued is not None and achievement_db.db_bytes_have_tables(rescued), (
            "dump_db_bytes 必须把 -wal 里的表 checkpoint 进主文件"
        )
        conn.close()

    def test_run_sync_uploads_a_verified_db(self, engine, http):
        """普通同步路径同样走"落盘 + 校验"：上传的字节里必须有表。"""
        assert sync_mod.run_sync("T", engine._db_path, engine=engine) is True
        assert achievement_db.db_bytes_have_tables(http.posted[0])

    def test_reset_gives_up_when_dump_is_unusable(self, http, monkeypatch):
        """校验不通过时**不上传**（宁可失败，也不拿空壳去覆盖云端的用户数据）。"""
        monkeypatch.setattr(sync_mod, "build_empty_db_bytes", lambda: None)
        assert sync_mod.reset_cloud_db("T") is False
        assert http.posted == []

    def test_sync_gives_up_when_dump_is_unusable(self, engine, http, monkeypatch):
        """同步路径同理：本地库读不出可用内容 → 不上传、报失败。"""
        # 注意：`achievement_sync` 是 `from services.achievement_db import dump_db_bytes`
        # 直接绑名，所以要补丁在**它自己的命名空间**上（补丁 achievement_db 里那个名字没用）。
        monkeypatch.setattr(sync_mod, "dump_db_bytes", lambda *a, **kw: None)
        seen: list = []
        assert sync_mod.run_sync("T", engine._db_path, on_status=seen.append, engine=engine) is False
        assert http.posted == []
        assert seen[-1] == "syncing_failed"
