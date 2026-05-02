"""主题引擎单元测试"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ui.theme_engine import ThemeEngine, Theme
from ui.constants import COLORS


def test_theme_engine():
    engine = ThemeEngine('.')
    assert len(engine._preset_themes) == 5

    original_accent = COLORS["accent"]

    theme = engine.load_theme('ocean')
    assert theme is not None
    assert theme.name == 'ocean'

    engine.apply_theme(theme)
    assert COLORS["accent"] == '#00b4d8', f'Expected #00b4d8, got {COLORS["accent"]}'

    engine.load_theme('default')
    engine.apply_theme(engine._current_theme)
    assert COLORS["accent"] == original_accent

    version_colors = engine.get_version_accent('1.21')
    assert version_colors is not None
    assert version_colors['accent'] == '#6a0dad'

    no_color = engine.get_version_accent('1.6')
    assert no_color is None

    random_color = ThemeEngine.generate_random_accent()
    assert random_color.startswith('#') and len(random_color) == 7

    lightened = engine._lighten_color('#e94560', 0.3)
    assert lightened.startswith('#') and len(lightened) == 7

    available = engine.get_available_themes()
    assert len(available) >= 5

    print("All theme engine tests passed!")


if __name__ == '__main__':
    test_theme_engine()
