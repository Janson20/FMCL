"""UI 常量定义 - 颜色主题、字体检测、全局配置

颜色主题、User-Agent、字体检测都已搬到服务层（``services/palette.py``、
``services/user_agent.py``、``services/font_service.py``），这里只做**再导出**，
保证 ``from ui.constants import COLORS, USER_AGENT, FONT_FAMILY`` 等既有写法继续可用，
并且拿到的是同一批对象：

- ``COLORS`` 与 ``services.palette.COLORS`` 是**同一个** dict 对象
  （``ThemeEngine`` 原地 ``clear``/``update`` 它，所有引用自动同步）；
- ``FONT_FAMILY`` 与 ``services.font_service.FONT_FAMILY`` 是同一个
  ``LazyStr`` 实例（首次 ``str()`` 才跑检测并缓存，不会因为多一条导入路径就多探测一次）。

**字体检测为什么也搬（阶段 2 任务 2.10）**：QML 运行时不许 import ``ui/`` 包 ——
``ui/__init__.py`` 会 ``from ui.app import ModernApp`` 把 ``customtkinter`` 拖进来
（阶段 0 第 10.4 节实测踩过；阶段 2 契约第六节决策 12 定为红线），而字体检测是
纯标准库逻辑（``subprocess`` + ``glob`` + ``platform``），必须能在 PySide6 进程里单独用。

``RESOURCE_TYPES`` 属于界面呈现配置、与 ``ui/`` 里的资源页强绑定，本轮不搬。
"""

from services.font_service import (  # noqa: F401 - 再导出，供既有 `from ui.constants import …` 使用
    _CHINESE_FONT_PRIORITY,
    _EMOJI_FONT_PRIORITY,
    _FONT_INSTALL_MARKER,
    FONT_FAMILY,
    _detect_font_family,
    _has_install_attempted,
    _install_chinese_font,
    _install_emoji_font,
    _mark_install_attempted,
    _run_fc_list,
    _run_fc_match,
    _scan_common_cjk_fonts,
    _select_preferred,
)
from services.palette import COLORS, current_colors  # noqa: F401 - 再导出
from services.user_agent import USER_AGENT, LazyStr, _get_fmcl_version, _get_user_agent  # noqa: F401 - 再导出

# ─── 资源类型配置 ─────────────────────────────────────────────

RESOURCE_TYPES = {
    "mods": {
        "label": "🧩 模组",
        "folder": "mods",
        "extensions": {".jar", ".zip", ".disabled"},
        "description": "将 .jar / .zip 模组文件拖拽到此处安装",
    },
    "resourcepacks": {
        "label": "🎨 资源包",
        "folder": "resourcepacks",
        "extensions": {".zip"},
        "description": "将 .zip 资源包文件拖拽到此处安装",
    },
    "saves": {
        "label": "🗺️ 地图",
        "folder": "saves",
        "extensions": {".zip"},
        "description": "将 .zip 地图存档文件拖拽到此处安装",
    },
    "shaderpacks": {
        "label": "✨ 光影",
        "folder": "shaderpacks",
        "extensions": {".zip"},
        "description": "将 .zip 光影包文件拖拽到此处安装",
    },
}
