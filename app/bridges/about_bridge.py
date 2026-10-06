"""关于桥 —— QML 侧的 `About` 上下文属性（阶段 3 任务 3.4；对照表 A-08 / J-01~J-03）。

## 它是什么

关于页要四类东西：**版本信息（4 行）、9 个鸣谢项目、赞赏链接、用户协议正文**。
这些都住在 `services/about_service.py` 里（逐字照抄旧代码的常量 + 复用 3.1 的
`legal_service`），桥只负责把它们搬到 QML 能绑的属性上，并把"打开外链"接过去。

## 协议正文为什么给两种

`termsText` 是全文（读得到时），空串表示读不到 —— 页面此时用语言文件里的
`terms_content` 摘要兜底，与 `qml/StartupDialogs.qml` 的处理完全一致
（缺陷 D-162 定的规矩：读不到也不能是一个空白框）。
`termsAvailable` 让页面不用自己判空串。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from services.about_service import AboutService

logger = logging.getLogger("app.bridges.about_bridge")


class AboutBridge(QObject):
    """上下文属性 `About`。"""

    #: 打开外链的结果（参数 = 服务返回的结果字典；失败时页面给一句提示）。
    linkFailed = Signal(str)
    #: 状态条文案（**已翻译**；由页面转给 `Shell.setStatus`）。
    statusMessage = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._service: Optional[AboutService] = None

    # ─── 装配期注入 ─────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        """`main_qml.register_bridges` 在注册前调它：取关于服务。"""
        if context is None:
            return
        try:
            self._service = context.try_get("about")
        except Exception as e:  # noqa: BLE001 - 取不到不该让整页打不开
            logger.warning("关于页取服务失败: %s", e)
            self._service = None

    @Property(bool, constant=True)
    def available(self) -> bool:
        return self._service is not None

    # ─── 版本信息与鸣谢 ─────────────────────────────────────

    @Property(str, constant=True)
    def appName(self) -> str:  # noqa: N802
        return self._service.app_name() if self._service is not None else "FMCL"

    @Property(str, constant=True)
    def appSubtitle(self) -> str:  # noqa: N802
        return self._service.app_subtitle() if self._service is not None else ""

    @Property("QVariantList", constant=True)
    def info(self) -> List[Dict[str, str]]:
        """`[{key, value}]`：4 行系统信息（key 是 i18n 键，译文在 QML 侧取）。"""
        return self._service.info() if self._service is not None else []

    @Property("QVariantList", constant=True)
    def acknowledgments(self) -> List[Dict[str, str]]:
        """9 个鸣谢项目：`{name, url, license_url}`。"""
        return self._service.acknowledgments() if self._service is not None else []

    @Property(str, constant=True)
    def donateUrl(self) -> str:  # noqa: N802
        return self._service.donate_url() if self._service is not None else ""

    # ─── 用户协议（J-01 / J-02） ───────────────────────────

    @Property(str, constant=True)
    def termsText(self) -> str:  # noqa: N802
        """协议全文；**空串表示读不到**（页面用 `terms_content` 摘要兜底）。"""
        return self._service.terms_text() if self._service is not None else ""

    @Property(bool, constant=True)
    def termsAvailable(self) -> bool:  # noqa: N802
        return self._service.terms_available() if self._service is not None else False

    # ─── 外链 ───────────────────────────────────────────────

    @Slot(str, result=bool)
    def openUrl(self, url: str) -> bool:  # noqa: N802
        """用系统默认浏览器打开外链（只放行 http/https，判据在服务层）。"""
        if self._service is None:
            return False
        result = self._service.open_url(url)
        if not result.get("ok"):
            self.linkFailed.emit(str(result.get("error", "")))
            return False
        return True

    @Slot(result=bool)
    def openDonate(self) -> bool:  # noqa: N802
        """打开赞赏链接（旧关于窗口底部那个按钮）。"""
        if self._service is None:
            return False
        return self.openUrl(self._service.donate_url())

    # ─── 自检 ───────────────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        return {
            "available": self._service is not None,
            "info": len(self.info),
            "acknowledgments": len(self.acknowledgments),
            "termsAvailable": self.termsAvailable,
            "termsLength": len(self.termsText),
        }


__all__ = ["AboutBridge"]
