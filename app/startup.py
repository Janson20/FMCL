"""启动流程控制器（阶段 2 任务 2.14）—— 把旧 `main.py` 的启动时序**逐条复刻**到 QML 侧。

## 旧实现有 4 条互相竞争的退出路径（风险 R-34 明确点名不能简化掉）

`main.py:259-306` 的 `_try_dismiss_splash` / `_dismiss_splash` / `_show_init_error` 是一个
带容错的时序状态机，本模块逐条对齐：

| # | 条件 | 旧行为 | 本实现 |
|---|------|--------|--------|
| 1 | launcher 与成就引擎**都就绪**，且已过 1 秒 | 关启动画面 → 显示主窗口 | `splashDismissed` + `mainWindowReady` |
| 2 | 到 30 秒仍未就绪 | 记 warning 并**强制**关启动画面 | 同左（`timeout` 计入 `describe()`） |
| 3 | launcher 初始化**失败** | 关启动画面 → **仍然显示主窗口** → 状态栏报错 → 弹错误框 | `coreFailed(title, message)`；主窗口照常显示 |
| 4 | 关启动画面本身抛异常 | 记 error，继续走后续流程 | 同左（`_dismiss` 里兜住） |

另外两条与旧实现一致的行为：

* 每 200ms 轮询一次（不是轮询到就关，而是"轮询 + 满足条件后按剩余时间补足 1 秒"）；
* 两个初始化**并行**跑（启动器核心与成就引擎各一条后台任务）。

## 启动后的链条：协议 → 公告 → 预下载（对照表 A-21 / A-22 / A-23）

`main.py:456` 的顺序是 `_on_app_ready(on_agreement_complete=...)` → 公告 → 预下载：

1. `terms_consent` 或 `ai_privacy_consent` 任一为假 → 弹协议（旧实现延迟 500ms）→ 用户确认后写回配置；
2. 拉公告（`services.notice_service.fetch_notice`，失败**静默跳过**）→ 用户关闭后继续；
3. 预下载检查（`launcher.predownload.run_predownload_check(ui_port, minecraft_dir, _)`，它走 `UIPort`，
   所以 QML 侧**不需要改服务层**）。

## 为什么这些逻辑不写在 QML 里

计时、超时、失败兜底、链条顺序都是**容错语义**，属于"简化实现就会丢容错"的那一类（R-34）。
放在 Python 侧可以被单元测试用注入的时钟与假初始化函数**快速、确定地**验证；
写在 QML 里就只能靠等真实时间。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, Optional

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot

logger = logging.getLogger("app.startup")

#: 阶段取值（QML 用来决定显示启动画面还是主窗口）
PHASE_SPLASH = "splash"
PHASE_READY = "ready"
PHASE_FAILED = "failed"

#: 默认时序（与旧实现逐字一致：≥1 秒、30 秒硬超时、200ms 轮询）
DEFAULT_MIN_SPLASH_MS = 1000
DEFAULT_HARD_TIMEOUT_MS = 30000
DEFAULT_POLL_MS = 200
#: 协议弹窗的延迟（旧实现 `self.after(500, ...)`）
DEFAULT_AGREEMENT_DELAY_MS = 500


class StartupController(QObject):
    """启动时序 + 启动后链条。构造后调 :meth:`start`。"""

    phaseChanged = Signal()
    splashDismissed = Signal()
    #: 核心初始化失败（**主窗口仍然要显示**，这是旧实现的行为）
    coreFailed = Signal(str, str)
    #: 状态栏文本（旧 `set_status(text, level)`，10 秒自动清空由 Shell 桥负责）
    statusChanged = Signal(str, str)
    #: 需要显示用户协议/AI 隐私同意弹窗
    agreementRequired = Signal()
    #: 公告拉到了（内容非空才发）
    noticeReady = Signal(str)
    #: 本次会话有没有公告可看（首页的"公告入口"要它）
    noticeChanged = Signal()
    #: 协议 → 公告 → 预下载 链条走完
    chainFinished = Signal()
    #: 后台静默检查更新发现了新版本（A-24；版本号 + 更新说明）
    updateAvailable = Signal(str, str)

    def __init__(
        self,
        context: Any,
        *,
        ui_port: Any = None,
        min_splash_ms: int = DEFAULT_MIN_SPLASH_MS,
        hard_timeout_ms: int = DEFAULT_HARD_TIMEOUT_MS,
        poll_ms: int = DEFAULT_POLL_MS,
        agreement_delay_ms: int = DEFAULT_AGREEMENT_DELAY_MS,
        launcher_factory: Optional[Callable[[], Any]] = None,
        achievements_factory: Optional[Callable[[], Any]] = None,
        notice_fetcher: Optional[Callable[[], Optional[str]]] = None,
        predownload_runner: Optional[Callable[[], Any]] = None,
        terms_loader: Optional[Callable[[], str]] = None,
        launcher_wiring: Optional[Callable[[Any], None]] = None,
        update_checker: Optional[Callable[[], Any]] = None,
        clock: Callable[[], float] = time.monotonic,
        splash_expected: bool = False,
        parent: Optional[QObject] = None,
    ) -> None:
        """
        Args:
            注入点（全部为测试而设，默认值是生产实现）：
            `launcher_factory` / `achievements_factory` / `notice_fetcher` /
            `predownload_runner` / `clock` / 三个毫秒参数。
            splash_expected: 调用方**马上就会**调 :meth:`start`（装配方知道这件事：
                `main_qml.assemble` 的 `start_startup`）。它只影响"引擎加载那一刻"
                QML 看到的 :attr:`startupActive`：置真时主窗口从一开始就藏着，
                不会先闪一下再被启动画面盖住（缺陷 D-142 的落地细节）。
        """
        super().__init__(parent)
        self._context = context
        self._ui_port = ui_port
        self.min_splash_ms = int(min_splash_ms)
        self.hard_timeout_ms = int(hard_timeout_ms)
        self.poll_ms = max(10, int(poll_ms))
        self.agreement_delay_ms = int(agreement_delay_ms)
        self._clock = clock

        self._launcher_factory = launcher_factory or self._default_launcher_factory
        self._achievements_factory = achievements_factory or self._default_achievements_factory
        self._notice_fetcher = notice_fetcher or self._default_notice_fetcher
        self._predownload_runner = predownload_runner or self._default_predownload_runner
        #: 协议全文的读取器（默认 `services.legal_service.load_terms_text`）。**测试注入点**：
        #: 默认实现读仓库根的 `TERMS_OF_USE.md`（16 KB），测试没必要真读盘。
        self._terms_loader = terms_loader or self._default_terms_loader
        #: 懒加载缓存（协议全文只读一次；`termsText` 是 constant 属性）
        self._terms_cache: Optional[str] = None
        #: 核心接线（端口/账号系统/游戏语言）。**测试注入点**：默认实现会碰真实
        #: `config.json` / `accounts.json`，单元测试必须能把它换掉（见 `_wire_launcher`）。
        self._launcher_wiring = launcher_wiring
        #: 自动检查更新的实现（默认 `updater.check_for_update`）。**测试注入点**：
        #: 真实实现会发 HTTP，测试里必须能换掉（阶段 2 的启动测试全是毫秒级时序断言，
        #: 一次网络往返就能让它们全红）。
        self._update_checker = update_checker

        self._phase = PHASE_SPLASH
        self._status_text = ""
        self._status_level = "info"
        self._started_at = 0.0
        self._splash_expected = bool(splash_expected)
        self._launcher_ready = threading.Event()
        self._achievements_ready = threading.Event()
        self._dismissed = False
        self._dismiss_reason = ""
        self._launcher_error: Optional[str] = None
        self._chrono: list = []  # 事件流水（供测试与诊断）
        #: 本次会话拉到的公告正文（首页"公告入口"重看用；空串 = 没有公告）
        self._last_notice = ""
        #: 当前这次公告展示是"重看"还是"启动链条里的第一次"。
        #: 重看关掉时**不能**再跑一次预下载（那是启动链条的一步，不是公告的附庸）。
        self._notice_replay = False
        #: 启动后置任务是否已经踢出去过（`start_post_ready_tasks()` 的幂等闸）
        self._post_ready_started = False
        #: QML 侧桥表（`main_qml.assemble` 用 :meth:`set_bridges` 回填）。
        #: 启动后置任务要把成就总览交给首页桥，但桥是在 StartupController 之后
        #: 才注册的，所以只能走"入口回填"这条路（同 `HomeBridge.use_engine`）。
        self._bridges: Dict[str, Any] = {}

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.poll_ms)
        self._poll_timer.timeout.connect(self._poll)
        self._agreement_timer = QTimer(self)
        self._agreement_timer.setSingleShot(True)
        self._agreement_timer.timeout.connect(self._check_agreement)

    # ─── 只读状态 ───────────────────────────────────────────

    @Property(str, notify=phaseChanged)
    def phase(self) -> str:
        return self._phase

    @Property(str, notify=phaseChanged)
    def statusText(self) -> str:  # noqa: N802
        return self._status_text

    @Property(str, notify=phaseChanged)
    def statusLevel(self) -> str:  # noqa: N802
        return self._status_level

    @Property(bool, notify=phaseChanged)
    def launcherReady(self) -> bool:  # noqa: N802
        return self._launcher_ready.is_set()

    @Property(bool, notify=phaseChanged)
    def achievementsReady(self) -> bool:  # noqa: N802
        return self._achievements_ready.is_set()

    @Property(bool, notify=phaseChanged)
    def dismissed(self) -> bool:
        return self._dismissed

    @Property(bool, notify=noticeChanged)
    def hasNotice(self) -> bool:  # noqa: N802
        """本次会话是否拉到过公告（首页据此决定"查看公告"入口是否可用）。"""
        return bool(self._last_notice)

    @Property(str, constant=True)
    def termsText(self) -> str:  # noqa: N802
        """用户协议**全文**（`TERMS_OF_USE.md` 的 Markdown 原文）。

        为什么是 Markdown 原文而不是 HTML：QML 的 `Text { textFormat: Text.MarkdownText }`
        自带渲染，而且颜色/字号来自 `Theme.*`（旧实现把 `_TERMS_DARK_CSS` 塞进 `<style>`，
        Qt 的富文本引擎根本不认 `<style>`，照搬会得到黑字黑底）。
        读不到时返回空串 —— `StartupDialogs.qml` 会退回语言文件里的摘要
        （旧实现在读不到时也是给一句提示，语义一致）。
        """
        if self._terms_cache is None:
            try:
                self._terms_cache = str(self._terms_loader() or "")
            except Exception as e:  # noqa: BLE001 - 协议读不出来不该让弹窗打不开
                logger.error("读取用户协议失败: %s", e)
                self._terms_cache = ""
            logger.info("用户协议全文：%d 字符", len(self._terms_cache))
        return self._terms_cache

    @Property(bool, notify=phaseChanged)
    def splashExpected(self) -> bool:  # noqa: N802
        """调用方是否声明了"马上要跑启动流程"（`splash_expected`，只读）。"""
        return self._splash_expected

    @Property(bool, notify=phaseChanged)
    def startupActive(self) -> bool:  # noqa: N802
        """**启动画面是否占屏** —— QML 的 `Splash` 与主窗口都只绑这一个属性。

        语义（缺陷 D-142 的落地）：

        * 启动流程**没开跑**（`assemble(start_startup=False)`：测试、探针、CLI 装配
          半个引擎的场景）→ False：没有"正在启动"这回事，不该把窗口藏起来，
          更不该显示一个**永远不会自己关掉**的加载窗（那正是用户报上来的现象）；
        * 开跑之后 → 由四条竞争退出路径决定的 `dismissed` 说了算。

        为什么要有 `splash_expected` 这一档：`start()` 必须在 `engine.load()` **之后**
        调（协议/公告弹窗要 QML 侧的宿主先就位），而 QML 在 `load()` 期就要决定
        "藏主窗口、显启动画面"。没有这个声明位的话，主窗口会先显示一帧、
        再被启动画面盖住 —— 一次看得见的闪。
        """
        if not self._splash_expected and not self._started_at:
            return False
        return not self._dismissed

    @Property(int, constant=True)
    def minimumSplashMs(self) -> int:  # noqa: N802
        return self.min_splash_ms

    @Property(int, constant=True)
    def hardTimeoutMs(self) -> int:  # noqa: N802
        return self.hard_timeout_ms

    @Slot(result="QVariantMap")
    def describe(self) -> dict:
        return {
            "phase": self._phase,
            "dismissed": self._dismissed,
            "dismiss_reason": self._dismiss_reason,
            "startup_active": self.startupActive,
            "splash_expected": self._splash_expected,
            "started": bool(self._started_at),
            "launcher_ready": self._launcher_ready.is_set(),
            "achievements_ready": self._achievements_ready.is_set(),
            "launcher_error": self._launcher_error,
            "elapsed_ms": int((self._clock() - self._started_at) * 1000) if self._started_at else 0,
            "min_splash_ms": self.min_splash_ms,
            "hard_timeout_ms": self.hard_timeout_ms,
            "chrono": list(self._chrono),
        }

    # ─── 启动 ───────────────────────────────────────────────

    def start(self) -> None:
        """开跑：记时、并行初始化、开轮询。**幂等**（重复调用只记一笔）。"""
        if self._started_at:
            logger.debug("StartupController.start() 被重复调用，忽略")
            return
        self._started_at = self._clock()
        self._chrono.append(("start", 0))
        # 级别用 "info" 而不是自造的 "loading"：状态条（`ShellBridge.setStatus`）只认
        # info / success / warning / error 四档，其它值会被它降级成 info 并打一条 warning
        # （返工 A 组接线时在日志里实测到 `未知状态级别 'loading'`）。四档是跨界面的公共词汇，
        # 不该在这里另立一套。
        self._set_status("startup_initializing", "info")
        tasks = getattr(self._context, "tasks", None)

        def _run(fn: Callable[[], Any], label: str) -> None:
            if tasks is None:
                fn()
                return
            tasks.submit(fn, name=f"startup.{label}", on_error=self._on_task_error)

        _run(self._init_launcher, "launcher")
        _run(self._init_achievements, "achievements")
        self._poll_timer.start()
        self._poll()

    def stop(self) -> None:
        """退出路径：停表（初始化线程是 daemon，不必等）。"""
        try:
            self._poll_timer.stop()
            self._agreement_timer.stop()
        except RuntimeError:
            pass

    def set_bridges(self, bridges: Dict[str, Any]) -> None:
        """回填 QML 侧桥表（由 `main_qml.assemble` 在注册完所有桥之后调用）。

        为什么需要它：启动后置任务要把"刚取到的成就总览"交给首页（省一次查库），
        而 `Home` 桥是在本控制器**之后**才注册进引擎的 —— 构造期拿不到，
        只能由入口回填。桥缺席时相关功能静默跳过（首页自己会再刷新一次）。
        """
        self._bridges = dict(bridges or {})

    # ─── 四条竞争路径 ───────────────────────────────────────

    def _poll(self) -> None:
        if self._dismissed:
            return
        elapsed_ms = (self._clock() - self._started_at) * 1000

        # 路径 3：核心初始化失败 → 立刻收尾（主窗口照常显示）
        if self._launcher_error is not None:
            self._dismiss("failed")
            self.coreFailed.emit("startup_failed_title", self._launcher_error)
            self._set_status(self._launcher_error, "error")
            return

        both_ready = self._launcher_ready.is_set() and self._achievements_ready.is_set()
        if both_ready:
            # 路径 1：都就绪 → 补足"至少显示 1 秒"
            if elapsed_ms >= self.min_splash_ms:
                self._dismiss("ready")
            return

        # 路径 2：硬超时 → 强制关（保留旧实现的 warning 语义）
        if elapsed_ms >= self.hard_timeout_ms:
            logger.warning(
                "启动等待超时 %d ms，强制关闭启动画面（launcher=%s achievements=%s）",
                self.hard_timeout_ms,
                self._launcher_ready.is_set(),
                self._achievements_ready.is_set(),
            )
            self._dismiss("timeout")

    def _dismiss(self, reason: str) -> None:
        """关启动画面。**路径 4**：这一段的异常不能影响后续流程。"""
        if self._dismissed:
            return
        self._dismissed = True
        self._dismiss_reason = reason
        self._chrono.append(("dismiss", int((self._clock() - self._started_at) * 1000)))
        try:
            self._poll_timer.stop()
        except RuntimeError:
            pass
        try:
            self.splashDismissed.emit()
        except Exception as e:  # noqa: BLE001 - 关画面失败也要继续
            logger.error("关闭启动画面失败（继续启动）: %s", e)

        if reason == "failed":
            self._set_phase(PHASE_FAILED)
            # 旧实现：初始化失败时**仍然显示主窗口**并报错，不退出。
            logger.error("启动器初始化失败，主窗口仍会显示：%s", self._launcher_error)
            return

        self._set_phase(PHASE_READY)
        self._set_status("startup_ready", "success")
        # 链条：延迟一点再查协议（旧实现 `after(500, ...)`）
        self._agreement_timer.start(self.agreement_delay_ms)
        # 启动后置任务（Token 刷新 / 自动检查更新 / 成就云同步 / 签到）。
        # 时机与旧实现一致：旧 `main.py` 在 `_dismiss_splash()` 之后立刻
        # `threading.Thread(target=_post_init_achievements)`，Token 刷新也是
        # 在 launcher 就绪的同一段里踢出去的。
        self.start_post_ready_tasks()

    # ─── 初始化任务 ─────────────────────────────────────────

    def _init_launcher(self) -> None:
        try:
            launcher = self._launcher_factory()
            self._chrono.append(("launcher_ready", int((self._clock() - self._started_at) * 1000)))
            # 注册进 AppContext：服务/界面以后用 ctx.try_get("launcher") 拿同一个实例
            try:
                if self._context is not None:
                    self._context.register_instance("launcher", launcher, replace=True)
            except Exception as e:  # noqa: BLE001
                logger.warning("把 launcher 注册进 AppContext 失败: %s", e)
            # 接线**排在后面单独一个后台任务**，不占"核心就绪"这一步的时间：它要
            # import `launcher.account`（首次约 600 ms）与 `main`，而启动画面的四条
            # 竞争退出路径只等"核心就绪"这一个信号。旧入口也是这个顺序 ——
            # `_dismiss_splash()` 之后才在 `_on_launcher_ready()` 里接线。
            self._schedule_wiring(launcher)
        except Exception as e:  # noqa: BLE001 - 失败要能被路径 3 看到
            self._launcher_error = str(e) or type(e).__name__
            logger.error("启动器核心初始化失败: %s", e, exc_info=True)
        finally:
            self._launcher_ready.set()

    def _schedule_wiring(self, launcher: Any) -> None:
        """把接线踢进后台任务（拿不到任务框架时就地跑）。"""
        tasks = getattr(self._context, "tasks", None)
        if tasks is None:
            self._wire_launcher(launcher)
            return
        try:
            tasks.submit(self._wire_launcher, launcher, name="startup.wiring")
        except Exception as e:  # noqa: BLE001 - 提交失败就退化成同步接线
            logger.warning("接线任务提交失败（改为就地执行）: %s", e)
            self._wire_launcher(launcher)

    def _wire_launcher(self, launcher: Any) -> None:
        """把核心接到"界面之外的那一圈"上（旧 `main.py:337-455` 的接线段）。

        这一段**不能留给页面**：少了它，QML 版启动游戏时不会带上账号 ——
        ``MinecraftLauncher.launch_game()`` 里 ``if self._account_system:`` 才是
        登录凭据的唯一来源，没注入就静默退化成 ``config.player_name`` 的离线身份
        （`launcher/core.py:1025-1045`）。旧入口把这段写在 ``_on_launcher_ready()``
        里，本方法是它的对应物。

        **具体步骤在服务里**（红线 2：业务逻辑只属于 services）：
        `AccountService.attach_launcher()` 负责端口/迁移/账号系统注入，
        `GameService.ensure_game_language()` 负责 A-26。本函数只做"谁被调到"的编排，
        并且在测试里可以被整体替换（`launcher_wiring` 注入点）——
        否则单元测试会去动真实 `config.json` 与 `accounts.json`。
        """
        wiring = self._launcher_wiring or self._default_launcher_wiring
        try:
            wiring(launcher)
        except Exception as e:  # noqa: BLE001 - 接线失败不该挡住启动（界面会有提示）
            logger.error("启动器接线失败: %s", e, exc_info=True)

    def _default_launcher_wiring(self, launcher: Any) -> None:
        """生产路径的接线（六件事，顺序与旧实现一致）。"""
        port = self._ui_port or (getattr(self._context, "ui", None) if self._context else None)
        if port is not None:
            try:
                launcher.set_ui_port(port)
            except Exception as e:  # noqa: BLE001 - 端口缺失时核心会自己降级
                logger.warning("UI 端口注入核心失败: %s", e)

        account = self._service("account")
        if account is not None:
            attached = account.attach_launcher(launcher, port)
            logger.info("账号系统接线：%s", "已注入启动器" if attached else "不可用（将退化为离线身份）")

        game = self._service("game")
        if game is not None:
            game.use_launcher(launcher)
            game.ensure_game_language()

    def _init_achievements(self) -> None:
        try:
            self._achievements_factory()
            self._chrono.append(("achievements_ready", int((self._clock() - self._started_at) * 1000)))
        except Exception as e:  # noqa: BLE001 - 成就初始化失败**不影响启动**（旧实现同）
            logger.error("成就引擎初始化失败: %s", e)
        finally:
            self._achievements_ready.set()

    def _on_task_error(self, error: BaseException) -> None:
        """兜底：任务框架报错（例如执行基底拒绝提交）也算初始化失败。"""
        logger.error("启动初始化任务失败: %s", error)
        self._launcher_error = self._launcher_error or str(error)
        self._launcher_ready.set()

    # ─── 启动后置任务（A-24 / A-25 / F-09）──────────────────

    def start_post_ready_tasks(self) -> None:
        """启动完成后要跑的四件事。**幂等**（重复调用只跑一次）。

        与旧实现的对应关系（都在 `main.py` 的启动段里）：

        * Token 刷新 —— ``main.py:429`` 的 ``_auto_refresh_tokens``（A-25）；
        * 自动检查更新 —— ``main.py:465-466``，受 ``config.auto_check_update`` 控制、
          **静默模式**（没新版本不提示，A-24）；
        * 成就云同步 —— ``main.py:317-327``（F-09 前半句）；
        * 每日签到 —— ``main.py:328-334``（F-09 后半句"连续天数提示"）。

        四件事都在后台任务里跑（旧实现也是各自一个线程），失败只记日志 ——
        它们是"顺手做的事"，不该让界面弹错误框。
        """
        if self._post_ready_started:
            return
        self._post_ready_started = True

        def _run(fn: Callable[[], Any], label: str) -> None:
            tasks = getattr(self._context, "tasks", None)
            if tasks is None:
                fn()
                return
            try:
                tasks.submit(fn, name=f"startup.{label}")
            except Exception as e:  # noqa: BLE001 - 提交失败不该挡住其余几件
                logger.warning("启动后置任务 %s 提交失败: %s", label, e)

        _run(self._refresh_tokens, "tokens")
        if self._auto_update_enabled():
            _run(self._check_update, "update")
        _run(self._achievements_post_ready, "achievements")

    def _auto_update_enabled(self) -> bool:
        return bool(self._config_value("auto_check_update", False))

    def _config_value(self, name: str, default: Any) -> Any:
        """读一个配置项：优先 `AppContext` 里那份（测试可注入），回退全局单例。"""
        try:
            config = getattr(self._context, "config", None) if self._context is not None else None
            if config is None:
                from config import config as global_config

                config = global_config
            return getattr(config, name, default)
        except Exception as e:  # noqa: BLE001 - 读配置失败按默认值处理
            logger.warning("读取配置 %s 失败（用默认值 %r）: %s", name, default, e)
            return default

    def _refresh_tokens(self) -> None:
        """A-25：批量刷新微软账号 Token（旧 `main.py:103-113`）。"""
        service = self._service("account")
        if service is None:
            return
        count = service.auto_refresh_tokens()
        if count:
            logger.info("启动时自动刷新了 %d 个微软账号的 Token", count)

    def _check_update(self) -> None:
        """A-24：后台静默检查更新。有新版本才发信号（旧实现同）。"""
        checker = self._update_checker
        if checker is None:
            try:
                from updater import check_for_update

                checker = check_for_update
            except Exception as e:  # noqa: BLE001 - updater 缺席就跳过这一步
                logger.warning("自动检查更新不可用（%s）", e)
                return
        try:
            info = checker()
        except Exception as e:  # noqa: BLE001 - 旧实现在静默模式下连状态栏都不写
            logger.warning("自动检查更新失败（静默跳过）: %s", e)
            return
        if not info:
            return
        self._chrono.append(("update_available", int((self._clock() - self._started_at) * 1000)))
        self.updateAvailable.emit(str(info.get("version", "")), str(info.get("body", "")))

    def _achievements_post_ready(self) -> None:
        """F-09：成就云同步 + 每日签到，然后把总览喂给首页。"""
        service = self._service("achievement")
        if service is None:
            return
        engine = service.engine()
        if engine is None:
            return

        token = ""
        try:
            token = str(self._config_value("jdz_token", "") or "")
        except Exception as e:  # noqa: BLE001
            logger.warning("读取净读 Token 失败（跳过云同步）: %s", e)

        if token:
            self._set_status("startup_ach_syncing", "info")
            try:
                ok = service.sync_with_engine(engine, token)
            except Exception as e:  # noqa: BLE001 - 同步失败只提示，不影响使用
                logger.error("成就云存档同步失败: %s", e)
                self._set_status("startup_ach_sync_failed", "warning")
                ok = False
            if ok:
                self._chrono.append(("achievements_synced", int((self._clock() - self._started_at) * 1000)))
                self._set_status("startup_ach_synced", "success")
            else:
                # 旧实现在这里无论 `run_sync` 返回什么都会提示"同步完成"
                # （它压根没看返回值）。本版如实区分 —— 见 `07-known-defects.md` 的 D-163。
                self._set_status("startup_ach_sync_failed", "warning")

        # 签到：连续天数由 `AchievementService.checkin()` 如实返回（旧实现在这里
        # 读的是 `result.get("success")/get("streak")`，两个键都不存在 —— 提示是死代码，
        # 详见 `services/achievement_service.py` 的 `checkin()`）。
        try:
            result = service.checkin()
        except Exception as e:  # noqa: BLE001
            logger.error("每日签到失败: %s", e)
            self._set_status("startup_checkin_failed", "warning")
        else:
            if result.get("checked_in"):
                self._chrono.append(("checked_in", int((self._clock() - self._started_at) * 1000)))
                self._set_status("startup_checkin_ok", "success")
            # 已签到时**不发提示**（旧实现同样什么都不显示），但天数仍然要显示在首页上

        self._publish_achievements(service)

    def _publish_achievements(self, service: Any) -> None:
        """把成就总览交给首页桥（少查一次库；桥缺席时静默跳过）。"""
        bridge = self._service_bridge("Home")
        if bridge is None:
            return
        data = None
        try:
            data = service.load_progress()
        except Exception as e:  # noqa: BLE001
            logger.warning("读取成就总表失败: %s", e)
        try:
            bridge.publish_achievements(data)
        except Exception as e:  # noqa: BLE001
            logger.warning("把成就总览交给首页失败: %s", e)

    def _service(self, name: str) -> Any:
        if self._context is None:
            return None
        getter = getattr(self._context, "try_get", None)
        if not callable(getter):
            return None  # 测试用的假上下文可能没有服务表
        try:
            return getter(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("取服务 %s 失败: %s", name, e)
            return None

    def _service_bridge(self, name: str) -> Any:
        """取 QML 侧的桥（`main_qml` 注册进 `engine._fmcl_bridges`，这里由入口回填）。"""
        return (self._bridges or {}).get(name)

    # ─── 协议 → 公告 → 预下载 ───────────────────────────────

    def _check_agreement(self) -> None:
        try:
            from config import config

            agreed = bool(getattr(config, "terms_consent", False)) and bool(
                getattr(config, "ai_privacy_consent", False)
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("读取协议同意状态失败（按已同意处理）: %s", e)
            agreed = True

        if not agreed:
            self._chrono.append(("agreement_required", int((self._clock() - self._started_at) * 1000)))
            self.agreementRequired.emit()
            return
        self._fetch_notice()

    @Slot()
    def confirmAgreement(self) -> None:  # noqa: N802
        """QML 侧用户点了"同意"：写回两个标志并继续链条。"""
        try:
            from config import config

            config.terms_consent = True
            config.ai_privacy_consent = True
            save = getattr(config, "save_config", None)
            if callable(save):
                save()
        except Exception as e:  # noqa: BLE001 - 写不进去也要让用户能继续用
            logger.error("写入协议同意状态失败: %s", e)
        self._chrono.append(("agreement_confirmed", int((self._clock() - self._started_at) * 1000)))
        self._fetch_notice()

    def _fetch_notice(self) -> None:
        tasks = getattr(self._context, "tasks", None)

        def _work() -> None:
            content = None
            try:
                content = self._notice_fetcher()
            except Exception as e:  # noqa: BLE001 - 公告失败静默跳过（旧实现同）
                logger.warning("拉取公告失败（跳过）: %s", e)
            self._after_notice(content)

        if tasks is None:
            _work()
        else:
            tasks.submit(_work, name="startup.notice", on_error=lambda e: self._after_notice(None))

    @Slot(str)
    def noticeFetched(self, content: str) -> None:  # noqa: N802
        """（QML 侧不需要调；给测试与"外部已拿到公告"的场景留的口子。）"""
        self._after_notice(content or None)

    def _after_notice(self, content: Optional[str]) -> None:
        def _deliver() -> None:
            if content:
                self._chrono.append(("notice_ready", int((self._clock() - self._started_at) * 1000)))
                self._last_notice = str(content)
                self._notice_replay = False
                self.noticeChanged.emit()
                self.noticeReady.emit(content)
            else:
                self.startPredownload()

        # 保证在主线程（后台任务回来的路上可能是 worker 线程）
        scheduler = getattr(self._context, "scheduler", None)
        if scheduler is not None:
            scheduler(_deliver)
        else:
            _deliver()

    @Slot()
    def replayNotice(self) -> None:  # noqa: N802
        """重新展示本次会话的公告（首页的"公告入口"）。

        与启动链条里的那次展示**刻意不同**：关掉它不会触发预下载
        （``dismissNotice()`` 会看 ``_notice_replay``）。没有公告时什么都不做。
        """
        if not self._last_notice:
            logger.debug("replayNotice()：本次会话没有公告")
            return
        self._notice_replay = True
        self.noticeReady.emit(self._last_notice)

    @Slot()
    def dismissNotice(self) -> None:  # noqa: N802
        """QML 侧关掉公告 → 继续预下载；**重看**关掉时只结束展示。"""
        if self._notice_replay:
            self._notice_replay = False
            self._chrono.append(("notice_replayed_closed", int((self._clock() - self._started_at) * 1000)))
            return
        self.startPredownload()

    @Slot()
    def startPredownload(self) -> None:  # noqa: N802
        """预下载检查。服务层的 `run_predownload_check` 走 `UIPort`，QML 侧不用改它。"""
        tasks = getattr(self._context, "tasks", None)

        def _work() -> None:
            try:
                self._predownload_runner()
            except Exception as e:  # noqa: BLE001 - 预下载失败不该挡住使用
                logger.error("预下载检查失败（跳过）: %s", e)
            self._chrono.append(("chain_finished", int((self._clock() - self._started_at) * 1000)))
            self._emit_finished()

        if tasks is None:
            _work()
        else:
            tasks.submit(_work, name="startup.predownload", on_error=lambda e: self._emit_finished())

    def _emit_finished(self) -> None:
        try:
            self.chainFinished.emit()
        except RuntimeError:
            pass

    # ─── 默认实现（生产路径） ───────────────────────────────

    @staticmethod
    def _default_launcher_factory() -> Any:
        from config import config
        from launcher import MinecraftLauncher

        return MinecraftLauncher(config)

    @staticmethod
    def _default_achievements_factory() -> Any:
        from achievement_engine import init_achievement_engine
        from config import config

        return init_achievement_engine(config.base_dir)

    @staticmethod
    def _default_notice_fetcher() -> Optional[str]:
        from services.notice_service import fetch_notice

        return fetch_notice()

    @staticmethod
    def _default_terms_loader() -> str:
        """生产路径：读仓库根 / `_MEIPASS` 下的 `TERMS_OF_USE.md`（见 `services/legal_service`）。"""
        from services.legal_service import load_terms_text

        return load_terms_text()

    def _default_predownload_runner(self) -> Any:
        from config import config
        from launcher.predownload import run_predownload_check
        from services.i18n_service import _

        port = self._ui_port
        if port is None and self._context is not None:
            port = getattr(self._context, "ui", None)
        if port is None:
            logger.warning("没有 UI 端口，跳过预下载检查")
            return None
        return run_predownload_check(port, config.minecraft_dir, _)

    # ─── 内部小工具 ─────────────────────────────────────────

    def _set_phase(self, phase: str) -> None:
        if phase != self._phase:
            self._phase = phase
            self.phaseChanged.emit()

    def _set_status(self, key_or_text: str, level: str) -> None:
        """状态文本。**直接给 i18n 键名**（旧实现传的是已翻译的中文；
        这里传键名，由 QML 侧 `Tr.map[...]` 翻译 —— 避免 Python 侧持有界面文案）。"""
        self._status_text = key_or_text
        self._status_level = level
        self.phaseChanged.emit()
        self.statusChanged.emit(key_or_text, level)


__all__ = [
    "DEFAULT_AGREEMENT_DELAY_MS",
    "DEFAULT_HARD_TIMEOUT_MS",
    "DEFAULT_MIN_SPLASH_MS",
    "DEFAULT_POLL_MS",
    "PHASE_FAILED",
    "PHASE_READY",
    "PHASE_SPLASH",
    "StartupController",
]
