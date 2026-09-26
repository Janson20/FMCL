"""公告服务 —— 拉取启动公告（阶段 2 任务 2.14 的"协议 → 公告 → 预下载"链条要用）。

## 为什么从 `ui/dialogs.py` 搬过来

原实现是 `ui/dialogs.py` 里的 `NOTICE_URL` + `fetch_notice()`，**本身零界面依赖**
（一次 `requests.get` + 去空白），但它住在 `ui/` 包里 —— 而 QML 运行时**不许 import `ui/`**
（`ui/__init__.py` 会拉起 customtkinter，阶段 0 第 10.4 节实测踩过），
于是新 UI 根本用不上它。这与阶段 2 把字体检测搬进 `services/font_service.py` 是同一类问题、
同一种修法（形态 ①：原样搬家 + 旧位置再导出）。

`ui/dialogs.py` 里的 `fetch_notice` 现在是本模块的转发，Tk 界面行为逐字不变。

**requests 必须带 UA**（工作区规范）：沿用旧实现的行为 —— 旧代码没显式设 UA，
这里**不擅自改动它的请求语义**（改 UA 属于行为变更，要单独登记）。
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger("services.notice")

#: 公告地址（与旧实现逐字一致）
NOTICE_URL = "https://jingdu.qzz.io/static/fmcl-notice.txt"

#: 超时（与旧实现逐字一致）
TIMEOUT_S = 10


def fetch_notice(url: str = NOTICE_URL, timeout: int = TIMEOUT_S) -> Optional[str]:
    """拉取公告正文。失败或内容为空返回 ``None``（**不抛异常** —— 公告失败不该挡住启动）。

    与旧实现的差异只有一处：把 `url` / `timeout` 提成参数，便于测试注入（默认值不变）。
    """
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = "utf-8"
        text = resp.text.strip()
        return text if text else None
    except Exception as e:  # noqa: BLE001 - 网络问题绝不能让启动失败
        logger.warning("获取公告失败: %s", e)
        return None


__all__ = ["NOTICE_URL", "TIMEOUT_S", "fetch_notice"]
