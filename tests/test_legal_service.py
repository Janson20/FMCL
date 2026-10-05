"""`services/legal_service.py` 的回归守卫（阶段 3 任务 3.1 验收补的缺陷 D-162）。

守的是"协议**全文**真的能被读到"这件事：旧弹窗显示 `TERMS_OF_USE.md` 全文（110 行 /
16 KB），QML 版一度只显示语言文件里的一句摘要 —— 那属于功能丢失（红线 1）。
判据不写"文件里有某句话"（那会随协议文本改动而碎），而是"读出来的就是那个文件本身"。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from services.legal_service import TERMS_FILENAME, load_terms_text, terms_path  # noqa: E402


def test_default_path_points_at_the_repo_file() -> None:
    """开发态：仓库根的 `TERMS_OF_USE.md`（与 `ui/app_about._get_terms_md_path` 同一处）。"""
    path = terms_path()
    assert path.name == TERMS_FILENAME
    assert path.is_file(), f"{path} 不存在 —— 打包清单里也必须带上它"
    assert path.parent == REPO_ROOT


def test_loads_the_whole_document() -> None:
    """读出来的必须是**全文**：与文件本身逐字节一致。"""
    text = load_terms_text()
    assert text, "协议全文读出来是空串"
    assert text == terms_path().read_text(encoding="utf-8")
    assert text.startswith("#"), "Markdown 原文应保留标题标记（QML 侧用 MarkdownText 渲染）"
    # 全文的标志：旧弹窗显示的 12 节标题都在（不是语言文件里那句摘要）
    assert text.count("\n## ") >= 10, f"只读到 {text.count(chr(10) + '## ')} 个小节，像是被截断了"


def test_missing_file_returns_empty_instead_of_raising(tmp_path: Path) -> None:
    """文件不在时返回空串（界面侧用摘要兜底），不抛异常 —— 与旧实现同语义。"""
    assert load_terms_text(tmp_path / "nope.md") == ""


def test_unreadable_file_returns_empty(tmp_path: Path) -> None:
    """读不了（这里是"路径其实是个目录"）也不能抛。"""
    target = tmp_path / "TERMS_OF_USE.md"
    target.mkdir()
    assert load_terms_text(target) == ""


@pytest.mark.parametrize("lang", ["zh_CN", "en_US", "zh_TW", "ja_JP"])
def test_summary_fallback_key_exists(lang: str) -> None:
    """读不到协议时的兜底文案键必须在 4 个语言文件里（否则界面显示键名）。"""
    import json

    table = json.loads((REPO_ROOT / "ui" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))
    assert "terms_content" in table


def test_packaging_collects_the_terms_file() -> None:
    """打包清单必须把 `TERMS_OF_USE.md` 放在产物根 —— 否则打包后弹窗只能显示摘要。

    真源是 `scripts/build_plan.py` 的 `common_datas()`（两种后端共用），`build.spec`
    只是把它接进 Analysis；`tests/test_build_plan.py` 另有测试盯着这条链。
    """
    plan = (REPO_ROOT / "scripts" / "build_plan.py").read_text(encoding="utf-8")
    assert "TERMS_OF_USE.md" in plan
    spec = (REPO_ROOT / "build.spec").read_text(encoding="utf-8")
    assert "common_datas" in spec or "build_plan" in spec
