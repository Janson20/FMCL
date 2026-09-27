"""启动画面可见性（界面返工 A 组 / 缺陷 D-142）。

## 它钉住的是用户报上来的现象

> 启动时的加载窗在加载完后没消失。

`qml/Splash.qml` 旧版本写的是 **`visible: true` 字面量**，全工程没有任何一行把它关掉；
Python 侧的启动时序四条退出路径全都正常跑完了，那个置顶窗口却一直盖在界面上。

## 为什么必须跑真 QML（而不是断言"那一行有绑定"）

同类的判据全都在这一层：`Splash.qml` 的 `visible` 到底算出了什么、`FluWindow` 的
`autoVisible` 有没有把主窗口提前显示出来、`transientParent` 会不会把启动画面连带藏掉
（**这条是返工 A 组实测踩到的**：Qt 不允许 transient 子窗口在父窗口不可见时显示，
所以"藏主窗口"这个动作一度让启动画面也不可见了）。

真引擎加载 `App.qml` 必须放在子进程里（`FluWindow` 同进程开第二个引擎会 access violation，
见 `tests/qml_shell_probe.py` 的文件头），所以这里跑 `tests/qml_startup_probe.py` 并断言它
打印出来的 `PROBE_JSON`。

## 负例（非空证明）

`poc/_probe_startup_visibility_mutation.py` 把 `Splash.qml` 的 `visible` 改回字面量
`true`（就是旧版本那个写法），本用例必须变红 —— 那条证据脚本会自己跑一遍这个文件。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
PROBE = TESTS_DIR / "qml_startup_probe.py"

#: 探针本身要跑 1 秒最小显示 + 300ms 淡出 + 装配（实测整体 4.5s 左右），给 6 倍余量。
PROBE_TIMEOUT = 180

#: 探针必须给出的判据（少一条就说明探针被改坏了，而不是"通过"）
REQUIRED_CHECKS = (
    "装载时启动画面可见",
    "装载时主窗口不可见",
    "初始化中文案已翻译",
    "初始化中启动画面仍可见",
    "收尾后已标记 dismissed",
    "收尾后 startupActive 为假",
    "淡出结束后启动画面隐藏",
    "主窗口最终可见",
    "没有 QML 绑定错误",
)


@pytest.fixture(scope="module")
def probe() -> Dict[str, Any]:
    """跑一次探针（整个模块只跑一次）。"""
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(PROBE)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), timeout=PROBE_TIMEOUT, env=env,
    )
    payload = None
    for line in completed.stdout.splitlines():
        if line.startswith("PROBE_JSON:"):
            payload = json.loads(line[len("PROBE_JSON:"):])
    assert payload is not None, (
        f"探针没有输出 PROBE_JSON（exit={completed.returncode}）\n"
        f"stdout={completed.stdout[-3000:]}\nstderr={completed.stderr[-2000:]}"
    )
    assert completed.returncode == 0, (
        f"探针自判不通过（exit={completed.returncode}）：errors={payload.get('errors')}\n"
        f"checks={json.dumps(payload.get('checks'), ensure_ascii=False)}"
    )
    return payload


def test_probe_reports_all_expected_checks(probe: Dict[str, Any]) -> None:
    """九条判据一条都不能少 —— 少一条说明探针被削弱了，那不算通过。"""
    checks = probe.get("checks") or {}
    missing = [name for name in REQUIRED_CHECKS if name not in checks]
    assert missing == [], f"探针缺少判据: {missing}"
    failed = [name for name in REQUIRED_CHECKS if not checks[name]]
    assert failed == [], f"判据不通过: {failed}"


def test_splash_is_hidden_after_the_startup_flow(probe: Dict[str, Any]) -> None:
    """**用户报的那个 bug**：启动流程收尾之后，启动画面必须真的不可见。"""
    final = probe["observations"][-1]
    assert final["dismissed"] is True, "启动流程没有收尾，后面两条判据都不成立"
    assert final["splashVisible"] is False, (
        f"{final['atMs']}ms 时启动画面仍可见（tag={final['tag']}）—— 加载窗又关不掉了"
    )


def test_main_window_is_hidden_while_the_splash_is_up(probe: Dict[str, Any]) -> None:
    """启动画面占屏期间主窗口不许露出来（否则就是"两层窗口叠在一起"）。"""
    first = probe["observations"][0]
    assert first["splashVisible"] is True, "装载完成时启动画面就不可见（用户会看到一片空白）"
    assert first["mainVisible"] is False, "主窗口在启动画面期间可见"


def test_main_window_is_visible_after_the_flow(probe: Dict[str, Any]) -> None:
    """收尾之后主窗口必须可见（藏起来不还回来同样是坏结果）。"""
    final = probe["observations"][-1]
    assert final["mainVisible"] is True, "启动流程结束后主窗口没有显示出来"


def test_splash_is_a_real_top_level_window(probe: Dict[str, Any]) -> None:
    """启动画面必须与主窗口**解除 transient 关系**。

    实测（`poc/_probe_splash_window_visibility.py`）：Qt 不允许 transient 子窗口
    在父窗口不可见时显示 —— 主窗口一藏，启动画面会被连带压成 `visible=false`。
    这条判据把那次踩坑固化成回归：`transientParent` 只要不是 none 就红。
    """
    for snap in probe["observations"]:
        assert snap["splashTransientParent"] == "none", (
            f"启动画面还挂在主窗口下（transientParent={snap['splashTransientParent']}）"
        )


def test_status_text_is_translated_in_the_splash(probe: Dict[str, Any]) -> None:
    """启动画面的状态文案必须来自语言文件，而不是把 i18n 键名印在脸上。

    `StartupController.statusText` 给的是键名（Python 侧不持有界面文案），
    翻译在 QML 侧做。返工 A 组顺手补上了 `startup_initializing` / `startup_ready`
    两个键 —— 在那之前界面上显示的是 `startup_initializing` 这个键名本身。
    """
    while_running = probe["observations"][1]
    assert while_running["statusText"] not in ("", "startup_initializing"), (
        f"启动中的状态文案没翻译: {while_running['statusText']!r}"
    )
    assert while_running["statusText"] != "", "启动中的状态文案是空的"
