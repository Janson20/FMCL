"""联机会话（阶段 3 任务 3.13）。

链路：

    qml/pages/online/OnlinePage.qml
        -> app/bridges/online_bridge.py（QML 名 Online）
        -> services/online_session.py（本模块：状态机与全部时序）
        -> services/online_service.py（EasyTier / 陶瓦 / 转发 / 广播 / Ping 原语）

为什么需要这一层：阶段 1.5 把联机的**原语**搬进了 services/，但"创建大厅 / 加入
大厅 / 退出 / 成员轮询 / 客机 3 秒后建连 / 主机 5 秒后就绪"这些**时序**仍留在
ui/app_online.py（Tk 的 after 与线程）。QML 不能复用 Tk 的界面类，所以把时序原样
搬到这里；旧 Tk 路径不动，两者共用 services/online_service.py。

线程契约：
    - 公开方法只由桥在主线程调用。
    - 下载 / 启动 / 寻找房主 / 转发 / 停止在工作线程执行；EasyTier reader 线程、
      SCF 线程与定时器线程也会回调进来。
    - 本模块**不碰 Qt、不碰界面、不翻译文案**：只把状态与事件交给 observer，
      由桥排队回主线程、翻译、发 Qt 信号。
    - 所有晚到回调用代际号 generation 丢弃；detach 之后一个都不再投递。

既有缺陷的处置（保持 07-known-defects 的"已修正"状态）：
    - D-82：平台门控可达（QML 用空态页展示 online_unsupported_platform）。
    - D-83：安装成功只算旧版本目录清单，确认后才删；没有确认一个都不删。
    - D-86：日志由桥保留最近 5000 行（LogView 同上限），不再无上限。
    - D-90：worker 只投递事件，不直接碰界面；投递与渲染彻底分开。
    - D-107：解压清理已经由 online_service 修好，本模块不重复实现。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.online_service import (
    EASYTIER_VERSION,
    LOOPBACK,
    EasyTierManager,
    LobbyInfo,
    LobbyState,
    OnlineService,
)

logger = logging.getLogger(__name__)

#: 环境态（桥原样暴露给 QML）。
ENV_CHECKING = "checking"
ENV_READY = "ready"
ENV_MISSING = "missing"
ENV_INSTALLING = "installing"
ENV_FAILED = "failed"

#: 安装按钮态；与 env_state 分开是因为"失败可重试"需要记住上次结果。
INSTALL_IDLE = "idle"
INSTALL_RUNNING = "running"
INSTALL_DONE = "done"
INSTALL_FAILED = "failed"

#: 旧界面从 launcher 取的两个回调；取不到就按"未登录"处理。
CALLBACK_NAMES = ("get_jdz_username", "get_jdz_token")

#: 日志在桥里保留的最大行数（D-86；与 LogView 默认上限一致）。
LOG_BUFFER_MAX_LINES = 5000


def launcher_callbacks(launcher: Any) -> Dict[str, Callable[..., Any]]:
    """从核心启动器取净读账号回调；取不到的键不放进字典。"""
    out: Dict[str, Callable[..., Any]] = {}
    if launcher is None:
        return out
    for name in CALLBACK_NAMES:
        fn = getattr(launcher, name, None)
        if callable(fn):
            out[name] = fn
    return out


class OnlineSession:
    """联机页的全部会话状态（不含任何 UI / Qt 引用）。"""

    def __init__(
        self,
        *,
        service: Optional[OnlineService] = None,
        launcher: Any = None,
        achievement: Any = None,
        callbacks: Optional[Dict[str, Callable[..., Any]]] = None,
        observer: Any = None,
    ) -> None:
        self._service = service if service is not None else OnlineService()
        self._launcher = launcher
        self._achievement = achievement
        self._callbacks: Dict[str, Callable[..., Any]] = launcher_callbacks(launcher)
        if callbacks:
            self._callbacks.update(callbacks)
        self._observer = observer
        self._lock = threading.RLock()

        self._platform_supported = bool(self._service.is_platform_supported())
        self._manager: Optional[EasyTierManager] = None
        self._activated = False
        self._closing = False
        self._generation = 0

        self._env_state = ENV_CHECKING
        self._install_state = INSTALL_IDLE
        self._et_running = False
        self._et_pid = 0
        self._et_status_key = "online_et_stopped"
        self._et_status_params: Dict[str, Any] = {}

        self._lobby_state = LobbyState.IDLE
        self._lobby: Optional[LobbyInfo] = None
        self._is_host = False
        self._local_mc_port = 0
        self._lobby_status_key = ""
        self._lobby_status_params: Dict[str, Any] = {}
        self._lobby_status_level = "info"
        self._busy = ""

        self._members: List[Dict[str, Any]] = []
        self._guest_latency = 0

        self._scanning = False
        self._worlds: List[Dict[str, Any]] = []
        self._scan_ports: set = set()
        self._listener: Any = None
        self._scan_timer: Optional[threading.Timer] = None

        self._stale_versions: List[str] = []

        self._scf_client: Any = None
        self._forwarder: Any = None
        self._broadcast: Any = None
        self._game_watcher: Any = None
        self._member_timer: Optional[threading.Timer] = None
        self._ready_timer: Optional[threading.Timer] = None
        self._guest_timer: Optional[threading.Timer] = None
        self._stopping = False

    # ─── 观察者 ─────────────────────────────────────────────

    def set_observer(self, observer: Any) -> None:
        with self._lock:
            self._observer = observer

    def _emit(self, name: str, *args: Any) -> None:
        observer = self._observer
        if observer is None:
            return
        fn = getattr(observer, name, None)
        if not callable(fn):
            return
        try:
            fn(*args)
        except Exception as e:  # noqa: BLE001 - 观察者坏了不能拖垮联机线程
            logger.warning("联机观察者 %s 失败: %s", name, e)

    def _changed(self, section: str) -> None:
        self._emit("on_changed", section)

    def _status(self, key: str, level: str = "info", **params: Any) -> None:
        self._emit("on_status", key, level, dict(params))

    def _notice(self, key: str, level: str = "info", detail: str = "") -> None:
        self._emit("on_notice", key, level, str(detail or ""))

    def _log(self, text: Any) -> None:
        self._emit("on_log", str(text))

    def _log_key(self, key: str, **params: Any) -> None:
        self._emit("on_log_key", key, dict(params))

    # ─── 只读状态 ───────────────────────────────────────────

    @property
    def platform_supported(self) -> bool:
        return self._platform_supported

    @property
    def env_state(self) -> str:
        with self._lock:
            return self._env_state

    @property
    def install_state(self) -> str:
        with self._lock:
            return self._install_state

    @property
    def et_running(self) -> bool:
        with self._lock:
            return self._et_running

    @property
    def et_pid(self) -> int:
        with self._lock:
            return self._et_pid

    @property
    def et_status_key(self) -> str:
        with self._lock:
            return self._et_status_key

    @property
    def et_status_params(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._et_status_params)

    @property
    def lobby_state(self) -> str:
        with self._lock:
            return self._lobby_state.value

    @property
    def busy(self) -> str:
        with self._lock:
            return self._busy

    @property
    def has_lobby(self) -> bool:
        with self._lock:
            return self._lobby is not None

    @property
    def is_host(self) -> bool:
        with self._lock:
            return self._is_host

    @property
    def lobby_code(self) -> str:
        with self._lock:
            return self._lobby.full_code if self._lobby is not None else ""

    @property
    def local_port(self) -> int:
        with self._lock:
            return int(self._local_mc_port or 0)

    @property
    def lobby_status_key(self) -> str:
        with self._lock:
            return self._lobby_status_key

    @property
    def lobby_status_params(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._lobby_status_params)

    @property
    def lobby_status_level(self) -> str:
        with self._lock:
            return self._lobby_status_level

    @property
    def members(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._members]

    @property
    def guest_latency(self) -> int:
        with self._lock:
            return int(self._guest_latency or 0)

    @property
    def worlds(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._worlds]

    @property
    def scanning(self) -> bool:
        with self._lock:
            return self._scanning

    @property
    def stale_versions(self) -> List[str]:
        with self._lock:
            return list(self._stale_versions)

    @property
    def can_install(self) -> bool:
        with self._lock:
            return (
                self._platform_supported
                and self._env_state in (ENV_MISSING, ENV_FAILED)
                and self._install_state != INSTALL_RUNNING
            )

    @property
    def can_scan(self) -> bool:
        with self._lock:
            return self._platform_supported and not self._scanning

    @property
    def can_create(self) -> bool:
        with self._lock:
            return (
                self._platform_supported
                and not self._closing
                and not self._busy
                and self._lobby is None
            )

    @property
    def can_join(self) -> bool:
        return self.can_create

    @property
    def can_leave(self) -> bool:
        with self._lock:
            return (
                self._platform_supported
                and not self._closing
                and self._lobby is not None
                and self._lobby_state == LobbyState.CONNECTED
            )

    # ─── 登录 / 成就 / 线程工具 ──────────────────────────────

    def _callback_value(self, name: str) -> str:
        fn = self._callbacks.get(name)
        if not callable(fn):
            return ""
        try:
            value = fn()
        except Exception as e:  # noqa: BLE001 - 回调坏了按未登录处理
            logger.debug("联机回调 %s 失败: %s", name, e)
            return ""
        return str(value or "").strip()

    def login_error_key(self) -> str:
        """返回空串表示已登录；否则返回 i18n 键。"""
        if self._callback_value("get_jdz_username"):
            return ""
        if self._callback_value("get_jdz_token"):
            return "online_login_expired"
        return "online_need_login"

    def _display_name(self) -> str:
        return self._callback_value("get_jdz_username") or "Host"

    def _trigger_achievement(self, achievement_id: str) -> bool:
        """推进一次成就进度（与 ServerSession 同一口径，QML 永远不碰）。"""
        engine = None
        getter = getattr(self._achievement, "engine", None)
        if callable(getter):
            try:
                engine = getter()
            except Exception as e:  # noqa: BLE001
                logger.warning("取成就引擎失败: %s", e)
        if engine is None:
            try:
                from achievement_engine import get_achievement_engine

                engine = get_achievement_engine()
            except Exception as e:  # noqa: BLE001
                logger.debug("成就引擎不可用: %s", e)
        if engine is None:
            return False
        try:
            engine.update_progress(achievement_id, value=1, trigger_type="increment")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("触发成就 %s 失败: %s", achievement_id, e)
            return False

    def _spawn(
        self,
        name: str,
        work: Callable[[], Any],
        on_done: Callable[[Any], None],
        on_error: Callable[[Any], None],
    ) -> None:
        def run() -> None:
            try:
                result = work()
            except Exception as e:  # noqa: BLE001 - 工作线程异常必须回到 on_error
                try:
                    on_error(e)
                except Exception as inner:  # noqa: BLE001
                    logger.exception("联机任务 %s 的 on_error 失败: %s", name, inner)
                return
            try:
                on_done(result)
            except Exception as e:  # noqa: BLE001
                logger.exception("联机任务 %s 的 on_done 失败: %s", name, e)

        thread = threading.Thread(target=run, name=f"online-{name}", daemon=True)
        thread.start()

    def _guard(self, generation: int) -> bool:
        with self._lock:
            return not self._closing and generation == self._generation

    def _invalidate(self) -> int:
        with self._lock:
            self._generation += 1
            return self._generation

    def _schedule(self, seconds: float, fn: Callable[[], None], generation: int) -> Optional[threading.Timer]:
        def run() -> None:
            if not self._guard(generation):
                return
            try:
                fn()
            except Exception:  # noqa: BLE001 - 定时器里异常不能杀进程
                logger.exception("联机定时器任务失败")

        timer = threading.Timer(max(0.0, float(seconds)), run)
        timer.daemon = True
        timer.start()
        return timer

    def _cancel(self, timer: Optional[threading.Timer]) -> None:
        if timer is None:
            return
        try:
            timer.cancel()
        except Exception:  # noqa: BLE001
            pass

    def _ensure_manager(self) -> Optional[EasyTierManager]:
        with self._lock:
            if self._manager is not None:
                return self._manager
        try:
            manager = self._service.create_manager()
        except Exception as e:  # noqa: BLE001 - 目录/平台问题只让环境态失败
            logger.error("创建 EasyTier 管理器失败: %s", e)
            with self._lock:
                self._env_state = ENV_FAILED
            self._changed("env")
            return None
        # 中继节点预取推迟到真正启动大厅时（EasyTierManager.launch 内部会做），
        # 打开页面只读本地安装状态：不在页面构造期起网络线程，免得抢 Qt 的
        # QML 编译单元窗口（D-150 的同一类问题）。
        with self._lock:
            if self._manager is None:
                self._manager = manager
            return self._manager

    # ─── 激活 / 环境 / 安装 / 扫描 ───────────────────────────

    def activate(self) -> None:
        if self._closing or not self._platform_supported:
            return
        self._ensure_manager()
        with self._lock:
            first = not self._activated
            self._activated = True
        if first:
            self._log_key("status_ready")
        self.refresh_env(announce=first)

    def refresh_env(self, *, announce: bool = False) -> None:
        if not self._platform_supported:
            return
        manager = self._ensure_manager()
        if manager is None:
            return
        installed = bool(manager.is_installed)
        running = bool(manager.is_running)
        pid = int(manager.pid or 0) if running else 0
        with self._lock:
            before = (
                self._env_state,
                self._install_state,
                self._et_running,
                self._et_pid,
                self._et_status_key,
                tuple(sorted(self._et_status_params.items())),
            )
            self._env_state = ENV_READY if installed else ENV_MISSING
            self._et_running = running
            self._et_pid = pid
            if running:
                self._et_status_key = "online_et_running"
                self._et_status_params = {"pid": pid}
            else:
                self._et_status_key = "online_et_stopped"
                self._et_status_params = {}
            if installed:
                if self._install_state != INSTALL_RUNNING:
                    self._install_state = INSTALL_DONE
            elif self._install_state != INSTALL_RUNNING and self._install_state != INSTALL_FAILED:
                self._install_state = INSTALL_IDLE
            after = (
                self._env_state,
                self._install_state,
                self._et_running,
                self._et_pid,
                self._et_status_key,
                tuple(sorted(self._et_status_params.items())),
            )
            changed = before != after
        if announce:
            if installed:
                self._log_key("online_et_ready", version=EASYTIER_VERSION)
            else:
                self._log_key("online_et_not_found")
        # 只有真的变了才广播：页面存活期间的重复 refresh 不应让 QML 重算
        # installButtonKey 之类会改布局的绑定（D-150 的同族触发条件）。
        if changed:
            self._changed("env")

    def install_env(self) -> None:
        if self._closing or not self._platform_supported:
            return
        manager = self._ensure_manager()
        if manager is None:
            return
        with self._lock:
            if self._install_state == INSTALL_RUNNING:
                return
            if manager.is_installed:
                self._install_state = INSTALL_DONE
        if manager.is_installed:
            self.refresh_env()
            return
        with self._lock:
            self._install_state = INSTALL_RUNNING
            self._env_state = ENV_INSTALLING
            generation = self._generation
        self._changed("env")
        self._log_key("online_env_setup_start")

        def work() -> bool:
            return bool(
                manager.download(
                    on_progress=lambda message: self._log(f"[FMCL] {message}")
                )
            )

        def done(ok: bool) -> None:
            self._finish_install(generation, ok, "")

        def error(exc: Any) -> None:
            self._finish_install(generation, False, str(exc))

        self._spawn("install", work, done, error)

    def _finish_install(self, generation: int, ok: bool, error: str) -> None:
        if not self._guard(generation):
            return
        manager = self._manager
        installed = bool(manager.is_installed) if manager is not None else False
        stale: List[str] = []
        if ok and installed:
            if manager is not None:
                for path in list(getattr(manager, "last_stale_version_dirs", []) or []):
                    stale.append(str(path))
            with self._lock:
                self._install_state = INSTALL_DONE
                self._env_state = ENV_READY
                self._et_status_key = "online_et_stopped"
                self._et_status_params = {}
                self._stale_versions = list(stale)
            self._log_key("online_env_setup_done")
            self._status("online_env_setup_done", "success")
            self._changed("env")
            self._changed("stale")
            return
        with self._lock:
            self._install_state = INSTALL_FAILED
            self._env_state = ENV_MISSING
            self._et_status_key = "online_et_not_found"
            self._et_status_params = {}
            self._stale_versions = []
        if error:
            self._log(f"[FMCL] {error}")
        self._log_key("online_env_setup_failed")
        self._status("online_env_setup_failed", "warning")
        self._changed("env")
        self._changed("stale")

    def scan_worlds(self) -> None:
        if self._closing or not self._platform_supported:
            return
        manager = self._ensure_manager()
        if manager is None:
            return
        if not manager.is_installed:
            self._log_key("online_et_not_found")
            self._status("online_et_not_found", "warning")
            return
        with self._lock:
            if self._scanning:
                return
            self._scanning = True
            self._worlds = []
            self._scan_ports = set()
            generation = self._generation
        self._changed("worlds")
        self._log_key("online_discovering")

        try:
            listener = self._service.create_broadcast_listener(True)
        except Exception as e:  # noqa: BLE001
            logger.error("创建局域网监听器失败: %s", e)
            with self._lock:
                self._scanning = False
            self._changed("worlds")
            return

        def on_world(port: int, motd: str) -> None:
            self._on_world(generation, port, motd)

        try:
            listener.on_receive = on_world
            listener.start()
        except Exception as e:  # noqa: BLE001
            logger.error("启动局域网监听器失败: %s", e)
            with self._lock:
                self._scanning = False
            self._changed("worlds")
            return

        with self._lock:
            self._listener = listener
            self._scan_timer = self._schedule(3.5, lambda: self._finish_scan(generation), generation)

    def _on_world(self, generation: int, port: int, motd: str) -> None:
        with self._lock:
            if not self._guard(generation) or not self._scanning:
                return
            if port in self._scan_ports:
                return
            self._scan_ports.add(port)
            self._worlds.append({"port": int(port), "motd": str(motd or "")})
        self._changed("worlds")

    def _finish_scan(self, generation: int) -> None:
        with self._lock:
            if not self._guard(generation):
                return
            listener = self._listener
            self._listener = None
            self._scan_timer = None
            self._scanning = False
        if listener is not None:
            try:
                listener.stop()
            except Exception as e:  # noqa: BLE001
                logger.debug("停止局域网监听器失败: %s", e)
        self._changed("worlds")


    # ─── 大厅：创建 / 加入 ──────────────────────────────────

    def _set_lobby_state(self, new_state: LobbyState) -> None:
        with self._lock:
            old = self._lobby_state
            self._lobby_state = self._service.lobby_state_transition(old, new_state)
        self._changed("lobby")

    def _set_busy(self, value: str) -> None:
        with self._lock:
            self._busy = str(value or "")
        self._changed("lobby")

    def _set_lobby_status(self, key: str, level: str = "info", **params: Any) -> None:
        with self._lock:
            self._lobby_status_key = str(key or "")
            self._lobby_status_params = dict(params)
            self._lobby_status_level = str(level or "info")
        self._changed("lobby")

    def create_lobby(self, port_text: str) -> None:
        if self._closing or not self._platform_supported:
            return
        manager = self._ensure_manager()
        login_error = self.login_error_key()
        if login_error:
            self._log_key(login_error)
            self._status(login_error, "warning")
            return
        if manager is None or not manager.is_installed:
            self._log_key("online_et_not_found")
            self._status("online_et_not_found", "warning")
            return
        if manager.is_running:
            self._log_key("online_et_already_running")
            self._status("online_et_already_running", "warning")
            return
        mc_port, port_str = self._service.parse_mc_port(str(port_text or ""))
        if mc_port is None:
            self._log_key("online_invalid_port", port=port_str)
            self._status("online_invalid_port", "error", port=port_str)
            return
        with self._lock:
            if self._busy or self._lobby is not None:
                return
            self._busy = "create"
            self._stopping = False
            generation = self._generation
        self._set_lobby_state(LobbyState.CREATING)
        self._set_busy("create")
        self._log_key("online_creating_lobby")

        def work() -> Tuple[LobbyInfo, int, int]:
            lobby = self._service.generate_lobby()
            lobby.minecraft_port = mc_port
            lobby.is_host = True
            launched = manager.launch(
                lobby,
                as_host=True,
                on_output=lambda line: self._log(line),
                on_exited=lambda code: self._on_et_exited(int(code)),
                player_name=self._display_name(),
            )
            return lobby, mc_port, int(launched)

        def done(result: Tuple[LobbyInfo, int, int]) -> None:
            lobby, port, launched = result
            self._finish_create(generation, lobby, port, launched)

        def error(exc: Any) -> None:
            self._fail_lobby(
                generation,
                "online_create_failed",
                exc,
                notice_key="notify_room_create_failed",
            )

        self._spawn("create", work, done, error)

    def _finish_create(self, generation: int, lobby: LobbyInfo, mc_port: int, launched: int) -> None:
        if not self._guard(generation):
            return
        if launched != 0:
            self._log_key("online_launch_failed")
            self._status("online_launch_failed", "error")
            self._reset_lobby()
            return

        manager = self._manager
        scf = manager.scaffolding_server if manager is not None else None
        if scf is not None:
            scf.on_profiles_changed = lambda profiles: self._on_members_changed(generation, profiles)
        watcher = None
        try:
            watcher = self._service.create_game_watcher(int(mc_port or 0))
            watcher.on_game_stopped = lambda: self._on_host_game_stopped(generation)
            watcher.start()
        except Exception as e:  # noqa: BLE001 - 看门狗起不来不影响开大厅
            logger.warning("创建主机看门狗失败: %s", e)
            watcher = None

        with self._lock:
            self._game_watcher = watcher
            self._stopping = False
            self._lobby = lobby
            self._is_host = True
            self._local_mc_port = int(mc_port or 0)
            self._lobby_state = LobbyState.CONNECTED
            self._busy = ""
            self._et_running = True
            self._et_pid = int(getattr(manager, "pid", 0) or 0)
            self._et_status_key = "online_et_connecting"
            self._et_status_params = {}
            self._lobby_status_key = "online_waiting_network"
            self._lobby_status_params = {}
            self._lobby_status_level = "warning"
        self._log_key("online_lobby_created", code=lobby.full_code)
        self._log_key("online_waiting_network")
        self._status("online_lobby_created", "success", code=lobby.full_code)
        self._trigger_achievement("online_first_online")
        self._trigger_achievement("online_room_owner")
        self._notice("notify_room_created", "success", lobby.full_code)
        self._changed("lobby")
        self._start_member_poll(generation)
        with self._lock:
            self._ready_timer = self._schedule(
                5.0, lambda: self._host_network_ready(generation), generation
            )

    def join_lobby(self, code_text: str) -> None:
        if self._closing or not self._platform_supported:
            return
        manager = self._ensure_manager()
        login_error = self.login_error_key()
        if login_error:
            self._log_key(login_error)
            self._status(login_error, "warning")
            return
        if manager is None or not manager.is_installed:
            self._log_key("online_et_not_found")
            self._status("online_et_not_found", "warning")
            return
        if manager.is_running:
            self._log_key("online_et_already_running")
            self._status("online_et_already_running", "warning")
            return
        code = str(code_text or "").strip()
        if not code:
            self._log_key("online_empty_code")
            self._status("online_empty_code", "warning")
            return
        lobby = self._service.parse_lobby_code(code)
        if lobby is None:
            self._log_key("online_invalid_code")
            self._status("online_invalid_code", "warning")
            return
        with self._lock:
            if self._busy or self._lobby is not None:
                return
            self._busy = "join"
            self._stopping = False
            generation = self._generation
        self._set_lobby_state(LobbyState.JOINING)
        self._set_busy("join")
        self._log_key("online_joining_lobby", code=lobby.full_code)

        def work() -> Tuple[LobbyInfo, int]:
            lobby.is_host = False
            launched = manager.launch(
                lobby,
                as_host=False,
                on_output=lambda line: self._log(line),
                on_exited=lambda code_value: self._on_et_exited(int(code_value)),
            )
            return lobby, int(launched)

        def done(result: Tuple[LobbyInfo, int]) -> None:
            joined, launched = result
            self._finish_join(generation, joined, launched)

        def error(exc: Any) -> None:
            self._fail_lobby(
                generation,
                "online_join_failed",
                exc,
                notice_key="notify_room_join_failed",
            )

        self._spawn("join", work, done, error)

    def _finish_join(self, generation: int, lobby: LobbyInfo, launched: int) -> None:
        if not self._guard(generation):
            return
        if launched != 0:
            self._log_key("online_launch_failed")
            self._status("online_launch_failed", "error")
            self._reset_lobby()
            return

        manager = self._manager
        with self._lock:
            self._stopping = False
            self._lobby = lobby
            self._is_host = False
            self._local_mc_port = 0
            self._lobby_state = LobbyState.CONNECTED
            self._busy = ""
            self._et_running = True
            self._et_pid = int(getattr(manager, "pid", 0) or 0)
            self._et_status_key = "online_et_connecting"
            self._et_status_params = {}
            self._lobby_status_key = "online_waiting_network"
            self._lobby_status_params = {}
            self._lobby_status_level = "warning"
        self._log_key("online_joined_lobby", code=lobby.full_code)
        self._log_key("online_waiting_network")
        self._status("online_joined_lobby", "success", code=lobby.full_code)
        self._trigger_achievement("online_first_online")
        self._trigger_achievement("online_guest")
        self._notice("notify_room_joined", "success", lobby.full_code)
        self._changed("lobby")
        with self._lock:
            self._guest_timer = self._schedule(
                3.0, lambda: self._setup_guest(generation), generation
            )

    def _fail_lobby(self, generation: int, key: str, exc: Any, *, notice_key: str) -> None:
        if not self._guard(generation):
            return
        detail = str(exc)
        self._log_key(key, error=detail)
        self._status(key, "error", error=detail)
        self._notice(notice_key, "error", detail[:50])
        self._reset_lobby()


    # ─── 客机建连 ───────────────────────────────────────────

    def _setup_guest(self, generation: int) -> None:
        if not self._guard(generation):
            return
        with self._lock:
            if self._is_host or self._lobby is None:
                return
        manager = self._manager
        if manager is None or not manager.is_running:
            self._log_key("online_forward_failed_et")
            return
        self._log_key("online_setting_up_forward")

        def work() -> Optional[int]:
            host_info = None
            for attempt in range(1, 3):
                self._log_key("online_discovering_host", attempt=attempt)
                host_info = manager.discover_host(timeout=40.0)
                if host_info is not None:
                    break
            if host_info is None:
                return None
            host_ip, scf_port = host_info
            scf_local = manager.add_port_forward(host_ip, scf_port)
            if scf_local is None:
                return None
            client = self._service.create_scaffolding_client(
                "127.0.0.1",
                scf_local,
                self._display_name(),
                self._service.machine_id(),
                self._service.vendor(),
            )
            if not client.connect():
                return None
            client.on_player_list_changed = lambda profiles: self._on_members_changed(
                generation, profiles
            )
            client.on_server_shutdown = lambda: self._on_server_shutdown(generation)
            mc_port = client.get_server_port()
            if mc_port is None or mc_port <= 0:
                logger.warning("c:server_port returned invalid port, falling back to 25565")
                mc_port = 25565
            local = manager.add_port_forward(host_ip, mc_port)
            if local is None:
                try:
                    client.disconnect()
                except Exception:  # noqa: BLE001
                    pass
                return None
            with self._lock:
                if not self._guard(generation) or self._is_host or self._lobby is None:
                    try:
                        client.disconnect()
                    except Exception:  # noqa: BLE001
                        pass
                    return None
                self._scf_client = client
                self._local_mc_port = int(local)
            return int(local)

        def done(local: Optional[int]) -> None:
            self._finish_guest(generation, local)

        def error(exc: Any) -> None:
            if not self._guard(generation):
                return
            detail = str(exc)
            self._log_key("online_forward_failed_detail", error=detail)
            self._set_lobby_status(
                "online_forward_failed_detail", "error", error=detail
            )

        self._spawn("guest", work, done, error)

    def _finish_guest(self, generation: int, local: Optional[int]) -> None:
        if not self._guard(generation):
            return
        if local is None:
            self._log_key("online_forward_failed")
            self._set_lobby_status("online_forward_failed", "error")
            return
        manager = self._manager
        if manager is None or not manager.is_running:
            self._log_key("online_forward_failed_et")
            self._set_lobby_status("online_forward_failed_et", "error")
            return
        with self._lock:
            self._local_mc_port = int(local or 0)
        self._log_key("online_forward_ready", port=int(local))
        self._set_lobby_status("online_forward_ready", "success", port=int(local))

        tcp_port = int(self._service.get_random_port())
        forwarder = self._service.create_tcp_forwarder(tcp_port, LOOPBACK, int(local))
        forwarder.start()
        description = f"§eFMCL 大厅 - {self._display_name()}"
        broadcast = self._service.create_broadcast_simulator()
        try:
            broadcast.start(description, tcp_port)
        except Exception:
            try:
                forwarder.stop()
            except Exception:  # noqa: BLE001
                pass
            raise
        with self._lock:
            if not self._guard(generation):
                stale = True
            else:
                stale = False
                self._forwarder = forwarder
                self._broadcast = broadcast
                self._et_status_key = "online_et_running"
                self._et_status_params = {"pid": int(getattr(manager, "pid", 0) or 0)}
        if stale:
            for item in (forwarder, broadcast):
                try:
                    item.stop()
                except Exception:  # noqa: BLE001
                    pass
            return
        self._log_key("online_forward_complete", local_port=tcp_port)
        self._status("online_forward_complete", "success", local_port=tcp_port)
        self._changed("lobby")
        self._start_member_poll(generation)

    # ─── 主机就绪 / 成员 / 异常收尾 ─────────────────────────

    def _host_network_ready(self, generation: int) -> None:
        if not self._guard(generation):
            return
        manager = self._manager
        with self._lock:
            if self._is_host is False or self._lobby is None:
                return
            if manager is None or not manager.is_running:
                return
            self._et_status_key = "online_et_running"
            self._et_status_params = {"pid": int(getattr(manager, "pid", 0) or 0)}
            self._lobby_status_key = ""
            self._lobby_status_params = {}
            self._lobby_status_level = "info"
            local_port = int(self._local_mc_port or 0)
            self._ready_timer = None
        self._log_key("online_forward_complete", local_port=local_port)
        self._changed("lobby")

    def _on_members_changed(self, generation: int, profiles: Any = None) -> None:
        if not self._guard(generation):
            return
        self._refresh_members(profiles)

    def _refresh_members(self, profiles: Any = None) -> None:
        manager = self._manager
        with self._lock:
            is_host = self._is_host
            client = self._scf_client
            if self._lobby is None:
                return
        latency = 0
        if is_host:
            scf = manager.scaffolding_server if manager is not None else None
            if scf is None:
                return
            rows_source = list(scf.all_profiles)
        else:
            if client is None or not client.is_connected:
                return
            rows_source = list(client.profiles)
            latency = int(getattr(client, "latency_ms", 0) or 0)
        rows_source = self._service.sort_player_list(rows_source)
        rows: List[Dict[str, Any]] = []
        for item in rows_source:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "name": str(item.get("name", "?")),
                    "vendor": str(item.get("vendor", "?")),
                    "host": str(item.get("kind", "")) == "HOST",
                }
            )
        with self._lock:
            self._members = rows
            self._guest_latency = latency
        self._changed("members")

    def _start_member_poll(self, generation: int) -> None:
        self._stop_member_poll()
        self._refresh_members()
        with self._lock:
            self._member_timer = self._schedule(
                5.0, lambda: self._poll_members(generation), generation
            )

    def _poll_members(self, generation: int) -> None:
        if not self._guard(generation):
            return
        manager = self._manager
        with self._lock:
            is_host = self._is_host
            client = self._scf_client
            if self._lobby is None:
                self._member_timer = None
                return
        if is_host:
            if manager is None or manager.scaffolding_server is None:
                with self._lock:
                    self._member_timer = None
                return
        else:
            if client is None or not client.is_connected:
                with self._lock:
                    self._member_timer = None
                return
        self._refresh_members()
        with self._lock:
            self._member_timer = self._schedule(
                5.0, lambda: self._poll_members(generation), generation
            )

    def _stop_member_poll(self) -> None:
        with self._lock:
            timer = self._member_timer
            self._member_timer = None
        self._cancel(timer)

    def _on_server_shutdown(self, generation: int) -> None:
        if not self._guard(generation):
            return
        self._log_key("online_forward_failed_et")
        self._set_lobby_status("online_forward_failed_et", "error")
        self.leave_lobby()

    def _on_host_game_stopped(self, generation: int) -> None:
        if not self._guard(generation):
            return
        with self._lock:
            if not self._is_host:
                return
        self._log_key("online_left_lobby")
        self.leave_lobby()

    def _on_et_exited(self, exit_code: int) -> None:
        if self._closing:
            return
        with self._lock:
            stopping = self._stopping
            self._stopping = False
            self._et_running = False
            self._et_pid = 0
            self._et_status_key = "online_et_stopped"
            self._et_status_params = {}
        if stopping:
            self._changed("env")
            return
        self._log_key("online_et_exited", exit_code=int(exit_code))
        self._stop_objects()
        self._reset_lobby()
        self._status("online_et_exited", "warning", exit_code=int(exit_code))
        self._changed("env")

    # ─── 退出大厅 / 清理 / 收尾 ──────────────────────────────

    def leave_lobby(self) -> None:
        if self._closing:
            return
        self._invalidate()
        with self._lock:
            ready_timer = self._ready_timer
            guest_timer = self._guest_timer
            self._ready_timer = None
            self._guest_timer = None
        self._cancel(ready_timer)
        self._cancel(guest_timer)
        self._stop_member_poll()
        self._log_key("online_leaving_lobby")
        self._stop_objects()
        manager = self._manager
        if manager is not None and manager.is_running:
            self._stopping = True
            try:
                manager.stop_async()
            except Exception as e:  # noqa: BLE001
                logger.warning("异步停止 EasyTier 失败: %s", e)
                self._stopping = False
        self._reset_lobby()
        with self._lock:
            self._et_running = False
            self._et_pid = 0
            self._et_status_key = "online_et_stopped"
            self._et_status_params = {}
        self._log_key("online_left_lobby")
        self._status("online_left_lobby", "info")
        self._changed("env")

    def _reset_lobby(self) -> None:
        self._invalidate()
        with self._lock:
            timers = [self._ready_timer, self._guest_timer, self._member_timer]
            self._ready_timer = None
            self._guest_timer = None
            self._member_timer = None
        for timer in timers:
            self._cancel(timer)
        self._stop_objects()
        with self._lock:
            self._lobby = None
            self._is_host = False
            self._local_mc_port = 0
            self._members = []
            self._guest_latency = 0
            self._busy = ""
            self._lobby_status_key = ""
            self._lobby_status_params = {}
            self._lobby_status_level = "info"
            self._lobby_state = self._service.lobby_state_transition(
                self._lobby_state, LobbyState.LEAVING
            )
            self._lobby_state = self._service.lobby_state_transition(
                self._lobby_state, LobbyState.IDLE
            )
        self._changed("lobby")
        self._changed("members")

    def _stop_objects(self) -> None:
        with self._lock:
            broadcast = self._broadcast
            forwarder = self._forwarder
            client = self._scf_client
            watcher = self._game_watcher
            self._broadcast = None
            self._forwarder = None
            self._scf_client = None
            self._game_watcher = None
        if broadcast is not None:
            try:
                broadcast.stop()
            except Exception as e:  # noqa: BLE001
                logger.debug("停止局域网广播失败: %s", e)
        if forwarder is not None:
            try:
                forwarder.stop()
            except Exception as e:  # noqa: BLE001
                logger.debug("停止 TCP 转发失败: %s", e)
        if client is not None:
            try:
                client.on_player_list_changed = None
                client.on_server_shutdown = None
                client.on_heartbeat = None
                client.disconnect()
            except Exception as e:  # noqa: BLE001
                logger.debug("断开陶瓦客户端失败: %s", e)
        if watcher is not None:
            try:
                watcher.stop()
            except Exception as e:  # noqa: BLE001
                logger.debug("停止 GameWatcher 失败: %s", e)

    def cleanup_stale_versions(self) -> None:
        with self._lock:
            paths = list(self._stale_versions)
            self._stale_versions = []
        manager = self._manager
        if paths and manager is not None:
            targets = [Path(item) for item in paths]
            try:
                manager.remove_stale_versions(targets)
            except Exception as e:  # noqa: BLE001 - 清理失败只影响这几个目录
                logger.warning("删除 EasyTier 旧版本目录失败: %s", e)
            failures = {
                str(path): reason
                for path, reason in (getattr(manager, "last_remove_failures", []) or [])
            }
            for item in paths:
                path = Path(item)
                reason = failures.get(str(path))
                if reason:
                    self._log_key("rm_delete_failed", error=f"{path.name}: {reason}")
                else:
                    self._log_key("rm_deleted", name=path.name)
        self._changed("stale")

    def dismiss_stale_versions(self) -> None:
        with self._lock:
            self._stale_versions = []
        self._changed("stale")

    def detach(self) -> None:
        if self._closing:
            return
        with self._lock:
            self._closing = True
            self._generation += 1
        timers = []
        with self._lock:
            timers = [self._scan_timer, self._ready_timer, self._guest_timer, self._member_timer]
            self._scan_timer = None
            self._ready_timer = None
            self._guest_timer = None
            self._member_timer = None
        for timer in timers:
            self._cancel(timer)
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                listener.stop()
            except Exception as e:  # noqa: BLE001
                logger.debug("退出时停止局域网监听器失败: %s", e)
        self._stop_objects()
        manager = self._manager
        if manager is not None:
            self._stopping = True
            try:
                manager.stop()
            except Exception as e:  # noqa: BLE001
                logger.warning("退出时停止 EasyTier 失败: %s", e)
        self.set_observer(None)

    def rebind(
        self,
        *,
        launcher: Any = None,
        achievement: Any = None,
        callbacks: Optional[Dict[str, Callable[..., Any]]] = None,
    ) -> None:
        merged = launcher_callbacks(launcher)
        if callbacks:
            merged.update(callbacks)
        with self._lock:
            if launcher is not None:
                self._launcher = launcher
            if achievement is not None:
                self._achievement = achievement
            if merged:
                self._callbacks.update(merged)


__all__ = [
    "CALLBACK_NAMES",
    "ENV_CHECKING",
    "ENV_FAILED",
    "ENV_INSTALLING",
    "ENV_MISSING",
    "ENV_READY",
    "INSTALL_DONE",
    "INSTALL_FAILED",
    "INSTALL_IDLE",
    "INSTALL_RUNNING",
    "LOG_BUFFER_MAX_LINES",
    "OnlineSession",
    "launcher_callbacks",
]
