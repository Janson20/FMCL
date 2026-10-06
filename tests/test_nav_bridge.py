"""`NavBridge`（阶段 2 任务 2.11）测试：路由表 / 推入 / 返回 / 面包屑 / 深链 / 插件路由 / 栈深上限。

三条纪律（与 `tests/test_theme_bridge.py`、`tests/test_tr_bridge.py` 一致）：

- ``QT_QPA_PLATFORM=offscreen`` 在 **import PySide6 之前**设置；
- **自己建 `QGuiApplication`**（一个进程只能有一个：先看 `instance()`）；
- 不引第三方测试依赖（没有 ``pytest-qt``）。

本文件只测**桥本身**（纯 QObject，不需要 QML）。真引擎加载 `App.qml` 的走查在
`tests/test_shell_qml.py`。每条断言都能变红：非法输入那几条（未知路由 / 深链格式错 /
插件路由非法）都要求 `navFailed` 真的发出来，不是"返回了个 False 就算过"。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
from PySide6.QtGui import QGuiApplication

from app.bridges import nav_bridge as nb
from app.bridges.nav_bridge import NavBridge


def _qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


_APP = _qapp()

#: 02 §3.1 骨架图的一级导航顺序（12 项，顺序也冻结在 `_NAV_ORDER` 里）。
NAV_ORDER: Tuple[str, ...] = (
    "home", "versions", "resources", "servers", "online", "backups",
    "music", "tools", "achievements", "agent", "bedrock", "settings",
)


def grouped_nav_order() -> List[str]:
    """按 `NAV_GROUPS` 分组后的领域顺序（**显示层**的顺序，供别的用例引用）。

    期望值从桥自己的分组表算出来（不是在测试里再抄一份），所以"把某个领域挪组"
    只会让实现与期望同时变，不会留下一条过期的断言。
    """
    order: List[str] = []
    for _group, _title_key, members in nb.NAV_GROUPS:
        order.extend(domain for domain in NAV_ORDER if domain in members)
    return order


class _Spy:
    """信号记录器：把 `(信号名, 参数)` 顺序记下来，供"到底发没发"的断言用。"""

    def __init__(self, *pairs: Tuple[str, Any]) -> None:
        self.calls: List[Tuple[str, Tuple[Any, ...]]] = []
        for name, signal in pairs:
            signal.connect(lambda *args, _n=name: self.calls.append((_n, args)))

    def names(self) -> List[str]:
        return [name for name, _ in self.calls]

    def args_of(self, name: str) -> List[Tuple[Any, ...]]:
        return [args for call, args in self.calls if call == name]

    def clear(self) -> None:
        self.calls.clear()


@pytest.fixture()
def nav() -> NavBridge:
    return NavBridge()


@pytest.fixture()
def spy(nav: NavBridge) -> _Spy:
    return _Spy(
        ("route", nav.routeChanged),
        ("breadcrumb", nav.breadcrumbChanged),
        ("failed", nav.navFailed),
        ("plugin", nav.pluginRouteAdded),
    )


# ─── 1. 路由表 ──────────────────────────────────────────────────


def test_builtin_table_has_the_12_domains_as_roots() -> None:
    routes = nb.build_default_routes()
    roots = [r.id for r in routes if not r.parent]
    assert roots == list(NAV_ORDER), "一级领域必须是 02 §3.1 的那 12 项，顺序也一样"


def test_route_ids_are_unique_and_parents_exist() -> None:
    routes = {r.id: r for r in nb.build_default_routes()}
    assert len(routes) == len(nb.build_default_routes()), "路由 id 必须唯一"
    for route in routes.values():
        if route.parent:
            assert route.parent in routes, f"{route.id} 的父路由 {route.parent} 不存在"
            assert route.level == routes[route.parent].level + 1, f"{route.id} 的层级与父级不连续"


def test_every_route_has_title_key_icon_and_qml_file() -> None:
    for route in nb.build_default_routes():
        assert route.title_key, f"{route.id} 缺 title_key"
        assert route.icon, f"{route.id} 缺 icon"
        assert route.qml_file.startswith("pages/"), f"{route.id} 的 qml_file 必须是 pages/ 下的相对路径"


def test_every_route_qml_file_exists() -> None:
    root = nb.qml_root()
    missing = [r.id for r in nb.build_default_routes() if not (root / r.qml_file).is_file()]
    assert missing == [], f"这些路由指向的 QML 文件不存在：{missing}"


def test_every_route_icon_exists_in_the_icon_set() -> None:
    icons = nb.qml_root() / "assets" / "icons"
    missing = [r.id for r in nb.build_default_routes() if not (icons / f"{r.icon}.svg").is_file()]
    assert missing == [], f"这些路由用的图标文件不存在：{missing}"


def test_nav_items_are_the_12_domains_in_order(nav: NavBridge) -> None:
    """一级导航项：**12 个领域一个不少、顺序与骨架图一致**（返工 B 组之后多两个分组字段）。

    分组只是**多带的元数据**，不改变 `nav_items()` 的顺序 —— 顺序由 `_NAV_ORDER` 冻结
    （对应 02 骨架图），"屏幕上按组排"是显示层的事（`shell/Navigation.qml` 的
    `buildRows()`，由 `tests/test_shell_qml.py` 断言屏幕顺序）。
    """
    items = nav.nav_items()
    assert [item["id"] for item in items] == list(NAV_ORDER)
    for item in items:
        assert set(item) == {"id", "title_key", "icon", "source", "group", "group_title_key"}
        assert item["source"] == "builtin"
        assert item["group_title_key"].startswith("nav_group_"), item
    # 每个领域都必须落在某个声明过的组里（漏归组的会被兜底到 plugin 组，这里不允许）
    declared = {domain for _g, _t, members in nb.NAV_GROUPS for domain in members}
    assert {item["id"] for item in items} <= declared


def test_routes_and_breadcrumb_carry_usable_qml_urls(nav: NavBridge) -> None:
    nav.push("versions")
    by_id = {item["id"]: item for item in nav.routes()}
    assert by_id["versions"]["qml_url"].startswith("file:///")
    assert by_id["versions"]["qml_url"].endswith("/qml/pages/versions/VersionsPage.qml")
    assert nav.breadcrumb[0]["qml_url"] == by_id["versions"]["qml_url"]


# ─── 2. 推入 / 返回 / 首页 / 重置 ───────────────────────────────


def test_push_sets_current_route_and_announces(nav: NavBridge, spy: _Spy) -> None:
    assert nav.currentRoute == ""
    assert nav.push("versions") is True
    assert nav.currentRoute == "versions"
    assert nav.depth == 1
    assert nav.canGoBack is False
    assert spy.names() == ["route", "breadcrumb"]
    assert spy.args_of("route")[0] == ("versions",)


def test_push_unknown_route_fails_loudly(nav: NavBridge, spy: _Spy) -> None:
    nav.push("home")
    spy.clear()
    assert nav.push("versions/nope") is False
    assert nav.currentRoute == "home", "失败不该改变当前路由"
    assert nav.depth == 1
    assert spy.names() == ["failed"], "未知路由必须发 navFailed（不许静默）"
    assert "versions/nope" in spy.args_of("failed")[0][0]


def test_push_same_route_same_params_is_a_noop(nav: NavBridge, spy: _Spy) -> None:
    nav.push("versions")
    spy.clear()
    assert nav.push("versions") is True
    assert nav.depth == 1, "点两下同一个导航项不该出现两页"
    assert spy.calls == [], "完全没变化就一个信号都不该发"


def test_push_same_route_different_params_pushes_again(nav: NavBridge) -> None:
    nav.push("versions/detail", {"version": "1.21.4"})
    nav.push("versions/detail", {"version": "1.20.1"})
    assert nav.depth == 2, "参数不同是两次导航（连着看两个版本，返回要能回到上一个）"
    assert nav.currentParams == {"version": "1.20.1"}
    assert nav.goBack() is True
    assert nav.currentParams == {"version": "1.21.4"}


def test_go_back_pops_one_frame_and_stops_at_root(nav: NavBridge, spy: _Spy) -> None:
    nav.push("versions")
    nav.push("versions/detail")
    spy.clear()
    assert nav.goBack() is True
    assert nav.currentRoute == "versions"
    assert spy.names() == ["route", "breadcrumb"]
    spy.clear()
    assert nav.goBack() is False, "已经在栈底：这是无事可做，不是失败"
    assert nav.currentRoute == "versions"
    assert nav.canGoBack is False
    assert spy.calls == [], "栈底返回不该报 navFailed"


def test_go_back_to_steps_clamps_to_stack(nav: NavBridge) -> None:
    for route in ("versions", "versions/detail", "versions/detail/mods", "versions/detail/launch"):
        nav.push(route)
    assert nav.goBackTo(2) is True
    assert nav.currentRoute == "versions/detail"
    assert nav.goBackTo(0) is False
    assert nav.goBackTo(99) is True, "越界要夹到合法范围，不是报错"
    assert nav.currentRoute == "versions"
    assert nav.depth == 1


def test_go_home_pushes_and_keeps_the_back_path(nav: NavBridge) -> None:
    nav.push("versions")
    nav.push("versions/detail")
    assert nav.goHome() is True
    assert nav.currentRoute == "home"
    assert nav.depth == 3, "goHome 是'去首页'，保留返回路径"
    assert nav.canGoBack is True


def test_reset_returns_to_the_initial_state(nav: NavBridge) -> None:
    nav.push("versions")
    nav.push("versions/detail")
    assert nav.reset() is True
    assert nav.currentRoute == "home"
    assert nav.depth == 1
    assert nav.canGoBack is False, "reset 是推倒重来，退不回任何页面"
    assert nav.reset() is False, "已经在初始态就是无事可做"


def test_depth_limit_drops_the_oldest_frame(nav: NavBridge) -> None:
    for index in range(nb.MAX_DEPTH + 5):
        assert nav.push("versions/detail", {"version": str(index)}) is True
    assert nav.depth == nb.MAX_DEPTH, "栈深必须有上限"
    assert nav.currentRoute == "versions/detail"
    assert nav.currentParams == {"version": str(nb.MAX_DEPTH + 4)}, "最新的那一页必须还在"
    assert nav.breadcrumb[0]["params"]["version"] == "5", "丢的是栈底（最老的 5 帧）"
    assert nav.canGoBack is True


def test_params_are_normalized_to_a_flat_map(nav: NavBridge) -> None:
    nav.push("versions/detail", {"version": "1.21.4", "count": 3, "flag": True, "drop": None})
    assert nav.currentParams == {"version": "1.21.4", "count": 3, "flag": True}
    nav.push("versions", "not-a-map")
    assert nav.currentParams == {}, "参数不是键值表时忽略，不该炸"


# ─── 3. 面包屑 ──────────────────────────────────────────────────


def test_breadcrumb_maps_the_stack_with_back_count(nav: NavBridge) -> None:
    nav.push("home")
    nav.push("versions")
    nav.push("versions/detail")
    crumbs = nav.breadcrumb
    assert [c["id"] for c in crumbs] == ["home", "versions", "versions/detail"]
    assert [c["back_count"] for c in crumbs] == [2, 1, 0], "back_count = 退回该项要点几次返回"
    assert [c["is_current"] for c in crumbs] == [False, False, True]
    assert crumbs[0]["title_key"] == "tab_game"


def test_breadcrumb_follows_params_and_titles(nav: NavBridge) -> None:
    nav.push("versions/detail", {"version": "1.21.4"})
    crumb = nav.breadcrumb[-1]
    assert crumb["params"] == {"version": "1.21.4"}
    assert crumb["title_key"] == nav.currentTitleKey
    assert crumb["description_key"] == nav.currentDescriptionKey


# ─── 4. 深链 ────────────────────────────────────────────────────


def test_deep_link_parses_host_and_path(nav: NavBridge) -> None:
    assert nav.openDeepLink("fmcl://versions/detail?version=1.21.4") is True
    assert nav.currentRoute == "versions/detail"
    assert nav.currentParams == {"version": "1.21.4"}


def test_deep_link_accepts_empty_host_and_trailing_slash(nav: NavBridge) -> None:
    assert nav.openDeepLink("fmcl:///versions/detail") is True
    assert nav.currentRoute == "versions/detail"
    assert nav.openDeepLink("fmcl://versions/") is True
    assert nav.currentRoute == "versions"


def test_deep_link_decodes_query_values(nav: NavBridge) -> None:
    assert nav.openDeepLink("fmcl://versions/detail?version=1.21.4&name=%E4%B8%AD%E6%96%87") is True
    assert nav.currentParams["name"] == "中文", "查询串要按 URL 规则解码"


def test_deep_link_without_query_has_empty_params(nav: NavBridge) -> None:
    assert nav.openDeepLink("fmcl://online/create") is True
    assert nav.currentParams == {}


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/versions",
        "file:///D:/versions",
        "versions/detail",  # 裸路由 id 不是深链
        "",
        "   ",
        "fmcl://",
        "not-a-deep-link",
    ],
)
def test_bad_deep_links_are_rejected_loudly(nav: NavBridge, spy: _Spy, url: str) -> None:
    assert nav.openDeepLink(url) is False
    assert nav.depth == 0, "被拒的深链不该留下任何栈帧"
    assert spy.names() == ["failed"], f"{url!r} 必须发 navFailed（不许静默）"


def test_deep_link_to_unknown_route_is_rejected(nav: NavBridge, spy: _Spy) -> None:
    assert nav.openDeepLink("fmcl://versions/unknown") is False
    assert spy.names() == ["failed"]
    assert "versions/unknown" in spy.args_of("failed")[0][0]


def test_deep_link_path_case_is_strict(nav: NavBridge, spy: _Spy) -> None:
    # URL 的 host 天生大小写不敏感（QUrl 自己会归一），path 不是 —— 路由 id 全小写，
    # 所以只有 path 写错才会落到"未知路由"，这正是我们要的行为。
    assert nav.openDeepLink("fmcl://Versions/detail") is True
    spy.clear()
    assert nav.openDeepLink("fmcl://versions/Detail") is False
    assert spy.names() == ["failed"]


# ─── 5. 查询 ────────────────────────────────────────────────────


def test_resolve_returns_route_or_empty_map(nav: NavBridge, spy: _Spy) -> None:
    info = nav.resolve("versions/install")
    assert info["title_key"] == "install_new_version"
    assert info["parent"] == "versions"
    assert info["level"] == 1
    assert nav.resolve("nope") == {}
    assert spy.calls == [], "查询不是导航，未知 id 不该报 navFailed"


def test_routes_lists_everything_with_source(nav: NavBridge) -> None:
    assert len(nav.routes()) == nav.routeCount == len(nb.build_default_routes())
    assert {item["source"] for item in nav.routes()} == {"builtin"}


# ─── 6. 插件路由（02 §7 的 UI API v2） ──────────────────────────


def test_plugin_route_registers_and_lands_in_nav(nav: NavBridge, spy: _Spy) -> None:
    assert nav.registerPluginRoute("plugin/com.demo/main", "tab_agent", "file:///tmp/Main.qml", "com.demo") is True
    assert spy.names() == ["plugin"]
    assert spy.args_of("plugin")[0] == ("plugin/com.demo/main",)
    assert nav.resolve("plugin/com.demo/main")["source"] == "plugin"
    items = nav.nav_items()
    assert len(items) == 13, "插件页是一级导航项（02 §7：ui.page.register 返回一级导航项）"
    assert items[-1]["id"] == "plugin/com.demo/main"
    assert nav.push("plugin/com.demo/main") is True
    assert nav.currentQmlUrl == "file:///tmp/Main.qml", "插件页的 URL 由插件自己给，原样透出"


@pytest.mark.parametrize("route_id", ["main", "demo/main", "plugindemo", "plugin/"])
def test_plugin_route_requires_the_prefix(nav: NavBridge, spy: _Spy, route_id: str) -> None:
    assert nav.registerPluginRoute(route_id, "tab_agent", "file:///tmp/Main.qml", "demo") is False
    assert spy.names() == ["failed"]
    assert nb.PLUGIN_ROUTE_PREFIX in spy.args_of("failed")[0][0]


def test_plugin_route_rejects_legacy_ui_api(nav: NavBridge, spy: _Spy) -> None:
    # 旧插件（ui_api=1）那套 `get_tab_ui(parent)` 传的是 tkinter 容器，给不出 QML 组件。
    assert nav.registerPluginRoute("plugin/legacy/page", "tab_agent", "", "legacy") is False
    assert nav.registerPluginRoute("plugin/legacy/page", "tab_agent", "   ", "legacy") is False
    assert spy.names() == ["failed", "failed"]
    assert "不受支持" in spy.args_of("failed")[0][0]
    assert nav.resolve("plugin/legacy/page") == {}, "被降级的插件不该留下半条路由"


def test_plugin_route_cannot_shadow_builtins(nav: NavBridge, spy: _Spy) -> None:
    assert nav.registerPluginRoute("versions", "tab_agent", "file:///tmp/x.qml", "demo") is False
    assert spy.names() == ["failed"]


def test_plugin_route_cannot_be_taken_over(nav: NavBridge, spy: _Spy) -> None:
    assert nav.registerPluginRoute("plugin/demo/main", "tab_agent", "file:///tmp/a.qml", "demo") is True
    spy.clear()
    assert nav.registerPluginRoute("plugin/demo/main", "tab_agent", "file:///tmp/b.qml", "other") is False
    assert spy.names() == ["failed"]
    assert "已被占用" in spy.args_of("failed")[0][0]


def test_plugin_route_repeat_registration_is_idempotent(nav: NavBridge, spy: _Spy) -> None:
    assert nav.registerPluginRoute("plugin/demo/main", "tab_agent", "file:///tmp/a.qml", "demo") is True
    spy.clear()
    assert nav.registerPluginRoute("plugin/demo/main", "tab_agent", "file:///tmp/a.qml", "demo") is True
    assert spy.calls == [], "同一属主重复登记同一条：幂等成功，不重复发信号"
    assert len(nav.nav_items()) == 13


def test_plugin_route_falls_back_to_the_id_as_title_key(nav: NavBridge) -> None:
    assert nav.registerPluginRoute("plugin/demo/main", "", "file:///tmp/a.qml", "demo") is True
    assert nav.resolve("plugin/demo/main")["title_key"] == "plugin/demo/main"


# ─── 7. 仅 Windows 的领域（02 §4.11 基岩版） ─────────────────────


def test_bedrock_domain_is_marked_windows_only() -> None:
    routes = {r.id: r for r in nb.build_default_routes()}
    assert routes["bedrock"].platform == nb.WINDOWS_PLATFORM
    assert routes["bedrock/download"].platform == nb.WINDOWS_PLATFORM
    others = [r.id for r in routes.values() if r.platform and not r.id.startswith("bedrock")]
    assert others == [], f"只有基岩版领域是仅 Windows 的，这里多了：{others}"


def test_bedrock_is_hidden_and_rejected_off_windows(nav: NavBridge, spy: _Spy,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    """非 Windows：导航项里不出现（与旧界面一致），但深链/Agent 跳过来要**明确拒绝**。

    两条一起做才是完整的：只隐藏会留下"深链点出一页空白"，只拒绝会在 Linux 上
    摆一个永远点不开的导航项。
    """
    monkeypatch.setattr(nb, "host_platform", lambda: "linux")
    ids = [item["id"] for item in nav.nav_items()]
    assert "bedrock" not in ids and len(ids) == 11
    assert ids == [d for d in NAV_ORDER if d != "bedrock"]

    assert nav.resolve("bedrock")["available"] is False
    assert nav.resolve("versions")["available"] is True
    assert [r["id"] for r in nav.routes() if not r["available"]] == [
        "bedrock", "bedrock/detail", "bedrock/download",
    ]

    assert nav.push("bedrock") is False
    assert nav.openDeepLink("fmcl://bedrock/download") is False
    assert nav.depth == 0, "被拒绝的跳转不该留下栈帧"
    assert spy.names() == ["failed", "failed"]
    assert "windows" in spy.args_of("failed")[0][0] and "linux" in spy.args_of("failed")[0][0]


def test_bedrock_works_on_windows(nav: NavBridge, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nb, "host_platform", lambda: nb.WINDOWS_PLATFORM)
    assert [item["id"] for item in nav.nav_items()] == list(NAV_ORDER)
    assert nav.push("bedrock") is True
    assert nav.currentRoute == "bedrock"
    assert nav.resolve("bedrock")["available"] is True


# ─── 8. 装配接线（入口不许改，这条钉住"模块落地即被注册"） ────────


def test_main_qml_registers_nav_and_shell_by_name() -> None:
    import main_qml

    table = {name: (module, cls) for name, module, cls in main_qml.CONTEXT_BRIDGES}
    assert table["Nav"] == ("app.bridges.nav_bridge", "NavBridge")
    assert table["Shell"] == ("app.bridges.shell_bridge", "ShellBridge")

    import importlib

    for name, (module_name, class_name) in (("Nav", table["Nav"]), ("Shell", table["Shell"])):
        cls = getattr(importlib.import_module(module_name), class_name)
        assert cls.__name__ == class_name, f"{name} 的类名必须与冻结表一致"


def test_module_registry_lets_shell_find_nav() -> None:
    first = nav_bridge_instance = NavBridge()
    assert nb.current_nav() is nav_bridge_instance
    second = NavBridge()
    assert nb.current_nav() is second, "登记的是最近构造的那个（装配路径上只有一个）"
    assert first is not second


def test_qml_root_points_at_the_repo_qml_dir() -> None:
    root = nb.qml_root()
    assert (root / "App.qml").is_file()
    assert root.name == "qml"


def test_nav_bridge_has_no_business_rule_imports() -> None:
    """红线 2：桥上不许出现业务判断。这条用"源码里不 import services"钉住一个方向。"""
    source = Path(nb.__file__).read_text(encoding="utf-8")
    assert "from services" not in source and "import services" not in source
    assert "PySide6.QtWidgets" not in source


# ─── 离开守卫（阶段 3 任务 3.4；M-Q1 的 B6）─────────────────────
#
# 守卫是"有未保存改动时，跨域导航要先问一次"的机制。设置页是第一个用户
# （草稿范式），3.11 的服务器配置编辑器（对照表写明"未保存提示"）会用同一个机制。
# 这几条钉住四件事：**默认不拦**、**跨域才拦**、**域内不拦**、**裁决后只走一次**。


class TestLeaveGuard:
    @pytest.fixture()
    def guarded(self, nav: NavBridge) -> NavBridge:
        nav.push("home")
        nav.push("settings")
        return nav

    def test_no_guard_means_no_change_of_behaviour(self, nav: NavBridge) -> None:
        """没登记守卫时，导航行为与 3.3 之前**逐字一致**（这条是回归保险）。"""
        nav.push("home")
        nav.push("settings/java")
        assert nav.guardedDomains() == []
        assert nav.push("home") is True
        assert nav.currentRoute == "home"

    def test_in_domain_navigation_is_never_blocked(self, guarded: NavBridge) -> None:
        """B1：草稿是**整域**一份，在设置里换分区不该被问"要不要保存"。"""
        guarded.setLeaveGuard("settings", True)
        assert guarded.push("settings/java") is True
        assert guarded.push("settings/theme") is True
        assert guarded.currentRoute == "settings/theme"
        assert guarded.property("pendingLeaveRoute") == ""

    def test_cross_domain_navigation_is_blocked_once(self, guarded: NavBridge) -> None:
        events: List[Tuple[str, str]] = []
        guarded.leaveBlocked.connect(lambda domain, route: events.append((domain, route)))
        guarded.setLeaveGuard("settings", True)
        # 返回 False = 这次导航**没有**发生（页面没换）
        assert guarded.push("home") is False
        assert guarded.currentRoute == "settings"
        assert events == [("settings", "home")]
        assert guarded.property("pendingLeaveRoute") == "home"

    def test_second_request_does_not_stack_up(self, guarded: NavBridge) -> None:
        """已经在等裁决时再点别的导航项，不该把待裁决目标换掉（否则用户答的是另一个问题）。"""
        events: List[Tuple[str, str]] = []
        guarded.leaveBlocked.connect(lambda domain, route: events.append((domain, route)))
        guarded.setLeaveGuard("settings", True)
        guarded.push("home")
        guarded.push("versions")
        assert events == [("settings", "home")], "第二次请求应当被忽略"
        assert guarded.property("pendingLeaveRoute") == "home"

    def test_cancel_keeps_the_page_and_clears_the_request(self, guarded: NavBridge) -> None:
        cancelled: List[str] = []
        guarded.leaveCancelled.connect(cancelled.append)
        guarded.setLeaveGuard("settings", True)
        guarded.push("home")
        assert guarded.confirmLeave(False) is False
        assert guarded.currentRoute == "settings"
        assert cancelled == ["settings"]
        assert guarded.property("pendingLeaveRoute") == ""
        # 取消之后守卫还在（用户留下了，草稿也没丢）
        assert guarded.guardedDomains() == ["settings"]

    def test_confirm_navigates_even_if_the_page_discards_first(self, guarded: NavBridge) -> None:
        """**这是 3.4 的一个真缺陷的回归钉**（探针先红的）：

        确认流程是"先 `discardDraft()`（→ `setLeaveGuard(False)`）再 `confirmLeave(True)`"。
        第一版让 `setLeaveGuard(False)` 顺手把待裁决导航作废了（本意是防幽灵跳转），
        于是那次导航在到达 `confirmLeave` 之前就被清掉 —— 用户点了「确定」却留在原地。
        """
        confirmed: List[str] = []
        guarded.leaveConfirmed.connect(
            lambda domain: (confirmed.append(domain), guarded.setLeaveGuard(domain, False))
        )
        guarded.setLeaveGuard("settings", True)
        guarded.push("home")
        assert guarded.confirmLeave(True) is True
        assert guarded.currentRoute == "home"
        assert confirmed == ["settings"]
        assert guarded.guardedDomains() == [], "丢弃之后守卫要撤销"

    def test_confirm_without_a_request_does_nothing(self, guarded: NavBridge) -> None:
        assert guarded.confirmLeave(True) is False
        assert guarded.currentRoute == "settings"

    def test_guard_applies_to_back_and_home_and_reset(self, guarded: NavBridge) -> None:
        guarded.push("settings/java")
        guarded.setLeaveGuard("settings", True)
        assert guarded.goBack() is True, "域内返回不拦（settings/java → settings）"
        assert guarded.currentRoute == "settings"
        assert guarded.goHome() is False, "回首页是跨域，要问"
        assert guarded.reset() is False, "重置栈同样是离开设置域"
        assert guarded.goBackTo(1) is False, "退到 home 也要问（栈里只剩 [home, settings]）"
        assert guarded.currentRoute == "settings"
        assert guarded.property("pendingLeaveRoute") == "home"

    def test_deep_link_is_blocked_too(self, guarded: NavBridge) -> None:
        guarded.setLeaveGuard("settings", True)
        assert guarded.openDeepLink("fmcl://versions/detail?version=1.20.4") is False
        assert guarded.property("pendingLeaveRoute") == "versions/detail"

    def test_guard_is_domain_scoped(self, nav: NavBridge) -> None:
        nav.push("home")
        nav.push("versions")
        nav.setLeaveGuard("settings", True)
        assert nav.push("home") is True, "守卫登记在设置域，不该拦住版本页的导航"
        assert nav.guardedDomains() == ["settings"]

    def test_set_leave_guard_normalizes_domain(self, guarded: NavBridge) -> None:
        guarded.setLeaveGuard("settings/java", True)
        assert guarded.guardedDomains() == ["settings"], "域 id 取路由 id 的第一段"

    def test_unknown_route_still_fails_loudly(self, guarded: NavBridge) -> None:
        """守卫不拦"未知路由"这条错：那是错误，不是"要不要保存"的问题。"""
        errors: List[str] = []
        guarded.navFailed.connect(errors.append)
        guarded.setLeaveGuard("settings", True)
        assert guarded.push("no/such/route") is False
        assert errors and guarded.property("pendingLeaveRoute") == ""
