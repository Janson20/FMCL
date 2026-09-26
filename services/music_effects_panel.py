"""音效**面板**的零 GUI 逻辑（阶段 1 任务 1.4-C，形态 2：逻辑与界面切分）。

宿主是 `ui/app_music.py` 的：

* `_build_fx_eq_section` / `_build_fx_reverb_section` / `_build_fx_pitch_section` /
  `_build_fx_speed_section`（构建分区时读设置值 → 显示文案）；
* `_music_on_reverb_delay` / `_music_on_reverb_decay` / `_music_on_reverb_wet` /
  `_music_on_pitch_change` / `_music_on_speed_change`（滑块回调时算同一份文案）；
* `_music_reset_fx`（重置成哪些默认值）；
* `_music_cleanup_fx_files`（删临时文件）。

## 为什么不直接写进 `services/music_effects.py`

那才是音效的"老家"，但 1.4-A 的永久守卫
``tests/test_services_relocation.py::test_implementation_kept_the_original_line_count``
把 ``services/music_effects.py`` 的**非空行数钉死在 `git HEAD:ui/music_effects.py`**
（"整体搬家"不变式），例外登记表 ``REGISTERED_LINE_DELTAS`` 又住在禁改的
``tests/test_services_*.py`` 里。于是 1.4-C 的追加只能另开一个模块 ——
这不是"为了分层而分层"，是被那条守卫逼出来的（实测行数差见
``poc/_report_1_4c.md``）。

## 切缝判据（与 1.4-A / 1.4-B 同一条）

`CTkFrame` / `CTkSlider` / `CTkCheckBox` / `.set()` / `.configure()` 留在界面侧；
搬进来的是**设置值 → 显示文案**的格式化、**重置成哪些默认值**、**临时文件清理**。
原文有 6 处格式串在"构建"与"回调"里各写了一份，现在只有一份唯一真相。

本模块零 UI、零线程、零定时器、零 i18n。
"""

from __future__ import annotations

import os
from typing import List

from services.music_effects import EffectSettings


def eq_gain_text(gain: float) -> str:
    """EQ 单段增益的显示文案（原文 ``f"{s.eq_gains[i]:+.0f}"``）。"""
    return f"{gain:+.0f}"


def reverb_delay_text(delay_ms: float) -> str:
    """混响延迟的显示文案（原文 ``f"{s.reverb_delay_ms:.0f}ms"``）。"""
    return f"{delay_ms:.0f}ms"


def reverb_decay_text(decay: float) -> str:
    """混响衰减的显示文案（原文 ``f"{s.reverb_decay:.1f}"``）。"""
    return f"{decay:.1f}"


def reverb_wet_text(wet_level: float) -> str:
    """混响湿信号比例的显示文案（原文 ``f"{s.reverb_wet_level:.1f}"``）。"""
    return f"{wet_level:.1f}"


def pitch_text(semitones: float) -> str:
    """变调半音的显示文案（原文 ``f"{s.pitch_semitones:+.1f} semitones"``）。"""
    return f"{semitones:+.1f} semitones"


def speed_text(rate: float) -> str:
    """变速倍率的显示文案（原文 ``f"{s.speed_rate:.2f}x"``）。"""
    return f"{rate:.2f}x"


def reset_effect_settings(settings: EffectSettings) -> None:
    """把 `_music_reset_fx` 会重置的那 10 个字段恢复默认值（**就地改**，不换对象）。

    原文逐字搬来。两处**刻意的不同**：

    * ``pan_enabled`` / ``pan_value`` **不重置** —— 原文的 `_music_reset_fx` 也没碰它们
      （界面上没有声像控件；`AudioEffectProcessor.reset()` 换整个对象是另一条路径）；
    * ``eq_gains`` 重新绑定一个**新** list（``[0.0] * 10``），与原文一致；
      持有旧 list 引用的读者看到的是旧值 —— 原文同样如此。
    """
    settings.eq_enabled = False
    settings.eq_gains = [0.0] * 10
    settings.reverb_enabled = False
    settings.reverb_delay_ms = 60.0
    settings.reverb_decay = 0.4
    settings.reverb_wet_level = 0.3
    settings.pitch_enabled = False
    settings.pitch_semitones = 0.0
    settings.speed_enabled = False
    settings.speed_rate = 1.0


def cleanup_temp_files(paths: List[str]) -> None:
    """删除音效处理产生的临时文件并清空这个 list（**就地**清空，与界面共享同一对象）。

    原文 `_music_cleanup_fx_files` 的循环体：存在才删、任何删除异常一律吞掉，
    删完再 ``paths.clear()``。``paths`` 由界面传入它自己那个 list，
    所以界面侧范围外的旧读者看到的仍是同一份内容。
    """
    for fp in paths:
        try:
            if os.path.exists(fp):
                os.remove(fp)
        except Exception:
            pass
    paths.clear()


__all__ = [
    "cleanup_temp_files",
    "eq_gain_text",
    "pitch_text",
    "reset_effect_settings",
    "reverb_decay_text",
    "reverb_delay_text",
    "reverb_wet_text",
    "speed_text",
]
