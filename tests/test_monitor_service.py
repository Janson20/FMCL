"""任务 1.13「监控服务」抽取的永久回归守卫。

`ui/app_monitor.py` 里那些**无 UI 依赖**的部分（多厂商 GPU 检测/采样、
CPU/内存/交换区采集、全局热键注册）已搬进 `services/monitor_service.py`。

搬运方式：脚本按锚点抽取 + **逐字节子串断言**（证明搬走的没被改写），
外加一次**从原文出发的完整性核对**（证明没有静默丢行）——
后者不是多余的：第一版抽取脚本确实丢掉了两个常量，直到运行时
`NameError: name '_GPU_SAMPLE_INTERVAL' is not defined` 才暴露。

这里守住的四件事：

1. 服务零 UI 依赖，且能脱离 Tk 独立采集出真实指标（阶段 1 的核心诉求）；
2. `collect()` 的返回键与改造前完全一致（界面渲染代码依赖这些键名）；
3. 依赖标志是「读取时代理」——热键注册失败会翻转 `keyboard_available`，
   调用方必须能看到这个翻转；
4. 界面侧不再重复实现这些逻辑，且原文里被搬走的代码没有在新的地方留副本。
"""

from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: 改造前 `PerformanceMonitorWindow._collect_metrics` 产出的键（逐个核对过）
EXPECTED_METRIC_KEYS = {
    "cpu_percent",
    "cpu_freq",
    "mem_percent",
    "mem_used",
    "mem_total",
    "swap_used",
    "swap_total",
    "gpu_name",
    "gpu_util",
    "gpu_mem",
    "gpu_temp",
}


@pytest.fixture(scope="module")
def svc():
    return importlib.import_module("services.monitor_service")


class TestServiceIsStandalone:
    def test_import_does_not_load_gui_stack(self):
        """服务导入不得拖入任何 GUI 栈。

        在子进程里查，因为同进程内前面的测试可能已经导入过 customtkinter，
        那样查出来的结果没有意义。
        """
        import subprocess

        code = (
            "import sys, services.monitor_service as m; "
            "gui=[x for x in ('customtkinter','tkinter','PySide6') if x in sys.modules]; "
            "print('GUI=' + ','.join(gui)); print('collector=' + str(hasattr(m,'MetricsCollector')))"
        )
        out = subprocess.run(
            # ⚠️ 必须显式指定 `encoding` + `errors`（第 12 轮修正）：
            # 只写 `text=True` 时 Python 用 `locale.getpreferredencoding(False)` ——
            # 在中文 Windows 上是 **GBK/cp936**，而子进程按 UTF-8 输出，
            # 于是 subprocess 的 reader 线程抛 `UnicodeDecodeError`，pytest 报
            # `PytestUnhandledThreadExceptionWarning`（实测：字节 0xd0 起头）。
            # 断言恰好只看 ASCII 行，所以测试"通过"了、警告却一直挂着 ——
            # 这属于"绿着但没说真话"，一样要修。
            [sys.executable, "-X", "utf8", "-c", code],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(REPO_ROOT), timeout=120,
        )
        assert out.returncode == 0, out.stderr
        assert "GUI=" in out.stdout
        assert "GUI=\n" in out.stdout + "\n" or "GUI=" in out.stdout.splitlines()[0], out.stdout
        assert out.stdout.splitlines()[0].strip() == "GUI=", f"导入服务时加载了 GUI 栈: {out.stdout}"
        assert out.stdout.splitlines()[1].strip() == "collector=True"

    def test_source_has_no_ui_imports(self):
        purity_spec = importlib.util.spec_from_file_location(
            "_fmcl_purity_monitor_test", REPO_ROOT / "scripts" / "check_services_purity.py"
        )
        purity = importlib.util.module_from_spec(purity_spec)
        sys.modules[purity_spec.name] = purity
        purity_spec.loader.exec_module(purity)

        rule = next(r for r in purity.RULES if r.group == "services")
        report = purity.check_file(REPO_ROOT / "services" / "monitor_service.py", rule)
        assert report.ok, "\n".join(v.render() for v in report.violations)


class TestMetricsCollection:
    def test_collect_returns_the_same_keys_as_before(self, svc):
        """返回键必须与改造前一致 —— 界面渲染代码用这些键取值。"""
        data = svc.MetricsCollector().collect()
        assert isinstance(data, dict)
        assert set(data) == EXPECTED_METRIC_KEYS, (
            f"缺失 {EXPECTED_METRIC_KEYS - set(data)}，多出 {set(data) - EXPECTED_METRIC_KEYS}"
        )

    @pytest.mark.skipif(not importlib.util.find_spec("psutil"), reason="psutil 不可用")
    def test_collect_produces_plausible_values(self, svc):
        data = svc.MetricsCollector().collect()
        assert isinstance(data["cpu_percent"], (int, float))
        assert 0 <= data["mem_percent"] <= 100
        assert re.fullmatch(r"[\d.]+ [KMGT]?B", str(data["mem_total"])), data["mem_total"]

    def test_gpu_sampling_is_throttled(self, svc):
        """GPU 查询较慢，改造前就有 2 秒节流；搬运后必须保留。"""
        collector = svc.MetricsCollector()
        calls = {"n": 0}
        real = collector.sample_gpu

        def counting():
            calls["n"] += 1
            return real()

        collector.sample_gpu = counting
        collector.collect()
        first = calls["n"]
        collector.collect()
        assert calls["n"] == first, "第二次采集没有命中节流，GPU 会被反复查询"

    def test_gpu_lifecycle_methods_are_idempotent(self, svc):
        collector = svc.MetricsCollector()
        collector.shutdown_gpu()  # 未初始化时调用不得抛异常
        assert collector.sample_gpu() == {}, "没有检测器时应返回空 dict 而不是抛异常"


class TestDependencyFlags:
    def test_flags_are_read_through_not_copied(self, svc):
        """`monitor_service.keyboard_available` 必须是**读取时**代理。

        热键注册失败会把底层标志置 False；如果公开名是导入时的副本，
        调用方就永远看到旧值。
        """
        original = svc._keyboard_available
        try:
            svc._keyboard_available = not original
            assert svc.keyboard_available is (not original), (
                "公开标志没有反映底层翻转 —— 说明它是导入时的副本"
            )
        finally:
            svc._keyboard_available = original
        assert svc.keyboard_available is original

    def test_all_four_flags_exist(self, svc):
        for name in ("psutil_available", "pynvml_available", "keyboard_available", "gpu_detector_available"):
            assert isinstance(getattr(svc, name), bool), name

    def test_unknown_attribute_still_raises(self, svc):
        with pytest.raises(AttributeError):
            _ = svc.definitely_not_a_flag


class TestUiSideNoLongerDuplicatesLogic:
    def test_ui_file_no_longer_defines_the_moved_logic(self):
        src = (REPO_ROOT / "ui" / "app_monitor.py").read_text(encoding="utf-8")
        assert "class _GPUDetector" not in src, "GPU 检测器还在界面文件里，搬家没生效"
        assert "def _format_bytes" not in src, "格式化辅助还在界面文件里"
        assert "def _format_net_speed" not in src

    def test_ui_file_keeps_the_interface_parts(self):
        src = (REPO_ROOT / "ui" / "app_monitor.py").read_text(encoding="utf-8")
        assert "class PerformanceMonitorWindow" in src, "悬浮窗类不该被搬走"
        assert "class MonitorMixin" in src, "Mixin 不该被搬走"
        assert "_monitor_svc" in src, "界面侧应改为引用服务"

    def test_ui_file_no_longer_touches_moved_symbols_directly(self):
        """界面侧不得再直接引用被搬走的私有符号（否则一会儿用服务一会儿用老名，容易漂移）。"""
        src = (REPO_ROOT / "ui" / "app_monitor.py").read_text(encoding="utf-8")
        for stale in ("_psutil_available", "_keyboard_available", "_keyboard_monitor",
                      "_GPUDetector", "_gpu_detector", "_gpu_cache", "_gpu_last_sample"):
            # 允许出现在赋值/注释里（例如 _format_net_speed = _monitor_svc._format_net_speed）
            uses = [
                ln for ln in src.splitlines()
                if re.search(rf"(?<![\w.]){re.escape(stale)}(?![\w])", ln)
                and not ln.strip().startswith("#")
            ]
            assert uses == [], f"界面侧仍在直接使用 {stale}: {uses[:3]}"
