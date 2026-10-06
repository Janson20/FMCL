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
from launcher.errors import InstallCancelled  # noqa: E402


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
    # 3.3 起校验与修复共用这两个内部件（`_read_version_json_data` 读 JSON、
    # `_sha1_of` 算哈希）；上面那三个也是"绑真方法"的写法，这里保持一致。
    # `_sha1_of` 在核心那边是 **staticmethod**：绑到桩上必须用 `staticmethod()` 包一层，
    # 否则通过实例访问会被当成普通函数、被自动绑上 `self`（报"多给了一个参数"）。
    _read_version_json_data = MinecraftLauncher._read_version_json_data
    _sha1_of = staticmethod(MinecraftLauncher._sha1_of)
    expected_version_files = MinecraftLauncher.expected_version_files
    repair_installed_version = MinecraftLauncher.repair_installed_version

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


class FakeInstallModule:
    """假的 `minecraft_launcher_lib.install`：按"文件级回调"演一遍下载。

    真 mcllib 的下载循环就是每下一个文件调一次 `setStatus`/`setProgress`
    （`_helper.download_file`），核心层的取消钩子正是挂在那两个回调里的 ——
    假件必须复现这个节奏，否则验不出"取消是文件级生效"。
    """

    def __init__(self, owner: "InstallStub") -> None:
        self._owner = owner

    def install_minecraft_version(self, version_id: str, directory: Any, callback: Any = None) -> None:
        self._owner.calls.append(("install", version_id))
        if self._owner.fail_with is not None:
            raise self._owner.fail_with
        hooks = callback or {}
        hooks.get("setMax", lambda _n: None)(2)
        for step in (1, 2):
            if self._owner.cancel_after_steps and step > self._owner.cancel_after_steps:
                self._owner.cancelled[0] = True
            self._owner.steps.append(step)
            hooks.get("setStatus", lambda _s: None)(f"Download file{step}.jar")
            hooks.get("setProgress", lambda _p: None)(step)


class FakeUtilsModule:
    def __init__(self, owner: "InstallStub") -> None:
        self._owner = owner

    def get_available_versions(self, directory: Any) -> Any:
        return [{"id": "1.20.4", "type": "release"}]


class FakeMcllib:
    def __init__(self, owner: "InstallStub") -> None:
        self.install = FakeInstallModule(owner)
        self.utils = FakeUtilsModule(owner)


class InstallStub:
    """`install_version` 的最小宿主（与 `LauncherStub` 同一套"绑真方法"的做法）。"""

    install_version = MinecraftLauncher.install_version
    get_available_versions = MinecraftLauncher.get_available_versions
    _get_callback = MinecraftLauncher._get_callback
    #: 老出口（一元签名，被混入类遮住的那两个就长这样）+ 3.3 的"本次调用出口"三件套
    _set_status = MinecraftLauncher._set_status
    _set_progress = MinecraftLauncher._set_progress
    _set_max = MinecraftLauncher._set_max
    _should_report_progress = MinecraftLauncher._should_report_progress
    _emit_status = MinecraftLauncher._emit_status
    _emit_progress = MinecraftLauncher._emit_progress
    expected_version_files = MinecraftLauncher.expected_version_files
    _read_version_json_data = MinecraftLauncher._read_version_json_data
    _sha1_of = staticmethod(MinecraftLauncher._sha1_of)
    _find_version_jar = MinecraftLauncher._find_version_jar
    find_version_json = MinecraftLauncher.find_version_json
    verify_installed_version = MinecraftLauncher.verify_installed_version
    repair_installed_version = MinecraftLauncher.repair_installed_version
    #: 修复的两个上限（类属性，不是方法）也跟着绑过来，否则修复一跑就 AttributeError
    MAX_REPAIR_FILES = MinecraftLauncher.MAX_REPAIR_FILES
    MAX_REPAIR_REPORTED = MinecraftLauncher.MAX_REPAIR_REPORTED

    def __init__(self, root: Path) -> None:
        self.config = FakeConfig(root)
        self.minecraft_dir = str(self.config.minecraft_dir)
        self.on_progress: Any = None
        self.current_max = 0
        self.calls: list = []
        self.steps: list = []
        self.cancelled = [False]
        self.cancel_after_steps = 0
        self.fail_with: Optional[BaseException] = None
        self._instance_info_cache: Dict[str, Any] = {}
        self._instance_cache_valid = False
        self._mcllib = FakeMcllib(self)

    def invalidate_instance_cache(self) -> None:
        self._instance_info_cache = {}
        self._instance_cache_valid = False

    def _emit_plugin_hook(self, *_args: Any, **_kwargs: Any) -> None:
        return None


# ─── 3.3：取消（尽力取消，文件级粒度）──────────────────────────


class _ShadowingMixin:
    """模仿 `MultiMCMixin`：在 MRO 里排在核心**前面**，且定义同名的一元 `_set_status`。

    D-172 的现场就是它：`MinecraftLauncher` 把这个混入类放在第一位，于是
    `self._set_status` 解析到的是**它**，而不是核心那个（核心的实现被遮住了）。
    谁给 `self._set_status` 多传一个参数，真实类上就是
    `TypeError: _set_status() takes 2 positional arguments but 3 were given`
    —— 表现是"装带加载器的版本必失败"（用户 2026-10-06 实测）。
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.shadowed: list = []

    def _set_status(self, msg: str) -> None:
        self.shadowed.append(msg)


class ShadowedInstallStub(_ShadowingMixin, InstallStub):
    """与 `MinecraftLauncher` 同一套 MRO 形状：混入类在前、核心在后。"""


class TestCallbackSurvivesMroShadowing:
    def test_the_hazard_is_real_in_the_shipped_class(self) -> None:
        """先证明这个坑真实存在：真类上的 `_set_status` 就是混入类那一份。"""
        from launcher import MinecraftLauncher
        from launcher.multimc import MultiMCMixin

        assert MinecraftLauncher._set_status is MultiMCMixin._set_status, (
            "MRO 变了？这条钉子说明的是「_set_status 会被遮住」，请重新评估 D-172 的修法"
        )

    def test_install_works_through_a_shadowing_mro(self, tmp_path: Path) -> None:
        """被遮住时安装也必须跑通：进度走 `_emit_*`，老方法只按它自己的一元签名调。"""
        stub = ShadowedInstallStub(tmp_path)
        sink: list = []

        ok, installed = stub.install_version(
            "1.20.4", "无", on_progress=lambda current, total, text: sink.append((current, total, text))
        )

        assert ok is True and installed == "1.20.4"
        assert (2, 2, "") in sink, f"本次调用的出口要拿到进度：{sink}"
        assert (0, 0, "Download file1.jar") in sink, f"状态文本也要走同一条出口：{sink}"

    def test_legacy_path_still_calls_the_shadowing_method_with_one_argument(self, tmp_path: Path) -> None:
        """没有本次出口时（旧 Tk 路径）仍然只用**一元**调那个被遮住的方法。

        这条是"修法本身"的钉子：把 `_get_callback` 改回 `self._set_status(status, on_progress)`
        这里就红 —— 而那是 3.3 曾经交付过的真实缺陷（D-172）。
        """
        stub = ShadowedInstallStub(tmp_path)
        ok, _ = stub.install_version("1.20.4", "无")
        assert ok is True
        assert stub.shadowed, "老路径应当把状态交给混入类那一份实现（Tk 的 set_status）"


class TestInstallCancellation:
    def test_cancel_before_start_downloads_nothing(self, tmp_path: Path) -> None:
        """还没开始就点了取消：一个字节都不该下。"""
        stub = InstallStub(tmp_path)
        with pytest.raises(InstallCancelled):
            stub.install_version("1.20.4", "无", cancel_check=lambda: True)
        assert stub.calls == [], "取消之后不该再去碰下载器"
        assert stub.steps == []

    def test_cancel_lands_in_the_download_callback(self, tmp_path: Path) -> None:
        """取消是**文件级**的：回调里发现标志就抛，下一个文件连状态回调都走不到。

        （"在飞的那个文件"当然已经开始了 —— 磁盘上没有事务，能做到的就是
        "下一个文件不再动"，这也是界面必须如实刷新列表的原因。）
        """
        stub = InstallStub(tmp_path)
        stub.cancel_after_steps = 1
        sink: list = []
        with pytest.raises(InstallCancelled):
            stub.install_version(
                "1.20.4", "无",
                on_progress=lambda current, total, text: sink.append((current, total, text)),
                cancel_check=lambda: stub.cancelled[0],
            )
        assert (2, 2, "") not in sink, f"第二个文件的进度不该报出来：{sink}"
        assert (1, 2, "") in sink, f"第一个文件是正常下完的：{sink}"

    def test_without_cancel_check_nothing_changes(self, tmp_path: Path) -> None:
        """旧调用方（Tk 界面）不传 `cancel_check` —— 行为必须与从前逐字节一致。

        这条是取消功能的**反向钉子**：谁要是把取消检查做成"默认总在查"，这里先红。
        """
        stub = InstallStub(tmp_path)
        ok, installed = stub.install_version("1.20.4", "无")
        assert ok is True and installed == "1.20.4"
        assert stub.steps == [1, 2], "没有取消标志时两个文件都该下完"

    def test_install_failure_still_returns_false(self, tmp_path: Path) -> None:
        """取消之外的真失败仍然走老契约 `(False, version_id)`，不许变成异常。"""
        stub = InstallStub(tmp_path)
        stub.fail_with = RuntimeError("boom")
        ok, installed = stub.install_version("1.20.4", "无")
        assert ok is False and installed == "1.20.4"

    def test_cancelled_install_is_not_reported_as_failure(self, tmp_path: Path) -> None:
        """取消抛的是 `InstallCancelled` 而不是被吞成 `(False, …)`：
        调用方据此区分"装坏了"与"我不装了"（界面文案与日志都不同）。"""
        stub = InstallStub(tmp_path)
        stub.cancel_after_steps = 1
        try:
            stub.install_version("1.20.4", "无", cancel_check=lambda: stub.cancelled[0])
        except InstallCancelled:
            pass
        else:  # pragma: no cover - 走不到
            raise AssertionError("取消必须原样抛出 InstallCancelled")


class TestInstallProgressSink:
    def test_per_call_sink_replaces_the_global_one(self, tmp_path: Path) -> None:
        """QML 侧的进度出口**只喂给本次调用**：全局 `on_progress` 是旧 Tk 界面的
        （`main.py:439` 挂的），两个消费者同时收到会让状态条打架。"""
        stub = InstallStub(tmp_path)
        sink: list = []
        global_sink: list = []
        stub.on_progress = lambda current, total, text: global_sink.append((current, total, text))

        ok, _ = stub.install_version(
            "1.20.4", "无", on_progress=lambda current, total, text: sink.append((current, total, text))
        )
        assert ok is True
        assert global_sink == [], "传了本次调用的出口就不该再喂全局出口"
        assert (0, 0, "Download file1.jar") in sink, f"状态文本要走同一条出口：{sink}"
        assert (2, 2, "") in sink, f"进度要走同一条出口：{sink}"


# ─── 3.3 / D-167：修复（校验的下一半）──────────────────────────


def _library(path_rel: str, payload: bytes, url: str = "https://libs/a.jar") -> Dict[str, Any]:
    return {
        "name": "g:a:1",
        "downloads": {
            "artifact": {
                "path": path_rel,
                "sha1": hashlib.sha1(payload).hexdigest(),
                "url": url,
            }
        },
    }


class TestRepairInstalledVersion:
    """修复链路：清单同源、合格文件不重下、坏的重下、缺的补上。"""

    def _prepare(self, tmp_path: Path, *, jar_payload: bytes = b"client jar") -> Dict[str, Any]:
        ok_payload = b"good library"
        bad_payload = b"tampered library"
        version_dir = make_version(
            tmp_path, "1.20.4", json_name="1.20.4.json",
            payload={
                "id": "1.20.4",
                "libraries": [
                    _library("g/a/1/a-1.jar", ok_payload),
                    _library("g/b/1/b-1.jar", ok_payload, url="https://libs/b.jar"),
                ],
                "downloads": {"client": {
                    "sha1": hashlib.sha1(jar_payload).hexdigest(),
                    "url": "https://x/client.jar",
                }},
            },
        )
        libs = tmp_path / ".minecraft" / "libraries" / "g"
        (libs / "a" / "1").mkdir(parents=True, exist_ok=True)
        (libs / "b" / "1").mkdir(parents=True, exist_ok=True)
        (libs / "a" / "1" / "a-1.jar").write_bytes(ok_payload)
        (libs / "b" / "1" / "b-1.jar").write_bytes(bad_payload)
        #: 主程序 jar **不存在**：修复要能补上（旧校验只看已存在的文件，看不见"缺失"）
        return {"version_dir": version_dir, "jar": version_dir / "1.20.4.jar", "jar_payload": jar_payload}

    def test_repair_only_touches_broken_and_missing_files(self, tmp_path: Path, monkeypatch) -> None:
        fixture = self._prepare(tmp_path)
        downloaded: list = []

        def fake_download(url, path, callback=None, **kwargs):
            downloaded.append((url, str(path)))
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(fixture["jar_payload"] if url.endswith("client.jar") else b"good library")
            return True

        monkeypatch.setattr("minecraft_launcher_lib._helper.download_file", fake_download)
        stub = InstallStub(tmp_path)
        result = stub.repair_installed_version("1.20.4")

        assert result["ok"] is True, result
        assert result["missing"] == 1 and result["broken"] == 1
        assert result["repaired"] == 2
        assert result["remaining"] == []
        assert sorted(url for url, _ in downloaded) == sorted(
            ["https://libs/b.jar", "https://x/client.jar"]
        ), f"只该重下坏的那个库与缺失的主 jar（合格的那个不许动），实际：{downloaded}"
        assert fixture["jar"].read_bytes() == fixture["jar_payload"]

    def test_repair_reports_failures_but_keeps_going(self, tmp_path: Path, monkeypatch) -> None:
        """单个文件下不动不该中断整轮修复 —— 能修几个是几个，结果如实报告。"""
        fixture = self._prepare(tmp_path)

        def fake_download(url, path, callback=None, **kwargs):
            if url.endswith("client.jar"):
                raise RuntimeError("network down")
            Path(path).write_bytes(b"good library")
            return True

        monkeypatch.setattr("minecraft_launcher_lib._helper.download_file", fake_download)
        stub = InstallStub(tmp_path)
        result = stub.repair_installed_version("1.20.4")

        assert result["ok"] is False
        assert result["repaired"] == 1
        assert result["failed"], "失败原因要带回来（界面要显示条数）"
        assert len(result["remaining"]) == 1, f"还剩一个没修好：{result['remaining']}"

    def test_repair_can_be_cancelled(self, tmp_path: Path, monkeypatch) -> None:
        """取消之后剩下的坏文件不许再动（能停的地方是**下一个文件之前**）。"""
        fixture = self._prepare(tmp_path)
        flag = [False]

        def fake_download(url, path, callback=None, **kwargs):
            Path(path).write_bytes(b"good library")
            flag[0] = True  #: 下完第一个就点取消
            return True

        monkeypatch.setattr("minecraft_launcher_lib._helper.download_file", fake_download)
        stub = InstallStub(tmp_path)
        bad_lib = tmp_path / ".minecraft" / "libraries" / "g" / "b" / "1" / "b-1.jar"
        with pytest.raises(InstallCancelled):
            stub.repair_installed_version("1.20.4", cancel_check=lambda: flag[0])
        assert bad_lib.read_bytes() == b"tampered library", "取消之后那个坏库仍然原样"
        assert fixture["jar"].exists(), "在飞的那个文件（主 jar）已经下完了，磁盘上没有事务"

    def test_repair_without_version_json_says_so(self, tmp_path: Path) -> None:
        stub = InstallStub(tmp_path)
        result = stub.repair_installed_version("没有这个")
        assert result["ok"] is False and result["reason"] == "json_missing"

    def test_verify_and_repair_share_the_same_file_list(self, tmp_path: Path) -> None:
        """**清单同源**（D-167 的收口点）：校验说坏的那一个，修复必须正好去下它。

        这条与 `test_repair_only_touches_broken_and_missing_files` 是一对：
        一个盯"修复下了哪些"，一个盯"校验报了哪些" —— 两处各写一份清单时，
        它们会对不上（D-170 就是这么来的：读 JSON 与找 jar 各有一套假设）。
        """
        fixture = self._prepare(tmp_path)
        stub = InstallStub(tmp_path)
        verified = stub.verify_installed_version("1.20.4")
        assert verified["total"] == 2, "坏库 + 合格库（缺失的 jar 不算在内，这是 3.2 的语义）"
        assert len(verified["invalid"]) == 1
        assert verified["invalid"][0].endswith("b-1.jar")

        expected = stub.expected_version_files(
            stub._read_version_json_data(fixture["version_dir"] / "1.20.4.json"),
            stub.config.get_versions_dir(),
            "1.20.4",
        )
        assert {item["kind"] for item in expected} == {"library", "client"}
        assert len(expected) == 3, f"清单应当是 2 库 + 1 主 jar：{expected}"


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-q"]))
