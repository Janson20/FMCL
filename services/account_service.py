"""账号与皮肤服务（阶段 3 任务 3.1 的读侧 + 3.5 的写侧）。

## 范围

3.1 的首页与侧边栏要的是**只读**的账号信息与皮肤操作，所以那一轮只落了读侧；
3.5（账号管理页）在本文件上补齐**写侧**：

| 对照表 | 能力 | 旧实现位置 |
|--------|------|-----------|
| B-16 | 当前账号（名称 / 类型）+ 管理入口的存在性判断 | `ui/app_base.py:925-947` |
| B-17 | 皮肤尺寸校验 → 复制到 `.minecraft/skins/` → 写 `skin_path` → 移除 | `ui/app_base.py:972-1026` |
| A-25 | 启动时批量刷新微软账号 Token（读侧那一半）+ 3.5 新增的「全部刷新」 | `main.py:103-113, 429` |
| M-13 | 账号页的账号信息与入口 | `ui/windows/launcher_settings.py:574-616` |
| M-26 | 添加账号：微软 / 离线 / 外置登录 | `ui/windows/account_manager.py:36-174` |
| M-27 | 账号列表（类型、当前标记、UUID、空态） | `ui/windows/account_manager.py:303-421` |
| M-28 | 设为当前 / 删除 / 刷新 Token | `ui/windows/account_manager.py:390-421, 473-495` |
| M-29 | 导入 / 导出账号文件 | `ui/windows/account_manager.py:262-284, 497-560` |

## 三条设计取舍（阶段 3.5）

1. **长任务只在这里起线程，界面永远不等**。微软登录（浏览器 OAuth）最长
   `OAUTH_TIMEOUT_SECONDS` 秒、外置登录一次 HTTP、批量刷新 N 次 HTTP —— 这些都是
   `begin_*` 系列方法，方法**立刻返回**，结果经 `emit` 回调抛出。回调在**工作线程**
   上执行，桥负责转成 Qt 信号（Qt 的信号跨线程是队列投递，安全）。
2. **取消用 `threading.Event`**（`begin_cancel()` 由界面线程置位）。取消只在核心层的
   安全点生效（见 `launcher/account.py::_sleep_or_cancel`），本层只负责"建事件、传下去、
   把'已取消'如实回报"。取消**不算失败**：结果里的 `cancelled` 优先于 `ok`。
3. **进度回调尽量少、每次都有意义**：核心层的中文状态串（`正在打开浏览器...`）**逐字**转出，
   不做二次包装（与 `16` §17.3 对 `settings` 那批硬编码中文的处理一致：原样透出，
   挂账阶段 4 统一收口 i18n）。

## 为什么账号系统要单独包一层

`launcher/account.py` 的 ``GlobalAccountSystem`` 是个全局单例
（``init_account_system`` / ``get_account_system``），而且**界面侧还有一处历史坑**：
旧代码在四个窗口里各写了一遍 `type_labels` 字典来把 ``AccountType`` 翻成 i18n 键
（`ui/app_base.py:934-938` 是其中一份）。这里把"类型 → i18n 键"的映射**收口到一处**
（``ACCOUNT_TYPE_KEYS``），界面只拿键、不自己拼。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from services.base import Service

#: 账号类型 → i18n 键。收口点见模块文档（旧实现散在四个窗口里）。
ACCOUNT_TYPE_KEYS: Dict[str, str] = {
    "microsoft": "account_type_microsoft",
    "offline": "account_type_offline",
    "yggdrasil": "account_type_yggdrasil",
}

#: 允许的皮肤尺寸（旧 `ui/app_base.py:987`；**注意旧提示文案只提了两种**，
#: 文案键 ``skin_size_invalid`` 逐字保留不动 —— 改文案是行为变更，要单独裁决）。
SKIN_SIZES: Tuple[Tuple[int, int], ...] = ((64, 64), (64, 32), (128, 128), (128, 64))

#: 加账号的三种方式（`begin_login` 的 `kind`）。与旧窗口的三个按钮一一对应。
LOGIN_KINDS: Tuple[str, ...] = ("microsoft", "offline", "yggdrasil")

#: 账号导出文件（旧 `account_manager.py:520` 的 `("FMCL Accounts", "*.fmcl_accounts")`）。
ACCOUNT_FILE_SUFFIX = ".fmcl_accounts"
ACCOUNT_FILE_FILTER = "*.fmcl_accounts"

#: 进度记录里 `phase` 的取值。`running` = 进行中、`login_ok` / `login_failed` / `cancelled` = 终态。
PHASE_RUNNING = "running"
PHASE_LOGIN_OK = "login_ok"
PHASE_LOGIN_FAILED = "login_failed"
PHASE_CANCELLED = "cancelled"
PHASE_REFRESHED = "refreshed"

#: `progress()` 的监听器类型：`(record: dict) -> None`，在**调用它的线程**上执行。
ProgressListener = Callable[[Dict[str, Any]], None]


class AccountService(Service):
    """账号读侧 + 写侧 + 皮肤 + Token 刷新。"""

    name = "account"
    label = "账号与皮肤"

    def __init__(self, context: Any = None, *, system: Any = None, launcher: Any = None) -> None:
        """
        Args:
            context: ``AppContext``。
            system: 显式注入的 ``GlobalAccountSystem``。**测试用**；生产路径为
                None，届时懒调 ``launcher.account.get_account_system()``
                （旧界面的取法完全一致，见 `ui/app_base.py:927-931`）。
            launcher: 显式注入的 ``MinecraftLauncher``（皮肤路径存在它的 config 上）。
        """
        super().__init__(context)
        self._system = system
        self._launcher = launcher
        #: 进度监听器（桥注册；见 `add_listener`）。列表本身在**主线程**改、在工作线程读，
        #: 用锁保护 —— 界面在登录进行中关页会 `remove_listener`。
        self._listeners: List[ProgressListener] = []
        self._listener_lock = threading.Lock()
        #: 当前登录/刷新的取消事件。`begin_cancel()` 置位，`begin_*` 收尾时丢弃。
        self._cancel_event: Optional[threading.Event] = None
        self._cancel_lock = threading.Lock()

    # ─── 装配 ───────────────────────────────────────────────

    def use_system(self, system: Any) -> None:
        self._system = system

    def use_launcher(self, launcher: Any) -> None:
        self._launcher = launcher

    @property
    def system(self) -> Any:
        """账号系统单例；不可用时返回 None（界面据此显示"未选择账号"）。"""
        if self._system is None:
            try:
                from launcher.account import get_account_system

                self._system = get_account_system()
            except Exception as e:  # noqa: BLE001 - 账号模块坏掉不该让首页打不开
                self.log.warning("取账号系统失败: %s", e)
                return None
        return self._system

    # ─── 装配：把核心接到账号系统上（旧 main.py:337-455 的接线段）──

    def attach_launcher(self, launcher: Any, ui_port: Any = None) -> bool:
        """把启动器核心与账号系统接起来。返回是否接上了账号系统。

        这一步**不能省**：``MinecraftLauncher.launch_game()`` 里
        ``if self._account_system:`` 才是登录凭据的唯一来源，没注入就静默退化成
        ``config.player_name`` 的离线身份（`launcher/core.py:1025-1045`）。
        旧入口把这段写在 ``main.py`` 的 ``_on_launcher_ready()`` 里，本方法是它的
        一比一对应物；顺序也一致（端口 → 旧配置迁移 → 取系统 → 注入）。

        Args:
            launcher: ``MinecraftLauncher`` 实例。
            ui_port: UI 能力端口（账号模块的登录流程要弹窗/问密码）；
                None 时跳过注入（旧实现同样允许核心侧缺端口降级）。

        Raises:
            不抛异常：任一子步骤失败只记日志 —— 账号接不上时界面仍要能用（离线身份）。
        """
        if launcher is None:
            return False
        self._launcher = launcher

        if ui_port is not None:
            try:
                from launcher.account import set_ui_port as set_account_ui_port

                set_account_ui_port(ui_port)
            except Exception as e:  # noqa: BLE001
                self.log.warning("UI 端口注入账号模块失败: %s", e)

        # 旧配置迁移（`player_name` → 离线账号；一个账号都没有时建默认 Steve）。
        # 必须在取账号系统**之前**：`migrate_accounts()` 内部会 `init_account_system()`。
        try:
            migrate = getattr(self.config, "migrate_accounts", None)
            if callable(migrate):
                migrate()
        except Exception as e:  # noqa: BLE001 - 迁移失败不影响启动（旧实现同）
            self.log.warning("账号迁移失败（不影响启动）: %s", e)

        system = self.system
        if system is None:
            return False
        setter = getattr(launcher, "set_account_system", None)
        if callable(setter):
            try:
                setter(system)
            except Exception as e:  # noqa: BLE001
                self.log.warning("账号系统注入启动器失败: %s", e)
                return False
        return True

    # ─── B-16 当前账号 ──────────────────────────────────────

    def current_account(self) -> Optional[Dict[str, Any]]:
        """当前账号的只读快照；未选账号返回 None。"""
        system = self.system
        if system is None:
            return None
        try:
            account = system.current_account
        except Exception as e:  # noqa: BLE001
            self.log.warning("读当前账号失败: %s", e)
            return None
        if account is None:
            return None
        return self.account_snapshot(account)

    @staticmethod
    def account_snapshot(account: Any) -> Dict[str, Any]:
        """把一个 ``Account`` 归一成界面要的字典（**不碰令牌**）。"""
        type_value = getattr(getattr(account, "account_type", None), "value", "") or ""
        return {
            "id": str(getattr(account, "id", "") or ""),
            "name": str(getattr(account, "name", "") or ""),
            "type": type_value,
            "type_key": ACCOUNT_TYPE_KEYS.get(type_value, ""),
            "uuid": str(getattr(account, "uuid", "") or ""),
            "display_name": str(getattr(account, "display_name", "") or getattr(account, "name", "") or ""),
        }

    def account_summary(self) -> Dict[str, Any]:
        """首页/侧边栏用的账号摘要（未选账号时 ``has_account=False``）。"""
        snapshot = self.current_account()
        if snapshot is None:
            return {"has_account": False, "id": "", "name": "", "type": "", "type_key": "", "uuid": ""}
        snapshot["has_account"] = True
        return snapshot

    def accounts(self) -> list:
        """全部账号快照（3.5 的账号卡片列表要用；首页只用来判断"有没有账号"）。"""
        system = self.system
        if system is None:
            return []
        try:
            return [self.account_snapshot(a) for a in system.accounts]
        except Exception as e:  # noqa: BLE001
            self.log.warning("读账号列表失败: %s", e)
            return []

    def set_current_account(self, account_id: str) -> bool:
        """切换当前账号（旧 `GlobalAccountSystem.set_current_account` 的一行委托）。"""
        system = self.system
        if system is None:
            return False
        try:
            return bool(system.set_current_account(account_id))
        except Exception as e:  # noqa: BLE001
            self.log.error("切换当前账号失败: %s", e)
            return False

    def remove_account(self, account_id: str) -> bool:
        """删除账号（**确认框在界面侧**：旧实现的二次确认在窗口里，M-28）。

        账号不存在时返回 False（旧实现 `get_account` 取不到就直接 return）。
        """
        system = self.system
        if system is None:
            return False
        try:
            return bool(system.remove_account(account_id))
        except Exception as e:  # noqa: BLE001 - 删账号失败要如实回报，不能让页面崩
            self.log.error("删除账号失败: %s", e)
            return False

    def has_duplicate_name(self, name: str, kind: str) -> bool:
        """同名（且同类型）账号是否已存在 —— 只用于**提醒**，不拦截创建。

        用户 2026-10-06 裁决：旧实现只校验非空，`accounts.json` 允许存同名两个，
        所以迁移后也允许，仅多一句提示（属新增，已登记在对照表 M-26 备注）。
        """
        wanted = str(name or "").strip()
        if not wanted:
            return False
        return any(a["name"] == wanted and a["type"] == kind for a in self.accounts())

    def account_count(self) -> int:
        """账号总数（比 `len(accounts())` 少一次快照构造，列表刷新时用）。"""
        system = self.system
        if system is None:
            return 0
        try:
            return len(system.accounts)
        except Exception as e:  # noqa: BLE001
            self.log.warning("读账号数量失败: %s", e)
            return 0

    # ─── M-26 登录（长任务：立刻返回，结果经 emit 回调抛出）──────

    def add_listener(self, listener: ProgressListener) -> None:
        """登记进度监听器（桥用）。重复登记同一函数不会加两次。"""
        if listener is None:
            return
        with self._listener_lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: ProgressListener) -> None:
        """注销进度监听器（页面销毁时调；不注销会让回调打到已死对象上）。"""
        with self._listener_lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def begin_cancel(self) -> bool:
        """请求取消当前登录/刷新。返回是否真的发给了某个进行中的任务。

        线程安全：`threading.Event.set()` 可以从任意线程调（界面线程点按钮）。
        """
        with self._cancel_lock:
            event = self._cancel_event
        if event is None:
            return False
        event.set()
        return True

    def busy(self) -> bool:
        """是否有登录/刷新在进行（界面据此禁用按钮）。"""
        with self._cancel_lock:
            return self._cancel_event is not None

    def start_login(self, kind: str, params: Optional[Dict[str, Any]] = None) -> str:
        """开始一次登录。**立刻返回**；结果与进度经 `emit` 回调抛出。

        Args:
            kind: `microsoft` / `offline` / `yggdrasil`。
            params: `offline` 要 `name`；`yggdrasil` 要 `server_url` / `username` / `password`。

        Returns:
            `""` 表示已经开始了；否则返回一个 i18n 键说明为什么没开始
            （`account_name_required` / `account_ygg_fields_required` / `account_login_failed`）。
            参数校验在**这里**做（旧实现在对话框的按钮处理里，`account_manager.py:158-174`），
            界面只管把字段原样递进来。
        """
        kind = str(kind or "").strip()
        params = dict(params or {})
        if self.system is None:
            return "account_login_failed"
        if kind == "offline":
            name = str(params.get("name", "")).strip()
            if not name:
                return "account_name_required"
            params = {"name": name}
        elif kind == "yggdrasil":
            server_url = str(params.get("server_url", "")).strip()
            username = str(params.get("username", "")).strip()
            password = str(params.get("password", ""))
            if not server_url or not username or not password:
                return "account_ygg_fields_required"
            params = {"server_url": server_url, "username": username, "password": password}
        elif kind == "microsoft":
            params = {}
        else:
            self.log.error("未知的登录方式: %s", kind)
            return "account_login_failed"

        if not self._install_cancel_event():
            return "account_login_failed"  # 已有任务在跑（界面本该禁用按钮）
        threading.Thread(
            target=self._run_login, args=(kind, params), name=f"account-login-{kind}", daemon=True
        ).start()
        return ""

    def _run_login(self, kind: str, params: Dict[str, Any]) -> None:
        """登录工作线程体。**绝不碰 Qt/QML**（迁移红线 3）。"""
        result: Dict[str, Any] = {"ok": False, "kind": kind, "cancelled": False, "name": "", "reason": ""}
        try:
            #: `message_key` 是 **i18n 键**（界面翻成当前语言，免得进度条空着）；
            #: 之后核心层回调来的 `message` 是**已经成句的中文**，界面原样显示。
            #: 两者用**不同的字段**表达，界面就不会把键名当文案印出来
            #: （2026-10-06 用户验收报的 `account_ms_verifying` 直接显示，就是这个坑）。
            self._emit(kind=kind, phase=PHASE_RUNNING, message="", message_key=self._status_key(kind),
                       current=0, total=1)
            account = self._call_login(kind, params)
            if account is None:
                result["cancelled"] = self._cancel_requested()
                result["reason"] = PHASE_CANCELLED if result["cancelled"] else PHASE_LOGIN_FAILED
            else:
                snapshot = self.account_snapshot(account)
                result["ok"] = True
                result["name"] = snapshot["name"]
                result["id"] = snapshot["id"]
                result["type"] = snapshot["type"]
                if kind == "offline":
                    # 阶段 1.23（D-110）：新建离线账号 == 给角色起名。
                    # 旧界面把成就触发挂在窗口回调上（`ui/app_base.py:965`），
                    # 这里挂在**服务**上 —— 任何界面（含将来的 CLI）建离线账号都会触发。
                    self._trigger_ach("personalize_rename")
        except Exception as e:  # noqa: BLE001 - 登录异常不该让线程带着栈崩掉
            self.log.error("%s 登录异常: %s", kind, e)
            result["reason"] = PHASE_LOGIN_FAILED
            result["error"] = str(e)
        finally:
            self._clear_cancel_event()
        phase = PHASE_CANCELLED if result["cancelled"] else (PHASE_LOGIN_OK if result["ok"] else PHASE_LOGIN_FAILED)
        result["phase"] = phase
        # 终态把结果**摊平**进记录（`ok` / `name` / `type` / `id` / `cancelled` / `phase` 都在顶层），
        # 免得每个监听器都写一遍 `record["result"]["..."]`。
        result.setdefault("kind", kind)
        result.setdefault("message", "")
        result.setdefault("current", 1)
        result.setdefault("total", 1)
        self._emit(**result)
    def _call_login(self, kind: str, params: Dict[str, Any]) -> Any:
        """按方式调核心层（三种登录都带同一套取消语义）。"""
        system = self.system
        event = self._current_cancel_event()
        callback = self._status_callback(kind)
        if kind == "microsoft":
            return system.microsoft_login(status_callback=callback, cancel_event=event)
        if kind == "offline":
            return system.offline_login(params["name"])
        return system.yggdrasil_login(
            params["server_url"], params["username"], params["password"],
            status_callback=callback, cancel_event=event,
        )

    # ─── M-28 刷新 Token ────────────────────────────────────

    def refresh_token(self, account_id: str) -> Tuple[bool, bool]:
        """刷新单个账号的 Token。返回 ``(是否成功, 是否被取消)``。

        旧实现只有微软账号可刷（`refresh_account_token` 里非微软直接返回 True）——
        服务的 `refresh_token_succeeded()` 把"非微软"如实当成成功（与旧界面同）。
        """
        system = self.system
        if system is None:
            return False, False
        account = self._find_account(account_id)
        if account is None:
            return False, False
        try:
            ok = bool(system.refresh_account_token(account))
        except Exception as e:  # noqa: BLE001
            self.log.error("刷新 Token 失败: %s", e)
            return False, False
        return ok, False

    def refresh_token_succeeded(self, account_id: str) -> bool:
        """刷新后该账号的 Token 是否可用（微软看 `access_token` 是否还在且没过期）。"""
        account = self._find_account(account_id)
        if account is None:
            return False
        try:
            if getattr(account, "account_type", None).value != "microsoft":  # type: ignore[union-attr]
                return True
            return not account.is_token_expired(buffer_seconds=0)
        except Exception as e:  # noqa: BLE001
            self.log.debug("判断 Token 状态失败: %s", e)
            return False

    def start_refresh_all(self) -> bool:
        """开始「全部刷新 Token」（强制刷新，不判是否过期）。立刻返回。"""
        if self.system is None:
            return False
        if not self._install_cancel_event():
            return False
        threading.Thread(target=self._run_refresh_all, name="account-refresh-all", daemon=True).start()
        return True

    def _run_refresh_all(self) -> None:
        """批量刷新工作线程体。逐个账号回报进度，可取消。"""
        result: Dict[str, Any] = {"ok": False, "cancelled": False, "total": 0,
                                 "success": 0, "failed": 0, "skipped": 0}
        try:
            on_account = self._refresh_progress_callback()
            summary = self.system.refresh_all_account_tokens(
                on_account=on_account, cancel_event=self._current_cancel_event()
            )
            result.update(summary or {})
            result["ok"] = bool(result.get("success", 0)) or not result.get("total", 0)
        except Exception as e:  # noqa: BLE001
            self.log.error("全部刷新 Token 异常: %s", e)
            result["error"] = str(e)
        finally:
            self._clear_cancel_event()
        phase = PHASE_CANCELLED if result.get("cancelled") else PHASE_REFRESHED
        result["phase"] = phase
        result.setdefault("kind", "refresh_all")
        result.setdefault("message", "")
        result.setdefault("current", result.get("total", 0))
        self._emit(**result)

    # ─── M-29 导入 / 导出 ───────────────────────────────────

    def export_accounts(self, password: str, target_path: str) -> Dict[str, Any]:
        """把全部账号加密导出到文件（同步：PBKDF2 60 万轮，约 0.2 秒）。

        Returns:
            ``{"ok", "path", "error"}`；`error` 是 i18n 键或系统错误串
            （`account_export_failed` / `account_export_password_mismatch` 由界面判，
            这里只负责"能不能写出文件"）。
        """
        result: Dict[str, Any] = {"ok": False, "path": str(target_path or ""), "error": ""}
        system = self.system
        if system is None:
            result["error"] = "account_export_failed"
            return result
        if not password:
            result["error"] = "account_export_password_mismatch"
            return result
        try:
            data = system.export_accounts(password)
        except Exception as e:  # noqa: BLE001
            self.log.error("导出账号失败: %s", e)
            result["error"] = str(e)
            return result
        if not data:
            result["error"] = "account_export_failed"
            return result
        path = Path(str(target_path or ""))
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except Exception as e:  # noqa: BLE001 - 写不进去只报错（旧实现弹 str(e)）
            self.log.error("写账号导出文件失败: %s", e)
            result["error"] = str(e)
            return result
        result["ok"] = True
        result["bytes"] = len(data)
        self.log.info("账号已导出到 %s（%d 字节）", path, len(data))
        return result

    def import_accounts(self, password: str, source_path: str) -> Dict[str, Any]:
        """从文件导入账号（同步）。

        Returns:
            ``{"ok", "count", "error"}`。`count` 的语义与核心层一致：**-1 = 密码错误
            或文件损坏**、0 = 没有可导入的条目、>0 = 导入条数（含覆盖同 id 的）。
            `error` 为 `account_import_password_error` 时界面弹旧文案"密码错误或文件已损坏"。
        """
        result: Dict[str, Any] = {"ok": False, "count": 0, "error": ""}
        system = self.system
        if system is None:
            result["error"] = "account_import_password_error"
            return result
        try:
            data = Path(str(source_path or "")).read_bytes()
        except Exception as e:  # noqa: BLE001 - 旧实现弹 str(e)
            self.log.error("读账号导入文件失败: %s", e)
            result["error"] = str(e)
            return result
        try:
            count = int(system.import_accounts(password, data))
        except Exception as e:  # noqa: BLE001
            self.log.error("导入账号失败: %s", e)
            result["error"] = str(e)
            return result
        if count < 0:
            result["error"] = "account_import_password_error"
            return result
        result["ok"] = True
        result["count"] = count
        return result

    def open_in_explorer(self, target_path: str) -> Tuple[bool, str]:
        """用系统文件管理器打开目录（导出成功后那颗「打开所在目录」按钮）。

        Returns:
            ``(是否成功, 错误串)``。平台分支与 `services/log_service.py::_open_path` 同一套：
            Windows `os.startfile` / macOS `open` / 其它 `xdg-open`。
        """
        directory = str(Path(str(target_path or "")).parent)
        if not directory or directory == ".":
            return False, "account_export_failed"
        try:
            _open_path(directory)
        except Exception as e:  # noqa: BLE001 - 打不开目录只报错
            self.log.warning("打开导出目录失败: %s", e)
            return False, str(e)
        return True, ""

    # ─── A-25 启动时批量刷新 Token ──────────────────────────

    def auto_refresh_tokens(self) -> int:
        """批量刷新过期 Token，返回刷新成功的账号数（失败返回 0，不抛异常）。"""
        system = self.system
        if system is None:
            return 0
        try:
            return int(system.auto_refresh_all_tokens() or 0)
        except Exception as e:  # noqa: BLE001 - 旧实现同样只记日志
            self.log.warning("启动时自动刷新 Token 失败（不影响正常使用）: %s", e)
            return 0

    # ─── B-17 皮肤 ──────────────────────────────────────────

    def skin_path(self) -> str:
        """当前自定义皮肤路径（空串表示没有）。"""
        launcher = self._resolve_launcher()
        if launcher is None:
            return ""
        try:
            return str(launcher.get_skin_path() or "")
        except Exception as e:  # noqa: BLE001
            self.log.warning("读皮肤路径失败: %s", e)
            return ""

    def select_skin(self, file_path: str) -> Tuple[bool, str, Dict[str, Any]]:
        """选择皮肤：校验尺寸 → 写配置 → 复制到 ``.minecraft/skins/``。

        Returns:
            ``(是否成功, i18n 键, 参数)``。失败时键是 ``skin_size_invalid`` /
            ``skin_file_error``，成功时是 ``skin_installed``（带 ``filename``）。
            **不在这里做翻译** —— 界面拿键去翻（QML 走 ``Tr.map``，旧界面走 ``_``）。
        """
        path = Path(str(file_path or ""))
        if not path.is_file():
            return False, "skin_file_error", {}

        ok, key, params = self._validate_skin(path)
        if not ok:
            return False, key, params

        launcher = self._resolve_launcher()
        if launcher is None:
            return False, "skin_file_error", {}

        # 顺序与旧实现一致：先写配置、再复制（复制失败也已经"选中"了）
        try:
            launcher.set_skin_path(str(path))
        except Exception as e:  # noqa: BLE001
            self.log.error("写入皮肤路径失败: %s", e)
            return False, "skin_file_error", {}

        try:
            skin_dir = Path(launcher.get_minecraft_dir()) / "skins"
            skin_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(path), str(skin_dir / path.name))
        except Exception as e:  # noqa: BLE001 - 旧实现这里没兜异常，但不该让首页崩
            self.log.error("复制皮肤到 .minecraft/skins 失败: %s", e)
            return False, "skin_file_error", {}

        self._trigger_ach("personalize_skin")
        return True, "skin_installed", {"filename": path.name}

    def remove_skin(self) -> None:
        """移除自定义皮肤（清空 ``skin_path``，不删已复制到 skins/ 的文件 —— 旧实现同）。"""
        launcher = self._resolve_launcher()
        if launcher is None:
            return
        try:
            launcher.set_skin_path(None)
        except Exception as e:  # noqa: BLE001
            self.log.error("清除皮肤路径失败: %s", e)

    def _validate_skin(self, path: Path) -> Tuple[bool, str, Dict[str, Any]]:
        """尺寸校验。PIL 缺席时**跳过校验**（旧实现同：`ImportError: pass`）。"""
        try:
            from PIL import Image
        except ImportError:
            return True, "", {}
        try:
            with Image.open(path) as img:
                width, height = img.size
        except Exception as e:  # noqa: BLE001 - 打不开的图片 = 文件错误
            self.log.warning("读取皮肤文件失败: %s", e)
            return False, "skin_file_error", {}
        if (width, height) not in SKIN_SIZES:
            return False, "skin_size_invalid", {"width": width, "height": height}
        return True, "", {}

    # ─── 内部：进度 / 取消 / 账号检索 ───────────────────────

    def _emit(self, **record: Any) -> None:
        """通知所有监听器。**在工作线程上执行**（桥负责转成 Qt 信号）。"""
        with self._listener_lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(record)
            except Exception as e:  # noqa: BLE001 - 监听器是界面的，坏掉不该打断任务
                self.log.warning("账号进度监听器异常: %s", e)

    def _status_callback(self, kind: str) -> Callable[[str], None]:
        """给核心层的 `status_callback`：把中文状态串原样转成一条 running 进度。

        `message` 走"原样透出"（核心层的那批中文，见模块文档第 3 条取舍），
        **不**放进 `message_key` —— 那不是键，界面不能拿去翻。
        """

        def report(message: str) -> None:
            self._emit(kind=kind, phase=PHASE_RUNNING, message=str(message or ""), message_key="",
                       current=0, total=1)

        return report

    def _refresh_progress_callback(self) -> Callable[[str, int, int], None]:
        """给核心层的逐个账号刷新回调（`refresh_all_account_tokens(on_account=...)`）。"""

        def report(name: str, index: int, total: int) -> None:
            self._emit(kind="refresh_all", phase=PHASE_RUNNING, message="",
                       name=str(name or ""), current=int(index), total=int(total))

        return report

    @staticmethod
    def _status_key(kind: str) -> str:
        """登录刚开始时先给界面一句 i18n 键（界面翻成当前语言），免得空着。"""
        if kind == "microsoft":
            return "account_ms_verifying"
        return "logging_in"

    def _install_cancel_event(self) -> bool:
        """建一个新的取消事件；已有任务在跑时返回 False（不抢占）。"""
        with self._cancel_lock:
            if self._cancel_event is not None:
                return False
            self._cancel_event = threading.Event()
            return True

    def _clear_cancel_event(self) -> None:
        with self._cancel_lock:
            self._cancel_event = None

    def _current_cancel_event(self) -> Optional[threading.Event]:
        with self._cancel_lock:
            return self._cancel_event

    def _cancel_requested(self) -> bool:
        event = self._current_cancel_event()
        return bool(event is not None and event.is_set())

    def _find_account(self, account_id: str) -> Any:
        """按 id 取**核心层的 Account 对象**（刷新/删除要它，不能拿快照）。"""
        system = self.system
        if system is None:
            return None
        try:
            return system.get_account(str(account_id or ""))
        except Exception as e:  # noqa: BLE001
            self.log.warning("按 id 取账号失败: %s", e)
            return None

    def _resolve_launcher(self) -> Any:
        if self._launcher is None and self.attached:
            self._launcher = self.try_get("launcher")
        return self._launcher

    def _trigger_ach(self, achievement_id: str, value: int = 1, trigger_type: Optional[str] = None) -> None:
        """触发成就进度（与 GameService 同一套语义，见那边的说明）。"""
        try:
            from achievement_engine import get_achievement_engine

            engine = get_achievement_engine()
            if engine:
                engine.update_progress(achievement_id, value=value, trigger_type=trigger_type)
        except Exception as e:  # noqa: BLE001
            self.log.debug("触发成就 %s 失败: %s", achievement_id, e)


def _open_path(path: str) -> None:
    """按平台用系统默认方式打开目录（与 `services/log_service.py::_open_path` 同一套分支）。

    `os.startfile` 只有 Windows 有，所以按属性探测；测试要断言"真的去开目录"时
    请用 `AccountService.open_in_explorer` 的返回值，别去 mock 这个函数。
    """
    if hasattr(os, "startfile"):  # Windows
        os.startfile(path)  # type: ignore[attr-defined]  # noqa: S606 - 打开目录不是执行程序
        return
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])  # noqa: S603,S607 - 固定命令 + 用户自己的目录
        return
    subprocess.Popen(["xdg-open", path])  # noqa: S603,S607


__all__ = [
    "ACCOUNT_FILE_FILTER",
    "ACCOUNT_FILE_SUFFIX",
    "ACCOUNT_TYPE_KEYS",
    "LOGIN_KINDS",
    "PHASE_CANCELLED",
    "PHASE_LOGIN_FAILED",
    "PHASE_LOGIN_OK",
    "PHASE_REFRESHED",
    "PHASE_RUNNING",
    "SKIN_SIZES",
    "AccountService",
]
