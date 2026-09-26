"""图标上色图片提供者（阶段 2 任务 2.16）—— 把 2.10 的实测结论落地成代码。

## 为什么需要它（问题是什么）

`qml/assets/icons/*.svg` 的每个可见元素都写 `fill="currentColor"`，而 **QtSvg 把
`currentColor` 解析成不透明黑**（`qml/assets/icons/README.md` 第三节的实测表）。
暗色主题下黑图标几乎看不见，所以上色是必须的。README 那张表还实测了另外四路线：

* `ColorOverlay`（Qt5Compat）与 `MultiEffect`（QtQuick.Effects）在**本仓库所有测试的
  跑法**（`QT_QPA_PLATFORM=offscreen`）下**静默失效**（不报错、就是不变色）；
* `MultiEffect.colorization` 即使在真实窗口下色值也偏暗（期望蓝 → `0,0,76`）；
* `OpacityMask` 可行但同样在 offscreen 下失效。

对比下来只有一条路在两种环境下都精确、而且**测试验得了**：

    QQuickImageProvider + QML 侧 `image://fmcl-icon/<name>?color=%23RRGGBB`

Python 侧读 SVG 文本 → 把 `currentColor` 替换成目标色 → 交给 `QSvgRenderer` 渲成
`QImage` 返回。零 shader 依赖，两种 QPA 平台下像素都是精确色（本模块的测试逐像素验）。

## 用法

QML 侧请用组件 `FmIcon`（`qml/components/FmIcon.qml`）而不是手写 `Image`：

    FmIcon { name: "check"; color: Theme.accent; size: Theme.iconSize }

装配期**必须在 `engine.load()` 之前**注册（QML 加载时就会去取图）：

    from app.bridges.icon_provider import install as install_icon_provider
    install_icon_provider(engine)

## PySide6 6.7.3 上实测的三条（`poc/` 不在本任务范围内，实测脚本见本任务报告）

1. `QQuickImageProvider` 在 **`PySide6.QtQuick`** 里，不在 `PySide6.QtQml`
   （`QtQml` 只有 `QQmlImageProviderBase`）。
2. 传给 `requestImage` 的 `id` **保留原始百分号编码**（实测收到
   `"check?color=%23ff0000"`），Qt **不**替我们解码 —— 所以本模块自己
   `unquote()`。两种写法（`%23` 与裸 `#`）都接受。
3. `size` 是 C++ 侧 `QSize*` 出参，**PySide6 不把 Python 里的写入传回去**
   （实测 `setWidth(33)` 之后 Qt 拿到的仍是 `-1,-1`）。这不影响正确性：`Image`
   在 `size` 无效时用**返回图片的实际尺寸**，而 `FmIcon` 又总是显式给 `sourceSize`
   （`requestedSize` 是**真的**传过来的，实测 20/40 与 QML 里写的一致）。
   本模块仍会尽力写出参，只为了将来 PySide6 修好之后自动变精确。

## 线程前提

`Image` 开了 `asynchronous` 时 Qt 会在**图片加载线程**里调 `requestImage`，所以缓存的
读写加了锁。渲染本身每次新建 `QSvgRenderer`（不共享实例），SVG 文本按 (name, color)
缓存，避免每条请求都读盘。
"""

from __future__ import annotations

import logging
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Optional, Tuple
from urllib.parse import unquote

from PySide6.QtCore import QByteArray, QRectF, QSize
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQml import QQmlImageProviderBase
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer

logger = logging.getLogger("app.bridges.icon_provider")

#: QML 里用的图片提供者 id 与 URL 前缀：`image://fmcl-icon/<name>?color=%23RRGGBB`。
ICON_PROVIDER_ID = "fmcl-icon"
ICON_URL_PREFIX = f"image://{ICON_PROVIDER_ID}/"

#: 图标名规则，与 `app/bridges/runtime_bridge.py` / `tests/test_icon_set.py` 同一条：
#: 小写字母开头，小写字母/数字分段，段间用连字符。**不取 `Path(name).stem`** ——
#: 那会把 `"nested/check"` 悄悄变成 `check`（2.10 实测踩过，目录分隔符被无声吞掉）。
ICON_NAME_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")

#: SVG 里等待替换的占位色（`qml/assets/icons/README.md` 第二节的纪律）。
CURRENT_COLOR = "currentColor"

#: 没给 `requestedSize` 时的渲染边长 = SVG 的原始尺寸（全是 24x24）。
DEFAULT_SIZE = 24

#: 单边上限：挡住 `sourceSize: 100000` 这种把内存打爆的调用（返回空图 + warning）。
MAX_SIZE = 1024

#: 渲染结果缓存条数上限（键 = 图标名 + 颜色 + 尺寸）。组件库页面同屏几十个图标，
#: 尺寸与颜色就那么几种，256 条足够且不会无界增长。
CACHE_LIMIT = 256


def qml_root() -> Path:
    """`qml/` 目录（开发态 → 打包态）。与 `runtime_bridge` / `nav_bridge` 同一规则。"""
    import sys

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass) / "app_qml"
    return Path(__file__).resolve().parents[2] / "qml"


def default_icon_dir() -> Path:
    """图标资源目录（`qml/assets/icons`）。"""
    return qml_root() / "assets" / "icons"


def icon_url(name: str, color: str) -> str:
    """拼一条 QML 可直接用的 `image://` URL（`#` 必须百分号编码）。

    给 Python 侧的测试/证据脚本用；QML 侧由 `FmIcon` 自己拼（那边有 `encodeURIComponent`）。
    """
    from urllib.parse import quote

    return f"{ICON_URL_PREFIX}{name}?color={quote(str(color), safe='')}"


def parse_request(image_id: str) -> Tuple[str, str]:
    """拆 `"<name>?color=%23RRGGBB"` → `(name, "#RRGGBB")`。

    接受两种写法：`%23` 编码的（QML 的 `encodeURIComponent` 产物）与裸 `#`
    （实测 Qt 不做解码，所以两种都可能出现在这里）。颜色缺省时用 `#000000` 兜底，
    这样"只想看一眼图标形状"的调试也能用。
    """
    raw = str(image_id or "")
    name, _, query = raw.partition("?")
    color = ""
    for part in query.split("&"):
        key, _, value = part.partition("=")
        if key.strip().lower() == "color":
            color = unquote(value.strip())
            break
    return unquote(name.strip()), color


def normalize_color(text: str) -> str:
    """把颜色串折成 `#rrggbb` / `#aarrggbb`；折不出来返回空串（调用方记 warning）。"""
    candidate = str(text or "").strip()
    if not candidate:
        return "#000000"
    color = QColor(candidate)
    if not color.isValid():
        return ""
    if color.alpha() != 255:
        return color.name(QColor.NameFormat.HexArgb)
    return color.name()


def target_size(requested: Any, size: Any = None) -> Tuple[int, int]:
    """算渲染边长：优先 `requestedSize`（QML 的 `sourceSize`），否则 `size`，最后 24。

    返回 `(w, h)`；越界（<=0 或 > `MAX_SIZE`）时不在这里报错 —— 由调用方判断并记 warning。
    """
    for candidate in (requested, size):
        if candidate is None:
            continue
        try:
            width, height = int(candidate.width()), int(candidate.height())
        except Exception:  # noqa: BLE001 - 传进来的不是 QSize 就跳过这一档
            continue
        if width > 0 and height > 0:
            return width, height
    return DEFAULT_SIZE, DEFAULT_SIZE


def recolor_svg(text: str, color: str) -> str:
    """把 SVG 文本里的 `currentColor` 全部换成目标色。

    只做字符串替换（不做 XML 解析）：每个可见元素上都显式写了 `fill="currentColor"`，
    这正是 2.10 让它显式写的原因 —— 不给 QtSvg 的继承规则留解释空间。
    """
    return text.replace(CURRENT_COLOR, color)


class IconImageProvider(QQuickImageProvider):
    """`image://fmcl-icon/<name>?color=%23RRGGBB` 的提供者。"""

    def __init__(self, icon_dir: Optional[Path] = None) -> None:
        """
        Args:
            icon_dir: 图标目录。默认 `qml/assets/icons`（开发态与打包态自动区分）。
        """
        super().__init__(QQmlImageProviderBase.ImageType.Image)
        self._icon_dir = Path(icon_dir) if icon_dir is not None else default_icon_dir()
        self._cache: "OrderedDict[Tuple[str, str, int, int], QImage]" = OrderedDict()
        self._lock = threading.Lock()
        self._svg_cache: dict = {}
        self.requests = 0
        self.misses = 0

    # ─── QQuickImageProvider 接口 ───────────────────────────

    def requestImage(self, image_id: str, size: Any, requestedSize: Any) -> QImage:  # noqa: N802, N803
        """Qt 的取图入口（`Image.source` 一变就会走到这里）。

        返回**空 QImage** 表示"没有这张图"：Qt 侧表现为 `Image.status === Error`
        （`FmIcon` 会据此退化成未上色的 SVG，而不是一片空白），日志里另有 warning。
        """
        self.requests += 1
        name, raw_color = parse_request(str(image_id))
        if not ICON_NAME_RE.match(name):
            logger.warning("图标名不合法：%r（只允许小写字母、数字与连字符）", image_id)
            return QImage()
        path = self._icon_dir / f"{name}.svg"
        if not path.is_file():
            logger.warning("图标不存在：%s", path)
            return QImage()
        color = normalize_color(raw_color)
        if not color:
            logger.warning("图标 %s 的颜色 %r 不是合法颜色（应为 #rrggbb），拒绝渲染", name, raw_color)
            return QImage()
        width, height = target_size(requestedSize, size)
        if width > MAX_SIZE or height > MAX_SIZE:
            logger.warning("图标 %s 请求的尺寸 %dx%d 超过上限 %d，拒绝渲染", name, width, height, MAX_SIZE)
            return QImage()

        key = (name, color, width, height)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        self.misses += 1
        image = self._render(name, path, color, width, height)
        if image.isNull():
            return image
        with self._lock:
            self._cache[key] = image
            while len(self._cache) > CACHE_LIMIT:
                self._cache.popitem(last=False)
        return image

    # ─── 渲染 ───────────────────────────────────────────────

    def _svg_text(self, name: str, path: Path) -> str:
        """读 SVG 文本（按名字缓存，避免每条请求都读盘）。读不到返回空串。"""
        with self._lock:
            cached = self._svg_cache.get(name)
        if cached is not None:
            return cached
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.warning("读图标失败 %s: %s", path, e)
            return ""
        with self._lock:
            self._svg_cache[name] = text
        return text

    def _render(self, name: str, path: Path, color: str, width: int, height: int) -> QImage:
        """渲一张 `width x height` 的图（正方形图标居中，保持宽高比）。"""
        text = self._svg_text(name, path)
        if not text:
            return QImage()
        renderer = QSvgRenderer(QByteArray(recolor_svg(text, color).encode("utf-8")))
        if not renderer.isValid():
            logger.warning("QSvgRenderer 认为图标无效：%s", path)
            return QImage()
        image = QImage(width, height, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            side = min(width, height)
            box = QRectF((width - side) / 2.0, (height - side) / 2.0, side, side)
            renderer.render(painter, box)
        finally:
            painter.end()
        return image

    # ─── 缓存（诊断用，测试也读） ───────────────────────────

    @property
    def cacheSize(self) -> int:  # noqa: N802 - 与 QML 属性命名风格一致，便于日志
        with self._lock:
            return len(self._cache)

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._svg_cache.clear()


def install(engine: Any, icon_dir: Optional[Path] = None) -> IconImageProvider:
    """把提供者注册到 QML 引擎（**必须在 `engine.load()` 之前**调用）。

    为什么装配方得显式调一次：`main_qml.py` 不在本任务的可改范围内，而注册时机只由
    装配处决定。给的那一行写在 2.16 的报告里（`assemble()` 里 `apply_theme_font()` 之后）。

    与 `main_qml.register_bridges()` 同一约定：留一份**强引用**在引擎上
    （`addImageProvider` 不接管所有权），否则提供者被 GC 之后 QML 取图会拿到空图，
    而且没有任何有用的报错。
    """
    provider = IconImageProvider(icon_dir)
    engine.addImageProvider(ICON_PROVIDER_ID, provider)
    stores = getattr(engine, "_fmcl_icon_providers", None)
    if stores is None:
        stores = {}
        engine._fmcl_icon_providers = stores
    stores[ICON_PROVIDER_ID] = provider
    logger.info("图标上色提供者已注册：%s（图标目录 %s）", ICON_URL_PREFIX, provider._icon_dir)
    return provider


def installed(engine: Any) -> Optional[IconImageProvider]:
    """取引擎上已注册的提供者（诊断/测试用；没注册返回 None）。"""
    return getattr(engine, "_fmcl_icon_providers", {}).get(ICON_PROVIDER_ID)


__all__ = [
    "CACHE_LIMIT",
    "CURRENT_COLOR",
    "DEFAULT_SIZE",
    "ICON_NAME_RE",
    "ICON_PROVIDER_ID",
    "ICON_URL_PREFIX",
    "IconImageProvider",
    "MAX_SIZE",
    "default_icon_dir",
    "icon_url",
    "install",
    "installed",
    "normalize_color",
    "parse_request",
    "qml_root",
    "recolor_svg",
    "target_size",
]
