"""关于页服务（阶段 3 任务 3.4；对照表 A-08 / J-01 / J-02 / J-03）。

## 范围（用户 2026-10-06 裁决：关于页归 3.4，不留给 3.26）

| 对照表 | 能力 | 旧实现位置 |
|--------|------|-----------|
| A-08 / J-03 | 关于对话框：版本信息 + 9 个鸣谢项目外链 + 赞赏链接 | `ui/app_handlers.py:1487-1700` |
| J-01 | 用户协议正文渲染（`TERMS_OF_USE.md`） | `ui/app_about.py:151-210` |
| J-02 | 协议深色样式（旧内联 CSS → QML 样式） | `ui/app_about.py:26-122` |

## 这里为什么是"数据"而不是"界面"

9 条鸣谢的名字与两个外链、赞赏链接、4 条系统信息，全是**逐字照抄旧代码的常量**
（`ui/app_handlers.py:1589-1635`、`1550-1555`）。规则是"业务/数据在 services、
桥不带业务"，所以它们落在这里；QML 侧只负责排版与取 i18n 标题。

## 协议正文复用 3.1 的 `legal_service`

`_load_terms_md()` 的路径规则与失败语义在 `services/legal_service.py` 里已经有一份
（3.1 交付、缺陷 D-162 的产物），这里**直接复用**，不另写第二份：
读不到时返回空串，界面用语言文件里的 `terms_content` 摘要兜底
（与 `qml/StartupDialogs.qml` 的兜底完全一致）。

## 与旧实现的差异

* 旧"关于"是**独立 Toplevel 窗口**（`ctk.CTkToplevel` + `grab_set`），500×520 居中；
  新版是设置域里的一条路由（`settings/about`），随信息架构（`02` §4.12 的 L2"关于"）。
* 旧窗口里的图标是 `icon.ico`（读不到时退化成 `\u26cf` 那个字符图标）；
  新版走 `FmIcon` + 图标资源集（闸门 R2 禁 emoji/符号图标）。
"""

from __future__ import annotations

import platform
import sys
import webbrowser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services.base import Service
from services.legal_service import load_terms_text

#: 应用名与副标题（旧关于窗口顶部那两行，`ui/app_handlers.py:1540-1544`）
APP_NAME = "FMCL"
APP_SUBTITLE = "Fusion Minecraft Launcher"

#: i18n 键（界面取译文；服务只给键与值）
KEY_TITLE = "about_title"
KEY_VERSION = "about_version"
KEY_PYTHON = "about_python"
KEY_SYSTEM = "about_system"
KEY_ARCH = "about_arch"
KEY_ACKNOWLEDGMENTS = "about_acknowledgments"
KEY_ADDRESS = "about_address"
KEY_LICENSE_BTN = "about_license_btn"
KEY_LICENSE = "about_license"
KEY_DONATE = "about_donate"
KEY_TERMS_MISSING = "about_terms_not_found"

#: 打包态读协议文件的目录变量（与 `legal_service.terms_path()` 同一套规则；
#: 这里也暴露一份"用户协议"页的标题键）
KEY_TERMS_TITLE = "about_title"

#: 赞赏链接（旧 `_show_about` 的 `webbrowser.open("https://ifdian.net/a/janson20")`）
DONATE_URL = "https://ifdian.net/a/janson20"

#: 9 个鸣谢项目（**逐字**来自 `ui/app_handlers.py:1589-1635`，顺序也不动）
ACKNOWLEDGMENTS: List[Dict[str, str]] = [
    {
        "name": "PCL-CE",
        "url": "https://github.com/PCL-community/PCL-CE",
        "license_url": "https://github.com/PCL-Community/PCL-CE/blob/dev/LICENSE",
    },
    {
        "name": "HMCL",
        "url": "https://github.com/HMCL-dev/HMCL",
        "license_url": "https://github.com/HMCL-dev/HMCL/blob/main/LICENSE",
    },
    {
        "name": "opencode",
        "url": "https://github.com/anomalyco/opencode",
        "license_url": "https://github.com/anomalyco/opencode/blob/dev/LICENSE",
    },
    {
        "name": "minecraft-launcher-lib",
        "url": "https://github.com/JakobDev/minecraft-launcher-lib",
        "license_url": "https://github.com/JakobDev/minecraft-launcher-lib/blob/master/LICENSE",
    },
    {
        "name": "forgePY",
        "url": "https://github.com/matejmajny/forgePY",
        "license_url": "https://github.com/matejmajny/forgePY/blob/main/LICENSE",
    },
    {
        "name": "lx-music-desktop",
        "url": "https://github.com/lyswhut/lx-music-desktop",
        "license_url": "https://github.com/lyswhut/lx-music-desktop/blob/master/LICENSE",
    },
    {
        "name": "auto-mod-classifier",
        "url": "https://github.com/qk-yiyihehe/auto-mod-classifier",
        "license_url": "https://github.com/qk-yiyihehe/auto-mod-classifier/blob/main/LICENSE",
    },
    {
        "name": "CapsWriter-Offline",
        "url": "https://github.com/HaujetZhao/CapsWriter-Offline",
        "license_url": "https://github.com/HaujetZhao/CapsWriter-Offline/blob/master/LICENSE",
    },
    {
        "name": "BedrockBoot",
        "url": "https://github.com/Round-Studio/BedrockBoot",
        "license_url": "https://github.com/Round-Studio/BedrockBoot/blob/2.0-develop/LICENSE",
    },
]


def app_version() -> str:
    """FMCL 版本号（`services/user_agent._get_fmcl_version` 同一个取值路径）。"""
    try:
        from services.user_agent import _get_fmcl_version

        return str(_get_fmcl_version())
    except Exception:  # noqa: BLE001 - 版本号读不到不该让关于页崩掉
        return "unknown"


def version_info() -> List[Dict[str, str]]:
    """关于页顶部那 4 行系统信息（旧 `info_items` 的键与取值，顺序不动）。

    旧实现：`about_version` / `about_python` / `about_system` / `about_arch`，
    取值分别是版本号、`platform.python_version()`、`f"{system} {release}"`、`machine()`。
    """
    return [
        {"key": KEY_VERSION, "value": app_version()},
        {"key": KEY_PYTHON, "value": platform.python_version()},
        {"key": KEY_SYSTEM, "value": f"{platform.system()} {platform.release()}"},
        {"key": KEY_ARCH, "value": platform.machine()},
    ]


def acknowledgments() -> List[Dict[str, str]]:
    """鸣谢列表的**副本**（防止调用方就地改动模块级常量）。"""
    return [dict(item) for item in ACKNOWLEDGMENTS]


class AboutService(Service):
    """关于页的数据出口（版本信息 / 鸣谢 / 赞赏 / 协议正文 / 外链）。"""

    name = "about"
    label = "关于"

    def __init__(
        self,
        context: Any = None,
        *,
        opener: Optional[Callable[[str], Any]] = None,
        terms_path: Optional[Path] = None,
    ) -> None:
        """
        Args:
            opener: 打开外链的实现（默认 `webbrowser.open`）。测试注入。
            terms_path: 覆盖协议文件路径（测试用）。
        """
        super().__init__(context)
        self._opener = opener
        self._terms_path = terms_path

    # ─── 数据 ───────────────────────────────────────────────

    def app_name(self) -> str:
        return APP_NAME

    def app_subtitle(self) -> str:
        return APP_SUBTITLE

    def info(self) -> List[Dict[str, str]]:
        return version_info()

    def acknowledgments(self) -> List[Dict[str, str]]:
        return acknowledgments()

    def donate_url(self) -> str:
        return DONATE_URL

    def terms_text(self) -> str:
        """用户协议全文（读不到返回空串；界面用 `terms_content` 摘要兜底）。"""
        return load_terms_text(self._terms_path)

    def terms_available(self) -> bool:
        return bool(self.terms_text())

    # ─── 外链 ───────────────────────────────────────────────

    def open_url(self, url: str) -> Dict[str, Any]:
        """用系统默认浏览器打开外链。返回 `{"ok", "url", "error"}`。

        只放行 http/https：`webbrowser.open` 对 `file://` 之类的协议会交给系统处理，
        而"从数据里取来的 URL"不该有打开任意本地路径的能力（调用方全是常量，
        这条是防御性判据，同时也是测试能验的边界）。
        """
        target = str(url or "").strip()
        result = {"ok": False, "url": target, "error": ""}
        if not target.lower().startswith(("http://", "https://")):
            result["error"] = "unsupported_scheme"
            return result
        opener = self._opener or webbrowser.open
        try:
            opener(target)
        except Exception as e:  # noqa: BLE001 - 打不开浏览器只报错，不抛给界面
            self.log.warning("打开外链失败（%s）：%s", target, e)
            result["error"] = str(e)
            return result
        result["ok"] = True
        return result

    # ─── 自检 ───────────────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        info = super().describe()
        info.update(
            {
                "version": app_version(),
                "acknowledgments": len(ACKNOWLEDGMENTS),
                "terms_available": self.terms_available(),
                "frozen": bool(getattr(sys, "frozen", False)),
            }
        )
        return info


__all__ = [
    "ACKNOWLEDGMENTS",
    "APP_NAME",
    "APP_SUBTITLE",
    "DONATE_URL",
    "AboutService",
    "acknowledgments",
    "app_version",
    "version_info",
]
