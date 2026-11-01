"""应用配置：JSON 持久化 + 默认值合并。

配置写在程序目录下的 ``data/config.json``，采用「浅合并 + 点号路径」读写，
保证新增配置项时老配置文件不会报错。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict

from . import paths
from .jsonio import atomic_write_json

logger = logging.getLogger(__name__)


DEFAULTS: Dict[str, Any] = {
    # ── 外观 ────────────────────────────────────────────────
    "appearance": {
        "theme": "auto",            # auto | light | dark
        "accent": "#6C4DF6",        # 品牌主色（强调色）
        # 窗口背景：solid（主题底色）| image（自定义背景图）。
        # 早先这里写的是 "mica | solid | gradient"，但三个值一个都没实现过，
        # 而且 "gradient" 违反本项目的 UI 规范（不用渐变）—— 现在只留真正生效的两种。
        "background": "solid",
        "background_image": "",     # data/backgrounds/ 下的文件名（不存绝对路径）
        # 下面这几个默认值是照着「图看清楚、面板别糊住图」调出来的：
        # 背景图本身给足 85% 不透明度，磨砂只留 10%（糊太狠就白瞎了原图），
        # 蒙版 30% 保证文字读得清；四个分区都比以前更透，内容区最透（卡片 20%）、
        # 贴着窗口边的播放栏最实（40%）。
        "background_opacity": 85,   # 0-100：背景图自身的不透明度
        "background_blur": 10,      # 0-100：磨砂感（模糊）
        "background_scrim": 30,     # 0-100：蒙版浓度（压暗/提亮，保证文字可读）
        # 分区不透明度（0-100）。内容区（主体）不设面板色，图直接透出来最清；
        # 侧边栏 / 播放栏 / 覆盖层 / 卡片各自压一层，越「贴边」越实 ——
        # 一眼能看出层次，又不至于把图全挡掉。
        "surface_sidebar": 30,      # 左侧导航栏
        "surface_bottom": 40,       # 底部播放栏
        "surface_overlay": 35,      # 覆盖层页面：歌词页（展开播放）、歌单/专辑/歌手详情
        "surface_card": 20,         # 内容区里的卡片与列表底
        "animations": True,
        "nav_expanded": True,
    },
    # ── 播放 ────────────────────────────────────────────────
    "playback": {
        "volume": 65,
        "muted": False,
        "mode": "loop_list",        # order | loop_list | loop_single | shuffle
        "quality": "320k",          # 128k | 320k | flac | flac24bit
        "auto_play_next": True,
        "fade_in_ms": 0,
        "remember_progress": True,
        "seek_step_ms": 5000,
        # 启动时把上次退出时的播放队列与在播曲目摆回来（不自动播放）
        "restore_session": True,
    },
    # ── 音量均衡 ────────────────────────────────────────────
    # 响度是**实测**出来的（EBU R128 / BS.1770 门限积分响度），存的是测量值，
    # 增益按这里的参数现算 —— 所以改目标响度不必重算整库。
    "audio": {
        "equalize": True,           # 音量均衡总开关
        "target_lufs": -14.0,       # 目标响度（-14 是流媒体通行口径）
        "preamp_db": 0.0,           # 额外增益（整体再抬一点 / 压一点）
        "allow_boost": True,        # 是否允许把轻的曲子抬上来（关掉则只衰减）
        "max_gain_db": 12.0,
        "min_gain_db": -24.0,
        # 每首分析多少秒音频，0 = 全曲。在线曲目会再拉一遍音频，
        # 想省流量可以设成 120 之类（实测与全曲通常差 1 LU 以内）。
        "analysis_seconds": 0,
        # 解码的两个调参（一般不用动）：单次分析的整体超时（秒），以及每轮
        # 事件循环最多花在解码上的时间（秒）—— 后者决定解码期间界面有多流畅。
        "decode_timeout": 180,
        "slice_seconds": 0.008,
        "prefetch_next": True,      # 预取队列下一首的响度
        "prefer_tags": False,       # 本地文件优先用 ReplayGain 标签（默认以实测为准）
        "source_offset": True,      # 分析失败的曲目按同音源中位增益兜底
    },
    # ── 更新 ────────────────────────────────────────────────
    "update": {
        "auto_check": True,         # 启动后静默检查 + 定期检查
        "interval_hours": 24,       # 自动检查间隔（小时）
        "include_prerelease": False,
        "last_check": 0,            # 上次检查的时间戳（重启后不必立刻再查）
        "skip_version": "",         # 「跳过这个版本」记在这里
    },
    # ── 音源 ────────────────────────────────────────────────
    "sources": {
        "enabled": ["netease", "local"],
        "priority": ["netease", "local"],
        "fallback": True,           # 播放失败时跨音源兜底
        "prefer_original": True,    # 优先原唱
        "concurrency": 6,
    },
    # ── 网易云账号 ──────────────────────────────────────────
    "account": {
        "provider": "netease",
        "auto_login": True,         # 启动时用已保存凭据自动登录
        "save_credentials": True,
        # 凭据剩余有效期少于这么多天时自动续期（0 = 关闭）。
        # 服务端给 MUSIC_U 的 Max-Age 是 180 天，续期会重新计时。
        "cookie_refresh_days": 7,
        # 便携模式：主密钥不以本机绑定，整个程序目录拷到别的电脑也能解开凭据
        # （Windows 上即不再用当前用户的 DPAPI 包裹）。关掉更安全，但换机要重新登录。
        "portable": False,
    },
    # ── 存储 ────────────────────────────────────────────────
    "storage": {
        "cache_cover": True,
        "cache_lyric": True,
        "cache_media": False,
        "media_cache_limit_mb": 2048,
        "download_dir": "",         # 空 = 程序目录下的 downloads
        # 「保存封面」的另存为对话框上次用过的目录（空 = 系统图片目录）
        "cover_dir": "",
    },
    # ── 歌曲下载 ────────────────────────────────────────────
    # 目录用 storage.download_dir（不在这里再开一个键，同一个东西只能有一处真相）
    "download": {
        "quality": "320k",          # auto | flac24bit | flac | 320k | 128k
        "filename_template": "{singer} - {name}",
        "write_tags": True,         # 写标题 / 歌手 / 专辑 / 年份标签（需要 mutagen）
        "embed_cover": True,        # 写标签时顺便把封面嵌进去
        "save_lyric": True,         # 同目录同名 .lrc（UTF-8 带 BOM）
        "duplicate": "rename",      # rename 自动加序号 | skip 跳过 | overwrite 覆盖
        "add_to_library": False,    # 下完自动加进「本地音乐」曲库
        "concurrency": 2,           # 同时下载几首（1-6，太高会被音源风控）
    },
    # ── 歌词 / 桌面歌词 ─────────────────────────────────────
    "lyrics": {
        "show_translation": True,
        "show_romaji": False,
        # 逐字（动态）歌词：有逐字数据就用真的，没有就按行时间摊出伪动态
        "dynamic": True,
        "font_size": 17,
        "alignment": "center",      # left | center | right
        "desktop_lyric": False,
        "desktop_lyric_locked": False,
    },
    # ── 本地音乐 ────────────────────────────────────────────
    "local": {
        "folders": [],
        "extensions": [".mp3", ".flac", ".m4a", ".aac", ".wav", ".ogg", ".opus", ".wma"],
        "scan_on_start": False,
        # 扫描时按「歌名 + 歌手」在线匹配封面与歌词（只写曲库索引，不改音乐文件）。
        # 首次扫描大曲库会慢一些，重扫会沿用上次结果，不再重复搜索。
        "match_online": True,
    },
    # ── 窗口 ────────────────────────────────────────────────
    "window": {
        "width": 1180,
        "height": 760,
        "maximized": False,
        "player_expanded": False,
        "queue_visible": False,
        # 系统托盘：图标是否常驻（系统没有托盘时这个开关没有意义）
        "tray_icon": True,
        # 关闭主窗口时：ask 每次询问 | tray 最小化到托盘 | quit 直接退出
        "close_action": "ask",
    },
    # ── 高级 ────────────────────────────────────────────────
    "advanced": {
        "proxy": "",
        "timeout": 15,
        "log_level": "INFO",
    },
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for k, v in (override or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """线程安全的配置对象。"""

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path else paths.config_file()
        self._lock = threading.RLock()
        self._data: Dict[str, Any] = json.loads(json.dumps(DEFAULTS))
        self.load()

    # ── 读写 ────────────────────────────────────────────────

    def load(self) -> None:
        with self._lock:
            try:
                if self._path.exists():
                    raw = json.loads(self._path.read_text(encoding="utf-8") or "{}")
                    if isinstance(raw, dict):
                        self._data = _deep_merge(DEFAULTS, raw)
            except Exception as e:  # 配置损坏不应阻塞启动
                logger.warning("配置读取失败，使用默认值: %s", e)
                self._data = json.loads(json.dumps(DEFAULTS))

    def save(self) -> None:
        with self._lock:
            try:
                # 原子写 + indent=2：配置是给人看、也给手改的（见 app/jsonio.py）
                atomic_write_json(self._path, self._data, indent=2)
            except Exception as e:
                logger.error("配置保存失败: %s", e)

    # ── 访问 ────────────────────────────────────────────────

    def get(self, dotted: str, default: Any = None) -> Any:
        with self._lock:
            node: Any = self._data
            for part in dotted.split("."):
                if not isinstance(node, dict) or part not in node:
                    return default
                node = node[part]
            return node

    def set(self, dotted: str, value: Any, *, autosave: bool = True) -> None:
        with self._lock:
            parts = dotted.split(".")
            node = self._data
            for part in parts[:-1]:
                nxt = node.get(part)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[part] = nxt
                node = nxt
            node[parts[-1]] = value
        if autosave:
            self.save()

    def update(self, values: Dict[str, Any], *, autosave: bool = True) -> None:
        for k, v in values.items():
            self.set(k, v, autosave=False)
        if autosave:
            self.save()

    def as_dict(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def reset(self) -> None:
        with self._lock:
            self._data = json.loads(json.dumps(DEFAULTS))
        self.save()


_instance: Config | None = None


def config() -> Config:
    global _instance
    if _instance is None:
        _instance = Config()
    return _instance
