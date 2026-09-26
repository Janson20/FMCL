"""歌词**显示侧**的零 GUI 逻辑（阶段 1 任务 1.4-C，形态 2：逻辑与界面切分）。

宿主是 `ui/app_music.py` 的 `_start_lyric_poll` / `_stop_lyric_poll` /
`_poll_lyric_progress` / `_update_lyric_display` / `_fetch_and_start_lyric`。

## 为什么不直接写进 `services/music_lyrics.py`

那才是歌词的"老家"，但 1.4-A 的永久守卫
``tests/test_services_relocation.py::test_implementation_kept_the_original_line_count``
把 ``services/music_lyrics.py`` 的**非空行数钉死在 `git HEAD:ui/music_lyrics.py`**
（"整体搬家"不变式：内容整体搬走、没有删改），例外登记表
``REGISTERED_LINE_DELTAS`` 又住在禁改的 ``tests/test_services_*.py`` 里。
于是 1.4-C 的追加只能另开一个模块 —— 这不是"为了分层而分层"，是被那条守卫
逼出来的；本模块的实测行数差已记在 ``poc/_report_1_4c.md``。

## 切缝判据（与 1.4-A / 1.4-B 同一条）

> **这段代码是否 import GUI（customtkinter / tkinter / CTk*）？是否直接创建/销毁/配置
> 控件？是否 `self.after(...)` 排期、是否读 `winfo_exists()`？**

* 搬进来：**取哪一行歌词、副文本取翻译还是罗马音、下一次轮询隔多久、该不该停、
  取词并装载**；
* 留在界面：`after` / `after_cancel`、`configure(text=...)`、`threading.Thread`、
  以及桌面歌词窗口的 `update_progress`。

因此本模块零 UI、零线程、零定时器、零 i18n（文案由界面侧取好传进来）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from services.music_lyrics import LyricParser

#: 播放中、且（音乐标签页可见 或 桌面歌词可见）时的轮询间隔
#: （原文 `_poll_lyric_progress` 里写死 100）
POLL_ACTIVE_MS = 100

#: 音乐标签页不可见且没有桌面歌词时的轮询间隔（降频但仍轮询，
#: 以便切回来时立刻是新歌词；原文写死 300）
POLL_IDLE_MS = 300


@dataclass(frozen=True)
class LyricDisplayPlan:
    """内嵌歌词一次刷新的显示取值（原文 `_update_lyric_display`）。

    ``text`` 为空表示"当前时刻没有歌词行"或"该行文本为空" —— 两种情况下原文
    都是把主副两个标签置空（主标签 `configure(text="")`、副标签 `configure(text="")`），
    所以界面按同一个分支处理即可。
    """

    text: str
    trans_text: str


def display_plan(
    parser: LyricParser,
    elapsed_ms: int,
    *,
    show_translation: bool,
    show_roma: bool,
) -> LyricDisplayPlan:
    """算出此刻该显示的歌词主文本与副文本。

    副文本优先翻译；翻译为空、或翻译非空但**没开翻译开关**时才看罗马音
    （原文 `if show_translation and current.translation: ... elif show_roma and
    current.roma: ...` —— 注意这不是"翻译为空才看罗马音"）。
    """
    current = parser.get_line_at(elapsed_ms)
    if current is None:
        return LyricDisplayPlan(text="", trans_text="")
    trans = ""
    if show_translation and current.translation:
        trans = current.translation
    elif show_roma and current.roma:
        trans = current.roma
    return LyricDisplayPlan(text=current.text, trans_text=trans)


@dataclass(frozen=True)
class LyricPollPlan:
    """下次歌词轮询的取值（界面照着排定时器，自己不重算这些判定）。

    * ``elapsed_ms`` 为 ``None`` → 本次**只排下一次**，不刷新显示
      （音乐标签页与桌面歌词都不可见时，原文走这条分支）；
    * ``delay_ms`` 是下一次 `after` 的毫秒数。
    """

    delay_ms: int
    elapsed_ms: Optional[int]


def should_stop_polling(*, is_playing: bool, is_paused: bool) -> bool:
    """歌词轮询该不该停（原文 ``if not self._music_is_playing or self._music_is_paused``）。

    判据在这里，真正的 ``after_cancel`` 仍在界面侧 —— 服务不碰定时器。
    """
    return (not is_playing) or is_paused


def poll_plan(*, tab_active: bool, desktop_visible: bool, progress: float) -> LyricPollPlan:
    """下一次轮询的间隔，以及本次要显示的播放时刻。

    1. 音乐标签页不可见 **且** 桌面歌词不可见 → 降频到 `POLL_IDLE_MS`，本次不刷新；
    2. 其余 → 常速 `POLL_ACTIVE_MS`，并给出 ``int(progress * 1000)``。

    第 2 条**总是**给 ``elapsed_ms``（哪怕算出 0）：原文无条件调用
    `_update_lyric_display`，用"0 视为假"来判断会漏掉进度为 0 时的首行歌词。

    本函数刻意**不**接收播放态：``desktop_visible`` 要读 Tk 的 `winfo_exists()`，
    界面侧先判"该不该停"（`should_stop_polling`）并直接 return，才不会在没播放时
    白读一次 Tk 状态 —— 这与原文的短路求值逐字一致。
    """
    if not tab_active and not desktop_visible:
        return LyricPollPlan(delay_ms=POLL_IDLE_MS, elapsed_ms=None)
    return LyricPollPlan(delay_ms=POLL_ACTIVE_MS, elapsed_ms=int(progress * 1000))


def load_lyric(
    parser: LyricParser, online_info: Any, sources: Optional[Mapping[str, Any]] = None
) -> bool:
    """取在线歌词文本并装进解析器；返回是否真的装入了歌词。

    对应原文 `_fetch_and_start_lyric` 的线程体（音源查找 + 空文本判定 + 清空并解析），
    **异常一律吞掉并返回 False**（原文 `except Exception: pass`；`from_online_info`
    之类的鸭子类型错误也一并吞掉）。

    音源表通过 ``sources`` 注入：界面侧传自己模块里的那个名字
    （`ui/app_music.py` 的 `MUSIC_SOURCES`），从而保持原来的补丁点不变；
    不传时才回落到 `services.music_source.MUSIC_SOURCES`。
    """
    try:
        if sources is None:
            from services import music_source

            sources = music_source.MUSIC_SOURCES
        src = sources.get(online_info.source)
        if not src:
            return False
        lrc_text = src.get_lyric(online_info)
        if not lrc_text:
            return False
        parser.clear()
        parser.parse(lrc_text)
        return True
    except Exception:
        return False


__all__ = [
    "POLL_ACTIVE_MS",
    "POLL_IDLE_MS",
    "LyricDisplayPlan",
    "LyricPollPlan",
    "display_plan",
    "load_lyric",
    "poll_plan",
    "should_stop_polling",
]
