"""国际化(i18n)模块 - 提供多语言支持

原地址是 ``ui/i18n.py``，除 ``_get_locales_dir()`` 外逐字搬运（含全部边界行为）。
搬家的原因：``launcher/core.py`` / ``downloader.py`` 只想要一个 UA 字符串，
``from ui.constants import ...`` 却会先执行 ``ui/__init__.py`` 的 eager import
（customtkinter + 整个界面），核心层被界面层反向绑死。

语言文件目录不再假设 ``Path(__file__).parent / "locales"``（``__file__`` 现在位于
``services/``），改为按以下顺序解析：

1. ``set_locales_dir()`` 显式注入的目录（最高优先级，供测试注入）
2. PyInstaller 冻结环境下的 ``<_MEIPASS>/ui/locales``（存在时）
3. 开发态仓库布局 ``<repo>/ui/locales``
4. 当前工作目录下的 ``ui/locales``

四者都不可用时与从前一样：静默回退，不抛异常、不打日志，
``_translate()`` 逐键返回键名本身。
"""

import json
import locale
import os
from pathlib import Path
from typing import Dict, Optional, Union

# 可用语言列表
AVAILABLE_LANGUAGES = {"zh_CN": "简体中文", "en_US": "English", "ja_JP": "日本語", "zh_TW": "繁體中文"}

# 默认语言
DEFAULT_LANGUAGE = "zh_CN"

# 全局状态
_current_language: str = DEFAULT_LANGUAGE
_translations: Dict[str, str] = {}

# 显式注入的语言文件目录（None 表示按下方规则自动解析）
_locales_dir: Optional[Path] = None


def set_locales_dir(path: Optional[Union[str, Path]]) -> None:
    """显式指定语言文件目录（主要供测试注入），传 None 恢复自动解析。"""
    global _locales_dir
    _locales_dir = Path(path) if path is not None else None


def _get_locales_dir() -> Path:
    """获取语言文件目录（按模块 docstring 里的四级顺序解析）"""
    if _locales_dir is not None:
        return _locales_dir

    # 如果运行在 PyInstaller 打包环境下，使用 _MEIPASS
    import sys

    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        meipass_dir = Path(getattr(sys, "_MEIPASS", "")) / "ui" / "locales"
        if meipass_dir.exists():
            return meipass_dir

    # 开发态：services/ 的上一级就是仓库根，ui/locales 与它同级
    repo_locales_dir = Path(__file__).resolve().parent.parent / "ui" / "locales"
    if repo_locales_dir.exists():
        return repo_locales_dir

    # 兜底：当前工作目录
    cwd_locales_dir = Path.cwd() / "ui" / "locales"
    if cwd_locales_dir.exists():
        return cwd_locales_dir

    return repo_locales_dir


def _load_translations(lang_code: str) -> Dict[str, str]:
    """加载指定语言的翻译文件"""
    locales_dir = _get_locales_dir()
    lang_file = locales_dir / f"{lang_code}.json"

    if not lang_file.exists():
        # 尝试加载默认语言
        if lang_code != DEFAULT_LANGUAGE:
            return _load_translations(DEFAULT_LANGUAGE)
        return {}

    try:
        with open(lang_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        if lang_code != DEFAULT_LANGUAGE:
            return _load_translations(DEFAULT_LANGUAGE)
        return {}


def _detect_system_language() -> str:
    """检测系统语言"""
    try:
        system_locale, encoding = locale.getlocale()
        if not system_locale:
            # locale.getlocale() 可能返回 (None, None)，尝试从环境变量获取
            system_locale = locale.getlocale(category=locale.LC_ALL)
            if isinstance(system_locale, tuple):
                system_locale = system_locale[0]
        if system_locale:
            # 标准化：某些平台的语言代码可能包含编码后缀（如 "zh_CN.UTF-8"）
            system_locale = system_locale.split(".")[0]
            # 标准化语言代码
            lang_map = {
                "zh_CN": "zh_CN",
                "zh_SG": "zh_CN",
                "zh_TW": "zh_TW",
                "zh_HK": "zh_TW",
                "ja_JP": "ja_JP",
                "ja": "ja_JP",
                "en_US": "en_US",
                "en_GB": "en_US",
                "en": "en_US",
            }
            return lang_map.get(system_locale, DEFAULT_LANGUAGE)
    except Exception:
        pass
    return DEFAULT_LANGUAGE


def init_i18n(config_language: Optional[str] = None) -> str:
    """
    初始化国际化系统

    Args:
        config_language: 配置文件中的语言设置，如果为 None 则自动检测系统语言

    Returns:
        当前使用的语言代码
    """
    global _current_language, _translations

    # 确定语言
    if config_language and config_language in AVAILABLE_LANGUAGES:
        _current_language = config_language
    else:
        _current_language = _detect_system_language()

    # 加载翻译
    _translations = _load_translations(_current_language)

    return _current_language


def get_current_language() -> str:
    """获取当前语言代码"""
    return _current_language


def set_language(lang_code: str) -> bool:
    """
    切换语言

    Args:
        lang_code: 目标语言代码

    Returns:
        是否切换成功
    """
    global _current_language, _translations

    if lang_code not in AVAILABLE_LANGUAGES:
        return False

    _current_language = lang_code
    _translations = _load_translations(lang_code)

    return True


def _translate(key: str, **kwargs) -> str:
    """
    翻译单个键

    Args:
        key: 翻译键名
        **kwargs: 格式化参数

    Returns:
        翻译后的文本
    """
    if key in _translations:
        text = _translations[key]
    else:
        # 如果没有找到翻译，返回原始键名
        text = key

    # 处理格式化参数
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, ValueError):
            pass

    return text


# 简化的 gettext 风格函数
def _(key: str, **kwargs) -> str:
    """翻译函数，类似于 gettext 的 _()"""
    return _translate(key, **kwargs)


# 便捷函数
def tr(key: str, **kwargs) -> str:
    """翻译函数 alias"""
    return _translate(key, **kwargs)


def get_available_languages() -> Dict[str, str]:
    """获取可用语言列表"""
    return AVAILABLE_LANGUAGES.copy()


__all__ = [
    "AVAILABLE_LANGUAGES",
    "DEFAULT_LANGUAGE",
    "set_locales_dir",
    "init_i18n",
    "get_current_language",
    "set_language",
    "get_available_languages",
    "_translate",
    "_",
    "tr",
]
