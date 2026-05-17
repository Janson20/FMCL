"""国际化(i18n)模块 - 提供多语言支持"""
import json
import os
import locale
from pathlib import Path
from typing import Dict, Optional

# 可用语言列表
AVAILABLE_LANGUAGES = {
    "zh_CN": "简体中文",
    "en_US": "English",
    "ja_JP": "日本語",
    "zh_TW": "繁體中文",
}

# 默认语言
DEFAULT_LANGUAGE = "zh_CN"

# 全局状态
_current_language: str = DEFAULT_LANGUAGE
_translations: Dict[str, str] = {}


def _get_locales_dir() -> Path:
    """获取语言文件目录"""
    # 优先使用运行目录
    base_dir = Path(__file__).parent
    locales_dir = base_dir / "locales"
    
    # 如果运行在 PyInstaller 打包环境下，使用 _MEIPASS
    import sys
    if getattr(sys, 'frozen', False) or hasattr(sys, "_MEIPASS"):
        locales_dir = Path(sys._MEIPASS) / "ui" / "locales"
        if not locales_dir.exists():
            import logging
            logging.warning(f"PyInstaller locales dir not found: {locales_dir}, fallback to {base_dir / 'locales'}")
            locales_dir = base_dir / "locales"
    
    return locales_dir


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
        system_locale, _ = locale.getdefaultlocale()
        if system_locale:
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
