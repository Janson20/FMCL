"""`app/bridges/modpack_bridge.py` 的回归守卫（阶段 3 任务 3.8，精简档）。

只钉桥自己负责的几条：搜索分页口径、文件认领与元数据、可选组件、客户端/服务器
安装入口、下载回填与世代号。服务层与核心层一律替身 —— 这一层不联网，也不重复测
`tests/test_modpack_service.py` 已经钉住的纯函数。
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

from app.bridges.modpack_bridge import (  # noqa: E402
    PAGE_SIZE,
    STATE_READY,
    VIEW_RESULTS,
    VIEW_VERSIONS,
    ModpackBridge,
)
from app.tasks import TaskRunner  # noqa: E402
from services import i18n_service
from services import modpack_service as svc  # noqa: E402
from services.browse_common import SearchOutcome  # noqa: E402

_APP = QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture(autouse=True, scope="module")
def _chinese_locale() -> Any:
    """整模块用中文语言文件，并在结束时还原（D-166 的教训：别污染后续模块）。"""
    saved_lang = i18n_service.get_current_language()
    saved = dict(i18n_service._translations)
    i18n_service.init_i18n("zh_CN")
    yield
    i18n_service._translations.clear()
    i18n_service._translations.update(saved)
    i18n_service._current_language = saved_lang

PACK_INFO = {
    "format": "mrpack",
    "name": "Demo Pack",
    "summary": "A demo",
    "minecraftVersion": "1.20.1",
    "optionalFiles": ["config/a.zip", "mods/b.jar"],
}


def pump(condition: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not condition():
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()
    return condition()


def make_hit(index: int) -> Dict[str, Any]:
    return {
        "project_id": f"pack-{index}",
        "title": f"Pack {index}",
        "description": "desc",
        "downloads": 1000 + index,
        "versions": ["1.20.1"],
        "icon_url": "",
    }


class FakeService:
    def __init__(self, pack_path: Path) -> None:
        self.pack_path = pack_path
        self.search_calls: List[tuple] = []
        self.ai_calls: List[tuple] = []
        self.load_calls: List[str] = []
        self.download_calls: List[tuple] = []
        self.install_calls: List[Dict[str, Any]] = []
        self.server_calls: List[Dict[str, Any]] = []
        self.achievements: List[str] = []
        self.hits = [make_hit(i) for i in range(25)]
        self.versions = [
            {
                "id": f"v{i}",
                "version_number": f"1.0.{i}",
                "date_published": f"2026-01-{(i % 28) + 1:02d}T00:00:00Z",
                "game_versions": ["1.20.1"] if i < 40 else ["1.19.4"],
                "files": [{"primary": True}],
            }
            for i in range(60)
        ]

    def search_modpacks(self, query: str, offset: int, limit: int) -> SearchOutcome:
        self.search_calls.append((query, offset, limit))
        page = self.hits[offset : offset + limit]
        return SearchOutcome(hits=page, total_hits=len(self.hits))

    def search_ai_modpacks(self, query: str, token: str):
        self.ai_calls.append((query, token))
        return list(self.hits), ["demo", "pack"]

    def fetch_modpack_versions(self, project_id: str):
        return list(self.versions)

    def build_sorted_version_list(self, versions, unknown_version_label):
        return svc.build_sorted_version_list(versions, unknown_version_label)

    def load_pack_info(self, callbacks, path: str):
        self.load_calls.append(path)
        return dict(PACK_INFO)

    def describe_pack_info(self, info):
        return svc.describe_pack_info(info)

    def format_label(self, pack_format: str):
        return svc.format_label(pack_format)

    def progress_snapshot(self, data):
        return svc.progress_snapshot(data)

    def call_install_entry(self, callbacks, path, optional_files, pack_format="mrpack", instance_name=None):
        self.install_calls.append(
            {
                "path": path,
                "optional": list(optional_files),
                "format": pack_format,
                "instance": instance_name,
            }
        )
        return callbacks["install_modpack"](
            path, optional_file_ids=optional_files or None, instance_name=instance_name
        )

    def call_server_install_entry(self, callbacks, path, optional_files, server_name):
        self.server_calls.append(
            {"path": path, "optional": list(optional_files), "server_name": server_name}
        )
        return callbacks["install_mrpack_server"](
            path, optional_files=optional_files, server_name=server_name
        )

    def download_modpack_version(self, project_id, version_data, status_callback=None):
        self.download_calls.append((project_id, version_data.get("id")))
        if status_callback:
            status_callback("downloading")
        return True, str(self.pack_path)

    def trigger_achievement(self, achievement_id: str) -> bool:
        self.achievements.append(achievement_id)
        return True


class FakeLauncher:
    def __init__(self) -> None:
        self._mp_progress: Optional[Dict[str, Any]] = None
        self.client_calls: List[tuple] = []
        self.server_calls: List[tuple] = []
        self.installed: List[str] = []

    def get_installed_version_ids(self) -> List[str]:
        return list(self.installed)

    def install_modpack(self, path: str, *, optional_file_ids=None, instance_name=None):
        self.client_calls.append((path, list(optional_file_ids or []), instance_name))
        self._mp_progress = {"phase": "detected", "format_name": "Modrinth", "overall": 0}
        time.sleep(0.3)
        self._mp_progress = {
            "phase": "parallel",
            "overall": 0.5,
            "status_text": "并行安装中",
            "mrpack": {"current": 1, "max": 2, "label": "包"},
            "vanilla": {"current": 1, "max": 2, "label": "原版"},
        }
        time.sleep(0.3)
        self._mp_progress = {"phase": "done", "overall": 1.0}
        return True, str(instance_name or "1.20.1-demo")

    def install_mrpack_server(self, path: str, optional_files=None, server_name=None):
        self.server_calls.append((path, list(optional_files or []), server_name))
        self._mp_progress = {"phase": "detected", "format_name": "Modrinth", "overall": 0}
        time.sleep(0.05)
        self._mp_progress = {"phase": "done", "overall": 1.0}
        return True, server_name or "demo-server"


class FakeContext:
    def __init__(self, service: Any, launcher: Any, tasks: Any) -> None:
        self._services = {"modpack": service, "launcher": launcher}
        self.config = SimpleNamespace(jdz_token="token", language="zh_CN")
        self.tasks = tasks

    def try_get(self, name: str) -> Any:
        return self._services.get(name)

    def shutdown(self) -> None:
        self.tasks.shutdown(wait=False)


@pytest.fixture()
def harness(tmp_path: Path) -> Any:
    pack = tmp_path / "demo.mrpack"
    pack.write_bytes(b"x")
    service = FakeService(pack)
    launcher = FakeLauncher()
    tasks = TaskRunner(max_workers=2, name="test-modpack")
    context = FakeContext(service, launcher, tasks)
    bridge = ModpackBridge()
    bridge.bind(context)
    statuses: List[tuple] = []
    notices: List[tuple] = []
    files: List[tuple] = []
    finished: List[tuple] = []
    bridge.statusMessage.connect(lambda text, level: statuses.append((text, level)))
    bridge.noticeRequested.connect(lambda text, level: notices.append((text, level)))
    bridge.fileReady.connect(lambda target, path: files.append((target, path)))
    bridge.installFinished.connect(lambda target, ok, result: finished.append((target, ok, result)))
    yield SimpleNamespace(
        service=service,
        launcher=launcher,
        context=context,
        bridge=bridge,
        pack=pack,
        statuses=statuses,
        notices=notices,
        files=files,
        finished=finished,
    )
    bridge.detach()
    context.shutdown()


def select_file(harness: Any, path: Optional[Path] = None) -> None:
    harness.bridge.setFilePath(str(path or harness.pack))
    assert pump(lambda: harness.bridge.fileState == STATE_READY), harness.bridge.fileState


# ═══════════════════════════════════════════════════════════════════
# 文件 / 元数据 / 组件
# ═══════════════════════════════════════════════════════════════════


def test_set_file_loads_metadata_and_components(harness: Any) -> None:
    select_file(harness)
    bridge = harness.bridge
    assert bridge.hasFile is True
    assert bridge.fileName == "demo.mrpack"
    assert bridge.packName == "Demo Pack"
    assert bridge.formatText == "Modrinth"
    assert bridge.mcVersionText == "Minecraft 1.20.1"
    assert [row["name"] for row in bridge.components] == ["config/a.zip", "mods/b.jar"]
    assert all(row["selectable"] for row in bridge.components)
    assert harness.service.load_calls == [str(harness.pack)]


def test_toggle_component_changes_selection(harness: Any) -> None:
    select_file(harness)
    bridge = harness.bridge
    bridge.toggleComponent("mods/b.jar")
    assert bridge.selectedComponents == ["mods/b.jar"]
    assert [row["selected"] for row in bridge.components] == [False, True]
    bridge.toggleComponent("mods/b.jar")
    assert bridge.selectedComponents == []


def test_activate_claims_downloaded_file(harness: Any) -> None:
    bridge = harness.bridge
    bridge._pending_files["client"] = str(harness.pack)
    bridge.activate("client")
    assert pump(lambda: bridge.fileState == STATE_READY)
    assert bridge.filePath == str(harness.pack)
    assert bridge.hasFile is True
    assert bridge._pending_files["client"] == ""


# ═══════════════════════════════════════════════════════════════════
# 安装
# ═══════════════════════════════════════════════════════════════════


def test_client_install_passes_optional_files_and_name(harness: Any) -> None:
    select_file(harness)
    bridge = harness.bridge
    bridge.toggleComponent("config/a.zip")
    bridge.beginInstallNaming()
    assert bridge.naming is True
    assert bridge.instanceName == "Demo Pack"
    assert bridge.instanceNameValid is True
    bridge.setInstanceName("My Pack")
    bridge.install()
    assert bridge.busy is True
    assert pump(lambda: bridge.installState == "done", 8.0)
    assert harness.service.install_calls == [
        {
            "path": str(harness.pack),
            "optional": ["config/a.zip"],
            "format": "mrpack",
            "instance": "My Pack",
        }
    ]
    assert harness.launcher.client_calls == [(str(harness.pack), ["config/a.zip"], "My Pack")]
    assert bridge.resultLevel == "success"
    assert "My Pack" in bridge.resultText
    assert harness.finished == [("client", True, "My Pack")]
    assert "modder_lazy" in harness.service.achievements


def test_instance_name_conflict_blocks_install(harness: Any) -> None:
    select_file(harness)
    bridge = harness.bridge
    harness.launcher.installed = ["demo pack"]
    bridge.beginInstallNaming()
    assert bridge.naming is True
    assert bridge.instanceName == "Demo Pack"
    assert bridge.instanceNameValid is False
    assert "Demo Pack" in bridge.instanceNameError
    bridge.install()
    assert not harness.service.install_calls
    assert bridge.installState == ""


def test_invalid_instance_name_blocks_install(harness: Any) -> None:
    select_file(harness)
    bridge = harness.bridge
    bridge.beginInstallNaming()
    bridge.setInstanceName("bad/name")
    assert bridge.instanceNameValid is False
    assert bridge.instanceNameError
    bridge.install()
    assert not harness.service.install_calls


def test_naming_back_hides_the_step(harness: Any) -> None:
    select_file(harness)
    bridge = harness.bridge
    bridge.beginInstallNaming()
    bridge.backToInfo()
    assert bridge.naming is False


def test_progress_snapshot_is_exposed_during_install(harness: Any) -> None:
    select_file(harness)
    bridge = harness.bridge
    bridge.install()
    assert pump(lambda: "整合包" in bridge.mrpackText, 4.0), bridge.mrpackText
    assert bridge.progress > 0
    assert pump(lambda: bridge.installState == "done", 8.0)


def test_server_install_uses_server_entry_and_name(harness: Any) -> None:
    bridge = harness.bridge
    bridge.activate("server")
    select_file(harness)
    bridge.setServerName("my-server")
    bridge.install()
    assert pump(lambda: bridge.installState == "done", 8.0)
    assert harness.service.server_calls == [
        {"path": str(harness.pack), "optional": [], "server_name": "my-server"}
    ]
    assert harness.finished == [("server", True, "my-server")]
    assert "server_modpack_server" in harness.service.achievements


def test_install_without_file_is_refused(harness: Any) -> None:
    harness.bridge.install()
    assert harness.notices
    assert not harness.service.install_calls
    assert not harness.service.server_calls


# ═══════════════════════════════════════════════════════════════════
# 搜索 / 分页
# ═══════════════════════════════════════════════════════════════════


def test_normal_search_requests_backend_pages(harness: Any) -> None:
    bridge = harness.bridge
    bridge.setQuery("demo")
    bridge.search()
    assert pump(lambda: bridge.loadState == STATE_READY)
    assert harness.service.search_calls == [("demo", 0, PAGE_SIZE)]
    assert bridge.pageCount == 3
    assert bridge.total == 25
    assert len(bridge.items) == 10

    bridge.goto(2)
    assert pump(lambda: bridge.page == 2 and not bridge.loadState == "loading")
    assert harness.service.search_calls[-1] == ("demo", 10, PAGE_SIZE)
    assert len(bridge.items) == 10
    assert bridge.items[0]["title"] == "Pack 10"
    assert bridge.rangeStart == 11 and bridge.rangeEnd == 20


def test_ai_search_paginates_locally(harness: Any) -> None:
    bridge = harness.bridge
    bridge.setQuery("demo")
    bridge.aiSearch()
    assert pump(lambda: bridge.loadState == STATE_READY)
    calls = len(harness.service.ai_calls)
    assert bridge.pageCount == 3
    assert len(bridge.items) == 10
    bridge.goto(3)
    assert bridge.page == 3
    assert len(bridge.items) == 5
    assert len(harness.service.ai_calls) == calls


# ═══════════════════════════════════════════════════════════════════
# 版本视图 / 下载
# ═══════════════════════════════════════════════════════════════════


def test_open_versions_groups_and_loads_more(harness: Any) -> None:
    bridge = harness.bridge
    bridge.openVersions("pack-1", "Demo Pack")
    assert pump(lambda: bridge.versionsState == STATE_READY)
    assert bridge.browserView == VIEW_VERSIONS
    assert bridge.versionsTotal == 60
    assert bridge.versionsShown == 50
    assert bridge.versionsCanLoadMore is True
    assert any(row["kind"] == "header" for row in bridge.versionRows)
    bridge.loadMoreVersions()
    assert bridge.versionsShown == bridge.versionsRowTotal
    assert bridge.versionsShown > 60  # 60 条版本 + 两个分组头
    assert bridge.versionsCanLoadMore is False
    bridge.backToResults()
    assert bridge.browserView == VIEW_RESULTS


def test_download_stores_pending_file_and_emits(harness: Any) -> None:
    bridge = harness.bridge
    bridge.activateBrowser("server")
    bridge.openVersions("pack-1", "Demo Pack")
    assert pump(lambda: bridge.versionsState == STATE_READY)
    bridge.downloadVersion("v1")
    assert pump(lambda: not bridge.downloading)
    assert harness.service.download_calls == [("pack-1", "v1")]
    assert bridge._pending_files["server"] == str(harness.pack)
    assert harness.files == [("server", str(harness.pack))]
