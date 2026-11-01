"""延迟导入：把"启动时用不到的重模块"挪到真正用得到的时候。

为什么需要
----------
实测（Python 3.14 + 本机）：``import requests`` 要 **0.4 秒**（它自己还要拉
urllib3 / charset_normalizer / certifi / idna），而这份开销原先压在启动链上 ——
``app.bridges.*`` 与 ``app.core.models`` 一路 import 到 ``app.sources`` / ``app.core.cache``，
于是用户在 Qt 还没起来时就先白等了这 0.4 秒。

用法与约束
----------
::

    from ..lazy import requests          # 模块级只拿到一个代理，不触发导入

    session = requests.Session()         # 第一次取属性时才真的 import

代理把属性访问原样转给真模块，所以 ``requests.get(...)``、``except requests.RequestException``、
``isinstance(x, requests.Response)`` 这些写法**一个字都不用改**。

PyInstaller 注意：真正的导入必须是**字面量 import 语句**（见 :func:`_load_module`），
静态分析才能顺着它把 requests 全家桶与 certifi 的 cacert.pem 一起收进包里。
``importlib.import_module("requests")`` 这种写法在打包版里会缺依赖。
"""

from __future__ import annotations

import importlib
import sys
import threading
from typing import Any, Dict, Tuple

__all__ = ["LazyModule", "requests", "prewarm", "loaded"]

_cache: Dict[str, Any] = {}
_lock = threading.Lock()


def _load_module(name: str) -> Any:
    """真正导入（带缓存）。字面量 import 语句不能换成 importlib 动态导入。"""
    module = _cache.get(name)
    if module is not None:
        return module
    with _lock:
        module = _cache.get(name)
        if module is None:
            if name == "requests":
                import requests  # noqa: PLC0415 - 字面量：PyInstaller 静态分析靠它
                module = requests
            else:
                module = importlib.import_module(name)
            _cache[name] = module
    return module


class LazyModule:
    """模块代理：属性访问时导入，之后完全透明。"""

    __slots__ = ("_name", "_module")

    def __init__(self, name: str) -> None:
        self._name = name
        self._module: Any = None

    def _load(self) -> Any:
        module = self._module
        if module is None:
            module = self._module = _load_module(self._name)
        return module

    def __getattr__(self, item: str) -> Any:
        # 只有常规查找失败才会走到这里，所以 __slots__ 里的字段不受影响
        return getattr(self._load(), item)

    def __dir__(self) -> list[str]:
        return dir(self._load())

    def __repr__(self) -> str:
        state = "已导入" if self._module is not None else "未导入"
        return f"<LazyModule {self._name!r} ({state})>"


#: ``import requests`` 的替代品，用法完全一致
requests = LazyModule("requests")


def loaded() -> Tuple[str, ...]:
    """已经真正导入的模块名（排查"到底谁把它拉进来了"用）。"""
    return tuple(sorted(_cache))


def prewarm(*names: str) -> threading.Thread:
    """后台预热：在 QML 编译那 0.8 秒里把模块先导进来。

    那段窗口里主线程基本都在 C++ 里（ctypes/Qt 调用会释放 GIL），所以这活儿
    是真·并行：等主流程走到需要 requests 的地方时，它已经在内存里了。
    失败只记一行 stderr —— 预热失败不该影响任何主流程。
    """

    def _run() -> None:
        for name in names:
            try:
                _load_module(name)
            except Exception as e:  # pragma: no cover - 罕见
                print(f"预热 {name} 失败: {e}", file=sys.stderr)

    thread = threading.Thread(target=_run, name="fmp-prewarm", daemon=True)
    thread.start()
    return thread
