"""`app/bridges/mod_browser_bridge.py` 的回归守卫（阶段 3 任务 3.7，精简档）。

只钉"桥自己负责的那几条"：

* 双源搜索的结果怎么落成卡片 / 分页（**本地切片**，10 条一页）；
* **世代号**：晚到的旧结果整批丢弃（D-93 的桥侧同形）；
* 三个标签页的状态互不污染；
* AI 搜索的三种边界（无 token / 空查询 / 正常）；
* 详情页：项目 + 版本 + 首版依赖；
* 安装：模组缺筛选的拦截、目录选择的挂起与继续、成功/失败文案。

服务层（搜索编排、安装、版本解析）与核心层（网络）**一律替身** —— 这一层不该联网，
也不该重复测 `tests/test_mod_browser_staleness.py` 已经钉住的服务侧世代号。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.bridges.mod_browser_bridge import (  # noqa: E402
    PAGE_SIZE,
    TAB_MODS,
    TAB_RESOURCE_PACKS,
    TAB_SHADERS,
    ModBrowserBridge,
)
from app.tasks import TaskRunner  # noqa: E402
from services.browse_common import SearchOutcome  # noqa: E402

_APP = QGuiApplication.instance() or QGuiApplication([])


def pump(condition: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    """驱动主线程事件循环直到条件成立（跨线程投递没有它就永远落不了地）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not condition():
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()
    return condition()


def hit(project_id: str, title: str, *, downloads: int = 100, source: str = "modrinth") -> Dict[str, Any]:
    return {
        "project_id": project_id,
        "title": title,
        "description": "desc",
        "author": "me",
        "downloads": downloads,
        "icon_url": "",
        "source": source,
        "game_versions": ["1.20.1", "1.20.2"],
        "loaders": ["fabric"],
    }


class FakeService:
    """`ModBrowserService` 的最小替身：只实现桥会调的那几个入口。"""

    def __init__(self) -> None:
        self.mc = Path(".")   #: 由 fixture 改成 tmp_path 下的目录，避免测试往仓库写
        self.search_calls: List[tuple] = []
        self.ai_calls: List[tuple] = []
        self.install_calls: List[Dict[str, Any]] = []
        self.achievements: List[int] = []
        self.detail_calls: List[tuple] = []
        self.deps_calls: List[tuple] = []
        #: 让搜索可控：默认返回 N 条
        self.hits: List[Dict[str, Any]] = [hit(f"p{i}", f"Mod {i}") for i in range(25)]
        self.total = len(self.hits)
        self.search_error: Optional[Exception] = None
        self.install_result: tuple = (True, "ok", ["a.jar"])
        self.versions: List[Dict[str, Any]] = [
            {"id": "v2", "name": "2.0", "game_versions": ["1.20.1"], "loaders": ["fabric"],
             "filename": "b.jar", "size": 2048, "dependencies": [{"project_id": "dep1", "kind": "required"}]},
            {"id": "v1", "name": "1.0", "game_versions": ["1.20.1"], "loaders": ["fabric"],
             "filename": "a.jar", "size": 1024, "dependencies": []},
        ]

    # ── 搜索 ──
    def search_client_tab(self, tab: str, query: str, game_version, mod_loader) -> Optional[SearchOutcome]:
        self.search_calls.append((tab, query, game_version, mod_loader))
        if self.search_error is not None:
            raise self.search_error
        return SearchOutcome(hits=list(self.hits), total_hits=self.total, sources={})

    def search_ai_merged_tab(self, tab: str, query: str, token: str, game_version, mod_loader) -> SearchOutcome:
        self.ai_calls.append((tab, query, token, game_version, mod_loader))
        return SearchOutcome(hits=list(self.hits[:3]), total_hits=3, sources={}, keywords=["kw1", "kw2"])

    # ── 详情 ──
    def project_detail(self, source: str, project_id: str) -> Dict[str, Any]:
        self.detail_calls.append((source, project_id))
        return {"source": source, "project_id": project_id, "title": "Fancy", "description": "d",
                "author": "a", "downloads": 42, "icon_url": "", "slug": "fancy"}

    def project_versions(self, source: str, project_id: str, game_version=None, mod_loader=None, limit=100):
        return list(self.versions)

    def version_dependency_preview(self, source, project_id, version_id, game_version=None, mod_loader=None,
                                   name_limit=8):
        self.deps_calls.append((source, project_id, version_id))
        return [{"project_id": "dep1", "kind": "required", "name": "Dep One"}]

    # ── 安装 ──
    def resolve_install_dir(self, callbacks, tab_key, default_game_version, version_id, get_v, get_l) -> str:
        return str(self.mc / tab_key)

    def install_dir_for(self, mc_dir, version_id: str, tab_key: str) -> Path:
        #: 版本隔离目录（与 services.install_dir_for 同形；测试只关心桥有没有用它）
        return Path(mc_dir) / "versions" / str(version_id) / tab_key

    def install_chosen_version(self, source, tab_key, project_id, title, version_id, game_version, mod_loader,
                               target_dir, status_callback=None):
        self.install_calls.append({"source": source, "tab": tab_key, "project_id": project_id,
                                   "version_id": version_id, "game_version": game_version,
                                   "loader": mod_loader, "target_dir": target_dir})
        if status_callback:
            status_callback("正在下载 x.jar...")
        return self.install_result

    # ── 杂项 ──
    def describe_version(self, version_id: str) -> Dict[str, Any]:
        return {"game_version": "1.20.1", "loader": "fabric"}

    @staticmethod
    def compat_loader(loader: Optional[str]) -> Optional[str]:
        return {"legacyfabric": "fabric", "cleanroom": "forge"}.get(loader or "", loader)

    @staticmethod
    def game_version_options() -> List[str]:
        return ["1.20.1", "1.20.2"]

    def trigger_achievements(self, count: int) -> None:
        self.achievements.append(count)


class FakeLauncher:
    def __init__(self, mc: str = "D:/mc", token: Optional[str] = None,
                 versions: Optional[List[str]] = None) -> None:
        self.mc = mc
        self.token = token
        self.versions = ["1.20.1-fabric", "1.20.4-forge"] if versions is None else list(versions)

    def get_minecraft_dir(self) -> str:
        return self.mc

    def get_installed_versions(self) -> List[Dict[str, Any]]:
        return [{"id": v} for v in self.versions]

    def get_installed_version_ids(self) -> List[str]:
        return list(self.versions)

    def get_jdz_token(self) -> Optional[str]:
        return self.token


class FakeContext:
    def __init__(self, service: Any, launcher: Any = None, tasks: Any = None) -> None:
        self._services = {"mod_browser": service}
        if launcher is not None:
            self._services["launcher"] = launcher
        self.config = SimpleNamespace(jdz_token=None, last_launched_version="")
        self.tasks = tasks or TaskRunner(max_workers=2, name="test-mod-browser")

    def try_get(self, name: str) -> Any:
        return self._services.get(name)

    def shutdown(self) -> None:
        if self.tasks is not None:
            self.tasks.shutdown(wait=False)


@pytest.fixture()
def harness(tmp_path: Path) -> Any:
    service = FakeService()
    service.mc = tmp_path / "mc"
    launcher = FakeLauncher(mc=str(service.mc))
    context = FakeContext(service, launcher)
    bridge = ModBrowserBridge()
    bridge.bind(context)
    statuses: List[tuple] = []
    notices: List[tuple] = []
    directories: List[tuple] = []
    bridge.statusMessage.connect(lambda t, lv: statuses.append((t, lv)))
    bridge.noticeRequested.connect(lambda t, lv: notices.append((t, lv)))
    bridge.directoryRequested.connect(lambda k, d: directories.append((k, d)))
    yield SimpleNamespace(
        service=service, launcher=launcher, context=context, bridge=bridge,
        statuses=statuses, notices=notices, directories=directories,
    )
    bridge.detach()
    context.shutdown()


def start_search(harness: Any, tab: str, query: str = "", game_version: str = "", loader: str = "") -> None:
    """按"选好筛选 → 点搜索"的顺序驱动一次搜索（页面上就是这么走的）。

    合并成单页之后没有 `activateResults` 了：浏览页把筛选交给桥，再调 `search()`
    或 `aiSearch()` —— 本函数就是那两个动作的简写。
    """
    bridge = harness.bridge
    bridge.setTab(tab)
    bridge.setQuery(query)
    bridge.setGameVersion(game_version)
    bridge.setLoader(loader)
    bridge.search()


# ═══════════════════════════════════════════════════════════════════
# 搜索结果 / 分页
# ═══════════════════════════════════════════════════════════════════


def test_search_fills_first_page_and_paginates_locally(harness: Any) -> None:
    bridge = harness.bridge
    start_search(harness, TAB_MODS, "sodium", "", "fabric")
    assert pump(lambda: bridge.loadState == "ready"), bridge.loadState

    assert len(bridge.items) == PAGE_SIZE
    assert bridge.pageCount == 3          # 25 条 / 10 条一页
    assert bridge.rangeStart == 1 and bridge.rangeEnd == 10
    assert bridge.total == 25
    #: 卡片上已装饰好显示文本
    assert bridge.items[0]["downloads_text"]
    assert bridge.items[0]["source_name"] == "Modrinth"

    #: 翻页是**本地切片**，不再发起搜索
    calls = len(harness.service.search_calls)
    bridge.goto(3)
    assert bridge.page == 3
    assert len(bridge.items) == 5
    assert bridge.rangeStart == 21 and bridge.rangeEnd == 25
    assert len(harness.service.search_calls) == calls


def test_search_result_reports_total_and_status(harness: Any) -> None:
    bridge = harness.bridge
    start_search(harness, TAB_MODS, "", "", "")
    assert pump(lambda: bridge.loadState == "ready")
    assert bridge.total == 25
    assert harness.statuses, "搜索成功后应该有状态栏文案"


def test_stale_result_is_dropped(harness: Any) -> None:
    """D-93 的桥侧同形：世代号对不上的结果整批丢弃（连缓存都不写）。"""
    bridge = harness.bridge
    start_search(harness, TAB_MODS, "a", "", "fabric")
    assert pump(lambda: bridge.loadState == "ready")
    state = bridge._states[TAB_MODS]
    current = state["seq"]

    #: 伪造一条"上一代"的结果（更旧的请求晚到）
    bridge._apply_search(
        {
            "kind": "search",
            "tab": TAB_MODS,
            "seq": current - 1,
            "ai": False,
            "filter_key": ("old", None, None),
            "result": SearchOutcome(hits=[hit("old", "Old")], total_hits=1),
            "error": None,
        }
    )
    assert len(state["hits"]) == 25, "旧世代的结果覆盖了新结果"
    assert bridge.items[0]["title"] != "Old"


def test_tabs_keep_their_own_state(harness: Any) -> None:
    bridge = harness.bridge
    start_search(harness, TAB_MODS, "a", "", "fabric")
    assert pump(lambda: bridge.loadState == "ready")
    mods_titles = [i["title"] for i in bridge.items]

    start_search(harness, TAB_SHADERS, "b", "", "")
    assert pump(lambda: bridge.loadState == "ready")
    assert bridge.tab == TAB_SHADERS
    assert bridge.query == "b"

    bridge.setTab(TAB_MODS)
    assert bridge.page == 1
    assert [i["title"] for i in bridge.items] == mods_titles


# ═══════════════════════════════════════════════════════════════════
# AI 搜索
# ═══════════════════════════════════════════════════════════════════


def test_ai_without_token_only_notifies(harness: Any) -> None:
    bridge = harness.bridge
    start_search(harness, TAB_MODS, "optimize me", "", "fabric")
    assert pump(lambda: bridge.loadState == "ready")
    harness.notices.clear()
    harness.service.ai_calls.clear()

    bridge.aiSearch()
    assert not harness.service.ai_calls, "没有 token 不该发起 AI 搜索"
    assert harness.notices, "没有 token 应该给一条提示"


def test_ai_with_token_reports_keywords(harness: Any) -> None:
    harness.launcher.token = "tok"
    bridge = harness.bridge
    start_search(harness, TAB_MODS, "optimize me", "", "fabric")
    assert pump(lambda: bridge.loadState == "ready")

    bridge.aiSearch()
    assert pump(lambda: len(bridge.aiKeywords) == 2)
    assert list(bridge.aiKeywords) == ["kw1", "kw2"]
    assert harness.service.ai_calls[-1][2] == "tok"
    assert len(bridge.items) == 3


def test_ai_with_empty_query_falls_back_to_normal_search(harness: Any) -> None:
    harness.launcher.token = "tok"
    bridge = harness.bridge
    start_search(harness, TAB_MODS, "", "", "fabric")
    assert pump(lambda: bridge.loadState == "ready")
    harness.service.ai_calls.clear()

    bridge.aiSearch()
    assert pump(lambda: bridge.loadState == "ready")
    assert not harness.service.ai_calls, "空查询的 AI 搜索应退化成普通搜索"


# ═══════════════════════════════════════════════════════════════════
# 详情页
# ═══════════════════════════════════════════════════════════════════


def test_detail_loads_project_versions_and_first_deps(harness: Any) -> None:
    bridge = harness.bridge
    bridge.activate("", "")
    bridge.openDetail("modrinth", "proj1", TAB_MODS, "Fancy")
    assert pump(lambda: bridge.detailState == "ready"), bridge.detailState
    assert bridge.detailTitle == "Fancy"
    assert len(bridge.detailVersions) == 2
    assert bridge.detailSelectedId == "v2", "详情页默认选中第一个版本"
    assert pump(lambda: len(bridge.detailDeps) == 1)
    dep = bridge.detailDeps[0]
    assert dep["project_id"] == "dep1" and dep["kind"] == "required"
    assert dep["kindKey"] == "browse_dep_required"


def test_detail_error_when_project_missing(harness: Any) -> None:
    bridge = harness.bridge
    harness.service.project_detail = lambda source, pid: {}  # type: ignore[assignment]
    bridge.activate("", "")
    bridge.openDetail("modrinth", "nope", TAB_MODS, "")
    assert pump(lambda: bridge.detailState == "error")


# ═══════════════════════════════════════════════════════════════════
# 安装
# ═══════════════════════════════════════════════════════════════════


def test_install_mods_requires_version_and_loader(harness: Any) -> None:
    bridge = harness.bridge
    start_search(harness, TAB_MODS, "", "", "")   # 三个筛选都为空
    assert pump(lambda: bridge.loadState in ("ready", "empty", "error"))
    harness.service.install_calls.clear()

    bridge.installFromCard("modrinth", "p1", "Mod 1")
    assert not harness.service.install_calls
    assert not harness.directories, "缺筛选时不该先弹目录框"
    assert harness.statuses, "应该提示需要先选版本/加载器"


def test_install_asks_directory_then_installs(harness: Any) -> None:
    bridge = harness.bridge
    start_search(harness, TAB_MODS, "q", "1.20.1", "fabric")
    assert pump(lambda: bridge.loadState == "ready")

    bridge.installFromCard("modrinth", "p1", "Mod 1")
    assert harness.directories, "安装前应该请页面弹目录选择框"
    kind, default_dir = harness.directories[-1]
    assert kind == TAB_MODS and default_dir

    bridge.provideDirectory("D:/mc/mods")
    assert pump(lambda: len(harness.service.install_calls) == 1)
    call = harness.service.install_calls[0]
    assert call["target_dir"] == "D:/mc/mods"
    assert call["project_id"] == "p1" and call["version_id"] is None
    assert pump(lambda: not bridge.busy)
    assert harness.service.achievements == [1], "安装成功后要触发成就"


def test_install_cancel_reports_cancelled(harness: Any) -> None:
    bridge = harness.bridge
    start_search(harness, TAB_MODS, "q", "1.20.1", "fabric")
    assert pump(lambda: bridge.loadState == "ready")
    bridge.installFromCard("modrinth", "p1", "Mod 1")
    harness.statuses.clear()

    bridge.cancelDirectory()
    assert harness.statuses, "取消目录选择应该给文案"
    assert not harness.service.install_calls


def test_install_target_is_the_browsed_version_folder(harness: Any) -> None:
    """从某个版本进来时，目录选择框的默认位置是**那个版本**的资源目录。

    2026-10-23 用户反馈「没有自动定位到当前浏览版本的资源文件夹」：之前
    `resolve_install_dir` 依赖已安装实例列表（核心没起来时为空），于是退到相对的
    `mods`。现在来源版本已知就直接用它（隔离版本 → `versions/<id>/<sub>`）。
    """
    bridge = harness.bridge
    bridge.activate("1.20.4-forge", "forge")
    start_search(harness, TAB_MODS, "", "1.20.4", "forge")
    assert pump(lambda: bridge.loadState == "ready")
    harness.directories.clear()

    bridge.installFromCard("modrinth", "p1", "Mod 1")
    assert harness.directories, "应该请求页面弹目录选择框"
    _kind, default_dir = harness.directories[-1][0], harness.directories[-1][1]
    assert default_dir == str(harness.service.mc / "versions" / "1.20.4-forge" / "mods"), default_dir
    assert Path(default_dir).is_dir(), "弹框前目录应该已经建出来（否则选择框停在上级）"


def test_install_failure_reports_error(harness: Any) -> None:
    harness.service.install_result = (False, "boom", [])
    bridge = harness.bridge
    start_search(harness, TAB_MODS, "q", "1.20.1", "fabric")
    assert pump(lambda: bridge.loadState == "ready")
    bridge.installFromCard("modrinth", "p1", "Mod 1")
    bridge.provideDirectory("D:/mc/mods")

    assert pump(lambda: not bridge.busy)
    assert harness.notices, "安装失败应该有一条提示"


def test_install_selected_uses_chosen_version(harness: Any) -> None:
    bridge = harness.bridge
    bridge.activate("1.20.1-fabric", "fabric")
    bridge.openDetail("modrinth", "proj1", TAB_MODS, "Fancy")
    assert pump(lambda: bridge.detailState == "ready")
    bridge.selectVersion("v1")
    assert pump(lambda: bridge.detailSelectedId == "v1")

    bridge.installSelected()
    assert harness.directories, "详情页安装同样先问目录"
    bridge.provideDirectory("D:/mc/mods")
    assert pump(lambda: len(harness.service.install_calls) == 1)
    assert harness.service.install_calls[0]["version_id"] == "v1"


# ═══════════════════════════════════════════════════════════════════
# 上下文
# ═══════════════════════════════════════════════════════════════════


def test_activate_derives_default_filters_from_version(harness: Any) -> None:
    bridge = harness.bridge
    bridge.activate("1.20.1-fabric", "fabric")
    assert bridge.versionId == "1.20.1-fabric"
    assert bridge.defaultGameVersion == "1.20.1"
    assert bridge.gameVersion == "1.20.1", "筛选默认值取来源版本的游戏版本"
    assert bridge.loader == "fabric"


def test_activate_without_version_falls_back_to_last_launched(harness: Any) -> None:
    """路由没给版本时筛选**自动填充**（用户 2026-10-23 反馈）。

    顺序与资源页一致：最近使用 → 第一个已安装。这里验"最近使用"那条。
    """
    harness.context.config.last_launched_version = "1.20.1-fabric"
    bridge = harness.bridge
    bridge.activate("", "")
    assert bridge.versionId == "", "浏览全部资源时路由版本仍是空串（安装目录匹配用）"
    assert bridge.defaultGameVersion == "1.20.1", "默认游戏版本取最近使用版本"
    assert bridge.gameVersion == "1.20.1"
    assert bridge.loader == "fabric"


def test_activate_without_version_falls_back_to_first_installed(harness: Any) -> None:
    harness.context.config.last_launched_version = "9.9.9-不存在的版本"
    bridge = harness.bridge
    bridge.activate("", "")
    assert bridge.gameVersion == "1.20.1", "最近使用不在已安装列表里时退到第一个已安装版本"
    assert bridge.loader == "fabric"


def test_activate_all_versions_keeps_filters_empty_when_nothing_installed(harness: Any) -> None:
    harness.launcher.versions = []
    bridge = harness.bridge
    bridge.activate("", "")
    assert bridge.versionId == ""
    assert bridge.gameVersion == "", "一个版本都没装时筛选保持「全部」"
    assert bridge.loader == ""


def test_version_choices_are_loaded_and_cached(harness: Any) -> None:
    bridge = harness.bridge
    bridge.activate("", "")
    bridge.loadVersionChoices()
    assert pump(lambda: len(bridge.gameVersionChoices) == 3)  # 全部版本 + 2 个候选
    assert bridge.gameVersionChoices[0]["value"] == ""
