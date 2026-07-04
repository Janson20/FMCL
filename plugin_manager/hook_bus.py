"""钩子总线 - 线程安全的插件钩子注册/注销/触发机制

支持:
    - 多插件同时注册同一钩子点
    - 钩子处理器优先级排序
    - 钩子返回值聚合（列表 / 首个非 None / 短路）
    - 线程安全（可重入锁）
"""

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Any, Optional

from logzero import logger

from plugin_manager.base import HookPoint


class HookStrategy(str, Enum):
    """钩子触发策略"""
    ALL = "all"                  # 按优先级依次调用所有处理器，不关心返回值
    COLLECT = "collect"          # 按优先级依次调用，收集所有返回值
    FIRST = "first"              # 按优先级依次调用，返回第一个非 None 值
    SHORT_CIRCUIT = "short_circuit"  # 按优先级依次调用，某个处理器返回 True 则停止


# 每个钩子点的默认策略
_HOOK_DEFAULT_STRATEGY: Dict[HookPoint, HookStrategy] = {
    # 应用生命周期
    HookPoint.APP_STARTUP: HookStrategy.ALL,
    HookPoint.APP_SHUTDOWN: HookStrategy.ALL,
    # 游戏生命周期
    HookPoint.GAME_PRE_LAUNCH: HookStrategy.COLLECT,    # 收集多个插件的启动参数修改
    HookPoint.GAME_POST_LAUNCH: HookStrategy.ALL,
    HookPoint.GAME_STOPPED: HookStrategy.ALL,
    HookPoint.GAME_CRASHED: HookStrategy.COLLECT,       # 收集多个插件的崩溃分析结果
    # 版本生命周期
    HookPoint.VERSION_PRE_INSTALL: HookStrategy.FIRST,  # 首个插件可阻止安装
    HookPoint.VERSION_POST_INSTALL: HookStrategy.ALL,
    HookPoint.VERSION_PRE_REMOVE: HookStrategy.FIRST,
    # 服务器生命周期
    HookPoint.SERVER_PRE_START: HookStrategy.FIRST,
    HookPoint.SERVER_POST_START: HookStrategy.ALL,
    HookPoint.SERVER_STOPPED: HookStrategy.ALL,
    # UI 扩展
    HookPoint.UI_TAB_REGISTER: HookStrategy.COLLECT,       # 收集所有插件的标签页
    HookPoint.UI_SIDEBAR_REGISTER: HookStrategy.COLLECT,   # 收集侧边栏项目
    HookPoint.UI_SETTINGS_REGISTER: HookStrategy.COLLECT,  # 收集设置条目
    # 下载生命周期
    HookPoint.DOWNLOAD_PRE_DOWNLOAD: HookStrategy.COLLECT,
    HookPoint.DOWNLOAD_POST_DOWNLOAD: HookStrategy.ALL,
}


@dataclass(order=True)
class HookHandler:
    """钩子处理器"""
    priority: int
    plugin_id: str = field(compare=False)
    callback: Callable = field(compare=False)
    hook_point: HookPoint = field(compare=False)


class HookBus:
    """钩子总线

    线程安全，支持优先级排序、策略控制。

    用法:
        bus = HookBus()

        # 注册
        bus.register(HookPoint.APP_STARTUP, my_callback, priority=10, plugin_id="my")

        # 触发
        bus.emit(HookPoint.APP_STARTUP, arg1=val1, arg2=val2)

        # 注销
        bus.unregister(HookPoint.APP_STARTUP, plugin_id="my")
        bus.unregister_all(plugin_id="my")  # 注销某插件的全部钩子
    """

    def __init__(self):
        self._handlers: Dict[HookPoint, List[HookHandler]] = {
            hp: [] for hp in HookPoint
        }
        self._lock = threading.RLock()

    def register(
        self,
        hook_point: HookPoint,
        callback: Callable,
        plugin_id: str,
        priority: int = 100,
    ) -> bool:
        """注册钩子处理器

        Args:
            hook_point: 钩子点
            callback: 处理器函数，接收 **kwargs
            plugin_id: 插件 ID
            priority: 优先级，数字越小越先执行，默认 100

        Returns:
            是否注册成功
        """
        handler = HookHandler(
            priority=priority,
            plugin_id=plugin_id,
            callback=callback,
            hook_point=hook_point,
        )
        with self._lock:
            # 防止重复注册
            existing = self._handlers.get(hook_point, [])
            for h in existing:
                if h.plugin_id == plugin_id and h.callback is callback:
                    logger.warning(
                        f"HookBus: 插件 '{plugin_id}' 重复注册钩子 {hook_point.value}，已忽略"
                    )
                    return False
            existing.append(handler)
            existing.sort()  # 按 priority 排序
            logger.debug(f"HookBus: [{plugin_id}] 注册钩子 {hook_point.value} (优先级 {priority})")
        return True

    def unregister(self, hook_point: HookPoint, plugin_id: str) -> int:
        """注销某插件在指定钩子点的所有处理器

        Returns:
            注销的处理器数量
        """
        count = 0
        with self._lock:
            handlers = self._handlers.get(hook_point, [])
            self._handlers[hook_point] = [
                h for h in handlers if h.plugin_id != plugin_id
            ]
            count = len(handlers) - len(self._handlers[hook_point])
        if count > 0:
            logger.debug(f"HookBus: [{plugin_id}] 注销钩子 {hook_point.value} ({count} 个)")
        return count

    def unregister_all(self, plugin_id: str) -> int:
        """注销某插件的全部钩子处理器

        Returns:
            注销的处理器总数
        """
        total = 0
        with self._lock:
            for hook_point in HookPoint:
                total += self.unregister(hook_point, plugin_id)
        return total

    def emit(self, hook_point: HookPoint, **kwargs) -> Any:
        """触发钩子

        Args:
            hook_point: 钩子点
            **kwargs: 传递给处理器的参数

        Returns:
            取决于策略:
                - ALL: None
                - COLLECT: List[Tuple[str, Any]] 所有处理器返回值，附带 plugin_id
                - FIRST: Any 首个非 None 值
                - SHORT_CIRCUIT: bool 是否有处理器返回 True
        """
        strategy = _HOOK_DEFAULT_STRATEGY.get(hook_point, HookStrategy.ALL)

        with self._lock:
            handlers = list(self._handlers.get(hook_point, []))

        if not handlers:
            if strategy == HookStrategy.COLLECT:
                return []
            if strategy == HookStrategy.FIRST:
                return None
            if strategy == HookStrategy.SHORT_CIRCUIT:
                return False
            return None

        if strategy == HookStrategy.ALL:
            self._execute_all(handlers, kwargs)
            return None

        if strategy == HookStrategy.COLLECT:
            return self._execute_collect(handlers, kwargs)

        if strategy == HookStrategy.FIRST:
            return self._execute_first(handlers, kwargs)

        if strategy == HookStrategy.SHORT_CIRCUIT:
            return self._execute_short_circuit(handlers, kwargs)

        return None

    def has_listeners(self, hook_point: HookPoint) -> bool:
        """检查钩子点是否有注册的处理器"""
        return len(self._handlers.get(hook_point, [])) > 0

    def get_listener_count(self, plugin_id: Optional[str] = None) -> int:
        """获取监听器数量"""
        if plugin_id:
            count = 0
            for handlers in self._handlers.values():
                count += sum(1 for h in handlers if h.plugin_id == plugin_id)
            return count
        return sum(len(h) for h in self._handlers.values())

    # ── 内部执行方法 ──

    def _execute_all(self, handlers: List[HookHandler], kwargs: dict):
        """依次执行所有处理器，忽略异常"""
        for handler in handlers:
            try:
                handler.callback(**kwargs)
            except Exception as e:
                logger.error(
                    f"HookBus: 插件 '{handler.plugin_id}' "
                    f"钩子 {handler.hook_point.value} 执行异常: {e}"
                )

    def _execute_collect(self, handlers: List[HookHandler], kwargs: dict) -> list:
        """依次执行，收集所有返回值（附带 plugin_id）

        Returns:
            List[Tuple[str, Any]]: [(plugin_id, result), ...]
        """
        results = []
        for handler in handlers:
            try:
                result = handler.callback(**kwargs)
                if result is not None:
                    results.append((handler.plugin_id, result))
            except Exception as e:
                logger.error(
                    f"HookBus: 插件 '{handler.plugin_id}' "
                    f"钩子 {handler.hook_point.value} 收集异常: {e}"
                )
        return results

    def _execute_first(self, handlers: List[HookHandler], kwargs: dict) -> Any:
        """依次执行，返回首个非 None 值"""
        for handler in handlers:
            try:
                result = handler.callback(**kwargs)
                if result is not None:
                    return result
            except Exception as e:
                logger.error(
                    f"HookBus: 插件 '{handler.plugin_id}' "
                    f"钩子 {handler.hook_point.value} 异常: {e}"
                )
        return None

    def _execute_short_circuit(self, handlers: List[HookHandler], kwargs: dict) -> bool:
        """依次执行，某个返回 True 则停止"""
        for handler in handlers:
            try:
                if handler.callback(**kwargs):
                    return True
            except Exception as e:
                logger.error(
                    f"HookBus: 插件 '{handler.plugin_id}' "
                    f"钩子 {handler.hook_point.value} 短路异常: {e}"
                )
        return False
