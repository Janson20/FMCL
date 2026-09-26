"""UI 冒烟测试自身的测试（阶段 2 任务 2.19 的验收件）。

## 它测什么

1. **跑一遍真东西**：`python tests/ui_smoke.py --json` 必须退出码 0，JSON 结构合法；
2. **12 个一级页面都在结果里**：期望名单从 `app/bridges/nav_bridge.py` 的路由表算出来
   （不写死一份可能过期的名单），每一页都要有：路由 id、页面根对象名、三态、截图；
3. **截图真的存在且非空**：不只看路径 —— 文件必须在、大小 ≥ 1 KB、PNG magic 正确、
   与冒烟脚本记录的字节数一致、SHA256 两两不同（否则"12 张图长得一样"也能算过）；
4. **主题 / 语言热切换**都各切了一遍（5 预设 / 4 语言），且切换后零报错；
5. **负例自检**（`ui_smoke.py --self-test`）：非法路由必须让脚本失败、坏绑定必须被
   消息计数判出来、`IGNORED_PATTERNS` 不许吞掉真正的 TypeError。

## 慢测

一次完整走查实测约 **21 s**（子进程装配 ~0.8 s + 12 页 × 三态 + 5 主题 + 4 语言 +
两个组件的性能段），自检再约 15 s。所以整个文件标了 `pytest.mark.slow`：

    .venv\\Scripts\\python.exe -m pytest tests/ -q -m "not slow"   # 跳过它
    .venv\\Scripts\\python.exe -m pytest tests/test_ui_smoke.py -q  # 只跑它

> 注：`slow` 标记没有登记进 `pyproject.toml` 的 `markers`（本任务只允许改
> `qml/`、`tests/` 下的指定文件），所以 pytest 会打一条 `PytestUnknownMarkWarning`。
> 标记本身照常生效（`-m` 选择不受"是否登记"影响）。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
SMOKE_SCRIPT = TESTS_DIR / "ui_smoke.py"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

import ui_smoke  # noqa: E402 - 被测对象就是它（纯标准库，不会拉起 Qt）

from app.bridges import nav_bridge as nb  # noqa: E402

pytestmark = pytest.mark.slow

#: 三态的名字与顺序（与 `tests/_smoke_driver.py:STATES` 同一份）
STATES = ("loading", "empty", "error")

#: 冒烟脚本一次完整走查的实测耗时约 21 s；给 pytest 的超时余量按 4 倍算。
SMOKE_TIMEOUT = 300

#: JSON 里必须有的顶层键（结构合法性的判据）
REQUIRED_KEYS = (
    "schema", "kind", "ok", "child", "environment", "summary", "failures", "ignored",
    "ignoredPatterns", "routes", "pages", "themes", "languages", "shots",
)


def expected_domains() -> List[str]:
    """内置的一级领域 id（从路由表算，当前平台不支持的自动排除，如非 Windows 的基岩版）。"""
    return [
        route.id for route in nb.build_default_routes()
        if route.level == 0 and (not route.platform or route.platform == nb.host_platform())
    ]


@pytest.fixture(scope="module")
def smoke_json(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, Any]:
    """跑一次 `tests/ui_smoke.py --json`（整个模块只跑一次，结果缓存）。"""
    out_dir = tmp_path_factory.mktemp("ui_smoke")
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(SMOKE_SCRIPT), "--json", "--out", str(out_dir)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), timeout=SMOKE_TIMEOUT,
    )
    assert completed.returncode == 0, (
        f"UI 冒烟测试失败（exit={completed.returncode}）：\n"
        f"stdout={completed.stdout[-4000:]}\nstderr={completed.stderr[-2000:]}"
    )
    try:
        data = json.loads(completed.stdout)
    except ValueError as exc:  # noqa: BLE001
        raise AssertionError(
            f"--json 的输出不是合法 JSON（{exc}）：\n{completed.stdout[:2000]}"
        ) from exc
    data["_outDir"] = str(out_dir)
    data["_stderr"] = completed.stderr
    return data


# ─── 1. 结构 ────────────────────────────────────────────────────


def test_json_structure_is_valid(smoke_json: Dict[str, Any]) -> None:
    for key in REQUIRED_KEYS:
        assert key in smoke_json, f"--json 结果缺键 {key}"
    assert smoke_json["schema"] == ui_smoke.SCHEMA
    assert smoke_json["kind"] == "ui-smoke"
    assert smoke_json["ok"] is True
    assert smoke_json["child"]["returncode"] == 0
    assert isinstance(smoke_json["pages"], list) and isinstance(smoke_json["shots"], list)
    assert isinstance(smoke_json["summary"], dict)
    assert smoke_json["summary"]["failedChecks"] == 0
    assert smoke_json["summary"]["failedMessages"] == 0
    assert smoke_json["failures"] == [], f"不该有失败：{smoke_json['failures']}"


def test_table_renderer_uses_the_same_verdict(smoke_json: Dict[str, Any]) -> None:
    """人读的表与 `--json` 走**同一套判定**（`main()` 只是选了打印哪一个）。

    这里不另起一个子进程跑 21 s 的完整走查：把同一份 payload 交给 `evaluate()` 再渲染，
    渲染出来的表必须是绿的、且 12 页都在表里。
    """
    payload = {key: value for key, value in smoke_json.items() if not key.startswith("_")}
    run = ui_smoke.ChildRun(returncode=0, payload=payload)
    report = ui_smoke.evaluate(run, Path(smoke_json["_outDir"]))
    assert report.ok, f"同一份数据在人读路径上判成了失败：{report.failures[:3]}"
    table = ui_smoke.render_table(report)
    assert "汇总：" in table and "结论：全绿" in table
    for row in smoke_json["pages"]:
        assert row["route"] in table and row["screenshot"]["file"] in table


def test_cli_helpers_exit_zero() -> None:
    """`--list-ignored` 走一遍 CLI（便宜的一次真子进程），确认入口本身没坏。"""
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(SMOKE_SCRIPT), "--list-ignored"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), timeout=60,
    )
    assert completed.returncode == 0, completed.stderr[-1000:]
    assert "登记在案的平台噪声" in completed.stdout
    for entry in ui_smoke.IGNORED_PATTERNS:
        assert entry.pattern in completed.stdout


# ─── 2. 12 个一级页面 ───────────────────────────────────────────


def test_all_builtin_domains_were_walked(smoke_json: Dict[str, Any]) -> None:
    walked = [row["route"] for row in smoke_json["pages"]]
    for domain in expected_domains():
        assert domain in walked, f"一级领域 {domain} 没有被遍历（走了 {walked}）"
    assert len(walked) >= len(expected_domains())
    assert len(set(walked)) == len(walked), "同一个路由被走了两次"


def test_every_page_has_route_object_and_three_states(smoke_json: Dict[str, Any]) -> None:
    for row in smoke_json["pages"]:
        route = row["route"]
        assert row["pushed"] is True, f"{route} 没切过去"
        assert row["currentRoute"] == route, f"{route} 的 currentRoute 不对"
        assert row["pageObjectName"], f"{route} 的页面根对象没有 objectName"
        assert row["matchesByName"] >= 1, f"{route} 的页面根对象按 objectName 找不到"
        assert row["foundByName"] is True, f"{route} 按 objectName 找回来的对象不对"
        assert row["pageVisible"] is True, f"{route} 的前台页面不可见"
        assert row["routeIdOnPage"] == route, f"{route} 页面拿到的帧不对"
        assert row["stackError"] == "", f"{route} 的 PageStack 报了 {row['stackError']!r}"
        states = row["states"]
        assert [entry["state"] for entry in states] == list(STATES), (
            f"{route} 的三态没跑全：{states}"
        )
        for entry in states:
            assert entry["got"] == entry["state"], f"{route} 的 {entry['state']} 没生效"
            assert entry["label"].strip(), f"{route} 的 {entry['state']} 没有文案"
            assert entry["icon"].startswith("file:"), f"{route} 的 {entry['state']} 没有图标"


# ─── 3. 截图 ────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_screenshots_exist_and_are_not_empty(smoke_json: Dict[str, Any]) -> None:
    shots = smoke_json["shots"]
    assert len(shots) >= len(smoke_json["pages"]), "截图数量少于页面数量"
    for shot in shots:
        path = Path(shot["path"])
        assert path.is_file(), f"截图文件不存在：{path}"
        size = path.stat().st_size
        assert size >= ui_smoke.MIN_SHOT_BYTES, f"{shot['file']} 只有 {size} 字节"
        assert path.read_bytes()[:8] == ui_smoke.PNG_MAGIC, f"{shot['file']} 不是 PNG"
        assert size == shot["bytes"], f"{shot['file']} 的实际大小与记录不一致"
        assert shot["verified"] is True, f"{shot['file']} 没通过冒烟脚本自己的校验"
        assert shot["width"] > 0 and shot["height"] > 0
        assert _sha256(path) == shot["sha256"], f"{shot['file']} 的内容与记录不一致"


def test_page_screenshots_are_all_different(smoke_json: Dict[str, Any]) -> None:
    """12 页的截图必须两两不同 —— 一样就说明"截的是同一张图"，等于没截。"""
    hashes: Dict[str, str] = {}
    for row in smoke_json["pages"]:
        shot = row["screenshot"]
        digest = next(entry["sha256"] for entry in smoke_json["shots"]
                      if entry["file"] == shot["file"])
        assert digest not in hashes, f"{row['route']} 与 {hashes[digest]} 的截图完全相同"
        hashes[digest] = row["route"]
    assert len(hashes) == len(smoke_json["pages"])


# ─── 4. 主题 / 语言热切换 ───────────────────────────────────────


def test_theme_hot_switch_covers_every_preset(smoke_json: Dict[str, Any]) -> None:
    available = smoke_json["themes"]["available"]
    assert len(available) >= 5, f"预设主题应有 5 个：{available}"
    rows = smoke_json["themeRows"]
    assert [row["name"] for row in rows] == available, "不是每个可用主题都切了一次"
    for row in rows:
        assert row["current"] == row["name"], f"{row['name']} 没切过去"
        assert row["revisionAfter"] > row["revisionBefore"], "主题切换必须自增 revision"
        assert row["messageCount"] == 0, f"{row['name']} 切换时报了 QML 消息：{row['messages']}"
        assert row["screenshot"]["bytes"] > 0
    assert smoke_json["summary"]["themes"] == len(rows)


def test_language_hot_switch_covers_every_language(smoke_json: Dict[str, Any]) -> None:
    codes = sorted(path.stem for path in (REPO_ROOT / "ui" / "locales").glob("*.json"))
    available = smoke_json["languages"]["available"]
    assert sorted(available) == codes, f"语言列表与 ui/locales 不一致：{available} vs {codes}"
    rows = smoke_json["languageRows"]
    assert [row["code"] for row in rows] == available, "不是每种语言都切了一次"
    for row in rows:
        assert row["ok"] is True and row["current"] == row["code"], f"{row['code']} 没切过去"
        assert row["keyCount"] > 1000, f"{row['code']} 的键数不对：{row['keyCount']}"
        assert row["messageCount"] == 0, f"{row['code']} 切换时报了 QML 消息：{row['messages']}"
    assert smoke_json["summary"]["languages"] == len(rows)


# ─── 5. 组件（任务 2.18）与平台噪声登记 ─────────────────────────


def test_logview_and_logdemo_evidence(smoke_json: Dict[str, Any]) -> None:
    view = smoke_json.get("logView")
    demo = smoke_json.get("logDemo")
    assert view and demo, "组件段的证据缺失"
    assert view["invokeAppend"] is True, "Python 侧调不通 append（QMetaObject.invokeMethod）"
    assert view["cap"]["settled"] == view["cap"]["maxLines"], f"上限没对齐：{view['cap']}"
    assert view["cap"]["peak"] <= view["cap"]["maxLines"] + 512, f"瞬态越界：{view['cap']}"
    assert view["search"]["visibleHitBoxes"] > 0, "搜索高亮没渲染出来"
    assert view["autoScrollAfterDrag"] is False, "用户上滚之后自动滚动没关掉"
    assert abs(view["contentYAfter"] - view["contentYBefore"]) < 1.0, "追加日志抢了用户的滚动位置"
    assert view["copy"]["clipboardChars"] > 0, "复制全部没有写进剪贴板"
    assert view["lineCountAfterClear"] == 0
    assert demo["reload"]["signalCount"] == 1, "读取真实日志的按钮没有形成回环"
    assert demo["afterGenerate5000"] > 4000 and demo["afterClear"] == 0


def test_ignored_patterns_are_registered_and_few(smoke_json: Dict[str, Any]) -> None:
    patterns = smoke_json["ignoredPatterns"]
    assert patterns, "登记表不能为空（为空就说明判定逻辑被改坏了）"
    for entry in patterns:
        assert entry["pattern"] and entry["reason"], f"登记项缺字段：{entry}"
    ignored = smoke_json["ignored"]
    assert len(ignored) <= 5, f"放行的消息太多（{len(ignored)}），判据可能被放松了：{ignored}"
    for entry in ignored:
        assert entry["reason"], f"放行的消息没有登记理由：{entry}"


# ─── 6. 负例自检（把冒烟脚本当被测对象） ────────────────────────


def test_self_test_catches_all_three_negative_cases(tmp_path: Path) -> None:
    """`--self-test` 自己会断言三条负例都被抓住；它绿 = 冒烟测试不是空断言。"""
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(SMOKE_SCRIPT), "--self-test", "--out", str(tmp_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), timeout=SMOKE_TIMEOUT,
    )
    assert completed.returncode == 0, (
        f"负例自检没全绿（exit={completed.returncode}）：\n{completed.stdout[-3000:]}"
    )
    assert "[OK] ①" in completed.stdout and "[OK] ②" in completed.stdout and "[OK] ③" in completed.stdout
    assert "FAIL" not in completed.stdout


def test_classifier_never_swallows_real_errors() -> None:
    """直接钉住分类函数：真正的 QML 报错不许被 `IGNORED_PATTERNS` 吞掉。"""
    for text in (
        "TypeError: Cannot read property 'x' of null",
        "ReferenceError: foo is not defined",
        "qrc:/x.qml:12:5: Unable to assign [undefined] to int",
        "PageStack sync failed: component not ready",
    ):
        assert ui_smoke.classify({"mode": "QtWarningMsg", "text": text}) == "failure", text
    for entry in ui_smoke.IGNORED_PATTERNS:
        assert ui_smoke.classify({"mode": "QtWarningMsg", "text": entry.pattern}) == "ignored"
        assert ui_smoke.ignored_reason(entry.pattern) == entry.reason
    # 平台噪声的判定必须是**子串命中**，不能顺手放行整段前缀
    assert ui_smoke.ignored_reason("QFontDatabase: something else entirely") == ""
    assert ui_smoke.classify({"mode": "QtCriticalMsg", "text": "未知问题"}) == "failure"


def test_severity_alone_decides_for_unknown_text() -> None:
    """没见过的文本按级别判：warning 及以上算失败，debug/info 不算。"""
    assert ui_smoke.classify({"mode": "QtWarningMsg", "text": "some new platform complaint"}) == "failure"
    assert ui_smoke.classify({"mode": "QtDebugMsg", "text": "qml: 正常日志"}) == "ok"
    assert ui_smoke.classify({"mode": "QtInfoMsg", "text": "qml: 正常日志"}) == "ok"
