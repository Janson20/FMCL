"""崩溃日志编码回退的回归守卫（阶段 1.23 缺陷修复）。

**缺陷**：`read_file_tail` 与 `diagnose_crash` / `collect_*_ai_context` 里的
三档编码回退，每一档都用 `errors="ignore"`。而 `errors="ignore"` 意味着
`UnicodeDecodeError` **永远不会抛出** —— 于是 `except ...: continue` 那级回退
**永不执行**：

- gbk / latin-1 两级是不可达的死代码；
- GBK 编码的日志被当作 utf-8 解码，中文变成乱码（字节流里恰好构成合法
  UTF-8 序列的部分会被解成西里尔字母之类的字符），送给 AI 的上下文是坏的。

影响用户可见：本机日志若是 GBK（中文 Windows 上很常见），崩溃分析质量下降。

**修法**：前两档改为严格解码（失败才换下一档），latin-1 作为最后一档
（它把每个字节一一映射成码点，永不失败，所以必须放最后）。

顺带把 `readlines()[-lines:]` 的隐含语义写明：`lines=0` 时 `-0 == 0` 取到整份
文件、`lines<0` 时变成"掐掉开头若干行" —— 两种都不像本意，现在统一为
"`lines <= 0` 表示不限行数"。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

crash_service = importlib.import_module("services.crash_service")
read_file_tail = crash_service.read_file_tail

GBK_TEXT = "[12:00:00] [Server thread/INFO]: 玩家 小明 加入了游戏\n崩溃原因：内存不足\n"
UTF8_TEXT = "[12:00:00] [main/ERROR]: 初始化失败：无法加载资源包\n"


def old_read_file_tail(filepath: str, lines: int = 200) -> str:
    """修复前的写法（原样复刻），用于对照 —— 让"修的是真问题"有据可查。"""
    import os

    if not filepath or not os.path.exists(filepath):
        return ""
    try:
        for enc in ("utf-8", "gbk", "latin-1"):
            try:
                with open(filepath, "r", encoding=enc, errors="ignore") as f:
                    return "".join(f.readlines()[-lines:])
            except (UnicodeDecodeError, UnicodeError):
                continue
    except Exception:
        pass
    return ""


class TestGbkFallbackIsNowReachable:
    def test_gbk_log_reads_back_exactly(self, tmp_path):
        path = tmp_path / "gbk.log"
        path.write_bytes(GBK_TEXT.encode("gbk"))
        assert read_file_tail(str(path)) == GBK_TEXT

    def test_control_old_behaviour_mangled_the_chinese(self, tmp_path):
        """对照：修复前中文读不出来（证明这不是"假想缺陷"）。"""
        path = tmp_path / "gbk.log"
        path.write_bytes(GBK_TEXT.encode("gbk"))
        old = old_read_file_tail(str(path))
        assert "小明" not in old, f"旧实现竟然读对了？{old!r}"
        assert "内存不足" not in old

    def test_utf8_behaviour_unchanged(self, tmp_path):
        path = tmp_path / "utf8.log"
        path.write_bytes(UTF8_TEXT.encode("utf-8"))
        assert read_file_tail(str(path)) == UTF8_TEXT
        assert read_file_tail(str(path)) == old_read_file_tail(str(path))


class TestLineSemantics:
    def test_tail_of_n_lines(self, tmp_path):
        path = tmp_path / "multi.log"
        path.write_text("".join(f"line-{i}\n" for i in range(10)), encoding="utf-8")
        assert read_file_tail(str(path), 3) == "line-7\nline-8\nline-9\n"

    def test_non_positive_means_no_limit(self, tmp_path):
        """`lines<=0` 显式表示"不限行数"。

        修复前：`lines=0` 因为 `-0 == 0` 恰好取到全文，而 `lines<0` 会变成
        "掐掉开头若干行" —— 后者显然不是本意。
        """
        path = tmp_path / "multi.log"
        content = "".join(f"line-{i}\n" for i in range(10))
        path.write_text(content, encoding="utf-8")
        assert read_file_tail(str(path), 0) == content
        assert read_file_tail(str(path), -1) == content
        assert old_read_file_tail(str(path), -1) != content, "对照：旧实现在此语义不同"

    def test_more_lines_than_present(self, tmp_path):
        path = tmp_path / "multi.log"
        content = "".join(f"line-{i}\n" for i in range(10))
        path.write_text(content, encoding="utf-8")
        assert read_file_tail(str(path), 100) == content


class TestLastResortStillWorks:
    def test_undecodable_bytes_do_not_yield_empty(self, tmp_path):
        """非法字节文件必须仍能读出可读部分，而不是返回空串。

        三档编码里 latin-1 永不失败，因此是可靠的最后一档 —— 这也是它必须
        排在最后的原因（否则它会把任何字节串都"成功"解出来，让后面两档失效）。
        """
        path = tmp_path / "mixed.log"
        path.write_bytes(b"valid ascii line\n" + bytes([0x81, 0x40, 0xFF]) + b"\n")
        out = read_file_tail(str(path))
        assert "valid ascii line" in out

    def test_missing_and_empty_paths(self, tmp_path):
        assert read_file_tail(str(tmp_path / "nope.log")) == ""
        assert read_file_tail("") == ""


@pytest.mark.parametrize("func_name", ["diagnose_crash", "collect_ai_context", "collect_server_ai_context"])
def test_other_encoding_sites_also_use_strict(func_name):
    """同一个 `errors="ignore"` 模式在服务里有 4 处，必须一起修。

    这里用源码断言而不是行为断言：另外三处的行为依赖调用上下文（崩溃文件字典、
    缓冲区、服务器日志行），逐个构造输入成本高且脆弱；而"是否还留着
    `errors="ignore"`" 恰好是这次缺陷的机械特征。
    """
    src = (Path(crash_service.__file__)).read_text(encoding="utf-8")
    assert 'read_text(enc, errors="ignore")' not in src, "仍有一档编码回退用的是 errors=ignore（回退不可达）"
    assert 'errors="ignore")\n' not in src.split("body = e.read()")[0], "文件读取路径不应再用 errors=ignore"
