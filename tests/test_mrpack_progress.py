"""模组加载器进度映射的回归守卫（2026-10-23 用户反馈）。"""

from __future__ import annotations

import threading

from launcher.mrpack import MrpackMixin


class DummyMrpack(MrpackMixin):
    def __init__(self) -> None:
        self._mp_progress = {
            "phase": "loader",
            "overall": self.MODPACK_PARALLEL_WEIGHT,
            "loader_current": 0,
            "loader_max": 1,
        }


def test_loader_progress_fills_the_last_tenth() -> None:
    dummy = DummyMrpack()
    calls = []
    callback = dummy._make_modpack_loader_callback(
        {
            "setMax": lambda n: calls.append(("max", n)),
            "setProgress": lambda n: calls.append(("progress", n)),
            "setStatus": lambda s: calls.append(("status", s)),
        },
        threading.Lock(),
    )

    callback["setMax"](10)
    assert dummy._mp_progress["overall"] == 0.9
    callback["setProgress"](5)
    assert dummy._mp_progress["overall"] == 0.95
    callback["setStatus"]("安装 Forge")
    assert dummy._mp_progress["loader_label"] == "安装 Forge"
    callback["setProgress"](20)  # 超过 max 要夹到 100%
    assert dummy._mp_progress["overall"] == 1.0
    assert calls[:2] == [("max", 10), ("progress", 5)]


def test_loader_progress_stays_in_loader_segment_without_max() -> None:
    dummy = DummyMrpack()
    callback = dummy._make_modpack_loader_callback({}, threading.Lock())
    callback["setProgress"](3)  # 没有 setMax：按 max=1 处理，夹到段尾
    assert dummy._mp_progress["overall"] == 1.0
