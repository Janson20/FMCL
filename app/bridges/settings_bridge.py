"""设置桥 —— QML 侧的 `Settings` 上下文属性（阶段 3 任务 3.4）。

## 它是什么

设置页要四类东西：**草稿（9 个设置项）、主题列表、Java 扫描结果、保存/重启的结果**。
桥只做三件事：把服务的返回值变成 QML 能绑的属性与信号、把 QML 的动作转成服务调用
或任务提交、把 i18n 键翻成当前语言的一句话。**一句业务规则都不写**（红线 2）：
草稿语义、写盘顺序、成就映射、强调色校验、线程数夹取、Java 行文本拼法全在
`SettingsService`（`services/settings_service.py`）。

## 三处接线（都在 `use_engine()` 里，幂等）

1. **`Theme`**：主题/强调色的预览是"改内存不落盘"（M-Q1 的 B2），
   而内存里的颜色在 `services.palette.COLORS`；`Theme` 桥是它对 QML 的窗口。
   服务改完 `COLORS` 之后必须让 `Theme.refresh()` 重读一遍，否则界面上看不到预览。
2. **`Tr`**：语言的预览同理（`i18n_service.set_language` 只换字典，不写配置）。
3. **`Tasks`**：Java 扫描登记成任务种类 `settings.java_scan`（冷缓存时要翻目录，
   走任务才算后台任务，状态栏的后台指示才看得见）。

## 与旧界面的**状态栏文案**逐条对齐

旧设置在每次控件改动时就 `parent.set_status(...)`（`launcher_settings.py:989-1215`）。
草稿范式下改控件不再产生副作用，所以那批文案挪到**提交时**逐条重放
（同一批 i18n 键、同一批 `{占位符}`、同一个 level）—— 这样"改了 3 项"会依次发出
3 条状态信号，状态栏显示最后一条，与旧界面的最终观感一致。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Property, QObject, Signal, Slot

from services.i18n_service import _
from services.settings_service import (
    ACCENT_INVALID_KEY,
    DRAFT_KEYS,
    JAVA_MODE_LABEL_KEYS,
    THREADS_MAX,
    THREADS_MIN,
    SettingsService,
)

logger = logging.getLogger("app.bridges.settings_bridge")

#: 登记到 `Tasks` 桥的任务种类名（Java 扫描）
KIND_JAVA_SCAN = "settings.java_scan"

#: Java 扫描的四个状态（页面据此显示列表 / "扫描中" / 错误）
SCAN_IDLE = "idle"
SCAN_SCANNING = "scanning"
SCAN_READY = "ready"
SCAN_ERROR = "error"

#: 离开守卫用的域 id（`app/bridges/nav_bridge.py` 的 `setLeaveGuard`）。
#: 设置域下所有路由的前缀都是 `settings`。
DOMAIN = "settings"


class SettingsBridge(QObject):
    """上下文属性 `Settings`。"""

    #: 草稿（`fields`）或"有没有未保存改动"（`dirty`）变了。
    draftChanged = Signal()
    #: 主题列表变了（导入成功后）。
    themesChanged = Signal()
    #: Java 扫描状态 / 结果变了。
    javaChanged = Signal()
    #: 保存结果（参数 = 服务返回的结果字典，`ok`/`applied`/`errors`/`achievements`）。
    saveFinished = Signal(dict)
    #: 请界面退出（「保存并重启」的最后一步；`App.qml` 收到后 `Qt.quit()`）。
    restartRequested = Signal()
    #: 状态条文案（**已翻译**；由页面转给 `Shell.setStatus`）。
    statusMessage = Signal(str, str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._service: Optional[SettingsService] = None
        self._theme: Any = None
        self._tr: Any = None
        self._tasks: Any = None
        self._nav: Any = None
        self._kinds_registered = False

        # ── 普通 Python 属性（任务线程可能写它们；emit 走 Qt 的跨线程排队）──
        self._fields: Dict[str, Any] = {}
        self._dirty = False
        self._change_count = 0
        self._themes: List[Dict[str, Any]] = []
        self._languages: List[Dict[str, str]] = []
        self._java_modes: List[Dict[str, str]] = []
        self._java_rows: List[Dict[str, Any]] = []
        self._java_state = SCAN_IDLE
        self._java_error = ""
        self._busy = False

    # ─── 装配期注入 ─────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        """`main_qml.register_bridges` 在注册前调它：取设置服务并喂上"退出"出口。"""
        self._service = self._service_of(context, "settings")
        if self._service is None:
            logger.warning("设置服务不可用：设置页只会显示错误态")
            return
        # **刻意不在这里建草稿**（阶段 3 任务 3.4 人工验收反馈的修复）：
        # 装配发生得很早（那时 launcher 还没进上下文、用户也还没做任何设置），
        # 那时建的草稿会在用户于别处改设置（顶栏地球切语言）之后变成**过期基线**，
        # 而预览会把过期值按回内存 —— 症状是"改个主题，语言自己变回英文"。
        # 草稿由页面 `Component.onCompleted` 里的 `Settings.beginDraft()` 建（那一刻才准）。
        self._service.set_quit_handler(self.restartRequested.emit)
        self.refresh_all()

    def use_engine(self, engine: Any) -> None:
        """接上 `Theme` / `Tr`（预览刷新）、`Tasks`（Java 扫描）与 `Nav`（离开守卫）。"""
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        theme = registry.get("Theme")
        if theme is not None:
            self._theme = theme
        tr = registry.get("Tr")
        if tr is not None:
            self._tr = tr
        nav = registry.get("Nav")
        if nav is not None:
            self._nav = nav
            self._sync_guard()
        tasks = registry.get("Tasks")
        if tasks is not None:
            self._bind_tasks(tasks)

    def _bind_tasks(self, tasks: Any) -> None:
        """登记任务种类 + 订阅结束信号（幂等，见 `InstallBridge._bind_tasks` 的同一理由）。"""
        if tasks is self._tasks and self._kinds_registered:
            return
        self._tasks = tasks
        register = getattr(tasks, "register_kind", None)
        if callable(register) and self._service is not None:
            try:
                register(KIND_JAVA_SCAN, self._service.java_scan_task, True)
                self._kinds_registered = True
            except Exception as e:  # noqa: BLE001 - 登记不上只是扫不了，不该让装配失败
                logger.warning("登记任务种类 %s 失败: %s", KIND_JAVA_SCAN, e)
        for signal_name, handler in (
            ("taskFinished", self._on_task_finished),
            ("taskFailed", self._on_task_failed),
        ):
            signal = getattr(tasks, signal_name, None)
            if signal is None:
                logger.warning("Tasks 桥没有 %s 信号，Java 扫描结果不会回来", signal_name)
                continue
            try:
                signal.connect(handler)
            except Exception as e:  # noqa: BLE001
                logger.warning("接 Tasks.%s 失败: %s", signal_name, e)

    @staticmethod
    def _service_of(context: Any, name: str) -> Optional[SettingsService]:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001 - 取不到不该让整页打不开
            logger.warning("设置页取服务 %s 失败: %s", name, e)
            return None

    # ─── 草稿（B1 / B2） ────────────────────────────────────

    def refresh_all(self) -> None:
        """从服务重读草稿 / 主题列表 / 语言列表 / Java 模式表。"""
        if self._service is None:
            return
        self._fields = self._service.current_draft()
        self._dirty = self._service.dirty()
        self._change_count = len(self._service.changes())
        self._languages = self._service.language_options()
        self._java_modes = [
            {"value": item["value"], "label": _(item["label_key"])} for item in self._service.java_modes()
        ]
        self._refresh_themes()
        self.draftChanged.emit()
        self._sync_guard()

    def _refresh_themes(self) -> None:
        if self._service is None:
            self._themes = []
            return
        # 用户导入的主题带一个后缀标记（旧实现 `f"{name} {_('settings_theme_user_tag')}"`）
        tag = _("settings_theme_user_tag")
        self._themes = [
            {
                "name": item["name"],
                "label": item["label"] if not item["is_user"] else f"{item['name']} {tag}",
                "source": item["source"],
                "description": item["description"],
                "is_user": item["is_user"],
            }
            for item in self._service.themes()
        ]
        self.themesChanged.emit()

    @Property("QVariantMap", notify=draftChanged)
    def fields(self) -> Dict[str, Any]:  # noqa: N802 - QML 属性名
        """草稿里的 9 个键（界面控件全部绑它）。"""
        return self._fields

    @Property(bool, notify=draftChanged)
    def dirty(self) -> bool:  # noqa: N802
        """有没有未保存的改动（动作条、顶栏标记、离开守卫都看它）。"""
        return self._dirty

    @Property(int, notify=draftChanged)
    def changeCount(self) -> int:  # noqa: N802
        return self._change_count

    @Property("QVariantList", notify=draftChanged)
    def changes(self) -> List[str]:  # noqa: N802
        if self._service is None:
            return []
        return sorted(self._service.changes())

    @Property("QVariantList", notify=themesChanged)
    def themes(self) -> List[Dict[str, Any]]:  # noqa: N802
        return self._themes

    @Property("QVariantList", notify=draftChanged)
    def languages(self) -> List[Dict[str, str]]:  # noqa: N802
        return self._languages

    @Property("QVariantList", notify=draftChanged)
    def javaModes(self) -> List[Dict[str, str]]:  # noqa: N802
        return self._java_modes

    @Property(int, constant=True)
    def threadsMin(self) -> int:  # noqa: N802
        return THREADS_MIN

    @Property(int, constant=True)
    def threadsMax(self) -> int:  # noqa: N802
        return THREADS_MAX

    @Property(bool, constant=True)
    def available(self) -> bool:
        """服务在不在（缺席时页面走错误态）。"""
        return self._service is not None

    @Slot(result=bool)
    def beginDraft(self) -> bool:  # noqa: N802
        """页面出现时调：吃已有草稿（B1：切页面回来时改动还在）。"""
        if self._service is None:
            return False
        self._service.begin_draft()
        self.refresh_all()
        return True

    @Slot(str, "QVariant", result="QVariantMap")
    def setField(self, key: str, value: Any) -> Dict[str, Any]:  # noqa: N802
        """改一项草稿。返回服务的判定结果（`ok` / `error` / `dirty` / `preview`）。"""
        if self._service is None:
            return {"ok": False, "error": "service_unavailable", "dirty": False, "preview": False}
        result = self._service.set_draft(key, value)
        if result.get("preview"):
            self._refresh_preview()
        if not result.get("ok"):
            self.statusMessage.emit(self._error_text(result.get("error", "")), "error")
        self._sync_draft_state()
        return result

    @Slot(str, result=str)
    def validateAccent(self, text: str) -> str:  # noqa: N802
        """强调色的即时校验：返回空串表示合法，否则返回给用户看的一句话。"""
        if self._service is None:
            return ""
        try:
            from services.settings_service import normalize_accent

            normalize_accent(text)
        except ValueError as e:
            return self._error_text(str(e))
        except Exception as e:  # noqa: BLE001
            return str(e)
        return ""

    @Slot()
    def discardDraft(self) -> None:  # noqa: N802
        """「取消」/ 离开确认丢弃：还原预览 + 清空草稿。"""
        if self._service is None:
            return
        self._service.discard()
        self._refresh_preview()
        self._sync_draft_state()

    @Slot(result="QVariantMap")
    def commitDraft(self) -> Dict[str, Any]:  # noqa: N802
        """「保存」：写盘 + 触发成就 + 逐个键重放状态栏文案。"""
        if self._service is None:
            return {"ok": False, "changed": False, "applied": [], "errors": ["service_unavailable"]}
        result = self._service.commit()
        for key in result.get("applied", []):
            text, level = self._status_for(key)
            if text:
                self.statusMessage.emit(text, level)
        if not result.get("applied"):
            self.statusMessage.emit(_("settings_saved"), "info")
        for key in result.get("errors", []):
            self.statusMessage.emit(_("backup_settings_save_failed", error=key), "error")
        # 提交后预览与已保存值一致（服务已经写盘），但仍要刷新一次：
        # 主题/语言在"预览→落盘"之间没有任何变化，而 QML 侧的只读快照要对齐。
        self.refresh_all()
        self.saveFinished.emit(dict(result))
        return result

    @Slot(result="QVariantMap")
    def saveAndRestart(self) -> Dict[str, Any]:  # noqa: N802
        """「保存并重启」（B5）：先落盘草稿，再拉新进程，最后请求退出。"""
        if self._service is None:
            return {"ok": False, "stage": "service", "error": "service_unavailable"}
        self._busy = True
        result = self._service.save_and_restart()
        self._busy = False
        if not result.get("ok"):
            stage = result.get("stage", "")
            if stage == "commit":
                for key in result.get("errors", []):
                    self.statusMessage.emit(_("backup_settings_save_failed", error=key), "error")
            elif stage == "spawn":
                self.statusMessage.emit(
                    _("settings_restart_failed", error=result.get("error", "")), "error"
                )
            else:
                self.statusMessage.emit(_("settings_restart_failed", error=stage), "error")
            self.refresh_all()
            return result
        self.refresh_all()
        return result

    def _sync_draft_state(self) -> None:
        if self._service is None:
            return
        self._fields = self._service.current_draft()
        self._dirty = self._service.dirty()
        self._change_count = len(self._service.changes())
        self.draftChanged.emit()
        self._sync_guard()

    def _sync_guard(self) -> None:
        """把"设置域有未保存改动"告诉导航桥（M-Q1 的 B6：离开时提示一次）。

        守卫登记在**桥**里而不是页面里：草稿活在服务里、生命周期比页面长
        （用户离开再回来草稿还在），页面被销毁时不该顺手把守卫也撤掉。
        """
        if self._nav is None:
            return
        setter = getattr(self._nav, "setLeaveGuard", None)
        if not callable(setter):
            return
        try:
            setter(DOMAIN, bool(self._dirty))
        except Exception as e:  # noqa: BLE001 - 守卫登记失败只是少了提示，不该挡住设置页
            logger.warning("登记离开守卫失败: %s", e)

    def _refresh_preview(self) -> None:
        """让 QML 侧的 `Theme` / `Tr` 只读快照跟上内存里的预览值。"""
        def _call(obj: Any, name: str) -> None:
            fn = getattr(obj, name, None)
            if callable(fn):
                try:
                    fn()
                except Exception as e:  # noqa: BLE001 - 刷新失败只是预览慢一拍
                    logger.warning("%s.%s() 失败: %s", type(obj).__name__, name, e)

        if self._theme is not None:
            _call(self._theme, "refresh")
        if self._tr is not None:
            _call(self._tr, "refresh")

    # ─── 状态栏文案（逐条对齐旧实现） ───────────────────────

    def _error_text(self, error: str) -> str:
        """把服务给的错误标识翻成一句话。

        服务的错误标识有两种：**i18n 键**（强调色非法，旧界面就有这句）
        与 `unknown_<kind>:<值>` 形式（主题/语言/Java 模式的非法取值 ——
        旧界面的下拉框里不可能出现非法值，所以旧界面没有对应文案）。
        """
        if not error:
            return ""
        if error == ACCENT_INVALID_KEY:
            return _(ACCENT_INVALID_KEY)
        if error.startswith("unknown_"):
            return _("plugin_state_error")
        return error

    def _status_for(self, key: str) -> Tuple[str, str]:
        """某个键提交后该发哪句状态栏文案（键、文案、级别的对应关系见模块文档）。"""
        value = self._fields.get(key)
        if key == "minimize_on_game_launch":
            return (
                _("settings_minimize_enabled") if value else _("settings_minimize_disabled"),
                "success" if value else "info",
            )
        if key == "mirror_enabled":
            return (
                _("settings_mirror_enabled") if value else _("settings_mirror_disabled"),
                "success" if value else "info",
            )
        if key == "download_threads":
            return _("settings_threads", threads=value), "info"
        if key == "language":
            name = dict((item["code"], item["name"]) for item in self._languages).get(str(value), str(value))
            return _("settings_language_changed", lang=name), "info"
        if key == "theme_name":
            return _("settings_theme_changed", theme=str(value)), "info"
        if key == "accent_color":
            return (
                (_("settings_accent_applied"), "success") if value else (_("settings_accent_cleared"), "info")
            )
        if key == "dynamic_version_theme":
            return (
                _("settings_dynamic_enabled") if value else _("settings_dynamic_disabled"),
                "info",
            )
        if key == "java_mode":
            label = {item["value"]: item["label"] for item in self._java_modes}.get(
                str(value), JAVA_MODE_LABEL_KEYS.get(str(value), str(value))
            )
            return _("settings_java_changed", mode=label), "info"
        if key == "java_custom_path":
            return _("settings_java_path_set", path=str(value or "")), "success"
        return "", "info"

    # ─── 主题（M-07 / M-08 / M-10） ─────────────────────────

    @Slot(str, result=bool)
    def importTheme(self, path: str) -> bool:  # noqa: N802
        """导入主题 .json（B4：立即执行，不进草稿）。"""
        if self._service is None:
            return False
        ok, message = self._service.import_theme(path)
        self.statusMessage.emit(message, "success" if ok else "error")
        if ok:
            self._refresh_themes()
        return ok

    @Slot(result=str)
    def randomAccent(self) -> str:  # noqa: N802
        """生成一个随机强调色并写进草稿（旧「随机」按钮：生成 + 立即应用）。"""
        if self._service is None:
            return ""
        color = self._service.random_accent()
        self.setField("accent_color", color)
        self.statusMessage.emit(_("settings_accent_random_applied", color=color), "success")
        return color

    @Slot(result="QVariantMap")
    def describeDraft(self) -> Dict[str, Any]:
        """自检/测试用：草稿、脏标志、改动清单。"""
        if self._service is None:
            return {"available": False}
        return {
            "available": True,
            "dirty": self._service.dirty(),
            "fields": self._service.current_draft(),
            "changes": sorted(self._service.changes()),
            "achievements": self._service.fired_achievements(),
        }

    # ─── Java（M-03 / M-04 / M-05） ────────────────────────

    @Property("QVariantList", notify=javaChanged)
    def javaRuntimes(self) -> List[Dict[str, Any]]:  # noqa: N802
        return self._java_rows

    @Property(str, notify=javaChanged)
    def javaScanState(self) -> str:  # noqa: N802
        return self._java_state

    @Property(str, notify=javaChanged)
    def javaError(self) -> str:  # noqa: N802
        return self._java_error

    @Slot()
    def scanJava(self) -> int:  # noqa: N802
        """扫描系统 Java（走 `Tasks` 桥；返回任务 id，-1 表示提交失败）。"""
        if self._service is None or self._tasks is None:
            return -1
        submit = getattr(self._tasks, "submit", None)
        if not callable(submit):
            return -1
        self._java_state = SCAN_SCANNING
        self._java_error = ""
        self.javaChanged.emit()
        try:
            return int(submit(KIND_JAVA_SCAN, {}))
        except Exception as e:  # noqa: BLE001 - 提交失败要有可见原因
            logger.warning("提交 Java 扫描任务失败: %s", e)
            self._java_state = SCAN_ERROR
            self._java_error = str(e)
            self.javaChanged.emit()
            return -1

    @Slot(dict)
    def _on_task_finished(self, payload: dict) -> None:
        """任务结束（主线程定时器里发出）。只认本桥登记的那一种任务。"""
        if not self._is_our_task(payload):
            return
        result = payload.get("result") or {}
        if not isinstance(result, dict):
            self._java_state = SCAN_ERROR
            self._java_error = str(result)
            self.javaChanged.emit()
            return
        self._java_rows = list(result.get("rows") or [])
        error = str(result.get("error") or "")
        self._java_state = SCAN_ERROR if error else SCAN_READY
        self._java_error = error
        self.javaChanged.emit()
        if error:
            self.statusMessage.emit(error, "error")

    @Slot(dict)
    def _on_task_failed(self, payload: dict) -> None:
        if not self._is_our_task(payload):
            return
        self._java_state = SCAN_ERROR
        self._java_error = str(payload.get("error") or payload.get("message") or "")
        self.javaChanged.emit()
        if self._java_error:
            self.statusMessage.emit(self._java_error, "error")

    @staticmethod
    def _is_our_task(payload: dict) -> bool:
        """任务载荷是不是本桥登记的那一种。

        载荷字段是 `qt_tasks.py` 发的：`{"taskId", "name", "result"/"error", "ok", …}`
        —— 种类名在 **`name`** 里（不是 `kind`，第一版就是照 `install_bridge` 猜错的）。
        """
        name = ""
        if isinstance(payload, dict):
            name = str(payload.get("name") or "")
        return name == KIND_JAVA_SCAN

    @Slot()
    def refreshJavaStatus(self) -> None:  # noqa: N802
        """旧「刷新扫描」的状态栏提示（`settings_java_scan_refresh`）。"""
        self.statusMessage.emit(_("settings_java_scan_refresh"), "info")

    # ─── 自检 ───────────────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        return {
            "available": self._service is not None,
            "dirty": self._dirty,
            "changeCount": self._change_count,
            "themes": len(self._themes),
            "languages": len(self._languages),
            "javaRows": len(self._java_rows),
            "javaState": self._java_state,
            "keys": list(DRAFT_KEYS),
        }


__all__ = [
    "DOMAIN",
    "KIND_JAVA_SCAN",
    "SCAN_ERROR",
    "SCAN_IDLE",
    "SCAN_READY",
    "SCAN_SCANNING",
    "SettingsBridge",
]
