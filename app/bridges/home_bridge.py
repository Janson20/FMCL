"""首页桥 —— QML 侧的 `Home` 上下文属性（阶段 3 任务 3.1）。

## 它是什么

首页要显示的东西只有五类：**当前账号、皮肤、最近启动的版本、游戏进程状态、
成就与签到摘要**（IA 见 `docs/refactor/02-architecture-and-pages.md` 第 4.1 节）。
桥的职责是把它们从服务里取出来、变成 QML 能绑定的属性与信号，
**一句业务规则都不写**（红线 2）：皮肤尺寸校验在 `AccountService`、
退出码归因在 `GameService`、成就统计在 `AchievementService`。

## 三条设计约束

1. **状态条文案由桥翻译**：`GameService` 报的是 i18n 键 + 参数（它不持有界面文案），
   QML 又没法做 `str.format`，所以由桥用 `services.i18n_service._` 翻好，
   再发 `statusMessage(text, level)`。App.qml 收到后转手给 `Shell.setStatus`。
   键都以**字面量**形式写在本文件里（`_("game_crashed", code=…)` 那种），
   这样 `scripts/check_i18n.py` 的静态扫描看得见它们（写成变量就查不到了）。
2. **观察者回调可能在任意线程**：`GameService` 的启动/退出监控在工作线程里回调，
   所以回调里**只做两件事**：写普通 Python 属性 + `emit` 信号。
   不碰 Q_PROPERTY 的背后存储以外的东西、更不碰 QML 引擎（契约第三节硬规则 3）。
3. **桥缺席也要能用**：任一服务取不到时属性返回"空"值、槽只记日志不抛异常 ——
   首页少一块信息可以接受，整页打不开不行。

## 为什么 `refresh()` 是显式槽而不是属性依赖

账号、皮肤、最近版本、成就数这四份数据来自**四个不同的服务**，没有一个共同的
"变了"信号可订阅（账号变更由 3.5 的账号页通知、皮肤由本页自己改、最近版本由
启动成功触发、成就在解锁时变）。硬造一个聚合信号只会让"什么时候该刷新"变得
不可推理；显式 `refresh()` 配上"页面出现时刷一次 + 每次操作后刷一次"更简单，
也更好在测试里断言（`tests/test_home_bridge.py`）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from services.game_service import (
    STATE_LAUNCHING,
    STATE_RUNNING,
    STATE_WAITING,
)
from services.i18n_service import _

logger = logging.getLogger("app.bridges.home_bridge")

#: 游戏状态里"正在进行中"的那几个（决定按钮该显示"启动"还是"忙碌"）。
BUSY_STATES = (STATE_LAUNCHING, STATE_WAITING, STATE_RUNNING)


class HomeBridge(QObject):
    """上下文属性 `Home`。"""

    #: 账号信息变了（切换账号、刷新）。
    accountChanged = Signal()
    #: 第一次刷新做完了（页面据此从"加载中"切到内容/空态）。
    readyChanged = Signal()
    #: 皮肤路径变了（选择/移除）。
    skinChanged = Signal()
    #: 最近启动的版本变了。
    recentVersionChanged = Signal()
    #: 游戏状态、进程可用性变了。
    gameChanged = Signal()
    #: 成就摘要 / 签到天数变了。
    achievementsChanged = Signal()
    #: 公告可用性变了（启动流程拉到公告，或本次会话根本没有公告）。
    noticeChanged = Signal()
    #: 状态条文案（**已翻译**；由 App.qml 转给 `Shell.setStatus`）。
    statusMessage = Signal(str, str)
    #: 请求最小化主窗口（`GameService` 说"游戏窗口出现了"，B-03 的最小化开关）。
    minimizeWindowRequested = Signal()
    #: 游戏崩溃（退出码非 0 且非用户强杀）。`message` 已翻译（B-07）。
    crashDetected = Signal(int, str)
    #: 请求重新展示公告（首页的"公告入口"，A-22）。
    noticeRequested = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._game: Any = None
        self._account: Any = None
        self._achievement: Any = None
        self._startup: Any = None
        self._nav: Any = None

        # ── 全部是普通 Python 属性（工作线程会写它们，R7 只允许这么写）──
        self._account_summary: Dict[str, Any] = {}
        self._skin_path = ""
        self._recent_version = ""
        self._game_state = ""
        self._achievement_unlocked = 0
        self._achievement_total = 0
        self._checkin_streak = 0
        self._achievements_loaded = False
        self._ready = False

    # ─── 装配期注入 ─────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        """`main_qml.register_bridges` 在注册前调它：取三个服务。"""
        self._game = self._service(context, "game")
        self._account = self._service(context, "account")
        self._achievement = self._service(context, "achievement")
        if self._game is not None:
            # 观察者协议见 services/game_service.py 的模块文档
            self._game.set_observer(self)
        self.refresh()

    def use_engine(self, engine: Any) -> None:
        """接上 `Startup`（公告入口要它）与 `Nav`（空态里的跳转）。

        **只从引擎的桥表里取，不猜**（缺陷 D-153 的教训）。`Startup` 是在
        `register_bridges` 之后才注册进桥表的（见 `main_qml.assemble`），所以这里
        还顺手接一下它的启动链条信号：启动流程跑完（或阶段变化）时刷一次首页 ——
        否则"账号卡片 / 成就摘要"要等到用户手动操作才会出现（启动流程是异步的，
        `bind()` 那一刻 launcher 与账号系统都还没就绪）。
        """
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        startup = registry.get("Startup")
        if startup is not None:
            self._startup = startup
            self._connect_startup(startup)
        nav = registry.get("Nav")
        if nav is not None:
            self._nav = nav

    def _connect_startup(self, startup: Any) -> None:
        """接启动流程的进度信号 → 首页刷新（接不上只记日志，不影响其它功能）。"""
        for signal_name in ("phaseChanged", "chainFinished"):
            signal = getattr(startup, signal_name, None)
            if signal is None:
                continue
            try:
                signal.connect(self._on_startup_progress)
            except Exception as e:  # noqa: BLE001
                logger.warning("接 Startup.%s 失败: %s", signal_name, e)
        notice_signal = getattr(startup, "noticeChanged", None)
        if notice_signal is not None:
            try:
                # 公告到位/消失 → 首页那个"查看公告"入口的可用性跟着变
                notice_signal.connect(self.noticeChanged.emit)
            except Exception as e:  # noqa: BLE001
                logger.warning("接 Startup.noticeChanged 失败: %s", e)

    def _on_startup_progress(self, *_args: Any) -> None:
        self.refresh()

    @staticmethod
    def _service(context: Any, name: str) -> Any:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001 - 单个服务取不到不该让首页整页打不开
            logger.warning("首页取服务 %s 失败: %s", name, e)
            return None

    # ─── 账号（B-16）───────────────────────────────────────

    @Property(bool, notify=accountChanged)
    def hasAccount(self) -> bool:  # noqa: N802
        return bool(self._account_summary.get("has_account"))

    @Property(str, notify=accountChanged)
    def accountName(self) -> str:  # noqa: N802
        return str(self._account_summary.get("name", ""))

    @Property(str, notify=accountChanged)
    def accountTypeKey(self) -> str:  # noqa: N802
        """账号类型的 **i18n 键**（QML 里 `Tr.map[Home.accountTypeKey]` 才会跟着语言切换）。"""
        return str(self._account_summary.get("type_key", ""))

    @Property(str, notify=accountChanged)
    def accountUuid(self) -> str:  # noqa: N802
        return str(self._account_summary.get("uuid", ""))

    # ─── 皮肤（B-17）───────────────────────────────────────

    @Property(bool, notify=skinChanged)
    def hasSkin(self) -> bool:  # noqa: N802
        return bool(self._skin_path)

    @Property(str, notify=skinChanged)
    def skinPath(self) -> str:  # noqa: N802
        return self._skin_path

    @Property(str, notify=skinChanged)
    def skinFileName(self) -> str:  # noqa: N802
        """皮肤文件名（页面只显示文件名，不显示整条路径）。"""
        if not self._skin_path:
            return ""
        return self._skin_path.replace("\\", "/").rsplit("/", 1)[-1]

    # ─── 最近启动的版本（3.1 的"最近版本一键启动"）──────────

    @Property(str, notify=recentVersionChanged)
    def recentVersion(self) -> str:  # noqa: N802
        return self._recent_version

    @Property(bool, notify=recentVersionChanged)
    def hasRecentVersion(self) -> bool:  # noqa: N802
        return bool(self._recent_version)

    # ─── 游戏进程（B-03 / B-04）────────────────────────────

    @Property(str, notify=gameChanged)
    def gameState(self) -> str:  # noqa: N802
        return self._game_state or "idle"

    @Property(bool, notify=gameChanged)
    def gameRunning(self) -> bool:  # noqa: N802
        return self.gameState in BUSY_STATES

    @Property(bool, notify=gameChanged)
    def gameBusy(self) -> bool:  # noqa: N802
        """正在启动/运行 —— 状态区据此给出"忙"的观感。"""
        return self.gameState in BUSY_STATES

    @Property(bool, notify=gameChanged)
    def gameStarting(self) -> bool:  # noqa: N802
        """**正在启动**（进程已起、窗口还没出现）—— 启动按钮的 loading 绑它。

        与 :attr:`gameBusy` 刻意不同：旧界面在 `launch_done` 那一刻就把启动按钮恢复
        可点了（`ui/app_handlers.py:1160`），也就是**游戏跑着也允许再点启动**。
        本轮保持这个行为（红线 1：功能只增不减、既有行为不擅自改），
        所以 loading 只在"启动中/等窗口"两态显示。
        """
        return self.gameState in (STATE_LAUNCHING, STATE_WAITING)

    @Property(bool, notify=gameChanged)
    def canLaunch(self) -> bool:  # noqa: N802
        """能不能启动：有最近版本、且当前没有在启动/运行。"""
        return bool(self._recent_version) and self.gameState not in (STATE_LAUNCHING, STATE_WAITING)

    @Property(bool, notify=gameChanged)
    def canKill(self) -> bool:  # noqa: N802
        """能不能强杀：进程在跑（含"等窗口"阶段 —— 旧界面在这时就放开强杀按钮了）。"""
        return self.gameState in BUSY_STATES

    # ─── 成就与签到（F-09 / 3.1 的摘要）────────────────────

    @Property(int, notify=achievementsChanged)
    def achievementUnlocked(self) -> int:  # noqa: N802
        return self._achievement_unlocked

    @Property(int, notify=achievementsChanged)
    def achievementTotal(self) -> int:  # noqa: N802
        return self._achievement_total

    @Property(bool, notify=achievementsChanged)
    def achievementsLoaded(self) -> bool:  # noqa: N802
        return self._achievements_loaded

    @Property(int, notify=achievementsChanged)
    def checkinStreak(self) -> int:  # noqa: N802
        return self._checkin_streak

    @Property(bool, notify=noticeChanged)
    def hasNotice(self) -> bool:  # noqa: N802
        """本次会话是否拉到过公告（决定首页那个"查看公告"入口是否可用）。"""
        if self._startup is None:
            return False
        try:
            return bool(self._startup.property("hasNotice"))
        except Exception as e:  # noqa: BLE001
            logger.warning("读 Startup.hasNotice 失败: %s", e)
            return False

    # ─── 槽 ─────────────────────────────────────────────────

    @Slot()
    def refresh(self) -> None:  # noqa: N802
        """重取账号 / 皮肤 / 最近版本 / 成就摘要（见模块文档第 3 条）。"""
        self._refresh_account()
        self._refresh_skin()
        self._refresh_recent_version()
        self._refresh_achievements()
        self._refresh_game_state()
        if not self._ready:
            self._ready = True
            self.readyChanged.emit()

    @Property(bool, notify=readyChanged)
    def ready(self) -> bool:
        """是否已经取过一轮数据（页面用它区分"加载中"与"真的没有内容"）。"""
        return self._ready

    @Slot()
    @Slot(str)
    def launch(self, versionId: str = "") -> None:  # noqa: N802
        """启动一个版本；`versionId` 为空时用"最近使用的版本"。"""
        version = str(versionId or "").strip() or self._recent_version
        if not version:
            self.statusMessage.emit(_("version_id_required"), "error")
            return
        if self._game is None:
            logger.warning("Home.launch(%s)：游戏服务不可用", version)
            return
        self._game.launch(version)

    @Slot()
    def killGame(self) -> None:  # noqa: N802
        """强制结束游戏进程（B-04）。"""
        if self._game is None:
            return
        self._game.kill()

    @Slot(str)
    def selectSkin(self, path: str) -> None:  # noqa: N802
        """选定一个皮肤文件（尺寸校验与复制在 `AccountService`，B-17）。"""
        if self._account is None:
            return
        ok, key, params = self._account.select_skin(path)
        self.refresh()
        self.statusMessage.emit(_(key, **params), "success" if ok else "warning")

    @Slot()
    def removeSkin(self) -> None:  # noqa: N802
        if self._account is None:
            return
        self._account.remove_skin()
        self.refresh()
        self.statusMessage.emit(_("skin_remove"), "info")

    @Slot()
    def openNotice(self) -> None:  # noqa: N802
        """请求重看公告（真正的展示在 `StartupDialogs.qml`，这里只发请求）。"""
        if self._startup is None or not self.hasNotice:
            logger.debug("Home.openNotice()：本次会话没有公告可看")
            return
        self.noticeRequested.emit()

    @Slot()
    def openVersions(self) -> None:  # noqa: N802
        """跳到版本页（空态里的"去安装/管理版本"入口）。"""
        if self._nav is None:
            logger.debug("Home.openVersions()：Nav 未注入，跳过")
            return
        try:
            self._nav.push("versions")
        except Exception as e:  # noqa: BLE001
            logger.warning("跳转版本页失败: %s", e)

    # ─── GameService 的观察者回调（**任意线程**）───────────

    def on_state(self, state: str) -> None:
        self._game_state = state
        self.gameChanged.emit()

    def on_status(self, key: str, level: str, params: Dict[str, Any]) -> None:
        text = self._status_text(key, params)
        self.statusMessage.emit(text, level)

    def on_launch_result(
        self, version_id: str, target_version: Optional[str], success: bool, error: str
    ) -> None:
        if success:
            self._recent_version = str(target_version or version_id)
            self.recentVersionChanged.emit()
        self._refresh_game_state()

    def on_window_detected(self) -> None:
        # 旧实现在这一步才决定要不要最小化（B-03）：动画停下 + 按开关最小化
        try:
            minimize = bool(self._game.minimize_after) if self._game is not None else False
        except Exception:  # noqa: BLE001
            minimize = False
        if minimize:
            self.statusMessage.emit(_("game_window_detected"), "success")
            self.minimizeWindowRequested.emit()
        else:
            self.statusMessage.emit(_("game_window_ready"), "success")
        self._refresh_game_state()

    def on_game_exit(self, exit_code: int, crashed: bool, crash_files: Dict[str, Any], killed: bool) -> None:
        """退出/崩溃。与旧实现一致：强杀时不再重复发"已退出"状态（`ui/app_handlers.py:1304`）。"""
        if not crashed and not killed:
            self.statusMessage.emit(_("game_exited"), "info")
        if crashed:
            self.crashDetected.emit(int(exit_code), _("game_crashed", code=exit_code))
            logger.warning("游戏崩溃（退出码 %s），崩溃文件: %s", exit_code, sorted(crash_files))
        self._refresh_game_state()

    def on_killed(self, exit_code: int) -> None:
        self._refresh_game_state()

    def on_auto_backup(self, world: str, message: str, success: bool) -> None:
        # 自动备份是"顺手做的事"，成功就别打扰用户；失败要看得见（旧实现也是只记日志）
        if not success:
            logger.warning("自动备份失败（%s）: %s", world, message)

    # ─── 内部 ───────────────────────────────────────────────

    def _refresh_account(self) -> None:
        if self._account is None:
            return
        try:
            self._account_summary = self._account.account_summary()
        except Exception as e:  # noqa: BLE001 - 账号服务坏了不该让首页整页打不开
            logger.warning("读账号摘要失败: %s", e)
            self._account_summary = {}
        self.accountChanged.emit()

    def _refresh_skin(self) -> None:
        if self._account is None:
            return
        try:
            path = self._account.skin_path()
        except Exception as e:  # noqa: BLE001
            logger.warning("读皮肤路径失败: %s", e)
            path = ""
        if path != self._skin_path:
            self._skin_path = path
            self.skinChanged.emit()

    def _refresh_recent_version(self) -> None:
        if self._game is None:
            return
        try:
            version = self._game.last_launched_version
        except Exception as e:  # noqa: BLE001
            logger.warning("读最近启动版本失败: %s", e)
            version = ""
        if version != self._recent_version:
            self._recent_version = version
            self.recentVersionChanged.emit()

    def _refresh_achievements(self) -> None:
        if self._achievement is None:
            return
        try:
            self._checkin_streak = self._achievement.checkin_streak()
        except Exception as e:  # noqa: BLE001
            logger.warning("读签到天数失败: %s", e)
        self._notify_achievements()

    def publish_achievements(self, data: Optional[List[Dict[str, Any]]]) -> None:
        """由启动流程把"刚取到的成就总表"喂进来（避免首页再查一次库）。

        `data` 为 None（引擎未初始化）时**保持上一次的数字**，只把
        `achievementsLoaded` 留在 False —— 与旧界面"取不到就不渲染"一致。
        """
        if data is None:
            self._achievements_loaded = False
            self._notify_achievements()
            return
        summary = None
        if self._achievement is not None:
            summary = self._achievement.summarize(data)
        if summary is None:
            self._achievements_loaded = False
        else:
            self._achievement_total = int(summary.total)
            self._achievement_unlocked = int(summary.unlocked)
            self._achievements_loaded = True
        self._notify_achievements()

    def _notify_achievements(self) -> None:
        self.achievementsChanged.emit()

    def _refresh_game_state(self) -> None:
        if self._game is not None:
            self._game_state = self._game.state
        self.gameChanged.emit()

    def _status_text(self, key: str, params: Dict[str, Any]) -> str:
        """把 `GameService` 报的 i18n 键翻成当前语言的一句话。

        键是**字面量**（见模块文档第 1 条）；未知键回退成键名本身 ——
        与旧界面 `_(key)` 的行为一致（缺键时界面显示键名，不显示空白）。
        """
        data = params or {}
        if key == "game_launching":
            return _("game_launching", version=data.get("version", ""))
        if key == "game_launched":
            return _("game_launched", version=data.get("version", ""))
        if key == "game_launch_failed":
            return _("game_launch_failed", version=data.get("version", ""))
        if key == "game_launch_error":
            return _("game_launch_error", error=data.get("error", ""))
        if key == "game_window_detected":
            return _("game_window_detected")
        if key == "game_window_ready":
            return _("game_window_ready")
        if key == "game_exited":
            return _("game_exited")
        if key == "game_crashed":
            return _("game_crashed", code=data.get("code", -1))
        if key == "game_process_killed":
            return _("game_process_killed")
        if key == "game_no_process":
            return _("game_no_process")
        if key == "version_id_required":
            return _("version_id_required")
        logger.warning("HomeBridge 收到未知状态键 %r（回退成键名）", key)
        return key


__all__ = ["BUSY_STATES", "HomeBridge"]
