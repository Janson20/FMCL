"""「全家福」成就条件的纯逻辑测试（L-Q5 / D-109）。

背景（这是本次修复的核心事实，写在测试最上面以便后人看懂）：
``ui/windows/resource_manager.py::_check_full_house`` 从它被引入的那次提交
（``fcb1fa1 feat(achievement): 添加多个成就触发点``）起就**永不触发**：

```python
has_datapacks = self._dir_has_content(self._get_resource_dir("datapacks"))
```

而 ``ui/constants.py::RESOURCE_TYPES`` 只有 4 个键（mods / resourcepacks /
saves / shaderpacks），``_get_resource_dir`` 内部就是 ``RESOURCE_TYPES[type]["folder"]``
→ 必然 ``KeyError("datapacks")``，又被同一函数的 ``except Exception: pass`` 吞掉，
于是 ``_check_ach("modder_full_house", ...)`` 那一行**从来没被执行过**。

而成就在 4 种语言里的文案写的是"安装 Forge、Fabric、NeoForge 各一个版本"，
所以按**文案**实现：条件改为"三种加载器各至少装过一个版本"，由
``version_utils.scan_installed_loaders`` / ``has_all_mod_loaders`` 计算。

这里只测纯逻辑（不碰 Tk、不建窗口）。接线在 ``resource_manager.py`` 里，
由 ``tests/test_resource_achievement_wiring.py`` 之类的静态检查守着。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from version_utils import (
    FULL_HOUSE_LOADERS,
    has_all_mod_loaders,
    parse_mod_loader_from_version,
    scan_installed_loaders,
)


def _write_version(mc_dir: Path, version_id: str, libraries: list) -> None:
    """按真实布局写一个版本：versions/<id>/<id>.json"""
    vdir = mc_dir / "versions" / version_id
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{version_id}.json").write_text(
        json.dumps({"id": version_id, "libraries": libraries}), encoding="utf-8"
    )


def _lib(name: str) -> dict:
    """构造一条 libraries 条目（真实 JSON 里就是 name 形如 group:artifact:version）"""
    group, artifact, version = name.split(":")
    return {"name": name, "downloads": {"artifact": {"path": f"{group.replace('.', '/')}/{artifact}/{version}/{artifact}-{version}.jar"}}}


def _vanilla_lib() -> dict:
    return _lib("com.mojang:brigadier:1.0.18")


# ── 真实库名（取自 version_utils 的加载器识别规则）────────────────────
FORGE_LIBS = [_vanilla_lib(), _lib("net.minecraftforge:forge:1.20.4-49.0.26")]
NEOFORGE_LIBS = [_vanilla_lib(), _lib("net.neoforged.fancymodloader:core:1.0.0")]
FABRIC_LIBS = [_vanilla_lib(), _lib("net.fabricmc:fabric-loader:0.15.11")]
QUILT_LIBS = [_vanilla_lib(), _lib("net.fabricmc:fabric-loader:0.15.11"), _lib("org.quiltmc:quilt-loader:0.20.0")]
VANILLA_LIBS = [_vanilla_lib()]


@pytest.fixture()
def mc_dir(tmp_path: Path) -> Path:
    d = tmp_path / ".minecraft"
    (d / "versions").mkdir(parents=True)
    return d


class TestScanInstalledLoaders:
    def test_empty_when_no_versions_dir(self, tmp_path: Path) -> None:
        assert scan_installed_loaders(str(tmp_path / "nope")) == {}

    def test_empty_when_minecraft_dir_missing(self) -> None:
        assert scan_installed_loaders("") == {}

    def test_vanilla_only_is_not_reported(self, mc_dir: Path) -> None:
        _write_version(mc_dir, "1.20.4", VANILLA_LIBS)
        assert scan_installed_loaders(str(mc_dir)) == {}

    def test_detects_each_loader(self, mc_dir: Path) -> None:
        _write_version(mc_dir, "1.20.4-forge-49.0.26", FORGE_LIBS)
        _write_version(mc_dir, "fabric-loader-0.15.11-1.20.4", FABRIC_LIBS)
        _write_version(mc_dir, "1.20.4-neoforge-20.4.234", NEOFORGE_LIBS)
        got = scan_installed_loaders(str(mc_dir))
        assert set(got) == {"forge", "fabric", "neoforge"}
        assert got["forge"] == ["1.20.4-forge-49.0.26"]
        assert got["fabric"] == ["fabric-loader-0.15.11-1.20.4"]
        assert got["neoforge"] == ["1.20.4-neoforge-20.4.234"]

    def test_groups_multiple_versions_of_same_loader(self, mc_dir: Path) -> None:
        _write_version(mc_dir, "1.20.4-forge-49.0.26", FORGE_LIBS)
        _write_version(mc_dir, "1.19.2-forge-43.2.0", FORGE_LIBS)
        got = scan_installed_loaders(str(mc_dir))
        assert got["forge"] == ["1.19.2-forge-43.2.0", "1.20.4-forge-49.0.26"]  # 排序稳定

    def test_missing_json_falls_back_to_folder_name(self, mc_dir: Path) -> None:
        """完全没有 JSON 文件时回退到文件名匹配（与 has_mod_loader_from_json 一致）"""
        (mc_dir / "versions" / "fabric-loader-0.15.11-1.20.4").mkdir(parents=True)
        got = scan_installed_loaders(str(mc_dir))
        assert got == {"fabric": ["fabric-loader-0.15.11-1.20.4"]}

    def test_broken_json_verdict_matches_has_mod_loader_from_json(self, mc_dir: Path) -> None:
        """**一致性测试**：JSON 坏掉时，本函数与 has_mod_loader_from_json 必须同判。

        实测 ``parse_instance_from_json`` 对坏 JSON 返回 ``state='error',
        loader_type=None`` 的实例（不是 None），所以两侧都判"无加载器"。
        这条测试的价值不是"坏 JSON 该判什么"，而是**不许出现两套标准** ——
        否则界面显示"这是 Forge 实例"、成就却说不是。
        """
        from version_utils import has_mod_loader_from_json

        vdir = mc_dir / "versions" / "1.20.4-forge-49.0.26"
        vdir.mkdir(parents=True)
        (vdir / "1.20.4-forge-49.0.26.json").write_text("{ 这不是 JSON", encoding="utf-8")

        got = scan_installed_loaders(str(mc_dir))
        predicate = has_mod_loader_from_json("1.20.4-forge-49.0.26", str(mc_dir))
        assert ("forge" in got) == predicate

    def test_every_reported_version_passes_has_mod_loader_from_json(self, mc_dir: Path) -> None:
        """反向一致性：凡是被盘进来的版本，既有谓词也必须认为是"有加载器"的"""
        from version_utils import has_mod_loader_from_json

        _write_version(mc_dir, "1.20.4-forge-49.0.26", FORGE_LIBS)
        _write_version(mc_dir, "fabric-loader-0.15.11-1.20.4", FABRIC_LIBS)
        _write_version(mc_dir, "1.20.4", VANILLA_LIBS)
        for _loader, ids in scan_installed_loaders(str(mc_dir)).items():
            for vid in ids:
                assert has_mod_loader_from_json(vid, str(mc_dir)) is True, vid

    def test_quilt_is_reported_under_whatever_key_the_engine_picks(self, mc_dir: Path) -> None:
        """加载器种类如实上报、不做过滤；具体键名以识别引擎为准（不写死猜测）"""
        _write_version(mc_dir, "1.20.4-quilt-0.20.0", QUILT_LIBS)
        got = scan_installed_loaders(str(mc_dir))
        assert len(got) == 1
        assert list(got.values())[0] == ["1.20.4-quilt-0.20.0"]

    def test_files_are_not_treated_as_versions(self, mc_dir: Path) -> None:
        (mc_dir / "versions" / "README.txt").write_text("x", encoding="utf-8")
        _write_version(mc_dir, "1.20.4", VANILLA_LIBS)
        assert scan_installed_loaders(str(mc_dir)) == {}


class TestHasAllModLoaders:
    def test_all_three_required_by_default(self, mc_dir: Path) -> None:
        assert FULL_HOUSE_LOADERS == ("forge", "fabric", "neoforge")

    def test_false_on_empty_dir(self, mc_dir: Path) -> None:
        assert has_all_mod_loaders(str(mc_dir)) is False

    def test_false_when_one_missing(self, mc_dir: Path) -> None:
        _write_version(mc_dir, "1.20.4-forge-49.0.26", FORGE_LIBS)
        _write_version(mc_dir, "fabric-loader-0.15.11-1.20.4", FABRIC_LIBS)
        assert has_all_mod_loaders(str(mc_dir)) is False

    def test_true_when_all_three_present(self, mc_dir: Path) -> None:
        _write_version(mc_dir, "1.20.4-forge-49.0.26", FORGE_LIBS)
        _write_version(mc_dir, "fabric-loader-0.15.11-1.20.4", FABRIC_LIBS)
        _write_version(mc_dir, "1.20.4-neoforge-20.4.234", NEOFORGE_LIBS)
        assert has_all_mod_loaders(str(mc_dir)) is True

    def test_empty_requirement_is_not_vacuously_true(self, mc_dir: Path) -> None:
        """空集合不能算满足——否则传错参数会静默解锁成就"""
        assert has_all_mod_loaders(str(mc_dir), loaders=()) is False

    def test_custom_requirement(self, mc_dir: Path) -> None:
        _write_version(mc_dir, "1.20.4-forge-49.0.26", FORGE_LIBS)
        assert has_all_mod_loaders(str(mc_dir), loaders=("forge",)) is True
        assert has_all_mod_loaders(str(mc_dir), loaders=("Forge",)) is True  # 大小写不敏感

    def test_modpack_instance_counted_too(self, mc_dir: Path) -> None:
        """整合包实例（版本隔离目录里的 Forge）同样算数"""
        _write_version(mc_dir, "MyPack", FORGE_LIBS)
        assert has_all_mod_loaders(str(mc_dir), loaders=("forge",)) is True


def test_回归守卫_datapacks_不在_RESOURCE_TYPES_里所以旧实现必然抛异常() -> None:
    """把缺陷的**根因**钉住：旧写法查的键根本不存在。

    如果有人以为"其实只是没装 datapacks 目录"而回退旧实现，这条会立刻失败。
    """
    from ui.constants import RESOURCE_TYPES

    assert "datapacks" not in RESOURCE_TYPES, (
        "RESOURCE_TYPES 里出现了 datapacks —— 若是有意新增该资源类型，"
        "请同时更新本测试与 _check_full_house 的语义说明"
    )
    assert set(RESOURCE_TYPES) == {"mods", "resourcepacks", "saves", "shaderpacks"}
    # 文件名匹配的回退也必须能识别三种加载器（破 JSON 场景）
    assert parse_mod_loader_from_version("1.20.4-forge-49.0.26") == "forge"
    assert parse_mod_loader_from_version("fabric-loader-0.15.11-1.20.4") == "fabric"
    assert parse_mod_loader_from_version("1.20.4-neoforge-20.4.234") == "neoforge"
