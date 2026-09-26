"""界面层"跨对象写入"的守卫（D-95 家族）。

缺陷形状：窗口拿到别的对象的**私有状态**或**回调字段**，直接读写它。
两个后果都很隐蔽：

* 进度条显示的是别人的进度（两个安装窗口同时开着时）；
* 安装结束时把别人的回调"还原"成旧值甚至 `None`，之后那个回调再也不触发。

D-95 的原始记录：
``ui/windows/modpack_install.py`` 保存 `launcher.on_progress` 并在 `finally` 里还原 ——
**但整个流程从未替换过它**，于是"还原"只会把安装期间别人设的新回调写回旧值；
`modpack_server.py` 则声明了一个从未被读写的 `_orig_on_progress`。

本轮已修：删掉这段死逻辑与跨对象写入（以及那个死字段）。
**仍未解决**（留在 D-95 记录里）：两个窗口都会轮询同一个 `launcher._mp_progress`，
彻底修法要在 `launcher/mrpack.py` 给进度字典加"本次安装"的会话标识，
属于 1.17 改接线 / 阶段 2 换 TaskRunner 的范围。这里只把"已经修掉的部分"钉住，
并把"还欠着的部分"写成显式断言，避免它被当成已修完。
"""

from __future__ import annotations

import ast
import io
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL = REPO_ROOT / "ui" / "windows" / "modpack_install.py"
SERVER = REPO_ROOT / "ui" / "windows" / "modpack_server.py"


def _src(path: Path) -> str:
    return io.open(path, encoding="utf-8").read()


@pytest.mark.parametrize("path", [INSTALL, SERVER])
def test_no_window_writes_launcher_on_progress(path: Path) -> None:
    """任何窗口都不得给 launcher 的 ``on_progress`` 赋值（D-95）。"""
    tree = ast.parse(_src(path))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for tgt in targets:
            if isinstance(tgt, ast.Attribute) and tgt.attr == "on_progress":
                offenders.append(f"{path.name}:{node.lineno} {ast.unparse(tgt)} = ...")
    assert not offenders, "窗口又在改 launcher.on_progress：\n" + "\n".join(offenders)


@pytest.mark.parametrize("path", [INSTALL, SERVER])
def test_no_dead_orig_on_progress_field(path: Path) -> None:
    """`_orig_on_progress` 既不该被赋值、也不该被声明（删干净）。"""
    src = _src(path)
    tree = ast.parse(src)
    assigned = [
        f"{path.name}:{n.lineno}"
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute) and n.attr == "_orig_on_progress" and isinstance(n.ctx, ast.Store)
    ]
    assert not assigned, f"仍在给 _orig_on_progress 赋值：{assigned}"


def test_known_gap_is_documented_not_hidden() -> None:
    """把"还欠着的部分"显式写出来：D-95 只修了一半。

    如果哪天有人真的加了会话标识，这条会失败 —— 那是好事，
    请把这条测试改成断言"两个窗口的轮询带了会话标识"。
    """
    install_src = _src(INSTALL)
    server_src = _src(SERVER)
    assert "_mp_progress" in install_src and "_mp_progress" in server_src, (
        "两个窗口都不再轮询 _mp_progress 了？请核对 D-95 的状态与 07-known-defects.md"
    )
    both_poll = "hasattr(launcher_inst, \"_mp_progress\")" in install_src and (
        "hasattr(launcher_inst, \"_mp_progress\")" in server_src
    )
    assert both_poll, "轮询写法变了，请人工核对 D-95 的剩余风险是否仍然存在"
