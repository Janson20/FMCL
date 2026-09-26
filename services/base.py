"""服务基类。

每个服务是一个普通对象，遵循三条约定：

1. **构造期不做重活**：构造函数只保存配置，不做网络/磁盘/子进程调用。
   重活放在 ``start()``，由 ``AppContext.start_all()`` 在启动阶段统一调用。
2. **依赖通过 context 拿**：服务之间不互相 import 具体类，而是
   ``self.require("music")``。这样服务可被单独实例化做单元测试。
3. **零 UI 依赖**：不得 import tkinter / PySide6 / ui.*。需要与用户交互时，
   通过 ``self.context.ui``（``app.ports.UIPort``）发起。

``Service`` 只提供生命周期与依赖查找，不提供任何业务方法。
"""

from __future__ import annotations

import logging
from abc import ABC
from typing import TYPE_CHECKING, Any, ClassVar, Dict, Optional

from services.errors import ServiceNotAvailable

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查，运行期避免循环导入
    from app.context import AppContext


class Service(ABC):
    """所有服务的基类。"""

    #: 服务在 AppContext 中的注册名，子类必须覆盖且全局唯一。
    name: ClassVar[str] = ""
    #: 人类可读名称，用于日志与任务面板展示。
    label: ClassVar[str] = ""
    #: 依赖的服务名列表；``AppContext.start_all()`` 会据此排序并校验。
    requires: ClassVar[tuple[str, ...]] = ()

    def __init__(self, context: Optional["AppContext"] = None) -> None:
        self._context: Optional["AppContext"] = context
        self._started = False
        self.log: logging.Logger = logging.getLogger(
            f"services.{self.name}" if self.name else f"services.{type(self).__name__}"
        )

    # ─── 生命周期 ────────────────────────────────────────────

    def attach(self, context: "AppContext") -> None:
        """由 AppContext 注册时调用，注入上下文。"""
        self._context = context

    @property
    def context(self) -> "AppContext":
        if self._context is None:
            raise ServiceNotAvailable(
                f"服务 {self.name or type(self).__name__} 尚未绑定 AppContext",
                detail="请在 AppContext.register() 之后再使用该服务",
            )
        return self._context

    @property
    def attached(self) -> bool:
        return self._context is not None

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        """启动期初始化（读盘、建连接池、注册钩子）。必须可重入安全。"""
        self._started = True

    def stop(self) -> None:
        """退出期清理（关线程、落盘、断开连接）。不得抛异常打断退出流程。"""
        self._started = False

    # ─── 依赖查找 ────────────────────────────────────────────

    def require(self, name: str) -> Any:
        """取一个必须存在的服务，缺失即抛 ServiceNotAvailable。"""
        return self.context.require(name)

    def try_get(self, name: str) -> Optional[Any]:
        """取一个可选服务，缺失返回 None。"""
        return self.context.try_get(name)

    def has(self, name: str) -> bool:
        return self.context.has(name)

    # ─── 事件 ────────────────────────────────────────────────

    def publish(self, event: str, **payload: Any) -> None:
        """向事件总线发一条通知。UI 层负责切回主线程。"""
        self.context.events.publish(event, source=self.name, **payload)

    def subscribe(self, event: str, handler) -> None:
        self.context.events.subscribe(event, handler)

    def unsubscribe(self, event: str, handler) -> None:
        self.context.events.unsubscribe(event, handler)

    # ─── 便捷访问 ────────────────────────────────────────────

    @property
    def ui(self):
        """UI 能力端口（默认是 NullUIPort，永不抛异常）。"""
        return self.context.ui

    @property
    def tasks(self):
        """任务调度器。"""
        return self.context.tasks

    @property
    def config(self):
        """全局配置对象（保持与旧代码同一份实例）。"""
        return self.context.config

    # ─── 诊断 ────────────────────────────────────────────────

    def describe(self) -> Dict[str, Any]:
        """供 ``--dump-services`` / 崩溃报告使用。"""
        return {
            "name": self.name,
            "label": self.label or self.name,
            "class": type(self).__name__,
            "requires": list(self.requires),
            "started": self._started,
            "attached": self._context is not None,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} name={self.name!r} started={self._started}>"


__all__ = ["Service"]
