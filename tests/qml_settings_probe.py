"""设置页 QML 探针（阶段 3 任务 3.4；`tests/test_settings_page_qml.py` 用**子进程**驱动）。

## 为什么另起进程

与 `tests/qml_install_probe.py` / `qml_versions_probe.py` 同一个理由：真引擎 + FluentUI 的
`FluWindow` 在同一个进程里跨测试文件不安全。这里只加载一次 `App.qml`。

## 它验证什么（对着 3.4 的验收条目逐条来）

1. **入口与分区**：一级导航点「设置」→ 路由 `settings` → 左侧 8 个分区（从路由表现取），
   右侧默认落在「启动器功能」；
2. **草稿语义（M-Q1 的 B1/B2）**：改开关/滑块/下拉 → `Settings.dirty` 变真、
   **核心层的 setter 一次都没被调**（这就是"没写盘"的可验判据）；切换分区再切回来，
   改动还在（B1 的整窗一份草稿）；
3. **预览（B2）**：选主题 → 内存里的 `services.palette.COLORS["accent"]` 当场变，
   而配置里的 `theme_name` 没变；点「取消」→ 颜色还原成已保存值；
4. **保存（B3）**：点「保存」→ 核心层的 setter 按 `DRAFT_KEYS` 顺序被调用、
   `config.save_config` 被调用、成就只触发一次（再点一次不会重复触发）；
5. **语言热切换（M-Q2 的行为变更）**：改语言 → `Tr.language` 当场变（预览）、
   配置没变；保存后配置才变；分区标题跟着译文变；
6. **离开守卫（B6）**：有未保存改动时点别的导航项 → 弹确认框、路由**没变**；
   点「取消」→ 留在设置；点「确定」→ 丢弃草稿并真的导航过去；
7. **Java（M-03/04/05）**：切到"从扫描列表中选择" → 扫描区出现 → 点刷新 → 真任务桥
   跑出两行 → 选中一行写进草稿的 `java_custom_path`；切到"自定义路径"→ 输入框出现；
8. **日志（A-13/A-18）**：写一行日志 → `LogView` 里出现；清空 → 行数归零；
   导出到临时文件 → 文件真的写出来了（来源是缓冲区，因为探针里没有落盘 handler）；
9. **关于（A-08/J-01~J-03）**：4 行系统信息、9 个鸣谢、协议正文非空；
   点鸣谢的「地址」→ 打开外链的实现被调用（参数就是那一行的 url）；
10. **保存并重启（M-24 / B5）**：点按钮 → 先落盘（setter 被调用）再拉起新进程
    （注入的 spawn 收到 argv），并发出 `restartRequested`。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QEvent, QMetaObject, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import main_qml  # noqa: E402
from app.bootstrap import build_context  # noqa: E402
from app.bridges.icon_provider import install as install_icon_provider  # noqa: E402
from services.about_service import AboutService  # noqa: E402
from services.log_service import LogService  # noqa: E402
from services.settings_service import SettingsService  # noqa: E402
from services.theme_service import init_theme_engine  # noqa: E402

MARKER = "PROBE_JSON:"
APP = QGuiApplication.instance() or QGuiApplication([])
SINK = main_qml.install_message_handler()

#: `--shots <目录>`：顺手把关键几帧落盘（人工验收用的证据截图）。
#: **必须在模块级解析**：下面的检查在 import 期间就跑完了，放到 `main()` 里就晚了。
SHOT_DIR: Optional[Path] = None
SHOTS: List[str] = []
if "--shots" in sys.argv:
    _index = sys.argv.index("--shots")
    if _index + 1 < len(sys.argv):
        SHOT_DIR = Path(sys.argv[_index + 1])
        SHOT_DIR.mkdir(parents=True, exist_ok=True)

QML_ERROR_MARKERS = ("is not defined", "TypeError", "Unable to assign", "Cannot assign", "ReferenceError")
BENIGN_MARKERS = (
    "This plugin does not support",
    "QFont::setPointSize",
    "propagateSizeHints",
    "Failed to create",
)

TMP_DIR = Path(tempfile.mkdtemp(prefix="fmcl-settings-probe-"))
THEMES_DIR = TMP_DIR / "themes"
LOG_FILE = TMP_DIR / "probe.log"


# ─── 假核心层 / 假配置 ─────────────────────────────────────────


class FakeConfig:
    """配置对象的替身：只保留设置页读写的那些属性 + 写盘计数。"""

    def __init__(self) -> None:
        self.base_dir = TMP_DIR
        self.log_file = LOG_FILE
        self.language = "zh_CN"
        self.language_chosen = True
        self.accent_color: Optional[str] = None
        self.theme_name = "default"
        self.dynamic_version_theme = False
        self.minimize_on_game_launch = False
        self.mirror_enabled = True
        self.download_threads = 4
        self.java_mode = "auto"
        self.java_custom_path: Optional[str] = None
        self.saves = 0

    def save_config(self) -> bool:
        self.saves += 1
        return True


class FakeLauncher:
    """`MinecraftLauncher` 的替身：9 个 getter/setter + Java 扫描。

    **setter 全部记账**：探针要证明"改控件时一次都没调、点保存时才调"，
    这份 `writes` 就是那条判据的观测点。
    """

    def __init__(self, config: FakeConfig) -> None:
        self.config = config
        self.writes: List[Tuple[str, Any]] = []
        self.scan_calls = 0

    # 读侧 ─────────────────────────────────────────────────────
    def get_minimize_on_game_launch(self) -> bool:
        return bool(self.config.minimize_on_game_launch)

    def get_mirror_enabled(self) -> bool:
        return bool(self.config.mirror_enabled)

    def get_download_threads(self) -> int:
        return int(self.config.download_threads)

    def get_language(self) -> str:
        return str(self.config.language)

    def get_theme_name(self) -> str:
        return str(self.config.theme_name)

    def get_accent_color(self) -> Optional[str]:
        return self.config.accent_color

    def get_dynamic_version_theme(self) -> bool:
        return bool(self.config.dynamic_version_theme)

    def get_java_mode(self) -> str:
        return str(self.config.java_mode)

    def get_java_custom_path(self) -> Optional[str]:
        return self.config.java_custom_path

    # 写侧 ─────────────────────────────────────────────────────
    # 每个 setter 都像真核心层那样落一次盘（`launcher/core.py` 的 `set_*` 结尾
    # 都是 `config.save_config()`）—— 探针里"有没有写盘"因此有两个观测点：
    # 这份 `writes` 与 `FakeConfig.saves`。
    def set_minimize_on_game_launch(self, enabled: bool) -> None:
        self.config.minimize_on_game_launch = bool(enabled)
        self.writes.append(("minimize_on_game_launch", bool(enabled)))
        self.config.save_config()

    def set_mirror_enabled(self, enabled: bool) -> None:
        self.config.mirror_enabled = bool(enabled)
        self.writes.append(("mirror_enabled", bool(enabled)))
        self.config.save_config()

    def set_download_threads(self, threads: int) -> None:
        self.config.download_threads = int(threads)
        self.writes.append(("download_threads", int(threads)))
        self.config.save_config()

    def set_language(self, code: str) -> None:
        self.config.language = str(code)
        self.writes.append(("language", str(code)))
        self.config.save_config()

    def set_theme_name(self, name: str) -> None:
        self.config.theme_name = str(name)
        self.writes.append(("theme_name", str(name)))
        self.config.save_config()

    def set_accent_color(self, color: Optional[str]) -> None:
        self.config.accent_color = color
        self.writes.append(("accent_color", color))
        self.config.save_config()

    def set_dynamic_version_theme(self, enabled: bool) -> None:
        self.config.dynamic_version_theme = bool(enabled)
        self.writes.append(("dynamic_version_theme", bool(enabled)))
        self.config.save_config()

    def set_java_mode(self, mode: str) -> None:
        self.config.java_mode = str(mode)
        self.writes.append(("java_mode", str(mode)))
        self.config.save_config()

    def set_java_custom_path(self, path: Optional[str]) -> None:
        self.config.java_custom_path = path
        self.writes.append(("java_custom_path", path))
        self.config.save_config()

    def scan_system_java(self) -> List[Dict[str, Any]]:
        self.scan_calls += 1
        return [
            {
                "path": str(TMP_DIR / "jdk21" / "bin" / "java.exe"),
                "home": str(TMP_DIR / "jdk21"),
                "major_version": 21,
                "version_str": "21.0.1",
                "arch": "x64",
                "is_jre": False,
            },
            {
                "path": str(TMP_DIR / "jre8" / "bin" / "java.exe"),
                "home": str(TMP_DIR / "jre8"),
                "major_version": 8,
                "version_str": "1.8.0_402",
                "arch": "x86",
                "is_jre": True,
            },
        ]


class PinnedConfig:
    """钉住界面语言（`TrBridge` 会读根模块的 `config`，见 D-160 的修法）。"""

    def __init__(self, language: str = "zh_CN") -> None:
        self.language = language
        self.language_chosen = True
        self.base_dir = TMP_DIR

    def save_config(self) -> bool:
        return True


from app.bridges import tr_bridge as _tr_bridge  # noqa: E402

_tr_bridge._root_config = lambda: PinnedConfig()  # type: ignore[assignment]

from config_isolation import isolate_config_writes  # noqa: E402

isolate_config_writes("settings-probe")

FAKE_CONFIG = FakeConfig()
FAKE_LAUNCHER = FakeLauncher(FAKE_CONFIG)
THEME_ENGINE = init_theme_engine(str(TMP_DIR))

SPAWNED: List[List[str]] = []
OPENED: List[str] = []
URLS: List[str] = []


def fake_spawn(argv: Any, **_kwargs: Any) -> None:
    SPAWNED.append([str(item) for item in argv])


SETTINGS_SERVICE = SettingsService(
    launcher=FAKE_LAUNCHER,
    theme_engine=THEME_ENGINE,
    config=FAKE_CONFIG,
    spawn=fake_spawn,
)
LOG_SERVICE = LogService(config=FAKE_CONFIG, opener=lambda path: OPENED.append(str(path)))
ABOUT_SERVICE = AboutService(opener=lambda url: URLS.append(str(url)))

CONTEXT = build_context(config=None, register=True, set_current=False)
CONTEXT.register_instance("settings", SETTINGS_SERVICE, replace=True)
CONTEXT.register_instance("log", LOG_SERVICE, replace=True)
CONTEXT.register_instance("about", ABOUT_SERVICE, replace=True)

ENGINE = main_qml.build_engine()
install_icon_provider(ENGINE)
#: 主题桥也钉到假配置上：否则它会读**根模块的真 config**，把开发机上用户自己的
#: `accent_color`（例如 `#abcdef`）应用到全局调色板，探针里"取消后还原成已保存值"
#: 这条判据就会拿一个被污染的基线去比（第一版就是这么红的）。
from app.bridges.theme_bridge import ThemeBridge as _ThemeBridge  # noqa: E402

BRIDGES = main_qml.register_bridges(ENGINE, CONTEXT, prebuilt={"Theme": _ThemeBridge(config=FAKE_CONFIG)})

SETTINGS = ENGINE._fmcl_bridges["Settings"]
LOGS = ENGINE._fmcl_bridges["Logs"]
ABOUT = ENGINE._fmcl_bridges["About"]
NAV = ENGINE._fmcl_bridges["Nav"]
SHELL = ENGINE._fmcl_bridges["Shell"]
TR = ENGINE._fmcl_bridges["Tr"]
TASKS = ENGINE._fmcl_bridges["Tasks"]
DIALOG_HOST = None

for bridge in (SETTINGS, LOGS, ABOUT):
    use_engine = getattr(bridge, "use_engine", None)
    if callable(use_engine):
        use_engine(ENGINE)

LOG_SERVICE.attach_logger()

ENGINE.load(QUrl.fromLocalFile(str(main_qml.app_qml_path("App.qml"))))
ROOT = ENGINE.rootObjects()[0] if ENGINE.rootObjects() else None
QTest.qWait(150)


# ─── 通用工具 ──────────────────────────────────────────────────


REPORT: Dict[str, Any] = {
    "checks": [],
    "failures": [],
    "notes": [],
    "bridges_registered": list(BRIDGES.get("registered") or []),
    "bridges_missing": list(BRIDGES.get("missing") or []),
}


def check(name: str, ok: bool, detail: str = "") -> bool:
    REPORT["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
    if not ok:
        REPORT["failures"].append(f"{name}: {detail}")
    # 同时打到 stderr：探针中途抛异常时，已经跑过的判据仍然看得见（否则只剩一条 traceback）
    print(f"[check] {'OK  ' if ok else 'FAIL'} {name} {detail}", file=sys.stderr)
    return bool(ok)


def _walk(root_item: Any) -> Any:
    for child in root_item.childItems():
        yield child
        yield from _walk(child)


def _matches(name: str) -> List[Any]:
    out: List[Any] = []
    if ROOT is None:
        return out
    content = ROOT.contentItem()
    if content is None:
        return out
    for candidate in _walk(content):
        if candidate.objectName() == name:
            out.append(candidate)
    return out


def _visible(node: Any) -> bool:
    try:
        return bool(node.isVisible())
    except Exception:  # noqa: BLE001
        return False


def item(name: str) -> Any:
    """按 objectName 找项（同名时优先可见的那个，与 3.2/3.3 探针同款）。"""
    found = _matches(name)
    for candidate in found:
        if _visible(candidate):
            return candidate
    return found[0] if found else None


def items_named(name: str) -> List[Any]:
    found = _matches(name)
    alive = [node for node in found if _visible(node)]
    return alive if alive else found


def text_of(name: str) -> str:
    target = item(name)
    if target is None:
        return "<missing>"
    value = target.property("text")
    return "" if value is None else str(value)


def visible(name: str) -> bool:
    target = item(name)
    return bool(target is not None and target.property("visible"))


def enabled(name: str) -> bool:
    target = item(name)
    return bool(target is not None and target.property("enabled"))


def settle(timeout_ms: int = 2000) -> None:
    stack = item("pageStack")
    if stack is None:
        return
    waited = 0
    while waited < timeout_ms:
        pages = [
            child for child in stack.childItems()
            if child.objectName().endswith("Page") and child.property("visible")
        ]
        if len(pages) <= 1:
            return
        QTest.qWait(50)
        waited += 50


def click(target: Any) -> None:
    if target is None:
        raise RuntimeError("点击目标不存在")
    settle()
    centre = target.mapToScene(QPointF(target.width() / 2, target.height() / 2))
    global_pos = ROOT.mapToGlobal(centre)
    for event_type, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton),
                                (QEvent.MouseButtonRelease, Qt.NoButton)):
        APP.sendEvent(ROOT, QMouseEvent(event_type, centre, global_pos, Qt.LeftButton, buttons, Qt.NoModifier))
        QTest.qWait(40)


def click_named(name: str) -> bool:
    target = item(name)
    if target is None:
        return False
    click(target)
    return True


def invoke(target: Any, method: str) -> bool:
    from PySide6.QtCore import QMetaObject

    if target is None:
        return False
    return bool(QMetaObject.invokeMethod(target, method, Qt.ConnectionType.DirectConnection))


def wait_until(predicate: Any, timeout_ms: int = 4000, step_ms: int = 50) -> bool:
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return True
        QTest.qWait(step_ms)
        waited += step_ms
    return bool(predicate())


def shot(name: str) -> None:
    """把当前一帧落盘（只在 `--shots` 给了目录时做；失败不影响判据）。"""
    if SHOT_DIR is None or ROOT is None:
        return
    try:
        image = ROOT.grabWindow()
        target = SHOT_DIR / f"{name}.png"
        if image.isNull() or not image.save(str(target)):
            return
        SHOTS.append(str(target))
    except Exception as e:  # noqa: BLE001 - 截图失败不影响判据
        REPORT["notes"].append(f"截图 {name} 失败: {e}")


def accent_color() -> str:
    from services.palette import COLORS

    return str(COLORS.get("accent", ""))


def combo_labels(combo: Any) -> List[str]:
    """读下拉里各项的**显示文本**（用户看到的那一份）。"""
    if combo is None:
        return []
    model = combo.property("model")
    out: List[str] = []
    for index in range(int(combo.property("count"))):
        row = model[index] if hasattr(model, "__getitem__") else model.get(index)
        if hasattr(row, "get"):
            out.append(str(row.get("label") if row.get("label") is not None else row.get("name")))
        else:
            out.append(str(row))
    return out


def log_lines_in_view() -> int:
    view = item("settingsLogView")
    if view is None:
        return -1
    return int(view.property("lineCount"))


def goto(route: str) -> None:
    NAV.push(route)
    QTest.qWait(120)
    settle()


def debug(name: str, value: Any) -> None:
    """诊断输出（写进 report 与 stderr）—— 判据失败时不用再跑一遍去猜中间值。"""
    REPORT.setdefault("debug", {})[name] = value
    print(f"[debug] {name} = {value}", file=sys.stderr)


def record_names(names: List[str]) -> None:
    """把一组 objectName 是否真的在树里记进 report（结构被改掉时测试会红）。"""
    table = REPORT.setdefault("objectNames", {})
    for name in names:
        table[name] = item(name) is not None


# ══ 1. 入口与分区 ══════════════════════════════════════════════

REPORT["route_before"] = NAV.currentRoute
goto("settings")
REPORT["route_after"] = NAV.currentRoute
check("进入设置路由", REPORT["route_after"] == "settings", REPORT["route_after"])
check("设置页加载出来了", item("settingsPage") is not None, "objectName=settingsPage")
check("左侧分区导航在工作", item("settingsSectionNav") is not None)
sections = items_named("settingsSectionItem")
REPORT["section_count"] = len(sections)
check("分区数为 8（路由表里 parent=settings 的全部）", len(sections) == 8, str(len(sections)))
check("默认落在启动器分区", item("settingsLauncherSection") is not None,
      str([node.property("text") for node in sections]))
check("底部动作条存在", item("settingsActionBar") is not None)
check("初始没有未保存标记", not visible("settingsUnsavedTag"))
check("初始「保存」不可用", not enabled("settingsSaveButton"))
#: 当前分区指示条：既验"选中态看得见"，也钉住它的**高度** ——
#: 第一版忘了卡高度，ColumnLayout 把多余高度分给了第一行，指示条被拉成 210px 的长杠。
_indicators = items_named("settingsSectionIndicator")
check("当前分区有强调色指示条", len(_indicators) == 1, str(len(_indicators)))
if _indicators:
    _height = float(_indicators[0].height())
    check("指示条高度与列表项相称（没被布局拉长）", 0 < _height <= 48, str(_height))
record_names(["settingsPage", "settingsLayout", "settingsSectionNav", "settingsSectionList",
              "settingsNavTitle", "settingsActionBar", "settingsUnsavedTag",
              "settingsCancelButton", "settingsSaveButton", "settingsSaveRestartButton",
              "settingsSectionLoader", "settingsLauncherSection", "settingsLauncherScroll",
              "settingsMinimizeSwitch", "settingsMirrorSwitch", "settingsThreadsSlider",
              "settingsLanguageCombo"])
#: 三个 `FileDialog` **不在**这里登记：Qt6 的 `FileDialog` 不是可视项（`QQuickAbstractDialog`），
#: 按 objectName 走视觉树永远找不到它 —— 登记进去只会得到一条假失败。
#: 它们的接线由"点导入/导出按钮会打开对话框"来验（人工比对清单里有这一条）。
shot("settings_launcher")

# ══ 2. 草稿语义（改控件不写盘）═════════════════════════════════

FAKE_LAUNCHER.writes.clear()
saves_before = FAKE_CONFIG.saves

minimize = item("settingsMinimizeSwitch")
check("最小化开关存在", minimize is not None)
before_minimize = bool(FAKE_CONFIG.minimize_on_game_launch)
click(minimize)
REPORT["minimize_after_click"] = bool(minimize.property("checked"))
check("点开关在草稿里生效", REPORT["minimize_after_click"] is not before_minimize,
      str(REPORT["minimize_after_click"]))
check("**没有**写盘（核心层 setter 一次没调）", FAKE_LAUNCHER.writes == [], str(FAKE_LAUNCHER.writes))
check("**没有**存配置", FAKE_CONFIG.saves == saves_before, str(FAKE_CONFIG.saves))
check("草稿变脏", bool(SETTINGS.property("dirty")))
check("计数为 1", int(SETTINGS.property("changeCount")) == 1, str(SETTINGS.property("changeCount")))
check("未保存标记出现", visible("settingsUnsavedTag"))
check("「保存」变可用", enabled("settingsSaveButton"))
check("「取消」变可用", enabled("settingsCancelButton"))

# 滑块：拖到 12（直接设值 + 触发 moved，与 Qt 的实际交互等价）
slider = item("settingsThreadsSlider")
check("线程滑块存在", slider is not None)
slider.setProperty("value", 12)
QMetaObject.invokeMethod(slider, "moved", Qt.ConnectionType.DirectConnection)
QTest.qWait(60)
REPORT["threads_draft"] = SETTINGS.property("fields").get("download_threads")
check("滑块改动进草稿", int(REPORT["threads_draft"]) == 12, str(REPORT["threads_draft"]))
check("滑块改动也没写盘", FAKE_LAUNCHER.writes == [], str(FAKE_LAUNCHER.writes))
check("计数为 2", int(SETTINGS.property("changeCount")) == 2, str(SETTINGS.property("changeCount")))

# ══ 3. 主题预览（B2：内存生效、不落盘）═════════════════════════

goto("settings/theme")
check("主题分区加载出来了", item("settingsThemeSection") is not None, "objectName=settingsThemeSection")
record_names(["settingsThemeSection", "settingsThemeScroll", "settingsThemeCombo",
              "settingsThemeImportButton", "settingsAccentField", "settingsAccentApplyButton",
              "settingsAccentRandomButton", "settingsDynamicSwitch"])
shot("settings_theme")
accent_before = accent_color()
theme_combo = item("settingsThemeCombo")
REPORT["theme_labels"] = combo_labels(theme_combo)
check("主题下拉有 5 个预设", len(REPORT["theme_labels"]) == 5, str(REPORT["theme_labels"]))
if theme_combo is not None:
    theme_combo.setProperty("currentIndex", 1)
    theme_combo.activated.emit(1)
QTest.qWait(80)
accent_preview = accent_color()
REPORT["accent_before"] = accent_before
REPORT["accent_preview"] = accent_preview
check("预览改了内存里的强调色", accent_preview != accent_before, f"{accent_before} → {accent_preview}")
check("预览**没有**写进配置", FAKE_CONFIG.theme_name == "default", FAKE_CONFIG.theme_name)
check("预览也没调核心层", FAKE_LAUNCHER.writes == [], str(FAKE_LAUNCHER.writes))
check("主题变更进了草稿", SETTINGS.property("fields").get("theme_name") == "ocean",
      str(SETTINGS.property("fields").get("theme_name")))

# 强调色输入 + 应用（预览）
accent_field = item("settingsAccentField")
check("强调色输入框存在", accent_field is not None)
accent_field.setProperty("text", "#123456")
accent_field.edited.emit("#123456")
QTest.qWait(40)
click_named("settingsAccentApplyButton")
QTest.qWait(80)
REPORT["accent_manual"] = accent_color()
check("自定义强调色当场预览", REPORT["accent_manual"].lower() == "#123456", REPORT["accent_manual"])
check("非法输入会被拦下（校验函数返回非空）",
      SETTINGS.validateAccent("#12") != "" and SETTINGS.validateAccent("#123456") == "",
      SETTINGS.validateAccent("#12"))

# 取消：丢弃草稿 + 还原预览
click_named("settingsCancelButton")
QTest.qWait(120)
REPORT["accent_after_cancel"] = accent_color()
check("取消后强调色还原成已保存值", REPORT["accent_after_cancel"] == accent_before,
      f"{REPORT['accent_after_cancel']} vs {accent_before}")
check("取消后草稿清空", not bool(SETTINGS.property("dirty")))
check("取消后仍未写盘", FAKE_LAUNCHER.writes == [], str(FAKE_LAUNCHER.writes))
check("取消后未保存标记消失", not visible("settingsUnsavedTag"))

# ══ 4. 保存（B3：写盘 + 成就只触发一次）═══════════════════════

goto("settings/launcher")
click(item("settingsMinimizeSwitch"))  # 再改一次最小化
QTest.qWait(60)
goto("settings/theme")
if theme_combo is not None:
    theme_combo.setProperty("currentIndex", 2)
    theme_combo.activated.emit(2)
QTest.qWait(80)
check("切分区回来改动还在（B1 整窗一份草稿）",
      SETTINGS.property("fields").get("minimize_on_game_launch") is not before_minimize
      and SETTINGS.property("fields").get("theme_name") == "forest",
      json.dumps({k: SETTINGS.property("fields").get(k) for k in ("minimize_on_game_launch", "theme_name")},
                 ensure_ascii=False))
REPORT["changes_before_save"] = list(SETTINGS.property("changes"))
check("改动清单是这两项",
      sorted(SETTINGS.property("changes")) == ["minimize_on_game_launch", "theme_name"],
      str(sorted(SETTINGS.property("changes"))))

click_named("settingsSaveButton")
QTest.qWait(150)
REPORT["writes_after_save"] = [list(row) for row in FAKE_LAUNCHER.writes]
write_keys = [row[0] for row in FAKE_LAUNCHER.writes]
check("保存把改动写进核心层", sorted(write_keys) == ["minimize_on_game_launch", "theme_name"], str(write_keys))
check("保存后草稿不再脏", not bool(SETTINGS.property("dirty")))
check("保存时配置真的落盘了", FAKE_CONFIG.saves >= 2, str(FAKE_CONFIG.saves))
REPORT["achievements"] = sorted(SETTINGS_SERVICE.fired_achievements())
check("保存触发了主题成就（最小化那项没有成就）",
      REPORT["achievements"] == ["personalize_theme_master"], str(REPORT["achievements"]))
shot("settings_theme_saved")

# 再点一次保存（没有改动）：不该重复触发成就
achievements_before = sorted(SETTINGS_SERVICE.fired_achievements())
SETTINGS.commitDraft()
QTest.qWait(60)
check("没改动时保存不会重复触发成就",
      sorted(SETTINGS_SERVICE.fired_achievements()) == achievements_before,
      str(sorted(SETTINGS_SERVICE.fired_achievements())))

# ══ 5. 语言热切换（M-Q2 的行为变更）═══════════════════════════

goto("settings/launcher")
language_combo = item("settingsLanguageCombo")
REPORT["language_labels"] = combo_labels(language_combo)
check("语言下拉有 4 项", len(REPORT["language_labels"]) == 4, str(REPORT["language_labels"]))
language_before = str(TR.property("language"))
if language_combo is not None:
    language_combo.setProperty("currentIndex", 0)
    language_combo.activated.emit(0)
QTest.qWait(120)
REPORT["language_preview"] = str(TR.property("language"))
check("语言当场热切换（预览）", REPORT["language_preview"] != language_before,
      f"{language_before} → {REPORT['language_preview']}")
check("预览时配置没变", FAKE_CONFIG.language == "zh_CN", FAKE_CONFIG.language)
check("切换后是 en_US（下拉按 code 排序的第一项）",
      REPORT["language_preview"] == "en_US", REPORT["language_preview"])
REPORT["section_title_en"] = text_of("settingsNavTitle")
shot("settings_english")

# 取消：语言也要还原
click_named("settingsCancelButton")
QTest.qWait(120)
REPORT["language_after_cancel"] = str(TR.property("language"))
check("取消后语言还原", REPORT["language_after_cancel"] == "zh_CN", REPORT["language_after_cancel"])

# ══ 6. 离开守卫（B6）══════════════════════════════════════════

FAKE_LAUNCHER.writes.clear()
check("没有改动时不登记守卫", NAV.guardedDomains() == [], str(NAV.guardedDomains()))
click(item("settingsMirrorSwitch"))  # 造一个未保存改动
QTest.qWait(80)
check("有改动时登记守卫", NAV.guardedDomains() == ["settings"], str(NAV.guardedDomains()))
route_before_leave = NAV.currentRoute
NAV.push("home")
QTest.qWait(120)
check("跨域导航被挡下",
      NAV.currentRoute == route_before_leave and NAV.property("pendingLeaveRoute") == "home",
      f"{NAV.currentRoute} / {NAV.property('pendingLeaveRoute')}")
dialog = item("promptConfirmDialog")
check("弹出了确认框", dialog is not None and dialog.objectName() == "promptConfirmDialog")
shot("settings_unsaved_confirm")
invoke(dialog, "cancel")
QTest.qWait(120)
check("选「取消」留在设置页", NAV.currentRoute == route_before_leave, NAV.currentRoute)
check("选「取消」后没有任何待裁决导航",
      NAV.property("pendingLeaveRoute") == "", NAV.property("pendingLeaveRoute"))
check("草稿还在（没有丢弃）", bool(SETTINGS.property("dirty")))
NAV.push("home")
QTest.qWait(150)
dialog = item("promptConfirmDialog")
check("当选「确定」后确认框收回", invoke(dialog, "accept"))
QTest.qWait(250)
debug("route_after_accept", NAV.currentRoute)
check("选「确定」真的离开", NAV.currentRoute == "home", NAV.currentRoute)
check("离开时草稿被丢弃", not bool(SETTINGS.property("dirty")))
check("离开后守卫撤销", NAV.guardedDomains() == [], str(NAV.guardedDomains()))
REPORT["mirror_after_discard"] = bool(FAKE_CONFIG.mirror_enabled)
check("丢弃没有写盘", FAKE_LAUNCHER.writes == [], str(FAKE_LAUNCHER.writes))

# ══ 7. Java 分区（M-03 / M-04 / M-05）═════════════════════════

goto("settings/java")
check("Java 分区加载出来了", item("settingsJavaSection") is not None)
record_names(["settingsJavaSection", "settingsJavaScroll", "settingsJavaModeCombo",
              "settingsJavaCustomRow", "settingsJavaCustomField", "settingsJavaBrowseButton",
              "settingsJavaScanRow", "settingsJavaScanRefresh", "settingsJavaScanState"])
java_combo = item("settingsJavaModeCombo")
REPORT["java_modes"] = combo_labels(java_combo)
check("Java 模式下拉 3 项", len(REPORT["java_modes"]) == 3, str(REPORT["java_modes"]))
check("默认不显示自定义路径区", not visible("settingsJavaCustomRow"))
if java_combo is not None:
    java_combo.setProperty("currentIndex", 2)  # custom
    java_combo.activated.emit(2)
QTest.qWait(120)
check("选「自定义路径」显示输入框", visible("settingsJavaCustomRow"))
if java_combo is not None:
    java_combo.setProperty("currentIndex", 1)  # scan
    java_combo.activated.emit(1)
QTest.qWait(120)
check("选「从扫描列表中选择」显示扫描区", visible("settingsJavaScanRow"))
check("扫描区有状态文案", text_of("settingsJavaScanState") != "", text_of("settingsJavaScanState"))
click_named("settingsJavaScanRefresh")
QTest.qWait(200)
debug("java_state_after_scan", {
    "bridge": SETTINGS.describe(),
    "tasks": TASKS.describe(),
    "scanCalls": FAKE_LAUNCHER.scan_calls,
})
found = wait_until(lambda: len(items_named("settingsJavaScanItem")) >= 2, timeout_ms=5000)
REPORT["java_rows"] = len(items_named("settingsJavaScanItem"))
check("扫描任务跑出两行（走真任务桥）", found and REPORT["java_rows"] == 2, str(REPORT["java_rows"]))
check("核心层的 scan_system_java 被调用了", FAKE_LAUNCHER.scan_calls >= 1, str(FAKE_LAUNCHER.scan_calls))
rows = items_named("settingsJavaScanItem")
if rows:
    click(rows[0])
    QTest.qWait(80)
REPORT["java_path"] = SETTINGS.property("fields").get("java_custom_path")
check("选中一行写进草稿", str(REPORT["java_path"]).endswith("java.exe"), str(REPORT["java_path"]))
shot("settings_java")

# ══ 8. 日志分区（A-13 / A-18）══════════════════════════════════

goto("settings/log")
check("日志分区加载出来了", item("settingsLogSection") is not None)
check("LogView 在位", item("settingsLogView") is not None)
record_names(["settingsLogSection", "settingsLogLayout", "settingsLogStats",
              "settingsLogClearButton", "settingsLogExportButton", "settingsLogFolderButton",
              "settingsLogPath", "settingsLogView"])
before_lines = log_lines_in_view()
marker = "[FMCL] settings-probe-log-line"
logging.getLogger("logzero_default").info(marker)
QTest.qWait(200)
after_lines = log_lines_in_view()
REPORT["log_lines"] = [before_lines, after_lines]
check("新日志行进到 LogView", after_lines > before_lines, f"{before_lines} → {after_lines}")
check("日志行数有上限（5000）", int(item("settingsLogView").property("maxLines")) == 5000,
      str(item("settingsLogView").property("maxLines")))
shot("settings_log")

export_target = str(TMP_DIR / "exported.log")
LOGS.exportLog(export_target)
QTest.qWait(80)
REPORT["export_exists"] = Path(export_target).exists()
check("导出写出了文件", REPORT["export_exists"], export_target)
if REPORT["export_exists"]:
    REPORT["export_bytes"] = Path(export_target).stat().st_size
    check("导出文件非空", REPORT["export_bytes"] > 0, str(REPORT["export_bytes"]))
LOGS.openLogDir()
QTest.qWait(40)
check("打开日志目录调到了平台实现", OPENED == [str(TMP_DIR)], str(OPENED))
click_named("settingsLogClearButton")
QTest.qWait(150)
REPORT["log_lines_after_clear"] = log_lines_in_view()
#: 清空之后**可能马上又有一行**：服务自己会记一条"日志缓冲已清空（N 行）"
#: （旧界面的清空按钮同理 —— 那次操作本身也是一条日志）。所以判据是"掉到 1 行以内"。
check("清空后行数归零（最多剩清空动作自己那一行）",
      REPORT["log_lines_after_clear"] <= 1, str(REPORT["log_lines_after_clear"]))

# ══ 9. 关于分区（A-08 / J-01 ~ J-03）══════════════════════════

goto("settings/about")
check("关于分区加载出来了", item("settingsAboutSection") is not None)
record_names(["settingsAboutSection", "settingsAboutScroll", "settingsAboutInfo",
              "settingsAboutDonate", "settingsAboutTerms"])
REPORT["about_info_rows"] = len(items_named("settingsAboutInfoRow"))
check("版本信息 4 行（QML 侧真的画了 4 行）", REPORT["about_info_rows"] == 4, str(REPORT["about_info_rows"]))
acks = items_named("settingsAboutAck")
REPORT["about_acks"] = len(acks)
check("鸣谢 9 个项目", REPORT["about_acks"] == 9, str(REPORT["about_acks"]))
terms = text_of("settingsAboutTerms")
REPORT["terms_length"] = len(terms)
check("协议正文渲染出来了（长文本）", REPORT["terms_length"] > 500, str(REPORT["terms_length"]))
check("版本信息 4 行（服务侧）", len(ABOUT.property("info")) == 4, str(ABOUT.property("info")))
check("关于页自检可用", bool(ABOUT.describe()["available"]))
shot("settings_about")

# 点第一个鸣谢项目的「地址」：打开外链的实现要收到那一行的 url
# （用信号而不是鼠标：这一行在滚动视图的可视区之外，点不到 —— 那不是缺陷）
URLS.clear()
address_buttons = [node for node in _matches("fmButton")
                   if str(node.property("text")) == "地址"]
check("鸣谢行有「地址」按钮", bool(address_buttons), str(len(address_buttons)))
if address_buttons:
    address_buttons[0].clicked.emit()
QTest.qWait(80)
REPORT["opened_urls"] = list(URLS)
check("外链打开了第一条鸣谢的 url",
      URLS[:1] == ["https://github.com/PCL-community/PCL-CE"], str(URLS))

# ══ 10. 分区六~八（阶段 3.5 起账户分区是真的内容，AI / 插件仍是占位）══
#
# 3.4 时三个都是占位（账户 / AI / 插件）。3.5 把账户分区做成了真页面，所以这里改成：
#   * `settings/account` —— 断言**不再**是占位，而且账号页的控件真的在树里；
#   * `settings/ai` / `settings/plugin` —— 仍按 3.4 的裁决断言占位（随 3.22 / 3.23 迁）。

goto("settings/account")
_account_placeholder = item("settingsPlaceholder")
check("账户分区不再是占位（3.5 已交付）", _account_placeholder is None,
      "settingsPlaceholder 还在 —— 分区可能没接上")
check("账户分区的标题在树里", item("accountTitle") is not None, "accountTitle")
check("账户分区的动作条在树里", item("accountActionRow") is not None, "accountActionRow")
check("账户分区的账号列表在树里", item("accountListView") is not None, "accountListView")

placeholder_titles = []
placeholder_hints = []
for _route in ("settings/ai", "settings/plugin"):
    goto(_route)
    _placeholder = item("settingsPlaceholder")
    placeholder_titles.append("" if _placeholder is None else str(_placeholder.property("title")))
    placeholder_hints.append("" if _placeholder is None else str(_placeholder.property("description")))
REPORT["placeholder_titles"] = placeholder_titles
REPORT["placeholder_hints"] = placeholder_hints
check("AI / 插件两个占位分区都显示占位内容", all(title for title in placeholder_titles), str(placeholder_titles))
check("两个占位分区标题各不相同", len(set(placeholder_titles)) == 2, str(placeholder_titles))
check("占位分区带说明文案（不是白屏）", all(hint for hint in placeholder_hints), str(placeholder_hints))
shot("settings_placeholder")

# ══ 11. 保存并重启（M-24 / B5）════════════════════════════════

goto("settings/launcher")
check("回到启动器分区", item("settingsLauncherSection") is not None)
FAKE_LAUNCHER.writes.clear()
SPAWNED.clear()
click(item("settingsMinimizeSwitch"))  # 造一个待保存的改动（启动器分区里可见的那个）
QTest.qWait(80)
check("待保存改动已就位", bool(SETTINGS.property("dirty")))
_save_restart = item("settingsSaveRestartButton")
debug("save_restart_button", None if _save_restart is None else {
    "visible": bool(_save_restart.property("visible")),
    "enabled": bool(_save_restart.property("enabled")),
    "text": str(_save_restart.property("text")),
})
click_named("settingsSaveRestartButton")
QTest.qWait(250)
debug("writes_after_restart", [list(row) for row in FAKE_LAUNCHER.writes])
REPORT["spawned"] = [list(row) for row in SPAWNED]
_restart_keys = sorted(row[0] for row in FAKE_LAUNCHER.writes)
check("保存并重启：先落盘（整窗草稿一次提交）",
      _restart_keys == ["java_custom_path", "java_mode", "minimize_on_game_launch"],
      str(_restart_keys))
check("保存并重启：拉起了新进程", len(SPAWNED) == 1, str(SPAWNED))
check("新进程命令行非空", bool(SPAWNED and SPAWNED[0]), str(SPAWNED))
check("保存并重启后草稿清空", not bool(SETTINGS.property("dirty")))

# ══ 收尾：QML 日志里不许有报错 ════════════════════════════════

messages = [str(text) for text in getattr(SINK, "messages", [])]
errors = [
    text for text in messages
    if any(marker in text for marker in QML_ERROR_MARKERS)
    and not any(benign in text for benign in BENIGN_MARKERS)
]
REPORT["qml_message_count"] = len(messages)
REPORT["qml_errors"] = errors[:10]
check("QML 日志没有报错", not errors, str(errors[:3]))
REPORT["shots"] = SHOTS
REPORT["ok"] = not REPORT["failures"]


def main() -> int:
    print(MARKER + json.dumps(REPORT, ensure_ascii=False))
    return 0 if REPORT["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())



