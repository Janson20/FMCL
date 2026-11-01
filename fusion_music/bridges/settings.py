"""设置控制器：读写配置、缓存管理、程序目录信息。"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import List

from PySide6.QtCore import QObject, Property, Signal, Slot

from .. import paths, shellopen
from ..config import DEFAULTS
from ..core import backgrounds, cache, downloader
from ..units import human_size

logger = logging.getLogger(__name__)

THEME_OPTIONS = [
    {"id": "auto", "name": "跟随系统"},
    {"id": "light", "name": "浅色"},
    {"id": "dark", "name": "深色"},
]

ACCENT_PRESETS = [
    {"id": "#6C4DF6", "name": "星云紫"},
    {"id": "#3B82F6", "name": "冰川蓝"},
    {"id": "#0EA5A4", "name": "青竹"},
    {"id": "#E5484D", "name": "朱砂"},
    {"id": "#F59E0B", "name": "琥珀"},
    {"id": "#EC4899", "name": "绯樱"},
]

LYRIC_ALIGN_OPTIONS = [
    {"id": "left", "name": "靠左"},
    {"id": "center", "name": "居中"},
    {"id": "right", "name": "靠右"},
]

# 歌词对齐的合法取值，非法值一律回落到居中
LYRIC_ALIGNMENTS = ("left", "center", "right")

#: 音量均衡的目标响度档位。数值越小整体越响（越接近「响度战争」的口径）
TARGET_LUFS_OPTIONS = [
    {"id": -18.0, "name": "-18 LUFS（ReplayGain 2.0）"},
    {"id": -16.0, "name": "-16 LUFS（留更多动态）"},
    {"id": -14.0, "name": "-14 LUFS（流媒体标准）"},
    {"id": -12.0, "name": "-12 LUFS（偏响）"},
    {"id": -10.0, "name": "-10 LUFS（很响）"},
]

class _CacheEmitter(QObject):
    """把缓存整理的耗时结果投递回主线程（Qt 会自动排队到接收者线程）。"""

    deduped = Signal(int, int)   # (删除文件数, 释放字节数)；-1 表示失败


class SettingsController(QObject):
    changed = Signal()
    cacheChanged = Signal()
    loudnessChanged = Signal()
    message = Signal(str)
    errorOccurred = Signal(str)

    def __init__(self, config, loudness=None, parent=None):
        super().__init__(parent)
        self._config = config
        self._loudness = loudness
        # 缓存去重放在工作线程里做（要读几 GB 的盘），结果用信号投回主线程
        self._cache_emitter = _CacheEmitter(self)
        self._cache_emitter.deduped.connect(self._on_deduped)
        self._dedupe_busy = False
        # backgroundImage 会被 QML 反复求值（每个窗口的背景层都绑它），
        # 而解析要做文件校验，所以按文件名缓存一次
        self._background_cache_key: str | None = None
        self._background_cache = None

    # ── 只读信息 ────────────────────────────────────────────

    @Property(str, constant=True)
    def dataDir(self) -> str:  # noqa: N802
        return str(paths.data_dir())

    @Property(str, constant=True)
    def programDir(self) -> str:  # noqa: N802
        return str(paths.program_dir())

    @Property(str, constant=True)
    def credentialPath(self) -> str:  # noqa: N802
        return str(paths.credentials_file())

    @Property(str, constant=True)
    def cacheDir(self) -> str:  # noqa: N802
        return str(paths.cache_dir())

    @Property(str, notify=changed)
    def downloadDir(self) -> str:  # noqa: N802
        return downloader.default_directory(self._config)

    @Property("QVariantList", constant=True)
    def qualityOptions(self):  # noqa: N802
        """音质选项（与下载对话框同一份文案，见 :func:`downloader.quality_choices`）。"""
        return downloader.quality_choices()

    @Property("QVariantList", constant=True)
    def themeOptions(self):  # noqa: N802
        return THEME_OPTIONS

    @Property("QVariantList", constant=True)
    def accentPresets(self):  # noqa: N802
        return ACCENT_PRESETS

    # ── 可写设置 ────────────────────────────────────────────

    @Property(str, notify=changed)
    def theme(self) -> str:
        return str(self._config.get("appearance.theme", "auto"))

    @Property(str, notify=changed)
    def accent(self) -> str:
        return str(self._config.get("appearance.accent", "#6C4DF6"))

    @Property(bool, notify=changed)
    def animations(self) -> bool:
        return bool(self._config.get("appearance.animations", True))

    @Property(bool, notify=changed)
    def navExpanded(self) -> bool:  # noqa: N802
        return bool(self._config.get("appearance.nav_expanded", True))

    @Property(str, notify=changed)
    def quality(self) -> str:
        return str(self._config.get("playback.quality", "320k"))

    @Property(bool, notify=changed)
    def fallback(self) -> bool:
        return bool(self._config.get("sources.fallback", True))

    @Property(bool, notify=changed)
    def restoreSession(self) -> bool:  # noqa: N802
        """启动时是否把上次的播放队列与在播曲目恢复出来。"""
        return bool(self._config.get("playback.restore_session", True))

    @Property(bool, notify=changed)
    def preferOriginal(self) -> bool:  # noqa: N802
        return bool(self._config.get("sources.prefer_original", True))

    @Property(bool, notify=changed)
    def cacheCover(self) -> bool:  # noqa: N802
        return bool(self._config.get("storage.cache_cover", True))

    @Property(bool, notify=changed)
    def cacheLyric(self) -> bool:  # noqa: N802
        return bool(self._config.get("storage.cache_lyric", True))

    @Property(bool, notify=changed)
    def cacheMedia(self) -> bool:  # noqa: N802
        return bool(self._config.get("storage.cache_media", False))

    @Property(int, notify=changed)
    def mediaCacheLimit(self) -> int:  # noqa: N802
        return int(self._config.get("storage.media_cache_limit_mb", 2048) or 2048)

    @Property(bool, notify=changed)
    def showTranslation(self) -> bool:  # noqa: N802
        return bool(self._config.get("lyrics.show_translation", True))

    @Property(bool, notify=changed)
    def showRomaji(self) -> bool:  # noqa: N802
        return bool(self._config.get("lyrics.show_romaji", False))

    @Property(bool, notify=changed)
    def lyricDynamic(self) -> bool:  # noqa: N802
        """逐字（动态）歌词：有 yrc 逐字数据就用真的，没有就按行时间摊伪动态。"""
        return bool(self._config.get("lyrics.dynamic", True))

    @Property(int, notify=changed)
    def lyricFontSize(self) -> int:  # noqa: N802
        return int(self._config.get("lyrics.font_size", 17) or 17)

    @Property(str, notify=changed)
    def lyricAlignment(self) -> str:  # noqa: N802
        value = str(self._config.get("lyrics.alignment", "center") or "").strip().lower()
        return value if value in LYRIC_ALIGNMENTS else "center"

    @Property("QVariantList", constant=True)
    def lyricAlignOptions(self):  # noqa: N802
        return LYRIC_ALIGN_OPTIONS

    @Property(bool, notify=changed)
    def desktopLyric(self) -> bool:  # noqa: N802
        return bool(self._config.get("lyrics.desktop_lyric", False))

    @Property(bool, notify=changed)
    def autoLogin(self) -> bool:  # noqa: N802
        return bool(self._config.get("account.auto_login", True))

    @Property(bool, notify=changed)
    def saveCredentials(self) -> bool:  # noqa: N802
        return bool(self._config.get("account.save_credentials", True))

    @Property(int, notify=changed)
    def cookieRefreshDays(self) -> int:  # noqa: N802
        """凭据剩余有效期少于这么多天时自动续期（0 = 关闭）。"""
        try:
            return int(self._config.get("account.cookie_refresh_days", 7) or 0)
        except (TypeError, ValueError):
            return 7

    @Property(bool, notify=changed)
    def scanOnStart(self) -> bool:  # noqa: N802
        return bool(self._config.get("local.scan_on_start", False))

    @Property(bool, notify=changed)
    def matchLocalOnline(self) -> bool:  # noqa: N802
        """扫描本地曲库时，是否按「歌名 + 歌手」在线匹配封面与歌词。"""
        return bool(self._config.get("local.match_online", True))

    # ── 歌曲下载 ────────────────────────────────────────────

    @Property(str, notify=changed)
    def defaultQuality(self) -> str:  # noqa: N802
        """下载默认音质（auto = 按可用最高档）。"""
        from ..core import downloader

        value = str(self._config.get("download.quality", "320k") or "320k")
        if value == "auto":
            return value
        return value if value in downloader.QUALITY_LABELS else "320k"

    @Property(str, notify=changed)
    def filenameTemplate(self) -> str:  # noqa: N802
        return str(self._config.get("download.filename_template", "{singer} - {name}")
                   or "{singer} - {name}")

    @Property(bool, notify=changed)
    def downloadWriteTags(self) -> bool:  # noqa: N802
        return bool(self._config.get("download.write_tags", True))

    @Property(bool, notify=changed)
    def downloadEmbedCover(self) -> bool:  # noqa: N802
        return bool(self._config.get("download.embed_cover", True))

    @Property(bool, notify=changed)
    def downloadSaveLyric(self) -> bool:  # noqa: N802
        return bool(self._config.get("download.save_lyric", True))

    @Property(bool, notify=changed)
    def downloadAddToLibrary(self) -> bool:  # noqa: N802
        return bool(self._config.get("download.add_to_library", False))

    @Property(str, notify=changed)
    def downloadDuplicate(self) -> str:  # noqa: N802
        from ..core import naming

        value = str(self._config.get("download.duplicate", "rename") or "rename")
        return value if value in naming.DUPLICATE_MODES else "rename"

    @Property(int, notify=changed)
    def downloadConcurrency(self) -> int:  # noqa: N802
        from ..core.downloader import DownloadManager

        return DownloadManager.clamp_concurrency(self._config.get("download.concurrency", 2))

    @Property("QVariantList", constant=True)
    def downloadDuplicateOptions(self):  # noqa: N802
        """同名文件处理的选项（与下载对话框同一份文案）。"""
        return downloader.duplicate_choices()

    @Slot(str)
    def setDownloadDir(self, url: str) -> None:  # noqa: N802
        """设置下载目录（``file://`` URL 或普通路径）。目录建不出来时明确报错。"""
        path = paths.normalize_folder(url)
        if not path:
            return
        try:
            Path(path).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            self.errorOccurred.emit(f"这个目录用不了：{e}")
            return
        if not Path(path).is_dir():
            self.errorOccurred.emit("选择的不是一个文件夹")
            return
        self._config.set("storage.download_dir", path)
        self.changed.emit()
        self.message.emit("下载目录已更新")

    @Slot()
    def resetDownloadDir(self) -> None:  # noqa: N802
        """恢复默认下载目录（程序目录下的 downloads）。"""
        self._config.set("storage.download_dir", "")
        self.changed.emit()
        self.message.emit("下载目录已恢复默认")

    @Property("QVariantList", notify=changed)
    def enabledSources(self):  # noqa: N802
        from ..sources import SOURCE_META

        enabled = set(self._config.get("sources.enabled", []) or [])
        return [
            {"id": m["id"], "name": m["name"], "enabled": m["id"] in enabled}
            for m in SOURCE_META
        ]

    # ── 背景图 ──────────────────────────────────────────────
    #
    # 图片本体存在 data/backgrounds/ 里（见 core/backgrounds.py），配置里只有文件名。
    # 这里负责：入库/替换/清除、把文件名解析成 QML 能用的 URL、以及给界面显示的状态文案。

    @Property(bool, notify=changed)
    def backgroundActive(self) -> bool:  # noqa: N802
        """背景图是否真的生效（模式选的是图，**而且**文件确实能读到）。

        文件被删掉时这里会变回 False：界面自动退回纯色底，而不是对着一张
        不存在的图干等。
        """
        if str(self._config.get("appearance.background", "solid")) != "image":
            return False
        return self._background() is not None

    @Property(str, notify=changed)
    def backgroundImage(self) -> str:  # noqa: N802
        """背景图的 file:// URL（不生效时是空串，QML 直接绑给 Image.source）。"""
        image = self._background()
        return image.url if image else ""

    @Property(int, notify=changed)
    def backgroundImageWidth(self) -> int:  # noqa: N802
        """原图像素宽（0 = 没图）。

        QML 侧拿它算 ``sourceSize``：按同一个比例缩到长边 2560 以内再解码，
        既不会把 4K 原图整张塞进显存，也不会因为只给一边而改变宽高比。
        """
        image = self._background()
        return image.width if image else 0

    @Property(int, notify=changed)
    def backgroundImageHeight(self) -> int:  # noqa: N802
        image = self._background()
        return image.height if image else 0

    def _percent(self, key: str, default: int) -> int:
        """0–100 的百分比设置：脏数据夹回范围，不抛异常。"""
        try:
            value = int(self._config.get(key, default))
        except (TypeError, ValueError):
            return default
        return max(0, min(100, value))

    @Property(int, notify=changed)
    def backgroundOpacity(self) -> int:  # noqa: N802
        """背景图自身的不透明度（0-100）：调小就让底下的主题色透出来。"""
        return self._percent("appearance.background_opacity", 65)

    @Property(int, notify=changed)
    def backgroundBlur(self) -> int:  # noqa: N802
        """磨砂感（0-100）：模糊半径，0 = 保持原图清晰。"""
        return self._percent("appearance.background_blur", 25)

    @Property(int, notify=changed)
    def backgroundScrim(self) -> int:  # noqa: N802
        """蒙版浓度（0-100）：暗色主题压黑、浅色主题提白，保证文字读得清。"""
        return self._percent("appearance.background_scrim", 35)

    @Property(int, notify=changed)
    def surfaceSidebar(self) -> int:  # noqa: N802
        """左侧导航栏的不透明度（0-100）。"""
        return self._percent("appearance.surface_sidebar", 68)

    @Property(int, notify=changed)
    def surfaceBottom(self) -> int:  # noqa: N802
        """底部播放栏的不透明度（0-100）。"""
        return self._percent("appearance.surface_bottom", 74)

    @Property(int, notify=changed)
    def surfaceOverlay(self) -> int:  # noqa: N802
        """覆盖层页面的不透明度（0-100）：歌词页（展开播放）、歌单 / 专辑 / 歌手详情。"""
        return self._percent("appearance.surface_overlay", 62)

    @Property(int, notify=changed)
    def surfaceCard(self) -> int:  # noqa: N802
        """内容区里卡片与列表底的不透明度（0-100）。"""
        return self._percent("appearance.surface_card", 80)

    @Property(str, notify=changed)
    def backgroundImageInfo(self) -> str:  # noqa: N802
        """设置页显示的一行状态：尺寸 · 体积 · 文件名（缺失时说明原因）。"""
        name = str(self._config.get("appearance.background_image", "") or "")
        if not name:
            return ""
        image = self._background()
        if image is None:
            return f"图片已丢失：{name}（已退回纯色背景，重新选一张即可）"
        return image.info_text

    def _background(self):
        """当前配置指向的背景图（带缓存：这是 QML 每次求值都会读的属性）。

        缓存的是"文件名 → 解析结果"。但文件可能被程序外面删掉或替换，所以**命中
        缓存时也要核对一次**：``stat`` 很便宜，而重新解析要过一遍 Qt 的格式探测，
        不能每帧都做。对不上就作废重来 —— 否则删掉文件后界面还会一直指着一张
        不存在的图。
        """
        name = str(self._config.get("appearance.background_image", "") or "")
        if name == self._background_cache_key:
            cached = self._background_cache
            if cached is None:
                return None
            try:
                if cached.path.is_file() and cached.path.stat().st_size == cached.size:
                    return cached
            except OSError:
                pass
        self._background_cache_key = name
        self._background_cache = backgrounds.resolve(name) if name else None
        return self._background_cache

    def _forget_background(self) -> None:
        self._background_cache_key = None
        self._background_cache = None

    @Slot(str)
    def applyBackgroundImage(self, url: str) -> None:  # noqa: N802
        """把用户选中的图片入库并启用。

        入参是 QML ``FileDialog`` 给的 URL（``file:///D:/...``）。入库失败只报错，
        不动原有配置 —— 用户选错一个文件不该把现有背景弄没。
        """
        path = paths.from_file_url(url)
        if not path:
            self.errorOccurred.emit("没有选择图片文件")
            return
        old = str(self._config.get("appearance.background_image", "") or "")
        try:
            image = backgrounds.store(path)
        except backgrounds.BackgroundError as e:
            self.errorOccurred.emit(f"背景图设置失败：{e}")
            return
        except Exception as e:  # pragma: no cover - 兜底，不让选图把窗口带崩
            logger.exception("背景图入库异常")
            self.errorOccurred.emit(f"背景图设置失败：{e}")
            return

        self._config.set("appearance.background_image", image.name)
        self._config.set("appearance.background", "image")
        self._forget_background()
        if old and old != image.name:
            backgrounds.remove(old)
        backgrounds.prune(keep=image.name)
        self.changed.emit()
        self.message.emit(f"背景图已更新：{image.width}×{image.height}")

    @Slot()
    def clearBackgroundImage(self) -> None:  # noqa: N802
        """恢复到主题纯色底，并删掉入库的图（不留副本）。"""
        old = str(self._config.get("appearance.background_image", "") or "")
        self._config.set("appearance.background_image", "")
        self._config.set("appearance.background", "solid")
        self._forget_background()
        if old:
            backgrounds.remove(old)
        backgrounds.prune()
        self.changed.emit()
        self.message.emit("已恢复为纯色背景")

    @Property("QVariant", notify=changed)
    def allSettings(self):  # noqa: N802
        return self._config.as_dict()

    # ── 写入 ────────────────────────────────────────────────

    @Slot(str, "QVariant")
    def set(self, key: str, value) -> None:  # noqa: A003
        self._config.set(str(key), value)
        self.changed.emit()

    @Slot(str, bool)
    def setBool(self, key: str, value: bool) -> None:  # noqa: N802
        self._config.set(str(key), bool(value))
        self.changed.emit()

    @Slot(str, int)
    def setInt(self, key: str, value: int) -> None:  # noqa: N802
        self._config.set(str(key), int(value))
        self.changed.emit()

    @Slot(str, str, bool)
    def setSourceEnabled(self, source_id: str, enabled: bool) -> None:  # noqa: N802
        enabled_list: List[str] = list(self._config.get("sources.enabled", []) or [])
        if enabled and source_id not in enabled_list:
            enabled_list.append(source_id)
        elif not enabled and source_id in enabled_list:
            enabled_list.remove(source_id)
        if not enabled_list:
            self.errorOccurred.emit("至少需要启用一个音源")
            return
        self._config.set("sources.enabled", enabled_list)
        self.changed.emit()
        self.message.emit("音源设置已更新")

    @Slot(str, result=bool)
    def moveSourceUp(self, source_id: str) -> bool:  # noqa: N802
        order: List[str] = list(self._config.get("sources.priority", []) or [])
        if source_id not in order or order.index(source_id) == 0:
            return False
        i = order.index(source_id)
        order[i - 1], order[i] = order[i], order[i - 1]
        self._config.set("sources.priority", order)
        self.changed.emit()
        return True

    # ── 缓存 ────────────────────────────────────────────────

    @Property("QVariant", notify=cacheChanged)
    def cacheStats(self):  # noqa: N802
        try:
            raw = cache.cache_stats()
        except Exception:
            raw = {"cover": 0, "lyric": 0, "media": 0}
        total = sum(raw.values())
        return {
            "cover": human_size(raw.get("cover", 0)),
            "lyric": human_size(raw.get("lyric", 0)),
            "media": human_size(raw.get("media", 0)),
            "total": human_size(total),
            "totalBytes": total,
        }

    @Slot()
    def refreshCache(self) -> None:  # noqa: N802
        self.cacheChanged.emit()

    @Slot()
    def clearCache(self) -> None:  # noqa: N802
        try:
            cache.clear_cache()
            self.message.emit("缓存已清空")
        except Exception as e:
            logger.warning("清空缓存失败: %s", e)
            self.errorOccurred.emit("清空缓存失败")
        self.cacheChanged.emit()

    @Slot(str)
    def clearCachePart(self, part: str) -> None:  # noqa: N802
        try:
            cache.clear_cache(str(part))
            self.message.emit("已清理缓存")
        except Exception as e:
            logger.warning("清理缓存失败: %s", e)
        self.cacheChanged.emit()

    @Slot()
    def trimCache(self) -> None:  # noqa: N802
        cache.trim_cache(self.mediaCacheLimit)
        self.refreshCache()
        self.message.emit("已按配额清理缓存")

    @Slot()
    def dedupeCache(self) -> None:  # noqa: N802
        """清理媒体缓存里内容重复的文件（老版本按播放地址当键留下的旧账）。

        可能要读几 GB 的盘，所以丢进工作线程；重复点击只认第一次。
        """
        if self._dedupe_busy:
            return
        self._dedupe_busy = True
        self.message.emit("正在比对重复的缓存文件…")
        threading.Thread(
            target=self._dedupe_worker, daemon=True, name="dedupe-cache"
        ).start()

    def _dedupe_worker(self) -> None:
        """（工作线程）只做比对与删除，结果用信号投回主线程。"""
        try:
            removed, freed = cache.dedupe_media()
        except Exception:  # pragma: no cover - 兜底，别把线程异常吞掉
            logger.exception("清理重复缓存异常")
            removed, freed = -1, 0
        self._cache_emitter.deduped.emit(int(removed), int(freed))

    @Slot(int, int)
    def _on_deduped(self, removed: int, freed: int) -> None:
        self._dedupe_busy = False
        self.cacheChanged.emit()
        if removed < 0:
            self.errorOccurred.emit("清理重复缓存失败")
        elif removed == 0:
            self.message.emit("没有发现内容重复的缓存文件")
        else:
            self.message.emit(f"已删除 {removed} 个重复文件，释放 {human_size(freed)}")

    # ── 音量均衡 ────────────────────────────────────────────

    @Property(bool, notify=changed)
    def equalize(self) -> bool:
        return bool(self._config.get("audio.equalize", True))

    @Property(float, notify=changed)
    def targetLufs(self) -> float:  # noqa: N802
        try:
            return float(self._config.get("audio.target_lufs", -14.0))
        except (TypeError, ValueError):
            return -14.0

    @Property(bool, notify=changed)
    def allowBoost(self) -> bool:  # noqa: N802
        return bool(self._config.get("audio.allow_boost", True))

    @Property(bool, notify=changed)
    def prefetchLoudness(self) -> bool:  # noqa: N802
        return bool(self._config.get("audio.prefetch_next", True))

    @Property("QVariantList", constant=True)
    def targetLufsOptions(self):  # noqa: N802
        return [dict(item) for item in TARGET_LUFS_OPTIONS]

    @Property("QVariant", notify=loudnessChanged)
    def loudnessStats(self):  # noqa: N802
        """响度分析的进度与缓存占用（设置页显示用）。"""
        fallback = {"measured": 0, "failed": 0, "pending": 0, "total": 0, "size": "0 B",
                    "avgLoudness": "", "enabled": self.equalize}
        if self._loudness is None:
            return fallback
        try:
            raw = self._loudness.stats()
        except Exception:
            return fallback
        avg = raw.get("avgLoudness")
        return {
            "measured": int(raw.get("measured", 0) or 0),
            "failed": int(raw.get("failed", 0) or 0),
            "pending": int(raw.get("pending", 0) or 0),
            "total": int(raw.get("total", 0) or 0),
            "size": human_size(int(raw.get("bytes", 0) or 0)),
            "avgLoudness": f"{avg:.1f} LUFS" if isinstance(avg, (int, float)) else "",
            "enabled": bool(raw.get("enabled", self.equalize)),
        }

    @Slot()
    def notifyLoudnessChanged(self) -> None:  # noqa: N802
        """响度缓存变了（分析出一个结果 / 清空），通知界面刷新统计。"""
        self.loudnessChanged.emit()

    @Slot()
    def clearLoudness(self) -> None:  # noqa: N802
        """清空响度缓存（下次播放会重新分析）。"""
        if self._loudness is None:
            return
        try:
            self._loudness.clear()
            self.message.emit("已清空音量均衡的分析结果，下次播放会重新分析")
        except Exception as e:
            logger.warning("清空响度缓存失败: %s", e)
            self.errorOccurred.emit("清空音量均衡数据失败")
        self.loudnessChanged.emit()

    # ── 系统操作 ────────────────────────────────────────────

    @Slot(str)
    def openPath(self, path: str) -> None:  # noqa: N802
        target = path or str(paths.data_dir())
        try:
            shellopen.open_path(target)
        except OSError as e:
            logger.warning("打开路径失败: %s", e)
            self.errorOccurred.emit(f"无法打开路径：{target}")

    @Slot(str)
    def copyToClipboard(self, text: str) -> None:  # noqa: N802
        try:
            from PySide6.QtGui import QGuiApplication

            QGuiApplication.clipboard().setText(str(text or ""))
            self.message.emit("已复制到剪贴板")
        except Exception as e:
            logger.debug("复制失败: %s", e)

    @Slot()
    def resetAll(self) -> None:
        self._config.reset()
        self.changed.emit()
        self.message.emit("设置已恢复默认值")

    @Slot(result="QVariant")
    def defaults(self):  # noqa: N802
        return DEFAULTS
