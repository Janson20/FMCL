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
- 映射见 :data:`FLUENT_MAP`；``success`` / ``warning`` / ``error`` 在 FluTheme 里
  没有对应项，只留在我们这边给自研组件用。
- **必须显式设 ``FluTheme.darkMode``**：实测 ``FluTheme.dark`` 只读（外部改不了），
  而 5 个预设主题全是深色（契约第六节决策 9）。
- 取不到 FluTheme 时**只记 warning 并继续**：``fluentAvailable`` 变 False，界面照常起来。
  注意 ``singletonInstance`` 在模块不可用时返回的是 **undefined 的 QJSValue** 而不是 None
  （``poc/theme_probe_engine_ref.py`` 实测），所以判据是"是不是 QObject"。

## 界面返工 A 组修掉的两个真缺陷（缺陷 D-139 / D-140）

### D-139：``darkMode`` 的取值语义搞错了（``FLUENT_DARK`` 曾经是 1）

FluentUI **不用** ``Qt::ColorScheme`` —— 它自己的枚举在
``third_party/FluentUI/src/Def.h``：

    namespace FluThemeType { enum DarkMode { System = 0x0000, Light = 0x0001, Dark = 0x0002 }; }

所以旧值 ``1`` 是 **Light**。实测（``poc/_probe_fluent_darkmode.py``，本机复跑过）：

    setProperty("darkMode", 0) → dark=True   windowBg=#202020  frameActive=#303030   （System，本机系统是深色）
    setProperty("darkMode", 1) → dark=False  windowBg=#ededed  frameActive=#ffffff   （Light）
    setProperty("darkMode", 2) → dark=True   windowBg=#202020  frameActive=#303030   （Dark）

症状就是用户截图里那条**白色的窗口顶栏**：FluentUI 控件（FluWindow 的 appBar、
FluButton、原生 TextField）全是浅色，而我们自己的壳层读的是深色调色板。

### D-140：写 ``darkMode`` 会把刚写进去的颜色**全部冲掉** —— 所以写序有硬要求

``FluTheme.cpp`` 里两条直连信号：

    connect(this, &FluTheme::darkModeChanged, this, [=] { Q_EMIT darkChanged(); });
    connect(this, &FluTheme::darkChanged, this, [=] { refreshColors(); });

而 ``refreshColors()`` 会把**全部 14 个颜色**重置成 FluentUI 的默认值。实测：
Light→Dark 切一次，刚写进去的 ``windowBackgroundColor=#1a1a2e`` 变回 ``#202020``。

反过来，"同一个值再写一次"**不会**触发（``Q_PROPERTY_AUTO`` 宏里有
``if (_##M != in_##M)`` 判断，``third_party/FluentUI/src/stdafx.h``），
所以这个坑只在 darkMode **真的变化**的那一次出现 —— 也就是修好 D-139 的第一次同步。

**结论：先写 ``darkMode``，再写颜色。** 顺序被 ``test_dark_mode_is_written_before_colors``
钉住（把两行调过来该用例立刻变红）。

## 语义令牌（返工 A 组新增）

QML 侧**不许自己算颜色**（闸门 R8 禁颜色字面量，``Qt.rgba`` 也在禁列），
所以"比卡片亮 6% 的悬停色"这类派生值必须由本桥算好再暴露成属性。
派生只依赖 :data:`COLOR_KEYS` 那 12 个键，因此：

* **不改** `services.palette.COLORS` 的 12 键契约（主题文件格式一个字都没变，老主题照常能用）；
* 用户导入的主题只要定义那 12 键，派生令牌自动跟着走。

派生表与理由见 :func:`derive_tokens`。锁深色是契约第六节决策 9 的延续
（5 个预设主题全是深色），所以 ``itemHover`` / ``scrim`` 这类叠加色直接按
"白 + alpha"给（浅色主题需要反过来，那是"支持亮色"时的事，见 ``05`` 的遗留项）。

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

#: `COLORS` 的键（下划线）→ QML 属性名（驼峰）。
#: 返工 A 组之后，FluTheme 映射不再用它（映射表的左边已经是驼峰令牌名），
#: 但"蛇形键 ↔ 驼峰属性"的对照关系仍是主题文件与 QML 之间的桥，留着给诊断与测试用。
SNAKE_TO_CAMEL: Dict[str, str] = {snake: camel for camel, snake in COLOR_KEYS}

#: 我们的键 → FluTheme 属性。**左侧是"令牌名"**：既能是 :data:`COLOR_KEYS` 里的 12 个驼峰名，
#: 也能是 :func:`derive_tokens` 派生出来的语义令牌（两者在 `_tokens` 里合并成一张表）。
#:
#: `success` / `warning` / `error` 无对应项，只留在我们这边给自研组件用。
#: `itemNormalColor` **故意不映射**：FluentUI 深色下它本来就是全透明（`QColor(255,255,255,0)`），
#: 旧映射把它写成 `bg_light`（`#0f3460`），等于给每个 Flu* 控件的常态底刷了一层蓝 ——
#: 这是"界面发花"的一个来源。留给 FluTheme 自己的深色默认值。
#: `accentColor` **也不写**：它是 `FluAccentColor` 对象，改它会触发 `refreshColors()`
#: （见 D-140），而我们真正要的是 `primaryColor`。
FLUENT_MAP: Tuple[Tuple[str, str], ...] = (
    ("accent", "primaryColor"),
    ("windowBgInactive", "backgroundColor"),
    ("windowBgInactive", "windowBackgroundColor"),
    ("windowBg", "windowActiveBackgroundColor"),
    ("barBg", "frameColor"),
    ("barBg", "frameActiveColor"),
    ("divider", "dividerColor"),
    ("textPrimary", "fontPrimaryColor"),
    ("textSecondary", "fontSecondaryColor"),
    ("textTertiary", "fontTertiaryColor"),
    ("itemHover", "itemHoverColor"),
    ("itemPress", "itemPressColor"),
    ("itemCheck", "itemCheckColor"),
)

#: `FluTheme.darkMode` 的取值。**不是 `Qt::ColorScheme`**（缺陷 D-139）：
#: `FluThemeType::DarkMode` 是 `System=0 / Light=1 / Dark=2`（`third_party/FluentUI/src/Def.h`）。
#: 证据：`poc/_probe_fluent_darkmode.py`，实测 1 → `dark=False`、2 → `dark=True`。
FLUENT_LIGHT = 1
FLUENT_DARK = 2

#: `FluTheme.darkMode` 的属性名（写序有硬要求，见模块文档 D-140）。
FLUENT_DARK_MODE_PROP = "darkMode"

#: `darkMode` 的默认值：本项目的主题集全是深色，且**不求值系统**。
FLUENT_DARK_MODE_DEFAULT = FLUENT_DARK

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

#: 返工 A 组新增的**动效与布局**令牌（数值取自参考项目 FusionMusicPlayer 的
#: `app/ui/Theme.qml`，与我们的 `radius*` 档位对齐后取整）。
DURATION_FAST = 110
DURATION_NORMAL = 190
DURATION_SLOW = 300
NAV_WIDTH = 208
TITLE_BAR_HEIGHT = 40
STATUS_BAR_HEIGHT = 28


# ─── 语义令牌派生（返工 A 组任务 A5） ──────────────────────────

#: 派生令牌名（QML 属性名）。**与 :data:`COLOR_KEYS` 的 12 个名字不重叠** ——
#: 重叠会让 `_tokens` 的合并悄悄覆盖掉主题文件里的值（有测试钉住不重叠）。
TOKEN_KEYS: Tuple[str, ...] = (
    "windowBg",
    "windowBgInactive",
    "navBg",
    "barBg",
    "cardHover",
    "divider",
    "overlayBg",
    "scrim",
    "textTertiary",
    "accentSoft",
    "accentPressed",
    "accentText",
    "itemHover",
    "itemPress",
    "itemCheck",
)

#: 深色主题下"叠加一层白"的不透明度档位（FluentUI 深色用的就是这三档，
#: 见 `FluTheme.cpp` 的 `refreshColors()`）。锁深色 = 只会用到白色叠加。
ITEM_HOVER_ALPHA = 0.06
ITEM_PRESS_ALPHA = 0.09
ITEM_CHECK_ALPHA = 0.12
#: 强调色的柔和底（参考项目深色档是 0.20）。
ACCENT_SOFT_ALPHA = 0.20
#: 模态遮罩（参考项目深色档 0.55）。
SCRIM_ALPHA = 0.55
#: `accentText` 的对比度阈值：强调色相对亮度高于它就用深色文字，否则用白字。
ACCENT_TEXT_LUMINANCE_THRESHOLD = 0.55


def mix_colors(first: QColor, second: QColor, ratio: float) -> QColor:
    """按 ``ratio`` 把 ``first`` 混向 ``second``（含 alpha 通道）。

    QColor 在 Qt6/PySide6 里**没有** mix 方法，所以这里自己插值
    （参考项目的 QML `Theme.mix()` 是同一套算法，只是那边在 QML 里算 ——
    我们这边放 Python 是因为闸门 R8 不许 QML 出现颜色字面量）。

    Examples:
        >>> mix_colors(QColor("#000000"), QColor("#ffffff"), 0.5).name()
        '#808080'
    """
    t = min(1.0, max(0.0, float(ratio)))
    return QColor.fromRgbF(
        first.redF() + (second.redF() - first.redF()) * t,
        first.greenF() + (second.greenF() - first.greenF()) * t,
        first.blueF() + (second.blueF() - first.blueF()) * t,
        first.alphaF() + (second.alphaF() - first.alphaF()) * t,
    )


def relative_luminance(color: QColor) -> float:
    """WCAG 相对亮度（0~1）—— 只用来判断"强调色上该压黑字还是白字"。"""
    channels = []
    for value in (color.redF(), color.greenF(), color.blueF()):
        channels.append(value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def derive_tokens(colors: Dict[str, QColor]) -> Dict[str, QColor]:
    """从 12 个主题色派生出界面重做需要的语义令牌。

    Args:
        colors: 驼峰名 → QColor 的 12 键表（见 :data:`COLOR_KEYS`）。
            缺键时按黑兜底（与 :func:`normalize_color` 同一条兜底策略）。

    Returns:
        令牌名 → QColor（键见 :data:`TOKEN_KEYS`）。

    +-------------------+--------------------------------+------------------------------------+
    | 令牌              | 派生                            | 为什么                              |
    +===================+================================+====================================+
    | ``windowBg``      | ``bg_dark`` 原值                | 窗口/页面最底那一层（最暗）          |
    | ``windowBgInactive`` | ``bg_dark`` 压暗 18%         | FluWindow 失焦时的底色比激活时更暗   |
    | ``navBg``         | ``bg_medium`` 原值              | 导航栏与页面区同色会糊成一片         |
    | ``barBg``         | ``bg_medium`` 原值              | 顶栏/状态栏与导航栏同层              |
    | ``cardHover``     | ``card_bg`` 提亮 6%             | 卡片/列表行悬停要有反馈              |
    | ``divider``       | ``card_border`` 混向 ``bg_medium`` 50% | 分割线要比卡片描边更弱        |
    | ``overlayBg``     | ``card_bg`` 提亮 5%             | 对话框要比卡片再"浮"一层             |
    | ``scrim``         | 黑 55% 透明                     | 模态遮罩                            |
    | ``textTertiary``  | ``text_secondary`` 压暗 45%     | 三级文字（补充说明、计数）           |
    | ``accentSoft``    | 强调色 20% 透明                 | 导航选中底/标签底（不要实心强调块）  |
    | ``accentPressed`` | 强调色压暗 15%                  | 按下态（Qt 的 `darker()`）           |
    | ``accentText``    | 按强调色亮度给白或近黑          | 强调底上的文字（浅强调色压白字看不清）|
    | ``itemHover``     | 白 6% 透明                      | FluentUI 深色的悬停叠加              |
    | ``itemPress``     | 白 9% 透明                      | 同上，按下                          |
    | ``itemCheck``     | 白 12% 透明                     | 同上，选中/勾选                     |
    +-------------------+--------------------------------+------------------------------------+
    """
    black = QColor("#000000")
    white = QColor("#ffffff")

    def get(name: str) -> QColor:
        return colors.get(name, black)

    accent = get("accent")
    bg_dark = get("bgDark")
    bg_medium = get("bgMedium")
    card_bg = get("cardBg")
    text_secondary = get("textSecondary")

    use_white_text = relative_luminance(accent) <= ACCENT_TEXT_LUMINANCE_THRESHOLD

    def overlay(alpha: float) -> QColor:
        return QColor(255, 255, 255, int(round(255 * alpha)))

    return {
        "windowBg": bg_dark,
        "windowBgInactive": mix_colors(bg_dark, black, 0.18),
        "navBg": bg_medium,
        "barBg": bg_medium,
        "cardHover": mix_colors(card_bg, white, 0.06),
        "divider": mix_colors(get("cardBorder"), bg_medium, 0.5),
        "overlayBg": mix_colors(card_bg, white, 0.05),
        "scrim": QColor(0, 0, 0, int(round(255 * SCRIM_ALPHA))),
        "textTertiary": mix_colors(text_secondary, bg_medium, 0.45),
        "accentSoft": QColor(accent.red(), accent.green(), accent.blue(), int(round(255 * ACCENT_SOFT_ALPHA))),
        "accentPressed": accent.darker(115),
        # 强调色偏亮时压白字看不清（FmButton 的遗留项 `onAccent`）：亮度超过阈值就改用深色。
        "accentText": white if use_white_text else mix_colors(bg_dark, black, 0.4),
        "itemHover": overlay(ITEM_HOVER_ALPHA),
        "itemPress": overlay(ITEM_PRESS_ALPHA),
        "itemCheck": overlay(ITEM_CHECK_ALPHA),
    }


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
        #: 派生令牌的**对象**缓存（QML 每读一次属性都要 new 一个 QColor 太浪费；
        #: 值只随主题变，所以跟 `_colors` 一起在 `_read_colors()` 里重算一次）。
        self._derived: Dict[str, QColor] = {}
        self._revision = 0
        self._fluent: Optional[QObject] = None
        self._fluent_warned = False
        self._theme_name = str(self._config_value("theme_name", DEFAULT_THEME) or DEFAULT_THEME)
        self._accent = self._config_value("accent_color", None)
        self._apply_initial_theme()

    # ─── 12 个色键（契约 §5.1 冻结的名字与顺序） ──────────────

    def _color(self, camel: str) -> QColor:
        return QColor(self._colors.get(camel, FALLBACK_COLOR))

    def _token(self, name: str) -> QColor:
        """按令牌名取色：先查 12 个主题色，再查派生令牌（两边**不允许重名**）。

        FluTheme 映射表（:data:`FLUENT_MAP`）与新增的 QML 属性都走这里，
        这样"注入 FluTheme 的颜色"与"QML 看到的颜色"永远是同一个来源。
        """
        if name in self._colors:
            return self._color(name)
        cached = self._derived.get(name)
        if cached is not None:
            return cached
        logger.warning("未知令牌 %r（按 %s 兜底）", name, FALLBACK_COLOR)
        return QColor(FALLBACK_COLOR)

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

    # ─── 派生语义令牌（返工 A 组任务 A5；派生规则见 derive_tokens） ──
    #
    # 为什么要有这一组：闸门 R8 不许 QML 出现颜色字面量（`Qt.rgba` 也在禁列），
    # 所以"比卡片亮 6% 的悬停色"这种值只能由 Python 算好。它们**全部**由那 12 个
    # 主题色派生，因此主题文件格式没变、用户导入的主题照样能用。

    @Property(QColor, notify=changed)
    def windowBg(self) -> QColor:  # noqa: N802
        """窗口/页面区最底那一层（= `bg_dark`）。"""
        return self._token("windowBg")

    @Property(QColor, notify=changed)
    def windowBgInactive(self) -> QColor:  # noqa: N802
        """窗口失焦时的底色（比激活时压暗 18%）。"""
        return self._token("windowBgInactive")

    @Property(QColor, notify=changed)
    def navBg(self) -> QColor:  # noqa: N802
        """左侧导航栏底色（= `bg_medium`）。"""
        return self._token("navBg")

    @Property(QColor, notify=changed)
    def barBg(self) -> QColor:  # noqa: N802
        """顶栏/状态栏底色（= `bg_medium`）。"""
        return self._token("barBg")

    @Property(QColor, notify=changed)
    def cardHover(self) -> QColor:  # noqa: N802
        """卡片/列表行悬停时的底色（卡片色提亮 6%）。"""
        return self._token("cardHover")

    @Property(QColor, notify=changed)
    def divider(self) -> QColor:
        """1px 分割线（比 `cardBorder` 更弱，不会把界面切成豆腐块）。"""
        return self._token("divider")

    @Property(QColor, notify=changed)
    def overlayBg(self) -> QColor:  # noqa: N802
        """对话框/浮层底色（比卡片再"浮"一层）。"""
        return self._token("overlayBg")

    @Property(QColor, notify=changed)
    def scrim(self) -> QColor:
        """模态遮罩（黑 55% 透明）。"""
        return self._token("scrim")

    @Property(QColor, notify=changed)
    def textTertiary(self) -> QColor:  # noqa: N802
        """三级文字：补充说明、计数、"第 N 项"这类信息。"""
        return self._token("textTertiary")

    @Property(QColor, notify=changed)
    def accentSoft(self) -> QColor:  # noqa: N802
        """强调色的柔和底（导航选中底、标签底）—— 不要用实心强调块。"""
        return self._token("accentSoft")

    @Property(QColor, notify=changed)
    def accentPressed(self) -> QColor:  # noqa: N802
        """强调色的按下态（压暗 15%）。"""
        return self._token("accentPressed")

    @Property(QColor, notify=changed)
    def accentText(self) -> QColor:  # noqa: N802
        """压在强调色上的文字色（按强调色亮度自动选白/近黑）。

        这是阶段 2 记录在案的遗留项：`FmButton` 一直是"白字压强调色"，
        遇到浅强调色（黄、青）就几乎看不见。现在由这里统一给。
        """
        return self._token("accentText")

    @Property(QColor, notify=changed)
    def itemHover(self) -> QColor:  # noqa: N802
        """FluentUI 式悬停叠加（白 6% 透明）—— 叠在任何表面上都协调。"""
        return self._token("itemHover")

    @Property(QColor, notify=changed)
    def itemPress(self) -> QColor:  # noqa: N802
        """FluentUI 式按下叠加（白 9% 透明）。"""
        return self._token("itemPress")

    @Property(QColor, notify=changed)
    def itemCheck(self) -> QColor:  # noqa: N802
        """FluentUI 式选中叠加（白 12% 透明）。"""
        return self._token("itemCheck")

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

    # ─── 动效与布局令牌（返工 A 组新增；数值来源见常量注释） ──

    @Property(int, constant=True)
    def durationFast(self) -> int:  # noqa: N802
        """快动效 110ms：悬停、选中底色这类"随手就变"的反馈。"""
        return DURATION_FAST

    @Property(int, constant=True)
    def durationNormal(self) -> int:  # noqa: N802
        """常规动效 190ms：展开/收起、页面切换。"""
        return DURATION_NORMAL

    @Property(int, constant=True)
    def durationSlow(self) -> int:  # noqa: N802
        """慢动效 300ms：启动画面淡出这类一次性过渡。"""
        return DURATION_SLOW

    @Property(int, constant=True)
    def navWidth(self) -> int:  # noqa: N802
        """左侧导航栏宽度（208）。"""
        return NAV_WIDTH

    @Property(int, constant=True)
    def titleBarHeight(self) -> int:  # noqa: N802
        """顶栏高度（40）—— `FluAppBar` 的默认 30 太挤，放不下搜索框。"""
        return TITLE_BAR_HEIGHT

    @Property(int, constant=True)
    def statusBarHeight(self) -> int:  # noqa: N802
        """底部状态栏高度（28）。"""
        return STATUS_BAR_HEIGHT

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

    def _refresh_tokens(self) -> None:
        """读完 12 键之后立刻重算派生令牌。

        **必须与 `_read_colors()` 成对调用**（两个读口子：`_sync()` 与
        `_apply_initial_theme()`），否则派生令牌会停在上一套主题上 ——
        症状是"切了主题，卡片变了但悬停色/选中条还是旧的"。
        """
        self._colors = self._read_colors()
        self._derived = derive_tokens({name: self._color(name) for name, _ in COLOR_KEYS})

    def _bump(self) -> None:
        """自增 revision 并发 `changed`（12 个色键 + 15 个令牌 + revision 共用的 NOTIFY）。"""
        self._revision += 1
        self.changed.emit()

    def _sync(self) -> None:
        """服务改完 `COLORS` 之后的统一收尾：读属性 → 写 FluTheme → 通知 QML。

        三个动作**必须成对出现**：一开始只写了前两步里的"读 + 发信号"，
        结果切主题时 FluTheme 没跟着变（FluentUI 控件不跟随）—— 被
        `test_fluent_theme_gets_the_keys_and_dark_mode` 抓到。
        """
        self._refresh_tokens()
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
        self._refresh_tokens()
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
        logger.info("FluTheme 已接上：darkMode=%s（Dark）+ 注入 %d 个颜色键", FLUENT_DARK, len(FLUENT_MAP))
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
        """写 FluTheme：**先 `darkMode`，再颜色**（写序有硬要求，见模块文档 D-140）。

        为什么顺序不能反：`darkMode` 真的变化时会触发
        `darkModeChanged → darkChanged → refreshColors()`，而 `refreshColors()`
        会把**全部**颜色重置成 FluentUI 默认值。先写颜色再写 `darkMode`，
        等于刚涂好的墙立刻被刷回原色 —— 实测 `#1a1a2e` → `#202020`。
        """
        theme = self._resolve_fluent()
        if theme is None:
            return False
        try:
            # 1) 先定明暗：本项目的主题集全是深色（契约第六节决策 9），不求值系统。
            theme.setProperty(FLUENT_DARK_MODE_PROP, FLUENT_DARK_MODE_DEFAULT)
            # 2) 再把我们的令牌逐个盖上去（这一步必须在 1) 之后）。
            for token, prop in FLUENT_MAP:
                theme.setProperty(prop, self._token(token))
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
    "ACCENT_SOFT_ALPHA",
    "ACCENT_TEXT_LUMINANCE_THRESHOLD",
    "COLOR_KEYS",
    "DEFAULT_THEME",
    "DURATION_FAST",
    "DURATION_NORMAL",
    "DURATION_SLOW",
    "FALLBACK_COLOR",
    "FLUENT_DARK",
    "FLUENT_DARK_MODE_DEFAULT",
    "FLUENT_DARK_MODE_PROP",
    "FLUENT_LIGHT",
    "FLUENT_MAP",
    "FLUENT_THEME_TYPE",
    "FLUENT_URI",
    "ITEM_CHECK_ALPHA",
    "ITEM_HOVER_ALPHA",
    "ITEM_PRESS_ALPHA",
    "NAV_WIDTH",
    "QML_NAME",
    "SCRIM_ALPHA",
    "STATUS_BAR_HEIGHT",
    "TITLE_BAR_HEIGHT",
    "TOKEN_KEYS",
    "ThemeBridge",
    "derive_tokens",
    "is_hex_color",
    "mix_colors",
    "normalize_color",
    "relative_luminance",
]
