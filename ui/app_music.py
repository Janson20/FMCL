"""ModernApp 音乐播放器 Mixin - 音乐标签页相关方法"""

import json
import math
import os
import platform
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter.filedialog as filedialog
import webbrowser
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import customtkinter as ctk
import requests
from logzero import logger

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _
from ui.music_desktop_lyric import DesktopLyricWindow
from ui.music_effects import (
    EQ_FREQS,
    EQ_GAIN_MAX,
    EQ_GAIN_MIN,
    PITCH_MAX,
    PITCH_MIN,
    SPEED_MAX,
    SPEED_MIN,
    AudioEffectProcessor,
    EffectSettings,
)
from ui.music_lyrics import LyricLine, LyricParser
from ui.music_playlist import (
    HISTORY_PLAYLIST_ID,
    SORT_ADD_TIME_ASC,
    SORT_ADD_TIME_DESC,
    SORT_NAME_ASC,
    SORT_NAME_DESC,
    Playlist,
    PlaylistManager,
    PlaylistSong,
)
from ui.music_risk_captcha import run_captcha_flow
from ui.music_source import MUSIC_SOURCES, SOURCE_META, resolve_track, search_all
from ui.music_source.base import MusicInfo as OnlineMusicInfo
# ════════════════════════════════════════════════════════════════════════
# 阶段 1 任务 1.4-A（形态 1：整体搬家）：音频解析/校验/转码住进了 services/
#
# 搬走的 10 个模块级函数实测零 GUI 触点（不碰控件、不调 after、不用 i18n），
# 现在住在 ``services/music_audio.py``。下面这批 ``_xxx`` 别名在**导入时**绑定，
# 所以 ``ui.app_music._extract_audio_metadata is services.music_audio.extract_audio_metadata``
# 成立；本文件其余方法（含本轮范围外的在线搜索 / 下载回退 / 歌单 UI）继续按旧名调用，
# 公开行为一字不变。
#
# 降级开关的**真正读取点**已随实现搬到服务侧（函数体按模块级全局名读它们），
# 因此测试补丁点也一并搬到 services.music_audio —— 见
# ``tests/test_music_fallback.py`` 顶部的中文说明。
# ════════════════════════════════════════════════════════════════════════
from services.music_audio import (  # noqa: F401  （常量 + 降级开关的旧名兼容）
    AUDIO_EXTENSIONS,
    MUSIC_METADATA_CACHE_MAX,
    _AUDIO_FILE_MAGIC,
    _DURATION_TOLERANCE_MIN_SEC,
    _DURATION_TOLERANCE_RATIO,
    _LOSSLESS_EXTENSIONS,
    _M4A_FTYP_MAGIC,
    _mutagen_import_error,
)
from services.music_audio import extract_audio_metadata as _extract_audio_metadata
from services.music_audio import format_local_quality as _format_local_quality
from services.music_audio import format_online_quality as _format_online_quality
from services.music_audio import format_play_count as _format_play_count
from services.music_audio import format_time as _format_time
from services.music_audio import get_tag as _get_tag
from services.music_audio import is_m4a_container as _is_m4a_container
from services.music_audio import transcode_audio_to_wav as _transcode_audio_to_wav
from services.music_audio import validate_audio_duration as _validate_audio_duration
from services.music_audio import validate_audio_file_header as _validate_audio_file_header

# ════════════════════════════════════════════════════════════════════════
# 阶段 1 任务 1.4-B（形态 2：逻辑与界面切分）：**在线侧**的判定与编排搬进 services/
#
#   services/music_online.py    —— 搜索准入/结果归一化/分页/自动音质/取流完成判定/正在播放取值
#   services/music_download.py  —— 多源回退下载编排、临时文件规则、B站风控重试编排
#   services/music_wy_remote.py —— 网易云远程歌单同步编排与分页数据准备（只读、不落盘）
#
# 切缝与 1.4-A 同一条：**这段代码是否 import GUI、或是否直接创建/销毁控件**。
# 控件、线程、after、i18n 文案一律留在本文件；服务把这些值当参数收进来、
# 把新值放在返回值里（SearchOutcome / PagerPlan / SyncApplyPlan 这些数据类）。
#
# 1.4-A 的「引擎独占 + 界面镜像」约定原样成立：本轮**没有**任何一处在委托后
# 改写引擎状态 —— 镜像块所在的 `_play_online_file` / `_play_file` 逐字节未动，
# `_music_metadata_cache` / `_music_modes_used` 仍是引擎里那个对象。
# ════════════════════════════════════════════════════════════════════════
from services import music_download, music_online
from services import music_wy_remote as wy_remote

# ════════════════════════════════════════════════════════════════════════
# 阶段 1 任务 1.4-C（形态 2：逻辑与界面切分）：**本地侧**的判定与编排搬进 services/
#
#   services/music_local.py    —— 本地歌单 CRUD/排序/播放历史/侧边栏与行清单/播放全部/底栏取值（新增）
#   services/music_hotkeys.py  —— 全局热键键位表与注册/注销编排（新增）
#   services/music_lyric_display.py  —— 歌词轮询判定、当前行与副文本取值、取词装载（新增）
#   services/music_effects_panel.py  —— 音效显示文案格式化、重置默认值、临时文件清理（新增）
#   services/desktop_lyric.py  —— 桌面歌词开关的目标可见性、待推送的歌词行（1.4-C 追加）
#   services/music_wy_remote.py—— 1.4-B 留的 is_remote_key/strip_remote_key/entry_label 接上调用方
#
# 后两个（歌词显示 / 音效面板）为什么另开新模块、不直接追加进 music_lyrics.py 与
# music_effects.py：1.4-A 的整体搬家守卫 test_services_relocation.py::
# test_implementation_kept_the_original_line_count 把那两个文件的行数钉死在 git 原文，
# 例外登记表 REGISTERED_LINE_DELTAS 又住在禁改的 tests/test_services_*.py 里。
#
# 切缝与 1.4-A / 1.4-B 同一条：**这段代码是否 import GUI、是否直接创建/销毁/配置控件、
# 是否 self.after 排期、是否读 winfo_exists()**。控件、线程、after、i18n 文案一律留在
# 本文件；服务把值当参数收进来、把新值放在返回值里（*Plan / *Outcome 这些数据类）。
#
# 1.4-A 的「引擎独占 + 界面镜像」约定原样成立：本轮**没有**一处在委托后改写引擎状态
# —— `_play_file` / `_play_online_file` 逐字节未动。
# ════════════════════════════════════════════════════════════════════════
from services import desktop_lyric, music_effects_panel, music_hotkeys, music_local, music_lyric_display


_pygame_import_error = None
try:
    import pygame
    import pygame.mixer as mixer
except ImportError as e:
    _pygame_import_error = e


# 网易云账号歌单（侧边栏同步，只读）:
# 条目 id 前缀（区分本地歌单与远程歌单，远程歌单不进入 PlaylistManager，
# 因此永不落盘、永不参与本地歌单的任何编辑操作）。
# 阶段 1.4-B：前缀/每页条数/刷新间隔的唯一真相搬到 services.music_wy_remote
# （`remote_key()` 也住那儿），这里保留旧名再导出，本文件其余方法一个字都不用改。
_WY_REMOTE_PREFIX = wy_remote.REMOTE_PREFIX
# 远程歌单歌曲列表每页条数（超过 20 首时按页展示）
_MUSIC_WY_PAGE_SIZE = wy_remote.PAGE_SIZE
# 定期自动刷新歌单列表的间隔（毫秒）
_WY_REMOTE_PERIODIC_MS = wy_remote.PERIODIC_MS

# ── 播放模式 / 淡入淡出 / 轮询参数（阶段 1.4-A：以 services/music_player 为唯一真相）──
# 旧名在下面全部保留为再导出，本文件其余 180 多个方法一个字都不用改。
from services.music_player import ALL_PLAY_MODES  # noqa: F401
from services.music_player import FADE_INTERVAL_MS, FADE_STEPS  # noqa: F401
from services.music_player import FADE_OUT_PAUSE, FADE_OUT_STOP  # noqa: F401
from services.music_player import MusicPlayerService
from services.music_player import PLAY_MODE_LOOP_LIST, PLAY_MODE_LOOP_SINGLE  # noqa: F401
from services.music_player import PLAY_MODE_NAMES, PLAY_MODE_RANDOM  # noqa: F401
from services.music_player import PLAY_MODE_SEQUENTIAL  # noqa: F401
from services.music_player import PROGRESS_POLL_IDLE_MS, PROGRESS_POLL_MS  # noqa: F401
from services.music_state import (  # noqa: F401
    LOAD_RETRY_MAX,
    LOAD_RETRY_MS,
    PERIODIC_SAVE_INTERVAL_MS,
    SAVE_DEBOUNCE_MS,
    apply_wy_cookie,
    build_music_state,
    login_retry_due,
    parse_music_state,
    read_saved_cookie,
    volume_to_slider,
)

# 阶段 1.4-C：7 个动作的组合键表搬到 services.music_hotkeys
# （注册与注销共用同一份动作顺序，不会再出现"只改了其中一处"）。
# 这里保留旧名再导出 —— `DEFAULT_HOTKEYS` 全仓库只有本文件读，别的读者一字不用改。
DEFAULT_HOTKEYS = music_hotkeys.DEFAULT_HOTKEYS



MUSIC_ORIGINAL_FEEDBACK_URL = "https://doc.weixin.qq.com/forms/AKgAhAf7ABQAUoAtgbcAHkCNf0v0B41mf"


_hotkey_import_error = None
try:
    import keyboard as _keyboard

    _keyboard_available = True
except Exception as e:
    _hotkey_import_error = e
    _keyboard_available = False


# ════════════════════════════════════════════════════════════════════════
# 阶段 1 任务 1.4-A（形态 1）：SMTC（Windows 系统媒体控制）控制器整体搬进
# ``services/music_smtc.py``。类名去掉了前导下划线，这里保留 ``_SMTCController``
# 旧名别名 —— 它仍然是"零 GUI 控件、按主线程契约使用调用方 after"的那个实现。
# winsdk 的降级探测（``_winsdk_available`` / ``_winsdk_import_error``）也住在
# 该模块；这里只做旧名再导出，**真正被读取的名字**是
# ``services.music_smtc._winsdk_available`` 与 ``services.music_audio._winsdk_available``。
# ════════════════════════════════════════════════════════════════════════
from services.music_smtc import SMTCController as _SMTCController  # noqa: F401
from services.music_smtc import _winsdk_available, _winsdk_import_error  # noqa: F401


class MusicPlayerMixin(object):
    def __init_music(self):
        # ── 播放引擎（阶段 1.4-A）：状态机/决策/纯计算住在 services/music_player.py ──
        # 直接构造而不注册进 AppContext：注册要改 app/**（禁改文件），
        # 而 Service 基类本身就支持"单独实例化做单元测试"。它没有 context，
        # 所以只用状态机/决策/mixer 驱动这些不依赖 context 的成员。
        self._music_engine = MusicPlayerService()
        self._music_playlist: List[str] = []
        self._music_current_index: int = -1
        self._music_is_playing: bool = False
        self._music_is_paused: bool = False
        self._music_volume: float = 0.7
        self._music_play_mode: int = PLAY_MODE_LOOP_LIST
        self._music_last_folder: str = ""
        self._music_progress: float = 0
        self._music_seek_offset: float = 0
        self._music_duration: float = 0
        # 元数据缓存由引擎持有，这里**采纳同一个 OrderedDict**：范围外的
        # _music_scan_folder_restore 仍会 self._music_metadata_cache.clear()，
        # 共享同一个对象才能让引擎缓存同步清空（不会出现两份缓存各自为政）。
        self._music_metadata_cache: OrderedDict = self._music_engine.metadata_cache
        self._music_mini_mode: bool = False
        self._music_progress_timer_id = None
        self._music_init_done: bool = False
        self._music_hotkeys_registered: bool = False
        self._music_warmup_hook = None
        self._music_playlist_widgets: List[dict] = []
        self._music_smtc: _SMTCController = _SMTCController()
        self._music_smtc.set_parent(self)
        self._music_fade_timer_id = None
        self._music_is_fading = False
        self._music_fade_out_target: Optional[str] = None
        # 同上：成就判据用的"试过哪些播放模式"集合也采纳引擎里那一个
        self._music_modes_used: set = self._music_engine.state.modes_used
        # ── 歌单管理 ──
        self._music_playlist_manager: PlaylistManager = PlaylistManager()
        # ── 在线搜索状态 ──
        self._music_tab_mode: str = "local"  # "local" | "online"
        self._music_search_results: List[OnlineMusicInfo] = []
        self._music_search_widgets: List[dict] = []
        self._music_search_thread_id = None
        self._music_selected_source: str = "kw"
        self._music_search_keyword: str = ""
        self._music_search_page: int = 1  # 当前搜索页码（从 1 开始）
        self._music_search_page_size: int = 30  # 每页搜索条数
        self._music_search_has_more: bool = False  # 当前页是否满页（可能存在下一页）
        self._music_search_total_pages: int = 0  # 搜索结果总页数（音源提供总数时一次性算出，0=未知）
        self._music_search_busy: bool = False  # 搜索请求进行中标记
        self._music_search_seq: int = 0  # 搜索请求序号（防旧请求覆盖新请求）
        self._music_pager_widgets: List[dict] = []  # 分页页码按钮组件列表
        self._music_current_online_info: Optional[OnlineMusicInfo] = None
        self._music_is_online_playing: bool = False
        self._music_current_filepath: Optional[str] = None  # 当前播放的文件路径（本地/在线临时文件）
        self._music_current_quality: str = ""  # 在线播放实际获取到的音质档位（128k/320k/flac）
        self._music_temp_files: List[str] = []  # 缓存的临时文件列表
        # 下载编排的注入缝 + 「界面自己那个 list 对象」：服务就地增删这一个 list，
        # 范围外的旧读者（_music_cleanup_temp_files 等）看到的仍是同一份内容
        # —— 与 1.4-A 采纳同一个 _music_metadata_cache 是同一手法。
        self._music_download_ctx = music_download.DownloadContext(
            temp_files=self._music_temp_files
        )
        self._music_stream_seq: int = 0  # 在线播放请求序号（防旧线程覆盖新请求）
        # ── 复制歌名/歌手 ──
        self._music_now_title: str = ""  # 当前播放的歌名（供复制按钮使用）
        self._music_now_artist: str = ""  # 当前播放的歌手（供复制按钮使用）
        self._music_copy_feedback_timer = None  # 复制反馈文字恢复定时器
        # ── 歌词状态 ──
        self._music_lyric_parser: LyricParser = LyricParser()
        self._music_lyric_lines: List[LyricLine] = []
        self._music_show_lyric_translation: bool = True
        self._music_show_lyric_roma: bool = False
        self._music_desktop_lyric: Optional[DesktopLyricWindow] = None
        self._music_lyric_poll_id = None
        # ── 音效状态 ──
        self._music_effects = AudioEffectProcessor()
        self._music_effects_processed_files: List[str] = []  # 效果处理产生的临时文件
        # ── 定时保存 ──
        self._music_periodic_save_id = None
        # ── 歌单上下文（播歌单中的歌曲时记录，供上下曲使用） ──
        self._music_playlist_context_songs: List[PlaylistSong] = []
        self._music_playlist_context_idx: int = -1
        # ── 歌单下一首预取（播放过半时后台加载，放完秒播） ──
        self._music_prefetch_seq: int = 0  # 预取请求序号（任何新播放都会使其失效）
        self._music_prefetch_slot: Optional[dict] = None  # 预取就绪槽位 {seq, idx, song_key, temp_path, ...}
        self._music_prefetch_started: bool = False  # 当前歌曲是否已触发过预取（每首只触发一次）
        # ── 网易云账号歌单同步（只读，不落盘） ──
        self._music_wy_remote_playlists: List[dict] = []  # [{id, name, track_count, cover_url}]
        self._music_wy_remote_cache: Dict[str, List[PlaylistSong]] = {}  # 歌单id -> 歌曲（内存缓存）
        self._music_wy_remote_view_id: Optional[str] = None  # 当前查看的远程歌单 id
        self._music_wy_remote_view_songs: List[PlaylistSong] = []  # 当前查看的远程歌单歌曲
        self._music_wy_loading_ids: Set[str] = set()  # 歌曲加载中的歌单 id（防重复请求）
        self._music_wy_sync_busy: bool = False  # 歌单列表同步进行中
        self._music_wy_sync_seq: int = 0  # 同步请求序号（防旧结果覆盖新请求）
        self._music_wy_sync_failed: bool = False  # 最近一次列表同步是否失败
        self._music_wy_remote_sort_mode: str = SORT_ADD_TIME_DESC  # 远程歌单内存排序模式
        self._music_wy_remote_page: int = 1  # 远程歌单当前页码（每页 _MUSIC_WY_PAGE_SIZE 首）
        self._music_wy_queue: "queue.Queue" = queue.Queue()  # 后台线程 -> 主线程事件队列
        self._music_wy_dispatcher_id = None
        self._music_wy_periodic_id = None

    def _init_music_lazy(self):
        if self._music_init_done:
            return
        self._music_init_done = True
        if _pygame_import_error is not None:
            logger.warning(f"pygame 导入失败: {_pygame_import_error}")
        else:
            try:
                pygame.init()
                mixer.init()
                try:
                    mixer.music.set_volume(0)
                    mixer.music.set_volume(self._music_volume)
                except Exception:
                    pass
                logger.info("pygame mixer 初始化完成")
            except Exception as e:
                logger.error(f"pygame mixer 初始化失败: {e}")
        self._load_music_state()
        self._music_apply_wy_saved_login()
        if self._music_playlist:
            self._rebuild_playlist_ui()
        # 启动网易云账号歌单同步调度器与定期刷新（已登录时生效，未登录自动跳过）
        try:
            self._music_wy_dispatcher_id = self.after(200, self._music_wy_dispatcher_tick)
            self._music_wy_start_periodic()
        except Exception as e:
            logger.debug(f"启动网易云歌单同步调度失败: {e}")
        # 启动定时保存（30 秒间隔，避免频繁写盘）
        self._music_start_periodic_save()

    def _build_music_tab_content(self):
        self.__init_music()
        self._music_tab_content = ctk.CTkFrame(self.music_tab, fg_color="transparent")
        self._music_tab_content.pack(fill=ctk.BOTH, expand=True)

        # 子标签页切换栏
        self._build_music_source_tabs()

        # 本地音乐主框架
        self._music_main_frame = ctk.CTkFrame(self._music_tab_content, fg_color="transparent")
        self._build_music_control_panel()
        self._build_music_playlist_panel()
        self._build_music_now_playing()
        self._build_music_mini_bar()
        self._music_mini_bar.pack_forget()

        # 在线搜索框架
        self._music_online_frame = ctk.CTkFrame(self._music_tab_content, fg_color="transparent")
        self._build_music_online_panel()

        # 歌单标签页
        self._build_music_playlist_tab_panel()

        self._music_main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        self._init_music_lazy()
        self.after(500, self._register_hotkeys)

    def _build_music_control_panel(self):
        panel = ctk.CTkFrame(self._music_main_frame, fg_color=COLORS["card_bg"], corner_radius=12)
        panel.pack(fill=ctk.X, pady=(0, 10))
        self._music_control_panel = panel

        top_row = ctk.CTkFrame(panel, fg_color="transparent")
        top_row.pack(fill=ctk.X, padx=12, pady=(12, 5))

        ctk.CTkLabel(
            top_row,
            text=_("music_open_folder"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self._music_folder_btn = ctk.CTkButton(
            top_row,
            text="📂",
            width=35,
            height=30,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["accent"],
            command=self._music_open_folder,
        )
        self._music_folder_btn.pack(side=ctk.LEFT, padx=(8, 0))

        self._music_folder_label = ctk.CTkLabel(
            top_row,
            text=_("music_no_folder"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._music_folder_label.pack(side=ctk.LEFT, padx=(10, 0))

        self._music_mini_toggle_btn = ctk.CTkButton(
            top_row,
            text=_("music_mini_mode"),
            width=80,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._music_toggle_mini_mode,
        )
        self._music_mini_toggle_btn.pack(side=ctk.RIGHT, padx=(5, 0))

        self._music_song_count_label = ctk.CTkLabel(
            top_row, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=11), text_color=COLORS["text_secondary"]
        )
        self._music_song_count_label.pack(side=ctk.RIGHT, padx=(10, 0))

        ctk.CTkFrame(panel, fg_color=COLORS["card_border"], height=1).pack(fill=ctk.X, padx=12, pady=3)

        ctrl_row = ctk.CTkFrame(panel, fg_color="transparent")
        ctrl_row.pack(fill=ctk.X, padx=12, pady=(2, 8))

        play_btns = ctk.CTkFrame(ctrl_row, fg_color="transparent")
        play_btns.pack(side=ctk.LEFT)

        btn_cfg = {
            "width": 36,
            "height": 30,
            "font": ctk.CTkFont(size=14),
            "fg_color": COLORS["bg_light"],
            "hover_color": COLORS["accent"],
        }

        self._music_prev_btn = ctk.CTkButton(play_btns, text="⏮", command=self._music_prev, **btn_cfg)
        self._music_prev_btn.pack(side=ctk.LEFT, padx=2)

        self._music_play_btn = ctk.CTkButton(play_btns, text="▶", command=self._music_toggle_play, **btn_cfg)
        self._music_play_btn.pack(side=ctk.LEFT, padx=2)

        self._music_next_btn = ctk.CTkButton(play_btns, text="⏭", command=self._music_next, **btn_cfg)
        self._music_next_btn.pack(side=ctk.LEFT, padx=2)

        self._music_stop_btn = ctk.CTkButton(play_btns, text="⏹", command=self._music_stop, **btn_cfg)
        self._music_stop_btn.pack(side=ctk.LEFT, padx=2)

        self._music_mode_btn = ctk.CTkButton(
            ctrl_row,
            text="🔁",
            width=36,
            height=30,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._music_cycle_mode,
        )
        self._music_mode_btn.pack(side=ctk.LEFT, padx=(10, 0))
        self._update_mode_btn_text()

        # 音效按钮
        self._music_fx_btn = ctk.CTkButton(
            ctrl_row,
            text="🎛",
            width=36,
            height=30,
            font=ctk.CTkFont(size=13),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._music_open_fx_panel,
        )
        self._music_fx_btn.pack(side=ctk.LEFT, padx=(4, 0))

        vol_frame = ctk.CTkFrame(ctrl_row, fg_color="transparent")
        vol_frame.pack(side=ctk.RIGHT)

        self._music_mute_btn = ctk.CTkButton(
            vol_frame,
            text="🔊",
            width=30,
            height=30,
            font=ctk.CTkFont(size=14),
            fg_color="transparent",
            hover_color=COLORS["bg_light"],
            command=self._music_toggle_mute,
        )
        self._music_mute_btn.pack(side=ctk.LEFT)

        self._music_vol_slider = ctk.CTkSlider(
            vol_frame,
            from_=0,
            to=100,
            width=100,
            command=self._music_set_volume,
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["accent_hover"],
        )
        self._music_vol_slider.set(int(self._music_volume * 100))
        self._music_vol_slider.pack(side=ctk.LEFT, padx=(5, 0))

        progress_frame = ctk.CTkFrame(panel, fg_color="transparent")
        progress_frame.pack(fill=ctk.X, padx=12, pady=(0, 8))

        self._music_cur_label = ctk.CTkLabel(
            progress_frame,
            text="0:00",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            width=40,
        )
        self._music_cur_label.pack(side=ctk.LEFT)

        self._music_progress_bar = ctk.CTkSlider(
            progress_frame,
            from_=0,
            to=100,
            command=self._music_seek,
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["accent_hover"],
        )
        self._music_progress_bar.set(0)
        self._music_progress_bar.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=5)

        self._music_end_label = ctk.CTkLabel(
            progress_frame,
            text="0:00",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            width=40,
        )
        self._music_end_label.pack(side=ctk.RIGHT)

        now_title_row = ctk.CTkFrame(panel, fg_color="transparent")
        now_title_row.pack(padx=12, anchor=ctk.W, pady=(0, 2))

        self._music_now_label_top = ctk.CTkLabel(
            now_title_row,
            text=_("music_no_track"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self._music_now_label_top.pack(side=ctk.LEFT)

        # 当前播放音质标签（FLAC/320K/192K/128K）
        self._music_quality_tag = ctk.CTkLabel(
            now_title_row,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10, weight="bold"),
            text_color=COLORS["accent"],
        )
        self._music_quality_tag.pack(side=ctk.LEFT, padx=(8, 0))

        # 歌名/歌手复制按钮
        copy_btn_cfg = {
            "width": 64,
            "height": 22,
            "font": ctk.CTkFont(family=FONT_FAMILY, size=10),
            "fg_color": COLORS["bg_light"],
            "hover_color": COLORS["card_border"],
            "state": "disabled",
        }

        self._music_copy_title_btn = ctk.CTkButton(
            now_title_row, text=_("music_copy_title"), command=self._music_copy_title, **copy_btn_cfg
        )
        self._music_copy_title_btn.pack(side=ctk.LEFT, padx=(8, 0))

        self._music_copy_artist_btn = ctk.CTkButton(
            now_title_row, text=_("music_copy_artist"), command=self._music_copy_artist, **copy_btn_cfg
        )
        self._music_copy_artist_btn.pack(side=ctk.LEFT, padx=(4, 0))

        self._music_now_label_sub = ctk.CTkLabel(
            panel, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=COLORS["text_secondary"]
        )
        self._music_now_label_sub.pack(padx=12, anchor=ctk.W, pady=(0, 8))

        self._theme_refs.append((self._music_prev_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_play_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_next_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_stop_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_mode_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))
        self._theme_refs.append(
            (
                self._music_vol_slider,
                {
                    "fg_color": "bg_light",
                    "progress_color": "accent",
                    "button_color": "text_primary",
                    "button_hover_color": "accent_hover",
                },
            )
        )
        self._theme_refs.append(
            (
                self._music_progress_bar,
                {
                    "fg_color": "bg_light",
                    "progress_color": "accent",
                    "button_color": "text_primary",
                    "button_hover_color": "accent_hover",
                },
            )
        )
        self._theme_refs.append((self._music_now_label_top, {"text_color": "text_primary"}))
        self._theme_refs.append((self._music_quality_tag, {"text_color": "accent"}))
        self._theme_refs.append(
            (self._music_copy_title_btn, {"fg_color": "bg_light", "hover_color": "card_border"})
        )
        self._theme_refs.append(
            (self._music_copy_artist_btn, {"fg_color": "bg_light", "hover_color": "card_border"})
        )
        self._theme_refs.append((self._music_now_label_sub, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_cur_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_end_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_folder_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_folder_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_mini_toggle_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))
        self._theme_refs.append((self._music_song_count_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_control_panel, {"fg_color": "card_bg"}))
        self._theme_refs.append((self._music_fx_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))

    def _build_music_playlist_panel(self):
        list_frame = ctk.CTkFrame(self._music_main_frame, fg_color=COLORS["card_bg"], corner_radius=12)
        list_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(0, 10))
        self._music_list_frame = list_frame

        header = ctk.CTkFrame(list_frame, fg_color="transparent", height=35)
        header.pack(fill=ctk.X, padx=12, pady=(12, 5))
        header.pack_propagate(False)

        ctk.CTkLabel(
            header,
            text=_("music_playlist"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self._music_scroll = ctk.CTkScrollableFrame(
            list_frame, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        self._music_scroll.pack(fill=ctk.BOTH, expand=True, padx=8, pady=(5, 10))
        self._theme_refs.append((self._music_scroll, {"scrollbar_button_color": "bg_light"}))

    def _build_music_playlist_tab_panel(self):
        """构建歌单标签页 — 左侧侧边栏 + 右侧歌曲列表 + 排序控件"""
        self._music_playlist_frame = ctk.CTkFrame(self._music_tab_content, fg_color="transparent")

        main = ctk.CTkFrame(self._music_playlist_frame, fg_color=COLORS["card_bg"], corner_radius=12)
        main.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        # ── 左侧：歌单侧边栏 ──
        sidebar_frame = ctk.CTkFrame(main, fg_color="transparent", width=160)
        sidebar_frame.pack(side=ctk.LEFT, fill=ctk.Y, padx=(8, 4), pady=10)
        sidebar_frame.pack_propagate(False)

        ctk.CTkLabel(
            sidebar_frame,
            text=_("music_playlists"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_secondary"],
        ).pack(anchor=ctk.W, padx=8, pady=(0, 6))

        self._music_playlist_sidebar = ctk.CTkScrollableFrame(
            sidebar_frame, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"], height=200
        )
        self._music_playlist_sidebar.pack(fill=ctk.BOTH, expand=True, padx=2)

        self._music_new_playlist_btn = ctk.CTkButton(
            sidebar_frame,
            text=_("music_new_playlist"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["accent"],
            height=28,
            command=self._music_create_playlist_dialog,
        )
        self._music_new_playlist_btn.pack(fill=ctk.X, padx=6, pady=(6, 0))

        separator = ctk.CTkFrame(main, fg_color=COLORS["card_border"], width=1)
        separator.pack(side=ctk.LEFT, fill=ctk.Y, padx=2, pady=10)

        # ── 右侧：歌曲列表区域 ──
        right_frame = ctk.CTkFrame(main, fg_color="transparent")
        right_frame.pack(side=ctk.LEFT, fill=ctk.BOTH, expand=True, padx=(4, 8), pady=10)

        # 排序控件
        sort_frame = ctk.CTkFrame(right_frame, fg_color="transparent", height=28)
        sort_frame.pack(fill=ctk.X, pady=(0, 4))
        sort_frame.pack_propagate(False)
        self._music_sort_frame = sort_frame

        sort_label_font = ctk.CTkFont(family=FONT_FAMILY, size=11)

        self._music_sort_add_time_btn = ctk.CTkButton(
            sort_frame,
            text=_("music_sort_add_time") + " ▼",
            font=sort_label_font,
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            height=24,
            width=100,
            command=lambda: self._music_do_sort(SORT_ADD_TIME_DESC),
        )
        self._music_sort_add_time_btn.pack(side=ctk.LEFT, padx=(0, 4))

        self._music_sort_name_btn = ctk.CTkButton(
            sort_frame,
            text=_("music_sort_name") + " ▲",
            font=sort_label_font,
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            height=24,
            width=80,
            command=lambda: self._music_do_sort(SORT_NAME_ASC),
        )
        self._music_sort_name_btn.pack(side=ctk.LEFT)

        # 播放全部按钮
        self._music_play_all_btn = ctk.CTkButton(
            sort_frame,
            text="▶ " + _("music_play_all"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            height=24,
            width=100,
            command=self._music_play_playlist_all,
        )
        self._music_play_all_btn.pack(side=ctk.RIGHT)

        # 歌单歌曲列表
        self._music_playlist_scroll = ctk.CTkScrollableFrame(
            right_frame, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        self._music_playlist_scroll.pack(fill=ctk.BOTH, expand=True)

        # ── 网易云远程歌单分页栏（超过 20 首时按页展示，默认隐藏） ──
        pager = ctk.CTkFrame(right_frame, fg_color="transparent", height=30)
        pager.pack(fill=ctk.X, pady=(4, 0))
        pager.pack_propagate(False)

        pager_btn_cfg = {
            "font": ctk.CTkFont(family=FONT_FAMILY, size=11),
            "fg_color": COLORS["bg_light"],
            "hover_color": COLORS["accent"],
            "height": 24,
            "width": 90,
        }

        self._music_wy_pager_prev = ctk.CTkButton(
            pager,
            text=_("music_page_prev"),
            command=lambda: self._music_wy_go_page(self._music_wy_remote_page - 1),
            **pager_btn_cfg,
        )
        self._music_wy_pager_prev.pack(side=ctk.LEFT)

        self._music_wy_pager_page_label = ctk.CTkLabel(
            pager,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        )
        self._music_wy_pager_page_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        self._music_wy_pager_next = ctk.CTkButton(
            pager,
            text=_("music_page_next"),
            command=lambda: self._music_wy_go_page(self._music_wy_remote_page + 1),
            **pager_btn_cfg,
        )
        self._music_wy_pager_next.pack(side=ctk.RIGHT)

        self._music_wy_pager_frame = pager
        self._music_wy_pager_frame.pack_forget()
        self._theme_refs.append((self._music_wy_pager_prev, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_wy_pager_next, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_wy_pager_page_label, {"text_color": "text_secondary"}))

        self._theme_refs.append((self._music_new_playlist_btn, {"fg_color": "bg_light", "hover_color": "accent"}))

        # 初始化状态
        self._music_playlist_sidebar_widgets: List[dict] = []

    def _build_music_now_playing(self):
        self._music_cover_frame = ctk.CTkFrame(
            self._music_main_frame, fg_color=COLORS["card_bg"], corner_radius=12, width=200
        )
        self._music_cover_frame.pack(side=ctk.RIGHT, fill=ctk.Y, padx=(10, 0))
        self._music_cover_frame.pack_propagate(False)

        self._music_cover_label = ctk.CTkLabel(
            self._music_cover_frame, text="🎵", font=ctk.CTkFont(size=60), text_color=COLORS["text_secondary"]
        )
        self._music_cover_label.pack(pady=(30, 10))

        self._music_cover_artist = ctk.CTkLabel(
            self._music_cover_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        self._music_cover_artist.pack(pady=(0, 5))

        self._music_cover_album = ctk.CTkLabel(
            self._music_cover_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        self._music_cover_album.pack(pady=(0, 10))
        self._theme_refs.append((self._music_cover_frame, {"fg_color": "card_bg"}))
        self._theme_refs.append((self._music_cover_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_cover_artist, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._music_cover_album, {"text_color": "text_secondary"}))

        # 歌词显示区域（封面下方）
        self._lyric_current_label = ctk.CTkLabel(
            self._music_cover_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["accent"],
            wraplength=180,
            justify="center",
        )
        self._lyric_current_label.pack(pady=(0, 4))

        self._lyric_trans_label = ctk.CTkLabel(
            self._music_cover_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            wraplength=180,
            justify="center",
        )
        self._lyric_trans_label.pack()
        self._theme_refs.append((self._lyric_current_label, {"text_color": "accent"}))
        self._theme_refs.append((self._lyric_trans_label, {"text_color": "text_secondary"}))

    def _build_music_mini_bar(self):
        self._music_mini_bar = ctk.CTkFrame(
            self._music_tab_content, fg_color=COLORS["card_bg"], corner_radius=8, height=55
        )
        self._music_mini_bar.pack_propagate(False)

        inner = ctk.CTkFrame(self._music_mini_bar, fg_color="transparent")
        inner.pack(fill=ctk.BOTH, expand=True, padx=10, pady=5)

        self._music_mini_title = ctk.CTkLabel(
            inner,
            text=_("music_no_track"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold"),
            text_color=COLORS["text_primary"],
        )
        self._music_mini_title.pack(side=ctk.LEFT, padx=(0, 10))

        btn_cfg = {
            "width": 30,
            "height": 28,
            "font": ctk.CTkFont(size=12),
            "fg_color": COLORS["bg_light"],
            "hover_color": COLORS["accent"],
        }

        self._music_mini_prev = ctk.CTkButton(inner, text="⏮", command=self._music_prev, **btn_cfg)
        self._music_mini_prev.pack(side=ctk.LEFT, padx=1)

        self._music_mini_play = ctk.CTkButton(inner, text="▶", command=self._music_toggle_play, **btn_cfg)
        self._music_mini_play.pack(side=ctk.LEFT, padx=1)

        self._music_mini_next = ctk.CTkButton(inner, text="⏭", command=self._music_next, **btn_cfg)
        self._music_mini_next.pack(side=ctk.LEFT, padx=1)

        self._music_mini_vol = ctk.CTkSlider(
            inner,
            from_=0,
            to=100,
            width=80,
            command=self._music_set_volume,
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
            button_color=COLORS["text_primary"],
            button_hover_color=COLORS["accent_hover"],
        )
        self._music_mini_vol.set(int(self._music_volume * 100))
        self._music_mini_vol.pack(side=ctk.RIGHT, padx=(5, 0))

        ctk.CTkButton(
            inner,
            text=_("music_expand"),
            width=60,
            height=28,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._music_toggle_mini_mode,
        ).pack(side=ctk.RIGHT, padx=(5, 0))

        self._theme_refs.append((self._music_mini_bar, {"fg_color": "card_bg"}))
        self._theme_refs.append((self._music_mini_title, {"text_color": "text_primary"}))
        self._theme_refs.append((self._music_mini_prev, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_mini_play, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_mini_next, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append(
            (
                self._music_mini_vol,
                {
                    "fg_color": "bg_light",
                    "progress_color": "accent",
                    "button_color": "text_primary",
                    "button_hover_color": "accent_hover",
                },
            )
        )

    # ═══════════════ 子标签页切换栏 ═══════════════

    def _build_music_source_tabs(self):
        tab_bar = ctk.CTkFrame(self._music_tab_content, fg_color="transparent", height=32)
        tab_bar.pack(fill=ctk.X, padx=15, pady=(10, 0))
        tab_bar.pack_propagate(False)
        self._music_source_tab_bar = tab_bar

        btn_cfg = {
            "height": 28,
            "font": ctk.CTkFont(family=FONT_FAMILY, size=12),
            "fg_color": COLORS["bg_light"],
            "hover_color": COLORS["accent"],
        }

        self._music_local_tab_btn = ctk.CTkButton(
            tab_bar, text=_("music_tab_local"), width=100, command=self._music_switch_to_local, **btn_cfg
        )
        self._music_local_tab_btn.pack(side=ctk.LEFT, padx=(0, 4))

        self._music_online_tab_btn = ctk.CTkButton(
            tab_bar, text=_("music_tab_online"), width=100, command=self._music_switch_to_online, **btn_cfg
        )
        self._music_online_tab_btn.pack(side=ctk.LEFT)

        self._music_playlist_tab_btn = ctk.CTkButton(
            tab_bar, text=_("music_tab_playlist"), width=100, command=self._music_switch_to_playlist_tab, **btn_cfg
        )
        self._music_playlist_tab_btn.pack(side=ctk.LEFT, padx=(4, 0))

        self._theme_refs.append((self._music_local_tab_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_online_tab_btn, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_playlist_tab_btn, {"fg_color": "bg_light", "hover_color": "accent"}))

    def _music_switch_to_local(self):
        self._music_tab_mode = "local"
        self._music_online_frame.pack_forget()
        self._music_playlist_frame.pack_forget()
        if self._music_mini_mode:
            self._music_mini_bar.pack(fill=ctk.X, padx=15, pady=(0, 15))
        else:
            self._music_main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)
        self._music_local_tab_btn.configure(fg_color=COLORS["accent"])
        self._music_online_tab_btn.configure(fg_color=COLORS["bg_light"])
        self._music_playlist_tab_btn.configure(fg_color=COLORS["bg_light"])
        self._stop_search_loading()
        # 刷新全部歌曲列表（保留歌单查看状态，返回歌单标签页时恢复）
        self._rebuild_playlist_ui()

    def _music_switch_to_online(self):
        self._music_tab_mode = "online"
        self._music_mini_bar.pack_forget()
        self._music_main_frame.pack_forget()
        self._music_playlist_frame.pack_forget()
        self._music_online_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)
        self._music_online_tab_btn.configure(fg_color=COLORS["accent"])
        self._music_local_tab_btn.configure(fg_color=COLORS["bg_light"])
        self._music_playlist_tab_btn.configure(fg_color=COLORS["bg_light"])

    def _music_switch_to_playlist_tab(self):
        """切换到歌单标签页"""
        self._music_tab_mode = "playlist"
        self._music_mini_bar.pack_forget()
        self._music_main_frame.pack_forget()
        self._music_online_frame.pack_forget()
        self._music_playlist_frame.pack(fill=ctk.BOTH, expand=True)
        self._music_playlist_tab_btn.configure(fg_color=COLORS["accent"])
        self._music_local_tab_btn.configure(fg_color=COLORS["bg_light"])
        self._music_online_tab_btn.configure(fg_color=COLORS["bg_light"])
        self._stop_search_loading()
        self._rebuild_playlist_sidebar()
        # 恢复之前打开的歌单视图（远程歌单或本地歌单），
        # 避免从其它子标签页返回后歌单打开状态被清空
        wy_view_id = self._music_wy_remote_view_id
        if wy_view_id:
            cached = wy_remote.cached_songs(self._music_wy_remote_cache, wy_view_id)
            if cached is not None:
                self._music_render_wy_remote_songs(wy_view_id, cached)
            else:
                self._music_show_wy_remote_loading(wy_view_id)
                self._music_wy_fetch_remote_songs(wy_view_id)
        else:
            pl = self._music_playlist_manager.get_current_playlist()
            if pl is not None:
                self._rebuild_playlist_song_list(pl)

    # ═══════════════ 在线搜索面板 ═══════════════

    def _build_music_online_panel(self):
        # 搜索栏
        search_bar = ctk.CTkFrame(self._music_online_frame, fg_color=COLORS["card_bg"], corner_radius=12)
        search_bar.pack(fill=ctk.X, pady=(0, 10))

        search_inner = ctk.CTkFrame(search_bar, fg_color="transparent")
        search_inner.pack(fill=ctk.X, padx=12, pady=10)

        self._music_search_entry = ctk.CTkEntry(
            search_inner,
            placeholder_text=_("music_search_placeholder"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13),
            fg_color=COLORS["bg_light"],
            border_color=COLORS["card_border"],
            text_color=COLORS["text_primary"],
        )
        self._music_search_entry.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(0, 8))
        self._music_search_entry.bind("<Return>", lambda e: self._music_do_search())

        self._music_search_btn = ctk.CTkButton(
            search_inner,
            text=_("music_search_btn"),
            width=80,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=self._music_do_search,
        )
        self._music_search_btn.pack(side=ctk.LEFT)

        # 音源选择行
        source_row = ctk.CTkFrame(search_bar, fg_color="transparent")
        source_row.pack(fill=ctk.X, padx=12, pady=(0, 8))

        ctk.CTkLabel(
            source_row,
            text=_("music_source_select") + ": ",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT)

        self._music_source_buttons = {}
        for meta in SOURCE_META:
            btn = ctk.CTkButton(
                source_row,
                text=meta["name"],
                width=70,
                height=24,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                fg_color=COLORS["bg_light"],
                hover_color=COLORS["accent"],
                command=lambda s=meta["id"]: self._music_select_source(s),
            )
            btn.pack(side=ctk.LEFT, padx=(4, 0))
            self._music_source_buttons[meta["id"]] = btn
            self._theme_refs.append((btn, {"fg_color": "bg_light", "hover_color": "accent"}))

        self._music_select_source(self._music_selected_source)

        # 网易云百度百科原唱兜底：同步开关状态并注册异步回填回调
        # （查询在后台线程执行，回填后经 after(0,...) 转主线程刷新置顶行）
        wy_src = MUSIC_SOURCES.get("wy")
        if wy_src is not None:
            try:
                from config import config as _cfg

                wy_src.set_baike_enabled(bool(getattr(_cfg, "music_baike_original_enabled", True)))
            except Exception as e:
                logger.warning(f"同步网易云百度百科原唱开关失败: {e}")
            wy_src.set_original_fallback_callback(self._music_original_backfill_cb)

        # 音质选择
        quality_row = ctk.CTkFrame(search_bar, fg_color="transparent")
        quality_row.pack(fill=ctk.X, padx=12, pady=(0, 8))

        ctk.CTkLabel(
            quality_row,
            text=_("music_quality_label") + ": ",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT)

        self._music_quality_var = ctk.StringVar(value="auto")
        for q_text, q_val in [
            (_("music_quality_auto"), "auto"),
            ("128K", "128k"),
            ("320K", "320k"),
            ("FLAC", "flac"),
        ]:
            ctk.CTkRadioButton(
                quality_row,
                text=q_text,
                variable=self._music_quality_var,
                value=q_val,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                fg_color=COLORS["accent"],
                hover_color=COLORS["accent_hover"],
                text_color=COLORS["text_primary"],
            ).pack(side=ctk.LEFT, padx=(8, 0))

        # 桌面歌词按钮
        self._music_dlrc_btn = ctk.CTkButton(
            quality_row,
            text=_("music_desktop_lyric"),
            width=80,
            height=24,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["card_border"],
            command=self._music_toggle_desktop_lyric,
        )
        self._music_dlrc_btn.pack(side=ctk.RIGHT)
        self._theme_refs.append((self._music_dlrc_btn, {"fg_color": "bg_light", "hover_color": "card_border"}))

        # 原唱错标/漏标反馈按钮
        self._music_original_feedback_btn = ctk.CTkButton(
            quality_row,
            text=_("music_original_feedback"),
            height=24,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            fg_color="transparent",
            hover_color=COLORS["card_border"],
            text_color=COLORS["accent"],
            command=lambda: webbrowser.open(MUSIC_ORIGINAL_FEEDBACK_URL),
        )
        self._music_original_feedback_btn.pack(side=ctk.RIGHT, padx=(8, 8))
        self._theme_refs.append(
            (self._music_original_feedback_btn, {"fg_color": "transparent", "hover_color": "card_border"})
        )

        self._theme_refs.append((search_bar, {"fg_color": "card_bg"}))

        # 搜索结果列表
        result_frame = ctk.CTkFrame(self._music_online_frame, fg_color=COLORS["card_bg"], corner_radius=12)
        result_frame.pack(fill=ctk.BOTH, expand=True)
        self._music_online_result_frame = result_frame

        result_header = ctk.CTkFrame(result_frame, fg_color="transparent", height=30)
        result_header.pack(fill=ctk.X, padx=12, pady=(10, 5))
        result_header.pack_propagate(False)

        ctk.CTkLabel(
            result_header,
            text=_("music_playlist"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT)

        self._music_search_status = ctk.CTkLabel(
            result_header, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=COLORS["text_secondary"]
        )
        self._music_search_status.pack(side=ctk.RIGHT)

        self._music_online_scroll = ctk.CTkScrollableFrame(
            result_frame, fg_color="transparent", scrollbar_button_color=COLORS["bg_light"]
        )
        self._music_online_scroll.pack(fill=ctk.BOTH, expand=True, padx=8, pady=(5, 10))
        self._theme_refs.append((self._music_online_scroll, {"scrollbar_button_color": "bg_light"}))
        self._theme_refs.append((result_frame, {"fg_color": "card_bg"}))

        # 分页栏（上一页 / 页码按钮 / 下一页）
        pager_frame = ctk.CTkFrame(result_frame, fg_color="transparent", height=46)
        pager_frame.pack(fill=ctk.X, padx=12, pady=(0, 8))
        pager_frame.pack_propagate(False)
        self._music_pager_frame = pager_frame

        pager_btn_cfg = {
            "height": 24,
            "font": ctk.CTkFont(family=FONT_FAMILY, size=10),
            "fg_color": COLORS["bg_light"],
            "hover_color": COLORS["accent"],
        }

        page_prev_key = "music_page_prev"
        page_prev_text = _(page_prev_key)
        if page_prev_text == page_prev_key:
            page_prev_text = "◀ 上一页"
        self._music_pager_prev = ctk.CTkButton(
            pager_frame, text=page_prev_text, width=78, command=self._music_search_prev_page, **pager_btn_cfg
        )
        self._music_pager_prev.pack(side=ctk.LEFT, padx=(0, 4))

        # 页码按钮横向滚动容器（音源总数多时页数可达上百，超出窗口宽度可横向滚动）
        self._music_pager_page_box = ctk.CTkScrollableFrame(
            pager_frame,
            fg_color="transparent",
            scrollbar_button_color=COLORS["bg_light"],
            orientation="horizontal",
            height=40,
        )
        self._music_pager_page_box.pack(side=ctk.LEFT, fill=ctk.X, expand=True)
        self._theme_refs.append((self._music_pager_page_box, {"scrollbar_button_color": "bg_light"}))

        page_next_key = "music_page_next"
        page_next_text = _(page_next_key)
        if page_next_text == page_next_key:
            page_next_text = "下一页 ▶"
        self._music_pager_next = ctk.CTkButton(
            pager_frame, text=page_next_text, width=78, command=self._music_search_next_page, **pager_btn_cfg
        )
        self._music_pager_next.pack(side=ctk.RIGHT, padx=(4, 0))

        self._music_pager_label = ctk.CTkLabel(
            pager_frame,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        self._music_pager_label.pack(side=ctk.RIGHT, padx=(0, 8))

        self._theme_refs.append((self._music_pager_prev, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_pager_next, {"fg_color": "bg_light", "hover_color": "accent"}))
        self._theme_refs.append((self._music_pager_label, {"text_color": "text_secondary"}))
        self._music_rebuild_pager()

    def _music_select_source(self, source_id: str):
        self._music_selected_source = source_id
        for sid, btn in self._music_source_buttons.items():
            btn.configure(fg_color=COLORS["accent"] if sid == source_id else COLORS["bg_light"])

    def _update_mode_btn_text(self):
        mode_texts = {
            PLAY_MODE_SEQUENTIAL: "➡",
            PLAY_MODE_LOOP_LIST: "🔁",
            PLAY_MODE_LOOP_SINGLE: "🔂",
            PLAY_MODE_RANDOM: "🔀",
        }
        if hasattr(self, "_music_mode_btn") and self._music_mode_btn.winfo_exists():
            self._music_mode_btn.configure(text=mode_texts.get(self._music_play_mode, "🔁"))

    def _update_now_playing_info(self):
        # 取值规则（音质文案怎么拼、标题/歌手/专辑/时长从哪儿取、封面走 URL 还是
        # 内嵌字节）在 services.music_online.now_playing_plan；控件写入、SMTC 上报、
        # 复制按钮状态刷新留在界面侧，分支与原文逐条对应。
        # 原文在非在线分支里调了两次 _get_current_file()，两次之间只有控件
        # configure（不改 _music_playlist / _music_current_index），而
        # _get_current_file 是纯函数，因此合并成一次调用，结果逐字相同。
        is_online = bool(self._music_is_online_playing and self._music_current_online_info)
        local_path = "" if is_online else (self._get_current_file() or "")
        local_meta = self._get_metadata(local_path) if local_path else None
        plan = music_online.now_playing_plan(
            online=is_online,
            online_info=self._music_current_online_info if is_online else None,
            current_quality=getattr(self, "_music_current_quality", "") or "",
            local_path=local_path,
            local_meta=local_meta,
        )
        # 音质标签：在线歌曲按实际获取到的音质档位，本地歌曲按文件真实码率
        self._music_quality_tag.configure(text=plan.quality_text)

        # 在线播放优先
        if plan.mode == music_online.NOW_PLAYING_ONLINE:
            self._music_now_label_top.configure(text=plan.title)
            self._music_now_label_sub.configure(text=plan.sub_text)
            self._music_mini_title.configure(text=plan.mini_text)
            self._music_end_label.configure(text=_format_time(plan.duration))
            self._music_cover_label.configure(text="🎵")
            self._music_cover_artist.configure(text=plan.artist)
            self._music_cover_album.configure(text=plan.album)
            self._music_now_title = plan.title
            self._music_now_artist = plan.artist
            self._music_refresh_copy_btn_state()

            if plan.cover_url:
                self._fetch_and_display_online_cover(plan.cover_url)
            self._music_smtc.update_now_playing(plan.title, plan.artist, plan.album, None)
            return

        if plan.mode == music_online.NOW_PLAYING_EMPTY:
            self._music_now_label_top.configure(text=_("music_no_track"))
            self._music_now_label_sub.configure(text="")
            self._music_cover_label.configure(text="🎵")
            self._music_cover_artist.configure(text="")
            self._music_cover_album.configure(text="")
            self._music_mini_title.configure(text=_("music_no_track"))
            self._music_now_title = ""
            self._music_now_artist = ""
            self._music_refresh_copy_btn_state()
            self._music_progress_bar.set(0)
            self._music_cur_label.configure(text="0:00")
            self._music_end_label.configure(text="0:00")
            return

        self._music_now_label_top.configure(text=plan.title)
        self._music_now_label_sub.configure(text=plan.sub_text)
        self._music_mini_title.configure(text=plan.mini_text)

        self._music_end_label.configure(text=_format_time(plan.duration))

        if plan.cover_bytes:
            self._display_cover(plan.cover_bytes)
        else:
            self._music_cover_label.configure(text="🎵")

        self._music_cover_artist.configure(text=plan.artist if plan.artist else "")
        self._music_cover_album.configure(text=plan.album if plan.album else "")

        self._music_now_title = plan.title
        self._music_now_artist = plan.artist
        self._music_refresh_copy_btn_state()

        self._music_smtc.update_now_playing(plan.title, plan.artist, plan.album, plan.cover_bytes)

    def _music_refresh_copy_btn_state(self):
        """根据当前播放状态启用/禁用歌名、歌手复制按钮"""
        if not (hasattr(self, "_music_copy_title_btn") and hasattr(self, "_music_copy_artist_btn")):
            return
        has_track = bool(self._music_now_title)
        for btn in (self._music_copy_title_btn, self._music_copy_artist_btn):
            if btn.winfo_exists():
                btn.configure(state="normal" if has_track else "disabled")

    def _music_copy_title(self):
        self._music_copy_to_clipboard(self._music_now_title, self._music_copy_title_btn)

    def _music_copy_artist(self):
        self._music_copy_to_clipboard(self._music_now_artist, self._music_copy_artist_btn)

    def _music_copy_to_clipboard(self, text: str, btn: ctk.CTkButton):
        if not text:
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
        except Exception as e:
            logger.debug(f"复制到剪贴板失败: {e}")
            return
        self._music_show_copy_feedback(btn)

    def _music_show_copy_feedback(self, btn: ctk.CTkButton):
        if self._music_copy_feedback_timer:
            try:
                self.after_cancel(self._music_copy_feedback_timer)
            except Exception:
                pass
            self._music_copy_feedback_timer = None
        original = getattr(btn, "_music_copy_orig_text", "") or btn.cget("text")
        btn._music_copy_orig_text = original
        btn.configure(text=_("music_copied"))

        def _restore():
            if btn.winfo_exists():
                btn.configure(text=original)

        self._music_copy_feedback_timer = self.after(1500, _restore)

    def _fetch_and_display_online_cover(self, url: str):
        """异步获取在线封面图并显示

        HTTP 200 判定与「异常一律吞掉」搬进 services.music_online.fetch_cover_bytes
        （零 UI、http_get 可注入）；线程与 after 留在界面侧。
        """
        app = self

        def _fetch():
            data = music_online.fetch_cover_bytes(url)
            if data is not None:
                app.after(0, lambda d=data: app._display_cover(d))

        threading.Thread(target=_fetch, daemon=True).start()

    def _display_cover(self, cover_data: bytes):
        try:
            import io

            from PIL import Image

            image = Image.open(io.BytesIO(cover_data))
            cover_size = (150, 150)
            ctk_image = ctk.CTkImage(light_image=image, dark_image=image, size=cover_size)
            self._music_cover_label.configure(image=ctk_image, text="")
            self._music_cover_label._image = ctk_image
        except Exception:
            self._music_cover_label.configure(text="🎵")

    def _get_current_file(self) -> Optional[str]:
        # 判据搬到 services.music_player.MusicPlayerService.current_file（零 UI）
        return self._music_engine.current_file(self._music_playlist, self._music_current_index)

    def _get_metadata(self, filepath: str) -> dict:
        """曲目元数据（LRU 缓存与解析规则在 services/music_player + services/music_audio）

        缓存对象与引擎共享**同一个 OrderedDict**（见 __init_music），
        所以范围外代码里的 `self._music_metadata_cache.clear()` 依然有效。
        """
        return self._music_engine.get_metadata(filepath)

    def _play_file(self, filepath: str, start_pos: float = 0):
        # pygame 可用性判断与 mixer 三连（load→set_volume(0)→play）由
        # services.music_player 负责；这里的 try **仍然包住"mixer + 状态迁移 + Tk 调用"**，
        # 与原文的异常边界逐字一致（失败同样记日志并把播放态复位）。
        if not self._music_engine.available:
            logger.warning("pygame 不可用，无法播放")
            return
        # 新播放开始：使在途/已完成的歌单预取失效
        self._music_invalidate_prefetch()
        self._music_cancel_fade()
        self._stop_lyric_poll()
        # 应用音效处理
        processed_path = filepath
        if self._music_effects.settings.has_any_enabled:
            try:
                fx_path = self._music_effects.process(filepath)
                if fx_path and fx_path != filepath:
                    self._music_effects_processed_files.append(fx_path)
                    processed_path = fx_path
            except Exception:
                pass
        try:
            self._music_engine.load_and_play(processed_path, start_pos)
            self._music_engine.begin_local_playback(
                filepath, self._get_metadata(filepath).get("duration", 0), start_pos
            )
            self._music_is_playing = True
            self._music_is_paused = False
            # 镜像引擎独占的状态（范围外的旧读者仍按 self._music_xxx 读）
            self._music_seek_offset = self._music_engine.state.seek_offset
            self._music_current_filepath = self._music_engine.state.current_filepath
            self._music_duration = self._music_engine.state.duration
            self._music_is_fading = self._music_engine.state.is_fading
            self._music_fade_out_target = self._music_engine.state.fade_out_target
            self._update_play_btn_ui()
            self._update_now_playing_info()
            self._start_progress_poll()
            self._start_lyric_poll()
            self._music_smtc.set_playing()
            self._highlight_current_in_list()
            self._music_fade_in()
            self._trigger_ach("music_first_play")
            self._trigger_ach("music_play_count")
            self._music_record_play_history_local(filepath)
        except Exception as e:
            logger.error(f"播放失败: {filepath}: {e}")
            self._music_is_playing = False
            self._update_play_btn_ui()

    def _play_online_file(
        self,
        filepath: str,
        online_info: OnlineMusicInfo,
        start_pos: float = 0,
        history_origin: Optional[OnlineMusicInfo] = None,
        quality: str = "",
    ):
        """播放在线缓存的临时文件

        Args:
            filepath: 临时音频文件路径
            online_info: 实际播放的歌曲信息（跨源兜底后可能被替换）
            start_pos: 起始播放位置（秒）
            history_origin: 用户点播的原始歌曲（兜底时历史记录用原歌曲）
            quality: 实际获取到的音质档位（128k/320k/flac，用于显示）
        """
        if not self._music_engine.available:
            return
        # 新播放开始：使在途/已完成的歌单预取失效
        self._music_invalidate_prefetch()
        self._music_cancel_fade()
        self._stop_lyric_poll()
        # 应用音效处理
        processed_path = filepath
        if self._music_effects.settings.has_any_enabled:
            try:
                fx_path = self._music_effects.process(filepath)
                if fx_path and fx_path != filepath:
                    self._music_effects_processed_files.append(fx_path)
                    processed_path = fx_path
            except Exception:
                pass
        try:
            self._music_engine.load_and_play(processed_path, start_pos)
            self._music_engine.begin_online_playback(filepath, online_info.interval, quality, start_pos)
            self._music_is_playing = True
            self._music_is_paused = False
            self._music_is_online_playing = True
            self._music_current_online_info = online_info
            self._music_current_quality = self._music_engine.state.current_quality
            # 镜像引擎独占的状态（范围外的旧读者仍按 self._music_xxx 读）
            self._music_seek_offset = self._music_engine.state.seek_offset
            self._music_current_filepath = self._music_engine.state.current_filepath
            self._music_duration = self._music_engine.state.duration
            self._music_is_fading = self._music_engine.state.is_fading
            self._music_fade_out_target = self._music_engine.state.fade_out_target
            self._update_play_btn_ui()
            self._update_now_playing_info()
            self._start_progress_poll()
            self._fetch_and_start_lyric(online_info)
            self._music_smtc.set_playing()
            self._music_fade_in()
            self._trigger_ach("music_first_play")
            self._trigger_ach("music_play_count")
            # 播放历史记录用户点播的原始歌曲（兜底播放时记录的也是原歌曲）
            self._music_record_play_history_online(history_origin or online_info)
        except Exception as e:
            logger.error(f"在线播放失败: {e}")
            self._music_is_playing = False
            self._music_is_online_playing = False
            self._update_play_btn_ui()

    def _music_cancel_fade(self):
        # after_cancel 属于界面侧（服务不调 after）；淡入淡出的状态复位在服务里
        if self._music_fade_timer_id is not None:
            self.after_cancel(self._music_fade_timer_id)
            self._music_fade_timer_id = None
        self._music_engine.cancel_fade()
        self._music_is_fading = self._music_engine.state.is_fading
        self._music_fade_out_target = self._music_engine.state.fade_out_target

    def _music_fade_in(self, step: int = 0):
        """淡入一步

        步进数值与状态迁移由 services.music_player.fade_in_step 计算
        （"下一步音量是多少 / 是否结束 / 是否取消"），`after` 定时器留在界面侧。
        """
        decision = self._music_engine.fade_in_step(
            step, self._music_volume, self._music_is_playing, self._music_is_paused
        )
        if decision.cancel:
            self._music_cancel_fade()
            return
        if decision.volume is not None:
            self._music_engine.set_mixer_volume(decision.volume)
        self._music_is_fading = decision.fading
        if decision.schedule_next:
            self._music_fade_timer_id = self.after(FADE_INTERVAL_MS, lambda: self._music_fade_in(step + 1))
        else:
            self._music_fade_timer_id = None

    def _music_fade_out(self, step: int = 0):
        """淡出一步

        数值与状态由 services.music_player.fade_out_step 计算；走完最后一步时
        `finished=True`，界面接着执行 `_music_execute_fade_out_target()`。
        """
        decision = self._music_engine.fade_out_step(
            step, self._music_volume, self._music_is_playing, self._music_is_paused
        )
        if decision.cancel:
            self._music_cancel_fade()
            return
        if decision.volume is not None:
            self._music_engine.set_mixer_volume(decision.volume)
        self._music_is_fading = decision.fading
        if decision.schedule_next:
            self._music_fade_timer_id = self.after(FADE_INTERVAL_MS, lambda: self._music_fade_out(step + 1))
        else:
            self._music_fade_timer_id = None
        if decision.finished:
            self._music_execute_fade_out_target()

    def _music_execute_fade_out_target(self):
        # 目标值住在引擎里；take 即"取走并清空"，语义与原文一致
        target = self._music_engine.take_fade_out_target()
        self._music_fade_out_target = None
        if target == FADE_OUT_PAUSE:
            self._music_engine.pause_mixer()
            self._music_is_playing = False
            self._music_is_paused = True
            self._update_play_btn_ui()
            self._music_smtc.set_paused()
            self._music_engine.set_mixer_volume(self._music_volume)
        elif target == FADE_OUT_STOP:
            self._music_engine.stop_mixer()
            self._music_is_playing = False
            self._music_is_paused = False
            self._music_progress = 0
            self._music_engine.reset_seek_offset()
            self._music_seek_offset = self._music_engine.state.seek_offset
            self._update_play_btn_ui()
            self._music_progress_bar.set(0)
            self._music_cur_label.configure(text="0:00")
            self._music_smtc.set_stopped()
            self._music_engine.set_mixer_volume(self._music_volume)

    def _music_toggle_play(self):
        # 分支判定（哪一种"非法/该走哪条路"）搬进 services.music_player.toggle_play_action；
        # mixer 与 Tk 调用留在界面侧，分支顺序与原文逐条对应。
        action = self._music_engine.toggle_play_action(
            is_playing=self._music_is_playing,
            is_paused=self._music_is_paused,
            has_playlist=bool(self._music_playlist),
            is_online_playing=self._music_is_online_playing,
            has_online_info=bool(self._music_current_online_info),
            current_index=self._music_current_index,
            is_fading=self._music_is_fading,
        )
        if action == "ignore":
            return
        if action == "replay_online":
            # 重播当前在线歌曲
            self._music_play_online_url(self._music_current_online_info)
            return
        if action == "play_local":
            if self._music_current_index < 0:
                self._music_current_index = 0
            self._play_file(
                self._music_playlist[self._music_current_index], self._music_progress if self._music_progress > 0 else 0
            )
        elif action == "ignore_fading":
            return
        elif action == "resume":
            try:
                self._music_engine.resume_mixer()
                self._music_is_playing = True
                self._music_is_paused = False
                self._update_play_btn_ui()
                self._start_progress_poll()
                self._start_lyric_poll()
                self._music_smtc.set_playing()
                self._music_fade_in()
            except Exception as e:
                logger.error(f"恢复播放失败: {e}")
        elif action == "fade_out_pause":
            self._music_engine.begin_fade_out(FADE_OUT_PAUSE)
            self._music_fade_out_target = self._music_engine.state.fade_out_target
            self._stop_progress_poll()
            self._stop_lyric_poll()
            self._music_fade_out()

    def _music_stop(self, instant: bool = False):
        if not self._music_engine.available:
            return
        # 停止/切换播放：在途与已完成的歌单预取全部失效
        self._music_invalidate_prefetch()
        self._music_cancel_fade()
        if not instant and self._music_is_playing and not self._music_is_paused:
            self._music_engine.begin_fade_out(FADE_OUT_STOP)
            self._music_fade_out_target = self._music_engine.state.fade_out_target
            self._stop_progress_poll()
            self._stop_lyric_poll()
            self._music_fade_out()
            return
        # mixer stop + unload 由服务负责（内部吞异常，与原文 try/except pass 等价）
        self._music_engine.stop_mixer()
        self._music_is_playing = False
        self._music_is_paused = False
        self._music_is_online_playing = False
        self._music_current_online_info = None
        self._music_progress = 0
        self._music_engine.reset_seek_offset()
        self._music_seek_offset = self._music_engine.state.seek_offset
        self._stop_progress_poll()
        self._stop_lyric_poll()
        self._update_play_btn_ui()
        self._music_progress_bar.set(0)
        self._music_cur_label.configure(text="0:00")
        self._music_smtc.set_stopped()

    def _music_invalidate_prefetch(self):
        """新播放开始/停止：使在途与已完成的歌单预取全部失效

        序号/槽位/闸门三个状态住在 services.music_player（本轮范围内独占），
        界面侧属性只做镜像，供旧读者使用。
        """
        self._music_engine.invalidate_prefetch()
        self._music_prefetch_seq = self._music_engine.state.prefetch_seq
        self._music_prefetch_slot = self._music_engine.state.prefetch_slot
        self._music_prefetch_started = self._music_engine.state.prefetch_started

    def _music_maybe_prefetch_next(self):
        """当前歌曲播放过半时，后台预取歌单中的下一首在线歌曲（放完秒播）

        仅预取在线歌曲（本地文件加载快，无需预取）；预取为尽力而为，
        未完成/失败时播完仍走常规按需加载流程，不影响原行为。
        """
        # 判定（含"每首只触发一次"的闸门与随机索引）在 services.music_player.plan_prefetch；
        # **线程仍由界面侧起**，音质变量也在主线程读（后台线程绝不触碰 Tk 变量）。
        plan = self._music_engine.plan_prefetch(
            self._music_playlist_context_songs,
            self._music_playlist_context_idx,
            self._music_play_mode,
            self._music_duration,
            self._music_progress,
        )
        self._music_prefetch_seq = self._music_engine.state.prefetch_seq
        self._music_prefetch_started = self._music_engine.state.prefetch_started
        if plan is None:
            return
        # 主线程读取用户音质偏好（后台线程绝不触碰 Tk 变量）
        raw_quality = self._music_quality_var.get()
        threading.Thread(
            target=self._music_prefetch_worker,
            args=(plan.seq, plan.index, plan.song, raw_quality),
            daemon=True,
        ).start()

    def _music_prefetch_worker(self, seq: int, idx: int, song: PlaylistSong, raw_quality: str):
        """后台线程：预取歌单下一首（绝不触碰 Tk，结果经队列回主线程）"""
        event = ("prefetch_fail", seq)
        try:
            from ui.music_source.base import MusicInfo

            info = MusicInfo(
                name=song.online_name,
                singer=song.online_singer,
                source=song.online_source,
                songmid=song.online_songmid,
                album_name=song.online_album,
                interval=song.online_interval,
                img=song.online_img,
                types=[dict(t) for t in (song.online_types or [])],
                _types=dict(song.online_type_detail or {}),
            )
            play_info, play_quality = info, raw_quality
            if raw_quality == "auto":
                # 与按需播放同一流程：解析出具体音质（含 types 缺失时的搜索补齐），
                # 避免 "auto" 透传到下载层导致音质标签为空且降级到 128k
                play_info, play_quality = self._music_resolve_auto_quality_async(info)
            result_path, result_info, result_quality = self._fetch_online_song(play_info, play_quality, raw_quality)
            if result_path and os.path.exists(result_path):
                event = ("prefetch", seq, idx, result_path, result_info, info, result_quality)
        except Exception as e:
            logger.debug(f"预取歌单下一首异常: {e}")
        try:
            self._music_wy_queue.put(event)
        except Exception:
            pass

    def _music_on_prefetch_ready(self, seq, idx, temp_path, result_info, origin_info, quality):
        """预取下载完成（主线程）：序号仍有效则存入预取槽位，否则丢弃临时文件

        槽位结构与"序号失效即丢弃"的判定搬到 services.music_player.accept_prefetch
        （临时文件清理 `_discard_temp_file` 仍属界面侧）。
        """
        if not self._music_engine.accept_prefetch(seq, idx, temp_path, result_info, origin_info, quality):
            self._discard_temp_file(temp_path)
            return
        self._music_prefetch_slot = self._music_engine.state.prefetch_slot

    def _play_playlist_context_song(self, idx: int):
        """播歌单上下文中指定索引的歌曲（支持本地/在线混合）

        "该播哪一首"的解析（越界判定、本地文件缺失、本地路径列表与索引、
        预取槽位六条件校验）搬进 services.music_player.resolve_context_target；
        高亮、递归跳歌与 mixer 播放留在界面侧。
        """
        songs = self._music_playlist_context_songs
        target = self._music_engine.resolve_context_target(songs, idx, self._music_prefetch_slot)
        if target is None:
            return
        song = target.song
        self._music_playlist_context_idx = target.index
        self._highlight_playlist_song(target.index)
        if target.kind == "local_missing":
            # 跳过不存在的本地文件，播下一首
            self._play_playlist_context_song((target.index + 1) % len(songs))
            return
        if target.kind == "local":
            # 构建本地文件列表供 _play_file 使用
            self._music_playlist = target.local_paths
            self._music_current_index = target.local_index
            self._music_progress = 0
            self._play_file(song.file_path)
        else:
            # 优先消费预取结果（序号 + 索引 + 歌曲指纹 + 文件存在校验），放完秒播
            slot = target.prefetched
            if slot is not None:
                self._music_engine.consume_prefetch_slot()
                self._music_prefetch_slot = self._music_engine.state.prefetch_slot
                # 使在途的按需下载结果失效（防旧线程覆盖本次预取播放）
                self._music_stream_seq += 1
                self._play_online_file(
                    slot["temp_path"],
                    slot["result_info"],
                    0,
                    history_origin=slot["origin_info"],
                    quality=slot["quality"],
                )
                return
            from ui.music_source.base import MusicInfo

            info = MusicInfo(
                name=song.online_name,
                singer=song.online_singer,
                source=song.online_source,
                songmid=song.online_songmid,
                album_name=song.online_album,
                interval=song.online_interval,
                img=song.online_img,
                # 恢复加入歌单时的可用音质信息（自动音质解析与 URL 获取依赖）
                types=[dict(t) for t in (song.online_types or [])],
                _types=dict(song.online_type_detail or {}),
            )
            self._music_play_online_url(info)

    def _highlight_playlist_song(self, target_idx: int):
        """高亮歌单歌曲列表中指定索引的行"""
        # 网易云远程歌单（分页）：目标歌曲不在当前页时自动翻页
        if getattr(self, "_music_wy_remote_view_id", None):
            if target_idx < 0:
                return
            page = target_idx // _MUSIC_WY_PAGE_SIZE + 1
            if page != self._music_wy_remote_page and self._music_wy_remote_view_songs:
                self._music_wy_remote_page = page
                self._music_render_wy_remote_songs(
                    self._music_wy_remote_view_id, self._music_wy_remote_view_songs
                )
            for w in self._music_playlist_widgets:
                try:
                    f = w.get("frame")
                    if not f or not f.winfo_exists():
                        continue
                    if w.get("real_index") == target_idx:
                        f.configure(fg_color=COLORS["accent"])
                    else:
                        f.configure(fg_color="transparent")
                except Exception:
                    pass
            return
        for w in self._music_playlist_widgets:
            try:
                f = w.get("frame")
                if not f or not f.winfo_exists():
                    continue
                if w.get("index") == target_idx:
                    f.configure(fg_color=COLORS["accent"])
                else:
                    f.configure(fg_color="transparent")
            except Exception:
                pass

    def _music_prev(self):
        # 索引决策（含随机模式"不重复当前首"）搬进 services.music_player.prev_index
        if self._music_playlist_context_songs:
            n = len(self._music_playlist_context_songs)
            if n == 0:
                return
            self._play_playlist_context_song(
                self._music_engine.prev_index(self._music_play_mode, n, self._music_playlist_context_idx)
            )
            return
        if not self._music_playlist:
            return
        self._music_current_index = self._music_engine.prev_index(
            self._music_play_mode, len(self._music_playlist), self._music_current_index
        )
        self._music_progress = 0
        self._play_file(self._music_playlist[self._music_current_index])

    def _music_next(self):
        # 索引决策搬进 services.music_player：
        #  - 歌单上下文用 next_context_index（含"预取已就绪就直接切过去"的判定：
        #    序号有效 + 索引合法 + 歌曲指纹一致，随机模式无需重新掷骰）；
        #  - 普通歌单用 next_index。
        if self._music_playlist_context_songs:
            n = len(self._music_playlist_context_songs)
            if n == 0:
                return
            self._play_playlist_context_song(
                self._music_engine.next_context_index(
                    self._music_playlist_context_songs,
                    self._music_playlist_context_idx,
                    self._music_play_mode,
                    self._music_prefetch_slot,
                    self._music_prefetch_seq,
                )
            )
            return
        if not self._music_playlist:
            return
        self._music_current_index = self._music_engine.next_index(
            self._music_play_mode, len(self._music_playlist), self._music_current_index
        )
        self._music_progress = 0
        self._play_file(self._music_playlist[self._music_current_index])

    def _music_seek(self, value: float):
        if not self._music_is_playing and not self._music_is_paused:
            return
        if not self._music_current_filepath:
            return
        self._music_cancel_fade()
        try:
            pos = self._music_engine.seek_seconds(value, self._music_duration)
            was_paused = self._music_is_paused
            # 重载文件到指定位置（set_pos 不重置 get_pos 计时器，必须 reload）——
            # mixer 四行搬进 services.music_player.reload_and_seek，异常边界不变
            self._music_engine.reload_and_seek(pos, self._music_current_filepath, was_paused)
            self._music_progress = pos
            self._music_engine.state.seek_offset = pos
            self._music_seek_offset = self._music_engine.state.seek_offset
            if was_paused:
                self._stop_progress_poll()
            else:
                self._start_progress_poll()
                self._music_fade_in()
        except Exception:
            pass

    def _music_set_volume(self, value: float):
        # "pygame 可用 + 不在淡入淡出中才设音量"这条判据在服务侧
        # （set_mixer_volume 内部吞异常，与原文的 try/except pass 等价）
        self._music_volume = value / 100.0
        if self._music_engine.available and not self._music_is_fading:
            self._music_engine.set_mixer_volume(self._music_volume)
        self._update_mute_btn_ui()

    def _music_toggle_mute(self):
        # 分支与"静音前音量"的记忆规则搬进 services.music_player.toggle_mute
        # （原文 getattr(self, "_music_vol_before_mute", 0.7) 的默认值 = 引擎里的初值）
        result = self._music_engine.toggle_mute(self._music_volume)
        self._music_volume = result.volume
        if result.remember is not None:
            self._music_vol_before_mute = result.remember
        if self._music_volume > 0:
            self._music_vol_slider.set(volume_to_slider(self._music_volume))
            self._music_mini_vol.set(volume_to_slider(self._music_volume))
        else:
            self._music_vol_slider.set(0)
            self._music_mini_vol.set(0)
        if self._music_engine.available and not self._music_is_fading:
            self._music_engine.set_mixer_volume(self._music_volume)
        self._update_mute_btn_ui()
        self._trigger_ach("music_volume_tweaker")

    def _update_mute_btn_ui(self):
        if self._music_volume == 0:
            self._music_mute_btn.configure(text="🔇")
        else:
            self._music_mute_btn.configure(text="🔊")

    def _update_play_btn_ui(self):
        if self._music_is_playing and not self._music_is_paused:
            self._music_play_btn.configure(text="⏸")
            self._music_mini_play.configure(text="⏸")
        else:
            self._music_play_btn.configure(text="▶")
            self._music_mini_play.configure(text="▶")
        self._update_music_footer()

    def _start_progress_poll(self):
        self._stop_progress_poll()
        self._poll_music_progress()

    def _stop_progress_poll(self):
        if self._music_progress_timer_id is not None:
            self.after_cancel(self._music_progress_timer_id)
            self._music_progress_timer_id = None

    def _poll_music_progress(self):
        # 进度换算（秒数 / 百分比）搬进 services.music_player 的纯函数；
        # `after` 定时器与控件更新留在界面侧。
        # 注意 `is_music_busy()` 在 mixer 不可用时返回 **None**：原文那一行会抛异常
        # 并被下面的 try 吞掉（于是"曲目结束"分支不会被走到），用 `is False` 复现同一语义。
        if not self._music_is_playing or self._music_is_paused:
            self._stop_progress_poll()
            return
        if not self._is_music_tab_active():
            self._music_progress_timer_id = self.after(PROGRESS_POLL_IDLE_MS, self._poll_music_progress)
            return
        try:
            if self._music_engine.is_music_busy():
                pos = self._music_engine.poll_position(self._music_seek_offset)
                if pos is not None:
                    self._music_progress = pos
                    cur_text = _format_time(pos)
                    self._music_cur_label.configure(text=cur_text)
                    pct = self._music_engine.progress_percent(pos, self._music_duration)
                    if pct is not None:
                        self._music_progress_bar.set(pct)
                    # 播放过半时后台预取歌单下一首（放完秒播）
                    self._music_maybe_prefetch_next()
            if self._music_engine.is_music_busy() is False and self._music_is_playing:
                self._on_track_end()
        except Exception:
            pass
        self._music_progress_timer_id = self.after(PROGRESS_POLL_MS, self._poll_music_progress)

    # ═══════════════ 歌词轮询 ═══════════════

    def _start_lyric_poll(self):
        self._stop_lyric_poll()
        self._poll_lyric_progress()

    def _stop_lyric_poll(self):
        if self._music_lyric_poll_id is not None:
            self.after_cancel(self._music_lyric_poll_id)
            self._music_lyric_poll_id = None

    def _poll_lyric_progress(self):
        # 「该不该停」搬进 services.music_lyric_display.should_stop_polling，
        # 「降频还是常速 + 本次显示第几毫秒」搬进 poll_plan。
        # 播放态判定放在最前面：原文在这里就短路返回，不会在白停的时候
        # 去读一次 Tk 状态（is_visible 内部要查 winfo_exists），这里保持同序。
        if music_lyric_display.should_stop_polling(
            is_playing=self._music_is_playing, is_paused=self._music_is_paused
        ):
            self._stop_lyric_poll()
            return
        plan = music_lyric_display.poll_plan(
            tab_active=self._is_music_tab_active(),
            desktop_visible=bool(self._music_desktop_lyric and self._music_desktop_lyric.is_visible),
            progress=self._music_progress,
        )
        try:
            if plan.elapsed_ms is not None:
                self._update_lyric_display(plan.elapsed_ms)
                if self._music_desktop_lyric and self._music_desktop_lyric.is_visible:
                    self._music_desktop_lyric.update_progress(plan.elapsed_ms)
        except Exception:
            pass
        self._music_lyric_poll_id = self.after(plan.delay_ms, self._poll_lyric_progress)

    def _update_lyric_display(self, elapsed_ms: int):
        """更新内嵌歌词显示"""
        if not hasattr(self, "_lyric_current_label") or not self._lyric_current_label:
            return
        # 「取哪一行 + 副文本取翻译还是罗马音」搬进 services.music_lyric_display.display_plan。
        # 没有当前行时服务给两段空串，与原文"两个标签都置空"的分支等价。
        plan = music_lyric_display.display_plan(
            self._music_lyric_parser,
            elapsed_ms,
            show_translation=self._music_show_lyric_translation,
            show_roma=self._music_show_lyric_roma,
        )
        self._lyric_current_label.configure(text=plan.text)
        if hasattr(self, "_lyric_trans_label"):
            self._lyric_trans_label.configure(text=plan.trans_text)

    def _fetch_and_start_lyric(self, online_info: OnlineMusicInfo):
        """获取歌词并开始解析"""
        app = self

        def _fetch():
            try:
                # 「音源查找 + 空歌词判定 + 解析」搬进 services.music_lyric_display.load_lyric；
                # 音源表由界面传进去，`MUSIC_SOURCES` 在本模块仍是原来的补丁点。
                # 外层 try 也逐字保留：原文连 app.after 抛出的异常一并吞掉。
                loaded = music_lyric_display.load_lyric(
                    app._music_lyric_parser, online_info, MUSIC_SOURCES
                )
                if not loaded:
                    return
                app.after(0, app._start_lyric_poll)
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _is_music_tab_active(self):
        # 阶段 1.23（D-100）修正：原实现比较 self.tabview.get() 与 _("tab_music")，
        # 而 _() 是在调用时重算的 —— 设置窗口切换语言会立刻改全局翻译状态却不重建
        # 界面，于是控件标题仍是旧语言文本、与重算结果不相等，该判断恒为 False
        # （依赖它的播放页逻辑静默失效）。改用 app_base 提供的稳定标识。
        try:
            return self.current_tab_id() == "music"
        except Exception:
            return False

    def _on_track_end(self):
        self._music_is_playing = False
        self._stop_progress_poll()
        self._stop_lyric_poll()
        if self._music_is_online_playing:
            self._music_is_online_playing = False
            self._music_current_online_info = None
            # 如果正在播歌单中的在线歌曲，自动切到下一首
            if self._music_playlist_context_songs:
                self._music_next()
                return
            self._update_play_btn_ui()
            self._music_engine.reset_seek_offset()
            self._music_seek_offset = self._music_engine.state.seek_offset
            self._music_progress_bar.set(0)
            self._music_cur_label.configure(text="0:00")
            self._music_smtc.set_stopped()
            return
        # "下一首是哪一首"的决策（顺序/列表循环/单曲循环/随机）搬进
        # services.music_player.track_end_action；播放动作仍留界面侧。
        # 记录现状、疑为缺陷：歌单为空时原文的算术本身就抛（LOOP_LIST 的
        # `% 0`、RANDOM 的 randrange(0)、LOOP_SINGLE 的空列表下标），
        # 异常被 _poll_music_progress 的 except 吞掉 → 表现为"静默无动作"。
        # 服务照抄同一套算术，不做额外兜底。
        action = self._music_engine.track_end_action(
            self._music_play_mode, self._music_current_index, len(self._music_playlist)
        )
        if action.kind == "replay":
            self._play_file(self._music_playlist[self._music_current_index])
        elif action.kind == "next":
            self._music_current_index = action.index
            self._music_progress = 0
            self._play_file(self._music_playlist[self._music_current_index])
        elif action.kind == "stop":
            self._update_play_btn_ui()
            self._music_engine.reset_seek_offset()
            self._music_seek_offset = self._music_engine.state.seek_offset
            self._music_progress_bar.set(0)
            self._music_cur_label.configure(text="0:00")
            self._music_smtc.set_stopped()

    def _music_cycle_mode(self):
        # 模式语义（PLAY_MODE_* / 循环顺序 / "集齐 4 种"判据）以 services/music_player 为准
        self._music_play_mode = self._music_engine.cycle_mode(self._music_play_mode)
        self._update_mode_btn_text()
        # _music_modes_used 就是引擎里的那个 set（见 __init_music），故此处无需再同步
        self._music_modes_used.add(self._music_play_mode)
        if len(self._music_modes_used) >= ALL_PLAY_MODES:
            self._check_ach("music_mode_master", True)

    def _music_toggle_mini_mode(self):
        self._music_mini_mode = not self._music_mini_mode
        if self._music_mini_mode:
            self._music_main_frame.pack_forget()
            self._music_mini_bar.pack(fill=ctk.X, padx=15, pady=(0, 15))
            self._music_mini_toggle_btn.configure(text=_("music_expand"))
            self._trigger_ach("music_mini_mode")
        else:
            self._music_mini_bar.pack_forget()
            self._music_main_frame.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)
            self._music_mini_toggle_btn.configure(text=_("music_mini_mode"))
        self._save_music_state_later()

    def _music_open_folder(self):
        folder = filedialog.askdirectory(title=_("music_select_folder"))
        if not folder:
            return
        self._music_scan_folder(folder)

    def _music_scan_folder(self, folder: str):
        # 文件枚举与排序（含"异常/空目录都直接返回"）搬进 services.music_player.scan_folder
        files = self._music_engine.scan_folder(folder, AUDIO_EXTENSIONS)
        if not files:
            return
        self._music_stop()
        self._music_playlist = files
        self._music_current_index = -1
        self._music_playlist_context_songs = []  # 退出歌单上下文
        self._music_playlist_context_idx = -1
        self._music_last_folder = folder
        self._music_metadata_cache.clear()
        self._music_folder_label.configure(text=os.path.basename(folder) or folder)
        count_text = _("music_song_count", count=len(files))
        if count_text == "music_song_count":
            count_text = f"{len(files)} 首"
        self._music_song_count_label.configure(count_text)
        self._rebuild_playlist_ui()
        self._save_music_state_later()

    def _rebuild_playlist_ui(self):
        for w in self._music_playlist_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_playlist_widgets.clear()
        for idx, filepath in enumerate(self._music_playlist):
            self._add_playlist_row(idx, filepath)
        self._highlight_current_in_list()

    def _add_playlist_row(self, idx: int, filepath: str):
        # 「歌名 - 歌手」与时长文案的拼装（含 50 字符截断）搬进
        # services.music_local.playlist_row_plan
        plan = music_local.playlist_row_plan(filepath, self._get_metadata(filepath))

        row = ctk.CTkFrame(self._music_scroll, fg_color="transparent", height=32)
        row.pack(fill=ctk.X, pady=1)

        index_label = ctk.CTkLabel(
            row,
            text=str(idx + 1),
            width=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        index_label.pack(side=ctk.LEFT)

        name_label = ctk.CTkLabel(
            row,
            text=plan.name_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        name_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(5, 5))

        if plan.dur_text:
            dur_label = ctk.CTkLabel(
                row,
                text=plan.dur_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["text_secondary"],
                width=35,
            )
            dur_label.pack(side=ctk.RIGHT)

        # 添加到歌单按钮
        add_btn = ctk.CTkButton(
            row,
            text="➕",
            width=22,
            height=22,
            font=ctk.CTkFont(size=9),
            fg_color="transparent",
            hover_color=COLORS["accent"],
            text_color=COLORS["text_secondary"],
            command=lambda fp=filepath: self._music_add_to_playlist_menu(fp, is_online=False),
        )
        add_btn.pack(side=ctk.RIGHT, padx=(0, 2))

        for child in [row, index_label, name_label]:
            child.bind("<Button-1>", lambda e, i=idx: self._play_from_index(i))
            child.bind("<Double-Button-1>", lambda e, i=idx: self._play_from_index(i))

        self._music_playlist_widgets.append({"frame": row, "name_label": name_label, "index": idx})

    def _play_from_index(self, idx: int):
        # 越界判定与取值搬进 services.music_local.play_index_target
        # （返回 None = 索引非法）
        filepath = music_local.play_index_target(self._music_playlist, idx)
        if filepath is None:
            return
        self._music_current_index = idx
        self._music_progress = 0
        self._play_file(filepath)
        self._save_music_state_later()

    def _highlight_current_in_list(self):
        for w in self._music_playlist_widgets:
            try:
                f = w.get("frame")
                if not f or not f.winfo_exists():
                    continue
                label = w.get("name_label")
                if w.get("index") == self._music_current_index:
                    f.configure(fg_color=COLORS["accent"])
                    if label:
                        label.configure(text_color=COLORS["text_primary"])
                else:
                    f.configure(fg_color="transparent")
                    if label:
                        label.configure(text_color=COLORS["text_primary"])
            except Exception:
                pass

    # ═══════════════ 歌单管理 ─────────────────────

    # ── 弹出菜单关闭逻辑 ─────────────────────────────
    #
    # Windows 下 overrideredirect 窗口的 focus_force() 并不可靠：焦点请求
    # 可能失败并立刻补发 <FocusOut>，若菜单绑定 <FocusOut> 销毁自身，就会
    # 出现"菜单一闪而过"。因此弹出菜单不再依赖 <FocusOut>，改为：
    #   - 主窗口任意位置按下鼠标左/右键时关闭当前菜单
    #   - 菜单获得焦点时按 Escape 关闭
    #   - 超时（5 秒）自动关闭

    def _music_init_popup_menu_dismiss(self):
        """主窗口只绑定一次：点击任意位置时关闭正在弹出的菜单（不覆盖现有绑定）"""
        if getattr(self, "_music_popup_menu_dismiss_bound", False):
            return
        self._music_popup_menu_dismiss_bound = True
        self._music_popup_menu = None
        self.bind("<Button-1>", self._music_close_open_popup_menu, add="+")
        self.bind("<Button-3>", self._music_close_open_popup_menu, add="+")

    def _music_close_open_popup_menu(self, _event=None):
        """关闭当前弹出的菜单（若存在）"""
        menu = getattr(self, "_music_popup_menu", None)
        if menu is None:
            return False
        self._music_popup_menu = None
        try:
            menu.destroy()
        except Exception:
            pass
        return True

    def _music_open_popup_menu(self, menu):
        """登记弹出菜单并绑定关闭方式

        菜单是独立 Toplevel，其自身的点击事件不会冒泡到主窗口绑定；
        主窗口的点击（左键/右键）会触发 _music_close_open_popup_menu。
        """
        self._music_init_popup_menu_dismiss()
        self._music_close_open_popup_menu()  # 先关闭可能残留的菜单
        # 延迟登记：打开菜单的那次鼠标事件仍在派发中，不能让它立刻关闭自己
        self.after(1, lambda: setattr(self, "_music_popup_menu", menu))

        def _close_menu(e=None):
            self._music_close_open_popup_menu()

        menu.bind("<Escape>", _close_menu)
        menu.after(5000, _close_menu)

    def _rebuild_playlist_sidebar(self):
        """重建左侧歌单侧边栏"""
        if not hasattr(self, "_music_playlist_sidebar"):
            return
        # 清除旧组件
        for w in self._music_playlist_sidebar_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_playlist_sidebar_widgets.clear()

        # ── "全部歌曲" 与用户歌单条目 ──
        # 条目文案（系统歌单的图标前缀 + 歌曲数）与"当前选中"判据搬进
        # services.music_local.sidebar_plan；这里只剩控件生命周期。
        plan = music_local.sidebar_plan(self._music_playlist_manager, _("music_all_songs"))
        self._music_playlist_sidebar_widgets.append(
            self._build_sidebar_item(plan.all_songs.playlist_id, plan.all_songs.text)
        )

        # 分隔线
        sep = ctk.CTkFrame(self._music_playlist_sidebar, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, padx=12, pady=4)
        self._music_playlist_sidebar_widgets.append({"frame": sep})

        # ── 用户歌单列表 ──
        for item in plan.playlists:
            self._music_playlist_sidebar_widgets.append(
                self._build_sidebar_item(item.playlist_id, item.text, is_active=item.is_active)
            )

        # ── 网易云账号歌单（只读同步，不落盘，禁止编辑） ──
        self._music_rebuild_wy_remote_sidebar()

        # 高亮当前选中
        self._highlight_sidebar_selection()

    def _music_rebuild_wy_remote_sidebar(self):
        """重建侧边栏「网易云歌单」分组（只读，无右键菜单）"""
        remote = self._music_wy_remote_playlists
        if not remote:
            return
        # 分隔线
        sep = ctk.CTkFrame(self._music_playlist_sidebar, fg_color=COLORS["card_border"], height=1)
        sep.pack(fill=ctk.X, padx=12, pady=4)
        self._music_playlist_sidebar_widgets.append({"frame": sep})
        # 分组标题（最近一次同步失败时附加提示）
        # 括号后缀的拼法搬进 services.music_wy_remote.header_title（服务不做 i18n，
        # 两个文案由这里用 _() 取好传进去）
        title = wy_remote.header_title(
            _("music_wy_playlists"), _("music_wy_sync_failed"), self._music_wy_sync_failed
        )
        header = ctk.CTkLabel(
            self._music_playlist_sidebar,
            text=title,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=COLORS["text_secondary"],
        )
        header.pack(anchor=ctk.W, padx=10, pady=(4, 2))
        self._music_playlist_sidebar_widgets.append({"frame": header})
        # 远程歌单条目（只读：不绑定右键菜单，禁止编辑）
        # 条目 id 的前缀（remote_key）与显示文案（entry_label）都在
        # services.music_wy_remote 里 —— 1.4-B 留的入口，本轮接上调用方。
        for item in wy_remote.sidebar_items(remote, self._music_wy_remote_view_id):
            self._music_playlist_sidebar_widgets.append(
                self._build_sidebar_item(item.playlist_id, item.text, is_active=item.is_active)
            )

    def _build_sidebar_item(
        self, playlist_id: Optional[str], text: str, is_active: bool = False
    ) -> dict:
        """构建单个侧边栏条目

        playlist_id 以 _WY_REMOTE_PREFIX 开头时为网易云远程歌单条目
        （只读查看，无右键菜单，禁止编辑）。
        """
        active_bg = COLORS["bg_light"]
        normal_bg = "transparent"
        frame = ctk.CTkFrame(
            self._music_playlist_sidebar, fg_color=active_bg if is_active else normal_bg, corner_radius=6, height=30
        )
        frame.pack(fill=ctk.X, pady=1)
        frame.pack_propagate(False)

        label = ctk.CTkLabel(
            frame,
            text=text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        label.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=10)

        ctx = {"frame": frame, "label": label, "playlist_id": playlist_id}

        # 点击切换到歌单
        click_targets = [frame, label]
        if playlist_id is None:
            # 全部歌曲 → 切换到本地标签页
            for t in click_targets:
                t.bind("<Button-1>", lambda e: self._music_switch_to_local())
                t.bind("<Double-Button-1>", lambda e: self._music_switch_to_local())
        elif playlist_id.startswith(_WY_REMOTE_PREFIX):
            # 远程歌单条目：只读查看，不绑定右键菜单（禁止编辑）
            real_id = playlist_id[len(_WY_REMOTE_PREFIX):]
            for t in click_targets:
                t.bind("<Button-1>", lambda e, pid=real_id: self._music_show_wy_remote_playlist(pid))
                t.bind("<Double-Button-1>", lambda e, pid=real_id: self._music_show_wy_remote_playlist(pid))
        else:
            for t in click_targets:
                t.bind("<Button-1>", lambda e, pid=playlist_id: self._music_show_playlist(pid))
                t.bind("<Double-Button-1>", lambda e, pid=playlist_id: self._music_show_playlist(pid))
                # 右键菜单（仅用户歌单）
                t.bind("<Button-3>", lambda e, pid=playlist_id: self._music_show_playlist_context_menu(e, pid))

        return ctx

    def _highlight_sidebar_selection(self):
        """高亮侧边栏当前选中项"""
        mgr = self._music_playlist_manager
        selected_id = mgr.current_playlist_id
        wy_view_id = getattr(self, "_music_wy_remote_view_id", None)
        wy_key = self._music_wy_remote_key(wy_view_id) if wy_view_id else None

        for w in self._music_playlist_sidebar_widgets:
            frame = w.get("frame")
            if not frame or not frame.winfo_exists():
                continue
            pid = w.get("playlist_id")
            if pid is None:
                # "全部歌曲" — 仅在本地标签页时高亮
                frame.configure(fg_color="transparent")
            elif wy_key is not None and pid == wy_key:
                frame.configure(fg_color=COLORS["bg_light"])
            elif pid == selected_id:
                frame.configure(fg_color=COLORS["bg_light"])
            else:
                frame.configure(fg_color="transparent")

    def _music_show_playlist_context_menu(self, event, playlist_id: str):
        """显示歌单右键菜单"""
        pl = self._music_playlist_manager.get_playlist(playlist_id)
        if pl is None:
            return

        menu = ctk.CTkToplevel(self)
        menu.title("")
        menu.geometry(f"+{event.x_root}+{event.y_root}")
        menu.overrideredirect(True)
        menu.configure(fg_color=COLORS["card_bg"])
        menu.lift()
        menu.focus_force()

        btn_cfg = {
            "font": ctk.CTkFont(family=FONT_FAMILY, size=11),
            "fg_color": "transparent",
            "hover_color": COLORS["bg_light"],
            "text_color": COLORS["text_primary"],
            "anchor": "w",
            "height": 28,
        }

        ctk.CTkButton(
            menu,
            text=_("music_rename_playlist"),
            command=lambda: self._music_rename_playlist_dialog(playlist_id) or menu.destroy(),
            **btn_cfg,
        ).pack(fill=ctk.X, padx=4, pady=2)
        ctk.CTkButton(
            menu,
            text=_("music_delete_playlist"),
            command=lambda: self._music_delete_playlist_confirm(playlist_id) or menu.destroy(),
            **btn_cfg,
        ).pack(fill=ctk.X, padx=4, pady=2)

        # 如果是系统歌单，禁用编辑按钮
        if pl.is_system:
            for child in menu.winfo_children():
                try:
                    child.configure(state=ctk.DISABLED, text_color=COLORS["text_secondary"])
                except Exception:
                    pass

        self._music_open_popup_menu(menu)

    def _music_create_playlist_dialog(self):
        """弹出新建歌单对话框"""
        from ui.dialogs import show_input_dialog

        name = show_input_dialog(
            parent=self, title=_("music_new_playlist"), prompt=_("music_playlist_name_placeholder"), initial_value=""
        )
        # 「名字空白则不新建」+「新建后选为当前歌单」搬进
        # services.music_local.create_named_playlist（返回 None = 名字无效）
        pl = music_local.create_named_playlist(self._music_playlist_manager, name)
        if pl is None:
            return
        self._music_show_playlist(pl.id)
        self._save_music_state_later()

    def _music_rename_playlist_dialog(self, playlist_id: str):
        """弹出重命名歌单对话框"""
        from ui.dialogs import show_input_dialog

        # 「歌单不存在就不弹框」搬进 services.music_local.rename_target_name
        initial = music_local.rename_target_name(self._music_playlist_manager, playlist_id)
        if initial is None:
            return

        name = show_input_dialog(
            parent=self,
            title=_("music_rename_playlist"),
            prompt=_("music_playlist_name_placeholder"),
            initial_value=initial,
        )
        # 空名判定搬进 apply_rename；它的返回值是"是否真的发起了重命名"，
        # 不含 manager.rename_playlist 的成功与否（原文对那个返回值不作判断，
        # 系统歌单改名失败也照常重建侧边栏并落盘）。
        if not music_local.apply_rename(self._music_playlist_manager, playlist_id, name):
            return
        self._rebuild_playlist_sidebar()
        self._save_music_state_later()

    def _music_delete_playlist_confirm(self, playlist_id: str):
        """确认删除歌单"""
        import tkinter.messagebox as messagebox

        pl = self._music_playlist_manager.get_playlist(playlist_id)
        if pl is None:
            return

        msg = _("music_confirm_delete_playlist", name=pl.name)
        if msg == "music_confirm_delete_playlist":
            msg = f"确定要删除歌单「{pl.name}」吗？"

        if not messagebox.askyesno(_("music_delete_playlist"), msg):
            return

        mgr = self._music_playlist_manager
        mgr.delete_playlist(playlist_id)
        self._rebuild_playlist_sidebar()
        self._save_music_state_later()

    def _music_show_playlist(self, playlist_id: str):
        """在歌单标签页中显示指定歌单"""
        # 先确保在歌单标签页
        if self._music_tab_mode != "playlist":
            self._music_switch_to_playlist_tab()
        # 切换到本地歌单：清除远程歌单查看状态（防止两处同时高亮）
        self._music_wy_remote_view_id = None
        self._music_wy_remote_view_songs = []
        self._music_wy_update_pager()

        # 「取歌单 + 设为当前歌单」搬进 services.music_local.open_playlist
        # （歌单不存在时**不会**改动当前歌单，原文同序）
        pl = music_local.open_playlist(self._music_playlist_manager, playlist_id)
        if pl is None:
            return

        # 恢复该歌单的排序模式
        self._update_sort_buttons(pl.sort_mode)

        # 渲染歌曲列表
        self._rebuild_playlist_song_list(pl)
        self._rebuild_playlist_sidebar()

    def _rebuild_playlist_song_list(self, pl: Playlist, readonly: bool = False):
        """渲染歌单中的歌曲列表（readonly=True 时禁止编辑：无移除按钮、无右键菜单）"""
        # 清除旧列表
        for w in self._music_playlist_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_playlist_widgets.clear()

        if not pl.songs:
            # 空歌单占位
            empty_label = ctk.CTkLabel(
                self._music_playlist_scroll,
                text=_("music_playlist_empty"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
            )
            empty_label.pack(pady=30)
            self._music_playlist_widgets.append({"frame": empty_label})
            return

        for idx, song in enumerate(pl.songs):
            self._add_playlist_song_row(idx, song, readonly=readonly)

    def _add_playlist_song_row(self, idx: int, song: "PlaylistSong", readonly: bool = False):
        """渲染歌单中的单行歌曲（readonly=True 时无移除按钮）"""
        row = ctk.CTkFrame(self._music_playlist_scroll, fg_color="transparent", height=32)
        row.pack(fill=ctk.X, pady=1)

        # 序号
        ctk.CTkLabel(
            row,
            text=str(idx + 1),
            width=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        ).pack(side=ctk.LEFT)

        # 显示文本 / 时长 / 来源标记 / 要不要移除按钮，搬进
        # services.music_local.song_row_plan。下面三个显示条件**逐字保留**：
        # 它们与"文本是否为空"不是一回事（online_source 为空串时原文照样
        # 建一个空标签），所以只替换取值、不动条件。
        plan = music_local.song_row_plan(song, readonly)
        name_label = ctk.CTkLabel(
            row,
            text=plan.display_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        name_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(5, 5))

        # 时长标记
        if song.source_type == "online" and song.online_interval:
            ctk.CTkLabel(
                row,
                text=plan.dur_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["text_secondary"],
                width=35,
            ).pack(side=ctk.RIGHT, padx=(0, 2))

        # 来源标记
        if song.source_type == "online":
            ctk.CTkLabel(
                row,
                text=plan.source_tag,
                font=ctk.CTkFont(family=FONT_FAMILY, size=8),
                text_color=COLORS["accent"],
                width=28,
            ).pack(side=ctk.RIGHT, padx=(0, 2))

        # 移除按钮（只读歌单不显示：禁止编辑）；判据 = not readonly，由服务给出
        if plan.show_remove:
            remove_btn = ctk.CTkButton(
                row,
                text="✕",
                width=22,
                height=22,
                font=ctk.CTkFont(size=9),
                fg_color="transparent",
                hover_color=COLORS["accent"],
                text_color=COLORS["text_secondary"],
                command=lambda si=idx: self._music_remove_song_from_playlist(si),
            )
            remove_btn.pack(side=ctk.RIGHT, padx=(0, 4))

        # 点击播放
        self._bind_playlist_song_click(row, name_label, idx)

        self._music_playlist_widgets.append({"frame": row, "name_label": name_label, "index": idx})

    def _bind_playlist_song_click(self, row, label, idx: int):
        """绑定歌单歌曲点击事件"""
        # 网易云远程歌单（只读，分页）：绑定完整歌曲列表中的真实索引
        if self._music_wy_remote_view_id:
            songs = self._music_wy_remote_view_songs
            real_idx = (self._music_wy_remote_page - 1) * _MUSIC_WY_PAGE_SIZE + idx
            if real_idx >= len(songs):
                return

            def _play_remote(e=None):
                self._music_playlist_context_songs = list(songs)
                self._play_playlist_context_song(real_idx)

            for t in [row, label]:
                t.bind("<Button-1>", _play_remote)
                t.bind("<Double-Button-1>", _play_remote)
            return
        pl = self._music_playlist_manager.get_current_playlist()
        if pl is None or idx >= len(pl.songs):
            return
        song = pl.songs[idx]

        def _play(e=None):
            # 保存歌单上下文，供上下曲使用
            self._music_playlist_context_songs = list(pl.songs)
            self._play_playlist_context_song(idx)

        for t in [row, label]:
            t.bind("<Button-1>", _play)
            t.bind("<Double-Button-1>", _play)

    def _music_remove_song_from_playlist(self, song_index: int):
        """从当前歌单中移除歌曲"""
        # 「远程歌单只读 + 取当前歌单 + 移除」搬进
        # services.music_local.remove_song_from_current；
        # 返回值就是要重绘的那个歌单（None = 什么都没移除）。
        pl = music_local.remove_song_from_current(
            self._music_playlist_manager, song_index, readonly=bool(self._music_wy_remote_view_id)
        )
        if pl is None:
            return
        self._rebuild_playlist_song_list(pl)
        self._rebuild_playlist_sidebar()
        self._save_music_state_later()

    def _music_do_sort(self, mode: str):
        """执行歌单排序"""
        # 网易云远程歌单（只读）：仅内存内排序，不落盘；同模式再次点击切换方向。
        # 「空歌单判定 + 同模式翻转 + 就地排序」搬进
        # services.music_local.remote_sort_plan（返回 None = 无需动作）。
        if self._music_wy_remote_view_id:
            songs = self._music_wy_remote_view_songs
            sorted_mode = music_local.remote_sort_plan(
                songs, self._music_wy_remote_sort_mode, mode
            )
            if sorted_mode is None:
                return
            self._music_wy_remote_sort_mode = sorted_mode
            self._update_sort_buttons(sorted_mode)
            self._music_render_wy_remote_songs(self._music_wy_remote_view_id, songs)
            return
        # 「取当前歌单 + 同模式翻转 + 排序」搬进
        # services.music_local.local_sort_plan（返回 None = 没有当前歌单）
        plan = music_local.local_sort_plan(self._music_playlist_manager, mode)
        if plan is None:
            return
        self._update_sort_buttons(plan.mode)
        self._rebuild_playlist_song_list(plan.playlist)
        self._save_music_state_later()

    def _update_sort_buttons(self, mode: str):
        """更新排序按钮状态"""
        add_time_active = mode in (SORT_ADD_TIME_ASC, SORT_ADD_TIME_DESC)
        name_active = mode in (SORT_NAME_ASC, SORT_NAME_DESC)

        if mode == SORT_ADD_TIME_DESC:
            self._music_sort_add_time_btn.configure(text=_("music_sort_add_time") + " ▼", fg_color=COLORS["accent"])
        elif mode == SORT_ADD_TIME_ASC:
            self._music_sort_add_time_btn.configure(text=_("music_sort_add_time") + " ▲", fg_color=COLORS["accent"])
        else:
            self._music_sort_add_time_btn.configure(text=_("music_sort_add_time"), fg_color=COLORS["bg_light"])

        if mode == SORT_NAME_ASC:
            self._music_sort_name_btn.configure(text=_("music_sort_name") + " ▲", fg_color=COLORS["accent"])
        elif mode == SORT_NAME_DESC:
            self._music_sort_name_btn.configure(text=_("music_sort_name") + " ▼", fg_color=COLORS["accent"])
        else:
            self._music_sort_name_btn.configure(text=_("music_sort_name"), fg_color=COLORS["bg_light"])

    def _music_add_to_playlist_menu(self, song_info, is_online: bool = False):
        """弹出"添加到歌单"菜单"""
        mgr = self._music_playlist_manager
        playlists = mgr.playlists
        if not playlists:
            # 没有歌单，提示先创建
            import tkinter.messagebox as messagebox

            msg = _("music_new_playlist")
            if messagebox.askyesno(_("music_new_playlist"), "还没有歌单，是否创建一个？"):
                self._music_create_playlist_dialog()
            return

        # 创建临时右键菜单
        menu = ctk.CTkToplevel(self)
        menu.title("")
        menu.overrideredirect(True)
        menu.configure(fg_color=COLORS["card_bg"])
        menu.lift()
        menu.focus_force()

        btn_cfg = {
            "font": ctk.CTkFont(family=FONT_FAMILY, size=11),
            "fg_color": "transparent",
            "hover_color": COLORS["bg_light"],
            "text_color": COLORS["text_primary"],
            "anchor": "w",
            "height": 26,
        }

        # 「构造歌单项 + 逐歌单查重 + 条目文案（已存在加 ✓）」搬进
        # services.music_local.add_to_playlist_menu_plan。
        # 调用点与原文读元数据的位置一致（在菜单窗口创建之后），
        # 所以窗口弹出来的时机与原文相同。
        plan = music_local.add_to_playlist_menu_plan(
            self._music_playlist_manager,
            song_info,
            is_online,
            None if is_online else self._get_metadata(song_info),
        )
        for item in plan.items:
            btn = ctk.CTkButton(
                menu,
                text=item.text,
                command=lambda pid=item.playlist_id: self._music_add_song_to_playlist(pid, song_info, is_online) or menu.destroy(),
                **btn_cfg,
            )
            btn.pack(fill=ctk.X, padx=4, pady=1)

            if item.exists:
                btn.configure(state="disabled")

        # 自动定位
        try:
            x = self.winfo_pointerx()
            y = self.winfo_pointery()
            menu.geometry(f"+{x}+{y}")
        except Exception:
            pass

        self._music_open_popup_menu(menu)

    def _music_add_song_to_playlist(self, playlist_id: str, song_info, is_online: bool = False):
        """将歌曲添加到指定歌单"""
        # 「构造歌单项 + 去重添加 + 判断当前是否正在看这个歌单」搬进
        # services.music_local.add_song_to_playlist（元数据只在本地分支才取）
        outcome = music_local.add_song_to_playlist(
            self._music_playlist_manager,
            playlist_id,
            song_info,
            is_online,
            None if is_online else self._get_metadata(song_info),
        )
        if not outcome.added:
            return
        self._rebuild_playlist_sidebar()
        # 如果当前正在查看该歌单，刷新列表
        if outcome.playlist is not None:
            self._rebuild_playlist_song_list(outcome.playlist)
        self._save_music_state_later()

    # ── 播放历史记录 ──

    def _music_record_play_history_local(self, filepath: str):
        """记录本地歌曲到播放历史"""
        if not hasattr(self, "_music_playlist_manager"):
            return
        # 「构造歌单项 + 写进历史」搬进 services.music_local.record_history_local
        music_local.record_history_local(
            self._music_playlist_manager, filepath, self._get_metadata(filepath)
        )
        self._music_refresh_history_ui()

    def _music_record_play_history_online(self, online_info):
        """记录在线歌曲到播放历史"""
        if not hasattr(self, "_music_playlist_manager"):
            return
        # 「构造歌单项（转不了就放弃）+ 写进历史」搬进
        # services.music_local.record_history_online
        music_local.record_history_online(self._music_playlist_manager, online_info)
        self._music_refresh_history_ui()

    def _music_refresh_history_ui(self):
        """记录历史后刷新 UI（当前正停留在历史歌单视图时立即重绘）"""
        try:
            # 「停在歌单标签页 + 正在看历史歌单」的判据搬进
            # services.music_local.history_refresh_target
            current = music_local.history_refresh_target(
                self._music_playlist_manager, self._music_tab_mode
            )
            if current is None:
                return
            self._rebuild_playlist_song_list(current)
        except Exception:
            pass

    # ═══════════════ 网易云账号歌单（只读同步，不落盘） ═══════════════
    #
    # 登录网易云账号后，将账号创建的歌单同步到侧边栏「网易云歌单」分组。
    # 远程歌单只保存在内存（列表 + 歌曲缓存），不进入 PlaylistManager，
    # 因此永不写入 music.json、永不参与本地歌单的增删改（禁止编辑）。

    def _music_wy_remote_key(self, pl_id: str) -> str:
        """远程歌单的侧边栏条目 id（与本地歌单 id 区分）

        前缀约定的唯一真相在 services.music_wy_remote.remote_key
        （该模块只读、不落盘、不碰 PlaylistManager）。
        """
        return wy_remote.remote_key(pl_id)

    def _music_wy_sync_remote_playlists(self):
        """同步网易云登录账号创建的歌单列表（后台线程，结果经队列回主线程）

        未登录时清空远程歌单与歌曲缓存；同步失败保留上次成功结果并标记提示。
        """
        if not hasattr(self, "_music_wy_queue") or self._music_wy_sync_busy:
            return
        self._music_wy_sync_busy = True
        self._music_wy_sync_seq += 1
        seq = self._music_wy_sync_seq
        threading.Thread(target=self._music_wy_sync_worker, args=(seq,), daemon=True).start()

    def _music_wy_sync_worker(self, seq: int):
        """后台线程：检查登录态并拉取歌单列表（绝不触碰 Tk）

        登录态判定与拉取（含异常降级为 error 态）在
        services.music_wy_remote.sync_playlists；本方法只把结果投进主线程队列，
        队列的事件种类以该模块的 EVENT_* 为唯一真相。
        """
        state, data = wy_remote.sync_playlists()
        self._music_wy_queue.put(wy_remote.make_event(wy_remote.EVENT_SYNC, seq, state, data))

    def _music_wy_dispatcher_tick(self):
        """主线程调度器：统一处理后台线程投递的事件（worker 不直接触碰 Tk）"""
        if not hasattr(self, "_music_wy_queue"):
            return
        try:
            # drain_events 是生成器：处理某条事件抛异常时它随即关闭，
            # 剩余事件仍留在队列里、下一次 tick 再取（与原文 while+break 等价）；
            # 换成「先取完再返回列表」会把这批事件摘掉却不处理，等于丢事件。
            for event in wy_remote.drain_events(self._music_wy_queue):
                self._music_wy_handle_event(event)
        except Exception as e:
            logger.debug(f"网易云歌单事件处理异常: {e}")
        try:
            self._music_wy_dispatcher_id = self.after(
                wy_remote.DISPATCH_INTERVAL_MS, self._music_wy_dispatcher_tick
            )
        except Exception:
            pass

    def _music_wy_handle_event(self, event):
        # 事件种类（队列的线上协议）以 services.music_wy_remote 的 EVENT_* 为唯一真相；
        # EVENT_PREFETCH_FAIL 故意不分派：预取失败不设槽位，播完时走常规按需流程
        kind = event[0]
        try:
            if kind == wy_remote.EVENT_SYNC:
                self._music_wy_apply_sync(event[1], event[2], event[3])
            elif kind == wy_remote.EVENT_TRACKS:
                self._music_wy_apply_tracks(event[1], event[2])
            elif kind == wy_remote.EVENT_PREFETCH:
                self._music_on_prefetch_ready(event[1], event[2], event[3], event[4], event[5], event[6])
        except Exception as e:
            logger.debug(f"网易云歌单事件执行异常: {e}")

    def _music_wy_apply_sync(self, seq: int, state: str, playlists: List[dict]):
        """主线程应用歌单列表同步结果"""
        # 应用结论（过期/退出/失败/无变化/更新）在
        # services.music_wy_remote.plan_sync_apply；写回顺序与原文逐条对应 ——
        # 中间那次 _music_show_playlist 会让侧边栏看到**旧的** sync_failed
        # 与旧的歌单列表，顺序一换侧边栏标题就变了。
        plan = wy_remote.plan_sync_apply(
            seq=seq,
            sync_seq=self._music_wy_sync_seq,
            state=state,
            playlists=playlists,
            current_playlists=self._music_wy_remote_playlists,
            view_id=self._music_wy_remote_view_id,
            tab_mode=self._music_tab_mode,
        )
        if plan.kind == wy_remote.SYNC_STALE:
            return  # 过期同步结果（已触发新的同步），丢弃
        self._music_wy_sync_busy = False
        if plan.kind == wy_remote.SYNC_LOGGED_OUT:
            # 账号已退出（设置页退出/登录失效）：清空远程歌单与歌曲缓存
            if plan.clear_view:
                self._music_wy_remote_view_id = None
                self._music_wy_remote_view_songs = []
                if plan.fallback_to_history:
                    history = self._music_playlist_manager.get_or_create_history_playlist()
                    self._music_show_playlist(history.id)
            self._music_wy_remote_playlists = []
            self._music_wy_remote_cache.clear()
            self._music_wy_loading_ids.clear()
            self._music_wy_sync_failed = False
            self._rebuild_playlist_sidebar()
            return
        if plan.kind == wy_remote.SYNC_ERROR:
            # 网络/接口失败：保留上次成功结果，侧边栏标题标记同步失败
            self._music_wy_sync_failed = True
            return
        if plan.kind == wy_remote.SYNC_UNCHANGED:
            self._music_wy_sync_failed = False
            return  # 无变化，不重建侧边栏
        self._music_wy_sync_failed = False
        # 清除已不在列表中的歌单的歌曲缓存与加载标记
        for pid in plan.drop_ids:
            self._music_wy_remote_cache.pop(pid, None)
            self._music_wy_loading_ids.discard(pid)
        self._music_wy_remote_playlists = playlists
        # 当前查看的远程歌单已被删除：切回播放历史
        if plan.clear_view:
            self._music_wy_remote_view_id = None
            self._music_wy_remote_view_songs = []
            if plan.fallback_to_history:
                history = self._music_playlist_manager.get_or_create_history_playlist()
                self._music_show_playlist(history.id)
        self._rebuild_playlist_sidebar()

    def _music_show_wy_remote_playlist(self, pl_id: str):
        """在歌单标签页显示网易云远程歌单（只读）"""
        if self._music_tab_mode != "playlist":
            self._music_switch_to_playlist_tab()
        self._music_wy_remote_view_id = pl_id
        self._music_wy_remote_page = 1  # 切换歌单后回到第一页
        self._update_sort_buttons(self._music_wy_remote_sort_mode)
        # 缓存命中（含空歌单成功缓存 []）：直接渲染；None = 没缓存过要去后台拉，
        # 这个区分语义在 services.music_wy_remote.cached_songs
        cached = wy_remote.cached_songs(self._music_wy_remote_cache, pl_id)
        if cached is not None:
            self._music_render_wy_remote_songs(pl_id, cached)
        else:
            self._music_show_wy_remote_loading(pl_id)
            self._music_wy_fetch_remote_songs(pl_id)
        self._rebuild_playlist_sidebar()

    def _music_wy_fetch_remote_songs(self, pl_id: str):
        """后台拉取远程歌单歌曲（已在加载中则跳过）"""
        if pl_id in self._music_wy_loading_ids:
            return
        self._music_wy_loading_ids.add(pl_id)
        threading.Thread(target=self._music_wy_tracks_worker, args=(pl_id,), daemon=True).start()

    def _music_wy_tracks_worker(self, pl_id: str):
        """后台线程：拉取歌单歌曲（绝不触碰 Tk）

        拉取与失败降级（None = 失败，[] = 空歌单）在
        services.music_wy_remote.fetch_playlist_tracks。
        """
        infos = wy_remote.fetch_playlist_tracks(pl_id)
        self._music_wy_queue.put(wy_remote.make_event(wy_remote.EVENT_TRACKS, pl_id, infos))

    def _music_wy_apply_tracks(self, pl_id: str, infos):
        """主线程应用歌单歌曲拉取结果（None=失败，[]=空歌单）

        三种去向（仅缓存 / 显示失败 / 渲染）在
        services.music_wy_remote.tracks_action；PlaylistSong 转换与控件操作
        留在界面侧（远程歌单只读、不落盘）。
        """
        self._music_wy_loading_ids.discard(pl_id)
        action = wy_remote.tracks_action(pl_id, infos, self._music_wy_remote_view_id)
        if action == wy_remote.TRACKS_CACHE_ONLY:
            # 用户已切换歌单：成功结果仅缓存，供下次点击直接使用
            if infos is not None:
                self._music_wy_remote_cache[pl_id] = [PlaylistSong.from_online_info(i) for i in infos]
            return
        if action == wy_remote.TRACKS_FAILED:
            self._music_show_wy_remote_failed(pl_id)
            return
        songs = [PlaylistSong.from_online_info(i) for i in infos]
        self._music_wy_remote_cache[pl_id] = songs
        self._music_render_wy_remote_songs(pl_id, songs)

    def _music_show_wy_remote_loading(self, pl_id: str):
        """远程歌单加载中占位"""
        for w in self._music_playlist_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_playlist_widgets.clear()
        self._music_wy_remote_view_songs = []
        self._music_wy_update_pager()
        label = ctk.CTkLabel(
            self._music_playlist_scroll,
            text=_("music_wy_playlist_loading"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        label.pack(pady=30)
        self._music_playlist_widgets.append({"frame": label})

    def _music_show_wy_remote_failed(self, pl_id: str):
        """远程歌单加载失败提示（再次点击侧边栏条目可重试）"""
        for w in self._music_playlist_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_playlist_widgets.clear()
        self._music_wy_remote_view_songs = []
        self._music_wy_update_pager()
        label = ctk.CTkLabel(
            self._music_playlist_scroll,
            text=_("music_wy_playlist_load_failed"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=12),
            text_color=COLORS["text_secondary"],
        )
        label.pack(pady=30)
        self._music_playlist_widgets.append({"frame": label})

    def _music_render_wy_remote_songs(self, pl_id: str, songs: List[PlaylistSong]):
        """渲染远程歌单当前页歌曲列表（只读，禁止编辑，超过 20 首分页）

        页码钳制与切片范围在 services.music_wy_remote.page_window。
        """
        self._music_wy_remote_view_songs = songs
        window = wy_remote.page_window(len(songs), self._music_wy_remote_page)
        self._music_wy_remote_page = window.page
        start = window.start
        page_songs = songs[start : window.stop]
        transient = Playlist(id=self._music_wy_remote_key(pl_id), name="", songs=page_songs)
        self._rebuild_playlist_song_list(transient, readonly=True)
        # 记录每行在完整歌单中的真实索引（高亮/自动翻页依赖）
        for w in self._music_playlist_widgets:
            if "index" in w and "real_index" not in w:
                w["real_index"] = start + w["index"]
        self._music_wy_update_pager()

    def _music_wy_update_pager(self):
        """更新远程歌单分页栏（<=20 首或非远程视图时隐藏）

        可见性/页码钳制/按钮状态在 services.music_wy_remote.pager_plan。
        """
        frame = getattr(self, "_music_wy_pager_frame", None)
        if frame is None or not frame.winfo_exists():
            return
        plan = wy_remote.pager_plan(
            total=len(self._music_wy_remote_view_songs),
            page=self._music_wy_remote_page,
            has_view=bool(self._music_wy_remote_view_id),
        )
        self._music_wy_remote_page = plan.page
        if plan.hidden:
            frame.pack_forget()
            return
        frame.pack(fill=ctk.X, pady=(4, 0))
        self._music_wy_pager_page_label.configure(
            text=_("music_wy_playlist_page", page=plan.page, total=plan.pages)
        )
        self._music_wy_pager_prev.configure(state="normal" if plan.prev_enabled else "disabled")
        self._music_wy_pager_next.configure(state="normal" if plan.next_enabled else "disabled")

    def _music_wy_go_page(self, page: int):
        """远程歌单翻页（仅内存内展示切换，不影响完整播放列表）

        越界/同页守卫在 services.music_wy_remote.go_page_target。
        """
        if not self._music_wy_remote_view_id:
            return
        songs = self._music_wy_remote_view_songs
        target = wy_remote.go_page_target(
            page=page, current_page=self._music_wy_remote_page, total=len(songs)
        )
        if target is None:
            return
        self._music_wy_remote_page = target
        self._music_render_wy_remote_songs(self._music_wy_remote_view_id, songs)

    def _music_wy_start_periodic(self):
        """启动网易云歌单定期刷新（未登录时同步逻辑自动跳过，开销可忽略）"""
        if self._music_wy_periodic_id is not None:
            return
        self._music_wy_periodic_id = self.after(_WY_REMOTE_PERIODIC_MS, self._music_wy_periodic_tick)

    def _music_wy_periodic_tick(self):
        self._music_wy_periodic_id = None
        try:
            self._music_wy_sync_remote_playlists()
        except Exception as e:
            logger.debug(f"网易云歌单定时同步异常: {e}")
        self._music_wy_start_periodic()

    # ── 播放全部按钮 ──

    def _music_play_playlist_all(self):
        """播放当前歌单的所有可播放歌曲（支持本地/在线混合）"""
        # 「第一首可播歌曲 + 歌单上下文副本」搬进 services.music_local.play_all_target
        # 网易云远程歌单：播放远程歌曲（全部为在线歌曲）
        if self._music_wy_remote_view_id:
            target = music_local.play_all_target(self._music_wy_remote_view_songs, remote=True)
        else:
            pl = self._music_playlist_manager.get_current_playlist()
            if pl is None:
                return
            target = music_local.play_all_target(pl.songs)
        if target is None:
            return
        # 保存歌单上下文，供上下曲使用
        self._music_playlist_context_songs = target.context_songs
        if target.index >= 0:
            self._play_playlist_context_song(target.index)

    # ═══════════════ 注册热键 ─────────────────────

    def _register_hotkeys(self):
        if self._music_hotkeys_registered:
            return
        if not _keyboard_available:
            logger.debug("keyboard 库不可用，全局热键已禁用")
            return

        def _do_register():
            try:
                self._music_warmup_hook = _keyboard.hook(lambda e: None)
                time.sleep(0.1)
                # 7 个动作的组合键与注册顺序以 services.music_hotkeys 为唯一真相
                # （注销用的是同一份 HOTKEY_ACTIONS，不会再出现两处错位）；
                # backend 与回调由界面注入，服务只负责遍历编排。
                music_hotkeys.register_all(
                    _keyboard,
                    {
                        "play_pause": self._music_hotkey_play_pause,
                        "prev": self._music_hotkey_prev,
                        "next": self._music_hotkey_next,
                        "stop": self._music_hotkey_stop,
                        "vol_up": self._music_hotkey_vol_up,
                        "vol_down": self._music_hotkey_vol_down,
                        "vol_mute": self._music_hotkey_vol_mute,
                    },
                )
                self._music_hotkeys_registered = True
                logger.info("音乐播放全局热键已注册")
            except Exception as e:
                # Linux 下 keyboard 库通常需要 root 且全局热键不可靠，降级为 debug 避免噪音
                global _keyboard_available
                _keyboard_available = False
                if sys.platform == "win32":
                    logger.warning(f"注册全局热键失败: {e}")
                else:
                    logger.debug(f"当前平台不支持全局热键，已跳过: {e}")

        threading.Thread(target=_do_register, daemon=True).start()

    def _unregister_hotkeys(self):
        if not self._music_hotkeys_registered:
            return
        if not _keyboard_available:
            return
        try:
            # 与注册共用 services.music_hotkeys.HOTKEY_ACTIONS 的同一份顺序；
            # 异常仍在这里的 try 里吞掉（原文的边界逐字保留）。
            music_hotkeys.unregister_all(_keyboard)
            if self._music_warmup_hook is not None:
                self._music_warmup_hook()
                self._music_warmup_hook = None
            self._music_hotkeys_registered = False
            logger.info("音乐播放全局热键已注销")
        except Exception:
            pass

    def _music_hotkey_play_pause(self):
        self.after(0, self._music_toggle_play)

    def _music_hotkey_prev(self):
        self.after(0, self._music_prev)

    def _music_hotkey_next(self):
        self.after(0, self._music_next)

    def _music_hotkey_stop(self):
        self.after(0, self._music_stop)

    def _music_hotkey_vol_up(self):
        # 步长以 services.music_hotkeys.VOLUME_HOTKEY_STEP 为唯一真相
        self.after(0, lambda: self._adjust_volume(music_hotkeys.VOLUME_HOTKEY_STEP))

    def _music_hotkey_vol_down(self):
        self.after(0, lambda: self._adjust_volume(-music_hotkeys.VOLUME_HOTKEY_STEP))

    def _music_hotkey_vol_mute(self):
        self.after(0, self._music_toggle_mute)

    def _adjust_volume(self, delta: int):
        # 0..100 钳制算式搬进 services.music_player.volume_percent_after_delta
        new_vol = self._music_engine.volume_percent_after_delta(self._music_volume, delta)
        self._music_volume = new_vol / 100.0
        self._music_vol_slider.set(new_vol)
        if hasattr(self, "_music_mini_vol"):
            self._music_mini_vol.set(new_vol)
        if self._music_engine.available and not self._music_is_fading:
            self._music_engine.set_mixer_volume(self._music_volume)
        self._update_mute_btn_ui()
        self._trigger_ach("music_volume_tweaker")

    def _save_music_state_later(self):
        # 防抖延迟以 services.music_state.SAVE_DEBOUNCE_MS 为准（定时器留界面侧）
        self.after(SAVE_DEBOUNCE_MS, self._save_music_state)

    def _save_music_state(self):
        if not hasattr(self, "_music_init_done") or not self._music_init_done:
            return
        try:
            # 键名/取值规则住在 services.music_state.build_music_state；
            # **取值仍由界面提供**（这些属性被 100+ 个范围外方法读写，所有权没搬）
            state = build_music_state(
                last_folder=self._music_last_folder,
                current_index=self._music_current_index,
                progress=self._music_progress,
                volume=self._music_volume,
                play_mode=self._music_play_mode,
                mini_mode=self._music_mini_mode,
                playlist_id=self._music_playlist_manager.current_playlist_id,
                playlist_context_idx=self._music_playlist_context_idx,
                playlist_context_songs=self._music_playlist_context_songs,
            )
            if hasattr(self, "callbacks") and "save_music_state" in self.callbacks:
                self.callbacks["save_music_state"](state)
            # 标记歌单为脏，由后台定时器统一写入磁盘，避免频繁 I/O 卡顿
            if hasattr(self, "_music_playlist_manager"):
                self._music_playlist_manager.mark_dirty()
        except Exception as e:
            logger.debug(f"保存音乐状态失败: {e}")

    def _music_apply_wy_saved_login(self, _retry_count: int = 0):
        """将已保存的网易云音乐登录 Cookie 应用到网易云音源会话

        登录后网易云音源可播放 VIP 歌曲并获取 VIP 歌词；
        Cookie 由设置页扫码登录生成，加密持久化在配置中。
        启动早期 callbacks 尚未就绪（主窗口先以空 dict 创建），
        与 _load_music_state 相同：就绪前定时重试。
        """
        # 重试判据（回调没就绪 / 没有 get_wy_cookie 回调 → retry_count < 60 就重试）
        # 与"读 Cookie + 应用 Cookie"的容错都在 services.music_state 里
        if login_retry_due(getattr(self, "callbacks", None), _retry_count):
            self.after(LOAD_RETRY_MS, lambda: self._music_apply_wy_saved_login(_retry_count + 1))
            return
        cookie = read_saved_cookie(self.callbacks)
        if not cookie:
            return
        apply_wy_cookie(cookie)
        # 登录恢复成功后同步网易云账号歌单（未登录时由同步逻辑自动清空）
        try:
            self._music_wy_sync_remote_playlists()
        except Exception as e:
            logger.debug(f"启动网易云歌单同步失败: {e}")

    def _load_music_state(self, _retry_count: int = 0):
        if not hasattr(self, "callbacks"):
            return
        load_fn = self.callbacks.get("load_music_state")
        if not load_fn:
            if _retry_count < LOAD_RETRY_MAX:
                self.after(LOAD_RETRY_MS, lambda: self._load_music_state(_retry_count + 1))
            return
        try:
            state = load_fn()
            if not state:
                return
            # 键名/默认值/坏值容错全在 services.music_state.parse_music_state
            # （音乐模式名 → 模式号的映射也在那里；未知名字回退 loop_list）
            parsed = parse_music_state(state)
            folder = parsed.last_folder
            vol = parsed.volume
            self._music_current_index = parsed.current_index
            self._music_progress = parsed.progress
            self._music_mini_mode = parsed.mini_mode

            if vol is not None:
                self._music_volume = float(vol)

            self._music_play_mode = parsed.play_mode
            self._update_mode_btn_text()

            if hasattr(self, "_music_vol_slider") and self._music_vol_slider.winfo_exists():
                self._music_vol_slider.set(volume_to_slider(self._music_volume))
            if hasattr(self, "_music_mini_vol") and self._music_mini_vol.winfo_exists():
                self._music_mini_vol.set(volume_to_slider(self._music_volume))
            self._update_mute_btn_ui()

            if folder and os.path.isdir(folder):
                self._music_last_folder = folder
                self._music_folder_label.configure(text=os.path.basename(folder) or folder)
                self._music_scan_folder_restore(folder)
            # 加载歌单数据
            if hasattr(self, "_music_playlist_manager"):
                self._music_playlist_manager.load()
                self._rebuild_playlist_sidebar()
                # 自动切换到上次打开的歌单，若不存在则回退到播放历史
                saved_pl_id = parsed.last_playlist_id
                target_id = None
                if saved_pl_id and self._music_playlist_manager.get_playlist(saved_pl_id):
                    target_id = saved_pl_id
                else:
                    history = self._music_playlist_manager.get_or_create_history_playlist()
                    target_id = history.id
                self._music_show_playlist(target_id)
                # 恢复歌单中的歌曲位置和进度
                pl = self._music_playlist_manager.get_current_playlist()
                saved_song_idx = parsed.song_index
                if pl and 0 <= saved_song_idx < len(pl.songs):
                    self._music_playlist_context_songs = list(pl.songs)
                    self._music_playlist_context_idx = saved_song_idx
                    song = pl.songs[saved_song_idx]
                    self._music_progress = parsed.progress
                    # 同步 _music_playlist 供本地播放使用
                    if song.source_type == "local" and os.path.exists(song.file_path):
                        local_paths = [
                            s.file_path for s in pl.songs if s.source_type == "local" and os.path.exists(s.file_path)
                        ]
                        if local_paths:
                            self._music_playlist = local_paths
                            try:
                                self._music_current_index = local_paths.index(song.file_path)
                            except ValueError:
                                pass
                    self._highlight_playlist_song(saved_song_idx)
        except Exception as e:
            logger.debug(f"加载音乐状态失败: {e}")

    # ═══════════════ 启动器就绪后恢复播放状态 ═══════════════

    def _music_on_launcher_ready(self):
        """启动器核心初始化完成后调用，从配置中恢复音乐播放状态"""
        self._load_music_state()

    # ═══════════════ 定时保存 ═══════════════

    def _music_start_periodic_save(self):
        """启动后台定时保存（每 30 秒检查脏标记并落盘）

        周期值以 services.music_state.PERIODIC_SAVE_INTERVAL_MS 为准；
        `after` 定时器与 tick 的实现留在界面侧。
        """
        self._music_periodic_save_id = self.after(PERIODIC_SAVE_INTERVAL_MS, self._music_periodic_save_tick)

    def _music_periodic_save_tick(self):
        try:
            if hasattr(self, "_music_playlist_manager"):
                self._music_playlist_manager.save_if_dirty()
        except Exception:
            pass
        self._music_start_periodic_save()

    def _music_stop_periodic_save(self):
        if self._music_periodic_save_id is not None:
            self.after_cancel(self._music_periodic_save_id)
            self._music_periodic_save_id = None

    def _music_scan_folder_restore(self, folder: str):
        files = []
        try:
            for root, dirs, filenames in os.walk(folder):
                for fname in filenames:
                    ext = os.path.splitext(fname)[1].lower()
                    if ext in AUDIO_EXTENSIONS:
                        files.append(os.path.join(root, fname))
        except Exception:
            return
        if not files:
            return
        files.sort(key=lambda f: os.path.basename(f).lower())
        self._music_playlist = files
        self._music_metadata_cache.clear()
        count_text = _("music_song_count", count=len(files))
        if count_text == "music_song_count":
            count_text = f"{len(files)} 首"
        self._music_song_count_label.configure(count_text)
        self._rebuild_playlist_ui()
        self._update_mode_btn_text()

        if self._music_mini_mode:
            self._music_main_frame.pack_forget()
            self._music_mini_bar.pack(fill=ctk.X, padx=15, pady=(0, 15))
            if hasattr(self, "_music_mini_toggle_btn") and self._music_mini_toggle_btn.winfo_exists():
                self._music_mini_toggle_btn.configure(text=_("music_expand"))

    # ═══════════════ 在线搜索逻辑 ═══════════════

    def _music_do_search(self):
        # 取值规则（strip、空则不搜）在 services.music_online.normalize_keyword
        keyword = music_online.normalize_keyword(self._music_search_entry.get())
        if not keyword:
            return
        self._music_search_keyword = keyword
        self._music_start_search(music_online.FIRST_SEARCH_PAGE)

    def _music_start_search(self, page: int):
        """发起搜索请求（页码从 1 开始），带忙碌标记与请求序号防并发覆盖

        准入判定（忙碌/页码非法）与「每页条数取当前音源的服务端限制」在
        services.music_online.plan_search；控件与线程留在界面侧。
        """
        plan = music_online.plan_search(
            page=page,
            busy=self._music_search_busy,
            source_id=self._music_selected_source,
        )
        if plan is None:
            return
        self._music_search_page = plan.page
        self._music_search_busy = plan.busy
        self._music_search_total_pages = 0  # 新请求未返回前不沿用旧音源的页数
        self._music_search_page_size = plan.page_size
        self._music_search_seq += 1
        seq = self._music_search_seq
        self._music_search_btn.configure(state="disabled", text="...")
        self._music_search_status.configure(text=_("music_loading_url"))
        self._music_rebuild_pager()
        threading.Thread(
            target=self._music_online_search_thread, args=(self._music_search_keyword, page, seq), daemon=True
        ).start()

    def _music_online_search_thread(self, keyword: str, page: int, seq: int):
        # 单音源搜索（含「音源缺失 -> 空结果」与异常降级为 warning）在
        # services.music_online.run_source_search
        results = music_online.run_source_search(
            self._music_selected_source, keyword, page, self._music_search_page_size
        )
        self.after(0, lambda: self._music_rebuild_search_results(results, seq))

    def _music_rebuild_search_results(self, results, seq: Optional[int] = None):
        if seq is not None and seq != self._music_search_seq:
            return  # 过期请求（用户已重新搜索/翻页），丢弃
        self._music_search_busy = False
        self._music_search_results = results
        # 音源提供总数时一次性算出总页数；否则按满页启发式判断下一页。
        # 页数与状态栏形态的判定在 services.music_online.summarize_search。
        outcome = music_online.summarize_search(
            results,
            source_id=self._music_selected_source,
            page=self._music_search_page,
            page_size=self._music_search_page_size,
        )
        self._music_search_total_pages = outcome.total_pages
        self._music_search_has_more = outcome.has_more
        self._music_render_search_rows(results)
        if outcome.status == music_online.STATUS_RESULTS:
            count = outcome.count
            song_count_key = "music_song_count"
            count_text = _(song_count_key, count=count)
            if count_text == song_count_key:
                count_text = f"{count} 首"
            self._music_search_status.configure(text=count_text)
        elif outcome.status == music_online.STATUS_NO_MORE:
            # 非首页但无结果：已到最后一页
            no_more_key = "music_search_no_more"
            no_more_text = _(no_more_key)
            if no_more_text == no_more_key:
                no_more_text = "没有更多结果"
            self._music_search_status.configure(text=no_more_text)
        else:
            self._music_search_status.configure(text=_("music_search_no_results"))
        self._music_search_btn.configure(state="normal", text=_("music_search_btn"))
        self._music_rebuild_pager()

    def _music_render_search_rows(self, results: List[OnlineMusicInfo]):
        """重建搜索结果行（先销毁旧行，再按当前结果渲染）

        供搜索完成与百度百科原唱异步回填共用；回填时结果列表对象与
        _music_search_results 为同一引用，直接重渲染即可刷新徽章。
        """
        for w in self._music_search_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_search_widgets.clear()
        for idx, info in enumerate(results):
            self._music_add_search_row(idx, info)

    def _music_original_backfill_cb(self, results: List[OnlineMusicInfo]):
        """百度百科原唱查询完成后的回填回调（后台线程触发，转主线程渲染）"""
        try:
            self.after(0, lambda: self._music_apply_original_backfill(results))
        except Exception as e:
            logger.warning(f"百度百科原唱回填调度失败: {e}")

    def _music_apply_original_backfill(self, results: List[OnlineMusicInfo]):
        """主线程应用百度百科原唱回填：仅当结果仍是当前展示的搜索页时刷新

        「仍是当前页」是**对象身份**判定，规则在
        services.music_online.backfill_targets_current。
        """
        if not music_online.backfill_targets_current(results, self._music_search_results):
            return
        if not getattr(self, "_music_online_frame", None) or not self._music_online_frame.winfo_exists():
            return
        try:
            self._music_render_search_rows(results)
        except Exception as e:
            logger.warning(f"百度百科原唱回填渲染失败: {e}")

    def _music_search_go_page(self, page: int):
        """跳转到指定页码（三条守卫在 services.music_online.go_page_target）"""
        target = music_online.go_page_target(
            page=page,
            current_page=self._music_search_page,
            busy=self._music_search_busy,
            keyword=self._music_search_keyword,
            has_results=bool(self._music_search_results),
        )
        if target is None:
            return
        self._music_start_search(target)

    def _music_search_prev_page(self):
        self._music_search_go_page(self._music_search_page - 1)

    def _music_search_next_page(self):
        self._music_search_go_page(self._music_search_page + 1)

    def _music_rebuild_pager(self):
        """重建分页栏：全部页码按钮（一次性按总页数生成）+ 上一页/下一页状态

        页码集合与按钮可用性在 services.music_online.pager_plan；控件留在界面侧。
        """
        if not hasattr(self, "_music_pager_frame"):
            return
        cur = self._music_search_page
        busy = self._music_search_busy

        # 音源提供总数时直接生成全部页码；否则满页时推测下一页存在，逐页追加
        plan = music_online.pager_plan(
            current_page=cur,
            total_pages=self._music_search_total_pages,
            has_more=self._music_search_has_more,
            busy=busy,
        )
        pages = plan.pages

        for w in self._music_pager_widgets:
            try:
                f = w.get("frame")
                if f and f.winfo_exists():
                    f.destroy()
            except Exception:
                pass
        self._music_pager_widgets.clear()

        for p in pages:
            btn = ctk.CTkButton(
                self._music_pager_page_box,
                text=str(p),
                width=30,
                height=24,
                font=ctk.CTkFont(family=FONT_FAMILY, size=10),
                fg_color=COLORS["accent"] if p == cur else COLORS["bg_light"],
                hover_color=COLORS["accent"],
                command=lambda pg=p: self._music_search_go_page(pg),
            )
            btn.pack(side=ctk.LEFT, padx=2)
            self._music_pager_widgets.append({"frame": btn, "page": p})
            self._theme_refs.append((btn, {"fg_color": "bg_light", "hover_color": "accent"}))

        # 自动横向滚动到当前页（按钮宽度一致，按位置比例估算）
        try:
            box = self._music_pager_page_box
            box.update_idletasks()
            if len(pages) > 1:
                box._parent_canvas.xview_moveto((cur - 1) / (len(pages) - 1))
        except Exception:
            pass

        self._music_pager_prev.configure(state=ctk.NORMAL if plan.prev_enabled else ctk.DISABLED)
        self._music_pager_next.configure(
            state=ctk.NORMAL if plan.next_enabled else ctk.DISABLED
        )

        page_key = "music_search_page"
        page_text = _(page_key, page=cur)
        if page_text == page_key:
            page_text = f"第 {cur} 页"
        self._music_pager_label.configure(text=page_text)

    def _music_add_search_row(self, idx: int, info: OnlineMusicInfo):
        # 要显示什么（歌名截断、原唱徽章 key、时长、播放量、音源标签）在
        # services.music_online.search_row_plan；控件构建与事件绑定留在界面侧。
        row_plan = music_online.search_row_plan(idx, info)
        is_original = row_plan.is_original
        row = ctk.CTkFrame(
            self._music_online_scroll,
            fg_color=COLORS["bg_light"] if is_original else "transparent",
            height=32,
        )
        row.pack(fill=ctk.X, pady=1)

        index_label = ctk.CTkLabel(
            row,
            text=row_plan.index_text,
            width=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
        )
        index_label.pack(side=ctk.LEFT)

        # 歌名截断（35/33）与「歌名 - 歌手」的拼法在 search_row_plan 里
        name_wrap = ctk.CTkFrame(row, fg_color="transparent")
        name_wrap.pack(side=ctk.LEFT, fill=ctk.X, expand=True, padx=(5, 5))
        name_label = ctk.CTkLabel(
            name_wrap,
            text=row_plan.display,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            anchor="w",
        )
        name_label.pack(side=ctk.LEFT)

        if is_original:
            # 徽章文案的 i18n key 由服务给出（文案函数只能在界面调）
            ctk.CTkLabel(
                name_wrap,
                text=_(row_plan.tag_key, **row_plan.tag_params),
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["warning"],
            ).pack(side=ctk.LEFT, padx=(6, 0))

        if row_plan.dur_text:
            ctk.CTkLabel(
                row,
                text=row_plan.dur_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["text_secondary"],
                width=40,
            ).pack(side=ctk.RIGHT)

        # 播放量（音源未提供时为 0，不显示）
        if row_plan.play_text:
            ctk.CTkLabel(
                row,
                text=row_plan.play_text,
                font=ctk.CTkFont(family=FONT_FAMILY, size=9),
                text_color=COLORS["text_secondary"],
                width=44,
            ).pack(side=ctk.RIGHT, padx=(0, 4))

        source_label = ctk.CTkLabel(
            row,
            text=row_plan.source_text,
            font=ctk.CTkFont(family=FONT_FAMILY, size=8),
            text_color=COLORS["accent"],
            width=28,
        )
        source_label.pack(side=ctk.RIGHT, padx=(0, 4))

        # 添加到歌单按钮
        add_btn = ctk.CTkButton(
            row,
            text="➕",
            width=22,
            height=22,
            font=ctk.CTkFont(size=9),
            fg_color="transparent",
            hover_color=COLORS["accent"],
            text_color=COLORS["text_secondary"],
            command=lambda oi=info: self._music_add_to_playlist_menu(oi, is_online=True),
        )
        add_btn.pack(side=ctk.RIGHT, padx=(0, 2))

        for child in [row, index_label, name_wrap, name_label]:
            child.bind("<Button-1>", lambda e, i=idx: self._music_play_online_from_index(i))
            child.bind("<Double-Button-1>", lambda e, i=idx: self._music_play_online_from_index(i))

        self._music_search_widgets.append({"frame": row, "name_label": name_label, "index": idx})

    def _music_play_online_from_index(self, idx: int):
        if idx < 0 or idx >= len(self._music_search_results):
            return
        self._music_playlist_context_songs = []  # 退出歌单上下文
        self._music_playlist_context_idx = -1
        self._music_play_online_url(self._music_search_results[idx])

    def _music_resolve_auto_quality(self, online_info: OnlineMusicInfo) -> str:
        """自动音质：取当前账号在该歌曲上可用的最高音质

        音源搜索结果的 types/maxbr 由服务器按当前账号返回（免费用户
        最高 128k、音乐包 320k、黑胶VIP 无损、SVIP 母带，登录与否
        直接影响可用音质），自动模式即从高到低选第一个可用的。

        判定在 services.music_online.resolve_auto_quality（零 UI、音源表可注入）。
        """
        return music_online.resolve_auto_quality(online_info)

    def _music_resolve_auto_quality_async(self, online_info: OnlineMusicInfo) -> Tuple[OnlineMusicInfo, str]:
        """后台线程解析自动音质（含音质信息补齐）

        歌单/播放历史恢复的歌曲由 PlaylistSong 重建，旧数据可能缺少
        可用音质信息（types/_types）。此时先向音源重新搜索同款歌
        （按 songmid 匹配）补齐，再取最高可用音质——否则自动解析
        只能回退 128k，且 URL 获取缺少 hash 等详情。

        规则整体在 services.music_online.resolve_auto_quality_with_backfill。

        Returns:
            (补齐后的歌曲信息, 解析出的音质档位)
        """
        return music_online.resolve_auto_quality_with_backfill(online_info)

    def _music_play_online_url(self, online_info: OnlineMusicInfo):
        """触发在线歌曲播放：获取URL -> 下载到临时文件 -> 播放。

        原音源获取 URL 失败、或下载文件无效（HTML 错误页/试听片段）时，
        自动跨源兜底：在其它音源搜索同款歌并播放（YesPlayMusic UNM 风格）。
        """
        self._music_stop(instant=True)
        self._music_stream_seq += 1
        seq = self._music_stream_seq
        self._music_search_status.configure(text=_("music_loading_url"))
        # 用户原始选择（可能为 "auto"）：跨源兜底按此决定音质尝试顺序，
        # "auto" 时兜底从最高音质开始尝试，避免默认命中 128k
        raw_quality = self._music_quality_var.get()

        def _fetch_and_play():
            app = self  # 捕获主应用引用，避免线程间 self 丢失
            play_info = online_info
            play_quality = raw_quality
            if raw_quality == "auto":
                # 歌单/历史恢复的歌曲可能缺少音质信息（types），
                # 在后台线程重新搜索补齐后再解析自动音质，避免默认 128k
                play_info, play_quality = app._music_resolve_auto_quality_async(online_info)
            result_path, result_info, result_quality = app._fetch_online_song(play_info, play_quality, raw_quality)
            app.after(0, lambda: app._music_on_stream_ready(seq, result_path, result_info, online_info, result_quality))

        threading.Thread(target=_fetch_and_play, daemon=True).start()

    def _fetch_online_song(
        self, online_info: OnlineMusicInfo, quality: str, fallback_quality: Optional[str] = None
    ) -> Tuple[Optional[str], OnlineMusicInfo, str]:
        """获取在线歌曲并下载到临时文件，失败时自动跨源兜底。

        Args:
            online_info: 用户点播的歌曲信息
            quality: 原音源实际尝试的音质（"auto" 已解析为具体档位）
            fallback_quality: 跨源兜底的音质选择（可为 "auto"，表示
                从最高可用音质开始尝试；None 时沿用 quality）

        Returns:
            (临时文件路径, 实际播放的歌曲信息, 实际音质)：
            全部失败时路径为 None（info 保持原值）
        """
        # 编排（取流 -> 跨源兜底 -> B站风控重试 -> 汇总日志）整体在
        # services.music_download.fetch_online_song。临时文件表由界面自己那个
        # list 对象经 ctx 交进去；B站风控那条路要弹窗，所以把界面的
        # _music_try_bili_risk_retry 作为 risk_retry 传进去（它是必填参数，
        # 服务不给自己留一条「静默跳过风控」的默认路径）。
        return music_download.fetch_online_song(
            online_info,
            quality,
            fallback_quality,
            risk_retry=self._music_try_bili_risk_retry,
            ctx=self._music_download_ctx,
            notify_source=self._notify_fallback_source,
        )

    def _resolve_fallback(self, online_info: OnlineMusicInfo, quality: str) -> Optional[Tuple[OnlineMusicInfo, str]]:
        """跨源兜底解析：返回 (匹配歌曲, 播放URL) 或 None

        真正的搜索/候选筛选/逐档位试 URL 在 services.music_source.resolve_track；
        这层 try/except 与日志在 services.music_download.resolve_fallback。
        """
        return music_download.resolve_fallback(online_info, quality)

    def _download_fallback_result(
        self, fallback: Tuple[OnlineMusicInfo, str], quality: str
    ) -> Optional[Tuple[str, OnlineMusicInfo, str]]:
        """下载兜底结果并校验，成功返回 (临时文件路径, 实际歌曲信息, 音质)

        跨源兜底内部会尝试多个音质，无法精确得知最终命中档位，
        此处沿用用户请求的音质用于显示。

        编排（文件头 + 时长双重校验、失败即删临时文件）在
        services.music_download.download_fallback_result；「已切换到其它音源」
        的状态栏提示留在界面（_notify_fallback_source，含 i18n 与 after）。
        """
        return music_download.download_fallback_result(
            fallback,
            quality,
            ctx=self._music_download_ctx,
            notify_source=self._notify_fallback_source,
        )

    def _music_try_bili_risk_retry(
        self, online_info: OnlineMusicInfo, quality: str, fallback_quality: Optional[str] = None
    ) -> Optional[Tuple[str, OnlineMusicInfo, str]]:
        """B站风控验证：弹窗提示 + 浏览器滑块验证，通过后带 grisk_id 自动重试兜底。

        Returns:
            验证通过且兜底成功: (临时文件路径, 实际歌曲信息, 音质)；否则 None

        编排（取风控参数 -> 跑验证流程 -> 换 grisk_id -> 重试兜底）在
        services.music_download.try_bili_risk_retry；本方法只把三个对话框动作
        接进去 —— 服务在**与原文相同的时刻**调用它们（开窗在起验证流程之前、
        关窗在 finally 里），切主线程（after）在这里做。
        """
        return music_download.try_bili_risk_retry(
            online_info,
            quality,
            fallback_quality,
            ctx=self._music_download_ctx,
            open_dialog=lambda ref, ev: self.after(0, lambda: self._music_open_risk_dialog(ref, ev)),
            update_dialog=lambda ref, text: self.after(
                0, lambda: self._music_update_risk_dialog(ref, text)
            ),
            close_dialog=lambda ref: self.after(0, lambda: self._music_close_risk_dialog(ref)),
        )

    def _music_open_risk_dialog(self, dialog_ref: Dict[str, object], stop_event: threading.Event):
        """打开风控验证提示窗口"""
        try:
            dlg = ctk.CTkToplevel(self)
            dlg.title(_("music_risk_title"))
            dlg.geometry("400x190")
            dlg.resizable(False, False)
            dlg.transient(self)
            dlg.attributes("-topmost", True)

            ctk.CTkLabel(
                dlg,
                text=_("music_risk_title"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=15, weight="bold"),
                text_color=COLORS["text_primary"],
            ).pack(pady=(20, 6))

            label = ctk.CTkLabel(
                dlg,
                text=_("music_risk_hint"),
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                text_color=COLORS["text_secondary"],
                wraplength=350,
                justify="center",
            )
            label.pack(padx=20, pady=(0, 14))

            def on_close():
                stop_event.set()
                try:
                    dlg.destroy()
                except Exception:
                    pass

            dlg.protocol("WM_DELETE_WINDOW", on_close)
            ctk.CTkButton(
                dlg,
                text=_("music_risk_cancel"),
                width=100,
                height=28,
                font=ctk.CTkFont(family=FONT_FAMILY, size=12),
                fg_color=COLORS["bg_light"],
                hover_color=COLORS["card_border"],
                command=on_close,
            ).pack(pady=(0, 16))
            dialog_ref["dialog"] = dlg
            dialog_ref["label"] = label
            self._theme_refs.append((label, {"text_color": "text_secondary"}))
        except Exception as e:
            logger.warning(f"打开风控验证窗口失败: {e}")
            dialog_ref["dialog"] = None

    def _music_update_risk_dialog(self, dialog_ref: Dict[str, object], text: str):
        dlg = dialog_ref.get("dialog")
        label = dialog_ref.get("label")
        if dlg is not None and dlg.winfo_exists() and label is not None:
            try:
                label.configure(text=text)
            except Exception:
                pass

    def _music_close_risk_dialog(self, dialog_ref: Dict[str, object]):
        dlg = dialog_ref.get("dialog")
        if dlg is not None:
            try:
                dlg.destroy()
            except Exception:
                pass

    def _notify_fallback_source(self, source_id: str):
        """左下角状态栏提示已切换到其它音源播放

        音源显示名的取值规则在 services.music_download.source_display_name；
        i18n 文案与 after 切主线程留在界面侧。
        """
        name = music_download.source_display_name(source_id)
        message = _("music_fallback_status", source=name)
        self.after(0, lambda: self.set_status(message, "info"))

    def _music_get_download_headers(self, source_id: str) -> Dict:
        """获取音源下载所需的附加请求头（如 B站 upos CDN 的 Referer）

        音源缺失/抛异常 -> 空 dict，规则在
        services.music_download.get_download_headers。
        """
        return music_download.get_download_headers(source_id)

    def _music_get_download_cookies(self, source_id: str) -> Optional[Dict]:
        """获取音源下载所需的附加 cookies（如 B站 dash URL 的 buvid 一致性）

        音源缺失/抛异常 -> None（不是空 dict，语义不同不能混），规则在
        services.music_download.get_download_cookies。
        """
        return music_download.get_download_cookies(source_id)

    def _try_download_from_source(self, online_info: OnlineMusicInfo, quality: str) -> Tuple[Optional[str], Optional[str], str]:
        """尝试从指定音源获取 URL 并下载，校验文件有效后返回 (临时文件路径, 实际URL, 实际音质)。

        音质回退顺序（flac -> 320k -> 128k）、下载、文件头与时长双重校验全在
        services.music_download.try_download_from_source。
        """
        return music_download.try_download_from_source(
            online_info, quality, ctx=self._music_download_ctx
        )

    def _discard_temp_file(self, temp_path: str):
        """删除无效的临时文件并移出缓存列表

        规则在 services.music_download.discard_temp_file；传的是界面自己那个
        list 对象，范围外的旧读者看到的仍是同一份内容。
        """
        music_download.discard_temp_file(temp_path, self._music_temp_files)

    def _music_on_stream_ready(
        self,
        seq: int,
        temp_path: Optional[str],
        online_info: OnlineMusicInfo,
        origin_info: Optional[OnlineMusicInfo] = None,
        quality: str = "",
    ):
        """流媒体文件下载完成回调（带请求序号守卫，旧请求不覆盖新播放）

        Args:
            seq: 播放请求序号
            temp_path: 下载好的临时文件路径
            online_info: 实际播放的歌曲信息（可能是跨源兜底后的）
            origin_info: 用户点播的原始歌曲信息（兜底时用于播放历史记录）
            quality: 实际获取到的音质档位（128k/320k/flac，用于显示）
        """
        # 序号守卫与「有没有取到文件」的判定在
        # services.music_online.stream_ready_action；控件写入留在界面侧。
        action = music_online.stream_ready_action(seq, self._music_stream_seq, temp_path)
        if action == music_online.STREAM_STALE:
            return  # 用户已切换播放目标，丢弃过期结果
        self._music_search_status.configure(text="")
        if action == music_online.STREAM_PLAY:
            self._play_online_file(temp_path, online_info, 0, history_origin=origin_info, quality=quality)
        else:
            self._music_search_status.configure(text=_("music_url_failed"))

    def _music_download_to_temp(
        self,
        url: str,
        name_hint: str = "",
        extra_headers: Optional[Dict] = None,
        extra_cookies: Optional[Dict] = None,
    ) -> Optional[str]:
        """下载在线音频流到临时文件

        Args:
            url: 音频流地址
            name_hint: 临时文件名提示
            extra_headers: 附加请求头（如 B站 upos CDN 需要 Referer）
            extra_cookies: 附加 cookies（如 B站 dash URL 的 buvid 与 cookie 一致性校验）

        整体（非音频响应快速失败、后缀判定、mkstemp 命名、空文件丢弃、m4a 转码、
        数量裁剪）在 services.music_download.download_to_temp；临时文件表用的是
        界面自己那个 list 对象（_music_download_ctx.temp_files），范围外的旧读者
        看到的仍是同一份内容。
        """
        return music_download.download_to_temp(
            url,
            name_hint,
            extra_headers,
            extra_cookies,
            ctx=self._music_download_ctx,
        )

    def _music_cleanup_temp_files(self):
        """清理所有缓存的临时文件（规则在 services.music_download.cleanup_temp_files）"""
        music_download.cleanup_temp_files(self._music_temp_files)

    def _stop_search_loading(self):
        """停止搜索加载状态（切换标签页时调用）"""
        if hasattr(self, "_music_search_busy"):
            self._music_search_busy = False
        if hasattr(self, "_music_search_btn") and self._music_search_btn.winfo_exists():
            self._music_search_btn.configure(state="normal", text=_("music_search_btn"))
        if hasattr(self, "_music_search_status") and self._music_search_status.winfo_exists():
            self._music_search_status.configure(text="")
        if hasattr(self, "_music_pager_frame"):
            self._music_rebuild_pager()

    # ═══════════════ 桌面歌词管理 ═══════════════

    def _music_toggle_desktop_lyric(self):
        # 「不存在或不可见 → 应变可见」的判据搬进 services.desktop_lyric.target_visible
        # （两个分支的先后顺序调换，语义与原文一致）
        if desktop_lyric.target_visible(self._music_desktop_lyric):
            self._music_show_desktop_lyric()
            self._music_dlrc_btn.configure(fg_color=COLORS["accent"])
        else:
            self._music_hide_desktop_lyric()
            self._music_dlrc_btn.configure(fg_color=COLORS["bg_light"])

    def _music_show_desktop_lyric(self):
        """显示桌面歌词窗口"""
        if not self._music_desktop_lyric:
            try:
                self._music_desktop_lyric = DesktopLyricWindow(self)
            except Exception as e:
                logger.warning(f"创建桌面歌词窗口失败: {e}")
                return
        # 「解析完了才推歌词行」搬进 services.desktop_lyric.ready_lines；
        # 它返回 parser.lines 本身（窗口存的是同一个 list 对象，原文如此）
        lines = desktop_lyric.ready_lines(self._music_lyric_parser)
        if lines is not None:
            self._music_desktop_lyric.set_lyric_lines(lines)
        self._music_desktop_lyric.show_lyric()
        self._start_lyric_poll()

    def _music_hide_desktop_lyric(self):
        if self._music_desktop_lyric:
            self._music_desktop_lyric.hide_lyric()

    def _music_destroy_desktop_lyric(self):
        if self._music_desktop_lyric:
            self._music_desktop_lyric.destroy_lyric()
            self._music_desktop_lyric = None

    # ═══════════════ 音效面板 ═══════════════

    def _music_open_fx_panel(self):
        """打开音效设置面板"""
        if hasattr(self, "_music_fx_window") and self._music_fx_window and self._music_fx_window.winfo_exists():
            self._music_fx_window.lift()
            self._music_fx_window.focus_force()
            return
        self._music_fx_window = ctk.CTkToplevel(self)
        self._music_fx_window.title("音效设置")
        self._music_fx_window.geometry("420x520")
        self._music_fx_window.resizable(False, False)
        self._music_fx_window.configure(fg_color=COLORS["card_bg"])
        self._music_fx_window.protocol("WM_DELETE_WINDOW", self._music_close_fx_panel)
        self._music_fx_window.grab_set()

        main = ctk.CTkFrame(self._music_fx_window, fg_color="transparent")
        main.pack(fill=ctk.BOTH, expand=True, padx=15, pady=15)

        self._build_fx_eq_section(main)
        self._build_fx_reverb_section(main)
        self._build_fx_pitch_section(main)
        self._build_fx_speed_section(main)

        # 底部: 重置按钮
        ctk.CTkButton(
            main,
            # 阶段 1.20 修正：这里原用 _("music_cache_clear")（"清除缓存"），
            # 但 command 是 _music_reset_fx，其 docstring 写明"重置所有音效"
            # （把 EQ / 混响 / 变调 / 变速全部复位）—— 按钮名与功能不符。
            text=_("music_fx_reset"),
            width=100,
            height=30,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            fg_color=COLORS["bg_light"],
            hover_color=COLORS["accent"],
            command=self._music_reset_fx,
        ).pack(pady=(15, 0))

        self._music_fx_window.after(100, lambda: self._music_fx_window.focus_force())

    def _music_close_fx_panel(self):
        if hasattr(self, "_music_fx_window") and self._music_fx_window:
            try:
                self._music_fx_window.grab_release()
                self._music_fx_window.destroy()
            except Exception:
                pass
            self._music_fx_window = None

    def _build_fx_eq_section(self, parent):
        s = self._music_effects.settings
        label_font = ctk.CTkFont(family=FONT_FAMILY, size=12, weight="bold")

        eq_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_dark"], corner_radius=8)
        eq_frame.pack(fill=ctk.X, pady=(0, 8))

        header = ctk.CTkFrame(eq_frame, fg_color="transparent")
        header.pack(fill=ctk.X, padx=10, pady=(8, 5))

        eq_enable_var = ctk.BooleanVar(value=s.eq_enabled)
        ctk.CTkCheckBox(
            header,
            text=_("music_eq_enable"),
            variable=eq_enable_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda: self._music_on_eq_toggle(eq_enable_var.get()),
        ).pack(side=ctk.LEFT)

        ctk.CTkLabel(header, text=_("music_eq"), font=label_font, text_color=COLORS["text_primary"]).pack(
            side=ctk.LEFT, padx=(10, 0)
        )

        # EQ 滑块
        eq_sliders_frame = ctk.CTkFrame(eq_frame, fg_color="transparent")
        eq_sliders_frame.pack(fill=ctk.X, padx=10, pady=(5, 10))

        self._music_eq_sliders = []
        for i, freq in enumerate(EQ_FREQS):
            col_frame = ctk.CTkFrame(eq_sliders_frame, fg_color="transparent")
            col_frame.pack(side=ctk.LEFT, expand=True, padx=1)

            slider = ctk.CTkSlider(
                col_frame,
                from_=EQ_GAIN_MIN,
                to=EQ_GAIN_MAX,
                width=16,
                height=120,
                orientation="vertical",
                command=lambda v, idx=i: self._music_on_eq_change(idx, v),
                fg_color=COLORS["bg_light"],
                progress_color=COLORS["accent"],
                button_color=COLORS["text_primary"],
            )
            slider.set(s.eq_gains[i])
            slider.pack()
            self._music_eq_sliders.append(slider)

            ctk.CTkLabel(
                col_frame,
                text=str(freq) if freq >= 1000 else f"{freq}",
                font=ctk.CTkFont(family=FONT_FAMILY, size=7),
                text_color=COLORS["text_secondary"],
            ).pack()

            ctk.CTkLabel(
                col_frame,
                text=music_effects_panel.eq_gain_text(s.eq_gains[i]),
                font=ctk.CTkFont(family=FONT_FAMILY, size=7),
                text_color=COLORS["text_secondary"],
            ).pack()

    def _build_fx_reverb_section(self, parent):
        s = self._music_effects.settings
        label_font = ctk.CTkFont(family=FONT_FAMILY, size=11)

        rv_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_dark"], corner_radius=8)
        rv_frame.pack(fill=ctk.X, pady=(0, 8))

        header = ctk.CTkFrame(rv_frame, fg_color="transparent")
        header.pack(fill=ctk.X, padx=10, pady=(8, 5))

        rv_enable_var = ctk.BooleanVar(value=s.reverb_enabled)
        ctk.CTkCheckBox(
            header,
            text=_("music_reverb"),
            variable=rv_enable_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda: self._music_on_reverb_toggle(rv_enable_var.get()),
        ).pack(side=ctk.LEFT)

        # Delay
        row1 = ctk.CTkFrame(rv_frame, fg_color="transparent")
        row1.pack(fill=ctk.X, padx=10, pady=(0, 3))
        ctk.CTkLabel(row1, text="Delay", font=label_font, text_color=COLORS["text_secondary"]).pack(side=ctk.LEFT)
        self._music_reverb_delay_label = ctk.CTkLabel(
            row1, text=music_effects_panel.reverb_delay_text(s.reverb_delay_ms), font=label_font, text_color=COLORS["text_secondary"]
        )
        self._music_reverb_delay_label.pack(side=ctk.RIGHT)
        delay_slider = ctk.CTkSlider(
            rv_frame,
            from_=10,
            to=200,
            height=14,
            command=lambda v: self._music_on_reverb_delay(v),
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
        )
        delay_slider.set(s.reverb_delay_ms)
        delay_slider.pack(fill=ctk.X, padx=10, pady=(0, 3))
        self._music_reverb_delay_slider = delay_slider

        # Decay
        row2 = ctk.CTkFrame(rv_frame, fg_color="transparent")
        row2.pack(fill=ctk.X, padx=10, pady=(0, 3))
        ctk.CTkLabel(row2, text="Decay", font=label_font, text_color=COLORS["text_secondary"]).pack(side=ctk.LEFT)
        self._music_reverb_decay_label = ctk.CTkLabel(
            row2, text=music_effects_panel.reverb_decay_text(s.reverb_decay), font=label_font, text_color=COLORS["text_secondary"]
        )
        self._music_reverb_decay_label.pack(side=ctk.RIGHT)
        decay_slider = ctk.CTkSlider(
            rv_frame,
            from_=0.1,
            to=0.9,
            height=14,
            command=lambda v: self._music_on_reverb_decay(v),
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
        )
        decay_slider.set(s.reverb_decay)
        decay_slider.pack(fill=ctk.X, padx=10, pady=(0, 3))
        self._music_reverb_decay_slider = decay_slider

        # Wet Level
        row3 = ctk.CTkFrame(rv_frame, fg_color="transparent")
        row3.pack(fill=ctk.X, padx=10, pady=(0, 8))
        ctk.CTkLabel(row3, text="Wet", font=label_font, text_color=COLORS["text_secondary"]).pack(side=ctk.LEFT)
        self._music_reverb_wet_label = ctk.CTkLabel(
            row3, text=music_effects_panel.reverb_wet_text(s.reverb_wet_level), font=label_font, text_color=COLORS["text_secondary"]
        )
        self._music_reverb_wet_label.pack(side=ctk.RIGHT)
        wet_slider = ctk.CTkSlider(
            rv_frame,
            from_=0.0,
            to=1.0,
            height=14,
            command=lambda v: self._music_on_reverb_wet(v),
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
        )
        wet_slider.set(s.reverb_wet_level)
        wet_slider.pack(fill=ctk.X, padx=10, pady=(0, 3))
        self._music_reverb_wet_slider = wet_slider

    def _build_fx_pitch_section(self, parent):
        s = self._music_effects.settings
        label_font = ctk.CTkFont(family=FONT_FAMILY, size=11)

        pitch_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_dark"], corner_radius=8)
        pitch_frame.pack(fill=ctk.X, pady=(0, 8))

        header = ctk.CTkFrame(pitch_frame, fg_color="transparent")
        header.pack(fill=ctk.X, padx=10, pady=(8, 5))

        pt_enable_var = ctk.BooleanVar(value=s.pitch_enabled)
        ctk.CTkCheckBox(
            header,
            text=_("music_pitch"),
            variable=pt_enable_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda: self._music_on_pitch_toggle(pt_enable_var.get()),
        ).pack(side=ctk.LEFT)

        self._music_pitch_label = ctk.CTkLabel(
            header, text=music_effects_panel.pitch_text(s.pitch_semitones), font=label_font, text_color=COLORS["text_secondary"]
        )
        self._music_pitch_label.pack(side=ctk.RIGHT)

        pitch_slider = ctk.CTkSlider(
            pitch_frame,
            from_=PITCH_MIN,
            to=PITCH_MAX,
            height=14,
            command=lambda v: self._music_on_pitch_change(v),
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
        )
        pitch_slider.set(s.pitch_semitones)
        pitch_slider.pack(fill=ctk.X, padx=10, pady=(0, 8))
        self._music_pitch_slider = pitch_slider

    def _build_fx_speed_section(self, parent):
        s = self._music_effects.settings
        label_font = ctk.CTkFont(family=FONT_FAMILY, size=11)

        speed_frame = ctk.CTkFrame(parent, fg_color=COLORS["bg_dark"], corner_radius=8)
        speed_frame.pack(fill=ctk.X)

        header = ctk.CTkFrame(speed_frame, fg_color="transparent")
        header.pack(fill=ctk.X, padx=10, pady=(8, 5))

        sp_enable_var = ctk.BooleanVar(value=s.speed_enabled)
        ctk.CTkCheckBox(
            header,
            # 阶段 1.20 修正：这是**变速**分区的标题（右侧显示 f"{speed_rate:.2f}x"），
            # 原用 _("music_pitch_label")（"变速/变调"，含变调字样）。
            # 真正的变调分区在 _build_fx_pitch_section，用的是 _("music_pitch")（"变调"）。
            text=_("music_speed"),
            variable=sp_enable_var,
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            fg_color=COLORS["accent"],
            hover_color=COLORS["accent_hover"],
            command=lambda: self._music_on_speed_toggle(sp_enable_var.get()),
        ).pack(side=ctk.LEFT)

        self._music_speed_label = ctk.CTkLabel(
            header, text=music_effects_panel.speed_text(s.speed_rate), font=label_font, text_color=COLORS["text_secondary"]
        )
        self._music_speed_label.pack(side=ctk.RIGHT)

        speed_slider = ctk.CTkSlider(
            speed_frame,
            from_=SPEED_MIN,
            to=SPEED_MAX,
            height=14,
            command=lambda v: self._music_on_speed_change(v),
            fg_color=COLORS["bg_light"],
            progress_color=COLORS["accent"],
        )
        speed_slider.set(s.speed_rate)
        speed_slider.pack(fill=ctk.X, padx=10, pady=(0, 8))
        self._music_speed_slider = speed_slider

    # ── 音效回调 ──

    def _music_on_eq_toggle(self, enabled: bool):
        self._music_effects.settings.eq_enabled = enabled

    def _music_on_eq_change(self, idx: int, value: float):
        self._music_effects.settings.eq_gains[idx] = value

    def _music_on_reverb_toggle(self, enabled: bool):
        self._music_effects.settings.reverb_enabled = enabled

    def _music_on_reverb_delay(self, value: float):
        self._music_effects.settings.reverb_delay_ms = value
        if hasattr(self, "_music_reverb_delay_label"):
            self._music_reverb_delay_label.configure(text=music_effects_panel.reverb_delay_text(value))

    def _music_on_reverb_decay(self, value: float):
        self._music_effects.settings.reverb_decay = value
        if hasattr(self, "_music_reverb_decay_label"):
            self._music_reverb_decay_label.configure(text=music_effects_panel.reverb_decay_text(value))

    def _music_on_reverb_wet(self, value: float):
        self._music_effects.settings.reverb_wet_level = value
        if hasattr(self, "_music_reverb_wet_label"):
            self._music_reverb_wet_label.configure(text=music_effects_panel.reverb_wet_text(value))

    def _music_on_pitch_toggle(self, enabled: bool):
        self._music_effects.settings.pitch_enabled = enabled

    def _music_on_pitch_change(self, value: float):
        self._music_effects.settings.pitch_semitones = value
        if hasattr(self, "_music_pitch_label"):
            self._music_pitch_label.configure(text=music_effects_panel.pitch_text(value))

    def _music_on_speed_toggle(self, enabled: bool):
        self._music_effects.settings.speed_enabled = enabled

    def _music_on_speed_change(self, value: float):
        self._music_effects.settings.speed_rate = value
        if hasattr(self, "_music_speed_label"):
            self._music_speed_label.configure(text=music_effects_panel.speed_text(value))

    def _music_reset_fx(self):
        """重置所有音效"""
        s = self._music_effects.settings
        # 「重置成哪些默认值」搬进 services.music_effects_panel.reset_effect_settings。
        # pan_enabled / pan_value 刻意不重置 —— 原文的 _music_reset_fx 也没碰它们。
        music_effects_panel.reset_effect_settings(s)

        # 更新UI滑块
        if hasattr(self, "_music_eq_sliders"):
            for sl in self._music_eq_sliders:
                sl.set(0)
        if hasattr(self, "_music_reverb_delay_slider"):
            self._music_reverb_delay_slider.set(60)
        if hasattr(self, "_music_reverb_decay_slider"):
            self._music_reverb_decay_slider.set(0.4)
        if hasattr(self, "_music_reverb_wet_slider"):
            self._music_reverb_wet_slider.set(0.3)
        if hasattr(self, "_music_pitch_slider"):
            self._music_pitch_slider.set(0)
        if hasattr(self, "_music_speed_slider"):
            self._music_speed_slider.set(1.0)

    def _music_cleanup_fx_files(self):
        """清理音效处理产生的临时文件"""
        # 「存在才删 + 吞异常 + 清空」搬进 services.music_effects_panel.cleanup_temp_files；
        # 它就地清空界面这个 list，范围外的旧读者看到的仍是同一个对象。
        music_effects_panel.cleanup_temp_files(self._music_effects_processed_files)
        self._music_effects.cleanup()

    # ═══════════════ 清理 ═══════════════

    def _music_cleanup(self):
        self._music_stop(instant=True)
        self._stop_lyric_poll()
        self._update_music_footer()
        self._unregister_hotkeys()
        self._music_stop_periodic_save()
        self._save_music_state()
        # 退出前强制写盘
        if hasattr(self, "_music_playlist_manager"):
            self._music_playlist_manager.save()
        self._music_cleanup_temp_files()
        self._music_cleanup_fx_files()
        self._music_destroy_desktop_lyric()

    def _update_music_footer(self):
        if not hasattr(self, "_music_footer_frame"):
            return
        # 可见性判定 + 「歌名 - 歌手」拼装与 40 字符截断 + 播放/暂停字形，
        # 搬进 services.music_local.footer_plan；元数据只在本地分支才取
        # （read_metadata 是注入缝，读名仍是 self._get_metadata）。
        plan = music_local.footer_plan(
            path=self._get_current_file(),
            is_online_playing=self._music_is_online_playing,
            online_info=self._music_current_online_info,
            is_playing=self._music_is_playing,
            is_paused=self._music_is_paused,
            read_metadata=self._get_metadata,
        )
        if not plan.visible:
            try:
                for _w in [
                    self._music_footer_frame,
                    self._music_footer_label,
                    self._music_footer_prev,
                    self._music_footer_play,
                    self._music_footer_next,
                ]:
                    _w.pack_forget()
            except Exception:
                pass
            return
        self._music_footer_label.configure(text=plan.text)
        if not self._music_footer_frame.winfo_ismapped():
            self._music_footer_frame.pack(side=ctk.LEFT, expand=True)
            self._music_footer_label.pack(side=ctk.RIGHT, padx=(0, 5))
            self._music_footer_next.pack(side=ctk.RIGHT, padx=1)
            self._music_footer_play.pack(side=ctk.RIGHT, padx=1)
            self._music_footer_prev.pack(side=ctk.RIGHT, padx=1)
        self._music_footer_play.configure(text=plan.play_text)

    def _on_footer_music_toggle(self):
        self._music_toggle_play()
        self._update_music_footer()

    def _on_footer_music_prev(self):
        self._music_prev()
        self._update_music_footer()

    def _on_footer_music_next(self):
        self._music_next()
        self._update_music_footer()
