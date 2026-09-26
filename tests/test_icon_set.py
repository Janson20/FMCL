"""阶段 2 任务 2.10（图标资源集）的回归守卫。

## 这一组测试想钉住什么

图标集最容易"看起来齐了但其实是空的"：文件都在、名字都对，但（a）映射表里登记的
图标根本不存在，（b）SVG 里塞了 `<script>` 或渐变，（c）图标**渲染出来是空的**。
所以这里的断言分四层：

1. **映射表 ↔ 清单 ↔ 代码** 三方对齐（双向）：代码里出现过的 emoji 必须在
   `qml/assets/icons/emoji-map.json` 里有归属（映射到图标，或进 `exempt`），
   映射表里的每个 emoji 也必须在生成的清单里。任一方向缺一个就红 —— 这是"漏网"的唯一防线。
2. **文件级**：每个被映射的 SVG 存在、是合法 XML、24x24、含 `currentColor`、
   不含 `<script>`、不含渐变、不含中文、< 2KB。
3. **真实渲染**（不是只查文件存在）：
   - `QSvgRenderer` 把每个 SVG 渲到 24x24 的 `QImage`，断言非空、尺寸正确、**有非透明像素**；
   - QML 侧用 `Image` 真的加载并 `QQuickWindow.grabWindow()` **抓帧**一遍
     （`implicitWidth == 24` + 每格真的画出了像素）。这一条**在子进程里跑**，
     理由见下面那段"引擎不能留在 pytest 进程里"。
   > 取舍说明：`ColorOverlay` / `MultiEffect` / `layer.effect` 这三种 shader 上色在
   > `offscreen` 下**静默失效**（对照组：纯 Rectangle + ColorOverlay 也不变色，
   > 说明失效的是整条 shader 通路），所以本组测试不碰 shader，只验"加载 + 绘制"。
   > 上色路线的实测对比见 `qml/assets/icons/README.md` 第三节。
4. **负例自检**：故意造一个含 `<script>` 的 SVG、故意传一个不存在的图标名、
   故意写一条指向不存在文件的映射，断言守卫**真的会红** —— 否则上面三层都是空断言。

## 引擎不能留在 pytest 进程里（实测踩过，代价是 12 个 error）

`ThemeBridge._discover_engine()`（任务 2.8）用 `gc.get_objects()` 找进程里**最后一个**
`QQmlEngine` 来注入 `FluTheme`。本模块第一版在进程里建了 QML 探针引擎，于是后一个模块
`tests/test_main_qml_entry.py` 装配入口时把 `FluTheme` 注到了这个既没有 FluentUI 导入路径、
也没有根对象的引擎上 —— `App.qml` 起不来，`main_qml.assemble()` 抛
"QML 根对象未能创建"，还连带污染 `AppContext`（另外 3 条测试跟着红）。
实测对照：**带本文件全量跑 = 4 failed + 12 errors；`--ignore=tests/test_icon_set.py`
全量跑 = 2769 passed / 0 failed。**

所以本模块的规矩是：**不在 pytest 进程里建 `QGuiApplication` / `QQmlEngine`**，
真窗口那条路一律走子进程（与 `test_no_qml_binding_errors_at_process_exit` 同一思路）。

## 环境前提

`QT_QPA_PLATFORM=offscreen`（Qt 的 offscreen 平台插件，本仓库所有 Qt 测试都这么跑）。
不依赖 FluentUI 模块，也不依赖 `poc/` 与 `docs/refactor/`（那两处按契约不入库，
清单缺失时对应断言降级为 skip 而不是失败）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

# 必须在任何 PySide6 import 之前设好（本进程可能已经有别的 Qt 测试）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

ICON_DIR = REPO_ROOT / "qml" / "assets" / "icons"
MAP_PATH = ICON_DIR / "emoji-map.json"
DOC_PATH = REPO_ROOT / "docs" / "refactor" / "14-emoji-inventory.md"

#: 任务点名的图标名单（阶段 2 任务 2.10：能覆盖的语义，一个都不许少）
REQUIRED_ICONS = (
    "monitor stop settings-gear check close warning error info success refresh search download "
    "upload play pause prev next volume mute folder-open trash edit save copy lock unlock music "
    "lyrics equalizer tool achievement trophy mods resourcepack shaderpack saves server online "
    "backup bedrock agent crash plugin language theme account"
).split()

ICON_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
CJK_RE = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff"
                    r"\uac00-\ud7af\uf900-\ufaff\uff00-\uffef]")
MAX_SVG_BYTES = 2048


# ─── 读取 ───────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def emoji_map() -> dict:
    assert MAP_PATH.is_file(), f"映射表不存在: {MAP_PATH}"
    data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    assert isinstance(data.get("emoji"), dict) and isinstance(data.get("exempt"), dict), (
        "emoji-map.json 必须有 emoji 段与 exempt 段"
    )
    return data


@pytest.fixture(scope="module")
def svg_files() -> list:
    files = sorted(ICON_DIR.glob("*.svg"))
    assert files, f"{ICON_DIR} 下一个 SVG 都没有"
    return files


def svg_problems(path: Path) -> list:
    """一个 SVG 的全部问题（空 = 合格）。测试与负例共用这条判据。"""
    problems = []
    raw = path.read_bytes()
    if len(raw) >= MAX_SVG_BYTES:
        problems.append(f"体积 {len(raw)} 字节，超过 {MAX_SVG_BYTES}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return [f"不是合法 UTF-8: {exc}"]
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        return [f"不是合法 XML: {exc}"]
    if root.tag.split("}")[-1] != "svg":
        problems.append(f"根元素不是 svg: {root.tag}")
    if root.get("viewBox") != "0 0 24 24":
        problems.append(f'viewBox 必须是 "0 0 24 24"，实际 {root.get("viewBox")!r}')
    if "currentColor" not in text:
        problems.append("没有 currentColor（没法跟随主题上色）")
    lowered = text.lower()
    for bad, why in (("<script", "含 <script>"), ("gradient", "含渐变（项目 UI 红线 5）"),
                     ("<image", "内嵌了位图"), ("xlink:href", "引用了外部资源")):
        if bad in lowered:
            problems.append(why)
    if CJK_RE.search(text):
        problems.append("含中日韩文字（图标里不许有文字，文案要走 i18n）")
    drawable = [el.tag.split("}")[-1] for el in root.iter() if el.tag.split("}")[-1] in
                ("path", "rect", "circle", "ellipse", "polygon", "line", "polyline")]
    if not drawable:
        problems.append("没有任何可见元素")
    for el in root.iter():
        if el.tag.split("}")[-1] in ("path", "rect", "circle", "ellipse", "polygon"):
            if el.get("fill") != "currentColor":
                problems.append(f'可见元素没有显式 fill="currentColor"（{el.tag.split("}")[-1]}）')
    return problems


def map_problems(data: dict, icon_dir: Path = ICON_DIR) -> list:
    """映射表本身的问题（空 = 合格）。给不存在的图标名必须报出来 —— 负例测试就用这条。"""
    problems = []
    for section in ("emoji", "exempt"):
        for char, spec in data.get(section, {}).items():
            if len(char) != 1:
                problems.append(f"{section} 段的键必须恰好一个码点: {char!r}")
                continue
            if not isinstance(spec, dict):
                problems.append(f"{section}[U+{ord(char):04X}] 必须是对象")
                continue
            codepoint = f"U+{ord(char):04X}"
            if spec.get("codepoint") != codepoint:
                problems.append(f"{section}[{codepoint}] 的 codepoint 字段写成 {spec.get('codepoint')!r}")
            if not str(spec.get("reason", "")).strip():
                problems.append(f"{section}[{codepoint}] 缺 reason")
            kind = spec.get("kind")
            if kind not in ("ui", "log", "doc", "protocol"):
                problems.append(f"{section}[{codepoint}] 的 kind 非法: {kind!r}")
            if section == "exempt":
                continue
            icon = str(spec.get("icon", ""))
            if not icon.endswith(".svg"):
                problems.append(f"emoji[{codepoint}] 的 icon 必须是 `xxx.svg`: {icon!r}")
            elif not (icon_dir / icon).is_file():
                problems.append(f"emoji[{codepoint}] 映射到不存在的图标 {icon}")
    both = set(data.get("emoji", {})) & set(data.get("exempt", {}))
    for char in sorted(both):
        problems.append(f"U+{ord(char):04X} 同时在 emoji 段与 exempt 段")
    return problems


def doc_codepoints() -> set:
    """从生成的清单里取出**表 A**的码点集合（只扫表 A 那一段，表 B 也有 U+ 列）。

    表 A 的列是 `| emoji | 码点 | 名称 | … |`。
    """
    lines = DOC_PATH.read_text(encoding="utf-8").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith("## 二、")), None)
    assert start is not None, "清单里找不到表 A 的小节标题"
    found = set()
    for line in lines[start:]:
        if line.startswith("## 三、"):
            break
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 4 or not re.fullmatch(r"U\+[0-9A-F]{4,6}", cells[2]):
            continue
        cp = int(cells[2][2:], 16)
        assert cells[1] == chr(cp), f"清单表 A 的 emoji 列与码点列对不上: {line[:80]}"
        found.add(cp)
    assert found, "清单表 A 一行都没解析出来"
    return found


def _url(path: Path) -> str:
    from PySide6.QtCore import QUrl

    return QUrl.fromLocalFile(str(path)).toString()


def scan_code_emoji() -> set:
    """按**闸门 R2 的判据**扫代码（同一套区间，不抄第二份）。

    为什么用闸门而不是自己写一份：判据只能有一个来源，否则就会出现"清单说没有、闸门说违规"。
    R2 自己有三处盲区（SMP 漏块 / 需要 U+FE0F 的文本符号 / 逐字节拼装），
    完整的判据与命中在 `poc/_inventory_emoji.py` 与生成的清单里。
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_icon_set_gate", REPO_ROOT / "scripts" / "check_qml_rules.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_icon_set_gate"] = module
    spec.loader.exec_module(module)

    roots = ("ui", "app", "services", "launcher")
    skip = {"__pycache__", ".venv", ".git", ".mypy_cache"}
    found = set()
    for root in roots:
        base = REPO_ROOT / root
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if any(part in skip for part in path.parts):
                continue
            for char in path.read_text(encoding="utf-8"):
                if module._emoji_desc(ord(char)):
                    found.add(ord(char))
    return found


# ─── 1. 映射表 ↔ 清单 ↔ 代码 ────────────────────────────────────


def test_map_itself_is_wellformed(emoji_map):
    """映射表结构 + 每条映射指向的 SVG 真的存在。"""
    problems = map_problems(emoji_map)
    assert problems == [], "emoji-map.json 有问题:\n" + "\n".join(f"  - {p}" for p in problems)


def test_every_code_emoji_has_a_home(emoji_map):
    """**方向一**：代码里出现过的 emoji 必须在映射表里（映射或豁免），一个都不许漏。"""
    mapped = {ord(c) for c in emoji_map["emoji"]} | {ord(c) for c in emoji_map["exempt"]}
    scanned = scan_code_emoji()
    missing = sorted(scanned - mapped)
    assert missing == [], (
        "代码里出现但映射表里没有归属的 emoji: "
        + ", ".join(f"U+{cp:04X} {chr(cp)}" for cp in missing)
        + " —— 请加到 qml/assets/icons/emoji-map.json（界面元素映射图标，其余进 exempt）"
    )


def test_every_mapped_emoji_is_in_the_inventory(emoji_map):
    """**方向二**：映射表里的每个 emoji 都必须在清单里 —— 防"表里有、代码里早就没了"。"""
    if not DOC_PATH.is_file():
        pytest.skip(f"清单未生成（跑 poc/_inventory_emoji.py --write）: {DOC_PATH}")
    listed = doc_codepoints()
    mapped = {ord(c) for c in emoji_map["emoji"]} | {ord(c) for c in emoji_map["exempt"]}
    stale = sorted(mapped - listed)
    assert stale == [], (
        "映射表里登记但清单里没有的 emoji（条目过期）: "
        + ", ".join(f"U+{cp:04X} {chr(cp)}" for cp in stale)
    )
    extra = sorted(listed - mapped)
    assert extra == [], (
        "清单里有、映射表里没有归属的 emoji: "
        + ", ".join(f"U+{cp:04X} {chr(cp)}" for cp in extra)
    )


def test_inventory_covers_every_code_emoji(emoji_map):
    """清单也要覆盖代码（R2 判据）—— 三个方向都闭合，才叫"可核对"。"""
    if not DOC_PATH.is_file():
        pytest.skip(f"清单未生成: {DOC_PATH}")
    missing = sorted(scan_code_emoji() - doc_codepoints())
    assert missing == [], (
        "代码里有、清单里没有的 emoji: " + ", ".join(f"U+{cp:04X} {chr(cp)}" for cp in missing)
    )


def test_mapped_icons_cover_the_required_semantics(emoji_map):
    """任务点名的语义必须有图标文件（不能靠"以后再加"）。"""
    have = {p.stem for p in ICON_DIR.glob("*.svg")}
    missing = [name for name in REQUIRED_ICONS if name not in have]
    assert missing == [], f"任务要求的图标缺失: {missing}"


# ─── 2. 文件级 ──────────────────────────────────────────────────


def test_every_icon_file_is_valid(svg_files):
    """每个 SVG 都合规（这条同时是负例测试的被测函数）。"""
    problems = []
    for path in svg_files:
        for problem in svg_problems(path):
            problems.append(f"{path.name}: {problem}")
    assert problems == [], "SVG 不合规:\n" + "\n".join(f"  - {p}" for p in problems)


def test_icon_names_follow_the_convention(svg_files):
    bad = [p.name for p in svg_files if not ICON_NAME_RE.match(p.stem)]
    assert bad == [], f"图标命名不合规（小写 + 连字符）: {bad}"


def test_mapped_icons_all_exist_and_are_valid(emoji_map, svg_files):
    """被映射引用的 SVG 必须逐个存在且合规（映射表里写了名字就要能对上文件）。"""
    named = {str(spec["icon"]) for spec in emoji_map["emoji"].values()}
    assert named, "映射表一个图标都没引用"
    for name in sorted(named):
        path = ICON_DIR / name
        assert path.is_file(), f"映射到不存在的图标: {name}"
        assert svg_problems(path) == [], f"{name} 不合规: {svg_problems(path)}"


# ─── 3. 真实渲染 ────────────────────────────────────────────────


def test_every_icon_renders_to_a_non_empty_image(svg_files):
    """用 `QtSvg` 把每个 SVG 渲到 QImage：非空、24x24、**且有非透明像素**。

    只查"文件存在"是空断言 —— 一个空 `<svg/>` 也能存在。这里数的是真的画出来的像素。

    本组测试**刻意不在 pytest 进程里建 `QGuiApplication` / `QQmlEngine`**：
    只有 `QImage` / `QPainter` / `QSvgRenderer` 这类不需要应用实例的对象。
    真窗口那条路走子进程（见 `test_every_icon_loads_and_paints_in_qml`）。
    """
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    problems = []
    for path in svg_files:
        renderer = QSvgRenderer(str(path))
        if not renderer.isValid():
            problems.append(f"{path.name}: QSvgRenderer 认为无效")
            continue
        if renderer.defaultSize().width() != 24 or renderer.defaultSize().height() != 24:
            problems.append(f"{path.name}: defaultSize = {renderer.defaultSize()}")
        image = QImage(24, 24, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        if image.isNull() or image.size().width() != 24:
            problems.append(f"{path.name}: QImage 渲染失败")
            continue
        painted = sum(1 for y in range(image.height()) for x in range(image.width())
                      if image.pixelColor(x, y).alpha() > 0)
        if painted == 0:
            problems.append(f"{path.name}: 渲染出来是空的（0 个非透明像素）")
        elif painted < 20:
            problems.append(f"{path.name}: 只画出 {painted} 个像素，形状可能坏了")
    assert problems == [], "渲染问题:\n" + "\n".join(f"  - {p}" for p in problems)


QML_PROBE = r'''
"""子进程里的 QML 探针：把 N 个 SVG 摆成网格渲染出来，抓帧数每格画了多少像素。

为什么放子进程：见 tests/test_icon_set.py 里 `test_every_icon_loads_and_paints_in_qml`
的说明 —— 本进程里留下的 `QQmlEngine` 会把后面入口测试的 FluTheme 注入带偏。
"""
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QUrl                                    # noqa: E402
from PySide6.QtGui import QGuiApplication                          # noqa: E402
from PySide6.QtQml import QQmlComponent, QQmlEngine                # noqa: E402
from PySide6.QtQuick import QQuickWindow                           # noqa: E402

CELL, COLS = 24, 10
BACKGROUND = (0, 255, 0)

urls_path, base_path = sys.argv[1], sys.argv[2]
urls = json.loads(open(urls_path, encoding="utf-8").read())
rows = (len(urls) + COLS - 1) // COLS

SRC = """
import QtQuick
import QtQuick.Window
Window {
    id: win
    width: %(w)d; height: %(h)d; visible: true; color: "#00ff00"
    property var urls: %(urls)s
    Repeater {
        model: win.urls.length
        Image {
            objectName: "cell" + index
            x: (index %% %(cols)d) * %(cell)d
            y: Math.floor(index / %(cols)d) * %(cell)d
            width: %(cell)d; height: %(cell)d
            source: win.urls[index]
            sourceSize.width: 24; sourceSize.height: 24
        }
    }
}
""" % {"w": COLS * CELL, "h": rows * CELL, "urls": json.dumps(urls),
       "cols": COLS, "cell": CELL}

app = QGuiApplication(sys.argv[:1])
engine = QQmlEngine()
component = QQmlComponent(engine)
component.setData(SRC.encode(), QUrl.fromLocalFile(base_path))
if not component.isReady():
    print("PROBE_ERROR=" + json.dumps([e.toString() for e in component.errors()[:5]]))
    raise SystemExit(2)
window = component.create()
if window is None:
    print("PROBE_ERROR=" + json.dumps(["根对象创建失败（create() 返回 None）"]))
    raise SystemExit(2)
window.show()
for _ in range(80):
    app.processEvents()


def items_by_name(root):
    found = {}

    def walk(item):
        for child in item.childItems():
            if child.objectName():
                found[child.objectName()] = child
            walk(child)

    walk(root.contentItem())
    return found


cells = items_by_name(window)
loaded = {}
for index in range(len(urls)):
    item = cells.get("cell%d" % index)
    loaded[str(index)] = item.property("implicitWidth") if item is not None else 0

shot = window.grabWindow()
painted = {}
if not shot.isNull():
    for index in range(len(urls)):
        cx = (index % COLS) * CELL
        cy = (index // COLS) * CELL
        count = 0
        for y in range(cy, min(cy + CELL, shot.height())):
            for x in range(cx, min(cx + CELL, shot.width())):
                color = shot.pixelColor(x, y)
                if (color.red(), color.green(), color.blue()) != BACKGROUND:
                    count += 1
        painted[str(index)] = count

print("PROBE_RESULT=" + json.dumps({
    "count": len(urls),
    "loaded": loaded,
    "painted": painted,
    "grab": [shot.width(), shot.height()],
}))
window.close()
'''


def test_every_icon_loads_and_paints_in_qml(tmp_path, svg_files):
    """**实测渲染**：QML 的 `Image` 在真窗口里逐个加载并抓帧，断言每格真的画出了像素。

    为什么在**子进程**里跑（这一条是实测踩出来的，代价是 12 个 error）：
    `ThemeBridge._discover_engine()`（任务 2.8）用 `gc.get_objects()` 找进程里
    **最后一个** `QQmlEngine` 来注入 `FluTheme`。本模块若在 pytest 进程里留下引擎，
    下一个模块（`tests/test_main_qml_entry.py`）装配入口时就会把 `FluTheme` 注到
    这个既没有 FluentUI 导入路径、也没有根对象的引擎上 —— 于是它的 `App.qml` 起不来，
    `main_qml.assemble()` 抛 "QML 根对象未能创建"，并连带污染 `AppContext`（另外 3 条跟着红）。
    实测对照：带本文件全量跑 = 4 failed + 12 errors；`--ignore=tests/test_icon_set.py`
    全量跑 = 2769 passed / 0 failed。子进程隔离比"记得清理"更可靠。
    """
    payload = tmp_path / "icon_urls.json"
    payload.write_text(json.dumps([_url(p) for p in svg_files]), encoding="utf-8")
    base = tmp_path / "icon_probe.qml"
    base.write_text("// 只给 setData 一个基准 URL，内容由探针提供\n", encoding="utf-8")

    out = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", QML_PROBE, str(payload), str(base)],
        capture_output=True, text=True,
        # 必须显式给 encoding + errors：不带它时 subprocess 会用本地码页（本机 gbk）解码，
        # 遇到 UTF-8 字节就在 reader 线程抛 UnicodeDecodeError，stderr 直接变 None。
        encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT),
        env={**os.environ, "QT_QPA_PLATFORM": "offscreen"},
    )
    combined = (out.stdout or "") + (out.stderr or "")
    errors = [ln for ln in (out.stdout or "").splitlines() if ln.startswith("PROBE_ERROR=")]
    assert not errors, f"探针起不来: {errors} / stderr={out.stderr[-600:]!r}"
    lines = [ln for ln in (out.stdout or "").splitlines() if ln.startswith("PROBE_RESULT=")]
    assert lines, f"探针没输出结果（退出码 {out.returncode}）: {combined[-800:]!r}"
    result = json.loads(lines[0].split("=", 1)[1])
    assert result["count"] == len(svg_files)

    problems = []
    if result["grab"] == [0, 0]:
        problems.append("grabWindow() 返回空图（offscreen 下抓帧失败）")
    for index, path in enumerate(svg_files):
        if result["loaded"].get(str(index)) != 24:
            problems.append(f"{path.name}: Image.implicitWidth = {result['loaded'].get(str(index))}"
                            "（加载失败是 0）")
        elif result["painted"].get(str(index), 0) < 10:
            problems.append(f"{path.name}: 该格只画了 {result['painted'].get(str(index), 0)} 个像素"
                            "（抓帧后背景色没被动过）")
    assert problems == [], "QML 加载/绘制问题:\n" + "\n".join(f"  - {p}" for p in problems)


# ─── 4. Runtime.iconUrl ─────────────────────────────────────────


@pytest.fixture(scope="module")
def runtime():
    from app.bridges.runtime_bridge import RuntimeBridge

    return RuntimeBridge()


def test_icon_url_points_at_a_real_file(runtime, emoji_map):
    """槽返回的是可被 QML 直接用的 file URL，且指向真的存在的文件。"""
    from PySide6.QtCore import QUrl

    for name in sorted({str(spec["icon"])[:-4] for spec in emoji_map["emoji"].values()}):
        url = runtime.iconUrl(name)
        assert url.startswith("file:"), f"{name}: 不是 file URL（{url!r}）—— QML 会当成相对路径解析失败"
        local = QUrl(url).toLocalFile()
        assert local, f"{name}: 无法转回本地路径"
        assert Path(local).is_file(), f"{name}: {local} 不存在"
        assert Path(local).parent == ICON_DIR, f"{name}: 落到了别的目录 {local}"
        assert runtime.iconUrl(name) == url, "同一次进程里两次调用结果不一致"


def test_icon_url_accepts_name_with_extension(runtime):
    assert runtime.iconUrl("check.svg") == runtime.iconUrl("check")
    assert runtime.iconUrl("check") != ""


def test_icon_url_returns_empty_for_unknown_or_illegal_names(runtime, caplog):
    """**负例**：不存在的图标名必须返回空串（并留下 warning），不能编一个路径出来。"""
    from PySide6.QtCore import QUrl

    assert runtime.iconUrl("definitely-no-such-icon") == ""
    assert runtime.iconUrl("") == ""
    assert runtime.iconUrl("../../etc/passwd") == ""
    assert runtime.iconUrl("Check") == ""  # 大写不合命名规则
    assert runtime.iconUrl("nested/check") == ""  # 带目录不合规则
    # 正常名字必须落在图标目录里（而不是别处同名文件）
    ok = QUrl(runtime.iconUrl("check")).toLocalFile()
    assert Path(ok).parent == ICON_DIR, f"图标不来自 {ICON_DIR}: {ok}"


# ─── 5. 负例自检（证明上面不是空断言） ───────────────────────────


def test_guard_rejects_svg_with_script(tmp_path):
    """**负例**：含 `<script>` 的 SVG 必须被判不合格。"""
    bad = tmp_path / "evil.svg"
    bad.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
                   'viewBox="0 0 24 24"><script>alert(1)</script>'
                   '<path d="M0 0h24v24H0Z" fill="currentColor"/></svg>', encoding="utf-8")
    problems = svg_problems(bad)
    assert any("script" in p for p in problems), f"含 script 的 SVG 没被拦下: {problems}"


def test_guard_rejects_svg_with_gradient_and_scriptless_but_empty(tmp_path):
    """**负例**：渐变、内嵌位图、空文件、写死颜色都必须被拦下。"""
    gradient = tmp_path / "grad.svg"
    gradient.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
                        'viewBox="0 0 24 24"><linearGradient id="g"><stop offset="0"/>'
                        '</linearGradient><path d="M0 0h4v4H0Z" fill="url(#g)"/></svg>',
                        encoding="utf-8")
    assert any("渐变" in p for p in svg_problems(gradient))

    hardcoded = tmp_path / "hard.svg"
    hardcoded.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
                         'viewBox="0 0 24 24"><path d="M0 0h4v4H0Z" fill="#123456"/></svg>',
                         encoding="utf-8")
    assert any("currentColor" in p for p in svg_problems(hardcoded))

    empty = tmp_path / "empty.svg"
    empty.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
                     'viewBox="0 0 24 24"></svg>', encoding="utf-8")
    assert any("可见元素" in p for p in svg_problems(empty))

    wrong_box = tmp_path / "box.svg"
    wrong_box.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">'
                         '<path d="M0 0h4v4H0Z" fill="currentColor"/></svg>', encoding="utf-8")
    assert any("viewBox" in p for p in svg_problems(wrong_box))


def test_guard_rejects_map_entry_with_missing_icon(tmp_path):
    """**负例**：映射表指向不存在的图标，检查必须报错（不是静默通过）。"""
    fake = {
        "emoji": {"X": {"icon": "no-such-icon.svg", "codepoint": "U+0058", "name": "LATIN CAPITAL"
                        " LETTER X", "kind": "ui", "reason": "故意造的错误条目"}},
        "exempt": {},
    }
    problems = map_problems(fake, icon_dir=ICON_DIR)
    assert any("不存在" in p for p in problems), f"缺失图标没被拦下: {problems}"

    wrong_cp = {"emoji": {"X": {"icon": "check.svg", "codepoint": "U+0059", "kind": "ui",
                                "reason": "码点写错"}}, "exempt": {}}
    assert any("codepoint" in p for p in map_problems(wrong_cp, ICON_DIR))

    bad_kind = {"emoji": {"X": {"icon": "check.svg", "codepoint": "U+0058", "kind": "nope",
                                "reason": "判定非法"}}, "exempt": {}}
    assert any("kind" in p for p in map_problems(bad_kind, ICON_DIR))
