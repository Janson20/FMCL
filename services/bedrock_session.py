"""基岩版页面会话状态机（阶段 3 任务 3.15）。

链路：QML -> app/bridges/bedrock_bridge.py -> 本模块 -> services.bedrock_service。
本模块不 import Qt / ui.i18n，不弹窗、不起线程；只保存页面状态、算过滤分页、
用一把非阻塞锁做安装/启动/删除单飞，并把"缺条款 / .NET / 组件同意"算成前置计划。

服务层原有的 LAUNCH_LOCK / MSA_LOGIN_LOCK 继续保留；本模块解决页面重复点击。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from services.bedrock_service import (
    BUILD_TYPE_GDK,
    BedrockService,
    DotnetCheck,
    InstallOutcome,
    LaunchOutcome,
    RemoveOutcome,
)

STATE_IDLE = "idle"
STATE_LOADING = "loading"
STATE_READY = "ready"
STATE_ERROR = "error"

FILTER_ALL = ""
FILTER_KEYS = (FILTER_ALL, "UWP", BUILD_TYPE_GDK)

CALLBACK_NAMES = (
    "bedrock_get_available_versions",
    "bedrock_get_installed_versions",
    "bedrock_install_version",
    "bedrock_launch_version",
    "bedrock_remove_version",
    "bedrock_get_root",
)
WRITE_CALLBACK_NAMES = (
    "bedrock_install_version",
    "bedrock_launch_version",
    "bedrock_remove_version",
    "bedrock_get_root",
)


@dataclass(frozen=True)
class InstallPlan:
    version: str
    terms_required: bool
    dotnet: Optional[DotnetCheck]
    version_info: Dict[str, Any]


@dataclass(frozen=True)
class LaunchPlan:
    name: str
    component_consent_required: bool
    installed: Dict[str, Any]


class BedrockSession:
    """基岩版页面的全部会话状态（不含任何 UI 引用）。"""

    def __init__(self, service: BedrockService, *, launcher: Any = None) -> None:
        self._service = service
        self._launcher: Any = None
        self._callbacks: Dict[str, Callable[..., Any]] = {}
        self._installed: List[Dict[str, Any]] = []
        self._installed_state = STATE_IDLE
        self._installed_error = ""
        self._available: List[Dict[str, Any]] = []
        self._available_state = STATE_IDLE
        self._available_error = ""
        self._filtered: List[Dict[str, Any]] = []
        self._search = ""
        self._build_filter = FILTER_ALL
        self._page = 1
        self._selected = ""
        self._busy_kind = ""
        self._busy_name = ""
        self._op_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self.use_launcher(launcher)

    # ─── 注入 / 只读属性 ─────────────────────────────────────

    def use_launcher(self, launcher: Any) -> None:
        """替换核心启动器实例（启动链跑完后才出现，桥会重复调用）。

        核心的基岩回调在 MinecraftLauncher.get_callbacks() 里，键是
        bedrock_get_*，对应私有方法 _bedrock_get_*；直接从 launcher 上
        getattr("bedrock_get_*") 取不到（真实启动器只有下划线开头实现）。
        先走 get_callbacks()，再回退到公共属性，测试替身与真实核心都能用。
        """
        self._launcher = launcher
        callbacks: Dict[str, Callable[..., Any]] = {}
        if launcher is not None:
            source: Dict[str, Any] = {}
            getter = getattr(launcher, "get_callbacks", None)
            if callable(getter):
                try:
                    raw = getter()
                except Exception:
                    raw = None
                if isinstance(raw, dict):
                    source = raw
            for name in CALLBACK_NAMES:
                fn = source.get(name)
                if not callable(fn):
                    fn = getattr(launcher, name, None)
                if callable(fn):
                    callbacks[name] = fn
        self._callbacks = callbacks

    @property
    def callbacks(self) -> Dict[str, Callable[..., Any]]:
        return dict(self._callbacks)

    @property
    def launcher(self) -> Any:
        return self._launcher

    @property
    def core_ready(self) -> bool:
        return all(name in self._callbacks for name in CALLBACK_NAMES)

    @property
    def writable(self) -> bool:
        return all(name in self._callbacks for name in WRITE_CALLBACK_NAMES)

    @property
    def busy(self) -> bool:
        with self._op_lock:
            return bool(self._busy_kind)

    @property
    def busy_kind(self) -> str:
        with self._op_lock:
            return self._busy_kind

    @property
    def busy_name(self) -> str:
        with self._op_lock:
            return self._busy_name

    # ─── 单飞 ───────────────────────────────────────────────

    def begin(self, kind: str, name: str = "") -> bool:
        with self._op_lock:
            if self._busy_kind:
                return False
            self._busy_kind = str(kind or "")
            self._busy_name = str(name or "")
            return True

    def finish(self) -> None:
        with self._op_lock:
            self._busy_kind = ""
            self._busy_name = ""

    # ─── 已安装列表 ─────────────────────────────────────────

    @property
    def installed(self) -> List[Dict[str, Any]]:
        return list(self._installed)

    @property
    def installed_state(self) -> str:
        return self._installed_state

    @property
    def installed_error(self) -> str:
        return self._installed_error

    def mark_installed_loading(self) -> None:
        with self._state_lock:
            self._installed_state = STATE_LOADING
            self._installed_error = ""

    def apply_installed(self, rows: Optional[List[Dict[str, Any]]]) -> None:
        with self._state_lock:
            self._installed = list(rows or [])
            self._installed_state = STATE_READY
            self._installed_error = ""

    def fail_installed(self, message: str) -> None:
        with self._state_lock:
            self._installed_state = STATE_ERROR
            self._installed_error = str(message or "")

    def find_installed(self, name: str) -> Optional[Dict[str, Any]]:
        wanted = str(name or "")
        if not wanted:
            return None
        return next((row for row in self._installed if str(row.get("name", "")) == wanted), None)

    def load_installed(self) -> List[Dict[str, Any]]:
        return self._service.load_installed(self._callbacks)

    # ─── 可用版本 / 过滤 / 分页 ─────────────────────────────

    @property
    def available(self) -> List[Dict[str, Any]]:
        return list(self._available)

    @property
    def available_state(self) -> str:
        return self._available_state

    @property
    def available_error(self) -> str:
        return self._available_error

    @property
    def available_count(self) -> int:
        return len(self._available)

    @property
    def release_count(self) -> int:
        return self._service.count_release(self._available)

    def mark_available_loading(self) -> None:
        with self._state_lock:
            self._available_state = STATE_LOADING
            self._available_error = ""

    def apply_available(self, rows: Optional[List[Dict[str, Any]]]) -> None:
        with self._state_lock:
            self._available = list(rows or [])
            self._available_state = STATE_READY
            self._available_error = ""
            if self._selected and self.find_available(self._selected) is None:
                self._selected = ""
            self._rebuild(reset_page=True)

    def fail_available(self, message: str) -> None:
        with self._state_lock:
            self._available_state = STATE_ERROR
            self._available_error = str(message or "")

    def load_available(self, refresh: bool = False) -> List[Dict[str, Any]]:
        """refresh=True 强制重拉版本库；自动加载用 False（缓存优先）。"""
        return self._service.load_available(self._callbacks, refresh=refresh)

    @property
    def search(self) -> str:
        return self._search

    @property
    def build_filter(self) -> str:
        return self._build_filter

    @property
    def filter_index(self) -> int:
        try:
            return FILTER_KEYS.index(self._build_filter)
        except ValueError:
            return 0

    @property
    def page(self) -> int:
        return self._page

    @property
    def page_count(self) -> int:
        return self._service.total_pages(len(self._filtered))

    @property
    def filtered_count(self) -> int:
        return len(self._filtered)

    @property
    def page_rows(self) -> List[Dict[str, Any]]:
        if self._available_state != STATE_READY:
            return []
        result = self._service.paginate(self._filtered, self._page)
        if result.page != self._page:
            self._page = result.page
        return list(result.items)

    @property
    def selected(self) -> str:
        return self._selected

    def set_search(self, text: str) -> bool:
        value = str(text or "")
        with self._state_lock:
            if value == self._search:
                return False
            self._search = value
            self._rebuild(reset_page=True)
            return True

    def set_build_filter(self, key: str) -> bool:
        value = str(key or "")
        if value not in FILTER_KEYS:
            value = FILTER_ALL
        with self._state_lock:
            if value == self._build_filter:
                return False
            self._build_filter = value
            self._rebuild(reset_page=True)
            return True

    def select_version(self, version: str) -> bool:
        value = str(version or "")
        with self._state_lock:
            if not value:
                return False
            self._selected = value
            return True

    def clear_selection(self) -> None:
        with self._state_lock:
            self._selected = ""

    def find_available(self, version: str) -> Optional[Dict[str, Any]]:
        wanted = str(version or "")
        if not wanted:
            return None
        return next((row for row in self._available if str(row.get("version", "")) == wanted), None)

    def go_to_page(self, page: int) -> bool:
        with self._state_lock:
            target = self._service.paginate(self._filtered, int(page))
            if target.page == self._page:
                return False
            self._page = target.page
            return True

    def _rebuild(self, reset_page: bool) -> None:
        # all_label 传空串：空串既是 FILTER_ALL 的键，也不会匹配 UWP/GDK。
        self._filtered = self._service.filter_versions(
            self._available, self._search, self._build_filter, FILTER_ALL
        )
        if reset_page:
            self._page = 1
        slice_ = self._service.paginate(self._filtered, self._page)
        self._page = slice_.page

    # ─── 前置计划 ───────────────────────────────────────────

    def install_plan(self, version: str) -> InstallPlan:
        info = self.find_available(version)
        if info is None:
            raise ValueError("version_not_found")
        if not self._service.terms_accepted():
            return InstallPlan(version=str(version), terms_required=True, dotnet=None, version_info=info)
        dotnet: Optional[DotnetCheck] = None
        if self._service.requires_dotnet10(info):
            dotnet = self._service.check_dotnet10()
        return InstallPlan(version=str(version), terms_required=False, dotnet=dotnet, version_info=info)

    def accept_terms(self) -> None:
        self._service.mark_terms_accepted()

    def launch_plan(self, name: str) -> LaunchPlan:
        info = self.find_installed(name)
        if info is None:
            raise ValueError("installed_version_not_found")
        consent = False
        if self._service.component_setup_needed(self._callbacks, name):
            consent = not self._service.component_download_consented()
        return LaunchPlan(name=str(name), component_consent_required=bool(consent), installed=info)

    def accept_components(self) -> None:
        self._service.mark_components_consented()

    # ─── 编排入口（worker 线程）────────────────────────────

    def run_install(self, version: str) -> InstallOutcome:
        if not self.writable:
            raise RuntimeError("core_not_ready")
        return self._service.install_version(self._callbacks, version)

    def run_launch(
        self,
        name: str,
        *,
        status_cb: Optional[Callable[[str], None]] = None,
        download_notice: str = "",
        login_status_cb: Optional[Callable[[str], None]] = None,
        login_code_cb: Optional[Callable[[str, str], None]] = None,
        login_done_cb: Optional[Callable[[str, str], None]] = None,
    ) -> Optional[LaunchOutcome]:
        if not self.writable:
            raise RuntimeError("core_not_ready")
        return self._service.run_launch(
            self._callbacks,
            name,
            status_cb=status_cb,
            download_notice=download_notice,
            login_status_cb=login_status_cb,
            login_code_cb=login_code_cb,
            login_done_cb=login_done_cb,
        )

    def run_remove(self, name: str) -> RemoveOutcome:
        if not self.writable:
            raise RuntimeError("core_not_ready")
        return self._service.remove_version(self._callbacks, name)

    # ─── 打开目录（D-51）────────────────────────────────────

    def open_root(self) -> bool:
        """打开 bedrock_versions 根目录；没有路径返回 False，异常原样抛出。"""
        root = self._service.root_dir(self._callbacks)
        if not root:
            return False
        self._service.open_in_explorer(root)
        return True
