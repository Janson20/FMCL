"""ThemeBridge 测试（阶段 2 任务 2.8）。

三条纪律（与 `tests/test_hotkey_bridge.py` 一致）：

- ``QT_QPA_PLATFORM=offscreen`` 在 **import PySide6 之前**设置；
- **自己建 `QGuiApplication`**（一个进程只能有一个：先看 `instance()`）；
- 不引第三方测试依赖（没有 ``pytest-qt``）。

隔离要点：

- ``COLORS`` 是**全进程唯一的可变字典**，每个用例前后都还原快照
  （否则会污染 ``tests/test_theme_engine.py`` 之类直接读它的用例）；
- 配置对象用假对象注入（``config=``），**既不读也不写**真实的 ``config.json``；
- 主题引擎用真服务 + ``tmp_path``（业务逻辑就在服务里，mock 掉等于什么都没测）；
- QML 引擎用不带 FluentUI 导入路径的裸 ``QQmlApplicationEngine``：这样
  ``singletonInstance`` 拿不到 FluTheme，正好覆盖"缺失时优雅降级"这条路径；
  需要覆盖"注入成功"时用假引擎 + 假 FluTheme（``_FakeFluTheme``）。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import gc
import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterator, List, Tuple

import pytest
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from app.bridges import theme_bridge as tb
from services import palette
from services.theme_service import ThemeEngine


def _qapp() -> QGuiApplication:
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication([])
    return app


_APP = _qapp()

#: 契约 §5.1 冻结的 12 个色键（顺序也冻结）。
EXPECTED_COLOR_NAMES: Tuple[str, ...] = (
    "bgDark", "bgMedium", "bgLight", "accent", "accentHover", "success",
    "warning", "error", "textPrimary", "textSecondary", "cardBg", "cardBorder",
)

#: 5 个预设主题（`default` + 4 个彩色预设）。
PRESET_NAMES = {"default", "ocean", "forest", "lavender", "sunset"}


# ─── 夹具 ───────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def pristine_palette() -> Iterator[None]:
    """用例前后还原全局 `COLORS`（它是全进程唯一的可变字典）。"""
    saved = dict(palette.COLORS)
    try:
        yield
    finally:
        palette.COLORS.clear()
        palette.COLORS.update(saved)


class _FakeConfig:
    """假配置对象：记录写入，**不碰**真实 `config.json`。"""

    def __init__(self, theme_name: str = "default", accent_color: Any = None) -> None:
        self.theme_name = theme_name
        self.accent_color = accent_color
        self.saves = 0

    def save_config(self) -> None:
        self.saves += 1


class _FakeFluTheme(QObject):
    """假装是 FluTheme 单例：只记账 `setProperty` 调用。

    桥只通过 `setProperty` 写它（阶段 0 第 10.1 节的实测写法），
    所以覆盖这个方法就足以断言"映射表里的键 + darkMode 真的写进去了"。
    `QObject.setProperty` 不是虚函数，Python 侧的同名方法会直接生效。
    """

    def __init__(self) -> None:
        super().__init__()
        self.written: Dict[str, Any] = {}
        #: 写入顺序（返工 A 组 D-140：`darkMode` 必须在颜色之前）
        self.order: List[str] = []

    def setProperty(self, name: str, value: Any) -> bool:  # noqa: N802 - Qt 命名
        self.written[name] = value
        self.order.append(name)
        return True


class _FakeFluThemeWithRefresh(_FakeFluTheme):
    """比 `_FakeFluTheme` 更真：模拟 `darkMode` 变化触发 `refreshColors()`。

    这是 FluTheme **C++ 侧真实存在的行为**（`third_party/FluentUI/src/FluTheme.cpp`）：

        connect(this, &FluTheme::darkModeChanged, this, [=] { Q_EMIT darkChanged(); });
        connect(this, &FluTheme::darkChanged, this, [=] { refreshColors(); });

    而 `refreshColors()` 会把**全部**颜色重置成 FluentUI 的默认值。
    实测（`poc/_probe_fluent_darkmode.py`）：Light→Dark 切一次，刚写进去的
    `windowBackgroundColor=#1a1a2e` 立刻变回 `#202020`。

    所以"先写 darkMode 还是先写颜色"这件事**有可观测后果**，这里把它变成判据：
    写序反了的话，我们注入的颜色会被这个假对象冲掉，用例当场红。
    `Q_PROPERTY_AUTO` 宏里 `if (_##M != in_##M)` 的判断也照抄了 ——
    同值再写一次不触发（实测同样如此），所以这个坑只在 darkMode 真的变化时出现。
    """

    #: 与 FluTheme 深色默认值同量级的一组假默认值（值本身不重要，重要的是"会被冲掉"）
    DEFAULTS = {
        "primaryColor": "#0078d4",
        "backgroundColor": "#000000",
        "windowBackgroundColor": "#202020",
        "windowActiveBackgroundColor": "#1a1a1a",
        "frameColor": "#383838",
        "frameActiveColor": "#303030",
        "dividerColor": "#505050",
        "fontPrimaryColor": "#f8f8f8",
        "fontSecondaryColor": "#dedede",
        "fontTertiaryColor": "#c8c8c8",
        "itemHoverColor": "#0fffffff",
        "itemPressColor": "#17ffffff",
        "itemCheckColor": "#1fffffff",
    }

    def __init__(self) -> None:
        super().__init__()
        self.dark_mode = tb.FLUENT_LIGHT
        self.colors: Dict[str, QColor] = {k: QColor(v) for k, v in self.DEFAULTS.items()}
        #: 记录每一次"刷新"发生的时间点（写序反了就能从这上面看出来）
        self.refreshes: List[str] = []

    def setProperty(self, name: str, value: Any) -> bool:  # noqa: N802
        if name == tb.FLUENT_DARK_MODE_PROP:
            self.written[name] = value
            self.order.append(name)
            if int(value) != self.dark_mode:
                self.dark_mode = int(value)
                # ← 这就是 refreshColors()：把我们注入的颜色全部换成默认值
                self.colors = {k: QColor(v) for k, v in self.DEFAULTS.items()}
                self.refreshes.append(name)
            return True
        super().setProperty(name, value)  # 记账（written / order）
        self.colors[name] = QColor(value) if isinstance(value, QColor) else value
        return True


class _FakeQmlEngine:
    """假装是 QQmlEngine：满足桥用到的 `importPathList` / `singletonInstance`。

    `importPathList()` 必须给一条**真的带 `FluentUI/qmldir`** 的路径：桥把
    "导入路径里有 FluentUI 模块"当成调 `singletonInstance` 的**硬前置条件** ——
    模块解析不了时那个调用不是返回 None，而是把整个进程崩掉（全量测试实测踩到）。
    """

    def __init__(self, theme: Any, module_root: Any = None) -> None:
        self._theme = theme
        self._import_paths = [str(module_root)] if module_root is not None else []
        self.calls: List[Tuple[str, str]] = []

    def importPathList(self) -> List[str]:  # noqa: N802 - Qt 命名
        return list(self._import_paths)

    def singletonInstance(self, uri: str, name: str) -> Any:  # noqa: N802 - Qt 命名
        self.calls.append((uri, name))
        return self._theme


def _fake_fluent_module(tmp_path: Path) -> Path:
    """造一个"导得进 FluentUI 模块"的导入路径根（只要有 `FluentUI/qmldir`）。"""
    module = tmp_path / "qml" / "FluentUI"
    module.mkdir(parents=True, exist_ok=True)
    (module / "qmldir").write_text("module FluentUI\n", encoding="utf-8")
    return module.parent


@pytest.fixture
def theme_engine(pristine_palette, tmp_path: Path) -> ThemeEngine:
    """真 `ThemeEngine` + 隔离目录（用户主题目录落在 tmp_path 下）。"""
    return ThemeEngine(str(tmp_path))


@pytest.fixture
def bare_engine() -> QQmlApplicationEngine:
    """**不带** FluentUI 导入路径的引擎 —— `singletonInstance` 取不到 FluTheme。"""
    return QQmlApplicationEngine()


@pytest.fixture
def config() -> _FakeConfig:
    return _FakeConfig()


@pytest.fixture
def bridge(theme_engine: ThemeEngine, bare_engine: QQmlApplicationEngine, config: _FakeConfig) -> tb.ThemeBridge:
    return tb.ThemeBridge(engine=bare_engine, theme_engine=theme_engine, config=config)


class _Spy:
    """信号记录器（不引 pytest-qt：直接连一个 Python 可调用对象即可）。"""

    def __init__(self, signal: Any) -> None:
        self.calls: List[Tuple[Any, ...]] = []
        signal.connect(self._record)

    def _record(self, *args: Any) -> None:
        self.calls.append(args)

    @property
    def count(self) -> int:
        return len(self.calls)


# ─── 1. 12 个色键 ───────────────────────────────────────────────


def test_twelve_colors_track_the_global_palette(bridge: tb.ThemeBridge) -> None:
    """12 个色键的值必须与 `services.palette.COLORS` **逐个一致**。"""
    assert [camel for camel, _ in tb.COLOR_KEYS] == list(EXPECTED_COLOR_NAMES)
    for camel, snake in tb.COLOR_KEYS:
        value = getattr(bridge, camel)
        assert isinstance(value, QColor), f"{camel} 不是 QColor（QML 的 color 收不了）"
        assert value.name() == str(palette.COLORS[snake]).lower(), f"{camel} 与 COLORS[{snake}] 不一致"


def test_meta_object_exposes_the_frozen_surface(bridge: tb.ThemeBridge) -> None:
    """QML 只能看到**注册进元对象**的属性与槽 —— 名字错一个就是静默缺席。"""
    meta = bridge.metaObject()
    properties = {meta.property(i).name() for i in range(meta.propertyCount())}
    # `QMetaMethod.name()` 在 PySide6 里返回 QByteArray（不是 str），必须显式解码
    methods = {bytes(meta.method(i).name()).decode("utf-8") for i in range(meta.methodCount())}
    assert not (set(EXPECTED_COLOR_NAMES) - properties), f"缺属性: {sorted(set(EXPECTED_COLOR_NAMES) - properties)}"
    assert not ({"revision", "currentTheme", "themes", "fluentAvailable"} - properties), sorted(
        {"revision", "currentTheme", "themes", "fluentAvailable"} - properties
    )
    for token in ("fontFamily", "fontSizeBase", "spacingMd", "radiusMd", "iconSize"):
        assert token in properties, f"设计令牌 {token} 没注册成 Q_PROPERTY"
    for slot in ("refresh", "setTheme", "setAccent", "importTheme", "availableThemes",
                 "versionAccent", "applyVersionTheme"):
        assert slot in methods, f"槽 {slot} 没注册进元对象，QML 调不到"


def test_design_tokens_are_positive_and_font_family_is_set(bridge: tb.ThemeBridge) -> None:
    """设计令牌都是正数、字号递增，且 `fontFamily` 非空。"""
    assert bridge.fontFamily, "fontFamily 是空的 —— QML 拿不到中文字体"
    assert bridge.fontFamily == str(tb.FONT_FAMILY)
    sizes = [bridge.fontSizeSmall, bridge.fontSizeBase, bridge.fontSizeLarge, bridge.fontSizeTitle]
    assert sizes == sorted(sizes) and all(v > 0 for v in sizes), sizes
    spacings = [bridge.spacingXs, bridge.spacingSm, bridge.spacingMd, bridge.spacingLg, bridge.spacingXl]
    assert spacings == sorted(spacings) and all(v > 0 for v in spacings), spacings
    radii = [bridge.radiusSm, bridge.radiusMd, bridge.radiusLg]
    assert radii == sorted(radii) and all(v > 0 for v in radii), radii
    assert bridge.iconSize > 0
    # 取值来自 `ui/` 的实测分布（见桥的模块文档），这里把"量出来的那几个数"钉住
    assert (bridge.fontSizeSmall, bridge.fontSizeBase, bridge.fontSizeLarge, bridge.fontSizeTitle) == (10, 12, 14, 18)
    assert (bridge.spacingXs, bridge.spacingSm, bridge.spacingMd, bridge.spacingLg, bridge.spacingXl) == (5, 10, 15, 20, 30)
    assert (bridge.radiusSm, bridge.radiusMd, bridge.radiusLg) == (6, 8, 12)
    assert bridge.iconSize == 20
    # 返工 A 组新增的动效/布局令牌也要在元对象里（QML 读不到就是静默缺席）
    assert (bridge.durationFast, bridge.durationNormal, bridge.durationSlow) == (110, 190, 300)
    assert bridge.durationFast < bridge.durationNormal < bridge.durationSlow
    assert bridge.navWidth > 0 and bridge.titleBarHeight > 0 and bridge.statusBarHeight > 0


# ─── 1b. 派生语义令牌（返工 A 组任务 A5） ───────────────────────


def test_derived_tokens_are_exposed_and_not_black(bridge: tb.ThemeBridge) -> None:
    """15 个派生令牌都必须出现在元对象里，且**都是有效颜色**（不是兜底黑）。"""
    meta = bridge.metaObject()
    properties = {meta.property(i).name() for i in range(meta.propertyCount())}
    missing = [name for name in tb.TOKEN_KEYS if name not in properties]
    assert missing == [], f"派生令牌没注册成 Q_PROPERTY: {missing}"

    for name in tb.TOKEN_KEYS:
        value = getattr(bridge, name)
        assert isinstance(value, QColor), f"{name} 不是 QColor（QML 的 color 收不了）"
        assert value.isValid(), f"{name} 不是有效颜色"
    # 兜底黑只该在"读不到主题值"时出现；正常主题下不该有一片黑
    opaque = [n for n in tb.TOKEN_KEYS if bridge._token(n).alpha() == 255]
    assert all(bridge._token(n) != QColor(tb.FALLBACK_COLOR) for n in opaque), (
        "有不透明令牌掉进了兜底黑 —— 派生公式里的键名写错时会这样"
    )


def test_derived_tokens_follow_the_palette(bridge: tb.ThemeBridge, theme_engine: ThemeEngine) -> None:
    """派生令牌必须**跟着主题走**，而不是在桥构造时算一次就冻住。

    ⚠️ 判据写成"切主题后哪些令牌变了"之前先想清楚**比较的基准**：`COLORS` 是全进程唯一的
    可变字典，全量跑时上一个用例可能留下任意一套颜色（返工 A 组第一版就是拿环境里的
    accent 当基准，单跑绿、全量红）。所以这里不假设基准是什么，只断言"相对变化"。
    """
    before = {name: bridge._token(name).name() for name in tb.TOKEN_KEYS}
    accent_before = bridge.accent.name()
    bridge.setTheme("ocean")
    after = {name: bridge._token(name).name() for name in tb.TOKEN_KEYS}
    assert bridge.accent.name() != accent_before, "主题没切成功，后面的判据都不成立"
    changed = [name for name in tb.TOKEN_KEYS if before[name] != after[name]]
    # 15 个令牌里只有这几个**可能**与主题无关：
    #   * itemHover/itemPress/itemCheck 是"白 + alpha"（深色锁定，见模块文档）；
    #   * scrim 是纯黑 + alpha；
    #   * accentText 只在强调色**跨过亮度阈值**时才换（两个主题的强调色可能都偏暗 → 都是白字）。
    # 其余必须跟着主题变。少一个就说明那条派生链没接上 `_refresh_tokens()`。
    may_stay = {"itemHover", "itemPress", "itemCheck", "scrim", "accentText"}
    must_change = set(tb.TOKEN_KEYS) - may_stay
    missed = sorted(must_change - set(changed))
    assert missed == [], f"切主题后没跟着变的令牌: {missed}"
    # 反过来也要收紧：与主题无关的那几个里，只允许"叠加色"不变（accentText 允许不变）
    assert set(changed) <= set(tb.TOKEN_KEYS), "changed 里出现了不属于令牌表的名字"


def test_token_relations_hold(bridge: tb.ThemeBridge) -> None:
    """令牌之间的**关系**（比具体值更稳的判据，用户换主题也不会红）。"""
    # 表面层级：窗口底 < 导航/顶栏 < 卡片 < 卡片悬停
    assert bridge.windowBg.lightness() <= bridge.navBg.lightness(), "页面区没有比壳层更暗"
    assert bridge.navBg.lightness() <= bridge.cardBg.lightness(), "卡片没有比导航栏更亮"
    assert bridge.cardHover.lightness() > bridge.cardBg.lightness(), "悬停色没有比卡片更亮"
    # 失焦的窗口底色比激活时更暗
    assert bridge.windowBgInactive.lightness() < bridge.windowBg.lightness()
    # 叠加色必须带透明度（否则会把下层完全盖住）
    for name in ("accentSoft", "itemHover", "itemPress", "itemCheck", "scrim"):
        assert bridge._token(name).alpha() < 255, f"{name} 是全不透明的，叠不上去"
    # 文字层级：primary 亮于 secondary 亮于 tertiary
    assert (
        bridge.textPrimary.lightness() > bridge.textSecondary.lightness() > bridge.textTertiary.lightness()
    ), "三级文字的明度顺序不对"
    # 按下态比常态暗
    assert bridge.accentPressed.lightness() < bridge.accent.lightness(), "accentPressed 没有比 accent 暗"


def test_accent_text_switches_on_light_accents(theme_engine: ThemeEngine) -> None:
    """`accentText` 要按强调色亮度自动选色（阶段 2 的遗留项 `onAccent`）。

    强调色 `#e94560` 偏暗 → 白字；换成亮黄之后必须换成深色字，
    否则"白字压亮黄"几乎看不见。

    **两个桥都用配置注入强调色**（而不是读环境里那套）：`COLORS` 是全进程唯一的可变字典，
    全量跑时前面的用例会留下任意强调色 —— 第一版直接读环境，单跑绿、全量红。
    """
    dark_accent = tb.ThemeBridge(
        engine=QQmlApplicationEngine(),
        theme_engine=theme_engine,
        config=_FakeConfig(accent_color="#e94560"),
    )
    assert dark_accent.accent.name() == "#e94560", "强调色没设上，后面判据不成立"
    assert dark_accent.accentText.name() == "#ffffff", "偏暗的强调色应当配白字"

    light_accent = tb.ThemeBridge(
        engine=QQmlApplicationEngine(),
        theme_engine=theme_engine,
        config=_FakeConfig(accent_color="#ffe066"),
    )
    assert light_accent.accent.name() == "#ffe066", "强调色没设上，后面判据不成立"
    assert light_accent.accentText.lightness() < 128, (
        f"亮强调色上仍然给白字（accentText={light_accent.accentText.name()}）—— 对比度不够"
    )
    assert tb.relative_luminance(QColor("#ffe066")) > tb.ACCENT_TEXT_LUMINANCE_THRESHOLD
    assert tb.relative_luminance(QColor("#e94560")) <= tb.ACCENT_TEXT_LUMINANCE_THRESHOLD


# ─── 2. 主题切换 ────────────────────────────────────────────────


def test_set_theme_ocean_updates_accent_and_emits_changed(
    bridge: tb.ThemeBridge, theme_engine: ThemeEngine, config: _FakeConfig
) -> None:
    """`setTheme('ocean')` → accent 变 `#00b4d8`、`changed` 发出、配置被持久化。"""
    changed = _Spy(bridge.changed)
    theme_changed = _Spy(bridge.themeChanged)
    before_revision = bridge.revision

    bridge.setTheme("ocean")

    assert bridge.accent.name() == "#00b4d8"
    assert palette.COLORS["accent"] == "#00b4d8", "服务没把新主题写进全局 COLORS"
    assert changed.count >= 1, "changed 没发 —— QML 绑定不会重算"
    assert theme_changed.calls == [("ocean",)]
    assert bridge.currentTheme == "ocean"
    assert bridge.revision > before_revision
    # 持久化：与旧界面 set_theme_name 一致（写 config + 落盘）
    assert config.theme_name == "ocean" and config.saves == 1


def test_set_theme_unknown_name_fails_loudly_but_safely(bridge: tb.ThemeBridge) -> None:
    """未知主题：不抛异常、不改色，发 `themeFailed`（QML 用它弹提示）。"""
    failed = _Spy(bridge.themeFailed)
    changed = _Spy(bridge.changed)
    accent_before = bridge.accent.name()

    bridge.setTheme("no-such-theme")

    assert bridge.accent.name() == accent_before
    assert changed.count == 0
    assert failed.count == 1 and "未知主题" in failed.calls[0][0]


def test_set_accent_recomputes_hover_through_the_service(bridge: tb.ThemeBridge) -> None:
    """`setAccent` 之后 `accentHover` 必须是服务算出来的变亮值（桥不自己算颜色）。"""
    bridge.setTheme("ocean")
    bridge.setAccent("#123456")

    assert bridge.accent.name() == "#123456"
    expected = ThemeEngine._lighten_color("#123456", 0.3)
    assert bridge.accentHover.name() == expected
    assert palette.COLORS["accent_hover"] == expected


def test_set_accent_empty_string_clears_it(bridge: tb.ThemeBridge, config: _FakeConfig) -> None:
    """空串 = 清除自定义强调色（旧界面 `_on_accent_apply` 的空值分支）。"""
    bridge.setTheme("ocean")
    bridge.setAccent("#123456")
    bridge.setAccent("")

    assert bridge.accent.name() == "#00b4d8", "清除后应回到主题自带的强调色"
    assert config.accent_color is None


def test_set_accent_rejects_bad_format(bridge: tb.ThemeBridge) -> None:
    """非法强调色只发 `themeFailed`，不改色（判据与旧界面的格式校验一致）。"""
    failed = _Spy(bridge.themeFailed)
    for bad in ("red", "#12345", "#gggggg", "123456"):
        bridge.setAccent(bad)
    assert failed.count == 4
    assert all("格式不对" in args[0] for args in failed.calls)


def test_revision_is_monotonic_and_only_bumps_on_real_change(bridge: tb.ThemeBridge) -> None:
    """`revision` 单调递增；`refresh()` 在**没有真变化**时不空发 `changed`。"""
    seen = [bridge.revision]
    bridge.setTheme("ocean")
    seen.append(bridge.revision)
    bridge.setAccent("#123456")
    seen.append(bridge.revision)
    bridge.applyVersionTheme("1.20")
    seen.append(bridge.revision)
    assert seen == sorted(seen) and len(set(seen)) == len(seen), f"revision 不是严格递增: {seen}"

    changed = _Spy(bridge.changed)
    revision_before = bridge.revision
    bridge.refresh()
    assert bridge.revision == revision_before, "refresh() 在没有变化时不该自增 revision"
    assert changed.count == 0


def test_refresh_picks_up_external_mutation_of_the_global_dict(bridge: tb.ThemeBridge) -> None:
    """`COLORS` 被别人原地改了 → `refresh()` 要把新值读出来并通知 QML。

    ## 探针色必须**动态**挑（原先写死 `#abcdef`，全量跑时红过一次）

    `refresh()` 的语义是"**有变化时**才发 `changed`"。写死探针色的话，只要桥的缓存
    恰好已经等于那个颜色，就变成"确实没变化、确实不该发通知"—— 断言 `count == 1`
    必然失败。实测现象是**单独跑绿、全量跑红**（顺序依赖），不是产品缺陷。
    所以这里按"桥当前缓存值"反选一个必然不同的色。
    """
    cached = bridge.accent.name().lower()
    probe = "#abcdef" if cached != "#abcdef" else "#123456"
    assert probe != cached  # 保证下面测的是"真的变了"

    changed = _Spy(bridge.changed)
    palette.COLORS["accent"] = probe

    bridge.refresh()

    assert bridge.accent.name() == probe
    assert changed.count == 1


# ─── 3. 颜色归一化（D-63 / 风险 R-14） ──────────────────────────


def test_normalize_color_takes_the_first_element_of_a_tuple() -> None:
    """`(light, dark)` 元组取**第一个**元素（QML 的 color 不接受元组）。"""
    assert tb.normalize_color(("#ffffff", "#000000")) == "#ffffff"
    assert tb.normalize_color(("#E94560", "#000000")) == "#e94560"
    assert tb.normalize_color(["#0a0b0c", "#ffffff"]) == "#0a0b0c"
    # 畸形嵌套也照样折到底（递归），折不出来才回退
    assert tb.normalize_color((("#123456", "#000000"), "#ffffff")) == "#123456"


def test_normalize_color_falls_back_to_black_and_warns(caplog: pytest.LogCaptureFixture) -> None:
    """非法值 → `#000000` + warning（**不抛异常**：一个坏色值不该让界面起不来）。"""
    bad_values = (None, "", "   ", "not-a-color", "#12", 42, (), [], {"a": 1})
    with caplog.at_level("WARNING", logger=tb.__name__):
        for value in bad_values:
            assert tb.normalize_color(value, "accent") == tb.FALLBACK_COLOR, repr(value)
    assert len(caplog.records) == len(bad_values), "每个坏值都该有一条 warning"
    assert all("accent" in record.getMessage() for record in caplog.records)


def test_normalize_color_keeps_alpha_and_expands_short_form() -> None:
    """`#rgb` 展开成 `#rrggbb`；带 alpha 的写法保留 alpha 位数。"""
    assert tb.normalize_color("#fff") == "#ffffff"
    assert tb.normalize_color("#aabbccdd").lower() == "#aabbccdd"


def test_colors_property_normalizes_tuple_values_in_the_global_dict(bridge: tb.ThemeBridge) -> None:
    """端到端：`COLORS` 里塞元组 → 桥读出来的是字符串（不是元组，也不崩）。"""
    palette.COLORS["accent"] = ("#112233", "#445566")
    palette.COLORS["card_bg"] = None

    bridge.refresh()

    assert bridge.accent.name() == "#112233"
    assert bridge.cardBg.name() == tb.FALLBACK_COLOR


def test_is_hex_color_matches_the_legacy_validation() -> None:
    assert tb.is_hex_color("#123456") and tb.is_hex_color("#AABBCC")
    assert not tb.is_hex_color("123456")
    assert not tb.is_hex_color("#12345")
    assert not tb.is_hex_color("#1234567")
    assert not tb.is_hex_color("#gggggg")


# ─── 4. 主题清单与导入 ──────────────────────────────────────────


def test_available_themes_lists_the_presets_with_source(bridge: tb.ThemeBridge) -> None:
    """`availableThemes()` 含 5 个预设（default + 4 个彩色预设），每个都带 `source`。"""
    themes = bridge.availableThemes()
    assert isinstance(themes, list) and len(themes) >= 5
    names = {item["name"] for item in themes}
    assert PRESET_NAMES <= names, f"少了预设: {sorted(PRESET_NAMES - names)}"
    for item in themes:
        assert item["source"] in ("preset", "user")
        assert set(item) >= {"name", "author", "description", "version", "colors", "source"}
    assert bridge.themes == themes, "属性 themes 必须与 availableThemes() 是同一份数据"


def test_import_theme_rejects_invalid_json(bridge: tb.ThemeBridge, tmp_path: Path) -> None:
    """非法 json → 返回 False + `themeFailed`（解析在服务里，原因原样透出）。"""
    bad = tmp_path / "broken.json"
    bad.write_text("{ not json at all", encoding="utf-8")
    failed = _Spy(bridge.themeFailed)

    assert bridge.importTheme(str(bad)) is False
    assert failed.count == 1 and "JSON" in failed.calls[0][0]


def test_import_theme_accepts_a_real_file_and_refreshes_the_list(
    bridge: tb.ThemeBridge, tmp_path: Path
) -> None:
    """合法主题文件 → True、`themesChanged` 发出、列表里出现新的 user 主题。"""
    theme_file = tmp_path / "custom.json"
    theme_file.write_text(
        json.dumps({"name": "custom", "author": "test", "colors": {"accent": "#010203"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    themes_changed = _Spy(bridge.themesChanged)

    assert bridge.importTheme(str(theme_file)) is True
    assert themes_changed.count == 1
    imported = [item for item in bridge.availableThemes() if item["name"] == "custom"]
    assert imported and imported[0]["source"] == "user"

    # 导入之后可以切换过去（强调色来自刚导入的那份）
    bridge.setTheme("custom")
    assert bridge.accent.name() == "#010203"


def test_import_theme_missing_file_fails_softly(bridge: tb.ThemeBridge, tmp_path: Path) -> None:
    failed = _Spy(bridge.themeFailed)
    assert bridge.importTheme(str(tmp_path / "nope.json")) is False
    assert failed.count == 1 and "文件不存在" in failed.calls[0][0]


# ─── 5. 版本动态调色 ────────────────────────────────────────────


def test_version_accent_hits_and_misses(bridge: tb.ThemeBridge) -> None:
    """`versionAccent('1.21.4')` 命中 1.21 那一档；不命中返回空 map。"""
    hit = bridge.versionAccent("1.21.4")
    assert hit["accent"] == "#6a0dad" and hit["accent_hover"] == "#8b2fc9"
    assert hit["description"], "版本档位应带 description"

    assert bridge.versionAccent("1.6") == {}, "1.6 不该命中 1.16（前缀匹配，业务在服务里）"
    assert bridge.versionAccent("") == {}
    assert bridge.versionAccent("not-a-version") == {}


def test_apply_version_theme_recolors_the_current_theme(bridge: tb.ThemeBridge) -> None:
    """`applyVersionTheme` 把版本强调色应用上去（accent_hover 由服务重算）。"""
    bridge.setTheme("ocean")
    changed = _Spy(bridge.changed)

    bridge.applyVersionTheme("1.21.4")

    assert bridge.accent.name() == "#6a0dad"
    assert bridge.accentHover.name() == ThemeEngine._lighten_color("#6a0dad", 0.3)
    assert bridge.currentTheme == "ocean", "版本调色不换主题，只换强调色"
    assert changed.count >= 1


def test_apply_version_theme_unknown_version_reports_failure(bridge: tb.ThemeBridge) -> None:
    failed = _Spy(bridge.themeFailed)
    bridge.applyVersionTheme("0.0")
    assert failed.count == 1 and "没有对应的动态主题" in failed.calls[0][0]


# ─── 6. FluTheme 注入与优雅降级 ─────────────────────────────────


def test_bridge_survives_a_missing_fluent_theme(
    theme_engine: ThemeEngine, bare_engine: QQmlApplicationEngine, config: _FakeConfig
) -> None:
    """**取不到 FluTheme 时构造桥不能抛异常**，颜色照常可用。"""
    built = tb.ThemeBridge(engine=bare_engine, theme_engine=theme_engine, config=config)
    assert built.fluentAvailable is False, "裸引擎（没挂 FluentUI 导入路径）不该接上 FluTheme"
    assert built.accent.name() == str(palette.COLORS["accent"]).lower()
    # 后续同步会重试，但依然不许抛
    built.refresh()
    built.setTheme("forest")
    assert built.currentTheme == "forest" and built.accent.name() == "#2ecc71"


def test_bridge_never_calls_singleton_instance_without_the_module(
    theme_engine: ThemeEngine, config: _FakeConfig, tmp_path: Path
) -> None:
    """模块不在导入路径里时**连 `singletonInstance` 都不许调**（那会崩进程）。

    这是全量测试里实测踩到的崩溃：同进程里另一个引擎先加载过 FluentUI 之后，
    拿一个没挂导入路径的引擎去 `singletonInstance`，Qt 直接 access violation，
    Python 侧 `try/except` 兜不住。判据必须放在**调用之前**。
    """
    engine = _FakeQmlEngine(_FakeFluTheme())  # 有 FluTheme 也不许调：模块不可解析
    built = tb.ThemeBridge(engine=engine, theme_engine=theme_engine, config=config)
    assert engine.calls == [], f"没有 FluentUI 模块却调了 singletonInstance: {engine.calls}"
    assert built.fluentAvailable is False
    # 真引擎那条路径同样要挡住（裸 QQmlApplicationEngine，没 addImportPath）
    real = QQmlApplicationEngine()
    tb.ThemeBridge(engine=real, theme_engine=theme_engine, config=config)  # 不崩即通过
    assert tb._fluent_module_dir(real) is None


def test_bridge_survives_when_singleton_instance_returns_nothing(
    theme_engine: ThemeEngine, config: _FakeConfig, tmp_path: Path
) -> None:
    """模块在导入路径里、但 `singletonInstance` 返回 None（类型没注册）时同样只降级。"""
    root = _fake_fluent_module(tmp_path)
    built = tb.ThemeBridge(engine=_FakeQmlEngine(None, root), theme_engine=theme_engine, config=config)
    assert built.fluentAvailable is False


# ─── 6b. 引擎只认显式注入（缺陷 D-153） ─────────────────────────


def test_engine_is_taken_only_from_explicit_injection(
    theme_engine: ThemeEngine, config: _FakeConfig
) -> None:
    """**D-153 的反向判据**：进程里就算有别的引擎，没注入就是不注入 —— 桥不许去猜。

    缺陷现象（阶段 1 证据脚本 `poc/_verify_group_b_independent.py` 第 5 节的红灯）：
    早先这里有一条"在 `gc.get_objects()` 里取进程里**最后一个** `QQmlEngine`"的自动发现
    兜底，于是**先**建过一个游离引擎（测试探针、别的模块）时取的往往是它 ——
    `fluentAvailable` 变 False，而入口那个真引擎反而没接上。

    判据要能真的抓住回归，所以先证"这个游离引擎确实躺在 `gc` 的跟踪表里"
    （否则本用例就是空断言），再要求桥**看不见**它。
    """
    stray = QQmlApplicationEngine()  # 先造一个游离引擎（没挂 FluentUI 导入路径）
    assert any(obj is stray for obj in gc.get_objects()), (
        "游离引擎没被 gc 跟踪 —— 本用例模拟的『进程里有别的引擎』就不成立了，判据会退化成空断言"
    )

    built = tb.ThemeBridge(theme_engine=theme_engine, config=config)  # 刻意不传 engine=
    assert built._current_engine() is None, "桥又去进程里猜引擎了（D-153 回归）"
    assert built.fluentAvailable is False

    # 显式注入之后才认，而且认的就是注入的那个
    built.use_engine(stray)
    assert built._current_engine() is stray
    # 自动发现通道整条不存在（不是"被弱化"，是不许再长回来）
    assert not hasattr(tb.ThemeBridge, "_discover_engine"), "自动发现通道又回来了（D-153 回归）"


def test_injecting_the_engine_attaches_fluent_right_away(
    theme_engine: ThemeEngine, config: _FakeConfig, tmp_path: Path
) -> None:
    """注入引擎的那一刻就要接上 FluTheme。

    为什么必须"立刻"：入口的顺序是"建引擎 → 建桥并注册 → 注入 → `engine.load()`"，
    中间没有别的同步点（下一次同步要等到用户切主题/改强调色才发生）。
    不在这里真的注一次，FluentUI 控件在第一帧用的就是它自己的默认色 ——
    正是返工 A 组修过的"白顶栏"那类症状，而且没有任何报错。
    """
    fake = _FakeFluTheme()
    engine = _FakeQmlEngine(fake, _fake_fluent_module(tmp_path))
    built = tb.ThemeBridge(theme_engine=theme_engine, config=config)  # 先建桥（入口就是这么干的）
    assert built.fluentAvailable is False, "引擎都还没注入，不该已经接上 FluTheme"

    built.use_engine(engine)
    assert built.fluentAvailable is True, "注入引擎之后没有立刻接上 FluTheme"
    assert fake.written[tb.FLUENT_DARK_MODE_PROP] == tb.FLUENT_DARK
    assert fake.written["primaryColor"].name() == built._token("accent").name()


def test_re_injecting_another_engine_moves_the_fluent_target(
    theme_engine: ThemeEngine, config: _FakeConfig, tmp_path: Path
) -> None:
    """换引擎时要重新解析 FluTheme —— 缓存里握的是**上一个引擎**里的那个 C++ 单例。"""
    first, second = _FakeFluTheme(), _FakeFluTheme()
    built = tb.ThemeBridge(theme_engine=theme_engine, config=config)
    built.use_engine(_FakeQmlEngine(first, _fake_fluent_module(tmp_path)))
    assert built._fluent is first

    built.use_engine(_FakeQmlEngine(second, _fake_fluent_module(tmp_path)))
    assert built._fluent is second, "换了引擎还往上一个引擎的 FluTheme 写（写到一个没人看的对象上）"
    assert second.written["primaryColor"].name() == built._token("accent").name()


def test_fluent_log_levels_separate_the_middle_state_from_real_degradation(
    theme_engine: ThemeEngine, config: _FakeConfig, bare_engine: QQmlApplicationEngine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """两种"取不到 FluTheme"的日志级别不同（D-153 顺带定死的契约）。

    * **还没人注入引擎**：装配过程中的正常中间态（入口每次都是"先建桥、后注入"），
      报 warning 等于每次启动都留一条假的降级告警 —— 真正的降级会被它挤掉；
    * **引擎在、导入路径里没有 FluentUI 模块**：这才是真降级，报 warning，且**只报一次**
      （每次同步都会重试，不把这条告警锁住就会把日志刷爆）。
    """
    with caplog.at_level(logging.DEBUG, logger="app.bridges.theme_bridge"):
        built = tb.ThemeBridge(theme_engine=theme_engine, config=config)
        built.refresh()
        quiet = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert quiet == [], f"还没注入引擎就报了降级告警（入口每次都走这条路）: {quiet}"

        built.use_engine(bare_engine)  # 真引擎，但没有 FluentUI 导入路径
        built.refresh()
        built.refresh()
        noisy = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(noisy) == 1, f"真降级要报一次、且只报一次: {noisy}"


def test_fluent_theme_gets_the_keys_and_dark_mode(
    theme_engine: ThemeEngine, config: _FakeConfig, tmp_path: Path
) -> None:
    """映射表**真的**写到了 FluTheme 上，并且显式设了 `darkMode`。

    返工 A 组把"九键"扩成 :data:`tb.FLUENT_MAP` 全表（补上 `windowActiveBackgroundColor`
    这类以前没映射、于是用的是 FluentUI 自己灰度的键），并把 `darkMode` 从 1 改成 2 ——
    见下面 `test_dark_mode_uses_the_fluentui_enum_not_qt_colorscheme`。
    """
    fake = _FakeFluTheme()
    built = tb.ThemeBridge(engine=_FakeQmlEngine(fake, _fake_fluent_module(tmp_path)),
                           theme_engine=theme_engine, config=config)

    assert built.fluentAvailable is True
    expected = {prop: built._token(token).name() for token, prop in tb.FLUENT_MAP}
    actual = {
        name: value.name()
        for name, value in fake.written.items()
        if name != tb.FLUENT_DARK_MODE_PROP
    }
    assert actual == expected, f"映射写错了:\n实际 {actual}\n预期 {expected}"

    # 切主题之后 FluTheme 要跟着变（否则 FluentUI 控件不跟随）
    built.setTheme("ocean")
    assert fake.written["primaryColor"].name() == "#00b4d8"
    assert fake.written["fontPrimaryColor"].name() == "#e0e0e0"


def test_dark_mode_uses_the_fluentui_enum_not_qt_colorscheme(
    theme_engine: ThemeEngine, config: _FakeConfig, tmp_path: Path
) -> None:
    """`darkMode` 必须是 **2**（`FluThemeType::DarkMode::Dark`）—— 缺陷 D-139。

    这里曾经写的是 1，注释还写着"这是 Qt::Dark"。但 FluentUI 用的**不是** `Qt::ColorScheme`，
    而是它自己的枚举（`third_party/FluentUI/src/Def.h`）：

        enum DarkMode { System = 0x0000, Light = 0x0001, Dark = 0x0002 };

    于是 `1` 其实是 **Light** —— FluentUI 控件（FluWindow 的顶栏、输入框）全走浅色，
    而我们自己的壳层读的是深色调色板，界面上就是"白顶栏 + 黑正文"。
    实测证据：`poc/_probe_fluent_darkmode.py`（`darkMode=1 → dark=False`，`=2 → dark=True`）。
    """
    fake = _FakeFluTheme()
    tb.ThemeBridge(engine=_FakeQmlEngine(fake, _fake_fluent_module(tmp_path)),
                   theme_engine=theme_engine, config=config)

    assert tb.FLUENT_DARK == 2, "FluentUI 的 Dark 是 2（System=0 / Light=1 / Dark=2）"
    assert tb.FLUENT_LIGHT == 1
    assert fake.written[tb.FLUENT_DARK_MODE_PROP] == 2, (
        f"darkMode 写成了 {fake.written[tb.FLUENT_DARK_MODE_PROP]!r}；1 是 Light，界面上会出现浅色控件"
    )


def test_dark_mode_is_written_before_colors(
    theme_engine: ThemeEngine, config: _FakeConfig, tmp_path: Path
) -> None:
    """**写序**：`darkMode` 必须在颜色之前 —— 缺陷 D-140。

    `darkMode` 真的变化时会触发 `darkModeChanged → darkChanged → refreshColors()`，
    而 `refreshColors()` 会把全部颜色重置成 FluentUI 默认值。
    所以先写颜色再写 `darkMode`，等于刚注入的调色板立刻被冲掉 ——
    症状是"顶栏颜色对了、控件底色又变回 Flu 自己的灰"，而且没有任何报错。

    这个用例用 `_FakeFluThemeWithRefresh`（照抄了 FluTheme 的刷新行为）来判：
    只要写序反了，注入的颜色就活不下来。
    """
    fake = _FakeFluThemeWithRefresh()
    built = tb.ThemeBridge(engine=_FakeQmlEngine(fake, _fake_fluent_module(tmp_path)),
                           theme_engine=theme_engine, config=config)

    assert fake.refreshes, "假 FluTheme 认为 darkMode 没变过（从 Light 切 Dark 应当触发一次刷新）"
    assert fake.order[0] == tb.FLUENT_DARK_MODE_PROP, (
        f"第一条写的不是 darkMode 而是 {fake.order[0]!r} —— refreshColors() 会把颜色冲掉"
    )
    assert fake.order.index(tb.FLUENT_DARK_MODE_PROP) == 0

    # 语义判据（比顺序更本质）：注入的颜色在刷新之后**仍然是我们写的值**
    for token, prop in tb.FLUENT_MAP:
        expected = built._token(token).name()
        actual = fake.colors[prop].name()
        assert actual == expected, f"{prop} 被 refreshColors() 冲掉了：期望 {expected}，实际 {actual}"

    # 切主题之后重新注入一遍，值必须跟着变（而不是停在默认值上）
    built.setTheme("ocean")
    assert fake.colors["primaryColor"].name() == "#00b4d8"
    assert fake.colors["windowActiveBackgroundColor"].name() == built._token("windowBg").name(), (
        "切主题后派生令牌没有跟着重算（windowBg 应当来自新主题的 bg_dark）"
    )


def test_fluent_map_token_names_all_resolve(
    bridge: tb.ThemeBridge,
) -> None:
    """映射表左边的**令牌名**必须都能取到颜色（写错名字会静默变成黑色）。"""
    known = {camel for camel, _ in tb.COLOR_KEYS} | set(tb.TOKEN_KEYS)
    unknown = [token for token, _prop in tb.FLUENT_MAP if token not in known]
    assert unknown == [], f"映射表里有不认识的令牌名: {unknown}"
    # 派生令牌不许与 12 个主题色重名（重名会让主题文件里的值被悄悄覆盖）
    overlap = {camel for camel, _ in tb.COLOR_KEYS} & set(tb.TOKEN_KEYS)
    assert overlap == set(), f"派生令牌与主题色键重名: {overlap}"


# ─── 7. 入口的字体注入（任务 2.10） ─────────────────────────────


class _FakeEngineForFont:
    """假装是入口的 `QQmlApplicationEngine`：字体注入只需要那个强引用表。"""

    def __init__(self, theme: Any) -> None:
        self._fmcl_bridges: Dict[str, Any] = {} if theme is None else {"Theme": theme}


def _family_from_bridge(theme: tb.ThemeBridge) -> str:
    """入口按逗号切分 `fontFamily`（Linux 上它可能是组合链），取第一段。"""
    return str(theme.fontFamily).split(",")[0].strip()


def test_main_qml_applies_the_theme_font(bridge: tb.ThemeBridge) -> None:
    """`apply_theme_font()` 把 `Theme.fontFamily` 设成应用级字体。"""
    import main_qml

    previous = QGuiApplication.font()
    try:
        assert main_qml.apply_theme_font(_FakeEngineForFont(bridge)) is True
        families = QGuiApplication.font().families()
        assert families and families[0] == _family_from_bridge(bridge), (
            f"应用级字体没设上：期望 {_family_from_bridge(bridge)!r}，实际 {families!r}"
        )
    finally:
        QGuiApplication.setFont(previous)


def test_apply_theme_font_skips_silently_without_the_bridge(bridge: tb.ThemeBridge) -> None:
    """`Theme` 桥缺席（模块没落地 / 注册失败）时**静默跳过**，不改字体也不抛。"""
    import main_qml

    previous = QGuiApplication.font()
    try:
        for engine in (_FakeEngineForFont(None), object(), type("E", (), {"_fmcl_bridges": {}})()):
            assert main_qml.apply_theme_font(engine) is False
        assert QGuiApplication.font() == previous, "桥缺席时不该动应用级字体"
    finally:
        QGuiApplication.setFont(previous)


def test_bridge_registers_under_the_frozen_name() -> None:
    """入口的注册表里 `Theme` 必须指向本模块的桥（名字错一个 QML 就静默拿不到）。"""
    import main_qml

    entries = {name: (module, cls) for name, module, cls in main_qml.SINGLETON_BRIDGES}
    assert entries["Theme"] == ("app.bridges.theme_bridge", "ThemeBridge")
    assert tb.QML_NAME == "Theme"


def test_ui_constants_reexports_the_same_lazy_str():
    """旧写法 `from ui.constants import FONT_FAMILY` 必须拿到**同一个** `LazyStr` 实例。

    这是搬迁的硬要求：两份实例意味着两次字体探测，且两边可能给出不同的值。
    （`import ui.constants` 会执行 `ui/__init__.py`，本进程里 `tests/test_layering.py`
    也这么干，所以这里不额外付代价。）
    """
    import services.font_service as font_service
    import ui.constants as constants

    assert constants.FONT_FAMILY is font_service.FONT_FAMILY
    assert str(constants.FONT_FAMILY), "FONT_FAMILY 解出来是空串"


# ─── 8. QML 侧可见性（端到端） ──────────────────────────────────

#: 入口对单例桥的实际注册方式（见 `main_qml.register_bridges` 的文档）：
#: 登记模块名 + 上下文属性，**不**用 `qmlRegisterSingletonInstance`。
QML_PROBE = """
import QtQuick
import FMCL 1.0

Item {
    objectName: "themeProbe"
    property color themed: Theme.accent
    property color card: Theme.cardBg
    property int revision: Theme.revision
    property string family: Theme.fontFamily
    property int baseFont: Theme.fontSizeBase
    property string themeName: Theme.currentTheme
}
"""


def test_theme_is_visible_from_qml_and_rebinds_on_theme_change(tmp_path: Path, config: _FakeConfig) -> None:
    """端到端：QML 里 `import FMCL 1.0` 之后 `Theme.*` 可读，切主题时绑定**自动重算**。

    这是阶段 2 出门条件"主题热切换生效"的最小可断言形态：
    绑定重算靠的是 `changed` 这个 NOTIFY（QML 只跟踪属性读取，不跟踪函数返回值）。
    """
    from PySide6.QtQml import qmlRegisterModule

    theme_engine = ThemeEngine(str(tmp_path / "themes"))
    engine = QQmlApplicationEngine()
    bridge = tb.ThemeBridge(engine=engine, theme_engine=theme_engine, config=config)
    qmlRegisterModule("FMCL", 1, 0)
    engine.rootContext().setContextProperty(tb.QML_NAME, bridge)

    probe = tmp_path / "ThemeProbe.qml"
    probe.write_text(QML_PROBE, encoding="utf-8")
    engine.load(probe.as_uri())

    roots = engine.rootObjects()
    assert len(roots) == 1, "QML 探针没建出来（import FMCL 1.0 或 Theme 不可见？）"
    root = roots[0]
    assert QColor(root.property("themed")).name() == bridge.accent.name()
    assert QColor(root.property("card")).name() == bridge.cardBg.name()
    assert root.property("family") == bridge.fontFamily
    assert root.property("baseFont") == bridge.fontSizeBase
    assert root.property("themeName") == bridge.currentTheme

    bridge.setTheme("ocean")
    assert QColor(root.property("themed")).name() == "#00b4d8", "切主题后 QML 侧没有重算"
    assert root.property("revision") == bridge.revision


def test_register_bridges_keeps_later_qml_compilation_working(tmp_path: Path) -> None:
    """入口注册桥之后，**新编**一个用到 `QtQuick.Controls` 的组件仍要能成功。

    这条守着一个实测出来的平台级缺陷（阶段 2 任务 2.8 发现）：PySide6 6.7.3 下
    任何 `qmlRegisterType` / `qmlRegisterSingletonType` / `qmlRegisterSingletonInstance`
    调用之后，同进程里再编 `StackView` 一类控件会失败，
    报 `Cannot assign object to list property "data"` —— 而入口的顺序是
    "建引擎 → 注册桥 → 加载根组件"，正好每次都踩中。证据矩阵见
    `poc/theme_register_hazard_2_8.py`；`register_bridges` 因此改用
    "`qmlRegisterModule` + `setContextProperty`"。
    """
    from PySide6.QtQml import QQmlComponent

    import main_qml

    engine = QQmlApplicationEngine()
    result = main_qml.register_bridges(engine, None)
    assert "Theme" in result["registered"], f"Theme 没注册成功: {result}"
    assert engine._fmcl_bridges.get("Theme") is not None

    probe = tmp_path / "ControlsProbe.qml"
    probe.write_text("import QtQuick\nimport QtQuick.Controls\nStackView { objectName: \"probe\" }\n", encoding="utf-8")
    component = QQmlComponent(engine, probe.as_uri())
    assert component.isReady(), f"注册桥之后 QtQuick.Controls 编不了了: {[e.toString() for e in component.errors()]}"
