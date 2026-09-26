"""窗口冒烟：每个子窗口必须真的能**构造**出来（阶段 1 的结构性回归守卫）。

为什么需要它：阶段 1 的等价性证据大多是静态的（方法面、签名、逐字节、AST）。
它们能证明"代码没丢东西"，但证明不了"窗口还能打开" —— 而"打开就抛异常"
恰恰是薄委托改造最容易造成的破坏（漏搬一个属性、少一个回调、i18n 键写错）。

本测试把每个子窗口真的实例化一次（不进入事件循环、不联网、不扫用户目录），
构造失败即失败。

## 两个必须说清的前提

1. **构造期真的会做重活**：`ModBrowserWindow.__init__` 末尾会立刻起 worker 搜索
   （第一次写探针时实测打出了真实的 Modrinth 请求）。因此这里把
   `threading.Thread.start` 换成空操作 —— 后台任务全部只登记、不执行。
2. **快照已登记**：`GET_JOB` 的 `_run_in_thread` 是各窗口自己的调度器，
   所以"冻结线程"就够了；本测试不 mock 任何业务函数。

Tk 相关：在一个**模块级** root 上构造（一个进程里建第二个 `Tk()` 会失败 ——
`tests/test_log_widget.py` 里记过这个坑）。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Dict

import pytest

#: (名字, 模块, 类名, 构造形态)
WINDOWS = (
    ("mod_browser", "ui.windows.mod_browser", "ModBrowserWindow", "version_callbacks"),
    ("server_mod_browser", "ui.windows.server_mod_browser", "ServerModBrowserWindow", "version_callbacks"),
    ("modpack_browser", "ui.windows.modpack_browser", "ModpackBrowserWindow", "callbacks"),
    ("plugin_browser", "ui.windows.plugin_browser", "PluginBrowserWindow", "plugin_manager_market"),
    ("resource_manager", "ui.windows.resource_manager", "ResourceManagerWindow", "version_callbacks"),
    (
        "server_resource_manager",
        "ui.windows.server_resource_manager",
        "ServerResourceManagerWindow",
        "version_callbacks",
    ),
    ("modpack_install", "ui.windows.modpack_install", "ModpackInstallWindow", "callbacks"),
    ("modpack_server", "ui.windows.modpack_server", "ModpackServerWindow", "callbacks"),
    ("account_manager", "ui.windows.account_manager", "AccountManagerWindow", "account_system"),
    ("launcher_settings", "ui.windows.launcher_settings", "LauncherSettingsWindow", "callbacks"),
    (
        "server_config_editor",
        "ui.windows.server_config_editor",
        "ServerConfigEditorWindow",
        "version_callbacks",
    ),
    ("backup_settings", "ui.windows.backup_settings", "BackupSettingsWindow", "config"),
    ("netease_login", "ui.windows.netease_login", "NeteaseLoginWindow", "parent_only"),
    ("plugin_manager", "ui.windows.plugin_manager", "PluginManagerWindow", "plugin_manager"),
)


class _StubAccountSystem:
    """账号系统最小替身：只提供构造期与列表渲染读到的两个属性。"""

    def __init__(self) -> None:
        self.accounts: list = []
        self.current_account_id = None


class _StubPluginManager:
    def get_installed_plugins(self) -> list:
        return []


class _StubPluginMarket:
    def __init__(self) -> None:
        self.index: list = []


class _StubConfig:
    """全局配置最小替身：备份设置窗口构造期只读几个字段。

    `base_dir` 必须是**真的 Path**（窗口会做 `self.config.base_dir / "backups"`）；
    其余未定义字段返回 None（窗口里都是 `getattr(..., None) or 默认值` 的写法）。
    """

    backup_enabled = True
    backup_interval_days = 7
    backup_max_count = 10
    backup_before_launch = False
    backup_dir = ""

    def __init__(self) -> None:
        import tempfile

        self.base_dir = Path(tempfile.gettempdir())

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def __getattr__(self, item: str) -> Any:
        return None


@pytest.fixture(scope="module")
def frozen_threads():
    """把 `threading.Thread.start` 冻住：构造期不起任何后台任务。

    用 `MonkeyPatch.context()` 是为了**离开作用域后一定还原** ——
    全局改 `Thread.start` 若不还原，会悄悄影响同一进程里其它测试。
    """
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(threading.Thread, "start", lambda self: None)
        yield mp


@pytest.fixture(scope="module")
def tk_root():
    import customtkinter as ctk

    root = ctk.CTk()
    root.withdraw()
    yield root
    root.destroy()


@pytest.fixture(scope="module")
def callbacks(tmp_path_factory) -> Dict[str, Any]:
    tmp = tmp_path_factory.mktemp("smoke")
    (tmp / ".minecraft" / "versions").mkdir(parents=True, exist_ok=True)
    (tmp / "server").mkdir(parents=True, exist_ok=True)
    return {
        "get_minecraft_dir": lambda: str(tmp / ".minecraft"),
        "get_server_dir": lambda: str(tmp / "server"),
        "get_versions_dir": lambda: str(tmp / ".minecraft" / "versions"),
        "get_latest_release": lambda: "1.20.4",
        "get_jdz_token": lambda: "tok",
        "get_config": lambda: {"download_threads": 4},
        "rename_instance": lambda old, new: (True, new),
        "get_installed_versions": lambda: ["1.20.4"],
    }


@pytest.mark.parametrize(
    "name,module_name,class_name,kind",
    WINDOWS,
    ids=[w[0] for w in WINDOWS],
)
def test_window_constructs(
    name: str,
    module_name: str,
    class_name: str,
    kind: str,
    tk_root,
    callbacks: Dict[str, Any],
    frozen_threads,
) -> None:
    """构造 → 立即销毁。任何异常都说明这个窗口打不开了。"""
    module = __import__(module_name, fromlist=[class_name])
    cls = getattr(module, class_name)

    if kind == "version_callbacks":
        window = cls(tk_root, "1.20.4", callbacks)
    elif kind == "callbacks":
        window = cls(tk_root, callbacks)
    elif kind == "plugin_manager_market":
        window = cls(tk_root, _StubPluginManager(), _StubPluginMarket())
    elif kind == "plugin_manager":
        window = cls(tk_root, _StubPluginManager())
    elif kind == "parent_only":
        window = cls(tk_root)
    elif kind == "config":
        window = cls(tk_root, _StubConfig())
    elif kind == "account_system":
        window = cls(tk_root, _StubAccountSystem(), on_account_changed=lambda: None)
    else:  # pragma: no cover - 配置错误
        pytest.fail(f"未知构造形态 {kind}")

    try:
        assert window is not None
    finally:
        window.destroy()


def test_smoke_covers_every_window_class_in_ui_windows() -> None:
    """防止"新增窗口但忘了加进冒烟清单"。

    做法：扫 `ui/windows/*.py` 里所有 `ctk.CTkToplevel` 子类，
    与上面的清单对照；不在清单里的要么补进来，要么加进下面的白名单并写理由。
    """
    import ast
    import io

    known = {cls for _n, _m, cls, _k in WINDOWS}
    #: 有理由不进冒烟清单的窗口类
    ALLOWED_MISSING = {
        "AddAccountDialog": "账号管理窗口内部的模态对话框，构造需要回调与用户输入",
        "VersionPickerDialog": "版本选择子窗口，随主窗口生命周期创建",
        "PluginPermissionDialog": "插件权限确认对话框，需要权限清单",
    }

    found: set[str] = set()
    for path in sorted((Path(__file__).resolve().parent.parent / "ui" / "windows").glob("*.py")):
        tree = ast.parse(io.open(path, encoding="utf-8").read())
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {ast.unparse(b) for b in node.bases}
            if any("CTkToplevel" in b for b in bases):
                found.add(node.name)

    missing = sorted(found - known - set(ALLOWED_MISSING))
    assert not missing, (
        "以下 CTkToplevel 子类没有进入冒烟清单，也没有写理由：\n"
        + "\n".join(f"  {m}" for m in missing)
        + "\n请把可无头构造的加进 WINDOWS，其余加进 ALLOWED_MISSING 并写明为什么。"
    )
