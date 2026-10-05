"""首页 QML 验收测试（阶段 3 任务 3.1）。

跑法：本文件用**子进程**跑 `tests/qml_home_probe.py`（理由见那个文件的 docstring：
真引擎 + FluWindow 在同一个进程里跨测试文件不安全），再对探针输出做断言。

判据的三条纪律（与 `tests/test_shell_qml.py` 一致）：

1. **断言的是界面真的显示了什么**（读控件的 `text` / `enabled` / `contentState`），
   不是"桥里那个属性等于什么"；
2. **点击走真实坐标投递**，所以"点了没反应"这类接线断掉一定会被抓到；
3. 每个数字都对得上服务层给的假数据 —— 例如 `12/40 已解锁` 来自
   `AchievementService.summarize()` 的结果（假服务给 40/12）。
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
PROBE_SCRIPT = REPO_ROOT / "tests" / "qml_home_probe.py"
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
    report = probe()
    assert report["_returncode"] == 0


def test_every_recorded_item_exists() -> None:
    """17 个锚点都要真的在树里 —— 少一个就说明页面结构被改掉了。"""
    missing = [name for name, found in probe()["objectNames"].items() if not found]
    assert missing == [], f"首页缺少这些控件：{missing}"


def test_page_reaches_ready_state() -> None:
    assert probe()["state_ready"] == "ready"


def test_page_shows_empty_state_when_there_is_nothing() -> None:
    """首启（没账号、没皮肤、没启动过、成就 0）→ 空态，而不是"一堆空白卡片"。"""
    assert probe()["state_empty"] == "empty"


def test_account_card_shows_the_current_account() -> None:
    report = probe()
    assert report["accountName"] == "Steve"


def test_recent_version_and_launch_state() -> None:
    report = probe()
    assert report["recentVersion"] == "1.20.4"
    assert report["gameStateText"] == "游戏未运行"
    assert report["launchEnabled"] is True
    # 没在跑的时候不能强杀（旧界面的 kill 按钮同样是 DISABLED）
    assert report["killEnabled"] is False


def test_launch_button_calls_the_service_with_the_recent_version() -> None:
    """点"启动游戏" → 桥 → 服务，且版本号取自"最近使用版本"（不弹输入框）。"""
    assert probe()["launched"] == ["1.20.4"]


def test_skin_card_shows_the_file_name_then_follows_select_and_remove() -> None:
    report = probe()
    assert report["skinName"] == "hero.png"
    assert report["skin_selected"] == ["C:/mc/skins/other.png"], "选择皮肤必须落到服务上"
    assert report["skinName_after"] == "other.png"
    assert report["skin_removed"] == 1
    # 移除后回落到"暂无皮肤…"那句（语言文件里的原句，含换行）
    assert report["skin_remove_text"].startswith("暂无皮肤")


def test_checkin_streak_and_achievement_summary_are_displayed() -> None:
    """F-09 的"连续天数提示"这次真的看得见（旧实现是死代码，见服务层说明）。"""
    report = probe()
    assert report["checkinStreak"] == "已连续签到 3 天"
    assert report["achievementLabel"] == "12/40 已解锁"
    assert abs(float(report["achievementValue"]) - 0.3) < 1e-6


def test_running_state_updates_buttons_and_status_bar() -> None:
    report = probe()
    assert report["status_running"] == "游戏已就绪"
    assert report["status_level"] == "success"
    assert report["gameStateText_running"] == "游戏运行中"
    assert report["killEnabled_running"] is True
    # 旧界面在启动完成后就把启动按钮恢复可点（`ui/app_handlers.py:1160`），本轮保持
    assert report["launchEnabled_running"] is True
    assert report["launchLoading_running"] is False


def test_starting_state_shows_a_busy_button() -> None:
    """进程已起、窗口未出现 → 按钮转圈且不可点（B-05 的"加载中"语义）。"""
    report = probe()
    assert report["launchLoading_waiting"] is True
    assert report["launchEnabled_waiting"] is False


def test_kill_button_calls_the_service() -> None:
    assert probe()["kill_calls"] == 1


def test_exit_and_crash_messages_carry_the_exit_code() -> None:
    report = probe()
    assert report["status_exited"] == "游戏已正常退出"
    assert report["status_crashed"] == "游戏异常退出 (退出码: 1)"


def test_notice_entry_follows_the_startup_chain() -> None:
    """A-22：没有公告时入口禁用；拉到公告后可点，且点的是"重看"而不是重新拉。"""
    report = probe()
    assert report["noticeEnabled_before"] is False
    assert report["noticeEnabled_after"] is True
    assert report["notice_replayed"] == 1


def test_texts_follow_the_language_hot_switch() -> None:
    """A-15：首页文案是 `Tr.map[…]` 绑定，切到 en_US 后当场变。"""
    report = probe()
    assert report["launchText"] == "启动游戏"
    assert report["launchText_en"] == "Launch game"
    assert report["checkinText_en"] == "3-day check-in streak"
    assert report["launchText_zh"] == "启动游戏", "切回中文要能回来"


def test_no_qml_errors_during_the_whole_probe() -> None:
    errors: List[str] = probe()["qml_errors"]
    assert errors == [], "首页在加载与交互过程中产生了 QML 报错：\n" + "\n".join(errors)


def test_home_bridge_is_registered() -> None:
    report = probe()
    assert "Home" in report["bridges_registered"]
    assert report["bridges_missing"] == []


@pytest.mark.parametrize("name", ["homeLaunchButton", "homeKillButton"])
def test_action_buttons_are_clickable_targets(name: str) -> None:
    """两个动作按钮必须是**真的命中区**（点击测试已经证明；这里钉住它们存在）。"""
    assert probe()["objectNames"][name] is True
