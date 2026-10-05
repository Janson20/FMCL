"""`app/bridges/home_bridge.py` 的回归守卫（阶段 3 任务 3.1）。

桥这一层只有三件事要做对，本文件就守这三件：

1. **参数转换**：把服务给的 i18n 键 + 参数翻成当前语言的一句话（键都以字面量写在
   `_status_text()` 里，所以这里逐个键断言"翻出来的是人话，不是键名"）；
2. **状态映射**：`GameService` 的状态 → 按钮可用性（能不能启动 / 能不能强杀）；
3. **缺席不崩**：任一服务取不到时属性返回空值、槽只记日志。

界面本身（真的显示出来了没有）由 `tests/test_home_page_qml.py` 的 QML 探针管，
这里不重复。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.bridges.home_bridge import HomeBridge  # noqa: E402
from services.game_service import (  # noqa: E402
    STATE_CRASHED,
    STATE_EXITED,
    STATE_IDLE,
    STATE_LAUNCHING,
    STATE_RUNNING,
    STATE_WAITING,
)


def _app() -> Any:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


@pytest.fixture(autouse=True)
def _qt_app() -> Any:
    yield _app()


@pytest.fixture(autouse=True, scope="module")
def _chinese_locale() -> Any:
    """**整个模块**用中文语言文件（生产的 QML 入口由 `Tr` 桥做同一件事）。

    不初始化的话 `_()` 会返回键名本身 —— 那样"文案翻好了没有"这类断言就没有意义了
    （`services/i18n_service` 的全局状态在测试之间会互相影响，所以用完还原）。
    """
    from services import i18n_service

    saved_lang = i18n_service.get_current_language()
    saved = dict(i18n_service._translations)
    i18n_service.init_i18n("zh_CN")
    yield
    i18n_service._translations.clear()
    i18n_service._translations.update(saved)
    i18n_service._current_language = saved_lang


class FakeGame:
    name = "game"

    def __init__(self) -> None:
        self.observer: Any = None
        self.state = STATE_IDLE
        self.last_launched_version = ""
        self.minimize_after = False
        self.launched: List[str] = []
        self.kills = 0

    def set_observer(self, observer: Any) -> None:
        self.observer = observer

    def launch(self, version_id: str, **_kwargs: Any) -> None:
        self.launched.append(version_id)

    def kill(self) -> bool:
        self.kills += 1
        return True


class FakeAccount:
    name = "account"

    def __init__(self) -> None:
        self.summary = {"has_account": True, "id": "1", "name": "Alex", "type": "offline",
                        "type_key": "account_type_offline", "uuid": "u-1"}
        self.skin = "C:/skins/a.png"
        self.select_result: Any = (True, "skin_installed", {"filename": "a.png"})
        self.removed = 0

    def account_summary(self) -> Dict[str, Any]:
        return dict(self.summary)

    def skin_path(self) -> str:
        return self.skin

    def select_skin(self, path: str) -> Any:
        return self.select_result

    def remove_skin(self) -> None:
        self.removed += 1


class FakeAchievement:
    name = "achievement"

    def __init__(self) -> None:
        self.streak = 2

    def checkin_streak(self) -> int:
        return self.streak

    def summarize(self, data: Any) -> Any:
        class _S:
            total = 10
            unlocked = 4
            percent = 40.0

        return _S()


class FakeContext:
    def __init__(self, services: Dict[str, Any]) -> None:
        self._services = services

    def try_get(self, name: str) -> Any:
        return self._services.get(name)


def make(services: Optional[Dict[str, Any]] = None) -> tuple[HomeBridge, Dict[str, Any]]:
    parts: Dict[str, Any] = {
        "game": FakeGame(),
        "account": FakeAccount(),
        "achievement": FakeAchievement(),
    }
    if services is not None:
        parts.update(services)
    bridge = HomeBridge()
    bridge.bind(FakeContext(parts))
    return bridge, parts


# ─── 状态 → 按钮可用性 ─────────────────────────────────────────


class TestGameStateMapping:
    def test_launch_enabled_only_with_a_version(self) -> None:
        bridge, parts = make()
        assert bridge.canLaunch is False, "没有最近版本时不该能点启动"
        parts["game"].last_launched_version = "1.20.4"
        bridge.refresh()
        assert bridge.canLaunch is True

    @pytest.mark.parametrize(
        "state,can_launch,can_kill,starting",
        [
            (STATE_IDLE, True, False, False),
            (STATE_LAUNCHING, False, True, True),
            (STATE_WAITING, False, True, True),
            # 旧界面在启动完成后就恢复可点（`ui/app_handlers.py:1160`），本轮保持
            (STATE_RUNNING, True, True, False),
            (STATE_EXITED, True, False, False),
            (STATE_CRASHED, True, False, False),
        ],
    )
    def test_button_availability_per_state(
        self, state: str, can_launch: bool, can_kill: bool, starting: bool
    ) -> None:
        bridge, parts = make()
        parts["game"].last_launched_version = "1.20.4"
        bridge.refresh()  # 最近版本是服务里的值，刷新后才进桥
        bridge.on_state(state)
        assert bridge.canLaunch is can_launch, state
        assert bridge.canKill is can_kill, state
        assert bridge.gameStarting is starting, state
        assert bridge.gameState == state

    def test_running_flag_covers_the_three_active_states(self) -> None:
        bridge, _parts = make()
        for state, expected in (
            (STATE_IDLE, False),
            (STATE_LAUNCHING, True),
            (STATE_WAITING, True),
            (STATE_RUNNING, True),
            (STATE_EXITED, False),
        ):
            bridge.on_state(state)
            assert bridge.gameRunning is expected, state


# ─── 槽 → 服务 ─────────────────────────────────────────────────


class TestSlots:
    def test_launch_uses_the_recent_version_when_none_is_given(self) -> None:
        bridge, parts = make()
        parts["game"].last_launched_version = "1.20.4"
        bridge.refresh()
        bridge.launch("")
        assert parts["game"].launched == ["1.20.4"]

    def test_launch_without_any_version_reports_a_status(self) -> None:
        bridge, parts = make()
        seen: List[tuple] = []
        bridge.statusMessage.connect(lambda text, level: seen.append((text, level)))
        bridge.launch("")
        assert parts["game"].launched == []
        assert seen and seen[0][1] == "error"
        assert seen[0][0] == "请输入版本 ID", "状态文案必须翻好，不能是键名"

    def test_kill_delegates(self) -> None:
        bridge, parts = make()
        bridge.killGame()
        assert parts["game"].kills == 1

    def test_select_skin_reports_success_and_refreshes(self) -> None:
        bridge, parts = make()
        seen: List[tuple] = []
        bridge.statusMessage.connect(lambda text, level: seen.append((text, level)))
        bridge.selectSkin("C:/skins/a.png")
        assert seen and seen[0][1] == "success"
        assert "a.png" in seen[0][0]

    def test_select_skin_reports_the_service_key_and_params(self) -> None:
        account = FakeAccount()
        account.select_result = (False, "skin_size_invalid", {"width": 32, "height": 32})
        bridge, _parts = make({"account": account})
        seen: List[tuple] = []
        bridge.statusMessage.connect(lambda text, level: seen.append((text, level)))
        bridge.selectSkin("C:/skins/bad.png")
        assert seen[0][1] == "warning"
        assert "32x32" in seen[0][0], "参数要真的填进那句话里"

    def test_remove_skin_delegates(self) -> None:
        bridge, parts = make()
        bridge.removeSkin()
        assert parts["account"].removed == 1

    def test_open_versions_without_nav_is_a_no_op(self) -> None:
        bridge, _parts = make()
        bridge.openVersions()  # 不抛异常即通过


# ─── 服务回调 → 属性 / 信号 ────────────────────────────────────


class TestObserverCallbacks:
    def test_launch_result_updates_the_recent_version(self) -> None:
        bridge, _parts = make()
        changes: List[int] = []
        bridge.recentVersionChanged.connect(lambda: changes.append(1))
        bridge.on_launch_result("1.20.4", "1.20.4-forge-49.0.26", True, "")
        assert bridge.recentVersion == "1.20.4-forge-49.0.26"
        assert bridge.hasRecentVersion is True
        assert changes == [1]

    def test_failed_launch_does_not_change_the_recent_version(self) -> None:
        bridge, _parts = make()
        bridge.on_launch_result("1.20.4", None, False, "boom")
        assert bridge.recentVersion == ""

    def test_window_detected_asks_for_minimise_only_when_configured(self) -> None:
        bridge, parts = make()
        seen: List[str] = []
        bridge.statusMessage.connect(lambda text, level: seen.append(text))
        requested: List[int] = []
        bridge.minimizeWindowRequested.connect(lambda: requested.append(1))

        parts["game"].minimize_after = False
        bridge.on_window_detected()
        assert requested == []
        assert seen[-1] == "游戏已就绪"

        parts["game"].minimize_after = True
        bridge.on_window_detected()
        assert requested == [1]
        assert seen[-1] == "游戏窗口已出现，启动器已最小化"

    def test_crash_reports_the_exit_code_in_the_message(self) -> None:
        bridge, _parts = make()
        crashes: List[tuple] = []
        bridge.crashDetected.connect(lambda code, message: crashes.append((code, message)))
        bridge.on_game_exit(7, True, {"crash_report": "x"}, False)
        assert crashes == [(7, "游戏异常退出 (退出码: 7)")]

    def test_clean_exit_reports_the_normal_message(self) -> None:
        bridge, _parts = make()
        seen: List[str] = []
        bridge.statusMessage.connect(lambda text, level: seen.append(text))
        bridge.on_game_exit(0, False, {}, False)
        assert seen == ["游戏已正常退出"]

    def test_a_killed_game_does_not_claim_it_exited_normally(self) -> None:
        """强杀之后不再紧跟一句"游戏已正常退出"。

        旧实现在这里会**两句都发**（先"游戏进程已强制结束"、再"游戏已正常退出"）——
        见 `07-known-defects.md` 的 D-162。本桥刻意只留前一句。
        """
        bridge, _parts = make()
        seen: List[str] = []
        bridge.statusMessage.connect(lambda text, level: seen.append(text))
        bridge.on_game_exit(-1, False, {}, True)
        assert seen == []

    def test_broken_observer_arguments_do_not_raise(self) -> None:
        bridge, _parts = make()
        bridge.on_status("unknown_key", "info", {})
        bridge.on_auto_backup("world", "msg", False)


# ─── 成就 / 账号 / 皮肤属性 ────────────────────────────────────


class TestDerivedProperties:
    def test_account_properties_come_from_the_service(self) -> None:
        bridge, _parts = make()
        assert bridge.hasAccount is True
        assert bridge.accountName == "Alex"
        assert bridge.accountTypeKey == "account_type_offline"
        assert bridge.accountUuid == "u-1"

    def test_skin_properties_show_only_the_file_name(self) -> None:
        bridge, _parts = make()
        assert bridge.hasSkin is True
        assert bridge.skinFileName == "a.png"

    def test_checkin_streak_comes_from_the_achievement_service(self) -> None:
        bridge, _parts = make()
        assert bridge.checkinStreak == 2

    def test_publish_achievements_sets_the_summary(self) -> None:
        bridge, _parts = make()
        changes: List[int] = []
        bridge.achievementsChanged.connect(lambda: changes.append(1))
        bridge.publish_achievements([{"id": "x"}])
        assert bridge.achievementsLoaded is True
        assert (bridge.achievementUnlocked, bridge.achievementTotal) == (4, 10)
        assert changes == [1]

    def test_publish_without_engine_keeps_loading_state(self) -> None:
        bridge, _parts = make()
        bridge.publish_achievements(None)
        assert bridge.achievementsLoaded is False

    def test_ready_flips_after_the_first_refresh(self) -> None:
        bridge = HomeBridge()
        assert bridge.ready is False
        changes: List[int] = []
        bridge.readyChanged.connect(lambda: changes.append(1))
        bridge.bind(FakeContext({"game": FakeGame(), "account": FakeAccount(), "achievement": FakeAchievement()}))
        assert bridge.ready is True
        assert changes == [1]


# ─── 缺席不崩 ──────────────────────────────────────────────────


class TestDegradedModes:
    def test_every_property_works_without_services(self) -> None:
        bridge = HomeBridge()
        bridge.bind(None)
        assert bridge.hasAccount is False
        assert bridge.accountName == ""
        assert bridge.hasSkin is False
        assert bridge.skinFileName == ""
        assert bridge.hasRecentVersion is False
        assert bridge.gameState == "idle"
        assert bridge.canLaunch is False
        assert bridge.canKill is False
        assert bridge.checkinStreak == 0
        assert bridge.hasNotice is False

    def test_slots_without_services_are_no_ops(self) -> None:
        bridge = HomeBridge()
        bridge.bind(None)
        bridge.launch("1.20.4")
        bridge.killGame()
        bridge.selectSkin("x.png")
        bridge.removeSkin()
        bridge.openNotice()
        bridge.openVersions()
        bridge.refresh()

    def test_broken_service_calls_do_not_raise(self) -> None:
        class Broken:
            def account_summary(self) -> Any:
                raise RuntimeError("坏了")

            def skin_path(self) -> Any:
                raise RuntimeError("坏了")

        bridge = HomeBridge()
        bridge.bind(FakeContext({"account": Broken()}))
        assert bridge.hasAccount is False
        assert bridge.hasSkin is False


# ─── 文案表 ────────────────────────────────────────────────────


class TestStatusText:
    @pytest.mark.parametrize(
        "key,params,expected",
        [
            ("game_launching", {"version": "1.20.4"}, "正在启动 1.20.4..."),
            ("game_launched", {"version": "1.20.4"}, "1.20.4 已启动，等待游戏窗口..."),
            ("game_launch_failed", {"version": "1.20.4"}, "1.20.4 启动失败"),
            ("game_launch_error", {"error": "boom"}, "启动错误: boom"),
            ("game_window_detected", {}, "游戏窗口已出现，启动器已最小化"),
            ("game_window_ready", {}, "游戏已就绪"),
            ("game_exited", {}, "游戏已正常退出"),
            ("game_crashed", {"code": 3}, "游戏异常退出 (退出码: 3)"),
            ("game_process_killed", {}, "游戏进程已强制结束"),
            ("game_no_process", {}, "没有正在运行的游戏进程"),
            ("version_id_required", {}, "请输入版本 ID"),
        ],
    )
    def test_status_keys_translate_with_parameters(self, key: str, params: Dict[str, Any], expected: str) -> None:
        """每个键都要翻成**人话**（而不是键名）——键以字面量写在 `_status_text()` 里，
        所以这里能逐个断言；文案本身来自语言文件。"""
        from services import i18n_service

        saved_lang = i18n_service.get_current_language()
        saved = dict(i18n_service._translations)
        try:
            i18n_service.init_i18n("zh_CN")
            bridge, _parts = make()
            seen: List[str] = []
            bridge.statusMessage.connect(lambda text, level: seen.append(text))
            bridge.on_status(key, "info", params)
            assert seen == [expected]
        finally:
            i18n_service._translations.clear()
            i18n_service._translations.update(saved)
            i18n_service._current_language = saved_lang

    def test_unknown_key_falls_back_to_the_key_name(self) -> None:
        """缺键时界面显示键名（与旧 `_(key)` 一致），而不是空白。"""
        bridge, _parts = make()
        seen: List[str] = []
        bridge.statusMessage.connect(lambda text, level: seen.append(text))
        bridge.on_status("some_unknown_key", "info", {})
        assert seen == ["some_unknown_key"]
