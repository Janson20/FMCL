"""首次启动选语言的 QML 验收测试（A-27；用户 2026-10-05 转达的网友需求）。

跑法：本文件用**子进程**跑 `tests/qml_first_run_language_probe.py`（理由同
`tests/test_home_page_qml.py`：真引擎 + FluWindow 跨测试文件不安全），
再对探针输出做断言。

需求原文：*"应网友要求，首次启动时选择语言（tk 版不用改，即将废弃）"*。
判据落在**用户能感知的顺序**上：先问语言 → 选 → 界面当场变 → 才给协议。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_SCRIPT = REPO_ROOT / "tests" / "qml_first_run_language_probe.py"
PROBE_MARKER = "PROBE_JSON:"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_REPORT: Dict[str, Any] = {}


def probe() -> Dict[str, Any]:
    """跑一次子进程探针并缓存。"""
    if _REPORT:
        return _REPORT
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(PROBE_SCRIPT)],
        capture_output=True,
        text=True,
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
    _REPORT.update(payload)
    return _REPORT


def test_probe_exits_clean() -> None:
    assert probe()["_returncode"] == 0


def test_overlay_is_hidden_until_the_chain_asks_for_it() -> None:
    report = probe()
    assert report["overlay_before"] is False
    assert report["overlay_after"] is True


def test_language_comes_before_the_agreement() -> None:
    """先问语言再给条款：协议全文是按语言渲染的，顺序反了用户先看到看不懂的文字。"""
    report = probe()
    assert report["agreement_opened_before_choice"] is False
    assert report["agreement_after_choice"] is True


def test_options_come_from_the_translation_bridge() -> None:
    """四种语言各一行，选项来自 `Tr.availableLanguages`（不在 QML 里写死名单）。"""
    assert probe()["options"] == [
        "languageOption_en_US", "languageOption_ja_JP",
        "languageOption_zh_CN", "languageOption_zh_TW",
    ]


def test_current_language_is_preselected() -> None:
    assert probe()["preselected"] == ["languageOption_zh_CN"]


def test_clicking_an_option_selects_it() -> None:
    report = probe()
    assert report["clicked_option"] is True
    assert report["checked_after_click"] == ["languageOption_en_US"]


def test_confirming_switches_and_persists_the_language() -> None:
    report = probe()
    assert report["language_after"] == "en_US", "界面没有当场切成英文"
    assert report["config_language"] == "en_US", "语言没有落盘"
    assert report["config_chosen"] is True, "「已选过」没有落盘（下次启动还会问）"
    assert report["config_saves"] >= 1


def test_overlay_closes_after_confirming() -> None:
    assert probe()["overlay_final"] is False


def test_no_qml_errors() -> None:
    errors: List[str] = probe()["qml_errors"]
    assert errors == [], "语言浮层产生了 QML 报错：\n" + "\n".join(errors)
