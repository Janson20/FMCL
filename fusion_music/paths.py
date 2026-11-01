"""程序路径解析。

所有运行时数据（配置、凭据、缓存、歌单）默认落在**程序所在目录**下，
使整个应用保持绿色/便携：拷贝目录即可带走全部数据。

可通过环境变量 `FUSION_MUSIC_HOME` 或命令行 `--data-dir` 覆盖。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

APP_NAME = "FusionMusicPlayer"
APP_DISPLAY_NAME = "Fusion Music Player"
ORG_NAME = "FusionLab"

# 版本号的唯一来源是 app/_version.py（由 scripts/release.py 更新，
# 发布流水线在打包前会依据 git tag 覆写一次）。
# 保留兜底是为了在文件被误删时仍能启动，而不是隐藏错误。
try:  # pragma: no cover
    from ._version import APP_VERSION
except ImportError:  # pragma: no cover
    APP_VERSION = "0.0.0-unknown"

_DATA_DIR_OVERRIDE: Optional[Path] = None


def program_dir() -> Path:
    """程序所在目录（打包后为 exe 目录，开发时为项目根目录）。

    注意这是**放用户数据**的地方（``data/``、``downloads/``），不是放打包进去的
    只读资源的地方 —— 找 ``assets/`` 请用 :func:`resource_dir`。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def resource_dir() -> Path:
    """打包进程序的**只读资源**（``assets/``、``app/ui/``）所在目录。

    开发时就是项目根目录；打包之后**不是** exe 所在目录：PyInstaller 6 的
    onedir 把数据文件放进 ``_internal/``，onefile 放进 ``%TEMP%\\_MEIxxxx``，
    两者都是 ``sys._MEIPASS``。以前用 ``program_dir()`` 去找 ``assets/icon.ico``，
    在打包版里必然落空 —— 表现就是「最小化到托盘之后没有图标」。
    """
    if getattr(sys, "frozen", False):
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            return Path(bundle)
    return program_dir()


def is_writable(path: Path, *, create: bool = False) -> bool:
    """探测目录是否可写（安装到 Program Files 时需要回退）。

    ``create=True`` 时顺手把目录建出来 —— 「能不能在这里放数据」问的就是
    「建不出目录 / 建出来却写不进探测文件」这两件事，一次问清。
    自动更新那边只要探测已有的程序目录，于是默认不建。
    """
    try:
        if create:
            path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write_probe_{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def _fallback_dir() -> Path:
    """程序目录不可写时的回退位置。"""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return Path(base) / APP_NAME
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / APP_NAME


def set_data_dir(path: str | os.PathLike | None) -> None:
    """覆盖数据根目录（由命令行参数或环境变量调用）。"""
    global _DATA_DIR_OVERRIDE
    _DATA_DIR_OVERRIDE = Path(path).expanduser().resolve() if path else None


def pop_data_dir_arg(argv: list[str]) -> Optional[str]:
    """从 argv 里取出并移除 ``--data-dir X``，返回 X（没有则 None）。

    入口要在**导入 Qt 之前**把启动画面画出来，而画面用哪张底图取决于
    ``data/config.json`` 里的主题与强调色 —— 所以数据目录得提前定下来。
    这里解析一次，随后把结果原样传给 :class:`app.application.Application`，
    避免两边各解析一遍却得出不同结论。
    """
    if "--data-dir" in argv:
        index = argv.index("--data-dir")
        if index + 1 < len(argv):
            value = argv[index + 1]
            del argv[index : index + 2]
            return value
    return None


def data_dir() -> Path:
    """数据根目录：默认为『程序当前目录』。

    程序目录不可写时（例如解压到 ``Program Files``）自动回退到用户目录，
    避免便携版在被安装后无法保存设置与歌单。
    """
    if _DATA_DIR_OVERRIDE is not None:
        return _DATA_DIR_OVERRIDE
    env = os.environ.get("FUSION_MUSIC_HOME")
    if env:
        return Path(env).expanduser().resolve()
    target = program_dir() / "data"
    if is_writable(target, create=True):
        return target
    return _fallback_dir()


def cache_dir() -> Path:
    return data_dir() / "cache"


def cover_cache_dir() -> Path:
    return cache_dir() / "cover"


def lyric_cache_dir() -> Path:
    return cache_dir() / "lyric"


def media_cache_dir() -> Path:
    return cache_dir() / "media"


def backgrounds_dir() -> Path:
    """自定义背景图的落盘目录（设置 → 外观 → 背景图）。

    放在 ``data/`` 里而不是用户选图时的原始位置：便携（拷走 data 就带走全部数据），
    原图被挪走或删掉也不影响程序。见 :mod:`app.core.backgrounds`。
    """
    return data_dir() / "backgrounds"


def config_file() -> Path:
    return data_dir() / "config.json"


def credentials_file() -> Path:
    """加密凭据文件（网易云登录态等）。"""
    return data_dir() / "credentials.enc"


def key_file() -> Path:
    """主密钥文件（Windows 下由 DPAPI 包裹）。"""
    return data_dir() / "keys" / "master.key"


def playlists_file() -> Path:
    return data_dir() / "playlists.json"


def history_file() -> Path:
    return data_dir() / "history.json"


def favorites_file() -> Path:
    return data_dir() / "favorites.json"


def session_file() -> Path:
    """上次播放会话（在播曲目 + 队列 + 进度），见 :mod:`app.core.session`。"""
    return data_dir() / "session.json"


def loudness_file() -> Path:
    """响度测算缓存（音量均衡用），见 :mod:`app.core.loudness_store`。"""
    return data_dir() / "loudness.json"


def updates_dir() -> Path:
    """自动更新的下载与暂存目录，见 :mod:`app.core.updater`。"""
    return data_dir() / "updates"


def log_dir() -> Path:
    return data_dir() / "logs"


def qml_dir() -> Path:
    return Path(__file__).resolve().parent / "ui"


def resource(path: str) -> str:
    """返回 QML 可用的本地文件 URL 字符串。"""
    from PySide6.QtCore import QUrl

    p = Path(path)
    if not p.is_absolute():
        p = qml_dir() / path
    return QUrl.fromLocalFile(str(p)).toString()


def from_file_url(value: str) -> str:
    """QML 文件对话框给的 ``file://`` URL → 本地路径（普通路径原样返回）。

    QML 的 ``FileDialog.selectedFile`` / ``FolderDialog.selectedFolder`` 都是
    ``url`` 类型，``toString()`` 出来是 ``file:///D:/x/y.mp3``（中文与空格
    还会被百分号转义）。直接塞给 ``Path`` 会得到 ``file:\\D:\\x\\y.mp3``
    这种四不像，落盘时报 ``WinError 123``。

    解析不出来时原样返回：宁可让调用方拿着原来那个字符串去报错，也不要在这里
    悄悄吞成空串。
    """
    text = str(value or "").strip()
    if not text or not text.lower().startswith("file:"):
        return text
    try:
        from PySide6.QtCore import QUrl

        return QUrl(text).toLocalFile() or text
    except Exception:  # pragma: no cover - 依赖 Qt
        return text


def normalize_folder(value: str) -> str:
    """文件夹选择器给的 URL 或手输路径 → 绝对路径（空串表示没给）。

    手输路径仍然兼容 —— 老配置文件里存的就是纯路径，用户也可能从资源管理器
    粘一个进来（还常常带着两边引号）。
    """
    text = from_file_url(str(value or "").strip().strip('"').strip())
    if not text:
        return ""
    try:
        return os.path.abspath(os.path.expanduser(text))
    except Exception:  # pragma: no cover - 路径异常时原样返回
        return text


def ensure_dirs() -> None:
    for d in (
        data_dir(),
        cache_dir(),
        cover_cache_dir(),
        lyric_cache_dir(),
        media_cache_dir(),
        log_dir(),
        backgrounds_dir(),
        key_file().parent,
    ):
        d.mkdir(parents=True, exist_ok=True)
