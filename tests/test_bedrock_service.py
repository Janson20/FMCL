"""``services/bedrock_service.py``（阶段 1 任务 1.11）的单元测试。

**全部离线**：不联网、不开窗口、不装基岩版、不碰注册表 / 子进程 / winget。
所有外部动作都换成替身（`BedrockService.__init__` 的注入参数，默认值是真实
实现），版本列表用**假版本 DB**喂进真实解析器（`BedrockManager` 的
`_get_version_db` 被打桩），因此测的是"界面会拿到的真实数据形状"。

覆盖：可用/已安装列表、过滤与分页边界、安装前置检查四个分支、
安装编排与失败聚合、启动/删除编排、各结果类型的字段契约。
"""

from __future__ import annotations

import ast
import io
import queue
import threading
from pathlib import Path

import pytest

from services.bedrock_service import (
    DEFAULT_PAGE_SIZE,
    LAUNCH_LOCK,
    BedrockService,
    DotnetCheck,
    InstallOutcome,
    LaunchOutcome,
    PageSlice,
    RemoveOutcome,
    clamp_page,
    count_release,
    filter_versions,
    find_installed,
    paginate,
    total_pages,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


# ═══════════════════════════════════════════════════════════════════
# 替身：假版本 DB / 假配置 / 假组件模块 / 假 dotnet 模块
# ═══════════════════════════════════════════════════════════════════


def fake_version_db() -> dict:
    """一份 mcappx.com 形状的假版本库（字段名与真实 BedrockManager 解析的一致）。"""
    return {
        "From_mcappx.com": {
            "1.21.120.21": {
                "Type": "Release",
                "BuildType": "GDK",
                "Date": "2026-01-05",
                "Variations": [{"Arch": "x64", "MD5": "aa" * 16, "OSbuild": "19045"}],
            },
            "1.21.100.6": {
                "Type": "Release",
                "BuildType": "UWP",
                "Date": "2025-12-01",
                "Variations": [{"Arch": "x64", "MD5": "bb" * 16, "OSbuild": "19045"}],
            },
            "1.21.130.1": {
                "Type": "Preview",
                "BuildType": "UWP",
                "Date": "2026-02-01",
                "Variations": [{"Arch": "x64", "MD5": "cc" * 16, "OSbuild": "19045"}],
            },
            # 没有 x64 Variation 的条目必须被解析器跳过（对齐真实实现）
            "1.20.0.0": {"Type": "Release", "BuildType": "UWP", "Variations": [{"Arch": "arm64"}]},
        }
    }


def parsed_available(tmp_path: Path) -> list:
    """用**真实** `BedrockManager.get_available_versions()` 解析假版本 DB。

    刻意不复刻解析代码：解析属于 `launcher/bedrock/`（本来就零 GUI），
    这里要验证的是"服务拿到的数据结构与真实生产者一致"。
    """
    from launcher.bedrock import BedrockManager

    manager = BedrockManager(tmp_path / "bedrock_versions")
    manager._get_version_db = lambda refresh=False: fake_version_db()  # type: ignore[assignment]
    return manager.get_available_versions()


class FakeConfig:
    """`config.config` 的替身（条款 / 组件同意标记）。"""

    def __init__(self, terms: bool = False, components: bool = False):
        self.bedrock_terms_accepted = terms
        self.bedrock_components_consented = components
        self.save_calls = 0

    def save_config(self) -> None:
        self.save_calls += 1


class FakeComponents:
    def __init__(self, ready: bool = False, fail: bool = False):
        self._ready = ready
        self._fail = fail
        self.download_calls: list = []

    def is_ready(self) -> bool:
        return self._ready

    def download(self, progress_cb=None, status_cb=None, force: bool = False):
        self.download_calls.append({"status_cb": status_cb, "force": force})
        if status_cb:
            status_cb("假组件下载中")
        if self._fail:
            raise RuntimeError("组件下载炸了")
        self._ready = True
        return Path("XUserLauncher.Core.dll")


class FakeDotnet:
    """`launcher.bedrock.dotnet` 的替身。"""

    SDK_DOWNLOAD_PAGE = "https://dotnet.microsoft.com/en-us/download/dotnet/10.0"

    def __init__(self, has: bool = False, url_raises: bool = False, url: str = "https://x/sdk.exe"):
        self._has = has
        self._url_raises = url_raises
        self._url = url
        self.sdk_url_calls = 0

    def has_sdk10(self) -> bool:
        return self._has

    def sdk_download_url(self, arch=None) -> str:
        self.sdk_url_calls += 1
        if self._url_raises:
            raise RuntimeError("取直链失败")
        return self._url


class FakeMsauth:
    """`launcher.bedrock.msauth` 的替身（设备码登录四件套）。"""

    def __init__(self, cached: str = "", token: dict | None = None):
        self._cached = cached
        self._token = token or {"access_token": "AT", "refresh_token": "RT"}
        self.cache_calls = 0
        self.poll_calls = 0
        self.saved: list = []

    def get_cached_access_token(self, status_cb=None) -> str:
        self.cache_calls += 1
        if status_cb:
            status_cb("查缓存")
        return self._cached

    def request_device_code(self) -> dict:
        return {
            "device_code": "DC",
            "user_code": "CODE-1",
            "verification_uri": "https://microsoft.com/link",
            "interval": 3,
        }

    def poll_device_code(self, device_code, interval=5, status_cb=None) -> dict:
        self.poll_calls += 1
        if status_cb:
            status_cb(f"轮询 {device_code} 间隔 {interval}")
        return self._token

    def save_credentials(self, access_token: str, refresh_token: str = "") -> bool:
        self.saved.append((access_token, refresh_token))
        return True


def make_service(**kwargs) -> BedrockService:
    kwargs.setdefault("config_provider", lambda: FakeConfig())
    return BedrockService(**kwargs)


def callbacks(**over) -> dict:
    """一套最小的 bedrock_* 回调替身。"""
    base = {
        "bedrock_get_installed_versions": lambda: [],
        "bedrock_get_available_versions": lambda: [],
        "bedrock_install_version": lambda version, name="": (True, {"name": f"名-{version}"}),
        "bedrock_launch_version": lambda name, args="", access_token="": (True, "启动中"),
        "bedrock_remove_version": lambda name: (True, ""),
        "bedrock_get_root": lambda: "",
    }
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _release_launch_lock():
    """任何一条用例失败后都不许把进程级启动锁漏在"已持有"状态。

    记录现状：`LAUNCH_LOCK` 是**模块级**的（原文就是模块级，跨 UI 实例共享），
    所以一条用例把它拿走会让后面的用例莫名其妙地拿到 None。
    """
    yield
    if LAUNCH_LOCK.locked():
        LAUNCH_LOCK.release()


# ═══════════════════════════════════════════════════════════════════
# 版本列表：真实解析器 + 假版本 DB
# ═══════════════════════════════════════════════════════════════════


class TestVersionList:
    def test_parsed_db_shape_matches_service_assumptions(self, tmp_path):
        """假版本 DB 经真实解析后，服务读的 4 个字段都在且类型正确。"""
        available = parsed_available(tmp_path)
        # 没有 x64 Variation 的条目被跳过：4 条里活下来 3 条
        assert [v["version"] for v in available] == ["1.21.130.1", "1.21.120.21", "1.21.100.6"]
        for v in available:
            assert set(v) == {"version", "type", "build_type", "date", "md5", "os_build"}
            assert v["type"] in ("release", "preview", "beta")
            assert v["build_type"] in ("UWP", "GDK")

    def test_load_available_passes_callback_result_through(self, tmp_path):
        available = parsed_available(tmp_path)
        svc = make_service()
        assert svc.load_available(callbacks(bedrock_get_available_versions=lambda: available)) == available

    def test_load_installed_passes_callback_result_through(self):
        installed = [{"name": "我的基岩", "version": "1.21.120.21", "build_type": "GDK"}]
        svc = make_service()
        assert svc.load_installed(callbacks(bedrock_get_installed_versions=lambda: installed)) == installed

    def test_load_uses_empty_default_when_callback_absent(self):
        svc = make_service()
        assert svc.load_available({}) == []
        assert svc.load_installed({}) == []

    def test_load_does_not_swallow_callback_errors(self):
        """异常不翻译：原文的 try/except 留在界面，服务必须照抛。"""
        def boom():
            raise RuntimeError("版本库炸了")

        svc = make_service()
        with pytest.raises(RuntimeError, match="版本库炸了"):
            svc.load_available({"bedrock_get_available_versions": boom})

    def test_root_dir_reads_root_callback(self):
        svc = make_service()
        assert svc.root_dir(callbacks(bedrock_get_root=lambda: r"D:\b")) == r"D:\b"
        assert svc.root_dir({}) == ""

    def test_count_release(self, tmp_path):
        available = parsed_available(tmp_path)
        assert count_release(available) == 2
        assert make_service().count_release(available) == 2

    def test_find_installed_matches_by_name_only(self):
        installed = [{"name": "A", "build_type": "UWP"}, {"name": "B", "build_type": "GDK"}]
        assert find_installed(installed, "B") == {"name": "B", "build_type": "GDK"}
        assert find_installed(installed, "C") is None
        assert find_installed([], "A") is None


# ═══════════════════════════════════════════════════════════════════
# 过滤与分页边界
# ═══════════════════════════════════════════════════════════════════


ALL_LABEL = "全部"  # 界面注入的 `_("bedrock_filter_all")` 替身


class TestFilter:
    def test_empty_keyword_and_all_label_keeps_everything(self, tmp_path):
        available = parsed_available(tmp_path)
        assert filter_versions(available, "", ALL_LABEL, ALL_LABEL) == available

    def test_keyword_is_stripped_and_lowercased(self, tmp_path):
        available = parsed_available(tmp_path)
        got = filter_versions(available, "  1.21.12  ", ALL_LABEL, ALL_LABEL)
        assert [v["version"] for v in got] == ["1.21.120.21"]

    def test_build_type_filter(self, tmp_path):
        available = parsed_available(tmp_path)
        got = filter_versions(available, "", "GDK", ALL_LABEL)
        assert [v["version"] for v in got] == ["1.21.120.21"]

    def test_keyword_and_build_type_are_anded(self, tmp_path):
        available = parsed_available(tmp_path)
        # 关键字命中 GDK 那一条，但档位选了 UWP → 交集为空
        assert filter_versions(available, "1.21.120", "UWP", ALL_LABEL) == []

    def test_non_all_label_is_compared_against_build_type(self, tmp_path):
        """档位值既不是"全部"也不是真实 build_type 时，结果为空（不是全部）。"""
        available = parsed_available(tmp_path)
        assert filter_versions(available, "", "Bogus", ALL_LABEL) == []

    def test_missing_version_field_is_tolerated(self):
        """`v.get("version", "")` 的兜底照抄：缺字段的条目不会被当成命中。"""
        assert filter_versions([{"build_type": "UWP"}], "1.21", ALL_LABEL, ALL_LABEL) == []
        assert filter_versions([{"build_type": "UWP"}], "", ALL_LABEL, ALL_LABEL) == [{"build_type": "UWP"}]

    def test_service_delegates_filter(self, tmp_path):
        available = parsed_available(tmp_path)
        svc = make_service()
        assert svc.filter_versions(available, "", "GDK", ALL_LABEL) == filter_versions(
            available, "", "GDK", ALL_LABEL
        )


class TestPagination:
    def test_total_pages_of_empty_is_one(self):
        assert total_pages(0) == 1
        assert make_service().total_pages(0) == 1

    def test_single_page_and_exact_boundary(self):
        assert total_pages(1) == 1
        assert total_pages(DEFAULT_PAGE_SIZE) == 1
        assert total_pages(DEFAULT_PAGE_SIZE + 1) == 2
        assert total_pages(DEFAULT_PAGE_SIZE * 3) == 3

    def test_total_pages_never_below_one(self):
        assert total_pages(0, 20) == 1
        assert total_pages(5, 20) == 1
        assert clamp_page(0, 1) == 1

    def test_clamp_page(self):
        assert clamp_page(5, 3) == 3
        assert clamp_page(0, 3) == 1
        assert clamp_page(2, 3) == 2

    def test_paginate_empty_list(self):
        got = paginate([], 7, 20)
        assert got == PageSlice([], 1, 1)

    def test_paginate_clamps_out_of_range_page(self):
        items = [{"version": str(i)} for i in range(45)]
        got = paginate(items, 99, 20)
        assert got.total_pages == 3
        assert got.page == 3
        assert [v["version"] for v in got.items] == [str(i) for i in range(40, 45)]
        assert paginate(items, -3, 20).page == 1

    def test_paginate_last_partial_page(self):
        items = [{"version": str(i)} for i in range(45)]
        assert len(paginate(items, 1, 20).items) == 20
        assert len(paginate(items, 2, 20).items) == 20
        assert len(paginate(items, 3, 20).items) == 5

    def test_paginate_real_available_is_single_page(self, tmp_path):
        available = parsed_available(tmp_path)
        got = make_service().paginate(available, 1, DEFAULT_PAGE_SIZE)
        assert (got.page, got.total_pages, len(got.items)) == (1, 1, len(available))


# ═══════════════════════════════════════════════════════════════════
# 安装前置检查：四个分支（条款 / 缺 .NET / 组件 / 平台不支持）
# ═══════════════════════════════════════════════════════════════════


class TestPreconditions:
    def test_terms_flag_and_persist(self):
        cfg = FakeConfig(terms=False)
        svc = BedrockService(config_provider=lambda: cfg)
        assert svc.terms_accepted() is False
        svc.mark_terms_accepted()
        assert cfg.bedrock_terms_accepted is True
        assert cfg.save_calls == 1, "同意后必须落盘（否则每次都要重问）"

    def test_terms_already_accepted_short_circuits(self):
        svc = BedrockService(config_provider=lambda: FakeConfig(terms=True))
        assert svc.terms_accepted() is True

    def test_component_consent_flag_and_persist(self):
        cfg = FakeConfig(components=False)
        svc = BedrockService(config_provider=lambda: cfg)
        assert svc.component_download_consented() is False
        svc.mark_components_consented()
        assert cfg.bedrock_components_consented is True and cfg.save_calls == 1

    def test_requires_dotnet10_only_for_gdk(self):
        svc = make_service()
        assert svc.requires_dotnet10({"build_type": "GDK"}) is True
        assert svc.requires_dotnet10({"build_type": "UWP"}) is False
        assert svc.requires_dotnet10({}) is False

    def test_dotnet_present_no_url_lookup(self):
        dotnet = FakeDotnet(has=True)
        got = make_service(dotnet=dotnet).check_dotnet10()
        assert got == DotnetCheck(True, "")
        assert dotnet.sdk_url_calls == 0, "已装 SDK 时原文直接返回，不会去取直链"

    def test_dotnet_missing_uses_direct_url(self):
        dotnet = FakeDotnet(has=False, url="https://x/dotnet-sdk-10.0.100-win-x64.exe")
        got = make_service(dotnet=dotnet).check_dotnet10()
        assert got.has_sdk10 is False
        assert got.download_url == "https://x/dotnet-sdk-10.0.100-win-x64.exe"

    def test_dotnet_missing_and_url_lookup_fails_falls_back_to_page(self):
        dotnet = FakeDotnet(has=False, url_raises=True)
        got = make_service(dotnet=dotnet).check_dotnet10()
        assert got.has_sdk10 is False
        assert got.download_url == FakeDotnet.SDK_DOWNLOAD_PAGE
        assert dotnet.sdk_url_calls == 1

    def test_dotnet_check_error_is_not_swallowed(self):
        """检测本身抛异常时照抛 —— 界面的 `except` 负责"异常即放行下载"。"""

        class Boom:
            SDK_DOWNLOAD_PAGE = "page"

            def has_sdk10(self):
                raise OSError("注册表读不了")

        with pytest.raises(OSError, match="注册表读不了"):
            make_service(dotnet=Boom()).check_dotnet10()

    def test_component_setup_needed_matrix(self):
        gdk = [{"name": "G", "build_type": "GDK"}]
        uwp = [{"name": "U", "build_type": "UWP"}]
        svc = make_service(components=FakeComponents(ready=False))
        # GDK 且组件缺失 → 需要征求同意
        assert svc.component_setup_needed(callbacks(bedrock_get_installed_versions=lambda: gdk), "G") is True
        # UWP → 不需要
        assert svc.component_setup_needed(callbacks(bedrock_get_installed_versions=lambda: uwp), "U") is False
        # 名字对不上 → 不需要
        assert svc.component_setup_needed(callbacks(bedrock_get_installed_versions=lambda: gdk), "X") is False
        # 组件已就绪 → 不需要
        ready = make_service(components=FakeComponents(ready=True))
        assert ready.component_setup_needed(callbacks(bedrock_get_installed_versions=lambda: gdk), "G") is False

    def test_platform_unsupported_callback_result_is_normalized(self):
        """平台不支持：`launcher/core.py` 的回调返回 (False, "基岩版仅支持 Windows 系统")。

        假替身，不碰真实平台判断 / 注册表 / 子进程。
        """
        svc = make_service()
        outcome = svc.install_version(
            callbacks(bedrock_install_version=lambda v, n="": (False, "基岩版仅支持 Windows 系统")), "1.21"
        )
        assert outcome == InstallOutcome("1.21", False, "基岩版仅支持 Windows 系统")


# ═══════════════════════════════════════════════════════════════════
# 安装 / 删除编排
# ═══════════════════════════════════════════════════════════════════


class TestInstallOrchestration:
    def test_success_uses_dict_name(self):
        svc = make_service()
        calls = []

        def cb(version, name=""):
            calls.append((version, name))
            return True, {"name": "我的基岩 1.21", "path": "p"}

        outcome = svc.install_version(callbacks(bedrock_install_version=cb), "1.21.120.21")
        assert outcome == InstallOutcome("1.21.120.21", True, "我的基岩 1.21")
        assert calls == [("1.21.120.21", "")], "第二个位置参数原文传空串"

    def test_success_but_non_dict_info_falls_back_to_version(self):
        """原文 `success and isinstance(info, dict)` —— 非 dict 一律走失败支路。"""
        svc = make_service()
        outcome = svc.install_version(
            callbacks(bedrock_install_version=lambda v, n="": (True, "莫名其妙")), "1.21"
        )
        assert outcome == InstallOutcome("1.21", False, "莫名其妙")

    def test_success_dict_without_name_falls_back_to_version(self):
        svc = make_service()
        outcome = svc.install_version(
            callbacks(bedrock_install_version=lambda v, n="": (True, {"path": "p"})), "9.9"
        )
        assert outcome == InstallOutcome("9.9", True, "9.9")

    def test_failure_stringifies_info(self):
        svc = make_service()
        outcome = svc.install_version(
            callbacks(bedrock_install_version=lambda v, n="": (False, ValueError("下载失败"))), "1.21"
        )
        assert outcome.success is False and "下载失败" in outcome.display

    def test_callback_exception_is_not_swallowed(self):
        def boom(version, name=""):
            raise KeyError("bedrock_install_version")

        svc = make_service()
        with pytest.raises(KeyError):
            svc.install_version(callbacks(bedrock_install_version=boom), "1.21")

    def test_hard_subscript_key_is_preserved(self):
        """`callbacks["bedrock_install_version"]` 必须是**硬下标**。

        `scripts/check_callback_keys.py` 靠这个形状把它算成 hard 读取；
        改成 `.get(...)` 会让死键检测从"会崩"降级成"静默失效"。
        """
        tree = ast.parse(io.open(REPO_ROOT / "services" / "bedrock_service.py", encoding="utf-8").read())
        subscripts = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
            and node.slice.value in ("bedrock_install_version", "bedrock_launch_version",
                                     "bedrock_remove_version")
        ]
        assert sorted(n.slice.value for n in subscripts) == [
            "bedrock_install_version", "bedrock_launch_version", "bedrock_remove_version"
        ]

    def test_remove_success_and_failure(self):
        svc = make_service()
        assert svc.remove_version(callbacks(), "A") == RemoveOutcome("A", True, "")
        failed = svc.remove_version(
            callbacks(bedrock_remove_version=lambda n: (False, "目录占用")), "A"
        )
        assert failed == RemoveOutcome("A", False, "目录占用")


# ═══════════════════════════════════════════════════════════════════
# 启动编排
# ═══════════════════════════════════════════════════════════════════


class TestLaunchOrchestration:
    def test_uwp_launch_skips_components_and_login(self):
        launched = []
        components = FakeComponents(ready=False)
        msauth = FakeMsauth()
        svc = make_service(components=components, msauth=msauth, xbox_signed_in=lambda: False)

        outcome = svc.run_launch(
            callbacks(
                bedrock_get_installed_versions=lambda: [{"name": "U", "build_type": "UWP"}],
                bedrock_launch_version=lambda n, a="", t="": (launched.append((n, a, t)) or (True, "启动中")),
            ),
            "U",
        )
        assert outcome == LaunchOutcome("U", True, "启动中")
        assert launched == [("U", "", "")], "UWP 不做组件下载、不做登录，token 为空串"
        assert components.download_calls == [] and msauth.cache_calls == 0

    def test_gdk_launch_downloads_components_then_logs_in(self):
        order = []
        components = FakeComponents(ready=False)
        msauth = FakeMsauth(cached="CACHED-AT")
        notices = []
        statuses = []

        def signed_in():
            order.append("signed_in")
            return False

        class OrderedComponents(FakeComponents):
            def download(self, progress_cb=None, status_cb=None, force=False):
                order.append("download")
                return super().download(progress_cb=progress_cb, status_cb=status_cb, force=force)

        svc = make_service(
            components=OrderedComponents(ready=False), msauth=msauth, xbox_signed_in=signed_in
        )
        outcome = svc.run_launch(
            callbacks(
                bedrock_get_installed_versions=lambda: [{"name": "G", "build_type": "GDK"}],
                bedrock_launch_version=lambda n, a="", t="": (
                    order.append("launch") or (True, f"token={t}")
                ),
            ),
            "G",
            status_cb=statuses.append,
            download_notice="正在下载认证组件...",
            login_status_cb=notices.append,
        )
        assert outcome.success is True and outcome.message == "token=CACHED-AT"
        assert order == ["download", "signed_in", "launch"]
        assert statuses[0] == "正在下载认证组件...", "download_notice 只在真缺组件时播报一次"
        assert "查缓存" in notices

    def test_ready_components_skip_download_but_still_check_login(self):
        components = FakeComponents(ready=True)
        msauth = FakeMsauth(cached="AT2")
        statuses = []
        svc = make_service(components=components, msauth=msauth, xbox_signed_in=lambda: False)
        outcome = svc.run_launch(
            callbacks(
                bedrock_get_installed_versions=lambda: [{"name": "G", "build_type": "GDK"}],
                bedrock_launch_version=lambda n, a="", t="": (True, t),
            ),
            "G",
            status_cb=statuses.append,
            download_notice="不该出现",
        )
        assert components.download_calls == []
        assert statuses == []
        assert outcome.message == "AT2"

    def test_busy_lock_returns_none_and_does_not_launch(self):
        called = []
        assert LAUNCH_LOCK.acquire(blocking=False)
        try:
            outcome = make_service().run_launch(
                callbacks(bedrock_launch_version=lambda n, a="", t="": (called.append(n) or (True, ""))), "A"
            )
        finally:
            LAUNCH_LOCK.release()
        assert outcome is None
        assert called == [], "被防重入挡掉时不调发布回调（原文此时不投递任何任务）"

    def test_launch_callback_exception_releases_lock(self):
        def boom(n, a="", t=""):
            raise RuntimeError("启动炸了")

        svc = make_service()
        with pytest.raises(RuntimeError, match="启动炸了"):
            svc.run_launch(callbacks(bedrock_launch_version=boom), "A")
        assert LAUNCH_LOCK.locked() is False, "异常路径必须释放锁（原文的 finally 语义）"

    def test_component_download_failure_propagates_and_releases_lock(self):
        svc = make_service(
            components=FakeComponents(ready=False, fail=True),
            msauth=FakeMsauth(),
            xbox_signed_in=lambda: False,
        )
        with pytest.raises(RuntimeError, match="组件下载炸了"):
            svc.run_launch(
                callbacks(bedrock_get_installed_versions=lambda: [{"name": "G", "build_type": "GDK"}]), "G"
            )
        assert LAUNCH_LOCK.locked() is False

    def test_import_error_in_xbox_check_is_swallowed(self):
        """原文 `except ImportError: pass` —— 取不到身份检查就带着空 token 继续启动。"""
        msauth = FakeMsauth(cached="不该用到")

        def raise_import_error():
            raise ImportError("no env module")

        svc = make_service(
            components=FakeComponents(ready=True), msauth=msauth, xbox_signed_in=raise_import_error
        )
        outcome = svc.run_launch(
            callbacks(
                bedrock_get_installed_versions=lambda: [{"name": "G", "build_type": "GDK"}],
                bedrock_launch_version=lambda n, a="", t="": (True, f"[{t}]"),
            ),
            "G",
        )
        assert outcome.message == "[]"
        assert msauth.cache_calls == 0

    def test_gdk_launch_with_existing_xbox_identity_skips_login(self):
        msauth = FakeMsauth()
        svc = make_service(
            components=FakeComponents(ready=True), msauth=msauth, xbox_signed_in=lambda: True
        )
        outcome = svc.run_launch(
            callbacks(
                bedrock_get_installed_versions=lambda: [{"name": "G", "build_type": "GDK"}],
                bedrock_launch_version=lambda n, a="", t="": (True, f"[{t}]"),
            ),
            "G",
        )
        assert outcome.message == "[]"
        assert msauth.cache_calls == 0


class TestMsaLoginFlow:
    def test_cached_token_fast_path_no_lock_no_device_code(self):
        msauth = FakeMsauth(cached="CACHED")
        svc = make_service(msauth=msauth)
        assert svc.msa_login_flow() == "CACHED"
        assert msauth.poll_calls == 0 and msauth.saved == []

    def test_full_device_code_flow(self):
        msauth = FakeMsauth(cached="", token={"access_token": "AT", "refresh_token": "RT"})
        svc = make_service(msauth=msauth)
        codes, dones, statuses = [], [], []
        token = svc.msa_login_flow(
            status_cb=statuses.append,
            code_cb=lambda code, uri: codes.append((code, uri)),
            done_cb=lambda t, err: dones.append((t, err)),
        )
        assert token == "AT"
        assert codes == [("CODE-1", "https://microsoft.com/link")]
        assert dones == [("AT", "")]
        assert msauth.saved == [("AT", "RT")]
        assert msauth.cache_calls == 2, "原文持锁后会再查一次缓存"
        assert any("轮询 DC 间隔 3" in s for s in statuses), "interval 透传"

    def test_verification_uri_falls_back_to_microsoft_link(self):
        class NoUri(FakeMsauth):
            def request_device_code(self):
                return {"device_code": "DC", "user_code": "C", "interval": "5"}

        codes = []
        make_service(msauth=NoUri()).msa_login_flow(code_cb=lambda c, u: codes.append((c, u)))
        assert codes == [("C", "https://www.microsoft.com/link")]

    def test_missing_access_token_raises_keyerror(self):
        """原文最后一行是 `token["access_token"]` —— 缺字段就是 KeyError，照抄不改。"""
        class BadToken(FakeMsauth):
            def poll_device_code(self, device_code, interval=5, status_cb=None):
                return {"refresh_token": "RT"}

        with pytest.raises(KeyError):
            make_service(msauth=BadToken()).msa_login_flow()


class TestOpenFolder:
    def test_open_in_explorer_uses_injected_opener(self):
        opened = []
        svc = make_service(startfile=opened.append)
        svc.open_in_explorer(r"D:\b")
        assert opened == [r"D:\b"]

    def test_open_in_explorer_propagates_error(self):
        def boom(path):
            raise OSError("没有关联程序")

        with pytest.raises(OSError, match="没有关联程序"):
            make_service(startfile=boom).open_in_explorer("x")


# ═══════════════════════════════════════════════════════════════════
# 结果类型契约 / 服务独立性 / 零 UI
# ═══════════════════════════════════════════════════════════════════


class TestResultContracts:
    def test_dataclasses_are_frozen_and_positional(self):
        assert InstallOutcome("v", True, "d") == InstallOutcome(version="v", success=True, display="d")
        assert LaunchOutcome("n", False, "m").message == "m"
        assert RemoveOutcome("n", True, "").success is True
        with pytest.raises(Exception):
            InstallOutcome("v", True, "d").success = False  # type: ignore[misc]

    def test_page_slice_contract(self):
        got = PageSlice(items=[1, 2], page=2, total_pages=3)
        assert (got.items, got.page, got.total_pages) == ([1, 2], 2, 3)

    def test_dotnet_check_contract(self):
        assert DotnetCheck(False, "url").download_url == "url"
        assert DotnetCheck(True, "").has_sdk10 is True


class TestServiceStandalone:
    def test_instantiable_without_app_context(self):
        svc = BedrockService()
        assert svc.attached is False
        assert svc.name == "bedrock" and svc.label == "基岩版"
        assert svc.describe()["requires"] == []

    def test_service_module_has_zero_ui_and_zero_threads(self):
        src = io.open(REPO_ROOT / "services" / "bedrock_service.py", encoding="utf-8").read()
        tree = ast.parse(src)
        bad = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                bad += [a.name for a in node.names if a.name.split(".")[0] in ("tkinter", "customtkinter", "ui")]
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                if mod.split(".")[0] in ("tkinter", "customtkinter", "ui"):
                    bad.append(mod)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in ("Thread", "Timer", "after"):
                    bad.append(ast.unparse(node.func))
        assert bad == [], f"服务层出现 UI / 线程 / 定时器：{bad}"

    def test_module_locks_are_shared_process_wide(self):
        """两个服务实例共用**模块级**的锁（原文语义，防跨实例重复启动）。"""
        first = BedrockService()
        second = BedrockService()
        assert first is not second
        # 锁不在实例上，而是模块级：拿住一次，另一个实例的启动也会被挡
        assert LAUNCH_LOCK.acquire(blocking=False)
        try:
            assert second.run_launch(callbacks(), "A") is None
        finally:
            LAUNCH_LOCK.release()

    def test_custom_lock_is_not_used_by_run_launch(self):
        """记录现状：`run_launch` 用的是**模块级** LAUNCH_LOCK，不接受注入。

        原文的锁就是模块级的（跨 ModernApp 实例共享），本轮保持；这条测试
        是"如果有人后来改成实例级 / 可注入，请同时评估跨实例防重入"的提醒。
        """
        assert not hasattr(BedrockService(), "_launch_lock")
        assert isinstance(LAUNCH_LOCK, type(threading.Lock()))


# ═══════════════════════════════════════════════════════════════════
# 运行期接线：用"未绑定方法 + 假宿主"直接调 Mixin 方法
# （仓库既有的测试风格：`ServerTabMixin._watch_server_exit(FakeApp())`）
# ═══════════════════════════════════════════════════════════════════


class FakeOwner:
    """`BedrockMixin` 的假宿主：只提供被调方法真正读到的那几个属性。"""

    def __init__(self, *, callbacks_map=None, service=None, fields=None):
        self.callbacks = callbacks_map if callbacks_map is not None else callbacks()
        self._task_queue = queue.Queue()
        self.status = []
        self.threads = []
        if service is not None:
            self._bedrock_service_fallback = service
        for key, value in (fields or {}).items():
            setattr(self, key, value)

    def set_status(self, text, kind=""):
        self.status.append((text, kind))

    def _run_in_thread(self, fn, *args):
        self.threads.append((fn, args))


class TestMixinRuntimeWiring:
    """证明界面方法真的**跑到**服务上，而不只是"语法上引用了服务"。"""

    def test_load_installed_enqueues_loaded_task(self):
        from ui.app_bedrock import BedrockMixin

        installed = [{"name": "A", "build_type": "UWP"}]
        owner = FakeOwner(callbacks_map=callbacks(bedrock_get_installed_versions=lambda: installed))
        BedrockMixin._load_bedrock_installed(owner)
        assert owner._task_queue.get_nowait() == ("bedrock_installed_loaded", installed)

    def test_load_available_error_enqueues_error_task(self):
        from ui.app_bedrock import BedrockMixin

        def boom():
            raise RuntimeError("版本库炸了")

        owner = FakeOwner(callbacks_map=callbacks(bedrock_get_available_versions=boom))
        BedrockMixin._load_bedrock_available(owner)
        assert owner._task_queue.get_nowait() == ("bedrock_load_error", "版本库炸了")

    def test_total_pages_delegates_with_ui_page_size(self):
        from ui.app_bedrock import BedrockMixin

        owner = FakeOwner(fields={"bedrock_filtered": [{}] * 45, "_bedrock_page_size": 20})
        assert BedrockMixin._get_bedrock_total_pages(owner) == 3

    def test_install_worker_success_and_failure_task_shapes(self):
        from ui.app_bedrock import BedrockMixin

        ok_owner = FakeOwner(
            callbacks_map=callbacks(bedrock_install_version=lambda v, n="": (True, {"name": "好名字"}))
        )
        BedrockMixin._install_bedrock_worker(ok_owner, "1.21")
        assert ok_owner._task_queue.get_nowait() == ("bedrock_install_done", ("1.21", True, "好名字"))

        bad_owner = FakeOwner(
            callbacks_map=callbacks(bedrock_install_version=lambda v, n="": (False, "下载失败"))
        )
        BedrockMixin._install_bedrock_worker(bad_owner, "1.21")
        assert bad_owner._task_queue.get_nowait() == ("bedrock_install_done", ("1.21", False, "下载失败"))

    def test_remove_worker_enqueues_outcome(self):
        from ui.app_bedrock import BedrockMixin

        owner = FakeOwner(callbacks_map=callbacks(bedrock_remove_version=lambda n: (True, "")))
        BedrockMixin._remove_bedrock_worker(owner, "A")
        assert owner._task_queue.get_nowait() == ("bedrock_remove_done", ("A", True, ""))

    def test_launch_worker_busy_lock_enqueues_nothing(self):
        """防重入被挡时界面**不投递任何任务**（原文语义），状态栏也不刷新。"""
        from ui.app_bedrock import BedrockMixin

        owner = FakeOwner()
        assert LAUNCH_LOCK.acquire(blocking=False)
        try:
            BedrockMixin._launch_bedrock_worker(owner, "A")
        finally:
            LAUNCH_LOCK.release()
        assert owner._task_queue.empty()
        assert owner.status == []

    def test_launch_worker_success_enqueues_done(self):
        from ui.app_bedrock import BedrockMixin

        owner = FakeOwner(
            callbacks_map=callbacks(
                bedrock_get_installed_versions=lambda: [{"name": "U", "build_type": "UWP"}],
                bedrock_launch_version=lambda n, a="", t="": (True, "启动中"),
            )
        )
        BedrockMixin._launch_bedrock_worker(owner, "U")
        assert owner._task_queue.get_nowait() == ("bedrock_launch_done", ("U", True, "启动中"))

    def test_launch_worker_exception_enqueues_failure(self):
        from ui.app_bedrock import BedrockMixin

        def boom(n, a="", t=""):
            raise RuntimeError("启动炸了")

        owner = FakeOwner(
            callbacks_map=callbacks(
                bedrock_get_installed_versions=lambda: [{"name": "U", "build_type": "UWP"}],
                bedrock_launch_version=boom,
            )
        )
        BedrockMixin._launch_bedrock_worker(owner, "U")
        assert owner._task_queue.get_nowait() == ("bedrock_launch_done", ("U", False, "启动炸了"))

    def test_msa_login_flow_wires_three_sinks_to_task_queue(self):
        from ui.app_bedrock import BedrockMixin

        service = make_service(msauth=FakeMsauth(token={"access_token": "AT", "refresh_token": "RT"}))
        owner = FakeOwner(service=service)
        assert BedrockMixin._msa_login_flow(owner) == "AT"
        first = owner._task_queue.get_nowait()
        assert first[0] == "bedrock_msa_status"
        kinds = {first[0]}
        while not owner._task_queue.empty():
            kinds.add(owner._task_queue.get_nowait()[0])
        assert kinds == {"bedrock_msa_status", "bedrock_msa_code", "bedrock_msa_done"}

    def test_open_folder_uses_service_root_and_opener(self):
        from ui.app_bedrock import BedrockMixin

        opened = []
        service = make_service(startfile=opened.append)
        owner = FakeOwner(
            callbacks_map=callbacks(bedrock_get_root=lambda: r"D:\b"), service=service
        )
        BedrockMixin._open_bedrock_folder(owner)
        assert opened == [r"D:\b"]

    def test_open_folder_empty_root_does_nothing(self):
        from ui.app_bedrock import BedrockMixin

        opened = []
        owner = FakeOwner(service=make_service(startfile=opened.append))
        BedrockMixin._open_bedrock_folder(owner)  # callbacks 里 root 默认 ""
        assert opened == []

    def test_handle_bedrock_task_returns_false_without_tab(self):
        from ui.app_bedrock import BedrockMixin

        owner = FakeOwner(fields={"bedrock_tab": None})
        assert BedrockMixin._handle_bedrock_task(owner, "bedrock_load_error", "x") is False

    def test_handle_bedrock_task_release_count_uses_service(self):
        from ui.app_bedrock import BedrockMixin

        calls = []

        class SpyService(BedrockService):
            def count_release(self, available):
                calls.append(available)
                return super().count_release(available)

        available = [{"type": "release"}, {"type": "preview"}, {"type": "release"}]
        owner = FakeOwner(
            service=SpyService(),
            fields={
                "bedrock_tab": object(),
                "bedrock_available": available,
                "_on_bedrock_filter_change": lambda: None,
            },
        )
        assert BedrockMixin._handle_bedrock_task(owner, "bedrock_available_loaded", available) is True
        assert calls == [available], "release 计数必须走服务"
        # i18n 未初始化时 `_()` 返回键名本身，所以这里只能钉住"投递了这条状态"
        assert owner.status[-1] == ("bedrock_available_status", "success")
        # 再单独钉住计数结果本身（与界面文案解耦）
        assert count_release(available) == 2

