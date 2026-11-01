"""上次播放会话：在播曲目 / 播放队列 / 播放模式 / 播放进度。

退出时把队列原样记下来，下次启动摆回界面 —— 但**不自动出声**
（见 :meth:`app.core.player.PlayerEngine.restoreSession`）。

落盘沿用 :mod:`app.core.store` 那套「原子写入 + 损坏备份」：队列可能有上千首，
所以写盘时机由播放引擎挑（队列变化合并延迟、播放中按间隔记进度），这里只管读写。
文件是纯文本，手改坏了也只是回到「没有会话」而已，不会把界面带崩。

放在这里而不是塞进 ``player.py``，是为了让「什么算一份能恢复的会话」这条判断
不需要 Qt 也不需要音频设备就能回归（``tests/test_core.py``）。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import paths
from ..units import to_int
from .models import Track
from .queue import PLAY_MODE_NAMES, PlayMode, PlayQueue, mode_from_name
from .store import atomic_write_json, read_json

logger = logging.getLogger(__name__)

#: 会话文件格式版本。字段含义变了就 +1：读不认识的版本宁可当作「没有会话」，
#: 也不要拿半个队列去猜 —— 猜错了是一堆放不了的歌躺在队列里。
SCHEMA_VERSION = 1


def is_playable(track: Track) -> bool:
    """能不能排进队列：有歌名，且有音源 id 或本地路径。

    会话文件是纯文本、用户随时可以手改：里面躺着一条空壳曲目的话，恢复出来
    会变成播放栏里一首点不动的「歌」，不如直接丢掉。
    """
    if track is None or not str(track.name or "").strip():
        return False
    return bool(str(track.songmid or "").strip() or str(track.path or "").strip())


@dataclass
class Session:
    """一份可恢复的播放会话。"""

    tracks: List[Track] = field(default_factory=list)
    index: int = 0
    mode: PlayMode = PlayMode.LOOP_LIST
    position_ms: int = 0
    saved_at: int = 0

    def queue_dict(self) -> Dict[str, Any]:
        """给 :meth:`PlayQueue.load_dict` 用的队列快照。"""
        return {
            "mode": PLAY_MODE_NAMES.get(self.mode, "loop_list"),
            "index": self.index,
            "tracks": [t.to_dict() for t in self.tracks],
        }



def save(queue: PlayQueue, position_ms: int = 0, *, path: Optional[Path] = None) -> bool:
    """把队列与进度写进会话文件；队列为空时顺手删掉旧文件。

    返回是否写成功 —— 存不下（磁盘满 / 目录只读）不该影响退出。
    """
    target = Path(path) if path else paths.session_file()
    snapshot = queue.to_dict()
    if not snapshot.get("tracks"):
        # 空队列没什么可恢复的：留一个空壳文件不如不留，
        # 否则「清空队列 → 退出 → 重开」还会把上一次的队列捞回来
        clear(target)
        return True
    payload = {
        "version": SCHEMA_VERSION,
        "saved_at": int(time.time()),
        "position_ms": max(0, to_int(position_ms)),
        "queue": snapshot,
    }
    try:
        atomic_write_json(target, payload)
        return True
    except Exception as e:
        logger.warning("保存播放会话失败: %s", e)
        return False


def load(*, path: Optional[Path] = None) -> Optional[Session]:
    """读回上次的会话。

    文件缺失 / 损坏 / 版本不认识 / 过滤完一首不剩时返回 ``None`` —— 调用方
    按「没有可恢复的会话」处理即可。
    """
    target = Path(path) if path else paths.session_file()
    raw = read_json(target, None)
    if not isinstance(raw, dict) or to_int(raw.get("version")) != SCHEMA_VERSION:
        return None

    queue = raw.get("queue")
    if not isinstance(queue, dict):
        return None

    tracks = [
        t
        for t in (Track.from_dict(d) for d in (queue.get("tracks") or []) if isinstance(d, dict))
        if is_playable(t)
    ]
    if not tracks:
        return None

    # 下标夹回范围：手改过的文件里可能是 99，直接拿去索引会 IndexError
    index = min(max(0, to_int(queue.get("index"))), len(tracks) - 1)
    return Session(
        tracks=tracks,
        index=index,
        mode=mode_from_name(str(queue.get("mode") or ""), PlayMode.LOOP_LIST),
        position_ms=max(0, to_int(raw.get("position_ms"))),
        saved_at=max(0, to_int(raw.get("saved_at"))),
    )


def clear(path: Optional[Path] = None) -> None:
    """删掉会话文件（关掉恢复功能、或队列被清空时用）。"""
    target = Path(path) if path else paths.session_file()
    try:
        target.unlink(missing_ok=True)
    except OSError as e:
        logger.debug("删除会话文件失败: %s", e)
