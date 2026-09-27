"""像素级视觉判据的**定义**（`tests/visual_probe.py` 与 `tests/test_visual_regression.py` 共用）。

## 为什么要有这一层

到返工 C 组为止，"界面长什么样"这件事的判据全部停在**属性层**：
`tests/test_ui_smoke.py` 记的是 `Theme.bgDark.name()`（桥的属性值），截图只判
"文件非空 + 12 页两两不同"。也就是说，**主题色有没有真的画到屏幕上**、
**有没有一块浅色的外来件压在深色界面里**，都没有任何断言。

而 R9（禁原生控件）的判据文本里写着"原生控件的颜色来自系统调色板，在锁定深色的界面里
**必然是浅色的外来件**" —— 这句话在 `poc/_probe_visual_api.py` 之前**一个像素都没量过**。
本模块把那句话变成可测量的量：

* **令牌命中**：帧里有多少块的颜色等于某个主题令牌（容差 Manhattan ≤ 6）。
  实测平坦区在降采样后是**精确值**，所以容差只用来吸收缩放取整。
* **浅色外来件**：颜色"浅"（相对亮度 ≥ 0.5）**且不属于任何令牌**。深色主题的白字、
  图标、强调色都在令牌表里，所以这条判据不会对文字误报；原生控件用的
  `#e0e0e0` / `#f6f6f6` 一定不在表里。
* **成片**：单个浅色外来色要有 ≥ 8 个块（一个块 = 4x4 原始像素）、聚成近似矩形
  （填充率 ≥ 0.5、高度 ≥ 2 块）才算"一件东西"。抗锯齿的散点凑不出这个形状。
  `SLAB_MIN_BLOCKS` 是按"最小的真实控件"定的：一个 32x32 的图标按钮 = 8x8 块。

## 定标数字（`poc/_probe_visual_api.py` 实测，offscreen / DPR=1 / 1280x840）

| 场景 | 浅色块 | 外来色占比 | 成片 |
| --- | --- | --- | --- |
| 5 个预设主题的主窗口 | 0.0% | 0.007% ~ 0.012% | **0** |
| 自研 `FmButton`（深色底、强调色填充） | 5.9% | 0.343%（文字抗锯齿） | **0** |
| 原生 `Button`（Basic 样式） | 5.1% | **5.143%** | **1 片 `#e0e0e0`×270** |

三个数字合起来说明：**成片数**才是那个既能抓住原生控件、又不对文字误报的判据；
占比只能当辅助（自研按钮的文字抗锯齿也有 0.34%）。
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

#: 降采样倍数：一个"块"= 4x4 原始像素。选 4 而不是 8 是按形状定的 ——
#: 8 倍时一个 160x36 的按钮只剩 20x4 块，边界全糊掉；4 倍时它是 40x9 块，形状还在，
#: 而整帧（1280x840）的统计耗时实测 24~35 ms。
DOWNSCALE = 4

#: 颜色容差（曼哈顿距离）。平坦区降采样后是精确值，容差只吸收缩放取整；
#: 放大会把相邻令牌判成同一种（实测 12 会让 `navBg #2a1a12` 与 `cardBg #2a1e14` 互相命中）。
TOKEN_TOL = 6

#: "浅"的门槛（相对亮度）。深色主题的所有表面都在 0.05 以下。
LIGHT_LUMA = 0.5

#: 成片判据（见模块文档的定标表）。
SLAB_MIN_BLOCKS = 8
SLAB_MIN_FILL = 0.5
SLAB_MIN_HEIGHT = 2

RGB = Tuple[int, int, int]


def color_from_hex(text: str) -> RGB:
    """`#rrggbb` → (r, g, b)。"""
    value = text.strip().lstrip("#")
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def hex_of(color: RGB) -> str:
    return "#%02x%02x%02x" % (color[0], color[1], color[2])


def relative_luminance(color: RGB) -> float:
    """WCAG 的相对亮度（不是 HSL 的 lightness：那里的 0.5 对应这里的 0.21）。"""

    def channel(value: int) -> float:
        c = value / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(color[0]) + 0.7152 * channel(color[1]) + 0.0722 * channel(color[2])


def contrast_ratio(first: RGB, second: RGB) -> float:
    """WCAG 对比度（1.0 ~ 21.0）。正文文字要求 ≥ 4.5，大字号/次要文字 ≥ 3.0。"""
    a, b = relative_luminance(first), relative_luminance(second)
    lighter, darker = max(a, b), min(a, b)
    return (lighter + 0.05) / (darker + 0.05)


def distance(first: RGB, second: RGB) -> int:
    """曼哈顿距离（比欧氏距离更适合"容差"这种口径：逐通道差值的和）。"""
    return abs(first[0] - second[0]) + abs(first[1] - second[1]) + abs(first[2] - second[2])


def block_colors(image: QImage) -> List[List[RGB]]:
    """把帧降采样成"块网格"：`[y][x]` = 该块的**平均**颜色。

    为什么是平均（`SmoothTransformation`）而不是抽样：抽样会把一个浅色实心控件采成
    "一半浅色一半背景"，判据就不稳；平均之后"整块是不是浅色"是确定的。
    """
    small = image.scaled(
        max(1, image.width() // DOWNSCALE), max(1, image.height() // DOWNSCALE),
        Qt.IgnoreAspectRatio, Qt.SmoothTransformation,
    ).convertToFormat(QImage.Format.Format_RGB32)
    raw = bytes(small.constBits())
    stride = small.sizeInBytes() // small.height()
    grid: List[List[RGB]] = []
    for y in range(small.height()):
        row = raw[y * stride:y * stride + small.width() * 4]
        grid.append([(row[x * 4 + 2], row[x * 4 + 1], row[x * 4]) for x in range(small.width())])
    return grid


def histogram(grid: Sequence[Sequence[RGB]]) -> Counter:
    counter: Counter = Counter()
    for row in grid:
        counter.update(row)
    return counter


def near_any(color: RGB, tokens: Iterable[RGB], tolerance: int = TOKEN_TOL) -> bool:
    return any(distance(color, token) <= tolerance for token in tokens)


def find_slabs(counter: Counter, positions: Mapping[RGB, Sequence[Tuple[int, int]]]) -> List[Dict[str, Any]]:
    """从候选色里挑出**成片**的那些（近似矩形的实心区域），按块数从多到少排。"""
    found: List[Dict[str, Any]] = []
    for color, count in counter.items():
        if count < SLAB_MIN_BLOCKS:
            continue
        xs = [point[0] for point in positions[color]]
        ys = [point[1] for point in positions[color]]
        width = max(xs) - min(xs) + 1
        height = max(ys) - min(ys) + 1
        fill = count / float(width * height)
        if fill >= SLAB_MIN_FILL and height >= SLAB_MIN_HEIGHT:
            found.append({
                "color": hex_of(color), "blocks": count, "bbox": [width, height],
                "fill": round(fill, 2), "luma": round(relative_luminance(color), 3),
            })
    return sorted(found, key=lambda row: -row["blocks"])


def measure(image: QImage, tokens: Mapping[str, str]) -> Dict[str, Any]:
    """一帧的像素记录。`tokens` 是"令牌名 → `#rrggbb`"。

    两次遍历：先出直方图，再只对**可能是成片的候选色**收集坐标
    （每帧 67200 个块，全量存坐标要多 5 MB 且没必要）。
    """
    grid = block_colors(image)
    counter = histogram(grid)
    total = sum(counter.values()) or 1
    table = {name: color_from_hex(value) for name, value in tokens.items()}
    hits = {name: 0 for name in tokens}
    for color, count in counter.items():
        for name, token in table.items():
            if distance(color, token) <= TOKEN_TOL:
                hits[name] += count
    light = [color for color in counter if relative_luminance(color) >= LIGHT_LUMA]
    foreign = [color for color in light if not near_any(color, table.values())]
    foreign_counter: Counter = Counter({color: counter[color] for color in foreign})
    positions: Dict[RGB, List[Tuple[int, int]]] = {}
    if foreign:
        wanted = set(foreign)
        for y, row in enumerate(grid):
            for x, color in enumerate(row):
                if color in wanted:
                    positions.setdefault(color, []).append((x, y))
    return {
        "size": [image.width(), image.height()],
        "blocks": total,
        "distinct": len(counter),
        "meanLuma": round(sum(relative_luminance(c) * n for c, n in counter.items()) / total, 4),
        "lightPct": round(100.0 * sum(counter[c] for c in light) / total, 3),
        "foreignPct": round(100.0 * sum(foreign_counter.values()) / total, 3),
        "foreignSlabs": find_slabs(foreign_counter, positions),
        "hits": hits,
        "top": [[hex_of(color), count] for color, count in counter.most_common(4)],
    }


def pct(record: Mapping[str, Any], token: str) -> float:
    """某个令牌占了屏幕的百分之几（块数占比）。"""
    blocks = record.get("blocks") or 1
    return round(100.0 * (record.get("hits", {}).get(token, 0)) / blocks, 2)
