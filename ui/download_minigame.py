"""下载等待小游戏模块 — 让用户在等待下载时打开浏览器玩深渊快线"""

import sys
import webbrowser
from pathlib import Path


def _get_static_dir() -> Path:
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "ui" / "static"
    return Path(__file__).resolve().parent / "static"


def get_game_path() -> Path:
    return _get_static_dir() / "game.html"


def open_game_in_browser() -> bool:
    p = get_game_path()
    if not p.exists():
        return False
    webbrowser.open(p.resolve().as_uri())
    return True
