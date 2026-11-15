"""成就页 QML 桥。

链路：

    qml/pages/achievements/*.qml
        -> Achievements.xxx
    app/bridges/achievements_bridge.py（本模块）
        -> services/achievement_service.py（统计 / 同步 / 重置编排）
        -> services/achievement_engine.py（进程级 SQLite 引擎）

线程约定与 3.20 相同：长活全部走 AppContext 的 TaskRunner，worker 只通过排队
信号投递结果，真正改 QObject 属性的只有主线程的 _deliver()。解锁回调由
引擎在任意线程调用，本模块只组装普通字典并排队，不在 worker 里 emit QML 信号、
不读配置、不发 HTTP。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Qt, Signal, Slot

from services.i18n_service import _

logger = logging.getLogger("app.bridges.achievements_bridge")

#: 分类 → 图标名（qml/assets/icons/*.svg 的 base name）。
#: 旧引擎里的分类 icon 是 emoji；QML 红线禁 emoji，桥必须在边界上换掉。
CATEGORY_ICONS: Dict[str, str] = {
    "gamer": "game",
    "modder": "mods",
    "server": "server",
    "backup": "backup",
    "online": "online",
    "agent": "agent",
    "personalize": "theme",
    "advanced": "rocket",
    "music": "music",
}

#: 成就 id → 图标名。按旧定义里的语义逐项换成单色 SVG，既不丢辨识度也不出现 emoji。
ACHIEVEMENT_ICONS: Dict[str, str] = {
    "gamer_first_launch": "game",
    "gamer_version_collector": "package",
    "gamer_cross_era": "timer",
    "gamer_retro": "game",
    "gamer_frontier": "rocket",
    "gamer_launch_master": "trophy",
    "modder_first_mod": "mods",
    "modder_mod_expert": "settings-gear",
    "modder_full_house": "home",
    "modder_diy": "tool",
    "modder_lazy": "download",
    "modder_dependency_expert": "link",
    "server_first_server": "server",
    "server_admin": "crown",
    "server_modpack_server": "modpack",
    "server_uptime": "timer",
    "server_cross_play": "link",
    "backup_first_backup": "save",
    "backup_time_machine": "history",
    "backup_mania": "saves",
    "backup_automation": "repeat",
    "backup_export_share": "upload",
    "online_first_online": "online",
    "online_room_owner": "home",
    "online_guest": "door",
    "agent_first_chat": "chat",
    "agent_nlp_master": "target",
    "agent_multi_turn": "repeat",
    "agent_terminal_warrior": "bolt",
    "personalize_skin": "account",
    "personalize_rename": "edit",
    "personalize_theme_master": "theme",
    "personalize_my_color": "dice",
    "personalize_version_theme": "rainbow",
    "personalize_import_theme": "download",
    "advanced_mirror": "rocket",
    "advanced_multithread": "bolt",
    "advanced_polyglot": "language",
    "advanced_crash_analyst": "search",
    "advanced_log_hunter": "note",
    "advanced_updater": "refresh",
    "advanced_checkin": "calendar",
    "music_first_play": "music",
    "music_play_count": "headphone",
    "music_mode_master": "shuffle",
    "music_mini_mode": "chevron-down",
    "music_volume_tweaker": "slider",
}

_ROMAN = ("", "I", "II", "III", "IV", "V", "VI")


def stage_label(name: str, stage: int, max_stage: int) -> str:
    """成就名 + 罗马数字阶段。旧定义里的 stage_names 是中文硬编码；QML 不直接用它。"""
    if max_stage <= 1 or stage <= 0:
        return name
    numeral = _ROMAN[stage] if 0 < stage < len(_ROMAN) else str(stage)
    return f"{name} {numeral}"


class AchievementsBridge(QObject):
    """上下文属性 Achievements。"""

    changed = Signal()
    #: 引擎解锁回调 → 壳层弹 Toast。payload = {achievementId,title,subtitle,icon,stage}
    unlockNotice = Signal("QVariantMap")
    #: 一般提示（同步成功/失败、需要登录）→ 壳层 Toast
    noticeRequested = Signal(str, str)
    posted = Signal("QVariantMap")

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._service: Any = None
        self._tasks: Any = None
        self._config: Any = None
        self._engine: Any = None
        self._callback: Any = None
        self._handles: Dict[str, Any] = {}
        self._closing = False

        self._state = "loading"
        self._error = ""
        self._categories: List[Dict[str, Any]] = []
        self._summary: Dict[str, Any] = {"total": 0, "unlocked": 0, "percent": 0.0}
        self._last_sync_at = 0.0
        self._logged_in = False
        self._sync_state = "idle"
        self._status_text = ""
        self._status_level = "info"
        self._push_running = False

        self.posted.connect(self._deliver, Qt.QueuedConnection)

    # ─── 装配 / 生命周期 ────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        self._context = context
        self._tasks = getattr(context, "tasks", None) if context is not None else None
        self._config = getattr(context, "config", None) if context is not None else None
        if context is not None:
            getter = getattr(context, "try_get", None)
            if callable(getter):
                try:
                    self._service = getter("achievement")
                except Exception as e:  # noqa: BLE001
                    logger.warning("取成就服务失败: %s", e)
        self.attach_engine()
        self.refresh()

    def detach(self) -> None:
        """退出链：摘掉引擎回调并取消仍在跑的任务。"""
        self._closing = True
        self._detach_engine()
        for key, handle in list(self._handles.items()):
            try:
                handle.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._handles.clear()
        self._push_running = False

    def attach_engine(self, engine: Any = None) -> bool:
        """把本桥的解锁回调挂到引擎上（幂等，引擎重建后会换挂）。

        StartupController 在 factory 刚返回时会把引擎显式传进来；QML 装配早期
        还没初始化引擎时则回退到服务单例。
        """
        if self._service is None or self._closing:
            return False
        if engine is None:
            try:
                engine = self._service.engine()
            except Exception as e:  # noqa: BLE001
                logger.warning("取成就引擎失败: %s", e)
                return False
        if engine is None:
            return False
        if engine is self._engine and self._callback is not None:
            return True
        self._detach_engine()
        callback = self._on_engine_unlock
        register = getattr(engine, "register_unlock_callback", None)
        if not callable(register):
            logger.warning("成就引擎没有 register_unlock_callback，无法接解锁提示")
            return False
        try:
            register(callback)
        except Exception as e:  # noqa: BLE001
            logger.warning("注册成就解锁回调失败: %s", e)
            return False
        self._engine = engine
        self._callback = callback
        return True

    def _detach_engine(self) -> None:
        engine = self._engine
        callback = self._callback
        self._engine = None
        self._callback = None
        if engine is None or callback is None:
            return
        unregister = getattr(engine, "unregister_unlock_callback", None)
        if callable(unregister):
            try:
                unregister(callback)
            except Exception as e:  # noqa: BLE001
                logger.warning("注销成就解锁回调失败: %s", e)

    # ─── 只读属性 ───────────────────────────────────────────

    @Property(bool, notify=changed)
    def available(self) -> bool:
        return self._service is not None

    @Property(str, notify=changed)
    def state(self) -> str:
        return self._state

    @Property(str, notify=changed)
    def error(self) -> str:
        return self._error

    @Property("QVariantList", notify=changed)
    def categories(self) -> List[Dict[str, Any]]:
        return self._categories

    @Property("QVariantMap", notify=changed)
    def summary(self) -> Dict[str, Any]:
        return dict(self._summary)

    @Property(float, notify=changed)
    def lastSyncAt(self) -> float:  # noqa: N802
        return self._last_sync_at

    @Property(bool, notify=changed)
    def loggedIn(self) -> bool:  # noqa: N802
        return self._logged_in

    @Property(str, notify=changed)
    def syncState(self) -> str:  # noqa: N802
        return self._sync_state

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._sync_state in ("syncing", "resetting")

    @Property(str, notify=changed)
    def statusText(self) -> str:  # noqa: N802
        return self._status_text

    @Property(str, notify=changed)
    def statusLevel(self) -> str:  # noqa: N802
        return self._status_level

    # ─── 数据 ───────────────────────────────────────────────

    @Slot()
    def refresh(self) -> None:  # noqa: N802
        if self._closing:
            return
        self._refresh_login()
        if self._service is None:
            self._state = "error"
            self._error = _("ach_engine_unavailable")
            self.changed.emit()
            return
        try:
            raw = self._service.load_progress()
        except Exception as e:  # noqa: BLE001
            logger.warning("读取成就数据失败: %s", e)
            self._state = "error"
            self._error = str(e)
            self.changed.emit()
            return
        if raw is None:
            self._state = "error"
            self._error = _("ach_engine_unavailable")
            self.changed.emit()
            return
        self._categories = self._build_categories(raw)
        try:
            summary = self._service.summarize(raw)
            self._summary = {
                "total": int(summary.total),
                "unlocked": int(summary.unlocked),
                "percent": float(summary.percent),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning("汇总成就数据失败: %s", e)
            self._summary = {"total": 0, "unlocked": 0, "percent": 0.0}
        try:
            ts = self._service.last_sync_time()
            self._last_sync_at = float(ts or 0.0)
        except Exception:  # noqa: BLE001
            self._last_sync_at = 0.0
        self._state = "ready"
        self._error = ""
        self.changed.emit()

    def _build_categories(self, raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for cat in raw or []:
            if not isinstance(cat, dict):
                continue
            cat_id = str(cat.get("category") or "")
            meta = cat.get("category_meta") or {}
            name_key = str(meta.get("i18n_key") or "ach_category_title")
            icon = CATEGORY_ICONS.get(cat_id, "trophy")
            items = [self._item(row, cat_id, name_key, icon) for row in (cat.get("achievements") or [])]
            unlocked = sum(1 for item in items if item["unlocked"])
            total = len(items)
            out.append(
                {
                    "id": cat_id,
                    "nameKey": name_key,
                    "icon": icon,
                    "unlocked": unlocked,
                    "total": total,
                    "percent": round(unlocked / total * 100, 1) if total > 0 else 0.0,
                    "achievements": items,
                }
            )
        return out

    def _item(self, row: Dict[str, Any], cat_id: str, name_key: str, cat_icon: str) -> Dict[str, Any]:
        data = dict(row or {})
        aid = str(data.get("id") or "")
        max_stage = int(data.get("max_stage") or 0)
        stage = int(data.get("progress_stage") or 0)
        stages = list(data.get("stages") or [])
        stage_rows = [
            {
                "index": index + 1,
                "threshold": int(threshold or 0),
                "unlocked": stage >= index + 1,
                "current": stage == index + 1,
            }
            for index, threshold in enumerate(stages)
        ]
        next_threshold = data.get("next_threshold")
        return {
            "id": aid,
            "category": cat_id,
            "categoryNameKey": name_key,
            "categoryIcon": cat_icon,
            "nameKey": str(data.get("i18n_key") or aid),
            "descKey": str(data.get("desc_i18n_key") or ""),
            "icon": ACHIEVEMENT_ICONS.get(aid, cat_icon),
            "stage": stage,
            "maxStage": max_stage,
            "current": int(data.get("progress_current") or 0),
            "nextThreshold": int(next_threshold) if next_threshold is not None else 0,
            "percent": float(data.get("progress_percent") or 0.0),
            "unlocked": stage > 0,
            "maxed": max_stage > 0 and stage >= max_stage,
            "stages": stage_rows,
        }

    @Slot(str, result="QVariantMap")
    def category(self, category_id: str) -> Dict[str, Any]:  # noqa: N802
        wanted = str(category_id or "")
        for cat in self._categories:
            if str(cat.get("id") or "") == wanted:
                return cat
        return {}

    @Slot(str, result="QVariantMap")
    def achievement(self, achievement_id: str) -> Dict[str, Any]:  # noqa: N802
        wanted = str(achievement_id or "")
        for cat in self._categories:
            for item in cat.get("achievements") or []:
                if str(item.get("id") or "") == wanted:
                    return item
        return {}

    # ─── 登录 / 同步 / 重置 ─────────────────────────────────

    def _refresh_login(self) -> None:
        self._logged_in = bool(self._token())

    def _token(self) -> str:
        context = self._context
        if context is not None:
            try:
                launcher = context.try_get("launcher")
            except Exception:
                launcher = None
            getter = getattr(launcher, "get_jdz_token", None) if launcher is not None else None
            if callable(getter):
                try:
                    token = str(getter() or "").strip()
                    if token:
                        return token
                except Exception as e:  # noqa: BLE001
                    logger.warning("读取净读 Token 失败: %s", e)
        config = self._config
        if config is not None:
            try:
                return str(getattr(config, "jdz_token", "") or "").strip()
            except Exception as e:  # noqa: BLE001
                logger.warning("读取配置中的净读 Token 失败: %s", e)
        return ""

    @Slot()
    def syncNow(self) -> None:  # noqa: N802
        if self._service is None or self._engine is None or self.busy:
            return
        token = self._token()
        if not token:
            self._require_login()
            return
        self._sync_state = "syncing"
        self._status_text = _("ach_sync_status_syncing")
        self._status_level = "info"
        self.changed.emit()
        engine = self._engine

        def work() -> Any:
            return self._service.sync_with_engine(engine, token)

        self._submit(
            "achievements.sync",
            work,
            lambda result: self._post({"kind": "sync-done", "result": bool(result)}),
            lambda error: self._post({"kind": "sync-error", "error": str(error)}),
        )

    @Slot()
    def resetCloud(self) -> None:  # noqa: N802
        if self._service is None or self.busy:
            return
        token = self._token()
        if not token:
            self._require_login()
            return
        self._sync_state = "resetting"
        self._status_text = _("ach_sync_status_resetting")
        self._status_level = "info"
        self.changed.emit()

        def work() -> Any:
            return self._service.reset_cloud(token)

        self._submit(
            "achievements.reset-cloud",
            work,
            lambda result: self._post({"kind": "reset-cloud-done", "result": bool(result)}),
            lambda error: self._post({"kind": "reset-cloud-error", "error": str(error)}),
        )

    @Slot()
    def resetLocal(self) -> None:  # noqa: N802
        if self._service is None or self.busy:
            return
        self._sync_state = "resetting"
        self._status_text = _("ach_sync_status_resetting")
        self._status_level = "info"
        self.changed.emit()

        def work() -> Any:
            return self._service.reset_local()

        self._submit(
            "achievements.reset-local",
            work,
            lambda result: self._post({"kind": "reset-local-done", "result": result}),
            lambda error: self._post({"kind": "reset-local-error", "error": str(error)}),
        )

    def _require_login(self) -> None:
        self._refresh_login()
        self._status_text = _("ach_sync_login_title")
        self._status_level = "warning"
        self.changed.emit()
        self.noticeRequested.emit(_("ach_sync_login_title"), "warning")

    # ─── 任务线程 → 主线程 ──────────────────────────────────

    def _submit(self, key: str, work: Any, done: Any, failed: Any) -> None:
        if self._tasks is None:
            try:
                result = work()
            except Exception as e:  # noqa: BLE001
                failed(e)
            else:
                done(result)
            return
        try:
            handle = self._tasks.submit(work, name=key, on_done=done, on_error=failed)
        except Exception as e:  # noqa: BLE001
            failed(e)
            return
        self._handles[key] = handle

    def _post(self, payload: Dict[str, Any]) -> None:
        if self._closing:
            return
        try:
            self.posted.emit(payload)
        except RuntimeError:
            pass

    def _deliver(self, payload: Dict[str, Any]) -> None:
        if self._closing:
            return
        kind = str(payload.get("kind", "") or "")
        if kind in (
            "sync-done",
            "sync-error",
            "reset-cloud-done",
            "reset-cloud-error",
            "reset-local-done",
            "reset-local-error",
        ):
            key = {
                "sync-done": "achievements.sync",
                "sync-error": "achievements.sync",
                "reset-cloud-done": "achievements.reset-cloud",
                "reset-cloud-error": "achievements.reset-cloud",
                "reset-local-done": "achievements.reset-local",
                "reset-local-error": "achievements.reset-local",
            }.get(kind, "")
            self._handles.pop(key, None)
            self._sync_state = "idle"
            if kind == "sync-done" and payload.get("result"):
                self._last_sync_at = time.time()
                self._status_text = _("ach_sync_success")
                self._status_level = "success"
                self.refresh()
                self.noticeRequested.emit(_("ach_sync_success"), "success")
                return
            if kind == "reset-cloud-done" and payload.get("result"):
                self._status_text = _("ach_reset_cloud_success")
                self._status_level = "success"
                self.changed.emit()
                self.noticeRequested.emit(_("ach_reset_cloud_success"), "success")
                return
            if kind == "reset-local-done":
                self._status_text = _("ach_reset_local_success")
                self._status_level = "success"
                self.refresh()
                self.noticeRequested.emit(_("ach_reset_local_success"), "success")
                return
            self._status_text = _("ach_sync_failed")
            self._status_level = "error"
            self.changed.emit()
            self.noticeRequested.emit(_("ach_sync_failed"), "error")
            return

        if kind == "push-done":
            self._push_running = False
            if payload.get("result"):
                self._last_sync_at = time.time()
                self.changed.emit()
            return
        if kind == "push-error":
            self._push_running = False
            logger.warning("解锁后推送成就云存档失败: %s", payload.get("error", ""))
            return

        if kind == "unlock":
            notice = dict(payload.get("payload") or {})
            if notice:
                self.unlockNotice.emit(notice)
                self.refresh()
                self._start_push()
            return

    # ─── 解锁通知 ───────────────────────────────────────────

    def _on_engine_unlock(self, ach_def: Any, stage: int, stage_name: str) -> None:
        """引擎回调（任意线程）：只组装字典 + 排队，不碰 QObject / 配置 / HTTP。"""
        if self._closing or self._service is None:
            return
        try:
            notice = self._service.unlock_notice(ach_def, int(stage), str(stage_name or ""))
            title = _(notice.i18n_key)
            max_stage = len(getattr(ach_def, "stages", []) or [])
            subtitle = stage_label(title, int(stage), max_stage) if max_stage > 1 else _("ach_unlocked")
            icon = ACHIEVEMENT_ICONS.get(str(notice.achievement_id), "trophy")
            self._post(
                {
                    "kind": "unlock",
                    "payload": {
                        "achievementId": str(notice.achievement_id),
                        "title": title,
                        "subtitle": subtitle,
                        "icon": icon,
                        "stage": int(stage),
                    },
                }
            )
        except Exception as e:  # noqa: BLE001 - 通知失败不能影响成就写库
            logger.warning("组装成就解锁通知失败: %s", e)

    def _start_push(self) -> None:
        """解锁后顺手上传一次（旧 F-08 的行为）。失败只记日志。"""
        if self._push_running or self._service is None or self._tasks is None:
            return
        token = self._token()
        if not token:
            return
        self._push_running = True

        def work() -> Any:
            return self._service.push_sync(token)

        self._submit(
            "achievements.push",
            work,
            lambda result: self._post({"kind": "push-done", "result": bool(result)}),
            lambda error: self._post({"kind": "push-error", "error": str(error)}),
        )
