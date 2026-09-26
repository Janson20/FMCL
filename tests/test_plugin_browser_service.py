"""``services/plugin_browser_service.py`` 的离线单测（阶段 1 任务 1.8-B）。

用**假 ``pm`` / 假 ``market``**（不是真实插件系统、不读盘、不联网）覆盖任务书点名的
插件域编排：索引加载、更新检查、搜索+标签过滤、分页判定、已装状态查询，
以及"下载 → 安装 → 授权 → 加载 → 启用"和"从市场更新"两条链路的分支与调用顺序。

插件市场与插件管理器本来就是零 GUI 的根包，服务把它们当**注入依赖**用，
所以这里不需要 ``AppContext``、也不需要 ``tmp_path``。
"""

from __future__ import annotations

import pytest

from services import plugin_browser_service as svc
from services.browse_common import normalize_search, total_pages


# ─── 假依赖 ─────────────────────────────────────────────────────


class _State:
    def __init__(self, value="enabled"):
        self.value = value


class _PermState:
    def __init__(self, ungranted=()):
        self._ungranted = list(ungranted)

    def get_ungranted_permissions(self):
        return list(self._ungranted)


class FakePM:
    """记录调用顺序的假 ``PluginManager``。"""

    def __init__(self, installed=None, state="enabled", install_ok=True, install_msg="装好了", enable_ok=True):
        self.calls: list = []
        self._installed = installed or {}
        self._state = state
        self._install_ok = install_ok
        self._install_msg = install_msg
        self._enable_ok = enable_ok
        self.load_raises: Exception | None = None

    def get_installed_versions_map(self):
        self.calls.append(("get_installed_versions_map",))
        return {"a": "1.0.0"}

    def get_all_plugin_meta(self):
        self.calls.append(("get_all_plugin_meta",))
        return self._installed

    def get_plugin_state(self, pid):
        self.calls.append(("get_plugin_state", pid))
        if self._state is None:
            return None
        return _State(self._state)

    def get_permission_state(self, pid):
        self.calls.append(("get_permission_state", pid))
        return self._perm_state

    def install_from_file(self, fmpl_path, plugin_id):
        self.calls.append(("install_from_file", fmpl_path, plugin_id))
        return self._install_ok, self._install_msg

    def grant_manifest_permissions(self, pid):
        self.calls.append(("grant_manifest_permissions", pid))
        return True, ""

    def load_plugin(self, pid):
        self.calls.append(("load_plugin", pid))
        if self.load_raises is not None:
            raise self.load_raises
        return True, ""

    def enable_plugin(self, pid):
        self.calls.append(("enable_plugin", pid))
        return self._enable_ok, "" if self._enable_ok else "启用被拒绝"

    def update_plugin_from_market(self, pid, progress_callback=None):
        self.calls.append(("update_plugin_from_market", pid, progress_callback))
        return True, "更新好了"

    _perm_state = _PermState()


class FakeMarket:
    """记录入参的假 ``PluginMarket``。"""

    def __init__(self, index=None, error="", tags=None, search_result=None, updates=None, download=(None, "")):
        self.calls: list = []
        self._index = index if index is not None else [{"id": "a"}]
        self._error = error
        self._tags = tags if tags is not None else {"ui": "界面"}
        self._search_result = search_result if search_result is not None else [{"id": "a"}]
        self._updates = updates if updates is not None else {"a": {"has_update": True}}
        self._download = download

    def fetch_index(self, force=False):
        self.calls.append(("fetch_index", force))
        return self._index, self._error

    def search(self, query="", tags=None):
        self.calls.append(("search", query, tags))
        return self._search_result

    def get_available_tags(self):
        self.calls.append(("get_available_tags",))
        return self._tags

    def check_updates(self, installed_versions):
        self.calls.append(("check_updates", installed_versions))
        return self._updates

    def download_plugin(self, plugin_id, progress_callback=None):
        self.calls.append(("download_plugin", plugin_id, progress_callback))
        return self._download


# ═══════════════════════════════════════════════════════════════════
# 索引 / 更新检查 / 过滤 / 分页
# ═══════════════════════════════════════════════════════════════════


class TestIndexAndUpdates:
    def test_fetch_index_forwards_force(self) -> None:
        market = FakeMarket(index=[{"id": "x"}], error="")
        assert svc.fetch_market_index(market, True) == ([{"id": "x"}], "")
        assert market.calls == [("fetch_index", True)]

    def test_fetch_index_error_is_returned_not_raised(self) -> None:
        """原文把 ``error`` 直接显示到状态栏 —— 服务不翻译、不抛。"""
        market = FakeMarket(error="网络不可用")
        plugins, error = svc.fetch_market_index(market)
        assert error == "网络不可用"
        assert plugins == [{"id": "a"}]  # 原文同时把 plugins 也传下去了

    def test_check_market_updates_passes_installed_versions(self) -> None:
        pm, market = FakePM(), FakeMarket()
        assert svc.check_market_updates(pm, market) == {"a": {"has_update": True}}
        assert ("get_installed_versions_map",) in pm.calls
        assert ("check_updates", {"a": "1.0.0"}) in market.calls

    def test_installed_versions_map_passthrough(self) -> None:
        assert svc.installed_versions_map(FakePM()) == {"a": "1.0.0"}

    def test_update_count(self) -> None:
        assert svc.update_count({"a": {"has_update": True}, "b": {"has_update": False}}) == 1
        assert svc.update_count({}) == 0

    def test_update_count_missing_key_raises(self) -> None:
        """记录现状、疑为缺陷：``info["has_update"]`` 是硬下标 → 缺键即 KeyError。

        上游 ``market.check_updates`` 保证每个条目都带这个键，因此本轮只搬家，
        照原文让它抛（不做静默兜底，免得把上游的契约破坏藏起来）。
        """
        with pytest.raises(KeyError):
            svc.update_count({"a": {}})


class TestFilterAndPaging:
    def test_filter_plugins_strips_query_and_builds_tags(self) -> None:
        market = FakeMarket(search_result=[{"id": "hit"}])
        out = svc.filter_plugins(market, "  搜索词  ", "ui")
        assert out == [{"id": "hit"}]
        assert market.calls == [("search", "搜索词", ["ui"])]

    def test_active_tag_none_means_no_tags_filter(self) -> None:
        market = FakeMarket()
        svc.filter_plugins(market, "", None)
        assert market.calls == [("search", "", None)]

    def test_query_none_is_normalized_to_empty(self) -> None:
        """控件理论上不会给 None，但规范化口径与原文 ``(x or "").strip()`` 一致。"""
        market = FakeMarket()
        svc.filter_plugins(market, None, None)  # type: ignore[arg-type]
        assert market.calls == [("search", normalize_search(None), None)]

    def test_available_tags_passthrough(self) -> None:
        market = FakeMarket(tags={"a": "标签A"})
        assert svc.available_tags(market) == {"a": "标签A"}

    @pytest.mark.parametrize(
        "current,delta,total,expected",
        [
            (0, 1, 30, 1),
            (0, 1, 0, None),  # 0 条时只有 1 页 → 不能翻
            (0, -1, 30, None),  # 第一页不能再往前
            (2, 1, 30, None),  # 最后一页不能再往后
            (2, -1, 30, 1),
            (0, 1, 11, 1),
        ],
    )
    def test_page_after_delta(self, current, delta, total, expected) -> None:
        assert svc.page_after_delta(current, delta, total, 10) == expected

    def test_page_after_delta_boundary_uses_same_formula_as_total_pages(self) -> None:
        """右边界与 ``total_pages`` 同式：``new_page < total_pages(total, size)``。"""
        for total in range(0, 40):
            for delta in (-1, 1):
                expected = 1 if (total + 9) // 10 > 1 else None
                got = svc.page_after_delta(0, delta, total, 10)
                assert got == (expected if delta == 1 else None)
                if delta == 1 and total > 10:
                    assert got is not None and got < total_pages(total, 10)


class TestInstalledLookup:
    def test_installed_with_state(self) -> None:
        pm = FakePM(installed={"a": {}}, state="disabled")
        assert svc.installed_lookup(pm, "a") == (True, "disabled")

    def test_not_installed_has_empty_state(self) -> None:
        pm = FakePM(installed={"b": {}})
        assert svc.installed_lookup(pm, "a") == (False, "")

    def test_installed_but_state_unavailable(self) -> None:
        """记录现状、疑为缺陷：``get_plugin_state`` 返回 None 时状态串是空串
        （界面 ``state_labels.get("", ...)`` 会退回"已安装"文案）。"""
        pm = FakePM(installed={"a": {}}, state=None)
        assert svc.installed_lookup(pm, "a") == (True, "")

    def test_none_meta_mapping_is_tolerated(self) -> None:
        class _PM(FakePM):
            def get_all_plugin_meta(self):
                return None

        assert svc.installed_lookup(_PM(), "a") == (False, "")


# ═══════════════════════════════════════════════════════════════════
# 安装 / 更新 / 启用编排
# ═══════════════════════════════════════════════════════════════════


class TestInstallPlugin:
    def test_happy_path_call_order(self) -> None:
        pm = FakePM()
        market = FakeMarket(download=("/tmp/a.fmpl", ""))
        progress = lambda s, c, t: None  # noqa: E731
        ok, msg = svc.install_plugin(pm, market, "a", ["network.socket"], progress)
        assert (ok, msg) == (True, "装好了")
        assert market.calls == [("download_plugin", "a", progress)]
        assert pm.calls == [
            ("install_from_file", "/tmp/a.fmpl", "a"),
            ("grant_manifest_permissions", "a"),
            ("load_plugin", "a"),
            ("enable_plugin", "a"),
        ]

    def test_download_error_short_circuits_before_install(self) -> None:
        pm = FakePM()
        market = FakeMarket(download=(None, "下载失败：404"))
        ok, msg = svc.install_plugin(pm, market, "a", ["x"])
        assert (ok, msg) == (False, "下载失败：404")
        assert pm.calls == [], "下载失败后不得再走安装/授权/启用"

    def test_install_failure_skips_grant_load_enable(self) -> None:
        pm = FakePM(install_ok=False, install_msg="签名校验失败")
        market = FakeMarket(download=("/tmp/a.fmpl", ""))
        ok, msg = svc.install_plugin(pm, market, "a", ["x"])
        assert (ok, msg) == (False, "签名校验失败")
        assert pm.calls == [("install_from_file", "/tmp/a.fmpl", "a")]

    def test_no_permissions_skips_grant(self) -> None:
        """``permissions`` 为空时**不调用** ``grant_manifest_permissions``（原文的 ``if``）。"""
        pm = FakePM()
        market = FakeMarket(download=("/tmp/a.fmpl", ""))
        svc.install_plugin(pm, market, "a", [])
        assert ("grant_manifest_permissions", "a") not in pm.calls
        # 记录现状、疑为缺陷：即使没有权限也照样 load + enable
        assert ("load_plugin", "a") in pm.calls and ("enable_plugin", "a") in pm.calls

    def test_load_or_enable_result_is_ignored(self) -> None:
        """记录现状、疑为缺陷：``load_plugin`` / ``enable_plugin`` 失败时仍报"安装成功"。"""
        pm = FakePM(enable_ok=False)
        market = FakeMarket(download=("/tmp/a.fmpl", ""))
        assert svc.install_plugin(pm, market, "a", []) == (True, "装好了")

    def test_installer_exception_propagates(self) -> None:
        class _Boom(FakePM):
            def install_from_file(self, fmpl_path, plugin_id):
                raise OSError("写盘失败")

        with pytest.raises(OSError):
            svc.install_plugin(_Boom(), FakeMarket(download=("/tmp/a.fmpl", "")), "a", [])


class TestUpdatePlugin:
    def test_forwards_progress_callback(self) -> None:
        pm = FakePM()
        progress = lambda s, c, t: None  # noqa: E731
        assert svc.update_plugin_from_market(pm, "a", progress) == (True, "更新好了")
        assert pm.calls == [("update_plugin_from_market", "a", progress)]

    def test_default_callback_is_none(self) -> None:
        pm = FakePM()
        svc.update_plugin_from_market(pm, "a")
        assert pm.calls == [("update_plugin_from_market", "a", None)]


class TestEnablePlugin:
    def test_enable_target_reads_manifest_permissions(self) -> None:
        pm = FakePM(installed={"a": {"manifest": {"name": "插件A", "permissions": ["filesystem.write"]}}})
        manifest, permissions = svc.enable_target(pm, "a")
        assert manifest["name"] == "插件A"
        assert permissions == ["filesystem.write"]

    def test_enable_target_without_manifest(self) -> None:
        pm = FakePM(installed={"a": {}})
        assert svc.enable_target(pm, "a") == ({}, [])

    def test_unknown_plugin_gives_empty_result(self) -> None:
        assert svc.enable_target(FakePM(installed={}), "ghost") == ({}, [])

    def test_has_ungranted_permissions(self) -> None:
        pm = FakePM()
        pm._perm_state = _PermState(["network.socket"])
        assert svc.has_ungranted_permissions(pm, "a") is True
        pm._perm_state = _PermState([])
        assert svc.has_ungranted_permissions(pm, "a") is False

    def test_has_ungranted_permissions_when_state_missing(self) -> None:
        class _PM(FakePM):
            def get_permission_state(self, pid):
                return None

        assert svc.has_ungranted_permissions(_PM(), "a") is False

    def test_enable_installed_plugin_loads_then_enables(self) -> None:
        pm = FakePM(enable_ok=True)
        assert svc.enable_installed_plugin(pm, "a") == (True, "")
        assert pm.calls == [("load_plugin", "a"), ("enable_plugin", "a")]

    def test_enable_failure_is_returned_not_raised(self) -> None:
        pm = FakePM(enable_ok=False)
        assert svc.enable_installed_plugin(pm, "a") == (False, "启用被拒绝")

    def test_load_exception_is_swallowed_into_false(self) -> None:
        """与安装路径**不同**：这里的 ``try/except`` 会把异常转成 ``(False, str(e))``。"""
        pm = FakePM()
        pm.load_raises = RuntimeError("钩子注册失败")
        ok, msg = svc.enable_installed_plugin(pm, "a")
        assert ok is False and msg == "钩子注册失败"
        assert ("enable_plugin", "a") not in pm.calls


class TestServiceFacade:
    def test_instantiable_without_app_context(self) -> None:
        service = svc.PluginBrowserService()
        assert service.attached is False
        assert service.name == "plugin_browser"

    def test_facade_delegates(self) -> None:
        service = svc.PluginBrowserService()
        pm, market = FakePM(), FakeMarket()
        assert service.fetch_market_index(market) == ([{"id": "a"}], "")
        assert service.update_count({"a": {"has_update": True}}) == 1
        assert service.page_after_delta(0, 1, 30, 10) == 1
        assert service.installed_lookup(pm, "a")[0] is False
