"""插件系统的编排层测试 —— `manager.py`（PluginManager）。

全部离线：插件目录、配置、权限、状态文件都在 `tmp_path` 里；`updater.get_current_version`
被替换成固定版本号（FMCL 版本闸门要用），没有任何网络或真实用户目录访问。

这里重点钉的是"用户看得见但不会抛异常"的地方：状态机（SCANNED/LOADED/ENABLED/
ERROR/INCOMPATIBLE）、权限闸门、持久化与重启恢复、卸载与更新失败时的收尾。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import updater
from plugin_manager.base import HookPoint, PluginState
from plugin_manager.manager import PluginManager
from plugin_manager.permissions import PluginPermission, PluginPermissionState

#: 完整插件：记录生命周期 + 启用时注册一个钩子（用来验证禁用会注销钩子）
PLUGIN_BODY = (
    "from plugin_manager.base import HookPoint, PluginBase\n"
    "\n"
    "\n"
    "class Demo(PluginBase):\n"
    "    def __init__(self):\n"
    "        self.loaded = False\n"
    "        self.enabled = False\n"
    "        self.disabled = False\n"
    "\n"
    "    def on_load(self):\n"
    "        self.loaded = True\n"
    "\n"
    "    def on_enable(self):\n"
    "        self.enabled = True\n"
    "        self._manager.register_hook(self.manifest.id, HookPoint.APP_STARTUP, lambda **kw: None)\n"
    "\n"
    "    def on_disable(self):\n"
    "        self.disabled = True\n"
)


def _manifest(pid: str = "com.demo.p", **kw) -> dict:
    data = {
        "id": pid,
        "name": "Demo",
        "version": "1.0.0",
        "author": "tester",
        "min_fmcl_version": "1.0.0",
    }
    data.update(kw)
    return data


def _install_plugin(root: Path, pid: str = "com.demo.p", body: str | None = None, **manifest_kw) -> Path:
    """在 `<root>/installed/<pid>/` 造一个插件（等同手工放进去的插件）。"""
    plugin = root / "installed" / pid
    plugin.mkdir(parents=True, exist_ok=True)
    (plugin / "plugin.json").write_text(json.dumps(_manifest(pid, **manifest_kw), ensure_ascii=False), encoding="utf-8")
    (plugin / "__init__.py").write_text(body if body is not None else PLUGIN_BODY, encoding="utf-8")
    return plugin


@pytest.fixture()
def pm_env(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    """一个隔离的 PluginManager（FMCL 版本固定成 1.8.0）。"""
    monkeypatch.setattr(updater, "get_current_version", lambda: "1.8.0")
    root = tmp_path / "plugins"
    return SimpleNamespace(tmp=tmp_path, root=root, pm=PluginManager(root), pid="com.demo.p")


def _instance(pm: PluginManager, pid: str):
    """拿插件实例（`main.py:_build_plugin_tabs` 也是这么取 `pm._instances`）。"""
    return pm._instances[pid]


# ═══════════════════════════════════════════════════════════════════
# 目录布局 / 扫描
# ═══════════════════════════════════════════════════════════════════


def test_manager_creates_isolated_directory_layout(pm_env):
    assert sorted(p.name for p in pm_env.root.iterdir()) == [
        "cache",
        "configs",
        "data",
        "disabled",
        "installed",
        "temp",
    ]
    assert pm_env.pm.scan() == []


def test_scan_finds_plugins_and_keeps_them_scanned(pm_env):
    _install_plugin(pm_env.root, "com.demo.a")
    _install_plugin(pm_env.root, "com.demo.b")
    assert sorted(pm_env.pm.scan()) == ["com.demo.a", "com.demo.b"]
    assert pm_env.pm.get_plugin_state("com.demo.a") == PluginState.SCANNED
    assert pm_env.pm.get_loaded_plugins() == []
    assert sorted(pm_env.pm.get_installed_versions_map()) == ["com.demo.a", "com.demo.b"]
    # 再扫一次是幂等的，状态不会被重置
    assert sorted(pm_env.pm.scan()) == ["com.demo.a", "com.demo.b"]


def test_scan_survives_a_broken_plugin_next_to_a_good_one(pm_env):
    _install_plugin(pm_env.root, "com.demo.bad")
    (pm_env.root / "installed" / "com.demo.bad" / "plugin.json").write_text("{ 坏 JSON", encoding="utf-8")
    _install_plugin(pm_env.root, "com.demo.good")
    assert pm_env.pm.scan() == ["com.demo.good"]


# ═══════════════════════════════════════════════════════════════════
# 加载 / 启用 / 禁用 状态机
# ═══════════════════════════════════════════════════════════════════


def test_load_plugin_refuses_unknown_id(pm_env):
    assert pm_env.pm.load_plugin("com.不存在") == (False, "插件未发现: com.不存在")
    assert pm_env.pm.get_plugin_state("com.不存在") is None


def test_load_plugin_enforces_fmcl_version_gate(pm_env):
    _install_plugin(pm_env.root, "com.too.new", min_fmcl_version="9.0.0")
    _install_plugin(pm_env.root, "com.too.old", max_fmcl_version="1.0.0")
    pm_env.pm.scan()

    ok, msg = pm_env.pm.load_plugin("com.too.new")
    assert ok is False and "低于插件最低要求" in msg
    assert pm_env.pm.get_plugin_state("com.too.new") == PluginState.INCOMPATIBLE
    assert pm_env.pm.get_plugin_error("com.too.new") == msg

    ok, msg = pm_env.pm.load_plugin("com.too.old")
    assert ok is False and "超过插件最高支持" in msg
    assert pm_env.pm.get_plugin_state("com.too.old") == PluginState.INCOMPATIBLE


def test_load_plugin_reports_malformed_version_instead_of_crashing(pm_env):
    """清单里版本号不可解析时，`load_plugin` 必须**返回失败**而不是抛异常。

    D-128：`min_fmcl_version` 是必填字段，缺了就变空串（`from_dict` 不校验），
    而 `_check_fmcl_version` 会拿空串去 `compare_versions` → `int("")` 抛
    ValueError。这个异常从 `load_plugin` 里**直接冒出去**（管理器不是异常边界），
    界面上表现为一条 `invalid literal for int() with base 10: ''` 的原始报错，
    状态机也停在 LOADING 之外（没进 INCOMPATIBLE/INIT_ERROR 任何一档）。
    """
    _install_plugin(pm_env.root, "com.no.version")
    data = _manifest("com.no.version")
    del data["min_fmcl_version"]
    (pm_env.root / "installed" / "com.no.version" / "plugin.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8"
    )
    pm_env.pm.scan()

    ok, msg = pm_env.pm.load_plugin("com.no.version")
    assert ok is False and "版本号无法解析" in msg
    assert pm_env.pm.get_plugin_state("com.no.version") == PluginState.INIT_ERROR


def test_load_enable_disable_lifecycle(pm_env):
    _install_plugin(pm_env.root, pm_env.pid)
    pm_env.pm.scan()

    ok, msg = pm_env.pm.load_plugin(pm_env.pid)
    assert (ok, msg) == (True, "")
    assert pm_env.pm.get_plugin_state(pm_env.pid) == PluginState.LOADED
    assert _instance(pm_env.pm, pm_env.pid).loaded is True

    ok, msg = pm_env.pm.enable_plugin(pm_env.pid)
    assert (ok, msg) == (True, "")
    assert pm_env.pm.get_plugin_state(pm_env.pid) == PluginState.ENABLED
    assert pm_env.pm.get_enabled_plugins() == [pm_env.pid]
    assert pm_env.pm.hook_bus.get_listener_count(pm_env.pid) == 1  # on_enable 里注册的
    assert pm_env.pm.enable_plugin(pm_env.pid) == (True, "")  # 重复启用是幂等的

    ok, msg = pm_env.pm.disable_plugin(pm_env.pid)
    assert (ok, msg) == (True, "")
    assert pm_env.pm.get_plugin_state(pm_env.pid) == PluginState.DISABLED
    assert _instance(pm_env.pm, pm_env.pid).disabled is True
    assert pm_env.pm.hook_bus.get_listener_count(pm_env.pid) == 0, "禁用必须注销该插件的全部钩子"


def test_enable_plugin_loads_first_when_still_scanned(pm_env):
    _install_plugin(pm_env.root, pm_env.pid)
    pm_env.pm.scan()
    assert pm_env.pm.enable_plugin(pm_env.pid) == (True, "")
    assert pm_env.pm.get_plugin_state(pm_env.pid) == PluginState.ENABLED


def test_enable_plugin_re_enables_after_disable(pm_env):
    _install_plugin(pm_env.root, pm_env.pid)
    pm_env.pm.scan()
    pm_env.pm.enable_plugin(pm_env.pid)
    pm_env.pm.disable_plugin(pm_env.pid)
    assert pm_env.pm.enable_plugin(pm_env.pid) == (True, "")
    assert pm_env.pm.get_plugin_state(pm_env.pid) == PluginState.ENABLED


def test_on_enable_failure_sets_error_state_and_reason(pm_env):
    _install_plugin(pm_env.root, pm_env.pid, body="from plugin_manager.base import PluginBase\n"
                     "\n"
                     "\n"
                     "class Demo(PluginBase):\n"
                     "    def on_enable(self):\n"
                     "        raise RuntimeError('启用就炸')\n"
                     "\n"
                     "    def on_disable(self):\n"
                     "        pass\n")
    pm_env.pm.scan()
    ok, msg = pm_env.pm.enable_plugin(pm_env.pid)
    assert ok is False and "启用失败" in msg and "启用就炸" in msg
    assert pm_env.pm.get_plugin_state(pm_env.pid) == PluginState.ERROR
    assert pm_env.pm.get_plugin_error(pm_env.pid) == "启用就炸"


def test_on_disable_failure_still_disables_and_unregisters_hooks(pm_env):
    """`on_disable` 抛异常不能阻止停用（文档明确写了"不会阻止停用流程"）。"""
    _install_plugin(
        pm_env.root,
        pm_env.pid,
        body="from plugin_manager.base import HookPoint, PluginBase\n"
        "\n"
        "\n"
        "class Demo(PluginBase):\n"
        "    def on_enable(self):\n"
        "        self._manager.register_hook(self.manifest.id, HookPoint.APP_STARTUP, lambda **kw: None)\n"
        "\n"
        "    def on_disable(self):\n"
        "        raise RuntimeError('停用就炸')\n",
    )
    pm_env.pm.scan()
    pm_env.pm.enable_plugin(pm_env.pid)
    assert pm_env.pm.disable_plugin(pm_env.pid) == (True, "")
    assert pm_env.pm.get_plugin_state(pm_env.pid) == PluginState.DISABLED
    assert pm_env.pm.hook_bus.get_listener_count(pm_env.pid) == 0


def test_on_load_failure_does_not_block_loading(pm_env):
    """`on_load` 的异常只写日志（记录现状：插件照常进入 LOADED）。"""
    _install_plugin(
        pm_env.root,
        pm_env.pid,
        body="from plugin_manager.base import PluginBase\n"
        "\n"
        "\n"
        "class Demo(PluginBase):\n"
        "    def on_load(self):\n"
        "        raise RuntimeError('on_load 炸')\n"
        "\n"
        "    def on_enable(self):\n"
        "        pass\n"
        "\n"
        "    def on_disable(self):\n"
        "        pass\n",
    )
    pm_env.pm.scan()
    assert pm_env.pm.load_plugin(pm_env.pid) == (True, "")
    assert pm_env.pm.get_plugin_state(pm_env.pid) == PluginState.LOADED


def test_disable_plugin_refuses_when_not_loaded(pm_env):
    _install_plugin(pm_env.root, pm_env.pid)
    pm_env.pm.scan()
    assert pm_env.pm.disable_plugin(pm_env.pid) == (False, "插件未加载")


# ═══════════════════════════════════════════════════════════════════
# 状态持久化与重启恢复
# ═══════════════════════════════════════════════════════════════════


def test_state_file_only_persists_stable_states(pm_env):
    _install_plugin(pm_env.root, "com.demo.a")
    _install_plugin(pm_env.root, "com.demo.b")
    pm_env.pm.scan()
    pm_env.pm.enable_plugin("com.demo.a")

    data = json.loads((pm_env.root / "configs" / "plugin_states.json").read_text(encoding="utf-8"))
    assert data == {"com.demo.a": "enabled"}  # SCANNED 的 b 不落盘

    pm_env.pm.disable_plugin("com.demo.a")
    data = json.loads((pm_env.root / "configs" / "plugin_states.json").read_text(encoding="utf-8"))
    assert data == {"com.demo.a": "disabled"}


def test_scan_restores_previously_enabled_plugin(pm_env):
    """重启后自动恢复"上次启用"的插件（`scan()` 的恢复分支）。

    这条同时是 D-138 的用户可见面：原来这个分支**必然失败** ——
    它先 `grant_manifest_permissions`（建出权限状态）再 `enable_plugin`，
    而闸门会被三条未声明的高风险权限拒绝，插件停在 LOADED。
    """
    _install_plugin(pm_env.root, pm_env.pid)
    configs = pm_env.root / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    (configs / "plugin_states.json").write_text(json.dumps({pm_env.pid: "enabled"}), encoding="utf-8")

    assert pm_env.pm.scan() == [pm_env.pid]
    assert pm_env.pm.get_plugin_state(pm_env.pid) == PluginState.ENABLED
    assert pm_env.pm.get_enabled_plugins() == [pm_env.pid]
    assert _instance(pm_env.pm, pm_env.pid).enabled is True


def test_scan_keeps_disabled_plugins_disabled_and_drops_stale_entries(pm_env):
    _install_plugin(pm_env.root, pm_env.pid)
    configs = pm_env.root / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    (configs / "plugin_states.json").write_text(
        json.dumps({pm_env.pid: "disabled", "com.已经删掉的": "enabled"}), encoding="utf-8"
    )
    pm_env.pm.scan()
    assert pm_env.pm.get_plugin_state(pm_env.pid) == PluginState.SCANNED
    assert pm_env.pm.get_enabled_plugins() == []
    # 已不存在的插件条目被清掉
    data = json.loads((configs / "plugin_states.json").read_text(encoding="utf-8"))
    assert data == {}


def test_load_plugin_registers_a_permission_state_for_the_instance(pm_env):
    """记录现状（D-136）：`load_plugin` 给实例注入的 `_perm_state` 与
    `pm._perm_states[pid]` **不是同一个对象** —— 之后 `grant_manifest_permissions`
    新建/更新的是后者，插件自己看到的仍是那份"全未授权"的旧对象。
    后果：用户在权限弹窗里点了"允许"，插件的 `notify()` 仍被静默拦掉。
    """
    _install_plugin(pm_env.root, pm_env.pid, permissions=["ui.notification"])
    pm_env.pm.scan()
    pm_env.pm.load_plugin(pm_env.pid)
    instance = _instance(pm_env.pm, pm_env.pid)
    before = instance._perm_state

    pm_env.pm.grant_manifest_permissions(pm_env.pid)
    assert pm_env.pm.check_permission(pm_env.pid, PluginPermission.UI_NOTIFICATION) == "granted"
    assert instance._perm_state is before
    assert instance._perm_state is not pm_env.pm.get_permission_state(pm_env.pid)
    assert instance._perm_state.is_granted(PluginPermission.UI_NOTIFICATION) is False


# ═══════════════════════════════════════════════════════════════════
# 权限闸门与持久化
# ═══════════════════════════════════════════════════════════════════


def test_enable_is_not_blocked_by_undeclared_high_risk_permissions(pm_env):
    """插件**没声明**的权限不能挡住它启用。

    D-138：权限状态给全部 12 个权限都建了条目（未声明的也是 granted=False），
    而三条高风险权限（network.socket / core.launch_hook / core.process）默认未授权 ——
    原来的闸门遍历的是"全部未授权权限"，于是**只要权限状态存在**，
    任何插件启用时都会被这三条拒绝（哪怕清单里一个权限都没写）。
    用户可见后果有两个：
    ① `scan()` 的"恢复上次启用"分支必然失败（它先 `grant_manifest_permissions`
       建出状态、再 enable）→ **重启启动器后插件不再自动启用**，只有一条 warning 日志；
    ② 从市场装完带权限的插件后，服务层调用的 `enable_plugin` 被拒，
       而 `services/plugin_browser_service.install_plugin` 丢弃返回值 → 界面报"安装成功"。
    """
    _install_plugin(pm_env.root, pm_env.pid)  # 不声明任何权限
    pm_env.pm.scan()
    pm_env.pm.load_plugin(pm_env.pid)
    pm_env.pm.grant_manifest_permissions(pm_env.pid)  # 这一步会建出权限状态

    ok, msg = pm_env.pm.enable_plugin(pm_env.pid)
    assert (ok, msg) == (True, "")
    assert pm_env.pm.get_plugin_state(pm_env.pid) == PluginState.ENABLED


def test_enable_is_blocked_by_ungranted_high_risk_permission(pm_env):
    _install_plugin(pm_env.root, pm_env.pid, permissions=["network.http", "core.process"])
    pm_env.pm.scan()
    pm_env.pm.load_plugin(pm_env.pid)
    # 先建出权限状态（模拟"用户在权限弹窗里授权过低/中风险"）
    ok, info = pm_env.pm.grant_manifest_permissions(pm_env.pid)
    assert ok is True and "core.process" in info  # 高风险没回调 → 不授予

    state = pm_env.pm.get_permission_state(pm_env.pid)
    assert state.is_granted(PluginPermission.NETWORK_HTTP) is True
    assert state.is_granted(PluginPermission.CORE_PROCESS) is False

    ok, msg = pm_env.pm.enable_plugin(pm_env.pid)
    assert ok is False and "高风险权限" in msg
    assert pm_env.pm.get_plugin_state(pm_env.pid) != PluginState.ENABLED

    # 用户点"允许"之后就能启用
    pm_env.pm.set_perm_confirm_callback(lambda pid, perm: True)
    pm_env.pm.grant_manifest_permissions(pm_env.pid)
    assert pm_env.pm.get_permission_state(pm_env.pid).is_granted(PluginPermission.CORE_PROCESS) is True
    assert pm_env.pm.enable_plugin(pm_env.pid) == (True, "")


def test_grant_manifest_permissions_ignores_unknown_permission_names(pm_env):
    _install_plugin(pm_env.root, pm_env.pid, permissions=["network.http", "这是编的权限"])
    pm_env.pm.scan()
    ok, info = pm_env.pm.grant_manifest_permissions(pm_env.pid)
    assert (ok, info) == (True, "")
    state = pm_env.pm.get_permission_state(pm_env.pid)
    assert state.is_granted(PluginPermission.NETWORK_HTTP) is True
    assert len(state.get_ungranted_permissions()) == len(list(PluginPermission)) - 1

    # 未发现的插件
    assert pm_env.pm.grant_manifest_permissions("com.不存在") == (False, "插件未发现")


def test_perm_confirm_callback_exception_does_not_break_granting(pm_env):
    def boom(pid, perm):
        raise RuntimeError("弹窗炸了")

    _install_plugin(pm_env.root, pm_env.pid, permissions=["network.http", "core.process"])
    pm_env.pm.scan()
    pm_env.pm.set_perm_confirm_callback(boom)
    ok, info = pm_env.pm.grant_manifest_permissions(pm_env.pid)
    assert ok is True and "core.process" in info
    assert pm_env.pm.get_permission_state(pm_env.pid).is_granted(PluginPermission.NETWORK_HTTP) is True


def test_check_and_request_permission_paths(pm_env):
    _install_plugin(pm_env.root, pm_env.pid, permissions=["network.http"])
    pm_env.pm.scan()
    pm_env.pm.grant_manifest_permissions(pm_env.pid)

    assert pm_env.pm.check_permission(pm_env.pid, PluginPermission.NETWORK_HTTP) == "granted"
    assert pm_env.pm.request_permission(pm_env.pid, PluginPermission.NETWORK_HTTP) is True
    assert pm_env.pm.check_permission("com.不存在", PluginPermission.NETWORK_HTTP) == "denied"
    assert pm_env.pm.request_permission("com.不存在", PluginPermission.NETWORK_HTTP) is False

    # need_confirm + 没有回调 → 拒绝；有回调且同意 → 授权并落盘
    assert pm_env.pm.check_permission(pm_env.pid, PluginPermission.NETWORK_SOCKET) == "need_confirm"
    assert pm_env.pm.request_permission(pm_env.pid, PluginPermission.NETWORK_SOCKET) is False
    pm_env.pm.set_perm_confirm_callback(lambda pid, perm: True)
    assert pm_env.pm.request_permission(pm_env.pid, PluginPermission.NETWORK_SOCKET) is True
    assert pm_env.pm.check_permission(pm_env.pid, PluginPermission.NETWORK_SOCKET) == "granted"

    pm_env.pm.revoke_permission(pm_env.pid, PluginPermission.NETWORK_SOCKET)
    assert pm_env.pm.check_permission(pm_env.pid, PluginPermission.NETWORK_SOCKET) == "need_confirm"


def test_permission_state_survives_a_manager_restart(pm_env):
    _install_plugin(pm_env.root, pm_env.pid, permissions=["network.http"])
    pm_env.pm.scan()
    pm_env.pm.grant_manifest_permissions(pm_env.pid)
    perm_file = pm_env.root / "configs" / f"{pm_env.pid}_perms.json"
    assert perm_file.is_file()

    fresh = PluginManager(pm_env.root)
    fresh.scan()
    assert fresh.check_permission(pm_env.pid, PluginPermission.NETWORK_HTTP) == "granted"
    assert fresh.check_permission(pm_env.pid, PluginPermission.CORE_PROCESS) == "need_confirm"


def test_plugin_config_load_and_save_roundtrip(pm_env):
    _install_plugin(pm_env.root, pm_env.pid)
    configs = pm_env.root / "configs"
    configs.mkdir(parents=True, exist_ok=True)
    (configs / f"{pm_env.pid}.json").write_text(json.dumps({"volume": 30}), encoding="utf-8")
    pm_env.pm.scan()
    assert pm_env.pm.get_plugin_config(pm_env.pid) == {"volume": 30}

    pm_env.pm._configs[pm_env.pid] = {"volume": 55}
    pm_env.pm.save_plugin_config(pm_env.pid)
    assert json.loads((configs / f"{pm_env.pid}.json").read_text(encoding="utf-8")) == {"volume": 55}

    # 没有配置的插件 → 静默什么都不做（不建文件、不抛异常）
    pm_env.pm.save_plugin_config("com.没有配置")
    assert not (configs / "com.没有配置.json").exists()


# ═══════════════════════════════════════════════════════════════════
# 卸载 / 更新回滚 / 插件间 API
# ═══════════════════════════════════════════════════════════════════


def test_uninstall_plugin_removes_files_state_and_hooks(pm_env):
    plugin = _install_plugin(pm_env.root, pm_env.pid)
    pm_env.pm.scan()
    pm_env.pm.enable_plugin(pm_env.pid)
    assert pm_env.pm.uninstall_plugin(pm_env.pid) == (True, "")
    assert not plugin.exists()
    assert pm_env.pm.get_plugin_state(pm_env.pid) is None
    assert pm_env.pm.get_installed_versions_map() == {}
    assert pm_env.pm.hook_bus.get_listener_count(pm_env.pid) == 0
    assert pm_env.pm.uninstall_plugin(pm_env.pid)[0] is False  # 已经没了


def _make_fmpl_file(path: Path, pid: str = "com.demo.p", version: str = "2.0.0", files: dict | None = None) -> Path:
    import zipfile

    payload = {
        "plugin.json": json.dumps(_manifest(pid, version=version), ensure_ascii=False),
        "__init__.py": PLUGIN_BODY,
    }
    payload.update(files or {})
    with zipfile.ZipFile(path, "w") as zf:
        for name, text in payload.items():
            zf.writestr(name, text)
    return path


def test_update_plugin_restores_the_old_version_when_the_package_is_broken(pm_env):
    plugin = _install_plugin(pm_env.root, pm_env.pid)
    (plugin / "keep.txt").write_text("v1 的配置", encoding="utf-8")
    pm_env.pm.scan()

    broken = pm_env.tmp / "broken.fmpl"
    import zipfile

    with zipfile.ZipFile(broken, "w") as zf:
        zf.writestr("__init__.py", PLUGIN_BODY)  # 缺 plugin.json

    ok, msg = pm_env.pm.update_plugin(pm_env.pid, str(broken))
    assert ok is False and "安装新版本失败" in msg
    assert (plugin / "keep.txt").read_text(encoding="utf-8") == "v1 的配置", "失败的更新必须回滚"
    assert (plugin / "plugin.json").is_file()
    assert not (pm_env.root / "temp" / f"_backup_{pm_env.pid}").exists(), "回滚后要清掉备份"


def test_update_plugin_replaces_the_old_version_on_success(pm_env):
    plugin = _install_plugin(pm_env.root, pm_env.pid)
    (plugin / "keep.txt").write_text("v1", encoding="utf-8")
    pm_env.pm.scan()

    good = _make_fmpl_file(pm_env.tmp / "v2.fmpl", files={"new.txt": "v2"})
    ok, msg = pm_env.pm.update_plugin(pm_env.pid, str(good))
    assert (ok, msg) == (True, "")
    assert json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))["version"] == "2.0.0"
    assert (plugin / "new.txt").is_file() and not (plugin / "keep.txt").exists()


def test_export_and_import_plugin_api_and_hook_proxy(pm_env):
    _install_plugin(pm_env.root, pm_env.pid)
    pm_env.pm.scan()
    pm_env.pm.export_api(pm_env.pid, "greet", lambda: "hi")
    assert pm_env.pm.get_plugin_api(pm_env.pid, "greet")() == "hi"
    assert pm_env.pm.get_plugin_api(pm_env.pid, "没有这个") is None
    assert pm_env.pm.get_plugin_api("com.不存在", "greet") is None

    seen: list[str] = []
    pm_env.pm.register_hook(pm_env.pid, HookPoint.APP_STARTUP, lambda **kw: seen.append("hook"))
    pm_env.pm.emit(HookPoint.APP_STARTUP)
    assert seen == ["hook"]

    # 卸载时导出 API 一并清掉
    pm_env.pm.uninstall_plugin(pm_env.pid)
    assert pm_env.pm.get_plugin_api(pm_env.pid, "greet") is None


def test_all_plugin_meta_shape(pm_env):
    _install_plugin(pm_env.root, pm_env.pid, permissions=["network.http"], description={"zh_CN": "演示"})
    pm_env.pm.scan()
    meta = pm_env.pm.get_all_plugin_meta()
    assert list(meta) == [pm_env.pid]
    assert meta[pm_env.pid]["state"] == "scanned"
    assert meta[pm_env.pid]["error"] == ""
    assert meta[pm_env.pid]["manifest"]["description"] == {"zh_CN": "演示"}
    assert meta[pm_env.pid]["permissions"]["network.http"]["granted"] is False


# ═══════════════════════════════════════════════════════════════════
# market.py —— 纯逻辑与"网络全替身"的离线测试
# ═══════════════════════════════════════════════════════════════════

INDEX_PAYLOAD = {
    "categories": {"ui": "界面", "util": "工具"},
    "plugins": [
        {
            "id": "com.a",
            "name": "Alpha",
            "version": "1.1.0",
            "author": "alice",
            "permissions": ["network.http"],
            "description": {"zh_CN": "甲插件", "en_US": "Alpha plugin"},
            "tags": ["ui"],
        },
        {
            "id": "com.b",
            "name": "Beta",
            "version": "0.9.0",
            "author": "bob",
            "description": {"zh_CN": "乙插件"},
            "tags": ["util"],
        },
    ],
}


class _FakeRequestsError(Exception):
    """形状核对：`market` 只捕获 ``requests.RequestException``。"""


class _FakeResp:
    """替身形状核对：`market` 只用到 ``.json()`` / ``.raise_for_status()`` / ``.content``。"""

    def __init__(self, payload=None, content: bytes = b"") -> None:
        self._payload = payload
        self.content = content
        self.raise_calls = 0

    def json(self):
        if self._payload is None:
            raise ValueError("替身没有准备 JSON")
        return self._payload

    def raise_for_status(self) -> None:
        self.raise_calls += 1


class _FakeRequests:
    RequestException = _FakeRequestsError

    def __init__(
        self,
        api_payload=None,
        raw: dict | None = None,
        error: Exception | None = None,
        raw_error: Exception | None = None,
    ) -> None:
        self.api_payload = api_payload
        self.raw = raw or {}
        self.error = error
        self.raw_error = raw_error
        self.calls: list[dict] = []

    def get(self, url, headers=None, timeout=None, **kw):  # noqa: ARG002 - 形状固定
        self.calls.append({"url": url, "headers": headers or {}, "timeout": timeout})
        if self.error is not None:
            raise self.error
        if url.endswith("index.json") or "api.github.com" in url:
            return _FakeResp(payload=self.api_payload)
        if self.raw_error is not None:
            raise self.raw_error
        return _FakeResp(content=self.raw.get(url, b""))


def _market(tmp_path: Path, monkeypatch, **fake_kw):
    from plugin_manager import market as market_mod

    market = market_mod.PluginMarket(cache_dir=tmp_path / "cache", repo="owner/repo", branch="main")
    fake = _FakeRequests(**fake_kw)
    monkeypatch.setattr(market_mod, "requests", fake)
    return market, fake, market_mod


def test_fetch_index_downloads_writes_cache_and_sends_user_agent(tmp_path, monkeypatch):
    market, fake, market_mod = _market(tmp_path, monkeypatch, api_payload=INDEX_PAYLOAD)
    plugins, error = market.fetch_index(force=True)
    assert (len(plugins), error) == (2, "")
    assert len(fake.calls) == 1  # 索引只发一次请求
    call = fake.calls[0]
    assert call["url"] == "https://raw.githubusercontent.com/owner/repo/main/index.json"
    assert call["timeout"] == market_mod.REQUEST_TIMEOUT
    assert "User-Agent" in call["headers"] and "FMCL" in call["headers"]["User-Agent"]

    cached = json.loads((tmp_path / "cache" / "market_index.json").read_text(encoding="utf-8"))
    assert cached["_cached_at"] and cached["plugins"] == INDEX_PAYLOAD["plugins"]
    assert "_validation_errors" not in cached  # 校验错误字段不进缓存


def test_fetch_index_prefers_fresh_cache_and_force_bypasses_it(tmp_path, monkeypatch):
    market, fake, market_mod = _market(tmp_path, monkeypatch, api_payload=INDEX_PAYLOAD)
    market.fetch_index(force=True)
    assert len(fake.calls) == 1

    plugins, error = market.fetch_index()  # 默认走缓存
    assert (len(plugins), error) == (2, "")
    assert len(fake.calls) == 1, "有效缓存不该再发请求"

    market.fetch_index(force=True)
    assert len(fake.calls) == 2


def test_fetch_index_returns_error_when_offline_without_cache(tmp_path, monkeypatch):
    market, fake, _ = _market(tmp_path, monkeypatch, error=_FakeRequestsError("网络断了"))
    plugins, error = market.fetch_index(force=True)
    assert plugins == [] and "获取插件索引失败" in error and "网络断了" in error


def test_is_cache_valid_ttl_boundaries(tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    market, _, _ = _market(tmp_path, monkeypatch, api_payload=INDEX_PAYLOAD)
    assert market._is_cache_valid() is False  # 文件不存在

    cache_file = tmp_path / "cache" / "market_index.json"
    now = datetime.now(timezone.utc)
    for label, cached_at, expected in (
        ("刚缓存", now.isoformat(), True),
        ("过期 2 小时", (now - timedelta(hours=2)).isoformat(), False),
        ("缺 _cached_at", None, False),
    ):
        payload = {"plugins": []}
        if cached_at is not None:
            payload["_cached_at"] = cached_at
        cache_file.write_text(json.dumps(payload), encoding="utf-8")
        assert market._is_cache_valid() is expected, label

    cache_file.write_text("{ 坏 JSON", encoding="utf-8")
    assert market._is_cache_valid() is False


def test_fetch_index_hides_network_failure_when_cache_is_still_fresh(tmp_path, monkeypatch):
    """记录现状（D-137）：``force=True`` 且网络失败、但缓存还新鲜时，
    `fetch_index` 用缓存继续走完并把 ``error`` 返回成**空串** ——
    界面只在 error 非空时提示，于是用户点"刷新"看到的是旧列表且**没有任何失败提示**。
    """
    market, _, _ = _market(tmp_path, monkeypatch, api_payload=INDEX_PAYLOAD)
    market.fetch_index(force=True)

    market2, _fake2, _ = _market(tmp_path, monkeypatch, error=_FakeRequestsError("网络断了"))
    plugins, error = market2.fetch_index(force=True)
    assert len(plugins) == 2
    assert error == "", "现状：这次降级不告诉调用方网络失败了"


def test_get_available_tags_exposes_index_categories(tmp_path, monkeypatch):
    """标签筛选栏的数据源。

    D-125：`PluginMarket._index_data` 从来没有被赋值过（`fetch_index` 只更新
    `_plugin_list`），所以 `get_available_tags()` **永远返回空字典**，
    而 `ui/windows/plugin_browser.py::_update_tag_bar` 看到空字典就
    `pack_forget()` 把整条标签栏隐藏 —— 市场索引里的 ``categories`` 白写了，
    "按标签筛选"这个功能对用户完全不可见（也不会报错）。
    """
    market, _, _ = _market(tmp_path, monkeypatch, api_payload=INDEX_PAYLOAD)
    assert market.get_available_tags() == {}  # 还没拉索引
    market.fetch_index(force=True)
    assert market.get_available_tags() == {"ui": "界面", "util": "工具"}


def test_search_matches_name_id_author_and_description(tmp_path, monkeypatch):
    market, _, _ = _market(tmp_path, monkeypatch, api_payload=INDEX_PAYLOAD)
    market.fetch_index(force=True)

    assert [p["id"] for p in market.search()] == ["com.a", "com.b"]  # 默认按名字排序
    assert [p["id"] for p in market.search("alpha")] == ["com.a"]
    assert [p["id"] for p in market.search("COM.B")] == ["com.b"]
    assert [p["id"] for p in market.search("bob")] == ["com.b"]
    assert [p["id"] for p in market.search("乙插件")] == ["com.b"]
    assert market.search("没有这个词") == []
    assert [p["id"] for p in market.search(tags=["ui"])] == ["com.a"]
    assert [p["id"] for p in market.search("插件", tags=["util"])] == ["com.b"]
    assert market.search(tags=["不存在"]) == []


def test_sort_by_name_author_and_unknown_key(tmp_path, monkeypatch):
    market, _, _ = _market(tmp_path, monkeypatch, api_payload=INDEX_PAYLOAD)
    market.fetch_index(force=True)
    plugins = market.search()

    assert [p["id"] for p in market.sort_by(plugins, "name")] == ["com.a", "com.b"]
    assert [p["id"] for p in market.sort_by(plugins, "name", reverse=True)] == ["com.b", "com.a"]
    assert [p["id"] for p in market.sort_by(plugins, "author")] == ["com.a", "com.b"]
    # 记录现状：文档说支持 "version"，但实现里没有这个分支 → 原样返回（不排序、不报错）
    assert market.sort_by(plugins, "version") == plugins


def test_check_updates_reports_newer_and_older_versions(tmp_path, monkeypatch):
    market, _, _ = _market(tmp_path, monkeypatch, api_payload=INDEX_PAYLOAD)
    market.fetch_index(force=True)

    assert market.check_updates({}) == {}
    result = market.check_updates({"com.a": "1.0.0", "com.b": "1.0.0", "com.c": "1.0.0"})
    assert sorted(result) == ["com.a", "com.b"]  # 索引里没有的插件不出现
    assert result["com.a"]["has_update"] is True and result["com.a"]["is_newer"] is False
    assert result["com.a"]["latest"] == "1.1.0" and result["com.a"]["installed"] == "1.0.0"
    assert result["com.b"]["has_update"] is False and result["com.b"]["is_newer"] is True
    # 版本相同 → 两边都是 False，条目根本不出现
    assert market.check_updates({"com.a": "1.1.0"}) == {}


def test_check_updates_survives_one_unparseable_installed_version(tmp_path, monkeypatch):
    """一个插件版本号读不出来，不能让整个"检查更新"挂掉。

    D-126：`check_updates` 在一个循环里比较所有已装插件，`compare_versions`
    对空串/垃圾串抛 ValueError，异常直接穿出循环 → 整份更新信息一个都拿不到。
    调用方（`ui/windows/plugin_browser.py::_check_updates_async` 的 worker 线程）
    没有任何 try/except，线程直接死掉、`after(0, ...)` 永不执行 ——
    用户看到的只是"更新摘要一直不出现"，没有任何报错。
    """
    market, _, _ = _market(tmp_path, monkeypatch, api_payload=INDEX_PAYLOAD)
    market.fetch_index(force=True)

    result = market.check_updates({"com.a": "1.0.0", "com.b": ""})
    assert result["com.a"]["has_update"] is True, "坏版本号的那个插件被跳过，好插件照常报告"
    assert "com.b" not in result


def test_download_plugin_packs_fmpl_and_cleans_temp_dir(tmp_path, monkeypatch, fake_tempfile):
    raw_url = "https://raw.githubusercontent.com/owner/repo/main/plugins/com.a/__init__.py"
    listing = [
        {"path": "plugins/com.a/plugin.json", "download_url": "https://raw/plugin.json", "size": 10, "type": "file"},
        {"path": "plugins/com.a/__init__.py", "download_url": raw_url, "size": 20, "type": "file"},
    ]
    market, fake, market_mod = _market(
        tmp_path,
        monkeypatch,
        api_payload=listing,
        raw={
            "https://raw/plugin.json": json.dumps(_manifest("com.a")).encode("utf-8"),
            raw_url: b"VALUE = 1\n",
        },
    )

    stages: list[tuple] = []
    fmpl_path, error = market.download_plugin("com.a", progress_callback=lambda *a: stages.append(a))
    assert (fmpl_path, error) == (str(tmp_path / "cache" / "com.a.fmpl"), "")
    assert [s[0] for s in stages] == ["listing", "downloading", "downloading", "packing", "done"]
    assert stages[-1] == ("done", 1, 1)
    assert all(call["headers"].get("User-Agent") for call in fake.calls), "每次请求都必须带 UA"

    import zipfile

    with zipfile.ZipFile(fmpl_path) as zf:
        assert sorted(zf.namelist()) == ["__init__.py", "plugin.json"]
        assert json.loads(zf.read("plugin.json").decode("utf-8"))["id"] == "com.a"
    assert fake_tempfile.created and not fake_tempfile.created[0].exists(), "打包后要清掉临时目录"


class _FakeTempfile:
    """替身形状核对：`market` 只调 ``tempfile.mkdtemp(prefix=...)``。

    目录名**故意不使用 prefix** —— prefix 里带着 ``plugin_id``（可能含 ``..``
    或路径分隔符），照抄会让替身自己越界；这里只需要"一个能被清理掉的临时目录"。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.created: list[Path] = []
        self.prefixes: list[str] = []

    def mkdtemp(self, prefix: str = "") -> str:
        self.prefixes.append(prefix)
        path = self.root / f"tmp{len(self.created)}"
        path.mkdir(parents=True, exist_ok=True)
        self.created.append(path)
        return str(path)


@pytest.fixture()
def fake_tempfile(tmp_path: Path, monkeypatch) -> _FakeTempfile:
    """把 `market` 的临时目录钉在 tmp_path 里（否则它会写到系统临时目录）。"""
    from plugin_manager import market as market_mod

    work = tmp_path / "work"
    work.mkdir()
    fake = _FakeTempfile(work)
    monkeypatch.setattr(market_mod, "tempfile", fake)
    return fake


def test_download_plugin_error_paths(tmp_path, monkeypatch, fake_tempfile):
    listing = [{"path": "plugins/com.a/__init__.py", "download_url": "https://raw/init.py", "size": 1, "type": "file"}]
    market, _, _ = _market(tmp_path, monkeypatch, api_payload=listing, raw={"https://raw/init.py": b"X=1"})

    # 下载下来的东西里没有 plugin.json
    fmpl_path, error = market.download_plugin("com.a")
    assert fmpl_path is None and error == "下载的插件缺少 plugin.json"
    assert not fake_tempfile.created[-1].exists(), "失败也要清掉临时目录"

    # 单个文件下载失败（目录列得出来，文件下不动）
    market3, _, _ = _market(tmp_path, monkeypatch, api_payload=listing, raw_error=_FakeRequestsError("连接被重置"))
    fmpl_path, error = market3.download_plugin("com.a")
    assert fmpl_path is None and "下载失败" in error and "连接被重置" in error

    # 连目录都列不出来：`_list_github_dir` 把网络异常吞成空列表 → 文案只能笼统说
    # "不存在或无法访问"（真实原因只在日志里，见报告里的次要观察）
    market4, _, _ = _market(tmp_path, monkeypatch, api_payload=listing, error=_FakeRequestsError("连接被重置"))
    fmpl_path, error = market4.download_plugin("com.a")
    assert fmpl_path is None and error == "插件 'com.a' 在仓库中不存在或无法访问"

    # 仓库里确实没有这个插件目录（放在最后：它会换掉模块上的 requests 替身）
    market2, _, _ = _market(tmp_path, monkeypatch, api_payload=[])
    fmpl_path, error = market2.download_plugin("com.不存在")
    assert fmpl_path is None and "在仓库中不存在或无法访问" in error


def test_download_plugin_uses_raw_plugin_id_in_output_path(tmp_path, monkeypatch, fake_tempfile):
    """记录现状（D-135）：打包输出路径是 ``cache_dir / f"{plugin_id}.fmpl"``，
    `plugin_id` 未做任何净化 —— 而它来自市场索引 JSON（远端数据）。
    含 ``..`` 的 id 会把 .fmpl 写到缓存目录之外（这里只写在 tmp_path 内，
    不做真正的越界写入，只钉住"路径确实跑出了 cache_dir"这个事实）。
    """
    pid = "../逃逸"
    raw_url = "https://raw/init.py"
    listing = [
        {"path": f"plugins/{pid}/plugin.json", "download_url": "https://raw/plugin.json", "size": 10, "type": "file"},
        {"path": f"plugins/{pid}/__init__.py", "download_url": raw_url, "size": 1, "type": "file"},
    ]
    market, _, _ = _market(
        tmp_path,
        monkeypatch,
        api_payload=listing,
        raw={"https://raw/plugin.json": json.dumps(_manifest(pid)).encode("utf-8"), raw_url: b"X=1"},
    )

    fmpl_path, error = market.download_plugin(pid)
    assert error == ""
    assert fake_tempfile.prefixes == [f"fmcl_plugin_{pid}_"]
    assert Path(fmpl_path).parent != market._cache_dir, "现状：产物落在缓存目录之外"
    assert Path(fmpl_path).is_file()
