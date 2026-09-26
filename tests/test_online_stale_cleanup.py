"""任务 1.24（阶段 1 收口）的守卫：D-83 / D-107 / D-90 / D-114。

**全部离线**：不联网、不起子进程、不碰注册表；所有目录都造在 ``tmp_path`` 里，
缩略图/线程/对话框一律用替身，**不对用户机器上的真实目录做任何写操作**。

四条缺陷各自的"改前 → 改后"对照写在每个用例的 docstring 里：

- **D-83**：``EasyTierManager.download()`` 旧行为是**无声递归删除**版本根下所有
  非当前版本目录。新行为：服务只算清单（纯读取），界面问过用户才删；
  **没人确认 → 一个目录都不删**。
- **D-107**：``_flatten_extracted_dir()`` 旧行为无条件 ``rmtree(ignore_errors=True)``；
  新行为：只在子目录**已空**时删，非空保留并记日志。
- **D-90**：worker 线程旧行为直接调 ``winfo_exists()`` + ``after`` 进 UI；
  新行为：worker 只入队，主线程轮询渲染。
- **D-114**：晚到的缩略图回调打到已销毁控件时不再抛 ``TclError``。
"""

from __future__ import annotations

import ast
import logging
import threading
import zipfile
from pathlib import Path

import pytest

import ui.app_online as app_online
from services.online_service import EASYTIER_VERSION, EasyTierManager
from ui.app_online import OnlineTabMixin
from ui.i18n import _
from ui.log_widget import LOG_VIEW_MAX_LINES

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_FILE = REPO_ROOT / "ui" / "app_online.py"
RESOURCE_MANAGER = REPO_ROOT / "ui" / "windows" / "resource_manager.py"


# ═══════════════════════════════════════════════════════════════════
# 夹具与小工具
# ═══════════════════════════════════════════════════════════════════


def make_base_dir(tmp: Path, tag: str = "case") -> Path:
    """造出与真实布局一致的安装目录（版本根 / <版本> / easytier-windows-x86_64）。"""
    base_dir = tmp / tag / "FMCL" / "EasyTier" / EASYTIER_VERSION / "easytier-windows-x86_64"
    base_dir.mkdir(parents=True)
    for name in ("easytier-core.exe", "easytier-cli.exe", "Packet.dll"):
        (base_dir / name).write_bytes(b"MZ-current")
    return base_dir


def snapshot(root: Path) -> set:
    """整棵树的快照（类型 + 相对路径 + 文件大小），用于证明"什么都没被删/改"。"""
    items = set()
    for path in root.rglob("*"):
        kind = "D" if path.is_dir() else "F"
        size = "" if path.is_dir() else str(path.stat().st_size)
        items.add(f"{kind} {path.relative_to(root)} {size}")
    return items


def make_zip(files: dict) -> bytes:
    import io as _io

    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, payload in files.items():
            zf.writestr(name, payload)
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, n=None):
        return self._payload if n is None else self._payload[:n]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class LogRecords:
    """把 logzero 的日志记录抓下来（logzero.logger 就是一个标准 logging.Logger）。"""

    def __init__(self):
        self.records: list = []
        self._handler = logging.Handler()
        self._handler.emit = self.records.append

    def __enter__(self):
        from logzero import logger as logzero_logger

        self._logger = logzero_logger
        self._logger.addHandler(self._handler)
        return self

    def __exit__(self, *exc):
        self._logger.removeHandler(self._handler)
        return False

    def messages(self, level: int) -> list:
        return [r.getMessage() for r in self.records if r.levelno == level]


class FakeTextbox:
    """够用的文本控件替身（`append_line` 只用到 insert/index/delete/see/configure）。"""

    def __init__(self):
        self.lines: list = []
        self.states: list = []
        self.seen = 0

    def configure(self, **kw):
        if "state" in kw:
            self.states.append(kw["state"])

    def insert(self, index, text):
        self.lines.extend(text.rstrip("\n").split("\n"))

    def index(self, spec):
        return f"{len(self.lines) + 1}.0"

    def delete(self, start, end):
        self.lines.clear()

    def see(self, index):
        self.seen += 1


class FakeOwner(OnlineTabMixin):
    """联机 Mixin 的假宿主：不建任何 Tk 控件。

    ``winfo_exists`` / ``after`` 一旦被调用就**大声失败** —— 这正是 D-90 要钉住的
    那条红线（worker 线程不许碰 Tk）。
    """

    def __init__(self, manager=None, alive: bool = True):
        self._et_manager = manager
        self.log_lines: list = []
        self._alive = alive
        self.after_calls: list = []
        self.tk_calls: list = []
        self._online_log_text = FakeTextbox()

    # 任何 Tk 触点都会留下痕迹
    def winfo_exists(self):
        self.tk_calls.append("winfo_exists")
        return self._alive

    def after(self, ms, fn, *args):
        self.tk_calls.append(f"after({ms})")
        self.after_calls.append((ms, fn))
        return "timer"

    def _append_online_log(self, message):  # D-83 的界面侧用例只关心"记了哪几行"
        self.log_lines.append(message)


# ═══════════════════════════════════════════════════════════════════
# D-83（服务层）：清单是纯读取的，且只收"看起来像 EasyTier 版本目录"的项
# ═══════════════════════════════════════════════════════════════════


class TestStaleVersionDirs:
    def _layout(self, tmp: Path) -> Path:
        base_dir = make_base_dir(tmp)
        root = tmp / "case" / "FMCL" / "EasyTier"
        (root / "2.5.0").mkdir()  # ① 版本号形态
        (root / "2.5.0" / "easytier-core.exe").write_bytes(b"MZ-old")
        (root / "easytier-manual-copy").mkdir()  # ② 名字不像版本但有 core
        (root / "easytier-manual-copy" / "easytier-core.exe").write_bytes(b"MZ-odd")
        (root / "我的备份").mkdir()  # ③ 用户自己的东西：不入列
        (root / "我的备份" / "很重要.txt").write_text("别删我", encoding="utf-8")
        (root / "notes.txt").write_text("根下的文件", encoding="utf-8")  # ④ 不是目录
        return base_dir

    def test_names_is_a_pure_read(self, tmp_path):
        """★ D-83 的核心：算清单**不删任何东西**（跑完前后逐项比对整棵树）。"""
        base_dir = self._layout(tmp_path)
        mgr = EasyTierManager(base_dir)

        before = snapshot(tmp_path)
        first = mgr.stale_version_dirs()
        second = mgr.stale_version_dirs()
        after = snapshot(tmp_path)

        assert before == after, "清单函数动过文件系统（这正是 D-83 要禁止的）"
        assert [str(p) for p in first] == [str(p) for p in second]
        assert first == [tmp_path / "case" / "FMCL" / "EasyTier" / "2.5.0"] or sorted(
            p.name for p in first
        ) == ["2.5.0", "easytier-manual-copy"]

    def test_judgement_accepts_version_names_and_core_dirs_only(self, tmp_path):
        """判据：版本号形态的目录名，**或**目录里有 easytier-core.exe。"""
        base_dir = self._layout(tmp_path)
        names = sorted(p.name for p in EasyTierManager(base_dir).stale_version_dirs())
        assert names == ["2.5.0", "easytier-manual-copy"]
        assert "我的备份" not in names, "没有 easytier-core.exe、名字也不像版本的目录不入列"
        assert "notes.txt" not in names, "文件不入列"

    def test_current_version_dir_and_ancestors_never_listed(self, tmp_path):
        """当前版本目录、当前安装目录自身及其祖先永不入列。"""
        base_dir = make_base_dir(tmp_path)
        mgr = EasyTierManager(base_dir)
        listed = {str(p) for p in mgr.stale_version_dirs()}
        assert str(base_dir) not in listed
        assert str(base_dir.parent) not in listed
        assert str(base_dir.parent.parent) not in listed
        assert EASYTIER_VERSION not in {p.name for p in mgr.stale_version_dirs()}

    def test_missing_version_root_is_empty_list(self, tmp_path):
        """版本根不存在（还没装过）→ 空清单，不抛异常。"""
        base_dir = tmp_path / "nothing" / "FMCL" / "EasyTier" / EASYTIER_VERSION / "x"
        assert EasyTierManager(base_dir).stale_version_dirs() == []


# ═══════════════════════════════════════════════════════════════════
# D-83（服务层）：download() 不再删；remove_stale_versions 只删显式传入的
# ═══════════════════════════════════════════════════════════════════


class TestDownloadAndRemove:
    def _install_case(self, tmp_path) -> Path:
        """造出"已有一个旧版本目录 + 一个用户目录"的现场。"""
        base_dir = make_base_dir(tmp_path)
        root = tmp_path / "case" / "FMCL" / "EasyTier"
        (root / "2.5.0").mkdir()
        (root / "2.5.0" / "easytier-core.exe").write_bytes(b"MZ-old")
        (root / "我的备份").mkdir()
        (root / "我的备份" / "很重要.txt").write_text("别删我", encoding="utf-8")
        return base_dir

    def _download(self, base_dir: Path, monkeypatch):
        payload = make_zip(
            {
                "easytier-core.exe": b"MZ-new",
                "easytier-cli.exe": b"MZ-new",
                "Packet.dll": b"MZ-new",
            }
        )
        monkeypatch.setattr(
            "services.online_service.EASYTIER_DOWNLOAD_URLS", ["https://fake-mirror/{version}.zip"]
        )
        mgr = EasyTierManager(base_dir, urlopen=lambda req, timeout=None, **kw: _FakeResponse(payload))
        assert mgr.download(on_progress=lambda _m: None) is True
        return mgr

    def test_download_deletes_nothing_and_hands_back_the_list(self, tmp_path, monkeypatch):
        """★ 改前：download() 在 worker 线程里删掉版本根下所有非当前版本目录。

        改后：**一个目录都不删**，只把清单放在 ``last_stale_version_dirs`` 上交回调用方。
        """
        base_dir = self._install_case(tmp_path)
        root = tmp_path / "case" / "FMCL" / "EasyTier"
        before = snapshot(tmp_path)

        mgr = self._download(base_dir, monkeypatch)

        after = snapshot(tmp_path)
        # 只允许"当前安装目录里的三个文件被本次安装覆盖"这一处差异
        changed = {
            item
            for item in (before ^ after)
            if not item.split(" ", 2)[1].startswith(
                str(Path("case") / "FMCL" / "EasyTier" / EASYTIER_VERSION)
            )
        }
        assert changed == set(), f"download() 动了版本根下的别的东西：{sorted(changed)}"
        assert (root / "2.5.0").is_dir(), "旧版本目录必须还在（改前会被删掉）"
        assert (root / "我的备份" / "很重要.txt").is_file(), "用户目录必须还在"
        assert [p.name for p in mgr.last_stale_version_dirs] == ["2.5.0"]

    def test_download_resets_the_previous_list(self, tmp_path, monkeypatch):
        """每次 download 都重算清单：上一次的结果不会残留下来被误删。"""
        base_dir = self._install_case(tmp_path)
        root = tmp_path / "case" / "FMCL" / "EasyTier"
        mgr = self._download(base_dir, monkeypatch)
        assert mgr.last_stale_version_dirs, "第一次应当算出清单"
        (root / "2.5.0").rename(root / "3.0.0")
        mgr2 = self._download(base_dir, monkeypatch)
        assert [p.name for p in mgr2.last_stale_version_dirs] == ["3.0.0"]

    def test_failed_download_keeps_an_empty_list(self, tmp_path, monkeypatch):
        """下载失败 → 清单为空（调用方拿不到"可以删"的建议）。"""
        base_dir = self._install_case(tmp_path)
        monkeypatch.setattr(
            "services.online_service.EASYTIER_DOWNLOAD_URLS", ["https://fake/{version}.zip"]
        )

        def dead(req, timeout=None, **kw):
            raise OSError("网络不通")

        mgr = EasyTierManager(base_dir, urlopen=dead)
        assert mgr.download() is False
        assert mgr.last_stale_version_dirs == []

    def test_remove_only_deletes_explicitly_passed_paths(self, tmp_path):
        base_dir = self._install_case(tmp_path)
        root = tmp_path / "case" / "FMCL" / "EasyTier"
        mgr = EasyTierManager(base_dir)

        removed = mgr.remove_stale_versions([root / "2.5.0"])

        assert removed == 1
        assert not (root / "2.5.0").exists()
        assert (root / "我的备份" / "很重要.txt").is_file(), "没传入的目录一个都不能动"
        assert mgr.last_remove_failures == []

    def test_remove_failures_are_not_silent(self, tmp_path):
        """★ 改前：``rmtree(ignore_errors=True)``，失败连日志都没有。

        改后：失败的路径不当成功计数、写 error 日志、并记进 ``last_remove_failures``。
        """
        base_dir = self._install_case(tmp_path)
        root = tmp_path / "case" / "FMCL" / "EasyTier"
        missing = root / "不存在的版本"
        mgr = EasyTierManager(base_dir)

        with LogRecords() as logs:
            removed = mgr.remove_stale_versions([missing, root / "2.5.0"])

        assert removed == 1, "只有真的删掉的那个算成功"
        assert len(mgr.last_remove_failures) == 1
        assert mgr.last_remove_failures[0][0] == missing
        errors = logs.messages(logging.ERROR)
        assert any(str(missing) in m for m in errors), f"失败没有被记进 error 日志：{errors}"

    def test_remove_refuses_ancestors_of_the_install_dir(self, tmp_path):
        """拒绝删除当前安装目录的自身/祖先（清单算错也不会毁掉正在用的安装）。"""
        base_dir = self._install_case(tmp_path)
        root = tmp_path / "case" / "FMCL" / "EasyTier"
        mgr = EasyTierManager(base_dir)
        with LogRecords() as logs:
            removed = mgr.remove_stale_versions([root, base_dir])
        assert removed == 0
        assert root.is_dir() and base_dir.is_dir()
        assert len(mgr.last_remove_failures) == 2
        assert any("拒绝删除" in m for m in logs.messages(logging.ERROR))

    def test_non_oserror_from_rmtree_propagates(self, tmp_path, monkeypatch):
        """不吞异常：``rmtree`` 抛出的非 OSError 原样冒泡（只把 OSError 当"删失败"）。"""
        base_dir = self._install_case(tmp_path)
        root = tmp_path / "case" / "FMCL" / "EasyTier"
        mgr = EasyTierManager(base_dir)

        def boom(path, *a, **kw):
            raise RuntimeError("不该被吞掉的异常")

        monkeypatch.setattr("services.online_service.shutil.rmtree", boom)
        with pytest.raises(RuntimeError):
            mgr.remove_stale_versions([root / "2.5.0"])

    def test_real_oserror_is_counted_not_raised(self, tmp_path, monkeypatch):
        """真实文件系统错误（这里是"目录非空不可删"的模拟）→ 不抛，但要记账。"""
        base_dir = self._install_case(tmp_path)
        root = tmp_path / "case" / "FMCL" / "EasyTier"
        mgr = EasyTierManager(base_dir)

        def denied(path, *a, **kw):
            raise PermissionError("拒绝访问")

        monkeypatch.setattr("services.online_service.shutil.rmtree", denied)
        with LogRecords() as logs:
            removed = mgr.remove_stale_versions([root / "2.5.0"])
        assert removed == 0
        assert (root / "2.5.0").is_dir(), "删失败时目录必须留着"
        assert any("失败" in m for m in logs.messages(logging.ERROR))


# ═══════════════════════════════════════════════════════════════════
# D-83（界面侧）：问过用户才删；不确认 → 一个都不删
# ═══════════════════════════════════════════════════════════════════


class TestOfferCleanupDialog:
    def _manager_with_two_stale(self, tmp_path) -> EasyTierManager:
        base_dir = make_base_dir(tmp_path)
        root = tmp_path / "case" / "FMCL" / "EasyTier"
        (root / "2.5.0").mkdir()
        (root / "2.4.0").mkdir()
        mgr = EasyTierManager(base_dir)
        mgr.last_stale_version_dirs = mgr.stale_version_dirs()
        assert len(mgr.last_stale_version_dirs) == 2
        return mgr

    def test_declined_leaves_everything(self, tmp_path, monkeypatch):
        """★ 无人确认（用户点"否"）→ 一个目录都不删。"""
        mgr = self._manager_with_two_stale(tmp_path)
        root = tmp_path / "case" / "FMCL" / "EasyTier"
        before = snapshot(tmp_path)

        class FakeMessagebox:
            asked: list = []

            @staticmethod
            def askyesno(title, message, **kw):
                FakeMessagebox.asked.append((title, message))
                return False

        monkeypatch.setattr(app_online, "messagebox", FakeMessagebox)
        owner = FakeOwner(mgr)
        OnlineTabMixin._offer_stale_version_cleanup(owner)

        assert snapshot(tmp_path) == before, "用户拒绝之后依然有东西被删了"
        assert (root / "2.5.0").is_dir() and (root / "2.4.0").is_dir()
        assert FakeMessagebox.asked, "应当先问过用户"
        assert owner.log_lines == [], "什么都没删就不该往日志里写'已删除'"

    def test_confirmed_deletes_exactly_the_listed_dirs(self, tmp_path, monkeypatch):
        """确认后：只删清单里的，逐条写日志。"""
        mgr = self._manager_with_two_stale(tmp_path)
        root = tmp_path / "case" / "FMCL" / "EasyTier"
        (root / "我的备份").mkdir()
        (root / "我的备份" / "很重要.txt").write_text("别删我", encoding="utf-8")

        class FakeMessagebox:
            @staticmethod
            def askyesno(title, message, **kw):
                assert "2.5.0" in message and "2.4.0" in message, "对话框里应当列出待删目录"
                return True

        monkeypatch.setattr(app_online, "messagebox", FakeMessagebox)
        owner = FakeOwner(mgr)
        OnlineTabMixin._offer_stale_version_cleanup(owner)

        assert not (root / "2.5.0").exists() and not (root / "2.4.0").exists()
        assert (root / "我的备份" / "很重要.txt").is_file(), "清单外的目录不能被碰"
        # ★ 期望值必须用 `_()` **现算**，不能写死键名。（第 12 轮修正）
        #
        # 原来这里写的是 `["[FMCL] rm_deleted", "[FMCL] rm_deleted"]`，依据是一句
        # 注释「单元测试里 ui.i18n 没有加载语言包，`_()` 返回键名本身」——
        # **这个前提没有被钉死**：`tests/test_layering.py::test_i18n_language_state_visible_through_shim`
        # 会 `set_language("en_US")` 再 restore，而 restore 本身就会把 zh_CN 语言包
        # **加载进内存**。此后同进程里 `_()` 返回的就是中文，不再是键名。
        # 实测症状：**单独跑绿、全量跑红**（2 个用例，第 11 轮结束时留下的红灯）。
        # 两种状态都是合法的，唯一不变的是"日志行 = 当前语言的译文"。
        expected = sorted(["[FMCL] " + _("rm_deleted", name="2.4.0"),
                           "[FMCL] " + _("rm_deleted", name="2.5.0")])
        assert sorted(owner.log_lines) == expected, owner.log_lines

    def test_dialog_failure_deletes_nothing(self, tmp_path, monkeypatch):
        """对话框本身抛异常（没有界面 / TclError）→ 按"不确认"处理。"""
        mgr = self._manager_with_two_stale(tmp_path)
        before = snapshot(tmp_path)

        class FakeMessagebox:
            @staticmethod
            def askyesno(title, message, **kw):
                raise RuntimeError("no display")

        monkeypatch.setattr(app_online, "messagebox", FakeMessagebox)
        OnlineTabMixin._offer_stale_version_cleanup(FakeOwner(mgr))
        assert snapshot(tmp_path) == before

    def test_no_candidates_never_asks(self, tmp_path, monkeypatch):
        """清单为空 → 连对话框都不弹。"""
        base_dir = make_base_dir(tmp_path)
        mgr = EasyTierManager(base_dir)

        class FakeMessagebox:
            @staticmethod
            def askyesno(title, message, **kw):  # pragma: no cover - 不该被调用
                raise AssertionError("清单为空时不该打扰用户")

        monkeypatch.setattr(app_online, "messagebox", FakeMessagebox)
        OnlineTabMixin._offer_stale_version_cleanup(FakeOwner(mgr))

    def test_partial_failure_is_reported_per_path(self, tmp_path, monkeypatch):
        """删失败时，那一行走"删除失败"文案，成功的行走"已删除"。"""
        mgr = self._manager_with_two_stale(tmp_path)
        root = tmp_path / "case" / "FMCL" / "EasyTier"

        class FakeMessagebox:
            @staticmethod
            def askyesno(title, message, **kw):
                return True

        monkeypatch.setattr(app_online, "messagebox", FakeMessagebox)
        owner = FakeOwner(mgr)
        # 让其中一个在"问完之后"消失：服务会把它记成失败
        (root / "2.4.0").rmdir()
        OnlineTabMixin._offer_stale_version_cleanup(owner)

        # 期望值现算（理由同上一个用例）。这里**不能**比对整行：
        # `{error}` 里是操作系统给的本地化错误文本（本机是"不是一个已存在的目录"），
        # 写死它会把测试绑死在某台机器的系统语言上。而且语言包**没加载**时
        # `_translate()` 会原样返回键名、连 `{}` 都不做替换（这是既有行为），
        # 所以这里只钉"两条日志、一条成功一条失败、失败行以失败文案开头"。
        assert len(owner.log_lines) == 2, owner.log_lines
        deleted_line = "[FMCL] " + _("rm_deleted", name="2.5.0")
        assert deleted_line in owner.log_lines, owner.log_lines
        failed_head = "[FMCL] " + _("rm_delete_failed", error="\x00").split("\x00")[0]
        others = [line for line in owner.log_lines if line != deleted_line]
        assert len(others) == 1 and others[0].startswith(failed_head), owner.log_lines
        assert len(mgr.last_remove_failures) == 1

    def test_log_lines_follow_the_active_locale(self, tmp_path, monkeypatch):
        """★ 日志行必须**随当前语言**变化 —— 把"前提"钉死，而不是假设它。

        上一个用例踩的坑是"假设测试进程里语言包没被加载"。这两条用例反过来
        **显式**把语言切成 en_US（并用 monkeypatch 自动还原），证明日志行真的
        走 `_()`、且 `{name}` / `{error}` 真的被替换进去；如果哪天后缀被写死成
        中文或键名，这里会红。
        """
        import services.i18n_service as i18n

        monkeypatch.setattr(i18n, "_current_language", "en_US", raising=False)
        monkeypatch.setattr(i18n, "_translations", i18n._load_translations("en_US"), raising=False)

        class FakeMessagebox:
            @staticmethod
            def askyesno(title, message, **kw):
                return True

        monkeypatch.setattr(app_online, "messagebox", FakeMessagebox)

        # ① 全部成功
        mgr = self._manager_with_two_stale(tmp_path)
        owner = FakeOwner(mgr)
        OnlineTabMixin._offer_stale_version_cleanup(owner)
        assert owner.log_lines == ["[FMCL] Deleted: 2.4.0", "[FMCL] Deleted: 2.5.0"], owner.log_lines

        # ② 一条失败：失败行的 `{error}` 必须真的被替换进去（含失败路径名）
        mgr2 = self._manager_with_two_stale(tmp_path / "partial")
        root2 = tmp_path / "partial" / "case" / "FMCL" / "EasyTier"
        (root2 / "2.4.0").rmdir()
        owner2 = FakeOwner(mgr2)
        OnlineTabMixin._offer_stale_version_cleanup(owner2)
        assert any(
            line.startswith("[FMCL] Failed to delete: ") and "2.4.0" in line
            for line in owner2.log_lines
        ), owner2.log_lines


# ═══════════════════════════════════════════════════════════════════
# D-107：解压目录拍平 —— 只在子目录搬空时才删
# ═══════════════════════════════════════════════════════════════════


class TestFlattenExtractedDir:
    def _nested(self, tmp_path) -> Path:
        base_dir = make_base_dir(tmp_path)
        nested = base_dir / "easytier-windows-x86_64"
        nested.mkdir()
        for name in ("easytier-core.exe", "easytier-cli.exe", "Packet.dll"):
            (nested / name).write_bytes(b"MZ-nested")
        return base_dir

    def test_empty_subdir_is_removed(self, tmp_path):
        """子目录里只有那三个文件 → 搬走后为空 → 删掉（与改前一致）。"""
        base_dir = self._nested(tmp_path)
        mgr = EasyTierManager(base_dir)
        mgr._flatten_extracted_dir()
        assert mgr.is_installed
        assert not (base_dir / "easytier-windows-x86_64").exists()

    def test_non_empty_subdir_is_kept(self, tmp_path):
        """★ 改前：不管里面还有什么，一律 ``rmtree(ignore_errors=True)``。

        改后：搬走三个文件后子目录里**还有别的东西** → 原样保留（并记日志）。
        """
        base_dir = self._nested(tmp_path)
        nested = base_dir / "easytier-windows-x86_64"
        (nested / "附加工具.exe").write_bytes(b"MZ-extra")
        (nested / "说明.txt").write_text("zip 自带的东西", encoding="utf-8")

        mgr = EasyTierManager(base_dir)
        with LogRecords() as logs:
            mgr._flatten_extracted_dir()

        assert mgr.is_installed, "三个目标文件必须已经搬到上一层"
        assert nested.is_dir(), "非空子目录必须保留（改前会被连内容一起删掉）"
        assert (nested / "附加工具.exe").is_file()
        assert (nested / "说明.txt").is_file()
        info = logs.messages(logging.INFO)
        assert any("未搬走" in m and "说明.txt" in m for m in info), f"应当记下保留原因：{info}"

    def test_flatten_tolerates_missing_targets(self, tmp_path):
        """子目录里一个目标文件都没有 → 不当成功（is_installed 仍为假），也不抛。"""
        base_dir = make_base_dir(tmp_path)
        (base_dir / "easytier-windows-x86_64").mkdir()
        (base_dir / "easytier-core.exe").unlink()
        mgr = EasyTierManager(base_dir)
        mgr._flatten_extracted_dir()
        assert mgr.is_installed is False


# ═══════════════════════════════════════════════════════════════════
# D-90：worker 只入队，主线程轮询渲染
# ═══════════════════════════════════════════════════════════════════


class TestOnlineLogDelivery:
    def test_append_only_enqueues_and_never_touches_tk(self):
        """★ 改前：``_append_online_log`` 里直接 ``winfo_exists()`` + ``after(0, ...)``。

        改后：任何线程调用都只入队。这里用一个**没有 Tk**的假宿主，
        并且它的 ``winfo_exists`` / ``after`` 一旦被调用就记录在案。
        """
        owner = FakeOwner()
        OnlineTabMixin._append_online_log(owner, "第一行")
        OnlineTabMixin._append_online_log(owner, "第二行")

        assert owner.tk_calls == [], f"入队路径碰了 Tk：{owner.tk_calls}"
        assert owner._online_log_queue().qsize() == 2

    def test_worker_thread_only_enqueues(self):
        """真起一个 worker 线程调用它：除入队外什么都不做（不抛、不碰控件）。"""
        owner = FakeOwner()
        errors: list = []

        def worker():
            try:
                for i in range(50):
                    OnlineTabMixin._append_online_log(owner, f"worker-{i}")
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)
        assert errors == []
        assert owner.tk_calls == []
        assert owner._online_log_queue().qsize() == 50

    def test_drain_renders_on_demand_and_reschedules(self):
        """主线程 drain：把队列里的行写进控件，并排下一拍。"""
        owner = FakeOwner()
        for i in range(5):
            OnlineTabMixin._append_online_log(owner, f"line-{i}")

        OnlineTabMixin._drain_online_log(owner)

        assert owner._online_log_text.lines == [f"line-{i}" for i in range(5)]
        assert owner.after_calls and owner.after_calls[-1][0] == app_online.ONLINE_LOG_DRAIN_MS
        assert owner._online_log_text.states[0] == "normal", "渲染前要先切到可写"

    def test_drain_is_bounded_per_tick(self):
        """一次 drain 最多渲染 ONLINE_LOG_DRAIN_BATCH 行（避免积压时卡主线程）。"""
        owner = FakeOwner()
        total = app_online.ONLINE_LOG_DRAIN_BATCH + 7
        for i in range(total):
            OnlineTabMixin._append_online_log(owner, f"line-{i}")

        OnlineTabMixin._drain_online_log(owner)
        assert len(owner._online_log_text.lines) == app_online.ONLINE_LOG_DRAIN_BATCH
        OnlineTabMixin._drain_online_log(owner)
        assert len(owner._online_log_text.lines) == total

    def test_drain_stops_when_widget_is_gone(self):
        """界面销毁后不再排期（也不抛）。"""
        owner = FakeOwner(alive=False)
        OnlineTabMixin._append_online_log(owner, "不会有人看到")
        OnlineTabMixin._drain_online_log(owner)
        assert owner.after_calls == []

    def test_line_cap_still_applies(self, tmp_path):
        """D-86 的行数上限必须还在（渲染路径没有绕开 append_line）。"""
        owner = FakeOwner()
        for i in range(LOG_VIEW_MAX_LINES + 20):
            OnlineTabMixin._append_online_log(owner, f"l{i}")
        # 分批 drain 到清空
        for _ in range(30):
            OnlineTabMixin._drain_online_log(owner)
            if owner._online_log_queue().empty():
                break
        assert len(owner._online_log_text.lines) <= LOG_VIEW_MAX_LINES

    def test_source_has_no_widget_calls_in_append(self):
        """AST 断言：``_append_online_log`` 的函数体里没有任何控件/Dk 触点。"""
        tree = ast.parse(UI_FILE.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OnlineTabMixin")
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_append_online_log")
        banned = {
            "winfo_exists", "after", "after_cancel", "configure", "insert", "delete",
            "see", "update", "update_idletasks", "pack", "pack_forget", "get",
        }
        hits = [
            ast.unparse(node)
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in banned
        ]
        assert hits == [], f"_append_online_log 里出现了控件调用：{hits}"

    def test_wiring_uses_the_queue_instead_of_after(self):
        """接线断言：下载进度回调不再 ``after(0, ...)``；输出面板负责启动轮询。"""
        src = UI_FILE.read_text(encoding="utf-8")
        tree = ast.parse(src)
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "OnlineTabMixin")
        funcs = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}

        setup = funcs["_on_setup_environment"]
        inner = [
            n
            for n in ast.walk(setup)
            if isinstance(n, (ast.FunctionDef, ast.Lambda))
            and any(
                isinstance(c, ast.Attribute) and c.attr == "after"
                for c in ast.walk(n)
                if isinstance(c, ast.Call)
            )
        ]
        assert inner == [], "worker 侧的回调里还有 after（那正是 D-90）"
        called = {
            ast.unparse(node.func)
            for node in ast.walk(funcs["_build_online_output_panel"])
            if isinstance(node, ast.Call)
        }
        assert any(name.endswith("_start_online_log_drain") for name in called), (
            "输出面板建好后必须由主线程启动轮询"
        )


# ═══════════════════════════════════════════════════════════════════
# D-114：晚到的缩略图回调打到已销毁控件
# ═══════════════════════════════════════════════════════════════════


def test_thumbnail_icon_guards_destroyed_target():
    """AST：``_set_thumbnail_icon`` 里对 ``winfo_children()`` 有异常守卫。

    真实现场（`poc/_probe_d94_page_cache.py` 实测）：翻页会销毁整页控件，而在途的
    缩略图回调仍会打到已销毁的 icon_frame —— 旧代码在这里抛
    ``TclError: bad window path name``，并且后面的 ``_on_thumbnail_done()`` 再也
    执行不到（进度标签永久停在 x/y）。
    """
    tree = ast.parse(RESOURCE_MANAGER.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ResourceManagerWindow")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_set_thumbnail_icon")

    guarded = False
    for node in ast.walk(fn):
        if isinstance(node, ast.Try):
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Attribute)
                    and inner.func.attr == "winfo_children"
                ):
                    guarded = True
    assert guarded, "_set_thumbnail_icon 必须在 try 里调用 winfo_children（D-114）"
    assert "_on_thumbnail_done" in RESOURCE_MANAGER.read_text(encoding="utf-8")


def test_thumbnail_apply_keeps_counting_after_dead_frame():
    """``_apply_thumbnail`` 的目标控件没了也要继续走完（进度计数不受影响）。"""
    src = RESOURCE_MANAGER.read_text(encoding="utf-8")
    tree = ast.parse(src)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "ResourceManagerWindow")
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_apply_thumbnail")
    calls = [
        ast.unparse(node.func)
        for node in ast.walk(fn)
        if isinstance(node, ast.Call)
    ]
    assert "self._set_thumbnail_icon" in calls
    assert "self._on_thumbnail_done" in calls
    # _on_thumbnail_done 不能包在"只有画成功才走"的分支里：它必须与绘制同级
    assert not any(
        isinstance(node, ast.If) and "self._on_thumbnail_done" in ast.unparse(node)
        for node in ast.walk(fn)
    ), "缩略图进度计数不该被'控件是否还活着'左右"
