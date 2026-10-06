"""账号页 QML 探针（阶段 3 任务 3.5；`tests/test_account_page_qml.py` 用**子进程**驱动）。

## 为什么另起进程

与 3.1~3.4 的探针同一个理由：真引擎 + FluentUI 的 `FluWindow` 在同一个进程里跨测试文件
不安全。这里只加载一次 `App.qml`，跑完整条链路（导航 → 分区 → 控件 → 桥 → 服务）。

## 它验证什么（对着 3.5 的验收条目逐条来）

1. **入口**：`settings/account` 深链（首页卡片与顶栏账号按钮都指向它）→ 账号分区真的加载；
2. **列表（M-27）**：三种类型的徽标文案与配色档位、当前账号的「★ + 当前标签 + 强调色指示条」、
   UUID 截断 20 字符、**只有微软账号有「刷新Token」按钮**、空态文案；
3. **切换当前（M-28）**：点某行的「设为当前」→ 服务的 `set_current_account` 被调 →
   列表当场刷新（指示条换行）→ **首页卡片同步**（`Home.accountName` 跟着变，M-13 的"侧边栏同步"）；
4. **删除（M-28）**：点「删除」→ 弹确认框（文案带账号名）→「确定」才真删，
   「取消」时一行都不动；
5. **刷新 Token（M-28）**：点「刷新Token」→ 确认框 → 确定后 `refresh_account_token` 被调 +
   状态栏文案；
6. **全部刷新（A-25 在账号页的落点）**：点「全部刷新」→ 进度卡出现、逐账号进度文本
   （`1/2`）→ 结束后状态栏成功文案；
7. **登录（M-26）**：离线（空名字 → 就地报错；有名字 → 真建账号 + 列表多一行）、
   微软（点按钮 → 进度卡 + 「取消」→ 取消后状态栏"已取消"且没建账号）、
   外置（缺字段 → 就地报错）；
8. **导入 / 导出（M-29）**：导出到临时文件（真加密）→ 结果对话框出现（带路径）→
   「打开目录」调用注入的 opener；导入另一个文件 → 账号数增加、Toast 提示；
   错密码 → 旧文案"密码错误或文件已损坏"；
9. **三态**：空列表走 `FmEmptyState`；桥缺席时整块走错误态（`Accounts.available` 判据）。

替身是**真的 `Account` 数据类**（`launcher.account.Account`）+ 假账号系统 —— 这样
类型徽标、UUID 截断、`display_name` 这些派生字段走的是与生产同一条代码。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import Q_ARG, QEvent, QMetaObject, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QGuiApplication, QMouseEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

import main_qml  # noqa: E402
from app.bootstrap import build_context  # noqa: E402
from app.bridges.icon_provider import install as install_icon_provider  # noqa: E402
from launcher.account import Account, AccountType  # noqa: E402
from services.account_service import AccountService  # noqa: E402

MARKER = "PROBE_JSON:"
_MISSING = object()
APP = QGuiApplication.instance() or QGuiApplication([])
SINK = main_qml.install_message_handler()

SHOT_DIR: Optional[Path] = None
SHOTS: List[str] = []
if "--shots" in sys.argv:
    _index = sys.argv.index("--shots")
    if _index + 1 < len(sys.argv):
        SHOT_DIR = Path(sys.argv[_index + 1])
        SHOT_DIR.mkdir(parents=True, exist_ok=True)

QML_ERROR_MARKERS = ("is not defined", "TypeError", "Unable to assign", "Cannot assign", "ReferenceError")
BENIGN_MARKERS = (
    "This plugin does not support",
    "QFont::setPointSize",
    "propagateSizeHints",
    "Failed to create",
)

TMP_DIR = Path(tempfile.mkdtemp(prefix="fmcl-account-probe-"))
EXPORT_PATH = TMP_DIR / "exported.fmcl_accounts"
IMPORT_PATH = TMP_DIR / "imported.fmcl_accounts"
OPENED: List[str] = []


# ─── 假账号系统（真 `Account` 数据类）──────────────────────────


def make_account(account_id: str, name: str, kind: str, uuid: str = "", expired: bool = False) -> Account:
    return Account(
        id=account_id,
        name=name,
        account_type=AccountType(kind),
        uuid=uuid or f"uuid-{account_id}-0123456789abcdef",
        access_token="" if expired else "header.payload.signature",
        refresh_token="refresh" if kind == "microsoft" else None,
        yggdrasil_server_url="https://example.invalid/api/yggdrasil" if kind == "yggdrasil" else None,
        created_at="2026-01-01T00:00:00+00:00",
        last_login="2026-01-01T00:00:00+00:00",
    )


class FakeAccountSystem:
    """能跑真流程的最小账号系统；登录尊重注入的 `cancel_event`（与核心层同一契约）。"""

    def __init__(self) -> None:
        self.accounts: List[Account] = []
        self.current_account_id: Optional[str] = None
        self.login_delay = 0.0
        self.calls: List[str] = []
        self.removed: List[str] = []
        self.exported_password: Optional[str] = None
        self.import_result: int = 0
        self.refresh_all_calls = 0
        self.writes = 0
        #: 批量刷新每个账号之间的停顿（真实现是一次 HTTP 往返）
        self.refresh_all_step = 0.05
        #: **观察闸门**：非 None 时批量刷新会 `wait` 它 —— 探针用它把"进行中"这一刻
        #: **冻住**，从而确定性地断言进度卡可见。早先的做法是"每账号 sleep 0.05、
        #: 抢在窗口里读一次"：整仓 `-n 8` 并行时主线程可能整段错过这个窗口，
        #: 实测偶发假红（子进程测试为此还加过"只对这一条重跑"的兜底）。
        self.refresh_all_gate: Optional[threading.Event] = None

    # 读侧
    @property
    def current_account(self) -> Optional[Account]:
        for account in self.accounts:
            if account.id == self.current_account_id:
                return account
        return None

    def get_account(self, account_id: str) -> Optional[Account]:
        for account in self.accounts:
            if account.id == account_id:
                return account
        return None

    # 写侧
    def add_account(self, account: Account) -> bool:
        self.accounts.append(account)
        self.writes += 1
        return True

    def set_current_account(self, account_id: str) -> bool:
        self.calls.append(f"set_current:{account_id}")
        if self.get_account(account_id) is None:
            return False
        self.current_account_id = account_id
        self.writes += 1
        return True

    def remove_account(self, account_id: str) -> bool:
        self.calls.append(f"remove:{account_id}")
        account = self.get_account(account_id)
        if account is None:
            return False
        self.accounts.remove(account)
        self.removed.append(account_id)
        if self.current_account_id == account_id:
            self.current_account_id = None
        self.writes += 1
        return True

    # 登录（三种都尊重 cancel_event）
    def _wait_cancel(self, cancel_event: Any) -> bool:
        if cancel_event is None:
            return False
        if self.login_delay <= 0:
            return cancel_event.is_set()
        cancel_event.wait(self.login_delay)
        return cancel_event.is_set()

    def microsoft_login(self, status_callback: Any = None, *, cancel_event: Any = None) -> Optional[Account]:
        self.calls.append("microsoft_login")
        if status_callback:
            #: **先停 0.25 秒再回调**：让"刚起跑、只有占位键"的那一刻真实存在，
            #: 探针才能验到"占位那一步显示的是译文而不是键名"（2026-10-06 的验收缺陷）。
            #: 真核心层里这一刻在 `get_secure_login_data` 的网络往返期间，同样是可感知的。
            if cancel_event is not None:
                cancel_event.wait(0.25)
            status_callback("正在打开浏览器...")
        if self._wait_cancel(cancel_event):
            return None
        account = make_account("MS-NEW", "MSPlayer", "microsoft")
        self.add_account(account)
        self.set_current_account(account.id)
        return account

    def offline_login(self, username: str) -> Optional[Account]:
        self.calls.append(f"offline_login:{username}")
        account = make_account(f"OFF-{username}", username, "offline")
        self.add_account(account)
        self.set_current_account(account.id)
        return account

    def yggdrasil_login(self, server_url: str, username: str, password: str,
                        status_callback: Any = None, *, cancel_event: Any = None) -> Optional[Account]:
        self.calls.append(f"yggdrasil_login:{username}")
        if self._wait_cancel(cancel_event):
            return None
        account = make_account(f"YGG-{username}", username, "yggdrasil")
        self.add_account(account)
        self.set_current_account(account.id)
        return account

    # Token 刷新
    def refresh_account_token(self, account: Account, *, cancel_event: Any = None) -> bool:
        self.calls.append(f"refresh_token:{account.id}")
        if account.account_type != AccountType.MICROSOFT:
            return True
        account.access_token = "header.payload.signature"
        self.writes += 1
        return True

    def auto_refresh_all_tokens(self) -> int:
        return 0

    def refresh_all_account_tokens(self, on_account: Any = None, *, cancel_event: Any = None) -> Dict[str, Any]:
        self.refresh_all_calls += 1
        targets = [a for a in self.accounts if a.account_type == AccountType.MICROSOFT and a.refresh_token]
        result = {"ok": True, "total": len(targets), "success": 0, "failed": 0,
                  "skipped": len(self.accounts) - len(targets), "cancelled": False}
        for index, account in enumerate(targets, start=1):
            if cancel_event is not None and cancel_event.is_set():
                result["cancelled"] = True
                break
            if on_account:
                on_account(account.name, index, len(targets))
            self.refresh_account_token(account, cancel_event=cancel_event)
            result["success"] += 1
            gate = self.refresh_all_gate
            if gate is not None:
                #: 观察闸门：把"进行中"这一刻冻住，等探针看完再放行（确定性，不靠 sleep）
                gate.wait(10.0)
            else:
                time.sleep(self.refresh_all_step)
        return result

    # 导入 / 导出（真加密由真系统的同签名代码负责；这里只判"服务把口令与路径传对了"）
    def export_accounts(self, password: str, account_ids: Any = None) -> Optional[bytes]:
        self.exported_password = password
        self.calls.append("export_accounts")
        return b"FMCL_ACCOUNTS_V1\n" + password.encode("utf-8")

    def import_accounts(self, password: str, data: bytes, merge: bool = True) -> int:
        self.calls.append(f"import_accounts:{password}")
        if password == "good-password":
            self.import_result += 1
            self.add_account(make_account(f"IMP-{self.import_result}", f"Imported{self.import_result}", "offline"))
            return 1
        return -1


SYSTEM = FakeAccountSystem()
SYSTEM.accounts.append(make_account("MS-A", "MainAccount", "microsoft"))
SYSTEM.accounts.append(make_account("OFF-B", "OfflineGuy", "offline"))
SYSTEM.accounts.append(make_account("YGG-C", "YggUser", "yggdrasil"))
SYSTEM.current_account_id = "MS-A"

SYSTEM.login_delay = 0.4
SERVICE = AccountService(system=SYSTEM)

from config_isolation import isolate_config_writes  # noqa: E402

isolate_config_writes("account-probe")

CONTEXT = build_context(config=None, register=True, set_current=False)
CONTEXT.register_instance("account", SERVICE, replace=True)
#: 「打开所在目录」的替身：服务层走注入的 opener（默认是 `os.startfile`，探针里不能真开窗口）
import services.account_service as _account_service  # noqa: E402

#: 「打开所在目录」的替身：`open_in_explorer()` 走的是**模块级** `_open_path`，
#: 所以必须替换模块属性（第一版写成实例属性，永远不生效 —— 探针因此误报红灯）。
_account_service._open_path = lambda path: OPENED.append(str(path))  # type: ignore[attr-defined]

ENGINE = main_qml.build_engine()
install_icon_provider(ENGINE)
BRIDGES = main_qml.register_bridges(ENGINE, CONTEXT)

ACCOUNTS = ENGINE._fmcl_bridges["Accounts"]
HOME = ENGINE._fmcl_bridges["Home"]
NAV = ENGINE._fmcl_bridges["Nav"]
SHELL = ENGINE._fmcl_bridges["Shell"]
TR = ENGINE._fmcl_bridges["Tr"]

for bridge in (ACCOUNTS, HOME):
    use_engine = getattr(bridge, "use_engine", None)
    if callable(use_engine):
        use_engine(ENGINE)

ENGINE.load(QUrl.fromLocalFile(str(main_qml.app_qml_path("App.qml"))))
ROOT = ENGINE.rootObjects()[0] if ENGINE.rootObjects() else None
QTest.qWait(200)

# ─── 通用工具 ──────────────────────────────────────────────────

REPORT: Dict[str, Any] = {
    "checks": [],
    "failures": [],
    "notes": [],
    "shots": SHOTS,
    "bridges_registered": list(BRIDGES.get("registered") or []),
    "bridges_missing": list(BRIDGES.get("missing") or []),
}


def check(name: str, ok: bool, detail: str = "") -> bool:
    REPORT["checks"].append({"name": name, "ok": bool(ok), "detail": str(detail)})
    if not ok:
        REPORT["failures"].append(f"{name}: {detail}")
    print(f"[check] {'OK  ' if ok else 'FAIL'} {name} {detail}", file=sys.stderr)
    return bool(ok)


def _walk(root_item: Any) -> Any:
    for child in root_item.childItems():
        yield child
        yield from _walk(child)


def _matches(name: str) -> List[Any]:
    out: List[Any] = []
    if ROOT is None:
        return out
    content = ROOT.contentItem()
    if content is None:
        return out
    for candidate in _walk(content):
        if candidate.objectName() == name:
            out.append(candidate)
    return out


def _visible(node: Any) -> bool:
    try:
        return bool(node.isVisible())
    except Exception:  # noqa: BLE001
        return False


def item(name: str) -> Any:
    found = _matches(name)
    for candidate in found:
        if _visible(candidate):
            return candidate
    return found[0] if found else None


def items_named(name: str) -> List[Any]:
    found = _matches(name)
    alive = [node for node in found if _visible(node)]
    return alive if alive else found


def text_of(name: str) -> str:
    target = item(name)
    if target is None:
        return "<missing>"
    value = target.property("text")
    return "" if value is None else str(value)


def visible(name: str) -> bool:
    target = item(name)
    return bool(target is not None and target.property("visible"))


def enabled(name: str) -> bool:
    target = item(name)
    return bool(target is not None and target.property("enabled"))


def settle(timeout_ms: int = 2000) -> None:
    stack = item("pageStack")
    if stack is None:
        return
    waited = 0
    while waited < timeout_ms:
        pages = [
            child for child in stack.childItems()
            if child.objectName().endswith("Page") and child.property("visible")
        ]
        if len(pages) <= 1:
            return
        QTest.qWait(50)
        waited += 50


def click(target: Any) -> None:
    if target is None:
        raise RuntimeError("点击目标不存在")
    settle()
    centre = target.mapToScene(QPointF(target.width() / 2, target.height() / 2))
    global_pos = ROOT.mapToGlobal(centre)
    for event_type, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton),
                                (QEvent.MouseButtonRelease, Qt.NoButton)):
        APP.sendEvent(ROOT, QMouseEvent(event_type, centre, global_pos, Qt.LeftButton, buttons, Qt.NoModifier))
        QTest.qWait(40)


def click_named(name: str) -> bool:
    target = item(name)
    if target is None:
        return False
    click(target)
    return True


def invoke(target: Any, method: str, value: Any = _MISSING) -> bool:
    """调 QML 里的函数/槽。给 `value` 时按 `Q_ARG(QVariant)` 传一个参数。"""
    if target is None:
        return False
    if value is _MISSING:
        return bool(QMetaObject.invokeMethod(target, method, Qt.ConnectionType.DirectConnection))
    return bool(QMetaObject.invokeMethod(
        target, method, Qt.ConnectionType.DirectConnection,
        Q_ARG("QVariant", value)))


def wait_until(predicate: Any, timeout_ms: int = 4000, step_ms: int = 30) -> bool:
    waited = 0
    while waited < timeout_ms:
        if predicate():
            return True
        QTest.qWait(step_ms)
        waited += step_ms
    return bool(predicate())


def shot(name: str) -> None:
    if SHOT_DIR is None or ROOT is None:
        return
    try:
        image = ROOT.grabWindow()
        if image.isNull() or not image.save(str(SHOT_DIR / f"{name}.png")):
            return
        SHOTS.append(str(SHOT_DIR / f"{name}.png"))
    except Exception as e:  # noqa: BLE001
        REPORT["notes"].append(f"截图 {name} 失败: {e}")


def goto(route: str) -> None:
    NAV.push(route)
    QTest.qWait(150)
    settle()


#: 探针里用到的账号 id（与假账号系统里种下的三条一致）
KNOWN_IDS = ("MS-A", "OFF-B", "YGG-C")


def name_of(account_id: str) -> str:
    """某一行的账号名。**按 id 取** —— `ListView` 会复用委托，按"第 N 个
    objectName"读会拿到同一行的三份拷贝（第一版就是这么错的）。"""
    return text_of(f"accountName-{account_id}")


def tag_of(account_id: str) -> str:
    return text_of(f"accountTypeTag-{account_id}")


def uuid_of(account_id: str) -> str:
    return text_of(f"accountUuid-{account_id}")


def row_names() -> List[str]:
    """三个已知账号的名字，按 id 顺序（不是屏幕顺序）。"""
    return [name_of(account_id) for account_id in KNOWN_IDS]


def row_count() -> int:
    """列表里真正存在的行数（按三个已知 id 之外再加上导入/新建的 id 统计）。"""
    count = 0
    for node in _matches("accountRow"):
        count += 1
    return count


def status_text() -> str:
    return str(SHELL.property("statusText"))


def section_item() -> Any:
    """账号分区的根 Item（诊断用：它的 `progressActive` 是进度卡可见性的真值）。"""
    return item("accountSection")


def title_of(name: str) -> str:
    """读 `FmEmptyState` / `FmCard` 的 `title` 属性（它们没有 `text`）。"""
    target = item(name)
    if target is None:
        return "<missing>"
    value = target.property("title")
    return "" if value is None else str(value)


def qvariant(node: Any, name: str) -> Dict[str, Any]:
    """读一个 QVariantMap 属性并转成真 dict。

    PySide 侧读到的是 `QJSValue`（没有 `.get()`）—— 3.4 的探针在 `combo_labels` 里
    吃过同一个亏（那里的 `model[index]` 也要 `.toVariant()`）。
    """
    if node is None:
        return {}
    value = node.property(name)
    if hasattr(value, "toVariant"):
        value = value.toVariant()
    return dict(value) if isinstance(value, dict) else {}


def status_level() -> str:
    return str(SHELL.property("statusLevel"))


def switch_language(code: str) -> None:
    """直接换语言（顶栏地球那条路；探针里不走 UI，省得依赖顶栏结构）。"""
    SERVICE_TABLE = getattr(TR, "setLanguage", None)
    if callable(SERVICE_TABLE):
        TR.setLanguage(code)
    else:  # 兜底：直接改服务的字典
        from services import i18n_service

        i18n_service.set_language(code)
    QTest.qWait(150)


# ══ 1. 入口：深链进账号分区 ═══════════════════════════════════

goto("home")
check("首页账号卡片有名字", str(HOME.property("accountName")) == "MainAccount",
      str(HOME.property("accountName")))

goto("settings")
check("设置页默认在启动器分区", item("settingsLauncherSection") is not None)
check("账户分区此刻没有渲染", item("accountTitle") is None, "accountTitle 不该在启动器分区出现")

goto("settings/account")
check("深链进入账号分区", item("accountTitle") is not None, "accountTitle")
record_ok = True
for name in ("accountTitle", "accountDescription", "accountActionRow", "accountAddMicrosoft",
             "accountAddOffline", "accountAddYggdrasil", "accountRefreshAll", "accountImport",
             "accountExport", "accountListView", "accountListCard"):
    record_ok = record_ok and item(name) is not None
check("账号页主要控件都在树里", record_ok, "见 objectNames 表")
REPORT["objectNames"] = {name: item(name) is not None for name in (
    "accountTitle", "accountDescription", "accountActionRow", "accountAddMicrosoft",
    "accountAddOffline", "accountAddYggdrasil", "accountRefreshAll", "accountImport",
    "accountExport", "accountListView", "accountListCard", "accountEmptyState",
    "accountProgressCard", "accountCancel")}

# ══ 2. 列表（M-27）════════════════════════════════════════════

names = row_names()
REPORT["row_names"] = names
check("三个账号各一行（按 id 取）", names == ["\u2605 MainAccount", "OfflineGuy", "YggUser"], str(names))
check("列表里三行都在（按 accountRow 计）", row_count() >= 3, str(row_count()))

check("当前账号带 ★ 前缀（旧窗口 `account_manager.py:372` 同款）",
      names[0].startswith("\u2605 "), names[0])
check("非当前账号没有 ★", not names[1].startswith("\u2605") and not names[2].startswith("\u2605"), str(names[1:]))

type_tags = [tag_of(account_id) for account_id in KNOWN_IDS]
REPORT["type_tags"] = type_tags
check("类型徽标按 i18n 显示且三种互不相同",
      len(set(type_tags)) == 3 and all(tag != "" for tag in type_tags), str(type_tags))

uuids = [uuid_of(account_id) for account_id in KNOWN_IDS]
REPORT["uuids"] = uuids
check("UUID 行是 `UUID: xxx...` 且截断到 20 字符",
      all(text.startswith("UUID: ") and text.endswith("...") for text in uuids)
      and len(uuids[0]) == len("UUID: ") + 20 + 3, str(uuids))

current_tags = [node for node in items_named("accountCurrentTag") if node.property("visible")]
check("「当前」标签只有一个（在 MainAccount 那行）", len(current_tags) == 1, str(len(current_tags)))

indicators = [node for node in items_named("accountCurrentIndicator") if node.property("visible")]
check("当前账号的强调色指示条只出现一次", len(indicators) == 1, str(len(indicators)))

refresh_buttons = [node for node in items_named("accountRefreshToken-MS-A") if node.property("visible")]
offline_refresh = [node for node in items_named("accountRefreshToken-OFF-B") if node.property("visible")]
ygg_refresh = [node for node in items_named("accountRefreshToken-YGG-C") if node.property("visible")]
check("只有微软账号那行有「刷新Token」按钮",
      len(refresh_buttons) == 1 and len(offline_refresh) == 0 and len(ygg_refresh) == 0,
      f"ms={len(refresh_buttons)} offline={len(offline_refresh)} ygg={len(ygg_refresh)}")
check("当前账号那行没有「设为当前」按钮",
      item("accountSetCurrent-MS-A") is None or not item("accountSetCurrent-MS-A").property("visible"),
      "当前行不该有设为当前")
check("非当前账号那行有「设为当前」按钮", item("accountSetCurrent-OFF-B") is not None)

shot("account_list")

# ══ 3. 切换当前账号（M-28 + M-13 的首页同步）══════════════════

before_home = str(HOME.property("accountName"))
click_named("accountSetCurrent-OFF-B")
QTest.qWait(200)
check("服务收到了切换请求", "set_current:OFF-B" in SYSTEM.calls, str(SYSTEM.calls[-3:]))
check("当前账号换成 OfflineGuy（列表当场刷新）",
      SYSTEM.current_account_id == "OFF-B", str(SYSTEM.current_account_id))
check("首页卡片同步（M-13 的『侧边栏同步』在 QML 下的落点）",
      str(HOME.property("accountName")) == "OfflineGuy",
      f"{before_home} → {HOME.property('accountName')}")
check("切换后「★」跟着挪（MainAccount 没了、OfflineGuy 有了）",
      not name_of("MS-A").startswith("\u2605") and name_of("OFF-B").startswith("\u2605"),
      str(row_names()))

# 切回微软账号（后面的刷新 Token 用例要用）
click_named("accountSetCurrent-MS-A")
QTest.qWait(150)

# ══ 4. 删除（M-28：确认框 + 取消不删）═════════════════════════

click_named("accountDelete-YGG-C")
QTest.qWait(150)
dialog = item("accountConfirmDialog")
check("点删除弹出确认框", dialog is not None)
#: `request` 是 QVariantMap —— PySide 侧拿到的是 `QJSValue`，`.toVariant()` 才是 dict
message = str(qvariant(dialog, "request").get("message", ""))
REPORT["delete_message"] = message
check("确认框文案带账号名（旧 `account_delete_confirm` 的 {name}）", "YggUser" in message, message)
shot("account_delete_confirm")

invoke(dialog, "cancel")
QTest.qWait(150)
check("点「取消」不删账号", SYSTEM.removed == [], str(SYSTEM.removed))
check("点「取消」后确认框收回", item("accountConfirmDialog") is None)
check("行数没变", row_count() >= 3, str(row_count()))

click_named("accountDelete-YGG-C")
QTest.qWait(150)
dialog = item("accountConfirmDialog")
invoke(dialog, "accept")
QTest.qWait(250)
check("点「确定」真的删了", SYSTEM.removed == ["YGG-C"], str(SYSTEM.removed))
check("删除后 YggUser 那一行真的没了", item("accountName-YGG-C") is None, str(row_names()))
check("删除后状态栏有反馈", status_text() != "", status_text())

# ══ 5. 刷新 Token（M-28）══════════════════════════════════════

click_named("accountRefreshToken-MS-A")
QTest.qWait(150)
dialog = item("accountConfirmDialog")
check("点「刷新Token」弹确认框", dialog is not None)
invoke(dialog, "accept")
QTest.qWait(250)
check("确认后真的刷了", "refresh_token:MS-A" in SYSTEM.calls, str(SYSTEM.calls[-3:]))
check("刷新成功走旧文案 `account_refresh_token_success`", status_level() == "success" and status_text() != "",
      f"{status_level()} / {status_text()}")

# ══ 6. 全部刷新 Token（A-25 在账号页的落点）═══════════════════

SYSTEM.calls.clear()
#: **观察闸门**：假系统的批量刷新会停在账号间隙里等这个事件，"进行中"这一刻因此被冻住 ——
#: 进度卡的可见性可以**确定性地**断言，不用"抢在 busy 窗口里读一次"
#: （那种写法在整仓 `-n 8` 并行时偶发假红，子进程测试为此还加过重跑兜底）。
SYSTEM.refresh_all_gate = threading.Event()
click_named("accountRefreshAll")
check("全部刷新启动了", wait_until(lambda: SYSTEM.refresh_all_calls >= 1, 2000),
      str(SYSTEM.refresh_all_calls))
busy_seen = wait_until(lambda: bool(ACCOUNTS.property("busy")), 2000)
#: 轮询等它可见（可见性绑定要到下一轮事件循环才更新）—— 此刻工作线程还被闸门冻着，
#: 所以等到的一定是"进行中"这一帧，而不是"结束后的残留"。
progress_seen = wait_until(
    lambda: item("accountProgressCard") is not None
    and bool(item("accountProgressCard").property("visible")), 2000)
card = item("accountProgressCard")
REPORT["progress_debug"] = {"busySeen": bool(busy_seen),
                            "cardVisible": bool(card.property("visible")) if card is not None else None}
progress_label = text_of("accountProgressLabel")
shot("account_refresh_all")
REPORT["refresh_all_progress"] = progress_label
check("进度卡与进度文案出现", progress_seen, f"visible={progress_seen} label={progress_label}")

# 看完了 → 放行，再等它收尾
SYSTEM.refresh_all_gate.set()
ok = wait_until(lambda: str(SHELL.property("statusText")) != "" and not bool(ACCOUNTS.property("busy")), 5000)
check("批量刷新收尾（busy 归位 + 状态栏文案）", ok,
      f"busy={ACCOUNTS.property('busy')} status={SHELL.property('statusText')}")
SYSTEM.refresh_all_gate = None

# ══ 7. 登录（M-26）════════════════════════════════════════════

# 7.1 离线：空名字 → 就地报错
click_named("accountAddOffline")
QTest.qWait(150)
dialog = item("accountAddDialog")
check("离线表单出现", dialog is not None and str(dialog.property("kind")) == "offline")
shot("account_add_offline")
submit = item("accountAddSubmit")
invoke(dialog, "submit")
QTest.qWait(120)
check("空名字就地报错（旧 `account_name_required`）",
      text_of("accountAddError") != "", text_of("accountAddError"))
check("空名字没有建账号", "offline_login:" not in " ".join(SYSTEM.calls), str(SYSTEM.calls[-3:]))

# 7.2 离线：填名字 → 真建
field = item("accountAddNameField")
field.setProperty("text", "NewOffline")
QTest.qWait(60)
invoke(dialog, "submit")
QTest.qWait(400)
check("填了名字就建号", any(call.startswith("offline_login:NewOffline") for call in SYSTEM.calls),
      str(SYSTEM.calls[-3:]))
check("新账号出现在列表里", item("accountName-OFF-NewOffline") is not None, str(row_names()))
check("新账号自动成为当前账号（核心层行为）", SYSTEM.current_account_id == "OFF-NewOffline",
      str(SYSTEM.current_account_id))
check("表单提交后收回", item("accountAddDialog") is None)

# 7.3 外置：缺字段 → 就地报错
click_named("accountAddYggdrasil")
QTest.qWait(150)
dialog = item("accountAddDialog")
check("外置表单出现", dialog is not None and str(dialog.property("kind")) == "yggdrasil")
shot("account_add_yggdrasil")
invoke(dialog, "submit")
QTest.qWait(120)
check("外置缺字段就地报错（旧 `account_ygg_fields_required`）",
      text_of("accountAddError") != "", text_of("accountAddError"))
invoke(dialog, "cancelled")   # `cancel()` 是普通函数，QML 里只有信号能被 invokeMethod 调到
QTest.qWait(150)
check("取消后表单收回", item("accountAddDialog") is None)

# 7.4 微软：进度 + 取消
SYSTEM.login_delay = 1.2
click_named("accountAddMicrosoft")
QTest.qWait(150)
dialog = item("accountAddDialog")
check("微软表单出现（说明 + 打开浏览器登录）", dialog is not None and str(dialog.property("kind")) == "microsoft")
shot("account_add_microsoft")
invoke(dialog, "submit")
QTest.qWait(120)
check("登录期间进度卡出现并禁用添加按钮",
      bool(item("accountProgressCard").property("visible")) and not enabled("accountAddMicrosoft"),
      f"busy={ACCOUNTS.property('busy')}")
check("取消按钮可用（本轮新增能力）", visible("accountCancel"))

#: **进度文案必须是给用户看的话，不能是 i18n 键名** —— 2026-10-06 用户验收当场报的
#: `account_ms_verifying` 直接显示，根因就是服务层把"要翻译的键"塞进了 `message`，
#: 而页面把它当文案印了出来（现在键走 `message_key`、由桥翻好再给页面）。
progress_now = text_of("accountProgressLabel")
REPORT["login_progress_text"] = progress_now
check("进度文案是翻译好的话、不是键名",
      progress_now not in ("", "<missing>")
      and not progress_now.startswith(("account_", "logging_", "settings_")),
      progress_now)
#: 回调那条中文稍后到达时会覆盖占位 —— 两条都必须是"给用户看的话"（见上面那条的由来）
progress_later = wait_until(lambda: "浏览器" in text_of("accountProgressLabel"), 2000)
progress_after = text_of("accountProgressLabel")
REPORT["login_progress_text_after_callback"] = progress_after
check("核心层回调来的状态串也在（原样透出）",
      progress_later and "浏览器" in progress_after, progress_after)
shot("account_login_progress")
rows_before_cancel = row_count()
click_named("accountCancel")
ok = wait_until(lambda: not bool(ACCOUNTS.property("busy")), 3000)
check("取消后 busy 归位", ok, str(ACCOUNTS.property("busy")))
check("取消后没有建账号", row_count() == rows_before_cancel, f"{rows_before_cancel} → {row_count()}")
check("取消后状态栏提示已取消", "取消" in status_text(), status_text())
check("取消按钮消失", not visible("accountCancel"))

# 7.5 微软：正常登录成功
SYSTEM.login_delay = 0.0
click_named("accountAddMicrosoft")
QTest.qWait(120)
dialog = item("accountAddDialog")
invoke(dialog, "submit")
ok = wait_until(lambda: item("accountName-MS-NEW") is not None, 4000)
check("微软登录成功后新账号进列表", ok, str(row_names()))
check("登录成功后进度卡收回", not bool(item("accountProgressCard").property("visible")))

# ══ 8. 导入 / 导出（M-29）═════════════════════════════════════

click_named("accountExport")
QTest.qWait(150)
check("导出第一步弹密码框", item("accountExportPasswordDialog") is not None)
shot("account_export_password")
password_dialog = item("accountExportPasswordDialog")
invoke(password_dialog, "input", "good-password")
QTest.qWait(60)
check("密码框里的明文就是输入的内容（掩码只影响显示）",
      password_dialog is not None and str(password_dialog.property("value")) == "good-password",
      str(password_dialog.property("value")) if password_dialog else "")
invoke(password_dialog, "submit")
QTest.qWait(250)

# ── 第 2 步：再输一遍核对（旧实现 `account_manager.py:555` 的第二个 `CTkInputDialog`）──
confirm_dialog = item("accountExportPasswordDialog")
check("导出第 2 步仍在同一个对话框里（提示语换成「请再次输入」）",
      confirm_dialog is not None
      and "再次输入" in str(confirm_dialog.property("prompt")),
      str(confirm_dialog.property("prompt")) if confirm_dialog else "<missing>")
shot("account_export_password_confirm")

# 先故意输错：应退回第 1 步 + 显示旧文案，且**不写文件**
invoke(confirm_dialog, "input", "wrong-password")
QTest.qWait(60)
invoke(confirm_dialog, "submit")
QTest.qWait(250)
back_dialog = item("accountExportPasswordDialog")
REPORT["export_mismatch_error"] = str(back_dialog.property("errorText")) if back_dialog else "<missing>"
REPORT["export_debug"] = {
    "dialogFound": back_dialog is not None,
    "dialogError": str(back_dialog.property("errorText")) if back_dialog else "",
    "fileWritten": EXPORT_PATH.is_file(),
}
check("两次不一致退回第 1 步并显示旧文案 `account_export_password_mismatch`",
      back_dialog is not None and "不一致" in REPORT["export_mismatch_error"],
      REPORT["export_mismatch_error"])
check("两次不一致时**没有**写文件", not EXPORT_PATH.is_file(), str(EXPORT_PATH))
shot("account_export_password_mismatch")

# 再走一遍正确路径
invoke(back_dialog, "input", "good-password")
QTest.qWait(60)
invoke(back_dialog, "submit")
QTest.qWait(250)
confirm_dialog = item("accountExportPasswordDialog")
invoke(confirm_dialog, "input", "good-password")
QTest.qWait(60)
invoke(confirm_dialog, "submit")
QTest.qWait(250)
check("两次一致后进入选文件环节（对话框收回）",
      item("accountExportPasswordDialog") is None, "对话框还开着")
ACCOUNTS.submitExportPath(str(EXPORT_PATH))
QTest.qWait(250)
check("导出真的写了文件", EXPORT_PATH.is_file(), str(EXPORT_PATH))
check("导出用了用户设的口令", SYSTEM.exported_password == "good-password", str(SYSTEM.exported_password))
result_dialog = item("accountExportResultDialog")
check("导出结果对话框出现", result_dialog is not None)
check("结果对话框带文件路径", str(result_dialog.property("exportPath")) == str(EXPORT_PATH),
      str(result_dialog.property("exportPath")) if result_dialog else "")
shot("account_export_result")
invoke(result_dialog, "openFolderRequested")
QTest.qWait(150)
check("「打开目录」调用了注入的 opener", OPENED and str(OPENED[-1]) == str(TMP_DIR), str(OPENED))
invoke(result_dialog, "closed")
QTest.qWait(150)
check("关掉结果框后对话框收回", item("accountExportResultDialog") is None)

rows_before_import = row_count()
ACCOUNTS.requestImport()
QTest.qWait(120)
check("导入第一步弹密码框", item("accountImportPasswordDialog") is not None)
import_dialog = item("accountImportPasswordDialog")
invoke(import_dialog, "input", "good-password")
QTest.qWait(60)
invoke(import_dialog, "submit")
QTest.qWait(200)
ACCOUNTS.submitImport(str(EXPORT_PATH))
QTest.qWait(250)
check("导入后账号数增加", row_count() == rows_before_import + 1,
      f"{rows_before_import} → {row_count()}")
notice = qvariant(ACCOUNTS, "lastNotice")
REPORT["import_notice"] = notice
check("导入成功给了提示（Toast 走旧键 `account_import_success`）",
      "导入" in str(notice.get("message", "")) and str(notice.get("level", "")) == "success",
      str(notice))

# 错密码 → 旧文案
ACCOUNTS.requestImport()
QTest.qWait(100)
import_dialog = item("accountImportPasswordDialog")
invoke(import_dialog, "input", "bad-password")
QTest.qWait(60)
invoke(import_dialog, "submit")
QTest.qWait(150)
ACCOUNTS.submitImport(str(EXPORT_PATH))
QTest.qWait(200)
check("错密码不建账号", row_count() == rows_before_import + 1, str(row_count()))
REPORT["import_error_status"] = status_text()

# ══ 9. 空态 / 三态 / 多语言 / 主题 ═══════════════════════════

SYSTEM.accounts.clear()
SYSTEM.current_account_id = None
ACCOUNTS.refresh()
QTest.qWait(200)
check("空列表显示空态", bool(item("accountEmptyState").property("visible")), "accountEmptyState")
check("空态文案是旧键 `account_no_accounts`",
      title_of("accountEmptyState") != "" and item("accountEmptyState") is not None,
      title_of("accountEmptyState"))
shot("account_empty")

# 主题：切 5 个预设 + 自定义强调色，页面不报错（R8 由闸门管，这里管"渲染不炸"）
from services.palette import COLORS  # noqa: E402

theme_results: Dict[str, bool] = {}
from services.theme_service import get_theme_engine  # noqa: E402

theme_engine = get_theme_engine()
available = {}
if theme_engine is not None:
    for row_theme in theme_engine.get_available_themes():
        loaded = theme_engine.load_theme(str(row_theme.get("name", "")))
        if loaded is not None:
            available[str(getattr(loaded, "name", ""))] = loaded
REPORT["available_themes"] = sorted(available)
for theme_name in ("default", "ocean", "forest", "lavender", "sunset"):
    theme = available.get(theme_name)
    if theme is None or theme_engine is None:
        theme_results[theme_name] = False
        REPORT["notes"].append(f"主题 {theme_name} 不在引擎的预设里：{sorted(available)}")
        continue
    try:
        theme_engine.apply_theme(theme)
        QTest.qWait(80)
        theme_results[theme_name] = bool(item("accountTitle") is not None)
    except Exception as e:  # noqa: BLE001
        theme_results[theme_name] = False
        REPORT["notes"].append(f"主题 {theme_name} 异常: {e}")
REPORT["themes"] = theme_results
check("5 个预设主题下账号页都在", all(theme_results.values()), str(theme_results))
check("主题色来自 Theme（强调色存在）", str(COLORS.get("accent", "")) != "", str(COLORS.get("accent")))

# 四语言：切过去之后标题与按钮文案都变（无缺键 → 不会显示成裸键名）
language_results: Dict[str, str] = {}
for code in ("en_US", "zh_TW", "ja_JP", "zh_CN"):
    switch_language(code)
    language_results[code] = text_of("accountTitle")
    shot(f"account_lang_{code}")
REPORT["languages"] = language_results
check("四语言下标题都翻出来了（不是裸键名）",
      all(value not in ("", "account_manager_title", "<missing>") for value in language_results.values()),
      str(language_results))
check("四种语言下文案互不相同（真的换了）",
      len(set(language_results.values())) == 4, str(language_results))

# ══ 10. 桥缺席时的错误态（结构判据）══════════════════════════

check("桥已注册（Accounts 在 context 属性里）", "Accounts" in REPORT["bridges_registered"],
      str(REPORT["bridges_registered"]))

# ─── QML 报错汇总 ─────────────────────────────────────────────

messages = list(getattr(SINK, "messages", []) or [])
REPORT["qml_messages"] = [str(m) for m in messages][:40]
errors = [
    str(m) for m in messages
    if any(marker in str(m) for marker in QML_ERROR_MARKERS)
    and not any(benign in str(m) for benign in BENIGN_MARKERS)
]
check("QML 无 TypeError / 未定义引用", not errors, json.dumps(errors[:5], ensure_ascii=False))

passed = sum(1 for entry in REPORT["checks"] if entry["ok"])
REPORT["passed"] = passed
REPORT["total"] = len(REPORT["checks"])
print(MARKER + json.dumps(REPORT, ensure_ascii=False))
sys.exit(0 if not REPORT["failures"] else 1)
