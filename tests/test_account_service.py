"""`services/account_service.py` 的永久回归守卫（阶段 3 任务 3.1 的读侧）。

守三件事（对照表 B-16 / B-17 / A-25）：

1. **账号读侧**：没账号时给"空摘要"而不是抛异常；有账号时把类型翻译成 i18n 键
   （旧实现把这张映射表在四个窗口里各写了一遍）；
2. **皮肤**：尺寸校验的四种合法尺寸、非法尺寸的消息键与参数、复制到
   `.minecraft/skins/`、移除；
3. **Token 刷新**：失败不抛（旧实现只记日志）。

皮肤测试用真 PNG（PIL 是本仓库既有依赖），不是"假装是图片的字节"——
尺寸校验读的就是 PIL 的 `img.size`，用假文件测不出真行为。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.context import AppContext  # noqa: E402
from services.account_service import ACCOUNT_TYPE_KEYS, SKIN_SIZES, AccountService  # noqa: E402

# ─── 替身 ──────────────────────────────────────────────────────


class FakeAccount:
    def __init__(self, name: str = "Steve", kind: str = "offline", uuid: Optional[str] = None) -> None:
        self.id = f"id-{name}"
        self.name = name
        self.uuid = uuid
        self.account_type = type("T", (), {"value": kind})()
        self.display_name = name


class FakeSystem:
    def __init__(self, accounts: Optional[List[FakeAccount]] = None, current: Optional[FakeAccount] = None) -> None:
        self.accounts = accounts or []
        self.current_account = current
        self.refreshed = 0
        self.switched: List[str] = []

    def auto_refresh_all_tokens(self) -> int:
        self.refreshed += 1
        return 2

    def set_current_account(self, account_id: str) -> bool:
        self.switched.append(account_id)
        return True


class BrokenSystem:
    @property
    def current_account(self) -> Any:
        raise RuntimeError("账号文件坏了")

    @property
    def accounts(self) -> Any:
        raise RuntimeError("账号文件坏了")

    def auto_refresh_all_tokens(self) -> int:
        raise RuntimeError("网络炸了")


class FakeLauncher:
    def __init__(self, minecraft_dir: Path) -> None:
        self.minecraft_dir = minecraft_dir
        self.skin_path: Optional[str] = None
        self.writes: List[Optional[str]] = []

    def get_skin_path(self) -> Optional[str]:
        return self.skin_path

    def set_skin_path(self, path: Optional[str]) -> None:
        self.skin_path = path
        self.writes.append(path)

    def get_minecraft_dir(self) -> str:
        return str(self.minecraft_dir)


def make_service(
    system: Any = None, launcher: Any = None
) -> AccountService:
    ctx = AppContext()
    service = AccountService(system=system, launcher=launcher)
    ctx.register(service)
    return service


def make_png(path: Path, size: tuple) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (0, 0, 0, 0)).save(path)
    return path


# ─── B-16 账号读侧 ─────────────────────────────────────────────


class TestAccountRead:
    def test_no_account_system_returns_empty_summary(self) -> None:
        service = make_service(system=None)
        summary = service.account_summary()
        assert summary["has_account"] is False
        assert summary["name"] == ""
        assert service.current_account() is None
        assert service.accounts() == []

    def test_no_current_account_is_reported_as_empty(self) -> None:
        service = make_service(system=FakeSystem(accounts=[], current=None))
        summary = service.account_summary()
        assert summary["has_account"] is False

    def test_current_account_is_normalised_with_the_type_key(self) -> None:
        account = FakeAccount("Alex", "microsoft", "uuid-1")
        service = make_service(system=FakeSystem([account], account))
        summary = service.account_summary()
        assert summary == {
            "has_account": True,
            "id": "id-Alex",
            "name": "Alex",
            "type": "microsoft",
            "type_key": "account_type_microsoft",
            "uuid": "uuid-1",
            "display_name": "Alex",
        }

    def test_every_account_type_has_an_i18n_key(self) -> None:
        """三种类型都要有键 —— 旧实现那张表散在四个窗口里，缺一种就显示空白。"""
        assert set(ACCOUNT_TYPE_KEYS) == {"microsoft", "offline", "yggdrasil"}
        import json

        for lang in ("zh_CN", "en_US", "zh_TW", "ja_JP"):
            table = json.loads((REPO_ROOT / "ui" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            for key in ACCOUNT_TYPE_KEYS.values():
                assert key in table, f"{lang} 缺少账号类型键 {key}"

    def test_account_snapshot_never_leaks_tokens(self) -> None:
        """快照里不许出现 access/refresh/client token（界面用不到，泄漏面越小越好）。"""
        account = FakeAccount("Alex", "microsoft")
        account.access_token = "secret"
        account.refresh_token = "secret"
        account.client_token = "secret"
        snapshot = AccountService.account_snapshot(account)
        assert not any("token" in key for key in snapshot)

    def test_broken_system_degrades_instead_of_raising(self) -> None:
        service = make_service(system=BrokenSystem())
        assert service.current_account() is None
        assert service.accounts() == []
        assert service.auto_refresh_tokens() == 0

    def test_set_current_account_delegates(self) -> None:
        system = FakeSystem()
        service = make_service(system=system)
        assert service.set_current_account("id-1") is True
        assert system.switched == ["id-1"]


class TestTokenRefresh:
    def test_auto_refresh_returns_the_count(self) -> None:
        system = FakeSystem()
        service = make_service(system=system)
        assert service.auto_refresh_tokens() == 2
        assert system.refreshed == 1

    def test_without_a_system_it_is_zero(self) -> None:
        assert make_service(system=None).auto_refresh_tokens() == 0


# ─── B-17 皮肤 ─────────────────────────────────────────────────


class TestSkin:
    def test_skin_path_reads_the_launcher_config(self, tmp_path: Path) -> None:
        launcher = FakeLauncher(tmp_path)
        launcher.skin_path = "C:/skins/a.png"
        service = make_service(launcher=launcher)
        assert service.skin_path() == "C:/skins/a.png"

    def test_skin_path_is_empty_without_a_launcher(self) -> None:
        assert make_service(launcher=None).skin_path() == ""

    @pytest.mark.parametrize("size", SKIN_SIZES)
    def test_all_four_sizes_are_accepted(self, tmp_path: Path, size: tuple) -> None:
        launcher = FakeLauncher(tmp_path / ".minecraft")
        service = make_service(launcher=launcher)
        skin = make_png(tmp_path / "skins" / f"{size[0]}x{size[1]}.png", size)

        ok, key, params = service.select_skin(str(skin))
        assert ok is True, f"{size} 应该被接受"
        assert key == "skin_installed"
        assert params == {"filename": skin.name}

    def test_invalid_size_is_rejected_with_width_and_height(self, tmp_path: Path) -> None:
        launcher = FakeLauncher(tmp_path / ".minecraft")
        service = make_service(launcher=launcher)
        skin = make_png(tmp_path / "bad.png", (32, 32))

        ok, key, params = service.select_skin(str(skin))
        assert ok is False
        assert key == "skin_size_invalid"
        assert params == {"width": 32, "height": 32}
        assert launcher.writes == [], "尺寸不合格时连 skin_path 都不该写"

    def test_selected_skin_is_written_and_copied(self, tmp_path: Path) -> None:
        mc_dir = tmp_path / ".minecraft"
        launcher = FakeLauncher(mc_dir)
        service = make_service(launcher=launcher)
        skin = make_png(tmp_path / "hero.png", (64, 64))

        ok, _key, _params = service.select_skin(str(skin))
        assert ok is True
        assert launcher.skin_path == str(skin)
        copied = mc_dir / "skins" / "hero.png"
        assert copied.is_file(), "皮肤必须复制到 .minecraft/skins/（游戏从那里读）"
        assert copied.read_bytes() == skin.read_bytes()

    def test_missing_file_is_a_file_error(self, tmp_path: Path) -> None:
        service = make_service(launcher=FakeLauncher(tmp_path))
        ok, key, params = service.select_skin(str(tmp_path / "nope.png"))
        assert (ok, key, params) == (False, "skin_file_error", {})

    def test_not_an_image_is_a_file_error(self, tmp_path: Path) -> None:
        service = make_service(launcher=FakeLauncher(tmp_path / ".minecraft"))
        broken = tmp_path / "broken.png"
        broken.write_text("not a png", encoding="utf-8")
        ok, key, _params = service.select_skin(str(broken))
        assert (ok, key) == (False, "skin_file_error")

    def test_without_a_launcher_selection_fails_cleanly(self, tmp_path: Path) -> None:
        service = make_service(launcher=None)
        skin = make_png(tmp_path / "hero.png", (64, 64))
        ok, key, _params = service.select_skin(str(skin))
        assert (ok, key) == (False, "skin_file_error")

    def test_remove_skin_clears_the_path(self, tmp_path: Path) -> None:
        launcher = FakeLauncher(tmp_path / ".minecraft")
        launcher.skin_path = "C:/skins/a.png"
        service = make_service(launcher=launcher)
        service.remove_skin()
        assert launcher.skin_path is None
        assert launcher.writes == [None]

    def test_remove_without_a_launcher_is_a_no_op(self) -> None:
        make_service(launcher=None).remove_skin()  # 不抛异常即通过

    def test_message_keys_exist_in_all_locales(self) -> None:
        """服务回的是**消息键**，所以这些键必须在 4 个语言文件里（界面上才不会显示键名）。"""
        import json

        wanted = {"skin_installed", "skin_size_invalid", "skin_file_error", "skin_remove"}
        for lang in ("zh_CN", "en_US", "zh_TW", "ja_JP"):
            table = json.loads((REPO_ROOT / "ui" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
            missing = sorted(key for key in wanted if key not in table)
            assert missing == [], f"{lang} 缺少 {missing}"

    def test_skin_achievement_is_triggered(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        triggered: List[str] = []

        class FakeEngine:
            def update_progress(self, achievement_id: str, value: int = 1, trigger_type: Any = None) -> None:
                triggered.append(achievement_id)

        import achievement_engine

        monkeypatch.setattr(achievement_engine, "get_achievement_engine", lambda: FakeEngine())
        launcher = FakeLauncher(tmp_path / ".minecraft")
        service = make_service(launcher=launcher)
        skin = make_png(tmp_path / "hero.png", (64, 64))
        service.select_skin(str(skin))
        assert triggered == ["personalize_skin"]

    def test_broken_achievement_engine_does_not_block_the_skin(self, tmp_path: Path) -> None:
        import achievement_engine

        def boom() -> Any:
            raise RuntimeError("成就库坏了")

        original = achievement_engine.get_achievement_engine
        achievement_engine.get_achievement_engine = boom  # type: ignore[assignment]
        try:
            launcher = FakeLauncher(tmp_path / ".minecraft")
            service = make_service(launcher=launcher)
            skin = make_png(tmp_path / "hero.png", (64, 64))
            ok, _key, _params = service.select_skin(str(skin))
            assert ok is True
        finally:
            achievement_engine.get_achievement_engine = original  # type: ignore[assignment]


class TestSkinSizeTable:
    def test_the_four_sizes_match_the_old_implementation(self) -> None:
        """与旧 `ui/app_base.py:987` 的四个尺寸逐字一致（改这里等于改用户可见行为）。"""
        assert SKIN_SIZES == ((64, 64), (64, 32), (128, 128), (128, 64))


def test_service_has_no_ui_imports() -> None:
    """服务层零 UI 依赖（阶段 1 的硬约束，这里对新服务再钉一次）。"""
    source = (REPO_ROOT / "services" / "account_service.py").read_text(encoding="utf-8")
    for forbidden in ("import tkinter", "import customtkinter", "PySide6", "from ui"):
        assert forbidden not in source
    game_source = (REPO_ROOT / "services" / "game_service.py").read_text(encoding="utf-8")
    for forbidden in ("import tkinter", "import customtkinter", "PySide6", "from ui"):
        assert forbidden not in game_source
