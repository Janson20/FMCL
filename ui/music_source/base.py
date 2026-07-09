"""音乐源基类 - 所有音源插件必须继承此基类"""

import abc
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

import requests

from ui.music_source.utils import create_session

logger = logging.getLogger("music_source")

# 默认请求超时（秒）
DEFAULT_TIMEOUT = 15
# 最大重试次数
MAX_RETRIES = 3
# 重试间隔基数（秒）
RETRY_BASE_DELAY = 1.0


class QualityLevel:
    """音质等级常量"""

    LOW = "128k"
    MEDIUM = "320k"
    HIGH = "flac"
    LOSSLESS = "flac24bit"

    ALL = ["flac24bit", "flac", "320k", "128k"]

    @classmethod
    def index(cls, quality: str) -> int:
        try:
            return cls.ALL.index(quality)
        except ValueError:
            return 3


@dataclass
class MusicInfo:
    """歌曲信息数据结构"""

    name: str  # 歌曲名
    singer: str  # 歌手名
    source: str  # 来源标识 (kw/kg/mg/tx/wy)
    songmid: str  # 歌曲ID
    album_name: str = ""  # 专辑名
    album_id: str = ""  # 专辑ID
    interval: int = 0  # 时长(秒)
    img: Optional[str] = None  # 封面图URL
    lrc: Optional[str] = None  # 歌词
    types: List[Dict] = field(default_factory=list)  # 可用音质列表
    _types: Dict = field(default_factory=dict)  # 音质详情 (hash/size)
    type_url: Dict = field(default_factory=dict)  # 音质URL缓存
    other_source: Optional[str] = None  # 备用源

    def __repr__(self):
        return f"MusicInfo({self.name} - {self.singer}, {self.source}/{self.songmid})"


class BaseMusicSource(abc.ABC):
    """音乐源基类

    子类必须实现:
        - search(keyword, page, limit) -> List[MusicInfo]
        - get_music_url(info, quality) -> Optional[str]
        - get_lyric(info) -> Optional[str]
        - get_pic_url(info) -> Optional[str]
    """

    # 子类必须覆盖
    source_id: str = ""
    source_name: str = ""
    limits: Dict = {"search": 30, "lyric": 1, "url": 1}

    def __init__(self):
        self._session = create_session()

    # ── 抽象接口 ──────────────────────────────────────

    @abc.abstractmethod
    def search(self, keyword: str, page: int = 1, limit: int = 30) -> List[MusicInfo]:
        """搜索歌曲

        Args:
            keyword: 搜索关键词 (歌曲名+歌手名)
            page: 页码 (从1开始)
            limit: 每页数量

        Returns:
            歌曲信息列表
        """
        ...

    @abc.abstractmethod
    def get_music_url(self, info: MusicInfo, quality: str = "128k") -> Optional[str]:
        """获取歌曲播放URL

        Args:
            info: 歌曲信息
            quality: 期望音质

        Returns:
            可播放的URL或None
        """
        ...

    @abc.abstractmethod
    def get_lyric(self, info: MusicInfo) -> Optional[str]:
        """获取歌词

        Args:
            info: 歌曲信息

        Returns:
            LRC格式歌词文本或None
        """
        ...

    @abc.abstractmethod
    def get_pic_url(self, info: MusicInfo) -> Optional[str]:
        """获取封面图片URL

        Args:
            info: 歌曲信息

        Returns:
            封面图URL或None
        """
        ...

    # ── HTTP 辅助方法 ─────────────────────────────────

    def http_get(
        self,
        url: str,
        headers: Optional[Dict] = None,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = MAX_RETRIES,
        **kwargs,
    ) -> requests.Response:
        """带重试的GET请求"""
        return self._request("GET", url, headers=headers, timeout=timeout, retries=retries, **kwargs)

    def http_post(
        self,
        url: str,
        data: Optional[Dict] = None,
        json: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        timeout: int = DEFAULT_TIMEOUT,
        retries: int = MAX_RETRIES,
        **kwargs,
    ) -> requests.Response:
        """带重试的POST请求"""
        return self._request(
            "POST", url, data=data, json=json, headers=headers, timeout=timeout, retries=retries, **kwargs
        )

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        retries = kwargs.pop("retries", MAX_RETRIES)
        last_error = None
        for attempt in range(retries + 1):
            try:
                resp = self._session.request(method, url, **kwargs)
                resp.raise_for_status()
                return resp
            except requests.RequestException as e:
                last_error = e
                if attempt < retries:
                    delay = RETRY_BASE_DELAY * (2**attempt)
                    logger.debug(f"[{self.source_id}] 请求重试 {attempt + 1}/{retries}: {url} (delay={delay}s)")
                    time.sleep(delay)
        raise last_error

    # ── 音质辅助方法 ─────────────────────────────────

    def get_best_quality(self, info: MusicInfo, preferred: str = "128k") -> str:
        """根据偏好获取最佳可用音质"""
        available = {t.get("type") for t in info.types}
        if not available:
            return "128k"
        if preferred in available:
            return preferred
        # 从高到低匹配
        for q in QualityLevel.ALL:
            if q in available:
                return q
        return "128k"

    def format_duration(self, seconds: int) -> str:
        """格式化时长为 m:ss"""
        if seconds <= 0:
            return "0:00"
        m = seconds // 60
        s = seconds % 60
        return f"{m}:{s:02d}"

    def format_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes < 1024:
            return f"{size_bytes}B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f}K"
        else:
            return f"{size_bytes / (1024 * 1024):.1f}M"
