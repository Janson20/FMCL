"""设置页 QML 验收测试（阶段 3 任务 3.4）。

跑法：本文件用**子进程**跑 `tests/qml_settings_probe.py`（理由见那个文件的 docstring：
真引擎 + FluWindow 在同一个进程里跨测试文件不安全），再对探针输出做断言。

判据的三条纪律（与 `tests/test_versions_page_qml.py` / `test_install_page_qml.py` 一致）：

1. **断言的是界面真的显示了什么**（读控件的 `text` / `visible` / `enabled`），
   不是"桥里那个属性等于什么"；
2. **点击走真实坐标投递**（滚动区外的那种改用信号，理由写在探针里），
   所以"点了没反应"这类接线断掉一定会被抓到；
3. 探针里的 `Settings` / `Logs` / `About` 三个服务是**真的**服务
   （只有核心层 `MinecraftLauncher` 与配置对象是假的），所以草稿语义、
   写盘顺序、成就触发、导出与协议读取这些**走的都是生产代码那条路**。

测试名的中文注释里带的编号是 `docs/refactor/04-parity-matrix.md` 的条目号。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_SCRIPT = REPO_ROOT / "tests" / "qml_settings_probe.py"
PROBE_MARKER = "PROBE_JSON:"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


_REPORT: Dict[str, Any] = {}


def probe() -> Dict[str, Any]:
    """跑一次子进程探针并缓存（一个会话只跑一次，约 30 秒：8 个分区各走一遍）。"""
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


def failures() -> List[str]:
    return list(probe().get("failures", []))


# ─── 探针本身 ──────────────────────────────────────────────────


def test_probe_exits_clean() -> None:
    assert probe()["_returncode"] == 0, f"探针自己的判据有失败项：{failures()}"


def test_probe_reports_no_qml_errors() -> None:
    """整场跑完 0 条 QML 报错 —— 尤其是页面被弹出（销毁）之后那条
    `Cannot read property '…' of null`：设置页是最典型的"可弹出 + 多分区"页面，
    绑定与信号处理都必须扛得住"桥还活着、页面已经没了"。"""
    assert probe()["qml_errors"] == []


def test_every_recorded_item_exists() -> None:
    """登记过的控件都要真的在树里 —— 少一个就说明页面结构被改掉了。"""
    missing = [name for name, found in probe()["objectNames"].items() if not found]
    assert missing == [], f"设置页缺少这些控件：{missing}"


def test_three_bridges_are_registered() -> None:
    report = probe()
    registered = report.get("bridges_registered") or []
    assert "Settings" in registered
    assert "Logs" in registered
    assert "About" in registered


# ─── 入口与分区（A-07） ────────────────────────────────────────


def test_settings_route_opens_the_page() -> None:
    report = probe()
    assert report["route_before"] != "settings"
    assert report["route_after"] == "settings"


def test_section_nav_lists_eight_sections() -> None:
    """分区清单从路由表现取（`parent === "settings"`）：8 条，且**不含** `dev/gallery`。"""
    assert probe()["section_count"] == 8, "少了或多了一个分区"


def test_default_section_is_launcher() -> None:
    report = probe()
    assert report["objectNames"]["settingsLauncherSection"] is True


# ─── 草稿范式（M-Q1 的 B1/B2） ─────────────────────────────────


def test_toggling_a_switch_only_touches_the_draft() -> None:
    """**本页最核心的判据**：改控件不写盘 —— 核心层的 setter 一次都没被调、配置没保存。"""
    report = probe()
    checks = {row["name"]: row for row in report["checks"]}
    assert checks["点开关在草稿里生效"]["ok"] is True
    assert checks["**没有**写盘（核心层 setter 一次没调）"]["ok"] is True
    assert checks["**没有**存配置"]["ok"] is True
    assert checks["草稿变脏"]["ok"] is True


def test_slider_changes_go_to_the_draft_too() -> None:
    report = probe()
    assert int(report["threads_draft"]) == 12


def test_switching_sections_keeps_the_draft() -> None:
    """B1 的整窗一份草稿：切到主题分区再回来，启动器那边的改动还在。"""
    checks = {row["name"]: row for row in probe()["checks"]}
    assert checks["切分区回来改动还在（B1 整窗一份草稿）"]["ok"] is True


def test_pending_flags_follow_the_draft() -> None:
    checks = {row["name"]: row for row in probe()["checks"]}
    assert checks["未保存标记出现"]["ok"] is True
    assert checks["「保存」变可用"]["ok"] is True
    assert checks["取消后未保存标记消失"]["ok"] is True


def test_draft_is_not_created_at_assembly() -> None:
    """草稿由**页面**建，不是装配期建（2026-10-06 验收反馈的修复）。

    装配发生在用户做任何设置之前，那时建的草稿会在用户于别处改设置
    （顶栏地球切语言）之后变成过期基线 —— 症状是"改个主题，语言自己变回英文"。
    钉子看桥的源码与页面：`bind()` 里不许有 `begin_draft()`，页面里必须有一处。
    """
    bridge = (REPO_ROOT / "app" / "bridges" / "settings_bridge.py").read_text(encoding="utf-8")
    page = (REPO_ROOT / "qml" / "pages" / "settings" / "SettingsPage.qml").read_text(encoding="utf-8")
    #: 只看**装配路径**（`bind()`）的代码：它不许建草稿。
    #: 桥上的 `beginDraft()` 槽是给页面调的，那个必须留着（页面进入时才建）。
    start = bridge.find("def bind(self")
    end = bridge.find("def use_engine", start)
    assert start > 0 and end > start, "找不到 bind()/use_engine()（桥的结构变了）"
    bind_body = "\n".join(
        line for line in bridge[start:end].split("\n") if not line.strip().startswith("#")
    )
    assert "begin_draft()" not in bind_body, "装配期又建草稿了（过期基线的根因）"
    assert "def beginDraft(" in bridge, "页面调用的那个槽被删了"
    assert "Settings.beginDraft()" in page, "页面进入时没有建草稿"
    # 行为判据（重定基线）在服务层：`test_settings_service.py` 的
    # `test_begin_draft_rebases_on_external_changes` 与
    # `test_preview_does_not_revive_a_stale_language`


# ─── 主题与强调色的预览（M-07 / M-09 / M-10 / B2） ─────────────


def test_theme_dropdown_has_five_presets() -> None:
    assert len(probe()["theme_labels"]) == 5


def test_theme_preview_changes_memory_but_not_the_config() -> None:
    """B2：改主题当场看得见（内存调色板变了），但配置里什么都没变。"""
    report = probe()
    assert report["accent_preview"] != report["accent_before"]
    checks = {row["name"]: row for row in report["checks"]}
    assert checks["预览**没有**写进配置"]["ok"] is True
    assert checks["预览也没调核心层"]["ok"] is True


def test_cancel_restores_the_preview() -> None:
    """「取消」= 丢弃草稿 + 还原预览：强调色退回已保存值，且整场没有写盘。"""
    report = probe()
    assert report["accent_after_cancel"] == report["accent_before"]
    checks = {row["name"]: row for row in report["checks"]}
    assert checks["取消后草稿清空"]["ok"] is True
    assert checks["取消后仍未写盘"]["ok"] is True


def test_manual_accent_is_validated() -> None:
    report = probe()
    assert str(report["accent_manual"]).lower() == "#123456", "合法值要当场预览"
    checks = {row["name"]: row for row in report["checks"]}
    assert checks["非法输入会被拦下（校验函数返回非空）"]["ok"] is True


# ─── 保存（M-01 / M-02 / M-12 / M-25 / B3） ────────────────────


def test_save_writes_only_the_changed_keys() -> None:
    report = probe()
    assert report["writes_after_save"] == [
        ["minimize_on_game_launch", True],
        ["theme_name", "forest"],
    ], "保存要按 DRAFT_KEYS 顺序只写改动过的键"


def test_save_fires_each_achievement_once_per_session() -> None:
    """B3：成就改到「确定」时才触发，同类一次会话只触发一次。"""
    report = probe()
    assert report["achievements"] == ["personalize_theme_master"]
    checks = {row["name"]: row for row in report["checks"]}
    assert checks["没改动时保存不会重复触发成就"]["ok"] is True


# ─── 语言（M-06 / M-Q2 的行为变更） ────────────────────────────


def test_language_switches_hot_without_saving() -> None:
    """M-Q2 已裁决"照实升级为热切换"：改语言当场生效、配置仍没变（预览）。"""
    report = probe()
    assert report["language_preview"] == "en_US"
    checks = {row["name"]: row for row in report["checks"]}
    assert checks["语言当场热切换（预览）"]["ok"] is True
    assert checks["预览时配置没变"]["ok"] is True


def test_language_cancel_restores_it() -> None:
    report = probe()
    assert report["language_after_cancel"] == "zh_CN"


def test_four_languages_are_offered() -> None:
    assert len(probe()["language_labels"]) == 4


# ─── 离开守卫（M-Q1 的 B6） ────────────────────────────────────


def test_leaving_with_unsaved_changes_asks_once() -> None:
    checks = {row["name"]: row for row in probe()["checks"]}
    assert checks["有改动时登记守卫"]["ok"] is True
    assert checks["跨域导航被挡下"]["ok"] is True
    assert checks["弹出了确认框"]["ok"] is True


def test_cancel_in_the_prompt_stays_on_the_page() -> None:
    checks = {row["name"]: row for row in probe()["checks"]}
    assert checks["选「取消」留在设置页"]["ok"] is True
    assert checks["草稿还在（没有丢弃）"]["ok"] is True


def test_confirm_leaves_and_discards() -> None:
    """「确定」= 丢弃草稿并离开：草稿清空、守卫撤销、**且没有写盘**。"""
    checks = {row["name"]: row for row in probe()["checks"]}
    assert checks["选「确定」真的离开"]["ok"] is True
    assert checks["离开时草稿被丢弃"]["ok"] is True
    assert checks["离开后守卫撤销"]["ok"] is True
    assert checks["丢弃没有写盘"]["ok"] is True


# ─── Java（M-03 / M-04 / M-05） ────────────────────────────────


def test_java_mode_dropdown_has_three_options() -> None:
    assert len(probe()["java_modes"]) == 3


def test_java_areas_follow_the_mode() -> None:
    checks = {row["name"]: row for row in probe()["checks"]}
    assert checks["默认不显示自定义路径区"]["ok"] is True
    assert checks["选「自定义路径」显示输入框"]["ok"] is True
    assert checks["选「从扫描列表中选择」显示扫描区"]["ok"] is True


def test_java_scan_runs_through_the_task_bridge_and_fills_the_list() -> None:
    report = probe()
    assert report["java_rows"] == 2, "两行扫描结果要真的画出来"
    checks = {row["name"]: row for row in report["checks"]}
    assert checks["核心层的 scan_system_java 被调用了"]["ok"] is True
    assert str(report["java_path"]).endswith("java.exe"), "选中一行要写进草稿"


# ─── 日志（A-13 / A-18） ───────────────────────────────────────


def test_log_lines_reach_the_view() -> None:
    before, after = probe()["log_lines"]
    assert after > before


def test_log_capacity_matches_the_legacy_limit() -> None:
    checks = {row["name"]: row for row in probe()["checks"]}
    assert checks["日志行数有上限（5000）"]["ok"] is True


def test_log_export_writes_a_file() -> None:
    report = probe()
    assert report["export_exists"] is True
    assert report["export_bytes"] > 0


def test_log_clear_and_open_dir() -> None:
    report = probe()
    assert report["log_lines_after_clear"] <= 1
    checks = {row["name"]: row for row in report["checks"]}
    assert checks["打开日志目录调到了平台实现"]["ok"] is True


# ─── 关于（A-08 / J-01 ~ J-03） ────────────────────────────────


def test_about_renders_info_acknowledgments_and_terms() -> None:
    report = probe()
    assert report["about_info_rows"] == 4, "版本 / Python / 系统 / 架构 四行"
    assert report["about_acks"] == 9, "9 个鸣谢项目"
    assert report["terms_length"] > 500, "协议正文要真的渲染出来（不是摘要）"


def test_acknowledgment_links_open_the_right_url() -> None:
    assert probe()["opened_urls"][:1] == ["https://github.com/PCL-community/PCL-CE"]


# ─── 占位分区（3.4 的范围裁决） ────────────────────────────────


def test_placeholder_sections_are_not_blank() -> None:
    report = probe()
    titles = report["placeholder_titles"]
    assert all(titles), f"账户 / AI / 插件三个分区都要有占位内容：{titles}"
    assert len(set(titles)) == 3, "三个占位分区标题不能一样"


# ─── 保存并重启（M-24 / B5） ───────────────────────────────────


def test_save_and_restart_saves_first_then_spawns() -> None:
    report = probe()
    checks = {row["name"]: row for row in report["checks"]}
    assert checks["保存并重启：先落盘（整窗草稿一次提交）"]["ok"] is True
    assert checks["保存并重启：拉起了新进程"]["ok"] is True
    assert len(report["spawned"]) == 1
