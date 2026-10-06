"""安装新版本向导服务（阶段 3 任务 3.3）。

## 范围（`03-phases.md` 的 3.3 + 2026-10-06 用户裁决）

| 对照表 | 能力 | 旧实现位置 |
|--------|------|-----------|
| B-09 | 手动输入版本 ID（从第一个空格处截断，旧规则） | `ui/app_base.py:1173-1181`、`ui/app_handlers.py:903-908` |
| B-10 | 9 种模组加载器下拉 + 兼容提示 | `ui/app_base.py:1193-1228`、`ui/app_handlers.py:294-313` |
| B-11 | 安装版本（进度 / 取消 / 完成提示） | `ui/app_base.py:1234-1243`、`ui/app_handlers.py:903-915` |
| B-12 | 安装整合包入口（**只做入口**，页面归 3.8） | `ui/app_base.py:1245-1254` |
| B-13 | 可用版本：正式版/测试版切换 + 每页 20 + 分页 + 点击回填 | `ui/app_base.py:1229-1352`、`ui/app_handlers.py:180-292` |

用户当场裁决的五条（`docs/refactor/16-phase3-execution-log.md` §14 有完整记录）：

1. **交付范围 A** —— B-09~B-13 + B-12 入口 + D-167「修复」；
2. **取消 = 尽力取消（A）** —— 核心层 `cancel_check`，文件级粒度，已下好的文件保留；
3. **呈现 = 独立路由页 `versions/install`（A）**（旧界面是版本页右侧同栏面板）；
4. **装完自动回版本列表并选中新版本（A）** —— 等效旧界面的"清空输入框 + 刷新列表"；
5. **兼容提示 = 旧提示 + 真实支持性查询（B）** —— mcllib 那四个加载器问各自官方 meta，
   四个自定义安装器按本地规则判（规则出处见 `local_loader_support()`）。

## 三条设计约束（与 `VersionService` 同一套）

1. **业务规则在这里**：版本 ID 截断、分页夹取、正式版/测试版归类、加载器 id ↔ 显示名
   ↔ i18n 键、兼容判定、安装结果判读，全在本模块；桥与 QML 不做判断（红线 2）。
2. **慢活走任务**：可用版本清单走 `self.tasks.submit`（读网络）；
   **安装任务走 `Tasks` 桥**（`app/bridges/qt_tasks.py`）—— 这样它才算"后台任务"
   （状态栏 `Shell.busy` 与后台任务指示只认经 `Tasks.submit` 提交的任务），
   进度与取消也由那条成熟链路负责（100ms 合并 + `Tasks.cancel`）。
3. **观察者回调可能在任意线程**：回调里只写普通 Python 属性 + emit 信号，不碰界面。

## 观察者协议（`set_observer(obj)`）

* ``on_available(rows, error)`` —— 可用版本清单加载完成（`error` 非空表示失败）
* ``on_status(key, level, params)`` —— 状态条文案（**i18n 键 + 参数**，翻译在桥里做）
* ``on_compat(loader, version, state, source, supported)`` —— 兼容查询结果
  （`state` 见 `COMPAT_*`；`supported` 是三态里的第三态：`True`/`False`/`None`(未知)）

**刻意没有 `on_result`**：安装 / 修复任务的结果由 `Tasks` 桥的 `taskFinished` 直接带回
（任务的返回值就是结果字典），再走一遍观察者协议等于同一条结果有两条路。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from services.base import Service
from version_utils import compare_versions, is_mc_normal_version, is_snapshot

# ─── 常量：与旧界面逐条对齐 ──────────────────────────────────────

#: 可用版本的两个标签页（旧 `version_tab_var` 的 `"release"` / `"snapshot"`）
TAB_RELEASE = "release"
TAB_SNAPSHOT = "snapshot"
TABS: Tuple[str, ...] = (TAB_RELEASE, TAB_SNAPSHOT)

#: 每页 20 条（旧 `self._page_size = 20`，`ui/app_base.py:1269`）
PAGE_SIZE = 20

#: "不装加载器"的稳定 id（旧界面存的是**译文**"无"，那是个坑：切语言就变。
#: 这里起用稳定 id，显示名与传给核心的名字在 `LOADER_DISPLAY` 里映射）。
LOADER_NONE = "none"

#: 9 种加载器，顺序**逐字对齐**旧下拉（`ui/app_base.py:1196-1206`）
LOADER_IDS: Tuple[str, ...] = (
    "none",
    "forge",
    "fabric",
    "neoforge",
    "quilt",
    "liteloader",
    "legacyfabric",
    "cleanroom",
    "optifine",
)

#: 稳定 id → i18n 键（`mod_loader_*` 那 9 个键在 4 个语言里都已存在）
LOADER_LABEL_KEYS: Dict[str, str] = {
    "none": "mod_loader_none",
    "forge": "mod_loader_forge",
    "fabric": "mod_loader_fabric",
    "neoforge": "mod_loader_neoforge",
    "quilt": "mod_loader_quilt",
    "liteloader": "mod_loader_liteloader",
    "legacyfabric": "mod_loader_legacyfabric",
    "cleanroom": "mod_loader_cleanroom",
    "optifine": "mod_loader_optifine",
}

#: 稳定 id → **传给核心的名字**。核心的 `install_version(version_id, mod_loader)`
#: 要的是显示名（`downloader.MOD_LOADER_IDS` 以 "Forge"/"Fabric"/… 为键），
#: 不装加载器时旧界面传的是 `"无"` —— 这里保持同一个字面量。
LOADER_DISPLAY: Dict[str, str] = {
    "none": "无",
    "forge": "Forge",
    "fabric": "Fabric",
    "neoforge": "NeoForge",
    "quilt": "Quilt",
    "liteloader": "LiteLoader",
    "legacyfabric": "LegacyFabric",
    "cleanroom": "Cleanroom",
    "optifine": "OptiFine",
}

#: mcllib 里有官方 meta 可查的四个（核心 `check_mod_loader_support()` 认这四个）
QUERYABLE_LOADERS: Tuple[str, ...] = ("forge", "fabric", "neoforge", "quilt")

#: 其余四个是 `downloader.py` 里的自定义安装器 —— 它们**没有查询接口**
#: （`_install_liteloader` / `_install_legacyfabric` / `_install_cleanroom` /
#: `_install_optifine` 的签名里连 callback 都没有），只能按本地规则判。
CUSTOM_LOADERS: Tuple[str, ...] = ("liteloader", "legacyfabric", "cleanroom", "optifine")

#: 兼容查询状态（给 QML 直接绑）
COMPAT_IDLE = "idle"
COMPAT_CHECKING = "checking"
COMPAT_OK = "ok"
COMPAT_BAD = "bad"
COMPAT_UNKNOWN = "unknown"

#: 任务结果状态
RESULT_DONE = "done"
RESULT_CANCELLED = "cancelled"
RESULT_FAILED = "failed"

#: 可用版本清单的复用窗口（秒）。用户在版本页与向导页之间来回走时不该每次都联网；
#: 点刷新（`force=True`）永远重新取。旧界面每次刷新都重新联网 —— 这条是**刻意的行为变更**，
#: 已记在 `04-parity-matrix.md` 的 B-13 备注里。
AVAILABLE_TTL_SECONDS = 300.0

#: LiteLoader 官方清单里最后一条正式版就是 1.12.2（2026-10 核对）。
#: 只有**上界**是本地规则；1.7.10~1.12.2 之间到底有没有，还要问它自己的清单，
#: 所以那个区间返回"未知"而不是"支持"（宁可说不知道，也不谎报支持）。
LITELOADER_LAST_VERSION = "1.12.2"


# ─── 纯函数（无 I/O，测试直接打）─────────────────────────────────


def clean_version_id(text: str) -> str:
    """从列表行或用户输入里取出可用的版本 ID。

    旧规则（`ui/app_handlers.py:289`）：``version_id.split()[0]`` —— 从**第一个空白**处
    截断。Mojang 清单里个别条目的 id 带后缀，旧界面用这一刀处理，这里逐字保留。
    """
    parts = str(text or "").split()
    return parts[0] if parts else ""


def split_available(versions: Sequence[Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把核心给的清单按 正式版 / 测试版 分成两份，**保持上游顺序**（新 → 旧）。

    旧实现（`ui/app_handlers.py:172-174`）只认 ``type == "release"`` 与
    ``type == "snapshot"``，``old_beta`` / ``old_alpha`` 直接不进列表 —— 这里保持一致。
    """
    release: List[Dict[str, Any]] = []
    snapshot: List[Dict[str, Any]] = []
    for item in versions or []:
        if not isinstance(item, dict):
            continue
        version_id = str(item.get("id", "") or "").strip()
        if not version_id:
            continue
        kind = str(item.get("type", "") or "")
        if kind not in TABS:
            continue
        row = {"id": version_id, "type": kind, "snapshot": kind == TAB_SNAPSHOT}
        (snapshot if kind == TAB_SNAPSHOT else release).append(row)
    return release, snapshot


def page_slice(rows: Sequence[Dict[str, Any]], page: int, size: int = PAGE_SIZE) -> Dict[str, Any]:
    """切片 + 夹取页码（旧 `_render_current_tab` 的语义：页码越界就夹到合法范围）。

    返回 ``{"rows", "page", "pageCount", "total"}``；空列表也是 1 页（旧界面显示 "1/1"）。
    """
    items = list(rows or [])
    size = max(1, int(size or PAGE_SIZE))
    total = len(items)
    page_count = max(1, (total + size - 1) // size)
    try:
        wanted = int(page)
    except (TypeError, ValueError):
        wanted = 1
    current = max(1, min(page_count, wanted))
    start = (current - 1) * size
    return {"rows": items[start:start + size], "page": current, "pageCount": page_count, "total": total}


def is_release_version(version: str) -> bool:
    """是不是"正式版号"（快照 / 预发布 / RC 都不算）。

    **输入请先过 `clean_version_id()`**：本函数不替你截断列表行那种带后缀的写法
    （`"1.20.4 (release)"` 会被判成非正式版）。服务内部的唯一调用方
    `local_loader_support()` 已经清洗过了 —— 留着这条说明是免得后来者直接拿用户
    原始输入来喂它。
    """
    value = str(version or "").strip()
    return bool(value) and is_mc_normal_version(value) and not is_snapshot(value)


def local_loader_support(loader: str, minecraft_version: str) -> Tuple[Optional[bool], str]:
    """四个自定义安装器的**本地**兼容规则。返回 ``(三态, 依据)``。

    每条规则都有出处（不写"我记得"）：

    * ``cleanroom`` —— `downloader.py:794` 自己的判断就是"仅支持 MC 1.12.2"；
    * ``liteloader`` —— 官方清单最后一条正式版是 1.12.2（`LITELOADER_LAST_VERSION`），
      超过它判不支持；**没超过时返回"未知"**，因为"这个版本有没有 LiteLoader"
      只有它自己的 versions.json 说了算（本地不猜、也不联网）；
    * 四个自定义安装器都是**按正式版号去查各自清单**的（LiteLoader 的 versions.json、
      OptiFine 的 BMCLAPI 列表、LegacyFabric 的 game 列表都以正式版号为键），
      所以快照 / 预发布判不支持 —— 这是**保守提示**而不是硬结论（界面只给警告）。

    写错过一次，留个记号：`downloader.py:640-643` 里的 ``version.startswith("2.0")``
    是 **LegacyFabric 的版本号归一化**（它的清单用 ``2point0_`` 前缀表示 2.0），
    **不是**"不支持 2.0"—— 所以这里没有那条规则。

    ``(None, "")`` = 本地判不了（要么不是自定义加载器，要么得问清单）。
    """
    loader_id = str(loader or "").strip().lower()
    version = clean_version_id(minecraft_version)
    if not version or loader_id not in CUSTOM_LOADERS:
        return None, ""
    if not is_release_version(version):
        return False, "local:release_only"
    if loader_id == "cleanroom":
        return version == "1.12.2", "local:cleanroom"
    if loader_id == "liteloader":
        try:
            if compare_versions(version, LITELOADER_LAST_VERSION) > 0:
                return False, "local:liteloader_bound"
        except Exception:  # noqa: BLE001 - 版本号千奇百怪，比不了就当"未知"
            return None, ""
        return None, ""
    return None, ""


#: 缓存里"没查过"的哨兵（`None` 是一个合法结果，不能拿它当"没有"）
_MISSING = object()

#: "查不出来"的缓存有效期（秒）：网络抖出来的 unknown 不该钉死一整轮会话
UNKNOWN_TTL_SECONDS = 60.0


class InstallService(Service):
    """安装向导：可用版本清单（读）+ 兼容提示（查）+ 安装任务（写）。"""

    name = "install"
    label = "安装新版本向导"

    def __init__(self, context: Any = None, *, launcher: Any = None) -> None:
        """
        Args:
            context: ``AppContext``（由 ``Service.attach`` 注入）。
            launcher: 显式注入的 ``MinecraftLauncher``。**测试用**；生产路径为 None，
                届时从 ``AppContext`` 取 ``"launcher"``。
        """
        super().__init__(context)
        self._launcher = launcher
        self._observer: Any = None
        self._lock = threading.RLock()
        self._release: List[Dict[str, Any]] = []
        self._snapshot: List[Dict[str, Any]] = []
        self._available_error = ""
        self._available_at = 0.0
        self._loading = False
        #: ``(loader, version) -> (state, source, supported, at)``；命中就不重复联网
        self._compat_cache: Dict[Tuple[str, str], Tuple[str, str, Optional[bool], float]] = {}

    # ─── 装配 ───────────────────────────────────────────────

    def use_launcher(self, launcher: Any) -> None:
        """注入启动器核心（``AppContext`` 里那份唯一实例）。"""
        self._launcher = launcher

    def set_observer(self, observer: Any) -> None:
        """设置观察者（界面桥）。同一时刻只保留一个 —— 它代表"当前那个界面"。"""
        self._observer = observer

    def _resolve_launcher(self) -> Any:
        if self._launcher is not None:
            return self._launcher
        try:
            self._launcher = self.context.try_get("launcher")
        except Exception as e:  # noqa: BLE001 - 上下文不可用时降级为"没有核心"
            self.log.warning("取 launcher 失败: %s", e)
            self._launcher = None
        return self._launcher

    # ─── 可用版本清单（B-13）────────────────────────────────

    @property
    def release_count(self) -> int:
        with self._lock:
            return len(self._release)

    @property
    def snapshot_count(self) -> int:
        with self._lock:
            return len(self._snapshot)

    def available_is_fresh(self) -> bool:
        """清单是不是还在复用窗口内（窗口内不联网，旧界面是每次都联网）。"""
        with self._lock:
            if not self._release and not self._snapshot:
                return False
            return (time.time() - self._available_at) < AVAILABLE_TTL_SECONDS

    def load_available(self, *, force: bool = False) -> Any:
        """异步取可用版本清单。返回 ``TaskHandle``（复用缓存时返回 ``None``）。

        ``force=True`` 是刷新按钮：丢掉复用窗口，无条件重新联网。
        """
        if not force and self.available_is_fresh():
            self._emit_available()
            return None
        with self._lock:
            if self._loading:
                return None  # 已经在取，别叠加（旧界面连点刷新会起好几个线程）
            self._loading = True
        self._emit_status("loading_available", "loading", {})
        return self.tasks.submit(
            self._load_sync,
            name="install.available",
            on_error=lambda e: self._load_done([], str(e)),
        )

    def _load_sync(self) -> List[Dict[str, Any]]:
        launcher = self._resolve_launcher()
        if launcher is None:
            self._load_done([], "launcher_unavailable")
            return []
        try:
            versions = launcher.get_available_versions()
        except Exception as e:  # noqa: BLE001
            self._load_done([], str(e))
            return []
        rows = [dict(item) for item in (versions or []) if isinstance(item, dict)]
        self._load_done(rows, "")
        return rows

    def _load_done(self, rows: Sequence[Any], error: str) -> None:
        release, snapshot = split_available(rows)
        with self._lock:
            self._loading = False
            if error:
                # 失败**不清空**手里已有的清单：一次网络抖动不该让用户面前的列表消失
                self._available_error = str(error)
            else:
                self._available_error = ""
                self._release, self._snapshot = release, snapshot
                self._available_at = time.time()
            payload = list(self._release) + list(self._snapshot)
            counts = (len(self._release), len(self._snapshot))
            message = self._available_error
        if message:
            self._emit_status("version_available_failed", "warning", {"error": message})
        else:
            self._emit_status(
                "version_available_loaded", "success", {"release": counts[0], "snapshot": counts[1]}
            )
        self._emit("on_available", payload, message)

    def page_of(self, tab: str, page: int) -> Dict[str, Any]:
        """某一标签页的第 ``page`` 页（页码会被夹到合法范围）。"""
        key = tab if tab in TABS else TAB_RELEASE
        with self._lock:
            rows = list(self._release if key == TAB_RELEASE else self._snapshot)
            release_count, snapshot_count = len(self._release), len(self._snapshot)
        data = page_slice(rows, page)
        data.update({"tab": key, "releaseCount": release_count, "snapshotCount": snapshot_count})
        return data

    def _emit_available(self) -> None:
        with self._lock:
            payload = list(self._release) + list(self._snapshot)
            message = self._available_error
        self._emit("on_available", payload, message)

    # ─── 兼容提示（B-10 的"真实支持性查询"，用户裁决 B）─────

    def check_compat(self, version_id: str, loader: str) -> Any:
        """异步查"这个加载器支不支持这个版本"。返回 ``TaskHandle``（命中缓存返回 ``None``）。

        三态语义见 `local_loader_support()` 与核心 `check_mod_loader_support()`：
        **"查不出来"单独一态**，界面按"参考信息"显示，绝不据此拦住安装。
        """
        version = clean_version_id(version_id)
        loader_id = str(loader or "").strip().lower()
        if not version or loader_id in ("", LOADER_NONE) or loader_id not in LOADER_IDS:
            self._compat_done(loader_id, version, COMPAT_IDLE, "", None)
            return None
        key = (loader_id, version)
        with self._lock:
            cached = self._compat_cache.get(key)
        if cached is not None:
            state, source, supported, at = cached
            if state != COMPAT_UNKNOWN or (time.time() - at) < UNKNOWN_TTL_SECONDS:
                self._compat_done(loader_id, version, state, source, supported)
                return None
        self._compat_done(loader_id, version, COMPAT_CHECKING, "", None)
        return self.tasks.submit(self._compat_sync, loader_id, version, name=f"install.compat.{loader_id}")

    def _compat_sync(self, loader: str, version: str) -> Dict[str, Any]:
        supported, source = local_loader_support(loader, version)
        if supported is None and loader in QUERYABLE_LOADERS:
            launcher = self._resolve_launcher()
            if launcher is not None:
                try:
                    supported = launcher.check_mod_loader_support(loader, version)
                except Exception as e:  # noqa: BLE001 - 查询失败=未知
                    self.log.info("兼容查询失败 (%s/%s): %s", loader, version, e)
                    supported = None
                source = "remote"
        if supported is None:
            state = COMPAT_UNKNOWN
        else:
            state = COMPAT_OK if supported else COMPAT_BAD
        with self._lock:
            self._compat_cache[(loader, version)] = (state, source, supported, time.time())
        self._compat_done(loader, version, state, source, supported)
        return {"loader": loader, "version": version, "state": state, "source": source}

    def _compat_done(
        self, loader: str, version: str, state: str, source: str, supported: Optional[bool]
    ) -> None:
        self._emit("on_compat", loader, version, state, source, supported)

    # ─── 安装任务（B-11；工作者由 `InstallBridge` 注册到 `Tasks` 桥）──

    def install_task(self, ctx: Any, *, version_id: str = "", loader: str = LOADER_NONE) -> Dict[str, Any]:
        """安装任务的**工作者**（在工作线程上跑，``ctx`` 来自 `Tasks.submit`）。

        返回值就是结果字典（``Tasks`` 桥原样回传）：``{"ok", "state", "requested",
        "installed", "loader", "reason", "error"}``。

        为什么"取消"与"失败"要分成两个 state：用户点了取消之后界面不该说"安装失败"
        —— 那不是错误，是他的决定（`launcher/errors.py` 里把语义写清了）。
        """
        from launcher.errors import InstallCancelled

        requested = clean_version_id(version_id)
        loader_id = loader if loader in LOADER_IDS else LOADER_NONE
        display = LOADER_DISPLAY.get(loader_id, "无")
        base = {"requested": requested, "loader": loader_id, "loaderName": display}

        if not requested:
            self._emit_status("version_id_required", "error", {})
            return {**base, "ok": False, "state": RESULT_FAILED, "reason": "invalid"}

        launcher = self._resolve_launcher()
        if launcher is None:
            self._emit_status("version_install_failed", "error", {"version": requested})
            return {**base, "ok": False, "state": RESULT_FAILED, "reason": "launcher_unavailable"}

        if loader_id == LOADER_NONE:
            self._emit_status("version_installing", "loading", {"version": requested})
        else:
            self._emit_status("version_installing_loader", "loading", {"version": requested, "loader": display})

        def report_progress(current: int, total: int, _text: str = "") -> None:
            # 进度上报失败绝不能影响安装本身（旧实现里回调是直接打给界面的，
            # 界面一抖整个安装就崩了 —— 这里吞掉异常）。
            try:
                ctx.progress(int(current or 0), int(total or 0), "")
            except Exception:  # noqa: BLE001
                pass

        def cancelled() -> bool:
            try:
                return bool(ctx.cancelled)
            except Exception:  # noqa: BLE001 - 问不到就当没取消
                return False

        try:
            ok, installed = launcher.install_version(
                requested, display, on_progress=report_progress, cancel_check=cancelled
            )
        except InstallCancelled:
            self._emit_status("version_install_cancelled", "info", {"version": requested})
            self._refresh_versions()
            return {**base, "ok": False, "state": RESULT_CANCELLED}
        except Exception as e:  # noqa: BLE001
            self.log.error("安装 %s 失败: %s", requested, e)
            self._emit_status("version_install_failed", "error", {"version": requested})
            return {**base, "ok": False, "state": RESULT_FAILED, "error": str(e)}

        if not ok:
            self._emit_status("version_install_failed", "error", {"version": requested})
            return {**base, "ok": False, "state": RESULT_FAILED, "reason": "install_failed"}

        installed_id = str(installed or requested)
        self._emit_status("version_installed", "success", {"version": installed_id})
        self._note_installed(installed_id)
        return {**base, "ok": True, "state": RESULT_DONE, "installed": installed_id}

    # ─── 装完之后（用户裁决 4：回列表并选中它）───────────────

    def _version_service(self) -> Any:
        try:
            return self.try_get("version")
        except Exception as e:  # noqa: BLE001 - 版本服务缺席只是不自动刷新
            self.log.warning("取版本服务失败: %s", e)
            return None

    def _note_installed(self, version_id: str) -> None:
        """告诉版本服务"刚装好这个版本"：它会重扫列表并记下"待选中"。

        为什么不在这里直接推路由：**导航是界面的事**（桥在做），服务只负责
        "世界变了"这一条事实 —— 谁在看着列表谁自己刷新（红线 2）。
        """
        service = self._version_service()
        note = getattr(service, "note_installed", None)
        if callable(note):
            try:
                note(version_id)
            except Exception as e:  # noqa: BLE001
                self.log.warning("通知版本服务失败: %s", e)

    def _refresh_versions(self) -> None:
        """取消之后也要重扫：磁盘上可能留下了一个不完整的版本目录。"""
        service = self._version_service()
        load = getattr(service, "load", None)
        if callable(load):
            try:
                load(force=True)
            except Exception as e:  # noqa: BLE001
                self.log.warning("刷新版本列表失败: %s", e)

    # ─── 内部 ───────────────────────────────────────────────

    def _emit(self, name: str, *args: Any) -> None:
        observer = self._observer
        callback = getattr(observer, name, None) if observer is not None else None
        if callback is None:
            return
        try:
            callback(*args)
        except Exception as e:  # noqa: BLE001 - 观察者出错不该把服务带崩
            self.log.warning("观察者回调 %s 失败: %s", name, e)

    def _emit_status(self, key: str, level: str, params: Dict[str, Any]) -> None:
        self._emit("on_status", key, level, dict(params))


__all__ = [
    "AVAILABLE_TTL_SECONDS",
    "COMPAT_BAD",
    "COMPAT_CHECKING",
    "COMPAT_IDLE",
    "COMPAT_OK",
    "COMPAT_UNKNOWN",
    "CUSTOM_LOADERS",
    "InstallService",
    "LOADER_DISPLAY",
    "LOADER_IDS",
    "LOADER_LABEL_KEYS",
    "LOADER_NONE",
    "PAGE_SIZE",
    "QUERYABLE_LOADERS",
    "RESULT_CANCELLED",
    "RESULT_DONE",
    "RESULT_FAILED",
    "TAB_RELEASE",
    "TAB_SNAPSHOT",
    "TABS",
    "clean_version_id",
    "is_release_version",
    "local_loader_support",
    "page_slice",
    "split_available",
]
