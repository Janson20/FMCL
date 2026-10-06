"""安装向导 QML 验收测试（阶段 3 任务 3.3）。

跑法：本文件用**子进程**跑 `tests/qml_install_probe.py`（理由见那个文件的 docstring：
真引擎 + FluWindow 在同一个进程里跨测试文件不安全），再对探针输出做断言。

判据的三条纪律（与 `tests/test_versions_page_qml.py` 一致）：

1. **断言的是界面真的显示了什么**（读控件的 `text` / `visible` / `enabled`），
   不是"桥里那个属性等于什么"；
2. **点击走真实坐标投递**，所以"点了没反应"这类接线断掉一定会被抓到；
3. 探针里的 `Tasks` 是**真的** `TaskBridge`（只有 `install` 服务是假的），
   所以"进度真的合并着回来了、取消真的传到了工作者的取消标志"都被验到。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_SCRIPT = REPO_ROOT / "tests" / "qml_install_probe.py"
PROBE_MARKER = "PROBE_JSON:"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


_REPORT: Dict[str, Any] = {}


def probe() -> Dict[str, Any]:
    """跑一次子进程探针并缓存（一个会话只跑一次，约 8~12 秒：里面有两次安装流程）。"""
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


def loader_labels_en() -> List[str]:
    return list(probe().get("loader_labels_en", []))


# ─── 探针本身 ──────────────────────────────────────────────────


def test_probe_exits_clean() -> None:
    assert probe()["_returncode"] == 0


def test_probe_reports_no_qml_errors() -> None:
    """整场跑完 0 条 QML 报错 —— 尤其是页面被弹出（销毁）之后那条
    `Cannot read property '…' of null`：本页是可弹出的页面，绑定与信号处理
    都必须扛得住"桥还活着、页面已经没了"。"""
    assert probe()["qml_errors"] == []
    assert probe()["qml_errors_at_start"] == []


def test_every_recorded_item_exists() -> None:
    """23 个锚点都要真的在树里 —— 少一个就说明页面结构被改掉了。"""
    missing = [name for name, found in probe()["objectNames"].items() if not found]
    assert missing == [], f"安装向导缺少这些控件：{missing}"


def test_both_bridges_are_registered() -> None:
    report = probe()
    assert "Install" in report["bridges_registered"]
    assert report["bridges_missing"] == []


# ─── 入口与路由 ────────────────────────────────────────────────


def test_entry_button_from_versions_page_opens_the_wizard() -> None:
    """3.2 的「安装新版本」按钮 → 3.3 的向导页：这条一断，用户就找不到安装入口
    （3.2 人工验收时就报过"没有找到在哪里装版本"）。"""
    assert probe()["page"] is True
    assert probe()["route_after_entry"] == "versions/install"


# ─── 可用版本三态（SOP 第 5 条）────────────────────────────────


def test_list_has_empty_state() -> None:
    assert probe()["list_state_empty"] == "empty"
    assert probe()["empty_state_visible"] is True


def test_list_reaches_ready_state() -> None:
    assert probe()["list_state_ready"] == "ready"
    assert probe()["grid_visible"] is True


def test_page_itself_stays_ready_without_the_list() -> None:
    """整页永远 ready：离线（取不到可用版本）时**手动输入版本 ID 这条路必须还在**，
    否则最常见的故障场景反而装不了东西。"""
    page_state = probe()["objectNames"]["installVersionId"]
    assert page_state is True
    assert probe()["list_state_empty"] == "empty"


# ─── 分页与标签页（B-13）──────────────────────────────────────


def test_page_size_is_twenty_like_the_old_ui() -> None:
    report = probe()
    assert report["page_count"] == 3, "45 个正式版 ÷ 每页 20 应当是 3 页"
    assert report["chip_count_page1"] == 20
    assert report["chip_count_page2"] == 20
    assert report["page_after_next"] == 2


def test_switching_tab_resets_page_and_shows_snapshots() -> None:
    report = probe()
    assert report["tab_after_switch"] == "snapshot"
    assert report["page_after_tab_switch"] == 1, "切标签页要把页码重置回 1（旧界面语义）"
    assert report["snapshot_chips"] == 3
    assert report["tab_back"] == "release"


def test_pager_is_visible() -> None:
    assert probe()["pager_visible"] is True


# ─── 9 种加载器（B-10）────────────────────────────────────────


def test_loader_combo_has_nine_entries_in_old_order() -> None:
    report = probe()
    assert report["loader_count"] == 9
    assert report["loader_labels"] == [
        "无", "Forge", "Fabric", "NeoForge", "Quilt",
        "LiteLoader", "LegacyFabric", "Cleanroom", "OptiFine",
    ]


def test_choosing_a_loader_writes_back_to_the_bridge() -> None:
    report = probe()
    assert report["loader_after_pick"] == "forge"
    assert report["loader_name_after_pick"] == "Forge", "传给核心的必须是显示名"


# ─── 兼容提示（B-10 的后半）───────────────────────────────────


def test_compat_hint_shows_the_service_answer() -> None:
    report = probe()
    assert report["compat_key_bad"] == "version_compat_bad"
    assert report["compat_bar_text_bad"] == "Forge 不支持 1.20.4，装下去大概率会失败"
    assert report["compat_key_ok"] == "version_compat_ok"
    assert report["compat_bar_text_ok"] == "Forge 支持 1.20.4"


# ─── 点一行回填（B-13）────────────────────────────────────────


def test_clicking_a_version_fills_the_input() -> None:
    report = probe()
    assert report["version_id_after_pick"] == "1.20.2"
    assert report["field_text_after_pick"] == "1.20.2", "输入框里也要看得见"


# ─── 安装：进度 / 取消 / 结果 / 去向（B-11 + 用户裁决 4）─────


def test_install_registers_as_a_background_task() -> None:
    """安装必须经 `Tasks` 桥提交：状态栏的后台任务指示（`Shell.busy`）只认它。

    断言用**成员包含**而不是全等：同一次装配里别的桥也会登记自己的任务种类
    （3.4 的 `settings.java_scan` 就是），全等会把"别人加了一种任务"当成回归。
    """
    report = probe()
    assert "version.install" in report["task_kinds"]
    assert report["active_tasks"] == 1
    assert report["shell_busy"] is True


def test_progress_is_visible_during_install() -> None:
    report = probe()
    assert report["busy_during_install"] is True
    assert report["progress_card_visible"] is True
    assert report["progress_bar_visible"] is True
    assert report["progress_moved"] > 0.0
    #: 判据是**格式**（已完成 / 总数），不是某一档具体数字：并行跑测试（`-n auto`）时
    #: CPU 一忙，进度会多走一两档 —— 曾经写死 `== "1 / 5"` 因此偶发红。
    assert re.fullmatch(r"\d+ / 5", str(report["progress_label"])), (
        f"有总数时要显示「已完成 / 总数」，实际 {report['progress_label']!r}"
    )


def test_cancel_stops_the_worker_and_reports_cancelled() -> None:
    report = probe()
    assert report["state_after_cancel"] == "cancelled"
    assert report["cancel_calls"] == [["install_cancelled", "1.20.4"]], "取消要真的传到工作者的取消标志"
    assert report["active_tasks_after_cancel"] == 0


def test_success_shows_the_result_and_returns_to_the_version_list() -> None:
    report = probe()
    assert report["state_after_success"] == "done"
    assert report["result_bar_text"] == "1.20.4-forge-49.0.26 安装成功!"
    assert report["result_level"] == "success"
    assert report["installed_signals"] == ["1.20.4-forge-49.0.26"]
    assert report["installed_version"] == "1.20.4-forge-49.0.26"
    assert report["route_after_success"] == "versions", "装完自动回版本列表（用户裁决 4）"


def test_modpack_button_pushes_the_entry_route() -> None:
    """B-12 只要入口：页面属 3.8，路由先通。"""
    assert probe()["route_after_modpack_click"] == "versions/modpack"


def test_empty_version_id_is_refused_with_a_status_message() -> None:
    assert probe()["status_after_empty_install"] == "请输入版本 ID"


# ─── 语言热切换 ────────────────────────────────────────────────


def test_loader_labels_follow_the_language() -> None:
    labels_en = loader_labels_en()
    assert labels_en[0] == "None", "英文下「无」要变 None"
    assert labels_en[1] == "Forge"
    assert labels_en[8] == "OptiFine"
    assert probe()["loader_labels_zh"][0] == "无"


def test_buttons_follow_the_language() -> None:
    assert probe()["retry_text_en"] == "Retry install"


# ─── 语言入口（2026-10-06 验收：只能手改 config.json 换语言）────────


def test_appbar_language_button_opens_the_shared_overlay() -> None:
    """顶栏的地球图标要能打开**A-27 那个**语言浮层（一份实现，两个入口）。

    用户验收原话："每次把 config.json 改成 zh_CN，启动 qt 版直接就是英文" ——
    根子就是 A-27 只在首次启动问一次、设置页要到 3.4 才有，中间没有换语言的地方。
    """
    report = probe()
    assert report["language_overlay_visible"] is True
    assert report["language_title_nonempty"] is True
    assert report["language_option_count"] >= 4, "四种语言都要列出来"
    assert report["language_overlay_closed"] is True, "确认之后要关掉"
    assert report["language_after_confirm"] in ("zh_CN", "en_US", "ja_JP", "zh_TW")
