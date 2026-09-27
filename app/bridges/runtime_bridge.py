"""运行时信息桥 —— QML 侧需要知道的"关于这个进程"的只读事实。

放这里的判据很简单：**QML 不该自己去猜环境**。版本号、数据目录、日志路径、
Python/Qt 版本、以及"桥接注册结果"都属于这一类；页面拿它们显示信息或做
能力判断（例如"FluentUI 模块目录不存在"时给一条明确提示，而不是白屏）。

只读 + 无业务规则：所有值在构造时取快照，之后只有 `set_bridge_status()` 会变。
"""

from __future__ import annotations

import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot
from PySide6.QtGui import QColor

APP_NAME = "FMCL"

logger = logging.getLogger(__name__)

#: 图标名（不含扩展名）：小写字母/数字，用连字符分段（见 qml/assets/icons/README.md）
_ICON_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")


def _app_version() -> str:
    """版本号的单一真相来源是 `services.user_agent`（阶段 1 已统一到那里）。

    **缺陷修复（任务 2.19 的冒烟测试发现）**：这里原先写的是
    `from services.user_agent import get_fmcl_version` —— 那个模块里**没有这个名字**
    （实际叫 `_get_fmcl_version`），ImportError 被下面的 `except` 吞掉后恒返回 `"unknown"`，
    于是窗口标题一直显示 `FMCL unknown`。同类错误之所以能潜伏这么久：
    兜底值本身是合法的（不是空串也不是 None），界面上"看起来正常"。
    测试 `tests/test_main_qml_entry.py::test_runtime_reports_the_real_app_version` 现在钉住它。
    """
    try:
        from services.user_agent import _get_fmcl_version

        version = str(_get_fmcl_version())
    except Exception:  # noqa: BLE001 - 拿不到版本不该挡住界面
        return "unknown"
    # 再兜一层：服务层万一也返回空，不要让标题变成 "FMCL "
    return version or "unknown"


class RuntimeBridge(QObject):
    """上下文属性 `Runtime`。"""

    bridgeStatusChanged = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._registered: List[str] = []
        self._missing: List[str] = []
        self._data_dir = ""
        #: 装配方注入的引擎（`register_bridges()` 会调 `use_engine`）——
        #: 用来判断图标上色 provider 到底注没注到这个引擎上（见 `iconUrl`）。
        self._engine: Any = None
        try:
            from config import config

            self._data_dir = str(getattr(config, "base_dir", "") or "")
        except Exception:  # noqa: BLE001
            pass

    def use_engine(self, engine: Any) -> None:
        """由装配方显式注入 QML 引擎（与 `ThemeBridge.use_engine` 同一约定）。"""
        if engine is not None:
            self._engine = engine

    # ─── 只读属性 ────────────────────────────────────────────

    @Property(str, constant=True)
    def appName(self) -> str:  # noqa: N802 - QML 属性名
        return APP_NAME

    @Property(str, constant=True)
    def appVersion(self) -> str:  # noqa: N802
        return _app_version()

    @Property(str, constant=True)
    def pythonVersion(self) -> str:  # noqa: N802
        return sys.version.split()[0]

    @Property(str, constant=True)
    def qtVersion(self) -> str:  # noqa: N802
        from PySide6.QtCore import qVersion

        return str(qVersion())

    @Property(str, constant=True)
    def dataDir(self) -> str:  # noqa: N802
        return self._data_dir

    @Property(str, constant=True)
    def qmlSourcePath(self) -> str:  # noqa: N802
        """当前 `qml/` 目录（开发态是仓库目录，打包后是解包目录）。"""
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return str(Path(meipass) / "app_qml")
        return str(Path(__file__).resolve().parents[2] / "qml")

    @Property(str, constant=True)
    def fluentModulePath(self) -> str:  # noqa: N802
        meipass = getattr(sys, "_MEIPASS", None)
        base = Path(meipass) / "qml" if meipass else Path(__file__).resolve().parents[2] / "third_party" / "_install" / "qml"
        return str(base)

    @Property(bool, constant=True)
    def fluentModuleAvailable(self) -> bool:  # noqa: N802
        return (Path(self.fluentModulePath) / "FluentUI").is_dir()

    # ─── 图标资源（阶段 2 任务 2.10：界面禁止 emoji，一律用 qml/assets/icons/*.svg） ──

    def _default_icon_color(self) -> str:
        """默认图标颜色：主题的二级文字色（缺陷 D-141）。

        以前的默认是**不传颜色**，而 `IconImageProvider.normalize_color("")` 返回
        `#000000`；93 个图标资源全都写着 `fill="currentColor"`，而 QtSvg 把
        `currentColor` 解析成**不透明黑** —— 于是深色主题下导航/顶栏的图标几乎看不见
        （用户截图里就是这样）。默认值改成主题色之后，40 多处
        `Runtime.iconUrl("xxx")` 调用点一行都不用改就恢复了可见性。
        """
        try:
            from services import palette

            from app.bridges.icon_provider import normalize_color

            colors = getattr(palette, "COLORS", None) or {}
            return normalize_color(colors.get("text_secondary")) or "#000000"
        except Exception as e:  # noqa: BLE001 - 读不到主题就退回老行为（黑图标，但仍可见）
            logger.debug("取默认图标颜色失败（%s），退回黑色", e)
            return "#000000"

    def _color_text(self, color: Any) -> str:
        """把 `QColor` / 字符串折成 `#rrggbb` / `#aarrggbb`（空值返回空串）。

        给 Python 侧的调用方与测试用；QML 侧要指定颜色时请用 `FmIcon { color: … }`
        （它自己拼 provider URL），不要指望本桥的槽带第二个参数 —— QML 的槽重载
        解析顺序不由我们决定，多一个同名重载就多一条"静默走错分支"的路径。
        """
        from app.bridges.icon_provider import normalize_color

        if color is None:
            return ""
        if isinstance(color, QColor):
            return normalize_color(color.name(QColor.NameFormat.HexArgb))
        return normalize_color(str(color))

    @Slot(str, result=str)
    def iconUrl(self, name: str) -> str:  # noqa: N802 - QML 槽名
        """图标名 → 可直接给 `Image.source` 用的 URL（**默认按主题上色**）。

        图标不存在时返回空串并打一条 warning（`Image` 拿到空串只是不画，不会崩）——
        QML 侧的约定是 `Image { source: Runtime.iconUrl("check") }`，
        而不是自己拼路径（拼路径在打包态会错）。
        命名规则见 `qml/assets/icons/README.md`：小写加连字符，不带目录与扩展名也行。

        两种返回形态（缺陷 D-141 的落地）：

        * **上色 provider 已注册到本桥拿到的引擎**（`main_qml.assemble()` 的正常路径）→
          `image://fmcl-icon/<name>?color=%23RRGGBB`，颜色取主题的二级文字色；
        * **没拿到引擎 / provider 没注册**（只装配半个引擎的测试与证据脚本）→
          老行为的 `file:///…/icons/<name>.svg`：能看见，但是黑的
          （`FmIcon` 会据此把 `fallbackUsed` 置真并报警，不会被静默接受）。
        """
        return self._icon_url(name, None)

    def _icon_url(self, name: str, color: Any) -> str:
        """`iconUrl` 的实现：校验名字 → 选上色 URL 或退化 URL。"""
        # 先剥掉可选的 `.svg` 后缀，再**整名**匹配命名规则：
        # 不能先取 `Path(name).stem` —— `"nested/check"` 会被它悄悄变成 `check`，
        # 目录分隔符就被无声吞掉了（实测踩过，见 tests/test_icon_set.py 的负例）。
        raw = str(name).strip()
        if raw.endswith(".svg"):
            raw = raw[:-4]
        if not _ICON_NAME_RE.match(raw):
            logger.warning("图标名不合法：%r（只允许小写字母、数字与连字符，可带 .svg 后缀）", name)
            return ""
        path = Path(self.qmlSourcePath) / "assets" / "icons" / f"{raw}.svg"
        if not path.is_file():
            logger.warning("图标不存在：%s", path)
            return ""
        if self._provider_url_available():
            from app.bridges.icon_provider import icon_url

            return icon_url(raw, self._color_text(color) or self._default_icon_color())
        from PySide6.QtCore import QUrl

        return QUrl.fromLocalFile(str(path)).toString()

    def _provider_url_available(self) -> bool:
        """上色 provider 是否已注册到本桥拿到的那台引擎上。

        判据**具体到引擎**：`addImageProvider` 是挂在某一台引擎上的，另一台引擎解析
        `image://fmcl-icon/...` 只会得到一张加载失败的图，还带一条 Qt 报错。
        没拿到引擎时**一定**返回 False（退回文件 URL）—— 独立进程里的测试与证据脚本
        因此拿到的是确定的老行为，不会被"同进程里别的引擎装过 provider"带偏。
        """
        if self._engine is None:
            return False
        try:
            from app.bridges import icon_provider

            return icon_provider.installed(self._engine) is not None
        except Exception as e:  # noqa: BLE001 - 判断失败按"没有 provider"处理（老行为）
            logger.debug("判断图标 provider 是否可用失败：%s", e)
            return False

    # ─── 桥接状态（装配期写入，QML 据此给出人话提示） ──────────

    @Property(str, notify=bridgeStatusChanged)
    def bridgeStatus(self) -> str:  # noqa: N802
        if not self._missing:
            return f"bridges ok ({len(self._registered)})"
        return "bridges missing: " + ", ".join(self._missing)

    @Property(list, notify=bridgeStatusChanged)
    def missingBridges(self) -> List[str]:  # noqa: N802
        return list(self._missing)

    @Slot("QVariantMap")
    def set_bridge_status(self, result: Dict[str, Any]) -> None:
        """由入口在注册完成后调用（`{"registered": [...], "missing": [...]}`）。"""
        self._registered = list(result.get("registered", []))
        self._missing = list(result.get("missing", []))
        self.bridgeStatusChanged.emit()

    @Slot(result="QVariantMap")
    def describe(self) -> Dict[str, Any]:
        return {
            "app": f"{APP_NAME} {self.appVersion}",
            "python": self.pythonVersion,
            "qt": self.qtVersion,
            "data_dir": self._data_dir,
            "qml": self.qmlSourcePath,
            "fluent": self.fluentModuleAvailable,
            "registered": list(self._registered),
            "missing": list(self._missing),
            "frozen": hasattr(sys, "_MEIPASS") or bool(getattr(sys, "frozen", False)),
            "platform": os.name,
        }


__all__ = ["RuntimeBridge"]
