"""全仓测试的公共装配。

## 为什么要有这个文件（2026-10-06）

**测试不许改写开发机上那份 `config.json`。**

这一条是花了代价才定下来的：并行跑全量（`pytest -n auto`）之后，用户的
`config.json` 里 `language` 从 `en_US` 变成了 `zh_CN`、`accent_color` 从 `#abcdef`
变成了空串 —— 而用户看到的现象正是"我的语言设置又被改回去了"（D-173的一部分）。
串行跑时看不出来：最后写盘的那个人**恰好**把原值写了回去（谁最后跑完谁说了算），
一并行就露馅。

做法：整场会话装一次"写盘守卫"（`tests/config_isolation.py`），真
`config.save_config()` 只记数不落盘；要验"语言/主题真的会落盘"的用例自带
`FakeConfig`（`tests/test_tr_bridge.py` / `tests/test_theme_bridge.py` 都是这么写的），
不受影响。**子进程**（QML 探针 / 冒烟驱动 / 视觉探针）跑不到这份 conftest，
各自在装配前调一次同一个守卫。
"""

from __future__ import annotations

import logging
from typing import Iterator

import pytest
from config_isolation import isolate_config_writes

logger = logging.getLogger("tests.conftest")


@pytest.fixture(scope="session", autouse=True)
def _block_real_config_writes() -> Iterator[None]:
    """整场测试：真配置只读不写（见文件头）。"""
    blocked = isolate_config_writes("pytest-session")
    yield
    count = blocked()
    if count:
        logger.info("整场测试拦下 %d 次真配置写盘（开发机的 config.json 未被改动）", count)
