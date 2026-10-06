"""账号桥 —— QML 侧的 `Accounts` 上下文属性（阶段 3 任务 3.5，对照表 M-13 / M-26 ~ M-29）。

## 它在链路的哪一环

    qml/pages/settings/AccountSection.qml
        ↓ 取属性 / 调槽
    app/bridges/accounts_bridge.py（本模块，**零业务规则**）
        ↓ 转发
    services/account_service.py（登录 / 删除 / 导入导出 / 刷新 / 取消的全部规则）
        ↓ 委托
    launcher/account.py（GlobalAccountSystem）

桥只做三件事：**把服务的返回值变成能绑的属性**、**把 QML 的动作转成服务调用**、
**把服务的进度回调变成 Qt 信号**。参数该校验、取消该不该算失败、重名要不要拦，
一条都不在这里判（迁移红线 2）。

## 三处接线（`bind()` + `use_engine()`）

1. **服务的进度回调 → Qt 信号**：登录/刷新的工作线程调 `onProgress`，这里立刻
   `TaskBridge` 那套同款手法 —— 用 `QMetaObject.invokeMethod(..., QueuedConnection)`
   把记录丢回主线程，由 `_deliver()` 在主线程上刷新快照并发 `accountsChanged` /
   `progressChanged`。**worker 绝不碰 QML**（契约红线 3）。
2. **账号变更 → 首页卡片**（M-13 的"侧边栏同步"在新架构里的落点）：本桥在服务报出
   终态时发 `accountsChanged`，并**转调** `Home.refresh()`。方向是单向的
   （账号 → 首页），所以不存在两个桥互相拉扯；"账号数据"的唯一所有者仍是
   `AccountService`（D-187 的同一原则）。Home 缺席（单测里）只记日志。
3. **状态条**：`statusMessage(text, level)` 由页面转给 `Shell.setStatus`，
   与 `SettingsBridge` 完全一致（桥不直接碰壳层，页面才知道自己在哪一页）。

## 对话框：为什么真值留在桥里

旧窗口用 `CTkInputDialog` 模态取密码（`account_manager.py:526-557`）。QML 侧没有
"就地等一下再返回"的模态输入，所以导入/导出走**声明式状态机**：

    requestImport()  → importPasswordRequested()  → 页面弹一次密码框
                     → setImportPassword(pw)      → importFileRequested() → 页面弹文件框
                     → submitImport(path)         → 服务

    requestExport()  → exportPasswordRequested() → 页面弹密码框（设密码）
                     → setExportPassword(pw)      → exportPasswordConfirmRequested()
                                                  → 页面**再弹一次**（核对；旧实现同）
                     → confirmExportPassword(pw)  → 一致：exportFileRequested() → 文件框
                                                  → 不一致：passwordMismatch(旧文案)
                                                    + 退回第一步重输
                     → submitExportPath(path)     → exported(path) → 结果框（可「打开目录」）

密码**只留在桥的 Python 属性里**（QML 只回传明文，不自己存），拿到就用、出错就报。
导出的两步核对与旧实现 `account_manager.py:547-560` 一一对应：同样的两次问答、同样的
`account_export_password_mismatch` 文案；差别只在"不一致时退回首步重输"而不是整段放弃。

## 与旧实现的差异（详见对照表 M-13 / M-26 ~ M-29 与 `16` §十九）

* **取消**：旧窗口登录期间只把三个按钮置灰、最长干等 180 秒，没有取消按钮；
  新界面给 `cancel()`（本轮用户裁决"真取消"，核心层加了协作式取消口）。
* **导出成功**多一颗「打开所在目录」（用户 2026-10-06 裁决）。
* **密码框**全部走掩码（`TextInputDialog` 的 `password=true`，M-Q6 的既有裁决）。
* **两次密码不一致**：旧实现是"弹警告 + 整段放弃"，新实现是"旧文案就地显示 + 退回第 1 步重输"
  （判定与文案逐字相同，只有"要不要从头再来"这一处不同）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from services.account_service import ACCOUNT_FILE_FILTER, ACCOUNT_FILE_SUFFIX
from services.i18n_service import _

logger = logging.getLogger("app.bridges.accounts_bridge")

#: 契约第四节冻结的 QML 注册名（入口 `main_qml.CONTEXT_BRIDGES` 注册成上下文属性）。
QML_NAME = "Accounts"

#: 离开守卫用的域 id（与路由前缀一致）。
DOMAIN = "settings/account"


class AccountsBridge(QObject):
    """上下文属性 `Accounts`。"""

    #: 账号列表 / 当前账号变了（列表、当前标记、首页卡片都看它）。
    accountsChanged = Signal()
    #: 有没有登录/刷新在进行（按钮禁用态、进度条可见性）。
    busyChanged = Signal()
    #: 最近一条提示变了（`lastNotice`）。
    noticeChanged = Signal()
    #: 进度（已翻译的 i18n 键或核心层原样透出的中文状态串 + 计数）。
    progressChanged = Signal()
    #: 要求确认删除（参数 = 账号 id / 名字；页面弹 `Dialogs` 确认框）。
    deleteRequested = Signal(str, str)
    #: 要求确认刷新 Token（逐账号那颗按钮；参数 = 账号 id / 名字）。
    refreshTokenRequested = Signal(str, str)
    #: 状态条文案（**已翻译**；页面转给 `Shell.setStatus`）。
    statusMessage = Signal(str, str)
    #: 一条提示（导入成功/失败、重名提醒…）—— 页面用 `Dialogs.notify` 弹 Toast。
    noticeRequested = Signal(str, str)
    #: 导出成功的结果对话框（参数 = 文件路径；页面弹 `ExportResultDialog`）。
    exported = Signal(str)
    #: **内部投递信号**（见 `_on_progress`）：worker 线程只排队并 emit 它，
    #: 真正落属性/发信号的是主线程上的 `_deliver()`。
    progressPosted = Signal()
    #: 导出两步密码不一致（参数 = 旧文案 `account_export_password_mismatch`）。
    #: 页面把它存进 `exportPasswordError`；紧接着会再收到一次
    #: `exportPasswordRequested`（退回第 1 步重输）。
    passwordMismatch = Signal(str)
    #: 导入/导出的声明式对话框请求（无参数，页面自己读桥上的 prompt 属性）。
    importPasswordRequested = Signal()
    importFileRequested = Signal()
    exportPasswordRequested = Signal()
    exportPasswordConfirmRequested = Signal()
    exportFileRequested = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._service: Any = None
        self._home: Any = None
        self._accounts: List[Dict[str, Any]] = []
        self._current_id = ""
        self._busy = False
        self._progress: Dict[str, Any] = {}
        #: 最近一条提示（`noticeRequested` 的内容）。**给测试一条可读的判据** ——
        #: 否则"导入成功了但状态栏还没来得及更新"这类时序问题只能靠 sleep 猜。
        self._last_notice: Dict[str, Any] = {}

        # ── 声明式对话框的真值（全部在 Python 侧，QML 只回传明文）──
        self._import_password: str = ""
        self._import_stage: str = ""
        self._export_password: str = ""
        self._export_stage: str = ""
        self._export_path: str = ""

        #: 进度记录**只在工作线程写、主线程读**，所以先排队再投递（见模块文档第 1 条）。
        self._pending: List[Dict[str, Any]] = []
        self._pending_lock = threading.Lock()
        #: worker → 主线程的投递口。**用信号而不是 `QMetaObject.invokeMethod`**：
        #: 契约第六节决策 10（`scripts/check_qml_rules.py` 的 R7）禁止后者 —— 它的
        #: var 参数签名不匹配会**静默失败**；信号跨线程是队列投递，失败会报在日志里。
        self.progressPosted.connect(self._deliver)

    # ─── 装配期注入 ─────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        """`main_qml.register_bridges` 在注册前调它：取账号服务并接上进度回调。"""
        self._service = self._service_of(context, "account")
        if self._service is None:
            logger.warning("账号服务不可用：账号分区只会显示错误态")
            return
        try:
            self._service.add_listener(self._on_progress)
        except Exception as e:  # noqa: BLE001 - 挂不上监听只是没有进度，页面仍可用
            logger.warning("订阅账号进度失败: %s", e)
        self.refresh()

    def use_engine(self, engine: Any) -> None:
        """接上 `Home` 桥（账号变更后刷新首页卡片）。"""
        registry = getattr(engine, "_fmcl_bridges", None) or {}
        home = registry.get("Home")
        if home is not None:
            self._home = home

    def detach(self) -> None:
        """注销进度监听（退出/重建引擎时调，避免回调打到已销毁的对象上）。"""
        if self._service is None:
            return
        try:
            self._service.remove_listener(self._on_progress)
        except Exception as e:  # noqa: BLE001
            logger.debug("注销账号进度监听失败: %s", e)

    @staticmethod
    def _service_of(context: Any, name: str) -> Any:
        if context is None:
            return None
        try:
            return context.try_get(name)
        except Exception as e:  # noqa: BLE001
            logger.warning("账号页取服务 %s 失败: %s", name, e)
            return None

    # ─── 只读属性 ───────────────────────────────────────────

    @Property(bool, constant=True)
    def available(self) -> bool:
        """服务在不在（缺席时页面走错误态，而不是绑一堆 undefined）。"""
        return self._service is not None

    @Property("QVariantList", notify=accountsChanged)
    def accounts(self) -> List[Dict[str, Any]]:  # noqa: N802
        """账号列表。每项：`id` `name` `type` `type_key` `uuid` `uuid_short` `current`
        `display_name`（当前账号带 `★` 前缀，与旧窗口 `account_manager.py:372` 同款）。"""
        return self._accounts

    @Property(int, notify=accountsChanged)
    def count(self) -> int:  # noqa: N802
        return len(self._accounts)

    @Property(bool, notify=accountsChanged)
    def hasAccounts(self) -> bool:  # noqa: N802
        return bool(self._accounts)

    @Property(str, notify=accountsChanged)
    def currentId(self) -> str:  # noqa: N802
        return self._current_id

    @Property(str, notify=accountsChanged)
    def currentName(self) -> str:  # noqa: N802
        for account in self._accounts:
            if account["current"]:
                return str(account["name"])
        return ""

    @Property(str, notify=accountsChanged)
    def currentTypeKey(self) -> str:  # noqa: N802
        for account in self._accounts:
            if account["current"]:
                return str(account["type_key"])
        return ""

    @Property(bool, notify=busyChanged)
    def busy(self) -> bool:
        """有登录/刷新在进行（页面据此禁用"添加"与"全部刷新"）。"""
        return self._busy

    @Property("QVariantMap", notify=progressChanged)
    def progress(self) -> Dict[str, Any]:
        """最近一条进度：`kind` `phase` `message` `name` `current` `total` `cancelled`。"""
        return self._progress

    @Property(bool, notify=progressChanged)
    def progressVisible(self) -> bool:  # noqa: N802
        return self._busy

    @Property(bool, notify=progressChanged)
    def cancellable(self) -> bool:  # noqa: N802
        """进度条旁边那颗「取消」是否可用（只在真能取消时有意义）。"""
        return self._busy and str(self._progress.get("kind", "")) in ("microsoft", "yggdrasil", "refresh_all")

    @Property("QVariantMap", notify=noticeChanged)
    def lastNotice(self) -> Dict[str, Any]:  # noqa: N802
        """最近一条提示：`{"message", "level"}`（空 dict = 还没提示过）。"""
        return self._last_notice

    @Property(int, notify=progressChanged)
    def progressCurrent(self) -> int:  # noqa: N802
        return int(self._progress.get("current", 0) or 0)

    @Property(int, notify=progressChanged)
    def progressTotal(self) -> int:  # noqa: N802
        return int(self._progress.get("total", 0) or 0)

    #: 导出结果对话框的文案键（页面用 `Tr.map` 翻）
    @Property(str, constant=True)
    def exportTitleKey(self) -> str:  # noqa: N802
        return "account_export_title"

    @Property(str, constant=True)
    def exportSuccessKey(self) -> str:  # noqa: N802
        return "account_export_success"

    @Property(str, constant=True)
    def exportFailedKey(self) -> str:  # noqa: N802
        return "account_export_failed"

    @Property(str, constant=True)
    def openFolderKey(self) -> str:  # noqa: N802
        """「打开所在目录」按钮文案 —— 复用 3.4 已在用的 `version_open_folder`。"""
        return "version_open_folder"

    @Property(str, constant=True)
    def fileFilter(self) -> str:  # noqa: N802
        """文件对话框过滤器（旧 `("FMCL Accounts", "*.fmcl_accounts")`）。"""
        return ACCOUNT_FILE_FILTER

    @Property(str, constant=True)
    def fileSuffix(self) -> str:  # noqa: N802
        return ACCOUNT_FILE_SUFFIX

    # ─── 绑定/刷新 ──────────────────────────────────────────

    @Slot()
    def refresh(self) -> None:
        """从服务重读列表与当前账号（槽：页面 `Component.onCompleted` 与重试都调它）。"""
        if self._service is None:
            self._accounts = []
            self._current_id = ""
            self.accountsChanged.emit()
            return
        try:
            current = self._service.current_account()
            current_id = str((current or {}).get("id", ""))
            rows = []
            for account in self._service.accounts():
                row = dict(account)
                row["current"] = (row["id"] == current_id and current_id != "")
                row["uuid_short"] = (str(row.get("uuid") or "")[:20] + "...") if row.get("uuid") else "-"
                name = str(row.get("name", ""))
                row["display_name"] = ("\u2605 " + name) if row["current"] else name
                rows.append(row)
            self._accounts = rows
            self._current_id = current_id
            self.accountsChanged.emit()
        except Exception as e:  # noqa: BLE001 - 列表刷不出来不该让页面崩
            logger.error("刷新账号列表失败: %s", e)

    def _refresh_home(self) -> None:
        """账号变更后刷新首页卡片。**单向**：账号 → 首页，反向没有连线。"""
        home = self._home
        if home is None:
            return
        try:
            home.refresh()
        except Exception as e:  # noqa: BLE001 - 首页刷不动不影响账号页
            logger.warning("刷新首页账号卡片失败: %s", e)

    # ─── 进度：worker 线程 → 主线程 ─────────────────────────

    def _on_progress(self, record: Dict[str, Any]) -> None:
        """服务的进度回调。**运行在工作线程上**，所以只排队 + 投递，不碰任何属性。

        `progressPosted` 的接收方 `_deliver` 住在主线程（本对象属于主线程），
        跨线程 emit 会走队列投递 —— 这是契约允许的"worker 绝不碰 QML"的实现方式。
        """
        try:
            with self._pending_lock:
                self._pending.append(dict(record))
            self.progressPosted.emit()
        except Exception as e:  # noqa: BLE001 - 进度丢了也不能让登录线程崩
            logger.debug("投递账号进度失败: %s", e)

    @Slot()
    def _deliver(self) -> None:
        """主线程：把排队的进度记录逐条落成属性 + 信号。"""
        with self._pending_lock:
            records = self._pending
            self._pending = []
        for record in records:
            self._apply_progress(record)

    def _apply_progress(self, record: Dict[str, Any]) -> None:
        phase = str(record.get("phase", ""))
        kind = str(record.get("kind", ""))
        self._progress = self._translated(record)
        if phase == "running":
            if not self._busy:
                self._busy = True
                self.busyChanged.emit()
            self.progressChanged.emit()
            return

        # ── 终态 ──
        self._busy = False
        self.busyChanged.emit()
        self.progressChanged.emit()
        self.refresh()
        if kind == "refresh_all":
            self._report_refresh_all(record)
            return
        self._report_login(record)

    # ─── 报告 ───────────────────────────────────────────────

    def _notify(self, message: str, level: str) -> None:
        """记一条提示并广播（页面转成 Toast）。"""
        self._last_notice = {"message": str(message), "level": str(level)}
        self.noticeChanged.emit()
        self.noticeRequested.emit(str(message), str(level))

    def _status(self, text: str, level: str) -> None:
        self.statusMessage.emit(str(text), str(level))

    @staticmethod
    def _translated(record: Dict[str, Any]) -> Dict[str, Any]:
        """把 `message_key` 翻成当前语言，填进 `message`。

        服务层的进度记录里，`message` 是**已经成句的中文**（核心层那批，原样透出），
        而 `message_key` 是**界面要翻译的 i18n 键**（登录刚开始时那句占位）。
        两者在这里合并成同一个 `message` 字段给 QML —— 页面只显示、不判断
        （2026-10-06 用户验收报的"`account_ms_verifying` 直接显示了出来"，
        根因就是当时两边共用 `message` 字段、页面把键名当文案印了）。
        """
        data = dict(record)
        key = str(data.get("message_key", "") or "")
        if key:
            data["message"] = _(key)
        data.pop("message_key", None)
        return data

    def _report_login(self, record: Dict[str, Any]) -> None:
        kind = str(record.get("kind", ""))
        if record.get("cancelled"):
            self._status(_("account_login_cancelled"), "warning")
            self._refresh_home()  # 取消也可能已经写进了账号（核心层先落盘再回报）
            return
        if record.get("ok"):
            self._status(_("logging_in") + " " + str(record.get("name", "")), "success")
            self._refresh_home()
            return
        # 失败：旧实现是 messagebox.showwarning(_("warning"), _("account_login_failed", type=...))
        self._status(_("account_login_failed", type=kind), "error")
        self._refresh_home()

    def _report_refresh_all(self, record: Dict[str, Any]) -> None:
        if record.get("cancelled"):
            self._status(_("account_refresh_all"), "warning")
            return
        if int(record.get("failed", 0) or 0) > 0:
            self._status(_("account_refresh_token_failed"), "error")
            return
        self._status(_("account_refresh_token_success"), "success")

    # ─── 槽：切换 / 删除 / 刷新 ─────────────────────────────

    @Slot(str)
    def setCurrent(self, accountId: str) -> None:  # noqa: N802
        """切换当前账号（M-28）。切成功后**立刻**刷新列表与首页卡片。"""
        if self._service is None:
            return
        if self._service.set_current_account(str(accountId or "")):
            self.refresh()
            self._refresh_home()
        else:
            self._status(_("account_set_current"), "error")

    @Slot(str)
    def requestDelete(self, accountId: str) -> None:  # noqa: N802
        """请页面弹确认框（D-183：确认走声明式属性 + 信号，不传 JS 回调）。"""
        name = ""
        for account in self._accounts:
            if account["id"] == str(accountId):
                name = str(account["name"])
                break
        self.deleteRequested.emit(str(accountId), name)

    @Slot(str)
    def confirmDelete(self, accountId: str) -> None:  # noqa: N802
        """确认删除（页面在用户点「确定」后调）。"""
        if self._service is None:
            return
        if self._service.remove_account(str(accountId or "")):
            self.refresh()
            self._refresh_home()
            self._status(_("account_delete"), "success")
        else:
            self._status(_("account_delete"), "error")

    @Slot(str)
    def requestRefreshToken(self, accountId: str) -> None:  # noqa: N802
        """逐账号刷新 Token：先确认再刷（旧实现直接刷，本轮给它一次确认）。"""
        name = ""
        for account in self._accounts:
            if account["id"] == str(accountId):
                name = str(account["name"])
                break
        self.refreshTokenRequested.emit(str(accountId), name)

    @Slot(str)
    def confirmRefreshToken(self, accountId: str) -> None:  # noqa: N802
        """真的去刷（一次 HTTP，同步调用；失败按旧文案报）。"""
        if self._service is None:
            return
        ok, _cancelled = self._service.refresh_token(str(accountId or ""))
        self.refresh()
        if ok:
            self._status(_("account_refresh_token_success"), "success")
        else:
            self._status(_("account_refresh_token_failed"), "error")

    # ─── 槽：登录（M-26）────────────────────────────────────

    @Slot(str, "QVariantMap")
    def startLogin(self, kind: str, params: Any = None) -> None:  # noqa: N802
        """开始一次登录（微软 / 离线 / 外置）。参数校验在**服务**里，这里只转发。

        校验失败时服务返回一个 i18n 键，这里按旧实现的观感弹警告（旧：`messagebox.showwarning`）。
        """
        if self._service is None:
            return
        data = dict(params or {})
        reason = self._service.start_login(str(kind or ""), data)
        if reason:
            #: `account_login_failed` 带 `{type}` 占位符 —— 这里必须把 kind 填进去，
            #: 否则状态栏会显示模板原文（`{type} 登录失败，请重试`）。工作线程那条失败路径
            #: 一直是带参数调的，两条路径的文案口径要一致。
            self._status(_(reason, type=str(kind or "")), "warning")
            return
        if str(kind) == "offline":
            name = str(data.get("name", "")).strip()
            if self._service.has_duplicate_name(name, "offline"):
                # 用户裁决：重名只提醒不拦截（旧实现连提醒都没有）
                self._notify(_("account_name_duplicate", name=name), "warning")
        self._busy = True
        self.busyChanged.emit()
        self.progressChanged.emit()

    @Slot()
    def cancel(self) -> None:
        """取消当前登录/刷新（本轮新增能力；真正的中断在核心层）。"""
        if self._service is None:
            return
        if not self._service.begin_cancel():
            return
        self._busy = False
        self.busyChanged.emit()
        self._status(_("account_login_cancelled"), "warning")

    @Slot(result=bool)
    def refreshAll(self) -> bool:  # noqa: N802
        """「全部刷新 Token」（A-25 在账号页的落点）。立刻返回，进度走 `progress`。"""
        if self._service is None:
            return False
        if not self._service.start_refresh_all():
            return False
        self._busy = True
        self.busyChanged.emit()
        self.progressChanged.emit()
        return True

    # ─── 槽：导入 / 导出（M-29）─────────────────────────────

    @Slot()
    def requestImport(self) -> None:  # noqa: N802
        """导入第一步：请页面弹第一个密码框（旧：`CTkInputDialog`）。"""
        self._import_stage = "password"
        self._import_password = ""
        self.importPasswordRequested.emit()

    @Slot(str)
    def setImportPassword(self, password: str) -> None:  # noqa: N802
        """用户填了导入密码（空串 = 取消，旧实现直接 return 什么都不做）。"""
        if self._import_stage != "password":
            return
        self._import_stage = ""
        text = str(password or "")
        if not text:
            return
        self._import_password = text
        self.importFileRequested.emit()

    @Slot(str)
    def submitImport(self, path: str) -> None:  # noqa: N802
        """用户选好了导入文件（空串 = 取消）。"""
        if self._service is None:
            return
        target = str(path or "")
        password, self._import_password = self._import_password, ""
        if not target:
            return
        result = self._service.import_accounts(password, target)
        if not result.get("ok"):
            key = str(result.get("error", ""))
            # 服务对"密码错 / 文件坏"给的是旧键；其余是系统错误串（旧实现弹 str(e)）
            self._notify(_(key) if "_" in key else key, "error")
            return
        self.refresh()
        self._refresh_home()
        self._notify(_("account_import_success", count=result.get("count", 0)), "success")

    @Slot()
    def requestExport(self) -> None:  # noqa: N802
        """导出第一步：请页面弹密码框（旧：第一个 `CTkInputDialog`）。"""
        self._export_stage = "password"
        self._export_password = ""
        self._export_path = ""
        self.exportPasswordRequested.emit()

    @Slot(str)
    def setExportPassword(self, password: str) -> None:  # noqa: N802
        """导出第一步的密码（空串 = 取消，旧实现直接 `return`）。

        对齐旧实现 `account_manager.py:547-560`：**先设密码、再输一遍核对**。
        两步都是"服务端的问答"，所以中间状态留在桥里（QML 只回传明文）。
        """
        if self._export_stage != "password":
            return
        text = str(password or "")
        if not text:
            self._export_stage = ""
            return
        self._export_password = text
        self._export_stage = "password_confirm"
        self.exportPasswordConfirmRequested.emit()

    @Slot(str)
    def confirmExportPassword(self, password: str) -> None:  # noqa: N802
        """导出第二步：核对两次输入；一致才进"选文件"。

        不一致时**退回首步重输**，并把旧文案 `account_export_password_mismatch`
        （"两次输入的密码不一致"）交给界面就地显示 —— 旧实现是弹一个警告框然后整段放弃，
        两者的判定与文案相同，差别只在"要不要从头再来"。
        """
        if self._export_stage != "password_confirm":
            return
        text = str(password or "")
        if not text:
            self._export_stage = ""
            self._export_password = ""
            return
        if text != self._export_password:
            self._export_password = ""
            self._export_stage = "password"
            #: **把旧文案随信号一起交给界面**，而不是让页面去问"这次是不是重试"。
            #: 第一版写过一个 `exportPasswordRetry` 只读属性（读一次就复位），
            #: 桥测试指出那是**读属性改状态** —— QML 的绑定可能随时重算，第二次读就是 False，
            #: 属于埋雷。信号是**推**的、只发生一次，语义干净。
            self.passwordMismatch.emit(_("account_export_password_mismatch"))
            self.exportPasswordRequested.emit()
            return
        self._export_stage = ""
        self.exportFileRequested.emit()

    @Slot(str)
    def submitExportPath(self, path: str) -> None:  # noqa: N802
        """用户选好了导出位置（空串 = 取消）。拿到路径后立刻导出。"""
        if self._service is None:
            return
        target = str(path or "")
        password = self._export_password
        if not target:
            self._export_password = ""
            return
        result = self._service.export_accounts(password, target)
        if not result.get("ok"):
            key = str(result.get("error", ""))
            self._notify(_(key) if "_" in key else key, "error")
            return
        self._export_path = str(result.get("path", target))
        self._export_password = ""
        self.exported.emit(self._export_path)

    @Slot()
    def openExportFolder(self) -> None:
        """导出结果对话框上的「打开所在目录」（本轮新增）。"""
        if self._service is None or not self._export_path:
            return
        ok, error = self._service.open_in_explorer(self._export_path)
        if not ok:
            self._notify(error or _("account_export_failed"), "error")


__all__ = ["DOMAIN", "QML_NAME", "AccountsBridge"]
