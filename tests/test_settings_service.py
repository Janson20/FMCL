"""`services/settings_service.py` 的永久回归守卫（阶段 3 任务 3.4）。

这一层守的是**行为**：草稿语义（M-Q1 的 B1/B2/B3/B4/B5/B6 六条边界）、
写盘顺序与"只写改动过的键"、成就的触发时机与会话去重、强调色校验、
线程数夹取、语言与主题的预览/还原、Java 扫描任务、重启命令行的拼法。

**不碰真配置、不碰真主题目录、不起真进程**：所有替身都在下面，
`FakeConfig` 还带一个写盘计数器 —— "有没有落盘"因此是可断言的数字
（不是"看着像没写"）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.settings_service import (  # noqa: E402
    ACCENT_INVALID_KEY,
    COMMIT_ACHIEVEMENTS,
    DEFAULT_THEME,
    DRAFT_KEYS,
    IMPORT_ACHIEVEMENT,
    JAVA_MODE_LABEL_KEYS,
    JAVA_MODES,
    PREVIEW_KEYS,
    THREADS_ACHIEVEMENT,
    THREADS_MAX,
    THREADS_MIN,
    SettingsService,
    clamp_threads,
    describe_changes,
    java_mode_shows_custom,
    java_mode_shows_scan,
    java_row_title,
    java_row_view,
    normalize_accent,
    theme_view,
    threads_is_multithreaded,
)
from services.theme_service import ThemeEngine, init_theme_engine  # noqa: E402

#: 界面文案要在四种语言里都找得到（少一个就会显示裸键名）
LANGUAGES = ("zh_CN", "en_US", "ja_JP", "zh_TW")


# ─── 替身 ──────────────────────────────────────────────────────


class FakeConfig:
    """配置对象替身（9 个设置键 + 语言 + 写盘计数）。"""

    def __init__(self, **overrides: Any) -> None:
        self.language = "zh_CN"
        self.theme_name = DEFAULT_THEME
        self.accent_color: Optional[str] = None
        self.dynamic_version_theme = False
        self.minimize_on_game_launch = False
        self.mirror_enabled = True
        self.download_threads = 4
        self.java_mode = "auto"
        self.java_custom_path: Optional[str] = None
        self.saves = 0
        for key, value in overrides.items():
            setattr(self, key, value)

    def save_config(self) -> bool:
        self.saves += 1
        return True


class FakeLauncher:
    """核心层替身：9 组 getter/setter（setter 记名 + 落盘）+ Java 扫描。"""

    def __init__(self, config: FakeConfig, runtimes: Optional[List[Dict[str, Any]]] = None) -> None:
        self.config = config
        self.writes: List[Tuple[str, Any]] = []
        self.scan_calls = 0
        self.runtimes = runtimes if runtimes is not None else [JAVA_ROW]
        self.scan_error: Optional[Exception] = None

    # 读侧
    def get_minimize_on_game_launch(self) -> bool:
        return bool(self.config.minimize_on_game_launch)

    def get_mirror_enabled(self) -> bool:
        return bool(self.config.mirror_enabled)

    def get_download_threads(self) -> int:
        return int(self.config.download_threads)

    def get_language(self) -> str:
        return str(self.config.language)

    def get_theme_name(self) -> str:
        return str(self.config.theme_name)

    def get_accent_color(self) -> Optional[str]:
        return self.config.accent_color

    def get_dynamic_version_theme(self) -> bool:
        return bool(self.config.dynamic_version_theme)

    def get_java_mode(self) -> str:
        return str(self.config.java_mode)

    def get_java_custom_path(self) -> Optional[str]:
        return self.config.java_custom_path

    # 写侧（真核心层的每个 setter 结尾都是 `config.save_config()`）
    def set_minimize_on_game_launch(self, enabled: bool) -> None:
        self.config.minimize_on_game_launch = bool(enabled)
        self.writes.append(("minimize_on_game_launch", bool(enabled)))
        self.config.save_config()

    def set_mirror_enabled(self, enabled: bool) -> None:
        self.config.mirror_enabled = bool(enabled)
        self.writes.append(("mirror_enabled", bool(enabled)))
        self.config.save_config()

    def set_download_threads(self, threads: int) -> None:
        self.config.download_threads = clamp_threads(threads)
        self.writes.append(("download_threads", clamp_threads(threads)))
        self.config.save_config()

    def set_language(self, code: str) -> None:
        self.config.language = str(code)
        self.writes.append(("language", str(code)))
        self.config.save_config()

    def set_theme_name(self, name: str) -> None:
        self.config.theme_name = str(name)
        self.writes.append(("theme_name", str(name)))
        self.config.save_config()

    def set_accent_color(self, color: Optional[str]) -> None:
        self.config.accent_color = color
        self.writes.append(("accent_color", color))
        self.config.save_config()

    def set_dynamic_version_theme(self, enabled: bool) -> None:
        self.config.dynamic_version_theme = bool(enabled)
        self.writes.append(("dynamic_version_theme", bool(enabled)))
        self.config.save_config()

    def set_java_mode(self, mode: str) -> None:
        self.config.java_mode = str(mode)
        self.writes.append(("java_mode", str(mode)))
        self.config.save_config()

    def set_java_custom_path(self, path: Optional[str]) -> None:
        self.config.java_custom_path = path
        self.writes.append(("java_custom_path", path))
        self.config.save_config()

    def scan_system_java(self) -> List[Dict[str, Any]]:
        self.scan_calls += 1
        if self.scan_error is not None:
            raise self.scan_error
        return [dict(item) for item in self.runtimes]


JAVA_ROW: Dict[str, Any] = {
    "path": r"D:\java\jdk21\bin\java.exe",
    "home": r"D:\java\jdk21",
    "major_version": 21,
    "version_str": "21.0.1",
    "arch": "x64",
    "is_jre": False,
}


class FakeAchievements:
    """成就引擎替身：记下 `update_progress` / `check_and_unlock` 的调用。"""

    def __init__(self, fail: bool = False) -> None:
        self.progress: List[Tuple[str, int]] = []
        self.checks: List[Tuple[str, bool]] = []
        self.fail = fail

    def update_progress(self, achievement_id: str, value: int = 1, trigger_type: Any = None) -> None:
        if self.fail:
            raise RuntimeError("成就库坏了")
        self.progress.append((achievement_id, value))

    def check_and_unlock(self, achievement_id: str, condition_met: bool) -> None:
        if self.fail:
            raise RuntimeError("成就库坏了")
        self.checks.append((achievement_id, condition_met))


class FakeI18n:
    """`services.i18n_service` 的替身（只实现设置页用到的四个函数）。"""

    def __init__(self) -> None:
        self.current = "zh_CN"
        self.calls: List[str] = []

    def get_available_languages(self) -> Dict[str, str]:
        return {"zh_CN": "简体中文", "en_US": "English", "ja_JP": "日本語", "zh_TW": "繁體中文"}

    def get_current_language(self) -> str:
        return self.current

    def set_language(self, code: str) -> bool:
        if code not in self.get_available_languages():
            return False
        self.current = code
        self.calls.append(code)
        return True


# ─── 夹具 ──────────────────────────────────────────────────────


@pytest.fixture()
def engine(tmp_path: Path) -> ThemeEngine:
    """一个干净的主题引擎。

    **必须把全局 `COLORS` 复位**：它是全进程唯一的可变字典，
    `ThemeEngine.__init__` 又会把"当时的 COLORS"记成 `_original_colors` ——
    上一个测试留下的海洋蓝主题会被下一个测试当成"原始调色板"
    （第一版就是这么红的：`before` 与 `ocean` 的强调色一模一样）。
    """
    from services.palette import COLORS

    pristine = {
        "bg_dark": "#1a1a2e", "bg_medium": "#16213e", "bg_light": "#0f3460",
        "accent": "#e94560", "accent_hover": "#ff6b81", "success": "#2ecc71",
        "warning": "#f39c12", "error": "#e74c3c", "text_primary": "#ffffff",
        "text_secondary": "#a0a0b0", "card_bg": "#1e2a4a", "card_border": "#2d3a5c",
    }
    COLORS.clear()
    COLORS.update(pristine)
    return init_theme_engine(str(tmp_path))


@pytest.fixture()
def world(tmp_path: Path, engine: ThemeEngine, monkeypatch: pytest.MonkeyPatch) -> Dict[str, Any]:
    """一套装配好的替身（配置 / 核心层 / 语言 / 成就 / 进程与打开目录）。"""
    config = FakeConfig()
    launcher = FakeLauncher(config)
    i18n = FakeI18n()
    achievements = FakeAchievements()
    spawned: List[List[str]] = []
    opened: List[str] = []
    quit_calls: List[bool] = []

    service = SettingsService(
        launcher=launcher,
        theme_engine=engine,
        i18n=i18n,
        config=config,
        spawn=lambda argv, **kwargs: spawned.append([str(x) for x in argv]),
        request_quit=lambda: quit_calls.append(True),
        opener=lambda path: opened.append(str(path)),
    )
    monkeypatch.setattr(
        SettingsService, "_achievement_engine", staticmethod(lambda: achievements)
    )
    return {
        "service": service,
        "config": config,
        "launcher": launcher,
        "i18n": i18n,
        "achievements": achievements,
        "spawned": spawned,
        "opened": opened,
        "quit": quit_calls,
    }


# ─── 纯函数 ────────────────────────────────────────────────────


class TestPureHelpers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [(1, 1), (0, 1), (-5, 1), (255, 255), (256, 255), (999, 255), (4.6, 5), ("7", 7), ("x", 4), (None, 4)],
    )
    def test_clamp_threads(self, raw: Any, expected: int) -> None:
        assert clamp_threads(raw) == expected

    def test_threads_min_max_match_the_legacy_slider(self) -> None:
        """旧滑块是 `from_=1, to=255`（`launcher_settings.py:533-567`）。"""
        assert (THREADS_MIN, THREADS_MAX) == (1, 255)

    def test_multithread_achievement_condition(self) -> None:
        """旧实现是 `_check_ach("advanced_multithread", threads > 1)`。"""
        assert threads_is_multithreaded(2) is True
        assert threads_is_multithreaded(1) is False
        assert threads_is_multithreaded(0) is False

    def test_accent_accepts_hash_and_six_hex(self) -> None:
        assert normalize_accent(" #AbCdEf ") == "#AbCdEf", "大小写与首尾空白都要放过"

    @pytest.mark.parametrize("raw", ["", "   ", None])
    def test_accent_empty_means_clear(self, raw: Any) -> None:
        assert normalize_accent(raw) is None

    @pytest.mark.parametrize("raw", ["#12345", "#1234567", "123456", "#zzzzzz", "abcdef"])
    def test_accent_rejects_invalid(self, raw: str) -> None:
        with pytest.raises(ValueError) as info:
            normalize_accent(raw)
        assert str(info.value) == ACCENT_INVALID_KEY, "错误标识必须是那个 i18n 键"

    def test_java_row_title_matches_the_legacy_fstring(self) -> None:
        """旧实现：`f"Java {major} ({kind}) - {version_str} [{arch}]"`。"""
        assert java_row_title(JAVA_ROW) == "Java 21 (JDK) - 21.0.1 [x64]"
        assert java_row_title({**JAVA_ROW, "is_jre": True}) .startswith("Java 21 (JRE)")

    def test_java_row_view_carries_the_value_to_write(self) -> None:
        view = java_row_view(JAVA_ROW)
        assert view["value"] == JAVA_ROW["path"], "选中后要写进 java_custom_path 的就是它"
        assert view["subtitle"] == JAVA_ROW["home"]

    def test_theme_view_tags_user_themes(self) -> None:
        preset = theme_view({"name": "ocean", "source": "preset"})
        user = theme_view({"name": "mine", "source": "user"}, user_tag="[user]")
        assert preset["label"] == "ocean" and preset["is_user"] is False
        assert user["label"] == "mine [user]" and user["is_user"] is True

    def test_describe_changes_treats_none_and_empty_as_equal(self) -> None:
        saved = {key: None for key in DRAFT_KEYS}
        saved["java_custom_path"] = None
        draft = dict(saved)
        draft["java_custom_path"] = ""
        assert describe_changes(saved, draft) == {}, "None 与空串都是'没设'，不该算改动"

    def test_describe_changes_reports_real_differences(self) -> None:
        saved = {key: None for key in DRAFT_KEYS}
        draft = dict(saved)
        draft["mirror_enabled"] = True
        assert list(describe_changes(saved, draft)) == ["mirror_enabled"]

    def test_java_mode_visibility_helpers(self) -> None:
        assert java_mode_shows_custom("custom") and not java_mode_shows_custom("scan")
        assert java_mode_shows_scan("scan") and not java_mode_shows_scan("auto")

    def test_java_modes_and_label_keys_are_complete(self) -> None:
        assert JAVA_MODES == ("auto", "scan", "custom")
        assert set(JAVA_MODE_LABEL_KEYS) == set(JAVA_MODES)

    def test_preview_keys_are_the_three_visible_ones(self) -> None:
        """B2 只管看得见的三项：主题、强调色、语言。"""
        assert set(PREVIEW_KEYS) == {"language", "theme_name", "accent_color"}
        assert set(COMMIT_ACHIEVEMENTS).issubset(set(DRAFT_KEYS))

    def test_module_all_names_resolve(self) -> None:
        import services.settings_service as module

        missing = [name for name in module.__all__ if not hasattr(module, name)]
        assert missing == [], f"__all__ 里有不存在的名字：{missing}"


# ─── 快照与草稿（B1 / B2） ─────────────────────────────────────


class TestDraft:
    def test_snapshot_reads_through_the_launcher(self, world: Dict[str, Any]) -> None:
        world["config"].download_threads = 300
        snapshot = world["service"].snapshot()
        assert snapshot["download_threads"] == 255, "快照也要夹取，界面与配置必须是同一个数"
        assert set(snapshot) == set(DRAFT_KEYS)

    def test_begin_draft_keeps_existing_edits(self, world: Dict[str, Any]) -> None:
        """B1：切分区回来时改动还在（不能每次进页面就重置草稿）。"""
        service = world["service"]
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        service.begin_draft()  # 页面再次进入
        assert service.current_draft()["mirror_enabled"] is False
        assert service.dirty() is True

    def test_begin_draft_force_restarts(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        service.begin_draft(force=True)
        assert service.current_draft()["mirror_enabled"] is True
        assert service.dirty() is False

    def test_begin_draft_rebases_on_external_changes(self, world: Dict[str, Any]) -> None:
        """**草稿要重定基线**（3.4 人工验收反馈的修复）。

        草稿活的时间比页面长，而"已保存的值"可能在草稿之外被改掉（最典型：
        顶栏地球切语言）。重定规则：改过的键保留、没动过的键跟随最新已保存值。
        """
        service = world["service"]
        service.begin_draft()
        service.set_draft("mirror_enabled", False)      # 用户改了这个
        world["launcher"].set_language("en_US")          # 别处改了语言（地球）
        world["config"].save_config()
        service.begin_draft()                            # 再次进设置页
        draft = service.current_draft()
        assert draft["mirror_enabled"] is False, "用户改过的键必须保留"
        assert draft["language"] == "en_US", "没动过的键要跟随最新已保存值"
        assert sorted(service.changes()) == ["mirror_enabled"], "重定之后只该剩这一项改动"

    def test_preview_does_not_revive_a_stale_language(self, world: Dict[str, Any]) -> None:
        """**用户报的"语言还是会自动变英文"的钉子**（2026-10-06 真机验收）。

        旧写法：装配期建的草稿里 `language` = 当时的语言（en_US）→ 用户用地球切到中文 →
        进设置页只改了个**主题** → 预览把整份**过期草稿**应用一遍 → 界面当场变英文，
        而 `config.json` 还是 zh_CN（更糟的是接着点保存会把 en_US 写回盘）。
        """
        service = world["service"]
        world["config"].language = "en_US"
        world["i18n"].current = "en_US"
        service.begin_draft()                            # 装配/首次进页面时的草稿
        service.set_draft("theme_name", "ocean")
        assert world["i18n"].current == "en_US"
        # 用户在别处把语言切到中文（顶栏地球：`Tr.setLanguage` → 写 config）
        world["launcher"].set_language("zh_CN")
        world["i18n"].current = "zh_CN"

        service.begin_draft()                            # 进设置页 → 重定基线
        assert service.current_draft()["language"] == "zh_CN"
        service.set_draft("theme_name", "forest")        # 只改主题（会走预览）
        assert world["i18n"].current == "zh_CN", "预览不许把外部刚切的语言按回旧的"
        assert world["launcher"].writes[-1][0] == "language", "前提：上一步确实写过语言"
        result = service.commit()
        assert "language" not in result["applied"], "语言没被改过，就不该出现在写盘清单里"
        assert world["config"].language == "zh_CN"

    def test_set_draft_does_not_write_anything(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        for key, value in (
            ("minimize_on_game_launch", True),
            ("mirror_enabled", False),
            ("download_threads", 16),
        ):
            assert service.set_draft(key, value)["ok"] is True
        assert world["launcher"].writes == []
        assert world["config"].saves == 0

    def test_set_draft_rejects_unknown_keys(self, world: Dict[str, Any]) -> None:
        result = world["service"].set_draft("nope", 1)
        assert result["ok"] is False and result["error"].startswith("unknown_key:")

    def test_set_draft_rejects_unknown_language(self, world: Dict[str, Any]) -> None:
        result = world["service"].set_draft("language", "fr_FR")
        assert result["ok"] is False and "unknown_language" in result["error"]

    def test_set_draft_rejects_unknown_theme(self, world: Dict[str, Any]) -> None:
        result = world["service"].set_draft("theme_name", "does-not-exist")
        assert result["ok"] is False and "unknown_theme" in result["error"]

    def test_set_draft_rejects_unknown_java_mode(self, world: Dict[str, Any]) -> None:
        result = world["service"].set_draft("java_mode", "psychic")
        assert result["ok"] is False and "unknown_java_mode" in result["error"]

    def test_invalid_accent_keeps_the_old_value(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        service.set_draft("accent_color", "#123456")
        result = service.set_draft("accent_color", "#12")
        assert result["ok"] is False and result["error"] == ACCENT_INVALID_KEY
        assert service.current_draft()["accent_color"] == "#123456", "非法值不许进草稿"

    def test_java_custom_path_trims_and_empties(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        assert service.set_draft("java_custom_path", "  D:\\jdk\\bin\\java.exe ")["value"] == (
            "D:\\jdk\\bin\\java.exe"
        )
        assert service.set_draft("java_custom_path", "   ")["value"] is None

    def test_preview_flag_marks_the_three_visible_keys(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        assert service.set_draft("theme_name", "ocean")["preview"] is True
        assert service.set_draft("download_threads", 8)["preview"] is False

    def test_discard_restores_preview_and_clears(self, world: Dict[str, Any], engine: ThemeEngine) -> None:
        from services.palette import COLORS

        service = world["service"]
        service.begin_draft()
        before = COLORS["accent"]
        service.set_draft("theme_name", "ocean")
        assert COLORS["accent"] != before, "预览要当场生效"
        service.discard()
        assert COLORS["accent"] == before, "取消要还原"
        assert service.dirty() is False and service.has_draft() is False

    def test_cancel_restores_the_language_too(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        service.set_draft("language", "en_US")
        assert world["i18n"].current == "en_US", "语言也要当场热切换（预览）"
        service.discard()
        assert world["i18n"].current == "zh_CN", "取消后语言退回已保存值"

    def test_language_only_preview_does_not_touch_the_theme(self, world: Dict[str, Any], engine: ThemeEngine) -> None:
        """只传 language 的预览不许动主题（`theme_name` 缺席 ≠ 切回 default）。"""
        from services.palette import COLORS

        service = world["service"]
        service.begin_draft()
        service.set_draft("theme_name", "ocean")
        accent_with_ocean = COLORS["accent"]
        service.apply_preview({"language": "en_US"})
        assert COLORS["accent"] == accent_with_ocean, "主题被按回默认了"
        assert world["i18n"].current == "en_US"

    def test_changes_only_lists_modified_keys(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        service.set_draft("download_threads", 4)  # 与基线相同
        assert sorted(service.changes()) == ["mirror_enabled"]

    def test_describe_carries_the_draft_state(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        info = service.describe()
        assert info["dirty"] is True
        assert info["changes"] == ["mirror_enabled"]
        assert info["draft"]["mirror_enabled"] is False


# ─── 提交（B3 / B4 / M-25） ────────────────────────────────────


class TestCommit:
    def test_commit_writes_only_changed_keys_in_order(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        service.set_draft("download_threads", 12)
        service.set_draft("minimize_on_game_launch", True)
        result = service.commit()
        assert result["ok"] is True
        # 写盘顺序 = DRAFT_KEYS 顺序，不是用户点击顺序
        assert [row[0] for row in world["launcher"].writes] == [
            "minimize_on_game_launch",
            "download_threads",
        ]
        assert world["config"].minimize_on_game_launch is True
        assert world["config"].download_threads == 12

    def test_commit_clears_the_draft(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        service.commit()
        assert service.dirty() is False and service.has_draft() is False

    def test_commit_without_changes_touches_nothing(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        result = service.commit()
        assert result["changed"] is False and result["applied"] == []
        assert world["launcher"].writes == []
        assert world["config"].saves == 0

    def test_achievements_fire_on_commit_not_on_edit(self, world: Dict[str, Any]) -> None:
        """B3：改控件的时候**不**触发成就。"""
        service = world["service"]
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        service.set_draft("download_threads", 8)
        service.set_draft("theme_name", "ocean")
        service.set_draft("language", "en_US")
        service.set_draft("accent_color", "#123456")
        assert world["achievements"].progress == [], "改动瞬间不该触发成就（旧实现是当场的）"
        service.commit()
        fired = [row[0] for row in world["achievements"].progress]
        assert sorted(fired) == sorted(
            [
                COMMIT_ACHIEVEMENTS["mirror_enabled"],
                COMMIT_ACHIEVEMENTS["theme_name"],
                COMMIT_ACHIEVEMENTS["language"],
                COMMIT_ACHIEVEMENTS["accent_color"],
            ]
        )
        assert world["achievements"].checks == [(THREADS_ACHIEVEMENT, True)]

    def test_cancelled_draft_never_fires_achievements(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        service.discard()
        assert world["achievements"].progress == [] and world["achievements"].checks == []

    def test_same_achievement_fires_once_per_session(self, world: Dict[str, Any]) -> None:
        """B3：同类成就一次会话只触发一次（再改再存也不重复计数）。"""
        service = world["service"]
        for value in (False, True):
            service.begin_draft()
            service.set_draft("mirror_enabled", value)
            service.commit()
        fired = [row[0] for row in world["achievements"].progress]
        assert fired == [COMMIT_ACHIEVEMENTS["mirror_enabled"]]
        assert service.fired_achievements() == [COMMIT_ACHIEVEMENTS["mirror_enabled"]]

    def test_threads_achievement_requires_more_than_one_thread(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.begin_draft()
        service.set_draft("download_threads", 1)
        service.commit()
        assert world["achievements"].checks == [], "单线程不该解锁 advanced_multithread"
        assert service.fired_achievements() == []

    def test_achievement_failure_does_not_break_the_save(self, world: Dict[str, Any]) -> None:
        world["achievements"].fail = True
        service = world["service"]
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        result = service.commit()
        assert result["ok"] is True
        assert world["config"].mirror_enabled is False

    def test_write_failure_is_reported_per_key(self, world: Dict[str, Any]) -> None:
        class Broken(FakeLauncher):
            def set_mirror_enabled(self, enabled: bool) -> None:
                raise RuntimeError("写不了")

        service = world["service"]
        world["service"]._launcher = Broken(world["config"])  # noqa: SLF001 - 注入点
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        result = service.commit()
        assert result["ok"] is False and result["errors"] == ["mirror_enabled"]
        assert "mirror_enabled" not in result["applied"]
        assert service.has_draft() is False, "失败也不该把草稿留在半提交状态"

    def test_import_theme_is_immediate_and_fires_its_own_achievement(
        self, world: Dict[str, Any], tmp_path: Path
    ) -> None:
        """B4：导入是动作型条目 —— 不进草稿，成就当场触发。"""
        import json

        theme_file = tmp_path / "my.json"
        theme_file.write_text(
            json.dumps({"name": "mine", "colors": {"accent": "#010203"}}, ensure_ascii=False),
            encoding="utf-8",
        )
        ok, message = world["service"].import_theme(str(theme_file))
        assert ok is True and "mine" in message
        assert [row[0] for row in world["achievements"].progress] == [IMPORT_ACHIEVEMENT]
        assert world["service"].has_draft() is False, "导入不该顺手开一份草稿"

    def test_import_theme_failure_is_returned_not_raised(self, world: Dict[str, Any]) -> None:
        ok, message = world["service"].import_theme(str(Path("no-such-file.json")))
        assert ok is False and message


# ─── Java 扫描（M-03 / M-04 / M-05） ───────────────────────────


class TestJavaScan:
    def test_task_returns_rows(self, world: Dict[str, Any]) -> None:
        result = world["service"].java_scan_task(None)
        assert result["error"] == ""
        assert result["count"] == 1 and result["rows"][0]["value"] == JAVA_ROW["path"]
        assert world["launcher"].scan_calls == 1

    def test_task_reports_scan_failures(self, world: Dict[str, Any]) -> None:
        world["launcher"].scan_error = RuntimeError("扫描器炸了")
        result = world["service"].java_scan_task(None)
        assert result["count"] == 0 and "扫描器炸了" in result["error"]

    def test_task_without_launcher(self, world: Dict[str, Any]) -> None:
        service = SettingsService(config=world["config"])
        result = service.java_scan_task(None)
        assert result["error"] == "launcher_unavailable"

    def test_java_modes_carry_label_keys(self, world: Dict[str, Any]) -> None:
        rows = world["service"].java_modes()
        assert [row["value"] for row in rows] == list(JAVA_MODES)
        assert all(row["label_key"] for row in rows)


# ─── 语言与主题（M-06 / M-07 / M-10 / M-11） ───────────────────


class TestLanguageAndTheme:
    def test_language_options_are_sorted_by_code(self, world: Dict[str, Any]) -> None:
        codes = [row["code"] for row in world["service"].language_options()]
        assert codes == sorted(available for available in world["i18n"].get_available_languages())
        assert codes == sorted(codes)

    def test_current_language_comes_from_i18n(self, world: Dict[str, Any]) -> None:
        world["i18n"].current = "en_US"
        assert world["service"].current_language() == "en_US"

    def test_themes_include_presets(self, world: Dict[str, Any]) -> None:
        names = [row["name"] for row in world["service"].themes()]
        assert names[:5] == ["default", "ocean", "forest", "lavender", "sunset"]

    def test_random_accent_is_a_valid_color(self, world: Dict[str, Any]) -> None:
        color = world["service"].random_accent()
        assert normalize_accent(color) == color, "随机色本身必须过校验"

    def test_version_accent_lookup(self, world: Dict[str, Any]) -> None:
        assert world["service"].version_accent("1.21.1")["accent"] == "#6a0dad"
        assert world["service"].version_accent("9.9.9") == {}

    def test_preview_without_engine_is_a_no_op(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """引擎真的取不到时（`get_theme_engine()` 抛），预览要**安静地失败**，不许抛给界面。"""
        import services.settings_service as module

        def boom() -> ThemeEngine:
            raise RuntimeError("ThemeEngine 未初始化")

        monkeypatch.setattr(module, "get_theme_engine", boom)
        service = SettingsService(theme_engine=None)
        assert service.engine() is None
        assert service.preview_theme("ocean") is False
        assert service.preview_accent("#123456")["ok"] is False
        assert service.themes() == []


# ─── 重启（M-24 / B5） ─────────────────────────────────────────


class TestRestart:
    def test_restart_argv_uses_orig_argv(self, world: Dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "orig_argv", ["py", "-X", "utf8", "main_qml.py"], raising=False)
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        assert world["service"].restart_argv() == ["py", "-X", "utf8", "main_qml.py"]

    def test_restart_argv_falls_back_to_the_entry_script(
        self, world: Dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(sys, "orig_argv", raising=False)
        monkeypatch.setattr(sys, "frozen", False, raising=False)
        argv = world["service"].restart_argv()
        assert argv[0] == sys.executable and argv[1].endswith("main_qml.py")

    def test_restart_argv_frozen(self, world: Dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "frozen", True, raising=False)
        assert world["service"].restart_argv() == [sys.executable]

    def test_save_and_restart_saves_then_spawns_then_quits(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.set_quit_handler(lambda: world["quit"].append(True))
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        result = service.save_and_restart()
        assert result["ok"] is True and result["stage"] == "quit"
        assert world["config"].mirror_enabled is False, "先落盘"
        assert len(world["spawned"]) == 1, "再拉新进程"
        assert world["quit"] == [True], "最后才请求退出"

    def test_spawn_failure_does_not_quit(self, world: Dict[str, Any]) -> None:
        """**有意偏离旧实现**：拉不起新进程时留在原地，别把用户关在门外。"""
        def boom(argv: Any, **kwargs: Any) -> None:
            raise OSError("进程创建失败")

        service = SettingsService(
            launcher=world["launcher"],
            theme_engine=world["service"].engine(),
            i18n=world["i18n"],
            config=world["config"],
            spawn=boom,
            request_quit=lambda: world["quit"].append(True),
        )
        result = service.save_and_restart()
        assert result["ok"] is False and result["stage"] == "spawn"
        assert world["quit"] == [], "拉不起来就不许退出"

    def test_save_failure_does_not_spawn(self, world: Dict[str, Any]) -> None:
        class Broken(FakeLauncher):
            def set_mirror_enabled(self, enabled: bool) -> None:
                raise RuntimeError("写不了")

        service = SettingsService(
            launcher=Broken(world["config"]),
            theme_engine=world["service"].engine(),
            i18n=world["i18n"],
            config=world["config"],
            spawn=lambda argv, **kwargs: world["spawned"].append(list(argv)),
            request_quit=lambda: world["quit"].append(True),
        )
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        result = service.save_and_restart()
        assert result["ok"] is False and result["stage"] == "commit"
        assert world["spawned"] == [] and world["quit"] == []

    def test_missing_quit_handler_is_reported(self, world: Dict[str, Any]) -> None:
        service = world["service"]
        service.set_quit_handler(None)
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        result = service.save_and_restart()
        assert result["ok"] is False and result["stage"] == "quit"


# ─── 服务集成：与 launcher 缺席时的回退 ───────────────────────


class TestFallbacks:
    def test_commit_falls_back_to_the_config_object(self, world: Dict[str, Any]) -> None:
        service = SettingsService(config=world["config"], theme_engine=world["service"].engine())
        service.__dict__["_launcher"] = None
        service.begin_draft()
        service.set_draft("minimize_on_game_launch", True)
        result = service.commit()
        assert result["ok"] is True
        assert world["config"].minimize_on_game_launch is True
        assert world["config"].saves == 1, "没有 launcher 时由服务自己存一次盘"

    def test_context_is_used_when_nothing_is_injected(self, world: Dict[str, Any]) -> None:
        from app.context import AppContext

        ctx = AppContext()
        cfg = FakeConfig()
        launcher = FakeLauncher(cfg)
        ctx.register_instance("launcher", launcher, replace=True)
        ctx.register_instance("config", cfg, replace=True)
        service = SettingsService(context=ctx)
        assert service.snapshot()["mirror_enabled"] is True
        service.begin_draft()
        service.set_draft("mirror_enabled", False)
        service.commit()
        assert [row[0] for row in launcher.writes] == ["mirror_enabled"]

    def test_i18n_defaults_to_the_real_module(self) -> None:
        service = SettingsService()
        codes = set(service.available_languages())
        assert codes == set(LANGUAGES), "四个语言都要在（缺了就显示裸键名）"

    def test_language_options_keys_exist_in_every_locale(self) -> None:
        """界面用到的 i18n 键在四种语言里都要有（服务只给键名，译文在语言文件里）。"""
        import json

        needed = set(JAVA_MODE_LABEL_KEYS.values()) | {
            "settings_language_hint",
            "settings_minimize_hint",
            "settings_mirror_hint",
            "settings_java_scanning",
            "settings_java_path_set",
            "settings_unsaved_tag",
            "settings_unsaved_count",
            "settings_unsaved_confirm",
            "settings_service_missing",
            "settings_save_restart",
            "settings_restart_failed",
            "settings_log_export",
            "settings_log_export_title",
            "settings_log_cleared",
            "settings_log_path_label",
            "settings_log_lines",
            "settings_log_empty",
        }
        for code in LANGUAGES:
            path = REPO_ROOT / "ui" / "locales" / f"{code}.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            missing = sorted(key for key in needed if key not in data)
            assert missing == [], f"{code}.json 缺键：{missing}"
