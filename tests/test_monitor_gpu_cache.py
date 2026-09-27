"""D-11 的并发回归守卫：GPU 缓存与检测器只做「整体发布 / 只读快照」。

现场（修复前）：`MetricsCollector.collect()` 在工作线程里跑，做完
`self._gpu_cache = self.sample_gpu()`（整体替换）之后**又连续读了它 4 次**；
同时主线程的 `init_gpu()` / `shutdown_gpu()` 会写 `self._gpu_detector`。
两个采样线程同时在跑时（QML 侧线程池 / Tk 侧重叠的刷新拍），那 4 个字段就可能
来自两份不同采样 —— 例如显卡名是新的、占用率还是上一拍的；`sample_gpu()` 里
还夹着一次「先检查后使用」。

修复方式（无锁，服务层不引锁）：写侧「整体构造 → 一次性替换」，读侧
「绑定一次局部引用 → 只读这份快照」。本文件守住三件事：

1. **2 秒节流还在**：用假时钟把窗口两侧都走一遍（1 秒后不查、2.5 秒后才查）。
   修竞态最容易顺手把节流删掉，那会让 GPU 查询变频繁 —— 这是本缺陷的硬约束；
2. **并发替换 + 并发读**：读者拿到的 4 个字段永远出自同一份快照，且从不抛异常；
3. 上面两条**不是空断言**：把修复前的读法复刻成子类，同一套并发夹具跑它
   **必须**抓到拼接（负例自检）；另有一条 AST 结构守卫，与线程调度无关。

跑法::

    .venv\\Scripts\\python.exe -m pytest tests/test_monitor_gpu_cache.py -q
"""

from __future__ import annotations

import ast
import importlib
import itertools
import re
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

#: collect() 在 psutil 缺席时直接返回 {}（服务里的早退分支），那本文件测不到任何东西
pytest.importorskip("psutil")

#: collect() 结果里的 GPU 字段顺序（与服务的 4 个渲染键一一对应）
GPU_FIELDS = ("gpu_name", "gpu_util", "gpu_mem", "gpu_temp")
#: 快照内部键（与上面同序）
SNAPSHOT_KEYS = ("name", "util", "mem", "temp")


@pytest.fixture()
def svc():
    return importlib.import_module("services.monitor_service")


@pytest.fixture()
def fast_psutil(monkeypatch, svc):
    """把 ``psutil.cpu_percent(interval=0.1)`` 的 100ms 阻塞换成即时返回。

    采集循环要跑上千次，不换掉这一处就要多花好几分钟。被替换的只有「等 100ms 再取
    占用率」这一行，collect() 的其余路径（内存 / 交换区 / GPU 段）与生产代码一致。
    """
    monkeypatch.setattr(svc.psutil, "cpu_percent", lambda interval=0: 1.0)


def _snapshot(generation: int) -> dict:
    """一份**自洽**的 GPU 快照：4 个字段都带同一个代号，拼接一眼可见。"""
    return {
        "name": f"GPU-{generation}",
        "util": f"util-{generation}",
        "mem": f"mem-{generation}",
        "temp": f"temp-{generation}",
    }


def _tag_of(value) -> "int | None":
    """从字段值尾部反解代号；空字符串表示「还没有快照」。"""
    match = re.search(r"(\d+)$", str(value or ""))
    return int(match.group(1)) if match else None


def _tags_of(data: dict) -> set:
    """一次采集结果里 4 个 GPU 字段各自带的代号集合。

    自洽的定义：``{None}``（还没有快照，4 个字段都空）或者只有一个元素。
    """
    return {_tag_of(data.get(field, "")) for field in GPU_FIELDS}


class _FakeClock:
    """只替 ``time.time()`` 的假时钟（collect() 读时间只用这一个函数）。

    ``sleep`` 转发给真模块：服务的热键线程会调 ``time.sleep`` —— 本文件不注册热键，
    但替身少一个属性是没必要的隐患。
    """

    def __init__(self, real, now: float = 1000.0) -> None:
        self._real = real
        self.now = now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self._real.sleep(seconds)


def test_gpu_throttle_is_kept(monkeypatch, svc):
    """2 秒节流必须还在：修竞态不许顺手把 GPU 查询变频繁。

    比 ``tests/test_monitor_service.py`` 的「连调两次只查一次」更严：用假时钟把
    节流窗口两侧都走一遍，并断言「不查」的那一拍沿用上一版快照而不是变空。
    """
    collector = svc.MetricsCollector()
    clock = _FakeClock(svc.time)
    monkeypatch.setattr(svc, "time", clock)

    sampled_at = []

    def fake_sample():
        sampled_at.append(clock.now)
        return _snapshot(len(sampled_at))

    collector.sample_gpu = fake_sample

    first = collector.collect()
    assert sampled_at == [1000.0]
    assert _tags_of(first) == {1}

    clock.now += 1.0
    second = collector.collect()
    assert sampled_at == [1000.0], "距上次采样只过了 1 秒就又查了一次 GPU —— 节流被改坏了"
    assert _tags_of(second) == {1}, "节流窗口内应当沿用上一版快照，不该变空"

    clock.now += 1.5
    third = collector.collect()
    assert sampled_at == [1000.0, 1002.5], "过了 2.5 秒仍不采样 —— 节流把常态查询也挡住了"
    assert _tags_of(third) == {2}


def test_concurrent_publish_and_read_never_splice(monkeypatch, svc, fast_psutil):
    """一个线程反复「整体替换」缓存，另一个线程反复读：读者永远拿到自洽的一份。

    两侧走的都是生产路径：写侧与读侧都是 ``collect()``（把节流窗口改成 0，让每次
    采集都必须真正采样 —— 交替频率拉到最大）。
    """
    collector = svc.MetricsCollector()
    monkeypatch.setattr(svc, "_GPU_SAMPLE_INTERVAL", 0.0)

    generations = itertools.count(1)
    samplers = set()

    def fake_sample():
        # 「构造一份快照」有真实耗时：两个线程必然重叠，这正是修复前会拼接的窗口
        generation = next(generations)
        samplers.add(threading.get_ident())
        time.sleep(0.0002)
        return _snapshot(generation)

    collector.sample_gpu = fake_sample

    stop = threading.Event()
    writer_errors = []

    def writer():
        try:
            while not stop.is_set():
                collector.collect()
        except Exception as exc:  # pragma: no cover - 写侧异常直接让用例失败
            writer_errors.append(exc)

    thread = threading.Thread(target=writer, name="gpu-cache-writer", daemon=True)
    thread.start()
    rounds = 1200
    spliced = []
    reads = 0
    previous_interval = sys.getswitchinterval()
    # 默认 5ms 的切换间隔太粗：修复前那 4 次取值之间只隔约 0.2 微秒，调度器根本抢不进去，
    # 于是这条用例会在**有缺陷的代码**上也变绿（实测过：修成 1 微秒后，同一段代码
    # 1200 轮里被抓到 103 次拼接 —— 这条用例才真的能抓回归）。
    sys.setswitchinterval(1e-6)
    try:
        for _ in range(rounds):
            data = collector.collect()
            reads += 1
            tags = _tags_of(data)
            if len(tags) != 1:
                spliced.append(tags)
    finally:
        sys.setswitchinterval(previous_interval)
        stop.set()
        thread.join(timeout=5)

    assert thread.is_alive() is False, "写线程没能在 5 秒内退出"
    assert writer_errors == [], f"写侧抛异常：{writer_errors[:3]}"
    assert reads == rounds
    # 不是空断言的前提：两个线程**确实**都走过采样/发布路径（真并发，不是轮流跑）
    assert len(samplers) >= 2, f"只有 {len(samplers)} 条线程参与采样，并发窗口没被打开"
    assert next(generations) > 100, "发布次数太少，交替强度不足以构成回归守卫"
    assert spliced == [], f"读者读到了两份采样拼起来的字段：{spliced[:5]}"


def _pre_fix_reader(base):
    """复刻 D-11 修复前的读法：4 个字段**各自重新取一次** ``self._gpu_cache``。

    只用于负例自检（证明「自洽」不变式抓得住拼接），不进生产代码。
    字段之间 ``time.sleep(0)`` 让出 GIL —— 真实并发下「另一个线程整体替换缓存」
    就发生在这个位置。
    """

    class _PreFixReader(base):
        def read_gpu(self) -> dict:
            result = {}
            for field, key in zip(GPU_FIELDS, SNAPSHOT_KEYS):
                result[field] = self._gpu_cache.get(key, "")
                time.sleep(0)
            return result

    return _PreFixReader


def test_negative_control_the_invariant_catches_the_pre_fix_pattern(svc):
    """负例自检：同一套「自洽」不变式，对修复前的读法**必须**报红。

    没有这一条，上面那条并发用例可能只是「运气好没撞上」。这里刻意把读窗口拉宽
    （字段之间 ``time.sleep(0)`` 让出 GIL）并让写线程**不停地**整体替换缓存：
    修复前的读法必然读到 ``[旧, 旧, 新, 新]`` 这种拼接，于是「代号集合只有 1 个
    元素」这个判据会红 —— 判据的灵敏度被这一条钉住了。

    实测（本机 Python 3.11 / Windows）：300 轮里有 168 轮读到拼接，
    所以这不是「碰运气才偶尔变红」的用例。
    """
    collector = _pre_fix_reader(svc.MetricsCollector)()
    stop = threading.Event()
    generations = itertools.count(1)
    published = [0]

    def writer():
        # 不加任何 sleep：写侧占满自己的时间片，"发布"才真的会落在 4 次读取中间
        while not stop.is_set():
            collector._gpu_cache = _snapshot(next(generations))
            published[0] += 1

    thread = threading.Thread(target=writer, name="gpu-cache-publisher", daemon=True)
    thread.start()
    previous_interval = sys.getswitchinterval()
    # 默认 5ms 的切换间隔太粗，把「读到一半被抢占」真正暴露出来
    sys.setswitchinterval(1e-6)
    try:
        spliced = []
        for _ in range(300):
            tags = _tags_of(collector.read_gpu())
            if len(tags) != 1:
                spliced.append(tags)
    finally:
        sys.setswitchinterval(previous_interval)
        stop.set()
        thread.join(timeout=5)

    assert published[0] > 1000, f"写线程只发布了 {published[0]} 次，交替压力不足，负例自检没意义"
    assert spliced, (
        "负例自检失效：修复前的读法一次拼接都没读到 —— 夹具太温柔，"
        "并发用例里的「spliced == []」就不能当证据"
    )
    assert {item for tags in spliced for item in tags if item is not None} != set(), spliced[:5]


def test_detector_replaced_while_sampling_stays_bounded(monkeypatch, svc, fast_psutil):
    """主线程换检测器、采样线程同时读数：不抛异常，且每拍都是自洽的一份。

    覆盖 D-11 的另一半现场：``sample_gpu()`` 里「先检查 ``self._gpu_detector``
    非空、再调它」的间隙。修复后引用只取一次、``shutdown_gpu`` 只做「摘引用」；
    采样撞上正在关闭的检测器时由后端自己的 try/except 收敛成空字段（有界降级，
    见类 docstring 的「残留」一段），不会把异常抛到调用方。
    """

    class _FakeDetector:
        def __init__(self) -> None:
            self.generation = 0
            self.closed = False

        def init(self) -> None:
            pass

        def sample(self) -> dict:
            if self.closed:
                return {}
            self.generation += 1
            return _snapshot(self.generation)

        def shutdown(self) -> None:
            self.closed = True

    monkeypatch.setattr(svc, "_GPUDetector", _FakeDetector)
    monkeypatch.setattr(svc, "_GPU_SAMPLE_INTERVAL", 0.0)
    collector = svc.MetricsCollector()

    stop = threading.Event()
    lifecycle_errors = []

    def lifecycle():
        try:
            while not stop.is_set():
                collector.init_gpu()
                time.sleep(0.0005)
                collector.shutdown_gpu()
        except Exception as exc:  # pragma: no cover - 生命周期侧异常直接让用例失败
            lifecycle_errors.append(exc)

    thread = threading.Thread(target=lifecycle, name="gpu-detector-lifecycle", daemon=True)
    thread.start()
    rounds = 600
    spliced = []
    try:
        for _ in range(rounds):
            tags = _tags_of(collector.collect())
            if len(tags) != 1:
                spliced.append(tags)
    finally:
        stop.set()
        thread.join(timeout=5)

    assert lifecycle_errors == [], f"换检测器时抛到了调用方：{lifecycle_errors[:3]}"
    assert spliced == [], f"换检测器的同时读到了拼接：{spliced[:5]}"


def test_collect_binds_the_cache_snapshot_exactly_once(svc):
    """结构守卫：``collect()`` 里 ``self._gpu_cache`` 只能一次整体写、一次整体读。

    这一条与线程调度无关，是「防回归」而不是「防竞态」：只要有人把读法改回
    「逐字段重新取属性」（D-11 的原现场），读次数立刻从 1 变成 4，这里就红。
    另外禁止出现 ``self._gpu_cache[...] = ...`` / ``.clear()`` 这类就地修改 ——
    读侧「一整份自洽快照」的前提就是靠「只整体替换」撑着的。
    """
    source = (REPO_ROOT / "services" / "monitor_service.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    collector_cls = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MetricsCollector"
    )
    collect_fn = next(
        node for node in collector_cls.body if isinstance(node, ast.FunctionDef) and node.name == "collect"
    )

    reads = []
    writes = []
    for node in ast.walk(collect_fn):
        if isinstance(node, ast.Attribute) and node.attr == "_gpu_cache":
            (reads if isinstance(node.ctx, ast.Load) else writes).append(node.lineno)

    assert len(reads) == 1, (
        f"collect() 读了 {len(reads)} 次 self._gpu_cache（行 {reads}）—— "
        "应当只绑定一次局部引用，逐字段重读会重新引入「两份采样拼接」"
    )
    assert len(writes) == 1, f"collect() 写了 {len(writes)} 次 self._gpu_cache（行 {writes}）"

    inplace = []
    for node in ast.walk(collector_cls):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute):
            if node.value.attr == "_gpu_cache":
                inplace.append(node.lineno)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("clear", "update", "pop", "setdefault"):
                if isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "_gpu_cache":
                    inplace.append(node.lineno)
    assert inplace == [], f"MetricsCollector 里出现了就地修改缓存：行 {inplace}"
