"""重构前基线提交 —— 所有"与原文逐字比对"的测试都从这里取原文。

## 为什么需要这个模块（阶段 2 开工时补的账）

阶段 1 有一批测试把 ``git show HEAD:<路径>`` 当作"搬家前的原文"。搬家提交还在工作区
里的时候，``HEAD`` 恰好就是搬家**前**的形态，所以测试是对的；但**搬家一旦提交进历史，
``HEAD`` 就变成了搬家之后的代码**，于是：

* 旧路径可能只剩一个转发 shim（几十行），"行数与原文一致"整片报红 —— 实测 22 个用例；
* 更坏的是**假绿**：两边都是重构后的代码，比对当然相等，真实的删改被安静地放过去。
  同一条毛病也发生在 ``scripts/relocate_module.py`` 的闸门上（它曾因此报 20 项失败）。

因此基线必须**钉死在一个提交上**，与 `git log` / 当前分支状态完全无关，才能重复执行。

## 真相来源

`scripts/relocate_module.py` 的 ``BASELINE_COMMIT`` 是权威值；本文件保存一份副本，好让
测试不依赖"动态加载脚本"这一手。两边不一致会被
``test_services_relocation.py::test_pristine_baseline_pin_is_valid`` 挡住 ——
这和本仓库既有的"两张登记表必须同步"是同一套防守思路。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 重构前最后一个提交（v2.12.6 的发布提交）。**不要改成 HEAD**，理由见模块文档。
PRISTINE_COMMIT = "640f7eb90595a996f88901eea4a5302e7f9c48c9"

#: 基线自检探针：(路径, 该路径在基线里**是否应该存在**)。
#: 选的是可证伪的硬事实 —— 重构前 `services/` 包根本不存在，旧的大文件还在原位。
PRISTINE_PROBES = (
    ("services/__init__.py", False),
    ("ui/app_music.py", True),
    ("achievement_defs.py", True),
    ("ui/agent/providers/anthropic.py", True),
)


def git_show(path: str, *, commit: str = PRISTINE_COMMIT, repo_root: Optional[Path] = None) -> str:
    """取 ``path`` 在指定提交里的原文（默认基线提交）。取不到返回空串。"""
    root = repo_root or REPO_ROOT
    out = subprocess.run(
        ["git", "show", f"{commit}:{path}"], capture_output=True, cwd=str(root)
    )
    if out.returncode != 0:
        return ""
    return out.stdout.decode("utf-8", "replace")


def git_has(spec: str, *, repo_root: Optional[Path] = None) -> bool:
    """``git cat-file -e <spec>`` 是否成功。"""
    root = repo_root or REPO_ROOT
    return subprocess.run(
        ["git", "cat-file", "-e", spec], capture_output=True, cwd=str(root)
    ).returncode == 0


def git_rev_parse(rev: str, *, repo_root: Optional[Path] = None) -> str:
    """解析一个 revision 为完整 SHA（失败返回空串）。"""
    root = repo_root or REPO_ROOT
    out = subprocess.run(
        ["git", "rev-parse", rev], capture_output=True, cwd=str(root)
    )
    return out.stdout.decode("utf-8", "replace").strip() if out.returncode == 0 else ""


__all__ = [
    "PRISTINE_COMMIT",
    "PRISTINE_PROBES",
    "REPO_ROOT",
    "git_has",
    "git_rev_parse",
    "git_show",
]
