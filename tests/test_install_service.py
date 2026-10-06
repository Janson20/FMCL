"""`services/install_service.py` 的永久回归守卫（阶段 3 任务 3.3）。

这一层守的是**行为**：可用版本清单怎么取、怎么复用（B-13），9 种加载器的
id ↔ 显示名 ↔ i18n 键映射（B-10），兼容提示的两条来源（本地规则 + 远端查询，
用户裁决 B），以及安装工作者的结果判读（B-11：成功 / 失败 / **取消**三态）。
旧实现长在 `ui/app_base.py` 的 Tk 向导上（只能手点验证），现在长在服务上，
所以每条规则都要有自动化判据。

**三条踩过的坑，这里各钉一根钉子**：

1. `LOADER_NONE` 从"存译文"改成稳定 id，但**传给核心的仍是显示名**（`"无"`）——
   写错就会让核心的 `MOD_LOADER_IDS` 查不到加载器；
2. **取消不是失败**：用户点了取消之后界面不该说"安装失败"，所以
   `InstallCancelled` 单独映射成 `state="cancelled"`，并顺手重扫版本列表；
3. `downloader.py:640-643` 的 `version.startswith("2.0")` 是 LegacyFabric 的
   **版本号归一化**（清单里用 `2point0_` 前缀表示 2.0），不是"不支持 2.0" ——
   `local_loader_support()` 不许长出那条规则。

测试里不联网、不起后台线程池、不碰真文件系统：`FakeLauncher`（核心）、
`FakeTasks`（调度器）、`FakeVersion`（版本服务）、`RecordingObserver`（界面桥）
就是全部替身。`tasks` 走替身而不是真 `TaskRunner`，是为了把
"提交了几次""有没有叠加"变成可断言的**数字**。
"""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.context import AppContext  # noqa: E402
from launcher.errors import InstallCancelled  # noqa: E402
from services.install_service import (  # noqa: E402
    AVAILABLE_TTL_SECONDS,
    COMPAT_BAD,
    COMPAT_CHECKING,
    COMPAT_IDLE,
    COMPAT_OK,
    COMPAT_UNKNOWN,
    CUSTOM_LOADERS,
    LOADER_DISPLAY,
    LOADER_IDS,
    LOADER_LABEL_KEYS,
    LOADER_NONE,
    PAGE_SIZE,
    QUERYABLE_LOADERS,
    RESULT_CANCELLED,
    RESULT_DONE,
    RESULT_FAILED,
    TAB_RELEASE,
    TAB_SNAPSHOT,
    TABS,
    UNKNOWN_TTL_SECONDS,
    InstallService,
    clean_version_id,
    is_release_version,
    local_loader_support,
    page_slice,
    split_available,
)

#: 界面文案要在四种语言里都找得到（少一个就会显示裸键名）
LANGUAGES = ("zh_CN", "en_US", "ja_JP", "zh_TW")

# ─── 替身 ──────────────────────────────────────────────────────


class FakeLauncher:
    """`MinecraftLauncher` 的替身（只实现安装向导用到的那三个成员）。"""

    def __init__(self, versions: Optional[List[Dict[str, Any]]] = None) -> None:
        self.versions: List[Dict[str, Any]] = list(versions or [])
        self.calls: List[Tuple[Any, ...]] = []
        #: 设上就让 `get_available_versions()` 抛（验"失败不清空已有清单"）
        self.available_error: Optional[BaseException] = None
        self.install_result: Any = (True, "")
        self.install_error: Optional[BaseException] = None
        #: 安装过程中主动上报的进度（核心回调 → 服务 → ctx）
        self.install_progress: List[Tuple[int, int, str]] = []
        self.compat_results: Dict[Tuple[str, str], Optional[bool]] = {}
        self.compat_error: Optional[BaseException] = None
        self.compat_calls = 0
        self.last_on_progress: Any = None
        self.last_cancel_check: Any = None

    # ── 可用版本清单 ──
    def get_available_versions(self) -> List[Dict[str, Any]]:
        self.calls.append(("get_available_versions",))
        if self.available_error is not None:
            raise self.available_error
        return [dict(item) for item in self.versions]

    # ── 兼容查询 ──
    def check_mod_loader_support(self, loader: str, version: str) -> Optional[bool]:
        self.calls.append(("check_mod_loader_support", loader, version))
        self.compat_calls += 1
        if self.compat_error is not None:
            raise self.compat_error
        return self.compat_results.get((loader, version))

    # ── 安装 ──
    def install_version(
        self,
        version_id: str,
        mod_loader: str,
        *,
        on_progress: Any = None,
        cancel_check: Any = None,
    ) -> Any:
        """核心的安装入口。签名与 `downloader.py` 的那份保持一致（关键字传参）。"""
        self.calls.append(("install_version", version_id, mod_loader))
        self.last_on_progress = on_progress
        self.last_cancel_check = cancel_check
        for current, total, text in self.install_progress:
            if on_progress is not None:
                on_progress(current, total, text)
        if self.install_error is not None:
            raise self.install_error
        return self.install_result

    def install_calls(self) -> List[Tuple[Any, ...]]:
        return [call for call in self.calls if call[0] == "install_version"]


class FakeHandle:
    """`TaskHandle` 的最小替身：只保留测试要用的完成状态。"""

    def __init__(self) -> None:
        self.result: Any = None
        self.error: Optional[BaseException] = None
        self.done = False

    def wait(self, timeout: Optional[float] = None) -> bool:
        return True


class FakeTasks:
    """`TaskRunner` 的替身：记录每次 `submit`，可以"攒着不放行"。

    ``auto=True``（默认）时任务同步跑完 —— 等价于"网络秒回"，绝大多数用例要的是这个；
    ``auto=False`` 时任务进 ``pending``，用来验"取数进行中重复点刷新不会叠加"。
    """

    def __init__(self, auto: bool = True) -> None:
        self.auto = auto
        self.calls: List[Dict[str, Any]] = []
        self.pending: List[Tuple[Dict[str, Any], FakeHandle]] = []

    def submit(self, fn: Any, *args: Any, **kwargs: Any) -> FakeHandle:
        record: Dict[str, Any] = {"fn": fn, "args": args, "kwargs": kwargs, "name": kwargs.get("name", "")}
        handle = FakeHandle()
        self.calls.append(record)
        if self.auto:
            self.run(record, handle)
        else:
            self.pending.append((record, handle))
        return handle

    @staticmethod
    def run(record: Dict[str, Any], handle: FakeHandle) -> FakeHandle:
        """按 `TaskRunner._execute` 的契约跑一个任务：成功给 `on_done`，失败给 `on_error`。"""
        try:
            handle.result = record["fn"](*record["args"])
        except BaseException as e:  # noqa: BLE001 - 替身要模拟"任务抛了"这条路径
            handle.error = e
            callback = record["kwargs"].get("on_error")
            if callback is not None:
                callback(e)
        else:
            callback = record["kwargs"].get("on_done")
            if callback is not None:
                callback(handle.result)
        handle.done = True
        return handle

    def drain(self) -> None:
        """放行所有攒下的任务。"""
        while self.pending:
            record, handle = self.pending.pop(0)
            self.run(record, handle)

    def names(self) -> List[str]:
        return [str(record["name"]) for record in self.calls]


class RecordingObserver:
    """安装向导观察者替身：把三类回调按顺序记下来。

    ``boom=True`` 时每条回调都抛：桥在任意线程上出错都不该把服务带崩。
    """

    def __init__(self, boom: bool = False) -> None:
        self.events: List[Tuple[Any, ...]] = []
        self.boom = boom

    def _record(self, event: Tuple[Any, ...]) -> None:
        self.events.append(event)
        if self.boom:
            raise RuntimeError("观察者炸了")

    def on_available(self, rows: List[Dict[str, Any]], error: str) -> None:
        self._record(("available", list(rows), str(error)))

    def on_status(self, key: str, level: str, params: Dict[str, Any]) -> None:
        self._record(("status", key, level, dict(params)))

    def on_compat(
        self, loader: str, version: str, state: str, source: str, supported: Optional[bool]
    ) -> None:
        self._record(("compat", loader, version, state, source, supported))

    # ── 断言助手 ──
    def status_keys(self) -> List[str]:
        return [event[1] for event in self.events if event[0] == "status"]

    def status_for(self, key: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        for event in self.events:
            if event[0] == "status" and event[1] == key:
                return event[2], event[3]
        return None

    def available(self) -> List[Tuple[List[Dict[str, Any]], str]]:
        return [(event[1], event[2]) for event in self.events if event[0] == "available"]

    def compat(self) -> List[Tuple[str, str, str, str, Optional[bool]]]:
        return [(event[1], event[2], event[3], event[4], event[5]) for event in self.events if event[0] == "compat"]

    def compat_states(self, loader: str, version: str) -> List[str]:
        """某个 (加载器, 版本) 上依次发生过的状态（`checking` → 终态）。"""
        return [event[2] for event in self.compat() if event[0] == loader and event[1] == version]


class FakeConfig:
    """`Config` 的替身：安装向导不读配置，这里只是让 `AppContext` 有个配置对象。"""


class FakeVersion:
    """`VersionService` 的替身：只记"装完之后被叫去干了什么"。"""

    def __init__(self, boom: bool = False) -> None:
        self.calls: List[Tuple[Any, ...]] = []
        self.boom = boom

    def note_installed(self, version_id: str) -> None:
        self.calls.append(("note_installed", str(version_id)))
        if self.boom:
            raise RuntimeError("版本服务炸了")

    def load(self, *, force: bool = False) -> Any:
        self.calls.append(("load", bool(force)))
        if self.boom:
            raise RuntimeError("版本服务炸了")
        return None


class FakeTaskCtx:
    """`Tasks` 桥交给工作者的 `ctx` 替身（`TaskContext` 的最小面）。"""

    def __init__(self) -> None:
        self.progress_calls: List[Tuple[int, int, str]] = []
        self.cancelled = False
        self.progress_error: Optional[BaseException] = None

    def progress(self, current: int, total: int = 0, message: str = "") -> None:
        self.progress_calls.append((current, total, message))
        if self.progress_error is not None:
            raise self.progress_error


class ExplodingCtx:
    """一问就炸的 ctx：验工作者"进度报不出去、问不到取消"时的兜底。"""

    @property
    def cancelled(self) -> bool:
        raise RuntimeError("ctx 坏了")

    def progress(self, current: int, total: int = 0, message: str = "") -> None:
        raise RuntimeError("ctx 坏了")


# ─── 装配 ──────────────────────────────────────────────────────

#: 传给 `make_service()` 表示"上下文里没有 launcher、也不显式注入"（验核心缺席的降级）
NO_LAUNCHER = object()


def make_service(
    launcher: Any = None,
    tasks: Optional[FakeTasks] = None,
    version: Any = None,
    observer: Optional[RecordingObserver] = None,
) -> Tuple[InstallService, Any, FakeTasks, RecordingObserver]:
    """按**生产取数路径**装一个服务：真 `AppContext` + 注入的假替身。

    与 `tests/test_version_service.py` 的装配方式一致（`AppContext` 比
    `bootstrap.build_context()` 轻得多，而服务取依赖的那条路径是同一份实现）。
    """
    if launcher is NO_LAUNCHER:
        fake_launcher: Any = None
    elif launcher is None:
        fake_launcher = FakeLauncher()
    else:
        fake_launcher = launcher
    fake_tasks = tasks if tasks is not None else FakeTasks()
    context = AppContext(config=FakeConfig(), tasks=fake_tasks)
    if fake_launcher is not None:
        context.register_instance("launcher", fake_launcher)
    if version is not None:
        context.register_instance("version", version)
    service = InstallService(launcher=fake_launcher)
    context.register(service)
    rec = observer if observer is not None else RecordingObserver()
    service.set_observer(rec)
    return service, fake_launcher, fake_tasks, rec


def available_list() -> List[Dict[str, Any]]:
    """一份典型的 Mojang 清单：3 个正式版 + 2 个测试版 + 1 个远古版（该被丢掉）。"""
    return [
        {"id": "1.20.4", "type": "release"},
        {"id": "24w14a", "type": "snapshot"},
        {"id": "1.20.3", "type": "release"},
        {"id": "1.20.5-pre1", "type": "snapshot"},
        {"id": "1.20.2", "type": "release"},
        {"id": "b1.7.3", "type": "old_beta"},
    ]


def ids(rows: Any) -> List[str]:
    return [str(row["id"]) for row in rows]


# ─── B-09 版本 ID 截断 ─────────────────────────────────────────


class TestCleanVersionId:
    """从第一个空白处截断（旧 `ui/app_handlers.py:289` 的 `version.split()[0]`）。"""

    def test_truncates_the_trailing_note(self) -> None:
        assert clean_version_id("1.20.4 (release)") == "1.20.4"

    def test_truncates_at_tab(self) -> None:
        # 制表符也是空白：清单里偶尔用 "\t" 分隔 id 与后缀
        assert clean_version_id("\t1.20.4\tx") == "1.20.4"

    def test_keeps_a_plain_id(self) -> None:
        assert clean_version_id("1.20.4") == "1.20.4"

    def test_strips_surrounding_whitespace(self) -> None:
        # 用户手输时常带前导空格，不清掉会让核心找不到这个版本
        assert clean_version_id("  1.20.4  ") == "1.20.4"

    def test_empty_string(self) -> None:
        assert clean_version_id("") == ""

    def test_whitespace_only(self) -> None:
        assert clean_version_id(" \t\n ") == ""

    def test_none_is_empty_not_the_string_none(self) -> None:
        # 返回值必须始终是"可以直接当版本号用"的字符串（调用方不必先判空）
        assert clean_version_id(None) == ""


# ─── B-13 清单分类与分页 ───────────────────────────────────────


class TestSplitAvailable:
    """只认 release/snapshot，且**保持上游顺序**（新 → 旧）。"""

    def test_splits_and_keeps_upstream_order(self) -> None:
        release, snapshot = split_available([
            {"id": "1.20.4", "type": "release"},
            {"id": "24w14a", "type": "snapshot"},
            {"id": "1.20.3", "type": "release"},
            {"id": "1.20.5-pre1", "type": "snapshot"},
        ])
        assert ids(release) == ["1.20.4", "1.20.3"]
        assert ids(snapshot) == ["24w14a", "1.20.5-pre1"]

    def test_old_alpha_and_old_beta_are_dropped(self) -> None:
        # 旧界面同样不显示它们：远古版的安装链路与正式版完全不同
        release, snapshot = split_available([
            {"id": "a1.2.6", "type": "old_alpha"},
            {"id": "b1.7.3", "type": "old_beta"},
            {"id": "1.20.4", "type": "release"},
        ])
        assert ids(release) == ["1.20.4"]
        assert snapshot == []

    def test_row_shape_is_exactly_three_fields(self) -> None:
        release, _snapshot = split_available([{"id": "1.20.4", "type": "release", "url": "http://x"}])
        # 行会直接交给 QML 绑定：字段多了会把上游的内部结构泄进界面层
        assert release[0] == {"id": "1.20.4", "type": "release", "snapshot": False}

    def test_snapshot_row_is_flagged(self) -> None:
        release, snapshot = split_available([{"id": "24w14a", "type": "snapshot"}])
        assert snapshot[0]["snapshot"] is True
        assert release == []

    def test_entries_without_id_are_skipped(self) -> None:
        release, snapshot = split_available([
            {"type": "release"},
            {"id": "", "type": "release"},
            {"id": "   ", "type": "release"},
            {"id": "1.20.4", "type": "release"},
        ])
        assert ids(release) == ["1.20.4"]
        assert snapshot == []

    def test_id_is_stripped(self) -> None:
        release, _snapshot = split_available([{"id": " 1.20.4 ", "type": "release"}])
        assert release[0]["id"] == "1.20.4"

    def test_unknown_type_is_dropped(self) -> None:
        release, snapshot = split_available([{"id": "x", "type": "modified"}])
        assert (release, snapshot) == ([], [])

    def test_non_dict_entries_are_skipped(self) -> None:
        release, _snapshot = split_available(["1.20.4", None, 42, {"id": "1.19.2", "type": "release"}])
        assert ids(release) == ["1.19.2"]

    def test_empty_and_none_input(self) -> None:
        assert split_available([]) == ([], [])
        assert split_available(None) == ([], [])


class TestPageSlice:
    """每页 20 条 + 页码夹取（旧 `_render_current_tab` 的语义）。"""

    ROWS = [{"id": "v%02d" % index} for index in range(45)]

    def test_first_page(self) -> None:
        data = page_slice(self.ROWS, 1)
        assert data["page"] == 1
        assert data["pageCount"] == 3
        assert data["total"] == 45
        assert ids(data["rows"]) == ["v%02d" % index for index in range(0, 20)]

    def test_last_page_is_short(self) -> None:
        data = page_slice(self.ROWS, 3)
        assert ids(data["rows"]) == ["v%02d" % index for index in range(40, 45)]

    def test_page_zero_and_negative_clamp_to_the_first(self) -> None:
        # 页码越界必须夹取而不是返回空页：旧界面点"上一页"到底时会走到 0
        assert page_slice(self.ROWS, 0)["page"] == 1
        assert page_slice(self.ROWS, -5)["page"] == 1
        assert ids(page_slice(self.ROWS, 0)["rows"])[0] == "v00"

    def test_page_beyond_the_end_clamps_to_the_last(self) -> None:
        data = page_slice(self.ROWS, 999)
        assert data["page"] == 3
        assert ids(data["rows"]) == ["v%02d" % index for index in range(40, 45)]

    def test_non_numeric_page_falls_back_to_the_first(self) -> None:
        # 绑定层可能把输入框里的空串直接递进来
        assert page_slice(self.ROWS, "")["page"] == 1
        assert page_slice(self.ROWS, "abc")["page"] == 1
        assert page_slice(self.ROWS, None)["page"] == 1

    def test_numeric_string_page_works(self) -> None:
        assert page_slice(self.ROWS, "2")["page"] == 2

    def test_empty_list_is_one_page(self) -> None:
        # 旧界面空列表显示 "1/1"：0 页会让 QML 的分页条算出 1/0
        assert page_slice([], 1) == {"rows": [], "page": 1, "pageCount": 1, "total": 0}
        assert page_slice([], 7)["page"] == 1

    def test_page_count_is_at_least_one(self) -> None:
        assert page_slice([{"id": "a"}], 1)["pageCount"] == 1
        assert page_slice([], 1)["pageCount"] == 1

    def test_size_zero_falls_back_to_the_default_page_size(self) -> None:
        # size=0 会让页数算出除零/空页，服务兜成默认每页 20
        data = page_slice(self.ROWS, 1, 0)
        assert len(data["rows"]) == PAGE_SIZE
        assert data["pageCount"] == 3

    def test_custom_size(self) -> None:
        data = page_slice(self.ROWS, 2, 10)
        assert ids(data["rows"]) == ["v%02d" % index for index in range(10, 20)]
        assert data["pageCount"] == 5

    def test_input_list_is_not_aliased(self) -> None:
        rows = [{"id": "a"}]
        data = page_slice(rows, 1)
        data["rows"].append({"id": "b"})
        assert rows == [{"id": "a"}]


class TestIsReleaseVersion:
    """是不是"正式版号"（快照 / 预发布 / RC 都不算）。"""

    def test_plain_and_new_format_releases(self) -> None:
        assert is_release_version("1.20.4") is True
        assert is_release_version("1.12.2") is True
        assert is_release_version("26.1") is True

    def test_snapshots_are_not_releases(self) -> None:
        assert is_release_version("24w14a") is False
        assert is_release_version("snapshot-1") is False

    def test_pre_release_and_rc_are_not_releases(self) -> None:
        assert is_release_version("1.20.5-pre1") is False
        assert is_release_version("1.20.5-rc1") is False
        assert is_release_version("1.7.10-pre4") is False

    def test_blank_is_not_a_release(self) -> None:
        assert is_release_version("") is False
        assert is_release_version("   ") is False
        assert is_release_version(None) is False


# ─── B-10 加载器映射 ───────────────────────────────────────────


class TestLoaderTables:
    """9 种加载器的三张表必须**键集合完全一致**。"""

    EXPECTED_ORDER = (
        "none",
        "forge",
        "fabric",
        "neoforge",
        "quilt",
        "liteloader",
        "legacyfabric",
        "cleanroom",
        "optifine",
    )

    def test_ids_are_exactly_nine_and_in_the_old_order(self) -> None:
        # 顺序逐字对齐旧下拉（`ui/app_base.py:1196-1206`）：换顺序用户一眼就能看出来
        assert LOADER_IDS == self.EXPECTED_ORDER
        assert len(LOADER_IDS) == 9

    def test_label_keys_cover_every_loader(self) -> None:
        # 少一个键，那个加载器在下拉里就会显示成 `mod_loader_xxx`
        assert set(LOADER_LABEL_KEYS) == set(LOADER_IDS)

    def test_display_names_cover_every_loader(self) -> None:
        assert set(LOADER_DISPLAY) == set(LOADER_IDS)

    def test_display_names_are_exactly_the_old_literals(self) -> None:
        # 这几个字面量是**传给核心**的键（`downloader.MOD_LOADER_IDS`），不能改
        assert LOADER_DISPLAY[LOADER_NONE] == "无"
        assert LOADER_DISPLAY["forge"] == "Forge"
        assert LOADER_DISPLAY["optifine"] == "OptiFine"
        assert LOADER_DISPLAY["neoforge"] == "NeoForge"
        assert LOADER_DISPLAY["legacyfabric"] == "LegacyFabric"

    def test_label_keys_are_the_mod_loader_prefix(self) -> None:
        for loader_id, key in LOADER_LABEL_KEYS.items():
            assert key == "mod_loader_%s" % loader_id

    def test_queryable_and_custom_split_the_rest(self) -> None:
        rest = set(LOADER_IDS) - {LOADER_NONE}
        assert set(QUERYABLE_LOADERS) | set(CUSTOM_LOADERS) == rest
        assert set(QUERYABLE_LOADERS) & set(CUSTOM_LOADERS) == set()
        assert LOADER_NONE not in QUERYABLE_LOADERS + CUSTOM_LOADERS

    def test_tabs_and_page_size(self) -> None:
        assert TABS == (TAB_RELEASE, TAB_SNAPSHOT)
        assert PAGE_SIZE == 20


# ─── B-10 本地兼容规则 ─────────────────────────────────────────


class TestLocalLoaderSupport:
    """四个自定义安装器的**本地**规则（返回 `(三态, 依据)`）。"""

    def test_cleanroom_only_supports_1_12_2(self) -> None:
        assert local_loader_support("cleanroom", "1.12.2") == (True, "local:cleanroom")
        assert local_loader_support("cleanroom", "1.20.1") == (False, "local:cleanroom")

    def test_liteloader_upper_bound_is_1_12_2(self) -> None:
        assert local_loader_support("liteloader", "1.20.1") == (False, "local:liteloader_bound")
        assert local_loader_support("liteloader", "1.13") == (False, "local:liteloader_bound")

    def test_liteloader_inside_the_range_is_unknown(self) -> None:
        # 1.7.10~1.12.2 之间到底有没有，只有它自己的清单说了算 —— 本地不猜、也不谎报支持
        assert local_loader_support("liteloader", "1.12.2") == (None, "")
        assert local_loader_support("liteloader", "1.7.10") == (None, "")

    @pytest.mark.parametrize("loader", CUSTOM_LOADERS)
    def test_non_release_versions_are_unsupported(self, loader: str) -> None:
        # 四个自定义安装器都按**正式版号**去查各自清单：快照/预发布判不支持是保守提示
        for version in ("24w14a", "1.20.5-pre1"):
            assert local_loader_support(loader, version) == (False, "local:release_only")

    @pytest.mark.parametrize("loader", QUERYABLE_LOADERS)
    def test_queryable_loaders_are_not_judged_locally(self, loader: str) -> None:
        # mcllib 那四个要看各自官方 meta，本地一律"未知"
        assert local_loader_support(loader, "1.20.1") == (None, "")

    def test_legacyfabric_never_judges_2_0_unsupported(self) -> None:
        """`2.0` 不在本地规则里 —— 那条 `startswith("2.0")` 是版本号归一化，不是不支持。

        `downloader.py:640-643` 用它把 LegacyFabric 清单里的 `2point0_xxx` 映射回
        MC 的 `2.0`；曾经误读成"LegacyFabric 不支持 2.0"，于是兼容提示无端报红。
        """
        supported, source = local_loader_support("legacyfabric", "2.0")
        assert supported is not False, "2.0 被判不支持 = 那条误读的规则又长回来了"
        assert (supported, source) == (None, "")

    def test_legacyfabric_stays_unknown_for_other_versions(self) -> None:
        assert local_loader_support("legacyfabric", "1.12.2") == (None, "")
        assert local_loader_support("legacyfabric", "1.13.2") == (None, "")

    def test_non_custom_loaders_and_blank_input(self) -> None:
        assert local_loader_support(LOADER_NONE, "1.12.2") == (None, "")
        assert local_loader_support("", "1.12.2") == (None, "")
        assert local_loader_support("cleanroom", "") == (None, "")
        assert local_loader_support("cleanroom", "   ") == (None, "")

    def test_loader_id_and_version_are_normalized(self) -> None:
        # 加载器 id 大小写无关；版本号先过 `clean_version_id`（列表行可能带后缀）
        assert local_loader_support("CleanRoom", " 1.12.2 ") == (True, "local:cleanroom")
        assert local_loader_support("LiteLoader", "1.12.2") == (None, "")
        assert local_loader_support("cleanroom", "1.12.2 (release)") == (True, "local:cleanroom")

    def test_uncomparable_version_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """版本号比不动时返回"未知"，绝不返回"不支持"（宁可说不知道）。"""

        def boom(_a: str, _b: str) -> int:
            raise ValueError("比不了")

        monkeypatch.setattr("services.install_service.compare_versions", boom)
        assert local_loader_support("liteloader", "1.12.2") == (None, "")


# ─── B-13 可用版本清单（服务层）─────────────────────────────────


class TestLoadAvailable:
    """取数走 `self.tasks.submit`；失败**不清空**已有清单；窗口内不重复联网。"""

    def test_load_goes_through_the_task_runner(self) -> None:
        service, _launcher, tasks, _observer = make_service()
        handle = service.load_available()

        assert handle is not None, "冷启动必须真的提交任务，返回 None 会让界面永远停在加载中"
        assert tasks.names() == ["install.available"]
        assert tasks.calls[0]["fn"] == service._load_sync
        assert callable(tasks.calls[0]["kwargs"]["on_error"]), "失败路径要能被 Tasks 桥回报"

    def test_load_publishes_counts_and_pages(self) -> None:
        service, _launcher, _tasks, _observer = make_service(FakeLauncher(available_list()))
        service.load_available()

        assert service.release_count == 3
        assert service.snapshot_count == 2

        page = service.page_of(TAB_RELEASE, 1)
        assert ids(page["rows"]) == ["1.20.4", "1.20.3", "1.20.2"]
        assert (page["tab"], page["releaseCount"], page["snapshotCount"]) == (TAB_RELEASE, 3, 2)
        assert ids(service.page_of(TAB_SNAPSHOT, 1)["rows"]) == ["24w14a", "1.20.5-pre1"]

    def test_old_versions_never_reach_the_list(self) -> None:
        service, _launcher, _tasks, observer = make_service(FakeLauncher(available_list()))
        service.load_available()

        rows, error = observer.available()[-1]
        assert error == ""
        assert ids(rows) == ["1.20.4", "1.20.3", "1.20.2", "24w14a", "1.20.5-pre1"]

    def test_unknown_tab_falls_back_to_release(self) -> None:
        service, _launcher, _tasks, _observer = make_service(FakeLauncher(available_list()))
        service.load_available()

        page = service.page_of("乱写的标签页", 1)
        assert page["tab"] == TAB_RELEASE
        assert ids(page["rows"])[0] == "1.20.4"

    def test_page_of_clamps_the_page_number(self) -> None:
        # 45 个正式版 = 3 页；越界页码由服务夹取（QML 不该自己算）
        versions = [{"id": "1.%d" % index, "type": "release"} for index in range(45)]
        service, _launcher, _tasks, _observer = make_service(FakeLauncher(versions))
        service.load_available()

        assert service.page_of(TAB_RELEASE, 999)["page"] == 3
        assert len(service.page_of(TAB_RELEASE, 999)["rows"]) == 5
        assert service.page_of(TAB_RELEASE, 0)["page"] == 1

    def test_status_keys_around_a_successful_load(self) -> None:
        service, _launcher, _tasks, observer = make_service(FakeLauncher(available_list()))
        service.load_available()

        assert observer.status_keys() == ["loading_available", "version_available_loaded"]
        assert observer.status_for("loading_available") == ("loading", {})
        assert observer.status_for("version_available_loaded") == ("success", {"release": 3, "snapshot": 2})

    def test_failure_keeps_the_previous_list(self) -> None:
        launcher = FakeLauncher(available_list())
        service, _launcher, _tasks, observer = make_service(launcher)
        service.load_available()

        launcher.available_error = RuntimeError("网络断了")
        service.load_available(force=True)

        # 一次网络抖动不该让用户面前的列表消失
        assert (service.release_count, service.snapshot_count) == (3, 2)
        rows, error = observer.available()[-1]
        assert error == "网络断了"
        assert ids(rows) == ["1.20.4", "1.20.3", "1.20.2", "24w14a", "1.20.5-pre1"]
        assert observer.status_for("version_available_failed") == ("warning", {"error": "网络断了"})

    def test_failure_on_a_cold_start_is_an_empty_list(self) -> None:
        launcher = FakeLauncher(available_list())
        launcher.available_error = RuntimeError("网络断了")
        service, _launcher, _tasks, observer = make_service(launcher)
        service.load_available()

        assert (service.release_count, service.snapshot_count) == (0, 0)
        assert observer.available()[-1] == ([], "网络断了")

    def test_missing_launcher_is_reported_as_unavailable(self) -> None:
        service, _launcher, _tasks, observer = make_service(NO_LAUNCHER)
        handle = service.load_available()

        assert handle is not None
        assert observer.available()[-1] == ([], "launcher_unavailable")
        assert observer.status_for("version_available_failed") == ("warning", {"error": "launcher_unavailable"})

    def test_reuse_window_skips_the_network(self) -> None:
        service, launcher, tasks, observer = make_service(FakeLauncher(available_list()))
        service.load_available()
        assert service.available_is_fresh() is True

        assert service.load_available() is None, "窗口内复用必须返回 None（没有新任务）"
        assert len(tasks.calls) == 1
        assert launcher.calls.count(("get_available_versions",)) == 1
        # 复用时要**重放**清单：桥可能刚建好，没收到上一次的通知
        assert ids(observer.available()[-1][0]) == ["1.20.4", "1.20.3", "1.20.2", "24w14a", "1.20.5-pre1"]

    def test_stale_timestamp_turns_the_window_off(self) -> None:
        service, _launcher, _tasks, _observer = make_service(FakeLauncher(available_list()))
        service.load_available()

        service._available_at = time.time() - (AVAILABLE_TTL_SECONDS + 1)  # noqa: SLF001 - 等 300 秒不现实
        assert service.available_is_fresh() is False
        assert service.load_available() is not None

    def test_a_cold_service_is_never_fresh(self) -> None:
        service, _launcher, _tasks, _observer = make_service()
        assert service.available_is_fresh() is False

    def test_force_always_refetches(self) -> None:
        service, launcher, tasks, _observer = make_service(FakeLauncher(available_list()))
        service.load_available()

        assert service.load_available(force=True) is not None
        assert len(tasks.calls) == 2
        assert launcher.calls.count(("get_available_versions",)) == 2

    def test_in_flight_load_is_not_stacked(self) -> None:
        tasks = FakeTasks(auto=False)
        service, _launcher, _tasks, _observer = make_service(FakeLauncher(available_list()), tasks=tasks)

        first = service.load_available()
        second = service.load_available()  # 用户在刷新按钮上连点
        assert first is not None
        assert second is None, "_loading 守卫：已经在取就不许再叠一个任务"
        assert len(tasks.calls) == 1
        assert service.release_count == 0

        tasks.drain()
        assert service.release_count == 3
        # 放行后再调是"窗口内复用"（仍不联网），而不是"还在取"
        assert service.load_available() is None
        assert len(tasks.calls) == 1

    def test_observer_exception_does_not_break_the_load(self) -> None:
        service, _launcher, _tasks, _observer = make_service(
            FakeLauncher(available_list()), observer=RecordingObserver(boom=True)
        )
        assert service.load_available() is not None
        assert service.release_count == 3, "观察者炸了不该让清单本身丢数据"


# ─── B-10 兼容查询（服务层）────────────────────────────────────


class TestCheckCompat:
    """本地规则优先、远端兜底、结论进缓存；"查不出来"单独一态。"""

    def test_none_loader_is_idle(self) -> None:
        service, launcher, tasks, observer = make_service()
        assert service.check_compat("1.20.4", LOADER_NONE) is None
        assert tasks.calls == []
        assert launcher.compat_calls == 0
        assert observer.compat() == [(LOADER_NONE, "1.20.4", COMPAT_IDLE, "", None)]

    def test_blank_version_or_loader_is_idle(self) -> None:
        service, _launcher, tasks, observer = make_service()
        assert service.check_compat("", "forge") is None
        assert service.check_compat("   ", "forge") is None
        assert service.check_compat("1.20.4", "") is None

        assert tasks.calls == []
        assert [event[2] for event in observer.compat()] == [COMPAT_IDLE] * 3

    def test_unknown_loader_id_is_idle(self) -> None:
        # 未知 id 不能当成"某个加载器"去查：下拉里没有它，查了也没人显示
        service, launcher, tasks, observer = make_service()
        assert service.check_compat("1.20.4", "curse") is None
        assert tasks.calls == []
        assert launcher.compat_calls == 0
        assert observer.compat()[-1][2] == COMPAT_IDLE

    def test_queryable_loader_asks_the_core(self) -> None:
        launcher = FakeLauncher()
        launcher.compat_results[("forge", "1.20.1")] = True
        service, _launcher, tasks, observer = make_service(launcher)

        assert service.check_compat("1.20.1", "forge") is not None

        assert launcher.compat_calls == 1
        assert ("check_mod_loader_support", "forge", "1.20.1") in launcher.calls
        assert tasks.names() == ["install.compat.forge"]
        assert observer.compat_states("forge", "1.20.1") == [COMPAT_CHECKING, COMPAT_OK]
        assert observer.compat()[-1][3] == "remote"

    def test_remote_false_is_bad(self) -> None:
        launcher = FakeLauncher()
        launcher.compat_results[("fabric", "1.7.10")] = False
        service, _launcher, _tasks, observer = make_service(launcher)

        service.check_compat("1.7.10", "fabric")
        assert observer.compat()[-1][2:5] == (COMPAT_BAD, "remote", False)

    def test_remote_none_is_unknown_not_bad(self) -> None:
        # "查不出来"与"不支持"是两回事：界面按参考信息显示，绝不据此拦住安装
        launcher = FakeLauncher()
        launcher.compat_results[("forge", "1.20.1")] = None
        service, _launcher, _tasks, observer = make_service(launcher)

        service.check_compat("1.20.1", "forge")
        assert observer.compat()[-1][2] == COMPAT_UNKNOWN
        assert observer.compat()[-1][2] != COMPAT_BAD

    def test_remote_exception_is_unknown(self) -> None:
        launcher = FakeLauncher()
        launcher.compat_error = RuntimeError("meta 取不到")
        service, _launcher, _tasks, observer = make_service(launcher)

        service.check_compat("1.20.1", "quilt")
        assert observer.compat()[-1][2] == COMPAT_UNKNOWN

    def test_local_rule_hit_does_not_touch_the_core(self) -> None:
        service, launcher, tasks, observer = make_service(FakeLauncher())

        service.check_compat("1.12.2", "cleanroom")
        assert observer.compat()[-1][2:5] == (COMPAT_OK, "local:cleanroom", True)

        service.check_compat("1.20.1", "liteloader")
        assert observer.compat()[-1][2:5] == (COMPAT_BAD, "local:liteloader_bound", False)

        assert launcher.compat_calls == 0, "本地就能判的不该联网"
        assert len(tasks.calls) == 2, "但结论仍要走同一条任务链路回到观察者"

    def test_legacyfabric_2_0_is_not_reported_bad(self) -> None:
        """同一根钉子钉在服务层：LegacyFabric + 2.0 只能"未知"，不能报不支持。"""
        service, launcher, _tasks, observer = make_service(FakeLauncher())

        service.check_compat("2.0", "legacyfabric")
        assert observer.compat()[-1][2] == COMPAT_UNKNOWN
        assert observer.compat()[-1][2] != COMPAT_BAD
        assert launcher.compat_calls == 0

    def test_custom_loader_without_a_list_is_unknown(self) -> None:
        # optifine 的清单本地查不到 → 未知（不是 bad）
        service, _launcher, _tasks, observer = make_service(FakeLauncher())
        service.check_compat("1.8.9", "optifine")
        assert observer.compat()[-1][2:5] == (COMPAT_UNKNOWN, "", None)

    def test_cache_hit_does_not_query_again(self) -> None:
        launcher = FakeLauncher()
        launcher.compat_results[("forge", "1.20.1")] = True
        service, _launcher, tasks, observer = make_service(launcher)

        service.check_compat("1.20.1", "forge")
        service.check_compat("1.20.1", "forge")

        assert launcher.compat_calls == 1, "命中缓存还联网 = 用户每敲一个字符就打一次官方 meta"
        assert len(tasks.calls) == 1
        assert observer.compat_states("forge", "1.20.1") == [COMPAT_CHECKING, COMPAT_OK, COMPAT_OK]

    def test_cache_key_uses_the_cleaned_version(self) -> None:
        launcher = FakeLauncher()
        launcher.compat_results[("forge", "1.20.1")] = True
        service, _launcher, tasks, observer = make_service(launcher)

        service.check_compat("1.20.1 (release)", "Forge")
        service.check_compat(" 1.20.1 ", "forge")

        assert len(tasks.calls) == 1, "带后缀/带空格的写法必须落到同一个缓存键上"
        assert observer.compat()[-1][:2] == ("forge", "1.20.1")

    def test_unknown_cache_expires(self) -> None:
        launcher = FakeLauncher()
        launcher.compat_results[("forge", "1.20.1")] = None
        service, _launcher, tasks, _observer = make_service(launcher)

        service.check_compat("1.20.1", "forge")
        service.check_compat("1.20.1", "forge")
        assert len(tasks.calls) == 1, "unknown 在有效期里也不该反复查"

        service._compat_cache[("forge", "1.20.1")] = (  # noqa: SLF001 - 网络抖出来的 unknown 不该钉死一整轮会话
            COMPAT_UNKNOWN, "remote", None, time.time() - (UNKNOWN_TTL_SECONDS + 1),
        )
        service.check_compat("1.20.1", "forge")
        assert len(tasks.calls) == 2, "过期的 unknown 必须重查（否则用户修好网络也刷不出来）"
        assert launcher.compat_calls == 2

    def test_ok_and_bad_cache_never_expire(self) -> None:
        launcher = FakeLauncher()
        launcher.compat_results[("forge", "1.20.1")] = True
        service, _launcher, tasks, _observer = make_service(launcher)

        service.check_compat("1.20.1", "forge")
        service._compat_cache[("forge", "1.20.1")] = (  # noqa: SLF001 - 明确的答案不会因为时间过去而变
            COMPAT_OK, "remote", True, time.time() - 100 * UNKNOWN_TTL_SECONDS,
        )
        service.check_compat("1.20.1", "forge")
        assert len(tasks.calls) == 1


# ─── B-11 安装工作者 ───────────────────────────────────────────


class TestInstallTask:
    """`install_task(ctx, version_id=…, loader=…)`：返回值就是结果字典，三态要分清。"""

    def test_success_returns_the_result_dict(self) -> None:
        service, _launcher, _tasks, _observer = make_service(FakeLauncher())

        result = service.install_task(FakeTaskCtx(), version_id="1.20.4", loader=LOADER_NONE)

        # 这个字典由 Tasks 桥原样回传给界面：键名就是契约
        assert result == {
            "ok": True,
            "state": RESULT_DONE,
            "requested": "1.20.4",
            "installed": "1.20.4",
            "loader": LOADER_NONE,
            "loaderName": "无",
        }

    @pytest.mark.parametrize("loader_id", LOADER_IDS)
    def test_every_loader_id_is_accepted_and_named(self, loader_id: str) -> None:
        service, launcher, _tasks, _observer = make_service(FakeLauncher())

        result = service.install_task(FakeTaskCtx(), version_id="1.20.4", loader=loader_id)

        assert result["loader"] == loader_id
        assert result["loaderName"] == LOADER_DISPLAY[loader_id]
        # 核心的 `MOD_LOADER_IDS` 认的是显示名 —— 传稳定 id 过去会找不到加载器
        assert launcher.install_calls() == [("install_version", "1.20.4", LOADER_DISPLAY[loader_id])]

    @pytest.mark.parametrize("loader_id,display", [("none", "无"), ("forge", "Forge"), ("optifine", "OptiFine")])
    def test_display_names_are_the_old_literals(self, loader_id: str, display: str) -> None:
        # 这三个字面量是旧界面原样递给核心的（`MOD_LOADER_IDS` 的键），钉死它免得被"优化"成 id
        service, launcher, _tasks, _observer = make_service(FakeLauncher())

        result = service.install_task(FakeTaskCtx(), version_id="1.20.4", loader=loader_id)

        assert result["loaderName"] == display
        assert launcher.install_calls() == [("install_version", "1.20.4", display)]

    def test_status_keys_on_success_without_loader(self) -> None:
        service, _launcher, _tasks, observer = make_service(FakeLauncher())

        service.install_task(FakeTaskCtx(), version_id="1.20.4", loader=LOADER_NONE)

        assert observer.status_keys() == ["version_installing", "version_installed"]
        assert observer.status_for("version_installing") == ("loading", {"version": "1.20.4"})
        assert observer.status_for("version_installed") == ("success", {"version": "1.20.4"})

    def test_status_keys_on_success_with_loader(self) -> None:
        service, _launcher, _tasks, observer = make_service(FakeLauncher())

        service.install_task(FakeTaskCtx(), version_id="1.20.4", loader="forge")

        assert observer.status_keys() == ["version_installing_loader", "version_installed"]
        assert observer.status_for("version_installing_loader") == (
            "loading",
            {"version": "1.20.4", "loader": "Forge"},
        )

    def test_installed_id_comes_from_the_core(self) -> None:
        # 装出来的目录名可能带加载器后缀（1.20.4-forge-49.0.26），要原样回填给界面
        launcher = FakeLauncher()
        launcher.install_result = (True, "1.20.4-forge-49.0.26")
        service, _launcher, _tasks, observer = make_service(launcher)

        result = service.install_task(FakeTaskCtx(), version_id="1.20.4", loader="forge")

        assert result["installed"] == "1.20.4-forge-49.0.26"
        assert result["requested"] == "1.20.4"
        assert observer.status_for("version_installed") == ("success", {"version": "1.20.4-forge-49.0.26"})

    def test_empty_version_id_does_nothing(self) -> None:
        service, launcher, _tasks, observer = make_service(FakeLauncher())

        result = service.install_task(FakeTaskCtx(), version_id="   ", loader="forge")

        assert result["ok"] is False
        assert result["state"] == RESULT_FAILED
        assert result["reason"] == "invalid"
        assert result["requested"] == ""
        assert launcher.install_calls() == []
        # 只允许提示"请输入版本 ID"：绝不能先发一条"正在安装"
        assert observer.status_keys() == ["version_id_required"]
        assert observer.status_for("version_id_required") == ("error", {})

    def test_version_id_is_cleaned(self) -> None:
        service, launcher, _tasks, observer = make_service(FakeLauncher())

        result = service.install_task(FakeTaskCtx(), version_id="1.20.4 (release)")

        assert result["requested"] == "1.20.4"
        assert launcher.install_calls() == [("install_version", "1.20.4", "无")]
        assert observer.status_for("version_installing")[1] == {"version": "1.20.4"}

    def test_unknown_loader_falls_back_to_none(self) -> None:
        service, launcher, _tasks, _observer = make_service(FakeLauncher())

        result = service.install_task(FakeTaskCtx(), version_id="1.20.4", loader="curse")

        assert result["loader"] == LOADER_NONE
        assert result["loaderName"] == "无"
        assert launcher.install_calls()[0][2] == "无"

    def test_missing_launcher_fails_with_a_reason(self) -> None:
        service, _launcher, _tasks, observer = make_service(NO_LAUNCHER)

        result = service.install_task(FakeTaskCtx(), version_id="1.20.4", loader="forge")

        assert result["ok"] is False
        assert result["state"] == RESULT_FAILED
        assert result["reason"] == "launcher_unavailable"
        assert observer.status_keys() == ["version_install_failed"]
        assert observer.status_for("version_install_failed") == ("error", {"version": "1.20.4"})

    def test_use_launcher_fills_the_gap(self) -> None:
        service, _launcher, _tasks, _observer = make_service(NO_LAUNCHER)
        ctx = FakeTaskCtx()
        assert service.install_task(ctx, version_id="1.20.4")["state"] == RESULT_FAILED

        launcher = FakeLauncher()
        service.use_launcher(launcher)  # 启动链条跑完之后核心才注册进上下文
        assert service.install_task(ctx, version_id="1.20.4")["state"] == RESULT_DONE
        assert launcher.install_calls() == [("install_version", "1.20.4", "无")]

    def test_cancelled_is_not_failed(self) -> None:
        launcher = FakeLauncher()
        launcher.install_error = InstallCancelled("用户点的取消")
        service, _launcher, _tasks, observer = make_service(launcher)

        result = service.install_task(FakeTaskCtx(), version_id="1.20.4", loader="forge")

        assert result["ok"] is False
        assert result["state"] == RESULT_CANCELLED, "取消是用户的决定，不是错误"
        assert observer.status_keys() == ["version_installing_loader", "version_install_cancelled"]
        assert observer.status_for("version_install_cancelled") == ("info", {"version": "1.20.4"})
        assert "error" not in result

    def test_other_exception_is_a_failure_with_the_text(self) -> None:
        launcher = FakeLauncher()
        launcher.install_error = RuntimeError("磁盘满了")
        service, _launcher, _tasks, observer = make_service(launcher)

        result = service.install_task(FakeTaskCtx(), version_id="1.20.4", loader="forge")

        assert result["ok"] is False
        assert result["state"] == RESULT_FAILED
        assert result["error"] == "磁盘满了"
        assert observer.status_for("version_install_failed") == ("error", {"version": "1.20.4"})

    def test_false_tuple_is_a_failure(self) -> None:
        # 核心的约定：`(False, version_id)` 就是"没装上"，抛异常才叫意外
        launcher = FakeLauncher()
        launcher.install_result = (False, "1.20.4")
        service, _launcher, _tasks, observer = make_service(launcher)

        result = service.install_task(FakeTaskCtx(), version_id="1.20.4", loader="forge")

        assert result["ok"] is False
        assert result["state"] == RESULT_FAILED
        assert result["reason"] == "install_failed"
        assert "installed" not in result
        assert observer.status_keys()[-1] == "version_install_failed"
        assert "version_installed" not in observer.status_keys()

    def test_progress_is_forwarded_without_the_status_text(self) -> None:
        launcher = FakeLauncher()
        launcher.install_progress = [(1, 10, "下载中"), (3, 10, "校验中")]
        service, _launcher, _tasks, _observer = make_service(launcher)
        ctx = FakeTaskCtx()

        service.install_task(ctx, version_id="1.20.4")

        # 文案走 on_status 那条路，进度通道只收数字（否则进度条副标题会来回跳）
        assert ctx.progress_calls == [(1, 10, ""), (3, 10, "")]

    def test_progress_failure_does_not_break_the_install(self) -> None:
        launcher = FakeLauncher()
        launcher.install_progress = [(1, 10, "x")]
        service, _launcher, _tasks, _observer = make_service(launcher)
        ctx = FakeTaskCtx()
        ctx.progress_error = RuntimeError("界面没了")

        assert service.install_task(ctx, version_id="1.20.4")["state"] == RESULT_DONE

    def test_cancel_check_reads_the_ctx_flag(self) -> None:
        service, launcher, _tasks, _observer = make_service(FakeLauncher())
        ctx = FakeTaskCtx()

        service.install_task(ctx, version_id="1.20.4")

        assert callable(launcher.last_cancel_check), "核心要靠这个回调判断用户是否点了取消"
        assert launcher.last_cancel_check() is False
        ctx.cancelled = True
        assert launcher.last_cancel_check() is True, "取消标志必须实时读，不能在开跑时缓存一次"

    def test_broken_ctx_does_not_break_the_install(self) -> None:
        service, launcher, _tasks, _observer = make_service(FakeLauncher())

        assert service.install_task(ExplodingCtx(), version_id="1.20.4")["state"] == RESULT_DONE
        assert launcher.last_cancel_check() is False, "问不到取消就当没取消"


# ─── 装完之后与版本服务的交接（用户裁决 4）─────────────────────


class TestVersionHandoff:
    """成功 → `note_installed()`；取消 → `load(force=True)`；两者缺席都不能出错。"""

    def test_success_notes_the_installed_id(self) -> None:
        launcher = FakeLauncher()
        launcher.install_result = (True, "1.20.4-forge-49.0.26")
        version = FakeVersion()
        service, _launcher, _tasks, _observer = make_service(launcher, version=version)

        service.install_task(FakeTaskCtx(), version_id="1.20.4", loader="forge")

        # 版本服务据此重扫列表并记下"待选中"，界面才好在装完后回列表并选中它
        assert version.calls == [("note_installed", "1.20.4-forge-49.0.26")]

    def test_cancel_refreshes_the_list(self) -> None:
        launcher = FakeLauncher()
        launcher.install_error = InstallCancelled()
        version = FakeVersion()
        service, _launcher, _tasks, _observer = make_service(launcher, version=version)

        service.install_task(FakeTaskCtx(), version_id="1.20.4")

        # 取消可能落在"版本 JSON 刚写、jar 还没下"的中间态：磁盘上多了半个版本，必须重扫
        assert version.calls == [("load", True)]

    def test_failure_does_not_touch_the_version_service(self) -> None:
        launcher = FakeLauncher()
        launcher.install_result = (False, "1.20.4")
        version = FakeVersion()
        service, _launcher, _tasks, _observer = make_service(launcher, version=version)

        service.install_task(FakeTaskCtx(), version_id="1.20.4")
        assert version.calls == [], "装失败时磁盘没变，不该惊动版本列表"

    def test_missing_version_service_is_not_an_error(self) -> None:
        launcher = FakeLauncher()
        service, _launcher, _tasks, _observer = make_service(launcher)

        assert service.install_task(FakeTaskCtx(), version_id="1.20.4")["state"] == RESULT_DONE

        launcher.install_error = InstallCancelled()
        assert service.install_task(FakeTaskCtx(), version_id="1.20.4")["state"] == RESULT_CANCELLED

    def test_broken_version_service_is_swallowed(self) -> None:
        launcher = FakeLauncher()
        launcher.install_result = (True, "1.20.4")
        service, _launcher, _tasks, _observer = make_service(launcher, version=FakeVersion(boom=True))

        # 版本服务只是"顺带通知"：它坏了不能让已经装好的结果变成失败
        assert service.install_task(FakeTaskCtx(), version_id="1.20.4")["state"] == RESULT_DONE

        launcher.install_error = InstallCancelled()
        assert service.install_task(FakeTaskCtx(), version_id="1.20.4")["state"] == RESULT_CANCELLED

    def test_without_an_observer_everything_still_runs(self) -> None:
        # 桥还没接上时（启动早期）也要能跑：`_emit` 对 None 观察者静默
        launcher = FakeLauncher()
        service, _launcher, _tasks, _observer = make_service(launcher)
        service.set_observer(None)

        assert service.install_task(FakeTaskCtx(), version_id="1.20.4")["state"] == RESULT_DONE
        assert service.load_available() is not None

    def test_observer_exception_does_not_break_the_install(self) -> None:
        service, _launcher, _tasks, _observer = make_service(
            FakeLauncher(), observer=RecordingObserver(boom=True)
        )
        assert service.install_task(FakeTaskCtx(), version_id="1.20.4")["state"] == RESULT_DONE


# ─── 上下文坏掉时的降级 ────────────────────────────────────────


class BrokenLauncherContext(AppContext):
    """`try_get("launcher")` 直接抛的上下文（验"取核心失败"的降级）。"""

    def try_get(self, name: str) -> Any:
        if name == "launcher":
            raise RuntimeError("上下文坏了")
        return super().try_get(name)


class BrokenVersionContext(AppContext):
    """`try_get("version")` 直接抛的上下文（验"取版本服务失败"的降级）。"""

    def try_get(self, name: str) -> Any:
        if name == "version":
            raise RuntimeError("上下文坏了")
        return super().try_get(name)


class TestDegradedContexts:
    """上下文本身坏掉时：取不到核心 = 没有核心，取不到版本服务 = 不自动刷新。"""

    def test_broken_launcher_lookup_degrades_to_unavailable(self) -> None:
        context = BrokenLauncherContext(config=FakeConfig(), tasks=FakeTasks())
        service = InstallService()
        context.register(service)
        observer = RecordingObserver()
        service.set_observer(observer)

        result = service.install_task(FakeTaskCtx(), version_id="1.20.4")

        assert result["state"] == RESULT_FAILED
        assert result["reason"] == "launcher_unavailable"

        assert service.load_available() is not None, "取核心炸了也要把任务收干净，不能把异常漏给界面"
        assert observer.available()[-1] == ([], "launcher_unavailable")

    def test_broken_version_lookup_does_not_break_the_install(self) -> None:
        launcher = FakeLauncher()
        launcher.install_result = (True, "1.20.4")
        context = BrokenVersionContext(config=FakeConfig(), tasks=FakeTasks())
        context.register_instance("launcher", launcher)
        service = InstallService(launcher=launcher)
        context.register(service)

        assert service.install_task(FakeTaskCtx(), version_id="1.20.4")["state"] == RESULT_DONE

        launcher.install_error = InstallCancelled()
        assert service.install_task(FakeTaskCtx(), version_id="1.20.4")["state"] == RESULT_CANCELLED


# ─── 真 TaskRunner 上的 ctx 契约 ───────────────────────────────


class TestThroughTheRealTaskRunner:
    """再走一遍**真** `TaskRunner`：证明"假 ctx"与 `TaskContext` 的契约是一致的。

    上面的用例用假 `ctx` 换取确定性；这一组用真 `TaskContext`（`pass_context=True`）
    钉住 `install_task` 只依赖 `progress(current, total, message)` 与 `cancelled`
    这两个成员 —— 名字写错一个、签名差一位，这里就红。
    """

    @staticmethod
    def _service(launcher: FakeLauncher) -> Tuple[InstallService, AppContext, RecordingObserver]:
        context = AppContext(config=FakeConfig())
        context.register_instance("launcher", launcher)
        service = InstallService(launcher=launcher)
        context.register(service)
        observer = RecordingObserver()
        service.set_observer(observer)
        return service, context, observer

    def test_progress_reaches_the_task_context(self) -> None:
        launcher = FakeLauncher()
        launcher.install_progress = [(1, 4, "下载中"), (4, 4, "完成")]
        service, context, observer = self._service(launcher)
        progress: List[Tuple[int, int, str]] = []

        try:
            handle = context.tasks.submit(
                service.install_task,
                version_id="1.20.4",
                loader="forge",
                pass_context=True,
                name="install",
                on_progress=lambda **kw: progress.append((kw["current"], kw["total"], kw["message"])),
            )
            assert handle.wait(5.0) is True
        finally:
            context.tasks.shutdown(wait=False)

        assert handle.result["state"] == RESULT_DONE
        assert progress == [(1, 4, ""), (4, 4, "")]
        assert observer.status_keys() == ["version_installing_loader", "version_installed"]

    def test_cancel_check_sees_the_real_cancel_event(self) -> None:
        """`Tasks.cancel()` → `ctx.cancelled` → 核心的 `cancel_check()`，整条链走通。"""
        entered = threading.Event()
        release = threading.Event()

        class SlowLauncher(FakeLauncher):
            """进到安装里就停住，等主线程先点取消 —— 这样断言没有竞态。"""

            seen_cancel: Any = None

            def install_version(self, version_id, mod_loader, *, on_progress=None, cancel_check=None):
                entered.set()
                release.wait(5.0)
                self.seen_cancel = cancel_check() if cancel_check is not None else None
                return (False, version_id)

        launcher = SlowLauncher()
        service, context, _observer = self._service(launcher)
        try:
            handle = context.tasks.submit(
                service.install_task, version_id="1.20.4", loader="forge", pass_context=True,
            )
            assert entered.wait(5.0) is True, "任务没进到安装里，后面的取消断言就没有意义"
            handle.cancel()
            release.set()
            assert handle.wait(5.0) is True
        finally:
            release.set()
            context.tasks.shutdown(wait=False)

        assert launcher.seen_cancel is True
        assert handle.result["state"] == RESULT_FAILED


# ─── 界面文案键的完整性 ────────────────────────────────────────


def has_emoji(text: str) -> bool:
    for char in text:
        code = ord(char)
        if 0x1F300 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF or code == 0xFE0F:
            return True
    return False


class TestMessageKeys:
    """服务发出的每个状态键都必须在 4 个语言文件里存在（否则界面显示裸键名）。"""

    #: `load_available` 与 `install_task` 走一遍会发出的全部键
    STATUS_KEYS = (
        "loading_available",
        "version_available_loaded",
        "version_available_failed",
        "version_id_required",
        "version_installing",
        "version_installing_loader",
        "version_installed",
        "version_install_failed",
        "version_install_cancelled",
    )

    #: 状态键 → 文案里必须带的占位符（名字要与 `InstallBridge._status_text` 的关键字一致）
    PLACEHOLDERS: Dict[str, Tuple[str, ...]] = {
        "loading_available": (),
        "version_available_loaded": ("release", "snapshot"),
        "version_available_failed": (),
        "version_id_required": (),
        "version_installing": ("version",),
        "version_installing_loader": ("version", "loader"),
        "version_installed": ("version",),
        "version_install_failed": ("version",),
        "version_install_cancelled": ("version",),
    }

    @staticmethod
    def _load(lang: str) -> Dict[str, Any]:
        path = REPO_ROOT / "ui" / "locales" / f"{lang}.json"
        return json.loads(path.read_text(encoding="utf-8"))

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_status_keys_exist_in_every_language(self, lang: str) -> None:
        data = self._load(lang)
        missing = [key for key in self.STATUS_KEYS if key not in data]
        assert missing == [], f"{lang} 缺键: {missing}"

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_loader_label_keys_exist_in_every_language(self, lang: str) -> None:
        # 9 个加载器名走同一套 i18n：少一个，下拉里那一项就显示成 `mod_loader_xxx`
        data = self._load(lang)
        missing = [key for key in LOADER_LABEL_KEYS.values() if key not in data]
        assert missing == [], f"{lang} 缺键: {missing}"

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_placeholders_match_the_bridge(self, lang: str) -> None:
        """占位符名字必须与桥传的关键字一致 —— 不一致时参数会**静默**丢掉。

        桥里写的是 `_("version_installing", version=…)`：文案要是写成 `{ver}`，
        既不报错也不显示版本号，用户看到的是一句缺了版本号的话。
        """
        data = self._load(lang)
        for key, names in self.PLACEHOLDERS.items():
            text = str(data.get(key, ""))
            for name in names:
                assert "{%s}" % name in text, f"{lang}.{key} 缺占位符 {{{name}}}: {text!r}"

    @pytest.mark.parametrize("lang", LANGUAGES)
    def test_no_message_carries_an_emoji(self, lang: str) -> None:
        """新界面禁止 emoji（项目 UI 规范）：这些键是阶段 3 新加的，必须干净。"""
        data = self._load(lang)
        dirty = [key for key in self.STATUS_KEYS if key in data and has_emoji(str(data[key]))]
        assert dirty == [], f"{lang} 里这些键带 emoji: {dirty}"
