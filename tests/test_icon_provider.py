"""`IconImageProvider`（阶段 2 任务 2.16 的图标上色）测试。

## 这一组测试想钉住什么

图标上色是"看起来做了、其实是黑的"这类问题的高发区（`qml/assets/icons/README.md`
第三节实测了五种路线的失败方式）。所以这里不查"有没有调用过"，而是**数像素**：

1. **颜色真的是目标色**：渲染出来的每个非透明像素的 RGB 都必须等于请求的颜色
   （不是黑、不是偏暗 —— `MultiEffect.colorization` 那种"生效但偏暗"会被这条抓住）；
2. **尺寸跟着 `requestedSize` 走**：QML 的 `sourceSize` 就是这个参数（实测 Qt 在
   真实窗口下按设备像素比放大它，DPR=1.25 时 sourceSize 20 -> 请求 25），
   所以按它渲染才能在高 DPI 下不糊；
3. **两种环境都验**：`offscreen`（本仓库所有测试的跑法）与真实 Windows 平台
   （`QT_QPA_PLATFORM=windows`）各跑一次**真窗口抓帧**，两边像素都要精确；
4. **负例不崩**：未知图标名 / 非法名 / 非法颜色一律返回**空 QImage** + warning，
   不抛异常、不编一个假路径出来；
5. **百分号编码两种写法都能用**：Qt 实测**不**替我们解码 id（收到的是
   `check?color=%23ff0000`），所以 provider 自己 `unquote`，`%23` 与裸 `#` 都要认；
6. **缓存行为**：同参数只渲染一次、不同参数各一份、条数有上限（不会无界增长）。

## 环境前提

`QT_QPA_PLATFORM=offscreen`（在 import PySide6 之前设）。本模块**只在子进程里**建
`QGuiApplication` / `QQmlEngine`：pytest 进程里留下的引擎会把
`ThemeBridge._discover_engine()` 的 `gc` 扫描带偏（`tests/test_icon_set.py` 的模块
文档记着这次事故的代价 —— 12 个 error）。provider 自身的断言只需要
`QImage` / `QSvgRenderer` / `QPainter`，**不需要**应用实例。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# 必须在 import PySide6 之前设好（本进程可能已经有别的 Qt 测试跑过）
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.bridges import icon_provider as ip  # noqa: E402

ICON_DIR = REPO_ROOT / "qml" / "assets" / "icons"

#: 测试用的目标色（都是"一眼能看出不是黑"的颜色）
RED = "#ff0000"
GREEN = "#00ff00"
ACCENTISH = "#e94560"


@pytest.fixture(scope="module")
def provider() -> ip.IconImageProvider:
    return ip.IconImageProvider(ICON_DIR)


def rgb_of(color_hex: str) -> tuple:
    from PySide6.QtGui import QColor

    color = QColor(color_hex)
    return (color.red(), color.green(), color.blue())


def opaque_pixels(image) -> list:
    """(x, y, r, g, b) 列表：所有 alpha > 0 的像素。"""
    out = []
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() > 0:
                out.append((x, y, color.red(), color.green(), color.blue()))
    return out


def fetch(provider: ip.IconImageProvider, image_id: str, size: int = 32):
    from PySide6.QtCore import QSize

    return provider.requestImage(image_id, QSize(-1, -1), QSize(size, size))


# ─── 1. 颜色与像素（本任务的核心结论） ──────────────────────────


@pytest.mark.parametrize("color", [RED, GREEN, ACCENTISH])
def test_rendered_pixels_are_the_requested_color_not_black(provider, color):
    """渲出来的像素必须是**精确**的目标色，而且不是黑。

    判据落在**完全不透明**的像素上（`alpha == 255`）：抗锯齿的过渡像素是目标色与
    透明之间的混合，反预乘之后会有 ±1 的舍入误差（`check.svg` 的斜笔画上实测就是如此），
    拿它们做精确比较只会得到假失败。而"笔画内部"必须一个像素都不差 ——
    这条同时挡住"根本没换色"（全是黑）与"换了但偏暗"（`MultiEffect.colorization`
    那种 `0,0,76`）两种失败。
    """
    image = fetch(provider, f"check?color=%23{color[1:]}")
    assert not image.isNull(), "check.svg 应该能渲出来"
    assert image.width() == 32 and image.height() == 32

    expected = rgb_of(color)
    solid = []
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if pixel.alpha() == 255:
                solid.append((pixel.red(), pixel.green(), pixel.blue()))
    assert len(solid) >= 20, f"只有 {len(solid)} 个完全不透明的像素，形状可能坏了"
    wrong = [rgb for rgb in solid if rgb != expected]
    assert wrong == [], f"有 {len(wrong)} 个实心像素不是 {color}：{wrong[:5]}（黑色=没换色）"


def test_no_dark_pixels_at_all(provider):
    """任何有一定不透明度的像素都不该是黑的 —— `currentColor` 没被替换时的症状。"""
    image = fetch(provider, f"check?color=%23{ACCENTISH[1:]}")
    dark = [(x, y) for x, y, r, g, b in opaque_pixels(image) if max(r, g, b) <= 40]
    assert dark == [], f"有 {len(dark)} 个近黑像素：{dark[:5]}"


def test_different_colors_give_different_pixels(provider):
    """换个颜色必须真的换（防"颜色参数被忽略、永远渲同一个色"）。"""
    red = fetch(provider, f"check?color=%23{RED[1:]}")
    green = fetch(provider, f"check?color=%23{GREEN[1:]}")
    assert opaque_pixels(red) != opaque_pixels(green)


def test_all_check_icons_render_at_least_one_pixel(provider):
    """图标集里的每一个都要能渲出来（provider 是它们的统一出口）。"""
    empty = []
    for path in sorted(ICON_DIR.glob("*.svg")):
        image = fetch(provider, f"{path.stem}?color=%23{ACCENTISH[1:]}", 24)
        if image.isNull() or not opaque_pixels(image):
            empty.append(path.name)
    assert empty == [], f"这些图标渲出来是空的：{empty}"


# ─── 2. 尺寸 ────────────────────────────────────────────────────


def test_size_follows_the_requested_size(provider):
    from PySide6.QtCore import QSize

    for size in (16, 24, 32, 48):
        image = provider.requestImage(f"check?color=%23{RED[1:]}", QSize(-1, -1), QSize(size, size))
        assert (image.width(), image.height()) == (size, size)

    # 没有 requestedSize 时退回 SVG 的原始尺寸（24x24）
    fallback = provider.requestImage(f"check?color=%23{RED[1:]}", QSize(-1, -1), QSize(-1, -1))
    assert (fallback.width(), fallback.height()) == (ip.DEFAULT_SIZE, ip.DEFAULT_SIZE)


def test_non_square_request_keeps_the_aspect_ratio(provider):
    """非正方形请求：图标居中、保持宽高比（不能被拉伸成扁的）。

    40x20 的请求里，正方形边长取 `min(40, 20) = 20`，所以内容落在 y ∈ [0, 20)。
    """
    from PySide6.QtCore import QSize

    image = provider.requestImage(f"check?color=%23{RED[1:]}", QSize(-1, -1), QSize(40, 20))
    assert (image.width(), image.height()) == (40, 20)
    pixels = opaque_pixels(image)
    assert pixels, "内容不该因为非正方形请求就消失"
    xs = [x for x, _, _, _, _ in pixels]
    ys = [y for _, y, _, _, _ in pixels]
    assert min(xs) >= 10 and max(xs) < 30, f"横向应当居中（x ∈ [10, 30)，实际 {min(xs)}..{max(xs)}）"
    assert min(ys) >= 0 and max(ys) < 20, f"纵向应当占满（y ∈ [0, 20)，实际 {min(ys)}..{max(ys)}）"


def test_absurd_size_is_rejected(provider, caplog):
    from PySide6.QtCore import QSize

    with caplog.at_level("WARNING"):
        image = provider.requestImage(f"check?color=%23{RED[1:]}", QSize(-1, -1), QSize(99999, 99999))
    assert image.isNull(), "超过上限的尺寸必须拒绝，不能真的去分配那张图"
    assert "上限" in caplog.text


# ─── 3. 负例：未知 / 非法输入 ───────────────────────────────────


def test_unknown_icon_returns_empty_image_and_warns(provider, caplog):
    with caplog.at_level("WARNING"):
        image = fetch(provider, f"definitely-no-such-icon?color=%23{RED[1:]}")
    assert image.isNull(), "不存在的图标必须返回空图（QML 侧表现为 status=Error）"
    assert "图标不存在" in caplog.text
    # 再要一次也不许抛
    assert fetch(provider, f"definitely-no-such-icon?color=%23{RED[1:]}").isNull()


@pytest.mark.parametrize("image_id", ["", "Check?color=%23ff0000", "nested/check?color=%23ff0000",
                                      "../../etc/passwd?color=%23ff0000", "check.svg?color=%23ff0000"])
def test_illegal_names_are_rejected(provider, caplog, image_id):
    """命名规则与 `runtime_bridge.iconUrl` 同一条：小写 + 连字符，带目录/大写/扩展名都不认。"""
    with caplog.at_level("WARNING"):
        image = fetch(provider, image_id)
    assert image.isNull()
    assert "不合法" in caplog.text or "图标不存在" in caplog.text


@pytest.mark.parametrize("color", ["notacolor", "%23zzzzzz", "rgb(1,2,3)", "12345"])
def test_illegal_color_is_rejected(provider, caplog, color):
    with caplog.at_level("WARNING"):
        image = fetch(provider, f"check?color={color}")
    assert image.isNull(), "非法颜色必须拒绝渲染（否则会把一个说不清的色画到界面上）"
    assert "不是合法颜色" in caplog.text


def test_missing_color_falls_back_to_black_for_shape_debugging(provider):
    """不写 color 时按黑色渲染（调试"这个图标画出来是什么形状"用；不是正常用法）。"""
    image = fetch(provider, "check")
    pixels = opaque_pixels(image)
    assert len(pixels) >= 20
    assert {rgb for _, _, r, g, b in pixels for rgb in [(r, g, b)]} == {(0, 0, 0)}


# ─── 4. `#` 的两种百分号编码写法 ────────────────────────────────


def test_both_percent_encodings_produce_the_same_pixels(provider):
    """`%23ff0000` 与裸 `#ff0000` 必须等价。

    实测背景：Qt 传给 `requestImage` 的 id **保留原始百分号编码**
    （`poc` 探针收到的是 `"check?color=%23ff0000"`），Qt 不做解码 —— 所以本模块
    自己 `unquote`，两种写法都得认；QML 侧 `encodeURIComponent` 产出的是前者。
    """
    encoded = fetch(provider, "check?color=%23ff0000")
    raw = fetch(provider, "check?color=#ff0000")
    assert not encoded.isNull() and not raw.isNull()
    assert opaque_pixels(encoded) == opaque_pixels(raw)


def test_parse_request_handles_the_shapes_qml_and_tests_produce():
    assert ip.parse_request("check?color=%23ff0000") == ("check", "#ff0000")
    assert ip.parse_request("check?color=#ff0000") == ("check", "#ff0000")
    assert ip.parse_request("check") == ("check", "")
    assert ip.parse_request("check?color=%23ff000080&x=1") == ("check", "#ff000080")
    assert ip.parse_request("folder-open?COLOR=%23ABCDEF") == ("folder-open", "#ABCDEF")
    assert ip.parse_request("") == ("", "")


def test_icon_url_helper_matches_what_qml_builds():
    """`icon_url()`（Python 侧给测试/证据脚本用）与 QML 拼出来的形状一致。"""
    url = ip.icon_url("check", RED)
    assert url == "image://fmcl-icon/check?color=%23ff0000"
    assert ip.parse_request(url[len(ip.ICON_URL_PREFIX):]) == ("check", RED)


def test_normalize_color_forms():
    """输入输出都是 Qt 的十六进制语义：`#rrggbb` 或 `#aarrggbb`（与主题桥同一条约定）。"""
    assert ip.normalize_color("#FF0000") == "#ff0000"
    assert ip.normalize_color("#f00") == "#ff0000"
    assert ip.normalize_color("") == "#000000"
    assert ip.normalize_color("nope") == ""
    # 8 位写法 Qt 按 #aarrggbb 解释：这里 alpha=0x80、颜色是纯红
    assert ip.normalize_color("#80ff0000") == "#80ff0000"
    # 反过来写（先 rgb 后 alpha）会被 Qt 读成"蓝色 alpha=ff"，实现只是如实透出 Qt 的解析
    assert ip.normalize_color("#ff000080") == "#000080"


# ─── 5. 缓存 ────────────────────────────────────────────────────


def fresh_provider() -> ip.IconImageProvider:
    """每个缓存用例一个独立实例（计数干净，互不干扰）。"""
    return ip.IconImageProvider(ICON_DIR)


def test_cache_serves_the_second_request_without_rendering_again():
    provider = fresh_provider()
    first = fetch(provider, "check?color=%23ff0000")
    misses = provider.misses
    second = fetch(provider, "check?color=%23ff0000")
    assert provider.cacheSize == 1, "同参数只该有一份缓存"
    assert provider.misses == misses, "第二次不该再渲一遍"
    assert opaque_pixels(first) == opaque_pixels(second)


def test_cache_key_includes_color_and_size():
    provider = fresh_provider()
    fetch(provider, "check?color=%23ff0000", 24)
    fetch(provider, "check?color=%2300ff00", 24)  # 换色
    fetch(provider, "check?color=%23ff0000", 32)  # 换尺寸
    assert provider.cacheSize == 3, "颜色或尺寸不同就是不同的缓存条目（否则会串色/串尺寸）"
    assert provider.misses == 3


def test_cache_equivalent_spellings_share_one_entry():
    """`#FF0000` 与 `#ff0000`、`%23` 与裸 `#` 都是同一条缓存。"""
    provider = fresh_provider()
    fetch(provider, "check?color=%23FF0000")
    fetch(provider, "check?color=#ff0000")
    assert provider.cacheSize == 1 and provider.misses == 1


def test_cache_is_bounded():
    provider = fresh_provider()
    for index in range(ip.CACHE_LIMIT + 40):
        color = "#{:06x}".format((index * 7919) % 0xffffff)
        fetch(provider, f"check?color=%23{color[1:]}", 24)
    assert provider.cacheSize <= ip.CACHE_LIMIT, "缓存必须有上限，不然长时间跑会一直涨"
    assert provider.requests >= ip.CACHE_LIMIT + 40


def test_clear_cache_empties_both_levels():
    provider = fresh_provider()
    fetch(provider, "check?color=%23ff0000")
    assert provider.cacheSize == 1
    provider.clear_cache()
    assert provider.cacheSize == 0
    # 清完之后仍然能用（SVG 文本缓存也一起清了，必须能重读）
    assert opaque_pixels(fetch(provider, "check?color=%23ff0000"))


def test_exhausted_cache_paths_still_render_after_clear():
    """负例路径也不该往缓存里塞东西（否则"坏名字"会把好名字挤出去）。"""
    provider = fresh_provider()
    fetch(provider, "no-such-icon?color=%23ff0000")
    fetch(provider, "check?color=nope")
    fetch(provider, "check?color=%23ff0000")  # 这一条才应该进缓存
    assert provider.cacheSize == 1


# ─── 6. 装配期注册（不需要引擎：只验"注册动作"与强引用约定） ────


class _FakeEngine:
    """只为 `install()` 准备的替身：真引擎要 `QGuiApplication` 才能建，见模块文档。"""

    def __init__(self) -> None:
        self.providers = {}

    def addImageProvider(self, provider_id, provider):  # noqa: N802 - 与 Qt 同名
        self.providers[provider_id] = provider


def test_install_registers_under_the_frozen_id_and_keeps_a_strong_reference():
    engine = _FakeEngine()
    provider = ip.install(engine, ICON_DIR)
    assert ip.ICON_PROVIDER_ID == "fmcl-icon"
    assert engine.providers["fmcl-icon"] is provider
    assert ip.installed(engine) is provider, (
        "必须在引擎上留一份强引用：addImageProvider 不接管所有权，provider 被 GC 之后"
        "QML 取图会拿到空图而且没有任何有用的报错（与 main_qml 的 _fmcl_bridges 同一约定）"
    )
    assert ip.installed(_FakeEngine()) is None


def test_default_icon_dir_is_the_repo_icon_set():
    assert ip.default_icon_dir() == ICON_DIR
    assert ip.qml_root().name == "qml"


def test_icon_name_rule_is_the_same_as_the_runtime_bridge():
    """命名规则只能有一份判据（2.10 的 `Runtime.iconUrl` 与本 provider 同规则）。"""
    from app.bridges.runtime_bridge import _ICON_NAME_RE as runtime_re

    samples = ["check", "folder-open", "a1", "mood-great", "Check", "check.svg", "nested/check",
               "", "9lives", "check-", "-check", "check_icon"]
    for name in samples:
        assert bool(ip.ICON_NAME_RE.match(name)) == bool(runtime_re.match(name)), name


def test_recolor_svg_replaces_every_occurrence():
    text = '<svg><path fill="currentColor"/><rect fill="currentColor"/></svg>'
    out = ip.recolor_svg(text, "#123456")
    assert "currentColor" not in out
    assert out.count("#123456") == 2


# ─── 7. 两种 QPA 环境下的真窗口抓帧（子进程） ──────────────────

#: 子进程探针：真 `QQmlApplicationEngine` + 真窗口 + `grabWindow()` 数像素。
#: 为什么必须在**子进程**里：见模块文档（留在 pytest 进程里的引擎会污染后面的入口测试）。
#:
#: 两个关键细节（都是实测踩出来的）：
#:   * `sourceSize` 必须**等于**显示尺寸（48x48 画 48x48）。`sourceSize` 小于显示尺寸时
#:     Image 会把光栅图**平滑放大**，斜笔画的过渡像素把实心像素也糊掉了 ——
#:     实测 24 渲到 40 时"精确色"像素 = 0，24 渲到 24 时 = 10，48 渲到 48 时 = 133。
#:     `FmIcon` 正是这么写的（`sourceSize` 跟着 `size` 走），这里与它保持一致。
#:   * 计数**不按坐标区域**，而是全图统计目标色出现次数：真实平台下 Qt 会按设备像素比
#:     调整窗口与抓帧尺寸（实测 DPR=1.25 时请求 96x48 的窗口抓到 153x60），
#:     按坐标切格子会数错地方。
PLATFORM_PROBE = r'''
import json
import os
import sys

os.environ["QT_QPA_PLATFORM"] = sys.argv[1]
platform_name = sys.argv[1]
repo_root = sys.argv[2]
sys.path.insert(0, repo_root)

from PySide6.QtCore import QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtQml import QQmlApplicationEngine

from app.bridges.icon_provider import install

#: 两格：目标色是 (233, 69, 96) 与 (0, 255, 0)
CELL = 48
TARGETS = {"accent": (233, 69, 96), "green": (0, 255, 0)}

QML = """
import QtQuick
import QtQuick.Window

Window {
    width: 96
    height: 48
    visible: true
    color: "white"

    Image {
        objectName: "cell0"
        x: 0; y: 0; width: 48; height: 48
        source: "image://fmcl-icon/check?color=%23e94560"
        sourceSize.width: 48; sourceSize.height: 48
    }

    Image {
        objectName: "cell1"
        x: 48; y: 0; width: 48; height: 48
        source: "image://fmcl-icon/check?color=%2300ff00"
        sourceSize.width: 48; sourceSize.height: 48
    }
}
"""

try:
    app = QGuiApplication(sys.argv[:1])
except Exception as exc:  # 平台插件起不来（无显示器的 CI）—— 报出来，父测试决定跳过
    print("PROBE_SKIP=" + json.dumps({"platform": platform_name, "reason": str(exc)}))
    raise SystemExit(3)

engine = QQmlApplicationEngine()
provider = install(engine, os.path.join(repo_root, "qml", "assets", "icons"))
engine.loadData(QML.encode("utf-8"),
                QUrl.fromLocalFile(os.path.join(repo_root, "tmp", "platform_probe.qml")))
roots = engine.rootObjects()
if not roots:
    print("PROBE_ERROR=" + json.dumps({"platform": platform_name, "why": "root not created"}))
    raise SystemExit(2)
window = roots[0]
for _ in range(80):
    app.processEvents()

shot = window.grabWindow()
near = {key: 0 for key in TARGETS}
best = {key: 999 for key in TARGETS}
near_black = 0
for y in range(shot.height()):
    for x in range(shot.width()):
        pixel = shot.pixelColor(x, y)
        got = (pixel.red(), pixel.green(), pixel.blue())
        for key, want in TARGETS.items():
            delta = max(abs(a - b) for a, b in zip(got, want))
            best[key] = min(best[key], delta)
            if delta <= 8:
                near[key] += 1
        if max(got) <= 40:
            near_black += 1

print("PROBE_RESULT=" + json.dumps({
    "platform": platform_name,
    "qpa": app.platformName(),
    "dpr": float(window.devicePixelRatio()),
    "logical": [window.width(), window.height()],
    "grab": [shot.width(), shot.height()],
    "requests": provider.requests,
    "near": near,
    "best": best,
    "nearBlack": near_black,
}))
'''


def run_platform_probe(tmp_path: Path, platform: str) -> dict:
    """在子进程里用指定 QPA 平台跑一次探针；平台起不来就 `pytest.skip`。"""
    script = tmp_path / f"platform_probe_{platform}.py"
    script.write_text(PLATFORM_PROBE, encoding="utf-8", newline="\n")
    done = subprocess.run(
        [sys.executable, "-X", "utf8", str(script), platform, str(REPO_ROOT)],
        capture_output=True,
        text=True,
        # 必须显式给 encoding：不带它时按本地码页（本机 gbk）解码子进程输出，
        # 遇到 UTF-8 字节会在 reader 线程抛 UnicodeDecodeError（阶段 1 修过同族缺陷）
        encoding="utf-8",
        errors="replace",
        cwd=str(REPO_ROOT),
    )
    stdout = done.stdout or ""
    skips = [ln for ln in stdout.splitlines() if ln.startswith("PROBE_SKIP=")]
    if skips:
        pytest.skip(f"该 QPA 平台在这台机器上起不来：{skips[0][len('PROBE_SKIP='):]}")
    errors = [ln for ln in stdout.splitlines() if ln.startswith("PROBE_ERROR=")]
    assert not errors, f"{platform} 探针起不来：{errors} / stderr={done.stderr[-500:]!r}"
    lines = [ln for ln in stdout.splitlines() if ln.startswith("PROBE_RESULT=")]
    assert lines, f"{platform} 探针没输出结果（退出码 {done.returncode}）：{stdout[-800:]!r}"
    return json.loads(lines[0].split("=", 1)[1])


def test_native_qpa_platform_name() -> None:
    """给下面那条参数化测试用的平台名（Windows / macOS / Linux 各一个）。"""
    if os.name == "nt":
        assert native_platform() == "windows"
    else:
        assert native_platform() in ("cocoa", "xcb")


def native_platform() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "cocoa"
    return "xcb"


@pytest.mark.parametrize("platform", ["offscreen", "native"])
def test_qml_renders_exact_colors_in_a_real_window(tmp_path, platform):
    """**两种环境各抓一次帧**：offscreen（测试跑法）与真实原生平台。

    这条是"上色真的成立"的端到端证据：QML 的 `Image` -> `image://fmcl-icon` ->
    provider 渲成目标色 -> 合成到窗口 -> `grabWindow()` 逐像素数。
    没有 shader、没有 `ColorOverlay`，所以两边都该是精确色（README 第三节的对照表里，
    四种 shader 方案在 offscreen 下全部静默失效，只有这条路两边都对）。

    判据：目标色 ±8 以内的像素 >= 20 个、最接近的像素误差 <= 8、近黑像素 0 个。
    """
    resolved = native_platform() if platform == "native" else platform
    result = run_platform_probe(tmp_path, resolved)
    assert result["requests"] >= 2, "QML 侧应当向 provider 要过图"
    assert result["grab"] != [0, 0], "抓帧失败（grabWindow 返回空图）"
    for color, key in (("#e94560", "accent"), ("#00ff00", "green")):
        assert result["near"][key] >= 20, (
            f"{resolved}: 目标色 {color} 只有 {result['near'][key]} 个像素落在 ±8 以内"
            f"（dpr={result['dpr']}, window={result['logical']}, grab={result['grab']}）"
        )
        assert result["best"][key] <= 8, (
            f"{resolved}: 最接近 {color} 的像素差 {result['best'][key]} —— "
            "说明画出来的不是这个颜色（`MultiEffect.colorization` 那种偏暗会在这里红）"
        )
    assert result["nearBlack"] == 0, (
        f"{resolved}: 抓到 {result['nearBlack']} 个近黑像素 —— `currentColor` 没被替换"
        "（QtSvg 的默认行为就是黑），provider 没生效或者没注册"
    )
