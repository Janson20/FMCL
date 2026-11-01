"""一次性迁移旧 FMCL 音乐数据（只读旧文件，不覆盖旧数据）。

旧数据：``<base>/data/music.json`` + ``<base>/config.json`` 的 ``music_state``。
新数据：``<base>/data/music/`` 下的 playlists/history/favorites/local。

这里只做 JSON 形态转换，不依赖 Qt，也不读取旧 UI 代码。转换结果先写临时文件再
``os.replace``；旧 ``music.json`` 与 ``config.json`` 一个字节都不改。
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("fusion_music.migration")

MARKER = ".migrated-from-music-json"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 损坏的旧数据不应阻止新页面启动
        logger.warning("读取旧音乐数据失败 %s: %s", path, exc)
        return None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _track_from_old(song: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(song, dict):
        return None
    if str(song.get("source_type") or "") == "local":
        raw_path = str(song.get("file_path") or "").strip()
        if not raw_path:
            return None
        name = str(song.get("display_title") or "").strip()
        if not name:
            name = Path(raw_path).stem
        return {
            "source": "local",
            "songmid": raw_path,
            "name": name,
            "singer": str(song.get("display_artist") or ""),
            "album": "",
            "interval": 0,
            "cover": "",
            "path": raw_path,
            "added_at": int(float(song.get("added_at") or 0)),
        }

    source = str(song.get("online_source") or "").strip()
    songmid = str(song.get("online_songmid") or "").strip()
    name = str(song.get("online_name") or song.get("display_title") or "").strip()
    if not source or not songmid or not name:
        return None
    return {
        "source": source,
        "songmid": songmid,
        "name": name,
        "singer": str(song.get("online_singer") or song.get("display_artist") or ""),
        "album": str(song.get("online_album") or ""),
        "interval": int(song.get("online_interval") or 0),
        "cover": str(song.get("online_img") or ""),
        "types": list(song.get("online_types") or []),
        "type_detail": dict(song.get("online_type_detail") or {}),
        "added_at": int(float(song.get("added_at") or 0)),
    }


def _local_key(track: Dict[str, Any]) -> str:
    return os.path.normcase(os.path.abspath(str(track.get("path") or "")))


def legacy_settings(base_dir: Path) -> Dict[str, Any]:
    """从旧 config.json 提取可迁移的音乐设置（只读）。"""
    config_path = base_dir / "config.json"
    if not config_path.exists():
        return {}
    raw = _read_json(config_path)
    if not isinstance(raw, dict):
        return {}
    state = raw.get("music_state")
    out: Dict[str, Any] = {}
    if isinstance(state, dict):
        try:
            volume = float(state.get("music_volume"))
            out["volume"] = max(0, min(100, int(round(volume * 100)) if volume <= 1 else int(round(volume))))
        except (TypeError, ValueError):
            pass
        mode = str(state.get("music_play_mode") or "").strip()
        if mode in ("order", "loop_list", "loop_single", "shuffle"):
            out["mode"] = mode
        folder = str(state.get("music_last_folder") or "").strip()
        if folder:
            out["folder"] = folder
    if isinstance(raw.get("music_baike_original_enabled"), bool):
        out["prefer_original"] = bool(raw["music_baike_original_enabled"])
    return out


def migrate(base_dir: Path, music_root: Path) -> Dict[str, Any]:
    """迁移旧 ``data/music.json``。

    Returns:
        ``{"migrated": bool, "reason": str, "settings": dict}``。
        已经迁移过 / 新数据已存在时 ``migrated`` 为 False，绝不覆盖新文件。
    """
    old_path = base_dir / "data" / "music.json"
    settings = legacy_settings(base_dir)
    marker = music_root / MARKER
    targets = [music_root / "playlists.json", music_root / "history.json", music_root / "local.json"]
    if marker.exists() or any(p.exists() for p in targets):
        return {"migrated": False, "reason": "already", "settings": settings}
    if not old_path.exists():
        return {"migrated": False, "reason": "no_legacy", "settings": settings}

    raw = _read_json(old_path)
    if not isinstance(raw, dict):
        return {"migrated": False, "reason": "invalid_legacy", "settings": settings}

    playlists_out: List[Dict[str, Any]] = []
    history: List[Dict[str, Any]] = []
    local_seen: Dict[str, Dict[str, Any]] = {}
    local_out: List[Dict[str, Any]] = []
    for playlist in raw.get("playlists") or []:
        if not isinstance(playlist, dict):
            continue
        pid = str(playlist.get("id") or "")
        name = str(playlist.get("name") or "")
        songs = [t for t in (_track_from_old(s) for s in (playlist.get("songs") or [])) if t]
        for track in songs:
            if track.get("source") == "local":
                key = _local_key(track)
                if key and key not in local_seen:
                    local_seen[key] = track
                    local_out.append(track)
        if pid == "__history__" or playlist.get("is_system"):
            if pid == "__history__":
                history = songs
            continue
        if not pid or not name or pid.startswith("__"):
            continue
        playlists_out.append(
            {
                "id": pid,
                "name": name,
                "songs": songs,
                "created_at": float(playlist.get("created_at") or 0),
                "updated_at": float(playlist.get("updated_at") or 0),
                "sort_mode": str(playlist.get("sort_mode") or "add_time_desc"),
                "is_system": False,
            }
        )

    _write_json(music_root / "playlists.json", {"version": 1, "playlists": playlists_out})
    _write_json(music_root / "history.json", history[:300])
    _write_json(music_root / "favorites.json", [])
    _write_json(music_root / "local.json", local_out)
    _write_json(
        marker,
        {
            "version": 1,
            "migrated_at": int(time.time()),
            "playlists": len(playlists_out),
            "history": len(history),
            "local": len(local_out),
            "source": str(old_path),
        },
    )
    logger.info(
        "旧音乐数据已迁移：%d 个歌单 / %d 条历史 / %d 首本地",
        len(playlists_out), len(history), len(local_out),
    )
    return {
        "migrated": True,
        "reason": "ok",
        "settings": settings,
        "counts": {"playlists": len(playlists_out), "history": len(history), "local": len(local_out)},
    }


def apply_legacy_settings(config: Any, values: Dict[str, Any]) -> bool:
    """把旧设置写入新音乐配置；值不合法就跳过。返回是否改过。"""
    changed = False
    try:
        if "volume" in values:
            volume = int(values["volume"])
            if 0 <= volume <= 100:
                config.set("playback.volume", volume)
                changed = True
        if "mode" in values:
            config.set("playback.mode", str(values["mode"]))
            changed = True
        if "folder" in values:
            folder = str(values["folder"])
            if Path(folder).is_dir():
                config.set("local.folders", [folder])
                changed = True
        if "prefer_original" in values:
            config.set("sources.prefer_original", bool(values["prefer_original"]))
            changed = True
    except Exception as exc:  # noqa: BLE001 - 迁移设置失败不应阻止页面
        logger.warning("应用旧音乐设置失败: %s", exc)
    return changed
