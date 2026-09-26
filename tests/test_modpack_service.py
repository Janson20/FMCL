"""``services/modpack_service.py`` 的离线单测（阶段 1 任务 1.8-B）。

重点覆盖任务书点名的三块：

1. **整合包六种格式的识别与元数据解析** —— 用 ``tmp_path`` 造**真实结构的 zip**
   （不是打桩），再让服务走一遍 ``launcher/modpack_types.detect_modpack_archive``，
   六个分支各一条；
2. **版本列表构建与排序** —— 分组、组内倒序、伪分组头、未知版本占位文案；
3. **安装编排** —— "统一入口 → 旧格式分发"的判定与 ``(success, result)`` 传递。

全程不联网、不建窗口。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from services import modpack_service as svc


# ─── 造真实 zip ─────────────────────────────────────────────────


def _make_zip(path: Path, files: dict) -> str:
    """按 ``{zip 内路径: 内容}`` 造一个真实的 zip（``str`` 内容按 UTF-8 写入）。"""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in files.items():
            if isinstance(payload, (dict, list)):
                payload = json.dumps(payload, ensure_ascii=False)
            zf.writestr(name, payload)
    return str(path)


@pytest.fixture()
def packs(tmp_path) -> dict:
    """六种格式各造一个**真实结构**的压缩包。"""
    return {
        "mrpack": _make_zip(
            tmp_path / "a.mrpack",
            {"modrinth.index.json": {"name": "Fabulously", "versionId": "1.0.0", "dependencies": {"minecraft": "1.20.4"}}},
        ),
        "multimc": _make_zip(
            tmp_path / "b.zip", {"mmc-pack.json": {"formatVersion": 1, "components": [{"uid": "net.minecraft"}]}}
        ),
        "curseforge": _make_zip(
            tmp_path / "c.zip",
            {"manifest.json": {"minecraft": {"version": "1.20.4"}, "files": [], "name": "CF 包"}},
        ),
        "mcbbs": _make_zip(tmp_path / "d.zip", {"mcbbs.packmeta": {"name": "MCBBS 包", "version": "1.0"}}),
        "hmcl": _make_zip(tmp_path / "e.zip", {"modpack.json": {"name": "HMCL 包", "version": "1.0"}}),
        "launcher_pack": _make_zip(tmp_path / "f.zip", {"modpack.mrpack": "内嵌的整合包"}),
        "generic": _make_zip(
            tmp_path / "g.zip", {".minecraft/versions/1.20.4/1.20.4.json": {"id": "1.20.4"}}
        ),
    }


def _callbacks(**overrides):
    """六个元数据解析器的假回调（默认返回带 name 的字典）。"""
    callbacks = {}
    for key in (
        "get_mrpack_information",
        "get_multimc_pack_info",
        "get_cf_pack_info",
        "get_hmcl_pack_info",
        "get_mcbbs_pack_info",
        "get_compress_pack_info",
    ):
        callbacks[key] = lambda path, _k=key: {"name": f"{_k}:{Path(path).name}", "summary": "s"}
    callbacks.update(overrides)
    return callbacks


# ═══════════════════════════════════════════════════════════════════
# 六种格式：探测 + 元数据
# ═══════════════════════════════════════════════════════════════════


class TestFormatDetection:
    @pytest.mark.parametrize(
        "key,pack_type",
        [
            ("mrpack", "modrinth"),
            ("multimc", "multimc"),
            ("curseforge", "curseforge"),
            ("mcbbs", "mcbbs"),
            ("hmcl", "hmcl"),
            ("launcher_pack", "launcher_pack"),
            ("generic", "generic"),
        ],
    )
    def test_detect_real_archives(self, packs, key, pack_type) -> None:
        detection = svc.detect_pack_format(packs[key])
        assert detection.pack_type == pack_type
        assert detection.format_name  # 人类可读名必须非空

    def test_missing_file_raises_value_error(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="文件不存在"):
            svc.detect_pack_format(str(tmp_path / "nope.zip"))

    def test_bad_zip_raises_value_error(self, tmp_path) -> None:
        bad = tmp_path / "bad.zip"
        bad.write_text("这不是 zip", encoding="utf-8")
        with pytest.raises(ValueError, match="不是有效的 ZIP 文件"):
            svc.detect_pack_format(str(bad))

    def test_unrecognizable_zip_raises_from_detect(self, tmp_path) -> None:
        path = _make_zip(tmp_path / "x.zip", {"random.txt": "hi"})
        with pytest.raises(ValueError, match="无法识别整合包格式"):
            svc.detect_pack_format(path)


class TestLoadPackInfoBranches:
    """六个格式分支各一条：走的是**真实探测结果**，不是手工塞的 pack_type。"""

    @pytest.mark.parametrize(
        "key,expected_format,callback_key",
        [
            ("mrpack", "mrpack", "get_mrpack_information"),
            ("multimc", "multimc", "get_multimc_pack_info"),
            ("curseforge", "curseforge", "get_cf_pack_info"),
            ("mcbbs", "mcbbs", "get_mcbbs_pack_info"),
            ("hmcl", "hmcl", "get_hmcl_pack_info"),
            ("launcher_pack", "compress", "get_compress_pack_info"),
            ("generic", "compress", "get_compress_pack_info"),
        ],
    )
    def test_branch_marks_format_and_uses_the_right_callback(self, packs, key, expected_format, callback_key) -> None:
        seen = {}

        def _spy(path, _k=callback_key):
            seen["key"] = _k
            seen["path"] = path
            return {"name": "包名"}

        info = svc.load_pack_info(_callbacks(**{callback_key: _spy}), packs[key])
        assert seen == {"key": callback_key, "path": packs[key]}
        assert info["format"] == expected_format

    def test_compress_branch_keeps_launcher_pack_format_from_parser(self) -> None:
        """``load_info_compress`` 用 ``info.get("format", "compress")``：解析器自己
        认出 ``launcher_pack`` 时保留它（原文如此，不是笔误）。"""
        callbacks = _callbacks(get_compress_pack_info=lambda path: {"name": "n", "format": "launcher_pack"})
        assert svc.load_info_compress(callbacks, "whatever.zip")["format"] == "launcher_pack"

    def test_six_loaders_are_independent_and_do_not_share_format(self) -> None:
        cb = _callbacks()
        assert svc.load_info_mrpack(cb, "a")["format"] == "mrpack"
        assert svc.load_info_multimc(cb, "a")["format"] == "multimc"
        assert svc.load_info_curseforge(cb, "a")["format"] == "curseforge"
        assert svc.load_info_hmcl(cb, "a")["format"] == "hmcl"
        assert svc.load_info_mcbbs(cb, "a")["format"] == "mcbbs"
        assert svc.load_info_compress(cb, "a")["format"] == "compress"

    def test_missing_callback_key_raises_key_error(self, packs) -> None:
        """回调缺失时抛 ``KeyError``（原文就是 ``self.callbacks[...]`` 硬下标）。"""
        with pytest.raises(KeyError):
            svc.load_pack_info({}, packs["mrpack"])

    def test_unsupported_pack_type_raises_value_error(self, packs, monkeypatch) -> None:
        """格式不在表里 → ``ValueError("不支持的整合包格式: ...")``（原文同一句话）。"""
        import launcher.modpack_types as mt

        fake = mt.ModpackDetectionResult(pack_type="brand_new", format_name="未知格式")
        monkeypatch.setattr(svc, "detect_pack_format", lambda path: fake)
        with pytest.raises(ValueError, match="不支持的整合包格式: 未知格式"):
            svc.load_pack_info(_callbacks(), packs["mrpack"])

    def test_two_calls_do_not_interfere(self, packs) -> None:
        """每次调用都新建映射表（原文在方法内建字典），不会跨调用串味。"""
        first = svc.load_pack_info(_callbacks(), packs["mrpack"])
        second = svc.load_pack_info(_callbacks(), packs["hmcl"])
        assert first["format"] == "mrpack" and second["format"] == "hmcl"


# ═══════════════════════════════════════════════════════════════════
# 版本列表构建
# ═══════════════════════════════════════════════════════════════════


class TestBuildSortedVersionList:
    def test_groups_by_first_game_version_and_sorts_desc(self) -> None:
        versions = [
            {"version_number": "v1", "game_versions": ["1.20.1"], "date_published": "2024-01-01T00:00:00Z"},
            {"version_number": "v2", "game_versions": ["1.21"], "date_published": "2024-05-01T00:00:00Z"},
            {"version_number": "v3", "game_versions": ["1.20.1"], "date_published": "2024-03-01T00:00:00Z"},
        ]
        out = svc.build_sorted_version_list(versions, "<未知>")
        assert out[0] == {"_header": "1.21", "_count": 1}
        assert out[1]["version_number"] == "v2"
        assert out[2] == {"_header": "1.20.1", "_count": 2}
        # 组内按发布时间倒序
        assert [out[3]["version_number"], out[4]["version_number"]] == ["v3", "v1"]

    def test_unknown_version_label_is_used_verbatim(self) -> None:
        """占位文案由界面用 i18n 拼好传进来 —— 服务层不 import ``ui.i18n``。"""
        out = svc.build_sorted_version_list([{"version_number": "v", "game_versions": []}], "未知版本(界面文案)")
        assert out[0] == {"_header": "未知版本(界面文案)", "_count": 1}

    def test_unparsable_labels_fall_back_to_zero_key(self) -> None:
        """``_mc_sort_key`` 切不动时退化成 ``(0,)``，并且**不会抛**（原文的 except）。"""
        versions = [
            {"version_number": "a", "game_versions": ["snapshot-24w14a"]},
            {"version_number": "b", "game_versions": ["1.20"]},
        ]
        out = svc.build_sorted_version_list(versions, "<未知>")
        # 1.20 → (1, 20) 大于 snapshot 的 (0,) ⇒ 排前面
        assert [item.get("_header") for item in out if "_header" in item] == ["1.20", "snapshot-24w14a"]

    def test_numeric_versions_compare_numerically_not_lexically(self) -> None:
        versions = [
            {"version_number": "a", "game_versions": ["1.9"]},
            {"version_number": "b", "game_versions": ["1.10"]},
        ]
        heads = [i["_header"] for i in svc.build_sorted_version_list(versions, "?") if "_header" in i]
        assert heads == ["1.10", "1.9"], "按整数元组比较，1.10 应大于 1.9"

    def test_empty_input_gives_empty_list(self) -> None:
        assert svc.build_sorted_version_list([], "?") == []

    def test_missing_date_sorts_last(self) -> None:
        versions = [
            {"version_number": "no-date", "game_versions": ["1.20"]},
            {"version_number": "dated", "game_versions": ["1.20"], "date_published": "2024-01-01"},
        ]
        out = svc.build_sorted_version_list(versions, "?")
        assert out[1]["version_number"] == "dated"  # 空串倒序排最后


# ═══════════════════════════════════════════════════════════════════
# 搜索 / 版本取数 / 下载
# ═══════════════════════════════════════════════════════════════════


class TestSearchAndDownload:
    def test_search_modpacks_normalizes_result(self, monkeypatch) -> None:
        import modrinth

        seen = {}
        monkeypatch.setattr(
            modrinth,
            "search_modpacks",
            lambda **kw: seen.update(kw) or {"hits": [{"title": "A"}], "total_hits": 9, "sources": {"modrinth": 1}},
            raising=False,
        )
        outcome = svc.search_modpacks("q", 20, 10)
        assert seen == {"query": "q", "offset": 20, "limit": 10}
        assert (outcome.total_hits, outcome.sources) == (9, {"modrinth": 1})

    def test_search_ai_modpacks_passes_modpacks_search_type(self, monkeypatch) -> None:
        import modrinth

        seen = {}
        monkeypatch.setattr(
            modrinth,
            "ai_merged_search",
            lambda **kw: seen.update(kw) or {"hits": [{"title": "AI"}], "keywords": ["k1"]},
            raising=False,
        )
        hits, keywords = svc.search_ai_modpacks("整合包", "tok")
        assert seen == {"query": "整合包", "token": "tok", "search_type": "modpacks", "max_per_keyword": 30}
        assert [h["title"] for h in hits] == ["AI"] and keywords == ["k1"]

    def test_fetch_versions_propagates_errors(self, monkeypatch) -> None:
        import modrinth

        def _boom(project_id):
            raise RuntimeError("版本接口 500")

        monkeypatch.setattr(modrinth, "get_modpack_versions", _boom, raising=False)
        with pytest.raises(RuntimeError):
            svc.fetch_modpack_versions("pid")

    def test_download_modpack_version_forwards_callback(self, monkeypatch) -> None:
        import modrinth

        seen = {}
        monkeypatch.setattr(
            modrinth,
            "download_modpack_file",
            lambda project_id, **kw: seen.update({"project_id": project_id, **kw}) or (True, "/tmp/a.mrpack"),
            raising=False,
        )
        cb = lambda msg: None  # noqa: E731
        out = svc.download_modpack_version("pid", {"version_number": "v1"}, cb)
        assert out == (True, "/tmp/a.mrpack")
        assert seen == {"project_id": "pid", "version_data": {"version_number": "v1"}, "status_callback": cb}


# ═══════════════════════════════════════════════════════════════════
# 安装编排
# ═══════════════════════════════════════════════════════════════════


class TestInstallEntryDispatch:
    def test_unified_entry_wins_and_passes_optional_file_ids(self) -> None:
        seen = {}

        def _unified(path, optional_file_ids=None):
            seen.update({"path": path, "ids": optional_file_ids})
            return True, "统一入口完成"

        assert svc.call_install_entry({"install_modpack": _unified}, "/p/a.mrpack", ["opt1"], "mrpack") == (
            True,
            "统一入口完成",
        )
        assert seen == {"path": "/p/a.mrpack", "ids": ["opt1"]}

    def test_empty_optional_files_become_none(self) -> None:
        """``optional_file_ids=optional_files if optional_files else None``（原文如此）。"""
        seen = {}
        svc.call_install_entry(
            {"install_modpack": lambda path, optional_file_ids=None: seen.update({"ids": optional_file_ids}) or (True, "")},
            "/p/a.mrpack",
            [],
            "mrpack",
        )
        assert seen["ids"] is None

    def test_multimc_fallback_uses_optional_files_keyword(self) -> None:
        seen = {}
        callbacks = {
            "install_multimc_pack": lambda path, optional_files=None: seen.update(
                {"path": path, "files": optional_files}
            )
            or (True, "mmc"),
        }
        assert svc.call_install_entry(callbacks, "/p/b.zip", ["x"], "multimc") == (True, "mmc")
        assert seen == {"path": "/p/b.zip", "files": ["x"]}

    def test_non_multimc_falls_back_to_install_mrpack(self) -> None:
        seen = {}
        callbacks = {
            "install_multimc_pack": lambda *a, **k: pytest.fail("multimc 分支不该被走到"),
            "install_mrpack": lambda path, optional_files=None: seen.update({"path": path, "files": optional_files})
            or (False, "装不上"),
        }
        assert svc.call_install_entry(callbacks, "/p/c.zip", ["y"], "curseforge") == (False, "装不上")
        assert seen == {"path": "/p/c.zip", "files": ["y"]}

    def test_multimc_without_callback_uses_install_mrpack(self) -> None:
        """``format == "multimc"`` 但回调不存在时退回 ``install_mrpack``（原文条件）。"""
        seen = {}
        callbacks = {
            "install_mrpack": lambda path, optional_files=None: seen.update({"path": path, "files": optional_files})
            or (True, "mrpack 兜底"),
        }
        assert svc.call_install_entry(callbacks, "/p/d.zip", [], "multimc") == (True, "mrpack 兜底")
        assert seen["files"] == []

    def test_exception_propagates(self) -> None:
        def _boom(path, optional_file_ids=None):
            raise OSError("磁盘满了")

        with pytest.raises(OSError):
            svc.call_install_entry({"install_modpack": _boom}, "/p/a.mrpack", [], "mrpack")

    def test_server_entry_kwargs(self) -> None:
        seen = {}
        callbacks = {
            "install_mrpack_server": lambda path, optional_files=None, server_name=None: seen.update(
                {"path": path, "files": optional_files, "name": server_name}
            )
            or (True, "服务端完成"),
        }
        assert svc.call_server_install_entry(callbacks, "/p/a.mrpack", ["o"], "我的服务器") == (True, "服务端完成")
        assert seen == {"path": "/p/a.mrpack", "files": ["o"], "name": "我的服务器"}

    def test_server_entry_missing_callback_raises_key_error(self) -> None:
        with pytest.raises(KeyError):
            svc.call_server_install_entry({}, "/p/a.mrpack", [], None)


class TestProgressPercent:
    @pytest.mark.parametrize(
        "data,expected",
        [
            ({"current": 1, "max": 4}, 25.0),
            ({"current": 4, "max": 4}, 100.0),
            ({"current": 0, "max": 0}, 0.0),
            ({"current": 3}, 300.0),  # 缺 max → 按 1 算（原文 max(get("max",1),1)）
            ({}, 0.0),
            (None, 0.0),
        ],
    )
    def test_percent(self, data, expected) -> None:
        assert svc.progress_percent(data) == expected


class TestServiceFacade:
    def test_instantiable_without_app_context(self) -> None:
        service = svc.ModpackService()
        assert service.attached is False
        assert service.name == "modpack"

    def test_facade_delegates_to_module_functions(self, packs) -> None:
        service = svc.ModpackService()
        info = service.load_pack_info(_callbacks(), packs["mrpack"])
        assert info["format"] == "mrpack"
        assert service.progress_percent({"current": 1, "max": 2}) == 50.0
        assert service.detect_pack_format(packs["hmcl"]).pack_type == "hmcl"
