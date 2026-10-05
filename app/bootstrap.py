"""装配层：把 ``services/`` 里的服务注册进 ``AppContext``（阶段 1 任务 1.17）。

## 为什么需要它

在写这个文件之前，`AppContext` 虽然已经完整（注册表 / 拓扑启动 / 事件总线 /
UI 端口 / 任务调度器），但**没有任何生产代码构造它** —— 也就是说：

* 界面侧的 `_get_xxx_service(owner)` 永远 `ctx is None`，于是**每个窗口各自 new 一份服务**；
* "新旧 UI 共用同一份业务逻辑"这句话在运行期并不成立（只是"共用同一份代码"）。

本模块补上这段接线，并且**用懒注册**（`AppContext.register_lazy`）避免副作用：
服务模块里有几个 import 就拉起重依赖（`onnxruntime` / `pygame` / `winsdk`），
启动时它们多数用不到；懒注册让实例在**第一次被取用**时才建出来。

## 表里为什么是"模块:类名"字符串

这样 `app/bootstrap.py` 顶层**不 import 任何服务** —— 只 import `app` 自己。
真正的导入推迟到工厂被调用的那一刻。顺带还有一个好处：
某个服务模块临时坏掉（缺三方依赖）时，只有用到它的那条路径受影响，
启动与其它服务照常（`_resolve` 里对工厂异常的处理就是为这个写的）。

## 完整性

`tests/test_app_context_wiring.py` 会扫 `services/` 下所有 `Service` 子类，
要求它们要么在这张表里、要么在 `NOT_REGISTERED` 里写明理由 ——
防止以后新增服务却忘了注册（那种缺陷没有任何运行期报错）。
"""

from __future__ import annotations

import importlib
import logging
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from app.context import AppContext

logger = logging.getLogger("app.bootstrap")

#: 服务名 → ``"模块:类名"``。名字必须与各服务类里的 ``name`` 一致
#: （`AppContext.register` 也用这个名字做键，界面侧 `try_get(Service.name)` 才对得上）。
SERVICE_FACTORIES: Tuple[Tuple[str, str], ...] = (
    ("account", "services.account_service:AccountService"),
    ("achievement", "services.achievement_service:AchievementService"),
    ("agent", "services.agent_service:AgentService"),
    ("bedrock", "services.bedrock_service:BedrockService"),
    ("crash", "services.crash_service:CrashService"),
    ("game", "services.game_service:GameService"),
    ("mod_browser", "services.mod_browser_service:ModBrowserService"),
    ("modpack", "services.modpack_service:ModpackService"),
    ("music_player", "services.music_player:MusicPlayerService"),
    ("online", "services.online_service:OnlineService"),
    ("plugin_browser", "services.plugin_browser_service:PluginBrowserService"),
    ("resource", "services.resource_service:ResourceService"),
    ("server", "services.server_service:ServerService"),
    ("tool", "services.tool_service:ToolService"),
    ("voice", "services.voice_service:VoiceService"),
)

#: 刻意**不**注册进上下文的 Service 子类，以及理由。
#: 完整性测试会对着这张表核对（既防止漏注册，也防止"注册了却没写理由"）。
NOT_REGISTERED: Dict[str, str] = {
    "ServiceNotAvailable": "services/errors.py 里的异常类型，不是服务",
    "InvalidArgument": "services/errors.py 里的异常类型，不是服务",
    "NotFound": "services/errors.py 里的异常类型，不是服务",
    "AlreadyExists": "services/errors.py 里的异常类型，不是服务",
    "PermissionDenied": "services/errors.py 里的异常类型，不是服务",
    "OperationCancelled": "services/errors.py 里的异常类型，不是服务",
    "ConflictError": "services/errors.py 里的异常类型，不是服务",
    "ExternalToolError": "services/errors.py 里的异常类型，不是服务",
    "PlatformNotSupported": "services/errors.py 里的异常类型，不是服务",
    "NetworkError": "services/errors.py 里的异常类型，不是服务",
    "Service": "services/base.py 里的基类本身",
}


def _make_factory(spec: str, name: str) -> Callable[[], Any]:
    """把 ``"模块:类名"`` 变成一个只在调用时才 import 的工厂。"""

    def factory() -> Any:
        module_name, _, class_name = spec.partition(":")
        module = importlib.import_module(module_name)
        cls = getattr(module, class_name)
        return cls()

    factory.__name__ = f"factory_{name}"
    factory.__doc__ = f"懒构造 {spec}"
    return factory


def register_default_services(
    ctx: AppContext,
    *,
    include: Optional[Iterable[str]] = None,
    exclude: Sequence[str] = (),
    replace: bool = False,
) -> List[str]:
    """把默认服务表注册进上下文（懒注册）。返回实际注册的名字。

    Args:
        ctx: 目标上下文。
        include: 只注册这些名字（None = 全部）。
        exclude: 跳过这些名字。
        replace: 允许覆盖同名注册。
    """
    wanted = set(include) if include is not None else None
    skip = set(exclude)
    registered: List[str] = []
    for name, spec in SERVICE_FACTORIES:
        if wanted is not None and name not in wanted:
            continue
        if name in skip:
            continue
        ctx.register_lazy(name, _make_factory(spec, name), replace=replace)
        registered.append(name)
    return registered


def build_context(
    config: Any = None,
    ui: Any = None,
    scheduler: Optional[Callable[[Callable[[], None]], Any]] = None,
    *,
    register: bool = True,
    tasks: Any = None,
    events: Any = None,
    set_current: bool = True,
) -> AppContext:
    """建一个装好服务表的 ``AppContext``。

    Args:
        config: 全局配置对象（None 时上下文会懒加载根模块的 ``config`` 单例）。
        ui: ``UIPort`` 实现（Tk 界面是 ``ui/ports_tk.TkUIPort``）。
        scheduler: 主线程调度器（旧界面传 ``lambda fn: app.after(0, fn)``）。
        register: 是否注册默认服务表。
        set_current: 是否把它设为进程级"当前上下文"（界面侧的取服务辅助函数会用它）。
    """
    ctx = AppContext(config=config, ui=ui, tasks=tasks, events=events)
    if scheduler is not None:
        ctx.set_scheduler(scheduler)
    if ui is not None:
        ctx.set_ui(ui)
    if register:
        register_default_services(ctx)
    if set_current:
        AppContext.set_current(ctx)
    return ctx


def attach(ctx: AppContext, owner: Any) -> AppContext:
    """把上下文挂到界面对象上（``owner.context``），并登记为进程级当前上下文。

    界面侧的 ``_get_xxx_service(owner)`` 会先看 ``owner.context``、再看
    ``AppContext.current()``，两者指向同一个上下文，于是**全进程共用同一批服务实例**。
    """
    try:
        owner.context = ctx
    except Exception as e:  # noqa: BLE001 - 少数对象是 __slots__ 或不接受属性
        logger.warning("无法把 AppContext 挂到 %r 上: %s", type(owner).__name__, e)
    AppContext.set_current(ctx)
    return ctx


__all__ = [
    "SERVICE_FACTORIES",
    "NOT_REGISTERED",
    "register_default_services",
    "build_context",
    "attach",
]
