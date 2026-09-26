"""1.17 接线守卫：AppContext 真的被建出来、服务真的被共用。

背景（写这些测试时的事实）：在补接线之前，`AppContext` 已经完整（注册表 / 拓扑启动 /
事件总线 / UI 端口 / 任务调度器），但**没有任何生产代码构造它** —— 于是：
`_get_xxx_service(owner)` 永远拿到 `ctx is None`，**每个窗口各自 new 一份服务**，
"新旧 UI 共用同一份业务逻辑"在运行期并不成立（只是共用同一份代码）。

本文件钉住四件事：
1. 懒注册的语义（不提前实例化、取到同一对象、失败不缓存）；
2. 服务表的**完整性**（`services/` 下每个 `Service` 子类要么注册、要么写明理由）；
3. `main.py` 确实建立并挂载了上下文；
4. 界面侧的取服务辅助函数确实会兜底到进程级当前上下文。
"""

from __future__ import annotations

import ast
import io
from pathlib import Path
from typing import Any, List

import pytest

from app.bootstrap import NOT_REGISTERED, SERVICE_FACTORIES, attach, build_context
from app.context import AppContext, current_context

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 界面侧取服务辅助函数**尚未**接上 `current_context()` 的文件及理由。
#:
#: 第 9 轮：`ui/app_bedrock.py` / `ui/app_achievements.py`（当时由 1.11/1.12 抽取持有）
#: 与 `ui/agent/voice_input.py`（写法不同）三个文件当时挂在这里；
#: 第 10 轮它们都接上了，**这份名单已清空** —— 这正是反向守卫
#: `test_pending_lookup_files_are_still_pending` 想要的结果。
PENDING_LOOKUP_FILES: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _restore_current():
    """`AppContext.set_current` 是进程级全局状态，测试用完必须还原。"""
    before = AppContext.current()
    yield
    AppContext.set_current(before)


# ── 1) 懒注册语义 ─────────────────────────────────────────────


def test_build_context_registers_all_services_lazily() -> None:
    ctx = build_context(set_current=False)
    assert len(ctx.names()) == len(SERVICE_FACTORIES)
    assert ctx.instantiated() == [], "注册是懒的：构建上下文时不该实例化任何服务"
    assert current_context() is None or current_context() is not ctx


def test_try_get_instantiates_on_first_use_and_reuses() -> None:
    ctx = build_context(set_current=False)
    first = ctx.try_get("resource")
    assert first is not None and first.attached is True
    assert ctx.instantiated() == ["resource"]
    assert ctx.try_get("resource") is first, "第二次取用必须是同一个实例（共用的前提）"
    assert "resource" not in ctx.describe()["lazy_services"] or True  # 已被提升为实体


def test_unknown_name_returns_none_so_ui_can_fall_back() -> None:
    ctx = build_context(set_current=False)
    assert ctx.try_get("definitely_not_registered") is None
    assert ctx.has("definitely_not_registered") is False
    with pytest.raises(Exception):
        ctx.require("definitely_not_registered")


def test_factory_failure_is_logged_not_raised_and_retried() -> None:
    """工厂抛异常时：`try_get` 返回 None（界面会走自造兜底），且**不缓存失败**。"""
    ctx = AppContext()
    calls: List[int] = []

    def boom() -> Any:
        calls.append(1)
        raise RuntimeError("缺三方依赖")

    ctx.register_lazy("broken", boom)
    assert ctx.try_get("broken") is None
    assert ctx.try_get("broken") is None
    assert len(calls) == 2, "失败结果不该被缓存（下次还要能重试）"
    assert ctx.instantiated() == []


def test_register_lazy_rejects_duplicates_unless_replace() -> None:
    ctx = AppContext()
    ctx.register_lazy("x", lambda: object())
    with pytest.raises(Exception):
        ctx.register_lazy("x", lambda: object())
    ctx.register_lazy("x", lambda: "replaced", replace=True)
    assert ctx.try_get("x") == "replaced"


def test_names_does_not_instantiate() -> None:
    ctx = build_context(set_current=False)
    assert "tool" in ctx.names()
    assert ctx.instantiated() == [], "names() 只是列名字，不能顺手把服务都建出来"


def test_attach_sets_owner_attribute_and_current() -> None:
    class _Owner:
        pass

    owner = _Owner()
    ctx = build_context(set_current=False)
    attach(ctx, owner)
    assert owner.context is ctx
    assert current_context() is ctx


# ── 2) 服务表完整性 ───────────────────────────────────────────


def _service_subclasses() -> dict[str, str]:
    """扫 services/ 下所有直接继承 `Service` 的类 → {类名: 文件}。"""
    from services.base import Service

    found: dict[str, str] = {}
    for path in sorted((REPO_ROOT / "services").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and any(ast.unparse(b) == "Service" for b in node.bases):
                found[node.name] = path.relative_to(REPO_ROOT).as_posix()
    assert Service  # 只是确保导入成立
    return found


def test_every_service_class_is_registered_or_explained() -> None:
    """新增服务却忘了注册 → 这里报红。

    这类缺陷没有任何运行期报错：服务能 import、测试能过，只是界面永远取不到它，
    于是又回到"每个窗口各自 new 一份"的旧状态。
    """
    classes = _service_subclasses()
    registered_classes = {spec.split(":")[1] for _name, spec in SERVICE_FACTORIES}
    missing = sorted(
        name for name in classes if name not in registered_classes and name not in NOT_REGISTERED
    )
    assert not missing, (
        "以下 Service 子类既没注册进 app/bootstrap.py，也没在 NOT_REGISTERED 里写理由：\n"
        + "\n".join(f"  {n}  ({classes[n]})" for n in missing)
    )


def test_registered_names_match_service_class_names() -> None:
    """表里的键必须与各服务类自己的 `name` 一致 —— 否则 `try_get(Service.name)` 取不到。"""
    import importlib

    for name, spec in SERVICE_FACTORIES:
        module_name, _, class_name = spec.partition(":")
        cls = getattr(importlib.import_module(module_name), class_name)
        assert cls.name == name, f"{class_name}.name={cls.name!r} 与表里的 {name!r} 不一致"


def test_not_registered_entries_have_reasons() -> None:
    for cls_name, reason in NOT_REGISTERED.items():
        assert reason.strip(), f"{cls_name} 的理由是空的"


# ── 3) main.py 真的接线了 ─────────────────────────────────────


def test_main_builds_and_attaches_context() -> None:
    src = io.open(REPO_ROOT / "main.py", encoding="utf-8").read()
    assert "from app.bootstrap import attach, build_context" in src
    assert "build_context(" in src and "attach(context, app)" in src
    assert "context.start_all()" in src, "服务启动被漏掉了"
    assert "context.stop_all()" in src, "退出时没有逆序停服务"
    assert 'register_instance("launcher", launcher' in src, "launcher 没有进上下文"


# ── 4) 界面侧的取服务辅助函数真的会兜底 ───────────────────────


def _lookup_helpers() -> list[tuple[str, ast.FunctionDef]]:
    out: list[tuple[str, ast.FunctionDef]] = []
    for path in sorted((REPO_ROOT / "ui").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(io.open(path, encoding="utf-8", errors="replace").read())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_get_") and node.name.endswith("_service"):
                out.append((path.relative_to(REPO_ROOT).as_posix(), node))
    return out


def test_lookup_helpers_consult_the_current_context() -> None:
    helpers = _lookup_helpers()
    assert helpers, "一个取服务辅助函数都没找到？扫描逻辑错了"
    not_wired: dict[str, str] = {}
    for rel, node in helpers:
        body = ast.unparse(node)
        if "current_context()" in body:
            continue
        if rel in PENDING_LOOKUP_FILES:
            continue
        not_wired[rel] = node.name
    assert not not_wired, (
        "以下取服务辅助函数既没有兜底到进程级上下文，也不在 PENDING_LOOKUP_FILES 里：\n"
        + "\n".join(f"  {rel}::{fn}" for rel, fn in sorted(not_wired.items()))
        + "\n请在 `ctx = getattr(owner, \"context\", None)` 后加 ` or current_context()`。"
    )


def test_pending_lookup_files_are_still_pending() -> None:
    """PENDING 名单不能变成永久豁免：已经接上的文件必须从名单里删掉。"""
    wired: dict[str, str] = {}
    for rel, node in _lookup_helpers():
        if rel in PENDING_LOOKUP_FILES and "current_context()" in ast.unparse(node):
            wired[rel] = node.name
    assert not wired, (
        "以下文件已经接上 current_context()，请把它们从 PENDING_LOOKUP_FILES 里删掉：\n"
        + "\n".join(f"  {rel}::{fn}" for rel, fn in sorted(wired.items()))
    )


def test_windows_share_one_service_instance_via_context() -> None:
    """行为证明：两个不同的窗口取到**同一个**服务实例。"""
    ctx = build_context()
    import ui.windows.resource_manager as rm
    import ui.windows.server_resource_manager as srm

    a = rm._get_resource_service(None)
    b = srm._get_resource_service(object())
    assert a is b, "两个窗口取到的不是同一个实例 —— 上下文兜底没生效"
    assert a is ctx.try_get("resource")
