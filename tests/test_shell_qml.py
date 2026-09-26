"""`ShellBridge` + 窗口骨架（阶段 2 任务 2.12）测试。

## 两条纪律

1. ``QT_QPA_PLATFORM=offscreen`` 在 import PySide6 之前设置；**先有 `QGuiApplication`
   才能建 `QQmlApplicationEngine`**（顺序反了是进程硬崩 `exit=3221226505`）。
2. **"真引擎加载 `App.qml`"这一半跑在子进程里**（`tests/qml_shell_probe.py`）。
   理由是实测出来的：`QQmlApplicationEngine` + FluentUI 的 `FluWindow` 在同一个进程里
   **跨测试文件不安全** —— 全量测试跑到本文件时（此前已有若干测试文件建过引擎与
   FluWindow），`engine.load(App.qml)` 会以
   `Windows fatal exception: access violation`（`exit=-1073741819`）收场；
   单独跑本文件、或只带两三个 QML 测试文件跑都干净。子进程是全新进程，稳定可靠。
   `tests/test_main_qml_entry.py` 的退出行为测试出于同一理由也用了子进程。
   桥自身的断言仍在本进程里跑（不需要引擎）。

## 覆盖（任务书点名的六条 + 负例自检）

1. 根对象建出来、是 `FluWindow`、材质 `normal`（红线 5 / 闸门 R1）；
2. 12 个一级导航项都在（真 ListView 的委托 objectName 逐个核对）；
3. **点一下真的能切页**（合成鼠标事件打到导航项委托上）；
4. 切页后 `Nav.currentRoute` 变、`PageStack` 上的页面跟着换；
5. 状态条：`statusTimeout == 10000`（A-09 的 10 秒语义）+ 到点自动清空；
6. 加载 / 走查期间**零 QML 报错**；
7. 负例自检：非法路由与非法深链必须在状态条上**看得见**；插件页零报错；
   以及"我要是写错了，闸门 R3/R4/R8 会抓到"（对 `qml/` 的副本做变异后跑真闸门）。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from PySide6.QtCore import Property, QObject, Signal
from PySide6.QtGui import QGuiApplication

from app.bridges import nav_bridge as nb
from app.bridges.shell_bridge import DEFAULT_STATUS_TIMEOUT_MS, ShellBridge

REPO_ROOT = Path(__file__).resolve().parents[1]
GATE_SCRIPT = REPO_ROOT / "scripts" / "check_qml_rules.py"
PROBE_SCRIPT = REPO_ROOT / "tests" / "qml_shell_probe.py"
PROBE_MARKER = "PROBE_JSON:"

#: 02 §3.1 的 12 个一级领域（顺序即导航顺序）。
DOMAINS: Tuple[str, ...] = (
    "home", "versions", "resources", "servers", "online", "backups",
    "music", "tools", "achievements", "agent", "bedrock", "settings",
)

#: 当前平台上**真的会出现**在导航里的领域：基岩版仅 Windows（02 §4.11）。
AVAILABLE_DOMAINS: Tuple[str, ...] = tuple(
    domain for domain in DOMAINS
    if domain != "bedrock" or nb.host_platform() == nb.WINDOWS_PLATFORM
)

#: 这些子串出现在 Qt 消息里 = QML 真的报错了（平台级提示不算）。
QML_ERROR_MARKERS = (
    "TypeError", "ReferenceError", "SyntaxError", "is not defined",
    "Unable to assign", "Cannot assign", "is not a type", "Cannot read property",
    "of null", "failed to load component",
)

#: 明确放行的 Qt 提示（与本任务无关，且**不是** QML 报错）。
BENIGN_MARKERS = (
    "QFontDatabase: Cannot find font directory",
    "does not support propagateSizeHints",
    "No such file or directory",
)


def _qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


_APP = _qapp()


def qml_errors(messages: List[str]) -> List[str]:
    """从 Qt 消息里筛出真正的 QML 报错（放行平台级提示）。"""
    out = []
    for message in messages:
        if any(marker in message for marker in BENIGN_MARKERS):
            continue
        if any(marker in message for marker in QML_ERROR_MARKERS):
            out.append(message)
    return out


_REPORT: Dict[str, Any] = {}


def probe() -> Dict[str, Any]:
    """跑一次子进程探针并缓存结果（一个测试会话只需要跑一次）。"""
    if _REPORT:
        return _REPORT
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(PROBE_SCRIPT)],
        capture_output=True,
        text=True,
        # 必须显式给 encoding：不带它时按本地码页（本机 gbk）解码子进程输出，
        # 遇到 UTF-8 字节会在 reader 线程抛 UnicodeDecodeError（阶段 1 修过同族缺陷）。
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    payload = None
    for line in completed.stdout.splitlines():
        if line.startswith(PROBE_MARKER):
            payload = json.loads(line[len(PROBE_MARKER):])
    assert payload is not None, (
        f"探针没有输出结果（exit={completed.returncode}）：\n"
        f"stdout={completed.stdout[-2000:]!r}\nstderr={completed.stderr[-2000:]!r}"
    )
    payload["_returncode"] = completed.returncode
    payload["_stderr"] = completed.stderr
    _REPORT.update(payload)
    return _REPORT


# ─── 1. 根窗口与骨架四层 ────────────────────────────────────────


def test_root_window_is_created_and_is_a_fluwindow() -> None:
    root = probe()["root"]
    assert root["isWindow"] is True, "根对象必须是窗口（FluWindow 继承自 Window）"
    assert root["objectName"] == "appWindow"
    assert root["visible"] is True
    # 红线 5 + 闸门 R1：主窗口必须显式关掉渐变/亚克力材质
    assert root["effect"] == "normal"
    assert "FMCL" in root["title"]


def test_shell_has_title_bar_navigation_page_stack_and_status_bar() -> None:
    layers = probe()["layers"]
    for name in ("titleBar", "breadcrumb", "navigation", "pageStack", "statusBar", "globalSearchBox"):
        assert layers[name] is True, f"骨架缺 {name}"


def test_entry_registers_every_bridge_including_nav_and_shell() -> None:
    bridges = probe()["bridges"]
    assert bridges["missing"] == [], "阶段 2 的桥必须全部注册成功"
    for name in ("Nav", "Shell", "Theme", "Tr", "Runtime", "Tasks"):
        assert name in bridges["registered"]


# ─── 2. 12 个一级导航项 ─────────────────────────────────────────


def test_navigation_shows_the_domains() -> None:
    report = probe()
    assert report["navItems"] == list(AVAILABLE_DOMAINS), "导航项与顺序来自 Nav 的路由表"
    assert len(report["navItems"]) == (12 if nb.host_platform() == nb.WINDOWS_PLATFORM else 11)
    assert report["navDelegates"] == sorted(f"navItem_{domain}" for domain in AVAILABLE_DOMAINS)
    for name, text in report["navItemTexts"].items():
        assert text.strip(), f"{name} 的导航项没有文案"


# ─── 3. 点一下真的能切页 ────────────────────────────────────────


def test_clicking_a_nav_item_switches_the_page() -> None:
    row = {entry["domain"]: entry for entry in probe()["walk"]}["versions"]
    assert row["route"] == "versions", "点击导航项没有触发 Nav.push"
    assert row["page"] == "versionsPage", "PageStack 上的页面没换"
    assert row["depth"] == 2, "一级导航是压栈（首页仍在栈里）"
    assert "route: versions" in row["routeInfo"], "页面拿到的应该是 push 时冻结的那一帧"


def test_walking_all_domains_by_clicking_switches_every_page() -> None:
    walk = probe()["walk"]
    assert [row["domain"] for row in walk] == list(AVAILABLE_DOMAINS)
    for row in walk:
        assert row["route"] == row["domain"], f"{row['domain']} 切页后 currentRoute 不对"
        assert row["page"] == f"{row['domain']}Page", f"{row['domain']} 的页面没换"
        assert f"route: {row['domain']}" in row["routeInfo"]


def test_page_three_states_are_reachable() -> None:
    icons = {"loading": "loading.svg", "empty": "folder-open.svg", "error": "error.svg"}
    states = {entry["expected"]: entry for entry in probe()["threeStates"]}
    assert set(states) == set(icons), "三态都要能点到"
    for expected, entry in states.items():
        assert entry["state"] == expected
        assert icons[expected] in entry["icon"], f"{expected} 的图标不对：{entry['icon']}"
        assert entry["label"].strip(), f"{expected} 没有文案"


def test_back_button_and_breadcrumb_go_back() -> None:
    crumb = probe()["breadcrumb"]
    assert crumb["items"] == 3 and crumb["depth"] == 3, "面包屑应该是栈里的三帧"
    assert crumb["afterCrumbClick"] == "versions", "点面包屑应该退回到该层"
    assert crumb["afterBackButton"] == "home", "标题栏的返回按钮应该退一层"


# ─── 4. 状态条（A-09 的 10 秒语义） ─────────────────────────────


def test_status_timeout_is_the_legacy_ten_seconds() -> None:
    assert DEFAULT_STATUS_TIMEOUT_MS == 10_000, "旧 set_status 的自动清空时间是 10 秒（对照表 A-09）"
    assert probe()["status"]["timeoutMs"] == 10_000
    assert ShellBridge(nav=nb.NavBridge()).statusTimeout == 10_000


def test_status_bar_shows_the_text_and_can_be_cleared() -> None:
    status = probe()["status"]
    assert status["empty"] == ""
    assert status["afterSet"] == "skeleton check", "状态条必须跟着 Shell 走"
    assert status["level"] == "warning"
    assert status["afterClear"] == ""


def test_status_bar_auto_clears_after_the_timeout() -> None:
    """探针里把 10 秒压成 150 ms 跑同一套机制（真实值由上面那条钉住）。"""
    status = probe()["status"]
    assert status["shortTimeoutBefore"] == "auto clear me", "到点之前必须还在"
    assert status["shortTimeoutAfter"] == "", "到点必须自动清空"


def test_unknown_status_level_degrades_to_info() -> None:
    shell = ShellBridge(nav=nb.NavBridge())
    shell.setStatus("level check", "catastrophic")
    assert shell.statusLevel == "info", "非法级别降级为 info，不抛异常、不发失败信号"


def test_empty_status_text_clears() -> None:
    shell = ShellBridge(nav=nb.NavBridge())
    shell.setStatus("temp", "error")
    shell.setStatus("", "error")
    assert shell.statusText == "" and shell.statusLevel == "info"


# ─── 5. 负例自检：失败必须看得见 ────────────────────────────────


def test_unknown_route_failure_is_visible_in_the_status_bar() -> None:
    failure = probe()["unknownRoute"]
    assert failure["returned"] is False
    assert "versions/does-not-exist" in failure["statusText"], "非法路由必须显示在状态条上"
    assert failure["currentRoute"] == "home", "失败不该改变当前路由"
    assert failure["qmlMessages"] == [], "被拒的跳转不该在 QML 侧留下报错"


def test_bad_deep_link_failure_is_visible_in_the_status_bar() -> None:
    failure = probe()["badDeepLink"]
    assert failure["returned"] is False
    assert "fmcl" in failure["statusText"], "非法深链必须显示原因（协议名）"
    assert failure["currentRoute"] == "home"
    assert failure["depth"] == 1, "被拒的深链不该多出页面"


def test_deep_link_switches_the_page_and_passes_params() -> None:
    link = probe()["deepLink"]
    assert link["returned"] is True
    assert link["page"] == "versionsPage"
    assert link["params"] == {"version": "1.21.4"}
    assert "1.21.4" in link["routeInfo"], "参数必须真的传到页面上"


def test_plugin_page_loads_without_qml_errors() -> None:
    plugin = probe()["pluginPage"]
    assert plugin["registered"] is True and plugin["pushed"] is True
    assert plugin["page"] == "pluginPage", "插件页必须能起（不然插件的 UI 扩展等于全废）"
    assert plugin["route"] == "plugin/com.demo/main"
    assert plugin["qmlMessages"] == [], (
        "插件页少声明 PageStack 传的属性时不许有报错（pushFrame 只传页面声明过的属性）"
    )


def test_no_qml_errors_during_load_and_walk() -> None:
    errors = qml_errors(probe()["qmlMessages"])
    assert errors == [], f"加载 / 走查期间出现 QML 报错：{errors}"
    assert probe()["_stderr"] is not None
    assert qml_errors(probe()["_stderr"].splitlines()) == [], "子进程 stderr 里也不许有 QML 报错"


# ─── 6. Shell 的其余职责（桥级，不需要 QML） ────────────────────


class _FakeTasks(QObject):
    """假的任务桥：只要 `activeCount` + `activeChanged`，够 Shell 用。"""

    activeChanged = Signal()

    def __init__(self, count: int = 0) -> None:
        super().__init__()
        self._count = count

    @Property(int, notify=activeChanged)
    def activeCount(self) -> int:  # noqa: N802 - QML 属性名
        return self._count

    def set_count(self, value: int) -> None:
        self._count = value
        self.activeChanged.emit()


def test_task_count_and_busy_follow_the_tasks_bridge() -> None:
    nav = nb.NavBridge()
    tasks = _FakeTasks(0)
    shell = ShellBridge(nav=nav, tasks=tasks)
    seen: List[int] = []
    shell.taskCountChanged.connect(lambda: seen.append(shell.backgroundTaskCount))
    assert shell.backgroundTaskCount == 0 and shell.busy is False
    tasks.set_count(3)
    assert shell.backgroundTaskCount == 3 and shell.busy is True
    assert seen == [3], "Tasks.activeChanged 必须转成 Shell.taskCountChanged"


def test_shell_forwards_search_and_notification_requests() -> None:
    shell = ShellBridge(nav=nb.NavBridge())
    searches: List[str] = []
    toggles: List[int] = []
    shell.searchRequested.connect(searches.append)
    shell.notificationCenterToggled.connect(lambda: toggles.append(1))
    shell.globalSearch("sodium")
    shell.globalSearch("   ")
    shell.globalSearch("")
    assert searches == ["sodium"], "空关键词不发请求（清空输入框不该触发一次搜索）"
    shell.toggleNotificationCenter()
    shell.toggleNotificationCenter()
    assert len(toggles) == 2, "每次调用发一次，由浮层自己翻可见性"


def test_shell_title_follows_the_route() -> None:
    nav = nb.NavBridge()
    shell = ShellBridge(nav=nav)
    seen: List[str] = []
    shell.titleChanged.connect(lambda: seen.append(shell.currentTitleKey))
    nav.push("versions")
    assert shell.currentTitleKey == "installed_versions" == nav.currentTitleKey
    assert shell.currentTitle == "installed_versions", "没有 Tr 时回退成键名（不是空串）"
    assert seen[-1] == "installed_versions", "Nav.routeChanged 必须转成 Shell.titleChanged"


def test_shell_degrades_when_nav_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nb, "current_nav", lambda: None)
    shell = ShellBridge()
    assert shell.navItems() == [], "Nav 缺席时一级导航为空表，而不是抛异常"
    assert shell.currentTitle == "" and shell.currentTitleKey == ""
    assert shell.backgroundTaskCount == 0 and shell.busy is False
    shell.setStatus("still works", "info")
    assert shell.statusText == "still works", "壳层自身功能不该被 Nav 缺席带走"


# ─── 7. 闸门自检：我要是写错了，R1/R3/R4/R8 会抓到（变异测试） ────


def _load_gate() -> Any:
    """按路径加载 `scripts/check_qml_rules.py`（`scripts/` 不是包）。

    必须先登记进 `sys.modules`：`@dataclass` 处理类时会按 `cls.__module__` 反查
    （与 `tests/test_qml_rules_gate.py` 同一套路，那边已经踩过）。
    """
    cached = sys.modules.get("_fmcl_qml_gate")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("_fmcl_qml_gate", GATE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def _copy_qml_tree(tmp_path: Path) -> Path:
    base = tmp_path / "base"
    shutil.copytree(REPO_ROOT / "qml", base / "qml")
    return base


def test_pristine_shell_qml_passes_every_rule(tmp_path: Path) -> None:
    """我这份 QML 本身零违规（不含 `qml/components` 的白名单判定：那份文档还不存在）。"""
    gate = _load_gate()
    report = gate.run_gate(_copy_qml_tree(tmp_path))
    hits = [(v.path, v.rule) for result in report.results for v in result.violations]
    assert hits == [], f"我的 QML 有闸门违规：{hits}"


@pytest.mark.parametrize(
    ("relative", "original", "mutated", "rule"),
    [
        # 锚点必须**在整个文件里只出现一次**：App.qml 的注释里也写着 `effect: "normal"`，
        # 用裸串替换会改到注释上（探针 poc/_probe_r1_mutation.py 实测踩到），闸门当然不报。
        ("App.qml", '\n    effect: "normal"\n', '\n    effect: "acrylic"\n', "R1"),
        ("shell/Navigation.qml", 'color: Theme?.bgMedium ?? "transparent"', 'color: "#123456"', "R8"),
        ("shell/StatusBar.qml", 'color: Theme?.bgMedium ?? "transparent"', 'color: "\u7ea2\u8272"', "R3"),
        (
            "shell/Breadcrumb.qml",
            "text: Tr?.map[modelData.title_key] ?? modelData.title_key",
            'text: Tr.t("crumb")',
            "R4",
        ),
        ("shell/PageStack.qml", "visible: Nav ? Nav.depth > 0 : true", "gradient: Gradient {}", "R1"),
    ],
)
def test_gate_catches_mutations_of_my_qml(
    tmp_path: Path, relative: str, original: str, mutated: str, rule: str
) -> None:
    """每条变异都必须让**对应**的规则变红 —— 否则上面那句"零违规"就是空断言。"""
    base = _copy_qml_tree(tmp_path)
    target = base / "qml" / relative
    text = target.read_text(encoding="utf-8")
    assert text.count(original) == 1, f"{relative} 里 {original!r} 出现 {text.count(original)} 次，锚点不唯一"
    target.write_text(text.replace(original, mutated, 1), encoding="utf-8", newline="\n")

    gate = _load_gate()
    report = gate.run_gate(base)
    hits = {(v.rule, v.path) for result in report.results for v in result.violations}
    assert (rule, f"qml/{relative}") in hits, f"变异没被 {rule} 抓到：{sorted(hits)}"
