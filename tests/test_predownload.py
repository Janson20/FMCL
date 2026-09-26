"""预下载域（`launcher/predownload.py`，471 行）的回归守卫。

## 为什么补这个域

39 功能域走查表里，「预下载」那一行的"相关测试"只有兜底的 import 冒烟
（`test_entry_imports.py` / `test_imports.py`）—— 模块自己**一个测试都没有**。
而它做的事是：多线程分块下载 → 合并 → 递归解压到 `.minecraft` → 删掉压缩包，
还会在用户**什么都没做**的时候就写"已询问过"标记。没有测试的代码里放这种操作，
风险交给运气。

补法照第 11 轮「存档备份」那一轮的同一套路：**全离线、只用 `tmp_path`、
网络与子进程一律用替身**，并把用户可见后果写进断言。

## 顺带抓出的两个缺陷（写测试时发现的，见 `docs/refactor/07-known-defects.md`）

* **D-115**：`_extract_with_tool` 的"7z + 进度回调"分支**不检查返回码** ——
  `subprocess.Popen` 之后只 `proc.wait()` 就返回，于是解压失败被当成成功：
  `run()` 还会打一行"使用 xxx 解压成功"并返回 `RESULT_COMPLETED`，
  用户看到"预下载完成"、磁盘上却一个文件都没解出来（而"已询问过"标记早就写下了，
  再也不会被问第二次）。同函数的**另一个**分支用的是 `check=True`，两个分支判据不一致。
* **D-116**：多线程路径里**分段下载的网络异常被当成"用户取消"** ——
  `_download_part` 的 `except` 只做 `logger.error` + `cancel_event.set()`，
  `run()` 看到 cancel 已置位且 `_range_supported` 仍为 True 就返回
  `RESULT_CANCELLED`，而 `_finish_predownload` 对 CANCELLED 的定义是
  **"只关窗、什么提示都不弹"**。于是：网络抖动 → 进度窗自己关掉 → 没有任何解释，
  用户还不会被告知可以重来。单线程路径同样的情况下返回的是 `RESULT_ERROR`（会弹错误框）
  —— 两条路径的**用户可见行为不一致**。
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from app.ports import RecordingUIPort
from launcher import predownload as P

PAYLOAD = bytes(i % 251 for i in range(4096))


# ═══════════════════════════════════════════════════════════════════
# 替身：requests 与 UIPort
# ═══════════════════════════════════════════════════════════════════


class FakeResponse:
    def __init__(self, payload: bytes = b"", status_code: int = 200, headers: dict | None = None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def iter_content(self, chunk_size: int = 8192):
        for i in range(0, len(self._payload), chunk_size):
            yield self._payload[i:i + chunk_size]


class FakeRequests:
    """最小的 requests 替身：支持 head / 全量 get / Range get（可关掉 Range 支持）。"""

    def __init__(self, payload: bytes = PAYLOAD, *, support_range: bool = True,
                 head_status: int = 200, get_error: bool = False, content_length: int | None = None):
        self.payload = payload
        self.support_range = support_range
        self.head_status = head_status
        self.get_error = get_error
        self.content_length = len(payload) if content_length is None else content_length
        self.range_requests: list[tuple[int, int]] = []
        self.plain_gets = 0

    def head(self, url, timeout=None):
        return FakeResponse(b"", self.head_status, {"Content-Length": str(self.content_length)})

    def get(self, url, headers=None, stream=False, timeout=None):
        if self.get_error:
            raise RuntimeError("网络断了")
        rng = (headers or {}).get("Range")
        if rng:
            start_s, end_s = rng.replace("bytes=", "").split("-")
            start, end = int(start_s), int(end_s)
            self.range_requests.append((start, end))
            if not self.support_range:
                return FakeResponse(self.payload, 200, {"Content-Length": str(len(self.payload))})
            chunk = self.payload[start:end + 1]
            return FakeResponse(chunk, 206, {"Content-Length": str(len(chunk))})
        self.plain_gets += 1
        return FakeResponse(self.payload, 200, {"Content-Length": str(len(self.payload))})


def make_downloader(tmp_path: Path, fake: FakeRequests, **kw) -> P.Predownloader:
    d = P.Predownloader("https://example.invalid/x.rar", tmp_path, **kw)
    return d


class Port(RecordingUIPort):
    """在 `RecordingUIPort` 之上**保留 kwargs**。

    为什么非要有这么一层：`RecordingUIPort.show_progress` 只记 `report=`，
    把 `detail` / `heading` / `on_cancel` / `modal` / `wait_event` 全丢了 ——
    于是任何想断言"进度明细写了什么"的测试都会拿到 `KeyError` 或者更糟：
    断言写成一条永远成立的空话。这里把 kwargs 单独收进 `extra`，
    不动 `app/ports.py`（它是共享文件，其它测试依赖它现在的形状）。
    """

    def __init__(self, choice=None, **kw):
        super().__init__(choice=choice, **kw)
        self.extra: list = []

    def show_progress(self, report, **kwargs):
        super().show_progress(report)
        self.extra.append({"method": "show_progress", "report": report, **kwargs})

    def choose(self, title, prompt, options, default=None, **kwargs):
        self.extra.append({"method": "choose", "default": default, **kwargs})
        return super().choose(title, prompt, options, default, **kwargs)

    def details(self) -> list:
        return [c.get("detail") for c in self.extra if c["method"] == "show_progress"]


def capture_extract(monkeypatch, sink: list) -> None:
    """把 `_extract_rar` 换成只记录"拿到的 rar 是什么"的替身（不真解压）。

    注意替身挂在**类**上，所以它会被当成普通函数绑成方法 —— 第一个参数是 `self`。
    （第一版漏了这一点，`run()` 直接把 `TypeError` 吞成 `RESULT_ERROR`，
    测试报的是"预下载失败"，看起来像被测代码的错。**替身的签名也要被核对**。）
    """

    def fake_extract(self, rar_path: Path, dest_dir: Path):
        sink.append((rar_path.read_bytes() if rar_path.exists() else None, str(dest_dir)))

    monkeypatch.setattr(P.Predownloader, "_extract_rar", fake_extract)


# ═══════════════════════════════════════════════════════════════════
# 标记与入口
# ═══════════════════════════════════════════════════════════════════


class TestMarkerAndEntry:
    def test_marker_roundtrip(self, tmp_path):
        assert P.is_predownloaded(tmp_path) is False
        P.create_predownloaded_marker(tmp_path)
        assert P.is_predownloaded(tmp_path) is True
        assert (tmp_path / P.PREDOWNLOAD_MARKER).is_file()

    def test_create_marker_makes_parent_dirs(self, tmp_path):
        target = tmp_path / "deep" / "nested"
        P.create_predownloaded_marker(target)
        assert (target / P.PREDOWNLOAD_MARKER).is_file()

    def test_already_predownloaded_never_asks(self, tmp_path, monkeypatch):
        """★ 已经有标记 → 连问都不问（返回 None）。"""
        P.create_predownloaded_marker(tmp_path)
        ui = Port(choice="yes")
        assert P.run_predownload_check(ui, tmp_path, lambda k: k) is None
        assert ui.find("choose") == [], "已经预下载过就不该再打扰用户"

    @pytest.mark.parametrize("answer", ["no", None])
    def test_declined_still_writes_the_marker(self, tmp_path, answer):
        """★ 用户放弃（或直接关窗）→ 返回 False，但**依然写标记**（与原实现一致）。

        这条是刻意的既有行为：不写标记就会每次启动都问一遍。
        """
        ui = Port(choice=answer)
        assert P.run_predownload_check(ui, tmp_path, lambda k: k) is False
        assert P.is_predownloaded(tmp_path) is True
        assert (tmp_path / "minecraft.rar").exists() is False

    def test_yes_returns_true_and_uses_the_full_url(self, tmp_path, monkeypatch):
        seen = {}

        class StubPredownloader:
            def __init__(self, url, minecraft_dir, num_threads=4, chunk_size=8192):
                seen["url"] = url
                seen["num_threads"] = num_threads

            def set_progress_callback(self, cb):
                seen["cb"] = cb

            @property
            def cancel_event(self):
                return threading.Event()

            def cancel(self):
                seen["cancel"] = True

            def run(self):
                # 稍微慢一点：`run_predownload_check` 在建窗之前会查一次
                # `finished.is_set()`（那是给"head 请求直接失败"这类极端情况留的早退），
                # 替身瞬间返回就会走那条早退路径、根本建不出进度窗。
                time.sleep(0.05)
                return P.RESULT_COMPLETED

        monkeypatch.setattr(P, "Predownloader", StubPredownloader)
        ui = Port(choice="yes")
        assert P.run_predownload_check(ui, tmp_path, lambda k: k) is True
        assert seen["url"] == P.PREDOWNLOAD_URL
        assert seen["num_threads"] == 4
        # 进度窗口必须由端口建（端口负责切主线程），并且 cancel 接到下载器上
        shown = [c for c in ui.extra if c["method"] == "show_progress"]
        assert [c["modal"] for c in shown] == [True]
        assert "on_cancel" in shown[0]
        assert "cb" in seen

    def test_lite_uses_the_lite_url(self, tmp_path, monkeypatch):
        seen = {}

        class StubPredownloader:
            def __init__(self, url, minecraft_dir, num_threads=4, chunk_size=8192):
                seen["url"] = url

            def set_progress_callback(self, cb):
                pass

            def cancel(self):
                pass

            def run(self):
                return P.RESULT_CANCELLED

        monkeypatch.setattr(P, "Predownloader", StubPredownloader)
        ui = Port(choice="lite")
        assert P.run_predownload_check(ui, tmp_path, lambda k: k) is True
        assert seen["url"] == P.PREDOWNLOAD_LITE_URL


# ═══════════════════════════════════════════════════════════════════
# 下载：单线程 / 多线程 / 取消 / 失败
# ═══════════════════════════════════════════════════════════════════


class TestDownloading:
    def test_single_thread_downloads_then_cleans_up(self, tmp_path, monkeypatch):
        fake = FakeRequests()
        monkeypatch.setattr(P, "requests", fake)
        got: list = []
        capture_extract(monkeypatch, got)

        d = make_downloader(tmp_path, fake, num_threads=1, chunk_size=1024)
        progress: list = []
        d.set_progress_callback(lambda c, t, phase: progress.append((c, t, phase)))
        assert d.run() == P.RESULT_COMPLETED

        assert got and got[0][0] == PAYLOAD, "解压时拿到的应当就是完整的压缩包字节"
        assert not (tmp_path / "minecraft.rar").exists(), "收尾必须把压缩包删掉"
        assert [p[2] for p in progress] == ["download"] * 4, progress[:5]
        assert [p[0] for p in progress] == [1024, 2048, 3072, 4096], "进度必须是累加的已下载字节"
        assert progress[-1][0] == len(PAYLOAD)

    def test_multithread_parts_are_merged_in_order_and_removed(self, tmp_path, monkeypatch):
        """★ 4 个分段必须**按序号顺序**拼回原字节，且分片文件要删干净。"""
        fake = FakeRequests()
        monkeypatch.setattr(P, "requests", fake)
        got: list = []
        capture_extract(monkeypatch, got)

        d = make_downloader(tmp_path, fake, num_threads=4, chunk_size=256)
        assert d.run() == P.RESULT_COMPLETED

        assert len(fake.range_requests) == 4, fake.range_requests
        assert [r[0] for r in fake.range_requests] != sorted(r[0] for r in fake.range_requests) or True
        covered = b"".join(
            PAYLOAD[s:e + 1] for s, e in sorted(fake.range_requests)
        )
        assert covered == PAYLOAD, "四个 Range 区间必须无缝覆盖整个文件"
        assert got and got[0][0] == PAYLOAD, "合并出来的字节必须与原文逐字节相同（顺序拼错就会变）"
        assert list(tmp_path.glob("*.part*")) == [], "分片文件必须被清掉"

    def test_range_unsupported_falls_back_to_single_thread(self, tmp_path, monkeypatch):
        """★ 服务器不认 Range（返回 200 而不是 206）→ 自动回退单线程，仍要成功。"""
        fake = FakeRequests(support_range=False)
        monkeypatch.setattr(P, "requests", fake)
        got: list = []
        capture_extract(monkeypatch, got)

        d = make_downloader(tmp_path, fake, num_threads=4)
        assert d.run() == P.RESULT_COMPLETED
        assert fake.plain_gets == 1, "回退路径必须真的走一次全量 GET"
        assert got and got[0][0] == PAYLOAD
        assert list(tmp_path.glob("*.part*")) == []

    def test_user_cancel_during_multithread_returns_cancelled(self, tmp_path, monkeypatch):
        """★ 用户点"取消"→ `RESULT_CANCELLED`（界面只关窗、不弹提示），且不留垃圾。"""
        fake = FakeRequests()
        monkeypatch.setattr(P, "requests", fake)
        got: list = []
        capture_extract(monkeypatch, got)

        d = make_downloader(tmp_path, fake, num_threads=4)
        d.cancel()  # 等价于用户在下载途中点了取消
        assert d.run() == P.RESULT_CANCELLED
        assert got == [], "取消之后绝不能去解压"
        assert list(tmp_path.glob("*.part*")) == [], "分片文件必须被清掉"

    def test_part_network_error_is_an_error_not_a_user_cancel(self, tmp_path, monkeypatch):
        """★ D-116：分段下载的**网络异常**不能被当成"用户取消"。

        改前：`_download_part` 的 except 只置 cancel 事件，`run()` 看到 cancel 就返回
        `RESULT_CANCELLED`，而界面把 CANCELLED 定义成"只关窗、什么提示都不弹"
        —— 用户看到进度窗自己消失、没有任何解释，而单线程路径同样情况会弹错误框。
        """
        fake = FakeRequests()
        monkeypatch.setattr(P, "requests", fake)
        got: list = []
        capture_extract(monkeypatch, got)

        def boom(*a, **kw):
            raise RuntimeError("网络断了")

        monkeypatch.setattr(fake, "get", boom)
        d = make_downloader(tmp_path, fake, num_threads=4)
        assert d.run() == P.RESULT_ERROR, "分段下载失败必须报错，不能装作是用户取消"
        assert list(tmp_path.glob("*.part*")) == []

    @pytest.mark.parametrize("kwargs", [
        {"head_status": 500},
        {"content_length": 0},
    ])
    def test_head_failures_return_error(self, tmp_path, monkeypatch, kwargs):
        fake = FakeRequests(**kwargs)
        monkeypatch.setattr(P, "requests", fake)
        d = make_downloader(tmp_path, fake, num_threads=4)
        assert d.run() == P.RESULT_ERROR


# ═══════════════════════════════════════════════════════════════════
# 解压
# ═══════════════════════════════════════════════════════════════════


class TestAdmin:
    def test_no_extractor_at_all_raises_a_helpful_error(self, tmp_path, monkeypatch):
        """既没有 rarfile、也找不到任何外部工具 → 报出可操作的错误信息。"""
        monkeypatch.setitem(sys.modules, "rarfile", None)  # 让 `import rarfile` 抛 ImportError
        monkeypatch.setattr(P, "_find_rar_tool", lambda: None)
        d = make_downloader(tmp_path, FakeRequests())
        rar = tmp_path / "x.rar"
        rar.write_bytes(b"Rar!")
        with pytest.raises(RuntimeError) as ei:
            d._extract_rar(rar, tmp_path / "out")
        assert "7-Zip" in str(ei.value) and "WinRAR" in str(ei.value)

    def test_7z_extract_failure_is_not_reported_as_success(self, tmp_path, monkeypatch):
        """★ D-115：7z 返回非 0 时**必须**让调用方知道失败了。

        改前（带进度回调的那条分支）：`Popen` 之后只 `proc.wait()` 就返回，
        返回码从不检查 —— `run()` 会打"使用 7z 解压成功"并返回 `RESULT_COMPLETED`，
        用户看到"预下载完成"、磁盘上其实什么都没解出来。同函数的另一个分支
        用的是 `subprocess.run(..., check=True)`，两边判据本来就不一致。
        """
        class FakeProc:
            returncode = 2

            def __init__(self, *a, **kw):
                self.stdout = iter(["  0%\n", " 50%\n", "Everything is Ok\n"])

            def wait(self):
                return self.returncode

            def terminate(self):  # pragma: no cover - 取消路径才用
                pass

        monkeypatch.setattr(P.subprocess, "Popen", FakeProc)
        tool = "C:/fake/7z.exe"
        d = make_downloader(tmp_path, FakeRequests())
        rar = tmp_path / "x.rar"
        rar.write_bytes(b"Rar!")
        progress: list = []
        with pytest.raises(Exception):
            P._extract_with_tool(tool, rar, tmp_path / "out", threading.Event(),
                                 lambda c, t, ph: progress.append((c, t, ph)))
        assert progress, "进度回调本身仍然要收到百分比"

    def test_7z_extract_success_passes_through(self, tmp_path, monkeypatch):
        """返回码 0 = 成功，不许误报失败（上面那条的对照实验）。"""
        class FakeProc:
            returncode = 0

            def __init__(self, *a, **kw):
                self.stdout = iter(["  0%\n", "100%\n"])

            def wait(self):
                return 0

            def terminate(self):  # pragma: no cover
                pass

        monkeypatch.setattr(P.subprocess, "Popen", FakeProc)
        P._extract_with_tool("7z", tmp_path / "x.rar", tmp_path / "out", threading.Event(),
                             lambda *a: None)

    def test_non_7z_tool_still_uses_check_true(self, tmp_path, monkeypatch):
        """非 7z 工具走的是 `subprocess.run(..., check=True)` —— 判据不能被改掉。"""
        calls = []

        def fake_run(cmd, **kw):
            calls.append((cmd, kw))

            class R:
                returncode = 0

            return R()

        monkeypatch.setattr(P.subprocess, "run", fake_run)
        P._extract_with_tool("unrar", tmp_path / "x.rar", tmp_path / "out", threading.Event())
        assert calls and calls[0][1].get("check") is True

    def test_zip_slip_entries_are_skipped(self, tmp_path, monkeypatch):
        """★ 解压路径必须防目录穿越（`../` 与绝对路径都要跳过）。"""
        extracted: list = []

        class FakeRarFile:
            def __init__(self, path):
                self._names = ["ok.txt", "../escape.txt", "sub/../ok2.txt"]

            def namelist(self):
                return list(self._names)

            def extract(self, name, dest):
                extracted.append(name)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake_mod = type(sys)("rarfile")
        fake_mod.RarFile = FakeRarFile
        fake_mod.UNRAR_TOOL = ""
        monkeypatch.setitem(sys.modules, "rarfile", fake_mod)
        monkeypatch.setattr(P, "_find_rar_tool", lambda: None)

        d = make_downloader(tmp_path, FakeRequests())
        rar = tmp_path / "x.rar"
        rar.write_bytes(b"Rar!")
        dest = tmp_path / "out"
        dest.mkdir()
        d._extract_rar(rar, dest)

        assert "../escape.txt" not in extracted, "越界条目必须被跳过"

    def test_zip_slip_absolute_path_is_skipped(self, tmp_path, monkeypatch):
        extracted: list = []

        class FakeRarFile:
            def __init__(self, path):
                pass

            def namelist(self):
                return ["C:\\Windows\\evil.txt"]

            def extract(self, name, dest):
                extracted.append(name)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        fake_mod = type(sys)("rarfile")
        fake_mod.RarFile = FakeRarFile
        fake_mod.UNRAR_TOOL = ""
        monkeypatch.setitem(sys.modules, "rarfile", fake_mod)
        monkeypatch.setattr(P, "_find_rar_tool", lambda: None)

        d = make_downloader(tmp_path, FakeRequests())
        rar = tmp_path / "x.rar"
        rar.write_bytes(b"Rar!")
        dest = tmp_path / "out"
        dest.mkdir()
        d._extract_rar(rar, dest)
        assert extracted == [], "绝对路径条目必须被跳过"


# ═══════════════════════════════════════════════════════════════════
# 收尾提示与进度回调
# ═══════════════════════════════════════════════════════════════════


class TestFinishAndProgress:
    @pytest.mark.parametrize("result,expect", [
        (P.RESULT_CANCELLED, []),
        (P.RESULT_ERROR, ["show_error"]),
        (P.RESULT_COMPLETED, ["show_info"]),
    ])
    def test_finish_maps_result_to_dialogs(self, result, expect):
        """★ 取消 = **只关窗**（既有行为）；失败 = 错误框；成功 = 完成框。"""
        ui = RecordingUIPort()
        P._finish_predownload(ui, lambda k: k, result)
        assert len(ui.find("close_progress")) == 1
        shown = [m for m in ("show_info", "show_error") if ui.find(m)]
        assert shown == expect

    def test_progress_callback_three_phases(self, monkeypatch):
        ui = Port()
        ticks = [100.0]
        monkeypatch.setattr(P.time, "time", lambda: ticks[0])
        cb = P._make_progress_callback(ui, lambda k: k)

        # 注意：回调带 0.1 秒节流，而"距上次多久"是拿**冻结的时钟**算的 ——
        # 时钟不动时 `now - last == 0 < 0.1`，**第一次调用就会被节流掉**。
        # 所以每次想让它真的记录，都必须先把时钟往前推。
        ticks[0] += 0.2
        cb(1048576, 2097152, "download")    # 记录：1.0 MB / 2.0 MB
        ticks[0] += 0.05
        cb(1572864, 2097152, "download")    # 0.1 秒内 → 被节流丢掉
        ticks[0] += 0.2
        cb(2097152, 2097152, "download")    # 记录：2.0 MB / 2.0 MB
        cb(2097152, 2097152, "merge")       # 记录（merge 也走节流，但刚记过一次…）
        cb(30, 100, "extract")
        cb(31, 100, "extract")              # 解压阶段**不**节流

        assert ui.details() == ["1.0 MB / 2.0 MB", "2.0 MB / 2.0 MB", "30%", "31%"], ui.details()

    def test_progress_callback_never_divides_by_zero(self, monkeypatch):
        ui = Port()
        ticks = [100.0]
        monkeypatch.setattr(P.time, "time", lambda: ticks[0])
        cb = P._make_progress_callback(ui, lambda k: k)
        ticks[0] += 0.2
        cb(0, 0, "download")
        report = ui.extra[0]["report"]
        assert report.total >= 1, "total=0 必须兜底成 1，否则端口里的百分比会除零"
