"""`app/bridges/install_bridge.py` 的回归守卫（阶段 3 任务 3.3）。

桥这一层只有四件事要做对，本文件就守这四件：

1. **接线**：`bind()` 取安装服务并把自己挂成观察者；`use_engine()` 把服务的安装工作者
   登记成 `Tasks` 的任务种类（第三个参数必须是 `pass_context=True` —— 少了它工作者就拿不到
   `TaskContext`，进度与取消整条链路都断）并接上三条任务信号；
2. **状态映射**：可用清单三态（含"有数据 + 有错误仍是 ready"）、分页夹取、兼容提示的键名
   映射，以及"答非当前表单的答案必须丢掉"；
3. **参数转换**：服务给的 i18n 键 + 参数 → 当前语言的一句话（键都以字面量写在
   `_status_text()` 里，所以逐个键断言"翻出来的是人话，不是键名"）；
4. **缺席不崩**：服务 / `Tasks` / `Nav` 取不到时属性返回空值、槽只记日志。

界面本身（真的显示出来了没有）由 `tests/test_install_page_qml.py` 的 QML 探针管，这里不重复。
本文件里的 `Tasks` 是**假**的（信号由测试手动投递），所以验的是"桥拿到这些载荷之后怎么算"；
"进度真的合并着回来了、取消真的传到了工作者的取消标志"那条在探针里用真 `TaskBridge` 验。
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

from app.bridges.install_bridge import (  # noqa: E402
    KIND_INSTALL,
    STATE_EMPTY,
    STATE_ERROR,
    STATE_LOADING,
    STATE_READY,
    InstallBridge,
)
from services.install_service import (  # noqa: E402
    COMPAT_BAD,
    COMPAT_CHECKING,
    COMPAT_IDLE,
    COMPAT_OK,
    COMPAT_UNKNOWN,
    LOADER_IDS,
    LOADER_LABEL_KEYS,
    LOADER_NONE,
    RESULT_CANCELLED,
    RESULT_DONE,
    RESULT_FAILED,
    TAB_RELEASE,
    TAB_SNAPSHOT,
    TABS,
    page_slice,
    split_available,
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
    """**整个模块**用中文语言文件：否则 `_()` 返回键名，"翻好了没有"就没法断言。

    `services/i18n_service` 的全局状态会在测试模块之间互相影响（D-166 的教训：
    真装配的模块级 fixture 不还原语言，后续模块的中文断言会随用户 `config.json` 变红），
    所以用完把语言与译文表都放回去。
    """
    from services import i18n_service

    saved_lang = i18n_service.get_current_language()
    saved = dict(i18n_service._translations)
    i18n_service.init_i18n("zh_CN")
    yield
    i18n_service._translations.clear()
    i18n_service._translations.update(saved)
    i18n_service._current_language = saved_lang


# ─── 替身 ──────────────────────────────────────────────────────


class FakeInstallService:
    """`InstallService` 的替身：只实现桥用到的那几个方法，并把调用记下来。

    分组（`split_available`）与分页（`page_slice`）用的是**服务里那两个真纯函数** ——
    假服务要是自己再写一套分页，验的就不是产品的那套语义了。
    """

    name = "install"

    def __init__(self, versions: Optional[List[Dict[str, Any]]] = None) -> None:
        self.available: List[Dict[str, Any]] = list(versions or [])
        self.observers: List[Any] = []
        self.calls: List[Tuple[Any, ...]] = []

    def set_observer(self, observer: Any) -> None:
        self.observers.append(observer)

    def load_available(self, *, force: bool = False) -> Any:
        self.calls.append(("load_available", bool(force)))
        return object()

    def page_of(self, tab: str, page: int) -> Dict[str, Any]:
        self.calls.append(("page_of", str(tab), int(page)))
        release, snapshot = split_available(self.available)
        key = tab if tab in TABS else TAB_RELEASE
        data = page_slice(release if key == TAB_RELEASE else snapshot, page)
        data.update({"tab": key, "releaseCount": len(release), "snapshotCount": len(snapshot)})
        return data

    def check_compat(self, version_id: str, loader: str) -> Any:
        self.calls.append(("check_compat", str(version_id), str(loader)))
        return object()

    def install_task(self, ctx: Any, *, version_id: str = "", loader: str = LOADER_NONE) -> Dict[str, Any]:
        """工作者本身由 `Tasks` 桥在工作线程上调；桥只负责把它的**身份**登记过去。"""
        self.calls.append(("install_task", str(version_id), str(loader)))
        return {"ok": True, "state": RESULT_DONE, "requested": version_id, "installed": version_id}


class FakeSignal:
    """够用的信号替身：`connect` 记槽，`emit` 同步投递给所有槽。"""

    def __init__(self) -> None:
        self.slots: List[Any] = []

    def connect(self, slot: Any) -> bool:
        self.slots.append(slot)
        return True

    def emit(self, *args: Any) -> None:
        for slot in list(self.slots):
            slot(*args)


class FakeTasks:
    """`TaskBridge` 的替身：登记 / 提交 / 取消都记下来，三条信号由测试手动投递。"""

    def __init__(self) -> None:
        self.kinds: List[Tuple[str, Any, bool]] = []
        self.submitted: List[Tuple[str, Dict[str, Any]]] = []
        self.cancelled: List[int] = []
        self.next_task_id = 1
        self.last_task_id = 0
        self.progress = FakeSignal()
        self.taskFinished = FakeSignal()
        self.taskFailed = FakeSignal()

    def register_kind(self, kind: str, fn: Any, pass_context: bool = False) -> None:
        self.kinds.append((kind, fn, bool(pass_context)))

    def submit(self, kind: str, params: Optional[Dict[str, Any]] = None) -> int:
        self.submitted.append((kind, dict(params or {})))
        self.last_task_id = self.next_task_id
        self.next_task_id += 1
        return self.last_task_id

    def cancel(self, task_id: int) -> bool:
        self.cancelled.append(int(task_id))
        return True


class FakeNav:
    def __init__(self) -> None:
        self.pushes: List[Tuple[str, Dict[str, Any]]] = []

    def push(self, route_id: str, params: Optional[Dict[str, Any]] = None) -> bool:
        self.pushes.append((route_id, dict(params or {})))
        return True


class FakeEngine:
    def __init__(self, registry: Optional[Dict[str, Any]] = None) -> None:
        self._fmcl_bridges = dict(registry or {})


class FakeContext:
    def __init__(self, services: Dict[str, Any]) -> None:
        self._services = services

    def try_get(self, name: str) -> Any:
        return self._services.get(name)


# ─── 造数据与装配 ──────────────────────────────────────────────


def release_ids(count: int) -> List[str]:
    """造 `count` 个互不相同的正式版号（新 → 旧，与服务"保序"的约定一致）。"""
    return ["1.%d.%d" % (20 - (index // 10), index % 10) for index in range(count)]


def snapshot_ids(count: int) -> List[str]:
    return ["24w%02da" % (index + 1) for index in range(count)]


def manifest(
    releases: int = 3, snapshots: int = 2, extra: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """核心 `get_available_versions()` 那种**原样清单**（`{"id", "type"}`，未分组）。"""
    rows = [{"id": item, "type": TAB_RELEASE} for item in release_ids(releases)]
    rows += [{"id": item, "type": TAB_SNAPSHOT} for item in snapshot_ids(snapshots)]
    return rows + list(extra or [])


def make(
    versions: Optional[List[Dict[str, Any]]] = None,
    with_service: bool = True,
    with_tasks: bool = True,
    with_nav: bool = False,
) -> Tuple[InstallBridge, FakeInstallService, FakeTasks, FakeNav]:
    """装配一座桥：`versions` 是核心原样清单（`None` = 3 个正式版 + 2 个测试版）。"""
    service = FakeInstallService(manifest() if versions is None else versions)
    tasks = FakeTasks()
    nav = FakeNav()
    services: Dict[str, Any] = {"install": service} if with_service else {}
    registry: Dict[str, Any] = {}
    if with_tasks:
        registry["Tasks"] = tasks
    if with_nav:
        registry["Nav"] = nav
    bridge = InstallBridge()
    bridge.bind(FakeContext(services))
    if registry:
        bridge.use_engine(FakeEngine(registry))
    return bridge, service, tasks, nav


def start_install(
    bridge: InstallBridge, tasks: FakeTasks, version_id: str = "1.20.4", loader: str = LOADER_NONE
) -> int:
    """填表 + 点安装：返回假 `Tasks` 给出的 taskId（后面投递信号要用它）。"""
    bridge.setVersionId(version_id)
    bridge.setLoader(loader)
    bridge.install()
    assert tasks.submitted, "安装没有提交任务"
    return tasks.last_task_id


def finish(tasks: FakeTasks, task_id: int, **fields: Any) -> None:
    """投递一条 `taskFinished`（`result` 就是服务工作者的返回值）。"""
    tasks.taskFinished.emit({"taskId": task_id, "name": KIND_INSTALL, "result": dict(fields)})


def feed(
    bridge: InstallBridge,
    service: FakeInstallService,
    versions: Optional[List[Dict[str, Any]]] = None,
    error: str = "",
) -> None:
    """模拟一次真实返回：假服务手里的清单与桥收到的通知**必须一致**。

    桥的状态判定读的是 `page_of()` 的计数（不是 `on_available` 的行参数），所以这里两件事
    一起做 —— 只喂一半的话，测的就是一个不会发生的场景。
    """
    if versions is not None:
        service.available = list(versions)
    release, snapshot = split_available(service.available)
    bridge.on_available(release + snapshot, error)


def calls_of(service: FakeInstallService, name: str) -> List[Tuple[Any, ...]]:
    """假服务里某一类调用的全部记录（断言时不用自己翻整个调用表）。"""
    return [call for call in service.calls if call[0] == name]


def count_emits(signal: Any) -> List[int]:
    """给无参信号装一个计数器（返回的列表非空即"发过"）。"""
    seen: List[int] = []
    signal.connect(lambda: seen.append(1))
    return seen


def collect_status(bridge: InstallBridge) -> List[Tuple[str, str]]:
    """收集 `statusMessage` 的 (文案, 级别)。"""
    seen: List[Tuple[str, str]] = []
    bridge.statusMessage.connect(lambda text, level: seen.append((text, level)))
    return seen


def collect_installed(bridge: InstallBridge) -> List[str]:
    """收集 `installed` 信号的版本 id（页面靠它回列表并选中新版本）。"""
    seen: List[str] = []
    bridge.installed.connect(lambda version_id: seen.append(version_id))
    return seen


# ─── 装配与接线 ────────────────────────────────────────────────


class TestWiring:
    def test_bind_takes_the_service_and_registers_as_the_observer(self) -> None:
        bridge, service, _tasks, _nav = make()
        assert service.observers == [bridge], "服务只认一个观察者，必须正好是这座桥"

    def test_observed_rows_reach_the_page(self) -> None:
        """观察者挂对了没有，用一次真实回报验：服务的回报必须真的改到桥的属性。"""
        bridge, service, _tasks, _nav = make(versions=[])
        feed(bridge, service, manifest(releases=1, snapshots=0))
        assert bridge.availableState == STATE_READY
        assert [row["id"] for row in bridge.versions] == release_ids(1)

    def test_load_and_refresh_differ_by_the_force_flag(self) -> None:
        """`loadAvailable` 吃服务的复用窗口，`refreshAvailable` 无条件重新联网。"""
        bridge, service, _tasks, _nav = make()

        bridge.loadAvailable()
        bridge.refreshAvailable()

        assert ("load_available", False) in service.calls
        assert ("load_available", True) in service.calls

    def test_load_failure_falls_into_the_error_state(self) -> None:
        bridge, service, _tasks, _nav = make()

        def boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("网络炸了")

        service.load_available = boom  # type: ignore[assignment]
        bridge.loadAvailable()

        assert bridge.availableState == STATE_ERROR
        assert bridge.availableError == "网络炸了"

    def test_bind_without_a_context_does_not_raise(self) -> None:
        bridge = InstallBridge()
        bridge.bind(None)
        bridge.refreshAvailable()  # 不抛异常
        assert bridge.availableState == STATE_ERROR

    def test_missing_service_is_visible_as_an_error_state(self) -> None:
        """服务缺席时给一块可见的错误态，而不是让页面永远转圈（手动输入那条路还在）。"""
        bridge, _service, _tasks, _nav = make(with_service=False)
        assert bridge.availableState == STATE_LOADING, "还没取数之前是加载中"

        bridge.loadAvailable()

        assert bridge.availableState == STATE_ERROR
        assert bridge.availableError == "出错"
        assert bridge.versions == []

    def test_use_engine_registers_the_service_worker(self) -> None:
        bridge, service, tasks, _nav = make()
        assert tasks.kinds == [(KIND_INSTALL, service.install_task, True)], (
            "必须是「版本.安装 → 服务的 install_task」且 pass_context=True："
            "少了第三个参数，工作者拿不到 TaskContext，进度与取消全断"
        )

    def test_the_worker_is_not_registered_without_the_service(self) -> None:
        """服务缺席时登记不上（没有可调用的对象），但信号还是要接上。"""
        _bridge, _service, tasks, _nav = make(with_service=False)
        assert tasks.kinds == []
        assert len(tasks.progress.slots) == 1

    def test_use_engine_connects_the_three_task_signals(self) -> None:
        _bridge, _service, tasks, _nav = make()
        assert len(tasks.progress.slots) == 1
        assert len(tasks.taskFinished.slots) == 1
        assert len(tasks.taskFailed.slots) == 1

    def test_a_missing_task_signal_does_not_break_the_wiring(self) -> None:
        """`Tasks` 少了哪条信号就少哪条功能，登记与其余信号照常。"""
        tasks = FakeTasks()
        del tasks.taskFailed
        bridge, service, _tasks, _nav = make(with_tasks=False)

        bridge.use_engine(FakeEngine({"Tasks": tasks}))

        assert tasks.kinds == [(KIND_INSTALL, service.install_task, True)]
        assert len(tasks.progress.slots) == 1

    def test_a_failed_kind_registration_still_connects_the_signals(self) -> None:
        """登记失败只影响"装不了"，不该让整页装配失败。"""
        tasks = FakeTasks()

        def boom(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("种类表炸了")

        tasks.register_kind = boom  # type: ignore[assignment]
        bridge, _service, _tasks, _nav = make(with_tasks=False)

        bridge.use_engine(FakeEngine({"Tasks": tasks}))

        assert len(tasks.progress.slots) == 1

    def test_use_engine_without_nav_or_tasks_does_not_raise(self) -> None:
        bridge, _service, _tasks, _nav = make(with_tasks=False)

        bridge.use_engine(FakeEngine({}))

        bridge.installModpack()  # Nav 缺席时只记 debug


# ─── 可用版本三态 ──────────────────────────────────────────────


class TestAvailableStates:
    def test_no_rows_is_empty_not_error(self) -> None:
        bridge, service, _tasks, _nav = make(versions=[])
        states: List[str] = []
        bridge.availableChanged.connect(lambda: states.append(bridge.availableState))

        feed(bridge, service, [])

        assert bridge.availableState == STATE_EMPTY
        assert bridge.availableError == ""
        assert bridge.versions == []
        assert states, "状态变了要发 availableChanged"

    def test_error_carries_the_reason(self) -> None:
        bridge, service, _tasks, _nav = make(versions=[])

        feed(bridge, service, [], error="清单炸了")

        assert bridge.availableState == STATE_ERROR
        assert bridge.availableError == "清单炸了"

    def test_rows_become_ready(self) -> None:
        bridge, service, _tasks, _nav = make(versions=[])

        feed(bridge, service, manifest(releases=2, snapshots=1))

        assert bridge.availableState == STATE_READY
        assert bridge.total == 2, "默认在正式版标签页，总数就是这个标签的条数"
        assert bridge.releaseCount == 2
        assert bridge.snapshotCount == 1

    def test_data_wins_over_a_network_error(self) -> None:
        """有数据但同时有 error 时仍是 ready：一次网络抖动不该把用户面前的清单抹掉。

        服务的 `_load_done` 也是这个约定（失败时**不清空**手里已有的清单），
        桥这边对应的是"计数非零就 ready"。
        """
        bridge, service, _tasks, _nav = make(versions=manifest(releases=2, snapshots=0))

        feed(bridge, service, error="timeout")

        assert bridge.availableState == STATE_READY
        assert bridge.availableError == "timeout", "错误原因仍要留着（页面可以小声提示）"
        assert len(bridge.versions) == 2, "旧清单不许被一次抖动清空"


# ─── 分页与标签页（B-13）──────────────────────────────────────


class TestPaging:
    def test_page_size_is_twenty_like_the_old_ui(self) -> None:
        bridge, service, _tasks, _nav = make(versions=manifest(releases=45, snapshots=3))

        feed(bridge, service)

        assert bridge.pageCount == 3, "45 个正式版 ÷ 每页 20 应当分 3 页"
        assert bridge.total == 45
        assert [row["id"] for row in bridge.versions] == release_ids(20)

    def test_next_and_prev_go_through_the_service(self) -> None:
        bridge, service, _tasks, _nav = make(versions=manifest(releases=45, snapshots=0))
        feed(bridge, service)

        bridge.nextPage()
        assert bridge.page == 2
        assert calls_of(service, "page_of")[-1] == ("page_of", TAB_RELEASE, 2)

        bridge.prevPage()
        assert bridge.page == 1
        assert calls_of(service, "page_of")[-1] == ("page_of", TAB_RELEASE, 1)

    def test_prev_on_the_first_page_stays_put(self) -> None:
        """页码的夹取是**服务的**职责（`page_slice`）：桥只管把 0 交出去。"""
        bridge, service, _tasks, _nav = make(versions=manifest(releases=45, snapshots=0))
        feed(bridge, service)

        bridge.prevPage()

        assert bridge.page == 1
        assert calls_of(service, "page_of")[-1] == ("page_of", TAB_RELEASE, 0)

    def test_out_of_range_pages_are_clamped_by_the_service(self) -> None:
        bridge, service, _tasks, _nav = make(versions=manifest(releases=45, snapshots=0))
        feed(bridge, service)

        bridge.setPage(99)

        assert bridge.page == 3, "页码越界由服务的 page_slice 夹到最后一页"
        assert [row["id"] for row in bridge.versions] == release_ids(45)[40:]

    def test_page_zero_lands_on_the_first_page(self) -> None:
        bridge, service, _tasks, _nav = make(versions=manifest(releases=45, snapshots=0))
        feed(bridge, service)

        bridge.setPage(0)

        assert bridge.page == 1

    def test_a_non_numeric_page_falls_back_to_the_first(self) -> None:
        """QML 侧理论上给不出非数字，但槽是 Python 直调的，这里不许炸。"""
        bridge, service, _tasks, _nav = make()
        feed(bridge, service)

        bridge.setPage("不是数字")  # type: ignore[arg-type]

        assert bridge.page == 1

    def test_switching_tab_resets_the_page(self) -> None:
        """两个标签页都够 3 页，这样"重置回 1"才是真的重置（否则是分页夹取顺手救的）。"""
        bridge, service, _tasks, _nav = make(versions=manifest(releases=45, snapshots=45))
        feed(bridge, service)
        bridge.setPage(3)
        assert bridge.page == 3

        bridge.setTab(TAB_SNAPSHOT)

        assert bridge.tab == TAB_SNAPSHOT
        assert bridge.page == 1, "切标签页把页码重置回 1（旧界面语义）"
        assert bridge.total == 45
        assert [row["id"] for row in bridge.versions] == snapshot_ids(20)
        assert all(row["snapshot"] is True for row in bridge.versions)

    def test_an_unknown_tab_falls_back_to_release(self) -> None:
        bridge, service, _tasks, _nav = make()
        feed(bridge, service)

        bridge.setTab("不知道")

        assert bridge.tab == TAB_RELEASE

    def test_paging_failure_shows_an_empty_page(self) -> None:
        """分页算不出来时给空页，不是把整页钉在错误态（手动输入版本 ID 那条路还在）。"""
        bridge, service, _tasks, _nav = make()
        feed(bridge, service)

        def boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("分页炸了")

        service.page_of = boom  # type: ignore[assignment]
        bridge.setPage(2)

        assert bridge.versions == []
        assert bridge.page == 1
        assert bridge.pageCount == 1


# ─── 表单（B-09 / B-10）───────────────────────────────────────


class TestForm:
    def test_setting_the_same_version_id_does_not_emit(self) -> None:
        """输入框每敲一下都会回调，同值再发信号就是白刷一遍 QML。"""
        bridge, _service, _tasks, _nav = make()
        changes = count_emits(bridge.formChanged)

        bridge.setVersionId("")
        bridge.setVersionId("1.20.4")

        assert len(changes) == 1
        assert bridge.versionId == "1.20.4"

    def test_quick_select_truncates_the_row_label(self) -> None:
        """列表行是「1.20.4 (release)」这种带后缀的标签，回填要截到第一个空白处。"""
        bridge, _service, _tasks, _nav = make()
        changes = count_emits(bridge.formChanged)

        bridge.quickSelect("1.20.4 (release)")

        assert bridge.versionId == "1.20.4"
        assert changes, "回填也是一次表单变化（输入框要跟着变）"

    def test_loader_only_accepts_the_nine_known_ids(self) -> None:
        bridge, _service, _tasks, _nav = make()

        for loader_id in LOADER_IDS:
            bridge.setLoader(loader_id)
            assert bridge.loader == loader_id, loader_id

        bridge.setLoader("optifabric")  # 没登记过的加载器

        assert bridge.loader == LOADER_NONE, "非法值必须回落 none，不许原样透给核心"

    def test_loader_name_is_the_display_name(self) -> None:
        bridge, _service, _tasks, _nav = make()
        assert bridge.loaderName == "无", "不装加载器时传给核心的是「无」（旧界面同一个字面量）"

        bridge.setLoader("forge")

        assert bridge.loaderName == "Forge"

    def test_setting_the_same_loader_does_not_emit(self) -> None:
        bridge, _service, _tasks, _nav = make()
        bridge.setLoader("forge")
        changes = count_emits(bridge.formChanged)

        bridge.setLoader("forge")

        assert changes == []

    def test_loader_options_are_the_nine_with_label_keys(self) -> None:
        bridge, _service, _tasks, _nav = make()

        options = bridge.loaderOptions

        assert [item["value"] for item in options] == list(LOADER_IDS)
        assert [item["labelKey"] for item in options] == [LOADER_LABEL_KEYS[item] for item in LOADER_IDS]
        assert all(str(item["labelKey"]).startswith("mod_loader_") for item in options), (
            "标签是 i18n 键（页面自己翻），这样切语言时下拉会跟着走"
        )

    def test_changing_the_form_clears_the_old_compat_hint(self) -> None:
        """表单改了，上一条兼容提示就不再属于当前选择 —— 必须当场作废。"""
        bridge, _service, _tasks, _nav = make()
        bridge.setVersionId("1.20.4")
        bridge.setLoader("forge")
        bridge.on_compat("forge", "1.20.4", COMPAT_BAD, "remote", False)
        assert bridge.compatKey == "version_compat_bad"

        bridge.setLoader("fabric")

        assert bridge.compatState == COMPAT_IDLE
        assert bridge.compatKey == ""
        assert bridge.compatSource == ""

    def test_reset_clears_the_form_and_the_result(self) -> None:
        bridge, _service, tasks, _nav = make()
        task_id = start_install(bridge, tasks, version_id="1.20.4", loader="forge")
        finish(tasks, task_id, ok=True, state=RESULT_DONE, installed="1.20.4-forge")
        changes = count_emits(bridge.formChanged)

        bridge.reset()

        assert bridge.versionId == ""
        assert bridge.loader == LOADER_NONE
        assert bridge.compatKey == ""
        assert bridge.resultState == ""
        assert bridge.resultKey == ""
        assert changes, "清空也是一次表单变化（输入框要跟着空）"
        assert bridge.busy is False


# ─── 兼容提示（B-10 的后半）───────────────────────────────────


class TestCompat:
    CASES = [
        (COMPAT_CHECKING, "version_compat_checking"),
        (COMPAT_OK, "version_compat_ok"),
        (COMPAT_BAD, "version_compat_bad"),
        (COMPAT_UNKNOWN, "version_compat_unknown"),
    ]

    @pytest.mark.parametrize("state,key", CASES)
    def test_the_state_maps_to_an_i18n_key(self, state: str, key: str) -> None:
        bridge, _service, _tasks, _nav = make()
        bridge.setVersionId("1.20.4")
        bridge.setLoader("forge")
        changes = count_emits(bridge.formChanged)

        bridge.on_compat("forge", "1.20.4", state, "remote", None)

        assert bridge.compatState == state
        assert bridge.compatKey == key
        assert bridge.compatSource == "remote", "出处（local:xxx / remote）要留着，验收时靠它分辨谁说的"
        assert changes, "提示变了要发 formChanged（页面绑它）"

    def test_an_unlisted_state_leaves_the_key_empty(self) -> None:
        """没登记过的 state 给空键：页面据此不显示提示，而不是显示一个键名。"""
        bridge, _service, _tasks, _nav = make()
        bridge.setVersionId("1.20.4")
        bridge.setLoader("forge")

        bridge.on_compat("forge", "1.20.4", "没见过的状态", "remote", None)

        assert bridge.compatKey == ""

    def test_answers_for_a_previous_version_are_dropped(self) -> None:
        """迟到的旧答案不许覆盖新表单的结论（用户改完版本号，网络上还飘着旧查询）。"""
        bridge, _service, _tasks, _nav = make()
        bridge.setVersionId("1.20.4")
        bridge.setLoader("forge")
        bridge.on_compat("forge", "1.20.4", COMPAT_OK, "remote", True)

        bridge.setVersionId("1.19.2")
        bridge.on_compat("forge", "1.19.2", COMPAT_BAD, "remote", False)
        assert bridge.compatKey == "version_compat_bad"

        bridge.on_compat("forge", "1.20.4", COMPAT_OK, "remote", True)

        assert bridge.compatState == COMPAT_BAD, "答非当前表单的答案必须丢掉"
        assert bridge.compatKey == "version_compat_bad"

    def test_answers_for_a_previous_loader_are_dropped(self) -> None:
        bridge, _service, _tasks, _nav = make()
        bridge.setVersionId("1.20.4")
        bridge.setLoader("fabric")
        bridge.on_compat("fabric", "1.20.4", COMPAT_OK, "remote", True)

        bridge.setLoader("forge")
        bridge.on_compat("forge", "1.20.4", COMPAT_UNKNOWN, "remote", None)
        assert bridge.compatKey == "version_compat_unknown"

        bridge.on_compat("fabric", "1.20.4", COMPAT_OK, "remote", True)

        assert bridge.compatKey == "version_compat_unknown", "换了加载器，上一个加载器的答案一样要丢"

    def test_answers_match_the_truncated_version_id(self) -> None:
        """服务回的 version 是截断过的（`clean_version_id`），表单里那串带后缀也要认得。"""
        bridge, _service, _tasks, _nav = make()
        bridge.quickSelect("1.20.4 (release)")
        bridge.setLoader("forge")

        bridge.on_compat("forge", "1.20.4", COMPAT_OK, "remote", True)

        assert bridge.compatKey == "version_compat_ok"

    def test_check_compatibility_goes_through_the_service(self) -> None:
        bridge, service, _tasks, _nav = make()
        bridge.setVersionId("1.20.4")
        bridge.setLoader("forge")

        bridge.checkCompatibility()

        assert ("check_compat", "1.20.4", "forge") in service.calls

    def test_check_compatibility_without_a_service_does_not_raise(self) -> None:
        bridge, _service, _tasks, _nav = make(with_service=False)
        bridge.setVersionId("1.20.4")

        bridge.checkCompatibility()  # 不抛异常

        assert bridge.compatState == COMPAT_IDLE

    def test_a_failed_check_does_not_break_the_form(self) -> None:
        bridge, service, _tasks, _nav = make()

        def boom(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("查询炸了")

        service.check_compat = boom  # type: ignore[assignment]
        bridge.setVersionId("1.20.4")

        bridge.checkCompatibility()

        assert bridge.versionId == "1.20.4"
        assert bridge.compatKey == ""


# ─── 安装：提交 / 进度 / 取消 / 结果（B-11）───────────────────


class TestInstallFlow:
    def test_an_empty_version_id_is_refused_without_a_task(self) -> None:
        bridge, _service, tasks, _nav = make()
        messages = collect_status(bridge)

        bridge.install()

        assert tasks.submitted == [], "版本号都没填，不该白起一个任务"
        assert messages == [("请输入版本 ID", "error")]
        assert bridge.busy is False

    def test_the_submitted_params_are_cleaned_and_carry_the_loader(self) -> None:
        bridge, _service, tasks, _nav = make()
        bridge.setVersionId("1.20.4 forge")
        bridge.setLoader("forge")

        bridge.install()

        assert tasks.submitted == [(KIND_INSTALL, {"version_id": "1.20.4", "loader": "forge"})]

    def test_install_marks_busy_and_notifies(self) -> None:
        bridge, _service, tasks, _nav = make()
        changes = count_emits(bridge.progressChanged)

        start_install(bridge, tasks, version_id="1.20.4", loader="forge")

        assert bridge.busy is True
        assert bridge.canCancel is True, "任务在手才谈得上取消（旧界面没有这个能力）"
        assert changes, "忙碌状态变了要发 progressChanged"

    def test_a_second_install_while_busy_is_ignored(self) -> None:
        bridge, _service, tasks, _nav = make()
        start_install(bridge, tasks)

        bridge.install()

        assert len(tasks.submitted) == 1, "安装中再点一次不许叠第二个任务"

    def test_install_without_tasks_reports_an_error_instead_of_crashing(self) -> None:
        bridge, _service, _tasks, _nav = make(with_tasks=False)
        messages = collect_status(bridge)
        bridge.setVersionId("1.20.4")

        bridge.install()

        assert messages == [("出错", "error")]
        assert bridge.busy is False
        assert bridge.canCancel is False

    def test_a_rejected_submission_reports_an_error(self) -> None:
        """`Tasks.submit` 交不出 taskId（种类没登记 / 线程池拒绝）时不许显示成"正在安装"。"""
        bridge, _service, tasks, _nav = make()
        tasks.next_task_id = 0
        messages = collect_status(bridge)
        bridge.setVersionId("1.20.4")

        bridge.install()

        assert messages == [("出错", "error")]
        assert bridge.busy is False

    def test_a_raising_submission_is_survivable(self) -> None:
        bridge, _service, tasks, _nav = make()

        def boom(*_args: Any, **_kwargs: Any) -> int:
            raise RuntimeError("线程池炸了")

        tasks.submit = boom  # type: ignore[assignment]
        messages = collect_status(bridge)
        bridge.setVersionId("1.20.4")

        bridge.install()

        assert messages == [("出错", "error")]
        assert bridge.busy is False

    def test_progress_of_my_task_lands_in_the_properties(self) -> None:
        bridge, _service, tasks, _nav = make()
        task_id = start_install(bridge, tasks)
        assert bridge.progressDeterminate is False, "还没报过总数时是不确定进度"
        changes = count_emits(bridge.progressChanged)

        tasks.progress.emit({"taskId": task_id, "current": 3, "total": 10, "fraction": 0.3})

        assert bridge.progressCurrent == 3
        assert bridge.progressTotal == 10
        assert bridge.progress == pytest.approx(0.3)
        assert bridge.progressDeterminate is True
        assert changes, "进度变了要发 progressChanged"

    def test_progress_of_another_task_is_ignored(self) -> None:
        """`Tasks` 是全进程共用的：别的任务（扫描、校验）的进度绝不许串到安装页。"""
        bridge, _service, tasks, _nav = make()
        my_id = start_install(bridge, tasks)

        tasks.progress.emit({"taskId": my_id + 1, "current": 7, "total": 10, "fraction": 0.7})

        assert bridge.progressCurrent == 0
        assert bridge.progressTotal == 0
        assert bridge.progress == 0.0

    def test_progress_before_an_install_is_ignored(self) -> None:
        bridge, _service, tasks, _nav = make()

        tasks.progress.emit({"taskId": 1, "current": 5, "total": 10, "fraction": 0.5})

        assert bridge.progressCurrent == 0, "没有在跑的任务时，谁的进度都不收"

    def test_a_garbled_fraction_does_not_break_progress(self) -> None:
        bridge, _service, tasks, _nav = make()
        task_id = start_install(bridge, tasks)

        tasks.progress.emit({"taskId": task_id, "current": 1, "total": 2, "fraction": "一半"})

        assert bridge.progress == 0.0
        assert bridge.progressCurrent == 1, "小数坏掉不影响已完成的条数"

    def test_cancel_goes_through_tasks(self) -> None:
        bridge, _service, tasks, _nav = make()
        task_id = start_install(bridge, tasks)

        bridge.cancel()

        assert tasks.cancelled == [task_id]

    def test_cancel_is_a_no_op_when_nothing_is_running(self) -> None:
        bridge, _service, tasks, _nav = make()

        bridge.cancel()

        assert tasks.cancelled == []

    def test_cancel_is_no_longer_possible_after_the_end(self) -> None:
        bridge, _service, tasks, _nav = make()
        task_id = start_install(bridge, tasks)
        assert bridge.canCancel is True

        finish(tasks, task_id, ok=True, state=RESULT_DONE, installed="1.20.4")

        assert bridge.canCancel is False
        assert bridge.busy is False
        bridge.cancel()
        assert tasks.cancelled == [], "已经结束的任务没什么可取消的"

    def test_success_publishes_the_result_and_the_installed_signal(self) -> None:
        bridge, _service, tasks, _nav = make()
        installed = collect_installed(bridge)
        task_id = start_install(bridge, tasks, version_id="1.20.4", loader="forge")

        finish(
            tasks,
            task_id,
            ok=True,
            state=RESULT_DONE,
            requested="1.20.4",
            installed="1.20.4-forge-49.0.26",
            loader="forge",
        )

        assert bridge.busy is False
        assert bridge.resultState == RESULT_DONE
        assert bridge.resultKey == "version_installed"
        assert bridge.resultVersion == "1.20.4-forge-49.0.26"
        assert bridge.resultInstalled == "1.20.4-forge-49.0.26"
        assert bridge.resultRemaining == 0
        assert bridge.progress == 1.0, "成功了进度条要满格，不许停在最后一次上报"
        assert installed == ["1.20.4-forge-49.0.26"], "页面靠这条信号回列表并选中新版本"

    def test_success_without_an_installed_id_does_not_signal(self) -> None:
        """服务只回了 requested 时不许发空的 `installed`：页面会跳到一个不存在的版本。"""
        bridge, _service, tasks, _nav = make()
        installed = collect_installed(bridge)
        task_id = start_install(bridge, tasks)

        finish(tasks, task_id, ok=True, state=RESULT_DONE, requested="1.20.4")

        assert bridge.resultVersion == "1.20.4"
        assert bridge.resultInstalled == ""
        assert installed == []

    def test_cancelled_is_not_reported_as_failed(self) -> None:
        """用户点了取消不该看到"安装失败" —— 那不是错误，是他的决定。"""
        bridge, _service, tasks, _nav = make()
        installed = collect_installed(bridge)
        task_id = start_install(bridge, tasks)

        finish(tasks, task_id, ok=False, state=RESULT_CANCELLED, requested="1.20.4")

        assert bridge.resultState == RESULT_CANCELLED
        assert bridge.resultKey == "version_install_cancelled"
        assert installed == []
        assert bridge.busy is False

    def test_a_failed_result_is_reported_as_failed(self) -> None:
        bridge, _service, tasks, _nav = make()
        task_id = start_install(bridge, tasks)

        finish(tasks, task_id, ok=False, state=RESULT_FAILED, requested="1.20.4", error="磁盘满了")

        assert bridge.resultState == RESULT_FAILED
        assert bridge.resultKey == "version_install_failed"
        assert bridge.resultVersion == "1.20.4"

    def test_a_result_without_keys_counts_as_failed(self) -> None:
        """结束事件没带结果字典时按失败收尾：宁可报失败，也不能让界面永远"正在安装"。"""
        bridge, _service, tasks, _nav = make()
        task_id = start_install(bridge, tasks)

        tasks.taskFinished.emit({"taskId": task_id, "name": KIND_INSTALL, "result": None})

        assert bridge.busy is False
        assert bridge.resultState == RESULT_FAILED
        assert bridge.resultKey == "version_install_failed"

    def test_a_skipped_task_is_cancelled_not_failed(self) -> None:
        """「还没开始就被取消」的任务：载荷是 `result=None` + `cancelled=True`，必须认成已取消。

        `TaskRunner` 对排上队又被取消的任务是**直接跳过**的（`app/tasks.py::_execute` 开头
        那条 `if ctx.cancelled: handle._finish(None, None, skipped=True)`），所以此时
        **没有结果字典**，而且 `Tasks` 桥照样把 `ok` 标成 True（它只按"有没有异常"分叉）。
        用户点完安装立刻点取消就走这条路 —— 报成"安装失败"是在骂用户，不是报错。
        """
        bridge, _service, tasks, _nav = make()
        installed = collect_installed(bridge)
        task_id = start_install(bridge, tasks)

        tasks.taskFinished.emit(
            {"taskId": task_id, "name": KIND_INSTALL, "result": None, "cancelled": True, "ok": True}
        )

        assert bridge.resultState == RESULT_CANCELLED
        assert bridge.resultKey == "version_install_cancelled"
        assert bridge.busy is False
        assert bridge.canCancel is False
        assert bridge.resultVersion == "1.20.4", "取消也要留下「装的是哪个版本」"
        assert installed == [], "什么都没装成，不许发 installed（页面会跳到不存在的版本）"

    def test_a_cancelled_flag_inside_the_result_is_honoured(self) -> None:
        """结果字典自己带 `cancelled` 时同样不许报失败（服务侧还可能这样回）。"""
        bridge, _service, tasks, _nav = make()
        installed = collect_installed(bridge)
        task_id = start_install(bridge, tasks)

        finish(tasks, task_id, cancelled=True)

        assert bridge.resultState == RESULT_CANCELLED
        assert bridge.resultKey == "version_install_cancelled"
        assert installed == []

    def test_a_finish_of_another_task_is_ignored(self) -> None:
        bridge, _service, tasks, _nav = make()
        task_id = start_install(bridge, tasks)

        tasks.taskFinished.emit({"taskId": task_id + 1, "name": KIND_INSTALL, "result": {"ok": True}})

        assert bridge.busy is True, "别的任务结束不许把安装页的忙碌态清掉"
        assert bridge.resultState == ""

    def test_a_task_exception_falls_back_to_failed(self) -> None:
        """任务抛异常（服务理论上会把异常转成结果字典，这里是兜底）时也要收尾。"""
        bridge, _service, tasks, _nav = make()
        task_id = start_install(bridge, tasks)
        assert bridge.canCancel is True

        tasks.taskFailed.emit({"taskId": task_id, "name": KIND_INSTALL, "error": "炸了", "ok": False})

        assert bridge.busy is False
        assert bridge.resultState == RESULT_FAILED
        assert bridge.resultKey == "version_install_failed"
        assert bridge.canCancel is False
        assert bridge.resultVersion == "1.20.4", "失败时至少留下「装的是哪个版本」"

    def test_a_failed_task_of_another_id_is_ignored(self) -> None:
        bridge, _service, tasks, _nav = make()
        task_id = start_install(bridge, tasks)

        tasks.taskFailed.emit({"taskId": task_id + 1})

        assert bridge.busy is True

    def test_a_new_install_clears_the_previous_result(self) -> None:
        bridge, _service, tasks, _nav = make()
        task_id = start_install(bridge, tasks)
        finish(tasks, task_id, ok=False, state=RESULT_FAILED, requested="1.20.4")
        assert bridge.resultState == RESULT_FAILED

        start_install(bridge, tasks)

        assert bridge.resultState == "", "上一次的结果不许挂在这一次的进度条上"
        assert bridge.resultKey == ""
        assert bridge.resultVersion == ""

    def test_progress_is_reset_for_each_install(self) -> None:
        bridge, _service, tasks, _nav = make()
        task_id = start_install(bridge, tasks)
        tasks.progress.emit({"taskId": task_id, "current": 3, "total": 10, "fraction": 0.3})
        finish(tasks, task_id, ok=True, state=RESULT_DONE, installed="1.20.4")
        assert bridge.progress == 1.0

        new_id = start_install(bridge, tasks)

        assert new_id != task_id
        assert bridge.progressCurrent == 0
        assert bridge.progressTotal == 0
        assert bridge.progress == 0.0


# ─── 整合包入口（B-12）────────────────────────────────────────


class TestModpackEntry:
    def test_the_entry_pushes_the_route(self) -> None:
        """B-12 只要入口：整合包页面归 3.8，这里先把路由推出去。"""
        bridge, _service, _tasks, nav = make(with_nav=True)

        bridge.installModpack()

        assert nav.pushes == [("versions/modpack", {})]

    def test_without_nav_the_entry_only_logs(self) -> None:
        bridge, _service, _tasks, _nav = make(with_nav=False)

        bridge.installModpack()  # 不抛异常

    def test_a_raising_nav_does_not_break_the_page(self) -> None:
        bridge, _service, _tasks, nav = make(with_nav=True)

        def boom(*_args: Any, **_kwargs: Any) -> bool:
            raise RuntimeError("路由炸了")

        nav.push = boom  # type: ignore[assignment]

        bridge.installModpack()  # 不抛异常


# ─── 状态文案 ──────────────────────────────────────────────────


class TestStatusText:
    CASES = [
        ("version_id_required", {}, "请输入版本 ID"),
        ("version_installing", {"version": "1.20.4"}, "正在安装 1.20.4..."),
        ("version_installing_loader", {"version": "1.20.4", "loader": "Forge"}, "正在安装 1.20.4 + Forge..."),
        ("version_installed", {"version": "1.20.4"}, "1.20.4 安装成功!"),
        ("version_install_failed", {"version": "1.20.4"}, "1.20.4 安装失败"),
        ("version_install_cancelled", {"version": "1.20.4"}, "已取消安装 1.20.4"),
        ("version_available_failed", {}, "可用版本列表获取失败（离线模式）"),
        ("version_available_loaded", {"release": 45, "snapshot": 3}, "可用版本：正式版 45 个 / 测试版 3 个"),
        ("loading_available", {}, "正在获取可用版本..."),
    ]

    @pytest.mark.parametrize("key,params,expected", CASES)
    def test_status_is_translated(self, key: str, params: Dict[str, Any], expected: str) -> None:
        bridge, _service, _tasks, _nav = make()
        messages = collect_status(bridge)

        bridge.on_status(key, "success", params)

        assert messages == [(expected, "success")], "状态条要的是人话，不是 i18n 键"

    def test_missing_params_do_not_raise(self) -> None:
        """服务理论上总会带参数；少参数时也不能让 `format` 的异常漏到观察者框架外面。"""
        bridge, _service, _tasks, _nav = make()
        messages = collect_status(bridge)

        bridge.on_status("version_installing", "loading", {})

        assert messages == [("正在安装 ...", "loading")]

    def test_an_unknown_key_falls_back_to_the_key_itself(self) -> None:
        """未知键回落成**键名本身**（与 `VersionBridge._status_text` 同一条约定）。

        判据为什么是键名而不是空串：状态栏上出现 `version_install_xxx` 一眼就知道
        "哪条文案漏了"，而"闪一下什么都不显示"是查不出来的。原实现回的是空串，
        这条用例随实现一起统一（子代理报告的可疑点 1）。
        """
        bridge, _service, _tasks, _nav = make()
        messages = collect_status(bridge)

        bridge.on_status("没登记过的键", "info", {})

        assert messages == [("没登记过的键", "info")]
