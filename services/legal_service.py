"""法务文本服务 —— 读 `TERMS_OF_USE.md`（用户协议全文）。

## 为什么需要它（对应用户 3.1 验收报的缺陷 D-162）

旧界面的"使用条款与隐私协议"弹窗（`ui/app_handlers.py:747-861`）把
**`TERMS_OF_USE.md` 全文**（110 行 / 16 KB，12 节）渲染进弹窗；阶段 2 的 QML 版弹窗
（`qml/StartupDialogs.qml`）当时只放了语言文件里那段**摘要**（`terms_content`），
全文渲染被登记成"留给 3.26"。人工验收判定这是**功能丢失**（红线 1），所以提前落到这里。

## 为什么是独立模块而不是塞进 `app/startup.py`

* 读文件 + 定路径是**业务/数据**的事，不是"装配"的事（红线 2：业务逻辑在 services）；
* 3.26 的"关于 / 链接 / 协议"页要同一份文本，放在这里两边都用同一份实现；
* 与 `services/notice_service.py` 同构：模块级函数、`requests`/文件读取都在这一层，
  界面侧只拿字符串。

## 与旧实现的关系

路径规则与 `ui/app_about._get_terms_md_path()` **逐条一致**（打包态取 `_MEIPASS`，
开发态取仓库根），失败时的语义也与 `_load_terms_md()` 一致：**返回空串**而不是抛异常
（旧实现返回 `_("about_terms_not_found")` 那句提示；界面侧现在用语言文件里的
`terms_content` 摘要兜底，等价且不必让服务持有文案）。
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger("services.legal")

#: 协议文件名（与打包脚本 `scripts/build_plan.py` 收进产物的那一份同名）
TERMS_FILENAME = "TERMS_OF_USE.md"


def terms_path() -> Path:
    """协议文件路径：打包态在 `_MEIPASS`，开发态在仓库根（与旧实现逐字一致）。"""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / TERMS_FILENAME
    return Path(__file__).resolve().parent.parent / TERMS_FILENAME


def load_terms_text(path: Path | None = None) -> str:
    """读协议全文。文件不存在或读不了时返回**空串**（调用方负责兜底文案）。

    Args:
        path: 覆盖路径（测试用）。
    """
    target = path if path is not None else terms_path()
    try:
        if not target.is_file():
            logger.warning("协议文件不存在：%s", target)
            return ""
        return target.read_text(encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - 读不了协议不该让启动失败
        logger.error("读取协议文件失败（%s）：%s", target, e)
        return ""


__all__ = ["TERMS_FILENAME", "load_terms_text", "terms_path"]
