"""`services/account_service.py` 写侧（阶段 3 任务 3.5：M-13 / M-26 ~ M-29）的永久回归守卫。

守八件事：

1. **登录参数校验**（旧实现在对话框的按钮处理里）：离线空名字 → `account_name_required`、
   外置缺字段 → `account_ygg_fields_required`，且**校验失败不起线程**；
2. **登录是长任务**：`start_login()` **立刻返回**，进度与结果经监听器抛出（工作线程回调）；
3. **取消**：`begin_cancel()` 置位 → 核心层拿到同一个 `threading.Event`；取消不算失败
   （`phase == cancelled`，不是 `login_failed`）；
4. **成就**：新建离线账号触发 `personalize_rename`（阶段 1.23 / D-110 的唯一触发点），
   微软与外置登录**不**触发；
5. **全部刷新**：强制刷新（不筛过期）、逐个账号回报进度、可取消；
6. **导入/导出**：真加密往返（核心层 PBKDF2 + Fernet）、错密码 → `account_import_password_error`、
   `-1` 与 `0` 的语义不混；
7. **打开导出目录**：平台分支走注入的 opener，失败如实返回 `(False, 错误串)`；
8. **重名提醒**：只提醒不拦截（用户 2026-10-06 裁决）。

替身是"能跑真流程的最小账号系统"：`FakeSystem` 的登录方法尊重注入的 `cancel_event`
（与核心层 `launcher/account.py` 的协作式取消同一个契约），所以取消、进度、返回值
这三条链路在这里是**真的**被跑过一遍的。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.context import AppContext  # noqa: E402
from services.account_service import (  # noqa: E402
    PHASE_CANCELLED,
    PHASE_LOGIN_FAILED,
    PHASE_LOGIN_OK,
    PHASE_REFRESHED,
    PHASE_RUNNING,
    AccountService,
)

# ─── 替身 ──────────────────────────────────────────────────────


class FakeAccount:
    def __init__(self, name: str = "Steve", kind: str = "offline", uuid: Optional[str] = None,
                 expired: bool = False) -> None:
        self.id = f"id-{name}"
        self.name = name
        self.uuid = uuid or f"uuid-{name}"
        self.access_token = "token" if not expired else ""
        self.refresh_token = "refresh" if kind == "microsoft" else None
        self.account_type = type("T", (), {"value": kind})()
        self.display_name = name
        self._expired = expired

    def is_token_expired(self, buffer_seconds: int = 300) -> bool:
        return self._expired


class FakeSystem:
    """最小可用的账号系统：登录尊重 cancel_event，其余按真语义记账。"""

    def __init__(self, accounts: Optional[List[FakeAccount]] = None,
                 current: Optional[FakeAccount] = None) -> None:
        self.accounts: List[FakeAccount] = list(accounts or [])
        self.current_account = current
        self.current_account_id = current.id if current else None
        self.refreshed = 0
        self.switched: List[str] = []
        self.removed: List[str] = []
        self.login_calls: List[Dict[str, Any]] = []
        self.refresh_all_report: List[tuple] = []
        self.refresh_all_cancel_event: Any = None
        self.offline_login_result: Any = None
        self.ms_login_result: Any = None
        self.ygg_login_result: Any = None
        self.refresh_all_result: Optional[Dict[str, Any]] = None
        self.raise_on_login: Optional[Exception] = None

    # 读侧
    def get_account(self, account_id: str) -> Optional[FakeAccount]:
        for acc in self.accounts:
            if acc.id == account_id:
                return acc
        return None

    def set_current_account(self, account_id: str) -> bool:
        self.switched.append(account_id)
        acc = self.get_account(account_id)
        if acc is None:
            return False
        self.current_account = acc
        self.current_account_id = account_id
        return True

    def remove_account(self, account_id: str) -> bool:
        acc = self.get_account(account_id)
        if acc is None:
            return False
        self.accounts.remove(acc)
        self.removed.append(account_id)
        if self.current_account_id == account_id:
            self.current_account = None
            self.current_account_id = None
        return True

    # 登录（三种都尊重 cancel_event）
    def microsoft_login(self, status_callback=None, *, cancel_event=None):
        self.login_calls.append({"kind": "microsoft", "cancel_event": cancel_event})
        if status_callback:
            status_callback("正在打开浏览器...")
        if cancel_event is not None:
            cancel_event.wait(2.0)
            if cancel_event.is_set():
                return None
        if self.raise_on_login:
            raise self.raise_on_login
        if self.ms_login_result is None:
            self.ms_login_result = FakeAccount("MSPlayer", "microsoft")
        self.accounts.append(self.ms_login_result)
        return self.ms_login_result

    def offline_login(self, name: str):
        self.login_calls.append({"kind": "offline", "name": name})
        if self.offline_login_result is None:
            self.offline_login_result = FakeAccount(name, "offline")
        self.accounts.append(self.offline_login_result)
        return self.offline_login_result

    def yggdrasil_login(self, server_url, username, password, status_callback=None, *, cancel_event=None):
        self.login_calls.append({"kind": "yggdrasil", "server_url": server_url,
                                 "username": username, "password": password,
                                 "cancel_event": cancel_event})
        if cancel_event is not None and cancel_event.is_set():
            return None
        if self.ygg_login_result is None:
            self.ygg_login_result = FakeAccount(username, "yggdrasil")
        self.accounts.append(self.ygg_login_result)
        return self.ygg_login_result

    # 刷新
    def auto_refresh_all_tokens(self) -> int:
        self.refreshed += 1
        return 2

    def refresh_account_token(self, account: FakeAccount, *, cancel_event=None) -> bool:
        if account.account_type.value != "microsoft":
            return True
        account._expired = False
        account.access_token = "token"
        return True

    def refresh_all_account_tokens(self, on_account=None, *, cancel_event=None):
        self.refresh_all_cancel_event = cancel_event
        if self.refresh_all_result is not None:
            return dict(self.refresh_all_result)
        targets = [a for a in self.accounts if a.account_type.value == "microsoft" and a.refresh_token]
        result = {"ok": True, "total": len(targets), "success": 0, "failed": 0,
                  "skipped": len(self.accounts) - len(targets), "cancelled": False}
        for index, acc in enumerate(targets, start=1):
            if cancel_event is not None and cancel_event.is_set():
                result["cancelled"] = True
                break
            if on_account:
                on_account(acc.name, index, len(targets))
            self.refresh_all_report.append((acc.name, index, len(targets)))
            result["success"] += 1
            # 每个账号之间留一点真空隙，让"取消"在测试里可复现（真实现是一次 HTTP 往返）
            time.sleep(0.05)
        if cancel_event is not None and cancel_event.is_set():
            result["cancelled"] = True
        return result

    # 导入 / 导出
    def export_accounts(self, password: str):
        if not password:
            return None
        return b"FMCL_ACCOUNTS_V1\n" + password.encode("utf-8")

    def import_accounts(self, password: str, data: bytes, merge: bool = True) -> int:
        if not data.startswith(b"FMCL_ACCOUNTS_V1\n"):
            return -1
        try:
            payload = data[len(b"FMCL_ACCOUNTS_V1\n"):].decode("utf-8")
        except UnicodeDecodeError:
            return -1
        if payload != password:
            return -1
        # 约定：密码形如 `pw-<n>` 表示"这次导入 n 个账号"（测试用它验证 count 语义）
        if "-" in password:
            suffix = password.rsplit("-", 1)[-1]
            if suffix.isdigit():
                return int(suffix)
        return 1


def make_service(system: Any = None) -> AccountService:
    ctx = AppContext()
    service = AccountService(system=system)
    ctx.register(service)
    return service


def wait_for(records: List[Dict[str, Any]], phase: str, timeout: float = 5.0) -> Dict[str, Any]:
    """等一条指定 phase 的进度记录（异步任务的测试都得有个带超时的等待）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for record in list(records):
            if record.get("phase") == phase:
                return record
        time.sleep(0.01)
    raise AssertionError(f"等不到 phase={phase} 的记录；已有：{[r.get('phase') for r in records]}")


class Recorder:
    def __init__(self) -> None:
        self.records: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def __call__(self, record: Dict[str, Any]) -> None:
        with self._lock:
            self.records.append(dict(record))

    def phases(self) -> List[str]:
        with self._lock:
            return [r.get("phase") for r in self.records]


# ─── M-26 登录：参数校验（旧实现在对话框按钮处理里）──────────────


class TestLoginValidation:
    def test_offline_empty_name_is_rejected_without_starting_a_thread(self) -> None:
        service = make_service(FakeSystem())
        rec = Recorder()
        service.add_listener(rec)
        assert service.start_login("offline", {"name": "   "}) == "account_name_required"
        assert rec.records == []
        assert service.busy() is False
        assert service.system.login_calls == []

    def test_offline_name_is_stripped(self) -> None:
        system = FakeSystem()
        service = make_service(system)
        rec = Recorder()
        service.add_listener(rec)
        assert service.start_login("offline", {"name": "  Alex  "}) == ""
        wait_for(rec.records, PHASE_LOGIN_OK)
        assert system.login_calls[0]["name"] == "Alex"

    @pytest.mark.parametrize("params", [
        {"server_url": "", "username": "u", "password": "p"},
        {"server_url": "https://x", "username": "", "password": "p"},
        {"server_url": "https://x", "username": "u", "password": ""},
    ])
    def test_yggdrasil_missing_fields_are_rejected(self, params: Dict[str, str]) -> None:
        service = make_service(FakeSystem())
        assert service.start_login("yggdrasil", params) == "account_ygg_fields_required"
        assert service.busy() is False

    def test_unknown_kind_is_refused(self) -> None:
        service = make_service(FakeSystem())
        assert service.start_login("netease", {}) == "account_login_failed"
        assert service.busy() is False

    def test_no_account_system_is_refused(self) -> None:
        service = make_service(None)
        assert service.start_login("microsoft", {}) == "account_login_failed"

    def test_second_login_while_busy_is_refused(self) -> None:
        """界面本该禁用按钮；万一没禁，服务也不能让两个登录抢同一个取消事件。"""
        system = FakeSystem()
        service = make_service(system)
        assert service.start_login("microsoft", {}) == ""
        assert service.busy() is True
        try:
            assert service.start_login("offline", {"name": "Alex"}) == "account_login_failed"
        finally:
            service.begin_cancel()
            service._current_cancel_event().wait(2.0) if service._current_cancel_event() else None


# ─── M-26 登录：异步 + 进度 + 取消 ─────────────────────────────


class TestLoginAsync:
    def test_start_login_returns_immediately(self) -> None:
        """核心层的登录会阻塞（浏览器回调最长 180 秒）—— 调用点**不能**被它卡住。"""
        system = FakeSystem()
        service = make_service(system)
        started = time.time()
        assert service.start_login("microsoft", {}) == ""
        assert time.time() - started < 0.5
        service.begin_cancel()
        event = service._current_cancel_event()
        if event is not None:
            event.wait(2.0)

    def test_microsoft_login_reports_status_and_success(self) -> None:
        system = FakeSystem()
        service = make_service(system)
        rec = Recorder()
        service.add_listener(rec)
        assert service.start_login("microsoft", {}) == ""
        done = wait_for(rec.records, PHASE_LOGIN_OK)
        assert done["ok"] is True
        assert done["name"] == "MSPlayer"
        assert done["type"] == "microsoft"
        # 核心层那句中文状态**原样**转出来（不二次包装，见模块文档第 3 条取舍）
        running = [r for r in rec.records if r["phase"] == PHASE_RUNNING]
        assert any("正在打开浏览器" in str(r.get("message", "")) for r in running)
        assert service.busy() is False  # 收尾后必须放开

    def test_the_opening_sentinel_is_a_key_not_a_message(self) -> None:
        """登录刚开始那句占位**只能是 i18n 键**（走 `message_key`），不能混进 `message`。

        2026-10-06 用户验收当场报的 `account_ms_verifying` 直接显示在进度条上，根因就是
        第一版把键塞进了 `message`，界面当文案印了出来。这条判据钉住两者的分工：
        `message` = 已经成句的中文（核心层回调）/ `message_key` = 界面要翻的键。
        """
        system = FakeSystem()
        service = make_service(system)
        rec = Recorder()
        service.add_listener(rec)
        assert service.start_login("microsoft", {}) == ""
        wait_for(rec.records, PHASE_LOGIN_OK)
        running = [r for r in rec.records if r["phase"] == PHASE_RUNNING]
        sentinels = [r for r in running if r.get("message_key")]
        assert sentinels, f"没有一条带 message_key 的记录：{running}"
        assert sentinels[0]["message_key"] == "account_ms_verifying", sentinels[0]
        assert sentinels[0]["message"] == "", f"占位那一条不该带 message：{sentinels[0]}"

        # 离线登录的占位键是 `logging_in`（旧键，界面早就有了）
        rec2 = Recorder()
        service.add_listener(rec2)
        assert service.start_login("offline", {"name": "Alex"}) == ""
        wait_for(rec2.records, PHASE_LOGIN_OK)
        first = [r for r in rec2.records if r["phase"] == PHASE_RUNNING][0]
        assert first["message_key"] == "logging_in" and first["message"] == "", first

    def test_every_status_key_exists_in_the_locale_files(self) -> None:
        """占位键必须在语言文件里存在 —— 否则界面翻出来还是键名（就等于没修）。"""
        import json
        from pathlib import Path

        locales = Path(__file__).resolve().parents[1] / "ui" / "locales"
        for code in ("zh_CN", "en_US", "zh_TW", "ja_JP"):
            table = json.loads((locales / f"{code}.json").read_text(encoding="utf-8"))
            for key in ("account_ms_verifying", "logging_in"):
                assert key in table, f"{code} 缺 {key}"

    def test_microsoft_login_failure_is_reported_not_raised(self) -> None:
        system = FakeSystem()
        system.raise_on_login = RuntimeError("网络断了")
        service = make_service(system)
        rec = Recorder()
        service.add_listener(rec)
        assert service.start_login("microsoft", {}) == ""
        done = wait_for(rec.records, PHASE_LOGIN_FAILED)
        assert done["ok"] is False
        assert done["error"] == "网络断了"

    def test_cancel_reaches_the_core_and_is_not_a_failure(self) -> None:
        system = FakeSystem()
        service = make_service(system)
        rec = Recorder()
        service.add_listener(rec)
        assert service.start_login("microsoft", {}) == ""
        wait_for(rec.records, PHASE_RUNNING)
        assert service.begin_cancel() is True
        done = wait_for(rec.records, PHASE_CANCELLED)
        assert done["ok"] is False
        assert done["cancelled"] is True
        # 核心层确实收到了**同一个** Event 对象（不是"界面以为取消了"）
        assert system.login_calls[0]["cancel_event"] is not None
        assert system.login_calls[0]["cancel_event"].is_set() is True
        assert service.busy() is False

    def test_cancel_without_a_running_task_returns_false(self) -> None:
        service = make_service(FakeSystem())
        assert service.begin_cancel() is False

    def test_listener_exception_does_not_break_the_task(self) -> None:
        system = FakeSystem()
        service = make_service(system)
        rec = Recorder()

        def bad(_record: Dict[str, Any]) -> None:
            raise RuntimeError("界面炸了")

        service.add_listener(bad)
        service.add_listener(rec)
        assert service.start_login("offline", {"name": "Alex"}) == ""
        wait_for(rec.records, PHASE_LOGIN_OK)  # 坏监听器不影响好监听器

    def test_remove_listener_stops_delivery(self) -> None:
        system = FakeSystem()
        service = make_service(system)
        rec = Recorder()
        service.add_listener(rec)
        service.remove_listener(rec)
        assert service.start_login("offline", {"name": "Alex"}) == ""
        wait_for_quiet(service)
        assert rec.records == []

    def test_add_same_listener_twice_only_notifies_once(self) -> None:
        service = make_service(FakeSystem())
        rec = Recorder()
        service.add_listener(rec)
        service.add_listener(rec)
        service.start_login("offline", {"name": "Alex"})
        wait_for(rec.records, PHASE_LOGIN_OK)
        assert rec.phases().count(PHASE_LOGIN_OK) == 1


def wait_for_quiet(service: AccountService, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while service.busy() and time.time() < deadline:
        time.sleep(0.01)


# ─── 成就（阶段 1.23 / D-110）──────────────────────────────────


class TestOfflineAccountAchievement:
    def test_offline_login_triggers_personalize_rename(self, monkeypatch) -> None:
        triggered: List[str] = []

        class Engine:
            def update_progress(self, achievement_id, value=1, trigger_type=None):
                triggered.append(achievement_id)

        import achievement_engine

        monkeypatch.setattr(achievement_engine, "get_achievement_engine", lambda: Engine())
        system = FakeSystem()
        service = make_service(system)
        rec = Recorder()
        service.add_listener(rec)
        service.start_login("offline", {"name": "Alex"})
        wait_for(rec.records, PHASE_LOGIN_OK)
        assert triggered == ["personalize_rename"]

    def test_microsoft_login_does_not_trigger_it(self, monkeypatch) -> None:
        triggered: List[str] = []

        class Engine:
            def update_progress(self, achievement_id, value=1, trigger_type=None):
                triggered.append(achievement_id)

        import achievement_engine

        monkeypatch.setattr(achievement_engine, "get_achievement_engine", lambda: Engine())
        service = make_service(FakeSystem())
        rec = Recorder()
        service.add_listener(rec)
        service.start_login("microsoft", {})
        wait_for(rec.records, PHASE_LOGIN_OK)
        assert triggered == []

    def test_achievement_failure_does_not_fail_the_login(self, monkeypatch) -> None:
        import achievement_engine

        def boom():
            raise RuntimeError("成就引擎坏了")

        monkeypatch.setattr(achievement_engine, "get_achievement_engine", boom)
        service = make_service(FakeSystem())
        rec = Recorder()
        service.add_listener(rec)
        service.start_login("offline", {"name": "Alex"})
        assert wait_for(rec.records, PHASE_LOGIN_OK)["ok"] is True


# ─── M-27 读侧：类型键 / 重名 ──────────────────────────────────


class TestAccountListHelpers:
    def test_duplicate_name_matches_name_and_type(self) -> None:
        system = FakeSystem(accounts=[FakeAccount("Alex", "offline")])
        service = make_service(system)
        assert service.has_duplicate_name("Alex", "offline") is True
        assert service.has_duplicate_name("Alex", "microsoft") is False
        assert service.has_duplicate_name("", "offline") is False
        assert service.has_duplicate_name("  ", "offline") is False

    def test_duplicate_name_does_not_block_creation(self) -> None:
        """用户裁决：重名只提醒不拦截（旧实现 `accounts.json` 允许同名两个）。"""
        system = FakeSystem(accounts=[FakeAccount("Alex", "offline")])
        service = make_service(system)
        rec = Recorder()
        service.add_listener(rec)
        assert service.start_login("offline", {"name": "Alex"}) == ""
        assert wait_for(rec.records, PHASE_LOGIN_OK)["ok"] is True
        assert len(system.accounts) == 2

    def test_account_count_never_raises(self) -> None:
        assert make_service(None).account_count() == 0
        assert make_service(FakeSystem(accounts=[FakeAccount("A")])).account_count() == 1


# ─── M-28 切换 / 删除 / 刷新 ───────────────────────────────────


class TestSwitchAndRemove:
    def test_switch_current_account(self) -> None:
        acc = FakeAccount("Alex", "offline")
        system = FakeSystem(accounts=[acc])
        service = make_service(system)
        assert service.set_current_account(acc.id) is True
        assert system.switched == [acc.id]

    def test_switch_unknown_id_is_false(self) -> None:
        service = make_service(FakeSystem())
        assert service.set_current_account("nope") is False

    def test_remove_account(self) -> None:
        acc = FakeAccount("Alex", "offline")
        system = FakeSystem(accounts=[acc])
        service = make_service(system)
        assert service.remove_account(acc.id) is True
        assert system.removed == [acc.id]

    def test_remove_unknown_id_is_false(self) -> None:
        service = make_service(FakeSystem())
        assert service.remove_account("nope") is False

    def test_remove_last_account_is_allowed(self) -> None:
        """用户裁决：照旧允许删到 0（首页随即显示"未选择账号"）。"""
        acc = FakeAccount("Alex", "offline")
        system = FakeSystem(accounts=[acc], current=acc)
        service = make_service(system)
        assert service.remove_account(acc.id) is True
        assert service.accounts() == []
        assert service.account_summary()["has_account"] is False

    def test_refresh_token_for_microsoft(self) -> None:
        acc = FakeAccount("MS", "microsoft", expired=True)
        system = FakeSystem(accounts=[acc])
        service = make_service(system)
        ok, cancelled = service.refresh_token(acc.id)
        assert (ok, cancelled) == (True, False)
        assert service.refresh_token_succeeded(acc.id) is True

    def test_refresh_token_for_offline_account_is_a_noop_success(self) -> None:
        """旧实现：非微软账号 `refresh_account_token` 直接返回 True。"""
        acc = FakeAccount("Alex", "offline")
        service = make_service(FakeSystem(accounts=[acc]))
        assert service.refresh_token(acc.id) == (True, False)
        assert service.refresh_token_succeeded(acc.id) is True

    def test_refresh_token_unknown_id_is_false(self) -> None:
        service = make_service(FakeSystem())
        assert service.refresh_token("nope") == (False, False)
        assert service.refresh_token_succeeded("nope") is False


# ─── A-25（3.5 落点）全部刷新 Token ────────────────────────────


class TestRefreshAll:
    def _system(self) -> FakeSystem:
        return FakeSystem(accounts=[
            FakeAccount("MS1", "microsoft"),
            FakeAccount("Alex", "offline"),
            FakeAccount("MS2", "microsoft"),
        ])

    def test_refresh_all_reports_per_account_progress(self) -> None:
        system = self._system()
        service = make_service(system)
        rec = Recorder()
        service.add_listener(rec)
        assert service.start_refresh_all() is True
        done = wait_for(rec.records, PHASE_REFRESHED)
        assert done["total"] == 2  # 只有微软账号参与
        assert done["success"] == 2
        assert done["skipped"] == 1
        assert system.refresh_all_report == [("MS1", 1, 2), ("MS2", 2, 2)]
        names = [r["name"] for r in rec.records if r["phase"] == PHASE_RUNNING and r.get("name")]
        assert names == ["MS1", "MS2"]

    def test_refresh_all_can_be_cancelled(self) -> None:
        system = self._system()
        system.refresh_all_result = None
        service = make_service(system)
        rec = Recorder()
        service.add_listener(rec)
        assert service.start_refresh_all() is True
        service.begin_cancel()
        done = wait_for(rec.records, PHASE_CANCELLED)
        assert done["cancelled"] is True
        assert service.busy() is False

    def test_refresh_all_without_system_is_refused(self) -> None:
        assert make_service(None).start_refresh_all() is False

    def test_auto_refresh_still_works(self) -> None:
        """3.1 的启动期静默刷新不受本次改动影响。"""
        system = self._system()
        service = make_service(system)
        assert service.auto_refresh_tokens() == 2
        assert system.refreshed == 1


# ─── M-29 导入 / 导出 ──────────────────────────────────────────


class TestImportExport:
    def test_export_writes_file_and_reports_path(self, tmp_path: Path) -> None:
        service = make_service(FakeSystem())
        target = tmp_path / "sub" / f"accounts{'.fmcl_accounts'}"
        result = service.export_accounts("pw-2", str(target))
        assert result["ok"] is True
        assert result["path"] == str(target)
        assert target.read_bytes().startswith(b"FMCL_ACCOUNTS_V1\n")

    def test_export_without_password_is_refused(self, tmp_path: Path) -> None:
        service = make_service(FakeSystem())
        result = service.export_accounts("", str(tmp_path / "a.fmcl_accounts"))
        assert result["ok"] is False
        assert result["error"] == "account_export_password_mismatch"

    def test_export_when_system_returns_nothing(self, tmp_path: Path) -> None:
        class NoData(FakeSystem):
            def export_accounts(self, password: str) -> Any:
                return None

        service = make_service(NoData())
        result = service.export_accounts("pw", str(tmp_path / "a.fmcl_accounts"))
        assert result["ok"] is False
        assert result["error"] == "account_export_failed"

    def test_export_to_unwritable_path_reports_os_error(self, tmp_path: Path) -> None:
        service = make_service(FakeSystem())
        bad = tmp_path / "dir-as-file"
        bad.write_text("x", encoding="utf-8")
        result = service.export_accounts("pw", str(bad / "a.fmcl_accounts"))
        assert result["ok"] is False
        assert result["error"]  # 系统错误串原样透出（旧实现弹 str(e)）

    def test_import_wrong_password_gives_the_legacy_key(self, tmp_path: Path) -> None:
        source = tmp_path / "a.fmcl_accounts"
        source.write_bytes(b"FMCL_ACCOUNTS_V1\nnot-the-password")
        service = make_service(FakeSystem())
        result = service.import_accounts("pw", str(source))
        assert result["ok"] is False
        assert result["error"] == "account_import_password_error"
        assert result["count"] == 0

    def test_import_bad_file_format_gives_the_same_key(self, tmp_path: Path) -> None:
        """核心层对"文件格式不对"与"密码错"都返回 -1 → 旧界面同一句"密码错误或文件已损坏"。"""
        source = tmp_path / "a.fmcl_accounts"
        source.write_bytes(b"garbage")
        service = make_service(FakeSystem())
        result = service.import_accounts("pw", str(source))
        assert result["ok"] is False
        assert result["error"] == "account_import_password_error"

    def test_import_success_reports_count(self, tmp_path: Path) -> None:
        source = tmp_path / "a.fmcl_accounts"
        source.write_bytes(b"FMCL_ACCOUNTS_V1\npw-3")
        service = make_service(FakeSystem())
        result = service.import_accounts("pw-3", str(source))
        assert result["ok"] is True
        assert result["count"] == 3

    def test_import_missing_file_reports_error(self, tmp_path: Path) -> None:
        service = make_service(FakeSystem())
        result = service.import_accounts("pw", str(tmp_path / "nope.fmcl_accounts"))
        assert result["ok"] is False
        assert result["error"]
        assert result["count"] == 0

    def test_export_then_import_roundtrip_with_real_core(self, tmp_path: Path) -> None:
        """用**真的** `GlobalAccountSystem` 走一遍加密往返（不靠替身自证）。"""
        from launcher.account import GlobalAccountSystem, create_offline_account

        system = GlobalAccountSystem(tmp_path / "data")
        system.add_account(create_offline_account("Alex"))
        service = make_service(system)
        target = tmp_path / "out.fmcl_accounts"
        assert service.export_accounts("s3cret", str(target))["ok"] is True

        other = GlobalAccountSystem(tmp_path / "data2")
        importer = make_service(other)
        result = importer.import_accounts("s3cret", str(target))
        assert result["ok"] is True
        assert result["count"] == 1
        assert [a.name for a in other.accounts] == ["Alex"]
        # 错密码必须是 -1 那条路径
        assert importer.import_accounts("wrong", str(target))["error"] == "account_import_password_error"


# ─── 「打开所在目录」（3.5 新增）──────────────────────────────


class TestOpenInExplorer:
    def test_open_in_explorer_uses_injected_opener(self, monkeypatch, tmp_path: Path) -> None:
        opened: List[str] = []
        monkeypatch.setattr("services.account_service._open_path", opened.append)
        service = make_service(FakeSystem())
        ok, error = service.open_in_explorer(str(tmp_path / "out.fmcl_accounts"))
        assert (ok, error) == (True, "")
        assert opened == [str(tmp_path)]

    def test_open_in_explorer_reports_failure(self, monkeypatch, tmp_path: Path) -> None:
        def boom(_path: str) -> None:
            raise OSError("没有文件管理器")

        monkeypatch.setattr("services.account_service._open_path", boom)
        service = make_service(FakeSystem())
        ok, error = service.open_in_explorer(str(tmp_path / "out.fmcl_accounts"))
        assert ok is False
        assert "没有文件管理器" in error

    def test_open_in_explorer_without_path_is_refused(self) -> None:
        service = make_service(FakeSystem())
        ok, error = service.open_in_explorer("")
        assert ok is False and error == "account_export_failed"


# ─── 破系统（账号文件坏了）也不该抛穿 ──────────────────────────


class BrokenSystem:
    def get_account(self, account_id: str) -> Any:
        raise RuntimeError("账号文件坏了")

    @property
    def accounts(self) -> Any:
        raise RuntimeError("账号文件坏了")

    def set_current_account(self, account_id: str) -> Any:
        raise RuntimeError("账号文件坏了")

    def remove_account(self, account_id: str) -> Any:
        raise RuntimeError("账号文件坏了")

    def export_accounts(self, password: str) -> Any:
        raise RuntimeError("账号文件坏了")

    def import_accounts(self, password: str, data: bytes, merge: bool = True) -> Any:
        raise RuntimeError("账号文件坏了")


class TestBrokenSystemNeverRaises:
    def test_all_write_side_calls_are_guarded(self, tmp_path: Path) -> None:
        service = make_service(BrokenSystem())
        assert service.set_current_account("x") is False
        assert service.remove_account("x") is False
        assert service.account_count() == 0
        assert service.has_duplicate_name("Alex", "offline") is False
        assert service.refresh_token("x") == (False, False)
        assert service.export_accounts("pw", str(tmp_path / "a"))["ok"] is False
        source = tmp_path / "b"
        source.write_bytes(b"x")
        assert service.import_accounts("pw", str(source))["ok"] is False
