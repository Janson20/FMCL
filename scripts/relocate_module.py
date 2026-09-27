#!/usr/bin/env python
"""模块搬家工具（阶段 1「形态 1：整体搬家 + 转发 shim」的机械化实现）。

把已经**零 UI 依赖**的模块从旧位置搬进 `services/`，并在原位置留下
命名空间完全一致的转发 shim。适用于那些"逻辑与界面本来就分得开"的模块 ——
命令式地、可重复地、可验证地完成，而不是靠手工复制粘贴。

用法::

    python scripts/relocate_module.py --list              # 列出登记过的搬家
    python scripts/relocate_module.py --check             # 校验已搬的（CI 可用）
    python scripts/relocate_module.py --apply backup_manager   # 搬一个
    python scripts/relocate_module.py --apply-all         # 搬全部未搬的

为什么值得做成工具而不是一次性脚本：

1. **手工搬运无法证明"逐字一致"**。本工具对每个块做递归剔除 import 后的
   逐节点 AST 比对，并断言"新文件行数与 git 原文一致"。
2. **shim 的命名空间必须完整**。仓库里真实存在
   ``from achievement_engine import _do_merge_db`` 这类**私有名**导入，
   所以 shim 用 ``globals().update(vars(_impl))`` 而不是 ``from ... import *``
   （后者会丢掉私有名，那条导入直接 ImportError）。
3. 校验方向要双向：既验"搬过去的还在"，也验"**没有丢东西**"。
   只验前者会漏掉锚点之间的空隙 —— 本仓库在抽取 `ui/app_monitor.py` 时
   就真的丢过两个常量，直到运行时 NameError 才暴露。

**已登记的行数偏差**（`REGISTERED_LINE_DELTAS`）：搬家**完成之后**带任务号的
缺陷修复会合法地改变新文件的行数，于是"行数与 git 原文一致"这条会永远报红。
而**一个永远红的闸门比没有闸门更糟** —— 它会训练所有人忽略红色。
折中办法与 `poc/_post_extraction_patches.py` 同一套路：偏差逐条登记（带任务号
与理由），判据改成 `新文行数 == 原文行数 + 已登记偏差`；不在表里的文件仍然必须
**完全一致**。另外每条例外都必须**被命中**，命中 0 次要报错 —— 防止这张表
腐化成"哪里红了往哪里加"的垃圾场。

**基线不是 `HEAD`**（`BASELINE_COMMIT`）：搬家提交进历史之后，`HEAD` 就是搬家
**之后**的代码，"用 HEAD 取原文"会变成拿重构后的代码跟自己对比较 —— 轻则整片
报红，重则假绿。详见 `BASELINE_COMMIT` 上方的说明。

退出码：0 = 通过；1 = 有失败；2 = 用法错误。
"""

from __future__ import annotations

import argparse
import ast
import importlib
import io
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

#: 「git 原文」的**唯一来源**：重构前最后一个提交（v2.12.6 的发布提交 `640f7eb`）。
#:
#: ## 为什么不能写 ``HEAD``（阶段 1 收尾时真的踩了这个坑）
#:
#: 本工具的核心判据是"新文件必须与 git 原文一致"。搬家还在工作区里时，
#: ``HEAD`` 恰好就是搬家**前**的形态，于是"用 HEAD 取原文"看起来没问题。
#: 但搬家一旦提交进历史，``HEAD`` 就变成搬家**之后**的代码：
#:
#: - 旧路径往往只剩一个转发 shim（几十行），于是"行数与原文一致"整片报红；
#: - 更坏的情况是**假绿** —— 拿重构后的代码与重构后的代码比，两边当然一样，
#:   真实的删改被安静地放过去；
#: - 形状 1 的验证依赖"旧路径原文"来反推"没丢东西"，基线错了就等于没验。
#:
#: 因此基线**钉死在一个提交上**，与 `git log` / 当前分支状态完全无关，可重复执行。
#: 要换基线（例如以后基于新版本重新核对）必须显式改这里，并由
#: `check_baseline()` 挡住"钉错提交"。
BASELINE_COMMIT = "640f7eb90595a996f88901eea4a5302e7f9c48c9"

#: 基线自检探针：(提交里的路径, 该路径**是否应该存在**)。
#: 判据选的是可证伪的硬事实 —— 重构前 `services/` 包根本不存在，而旧的大文件还在原位。
#: 谁把基线指到了重构后的提交上，这里立刻报错，而不是产出一堆看不懂的比对失败。
BASELINE_PRISTINE_PROBES: Tuple[Tuple[str, bool], ...] = (
    ("services/__init__.py", False),
    ("ui/app_music.py", True),
    ("achievement_defs.py", True),
    ("ui/agent/providers/anthropic.py", True),
)


def _git_has(spec: str) -> bool:
    """``git cat-file -e <spec>`` 是否成功（spec 形如 ``<提交>:<路径>``）。"""
    return subprocess.run(
        ["git", "cat-file", "-e", spec], capture_output=True, cwd=str(REPO_ROOT)
    ).returncode == 0


def check_baseline() -> Tuple[bool, str]:
    """自检 ``BASELINE_COMMIT`` 本身可用：提交可达，且确实处于**重构前**形态。

    返回 ``(是否通过, 说明)``。这是"闸门自己也要被测试"的一个具体落实：
    比对用的基线一旦失效，整套判据就全是空话。
    """
    if not _git_has(f"{BASELINE_COMMIT}^{{commit}}"):
        return False, f"提交 {BASELINE_COMMIT} 在本地仓库里取不到（历史被重写？仓库不完整？）"
    bad = []
    for path, should_exist in BASELINE_PRISTINE_PROBES:
        exists = _git_has(f"{BASELINE_COMMIT}:{path}")
        if exists != should_exist:
            bad.append(f"{path} {'存在' if exists else '不存在'}（期望{'存在' if should_exist else '不存在'}）")
    if bad:
        return False, f"该提交不是重构前形态：{'；'.join(bad)}"
    return True, ""


@dataclass
class Move:
    """一次搬家：旧路径 → 新路径，外加对 import 来源的最小改写。

    ``package=True`` 时 ``src``/``dst`` 是**目录**：目录下每个 ``*.py`` 都会被
    整体搬走，并在旧目录里为**每个文件**留一个 shim —— 因为调用方可能直接
    import 子模块（本仓库的测试就写了 ``from ui.music_source.bili import ...``）。
    """

    src: str
    dst: str
    title: str
    refs: int = 0
    rewrites: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)
    package: bool = False


MOVES: List[Move] = [
    Move("achievement_defs.py", "services/achievement_defs.py",
         "成就定义（成就数据结构、触发条件）", 1),
    Move("achievement_engine.py", "services/achievement_engine.py",
         "成就引擎（47 项成就、9 大分类、多阶段、SQLite 持久化）", 22,
         (("from achievement_defs import", "from services.achievement_defs import"),)),
    Move("achievement_sync.py", "services/achievement_sync.py",
         "成就云存档同步（REST 推送/拉取/合并）", 4,
         (("from achievement_engine import", "from services.achievement_engine import"),)),
    Move("ui/server_config_schema.py", "services/server_config_schema.py",
         "服务器配置项 schema（类型/范围/枚举校验）", 1),
    Move("ui/music_lyrics.py", "services/music_lyrics.py",
         "歌词解析（LRC/翻译/罗马音）", 4,
         (("from ui.music_lyrics import", "from services.music_lyrics import"),)),
    Move("ui/music_effects.py", "services/music_effects.py",
         "音效 DSP（EQ/混响/变调/变速）", 1),
    # 返工 E 组补登记：这次搬家当初**漏登**了（`ui/music_playlist.py` 的 shim 一直在、
    # 测试侧的 `MOVED` 也一直覆盖它，但闸门这张表里没有它 —— 于是"行数是否与原文一致"
    # 这件事在闸门侧**从来没被校验过**）。补上它之后，D-20 / D-22 的行数偏差才有了
    # 合法的登记位置（两张登记表的键集合必须一致，见
    # `tests/test_services_relocation.py::test_registered_line_deltas_agree_with_the_relocate_gate`）。
    # refs = 11：按本文件的口径（全仓 .py 里「模块点分路径 + 非标识符字符」的出现次数，
    # 排除 poc/）实测得出。
    Move("ui/music_playlist.py", "services/music_playlist.py",
         "歌单持久化（歌单增删改、排序、拼音检索）", 11),
    Move("backup_manager.py", "services/backup_manager.py",
         "存档备份管理（备份/恢复/删除/校验/导出/ZIP 压缩）", 8),
    # 任务 1.4 的"音乐源适配"整块：9 个文件、约 164KB，实测 UI 触点全为 0，
    # 所以可以整包搬走，不需要切分逻辑/界面。
    Move("ui/music_source", "services/music_source",
         "音乐源适配（网易云/QQ/酷狗/酷我/咪咕/B站）", 20,
         (("from ui.music_source", "from services.music_source"),), package=True),
    # ─── 任务 1.14：Agent 服务 ───────────────────────────────────────
    # 实测：ui/agent/ 下除 agent_chat.py / agent_mixin.py / agent_mixin / voice_input.py
    # 之外，所有模块对 GUI 栈（tkinter/customtkinter/tkinterweb/tkhtmlview）的
    # import 数都是 **0**；包内互相引用一律是 `from ui.agent.x import ...` 的绝对
    # 形式，所以一条改写 `from ui.agent.` → `from services.agent.` 即可全覆盖。
    # refs 口径：全仓库 .py 中「模块点分路径 + 非标识符字符」的出现次数（已排除
    # poc/ 下的临时探针），避免 provider 命中 providers 这种子串误计。
    Move("ui/agent/models.py", "services/agent/models.py",
         "Agent 数据模型（模型目录 / ModelInfo / 默认模型）", 7,
         (("from ui.agent.", "from services.agent."),)),
    Move("ui/agent/stream.py", "services/agent/stream.py",
         "SSE 流式解析（SSEParser / SSEEventType）", 3,
         (("from ui.agent.", "from services.agent."),)),
    Move("ui/agent/provider.py", "services/agent/provider.py",
         "AI 提供商抽象层（BaseProvider / URL 归一化 / 连接测试）", 8,
         (("from ui.agent.", "from services.agent."),)),
    Move("ui/agent/session.py", "services/agent/session.py",
         "会话管理（AgentSession 新建/加载/列表/删除/压缩）", 1,
         (("from ui.agent.", "from services.agent."),)),
    Move("ui/agent/system_prompt.py", "services/agent/system_prompt.py",
         "系统提示词组装", 2,
         (("from ui.agent.", "from services.agent."),)),
    Move("ui/agent/permission.py", "services/agent/permission.py",
         "权限三级判定（allow/deny/ask + 通配符规则 + 持久化）", 2,
         (("from ui.agent.", "from services.agent."),)),
    Move("ui/agent/config.py", "services/agent/config.py",
         "Agent 配置（Provider 密钥 / 模型选择 / 权限规则读写）", 8,
         (("from ui.agent.", "from services.agent."),)),
    Move("ui/agent/skill.py", "services/agent/skill.py",
         "技能加载（SKILL.md 扫描与 prompt 注入文本）", 2,
         (("from ui.agent.", "from services.agent."),)),
    # engine.py 全仓库零调用点（除本次探针外 refs=0），但它有 1008 行纯逻辑，
    # 一并搬走留痕，不做删除。
    Move("ui/agent/engine.py", "services/agent/engine.py",
         "旧工具执行引擎（execute_tool 与 27 个 _xxx 实现，已被 tool_registry 取代）", 0,
         (("from ui.agent.", "from services.agent."),)),
    Move("ui/agent/tool_registry.py", "services/agent/tool_registry.py",
         "工具注册表（ToolRegistry 单例 / 定义导出 / 结构化执行）", 3,
         (("from ui.agent.", "from services.agent."),)),
    Move("ui/agent/providers", "services/agent/providers",
         "模型供应商适配（净读 AI / OpenAI / Anthropic / 自定义）", 21,
         (("from ui.agent.", "from services.agent."),), package=True),
    Move("ui/agent/tools", "services/agent/tools",
         "Agent 工具实现（文件/版本/模组/整合包/资源/服务器/系统/网页）", 35,
         (("from ui.agent.", "from services.agent."),), package=True),
]

SHIM_TEMPLATE = '''"""{title}（转发 shim）

实现已搬到 ``{dst}``。

**本模块名直接指向实现模块**（``sys.modules[__name__] = _impl``），而不是用
``globals().update(...)`` 复制引用。后者只能保证"读"到同一批对象，**"写"传不过去**：
``import {mod} as m; m.FOO = fake`` 只会改到 shim 的命名空间，实现模块里的
``FOO`` 不变 —— monkeypatch 会**静默失效**。

这个坑是实测踩到的：``ui/music_source/`` 搬走后，``tests/test_music_fallback.py``
里的 ``monkeypatch.setattr(ms, "MUSIC_SOURCES", fakes)`` 失效，测试**真去请求了
QQ 音乐接口**。

别名方式让 ``{mod} is {dst_mod}`` 成立：读、写、打补丁、``is`` 判定全部与
搬家前一致。

**同时还把实现模块的名字复制一份到本模块的 ``__dict__``**，用于兼容另一种加载
方式：仓库里 ``tests/test_music_playlist.py`` 是按**文件路径**加载模块
（``spec_from_file_location`` + 自己注册进 ``sys.modules``）以绕开 ``ui/__init__.py``
的 GUI 导入，它会**保留 exec 之前的模块对象** —— 只做别名的话那个对象仍是空壳
（实测踩到过：``mp.PlaylistSong`` AttributeError）。两种机制都很便宜，一起用最稳。

阶段 3 完成后本文件可删除。
"""

import sys

import {dst_mod} as _impl

sys.modules[__name__] = _impl

globals().update({{k: v for k, v in vars(_impl).items() if not k.startswith("__")}})
'''

PACKAGE_SHIM_TEMPLATE = '''"""{title}（转发 shim）

实现已搬到 ``{dst}``。

**本包名与各子模块名都直接指向实现模块**（``sys.modules[__name__] = _impl``），
而不是用 ``globals().update(...)`` 复制引用 —— 后者只能保证"读"到同一批对象，
**"写"传不过去**（monkeypatch 会静默失效，实测踩过）。

子模块也必须一并别名：否则 ``import {mod}.sub`` 会以旧包名把实现**再加载一次**，
产生两份模块对象（两个类 → ``isinstance`` 跨不过去）。

别名之外仍把名字复制一份到本模块 ``__dict__``，兼容"先取模块对象、再 exec"的
加载方式（理由见单模块模板里的说明）。

阶段 3 完成后本文件可删除。
"""

import importlib
import sys

import {dst_mod} as _impl

#: 实现包里的子模块（生成时按当时的文件清单写死）
_SUBMODULES: tuple = {submodules!r}

for _sub in _SUBMODULES:
    sys.modules[f"{{__name__}}.{{_sub}}"] = importlib.import_module(f"{{_DST}}.{{_sub}}")

sys.modules[__name__] = _impl

globals().update({{k: v for k, v in vars(_impl).items() if not k.startswith("__")}})
'''


def read(path: Path) -> str:
    """按原样读（不翻译换行符），搬家前后保持行尾一致。"""
    return io.open(path, encoding="utf-8", newline="").read()


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    io.open(path, "w", encoding="utf-8", newline="").write(text)


def strip_all_imports(src: str) -> str:
    """**递归**剔除所有 import 与模块 docstring 后 dump AST。

    递归是必须的：延迟导入常常写在函数体里（`achievement_sync.py` 就是），
    只剔模块级 import 会把这些行算成"差异"，产生假阴性。
    """

    class Stripper(ast.NodeTransformer):
        def visit_Import(self, node):  # noqa: N802
            return None

        def visit_ImportFrom(self, node):  # noqa: N802
            return None

    tree = ast.parse(src)
    body = []
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        body.append(Stripper().visit(node))
    return ast.dump(ast.Module(body=body, type_ignores=[]))


def is_shim(path: Path, package: bool = False) -> bool:
    """判断旧位置是否已经是转发 shim（目录则以 ``__init__.py`` 为准）。"""
    target = path / "__init__.py" if package else path
    if not target.exists():
        return False
    text = read(target)
    return "_impl" in text and len(text.splitlines()) < 60


def _module_units(m: Move) -> List[Tuple[Path, Path, str, str]]:
    """把一次搬家展开成「单个模块」列表：(旧文件, 新文件, 旧模块名, 新模块名)。"""
    src, dst = REPO_ROOT / m.src, REPO_ROOT / m.dst
    if not m.package:
        old_mod = m.src[:-3].replace("/", ".")
        new_mod = m.dst[:-3].replace("/", ".")
        return [(src, dst, old_mod, new_mod)]

    units = []
    for path in sorted(src.glob("*.py")):
        rel = path.relative_to(src)
        old_mod = f"{m.src.replace('/', '.')}.{path.stem}" if path.name != "__init__.py" else m.src.replace("/", ".")
        new_mod = f"{m.dst.replace('/', '.')}.{path.stem}" if path.name != "__init__.py" else m.dst.replace("/", ".")
        units.append((path, dst / rel, old_mod, new_mod))
    return units


def git_original(path: str) -> str:
    """取 ``path`` 在**基线提交**（不是 HEAD）里的原文，见 ``BASELINE_COMMIT``。"""
    out = subprocess.run(
        ["git", "show", f"{BASELINE_COMMIT}:{path}"], capture_output=True, cwd=str(REPO_ROOT)
    )
    return out.stdout.decode("utf-8")


class Reporter:
    def __init__(self) -> None:
        self.failures: List[str] = []
        #: 本次校验**实际命中**的已登记行数偏差（键 = 新文件相对路径）。
        #: 收尾时用它做"登记项过期"检查：登记了却一次都没命中 → 报错。
        self.used_line_deltas: set = set()
        #: 本次真的校验过几个搬家条目（0 表示没走到校验路径，此时不该做过期检查）
        self.verified_moves: int = 0

    def check(self, label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
        if not ok:
            self.failures.append(label)

    def mark_delta_used(self, new_rel: str) -> None:
        self.used_line_deltas.add(new_rel)

    def check_line_delta_registry(self) -> None:
        """登记项过期检查（反向守卫）：**已登记的行数偏差必须全部被命中**。

        没有这条，`REGISTERED_LINE_DELTAS` 会慢慢腐化：代码后来改回来了、
        文件被再次搬家、或者当初就是随手加的一条，都不会有人发现 ——
        于是这张表变成"永久豁免名单"。这里逼着登记项保持**有效且必要**。
        """
        stale = sorted(set(REGISTERED_LINE_DELTAS) - self.used_line_deltas)
        self.check(
            f"已登记的行数偏差全部被命中（{len(REGISTERED_LINE_DELTAS)} 项）",
            not stale,
            f"过期登记（文件已与原文一致、或该搬家没被校验到）：{stale}" if stale else "",
        )


def regen_shim(m: Move, rep: Reporter) -> None:
    """按**当前模板**重写某个条目的 shim（不改实现、不动实现文件）。

    为什么需要它：shim 模板本身会演进。实测踩过一次 —— 第一版模板用
    ``globals().update`` 复制引用，发现"打补丁传不过去"后修好了模板，
    但**已经搬过的那些条目的 shim 文件仍是旧的**，于是新搬的继续生成旧形式。
    与其每次写一次性修复脚本，不如让工具能重生成。
    """
    src = REPO_ROOT / m.src
    if not src.exists():
        return
    if m.package:
        target = src / "__init__.py"
        submodules = sorted(p.stem for p in src.glob("*.py") if p.name != "__init__.py")
        if not submodules:
            # 子模块 shim 已被清掉，从实现包里取清单
            impl_dir = REPO_ROOT / m.dst
            submodules = sorted(p.stem for p in impl_dir.glob("*.py") if p.name != "__init__.py")
        old_text = read(target)
        shim = PACKAGE_SHIM_TEMPLATE.format(
            title=m.title, dst=m.dst, mod=m.src.replace("/", "."),
            dst_mod=m.dst.replace("/", "."), submodules=tuple(submodules),
        ).replace("{_DST}", m.dst.replace("/", "."))
    else:
        target = src
        old_text = read(target)
        shim = SHIM_TEMPLATE.format(
            title=m.title, dst=m.dst, mod=m.src[:-3].replace("/", "."), refs=m.refs,
            dst_mod=m.dst[:-3].replace("/", "."),
        )
    if "\r\n" in old_text:
        shim = shim.replace("\n", "\r\n")
    write(target, shim)
    rep.check(f"{m.src} shim 已按当前模板重生成",
              "sys.modules[__name__] = _impl" in read(target), f"{len(shim.splitlines())} 行")


def apply_move(m: Move, rep: Reporter) -> None:
    src, dst = REPO_ROOT / m.src, REPO_ROOT / m.dst
    print(f"\n--- 搬家：{m.src} → {m.dst} ---")

    if is_shim(src, m.package):
        print("  [SKIP] 旧位置已是 shim，跳过")
        return
    if dst.exists():
        rep.check(f"{m.dst} 尚不存在（避免覆盖）", False)
        return

    units = _module_units(m)
    rep.check(f"找到 {len(units)} 个模块", bool(units))

    # 整包搬家：包内的每个子模块都要在**包自己的 __init__** 里登记别名，
    # 子模块文件本身不再单独留 shim（留着只会让人以为它还在起作用）。
    submodule_names = (
        sorted(p.stem for p in src.glob("*.py") if p.name != "__init__.py") if m.package else []
    )

    for old_file, new_file, old_mod, new_mod in units:
        text = read(old_file)
        crlf = "\r\n" in text
        moved = text
        for old, new in m.rewrites:
            moved = moved.replace(old, new)

        rep.check(f"{old_file.name} 主体 AST 逐节点一致",
                  strip_all_imports(text) == strip_all_imports(moved),
                  f"{len(text.splitlines())} 行")

        write(new_file, moved)
        rep.check(f"{new_file.relative_to(REPO_ROOT)} 已写入",
                  new_file.exists(), f"{len(moved.splitlines())} 行")

        if m.package:
            # 只有包的 __init__ 需要 shim；子模块由它统一别名
            if old_file.name != "__init__.py":
                old_file.unlink()
                continue
            shim = PACKAGE_SHIM_TEMPLATE.format(
                title=m.title, dst=m.dst, mod=m.src.replace("/", "."),
                dst_mod=m.dst.replace("/", "."), submodules=tuple(submodule_names),
            ).replace("{_DST}", m.dst.replace("/", "."))
        else:
            shim = SHIM_TEMPLATE.format(
                title=m.title, dst=m.dst, mod=old_mod, refs=m.refs, dst_mod=new_mod,
            )

        if crlf:
            shim = shim.replace("\n", "\r\n")
        write(old_file, shim)


#: 搬家完成之后**已登记**的行数变化：``{新文件相对路径(posix): (行数增量, 理由)}``。
#:
#: 这些不是"内容被删改"，而是搬家之后带任务号的缺陷修复。判据没有放松 ——
#: 不在表里的文件仍然必须与原文行数完全一致；每条例外都要写清归属与原因。
#: **每条例外都必须被命中**：命中 0 次（或增量对不上）都会报错，防止登记过期后
#: 这张表变成永远免疫的垃圾场。
REGISTERED_LINE_DELTAS: Dict[str, Tuple[int, str]] = {
    "services/music_effects.py": (
        99,
        "返工 E 组 D-147 + D-25（WP3，`poc/review/e_group/wp3_music_effects.md`）："
        "D-147 是「音效链依赖外部 ffmpeg，却既没声明、也没探测，失败完全静默」——"
        "真正做解码/编码的是外部 ffmpeg 进程（pydub 只是容器），而原来的守卫连日志都没有，"
        "用户看到的是「设了没反应」；修法是新增 `_find_ffmpeg()` / `FFMPEG_PATH` / "
        "`ffmpeg_available()` / `unavailable_reason()`（可查询的可用性状态）并把整体跳过与"
        "每个单效果的失败都记成可定位的 WARNING。"
        "D-25 是 `_apply_speed` 的未用形参（改为变速结果长度校验，正好是 D-19 的进度基准）"
        "与 `_apply_reverb` 的无用赋值（删除），并新增 D-19 要用的纯函数 "
        "`effective_duration()` / `playback_duration()`。"
        "总行数 +99（非空行 +82，多出的 17 行是函数之间的空行与 docstring 分段）。"
        "同一条偏差也登记在 tests/test_services_relocation.py 的 REGISTERED_LINE_DELTAS 里"
        "（那里按**非空行**算，所以登记的是 +82）。",
    ),
    "services/music_playlist.py": (
        53,
        "返工 E 组 D-20 + D-22（用户已裁决的第 1、2 批）：D-20 是写盘前的目录创建没有保护"
        "（父目录建不出来/无权限时直接抛）而调用方都吞异常 → 表现为「歌单静默不落盘」；"
        "修法按 config.py 的同构做法（先试 primary、不可写就回退到 %LOCALAPPDATA%\\FMCL\\data "
        "并打日志、两者都不可写打 error），落盘失败保留脏标记等下次重试。"
        "D-22 是把 normpath 从「每首歌算一次」提到循环外（统一比对键）。"
        "总行数 +53（非空行 +34，多出的 19 行是注释块、空行与函数之间的分隔）。"
        "同一条偏差也登记在 tests/test_services_relocation.py 的 REGISTERED_LINE_DELTAS 里"
        "（那里按**非空行**算，所以登记的是 +34）。",
    ),
    "services/backup_manager.py": (
        11,
        "D-112（阶段 1 第 11 轮）：同一秒内的两次备份会因文件名只有秒级精度（"
        "{存档名}_{YYYYmmdd_HHMMSS}.zip）而互相覆盖 —— 索引留下多条指向同一文件的记录，"
        "于是删一条会连带删掉另一条的备份文件、自动清理会删掉仅存的那个文件。"
        "修法是撞名时追加 6 位短后缀（一次备份 = 一个文件）+ 说明注释："
        "总行数 +11（非空行 +10，多出的 1 行是注释块前后的空行）。"
        "同一条偏差也登记在 tests/test_services_relocation.py 的 REGISTERED_LINE_DELTAS 里"
        "（那里按**非空行**算，所以登记的是 +10）。",
    ),
    "services/agent/engine.py": (
        1,
        "第 12 轮：读子进程文本输出的调用补 `errors=\"replace\"`。"
        "`subprocess.run(..., text=True)` 不指定 encoding 时用 "
        "`locale.getpreferredencoding(False)`，而项目自己的文档命令是 "
        "`python -X utf8 -m pytest` —— UTF-8 模式下子进程按 OEM 码页输出的中文会在 "
        "subprocess 的 reader 线程里抛 UnicodeDecodeError（实测 byte 0xd0）。"
        "补这个 kwarg 让那处调用多占一行。"
        "**同族另一处 `services/agent/tools/system.py` 的 +1 登记在 "
        "tests/test_agent_service.py 的 REGISTERED_LINE_DELTAS 里** —— "
        "它不在这张表的文件清单里（本表只覆盖 `scripts/relocate_module.py::MOVES` 里的条目）。",
    ),
}


def verify_move(m: Move, rep: Reporter) -> None:
    print(f"\n--- 校验：{m.src} → {m.dst} ---")
    rep.verified_moves += 1
    rep.check(f"{m.src} 是 shim（实现已不在原地）", is_shim(REPO_ROOT / m.src, m.package))

    for old_file, new_file, old_mod, new_mod in _module_units(m):
        name = old_file.name if m.package else m.src
        rep.check(f"{new_file.relative_to(REPO_ROOT)} 存在", new_file.exists())

        # 行数与 git 原文一致 —— "内容整体搬走、没有删改"最直接的机械证据
        git_path = old_file.relative_to(REPO_ROOT).as_posix()
        original = git_original(git_path)
        if original.strip():
            impl_lines = len(read(new_file).splitlines())
            orig_lines = len(original.splitlines())
            new_rel = new_file.relative_to(REPO_ROOT).as_posix()
            delta, why = REGISTERED_LINE_DELTAS.get(new_rel, (0, ""))
            if new_rel in REGISTERED_LINE_DELTAS:
                rep.mark_delta_used(new_rel)
            detail = f"原文 {orig_lines} / 新文 {impl_lines}"
            if delta:
                detail += f"（已登记偏差 +{delta}：{why}）"
            elif impl_lines != orig_lines:
                detail += "（内容被删改了？）"
            rep.check(f"{name} 行数与 git 原文一致", impl_lines == orig_lines + delta, detail)

        try:
            old = importlib.import_module(old_mod)
            new = importlib.import_module(new_mod)
        except Exception as e:  # noqa: BLE001
            rep.check(f"{old_mod} / {new_mod} 可导入", False, f"{type(e).__name__}: {e}")
            continue
        rep.check(f"{old_mod} / {new_mod} 可导入", True)

        # 实现模块"自己定义"的名字（排除它 import 进来的模块对象）必须逐个同对象
        imported_names = set()
        for node in ast.parse(read(new_file)).body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported_names.add(alias.asname or alias.name)

        own = [
            n for n in vars(new)
            if not n.startswith("__") and n not in imported_names and not isinstance(vars(new)[n], type(importlib))
        ]
        if not own and m.package and old_file.name == "__init__.py":
            # 纯 re-export 的包 __init__（实测：services/agent/providers 只做
            # `from ...providers.x import XProvider`）**自己一个名字都不定义**，
            # 于是"有自定义名"这条健全性断言不适用。此时真正该验的是**子模块别名**：
            # 少了它，`import ui.agent.providers.jingdu` 会以旧包名把实现再加载一次，
            # 产生两份模块对象（两个类 → isinstance 跨不过去）。
            subs = sorted(p.stem for p in (REPO_ROOT / m.dst).glob("*.py") if p.name != "__init__.py")
            bad = [
                s for s in subs
                if importlib.import_module(f"{old_mod}.{s}") is not importlib.import_module(f"{new_mod}.{s}")
            ]
            rep.check(f"{new_mod} 是纯 re-export 包，改验 {len(subs)} 个子模块别名",
                      bool(subs) and not bad, f"不一致 {bad}")
        else:
            rep.check(f"{new_mod} 有 {len(own)} 个自定义名", bool(own))
        missing = [n for n in own if not hasattr(old, n)]
        rep.check(f"{old_mod} 全部自定义名可取到", not missing, f"缺失 {missing}")
        mismatched = [n for n in own if getattr(old, n, None) is not getattr(new, n)]
        rep.check(f"{old_mod} 自定义名与实现同一对象", not mismatched, f"不一致 {mismatched}")

        privates = [n for n in dir(new) if n.startswith("_") and not n.startswith("__")]
        priv_bad = [n for n in privates if getattr(old, n, None) is not getattr(new, n)]
        rep.check(f"私有名转发一致（{len(privates)} 个）", not priv_bad, f"不一致 {priv_bad}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="模块搬家工具（形态 1）")
    parser.add_argument("--list", action="store_true", help="列出登记的搬家")
    parser.add_argument("--check", action="store_true", help="校验已搬的（不修改文件）")
    parser.add_argument("--apply", metavar="SRC", help="搬一个（用旧路径或模块名指定）")
    parser.add_argument("--apply-all", action="store_true", help="搬全部未搬的")
    parser.add_argument("--regen-shims", action="store_true",
                        help="按当前模板重写所有条目的 shim（不改实现文件）")
    args = parser.parse_args(argv)

    if args.list:
        for m in MOVES:
            src = REPO_ROOT / m.src
            if is_shim(src, m.package):
                state = "已是 shim"
            elif (REPO_ROOT / m.dst).exists():
                state = "已存在新文件"
            else:
                state = "待搬"
            kind = "包" if m.package else "模块"
            print(f"[{kind}] {m.src:26} -> {m.dst:34} [{state}]  {m.title}")
        return 0

    rep = Reporter()

    # 基线自检必须**先做**：基线坏了，后面每一条"与原文一致"的结论都不成立，
    # 而它产生的是一整片看不懂的比对失败 —— 那会把人引向错误的排查方向。
    if args.check or args.apply or args.apply_all or args.regen_shims:
        ok, detail = check_baseline()
        if not ok:
            print(f"\n[FAIL] 原文基线不可用：{detail}", file=sys.stderr)
            print(f"       基线提交 = {BASELINE_COMMIT}", file=sys.stderr)
            print("       基线不可用时，本工具的任何比对结论都不成立，直接判失败。", file=sys.stderr)
            return 1
        print(f"\n原文基线：{BASELINE_COMMIT[:7]} —— 「git 原文」一律取自这个**重构前**的提交，"
              "与当前 HEAD 无关")

    if args.regen_shims:
        for m in MOVES:
            if (REPO_ROOT / m.dst).exists():
                regen_shim(m, rep)
        # 顺带清字节码，避免旧 .pyc 掩盖问题
        for cache in REPO_ROOT.rglob("__pycache__"):
            if ".venv" in cache.parts:
                continue
            for f in cache.glob("*.pyc"):
                try:
                    f.unlink()
                except OSError:
                    pass

    if args.apply or args.apply_all:
        targets = MOVES if args.apply_all else [m for m in MOVES if args.apply in (m.src, Path(m.src).stem)]
        if not targets:
            print(f"没有匹配的搬家：{args.apply}", file=sys.stderr)
            return 2
        for m in targets:
            apply_move(m, rep)
        # 清掉可能过期的字节码，避免旧 .pyc 掩盖问题
        for cache in REPO_ROOT.rglob("__pycache__"):
            if ".venv" in cache.parts:
                continue
            for f in cache.glob("*.pyc"):
                try:
                    f.unlink()
                except OSError:
                    pass

    if args.check or args.apply or args.apply_all:
        for m in MOVES:
            # 注意必须传 m.package：目录搬家的 src 是目录，按文件去读会
            # 抛 PermissionError（这是一个真实踩过的 bug）。
            if is_shim(REPO_ROOT / m.src, m.package) and (REPO_ROOT / m.dst).exists():
                verify_move(m, rep)

    if args.regen_shims:
        # 重生成后也要校验，否则"改了模板但没生效"不会暴露
        for m in MOVES:
            if (REPO_ROOT / m.dst).exists():
                verify_move(m, rep)

    # 登记项过期检查放在最后：只有真的校验过搬家时，"没命中"才说明登记过期
    # （否则 `--apply` 一个全新条目时会把别人的登记项误判成过期）。
    if rep.verified_moves:
        rep.check_line_delta_registry()

    print("\n" + "=" * 72)
    if rep.failures:
        print(f"搬家工具：{len(rep.failures)} 项失败 -> {rep.failures}")
        return 1
    print("搬家工具：全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
