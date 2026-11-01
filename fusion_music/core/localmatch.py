"""本地曲目的在线匹配：封面与歌词从哪儿来。

本地文件只有标签里那点东西，封面常常没有、歌词多半没有。扫描时按「歌名 + 歌手」
去网易云找同一首歌（复用 :func:`app.sources.netease.pick_wiki_song` 的歌名 + 时长
核对，翻唱 / 伴奏会被 15 秒容差挡掉），匹配上了就把**在线身份**记在曲目上：

* ``cover``：没有内嵌封面时直接用匹配到的专辑图；
* ``match_source`` / ``match_songmid``：这首歌在网易云的身份。**歌词不在扫描阶段
  抓**，播放时才按这个身份去取 —— 否则几千首本地歌就是几千个歌词请求，扫描会从
  「等一会儿」变成「等一晚上」。

匹配结果跟着 ``local.json`` 一起落盘，重扫时按「路径 + 时长」沿用上次的结果
（:func:`adopt_match`），只有文件真的被换成别的歌（时长变了）才重新匹配。

这里的函数都是纯逻辑：不联网、不碰界面、不碰 Qt。联网只发生在
:class:`app.bridges.library.LibraryController` 的扫描线程里。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .models import Track

#: 匹配阶段的并发数。网易云那边是普通搜索接口，压太高容易吃风控
MATCH_WORKERS = 4

#: 扫描两个阶段的文案（界面上要显示，所以放在这里当唯一来源）
PHASE_SCAN = "扫描文件"
PHASE_MATCH = "匹配封面与歌词"

def key_for_path(path: str) -> str:
    """按路径取键（Windows 下大小写与分隔符不敏感，统一 normcase）。"""
    try:
        return os.path.normcase(os.path.abspath(str(path or "")))
    except Exception:
        return str(path or "")

def has_sidecar_lyric(path: str) -> bool:
    """同目录下有没有同名 ``.lrc``（有的话就不必为歌词去联网了）。"""
    if not path:
        return False
    try:
        p = Path(str(path))
        return any(p.with_suffix(suffix).exists() for suffix in (".lrc", ".LRC"))
    except OSError:
        return False

def needs_match(track: Optional[Track], has_lyric: Optional[bool] = None) -> bool:
    """这首歌还需要联网匹配吗：缺封面，或者缺歌词。

    ``has_lyric`` 是给批量调用预留的（扫描时已经把同目录 ``.lrc`` 查过一次），
    不传就自己查。
    """
    if track is None or not str(track.name or "").strip():
        return False
    if not track.cover:
        return True
    if has_lyric is None:
        has_lyric = has_sidecar_lyric(track.path)
    if has_lyric:
        return False
    # 没有 .lrc：只要还没记下在线身份，就值得去匹配一次（歌词要用它）
    return not track.match_songmid

def apply_match(track: Optional[Track], info) -> bool:
    """把匹配到的在线曲目写回本地曲目（记身份 + 补封面）。

    内嵌封面比在线封面准，所以**只补不覆盖**。
    """
    if track is None or info is None:
        return False
    song_id = str(getattr(info, "songmid", "") or "")
    source = str(getattr(info, "source", "") or "")
    if not song_id or not source:
        return False
    track.match_source = source
    track.match_songmid = song_id
    if not track.cover and getattr(info, "img", ""):
        track.cover = str(info.img)
    return True

def reuse_matches(previous: Optional[Iterable[Track]]) -> Dict[str, Track]:
    """上次扫描结果里「匹配过」的曲目，按路径收成一张表（重扫时不必再搜一遍）。"""
    out: Dict[str, Track] = {}
    for track in previous or []:
        if track is None or not track.path or not track.match_songmid:
            continue
        out[key_for_path(track.path)] = track
    return out

def adopt_match(track: Optional[Track], previous: Optional[Track]) -> bool:
    """沿用上次的匹配结果。

    只有**时长一模一样**才认：时长读不出来（``<= 0``）时不敢认 —— 那说明标签
    本来就没读全，凭它判断「还是同一首歌」没有依据。文件被换成另一首歌时长几乎
    必变，这一条就把它挡在外面了。
    """
    if track is None or previous is None or not previous.match_songmid:
        return False
    if int(track.interval or 0) <= 0 or int(track.interval or 0) != int(previous.interval or 0):
        return False
    track.match_source = previous.match_source
    track.match_songmid = previous.match_songmid
    if not track.cover:
        track.cover = previous.cover
    return True

def matched_count(tracks: Optional[Iterable[Track]]) -> int:
    """有多少首匹配到了在线信息（扫描完成后的提示里用）。"""
    return sum(1 for t in (tracks or []) if t is not None and t.match_songmid)

def pending_matches(tracks: Optional[Iterable[Track]]) -> List[Track]:
    """挑出需要联网匹配的曲目（保持原顺序）。"""
    return [t for t in (tracks or []) if needs_match(t)]
