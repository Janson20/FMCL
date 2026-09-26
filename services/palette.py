"""12 色调色板 —— 全进程唯一的可变颜色字典。

**关键语义**：``COLORS`` 必须是唯一一个 dict 对象，被
``ThemeEngine.apply_theme()`` 原地 ``clear()`` / ``update()`` 修改，
所有 ``from services.palette import COLORS``（或经 ``ui.constants`` 转发）
拿到同一对象的模块会自动看到新值。因此这里不提供返回副本的函数。

原地址是 ``ui/constants.py``；核心层需要读颜色，但核心层不该被界面层绑死，
故搬到服务层。
"""

# ─── 颜色主题 ───────────────────────────────────────────────
COLORS = {
    "bg_dark": "#1a1a2e",
    "bg_medium": "#16213e",
    "bg_light": "#0f3460",
    "accent": "#e94560",
    "accent_hover": "#ff6b81",
    "success": "#2ecc71",
    "warning": "#f39c12",
    "error": "#e74c3c",
    "text_primary": "#ffffff",
    "text_secondary": "#a0a0b0",
    "card_bg": "#1e2a4a",
    "card_border": "#2d3a5c",
}

# 运行时颜色引用（主题引擎会直接更新此字典，所有引用自动同步）
current_colors = COLORS


__all__ = ["COLORS", "current_colors"]
