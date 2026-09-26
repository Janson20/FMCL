"""成就触发点的**接线**守卫（D-109 / D-110 这一类缺陷的通解）。

这两个缺陷的共同形状：
    成就定义齐全、文案齐全、界面正常，**但触发点不存在或必然抛异常**。
这类问题没有任何运行期报错能提示你（异常被 `except: pass` 吞掉，
或者调用点干脆被上一次重构删掉了），只表现为"这项成就永远解锁不了"。

所以这里用静态检查把"每个成就都至少有一条能走到的触发路径"钉住：
1. 逐个成就 id，在**非定义、非测试**的代码里找它作为字符串字面量出现的位置；
2. 允许一个白名单（确实由动态 id 触发、或引擎内部按名字更新）；
3. 每个白名单条目都要写明**为什么**它不需要字面量调用点 —— 白名单不是垃圾场。

同时钉住 D-109 与 D-110 的具体修复点，防止回退。
"""

from __future__ import annotations

import ast
import io
import re
from functools import lru_cache
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFS_FILE = REPO_ROOT / "services" / "achievement_defs.py"
SKIP_DIRS = {".venv", ".git", "poc", "third_party", "build", "tmp", "node_modules", "__pycache__", "tests"}

#: 不需要"字面量触发点"的成就，以及各自的理由（每一条都必须能自圆其说）
DYNAMIC_ONLY: dict[str, str] = {
    "advanced_checkin": (
        "由 services/achievement_engine.py::AchievementEngine.checkin() 内部按名字 "
        "update_progress('advanced_checkin', ...) 更新，不在界面侧触发"
    ),
    "personalize_version_theme": (
        "由 launcher/core.py:2716 在切换版本动态调色时 update_progress 触发（CLI 与界面共用）"
    ),
}

#: 允许作为"触发点"的调用名（与 poc/_audit_achievement_triggers.py 保持一致）
TRIGGER_CALLS = {
    "_trigger_ach",
    "_check_ach",
    "trigger_ach",
    "_trigger_agent_ach",
    "update_progress",
    "check_and_unlock",
    "trigger_achievement",
}


@lru_cache(maxsize=1)
def _iter_source_files() -> tuple[Path, ...]:
    """仓库里需要扫描的 .py（排除 .venv / tests / poc / 构建产物）。

    带缓存：本文件要多次遍历全仓，重复 rglob 会让整个测试文件慢一倍以上。
    """
    out: list[Path] = []
    for p in REPO_ROOT.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.relative_to(REPO_ROOT).parts):
            continue
        out.append(p)
    return tuple(out)


def _defined_achievement_ids() -> dict[str, int]:
    tree = ast.parse(io.open(DEFS_FILE, encoding="utf-8").read())
    out: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "AchievementDef":
            for kw in node.keywords:
                if kw.arg == "achievement_id" and isinstance(kw.value, ast.Constant):
                    out[str(kw.value.value)] = node.lineno
    return out


@lru_cache(maxsize=1)
def _literal_trigger_sites() -> frozenset[str]:
    """代码里出现过的**全部**字符串字面量（不含成就定义文件、tests、poc）。

    用它回答一个很弱但极关键的问题：「这个成就 id 在代码里到底有没有被提到过」。
    弱一点的检查是有意的 —— 强弱不重要，重要的是它**恰好**能抓住
    "id 只存在于定义里、任何代码路径都不引用"这种永不触发的成就（D-110）。
    """
    found: set[str] = set()
    for path in _iter_source_files():
        if path == DEFS_FILE:
            continue
        try:
            tree = ast.parse(io.open(path, encoding="utf-8").read())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                found.add(node.value)
    return frozenset(found)


@lru_cache(maxsize=1)
def _literal_ids_passed_to_triggers() -> dict[str, tuple[str, ...]]:
    """触发了哪个 id（只看**直接**传字面量的调用点），用于反向拼写检查。"""
    sites: dict[str, list[str]] = {}
    for path in _iter_source_files():
        if path == DEFS_FILE:
            continue
        try:
            tree = ast.parse(io.open(path, encoding="utf-8").read())
        except (OSError, SyntaxError):
            continue
        rel = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name not in TRIGGER_CALLS:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                sites.setdefault(str(first.value), []).append(f"{rel}:{node.lineno}")
    return sites


@pytest.fixture(scope="module")
def achievement_audit() -> tuple[dict[str, int], set[str]]:
    return _defined_achievement_ids(), _literal_trigger_sites()


def test_every_achievement_id_is_referenced_by_code(achievement_audit) -> None:
    """每个成就的 id 都必须在代码里出现过（否则它永远不可能被触发）。

    ``personalize_rename``（D-110）就是这种：id 只在定义里出现，
    所有代码路径都不引用它 —— 界面正常、文案齐全、就是永远解锁不了。
    """
    defined, mentioned = achievement_audit
    assert len(defined) >= 40, f"成就定义数量异常：{len(defined)}"
    orphan = sorted(aid for aid in defined if aid not in mentioned and aid not in DYNAMIC_ONLY)
    assert not orphan, (
        "以下成就的 id 在代码里一次都没出现，也就**永远无法解锁**：\n"
        + "\n".join(f"  {aid}  (定义于 achievement_defs.py:{defined[aid]})" for aid in orphan)
        + "\n\n如果是被动态拼接的 id 触发的，请把理由加进本文件顶部的 DYNAMIC_ONLY；"
        "否则请补触发点 —— 这类缺陷不会有任何运行期报错。"
    )


def test_literal_trigger_call_sites_exist_for_the_bulk(achievement_audit) -> None:
    """次要信号：大多数成就应当是**直接**在触发调用里传字面量 id 的。

    这条不追求 100%（允许常量间接引用），只用来发现"整类触发点消失"的退化 ——
    阈值按实测值留出余量：当前 47 个成就里绝大多数是直接字面量。
    """
    defined, _sites = achievement_audit
    literal_call_ids: set[str] = set()
    for path in _iter_source_files():
        if path == DEFS_FILE:
            continue
        try:
            tree = ast.parse(io.open(path, encoding="utf-8").read())
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name not in TRIGGER_CALLS:
                continue
            first = node.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                literal_call_ids.add(str(first.value))
    assert len(literal_call_ids) >= 30, (
        f"直接传字面量 id 的触发点只剩 {len(literal_call_ids)} 个（期望 ≥30）；"
        "触发点可能被成批删掉了"
    )
    assert literal_call_ids <= set(defined), "有触发点用了未定义的 id（拼写错误）"


def test_dynamic_only_whitelist_entries_still_exist(achievement_audit) -> None:
    """白名单不能留死条目：被白名单豁免的成就本身必须还在，理由里的文件也要还在。"""
    defined, _sites = achievement_audit
    for aid in DYNAMIC_ONLY:
        assert aid in defined, f"白名单里的 {aid} 已不在成就定义中，请从 DYNAMIC_ONLY 移除"

    engine_src = io.open(REPO_ROOT / "services" / "achievement_engine.py", encoding="utf-8").read()
    assert "advanced_checkin" in engine_src
    core_src = io.open(REPO_ROOT / "launcher" / "core.py", encoding="utf-8").read()
    assert "personalize_version_theme" in core_src


def test_bulk_trigger_sites_pass_defined_ids(achievement_audit) -> None:
    """次要信号（一）：反向拼写检查 + "触发点被成批删掉"的退化检测。"""
    defined, _mentioned = achievement_audit
    literal_calls = _literal_ids_passed_to_triggers()
    unknown = sorted(set(literal_calls) - set(defined))
    assert not unknown, "以下 id 被触发但未定义（拼写错误，那次触发是空操作）：\n" + "\n".join(
        f"  {aid}  <- {', '.join(literal_calls[aid][:4])}" for aid in unknown
    )
    assert len(literal_calls) >= 30, (
        f"直接传字面量 id 的触发点只剩 {len(literal_calls)} 个（期望 ≥30）；触发点可能被成批删掉了"
    )


# ── D-109 / D-110 的定点回归 ────────────────────────────────────────────


def test_d109_full_house_does_not_query_a_nonexistent_resource_folder() -> None:
    """D-109：全家福成就的条件不许再去问一个不存在的资源目录。

    原实现 ``self._get_resource_dir("datapacks")`` 必然 ``KeyError``（``RESOURCE_TYPES``
    只有 4 个键），又被 ``except Exception: pass`` 吞掉 → 成就从引入那次提交起就没触发过。

    这里用 AST 判定（不看注释/docstring —— 修复说明里当然会出现 "datapacks" 这个词）：
    只要不再有 ``*_get_resource_dir("datapacks")`` / ``*["datapacks"]`` 这种**取值**，
    也没有把 datapacks 混进资源目录判断里，就算通过。
    """
    src = io.open(REPO_ROOT / "ui" / "windows" / "resource_manager.py", encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # 形状一：self._get_resource_dir("datapacks")
        if isinstance(node, ast.Call):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and arg.value == "datapacks":
                    pytest.fail(
                        f"ui/windows/resource_manager.py:{node.lineno} 又拿 datapacks 去查资源目录了（D-109 回退）"
                    )
        # 形状二：RESOURCE_TYPES["datapacks"] / any_dict["datapacks"]
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if node.slice.value == "datapacks":
                pytest.fail(
                    f"ui/windows/resource_manager.py:{node.lineno} 又用 datapacks 做键取值了（D-109 回退）"
                )

    # 服务层：check_full_house 的取值也要干净
    svc = REPO_ROOT / "services" / "resource_service.py"
    if svc.exists():
        svc_src = io.open(svc, encoding="utf-8").read()
        for node in ast.walk(ast.parse(svc_src)):
            if isinstance(node, ast.Call):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == "datapacks":
                        pytest.fail(
                            f"services/resource_service.py:{node.lineno} 又在查 datapacks 目录（D-109 回退）"
                        )

    # 条件必须来自加载器扫描（或已被显式裁决为"资源类型"读法并同步文案）
    combined = src + (io.open(svc, encoding="utf-8").read() if svc.exists() else "")
    assert re.search(r"has_all_mod_loaders|scan_installed_loaders", combined), (
        "全家福成就的条件既不是加载器扫描、也没查 datapacks —— "
        "若是改成「四个真实资源类型都非空」的读法，请同步改 4 份 ach_modder_full_house_desc 文案"
        "并更新 07-known-defects.md 的 D-109 裁决"
    )


def test_d110_offline_account_creation_triggers_personalize_rename() -> None:
    """D-110：手动角色名输入被移除后，personalize_rename 必须由账号系统触发。"""
    win = io.open(REPO_ROOT / "ui" / "windows" / "account_manager.py", encoding="utf-8").read()
    assert "on_offline_account_added" in win, "账号管理窗口丢失了离线账号创建的成就钩子"
    assert 'account_type == "offline"' in win, "钩子必须只在离线账号（=给角色起名）时触发"

    app_base = io.open(REPO_ROOT / "ui" / "app_base.py", encoding="utf-8").read()
    assert 'self._trigger_ach("personalize_rename")' in app_base, (
        "app_base 没有把 on_offline_account_added 接到 personalize_rename 成就上"
    )


def test_player_name_i18n_keys_are_still_orphaned_or_used() -> None:
    """记录现状：`player_name` / `player_name_placeholder` 在 f158841 后已无引用。

    这条不是"必须成立的需求"，而是**现状的记录 + 提醒**：如果哪天有人把手动角色名
    输入框加回来，请把 D-110 的触发点改成"两条路径都算"，并更新本测试。
    """
    used = False
    pattern = re.compile(r'["\']player_name(_placeholder)?["\']')
    for path in _iter_source_files():
        if path.name == "config.py":
            continue  # config 用的是 dict 键，不是 i18n
        try:
            src = io.open(path, encoding="utf-8").read()
        except OSError:
            continue
        if pattern.search(src):
            used = True
            break
    assert used is False, (
        "有人又开始引用 player_name i18n 键了 —— 请核对 D-110 的触发路径是否仍然正确"
    )
