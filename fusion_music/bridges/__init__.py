"""QML ↔ Python 桥接层。"""

from .search import SearchController
from .library import LibraryController
from .discover import DiscoverController
from .settings import SettingsController

__all__ = ["SearchController", "LibraryController", "DiscoverController", "SettingsController"]
