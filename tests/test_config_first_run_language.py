"""`config.language_chosen` 的读写与**迁移规则**（A-27 首次启动选语言的地基）。

为什么单独一个文件：这条迁移规则决定"谁会看到选语言弹窗"，
写错了要么让所有老用户升级后被问一次（烦），要么让新用户永远不被问（功能没生效）。
判据全部用**临时目录里的 config.json**，不碰仓库里那份。
"""

from __future__ import annotations

import json
import platform
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config import Config  # noqa: E402

pytestmark = pytest.mark.skipif(
    platform.system().lower() == "linux",
    reason="`Config(base_dir=...)` 在 Linux 上被刻意忽略（见 config.py:234 的说明）",
)


def make_config(tmp_path: Path, payload: dict) -> Config:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    return Config(base_dir=str(tmp_path))


class TestMigration:
    def test_old_config_with_language_counts_as_chosen(self, tmp_path: Path) -> None:
        """老配置里有 `language` 键 ⇒ 视为已选过（不能把老用户全问一遍）。"""
        cfg = make_config(tmp_path, {"language": "en_US", "player_name": "Steve"})
        assert cfg.language == "en_US"
        assert cfg.language_chosen is True

    def test_fresh_install_is_not_chosen(self, tmp_path: Path) -> None:
        """全新配置（连 language 键都没有）⇒ 要问一次。"""
        cfg = make_config(tmp_path, {"player_name": "Steve"})
        assert cfg.language_chosen is False

    def test_explicit_false_wins_over_the_migration(self, tmp_path: Path) -> None:
        """显式写了 `language_chosen: false` 就以它为准（例如用户重置了选择）。"""
        cfg = make_config(tmp_path, {"language": "ja_JP", "language_chosen": False})
        assert cfg.language == "ja_JP"
        assert cfg.language_chosen is False

    def test_missing_file_is_not_chosen(self, tmp_path: Path) -> None:
        tmp_path.mkdir(parents=True, exist_ok=True)
        cfg = Config(base_dir=str(tmp_path))
        assert cfg.language_chosen is False


class TestPersistence:
    def test_save_writes_the_flag(self, tmp_path: Path) -> None:
        cfg = make_config(tmp_path, {"player_name": "Steve"})
        assert cfg.language_chosen is False
        cfg.language = "zh_TW"
        cfg.language_chosen = True
        cfg.save_config()

        reloaded = Config(base_dir=str(tmp_path))
        assert reloaded.language_chosen is True
        assert reloaded.language == "zh_TW"

    def test_saved_payload_contains_the_flag(self, tmp_path: Path) -> None:
        """落盘内容里必须有这个键 —— 否则下次启动又会问一遍。"""
        cfg = make_config(tmp_path, {"player_name": "Steve"})
        cfg.language_chosen = True
        cfg.save_config()
        data = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
        assert data.get("language_chosen") is True
