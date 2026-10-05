"""阶段 2 任务 2.14（启动流程复刻）的回归守卫。

## 为什么这一组测试值钱

`06` 的 2.12 与风险 **R-34** 都点名：旧 `main.py` 的启动时序有 **4 条互相竞争的退出路径**
（≥1 秒显示 / 30 秒硬超时 / 初始化失败仍显示主窗口 / 关画面失败也继续）。
"简化实现就会丢容错"——所以每一条都要有断言，而且**不能用真实时间等 30 秒**：
`StartupController` 的时钟与三个毫秒参数都是注入点，这里用它们把 30 秒压成 200 毫秒。

另外钉住启动后的链条顺序：**协议 → 公告 → 预下载**（对照表 A-21 / A-22 / A-23），
含"公告拉取失败要静默跳过"这条旧行为。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, List

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.startup import (  # noqa: E402
    PHASE_FAILED,
    PHASE_READY,
    PHASE_SPLASH,
    StartupController,
)


def _app():
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


def pump(seconds: float, until: Callable[[], bool] | None = None) -> bool:
    """泵事件循环（定时器要靠它才会走）。"""
    app = _app()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.005)
    return until() if until is not None else True


class FakeTasks:
    """假任务框架：决定"后台初始化"是立刻跑完、挂住、还是丢进真线程。"""

    def __init__(self, mode: str = "run") -> None:
        self.mode = mode
        self.submitted: List[str] = []
        self._threads: List[threading.Thread] = []

    def submit(self, fn, **kwargs):
        self.submitted.append(str(kwargs.get("name", "")))
        if self.mode == "run":
            fn()
        elif self.mode == "drop":
            pass  # 永远不就绪 —— 用来测硬超时
        elif self.mode == "thread":
            t = threading.Thread(target=fn, daemon=True)
            t.start()
            self._threads.append(t)


class FakeContext:
    def __init__(self, tasks: Any = None) -> None:
        self.tasks = tasks if tasks is not None else FakeTasks("run")
        self.ui = None
        self.instances: dict = {}

    def register_instance(self, name: str, obj: Any, replace: bool = False) -> None:
        self.instances[name] = obj


def make(
    *,
    tasks_mode: str = "run",
    launcher: Callable[[], Any] | None = None,
    achievements: Callable[[], Any] | None = None,
    notice: Callable[[], Any] | None = None,
    predownload: Callable[[], Any] | None = None,
    min_splash_ms: int = 150,
    hard_timeout_ms: int = 1000,
    agreement_delay_ms: int = 10,
) -> tuple[StartupController, FakeContext]:
    ctx = FakeContext(FakeTasks(tasks_mode))
    ctl = StartupController(
        ctx,
        ui_port=None,
        min_splash_ms=min_splash_ms,
        hard_timeout_ms=hard_timeout_ms,
        poll_ms=20,
        agreement_delay_ms=agreement_delay_ms,
        launcher_factory=launcher or (lambda: object()),
        achievements_factory=achievements or (lambda: object()),
        notice_fetcher=notice or (lambda: None),
        predownload_runner=predownload or (lambda: None),
        # 阶段 3 任务 3.1 新增的三条注入点：默认实现会碰真实账号文件、**发 HTTP 检查更新**、
        # 并读真实 `config.json` 判断"要不要问界面语言" —— 本文件全是毫秒级时序断言，
        # 一次网络往返就能让它们全红（实测：`test_minimum_duration_is_what_makes_the_difference`
        # 因此超时过）。语言那一条固定成"已选过"，链条才是这里要验的 协议 → 公告 → 预下载。
        launcher_wiring=lambda _launcher: None,
        update_checker=lambda: None,
        language_required=lambda: False,
    )
    return ctl, ctx


@pytest.fixture(autouse=True)
def _app_ready():
    _app()
    yield


# ─── 1. 路径 1：两条初始化都就绪 + 至少显示 1 秒 ─────────────────


def test_splash_shows_at_least_the_minimum_duration():
    """**≥1 秒显示**（旧实现的行为）。断言的是真实耗时，不是"有没有被调用"。"""
    ctl, _ = make(min_splash_ms=250)
    t0 = time.monotonic()
    ctl.start()
    assert ctl.phase == PHASE_SPLASH
    assert ctl.dismissed is False, "初始化瞬间就绪，但启动画面不该立刻消失（至少要显示 min_splash_ms）"
    pump(1.0, lambda: ctl.dismissed)
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert ctl.dismissed is True, "启动画面一直没关"
    assert elapsed_ms >= 250, f"只显示了 {elapsed_ms:.0f}ms，少于 min_splash_ms=250"
    assert ctl.describe()["dismiss_reason"] == "ready"
    assert ctl.phase == PHASE_READY


def test_minimum_duration_is_what_makes_the_difference():
    """**非空断言的证明**：把 min_splash_ms 降到 0，同一个测量就会小于 250ms。

    如果上一条测试是空断言（例如它其实在测别的东西），这一条不会同时成立。
    """
    ctl, _ = make(min_splash_ms=0)
    t0 = time.monotonic()
    ctl.start()
    pump(1.0, lambda: ctl.dismissed)
    elapsed_ms = (time.monotonic() - t0) * 1000
    assert ctl.dismissed is True
    assert elapsed_ms < 250, f"min_splash_ms=0 时竟然也等了 {elapsed_ms:.0f}ms —— 上一条测的不是这个"


def test_splash_waits_for_both_initialisations():
    """**两条初始化都要等**：只有 launcher 就绪、成就引擎还挂着时不许关画面。"""
    ctl, _ = make(tasks_mode="drop", min_splash_ms=0, hard_timeout_ms=5000)
    ctl.start()
    pump(0.4)
    assert ctl.launcherReady is False and ctl.achievementsReady is False
    assert ctl.dismissed is False, "初始化都没就绪就把启动画面关了"


def test_one_slow_initialisation_keeps_the_splash():
    """真线程里放慢其中一个，另一个已就绪 —— 仍然不能关。"""
    ctl, ctx = make(tasks_mode="thread", min_splash_ms=0, hard_timeout_ms=5000)

    def slow_launcher():
        time.sleep(0.25)
        return object()

    ctl._launcher_factory = slow_launcher  # noqa: SLF001 - 测试注入
    ctl.start()
    pump(0.1)
    assert ctl.achievementsReady is True and ctl.launcherReady is False
    assert ctl.dismissed is False
    pump(1.0, lambda: ctl.dismissed)
    assert ctl.dismissed is True
    assert "launcher" in ctx.instances, "launcher 就绪后应当注册进上下文"


# ─── 2. 路径 2：30 秒硬超时 ────────────────────────────────────


def test_hard_timeout_forces_dismissal():
    """就绪永远不来 → 到 hard_timeout_ms 必须**强制**关画面（不能永远卡在启动画面）。"""
    ctl, _ = make(tasks_mode="drop", min_splash_ms=0, hard_timeout_ms=200)
    ctl.start()
    assert pump(1.5, lambda: ctl.dismissed), "硬超时没有生效 —— 启动画面会一直卡着"
    info = ctl.describe()
    assert info["dismiss_reason"] == "timeout"
    assert info["launcher_ready"] is False
    assert ctl.phase == PHASE_READY, "硬超时之后仍然要进主界面（不是 failed）"


# ─── 3. 路径 3：初始化失败仍显示主窗口 ─────────────────────────


def test_init_failure_still_dismisses_and_reports():
    """**旧实现的关键容错**：核心初始化失败时**仍然显示主窗口**并把错误写进状态栏。"""
    def boom():
        raise RuntimeError("模拟 launcher 初始化失败")

    ctl, _ = make(launcher=boom, min_splash_ms=0)
    failed: List[tuple] = []
    ctl.coreFailed.connect(lambda title, msg: failed.append((title, msg)))
    ctl.start()
    assert pump(1.0, lambda: ctl.dismissed), "初始化失败后启动画面没关"
    assert ctl.phase == PHASE_FAILED
    assert failed, "没有发出 coreFailed —— 用户看不到失败原因"
    assert "模拟 launcher 初始化失败" in failed[0][1]
    # 主窗口照常显示：`App.qml` 的 visible 绑的是 `Startup.dismissed`
    assert ctl.dismissed is True


def test_achievement_failure_does_not_break_startup():
    """成就引擎失败**不算**启动失败（旧实现同：只记 error）。"""
    def boom():
        raise RuntimeError("成就数据库坏了")

    ctl, _ = make(achievements=boom, min_splash_ms=0)
    ctl.start()
    assert pump(1.0, lambda: ctl.dismissed)
    assert ctl.phase == PHASE_READY, "成就引擎失败不该把启动判成 failed"
    assert ctl.achievementsReady is True  # "尝试过了"= 就绪（不再等）


# ─── 4. 状态文本：给的是 i18n 键，不是中文 ─────────────────────


def test_status_text_is_an_i18n_key_not_a_literal():
    """Python 侧不持有界面文案（闸门 R3 的精神）：状态文本给的是**键名**。"""
    ctl, _ = make(min_splash_ms=0)
    seen: List[tuple] = []
    ctl.statusChanged.connect(lambda text, level: seen.append((text, level)))
    ctl.start()
    pump(1.0, lambda: ctl.dismissed)
    assert seen, "一条状态都没发"
    for text, _level in seen:
        assert text.isascii(), f"状态文本里有非 ASCII 字符（像是写死了中文）: {text!r}"
    assert any(level == "success" for _t, level in seen), "就绪时应当发一条 success 状态"


# ─── 5. 启动链条：协议 → 公告 → 预下载 ─────────────────────────


def test_agreement_required_then_chain_continues(monkeypatch):
    """协议未同意时先要协议；同意之后才继续公告与预下载。"""
    import config as config_module

    cfg = config_module.config
    monkeypatch.setattr(cfg, "terms_consent", False, raising=False)
    monkeypatch.setattr(cfg, "ai_privacy_consent", False, raising=False)
    monkeypatch.setattr(cfg, "save_config", lambda: True, raising=False)

    calls: List[str] = []
    ctl, _ = make(
        min_splash_ms=0,
        notice=lambda: "公告正文",
        predownload=lambda: calls.append("predownload"),
    )
    agreed: List[int] = []
    notices: List[str] = []
    finished: List[int] = []
    ctl.agreementRequired.connect(lambda: agreed.append(1))
    ctl.noticeReady.connect(notices.append)
    ctl.chainFinished.connect(lambda: finished.append(1))

    ctl.start()
    assert pump(1.5, lambda: bool(agreed)), "没有要求协议同意"
    assert not notices, "还没同意协议就去拉公告了"
    assert cfg.terms_consent is False, "用户还没点同意，标志不该提前写"

    # 模拟 QML 里那一步：勾选两个框之后点「同意并继续」→ StartupDialogs 调这个槽
    ctl.confirmAgreement()
    assert cfg.terms_consent is True and cfg.ai_privacy_consent is True, "同意状态没有写回配置"

    assert pump(1.5, lambda: bool(notices)), "同意之后没有进入公告那一步"
    assert notices == ["公告正文"]
    assert not calls, "公告还没关闭就开始预下载了"

    ctl.dismissNotice()
    assert pump(1.5, lambda: bool(calls)), "关闭公告之后没有进入预下载"
    assert pump(1.5, lambda: bool(finished)), "链条没有收尾"


def test_agreement_skipped_when_already_consented(monkeypatch):
    """两个标志都为真时**跳过**协议直接拉公告（老用户不该每次启动都被拦）。"""
    import config as config_module

    cfg = config_module.config
    monkeypatch.setattr(cfg, "terms_consent", True, raising=False)
    monkeypatch.setattr(cfg, "ai_privacy_consent", True, raising=False)

    ctl, _ = make(min_splash_ms=0, notice=lambda: "公告")
    agreed: List[int] = []
    notices: List[str] = []
    ctl.agreementRequired.connect(lambda: agreed.append(1))
    ctl.noticeReady.connect(notices.append)
    ctl.start()
    assert pump(1.5, lambda: bool(notices))
    assert agreed == [], "已经同意过了还弹协议"


def test_notice_failure_skips_straight_to_predownload():
    """公告拉不到（网络问题）→ **静默跳过**，直接进预下载（旧实现同）。"""
    calls: List[str] = []

    def boom() -> Any:
        raise RuntimeError("网络不通")

    ctl, _ = make(min_splash_ms=0, notice=boom, predownload=lambda: calls.append("predownload"))
    notices: List[str] = []
    ctl.noticeReady.connect(notices.append)
    ctl.start()
    assert pump(2.0, lambda: bool(calls)), "公告失败后没有继续到预下载"
    assert notices == [], "公告失败却发了 noticeReady"


def test_empty_notice_skips_to_predownload():
    calls: List[str] = []
    ctl, _ = make(min_splash_ms=0, notice=lambda: None, predownload=lambda: calls.append("p"))
    ctl.start()
    assert pump(2.0, lambda: bool(calls))


# ─── 6. 幂等与退出 ─────────────────────────────────────────────


def test_start_is_idempotent():
    ctl, _ = make(min_splash_ms=0)
    ctl.start()
    first = ctl.describe()["chrono"]
    ctl.start()  # 第二次
    assert len(ctl.describe()["chrono"]) == len(first), "start() 被重复调用却重跑了一遍"

    pump(1.0, lambda: ctl.dismissed)


def test_stop_halts_the_timers():
    ctl, _ = make(tasks_mode="drop", min_splash_ms=0, hard_timeout_ms=200)
    ctl.start()
    ctl.stop()
    pump(0.6)
    assert ctl.dismissed is False, "stop() 之后定时器还在跑"


def test_describe_is_json_friendly():
    import json

    ctl, _ = make(min_splash_ms=0)
    ctl.start()
    pump(1.0, lambda: ctl.dismissed)
    json.dumps(ctl.describe())  # 不抛异常就算过
    assert ctl.describe()["phase"] in (PHASE_SPLASH, PHASE_READY, PHASE_FAILED)
