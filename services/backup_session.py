"""存档备份会话状态机（阶段 3 任务 3.14）。

旧实现 ``ui/app_backup.py`` 把存档扫描、备份列表、任务队列、自动备份开关和
备份设置都写在 Tk 页面/弹窗里；``services/backup_manager.py`` 已经拥有真正的
文件操作。本模块补的是 QML 会话层：**世界列表 / 当前世界 / 备份列表 / 任务状态 /
设置草稿**，并把“设置值如何落盘、保存后如何验证”收口在一处。

线程约定：本模块不起线程、不碰 Qt、不做 i18n。扫描与文件操作由桥交给
``TaskRunner``，任务函数只返回普通字典；所有状态更新由桥在主线程调用。
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.backup_manager import BackupEntry, BackupManager

logger = logging.getLogger("services.backup_session")

STATE_IDLE = "idle"
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_EMPTY = "empty"
STATE_ERROR = "error"

COMPRESS_LEVELS = (1, 3, 6, 9)
MAX_OPTIONS = (5, 10, 20, 30, 50, 0)
RESTORE_MODES = ("rename", "overwrite", "trash")


def _norm_path(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return os.path.normcase(os.path.abspath(os.path.expanduser(text)))


def _int_choice(value: Any, options: tuple, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number in options else default


class BackupSession:
    """一个存档备份页的会话状态（桥只读属性、只调方法）。"""

    def __init__(self, config: Any, achievement: Any = None) -> None:
        self._config = config
        self._achievement = achievement
        self._manager: Optional[BackupManager] = None

        self._worlds: List[Dict[str, Any]] = []
        self._worlds_state = STATE_IDLE
        self._worlds_error = ""
        self._selected_world = ""
        self._backups: List[BackupEntry] = []
        self._backups_state = STATE_IDLE
        self._backups_error = ""

        self._auto_launch = bool(getattr(config, "backup_auto_launch", False)) if config is not None else False
        self._auto_exit = bool(getattr(config, "backup_auto_exit", False)) if config is not None else False

        self._settings_open = False
        self._settings_snapshot: Dict[str, Any] = {}
        self._settings_draft: Dict[str, Any] = {}

    # ─── 基础属性 ───────────────────────────────────────────

    @property
    def available(self) -> bool:
        return self._config is not None

    @property
    def worlds(self) -> List[Dict[str, Any]]:
        return [dict(item) for item in self._worlds]

    @property
    def worlds_state(self) -> str:
        return self._worlds_state

    @property
    def worlds_error(self) -> str:
        return self._worlds_error

    @property
    def selected_world(self) -> str:
        return self._selected_world

    @property
    def backups(self) -> List[BackupEntry]:
        return list(self._backups)

    @property
    def backups_state(self) -> str:
        return self._backups_state

    @property
    def backups_error(self) -> str:
        return self._backups_error

    @property
    def auto_launch(self) -> bool:
        return self._auto_launch

    @property
    def auto_exit(self) -> bool:
        return self._auto_exit

    @property
    def default_backup_dir(self) -> str:
        if self._config is None:
            return ""
        base = getattr(self._config, "base_dir", None)
        if base is None:
            base = Path.cwd()
        return str(Path(base) / "backups")

    @property
    def backup_root(self) -> str:
        try:
            return str(self._manager_instance().backup_root)
        except Exception as e:  # noqa: BLE001 - 只用于展示/打开文件夹
            logger.warning("解析备份目录失败: %s", e)
            return self.default_backup_dir

    def _manager_instance(self) -> BackupManager:
        if self._config is None:
            raise RuntimeError("配置不可用")
        if self._manager is None:
            self._manager = BackupManager(self._config)
        return self._manager

    def _reset_manager(self) -> None:
        self._manager = None

    # ─── 世界列表 / 备份列表 ────────────────────────────────

    def scan_worlds(self) -> Dict[str, Any]:
        """扫描世界（工作线程调用；返回普通字典）。"""
        try:
            worlds = []
            for item in self._manager_instance().list_worlds():
                worlds.append(
                    {
                        "name": str(item.get("name", "") or ""),
                        "is_isolated": bool(item.get("is_isolated", False)),
                        "last_modified": float(item.get("last_modified", 0) or 0),
                    }
                )
            return {"ok": True, "worlds": worlds}
        except Exception as e:  # noqa: BLE001 - 扫描失败要让页面看到错误态
            logger.error("扫描存档失败: %s", e, exc_info=True)
            return {"ok": False, "worlds": [], "error": str(e)}

    def apply_worlds(self, result: Dict[str, Any]) -> None:
        """把扫描结果写回会话（主线程）。"""
        if not result.get("ok"):
            self._worlds = []
            self._worlds_state = STATE_ERROR
            self._worlds_error = str(result.get("error", "") or "")
            self._selected_world = ""
            self._backups = []
            self._backups_state = STATE_IDLE
            self._backups_error = ""
            return
        worlds = result.get("worlds", []) or []
        self._worlds = [dict(item) for item in worlds]
        self._worlds_state = STATE_READY if self._worlds else STATE_EMPTY
        self._worlds_error = ""
        names = {str(item.get("name", "")) for item in self._worlds}
        if self._selected_world and self._selected_world not in names:
            self._selected_world = ""
            self._backups = []
            self._backups_state = STATE_IDLE
            self._backups_error = ""
        elif self._selected_world:
            self.reload_backups()

    def select_world(self, name: str) -> bool:
        wanted = str(name or "").strip()
        if wanted == self._selected_world and self._backups_state not in (STATE_IDLE, STATE_ERROR):
            return False
        self._selected_world = wanted
        self._backups = []
        self._backups_state = STATE_IDLE
        self._backups_error = ""
        if wanted:
            self.reload_backups()
        return True

    def reload_backups(self) -> None:
        if not self._selected_world:
            self._backups = []
            self._backups_state = STATE_IDLE
            self._backups_error = ""
            return
        try:
            self._backups = self._manager_instance().get_backups(self._selected_world)
            self._backups_state = STATE_READY if self._backups else STATE_EMPTY
            self._backups_error = ""
        except Exception as e:  # noqa: BLE001
            logger.error("读取备份列表失败: %s", e, exc_info=True)
            self._backups = []
            self._backups_state = STATE_ERROR
            self._backups_error = str(e)

    # ─── 文件任务（工作线程入口）────────────────────────────

    def create_backup_task(self, ctx: Any, world: str, note: str) -> Dict[str, Any]:
        manager = self._manager_instance()

        def _progress(current: int, total: int, status: str) -> None:
            if ctx is not None:
                try:
                    ctx.progress(int(current), int(total), "")
                except Exception:  # noqa: BLE001 - 进度失败不能影响备份
                    pass

        ok, message = manager.create_backup(world, note, progress_callback=_progress)
        return {"ok": bool(ok), "message": str(message), "world": str(world), "note": str(note)}

    def restore_backup_task(self, ctx: Any, world: str, entry_id: str) -> Dict[str, Any]:
        manager = self._manager_instance()

        def _progress(current: int, total: int, status: str) -> None:
            if ctx is not None:
                try:
                    ctx.progress(int(current), int(total), "")
                except Exception:  # noqa: BLE001
                    pass

        ok, message = manager.restore_backup(entry_id, world, progress_callback=_progress)
        return {
            "ok": bool(ok),
            "message": str(message),
            "world": str(world),
            "entry": str(entry_id),
            "effective_mode": str(getattr(manager, "last_restore_mode", "") or ""),
        }

    def delete_backup_task(self, world: str, entry_id: str) -> Dict[str, Any]:
        ok, message = self._manager_instance().delete_backup(entry_id, world)
        return {"ok": bool(ok), "message": str(message), "world": str(world), "entry": str(entry_id)}

    def verify_backup_task(self, world: str, entry_id: str) -> Dict[str, Any]:
        ok, message = self._manager_instance().verify_backup(world, entry_id)
        return {"ok": bool(ok), "message": str(message), "world": str(world), "entry": str(entry_id)}

    def export_backup_task(self, world: str, entry_id: str, dest_path: str) -> Dict[str, Any]:
        ok, message = self._manager_instance().export_backup(entry_id, world, dest_path)
        return {"ok": bool(ok), "message": str(message), "world": str(world), "entry": str(entry_id), "path": dest_path}

    # ─── 任务结果（主线程）──────────────────────────────────

    def apply_create_result(self, result: Dict[str, Any]) -> None:
        if not result.get("ok"):
            return
        self._selected_world = str(result.get("world", "") or self._selected_world)
        self.reload_backups()
        self._trigger("backup_first_backup")
        self._trigger("backup_mania")

    def apply_restore_result(self, result: Dict[str, Any]) -> None:
        if not result.get("ok"):
            return
        self.reload_backups()
        self._trigger("backup_time_machine")

    def apply_export_result(self, result: Dict[str, Any]) -> None:
        if result.get("ok"):
            self._trigger("backup_export_share")

    def _trigger(self, achievement_id: str) -> bool:
        service = self._achievement
        if service is None:
            return False
        engine = None
        getter = getattr(service, "engine", None)
        if callable(getter):
            try:
                engine = getter()
            except Exception as e:  # noqa: BLE001 - 成就失败不能影响备份
                logger.warning("取成就引擎失败: %s", e)
                return False
        if engine is None:
            return False
        try:
            engine.update_progress(achievement_id, value=1, trigger_type="increment")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("触发成就 %s 失败: %s", achievement_id, e)
            return False

    # ─── 自动备份开关 ──────────────────────────────────────

    def refresh_auto(self) -> None:
        if self._config is None:
            self._auto_launch = False
            self._auto_exit = False
            return
        self._auto_launch = bool(getattr(self._config, "backup_auto_launch", False))
        self._auto_exit = bool(getattr(self._config, "backup_auto_exit", False))

    def set_auto(self, which: str, enabled: bool) -> Dict[str, Any]:
        """立即持久化一个自动备份开关；保存失败时回滚内存值。"""
        if self._config is None:
            return {"ok": False, "error": "unavailable"}
        attr = "backup_auto_launch" if str(which) == "launch" else "backup_auto_exit"
        old = bool(getattr(self._config, attr, False))
        wanted = bool(enabled)
        setattr(self._config, attr, wanted)
        if not self._save_config({attr: wanted}):
            setattr(self._config, attr, old)
            return {"ok": False, "error": "config_save_failed"}
        self.refresh_auto()
        if wanted:
            self._trigger("backup_automation")
        return {"ok": True}

    def _save_config(self, expected: Dict[str, Any]) -> bool:
        """调用 Config.save_config 并**读回**校验。

        Config.save_config 内部会吞掉异常并只弹通知，不会把失败传出来；备份
        设置如果只看返回值，失败了也会显示“已保存”。这里用读回校验把失败
        变成可显示的错误。
        """
        saver = getattr(self._config, "save_config", None)
        if not callable(saver):
            return True
        try:
            saver()
        except Exception as e:  # noqa: BLE001
            logger.error("保存备份设置失败: %s", e, exc_info=True)
            return False
        config_file = getattr(self._config, "config_file", None)
        if not config_file:
            return True
        try:
            data = json.loads(Path(str(config_file)).read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - 读不回来就按失败处理
            logger.error("读回配置失败: %s", e)
            return False
        for key, wanted in expected.items():
            current = data.get(key)
            if key == "backup_dir":
                if (current or "") != (wanted or ""):
                    logger.error("配置读回不一致: %s=%r，期望 %r", key, current, wanted)
                    return False
            elif current != wanted:
                logger.error("配置读回不一致: %s=%r，期望 %r", key, current, wanted)
                return False
        return True

    # ─── 备份设置草稿 ──────────────────────────────────────

    def _settings_from_config(self) -> Dict[str, Any]:
        if self._config is None:
            return {
                "path": "",
                "defaultPath": "",
                "pathIsCustom": False,
                "compressLevel": 6,
                "maxPerWorld": 10,
                "restoreMode": "rename",
            }
        custom = getattr(self._config, "backup_dir", None)
        return {
            "path": str(custom or self.default_backup_dir),
            "defaultPath": self.default_backup_dir,
            "pathIsCustom": bool(custom),
            "compressLevel": _int_choice(getattr(self._config, "backup_compress_level", 6), COMPRESS_LEVELS, 6),
            "maxPerWorld": _int_choice(getattr(self._config, "backup_max_per_world", 10), MAX_OPTIONS, 10),
            "restoreMode": (
                str(getattr(self._config, "backup_restore_mode", "rename") or "rename")
                if str(getattr(self._config, "backup_restore_mode", "rename") or "rename") in RESTORE_MODES
                else "rename"
            ),
        }

    @property
    def settings(self) -> Dict[str, Any]:
        source = self._settings_draft if self._settings_open else self._settings_from_config()
        data = dict(source)
        data["dirty"] = self._settings_dirty()
        return data

    @property
    def settings_dirty(self) -> bool:
        return self._settings_dirty()

    def _settings_dirty(self) -> bool:
        if not self._settings_open or not self._settings_snapshot:
            return False
        for key in ("path", "compressLevel", "maxPerWorld", "restoreMode"):
            if str(self._settings_draft.get(key, "")) != str(self._settings_snapshot.get(key, "")):
                return True
        return False

    def begin_settings(self) -> None:
        if self._settings_open:
            return
        self._settings_snapshot = self._settings_from_config()
        self._settings_draft = dict(self._settings_snapshot)
        self._settings_open = True

    def cancel_settings(self) -> None:
        self._settings_open = False
        self._settings_draft = {}

    def set_settings_field(self, field: str, value: Any) -> bool:
        if not self._settings_open:
            return False
        if field == "path":
            self._settings_draft["path"] = str(value or "")
            return True
        if field == "compressLevel":
            self._settings_draft["compressLevel"] = _int_choice(value, COMPRESS_LEVELS, 6)
            return True
        if field == "maxPerWorld":
            self._settings_draft["maxPerWorld"] = _int_choice(value, MAX_OPTIONS, 10)
            return True
        if field == "restoreMode":
            mode = str(value or "rename")
            self._settings_draft["restoreMode"] = mode if mode in RESTORE_MODES else "rename"
            return True
        return False

    def reset_settings_path(self) -> None:
        if self._settings_open:
            self._settings_draft["path"] = self.default_backup_dir

    def save_settings(self) -> Dict[str, Any]:
        """保存设置；返回 {ok, error, settings}。error 是稳定代码，不翻译。"""
        if self._config is None:
            return {"ok": False, "error": "unavailable"}
        if not self._settings_open:
            self.begin_settings()
        draft = dict(self._settings_draft)
        default_path = self.default_backup_dir
        raw_path = str(draft.get("path", "") or "").strip() or default_path
        try:
            absolute = os.path.abspath(os.path.expanduser(raw_path))
            target = Path(absolute)
            if target.exists() and not target.is_dir():
                return {"ok": False, "error": "path_not_directory"}
            target.mkdir(parents=True, exist_ok=True)
        except Exception as e:  # noqa: BLE001
            logger.error("创建备份目录失败: %s", e, exc_info=True)
            return {"ok": False, "error": "path_create_failed", "detail": str(e)}

        compress = _int_choice(draft.get("compressLevel", 6), COMPRESS_LEVELS, 6)
        max_count = _int_choice(draft.get("maxPerWorld", 10), MAX_OPTIONS, 10)
        restore = str(draft.get("restoreMode", "rename") or "rename")
        if restore not in RESTORE_MODES:
            restore = "rename"
        store_dir: Optional[str] = None if _norm_path(absolute) == _norm_path(default_path) else absolute

        old_values = {
            "backup_dir": getattr(self._config, "backup_dir", None),
            "backup_compress_level": getattr(self._config, "backup_compress_level", 6),
            "backup_max_per_world": getattr(self._config, "backup_max_per_world", 10),
            "backup_restore_mode": getattr(self._config, "backup_restore_mode", "rename"),
        }
        self._config.backup_dir = store_dir
        self._config.backup_compress_level = compress
        self._config.backup_max_per_world = max_count
        self._config.backup_restore_mode = restore
        expected = {
            "backup_dir": store_dir,
            "backup_compress_level": compress,
            "backup_max_per_world": max_count,
            "backup_restore_mode": restore,
        }
        if not self._save_config(expected):
            for key, value in old_values.items():
                setattr(self._config, key, value)
            # 原子写入失败时原文件通常没变；如果已经写进去了半套值，再尽力写回旧值。
            try:
                self._save_config(old_values)
            except Exception:  # noqa: BLE001
                pass
            return {"ok": False, "error": "config_save_failed"}

        self._reset_manager()
        self._settings_open = False
        self._settings_snapshot = {}
        self._settings_draft = {}
        self.refresh_auto()
        self.reload_backups()
        return {"ok": True, "settings": self.settings}

    # ─── 打开备份目录 ──────────────────────────────────────

    def open_folder(self) -> Dict[str, Any]:
        try:
            path = self._manager_instance().backup_root
            path.mkdir(parents=True, exist_ok=True)
            if os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
            return {"ok": True, "path": str(path)}
        except Exception as e:  # noqa: BLE001
            logger.error("打开备份目录失败: %s", e, exc_info=True)
            return {"ok": False, "error": str(e)}
