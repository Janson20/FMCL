"""公告服务 —— 拉取启动公告（"协议 → 公告 → 预下载"链条要用）。

## 边界

本模块**零界面依赖**（一次 `requests.get` + 去空白），放在 `services/` 下供
QML 运行时使用 —— QML 运行时**不许 import `ui/`**（`ui/__init__.py` 会拉起
customtkinter）。`ui/dialogs.py` 里的 `fetch_notice` 是本模块的转发，
Tk 界面行为不变。

**requests 必须带 UA**（工作区规范）：本模块**不擅自改动请求语义**
（改 UA 属于行为变更，要单独登记）。
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger("services.notice")

#: 公告地址（净读根域；已随域名迁移更新）
NOTICE_URL = "https://jingdu.org/static/fmcl-notice.txt"

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
