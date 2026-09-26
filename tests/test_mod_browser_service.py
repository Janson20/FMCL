"""``services/mod_browser_service.py`` 的离线单测（阶段 1 任务 1.8-B）。

用假 HTTP/假 API 返回覆盖任务书点名的场景：双源搜索结果归一化与来源统计
（含部分源失败、空结果、异常、字段缺失）、服务端 AI 搜索的去重/排序/失败聚合、
安装目标目录解析（版本隔离组合、目录不存在、无 loader 时的回退）、已装实例匹配
（同名/大小写/缺字段）、四个安装入口的调用与结果判定。

全程不联网、不建窗口、不需要机器上有游戏目录（用 ``tmp_path``）。
"""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from services import mod_browser_service as svc

REPO_ROOT = Path(__file__).resolve().parent.parent


# ─── 假数据 ─────────────────────────────────────────────────────


def _hits(prefix: str, n: int = 3) -> list:
    return [{"title": f"{prefix}-{i}", "project_id": f"{prefix}{i}", "downloads": i} for i in range(n)]


class _Inst:
    """假的"已安装版本"对象（真实实现是 launcher 的 dataclass）。"""

    def __init__(self, folder_name="", vanilla_name="", loader_type=None, has_loader=False):
        self.folder_name = folder_name
        self.vanilla_name = vanilla_name
        self.loader_type = loader_type
        self.has_loader = has_loader


@pytest.fixture()
def fake_curseforge(monkeypatch):
    """把 ``curseforge`` 的三个双源入口换成记录入参的假实现。"""
    import curseforge

    calls = []

    def _make(name):
        def _fake(**kwargs):
            calls.append((name, kwargs))
            return {
                "hits": _hits(name),
                "total_hits": 1500,
                "sources": {"modrinth": 100, "curseforge": 50},
            }

        return _fake

    for name in ("unified_search_mods", "unified_search_resource_packs", "unified_search_shaders"):
        monkeypatch.setattr(curseforge, name, _make(name), raising=False)
    return calls


# ═══════════════════════════════════════════════════════════════════
# 双源搜索（客户端三個标签页）
# ═══════════════════════════════════════════════════════════════════


class TestClientTabSearch:
    def test_mods_tab_passes_loader_and_batch_limit(self, fake_curseforge) -> None:
        outcome = svc.search_client_tab(svc.TAB_MODS, "sodium", "1.20.4", "fabric")
        assert outcome is not None
        name, kwargs = fake_curseforge[0]
        assert name == "unified_search_mods"
        assert kwargs == {"query": "sodium", "game_version": "1.20.4", "mod_loader": "fabric", "offset": 0, "limit": 300}

    @pytest.mark.parametrize(
        "tab_key,expected_name",
        [
            (svc.TAB_RESOURCE_PACKS, "unified_search_resource_packs"),
            (svc.TAB_SHADERS, "unified_search_shaders"),
        ],
    )
    def test_non_mods_tabs_do_not_pass_loader(self, fake_curseforge, tab_key, expected_name) -> None:
        """资源包 / 光影两个标签页的调用**没有** ``mod_loader`` 参数（原文如此）。"""
        outcome = svc.search_client_tab(tab_key, "q", "1.20.4", "fabric")
        assert outcome is not None
        name, kwargs = fake_curseforge[0]
        assert name == expected_name
        assert "mod_loader" not in kwargs, "非模组页不该传加载器（原文不含该参数）"
        assert kwargs["limit"] == 300

    def test_unknown_tab_returns_none_without_calling_backend(self, fake_curseforge) -> None:
        """未知标签页 ↔ 原文的 ``else: return``：什么都不发、也不报错。"""
        assert svc.search_client_tab("nope", "q", None, None) is None
        assert fake_curseforge == []

    def test_client_tabs_covers_exactly_the_three_branches(self) -> None:
        """``CLIENT_TABS`` 是界面"认不认识这个 tab_key"的前置判断依据。"""
        assert svc.CLIENT_TABS == ("mods", "resourcepacks", "shaders")

    def test_result_is_normalized_with_sources(self, fake_curseforge) -> None:
        outcome = svc.search_client_tab(svc.TAB_MODS, "q", None, None)
        assert [h["title"] for h in outcome.hits] == ["unified_search_mods-0", "unified_search_mods-1", "unified_search_mods-2"]
        assert outcome.total_hits == 1500
        assert outcome.sources == {"modrinth": 100, "curseforge": 50}

    def test_empty_result_uses_original_defaults(self, fake_curseforge, monkeypatch) -> None:
        """空结果：``hits=[]`` / ``total_hits=0`` / ``sources={}``（与原文的 get 默认值一致）。"""
        import curseforge

        monkeypatch.setattr(curseforge, "unified_search_mods", lambda **kw: {}, raising=False)
        outcome = svc.search_client_tab(svc.TAB_MODS, "q", None, None)
        assert (outcome.hits, outcome.total_hits, outcome.sources) == ([], 0, {})

    def test_partial_source_missing_key_is_tolerated(self, fake_curseforge, monkeypatch) -> None:
        """部分源失败：后端只回了 hits、没有 total_hits/sources 时按默认值走。"""
        import curseforge

        monkeypatch.setattr(
            curseforge, "unified_search_mods", lambda **kw: {"hits": _hits("only")}, raising=False
        )
        outcome = svc.search_client_tab(svc.TAB_MODS, "q", None, None)
        assert len(outcome.hits) == 3
        assert outcome.total_hits == 0
        assert outcome.sources == {}

    def test_backend_exception_propagates_untouched(self, monkeypatch) -> None:
        """失败**原样抛出**：界面侧那个 ``try/except`` 负责渲染错误态。"""
        import curseforge

        def _boom(**kwargs):
            raise RuntimeError("两个源都挂了")

        monkeypatch.setattr(curseforge, "unified_search_mods", _boom, raising=False)
        with pytest.raises(RuntimeError, match="两个源都挂了"):
            svc.search_client_tab(svc.TAB_MODS, "q", None, None)


class TestAiMergedSearch:
    def test_passes_search_type_and_max_per_keyword(self, monkeypatch) -> None:
        import modrinth

        seen = {}

        def _fake(**kwargs):
            seen.update(kwargs)
            return {"hits": _hits("AI", 2), "keywords": ["kw1", "kw2"]}

        monkeypatch.setattr(modrinth, "ai_merged_search", _fake, raising=False)
        outcome = svc.search_ai_merged_tab(svc.TAB_SHADERS, "好看的光影", "tok", "1.20.4", None)
        assert seen == {
            "query": "好看的光影",
            "token": "tok",
            "search_type": "shaders",
            "game_version": "1.20.4",
            "mod_loader": None,
            "max_per_keyword": 30,
        }
        assert outcome.keywords == ["kw1", "kw2"]
        assert outcome.total_hits == 0  # ai_merged_search 的假返回里没有这个键

    def test_exception_propagates(self, monkeypatch) -> None:
        import modrinth

        def _boom(**kwargs):
            raise ValueError("AI 服务不可用")

        monkeypatch.setattr(modrinth, "ai_merged_search", _boom, raising=False)
        with pytest.raises(ValueError):
            svc.search_ai_merged_tab(svc.TAB_MODS, "q", "t", None, None)


# ═══════════════════════════════════════════════════════════════════
# 服务端模组：单页搜索 + AI 搜索（去重 / 排序 / 失败聚合）
# ═══════════════════════════════════════════════════════════════════


class TestServerSearch:
    def test_page_search_uses_backend_pagination(self, monkeypatch) -> None:
        import modrinth

        seen = {}

        def _fake(**kwargs):
            seen.update(kwargs)
            return {"hits": _hits("srv"), "total_hits": 42}

        monkeypatch.setattr(modrinth, "search_server_mods", _fake, raising=False)
        outcome = svc.search_server_page("q", "1.20.4", "forge", 20, 10)
        assert seen == {"query": "q", "game_version": "1.20.4", "mod_loader": "forge", "offset": 20, "limit": 10}
        assert outcome.total_hits == 42
        assert outcome.sources == {}


class TestServerAiSearch:
    def test_no_keywords_returns_empty_pair(self, monkeypatch) -> None:
        """关键词扩展为空 ↔ 原文的"显示无结果并 return"。"""
        import modrinth

        monkeypatch.setattr(modrinth, "ai_expand_search_keywords", lambda q, t: [], raising=False)
        assert svc.search_server_ai("q", "tok", "1.20.4", "forge") == ([], [])

    def test_dedupes_sorts_and_aggregates_failures(self, monkeypatch) -> None:
        import modrinth

        monkeypatch.setattr(
            modrinth, "ai_expand_search_keywords", lambda q, t: ["kw1", "kw2", "kw3"], raising=False
        )

        def _fake(**kwargs):
            kw = kwargs["query"]
            if kw == "kw2":
                raise RuntimeError("这个词炸了")
            if kw == "kw1":
                return {"hits": [{"project_id": "a", "downloads": 1}, {"project_id": "b", "downloads": 9}]}
            # kw3：与 kw1 有重复 id，另外给一个 downloads 缺失的
            return {"hits": [{"project_id": "b", "downloads": 9}, {"project_id": "c"}]}

        monkeypatch.setattr(modrinth, "search_server_mods", _fake, raising=False)
        hits, keywords = svc.search_server_ai("q", "tok", "1.20.4", "forge")
        assert keywords == ["kw1", "kw2", "kw3"]
        # 去重后剩 a/b/c，按下载量降序（缺 downloads 的算 0）
        assert [h["project_id"] for h in hits] == ["b", "a", "c"]

    def test_hit_without_project_id_is_dropped(self, monkeypatch) -> None:
        import modrinth

        monkeypatch.setattr(modrinth, "ai_expand_search_keywords", lambda q, t: ["k"], raising=False)
        monkeypatch.setattr(
            modrinth,
            "search_server_mods",
            lambda **kw: {"hits": [{"title": "无 id"}, {"project_id": "x"}]},
            raising=False,
        )
        hits, _ = svc.search_server_ai("q", "tok", None, None)
        assert [h.get("project_id") for h in hits] == ["x"]

    def test_all_keywords_fail_returns_empty_hits_but_keeps_keywords(self, monkeypatch) -> None:
        """全部关键词失败时：``hits`` 为空但 ``keywords`` 非空（界面据此渲染空列表）。"""
        import modrinth

        monkeypatch.setattr(modrinth, "ai_expand_search_keywords", lambda q, t: ["k1"], raising=False)

        def _boom(**kwargs):
            raise RuntimeError("网络炸了")

        monkeypatch.setattr(modrinth, "search_server_mods", _boom, raising=False)
        hits, keywords = svc.search_server_ai("q", "tok", None, None)
        assert hits == [] and keywords == ["k1"]

    def test_custom_per_keyword(self, monkeypatch) -> None:
        import modrinth

        seen = []
        monkeypatch.setattr(modrinth, "ai_expand_search_keywords", lambda q, t: ["k"], raising=False)

        def _fake(**kwargs):
            seen.append(kwargs["limit"])
            return {"hits": []}

        monkeypatch.setattr(modrinth, "search_server_mods", _fake, raising=False)
        svc.search_server_ai("q", "t", None, None, per_keyword=7)
        assert seen == [7]


# ═══════════════════════════════════════════════════════════════════
# 安装目标解析与已装匹配
# ═══════════════════════════════════════════════════════════════════


class TestResolveInstallDir:
    def _call(self, tmp_path, tab_key, targets, installed, loader=None, version_id="1.20.4-fabric"):
        callbacks = {"get_minecraft_dir": lambda: str(tmp_path), "get_installed_versions": lambda: installed}
        return svc.resolve_install_dir(
            callbacks,
            tab_key,
            "1.20.4",  # default_game_version（窗口来源版本解析出的 MC 版本）
            version_id,
            lambda _tab: targets[0],
            lambda _tab: loader,
        )

    def test_matches_installed_instance_folder(self, tmp_path) -> None:
        installed = [_Inst("1.20.4-fabric", "1.20.4", "fabric", True)]
        out = self._call(tmp_path, svc.TAB_MODS, ("1.20.4",), installed, loader="fabric")
        assert Path(out) == tmp_path / "versions" / "1.20.4-fabric" / "mods"

    @pytest.mark.parametrize(
        "tab_key,sub",
        [(svc.TAB_RESOURCE_PACKS, "resourcepacks"), (svc.TAB_SHADERS, "shaderpacks")],
    )
    def test_other_tabs_use_their_subdir_and_ignore_loader(self, tmp_path, tab_key, sub) -> None:
        installed = [_Inst("1.20.4", "1.20.4", None, False)]
        out = self._call(tmp_path, tab_key, ("1.20.4",), installed, loader="fabric")
        assert Path(out) == tmp_path / "versions" / "1.20.4" / sub

    def test_falls_back_to_global_dir_when_nothing_matches(self, tmp_path) -> None:
        installed = [_Inst("1.19.2", "1.19.2", "fabric", True)]
        out = self._call(tmp_path, svc.TAB_MODS, ("1.20.4",), installed, loader="fabric")
        assert Path(out) == tmp_path / "mods"

    def test_no_loader_filter_matches_version_only_instance(self, tmp_path) -> None:
        """未指定加载器时走"仅版本匹配"那条路（偏好带加载器的实例）。"""
        installed = [_Inst("1.20.4", "1.20.4", None, False), _Inst("1.20.4-forge", "1.20.4", "forge", True)]
        out = self._call(tmp_path, svc.TAB_MODS, ("1.20.4",), installed, loader=None)
        assert Path(out) == tmp_path / "versions" / "1.20.4-forge" / "mods"

    def test_unknown_tab_key_defaults_to_mods(self, tmp_path) -> None:
        out = self._call(tmp_path, "unknown-tab", (None,), [], loader=None)
        assert Path(out) == tmp_path / "mods"

    def test_missing_minecraft_dir_callback_uses_relative_dot(self) -> None:
        """没有 ``get_minecraft_dir`` 时退回 ``Path(".")``（原文如此）。"""
        out = svc.resolve_install_dir({}, svc.TAB_MODS, None, "v", lambda _t: None, lambda _t: None)
        assert Path(out) == Path(".") / "mods"

    def test_broken_installed_versions_callback_falls_back(self, tmp_path) -> None:
        """取已装列表抛异常时只告警并当作空列表（原文的 ``except``）。"""
        callbacks = {
            "get_minecraft_dir": lambda: str(tmp_path),
            "get_installed_versions": lambda: (_ for _ in ()).throw(RuntimeError("读版本目录失败")),
        }
        out = svc.resolve_install_dir(callbacks, svc.TAB_MODS, "1.20.4", "v", lambda _t: "1.20.4", lambda _t: "fabric")
        assert Path(out) == tmp_path / "mods"


class TestMatchInstalledInstance:
    def _match(self, installed, game_version="1.20.4", loader=None, version_id="my-version"):
        return svc.match_installed_instance({"get_installed_versions": lambda: installed}, version_id, game_version, loader)

    def test_no_game_version_returns_none_without_reading_installed(self) -> None:
        """``game_version`` 为空时**不读**已装列表（原文的第一个 ``if``）。"""
        called = []
        callbacks = {"get_installed_versions": lambda: called.append(1) or []}
        assert svc.match_installed_instance(callbacks, "v", None, "fabric") is None
        assert called == []

    def test_exact_loader_match_prefers_window_version_id(self) -> None:
        installed = [_Inst("other", "1.20.4", "fabric"), _Inst("my-version", "1.20.4", "fabric")]
        assert self._match(installed, loader="fabric") == "my-version"

    def test_exact_loader_match_takes_first_when_window_version_absent(self) -> None:
        installed = [_Inst("a", "1.20.4", "fabric"), _Inst("b", "1.20.4", "fabric")]
        assert self._match(installed, loader="fabric") == "a"

    def test_loader_type_is_case_sensitive(self) -> None:
        """``loader_type`` 精确比较（原文 ``==``），大小写不同就不算命中。"""
        installed = [_Inst("upper", "1.20.4", "Fabric", True), _Inst("lower", "1.20.4", "fabric", True)]
        assert self._match(installed, loader="fabric") == "lower"

    def test_entries_missing_fields_are_skipped(self) -> None:
        class _NoFolder:
            vanilla_name = "1.20.4"
            loader_type = "fabric"

        installed = [_NoFolder(), _Inst("", "1.20.4", "fabric"), _Inst("ok", "1.20.4", "fabric")]
        assert self._match(installed, loader="fabric") == "ok"

    def test_vanilla_name_must_match_exactly(self) -> None:
        installed = [_Inst("x", "1.20.40", "fabric"), _Inst("y", "1.20.4", "fabric")]
        assert self._match(installed, loader="fabric") == "y"

    def test_version_only_path_prefers_has_loader_instance(self) -> None:
        installed = [_Inst("plain", "1.20.4", None, False), _Inst("with-loader", "1.20.4", None, True)]
        assert self._match(installed, loader=None) == "with-loader"

    def test_version_only_path_without_any_loader_instance_takes_first(self) -> None:
        installed = [_Inst("a", "1.20.4", None, False), _Inst("b", "1.20.4", None, False)]
        assert self._match(installed, loader=None) == "a"

    def test_no_match_returns_none(self) -> None:
        assert self._match([_Inst("a", "1.19.2", "fabric")], loader="fabric") is None

    def test_missing_callback_key_means_no_installed_versions(self) -> None:
        assert svc.match_installed_instance({}, "v", "1.20.4", "fabric") is None

    def test_installed_objects_may_be_plain_namespaces(self) -> None:
        """``getattr(inst, ...)`` 语义：任意对象都行（真实实现是 dataclass）。"""
        inst = types.SimpleNamespace(folder_name="ns", vanilla_name="1.20.4", loader_type="fabric")
        assert self._match([inst], loader="fabric") == "ns"


class TestServerModsDir:
    def test_loader_keyword_uses_version_isolation(self, tmp_path) -> None:
        out = svc.server_mods_dir({"get_server_dir": lambda: str(tmp_path)}, "1.20.4-forge")
        assert Path(out) == tmp_path / "1.20.4-forge" / "mods"
        assert Path(out).is_dir(), "原文有建目录的副作用"

    @pytest.mark.parametrize("version_id", ["1.20.4-fabric", "1.20.4-NeoForge", "FABRIC-1.20.4"])
    def test_keyword_match_is_case_insensitive(self, tmp_path, version_id) -> None:
        out = svc.server_mods_dir({"get_server_dir": lambda: str(tmp_path)}, version_id)
        assert Path(out) == tmp_path / version_id / "mods"

    def test_vanilla_version_uses_shared_mods_dir(self, tmp_path) -> None:
        out = svc.server_mods_dir({"get_server_dir": lambda: str(tmp_path)}, "1.20.4")
        assert Path(out) == tmp_path / "mods"
        assert Path(out).is_dir()

    def test_missing_directory_is_created(self, tmp_path) -> None:
        """目录不存在时会被创建（``mkdir(parents=True, exist_ok=True)``）。"""
        target = tmp_path / "nested" / "server"
        out = svc.server_mods_dir({"get_server_dir": lambda: str(target)}, "1.20.4")
        assert Path(out).is_dir()

    def test_missing_callback_uses_relative_dot(self, tmp_path, monkeypatch) -> None:
        """没有 ``get_server_dir`` 时退回 ``Path(".")``（原文如此）。"""
        monkeypatch.chdir(tmp_path)
        out = svc.server_mods_dir({}, "1.20.4")
        assert Path(out) == Path(".") / "mods"
        assert (tmp_path / "mods").is_dir()


# ═══════════════════════════════════════════════════════════════════
# 下载 / 安装编排
# ═══════════════════════════════════════════════════════════════════


class TestInstallOrchestration:
    def test_curseforge_branch_reports_title_as_installed_name(self, monkeypatch, tmp_path) -> None:
        import curseforge

        seen = {}

        def _fake(project_id, **kwargs):
            seen["project_id"] = project_id
            seen.update(kwargs)
            return True, "已安装"

        monkeypatch.setattr(curseforge, "install_mod", _fake, raising=False)
        ok, result, names = svc.install_mod("123", "JEI", "curseforge", "1.20.4", "forge", str(tmp_path))
        assert (ok, result, names) == (True, "已安装", ["JEI"])
        assert seen == {
            "project_id": 123,  # int(project_id)：原文就是 int 转换
            "game_version": "1.20.4",
            "mod_loader": "forge",
            "mods_dir": str(tmp_path),
        }

    def test_curseforge_failure_has_empty_installed_names(self, monkeypatch, tmp_path) -> None:
        import curseforge

        monkeypatch.setattr(curseforge, "install_mod", lambda *a, **k: (False, "下载失败"), raising=False)
        ok, result, names = svc.install_mod("1", "T", "curseforge", "1.20.4", "forge", str(tmp_path))
        assert (ok, result, names) == (False, "下载失败", [])

    def test_modrinth_branch_passes_status_callback_through(self, monkeypatch, tmp_path) -> None:
        import modrinth

        seen = {}
        cb = lambda msg: None  # noqa: E731

        def _fake(project_id, **kwargs):
            seen["project_id"] = project_id
            seen.update(kwargs)
            return True, "ok", ["A", "B", "C"]

        monkeypatch.setattr(modrinth, "install_mod_with_deps", _fake, raising=False)
        ok, result, names = svc.install_mod("pid", "T", "modrinth", "1.20.4", "fabric", str(tmp_path), cb)
        assert (ok, result, names) == (True, "ok", ["A", "B", "C"])
        assert seen["status_callback"] is cb
        assert seen["project_id"] == "pid"

    def test_resource_pack_and_shader_forward_their_directory_kwargs(self, monkeypatch, tmp_path) -> None:
        import modrinth

        seen = {}
        monkeypatch.setattr(
            modrinth,
            "install_resource_pack",
            lambda project_id, **kw: seen.update({"kind": "rp", "project_id": project_id, **kw}) or (True, "rp-ok"),
            raising=False,
        )
        monkeypatch.setattr(
            modrinth,
            "install_shader",
            lambda project_id, **kw: seen.update({"kind": "shader", "project_id": project_id, **kw}) or (False, "失败"),
            raising=False,
        )
        assert svc.install_resource_pack("p1", "1.20.4", str(tmp_path), None) == (True, "rp-ok")
        assert seen["kind"] == "rp" and seen["resourcepacks_dir"] == str(tmp_path)
        assert svc.install_shader("p2", "1.20.4", str(tmp_path), None) == (False, "失败")
        assert seen["kind"] == "shader" and seen["shaderpacks_dir"] == str(tmp_path)

    def test_server_install_has_single_branch(self, monkeypatch, tmp_path) -> None:
        import modrinth

        seen = {}
        monkeypatch.setattr(
            modrinth,
            "install_mod_with_deps",
            lambda project_id, **kw: seen.update({"project_id": project_id, **kw}) or (True, "ok", ["srv"]),
            raising=False,
        )
        out = svc.server_install_mod("p", "1.20.4", "forge", str(tmp_path))
        assert out == (True, "ok", ["srv"])
        assert seen == {
            "project_id": "p",
            "game_version": "1.20.4",
            "mod_loader": "forge",
            "mods_dir": str(tmp_path),
            "status_callback": None,
        }

    def test_installer_exception_propagates(self, monkeypatch, tmp_path) -> None:
        """下载器抛异常时**不吞**：界面侧那个 ``except`` 负责报错文案。"""
        import modrinth

        def _boom(*a, **kw):
            raise ConnectionError("断了")

        monkeypatch.setattr(modrinth, "install_resource_pack", _boom, raising=False)
        with pytest.raises(ConnectionError):
            svc.install_resource_pack("p", "1.20.4", str(tmp_path))


# ═══════════════════════════════════════════════════════════════════
# 服务对象本身
# ═══════════════════════════════════════════════════════════════════


class TestServiceFacade:
    def test_instantiable_without_app_context(self) -> None:
        service = svc.ModBrowserService()
        assert service.attached is False
        assert service.name == "mod_browser"

    def test_facade_delegates(self, fake_curseforge) -> None:
        service = svc.ModBrowserService()
        outcome = service.search_client_tab(svc.TAB_MODS, "q", None, None)
        assert outcome is not None and len(outcome.hits) == 3

    def test_compat_map_is_shared_with_browse_common(self) -> None:
        from services import browse_common

        assert svc.ModBrowserService.MOD_LOADER_COMPAT_MAP is browse_common.MOD_LOADER_COMPAT_MAP
        assert svc.compat_loader("legacyfabric") == "fabric"

    def test_service_modules_import_without_gui(self) -> None:
        """在**干净子进程**里 import 四个新服务，不得拖入任何 GUI 栈。

        与 ``tests/test_layering.py`` 同一手法：``sys.modules`` 是进程级状态，
        同进程内别的测试导入过 ``ui`` 会让判断失真，因此必须用子进程。
        """
        import subprocess
        import sys

        code = (
            "import sys;"
            "import services.browse_common, services.mod_browser_service,"
            " services.modpack_service, services.plugin_browser_service;"
            "bad=[m for m in sys.modules if m.split('.')[0] in ('tkinter','customtkinter','PySide6','ui')];"
            "print('|'.join(sorted(bad)))"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(REPO_ROOT),
            timeout=180,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "", f"服务层拖入了界面栈: {result.stdout.strip()}"
