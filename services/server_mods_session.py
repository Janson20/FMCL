"""服务器模组 / 资源包本地管理的会话状态机（阶段 3 任务 3.12）。

旧实现 ``ui/windows/server_resource_manager.py`` 把目录推导、扫描、搜索、分页、
模组元数据、启停、删除、导出、更新检查都写在 Tk 窗口里；阶段 1 已经把纯操作
搬进 ``services/resource_service.py``。本模块补的是**会话状态**：当前服务器 /
当前类型 / 搜索词 / 页码 / 元数据 / 进度 / 更新结果，以及“换服务器要清什么”。

和 ``services/resource_session.py`` 的区别只有一处，但很关键：服务器目录不是
``.minecraft/versions/<id>/...``，而是 ``get_server_dir()/<id>/mods|resourcepacks``。
旧代码用字符串里有没有 forge/fabric 来猜隔离层级，QML 侧永远有真实服务器名，
所以这里直接拼 ``<server_root>/<version>/<kind>``，不再猜。

线程约定：本模块不起线程、不碰 Qt。长活（元数据 / 筛选 / 更新）由桥交给
``TaskRunner``，任务函数以 ``(session, ctx)`` 形式在这里实现，进度走 ctx.progress。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("services.server_mods_session")

KIND_MODS = "mods"
KIND_PACKS = "resourcepacks"
KIND_ORDER: Tuple[str, ...] = (KIND_MODS, KIND_PACKS)

KIND_LABEL_KEYS: Dict[str, str] = {KIND_MODS: "server_mods_title", KIND_PACKS: "resource_packs"}
KIND_DESC_KEYS: Dict[str, str] = {KIND_MODS: "server_rm_mods_desc", KIND_PACKS: "rm_resourcepacks_desc"}
KIND_SEARCH_KEYS: Dict[str, str] = {
    KIND_MODS: "rm_search_placeholder_mods",
    KIND_PACKS: "rm_search_placeholder_resourcepacks",
}
KIND_ICONS: Dict[str, str] = {KIND_MODS: "mods", KIND_PACKS: "resourcepack"}

STATE_IDLE = "idle"
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_EMPTY = "empty"
STATE_ERROR = "error"

PAGE_SIZE = 10
MOD_EXTENSIONS = (".jar", ".zip")
PACK_EXTENSIONS = (".zip",)


def kind_label_key(kind: str) -> str:
    return KIND_LABEL_KEYS.get(kind, KIND_MODS)


def kind_desc_key(kind: str) -> str:
    return KIND_DESC_KEYS.get(kind, "")


def search_placeholder_key(kind: str) -> str:
    return KIND_SEARCH_KEYS.get(kind, KIND_SEARCH_KEYS[KIND_MODS])


def kind_icon(kind: str) -> str:
    return KIND_ICONS.get(kind, "file")


def detect_server_game_version(version_id: str) -> str:
    """服务器目录名 → Minecraft 版本（解析不出返回空串）。"""
    try:
        from version_utils import parse_mc_version_from_id

        return str(parse_mc_version_from_id(str(version_id or "")) or "")
    except Exception:  # noqa: BLE001 - 纯展示/搜索过滤，解析失败按未知处理
        return ""


def detect_server_loader(version_id: str, server_dir: Any = None) -> str:
    """服务器目录名 → 加载器；名字里没有线索时看服务端安装出来的标记文件。

    为什么不能只靠目录名：整合包开服的服务器名来自整合包名（例如
    Fabulously Optimized-26.3），里面没有 fabric。旧服务端模组浏览器
    在这种情况下会直接拒绝安装；这里从目录结构补一次判定，判定不出来仍返回
    空串，由界面提示，不猜。
    """
    try:
        from version_utils import parse_mod_loader_from_version

        parsed = str(parse_mod_loader_from_version(str(version_id or "")) or "")
    except Exception:  # noqa: BLE001
        parsed = ""
    if parsed:
        return parsed
    if not server_dir:
        return ""
    root = Path(str(server_dir))
    markers = (
        (root / "fabric-server-launch.jar", "fabric"),
        (root / "libraries" / "net" / "fabricmc" / "fabric-loader", "fabric"),
        (root / "libraries" / "net" / "neoforged" / "neoforge", "neoforge"),
        (root / "libraries" / "net" / "minecraftforge" / "forge", "forge"),
        (root / "libraries" / "org" / "quiltmc" / "quilt-loader", "quilt"),
        (root / "quilt-server-launch.jar", "quilt"),
    )
    for path, loader in markers:
        try:
            if path.exists():
                return loader
        except OSError:
            continue
    return ""


class ServerModsSession:
    """一台服务器的本地模组 / 资源包状态（桥只读属性、只调方法）。"""

    page_size: int = PAGE_SIZE

    def __init__(
        self,
        service: Any,
        *,
        callbacks: Optional[Dict[str, Callable[..., Any]]] = None,
        version_id: str = "",
        tasks: Any = None,
    ) -> None:
        self._service = service
        self._callbacks = dict(callbacks or {})
        self._tasks = tasks
        self._version = str(version_id or "").strip()
        self._kind = KIND_MODS
        self._search = ""
        self._page = 1
        self._items: List[Dict[str, Any]] = []
        self._metadata: List[Dict[str, Any]] = []
        self._visible: List[Dict[str, Any]] = []
        self._total = 0
        self._pages = 1
        self._state = STATE_IDLE
        self._error = ""
        self._busy = False
        self._filtering = False
        self._checking = False
        self._progress: Dict[str, Any] = {}

    # ─── 基础属性 ───────────────────────────────────────────

    @property
    def version_id(self) -> str:
        return self._version

    @property
    def kind(self) -> str:
        return self._kind

    @property
    def kind_label_key(self) -> str:
        return kind_label_key(self._kind)

    @property
    def kind_desc_key(self) -> str:
        return kind_desc_key(self._kind)

    @property
    def search_placeholder_key(self) -> str:
        return search_placeholder_key(self._kind)

    @property
    def kind_icon(self) -> str:
        return kind_icon(self._kind)

    @property
    def is_mods(self) -> bool:
        return self._kind == KIND_MODS

    @property
    def search(self) -> str:
        return self._search

    @property
    def page(self) -> int:
        return self._page

    @property
    def page_count(self) -> int:
        return self._pages

    @property
    def total(self) -> int:
        return self._total

    @property
    def items(self) -> List[Dict[str, Any]]:
        return list(self._visible[(self._page - 1) * self.page_size : self._page * self.page_size])

    @property
    def state(self) -> str:
        return self._state

    @property
    def error(self) -> str:
        return self._error

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def filtering(self) -> bool:
        return self._filtering

    @property
    def checking(self) -> bool:
        return self._checking

    @property
    def progress(self) -> Dict[str, Any]:
        return dict(self._progress)

    @property
    def tasks(self) -> Any:
        if self._tasks is not None:
            return self._tasks
        return getattr(self._service, "tasks", None)

    # ─── 切换服务器 / 类型 ──────────────────────────────────

    def activate(self, version: str, kind: str) -> bool:
        """切换服务器或类型；返回是否真的换了（换了才需要重新扫描）。"""
        wanted_version = str(version or "").strip()
        wanted_kind = str(kind or "").strip()
        if wanted_kind not in KIND_ORDER:
            wanted_kind = KIND_MODS
        changed = wanted_version != self._version or wanted_kind != self._kind
        if not changed:
            return False
        self._version = wanted_version
        self._kind = wanted_kind
        self._search = ""
        self._page = 1
        self._items = []
        self._metadata = []
        self._visible = []
        self._total = 0
        self._pages = 1
        self._state = STATE_IDLE
        self._error = ""
        self._progress = {}
        return True

    # ─── 路径 ───────────────────────────────────────────────

    @property
    def server_dir(self) -> Path:
        if not self._version:
            raise RuntimeError("未指定服务器")
        fn = self._callbacks.get("get_server_dir")
        if not callable(fn):
            raise RuntimeError("get_server_dir 回调未注册")
        return Path(str(fn())) / self._version

    def directory(self) -> Path:
        path = self.server_dir / self._kind
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning("创建服务器资源目录失败 (%s): %s", path, e)
        return path

    # ─── 扫描 ───────────────────────────────────────────────

    def load(self) -> Dict[str, Any]:
        """同步扫描目录；模组类型只到“目录扫描”，元数据另外异步提取。"""
        self._page = 1
        self._error = ""
        self._metadata = []
        self._busy = False
        self._progress = {}
        try:
            directory = self.directory()
            self._items = list(self._service.scan_resources(directory, self._kind))
        except Exception as e:  # noqa: BLE001
            logger.error("扫描服务器资源目录失败: %s", e)
            self._items = []
            self._visible = []
            self._total = 0
            self._pages = 1
            self._state = STATE_ERROR
            self._error = str(e)
            return self.result()

        if not self._items:
            self._visible = []
            self._total = 0
            self._pages = 1
            self._state = STATE_EMPTY
            return self.result()

        if self.is_mods:
            self._visible = []
            self._total = 0
            self._pages = 1
            self._state = STATE_LOADING
            return self.result()

        return self.repage()

    def apply_metadata(self, rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        self._metadata = [dict(r) for r in (rows or [])]
        self._busy = False
        self._progress = {}
        return self.repage()

    def apply_scan_fallback(self) -> Dict[str, Any]:
        """元数据任务被取消/失败时的降级：用扫描结果铺出可操作的列表。"""
        self._metadata = [
            {
                "name": str(item.get("name", "") or ""),
                "modid": "",
                "version": "",
                "author": "",
                "description": "",
                "icon_base64": None,
                "path": str(item.get("path", "") or ""),
                "size": item.get("size", ""),
                "disabled": bool(item.get("disabled")),
                "filename": str(item.get("name", "") or ""),
            }
            for item in self._items
        ]
        self._busy = False
        self._progress = {}
        return self.repage()

    def repage(self) -> Dict[str, Any]:
        source = self._metadata if self.is_mods else self._items
        if self._search:
            if self.is_mods:
                filtered = [m for m in source if self._matches_mod(m, self._search)]
            else:
                filtered = [m for m in source if self._search in str(m.get("name", "")).lower()]
        else:
            filtered = list(source)
        self._visible = filtered
        self._total = len(filtered)
        self._pages = max(1, (self._total + self.page_size - 1) // self.page_size)
        self._page = max(1, min(self._page, self._pages))
        if not source:
            self._state = STATE_EMPTY
        elif not filtered:
            self._state = STATE_READY
        else:
            self._state = STATE_READY
        return self.result()

    @staticmethod
    def _matches_mod(row: Dict[str, Any], needle: str) -> bool:
        for key in ("name", "modid", "author", "description", "filename"):
            if needle in str(row.get(key, "")).lower():
                return True
        return False

    def set_search(self, text: str) -> Dict[str, Any]:
        self._search = str(text or "").strip().lower()
        self._page = 1
        return self.repage()

    def goto(self, page: int) -> Dict[str, Any]:
        self._page = int(page or 1)
        return self.repage()

    def result(self) -> Dict[str, Any]:
        return {
            "state": self._state,
            "error": self._error,
            "rows": self.items,
            "page": self._page,
            "page_count": self._pages,
            "total": self._total,
            "kind": self._kind,
        }

    # ─── 长活：元数据 / 筛选 / 更新 ─────────────────────────

    def metadata_task(self, ctx: Any, directory: Any = None) -> List[Dict[str, Any]]:
        directory = Path(str(directory)) if directory is not None else self.directory()
        return list(
            self._service.extract_mods_metadata(
                directory,
                status_callback=lambda done, total: ctx.progress(done, total, ""),
                cancel_event=ctx.cancel_event,
            )
            or []
        )

    def filter_task(self, ctx: Any, directory: Any = None) -> Any:
        directory = Path(str(directory)) if directory is not None else self.directory()
        return self._service.filter_client_mods(
            str(directory),
            use_online=True,
            dry_run=False,
            progress_callback=lambda done, total, name="": ctx.progress(done, total, name or ""),
        )

    def prepare_update_check(self) -> Tuple[Optional[Tuple[Any, str, Optional[str]]], str]:
        game_version, mod_loader = self._service.update_targets(self._version)
        if not game_version:
            return None, "mod_update_unknown_version"
        mods = self._service.updatable_mods(self._metadata)
        if not mods:
            return None, "mod_update_no_modid"
        return (mods, game_version, mod_loader), ""

    def check_updates_task(self, ctx: Any, mods: Any, game_version: str, mod_loader: Optional[str]) -> Any:
        return self._service.collect_updates(
            mods, game_version, mod_loader, on_progress=lambda done, total: ctx.progress(done, total, "")
        )

    def prepare_batch_update(self, modids: Sequence[str]) -> Tuple[Optional[Tuple[Any, str, Optional[str]]], str]:
        game_version, mod_loader = self._service.update_targets(self._version)
        if not game_version or not mod_loader:
            return None, "mod_browser_unknown_loader"
        wanted = [str(m) for m in modids if str(m)]
        if not wanted:
            return None, ""
        return (wanted, game_version, mod_loader), ""

    def batch_update_task(
        self, ctx: Any, modids: Any, game_version: str, mod_loader: Optional[str], mods_dir: Any = None
    ) -> Any:
        return self._service.server_batch_update_mods(
            modids,
            Path(str(mods_dir)) if mods_dir is not None else self.directory(),
            game_version,
            mod_loader,
            on_progress=lambda done, total: ctx.progress(done, total, ""),
        )

    # ─── 文件操作 ───────────────────────────────────────────

    def install_files(self, paths: Sequence[str], directory: Any = None, kind: str = "") -> int:
        allowed = MOD_EXTENSIONS if (kind or self._kind) == KIND_MODS else PACK_EXTENSIONS
        directory = Path(str(directory)) if directory is not None else self.directory()
        installed = 0
        for raw in paths:
            source = Path(str(raw or ""))
            if not source.is_file() or source.suffix.lower() not in allowed:
                continue
            target = directory / source.name
            try:
                shutil.copy2(str(source), str(target))
                installed += 1
            except Exception as e:  # noqa: BLE001 - 单个失败不中断整批（旧实现同）
                logger.error("复制服务器资源失败 (%s): %s", source, e)
        return installed

    def toggle_mod(self, path: str, disabled: bool) -> bool:
        if not self.is_mods:
            return False
        return bool(self._service.toggle_server_mod(path, disabled))

    def delete(self, path: str) -> None:
        self._service.delete_path(path)

    def open_folder(self) -> None:
        self._service.open_server_mods_folder(self.directory())

    def export_text(self, header: str, enabled_label: str, disabled_label: str) -> str:
        return self._service.server_mod_list_text(self._metadata, header, enabled_label, disabled_label)

    @staticmethod
    def unknown_rows(result: Any) -> List[Dict[str, str]]:
        rows = []
        for row in getattr(result, "unknown", []) or []:
            rows.append({"file_name": str(row.get("file_name", "") or ""), "reason": str(row.get("reason", "") or "")})
        return rows


# ═══════════════════════════════════════════════════════════════════
# 待确认模组的批量禁用（3.12 弹窗）
# ═══════════════════════════════════════════════════════════════════


def server_mods_dir_for(callbacks: Dict[str, Callable[..., Any]], version: str) -> Path:
    """服务器名 → `<server_root>/<version>/mods`（弹窗跨页面调用时用）。

    与 :meth:`ServerModsSession.directory` 同一约定，但不依赖“当前会话是哪台服”：
    待确认弹窗出现在整合包安装页时，本地页可能还没被打开。
    """
    fn = callbacks.get("get_server_dir")
    if not callable(fn):
        raise RuntimeError("get_server_dir 回调未注册")
    return Path(str(fn())) / str(version or "").strip() / "mods"


def disable_server_mods(service: Any, mods_dir: Any, names: Sequence[str]) -> Tuple[int, List[str]]:
    """把指定的服务端模组追加 `.disabled`。

    ``names`` 只接受**文件名**（`Path(name).name`）：弹窗数据来自 jar 扫描结果，
    但跨页面传参时仍按不可信输入处理，不允许跳出 `mods/` 目录。

    返回 `(成功数, 失败文件名列表)`；单个失败不中断整批。已经 disabled 的不重复处理。
    """
    base = Path(str(mods_dir)).resolve()
    disabled = 0
    failed: List[str] = []
    for raw in names or []:
        name = Path(str(raw or "")).name
        if not name or name.endswith(".disabled"):
            continue
        target = (base / name).resolve()
        if base != target.parent and base not in target.parents:
            failed.append(name)
            continue
        if not target.is_file():
            failed.append(name)
            continue
        try:
            service.toggle_server_mod(str(target), False)
            disabled += 1
        except Exception as e:  # noqa: BLE001 - 单个失败不中断整批
            logger.warning("禁用待确认模组失败 (%s): %s", name, e)
            failed.append(name)
    return disabled, failed
