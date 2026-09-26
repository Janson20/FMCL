"""运行时信息桥 —— QML 侧需要知道的"关于这个进程"的只读事实。

放这里的判据很简单：**QML 不该自己去猜环境**。版本号、数据目录、日志路径、
Python/Qt 版本、以及"桥接注册结果"都属于这一类；页面拿它们显示信息或做
能力判断（例如"FluentUI 模块目录不存在"时给一条明确提示，而不是白屏）。

只读 + 无业务规则：所有值在构造时取快照，之后只有 `set_bridge_status()` 会变。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

APP_NAME = "FMCL"


def _app_version() -> str:
    """版本号的单一真相来源是 `services.user_agent`（阶段 1 已统一到那里）。"""
    try:
        from services.user_agent import get_fmcl_version

        return str(get_fmcl_version())
    except Exception:  # noqa: BLE001 - 拿不到版本不该挡住界面
        return "unknown"


class RuntimeBridge(QObject):
    """上下文属性 `Runtime`。"""

    bridgeStatusChanged = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._registered: List[str] = []
        self._missing: List[str] = []
        self._data_dir = ""
        try:
            from config import config

            self._data_dir = str(getattr(config, "base_dir", "") or "")
        except Exception:  # noqa: BLE001
            pass

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
