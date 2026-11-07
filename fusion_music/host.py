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
from .core.account import AccountManager
from .core.loudness_service import LoudnessService
from .core.player import PlayerEngine
from .core.store import Library
from .bridges.discover import DiscoverController
from .bridges.library import LibraryController
from .bridges.search import SearchController
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
        # 3.17 起音质不再手动选：固定 auto，逐级探测最高可用档位
        # （flac24bit → flac → 320k → 128k）。迁移可能带回旧的 320k/128k，
        # 所以必须放在 apply_legacy_settings 之后纠正。
        if str(self.config.get("playback.quality", "")) != "auto":
            self.config.set("playback.quality", "auto")

        self.library = Library()
        self.library.load()
        self.loudness: Optional[LoudnessService] = None
        try:
            self.loudness = LoudnessService(self.config)
        except Exception as exc:  # noqa: BLE001 - 没有解码器时退化为不均衡
            logger.warning("音量均衡初始化失败，按 0 dB 播放: %s", exc)
        self.player = PlayerEngine(self.config, self.library, self.loudness)
        self.library_controller = LibraryController(self.config, self.library)
        self.search = SearchController(self.config)
        self.discover = DiscoverController(self.config)
        # 网易云账号：3.18 起只负责“有凭据就恢复登录态 + 把远程歌单推给曲库”，
        # 扫码 / VIP / 续期界面归 3.19；这里先建好，避免 K-41 的远程歌单是死代码。
        self.account = AccountManager(self.config)
        self.account.playlistsChanged.connect(self._sync_remote_playlists)
        self.account.loggedInChanged.connect(self._sync_remote_playlists)
        try:
            self.player.restoreSession()
        except Exception as exc:  # noqa: BLE001 - 会话损坏只影响恢复
            logger.warning("恢复播放会话失败: %s", exc)
        try:
            self.account.restore()
        except Exception as exc:  # noqa: BLE001 - 凭据损坏只影响远程歌单
            logger.warning("恢复网易云登录态失败: %s", exc)
        logger.info(
            "音乐后端就绪：%s（歌单 %d / 本地 %d）",
            self.music_root,
            len(self.library.playlists()),
            len(self.library.local_tracks()),
        )

    def _sync_remote_playlists(self) -> None:
        if not hasattr(self, "library_controller"):
            return
        self.library_controller.set_remote_state(
            list(self.account.remotePlaylists),
            bool(self.account.playlistsFailed),
        )

    def scan(self) -> None:
        self.library_controller.scan()

    def shutdown(self) -> None:
        for controller in (self.search, self.discover, self.library_controller):
            fn = getattr(controller, "shutdown", None)
            if callable(fn):
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001 - 退出路径不再抛
                    logger.warning("关闭音乐控制器失败: %s", exc)
        try:
            self.account.cancelQrLogin()
        except Exception:
            pass
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
