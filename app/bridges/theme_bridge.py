"""主题桥（阶段 2 任务 2.8）—— QML 侧唯一能读/改主题的地方。

注册名 ``Theme``（由 ``main_qml.register_bridges()`` 用
**``qmlRegisterModule("FMCL", 1, 0)`` + ``setContextProperty("Theme", obj)``** 注册），
QML 侧 ``import FMCL 1.0`` 之后按驼峰名读：``Theme.bgDark`` / ``Theme.fontSizeBase``。

> **为什么不是 `qmlRegisterSingletonInstance`**：契约第四节原本写的是它，但实测
> PySide6 6.7.3 下**任何** `qmlRegister*Type/Instance` 调用之后，同进程里再编译用到
> `QtQuick.Controls` 的 QML 就会失败（`qml/App.qml` 的 `StackView` 必现
> `Cannot assign object to list property "data"`）。矩阵见
> ``poc/theme_register_hazard_2_8.py``，结论写在 ``main_qml.register_bridges`` 的文档里。
> **QML 侧的写法一个字都没变**（`Theme.bgDark` / `Tr.map[…]`），改的只是注册机制。

## 数据流（谁说了算）

    services/theme_service.ThemeEngine        ← 业务：预设/用户主题、版本调色、颜色计算
              │ 原地 clear/update
              ▼
    services/palette.COLORS（全进程唯一的可变字典）
              │ 读 + 归一化（本桥）
              ├─► QML 的 12 个 Q_PROPERTY（NOTIFY changed）
              └─► FluentUI 的 FluTheme 九键（阶段 0 第 10.1 节的映射表）

桥里**没有任何业务规则**：版本前缀匹配在 ``ThemeEngine.get_version_accent``、
主题文件解析在 ``ThemeEngine.import_theme_from_file``、变亮算法在
``ThemeEngine._lighten_color``、预设与用户主题的清点在
``ThemeEngine.get_available_themes``。本桥只做三件事：**参数转换、调服务、发信号**。

## 为什么一律用信号（不用 invokeMethod）

阶段 0 第 10.2 节实测：``QMetaObject.invokeMethod`` 调 QML 函数会**静默失败**
（QML 侧声明的是 ``var`` 参数，与 ``QVariantMap`` 签名不匹配，调用计数器始终为 0）。
契约第六节决策 10 把它定成红线，``scripts/check_qml_rules.py`` 的 R7 静态拦截。

## 颜色归一化（缺陷 D-63 / 风险 R-14）

``COLORS`` 的值可能是 ``"#rrggbb"``，也可能是 ``(light, dark)`` 元组
（``docs/refactor/07-known-defects.md`` 的 D-63；基线 ``ui/app_monitor.py:733-737``
就对元组特判过）。QML 的 ``color`` **不接受元组**，所以本桥统一归一化成字符串：

- 元组 / 列表 → 取**第一个**元素（旧界面把元组直接喂给 Tk，Tk 取的是亮色那一项）；
- ``#rgb`` → 交给 ``QColor`` 展开成 ``#rrggbb``；带 alpha 的 ``#aarrggbb`` 保留 alpha；
- 既不是颜色也不是元组（``None`` / 数字 / 乱写的串）→ 回退 ``#000000`` 并记 warning，
  **不抛异常** —— 一个坏色值不该让界面起不来。

## FluTheme 注入与优雅降级

- 取法：``engine.singletonInstance("FluentUI", "FluTheme")``（阶段 0 第 10.1 节实测可用）；
  ``poc/theme_probe_2_8.py`` 另测出**不必等 ``engine.load()``**，所以桥在构造期就能写。
- **先查导入路径再调 ``singletonInstance``**（:func:`_fluent_module_dir`）：模块解析不了时
  它不会优雅返回 ``None``，而是直接崩进程（全量测试里踩到，Windows access violation）。
- 九键映射见 :data:`FLUENT_MAP`；``success`` / ``warning`` / ``error`` 在 FluTheme 里
  没有对应项，只留在我们这边给自研组件用。
- **必须显式设 ``FluTheme.darkMode``**：实测 ``FluTheme.dark`` 只读（恒为 False），
  而 5 个预设主题全是深色（契约第六节决策 9）。
- 取不到 FluTheme 时**只记 warning 并继续**：``fluentAvailable`` 变 False，界面照常起来。
  注意 ``singletonInstance`` 在模块不可用时返回的是 **undefined 的 QJSValue** 而不是 None
  （``poc/theme_probe_engine_ref.py`` 实测），所以判据是"是不是 QObject"。

## 引擎从哪来（契约没规定注入方式，这里说清楚）

``main_qml.register_bridges()`` 用 ``cls()`` **无参**实例化桥，而 ``singletonInstance``
需要一个 ``QQmlEngine``。查找顺序：

1. 构造参数 ``engine=``（调用方/测试显式注入，最确定）；
2. 自动发现：``gc.get_objects()`` 里的 ``QQmlEngine`` 实例（实测能捞到入口建的那个；
   ``QCoreApplication.instance().children()`` 里**没有** —— 引擎构造时没给 parent）；
3. 都找不到 → 记一次 warning，之后每次同步重试（引擎可能晚于桥出现）。

引擎用**弱引用**持有（``weakref.ref``）：桥不该拖住引擎的析构。

## 设计令牌（契约第六节决策 11：放在 Theme 里）

取值是从 ``ui/`` 量出来的，不是拍脑袋 —— ``poc/measure_design_tokens.py`` 可复跑：

+-------------------+----+---------------------------------------------------------+
| 令牌              | 值 | 依据（``ui/**/*.py`` 递归统计的实测分布）                |
+===================+====+=========================================================+
| ``fontSizeSmall`` | 10 | ``CTkFont(size=10)`` 89 处（小标签 / 次要说明）          |
| ``fontSizeBase``  | 12 | 243 处，绝对主力（正文）                                 |
| ``fontSizeLarge`` | 14 | 69 处（强调正文 / 卡片小标题）                           |
| ``fontSizeTitle`` | 18 | ``size=18, weight="bold"`` 13 处（窗口 / 对话框标题）；  |
|                   |    | 16 bold（24 处，小节标题）取最接近的档位并入这一档       |
| ``spacingXs``     | 5  | ``padx/pady=5`` 52 处（紧凑内间距）                      |
| ``spacingSm``     | 10 | 98 处                                                    |
| ``spacingMd``     | 15 | 126 处，最高频                                           |
| ``spacingLg``     | 20 | 57 处（分区间距）                                        |
| ``spacingXl``     | 30 | 14 处（页面级留白 / 空状态）                             |
| ``radiusSm``      | 6  | ``corner_radius=6`` 12 处（列表行）                      |
| ``radiusMd``      | 8  | 40 处（按钮 / 输入框的主力档）                            |
| ``radiusLg``      | 12 | 27 处（卡片 / 面板）                                     |
| ``iconSize``      | 20 | FluentUI ``FluAppBar.qml:34`` 的默认 ``iconSize``；       |
|                   |    | 旧界面的字形图标是 22 / 24，同处一档                      |
+-------------------+----+---------------------------------------------------------+

字号单位：CTk 的 ``size`` 与 QML 的 ``font.pixelSize`` 都是像素，**1:1 沿用**，
不做 pt→px 换算。旧界面里 11 / 13 也用得多（204 / 155 处），但它们紧贴 12，
按"取最接近的档位"并入 Base；16（24 处）并入 Title。

## 持久化

``setTheme`` / ``setAccent`` 与旧界面 ``launcher/core.py:2660-2680`` 的
``set_theme_name`` / ``set_accent_color`` 对齐：写 ``config.theme_name`` /
``config.accent_color`` 并 ``save_config()`` —— 不写的话重启后主题会丢。
配置对象可以通过构造参数 ``config=`` 注入：测试传一个假对象就**既不读也不写**
真实的 ``config.json``（不给它时懒取根模块的 ``config`` 单例）。
"""

from __future__ import annotations

import gc
import logging
import weakref
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PySide6.QtCore import Property, QCoreApplication, QObject, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtQml import QQmlEngine

from services.font_service import FONT_FAMILY

logger = logging.getLogger(__name__)

#: 契约第四节冻结的 QML 注册名（由入口 `register_bridges` 注册成上下文属性）。
QML_NAME = "Theme"

#: QML 属性名（驼峰）→ `services.palette.COLORS` 的键（下划线）。顺序按契约 §5.1。
COLOR_KEYS: Tuple[Tuple[str, str], ...] = (
    ("bgDark", "bg_dark"),
    ("bgMedium", "bg_medium"),
    ("bgLight", "bg_light"),
    ("accent", "accent"),
    ("accentHover", "accent_hover"),
    ("success", "success"),
    ("warning", "warning"),
    ("error", "error"),
    ("textPrimary", "text_primary"),
    ("textSecondary", "text_secondary"),
    ("cardBg", "card_bg"),
    ("cardBorder", "card_border"),
)

#: `COLORS` 的键（下划线）→ QML 属性名（驼峰）。九键映射与 FluTheme 写入都要它。
SNAKE_TO_CAMEL: Dict[str, str] = {snake: camel for camel, snake in COLOR_KEYS}

#: 我们的键 → FluTheme 属性（阶段 0 第 10.1 节的九键映射表）。
#: `success` / `warning` / `error` 无对应项，只留在我们这边。
FLUENT_MAP: Tuple[Tuple[str, str], ...] = (
    ("accent", "primaryColor"),
    ("accent_hover", "itemHoverColor"),
    ("bg_dark", "windowBackgroundColor"),
    ("bg_medium", "backgroundColor"),
    ("bg_light", "itemNormalColor"),
    ("card_bg", "frameColor"),
    ("card_border", "dividerColor"),
    ("text_primary", "fontPrimaryColor"),
    ("text_secondary", "fontSecondaryColor"),
)

#: FluTheme 的暗色模式值（Qt::Dark）。`dark` 只读、`darkMode` 可写（阶段 0 第 10.1 节）。
FLUENT_DARK = 1

#: 归一化失败时的兜底色。
FALLBACK_COLOR = "#000000"

#: FluTheme 的模块 URI 与类型名（FluentUI 自编译插件提供）。
FLUENT_URI = "FluentUI"
FLUENT_THEME_TYPE = "FluTheme"

#: 默认主题名（`ThemeEngine._load_preset_themes()` 里的第一项）。
DEFAULT_THEME = "default"

#: 设计令牌（依据见模块文档的表格；这些是**常量**，QML 侧 constant=True 只读一次）。
FONT_SIZE_SMALL = 10
FONT_SIZE_BASE = 12
FONT_SIZE_LARGE = 14
FONT_SIZE_TITLE = 18
SPACING_XS = 5
SPACING_SM = 10
SPACING_MD = 15
SPACING_LG = 20
SPACING_XL = 30
RADIUS_SM = 6
RADIUS_MD = 8
RADIUS_LG = 12
ICON_SIZE = 20


def _coerce_color(value: Any) -> Optional[QColor]:
    """把值折成一个 QColor；折不出来返回 None（调用方负责回退与告警）。

    元组 / 列表按 D-63 的约定取**第一个**元素，并递归下去
    （``((light, dark), ...)`` 这种畸形值也能折到底或明确失败）。
    """
    if isinstance(value, QColor):
        return value if value.isValid() else None
    if isinstance(value, (tuple, list)):
        if not value:
            return None
        return _coerce_color(value[0])
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        color = QColor(text)
        return color if color.isValid() else None
    return None


def normalize_color(value: Any, key: str = "") -> str:
    """把 ``COLORS`` 里的一个值归一化成 QML 能用的颜色字符串。

    Args:
        value: ``"#rrggbb"`` / ``"#rgb"`` / ``"#aarrggbb"`` / ``(light, dark)`` 元组 / 其它。
        key: 出问题时写进日志的键名（便于定位是哪个主题色坏了）。

    Returns:
        归一化后的颜色字符串；无法识别时返回 :data:`FALLBACK_COLOR`。

    Examples:
        >>> normalize_color("#E94560")
        '#e94560'
        >>> normalize_color(("#ffffff", "#000000"))
        '#ffffff'
        >>> normalize_color(None)
        '#000000'
    """
    color = _coerce_color(value)
    if color is None:
        logger.warning(
            "主题色 %s 的值 %r 不是颜色（既非颜色串也非 (light, dark) 元组），回退 %s",
            key or "?",
            value,
            FALLBACK_COLOR,
        )
        return FALLBACK_COLOR
    if color.alpha() != 255:
        return color.name(QColor.NameFormat.HexArgb)
    return color.name()


def is_hex_color(text: str) -> bool:
    """`#rrggbb` 形态校验。

    判据与旧界面 `ui/windows/launcher_settings.py:1087-1094` 的
    `_on_accent_apply` 逐条一致：`#` 开头、长度 7、后 6 位能按十六进制解析。
    """
    if len(text) != 7 or not text.startswith("#"):
        return False
    try:
        int(text[1:], 16)
    except ValueError:
        return False
    return True


def _fluent_module_dir(engine: Any) -> Optional[Path]:
    """在引擎的导入路径里找 FluentUI 模块目录（必须带 `qmldir`）。

    **这不是"保险"，是硬前置条件**：`singletonInstance()` 在**解析不了该模块**的引擎上
    不会老老实实返回 `None` 让你降级 —— 全量测试里实测**整个进程 access violation**
    （栈在 Qt 内部，Python 侧 `try/except` 兜不住）。触发条件很隐蔽：只要**同进程里
    另一个引擎**先加载过 FluentUI（例如测试建过一个真的 `FluWindow`），插件就被
    进程级注册了，此后拿一个没挂导入路径的引擎去 `singletonInstance` 就崩。
    先查导入路径再调，等于把那条路径彻底封死。
    """
    try:
        paths = list(engine.importPathList())
    except Exception as e:  # noqa: BLE001 - 测试替身 / 拿不到路径都按"没有模块"处理
        logger.debug("取引擎导入路径失败（%s），按没有 FluentUI 模块处理", e)
        return None
    for raw in paths:
        if not raw or raw.startswith("qrc:"):
            continue
        candidate = Path(raw) / FLUENT_URI
        if (candidate / "qmldir").is_file():
            return candidate
    return None


class ThemeBridge(QObject):
    """上下文属性……不，**单例** `Theme`：12 个色键 + 设计令牌 + 主题操作。

    线程前提：与其它桥一样**只许在主线程**使用（QML 绑定与槽都在主线程），
    本桥不起线程、不接收 worker 回调（契约红线 3）。
    """

    #: 12 个色键与 `revision` 共同的 NOTIFY。QML 侧只要读任意色键就有依赖，
    #: 需要"任意变化都重算"的绑定读 `revision`。
    changed = Signal()
    #: 当前主题名变了（切换成功 / 导入后应用）。`currentTheme` 的 NOTIFY。
    themeChanged = Signal(str)
    #: 可用主题列表变了（导入了新主题）。`themes` / `availableThemes()` 的 NOTIFY。
    themesChanged = Signal()
    #: 操作失败的人话原因（未知主题、颜色格式不对、主题文件非法……）。
    #: 用信号而不是返回值：契约 §5.1 冻结了 `@Slot(str)` 的形态（无返回值），
    #: 而 QML 侧要能把失败提示出来（决策 10：一律用信号）。
    themeFailed = Signal(str)

    def __init__(
        self,
        engine: Optional[Any] = None,
        theme_engine: Optional[Any] = None,
        config: Optional[Any] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        """
        Args:
            engine: QML 引擎（取 FluTheme 用）。``None`` 时自动发现（见模块文档）。
            theme_engine: ``services.theme_service.ThemeEngine``。``None`` 时懒取；
                进程里没初始化过就按装配职责补一次 ``init_theme_engine``。
            config: 配置对象（读 ``theme_name`` / ``accent_color``，写回时调
                ``save_config()``）。``None`` → 懒取根模块的 ``config`` 单例；
                测试传一个假对象即可**完全隔离**（既不读也不写真实 config.json）。
            parent: Qt 父对象。
        """
        super().__init__(parent)
        self._engine = engine
        self._engine_ref: Optional[weakref.ReferenceType] = None
        self._theme_engine = theme_engine
        self._config = config
        self._config_checked = config is not None
        self._colors: Dict[str, str] = {}
        self._revision = 0
        self._fluent: Optional[QObject] = None
        self._fluent_warned = False
        self._theme_name = str(self._config_value("theme_name", DEFAULT_THEME) or DEFAULT_THEME)
        self._accent = self._config_value("accent_color", None)
        self._apply_initial_theme()

    # ─── 12 个色键（契约 §5.1 冻结的名字与顺序） ──────────────

    def _color(self, camel: str) -> QColor:
        return QColor(self._colors.get(camel, FALLBACK_COLOR))

    @Property(QColor, notify=changed)
    def bgDark(self) -> QColor:  # noqa: N802 - QML 属性名
        return self._color("bgDark")

    @Property(QColor, notify=changed)
    def bgMedium(self) -> QColor:  # noqa: N802
        return self._color("bgMedium")

    @Property(QColor, notify=changed)
    def bgLight(self) -> QColor:  # noqa: N802
        return self._color("bgLight")

    @Property(QColor, notify=changed)
    def accent(self) -> QColor:
        return self._color("accent")

    @Property(QColor, notify=changed)
    def accentHover(self) -> QColor:  # noqa: N802
        return self._color("accentHover")

    @Property(QColor, notify=changed)
    def success(self) -> QColor:
        return self._color("success")

    @Property(QColor, notify=changed)
    def warning(self) -> QColor:
        return self._color("warning")

    @Property(QColor, notify=changed)
    def error(self) -> QColor:
        return self._color("error")

    @Property(QColor, notify=changed)
    def textPrimary(self) -> QColor:  # noqa: N802
        return self._color("textPrimary")

    @Property(QColor, notify=changed)
    def textSecondary(self) -> QColor:  # noqa: N802
        return self._color("textSecondary")

    @Property(QColor, notify=changed)
    def cardBg(self) -> QColor:  # noqa: N802
        return self._color("cardBg")

    @Property(QColor, notify=changed)
    def cardBorder(self) -> QColor:  # noqa: N802
        return self._color("cardBorder")

    # ─── 调色板版本号与主题信息 ─────────────────────────────

    @Property(int, notify=changed)
    def revision(self) -> int:
        """整份调色板的版本号 —— 给"任意变化都重算"的绑定用。

        与 `Tr.revision` 同一个理由（契约第六节决策 1）：QML 只跟踪**属性读取**，
        不跟踪函数返回值；`setTheme` / `setAccent` / `applyVersionTheme` 都会自增它。
        """
        return self._revision

    @Property(str, notify=themeChanged)
    def currentTheme(self) -> str:
        """当前主题名（设置页的下拉框靠它回显）。"""
        return self._theme_name

    @Property("QVariantList", notify=themesChanged)
    def themes(self) -> List[Dict[str, Any]]:
        """可用主题列表（预设 + 用户导入），与 :meth:`availableThemes` 同一份数据。

        **为什么槽之外还要一个属性**：QML 不跟踪函数返回值，`model: Theme.availableThemes()`
        在导入新主题后**不会重算**（契约第六节决策 1 的同一条理由）。
        绑定请用 `Theme.themes`，命令式调用请用 `Theme.availableThemes()`。
        """
        return self.availableThemes()

    @Property(bool, notify=changed)
    def fluentAvailable(self) -> bool:
        """FluentUI 的 `FluTheme` 是否已接上（False = 九键注入降级了，界面照常可用）。"""
        return self._fluent is not None

    # ─── 设计令牌（constant=True：进程内不变，QML 只读一次） ──

    @Property(str, constant=True)
    def fontFamily(self) -> str:
        """中文字体族名（``services/font_service.FONT_FAMILY``，惰性检测）。

        `FONT_FAMILY` 是 `LazyStr`：首次读这个属性时才跑检测并缓存
        （Windows 直接返回 "Microsoft YaHei"，不跑 subprocess）。
        Linux 上它可能是 `"Noto Color Emoji, Noto Sans CJK SC"` 这样的**组合链**
        （旧实现在 Linux 上就这么返回的）；QML 的 `font.family` 只认单一家族名，
        那种情况下要取第一个逗号前的那段 —— 本属性**原样**透出，不改语义。
        """
        return str(FONT_FAMILY)

    @Property(int, constant=True)
    def fontSizeSmall(self) -> int:  # noqa: N802
        """小标签 / 次要说明（`CTkFont(size=10)`，89 处）。"""
        return FONT_SIZE_SMALL

    @Property(int, constant=True)
    def fontSizeBase(self) -> int:  # noqa: N802
        """正文主力（`size=12`，243 处）。"""
        return FONT_SIZE_BASE

    @Property(int, constant=True)
    def fontSizeLarge(self) -> int:  # noqa: N802
        """强调正文 / 卡片小标题（`size=14`，69 处）。"""
        return FONT_SIZE_LARGE

    @Property(int, constant=True)
    def fontSizeTitle(self) -> int:  # noqa: N802
        """窗口 / 对话框标题（`size=18, weight="bold"`，13 处）。"""
        return FONT_SIZE_TITLE

    @Property(int, constant=True)
    def spacingXs(self) -> int:  # noqa: N802
        """紧凑内间距（`padx/pady=5`，52 处）。"""
        return SPACING_XS

    @Property(int, constant=True)
    def spacingSm(self) -> int:  # noqa: N802
        """控件内间距（10，98 处）。"""
        return SPACING_SM

    @Property(int, constant=True)
    def spacingMd(self) -> int:  # noqa: N802
        """控件间标准间距（15，126 处，旧界面最高频）。"""
        return SPACING_MD

    @Property(int, constant=True)
    def spacingLg(self) -> int:  # noqa: N802
        """分区间距（20，57 处）。"""
        return SPACING_LG

    @Property(int, constant=True)
    def spacingXl(self) -> int:  # noqa: N802
        """页面级留白 / 空状态（30，14 处）。"""
        return SPACING_XL

    @Property(int, constant=True)
    def radiusSm(self) -> int:  # noqa: N802
        """列表行 / 小标签圆角（`corner_radius=6`，12 处）。"""
        return RADIUS_SM

    @Property(int, constant=True)
    def radiusMd(self) -> int:  # noqa: N802
        """按钮 / 输入框圆角（8，40 处）。"""
        return RADIUS_MD

    @Property(int, constant=True)
    def radiusLg(self) -> int:  # noqa: N802
        """卡片 / 面板圆角（12，27 处）。"""
        return RADIUS_LG

    @Property(int, constant=True)
    def iconSize(self) -> int:  # noqa: N802
        """控件内图标边长（20；FluentUI `FluAppBar` 的默认值）。"""
        return ICON_SIZE

    # ─── 主题操作（契约 §5.1 冻结的形态） ───────────────────

    @Slot()
    def refresh(self) -> None:
        """重新读一遍 `services.palette.COLORS` 并（有变化时）发 `changed`。

        给"别人直接改了全局字典"的场景用：`COLORS` 是全进程唯一的可变字典，
        `ThemeEngine` 之外也可能有人原地 update（旧的 `_reapply_theme` 就是这么干的）。
        顺带重试一次 FluTheme 注入 —— 引擎可能在桥构造之后才建好。
        """
        fresh = self._read_colors()
        if fresh != self._colors:
            self._colors = fresh
            self._bump()
        self._apply_to_fluent()

    @Slot(str)
    def setTheme(self, name: str) -> None:
        """切换主题（预设名或用户导入的主题名）。失败发 :attr:`themeFailed`。

        业务全在服务里：`load_theme` 查预设与用户目录、`apply_theme` 原地写全局
        `COLORS`。本槽只负责"当前主题名"的簿记与持久化（与旧的
        `launcher/core.py:2660 set_theme_name` 对齐）。
        """
        engine = self._ensure_theme_engine()
        text = str(name or "").strip()
        if engine is None:
            self._fail(f"主题引擎不可用，无法切换到「{text}」")
            return
        theme = engine.load_theme(text)
        if theme is None:
            self._fail(f"未知主题: {text}")
            return
        engine.apply_theme(theme, self._accent)
        self._theme_name = str(getattr(theme, "name", text) or text)
        self._persist_value("theme_name", self._theme_name)
        # 先同步调色板再发 themeChanged：否则 QML 的 onThemeChanged 处理器里读 Theme.accent
        # 拿到的是**旧值**（`_colors` 还没刷新），这种"信号到了数据还没到"最难查。
        self._sync()
        self.themeChanged.emit(self._theme_name)

    @Slot(str)
    def setAccent(self, hex_color: str) -> None:
        """设置自定义强调色；传空串表示**清除**（回到当前主题自带的强调色）。

        `accent_hover` 由服务里的 `ThemeEngine._lighten_color(accent, 0.3)` 算出来，
        桥不自己算颜色。格式校验是**参数转换**（旧界面 `_on_accent_apply` 的判据：
        `#` + 6 位十六进制），不合法就发 `themeFailed` 并保持原状。
        """
        engine = self._ensure_theme_engine()
        text = str(hex_color or "").strip()
        if engine is None:
            self._fail("主题引擎不可用，无法设置强调色")
            return
        if text and not is_hex_color(text):
            self._fail(f"强调色格式不对: {text}（应为 #rrggbb）")
            return
        theme = engine.load_theme(self._theme_name)
        if theme is None:
            self._fail(f"当前主题「{self._theme_name}」已不可用，无法设置强调色")
            return
        accent = text or None
        engine.apply_theme(theme, accent)
        self._accent = accent
        self._persist_value("accent_color", accent)
        self._sync()

    @Slot(str, result=bool)
    def importTheme(self, path: str) -> bool:
        """从 .json 文件导入主题。返回是否成功（失败原因见 `themeFailed`）。

        解析、校验、落盘全在 `ThemeEngine.import_theme_from_file`（服务层）里；
        本槽**不自动应用**导入的主题 —— 与旧界面 `_on_import_theme` 一致
        （导入只刷新列表，用户在列表里选中才应用）。
        """
        engine = self._ensure_theme_engine()
        if engine is None:
            self._fail("主题引擎不可用，无法导入主题")
            return False
        try:
            ok, message = engine.import_theme_from_file(str(path or ""))
        except Exception as e:  # noqa: BLE001 - 服务抛异常也不能崩在桥上
            self._fail(f"导入主题失败: {e}")
            return False
        if not ok:
            self._fail(str(message))
            return False
        logger.info("主题导入成功: %s", message)
        self.themesChanged.emit()
        return True

    @Slot(result="QVariantList")
    def availableThemes(self) -> List[Dict[str, Any]]:
        """可用主题列表（预设 + 用户导入）。

        每项是 `ThemeEngine.get_available_themes()` 原样返回的字典：
        `name` / `author` / `description` / `version` / `colors` /
        `source`（`"preset"` 或 `"user"`，用户主题另有 `file_path`）。

        绑定请用属性 `Theme.themes`（QML 不跟踪函数返回值）；本槽给命令式调用用。
        """
        engine = self._ensure_theme_engine()
        if engine is None:
            return []
        try:
            return [dict(item) for item in engine.get_available_themes()]
        except Exception as e:  # noqa: BLE001 - 读列表失败不该让设置页崩掉
            logger.warning("读取可用主题失败: %s", e)
            return []

    @Slot(str, result="QVariantMap")
    def versionAccent(self, versionId: str) -> Dict[str, Any]:
        """该 Minecraft 版本对应的动态强调色。

        返回 `accent` / `accent_hover` / `description`；不命中返回**空 map**
        （15 个版本的前缀匹配表在 `ThemeEngine.VERSION_COLOR_MAP`，业务不在桥上）。
        """
        engine = self._ensure_theme_engine()
        if engine is None:
            return {}
        info = engine.get_version_accent(str(versionId or "").strip())
        return dict(info) if info else {}

    @Slot(str)
    def applyVersionTheme(self, versionId: str) -> None:
        """把该版本的动态强调色应用到当前主题上（版本动态调色）。不持久化。

        与旧的 `launcher/core.py:2691 apply_version_theme` 一致的落点：强调色换成
        版本色、`accent_hover` 由服务的 `_lighten_color` 重算。
        **不读 `config.dynamic_version_theme`**：那个开关是"要不要自动跟随版本"的
        产品策略，属于调用方（阶段 3 的页面/设置），本槽只做"把这个版本应用上去"。
        """
        engine = self._ensure_theme_engine()
        text = str(versionId or "").strip()
        if engine is None:
            self._fail("主题引擎不可用，无法应用版本主题")
            return
        colors = engine.apply_version_theme(text)
        if not colors:
            self._fail(f"版本 {text} 没有对应的动态主题")
            return
        theme = engine.load_theme(self._theme_name) or engine.load_theme(DEFAULT_THEME)
        if theme is None:
            self._fail(f"当前主题「{self._theme_name}」不可用，无法应用版本主题")
            return
        engine.apply_theme(theme, colors.get("accent"))
        self._sync()

    # ─── 内部：读色 / 同步 / 引擎 / FluTheme ─────────────────

    def _read_colors(self) -> Dict[str, str]:
        """从 `services.palette.COLORS` 读 12 键并逐个归一化。

        每次现读模块属性（而不是 `from … import COLORS` 绑一份），
        这样"字典被换成另一个对象"的极端情况也能跟上。
        """
        from services import palette

        colors = getattr(palette, "COLORS", None) or {}
        return {camel: normalize_color(colors.get(snake), snake) for camel, snake in COLOR_KEYS}

    def _bump(self) -> None:
        """自增 revision 并发 `changed`（12 个色键 + revision 共用的 NOTIFY）。"""
        self._revision += 1
        self.changed.emit()

    def _sync(self) -> None:
        """服务改完 `COLORS` 之后的统一收尾：读属性 → 写 FluTheme → 通知 QML。

        三个动作**必须成对出现**：一开始只写了前两步里的"读 + 发信号"，
        结果切主题时 FluTheme 没跟着变（FluentUI 控件不跟随）—— 被
        `test_fluent_theme_gets_the_nine_keys_and_dark_mode` 抓到。
        """
        self._colors = self._read_colors()
        self._apply_to_fluent()
        self._bump()

    def _fail(self, reason: str) -> None:
        logger.warning("主题操作失败: %s", reason)
        self.themeFailed.emit(reason)

    def _apply_initial_theme(self) -> None:
        """构造期：把 `config` 里记着的主题应用上去，再读一遍色。

        与旧界面 `launcher/core.py:163-166` 的四行**语义一致**：这是**装配**不是业务
        —— 用哪个主题由 config 说，怎么应用由服务做。引擎不可用时跳过，
        直接用 `COLORS` 里现有的值（模块默认色），界面照常起来。
        """
        engine = self._ensure_theme_engine()
        if engine is not None:
            try:
                theme = engine.load_theme(self._theme_name)
                if theme is None and self._theme_name != DEFAULT_THEME:
                    logger.warning("配置里的主题「%s」已不可用，回退 %s", self._theme_name, DEFAULT_THEME)
                    self._theme_name = DEFAULT_THEME
                    theme = engine.load_theme(DEFAULT_THEME)
                if theme is not None:
                    engine.apply_theme(theme, self._accent)
            except Exception as e:  # noqa: BLE001 - 主题坏了也必须把界面起起来
                logger.warning("应用启动主题失败（继续用现有调色板）: %s", e)
        self._colors = self._read_colors()
        self._apply_to_fluent()

    def _ensure_theme_engine(self) -> Optional[Any]:
        """取主题引擎；进程里没初始化过就按装配职责补一次 `init_theme_engine`。

        阶段 2 的装配链（`main_qml.build_qt_context` → `app.bootstrap`）里没有主题
        这一类服务，而 `ThemeEngine` 的预设/用户主题目录需要 `base_dir`
        （`services/theme_service.py:178`）—— 与旧入口 `launcher/core.py:163` 同一处调用。
        """
        if self._theme_engine is not None:
            return self._theme_engine
        from services.theme_service import get_theme_engine, init_theme_engine

        try:
            self._theme_engine = get_theme_engine()
            return self._theme_engine
        except Exception:  # noqa: BLE001 - 未初始化（RuntimeError）就走下面的补初始化
            pass
        try:
            from config import config

            self._theme_engine = init_theme_engine(str(config.base_dir))
            logger.info("主题引擎此前未初始化，已按启动流程补一次 init_theme_engine")
            return self._theme_engine
        except Exception as e:  # noqa: BLE001 - 拿不到就整体降级成"只读 COLORS"
            logger.warning("主题引擎不可用（%s），主题切换功能降级", e)
            return None

    def use_engine(self, engine: Any) -> None:
        """由装配方**显式**注入 QML 引擎（`main_qml.register_bridges` 会调）。

        为什么要有这条显式通道（任务 2.10 实测踩到）：自动发现是"在 `gc.get_objects()`
        里找进程里最后建的那个 `QQmlEngine`"，于是**任何在入口装配之前创建过 QML 引擎的
        代码**（例如某个测试里的 QML 探针）都会让它把 `FluTheme` 注到**错的引擎**上 ——
        症状是入口的 `App.qml` 起不来，报一串跟主题毫无关系的 QML error。
        显式注入之后，`gc` 扫描只在没人调用本方法时才作为兜底。
        """
        if engine is None:
            return
        self._engine = engine
        self._engine_ref = None

    def _current_engine(self) -> Optional[Any]:
        """显式注入 → 缓存的弱引用 → 全堆扫描（顺序见模块文档）。"""
        if self._engine is not None:
            return self._engine
        if self._engine_ref is not None:
            found = self._engine_ref()
            if found is not None:
                return found
            self._engine_ref = None
        discovered = self._discover_engine()
        if discovered is None:
            return None
        logger.debug("ThemeBridge 没拿到显式注入的引擎，退回 gc 扫描发现的那个")
        try:
            self._engine_ref = weakref.ref(discovered)
        except TypeError:  # 极少数包装类型不支持弱引用 → 退化成强引用
            self._engine = discovered
        return discovered

    @staticmethod
    def _discover_engine() -> Optional[Any]:
        """在进程里找 `QQmlEngine`。

        为什么不用 `QCoreApplication.instance().children()`：入口是
        `QQmlApplicationEngine()` **无 parent** 建的（`main_qml.build_engine`），
        所以那里一个都没有（`poc/theme_probe_2_8.py` 实测：`app.children()` 里 0 个，
        `gc.get_objects()` 里 1 个且正是入口那个）。先查 children 只是留个万一。
        """
        app = QCoreApplication.instance()
        if app is not None:
            children = [child for child in app.children() if isinstance(child, QQmlEngine)]
            if children:
                return children[-1]
        try:
            found = [obj for obj in gc.get_objects() if isinstance(obj, QQmlEngine)]
        except Exception as e:  # noqa: BLE001 - 扫描失败按"没有引擎"处理
            logger.debug("扫描 QQmlEngine 失败: %s", e)
            return None
        if not found:
            return None
        if len(found) > 1:
            logger.warning("进程里有 %d 个 QQmlEngine，取最后一个用于 FluTheme 注入", len(found))
        return found[-1]

    def _resolve_fluent(self) -> Optional[QObject]:
        """取 FluTheme 单例（取到就缓存）。"""
        if self._fluent is not None:
            return self._fluent
        engine = self._current_engine()
        if engine is None:
            self._warn_fluent("进程里找不到 QQmlEngine")
            return None
        if _fluent_module_dir(engine) is None:
            self._warn_fluent("引擎的导入路径里没有 FluentUI 模块（先跑 scripts/build_fluentui.ps1）")
            return None
        try:
            candidate = engine.singletonInstance(FLUENT_URI, FLUENT_THEME_TYPE)
        except Exception as e:  # noqa: BLE001
            self._warn_fluent(f"singletonInstance 抛异常（{e}）")
            return None
        if not isinstance(candidate, QObject):
            # 模块不可用时拿到的是 undefined 的 QJSValue，而不是 None（实测）
            self._warn_fluent(f"FluTheme 不可用（singletonInstance 返回 {type(candidate).__name__}）")
            return None
        self._fluent = candidate
        logger.info("FluTheme 已接上：注入九键 + 显式 darkMode=%s", FLUENT_DARK)
        return candidate

    def _warn_fluent(self, reason: str) -> None:
        """降级告警**只记一次**（每次同步都重试，但别把日志刷爆）。"""
        if self._fluent_warned:
            return
        self._fluent_warned = True
        logger.warning(
            "FluTheme 取不到（%s）—— 只影响 FluentUI 控件的观感，我们自己的绑定照常工作；后续每次同步都会重试", reason
        )

    def _apply_to_fluent(self) -> bool:
        """写 FluTheme 的九键 + 显式 `darkMode`。取不到时降级并返回 False。"""
        theme = self._resolve_fluent()
        if theme is None:
            return False
        try:
            for snake, prop in FLUENT_MAP:
                theme.setProperty(prop, QColor(self._colors.get(SNAKE_TO_CAMEL[snake], FALLBACK_COLOR)))
            theme.setProperty("darkMode", FLUENT_DARK)
        except Exception as e:  # noqa: BLE001 - C++ 侧对象可能已随引擎析构
            logger.warning("写入 FluTheme 失败（本次降级，下次同步重试）: %s", e)
            self._fluent = None
            return False
        return True

    # ─── 内部：config 读写（持久化，与旧界面语义一致） ────────

    def _config_obj(self) -> Optional[Any]:
        """配置对象：注入的优先，否则懒取根模块的 ``config`` 单例（取不到就整体跳过）。"""
        if self._config is None and not self._config_checked:
            self._config_checked = True
            try:
                from config import config as root_config

                self._config = root_config
            except Exception as e:  # noqa: BLE001 - 没有 config（独立进程/测试）也要能用
                logger.debug("没有可用的 config 单例（%s），主题设置不持久化", e)
        return self._config

    def _config_value(self, name: str, default: Any) -> Any:
        obj = self._config_obj()
        if obj is None:
            return default
        return getattr(obj, name, default)

    def _persist_value(self, name: str, value: Any) -> None:
        """写回 config 并落盘；失败只记 warning —— 主题已经生效，别让保存失败回滚观感。"""
        obj = self._config_obj()
        if obj is None:
            return
        try:
            setattr(obj, name, value)
            save = getattr(obj, "save_config", None)
            if callable(save):
                save()
        except Exception as e:  # noqa: BLE001
            logger.warning("保存主题设置 %s=%r 失败: %s", name, value, e)


__all__ = [
    "COLOR_KEYS",
    "DEFAULT_THEME",
    "FALLBACK_COLOR",
    "FLUENT_DARK",
    "FLUENT_MAP",
    "FLUENT_THEME_TYPE",
    "FLUENT_URI",
    "QML_NAME",
    "ThemeBridge",
    "is_hex_color",
    "normalize_color",
]
