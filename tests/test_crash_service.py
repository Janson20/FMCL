"""崩溃诊断服务单元测试（阶段 1 任务 1.7）。

覆盖 ``services/crash_service.py``：崩溃原因诊断、日志尾部读取、AI 上下文组装、
以及 AI 请求本身。样本是自造的、但按真实日志形状写的（Fabric 的 mixin 注入失败、
Forge 的 Mod Loading failed、JVM OOM、OpenGL 报错）。

约定：
- **不联网**：所有 AI 调用都注入假 ``transport``；另有一个把
  ``urllib.request.urlopen`` 换成桩的用例，专门覆盖默认传输实现。
- 不写真实用户目录：``config.base_dir`` / ``log_file`` 一律指向 ``tmp_path``，
  ``structured_logger.slog`` 换成记录器，避免污染真实结构化日志。
- 钉住"只搬家不改逻辑"：若干历史上看起来奇怪的边界行为（``lines=0`` 返回整个
  文件、``errors="ignore"`` 使编码回退分支不可达）也写成用例，防止以后被
  "顺手修正"而在无人察觉的情况下改变行为。
"""

import io
import json
import platform
import urllib.error
from pathlib import Path

import pytest

import structured_logger
from services.crash_service import (
    AI_ENDPOINT,
    CRASH_TYPES,
    CrashService,
    analyze_crash,
    analyze_server_crash,
    collect_ai_context,
    collect_server_ai_context,
    diagnose_crash,
    read_file_tail,
)
from services.errors import NetworkError

# ─── 真实感样本 ─────────────────────────────────────────────

MIXIN_CRASH_REPORT = """---- Minecraft Crash Report ----
// Don't be sad, have a hug! <3

Time: 2024-05-01 20:14:33
Description: Initializing game

org.spongepowered.asm.mixin.throwables.MixinApplyError: Mixin apply for mod sodium failed
sodium.mixins.json:core.MixinChunkBuilder from mod sodium -> org.spongepowered.asm.mixin.injection.throwables.InvalidInjectionException
\tat org.spongepowered.asm.mixin.transformer.MixinTransformer.transformClassBytes(MixinTransformer.java:237)
\tat net.fabricmc.loader.impl.launch.knot.KnotClassLoader.loadClass(KnotClassLoader.java:215)
Caused by: org.spongepowered.asm.mixin.injection.throwables.InvalidInjectionException: Critical injection failure
\tat org.spongepowered.asm.mixin.injection.struct.InjectionInfo.postInject(InjectionInfo.java:618)
\t... 23 more


A detailed walkthrough of the error, its code path and all known details is as follows:
---------------------------------------------------------------------------------------
-- System Details --
\tMinecraft Version: 1.20.1
\tFabric Loader: 0.15.11
"""

OOM_LATEST_LOG = """[20:10:01] [main/INFO]: Loading Minecraft 1.20.1 with Fabric Loader 0.15.11
[20:10:02] [main/INFO]: Loading 214 mods:
[20:11:40] [Render thread/INFO]: OpenAL initialized on device Speakers
[20:41:02] [Render thread/ERROR]: java.lang.OutOfMemoryError: Java heap space
[20:41:02] [Render thread/ERROR]: Unable to allocate 4194304 bytes for chunk buffer
[20:41:02] [Render thread/ERROR]: GC overhead limit exceeded while filling world gen cache
[20:41:03] [Render thread/ERROR]: Shutting down
"""

KITCHEN_SINK_LOG = """[20:14:31] [main/INFO]: Forge mod loading, version 47.2.0
[20:14:33] [main/ERROR]: java.lang.OutOfMemoryError: Java heap space
[20:14:33] [main/ERROR]: Unable to allocate 4096 bytes for blockstate cache
[20:14:34] [main/ERROR]: java.lang.ClassNotFoundException: com.example.examplelib.ExampleLib
[20:14:34] [main/ERROR]: Missing mod: examplelib Requires forge 47.2.0 or above
[20:14:35] [main/ERROR]: net.minecraftforge.fml.LoadingFailedException: Mod Loading has failed
[20:14:36] [main/WARN]: org.spongepowered.asm.mixin: Mixin apply for mod sodium failed sodium.mixins.json
[20:14:37] [Render thread/ERROR]: OpenGL debug: GL error 1282, Shader compile failed on GPU
"""


# ─── 夹具 ───────────────────────────────────────────────────


class _SlogRecorder:
    """替换 ``structured_logger.slog``，把结构化日志调用记下来。"""

    def __init__(self):
        self.records = []

    def info(self, event, **kwargs):
        self.records.append(("info", event, kwargs))

    def error(self, event, **kwargs):
        self.records.append(("error", event, kwargs))

    def debug(self, event, **kwargs):
        self.records.append(("debug", event, kwargs))

    def warning(self, event, **kwargs):
        self.records.append(("warning", event, kwargs))

    def events(self):
        return [x[1] for x in self.records]

    def find(self, event):
        return [x for x in self.records if x[1] == event]


@pytest.fixture
def slog(monkeypatch):
    """不落盘的结构化日志记录器（服务层是在函数内 ``from structured_logger import slog``）。"""
    recorder = _SlogRecorder()
    monkeypatch.setattr(structured_logger, "slog", recorder)
    return recorder


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    """把全局 config 的基础目录与日志路径指到 tmp_path。

    两个原因：``log_file`` 会让"读磁盘启动器日志"这条分支读到真实文件；
    ``base_dir`` 决定 ``latest_structured.log`` 的位置，存在时会多出一段
    ``[结构化日志（最后100行）]``，让上下文内容随机器而变。
    """
    from config import config as root_config

    monkeypatch.setattr(root_config, "base_dir", tmp_path)
    monkeypatch.setattr(root_config, "log_file", tmp_path / "latest.log")
    return root_config


@pytest.fixture(autouse=True)
def no_real_network(monkeypatch):
    """任何用例都不允许真的发请求（要测默认实现时用例自己覆盖 urlopen）。"""

    def _boom(*args, **kwargs):
        raise AssertionError("测试用例不得发起真实网络请求")

    monkeypatch.setattr("urllib.request.urlopen", _boom)


def make_game_dir(tmp_path: Path, name: str = ".minecraft") -> Path:
    """造一个带奔溃报告的 Minecraft 目录结构。"""
    game = tmp_path / name
    (game / "crash-reports").mkdir(parents=True, exist_ok=True)
    (game / "logs").mkdir(parents=True, exist_ok=True)
    return game


def write(path: Path, text: str, encoding: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding=encoding)
    return path


class _FakeTransport:
    """假传输：记录调用参数，返回预置结果或抛出预置异常。"""

    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def __call__(self, url, data, headers, timeout):
        self.calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        if self.error is not None:
            raise self.error
        return self.result

    @property
    def body(self):
        return json.loads(self.calls[-1]["data"].decode("utf-8"))


def http_error(code: int, body: bytes = b"", fp=None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://example.invalid", code, "err", {}, fp or io.BytesIO(body))


# ─── read_file_tail ─────────────────────────────────────────


class TestReadFileTail:
    def test_returns_last_n_lines_with_trailing_newline(self, tmp_path):
        path = write(tmp_path / "logs" / "latest.log", "line-1\nline-2\nline-3\nline-4\nline-5\n")
        assert read_file_tail(str(path), 2) == "line-4\nline-5\n"

    def test_fewer_lines_than_requested(self, tmp_path):
        path = write(tmp_path / "short.log", "only\n")
        assert read_file_tail(str(path), 200) == "only\n"

    def test_missing_paths_return_empty_string(self, tmp_path):
        assert read_file_tail("") == ""
        assert read_file_tail(None) == ""
        assert read_file_tail(str(tmp_path / "nope" / "crash.txt")) == ""
        # 目录存在但不是文件：open() 抛 IsADirectoryError 后被兜住
        assert read_file_tail(str(tmp_path)) == ""

    def test_empty_file_returns_empty_string(self, tmp_path):
        path = write(tmp_path / "empty.log", "")
        assert read_file_tail(str(path), 200) == ""

    def test_huge_file_only_reads_tail(self, tmp_path):
        path = tmp_path / "huge.log"
        path.write_text("".join(f"line-{i}\n" for i in range(20000)), encoding="utf-8")
        tail = read_file_tail(str(path), 200)
        lines = tail.splitlines()
        assert len(lines) == 200
        assert lines[0] == "line-19800"
        assert lines[-1] == "line-19999"

    def test_undecodable_bytes_survive_via_latin1(self, tmp_path):
        """阶段 1.23 修正后的行为：非法字节由最后一档 latin-1 一对一带出。

        **这条用例原先钉的是缺陷**：改造前后每一档编码都用 ``errors="ignore"``，
        而 ``errors="ignore"`` 让 ``UnicodeDecodeError`` 永不抛出 —— 于是
        gbk / latin-1 两级回退**不可达**，坏字节被静默丢掉（原断言是
        ``"beforeafter\\n"``）。

        缺陷已修（前两档改严格解码，latin-1 作为永不失败的最后一档），
        所以坏字节现在作为 latin-1 字符保留，而不是消失。
        另见 ``tests/test_crash_log_encoding.py``。
        """
        path = tmp_path / "binary.log"
        path.write_bytes(b"before\xff\xfeafter\n")
        result = read_file_tail(str(path), 10)
        assert "before" in result and "after" in result
        assert result != "beforeafter\n", "坏字节不该再被静默丢弃"

    def test_gbk_file_decodes_via_fallback(self, tmp_path):
        """阶段 1.23 修正后的行为：GBK 文件真的走 gbk 回退，中文能读出来。

        **这条用例原先钉的是缺陷**：原实现下 GBK 中文按 utf-8 读不报错、只丢字节，
        编码回退分支因此不生效（原断言是 ``"第一行" not in result``）。

        这对用户是有影响的：本机日志若是 GBK（中文 Windows 上很常见），
        崩溃报告里的中文会变成乱码后才送给 AI 分析。
        """
        path = tmp_path / "gbk.log"
        path.write_bytes("第一行 OK\n".encode("gbk"))
        result = read_file_tail(str(path), 10)
        assert "第一行" in result, "GBK 中文应能通过回退正确解码"
        assert result == "第一行 OK\n"

    def test_zero_lines_returns_whole_file(self, tmp_path):
        """``lines=0`` → 取全文。

        这层语义现在**显式写明**（``lines <= 0`` 表示不限行数），不再依赖
        ``readlines()[-0:]`` 恰好等于整个列表这个巧合。
        """
        path = write(tmp_path / "n.log", "1\n2\n3\n")
        assert read_file_tail(str(path), 0) == "1\n2\n3\n"

    def test_negative_lines_means_no_limit(self, tmp_path):
        """``lines<0`` → 取全文（阶段 1.23 修正）。

        **这条用例原先钉的是缺陷**：原实现是 ``readlines()[-lines:]``，
        ``lines=-1`` 变成 ``readlines()[1:]``，即"掐掉开头 1 行" —— 显然不是本意。
        现在 ``lines <= 0`` 统一表示"不限行数"。
        """
        path = write(tmp_path / "n.log", "1\n2\n3\n")
        assert read_file_tail(str(path), -1) == "1\n2\n3\n"

    def test_lone_carriage_return_is_line_separator(self, tmp_path):
        """``for line in f`` 默认的 universal newlines：CR 也被当行分隔。"""
        path = tmp_path / "crash.txt"
        path.write_bytes(b"a\rb\rc\r")
        assert read_file_tail(str(path), 2) == "b\nc\n"


# ─── diagnose_crash ─────────────────────────────────────────


class TestDiagnoseCrash:
    def test_mixin_crash_report(self, tmp_path):
        game = make_game_dir(tmp_path)
        report = write(game / "crash-reports" / "crash-2024-05-01_20.14.33-client.txt", MIXIN_CRASH_REPORT)
        result = diagnose_crash({"crash_report": str(report)})
        assert [d["name"] for d in result] == ["Mixin 错误"]
        # required 命中 1 个（org.spongepowered.asm.mixin）+ optional 命中 1 个（.mixins.json）。
        # 注意 optional 里的 "mixin apply for mod" 是全小写，真实日志里是 "Mixin apply for mod"
        # （首字母大写），`in` 区分大小写所以不命中 —— 既有行为，原样保留。
        assert result[0]["score"] == 1 + 1 * 0.5
        assert result[0]["icon"] == "\U0001f9ec"
        assert result[0]["advice"] == "检查崩溃报告中 .mixins.json 前的模组名，更新或移除该模组。"

    def test_oom_game_log(self, tmp_path):
        game = make_game_dir(tmp_path)
        log = write(game / "logs" / "latest.log", OOM_LATEST_LOG)
        result = diagnose_crash({"game_log": str(log)})
        assert result[0]["name"] == "内存溢出"
        # required 1（OutOfMemoryError）+ optional 3（Unable to allocate / heap space /
        # GC overhead limit exceeded）
        assert result[0]["score"] == 1 + 3 * 0.5

    def test_same_type_from_multiple_files_is_merged(self, tmp_path):
        """同一类型出现在多份日志里，只算一次（文本先合并再匹配）。"""
        game = make_game_dir(tmp_path)
        report = write(game / "crash-reports" / "crash.txt", MIXIN_CRASH_REPORT)
        log = write(game / "logs" / "latest.log", MIXIN_CRASH_REPORT)
        result = diagnose_crash({"crash_report": str(report), "game_log": str(log)})
        assert [d["name"] for d in result] == ["Mixin 错误"]

    def test_sorted_by_score_and_capped_at_three(self, tmp_path):
        game = make_game_dir(tmp_path)
        log = write(game / "logs" / "latest.log", KITCHEN_SINK_LOG)
        result = diagnose_crash({"game_log": str(log)})
        # 渲染 2.5 > 模组加载异常 2.0 / 依赖缺失 2.0（同分稳定排序 → 保持 CRASH_TYPES 先后）
        assert [d["name"] for d in result] == ["渲染与图形错误", "模组加载异常", "依赖缺失/版本错误"]
        assert [d["score"] for d in result] == [2.5, 2.0, 2.0]
        # 同一份日志里 "Mixin 错误" 也命中（1.5 分），但被 top-3 截掉了
        assert "org.spongepowered.asm.mixin" in KITCHEN_SINK_LOG
        assert "Mixin 错误" not in [d["name"] for d in result]
        assert len(result) == 3, "最多只返回 3 条"

    def test_empty_or_missing_inputs_return_empty_list(self, tmp_path):
        assert diagnose_crash({}) == []
        assert diagnose_crash({"crash_report": None, "game_log": ""}) == []
        # 目录根本不存在的典型情形：还没有 crash-reports 目录
        assert diagnose_crash({"crash_report": str(tmp_path / "crash-reports" / "x.txt")}) == []
        assert diagnose_crash({"game_log": str(tmp_path / "logs" / "latest.log")}) == []

    def test_empty_file_is_not_enough(self, tmp_path):
        empty = write(tmp_path / "crash-reports" / "empty.txt", "")
        whitespace = write(tmp_path / "logs" / "blank.log", "\n \n\t\n")
        assert diagnose_crash({"crash_report": str(empty)}) == []
        assert diagnose_crash({"game_log": str(whitespace)}) == []

    def test_irrelevant_log_matches_nothing(self, tmp_path):
        log = write(tmp_path / "logs" / "latest.log", "[12:00:00] [main/INFO]: Hello world\n")
        assert diagnose_crash({"game_log": str(log)}) == []

    def test_debug_and_jvm_logs_are_scanned_too(self, tmp_path):
        debug = write(tmp_path / "logs" / "debug.log", "java.lang.OutOfMemoryError: Metaspace\n")
        jvm = write(tmp_path / "hs_err_pid1234.log", "SIGSEGV (0xb) at pc=0x00007f\nProblematic frame:\n")
        names = [d["name"] for d in diagnose_crash({"debug_log": str(debug), "jvm_crash_log": str(jvm)})]
        assert names == ["内存溢出", "Java 虚拟机崩溃"]

    def test_crash_types_table_is_intact(self):
        assert len(CRASH_TYPES) == 11
        for entry in CRASH_TYPES:
            assert set(entry) == {"name", "icon", "required", "optional", "cause", "advice"}
            assert entry["required"] and entry["optional"]


# ─── collect_ai_context ─────────────────────────────────────


def full_crash_files(game: Path, log_lines: int = 300, report_lines: int = 500) -> dict:
    """造一套完整崩溃文件（崩溃报告 / 游戏日志 / debug / jvm）。"""
    return {
        "crash_report": str(
            write(
                game / "crash-reports" / "crash-2024-05-01_20.14.33-client.txt",
                "".join(f"crash-report-line-{i:04d}\n" for i in range(report_lines)),
            )
        ),
        "game_log": str(
            write(game / "logs" / "latest.log", "".join(f"game-log-line-{i:04d}\n" for i in range(log_lines)))
        ),
        "debug_log": str(write(game / "logs" / "debug.log", "debug-log-line\n")),
        "jvm_crash_log": str(write(game / "hs_err_pid1234.log", "jvm-crash-line\n")),
    }


SECTION_ORDER = [
    "[崩溃报告]",
    "[游戏日志（最后200行）]",
    "[Debug 日志（最后200行）]",
    "[JVM 崩溃日志（最后200行）]",
    "[启动器日志（最后200行）]",
]


class TestCollectAiContext:
    def test_system_info_header_exact(self, tmp_path, slog):
        context = collect_ai_context({}, 7)
        assert context == (
            f"[系统信息]\nOS: {platform.system()} {platform.release()}\n"
            f"Python: {platform.python_version()}\n"
            f"Architecture: {platform.machine()}\n"
            f"退出码: 7"
        )
        assert "\n\n" not in context, "单段上下文不应有分段分隔符"

    def test_sections_appear_in_fixed_order(self, tmp_path, slog):
        game = make_game_dir(tmp_path)
        files = full_crash_files(game)
        context = collect_ai_context(files, 1, log_buffer=io.StringIO("launcher-line\n"))
        positions = [context.index(header) for header in SECTION_ORDER]
        assert positions == sorted(positions), f"段落顺序变了: {SECTION_ORDER}"
        for header in SECTION_ORDER:
            assert context.count(header) == 1, f"{header} 出现了不止一次"

    def test_crash_report_read_in_full_and_others_truncated(self, tmp_path, slog):
        game = make_game_dir(tmp_path)
        context = collect_ai_context(full_crash_files(game), 1, log_buffer=io.StringIO("launcher\n"))
        assert "crash-report-line-0000" in context, "崩溃报告应完整读取（99999 行上限）"
        assert "crash-report-line-0499" in context
        assert "game-log-line-0000" not in context, "游戏日志只取最后 200 行"
        assert "game-log-line-0099" not in context
        assert "game-log-line-0100" in context
        assert "game-log-line-0299" in context

    def test_empty_or_missing_files_are_skipped(self, tmp_path, slog):
        game = make_game_dir(tmp_path)
        missing = {
            "crash_report": str(game / "crash-reports" / "nope.txt"),
            "game_log": str(game / "logs" / "latest.log"),
        }
        context = collect_ai_context(missing, 2, log_buffer=io.StringIO("launcher\n"))
        for header in SECTION_ORDER[:-1]:
            assert header not in context, f"{header} 不该出现"
        assert "[启动器日志（最后200行）]\nlauncher" in context

    def test_log_buffer_wins_over_disk_log(self, tmp_path, slog, isolated_config):
        write(Path(isolated_config.log_file), "disk-line\n")
        context = collect_ai_context({}, 1, log_buffer=io.StringIO("buffer-line\n"))
        assert "[启动器日志（最后200行）]\nbuffer-line" in context
        assert "disk-line" not in context

    def test_empty_buffer_falls_back_to_disk_log(self, tmp_path, slog, isolated_config):
        """缓冲区存在但内容为空时走磁盘分支（与原 ``hasattr(...) and self._log_buffer`` 后的
        ``if not launcher_log.strip()`` 判定一致）。"""
        write(Path(isolated_config.log_file), "disk-line\n")
        context = collect_ai_context({}, 1, log_buffer=io.StringIO(""))
        assert "disk-line" in context

    def test_disk_log_keeps_only_last_200_lines(self, tmp_path, slog, isolated_config):
        write(Path(isolated_config.log_file), "".join(f"disk-{i:04d}\n" for i in range(300)))
        context = collect_ai_context({}, 1)
        assert "disk-0000" not in context
        assert "disk-0100" in context
        assert "disk-0299" in context

    def test_no_launcher_log_section_when_nothing_available(self, tmp_path, slog):
        assert "[启动器日志" not in collect_ai_context({}, 1)
        assert "[启动器日志" not in collect_ai_context({}, 1, log_buffer=None)

    def test_structured_log_section(self, tmp_path, slog):
        write(tmp_path / "latest_structured.log", "".join(f'{{"event":"e{i}"}}\n' for i in range(150)))
        context = collect_ai_context({}, 1)
        assert "[结构化日志（最后100行）]" in context
        assert '"event":"e49"' not in context  # 第 50 行（0 基）在第 100 行之前
        assert '"event":"e50"' in context
        assert '"event":"e149"' in context

    def test_structured_log_absent_no_section(self, tmp_path, slog):
        assert "[结构化日志" not in collect_ai_context({}, 1)

    def test_slog_record_fields(self, tmp_path, slog):
        game = make_game_dir(tmp_path)
        files = full_crash_files(game, log_lines=3, report_lines=3)
        context = collect_ai_context(files, 9, log_buffer=io.StringIO("launcher\n"))
        records = slog.find("ai_context_collected")
        assert len(records) == 1
        assert records[0][0] == "info"
        assert records[0][2] == {
            "exit_code": 9,
            "has_crash_report": True,
            "has_game_log": True,
            "has_debug_log": True,
            "has_jvm_log": True,
            "has_launcher_log": True,
            "context_length": len(context),
        }

    def test_slog_has_launcher_log_false_when_missing(self, tmp_path, slog):
        collect_ai_context({}, 9)
        assert slog.find("ai_context_collected")[0][2]["has_launcher_log"] is False


# ─── collect_server_ai_context ──────────────────────────────


class TestCollectServerAiContext:
    def test_sections_and_order(self, tmp_path, slog):
        context = collect_server_ai_context(
            3,
            version_id="1.20.1-forge-47.2.0",
            server_log_lines=[
                "[20:00:00] [Server thread/INFO]: Done (12.3s)!",
                "[20:00:01] [Server thread/ERROR]: boom",
            ],
            log_buffer=io.StringIO("launcher-line\n"),
        )
        assert context.startswith(
            f"[系统信息]\nOS: {platform.system()} {platform.release()}\n"
            f"Python: {platform.python_version()}\n"
            f"Architecture: {platform.machine()}\n"
            f"退出码: 3\n"
            f"场景: 服务器崩溃分析"
        )
        order = ["[服务器版本]", "[服务器日志（最后200行）]", "[启动器日志（最后200行）]"]
        positions = [context.index(h) for h in order]
        assert positions == sorted(positions)
        assert "[服务器版本]\n1.20.1-forge-47.2.0" in context
        assert "[服务器日志（最后200行）]\n[20:00:00] [Server thread/INFO]: Done (12.3s)!" in context
        assert "[启动器日志（最后200行）]\nlauncher-line" in context
        assert "耗时" not in context

    def test_optional_sections_omitted(self, tmp_path, slog):
        context = collect_server_ai_context(1)
        assert context.endswith("场景: 服务器崩溃分析"), "没有任何可选信息时只剩系统信息"
        assert "[服务器版本]" not in context
        assert "[服务器日志" not in context
        assert "[启动器日志" not in context

    def test_server_log_lines_truncated_to_200(self, tmp_path, slog):
        lines = [f"server-line-{i:04d}" for i in range(300)]
        context = collect_server_ai_context(1, server_log_lines=lines)
        assert "server-line-0000" not in context
        assert "server-line-0100" in context
        assert "server-line-0299" in context

    def test_crash_only_sections_absent(self, tmp_path, slog):
        context = collect_server_ai_context(1, log_buffer=io.StringIO("launcher\n"))
        for header in ("[崩溃报告]", "[游戏日志（最后200行）]", "[Debug 日志（最后200行）]", "[JVM 崩溃日志"):
            assert header not in context

    def test_empty_version_id_omits_section(self, tmp_path, slog):
        assert "[服务器版本]" not in collect_server_ai_context(1, version_id="")

    def test_slog_record_fields(self, tmp_path, slog):
        context = collect_server_ai_context(5, version_id="v", server_log_lines=["a"], log_buffer=io.StringIO("l\n"))
        records = slog.find("server_ai_context_collected")
        assert len(records) == 1
        assert records[0][2] == {
            "exit_code": 5,
            "has_server_log": True,
            "has_launcher_log": True,
            "context_length": len(context),
        }

    def test_slog_has_server_log_false_when_absent(self, tmp_path, slog):
        collect_server_ai_context(5)
        assert slog.find("server_ai_context_collected")[0][2]["has_server_log"] is False


# ─── analyze_crash / analyze_server_crash（全部注入假 transport）───


class TestAnalyzeCrash:
    def test_success_request_shape(self, slog):
        transport = _FakeTransport(result={"choices": [{"message": {"content": "## 崩溃原因分析\n内存不足"}}]})
        content = analyze_crash("CTX-BODY", 1, "TOKEN-123", transport=transport)
        assert content == "## 崩溃原因分析\n内存不足"

        call = transport.calls[-1]
        assert call["url"] == AI_ENDPOINT == "https://jingdu.qzz.io/api/deepseek/v1/chat/completions"
        assert call["timeout"] == 120
        assert call["headers"] == {
            "Content-Type": "application/json",
            "Authorization": "Bearer TOKEN-123",
            "User-Agent": "FMCL/1.0 (Minecraft Launcher; crash-analyzer)",
        }

        body = transport.body
        assert body["model"] == "deepseek-chat"
        assert body["stream"] is False
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"
        assert "Minecraft 崩溃日志分析专家" in body["messages"][0]["content"]
        assert "## 崩溃原因分析" in body["messages"][0]["content"]
        assert body["messages"][1]["content"] == "请分析以下 Minecraft 崩溃信息：\n\nCTX-BODY"
        # 请求体就是 dumps 出来的那一份（没有额外字段、没有换行）
        assert call["data"] == json.dumps(
            {"model": "deepseek-chat", "messages": body["messages"], "stream": False}
        ).encode("utf-8")

        assert slog.find("ai_crash_analysis") == [
            ("info", "ai_crash_analysis", {"exit_code": 1, "result_length": len(content)})
        ]

    def test_empty_ai_content_uses_placeholder(self, slog):
        for result in ({"choices": [{"message": {"content": ""}}]}, {}, {"choices": [{}]}):
            content = analyze_crash("CTX", 1, "TOK", transport=_FakeTransport(result=result))
            assert content == "AI 未返回有效分析结果。"

    def test_http_error_message_matches_old_display_text(self, slog):
        transport = _FakeTransport(error=http_error(401, '{"error":"bad token"}'.encode("utf-8")))
        with pytest.raises(NetworkError) as info:
            analyze_crash("CTX", 42, "TOK", transport=transport)
        err = info.value
        assert err.message == 'HTTP 401: {"error":"bad token"}'
        assert err.code == "network_error"
        assert isinstance(err.cause, urllib.error.HTTPError)
        assert slog.find("ai_crash_analysis_failed")[0][2] == {
            "exit_code": 42,
            "error": 'HTTP 401: {"error":"bad token"}',
        }

    def test_http_error_body_truncated_to_200_chars(self, slog):
        transport = _FakeTransport(error=http_error(500, ("x" * 500).encode("utf-8")))
        with pytest.raises(NetworkError) as info:
            analyze_crash("CTX", 1, "TOK", transport=transport)
        assert info.value.message == "HTTP 500: " + "x" * 200

    def test_http_error_with_unreadable_body(self, slog):
        class _BadFP:
            def read(self, *args):
                raise OSError("stream closed")

        transport = _FakeTransport(error=urllib.error.HTTPError("http://x", 500, "m", {}, _BadFP()))
        with pytest.raises(NetworkError) as info:
            analyze_crash("CTX", 1, "TOK", transport=transport)
        assert info.value.message == "HTTP 500: "

    def test_generic_exception_message_is_str_of_error(self, slog):
        transport = _FakeTransport(error=urllib.error.URLError("timed out"))
        with pytest.raises(NetworkError) as info:
            analyze_crash("CTX", 7, "TOK", transport=transport)
        assert info.value.message == "<urlopen error timed out>"
        assert slog.find("ai_crash_analysis_failed")[0][2]["error"] == "<urlopen error timed out>"

    def test_malformed_response_is_reported_as_error(self, slog):
        """choices 结构不对时旧实现是 ``IndexError`` 兜底 → 显示 str(e)，这里保持同样文本。"""
        with pytest.raises(NetworkError) as info:
            analyze_crash("CTX", 1, "TOK", transport=_FakeTransport(result={"choices": []}))
        assert info.value.message == "list index out of range"

    def test_default_transport_builds_the_same_http_request(self, monkeypatch, slog):
        """默认传输（真实 ``_http_post_json``）发出的 Request 与改造前逐项一致。"""
        captured = {}

        class _Response:
            def __init__(self, payload):
                self._payload = payload

            def read(self):
                return self._payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(req, timeout=None):
            captured["req"] = req
            captured["timeout"] = timeout
            return _Response(json.dumps({"choices": [{"message": {"content": "OK"}}]}).encode("utf-8"))

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        assert analyze_crash("CTX", 1, "TOK") == "OK"

        req = captured["req"]
        assert req.full_url == AI_ENDPOINT
        assert req.get_method() == "POST"
        assert captured["timeout"] == 120
        headers = {k.lower(): v for k, v in req.headers.items()}
        assert headers["content-type"] == "application/json"
        assert headers["authorization"] == "Bearer TOK"
        assert headers["user-agent"] == "FMCL/1.0 (Minecraft Launcher; crash-analyzer)"
        assert json.loads(req.data.decode("utf-8"))["model"] == "deepseek-chat"

    def test_http_error_from_default_transport(self, monkeypatch, slog):
        def fake_urlopen(req, timeout=None):
            raise http_error(403, b"forbidden")

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        with pytest.raises(NetworkError) as info:
            analyze_crash("CTX", 1, "TOK")
        assert info.value.message == "HTTP 403: forbidden"


class TestAnalyzeServerCrash:
    def test_success_request_shape(self, slog):
        transport = _FakeTransport(result={"choices": [{"message": {"content": "服务器没问题"}}]})
        content = analyze_server_crash("SRV-CTX", 2, "TOK", transport=transport)
        assert content == "服务器没问题"
        call = transport.calls[-1]
        assert call["url"] == AI_ENDPOINT
        assert call["timeout"] == 120
        assert call["headers"]["User-Agent"] == "FMCL/1.0 (Minecraft Launcher; server-crash-analyzer)"

        body = transport.body
        assert "Minecraft 服务器崩溃日志分析专家" in body["messages"][0]["content"]
        assert body["messages"][1]["content"] == "请分析以下 Minecraft 服务器异常退出信息：\n\nSRV-CTX"
        assert slog.find("ai_server_crash_analysis")[0][2] == {"exit_code": 2, "result_length": len(content)}

    def test_empty_ai_content_uses_placeholder(self, slog):
        assert analyze_server_crash("CTX", 1, "TOK", transport=_FakeTransport(result={})) == "AI 未返回有效分析结果。"

    def test_http_error_event_name_differs(self, slog):
        with pytest.raises(NetworkError) as info:
            analyze_server_crash("CTX", 4, "TOK", transport=_FakeTransport(error=http_error(429, b"slow down")))
        assert info.value.message == "HTTP 429: slow down"
        assert slog.find("ai_server_crash_analysis_failed")[0][2] == {"exit_code": 4, "error": "HTTP 429: slow down"}
        assert slog.find("ai_crash_analysis_failed") == [], "不得用客户端的日志事件名"

    def test_generic_error_message(self, slog):
        with pytest.raises(NetworkError) as info:
            analyze_server_crash("CTX", 1, "TOK", transport=_FakeTransport(error=RuntimeError("连接被重置")))
        assert info.value.message == "连接被重置"


# ─── CrashService 对象本身 ──────────────────────────────────


class TestCrashService:
    def test_identity_and_lifecycle(self):
        service = CrashService()
        assert service.name == "crash"
        assert service.label == "崩溃诊断"
        assert service.requires == ()
        assert service.attached is False, "阶段 1 的 Tk 界面没有 AppContext，服务必须能独立实例化"
        assert service.describe()["class"] == "CrashService"

    def test_methods_delegate_to_module_functions(self, tmp_path, slog):
        game = make_game_dir(tmp_path)
        files = full_crash_files(game, log_lines=5, report_lines=5)
        service = CrashService()
        assert service.diagnose_crash({"crash_report": files["crash_report"]}) == diagnose_crash(
            {"crash_report": files["crash_report"]}
        )
        assert service.read_file_tail(files["game_log"], 2) == read_file_tail(files["game_log"], 2)
        assert service.collect_ai_context(files, 3, io.StringIO("l\n")) == collect_ai_context(
            files, 3, io.StringIO("l\n")
        )
        assert service.collect_server_ai_context(3, "v", ["a"], io.StringIO("l\n")) == collect_server_ai_context(
            3, "v", ["a"], io.StringIO("l\n")
        )

    def test_constructor_transport_is_used_by_default(self, slog):
        transport = _FakeTransport(result={"choices": [{"message": {"content": "ok"}}]})
        service = CrashService(transport=transport)
        assert service.analyze_crash("CTX", 1, "TOK") == "ok"
        assert service.analyze_server_crash("CTX", 1, "TOK") == "ok"
        assert len(transport.calls) == 2

    def test_per_call_transport_overrides_constructor(self, slog):
        ctor = _FakeTransport(result={"choices": [{"message": {"content": "ctor"}}]})
        override = _FakeTransport(result={"choices": [{"message": {"content": "override"}}]})
        service = CrashService(transport=ctor)
        assert service.analyze_crash("CTX", 1, "TOK", transport=override) == "override"
        assert ctor.calls == [] and len(override.calls) == 1

    def test_context_can_be_attached_like_any_service(self):
        from app.context import AppContext

        ctx = AppContext()
        service = ctx.register(CrashService())
        assert service.context is ctx
        assert ctx.try_get("crash") is service
        assert service.attached is True


# ─── 界面 Mixin 的委托层 ────────────────────────────────────


class _FakeApp:
    """够用的假应用对象：有崩溃 Mixin、有日志缓冲区，没有 AppContext。"""

    def __init__(self, log_buffer=None):
        if log_buffer is not None:
            self._log_buffer = log_buffer


def _fake_app(**attrs):
    from ui.app_crash import CrashHandlerMixin

    class _App(CrashHandlerMixin, _FakeApp):
        pass

    app = _App(log_buffer=attrs.pop("log_buffer", None))
    for key, value in attrs.items():
        setattr(app, key, value)
    return app


class TestUiDelegates:
    def test_crash_types_is_the_same_list_object(self):
        from ui.app_crash import CrashHandlerMixin

        assert CrashHandlerMixin.CRASH_TYPES is CRASH_TYPES

    def test_method_names_and_signatures_unchanged(self):
        """搬家后 CrashHandlerMixin 上的方法名与参数表必须与改造前一致。"""
        import inspect

        from ui.app_crash import CrashHandlerMixin

        expected = {
            "CRASH_TYPES": None,
            "_diagnose_crash": ["self", "crash_files"],
            "_show_crash_dialog": ["self", "exit_code", "crash_files"],
            "_read_file_tail": ["self", "filepath", "lines"],
            "_collect_ai_context": ["self", "crash_files", "exit_code"],
            "_ai_analyze_server_crash": ["self", "exit_code"],
            "_collect_server_ai_context": ["self", "exit_code"],
            "_do_server_ai_analyze": ["self", "context", "exit_code", "token"],
            "_show_privacy_consent_dialog": ["self", "on_accept"],
            "_ai_analyze_crash": ["self", "crash_files", "exit_code"],
            "_do_ai_analyze": ["self", "crash_files", "exit_code", "token"],
            "_show_ai_result_dialog": ["self", "content", "title"],
        }
        for name, params in expected.items():
            assert hasattr(CrashHandlerMixin, name), f"{name} 丢了"
            if params is None:
                continue
            sig = inspect.signature(getattr(CrashHandlerMixin, name))
            assert list(sig.parameters) == params, f"{name} 的参数表变了"

        sig = inspect.signature(CrashHandlerMixin._read_file_tail)
        assert sig.parameters["lines"].default == 200
        sig = inspect.signature(CrashHandlerMixin._do_ai_analyze)
        assert sig.parameters["token"].default is None
        sig = inspect.signature(CrashHandlerMixin._show_ai_result_dialog)
        assert sig.parameters["title"].default == "AI 崩溃分析结果"

    def test_delegates_work_without_app_context(self, tmp_path, slog):
        game = make_game_dir(tmp_path)
        files = full_crash_files(game, log_lines=5, report_lines=5)
        app = _fake_app(log_buffer=io.StringIO("buffer-line\n"))

        assert app._diagnose_crash({"crash_report": files["crash_report"]}) == diagnose_crash(
            {"crash_report": files["crash_report"]}
        )
        assert app._read_file_tail(files["game_log"], 2) == read_file_tail(files["game_log"], 2)
        assert app._collect_ai_context(files, 3) == collect_ai_context(files, 3, log_buffer=app._log_buffer)
        assert app._collect_server_ai_context(3) == collect_server_ai_context(3, "", [], app._log_buffer)

    def test_delegate_passes_own_log_buffer(self, tmp_path, slog):
        app = _fake_app(log_buffer=io.StringIO("from-the-app-buffer\n"))
        assert "[启动器日志（最后200行）]\nfrom-the-app-buffer" in app._collect_ai_context({}, 1)

    def test_delegate_tolerates_missing_log_buffer_and_server_attrs(self, tmp_path, slog):
        app = _fake_app()  # 没有 _log_buffer / selected_server_version / _server_log_lines
        assert app._collect_ai_context({}, 1) == collect_ai_context({}, 1)
        assert app._collect_server_ai_context(1) == collect_server_ai_context(1)

    def test_delegate_reads_server_attributes(self, tmp_path, slog):
        app = _fake_app(
            log_buffer=io.StringIO("l\n"),
            selected_server_version="1.20.1-forge-47.2.0",
            _server_log_lines=["server-line"],
        )
        context = app._collect_server_ai_context(4)
        assert "[服务器版本]\n1.20.1-forge-47.2.0" in context
        assert "[服务器日志（最后200行）]\nserver-line" in context

    def test_service_instance_is_cached_and_reused(self):
        app = _fake_app()
        service = app._crash_service()
        assert isinstance(service, CrashService)
        assert app._crash_service() is service, "同一次运行内应当复用同一个实例"
        assert app._crash_service_fallback is service

    def test_service_instance_prefers_app_context_registration(self):
        from app.context import AppContext

        app = _fake_app()
        registered = CrashService()
        AppContext().register(registered)  # 未 attach 到 app 的 context，不应被采用
        assert app._crash_service() is not registered

        ctx = AppContext()
        ctx.register(registered, replace=True)
        app.context = ctx
        assert app._crash_service() is registered, "有 AppContext 时必须优先用注册表里的实例"

    def test_service_instance_survives_broken_context(self):
        class _BrokenContext:
            def try_get(self, name):
                raise RuntimeError("注册表炸了")

        app = _fake_app()
        app.context = _BrokenContext()
        assert isinstance(app._crash_service(), CrashService)
