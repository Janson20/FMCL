"""全局热键桥（阶段 2.6）—— 把现有非 Qt 全局热键实现接到 Qt 信号上。

QML 侧通过上下文属性 ``Hotkeys`` 使用（注册名由阶段 2 契约第四节冻结）::

    Connections { target: Hotkeys; function onTriggered(name) { ... } }
    Hotkeys.register("music_next", "ctrl+shift+right", "下一首")

## 技术决策（沿用阶段 0，本任务不改）

全局热键**保留现有非 Qt 方案**（``keyboard`` 库），回调必须经信号回到主线程，
**不得改用 ``QShortcut``**。依据 ``docs/refactor/08-phase0-execution-log.md`` 第七节：

- 全局热键是**进程级**的（游戏运行时也生效），这是产品卖点；
- ``QShortcut`` 只在应用有焦点时生效，**无法平替**；
- 阶段 0 的 POC（``poc/hotkey_thread_test.py``，6/6 通过）实测了
  ``keyboard 回调 → Signal.emit → 主线程槽`` 这条链路成立，本桥就是它的生产化版本。

## 现有 8 组热键清单（阶段 3 要用；机器可读版本见 ``LEGACY_HOTKEYS``）

注册入口只有两个模块：``services/monitor_service.py``（1 组）与
``services/music_hotkeys.py``（7 组）；真正调后端的是 UI 侧的两个方法。

+----+------------------+---------------------+---------------------------+--------------------------------+
| #  | 动作              | 组合键               | 注册模块                   | 回调做了什么                     |
+====+==================+=====================+===========================+================================+
| 1  | 性能监控开关      | ctrl+shift+m        | services/monitor_service   | ui/app_monitor.py              |
|    |                  |                     | MONITOR_HOTKEY:37          | _toggle_monitor → after(0,     |
|    |                  |                     | HotkeyManager:484          | _toggle_monitor_ui) 显示/隐藏   |
+----+------------------+---------------------+---------------------------+--------------------------------+
| 2  | 播放/暂停         | ctrl+shift+space    | services/music_hotkeys.py | _music_hotkey_play_pause →     |
|    |                  |                     | DEFAULT_HOTKEYS:32        | after(0, _music_toggle_play)   |
+----+------------------+---------------------+---------------------------+--------------------------------+
| 3  | 上一首            | ctrl+shift+left     | 同上                      | → after(0, _music_prev)        |
+----+------------------+---------------------+---------------------------+--------------------------------+
| 4  | 下一首            | ctrl+shift+right    | 同上                      | → after(0, _music_next)        |
+----+------------------+---------------------+---------------------------+--------------------------------+
| 5  | 停止              | ctrl+shift+down     | 同上                      | → after(0, _music_stop)        |
+----+------------------+---------------------+---------------------------+--------------------------------+
| 6  | 音量 +5           | ctrl+shift+up       | 同上                      | → after(0, _adjust_volume(+5)) |
+----+------------------+---------------------+---------------------------+--------------------------------+
| 7  | 音量 -5           | ctrl+shift+page down| 同上                      | → after(0, _adjust_volume(-5)) |
+----+------------------+---------------------+---------------------------+--------------------------------+
| 8  | 静音切换          | ctrl+shift+m        | 同上                      | → after(0, _music_toggle_mute) |
+----+------------------+---------------------+---------------------------+--------------------------------+

第 2~8 组由 ``ui/app_music.py:_register_hotkeys``（:3171）在**后台线程**里按
``music_hotkeys.register_all`` 注册，注销走同一份 ``HOTKEY_ACTIONS`` 顺序；
第 1 组由 ``ui/app_monitor.py:_register_monitor_hotkeys``（:411）调
``HotkeyManager.register(callback)``，它内部同样起线程并做预热 ``hook()`` + ``sleep(0.1)``。

**已知冲突（本轮发现，未修）**：第 1 组与第 8 组是**同一个组合键** ``ctrl+shift+m``
（监控开关 vs 静音切换）。两处各自独立注册，``keyboard`` 库会让两个回调都触发。
本桥只在注册时对这种"组合键撞车"记 warning，**不做拦截** —— 拦截会改变现有产品
行为（先注册的一方把后注册的挤掉），那属于阶段 3 的决策。

## 可注入后端

- :class:`HotkeyBackend` —— 协议（``available`` / ``add_hotkey`` / ``remove_hotkey``）。
- :class:`NullHotkeyBackend` —— 什么都不碰的空实现：无桌面环境的测试与降级装配用。
- :class:`KeyboardHotkeyBackend` —— ``keyboard`` 库薄封装（真后端，懒导入）。

真后端在无桌面/无权限时**优雅降级并 warning**：``keyboard`` 导入失败 → ``available``
为 False，``add_hotkey`` 抛 :class:`HotkeyBackendUnavailable`，桥接住它、记 warning、
发 ``registrationFailed``，**不抛异常打断启动**。

## 跨线程

``keyboard`` 的回调跑在**热键库自己的线程**上。桥的回调只做一件事：``triggered.emit(name)``。
Qt 的 ``AutoConnection`` 在跨线程时自动走 Queued，把信号投给主线程的接收者（QML
``Connections`` / 主线程 QObject）。因此桥**必须建在 Qt 主线程**，构造时会检查并
``logger.error``。桥的回调里**不碰任何 QObject 属性、不碰 QML 引擎**（契约红线 3）。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, NamedTuple, Optional, Protocol, Tuple, runtime_checkable

from PySide6.QtCore import Property, QCoreApplication, QObject, Signal, Slot

logger = logging.getLogger(__name__)

#: 契约第四节冻结的 QML 注册名（由 2.2 入口 ``setContextProperty`` 注册）。
QML_NAME = "Hotkeys"

#: 现有 8 组热键的机器可读清单（组合键 + 归属模块 + 回调语义），阶段 3 迁移时按它接线。
#: 键的含义：name/combo/owner/callback/callback_module。
LEGACY_HOTKEYS: Tuple[Dict[str, str], ...] = (
    {
        "name": "monitor_toggle",
        "combo": "ctrl+shift+m",
        "owner": "services/monitor_service.py:MONITOR_HOTKEY + HotkeyManager",
        "callback": "显示/隐藏性能监控悬浮窗",
        "callback_module": "ui/app_monitor.py:_toggle_monitor",
    },
    {
        "name": "play_pause",
        "combo": "ctrl+shift+space",
        "owner": "services/music_hotkeys.py:DEFAULT_HOTKEYS",
        "callback": "播放/暂停切换",
        "callback_module": "ui/app_music.py:_music_hotkey_play_pause",
    },
    {
        "name": "prev",
        "combo": "ctrl+shift+left",
        "owner": "services/music_hotkeys.py:DEFAULT_HOTKEYS",
        "callback": "上一首",
        "callback_module": "ui/app_music.py:_music_hotkey_prev",
    },
    {
        "name": "next",
        "combo": "ctrl+shift+right",
        "owner": "services/music_hotkeys.py:DEFAULT_HOTKEYS",
        "callback": "下一首",
        "callback_module": "ui/app_music.py:_music_hotkey_next",
    },
    {
        "name": "stop",
        "combo": "ctrl+shift+down",
        "owner": "services/music_hotkeys.py:DEFAULT_HOTKEYS",
        "callback": "停止播放",
        "callback_module": "ui/app_music.py:_music_hotkey_stop",
    },
    {
        "name": "vol_up",
        "combo": "ctrl+shift+up",
        "owner": "services/music_hotkeys.py:DEFAULT_HOTKEYS",
        "callback": "音量 +5（VOLUME_HOTKEY_STEP）",
        "callback_module": "ui/app_music.py:_music_hotkey_vol_up",
    },
    {
        "name": "vol_down",
        "combo": "ctrl+shift+page down",
        "owner": "services/music_hotkeys.py:DEFAULT_HOTKEYS",
        "callback": "音量 -5（VOLUME_HOTKEY_STEP）",
        "callback_module": "ui/app_music.py:_music_hotkey_vol_down",
    },
    {
        "name": "vol_mute",
        "combo": "ctrl+shift+m",
        "owner": "services/music_hotkeys.py:DEFAULT_HOTKEYS",
        "callback": "静音切换",
        "callback_module": "ui/app_music.py:_music_hotkey_vol_mute",
    },
)


class HotkeyBackendUnavailable(RuntimeError):
    """后端不可用（``keyboard`` 缺失、无桌面、无权限）。桥会接住它并降级。"""


@runtime_checkable
class HotkeyBackend(Protocol):
    """热键后端协议 —— 桥只依赖这三个成员。

    ``add_hotkey`` 返回的句柄是**不透明**的，桥原样存下来再交给 ``remove_hotkey``：
    这样既容得下 ``keyboard`` 库的 ``add_hotkey`` 返回值，也容得下测试替身的自造句柄。
    后端**可以**额外实现 ``release_warmup()``（拆预热钩子），桥用 ``getattr`` 探测，
    有就调、没有就跳过。
    """

    @property
    def available(self) -> bool:
        """后端是否真的能注册系统级热键。"""
        ...

    def add_hotkey(self, combo: str, callback: Callable[[], None]) -> Any: ...

    def remove_hotkey(self, handle: Any) -> None: ...


class NullHotkeyBackend:
    """什么都不碰的空后端：不 import ``keyboard``、不注册系统级热键。

    **语义（照着 ``app.ports.NullUIPort`` 的约定，但有一处刻意的不同）**：
    ``available`` 为 ``False``（它确实没接真实系统），但 ``add_hotkey`` **不抛异常**，
    照样返回一个记账用的句柄，只是永远不会触发回调。这样"无桌面环境/测试里的桥"
    与"真机上的桥"走的是**同一条记账路径**，差别只体现在 ``backendAvailable`` 上；
    否则桥的簿记逻辑在无桌面环境下根本没被覆盖过。
    """

    def __init__(self) -> None:
        #: 调用流水（``("add", combo)`` / ``("remove", handle)``），供测试断言。
        self.calls: List[Tuple[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return False

    def add_hotkey(self, combo: str, callback: Callable[[], None]) -> Any:
        handle = ("null", combo, callback)
        with self._lock:
            self.calls.append(("add", combo))
        logger.debug("NullHotkeyBackend 记账注册 %s（不会真的生效）", combo)
        return handle

    def remove_hotkey(self, handle: Any) -> None:
        with self._lock:
            self.calls.append(("remove", handle))
        logger.debug("NullHotkeyBackend 记账注销 %r", handle)


class KeyboardHotkeyBackend:
    """现有全局热键实现（``keyboard`` 库）的薄封装 —— **真后端**。

    与 ``services/monitor_service.HotkeyManager`` / ``ui/app_music._register_hotkeys``
    用的是同一个库、同一套调用序列，只是把"回调怎么回主线程"交给桥：

    - **懒导入** ``keyboard``：导入失败（未安装/受限环境）→ ``available`` 为 False，
      ``add_hotkey`` 抛 :class:`HotkeyBackendUnavailable`，绝不在这里抛 ImportError；
    - **预热**：首次注册前 ``hook(lambda e: None)`` + ``sleep(0.1)``，逐字保留阶段 1 的
      hack（阶段 0 第 7.3 节记录了"是否仍必要尚未定论"，迁移期先保留）；
    - ``add_hotkey`` / ``remove_hotkey`` 直接透传，句柄原样返回。

    注意预热会**阻塞约 0.1 秒**。旧实现在后台线程里注册以避开这次阻塞；桥的
    ``register`` 是同步的，启动路径若在意这 0.1 秒，可以在 work 线程里先调一次
    :meth:`prewarm`（QML 设置页里点一次"应用"的 0.1 秒可以接受）。
    """

    def __init__(self, module: Any = None) -> None:
        #: 注入的 ``keyboard`` 模块（测试用替身）；``None`` 表示真导入。
        self._module = module
        self._import_failed = False
        self._warmup_hook: Any = None
        self._warmed = False
        self._lock = threading.Lock()

    def _module_or_none(self) -> Any:
        if self._module is not None:
            return self._module
        if self._import_failed:
            return None
        try:
            import keyboard  # 延迟导入：只有真要注册热键时才拉起钩子库
        except Exception as e:  # noqa: BLE001 - 任何导入期异常都算不可用
            self._import_failed = True
            logger.warning("keyboard 库不可用，全局热键功能降级: %s", e)
            return None
        self._module = keyboard
        return self._module

    @property
    def available(self) -> bool:
        return self._module_or_none() is not None

    def prewarm(self) -> bool:
        """预热钩子（幂等）。返回是否可用（不可用时 False，不抛）。"""
        module = self._module_or_none()
        if module is None:
            return False
        with self._lock:
            if self._warmed:
                return True
            try:
                self._warmup_hook = module.hook(lambda e: None)
                time.sleep(0.1)
            except Exception as e:  # noqa: BLE001 - 预热失败仍继续尝试注册
                logger.warning("全局热键预热失败（继续尝试注册）: %s", e)
            self._warmed = True
        return True

    def add_hotkey(self, combo: str, callback: Callable[[], None]) -> Any:
        module = self._module_or_none()
        if module is None:
            raise HotkeyBackendUnavailable("keyboard 库不可用（未安装或导入失败）")
        self.prewarm()
        return module.add_hotkey(combo, callback)

    def remove_hotkey(self, handle: Any) -> None:
        module = self._module_or_none()
        if module is None:
            return
        module.remove_hotkey(handle)

    def release_warmup(self) -> None:
        """拆掉预热钩子（原实现在整批注销时调 ``self._warmup_hook()``）。"""
        with self._lock:
            hook = self._warmup_hook
            self._warmup_hook = None
        if hook is None:
            return
        try:
            hook()
        except Exception as e:  # noqa: BLE001
            logger.debug("拆预热钩子失败: %s", e)


class HotkeyEntry(NamedTuple):
    """一条已注册热键的簿记。``handle`` 是后端返回的不透明句柄。"""

    name: str
    combo: str
    description: str
    handle: Any


class HotkeyBridge(QObject):
    """全局热键注册表：``register`` / ``unregister`` / ``unregister_all`` + ``triggered``。

    只有三段逻辑：参数转换、调后端、发信号。没有任何业务规则。

    生命周期是**显式**的：退出路径必须调 :meth:`unregister_all`（拔掉系统级热键 +
    拆预热钩子）。这里刻意**不**接 ``QObject.destroyed`` 做自动注销 —— PySide6 6.7.3 下
    "接收者是发送者自己"的连接收不到该信号（三条实测结论见
    ``app/bridges/event_bridge.py`` 的类文档）。漏掉不致命：热键库线程上的迟到回调
    由 :meth:`_on_hotkey` 的 ``RuntimeError`` 分支兜住，不会把回调线程崩掉。
    """

    #: 后端回调（热键库线程）→ 主线程接收者：跨线程自动 Queued。
    triggered = Signal(str)
    #: ``activeKeys`` 变化。
    activeKeysChanged = Signal()
    #: 注册失败（同名重复 / 组合键为空 / 后端不可用或抛异常）：(name, 原因)。
    registrationFailed = Signal(str, str)

    def __init__(self, backend: Optional[HotkeyBackend] = None, parent: Optional[QObject] = None) -> None:
        """
        Args:
            backend: 热键后端；``None`` 时用真后端 :class:`KeyboardHotkeyBackend`。
                测试/无桌面环境传 :class:`NullHotkeyBackend` 或自造替身。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._backend: Any = backend if backend is not None else KeyboardHotkeyBackend()
        self._lock = threading.RLock()
        self._active: Dict[str, HotkeyEntry] = {}
        self._check_owner_thread()
        if not self._backend_available():
            logger.warning(
                "热键后端 %s 报告不可用：注册会被记账但**不会真的生效**（无桌面/无权限环境属正常降级）",
                type(self._backend).__name__,
            )

    # ─── 线程前提 ───────────────────────────────────────────

    def _check_owner_thread(self) -> None:
        """检查"桥建在主线程"这个 Queued 投递的前提；不满足时报错但不抛。"""
        if QCoreApplication.instance() is None:
            logger.error(
                "HotkeyBridge 在**没有 QCoreApplication** 的进程里创建："
                "热键回调 emit 的信号会排进一个不存在的事件循环，永远不会投递"
            )
            return
        if threading.current_thread() is not threading.main_thread():
            logger.error(
                "HotkeyBridge 建在非主线程（%s）：接收者必须属于主线程且有事件循环",
                threading.current_thread().name,
            )

    # ─── 属性 ───────────────────────────────────────────────

    @Property("QVariantList", notify=activeKeysChanged)
    def activeKeys(self) -> List[str]:
        """已注册的热键名（**按注册顺序**，便于界面对照用户操作次序）。"""
        with self._lock:
            return list(self._active)

    @Property(bool)
    def backendAvailable(self) -> bool:
        """后端能否真的注册系统级热键。

        无 NOTIFY：真后端的可用性在进程内不会翻转（懒导入只发生一次）。
        QML 侧在热键面板创建时读一次，用它决定是否提示"当前环境不支持全局热键"。
        """
        return self._backend_available()

    @Slot(result="QVariantList")
    def activeHotkeys(self) -> List[Dict[str, str]]:
        """已注册热键的详情（``name`` / ``combo`` / ``description``），供设置页显示。"""
        with self._lock:
            return [
                {"name": e.name, "combo": e.combo, "description": e.description}
                for e in self._active.values()
            ]

    def _backend_available(self) -> bool:
        try:
            return bool(self._backend.available)
        except Exception as e:  # noqa: BLE001 - 探测可用性本身不允许打断启动
            logger.warning("探测热键后端可用性失败，按不可用处理: %s", e)
            return False

    # ─── 注册 / 注销 ────────────────────────────────────────

    #: 两个 ``@Slot`` 叠加：QML 侧 ``register(name, combo)`` 与
    #: ``register(name, combo, description)`` 都能调（PySide6 6.7.3 无 ``Q_INVOKABLE``，
    #: 让方法可被 QML 调用只能靠 ``@Slot``；两种签名都实测过）。
    @Slot(str, str, result=bool)
    @Slot(str, str, str, result=bool)
    def register(self, name: str, combo: str, description: str = "") -> bool:
        """注册一个全局热键。返回是否注册成功（失败原因见 ``registrationFailed``）。

        - 同名重复注册 → 拒绝并 warning（不覆盖：覆盖会让先注册的回调静默失效）；
        - 组合键与已注册项撞车 → **允许**，只 warning（现有实现里监控与静音就撞在
          ``ctrl+shift+m``，拦截会改变产品行为）；
        - 后端抛异常 → 捕获、warning、发 ``registrationFailed``，**不打断启动**。
        """
        key = str(name or "").strip()
        text = str(combo or "").strip()
        note = str(description or "")
        if not key or not text:
            logger.warning("热键注册被拒绝：name=%r combo=%r 都不能为空", name, combo)
            self.registrationFailed.emit(key, "名称与组合键都不能为空")
            return False
        with self._lock:
            existing = self._active.get(key)
            conflict = [other for other, entry in self._active.items() if entry.combo == text]
        if existing is not None:
            logger.warning("热键 %s 已注册（%s），重复注册被忽略", key, existing.combo)
            self.registrationFailed.emit(key, f"同名热键已注册: {existing.combo}")
            return False

        def _callback(_key: str = key) -> None:
            # 这个闭包跑在热键库的线程上；只允许 emit，不碰任何 QObject 属性。
            self._on_hotkey(_key)

        try:
            handle = self._backend.add_hotkey(text, _callback)
        except Exception as e:  # noqa: BLE001 - 后端失败一律降级，不打断启动
            logger.warning("注册全局热键 %s (%s) 失败，已降级跳过: %s", key, text, e)
            self.registrationFailed.emit(key, str(e))
            return False

        with self._lock:
            self._active[key] = HotkeyEntry(name=key, combo=text, description=note, handle=handle)
        if conflict:
            logger.warning(
                "组合键 %s 已被 %s 占用，%s 会与它同时触发（本轮不拦截，见模块文档）",
                text, ", ".join(conflict), key,
            )
        if not self._backend_available():
            logger.warning("热键 %s (%s) 已记账，但后端不可用，不会真的生效", key, text)
        self.activeKeysChanged.emit()
        return True

    @Slot(str, result=bool)
    def unregister(self, name: str) -> bool:
        """注销一个热键；返回是否真的注销了（后端异常只记 warning）。"""
        key = str(name or "").strip()
        with self._lock:
            entry = self._active.pop(key, None)
        if entry is None:
            return False
        self._release(entry)
        self.activeKeysChanged.emit()
        return True

    @Slot(result=int)
    def unregister_all(self) -> int:
        """注销全部热键，返回注销条数。**退出路径必须调它**（见类文档）。

        没有已注册项时直接返回 0，**不**去拆预热钩子 —— 逐字对齐旧实现
        （``ui/app_music.py:_unregister_hotkeys`` 在未注册时提前 return，
        ``services/monitor_service.HotkeyManager.unregister`` 同理）。
        """
        with self._lock:
            entries = list(self._active.values())
            self._active.clear()
        if not entries:
            return 0
        for entry in entries:
            self._release(entry)
        release = getattr(self._backend, "release_warmup", None)
        if callable(release):
            try:
                release()
            except Exception as e:  # noqa: BLE001
                logger.warning("拆预热钩子失败: %s", e)
        self.activeKeysChanged.emit()
        return len(entries)

    def _release(self, entry: HotkeyEntry) -> None:
        try:
            self._backend.remove_hotkey(entry.handle)
        except Exception as e:  # noqa: BLE001 - 注销失败不阻断其他注销
            logger.warning("注销全局热键 %s (%s) 失败，已忽略: %s", entry.name, entry.combo, e)

    def _on_hotkey(self, name: str) -> None:
        """热键库线程上的回调：只 emit。已注销的名字直接丢弃（迟到回调）。"""
        with self._lock:
            alive = name in self._active
        if not alive:
            logger.debug("热键 %s 已注销，忽略这次迟到的回调", name)
            return
        try:
            self.triggered.emit(name)
        except RuntimeError as e:  # 桥已销毁
            logger.debug("热键 %s 触发时桥已销毁，已丢弃: %s", name, e)


__all__ = [
    "HotkeyBackend",
    "HotkeyBackendUnavailable",
    "HotkeyBridge",
    "HotkeyEntry",
    "KeyboardHotkeyBackend",
    "LEGACY_HOTKEYS",
    "NullHotkeyBackend",
    "QML_NAME",
]
