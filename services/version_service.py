"""已安装版本列表与详情服务（阶段 3 任务 3.2）。

## 范围（对照表 + 2026-10-06 用户裁决的"轻量新增"）

| 对照表 | 能力 | 旧实现位置 |
|--------|------|-----------|
| B-01 | 已安装版本列表 + 计数 + 每行显示文本（`[Forge 49.0.26]` / `(1.20.4)`） | `ui/app_handlers.py:53-167` |
| B-02 | 行操作：选中 / 重命名 / 删除（模组与设置入口归 3.6/3.7） | `ui/app_handlers.py:272-352`、`1100-1142` |
| A-04 | 刷新版本列表 | `ui/app_base.py:171-181`、`ui/app_handlers.py:882-893` |
| B-19 | **新增**：搜索与排序（名称 / 游戏版本 / 加载器） | 无（旧界面没有搜索框） |
| B-20 | **新增**：版本详情（路径 / 所需 Java 大版本 / 模组数量 / 加载器） | 无（旧界面没有详情页） |
| B-21 | **新增**：打开版本目录 | 无 |
| B-22 | **新增**：校验版本文件完整性（接 `MinecraftLauncher.verify_installed_version`） | 无（核心有这个能力，旧界面 0 个调用点） |

**"校验修复"里的"修复"没做**：核心只提供"校验"（`launcher/verify.py` 算哈希，
不重下文件）。真正的修复要复用安装器的下载链路（属于 3.3 的向导），本轮**不假装有** ——
已作为 `D-165` 挂账（理由与排期写在 `docs/refactor/07-known-defects.md`）。

## 三条设计约束

1. **业务规则搬进服务，界面只留展示**：显示文本拼法、排序键、删除后清"最近使用版本"、
   重命名失败的消息键归一化，全在这里；桥与 QML 不做判断（红线 2）。
2. **慢活走任务**：`load()` / `rename()` / `remove()` / `verify()` 都提交到
   `self.tasks`（读盘、弹窗等待、算哈希都不该卡界面）。结果一律经
   **观察者协议**回到桥，桥只写属性 + 发信号（桥的工作线程契约）。
3. **观察者回调可能在任意线程**：与 `GameService` 同一套约定，回调里不许碰界面。

## 观察者协议（`set_observer(obj)`）

* ``on_versions(rows, error)`` —— 列表加载完成（`error` 非空表示失败，`rows` 为空表）
* ``on_status(key, level, params)`` —— 状态条文案（**i18n 键 + 参数**，翻译在桥里做）
* ``on_result(kind, ok, data)`` —— 一次操作结束；`kind` ∈ ``{"rename","remove","verify"}``

## 与旧实现刻意一致的三处

* **删除确认的默认按钮仍是"是"**：旧界面用 `messagebox.askyesno`（回车即确认），
  这里同样 `default=True` —— 换掉它属行为变更，要单独裁决。
* **重命名的两个对话框顺序不变**：先输入新名字，再确认。
* **删除后把"最近使用版本"清掉**：旧界面在删掉的正是选中项时清 `selected_version`；
  新界面没有"选中即启动"这回事（首页用 `config.last_launched_version`），
  所以清的是它 —— 语义相同：被删掉的版本不能再是被选中的那个。
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.base import Service
from services.i18n_service import _

#: 排序键（QML 传字符串，非法值回落 ``SORT_NAME``）
SORT_NAME = "name"
SORT_VANILLA = "vanilla"
SORT_LOADER = "loader"
SORT_KEYS: Tuple[str, ...] = (SORT_NAME, SORT_VANILLA, SORT_LOADER)

#: 核心 ``rename_instance`` 返回的**消息键**；其余返回值是它原样拼的中文句子，
#: 交给 ``rename_instance_error`` 当参数（旧界面就是这么显示的，本轮保持）。
RENAME_MESSAGE_KEYS: Tuple[str, ...] = ("rename_instance_invalid", "rename_instance_exists")

#: 校验结果里最多回传多少条损坏文件路径（界面只显示条数，多余的不占内存）
MAX_INVALID_REPORTED = 50


def build_display_text(info: Any) -> str:
    """旧 `ui/app_handlers.py:76-85` 那四行的显示文本，**逐字保留**。

    * 有加载器：``1.20.4-forge-49.0.26 [Forge 49.0.26]``（版本号未知时只写 ``[Forge]``）
    * 无加载器且版本号认得出：``1.20.4  (1.20.4)``（**两个空格**，旧实现如此）
    * 两者都不成立：只有文件夹名
    """
    text = str(getattr(info, "folder_name", "") or "")
    loader = getattr(info, "loader_type", None)
    if loader:
        title = str(loader).title()
        version = getattr(info, "loader_version", None)
        if version and version != "Unknown":
            return text + " [%s %s]" % (title, version)
        return text + " [%s]" % title
    vanilla = getattr(info, "vanilla_name", "")
    if vanilla not in ("Unknown", ""):
        return text + "  (%s)" % vanilla
    return text


def row_from_info(info: Any) -> Dict[str, Any]:
    """``InstanceInfo`` → QML 直接可用的字典（键名 camelCase，与桥的其它属性一致）。"""
    return {
        "id": str(getattr(info, "folder_name", "") or ""),
        "display": build_display_text(info),
        "vanilla": str(getattr(info, "vanilla_name", "") or ""),
        "loader": str(getattr(info, "loader_type", "") or ""),
        "loaderVersion": str(getattr(info, "loader_version", "") or ""),
        "hasLoader": bool(getattr(info, "loader_type", None)),
        "state": str(getattr(info, "state", "") or ""),
        "reliable": bool(getattr(info, "reliable", True)),
        "javaMajor": int(getattr(info, "java_major", 0) or 0),
    }


class VersionService(Service):
    """已安装版本的读侧 + 重命名 / 删除 / 校验 / 打开目录。"""

    name = "version"
    label = "版本列表与详情"

    def __init__(
        self,
        context: Any = None,
        *,
        launcher: Any = None,
        opener: Optional[Callable[[str], None]] = None,
        ui_port: Any = None,
    ) -> None:
        """
        Args:
            context: ``AppContext``（由 ``Service.attach`` 注入）。
            launcher: 显式注入的 ``MinecraftLauncher``。**测试用**；生产路径为 None，
                届时从 ``AppContext`` 取 ``"launcher"``。
            opener: 打开目录的实现。**测试用**；生产路径为 None，届时按平台选
                `os.startfile` / `open` / `xdg-open`。
            ui_port: 显式注入的 ``UIPort``。**测试用**；生产路径为 None，
                届时用 ``self.context.ui``（QML 对话框宿主）。
        """
        super().__init__(context)
        self._launcher = launcher
        self._opener = opener
        self._ui_port = ui_port
        self._observer: Any = None
        self._rows: List[Dict[str, Any]] = []
        self._lock = threading.RLock()

    # ─── 装配 ───────────────────────────────────────────────

    def use_launcher(self, launcher: Any) -> None:
        """注入启动器核心（``AppContext`` 里那份唯一实例）。"""
        self._launcher = launcher

    def set_observer(self, observer: Any) -> None:
        """设置观察者（界面桥）。同一时刻只保留一个 —— 它代表"当前那个界面"。"""
        self._observer = observer

    @property
    def ui(self) -> Any:
        """UI 端口（显式注入优先，其次 ``AppContext`` 的那一个）。"""
        if self._ui_port is not None:
            return self._ui_port
        return self.context.ui

    # ─── 列表（B-01 / A-04 / B-19）──────────────────────────

    @property
    def rows(self) -> List[Dict[str, Any]]:
        """最近一次扫描的结果（原始顺序 = 核心给的名字序）。"""
        with self._lock:
            return [dict(row) for row in self._rows]

    def load(self, *, force: bool = False) -> Any:
        """异步扫描已安装版本。返回 ``TaskHandle``（测试可 ``wait()``）。

        ``force=True`` 时丢掉核心的实例缓存再扫（刷新按钮用它：用户在文件管理器里
        手动删了版本目录，靠目录集合比对本来也能发现，但"我点了刷新"应当无条件重读）。
        """
        self._emit_status("version_loading_installed", "loading", {})
        return self.tasks.submit(
            self._scan_sync,
            bool(force),
            name="version.load",
            on_error=lambda e: self._scan_done([], str(e)),
        )

    def _scan_sync(self, force: bool) -> List[Dict[str, Any]]:
        launcher = self._resolve_launcher()
        if launcher is None:
            # 核心还没注册（启动链条没跑完）或整个坏掉 —— **降级为按目录名列举**。
            # 为什么不报错：版本目录是磁盘上的事实，核心只负责"解析出加载器/游戏版本"
            # 这层元数据；拿不到元数据就把能显示的先显示出来（旧界面里这个面板等效于
            # "核心没起来时整个界面都打不开"，新架构下不该让一页跟着陪葬）。
            # 启动了链条之后 `VersionsBridge.on_chain_finished()` 会再扫一次，
            # 那时元数据就齐了。
            self.log.warning("启动器核心不可用，改为按目录名列举版本")
            rows = self._scan_folders()
            self._scan_done(rows, "")
            return rows
        if force:
            try:
                launcher.invalidate_instance_cache()
            except Exception as e:  # noqa: BLE001 - 缓存失效失败不该让刷新整个失败
                self.log.warning("清实例缓存失败: %s", e)
        try:
            infos = launcher.get_installed_versions()
        except Exception as e:  # noqa: BLE001
            self._scan_done([], str(e))
            return []
        rows = [row_from_info(info) for info in infos or []]
        self._scan_done(rows, "")
        return rows

    def _scan_folders(self) -> List[Dict[str, Any]]:
        """降级扫描：只列目录名（不含加载器/游戏版本等元数据）。"""
        versions_dir = self._versions_dir()
        try:
            entries = sorted(item for item in versions_dir.iterdir() if item.is_dir())
        except OSError as e:  # noqa: BLE001 - 目录不存在/没权限都算"没有版本"
            self.log.warning("列举版本目录失败 (%s): %s", versions_dir, e)
            return []
        rows: List[Dict[str, Any]] = []
        for entry in entries:
            rows.append({
                "id": entry.name,
                "display": entry.name,
                "vanilla": "",
                "loader": "",
                "loaderVersion": "",
                "hasLoader": False,
                "state": "",
                "reliable": False,
                "javaMajor": 0,
            })
        return rows

    def _scan_done(self, rows: List[Dict[str, Any]], error: str) -> None:
        with self._lock:
            self._rows = list(rows)
        if error:
            self._emit_status("version_load_failed", "error", {"error": error})
        else:
            self._emit_status("version_loaded", "success", {"count": len(rows)})
        self._emit("on_versions", list(rows), error)

    def filtered(self, text: str = "", sort_key: str = SORT_NAME) -> List[Dict[str, Any]]:
        """按关键词过滤 + 排序（B-19）。纯函数，只用最近一次扫描的结果。"""
        needle = str(text or "").strip().lower()
        with self._lock:
            rows = [dict(row) for row in self._rows]

        if needle:
            rows = [row for row in rows if needle in self._haystack(row)]

        key = sort_key if sort_key in SORT_KEYS else SORT_NAME
        if key == SORT_VANILLA:
            return sorted(rows, key=lambda r: (r.get("vanilla", "").lower(), r.get("id", "").lower()))
        if key == SORT_LOADER:
            return sorted(rows, key=lambda r: (r.get("loader", "").lower(), r.get("id", "").lower()))
        return sorted(rows, key=lambda r: r.get("id", "").lower())

    @staticmethod
    def _haystack(row: Dict[str, Any]) -> str:
        """参与搜索的字段：文件夹名 / 显示文本 / 游戏版本 / 加载器（含版本号）。"""
        return " ".join(
            str(row.get(field, "") or "").lower()
            for field in ("id", "display", "vanilla", "loader", "loaderVersion")
        )

    # ─── 详情（B-20）────────────────────────────────────────

    def detail(self, version_id: str) -> Dict[str, Any]:
        """单个版本的详情。**同步**：只读一个版本的信息，足够快。

        版本已不存在时返回 ``{"id": ..., "exists": False}``（界面据此提示后回列表）。
        """
        vid = str(version_id or "").strip()
        if not vid:
            return {"id": "", "exists": False}

        with self._lock:
            row = next((dict(item) for item in self._rows if item.get("id") == vid), None)

        version_dir = self._versions_dir() / vid
        info = self._instance_info(vid)
        if info is not None:
            row = row_from_info(info)
        elif row is None:
            row = {"id": vid, "display": vid, "vanilla": "", "loader": "", "loaderVersion": "",
                   "hasLoader": False, "state": "", "reliable": False, "javaMajor": 0}

        mods_dir = self._mods_dir(vid)
        row.update({
            "exists": version_dir.is_dir(),
            "path": str(version_dir),
            "jsonPath": str(self._json_path(vid) or ""),
            "modsDir": str(mods_dir) if mods_dir is not None else "",
            "modsCount": self._count_files(mods_dir) if mods_dir is not None else 0,
        })
        return row

    def _instance_info(self, version_id: str) -> Any:
        """取（带缓存的）``InstanceInfo``；取不到返回 None。"""
        launcher = self._resolve_launcher()
        if launcher is None:
            return None
        try:
            return launcher.get_instance_info(version_id)
        except Exception as e:  # noqa: BLE001 - 详情降级为"只有文件夹名"
            self.log.warning("读实例信息失败 (%s): %s", version_id, e)
            return None

    def _json_path(self, version_id: str) -> Optional[Path]:
        """版本 JSON 的路径。

        优先问核心的 `find_version_json()`（**真值来源只有那一处**：三种布局
        `versions/{id}/{id}.json` / `versions/{id}.json` / 目录里唯一的 json 都认，
        缺陷 D-170 就是"两处各认一半"造成的）；核心不可用时退回本地这份同样的查找。
        """
        launcher = self._resolve_launcher()
        if launcher is not None:
            finder = getattr(launcher, "find_version_json", None)
            if callable(finder):
                try:
                    found = finder(version_id)
                except Exception as e:  # noqa: BLE001 - 找不到就当没有
                    self.log.warning("核心查找版本 JSON 失败 (%s): %s", version_id, e)
                else:
                    return Path(found) if found else None

        version_dir = self._versions_dir() / version_id
        direct = version_dir / f"{version_id}.json"
        if direct.exists():
            return direct
        try:
            json_files = sorted(version_dir.glob("*.json"))
        except OSError:
            return None
        return json_files[0] if len(json_files) == 1 else None

    def _mods_dir(self, version_id: str) -> Optional[Path]:
        """该版本的模组目录（走 ``ResourceService``：装了加载器才用版本隔离目录）。"""
        mc_dir = self._minecraft_dir()
        resource = self.try_get("resource")
        if resource is not None:
            try:
                return Path(resource.resource_dir(version_id, mc_dir, "mods"))
            except Exception as e:  # noqa: BLE001 - 资源服务算不出就用全局目录
                self.log.warning("解析模组目录失败 (%s): %s", version_id, e)
        return mc_dir / "mods"

    @staticmethod
    def _count_files(folder: Optional[Path]) -> int:
        if folder is None:
            return 0
        try:
            return sum(1 for item in folder.iterdir() if item.is_file())
        except OSError:
            return 0

    # ─── 重命名（B-02）──────────────────────────────────────

    def rename(self, version_id: str) -> Any:
        """异步重命名：弹输入框 → 确认 → 调核心。返回 ``TaskHandle``。"""
        vid = str(version_id or "").strip()
        if not vid:
            self._emit_status("version_select_first", "error", {})
            return None
        return self.tasks.submit(self._rename_sync, vid, name=f"version.rename.{vid}")

    def _rename_sync(self, version_id: str) -> Dict[str, Any]:
        new_name = self.ui.ask_text(
            _("rename_instance_title"), _("rename_instance_prompt"), version_id
        )
        new_name = str(new_name or "").strip()
        if not new_name or new_name == version_id:
            return self._result("rename", True, {"id": version_id, "cancelled": True})

        # 旧界面：askyesno(_("rename_instance"), _("rename_instance_confirm", …))
        if not self.ui.confirm(
            _("rename_instance"),
            _("rename_instance_confirm", old=version_id, new=new_name),
            default=True,
        ):
            return self._result("rename", True, {"id": version_id, "cancelled": True})

        self._emit_status("version_renaming", "loading", {"old": version_id, "new": new_name})
        launcher = self._resolve_launcher()
        if launcher is None:
            self._emit_status("rename_instance_error", "error", {"error": "launcher_unavailable"})
            return self._result("rename", False, {"id": version_id, "error": "launcher_unavailable"})

        try:
            ok, message = launcher.rename_instance(version_id, new_name)
        except Exception as e:  # noqa: BLE001 - 核心抛异常也要变成一条状态文案
            self.log.error("重命名 %s 失败: %s", version_id, e)
            self._emit_status("rename_instance_error", "error", {"error": str(e)})
            return self._result("rename", False, {"id": version_id, "error": str(e)})

        message = str(message or "")
        if ok:
            self._emit_status("rename_instance_success", "success", {"old": version_id, "new": new_name})
            self._remember_renamed(version_id, new_name)
            return self._result("rename", True, {"id": version_id, "newName": new_name})

        key, params = self._rename_error(message, new_name)
        self._emit_status(key, "error", params)
        return self._result("rename", False, {"id": version_id, "messageKey": key, "params": params})

    @staticmethod
    def _rename_error(message: str, new_name: str) -> Tuple[str, Dict[str, Any]]:
        """核心的失败返回值 → (i18n 键, 参数)。见模块文档"与旧实现刻意一致"。"""
        if message in RENAME_MESSAGE_KEYS:
            if message == "rename_instance_exists":
                return message, {"name": new_name}
            return message, {}
        return "rename_instance_error", {"error": message}

    def _remember_renamed(self, old_name: str, new_name: str) -> None:
        """被重命名的正是"最近使用版本"时，把它一起改名（旧界面改的是 `selected_version`）。"""
        try:
            if str(getattr(self.config, "last_launched_version", "") or "") == old_name:
                self.config.last_launched_version = new_name
                save = getattr(self.config, "save_config", None)
                if callable(save):
                    save()
        except Exception as e:  # noqa: BLE001 - 记不住不影响重命名本身
            self.log.warning("更新 last_launched_version 失败: %s", e)

    # ─── 删除（B-02）────────────────────────────────────────

    def remove(self, version_id: str) -> Any:
        """异步删除：确认 → 调核心（核心负责删目录与 json）。返回 ``TaskHandle``。"""
        vid = str(version_id or "").strip()
        if not vid:
            self._emit_status("version_select_first", "error", {})
            return None
        return self.tasks.submit(self._remove_sync, vid, name=f"version.remove.{vid}")

    def _remove_sync(self, version_id: str) -> Dict[str, Any]:
        # 旧界面：askyesno(_("confirm_delete"), _("confirm_delete_version", version=…))
        if not self.ui.confirm(
            _("confirm_delete"),
            _("confirm_delete_version", version=version_id),
            default=True,
        ):
            return self._result("remove", True, {"id": version_id, "cancelled": True})

        self._emit_status("deleting_version", "loading", {"version": version_id})
        launcher = self._resolve_launcher()
        if launcher is None:
            self._emit_status("version_remove_error", "error", {"error": "launcher_unavailable"})
            return self._result("remove", False, {"id": version_id, "error": "launcher_unavailable"})

        try:
            ok, removed = launcher.remove_version(version_id)
        except Exception as e:  # noqa: BLE001
            self.log.error("删除 %s 失败: %s", version_id, e)
            self._emit_status("version_remove_error", "error", {"error": str(e)})
            return self._result("remove", False, {"id": version_id, "error": str(e)})

        if ok:
            self._emit_status("version_removed", "success", {"version": str(removed or version_id)})
            self._forget_removed(version_id)
        else:
            self._emit_status("version_remove_failed", "error", {"version": version_id})
        return self._result("remove", bool(ok), {"id": version_id})

    def _forget_removed(self, version_id: str) -> None:
        """删掉的正是"最近使用版本"时把它清空（旧界面清的是 `selected_version`）。"""
        try:
            if str(getattr(self.config, "last_launched_version", "") or "") == version_id:
                self.config.last_launched_version = ""
                save = getattr(self.config, "save_config", None)
                if callable(save):
                    save()
        except Exception as e:  # noqa: BLE001
            self.log.warning("清理 last_launched_version 失败: %s", e)

    # ─── 校验（B-22，新增）──────────────────────────────────

    def verify(self, version_id: str) -> Any:
        """异步校验版本文件完整性（接核心那个零调用点的能力）。返回 ``TaskHandle``。"""
        vid = str(version_id or "").strip()
        if not vid:
            self._emit_status("version_select_first", "error", {})
            return None
        return self.tasks.submit(self._verify_sync, vid, name=f"version.verify.{vid}")

    def _verify_sync(self, version_id: str) -> Dict[str, Any]:
        self._emit_status("version_verifying", "loading", {"version": version_id})
        launcher = self._resolve_launcher()
        if launcher is None:
            self._emit_status("version_verify_failed", "error", {"error": "launcher_unavailable"})
            return self._result("verify", False, {"id": version_id, "error": "launcher_unavailable"})

        try:
            result = launcher.verify_installed_version(version_id)
        except Exception as e:  # noqa: BLE001
            self.log.error("校验 %s 失败: %s", version_id, e)
            self._emit_status("version_verify_failed", "error", {"error": str(e)})
            return self._result("verify", False, {"id": version_id, "error": str(e)})

        data = result if isinstance(result, dict) else {}
        total = int(data.get("total", 0) or 0)
        valid = int(data.get("valid", 0) or 0)
        invalid = list(data.get("invalid", []) or [])

        if total <= 0:
            self._emit_status("version_verify_empty", "warning", {})
        elif not invalid:
            self._emit_status("version_verify_ok", "success", {"total": total})
        else:
            self._emit_status(
                "version_verify_bad", "warning", {"total": total, "invalid": len(invalid)}
            )

        return self._result("verify", total > 0 and not invalid, {
            "id": version_id,
            "total": total,
            "valid": valid,
            "invalid": [str(item) for item in invalid[:MAX_INVALID_REPORTED]],
            "invalidCount": len(invalid),
        })

    # ─── 打开目录（B-21，新增）──────────────────────────────

    def open_folder(self, version_id: str) -> bool:
        """在系统文件管理器里打开版本目录。返回是否打开成功。"""
        vid = str(version_id or "").strip()
        version_dir = self._versions_dir() / vid
        if not vid or not version_dir.is_dir():
            self._emit_status("version_detail_missing", "error", {})
            return False
        try:
            self._open_path(str(version_dir))
        except Exception as e:  # noqa: BLE001 - 打不开文件管理器只报一句
            self.log.error("打开目录失败 (%s): %s", version_dir, e)
            self._emit_status("version_open_folder_failed", "error", {"error": str(e)})
            return False
        return True

    def _open_path(self, path: str) -> None:
        if self._opener is not None:
            self._opener(path)
            return
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606 - 打开目录不是执行程序
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])

    # ─── 内部 ───────────────────────────────────────────────

    def _versions_dir(self) -> Path:
        try:
            return Path(self.config.get_versions_dir())
        except Exception as e:  # noqa: BLE001 - 配置异常退化成仓库下的 .minecraft
            self.log.warning("取版本目录失败: %s", e)
            return Path(".minecraft") / "versions"

    def _minecraft_dir(self) -> Path:
        try:
            return Path(self.config.minecraft_dir)
        except Exception as e:  # noqa: BLE001
            self.log.warning("取 .minecraft 目录失败: %s", e)
            return Path(".minecraft")

    def _resolve_launcher(self) -> Any:
        if self._launcher is not None:
            return self._launcher
        try:
            self._launcher = self.context.try_get("launcher")
        except Exception as e:  # noqa: BLE001 - 上下文不可用时降级为"没有核心"
            self.log.warning("取 launcher 失败: %s", e)
            self._launcher = None
        return self._launcher

    def _result(self, kind: str, ok: bool, data: Dict[str, Any]) -> Dict[str, Any]:
        self._emit("on_result", kind, bool(ok), dict(data))
        return dict(data)

    def _emit_status(self, key: str, level: str, params: Dict[str, Any]) -> None:
        self._emit("on_status", key, level, dict(params))

    def _emit(self, name: str, *args: Any) -> None:
        """调观察者；观察者抛异常不影响主流程（与 GameService 同一约定）。"""
        observer = self._observer
        if observer is None:
            return
        handler = getattr(observer, name, None)
        if handler is None:
            return
        try:
            handler(*args)
        except Exception as e:  # noqa: BLE001 - 界面回调坏了不该让后台任务崩掉
            self.log.warning("观察者回调 %s 失败: %s", name, e)


__all__ = [
    "VersionService",
    "SORT_KEYS",
    "SORT_LOADER",
    "SORT_NAME",
    "SORT_VANILLA",
    "build_display_text",
    "row_from_info",
]
