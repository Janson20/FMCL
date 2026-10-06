"""把单实例守卫的键改成"本次进程唯一"（测试 / 探针共用的一份实现）。

## 为什么需要它

单实例是**生产行为**：`main_qml.assemble()` 里 `SingleInstance(default_key(...))`
抢不到锁就抛 `AlreadyRunning`（"已有实例在跑"）。可它顺带把"整个进程独占一个键"
变成了测试的前置条件：

* 并行跑测试（`pytest -n auto`）时两个 worker 都去装配应用 → 后到的那个抛
  `AlreadyRunning`，一整个模块的 setup 全红（实测：`tests/test_main_qml_entry.py`
  14 条 ERROR，而代码一个字没错）；
* 开发机上开着启动器时再跑冒烟，同样以 `AlreadyRunning` 收场。

那种红是**环境占用**，不是界面缺陷，而且极易被误读成"界面坏了"。所以这里把键里
带上 PID：守卫本身照常走完（`QLocalServer` 监听、失败即 `AlreadyRunning`），
只是"两个实例不能同时跑"这条语义由进程自己独占一个键来保证。

## 为什么收口成一个模块

`tests/_smoke_driver.py` 与 `tests/qml_startup_probe.py` 各自抄过一份同样的
patch（先写的），`tests/test_main_qml_entry.py` 没有 —— 于是并行跑的时候只有它红。
"同一件事只有一份实现"是本仓库反复强调的规矩，所以第三处要用时先把它提出来。
"""

from __future__ import annotations

import os


def isolate_single_instance(prefix: str = "test") -> str:
    """把 `single_instance.default_key` 换成"原键 + 本进程 PID"。

    Args:
        prefix: 键后缀里的人话标签（`test` / `ui-smoke` / `startup-probe` …），
            只为了在日志里一眼看出是谁占着。

    Returns:
        加上的后缀（测试/探针把它记进报告，便于排查"另一个实例是谁"）。
    """
    from app.bridges import single_instance

    original = single_instance.default_key
    marker = f"-{prefix}-{os.getpid()}"

    def patched(app_name: str, data_dir: str) -> str:
        return original(app_name, data_dir) + marker

    single_instance.default_key = patched  # type: ignore[assignment]
    return marker


__all__ = ["isolate_single_instance"]
