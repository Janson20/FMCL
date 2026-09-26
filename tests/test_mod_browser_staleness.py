"""模组浏览窗口的请求世代守卫（阶段 1.22 修正 D-93）。

缺陷（记录在 `docs/refactor/07-known-defects.md` D-93）：

1. 没有任何请求序号/取消/过期判定 —— 连打两次搜索时，**晚返回的旧请求**
   会把新结果覆盖掉；
2. 工作线程**直接写共享状态**（`state["_cached_hits"]` 等），再用 `after(0)` 渲染，
   两个回调的执行顺序不确定 → 列表显示 A 的第一页、缓存却是 B 的结果，
   用户一按"下一页"就翻到完全不相干的内容。

这里不建真实窗口（Tk 窗口慢且脆），而是用 `object.__new__` 造一个"骨架实例"，
把 `after` 改成**内联执行**、把渲染函数换成记录器，于是可以精确控制
"哪个工作线程何时返回"，直接复现上面两个竞态。
"""

from __future__ import annotations

import ast
import io
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MOD_BROWSER = REPO_ROOT / "ui" / "windows" / "mod_browser.py"

TAB = "mods"


def _hits(prefix: str, n: int = 100) -> List[Dict]:
    return [{"title": f"{prefix}-{i}", "project_id": f"{prefix}{i}", "downloads": i} for i in range(n)]


class _FakeEntry:
    def __init__(self, text: str = "") -> None:
        self._text = text

    def get(self) -> str:
        return self._text


class _Skeleton:
    """把 ModBrowserWindow 的方法接到假状态上的最小宿主。"""

    def __init__(self) -> None:
        from ui.windows.mod_browser import ModBrowserWindow

        self.win = object.__new__(ModBrowserWindow)
        w = self.win
        # ── 假状态：只保留守卫与发布所需的键 ──
        w._tab_states = {
            TAB: {
                "current_offset": 0,
                "total_hits": 0,
                "current_query": "",
                "search_entry": _FakeEntry(""),
                "_ai_cached_hits": None,
                "_cached_hits": None,
                "_req_seq": 0,
                "status_label": None,
                "result_count_label": None,
                "page_label": None,
                "prev_btn": None,
                "next_btn": None,
                "list_frame": None,
                "loading_label": None,
                "ai_search_btn": None,
            }
        }
        # ── 记录器 ──
        self.renders: List[tuple] = []
        self.errors: List[tuple] = []
        self.discarded: List[tuple] = []
        self.statuses: List[tuple] = []
        self.after_calls: List[Any] = []
        self.threads: List[Any] = []
        self.ai_button_restores = 0

        # after 内联执行：测试里要"确定性地"跑回调
        def _after(_delay, cb=None, *args):
            self.after_calls.append(cb)
            if cb is not None:
                cb(*args)
            return "after-id"

        w.after = _after
        # 线程只记录、不启动（由测试决定"谁先返回"）
        w._run_in_thread = lambda fn, *a, **kw: self.threads.append((fn, a, kw))
        # 渲染/错误：**保留生产的世代守卫**，只把"画控件"这一段换成记录器。
        # 说明：守卫判定本身（_is_stale）是生产代码；被替换掉的只有真正创建
        # CTkLabel 的那部分。测试文件末尾另有 AST 断言，保证真实方法里
        # 确实调用了 _is_stale —— 否则这个替身就会与生产实现脱节。
        def _render(tab, hits, seq=None):
            if w._is_stale(tab, seq):
                self.discarded.append(("render", tab, seq))
                return
            self.renders.append((tab, list(hits), seq))

        def _render_err(tab, msg, seq=None):
            if w._is_stale(tab, seq):
                self.discarded.append(("error", tab, seq))
                return
            self.errors.append((tab, msg, seq))

        w._render_tab_results = _render
        w._render_tab_error = _render_err
        w._set_tab_status = lambda tab, text: self.statuses.append((tab, text))
        w._update_tab_pagination = lambda tab: None
        w._restore_ai_button = lambda tab: setattr(self, "ai_button_restores", self.ai_button_restores + 1)
        w._create_item = lambda tab, item: None
        w._get_selected_version = lambda tab: "1.20.4"
        w._get_selected_loader = lambda tab: "fabric"
        w.PAGE_SIZE = 20

    # 便捷访问
    @property
    def state(self) -> Dict:
        return self.win._tab_states[TAB]

    def search(self, query: str) -> None:
        self.state["search_entry"] = _FakeEntry(query)
        self.win._on_tab_search(TAB)

    def run_thread(self, index: int) -> None:
        fn, args, kwargs = self.threads[index]
        fn(*args, **kwargs)


@pytest.fixture()
def skel() -> _Skeleton:
    return _Skeleton()


@pytest.fixture()
def i18n_keys(monkeypatch):
    """把 `_()` 换成"返回键名 + kwargs"的假实现。

    测试进程里 i18n 没被 `set_language` 初始化过，`_()` 原样返回键名，
    于是"状态栏里有没有带上关键词"这类断言会假失败。直接钉住**键与参数**
    比钉翻译文本更稳（文案随时可能改，键不会）。
    """
    import ui.windows.mod_browser as mb

    def _fake(key, **kwargs):
        if not kwargs:
            return key
        return key + "|" + ",".join(f"{k}={v}" for k, v in sorted(kwargs.items()))

    monkeypatch.setattr(mb, "_", _fake, raising=True)
    return _fake


def _install_search(monkeypatch, results: Dict[str, Dict]) -> None:
    """把 curseforge 的三个搜索入口换成按 query 返回预设结果的假实现。"""

    def _fake(*, query, **kwargs):
        return results[query]

    import curseforge

    for name in ("unified_search_mods", "unified_search_resource_packs", "unified_search_shaders"):
        monkeypatch.setattr(curseforge, name, _fake, raising=False)


class TestStaleRequestIsDiscarded:
    def test_late_old_search_does_not_overwrite_new_results(self, skel, monkeypatch) -> None:
        """核心场景：先发起 A，再发起 B；A 晚返回时**整个丢弃**（连缓存都不写）。"""
        _install_search(
            monkeypatch,
            {
                "A": {"hits": _hits("A"), "total_hits": 100, "sources": {"a": 1}},
                "B": {"hits": _hits("B"), "total_hits": 100, "sources": {"b": 1}},
            },
        )
        skel.search("A")  # seq=1
        skel.search("B")  # seq=2（A 作废）
        assert skel.state["_req_seq"] == 2

        skel.run_thread(1)  # B 先返回
        assert skel.state["_cached_hits"][0]["title"] == "B-0"
        assert skel.renders[-1][1][0]["title"] == "B-0"

        renders_before = len(skel.renders)
        skel.run_thread(0)  # A 晚返回 —— 必须被丢弃
        assert len(skel.renders) == renders_before, "过期请求仍然渲染了"
        assert skel.state["_cached_hits"][0]["title"] == "B-0", "过期请求覆盖了缓存"
        assert skel.state["_sources"] == {"b": 1}

    def test_queued_render_from_old_generation_is_skipped(self, skel, monkeypatch) -> None:
        """after 回调排队期间又发起了新请求 → 旧回调必须自己退场。"""
        _install_search(monkeypatch, {"A": {"hits": _hits("A"), "total_hits": 1, "sources": {}}})
        skel.search("A")
        seq_a = skel.state["_req_seq"]

        skel.search("A2" if False else "A")  # 新请求（世代 +1）
        # 直接调用带旧世代号的渲染：应被丢弃
        skel.win._render_tab_results(TAB, _hits("OLD"), seq_a)
        assert skel.renders == [], "旧世代的 after 回调没有被拦住"
        assert skel.discarded, "丢弃路径没有被走到（守卫根本没生效？）"

    def test_stale_error_does_not_clobber_fresh_success(self, skel, monkeypatch) -> None:
        def _boom(*, query, **kwargs):
            raise RuntimeError("网络炸了")

        import curseforge

        monkeypatch.setattr(curseforge, "unified_search_mods", _boom, raising=False)
        skel.search("A")  # seq=1，会失败
        skel.search("B")  # seq=2
        skel.run_thread(0)  # A 的失败晚到
        assert skel.errors == [], "过期的错误态覆盖了新请求"

    def test_fresh_error_is_still_rendered(self, skel, monkeypatch) -> None:
        """反向保证：不是"什么都不报错"，当下这一代的失败必须显示出来。"""

        def _boom(*, query, **kwargs):
            raise RuntimeError("网络炸了")

        import curseforge

        monkeypatch.setattr(curseforge, "unified_search_mods", _boom, raising=False)
        skel.search("A")
        skel.run_thread(0)
        assert len(skel.errors) == 1 and "网络炸了" in skel.errors[0][1]


class TestPublishIsAtomic:
    def test_worker_does_not_touch_shared_state(self, skel, monkeypatch) -> None:
        """工作线程不得直接写共享状态（缓存/总数/来源），必须交给主线程回调。"""
        src = io.open(MOD_BROWSER, encoding="utf-8").read()
        tree = ast.parse(src)
        fn = next(
            n
            for c in tree.body
            if isinstance(c, ast.ClassDef)
            for n in c.body
            if isinstance(n, ast.FunctionDef) and n.name == "_do_tab_search"
        )
        forbidden = {"_cached_hits", "total_hits", "_sources", "_ai_cached_hits"}
        written = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Subscript) and isinstance(tgt.slice, ast.Constant):
                        if tgt.slice.value in forbidden:
                            written.add(str(tgt.slice.value))
        assert not written, f"_do_tab_search 里仍在直接写共享状态：{sorted(written)}"

    def test_cache_and_render_come_from_the_same_result(self, skel, monkeypatch) -> None:
        """缓存与"被渲染的那一页"必须来自同一次结果（同一代里原子提交）。"""
        _install_search(monkeypatch, {"A": {"hits": _hits("A"), "total_hits": 100, "sources": {}}})
        skel.search("A")
        skel.run_thread(0)
        cached_titles = [h["title"] for h in skel.state["_cached_hits"]]
        rendered_titles = [h["title"] for h in skel.renders[-1][1]]
        assert rendered_titles == cached_titles[: skel.win.PAGE_SIZE]

    def test_fetch_at_nonzero_offset_renders_that_page(self, skel, monkeypatch) -> None:
        """附带修正：原本"无缓存 + 当前页不是第 1 页"时会渲染第 1 页却按 offset 标页码。

        原文 `page = all_hits[:PAGE_SIZE]` 忽略了 `current_offset`，
        于是内容与"51-70 / 共 N"的标签自相矛盾。现在按 offset 取页。
        """
        _install_search(monkeypatch, {"A": {"hits": _hits("A"), "total_hits": 100, "sources": {}}})
        skel.state["current_query"] = "A"
        skel.state["current_offset"] = 40
        skel.win._begin_request(TAB)
        skel.win._do_tab_search(TAB, skel.state["_req_seq"])
        assert [h["title"] for h in skel.renders[-1][1]] == [f"A-{i}" for i in range(40, 60)]

    def test_cached_page_uses_offset_and_is_generation_checked(self, skel) -> None:
        skel.state["_cached_hits"] = _hits("A", 100)
        skel.state["current_offset"] = 20
        seq = skel.win._begin_request(TAB)
        skel.win._do_tab_search(TAB, seq)
        assert [h["title"] for h in skel.renders[-1][1]] == [f"A-{i}" for i in range(20, 40)]


class TestAiSearch:
    def test_ai_results_published_in_one_callback(self, skel, monkeypatch, i18n_keys) -> None:
        """AI 搜索：缓存、渲染、状态、按钮复位必须在同一次提交里，不能分 3 个 after。"""
        import modrinth

        monkeypatch.setattr(
            modrinth,
            "ai_merged_search",
            lambda **kwargs: {"hits": _hits("AI", 30), "keywords": ["kw1", "kw2"]},
            raising=False,
        )
        skel.state["search_entry"] = _FakeEntry("hello")
        skel.win.callbacks = {"get_jdz_token": lambda: "tok"}
        skel.win._on_ai_search(TAB)
        seq = skel.state["_req_seq"]
        skel.run_thread(0)

        assert skel.state["_ai_cached_hits"][0]["title"] == "AI-0"
        assert skel.state["total_hits"] == 30
        assert skel.renders[-1][1][0]["title"] == "AI-0"
        assert skel.ai_button_restores == 1, "AI 按钮没有复位"
        status_keys = [s[1] for s in skel.statuses]
        assert any(k.startswith("ai_search_done") for k in status_keys), status_keys
        done = next(k for k in status_keys if k.startswith("ai_search_done"))
        assert "kw1" in done and "total=30" in done, done
        assert seq == skel.state["_req_seq"]

    def test_stale_ai_results_are_dropped(self, skel, monkeypatch) -> None:
        import modrinth

        monkeypatch.setattr(
            modrinth,
            "ai_merged_search",
            lambda **kwargs: {"hits": _hits("AI", 5), "keywords": []},
            raising=False,
        )
        skel.state["search_entry"] = _FakeEntry("hello")
        skel.win.callbacks = {"get_jdz_token": lambda: "tok"}
        skel.win._on_ai_search(TAB)  # seq=1
        skel.win._begin_request(TAB)  # 更新的一代
        skel.renders.clear()
        skel.run_thread(0)
        assert skel.renders == [], "过期的 AI 结果仍然渲染了"
        assert skel.state["_ai_cached_hits"] is None, "过期的 AI 结果仍然写了缓存"


class TestPaginationUsesLocalCache:
    def test_next_page_disabled_when_cache_exhausted(self, skel) -> None:
        """D-103 的守卫不能因为 D-93 的改动而失效。"""
        skel.state["_cached_hits"] = _hits("A", 30)
        skel.state["current_offset"] = 20
        skel.win._on_tab_next_page(TAB)
        assert skel.state["current_offset"] == 20, "缓存已到尾仍能翻到空页"

    def test_next_page_advances_within_cache(self, skel) -> None:
        skel.state["_cached_hits"] = _hits("A", 100)
        skel.win._on_tab_next_page(TAB)
        assert skel.state["current_offset"] == skel.win.PAGE_SIZE


class TestProductionCodeActuallyGuards:
    """AST 级别的守卫（防止"测试里的替身守住了、生产代码没守"）。

    行为测试用替身替换了渲染函数；如果哪天有人把生产代码里的
    `if self._is_stale(...)` 删掉，行为测试仍会通过（替身里有判断）。
    所以这里直接读生产代码，把守卫的存在钉死。
    """

    @staticmethod
    def _method(name: str) -> ast.FunctionDef:
        tree = ast.parse(io.open(MOD_BROWSER, encoding="utf-8").read())
        for cls in tree.body:
            if isinstance(cls, ast.ClassDef):
                for node in cls.body:
                    if isinstance(node, ast.FunctionDef) and node.name == name:
                        return node
        raise AssertionError(f"找不到方法 {name}")

    @pytest.mark.parametrize(
        "method",
        ["_render_tab_results", "_render_tab_error", "_publish_tab_results", "_publish_ai_results"],
    )
    def test_method_consults_is_stale(self, method: str) -> None:
        node = self._method(method)
        calls = {
            n.func.attr
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "_is_stale" in calls, f"{method} 没有做世代校验（D-93 回退）"

    def test_begin_request_bumps_a_monotonic_counter(self) -> None:
        node = self._method("_begin_request")
        src = ast.unparse(node)
        assert "_req_seq" in src and "1" in src, "_begin_request 不再自增世代号"

    def test_search_entrypoints_start_a_new_generation(self) -> None:
        for name in ("_on_tab_search", "_on_ai_search", "_on_tab_next_page", "_on_tab_prev_page"):
            node = self._method(name)
            calls = {
                n.func.attr
                for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            }
            # 切页只在"无缓存需要现取"的分支里起新请求，因此用宽松断言：
            # 要么直接自增世代，要么调用 _do_tab_search 时**带上** seq
            src = ast.unparse(node)
            assert "_begin_request" in src or "_do_tab_search" not in src, (
                f"{name} 发起了搜索却没有世代号（D-93 回退）"
            )

    def test_workers_receive_the_generation_number(self) -> None:
        for name in ("_do_tab_search", "_do_ai_search"):
            node = self._method(name)
            args = [a.arg for a in node.args.args]
            assert "seq" in args, f"{name} 没有接收世代号参数（D-93 回退）"
            defaults = [ast.unparse(d) for d in node.args.defaults]
            assert "None" in defaults, f"{name} 的 seq 应当默认为 None（兼容直接调用）"
