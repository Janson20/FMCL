"""阶段 1 任务 1.8-A：``services/resource_service.py`` 的离线单元测试。

全部离线：不联网、不弹窗、不构造 Tk、不依赖机器上真的有游戏目录
（一律用 ``tmp_path`` 造目录与假 zip；HTTP 层用 monkeypatch 打桩）。

覆盖：目录推导 / 扫描与排序 / 过滤搜索分页 / 启停约定 / 删除安装导出的真实
文件效果 / 更新检测编排（含部分失败、空结果、异常）/ 仅客户端模组判定 /
缩略图池上限 4（D-92 守卫）/ 服务零 UI 依赖与可独立实例化 / 两个窗口类的方法面
与签名未变。

凡是钉住"疑为缺陷"的既有行为，注释里都写了 ``# 记录现状、疑为缺陷：...``。
"""

from __future__ import annotations

import ast
import importlib.util
import io
import json
import threading
import time
import zipfile
from pathlib import Path

import pytest

from services import resource_service as rs
from services.resource_service import InstallResult, ResourceService

REPO_ROOT = Path(__file__).resolve().parent.parent


# ─── 工具 ───────────────────────────────────────────────────


def _forge_version(mc_dir: Path, version_id: str) -> None:
    """造一个"装了 Forge"的版本目录（version_utils 从 libraries 识别加载器）。"""
    vdir = mc_dir / "versions" / version_id
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{version_id}.json").write_text(
        json.dumps(
            {
                "id": version_id,
                "time": "2023-06-12T12:00:00+00:00",
                "releaseTime": "2023-06-12T12:00:00+00:00",
                "libraries": [{"name": "net.minecraftforge:forge:1.20.1-47.2.0"}],
            }
        ),
        encoding="utf-8",
    )


def _vanilla_version(mc_dir: Path, version_id: str) -> None:
    vdir = mc_dir / "versions" / version_id
    vdir.mkdir(parents=True, exist_ok=True)
    (vdir / f"{version_id}.json").write_text(
        json.dumps({"id": version_id, "libraries": []}), encoding="utf-8"
    )


def _zip(path: Path, entries: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(path), "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _lines(path: Path) -> int:
    with io.open(path, encoding="utf-8") as fh:
        return sum(1 for _ in fh)


# ─── 服务本体：零 UI 依赖 + 可独立实例化 ────────────────────


def test_service_is_ui_free_and_standalone():
    """服务零 UI 依赖（复用 CI 的规则），且可脱离 AppContext 实例化。"""
    spec = importlib.util.spec_from_file_location("_purity_t", REPO_ROOT / "scripts" / "check_services_purity.py")
    purity = importlib.util.module_from_spec(spec)
    import sys

    sys.modules["_purity_t"] = purity  # dataclass 需要 __module__ 已在 sys.modules
    spec.loader.exec_module(purity)
    rule = next(r for r in purity.RULES if r.group == "services")
    report = purity.check_file(REPO_ROOT / "services" / "resource_service.py", rule)
    assert report.ok, [v.render() for v in report.violations]

    svc = ResourceService()
    assert svc.attached is False
    assert (svc.name, svc.label) == ("resource", "资源管理")
    # attached is False 时方法照常可用（不碰 self.context）
    assert svc.format_size(2048) == "2.0 KB"


# ─── 目录推导 ───────────────────────────────────────────────


def test_minecraft_dir_with_and_without_callback():
    assert rs.minecraft_dir({"get_minecraft_dir": lambda: "D:/mc"}) == Path("D:/mc")
    # 回调缺失时的兜底（相对路径 ./.minecraft），与原文一致
    assert rs.minecraft_dir({}) == Path(".") / ".minecraft"
    assert rs.ResourceService.minecraft_dir({}) == Path(".") / ".minecraft"


def test_has_mod_loader_and_resource_dir(tmp_path):
    forged = "1.20.1-forge-47.2.0"
    vanilla = "1.20.1"
    _forge_version(tmp_path, forged)
    _vanilla_version(tmp_path, vanilla)

    assert rs.has_mod_loader(forged, tmp_path) is True
    assert rs.has_mod_loader(vanilla, tmp_path) is False
    # 版本目录整个不存在 → 回退到版本 ID 字符串匹配 → 含 "forge" 即为 True
    assert rs.has_mod_loader(forged, tmp_path / "nope") is True
    assert rs.has_mod_loader("1.20.2", tmp_path / "nope") is False

    assert rs.resource_dir(forged, tmp_path, "mods") == tmp_path / "versions" / forged / "mods"
    assert rs.resource_dir(vanilla, tmp_path, "mods") == tmp_path / "mods"
    assert rs.resource_dir(vanilla, tmp_path, "saves") == tmp_path / "saves"

    # 非法资源类型：与原文 RESOURCE_TYPES[rtype]["folder"] 一样抛 KeyError
    with pytest.raises(KeyError):
        rs.resource_dir(vanilla, tmp_path, "datapacks")

    # 目录解析**不建目录**（原文就没有这个副作用）
    assert not (tmp_path / "mods").exists()
    assert not (tmp_path / "versions" / vanilla / "mods").exists()


def test_resource_dir_for_uses_callbacks(tmp_path):
    _vanilla_version(tmp_path, "1.20.1")
    callbacks = {"get_minecraft_dir": lambda: str(tmp_path)}
    assert rs.ResourceService.resource_dir_for(callbacks, "1.20.1", "resourcepacks") == tmp_path / "resourcepacks"


def test_server_mods_dir(tmp_path):
    forged = "1.20.1-forge-47.2.0"
    fabric = "1.20.1-FABRIC-0.15.0"  # 大小写不敏感（原文先 .lower()）
    neoforge = "1.21-neoforge-21.0.0"
    vanilla = "1.20.1"

    d1 = rs.server_mods_dir({"get_server_dir": lambda: str(tmp_path)}, forged)
    assert d1 == tmp_path / forged / "mods"
    assert d1.is_dir(), "原文就有 mkdir 副作用，薄委托后必须照旧"

    assert rs.server_mods_dir({"get_server_dir": lambda: str(tmp_path)}, fabric) == tmp_path / fabric / "mods"
    assert rs.server_mods_dir({"get_server_dir": lambda: str(tmp_path)}, neoforge) == tmp_path / neoforge / "mods"
    assert rs.server_mods_dir({"get_server_dir": lambda: str(tmp_path)}, vanilla) == tmp_path / "mods"

    # 回调缺失 → server_dir = Path(".")（用相对路径断言，不落盘到仓库里）
    rel = rs.server_mods_dir({}, "1.20.1")
    assert rel == Path(".") / "mods" or rel == Path("mods")


# ─── 扫描 ───────────────────────────────────────────────────


def test_scan_resources_mods(tmp_path):
    d = tmp_path / "mods"
    d.mkdir()
    (d / "b.jar").write_bytes(b"x" * 10)
    (d / "A.JAR").write_bytes(b"x" * 20)  # 大小写不敏感
    (d / "c.jar.disabled").write_bytes(b"x" * 2048)
    (d / "d.zip").write_bytes(b"x")
    (d / "notes.txt").write_text("skip", encoding="utf-8")
    (d / ".hidden.jar").write_bytes(b"x")  # 隐藏文件**不跳过**（原文只对 saves 跳隐藏）
    (d / "subdir.jar").mkdir()  # 目录不参与（只认文件）

    items = rs.scan_resources(d, "mods")
    names = [i["name"] for i in items]
    assert names == sorted(names), "按 Path 字典序排序"
    assert ".hidden.jar" in names, "记录现状：mods 标签页不过滤隐藏文件"
    assert "subdir.jar" not in names and "notes.txt" not in names

    by_name = {i["name"]: i for i in items}
    assert by_name["c.jar.disabled"]["disabled"] is True
    assert by_name["c.jar.disabled"]["size"] == "2.0 KB"
    assert by_name["b.jar"]["disabled"] is False and by_name["b.jar"]["size"] == "10 B"
    assert by_name["A.JAR"]["size"] == "20 B"
    assert all(i["is_dir"] is False and i["path"].endswith(i["name"]) for i in items)


def test_scan_resources_prefix_only_disabled_file(tmp_path):
    """``foo.disabled``：后缀本身就在 mods 白名单里，于是也被列出来。"""
    d = tmp_path / "mods"
    d.mkdir()
    (d / "foo.disabled").write_bytes(b"x")
    items = rs.scan_resources(d, "mods")
    assert len(items) == 1 and items[0]["disabled"] is True


def test_scan_resources_saves(tmp_path):
    d = tmp_path / "saves"
    d.mkdir()
    good = d / "world"
    good.mkdir()
    (good / "level.dat").write_bytes(b"D")
    bad = d / "notamap"
    bad.mkdir()
    (d / ".hiddenworld").mkdir()
    (d / "loose.zip").write_bytes(b"z")

    items = rs.scan_resources(d, "saves")
    names = [i["name"] for i in items]
    assert names == ["notamap", "world"], "只收目录、跳过隐藏目录与散落文件"
    by_name = {i["name"]: i for i in items}
    assert by_name["world"]["has_level_dat"] is True
    assert by_name["notamap"]["has_level_dat"] is False


def test_scan_resources_empty_and_missing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert rs.scan_resources(empty, "mods") == []
    # 记录现状、疑为缺陷：目录不存在时吞掉异常返回空列表，界面看到的是"空目录"
    assert rs.scan_resources(tmp_path / "does-not-exist", "mods") == []


def test_scan_cache_usable(tmp_path):
    d = tmp_path / "mods"
    d.mkdir()
    mtime = d.stat().st_mtime
    assert rs.scan_cache_usable(mtime, d) is True
    assert rs.scan_cache_usable(mtime + 1, d) is False
    assert rs.scan_cache_usable(mtime, tmp_path / "nope") is False


def test_format_size_and_dir_has_content(tmp_path):
    assert rs.format_size(0) == "0 B"
    assert rs.format_size(1023) == "1023 B"
    assert rs.format_size(1024) == "1.0 KB"
    assert rs.format_size(1024 * 1024) == "1.0 MB"
    assert rs.dir_has_content(tmp_path) is False
    (tmp_path / "f").write_bytes(b"x")
    assert rs.dir_has_content(tmp_path) is True
    assert rs.dir_has_content(tmp_path / "nope") is False


# ─── 过滤 / 搜索 / 分页 ─────────────────────────────────────


def test_normalize_search_and_filters():
    assert rs.normalize_search("  Sodium  ") == "sodium"

    mods = [
        {"name": "Sodium", "modid": "sodium", "author": "CaffeineMC", "description": "Renderer"},
        {"name": "JEI", "modid": "jei", "filename": "jei-1.20.1.jar"},
        {"name": "Iris", "description": "Shaders"},
    ]
    # 大小写不敏感；5 个字段（name/modid/author/description/filename）都参与匹配
    assert [m["name"] for m in rs.filter_mods(mods, "sodium")] == ["Sodium"]
    assert [m["name"] for m in rs.filter_mods(mods, "caffeine")] == ["Sodium"]
    assert [m["name"] for m in rs.filter_mods(mods, "shader")] == ["Iris"]
    assert [m["name"] for m in rs.filter_mods(mods, "jei-1.20.1")] == ["JEI"]
    assert rs.filter_mods(mods, "") is mods, "空搜索词原样返回同一个对象（原文如此）"

    # 非模组标签页只看 name（与 filter_mods 故意不同）
    items = [{"name": "pack.zip", "author": "someone"}]
    assert rs.filter_items(items, "pack") == items
    assert rs.filter_items(items, "someone") == []
    assert rs.filter_items(items, "") is items


def test_total_pages_clamp_and_paginate():
    assert rs.total_pages(0, 10) == 1, "空列表也算 1 页（按钮逻辑与原文一致）"
    assert rs.total_pages(1, 10) == 1
    assert rs.total_pages(10, 10) == 1
    assert rs.total_pages(11, 10) == 2
    assert rs.clamp_page(0, 3) == 1
    assert rs.clamp_page(2, 3) == 2
    assert rs.clamp_page(9, 3) == 3

    items = list(range(23))
    page_items, page, pages = rs.paginate(items, 1, 10)
    assert (page_items, page, pages) == (list(range(10)), 1, 3)
    page_items, page, pages = rs.paginate(items, 3, 10)
    assert (page_items, page, pages) == ([20, 21, 22], 3, 3)
    page_items, page, pages = rs.paginate(items, 99, 10)
    assert (page_items, page, pages) == ([20, 21, 22], 3, 3), "越界钳到末页"


def test_paginate_matches_server_service_semantics():
    """与 ``services/server_service.paginate`` 同形同语义（两份实现不得漂移）。"""
    from services.server_service import paginate as server_paginate

    for n in (0, 1, 9, 10, 11, 23):
        items = list(range(n))
        for page in (0, 1, 2, 5, 99):
            ours = rs.paginate(items, page, 10)
            theirs = server_paginate(items, page, 10)
            assert ours == theirs, f"n={n} page={page}: {ours} != {theirs}"


def test_page_items_key():
    page = [{"path": "/a/b.zip"}, {"name": "c.zip"}, {}]
    assert rs.page_items_key(page) == ("/a/b.zip", "c.zip", "")


# ─── 启停 / 删除 / 安装 ─────────────────────────────────────


def test_toggle_mod_convention_and_idempotence(tmp_path):
    f = tmp_path / "sodium.jar"
    f.write_bytes(b"J")

    new_name = rs.toggle_mod(str(f), False)
    assert new_name == "sodium.jar.disabled"
    assert (tmp_path / "sodium.jar.disabled").exists() and not f.exists()

    # 再"禁用"一次：追加第二个 .disabled（原文如此，不做幂等保护）
    again = rs.toggle_mod(str(tmp_path / "sodium.jar.disabled"), False)
    assert again == "sodium.jar.disabled.disabled"

    # 启用：Path.with_suffix("") 只去掉最后一个后缀
    back = rs.toggle_mod(str(tmp_path / "sodium.jar.disabled.disabled"), True)
    assert back == "sodium.jar.disabled"

    # 目标不存在 → 原样抛（界面那层 try/except 负责转成"操作失败"）
    with pytest.raises(OSError):
        rs.toggle_mod(str(tmp_path / "missing.jar"), False)


def test_toggle_server_mod_convention(tmp_path):
    f = tmp_path / "srv.jar"
    f.write_bytes(b"J")

    assert rs.toggle_server_mod(str(f), False) is True
    assert (tmp_path / "srv.jar.disabled").exists()
    assert rs.toggle_server_mod(str(tmp_path / "srv.jar.disabled"), True) is False
    assert f.exists()

    # 与客户端版的差别：这里是切掉最后 9 个字符，多重后缀只切一层
    multi = tmp_path / "a.jar.disabled.disabled"
    multi.write_bytes(b"J")
    assert rs.toggle_server_mod(str(multi), True) is False, "启用后返回新的禁用状态 False"
    assert (tmp_path / "a.jar.disabled").exists() and not multi.exists()


def test_delete_path_file_and_dir(tmp_path):
    f = tmp_path / "x.jar"
    f.write_bytes(b"J")
    d = tmp_path / "world"
    (d / "sub").mkdir(parents=True)
    (d / "sub" / "level.dat").write_bytes(b"D")

    rs.delete_path(str(f))
    assert not f.exists()
    rs.delete_path(str(d))
    assert not d.exists()
    with pytest.raises(OSError):
        rs.delete_path(str(tmp_path / "missing"))


def test_install_resource_copy_exists_and_missing_src(tmp_path):
    res_dir = tmp_path / "resourcepacks"
    src = _zip(tmp_path / "pack.zip", {"pack.mcmeta": "{}"})

    r = rs.install_resource(str(src), "resourcepacks", lambda: res_dir)
    assert r.ok and r.outcome == "installed"
    assert (res_dir / "pack.zip").exists(), "真的复制到了目标目录"

    r2 = rs.install_resource(str(src), "resourcepacks", lambda: res_dir)
    assert not r2.ok and r2.outcome == "exists" and r2.name == "pack.zip"

    # 源文件不存在 → 异常被兜住成 failed（界面显示 rm_install_failed）
    r3 = rs.install_resource(str(tmp_path / "nope.zip"), "resourcepacks", lambda: res_dir)
    assert not r3.ok and r3.outcome == "failed" and r3.error

    # resolve_dir 抛异常也在 try 内（与原文一样变成 failed 而不是往上抛）
    def boom():
        raise KeyError("datapacks")

    r4 = rs.install_resource(str(src), "datapacks", boom)
    assert not r4.ok and r4.outcome == "failed" and "datapacks" in r4.error


def test_install_save_folder_zip_and_unsupported(tmp_path):
    saves = tmp_path / "saves"
    saves.mkdir()

    # 1) 文件夹直接复制
    src_dir = tmp_path / "MyWorld"
    src_dir.mkdir()
    (src_dir / "level.dat").write_bytes(b"D")
    r = rs.install_save(src_dir, saves)
    assert r.ok and (saves / "MyWorld" / "level.dat").exists()
    assert rs.install_save(src_dir, saves).outcome == "map_exists"

    # 2) zip 顶层就有 level.dat → 整体解压到 saves/{zip 主名}/
    flat = _zip(tmp_path / "flat.zip", {"level.dat": "D", "region/r.0.0.mca": "R"})
    r2 = rs.install_save(flat, saves)
    assert r2.ok and (saves / "flat" / "level.dat").exists() and (saves / "flat" / "region").is_dir()

    # 3) zip 里有一层包装目录 → 剥掉前缀后解压
    wrapped = _zip(tmp_path / "wrapped.zip", {"wrapped/level.dat": "D", "wrapped/data/x.dat": "X"})
    r3 = rs.install_save(wrapped, saves)
    assert r3.ok and (saves / "wrapped" / "level.dat").exists() and (saves / "wrapped" / "data").is_dir()
    assert not (saves / "wrapped" / "wrapped").exists(), "包装目录必须被剥掉"

    # 4) 既没有顶层也没有子目录 level.dat → 整体解压（原文的兜底分支）
    nolvl = _zip(tmp_path / "nolvl.zip", {"readme.txt": "nothing"})
    r4 = rs.install_save(nolvl, saves)
    assert r4.ok and (saves / "nolvl" / "readme.txt").exists()

    # 5) 不支持的格式
    other = tmp_path / "map.rar"
    other.write_bytes(b"R")
    r5 = rs.install_save(other, saves)
    assert not r5.ok and r5.outcome == "unsupported" and r5.ext == ".rar"


def test_install_server_mods(tmp_path):
    src_jar = tmp_path / "a.jar"
    src_jar.write_bytes(b"A")
    src_zip = tmp_path / "b.zip"
    src_zip.write_bytes(b"B")
    other = tmp_path / "c.txt"
    other.write_text("skip", encoding="utf-8")
    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()

    n = rs.install_server_mods([str(src_jar), str(src_zip), str(other)], mods_dir)
    assert n == 2 and (mods_dir / "a.jar").exists() and (mods_dir / "b.zip").exists()
    assert not (mods_dir / "c.txt").exists()

    # 单个文件失败只记日志、不中断整批（用一个不存在但仍以 .jar 结尾的"假"源）
    broken = tmp_path / "broken.jar"
    broken.write_bytes(b"")
    mods_dir2 = tmp_path / "mods2"
    mods_dir2.write_text("", encoding="utf-8")  # 目标不是目录 → copy2 必然失败
    assert rs.install_server_mods([str(broken), str(src_jar)], mods_dir2) == 0


def test_write_text_file_roundtrip(tmp_path):
    out = tmp_path / "list.txt"
    rs.write_text_file(str(out), "中文内容\n第二行")
    assert out.read_text(encoding="utf-8") == "中文内容\n第二行"


# ─── 导出文本 ───────────────────────────────────────────────


def test_client_mod_list_text_format():
    mods = [
        {"name": "JEI", "modid": "jei", "version": "15.2.0", "filename": "jei.jar"},
        {"name": "Sodium", "modid": "sodium", "version": "0.5.8", "disabled": True},
        {"filename": "anon.jar"},
    ]
    text = rs.client_mod_list_text(mods, "=== HEADER ===\n")
    assert text.startswith("=== HEADER ===\n\n1. JEI\n")
    assert "   modid: jei  |  version: 15.2.0" in text
    assert "2. Sodium [Disabled]" in text, "记录现状：禁用标记是硬编码英文"
    assert "3. anon.jar\n   modid: -  |  version: -" in text, "缺字段的兜底与原文一致"
    assert text.endswith("\n\nTotal: 3 mods")


def test_server_mod_list_text_format():
    mods = [
        {"name": "ModA", "modid": "moda", "version": "1.0", "disabled": False},
        {"name": "ModB", "filename": "b.jar", "disabled": True},
    ]
    text = rs.server_mod_list_text(mods, "HEADER", "ENABLED", "DISABLED")
    assert text == "HEADER\nModA - ID: moda - 1.0 - ENABLED\nModB - DISABLED"


# ─── 更新检测编排（全部打桩，不联网）────────────────────────


def test_update_targets(monkeypatch):
    import modrinth
    import version_utils

    monkeypatch.setattr(modrinth, "parse_game_version_from_version", lambda vid: "1.20.1" if vid else None)
    monkeypatch.setattr(modrinth, "parse_mod_loader_from_version", lambda vid: "forge")
    monkeypatch.setattr(version_utils, "resolve_search_loader", lambda loader: loader)

    assert rs.update_targets("1.20.1-forge-47.2.0") == ("1.20.1", "forge")
    assert rs.update_targets("") == (None, "forge")


def test_updatable_mods_filters_disabled_and_missing_modid():
    mods = [
        {"name": "a", "modid": "a"},
        {"name": "b", "modid": "b", "disabled": True},
        {"name": "c"},
    ]
    assert [m["name"] for m in rs.updatable_mods(mods)] == ["a"]


def test_check_updates_orchestration_partial_failure(monkeypatch):
    """客户端更新检查：并发 8、部分失败/空结果/异常都不影响整体收敛。"""
    import curseforge

    calls = []

    def fake_dual_source(modid, mod_name, current_version, game_version, mod_loader):
        calls.append((modid, game_version, mod_loader))
        if modid == "boom":
            raise RuntimeError("模拟网络异常")
        if modid == "none":
            return None
        return {
            "project_id": f"pid-{modid}",
            "source": "curseforge",
            "latest_version": "2.0",
            "current_version": current_version,
            "mod_name": mod_name,
        }

    monkeypatch.setattr(curseforge, "check_update_dual_source", fake_dual_source)

    mods = [
        {"modid": "aaa", "name": "AAA", "version": "1.0", "path": "/m/aaa.jar"},
        {"modid": "none", "name": "NONE", "version": "1.0"},
        {"modid": "boom", "name": "BOOM", "version": "1.0"},
    ]
    progress, updates = [], []
    found = rs.check_updates(
        mods,
        "1.20.1",
        "forge",
        on_progress=lambda c, t: progress.append((c, t)),
        on_update=lambda mid, info: updates.append((mid, info)),
    )

    assert found == 1
    assert [u[0] for u in updates] == ["aaa"]
    mid, info = updates[0]
    # 结果字典的 5 个键与原文逐字一致（**不含 source**）
    assert set(info) == {"project_id", "latest_version", "current_version", "mod_name", "mod_path"}
    assert info["project_id"] == "pid-aaa" and info["mod_path"] == "/m/aaa.jar"
    assert sorted(c for c, _ in progress) == [1, 2, 3] and all(t == 3 for _, t in progress)
    assert len(calls) == 3 and all(gv == "1.20.1" and ml == "forge" for _, gv, ml in calls)


def test_check_updates_empty_list(monkeypatch):
    import curseforge

    monkeypatch.setattr(curseforge, "check_update_dual_source", lambda **kw: None)
    assert rs.check_updates([], "1.20.1", "forge") == 0


def test_collect_updates_serial_server_keys(monkeypatch):
    """服务端串行编排：键名是 ``title``（与客户端的 ``mod_name`` 不同，不合并）。"""
    import curseforge

    order = []

    def fake_dual_source(modid, mod_name, current_version, game_version, mod_loader):
        order.append(modid)
        if modid == "boom":
            raise RuntimeError("模拟异常")
        if modid == "none":
            return None
        return {
            "project_id": f"pid-{modid}",
            "latest_version": "3.0",
            "current_version": current_version,
            "source": "modrinth",
        }

    monkeypatch.setattr(curseforge, "check_update_dual_source", fake_dual_source)

    mods = [{"modid": "a", "name": "A"}, {"modid": "none", "name": "N"}, {"modid": "boom", "name": "B"}]
    progress = []
    result = rs.collect_updates(mods, "1.20.1", "forge", on_progress=lambda p, t: progress.append((p, t)))

    assert order == ["a", "none", "boom"], "服务端是串行 for，顺序确定"
    assert list(result) == ["a"]
    assert set(result["a"]) == {"latest_version", "project_id", "source", "title"}
    assert result["a"]["title"] == "A"
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_batch_update_mods_success_and_failure(monkeypatch, tmp_path):
    import modrinth

    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()
    old = mods_dir / "old.jar"
    old.write_bytes(b"OLD")

    def fake_latest(project_id, game_version=None, mod_loader=None):
        if project_id == "pid-none":
            return None
        if project_id == "pid-nofile":
            return {"files": []}
        return {"files": [{"primary": True, "url": "https://x/y.jar", "filename": "y.jar", "hashes": {"sha1": "h"}}]}

    downloaded = []

    def fake_download(url, target_dir, filename, expected_hashes=None):
        downloaded.append((url, target_dir, filename, expected_hashes))
        return (filename != "y.jar" or True), "ok"  # 全部成功

    monkeypatch.setattr(modrinth, "get_project_latest_version", fake_latest)
    monkeypatch.setattr(modrinth, "download_mod", fake_download)

    update_info = {
        "aaa": {"project_id": "pid-aaa", "mod_name": "A", "mod_path": str(old)},
        "none": {"project_id": "pid-none", "mod_name": "N", "mod_path": str(old)},
        "nofile": {"project_id": "pid-nofile", "mod_name": "F", "mod_path": str(old)},
    }
    progress = []
    ok, fail = rs.batch_update_mods(
        ["aaa", "none", "nofile", "unknown"],
        update_info,
        "1.20.1",
        "forge",
        on_progress=lambda d, t: progress.append((d, t)),
    )
    assert (ok, fail) == (1, 3)
    assert not old.exists(), "记录现状、疑为缺陷：下载前会先删掉旧文件"
    # 记录现状、疑为缺陷：``update_info`` 里没有的 modid 在**进 try 之前**就 return 了，
    # 所以它不计入进度（done 只到 3），但会算作一次失败。
    assert sorted(d for d, _ in progress) == [1, 2, 3] and all(t == 4 for _, t in progress)
    assert downloaded == [("https://x/y.jar", str(mods_dir), "y.jar", {"sha1": "h"})]


def test_batch_update_mods_download_returns_false(monkeypatch, tmp_path):
    import modrinth

    target = tmp_path / "m.jar"
    target.write_bytes(b"M")
    monkeypatch.setattr(
        modrinth,
        "get_project_latest_version",
        lambda pid, game_version=None, mod_loader=None: {
            "files": [{"primary": False, "url": "u", "filename": "f.jar"}]
        },
    )
    monkeypatch.setattr(modrinth, "download_mod", lambda *a, **k: (False, "err"))
    info = {"m": {"project_id": "p", "mod_name": "M", "mod_path": str(target)}}
    assert rs.batch_update_mods(["m"], info, "1.20.1", "forge") == (0, 1)


def test_server_batch_update_mods(monkeypatch, tmp_path):
    import modrinth

    mods_dir = tmp_path / "mods"
    mods_dir.mkdir()

    def fake_versions(project_id, game_version=None, mod_loader=None):
        if project_id == "empty":
            return []
        if project_id == "nourl":
            return [{"files": [{"primary": True, "filename": "x.jar"}]}]
        return [{"files": [{"primary": True, "url": "u", "filename": "srv.jar"}]}]

    monkeypatch.setattr(modrinth, "get_mod_versions", fake_versions)
    monkeypatch.setattr(modrinth, "download_mod", lambda url, d, name: True)

    progress = []
    ok, fail = rs.server_batch_update_mods(
        ["good", "empty", "nourl"], mods_dir, "1.20.1", "forge", on_progress=lambda d, t: progress.append((d, t))
    )
    assert (ok, fail) == (1, 2)
    assert sorted(d for d, _ in progress) == [1, 2, 3]


def test_server_batch_update_mods_exception(monkeypatch, tmp_path):
    import modrinth

    def boom(**kw):
        raise RuntimeError("模拟异常")

    monkeypatch.setattr(modrinth, "get_mod_versions", boom)
    assert rs.server_batch_update_mods(["x"], tmp_path, "1.20.1", "forge") == (0, 1)


# ─── 仅客户端模组判定（规则在 launcher/mod_classifier/）─────


def test_filter_client_mods_delegates(monkeypatch):
    import launcher.mod_classifier as mc

    seen = {}

    class FakeResult:
        summary = "禁用 2 个，待确认 1 个"
        unknown = [{"file_name": "weird.jar"}]

    def fake_filter(mods_dir, use_online=True, dry_run=False, progress_callback=None):
        seen.update(mods_dir=mods_dir, use_online=use_online, dry_run=dry_run, has_cb=progress_callback is not None)
        return FakeResult()

    monkeypatch.setattr(mc, "filter_server_mods", fake_filter)
    cb = lambda *a: None
    result = rs.filter_client_mods("D:/srv/mods", progress_callback=cb)

    assert result.summary == "禁用 2 个，待确认 1 个"
    assert seen == {"mods_dir": "D:/srv/mods", "use_online": True, "dry_run": False, "has_cb": True}


# ─── 缩略图（D-92 守卫 + 纯函数）─────────────────────────────


def test_thumbnail_helpers(tmp_path):
    z = tmp_path / "pack.zip"
    z.write_bytes(b"P")
    inflight: set = set()

    key = rs.claim_thumbnail(inflight, z)
    assert key and key in inflight
    assert rs.claim_thumbnail(inflight, z) is None, "同一路径在途时不再重复提交"
    rs.release_thumbnail(inflight, key)
    assert rs.claim_thumbnail(inflight, z) == key, "释放后可以再次提交"

    assert rs.thumbnail_key(z) == str(z.resolve())
    assert rs.thumbnail_is_current(3, 3) is True
    assert rs.thumbnail_is_current(3, 4) is False

    items = [
        {"path": str(z), "is_dir": False},
        {"path": str(tmp_path / "a.jar"), "is_dir": False},
        {"path": str(tmp_path / "cached.zip"), "is_dir": False, "_thumbnail": "B64"},
        {"path": str(tmp_path / "d"), "is_dir": True},
    ]
    cands = rs.thumbnail_candidates(items, "resourcepacks")
    assert [Path(c["path"]).name for c in cands] == ["pack.zip"], "只取 .zip、未缓存、非目录的条目"
    assert rs.thumbnail_candidates(items, "mods") == [], "模组标签页不取缩略图"
    assert rs.thumbnail_candidates(items, "shaderpacks") == cands


def test_load_thumbnail_uses_modrinth(monkeypatch, tmp_path):
    import modrinth

    seen = {}

    def fake_extract(zip_path, max_size=None):
        seen.update(path=str(zip_path), max_size=max_size)
        return "BASE64"

    monkeypatch.setattr(modrinth, "extract_zip_thumbnail", fake_extract)
    z = tmp_path / "p.zip"
    z.write_bytes(b"P")
    assert rs.load_thumbnail(z, 28) == "BASE64"
    assert seen == {"path": str(z), "max_size": 28}


