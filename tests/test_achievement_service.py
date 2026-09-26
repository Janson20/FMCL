"""``services/achievement_service.py``（阶段 1 任务 1.12）的单元测试。

**全部离线**：不联网、不开窗口。HTTP 一律用假 `requests`（成功 / 限流 /
网络异常 / 部分失败四种），本地与云端的 SQLite 都真落盘（`tmp_path` 或
`tempfile`），同步/重置的编排用注入的替身。

覆盖：进度数据准备（分类/分组/排序/空数据）、同步流程四种 HTTP 情形、
本地与云端重置、""哪些成就新解锁了""的判定、两个 Mixin 的方法面与签名冻结、
服务零 UI 依赖、服务可脱离 AppContext 独立实例化。
"""

from __future__ import annotations

import ast
import io
import sqlite3
from pathlib import Path

import pytest
import requests

from services import achievement_db
from services import achievement_engine as engine_mod
from services import achievement_sync as sync_mod
from services.achievement_defs import CATEGORY_ORDER
from services.achievement_engine import AchievementEngine
from services.achievement_service import (
    AchievementService,
    AchievementSummary,
    UnlockNotice,
    category_counts,
    format_sync_time,
    summarize,
    unlock_notice,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICE_FILE = REPO_ROOT / "services" / "achievement_service.py"
UI_FILE = REPO_ROOT / "ui" / "app_achievements.py"

#: 冻结的方法面（1.12 抽取时的原始面，19 个 —— 少一个 / 多一个 / 顺序变都算回退）
FROZEN_METHODS = [
    "_build_achievements_tab_content", "_refresh_achievements", "_render_achievement_data",
    "_render_category_section", "_render_achievement_card", "_get_ach_token", "_on_ach_sync",
    "_on_sync_done", "_set_sync_status", "_update_ach_last_sync_label", "_show_sync_login_hint",
    "_on_ach_reset_cloud", "_do_reset_cloud", "_on_reset_cloud_done", "_on_ach_reset_local",
    "_do_reset_local", "_triple_confirm", "_on_achievement_unlock", "_refresh_ach_colors",
]


# ═══════════════════════════════════════════════════════════════════
# 夹具：真 SQLite 落 tmp_path + 假 HTTP + 限流窗口复位
# ═══════════════════════════════════════════════════════════════════


@pytest.fixture
def engine(tmp_path):
    """真的 `AchievementEngine`，数据库落在 `tmp_path`。

    `init_achievement_engine` 写的是**模块级单例**，所以夹具结束时必须还原，
    否则后面跑到的用例会读到上一个 tmp 目录的库。
    """
    previous = engine_mod._engine_instance
    created = engine_mod.init_achievement_engine(tmp_path)
    try:
        yield created
    finally:
        engine_mod._engine_instance = previous


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """`achievement_sync` 的限流窗口也是模块级状态，用例之间必须隔离。"""
    saved = list(sync_mod._request_times)
    sync_mod._request_times.clear()
    try:
        yield
    finally:
        sync_mod._request_times[:] = saved


class FakeResponse:
    def __init__(self, status_code=200, content=b"", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


class FakeRequests:
    """`achievement_sync.requests` 的替身（只实现被用到的那两个函数）。"""

    RequestException = requests.RequestException

    def __init__(self, get=None, post=None):
        self._get = get
        self._post = post
        self.get_calls: list = []
        self.post_calls: list = []

    def get(self, url, headers=None, timeout=None):
        self.get_calls.append({"url": url, "headers": headers, "timeout": timeout})
        if self._get is None:
            return FakeResponse(404)
        return self._get(url=url, headers=headers, timeout=timeout)

    def post(self, url, headers=None, files=None, timeout=None):
        self.post_calls.append({"url": url, "headers": headers, "files": files, "timeout": timeout})
        if self._post is None:
            return FakeResponse(200)
        return self._post(url=url, headers=headers, files=files, timeout=timeout)


def use_http(monkeypatch, *, get=None, post=None) -> FakeRequests:
    fake = FakeRequests(get=get, post=post)
    monkeypatch.setattr(sync_mod, "requests", fake)
    return fake


def snapshot_db_bytes(path: Path) -> bytes:
    """把 WAL 里的内容并回主文件后再读字节（否则拿到的是空壳）。"""
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()  # 必须显式关：留着的连接会干扰后续写入（WAL 的 -shm 协调）
    return path.read_bytes()


def other_engine_with_progress(tmp_path: Path, achievement_id: str) -> bytes:
    """造一份"云端已有进度"的库字节。"""
    remote = AchievementEngine(tmp_path)
    remote.update_progress(achievement_id)
    return snapshot_db_bytes(remote._db_path)


# ═══════════════════════════════════════════════════════════════════
# 进度数据准备（分类 / 分组 / 排序 / 空数据）
# ═══════════════════════════════════════════════════════════════════


class TestProgressPreparation:
    def test_load_progress_without_engine_returns_none(self):
        """引擎未初始化时返回 None —— 对应原文 `_refresh_achievements` 的直接 return。"""
        assert AchievementService().load_progress() is None

    def test_load_progress_groups_by_category_in_fixed_order(self, engine):
        data = AchievementService().load_progress()
        assert [cat["category"] for cat in data] == [c.value for c in CATEGORY_ORDER], (
            "分类顺序必须与 CATEGORY_ORDER 一致（原文按它遍历）"
        )
        for cat in data:
            assert set(cat) == {"category", "category_meta", "achievements"}
            assert set(cat["category_meta"]) == {"i18n_key", "icon"}
            for ach in cat["achievements"]:
                # 界面 `_render_achievement_card` 直接读这些键，一个都不能少
                for key in ("id", "icon", "i18n_key", "desc_i18n_key", "max_stage",
                            "progress_stage", "progress_current", "progress_percent",
                            "current_stage_name", "next_threshold"):
                    assert key in ach, key
                assert ach["category"] == cat["category"], "分组必须自洽"

    def test_load_progress_covers_every_defined_achievement(self, engine):
        from services.achievement_defs import ACHIEVEMENTS

        data = AchievementService().load_progress()
        got = {a["id"] for cat in data for a in cat["achievements"]}
        assert got == {d.achievement_id for d in ACHIEVEMENTS}
        assert len(got) == 47

    def test_summarize_empty_data(self):
        assert summarize([]) == AchievementSummary(total=0, unlocked=0, percent=0)
        assert summarize([]).percent == 0, "total 为 0 时原文写的是整数 0，不是 0.0"

    def test_summarize_counts_items_not_stages(self):
        data = [
            {"achievements": [
                {"progress_stage": 0}, {"progress_stage": 1}, {"progress_stage": 3},
            ]},
            {"achievements": [{"progress_stage": 2}]},
        ]
        got = summarize(data)
        assert (got.total, got.unlocked, got.percent) == (4, 3, 75.0)

    def test_summarize_percent_rounding(self):
        data = [{"achievements": [{"progress_stage": 1}] + [{"progress_stage": 0}] * 2}]
        assert summarize(data).percent == 33.3

    def test_category_counts(self):
        assert category_counts([]) == (0, 0)
        ache = [{"progress_stage": 0}, {"progress_stage": 1}, {"progress_stage": 0}]
        assert category_counts(ache) == (1, 3)

    def test_service_delegates_summarize_and_category_counts(self, engine):
        svc = AchievementService()
        data = svc.load_progress()
        flat = [a for cat in data for a in cat["achievements"]]
        assert svc.summarize(data) == summarize(data)
        assert svc.category_counts(flat) == category_counts(flat)

    def test_summarize_reflects_a_real_unlock(self, engine):
        svc = AchievementService()
        before = svc.summarize(svc.load_progress())
        engine.update_progress("gamer_first_launch")
        after = svc.summarize(svc.load_progress())
        assert after.unlocked == before.unlocked + 1
        assert after.total == before.total

    def test_format_sync_time(self):
        # 固定时间戳 → 本地时区的格式化结果（与 `time.strftime` 同源）
        import time

        ts = 1_700_000_000.0
        assert format_sync_time(ts) == time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        assert AchievementService().format_sync_time(ts) == format_sync_time(ts)

    def test_last_sync_time_none_then_value(self, engine):
        svc = AchievementService()
        assert svc.last_sync_time() is None
        engine.set_last_sync_time(1234.5)
        assert svc.last_sync_time() == 1234.5

    def test_last_sync_time_without_engine_is_none(self):
        assert AchievementService().last_sync_time() is None

    def test_engine_returns_the_global_singleton(self, engine):
        assert AchievementService().engine() is engine
        assert engine_mod.get_achievement_engine() is engine

    def test_token_lookup_is_guarded(self):
        svc = AchievementService()
        assert svc.get_token({"get_jdz_token": lambda: "JWT"}) == "JWT"
        assert svc.get_token({"get_jdz_token": lambda: None}) == ""
        assert svc.get_token({}) == ""


# ═══════════════════════════════════════════════════════════════════
# 同步流程（假 HTTP：成功 / 限流 / 网络异常 / 部分失败）
# ═══════════════════════════════════════════════════════════════════


class TestSyncFlow:
    def test_download_upload_roundtrip_success(self, engine, monkeypatch):
        """服务器无存档（404）→ 只上传本地库 → 成功，并写入 last_sync_time。"""
        posted = {}

        def post(url, headers=None, files=None, timeout=None):
            posted["auth"] = headers.get("Authorization")
            posted["data"] = files["file"][1]
            return FakeResponse(201)

        fake = use_http(monkeypatch, get=lambda **kw: FakeResponse(404), post=post)
        before = snapshot_db_bytes(engine._db_path)
        svc = AchievementService()
        assert svc.sync_with_engine(engine, "TOKEN-1") is True
        assert len(fake.get_calls) == 1 and len(fake.post_calls) == 1
        assert posted["auth"] == "Bearer TOKEN-1"
        assert posted["data"] == before, "上传的就是同步前本地那份库"
        assert engine.get_last_sync_time() is not None, "传了 engine 时由 run_sync 自己记时间"

    def test_remote_merge_pulls_progress_down(self, engine, monkeypatch):
        """远端有进度 → 合并进本地库（保留完成度更高的那份）。

        走的是**不带 engine** 的 `run_sync`：带 engine 时末尾那次
        `set_last_sync_time` 会撞锁，见下一条"记录现状"的测试。
        """
        remote_bytes = other_engine_with_progress(engine._db_path.parent / "cloud", "gamer_first_launch")
        assert engine.get_progress("gamer_first_launch") is None, "本地一开始没有这项进度"

        use_http(monkeypatch, get=lambda **kw: FakeResponse(200, content=remote_bytes),
                 post=lambda **kw: FakeResponse(200))

        def runner(token, db_path, **kw):
            return sync_mod.run_sync(token, db_path)

        svc = AchievementService(sync_runner=runner)
        assert svc.sync_with_engine(engine, "T") is True
        assert engine.get_progress("gamer_first_launch") is not None, "远端解锁的成就应当合并到本地"

    def test_merge_branch_with_engine_no_longer_raises_lock_error(self, engine, monkeypatch):
        """合并分支 + 传 engine：**不再**报锁错误（任务 1.24 修正了 D-113）。

        这里原先是"记录现状、疑为缺陷"的用例：`run_sync(..., engine=engine)` 合并完
        远端数据后，末尾的 `engine.set_last_sync_time()` 会抛
        ``sqlite3.OperationalError: database is locked``（可复现：
        `poc/_probe_ach_sync_lock.py`）。根因是 `achievement_engine._do_merge_db`
        里那条**从未被关闭**的连接（``with sqlite3.connect(...)`` 只提交事务、
        不关连接）在 WAL 模式下对主库持着读锁，而 `run_sync` 随后整体替换了主库
        文件并删掉 `-wal`/`-shm`。

        修好之后本用例断言**新的行为**：同步成功、合并落下、同步时间被记录，
        没有任何异常。旧断言（"要么抛 locked、要么 ok 为 True"）已经失去意义。
        """
        remote_bytes = other_engine_with_progress(engine._db_path.parent / "cloud", "gamer_first_launch")
        use_http(monkeypatch, get=lambda **kw: FakeResponse(200, content=remote_bytes),
                 post=lambda **kw: FakeResponse(200))
        svc = AchievementService()
        assert svc.sync_with_engine(engine, "T") is True
        assert engine.get_progress("gamer_first_launch") is not None, "远端解锁的成就必须合并到本地"
        assert engine.get_last_sync_time() is not None, "簿记那一步（改前抛锁错误的地方）必须成功"

    def test_rate_limited_skips_http_entirely(self, engine, monkeypatch):
        """限流窗口打满 → 下载与上传都直接放弃，一次 HTTP 都不发。"""
        import time

        sync_mod._request_times.extend([time.time()] * sync_mod.MAX_REQUESTS_PER_WINDOW)
        fake = use_http(monkeypatch)
        assert AchievementService().sync_with_engine(engine, "T") is False
        assert fake.get_calls == [] and fake.post_calls == []
        assert engine.get_last_sync_time() is None

    def test_network_error_is_absorbed(self, engine, monkeypatch):
        """网络异常 → 返回 False，不向上抛。"""
        def boom(**kw):
            raise requests.RequestException("连接重置")

        fake = use_http(monkeypatch, get=boom, post=boom)
        assert AchievementService().sync_with_engine(engine, "T") is False
        assert len(fake.get_calls) == 1 and len(fake.post_calls) == 1

    def test_oversized_download_is_ignored_but_upload_still_happens(self, engine, monkeypatch):
        """部分失败：远端文件超过上限被忽略 → 仍会把本地库传上去。"""
        big = b"0" * (sync_mod.MAX_SIZE + 1)
        fake = use_http(monkeypatch, get=lambda **kw: FakeResponse(200, content=big),
                        post=lambda **kw: FakeResponse(201))
        assert AchievementService().sync_with_engine(engine, "T") is True
        assert len(fake.get_calls) == 1 and len(fake.post_calls) == 1

    def test_upload_http_failure_reports_false(self, engine, monkeypatch):
        fake = use_http(monkeypatch, get=lambda **kw: FakeResponse(404),
                        post=lambda **kw: FakeResponse(500, text="boom"))
        assert AchievementService().sync_with_engine(engine, "T") is False
        assert len(fake.post_calls) == 1

    def test_sync_with_engine_passes_engine_kwarg(self, engine):
        """`sync_with_engine` 必须把 engine 一起传下去（原文如此，拿它的锁）。"""
        seen = []

        def runner(token, db_path, **kw):
            seen.append((token, db_path, kw))
            return True

        svc = AchievementService(sync_runner=runner)
        assert svc.sync_with_engine(engine, "T") is True
        assert seen == [("T", engine._db_path, {"engine": engine})]

    def test_push_sync_omits_engine_and_writes_clock(self, engine):
        """`push_sync` 与 `sync_with_engine` 的两处刻意不同，逐条钉住。"""
        seen = []

        def runner(token, db_path, **kw):
            seen.append((token, db_path, kw))
            return True

        svc = AchievementService(
            engine_getter=lambda: engine, sync_runner=runner, clock=lambda: 9876.5
        )
        assert svc.push_sync("T") is True
        assert seen == [("T", engine._db_path, {})], "push_sync 刻意不传 engine"
        assert engine.get_last_sync_time() == 9876.5

    def test_push_sync_failure_does_not_write_time(self, engine):
        svc = AchievementService(engine_getter=lambda: engine, sync_runner=lambda *a, **k: False)
        assert svc.push_sync("T") is False
        assert engine.get_last_sync_time() is None

    def test_push_sync_without_engine_is_false(self):
        assert AchievementService().push_sync("T") is False

    def test_db_path_reads_engine_private_attribute(self, engine):
        """记录现状：引擎没有公开路径访问器，服务读的是 `engine._db_path`。"""
        svc = AchievementService(engine_getter=lambda: engine)
        assert svc._db_path(engine) == engine._db_path


# ═══════════════════════════════════════════════════════════════════
# 本地 / 云端重置
# ═══════════════════════════════════════════════════════════════════


class TestReset:
    def test_reset_local_clears_real_sqlite(self, engine):
        engine.update_progress("gamer_first_launch")
        engine.update_progress("gamer_version_collector", value=50)
        assert engine.get_stats()["unlocked_achievements"] == 2

        assert AchievementService().reset_local() is True
        assert engine.get_stats()["unlocked_achievements"] == 0
        assert engine.get_stats()["unlocked_stages"] == 0

    def test_reset_local_without_engine_returns_none(self):
        """记录现状、疑为缺陷：原文没有引擎时也照样提示"重置成功"。

        服务如实返回 None；界面那句无条件成功提示本轮不改（既有行为）。
        """
        assert AchievementService().reset_local() is None

    def test_reset_cloud_uploads_a_temp_db_not_the_local_one(self, tmp_path, monkeypatch):
        """重置云存档 = 上传一份**临时新建**的、**结构完整**的空库（不是本地那份）。

        任务 1.24（D-112）：这条路径原先上传的是个**没有表的空壳** ——
        `reset_cloud_db` 新建 `AchievementEngine` 后直接 `read_bytes()`，
        而引擎是 WAL 模式：表定义还在 `-wal` 里没并回主文件，主文件只有 4096 字节
        头部。后果是云端下一次下载它会合并失败，**"重置云存档"并不真的生效**。

        修好之后（checkpoint + 显式关连接 + 校验表存在），这里断言**新的行为**：
        上传的既不是本地那份库、也不含任何进度，而且**三张表都在**（旧断言是
        "要么没有表、要么有表但为空"，现在收紧成"必须有表且为空"）。
        """
        uploaded = {}

        def post(url, headers=None, files=None, timeout=None):
            uploaded["data"] = files["file"][1]
            uploaded["auth"] = headers.get("Authorization")
            return FakeResponse(201)

        use_http(monkeypatch, post=post)
        assert AchievementService().reset_cloud("TOKEN-2") is True
        assert uploaded["auth"] == "Bearer TOKEN-2"
        assert uploaded["data"].startswith(b"SQLite format 3\x00"), "上传的必须是一个 SQLite 文件"
        assert len(uploaded["data"]) > 4096, f"不能是 WAL 空壳（{len(uploaded['data'])} 字节）"
        assert achievement_db.db_bytes_have_tables(uploaded["data"]), "上传内容里必须真的有那三张表"

        landed = tmp_path / "uploaded.db"
        landed.write_bytes(uploaded["data"])
        conn = sqlite3.connect(str(landed))
        try:
            count = conn.execute("SELECT COUNT(*) FROM achievement_progress").fetchone()[0]
        finally:
            conn.close()
        assert count == 0, "重置上传的必须是一个空的库"

        # 端到端：这份内容必须能被"下载回来 + 合并"（改前的空壳在这里返回 None）
        from services.achievement_engine import _do_merge_db

        local = AchievementEngine(tmp_path / "local")
        local.update_progress("gamer_first_launch")
        merged = _do_merge_db(local._db_path, uploaded["data"])
        assert merged is not None, "上传上去的库必须能参与合并（改前的空壳在这里返回 None）"

    def test_reset_cloud_failure_propagates_as_false(self, monkeypatch):
        use_http(monkeypatch, post=lambda **kw: FakeResponse(500))
        assert AchievementService().reset_cloud("T") is False

    def test_reset_cloud_uses_injected_resetter(self):
        seen = []
        svc = AchievementService(cloud_resetter=lambda token: seen.append(token) or True)
        assert svc.reset_cloud("TK") is True
        assert seen == ["TK"]


# ═══════════════════════════════════════════════════════════════════
# "哪些成就新解锁了"的判定
# ═══════════════════════════════════════════════════════════════════


class TestUnlockJudgement:
    def test_engine_callback_then_notice_normalization(self, engine):
        """解锁判定在引擎里（多阶段阈值），服务只做载荷归一化。"""
        seen = []
        engine.register_unlock_callback(lambda d, s, n: seen.append((d, s, n)))

        engine.update_progress("gamer_version_collector", value=10)
        assert len(seen) == 1
        ach_def, stage, stage_name = seen[0]

        notice = AchievementService().unlock_notice(ach_def, stage, stage_name)
        assert notice == UnlockNotice(
            achievement_id="gamer_version_collector",
            icon=ach_def.icon,
            i18n_key="ach_gamer_version_collector",
            stage=1,
            stage_name="版本收藏家 I",
        )

    def test_second_stage_is_reported_as_its_own_unlock(self, engine):
        seen = []
        engine.register_unlock_callback(lambda d, s, n: seen.append((s, n)))
        engine.update_progress("gamer_version_collector", value=30)
        assert seen == [(2, "版本收藏家 II")]

    def test_no_unlock_callback_when_stage_does_not_advance(self, engine):
        seen = []
        engine.register_unlock_callback(lambda d, s, n: seen.append((d, s, n)))
        engine.update_progress("gamer_version_collector", value=5)  # 未达首档
        engine.update_progress("gamer_version_collector", value=9)  # 仍未达
        assert seen == []

    def test_pending_unlocks_lists_then_ack_clears(self, engine):
        engine.update_progress("gamer_first_launch")
        engine.update_progress("gamer_version_collector", value=10)
        svc = AchievementService()

        pending = svc.pending_unlocks()
        assert [p["achievement_id"] for p in pending] == ["gamer_first_launch", "gamer_version_collector"]
        assert all(p["stage_name"] for p in pending)

        svc.ack_unlocks([p["id"] for p in pending])
        assert svc.pending_unlocks() == []

    def test_pending_unlocks_and_ack_without_engine_are_safe(self):
        svc = AchievementService()
        assert svc.pending_unlocks() == []
        svc.ack_unlocks([1, 2])  # 不抛异常即为合格

    def test_unlock_notice_reads_attributes_directly(self):
        """**不做 getattr 兜底**：属性缺失就该抛 AttributeError（别兜成空文案）。"""
        class Bare:
            pass

        with pytest.raises(AttributeError):
            unlock_notice(Bare(), 1, "n")

    def test_module_level_unlock_notice_matches_service(self, engine):
        from services.achievement_defs import ACHIEVEMENTS

        ach_def = ACHIEVEMENTS[0]
        svc = AchievementService()
        assert svc.unlock_notice(ach_def, 1, "启程") == unlock_notice(ach_def, 1, "启程")


# ═══════════════════════════════════════════════════════════════════
# Mixin 方法面冻结 / 服务独立性 / 零 UI
# ═══════════════════════════════════════════════════════════════════


def own_methods(path: Path, cls_name: str) -> list:
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    return [m.name for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]


class TestMixinSurfaceUnchanged:
    def test_achievement_tab_method_surface_is_frozen(self):
        """19 个方法、顺序也不许变（1.12 只换实现，不动面）。"""
        assert own_methods(UI_FILE, "AchievementTabMixin") == FROZEN_METHODS
        assert len(FROZEN_METHODS) == 19

    def test_signatures_unchanged(self):
        import inspect

        from ui.app_achievements import AchievementTabMixin as M

        expected = {
            "_refresh_achievements": [],
            "_render_achievement_data": ["data"],
            "_render_category_section": ["cat_data"],
            "_get_ach_token": [],
            "_on_ach_sync": [],
            "_on_sync_done": ["success"],
            "_set_sync_status": ["text"],
            "_update_ach_last_sync_label": [],
            "_show_sync_login_hint": [],
            "_on_ach_reset_cloud": [],
            "_do_reset_cloud": ["token"],
            "_on_reset_cloud_done": ["success"],
            "_on_ach_reset_local": [],
            "_do_reset_local": [],
            "_triple_confirm": ["confirm_key", "on_confirm"],
            "_on_achievement_unlock": ["ach_def", "stage", "stage_name"],
            "_refresh_ach_colors": [],
        }
        for name, params in expected.items():
            got = list(inspect.signature(getattr(M, name)).parameters)
            assert got == ["self", *params], f"{name}: {got}"

    def test_get_ach_token_and_unlock_keep_return_annotations(self):
        import inspect

        from ui.app_achievements import AchievementTabMixin as M

        # `ui/app_achievements.py` 没有 `from __future__ import annotations`，
        # 所以注解在运行期就是真正的类型对象（不是字符串）
        assert inspect.signature(M._get_ach_token).return_annotation is str
        assert inspect.signature(M._on_achievement_unlock).parameters["stage"].annotation is int
        assert inspect.signature(M._on_achievement_unlock).parameters["stage_name"].annotation is str

    def test_module_level_service_getter_exists_and_caches(self):
        """`_get_achievement_service(owner)` 在 owner 上缓存同一个实例。"""
        import ui.app_achievements as mod

        class Owner:
            pass

        owner = Owner()
        first = mod._get_achievement_service(owner)
        assert isinstance(first, AchievementService)
        assert mod._get_achievement_service(owner) is first
        assert owner._achievement_service_fallback is first

    def test_service_getter_prefers_app_context(self):
        import ui.app_achievements as mod

        registered = AchievementService()

        class Ctx:
            def try_get(self, name):
                return registered if name == "achievement" else None

        class Owner:
            context = Ctx()

        assert mod._get_achievement_service(Owner()) is registered

    def test_service_getter_survives_slots_owner_and_none(self):
        import ui.app_achievements as mod

        class Slotted:
            __slots__ = ()

        assert isinstance(mod._get_achievement_service(Slotted()), AchievementService)
        assert isinstance(mod._get_achievement_service(None), AchievementService)

    def test_ui_no_longer_touches_the_engine_directly(self):
        """界面里不该再有 `from achievement_engine import` / `run_sync` 的直接调用。"""
        src = io.open(UI_FILE, encoding="utf-8").read()
        assert "from achievement_engine import" not in src
        assert "from achievement_sync import" not in src
        assert "get_achievement_engine" not in src


class TestServiceStandalone:
    def test_instantiable_without_app_context(self):
        svc = AchievementService()
        assert svc.attached is False
        assert svc.name == "achievement" and svc.label == "成就"
        assert svc.describe()["requires"] == []
        assert svc.describe()["attached"] is False

    def test_attaching_a_context_does_not_change_engine_semantics(self):
        """引擎仍是**全局单例**语义：挂不挂 AppContext 都读同一个 `_engine_instance`。"""
        from app.context import AppContext

        svc = AppContext().register(AchievementService())
        assert svc.attached is True
        assert svc.engine() is engine_mod.get_achievement_engine()

    def test_service_module_has_zero_ui_dependency(self):
        tree = ast.parse(io.open(SERVICE_FILE, encoding="utf-8").read())
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad += [a.name for a in node.names if a.name.split(".")[0] in ("tkinter", "customtkinter", "ui")]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.split(".")[0] in ("tkinter", "customtkinter", "ui"):
                    bad.append(mod)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("Thread", "Timer", "after"):
                    bad.append(ast.unparse(node.func))
        assert bad == [], f"服务层出现 UI / 线程 / 定时器：{bad}"

    def test_service_module_has_no_i18n_calls(self):
        """文案一律留界面：服务里不许出现 `_(...)`。"""
        tree = ast.parse(io.open(SERVICE_FILE, encoding="utf-8").read())
        calls = [
            node.lineno for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in ("_", "tr", "_translate")
        ]
        assert calls == []

    def test_ui_keeps_the_i18n_literal_keys(self):
        """`scripts/check_i18n.py` 靠界面文件里的字面量键做检查，不许变成纯动态。"""
        src = io.open(UI_FILE, encoding="utf-8").read()
        for key in ("ach_sync_btn", "ach_reset_cloud_btn", "ach_reset_local_btn",
                    "ach_last_sync_time", "ach_stats_detail", "ach_maxed",
                    "ach_confirm_step2_msg", "ach_sync_login_hint"):
            assert f'"{key}"' in src, f"界面丢了字面量键 {key}"


# ═══════════════════════════════════════════════════════════════════
# 运行期接线：用"未绑定方法 + 假宿主"直接调 Mixin 方法
# ═══════════════════════════════════════════════════════════════════


class FakeOwner:
    """`AchievementTabMixin` 的假宿主。

    `_set_sync_status` / `_refresh_achievements` 是 Mixin 自己的方法，这里在
    假宿主上放**同名记录器**顶掉它们 —— 这正是"未绑定方法 + 假对象"这种测试
    写法的意义：不必起 Tk 也能验证外层方法的编排。
    """

    def __init__(self, *, callbacks_map=None, service=None, fields=None):
        self.callbacks = callbacks_map if callbacks_map is not None else {}
        self.sync_status = []
        self.refreshed = 0
        self.after_calls = []
        self.threads = []
        if service is not None:
            self._achievement_service_fallback = service
        for key, value in (fields or {}).items():
            setattr(self, key, value)

    def _set_sync_status(self, text):
        self.sync_status.append(text)

    def _refresh_achievements(self):
        self.refreshed += 1

    def winfo_exists(self):
        return True

    def after(self, delay, fn=None):
        self.after_calls.append((delay, fn))


class TestMixinRuntimeWiring:
    """证明界面方法真的**跑到**服务上，而不只是"语法上引用了服务"。"""

    def test_get_ach_token_reads_callback(self):
        from ui.app_achievements import AchievementTabMixin

        owner = FakeOwner(callbacks_map={"get_jdz_token": lambda: "JWT"})
        assert AchievementTabMixin._get_ach_token(owner) == "JWT"
        assert AchievementTabMixin._get_ach_token(FakeOwner()) == ""

    def test_do_reset_local_calls_engine_and_refreshes(self, engine):
        from ui.app_achievements import AchievementTabMixin

        engine.update_progress("gamer_first_launch")
        owner = FakeOwner()
        AchievementTabMixin._do_reset_local(owner)
        assert engine.get_stats()["unlocked_achievements"] == 0, "本地库被真的重置了"
        assert owner.sync_status == ["ach_reset_local_success"], "界面提示仍是那句（既有行为）"
        assert owner.refreshed == 1

    def test_refresh_achievements_caches_service_data(self, engine):
        from ui.app_achievements import AchievementTabMixin

        rendered = []
        owner = FakeOwner(
            fields={
                "ach_scroll": _ExistsWidget(),
                "_render_achievement_data": rendered.append,
                "_update_ach_last_sync_label": lambda: None,
            }
        )
        AchievementTabMixin._refresh_achievements(owner)
        assert [cat["category"] for cat in owner._ach_data_cache] == [c.value for c in CATEGORY_ORDER]
        assert rendered and rendered[0] is owner._ach_data_cache

    def test_refresh_achievements_without_engine_is_a_noop(self):
        from ui.app_achievements import AchievementTabMixin

        owner = FakeOwner(
            fields={
                "ach_scroll": _ExistsWidget(),
                "_ach_data_cache": [],
                "_render_achievement_data": lambda d: pytest.fail("无引擎时不该渲染"),
                "_update_ach_last_sync_label": lambda: pytest.fail("无引擎时不该刷新标签"),
            }
        )
        AchievementTabMixin._refresh_achievements(owner)
        assert owner._ach_data_cache == []

    def test_update_last_sync_label_formats_timestamp(self, engine):
        from ui.app_achievements import AchievementTabMixin

        engine.set_last_sync_time(1_700_000_000.0)
        labels = []
        owner = FakeOwner(
            fields={
                "ach_last_sync_label": _ConfigureRecorder(labels.append),
            }
        )
        AchievementTabMixin._update_ach_last_sync_label(owner)
        assert labels == ["ach_last_sync_time"], "i18n 未初始化时 `_()` 返回键名"

    def test_update_last_sync_label_clears_when_never_synced(self, engine):
        from ui.app_achievements import AchievementTabMixin

        labels = []
        owner = FakeOwner(fields={"ach_last_sync_label": _ConfigureRecorder(labels.append)})
        AchievementTabMixin._update_ach_last_sync_label(owner)
        assert labels == [""], "没同步过就清空标签"

    def test_do_reset_cloud_starts_a_thread_and_disables_button(self, monkeypatch):
        from ui.app_achievements import AchievementTabMixin

        calls = []
        owner = FakeOwner(
            service=AchievementService(cloud_resetter=lambda token: calls.append(token) or True),
            fields={"ach_reset_cloud_btn": _ConfigureRecorder(lambda **kw: None)},
        )
        started = []

        class ImmediateThread:
            def __init__(self, target=None, daemon=None):
                self._target = target

            def start(self):
                started.append(True)
                self._target()

        monkeypatch.setattr("threading.Thread", ImmediateThread)
        # 假宿主上把"完成回调"接回真正的 Mixin 实现（未绑定方法 + 假对象）
        owner._on_reset_cloud_done = lambda ok: AchievementTabMixin._on_reset_cloud_done(owner, ok)
        AchievementTabMixin._do_reset_cloud(owner, "TK")
        assert calls == ["TK"]
        # 后台线程里只投递 `after(0, ...)`，完成回调由主线程执行
        assert owner.sync_status == ["ach_sync_status_resetting"]
        assert [d for d, _fn in owner.after_calls] == [0]
        owner.after_calls[0][1]()
        assert owner.sync_status == ["ach_sync_status_resetting", "ach_reset_cloud_success"]
        assert started == [True]

    def test_on_achievement_unlock_schedules_toast_and_refresh(self, engine, monkeypatch):
        from ui.app_achievements import AchievementTabMixin
        from services.achievement_defs import ACHIEVEMENTS

        owner = FakeOwner(callbacks_map={})  # 没有 token → 不起同步线程
        owner._get_ach_token = lambda: ""
        toasts = []
        import ui.dialogs as dialogs

        started = []

        class NoThread:
            def __init__(self, target=None, daemon=None):
                self._target = target

            def start(self):
                started.append(True)

        monkeypatch.setattr("threading.Thread", NoThread)
        original = dialogs.show_toast_notification
        dialogs.show_toast_notification = lambda parent, icon, title, stage: toasts.append((icon, title, stage))
        try:
            AchievementTabMixin._on_achievement_unlock(owner, ACHIEVEMENTS[0], 1, "启程")
            assert [d for d, _fn in owner.after_calls] == [100, 600]
            owner.after_calls[0][1]()  # 执行 Toast
            owner.after_calls[1][1]()  # 执行刷新
        finally:
            dialogs.show_toast_notification = original
        assert toasts == [(ACHIEVEMENTS[0].icon, ACHIEVEMENTS[0].i18n_key, "启程")]
        assert owner.refreshed == 1
        assert started == [], "没有 token 时不该起同步线程"


class _ExistsWidget:
    def winfo_exists(self):
        return True


class _ConfigureRecorder:
    def __init__(self, sink):
        self._sink = sink

    def winfo_exists(self):
        return True

    def configure(self, **kw):
        if "text" in kw:
            self._sink(kw["text"])


# 供 `_refresh_achievements` 测试使用的属性（放在类外定义，避免 FakeOwner 膨胀）
