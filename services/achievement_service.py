"""成就服务 —— `ui/app_achievements.py`（`AchievementTabMixin`，537 行 / 19 方法）
里那些**本质是逻辑、只是恰好写在界面类里**的代码（阶段 1 任务 1.12）。

## 切缝判据（只有一条）

**这段代码是否 import GUI、或是否直接创建/销毁控件。** 是 → 留在界面；
否 → 搬进本模块。服务自身不弹窗、不起线程、不调 ``after``、不碰控件、
不 ``import ui.*``、不 ``import ui.i18n``（文案一律留界面），
由 ``scripts/check_services_purity.py`` 持续守住。

## 这里的"引擎"与"服务"不是一回事

`services/achievement_engine.py`（623 行 / 25 方法）、`achievement_sync.py`、
`achievement_defs.py` 原本就是**零 GUI 的现成引擎**，本轮**一行都不改**。
本服务只做"编排"：

- 引擎单例的取用（原文各处都是函数体内 `from achievement_engine import
  get_achievement_engine`，这里保持惰性 import 与"全局单例"语义）；
- 进度数据 → 界面直接可渲染的统计量（总数 / 已解锁数 / 百分比 / 分类计数）；
- 同步、云重置、本地重置的调用编排；
- "哪一项成就、第几阶段解锁了"的载荷归一化。

## 切缝落在哪（四段）

1. **纯逻辑**：`summarize`（总量/已解锁/百分比）、`category_counts`（分类的
   "已解锁/总数"）、`format_sync_time`（时间戳 → ``%Y-%m-%d %H:%M:%S``）。
   逐字搬出，零改写。
2. **取值**：`load_progress`（`engine.get_all()`）、`last_sync_time`、
   `get_token`（``callbacks`` 里那个 `get_jdz_token`）。
3. **编排**：`sync_with_engine` / `push_sync` / `reset_cloud` / `reset_local`。
   原文的 ``threading.Thread`` 与 ``self.after(0, ...)`` **留界面** ——
   服务不自己起线程。
4. **解锁载荷**：`unlock_notice` 把引擎回调的 ``(ach_def, stage, stage_name)``
   归一成 `UnlockNotice`，QML 侧不必再知道 `AchievementDef` 的内部结构。

## 留在界面（不搬）的部分

控件创建与布局、主题色登记（`_theme_refs`）、成就卡片渲染、
**三次确认对话框**（`_triple_confirm`：① 按钮 ② `askyesno` ③ 输入确认短语）、
未登录提示框、Toast 通知（`show_toast_notification`）、`after` 调度、线程启动、
以及全部 `_("...")` 文案。

## 离线可测性

======================  ==========================================
注入参数                 对应的真实实现
======================  ==========================================
``engine_getter``       ``services.achievement_engine.get_achievement_engine``
``sync_runner``         ``services.achievement_sync.run_sync``
``cloud_resetter``      ``services.achievement_sync.reset_cloud_db``
``clock``               ``time.time``
======================  ==========================================

默认值就是真实实现，行为不变；测试里换成替身即可完全不联网、不开窗口。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.base import Service

# ═══════════════════════════════════════════════════════════════════
# 结果类型（界面据此选文案，服务不碰 i18n）
# ═══════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class AchievementSummary:
    """成就总览（原 `_render_achievement_data` 里那两行统计的产物）。

    ``percent`` 就是原文的 ``pct``：``round(unlocked / total * 100, 1)``，
    ``total`` 为 0 时是整数 0（原文写的是 ``0`` 而不是 ``0.0``）。
    界面用 ``percent / 100`` 直接喂进度条（原文如此）。
    """

    total: int
    unlocked: int
    percent: float


@dataclass(frozen=True)
class UnlockNotice:
    """一次"某成就升到了某阶段"的通知载荷。

    "**哪些**成就新解锁了"这个判定本身在引擎里（`services/achievement_engine.py`
    的 `update_progress` 里那段 ``unlocked_new_stages``，逐字保留不动）；服务
    这一层负责把"引擎说是哪一项、第几阶段"翻译成界面渲染 Toast 需要的字段。

    记录现状：``achievement_id`` 在旧 Tk 界面里**没有被用到**（Toast 只吃
    ``icon`` / ``i18n_key`` / ``stage_name``）。保留它是因为 QML 侧的解锁
    通知要能回指到具体成就（点 Toast 跳到那一项），且
    `tests/test_achievement_service.py` 用它断言"是**哪一项**成就"。
    """

    achievement_id: str
    icon: str
    i18n_key: str
    stage: int
    stage_name: str


# ═══════════════════════════════════════════════════════════════════
# 纯逻辑（逐字搬出，零改写）
# ═══════════════════════════════════════════════════════════════════


def summarize(data: List[Dict[str, Any]]) -> AchievementSummary:
    """原 `_render_achievement_data` 的统计段（`:141-154`）逐字搬出。

    原文是"先遍历一遍数 total/unlocked、再遍历一遍渲染"的**两遍结构**，
    这里只搬第一遍，第二遍（渲染）留在界面 —— 两遍的相对顺序不变。

    记录现状：原文把 ``total`` 与 ``unlocked`` 当**成就项**计数
    （``progress_stage > 0`` 即"已解锁"），而不是按阶段数加权；
    这里照抄，不改口径。
    """
    total = 0
    unlocked = 0

    for cat_data in data:
        ach_list = cat_data["achievements"]
        for a in ach_list:
            total += 1
            if a["progress_stage"] > 0:
                unlocked += 1

    percent = round(unlocked / total * 100, 1) if total > 0 else 0
    return AchievementSummary(total=total, unlocked=unlocked, percent=percent)


def category_counts(achievements: List[Dict[str, Any]]) -> Tuple[int, int]:
    """原 `_render_category_section` 的两行（`:180-186`）逐字搬出。

    Returns:
        ``(已解锁数, 总数)`` —— 界面把它渲染成 ``f"{unlocked}/{total}"``。
    """
    unlocked = sum(1 for a in achievements if a["progress_stage"] > 0)
    return unlocked, len(achievements)


def format_sync_time(ts: float) -> str:
    """原 `_update_ach_last_sync_label` 里的 ``time.strftime(...)``（`:347`）逐字搬出。"""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def unlock_notice(ach_def: Any, stage: int, stage_name: str) -> UnlockNotice:
    """原 `_on_achievement_unlock` 里对 ``ach_def`` 的取值（`:510`）归一化。

    记录现状：原文是在 ``after(100, _do_toast)`` **之后**才读
    ``ach_def.icon`` / ``ach_def.i18n_key``；这里提前到调用点读一次。
    两者都是同一批不可变的 `AchievementDef` 冻结语义（定义表是模块级常量），
    读值时机不影响结果；提前读的好处是"载荷不合法"会落在引擎
    `_notify_unlock` 的 ``except`` 里（能留下日志），而不是被 Tk 的
    ``after`` 回调静默吞掉。

    **不做 getattr 兜底**：原文是直接属性访问，缺失就该抛 AttributeError，
    兜底成空串会让 Toast 显示成空文案（更糟）。
    """
    return UnlockNotice(
        achievement_id=ach_def.achievement_id,
        icon=ach_def.icon,
        i18n_key=ach_def.i18n_key,
        stage=stage,
        stage_name=stage_name,
    )


# ═══════════════════════════════════════════════════════════════════
# 服务对象
# ═══════════════════════════════════════════════════════════════════


class AchievementService(Service):
    """成就业务服务：进度数据准备、同步 / 重置编排、解锁载荷归一化。

    约定（与 `services/agent_service.py`、`services/resource_service.py` 一致）：

    - 构造期只做赋值，不读盘、不连网、不建线程；可脱离 `AppContext` 单独
      实例化（``AchievementService()`` 在 pytest 里就能用）。
    - **保留引擎的全局单例语义**：方法直接转调
      `services.achievement_engine.get_achievement_engine()`，没有把引擎
      改成实例级状态 —— 改掉会让"启动期 splash 初始化的引擎"与"界面拿到的
      引擎"不是同一个对象（引擎里那份 SQLite 连接与解锁回调都会失联）。
    - **异常不翻译**：底层异常原样抛出，由界面决定怎么提示。
    - 服务**不自己起线程**：`sync_with_engine` / `reset_cloud` / `push_sync`
      都是同步调用，调用方（界面）负责丢进 worker 线程并 ``after`` 回主线程。
    """

    name = "achievement"
    label = "成就"

    def __init__(
        self,
        context: Any = None,
        *,
        engine_getter: Optional[Callable[[], Any]] = None,
        sync_runner: Optional[Callable[..., bool]] = None,
        cloud_resetter: Optional[Callable[[str], bool]] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """构造期只保存注入项；``None`` 一律表示"用真实实现"（惰性 import）。

        惰性 import 是**刻意**的：原文这些 import 全都写在函数体/内层函数里
        （`from achievement_sync import run_sync` 甚至写在后台线程的闭包里），
        提到模块顶部会改变启动期的导入顺序与代价（`achievement_sync` 会连带
        拉入 `requests`）。
        """
        super().__init__(context)
        self._engine_getter = engine_getter
        self._sync_runner_override = sync_runner
        self._cloud_resetter_override = cloud_resetter
        self._clock_override = clock

    # ─── 惰性依赖解析（私有：这是注入管道，不是业务 API）────

    def engine(self):
        """全局成就引擎单例（未初始化时 ``None``）。

        原文各处都写成函数体内的 ``from achievement_engine import
        get_achievement_engine``；那个根模块现在是 `services.achievement_engine`
        的转发 shim（`scripts/relocate_module.py` 的 MOVES 登记项），
        ``get_achievement_engine`` 是**同一个函数对象**，所以直接从服务层取
        少一跳、行为不变。
        """
        if self._engine_getter is not None:
            return self._engine_getter()
        from services.achievement_engine import get_achievement_engine

        return get_achievement_engine()

    def _runner(self) -> Callable[..., bool]:
        """``achievement_sync.run_sync``（下载 → 合并 → 上传）。"""
        if self._sync_runner_override is not None:
            return self._sync_runner_override
        from services.achievement_sync import run_sync

        return run_sync

    def _resetter(self) -> Callable[[str], bool]:
        """``achievement_sync.reset_cloud_db``（上传一份空库）。"""
        if self._cloud_resetter_override is not None:
            return self._cloud_resetter_override
        from services.achievement_sync import reset_cloud_db

        return reset_cloud_db

    def _now(self) -> float:
        """当前时间戳（可注入，便于断言写进引擎的那个值）。"""
        if self._clock_override is not None:
            return self._clock_override()
        return time.time()

    def _db_path(self, engine: Any):
        """取引擎的数据库路径。

        记录现状：`AchievementEngine` 没有公开的路径访问器，`run_sync`
        又必须拿到 ``db_path``，所以原文（以及这里）读的都是私有属性
        ``engine._db_path``。给引擎加一个公开属性属于引擎侧的改动，
        不在本轮范围内。
        """
        return engine._db_path

    # ─── 进度数据准备（供界面直接渲染）──────────────────────

    def load_progress(self) -> Optional[List[Dict[str, Any]]]:
        """原 `_refresh_achievements` 的取值部分（`:123-131`）。

        引擎未初始化时返回 ``None`` —— 原文此时直接 ``return``，既不渲染
        也不刷新"上次同步时间"标签。界面据此提前返回，与原文一致。
        """
        engine = self.engine()
        if engine is None:
            return None
        return engine.get_all()

    def summarize(self, data: List[Dict[str, Any]]) -> AchievementSummary:
        """总览统计（原 `_render_achievement_data` 的统计段）。"""
        return summarize(data)

    def category_counts(
        self, achievements: List[Dict[str, Any]]
    ) -> Tuple[int, int]:
        """单个分类的 ``(已解锁数, 总数)``（原 `_render_category_section`）。"""
        return category_counts(achievements)

    def last_sync_time(self) -> Optional[float]:
        """上次成功同步的时间戳（没同步过或无引擎时 ``None``）。"""
        engine = self.engine()
        if engine is None:
            return None
        return engine.get_last_sync_time()

    def format_sync_time(self, ts: float) -> str:
        """时间戳 → ``%Y-%m-%d %H:%M:%S``（原 `_update_ach_last_sync_label`）。"""
        return format_sync_time(ts)

    def unlock_notice(self, ach_def: Any, stage: int, stage_name: str) -> UnlockNotice:
        """把引擎回调的参数归一成界面可用的载荷（原 `_on_achievement_unlock`）。"""
        return unlock_notice(ach_def, stage, stage_name)

    # ─── Token ──────────────────────────────────────────────

    def get_token(self, callbacks: Dict[str, Callable]) -> str:
        """原 `_get_ach_token`（`:282-287`）逐字搬出。

        先 ``in`` 判断再硬下标 —— 这个写法一字不改：`scripts/check_callback_keys.py`
        正是靠它把这次访问降级成 ``soft(守卫)``（键缺失时是"功能静默降级"
        而不是 KeyError）。
        """
        if "get_jdz_token" in callbacks:
            token = callbacks["get_jdz_token"]()
            return token or ""
        return ""

    # ─── 同步 / 重置编排 ────────────────────────────────────

    def sync_with_engine(self, engine: Any, token: str) -> bool:
        """跑一次完整同步（原 `_on_ach_sync` 里后台线程的闭包体，`:311-316`）。

        原文把 ``engine`` 一起传给 ``run_sync``，让所有数据库操作走
        ``engine._lock`` 串行化、并在成功后由 ``run_sync`` 自己写
        ``last_sync_time`` —— 两个参数都照抄，行为不变。

        **必须在 worker 线程里调用**（会阻塞在 HTTP 上）；线程由界面起。
        """
        runner = self._runner()
        return runner(token, self._db_path(engine), engine=engine)

    def push_sync(self, token: str) -> bool:
        """解锁后的"顺手推一次"（原 `_on_achievement_unlock` 里 ``_push_sync``，`:517-527`）。

        与 :meth:`sync_with_engine` 有**两处刻意的不同**，都是照抄原文：

        1. 自己取引擎（取不到就直接结束，不报错）；
        2. ``run_sync`` **不传** ``engine`` —— 于是不走 ``engine._lock``，
           也**不由 run_sync 写 last_sync_time**，改成成功后自己写
           ``time.time()``。

        Returns:
            是否同步成功。界面据此决定要不要刷新"上次同步时间"标签
            （原文只在成功后 ``after(0, self._update_ach_last_sync_label)``）。
        """
        engine = self.engine()
        if not engine:
            return False
        runner = self._runner()
        ok = runner(token, self._db_path(engine))
        if ok:
            engine.set_last_sync_time(self._now())
        return bool(ok)

    def reset_cloud(self, token: str) -> bool:
        """重置云存档（原 `_do_reset_cloud` 里后台线程的那一步，`:374-379`）。

        **必须在 worker 线程里调用**（会发 HTTP）。
        """
        resetter = self._resetter()
        return resetter(token)

    def reset_local(self) -> Optional[bool]:
        """重置本地全部成就进度（原 `_do_reset_local`，`:392-399`）。

        记录现状、疑为缺陷：原文**不管有没有引擎**都会把状态栏写成"重置成功"
        （``_set_sync_status(_("ach_reset_local_success"))`` 写在 ``if engine``
        之外）。本方法把"到底有没有真的重置"如实返回（无引擎 → ``None``），
        但**不改**界面那句无条件成功提示 —— 那是既有行为，改它属于另一个
        裁决（要连带改文案与用户可见语义），不在本轮"只搬不改"的范围里。
        """
        engine = self.engine()
        if engine:
            return engine.reset_all()
        return None

    # ─── 解锁通知：引擎已提供、旧界面没接的 pull 路径 ───────

    def pending_unlocks(self) -> List[Dict[str, Any]]:
        """"已解锁但还没通知过"的记录（`AchievementEngine.get_unnotified_unlocks`）。

        记录现状：这条路在**旧 Tk 界面里没有任何调用方** —— 界面走的是引擎的
        push 回调（`main.py:393` 把 `_on_achievement_unlock` 注册进引擎）。
        引擎侧的 pull API（``get_unnotified_unlocks`` / ``mark_notified`` /
        ``batch_mark_notified``）早就写好了，但全仓无人用。

        这里把它包出来是给 QML 侧用的："打开成就页时补齐这段时间漏掉的
        toast"。本轮**不**改旧界面的接法（那会改变用户可见行为），
        `tests/test_achievement_service.py` 直接调本方法验证它与引擎一致。
        引擎未初始化时返回空列表。
        """
        engine = self.engine()
        if engine is None:
            return []
        return engine.get_unnotified_unlocks()

    def ack_unlocks(self, unlock_ids: List[int]) -> None:
        """把若干解锁记录标记为"已通知"（`AchievementEngine.batch_mark_notified`）。

        与 :meth:`pending_unlocks` 配套；同样**旧界面没有调用方**（见那里的
        说明）。引擎未初始化时什么都不做。
        """
        engine = self.engine()
        if engine is None:
            return
        engine.batch_mark_notified(unlock_ids)


__all__ = [
    "AchievementService",
    "AchievementSummary",
    "UnlockNotice",
    "category_counts",
    "format_sync_time",
    "summarize",
    "unlock_notice",
]
