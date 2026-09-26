"""HTTP User-Agent 与惰性字符串。

原地址是 ``ui/constants.py``：``USER_AGENT`` 只是 HTTP 头，``LazyStr`` 是通用
小工具，二者都与界面无关，却因为住在 ``ui`` 包内而把核心层
（``launcher/``、``downloader.py``）与界面层绑死，故搬到服务层。

``USER_AGENT`` 仍是 ``LazyStr`` 实例：import 时不读版本号，首次 ``str()`` /
f-string / ``format`` 时才调用 ``updater.get_current_version()`` 并缓存。
"""

# ─── 惰性字符串 ─────────────────────────────────────────────


class LazyStr:
    """惰性求值字符串 — 首次 str()/f-string/format 时才计算实际值

    各模块 ``from ui.constants import FONT_FAMILY`` 拿到的是轻量对象，
    真正的字体检测（subprocess 调用）在 CTkFont(family=FONT_FAMILY) 构建时发生。
    """

    def __init__(self, func):
        self._func = func
        self._value = None

    def __str__(self):
        if self._value is None:
            self._value = self._func()
        return self._value

    def __repr__(self):
        return str(self)


# ─── 版本号与 User-Agent ────────────────────────────────────


def _get_fmcl_version():
    """从 updater.py 获取 FMCL 版本号"""
    try:
        from updater import get_current_version

        return get_current_version()
    except Exception:
        pass
    return "unknown"


def _get_user_agent() -> str:
    """获取 HTTP User-Agent 字符串"""
    return f"FMCL/{_get_fmcl_version()}"


USER_AGENT = LazyStr(_get_user_agent)


__all__ = ["LazyStr", "USER_AGENT"]
