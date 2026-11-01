"""播放器宿主：把 FusionMusicPlayer 的后端装进 FMCL 的一个桥。

只负责生命周期与路径隔离；页面逻辑在 QML，曲库/播放规则在 core。
数据根固定为 ``<base>/data/music/``，不碰 FMCL 的 ``config.json`` 与旧
``data/music.json``（迁移只读，见 migration.py）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from . import paths
from .config import Config
from .core.loudness_service import LoudnessService
from .core.player import PlayerEngine
from .core.store import Library
from .bridges.library import LibraryController
from .migration import apply_legacy_settings, migrate

logger = logging.getLogger("fusion_music.host")


class MusicHost:
    """一个进程内唯一的音乐后端（初次进入音乐页时创建）。"""

    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self.music_root = self.base_dir / "data" / "music"
        self.migration: Dict[str, Any] = migrate(self.base_dir, self.music_root)
        paths.set_data_dir(self.music_root)
        paths.ensure_dirs()

        self.config = Config()
        if self.migration.get("migrated"):
            if apply_legacy_settings(self.config, self.migration.get("settings") or {}):
                self.config.save()

        self.library = Library()
        self.library.load()
        self.loudness: Optional[LoudnessService] = None
        try:
            self.loudness = LoudnessService(self.config)
        except Exception as exc:  # noqa: BLE001 - 没有解码器时退化为不均衡
            logger.warning("音量均衡初始化失败，按 0 dB 播放: %s", exc)
        self.player = PlayerEngine(self.config, self.library, self.loudness)
        self.library_controller = LibraryController(self.config, self.library)
        try:
            self.player.restoreSession()
        except Exception as exc:  # noqa: BLE001 - 会话损坏只影响恢复
            logger.warning("恢复播放会话失败: %s", exc)
        logger.info(
            "音乐后端就绪：%s（歌单 %d / 本地 %d）",
            self.music_root,
            len(self.library.playlists()),
            len(self.library.local_tracks()),
        )

    def scan(self) -> None:
        self.library_controller.scan()

    def shutdown(self) -> None:
        for name, obj in (
            ("player", self.player),
            ("loudness", self.loudness),
        ):
            if obj is None:
                continue
            fn = getattr(obj, "shutdown", None)
            if callable(fn):
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001 - 退出路径不再抛
                    logger.warning("关闭音乐%s失败: %s", name, exc)
        for name, obj in (("library", self.library), ("config", self.config)):
            fn = getattr(obj, "save", None)
            if callable(fn):
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("保存音乐%s失败: %s", name, exc)
