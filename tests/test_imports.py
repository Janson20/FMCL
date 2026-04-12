"""基础导入测试 - 验证所有模块可正常导入"""

import importlib
import sys

import pytest


# mirror.py 仅依赖标准库和 requests，可在无 GUI 环境下导入
@pytest.mark.parametrize("module", ["config", "mirror"])
def test_module_import(module):
    """测试非 GUI 模块可正常导入"""
    mod = importlib.import_module(module)
    assert mod is not None


def test_config_class():
    """测试 Config 类的基本功能"""
    from config import Config

    cfg = Config()
    assert hasattr(cfg, "mirror_enabled")
    assert hasattr(cfg, "download_threads")
    assert cfg.mirror_enabled is True
    assert cfg.download_threads == 4


def test_mirror_source():
    """测试 MirrorSource 类的基本功能"""
    from mirror import MirrorSource

    ms = MirrorSource()
    assert hasattr(ms, "enabled")
    assert hasattr(ms, "rewrite_url")
