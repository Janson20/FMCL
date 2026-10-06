"""版本实例的**路径解析**与重命名的永久回归守卫（阶段 3 任务 3.2 验收当场发现的两条）。

这两条都是"核心层里早就有、但一直没人走到"的路径假设问题，用户 2026-10-06 实测翻了出来：

* **D-169**：`rename_instance()` 里 `dst = new_dir / item.name`，而 `item` 是
  `os.listdir()` 返回的**字符串** → 每次重命名都抛
  `'str' object has no attribute 'name'`，重命名 100% 失败（旧界面同样如此）。
* **D-170**：`verify_installed_version()` 只认顶层 `versions/{id}.json`，
  而 `_read_instance_info()` 只认 `versions/{id}/{id}.json` —— 于是**列表里看得见、
  一校验就说"版本 JSON 不存在"**。收口到 `find_version_json()` 一处。

测试**不构造真的 `MinecraftLauncher`**（它的 `__init__` 会 minecraft_launcher_lib、
打镜像补丁、初始化主题引擎）：把类上的**真方法**绑到一个桩对象上跑，既走了真实代码路径、
又没有那些副作用（与 `poc/` 里既有的做法一致）。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from launcher.core import MinecraftLauncher  # noqa: E402


class FakeConfig:
    def __init__(self, root: Path) -> None:
        self.minecraft_dir = root / ".minecraft"
        self.base_dir = root

    def get_versions_dir(self) -> Path:
        return self.minecraft_dir / "versions"


class LauncherStub:
    """把 `MinecraftLauncher` 上**真的**方法绑过来的最小宿主。

    只给这些方法真正用到的那几个属性（`config` / `minecraft_dir` / 缓存字典），
    别的一概不装 —— 装了反而会让"方法其实依赖别的东西"这件事被掩盖。
    """

    find_version_json = MinecraftLauncher.find_version_json
    _find_version_jar = MinecraftLauncher._find_version_jar
    rename_instance = MinecraftLauncher.rename_instance
    verify_installed_version = MinecraftLauncher.verify_installed_version

    def __init__(self, root: Path) -> None:
        self.config = FakeConfig(root)
        self.minecraft_dir = str(self.config.minecraft_dir)
        self._instance_info_cache: Dict[str, Any] = {}
        self._instance_cache_valid = False
        self._plugin_manager = None

    def invalidate_instance_cache(self) -> None:
        self._instance_info_cache = {}
        self._instance_cache_valid = False

    def _emit_plugin_hook(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def make_version(root: Path, name: str, *, json_name: Optional[str] = None,
                 top_level_json: bool = False, payload: Optional[dict] = None) -> Path:
    """造一个已安装版本目录，返回目录路径（`json_name=None` 表示不放 JSON）。"""
    versions_dir = root / ".minecraft" / "versions"
    version_dir = versions_dir / name
    version_dir.mkdir(parents=True, exist_ok=True)
    if json_name is not None:
        (version_dir / json_name).write_text(
            json.dumps(payload if payload is not None else {"id": name}), encoding="utf-8"
        )
    if top_level_json:
        (versions_dir / f"{name}.json").write_text(
            json.dumps(payload if payload is not None else {"id": name}), encoding="utf-8"
        )
    return version_dir


# ─── D-170：三种布局都要认 ─────────────────────────────────────


class TestFindVersionJson:
    def test_folder_layout(self, tmp_path: Path) -> None:
        make_version(tmp_path, "1.20.4", json_name="1.20.4.json")
        stub = LauncherStub(tmp_path)
        found = stub.find_version_json("1.20.4")
        assert found is not None and found.name == "1.20.4.json"
        assert found.parent.name == "1.20.4"

    def test_top_level_layout(self, tmp_path: Path) -> None:
        """安装器会**同时**写顶层副本（`remove_version()` 两个都删，就是这个原因）。"""
        versions_dir = tmp_path / ".minecraft" / "versions"
        versions_dir.mkdir(parents=True)
        (versions_dir / "1.20.4.json").write_text("{}", encoding="utf-8")
        stub = LauncherStub(tmp_path)
        found = stub.find_version_json("1.20.4")
        assert found is not None and found.parent == versions_dir

    def test_third_party_layout_with_another_name(self, tmp_path: Path) -> None:
        """HMCL 之类导入的实例：目录里那个 json 不叫 `{id}.json`。"""
        make_version(tmp_path, "我的整合", json_name="custom.json")
        stub = LauncherStub(tmp_path)
        found = stub.find_version_json("我的整合")
        assert found is not None and found.name == "custom.json"

    def test_folder_layout_wins_over_top_level(self, tmp_path: Path) -> None:
        make_version(tmp_path, "1.20.4", json_name="1.20.4.json", top_level_json=True)
        stub = LauncherStub(tmp_path)
        found = stub.find_version_json("1.20.4")
        assert found is not None and found.parent.name == "1.20.4"

    def test_ambiguous_folder_is_not_guessed(self, tmp_path: Path) -> None:
        version_dir = make_version(tmp_path, "混乱", json_name="a.json")
        (version_dir / "b.json").write_text("{}", encoding="utf-8")
        stub = LauncherStub(tmp_path)
        assert stub.find_version_json("混乱") is None

    def test_missing_version(self, tmp_path: Path) -> None:
        (tmp_path / ".minecraft" / "versions").mkdir(parents=True)
        stub = LauncherStub(tmp_path)
        assert stub.find_version_json("没有这个") is None


# ─── D-170（第二半）：主程序 jar 的实际文件名 ───────────────────


class TestFindVersionJar:
    def test_id_named_jar(self, tmp_path: Path) -> None:
        version_dir = make_version(tmp_path, "1.20.4")
        (version_dir / "1.20.4.jar").write_bytes(b"jar")
        stub = LauncherStub(tmp_path)
        found = stub._find_version_jar(stub.config.get_versions_dir(), "1.20.4", {"url": "https://x/client.jar"})
        assert found is not None and found.name == "1.20.4.jar"

    def test_jar_named_after_the_download_url(self, tmp_path: Path) -> None:
        version_dir = make_version(tmp_path, "1.20.4")
        (version_dir / "client.jar").write_bytes(b"jar")
        stub = LauncherStub(tmp_path)
        found = stub._find_version_jar(stub.config.get_versions_dir(), "1.20.4",
                                       {"url": "https://piston-data.mojang.com/v1/objects/abc/client.jar"})
        assert found is not None and found.name == "client.jar"

    def test_renamed_instance_keeps_the_old_jar_name(self, tmp_path: Path) -> None:
        """重命名只改 JSON 与目录名，目录里的 jar 还是旧名字 —— 这正是实测踩到的那条。"""
        version_dir = make_version(tmp_path, "1.18.2-forge")
        (version_dir / "1.18.2.jar").write_bytes(b"jar")
        stub = LauncherStub(tmp_path)
        found = stub._find_version_jar(stub.config.get_versions_dir(), "1.18.2-forge", {})
        assert found is not None and found.name == "1.18.2.jar"

    def test_missing_jar(self, tmp_path: Path) -> None:
        make_version(tmp_path, "1.20.4")
        stub = LauncherStub(tmp_path)
        assert stub._find_version_jar(stub.config.get_versions_dir(), "1.20.4", {}) is None


# ─── D-169：重命名必须真的能跑通 ───────────────────────────────


class TestRenameInstance:
    def _prepare(self, tmp_path: Path) -> LauncherStub:
        version_dir = make_version(tmp_path, "1.18.2-forge-40.3.11",
                                   json_name="1.18.2-forge-40.3.11.json",
                                   top_level_json=True,
                                   payload={"id": "1.18.2-forge-40.3.11", "type": "release"})
        (version_dir / "1.18.2-forge-40.3.11.jar").write_bytes(b"jar")
        (version_dir / "natives").mkdir()
        (version_dir / "natives" / "a.dll").write_bytes(b"dll")
        return LauncherStub(tmp_path)

    def test_rename_moves_files_and_rewrites_id(self, tmp_path: Path) -> None:
        """D-169 的正向判据：旧实现这一步抛 `'str' object has no attribute 'name'`。"""
        stub = self._prepare(tmp_path)
        ok, message = stub.rename_instance("1.18.2-forge-40.3.11", "1.18.2-forge")
        assert ok is True, f"重命名失败：{message}"

        versions_dir = stub.config.get_versions_dir()
        new_dir = versions_dir / "1.18.2-forge"
        assert new_dir.is_dir()
        assert not (versions_dir / "1.18.2-forge-40.3.11").exists()
        assert (new_dir / "1.18.2-forge-40.3.11.jar").is_file(), (
            "目录里的文件应当**原样**搬过去（jar 的名字不会跟着改 —— 这正是 D-170 第二半："
            "重命名后的实例，主程序 jar 仍叫旧名字）"
        )
        assert (new_dir / "natives" / "a.dll").is_file()
        assert (new_dir / "1.18.2-forge.json").is_file()
        assert json.loads((new_dir / "1.18.2-forge.json").read_text(encoding="utf-8"))["id"] == "1.18.2-forge"
        assert not (versions_dir / "1.18.2-forge-40.3.11.json").exists(), "顶层副本也要改名"
        assert (versions_dir / "1.18.2-forge.json").is_file()

    def test_rename_rejects_bad_names(self, tmp_path: Path) -> None:
        stub = self._prepare(tmp_path)
        assert stub.rename_instance("1.18.2-forge-40.3.11", "has space")[1] == "rename_instance_invalid"
        assert stub.rename_instance("1.18.2-forge-40.3.11", "")[1] == "rename_instance_invalid"

    def test_rename_refuses_to_overwrite(self, tmp_path: Path) -> None:
        stub = self._prepare(tmp_path)
        make_version(tmp_path, "taken")
        assert stub.rename_instance("1.18.2-forge-40.3.11", "taken")[1] == "rename_instance_exists"

    def test_rename_of_a_missing_instance(self, tmp_path: Path) -> None:
        stub = self._prepare(tmp_path)
        ok, message = stub.rename_instance("no-such-version", "new-name")
        assert ok is False and "不存在" in message


# ─── D-170 的后果：校验要真的算起来 ────────────────────────────


class TestVerifyInstalledVersion:
    def test_verify_finds_the_folder_json_and_hashes_the_jar(self, tmp_path: Path) -> None:
        """只有 `versions/{id}/{id}.json` 这种布局时，校验也必须能算出结果。"""
        payload = b"client jar bytes"
        sha1 = hashlib.sha1(payload).hexdigest()
        version_dir = make_version(
            tmp_path, "1.18.2-forge", json_name="1.18.2-forge.json",
            payload={"id": "1.18.2-forge", "downloads": {"client": {"sha1": sha1, "url": "https://x/client.jar"}}},
        )
        (version_dir / "1.18.2-forge.jar").write_bytes(payload)

        stub = LauncherStub(tmp_path)
        result = stub.verify_installed_version("1.18.2-forge")
        assert result["total"] == 1, f"校验没找到主程序 jar：{result}"
        assert result["valid"] == 1
        assert result["invalid"] == []

    def test_verify_reports_a_corrupted_jar(self, tmp_path: Path) -> None:
        version_dir = make_version(
            tmp_path, "1.18.2-forge", json_name="1.18.2-forge.json",
            payload={"id": "1.18.2-forge", "downloads": {"client": {"sha1": "0" * 40, "url": "https://x/client.jar"}}},
        )
        (version_dir / "client.jar").write_bytes(b"tampered")

        stub = LauncherStub(tmp_path)
        result = stub.verify_installed_version("1.18.2-forge")
        assert result["total"] == 1 and result["valid"] == 0
        assert len(result["invalid"]) == 1

    def test_verify_of_a_missing_version_is_empty(self, tmp_path: Path) -> None:
        stub = LauncherStub(tmp_path)
        assert stub.verify_installed_version("没有这个") == {"total": 0, "valid": 0, "invalid": []}

    def test_renamed_instance_can_still_be_verified(self, tmp_path: Path) -> None:
        """重命名之后立刻校验：JSON 与 jar 都不叫新名字，但两条查找都要能找到它们。"""
        payload = b"client jar bytes"
        sha1 = hashlib.sha1(payload).hexdigest()
        stub = TestRenameInstance()._prepare(tmp_path)
        old_dir = stub.config.get_versions_dir() / "1.18.2-forge-40.3.11"
        (old_dir / "1.18.2-forge-40.3.11.json").write_text(
            json.dumps({"id": "1.18.2-forge-40.3.11",
                        "downloads": {"client": {"sha1": sha1, "url": "https://x/client.jar"}}}),
            encoding="utf-8",
        )
        (old_dir / "1.18.2-forge-40.3.11.jar").write_bytes(payload)

        ok, message = stub.rename_instance("1.18.2-forge-40.3.11", "1.18.2-forge")
        assert ok is True, message

        result = stub.verify_installed_version("1.18.2-forge")
        assert result["total"] == 1 and result["valid"] == 1, f"改名后校验不上：{result}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
