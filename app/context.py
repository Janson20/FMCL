"""AppContext —— 服务定位器与共享运行时。

角色有三：

1. **服务的注册表**：所有 ``services/*`` 在这里按名字注册，互相通过
   ``ctx.require("music")`` 取用，而不是互相 import 具体类。
2. **共享设施的持有者**：配置对象、UI 端口、任务调度器、事件总线。
   这些是"每个进程一份"的东西，放进服务里会造成循环依赖。
3. **新旧 UI 的接缝**：``legacy_callbacks()`` 把服务方法汇总成旧 Tk 界面
   认识的 ``dict[str, Callable]``，让 1.17「只改接线、不改界面」可行。

构造顺序由 ``start_all()`` 按 ``Service.requires`` 拓扑排序决定；
存在循环依赖时直接抛错（宁可启动失败，也不要随机顺序的隐性 bug）。
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

from app.events import EventBus
from app.ports import NullUIPort, UIPort
from app.tasks import Dispatcher, TaskRunner
from services.errors import AlreadyExists, ServiceNotAvailable

if TYPE_CHECKING:  # pragma: no cover
    from services.base import Service

logger = logging.getLogger(__name__)

#: 进程级"当前上下文"。由启动路径显式设置（``AppContext.set_current``），
#: 供界面侧的 ``_get_xxx_service(owner)`` 在没有 owner 上下文时复用同一批服务实例。
#: 为什么需要它：那些辅助函数的 ``owner`` 是**窗口自己**（`_get_resource_service(self)`），
#: 而窗口并没有 ``context`` 属性 —— 没有这个兜底，注册进上下文的服务永远取不到，
#: 每个窗口都会各自 new 一个服务实例（这正是 1.17 要消除的东西）。
_CURRENT: Optional["AppContext"] = None


class AppContext:
    """服务容器。线程安全：注册/查找可在任意线程进行。"""

    def __init__(
        self,
        config: Any = None,
        ui: Optional[UIPort] = None,
        tasks: Optional[TaskRunner] = None,
        events: Optional[EventBus] = None,
        app_name: str = "FMCL",
        max_workers: int = 8,
    ) -> None:
        self.app_name = app_name
        self._config = config
        self._ui: UIPort = ui if ui is not None else NullUIPort()
        self.tasks = tasks if tasks is not None else TaskRunner(max_workers=max_workers)
        self.events = events if events is not None else EventBus()
        self.log = logging.getLogger("app.context")
        self._services: Dict[str, Any] = {}
        #: 懒注册表：``name -> 工厂``。首次 ``try_get/require`` 时才实例化，
        #: 这是**启动速度**的关键 —— 有些服务模块 import 就拉起重依赖
        #: （onnxruntime / pygame / winsdk），而启动时多数界面用不到它们。
        self._factories: Dict[str, Callable[[], Any]] = {}
        self._lock = threading.RLock()
        self._started_order: List[str] = []
        self._ui_ready = False

    # ─── 进程级当前上下文 ───────────────────────────────────

    @classmethod
    def current(cls) -> Optional["AppContext"]:
        """取进程级当前上下文（未设置时返回 None）。"""
        return _CURRENT

    @classmethod
    def set_current(cls, ctx: Optional["AppContext"]) -> None:
        """设置进程级当前上下文。由启动路径调用；测试里用完要还原。"""
        global _CURRENT
        _CURRENT = ctx

    # ─── 共享设施 ───────────────────────────────────────────

    @property
    def config(self) -> Any:
        """全局配置对象。未显式注入时懒加载根模块的 ``config`` 单例。"""
        if self._config is None:
            from config import config as root_config

            self._config = root_config
        return self._config

    @property
    def ui(self) -> UIPort:
        return self._ui

    @ui.setter
    def ui(self, port: UIPort) -> None:
        self.set_ui(port)

    def set_ui(self, port: Optional[UIPort]) -> None:
        """替换 UI 端口。界面创建完成后调用，并置 ``ui_ready``。"""
        self._ui = port if port is not None else NullUIPort()
        self._ui_ready = self._ui.is_available()
        self.log.info("UI 端口已切换为 %s", type(self._ui).__name__)

    @property
    def ui_ready(self) -> bool:
        """界面是否可用（启动早期为 False，此时服务应走静默降级路径）。"""
        return self._ui_ready and self._ui.is_available()

    def set_scheduler(self, scheduler: Optional[Dispatcher]) -> None:
        """给任务调度器注入主线程 dispatcher（由 UI 层提供）。"""
        self.tasks.set_scheduler(scheduler)

    # ─── 注册与查找 ─────────────────────────────────────────

    def register(self, service: "Service", name: Optional[str] = None, replace: bool = False) -> "Service":
        """注册一个服务实例并注入本上下文。"""
        key = name or getattr(service, "name", "") or type(service).__name__
        if not key:
            raise ValueError("服务必须有非空的 name")
        if not replace and key in self._services:
            raise AlreadyExists(f"服务 {key!r} 已注册", detail="如需覆盖请传 replace=True")
        if hasattr(service, "attach"):
            service.attach(self)
        self._services[key] = service
        return service

    def register_instance(self, name: str, obj: Any, replace: bool = False) -> Any:
        """注册非 Service 的共享对象（例如 ``Config``、``PluginManager``）。"""
        if not replace and name in self._services:
            raise AlreadyExists(f"对象 {name!r} 已注册", detail="如需覆盖请传 replace=True")
        self._services[name] = obj
        return obj

    def register_lazy(self, name: str, factory: Callable[[], Any], replace: bool = False) -> None:
        """**懒注册**：第一次取用时才调用 ``factory()`` 建实例。

        为什么需要它：13 个服务里有几个模块 import 就拉起重依赖，而启动时用不到；
        全量 eager 注册会拖慢启动。懒注册让"服务是共享的"这件事在**用到时**才生效，
        同时保留"取不到就自己 new"的旧行为（未注册的名字仍然返回 None）。

        Raises:
            AlreadyExists: 同名已被 eager 或 lazy 注册过（除非 ``replace``）。
        """
        with self._lock:
            if not replace and (name in self._services or name in self._factories):
                raise AlreadyExists(f"服务 {name!r} 已注册", detail="如需覆盖请传 replace=True")
            self._factories[name] = factory

    def _resolve(self, name: str) -> Optional[Any]:
        """按需实例化懒注册项。实例化失败时**不缓存失败结果**（下次还会重试）。"""
        with self._lock:
            if name in self._services:
                return self._services[name]
            factory = self._factories.get(name)
            if factory is None:
                return None
            try:
                obj = factory()
            except Exception as e:  # noqa: BLE001 - 取不到就当没有，界面会走自造兜底
                self.log.error("懒注册的服务 %s 实例化失败: %s", name, e, exc_info=True)
                return None
            if hasattr(obj, "attach"):
                obj.attach(self)
            self._services[name] = obj
            self._factories.pop(name, None)
            return obj

    def unregister(self, name: str) -> bool:
        removed = False
        with self._lock:
            if name in self._services:
                del self._services[name]
                removed = True
            if name in self._factories:
                del self._factories[name]
                removed = True
        if name in self._started_order:
            self._started_order.remove(name)
        return removed

    def has(self, name: str) -> bool:
        return name in self._services or name in self._factories

    def get(self, name: str, default: Any = None) -> Any:
        obj = self._resolve(name)
        return default if obj is None else obj

    def try_get(self, name: str) -> Optional[Any]:
        return self._resolve(name)

    def require(self, name: str) -> Any:
        """取一个必须存在的服务；缺失时抛 ServiceNotAvailable。"""
        obj = self._resolve(name)
        if obj is None:
            raise ServiceNotAvailable(
                f"服务 {name!r} 未注册",
                detail=f"当前已注册: {self.names()}",
            )
        return obj

    def names(self) -> List[str]:
        """已注册（含懒注册）的名字。**不触发实例化**。"""
        return sorted(set(self._services) | set(self._factories))

    def instantiated(self) -> List[str]:
        """已经真正建出实例的服务名（``find()`` 只看这些）。"""
        return sorted(self._services)

    def services(self) -> Dict[str, Any]:
        """**已实例化**的服务快照（不触发懒注册，避免一次遍历拉起全部重依赖）。"""
        return dict(self._services)

    def find(self, cls: type) -> List[Any]:
        """按类型找**已实例化**的服务（用于可选功能探测，不用于主依赖）。"""
        return [s for s in self._services.values() if isinstance(s, cls)]

    # ─── 生命周期 ───────────────────────────────────────────

    def _start_order(self) -> List[str]:
        """按 ``requires`` 拓扑排序，返回启动顺序。检测循环依赖。"""
        from services.base import Service

        pending = {
            name: [r for r in getattr(svc, "requires", ()) if r in self._services and r != name]
            for name, svc in self._services.items()
            if isinstance(svc, Service)
        }
        order: List[str] = []
        resolved: set[str] = set()
        while pending:
            ready = [n for n, deps in pending.items() if all(d in resolved or d not in pending for d in deps)]
            if not ready:
                raise ServiceNotAvailable(
                    "服务依赖存在循环",
                    detail=f"无法解析: {sorted(pending)}",
                )
            for name in sorted(ready):
                order.append(name)
                resolved.add(name)
                del pending[name]
        return order

    def start_all(self) -> List[str]:
        """按依赖顺序调用每个服务的 ``start()``，返回实际启动顺序。

        单个服务启动失败不会中断其他服务：记录日志后继续，
        失败的记录在 ``self._start_failures`` 里供启动画面提示。
        """
        self._start_failures: Dict[str, BaseException] = {}
        order = self._start_order()
        for name in order:
            svc = self._services[name]
            if getattr(svc, "started", False):
                continue
            try:
                svc.start()
                self._started_order.append(name)
            except Exception as e:  # noqa: BLE001 - 单点失败不阻断启动
                self._start_failures[name] = e
                self.log.error("服务 %s 启动失败: %s", name, e, exc_info=True)
        return list(self._started_order)

    def stop_all(self) -> None:
        """逆序调用 ``stop()``。任何服务的异常都不得打断退出流程。"""
        for name in reversed(self._started_order):
            svc = self._services.get(name)
            if svc is None or not getattr(svc, "started", False):
                continue
            try:
                svc.stop()
            except Exception as e:  # noqa: BLE001
                self.log.error("服务 %s 停止失败: %s", name, e, exc_info=True)
        self._started_order.clear()
        try:
            self.tasks.shutdown(wait=False)
        except Exception as e:  # noqa: BLE001
            self.log.error("任务调度器关闭失败: %s", e, exc_info=True)

    # ─── 旧 UI 适配（阶段 1.17） ────────────────────────────

    def legacy_callbacks(self) -> Dict[str, Callable[..., Any]]:
        """汇总所有服务的 ``legacy_callbacks()``，供旧 Tk 界面使用。

        约定：服务的 ``legacy_callbacks()`` 返回 ``{键: 可调用对象}``，
        键名必须与 ``launcher.core.get_callbacks()`` 的键名一致。
        重复键会记警告并以后注册者为准——重复通常意味着两个服务都在
        声称拥有同一个回调，是需要修的设计问题。

        **只看已实例化的服务**：遍历时顺手把懒注册项全拉起来会拖慢启动，
        而这里的结果只在旧界面装配回调时用一次。
        """
        merged: Dict[str, Callable[..., Any]] = {}
        for name in self.instantiated():
            svc = self._services[name]
            provider = getattr(svc, "legacy_callbacks", None)
            if not callable(provider):
                continue
            try:
                part = provider()
            except Exception as e:  # noqa: BLE001
                self.log.error("服务 %s 汇总回调失败: %s", name, e, exc_info=True)
                continue
            if not isinstance(part, dict):
                continue
            for key, value in part.items():
                if key in merged:
                    self.log.warning("回调键 %r 被服务 %s 重复提供，后者覆盖前者", key, name)
                merged[key] = value
        return merged

    # ─── 诊断 ───────────────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        return {
            "app_name": self.app_name,
            "ui": type(self._ui).__name__,
            "ui_ready": self._ui_ready,
            "tasks": {
                "max_workers": self.tasks.max_workers,
                "active": self.tasks.active_count(),
                "has_scheduler": self.tasks.has_scheduler,
            },
            "events": self.events.events(),
            "services": [s.describe() if hasattr(s, "describe") else {"name": name, "class": type(s).__name__}
                         for name, s in sorted(self._services.items())],
            "lazy_services": sorted(self._factories),
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AppContext services={len(self._services)} ui={type(self._ui).__name__}>"


def current_context() -> Optional["AppContext"]:
    """``AppContext.current()`` 的**模块级别名**。

    界面侧的取服务辅助函数要写成一行：
    ``ctx = getattr(owner, "context", None) or current_context()``
    —— 用模块级函数比 `from app.context import AppContext` 再 `AppContext.current()`
    更短，也避免了到处出现这个类名。
    """
    return _CURRENT


__all__ = ["AppContext", "current_context"]
