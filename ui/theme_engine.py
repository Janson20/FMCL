"""动态主题引擎（转发 shim）

实现已搬到 ``services/theme_service.py``（核心层不该被界面层绑死）。

这里只做再导出，**必须是同一批对象**，不做包装、不做子类化：

- ``ui.theme_engine.Theme is services.theme_service.Theme``
- ``ui.theme_engine.ThemeEngine is services.theme_service.ThemeEngine``

模块级单例 ``_engine`` 由 ``services.theme_service`` 独家持有：
``init_theme_engine()`` / ``get_theme_engine()`` 是同一个函数对象，
从哪个模块导入都操作同一个引擎实例。

保留本模块是为了兼容 ``tests/test_theme_engine.py`` 与仓库内既有的
``from ui.theme_engine import ...`` 调用点。
"""

from services.theme_service import Theme, ThemeEngine, get_theme_engine, init_theme_engine

__all__ = ["Theme", "ThemeEngine", "init_theme_engine", "get_theme_engine"]
