"""服务器配置编辑器服务（阶段 3 任务 3.11）。

本模块承接旧 ui/windows/server_config_editor.py 的全部业务规则，但不再碰
Tk / QML 控件：

- 读取 server.properties、eula.txt、.fmcl_server.json（每服内存）
- 分类 / 搜索 / 六种控件的数据快照（52 条：49 条 properties + 内存/EULA/目录）
- 草稿、脏计数、单项/批量恢复默认、INT 范围校验
- 合并写回 properties（保留注释与未知键）、写回每服内存
- 原始文本的读取 / 换行规范化 / 原子写回
- 运行状态、文件缺失、EULA 状态

界面只负责把本模块返回的行快照渲染成控件，并把用户动作转发回来。翻译仍由桥
注入（translate(key)），服务层不认识任何语言文件。

设计约定与其它服务一致：不 import UI、不弹窗、不自己起线程；失败用返回值
或 services.errors.InvalidArgument 表达。所有方法只在主线程调用，内部不额外
加锁（配置编辑是单用户、单页面的短操作）。
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from logzero import logger

from launcher.server_config import (
    format_properties,
    get_server_launch_memory,
    get_server_properties_path,
    parse_properties,
    read_eula,
    set_server_launch_memory,
    write_server_properties,
)
from services.errors import InvalidArgument
from services.server_config_schema import (
    ALL_OPTIONS,
    BOOL,
    CATEGORIES,
    CONFIG_OPTIONS,
    ENUM,
    INFO,
    INT,
    MEMORY,
    get_option,
)

#: 每服内存下拉的固定选项（与旧编辑器一致；读到的自定义值会追加并排序）。
MEMORY_OPTIONS: Tuple[str, ...] = ("1G", "2G", "4G", "6G", "8G", "12G", "16G")
#: 没有每服配置时的默认内存。
DEFAULT_MEMORY = "2G"

#: 只读信息行的类型（桥据此渲染 EULA 徽标或目录路径 + 打开按钮）。
INFO_EULA = "eula"
INFO_DIR = "dir"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _bool_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "true" if _text(value).strip().lower() == "true" else "false"


def _memory_sort_key(value: str) -> float:
    """1G / 2048M → 可比较的 MB 数；无法解析的排到最后。"""
    text = _text(value).strip().upper()
    try:
        if text.endswith("G"):
            return float(text[:-1]) * 1024
        if text.endswith("M"):
            return float(text[:-1])
    except ValueError:
        pass
    return math.inf


def memory_choices(current: str = "") -> List[str]:
    """内存下拉候选：固定选项 + 当前自定义值，按大小排序。"""
    choices = list(MEMORY_OPTIONS)
    value = _text(current).strip()
    if value and value not in choices:
        choices.append(value)
    return sorted(choices, key=_memory_sort_key)


def normalize_raw_text(text: str) -> str:
    """把原始编辑框内容规范成单个 LF 结尾（与旧实现一致）。"""
    value = _text(text).replace("\r\n", "\n").replace("\r", "\n")
    return value.rstrip("\n") + "\n"


class ServerConfigSession:
    """一台服务器的配置编辑会话（草稿态）。

    生命周期：load() 读盘 → 界面改 set_value / set_memory → save() 写盘并重新
    读取。discard_changes() 丢弃草稿。
    """

    def __init__(self, server_dir: Any, version: str = "", *, running: bool = False) -> None:
        self.server_dir = Path(server_dir)
        self.version = str(version or "")
        self.properties_path = get_server_properties_path(self.server_dir)

        self.category = "world"
        self.search = ""
        self.file_missing = False
        self.load_error = ""
        self.running = bool(running)
        self.eula_agreed = False

        self.file_values: Dict[str, str] = {}
        self.draft_values: Dict[str, str] = {}
        self.touched: set = set()
        self.memory = DEFAULT_MEMORY
        self.original_memory = DEFAULT_MEMORY

    # ─── 读盘 ───────────────────────────────────────────────

    def load(self) -> bool:
        """从磁盘读取全部状态；失败时 load_error 非空并返回 False。"""
        try:
            self.file_missing = not self.properties_path.exists()
            if self.file_missing:
                raw: Dict[str, str] = {}
            else:
                text = self.properties_path.read_text(encoding="utf-8", errors="replace")
                raw = parse_properties(text)
        except Exception as e:  # noqa: BLE001 - 读盘失败要能显示在页面上
            self.load_error = f"{type(e).__name__}: {e}"
            logger.error("读取服务器配置失败 (%s): %s", self.properties_path, e)
            return False

        self.file_values = dict(raw)
        self.draft_values = {
            option["key"]: raw.get(option["key"], _text(option["default"])) for option in CONFIG_OPTIONS
        }
        self.touched.clear()
        try:
            stored = get_server_launch_memory(self.server_dir)
        except Exception as e:  # noqa: BLE001 - 读不到就按默认值
            logger.warning("读取服务器内存设置失败 (%s): %s", self.server_dir, e)
            stored = None
        self.memory = _text(stored).strip() or DEFAULT_MEMORY
        self.original_memory = self.memory
        self.eula_agreed = read_eula(self.server_dir)
        self.load_error = ""
        return True

    def refresh_runtime(self, running: Optional[bool] = None) -> None:
        """刷新运行状态与 EULA（服务器可能在编辑器打开期间启动）。"""
        if running is not None:
            self.running = bool(running)
        try:
            self.eula_agreed = read_eula(self.server_dir)
        except Exception:  # noqa: BLE001 - 只读展示，读不到保持旧值
            pass

    def discard_changes(self) -> bool:
        """丢弃草稿并重新读盘（离开页面 / 关闭确认后调用）。"""
        return self.load()

    # ─── 分类与搜索 ─────────────────────────────────────────

    def set_category(self, category_id: str) -> bool:
        wanted = _text(category_id).strip()
        if wanted not in {item["id"] for item in CATEGORIES}:
            return False
        self.category = wanted
        self.search = ""
        return True

    def set_search(self, text: str) -> None:
        self.search = _text(text).strip()
        if self.search and self.category == "raw":
            self.category = "world"

    @property
    def searching(self) -> bool:
        return bool(self.search)

    def visible_options(self, translate: Optional[Callable[..., str]] = None, **_: Any) -> List[Dict[str, Any]]:
        """当前应显示的配置项：有搜索词时跨分类匹配名称 / 键名 / 说明。"""
        if self.search:
            needle = self.search.lower()
            render = translate or (lambda key: key)
            out: List[Dict[str, Any]] = []
            for option in ALL_OPTIONS:
                name = _text(render(f"{option['i18n']}_n"))
                desc = _text(render(f"{option['i18n']}_d"))
                if needle in name.lower() or needle in desc.lower() or needle in _text(option["key"]).lower():
                    out.append(option)
            return out
        if self.category == "raw":
            return []
        return [option for option in ALL_OPTIONS if option["category"] == self.category]

    def search_count(self, translate: Optional[Callable[..., str]] = None) -> int:
        return len(self.visible_options(translate)) if self.searching else 0

    def scope_name(self, translate: Callable[..., str]) -> str:
        """恢复默认确认框里的范围名：搜索范围或当前分类。"""
        if self.searching:
            return translate("server_config_search_placeholder")
        for item in CATEGORIES:
            if item["id"] == self.category:
                return translate(item["i18n"])
        return translate("server_config_search_placeholder")

    # ─── 草稿值 ─────────────────────────────────────────────

    def will_write(self, key: str) -> bool:
        return self.file_missing or key in self.file_values or key in self.touched

    def current_write_values(self) -> Dict[str, str]:
        """本次保存真正要合并写回的键值（保留文件里的未知键）。"""
        values: Dict[str, str] = {}
        for key, value in self.file_values.items():
            if key not in self.draft_values:
                values[key] = value
        for key, value in self.draft_values.items():
            if self.will_write(key):
                values[key] = _text(value)
        return values

    def dirty_keys(self) -> List[str]:
        current = self.current_write_values()
        if self.file_missing:
            return [key for key in current if key in self.touched]
        dirty: List[str] = []
        for key, value in current.items():
            if self.file_values.get(key) != value:
                dirty.append(key)
        for key in self.file_values:
            if key not in current:
                dirty.append(key)
        return dirty

    def dirty_count(self) -> int:
        count = len(self.dirty_keys())
        if self.memory != self.original_memory:
            count += 1
        return count

    def set_value(self, key: str, value: Any) -> bool:
        option = get_option(_text(key))
        if option is None or option["kind"] in (INFO, MEMORY):
            return False
        name = option["key"]
        if option["kind"] == BOOL:
            self.draft_values[name] = _bool_text(value)
        else:
            self.draft_values[name] = _text(value).strip()
        self.touched.add(name)
        return True

    def set_memory(self, value: Any) -> bool:
        memory = _text(value).strip()
        if not memory:
            return False
        self.memory = memory
        self.touched.add("@memory")
        return True

    def reset_one(self, key: str) -> bool:
        option = get_option(_text(key))
        if option is None or option["kind"] == INFO:
            return False
        if option["kind"] == MEMORY:
            self.memory = _text(option.get("default") or DEFAULT_MEMORY)
        else:
            self.draft_values[option["key"]] = _text(option["default"])
        self.touched.add(option["key"])
        return True

    def reset_visible(self, translate: Optional[Callable[..., str]] = None) -> int:
        count = 0
        for option in self.visible_options(translate):
            if option["kind"] == INFO:
                continue
            if self.reset_one(option["key"]):
                count += 1
        return count

    # ─── 校验 / 保存 ────────────────────────────────────────

    def validation_error(self, option: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """返回 INT 项的错误描述（未出错返回 None）。文案由桥翻译。"""
        if option["kind"] != INT:
            return None
        key = option["key"]
        raw = _text(self.draft_values.get(key, "")).strip()
        if not raw:
            if self.will_write(key):
                return {"code": "plain", "key": key, "nameKey": f"{option['i18n']}_n"}
            return None
        try:
            number = int(raw)
        except ValueError:
            return {"code": "plain", "key": key, "nameKey": f"{option['i18n']}_n"}
        low, high = option.get("min"), option.get("max")
        if low is not None and high is not None and not (low <= number <= high):
            return {"code": "range", "key": key, "nameKey": f"{option['i18n']}_n", "min": low, "max": high}
        return None

    def validation_errors(self) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for option in CONFIG_OPTIONS:
            error = self.validation_error(option)
            if error is not None:
                out[option["key"]] = error
        return out

    def first_error(self) -> Optional[Dict[str, Any]]:
        for option in CONFIG_OPTIONS:
            error = self.validation_error(option)
            if error is not None:
                return error
        return None

    def save(self) -> Dict[str, Any]:
        """校验并写盘。返回 {ok, validation?, error?, memorySaved, warning?}。"""
        first = self.first_error()
        if first is not None:
            return {"ok": False, "validation": True, "error": first}
        values = self.current_write_values()
        if values:
            ok, error = write_server_properties(self.server_dir, values)
            if not ok:
                logger.error("保存服务器配置失败 (%s): %s", self.properties_path, error)
                return {"ok": False, "error": error or "write_failed"}
        try:
            memory_ok, memory_error = set_server_launch_memory(self.server_dir, self.memory)
        except Exception as e:  # noqa: BLE001 - 与 properties 写入失败分开报告
            memory_ok, memory_error = False, str(e)
        if not memory_ok:
            logger.warning("保存服务器内存设置失败 (%s): %s", self.server_dir, memory_error)
        if not self.load():
            return {"ok": False, "error": self.load_error or "reload_failed"}
        return {
            "ok": True,
            "memorySaved": bool(memory_ok),
            "warning": "" if memory_ok else (memory_error or "memory_write_failed"),
        }

    # ─── 原始文本 ───────────────────────────────────────────

    def raw_text(self) -> str:
        """磁盘原文；文件不存在时给一份完整的默认 properties。"""
        try:
            if self.properties_path.exists():
                return self.properties_path.read_text(encoding="utf-8", errors="replace")
            return format_properties({option["key"]: option["default"] for option in CONFIG_OPTIONS})
        except Exception as e:  # noqa: BLE001 - 原文只用于展示，失败给一行注释
            logger.error("读取 server.properties 原文失败 (%s): %s", self.properties_path, e)
            return f"# {type(e).__name__}: {e}\n"

    def write_raw(self, text: str) -> None:
        """规范化换行后原子写回原文，并重新读盘。失败抛 InvalidArgument。"""
        content = normalize_raw_text(text)
        tmp_path = self.properties_path.with_name(self.properties_path.name + ".tmp")
        try:
            self.properties_path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            os.replace(str(tmp_path), str(self.properties_path))
        except Exception as e:  # noqa: BLE001 - 统一收敛成服务异常
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            logger.error("写入 server.properties 原文失败 (%s): %s", self.properties_path, e)
            raise InvalidArgument(detail=f"{type(e).__name__}: {e}", cause=e) from e
        if not self.load():
            raise InvalidArgument(detail=self.load_error or "reload_failed")

    # ─── 行快照 ─────────────────────────────────────────────

    def rows(self, translate: Callable[..., str]) -> List[Dict[str, Any]]:
        """把当前可见项渲染成 QML 可直接消费的数据。"""
        options = self.visible_options(translate)
        if not options:
            return []
        dirty = set(self.dirty_keys())
        if self.memory != self.original_memory:
            dirty.add("@memory")
        out: List[Dict[str, Any]] = []
        for option in options:
            out.append(self._row(option, translate, dirty))
        return out

    def _row(self, option: Dict[str, Any], translate: Callable[..., str], dirty: set) -> Dict[str, Any]:
        key = option["key"]
        kind = option["kind"]
        is_search = bool(self.search)
        if is_search:
            category = next((c for c in CATEGORIES if c["id"] == option["category"]), None)
            group_key = option["category"]
            group_title_key = category["i18n"] if category else "scfg_cat_world"
        elif option.get("advanced"):
            group_key, group_title_key = "advanced", "server_config_group_advanced"
        else:
            group_key, group_title_key = "basic", "server_config_group_basic"

        row: Dict[str, Any] = {
            "key": key,
            "kind": kind,
            "nameKey": f"{option['i18n']}_n",
            "descKey": f"{option['i18n']}_d",
            "name": translate(f"{option['i18n']}_n"),
            "description": translate(f"{option['i18n']}_d"),
            "advanced": bool(option.get("advanced")),
            "default": _text(option["default"]),
            "groupKey": group_key,
            "groupTitleKey": group_title_key,
            "canReset": kind != INFO,
            "dirty": key in dirty,
            "rangeText": "",
            "enumItems": [],
            "infoKind": "",
            "infoText": "",
            "infoLevel": "neutral",
            "choices": [],
        }
        if kind == INT and option.get("min") is not None and option.get("max") is not None:
            row["rangeText"] = translate("server_config_int_range", min=option["min"], max=option["max"])
        if kind == MEMORY:
            row["value"] = self.memory
            row["choices"] = memory_choices(self.memory)
        elif kind == INFO:
            row["value"] = ""
            if key == "@eula":
                row["infoKind"] = INFO_EULA
                row["infoText"] = translate("server_config_eula_yes" if self.eula_agreed else "server_config_eula_no")
                row["infoLevel"] = "success" if self.eula_agreed else "warning"
            elif key == "@dir":
                row["infoKind"] = INFO_DIR
                row["infoText"] = str(self.server_dir)
        else:
            row["value"] = _text(self.draft_values.get(key, option["default"]))
        if kind == ENUM:
            current = _text(row["value"])
            raw_items = list(option.get("values") or [])
            if current and current not in [item[0] for item in raw_items]:
                raw_items.append((current, None))
            labels: List[str] = []
            for raw, label_key in raw_items:
                label = translate(label_key) if label_key else raw
                if label in labels:
                    label = f"{label} ({raw})"
                labels.append(label)
                row["enumItems"].append({"value": raw, "label": label})
        return row


__all__ = [
    "DEFAULT_MEMORY",
    "INFO_DIR",
    "INFO_EULA",
    "MEMORY_OPTIONS",
    "ServerConfigSession",
    "memory_choices",
    "normalize_raw_text",
]
