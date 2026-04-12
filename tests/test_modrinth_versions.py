"""测试 modrinth.py 版本解析与压缩功能 - 旧格式 + 新格式 (YY.D.H)"""

import pytest
from unittest.mock import patch


# ── _is_new_version_format 测试 ──


class TestIsNewVersionFormat:
    """测试新版本格式判断"""

    def test_legacy_format_1_x(self):
        from modrinth import _is_new_version_format
        assert _is_new_version_format("1.21") is False

    def test_legacy_format_1_x_y(self):
        from modrinth import _is_new_version_format
        assert _is_new_version_format("1.21.1") is False

    def test_legacy_format_1_20_4(self):
        from modrinth import _is_new_version_format
        assert _is_new_version_format("1.20.4") is False

    def test_new_format_26_1(self):
        from modrinth import _is_new_version_format
        assert _is_new_version_format("26.1") is True

    def test_new_format_26_1_1(self):
        from modrinth import _is_new_version_format
        assert _is_new_version_format("26.1.1") is True

    def test_new_format_27_3_2(self):
        from modrinth import _is_new_version_format
        assert _is_new_version_format("27.3.2") is True

    def test_invalid_version(self):
        from modrinth import _is_new_version_format
        assert _is_new_version_format("abc") is False

    def test_single_number(self):
        from modrinth import _is_new_version_format
        assert _is_new_version_format("26") is False

    def test_old_year_number(self):
        from modrinth import _is_new_version_format
        # 20.6 不是新格式 (yy < 26)，看起来像旧 loader version
        assert _is_new_version_format("20.6") is False


# ── parse_game_version_from_version 测试 ──


class TestParseGameVersionFromVersion:
    """测试从版本 ID 中提取游戏版本号"""

    def test_forge_legacy(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("1.20.4-forge-49.0.26") == "1.20.4"

    def test_fabric_legacy(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("fabric-loader-0.15.11-1.20.4") == "1.20.4"

    def test_neoforge_loader_version(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("neoforge-20.6.139") == "1.20.6"

    def test_neoforge_with_prefix(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("1.20.6-neoforge-20.6.139") == "1.20.6"

    def test_neoforge_21_0(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("neoforge-21.0.167") == "1.21"

    # ── 新格式测试 ──

    def test_new_format_major(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("26.1") == "26.1"

    def test_new_format_hotfix(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("26.1.1") == "26.1.1"

    def test_new_format_with_forge(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("26.1-forge-1.0.0") == "26.1"

    def test_new_format_hotfix_with_fabric(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("26.1.1-fabric-0.16.0") == "26.1.1"

    def test_new_format_fabric_loader_prefix(self):
        """测试 fabric-loader- 开头的新格式版本 (不以 MC 版本号开头)"""
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("fabric-loader-0.16.0-26.1.1") == "26.1.1"

    def test_new_format_fabric_loader_prefix_major(self):
        """测试 fabric-loader- 开头的新格式主版本"""
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("fabric-loader-0.16.0-26.1") == "26.1"

    def test_new_format_snapshot(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("26.1-snapshot-1") == "26.1"

    def test_new_format_pre_release(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("26.1-pre-1") == "26.1"

    def test_new_format_rc(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("26.1-rc-1") == "26.1"

    def test_new_format_27_3_2_neoforge(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("27.3.2-neoforge-1.0.0") == "27.3.2"

    def test_new_format_year_26(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("26.2") == "26.2"

    def test_new_format_year_26_hotfix(self):
        from modrinth import parse_game_version_from_version
        assert parse_game_version_from_version("26.2.3") == "26.2.3"


# ── compress_game_versions 测试 ──


class TestCompressGameVersions:
    """测试版本列表压缩展示"""

    @patch("modrinth._fetch_all_game_versions")
    @patch("modrinth._new_versions_cache", None)
    def test_legacy_single_version(self, mock_fetch):
        from modrinth import compress_game_versions
        mock_fetch.return_value = {21: {0, 1, 2, 3, 4}}
        result = compress_game_versions(["1.21"])
        assert result == "1.21"

    @patch("modrinth._fetch_all_game_versions")
    @patch("modrinth._new_versions_cache", None)
    def test_legacy_full_coverage(self, mock_fetch):
        from modrinth import compress_game_versions
        mock_fetch.return_value = {20: {0, 1, 2, 3, 4, 5, 6}}
        result = compress_game_versions(
            ["1.20", "1.20.1", "1.20.2", "1.20.3", "1.20.4", "1.20.5", "1.20.6"]
        )
        assert result == "1.20.x"

    @patch("modrinth._fetch_all_game_versions")
    @patch("modrinth._new_versions_cache", None)
    def test_legacy_range(self, mock_fetch):
        from modrinth import compress_game_versions
        mock_fetch.return_value = {20: {0, 1, 2, 3, 4, 5, 6}}
        result = compress_game_versions(["1.20", "1.20.1", "1.20.2"])
        assert result == "1.20-1.20.2"

    @patch("modrinth._fetch_all_game_versions")
    @patch("modrinth._new_versions_cache", None)
    def test_legacy_merged_x(self, mock_fetch):
        from modrinth import compress_game_versions
        mock_fetch.return_value = {
            16: {0, 1, 2, 3, 4, 5},
            17: {0},
            18: {0, 1, 2},
            19: {0, 1, 2, 3, 4},
        }
        result = compress_game_versions(
            ["1.16", "1.16.1", "1.16.2", "1.16.3", "1.16.4", "1.16.5",
             "1.17",
             "1.18", "1.18.1", "1.18.2",
             "1.19", "1.19.1", "1.19.2", "1.19.3", "1.19.4"]
        )
        assert "1.16.x" in result
        assert "1.18.x" in result
        assert "1.19.x" in result

    # ── 新格式测试 ──

    @patch("modrinth._fetch_all_game_versions")
    @patch("modrinth._new_versions_cache", {26: {1: {0, 1, 2, 3}}})
    def test_new_single_major(self, mock_fetch):
        from modrinth import compress_game_versions
        mock_fetch.return_value = {}
        result = compress_game_versions(["26.1"])
        assert result == "26.1"

    @patch("modrinth._fetch_all_game_versions")
    @patch("modrinth._new_versions_cache", {26: {1: {0, 1, 2, 3}}})
    def test_new_hotfix_single(self, mock_fetch):
        from modrinth import compress_game_versions
        mock_fetch.return_value = {}
        result = compress_game_versions(["26.1.1"])
        assert result == "26.1.1"

    @patch("modrinth._fetch_all_game_versions")
    @patch("modrinth._new_versions_cache", {26: {1: {0, 1, 2, 3}}})
    def test_new_full_coverage_hotfix(self, mock_fetch):
        from modrinth import compress_game_versions
        mock_fetch.return_value = {}
        result = compress_game_versions(["26.1", "26.1.1", "26.1.2", "26.1.3"])
        assert result == "26.1.x"

    @patch("modrinth._fetch_all_game_versions")
    @patch("modrinth._new_versions_cache", {26: {1: {0, 1, 2, 3}}})
    def test_new_partial_hotfix_range(self, mock_fetch):
        from modrinth import compress_game_versions
        mock_fetch.return_value = {}
        result = compress_game_versions(["26.1", "26.1.1"])
        assert result == "26.1-26.1.1"

    @patch("modrinth._fetch_all_game_versions")
    @patch("modrinth._new_versions_cache", {26: {1: {0, 1}, 2: {0, 1}}})
    def test_new_merged_x_versions(self, mock_fetch):
        from modrinth import compress_game_versions
        mock_fetch.return_value = {}
        result = compress_game_versions(
            ["26.1", "26.1.1", "26.2", "26.2.1"]
        )
        assert result == "26.1.x-26.2.x"

    @patch("modrinth._fetch_all_game_versions")
    @patch("modrinth._new_versions_cache", None)
    def test_empty_versions(self, mock_fetch):
        from modrinth import compress_game_versions
        mock_fetch.return_value = {}
        result = compress_game_versions([])
        assert result == ""

    @patch("modrinth._fetch_all_game_versions")
    @patch("modrinth._new_versions_cache", {26: {1: {0, 1, 2}}})
    def test_mixed_legacy_and_new(self, mock_fetch):
        from modrinth import compress_game_versions
        mock_fetch.return_value = {21: {0, 1, 2, 3, 4}}
        result = compress_game_versions(
            ["1.21", "1.21.1", "1.21.2", "1.21.3", "1.21.4",
             "26.1", "26.1.1", "26.1.2"]
        )
        assert "1.21.x" in result
        assert "26.1.x" in result


# ── parse_mod_loader_from_version 测试 ──


class TestParseModLoaderFromVersion:
    """测试从版本 ID 中解析模组加载器"""

    def test_fabric(self):
        from modrinth import parse_mod_loader_from_version
        assert parse_mod_loader_from_version("fabric-loader-0.15.11-1.20.4") == "fabric"

    def test_forge(self):
        from modrinth import parse_mod_loader_from_version
        assert parse_mod_loader_from_version("1.20.4-forge-49.0.26") == "forge"

    def test_neoforge(self):
        from modrinth import parse_mod_loader_from_version
        assert parse_mod_loader_from_version("neoforge-20.6.139") == "neoforge"

    def test_new_format_forge(self):
        from modrinth import parse_mod_loader_from_version
        assert parse_mod_loader_from_version("26.1-forge-1.0.0") == "forge"

    def test_new_format_fabric(self):
        from modrinth import parse_mod_loader_from_version
        assert parse_mod_loader_from_version("26.1-fabric-0.16.0") == "fabric"

    def test_no_loader(self):
        from modrinth import parse_mod_loader_from_version
        assert parse_mod_loader_from_version("1.20.4") is None
