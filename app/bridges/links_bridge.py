"""相关网站桥 —— QML 侧的 `Links` 上下文属性（阶段 3 任务 3.26 / J-04）。

## 它是什么

「工具箱 → 相关网站」页要两样东西：29 条站点（**名称/描述/标签的键**，页面用
`Tr.map[key]` 取当前语言）与「打开 / 复制」两个动作。数据在
`services/links_service.py`（逐条照抄旧实现），本桥只做搬运、外链白名单与剪贴板。

## 为什么白名单在这个桥里

与 `AboutService.open_url` 同一条口径：只放行 http/https。URL 来自服务层常量，
这条是防御性判据 —— 不让"数据里的字符串"获得打开本地路径的能力。

## 线程约定

`openUrl` / `copyLink` 都由 QML 在主线程调用（QDesktopServices 与剪贴板都要求
主线程），桥内不起线程、不碰 worker。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication

from services.i18n_service import _
from services.links_service import LinksService

logger = logging.getLogger("app.bridges.links_bridge")


class LinksBridge(QObject):
    """上下文属性 `Links`。"""

    #: 状态条文案（**已翻译**；由页面转给 `Shell.setStatus`）。
    statusMessage = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._service: Optional[LinksService] = None

    # ─── 装配期注入 ─────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        """`main_qml.register_bridges` 在注册前调它：取相关网站服务。"""
        if context is None:
            return
        try:
            self._service = context.try_get("links")
        except Exception as e:  # noqa: BLE001 - 取不到不该让页面打不开
            logger.warning("相关网站取服务失败: %s", e)
            self._service = None

    @Property(bool, constant=True)
    def available(self) -> bool:
        return self._service is not None

    @Property("QVariantList", constant=True)
    def sites(self) -> List[Dict[str, Any]]:
        """29 条站点：`{id, name_key, desc_key, tag_keys, url}`。"""
        return self._service.sites() if self._service is not None else []

    # ─── 打开 / 复制 ───────────────────────────────────────

    @Slot(str, result=bool)
    def openUrl(self, url: str) -> bool:  # noqa: N802
        """用系统默认浏览器打开外链（只放行 http/https，与关于页同一口径）。"""
        target = str(url or "").strip()
        if not target.lower().startswith(("http://", "https://")):
            self.statusMessage.emit(_("link_open_failed"), "error")
            return False
        try:
            opened = bool(QDesktopServices.openUrl(QUrl(target)))
        except Exception as e:  # noqa: BLE001 - 打不开浏览器只报错，不抛给界面
            logger.warning("打开外链失败（%s）：%s", target, e)
            opened = False
        if not opened:
            self.statusMessage.emit(_("link_open_failed"), "error")
        return opened

    @Slot(str, str, result=bool)
    def copyLink(self, name: str, url: str) -> bool:  # noqa: N802
        """复制链接到剪贴板；成功/失败都发状态条（旧实现同语义）。"""
        value = str(url or "").strip()
        label = str(name or "").strip() or value
        if not value:
            self.statusMessage.emit(_("copy_failed", error="empty_url"), "error")
            return False
        try:
            clipboard = QGuiApplication.clipboard()
            if clipboard is None:
                raise RuntimeError("clipboard_unavailable")
            clipboard.setText(value)
        except Exception as e:  # noqa: BLE001 - 剪贴板失败也要让用户看见
            self.statusMessage.emit(_("copy_failed", error=str(e) or type(e).__name__), "error")
            return False
        self.statusMessage.emit(_("link_copied", name=label), "success")
        return True

    # ─── 自检 ───────────────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        return {"available": self.available, "sites": len(self.sites)}


__all__ = ["LinksBridge"]
