"""界面后端（`ui_backend`）与入口分派的闸门（阶段 3 前置）。

## 背景

阶段 3 要逐页把界面从 Tk 迁到 QML，迁移期**两套界面并存**：`main.py`（Tk，仍是默认）与
`main_qml.py`（QML）。为了能"一条命令切过去、出了问题自动回来"，新增了三样东西：

1. `config.ui_backend`（`tk` / `qml`，默认 `tk`）；
2. 入口分派 `main._dispatch_ui`（优先级：命令行 `--ui` > 配置 > 默认）；
3. 回退：要求 qml 但装配前就能判定起不来（没装 Qt / 没有 FluentUI 模块 / 没有 QML 入口）
   → 记日志 + 回退经典界面；命令行显式要求时额外弹一次提示。

## 这里钉住的几件事

* **默认后端必须是 tk**：QML 侧的 12 个页面还是占位壳，提前切默认 = 把空页面发给用户；
* 非法值（手改配置文件、拼错的命令行参数）一律回落，**不许**让入口因为一个字符串起不来；
* 回退原因必须是人话（能看出缺什么），不是空字符串；
* `main.py` 不许引入 argparse（入口只有一套手写 CLI 语义，迁移红线 2）。

## 为什么配置文件测试全部用临时目录

`Config` 会读写真实的 `config.json`（仓库根或用户数据目录）。测试必须注入 `base_dir`，
否则会在开发机/CI 上**改掉真实配置**（历史上有过这类事故）。
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

import main  # noqa: E402
import main_qml  # noqa: E402
from config import Config  # noqa: E402
from scripts.build_plan import DEFAULT_UI_BACKEND, UI_BACKENDS, missing_qml_requirements  # noqa: E402


@pytest.fixture()
def temp_config(tmp_path: Path) -> Config:
    """一个落在临时目录里的配置对象（绝不碰真实 config.json）。"""
    return Config(base_dir=str(tmp_path))


# ─── 1. 配置项 ─────────────────────────────────────────────────


def test_default_backend_is_tk(temp_config: Config) -> None:
    assert temp_config.ui_backend == "tk" == DEFAULT_UI_BACKEND
    assert temp_config.ui_backend in UI_BACKENDS


def test_backend_round_trips_through_config_json(tmp_path: Path) -> None:
    first = Config(base_dir=str(tmp_path))
    first.ui_backend = "qml"
    first.save_config()

    on_disk = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert on_disk["ui_backend"] == "qml", "save_config 漏了 ui_backend，重启后会丢"

    reloaded = Config(base_dir=str(tmp_path))
    assert reloaded.ui_backend == "qml"


def test_invalid_backend_falls_back_instead_of_crashing(tmp_path: Path) -> None:
    """手改配置文件写坏了也不能让入口起不来。"""
    for broken in ("QML 界面", "", 123, None, ["qml"]):
        (tmp_path / "config.json").write_text(json.dumps({"ui_backend": broken}), encoding="utf-8")
        loaded = Config(base_dir=str(tmp_path))
        assert loaded.ui_backend == "tk", f"{broken!r} 应回落 tk，实际 {loaded.ui_backend!r}"


def test_valid_backend_is_case_insensitive(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(json.dumps({"ui_backend": "QmL"}), encoding="utf-8")
    assert Config(base_dir=str(tmp_path)).ui_backend == "qml"


# ─── 2. 命令行解析 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "argv,expected",
    [
        ([], None),
        (["--ui", "qml"], "qml"),
        (["--ui=qml"], "qml"),
        (["--ui=tk"], "tk"),
        (["login", "-name", "Steve"], None),
        (["-A", "看看版本"], None),
        (["--ui"], None),  # 缺值：不猜，交给默认后端
    ],
)
def test_parse_ui_arg(argv: list, expected: Any) -> None:
    assert main._parse_ui_arg(argv) == expected


def test_main_entry_has_exactly_one_cli_parser() -> None:
    """入口不许引入 argparse：现有 CLI 语义是手写解析（与 `main_qml` 共用同一套）。"""
    tree = ast.parse((REPO_ROOT / "main.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "argparse" not in imported, "main.py 引入了 argparse，会出现第二套 CLI 语义"


# ─── 3. 分派优先级 ─────────────────────────────────────────────


def test_dispatch_prefers_cli_over_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.config, "ui_backend", "qml", raising=False)
    monkeypatch.setattr(main, "_probe_qml_backend", lambda: (True, ""))
    assert main._dispatch_ui(["--ui", "tk"]) == ("tk", ""), "命令行必须压过配置"

    monkeypatch.setattr(main.config, "ui_backend", "tk", raising=False)
    assert main._dispatch_ui(["--ui", "qml"]) == ("qml", ""), "命令行必须压过配置"


def test_dispatch_uses_config_when_cli_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_probe_qml_backend", lambda: (True, ""))
    monkeypatch.setattr(main.config, "ui_backend", "qml", raising=False)
    assert main._dispatch_ui([]) == ("qml", "")

    monkeypatch.setattr(main.config, "ui_backend", "tk", raising=False)
    assert main._dispatch_ui([]) == ("tk", "")


def test_dispatch_falls_back_when_qml_unusable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.config, "ui_backend", "qml", raising=False)
    monkeypatch.setattr(main, "_probe_qml_backend", lambda: (False, "没装 PySide6（测试替身）"))
    action, why = main._dispatch_ui([])
    assert action == "fallback"
    assert why, "回退原因不能是空字符串：用户/日志要能看出缺什么"
    assert "PySide6" in why


def test_dispatch_with_broken_cli_value_stays_on_tk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main, "_probe_qml_backend", lambda: (True, ""))
    assert main._dispatch_ui(["--ui", "这是啥"]) == ("tk", "")


# ─── 4. QML 可用性探测 ─────────────────────────────────────────


def test_probe_reports_missing_pyside(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main.importlib.util, "find_spec", lambda name: None)
    usable, why = main._probe_qml_backend()
    assert usable is False
    assert "PySide6" in why


def test_probe_reports_missing_fluentui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_qml, "qml_import_path", lambda: tmp_path / "这里没有 FluentUI")
    usable, why = main._probe_qml_backend()
    assert usable is False
    assert "FluentUI" in why


def test_probe_reports_missing_app_qml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_qml, "qml_import_path", lambda: tmp_path)  # 目录存在，过第一关
    monkeypatch.setattr(main_qml, "app_qml_path", lambda name="App.qml": tmp_path / name)
    usable, why = main._probe_qml_backend()
    assert usable is False
    assert "App.qml" in why


def test_probe_accepts_this_checkout() -> None:
    """本机齐活时必须判定可用 —— 否则回退逻辑会把能跑的 QML 界面挡掉。"""
    if missing_qml_requirements(REPO_ROOT):
        pytest.skip("本机没有自编译的 FluentUI 模块（third_party/* 不入库）")
    assert main._probe_qml_backend() == (True, "")


# ─── 5. 回退提示的触发条件 ─────────────────────────────────────


def test_fallback_notice_only_for_explicit_cli_request() -> None:
    """配置文件里的 qml 回退只记日志；只有命令行显式要求时才弹提示。

    判据用源码里的实际条件（`_parse_ui_arg(_argv) is not None`）而不是"跑一遍看有没有弹窗"：
    弹窗在测试里会阻塞。这里断言的是"分派结果里带着可判断的信息"。
    """
    source = (REPO_ROOT / "main.py").read_text(encoding="utf-8")
    assert "_action == \"fallback\" and _parse_ui_arg(_argv) is not None" in source
