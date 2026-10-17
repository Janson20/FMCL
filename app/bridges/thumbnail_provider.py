"""缩略图图片提供者（阶段 3 任务 3.6）—— 资源包 / 光影的预览图通道。

## 为什么需要它

服务层的 ``resource_service.load_thumbnail(zip, max_size)`` 返回的是**一张图的
base64**（从 zip 里解出来的 ``pack.png`` 之类）。QML 的 ``Image.source`` 只认
URL，而 **Qt Quick 的 ``Image`` 不认 data URI**（``data:image/png;base64,…`` 会
直接落到 ``status === Error``，本次任务的实测记录在 ``16`` §二十二）。三条出路：

1. 写临时文件再 ``file://`` —— 会在用户的临时目录里堆一堆文件，退出还得清；
2. 自己写一个 ``QQuickImageProvider`` —— 内存里递 ``QImage``，与
   ``app/bridges/icon_provider.py``（任务 2.16 图标上色）**同一套做法**；
3. 让 QML 侧用 ``Image`` 之外的控件画 —— 没有这种控件。

选 **2**。它与图标提供者共享同一批实测结论（PySide6 6.7.3 上
``QQuickImageProvider`` 在 ``PySide6.QtQuick`` 里、``requestImage`` 的 id 不做
百分号解码、``size`` 出参写不回去），所以那份踩坑记录不必重来一遍。

## 用法

装配期（``main_qml.py``，**在 ``engine.load()`` 之前**）::

    from app.bridges.resources_bridge import install as install_resources
    install_resources(engine)

之后 ``ResourcesBridge`` 自己会在 worker 线程里 ``register(path, base64)``，
拿到 ``image://fmcl-thumb/<id>`` 塞进条目的 ``thumbnail`` 字段，QML 侧:

    Image { source: String(row.thumbnail || "") ; sourceSize.width: 32 }

## 线程前提（与图标提供者相同的理由）

``Image`` 开了 ``asynchronous`` 时 Qt 会在**图片加载线程**里调 ``requestImage``，
而 ``register`` 是在**任务的 worker 线程**里调的 —— 两者都要碰缓存，所以全程加锁。

## 缓存策略

键是**自增 id**（不是路径）：同一条路径重新注册会拿到新 id，``Image.source``
因此必然变化，Qt 就不会拿旧图（路径做键的话，文件更新后界面不刷新）。旧 id
由 LRU 淘汰（``CACHE_LIMIT`` 条），淘汰后那张图变成空图 —— 界面只是回到没有
预览图的样子，不会崩。
"""

from __future__ import annotations

import base64
import logging
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QImage
from PySide6.QtQml import QQmlImageProviderBase
from PySide6.QtQuick import QQuickImageProvider

logger = logging.getLogger("app.bridges.thumbnail_provider")

#: QML 里的图片提供者 id 与 URL 前缀：`image://fmcl-thumb/<id>`。
THUMB_PROVIDER_ID = "fmcl-thumb"
THUMB_URL_PREFIX = f"image://{THUMB_PROVIDER_ID}/"

#: 缓存条数上限。资源包/光影一页 10 条，翻几页也不会把这数量撑爆；
#: 超过就淘汰最旧的（那张图在界面上已经滚走了）。
CACHE_LIMIT = 256


def decode_thumbnail(base64_data: str) -> QImage:
    """base64 → ``QImage``（解不出来返回空图，调用方据此走兜底图标）。"""
    text = str(base64_data or "").strip()
    if not text:
        return QImage()
    try:
        raw = base64.b64decode(text, validate=False)
    except Exception as e:  # noqa: BLE001 - 脏数据不该把任务线程打挂
        logger.warning("缩略图 base64 解码失败: %s", e)
        return QImage()
    image = QImage()
    try:
        image.loadFromData(raw)
    except Exception as e:  # noqa: BLE001
        logger.warning("缩略图数据无法解析为图片: %s", e)
        return QImage()
    return image


class ThumbnailImageProvider(QQuickImageProvider):
    """``image://fmcl-thumb/<id>`` 的提供者（内存缓存 + LRU 淘汰）。"""

    def __init__(self) -> None:
        super().__init__(QQmlImageProviderBase.ImageType.Image)
        self._images: "OrderedDict[str, QImage]" = OrderedDict()
        self._keys: Dict[str, str] = {}  # 去重键 -> 当前 id
        self._lock = threading.Lock()
        self._seq = 0
        self.requests = 0
        self.misses = 0

    # ─── 注册（worker 线程调）───────────────────────────────

    def register(self, key: str, base64_data: str) -> Optional[str]:
        """把一张 base64 图登记进来，返回可直接塞进 ``Image.source`` 的 URL。

        同一个 ``key``（条目路径）重复注册会**复用同一个 id** —— 否则列表每次
        重刷都会让 Qt 重新解码一遍同样的图。图片内容变了但 key 没变时，注册会
        用新图覆盖旧图（``_images`` 里是同一个 id）。
        """
        image = decode_thumbnail(base64_data)
        if image.isNull():
            return None
        name = str(key or "")
        with self._lock:
            existing = self._keys.get(name)
            if existing is not None and existing in self._images:
                self._images[existing] = image
                self._images.move_to_end(existing)
                return f"{THUMB_URL_PREFIX}{existing}"
            self._seq += 1
            image_id = f"{self._seq}"
            self._images[image_id] = image
            if name:
                self._keys[name] = image_id
            while len(self._images) > CACHE_LIMIT:
                old_id, _ = self._images.popitem(last=False)
                for k, v in list(self._keys.items()):
                    if v == old_id:
                        self._keys.pop(k, None)
        return f"{THUMB_URL_PREFIX}{image_id}"

    # ─── QQuickImageProvider 接口（图片加载线程调）─────────

    def requestImage(self, image_id: str, size: Any, requestedSize: Any) -> QImage:  # noqa: N802, N803
        """Qt 的取图入口。返回**空 QImage** = 没有这张图（界面走兜底图标）。"""
        self.requests += 1
        key = str(image_id or "").strip()
        with self._lock:
            image = self._images.get(key)
            if image is not None:
                self._images.move_to_end(key)
        if image is None:
            self.misses += 1
            return QImage()
        width, height = requested_size(requestedSize, size)
        if width <= 0 or height <= 0:
            return image
        if width == image.width() and height == image.height():
            return image
        # KeepAspectRatio：预览图不是正方形，硬拉伸会变形（界面给的是方框）
        return image.scaled(
            QSize(width, height),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )

    # ─── 诊断（测试也读）────────────────────────────────────

    @property
    def cacheSize(self) -> int:  # noqa: N802 - 与 QML 属性风格一致，便于日志
        with self._lock:
            return len(self._images)

    def clear_cache(self) -> None:
        with self._lock:
            self._images.clear()
            self._keys.clear()


def requested_size(requested: Any, size: Any = None) -> Tuple[int, int]:
    """算目标边长：优先 ``requestedSize``（QML 的 ``sourceSize``），否则 ``size``。

    两种都拿不到时返回 ``(0, 0)``，表示"按原图返回"。
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
    return 0, 0


def install(engine: Any) -> ThumbnailImageProvider:
    """把提供者注册到 QML 引擎（**必须在 ``engine.load()`` 之前**调用）。

    与图标提供者同一个约定：``addImageProvider`` 不接管所有权，所以在引擎上留一份
    强引用 —— 被 GC 掉之后 QML 取图会拿到空图，而且没有任何有用的报错。
    """
    provider = ThumbnailImageProvider()
    engine.addImageProvider(THUMB_PROVIDER_ID, provider)
    stores = getattr(engine, "_fmcl_thumb_providers", None)
    if stores is None:
        stores = {}
        engine._fmcl_thumb_providers = stores
    stores[THUMB_PROVIDER_ID] = provider
    logger.info("缩略图提供者已注册：%s", THUMB_URL_PREFIX)
    return provider


def installed(engine: Any) -> Optional[ThumbnailImageProvider]:
    """取引擎上已注册的提供者（诊断/测试用；没注册返回 None）。"""
    return getattr(engine, "_fmcl_thumb_providers", {}).get(THUMB_PROVIDER_ID)


__all__ = [
    "CACHE_LIMIT",
    "THUMB_PROVIDER_ID",
    "THUMB_URL_PREFIX",
    "ThumbnailImageProvider",
    "decode_thumbnail",
    "install",
    "installed",
    "requested_size",
]
