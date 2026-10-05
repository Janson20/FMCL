"""首次启动选语言（A-27）的 QML 探针 —— 子进程跑真引擎。

## 它验证什么

1. **链条入口**：`Startup._check_agreement()` 在"还没选过语言"时发 `languageRequired`，
   浮层弹出来、协议**没有**同时弹（先问语言再给条款）；
2. **选项来自 `Tr.availableLanguages`**：四种语言各一行，当前语言预选中；
3. **真的能选**：点 `en_US` 那一行 → 点"确定" → `Tr.language` 变成 `en_US`、
   `config.language` 落盘、`config.language_chosen` 置真、浮层收起、链条继续到协议；
4. 全程 **0 条 QML 报错**。

## 为什么用假配置

`AppContext.config` 可以注入 —— 探针注入一份内存配置，**不碰真实的 `config.json`**
（否则跑一次测试就把用户的界面语言改了）。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QEvent, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import main_qml  # noqa: E402
from app.bootstrap import build_context  # noqa: E402
from app.bridges.icon_provider import install as install_icon_provider  # noqa: E402
from app.startup import StartupController  # noqa: E402

MARKER = "PROBE_JSON:"
QML_ERROR_MARKERS = ("is not defined", "TypeError", "Unable to assign", "Cannot assign", "ReferenceError")
BENIGN_MARKERS = ("This plugin does not support", "QFontDatabase", "propagateSizeHints", "Failed to create")


class FakeConfig:
    """内存配置：只放这一步会读写的字段。"""

    def __init__(self) -> None:
        self.language = "zh_CN"
        self.language_chosen = False
        self.terms_consent = False
        self.ai_privacy_consent = False
        self.auto_check_update = False
        self.saves = 0

    def save_config(self) -> bool:
        self.saves += 1
        return True


APP = QGuiApplication.instance() or QGuiApplication([])
SINK = main_qml.install_message_handler()
CONFIG = FakeConfig()

# `TrBridge` 是**无参**构造的（`register_bridges` 只按 "模块:类名" 造它），
# 它自己去找"进程级唯一配置"（缺陷 D-160 的修法）。探针要注入**内存配置**，
# 就必须在注册桥之前把那个查找换成假的 —— 否则 `Tr.setLanguage()` 会去写真实的
# `config.json`（这条是实测踩到的：探针跑一次就把用户的配置文件改了 mtime）。
from app.bridges import tr_bridge as _tr_bridge  # noqa: E402

_tr_bridge._root_config = lambda: CONFIG  # type: ignore[assignment]

CONTEXT = build_context(config=CONFIG, register=True, set_current=False)
ENGINE = main_qml.build_engine()
install_icon_provider(ENGINE)
main_qml.register_bridges(ENGINE, CONTEXT)

STARTUP = StartupController(
    CONTEXT,
    ui_port=None,
    launcher_wiring=lambda _launcher: None,
    update_checker=lambda: None,
    notice_fetcher=lambda: None,
    predownload_runner=lambda: None,
    terms_loader=lambda: "",
    language_required=lambda: True,   # 本探针模拟"从没选过"
)
ENGINE.rootContext().setContextProperty("Startup", STARTUP)
ENGINE._fmcl_bridges["Startup"] = STARTUP
TR = ENGINE._fmcl_bridges["Tr"]

AGREEMENTS: List[int] = []
STARTUP.agreementRequired.connect(lambda: AGREEMENTS.append(1))

ENGINE.load(QUrl.fromLocalFile(str(main_qml.app_qml_path("App.qml"))))
ROOT = ENGINE.rootObjects()[0] if ENGINE.rootObjects() else None
QTest.qWait(200)


def walk(root_item: Any) -> Any:
    for child in root_item.childItems():
        yield child
        yield from walk(child)


def item(name: str) -> Any:
    if ROOT is None:
        return None
    for node in walk(ROOT.contentItem()):
        if node.objectName() == name:
            return node
    return None


def click(target: Any) -> bool:
    """按真实坐标投递 press + release（与 `tests/qml_home_probe.py` 同一套手法）。"""
    if target is None:
        return False
    centre = target.mapToScene(QPointF(target.width() / 2, target.height() / 2))
    global_pos = ROOT.mapToGlobal(centre)
    for event_type, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton),
                                (QEvent.MouseButtonRelease, Qt.NoButton)):
        APP.sendEvent(ROOT, QMouseEvent(event_type, centre, global_pos, Qt.LeftButton, buttons, Qt.NoModifier))
        QTest.qWait(40)
    return True


def visible(name: str) -> bool:
    node = item(name)
    return bool(node is not None and node.property("visible"))


def qml_errors() -> List[str]:
    return [m for m in SINK.messages
            if not any(marker in m for marker in BENIGN_MARKERS)
            and any(marker in m for marker in QML_ERROR_MARKERS)]


def main() -> int:
    report: Dict[str, Any] = {}

    report["overlay_before"] = visible("languageOverlay")
    # 链条入口：没选过语言 → 发 languageRequired（生产由启动流程的定时器触发）
    STARTUP._check_agreement()
    QTest.qWait(150)

    report["overlay_after"] = visible("languageOverlay")
    report["agreement_opened_before_choice"] = bool(AGREEMENTS)
    report["options"] = sorted(
        node.objectName() for node in walk(ROOT.contentItem())
        if node.objectName().startswith("languageOption_")
    )
    report["preselected"] = [
        node.objectName() for node in walk(ROOT.contentItem())
        if node.objectName().startswith("languageOption_") and node.property("checked")
    ]
    report["confirm_text"] = str((item("languageConfirmButton") or ROOT).property("text") or "")

    # 真的点一下 en_US，再点"确定"
    report["clicked_option"] = click(item("languageOption_en_US"))
    QTest.qWait(80)
    report["checked_after_click"] = sorted(
        node.objectName() for node in walk(ROOT.contentItem())
        if node.objectName().startswith("languageOption_") and node.property("checked")
    )
    report["clicked_confirm"] = click(item("languageConfirmButton"))
    QTest.qWait(150)

    report["language_after"] = str(TR.property("language"))
    report["config_language"] = CONFIG.language
    report["config_chosen"] = CONFIG.language_chosen
    report["config_saves"] = CONFIG.saves
    report["overlay_final"] = visible("languageOverlay")
    report["agreement_after_choice"] = bool(AGREEMENTS)
    report["qml_errors"] = qml_errors()
    print(MARKER + json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
