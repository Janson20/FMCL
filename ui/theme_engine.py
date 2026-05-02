"""动态主题引擎 - 支持导入 .json 主题文件、自定义强调色、Minecraft 版本动态调色"""
import json
import os
import copy
import random
from pathlib import Path
from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field, asdict

from ui.constants import COLORS


@dataclass
class Theme:
    """主题数据模型"""
    name: str
    author: str = ""
    description: str = ""
    version: str = "1.0"
    colors: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Theme":
        return cls(
            name=data.get("name", "Unnamed Theme"),
            author=data.get("author", ""),
            description=data.get("description", ""),
            version=data.get("version", "1.0"),
            colors=data.get("colors", {}),
        )


class ThemeEngine:
    """主题引擎 - 管理主题加载、切换、动态调色"""

    THEMES_DIR_NAME = "themes"

    def __init__(self, base_dir: str):
        self._base_dir = Path(base_dir)
        self._themes_dir = self._base_dir / self.THEMES_DIR_NAME
        self._themes_dir.mkdir(parents=True, exist_ok=True)

        self._current_theme: Optional[Theme] = None
        self._current_accent_color: Optional[str] = None
        self._original_colors = dict(COLORS)
        self._preset_themes: List[Theme] = self._load_preset_themes()

    # ─── 预设主题 ────────────────────────────────────────────

    def _load_preset_themes(self) -> List[Theme]:
        return [
            Theme(
                name="default",
                author="FMCL",
                description="FMCL 默认深色主题",
                version="1.0",
                colors={},
            ),
            Theme(
                name="ocean",
                author="FMCL",
                description="海洋蓝调",
                version="1.0",
                colors={
                    "bg_dark": "#0d1b2a",
                    "bg_medium": "#1b2838",
                    "bg_light": "#1b3a5c",
                    "accent": "#00b4d8",
                    "accent_hover": "#48cae4",
                    "success": "#2ecc71",
                    "warning": "#f39c12",
                    "error": "#e74c3c",
                    "text_primary": "#e0e0e0",
                    "text_secondary": "#8899aa",
                    "card_bg": "#1a2d42",
                    "card_border": "#2a4055",
                },
            ),
            Theme(
                name="forest",
                author="FMCL",
                description="森林绿意",
                version="1.0",
                colors={
                    "bg_dark": "#0a1a0f",
                    "bg_medium": "#122a18",
                    "bg_light": "#1a3d24",
                    "accent": "#2ecc71",
                    "accent_hover": "#58d68d",
                    "success": "#2ecc71",
                    "warning": "#f39c12",
                    "error": "#e74c3c",
                    "text_primary": "#e0e0e0",
                    "text_secondary": "#80a890",
                    "card_bg": "#142e1c",
                    "card_border": "#244a30",
                },
            ),
            Theme(
                name="lavender",
                author="FMCL",
                description="薰衣草紫",
                version="1.0",
                colors={
                    "bg_dark": "#1a0a2e",
                    "bg_medium": "#2a1245",
                    "bg_light": "#3a1a5c",
                    "accent": "#9b59b6",
                    "accent_hover": "#af7ac5",
                    "success": "#2ecc71",
                    "warning": "#f39c12",
                    "error": "#e74c3c",
                    "text_primary": "#e0e0e0",
                    "text_secondary": "#a080c0",
                    "card_bg": "#2a1a45",
                    "card_border": "#4a2a65",
                },
            ),
            Theme(
                name="sunset",
                author="FMCL",
                description="日落暖橙",
                version="1.0",
                colors={
                    "bg_dark": "#1a0f0a",
                    "bg_medium": "#2a1a12",
                    "bg_light": "#3d2818",
                    "accent": "#e67e22",
                    "accent_hover": "#f39c12",
                    "success": "#2ecc71",
                    "warning": "#f39c12",
                    "error": "#e74c3c",
                    "text_primary": "#e0d0c0",
                    "text_secondary": "#b09070",
                    "card_bg": "#2a1e14",
                    "card_border": "#4a3420",
                },
            ),
        ]

    # ─── 主题加载 ────────────────────────────────────────────

    def get_available_themes(self) -> List[Dict]:
        """获取所有可用主题列表（预设 + 用户导入）"""
        themes = []

        for preset in self._preset_themes:
            d = preset.to_dict()
            d["source"] = "preset"
            themes.append(d)

        for theme_file in self._themes_dir.glob("*.json"):
            try:
                with open(theme_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                theme = Theme.from_dict(data)
                d = theme.to_dict()
                d["source"] = "user"
                d["file_path"] = str(theme_file)
                themes.append(d)
            except Exception:
                pass

        return themes

    def load_theme(self, theme_name: str) -> Optional[Theme]:
        """按名称加载主题（先查预设，再查用户目录）"""
        for preset in self._preset_themes:
            if preset.name == theme_name:
                self._current_theme = preset
                return copy.deepcopy(preset)

        theme_file = self._themes_dir / f"{theme_name}.json"
        if theme_file.exists():
            try:
                with open(theme_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                theme = Theme.from_dict(data)
                self._current_theme = theme
                return copy.deepcopy(theme)
            except Exception:
                pass

        return None

    def import_theme_from_file(self, file_path: str) -> Tuple[bool, str]:
        """从 .json 文件导入主题"""
        path = Path(file_path)
        if not path.exists():
            return False, "文件不存在"
        if path.suffix.lower() != ".json":
            return False, "仅支持 .json 格式"

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            theme = Theme.from_dict(data)
            if not theme.colors:
                return False, "主题文件中没有颜色定义"
            if not theme.name or theme.name == "Unnamed Theme":
                return False, "主题文件缺少 name 字段"

            dest_path = self._themes_dir / f"{theme.name}.json"
            with open(dest_path, "w", encoding="utf-8") as f:
                json.dump(theme.to_dict(), f, ensure_ascii=False, indent=2)
            return True, f"主题「{theme.name}」导入成功"
        except json.JSONDecodeError:
            return False, "JSON 格式无效"
        except Exception as e:
            return False, f"导入失败: {e}"

    def delete_user_theme(self, theme_name: str) -> bool:
        """删除用户导入的主题文件"""
        theme_file = self._themes_dir / f"{theme_name}.json"
        if theme_file.exists():
            try:
                theme_file.unlink()
                return True
            except Exception:
                pass
        return False

    # ─── 主题应用 ────────────────────────────────────────────

    def apply_theme(self, theme: Theme, accent_color: Optional[str] = None) -> Dict[str, str]:
        """应用主题到 COLORS 字典（直接修改全局 COLORS，所有引用自动同步）"""
        if theme.name == "default" and not theme.colors:
            COLORS.clear()
            COLORS.update(self._original_colors)
        else:
            for key in COLORS:
                if key in theme.colors:
                    COLORS[key] = theme.colors[key]

        if accent_color:
            COLORS["accent"] = accent_color
            COLORS["accent_hover"] = self._lighten_color(accent_color, 0.3)

        self._current_accent_color = accent_color or COLORS.get("accent")
        return dict(COLORS)

    def get_current_colors(self) -> Dict[str, str]:
        if self._current_theme:
            for key in COLORS:
                if key in self._current_theme.colors:
                    COLORS[key] = self._current_theme.colors[key]
            if self._current_accent_color:
                COLORS["accent"] = self._current_accent_color
                COLORS["accent_hover"] = self._lighten_color(self._current_accent_color, 0.3)
        return dict(COLORS)

    # ─── Minecraft 版本动态调色 ─────────────────────────────

    VERSION_COLOR_MAP: Dict[str, Dict[str, str]] = {
        "1.21": {
            "accent": "#6a0dad",
            "accent_hover": "#8b2fc9",
            "description": "1.21 深紫色调",
        },
        "1.20": {
            "accent": "#c9a84c",
            "accent_hover": "#dbbf6e",
            "description": "1.20 樱花金",
        },
        "1.19": {
            "accent": "#2d7d46",
            "accent_hover": "#3da85e",
            "description": "1.19 深绿色调",
        },
        "1.18": {
            "accent": "#4a8fbf",
            "accent_hover": "#6aaadf",
            "description": "1.18 天空蓝",
        },
        "1.17": {
            "accent": "#6ba35a",
            "accent_hover": "#8cc47a",
            "description": "1.17 铜绿色",
        },
        "1.16": {
            "accent": "#8b4513",
            "accent_hover": "#a05a2a",
            "description": "1.16 下界红",
        },
        "1.15": {
            "accent": "#e8a87c",
            "accent_hover": "#f0c4a4",
            "description": "1.15 蜂蜜黄",
        },
        "1.14": {
            "accent": "#f7d794",
            "accent_hover": "#fae3b4",
            "description": "1.14 竹绿",
        },
        "1.13": {
            "accent": "#4aa3df",
            "accent_hover": "#6db8e8",
            "description": "1.13 海洋蓝",
        },
        "1.12": {
            "accent": "#b07d5a",
            "accent_hover": "#c89770",
            "description": "1.12 黏土棕",
        },
        "1.11": {
            "accent": "#7f8c8d",
            "accent_hover": "#95a5a6",
            "description": "1.11 探险者灰",
        },
        "1.10": {
            "accent": "#e67e22",
            "accent_hover": "#f0933b",
            "description": "1.10 霜灼橙",
        },
        "1.9": {
            "accent": "#3498db",
            "accent_hover": "#5dade2",
            "description": "1.9 战斗更新蓝",
        },
        "1.8": {
            "accent": "#9b59b6",
            "accent_hover": "#af7ac5",
            "description": "1.8 末影紫",
        },
        "1.7": {
            "accent": "#e74c3c",
            "accent_hover": "#ec7063",
            "description": "1.7 更新红",
        },
    }

    def get_version_accent(self, version_id: str) -> Optional[Dict[str, str]]:
        """根据 Minecraft 版本 ID 获取对应强调色"""
        for ver, colors in self.VERSION_COLOR_MAP.items():
            if version_id.startswith(ver):
                return colors
        return None

    def apply_version_theme(self, version_id: str) -> Optional[Dict[str, str]]:
        """根据版本 ID 应用动态主题，返回应用后的 colors"""
        version_colors = self.get_version_accent(version_id)
        if version_colors:
            colors = dict(CURRENT_COLORS)
            colors["accent"] = version_colors["accent"]
            colors["accent_hover"] = version_colors["accent_hover"]
            return colors
        return None

    # ─── 颜色工具 ────────────────────────────────────────────

    @staticmethod
    def _lighten_color(hex_color: str, factor: float = 0.3) -> str:
        """将颜色变亮"""
        hex_color = hex_color.lstrip("#")
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        r = min(255, int(r + (255 - r) * factor))
        g = min(255, int(g + (255 - g) * factor))
        b = min(255, int(b + (255 - b) * factor))
        return f"#{r:02x}{g:02x}{b:02x}"

    @staticmethod
    def _parse_hex(hex_color: str) -> Optional[Tuple[int, int, int]]:
        hex_color = hex_color.lstrip("#")
        if len(hex_color) != 6:
            return None
        try:
            return int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        except ValueError:
            return None

    @staticmethod
    def generate_random_accent() -> str:
        """生成随机强调色"""
        r = random.randint(80, 220)
        g = random.randint(80, 220)
        b = random.randint(80, 220)
        return f"#{r:02x}{g:02x}{b:02x}"


# 单例实例（全局共享）
_engine: Optional[ThemeEngine] = None


def init_theme_engine(base_dir: str) -> ThemeEngine:
    """初始化全局主题引擎"""
    global _engine
    _engine = ThemeEngine(base_dir)
    return _engine


def get_theme_engine() -> ThemeEngine:
    """获取全局主题引擎实例"""
    if _engine is None:
        raise RuntimeError("ThemeEngine 未初始化，请先调用 init_theme_engine()")
    return _engine
