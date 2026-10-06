"""账号页 QML 验收测试（阶段 3 任务 3.5）。

跑法：本文件用**子进程**跑 `tests/qml_account_probe.py`（理由见那个文件的 docstring：
真引擎 + FluentUI 的 `FluWindow` 在同一个进程里跨测试文件不安全），再对探针输出的
`PROBE_JSON:` 报告做断言。

判据的三条纪律（与 3.2 / 3.3 / 3.4 的验收文件一致）：

1. **断言的是界面真的显示了什么**（读控件的 `text` / `visible` / `enabled`），
   不是"桥里那个属性等于什么"；
2. **点击走真实坐标投递**，所以"点了没反应"这类接线断掉一定会被抓到；
3. 探针里的 `Accounts` 是**真的** `AccountBridge`（只有核心层的账号系统是假的），
   所以列表刷新、进度卡、取消标志这些**走的都是生产代码那条路**。

## 关于"重跑"（2026-10-06）

初版这里有一段"只对某条竞态判据重跑一次"的兜底：探针里「进度卡与进度文案出现」曾经是
竞态（假账号系统批量刷每个账号只停 50ms，busy 窗口约 100ms，机器一忙就抓不到）。
**该竞态已在探针侧修掉**（停顿提到 200ms + 可见性改成轮询等待），所以兜底整段删除 ——
本文件现在跑几次就是几次，没有任何重试。

测试名的中文注释里带的编号是 `docs/refactor/04-parity-matrix.md` 的条目号
（M-26 ~ M-29 / A-25，以及闸门 R5）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE_SCRIPT = REPO_ROOT / "tests" / "qml_account_probe.py"
PROBE_MARKER = "PROBE_JSON:"
PROBE_TIMEOUT = 600
FLUENT_DIR = REPO_ROOT / "third_party" / "_install" / "qml" / "FluentUI"

#: 账号页用的三个页内对话框（R5 的结构守卫，见最后一节）
DIALOG_DIR = REPO_ROOT / "qml" / "components" / "dialogs"
ACCOUNT_DIALOGS = ("AddAccountDialog.qml", "PasswordDialog.qml", "ExportResultDialog.qml")
COMPONENTS_MANIFEST = REPO_ROOT / "qml" / "components" / "COMPONENTS.md"

#: 与探针同一份过滤表：`qml_messages` 里出现这些字样就是真的 QML 报错
QML_ERROR_MARKERS = ("is not defined", "TypeError", "Unable to assign", "Cannot assign", "ReferenceError")
BENIGN_MARKERS = ("This plugin does not support", "QFont::setPointSize", "propagateSizeHints",
                  "Failed to create", "QFontDatabase")

#: 探针里 UUID 行的形状：`UUID: ` + 前 20 字符 + `...`
UUID_PREFIX = "UUID: "
UUID_KEEP = 20

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None or not FLUENT_DIR.is_dir(),
    reason=f"PySide6 缺插件或 FluentUI QML 模块未构建（{FLUENT_DIR}）—— 先跑 scripts/build_fluentui.ps1",
)


_REPORT: Dict[str, Any] = {}


def _run_probe() -> Dict[str, Any]:
    """跑一次子进程探针，返回解析好的报告（**不在这里判成败**）。"""
    try:
        completed = subprocess.run(
            [sys.executable, "-X", "utf8", str(PROBE_SCRIPT)],
            capture_output=True,
            text=True,
            # 必须显式给 encoding：不带它时按本地码页（本机 gbk）解码子进程输出，
            # 遇到 UTF-8 字节会在 reader 线程抛 UnicodeDecodeError（阶段 1 修过同族缺陷）
            encoding="utf-8",
            errors="replace",
            cwd=str(REPO_ROOT),
            env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
            timeout=PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        pytest.fail(f"探针 {PROBE_TIMEOUT}s 没跑完：stderr={str(exc.stderr)[-2000:]!r}")
    payload = None
    for line in completed.stdout.splitlines():
        if PROBE_MARKER in line:
            payload = json.loads(line[line.index(PROBE_MARKER) + len(PROBE_MARKER):])
    assert payload is not None, (
        f"探针没有输出结果（exit={completed.returncode}）：\n"
        f"stdout={completed.stdout[-2000:]!r}\nstderr={completed.stderr[-2000:]!r}"
    )
    payload["_returncode"] = completed.returncode
    payload["_stderr_tail"] = completed.stderr[-2000:]
    return payload


def probe() -> Dict[str, Any]:
    """跑一次子进程探针并缓存（一个会话只跑一次，约 20 秒：九段链路各走一遍）。

    **没有重试**：探针里那条竞态判据已在探针侧修掉（见文件头）。
    """
    if _REPORT:
        return _REPORT
    payload = _run_probe()
    payload["_attempts"] = 1
    _REPORT.update(payload)
    return _REPORT


def failures() -> List[str]:
    return [str(item) for item in probe().get("failures") or []]


def checks() -> Dict[str, Dict[str, Any]]:
    """探针的判据表（`name -> {ok, detail}`）——所有行为断言都从这里取，不重新推导。"""
    return {str(row["name"]): row for row in probe()["checks"]}


def require_ok(*names: str) -> None:
    """逐个断言探针判据为真；判据被改名/删掉也要红（说明探针结构变了）。"""
    rows = checks()
    for name in names:
        assert name in rows, f"探针里没有这条判据：{name}"
        row = rows[name]
        assert row["ok"] is True, f"{name}: {row['detail']}"


# ─── 探针本身 ──────────────────────────────────────────────────


def test_probe_exits_clean() -> None:
    """探针自己的判据一条不红、退出码 0、通过数等于总数。"""
    report = probe()
    assert report["_returncode"] == 0, f"探针自己的判据有失败项：{failures()}\nstderr={report['_stderr_tail']}"
    assert failures() == []
    assert report["passed"] == report["total"], f"{report['passed']} / {report['total']}"


def test_probe_reports_no_qml_errors() -> None:
    """整场跑完没有 `TypeError` / 未定义引用 —— 尤其是分区销毁、语言与主题热切换之后：
    账号分区的绑定与信号处理都必须扛得住"桥还活着、页面已经没了"。"""
    messages = [str(item) for item in probe()["qml_messages"]]
    errors = [item for item in messages
              if any(marker in item for marker in QML_ERROR_MARKERS)
              and not any(benign in item for benign in BENIGN_MARKERS)]
    assert errors == [], f"QML 报了错：{errors[:5]}"
    require_ok("QML 无 TypeError / 未定义引用")


def test_every_bridge_is_registered_including_accounts() -> None:
    """一个桥都不许缺席；账号分区依赖的四个桥逐个点名（`Accounts` 是本页的主角）。"""
    report = probe()
    assert report["bridges_missing"] == [], f"缺席的桥：{report['bridges_missing']}"
    assert {"Accounts", "Home", "Shell", "Nav"} <= set(report["bridges_registered"])
    require_ok("桥已注册（Accounts 在 context 属性里）")


def test_every_check_name_is_unique() -> None:
    """判据名不许重复 —— 重名会让一条被判红的检查被同名的绿灯**静默顶掉**。"""
    rows = probe()["checks"]
    names = [str(row["name"]) for row in rows]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    assert duplicates == [], f"这些判据名重复了：{duplicates}"
    assert probe()["total"] == len(names)


# ─── 入口与列表（M-27） ────────────────────────────────────────


def test_entry_anchors_and_three_rows_are_rendered() -> None:
    """深链进分区、14 个锚点控件都在树里、三个种下的账号按 id 各一行。"""
    report = probe()
    missing = [name for name, found in report["objectNames"].items() if not found]
    assert missing == [], f"账号分区缺少这些控件：{missing}"
    require_ok("设置页默认在启动器分区", "账户分区此刻没有渲染", "深链进入账号分区",
               "账号页主要控件都在树里", "三个账号各一行（按 id 取）", "列表里三行都在（按 accountRow 计）")
    assert report["row_names"] == ["\u2605 MainAccount", "OfflineGuy", "YggUser"]


def test_row_rendering_marks_star_tags_and_uuid() -> None:
    """行渲染：★ 只在当前账号、三种类型徽标互不相同、UUID 截断到 20 字符 + `...`。"""
    report = probe()
    require_ok("当前账号带 ★ 前缀（旧窗口 `account_manager.py:372` 同款）", "非当前账号没有 ★",
               "类型徽标按 i18n 显示且三种互不相同", "UUID 行是 `UUID: xxx...` 且截断到 20 字符")
    assert report["row_names"][0].startswith("\u2605 ")
    assert not any(name.startswith("\u2605") for name in report["row_names"][1:])
    assert len(set(report["type_tags"])) == 3 and all(report["type_tags"])
    uuids = list(report["uuids"])
    assert all(text.startswith(UUID_PREFIX) and text.endswith("...") for text in uuids)
    assert len(uuids[0]) == len(UUID_PREFIX) + UUID_KEEP + 3


def test_row_actions_follow_the_account_type() -> None:
    """行操作按账号类型给：「刷新Token」只有微软那行有，「设为当前」只有非当前行有，
    当前标记（标签 + 强调色指示条）全场各只出现一次。"""
    require_ok("「当前」标签只有一个（在 MainAccount 那行）", "当前账号的强调色指示条只出现一次",
               "只有微软账号那行有「刷新Token」按钮", "当前账号那行没有「设为当前」按钮",
               "非当前账号那行有「设为当前」按钮")


# ─── 切换当前账号（M-28 + M-13 的首页同步） ────────────────────


def test_switching_the_current_account_updates_the_list_and_home() -> None:
    """点「设为当前」→ 服务被调 → 列表当场刷新（★ 跟着挪）→ **首页卡片同步**。"""
    report = probe()
    require_ok("首页账号卡片有名字", "服务收到了切换请求", "当前账号换成 OfflineGuy（列表当场刷新）",
               "首页卡片同步（M-13 的『侧边栏同步』在 QML 下的落点）",
               "切换后「★」跟着挪（MainAccount 没了、OfflineGuy 有了）")


# ─── 删除（M-28） ──────────────────────────────────────────────


def test_delete_flow_confirms_by_name_cancels_and_removes() -> None:
    """确认框文案要带账号名；「取消」一行都不动；「确定」才真删 + 状态栏反馈。"""
    report = probe()
    assert "YggUser" in str(report["delete_message"]), f"确认框文案没带账号名：{report['delete_message']}"
    require_ok("点删除弹出确认框", "确认框文案带账号名（旧 `account_delete_confirm` 的 {name}）",
               "点「取消」不删账号", "点「取消」后确认框收回", "行数没变",
               "点「确定」真的删了", "删除后 YggUser 那一行真的没了", "删除后状态栏有反馈")


# ─── 刷新 Token / 全部刷新（M-28 / A-25） ──────────────────────


def test_refresh_token_confirms_then_calls_the_service() -> None:
    """「刷新Token」不是点了就刷：先确认框，确认后才调服务，成功走旧文案。"""
    require_ok("点「刷新Token」弹确认框", "确认后真的刷了", "刷新成功走旧文案 `account_refresh_token_success`")


def test_refresh_all_shows_a_progress_card_and_finishes() -> None:
    """「全部刷新」：进度卡真的显示出来、进度文案有内容、收尾时 busy 归位并给状态栏文案。"""
    report = probe()
    require_ok("全部刷新启动了", "进度卡与进度文案出现", "批量刷新收尾（busy 归位 + 状态栏文案）")
    debug = report["progress_debug"]
    assert debug["busySeen"] is True, f"bridge 的 busy 没起来：{debug}"
    assert debug["cardVisible"] is True, f"进度卡没显示：{debug}"
    assert str(report["refresh_all_progress"]) != ""


# ─── 登录（M-26） ──────────────────────────────────────────────


def test_offline_and_yggdrasil_forms_validate_then_create() -> None:
    """两张表单的就地校验：空名字 / 缺字段都拦下来（且不建账号）；填了名字就真建号。"""
    require_ok("离线表单出现", "空名字就地报错（旧 `account_name_required`）", "空名字没有建账号",
               "填了名字就建号", "新账号出现在列表里", "新账号自动成为当前账号（核心层行为）",
               "表单提交后收回", "外置表单出现", "外置缺字段就地报错（旧 `account_ygg_fields_required`）",
               "取消后表单收回")


def test_microsoft_login_progress_cancel_and_success() -> None:
    """微软登录全流程：进度卡出现并禁用添加按钮 → 「取消」不建账号且提示已取消 →
    再登录成功后新账号进列表、进度卡收回。"""
    require_ok("微软表单出现（说明 + 打开浏览器登录）", "登录期间进度卡出现并禁用添加按钮",
               "取消按钮可用（本轮新增能力）", "取消后 busy 归位", "取消后没有建账号",
               "取消后状态栏提示已取消", "取消按钮消失",
               "微软登录成功后新账号进列表", "登录成功后进度卡收回")


# ─── 导入 / 导出（M-29） ───────────────────────────────────────


def test_export_writes_the_file_and_calls_the_folder_opener() -> None:
    """导出链（旧实现是**两次**密码问答）：设密码 → 再输一遍核对 → 选文件 → 真写盘；
    结果框带路径，「打开目录」走注入的 opener（不是真开窗口）。"""
    require_ok("导出第一步弹密码框", "密码框里的明文就是输入的内容（掩码只影响显示）",
               "导出第 2 步仍在同一个对话框里（提示语换成「请再次输入」）",
               "导出真的写了文件", "导出用了用户设的口令", "导出结果对话框出现",
               "结果对话框带文件路径", "「打开目录」调用了注入的 opener",
               "关掉结果框后对话框收回")


def test_export_password_mismatch_goes_back_to_the_first_step() -> None:
    """两次密码不一致：退回第 1 步、**显示旧文案** `account_export_password_mismatch`、
    且一个字节都没写出去（旧实现是"弹警告 + 整段放弃"，判定与文案相同）。"""
    report = probe()
    require_ok("两次不一致退回第 1 步并显示旧文案 `account_export_password_mismatch`",
               "两次不一致时**没有**写文件", "两次一致后进入选文件环节（对话框收回）")
    assert "不一致" in str(report["export_mismatch_error"]), report["export_mismatch_error"]


def test_import_adds_an_account_and_reports_a_notice() -> None:
    """导入：密码框 → 选文件 → 账号数 +1，并给一条 success 级 Toast（旧键 `account_import_success`）。"""
    report = probe()
    require_ok("导入第一步弹密码框", "导入后账号数增加",
               "导入成功给了提示（Toast 走旧键 `account_import_success`）")
    notice = report["import_notice"]
    assert notice.get("level") == "success", f"提示档位不对：{notice}"
    assert str(notice.get("message")) not in ("", "None"), f"提示没有文案：{notice}"


def test_import_with_a_wrong_password_keeps_the_list() -> None:
    """错密码：一个账号都不建（旧文案「密码错误或文件已损坏」由服务层给），
    探针顺手记下当时的状态栏文案供排查。"""
    report = probe()
    require_ok("错密码不建账号")
    assert isinstance(report["import_error_status"], str)


# ─── 三态 / 主题 / 语言 ────────────────────────────────────────


def test_empty_list_shows_the_empty_state() -> None:
    """清空账号后列表走 `FmEmptyState`（旧键 `account_no_accounts`），不是一片空白。"""
    report = probe()
    assert report["objectNames"]["accountEmptyState"] is True
    require_ok("空列表显示空态", "空态文案是旧键 `account_no_accounts`")


def test_five_themes_keep_the_section_rendered() -> None:
    """五个预设主题逐个套上去，账号分区都还在，且强调色确实来自 `Theme`。"""
    report = probe()
    themes = report["themes"]
    assert set(themes) == {"default", "ocean", "forest", "lavender", "sunset"}, f"预设主题变了：{sorted(themes)}"
    assert all(themes.values()), f"这些主题下页面没了：{[k for k, v in themes.items() if not v]}"
    require_ok("5 个预设主题下账号页都在", "主题色来自 Theme（强调色存在）")


def test_four_languages_translate_the_title() -> None:
    """四语言各切一遍：标题都翻出来了（不是裸键名），且四种语言的文案互不相同。"""
    report = probe()
    languages = report["languages"]
    assert set(languages) == {"en_US", "zh_TW", "ja_JP", "zh_CN"}, f"语言集合变了：{sorted(languages)}"
    assert len(set(languages.values())) == 4, f"有语言没换文案：{languages}"
    assert all(value not in ("", "account_manager_title", "<missing>") for value in languages.values())
    require_ok("四语言下标题都翻出来了（不是裸键名）", "四种语言下文案互不相同（真的换了）")


# ─── 可复用对话框的结构守卫（闸门 R5） ─────────────────────────


def test_three_reusable_dialogs_are_kept_and_registered() -> None:
    """账号分区的三个页内对话框必须在 `qml/components/dialogs/` 里、
    并且在 `COMPONENTS.md` 的白名单里登记过（R5 靠这份清单判"自研件"）。"""
    manifest = COMPONENTS_MANIFEST.read_text(encoding="utf-8")
    for filename in ACCOUNT_DIALOGS:
        assert (DIALOG_DIR / filename).is_file(), f"账号页用的可复用对话框不见了：{filename}"
        assert filename[: -len(".qml")] in manifest, f"{filename} 没有登记进 COMPONENTS.md"
