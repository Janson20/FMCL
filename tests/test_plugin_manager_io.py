"""插件系统的文件系统层测试 —— `loader.py`（发现/指纹/动态加载）与
`installer.py`（.fmpl 解压/校验/覆盖安装/卸载/回滚）。

全部离线：所有读写都发生在 `tmp_path` 里，不联网、不装插件、不碰用户真实目录。
`installer` 是**会 rmtree 和 move 目录**的一类代码（与第 11 轮的存档备份同族），
所以这里的重点不是"能不能装上"，而是"失败时会不会毁掉已经装好的东西"、
"会不会写到自己的三个目录之外"、"删不掉的时候是不是还报成功"。
"""

from __future__ import annotations

import json
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin_manager.installer import PluginInstaller
from plugin_manager.loader import (
    PluginLoader,
    _compute_plugin_fingerprint,
    save_plugin_fingerprint,
    verify_plugin_fingerprint,
)
from plugin_manager.manifest import PluginManifest

#: 最小可用插件源码（`PluginLoader._find_plugin_class` 只认 PluginBase 子类）
PLUGIN_BODY = (
    "from plugin_manager.base import PluginBase\n"
    "\n"
    "\n"
    "class Demo(PluginBase):\n"
    "    def on_enable(self):\n"
    "        self.enabled = True\n"
    "\n"
    "    def on_disable(self):\n"
    "        self.enabled = False\n"
)


def _manifest_dict(pid: str = "com.demo.p", **kw) -> dict:
    data = {
        "id": pid,
        "name": "Demo",
        "version": "1.0.0",
        "author": "tester",
        "min_fmcl_version": "1.0.0",
    }
    data.update(kw)
    return data


def _make_plugin_dir(path: Path, pid: str = "com.demo.p", body: str | None = None, **kw) -> Path:
    """在 `path` 里造一个插件目录（plugin.json + 入口模块）。"""
    path.mkdir(parents=True, exist_ok=True)
    (path / "plugin.json").write_text(json.dumps(_manifest_dict(pid, **kw), ensure_ascii=False), encoding="utf-8")
    entry = kw.get("entry", "__init__")
    (path / f"{entry}.py").write_text(body if body is not None else PLUGIN_BODY, encoding="utf-8")
    return path


def _make_fmpl(path: Path, files: dict[str, str], pid: str = "com.demo.p") -> Path:
    """把 `files` 打成一个 .fmpl（zip）包；默认自动补上 plugin.json 与入口模块。"""
    payload = dict(files)
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in payload.items():
            zf.writestr(name, text)
    return path


def _good_fmpl(path: Path, pid: str = "com.demo.p", version: str = "1.0.0", extra: dict | None = None) -> Path:
    files = {
        "plugin.json": json.dumps(_manifest_dict(pid, version=version), ensure_ascii=False),
        "__init__.py": PLUGIN_BODY,
    }
    files.update(extra or {})
    return _make_fmpl(path, files)


@pytest.fixture()
def env(tmp_path: Path) -> SimpleNamespace:
    """三个目录 + 装载器/安装器，全部在 tmp_path 下。"""
    root = tmp_path / "plugins"
    installer = PluginInstaller(root / "installed", root / "disabled", root / "temp")
    loader = PluginLoader(root / "installed")
    return SimpleNamespace(
        tmp=tmp_path,
        root=root,
        installed=root / "installed",
        disabled=root / "disabled",
        temp=root / "temp",
        installer=installer,
        loader=loader,
    )


@pytest.fixture(autouse=True)
def _clean_sys_modules():
    """动态加载会把 `fmcl_plugin_*` 塞进 `sys.modules` —— 每个用例后清干净。"""
    before = set(sys.modules)
    yield
    for name in [m for m in sys.modules if m.startswith("fmcl_plugin_") and m not in before]:
        del sys.modules[name]


# ═══════════════════════════════════════════════════════════════════
# loader.py —— 发现
# ═══════════════════════════════════════════════════════════════════


def test_loader_creates_installed_dir_and_scans_nothing(tmp_path):
    loader = PluginLoader(tmp_path / "installed")
    assert (tmp_path / "installed").is_dir()
    assert loader.scan_installed() == {}


def test_scan_installed_reads_manifests_and_sets_install_path(env):
    _make_plugin_dir(env.installed / "com.demo.a", pid="com.demo.a", version="1.2.0")
    _make_plugin_dir(env.installed / "com.demo.b", pid="com.demo.b")

    found = env.loader.scan_installed()
    assert sorted(found) == ["com.demo.a", "com.demo.b"]
    assert found["com.demo.a"].version == "1.2.0"
    assert found["com.demo.a"].install_path == env.installed / "com.demo.a"


def test_scan_installed_skips_junk_without_raising(env):
    """目录里什么都可能有：普通文件、没有 plugin.json 的目录、坏 JSON、空 id。

    一个坏插件不能让整次扫描失败（`scan()` 是启动路径）。
    """
    (env.installed / "readme.txt").write_text("not a plugin", encoding="utf-8")
    (env.installed / "no_manifest").mkdir()
    bad = env.installed / "broken"
    bad.mkdir()
    (bad / "plugin.json").write_text("{ 这不是 JSON", encoding="utf-8")
    empty_id = env.installed / "empty_id"
    empty_id.mkdir()
    (empty_id / "plugin.json").write_text(json.dumps({"name": "无名"}), encoding="utf-8")
    _make_plugin_dir(env.installed / "ok", pid="com.demo.ok")

    found = env.loader.scan_installed()
    assert sorted(found) == ["com.demo.ok"]


# ═══════════════════════════════════════════════════════════════════
# loader.py —— 完整性指纹
# ═══════════════════════════════════════════════════════════════════


def test_fingerprint_roundtrip_and_tamper_detection(env):
    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    save_plugin_fingerprint(env.installed, "com.demo.p", plugin)
    assert (env.installed / "_fingerprints.json").is_file()
    assert verify_plugin_fingerprint(env.installed, "com.demo.p", plugin) == (True, "")

    # 改内容 / 加文件 / 删文件都会改变指纹
    (plugin / "__init__.py").write_text(PLUGIN_BODY + "\nX = 1\n", encoding="utf-8")
    ok, err = verify_plugin_fingerprint(env.installed, "com.demo.p", plugin)
    assert ok is False and "完整性校验失败" in err


def test_fingerprint_detects_added_and_removed_py_files(env):
    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    save_plugin_fingerprint(env.installed, "com.demo.p", plugin)
    (plugin / "helper.py").write_text("Y = 2\n", encoding="utf-8")
    assert verify_plugin_fingerprint(env.installed, "com.demo.p", plugin)[0] is False
    (plugin / "helper.py").unlink()
    assert verify_plugin_fingerprint(env.installed, "com.demo.p", plugin) == (True, "")


def test_fingerprint_ignores_manifest_tampering(env):
    """记录现状（已知盲点，见报告 D-132）：指纹只覆盖 ``*.py``，
    改 `plugin.json`（例如偷偷加一条 ``core.process`` 权限）**不会**被发现。
    """
    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    save_plugin_fingerprint(env.installed, "com.demo.p", plugin)
    data = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
    data["permissions"] = ["core.process", "network.socket"]
    (plugin / "plugin.json").write_text(json.dumps(data), encoding="utf-8")
    assert verify_plugin_fingerprint(env.installed, "com.demo.p", plugin) == (True, "")


def test_fingerprint_is_skipped_when_file_missing_or_corrupt(env):
    """没有指纹文件 / 指纹文件坏了 / 该插件没有记录 → 都放行（只记 warning）。"""
    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    assert verify_plugin_fingerprint(env.installed, "com.demo.p", plugin) == (True, "")

    (env.installed / "_fingerprints.json").write_text("{ 坏掉的 JSON", encoding="utf-8")
    assert verify_plugin_fingerprint(env.installed, "com.demo.p", plugin) == (True, "")

    (env.installed / "_fingerprints.json").write_text(json.dumps({"别的插件": "deadbeef"}), encoding="utf-8")
    assert verify_plugin_fingerprint(env.installed, "com.demo.p", plugin) == (True, "")


def test_save_fingerprint_wipes_other_records_when_file_is_corrupt(env):
    """记录现状（D-133）：指纹文件解析失败时 `save_plugin_fingerprint` 把整个字典
    重置成"只有刚装的这个插件"，其他插件的指纹记录**被静默抹掉** ——
    之后它们加载时走"无指纹记录，跳过校验"分支，完整性校验对它们等于关闭。
    """
    _make_plugin_dir(env.installed / "com.other")
    (env.installed / "_fingerprints.json").write_text("坏文件", encoding="utf-8")
    new_plugin = _make_plugin_dir(env.installed / "com.demo.p")
    save_plugin_fingerprint(env.installed, "com.demo.p", new_plugin)

    data = json.loads((env.installed / "_fingerprints.json").read_text(encoding="utf-8"))
    assert list(data) == ["com.demo.p"]


def test_compute_fingerprint_is_order_independent(env):
    """同内容、不同创建顺序 → 同一个指纹（按相对路径排序后拼接）。"""
    a = _make_plugin_dir(env.tmp / "a", body="Z = 1\n")
    (a / "b.py").write_text("B = 2\n", encoding="utf-8")
    c = _make_plugin_dir(env.tmp / "c", body="Z = 1\n")
    (c / "b.py").write_text("B = 2\n", encoding="utf-8")
    assert _compute_plugin_fingerprint(a) == _compute_plugin_fingerprint(c)


# ═══════════════════════════════════════════════════════════════════
# loader.py —— 动态加载与属性注入
# ═══════════════════════════════════════════════════════════════════


def test_load_module_requires_install_path_and_entry_file(env):
    manifest = PluginManifest.from_dict(_manifest_dict())
    assert env.loader.load_module(manifest) == (None, "manifest.install_path 未设置")

    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    missing_entry = PluginManifest.from_dict(_manifest_dict(entry="main"), install_path=plugin)
    instance, error = env.loader.load_module(missing_entry)
    assert instance is None and "入口模块 main.py 不存在" in error


def test_load_module_returns_instance_from_plugin_subclass(env):
    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    manifest = PluginManifest.from_dict(_manifest_dict(), install_path=plugin)
    instance, error = env.loader.load_module(manifest)
    assert error == "" and instance is not None
    assert type(instance).__name__ == "Demo"
    assert "fmcl_plugin_com_demo_p" in sys.modules


def test_load_module_rejects_module_without_plugin_subclass(env):
    plugin = env.installed / "com.demo.noclass"
    plugin.mkdir(parents=True)
    (plugin / "plugin.json").write_text(json.dumps(_manifest_dict("com.demo.noclass")), encoding="utf-8")
    (plugin / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    manifest = PluginManifest.from_dict(_manifest_dict("com.demo.noclass"), install_path=plugin)

    instance, error = env.loader.load_module(manifest)
    assert instance is None and "未找到继承自 PluginBase 的类" in error
    assert "fmcl_plugin_com_demo_noclass" not in sys.modules, "失败的模块不能留在 sys.modules 里"


def test_load_module_isolates_import_errors_and_cleans_sys_modules(env):
    plugin = _make_plugin_dir(env.installed / "com.demo.boom", body="raise RuntimeError('导入就炸')\n")
    manifest = PluginManifest.from_dict(_manifest_dict("com.demo.boom"), install_path=plugin)

    instance, error = env.loader.load_module(manifest)
    assert instance is None and "加载模块异常" in error and "导入就炸" in error
    assert "fmcl_plugin_com_demo_boom" not in sys.modules


def test_load_module_refuses_tampered_plugin(env):
    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    save_plugin_fingerprint(env.installed, "com.demo.p", plugin)
    (plugin / "__init__.py").write_text(PLUGIN_BODY + "\n# 被改过\n", encoding="utf-8")
    manifest = PluginManifest.from_dict(_manifest_dict(), install_path=plugin)
    instance, error = env.loader.load_module(manifest)
    assert instance is None and "完整性校验失败" in error


def test_reload_module_returns_a_fresh_instance(env):
    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    manifest = PluginManifest.from_dict(_manifest_dict(), install_path=plugin)
    first, _ = env.loader.load_module(manifest)
    second, error = env.loader.reload_module(manifest)
    assert error == "" and second is not None and second is not first


def test_module_name_collision_for_ids_differing_only_by_separators(env):
    """记录现状（D-134）：模块名由 ``id.replace(".","_").replace("-","_")`` 生成，
    于是 ``com.demo.p`` / ``com-demo-p`` / ``com_demo_p`` 撞同一个模块名 ——
    加载后一个会 `del sys.modules[前一个]`，卸载一个也会清掉另一个的模块缓存。
    """
    a = _make_plugin_dir(env.installed / "com.demo.p", pid="com.demo.p")
    b = _make_plugin_dir(env.installed / "com-demo-p", pid="com-demo-p")
    m_a = PluginManifest.from_dict(_manifest_dict("com.demo.p"), install_path=a)
    m_b = PluginManifest.from_dict(_manifest_dict("com-demo-p"), install_path=b)

    first, _ = env.loader.load_module(m_a)
    second, _ = env.loader.load_module(m_b)
    assert first is not None and second is not None
    # 两个插件的模块名相同 → sys.modules 里的那一项已经是后者的模块了
    assert type(first).__module__ == type(second).__module__ == "fmcl_plugin_com_demo_p"
    assert sys.modules["fmcl_plugin_com_demo_p"].__file__ == str(b / "__init__.py")


def test_inject_attributes_sets_paths_config_and_permissions(env):
    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    manifest = PluginManifest.from_dict(_manifest_dict(), install_path=plugin)
    instance, _ = env.loader.load_module(manifest)
    data_dir = env.root / "data" / "com.demo.p"
    perm_state = SimpleNamespace(is_granted=lambda p: False)

    env.loader.inject_attributes(instance, manifest, data_dir, {"volume": 30}, "MANAGER", perm_state)
    assert instance.manifest is manifest
    assert instance.plugin_dir == plugin
    assert instance.data_dir == data_dir and data_dir.is_dir()
    assert instance.config == {"volume": 30} and instance.config is not None
    assert instance._manager == "MANAGER"
    assert instance._perm_state is perm_state
    # 传进去的 dict 是拷贝，不是同一个对象
    cfg = {"a": 1}
    env.loader.inject_attributes(instance, manifest, data_dir, cfg, None, None)
    assert instance.config == {"a": 1} and instance.config is not cfg


def test_inject_attributes_uses_defaults_when_config_is_none(env):
    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    manifest = PluginManifest.from_dict(_manifest_dict(), install_path=plugin)
    instance, _ = env.loader.load_module(manifest)
    instance.get_default_config = lambda: {"volume": 80}
    env.loader.inject_attributes(instance, manifest, env.root / "data" / "x", None, None, None)
    assert instance.config == {"volume": 80}


def test_inject_attributes_keeps_empty_saved_config(env):
    """空配置（用户把配置清空了 / `configs/<id>.json` 是 `{}`）必须**保持为空**。

    D-127：`dict(config) if config else ...` 把"空字典"当成"没有配置"，
    于是用户刚清空的配置在下次加载时被默认值悄悄填回来 —— 保存过的设置被无声撤销。
    """
    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    manifest = PluginManifest.from_dict(_manifest_dict(), install_path=plugin)
    instance, _ = env.loader.load_module(manifest)
    instance.get_default_config = lambda: {"volume": 80, "theme": "dark"}
    env.loader.inject_attributes(instance, manifest, env.root / "data" / "x", {}, None, None)
    assert instance.config == {}


# ═══════════════════════════════════════════════════════════════════
# installer.py —— 输入校验与拒绝路径
# ═══════════════════════════════════════════════════════════════════


def test_install_rejects_missing_file_and_wrong_suffix(env):
    assert env.installer.install_from_fmpl(str(env.tmp / "没有这个文件.fmpl"), "com.demo.p") == (
        False,
        f"文件不存在: {env.tmp / '没有这个文件.fmpl'}",
    )
    zip_path = env.tmp / "plugin.zip"
    zip_path.write_bytes(b"PK\x03\x04")
    ok, msg = env.installer.install_from_fmpl(str(zip_path), "com.demo.p")
    assert ok is False and "不是 .fmpl 格式" in msg


@pytest.mark.parametrize("bad_id", ["../escape", "a/b", "a\\b", "..", "."])
def test_install_rejects_unsafe_plugin_id(env, bad_id):
    """ID 是**包里的 plugin.json 自己写的**（界面 `_on_install_from_file` 就是
    从包里读 id 再传进来），所以必须当作不可信输入：任何路径分隔或 ".."
    都会让 `installed/<id>` 指到安装目录之外。
    """
    fmpl = _good_fmpl(env.tmp / "x.fmpl", pid=bad_id)
    ok, msg = env.installer.install_from_fmpl(str(fmpl), bad_id)
    assert ok is False and "不安全" in msg
    # 真的什么都没建
    assert sorted(p.name for p in env.root.rglob("*") if p.is_dir()) == ["disabled", "installed", "temp"]


def test_install_rejects_bad_zip_and_missing_manifest_and_id_mismatch(env):
    bad_zip = env.tmp / "bad.fmpl"
    bad_zip.write_bytes(b"this is plain text, not a zip archive")
    assert env.installer.install_from_fmpl(str(bad_zip), "com.demo.p") == (False, "压缩包格式无效")

    no_manifest = _make_fmpl(env.tmp / "no_manifest.fmpl", {"__init__.py": PLUGIN_BODY})
    assert env.installer.install_from_fmpl(str(no_manifest), "com.demo.p") == (False, "压缩包中缺少 plugin.json")

    other_id = _good_fmpl(env.tmp / "other.fmpl", pid="com.other.p")
    ok, msg = env.installer.install_from_fmpl(str(other_id), "com.demo.p")
    assert ok is False and "不一致" in msg

    no_entry = _make_fmpl(
        env.tmp / "no_entry.fmpl", {"plugin.json": json.dumps(_manifest_dict("com.demo.p", entry="main"))}
    )
    ok, msg = env.installer.install_from_fmpl(str(no_entry), "com.demo.p")
    assert ok is False and "入口模块 main.py 不存在" in msg


def test_install_rejects_zip_slip_without_writing_outside(env):
    """Zip Slip：包里有 ``../evil.txt`` 时必须整体拒绝，且磁盘上什么都不能多。"""
    evil = _good_fmpl(env.tmp / "evil.fmpl", extra={"../evil.txt": "逃逸", "sub/../../evil2.txt": "逃逸"})
    ok, msg = env.installer.install_from_fmpl(str(evil), "com.demo.p")
    assert ok is False and "Zip Slip" in msg
    assert list(env.tmp.rglob("evil*.txt")) == []
    assert not (env.installed / "com.demo.p").exists()


# ═══════════════════════════════════════════════════════════════════
# installer.py —— 安装成功 / 覆盖安装 / 失败不留痕
# ═══════════════════════════════════════════════════════════════════


def test_install_success_places_files_and_writes_fingerprint(env):
    fmpl = _good_fmpl(env.tmp / "ok.fmpl", extra={"assets/data.txt": "hello"})
    ok, msg = env.installer.install_from_fmpl(str(fmpl), "com.demo.p")
    assert (ok, msg) == (True, "")
    target = env.installed / "com.demo.p"
    assert sorted(p.name for p in target.iterdir()) == ["__init__.py", "assets", "plugin.json"]
    # 指纹已写入，且装载器能直接发现 + 加载这个插件
    assert json.loads((env.installed / "_fingerprints.json").read_text(encoding="utf-8"))["com.demo.p"]
    manifests = env.loader.scan_installed()
    instance, error = env.loader.load_module(manifests["com.demo.p"])
    assert error == "" and instance is not None
    # 临时解压目录不在
    assert not (env.temp / "_extract_com.demo.p").exists()


def test_install_overwrites_previous_version_completely(env):
    ok, _ = env.installer.install_from_fmpl(str(_good_fmpl(env.tmp / "v1.fmpl", version="1.0.0", extra={"old.txt": "1"})), "com.demo.p")
    assert ok is True
    ok, _ = env.installer.install_from_fmpl(str(_good_fmpl(env.tmp / "v2.fmpl", version="2.0.0", extra={"new.txt": "2"})), "com.demo.p")
    assert ok is True
    target = env.installed / "com.demo.p"
    names = sorted(p.name for p in target.iterdir())
    assert names == ["__init__.py", "new.txt", "plugin.json"]
    assert json.loads((target / "plugin.json").read_text(encoding="utf-8"))["version"] == "2.0.0"


def test_failed_install_keeps_the_installed_version_intact(env):
    """装坏包**不能**毁掉已经装好的版本。

    D-121：`install_from_fmpl` 在解压/校验**之前**就把 `installed/<id>` 整个
    rmtree 掉了（"如果已存在，先卸载旧版"），于是"压缩包里缺 plugin.json"、
    "manifest ID 不一致"、"入口模块不存在"、"zip 坏了" 这四种失败，
    都会**先删掉用户正在用的那个版本**、再返回失败 —— 用户点一次"安装"就丢插件，
    而且界面只显示一条错误信息，没有任何回滚（`update_plugin` 有备份/回滚，
    `install_from_file` 这条路径没有）。
    """
    ok, _ = env.installer.install_from_fmpl(
        str(_good_fmpl(env.tmp / "v1.fmpl", extra={"keep.txt": "旧版的数据"})), "com.demo.p"
    )
    assert ok is True
    target = env.installed / "com.demo.p"

    broken = _make_fmpl(env.tmp / "broken.fmpl", {"__init__.py": PLUGIN_BODY})  # 缺 plugin.json
    ok, msg = env.installer.install_from_fmpl(str(broken), "com.demo.p")
    assert (ok, msg) == (False, "压缩包中缺少 plugin.json")
    assert (target / "keep.txt").read_text(encoding="utf-8") == "旧版的数据", "坏包不该删掉已装版本"


def test_failed_install_leaves_no_temp_extract_dir(env):
    """D-122：失败路径（缺 plugin.json / ID 不一致 / 缺入口 / Zip Slip）原来只
    在被解压目录里留一堆垃圾 —— 只有"意外异常"那条分支才清理。
    """
    broken = _make_fmpl(env.tmp / "broken.fmpl", {"__init__.py": PLUGIN_BODY})
    ok, _ = env.installer.install_from_fmpl(str(broken), "com.demo.p")
    assert ok is False
    assert not (env.temp / "_extract_com.demo.p").exists(), "失败的安装不该留下临时解压目录"
    assert list(env.temp.iterdir()) == []


# ═══════════════════════════════════════════════════════════════════
# installer.py —— 卸载 / 禁用 / 启用
# ═══════════════════════════════════════════════════════════════════


def test_uninstall_removes_directory(env):
    env.installer.install_from_fmpl(str(_good_fmpl(env.tmp / "ok.fmpl")), "com.demo.p")
    assert env.installer.uninstall("com.demo.p") == (True, "")
    assert not (env.installed / "com.demo.p").exists()
    assert env.installer.uninstall("com.demo.p")[0] is False  # 再卸一次 → 目录不存在


@pytest.mark.skipif(sys.platform != "win32", reason="依赖 Windows 文件被占用就删不掉的语义")
def test_uninstall_reports_failure_when_directory_survives(env):
    """删不掉的时候不能报成功。

    D-123：`shutil.rmtree(target_dir, ignore_errors=True)` 把权限/占用类错误全部
    吞掉，函数照样 `return True, ""` —— 管理器随即把插件从内存状态里清掉，
    界面上"卸载成功"，但目录和文件都还在：重启后 `scan()` 又把它当成新插件发现出来。
    这里用"文件被本进程打开"制造真实的删除失败（Windows 上 Python 打开的文件
    不带 FILE_SHARE_DELETE）。
    """
    plugin = _make_plugin_dir(env.installed / "com.demo.locked")
    locked = plugin / "plugin.json"
    with open(locked, "r", encoding="utf-8"):
        ok, msg = env.installer.uninstall("com.demo.locked")
    assert plugin.exists(), "前提没成立：文件没被真的占用"
    assert ok is False and "卸载失败" in msg


def test_disable_and_enable_move_the_directory_between_the_two_roots(env):
    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    ok, _ = env.installer.disable("com.demo.p")
    assert ok is True
    assert not plugin.exists() and (env.disabled / "com.demo.p").is_dir()
    assert env.installer.disable("com.demo.p")[0] is False  # 已经没有可禁用的目录

    ok, _ = env.installer.enable("com.demo.p")
    assert ok is True and plugin.is_dir() and not (env.disabled / "com.demo.p").exists()
    assert env.installer.enable("com.demo.p")[0] is False


def test_enable_refuses_to_clobber_an_existing_plugin(env):
    _make_plugin_dir(env.installed / "com.demo.p")
    _make_plugin_dir(env.disabled / "com.demo.p")
    ok, msg = env.installer.enable("com.demo.p")
    assert ok is False and "同名插件已存在" in msg
    # 两边都还在，什么都没丢
    assert (env.installed / "com.demo.p").is_dir() and (env.disabled / "com.demo.p").is_dir()


# ═══════════════════════════════════════════════════════════════════
# installer.py —— 备份 / 回滚 / 路径守卫
# ═══════════════════════════════════════════════════════════════════


def test_backup_rollback_and_cleanup_roundtrip(env):
    plugin = _make_plugin_dir(env.installed / "com.demo.p")
    (plugin / "keep.txt").write_text("v1", encoding="utf-8")

    backup, err = env.installer.backup_existing("com.demo.p")
    assert err == "" and backup == env.temp / "_backup_com.demo.p" and backup.is_dir()

    # 模拟"新版装了一半就坏了"
    shutil.rmtree(plugin, ignore_errors=True)
    _make_plugin_dir(env.installed / "com.demo.p", version="2.0.0")
    ok, _ = env.installer.rollback("com.demo.p")
    assert ok is True
    assert (plugin / "keep.txt").read_text(encoding="utf-8") == "v1", "回滚必须把旧版内容还回来"

    env.installer.cleanup_backup("com.demo.p")
    assert not (env.temp / "_backup_com.demo.p").exists()
    # 备份不存在时的返回值
    assert env.installer.backup_existing("com.demo.不存在")[0] is None
    assert env.installer.rollback("com.demo.不存在")[0] is False


@pytest.mark.parametrize("unsafe", ["a/../../victim", "../victim", "..", "a/b"])
def test_backup_and_cleanup_refuse_ids_that_escape_the_plugin_root(env, unsafe):
    """备份/回滚/清理这三条路径原来**没有**任何 ID 净化（`install_from_fmpl` /
    `uninstall` / `disable` / `enable` 都有）。

    D-124：这些函数里的路径是 `temp/"_backup_" + plugin_id` 与
    `installed/plugin_id`，`plugin_id` 直接来自"市场索引里的 id"或"包里的
    manifest.id"，都能含 ``/`` 与 ``..``。实测最狠的一种：`cleanup_backup("a/../..")`
    归一化到 `plugins/` 根，`rmtree` 把**整个插件根目录（含 configs/ data/ 与所有
    已装插件）删掉**。这里把"拒绝"钉死，并且断言根目录与旁观目录都还在。
    """
    victim = env.root / "victim"
    victim.mkdir(parents=True, exist_ok=True)
    (victim / "important.txt").write_text("别删我", encoding="utf-8")

    assert env.installer.backup_existing(unsafe)[0] is None
    assert env.installer.rollback(unsafe)[0] is False
    env.installer.cleanup_backup(unsafe)  # 不该抛异常，也不该删东西

    assert env.root.is_dir(), "插件根目录被删了"
    assert (victim / "important.txt").is_file(), "安装目录之外的东西被删了"