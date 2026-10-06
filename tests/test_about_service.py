"""`services/about_service.py` 的永久回归守卫（阶段 3 任务 3.4；A-08 / J-01 ~ J-03）。

守三件事：

1. **9 个鸣谢项目逐字不变**（名字、项目地址、许可证地址）—— 它们是给上游项目的
   署名，改一个字就是事实错误（对照 `ui/app_handlers.py:1589-1635`）；
2. **4 行系统信息与旧实现的取值一致**（版本 / Python / 系统 / 架构，键名也不变）；
3. **协议正文复用 `legal_service`**，读不到时返回空串（界面用 `terms_content` 摘要兜底），
   而不是抛异常或返回一句写死的中文。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, List

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.about_service import (  # noqa: E402
    ACKNOWLEDGMENTS,
    APP_NAME,
    APP_SUBTITLE,
    DONATE_URL,
    KEY_ACKNOWLEDGMENTS,
    KEY_ADDRESS,
    KEY_ARCH,
    KEY_DONATE,
    KEY_LICENSE,
    KEY_LICENSE_BTN,
    KEY_PYTHON,
    KEY_SYSTEM,
    KEY_VERSION,
    AboutService,
    acknowledgments,
    app_version,
    version_info,
)


class TestData:
    def test_app_identity(self) -> None:
        assert (APP_NAME, APP_SUBTITLE) == ("FMCL", "Fusion Minecraft Launcher")

    def test_nine_acknowledgments_in_order(self) -> None:
        """顺序也不动：旧对话框就是这么从上往下渲染的。"""
        assert [item["name"] for item in ACKNOWLEDGMENTS] == [
            "PCL-CE", "HMCL", "opencode", "minecraft-launcher-lib", "forgePY",
            "lx-music-desktop", "auto-mod-classifier", "CapsWriter-Offline", "BedrockBoot",
        ]

    def test_every_acknowledgment_has_two_links(self) -> None:
        for item in ACKNOWLEDGMENTS:
            assert set(item) == {"name", "url", "license_url"}
            assert item["url"].startswith("https://github.com/")
            assert item["license_url"].startswith("https://github.com/")
            assert item["license_url"].endswith("LICENSE")

    def test_donate_url(self) -> None:
        assert DONATE_URL == "https://ifdian.net/a/janson20"

    def test_acknowledgments_returns_a_copy(self) -> None:
        rows = acknowledgments()
        rows[0]["name"] = "HACKED"
        assert ACKNOWLEDGMENTS[0]["name"] == "PCL-CE", "调用方不许改到模块级常量"

    def test_version_info_keys(self) -> None:
        rows = version_info()
        assert [row["key"] for row in rows] == [KEY_VERSION, KEY_PYTHON, KEY_SYSTEM, KEY_ARCH]
        assert all(row["value"] for row in rows)
        assert rows[0]["value"] == app_version()

    def test_i18n_keys_exist_in_every_locale(self) -> None:
        import json

        needed = {KEY_ACKNOWLEDGMENTS, KEY_ADDRESS, KEY_LICENSE, KEY_LICENSE_BTN, KEY_DONATE,
                  KEY_VERSION, KEY_PYTHON, KEY_SYSTEM, KEY_ARCH}
        for code in ("zh_CN", "en_US", "zh_TW", "ja_JP"):
            data = json.loads((REPO_ROOT / "ui" / "locales" / f"{code}.json").read_text(encoding="utf-8"))
            missing = sorted(key for key in needed if key not in data)
            assert missing == [], f"{code}.json 缺键：{missing}"

    def test_app_version_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import services.user_agent as user_agent

        monkeypatch.setattr(user_agent, "_get_fmcl_version", lambda: "9.9.9")
        assert app_version() == "9.9.9"


class TestService:
    @pytest.fixture()
    def service(self) -> AboutService:
        return AboutService(opener=lambda url: None)

    def test_reads(self, service: AboutService) -> None:
        assert service.app_name() == APP_NAME
        assert len(service.info()) == 4
        assert len(service.acknowledgments()) == 9
        assert service.donate_url() == DONATE_URL

    def test_terms_text_reads_the_repo_file(self, service: AboutService) -> None:
        text = service.terms_text()
        assert len(text) > 500, "仓库根就有 TERMS_OF_USE.md，应该读得到全文"
        assert service.terms_available() is True

    def test_terms_text_overrides_the_path(self, tmp_path: Path) -> None:
        custom = tmp_path / "terms.md"
        custom.write_text("# 自定义协议\n", encoding="utf-8")
        service = AboutService(terms_path=custom)
        assert service.terms_text().startswith("# 自定义协议")

    def test_terms_missing_is_an_empty_string(self, tmp_path: Path) -> None:
        service = AboutService(terms_path=tmp_path / "missing.md")
        assert service.terms_text() == ""
        assert service.terms_available() is False

    def test_open_url_passes_through(self) -> None:
        opened: List[str] = []
        service = AboutService(opener=opened.append)
        result = service.open_url("https://github.com/HMCL-dev/HMCL")
        assert result["ok"] is True and opened == ["https://github.com/HMCL-dev/HMCL"]

    @pytest.mark.parametrize("url", ["", "   ", "file:///C:/windows/system32", "javascript:alert(1)", "ftp://x"])
    def test_open_url_rejects_non_http(self, url: str) -> None:
        opened: List[str] = []
        service = AboutService(opener=opened.append)
        result = service.open_url(url)
        assert result["ok"] is False and result["error"] == "unsupported_scheme"
        assert opened == [], "不认识的协议不许交给系统去打开"

    def test_open_url_reports_browser_failures(self) -> None:
        def boom(_url: str) -> None:
            raise RuntimeError("没有浏览器")

        service = AboutService(opener=boom)
        result = service.open_url("https://example.com")
        assert result["ok"] is False and "没有浏览器" in result["error"]

    def test_describe(self, service: AboutService) -> None:
        info = service.describe()
        assert info["acknowledgments"] == 9
        assert info["terms_available"] is True
        assert info["frozen"] is False

    def test_all_names_resolve(self) -> None:
        import services.about_service as module

        missing = [name for name in module.__all__ if not hasattr(module, name)]
        assert missing == []


class TestAppVersionFallback:
    def test_broken_updater_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """版本号读不到时返回 "unknown"，而不是让关于页打不开。"""
        import builtins

        import services.user_agent as user_agent

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "updater":
                raise ImportError("没有 updater")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert user_agent._get_fmcl_version() == "unknown"  # noqa: SLF001
