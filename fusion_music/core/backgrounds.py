"""自定义背景图的入库、替换与清理（设置 → 外观 → 背景图）。

设计取舍
--------
* **先复制进 ``data/backgrounds/``，配置里只存文件名**：便携（拷走 data 就带走
  全部数据）、原图挪位置或删掉都不影响程序，也不会把用户目录里的绝对路径写进
  配置文件。文件名取**内容摘要**，同一张图反复选用不会堆副本。
* **入库前让 Qt 自己认一遍**（``QImageReader``，只看头部不解码整张）：扩展名与
  内容对不上、或者当前 Qt 没装这个格式的解码器时，当场拒绝并说明原因 —— 比等到
  界面上显示一块空白再回头查要好。判定按**内容**来（``setDecideFormatFromContent``），
  所以存下来的扩展名是 Qt 认出来的真实格式，而不是用户文件名的后缀。
* 入库、替换、清理都只碰 ``backgrounds/`` 这一个目录，失败一律抛出
  :class:`BackgroundError`（原因直接给用户看），**绝不让背景图把设置界面搞崩**。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .. import paths
from ..units import human_size

logger = logging.getLogger(__name__)

#: 单张图体积上限。再大的多半是相机原图/素材图，加载进界面只会拖慢一切。
MAX_BYTES = 20 * 1024 * 1024
#: 解码后的像素上限。QML 侧虽然会按 sourceSize 缩到长边 2560 再解码，
#: 但 PNG 这类格式仍要先整张解开才能缩，12000×9000 的图会瞬间吃掉几百 MB。
MAX_PIXELS = 40_000_000
#: Qt 认出来的格式名 → 落盘扩展名
FORMAT_SUFFIX = {
    "jpeg": ".jpg", "jpg": ".jpg", "png": ".png", "webp": ".webp", "bmp": ".bmp",
    "gif": ".gif", "tiff": ".tif", "tif": ".tif", "svg": ".svg", "ico": ".ico",
}


class BackgroundError(Exception):
    """入库失败的原因，文案直接展示给用户。"""


@dataclass
class BackgroundImage:
    """一张已入库的背景图。"""

    name: str          # 文件名（存进配置的就是它）
    path: Path         # 绝对路径
    width: int
    height: int
    size: int          # 字节

    @property
    def url(self) -> str:
        """QML 里能直接用的文件 URL。

        用 ``Path.as_uri()`` 而不是 ``QUrl.fromLocalFile``：这一层不该依赖 Qt，
        而 ``as_uri()`` 生成的 ``file:///D:/...`` 正是 QML 认的形式。
        """
        return self.path.as_uri()

    @property
    def size_text(self) -> str:
        return human_size(self.size)

    @property
    def info_text(self) -> str:
        if self.width and self.height:
            return f"{self.width}×{self.height} · {self.size_text} · {self.name}"
        return f"{self.size_text} · {self.name}"


def _probe(path: Path) -> Tuple[str, int, int]:
    """让 Qt 认一下这张图，返回 ``(格式名, 宽, 高)``。

    只读图片头，不解码像素（大图也不会卡）。装了 Qt 才会走到这里 ——
    ``tests/test_core.py`` 是不依赖 Qt 的离线测试，取不到就只做文件级校验。
    """
    try:
        from PySide6.QtGui import QImageReader
    except Exception:  # pragma: no cover - 只有无 Qt 的裸环境下会走到
        return ("", 0, 0)

    reader = QImageReader(str(path))
    reader.setDecideFormatFromContent(True)
    if not reader.canRead():
        raise BackgroundError("这个文件不是能识别的图片（或系统缺少对应的解码器）")
    size = reader.size()
    width, height = int(size.width()), int(size.height())
    if width <= 0 or height <= 0:
        raise BackgroundError("图片尺寸读不出来，文件可能已损坏")
    if width * height > MAX_PIXELS:
        raise BackgroundError(f"图片太大（{width}×{height}），请先缩放后再用")
    fmt = bytes(reader.format() or b"").decode("ascii", "ignore").lower()
    return (fmt, width, height)


def resolve(name: str) -> Optional[BackgroundImage]:
    """把配置里的文件名解析成可用对象；文件缺失/损坏一律返回 None。

    界面据此回退到纯色底，而不是对着一张不存在的图干等（日志里会留一行）。
    """
    name = str(name or "").strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    path = paths.backgrounds_dir() / name
    try:
        if not path.is_file():
            logger.info("背景图不存在，回退到纯色底：%s", path)
            return None
        size = path.stat().st_size
        if size <= 0 or size > MAX_BYTES:
            logger.warning("背景图大小异常（%s 字节），回退到纯色底：%s", size, path)
            return None
        _fmt, width, height = _probe(path)
        return BackgroundImage(name=name, path=path, width=width, height=height, size=size)
    except BackgroundError as e:
        logger.warning("背景图无法使用（%s），回退到纯色底：%s", e, path)
        return None
    except OSError as e:
        logger.warning("读取背景图失败: %s", e)
        return None


def store(source: str | Path) -> BackgroundImage:
    """把用户选中的图复制进 ``data/backgrounds/``，返回入库结果。

    校验顺序（越便宜越靠前）：存在性 → 体积 → 内容是不是图片 → 尺寸上限。
    出错抛 :class:`BackgroundError`，文案直接给用户看。
    """
    src = Path(str(source or "").strip().strip('"'))
    if not src:
        raise BackgroundError("没有选择文件")
    try:
        if not src.is_file():
            raise BackgroundError("选择的文件不存在或不是普通文件")
        size = src.stat().st_size
    except OSError as e:
        raise BackgroundError(f"读取文件失败：{e}") from e
    if size <= 0:
        raise BackgroundError("文件是空的")
    if size > MAX_BYTES:
        raise BackgroundError(f"图片太大（{size / 1024 / 1024:.1f} MB），上限 {MAX_BYTES // 1024 // 1024} MB")

    # 先按内容认格式：扩展名可以骗人，Qt 认出来的才算数
    fmt, width, height = _probe(src)
    suffix = FORMAT_SUFFIX.get(fmt) or src.suffix.lower()
    if fmt and not suffix:
        raise BackgroundError(f"不支持这种图片格式：{fmt}")

    target_dir = paths.backgrounds_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise BackgroundError(f"背景图目录不可写：{e}") from e

    digest = hashlib.sha1(src.read_bytes()).hexdigest()[:16]
    name = f"{digest}{suffix}"
    target = target_dir / name

    if not target.exists():
        # 先写临时文件再改名：中途失败不会留下半张图让界面读到
        tmp = target.with_suffix(target.suffix + ".part")
        try:
            shutil.copyfile(src, tmp)
            os.replace(tmp, target)
        except OSError as e:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise BackgroundError(f"复制图片失败：{e}") from e
    logger.info("背景图已入库：%s（%d×%d，%d 字节）", name, width, height, size)
    return BackgroundImage(name=name, path=target, width=width, height=height, size=size)


def remove(name: str) -> None:
    """删掉入库的图（清除背景 / 换图后清理旧文件用）；没有这个文件也不报错。"""
    name = str(name or "").strip()
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return
    try:
        (paths.backgrounds_dir() / name).unlink(missing_ok=True)
    except OSError as e:
        logger.debug("删除背景图失败 %s: %s", name, e)


def prune(keep: str = "") -> int:
    """清掉 ``backgrounds/`` 里除 ``keep`` 之外的文件，返回删掉的个数。

    换图、清除背景之后调用：库里只该留当前在用的那一张，否则用户每换一次图
    就多堆一份几十 MB 的副本。
    """
    keep = str(keep or "").strip()
    removed = 0
    try:
        for item in paths.backgrounds_dir().iterdir():
            if not item.is_file() or item.name == keep:
                continue
            try:
                item.unlink()
                removed += 1
            except OSError as e:
                logger.debug("清理旧背景图失败 %s: %s", item, e)
    except OSError:
        return removed
    if removed:
        logger.info("已清理 %d 张不再使用的背景图", removed)
    return removed
