"""像素级视觉回归（返工 D 组）。判据在 `tests/visual_metrics.py`，采集在 `tests/visual_probe.py`。

## 它补的是哪个洞

到返工 C 组为止，界面的判据分两层：**组件契约**（`tests/test_components_qml.py`）与
**骨架走查**（`tests/ui_smoke.py`：路由、三态、主题/语言热切换、截图非空且两两不同）。
两层都在**属性层** —— 没有人断言过"主题色真的画到了屏幕上"。具体缺三条：

1. **主题落色**：`Theme.bgDark` 的值对不对，和"屏幕上那一片是不是这个色"是两件事
   （FluTheme 的映射写错、QML 里某个 `Rectangle` 硬编码，属性判据全都看不见）；
2. **浅色外来件**：R9（禁原生控件）的判据文本写着"原生控件在深色界面里必然是浅色的
   外来件"，这句话在本组之前**一个像素都没量过**；
3. **图标上色**：D-141 / D-144 的证据一直是"`Image.source` 这个字符串长什么样"，
   而不是"图标那块像素是什么颜色"。

## 判据怎么定（定标数字见 `tests/visual_metrics.py` 的模块文档）

| 判据 | 阈值 | 实测 |
| --- | --- | --- |
| 每一页/每个主题的**浅色外来成片** | `== 0` | 12 页 + 5 主题 + 画廊 2 帧 全是 0 |
| 深色主题**平均亮度** | `≤ 0.12` | 0.014 ~ 0.022 |
| 卡片/导航/页面底色覆盖率 | `≥ 30% / 10% / 8%` | 61% / 17% / 17% |
| 页头图标**近黑像素** | `== 0` | 12 页全 0（图标是主题文字色） |
| 原生 `Button` 的成片数（变异对照） | `≥ 1` | 1 片 `#e0e0e0`×270 块 |
| 自研 `FmButton` 的成片数 | `== 0` | 0 |

## 为什么不存"金标准截图"逐像素比对

那是另一套工程（要固定字体、DPR、Qt 版本，且每次合法改版都要重新生成），本仓库没有
这条流水线。这里存的是**每页/每主题的像素统计基线**（`tests/visual_baseline.json`，
覆盖率、平均亮度、颜色种数、图标像素数），带容差比对 —— 能抓住"某页变白了 / 卡片缩了 /
主题没落色"，又不会被一次合法的间距调整判红。基线用
`poc/_update_visual_baseline.py --write` 重生成，改动要在评审里看一眼。

## 慢测

一次完整采集实测约 **17 s**（12 页 + 三态 + 5 主题 + 画廊 2 帧 + 2 个变异宿主 + 抓两次
判确定性，共 25 张帧），所以整个文件标了 `pytest.mark.slow`：

    .venv\\Scripts\\python.exe -m pytest tests/test_visual_regression.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_DIR = Path(__file__).resolve().parent
PROBE = TESTS_DIR / "visual_probe.py"
BASELINE = TESTS_DIR / "visual_baseline.json"
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from PySide6.QtGui import QColor, QImage, QPainter  # noqa: E402
from visual_metrics import (  # noqa: E402
    color_from_hex,
    contrast_ratio,
    measure,
    pct,
    relative_luminance,
)

pytestmark = pytest.mark.slow

#: 一次采集的实测耗时约 17 s；超时按 8 倍给余量（CI 上 offscreen 首帧会慢）。
PROBE_TIMEOUT = 300
#: 深色主题的亮度上限（实测 0.014 ~ 0.022）。放宽到 0.12：要抓住的是"整页变亮"，
#: 而不是 0.001 的抖动。
MAX_MEAN_LUMA = 0.12
#: 浅色像素占比上限（深色主题下白字的抗锯齿也会贡献一点点）。
MAX_LIGHT_PCT = 0.10
#: 覆盖率下限：卡片 / 导航栏 / 页面底色。
MIN_CARD_PCT = 30.0
MIN_NAV_PCT = 10.0
MIN_WINDOW_PCT = 8.0
#: 页头图标的定点像素：这一块至少有这么多不透明像素，其中至少这么多接近文字色。
MIN_ICON_PIXELS = 100
MIN_ICON_TEXT_PIXELS = 5
#: 基线的容差（覆盖率按百分点、亮度按绝对值、颜色种数按比例）。
BASELINE_PCT_TOL = 8.0
BASELINE_LUMA_TOL = 0.02
BASELINE_DISTINCT_RATIO = 0.6
BASELINE_ICON_RATIO = 0.7


def _blank(width: int, height: int, color: str) -> QImage:
    """一张纯色图。`QImage.fill` 只接受单色值/`QColor`/字符串，**不**吃 `(r, g, b)` 元组。"""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return image


# ─── 1. 判据层自己也要被测（合成图，不需要引擎） ─────────────────


def test_metric_flags_a_synthetic_light_slab() -> None:
    """深色底 + 一块浅灰实心矩形 = 必须被算成"浅色外来成片"。

    这一条是**判据的变异证据**：如果 `measure()` 永远返回空列表，
    上面所有"成片 == 0"的断言都会变成空断言。
    """
    tokens = {"windowBg": "#1a1a2e", "cardBg": "#1e2a4a", "textPrimary": "#ffffff"}
    clean = measure(_blank(400, 300, "#1a1a2e"), tokens)
    assert clean["foreignSlabs"] == [], "纯底色的帧不该有成片"

    image = _blank(400, 300, "#1a1a2e")
    painter = QPainter(image)
    painter.fillRect(120, 100, 160, 36, QColor("#e0e0e0"))
    painter.end()
    dirty = measure(image, tokens)
    assert len(dirty["foreignSlabs"]) == 1, f"浅灰控件没被算成成片：{dirty['foreignSlabs']}"
    slab = dirty["foreignSlabs"][0]
    assert slab["color"] == "#e0e0e0" and slab["blocks"] >= 100
    assert dirty["foreignPct"] > 1.0, "外来占比也该涨上去"
    assert clean["foreignPct"] == 0.0


def test_metric_does_not_cry_wolf_on_themed_surfaces_or_thin_text() -> None:
    """令牌底色 + 细笔画文字 + 强调色块：**不**许报外来件（否则判据没法定阈值）。"""
    tokens = {
        "windowBg": "#1a1a2e", "cardBg": "#1e2a4a", "navBg": "#16213e",
        "accent": "#e94560", "textPrimary": "#ffffff",
    }
    image = _blank(400, 300, "#1a1a2e")
    painter = QPainter(image)
    painter.fillRect(0, 0, 60, 300, QColor("#16213e"))       # 导航栏
    painter.fillRect(80, 40, 300, 200, QColor("#1e2a4a"))    # 卡片
    painter.fillRect(100, 60, 120, 24, QColor("#e94560"))    # 强调色按钮
    for row in range(10):                                     # 细笔画文字
        painter.fillRect(100, 110 + row * 12, 200, 2, QColor("#ffffff"))
    painter.end()
    record = measure(image, tokens)
    assert record["foreignSlabs"] == [], f"令牌色被误判成外来件：{record['foreignSlabs']}"
    assert record["foreignPct"] < 1.0, f"外来占比过高：{record['foreignPct']}"
    assert record["hits"]["cardBg"] > 0 and record["hits"]["navBg"] > 0
    assert record["hits"]["accent"] > 0


def test_contrast_ratio_matches_the_wcag_reference_values() -> None:
    """对比度函数的定标：黑白 = 21、同色 = 1（判据要用它，先钉住它自己）。"""
    black, white = color_from_hex("#000000"), color_from_hex("#ffffff")
    assert contrast_ratio(black, white) == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio(white, white) == pytest.approx(1.0, abs=0.001)
    assert relative_luminance(black) == 0.0 and relative_luminance(white) == pytest.approx(1.0)
    # 深色主题的正文对比度必须够（这是"看得清"的底线）
    assert contrast_ratio(white, color_from_hex("#1a1a2e")) >= 4.5


# ─── 2. 真采集（子进程） ────────────────────────────────────────


@pytest.fixture(scope="module")
def visual(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, Any]:
    """跑一次视觉探针（整个模块只跑一次）。JSON 走文件，stdout 只留给人看。"""
    out_dir = tmp_path_factory.mktemp("visual")
    json_path = out_dir / "visual.json"
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", str(PROBE), "--out", str(out_dir / "shots"),
         "--json", str(json_path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(REPO_ROOT), timeout=PROBE_TIMEOUT,
    )
    assert completed.returncode == 0, (
        f"视觉探针失败（exit={completed.returncode}）：\n"
        f"stdout={completed.stdout[-3000:]}\nstderr={completed.stderr[-2000:]}"
    )
    assert json_path.is_file(), f"探针没落 JSON：{completed.stdout[-2000:]}"
    data = json.loads(json_path.read_text(encoding="utf-8"))
    data["_outDir"] = str(out_dir / "shots")
    return data


def test_probe_ran_clean(visual: Dict[str, Any]) -> None:
    assert visual["ok"] is True and visual["errors"] == []
    assert visual["platform"] == "offscreen", "判据是在 offscreen 下定标的"
    assert visual["pinned"]["theme"] == "default", "主题必须钉住，否则基线随个人配置变"
    assert len(visual["pages"]) == 12, f"一级领域页应有 12 个：{len(visual['pages'])}"
    # 平台噪声只有两条（字体目录 + offscreen 不支持 propagateSizeHints）。
    #
    # 这一条是**整轮**的消息判据，也是缺陷 D-145 的主钉子。为什么钉整轮而不是钉某一段：
    # 实测同一份破代码（`Repeater` 委托根读 `parent.*`）在**直接跑探针**时 29 条消息
    # 全落在画廊那一段，而在 **pytest 里跑**时它们落在更早的段落 —— 段号会随创建顺序漂移，
    # 整轮不会。按段归集的检查（`test_gallery_renders_cleanly`）保留，但只当第二道。
    for message in visual["messages"]:
        assert "TypeError" not in message and "ReferenceError" not in message, message


def test_every_page_is_dark_themed_and_free_of_foreign_light_slabs(
    visual: Dict[str, Any],
) -> None:
    """12 个一级页面：主题铺满、没有浅色外来件、页头图标是主题文字色。"""
    for row in visual["pages"]:
        route, frame = row["route"], row["frame"]
        assert frame["foreignSlabs"] == [], f"{route} 上有浅色外来件：{frame['foreignSlabs']}"
        assert frame["meanLuma"] <= MAX_MEAN_LUMA, f"{route} 的整页亮度 {frame['meanLuma']}"
        assert frame["lightPct"] <= MAX_LIGHT_PCT, f"{route} 的浅色像素 {frame['lightPct']}%"
        assert frame["distinct"] >= 80, f"{route} 只有 {frame['distinct']} 种颜色（像是空白页）"
        assert pct(frame, "cardBg") >= MIN_CARD_PCT, f"{route} 的卡片底色只有 {pct(frame, 'cardBg')}%"
        assert pct(frame, "navBg") >= MIN_NAV_PCT, f"{route} 的导航栏只有 {pct(frame, 'navBg')}%"
        assert pct(frame, "windowBg") >= MIN_WINDOW_PCT, f"{route} 的页面底色只有 {pct(frame, 'windowBg')}%"
        assert frame["hits"]["accent"] >= 1, f"{route} 上看不到强调色"
        icon = row["icon"]
        assert icon["found"] and icon["opaque"] >= MIN_ICON_PIXELS, f"{route} 的页头图标没画出来：{icon}"
        assert icon["nearTextPrimary"] >= MIN_ICON_TEXT_PIXELS, (
            f"{route} 的页头图标不是主题文字色（只有 {icon['nearTextPrimary']} 个像素命中）"
        )
        assert icon["nearBlack"] == 0, f"{route} 的页头图标里有近黑像素（D-141 的回归）：{icon}"


def test_page_icon_geometry_is_the_same_on_every_page(visual: Dict[str, Any]) -> None:
    """页头图标的落点必须一致 —— 12 个页面的骨架是同一个件，位置不该各页各样。"""
    rects = [tuple(row["icon"]["rect"]) for row in visual["pages"]]
    assert len(set(rects)) == 1, f"页头图标的位置不一致：{sorted(set(rects))}"


def test_three_states_render_and_differ(visual: Dict[str, Any]) -> None:
    """三态：都真的渲染出了对应的件、都没有外来件、内容和"加载中"不一样。"""
    rows = {row["state"]: row for row in visual["states"]}
    assert set(rows) == {"loading", "empty", "error", "ready"}, f"三态没跑全：{sorted(rows)}"
    for state in ("loading", "empty", "error"):
        row = rows[state]
        assert row["shown"] is True, f"{state} 状态件没显示"
        assert row["frame"]["foreignSlabs"] == [], f"{state} 态有浅色外来件"
        icon = row["icon"]
        assert icon["found"] and icon["opaque"] > 0, f"{state} 态的图标没画出来：{icon}"
        assert icon["nearBlack"] == 0, f"{state} 态的图标里有近黑像素：{icon}"
    # 三态必须**看得出不一样**：主色分布两两不同（否则"切了状态"这件事在屏幕上没发生）
    tops = {state: tuple(tuple(pair) for pair in rows[state]["frame"]["top"])
            for state in ("loading", "empty", "error", "ready")}
    assert len(set(tops.values())) >= 3, f"三态的像素分布太像了：{tops}"
    # 加载态那一圈进度环必须是强调色（`FmLoadingState` 用 `Theme.accent` 画环）——
    # 这是"哪个态在屏上"落到像素上的判据：
    assert rows["loading"]["frame"]["hits"]["accent"] >= 1, (
        "加载态看不到强调色（进度环没画出来，或者它不再用主题强调色）"
    )
    # 记录一条**被合法作废**的旧判据（阶段 3 任务 3.1）：原来这里写的是
    # `loading 的强调色像素 > empty 的强调色像素`，作为"空态不用强调色"的代理判据。
    # 首页变成真实页面之后，它的空态**故意**放了一颗主按钮（"管理版本" ——
    # 首启时那是整页唯一的下一步，见 `FmEmptyState.actionPrimary` 的说明），
    # 于是那个比较不再成立、也不再是任何一条设计意图的判据。
    # 取代它的是上面两条更强的直接判据：加载态**必须**有强调色（环），
    # 且"每页至少一处强调色"由 `test_every_page_is_dark_themed_and_free_of_foreign_light_slabs` 守着。


def test_every_preset_reaches_the_screen(visual: Dict[str, Any]) -> None:
    """每个预设主题：屏幕上占比最大的那块颜色就是它自己的卡片色，且强调色真的出现。"""
    names = [row["name"] for row in visual["themes"]]
    assert len(names) >= 5, f"预设主题应有 5 个：{names}"
    for row in visual["themes"]:
        frame, tokens = row["frame"], row["tokens"]
        assert frame["foreignSlabs"] == [], f"{row['name']} 上有浅色外来件：{frame['foreignSlabs']}"
        assert frame["meanLuma"] <= MAX_MEAN_LUMA, f"{row['name']} 不是深色：{frame['meanLuma']}"
        dominant = frame["top"][0][0]
        assert dominant.lower() == tokens["cardBg"].lower(), (
            f"{row['name']} 屏幕上最大的一块是 {dominant}，不是它的卡片色 {tokens['cardBg']}"
        )
        assert frame["hits"]["accent"] >= 1, (
            f"{row['name']} 看不出是哪个主题：强调色 {tokens['accent']} 一个像素都没有"
        )


def test_gallery_renders_cleanly(visual: Dict[str, Any]) -> None:
    """画廊（26 个组件同时在场）：没有外来件，而且这一段**一条 QML 报错都没有**。

    这一条是缺陷 D-145 的第二道钉子（主钉子在 `test_probe_ran_clean` 的整轮消息上）：
    `Repeater` 委托根读 `parent.width/height` 时父对象还没赋值，页面创建期会抛
    `TypeError`（`FmTable` 表头 5 条 + 画廊滚动演示 24 条）。画廊是 level 1 的开发页，
    不在冒烟测试的 12 页走查里，所以以前没人看见 —— 按段归集的检查能抓住
    "错误落在画廊这一段"的那种情况，整轮那条抓的是所有情况。
    """
    assert len(visual["gallery"]) == 2, f"画廊应有两帧（顶/底）：{len(visual['gallery'])}"
    for row in visual["gallery"]:
        assert row["frame"]["foreignSlabs"] == [], f"画廊 {row['name']} 有浅色外来件"
        assert row["frame"]["meanLuma"] <= MAX_MEAN_LUMA
        assert row["frame"]["distinct"] >= 100, f"画廊 {row['name']} 颜色太少，像是没渲染"
        assert row["messages"] == [], f"画廊 {row['name']} 报了 QML 消息：{row['messages'][:5]}"
    assert visual["gallery"][1]["contentY"] > 0, "画廊没有滚到底（底帧和顶帧是同一张图）"


def test_native_controls_are_the_light_foreign_slabs_r9_bans(visual: Dict[str, Any]) -> None:
    """R9 的像素证据：同一场景下原生控件刷出浅色成片，自研件一个都没有。

    两侧都要判：只有"原生有"而"自研没有"同时成立，R9 的判据文本才是有依据的
    （只有前者说明判据只是"抓到东西"，也可能是把自研件一起误判了）。
    """
    mutation = visual["mutation"]
    assert set(mutation) == {"FmButton", "native"}, f"变异对照缺样本：{sorted(mutation)}"
    native, own = mutation["native"], mutation["FmButton"]
    assert len(native["foreignSlabs"]) >= 1, (
        f"原生 Button 在深色主题里没有刷出浅色成片，R9 的判据文本就失去依据了：{native}"
    )
    slab = native["foreignSlabs"][0]
    assert slab["blocks"] >= 100, f"那片浅色太小，不像一个控件：{slab}"
    assert relative_luminance(color_from_hex(slab["color"])) > 0.5, f"那片不浅：{slab}"
    assert own["foreignSlabs"] == [], f"自研 FmButton 也被判成外来件了：{own['foreignSlabs']}"
    assert native["foreignPct"] > own["foreignPct"] + 1.0, (
        f"两侧的外来占比没拉开：原生 {native['foreignPct']}% vs 自研 {own['foreignPct']}%"
    )


def test_static_frames_are_deterministic_and_the_spinner_is_not(
    visual: Dict[str, Any],
) -> None:
    """静态页面必须逐字节稳定（基线才有意义），加载态必须**在动**（动画没死）。"""
    determinism = visual["determinism"]
    assert determinism["ready"]["identical"] is True, "静态页面两次抓帧不一致，像素基线不成立"
    assert determinism["loading"]["identical"] is False, (
        "加载态两次抓帧完全相同 —— 转圈的进度环没有动"
    )


# ─── 3. 与基线比对（抓"漂移"，不是抓"重画"） ─────────────────────


@pytest.fixture(scope="module")
def baseline() -> Dict[str, Any]:
    assert BASELINE.is_file(), (
        f"缺少像素基线 {BASELINE.name}；用 "
        "`.venv\\Scripts\\python.exe -X utf8 poc\\_update_visual_baseline.py --write` 生成"
    )
    return json.loads(BASELINE.read_text(encoding="utf-8"))


def test_baseline_covers_every_page_and_theme(visual: Dict[str, Any], baseline: Dict[str, Any]) -> None:
    assert sorted(baseline["pages"]) == sorted(row["route"] for row in visual["pages"])
    assert sorted(baseline["themes"]) == sorted(row["name"] for row in visual["themes"])
    assert baseline["schema"] == 1


def test_pages_match_the_recorded_baseline(visual: Dict[str, Any], baseline: Dict[str, Any]) -> None:
    """每一页的覆盖率 / 亮度 / 颜色种数 / 图标像素数都要在基线容差内。"""
    problems: List[str] = []
    for row in visual["pages"]:
        route, frame, icon = row["route"], row["frame"], row["icon"]
        want = baseline["pages"][route]
        for token, key, tolerance in (("cardBg", "cardBgPct", BASELINE_PCT_TOL),
                                      ("navBg", "navBgPct", BASELINE_PCT_TOL),
                                      ("windowBg", "windowBgPct", BASELINE_PCT_TOL)):
            got = pct(frame, token)
            if abs(got - want[key]) > tolerance:
                problems.append(f"{route}.{key}: {got}% vs 基线 {want[key]}%（容差 {tolerance}）")
        if abs(frame["meanLuma"] - want["meanLuma"]) > BASELINE_LUMA_TOL:
            problems.append(f"{route}.meanLuma: {frame['meanLuma']} vs {want['meanLuma']}")
        if frame["distinct"] < want["distinct"] * BASELINE_DISTINCT_RATIO:
            problems.append(f"{route}.distinct: {frame['distinct']} vs 基线 {want['distinct']}")
        if icon["opaque"] < want["iconOpaque"] * BASELINE_ICON_RATIO:
            problems.append(f"{route}.iconOpaque: {icon['opaque']} vs 基线 {want['iconOpaque']}")
    assert problems == [], "与像素基线不符：\n  " + "\n  ".join(problems)


def test_themes_match_the_recorded_baseline(visual: Dict[str, Any], baseline: Dict[str, Any]) -> None:
    problems: List[str] = []
    for row in visual["themes"]:
        name, frame = row["name"], row["frame"]
        want = baseline["themes"][name]
        dominant = frame["top"][0][0].lower()
        if dominant != want["dominant"]:
            problems.append(f"{name}.dominant: {dominant} vs 基线 {want['dominant']}")
        if abs(frame["meanLuma"] - want["meanLuma"]) > BASELINE_LUMA_TOL:
            problems.append(f"{name}.meanLuma: {frame['meanLuma']} vs {want['meanLuma']}")
        if frame["distinct"] < want["distinct"] * BASELINE_DISTINCT_RATIO:
            problems.append(f"{name}.distinct: {frame['distinct']} vs 基线 {want['distinct']}")
    assert problems == [], "主题像素与基线不符：\n  " + "\n  ".join(problems)
