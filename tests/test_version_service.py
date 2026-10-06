"""`services/version_service.py` 的永久回归守卫（阶段 3 任务 3.2）。

这一层守的是**行为**：版本列表怎么显示（B-01）、重命名/删除的交互顺序与失败文案
（B-02）、搜索排序（B-19）、详情字段（B-20）、打开目录（B-21）、校验（B-22）。
旧实现长在 `ui/app_handlers.py` 的 Tk 窗口类上（只能手点验证），现在长在服务上，
所以每条规则都要有自动化判据。

测试里**不碰真的文件系统**（除个别 tmp_path 用例）**也不弹真对话框**：
`FakeLauncher` / `FakePort` / `FakeConfig` 就是核心、界面端口、配置的替身。
"""

from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.context import AppContext  # noqa: E402
from services.version_service import (  # noqa: E402
    MAX_INVALID_REPORTED,
    SORT_LOADER,
    SORT_NAME,
    SORT_VANILLA,
    VersionService,
    build_display_text,
    row_from_info,
)
from version_utils import InstanceInfo  # noqa: E402

# ─── 替身 ──────────────────────────────────────────────────────


class FakeLauncher:
    """`MinecraftLauncher` 的替身（只实现服务用到的那几个成员）。"""

    def __init__(self, infos: Optional[List[InstanceInfo]] = None) -> None:
        self.infos = list(infos or [])
        self.calls: List[Tuple[Any, ...]] = []
        self.invalidated = 0
        self.rename_result: Tuple[bool, str] = (True, "ok")
        self.remove_result: Tuple[bool, str] = (True, "1.20.4")
        self.verify_result: Dict[str, Any] = {"total": 3, "valid": 3, "invalid": []}
        self.raise_on_scan = False
        self.instances: Dict[str, InstanceInfo] = {}

    def get_installed_versions(self) -> List[InstanceInfo]:
        self.calls.append(("get_installed_versions",))
        if self.raise_on_scan:
            raise RuntimeError("扫描炸了")
        return list(self.infos)

    def get_instance_info(self, version_id: str) -> Optional[InstanceInfo]:
        self.calls.append(("get_instance_info", version_id))
        return self.instances.get(version_id)

    def invalidate_instance_cache(self) -> None:
        self.invalidated += 1

    def rename_instance(self, old: str, new: str) -> Tuple[bool, str]:
        self.calls.append(("rename_instance", old, new))
        return self.rename_result

    def remove_version(self, version_id: str) -> Tuple[bool, str]:
        self.calls.append(("remove_version", version_id))
        return self.remove_result

    def verify_installed_version(self, version_id: str) -> Dict[str, Any]:
        self.calls.append(("verify_installed_version", version_id))
        return dict(self.verify_result)


class FakeConfig:
    """`Config` 的替身（服务只用到这四个成员）。"""

    def __init__(self, root: Optional[Path] = None, **kwargs: Any) -> None:
        self.minecraft_dir = Path(kwargs.get("minecraft_dir", root or Path(".minecraft")))
        self._versions_dir = Path(kwargs.get("versions_dir", self.minecraft_dir / "versions"))
        self.last_launched_version = kwargs.get("last_launched_version", "")
        self.saved = 0

    def get_versions_dir(self) -> Path:
        return self._versions_dir

    def save_config(self) -> None:
        self.saved += 1


class FakePort:
    """`UIPort` 的替身：把两次问答脚本化，并记录调用顺序。"""

    def __init__(self, text: Optional[str] = None, confirm: bool = True) -> None:
        self.text = text
        self.confirm_answer = confirm
        self.calls: List[Tuple[Any, ...]] = []

    def ask_text(self, title: str, prompt: str, initial: str = "", password: bool = False, **kw: Any) -> Optional[str]:
        self.calls.append(("ask_text", title, prompt, initial))
        return self.text

    def confirm(self, title: str, message: str, default: bool = False, **kw: Any) -> bool:
        self.calls.append(("confirm", title, message, default))
        return self.confirm_answer

    def is_available(self) -> bool:
        return True


class FakeResource:
    """`ResourceService` 的替身（只实现详情用到的目录解析）。"""

    def __init__(self, mods_dir: Path) -> None:
        self.mods_dir = mods_dir
        self.calls: List[Tuple[Any, ...]] = []

    def resource_dir(self, version_id: str, mc_dir: Path, resource_type: str) -> Path:
        self.calls.append(("resource_dir", version_id, str(mc_dir), resource_type))
        return self.mods_dir


class RecordingObserver:
    """观察者替身：把三类回调按顺序记下来。"""

    def __init__(self, boom: bool = False) -> None:
        self.events: List[Tuple[Any, ...]] = []
        self.boom = boom

    def on_versions(self, rows: List[Dict[str, Any]], error: str) -> None:
        self.events.append(("versions", rows, error))
        if self.boom:
            raise RuntimeError("观察者炸了")

    def on_status(self, key: str, level: str, params: Dict[str, Any]) -> None:
        self.events.append(("status", key, level, params))

    def on_result(self, kind: str, ok: bool, data: Dict[str, Any]) -> None:
        self.events.append(("result", kind, ok, data))

    # ── 断言助手 ──
    def status_keys(self) -> List[str]:
        return [event[1] for event in self.events if event[0] == "status"]

    def status_for(self, key: str) -> Optional[Tuple[str, Dict[str, Any]]]:
        for event in self.events:
            if event[0] == "status" and event[1] == key:
                return event[2], event[3]
        return None

    def results(self) -> List[Tuple[str, bool, Dict[str, Any]]]:
        return [(event[1], event[2], event[3]) for event in self.events if event[0] == "result"]

    def versions(self) -> List[Tuple[List[Dict[str, Any]], str]]:
        return [(event[1], event[2]) for event in self.events if event[0] == "versions"]


def make_service(
    launcher: Optional[FakeLauncher] = None,
    port: Optional[FakePort] = None,
    config: Optional[FakeConfig] = None,
    resource: Any = None,
    observer: Optional[RecordingObserver] = None,
) -> Tuple[VersionService, FakeLauncher, FakePort, FakeConfig, RecordingObserver]:
    """按**生产取数路径**装一个服务：真 `AppContext` + 注入的假替身。"""
    fake = launcher or FakeLauncher()
    fake_port = port or FakePort()
    fake_config = config or FakeConfig()
    ctx = AppContext(config=fake_config, ui=fake_port)
    ctx.register_instance("launcher", fake)
    if resource is not None:
        ctx.register_instance("resource", resource)
    service = VersionService(launcher=fake, ui_port=fake_port)
    ctx.register(service)
    rec = observer or RecordingObserver()
    service.set_observer(rec)
    return service, fake, fake_port, fake_config, rec


def info(name: str, **kwargs: Any) -> InstanceInfo:
    return InstanceInfo(folder_name=name, **kwargs)


# ─── B-01 列表 ─────────────────────────────────────────────────


class TestDisplayText:
    def test_loader_with_version(self) -> None:
        text = build_display_text(info("1.20.4-forge-49.0.26", loader_type="forge", loader_version="49.0.26"))
        assert text == "1.20.4-forge-49.0.26 [Forge 49.0.26]"

    def test_loader_without_version(self) -> None:
        assert build_display_text(info("1.20.4-forge", loader_type="forge")) == "1.20.4-forge [Forge]"

    def test_loader_version_unknown_is_not_shown(self) -> None:
        text = build_display_text(info("v", loader_type="fabric", loader_version="Unknown"))
        assert text == "v [Fabric]"

    def test_vanilla_uses_two_spaces(self) -> None:
        assert build_display_text(info("1.20.4", vanilla_name="1.20.4")) == "1.20.4  (1.20.4)"

    def test_unknown_vanilla_is_not_shown(self) -> None:
        assert build_display_text(info("weird", vanilla_name="Unknown")) == "weird"

    def test_row_shape(self) -> None:
        row = row_from_info(info("1.20.4", vanilla_name="1.20.4", java_major=17))
        assert row["id"] == "1.20.4"
        assert row["display"] == "1.20.4  (1.20.4)"
        assert row["hasLoader"] is False
        assert row["javaMajor"] == 17


class TestLoad:
    def test_load_publishes_rows_and_count(self) -> None:
        launcher = FakeLauncher([info("1.20.4", vanilla_name="1.20.4"), info("1.19.2", vanilla_name="1.19.2")])
        service, _fake, _port, _config, observer = make_service(launcher)

        handle = service.load()
        assert handle.wait(5.0) is True

        keys = observer.status_keys()
        assert keys[0] == "version_loading_installed"
        assert keys[-1] == "version_loaded"
        assert observer.status_for("version_loaded")[1] == {"count": 2}

        rows, error = observer.versions()[-1]
        assert error == ""
        assert [row["id"] for row in rows] == ["1.20.4", "1.19.2"]
        assert [row["id"] for row in service.rows] == ["1.20.4", "1.19.2"]

    def test_force_invalidates_core_cache(self) -> None:
        service, fake, _port, _config, _observer = make_service()
        assert service.load(force=True).wait(5.0) is True
        assert fake.invalidated == 1

    def test_plain_load_keeps_cache(self) -> None:
        service, fake, _port, _config, _observer = make_service()
        assert service.load().wait(5.0) is True
        assert fake.invalidated == 0

    def test_scan_failure_becomes_a_status_not_an_exception(self) -> None:
        launcher = FakeLauncher()
        launcher.raise_on_scan = True
        service, _fake, _port, _config, observer = make_service(launcher)

        assert service.load().wait(5.0) is True
        assert service.rows == []
        assert observer.status_for("version_load_failed")[1] == {"error": "扫描炸了"}
        assert observer.versions()[-1] == ([], "扫描炸了")

    def test_missing_launcher_degrades_to_a_folder_scan(self, tmp_path: Path) -> None:
        """核心不可用时**降级**为按目录名列举，而不是把整页判成错误。

        实测背景：视觉探针的装配不跑启动链条（`start_startup=False`），核心永远不在
        上下文里 —— 报错的话版本页会一直显示"出错"（曾经就是这样），而磁盘上的版本
        其实是看得见的。降级之后至少能列出目录名，元数据等链条跑完再补齐。
        """
        mc = tmp_path / ".minecraft"
        (mc / "versions" / "1.20.4").mkdir(parents=True)
        (mc / "versions" / "1.19.2-forge").mkdir(parents=True)

        service = VersionService(launcher=None)
        ctx = AppContext(config=FakeConfig(minecraft_dir=mc))
        ctx.register(service)
        observer = RecordingObserver()
        service.set_observer(observer)

        assert service.load().wait(5.0) is True

        rows, error = observer.versions()[-1]
        assert error == ""
        assert [row["id"] for row in rows] == ["1.19.2-forge", "1.20.4"]
        assert rows[0]["display"] == "1.19.2-forge"
        assert rows[0]["hasLoader"] is False, "降级扫描认不出加载器"
        assert observer.status_for("version_loaded")[1] == {"count": 2}

    def test_missing_versions_directory_is_an_empty_list(self, tmp_path: Path) -> None:
        service = VersionService(launcher=None)
        ctx = AppContext(config=FakeConfig(minecraft_dir=tmp_path / "nowhere"))
        ctx.register(service)
        observer = RecordingObserver()
        service.set_observer(observer)

        service.load().wait(5.0)
        assert observer.versions()[-1] == ([], "")
        assert observer.status_for("version_loaded")[1] == {"count": 0}

    def test_observer_exception_does_not_break_the_task(self) -> None:
        launcher = FakeLauncher([info("1.20.4")])
        service, _fake, _port, _config, _observer = make_service(launcher)
        service.set_observer(RecordingObserver(boom=True))

        handle = service.load()
        assert handle.wait(5.0) is True
        assert [row["id"] for row in service.rows] == ["1.20.4"]


# ─── B-19 搜索与排序 ───────────────────────────────────────────


class TestFilterAndSort:
    def _service(self) -> VersionService:
        launcher = FakeLauncher([
            info("1.20.4-forge-49.0.26", loader_type="forge", loader_version="49.0.26", vanilla_name="1.20.4"),
            info("1.19.2", vanilla_name="1.19.2"),
            info("1.20.1-fabric-0.15.0", loader_type="fabric", loader_version="0.15.0", vanilla_name="1.20.1"),
        ])
        service, _fake, _port, _config, _observer = make_service(launcher)
        service.load().wait(5.0)
        return service

    def test_empty_query_returns_everything(self) -> None:
        assert len(self._service().filtered()) == 3

    def test_query_matches_id(self) -> None:
        rows = self._service().filtered("fabric")
        assert [row["id"] for row in rows] == ["1.20.1-fabric-0.15.0"]

    def test_query_matches_vanilla_version(self) -> None:
        rows = self._service().filtered("1.19.2")
        assert [row["id"] for row in rows] == ["1.19.2"]

    def test_query_matches_loader_version(self) -> None:
        rows = self._service().filtered("49.0.26")
        assert [row["id"] for row in rows] == ["1.20.4-forge-49.0.26"]

    def test_query_is_case_insensitive_and_trimmed(self) -> None:
        assert len(self._service().filtered("  FORGE  ")) == 1

    def test_query_without_hit_is_empty(self) -> None:
        assert self._service().filtered("没有这个版本") == []

    def test_sort_by_name(self) -> None:
        rows = self._service().filtered("", SORT_NAME)
        assert [row["id"] for row in rows] == ["1.19.2", "1.20.1-fabric-0.15.0", "1.20.4-forge-49.0.26"]

    def test_sort_by_vanilla(self) -> None:
        rows = self._service().filtered("", SORT_VANILLA)
        assert [row["vanilla"] for row in rows] == ["1.19.2", "1.20.1", "1.20.4"]

    def test_sort_by_loader_puts_vanilla_first(self) -> None:
        rows = self._service().filtered("", SORT_LOADER)
        assert [row["loader"] for row in rows] == ["", "fabric", "forge"]

    def test_unknown_sort_key_falls_back_to_name(self) -> None:
        rows = self._service().filtered("", "乱写的键")
        assert [row["id"] for row in rows] == ["1.19.2", "1.20.1-fabric-0.15.0", "1.20.4-forge-49.0.26"]

    def test_filtered_returns_copies(self) -> None:
        service = self._service()
        first = service.filtered()
        first[0]["id"] = "被改坏了"
        assert service.rows[0]["id"] != "被改坏了"


# ─── B-20 详情 ─────────────────────────────────────────────────


class TestDetail:
    def test_detail_reports_path_java_and_loader(self, tmp_path: Path) -> None:
        mc = tmp_path / ".minecraft"
        version_dir = mc / "versions" / "1.20.4-forge-49.0.26"
        version_dir.mkdir(parents=True)
        (version_dir / "1.20.4-forge-49.0.26.json").write_text("{}", encoding="utf-8")

        launcher = FakeLauncher()
        launcher.instances["1.20.4-forge-49.0.26"] = info(
            "1.20.4-forge-49.0.26", loader_type="forge", loader_version="49.0.26",
            vanilla_name="1.20.4", java_major=17,
        )
        mods_dir = tmp_path / "mods"
        mods_dir.mkdir()
        (mods_dir / "a.jar").write_text("x", encoding="utf-8")
        (mods_dir / "b.jar").write_text("x", encoding="utf-8")

        service, _fake, _port, _config, _observer = make_service(
            launcher, config=FakeConfig(minecraft_dir=mc), resource=FakeResource(mods_dir)
        )
        data = service.detail("1.20.4-forge-49.0.26")

        assert data["exists"] is True
        assert data["path"] == str(version_dir)
        assert data["jsonPath"].endswith("1.20.4-forge-49.0.26.json")
        assert data["javaMajor"] == 17
        assert data["loader"] == "forge"
        assert data["modsCount"] == 2

    def test_detail_of_missing_version_says_so(self, tmp_path: Path) -> None:
        mc = tmp_path / ".minecraft"
        (mc / "versions").mkdir(parents=True)
        service, _fake, _port, _config, _observer = make_service(config=FakeConfig(minecraft_dir=mc))

        data = service.detail("1.20.4")
        assert data["exists"] is False
        assert data["id"] == "1.20.4"
        assert data["modsCount"] == 0

    def test_detail_finds_json_with_another_name(self, tmp_path: Path) -> None:
        """HMCL 导出的实例：目录里的 json 不叫 `{id}.json`。"""
        mc = tmp_path / ".minecraft"
        version_dir = mc / "versions" / "我的整合"
        version_dir.mkdir(parents=True)
        (version_dir / "custom.json").write_text("{}", encoding="utf-8")

        service, _fake, _port, _config, _observer = make_service(config=FakeConfig(minecraft_dir=mc))
        assert service.detail("我的整合")["jsonPath"].endswith("custom.json")

    def test_detail_without_unique_json_reports_empty_path(self, tmp_path: Path) -> None:
        mc = tmp_path / ".minecraft"
        version_dir = mc / "versions" / "混乱"
        version_dir.mkdir(parents=True)
        (version_dir / "a.json").write_text("{}", encoding="utf-8")
        (version_dir / "b.json").write_text("{}", encoding="utf-8")

        service, _fake, _port, _config, _observer = make_service(config=FakeConfig(minecraft_dir=mc))
        assert service.detail("混乱")["jsonPath"] == ""

    def test_detail_falls_back_to_list_row_when_core_has_no_info(self, tmp_path: Path) -> None:
        launcher = FakeLauncher([info("1.20.4", vanilla_name="1.20.4")])
        service, _fake, _port, _config, _observer = make_service(
            launcher, config=FakeConfig(minecraft_dir=tmp_path / ".minecraft")
        )
        service.load().wait(5.0)

        data = service.detail("1.20.4")
        assert data["vanilla"] == "1.20.4"
        assert data["display"] == "1.20.4  (1.20.4)"

    def test_detail_of_blank_id(self) -> None:
        service, _fake, _port, _config, _observer = make_service()
        assert service.detail("")["exists"] is False

    def test_detail_uses_resource_service_for_mods_dir(self, tmp_path: Path) -> None:
        version_mods = tmp_path / "versions" / "1.20.4-forge" / "mods"
        version_mods.mkdir(parents=True)
        (version_mods / "one.jar").write_text("x", encoding="utf-8")
        resource = FakeResource(version_mods)

        service, _fake, _port, _config, _observer = make_service(
            config=FakeConfig(minecraft_dir=tmp_path), resource=resource
        )
        data = service.detail("1.20.4-forge")
        assert data["modsDir"] == str(version_mods)
        assert data["modsCount"] == 1
        assert resource.calls[0][1] == "1.20.4-forge"


# ─── B-02 重命名 ───────────────────────────────────────────────


class TestRename:
    def test_rename_asks_then_confirms(self) -> None:
        port = FakePort(text="新名字", confirm=True)
        service, fake, _port, _config, observer = make_service(port=port)

        assert service.rename("旧名字").wait(5.0) is True

        assert [call[0] for call in port.calls] == ["ask_text", "confirm"]
        assert ("rename_instance", "旧名字", "新名字") in fake.calls
        assert "rename_instance_success" in observer.status_keys()
        assert observer.results()[-1][:2] == ("rename", True)

    def test_same_name_cancels_without_touching_core(self) -> None:
        port = FakePort(text="1.20.4")
        service, fake, _port, _config, observer = make_service(port=port)

        assert service.rename("1.20.4").wait(5.0) is True
        assert [call[0] for call in port.calls] == ["ask_text"]
        assert fake.calls == []
        assert observer.results()[-1][2]["cancelled"] is True
        assert observer.status_keys() == []

    def test_empty_input_cancels(self) -> None:
        service, fake, _port, _config, observer = make_service(port=FakePort(text="   "))
        service.rename("1.20.4").wait(5.0)
        assert fake.calls == []
        assert observer.results()[-1][2]["cancelled"] is True

    def test_declining_the_confirmation_cancels(self) -> None:
        port = FakePort(text="新名字", confirm=False)
        service, fake, _port, _config, observer = make_service(port=port)

        service.rename("旧名字").wait(5.0)
        assert [call[0] for call in port.calls] == ["ask_text", "confirm"]
        assert fake.calls == []
        assert observer.results()[-1][2]["cancelled"] is True

    def test_confirmation_is_defaulted_to_yes_like_the_old_dialog(self) -> None:
        port = FakePort(text="新名字")
        service, _fake, _port, _config, _observer = make_service(port=port)
        service.rename("旧名字").wait(5.0)
        assert port.calls[1][3] is True

    def test_invalid_name_reports_the_key(self) -> None:
        port = FakePort(text="有 空格的名字", confirm=True)
        service, fake, _port, _config, observer = make_service(port=port)
        fake.rename_result = (False, "rename_instance_invalid")

        service.rename("旧名字").wait(5.0)
        assert observer.status_for("rename_instance_invalid") == ("error", {})

    def test_existing_name_reports_the_name(self) -> None:
        port = FakePort(text="已存在", confirm=True)
        service, fake, _port, _config, observer = make_service(port=port)
        fake.rename_result = (False, "rename_instance_exists")

        service.rename("旧名字").wait(5.0)
        assert observer.status_for("rename_instance_exists") == ("error", {"name": "已存在"})

    def test_raw_core_message_goes_through_the_generic_key(self) -> None:
        port = FakePort(text="新名字", confirm=True)
        service, fake, _port, _config, observer = make_service(port=port)
        fake.rename_result = (False, "找不到实例 JSON 文件")

        service.rename("旧名字").wait(5.0)
        assert observer.status_for("rename_instance_error") == ("error", {"error": "找不到实例 JSON 文件"})

    def test_core_exception_becomes_a_status(self) -> None:
        port = FakePort(text="新名字", confirm=True)
        service, fake, _port, _config, observer = make_service(port=port)

        def boom(*_args: Any) -> None:
            raise OSError("磁盘只读")

        fake.rename_instance = boom  # type: ignore[assignment]
        service.rename("旧名字").wait(5.0)
        assert observer.status_for("rename_instance_error") == ("error", {"error": "磁盘只读"})
        assert observer.results()[-1][1] is False

    def test_renaming_the_remembered_version_moves_it(self) -> None:
        port = FakePort(text="新名字", confirm=True)
        config = FakeConfig(last_launched_version="旧名字")
        service, _fake, _port, _config, _observer = make_service(port=port, config=config)

        service.rename("旧名字").wait(5.0)
        assert config.last_launched_version == "新名字"
        assert config.saved == 1

    def test_renaming_another_version_keeps_the_remembered_one(self) -> None:
        port = FakePort(text="新名字", confirm=True)
        config = FakeConfig(last_launched_version="别的版本")
        service, _fake, _port, _config, _observer = make_service(port=port, config=config)

        service.rename("旧名字").wait(5.0)
        assert config.last_launched_version == "别的版本"
        assert config.saved == 0

    def test_blank_id_is_rejected(self) -> None:
        service, _fake, _port, _config, observer = make_service()
        assert service.rename("  ") is None
        assert observer.status_for("version_select_first")[0] == "error"


# ─── B-02 删除 ─────────────────────────────────────────────────


class TestRemove:
    def test_remove_confirms_then_deletes(self) -> None:
        port = FakePort(confirm=True)
        service, fake, _port, _config, observer = make_service(port=port)

        assert service.remove("1.20.4").wait(5.0) is True

        assert ("confirm", ) == (port.calls[0][0], )
        assert ("remove_version", "1.20.4") in fake.calls
        assert observer.status_for("version_removed") == ("success", {"version": "1.20.4"})
        assert observer.results()[-1][:2] == ("remove", True)

    def test_declining_does_not_delete(self) -> None:
        service, fake, _port, _config, observer = make_service(port=FakePort(confirm=False))
        service.remove("1.20.4").wait(5.0)
        assert fake.calls == []
        assert observer.results()[-1][2]["cancelled"] is True

    def test_failed_removal_reports_the_version(self) -> None:
        service, fake, _port, _config, observer = make_service(port=FakePort(confirm=True))
        fake.remove_result = (False, "1.20.4")
        service.remove("1.20.4").wait(5.0)
        assert observer.status_for("version_remove_failed") == ("error", {"version": "1.20.4"})

    def test_core_exception_becomes_a_status(self) -> None:
        service, fake, _port, _config, observer = make_service(port=FakePort(confirm=True))

        def boom(*_args: Any) -> None:
            raise PermissionError("没有权限")

        fake.remove_version = boom  # type: ignore[assignment]
        service.remove("1.20.4").wait(5.0)
        assert observer.status_for("version_remove_error") == ("error", {"error": "没有权限"})

    def test_deleting_the_remembered_version_forgets_it(self) -> None:
        config = FakeConfig(last_launched_version="1.20.4")
        service, _fake, _port, _config, _observer = make_service(
            port=FakePort(confirm=True), config=config
        )
        service.remove("1.20.4").wait(5.0)
        assert config.last_launched_version == ""
        assert config.saved == 1

    def test_deleting_another_version_keeps_the_remembered_one(self) -> None:
        config = FakeConfig(last_launched_version="别的版本")
        service, _fake, _port, _config, _observer = make_service(
            port=FakePort(confirm=True), config=config
        )
        service.remove("1.20.4").wait(5.0)
        assert config.last_launched_version == "别的版本"

    def test_blank_id_is_rejected(self) -> None:
        service, _fake, _port, _config, observer = make_service()
        assert service.remove("") is None
        assert observer.status_for("version_select_first")[0] == "error"


# ─── B-22 校验 ─────────────────────────────────────────────────


class TestVerify:
    def test_clean_version_reports_ok(self) -> None:
        service, fake, _port, _config, observer = make_service()
        fake.verify_result = {"total": 12, "valid": 12, "invalid": []}

        service.verify("1.20.4").wait(5.0)
        assert observer.status_for("version_verify_ok") == ("success", {"total": 12})
        assert observer.results()[-1][1] is True

    def test_broken_files_are_counted(self) -> None:
        service, fake, _port, _config, observer = make_service()
        fake.verify_result = {"total": 10, "valid": 8, "invalid": ["a.jar", "b.jar"]}

        service.verify("1.20.4").wait(5.0)
        assert observer.status_for("version_verify_bad") == ("warning", {"total": 10, "invalid": 2})
        data = observer.results()[-1][2]
        assert data["invalidCount"] == 2
        assert observer.results()[-1][1] is False

    def test_nothing_to_verify_is_a_warning_not_a_pass(self) -> None:
        service, fake, _port, _config, observer = make_service()
        fake.verify_result = {"total": 0, "valid": 0, "invalid": []}

        service.verify("1.20.4").wait(5.0)
        assert observer.status_for("version_verify_empty")[0] == "warning"

    def test_core_exception_becomes_a_status(self) -> None:
        service, fake, _port, _config, observer = make_service()

        def boom(*_args: Any) -> None:
            raise RuntimeError("哈希算不动")

        fake.verify_installed_version = boom  # type: ignore[assignment]
        service.verify("1.20.4").wait(5.0)
        assert observer.status_for("version_verify_failed") == ("error", {"error": "哈希算不动"})

    def test_invalid_list_is_capped(self) -> None:
        service, fake, _port, _config, observer = make_service()
        fake.verify_result = {
            "total": 100, "valid": 0,
            "invalid": ["f%d.jar" % i for i in range(MAX_INVALID_REPORTED + 20)],
        }
        service.verify("1.20.4").wait(5.0)
        data = observer.results()[-1][2]
        assert len(data["invalid"]) == MAX_INVALID_REPORTED
        assert data["invalidCount"] == MAX_INVALID_REPORTED + 20

    def test_blank_id_is_rejected(self) -> None:
        service, _fake, _port, _config, observer = make_service()
        assert service.verify(" ") is None
        assert observer.status_for("version_select_first")[0] == "error"


# ─── B-21 打开目录 ─────────────────────────────────────────────


class TestOpenFolder:
    def test_opens_the_version_directory(self, tmp_path: Path) -> None:
        mc = tmp_path / ".minecraft"
        (mc / "versions" / "1.20.4").mkdir(parents=True)
        opened: List[str] = []
        service, _fake, _port, _config, _observer = make_service(config=FakeConfig(minecraft_dir=mc))
        service._opener = opened.append  # noqa: SLF001 - 注入点就是给测试用的

        assert service.open_folder("1.20.4") is True
        assert opened == [str(mc / "versions" / "1.20.4")]

    def test_missing_version_reports_and_returns_false(self, tmp_path: Path) -> None:
        mc = tmp_path / ".minecraft"
        (mc / "versions").mkdir(parents=True)
        service, _fake, _port, _config, observer = make_service(config=FakeConfig(minecraft_dir=mc))

        assert service.open_folder("1.20.4") is False
        assert observer.status_for("version_detail_missing")[0] == "error"

    def test_blank_id_reports_and_returns_false(self) -> None:
        service, _fake, _port, _config, observer = make_service()
        assert service.open_folder("") is False
        assert observer.status_for("version_detail_missing")[0] == "error"

    def test_opener_failure_becomes_a_status(self, tmp_path: Path) -> None:
        mc = tmp_path / ".minecraft"
        (mc / "versions" / "1.20.4").mkdir(parents=True)
        service, _fake, _port, _config, observer = make_service(config=FakeConfig(minecraft_dir=mc))

        def boom(_path: str) -> None:
            raise OSError("没有关联的程序")

        service._opener = boom  # noqa: SLF001
        assert service.open_folder("1.20.4") is False
        assert observer.status_for("version_open_folder_failed") == ("error", {"error": "没有关联的程序"})


# ─── 界面文案键的完整性 ────────────────────────────────────────


class TestMessageKeys:
    """服务发出的每个状态键都必须在 4 个语言文件里存在（否则界面显示裸键名）。"""

    KEYS = (
        "version_loading_installed",
        "version_loaded",
        "version_load_failed",
        "version_renaming",
        "version_removed",
        "version_remove_failed",
        "version_remove_error",
        "version_select_first",
        "version_verifying",
        "version_verify_ok",
        "version_verify_bad",
        "version_verify_empty",
        "version_verify_failed",
        "version_detail_missing",
        "version_open_folder_failed",
        "rename_instance_title",
        "rename_instance_prompt",
        "rename_instance",
        "rename_instance_confirm",
        "rename_instance_success",
        "rename_instance_invalid",
        "rename_instance_exists",
        "rename_instance_error",
        "confirm_delete",
        "confirm_delete_version",
        "deleting_version",
    )

    @pytest.mark.parametrize("lang", ["zh_CN", "en_US", "ja_JP", "zh_TW"])
    def test_keys_exist_in_every_language(self, lang: str) -> None:
        path = REPO_ROOT / "ui" / "locales" / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        missing = [key for key in self.KEYS if key not in data]
        assert missing == [], f"{lang} 缺键: {missing}"

    @pytest.mark.parametrize("lang", ["zh_CN", "en_US", "ja_JP", "zh_TW"])
    def test_no_message_key_carries_an_emoji(self, lang: str) -> None:
        """新界面禁止 emoji（项目 UI 规范）：这些键是阶段 3 新加的，必须干净。"""
        path = REPO_ROOT / "ui" / "locales" / f"{lang}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        dirty = [key for key in self.KEYS if key in data and _has_emoji(str(data[key]))]
        assert dirty == [], f"{lang} 里这些键带 emoji: {dirty}"


def _has_emoji(text: str) -> bool:
    for ch in text:
        code = ord(ch)
        if 0x1F300 <= code <= 0x1FAFF or 0x2600 <= code <= 0x27BF or code == 0xFE0F:
            return True
    return False


# ─── 并发与线程 ────────────────────────────────────────────────


class TestThreading:
    def test_two_loads_at_once_do_not_interleave_badly(self) -> None:
        launcher = FakeLauncher([info("1.20.4", vanilla_name="1.20.4")])
        service, _fake, _port, _config, observer = make_service(launcher)
        gate = threading.Event()

        original = launcher.get_installed_versions

        def slow() -> Any:
            gate.wait(2.0)
            return original()

        launcher.get_installed_versions = slow  # type: ignore[assignment]
        first = service.load()
        second = service.load()
        gate.set()
        assert first.wait(5.0) and second.wait(5.0)
        assert len(observer.versions()) == 2
        assert [row["id"] for row in service.rows] == ["1.20.4"]
