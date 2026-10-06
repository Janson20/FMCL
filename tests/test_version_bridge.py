"""`app/bridges/version_bridge.py` 的回归守卫（阶段 3 任务 3.2）。

桥这一层只有四件事要做对，本文件就守这四件：

1. **状态映射**：`VersionService` 的三态 + 行数据 → 页面三态与可见列表；
2. **参数转换**：服务给的 i18n 键 + 参数 → 当前语言的一句话（键都以字面量写在
   `_status_text()` 里，所以逐个键断言"翻出来的是人话，不是键名"）；
3. **动作转调用**：QML 的槽 → 服务/游戏服务的方法、路由跳转；
4. **缺席不崩**：服务取不到时属性返回空值、槽只记日志。

界面本身（真的显示出来了没有）由 `tests/test_versions_page_qml.py` 的 QML 探针管。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.bridges.version_bridge import VersionsBridge  # noqa: E402
from services.game_service import (  # noqa: E402
    STATE_CRASHED,
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
    """整个模块用中文语言文件：否则 `_()` 返回键名，"翻好了没有"就没法断言。"""
    from services import i18n_service

    saved_lang = i18n_service.get_current_language()
    saved = dict(i18n_service._translations)
    i18n_service.init_i18n("zh_CN")
    yield
    i18n_service._translations.clear()
    i18n_service._translations.update(saved)
    i18n_service._current_language = saved_lang


# ─── 替身 ──────────────────────────────────────────────────────


class FakeVersionService:
    """`VersionService` 的替身：只实现桥用到的那几个方法，并把调用记下来。"""

    name = "version"

    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None) -> None:
        self.rows = list(rows or [])
        self.observers: List[Any] = []
        self.calls: List[Tuple[Any, ...]] = []
        self.handle = object()
        self.detail_data: Dict[str, Any] = {"exists": True, "modsCount": 7, "path": "/tmp/v"}
        self.open_ok = True

    def set_observer(self, observer: Any) -> None:
        self.observers.append(observer)

    def load(self, *, force: bool = False) -> Any:
        self.calls.append(("load", force))
        return self.handle

    def filtered(self, text: str = "", sort_key: str = "name") -> List[Dict[str, Any]]:
        self.calls.append(("filtered", text, sort_key))
        rows = [dict(row) for row in self.rows]
        if text:
            rows = [row for row in rows if text.lower() in str(row.get("id", "")).lower()]
        if sort_key == "vanilla":
            rows.sort(key=lambda r: str(r.get("vanilla", "")))
        else:
            rows.sort(key=lambda r: str(r.get("id", "")))
        return rows

    def detail(self, version_id: str) -> Dict[str, Any]:
        self.calls.append(("detail", version_id))
        data = dict(self.detail_data)
        data["id"] = version_id
        return data

    def rename(self, version_id: str) -> Any:
        self.calls.append(("rename", version_id))
        return self.handle

    def remove(self, version_id: str) -> Any:
        self.calls.append(("remove", version_id))
        return self.handle

    def verify(self, version_id: str) -> Any:
        self.calls.append(("verify", version_id))
        return self.handle

    def open_folder(self, version_id: str) -> bool:
        self.calls.append(("open_folder", version_id))
        return self.open_ok


class FakeGame:
    name = "game"

    def __init__(self) -> None:
        self.observers: List[Any] = []
        self.state = STATE_IDLE
        self.launched: List[str] = []
        self.kills = 0

    def add_observer(self, observer: Any) -> None:
        self.observers.append(observer)

    def set_observer(self, observer: Any) -> None:
        self.observers = [observer]

    def launch(self, version_id: str, **_kwargs: Any) -> None:
        self.launched.append(version_id)

    def kill(self) -> bool:
        self.kills += 1
        return True


class FakeNav:
    def __init__(self) -> None:
        self.pushes: List[Tuple[str, Dict[str, Any]]] = []
        self.backs = 0

    def push(self, route_id: str, params: Optional[Dict[str, Any]] = None) -> bool:
        self.pushes.append((route_id, dict(params or {})))
        return True

    def goBack(self) -> bool:  # noqa: N802
        self.backs += 1
        return True


class FakeEngine:
    def __init__(self, nav: Optional[FakeNav] = None) -> None:
        self._fmcl_bridges = {"Nav": nav} if nav is not None else {}


class FakeContext:
    def __init__(self, services: Dict[str, Any]) -> None:
        self._services = services

    def try_get(self, name: str) -> Any:
        return self._services.get(name)


def row(version_id: str, **kwargs: Any) -> Dict[str, Any]:
    data = {
        "id": version_id,
        "display": version_id,
        "vanilla": kwargs.get("vanilla", version_id),
        "loader": kwargs.get("loader", ""),
        "loaderVersion": kwargs.get("loaderVersion", ""),
        "hasLoader": bool(kwargs.get("loader")),
        "state": "original",
        "reliable": True,
        "javaMajor": kwargs.get("javaMajor", 0),
    }
    return data


def make(
    rows: Optional[List[Dict[str, Any]]] = None,
    with_nav: bool = False,
    with_game: bool = True,
) -> Tuple[VersionsBridge, FakeVersionService, FakeGame, FakeNav]:
    service = FakeVersionService(rows if rows is not None else [row("1.20.4"), row("1.19.2")])
    game = FakeGame()
    nav = FakeNav()
    services: Dict[str, Any] = {"version": service}
    if with_game:
        services["game"] = game
    bridge = VersionsBridge()
    bridge.bind(FakeContext(services))
    if with_nav:
        bridge.use_engine(FakeEngine(nav))
    return bridge, service, game, nav


def feed(bridge: VersionsBridge, service: FakeVersionService, rows: List[Dict[str, Any]],
         error: str = "") -> None:
    """模拟一次真实扫描：假服务的行数据与桥收到的通知**必须一致**。"""
    service.rows = list(rows)
    bridge.on_versions(list(rows), error)


# ─── 装配 ──────────────────────────────────────────────────────


class TestWiring:
    def test_bind_takes_the_service_and_starts_a_load(self) -> None:
        bridge, service, _game, _nav = make()
        assert service.observers == [bridge]
        assert ("load", False) in service.calls
        assert bridge.loadState == "loading", "还没拿到数据时是加载中"

    def test_game_observation_uses_a_dedicated_watcher(self) -> None:
        """首页与版本页都在观察游戏服务；本桥挂的是专用小对象（避免重复 Toast）。"""
        bridge, service, game, _nav = make()
        assert len(game.observers) == 1
        assert game.observers[0] is not bridge
        game.observers[0].on_state(STATE_RUNNING)
        assert bridge.gameState == STATE_RUNNING

    def test_bind_without_context_does_not_raise(self) -> None:
        bridge = VersionsBridge()
        bridge.bind(None)
        assert bridge.versions == []
        bridge.load()
        assert bridge.loadState == "error"

    def test_missing_game_service_is_survivable(self) -> None:
        bridge, service, _game, _nav = make(with_game=False)
        assert bridge.gameState == "idle"
        assert bridge.canKill is False
        bridge.launch("1.20.4")  # 不抛异常

    def test_use_engine_without_nav_does_not_raise(self) -> None:
        bridge, service, _game, _nav = make()
        bridge.use_engine(FakeEngine(None))
        bridge.goBack()  # Nav 缺席时只记日志


# ─── 列表与三态 ────────────────────────────────────────────────


class TestListState:
    def test_rows_become_ready(self) -> None:
        bridge, service, _game, _nav = make()
        changed: List[int] = []
        bridge.versionsChanged.connect(lambda: changed.append(1))

        feed(bridge, service, [row("1.20.4")], "")

        assert bridge.loadState == "ready"
        assert bridge.ready is True
        assert bridge.count == 1
        assert bridge.totalCount == 1
        assert changed, "内容变了要发 versionsChanged"

    def test_no_rows_is_empty_not_error(self) -> None:
        bridge, service, _game, _nav = make()
        feed(bridge, service, [], "")
        assert bridge.loadState == "empty"
        assert bridge.ready is True

    def test_error_carries_the_reason(self) -> None:
        bridge, service, _game, _nav = make()
        states: List[str] = []
        bridge.loadStateChanged.connect(lambda: states.append(bridge.loadState))

        feed(bridge, service, [], "扫描炸了")

        assert bridge.loadState == "error"
        assert bridge.loadError == "扫描炸了"
        assert states == ["error"]

    def test_visible_list_is_filtered_by_the_service(self) -> None:
        bridge, service, _game, _nav = make()
        feed(bridge, service, [row("1.20.4"), row("1.19.2")], "")

        bridge.setFilter("1.20")

        assert bridge.filter == "1.20"
        assert [item["id"] for item in bridge.versions] == ["1.20.4"]
        assert bridge.count == 1
        assert bridge.totalCount == 2
        assert ("filtered", "1.20", "name") in service.calls

    def test_sort_key_is_passed_through(self) -> None:
        bridge, service, _game, _nav = make()
        feed(bridge, service, [row("b", vanilla="2"), row("a", vanilla="1")], "")

        bridge.setSortKey("vanilla")

        assert bridge.sortKey == "vanilla"
        assert [item["vanilla"] for item in bridge.versions] == ["1", "2"]
        assert ("filtered", "", "vanilla") in service.calls

    def test_clear_filter_shows_everything_again(self) -> None:
        bridge, service, _game, _nav = make()
        feed(bridge, service, [row("1.20.4"), row("1.19.2")], "")
        bridge.setFilter("1.20")
        bridge.clearFilter()
        assert bridge.filter == ""
        assert bridge.count == 2

    def test_refresh_forces_a_rescan(self) -> None:
        bridge, service, _game, _nav = make()
        bridge.refresh()
        assert ("load", True) in service.calls

    def test_filter_failure_falls_back_to_all_rows(self) -> None:
        bridge, service, _game, _nav = make()
        feed(bridge, service, [row("1.20.4")], "")

        def boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("过滤炸了")

        service.filtered = boom  # type: ignore[assignment]
        bridge.setFilter("x")
        assert bridge.count == 1, "过滤失败退化成「全显示」，不是空列表"


# ─── 选中与详情 ────────────────────────────────────────────────


class TestSelection:
    def test_select_publishes_detail_and_a_status(self) -> None:
        bridge, service, _game, _nav = make()
        feed(bridge, service, [row("1.20.4")], "")
        messages: List[Tuple[str, str]] = []
        bridge.statusMessage.connect(lambda text, level: messages.append((text, level)))

        bridge.select("1.20.4")

        assert bridge.currentId == "1.20.4"
        assert bridge.hasCurrent is True
        assert bridge.detail["modsCount"] == 7
        assert ("detail", "1.20.4") in service.calls
        assert messages == [("已选择：1.20.4", "info")]

    def test_select_without_a_version_clears(self) -> None:
        bridge, service, _game, _nav = make()
        feed(bridge, service, [row("1.20.4")], "")
        bridge.select("1.20.4")
        bridge.select("")
        assert bridge.hasCurrent is False
        assert bridge.detail == {}

    def test_detail_failure_does_not_break_selection(self) -> None:
        bridge, service, _game, _nav = make()

        def boom(_vid: str) -> Any:
            raise RuntimeError("详情炸了")

        service.detail = boom  # type: ignore[assignment]
        bridge.select("1.20.4")
        assert bridge.currentId == "1.20.4"
        assert bridge.detail["exists"] is False

    def test_selection_is_dropped_when_the_version_disappears(self) -> None:
        bridge, service, _game, _nav = make()
        feed(bridge, service, [row("1.20.4")], "")
        bridge.select("1.20.4")

        feed(bridge, service, [row("1.19.2")], "")

        assert bridge.hasCurrent is False
        assert bridge.detail == {}

    def test_clear_selection_is_idempotent(self) -> None:
        bridge, service, _game, _nav = make()
        signals: List[int] = []
        bridge.selectionChanged.connect(lambda: signals.append(1))
        bridge.clearSelection()
        assert signals == []


# ─── 行操作 ────────────────────────────────────────────────────


class TestRowActions:
    def test_rename_marks_busy_and_clears_on_result(self) -> None:
        bridge, service, _game, _nav = make()
        busy: List[bool] = []
        bridge.busyChanged.connect(lambda: busy.append(bridge.busy))

        bridge.renameVersion("1.20.4")
        assert ("rename", "1.20.4") in service.calls
        assert bridge.busy is True

        bridge.on_result("rename", True, {"id": "1.20.4", "newName": "新名字"})
        assert bridge.busy is False
        assert busy == [True, False]

    def test_rename_refreshes_the_list(self) -> None:
        bridge, service, _game, _nav = make()
        bridge.renameVersion("1.20.4")
        before = len([call for call in service.calls if call[0] == "load"])
        bridge.on_result("rename", True, {"id": "1.20.4", "newName": "新名字"})
        after = [call for call in service.calls if call[0] == "load"]
        assert len(after) == before + 1
        assert after[-1] == ("load", True), "重命名后要强制重扫（文件夹名变了）"

    def test_cancelled_operation_does_not_refresh(self) -> None:
        bridge, service, _game, _nav = make()
        bridge.renameVersion("1.20.4")
        before = len([call for call in service.calls if call[0] == "load"])
        bridge.on_result("rename", True, {"id": "1.20.4", "cancelled": True})
        assert len([call for call in service.calls if call[0] == "load"]) == before
        assert bridge.busy is False

    def test_failed_operation_clears_busy_without_refresh(self) -> None:
        bridge, service, _game, _nav = make()
        bridge.removeVersion("1.20.4")
        before = len([call for call in service.calls if call[0] == "load"])
        bridge.on_result("remove", False, {"id": "1.20.4"})
        assert bridge.busy is False
        assert len([call for call in service.calls if call[0] == "load"]) == before

    def test_renaming_the_selected_version_moves_the_selection(self) -> None:
        bridge, service, _game, _nav = make()
        feed(bridge, service, [row("旧名字")], "")
        bridge.select("旧名字")
        bridge.renameVersion("旧名字")
        bridge.on_result("rename", True, {"id": "旧名字", "newName": "新名字"})
        assert bridge.currentId == "新名字"

    def test_remove_goes_through_the_service(self) -> None:
        bridge, service, _game, _nav = make()
        bridge.removeVersion("1.20.4")
        assert ("remove", "1.20.4") in service.calls
        assert bridge.busy is True

    def test_verify_goes_through_the_service(self) -> None:
        bridge, service, _game, _nav = make()
        bridge.verify("1.20.4")
        assert ("verify", "1.20.4") in service.calls
        assert bridge.busy is True
        bridge.on_result("verify", True, {"id": "1.20.4", "total": 3})
        assert bridge.busy is False

    def test_open_folder_goes_through_the_service(self) -> None:
        bridge, service, _game, _nav = make()
        bridge.openFolder("1.20.4")
        assert ("open_folder", "1.20.4") in service.calls

    def test_actions_without_a_service_do_not_raise(self) -> None:
        bridge = VersionsBridge()
        bridge.bind(FakeContext({}))
        bridge.renameVersion("1.20.4")
        bridge.removeVersion("1.20.4")
        bridge.verify("1.20.4")
        bridge.openFolder("1.20.4")
        assert bridge.busy is False


class TestStartupDeferral:
    """启动链条跑完要重新扫一次。

    实测背景：桥在 `bind()`（装配期）就扫了一次，那时启动链条还没把 `launcher`
    注册进上下文 —— 服务会降级成"只列目录名"（`VersionService._scan_sync`），
    所以链条跑完必须再扫一次，把加载器/游戏版本这些元数据补齐。
    """

    def test_chain_finished_triggers_a_reload(self) -> None:
        bridge, service, _game, _nav = make()
        before = len(service.calls)
        bridge.on_chain_finished()
        assert len(service.calls) > before
        assert service.calls[-1] == ("load", False)

    def test_a_scan_error_is_still_an_error(self) -> None:
        bridge, service, _game, _nav = make()
        feed(bridge, service, [], "磁盘炸了")
        assert bridge.loadState == "error"
        assert bridge.loadError == "磁盘炸了"

    def test_the_failure_status_reaches_the_status_bar(self) -> None:
        bridge, _service, _game, _nav = make()
        messages: List[Tuple[str, str]] = []
        bridge.statusMessage.connect(lambda text, level: messages.append((text, level)))
        bridge.on_status("version_load_failed", "error", {"error": "磁盘炸了"})
        assert messages == [("加载失败：磁盘炸了", "error")]


# ─── 游戏状态与启动 ────────────────────────────────────────────


class TestGame:
    def test_can_launch_needs_a_selection(self) -> None:
        bridge, service, _game, _nav = make()
        feed(bridge, service, [row("1.20.4")], "")
        assert bridge.canLaunch is False
        bridge.select("1.20.4")
        assert bridge.canLaunch is True

    @pytest.mark.parametrize(
        "state,can_launch,can_kill",
        [
            (STATE_IDLE, True, False),
            (STATE_LAUNCHING, False, True),
            (STATE_WAITING, False, True),
            (STATE_RUNNING, True, True),
            (STATE_CRASHED, True, False),
        ],
    )
    def test_button_availability_per_state(self, state: str, can_launch: bool, can_kill: bool) -> None:
        bridge, service, _game, _nav = make()
        feed(bridge, service, [row("1.20.4")], "")
        bridge.select("1.20.4")
        bridge.on_state(state)
        assert bridge.canLaunch is can_launch, state
        assert bridge.canKill is can_kill, state
        assert bridge.gameRunning is (state in (STATE_LAUNCHING, STATE_WAITING, STATE_RUNNING))

    def test_launch_without_selection_reports_it(self) -> None:
        bridge, service, _game, _nav = make()
        messages: List[Tuple[str, str]] = []
        bridge.statusMessage.connect(lambda text, level: messages.append((text, level)))
        bridge.launch("")
        assert messages == [("请先选择一个版本", "error")]

    def test_launch_passes_the_version(self) -> None:
        bridge, service, game, _nav = make()
        bridge.launch("1.20.4")
        assert game.launched == ["1.20.4"]

    def test_launch_uses_the_selection_when_blank(self) -> None:
        bridge, service, game, _nav = make()
        feed(bridge, service, [row("1.20.4")], "")
        bridge.select("1.20.4")
        bridge.launch("")
        assert game.launched == ["1.20.4"]

    def test_kill_goes_through_the_game_service(self) -> None:
        bridge, service, game, _nav = make()
        bridge.killGame()
        assert game.kills == 1

    def test_launch_result_refreshes_availability(self) -> None:
        bridge, service, game, _nav = make()
        feed(bridge, service, [row("1.20.4")], "")
        bridge.select("1.20.4")
        game.state = STATE_WAITING
        bridge.on_launch_result("1.20.4", "1.20.4", True, "")
        assert bridge.gameState == STATE_WAITING
        assert bridge.canLaunch is False

    def test_killed_callback_refreshes_state(self) -> None:
        bridge, service, game, _nav = make()
        game.state = STATE_IDLE
        bridge.on_killed(1)
        assert bridge.gameState == STATE_IDLE


# ─── 状态文案 ──────────────────────────────────────────────────


class TestStatusText:
    CASES = [
        ("version_loading_installed", {}, "正在加载已安装版本..."),
        ("version_loaded", {"count": 3}, "已安装 3 个版本"),
        ("version_load_failed", {"error": "磁盘错误"}, "加载失败：磁盘错误"),
        ("version_renaming", {"old": "a", "new": "b"}, "正在重命名 a → b..."),
        ("version_removed", {"version": "1.20.4"}, "1.20.4 已删除"),
        ("version_remove_failed", {"version": "1.20.4"}, "1.20.4 删除失败"),
        ("version_remove_error", {"error": "权限"}, "删除错误：权限"),
        ("version_select_first", {}, "请先选择一个版本"),
        ("version_verifying", {"version": "1.20.4"}, "正在校验 1.20.4..."),
        ("version_verify_ok", {"total": 12}, "校验通过：12 个文件"),
        ("version_verify_bad", {"total": 9, "invalid": 2}, "校验完成：9 个文件中有 2 个损坏"),
        ("version_verify_empty", {}, "没有可校验的文件（缺少版本 JSON）"),
        ("version_verify_failed", {"error": "读取失败"}, "校验失败：读取失败"),
        ("version_detail_missing", {}, "该版本已不存在"),
        ("version_open_folder_failed", {"error": "无关联"}, "打开目录失败：无关联"),
        ("rename_instance_success", {"old": "a", "new": "b"}, "实例已重命名: a → b"),
        ("rename_instance_invalid", {}, "实例名称不能为空或包含非法字符"),
        ("rename_instance_exists", {"name": "b"}, "目标名称 'b' 已存在，请使用其他名称"),
        ("rename_instance_error", {"error": "原因"}, "重命名失败: 原因"),
        ("deleting_version", {"version": "1.20.4"}, "正在删除 1.20.4..."),
    ]

    @pytest.mark.parametrize("key,params,expected", CASES)
    def test_status_is_translated(self, key: str, params: Dict[str, Any], expected: str) -> None:
        bridge, service, _game, _nav = make()
        messages: List[Tuple[str, str]] = []
        bridge.statusMessage.connect(lambda text, level: messages.append((text, level)))

        bridge.on_status(key, "success", params)

        assert messages == [(expected, "success")]

    def test_unknown_key_falls_back_to_the_key(self) -> None:
        bridge, service, _game, _nav = make()
        messages: List[Tuple[str, str]] = []
        bridge.statusMessage.connect(lambda text, level: messages.append((text, level)))
        bridge.on_status("没登记过的键", "info", {})
        assert messages == [("没登记过的键", "info")]


# ─── 导航 ──────────────────────────────────────────────────────


class TestNavigation:
    def test_open_detail_pushes_the_route_with_the_version(self) -> None:
        bridge, service, _game, nav = make(with_nav=True)
        feed(bridge, service, [row("1.20.4")], "")
        bridge.openDetail("1.20.4")
        assert nav.pushes == [("versions/detail", {"version": "1.20.4"})]
        assert bridge.currentId == "1.20.4"

    def test_browse_mods_carries_version_and_loader(self) -> None:
        bridge, service, _game, nav = make(with_nav=True)
        feed(bridge, service, [row("1.20.4-forge", loader="forge")], "")
        bridge.browseMods("1.20.4-forge")
        assert nav.pushes == [("resources/browse", {"version": "1.20.4-forge", "loader": "forge"})]

    def test_resource_manager_route(self) -> None:
        bridge, service, _game, nav = make(with_nav=True)
        bridge.openResourceManager("1.20.4")
        assert nav.pushes == [("resources/mods", {"version": "1.20.4"})]

    def test_resource_manager_for_current_requires_a_selection(self) -> None:
        bridge, service, _game, nav = make(with_nav=True)
        messages: List[Tuple[str, str]] = []
        bridge.statusMessage.connect(lambda text, level: messages.append((text, level)))

        bridge.openResourceManagerForCurrent()
        assert messages == [("请先选择一个版本", "error")]
        assert nav.pushes == []

        feed(bridge, service, [row("1.20.4")], "")
        bridge.select("1.20.4")
        bridge.openResourceManagerForCurrent()
        assert nav.pushes == [("resources/mods", {"version": "1.20.4"})]

    def test_browse_all_resources(self) -> None:
        bridge, service, _game, nav = make(with_nav=True)
        bridge.browseAllResources()
        assert nav.pushes == [("resources/browse", {})]

    def test_go_back(self) -> None:
        bridge, service, _game, nav = make(with_nav=True)
        bridge.goBack()
        assert nav.backs == 1

    def test_navigation_without_nav_does_not_raise(self) -> None:
        bridge, service, _game, _nav = make()
        bridge.openDetail("1.20.4")
        bridge.goBack()
        assert bridge.currentId == "1.20.4", "选中不依赖 Nav"
