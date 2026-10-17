"""阶段 3.6 QML 判据脚本 —— 「我的资源」页（精简档：一个脚本跑完所有判据）。

用法::

    .venv\\Scripts\\python.exe -X utf8 tests\\qml_resources_probe.py            # 全部判据
    .venv\\Scripts\\python.exe -X utf8 tests\\qml_resources_probe.py --shots     # 顺带截图到 docs/refactor/evidence/3.6

## 加载方式（与 3.4 / 3.5 已验收的探针同一条路）

``build_engine()`` → ``install_icon_provider()`` → ``register_bridges(..., prebuilt={"Resources": 假桥})``
→ ``ENGINE.load(App.qml)`` → ``Nav.push("resources/mods")``。

**不能**直接 ``engine.load(ResourcesPage.qml)``：这一页不是顶层窗口（根是 ``ColumnLayout``，
里面才是一个 ``FmPage``），它是 ``qml/shell/PageStack.qml`` 的 ``pushFrame()`` 用
``component.createObject(null)`` 建出来再 ``StackView.push()`` 的。脱离页面栈时根节点的
``width: parent ? parent.width : 0`` 求值为 0、``ListView`` 高度也是 0，委托一个都不会实例化
（判据会成片假失败）。走 ``App.qml`` + ``Nav.push`` 之后页面拿到的是 ``App.qml`` 那个
1280x840 窗口里的真实尺寸 —— 与 ``tests/qml_settings_probe.py:297``、
``tests/qml_account_probe.py:314``、``tests/visual_probe.py:234`` 完全同款。

``Resources`` 是**假桥** ``FakeResources``（属性/槽名与 ``app/bridges/resources_bridge.py``
一一对应；**故意不 import 真桥** —— 真桥要服务与后台任务，本脚本判的是"页面接得对不对"）。
2026-10-06 与真桥对齐过一次：页面把"收文件"统一成 ``Resources.takeFiles(urls, drop)``，
假桥补上同名槽（``dropFiles`` / ``submitFiles`` 留着，真桥里也还有）。
``Shell`` / ``Dialogs`` / ``Tr`` 用**真桥**（它们本来就在 ``App.qml`` 的上下文里）：
判据直接读 ``Shell.statusText`` / ``Dialogs.toasts`` / ``Tr.map``，不另造替身。

## 与首版（猜写法）的差异

首版有一半的 QML 调用是猜的，下面每条都换成了本仓库里已经跑过的写法：

| 首版（猜） | 现在 | 依据 |
|---|---|---|
| ``engine.load(ResourcesPage.qml)`` + ``rootObjects()`` | ``App.qml`` + ``Nav.push("resources/mods")`` | ``tests/qml_settings_probe.py`` / ``qml_account_probe.py`` |
| ``btn.clicked.emit()`` / ``pager.pageRequested.emit(2)`` | 真鼠标点击 ``click()``（按下 + 抬起，坐标投到窗口） | ``tests/qml_settings_probe.py:398`` |
| ``QMetaObject.invokeMethod(dialog, "checkedIds")`` 取返回值 | 点「更新选中」→ 断言假桥收到的 modid 列表 | PySide6 的 ``invokeMethod`` **拿不到返回值**（``Q_RETURN_ARG`` 只吃一个参数），首版那条永远拿到 None |
| ``page.property("contentState")`` | 读内层 ``resourcesBody``（``FmPage``）的 ``contentState`` —— 根节点是 ``ColumnLayout``，**没有**这个属性 | ``qml/components/FmPage.qml:57`` |
| ``resourceDescLabel`` 的文本与 i18n **键名**比对 | 与真 ``Tr.map[键]`` 的**译文**比对 | 页面写的是 ``root.t(键)``，渲染出来是译文不是键名 |
| 自造 ``_Shell`` / ``_Dialogs`` 假上下文属性 | 真桥：``Shell.statusText`` / ``Shell.statusLevel`` / ``Dialogs.toasts`` | ``tests/qml_account_probe.py:502`` |
| ``page.grabWindow()``（``ColumnLayout`` 上没有这个方法） | ``ROOT.grabWindow()`` 落盘 | ``tests/visual_probe.py:255``、``qml_settings_probe.py:436`` |
| 拖拽判据断言"页面把本地路径交给桥的 `dropFiles`" | 断言 `Resources.takeFiles(urls, drop=True)`（2026-10-06 页面与真桥都改成这个入口，url→本地路径在桥里折算） | ``qml/pages/resources/ResourcesPage.qml:570``、``app/bridges/resources_bridge.py:671`` |

**没有**为了好写而删掉判据，也**没有**为了让判据变绿而改页面语义。

## 判据结构

``Report.check(ok, 标签, 详情)`` 那一套 + 14 个小节（编号与首版一致）：
加载期零警告 / 版本选择器 / 四个页签 / 模组卡片 / 资源行 / 分页 / 三态 /
元数据进度 / 拖拽 / 更新对话框 / 确认删除 / 动作按钮 / 状态栏与 Toast / 截图。

页面**建不出来**时不再逐条刷红：只报一条失败，详情里给组件级错误原文
（``QQmlComponent.errors()`` —— ``PageStack`` 自己只留一行 ``lastError``，看不到全部）。

零 QML 警告走两份来源：``engine.warnings`` 信号（``QML_DISABLE_DISK_CACHE=1``，磁盘缓存会
掩盖告警）与 ``main_qml.install_message_handler()`` 的消息收集器（3.4/3.5 探针用的后者）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
#: 缓存会让编译单元**异步**取回，掩盖加载期的绑定告警（`main_qml.py:42-55` 的 D-150）。
os.environ.setdefault("QML_DISABLE_DISK_CACHE", "1")

from config_isolation import isolate_config_writes  # noqa: E402
from PySide6.QtCore import (  # noqa: E402
    Q_ARG,
    Property,
    QBuffer,
    QEvent,
    QIODevice,
    QMetaObject,
    QObject,
    QPointF,
    Qt,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QGuiApplication, QImage, QMouseEvent  # noqa: E402
from PySide6.QtQml import QQmlComponent  # noqa: E402

#: 必须在任何 QML 对象之前 import：否则 QQuickWindow 不在类型表里，`grabWindow()` 会 AttributeError
from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: E402,F401
from PySide6.QtTest import QTest  # noqa: E402

import main_qml  # noqa: E402
from app.bootstrap import build_context  # noqa: E402
from app.bridges.icon_provider import install as install_icon_provider  # noqa: E402
from app.bridges.thumbnail_provider import install as install_thumbnail_provider  # noqa: E402

PAGE = REPO_ROOT / "qml" / "pages" / "resources" / "ResourcesPage.qml"
SHOTS_DIR = REPO_ROOT / "docs" / "refactor" / "evidence" / "3.6"
#: 进页面的路由（与版本页齿轮、左侧一级导航同一个入口；页面按 `Resources.tab` 决定看哪一类）
ROUTE = "resources/mods"
#: 状态块的根对象名（与 `tests/visual_probe.py:STATE_PARTS` 同一份）
STATE_PARTS = {"loading": "fmLoadingState", "empty": "fmEmptyState", "error": "fmErrorState"}

TAB_KEYS = ("mods", "resourcepacks", "saves", "shaderpacks")
TAB_LABEL_KEYS = {
    "mods": "resource_mods",
    "resourcepacks": "resource_packs",
    "saves": "resource_maps",
    "shaderpacks": "resource_shaders",
}
TAB_DESC_KEYS = {
    "mods": "rm_mods_desc",
    "resourcepacks": "rm_resourcepacks_desc",
    "saves": "rm_saves_desc",
    "shaderpacks": "rm_shaderpacks_desc",
}

#: 环境噪声（不是 QML 的问题）：缺字体目录、offscreen 插件的尺寸提示
BENIGN_MARKERS = (
    "QFontDatabase",
    "QFont::setPointSize",
    "This plugin does not support",
    "propagateSizeHints",
)


def _as_urls(items: Any) -> List[str]:
    """把桥收到的 url 列表折成可比对的字符串（QML 的 `list<url>` 过来是 QUrl 或 str）。"""
    out: List[str] = []
    for item in items or []:
        out.append(item.toString() if hasattr(item, "toString") else str(item))
    return out


class FakeResources(QObject):
    """只实现页面用到的属性/槽（名字必须与 `ResourcesBridge` 一致）。"""
    itemsChanged = Signal()
    busyChanged = Signal()
    progressChanged = Signal()
    updatesChanged = Signal()
    statusMessage = Signal(str, str)
    noticeRequested = Signal(str, str)
    addFilesRequested = Signal()
    deleteRequested = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self._version = "1.20.4-forge"
        self._tab = "mods"
        self._search = ""
        self._items = []
        self._page = 1
        self._page_count = 1
        self._total = 0
        self._load_state = "empty"
        self._busy = False
        self._progress = {}
        self._updates = []
        self._mod_count = 0
        #: 版本候选（"全局 .minecraft"那一项永远在最前，不在此列）
        self._allowed_versions = ("1.20.4-forge", "1.21")
        #: 当前版本允许哪几个页签（原版只给资源包 / 地图 —— 2026-10-07 用户裁决）
        self._allowed_tabs = list(TAB_KEYS)
        #: 页面调过的槽（判据读它）
        self.calls = []

    # ── 属性（名字与真桥一一对应）──
    @Property(bool, constant=True)
    def available(self) -> bool:
        return True

    @Property(int, notify=itemsChanged)
    def revision(self) -> int:
        return len(self.calls)

    @Property(str, notify=itemsChanged)
    def versionId(self) -> str:  # noqa: N802
        return self._version

    @Property("QVariantList", notify=itemsChanged)
    def versionChoices(self):  # noqa: N802
        """**第一项永远是「全局 .minecraft」**（`value` 为空串）—— 与真桥一致。

        2026-10-07 用户验收反馈：没版本上下文时不该报"未指定游戏版本"，
        而是照旧读全局目录。
        """
        rows = [{"text": "全局 .minecraft", "value": ""}]
        rows.extend(
            {"text": vid, "value": vid}
            for vid in ("1.20.4-forge", "1.21")
            if vid in self._allowed_versions
        )
        return rows

    @Property(bool, notify=itemsChanged)
    def globalScope(self) -> bool:  # noqa: N802
        return not self._version

    @Property(str, constant=True)
    def globalHintKey(self) -> str:  # noqa: N802
        return "resource_scope_global_hint"

    @Property("QVariantList", notify=itemsChanged)
    def allowedTabs(self):  # noqa: N802
        return list(self._allowed_tabs)

    @Property(bool, notify=itemsChanged)
    def hasVersion(self) -> bool:  # noqa: N802
        return bool(self._version)

    @Property(str, notify=itemsChanged)
    def tab(self) -> str:
        return self._tab

    @Property("QVariantList", notify=itemsChanged)
    def tabs(self):  # noqa: N802
        """按 `allowedTabs` 过滤 —— 原版版本没有「模组 / 光影」。"""
        return [
            {"key": k, "labelKey": TAB_LABEL_KEYS[k], "active": k == self._tab}
            for k in TAB_KEYS
            if k in self._allowed_tabs
        ]

    def set_scope(self, version: str, allowed=None) -> None:
        """给判据用的开关：切版本 + 切"这个版本允许哪几个页签"。"""
        self._version = str(version or "")
        self._allowed_tabs = list(allowed if allowed is not None else TAB_KEYS)
        if self._tab not in self._allowed_tabs:
            self._tab = self._allowed_tabs[0]
        self.itemsChanged.emit()

    def set_versions(self, versions) -> None:
        """给判据用的开关：换"已安装版本列表"（空 = 一个版本都没装）。"""
        self._allowed_versions = tuple(versions or ())
        self.itemsChanged.emit()

    @Property(str, notify=itemsChanged)
    def tabLabelKey(self) -> str:  # noqa: N802
        return TAB_LABEL_KEYS[self._tab]

    @Property(str, notify=itemsChanged)
    def tabDescKey(self) -> str:  # noqa: N802
        return TAB_DESC_KEYS[self._tab]

    @Property(str, notify=itemsChanged)
    def searchPlaceholderKey(self) -> str:  # noqa: N802
        return "rm_search_placeholder_" + ("mods" if self._tab == "mods" else self._tab)

    @Property(str, notify=itemsChanged)
    def search(self) -> str:
        return self._search

    @Property("QVariantList", notify=itemsChanged)
    def items(self):  # noqa: N802
        return self._items

    @Property(int, notify=itemsChanged)
    def page(self) -> int:
        return self._page

    @Property(int, notify=itemsChanged)
    def pageCount(self) -> int:  # noqa: N802
        return self._page_count

    @Property(int, notify=itemsChanged)
    def total(self) -> int:
        return self._total

    @Property(str, notify=itemsChanged)
    def loadState(self) -> str:  # noqa: N802
        return self._load_state

    @Property(str, notify=itemsChanged)
    def loadError(self) -> str:  # noqa: N802
        return ""

    @Property(bool, notify=itemsChanged)
    def isMods(self) -> bool:  # noqa: N802
        return self._tab == "mods"

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        return self._busy

    @Property("QVariantMap", notify=progressChanged)
    def progress(self):
        return self._progress

    @Property(bool, notify=progressChanged)
    def progressVisible(self) -> bool:  # noqa: N802
        return bool(self._progress.get("message"))

    @Property(int, notify=itemsChanged)
    def modCount(self) -> int:  # noqa: N802
        return self._mod_count

    @Property("QVariantList", notify=updatesChanged)
    def updates(self):
        return self._updates

    @Property(int, notify=updatesChanged)
    def updatesCount(self) -> int:  # noqa: N802
        return len(self._updates)

    @Property(str, constant=True)
    def noVersionKey(self) -> str:  # noqa: N802
        """2026-10-07 用户反馈后换的键：不再是"未指定游戏版本，无法安装"那句。"""
        return "resource_no_version_hint"

    @Property(bool, notify=itemsChanged)
    def noVersion(self) -> bool:  # noqa: N802
        """一个版本都没装（选择器里只有「全局 .minecraft」）—— 空态标题据此分两种。"""
        return not self._allowed_versions and not self._version

    @Property(str, notify=itemsChanged)
    def emptyFolderKey(self) -> str:  # noqa: N802
        """"这个版本的资源目录是空的"用的键（带 `{label}`）。"""
        return "rm_folder_empty"

    @Property(str, notify=itemsChanged)
    def fileFilter(self) -> str:  # noqa: N802
        return "MOD (*.jar);;All (*.*)"

    # ── 槽（记录调用）──
    @Slot(str)
    def activate(self, version: str) -> None:  # noqa: N802
        self.calls.append(("activate", str(version)))

    @Slot(str)
    def setVersion(self, version: str) -> None:  # noqa: N802
        self.calls.append(("setVersion", str(version)))

    @Slot(str)
    def setTab(self, tab: str) -> None:  # noqa: N802
        self.calls.append(("setTab", str(tab)))

    @Slot(str)
    def setSearch(self, text: str) -> None:  # noqa: N802
        self.calls.append(("setSearch", str(text)))

    @Slot(int)
    def goto(self, page: int) -> None:  # noqa: N802
        self.calls.append(("goto", int(page)))

    @Slot()
    def refresh(self) -> None:  # noqa: N802
        self.calls.append(("refresh",))

    @Slot()
    def retry(self) -> None:  # noqa: N802
        self.calls.append(("retry",))

    @Slot(str, bool)
    def toggleMod(self, path: str, disabled: bool) -> None:  # noqa: N802
        self.calls.append(("toggleMod", str(path), bool(disabled)))

    @Slot(str, str)
    def requestDelete(self, path: str, name: str) -> None:  # noqa: N802
        self.calls.append(("requestDelete", str(path), str(name)))
        self.deleteRequested.emit(str(path), str(name))

    @Slot(str)
    def confirmDelete(self, path: str) -> None:  # noqa: N802
        self.calls.append(("confirmDelete", str(path)))

    @Slot()
    def requestAddFiles(self) -> None:  # noqa: N802
        self.calls.append(("requestAddFiles",))
        self.addFilesRequested.emit()

    @Slot("QVariantList")
    def submitFiles(self, paths) -> None:  # noqa: N802
        self.calls.append(("submitFiles", list(paths or [])))

    @Slot("QVariantList", bool)
    def takeFiles(self, items, drop: bool = False) -> None:  # noqa: N802
        """页面唯一的"收文件"入口（`drop=True` 表示拖进来的）。

        2026-10-06 与真桥对齐：页面已经改成 `Resources.takeFiles(urls, true/false)`，
        url → 本地路径的折算移进了桥（`ResourcesBridge.toLocalPaths`，红线 2）。
        """
        self.calls.append(("takeFiles", _as_urls(items), bool(drop)))

    @Slot("QVariantList")
    def dropFiles(self, paths) -> None:  # noqa: N802
        self.calls.append(("dropFiles", list(paths or [])))

    @Slot()
    def openFolder(self) -> None:  # noqa: N802
        self.calls.append(("openFolder",))

    @Slot()
    def exportList(self) -> None:  # noqa: N802
        self.calls.append(("exportList",))

    @Slot()
    def checkUpdates(self) -> None:  # noqa: N802
        self.calls.append(("checkUpdates",))

    @Slot("QVariantList")
    def applyUpdates(self, modids) -> None:  # noqa: N802
        self.calls.append(("applyUpdates", list(modids or [])))

    @Slot()
    def cancel(self) -> None:  # noqa: N802
        self.calls.append(("cancel",))

    # ── 给判据用的设置口 ──
    def set_state(self, state: str) -> None:
        self._load_state = state
        self.itemsChanged.emit()

    def set_tab(self, tab: str) -> None:
        self._tab = tab
        self.itemsChanged.emit()

    def set_items(self, items) -> None:
        self._items = list(items)
        self._total = len(self._items)
        self._load_state = "ready" if self._items else "empty"
        self.itemsChanged.emit()

    def set_page(self, page: int, count: int) -> None:
        self._page, self._page_count = int(page), int(count)
        self.itemsChanged.emit()

    def set_progress(self, message: str, current: int, total: int) -> None:
        self._progress = {"message": message, "current": current, "total": total}
        self._busy = bool(message)
        self.progressChanged.emit()
        self.busyChanged.emit()

    def set_updates(self, rows) -> None:
        self._updates = list(rows)
        self.updatesChanged.emit()

    def set_mod_count(self, count: int) -> None:
        self._mod_count = int(count)
        self.itemsChanged.emit()


class Report:
    """判据表（与首版同一套：`check(ok, 标签, 详情)` + 末尾统计）。"""

    def __init__(self) -> None:
        self.checks: List[Dict[str, Any]] = []

    def check(self, ok: bool, label: str, detail: str = "") -> bool:
        self.checks.append({"ok": bool(ok), "label": label, "detail": str(detail)})
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {label}" + (f"  -- {detail}" if detail else ""))
        return bool(ok)

    @property
    def failures(self) -> int:
        return sum(1 for c in self.checks if not c["ok"])

    def dump(self, path: Path) -> None:
        path.write_text(json.dumps(self.checks, ensure_ascii=False, indent=2), encoding="utf-8")


class Probe:
    """一次运行的上下文：引擎 / 窗口 / 假桥 / 消息收集，外加找件与点击的工具。

    找件、`visible/enabled/text_of`、`click()`、`shot()` 全部照搬
    `tests/qml_settings_probe.py:323-447` 与 `tests/qml_account_probe.py:396-462`
    （本仓库已验收的两份范式），只把"当前页面"从写死的名字改成按 objectName 找。
    """

    def __init__(self) -> None:
        self.app = QGuiApplication.instance() or QGuiApplication([])
        self.sink = main_qml.install_message_handler()
        #: 生产装配路径会把真配置写盘，先挡住（`tests/config_isolation.py`）
        self.blocked_config_writes = isolate_config_writes("resources-probe")
        self.context = build_context(config=None, register=True, set_current=False)
        self.fake = FakeResources()
        self.engine = main_qml.build_engine()
        install_icon_provider(self.engine)
        #: 缩略图通道：页面里 `Image { source: "image://fmcl-thumb/…" }` 靠它取图
        self.thumbs = install_thumbnail_provider(self.engine)
        #: `prebuilt` 顶掉真 `ResourcesBridge`（假桥只实现属性与槽名）
        self.bridges = main_qml.register_bridges(
            self.engine, self.context, prebuilt={"Resources": self.fake})
        self.root: Any = None
        self.engine_warnings: List[str] = []
        self.warnings_wired = False
        signal = getattr(self.engine, "warnings", None)
        if signal is not None:
            try:
                signal.connect(self._collect_engine_warnings)
                self.warnings_wired = True
            except Exception as exc:  # noqa: BLE001 - 信号接不上不该挡住判据
                print(f"  [note] engine.warnings 接不上：{exc}")
        self.nav = self.engine._fmcl_bridges.get("Nav")
        self.shell = self.engine._fmcl_bridges.get("Shell")
        self.dialogs = self.engine._fmcl_bridges.get("Dialogs")
        self.tr = self.engine._fmcl_bridges.get("Tr")

    # ── 装配 ────────────────────────────────────────────────

    def _collect_engine_warnings(self, items: Any) -> None:
        for item in items or []:
            text = item.toString() if hasattr(item, "toString") else str(item)
            self.engine_warnings.append(text)

    def load(self) -> bool:
        """建 `App.qml` 再 `Nav.push(ROUTE)`（3.4/3.5 探针同一条路径）。返回页面有没有建出来。"""
        self.engine.load(QUrl.fromLocalFile(str(main_qml.app_qml_path("App.qml"))))
        roots = self.engine.rootObjects()
        self.root = roots[0] if roots else None
        if self.root is None:
            return False
        QTest.qWait(300)
        if self.nav is not None:
            self.nav.push(ROUTE)
        self.settle()
        return self.item("resourcesPage") is not None

    def failure_detail(self) -> str:
        """页面没建出来时把原因摊开：组件级错误原文 + PageStack.lastError + 当前路由。"""
        parts: List[str] = []
        try:
            component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(PAGE)))
            if component.status() == QQmlComponent.Error:
                errors = " | ".join(error.toString() for error in component.errors())
                parts.append(f"组件错误[{PAGE.name}]：{errors}")
        except Exception as exc:  # noqa: BLE001 - 诊断本身失败不该盖掉真正的失败
            parts.append(f"编译诊断失败：{exc}")
        stack = self.item("pageStack")
        if stack is not None:
            last_error = str(stack.property("lastError") or "")
            if last_error:
                parts.append(f"PageStack.lastError：{last_error}")
            parts.append(f"PageStack.depth={stack.property('depth')}")
        if self.nav is not None:
            parts.append(f"Nav.currentRoute={self.nav.property('currentRoute')}")
        if self.root is None:
            parts.append("App.qml 没有建出根对象")
        return " / ".join(parts)

    # ── 找件（`tests/qml_settings_probe.py:323-380` 同款）──

    def _matches(self, name: str) -> List[Any]:
        out: List[Any] = []
        if self.root is None:
            return out
        content = self.root.contentItem()
        if content is None:
            return out
        for candidate in _walk(content):
            if candidate.objectName() == name:
                out.append(candidate)
        return out

    def item(self, name: str) -> Any:
        """按 objectName 找项（同名时优先可见的那个）。"""
        found = self._matches(name)
        for candidate in found:
            try:
                if candidate.isVisible():
                    return candidate
            except Exception:  # noqa: BLE001
                continue
        return found[0] if found else None

    def text_of(self, name: str) -> str:
        target = self.item(name)
        if target is None:
            return "<missing>"
        value = target.property("text")
        return "" if value is None else str(value)

    def visible(self, name: str) -> bool:
        target = self.item(name)
        return bool(target is not None and target.property("visible"))

    def enabled(self, name: str) -> bool:
        target = self.item(name)
        return bool(target is not None and target.property("enabled"))

    def page(self) -> Any:
        return self.item("resourcesPage")

    def find_qml_object(self, name: str) -> Any:
        """按 objectName 找 **QObject**（`FileDialog` 这类不是 Item 的东西走 QObject 树）。

        为什么不从根对象找：页面是 `createObject(null)` 建出来再被 StackView 认领的，
        它的 **QObject 父链挂在页面自己那棵树上**，`ROOT.findChildren()` 里没有它
        （实测 `ROOT.findChild(QObject, "resourceAddFilesDialog")` 返回 None，
        `resourcesPage.findChild(...)` 找得到）。所以先从页面里找，再退回根对象。
        """
        for base in (self.page(), self.root):
            if base is None:
                continue
            found = base.findChild(QObject, name)
            if found is not None:
                return found
        return None

    def body(self) -> Any:
        """页面根对象（`contentState` 三态就在它上面）。

        阶段 3.6 收尾时页面从"`Item` 套内层 `FmPage`"改成了**单根 `FmPage`**
        （`resourcesBody` 不复存在）—— 改的原因见页面文件头：冒烟驱动按**根对象**
        切三态，内层 `contentState` 会让它判"这个页面没有 contentState"。
        """
        return self.page()

    # ── 交互 ────────────────────────────────────────────────

    def settle(self, timeout_ms: int = 3000) -> None:
        """等页面拿到真实尺寸（过渡期间页面是 0 宽 0 高，`ListView` 的委托还没实例化）。"""
        waited = 0
        while waited < timeout_ms:
            page = self.page()
            if page is not None and page.width() > 1 and page.height() > 1:
                break
            QTest.qWait(50)
            waited += 50
        QTest.qWait(80)

    def click(self, target: Any) -> None:
        """真鼠标点击（按下 + 抬起，坐标投到窗口）—— `qml_settings_probe.py:398` 同款。"""
        if target is None:
            raise RuntimeError("点击目标不存在")
        self.settle()
        centre = target.mapToScene(QPointF(target.width() / 2, target.height() / 2))
        global_pos = self.root.mapToGlobal(centre)
        for event_type, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton),
                                    (QEvent.MouseButtonRelease, Qt.NoButton)):
            self.app.sendEvent(
                self.root,
                QMouseEvent(event_type, centre, global_pos, Qt.LeftButton, buttons, Qt.NoModifier))
            QTest.qWait(40)

    def click_named(self, name: str) -> bool:
        target = self.item(name)
        if target is None:
            return False
        self.click(target)
        QTest.qWait(60)
        return True

    def child(self, root: Any, name: str) -> Any:
        """在某个子树里按 objectName 找 —— 走**视觉树**（`childItems()` 递归）。

        浮层的按钮全树同名的很多（`okButton` / `cancelButton` 每个 `ConfirmDialog` 都有），
        所以本节一律"先拿到浮层根、再在它里面找"。
        **不用 `findChild(QQuickItem, …)`**：`ListView` 的委托在 QObject 树上的父级挂在
        委托模型那边，不在浮层子树里（实测同一个勾选框 `findChild` 返回 None、视觉树找得到）。
        """
        if root is None:
            return None
        for candidate in _walk(root):
            if candidate.objectName() == name:
                return candidate
        return None

    def click_named_from(self, root: Any, name: str) -> bool:
        """点某个子树里的控件（**先取最新的根**：Loader 一收，旧的包装器就是野指针）。"""
        return self.click_named(name) if root is None else self._click_target(self.child(root, name))

    def _click_target(self, target: Any) -> bool:
        if target is None:
            return False
        self.click(target)
        QTest.qWait(60)
        return True

    def emit_signal(self, target: Any, name: str, value: Any = None) -> bool:
        """触发 QML 自己声明的信号。

        两条都取自本仓库：无参信号直接 `.emit()`（`qml_account_probe.py:912` 的
        `failure_bar.closed.emit()`）；带参信号走 `QMetaObject.invokeMethod`
        （`qml_settings_probe.py:555` 的 `invokeMethod(slider, "moved")`）。
        """
        if target is None:
            return False
        signal = getattr(target, name, None)
        if signal is not None and hasattr(signal, "emit"):
            if value is None:
                signal.emit()
            else:
                signal.emit(value)
            return True
        if value is None:
            return bool(QMetaObject.invokeMethod(target, name, Qt.ConnectionType.DirectConnection))
        return bool(QMetaObject.invokeMethod(
            target, name, Qt.ConnectionType.DirectConnection, Q_ARG("QVariant", value)))

    def wait_until(self, predicate: Any, timeout_ms: int = 4000, step_ms: int = 50) -> bool:
        waited = 0
        while waited < timeout_ms:
            if predicate():
                return True
            QTest.qWait(step_ms)
            waited += step_ms
        return bool(predicate())

    # ── 判据要读的桥侧数据 ──────────────────────────────────

    def calls(self, kind: str) -> List[tuple]:
        return [call for call in self.fake.calls if call[0] == kind]

    def calls_since(self, index: int, kind: str) -> List[tuple]:
        return [call for call in self.fake.calls[index:] if call[0] == kind]

    def tr_map(self) -> Dict[str, str]:
        return dict(self.tr.property("map") or {}) if self.tr is not None else {}

    def translated(self, key: str) -> str:
        """页面的 `root.t(键)` 渲染出来的就是这一份（`qml/pages/resources/ResourcesPage.qml:92`）。"""
        return self.tr_map().get(key, key)

    def toasts(self) -> List[Dict[str, Any]]:
        return list(self.dialogs.property("toasts") or []) if self.dialogs is not None else []

    def register_thumbnail(self, key: str) -> str:
        """给假数据造一张**真图**登记进缩略图通道，返回 `image://fmcl-thumb/<id>`。

        不登记的话 `Image.source` 指向一个不存在的 id，Qt 会刷
        `Failed to get image from provider` —— 那是**假数据**的锅，不是页面的，
        不能让它污染"零 QML 警告"这条判据。
        """
        if self.thumbs is None:
            return ""
        image = QImage(4, 4, QImage.Format.Format_ARGB32)
        image.fill(QColor("#4f8cff"))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        encoded = bytes(buffer.data().toBase64()).decode("ascii")
        return str(self.thumbs.register(key, encoded) or "")

    def shot(self, name: str) -> Optional[Path]:
        """抓一帧整窗落盘（`visual_probe.py:255` 同款：`ROOT.grabWindow()`）。"""
        if self.root is None:
            return None
        SHOTS_DIR.mkdir(parents=True, exist_ok=True)
        image = self.root.grabWindow()
        path = SHOTS_DIR / f"{name}.png"
        if image.isNull() or not image.save(str(path)):
            return None
        return path

    # ── 警告 ────────────────────────────────────────────────

    def sink_messages(self, since: int = 0) -> List[str]:
        """消息收集器里**不是环境噪声**的那批（QML 的 console.log/告警/错误都走这里）。"""
        out: List[str] = []
        for raw in self.sink.messages[since:]:
            text = str(raw)
            if any(marker in text for marker in BENIGN_MARKERS):
                continue
            out.append(text)
        return out

    def all_messages(self) -> List[str]:
        return self.sink_messages(0)

    def warning_report(self, since: int) -> str:
        parts = [f"Qt: {text.splitlines()[0]}" for text in self.sink_messages(since)]
        parts += [f"engine.warnings: {text.splitlines()[0]}" for text in self.engine_warnings]
        return " ;; ".join(parts[:4])


# ─── 测试数据（形状照抄 `resources_bridge.items` 的条目字段）─────────


def mod_row(path: str, display: str, **over: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "name": path.rsplit("/", 1)[-1], "display_name": display, "path": path,
        "isMod": True, "isDir": False, "disabled": False, "author": "", "description": "",
        "modid": "", "version": "", "filename": path.rsplit("/", 1)[-1], "size": "1 KB",
        "icon": "", "thumbnail": "",
    }
    row.update(over)
    return row


def plain_row(path: str, display: str, **over: Any) -> Dict[str, Any]:
    row = mod_row(path, display, **over)
    row.update({"isMod": False, "disabled": False})
    return row


SODIUM_PATH = "D:/mc/mods/sodium.jar"
OLD_PATH = "D:/mc/mods/old.jar.disabled"
FAITHFUL_PATH = "D:/mc/resourcepacks/Faithful.zip"
MOD_A_PATH = "D:/mc/mods/a.jar"
SODIUM = mod_row(SODIUM_PATH, "Sodium", author="jellysquid", description="Rendering engine",
                 modid="sodium", version="0.5.8", filename="sodium.jar", size="1.2 MB")
OLD_MOD = mod_row(OLD_PATH, "Old Mod (disabled)", disabled=True, filename="old.jar.disabled",
                  size="10 KB")
FAITHFUL = plain_row(FAITHFUL_PATH, "Faithful", filename="Faithful.zip", size="3.4 MB",
                     icon="resourcepack", thumbnail="image://fmcl-thumb/1")
UPDATE_ROWS = [
    {"modid": "sodium", "mod_name": "Sodium", "current_version": "0.5.7",
     "latest_version": "0.5.8", "mod_path": "D:/mc/mods/sodium.jar"},
    {"modid": "lithium", "mod_name": "Lithium", "current_version": "0.12.0",
     "latest_version": "0.13.0", "mod_path": "D:/mc/mods/lithium.jar"},
]


def _walk(root_item: Any) -> Any:
    for child in root_item.childItems():
        yield child
        yield from _walk(child)


def banner(text: str) -> None:
    print(f"\n── {text} ──")


# ─── 各小节（编号与首版一致）────────────────────────────────────


def section_version_selector(probe: Probe, report: Report) -> None:
    """2. 版本选择器（两个入口汇到一页，看哪个版本由它决定）。"""
    box = probe.item("resourceVersionBox")
    report.check(box is not None, "版本选择器存在")
    if box is not None:
        #: 候选 = 「全局 .minecraft」+ 两个已安装版本（2026-10-07 反馈后新增的那一项）
        report.check(int(box.property("count")) == 3, "版本选择器有 3 个候选（含全局那一项）",
                     f"count={box.property('count')}")
    report.check(bool(probe.calls("activate")), "页面出现时调了 activate",
                 str(probe.calls("activate")))
    page = probe.page()
    version = str(page.property("version")) if page is not None else "<missing>"
    report.check(version == "1.20.4-forge", "页面版本来自桥的 versionId", f"version={version!r}")


def section_route_version(probe: Probe, report: Report) -> None:
    """2c. **路由给的版本必须真的传到桥**（2026-10-07 第三次验收反馈的钉子）。

    症状回顾：从版本列表选版本 B 进入资源管理，页面仍显示 A。根因是
    `onRouteParamsChanged` 里读了**派生绑定** `routeVersion` —— 那条绑定在 Qt 里是
    **异步**重算的，信号处理器跑的那一刻它还是旧值（空串），于是桥每一次收到的都是
    `activate("")`，照"路由没给版本"的兜底链走了最近使用/第一个已安装版本。

    这条判据直接**记下每次 `activate` 的参数**，再换一个版本压一次路由 —— 它问的是
    "页面到底把哪个版本告诉了桥"，而不是"页面显示什么"，所以能钉住根因；
    只断言显示文案的话，桥和页面都停在旧版本时反而是绿的。
    """
    fake = probe.fake
    page = probe.page()

    # ── ① 换一个版本重新进入：`activate` 的参数必须是路由里那一个 ──
    marker = len(fake.calls)
    fake.set_versions(["1.20.4", "1.21-fabric"])
    fake.set_scope("1.20.4")
    probe.nav.push("resources/mods", {"version": "1.21-fabric"})
    probe.settle()
    QTest.qWait(200)
    args = [str(call[1]) for call in probe.calls_since(marker, "activate")]
    report.check(args and args[-1] == "1.21-fabric",
                 "路由版本被原样传给桥（不是 activate(\"\")）", f"activate 调用={args}")

    # ── ② 换回另一个版本，再压一次（防"只对第一次生效"） ──
    marker = len(fake.calls)
    probe.nav.push("versions", {})
    probe.settle()
    probe.nav.push("resources/mods", {"version": "1.20.4"})
    probe.settle()
    QTest.qWait(200)
    args = [str(call[1]) for call in probe.calls_since(marker, "activate")]
    report.check(args and args[-1] == "1.20.4",
                 "第二次换版本同样把路由版本传给了桥", f"activate 调用={args}")

    # ── ③ 一级导航（路由**不带**版本）仍然是空串 —— 兜底链不能被改坏 ──
    marker = len(fake.calls)
    probe.nav.push("resources/packs", {})
    probe.settle()
    QTest.qWait(200)
    args = [str(call[1]) for call in probe.calls_since(marker, "activate")]
    report.check(args and args[-1] == "",
                 "不带版本的路由仍然传空串（交给桥的兜底链）", f"activate 调用={args}")

    #: 恢复现场给后面的小节用
    fake.set_versions(["1.20.4-forge", "1.21"])
    fake.set_scope("1.20.4-forge")
    probe.nav.push("resources/mods", {"version": "1.20.4-forge"})
    probe.settle()
    QTest.qWait(150)
    report.check(page is not None, "路由小节的现场已恢复")


def section_scope(probe: Probe, report: Report) -> None:
    """2b. 全局目录 / 原版页签（2026-10-07 用户验收反馈的三条）。"""
    page = probe.page()
    box = probe.item("resourceVersionBox")

    # ── ① 没版本上下文时是"全局 .minecraft"，不是"未指定游戏版本" ──
    probe.fake.set_scope("", list(TAB_KEYS))
    QTest.qWait(120)
    report.check(str(page.property("version")) == "", "全局目录时页面版本为空串",
                 f"version={page.property('version')!r}")
    hint = probe.text_of("resourceScopeHint")
    report.check(probe.item("resourceScopeHint") is not None and len(hint) > 0
                 and hint == probe.translated("resource_scope_global_hint"),
                 "全局目录时显示那句说明（且是译文）", f"got={hint!r}")
    #: 选择器里"全局"那一项必须能被选中（`currentIndex >= 0`）
    report.check(box is not None and int(box.property("currentIndex")) == 0,
                 "选择器选中项落在「全局 .minecraft」", str(box.property("currentIndex")) if box else "-")

    # ── ② 原版版本只给「资源包 / 地图」 ──
    probe.fake.set_scope("1.20.4", ["resourcepacks", "saves"])
    QTest.qWait(120)
    report.check(probe.item("fmTabBarItem-mods") is None, "原版版本没有「模组」页签")
    report.check(probe.item("fmTabBarItem-shaderpacks") is None, "原版版本没有「光影」页签")
    report.check(probe.item("fmTabBarItem-resourcepacks") is not None, "原版版本有「资源包」页签")
    report.check(probe.item("fmTabBarItem-saves") is not None, "原版版本有「地图」页签")
    report.check(probe.text_of("resourceScopeHint") == "" or not probe.item("resourceScopeHint").property("visible"),
                 "不是全局目录时那句说明不占位")
    #: 页签被过滤之后当前的 `tab` 必须落在允许集里（否则列表永远是空的）
    report.check(str(probe.fake.property("tab")) in ("resourcepacks", "saves"),
                 "原版版本的当前页签落在允许集里", str(probe.fake.property("tab")))

    # ── ③ 带加载器的版本又回到四个 ──
    probe.fake.set_scope("1.20.4-forge", list(TAB_KEYS))
    QTest.qWait(120)
    report.check(probe.item("fmTabBarItem-mods") is not None, "带加载器的版本「模组」页签回来")
    report.check(probe.item("fmTabBarItem-shaderpacks") is not None, "带加载器的版本「光影」页签回来")


def section_tabs(probe: Probe, report: Report) -> None:
    """3. 四个页签 + 说明文案跟着页签走（比的是**译文**，键名只是输入）。"""
    report.check(probe.item("resourceTabBar") is not None, "页签条存在")
    for key in TAB_KEYS:
        report.check(probe.item("fmTabBarItem-" + key) is not None, f"页签存在：{key}")
    for key in TAB_KEYS:
        probe.fake.set_tab(key)
        QTest.qWait(100)
        #: 说明文案现在是 `FmPage.description`（页头那行），不再是页面里的一个 Text
        #: —— 单根重构之后页头由 FmPage 提供（见 `body()` 的说明）。
        label = str(probe.page().property("description"))
        expected = probe.translated(TAB_DESC_KEYS[key])
        report.check(label == expected, f"{key} 的说明文案正确",
                     f"got={label!r} want={expected!r}")


def section_mod_cards(probe: Probe, report: Report) -> None:
    """4. 模组卡片（卡片 vs 一行的分支、禁用后缀、启停按钮）。"""
    probe.fake.set_tab("mods")
    probe.fake.set_page(1, 1)
    probe.fake.set_items([SODIUM, OLD_MOD])
    probe.fake.set_mod_count(2)
    probe.wait_until(lambda: probe.item("resourceModCard-" + SODIUM_PATH) is not None, 3000)
    QTest.qWait(120)
    #: 页面拿到真实尺寸才能判后面那些"点得动"的判据：`ListView` 高度为 0 时委托虽然
    #: 建得出来，但整块被 `clip` 掉，真鼠标点击一个都落不下去（不是判据写法的问题）。
    page = probe.page()
    body = probe.body()
    listview = probe.item("resourceListView")
    sizes = (f"page={page.width():.0f}x{page.height():.0f} "
             f"body={body.width():.0f}x{body.height():.0f} "
             f"list={listview.width():.0f}x{listview.height():.0f}"
             if (page is not None and body is not None and listview is not None) else "<缺件>")
    report.check(listview is not None and listview.height() > 0,
                 "列表区拿到真实高度（委托不被裁掉）", sizes)
    report.check(probe.item("resourceListView") is not None and probe.visible("resourceListView"),
                 "列表可见")
    report.check(probe.item("resourceModCard-" + SODIUM_PATH) is not None,
                 "模组卡片按 path 建了 objectName")
    report.check(probe.text_of("resourceName-" + SODIUM_PATH) == "Sodium",
                 "卡片标题取 display_name", probe.text_of("resourceName-" + SODIUM_PATH))
    toggle = probe.item("resourceToggle-" + OLD_PATH)
    toggle_text = str(toggle.property("text")) if toggle is not None else "<missing>"
    enable_text = probe.translated("rm_enable")
    report.check(toggle is not None and toggle_text == enable_text,
                 "禁用中的模组按钮文案是「启用」", f"got={toggle_text!r} want={enable_text!r}")
    #: 点"禁用"按钮 → 桥收到 (path, false)：**断言参数**而不是"桥被碰过"
    before = len(probe.fake.calls)
    probe.click_named("resourceToggle-" + SODIUM_PATH)
    got = probe.calls_since(before, "toggleMod")
    report.check(got == [("toggleMod", SODIUM_PATH, False)],
                 "点启用/禁用会调桥的 toggleMod（参数=路径+目标禁用态）", str(got))


def section_plain_rows(probe: Probe, report: Report) -> None:
    """5. 资源行（非模组页签走一行，不建卡片）。"""
    row = dict(FAITHFUL)
    #: 缩略图走真图（见 `Probe.register_thumbnail`：不登记的话 Qt 会刷 provider 告警）
    row["thumbnail"] = probe.register_thumbnail(FAITHFUL_PATH)
    probe.fake.set_tab("resourcepacks")
    probe.fake.set_items([row])
    probe.wait_until(lambda: probe.item("resourcePlainRow-" + FAITHFUL_PATH) is not None, 3000)
    QTest.qWait(120)
    report.check(probe.item("resourcePlainRow-" + FAITHFUL_PATH) is not None,
                 "资源行按 path 建了 objectName")
    report.check(probe.text_of("resourcePlainSize-" + FAITHFUL_PATH) == "3.4 MB",
                 "资源行显示体积", probe.text_of("resourcePlainSize-" + FAITHFUL_PATH))
    report.check(probe.item("resourceThumb-" + FAITHFUL_PATH) is not None,
                 "有缩略图时建 Image 委托")


def section_pagination(probe: Probe, report: Report) -> None:
    """6. 分页（组件不改自己的 page，"翻页"的判据是桥收到目标页号）。"""
    probe.fake.set_tab("mods")
    probe.fake.set_items([mod_row(f"D:/mc/mods/m{i}.jar", f"m{i}") for i in range(3)])
    probe.fake.set_page(1, 3)
    QTest.qWait(150)
    pager = probe.item("resourcePagination")
    report.check(pager is not None and probe.visible("resourcePagination"), "多于 1 页时分页可见")
    if pager is not None:
        report.check(int(pager.property("pageCount")) == 3, "分页总页数来自桥",
                     str(pager.property("pageCount")))
    report.check(probe.text_of("fmPaginationLabel").strip() == "1 / 3", "页码文案是 `1 / 3`",
                 probe.text_of("fmPaginationLabel"))
    before = len(probe.fake.calls)
    probe.click_named("fmPaginationNext")
    got = probe.calls_since(before, "goto")
    report.check(got == [("goto", 2)], "翻页把目标页号交给桥", str(got))


def section_states(probe: Probe, report: Report) -> None:
    """7. 三态（`contentState` 在页面**根对象**上 —— 它就是 `FmPage`）。"""
    body = probe.body()
    report.check(body is not None, "页面根对象存在（FmPage）")
    if body is None:
        return
    probe.fake.set_state("loading")
    QTest.qWait(150)
    report.check(str(body.property("contentState")) == "loading", "loading 态透传",
                 str(body.property("contentState")))
    report.check(probe.item("pageStateArea") is not None, "状态块存在（pageStateArea）")
    report.check(probe.visible("fmLoadingState"), "loading 显示 FmLoadingState")
    probe.fake.set_state("error")
    QTest.qWait(150)
    report.check(str(body.property("contentState")) == "error", "error 态透传",
                 str(body.property("contentState")))
    report.check(probe.visible("fmErrorState"), "error 显示 FmErrorState")
    probe.fake.set_items([])
    probe.fake.set_state("empty")
    QTest.qWait(150)
    report.check(str(body.property("contentState")) == "empty", "empty 态透传",
                 str(body.property("contentState")))
    report.check(probe.visible("fmEmptyState"), "empty 显示 FmEmptyState")
    #: 空态标题按**真实原因**分两种（2026-10-07 第二次反馈：原先写死成"还没有已安装的
    #: 版本"，而状态栏在说"文件夹不存在"，标题和原因对不上）。这里判"有版本、目录空"那一支。
    want = probe.translated("rm_folder_empty").replace(
        "{label}", probe.translated(str(probe.fake.property("tabLabelKey")))
    )
    report.check(probe.text_of("fmEmptyStateTitle") == want,
                 "空目录时空态标题是「{label} 文件夹为空」（不是「没有版本」那句）",
                 f"got={probe.text_of('fmEmptyStateTitle')!r} want={want!r}")
    #: 一个版本都没装时才是"先装一个版本"那句
    probe.fake.set_scope("", list(TAB_KEYS))
    probe.fake.set_versions(())
    QTest.qWait(120)
    want_no_version = probe.translated("resource_no_version_hint")
    report.check(probe.text_of("fmEmptyStateTitle") == want_no_version,
                 "一个版本都没装时空态标题才是 noVersionKey 那句",
                 f"got={probe.text_of('fmEmptyStateTitle')!r} want={want_no_version!r}")
    probe.fake.set_versions(("1.20.4-forge", "1.21"))
    probe.fake.set_scope("1.20.4-forge", list(TAB_KEYS))
    QTest.qWait(120)


def section_progress(probe: Probe, report: Report) -> None:
    """8. 模组元数据进度（局部加载：列表还在，不是整页 loading）。"""
    probe.fake.set_tab("mods")
    probe.fake.set_items([mod_row(MOD_A_PATH, "A")])
    probe.fake.set_progress("reading mods (1/4)", 1, 4)
    QTest.qWait(200)
    report.check(probe.visible("resourceModsProgress"), "元数据进度条可见")
    report.check(probe.text_of("fmProgressBarLabel") == "reading mods (1/4)",
                 "进度条文案来自桥的 progress.message", probe.text_of("fmProgressBarLabel"))
    report.check(probe.visible("resourceListView"), "进度中列表仍显示（不是整页 loading）")
    body = probe.body()
    report.check(body is not None and str(body.property("contentState")) == "ready",
                 "进度中整页仍是 ready", str(body.property("contentState")) if body else "<missing>")
    probe.fake.set_progress("", 0, 0)
    QTest.qWait(200)
    report.check(not probe.visible("resourceModsProgress"), "进度结束后进度条隐藏")


def section_dropzone(probe: Probe, report: Report) -> None:
    """9. 拖拽（`FmDropZone.dropped` → 页面把 url 列表原样交给 `Resources.takeFiles(urls, drop=True)`）。

    路径折算（`file://` → 本地路径）在**桥**里做（`ResourcesBridge.toLocalPaths`，红线 2），
    所以页面这一侧能判的就是"url 原样送达 + drop 标志为真"。
    """
    report.check(probe.visible("resourceDropZone"), "拖拽区可见")
    zone = probe.item("resourceDropZone")
    before = len(probe.fake.calls)
    dropped_url = QUrl.fromLocalFile("D:/a b/x.jar")
    emitted = probe.emit_signal(zone, "dropped", [dropped_url])
    QTest.qWait(200)
    got = probe.calls_since(before, "takeFiles")
    want = ("takeFiles", [dropped_url.toString()], True)
    report.check(emitted and bool(got) and got[0] == want,
                 "拖拽把 url 列表（含空格）以 drop=True 交给桥", f"emit={emitted} got={got} want={want}")


def section_updates(probe: Probe, report: Report) -> None:
    """10. 更新对话框（默认全选、"更新选中"只交勾选项、"全部更新"不看勾选）。"""
    probe.fake.set_tab("mods")
    probe.fake.set_items([SODIUM])
    probe.fake.set_updates(UPDATE_ROWS)
    probe.wait_until(lambda: probe.item("resourceUpdateDialog") is not None, 3000)
    QTest.qWait(150)
    page = probe.page()
    report.check(bool(page.property("updateDialogOpen")), "有可更新项时对话框自动打开")
    report.check(probe.item("resourceUpdateDialog") is not None,
                 "更新对话框已创建（Loader.active）")
    report.check(probe.text_of("resourceUpdateVersion-sodium") == "v0.5.7 -> v0.5.8",
                 "版本对比文案用 ASCII 箭头", probe.text_of("resourceUpdateVersion-sodium"))

    #: 取消勾选 lithium → 点「更新选中」→ 断言桥收到的就是勾选的那一个
    dialog = probe.item("resourceUpdateDialog")
    check_lithium = probe.child(dialog, "resourceUpdateCheck-lithium")
    report.check(check_lithium is not None
                 and bool(check_lithium.property("checked")),
                 "可更新项默认全选（lithium 的勾选框是选中的）")
    probe.click_named_from(dialog, "resourceUpdateCheck-lithium")
    probe.click_named_from(dialog, "resourceUpdateSelectedButton")
    before_after = probe.calls("applyUpdates")
    report.check(bool(before_after) and before_after[-1] == ("applyUpdates", ["sodium"]),
                 "取消勾选后「更新选中」只提交勾选的那一个", str(before_after))
    report.check(not bool(page.property("updateDialogOpen")), "更新后对话框关闭")

    #: 再开一次：这次先取消勾选，再点「全部更新」—— 语义是"不看勾选"
    probe.fake.set_updates(UPDATE_ROWS)
    probe.wait_until(lambda: probe.item("resourceUpdateDialog") is not None, 3000)
    QTest.qWait(150)
    dialog = probe.item("resourceUpdateDialog")
    probe.click_named_from(dialog, "resourceUpdateCheck-lithium")
    QTest.qWait(120)
    probe.click_named_from(dialog, "resourceUpdateAllButton")
    QTest.qWait(150)
    applied = probe.calls("applyUpdates")
    report.check(bool(applied) and applied[-1] == ("applyUpdates", ["sodium", "lithium"]),
                 "「全部更新」提交全部（不看勾选）", str(applied))


def section_delete(probe: Probe, report: Report) -> None:
    """11. 确认删除（`deleteRequested` → 确认框 → `confirmDelete` 只带那一个路径）。"""
    probe.fake.set_updates([])
    probe.fake.set_tab("mods")
    probe.fake.set_items([mod_row(MOD_A_PATH, "A")])
    QTest.qWait(200)
    before = len(probe.fake.calls)
    probe.click_named("resourceDelete-" + MOD_A_PATH)
    QTest.qWait(200)
    requested = probe.calls_since(before, "requestDelete")
    report.check(requested == [("requestDelete", MOD_A_PATH, "A")],
                 "点删除会请求确认（路径 + 显示名）", str(requested))
    report.check(probe.item("resourceDeleteDialog") is not None, "确认框已创建")
    before = len(probe.fake.calls)
    probe.click_named_from(probe.item("resourceDeleteDialog"), "okButton")
    QTest.qWait(200)
    confirmed = probe.calls_since(before, "confirmDelete")
    report.check(confirmed == [("confirmDelete", MOD_A_PATH)],
                 "确认后只删那一个路径", str(confirmed))

    #: 取消路径：再点一次删除，点「取消」—— 一个 confirmDelete 都不该有
    before = len(probe.fake.calls)
    probe.click_named("resourceDelete-" + MOD_A_PATH)
    QTest.qWait(200)
    probe.click_named_from(probe.item("resourceDeleteDialog"), "cancelButton")
    QTest.qWait(200)
    report.check(not probe.calls_since(before, "confirmDelete"), "点「取消」一个都不删",
                 str(probe.calls_since(before, "confirmDelete")))


def section_actions(probe: Probe, report: Report) -> None:
    """12. 动作按钮（模组页签专属按钮的可见性、有版本时「选择文件安装」可用）。"""
    page = probe.page()
    page.setProperty("updateDialogOpen", False)
    probe.fake.set_updates([])
    probe.fake.set_tab("mods")
    probe.fake.set_page(1, 1)
    probe.fake.set_items([mod_row(MOD_A_PATH, "A")])
    probe.fake.set_mod_count(1)
    QTest.qWait(200)
    mods_only_visible = probe.visible("resourceExportButton")
    probe.fake.set_tab("saves")
    QTest.qWait(200)
    report.check(mods_only_visible and not probe.visible("resourceExportButton"),
                 "「导出模组列表」只在模组页签可见")
    probe.fake.set_tab("mods")
    QTest.qWait(200)
    report.check(probe.enabled("resourceAddButton"), "「选择文件安装」在有版本时可用")
    before = len(probe.fake.calls)
    probe.click_named("resourceAddButton")
    got = probe.calls_since(before, "requestAddFiles")
    report.check(bool(got), "点「选择文件安装」通知页面弹文件框", str(got))
    file_dialog = probe.find_qml_object("resourceAddFilesDialog")
    opened = bool(file_dialog is not None and file_dialog.property("visible"))
    report.check(opened, "页面真的把文件框打开了（onAddFilesRequested → open()）",
                 f"visible={file_dialog.property('visible') if file_dialog is not None else '<missing>'}")
    if opened:
        #: 截图前必须收掉它：`FileDialog` 是**独立的顶层窗口**，`grabWindow()` 会把它一起拍进去
        #: （3.5 的证据图里就留着这么一张原生文件框，把页面状态全挡住了）。
        #: 收法用它的 `close()` 槽 —— `QMetaObject.invokeMethod` 调槽是本仓库的常规手法。
        QMetaObject.invokeMethod(file_dialog, "close", Qt.ConnectionType.DirectConnection)
        QTest.qWait(250)


def section_status_and_toast(probe: Probe, report: Report) -> None:
    """13. 状态栏 / Toast 转发（读**真桥**，不是自造的替身）。"""
    probe.fake.statusMessage.emit("已安装 2 个资源", "info")
    probe.fake.noticeRequested.emit("资源更新完成", "success")
    QTest.qWait(200)
    status_text = str(probe.shell.property("statusText")) if probe.shell is not None else "<missing>"
    status_level = str(probe.shell.property("statusLevel")) if probe.shell is not None else "<missing>"
    report.check(status_text == "已安装 2 个资源" and status_level == "info",
                 "状态栏文案转给 Shell.setStatus",
                 f"text={status_text!r} level={status_level!r}")
    toasts = probe.toasts()
    matched = [row for row in toasts
               if str(row.get("message")) == "资源更新完成" and str(row.get("level")) == "success"]
    report.check(bool(matched), "提示转成 Dialogs.notify",
                 str(matched[-1] if matched else (toasts[-1] if toasts else [])))


def section_shots(probe: Probe, report: Report) -> List[Path]:
    """14. 截图（`--shots`）：6 张，`docs/refactor/evidence/3.6/resources_<状态>.png`。"""
    shots: List[Path] = []

    #: 13 节留下的那条 Toast 会挂在右下角整整 4.5 秒（截图证据里不该有它）—— 用真桥的槽清掉。
    if probe.dialogs is not None:
        probe.dialogs.clearToasts()
        QTest.qWait(250)

    def grab(name: str) -> None:
        QTest.qWait(250)
        path = probe.shot(name)
        if path is not None:
            shots.append(path)
        print(f"  [shot] {name}: {path if path else '<失败>'}")

    probe.fake.set_updates([])
    probe.wait_until(lambda: probe.item("resourceUpdateDialog") is None, 2000)
    probe.fake.set_page(1, 1)
    probe.fake.set_tab("mods")
    probe.fake.set_items([SODIUM, OLD_MOD])
    probe.fake.set_mod_count(2)
    probe.fake.set_progress("", 0, 0)
    grab("resources_mods")

    probe.fake.set_tab("resourcepacks")
    pack_row = dict(FAITHFUL)
    pack_row["thumbnail"] = probe.register_thumbnail(FAITHFUL_PATH)
    probe.fake.set_items([pack_row])
    grab("resources_resourcepacks")

    probe.fake.set_tab("mods")
    probe.fake.set_items([])
    probe.fake.set_state("empty")
    grab("resources_empty")

    probe.fake.set_state("error")
    grab("resources_error")

    probe.fake.set_items([SODIUM])
    probe.fake.set_state("ready")
    probe.fake.set_updates(UPDATE_ROWS)
    probe.wait_until(lambda: probe.item("resourceUpdateDialog") is not None, 3000)
    grab("resources_updates")

    probe.fake.set_updates([])
    probe.wait_until(lambda: probe.item("resourceUpdateDialog") is None, 2000)
    probe.click_named("resourceDelete-" + SODIUM_PATH)
    probe.wait_until(lambda: probe.item("resourceDeleteDialog") is not None, 3000)
    grab("resources_delete")

    sizes = ", ".join(f"{path.name}:{path.stat().st_size}B" for path in shots)
    report.check(len(shots) == 6 and all(path.stat().st_size > 0 for path in shots),
                 "6 张截图都已生成（非空）", sizes)
    print(f"  截图目录：{SHOTS_DIR}")
    return shots


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 3.6「我的资源」页 QML 判据脚本")
    parser.add_argument("--shots", action="store_true",
                        help="顺带把 6 个关键状态截图到 docs/refactor/evidence/3.6")
    parser.add_argument("--json", default="", help="把判据结果写到指定 json")
    args = parser.parse_args()

    print("== 阶段 3.6「我的资源」页 QML 判据 ==")
    print(f"页面 {PAGE.relative_to(REPO_ROOT)}  路由 {ROUTE}  "
          f"平台 {os.environ.get('QT_QPA_PLATFORM', '')}")
    report = Report()

    probe = Probe()
    messages_before_load = len(probe.sink.messages)

    banner("1. 加载（App.qml + Nav.push）与零 QML 警告")
    loaded = probe.load()
    report.check(loaded, "资源页加载成功（页面栈里有 resourcesPage）",
                 "" if loaded else probe.failure_detail())
    if not loaded:
        print("\n  Qt 消息原文：")
        for text in probe.sink.messages[messages_before_load:]:
            print("    *", text.replace("\n", " / "))
        for text in probe.engine_warnings:
            print("    * engine.warnings:", text.replace("\n", " / "))
        print()
        print(f"判据 {len(report.checks)} 条，失败 {report.failures} 条")
        if args.json:
            report.dump(Path(args.json))
        return 1

    report.check(not probe.sink_messages(messages_before_load) and not probe.engine_warnings,
                 "加载期无 QML 警告", probe.warning_report(messages_before_load))

    banner("2. 版本选择器")
    section_version_selector(probe, report)
    banner("2b. 全局目录 / 原版页签")
    section_scope(probe, report)
    banner("2c. 路由带来的版本（第三次验收反馈的钉子）")
    section_route_version(probe, report)
    banner("3. 四个页签")
    section_tabs(probe, report)
    banner("4. 模组卡片")
    section_mod_cards(probe, report)
    banner("5. 资源行（非模组页签）")
    section_plain_rows(probe, report)
    banner("6. 分页")
    section_pagination(probe, report)
    banner("7. 三态")
    section_states(probe, report)
    banner("8. 模组元数据进度")
    section_progress(probe, report)
    banner("9. 拖拽")
    section_dropzone(probe, report)
    banner("10. 更新对话框")
    section_updates(probe, report)
    banner("11. 确认删除")
    section_delete(probe, report)
    banner("12. 动作按钮")
    section_actions(probe, report)
    banner("13. 状态栏 / Toast 转发")
    section_status_and_toast(probe, report)
    if args.shots:
        banner("14. 截图")
        section_shots(probe, report)

    report.check(not probe.all_messages() and not probe.engine_warnings,
                 "整场无 QML 警告（含交互期）", probe.warning_report(0))

    print()
    print(f"判据 {len(report.checks)} 条，失败 {report.failures} 条")
    if args.json:
        report.dump(Path(args.json))
    return 1 if report.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
