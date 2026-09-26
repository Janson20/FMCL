"""音乐播放全局热键（阶段 1 任务 1.4-C，形态 2：逻辑与界面切分）。

宿主是 `ui/app_music.py` 的 `_register_hotkeys` / `_unregister_hotkeys` 与 7 个
`_music_hotkey_*` 回调。

## 搬进来什么、留下什么

| 搬进来（纯数据 + 编排）                                  | 留在 `ui/app_music.py`（Tk 契约）                                    |
| -------------------------------------------------------- | -------------------------------------------------------------------- |
| 7 个动作的组合键表 `DEFAULT_HOTKEYS`                      | `keyboard.hook(...)` 预热与 `time.sleep(0.1)`                        |
| 动作顺序 `HOTKEY_ACTIONS`（注册与注销共用同一份）          | `threading.Thread(...)`（注册要起后台线程，**服务零线程**）           |
| `register_all` / `unregister_all` 的遍历编排              | 降级开关 `_keyboard_available`（界面模块级全局）与平台分支日志         |
| 音量热键的步长 `VOLUME_HOTKEY_STEP`                       | `self.after(0, ...)` 切回主线程（7 个 `_music_hotkey_*` 都只做这件事） |

原文把 7 个组合键**写了三遍**（表 + 注册 7 行 + 注销 7 行），三处必须同时改才不会
错位。现在键位与顺序只有这一份，注册与注销共用。

## 为什么 `backend` 要当参数传进来

`keyboard` 是**可选依赖**（`ui/app_music.py` 里 `import keyboard` 失败会把
`_keyboard_available` 置 False 并跳过注册），而且它的降级开关是**界面模块的模块级
全局**（`global _keyboard_available`），服务不该去改一个 UI 模块的全局量。
把 backend 当参数注入，服务只负责"按表调用 add/remove"，降级判定仍归界面。
测试注入一个记录调用的替身即可，不必装 `keyboard` 也不碰真实键盘钩子。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Tuple

#: 动作 → 组合键。**逐字保留**原文 `ui/app_music.py` 里的那张表（唯一真相搬到这里）。
DEFAULT_HOTKEYS: Dict[str, str] = {
    "play_pause": "ctrl+shift+space",
    "prev": "ctrl+shift+left",
    "next": "ctrl+shift+right",
    "stop": "ctrl+shift+down",
    "vol_up": "ctrl+shift+up",
    "vol_down": "ctrl+shift+page down",
    "vol_mute": "ctrl+shift+m",
}

#: 注册/注销的**动作顺序**（原文 `add_hotkey` / `remove_hotkey` 的调用顺序，逐字保留）。
#: 顺序有意义：`unregister_all` 在一个 try 块里跑，前面的抛异常后面的就不会被调用。
HOTKEY_ACTIONS: Tuple[str, ...] = (
    "play_pause",
    "prev",
    "next",
    "stop",
    "vol_up",
    "vol_down",
    "vol_mute",
)

#: 音量热键每次调整的百分点（原文两处写死 `self._adjust_volume(±5)`）。
VOLUME_HOTKEY_STEP = 5


def hotkey_combos() -> List[str]:
    """按注册顺序列出全部组合键（供界面显示/自检，不参与注册本身）。"""
    return [DEFAULT_HOTKEYS[action] for action in HOTKEY_ACTIONS]


def register_all(backend: Any, handlers: Mapping[str, Callable[[], Any]]) -> None:
    """按 `HOTKEY_ACTIONS` 顺序把 7 个热键注册到 ``backend``（原文 `add_hotkey` 七连）。

    ``handlers`` 由界面侧给出（动作名 → 该界面自己的回调）。``backend`` 抛异常时
    **不做任何吞异常处理** —— 原文把整段包在自己的 try 里，由界面决定降级策略。
    """
    for action in HOTKEY_ACTIONS:
        backend.add_hotkey(DEFAULT_HOTKEYS[action], handlers[action])


def unregister_all(backend: Any) -> None:
    """按 `HOTKEY_ACTIONS` 顺序注销 7 个热键（原文 `remove_hotkey` 七连）。

    同样不吞异常：原文这 7 行在一个 try 块里，某一行的异常会中断后面的注销
    并把已经置好的"已注册"标记留在原地 —— 界面侧照抄这个边界。
    """
    for action in HOTKEY_ACTIONS:
        backend.remove_hotkey(DEFAULT_HOTKEYS[action])


__all__ = [
    "DEFAULT_HOTKEYS",
    "HOTKEY_ACTIONS",
    "VOLUME_HOTKEY_STEP",
    "hotkey_combos",
    "register_all",
    "unregister_all",
]
