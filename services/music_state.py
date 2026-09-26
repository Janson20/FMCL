"""音乐播放状态的**读写规则**（阶段 1 任务 1.4-A，形态 3：服务 + 持久化）。

对应宿主是 `ui/app_music.py` 里的四个方法：`_save_music_state` /
`_save_music_state_later` / `_load_music_state` / `_music_apply_wy_saved_login`，
以及 `_music_start_periodic_save` 的周期参数。

## 切缝位置与理由（实测过的，不是猜的）

落盘本身**不在这里**：真正的写入路径是
`ui/app_music.py` → `self.callbacks["save_music_state"](state)` →
`launcher/core.py:save_music_state()` → `config.music_state` + `config.save_config()`。
服务层不 import `launcher`，所以"读什么键/缺键怎么办/坏 JSON 怎么办"这一层搬进来，
"取 payload → 交给回调 → 写文件"留在界面侧。

payload 的**取值**也留在界面侧：`music_volume` / `music_current_index` /
`music_playlist_context_idx` 这些属性被 100+ 个范围外方法读写（例如
`_update_mute_btn_ui` 读 `_music_volume`、`_play_from_index` 写
`_music_current_index`），把所有权搬走等于要求同时改写那些方法 —— 那是 1.4-B 的量。
所以 `build_music_state()` 是一组**关键字参数**，界面把当前值传进来；
键名、默认值、`PLAY_MODE_NAMES` 映射、"歌单上下文为空则记 -1"这类规则在服务里。

## 逐字保留的容错分支（有测试钉住）

``parse_music_state`` 把 `_load_music_state` 里的 ``state.get(...)`` 逐条搬过来，
**默认值一字不改**：``music_last_folder`` 默认 ``""``、``music_volume`` 默认 ``None``
（None 与 0 语义不同：None 表示"不改音量"）、``music_play_mode`` 默认 ``"loop_list"``、
``music_current_index`` 默认 ``-1``、``music_progress`` 默认 ``0``、
``music_mini_mode`` 默认 ``False``、``music_last_song_idx_in_playlist`` 默认 ``-1``。
坏 JSON 由**调用方**（`launcher/core.py` 的配置层）负责吞掉并返回空 dict；
界面侧的 ``if not state: return`` 与本模块 ``parse_music_state({})`` 的默认值
共同保证"读不进就保持默认"。往返测试见 `tests/test_music_service.py`。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from logzero import logger

from services.music_player import PLAY_MODE_IDS, PLAY_MODE_LOOP_LIST, PLAY_MODE_NAMES

#: `_save_music_state_later` 的防抖延迟（毫秒，原文写死 500）
SAVE_DEBOUNCE_MS = 500

#: `_music_start_periodic_save` 的周期（毫秒，原文写死 30000）
PERIODIC_SAVE_INTERVAL_MS = 30000

#: 启动期"回调未就绪就重试"的参数（原文写死 `_retry_count < 60` / `after(500, ...)`）
LOAD_RETRY_MAX = 60
LOAD_RETRY_MS = 500

#: payload 的键名（原文 `_save_music_state` 里写死的八个键）
KEY_LAST_FOLDER = "music_last_folder"
KEY_CURRENT_INDEX = "music_current_index"
KEY_PROGRESS = "music_progress"
KEY_VOLUME = "music_volume"
KEY_PLAY_MODE = "music_play_mode"
KEY_MINI_MODE = "music_mini_mode"
KEY_LAST_PLAYLIST_ID = "music_last_playlist_id"
KEY_LAST_SONG_IDX = "music_last_song_idx_in_playlist"

#: 网易云登录 Cookie 的回调名（原文 `self.callbacks.get("get_wy_cookie")`）
CALLBACK_WY_COOKIE = "get_wy_cookie"


def build_music_state(
    *,
    last_folder: str,
    current_index: int,
    progress: float,
    volume: float,
    play_mode: int,
    mini_mode: bool,
    playlist_id: Optional[str],
    playlist_context_idx: int,
    playlist_context_songs: List[Any],
) -> Dict[str, Any]:
    """组装落盘 payload（键名与取值规则逐字照搬原文 `_save_music_state`）。

    ``music_play_mode`` 存的是**字符串名**而不是数字（``PLAY_MODE_NAMES`` 映射），
    未知模式回退 ``"loop_list"``；``music_last_song_idx_in_playlist`` 在
    "没在播歌单上下文"时记 ``-1``。
    """
    return {
        KEY_LAST_FOLDER: last_folder,
        KEY_CURRENT_INDEX: current_index,
        KEY_PROGRESS: progress,
        KEY_VOLUME: volume,
        KEY_PLAY_MODE: PLAY_MODE_NAMES.get(play_mode, "loop_list"),
        KEY_MINI_MODE: mini_mode,
        KEY_LAST_PLAYLIST_ID: playlist_id,
        KEY_LAST_SONG_IDX: playlist_context_idx if playlist_context_songs else -1,
    }


@dataclass
class ParsedMusicState:
    """`_load_music_state` 需要的全部字段（默认值与原文逐条对应）。"""

    last_folder: str = ""
    #: None 表示"状态里没写音量"→ 调用方不要改当前音量（原文 ``vol = state.get(..., None)``）
    volume: Optional[float] = None
    play_mode: int = PLAY_MODE_LOOP_LIST
    current_index: int = -1
    progress: float = 0
    mini_mode: bool = False
    last_playlist_id: Optional[str] = None
    song_index: int = -1
    #: 原始 dict（原文只读上面这些键；留一份便于诊断与将来扩展）
    raw: Dict[str, Any] = field(default_factory=dict)


def parse_music_state(state: Dict[str, Any]) -> ParsedMusicState:
    """解析落盘状态（默认值/容错分支逐字照搬原文 `_load_music_state`）。

    未知的 ``music_play_mode`` 名字回退到 ``PLAY_MODE_LOOP_LIST``
    （原文 ``mode_map.get(mode, PLAY_MODE_LOOP_LIST)``）。
    """
    state = state or {}
    mode_name = state.get(KEY_PLAY_MODE, "loop_list")
    return ParsedMusicState(
        last_folder=state.get(KEY_LAST_FOLDER, ""),
        volume=state.get(KEY_VOLUME, None),
        play_mode=PLAY_MODE_IDS.get(mode_name, PLAY_MODE_LOOP_LIST),
        current_index=state.get(KEY_CURRENT_INDEX, -1),
        progress=state.get(KEY_PROGRESS, 0),
        mini_mode=state.get(KEY_MINI_MODE, False),
        last_playlist_id=state.get(KEY_LAST_PLAYLIST_ID),
        song_index=state.get(KEY_LAST_SONG_IDX, -1),
        raw=dict(state),
    )


def volume_to_slider(volume: float) -> int:
    """音量（0..1 浮点）→ 滑杆整数百分比（原文两处 ``int(self._music_volume * 100)``）。"""
    return int(volume * 100)


def login_retry_due(callbacks: Optional[Dict[str, Any]], retry_count: int) -> bool:
    """`_music_apply_wy_saved_login` 的重试判据。

    原文两处：``if not hasattr(self, "callbacks") or not self.callbacks:``（重试）
    与 ``get_fn = self.callbacks.get("get_wy_cookie")`` 缺失（重试），
    条件都是 ``_retry_count < 60``。callbacks 还没就绪时 `get_fn` 也取不到，
    所以两者归并成同一个判据。
    """
    if retry_count >= LOAD_RETRY_MAX:
        return False
    if not callbacks:
        return True
    return callbacks.get(CALLBACK_WY_COOKIE) is None


def read_saved_cookie(callbacks: Dict[str, Any]) -> Optional[str]:
    """从回调里读已保存的网易云 Cookie（原文 try/except + 日志）。

    异常时返回 None（原文 ``logger.debug(f"读取网易云登录 Cookie 失败: {e}")`` 后 return）。
    """
    get_fn = callbacks.get(CALLBACK_WY_COOKIE)
    if not get_fn:
        return None
    try:
        cookie = get_fn()
    except Exception as e:
        logger.debug(f"读取网易云登录 Cookie 失败: {e}")
        return None
    return cookie or None


def apply_wy_cookie(cookie: str) -> bool:
    """把 Cookie 应用到网易云音源会话（原文函数体内的延迟 import + try/except）。

    改动点：原文 import 的是 `ui.music_source.wy_apply_cookie`，音源适配整包已在
    任务 1.4 搬进 `services/music_source/`，这里改指服务层（`ui.music_source`
    现在只是转发 shim，两个名字指向同一模块）。
    """
    try:
        from services.music_source import wy_apply_cookie

        wy_apply_cookie(cookie)
        logger.info("已应用网易云音乐登录 Cookie")
        return True
    except Exception as e:
        logger.warning(f"应用网易云音乐登录 Cookie 失败: {e}")
        return False


__all__ = [
    "CALLBACK_WY_COOKIE",
    "KEY_CURRENT_INDEX",
    "KEY_LAST_FOLDER",
    "KEY_LAST_PLAYLIST_ID",
    "KEY_LAST_SONG_IDX",
    "KEY_MINI_MODE",
    "KEY_PLAY_MODE",
    "KEY_PROGRESS",
    "KEY_VOLUME",
    "LOAD_RETRY_MAX",
    "LOAD_RETRY_MS",
    "PERIODIC_SAVE_INTERVAL_MS",
    "ParsedMusicState",
    "SAVE_DEBOUNCE_MS",
    "apply_wy_cookie",
    "build_music_state",
    "login_retry_due",
    "parse_music_state",
    "read_saved_cookie",
    "volume_to_slider",
]
