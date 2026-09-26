"""国际化(i18n)（转发 shim）

实现已搬到 ``services/i18n_service.py``。

这里只做再导出，**必须是同一批对象**：仓库里有 31 处 ``from ui.i18n import _``，
它们在 import 时绑定函数对象，所以 ``ui.i18n._ is services.i18n_service._``。
下面用一条 ``from ... import ...`` 直接绑定全部名字（含 ``_`` / ``tr`` /
``_translate``），不做任何 ``def _(...)`` 包装 —— 包装会让绑定语义与状态可见性
变复杂。语言切换状态同理：``set_language()`` 改的是 ``services.i18n_service``
的模块级 ``_translations``，两边看到的是同一份。

语言文件目录可用 ``services.i18n_service.set_locales_dir()`` 注入。
"""

from services.i18n_service import (
    AVAILABLE_LANGUAGES,
    DEFAULT_LANGUAGE,
    _,
    _translate,
    get_available_languages,
    get_current_language,
    init_i18n,
    set_language,
    set_locales_dir,
    tr,
)

__all__ = [
    "AVAILABLE_LANGUAGES",
    "DEFAULT_LANGUAGE",
    "init_i18n",
    "get_current_language",
    "set_language",
    "get_available_languages",
    "set_locales_dir",
    "_",
    "tr",
    "_translate",
]
