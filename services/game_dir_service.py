"""多 .minecraft 目录的管理服务。

## 为什么需要它

启动器原先只有一个游戏目录（安装目录下的 `.minecraft` 或 Linux 的
`~/.minecraft`）。用户同时玩多个整合包/隔离环境时，只能手工改配置或移动文件。
本服务把"目录"提升为一等数据：

* **内置目录**（程序安装目录、官方启动器目录）由 `config.py` 每次启动现算，
  用户不能 rename/remove；
* **自定义目录**必须由用户命名，允许 rename/remove，磁盘文件永远不动；
* 切换只改 `config.current_game_dir_id` 与 `config.minecraft_dir`，核心层
  （`MinecraftLauncher`）的路径属性是动态读取的，因此切换后所有服务立刻看到新目录。

## 分工

* 数据与持久化：`config.Config`（目录列表、当前 id、每目录的最近启动版本）；
* 业务规则：本服务（名称长度/重复、路径存在性、游戏运行中禁止切换、缓存失效）；
* 展示与翻译：`app/bridges/game_dir_bridge.py`（服务只返回 i18n 键，不持有译文）。

## 线程约定

所有方法都是**同步、短时**的（列目录、写一个小 JSON），由 QML 主线程直接调用。
路径存在性检查可能落在网络盘上，但只有用户点按钮时才发生，不阻塞启动。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from config import (
    GAME_DIR_BUILTIN_NAME_KEYS,
    GAME_DIR_KIND_CUSTOM,
    GAME_DIR_KIND_INSTALL,
    GAME_DIR_KIND_OFFICIAL,
    GAME_DIR_NAME_MAX,
    normalize_game_dir_path,
)
from services.base import Service

# ─── 错误键（桥翻成当前语言；QML 不直接显示键名）────────────────

ERROR_NAME_REQUIRED = "game_dir_name_required"
ERROR_NAME_TOO_LONG = "game_dir_name_too_long"
ERROR_NAME_INVALID = "game_dir_name_invalid"
ERROR_NAME_DUPLICATE = "game_dir_name_duplicate"
ERROR_PATH_REQUIRED = "game_dir_path_required"
ERROR_PATH_MISSING = "game_dir_path_missing"
ERROR_PATH_NOT_DIR = "game_dir_path_not_dir"
ERROR_PATH_DUPLICATE = "game_dir_path_duplicate"
ERROR_NOT_FOUND = "game_dir_not_found"
ERROR_BUILTIN_LOCKED = "game_dir_builtin_locked"
ERROR_GAME_RUNNING = "game_dir_game_running"
ERROR_SWITCH_MISSING = "game_dir_switch_missing"
ERROR_IO = "game_dir_io_error"
ERROR_UNAVAILABLE = "game_dir_service_missing"

BUILTIN_KINDS = (GAME_DIR_KIND_INSTALL, GAME_DIR_KIND_OFFICIAL)
CONTROL_CHARS = "\r\n\t\x00"


def validate_name(raw: Any) -> Tuple[str, str]:
    """名称校验。返回 (规范化后的名称, 错误键)；没有错误时错误键为空串。"""
    name = str(raw or "").strip()
    if not name:
        return "", ERROR_NAME_REQUIRED
    if len(name) > GAME_DIR_NAME_MAX:
        return name, ERROR_NAME_TOO_LONG
    if any(char in name for char in CONTROL_CHARS):
        return name, ERROR_NAME_INVALID
    return name, ""


def name_key(name: Any) -> str:
    """名称去重键（大小写不敏感；只比较自定义目录）。"""
    return str(name or "").strip().casefold()


def _path_is_dir(path: Path) -> bool:
    try:
        return path.is_dir()
    except (OSError, ValueError):
        return False


def _path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


class GameDirService(Service):
    """目录列表、增删改与切换。"""

    name = "game_dir"
    label = "游戏目录管理"

    def __init__(
        self,
        context: Any = None,
        *,
        config: Any = None,
        launcher: Any = None,
    ) -> None:
        super().__init__(context)
        #: 显式注入（测试用）；生产路径从 AppContext 取
        self._config = config
        self._launcher = launcher

    # ─── 依赖 ───────────────────────────────────────────────

    @property
    def config(self) -> Any:
        if self._config is not None:
            return self._config
        return super().config

    def _resolve_launcher(self) -> Any:
        if self._launcher is not None:
            return self._launcher
        if not self.attached:
            return None
        try:
            return self.context.try_get("launcher")
        except Exception as e:  # noqa: BLE001 - 核心没起来时不阻断目录管理
            self.log.warning("取 launcher 失败: %s", e)
            return None

    def _config_available(self) -> bool:
        try:
            return callable(getattr(self.config, "all_game_dirs", None))
        except Exception:
            return False

    # ─── 读取 ───────────────────────────────────────────────

    def list_dirs(self) -> List[Dict[str, Any]]:
        """返回给界面用的目录行（显示名/种类键/可操作性都在这里定）。"""
        if not self._config_available():
            return []
        try:
            entries = list(self.config.all_game_dirs() or [])
        except Exception as e:  # noqa: BLE001 - 配置坏了显示空列表而不是让整页崩
            self.log.error("列举游戏目录失败: %s", e)
            return []
        rows: List[Dict[str, Any]] = []
        for entry in entries:
            kind = str(entry.get("kind") or GAME_DIR_KIND_CUSTOM)
            builtin = kind in BUILTIN_KINDS
            path = str(entry.get("path") or "")
            rows.append(
                {
                    "id": str(entry.get("id") or ""),
                    "kind": kind,
                    "builtin": builtin,
                    "name": "" if builtin else str(entry.get("name") or ""),
                    "nameKey": GAME_DIR_BUILTIN_NAME_KEYS.get(kind, ""),
                    "kindKey": "game_dir_kind_builtin" if builtin else "game_dir_kind_custom",
                    "path": path,
                    "active": bool(entry.get("active")),
                    "exists": bool(entry.get("exists")),
                    "canRename": not builtin,
                    "canRemove": not builtin,
                }
            )
        return rows

    def active(self) -> Dict[str, Any]:
        """当前目录（供桥暴露 activeId / activePath）。"""
        for row in self.list_dirs():
            if row["active"]:
                return row
        return {}

    def _find_row(self, dir_id: str) -> Optional[Dict[str, Any]]:
        wanted = str(dir_id or "").strip()
        if not wanted:
            return None
        for row in self.list_dirs():
            if row["id"] == wanted:
                return row
        return None

    # ─── 增删改 ─────────────────────────────────────────────

    def add(self, name: Any, path: Any) -> Dict[str, Any]:
        """添加自定义目录：名称必填、路径必须存在且是目录。"""
        if not self._config_available():
            return self._fail(ERROR_UNAVAILABLE)
        clean_name, error = validate_name(name)
        if error:
            return self._fail(error)
        path_text = str(path or "").strip()
        if not path_text:
            return self._fail(ERROR_PATH_REQUIRED)
        if "\x00" in path_text:
            return self._fail(ERROR_PATH_MISSING)
        try:
            normalized = normalize_game_dir_path(path_text)
        except (OSError, ValueError) as exc:
            return self._fail(ERROR_IO, error=str(exc))
        if not _path_exists(normalized):
            return self._fail(ERROR_PATH_MISSING)
        if not _path_is_dir(normalized):
            return self._fail(ERROR_PATH_NOT_DIR)
        if self._name_taken(clean_name):
            return self._fail(ERROR_NAME_DUPLICATE)
        if self._path_taken(normalized):
            return self._fail(ERROR_PATH_DUPLICATE)
        try:
            entry = self.config.add_custom_game_dir(clean_name, normalized)
        except ValueError as exc:
            mapped = str(exc)
            if mapped == "name_too_long":
                return self._fail(ERROR_NAME_TOO_LONG)
            if mapped == "path_duplicate":
                return self._fail(ERROR_PATH_DUPLICATE)
            return self._fail(ERROR_NAME_REQUIRED if mapped == "name_required" else ERROR_PATH_REQUIRED)
        except OSError as exc:
            return self._fail(ERROR_IO, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            self.log.error("添加游戏目录失败: %s", exc)
            return self._fail(ERROR_IO, error=str(exc))
        self._save()
        self.log.info("已添加游戏目录 %s (%s)", clean_name, normalized)
        return self._ok(entry=entry)

    def rename(self, dir_id: Any, name: Any) -> Dict[str, Any]:
        """重命名自定义目录；内置目录不允许改名。"""
        if not self._config_available():
            return self._fail(ERROR_UNAVAILABLE)
        row = self._find_row(dir_id)
        if row is None:
            return self._fail(ERROR_NOT_FOUND)
        if row["builtin"]:
            return self._fail(ERROR_BUILTIN_LOCKED)
        clean_name, error = validate_name(name)
        if error:
            return self._fail(error)
        if self._name_taken(clean_name, exclude_id=row["id"]):
            return self._fail(ERROR_NAME_DUPLICATE)
        try:
            entry = self.config.rename_custom_game_dir(row["id"], clean_name)
        except (KeyError, ValueError, OSError) as exc:
            return self._fail(ERROR_NOT_FOUND if isinstance(exc, KeyError) else ERROR_IO, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - 桥的槽不能把异常抛回 QML
            self.log.error("重命名游戏目录失败: %s", exc)
            return self._fail(ERROR_IO, error=str(exc))
        self._save()
        return self._ok(entry=entry)

    def remove(self, dir_id: Any) -> Dict[str, Any]:
        """从列表移除自定义目录（不删磁盘文件）。移除当前目录时切回默认目录。"""
        if not self._config_available():
            return self._fail(ERROR_UNAVAILABLE)
        row = self._find_row(dir_id)
        if row is None:
            return self._fail(ERROR_NOT_FOUND)
        if row["builtin"]:
            return self._fail(ERROR_BUILTIN_LOCKED)
        was_active = bool(row["active"])
        try:
            active_id = self.config.remove_custom_game_dir(row["id"])
        except (KeyError, OSError) as exc:
            return self._fail(ERROR_NOT_FOUND if isinstance(exc, KeyError) else ERROR_IO, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            self.log.error("移除游戏目录失败: %s", exc)
            return self._fail(ERROR_IO, error=str(exc))
        if was_active:
            # 移除当前目录会切回默认目录；默认目录可能也被用户删掉了，
            # 这里按"切换"的同一规则把它补建出来，避免后续页面在空路径上报错。
            try:
                Path(str(self.config.minecraft_dir)).mkdir(parents=True, exist_ok=True)
            except (OSError, ValueError) as exc:
                self.log.warning("创建默认游戏目录失败: %s", exc)
            self._reset_launcher_caches()
        self._save()
        return self._ok(active_id=active_id, was_active=was_active)

    # ─── 切换 ───────────────────────────────────────────────

    def activate(self, dir_id: Any) -> Dict[str, Any]:
        """切换当前游戏目录（游戏运行期间拒绝；内置目录缺失时自动创建）。"""
        if not self._config_available():
            return self._fail(ERROR_UNAVAILABLE)
        row = self._find_row(dir_id)
        if row is None:
            return self._fail(ERROR_NOT_FOUND)
        if row["active"]:
            # 当前目录被用户手工删除后又点了一次「使用」：内置目录补建，自定义目录报缺失。
            path = Path(row["path"])
            if not _path_exists(path):
                if row["builtin"]:
                    try:
                        path.mkdir(parents=True, exist_ok=True)
                    except (OSError, ValueError) as exc:
                        return self._fail(ERROR_IO, error=str(exc))
                else:
                    return self._fail(ERROR_SWITCH_MISSING)
            if not _path_is_dir(path):
                return self._fail(ERROR_PATH_NOT_DIR)
            return self._ok(changed=False)
        if self._game_running():
            return self._fail(ERROR_GAME_RUNNING)
        path = Path(row["path"])
        if not _path_exists(path):
            if row["builtin"]:
                try:
                    path.mkdir(parents=True, exist_ok=True)
                except (OSError, ValueError) as exc:
                    return self._fail(ERROR_IO, error=str(exc))
            else:
                return self._fail(ERROR_SWITCH_MISSING)
        if not _path_is_dir(path):
            return self._fail(ERROR_PATH_NOT_DIR)
        try:
            entry = self.config.set_current_game_dir(row["id"])
        except (KeyError, OSError) as exc:
            return self._fail(ERROR_NOT_FOUND if isinstance(exc, KeyError) else ERROR_IO, error=str(exc))
        except Exception as exc:  # noqa: BLE001
            self.log.error("切换游戏目录失败: %s", exc)
            return self._fail(ERROR_IO, error=str(exc))
        self._reset_launcher_caches()
        self._save()
        self.log.info("已切换游戏目录: %s (%s)", row["id"], path)
        return self._ok(entry=entry, changed=True)

    # ─── 内部 ───────────────────────────────────────────────

    def _game_running(self) -> bool:
        launcher = self._resolve_launcher()
        if launcher is None:
            return False
        try:
            if bool(launcher.is_game_running()):
                return True
        except Exception as e:  # noqa: BLE001 - 探测失败不应把用户永久锁在切换之外
            self.log.warning("检查游戏进程失败: %s", e)
        try:
            checker = getattr(launcher, "is_server_running", None)
            if callable(checker) and bool(checker()):
                return True
        except Exception as e:  # noqa: BLE001
            self.log.warning("检查服务器进程失败: %s", e)
        return False

    def _reset_launcher_caches(self) -> None:
        launcher = self._resolve_launcher()
        reset = getattr(launcher, "reset_game_dir_caches", None)
        if callable(reset):
            try:
                reset()
            except Exception as e:  # noqa: BLE001 - 缓存清不掉不影响切换本身
                self.log.warning("清理游戏目录缓存失败: %s", e)

    def _name_taken(self, name: str, exclude_id: str = "") -> bool:
        target = name_key(name)
        for row in self.list_dirs():
            if row["id"] == exclude_id or row["builtin"]:
                continue
            if name_key(row["name"]) == target:
                return True
        return False

    def _path_taken(self, path: Path) -> bool:
        key = os.path.normcase(os.path.normpath(str(path)))
        for row in self.list_dirs():
            existing = os.path.normcase(os.path.normpath(str(row["path"])))
            if existing == key:
                return True
        return False

    def _save(self) -> None:
        save = getattr(self.config, "save_config", None)
        if callable(save):
            try:
                save()
            except Exception as e:  # noqa: BLE001 - 内存里的选择已生效，落盘失败只记日志
                self.log.error("保存游戏目录配置失败: %s", e)

    @staticmethod
    def _ok(**extra: Any) -> Dict[str, Any]:
        result = {"ok": True, "error_key": "", "error_params": {}}
        result.update(extra)
        return result

    @staticmethod
    def _fail(key: str, **params: Any) -> Dict[str, Any]:
        return {"ok": False, "error_key": key, "error_params": dict(params)}


__all__ = [
    "ERROR_BUILTIN_LOCKED",
    "ERROR_GAME_RUNNING",
    "ERROR_IO",
    "ERROR_NAME_DUPLICATE",
    "ERROR_NAME_INVALID",
    "ERROR_NAME_REQUIRED",
    "ERROR_NAME_TOO_LONG",
    "ERROR_NOT_FOUND",
    "ERROR_PATH_DUPLICATE",
    "ERROR_PATH_MISSING",
    "ERROR_PATH_NOT_DIR",
    "ERROR_PATH_REQUIRED",
    "ERROR_SWITCH_MISSING",
    "ERROR_UNAVAILABLE",
    "GameDirService",
    "GAME_DIR_NAME_MAX",
    "name_key",
    "validate_name",
]
