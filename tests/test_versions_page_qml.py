"""版本页 QML 验收测试（阶段 3 任务 3.2）。

跑法：本文件用**子进程**跑 `tests/qml_versions_probe.py`（理由见那个文件的 docstring：
真引擎 + FluWindow 在同一个进程里跨测试文件不安全），再对探针输出做断言。

判据的三条纪律（与 `tests/test_home_page_qml.py` 一致）：

1. **断言的是界面真的显示了什么**（读控件的 `text` / `enabled` / `contentState`），
   不是"桥里那个属性等于什么"；
2. **点击走真实坐标投递**，所以"点了没反应"这类接线断掉一定会被抓到；
3. 每个数字都对得上服务层给的假数据 —— 行文本就是 `VersionService` 拼的那一份
   （`1.20.4-forge-49.0.26 [Forge 49.0.26]` / `1.19.2  (1.19.2)`，两个空格都在）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_SCRIPT = REPO_ROOT / "tests" / "qml_versions_probe.py"
PROBE_MARKER = "PROBE_JSON:"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


_REPORT: Dict[str, Any] = {}


def probe() -> Dict[str, Any]:
    """跑一次子进程探针并缓存（一个会话只跑一次，约 3~5 秒）。"""
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
        if PROBE_MARKER in line:
            payload = json.loads(line[line.index(PROBE_MARKER) + len(PROBE_MARKER):])
    assert payload is not None, (
        f"探针没有输出结果（exit={completed.returncode}）：\n"
        f"stdout={completed.stdout[-2000:]!r}\nstderr={completed.stderr[-2000:]!r}"
    )
    payload["_returncode"] = completed.returncode
    _REPORT.update(payload)
    return _REPORT


def test_probe_exits_clean() -> None:
    assert probe()["_returncode"] == 0


def test_every_recorded_item_exists() -> None:
    """21 个锚点都要真的在树里 —— 少一个就说明页面结构被改掉了。"""
    missing = [name for name, found in probe()["objectNames"].items() if not found]
    assert missing == [], f"版本页缺少这些控件：{missing}"


def test_page_is_reached_by_route() -> None:
    assert probe()["page"] is True
    assert probe()["route"] == "versions"


# ─── 三态 ──────────────────────────────────────────────────────


def test_page_starts_loading() -> None:
    """还没有任何扫描结果时是"加载中"，不是"空数据"。"""
    assert probe()["state_loading"] == "loading"


def test_page_shows_empty_state_when_nothing_is_installed() -> None:
    assert probe()["state_empty"] == "empty"


def test_page_reaches_ready_state_with_versions() -> None:
    assert probe()["state_ready"] == "ready"


# ─── 列表（B-01）───────────────────────────────────────────────


def test_rows_are_rendered() -> None:
    assert probe()["row_count"] == 3


def test_row_text_is_what_the_service_composed() -> None:
    """显示文本与旧实现逐字一致：加载器带版本、原版两个空格。"""
    assert probe()["row_titles"] == [
        "1.19.2  (1.19.2)",
        "1.20.1-fabric-0.15.0 [Fabric 0.15.0]",
        "1.20.4-forge-49.0.26 [Forge 49.0.26]",
    ]


def test_count_label_shows_the_total() -> None:
    assert probe()["count_text"] == "3"


# ─── 搜索与排序（B-19）─────────────────────────────────────────


def test_typing_in_the_search_box_filters_the_list() -> None:
    assert probe()["rows_filtered"] == 1
    assert probe()["count_text_filtered"] == "1 / 3"


def test_clearing_the_search_box_restores_the_list() -> None:
    assert probe()["rows_after_clear"] == 3


def test_changing_the_sort_dropdown_reaches_the_service() -> None:
    assert probe()["sort_after_switch"] == "vanilla"
    assert probe()["filtered_calls"] == ["vanilla"], "下拉那一下要真的带着新排序键去问服务"


def test_refresh_button_forces_a_rescan() -> None:
    """A-04：刷新按钮必须**强制失效缓存**，而不是吃缓存再扫一遍。"""
    assert probe()["load_forces"][-1] is True
    assert probe()["load_forces"].count(True) >= 1


def test_install_entry_is_always_reachable() -> None:
    """安装入口必须**一直可见**（用户实测报过"没有找到在哪里装版本"）。

    空态里那个动作按钮只在"一个版本都没装"时出现 —— 装了第一个版本之后，
    入口就只剩工具条上这一个了。
    """
    assert probe()["route_after_install_click"] == "versions/install"


# ─── 选中与行操作（B-02）───────────────────────────────────────


def test_clicking_a_row_selects_it_and_tells_the_status_bar() -> None:
    assert probe()["status_after_select"] == "已选择：1.19.2"
    assert probe()["detail_calls"], "选中要取一次详情"


def test_row_buttons_are_disabled_without_a_selection() -> None:
    assert probe()["launchEnabled_no_selection"] is False


def test_launch_and_resource_entries_unlock_with_a_selection() -> None:
    assert probe()["launchEnabled_selected"] is True
    assert probe()["resourceEnabled_selected"] is True


def test_rename_and_delete_buttons_reach_the_service() -> None:
    assert probe()["rename_calls"] == ["1.19.2"]
    assert probe()["remove_calls"] == ["1.19.2"]


def test_verify_and_open_folder_reach_the_service() -> None:
    """B-21 / B-22：新增的两个动作在详情页上真的接上了。"""
    assert probe()["verify_calls"] == ["1.20.4-forge-49.0.26"]
    assert probe()["open_folder_calls"] == ["1.20.4-forge-49.0.26"]


# ─── 启动与强杀（复用 B-03 / B-04）─────────────────────────────


def test_launch_button_starts_the_selected_version() -> None:
    assert probe()["launched"] == ["1.20.4-forge-49.0.26"]


def test_kill_button_is_disabled_while_idle_and_works_while_running() -> None:
    assert probe()["killEnabled_idle"] is False
    assert probe()["killEnabled_running"] is True
    assert probe()["kills"] == 1


# ─── 详情（B-20）───────────────────────────────────────────────


def test_detail_route_shows_the_detail_pane() -> None:
    assert probe()["detail_route"] == "versions/detail"
    assert probe()["detail_pane_visible"] is True
    assert probe()["detail_title"] == "1.20.4-forge-49.0.26"


def test_detail_fields_come_from_the_bridge() -> None:
    """游戏版本 / 加载器 / 所需 Java / 模组数量 / 路径 —— 五个字段逐个对。"""
    assert probe()["detail_values"] == [
        "1.20.4", "forge", "Java 17", "5", "C:/mc/versions/1.20.4-forge",
    ]


def test_detail_warns_when_the_version_is_gone() -> None:
    assert probe()["detail_missing_shown"] is True
    assert probe()["detail_missing_hidden"] is True


def test_back_returns_to_the_list() -> None:
    assert probe()["route_after_back"] == "versions"


# ─── 语言与报错 ────────────────────────────────────────────────


def test_texts_follow_the_language_switch() -> None:
    assert probe()["refresh_text_zh"] == "刷新"
    assert probe()["refresh_text_en"] == "Refresh"
    assert probe()["search_placeholder_en"] == "Search versions"


@pytest.mark.parametrize("phase", ["qml_errors_at_start", "qml_errors_after_back", "qml_errors"])
def test_no_qml_errors(phase: str) -> None:
    """阶段 3 验收标准第 4 条：遍历页面不许有 QML 报错。

    三个阶段分开断言：`qml_errors_at_start` 是"刚打开页面"、`..._after_back` 是
    "详情返回列表之后"（**返回时会 pop 掉一个页面实例**，绑定在这一刻最容易踩空 ——
    3.2 实测踩到过 15 条 `Cannot read property 't' of null`，所以这条必须分开钉）。
    """
    assert probe()[phase] == []


def test_every_bridge_is_registered() -> None:
    report = probe()
    assert report["bridges_missing"] == []
    assert "Versions" in report["bridges_registered"]
