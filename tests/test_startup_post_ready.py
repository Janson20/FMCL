"""启动后置链条的回归守卫（阶段 3 任务 3.1：A-24 / A-25 / F-09 与首页摘要）。

四条链路（旧 `main.py` 里各自一个线程 / 后台闭包）：

* **A-25** 启动时批量刷新微软账号 Token；
* **A-24** 后台**静默**检查更新（`config.auto_check_update` 可关；没新版本不提示）；
* **F-09** 成就云同步（有净读 Token 才同步）+ 每日签到（连续天数提示）；
* 把成就总览交给首页桥（省一次查库）。

判据都是**"到底发生了什么"**：服务被调了几次、发了什么信号、状态键是不是那一句。
真实的网络与真实账号文件都不碰（注入点见 `app/startup.py` 的 `launcher_wiring` /
`update_checker`）。
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Property, QObject, Signal, Slot  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.startup import StartupController  # noqa: E402


def _app() -> Any:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


class FakeConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.auto_check_update = kwargs.get("auto_check_update", True)
        self.jdz_token = kwargs.get("jdz_token", "")
        #: 首次启动选语言那一步要读写的两个字段（默认"已选过"，免得不相关的用例被拦在弹窗前）
        self.language = kwargs.get("language", "zh_CN")
        self.language_chosen = kwargs.get("language_chosen", True)
        self.terms_consent = kwargs.get("terms_consent", True)
        self.ai_privacy_consent = kwargs.get("ai_privacy_consent", True)
        self.saved = 0

    def save_config(self) -> bool:
        self.saved += 1
        return True


class FakeTasks:
    """立刻在当前线程跑完（回调用信号也能被下面的 `flush()` 处理掉）。"""

    def __init__(self) -> None:
        self.submitted: List[str] = []

    def submit(self, fn: Any, **kwargs: Any) -> Any:
        self.submitted.append(str(kwargs.get("name", "")))
        fn()
        return None


class FakeAccountService:
    name = "account"

    def __init__(self) -> None:
        self.refreshed = 0

    def auto_refresh_tokens(self) -> int:
        self.refreshed += 1
        return 3


class FakeAchievementService:
    name = "achievement"

    def __init__(self, checkin_result: Optional[Dict[str, Any]] = None, sync_ok: bool = True) -> None:
        self.checkin_result = checkin_result if checkin_result is not None else {"checked_in": True, "already": False, "streak": 4}
        self.sync_calls: List[Any] = []
        self.checkin_calls = 0
        self.sync_ok = sync_ok
        self.raise_on_checkin = False
        self.progress: Any = [{"a": 1}]
        self.engine_obj = object()

    def engine(self) -> Any:
        return self.engine_obj

    def sync_with_engine(self, engine: Any, token: str) -> bool:
        self.sync_calls.append((engine, token))
        return self.sync_ok

    def checkin(self) -> Dict[str, Any]:
        self.checkin_calls += 1
        if self.raise_on_checkin:
            raise RuntimeError("签到写库失败")
        return self.checkin_result

    def load_progress(self) -> Any:
        return self.progress


class FakeHomeBridge(QObject):
    achievementsChanged = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.published: List[Any] = []

    def publish_achievements(self, data: Any) -> None:
        self.published.append(data)


class FakeContext:
    def __init__(self, config: FakeConfig | None = None, services: Dict[str, Any] | None = None) -> None:
        self.tasks = FakeTasks()
        self.ui = None
        self.config = config or FakeConfig()
        self.instances: Dict[str, Any] = dict(services or {})

    def register_instance(self, name: str, obj: Any, replace: bool = False) -> None:
        self.instances[name] = obj

    def try_get(self, name: str) -> Any:
        return self.instances.get(name)


def make(**kwargs: Any) -> tuple[StartupController, FakeContext, Dict[str, Any]]:
    account = kwargs.pop("account", None) or FakeAccountService()
    achievement = kwargs.pop("achievement", None) or FakeAchievementService()
    config = kwargs.pop("config", None) or FakeConfig()
    services = {"account": account, "achievement": achievement}
    ctx = FakeContext(config=config, services=services)
    statuses: List[tuple] = []
    updates: List[tuple] = []

    # 默认把两个会往外跑的注入点关掉：真实 updater 会发 HTTP，真实接线会碰账号文件。
    # 需要它们时由调用方显式传（下面的 setdefault 不覆盖调用方给的值）。
    kwargs.setdefault("launcher_wiring", lambda _launcher: None)
    kwargs.setdefault("update_checker", lambda: None)
    kwargs.setdefault("notice_fetcher", lambda: None)
    kwargs.setdefault("predownload_runner", lambda: None)
    # 协议全文默认也换成空实现：默认实现要读 16 KB 的 `TERMS_OF_USE.md`，
    # 而"默认实现确实读到了全文"由 `test_default_terms_loader_reads_the_real_document` 单独验。
    kwargs.setdefault("terms_loader", lambda: "")
    # 语言那一步默认"已选过"（`language_required` 返回 False）：不固定的话，
    # 用例会依赖开发机上那份 `config.json` 里有没有 `language` 键。
    kwargs.setdefault("language_required", lambda: False)

    controller = StartupController(
        ctx,
        ui_port=None,
        min_splash_ms=10,
        hard_timeout_ms=500,
        poll_ms=10,
        agreement_delay_ms=1,
        launcher_factory=lambda: object(),
        achievements_factory=lambda: object(),
        **kwargs,
    )
    controller.statusChanged.connect(lambda key, level: statuses.append((key, level)))
    controller.updateAvailable.connect(lambda version, body: updates.append((version, body)))
    return controller, ctx, {"account": account, "achievement": achievement, "statuses": statuses, "updates": updates}


@pytest.fixture(autouse=True)
def _qt_app() -> Any:
    yield _app()


# ─── A-25 Token 刷新 ───────────────────────────────────────────


def test_token_refresh_goes_through_the_account_service() -> None:
    controller, _ctx, parts = make()
    controller.start_post_ready_tasks()
    assert parts["account"].refreshed == 1


def test_post_ready_tasks_are_idempotent() -> None:
    """重复调用只跑一次（`_dismiss` 在超时/失败路径上可能走到两次）。"""
    controller, _ctx, parts = make()
    controller.start_post_ready_tasks()
    controller.start_post_ready_tasks()
    assert parts["account"].refreshed == 1
    assert parts["achievement"].checkin_calls == 1


def test_missing_services_are_skipped_silently() -> None:
    ctx = FakeContext(services={})
    controller = StartupController(
        ctx,
        ui_port=None,
        launcher_wiring=lambda _launcher: None,
        update_checker=lambda: None,
    )
    controller.start_post_ready_tasks()  # 不抛异常即通过


# ─── A-24 自动检查更新 ─────────────────────────────────────────


def test_update_check_is_skipped_when_disabled_in_config() -> None:
    called: List[int] = []
    controller, _ctx, parts = make(config=FakeConfig(auto_check_update=False),
                                   update_checker=lambda: called.append(1))
    controller.start_post_ready_tasks()
    assert called == []


def test_update_check_is_silent_when_there_is_nothing_new() -> None:
    controller, _ctx, parts = make(update_checker=lambda: None)
    controller.start_post_ready_tasks()
    assert parts["updates"] == [], "静默模式：没新版本不该弹任何东西"


def test_update_available_is_reported() -> None:
    controller, _ctx, parts = make(update_checker=lambda: {"version": "9.9.9", "body": "notes"})
    controller.start_post_ready_tasks()
    assert parts["updates"] == [("9.9.9", "notes")]


def test_update_check_failure_is_swallowed() -> None:
    def boom() -> Any:
        raise RuntimeError("网络不可达")

    controller, _ctx, parts = make(update_checker=boom)
    controller.start_post_ready_tasks()
    assert parts["updates"] == []


# ─── F-09 云同步 + 签到 ────────────────────────────────────────


def test_cloud_sync_runs_only_with_a_token() -> None:
    controller, _ctx, parts = make(config=FakeConfig(jdz_token=""))
    controller.start_post_ready_tasks()
    assert parts["achievement"].sync_calls == []

    controller2, _ctx2, parts2 = make(config=FakeConfig(jdz_token="tk"))
    controller2.start_post_ready_tasks()
    assert len(parts2["achievement"].sync_calls) == 1
    assert parts2["achievement"].sync_calls[0][1] == "tk"
    keys = [key for key, _level in parts2["statuses"]]
    assert "startup_ach_syncing" in keys and "startup_ach_synced" in keys


def test_cloud_sync_failure_is_reported_as_warning() -> None:
    controller, _ctx, parts = make(config=FakeConfig(jdz_token="tk"),
                                   achievement=FakeAchievementService(sync_ok=False))
    controller.start_post_ready_tasks()
    assert ("startup_ach_sync_failed", "warning") in parts["statuses"]


def test_checkin_success_reports_the_status_key() -> None:
    """F-09 的"签到成功"这一步在旧实现里**永远不会显示**（见服务层的说明）。"""
    controller, _ctx, parts = make()
    controller.start_post_ready_tasks()
    assert ("startup_checkin_ok", "success") in parts["statuses"]


def test_already_checked_in_does_not_report_anything() -> None:
    achievement = FakeAchievementService(checkin_result={"checked_in": False, "already": True, "streak": 4})
    controller, _ctx, parts = make(achievement=achievement)
    controller.start_post_ready_tasks()
    keys = [key for key, _level in parts["statuses"]]
    assert "startup_checkin_ok" not in keys
    assert "startup_checkin_failed" not in keys


def test_checkin_failure_is_reported_as_warning() -> None:
    achievement = FakeAchievementService()
    achievement.raise_on_checkin = True
    controller, _ctx, parts = make(achievement=achievement)
    controller.start_post_ready_tasks()
    assert ("startup_checkin_failed", "warning") in parts["statuses"]


def test_achievements_are_handed_to_the_home_bridge() -> None:
    bridge = FakeHomeBridge()
    controller, _ctx, parts = make()
    controller.set_bridges({"Home": bridge})
    controller.start_post_ready_tasks()
    assert bridge.published == [parts["achievement"].progress]


def test_achievement_publishing_survives_a_broken_bridge() -> None:
    class BadBridge:
        def publish_achievements(self, data: Any) -> None:
            raise RuntimeError("桥坏了")

    controller, _ctx, _parts = make()
    controller.set_bridges({"Home": BadBridge()})
    controller.start_post_ready_tasks()  # 不抛异常即通过


def test_missing_home_bridge_is_not_an_error() -> None:
    controller, _ctx, _parts = make()
    controller.start_post_ready_tasks()


# ─── D-162：协议**全文**（旧弹窗显示 TERMS_OF_USE.md，QML 版一度只显示摘要）──


def test_terms_text_comes_from_the_loader() -> None:
    controller, _ctx, _parts = make(terms_loader=lambda: "# 标题\n正文")
    assert controller.property("termsText") == "# 标题\n正文"


def test_terms_text_is_read_once() -> None:
    """`termsText` 是常量属性 —— 读盘只该发生一次（弹窗可能被打开多次）。"""
    calls: List[int] = []

    def loader() -> str:
        calls.append(1)
        return "全文"

    controller, _ctx, _parts = make(terms_loader=loader)
    assert controller.property("termsText") == "全文"
    assert controller.property("termsText") == "全文"
    assert len(calls) == 1


def test_terms_text_is_empty_when_the_loader_fails() -> None:
    """读不到时返回空串（QML 侧退回语言文件里的摘要），不抛异常。"""
    def boom() -> str:
        raise RuntimeError("磁盘坏了")

    controller, _ctx, _parts = make(terms_loader=boom)
    assert controller.property("termsText") == ""


def test_default_terms_loader_reads_the_real_document() -> None:
    """默认实现（生产路径）读的就是仓库根的 `TERMS_OF_USE.md`。"""
    controller, _ctx, _parts = make(terms_loader=None)
    # `make()` 默认注入了空实现，这里显式还原成生产实现
    controller._terms_loader = StartupController._default_terms_loader
    controller._terms_cache = None
    text = controller.property("termsText")
    assert text.startswith("#"), "默认实现没有读到协议全文"
    assert len(text) > 5000, f"只读到 {len(text)} 字符，像是摘要而不是全文"


# ─── 首次启动选语言（A-27；用户 2026-10-05 转达的网友需求）────────


def test_language_is_asked_on_first_run() -> None:
    """第一次启动：先弹语言，**不**直接弹协议（协议全文按语言渲染，先问再给条款）。"""
    controller, _ctx, _parts = make(language_required=lambda: True, config=FakeConfig(terms_consent=False))
    asked: List[int] = []
    agreed: List[int] = []
    controller.languageRequired.connect(lambda: asked.append(1))
    controller.agreementRequired.connect(lambda: agreed.append(1))

    controller._check_agreement()  # 链条入口（生产由 `agreement_delay_ms` 定时器触发）

    assert asked == [1]
    assert agreed == [], "语言还没选就问协议了"


def test_already_chosen_goes_straight_to_the_agreement() -> None:
    controller, _ctx, _parts = make(language_required=lambda: False, config=FakeConfig(terms_consent=False))
    asked: List[int] = []
    agreed: List[int] = []
    controller.languageRequired.connect(lambda: asked.append(1))
    controller.agreementRequired.connect(lambda: agreed.append(1))

    controller._check_agreement()

    assert asked == []
    assert agreed == [1]


def test_choosing_a_language_marks_it_and_continues() -> None:
    """选完语言：写下"已选过"并回到链条（该弹协议就弹协议）。"""
    config = FakeConfig(terms_consent=False, language_chosen=False)
    controller, _ctx, _parts = make(language_required=lambda: True, config=config)
    agreed: List[int] = []
    controller.agreementRequired.connect(lambda: agreed.append(1))

    controller._check_agreement()
    controller.chooseLanguage()

    assert config.language_chosen is True
    assert config.saved >= 1, "「已选过」必须落盘，否则下次启动还会问"
    assert agreed == [1]
    chrono = [entry[0] for entry in controller.describe()["chrono"]]
    assert "language_chosen" in chrono, f"事件流水里没有记下这次选择：{chrono}"


def test_choosing_a_language_with_consent_already_given_goes_to_the_notice() -> None:
    """协议已同意过的老用户：选完语言直接进公告，不再弹协议。"""
    config = FakeConfig(terms_consent=True, ai_privacy_consent=True, language_chosen=False)
    controller, _ctx, _parts = make(language_required=lambda: True, config=config)
    agreed: List[int] = []
    controller.agreementRequired.connect(lambda: agreed.append(1))

    controller._check_agreement()
    controller.chooseLanguage()

    assert agreed == []


def test_language_step_survives_a_broken_config() -> None:
    """配置坏掉时按"已选过"处理 —— 宁可少问一次，也不能把启动卡住。"""
    class Broken:
        @property
        def language_chosen(self) -> Any:
            raise RuntimeError("配置读不出来")

        def save_config(self) -> None:
            raise RuntimeError("配置写不进去")

    controller, _ctx, _parts = make(config=Broken(), language_required=None)
    agreed: List[int] = []
    controller.agreementRequired.connect(lambda: agreed.append(1))

    controller._check_agreement()  # 读坏了 → 跳过语言这一步（按"已选过"处理，不打扰用户）
    controller.chooseLanguage()    # 写坏了也不该抛

    assert agreed == [1]


# ─── A-22 公告重看 ─────────────────────────────────────────────


def test_replay_notice_reemits_without_refetching() -> None:
    controller, _ctx, _parts = make()
    seen: List[str] = []
    controller.noticeReady.connect(seen.append)

    assert controller.property("hasNotice") is False
    controller.noticeFetched("hello notice")
    assert controller.property("hasNotice") is True
    assert seen == ["hello notice"]

    controller.replayNotice()
    assert seen == ["hello notice", "hello notice"], "重看必须是**同一份内容**，不再拉一次"


def test_dismissing_a_replayed_notice_does_not_start_predownload() -> None:
    """重看关掉 ≠ 启动链条走完：不能再跑一次预下载（那是启动链条的一步）。"""
    started: List[int] = []
    controller, _ctx, _parts = make(predownload_runner=lambda: started.append(1))

    controller.noticeFetched("hello")
    controller.replayNotice()
    controller.dismissNotice()
    assert started == [], "重看关掉时不该触发预下载"

    controller.dismissNotice()  # 启动链条里的那一次：正常继续
    assert started == [1]


def test_replay_without_a_notice_does_nothing() -> None:
    controller, _ctx, _parts = make()
    seen: List[str] = []
    controller.noticeReady.connect(seen.append)
    controller.replayNotice()
    assert seen == []


def test_has_notice_changes_signal_is_emitted() -> None:
    controller, _ctx, _parts = make()
    changes: List[int] = []
    controller.noticeChanged.connect(lambda: changes.append(1))
    controller.noticeFetched("hello")
    assert changes == [1]
