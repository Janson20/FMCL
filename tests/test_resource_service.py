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
import tokenize
import zipfile
from pathlib import Path

import pytest

from services import resource_service as rs
from services.resource_service import InstallResult, ResourceService

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_RESOURCE = REPO_ROOT / "ui" / "windows" / "resource_manager.py"
UI_SERVER = REPO_ROOT / "ui" / "windows" / "server_resource_manager.py"


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


def test_resource_type_mirror_matches_ui_constants():
    """服务层的目录/扩展名镜像不得与 ``ui/constants.py`` 漂移。"""
    from ui.constants import RESOURCE_TYPES

    assert rs.RESOURCE_FOLDERS == {k: v["folder"] for k, v in RESOURCE_TYPES.items()}
    assert rs.RESOURCE_EXTENSIONS == {k: set(v["extensions"]) for k, v in RESOURCE_TYPES.items()}
    for rtype in RESOURCE_TYPES:
        assert rs.resource_extensions(rtype) == set(RESOURCE_TYPES[rtype]["extensions"])


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


def test_pagination_count_comes_from_cached_items_not_backend_total_hits():
    """D-103 同型缺陷的守卫：页数只能用**缓存条数**算，不能用后端 total_hits。

    ``resource_manager.py`` 的更新弹窗就是按 ``len(sorted_items)``（本地缓存）分页的；
    这里用一个"后端说有 150 条、本地只缓存了 12 条"的场景钉住该行为。
    """
    cached = [("mod%d" % i, {"mod_name": "m%d" % i}) for i in range(12)]
    backend_response = {"total_hits": 150, "hits": cached}

    pages = rs.total_pages(len(cached), 10)
    assert pages == 2, "12 条缓存 → 2 页；若误用 total_hits 会算出 1 / 15"

    page_items, page, _ = rs.paginate(cached, 2, 10)
    assert len(page_items) == 2 and page == 2
    assert rs.total_pages(len(backend_response["hits"]), 10) == 2

    # 静态钉子：窗口代码里不得出现 total_hits 这个标识符（注释/字符串里提及不算）
    for path in (UI_RESOURCE, UI_SERVER):
        names = {
            tok.string
            for tok in tokenize.generate_tokens(io.open(path, encoding="utf-8").readline)
            if tok.type in (tokenize.NAME, tokenize.STRING) and tok.type == tokenize.NAME
        }
        assert "total_hits" not in names, f"{path.name} 里出现了 total_hits"


def test_window_page_count_always_from_local_len():
    """窗口里的页数一律由**本地条数**算出（``len(...)`` 或同函数内 ``x = len(...)``）。

    机械钉子：每个 ``service.total_pages(第一实参, ...)`` 的第一实参必须能追溯到
    某个 ``len(...)``——即"缓存条数"，而不是后端返回的总数（D-103 同型缺陷）。
    """
    for path, cls_name, expected in (
        (UI_RESOURCE, "ResourceManagerWindow", 6),
        (UI_SERVER, "ServerResourceManagerWindow", 1),
    ):
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
        seen = 0
        for func in [n for n in ast.walk(cls) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            # 该函数里所有"值为 len(...)"的局部名
            len_names = {
                tgt.id
                for node in ast.walk(func)
                if isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", "") == "len"
                for tgt in node.targets
                if isinstance(tgt, ast.Name)
            }
            for call in ast.walk(func):
                if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)):
                    continue
                if call.func.attr != "total_pages":
                    continue
                seen += 1
                assert len(call.args) == 2, ast.dump(call)
                first = call.args[0]
                from_len = (
                    isinstance(first, ast.Call) and getattr(first.func, "id", "") == "len"
                ) or (isinstance(first, ast.Name) and first.id in len_names)
                assert from_len, f"{cls_name} 的页数没走本地 len(...)：{ast.unparse(call)}"
                assert "total_hits" not in ast.unparse(call)
        assert seen == expected, f"{cls_name} 的 total_pages 调用点数量变了：{seen}"
        # 服务端窗口是"过滤 + 分页"一体的那份，用 paginate 整体算；
        # 客户端窗口沿用原文的 start/end 切片，所以这里只对服务端断言。
        if cls_name == "ServerResourceManagerWindow":
            assert any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "paginate"
                for n in ast.walk(cls)
            ), f"{cls_name} 应当用 paginate 算分页"


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


class _ThumbWindow:
    """只带缩略图池所需属性的假窗口（借用真实类上的方法）。"""

    from ui.windows.resource_manager import ResourceManagerWindow as _RM

    _THUMB_WORKERS = _RM._THUMB_WORKERS
    _thumb_runner = _RM._thumb_runner
    _release_thumb_pool = _RM._release_thumb_pool

    def __init__(self):
        self._thumb_pool = None


def test_thumbnail_pool_cap_is_four_d92_guard():
    """D-92 守卫：上限 4，且真实并发不超过 4（原实现是"每个 zip 一个线程"）。"""
    from ui.windows.resource_manager import ResourceManagerWindow as RM

    assert rs.THUMB_WORKERS == 4
    assert RM._THUMB_WORKERS == ResourceService.THUMB_WORKERS == rs.THUMB_WORKERS == 4
    # 界面侧不得再写字面量 4（上限只能有一个真源）
    src = io.open(UI_RESOURCE, encoding="utf-8").read()
    assert "_THUMB_WORKERS = THUMB_WORKERS" in src and "_THUMB_WORKERS = 4" not in src

    win = _ThumbWindow()
    pool = win._thumb_runner()
    assert pool.max_workers == 4
    assert win._thumb_runner() is pool, "池是惰性创建并复用的"

    lock = threading.Lock()
    active = [0]
    peak = [0]

    def job():
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1

    handles = [pool.submit(job) for _ in range(12)]
    for h in handles:
        h.wait(5.0)
    assert peak[0] == 4, f"12 个任务并发峰值必须是 4，实际 {peak[0]}"
    win._release_thumb_pool()
    assert win._thumb_pool is None


# ─── 拖拽解析与成就 ─────────────────────────────────────────


def test_parse_drop_paths_and_accept(tmp_path):
    a = tmp_path / "a.zip"
    a.write_bytes(b"A")
    b_dir = tmp_path / "b world"  # 路径里有空格 → 必须用 {} 包裹
    b_dir.mkdir()
    raw = "{" + str(a) + "} {" + str(b_dir) + "} plain.txt"
    files = rs.parse_drop_paths(raw)
    assert files == [str(a), str(b_dir), "plain.txt"]

    assert rs.accept_drop_entry(str(a), "resourcepacks") is True
    assert rs.accept_drop_entry(str(b_dir), "saves") is True, "地图可以是文件夹"
    assert rs.accept_drop_entry(str(b_dir), "mods") is False
    assert rs.accept_drop_entry(str(tmp_path / "nope.zip"), "resourcepacks") is False

    # 花括号不成对 → 与原文一样抛 ValueError（界面不回滚，Tk 打印 traceback）
    with pytest.raises(ValueError):
        rs.parse_drop_paths("{unclosed")


def test_check_full_house_uses_loader_scan_d109(monkeypatch, tmp_path):
    """D-109（已裁决修正）：条件改为"三种加载器各装过一版"，不再是资源目录判定。

    背景：旧实现查 ``_get_resource_dir("datapacks")`` 必然 ``KeyError``（``RESOURCE_TYPES``
    只有 4 个键），被吞掉后 ``modder_full_house`` 从引入起就没触发过。
    裁决见 ``docs/refactor/07-known-defects.md`` 的 D-109 节：按 4 语言文案
    「安装 Forge、Fabric、NeoForge 各一个版本」实现。
    """
    import achievement_engine

    unlocked = []

    class FakeEngine:
        def check_and_unlock(self, achievement_id, condition):
            unlocked.append((achievement_id, condition))

    monkeypatch.setattr(achievement_engine, "get_achievement_engine", lambda: FakeEngine())

    # 空 .minecraft → 不满足
    rs.check_full_house(tmp_path)
    assert unlocked == [("modder_full_house", False)]

    # 三种加载器各一版 → 满足
    unlocked.clear()
    for vid, lib in (
        ("1.20.4-forge-49.0.26", "net.minecraftforge:forge:1.20.4-49.0.26"),
        ("fabric-loader-0.15.11-1.20.4", "net.fabricmc:fabric-loader:0.15.11"),
        ("1.20.4-neoforge-20.4.234", "net.neoforged.fancymodloader:core:1.0.0"),
    ):
        vdir = tmp_path / "versions" / vid
        vdir.mkdir(parents=True)
        (vdir / f"{vid}.json").write_text(
            json.dumps({"id": vid, "libraries": [{"name": lib}]}), encoding="utf-8"
        )
    rs.check_full_house(tmp_path)
    assert unlocked == [("modder_full_house", True)]

    # 根因仍然钉住：RESOURCE_FOLDERS 里就是没有 datapacks，旧写法必然 KeyError
    with pytest.raises(KeyError):
        rs.resource_dir("1.20.1", tmp_path, "datapacks")
    assert "datapacks" not in rs.RESOURCE_FOLDERS


def test_check_full_house_survives_broken_achievement_engine(monkeypatch, tmp_path):
    """成就引擎坏掉也不能把界面拖下水（原文那层 try/except 保留）。"""
    import achievement_engine

    def boom():
        raise RuntimeError("成就系统坏了")

    monkeypatch.setattr(achievement_engine, "get_achievement_engine", boom)
    rs.check_full_house(tmp_path)  # 不得抛


def test_trigger_ach_swallows_broken_engine(monkeypatch):
    import achievement_engine

    def boom():
        raise RuntimeError("成就系统坏了")

    monkeypatch.setattr(achievement_engine, "get_achievement_engine", boom)
    rs._trigger_ach("modder_diy")  # 不得抛
    rs._check_ach("modder_full_house", True)  # 不得抛


def test_ach_helpers_shared_with_ui_module():
    import ui.windows.resource_manager as rm

    assert rm._trigger_ach is rs._trigger_ach
    assert rm._check_ach is rs._check_ach


# ─── 界面侧：方法面未变 + 薄委托真的能用 ────────────────────

CLIENT_METHODS = [
    "__init__",
    "_get_minecraft_dir",
    "_has_mod_loader",
    "_get_resource_dir",
    "_get_resource_label",
    "_get_resource_desc",
    "_build_ui",
    "_register_dnd",
    "_on_drop",
    "_switch_tab",
    "_refresh_current_list",
    "_refresh_mod_list",
    "_update_mod_loading",
    "_on_mod_metadata_loaded",
    "_check_full_house",
    "_dir_has_content",
    "_on_search",
    "_render_mod_list",
    "_render_filtered_list",
    "_render_current_page",
    "_update_pagination_labels",
    "_update_pagination",
    "_on_prev_page",
    "_on_next_page",
    "_create_mod_card",
    "_create_fallback_icon",
    "_scan_resources",
    "_format_size",
    "_create_resource_item",
    "_set_thumbnail_icon",
    "_thumb_runner",
    "_load_thumbnail_async",
    "_apply_thumbnail",
    "_release_thumb_pool",
    "destroy",
    "_on_thumbnail_done",
    "_install_resource",
    "_install_save",
    "_select_file_install",
    "_open_folder",
    "_delete_resource",
    "_toggle_mod",
    "_export_mod_list",
    "_check_mod_updates",
    "_on_update_check_done",
    "_on_update_check_error",
    "_show_update_dialog",
    "_batch_update_mods",
    "_on_batch_done",
    "_fix_customtkinter_icon",
    "_set_status",
]

SERVER_METHODS = [
    "__init__",
    # ── 以下两个方法**不是** 1.8-A 抽取带进来的，而是抽取之后补的缺陷修复 ──
    # D-104 / L-Q6：本窗口的 `rm_drop_hint` 文案一直写"把 .jar 拖到此处安装"，
    # 但从来没有注册过任何拖拽目标（客户端窗口是同一份文案、也真的接了 tkinterdnd2）。
    # 于阶段 1 第 7 轮按客户端同一方式补齐，源码里就插在 `__init__` 之后。
    # 原 27 个方法仍然一个不少、相对顺序不变 —— 这条断言的判据没有放松。
    "_register_dnd",
    "_on_drop",
    "_fix_customtkinter_icon",
    "_get_mods_dir",
    "_build_ui",
    "_open_folder",
    "_select_file_install",
    "_refresh_mod_list",
    "_update_mod_loading",
    "_on_mod_metadata_loaded",
    "_on_search",
    "_render_mod_list",
    "_on_prev_page",
    "_on_next_page",
    "_create_mod_card",
    "_create_placeholder_icon",
    "_toggle_mod",
    "_delete_mod",
    "_export_mod_list",
    "_check_mod_updates",
    "_do_check_updates",
    "_on_update_check_done",
    "_show_update_dialog",
    "_batch_update_mods",
    "_filter_client_mods",
    "_do_filter_client_mods",
    "_set_status",
    "_run_in_thread",
]


def _own_methods(path: Path, cls_name: str):
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    return [m.name for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]


def test_window_method_surface_unchanged():
    """方法面冻结（``ui/app_handlers.py`` 调的公开面）。

    客户端 51 个；服务端 27 个（1.8-A 抽取时的原始面）+ 2 个 D-104 修复补的拖拽方法。
    **判据仍然是双向的**：少一个、多一个、顺序变化都会红 —— 只有上面明确登记过的
    那 2 个是允许的追加。
    """
    assert _own_methods(UI_RESOURCE, "ResourceManagerWindow") == CLIENT_METHODS
    assert _own_methods(UI_SERVER, "ServerResourceManagerWindow") == SERVER_METHODS
    assert len(CLIENT_METHODS) == 51 and len(SERVER_METHODS) == 29


def test_window_init_signature_unchanged():
    import inspect

    from ui.windows.resource_manager import ResourceManagerWindow
    from ui.windows.server_resource_manager import ServerResourceManagerWindow

    for cls in (ResourceManagerWindow, ServerResourceManagerWindow):
        assert list(inspect.signature(cls.__init__).parameters) == ["self", "parent", "version_id", "callbacks"]


class _FakeClient:
    """借用真实类的方法的假窗口（不构造 Tk）：验证薄委托链路真的通。"""

    from ui.windows.resource_manager import ResourceManagerWindow as _RM

    _get_minecraft_dir = _RM._get_minecraft_dir
    _has_mod_loader = _RM._has_mod_loader
    _get_resource_dir = _RM._get_resource_dir
    _dir_has_content = staticmethod(_RM._dir_has_content)
    _scan_resources = _RM._scan_resources
    _format_size = _RM._format_size  # 注意：界面侧这个不是 staticmethod（原文如此）
    _install_resource = _RM._install_resource
    _install_save = _RM._install_save
    _toggle_mod = _RM._toggle_mod
    _check_full_house = _RM._check_full_house

    def __init__(self, mc_dir: Path, version_id: str):
        self.callbacks = {"get_minecraft_dir": lambda: str(mc_dir)}
        self.version_id = version_id
        self.statuses = []
        self._status_label = self
        self._mod_metadata = []
        self.refreshed = 0

    def configure(self, **kw):
        if "text" in kw:
            self.statuses.append(kw["text"])

    def winfo_exists(self):
        return True

    def _set_status(self, text: str):
        self.statuses.append(text)

    def _get_resource_label(self, rtype: str):
        return rtype

    def _refresh_current_list(self, force: bool = False):
        self.refreshed += 1


def test_client_window_delegates_through_service(tmp_path, monkeypatch):
    """界面方法 → ``_get_resource_service`` → 服务：目录/扫描/安装/启停都真的生效。"""
    import achievement_engine
    import ui.windows.resource_manager as rm

    # i18n 替身：避免依赖"i18n 是否被别的测试初始化过"这种全局状态
    monkeypatch.setattr(rm, "_", lambda key, **kw: key + ("(" + ",".join(sorted(kw)) + ")" if kw else ""))
    # 成就引擎替身：不碰真实单例（别的测试可能已经 init 过它）
    monkeypatch.setattr(achievement_engine, "get_achievement_engine", lambda: None)

    version_id = "1.20.1-forge-47.2.0"
    _forge_version(tmp_path, version_id)
    win = _FakeClient(tmp_path, version_id)

    mods_dir = win._get_resource_dir("mods")
    assert mods_dir == tmp_path / "versions" / version_id / "mods"

    mods_dir.mkdir(parents=True)
    (mods_dir / "a.jar").write_bytes(b"x")
    (mods_dir / "b.txt").write_text("skip", encoding="utf-8")
    assert [i["name"] for i in win._scan_resources(mods_dir, "mods")] == ["a.jar"]
    assert win._format_size(2048) == "2.0 KB"
    assert win._dir_has_content(mods_dir) is True

    src = _zip(tmp_path / "pack.zip", {"pack.mcmeta": "{}"})
    assert win._install_resource(str(src), "resourcepacks") is True
    assert (win._get_resource_dir("resourcepacks") / "pack.zip").exists()
    assert win._install_resource(str(src), "resourcepacks") is False
    assert win.statuses[-1].startswith("rm_file_exists"), "已存在分支写成状态栏（文案键在界面层）"

    win._toggle_mod(str(mods_dir / "a.jar"), False)
    assert (mods_dir / "a.jar.disabled").exists() and win.refreshed == 1
    win._toggle_mod(str(mods_dir / "a.jar.disabled"), True)
    assert (mods_dir / "a.jar").exists() and win.refreshed == 2

    # 全家福成就（D-109 修正版）：条件走加载器盘点，界面侧只需不炸
    win._check_full_house()


class _FakeServer:
    from ui.windows.server_resource_manager import ServerResourceManagerWindow as _SRM

    _get_mods_dir = _SRM._get_mods_dir
    _toggle_mod = _SRM._toggle_mod
    _delete_mod = _SRM._delete_mod

    def __init__(self, server_dir: Path, version_id: str):
        self.callbacks = {"get_server_dir": lambda: str(server_dir)}
        self.version_id = version_id
        self.statuses = []
        self._status_label = self
        self.refreshed = 0

    def configure(self, **kw):
        if "text" in kw:
            self.statuses.append(kw["text"])

    def winfo_exists(self):
        return True

    def _set_status(self, text: str):
        self.statuses.append(text)

    def _refresh_mod_list(self):
        self.refreshed += 1


def test_server_window_delegates_through_service(tmp_path, monkeypatch):
    import ui.windows.server_resource_manager as srm

    # i18n 替身：避免依赖"i18n 是否被别的测试初始化过"这种全局状态
    monkeypatch.setattr(srm, "_", lambda key, **kw: key + ("(" + ",".join(sorted(kw)) + ")" if kw else ""))

    mods_dir_check = _FakeServer(tmp_path, "1.20.1-forge-47.2.0")
    mods_dir = mods_dir_check._get_mods_dir()
    assert mods_dir == tmp_path / "1.20.1-forge-47.2.0" / "mods" and mods_dir.is_dir()
    assert _FakeServer(tmp_path, "1.20.1")._get_mods_dir() == tmp_path / "mods"

    target = mods_dir / "srv.jar"
    target.write_bytes(b"J")
    win = _FakeServer(tmp_path, "1.20.1-forge-47.2.0")
    win._toggle_mod({"filepath": str(target), "disabled": False})
    assert (mods_dir / "srv.jar.disabled").exists() and win.refreshed == 1
    win._toggle_mod({"filepath": str(mods_dir / "srv.jar.disabled"), "disabled": True})
    assert target.exists()

    monkeypatch.setattr(srm.messagebox, "askyesno", lambda *a, **k: True)
    win._delete_mod({"filepath": str(target), "name": "srv.jar"})
    assert not target.exists() and win.refreshed == 3
    assert win.statuses[-1].startswith("mod_deleted_ok")


def test_update_dialog_pagination_uses_cached_items():
    """D-103 守卫（界面侧）：更新弹窗的页数只能由本地缓存条数算出。

    用真实窗口类上的 ``_render_mod_list`` 做端到端验证需要整套控件，成本过高；
    这里改为**静态核对**：方法体里 ``service.total_pages(...)`` 的实参必须是
    ``len(<本地列表>)``，绝不能是任何"后端返回的总数"。
    行为层面的等价已经由 ``test_pagination_count_comes_from_cached_items_not_backend_total_hits``
    与 ``test_window_page_count_always_from_local_len`` 两条钉住。
    """
    tree = ast.parse(io.open(UI_RESOURCE, encoding="utf-8").read())
    dialog_calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_show_update_dialog":
            dialog_calls = [
                n
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "total_pages"
            ]
    assert len(dialog_calls) == 1, "更新弹窗里应当只有一处算页数"
    arg = dialog_calls[0].args[0]
    assert isinstance(arg, ast.Call) and getattr(arg.func, "id", "") == "len"
    assert ast.unparse(arg) == "len(sorted_items)", ast.unparse(arg)
    assert rs.total_pages(12, 10) == 2

