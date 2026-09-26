#!/usr/bin/env python
"""QML 规则闸门（阶段 2 任务 2.7；规则表见 docs/refactor/11-phase2-contract.md 第七节）。

用法::

    .venv\\Scripts\\python.exe scripts\\check_qml_rules.py              # 全部规则
    .venv\\Scripts\\python.exe scripts\\check_qml_rules.py --json        # 机器可读
    .venv\\Scripts\\python.exe scripts\\check_qml_rules.py --rule R3     # 只跑一条
    .venv\\Scripts\\python.exe scripts\\check_qml_rules.py --base poc\\qml_rules_fixtures\\bad
    .venv\\Scripts\\python.exe scripts\\check_qml_rules.py --list        # 规则与扫描范围
    .venv\\Scripts\\python.exe scripts\\check_qml_rules.py --list-known  # 已登记例外
    .venv\\Scripts\\python.exe scripts\\check_qml_rules.py --strict      # 已登记例外也算失败

退出码：0 = 通过；1 = 有未登记违规或有登记项过期；2 = 用法错误。

`--base` 默认是仓库根，目录结构假定与仓库一致（`qml/`、`app/bridges/`）；
`poc/qml_rules_fixtures/` 下的正负例就是靠它被同一套判据检查的 —— 闸门自己也要被测试。

## 八条规则的判据（写清楚，免得阶段 3 靠猜）

> R1~R7 是任务 2.7 冻结的七条（`docs/refactor/11-phase2-contract.md` 第七节）；
> **R8 是任务 2.8 新增的第八条**（判据与边界同样写在这里，契约第七节的表要按本文件同步）。

**R1 禁渐变**：`.qml`/`.js` 里出现渐变类型（`Gradient` / `LinearGradient` /
`RadialGradient` / `ConicalGradient` / `GradientStop`）、`gradient:` 属性，或亚克力/Mica
材质被开启（`FluAcrylic` 类型、`effect: "<非 normal>"`、`windowEffect = "<非 normal>"`）。
材质判据只认**字面量**：`effect: Theme.windowEffect` 这类动态值不判（见"局限"）。

**R2 禁 emoji**：按**码点**判定，不用正则（正则的 emoji 类不完整且随版本漂移）。
命中集合 = Unicode `Emoji_Presentation=Yes` 的 BMP 区间（逐条抄自 emoji-data.txt）
∪ SMP 图形区 U+1F000–U+1FAFF ∪ 变体选择符 U+FE0F ∪ 组合键帽 U+20E3；
另外把字符串里的 `\\uXXXX` / `\\u{...}` 转义解码后再判一次
（`"\\uD83D\\uDE00"` 与直接写 emoji 等价）。**扫描整个文件，注释也算** ——
项目全局规则是"不加 emoji"，这一点与 R3 恰好相反。

**R3 禁硬编码中文**：只判**字符串字面量里的 CJK**（含 `\\uXXXX` 转义写法）。
注释里的中文**允许且鼓励** —— 这是最容易写错的地方，所以扫描器是自写的单趟状态机：
`"a//b"` 里的 `//` 不是注释，`// "` 里的引号不是字符串，只有状态机能同时判对。
字符串与注释之外的**裸 CJK** 也判（那只会是正则字面量或中文标识符）。

**R4 i18n 绑定**：`Tr.t(` 出现在**属性绑定表达式**里即违规 —— 绑定必须读
`Tr.map["键"]`，否则语言切换不重算（契约第五.2 节）。"是不是绑定"的判据：
先找该位置最近的未配对 `{` 并给它定性 —— `= {`、`( {`、`, {`、`=> {`、函数体 `) {`、
`try/else/function {` 之后是**命令式 JS**；`onXxx: {` 是**信号处理器**（命令式）；
其余 `名: {` 是 QML 绑定块或对象体。命令式之外再用"所在语句的宿主键"复核
（同一条语句里 `on[A-Z]` 开头的键是处理器，处理器不是绑定；处理器绑在表达式上
也一样）；跨行绑定向上回溯到宿主键所在的行，`键: {` 的**块体绑定**也算绑定。
`Qt.binding(...)` 无条件算绑定。`.js` 文件不判（那里没有属性绑定）。
**命令式 JS 里的 `Tr.t(` 是允许的**。

**R5 违禁组件**：
(a) `qml/pages/**` 里 `import` 了已知 Python 顶层模块（`ui` / `launcher` / 仓库根模块…）
而又不是 `services` / `app.bridges` → 违规；
(b) `qml/pages/**` 里使用了 `qml/` 下自定义组件、但该组件名不在
`qml/components/COMPONENTS.md` 白名单里 → 违规。
白名单文件不存在（2.16 之前）或解析不出任何名字时，(b) **降级为"跳过并说明"**，
(a) 照常判定。

**R6 transparent**：`qml/overlays/**` 里出现 `color: "transparent"` / `"#00000000"` /
`"#0000"`（大小写不敏感）或 `Qt.rgba(0, 0, 0, 0)` → 违规。依据：无合成器的 X11 下
整窗透明会黑屏（阶段 0 第 12.5 节），悬浮窗要用 `Window.opacity` + 实色。

**R7 线程红线**：`app/bridges/**/*.py` 里
- `QMetaObject.invokeMethod` —— **无条件**违规（契约第六节决策 10：一律发信号）；
- worker 函数体内调用 `rootObjects()/rootContext()/setContextProperty()/createComponent()/
  setProperty()`，或对名字里含 `engine` 的对象调用 `load()/quit()/addImportPath()` → 违规；
- worker 函数体里写**类体声明过的 Qt 可见名字**（`Q_PROPERTY(...)` 的第二个 token、
  `@Property` 装饰的函数名、`Signal()` 的赋值名），含 `self.items[0] = …` 这种原地改 → 违规。
  "worker 函数" = 被 `Thread(target=…)` / `…submit(…)` / `functools.partial(…)` 引用的函数，
  **按名字解析，解析不到就不判** —— 宁可漏判也不把普通函数误判成 worker。
  写普通 Python 属性（`self._cache = {}`）与 `emit()` 信号是允许的。

**R8 禁颜色字面量**：`qml/**` 下的 `.qml` 与 `.js`（**不含 `.svg`**：图标资源天生带
`fill="#ffffff"`，扫了会满屏误报）里出现颜色字面量即违规：`#rgb` / `#rgba` /
`#rrggbb` / `#aarrggbb`（大小写不敏感），或 `Qt.rgba(...)` 字面量。
依据：缺陷 **D-102**「大量主题外的硬编码颜色，主题切换后不跟随」与风险 **R-15**
「禁止手工登记颜色」—— 颜色只能来自 `Theme.*`（2.8 的桥）或设计令牌。
判据写在 `no_comments` 视图上，所以**注释里写颜色不算**（注释正是该说明颜色的地方）；
`color: Theme.bgDark` 这类绑定本来就不匹配任何颜色字面量。
两条**刻意留出的口子**：alpha 为 0 的写法（`#0000` / `#00rrggbb` / `Qt.rgba(0, 0, 0, 0)`）
交给 R6 与布局，不重复报；`.svg` 不在扫描范围。

## 已知边界（哪边会漏判、哪边会误报）

每条规则都刻意做窄 —— 宽而误报的闸门会被绕过。下面这些写法**现在抓不到**
（阶段 3 的评审要盯）：

- **R1**：材质只认字面量。`effect: Theme.windowEffect` / `effect: someVar` 不判；
  反过来，第三方组件里恰好也叫 `gradient` / `effect` 的自定义属性会被误报。
- **R2**：只覆盖"默认就是 emoji 呈现"的码点集合加 U+FE0F / U+20E3。
  `✓ ★ ♥` 这类**文本**符号不判（要配 U+FE0F 才判）；用 `\\xE4\\xB8\\xAD`
  逐字节拼中文/emoji 的写法也抓不到。Unicode 更新后要补
  `_EMOJI_BMP_RANGES` / `_EMOJI_SMP_RANGES`。
- **R3**：CJK 判据含假名与谚文（比"中文"宽，硬编码日文韩文同样该走 i18n）；
  注释里的中文一律放行 —— 所以"把用户可见文案写成注释"这种自欺抓不到。
  字符串与注释之外的裸 CJK 也会报（那只会是正则字面量或中文标识符）。
- **R4**：是启发式，不是 QML 解析器。会**漏**：`var f = Tr.t; text: f("k")`
  这种先取值再调用的写法、`Qt.binding` 离得太远的写法、以及把绑定写在
  `Connections`/`Binding` 之外的非标准形态。会**误报**：QML 对象里自定义了
  以 `on[A-Z]` 开头的属性（QML 自己也不允许）、或把逗号写在宿主键与 `Tr.t(`
  之间的怪写法 —— 这两种都能用登记例外兜住。
- **R5**：`import` 判据用**固定的 Python 顶层名清单**（`PY_TOP_LEVELS`），
  新增的根模块要手动加；组件白名单靠 `COMPONENTS.md` 的宽容解析 ——
  文档里任何大驼峰反引号词都会被当成"已登记组件"，写文档时注意。
  只扫 `qml/pages/**`，`shell/` 与 `overlays/` 用自研件不在本规则范围。
- **R6**：只看 `color:` 上的 `transparent` / `#00000000` / `#0000` /
  `Qt.rgba(0, 0, 0, 0)`；`opacity: 0`、`layer.enabled` 之类的透明路径不判。
  `overlays/` 下**子项**的 `color: "transparent"` 同样会报（契约就是这么定的，
  确实需要就登记例外）。
- **R7**：worker 只认 `Thread(target=…)` / `…submit(…)` / `partial(…)` 的**名字**，
  `QThread` 子类的 `run()`、`asyncio`、外面再包一层 lambda 的目标都抓不到；
  `setattr(self, "prop", v)` 形式的写属性也抓不到。属性名只认同一个文件里
  类体声明的 `Q_PROPERTY` / `@Property` / `name = Property(...)` / `Signal()` ——
  **基类**里声明的属性抓不到。`self._cache = {}` 这类普通 Python 属性**故意不判**：
  那是桥里最常见的合法写法，判了会让整条规则变成噪声。
- **R8**：只认 `#hex` 与 `Qt.rgba(...)` 两种写法。**命名色**（`color: "white"` /
  `"red"` / `"steelblue"`）、`Qt.hsla(...)`、`Qt.lighter("#fff")` 的硬编码变体
  **抓不到**（不做命名色表：名单长了会误伤"字符串恰好叫 white"的地方，
  要补得先定"哪些名字算颜色"）。会**误报**：字符串里恰好长成 `#1234` / `#123456`
  的**非颜色**内容（URL 片段、编号）—— 登记例外兜住。alpha 为 0 的写法
  （`#0000` / `#00000000` / `Qt.rgba(0, 0, 0, 0)`）**故意不判**：那是 R6 的领地，
  重复报只会让两条规则的负例互相污染。`.svg` 不在扫描范围（图标天生带十六进制颜色）。

## 已登记例外（`REGISTERED_EXCEPTIONS`）

键 = `文件:行:规则`（文件是相对 `--base` 的 posix 路径），值 = `(理由, 任务号)`。
命中打 `[REGISTERED]`、不算失败；**登记了却一次都没命中 → 报错（过期）**，
防止这张表腐化成永久豁免名单（同 `scripts/relocate_module.py` 的
`REGISTERED_LINE_DELTAS`）。行号会随文件改动漂移：漂移了就说明这条登记必须重新确认，
这正是我们要的。`--rule` 只跑一条规则时，只对**该规则**的登记项做过期检查。
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 相对 `--base` 的扫描根（base 默认仓库根；fixture 测试用 --base 指向别处）
QML_ROOT = "qml"
BRIDGES_ROOT = "app/bridges"
COMPONENTS_WHITELIST = "qml/components/COMPONENTS.md"
COMPONENTS_DIR = "qml/components"
PAGES_DIR = "qml/pages"
OVERLAYS_DIR = "qml/overlays"

SKIP_DIRS = {"__pycache__", ".venv", ".git", "build", "dist", "node_modules"}

RULE_IDS: Tuple[str, ...] = ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R8")

RULE_LABELS: Dict[str, str] = {
    "R1": "禁渐变 / 禁亚克力与 Mica 材质",
    "R2": "禁 emoji（按码点判定）",
    "R3": "禁硬编码中文（只判字符串字面量，注释允许）",
    "R4": "绑定必须用 Tr.map[…]（绑定里不得有 Tr.t(）",
    "R5": "页面不得越界 import / 不得用白名单外的自研组件",
    "R6": "悬浮窗禁 color: transparent / #00000000",
    "R7": "桥的线程红线（worker 不得直接改 QObject / 碰引擎）",
    "R8": "禁颜色字面量（颜色只能来自 Theme.* / 设计令牌）",
}

# ─── R2 用到的 emoji 码点表 ────────────────────────────────────

#: `Emoji_Presentation=Yes`（默认就是彩色 emoji 呈现）的 BMP 区间。
#: 来源：Unicode 15.1 `emoji-data.txt`。这里**不**收 `Emoji=Yes` 但
#: `Emoji_Presentation=No` 的文本符号（✓ ★ ♥ 之类）—— 那些要配 U+FE0F 才变 emoji，
#: 而 U+FE0F 本身已在下面的单点表里，所以"文字符号 + 变体选择符"照样能抓到。
_EMOJI_BMP_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x231A, 0x231B),  # ⌚⌛
    (0x23E9, 0x23EC),  # ⏩⏪⏫⏬
    (0x23F0, 0x23F0),
    (0x23F3, 0x23F3),
    (0x25FD, 0x25FE),  # ◽◾
    (0x2614, 0x2615),  # ☔☕
    (0x2648, 0x2653),  # 十二星座
    (0x267F, 0x267F),
    (0x2693, 0x2693),
    (0x26A1, 0x26A1),  # ⚡
    (0x26AA, 0x26AB),  # ⚪⚫
    (0x26BD, 0x26BE),  # ⚽⚾
    (0x26C4, 0x26C5),  # ⛄⛅
    (0x26CE, 0x26CE),
    (0x26D4, 0x26D4),  # ⛔
    (0x26EA, 0x26EA),
    (0x26F2, 0x26F3),
    (0x26F5, 0x26F5),
    (0x26FA, 0x26FA),
    (0x26FD, 0x26FD),
    (0x2705, 0x2705),  # ✅
    (0x270A, 0x270B),
    (0x2728, 0x2728),  # ✨
    (0x274C, 0x274C),  # ❌
    (0x274E, 0x274E),
    (0x2753, 0x2755),
    (0x2757, 0x2757),  # ❗
    (0x2795, 0x2797),
    (0x27B0, 0x27B0),
    (0x27BF, 0x27BF),
    (0x2B1B, 0x2B1C),  # ⬛⬜
    (0x2B50, 0x2B50),  # ⭐
    (0x2B55, 0x2B55),  # ⭕
)

#: SMP 的图形区：整块都是 emoji / 字符图形，全收进来没有误报风险。
_EMOJI_SMP_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x1F000, 0x1F0FF),  # 麻将 / 多米诺 / 扑克
    (0x1F100, 0x1F1FF),  # 带圈字母补充（含区域指示符 = 国旗）
    (0x1F200, 0x1F2FF),  # 带圈表意文字补充
    (0x1F300, 0x1F5FF),  # 杂项符号与图形
    (0x1F600, 0x1F64F),  # 表情
    (0x1F650, 0x1F67F),  # 装饰符号（任务 2.10 补：原先漏了这一整块）
    (0x1F680, 0x1F6FF),  # 交通与地图符号
    (0x1F700, 0x1F7FF),  # 炼金术符号 —— **含 1F7E0–1F7EB 的彩色圆点**（🟡🟢 就在里面）
    (0x1F800, 0x1F8FF),  # 补充箭头-C
    (0x1F900, 0x1F9FF),  # 补充符号与图形
    (0x1FA00, 0x1FA6F),  # 象棋符号（任务 2.10 补）
    (0x1FA70, 0x1FAFF),  # 符号与图形扩展 A
)

#: **本项目实际用作界面图标**的 `Emoji_Presentation=No` 文本符号。
#:
#: 为什么单独列一张表而不是把整段 `Emoji=Yes` 都收进来：那些符号里绝大多数是
#: **真·文字符号**（数学、箭头、标点、`✓ ★ ♥` 等），全收会造成大量误报，闸门就会被绕过。
#: 但下面这些在 FMCL 里**就是当图标用的**（`07-known-defects.md` 的 D-07），
#: 换 UI 时必须一起换掉 —— 任务 2.10 的清单实测：这批符号原来的判据完全看不见。
_EMOJI_AS_ICON_SINGLES: Dict[int, str] = {
    0x23F9: "⏹ 停止方块（旧界面的「强杀游戏」按钮）",
    0x23F3: "⏳ 沙漏（下载等待）",
    0x2699: "⚙ 齿轮（旧界面的「资源管理」入口）",
    0x26A0: "⚠ 警告（状态栏）",
    0x2139: "ℹ 信息",
    0x25B6: "▶ 播放三角",
    0x270F: "✏ 铅笔（编辑）",
    0x2795: "➕ 加号（新增）",
    0x2796: "➖ 减号（移除）",
    0x2B50: "⭐ 星（收藏/评分）",
    0x1F512: "🔒 锁定（桌面歌词锁定态）",
    0x1F513: "🔓 解锁",
}

#: 单独成罪的码点：变体选择符-16（把 BMP 文本符号强变成 emoji 呈现）与组合键帽。
_EMOJI_SINGLE: Dict[int, str] = {
    0xFE0F: "变体选择符-16（把文本符号强行变成 emoji 呈现）",
    0x20E3: "组合键帽（1️⃣ 里的 U+20E3）",
}

# ─── R3 用到的 CJK 码点区间 ────────────────────────────────────

#: 判据比字面意义上的"中文"宽：硬编码日文/韩文同样该走 i18n，所以一并算。
_CJK_RANGES: Tuple[Tuple[int, int], ...] = (
    (0x3000, 0x303F),  # CJK 标点（、。「」等）
    (0x3040, 0x30FF),  # 平假名 / 片假名
    (0x3130, 0x318F),  # 谚文字母
    (0x3400, 0x4DBF),  # CJK 扩展 A
    (0x4E00, 0x9FFF),  # CJK 统一表意文字
    (0xAC00, 0xD7AF),  # 谚文音节
    (0xF900, 0xFAFF),  # CJK 兼容表意文字
    (0xFF00, 0xFFEF),  # 全角/半角形式（！？：等）
)

#: 字符串字面量里的转义码点：`\uXXXX`、`\u{XXXXX}`（ES6）、`\xXX`
_ESCAPE_RE = re.compile(r"\\u\{([0-9a-fA-F]{1,6})\}|\\u([0-9a-fA-F]{4})|\\x([0-9a-fA-F]{2})")

# ─── 已登记例外 ────────────────────────────────────────────────

#: 键 = `文件:行:规则`（文件相对 `--base`，posix 分隔符），值 = `(理由, 任务号)`。
#:
#: **新增违规不得往这里加**，必须直接改代码；只有"要等某个后续任务才能消除"的情况
#: 才允许登记，并且必须写清任务号 —— 每一条都会在过期检查里被点名。
#:
#: 示例（目前一条都没有，保留格式说明）::
#:
#:     "qml/overlays/LyricOverlay.qml:76:R6": (
#:         "歌词窗的整窗底色用 Window.opacity 控制，这里是**子项**的命中区，不是窗口底色",
#:         "2.15",
#:     ),
REGISTERED_EXCEPTIONS: Dict[str, Tuple[str, str]] = {}

#: 登记键的合法形状：`路径:行:规则号`
_REGISTRY_KEY_RE = re.compile(r"^[^:]+:\d+:(R[1-8])$")


# ─── 源码扫描：注释与字符串字面量分开 ──────────────────────────


class SourceIndex:
    """把字符偏移换算成 `行:列`（都从 1 开始）。"""

    def __init__(self, text: str) -> None:
        self.text = text
        self.line_starts: List[int] = [0]
        for m in re.finditer("\n", text):
            self.line_starts.append(m.end())

    @property
    def line_count(self) -> int:
        return len(self.line_starts)

    def line_col(self, offset: int) -> Tuple[int, int]:
        offset = max(0, min(offset, len(self.text)))
        line = bisect_right(self.line_starts, offset)
        return line, offset - self.line_starts[line - 1] + 1

    def line_start(self, line: int) -> int:
        if line < 1:
            return 0
        if line > len(self.line_starts):
            return len(self.text)
        return self.line_starts[line - 1]

    def line_end(self, line: int) -> int:
        """行末偏移（不含换行符）。"""
        if 1 <= line < len(self.line_starts):
            return max(self.line_starts[line - 1], self.line_starts[line] - 1)
        return len(self.text)

    def line_text(self, line: int) -> str:
        return self.text[self.line_start(line):self.line_end(line)]


@dataclass(frozen=True)
class StringSpan:
    """一段字符串字面量（QML/JS 侧）。"""

    start: int  # 起始引号的位置
    inner_start: int  # 内容起点（跳过起始引号）
    inner_end: int  # 内容终点（不含结束引号）
    quote: str
    text: str  # 原始内容（保留转义写法；未闭合的字面量也照样收进来）


@dataclass
class Scanned:
    """一个 QML/JS 文件的三份视图 + 字面量清单。"""

    masked: str  # 注释与字符串字面量都换成空格（换行保留）→ 只看"代码结构"
    no_comments: str  # 只把注释换成空格（字符串原样保留）→ 读 import 路径、颜色值
    strings: List[StringSpan]
    notes: List[str]  # 扫描期的异常（未闭合字符串/块注释），只提示不判违规


def scan_source(text: str, index: SourceIndex) -> Scanned:
    """单趟左到右扫描，把注释与字符串字面量分开。

    为什么不用正则：R3 要求"字符串里的中文算违规、注释里的中文不算"，而
    `text: "a//b"` 里的 `//` 不是注释、`// text: "设置` 里的引号也不是字符串。
    只有带状态的一趟扫描能同时判对这两个方向 —— 这也是 R3 最容易写错的地方。
    """
    n = len(text)
    masked = list(text)
    no_comments = list(text)
    strings: List[StringSpan] = []
    notes: List[str] = []

    def blank(both: bool, lo: int, hi: int) -> None:
        for k in range(max(lo, 0), min(hi, n)):
            if text[k] == "\n":
                continue
            masked[k] = " "
            if both:
                no_comments[k] = " "

    i = 0
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            end = text.find("\n", i)
            end = n if end < 0 else end
            blank(True, i, end)
            i = end
            continue
        if ch == "/" and nxt == "*":
            close = text.find("*/", i + 2)
            if close < 0:
                notes.append(f"块注释未闭合（从第 {index.line_col(i)[0]} 行开始）")
                end = n
            else:
                end = close + 2
            blank(True, i, end)
            i = end
            continue
        if ch in "\"'`":
            j = i + 1
            closed = False
            while j < n:
                c = text[j]
                if c == "\\":
                    j += 2
                    continue
                if c == ch:
                    closed = True
                    break
                if c == "\n" and ch != "`":
                    break  # 单双引号里的裸换行 = 未闭合，别把后面整个文件当成字符串
                j += 1
            inner_end = min(j, n)
            strings.append(StringSpan(i, i + 1, inner_end, ch, text[i + 1:inner_end]))
            if not closed:
                notes.append(f"字符串字面量未闭合（从第 {index.line_col(i)[0]} 行开始）")
                end = inner_end
            else:
                end = inner_end + 1
            blank(False, i, end)  # 只清 masked：no_comments 要留着读路径与颜色值
            i = end if end > i else i + 1
            continue
        i += 1

    return Scanned("".join(masked), "".join(no_comments), strings, notes)


# ─── 数据模型 ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Violation:
    """一处违规。`key` 就是已登记例外表里的键。"""

    rule: str
    path: str  # 相对 base 的 posix 路径
    line: int
    column: int
    detail: str

    @property
    def key(self) -> str:
        return f"{self.path}:{self.line}:{self.rule}"

    def render(self) -> str:
        return f"  {self.path}:{self.line}:{self.column}  [{self.rule}] {self.detail}"

    def as_json(self) -> Dict[str, object]:
        return {
            "rule": self.rule,
            "file": self.path,
            "line": self.line,
            "column": self.column,
            "detail": self.detail,
            "key": self.key,
        }


#: 一条规则的检查结果：违规、被扫过的文件、降级说明
Checked = Tuple[List["Violation"], List[str], str]


@dataclass
class RuleResult:
    """一条规则的结果（违规已按登记表分流过）。"""

    rule: str
    files: List[str] = field(default_factory=list)
    violations: List["Violation"] = field(default_factory=list)
    registered: List[Tuple["Violation", str, str]] = field(default_factory=list)
    note: str = ""


@dataclass
class ProjectInfo:
    """一次运行的全部输入（base 下的 qml/ 与 app/bridges/）。"""

    base: Path
    qml_ctx: List[FileCtx] = field(default_factory=list)
    py_ctx: List[FileCtx] = field(default_factory=list)
    local_types: Dict[str, str] = field(default_factory=dict)
    whitelist: Optional[Set[str]] = None
    whitelist_note: str = ""

    def pages_ctxs(self) -> List[FileCtx]:
        prefix = PAGES_DIR + "/"
        return [c for c in self.qml_ctx if c.rel.startswith(prefix) and c.kind == "qml"]

    def overlays_ctxs(self) -> List[FileCtx]:
        prefix = OVERLAYS_DIR + "/"
        return [c for c in self.qml_ctx if c.rel.startswith(prefix) and c.kind in ("qml", "js")]


# ─── 文件装载 ──────────────────────────────────────────────────


def iter_files(root: Path, suffixes: Sequence[str]) -> Iterator[Path]:
    """展开扫描根（跳过缓存与虚拟环境）。"""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


_KIND_BY_SUFFIX = {".qml": "qml", ".js": "js", ".svg": "svg", ".py": "py"}


@dataclass
class FileCtx:
    """一个被测文件的全部视图。"""

    path: Path
    rel: str
    kind: str
    text: str  # 原文（换行已归一到 \n，行号因此与原文一致）
    index: SourceIndex
    scanned: Optional[Scanned] = None
    tree: Optional[ast.Module] = None
    notes: List[str] = field(default_factory=list)

    @property
    def code(self) -> str:
        """注释与字符串字面量都抹掉的视图（看代码结构用）。"""
        return self.scanned.masked if self.scanned is not None else self.text

    @property
    def without_comments(self) -> str:
        """只抹掉注释的视图（读 import 路径、颜色值用）。"""
        return self.scanned.no_comments if self.scanned is not None else self.text


def load_text(path: Path) -> Optional[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return text.replace("\r\n", "\n").replace("\r", "\n")


def make_ctx(base: Path, path: Path) -> FileCtx:
    rel = path.relative_to(base).as_posix()
    raw = load_text(path)
    ctx = FileCtx(
        path=path,
        rel=rel,
        kind=_KIND_BY_SUFFIX.get(path.suffix.lower(), "?"),
        text=raw or "",
        index=SourceIndex(raw or ""),
    )
    if raw is None:
        ctx.notes.append("文件读取失败（不是 UTF-8 或无法访问）")
        return ctx
    if ctx.kind in ("qml", "js"):
        ctx.scanned = scan_source(raw, ctx.index)
        ctx.notes.extend(ctx.scanned.notes)
    return ctx


def load_project(base: Path) -> ProjectInfo:
    project = ProjectInfo(base=base)
    for path in iter_files(base / QML_ROOT, (".qml", ".js", ".svg")):
        project.qml_ctx.append(make_ctx(base, path))
    for path in iter_files(base / BRIDGES_ROOT, (".py",)):
        project.py_ctx.append(make_ctx(base, path))
    for ctx in project.qml_ctx:
        if ctx.kind == "qml" and ctx.path.stem[:1].isupper():
            project.local_types.setdefault(ctx.path.stem, ctx.rel)
    project.whitelist, project.whitelist_note = load_component_whitelist(base)
    return project


def source_excerpt(text: str, limit: int = 24) -> str:
    """取一段便于人读的源码摘录（把换行压成空格）。"""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _violation(rule: str, ctx: FileCtx, offset: int, detail: str) -> Violation:
    line, col = ctx.index.line_col(offset)
    return Violation(rule=rule, path=ctx.rel, line=line, column=col, detail=detail)


# ─── R1 禁渐变 / 禁材质 ────────────────────────────────────────

GRADIENT_TYPE_RE = re.compile(r"\b(?:Linear|Radial|Conical)?Gradient(?:Stop)?\b")
GRADIENT_PROP_RE = re.compile(r"(?<![\w.])gradient\s*:")
ACRYLIC_TYPE_RE = re.compile(r"(?<![\w.])FluAcrylic\b")
EFFECT_PROP_RE = re.compile(r"""(?<![\w.])effect\s*:\s*(['\"])([^'\"]*)\1""")
WINDOW_EFFECT_RE = re.compile(r"""windowEffect\s*(?:=|:)\s*(['\"])([^'\"]*)\1""")

#: 纯色无材质的取值（阶段 0 第 2.4 节实测：设为 normal 即无渐变/噪声）
MATERIAL_NORMAL = "normal"


def check_r1(project: ProjectInfo) -> Checked:
    """R1：渐变类型 / `gradient:` / 亚克力与 Mica 材质。"""
    out: List[Violation] = []
    files: List[str] = []
    for ctx in project.qml_ctx:
        if ctx.kind not in ("qml", "js") or ctx.scanned is None:
            continue
        files.append(ctx.rel)
        for m in GRADIENT_TYPE_RE.finditer(ctx.code):
            out.append(_violation("R1", ctx, m.start(), f"出现渐变类型 {m.group(0)}（项目 UI 规范：永不使用渐变）"))
        for m in GRADIENT_PROP_RE.finditer(ctx.code):
            out.append(_violation("R1", ctx, m.start(), "出现 gradient: 属性（项目 UI 规范：永不使用渐变）"))
        for m in ACRYLIC_TYPE_RE.finditer(ctx.code):
            out.append(_violation("R1", ctx, m.start(), "使用了 FluAcrylic 亚克力材质（阶段 0 第 2.4 节：材质必须关闭）"))
        for m in EFFECT_PROP_RE.finditer(ctx.without_comments):
            value = m.group(2).strip()
            if value.lower() == MATERIAL_NORMAL:
                continue
            out.append(_violation("R1", ctx, m.start(),
                                  f'窗口材质 effect: "{value}" —— 必须显式写成 effect: "normal"（纯色）'))
        for m in WINDOW_EFFECT_RE.finditer(ctx.without_comments):
            value = m.group(2).strip()
            if value.lower() == MATERIAL_NORMAL:
                continue
            out.append(_violation("R1", ctx, m.start(),
                                  f'windowEffect 被设成 "{value}" —— 只能是 "normal"'))
    return out, files, ""


# ─── R2 禁 emoji ───────────────────────────────────────────────


def _emoji_desc(cp: int) -> str:
    """命中 emoji 判定则返回人话描述，否则返回空串。"""
    if cp in _EMOJI_SINGLE:
        return _EMOJI_SINGLE[cp]
    # 本项目当图标用的文本符号（`Emoji_Presentation=No`，区间判据看不见它们）
    if cp in _EMOJI_AS_ICON_SINGLES:
        return _EMOJI_AS_ICON_SINGLES[cp]
    for lo, hi in _EMOJI_BMP_RANGES + _EMOJI_SMP_RANGES:
        if lo <= cp <= hi:
            return unicodedata.name(chr(cp), f"U+{cp:04X}")
    return ""


def escaped_codepoints(text: str) -> Iterator[Tuple[int, int]]:
    """解出字面量里的 `\\uXXXX` / `\\u{...}` / `\\xXX`，产出 (偏移, 码点)。

    为什么必须解：`"\\u4e2d\\u6587"` 与 `"\\uD83D\\uDE00"` 是硬编码中文/emoji 的
    **等价写法**，只看原文会漏掉。代理对必须拼起来，否则解出两个孤立代理码位，
    既不在 emoji 区间也不在 CJK 区间 —— 正好从两个规则底下溜过去。
    """
    matches = list(_ESCAPE_RE.finditer(text))
    i = 0
    while i < len(matches):
        m = matches[i]
        cp = int(m.group(1) or m.group(2) or m.group(3), 16)
        if 0xD800 <= cp <= 0xDBFF and i + 1 < len(matches):
            nxt = matches[i + 1]
            low = int(nxt.group(1) or nxt.group(2) or nxt.group(3), 16)
            if 0xDC00 <= low <= 0xDFFF:
                cp = 0x10000 + ((cp - 0xD800) << 10) + (low - 0xDC00)
                i += 1
        yield m.start(), cp
        i += 1


def _py_char_offset(ctx: FileCtx, line: int, col_bytes: int) -> int:
    """(行, UTF-8 字节列) → 字符偏移。`ast` 的 `col_offset` 是**字节**偏移。"""
    prefix = ctx.index.line_text(line).encode("utf-8")[:col_bytes]
    return ctx.index.line_start(line) + len(prefix.decode("utf-8", errors="ignore"))


def _py_offset(ctx: FileCtx, node: ast.AST) -> int:
    return _py_char_offset(ctx, getattr(node, "lineno", 1) or 1, getattr(node, "col_offset", 0) or 0)


def ensure_tree(ctx: FileCtx) -> Optional[ast.Module]:
    """惰性解析 Python 桥文件（解析失败返回 None，由调用方决定怎么报）。"""
    if ctx.tree is not None:
        return ctx.tree
    try:
        ctx.tree = ast.parse(ctx.text, filename=ctx.rel)
    except SyntaxError:
        return None
    return ctx.tree


def py_literal_sources(ctx: FileCtx) -> Iterator[Tuple[int, str]]:
    """产出 Python 字符串字面量的 `(起始字符偏移, 源码片段)`。

    为什么必须拿**源码片段**而不是 `node.value`：Python 在解析时就把 `\\uXXXX`
    解成了真字符，看值只能看到解好的结果 —— 而"转义写法的 emoji/中文"与直接写
    是等价的，R2 要判的是"文件里出现了 emoji"，所以只能看原文。
    """
    if ctx.tree is None:
        return
    for node in ast.walk(ctx.tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        end_line = getattr(node, "end_lineno", None)
        end_col = getattr(node, "end_col_offset", None)
        if end_line is None or end_col is None:
            continue
        start = _py_offset(ctx, node)
        raw = ctx.text[start:_py_char_offset(ctx, end_line, end_col)]
        if raw[:1] in ("r", "R"):  # 原始字符串不解释转义
            continue
        yield start, raw


def check_r2(project: ProjectInfo) -> Checked:
    """R2：QML / JS / SVG / 桥里的 emoji 码点（注释也算，含转义写法）。"""
    out: List[Violation] = []
    files: List[str] = []
    for ctx in list(project.qml_ctx) + list(project.py_ctx):
        files.append(ctx.rel)
        for offset, ch in enumerate(ctx.text):
            desc = _emoji_desc(ord(ch))
            if desc:
                out.append(_violation("R2", ctx, offset,
                                      f"出现 emoji 码点 U+{ord(ch):04X}（{desc}）—— 图标一律用 qml/assets/icons 下的 SVG"))
        if ctx.kind == "py":
            if ensure_tree(ctx) is None:
                continue  # 解析不了的文件由 R7 报语法错误，这里不重复刷屏
            for start, raw in py_literal_sources(ctx):
                for esc_off, cp in escaped_codepoints(raw):
                    desc = _emoji_desc(cp)
                    if desc:
                        out.append(_violation("R2", ctx, start + esc_off,
                                              f"用转义写法藏起来的 emoji（解码 U+{cp:04X}：{desc}）"))
        elif ctx.scanned is not None:
            for span in ctx.scanned.strings:
                for esc_off, cp in escaped_codepoints(span.text):
                    desc = _emoji_desc(cp)
                    if desc:
                        out.append(_violation("R2", ctx, span.inner_start + esc_off,
                                              f"用转义写法藏起来的 emoji（解码 U+{cp:04X}：{desc}）"))
    return out, files, ""


# ─── R3 禁硬编码中文（只判字符串字面量） ────────────────────────


def _char_class(ranges: Sequence[Tuple[int, int]]) -> str:
    return "".join(f"\\U{lo:08x}-\\U{hi:08x}" for lo, hi in ranges)


CJK_RE = re.compile("[" + _char_class(_CJK_RANGES) + "]")


def _is_cjk(cp: int) -> bool:
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def check_r3(project: ProjectInfo) -> Checked:
    """R3：字符串字面量里的 CJK 判违规；注释里的中文一律放行。

    注释不算违规是**刻意的**：本项目的注释本来就是中文，把注释算进去会让闸门
    从第一天起就永远红（而永远红的闸门等于没有闸门）。这里的难点全在"分清
    字面量与注释"，由 `scan_source()` 的单趟扫描负责。
    """
    out: List[Violation] = []
    files: List[str] = []
    for ctx in project.qml_ctx:
        if ctx.kind not in ("qml", "js") or ctx.scanned is None:
            continue
        files.append(ctx.rel)
        for span in ctx.scanned.strings:
            hit = CJK_RE.search(span.text)
            if hit is not None:
                out.append(_violation("R3", ctx, span.inner_start + hit.start(),
                                      f"字符串字面量里有硬编码文本「{source_excerpt(span.text)}」—— "
                                      '界面文案必须走 i18n：绑定写 Tr.map["键"]、命令式写 Tr.t("键")'))
            for esc_off, cp in escaped_codepoints(span.text):
                if _is_cjk(cp):
                    out.append(_violation("R3", ctx, span.inner_start + esc_off,
                                          f"用转义写法写死的中文（解码 U+{cp:04X}）—— 同样要走 i18n 键"))
        for m in CJK_RE.finditer(ctx.code):
            out.append(_violation("R3", ctx, m.start(),
                                  f"字符串与注释之外出现裸 CJK「{m.group(0)}」—— 只可能是正则字面量或中文标识符"))
    return out, files, ""


# ─── R4 i18n 绑定 ──────────────────────────────────────────────

TR_T_RE = re.compile(r"\bTr\b\s*\.\s*t\s*\(")
BINDING_KEY_RE = re.compile(r"^\s*([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*:")
#: `property [readonly|default|required] <类型> <名字>:` —— 类型段写成可选，
#: 且**不能**用惰性 `[^:]*?`：那会让 `property string info:` 里的名字匹配成 `o`
#: （惰性段一路吃到 `info` 的最后一个字符，剩一个字符刚好满足 `\w*`）。
PROPERTY_DECL_RE = re.compile(
    r"^\s*(?:readonly\s+|default\s+|required\s+)*property\s+"
    r"(?:[A-Za-z_][\w.<>\[\],]*\s+)?([A-Za-z_]\w*)\s*:"
)
HANDLER_KEY_RE = re.compile(r"^on[A-Z]")
STATEMENT_DELIMS = "{};"

#: 这些词后面的 `{` 一定是命令式 JS 块（不是 QML 对象体）。
#: `return {` 尤其重要：多行 JS 对象字面量 `return {\n  label: Tr.t("x")\n}` 里的
#: `label:` 长得和 QML 绑定一模一样，不把它定性成 JS 就会误报。
JS_OPENER_WORDS = (
    "try", "else", "do", "finally", "function", "catch", "return", "throw", "yield",
    "await", "case", "new", "typeof", "delete", "void", "in", "of", "instanceof",
)


def _last_delimiter(text: str, lo: int, hi: int) -> int:
    """[lo, hi) 里最后一个 `;` `{` `}` 的位置；没有则 -1。"""
    for k in range(min(hi, len(text)) - 1, max(lo, 0) - 1, -1):
        if text[k] in STATEMENT_DELIMS:
            return k
    return -1


def _enclosing_brace(text: str, offset: int) -> int:
    """offset 之前最近的"未配对 `{`"（找不到返回 -1）。只看抹掉字符串/注释的视图。"""
    depth = 0
    for k in range(offset - 1, -1, -1):
        ch = text[k]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                return k
            depth -= 1
    return -1


def _word_before(text: str, pos: int) -> str:
    k = pos
    while k >= 0 and (text[k].isalnum() or text[k] == "_"):
        k -= 1
    return text[k + 1:pos + 1]


def _key_before_colon(text: str, colon: int) -> str:
    k = colon - 1
    while k >= 0 and text[k] in " \t":
        k -= 1
    end = k + 1
    while k >= 0 and (text[k].isalnum() or text[k] in "_."):
        k -= 1
    return text[k + 1:end]


def _is_handler_key(key: str) -> bool:
    """`onClicked` / `Component.onCompleted` 这类信号处理器键。"""
    return bool(HANDLER_KEY_RE.match(key.split(".")[-1]))


def _opener_verdict(text: str, open_pos: int) -> Tuple[str, str]:
    """给一个 `{` 定性：`js` = 命令式 JS（块/对象字面量）；`qml` = QML 对象体或绑定块。

    这是 R4 的核心判据：QML 里 `{` 有三种身份，分错就会误报或漏报 ——
    `= {`/`( {`/`, {` 是 JS 对象字面量（命令式）、`onXxx: {` 是信号处理器（命令式）、
    其余 `名: {` 是绑定块或 QML 对象体（绑定语境）。
    """
    k = open_pos - 1
    while k >= 0 and text[k] in " \t\r\n":
        k -= 1
    if k < 0:
        return "js", "文件开头"
    ch = text[k]
    if ch in "([,=":
        return "js", "JS 对象/数组字面量或赋值右侧"
    if ch == ">":
        return "js", "箭头函数体"
    if ch == ")":
        return "js", "函数/方法体"
    if ch == ":":
        key = _key_before_colon(text, k)
        if key and _is_handler_key(key):
            return "js", f"信号处理器 {key}"
        return "qml", f"绑定块 {key}"
    word = _word_before(text, k)
    if word in JS_OPENER_WORDS:
        return "js", f"{word} 块"
    return "qml", f"QML 对象体 {word}"


def _leading_key(segment: str) -> Optional[str]:
    """语句片段形如 `键: 值` 时返回键，否则 None。

    防误报的两道闸门：片段里出现逗号/分号就放弃 —— JS 对象字面量
    `{ label: Tr.t(…), value: 1 }` 里的 `label` 不是 QML 绑定。
    """
    m = PROPERTY_DECL_RE.match(segment)
    if m is not None:
        return m.group(1)
    m = BINDING_KEY_RE.match(segment)
    if m is None:
        return None
    rest = segment[m.end():]
    if "," in rest or ";" in rest:
        return None
    return m.group(1)


def _statement_key(masked: str, index: SourceIndex, offset: int) -> Optional[str]:
    """找 offset 所在语句的宿主键（`键:` 里的键）；找不到返回 None（= 命令式）。"""
    line, _ = index.line_col(offset)
    line_start = index.line_start(line)
    delim = _last_delimiter(masked, line_start, offset)
    # 本行没有分隔符时，片段从**行首**起算 —— 不能从文件头起算：那样 `^\s*键:` 会
    # 落在上一个语句上，把宿主键认错（实测踩过：把上一行的 text 当成本行 property 的键）。
    seg_start = delim + 1 if delim >= 0 else line_start
    key = _leading_key(masked[seg_start:offset])
    if key is not None:
        return key
    # 跨行的情况：向上逐行回溯，走到所在 `{` 的那一行为止
    # （那一行本身要看：`键: {` 这种**块体绑定**的宿主键就写在那里）
    opener = _enclosing_brace(masked, offset)
    opener_line = index.line_col(opener)[0] if opener >= 0 else 0
    j = line - 1
    while j >= 1:
        if opener >= 0 and j < opener_line:
            break
        segment = masked[index.line_start(j):index.line_end(j)]
        key = _leading_key(segment)
        if key is not None:
            return key
        if segment.strip().endswith(("{", "}", ";")):
            break
        j -= 1
    return None


def binding_owner(ctx: FileCtx, offset: int) -> Optional[str]:
    """判断 offset 处的 `Tr.t(` 是否处在绑定表达式里；是则返回宿主键，否则 None。"""
    masked = ctx.code
    if "Qt.binding" in masked[max(0, offset - 300):offset]:
        return "Qt.binding(...)"  # 显式造绑定，不看上下文
    opener = _enclosing_brace(masked, offset)
    if opener >= 0:
        verdict, _why = _opener_verdict(masked, opener)
        if verdict == "js":
            return None
    key = _statement_key(masked, ctx.index, offset)
    if key is not None and _is_handler_key(key):
        # `onClicked: foo(Tr.t(…))` —— 处理器绑在表达式上同样是命令式，不是绑定
        return None
    return key


def check_r4(project: ProjectInfo) -> Checked:
    """R4：绑定表达式里的 `Tr.t(`。命令式 JS 里的 `Tr.t(` 是允许的。"""
    out: List[Violation] = []
    files: List[str] = []
    for ctx in project.qml_ctx:
        if ctx.kind != "qml" or ctx.scanned is None:  # .js 里没有属性绑定，不判
            continue
        files.append(ctx.rel)
        for m in TR_T_RE.finditer(ctx.code):
            owner = binding_owner(ctx, m.start())
            if owner is None:
                continue
            out.append(_violation("R4", ctx, m.start(),
                                  f"绑定表达式里出现 Tr.t(（宿主键 {owner}）—— 绑定只跟踪属性读取、"
                                  '不跟踪函数返回值，语言切换时不会重算；请改成 Tr.map["键"]'))
    return out, files, ""


# ─── R5 违禁组件 / 越界 import ─────────────────────────────────

#: 仓库里已知的 Python 顶层模块/包名（QML 不得直接 import）。
#: 用固定清单而不是"看磁盘上有什么"：fixture 目录里没有这些包，
#: 靠扫磁盘的话这条规则在负例上永远判不出来（等于没测过）。
PY_TOP_LEVELS: Tuple[str, ...] = (
    "achievement_defs", "achievement_engine", "achievement_sync", "api", "app",
    "backup_manager", "config", "curseforge", "download_config", "downloader", "launcher",
    "main", "minecraft_launcher_lib", "mirror", "models", "modrinth", "plugin_manager",
    "secure_storage", "services", "structured_logger", "third_party", "trees", "ui",
    "updater", "validation", "version_utils",
)

#: QML 允许直接 import 的 Python 侧名字（其余一律违规）
ALLOWED_PY_IMPORTS: Tuple[str, ...] = ("services", "app.bridges")

IMPORT_RE = re.compile(
    r"""^[ \t]*import[ \t]+(?P<uri>"[^"\n]*"|'[^'\n]*'|[A-Za-z_][\w.]*)(?:[ \t]+(?P<ver>[\d.]+))?""",
    re.MULTILINE,
)
LOCAL_TYPE_USE_RE = re.compile(r"(?<![\w.])([A-Z][A-Za-z0-9_]*)[ \t]*\{")


def load_component_whitelist(base: Path) -> Tuple[Optional[Set[str]], str]:
    """读 `qml/components/COMPONENTS.md` 的组件白名单。

    解析刻意宽容（反引号标识符 / 表格首列 / `- Name` 列表项），免得文档写法一变
    闸门就静默失效。返回 `(白名单, 说明)`：说明非空 = 本条子判定降级为跳过。
    """
    path = base / COMPONENTS_WHITELIST
    if not path.exists():
        return None, (f"{COMPONENTS_WHITELIST} 还不存在（阶段 2 的 2.16 才产出）—— "
                      "R5 的「白名单外自研组件」判定本次跳过，文件一落地就自动生效")
    text = load_text(path) or ""
    names: Set[str] = set()
    for found in re.finditer(r"`([A-Za-z_][A-Za-z0-9_]*)`", text):
        names.add(found.group(1))
    for line in text.splitlines():
        stripped = line.strip()
        match = re.match(r"^[-*]\s+([A-Z][A-Za-z0-9_]*)", stripped)
        if match is None:
            match = re.match(r"^\|\s*([A-Z][A-Za-z0-9_]*)\s*\|", stripped)
        if match is not None:
            names.add(match.group(1))
    names = {n for n in names if n[:1].isupper()}
    if not names:
        return None, f"{COMPONENTS_WHITELIST} 里没解析出任何组件名 —— R5 的该判定本次跳过"
    return names, ""


def check_r5(project: ProjectInfo) -> Checked:
    """R5：页面越界 import Python + 使用白名单外的自研组件。"""
    out: List[Violation] = []
    files: List[str] = []
    for ctx in project.pages_ctxs():
        files.append(ctx.rel)
        for m in IMPORT_RE.finditer(ctx.without_comments):
            uri = m.group("uri").strip()
            if uri.startswith(("'", '"')):
                continue  # 相对目录 / 资源 / JS 文件，不经 Python
            if uri.split(".")[0] not in PY_TOP_LEVELS:
                continue  # Qt / FluentUI / FMCL 等 QML 模块
            if any(uri == a or uri.startswith(a + ".") for a in ALLOWED_PY_IMPORTS):
                continue
            out.append(_violation("R5", ctx, m.start(),
                                  f"页面直接 import 了 Python 模块 {uri} —— "
                                  "QML 只能通过 app.bridges 暴露的桥访问 Python（契约第三节红线 1）"))
        if project.whitelist is None:
            continue
        for m in LOCAL_TYPE_USE_RE.finditer(ctx.code):
            name = m.group(1)
            if name not in project.local_types or name in project.whitelist:
                continue
            out.append(_violation("R5", ctx, m.start(),
                                  f"使用了白名单外的自研组件 {name}（定义在 {project.local_types[name]}）—— "
                                  "阶段 3 只允许用 qml/components/ 白名单里的件；"
                                  "确需例外请登记 REGISTERED_EXCEPTIONS"))
    return out, files, project.whitelist_note


# ─── R6 悬浮窗不得用 transparent ───────────────────────────────

TRANSPARENT_COLOR_RE = re.compile(
    r"""(?<![\w.])color\s*:\s*(?:"\s*transparent\s*"|'\s*transparent\s*'|"""
    r""""#00000000"|'#00000000'|"#0000"|'#0000')""",
    re.IGNORECASE,
)
CLEAR_RGBA_RE = re.compile(r"Qt\s*\.\s*rgba\s*\(\s*0\s*,\s*0\s*,\s*0\s*,\s*0\s*\)")


def check_r6(project: ProjectInfo) -> Checked:
    """R6：`qml/overlays/**` 里的整窗透明。"""
    out: List[Violation] = []
    files: List[str] = []
    for ctx in project.overlays_ctxs():
        files.append(ctx.rel)
        for m in TRANSPARENT_COLOR_RE.finditer(ctx.without_comments):
            out.append(_violation("R6", ctx, m.start(),
                                  '悬浮窗里出现 color: "transparent"（或 "#00000000"）—— '
                                  "无合成器的 X11 下会黑屏；整窗透明用 Window.opacity、底色用实色"
                                  "（阶段 0 第 12.5 节 / 契约第六节决策 5）"))
        for m in CLEAR_RGBA_RE.finditer(ctx.without_comments):
            out.append(_violation("R6", ctx, m.start(),
                                  "悬浮窗里出现 Qt.rgba(0, 0, 0, 0)（与 transparent 同一个坑）—— 请改实色"))
    return out, files, ""


# ─── R7 桥的线程红线 ───────────────────────────────────────────

#: worker 函数体里禁止调用的方法名（都落在"碰引擎 / 直接写 QObject"这条红线上）
WORKER_FORBIDDEN_CALLS: Tuple[str, ...] = (
    "rootObjects", "rootContext", "setContextProperty", "createComponent", "setProperty",
)

#: 对"名字里含 engine"的对象，worker 里还禁止这些动作
ENGINE_FORBIDDEN_ACTIONS: Tuple[str, ...] = ("load", "quit", "addImportPath", "setIncubationController")


def _dotted_name(node: ast.AST) -> str:
    """把 `a.b.c` 还原成字符串；不是纯名字链则返回空串。"""
    parts: List[str] = []
    cur: ast.AST = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""


def _callee_dotted(node: ast.Call) -> str:
    return _dotted_name(node.func)


def _assign_targets(node: ast.AST) -> List[ast.AST]:
    if isinstance(node, ast.Assign):
        return list(node.targets)
    if isinstance(node, (ast.AugAssign, ast.AnnAssign)):
        return [node.target]
    return []


def resolve_callable(node: ast.AST) -> str:
    """把 `target=` / `submit(…)` 的实参还原成函数名；还原不了返回空串。"""
    if isinstance(node, (ast.Name, ast.Attribute)):
        dotted = _dotted_name(node)
        return dotted.split(".")[-1] if dotted else ""
    if isinstance(node, ast.Call) and _callee_dotted(node).split(".")[-1] == "partial" and node.args:
        return resolve_callable(node.args[0])
    return ""


def worker_function_names(tree: ast.Module) -> Set[str]:
    """收集被当成 worker 跑的函数名。

    只按名字解析（`self._run` → `_run`）；解析不到就不判 —— 宁可漏判，
    也不要把普通函数误判成 worker（R7 的误报会让整条闸门被绕过）。
    """
    names: Set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _callee_dotted(node)
        if not callee:
            continue
        last = callee.split(".")[-1]
        target: Optional[ast.AST] = None
        if last == "Thread":
            target = next((k.value for k in node.keywords if k.arg == "target"), None)
            if target is None and node.args:
                target = node.args[0]
        elif last == "submit":
            target = next((k.value for k in node.keywords if k.arg in ("fn", "func")), None)
            if target is None and node.args:
                target = node.args[0]
        if target is None:
            continue
        name = resolve_callable(target)
        if name:
            names.add(name)
    return names


def qt_visible_names(tree: ast.Module) -> Set[str]:
    """类体里声明的 Qt 可见名字：`Q_PROPERTY` 属性名、`@Property` 函数名、`Signal` 名。"""
    names: Set[str] = set()
    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for stmt in cls.body:
            if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
                if _callee_dotted(stmt.value) == "Q_PROPERTY" and stmt.value.args:
                    arg = stmt.value.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        tokens = arg.value.split()
                        if len(tokens) >= 2:
                            names.add(tokens[1])
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in stmt.decorator_list:
                    dec_name = _callee_dotted(dec) if isinstance(dec, ast.Call) else _dotted_name(dec)
                    if dec_name.split(".")[-1] == "Property":
                        names.add(stmt.name)
            elif isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
                callee = _callee_dotted(stmt.value).split(".")[-1]
                if callee == "Signal":
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name):
                            names.add(tgt.id)
                elif callee == "Property":
                    # PySide6 的常见写法：count = Property(int, _get, notify=countChanged)
                    # 以及少见的 Q_PROPERTY("int count READ ...") 字符串形式
                    for tgt in stmt.targets:
                        if isinstance(tgt, ast.Name):
                            names.add(tgt.id)
                    if stmt.value.args:
                        first = stmt.value.args[0]
                        if isinstance(first, ast.Constant) and isinstance(first.value, str):
                            tokens = first.value.split()
                            if len(tokens) >= 2:
                                names.add(tokens[1])
    return names


def _r7_invoke_method(ctx: FileCtx, tree: ast.Module) -> List[Violation]:
    """无条件项：桥里出现 `QMetaObject.invokeMethod` 即违规（决策 10）。"""
    out: List[Violation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _callee_dotted(node).endswith("QMetaObject.invokeMethod"):
            out.append(_violation("R7", ctx, _py_offset(ctx, node),
                                  "出现 QMetaObject.invokeMethod —— 契约第六节决策 10："
                                  "var 参数签名不匹配会静默失败，Python→QML 一律发信号"))
    return out


def _r7_worker_body(ctx: FileCtx, tree: ast.Module) -> List[Violation]:
    """worker 函数体内：写 QML 可见属性 / 碰引擎。"""
    targets = worker_function_names(tree)
    if not targets:
        return []
    qt_names = qt_visible_names(tree)
    out: List[Violation] = []
    for func in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if func.name not in targets:
            continue
        for node in ast.walk(func):
            for target in _assign_targets(node):
                base = target.value if isinstance(target, ast.Subscript) else target
                name = _dotted_name(base)
                if not name:
                    continue
                last = name.split(".")[-1]
                if name.startswith("self.") and last in qt_names:
                    inplace = "（原地改列表/字典）" if isinstance(target, ast.Subscript) else ""
                    out.append(_violation("R7", ctx, _py_offset(ctx, node),
                                          f"worker 函数 {func.name}() 里直接写 QML 可见属性 self.{last}{inplace}"
                                          " —— 跨线程一律 emit 信号（Qt 会自动 Queued 投递）"))
                elif "engine" in name.lower():
                    out.append(_violation("R7", ctx, _py_offset(ctx, node),
                                          f"worker 函数 {func.name}() 里给引擎相关对象赋值（{name}）"
                                          " —— 工作线程绝不碰 QML 引擎"))
            if isinstance(node, ast.Call):
                callee = _callee_dotted(node)
                if not callee:
                    continue
                last = callee.split(".")[-1]
                if last in WORKER_FORBIDDEN_CALLS:
                    out.append(_violation("R7", ctx, _py_offset(ctx, node),
                                          f"worker 函数 {func.name}() 里调用 {callee}() —— "
                                          "工作线程绝不触碰 QML 引擎 / QObject 属性（契约第三节红线 3）"))
                elif last in ENGINE_FORBIDDEN_ACTIONS and "engine" in callee.lower():
                    out.append(_violation("R7", ctx, _py_offset(ctx, node),
                                          f"worker 函数 {func.name}() 里调用 {callee}() —— 引擎操作只能在主线程"))
    return out


def check_r7(project: ProjectInfo) -> Checked:
    """R7：`app/bridges/**/*.py` 的线程红线。"""
    out: List[Violation] = []
    files: List[str] = []
    for ctx in project.py_ctx:
        files.append(ctx.rel)
        tree = ensure_tree(ctx)
        if tree is None:
            # 解析失败时才知道原因，这里再解析一次拿报错信息（成本只落在坏文件上）
            try:
                ast.parse(ctx.text, filename=ctx.rel)
            except SyntaxError as e:
                out.append(Violation("R7", ctx.rel, e.lineno or 1, e.offset or 1,
                                     f"桥文件无法解析（{e.msg}）—— 语法先修好，线程红线才判得动"))
            continue
        out.extend(_r7_invoke_method(ctx, tree))
        out.extend(_r7_worker_body(ctx, tree))
    return out, files, ""


# ─── R8 禁颜色字面量（缺陷 D-102 / 风险 R-15） ─────────────────

#: R8 只扫这两种后缀。`.svg` **故意不扫**：图标资源天生带 `fill="#ffffff"`。
R8_SUFFIXES: Tuple[str, ...] = (".qml", ".js")

#: 颜色字面量：`#rgb` / `#rgba` / `#rrggbb` / `#aarrggbb`（大小写不敏感）。
#: 长的写在前面（正则交替按左到右匹配），末尾用 `(?![0-9a-fA-F])` 防止把 8 位切成 6 位；
#: 前面用 `(?<![\w#])` 防止匹配到 `abc#123456` 这种粘在标识符/URL 上的片段。
COLOR_LITERAL_RE = re.compile(
    r"(?<![\w#])#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{4}|[0-9a-fA-F]{3})(?![0-9a-fA-F])"
)

#: `Qt.rgba(...)`（允许点号两侧有空格；大小写不敏感，Qt/Qt 的别名 qml 也一样）。
RGBA_LITERAL_RE = re.compile(r"(?<![\w.])[Qq]t\s*\.\s*rgba\s*\(([^()]*)\)")


def _r8_is_transparent_hex(literal: str) -> bool:
    """是全透明的十六进制写法吗（`#0000` / `#00rrggbb`）。

    全透明**不是**"选了一个颜色"，而是布局手段 —— 这类写法归 R6（且只在
    `qml/overlays/**` 下判），R8 不重复报，免得两条规则的负例互相污染。
    """
    body = literal.lstrip("#").lower()
    if len(body) == 4:
        return body == "0000"
    if len(body) == 8:
        return body.startswith("00")
    return False


def _r8_is_transparent_rgba(args: str) -> bool:
    """`Qt.rgba(r, g, b, a)` 的 a 是不是 0（是则与 `transparent` 等价，归 R6）。"""
    parts = [part.strip() for part in args.split(",")]
    if len(parts) != 4:
        return False
    try:
        return float(parts[3]) == 0.0
    except ValueError:
        return False


def check_r8(project: ProjectInfo) -> Checked:
    """R8：`.qml` / `.js` 里不得出现颜色字面量（颜色只能来自 `Theme.*` 或设计令牌）。

    判据写在 `no_comments` 视图上：**注释里的颜色值不算**（注释正是该写清
    "这个色原来是 #1a1a2e" 的地方），字符串里的算 —— QML 的颜色字面量本来就写在字符串里。
    """
    out: List[Violation] = []
    files: List[str] = []
    for ctx in project.qml_ctx:
        if not ctx.rel.endswith(R8_SUFFIXES):
            continue
        files.append(ctx.rel)
        text = ctx.without_comments
        for m in COLOR_LITERAL_RE.finditer(text):
            literal = m.group(0)
            if _r8_is_transparent_hex(literal):
                continue
            out.append(_violation(
                "R8", ctx, m.start(),
                f"出现硬编码颜色 {literal} —— 颜色必须来自 Theme.*（或 Theme.fontSize* 之类的设计"
                "令牌），否则切主题时这里不跟随（缺陷 D-102 / 风险 R-15）；"
                "确实需要固定色请在 REGISTERED_EXCEPTIONS 登记并写明任务号",
            ))
        for m in RGBA_LITERAL_RE.finditer(text):
            if _r8_is_transparent_rgba(m.group(1)):
                continue
            out.append(_violation(
                "R8", ctx, m.start(),
                f"出现硬编码颜色 {source_excerpt(m.group(0))} —— 同上，请改用 Theme.* 里的同名键",
            ))
    return out, files, ""


#: 规则号 → 检查函数
CHECKERS: Dict[str, Callable[[ProjectInfo], Checked]] = {
    "R1": check_r1,
    "R2": check_r2,
    "R3": check_r3,
    "R4": check_r4,
    "R5": check_r5,
    "R6": check_r6,
    "R7": check_r7,
    "R8": check_r8,
}


# ─── 汇总：跑规则 + 分流已登记例外 ─────────────────────────────


@dataclass
class GateReport:
    """一次运行的全部结果。"""

    base: Path
    results: List[RuleResult] = field(default_factory=list)
    expired: List[Tuple[str, str, str]] = field(default_factory=list)  # (键, 理由, 任务号)
    notes: List[str] = field(default_factory=list)
    qml_files: int = 0
    bridge_files: int = 0
    registered_count: int = 0

    @property
    def violations(self) -> List[Violation]:
        return [v for result in self.results for v in result.violations]

    @property
    def ok(self) -> bool:
        return not self.violations and not self.expired


def run_gate(base: Path, only: Optional[Sequence[str]] = None) -> GateReport:
    """跑（指定）规则，把每处违规按 `REGISTERED_EXCEPTIONS` 分流成"未登记/已登记"。"""
    base = base.resolve()
    project = load_project(base)
    report = GateReport(base=base, qml_files=len(project.qml_ctx), bridge_files=len(project.py_ctx))
    for ctx in list(project.qml_ctx) + list(project.py_ctx):
        for note in ctx.notes:
            report.notes.append(f"{ctx.rel}: {note}")
    if not project.qml_ctx:
        report.notes.append(
            f"{QML_ROOT}/ 下目前一个 QML/JS 文件都没有（阶段 2 的 B/C/D 组才会开始写入）—— "
            "R1/R3/R4/R5/R6 的判定范围为空，本次不产生任何结论；闸门的判定能力由 "
            "poc/qml_rules_fixtures/ 下的负例证明（见 tests/test_qml_rules_gate.py）")

    active = [rid for rid in RULE_IDS if only is None or rid in only]
    used_keys: Set[str] = set()
    for rule in active:
        checker = CHECKERS[rule]
        raw, files, note = checker(project)
        result = RuleResult(rule=rule, files=files, note=note)
        for violation in raw:
            hit = REGISTERED_EXCEPTIONS.get(violation.key)
            if hit is None:
                result.violations.append(violation)
                continue
            reason, task = hit
            used_keys.add(violation.key)
            report.registered_count += 1
            result.registered.append((violation, reason, task))
        report.results.append(result)

    # 登记项过期检查（反向守卫）：登记了却一次都没命中 → 报错。
    # 只对**本次真的跑过**的规则报过期 —— 否则 `--rule R3` 会把别的规则的登记误判成过期。
    for key in sorted(REGISTERED_EXCEPTIONS):
        if key in used_keys:
            continue
        reason, task = REGISTERED_EXCEPTIONS[key]
        matched = _REGISTRY_KEY_RE.match(key)
        if matched is None:
            report.expired.append((key, f"登记键形状非法（应形如 qml/App.qml:42:R4）—— 永远不可能命中。{reason}", task))
            continue
        if matched.group(1) not in active:
            continue
        report.expired.append((key, reason, task))
    return report


# ─── 输出 ──────────────────────────────────────────────────────


def render_human(report: GateReport, show_ok: bool = False, strict: bool = False) -> int:
    """人读输出；返回退出码。"""
    print("=" * 88)
    print(f"QML 规则闸门（阶段 2 任务 2.7）  base = {report.base}")
    print(f"扫描范围：{QML_ROOT}/ {report.qml_files} 个文件（.qml/.js/.svg）、"
          f"{BRIDGES_ROOT}/ {report.bridge_files} 个 .py")
    print("=" * 88)

    for result in report.results:
        print(f"\n[{result.rule}] {RULE_LABELS[result.rule]}")
        print(f"  扫描 {len(result.files)} 个文件")
        if show_ok:
            for rel in result.files:
                print(f"  OK   {rel}")
        if result.note:
            print(f"  [跳过] {result.note}")
        by_file: Dict[str, List[Violation]] = {}
        for violation in result.violations:
            by_file.setdefault(violation.path, []).append(violation)
        for path, items in by_file.items():
            print(f"  FAIL {path}")
            for violation in items:
                print(violation.render())
        for violation, reason, task in result.registered:
            print(f"  [REGISTERED] {violation.path}:{violation.line}:{violation.column}  [{violation.rule}] "
                  f"{violation.detail}")
            print(f"        理由: {reason}")
            print(f"        归属任务: {task}")
        parts = ["通过" if not result.violations else f"失败（{len(result.violations)} 处未登记）"]
        if result.registered:
            parts.append(f"{len(result.registered)} 处已登记例外")
        print(f"  -> {'；'.join(parts)}")

    if report.notes:
        print("\n[说明]")
        for note in report.notes:
            print(f"  - {note}")

    if report.expired:
        print("\n[登记过期] 下列已登记例外一次都没命中 —— 说明它已经不需要了，请删掉登记：")
        for key, reason, task in report.expired:
            print(f"  [FAIL] {key}（归属任务 {task}）")
            print(f"        原理由: {reason}")

    print("\n" + "=" * 88)
    strict_fail = strict and report.registered_count > 0
    if report.ok and not strict_fail:
        print(f"QML 规则检查通过：{report.qml_files + report.bridge_files} 个文件、0 处未登记违规、"
              f"{report.registered_count} 处已登记例外")
        if report.registered_count:
            print("已登记例外见 --list-known；各自归属任务完成后必须移除登记。")
        return 0
    print(f"QML 规则检查失败：{len(report.violations)} 处未登记违规、"
          f"{report.registered_count} 处已登记例外、{len(report.expired)} 条登记过期"
          + ("（--strict：已登记例外也算失败）" if strict_fail else ""))
    print("规则判据见 scripts/check_qml_rules.py 顶部文档；违规要修代码，不要往 REGISTERED_EXCEPTIONS 里加。")
    return 1


def render_json(report: GateReport, strict: bool = False) -> Dict[str, object]:
    """机器可读输出（键用英文，值用中文；`violations[].key` 就是登记表的键）。"""
    strict_failures = report.registered_count if strict else 0
    return {
        "base": str(report.base),
        "scanned": {"qml": report.qml_files, "bridges": report.bridge_files},
        "rules": [
            {
                "id": result.rule,
                "label": RULE_LABELS[result.rule],
                "files": result.files,
                "note": result.note,
                "violations": [v.as_json() for v in result.violations],
                "registered": [
                    {"violation": v.as_json(), "reason": reason, "task": task}
                    for v, reason, task in result.registered
                ],
            }
            for result in report.results
        ],
        "expired_registrations": [
            {"key": key, "reason": reason, "task": task} for key, reason, task in report.expired
        ],
        "notes": report.notes,
        "summary": {
            "violations": len(report.violations),
            "registered": report.registered_count,
            "expired": len(report.expired),
            "strict_failures": strict_failures,
            "ok": report.ok and strict_failures == 0,
        },
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="QML 规则闸门（阶段 2 任务 2.7）")
    parser.add_argument("--base", default=str(REPO_ROOT),
                        help="基准目录（默认仓库根；fixture 测试用 poc/qml_rules_fixtures/bad 之类）")
    parser.add_argument("--rule", choices=RULE_IDS, action="append",
                        help="只跑某条规则，可重复（如 --rule R3 --rule R4）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出（stdout 只有 JSON）")
    parser.add_argument("--list", action="store_true", help="打印规则与扫描范围后退出")
    parser.add_argument("--list-known", action="store_true", help="打印已登记例外后退出")
    parser.add_argument("--show-ok", action="store_true", help="连通过的文件也打印")
    parser.add_argument("--strict", action="store_true", help="已登记例外也算失败（阶段收口时用）")
    args = parser.parse_args(argv)

    base = Path(args.base)

    if args.list:
        for rule in RULE_IDS:
            print(f"[{rule}] {RULE_LABELS[rule]}")
        print(f"\n扫描根：{base / QML_ROOT}（.qml/.js/.svg）、{base / BRIDGES_ROOT}（.py）")
        print(f"组件白名单：{base / COMPONENTS_WHITELIST}")
        return 0

    if args.list_known:
        if not REGISTERED_EXCEPTIONS:
            print("当前没有任何已登记例外")
            return 0
        for key, (reason, task) in sorted(REGISTERED_EXCEPTIONS.items()):
            print(f"{key}\n  归属任务: {task}\n  理由: {reason}")
        print(f"\n共 {len(REGISTERED_EXCEPTIONS)} 条已登记例外", file=sys.stderr)
        return 0

    if not base.is_dir():
        print(f"基准目录不存在: {base}", file=sys.stderr)
        return 2

    report = run_gate(base, only=args.rule)
    if args.json:
        print(json.dumps(render_json(report, args.strict), ensure_ascii=False, indent=2))
        passed = report.ok and not (args.strict and report.registered_count)
        return 0 if passed else 1
    return render_human(report, show_ok=args.show_ok, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
