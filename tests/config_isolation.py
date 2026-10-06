"""把**真**配置对象的写盘动作挡住（测试 / 探针共用的一份实现）。

## 为什么需要它

不少测试与探针走的是**生产装配路径**（`main_qml.assemble()`、`build_context()`），
它们手里那份 `config` 就是根模块的真单例 —— 于是任何一次 `save_config()` 都会
**改写开发机上那份 `config.json`**。实测踩到的后果（2026-10-06）：

* 跑完一轮 `pytest -n auto`，用户的 `config.json` 里 `language` 从 `en_US` 变成了
  `zh_CN`、`accent_color` 从 `#abcdef` 变成了空串 —— 而用户看到的现象正是
  "我的语言设置又被改回去了"（缺陷 D-173 的一部分）；
* 串行跑时看不出来：最后一个写盘的人**恰好**把原值写了回去（谁最后跑完就由谁说了算），
  一并行就露馅（D-175 的同类问题：测试不该碰用户的真实状态）。

## 用法

在**装配之前**调一次即可（幂等）：

```python
from config_isolation import isolate_config_writes

isolate_config_writes("ui-smoke")   # 之后所有 save_config() 只记数、不落盘
```

要验"语言/主题真的会落盘"的用例**不要**用它 —— 那些用例应该自带 `FakeConfig`
（`tests/test_tr_bridge.py` 就是这么做的），这里挡的只是"顺手把真配置改了"。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger("tests.config_isolation")

#: 进程级已安装的守卫（幂等用）：label → 记账字典
_INSTALLED: Dict[str, Dict[str, Any]] = {}


def isolate_config_writes(label: str = "test") -> Callable[[], int]:
    """把真 `config.save_config` 换成"只记数不落盘"。

    Args:
        label: 记账用的标签（日志里能看出是哪个测试/探针挡下的）。

    Returns:
        一个无参函数，返回**被挡下的次数**（探针把它记进报告，便于排查）。
    """
    from config import config

    state = _INSTALLED.get(label)
    if state is None:
        original = config.save_config
        state = {"blocked": 0, "original": original, "label": label}

        def blocked() -> None:
            state["blocked"] += 1
            if state["blocked"] == 1:
                #: 第一次拦下时把调用方记进日志：排查"到底是谁在写用户配置"时靠它
                #: （只记一次，免得高频写盘把日志刷爆）。
                logger.info("[%s] 拦下配置写盘，调用方：%s", label, _caller())
            logger.debug("[%s] 拦下一次配置写盘（测试不该改开发机的 config.json）", label)

        config.save_config = blocked  # type: ignore[method-assign]
        _INSTALLED[label] = state

    def blocked_count() -> int:
        return int(state["blocked"])

    return blocked_count


def _caller(depth: int = 6) -> str:
    """把调用栈里"测试自己的那一层"找出来（跳过本模块与 pytest 内部）。"""
    import traceback

    frames = traceback.extract_stack()[:-2]
    picked = [
        f"{frame.filename.rsplit(chr(92), 1)[-1]}:{frame.lineno}:{frame.name}"
        for frame in frames
        if "site-packages" not in frame.filename and "config_isolation" not in frame.filename
    ]
    return " <- ".join(picked[-depth:])


def restore_config_writes(label: str = "test") -> Optional[int]:
    """还原（同进程里还要真写盘时用）。返回被挡下的次数；没装过返回 None。"""
    from config import config

    state = _INSTALLED.pop(label, None)
    if state is None:
        return None
    config.save_config = state["original"]  # type: ignore[method-assign]
    return int(state["blocked"])


__all__ = ["isolate_config_writes", "restore_config_writes"]
