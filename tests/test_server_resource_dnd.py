"""D-104 / L-Q6：服务器资源管理窗口的拖拽安装（阶段 1 第 7 轮补齐）。

缺陷原状：窗口的 `rm_drop_hint` 文案写着"把 .jar 拖到此处安装"，
**但整份代码从未注册过任何拖拽目标** —— 用户照做毫无反应（客户端窗口是同一份文案，
却真的接了 `tkinterdnd2`）。

这里不建真实窗口（Tk 顶层窗口慢且脆），用 `object.__new__` 造骨架实例、
把 `_set_status` / `_refresh_mod_list` 换成记录器，然后直接喂一个假的 Drop 事件，
验证：合法文件被真的复制进服务端 mods 目录、状态栏与刷新被触发、
非法扩展名与空串被安静忽略。

顺带钉住"依赖漂移"这个更深的坑：`tkinterdnd2` 原先只在 `requirements*.txt` 里，
不在 `pyproject.toml` —— 于是 `uv sync` 建出来的环境里 **HAS_DND 恒为 False**，
拖拽静默失效（而发布产物用的是 requirements，反而是好的）。见
`poc/_audit_dependency_drift.py`。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


class _FakeEvent:
    def __init__(self, data: str) -> None:
        self.data = data


def _make_window(server_dir: Path, version_id: str = "1.20.4-forge-49.0.26"):
    """造一个只有 `_on_drop` 需要的那点状态/协作对象的骨架实例。"""
    from ui.windows.server_resource_manager import ServerResourceManagerWindow

    win = object.__new__(ServerResourceManagerWindow)
    win.version_id = version_id
    win.callbacks = {"get_server_dir": lambda: str(server_dir)}
    win.statuses: List[str] = []
    win.refreshes = 0
    win._set_status = lambda text: win.statuses.append(text)
    win._refresh_mod_list = lambda: setattr(win, "refreshes", win.refreshes + 1)
    return win


def _touch(path: Path, content: bytes = b"PK\x03\x04fake") -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return str(path)


def test_drop_installs_copied_jars_into_version_scoped_mods_dir(tmp_path: Path) -> None:
    win = _make_window(tmp_path)
    src = tmp_path / "downloads"
    a = _touch(src / "cool-mod.jar")
    b = _touch(src / "another.jar")

    win._on_drop(_FakeEvent(f"{{{a}}} {{{b}}}"))

    mods_dir = tmp_path / "1.20.4-forge-49.0.26" / "mods"
    assert (mods_dir / "cool-mod.jar").exists(), "拖拽的模组没有被复制进版本隔离目录"
    assert (mods_dir / "another.jar").exists()
    assert win.refreshes == 1, "安装后没有刷新列表"
    assert win.statuses, "没有写状态栏"


def test_drop_without_loader_uses_global_mods_dir(tmp_path: Path) -> None:
    win = _make_window(tmp_path, version_id="1.20.4")
    a = _touch(tmp_path / "dl" / "vanilla-mod.jar")

    win._on_drop(_FakeEvent(a))

    assert (tmp_path / "mods" / "vanilla-mod.jar").exists()
    assert not (tmp_path / "1.20.4").exists()


def test_drop_ignores_non_mod_extensions(tmp_path: Path) -> None:
    win = _make_window(tmp_path)
    txt = _touch(tmp_path / "dl" / "readme.txt")
    png = _touch(tmp_path / "dl" / "thumb.png")

    win._on_drop(_FakeEvent(f"{{{txt}}} {{{png}}}"))

    # 一个都没被接受时**连目录都不该建**（`_get_mods_dir` 有建目录副作用，
    # 而"没有可用文件"这条路径不该触发它）。
    mods_dir = tmp_path / "1.20.4-forge-49.0.26" / "mods"
    assert not mods_dir.exists() or list(mods_dir.iterdir()) == [], "非 .jar/.zip 文件不该被复制"
    assert win.refreshes == 0
    assert win.statuses, "应当给出「没有可用文件」的提示"


def test_drop_ignores_nonexistent_paths(tmp_path: Path) -> None:
    win = _make_window(tmp_path)
    win._on_drop(_FakeEvent(str(tmp_path / "不存在.jar")))
    assert win.refreshes == 0 and win.statuses


def test_drop_handles_empty_event_data(tmp_path: Path) -> None:
    win = _make_window(tmp_path)
    win._on_drop(_FakeEvent(""))
    assert win.refreshes == 0 and win.statuses


def test_drop_accepts_zip_files(tmp_path: Path) -> None:
    """服务端导入的白名单是 .jar **与** .zip（与 `_select_file_install` 的过滤器一致）。"""
    win = _make_window(tmp_path)
    z = _touch(tmp_path / "dl" / "mod-1.0.zip")
    win._on_drop(_FakeEvent(z))
    assert (tmp_path / "1.20.4-forge-49.0.26" / "mods" / "mod-1.0.zip").exists()


def test_drop_registration_is_wired_into_init() -> None:
    """结构守卫：`__init__` 必须在 HAS_DND 为真时安排 `_register_dnd`。

    这条用源码断言而不是实例化：真建一个 CTkToplevel 需要 Tk 主窗口与 100ms 延迟。
    """
    src = io.open(
        REPO_ROOT / "ui" / "windows" / "server_resource_manager.py", encoding="utf-8"
    ).read()
    assert "if HAS_DND:" in src, "HAS_DND 判定没了，拖拽注册不会发生"
    assert "self.after(100, self._register_dnd)" in src
    assert "drop_target_register(DND_FILES)" in src
    assert "dnd_bind(\"<<Drop>>\", self._on_drop)" in src


def test_tkinterdnd2_is_a_declared_dependency() -> None:
    """依赖漂移守卫（D-111）：跑的地方必须真的装上 tkinterdnd2。

    这条断言的是**运行时可用**，不是"清单里有" —— 清单写了但没装（正是本缺陷）
    同样会让拖拽静默失效。
    """
    import ui.windows.server_resource_manager as srm

    assert srm.HAS_DND, (
        "tkinterdnd2 不可用 → 两个资源窗口的拖拽会静默失效。"
        "请确认它写在 pyproject.toml 的 dependencies 里（uv 管理），"
        "而不是只在 requirements*.txt 里"
    )
