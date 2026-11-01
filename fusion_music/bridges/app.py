"""应用级控制器：导航状态、通知、窗口状态、剪贴板、封面保存。"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, List, Tuple

from PySide6.QtCore import QObject, Property, QStandardPaths, QUrl, Signal, Slot
from PySide6.QtGui import QGuiApplication

from .. import paths
from ..core import covers
from ..core.trackutil import as_dict
from ..sources import SOURCE_META
from ..units import human_size

logger = logging.getLogger(__name__)

# ── 窗口尺寸 ────────────────────────────────────────────────
# 默认尺寸是照 1080p 定的舒适值，但**不能顶出屏幕**：1366×768 上任务栏一占，
# 可用高度只剩 728，760 的窗口会把底部播放栏顶到屏幕外面、点都点不到；
# 1366×768 开 125% 缩放更极端（可用区域只有 1093×582，比窗口的最小高度还矮）。
# 所以初始尺寸与最小尺寸都要夹进**屏幕可用区域**（不是整块屏幕）。
WINDOW_DEFAULT_W = 1180
WINDOW_DEFAULT_H = 760
#: 窗口本身能缩到多小（布局的下限，与 Main.qml 里的两个常量没有第二个来源）
WINDOW_FLOOR_W = 880
WINDOW_FLOOR_H = 560
#: 与屏幕边缘留的缝（左右各半、上下各半）
SCREEN_MARGIN = 48


def fit_window(want: int, available: int, floor: int, margin: int = SCREEN_MARGIN) -> int:
    """把窗口尺寸夹进「屏幕可用区域 − 边距」（纯函数，便于离线测试）。

    * ``available <= 0``（拿不到屏幕信息，比如没有显示器）→ 只保证不小于 ``floor``；
    * 屏幕比 ``floor`` 还小时**底线让位给屏幕**：最小值本身把窗口撑出屏幕，
      比尺寸偏大更难用（那种情况下用户连边框都拖不到）。
    """
    try:
        want = int(want)
        available = int(available)
        floor = int(floor)
    except (TypeError, ValueError):
        return WINDOW_DEFAULT_W
    if available <= 0:
        return max(floor, want)
    limit = max(1, available - max(0, int(margin)))
    return max(min(floor, limit), min(want, limit))

PAGES: List[Dict[str, str]] = [
    {"id": "discover", "name": "发现音乐", "icon": "Globe", "desc": "推荐歌单与排行榜"},
    {"id": "roam", "name": "漫游", "icon": "MapCompassTop", "desc": "按口味持续推荐的流"},
    {"id": "search", "name": "搜索", "icon": "Search", "desc": "多音源在线搜索"},
    {"id": "library", "name": "我的音乐", "icon": "Heart", "desc": "我喜欢与歌单"},
    {"id": "local", "name": "本地音乐", "icon": "Folder", "desc": "扫描本地曲库"},
    {"id": "queue", "name": "播放队列", "icon": "List", "desc": "当前播放列表"},
    {"id": "downloads", "name": "下载", "icon": "Download", "desc": "下载任务与进度"},
]

# ── 关闭主窗口的行为 ────────────────────────────────────────
#: 「关闭主窗口时」的选项。设置页的下拉、询问框的「记住我的选择」与
#: :func:`close_decision` 共用这一份，不会各写各的。
CLOSE_ACTION_OPTIONS: List[Dict[str, str]] = [
    {"id": "ask", "name": "每次询问"},
    {"id": "tray", "name": "最小化到托盘"},
    {"id": "quit", "name": "直接退出"},
]
CLOSE_ACTIONS: Tuple[str, ...] = tuple(option["id"] for option in CLOSE_ACTION_OPTIONS)


def close_decision(action: str, tray_usable: bool) -> str:
    """点了 ✕ 之后到底该怎么办：``"tray" | "ask" | "quit"``。

    托盘用不了时（系统不支持，或者用户把托盘图标关了）**一律直接退出** ——
    没有托盘还把窗口藏起来的话，用户就再也找不回这个程序了。
    抽成纯函数是为了能离线回归：这种 bug 在界面上很难发现（点了 ✕ 窗口还在，
    看着像「卡住了」）。
    """
    if not tray_usable:
        return "quit"
    value = str(action or "").strip().lower()
    if value == "tray":
        return "tray"
    if value == "quit":
        return "quit"
    return "ask"


def _system_tray_available() -> bool:
    """系统有没有可用的通知区域（托盘）。

    查询走 QtWidgets 的 ``QSystemTrayIcon``（静态方法，不需要 QApplication）；
    界面那边用的是 ``Qt.labs.platform`` 的 ``SystemTrayIcon``，两者问的是同一个
    平台接口，结论一致。QtWidgets 缺失时按「没有托盘」处理：这时只能直接退出。

    **必须先确认存在 GUI 应用实例**：只有 QCoreApplication（测试 / 脚本）时，
    这个静态查询会在 Qt 内部空指针解引用 —— 是硬崩，不是异常，try/except
    拦不住（实测 0xC0000005）。注意不能用 ``QApplication.instance() is None`` 判断：
    它和 ``QCoreApplication.instance()`` 是同一个静态方法，只有一个 QCoreApplication
    时照样返回那个对象（类型不对而已），必须用 ``isinstance(..., QGuiApplication)``。
    真机上界面起来时这条判断永远为真，不影响行为。
    """
    try:
        from PySide6.QtGui import QGuiApplication
        from PySide6.QtWidgets import QApplication, QSystemTrayIcon

        if not isinstance(QApplication.instance(), QGuiApplication):
            logger.debug("没有 GUI 应用实例，按「系统没有托盘」处理")
            return False
        return bool(QSystemTrayIcon.isSystemTrayAvailable())
    except Exception as e:  # pragma: no cover - 依赖运行环境
        logger.debug("查询系统托盘失败: %s", e)
        return False


class _CoverEmitter(QObject):
    """把封面保存线程的结果投递回主线程（Qt 会自动排队到接收者线程）。"""

    saved = Signal(bool, str)


class AppController(QObject):
    pageChanged = Signal()
    expandedChanged = Signal()
    queuePanelChanged = Signal()
    notify = Signal(str, str)   # level, message
    sourceMetaChanged = Signal()
    loginRequested = Signal()
    settingsRequested = Signal()
    systemDarkChanged = Signal()
    trayChanged = Signal()
    closeActionChanged = Signal()

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self._config = config
        self._page = "discover"
        self._expanded = bool(config.get("window.player_expanded", False))
        self._queue_panel = bool(config.get("window.queue_visible", False))
        # 封面保存：结果要从工作线程回到主线程弹通知
        self._cover_emitter = _CoverEmitter(self)
        self._cover_emitter.saved.connect(self._on_cover_saved)
        # 托盘有没有：只问一次。平台集成给的这个答案在一个会话里是稳定的，
        # 界面按它决定要不要给托盘相关的选项（见 close_decision）。
        self._tray_available = _system_tray_available()

        # 跟随系统深浅色：FluThemeType 没有 Auto，需要我们自己读取系统配色。
        # 注意先确认真的**有 QGuiApplication**：只有 QCoreApplication 时
        # ``QGuiApplication.styleHints()`` 返回空指针，PySide6 会在里面直接崩
        # （不是抛异常，try/except 拦不住），测试与脚本里构造这个控制器就会踩到。
        try:
            if isinstance(QGuiApplication.instance(), QGuiApplication):
                hints = QGuiApplication.styleHints()
                hints.colorSchemeChanged.connect(self.systemDarkChanged)
            else:
                logger.debug("没有 QGuiApplication，跳过系统配色监听")
        except Exception as e:  # pragma: no cover
            logger.debug("无法监听系统配色变化: %s", e)

    @Property(bool, notify=systemDarkChanged)
    def systemDark(self) -> bool:  # noqa: N802
        try:
            from PySide6.QtCore import Qt

            return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            return False

    # ── 导航 ────────────────────────────────────────────────

    @Property("QVariantList", constant=True)
    def pages(self):  # noqa: N802
        return PAGES

    @Property(str, notify=pageChanged)
    def page(self) -> str:
        return self._page

    @Slot(str)
    def go(self, page_id: str) -> None:
        page_id = str(page_id or "").strip()
        if not page_id or page_id == self._page:
            return
        self._page = page_id
        self.pageChanged.emit()

    # ── 展开态 / 队列面板 ───────────────────────────────────

    @Property(bool, notify=expandedChanged)
    def expanded(self) -> bool:
        return self._expanded

    @Slot()
    def toggleExpanded(self) -> None:  # noqa: N802
        self._expanded = not self._expanded
        self._config.set("window.player_expanded", self._expanded)
        self.expandedChanged.emit()

    @Slot(bool)
    def setExpanded(self, value: bool) -> None:  # noqa: N802
        if bool(value) == self._expanded:
            return
        self._expanded = bool(value)
        self._config.set("window.player_expanded", self._expanded)
        self.expandedChanged.emit()

    @Property(bool, notify=queuePanelChanged)
    def queuePanel(self) -> bool:  # noqa: N802
        return self._queue_panel

    @Slot()
    def toggleQueuePanel(self) -> None:  # noqa: N802
        self._queue_panel = not self._queue_panel
        self._config.set("window.queue_visible", self._queue_panel)
        self.queuePanelChanged.emit()

    # ── 系统托盘 ────────────────────────────────────────────

    @Property(bool, notify=trayChanged)
    def trayAvailable(self) -> bool:  # noqa: N802
        """系统有没有托盘。没有的话托盘相关的选项一律不该出现。"""
        return self._tray_available

    @Property(bool, notify=trayChanged)
    def trayEnabled(self) -> bool:  # noqa: N802
        """用户有没有要托盘图标常驻。"""
        return bool(self._config.get("window.tray_icon", True))

    @Slot(bool)
    def setTrayEnabled(self, value: bool) -> None:  # noqa: N802
        self._config.set("window.tray_icon", bool(value))
        self.trayChanged.emit()

    @Property(bool, notify=trayChanged)
    def trayUsable(self) -> bool:  # noqa: N802
        """托盘真的能用吗：系统支持 **且** 用户没关掉。"""
        return self._tray_available and self.trayEnabled

    @Property(str, constant=True)
    def trayIconSource(self) -> str:  # noqa: N802
        """托盘图标地址。优先 .ico —— Windows 会按 DPI 从里面挑合适的那一档。

        资源要找 :func:`paths.resource_dir` 而不是 ``program_dir()``：
        打包后 ``assets/`` 在 ``_internal/``（onedir）或 ``%TEMP%\\_MEIxxxx``
        （onefile）里，按 exe 目录找会拿到空串 —— 托盘上就是一个没有图标的空白位。
        """
        assets = paths.resource_dir() / "assets"
        for name in ("icon.ico", "icon.png"):
            candidate = assets / name
            if candidate.exists():
                return paths.resource(str(candidate))
        return ""

    # ── 关闭主窗口的行为 ────────────────────────────────────

    @Property(str, notify=closeActionChanged)
    def closeAction(self) -> str:  # noqa: N802
        value = str(self._config.get("window.close_action", "ask") or "").strip().lower()
        return value if value in CLOSE_ACTIONS else "ask"

    @Slot(str)
    def setCloseAction(self, action: str) -> None:  # noqa: N802
        value = str(action or "").strip().lower()
        if value not in CLOSE_ACTIONS:
            value = "ask"
        self._config.set("window.close_action", value)
        self.closeActionChanged.emit()

    @Property("QVariantList", constant=True)
    def closeActionOptions(self):  # noqa: N802
        return [dict(option) for option in CLOSE_ACTION_OPTIONS]

    @Slot(result=str)
    def closeDecision(self) -> str:  # noqa: N802
        """关闭主窗口时该走哪条路，给界面直接调（逻辑见 :func:`close_decision`）。"""
        return close_decision(self.closeAction, self.trayUsable)

    @Slot()
    def quitApplication(self) -> None:  # noqa: N802
        """真的退出程序（托盘菜单的「退出」与询问框的「退出程序」都走这里）。

        **不能用 QML 的 ``Qt.quit()``**（等价于 ``QCoreApplication::quit()``）：
        托盘图标只要露过面，这个调用就会被吞掉 —— 实测（Qt 6.11 / Windows）
        窗口藏进托盘之后点「退出程序」什么都不会发生，用户再点一次 ✕ 又弹一遍
        询问框，看起来就是死循环。Qt 有意把「有托盘的程序」留活（托盘的意义就是
        窗口关了程序还在），``quit()`` 尊重这层拦截。
        ``exit(0)`` 是 ``quit()`` 文档里写的等价物，只是不受它影响；``aboutToQuit``
        照样会发，所以 :meth:`Application.shutdown` 里的存档一步都不会少。
        """
        logger.info("正在退出程序…")
        QGuiApplication.exit(0)

    # ── 封面保存 ────────────────────────────────────────────
    #
    # 交互定的是「弹系统另存为对话框，记住上次选的目录」：QML 侧先问
    # coverSaveHint() 要默认文件名与默认目录，用户确认后把最终路径交给
    # saveCover()。下载（可能几十 KB 到几 MB）放在工作线程里做，
    # 结果再用信号投回主线程弹通知 —— 不能让界面在下载期间卡住。

    def _cover_dir(self) -> Path:
        """另存为对话框的起始目录：上次存过的 → 系统图片目录 → 用户主目录。"""
        configured = str(self._config.get("storage.cover_dir", "") or "").strip()
        if configured:
            path = Path(configured).expanduser()
            if path.is_dir():
                return path
        try:
            pictures = QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.PicturesLocation
            )
        except Exception as e:  # pragma: no cover - 依赖运行环境
            logger.debug("取系统图片目录失败: %s", e)
            pictures = ""
        if pictures and Path(pictures).is_dir():
            return Path(pictures)
        return Path.home()

    @Slot("QVariant", result="QVariant")
    def coverSaveHint(self, track) -> Dict[str, str]:  # noqa: N802
        """另存为对话框的默认值：``{name, file, folder}``（后两个是 file:// URL）。

        扩展名是按封面地址**猜**的（``.jpg`` 起步），真正落盘时会按文件头纠正，
        见 :func:`app.core.covers.save_cover`。
        """
        info = as_dict(track)
        suffix = covers.suggest_suffix(info)
        name = covers.suggest_stem(info) + suffix
        folder = self._cover_dir()
        return {
            "name": name,
            "suffix": suffix.lstrip("."),
            "file": QUrl.fromLocalFile(str(folder / name)).toString(),
            "folder": QUrl.fromLocalFile(str(folder)).toString(),
        }

    @Slot("QVariant", str)
    def saveCover(self, track, url: str) -> None:  # noqa: N802
        """把曲目封面另存到 ``url``（QML FileDialog 给的 ``file://`` URL）。"""
        info = as_dict(track)
        if not info:
            self.error("没有可保存的封面")
            return
        threading.Thread(
            target=self._cover_worker, args=(info, str(url or "")),
            daemon=True, name="save-cover",
        ).start()

    def _cover_worker(self, info: Dict[str, Any], url: str) -> None:
        """（工作线程）只做下载与落盘，结果用信号投回主线程。"""
        try:
            path, size = covers.save_cover(info, url)
        except covers.CoverError as e:
            self._cover_emitter.saved.emit(False, f"保存封面失败：{e}")
            return
        except Exception as e:  # pragma: no cover - 兜底，别把线程里的异常吞掉
            logger.exception("保存封面异常")
            self._cover_emitter.saved.emit(False, f"保存封面失败：{e}")
            return
        try:
            self._config.set("storage.cover_dir", str(Path(path).parent))
        except Exception as e:
            logger.debug("记住封面保存目录失败: %s", e)
        self._cover_emitter.saved.emit(True, f"封面已保存：{path}（{human_size(size)}）")

    @Slot(bool, str)
    def _on_cover_saved(self, ok: bool, message: str) -> None:
        if ok:
            self.info(message)
        else:
            self.error(message)

    # ── 通知 ────────────────────────────────────────────────

    @Slot(str)
    def info(self, message: str) -> None:
        self.notify.emit("info", str(message))

    @Slot()
    def openLogin(self) -> None:  # noqa: N802
        self.loginRequested.emit()

    @Slot()
    def openSettings(self) -> None:  # noqa: N802
        self.settingsRequested.emit()

    @Slot(str)
    def success(self, message: str) -> None:
        self.notify.emit("success", str(message))

    @Slot(str)
    def warn(self, message: str) -> None:
        self.notify.emit("warning", str(message))

    @Slot(str)
    def error(self, message: str) -> None:
        self.notify.emit("error", str(message))

    # ── 信息 ────────────────────────────────────────────────

    @Property(str, constant=True)
    def version(self) -> str:
        from ..paths import APP_VERSION

        return APP_VERSION

    @Property(str, constant=True)
    def appName(self) -> str:  # noqa: N802
        from ..paths import APP_DISPLAY_NAME

        return APP_DISPLAY_NAME

    @Property(str, constant=True)
    def dataDir(self) -> str:  # noqa: N802
        return str(paths.data_dir())

    @Property("QVariantList", constant=True)
    def sources(self):  # noqa: N802
        return [dict(m) for m in SOURCE_META]

    # ── 窗口状态 ────────────────────────────────────────────

    @Slot(int, int)
    def saveWindowSize(self, width: int, height: int) -> None:  # noqa: N802
        self._config.set("window.width", int(width), autosave=False)
        self._config.set("window.height", int(height))

    @Slot(bool)
    def saveMaximized(self, value: bool) -> None:  # noqa: N802
        self._config.set("window.maximized", bool(value))

    @Slot(result=bool)
    def restoreMaximized(self) -> bool:  # noqa: N802
        return bool(self._config.get("window.maximized", False))

    def _available_size(self) -> Tuple[int, int]:
        """主屏可用区域（已排除任务栏）的逻辑像素尺寸；拿不到返回 ``(0, 0)``。"""
        try:
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                geo = screen.availableGeometry()
                return int(geo.width()), int(geo.height())
        except Exception as e:  # pragma: no cover - 依赖运行环境
            logger.debug("读取屏幕可用区域失败: %s", e)
        return 0, 0

    def _read_size(self, key: str, default: int) -> int:
        try:
            return int(self._config.get(key, default) or default)
        except (TypeError, ValueError):
            return int(default)

    @Slot(result=int)
    def minimumWidth(self) -> int:  # noqa: N802
        """窗口最小宽度。屏幕比它还窄时跟着屏幕缩 —— 否则最小值自己就把窗口顶出屏幕。"""
        return fit_window(WINDOW_FLOOR_W, self._available_size()[0], WINDOW_FLOOR_W)

    @Slot(result=int)
    def minimumHeight(self) -> int:  # noqa: N802
        return fit_window(WINDOW_FLOOR_H, self._available_size()[1], WINDOW_FLOOR_H)

    @Slot(result=int)
    def initialWidth(self) -> int:  # noqa: N802
        return fit_window(
            self._read_size("window.width", WINDOW_DEFAULT_W),
            self._available_size()[0],
            self.minimumWidth(),
        )

    @Slot(result=int)
    def initialHeight(self) -> int:  # noqa: N802
        return fit_window(
            self._read_size("window.height", WINDOW_DEFAULT_H),
            self._available_size()[1],
            self.minimumHeight(),
        )

    @Slot(int, int, int, int, result="QVariant")
    def fitWindow(self, want_w: int, want_h: int, floor_w: int, floor_h: int):  # noqa: N802
        """把任意窗口的「想要的尺寸 + 下限」夹进屏幕（设置 / 登录窗口用）。

        返回 ``{"width", "height"}``；QML 侧同时把它当作 minimumWidth/minimumHeight
        的来源 —— 低分辨率下**下限必须一起让位**，否则窗口被自己的最小值顶出屏幕。
        """
        avail_w, avail_h = self._available_size()
        w = fit_window(want_w, avail_w, floor_w)
        h = fit_window(want_h, avail_h, floor_h)
        return {"width": min(w, max(1, avail_w)) if avail_w > 0 else w,
                "height": min(h, max(1, avail_h)) if avail_h > 0 else h}

    @Slot(str, "QVariant")
    def savePref(self, key: str, value: Any) -> None:  # noqa: N802
        self._config.set(str(key), value)

    @Slot(str, "QVariant", result="QVariant")
    def loadPref(self, key: str, default: Any = None):  # noqa: N802
        return self._config.get(str(key), default)

    @Slot()
    def flush(self) -> None:
        self._config.save()


