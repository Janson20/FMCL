"""`app/bridges/accounts_bridge.py` 的回归守卫（阶段 3 任务 3.5）。

桥只有三件事要做对：**转发**（槽 → `AccountService`，业务规则一条都不在桥里）、**映射**
（服务快照 → `current` / `uuid_short` / `display_name`，i18n 键 → 当前语言的一句话）、**跨线程**
（进度回调在工作线程上，桥只排队 + `progressPosted` 投递，由主线程 `_deliver()` 落属性；用例用
`processEvents()` 驱动投递，不 sleep 猜时序）。

替身是"能跑真流程的最小账号系统"（同 `tests/test_account_manager_service.py`），界面由 QML 探针管。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtGui import QGuiApplication  # noqa: E402

from app.bridges import accounts_bridge as bridge_module  # noqa: E402
from app.bridges.accounts_bridge import AccountsBridge  # noqa: E402
from app.context import AppContext  # noqa: E402
from services.account_service import (  # noqa: E402
    ACCOUNT_FILE_FILTER,
    ACCOUNT_FILE_SUFFIX,
    PHASE_CANCELLED,
    PHASE_LOGIN_OK,
    PHASE_RUNNING,
    AccountService,
)

#: 进程内唯一的 QGuiApplication（offscreen，不在 import PySide6 之后另建）
_APP = QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture(autouse=True, scope="module")
def _chinese_locale() -> Any:
    """整个模块用中文语言文件：否则 `_()` 返回键名，"翻好了没有"就没法断言。"""
    from services import i18n_service

    saved_lang = i18n_service.get_current_language()
    saved = dict(i18n_service._translations)
    i18n_service.init_i18n("zh_CN")
    yield
    i18n_service._translations.clear()
    i18n_service._translations.update(saved)
    i18n_service._current_language = saved_lang


def pump(condition: Callable[[], bool], timeout_s: float = 5.0) -> bool:
    """驱动主线程事件循环直到条件成立（跨线程投递没有它就永远落不了地）。"""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline and not condition():
        _APP.processEvents()
        time.sleep(0.005)
    _APP.processEvents()
    return condition()


def emit_from_worker(service: Any, **record: Any) -> None:
    """在**工作线程**上把一条记录推给服务的监听器（桥的 worker 路径）；直接调 _emit 才能控制内容。"""
    thread = threading.Thread(target=lambda: service._emit(**record), daemon=True)
    thread.start()
    thread.join(2.0)
    assert not thread.is_alive(), "工作线程没有在预算时间内结束"


def fake_account(name: str = "Steve", kind: str = "offline", uuid: Optional[str] = None) -> Any:
    """核心层 `Account` 的最小替身（只带服务与桥读得到的字段）。"""
    return SimpleNamespace(id=f"id-{name}", name=name, uuid=uuid if uuid is not None else f"uuid-{name}",
                           refresh_token="refresh" if kind == "microsoft" else None,
                           account_type=SimpleNamespace(value=kind))


class FakeSystem:
    """最小可用的账号系统：登录/刷新尊重 `cancel_event`，增删与导入导出按真语义记账。"""

    #: 导出文件魔数（导入时校验）；下面这些是失败开关，用例按需改成实例属性
    MAGIC = b"FMCL_ACCOUNTS_V1\n"
    ms_login_fail, ms_login_delay, refresh_ok = False, 0.0, True
    export_expected, import_count = "", 2

    def __init__(self, accounts: Optional[List[Any]] = None, current: Any = None) -> None:
        self.accounts: List[Any] = list(accounts or [])
        self.current_account = current
        self.switched: List[str] = []
        self.removed: List[str] = []
        self.login_calls: List[Dict[str, Any]] = []
        self.refresh_calls: List[str] = []
        self.refresh_all_report: List[Tuple[str, int, int]] = []

    def get_account(self, account_id: str) -> Any:
        return next((a for a in self.accounts if a.id == account_id), None)

    def set_current_account(self, account_id: str) -> bool:
        self.switched.append(account_id)
        account = self.get_account(account_id)
        self.current_account = account or self.current_account
        return account is not None

    def remove_account(self, account_id: str) -> bool:
        account = self.get_account(account_id)
        if account is None:
            return False
        self.accounts.remove(account)
        self.removed.append(account_id)
        self.current_account = None if self.current_account is account else self.current_account
        return True

    def microsoft_login(self, status_callback: Any = None, *, cancel_event: Any = None) -> Any:
        self.login_calls.append({"kind": "microsoft"})
        if status_callback:
            status_callback("正在打开浏览器...")
        if cancel_event is not None and (cancel_event.wait(self.ms_login_delay) or cancel_event.is_set()):
            return None
        return None if self.ms_login_fail else self._born("MSPlayer", "microsoft")

    def offline_login(self, name: str) -> Any:
        self.login_calls.append({"kind": "offline", "name": name})
        return self._born(name, "offline")

    def yggdrasil_login(self, server_url: str, username: str, password: str, status_callback: Any = None,
                        *, cancel_event: Any = None) -> Any:
        self.login_calls.append({"kind": "yggdrasil", "username": username})
        return self._born(username, "yggdrasil")

    def _born(self, name: str, kind: str) -> Any:
        account = fake_account(name, kind)
        self.accounts.append(account)
        return account

    def refresh_account_token(self, account: Any, *, cancel_event: Any = None) -> bool:
        self.refresh_calls.append(account.id)
        return self.refresh_ok

    def refresh_all_account_tokens(self, on_account: Any = None, *, cancel_event: Any = None) -> Dict[str, Any]:
        targets = [a for a in self.accounts if a.account_type.value == "microsoft" and a.refresh_token]
        for index, account in enumerate(targets, start=1):
            if on_account:
                on_account(account.name, index, len(targets))
            self.refresh_all_report.append((account.name, index, len(targets)))
        return {"ok": True, "total": len(targets), "success": len(targets), "failed": 0,
                "skipped": len(self.accounts) - len(targets), "cancelled": False}

    def export_accounts(self, password: str) -> Optional[bytes]:
        if self.export_expected and password != self.export_expected:
            return None
        return self.MAGIC + password.encode("utf-8")

    def import_accounts(self, password: str, data: bytes, merge: bool = True) -> int:
        if not data.startswith(self.MAGIC) or data[len(self.MAGIC):].decode("utf-8", "replace") != password:
            return -1
        for index in range(self.import_count):
            self.accounts.append(fake_account(f"Imported{index}", "offline"))
        return self.import_count


class RecordingAccountService(AccountService):
    """真服务 + 一处替换：把"打开导出目录"记下来（测试绝不能真去开资源管理器）。"""

    def __init__(self, system: Any = None) -> None:
        super().__init__(system=system)
        self.opened: List[str] = []

    def open_in_explorer(self, target_path: str) -> Tuple[bool, str]:
        self.opened.append(str(target_path))
        return True, ""


class FakeHome:
    """`Home` 桥的最小替身（账号桥只调它的 `refresh()`）。"""

    def __init__(self, broken: bool = False) -> None:
        self.refreshes, self.broken = 0, broken

    def refresh(self) -> None:
        self.refreshes += 1
        if self.broken:
            raise RuntimeError("首页卡片坏了")


class Harness:
    """一次装配的产物：桥 + 真服务（子类）+ 真 `AppContext` + 全部信号收集器。"""

    def __init__(self, system: Any = None, *, with_service: bool = True) -> None:
        self.system: FakeSystem = system if system is not None else FakeSystem()
        self.context = AppContext()
        self.service: Optional[RecordingAccountService] = None
        if with_service:
            self.service = self.context.register(RecordingAccountService(system=self.system))
        self.statuses: List[Tuple[str, str]] = []
        self.notices: List[Tuple[str, str]] = []
        self.deleted: List[Tuple[str, str]] = []
        self.asked: List[Tuple[str, str]] = []
        self.exported: List[str] = []
        self.dialogs: List[str] = []
        self.mismatches: List[str] = []
        self.bridge = AccountsBridge()
        self.bridge.statusMessage.connect(lambda t, l: self.statuses.append((t, l)))
        self.bridge.noticeRequested.connect(lambda t, l: self.notices.append((t, l)))
        self.bridge.deleteRequested.connect(lambda i, n: self.deleted.append((i, n)))
        self.bridge.refreshTokenRequested.connect(lambda i, n: self.asked.append((i, n)))
        self.bridge.exported.connect(self.exported.append)
        self.bridge.passwordMismatch.connect(self.mismatches.append)
        for name, attr in (("import_password", "importPasswordRequested"), ("import_file", "importFileRequested"),
                           ("export_password", "exportPasswordRequested"),
                           ("export_password_confirm", "exportPasswordConfirmRequested"),
                           ("export_file", "exportFileRequested")):
            getattr(self.bridge, attr).connect(lambda name=name: self.dialogs.append(name))
        self.bridge.bind(self.context)

    def connect_home(self, *, broken: bool = False) -> FakeHome:
        home = FakeHome(broken=broken)
        self.bridge.use_engine(SimpleNamespace(_fmcl_bridges={"Home": home}))  # `use_engine` 的约定
        return home

    @property
    def last_status(self) -> Tuple[str, str]:
        return self.statuses[-1]


class TestDegradedMode:
    def test_every_property_has_an_error_state_without_the_service(self) -> None:
        h = Harness(with_service=False)
        bridge = h.bridge
        assert (bridge.available, bridge.accounts, bridge.count) == (False, [], 0)
        assert (bridge.hasAccounts, bridge.currentId, bridge.currentName) == (False, "", "")
        assert (bridge.currentTypeKey, bridge.busy, bridge.progressVisible) == ("", False, False)
        assert (bridge.cancellable, bridge.progress, bridge.lastNotice) == (False, {}, {})
        assert (bridge.progressCurrent, bridge.progressTotal) == (0, 0)
        assert bridge.fileFilter == ACCOUNT_FILE_FILTER and bridge.fileSuffix == ACCOUNT_FILE_SUFFIX
        assert (bridge.exportTitleKey, bridge.exportSuccessKey, bridge.exportFailedKey, bridge.openFolderKey) == (
            "account_export_title", "account_export_success", "account_export_failed", "version_open_folder")

    def test_no_usable_context_still_gives_an_error_state(self) -> None:
        class BrokenContext:
            def try_get(self, name: str) -> Any:
                raise RuntimeError("上下文坏了")

        for context in (None, AppContext(), BrokenContext()):  # 缺席 / 空上下文 / 取服务抛异常
            bridge = AccountsBridge()
            assert bridge.bind(context) is None and bridge.available is False and bridge.accounts == []

    def test_every_slot_without_the_service_only_fires_the_pure_requests(self) -> None:
        """没有服务时改动类槽全是空操作；只有"请页面弹框"这类纯请求信号照发。"""
        h = Harness(with_service=False)
        for call in (h.bridge.refresh, lambda: h.bridge.setCurrent("id-1"), lambda: h.bridge.confirmDelete("id-1"),
                     lambda: h.bridge.requestDelete("id-1"), lambda: h.bridge.confirmRefreshToken("id-1"),
                     lambda: h.bridge.requestRefreshToken("id-1"), lambda: h.bridge.startLogin("microsoft", None),
                     h.bridge.cancel, h.bridge.requestImport, lambda: h.bridge.setImportPassword("pw"),
                     lambda: h.bridge.submitImport("nope"), h.bridge.requestExport,
                     lambda: h.bridge.setExportPassword("pw"), lambda: h.bridge.confirmExportPassword("pw"),
                     lambda: h.bridge.submitExportPath("nope"), h.bridge.openExportFolder):
            call()
        assert h.bridge.refreshAll() is False and h.bridge.busy is False and h.bridge.count == 0
        assert (h.statuses, h.notices, h.exported, h.mismatches) == ([], [], [], [])
        assert h.deleted == [("id-1", "")], "requestDelete 不需要服务：它只是请页面弹确认框"
        assert h.dialogs == ["import_password", "import_file", "export_password", "export_password_confirm",
                             "export_file"]


class TestAccountList:
    def test_rows_map_the_current_flag_uuid_and_display_name(self) -> None:
        long_uuid = "0123456789abcdefghijKLMNOP"
        alex, steve = fake_account("Alex", "microsoft", long_uuid), fake_account("Steve")
        steve.uuid = ""
        h = Harness(FakeSystem([alex, steve], alex))
        assert h.bridge.available is True and (h.bridge.count, h.bridge.hasAccounts) == (2, True)
        current_row, other_row = h.bridge.accounts
        assert current_row["current"] is True and current_row["uuid_short"] == long_uuid[:20] + "..."
        assert current_row["display_name"] == "\u2605 Alex" and current_row["type_key"] == "account_type_microsoft"
        assert other_row["current"] is False and other_row["uuid_short"] == "-"
        assert other_row["display_name"] == "Steve", "星标只给当前账号"
        assert (h.bridge.currentId, h.bridge.currentName, h.bridge.currentTypeKey) == (
            "id-Alex", "Alex", "account_type_microsoft")
        changes: List[int] = []
        h.bridge.accountsChanged.connect(lambda: changes.append(1))
        h.system.accounts.append(fake_account("Ghost"))
        h.bridge.refresh()  # 值没变也发：QML 的绑定靠信号，不靠 diff
        h.bridge.refresh()
        assert changes == [1, 1] and h.bridge.count == 3


class TestAccountSlots:
    def test_set_current_success_refreshes_and_failure_reports_a_status(self) -> None:
        alex, bob = fake_account("Alex"), fake_account("Bob")
        h = Harness(FakeSystem([alex, bob], alex))
        home = h.connect_home()
        h.bridge.setCurrent("id-Bob")
        assert h.system.switched == ["id-Bob"] and h.bridge.currentName == "Bob" and home.refreshes == 1
        h.bridge.setCurrent("id-Nobody")
        assert h.bridge.currentId == "id-Bob" and home.refreshes == 1, "切换失败不改当前账号也不刷首页"
        assert h.last_status == ("设为当前", "error")

    def test_delete_requests_the_name_and_confirm_really_removes(self) -> None:
        alex, bob = fake_account("Alex"), fake_account("Bob")
        h = Harness(FakeSystem([alex, bob], alex))
        home = h.connect_home()
        h.bridge.requestDelete("id-Bob")
        h.bridge.requestDelete("id-Nobody")
        assert h.deleted == [("id-Bob", "Bob"), ("id-Nobody", "")], "确认框要拿到名字"
        h.bridge.confirmDelete("id-Bob")
        assert h.system.removed == ["id-Bob"] and h.bridge.count == 1 and home.refreshes == 1
        assert h.last_status == ("删除", "success") and h.notices == []
        h.bridge.confirmDelete("id-Nobody")
        assert h.last_status == ("删除", "error") and h.notices == [], "删不掉走状态条，不弹 Toast"

    def test_refresh_token_request_and_both_outcomes(self) -> None:
        h = Harness(FakeSystem([fake_account("MSPlayer", "microsoft")]))
        h.bridge.requestRefreshToken("id-MSPlayer")
        assert h.asked == [("id-MSPlayer", "MSPlayer")]
        h.bridge.confirmRefreshToken("id-MSPlayer")
        assert h.system.refresh_calls == ["id-MSPlayer"] and h.last_status == ("Token 刷新成功", "success")
        h.system.refresh_ok = False
        h.bridge.confirmRefreshToken("id-MSPlayer")
        h.bridge.confirmRefreshToken("id-Nobody")
        assert h.last_status == ("Token 刷新失败，请重新登录", "error")


class TestLogin:
    @pytest.mark.parametrize("kind,params,expected", [
        ("offline", {"name": "   "}, "请输入角色名"),
        ("yggdrasil", {"server_url": "https://x", "username": "", "password": "p"}, "请填写所有字段"),
        # 未知方式走的是 `account_login_failed`：桥要把 `type=kind` 填进占位符
        # （第一版漏了参数、状态栏会显示模板原文；桥测试报出来后已修）
        ("netease", {}, "netease 登录失败，请重试"),
    ])
    def test_validation_failures_become_status_messages(self, kind: str, params: Dict[str, Any],
                                                        expected: str) -> None:
        h = Harness(FakeSystem())
        h.bridge.startLogin(kind, params)
        assert h.last_status == (expected, "warning") and h.bridge.busy is False
        assert h.system.login_calls == [] and h.bridge.progress == {}, "校验是服务做的：一个登录方法都没调"

    @pytest.mark.parametrize("kind,params,name", [
        ("offline", {"name": "Alex"}, "Alex"),
        ("microsoft", {}, "MSPlayer"),
        ("yggdrasil", {"server_url": "https://x", "username": "Ygg", "password": "p"}, "Ygg"),
    ])
    def test_a_successful_start_flips_busy_then_reports_success(self, kind: str, params: Dict[str, Any],
                                                                name: str) -> None:
        h = Harness(FakeSystem())
        home = h.connect_home()
        h.bridge.startLogin(kind, params)
        assert h.bridge.busy is True and h.bridge.progressVisible is True
        assert pump(lambda: bool(h.statuses)), "登录终态没有投递到主线程"
        assert h.bridge.busy is False and h.bridge.count == 1 and home.refreshes >= 1
        assert h.last_status == (f"登录中 {name}", "success")

    def test_offline_duplicate_name_only_warns(self) -> None:
        h = Harness(FakeSystem([fake_account("Alex")]))
        h.bridge.startLogin("offline", {"name": "Alex"})
        assert h.bridge.busy is True and h.notices == [("已存在同名账号「Alex」，仍可继续创建", "warning")]
        assert pump(lambda: h.bridge.busy is False) and h.bridge.count == 2, "重名只提醒，不拦截创建"

    def test_cancel_and_login_failure_are_both_reported(self) -> None:
        system = FakeSystem()
        system.ms_login_delay = 2.0  # 卡在 cancel_event.wait 上，只有取消能唤醒
        h = Harness(system)
        h.bridge.startLogin("microsoft", {})
        assert pump(lambda: h.bridge.progress.get("phase") == PHASE_RUNNING), "登录起步的进度没到"
        assert h.bridge.cancellable is True, "微软登录进行中时「取消」可用"
        assert h.bridge.refreshAll() is False, "登录还在跑时不能抢同一个取消事件"
        h.bridge.cancel()
        assert h.bridge.busy is False and h.last_status == ("已取消登录", "warning")
        assert pump(lambda: h.bridge.progress.get("phase") == PHASE_CANCELLED), "取消的终态没到"
        assert h.bridge.busy is False, "取消的终态不该把界面又置回忙"
        h.bridge.cancel()  # 已经没有任务在跑
        assert h.statuses.count(("已取消登录", "warning")) == 2, "第二次取消不该再报一次"
        system.ms_login_delay, system.ms_login_fail = 0.0, True
        h.bridge.startLogin("microsoft", {})
        assert pump(lambda: h.last_status == ("microsoft 登录失败，请重试", "error")), "失败终态没投递"

    def test_refresh_all_reports_progress_and_success(self) -> None:
        h = Harness(FakeSystem([fake_account("MSPlayer", "microsoft")]))
        assert h.bridge.refreshAll() is True and h.bridge.busy is True and h.bridge.progressVisible is True
        assert h.bridge.cancellable is False, "进度记录还没投递时不假装能取消"
        assert pump(lambda: bool(h.statuses)), "全部刷新的终态没有投递"
        assert h.system.refresh_all_report == [("MSPlayer", 1, 1)]
        assert h.last_status == ("Token 刷新成功", "success") and h.bridge.busy is False


class TestProgressPipeline:
    def test_worker_records_are_queued_then_delivered_on_the_main_thread(self) -> None:
        h = Harness(FakeSystem([fake_account("Alex")]))
        home = h.connect_home()
        busy_changes: List[bool] = []
        h.bridge.busyChanged.connect(lambda: busy_changes.append(h.bridge.busy))
        emit_from_worker(h.service, phase=PHASE_RUNNING, kind="microsoft", message="正在打开浏览器...",
                         current=0, total=2)
        emit_from_worker(h.service, phase=PHASE_RUNNING, kind="microsoft", message="第二条", current=1, total=2)
        assert h.bridge.busy is False and h.bridge.progress == {}, "worker 只排队：没转事件循环前不落属性"
        assert pump(lambda: h.bridge.progress.get("message") == "第二条"), "两条 running 记录没有按序投递"
        assert (h.bridge.progressCurrent, h.bridge.progressTotal, h.bridge.cancellable) == (1, 2, True)
        account_changes: List[int] = []
        h.bridge.accountsChanged.connect(lambda: account_changes.append(1))
        h.system.accounts.append(fake_account("Newcomer"))
        emit_from_worker(h.service, phase=PHASE_LOGIN_OK, kind="offline", ok=True, name="Newcomer", current=1, total=1)
        assert pump(lambda: bool(h.statuses)), "终态记录没有投递"
        assert busy_changes == [True, False] and h.bridge.busy is False
        assert account_changes == [1], "终态要刷新列表并广播 accountsChanged"
        assert h.bridge.count == 2 and h.last_status == ("登录中 Newcomer", "success") and home.refreshes == 1

    def test_message_key_is_translated_and_message_is_passed_through(self) -> None:
        """进度记录的**两条来源**分工：`message_key` 要翻、`message` 原样透出。

        2026-10-06 用户验收当场报的 `account_ms_verifying` 直接显示在进度条上：
        第一版服务层把"要翻译的键"塞进了 `message`，页面把键名当文案印了。
        修法就是这里钉的两条 —— 键归 `message_key`（桥翻好再给 QML），
        核心层那批中文归 `message`（原样透出，见服务模块文档第 3 条取舍）。
        """

        h = Harness(FakeSystem([fake_account("Alex")]))
        emit_from_worker(h.service, phase=PHASE_RUNNING, kind="microsoft", message="",
                         message_key="account_ms_verifying", current=0, total=1)
        assert pump(lambda: bool(h.bridge.progress.get("message"))), "带 message_key 的记录没有投递"
        shown = str(h.bridge.progress.get("message"))
        assert shown != "account_ms_verifying", "键名被原样显示出来了"
        assert "_" not in shown and shown != "", shown
        assert "message_key" not in h.bridge.progress, "翻译完的键不该继续透给 QML"

        emit_from_worker(h.service, phase=PHASE_RUNNING, kind="microsoft",
                         message="正在打开浏览器...", message_key="", current=0, total=1)
        assert pump(lambda: h.bridge.progress.get("message") == "正在打开浏览器..."), "中文状态串被改掉了"

        # 用一个只读翻译替身确认"翻的是那条键、填的是译文"
        original = bridge_module._
        bridge_module._ = lambda key, **params: f"<{key}>" if not params else f"<{key}:{params}>"
        try:
            emit_from_worker(h.service, phase=PHASE_RUNNING, kind="offline", message="",
                             message_key="logging_in", current=0, total=1)
            assert pump(lambda: h.bridge.progress.get("message") == "<logging_in>"), h.bridge.progress
        finally:
            bridge_module._ = original


class TestTransferFlow:
    def test_the_export_flow_writes_the_file_and_opens_its_folder(self, tmp_path: Path) -> None:
        h = Harness(FakeSystem([fake_account("Alex")]))
        target = tmp_path / "backup.fmcl_accounts"
        h.bridge.requestExport()
        h.bridge.setExportPassword("pw-1")
        assert h.dialogs == ["export_password", "export_password_confirm"], "先设密码、再输一遍核对"
        h.bridge.confirmExportPassword("pw-1")
        assert h.dialogs[-1] == "export_file"
        h.bridge.submitExportPath(str(target))
        assert target.read_bytes() == b"FMCL_ACCOUNTS_V1\npw-1" and h.exported == [str(target)] and h.notices == []
        h.bridge.openExportFolder()
        assert h.service.opened == [str(target)], "「打开所在目录」要交给服务的 opener"

    def test_export_cancels_and_a_mismatch_never_write_a_file(self, tmp_path: Path) -> None:
        h = Harness(FakeSystem())
        h.bridge.requestExport()
        h.bridge.setExportPassword("")
        h.bridge.setExportPassword("pw-1")  # 取消后页面若还回填，桥不该再往下走
        assert h.dialogs == ["export_password"]
        h.bridge.requestExport()
        h.bridge.setExportPassword("pw-1")
        h.bridge.confirmExportPassword("typo")  # 两次不一致 → 退回首步重输
        #: 文案**随 `passwordMismatch` 信号一起**到（第一版是个"读一次就复位"的属性，
        #: 桥测试指出那是读属性改状态、对 QML 绑定是埋雷，已改成信号）
        assert h.mismatches == ["两次输入的密码不一致"] and h.dialogs[-1] == "export_password"
        h.bridge.setExportPassword("pw-1")
        h.bridge.confirmExportPassword("")  # 第二步取消
        h.bridge.submitExportPath(str(tmp_path / "x.fmcl_accounts"))
        assert h.exported == [] and h.notices[-1] == ("两次输入的密码不一致", "error")
        h.system.export_expected = "pw-right"  # 密码不对 → 核心层返回 None
        h.bridge.requestExport()
        h.bridge.setExportPassword("pw-1")
        h.bridge.confirmExportPassword("pw-1")
        h.bridge.submitExportPath(str(tmp_path / "x.fmcl_accounts"))
        assert h.notices[-1] == ("导出失败", "error") and not (tmp_path / "x.fmcl_accounts").exists()

    def test_the_import_flow_reports_the_count_and_refreshes(self, tmp_path: Path) -> None:
        source = tmp_path / "backup.fmcl_accounts"
        source.write_bytes(b"FMCL_ACCOUNTS_V1\npw-2")
        system = FakeSystem([fake_account("Alex")])
        system.import_count = 3
        h = Harness(system)
        home = h.connect_home()
        h.bridge.requestImport()
        h.bridge.setImportPassword("pw-2")
        assert h.dialogs == ["import_password", "import_file"]
        h.bridge.submitImport(str(source))
        assert h.bridge.count == 4 and h.notices == [("成功导入 3 个账号", "success")]
        assert home.refreshes == 1 and h.bridge.lastNotice["level"] == "success"

    def test_import_failures_and_cancels_are_reported(self, tmp_path: Path) -> None:
        source = tmp_path / "backup.fmcl_accounts"
        source.write_bytes(b"FMCL_ACCOUNTS_V1\npw-2")
        h = Harness(FakeSystem([fake_account("Alex")]))
        h.bridge.requestImport()
        h.bridge.setImportPassword("")
        h.bridge.setImportPassword("nope")  # 取消后回填一律忽略
        assert h.dialogs == ["import_password"]
        h.bridge.requestImport()
        h.bridge.setImportPassword("nope")
        h.bridge.submitImport(str(source))
        assert h.notices[-1] == ("密码错误或文件已损坏", "error") and h.bridge.count == 1


class TestDetachAndEngine:
    def test_bind_registers_the_listener_and_detach_removes_it(self) -> None:
        h = Harness()
        assert h.bridge._on_progress in h.service._listeners
        h.bridge.detach()
        assert h.service._listeners == []
        emit_from_worker(h.service, phase=PHASE_RUNNING, kind="offline", current=0, total=1)
        assert pump(lambda: h.bridge.busy, timeout_s=0.2) is False, "注销后进度不该再落到桥上"

    def test_use_engine_refreshes_the_home_card_and_tolerates_its_absence(self) -> None:
        alex, bob = fake_account("Alex"), fake_account("Bob")
        h = Harness(FakeSystem([alex, bob], alex))
        home = h.connect_home()
        h.bridge.setCurrent("id-Bob")
        h.bridge.confirmDelete("id-Bob")
        assert home.refreshes == 2, "切账号与删账号都要通知首页"
        h.bridge.use_engine(SimpleNamespace(_fmcl_bridges={}))  # 没有 Home
        h.bridge.use_engine(SimpleNamespace())  # 连 _fmcl_bridges 都没有
        h.bridge.use_engine(None)  # 引擎缺席
        h.connect_home(broken=True)
        h.bridge.setCurrent("id-Alex")
        assert h.bridge.currentId == "id-Alex" and len(h.statuses) == 1, "首页刷不动不影响账号页"
