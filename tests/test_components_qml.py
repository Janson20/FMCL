"""组件库与 Gallery 的守卫（阶段 2 任务 2.16 / 2.17）。

## 这一组测试想钉住什么

组件库最容易"看起来齐了、其实不能用"：文件都在、Gallery 也打开了，但某个组件一实例化
就报错、`objectName` 缺失（Gallery 与冒烟测试靠它找件）、或者某个绑定漏了判空
（引擎析构时上下文属性先被清成 null，退出阶段刷一屏 `TypeError`）。所以分三层：

1. **静态层**（本进程，不需要 Qt）：每个 `Fm*.qml` 有稳定的 `objectName`、文件头写了
   "什么时候用它 / 什么时候不要用"、所有 `Theme.* / Tr.map[…] / Runtime.iconUrl()` 读取
   都判了空、都被登记进 `qml/components/COMPONENTS.md`；Gallery 里**每一个**组件都有
   `galleryItem_<组件名>` 包装。
2. **子进程层**（`PROBE`）：真引擎加载 Gallery、逐个组件建出来、逐个 `objectName` 核对、
   真的点几下断言状态变了、**加载期零 QML 报错**，并抓帧留证据。
   为什么必须子进程：`QQmlApplicationEngine` + FluentUI 的 `FluWindow` 在同一个进程里
   跨测试文件不安全（实测 `access violation`，见 `tests/test_shell_qml.py` 的模块文档）。
3. **负例自检**：故意引用不存在的组件、故意给非法颜色、故意漏 `Theme` 判空 ——
   三种都必须让对应的守卫**变红**，否则上面两层就是空断言。

## 白名单守卫（R5 的独立复核）

`qml/pages/**` 里用到的自研组件必须全部在 `COMPONENTS.md` 里 —— 这条闸门 R5 会判，
但本模块**独立再断言一次**（`test_r5_has_no_violations_...`），并且用变异测试证明
"白名单真的拦得住"（往副本里塞一个没登记的组件，R5 必须报）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

# 必须在 import PySide6 之前设好（本模块只在子进程里建 Qt 应用，见模块文档）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

QML_DIR = REPO_ROOT / "qml"
COMPONENTS_DIR = QML_DIR / "components"
COMPONENTS_DOC = COMPONENTS_DIR / "COMPONENTS.md"
GALLERY_PATH = QML_DIR / "pages" / "dev" / "Gallery.qml"
GATE_SCRIPT = REPO_ROOT / "scripts" / "check_qml_rules.py"

#: 组件清单：`qml/components/Fm*.qml`（Gallery 与白名单必须与它对齐）
COMPONENTS: List[str] = sorted(path.stem for path in COMPONENTS_DIR.glob("Fm*.qml"))

#: Qt 消息里出现这些子串 = QML 真的报错了（与 `tests/test_shell_qml.py` 同一套判据）
QML_ERROR_MARKERS = (
    "TypeError", "ReferenceError", "SyntaxError", "is not defined",
    "Unable to assign", "Cannot assign", "is not a type", "Cannot read property",
    "of null", "failed to load component", "Cannot override", "non-existent property",
)

#: 明确放行的 Qt 提示（与本任务无关，且**不是** QML 报错）
BENIGN_MARKERS = (
    "QFontDatabase: Cannot find font directory",
    "does not support propagateSizeHints",
    "No such file or directory",
    "Invalid image provider",
)


def qml_errors(messages: List[str]) -> List[str]:
    """从 Qt 消息里筛出真正的 QML 报错（放行平台级提示）。"""
    out = []
    for message in messages:
        if any(marker in message for marker in BENIGN_MARKERS):
            continue
        if any(marker in message for marker in QML_ERROR_MARKERS):
            out.append(message)
    return out


# ─── 复用闸门的扫描器（判据只能有一份来源） ────────────────────


def _load_gate() -> Any:
    """按路径加载 `scripts/check_qml_rules.py`（`scripts/` 不是包）。

    必须先登记进 `sys.modules`：`@dataclass` 处理类时会按 `cls.__module__` 反查
    （与 `tests/test_qml_rules_gate.py` / `test_shell_qml.py` 同一套路）。
    """
    cached = sys.modules.get("_fmcl_qml_gate_2_16")
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location("_fmcl_qml_gate_2_16", GATE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


def code_view(text: str) -> str:
    """抹掉字符串与注释的视图（闸门自己的单趟扫描器 —— 不抄第二份判据）。

    为什么要抹：本任务的 QML 注释里到处是 `Theme.*` 这种写法（那正是该说明的地方），
    直接用正则扫原文会把注释里的示例当成"漏判空的绑定"。
    """
    gate = _load_gate()
    return gate.scan_source(text, gate.SourceIndex(text)).masked


#: 上下文属性读取：这些桥在引擎析构时**先被清成 null**，不判空就每个绑定刷一条 TypeError
CONTEXT_PROPS = ("Theme", "Tr", "Runtime", "Nav", "Shell", "Dialogs")


def unguarded_context_reads(text: str) -> List[str]:
    """找出漏判空的上下文属性读取（返回 `"行:列 片段"` 列表，空 = 合格）。

    合格写法只有两种：
      * 可选链 + 空值合并：`Theme?.accent ?? "transparent"` / `Tr?.map[k] ?? k`；
      * 先判存在：`typeof Runtime !== "undefined" && Runtime`（`Runtime?.iconUrl(…) ?? ""`）。
    不合格：`Theme.accent`、`Tr.map[k]`、`Runtime.iconUrl("x")`。
    """
    view = code_view(text)
    index = _load_gate().SourceIndex(text)
    found: List[str] = []
    patterns = [rf"(?<![\w?.]){name}\." for name in CONTEXT_PROPS]
    for pattern in patterns:
        for match in re.finditer(pattern, view):
            line, col = index.line_col(match.start())
            snippet = text[match.start():match.start() + 40].split("\n")[0]
            found.append(f"{line}:{col} {snippet}")
    return found


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


#: 根节点 `objectName` 的写法：**4 个空格缩进**的 `objectName: "fmXxx"`（子项缩进更深）
ROOT_OBJECT_NAME_RE = re.compile(r'^ {4}objectName:\s*"(?P<name>[A-Za-z0-9_]+)"\s*$', re.MULTILINE)


def root_object_name(text: str) -> str:
    match = ROOT_OBJECT_NAME_RE.search(text)
    return match.group("name") if match else ""


# ─── 1. 组件文件本身 ────────────────────────────────────────────


def test_there_are_enough_components():
    """`03` 任务 2.8 的清单是 19 项，加上 2.10 预留的上色图标件共 20 个。"""
    assert len(COMPONENTS) >= 19, f"组件数只有 {len(COMPONENTS)} 个：{COMPONENTS}"
    for required in ("FmButton", "FmTextField", "FmTextArea", "FmSwitch", "FmRadio",
                     "FmComboBox", "FmSlider", "FmSearchField", "FmListItem", "FmCard",
                     "FmPagination", "FmTag", "FmProgressRing", "FmProgressBar",
                     "FmInfoBar", "FmEmptyState", "FmLoadingState", "FmErrorState",
                     "FmTable", "FmIcon"):
        assert required in COMPONENTS, f"2.8 点名的 {required} 不在组件库里"


def test_every_component_declares_a_stable_object_name():
    """根节点必须有稳定的 `objectName`（Gallery 与冒烟测试按它找件）。"""
    problems = []
    seen: Dict[str, str] = {}
    for name in COMPONENTS:
        text = read(COMPONENTS_DIR / f"{name}.qml")
        object_name = root_object_name(text)
        if not object_name:
            problems.append(f"{name}.qml: 根节点没有 4 空格缩进的 objectName")
        elif not object_name.startswith("fm"):
            problems.append(f"{name}.qml: objectName 必须以 fm 开头（实际 {object_name}）")
        elif object_name in seen:
            problems.append(f"{name}.qml 与 {seen[object_name]} 用了同一个 objectName {object_name}")
        else:
            seen[object_name] = f"{name}.qml"
    assert problems == [], "objectName 问题:\n" + "\n".join(f"  - {p}" for p in problems)


def test_every_component_documents_when_to_use_and_when_not_to():
    """文件头必须写清"什么时候用它 / 什么时候不要用"（阶段 3 靠这段决定用哪个件）。"""
    missing = []
    for name in COMPONENTS:
        text = read(COMPONENTS_DIR / f"{name}.qml")
        if "什么时候用它" not in text:
            missing.append(f"{name}.qml 缺「什么时候用它」")
        if "什么时候不要用" not in text:
            missing.append(f"{name}.qml 缺「什么时候不要用」")
    assert missing == [], "组件头注释不完整:\n" + "\n".join(f"  - {m}" for m in missing)


def test_every_component_header_names_the_task():
    """每个件都要能追溯到任务号（2.16 组件库 / 2.10 图标）。"""
    for name in COMPONENTS:
        assert "2.16" in read(COMPONENTS_DIR / f"{name}.qml"), f"{name}.qml 的头部没写归属任务"


def test_components_do_not_import_services_or_bridges():
    """QML 只能通过上下文属性访问 Python（契约第三节红线 1）—— 组件库更不该破例。"""
    bad = []
    for path in sorted(COMPONENTS_DIR.glob("Fm*.qml")) + [GALLERY_PATH]:
        for line in read(path).splitlines():
            stripped = line.strip()
            if stripped.startswith("import ") and (
                "services" in stripped or "app.bridges" in stripped or "ui/" in stripped
            ):
                bad.append(f"{path.relative_to(REPO_ROOT)}: {stripped}")
    assert bad == [], f"组件/Gallery 直接 import 了 Python 侧模块：{bad}"


# ─── 2. 判空纪律（负例 N3 的被测函数） ──────────────────────────


@pytest.mark.parametrize("name", COMPONENTS)
def test_component_reads_all_guarded_context_props(name):
    """每个组件的 `Theme.* / Tr.map[…] / Runtime.iconUrl()` 读取都必须判空。"""
    found = unguarded_context_reads(read(COMPONENTS_DIR / f"{name}.qml"))
    assert found == [], (
        f"{name}.qml 有 {len(found)} 处没判空的上下文属性读取：{found[:5]} —— "
        '写成 `Theme?.x ?? 兜底`（引擎析构时上下文属性先被清成 null，'
        "而绑定还排在求值队列里，不判空会刷一屏 TypeError）"
    )


def test_gallery_reads_all_guarded_context_props():
    found = unguarded_context_reads(read(GALLERY_PATH))
    assert found == [], f"Gallery.qml 有漏判空的读取：{found[:5]}"


def test_guard_flags_raw_context_props_in_a_component():
    """**负例 N3**：把 `Theme?.accent ?? "transparent"` 改成 `Theme.accent`，守卫必须变红。"""
    text = read(COMPONENTS_DIR / "FmButton.qml")
    assert unguarded_context_reads(text) == [], "未变异的文件本来就不该有违规"

    mutated = text.replace('Theme?.accent ?? "transparent"', "Theme.accent", 1)
    assert mutated != text, "变异锚点没命中（源文件改过了？）"
    found = unguarded_context_reads(mutated)
    assert found, "漏了 Theme 判空竟然没被抓到 —— 这条守卫是空的"
    assert any("Theme.accent" in item for item in found)


def test_guard_accepts_the_two_legal_guarded_forms():
    """两种合格写法都要放行（避免守卫把正确代码判成违规）。"""
    ok = """
    Image { source: (Runtime?.iconUrl(name) ?? "") }
    Text { color: Theme?.textPrimary ?? "transparent"; text: Tr?.map[k] ?? k }
    """
    assert unguarded_context_reads(ok) == []
    assert unguarded_context_reads('visible: (typeof Runtime !== "undefined" && Runtime) ? true : false') == []


def test_guard_ignores_comments():
    """注释里写 `Theme.*` 是**该写**的（说明纪律），守卫不能误报。"""
    assert unguarded_context_reads('// 颜色只能来自 Theme.accent 之类的语义色键\nItem { }') == []


# ─── 3. 白名单（R5 的独立复核 + 负例） ──────────────────────────


def test_every_component_is_registered_in_the_whitelist():
    """**独立于闸门**再断言一次：每个组件都登记在 `COMPONENTS.md` 里。"""
    gate = _load_gate()
    names, note = gate.load_component_whitelist(REPO_ROOT)
    assert note == "", f"白名单没解析出来（R5 会降级为跳过）：{note}"
    assert names, "白名单是空的"
    missing = [name for name in COMPONENTS if name not in names]
    assert missing == [], f"这些组件没登记进 {COMPONENTS_DOC.name}：{missing}"


def test_whitelist_has_no_phantom_entries():
    """白名单里不该有"根本不存在的组件"（文档里的反引号写法很容易把它撑虚）。"""
    gate = _load_gate()
    names, note = gate.load_component_whitelist(REPO_ROOT)
    assert note == ""
    known = {path.stem for path in COMPONENTS_DIR.rglob("*.qml")}
    phantom = sorted(name for name in names if name not in known)
    assert phantom == [], (
        f"白名单里登记了不存在的组件：{phantom} —— 文档里被单反引号包住的大驼峰词都会被当成组件名，"
        "别把缩写或桥名写成那种形式的行内代码"
    )


def _copy_qml_tree(tmp_path: Path) -> Path:
    base = tmp_path / "base"
    shutil.copytree(QML_DIR, base / "qml")
    (base / "app" / "bridges").mkdir(parents=True, exist_ok=True)
    return base


def test_r5_reports_no_violations_and_does_not_skip():
    """闸门 R5 在真仓库上必须**真的判定**（不是"跳过"）且零违规。"""
    gate = _load_gate()
    report = gate.run_gate(REPO_ROOT, only=["R5"])
    result = report.results[0]
    assert result.note == "", f"R5 降级为跳过了：{result.note}"
    assert result.files, "R5 一个页面都没扫到"
    assert result.violations == [], "R5 违规：\n" + "\n".join(v.render() for v in result.violations)


def test_r5_catches_an_unregistered_component_in_a_page(tmp_path):
    """**R5 负例**：页面里用了一个没登记的自研件，闸门必须报出来。"""
    base = _copy_qml_tree(tmp_path)
    rogue = base / "qml" / "components" / "FmRogue.qml"
    rogue.write_text('import QtQuick\nItem { objectName: "fmRogue" }\n', encoding="utf-8", newline="\n")
    target = base / "qml" / "pages" / "dev" / "Gallery.qml"
    text = target.read_text(encoding="utf-8")
    text = text.replace("Item {\n    id: page", "Item {\n    FmRogue { }\n\n    id: page", 1)
    assert "FmRogue" in text, "变异锚点没命中"
    target.write_text(text, encoding="utf-8", newline="\n")

    gate = _load_gate()
    report = gate.run_gate(base, only=["R5"])
    hits = [v for v in report.results[0].violations if "FmRogue" in v.detail]
    assert hits, "白名单外的组件竟然没被 R5 抓到 —— 白名单守卫是空的"


def test_r8_catches_a_hex_literal_in_a_component(tmp_path):
    """**负例 N2（闸门侧）**：组件里塞一个十六进制颜色字面量，R8 必须报出来。"""
    base = _copy_qml_tree(tmp_path)
    target = base / "qml" / "components" / "FmButton.qml"
    text = target.read_text(encoding="utf-8")
    anchor = 'Theme?.accent ?? "transparent"'
    assert anchor in text, "变异锚点没命中"
    target.write_text(text.replace(anchor, '"#123456"', 1), encoding="utf-8", newline="\n")

    gate = _load_gate()
    report = gate.run_gate(base, only=["R8"])
    hits = [v for v in report.results[0].violations if v.path.endswith("FmButton.qml")]
    assert hits, "硬编码颜色没被 R8 抓到（颜色只能来自 Theme.*）"


def test_pristine_qml_tree_has_no_gate_violations_at_all(tmp_path):
    """整棵 `qml/` 副本跑全量闸门零违规（我的组件不给别人的规则添麻烦）。"""
    gate = _load_gate()
    report = gate.run_gate(_copy_qml_tree(tmp_path))
    hits = [(v.path, v.rule, v.detail) for result in report.results for v in result.violations]
    assert hits == [], f"闸门违规：{hits}"


# ══ 4. 子进程探针：真引擎 + 真窗口 + 真点击 ══════════════════════
#
# 为什么整体放子进程：`QQmlApplicationEngine` + FluentUI 的 `FluWindow` 在同一个进程里
# 跨测试文件不安全（全量测试跑到 QML 文件时 `engine.load(App.qml)` 会以
# `Windows fatal exception: access violation` 收场，见 `tests/test_shell_qml.py`）。
# 子进程是全新进程，稳定可靠；父测试只解析它打出来的一行 JSON。

PROBE = r'''
"""组件库 / Gallery 探针（由 tests/test_components_qml.py 生成并运行）。

用法：python probe.py <mode> <repo_root> <work_dir> [out_dir]

mode:
  components         每个 Fm*.qml 各建一个实例，核对根 objectName 齐全
  gallery            真 App.qml（FluentUI）-> 深链 fmcl://dev/gallery -> 交互 + 抓帧
  negative-component 故意引用一个不存在的组件（守卫必须变红）
  negative-color     故意给一个非法颜色（不许崩；provider 打 warning；FmIcon 退化）
"""

import json
import os
import shutil
import sys
from pathlib import Path

MODE = sys.argv[1]
REPO_ROOT = Path(sys.argv[2])
WORK = Path(sys.argv[3])
OUT = Path(sys.argv[4]) if len(sys.argv) > 4 else None

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
sys.path.insert(0, str(REPO_ROOT))

from PySide6.QtCore import QEvent, QPointF, Qt, QUrl                  # noqa: E402
from PySide6.QtGui import QGuiApplication, QMouseEvent                # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent        # noqa: E402
from PySide6.QtQuick import QQuickItem                                # noqa: E402
from PySide6.QtTest import QTest                                      # noqa: E402

from app.bridges.icon_provider import install as install_icons        # noqa: E402

MARKER = "PROBE_JSON:"
ROOT_OBJECT_NAME_RE = __import__("re").compile(
    r'^ {4}objectName:\s*"(?P<name>[A-Za-z0-9_]+)"\s*$', __import__("re").MULTILINE)

#: Qt 消息（QML 的 console / 绑定错误 / qWarning）与 provider 的 logging 记录
MESSAGES = []
PROVIDER_LOGS = []


class _LogCollector(__import__("logging").Handler):
    def emit(self, record):
        try:
            PROVIDER_LOGS.append(f"{record.levelname} {record.getMessage()}")
        except Exception:
            pass


def component_files():
    return sorted((REPO_ROOT / "qml" / "components").glob("Fm*.qml"))


def component_object_name(path):
    match = ROOT_OBJECT_NAME_RE.search(path.read_text(encoding="utf-8"))
    return match.group("name") if match else ""


class FakeConfig:
    language = "zh_CN"
    theme_name = "default"
    accent_color = None
    base_dir = str(REPO_ROOT)

    def save_config(self):
        pass


def make_plain_engine(work):
    """不带 FluentUI 的引擎 + 三个桥（组件只读 Theme / Tr / Runtime）。"""
    import logging

    from PySide6.QtCore import qInstallMessageHandler

    from app.bridges.runtime_bridge import RuntimeBridge
    from app.bridges.theme_bridge import ThemeBridge
    from app.bridges.tr_bridge import TrBridge
    from services.theme_service import ThemeEngine

    qInstallMessageHandler(lambda mode, ctx, message: MESSAGES.append(str(message)))
    logger = logging.getLogger("app.bridges.icon_provider")
    logger.addHandler(_LogCollector())
    logger.setLevel(logging.DEBUG)

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    engine = QQmlApplicationEngine()
    provider = install_icons(engine, REPO_ROOT / "qml" / "assets" / "icons")
    engine.rootContext().setContextProperty("Theme", ThemeBridge(
        engine=engine, theme_engine=ThemeEngine(str(work / "themes")), config=FakeConfig()))
    engine.rootContext().setContextProperty("Tr", TrBridge(config=FakeConfig()))
    engine.rootContext().setContextProperty("Runtime", RuntimeBridge())
    return app, engine, provider


def stage_qml_tree():
    """把 `qml/` 复制到工作目录（相对 import 才能解析；负例也只改副本）。"""
    work_qml = WORK / "qml"
    if not work_qml.exists():
        shutil.copytree(REPO_ROOT / "qml", work_qml)
    return work_qml


def item_index(root):
    """视觉子树里所有有 objectName 的项。委托只在这里可见（契约第六节决策 7）。"""
    found = {}

    def walk(item):
        if item.objectName():
            found.setdefault(item.objectName(), item)
        for child in item.childItems():
            walk(child)

    walk(root.contentItem())
    return found


def read_text_property(item, name):
    return str(item.property(name)) if item is not None else ""


def descendants_named(item, name):
    """按 objectName 在自己的子树里找项（组件内部的命中区靠这个定位）。

    为什么不用全窗口的索引：`fmTagClose` 这类内部名字**每个实例都一样**，
    `findChild` / `setdefault` 只会拿到第一个（很可能是别的标签里那个隐藏的）。
    """
    found = []

    def walk(node):
        for child in node.childItems():
            if child.objectName() == name:
                found.append(child)
            walk(child)

    walk(item)
    return found


def hit_chain(window, item):
    """点这个项的中心时，场景树上**最上层**命中谁（返回 objectName 链）。

    诊断"点了没反应"用：`childAt` 只认可见且接受鼠标的项，链上第一个不是目标
    就说明被别的项盖住了（或目标尺寸为 0）。
    """
    centre = item.mapToScene(QPointF(item.width() / 2.0, item.height() / 2.0))
    node = window.contentItem().childAt(centre.x(), centre.y())
    chain = []
    while node is not None and len(chain) < 12:
        chain.append(node.objectName() or node.metaObject().className())
        node = node.parentItem()
    return [round(centre.x(), 1), round(centre.y(), 1), round(item.width(), 1), round(item.height(), 1),
            bool(item.isVisible()), bool(item.isEnabled()), chain]


def geometry_probe(window, scroller, item):
    """把"目标在哪儿"的几种算法都量出来 —— **只为诊断**，正常路径不用它。

    实测结论（2.16）：`scroller.mapToScene()` 与实际可见位置对不上，
    因为 ScrollView 自己的 `contentY` 影子属性骗人（写它不会真的滚），
    真正要写的是它背后那个 Flickable 的 `contentY`（见 `flickable()` 的说明）。
    """
    flick = flickable(scroller)
    content = flick.property("contentItem") if flick is not None else None
    scene = item.mapToScene(QPointF(0, 0))
    into_flick = item.mapToItem(flick, QPointF(0, 0)) if flick is not None else QPointF(-1, -1)
    into_content = item.mapToItem(content, QPointF(0, 0)) if content is not None else QPointF(-1, -1)
    node = window.contentItem().childAt(scene.x() + item.width() / 2.0, scene.y())
    return {
        "scrollViewContentY": float(scroller.property("contentY") or 0),
        "flickContentY": float(flick.property("contentY") or 0) if flick is not None else -1,
        "contentHeight": float(flick.property("contentHeight") or 0) if flick is not None else -1,
        "scene": [round(scene.x(), 1), round(scene.y(), 1)],
        "intoFlick": [round(into_flick.x(), 1), round(into_flick.y(), 1)],
        "intoContent": [round(into_content.x(), 1), round(into_content.y(), 1)],
        "sceneHit": node.objectName() if node is not None else None,
    }


def click(app, window, item, scroller=None):
    """真鼠标点击（坐标由 item 自己算；要滚进视口就先滚）。"""
    if scroller is not None:
        scroll_into_view(window, scroller, item)
    centre = item.mapToScene(QPointF(item.width() / 2.0, item.height() / 2.0))
    global_pos = window.mapToGlobal(centre)
    for event_type, buttons in ((QEvent.MouseButtonPress, Qt.LeftButton),
                                (QEvent.MouseButtonRelease, Qt.NoButton)):
        app.sendEvent(window, QMouseEvent(event_type, centre, global_pos, Qt.LeftButton,
                                          buttons, Qt.NoModifier))
        QTest.qWait(30)
    QTest.qWait(60)


def items_named(root, name):
    """按 objectName 收集**全部**匹配项（`item_index` 是字典，同名项只会留第一个）。"""
    found = []

    def walk(node):
        if node.objectName() == name:
            found.append(node)
        for child in node.childItems():
            walk(child)

    walk(root)
    return found


def flickable(scroller):
    """取 ScrollView 背后真正的 Flickable。

    **不要**直接对 ScrollView 写 `contentY`：实测那只是把 ScrollView 自己的影子属性
    改了（读回来是 1868），Flickable 的内容项**根本没平移** —— `mapToScene` 出来的坐标
    仍是内容坐标系，于是深层控件的点击全落空（Gallery 的表格行与错误态重试按钮就是
    这么漏的）。写 Flickable 才会真的滚。
    """
    return scroller.property("contentItem") if scroller is not None else None


def scroll_into_view(window, scroller, item, tries=3):
    """把目标滚进视口：用**内容坐标系**算目标位置，再让 Flickable 滚过去。"""
    flick = flickable(scroller)
    if flick is None:
        return
    content = flick.property("contentItem")
    if content is None:
        return
    for _ in range(tries):
        target_y = item.mapToItem(content, QPointF(0, 0)).y()
        viewport = float(flick.height())
        span = max(0.0, float(flick.property("contentHeight") or 0) - viewport)
        wanted = max(0.0, min(target_y - viewport / 3.0, span))
        if abs(float(flick.property("contentY") or 0) - wanted) < 1.0:
            return
        flick.setProperty("contentY", wanted)
        QTest.qWait(100)


# ─── mode: components ───────────────────────────────────────────


def run_components(app, engine, provider, names):
    files = component_files()
    if names:
        files = [path for path in files if path.stem in names]
    source = ['import QtQuick', 'import QtQuick.Window', 'import "qml/components"', '',
              'Window {', '    id: win', '    width: 900', '    height: 700', '    visible: true',
              '    Column {']
    for path in files:
        source.append(f'        {path.stem} {{ }}')
    source += ['    }', '}', '']
    text = "\n".join(source)
    (WORK / "Components.qml").write_text(text, encoding="utf-8", newline="\n")

    component = QQmlComponent(engine)
    component.setData(text.encode("utf-8"), QUrl.fromLocalFile(str(WORK / "Components.qml")))
    if not component.isReady():
        return {"errors": [e.toString() for e in component.errors()]}
    window = component.create()
    if window is None:
        return {"errors": ["create() 返回 None"]}
    for _ in range(40):
        app.processEvents()
    window.show()
    for _ in range(40):
        app.processEvents()

    index = item_index(window)
    expected = {path.stem: component_object_name(path) for path in files}
    return {
        "expected": expected,
        "missing": sorted(name for name in expected.values() if name not in index),
        "found": sorted(name for name in expected.values() if name in index),
        "requests": provider.requests,
        "cache": provider.cacheSize,
        "qmlMessages": list(MESSAGES),
        "providerLogs": list(PROVIDER_LOGS),
    }


# ─── mode: gallery（真 App.qml + 深链 + 交互 + 抓帧） ──────────


def run_gallery():
    import main_qml
    from PySide6.QtQuick import QQuickWindow

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    sink = main_qml.install_message_handler()
    engine = main_qml.build_engine()
    bridges = main_qml.register_bridges(engine, None)
    provider = install_icons(engine, REPO_ROOT / "qml" / "assets" / "icons")
    main_qml.apply_theme_font(engine)
    engine.load(QUrl.fromLocalFile(str(REPO_ROOT / "qml" / "App.qml")))
    roots = engine.rootObjects()
    if not roots:
        return {"fatal": "App.qml 没起来", "messages": sink.messages}
    window = roots[0]
    QTest.qWait(150)

    nav = engine._fmcl_bridges["Nav"]
    report = {"bridges": bridges, "qmlMessagesBefore": list(sink.messages)}

    # 1) 深链进入 Gallery（本页刻意不在 12 个一级导航里）
    report["navItems"] = [entry["id"] for entry in engine._fmcl_bridges["Shell"].navItems()]
    report["deepLink"] = bool(nav.openDeepLink("fmcl://dev/gallery"))
    QTest.qWait(400)
    stack = window.findChild(QQuickItem, "pageStack")
    page = stack.property("currentItem") if stack is not None else None
    report["route"] = str(nav.currentRoute)
    report["page"] = page.objectName() if page is not None else ""
    if page is None:
        report["fatal"] = "Gallery 页面没建出来"
        return report
    QTest.qWait(200)

    index = item_index(window)
    report["expectedGroups"] = sorted(name for name in index if name.startswith("galleryItem_"))
    report["fallbackIcons"] = sorted(
        name for name, item in index.items()
        if item.property("fallbackUsed") is True)

    # 2) 交互：点/切/翻页/输入都真的落到页面状态上
    scroller = index.get("galleryScroll")
    click(app, window, index["galleryButtonPrimary"], scroller)
    report["afterClickCount"] = read_text_property(index.get("galleryClickCount"), "text")

    click(app, window, index["gallerySwitch"], scroller)
    report["afterSwitch"] = read_text_property(index.get("gallerySwitchReadout"), "text")

    click(app, window, index["galleryRadio3"], scroller)
    report["afterRadio"] = read_text_property(index.get("galleryRadioReadout"), "text")

    click(app, window, index["fmPaginationNext"], scroller)
    report["afterPagination"] = read_text_property(index.get("galleryPageReadout"), "text")

    close_areas = descendants_named(index["galleryTagClosable"], "fmTagClose")
    assert close_areas, "FmTag 里应当有一个可点的关闭命中区"
    click(app, window, close_areas[0], scroller)
    QTest.qWait(60)
    report["tagStillThere"] = item_index(window)["galleryTagClosable"].isVisible()
    restore = item_index(window).get("galleryTagRestore")
    if restore is not None and restore.isVisible():
        click(app, window, restore, scroller)
    report["tagRestored"] = item_index(window)["galleryTagClosable"].isVisible()

    search = index.get("gallerySearchField")
    search.setProperty("text", "lithium")
    QTest.qWait(40)
    report["searchText"] = read_text_property(search, "text")
    from PySide6.QtCore import QMetaObject
    report["searchInvoked"] = bool(
        QMetaObject.invokeMethod(search, "accepted", Qt.ConnectionType.DirectConnection))
    QTest.qWait(60)
    report["afterSearch"] = read_text_property(item_index(window).get("gallerySearchReadout"), "text")

    rows = items_named(window.contentItem(), "fmTableRow")
    report["tableRows"] = len(rows)
    if rows:
        scroll_into_view(window, scroller, rows[0])
        report["tableHit"] = hit_chain(window, rows[0])
        click(app, window, rows[0])
        report["afterRowClick"] = read_text_property(item_index(window).get("galleryTableReadout"), "text")

    retry = index.get("fmErrorStateRetry")
    if retry is not None:
        scroll_into_view(window, scroller, retry)
        report["retryHit"] = hit_chain(window, retry)
        click(app, window, retry)
        report["afterRetry"] = read_text_property(item_index(window).get("galleryRetryReadout"), "text")

    # 3) 抓帧（顶部与底部各一张：页面比屏幕长，所以要滚 —— 滚 Flickable，见 flickable()）
    if OUT is not None:
        OUT.mkdir(parents=True, exist_ok=True)
        flick = flickable(scroller)
        span = max(0.0, float(flick.property("contentHeight") or 0) - float(flick.height()))
        flick.setProperty("contentY", 0.0)
        QTest.qWait(140)
        top = window.grabWindow()
        shots = {"gallery_2_16_top.png": (top, 0.0)}
        flick.setProperty("contentY", span)
        QTest.qWait(180)
        shots["gallery_2_16_bottom.png"] = (window.grabWindow(), span)
        report["screenshots"] = {}
        for name, (shot, offset) in shots.items():
            path = OUT / name
            saved = bool(shot.save(str(path)))
            report["screenshots"][name] = {
                "saved": saved, "size": [shot.width(), shot.height()],
                "contentY": offset, "path": str(path),
            }
        assert shots["gallery_2_16_top.png"][0] != shots["gallery_2_16_bottom.png"], (
            "顶部与底部两张截图一模一样 —— 说明页面根本没滚动，底部那张没有意义"
        )
        # 组件清单落盘（证据文件）
        lines = ["# 2.16 组件库：Gallery 里逐个组件建出来的实测清单",
                 "# 由 tests/test_components_qml.py 生成（python tests/test_components_qml.py）", ""]
        for name in sorted(index):
            if name.startswith("galleryItem_"):
                lines.append(f"OK   {name}")
        lines += ["", f"组件个数（galleryItem_*）: {len(report['expectedGroups'])}",
                  f"FmIcon 退化成未上色 SVG 的个数: {len(report['fallbackIcons'])}",
                  f"深链 fmcl://dev/gallery 成功: {report['deepLink']}",
                  f"当前路由: {report['route']}  当前页面: {report['page']}",
                  f"provider 取图次数: {provider.requests}（缓存 {provider.cacheSize} 条）", ""]
        (OUT.parent / "components_2_16.txt").write_text("\n".join(lines), encoding="utf-8", newline="\n")

    report["isWindow"] = isinstance(window, QQuickWindow)
    report["qmlMessages"] = list(sink.messages)
    report["qmlMessagesAfter"] = list(sink.messages)
    report["provider"] = {"requests": provider.requests, "cache": provider.cacheSize}
    return report


# ─── mode: 负例 ─────────────────────────────────────────────────


def run_negative_component(app, engine, provider):
    """故意引用一个不存在的组件：加载必须**报错**（这就是守卫该红的样子）。"""
    text = ('import QtQuick\nimport QtQuick.Window\nimport "qml/components"\n\n'
            'Window {\n    width: 200\n    height: 100\n    visible: true\n'
            '    FmNoSuchComponent { }\n}\n')
    (WORK / "Bad.qml").write_text(text, encoding="utf-8", newline="\n")
    component = QQmlComponent(engine)
    component.setData(text.encode("utf-8"), QUrl.fromLocalFile(str(WORK / "Bad.qml")))
    ready = component.isReady()
    errors = [error.toString() for error in component.errors()]
    window = component.create() if ready else None
    if window is not None:
        window.show()
    for _ in range(10):
        app.processEvents()
    return {"ready": ready, "errors": errors, "created": window is not None}


def run_negative_color(app, engine, provider):
    """故意给非法颜色，分两条路各验一次：

    1. **手写 URL**（`image://fmcl-icon/check?color=notacolor`）能真的走到 provider 的
       非法色分支：不许崩、打 warning、这张图取不到（`Image.status` 变 Error）；
    2. **传给 FmIcon 的 `color`**：那是强类型的 `color` 属性，QML 在**构建期**就拒绝
       （`Invalid property assignment: color expected`）—— 这是比运行期兜底更好的结果，
       一并钉住（省得以后有人把 `color` 改成 string 把这道防线拆了）。
    """
    text = ('import QtQuick\nimport QtQuick.Window\nimport "qml/components"\n\n'
            'Window {\n    width: 300\n    height: 120\n    visible: true\n'
            '    Column {\n'
            '        Image {\n            objectName: "badImage"\n'
            '            width: 24; height: 24\n'
            '            source: "image://fmcl-icon/check?color=notacolor"\n'
            '            sourceSize.width: 24; sourceSize.height: 24\n        }\n'
            '        FmIcon { objectName: "goodIcon"; name: "check"; color: "#00ff00" }\n'
            '    }\n}\n')
    (WORK / "Color.qml").write_text(text, encoding="utf-8", newline="\n")
    component = QQmlComponent(engine)
    component.setData(text.encode("utf-8"), QUrl.fromLocalFile(str(WORK / "Color.qml")))
    if not component.isReady():
        return {"errors": [error.toString() for error in component.errors()]}
    window = component.create()
    if window is None:
        return {"errors": ["create() 返回 None"]}
    window.show()
    for _ in range(30):
        app.processEvents()
    index = item_index(window)

    typed = ('import QtQuick\nimport QtQuick.Window\nimport "qml/components"\n\n'
             'Window {\n    width: 200\n    height: 60\n    visible: true\n'
             '    FmIcon { name: "check"; color: "notacolor" }\n}\n')
    (WORK / "ColorTyped.qml").write_text(typed, encoding="utf-8", newline="\n")
    typed_component = QQmlComponent(engine)
    typed_component.setData(typed.encode("utf-8"), QUrl.fromLocalFile(str(WORK / "ColorTyped.qml")))

    return {
        "goodFallback": index["goodIcon"].property("fallbackUsed") is True,
        "badImageExists": "badImage" in index,
        "badImageWidth": index["badImage"].property("implicitWidth") if "badImage" in index else None,
        "typedRejected": not typed_component.isReady(),
        "typedErrors": [error.toString() for error in typed_component.errors()],
        "providerLogs": list(PROVIDER_LOGS),
        "qmlMessages": list(MESSAGES),
        "requests": provider.requests,
        "survived": True,
    }


def main():
    report = {"mode": MODE, "platform": os.environ.get("QT_QPA_PLATFORM", "")}
    try:
        if MODE == "gallery":
            report.update(run_gallery())
        else:
            stage = stage_qml_tree()
            app, engine, provider = make_plain_engine(WORK)
            if MODE == "components":
                report.update(run_components(app, engine, provider, sys.argv[5:] if len(sys.argv) > 5 else []))
            elif MODE == "negative-component":
                report.update(run_negative_component(app, engine, provider))
            elif MODE == "negative-color":
                report.update(run_negative_color(app, engine, provider))
            else:
                report["fatal"] = f"未知 mode {MODE}"
        report["qmlMessages"] = list(report.get("qmlMessages", []))
    except Exception as exc:  # 探针自己的异常也要作为结构化结果回传
        import traceback
        report["fatal"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    print(MARKER + json.dumps(report, ensure_ascii=False, default=str))
    return 0 if "fatal" not in report else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''

PROBE_MARKER = "PROBE_JSON:"
_PROBE_CACHE: Dict[str, Any] = {}


def run_probe(
    tmp_path: Path,
    mode: str,
    extra: Tuple[str, ...] = (),
    out_dir: Path = None,
    platform: str = "offscreen",
) -> Dict[str, Any]:
    """跑一次探针（按 mode 缓存：一个测试会话里同一个 mode 只跑一次，每次都是新进程）。

    `platform` 默认 `offscreen`（本仓库所有测试的跑法）；证据模式可以传 `windows`
    拿到带字体的真实截图（offscreen 下 Qt 找不到字体目录，界面上的文字是方框）。
    """
    key = mode + "|" + "|".join(extra) + "|" + (str(out_dir) if out_dir else "") + "|" + platform
    if key in _PROBE_CACHE:
        return _PROBE_CACHE[key]
    tmp_path.mkdir(parents=True, exist_ok=True)
    script = tmp_path / f"probe_{mode}.py"
    script.write_text(PROBE, encoding="utf-8", newline="\n")
    argv = [sys.executable, "-X", "utf8", str(script), mode, str(REPO_ROOT), str(tmp_path / f"work_{mode}")]
    if out_dir is not None:
        argv.append(str(out_dir))
    argv.extend(extra)
    done = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        # 必须显式给 encoding：不带它时按本地码页（本机 gbk）解码子进程输出，
        # 遇到 UTF-8 字节会在 reader 线程抛 UnicodeDecodeError（阶段 1 修过同族缺陷）
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
        env={**os.environ, "QT_QPA_PLATFORM": platform},
    )
    stdout = done.stdout or ""
    payload = None
    for line in stdout.splitlines():
        if line.startswith(PROBE_MARKER):
            payload = json.loads(line[len(PROBE_MARKER):])
    assert payload is not None, (
        f"探针没有输出结果（mode={mode}, exit={done.returncode}）：\n"
        f"stdout={stdout[-2000:]!r}\nstderr={(done.stderr or '')[-2000:]!r}"
    )
    payload["_returncode"] = done.returncode
    payload["_stderr"] = done.stderr or ""
    _PROBE_CACHE[key] = payload
    return payload


@pytest.fixture(scope="session")
def components_report(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, Any]:
    """每个组件各建一个实例（子进程探针，跑一次）。"""
    return run_probe(tmp_path_factory.mktemp("components"), "components")


@pytest.fixture(scope="session")
def gallery_report(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, Any]:
    """真 App.qml + 深链进 Gallery + 交互 + 抓帧（子进程探针，跑一次）。"""
    return run_probe(tmp_path_factory.mktemp("gallery"), "gallery")


# ─── 5. "每个组件都能建出来" ────────────────────────────────────


def test_probe_built_every_component(components_report):
    assert "fatal" not in components_report, f"探针挂了：{components_report.get('traceback')}"
    assert components_report.get("errors") in (None, []), f"实例化报错：{components_report.get('errors')}"
    assert components_report.get("missing") == [], (
        f"这些组件没建出来（根 objectName 找不到）：{components_report.get('missing')}"
    )
    assert sorted(components_report["expected"]) == COMPONENTS, "探针漏了组件（与 Fm*.qml 清单不一致）"
    assert sorted(components_report["found"]) == sorted(components_report["expected"].values())
    assert components_report["requests"] > 0, "组件里的 FmIcon 一次都没向 provider 取图"


def test_probe_harness_has_no_qml_errors(components_report):
    errors = qml_errors(components_report.get("qmlMessages", []))
    assert errors == [], f"实例化 20 个组件时出现 QML 报错：{errors}"


# ─── 6. Gallery 端到端 ──────────────────────────────────────────


def test_gallery_is_reachable_by_deep_link_and_not_in_the_primary_nav(gallery_report):
    assert "fatal" not in gallery_report, f"探针挂了：{gallery_report.get('traceback')}"
    assert gallery_report["deepLink"] is True, "fmcl://dev/gallery 必须打得开"
    assert gallery_report["route"] == "dev/gallery"
    assert gallery_report["page"] == "galleryPage", "深链之后 PageStack 上的页面应该就是 Gallery"
    assert gallery_report["isWindow"] is True, "根对象应当还是真窗口（App.qml 的 FluWindow）"
    assert "dev/gallery" not in gallery_report["navItems"], (
        "Gallery 是开发自查页，不该出现在 12 个一级导航里"
    )


def test_gallery_shows_every_component(gallery_report):
    groups = {name[len("galleryItem_"):] for name in gallery_report["expectedGroups"]}
    missing = [name for name in COMPONENTS if name not in groups]
    assert missing == [], f"Gallery 里缺这些组件的实例：{missing}"
    assert len(groups) >= len(COMPONENTS)


def test_gallery_loads_without_qml_errors(gallery_report):
    errors = qml_errors(gallery_report.get("qmlMessages", []))
    assert errors == [], f"加载 / 交互期间出现 QML 报错：{errors}"
    assert qml_errors(gallery_report.get("_stderr", "").splitlines()) == [], "子进程 stderr 里也有 QML 报错"
    assert gallery_report["bridges"]["missing"] == [], "阶段 2 的桥必须全部注册成功"


def test_gallery_icons_are_colored_by_the_provider_not_the_black_fallback(gallery_report):
    """`fallbackUsed` 全为 false = `image://fmcl-icon` 真的在供图（不是退化成黑 SVG）。"""
    assert gallery_report["provider"]["requests"] > 0, "provider 一次都没被调用"
    assert gallery_report["fallbackIcons"] == [], (
        f"这些图标的 provider 取图失败、退化成了未上色的 SVG：{gallery_report['fallbackIcons']}"
    )


def test_gallery_interactions_really_change_state(gallery_report):
    """活的实例：点一下按钮、拨一下开关、翻一页、换一个单选项都要落到页面状态上。"""
    assert gallery_report["afterClickCount"] == "1", "点主按钮没触发 clicked"
    assert gallery_report["afterSwitch"] == "false", "拨开关没改变状态（本来 true）"
    assert gallery_report["afterRadio"] == "3", "点第三个单选项没选中"
    assert gallery_report["afterPagination"] == "2", "点下一页没翻页"
    assert gallery_report["tagStillThere"] is False, "点标签上的关闭没有生效"
    assert gallery_report["tagRestored"] is True, "恢复按钮没把标签放回来"
    assert gallery_report["afterSearch"] == "lithium", "搜索框的回车没把关键词交出去"
    assert gallery_report["afterRowClick"].startswith("0"), "点表格第一行没触发 rowActivated"
    assert gallery_report["afterRetry"] == "1", "错误态的重试按钮没触发 retried"


# ─── 7. 负例自检（三条，逐一证明守卫会红） ──────────────────────


def test_negative_missing_component_is_caught(tmp_path):
    """**负例 N1**：引用一个不存在的组件 —— 加载必须报错（"零 QML 报错"这条判据才有意义）。"""
    report = run_probe(tmp_path, "negative-component")
    assert report.get("fatal") is None, f"探针自己挂了：{report.get('traceback')}"
    assert report["ready"] is False, "引用不存在的组件竟然加载成功了？"
    assert report["created"] is False
    joined = " ".join(report["errors"])
    assert "FmNoSuchComponent" in joined, f"报错里应当点名那个不存在的组件：{report['errors']}"
    assert qml_errors(report["errors"]), "这条报错必须能被我的 qml_errors() 判据认出来"


def test_negative_bad_color_does_not_crash_and_degrades_visibly(tmp_path, caplog):
    """**负例 N2（运行期侧）**：非法颜色不许崩；provider 打 warning；强类型属性在构建期就拒。

    闸门侧的同一件事（硬编码 `#123456` 被 R8 抓）在 `test_r8_catches_a_hex_literal_...`。
    """
    report = run_probe(tmp_path, "negative-color")
    assert report.get("fatal") is None, f"探针自己挂了：{report.get('traceback')}"
    assert report.get("errors") in (None, []), f"非法颜色不该让整个组件树起不来：{report.get('errors')}"
    assert report["survived"] is True
    assert report["badImageExists"] is True, "手写 URL 的那张图应当照常建出来（不崩）"
    assert any("不是合法颜色" in line for line in report["providerLogs"]), (
        f"provider 必须对非法颜色打 warning（实际日志：{report['providerLogs']}）"
    )
    assert report["typedRejected"] is True, (
        "FmIcon 的 color 是强类型 color：非法颜色应当在**构建期**就被 QML 拒绝"
    )
    assert any("color expected" in error for error in report["typedErrors"]), report["typedErrors"]
    assert report["goodFallback"] is False, "正常颜色的图标不该退化 —— 否则这条负例没有区分度"


def test_negative_unguarded_theme_is_caught():
    """**负例 N3**：漏 `Theme` 判空必须被守卫抓到（与静态层的实现是同一个函数）。"""
    baseline = read(COMPONENTS_DIR / "FmCard.qml")
    mutated = baseline.replace('Theme?.radiusLg ?? 0', "Theme.radiusLg", 1)
    assert mutated != baseline, "变异锚点没命中"
    assert unguarded_context_reads(mutated), "漏判空的 Theme 读取没被抓到"


# ─── 8. 证据入口（人工跑：python tests/test_components_qml.py） ──


if __name__ == "__main__":
    #: 一条命令产出端到端证据：截图 + 组件清单（见 README / 2.16 报告）。
    #: 跑两遍：`offscreen`（与测试同一条路径，作为判据证据）与原生平台
    #: （offscreen 下 Qt 找不到字体目录，界面文字是方框；截图要给评审看，用带字体的那份）。
    out = REPO_ROOT / "poc" / "gallery_2_16"
    payload = run_probe(REPO_ROOT / "tmp" / "evidence_2_16", "gallery", out_dir=out)
    native = "windows" if os.name == "nt" else ("cocoa" if sys.platform == "darwin" else "xcb")
    second = run_probe(
        REPO_ROOT / "tmp" / "evidence_2_16_native", "gallery",
        out_dir=REPO_ROOT / "poc" / "gallery_2_16_native", platform=native,
    )
    for name, report in (("offscreen", payload), (native, second)):
        summary = {k: v for k, v in report.items() if k not in ("qmlMessages", "_stderr")}
        print(f"--- {name} ---")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n截图与清单已写入 {out}、{out.parent / 'gallery_2_16_native'} 与 "
          f"{out.parent / 'components_2_16.txt'}")
    failed = [name for name, report in (("offscreen", payload), (native, second)) if "fatal" in report]
    raise SystemExit(1 if failed else 0)
