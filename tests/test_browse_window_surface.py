"""六个浏览/整合包窗口的**公开面冻结守卫**（阶段 1 任务 1.8-B）。

本轮把六个窗口类里的逻辑搬进 ``services/``，硬要求是"方法名集合与签名逐字不变
（双向断言：不许少、不许多）"。人眼核对 148 个方法不可靠，所以：

1. ``BASELINE_SURFACE`` 是从**改造前的字节快照**上跑 AST 生成的（``poc/_baseline_1_8b_*.py``，
   与"动手前原样复制"的文件逐字节相同），由
   ``python poc/_verify_1_8b_browse.py --surface`` 可复现；
2. 下面用 AST 解析**当前**文件，与冻结表做双向比对；
3. 再在**实时类对象**上用 ``inspect`` 复核一遍（防止"文件里有、类上没有"这种
   AST 看不出来的情况，例如被同名类属性遮蔽）。

另外钉住三处必须保留的既有修复（D-91 / D-103 / D-93）与四个新服务的独立性。
"""

from __future__ import annotations

import ast
import importlib
import inspect
import io
from pathlib import Path
from typing import Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 本轮改动的六个文件 → 它们的改造前字节快照
BASELINE_FILES: Dict[str, str] = {
    "ui/windows/mod_browser.py": "poc/_baseline_1_8b_mod_browser.py",
    "ui/windows/modpack_browser.py": "poc/_baseline_1_8b_modpack_browser.py",
    "ui/windows/plugin_browser.py": "poc/_baseline_1_8b_plugin_browser.py",
    "ui/windows/server_mod_browser.py": "poc/_baseline_1_8b_server_mod_browser.py",
    "ui/windows/modpack_install.py": "poc/_baseline_1_8b_modpack_install.py",
    "ui/windows/modpack_server.py": "poc/_baseline_1_8b_modpack_server.py",
}

#: 四个新服务模块（服务可脱离 AppContext 独立实例化）
SERVICE_MODULES: List[str] = [
    "services.browse_common",
    "services.mod_browser_service",
    "services.modpack_service",
    "services.plugin_browser_service",
]

# ─── 冻结表面（由 poc/_verify_1_8b_browse.py --surface 生成）───────
#
# 数据来源：``poc/_baseline_1_8b_*.py`` —— 它们是**改造前工作区的字节快照**
# （与动手前原样复制的文件逐字节相同，见 poc/_rebuild_baseline_1_8b.py 的自校验）。
# 重新生成::
#
#     python poc/_verify_1_8b_browse.py --surface > poc/_surface_1_8b.txt
#     python poc/_inject_surface_1_8b.py
BASELINE_SURFACE: Dict[str, Dict[str, Dict[str, str]]] = {
    "ui/windows/mod_browser.py": {
        "ModBrowserWindow": {
            "_search_loader": '@property (self) -> Optional[str]',
            "__init__": '(self, parent, version_id: Optional[str]=None, callbacks: Optional[Dict[str, Callable]]=None)',
            "_build_ui": '(self)',
            "_build_tab_content": '(self, tab_frame, tab_key: str)',
            "_on_tab_changed": '(self, value=None)',
            "_switch_to_tab": '(self, tab_key: str)',
            "_on_tab_search": '(self, tab_key: str)',
            "_begin_request": '(self, tab_key: str) -> int',
            "_is_stale": '(self, tab_key: str, seq: Optional[int]) -> bool',
            "_do_tab_search": '(self, tab_key: str, seq: Optional[int]=None)',
            "_publish_tab_results": '(self, tab_key: str, all_hits: List[Dict], total_hits: int, sources: Dict, seq: Optional[int]=None)',
            "_publish_ai_results": '(self, tab_key: str, all_hits: List[Dict], keywords: List[str], query: str, seq: Optional[int]=None)',
            "_on_ai_search": '(self, tab_key: str)',
            "_do_ai_search": '(self, tab_key: str, query: str, token: str, seq: Optional[int]=None)',
            "_restore_ai_button_if_current": '(self, tab_key: str, seq: Optional[int]=None)',
            "_restore_ai_button": '(self, tab_key: str)',
            "_mirror_version": '(self, tab_key: str, display_value: Optional[str]) -> None',
            "_mirror_loader": '(self, tab_key: str, display_value: Optional[str]) -> None',
            "_get_selected_version": '(self, tab_key: str) -> Optional[str]',
            "_get_selected_loader": '(self, tab_key: str) -> Optional[str]',
            "_load_version_options": '(self)',
            "_render_tab_results": '(self, tab_key: str, hits: List[Dict], seq: Optional[int]=None)',
            "_render_tab_error": '(self, tab_key: str, error_msg: str, seq: Optional[int]=None)',
            "_create_item": '(self, tab_key: str, item: Dict)',
            "_browsable_hits": '(self, state: Dict) -> int',
            "_update_tab_pagination": '(self, tab_key: str)',
            "_on_tab_prev_page": '(self, tab_key: str)',
            "_on_tab_next_page": '(self, tab_key: str)',
            "_render_page_from_cache": '(self, tab_key: str)',
            "_render_page_from_ai_cache": '(self, tab_key: str)',
            "_ask_install_dir": '(self, default_dir: str, title: str) -> Optional[str]',
            "_on_install_mod": "(self, project_id: str, title: str, source: str='modrinth')",
            "_install_mod": "(self, project_id: str, title: str, source: str='modrinth', mods_dir: Optional[str]=None)",
            "_on_install_resource_pack": '(self, project_id: str, title: str)',
            "_install_resource_pack": '(self, project_id: str, title: str, rp_dir: Optional[str]=None)',
            "_on_install_shader": '(self, project_id: str, title: str)',
            "_install_shader": '(self, project_id: str, title: str, shader_dir: Optional[str]=None)',
            "_get_mods_dir": '(self) -> str',
            "_get_resourcepacks_dir": '(self) -> str',
            "_get_shaderpacks_dir": '(self) -> str',
            "_resolve_install_dir": '(self, tab_key: str) -> str',
            "_match_installed_instance": '(self, game_version: Optional[str], mod_loader: Optional[str]) -> Optional[str]',
            "_format_downloads": '@staticmethod (count: int) -> str',
            "_set_tab_status": '(self, tab_key: str, text: str)',
            "_run_in_thread": '(self, target, *args, **kwargs)',
        },
    },
    "ui/windows/modpack_browser.py": {
        "ModpackBrowserWindow": {
            "__init__": '(self, parent, on_modpack_selected: Callable[[str], None])',
            "_build_ui": '(self)',
            "_on_search": '(self)',
            "_do_search": '(self)',
            "_on_ai_search": '(self)',
            "_do_ai_search": '(self, query: str, token: str)',
            "_restore_ai_button": '(self)',
            "_render_results": '(self, hits: List[Dict])',
            "_render_error": '(self, error_msg: str)',
            "_create_modpack_item": '(self, modpack: Dict)',
            "_format_downloads": '@staticmethod (count: int) -> str',
            "_update_pagination": '(self)',
            "_on_prev_page": '(self)',
            "_on_next_page": '(self)',
            "_render_page_from_ai_cache": '(self)',
            "_on_install_modpack": '(self, project_id: str, title: str)',
            "_fetch_versions_and_pick": '(self, project_id: str, title: str)',
            "_build_sorted_version_list": '(self, versions: List[Dict]) -> List[Dict]',
            "_show_version_picker": '(self, project_id: str, title: str, all_sorted: List[Dict], total_count: int)',
            "_render_version_batch": '(self, picker, project_id: str, title: str, state: dict)',
            "_on_version_selected": '(self, project_id: str, title: str, version_data: Dict, picker)',
            "_download_version": '(self, project_id: str, title: str, version_data: Dict)',
            "_set_status": '(self, text: str)',
            "_run_in_thread": '(self, target, *args, **kwargs)',
        },
    },
    "ui/windows/plugin_browser.py": {
        "PluginBrowserWindow": {
            "__init__": '(self, parent, plugin_manager, market)',
            "_on_focus_in": '(self)',
            "_center_on_parent": '(self, parent)',
            "_build_ui": '(self)',
            "_async_load_index": '(self, force: bool=False)',
            "_on_index_loaded": '(self, plugins: List[dict], error: str)',
            "_check_updates_async": '(self)',
            "_update_status_summary": '(self)',
            "_update_tag_bar": '(self)',
            "_on_tag_click": '(self, tag: Optional[str])',
            "_update_tag_bar_colors": '(self)',
            "_on_search": '(self)',
            "_apply_filter": '(self)',
            "_render_page": '(self)',
            "_create_plugin_card": '(self, plugin: dict)',
            "_install_plugin": '(self, plugin_info: dict)',
            "_on_install_success": '(self, pid: str, name: str)',
            "_on_install_failed": '(self, pid: str, error: str)',
            "_update_plugin": '(self, plugin_info: dict)',
            "_on_update_success": '(self, pid: str, name: str)',
            "_on_update_failed": '(self, pid: str, error: str)',
            "_enable_plugin": '(self, pid: str)',
            "_on_tag_single_click": '(self, tag: str)',
            "_change_page": '(self, delta: int)',
            "_set_status": "(self, text: str, level: str='info')",
        },
    },
    "ui/windows/server_mod_browser.py": {
        "ServerModBrowserWindow": {
            "_search_loader": '@property (self) -> Optional[str]',
            "__init__": '(self, parent, version_id: str, callbacks: Dict[str, Callable])',
            "_build_ui": '(self)',
            "_do_initial_search": '(self)',
            "_on_search": '(self)',
            "_on_ai_search": '(self)',
            "_do_ai_search": '(self, query: str, token: str)',
            "_do_search": '(self)',
            "_render_results": '(self, hits: List[Dict])',
            "_update_pagination": '(self)',
            "_on_prev_page": '(self)',
            "_on_next_page": '(self)',
            "_on_install_mod": '(self, project_id: str, title: str)',
            "_install_mod": '(self, project_id: str, title: str)',
            "_get_mods_dir": '(self) -> str',
            "_format_downloads": '@staticmethod (count: int) -> str',
            "_set_status": '(self, text: str)',
            "_run_in_thread": '(self, target, *args, **kwargs)',
        },
    },
    "ui/windows/modpack_install.py": {
        "ModpackInstallWindow": {
            "__init__": '(self, parent, callbacks: Dict[str, Callable])',
            "_build_ui": '(self)',
            "_select_file": '(self)',
            "_open_modrinth_browser": '(self)',
            "_on_modrinth_downloaded": '(self, mrpack_path: str)',
            "_load_mrpack_info": '(self)',
            "_load_info_mrpack": '(self, path: str) -> Dict[str, Any]',
            "_load_info_multimc": '(self, path: str) -> Dict[str, Any]',
            "_load_info_curseforge": '(self, path: str) -> Dict[str, Any]',
            "_load_info_hmcl": '(self, path: str) -> Dict[str, Any]',
            "_load_info_mcbbs": '(self, path: str) -> Dict[str, Any]',
            "_load_info_compress": '(self, path: str) -> Dict[str, Any]',
            "_clear_optional_frame": '(self)',
            "_show_mrpack_info": '(self, info: Dict[str, Any])',
            "_show_mmc_components": '(self, info: Dict)',
            "_show_mrpack_optional_files": '(self, info: Dict)',
            "_show_cf_files": '(self, info: Dict)',
            "_show_generic_components": '(self, info: Dict)',
            "_show_error": '(self, msg: str)',
            "_on_install": '(self)',
            "_do_install": '(self, optional_files: list)',
            "_on_install_done": '(self, success: bool, result: str)',
            "_run_in_thread": '(self, func, *args)',
        },
    },
    "ui/windows/modpack_server.py": {
        "ModpackServerWindow": {
            "__init__": '(self, parent, callbacks: Dict[str, Callable])',
            "_build_ui": '(self)',
            "_select_file": '(self)',
            "_open_modrinth_browser": '(self)',
            "_on_modrinth_downloaded": '(self, mrpack_path: str)',
            "_load_mrpack_info": '(self)',
            "_show_mrpack_info": '(self, info: Dict[str, Any])',
            "_show_error": '(self, msg: str)',
            "_on_install": '(self)',
            "_do_install": '(self, optional_files: list, server_name: Optional[str])',
            "_on_install_done": '(self, success: bool, result: str)',
            "_refresh_parent_server_list": '(self)',
            "_run_in_thread": '(self, func, *args)',
        },
    },
}



# ─── AST 工具（与 poc/_verify_1_8b_browse.py 同一套算法）───────────


def _sig_of(node: ast.FunctionDef) -> str:
    decorators = [ast.unparse(d) for d in node.decorator_list]
    args = ast.unparse(node.args)
    returns = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    prefix = f"{'@' + ','.join(decorators) + ' ' if decorators else ''}"
    return f"{prefix}({args}){returns}"


def _classes(tree: ast.Module) -> Dict[str, Dict[str, str]]:
    out: Dict[str, Dict[str, str]] = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out[node.name] = {
                m.name: _sig_of(m) for m in node.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return out


def _module_functions(tree: ast.Module) -> Dict[str, str]:
    return {
        n.name: _sig_of(n) for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _parse(rel: str) -> ast.Module:
    return ast.parse(io.open(REPO_ROOT / rel, encoding="utf-8").read())


def _body_without_docstring(node: ast.FunctionDef) -> str:
    """方法的源码文本，**去掉 docstring**（注释里提到 ``ctk.StringVar`` 是说明，不是使用）。"""
    clone = ast.parse(ast.unparse(node)).body[0]
    assert isinstance(clone, ast.FunctionDef)
    first = clone.body[0] if clone.body else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        clone.body = clone.body[1:]
    return ast.unparse(clone)


def _module_name(rel: str) -> str:
    return rel.replace("/", ".")[:-3]


def _param_names(body: str) -> List[str]:
    """从 ``self, tab_key: str, seq: Optional[int]=None, *args`` 里取出参数名列表。

    括号深度为 0 的逗号才是参数分隔符（``Optional[Dict[str, Callable]]`` 里的逗号不是）。
    """
    parts: List[str] = []
    token = ""
    depth = 0
    for ch in body:
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(token)
            token = ""
        else:
            token += ch
    parts.append(token)

    names: List[str] = []
    for part in parts:
        name = part.split(":")[0].split("=")[0].strip().lstrip("*").strip()
        if name:
            names.append(name)
    return names


# ─── 1. 冻结表面：双向比对 ──────────────────────────────────────


class TestWindowSurfaceIsFrozen:
    @pytest.mark.parametrize("rel", sorted(BASELINE_FILES))
    def test_method_sets_and_signatures_are_identical(self, rel: str) -> None:
        """不许少、不许多、签名不许变。"""
        expected = BASELINE_SURFACE[rel]
        current = _classes(_parse(rel))
        assert sorted(current) == sorted(expected), (
            f"{rel} 的类集合变了：{sorted(expected)} -> {sorted(current)}"
        )
        for cls_name, methods in expected.items():
            got = current[cls_name]
            assert sorted(got) == sorted(methods), (
                f"{rel}::{cls_name} 方法面变了："
                f"缺失 {sorted(set(methods) - set(got))}，新增 {sorted(set(got) - set(methods))}"
            )
            for name, sig in methods.items():
                assert got[name] == sig, f"{rel}::{cls_name}.{name} 签名变了：{sig} -> {got[name]}"

    @pytest.mark.parametrize("rel", sorted(BASELINE_FILES))
    def test_module_level_functions_are_kept(self, rel: str) -> None:
        """模块级函数不许丢、签名不许变（新增的 ``_get_*_service`` 是允许的）。"""
        baseline = _module_functions(_parse(BASELINE_FILES[rel]))
        current = _module_functions(_parse(rel))
        missing = sorted(set(baseline) - set(current))
        assert missing == [], f"{rel} 丢了模块级函数：{missing}"
        for name, sig in baseline.items():
            assert current[name] == sig, f"{rel}::{name} 签名变了：{sig} -> {current[name]}"
        added = sorted(set(current) - set(baseline))
        assert added == [name for name in added if name.startswith("_get_")], (
            f"{rel} 新增了非取服务用法的模块级函数：{added}"
        )

    @pytest.mark.parametrize("rel", sorted(BASELINE_FILES))
    def test_live_class_matches_the_frozen_surface(self, rel: str) -> None:
        """实时类对象复核：文件里有的方法必须真的挂在类上。"""
        mod = importlib.import_module(_module_name(rel))
        for cls_name, methods in BASELINE_SURFACE[rel].items():
            cls = getattr(mod, cls_name)
            for name in methods:
                assert hasattr(cls, name), f"{mod.__name__}.{cls_name}.{name} 在实时类上不存在"
                assert name in cls.__dict__, f"{cls_name}.{name} 不是在本类上定义的（被别处覆盖了？）"

    @pytest.mark.parametrize("rel", sorted(BASELINE_FILES))
    def test_live_signatures_match(self, rel: str) -> None:
        """``inspect`` 复核：实时类上的**参数表**与冻结表一致。

        注解文本由上面那条 AST 比对**逐字**钉住；这里比参数名/顺序，
        因为 ``inspect`` 与 ``ast.unparse`` 对默认值字符串的引号风格不同
        （``'info'`` vs ``"info"``），逐字比会假失败。
        """
        mod = importlib.import_module(_module_name(rel))
        for cls_name, methods in BASELINE_SURFACE[rel].items():
            cls = getattr(mod, cls_name)
            for name, frozen in methods.items():
                obj = inspect.getattr_static(cls, name)
                if isinstance(obj, property):
                    assert frozen.startswith("@property "), f"{cls_name}.{name} 不再是 property"
                    continue
                if isinstance(obj, staticmethod):
                    assert frozen.startswith("@staticmethod "), f"{cls_name}.{name} 不再是 staticmethod"
                    target = obj.__func__
                else:
                    assert not frozen.startswith("@static"), f"{cls_name}.{name} 变成了 staticmethod"
                    target = obj
                body = frozen.split(" ", 1)[1] if frozen.startswith("@") else frozen
                body = body.split(" -> ")[0].strip()
                expected = _param_names(body.strip("()"))
                live = list(inspect.signature(target).parameters)
                assert live == expected, f"{cls_name}.{name} 参数表变了：{live} != {expected}"


# ─── 2. 三处既有修复的守卫 ──────────────────────────────────────


class TestPreservedDefectFixes:
    """D-91 / D-103 / D-93 不许因为本轮搬家而回退。"""

    @staticmethod
    def _method(rel: str, cls_name: str, method: str) -> ast.FunctionDef:
        tree = _parse(rel)
        for cls in tree.body:
            if isinstance(cls, ast.ClassDef) and cls.name == cls_name:
                for node in cls.body:
                    if isinstance(node, ast.FunctionDef) and node.name == method:
                        return node
        raise AssertionError(f"找不到 {rel}::{cls_name}.{method}")

    def test_d91_getters_never_read_the_option_menu_variable(self) -> None:
        """D-91：两个 getter 只读纯值镜像，界面里不得出现下拉框/变量取值。"""
        src = io.open(REPO_ROOT / "ui" / "windows" / "mod_browser.py", encoding="utf-8").read()
        assert "ctk.CTkOptionMenu" in src, "下拉控件本身必须还在"
        for bad in ("version_var.get()", "loader_var.get()", "version_menu.get()", "loader_menu.get()"):
            assert bad not in src, f"D-91 回退：界面里出现了 {bad}"
        for name in ("_get_selected_version", "_get_selected_loader"):
            body = _body_without_docstring(self._method("ui/windows/mod_browser.py", "ModBrowserWindow", name))
            assert "StringVar" not in body and "version_var" not in body and "loader_var" not in body, (
                f"D-91 回退：{name} 又开始读 Tk 变量了"
            )
            assert "selected_" in body, f"D-91 回退：{name} 不再读纯值镜像"

    def test_d91_mirrors_are_still_written_on_every_change_point(self) -> None:
        src = io.open(REPO_ROOT / "ui" / "windows" / "mod_browser.py", encoding="utf-8").read()
        assert "self._mirror_version(tab_key, default_version)" in src
        assert "command=lambda value, tk=tab_key: self._mirror_version(tk, value)" in src
        assert "self._mirror_loader(tab_key, default_loader_display)" in src
        assert "command=lambda value, tk=tab_key: self._mirror_loader(tk, value)" in src
        assert "self._mirror_version(tab_key, all_display)" in src  # 版本列表异步填充后也要同步镜像

    def test_d103_pagination_still_uses_local_cache_count(self) -> None:
        """D-103：分页与"显示区间"仍走 ``_browsable_hits``，且判定口径来自服务层。"""
        src = io.open(REPO_ROOT / "ui" / "windows" / "mod_browser.py", encoding="utf-8").read()
        assert "available = self._browsable_hits(state)" in src
        assert "self._browsable_hits(state)" in ast.unparse(
            self._method("ui/windows/mod_browser.py", "ModBrowserWindow", "_on_tab_next_page")
        )
        browsable = ast.unparse(self._method("ui/windows/mod_browser.py", "ModBrowserWindow", "_browsable_hits"))
        assert "browsable_count" in browsable, "D-103 的判定体必须仍在（哪怕委托到服务层）"

        from services.browse_common import browsable_count

        assert browsable_count(None, list(range(300)), 1500) == 300

    def test_d93_generation_guard_surface_is_untouched(self) -> None:
        """D-93：六件套还在，且 ``_do_tab_search`` / ``_do_ai_search`` 仍带 ``seq`` 默认参数。"""
        for name in ("_begin_request", "_is_stale", "_do_tab_search", "_do_ai_search", "_publish_tab_results", "_publish_ai_results"):
            assert name in BASELINE_SURFACE["ui/windows/mod_browser.py"]["ModBrowserWindow"]
            self._method("ui/windows/mod_browser.py", "ModBrowserWindow", name)  # 存在性
        search = self._method("ui/windows/mod_browser.py", "ModBrowserWindow", "_do_tab_search")
        args = [a.arg for a in search.args.args]
        assert "seq" in args, "D-93 回退：_do_tab_search 不再接收世代号"

    def test_d93_worker_still_does_not_write_shared_state(self) -> None:
        """D-93：``_do_tab_search`` 里不得出现对缓存/总数/来源的直接赋值。"""
        node = self._method("ui/windows/mod_browser.py", "ModBrowserWindow", "_do_tab_search")
        forbidden = {"_cached_hits", "total_hits", "_sources", "_ai_cached_hits"}
        written = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Assign):
                for tgt in sub.targets:
                    if isinstance(tgt, ast.Subscript) and isinstance(tgt.slice, ast.Constant):
                        if tgt.slice.value in forbidden:
                            written.add(str(tgt.slice.value))
        assert written == set(), f"_do_tab_search 又在直接写共享状态：{sorted(written)}"

    def test_d93_unknown_tab_is_ignored_before_reading_filters(self) -> None:
        """原文对未知标签页是 ``else: return``：连筛选值都不读。

        改写后必须保持这个**求值顺序**：否则未知 ``tab_key`` 会先在
        ``_get_selected_version`` 上抛 ``KeyError``，被同一个 ``except``
        兜成"搜索失败"错误态 —— 可见行为就变了。
        """
        body = ast.unparse(self._method("ui/windows/mod_browser.py", "ModBrowserWindow", "_do_tab_search"))
        assert "CLIENT_TABS" in body, "未知标签页的前置判断没了"
        assert body.index("CLIENT_TABS") < body.index("_get_selected_version"), (
            "前置判断必须在读筛选值之前"
        )

    def test_d95_cross_object_writes_still_absent(self) -> None:
        """D-95：两个安装窗口既不写 ``on_progress``，也仍然轮询 ``_mp_progress``。"""
        for rel in ("ui/windows/modpack_install.py", "ui/windows/modpack_server.py"):
            src = io.open(REPO_ROOT / rel, encoding="utf-8").read()
            assert 'hasattr(launcher_inst, "_mp_progress")' in src, f"{rel} 的进度轮询写法变了"
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for tgt in targets:
                        assert not (isinstance(tgt, ast.Attribute) and tgt.attr == "on_progress"), rel


# ─── 3. 服务的独立性 ────────────────────────────────────────────


class TestServicesAreIndependent:
    def test_every_service_instantiates_without_app_context(self) -> None:
        found = 0
        for mod_name in SERVICE_MODULES:
            mod = importlib.import_module(mod_name)
            for obj in vars(mod).values():
                if isinstance(obj, type) and getattr(obj, "name", "") and hasattr(obj, "attached"):
                    inst = obj()
                    assert inst.attached is False, f"{mod_name}.{obj.__name__} 构造期碰了 AppContext"
                    assert inst.started is False
                    found += 1
        assert found == 3, f"应当有 3 个服务类（browse_common 是纯函数模块），实际 {found}"

    def test_services_have_no_ui_imports(self) -> None:
        """AST 层面的零 UI 依赖（``scripts/check_services_purity.py`` 之外的第二道）。"""
        forbidden = ("tkinter", "customtkinter", "PySide6", "shiboken6", "ui")
        for mod_name in SERVICE_MODULES:
            rel = mod_name.replace(".", "/") + ".py"
            tree = _parse(rel)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom):
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    assert top not in forbidden, f"{rel} import 了 {name}"

    def test_window_modules_import_the_services(self) -> None:
        """每个窗口都用**惰性取用**的方式拿服务（与 1.8-A 的 ``_get_resource_service`` 同形）。"""
        expected = {
            "ui/windows/mod_browser.py": "_mod_browser_service",
            "ui/windows/server_mod_browser.py": "_mod_browser_service",
            "ui/windows/modpack_browser.py": "_modpack_service",
            "ui/windows/modpack_install.py": "_modpack_service",
            "ui/windows/modpack_server.py": "_modpack_service",
            "ui/windows/plugin_browser.py": "_plugin_browser_service",
        }
        #: 缓存属性的名字与函数名去掉 ``_get`` 后的部分相同（``_get_mod_browser_service``
        #: ↔ ``_mod_browser_service_fallback``）
        for rel, attr in expected.items():
            src = io.open(REPO_ROOT / rel, encoding="utf-8").read()
            assert f"def _get{attr}(owner: Any = None)" in src, rel
            assert f"owner._{attr.lstrip('_')}_fallback = service" in src, rel
