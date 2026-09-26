"""系统性能指标采集服务（阶段 1 任务 1.13 抽取）。

从 ``ui/app_monitor.py`` **逐字搬运**而来：多厂商 GPU 检测/采样、CPU/内存/交换区
采样、以及全局热键的注册与注销。搬运方式是用脚本按锚点抽取、原样拼接，并断言
每个块都是本文件的严格子串（见 ``poc/_extract_monitor_service.py``），
因此行为与改造前一致。

本模块**零 UI 依赖**（由 ``scripts/check_services_purity.py`` 强制）。
界面相关的东西（悬浮窗部件、窗口生命周期、把结果刷到控件上）留在
``ui/app_monitor.py``。

公开 API：

- :class:`MetricsCollector` —— 采集一次系统指标，返回可直接渲染的 dict
- :class:`HotkeyManager` —— 注册/注销全局热键，回调由调用方提供
- ``psutil_available`` / ``pynvml_available`` / ``keyboard_available`` /
  ``gpu_detector_available`` —— 依赖可用性标志（**可变状态**）

.. note::
   这四个标志是可变状态（热键注册失败会把 ``keyboard_available`` 置 False），
   所以调用方应通过 ``monitor_service.<flag>`` 读取，而不是
   ``from services.monitor_service import keyboard_available`` —— 后者拿到的是
   导入那一刻的副本，后续翻转看不到。这是搬运时特意保留的语义。
"""

import logging
import os
import re
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

#: 全局热键（原样保留）
MONITOR_HOTKEY = "ctrl+shift+m"

# GPU 采集间隔（GPU 查询较慢，降低频率）
_GPU_SAMPLE_INTERVAL = 2
# 整体刷新间隔
_REFRESH_INTERVAL = 1.0


# ─── 依赖检测 ───────────────────────────────────────────────
try:
    import psutil

    _psutil_available = True
except ImportError:
    _psutil_available = False
    logger.warning("psutil 库不可用，性能监控功能将受限")

try:
    import pynvml

    _pynvml_available = True
except ImportError:
    _pynvml_available = False
    logger.debug("pynvml 库不可用，NVIDIA GPU 监控将降级")

try:
    import keyboard as _keyboard_monitor

    _keyboard_available = True
except Exception:
    _keyboard_available = False

try:
    from gpu_detector import detect as gpu_detect

    _gpu_detector_available = True
except ImportError:
    _gpu_detector_available = False
    logger.debug("gpu-detector 库不可用，多厂商 GPU 检测将降级")

def _format_bytes(n: int) -> str:
    """将字节数格式化为可读字符串"""
    if n < 1024:
        return f"{n} B"
    elif n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    elif n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    else:
        return f"{n / (1024 * 1024 * 1024):.2f} GB"


def _format_net_speed(bytes_per_sec: float) -> str:
    """将网络/磁盘速度格式化为可读字符串"""
    abs_val = abs(bytes_per_sec)
    if abs_val < 1024:
        return f"{bytes_per_sec:.0f} B/s"
    elif abs_val < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    elif abs_val < 1024 * 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"
    else:
        return f"{bytes_per_sec / (1024 * 1024 * 1024):.2f} GB/s"


# ─── 多厂商 GPU 检测与采样 ────────────────────────────────────


class _GPUDetector:
    """统一 GPU 检测器：按优先级尝试 NVIDIA → AMD → Intel 后端"""

    def __init__(self):
        self._backend: str = ""
        self._backend_name: str = ""
        self._nvml_handle = None
        self._gpu_cache: dict = {}

    def init(self):
        """按优先级尝试各后端"""
        if self._try_pynvml():
            return
        if self._try_nvidia_smi():
            return
        if self._try_rocm_smi():
            return
        if self._try_amd_smi():
            return
        self._try_gpu_detector()

    def _set_backend(self, name: str):
        self._backend = name
        self._backend_name = name
        if name:
            logger.debug(f"GPU 监控后端: {name}")
        else:
            logger.debug("未检测到可用 GPU 监控后端")

    def _try_pynvml(self) -> bool:
        if not _pynvml_available:
            return False
        try:
            pynvml.nvmlInit()
            count = pynvml.nvmlDeviceGetCount()
            if count > 0:
                self._nvml_handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                self._set_backend("pynvml")
                return True
            pynvml.nvmlShutdown()
        except Exception:
            pass
        return False

    def _try_nvidia_smi(self) -> bool:
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0 and result.stdout.strip():
                self._gpu_cache["name"] = result.stdout.strip().split("\n")[0].strip()
                self._set_backend("nvidia-smi")
                return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return False

    def _try_rocm_smi(self) -> bool:
        if sys.platform != "linux":
            return False
        try:
            result = subprocess.run(
                ["rocm-smi", "--showproductname", "--csv"],
                capture_output=True, text=True, errors="replace", timeout=5,
            )
            if result.returncode == 0 and "GPU" in result.stdout:
                # 提取 GPU 名称
                for line in result.stdout.strip().split("\n"):
                    if "card" in line.lower() or "GPU" in line:
                        parts = line.split(",")
                        if len(parts) >= 2:
                            self._gpu_cache["name"] = parts[1].strip().strip('"')
                            self._set_backend("rocm-smi")
                            return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return False

    def _try_amd_smi(self) -> bool:
        try:
            result = subprocess.run(
                ["amd-smi", "static", "--json"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                try:
                    data = json.loads(result.stdout)
                except json.JSONDecodeError:
                    return False
                cards = data.get("cards", []) or data
                if cards:
                    first = cards[0] if isinstance(cards, list) else next(iter(cards.values()), {})
                    name = first.get("product_name", first.get("name", ""))
                    if name:
                        self._gpu_cache["name"] = str(name)
                        self._set_backend("amd-smi")
                        return True
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return False

    def _try_gpu_detector(self):
        if not _gpu_detector_available:
            self._set_backend("")
            return
        try:
            gpus = gpu_detect()
            if gpus:
                g = gpus[0]
                self._gpu_cache["name"] = str(getattr(g, "name", "Unknown GPU"))
                # gpu-detector 提供静态信息，无法获取实时数据
                self._set_backend("gpu-detector")
        except Exception:
            self._set_backend("")

    def shutdown(self):
        if self._backend == "pynvml" and self._nvml_handle is not None:
            try:
                pynvml.nvmlShutdown()
            except Exception:
                pass
            self._nvml_handle = None

    def sample(self) -> dict:
        """采样 GPU 信息，返回 {name, util, mem, temp}"""
        if self._backend == "pynvml":
            return self._sample_pynvml()
        if self._backend == "nvidia-smi":
            return self._sample_nvidia_smi()
        if self._backend == "rocm-smi":
            return self._sample_rocm_smi()
        if self._backend == "amd-smi":
            return self._sample_amd_smi()
        if self._backend == "gpu-detector":
            return self._gpu_cache.copy()  # 静态信息
        return {}

    def _sample_pynvml(self) -> dict:
        gpu = {}
        try:
            name = pynvml.nvmlDeviceGetName(self._nvml_handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            gpu["name"] = str(name)

            util_info = pynvml.nvmlDeviceGetUtilizationRates(self._nvml_handle)
            gpu["util"] = f"{util_info.gpu}%"

            mem_info = pynvml.nvmlDeviceGetMemoryInfo(self._nvml_handle)
            pct = (mem_info.used / mem_info.total * 100) if mem_info.total > 0 else 0
            gpu["mem"] = f"{_format_bytes(mem_info.used)} / {_format_bytes(mem_info.total)} ({pct:.0f}%)"

            try:
                temp = pynvml.nvmlDeviceGetTemperature(self._nvml_handle, pynvml.NVML_TEMPERATURE_GPU)
                gpu["temp"] = f"{temp}°C"
            except Exception:
                pass
        except Exception:
            pass
        return gpu

    def _sample_nvidia_smi(self) -> dict:
        gpu = dict(self._gpu_cache)
        try:
            result = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                vals = [v.strip() for v in result.stdout.strip().split(",")]
                if len(vals) >= 4:
                    gpu["util"] = f"{vals[0]}%"
                    used = int(vals[1]) * 1024 * 1024
                    total = int(vals[2]) * 1024 * 1024
                    pct = (used / total * 100) if total > 0 else 0
                    gpu["mem"] = f"{_format_bytes(used)} / {_format_bytes(total)} ({pct:.0f}%)"
                    if vals[3] and vals[3] != "[Not Supported]":
                        gpu["temp"] = f"{vals[3]}°C"
        except Exception:
            pass
        return gpu

    def _sample_rocm_smi(self) -> dict:
        gpu = dict(self._gpu_cache)
        try:
            result = subprocess.run(
                ["rocm-smi", "--showuse", "--showmemuse", "--showtemp", "--csv"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
            )
            if result.returncode == 0:
                lines = [l for l in result.stdout.strip().split("\n") if l and not l.startswith("#")]
                if lines:
                    data = lines[-1]
                    cols = [c.strip().strip('"') for c in data.split(",")]
                    if len(cols) >= 4:
                        gpu["util"] = f"{cols[1]}%"
                    if len(cols) >= 5:
                        mem_used_match = re.search(r"(\d+)%", cols[2])
                        if mem_used_match:
                            gpu["mem"] = f"{mem_used_match.group(1)}%"
                    if len(cols) >= 6:
                        temp_match = re.search(r"(\d+)", cols[3])
                        if temp_match:
                            gpu["temp"] = f"{temp_match.group(1)}°C"
        except Exception:
            pass
        return gpu

    def _sample_amd_smi(self) -> dict:
        gpu = dict(self._gpu_cache)
        try:
            result = subprocess.run(
                ["amd-smi", "metric", "--csv"],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=5,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                lines = [l for l in result.stdout.strip().split("\n") if l]
                if len(lines) > 1:
                    headers = [h.strip() for h in lines[0].split(",")]
                    values = [v.strip() for v in lines[1].split(",")]
                    row = dict(zip(headers, values))
                    for h in headers:
                        hl = h.lower()
                        if "utilization" in hl or "gfx_activity" in hl:
                            gpu["util"] = str(row.get(h, ""))
                        elif "memory_used" in hl:
                            gpu["mem"] = str(row.get(h, ""))
                        elif "temperature" in hl:
                            temp_val = row.get(h, "").replace("°C", "").strip()
                            gpu["temp"] = f"{temp_val}°C"
        except Exception:
            pass
        return gpu


# ─── 依赖可用性标志的公开别名 ────────────────────────────────
#
# 这四个标志是**可变状态**：热键注册失败会把 ``_keyboard_available`` 置 False。
# 因此不能简单地写 ``keyboard_available = _keyboard_available`` —— 那样调用方拿到的是
# 导入那一刻的副本，后续翻转看不到。用模块级 ``__getattr__`` 做读取时代理，
# 既保留了原文的私有名（搬运一字未改），又给外部一个稳定的公开名。

_FLAG_ALIASES = {
    "psutil_available": "_psutil_available",
    "pynvml_available": "_pynvml_available",
    "keyboard_available": "_keyboard_available",
    "gpu_detector_available": "_gpu_detector_available",
}


def __getattr__(name):
    """``monitor_service.<flag>`` 读取的是**当前**值，不是导入时的副本。"""
    target = _FLAG_ALIASES.get(name)
    if target is not None:
        return globals()[target]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_FLAG_ALIASES))


# ─── 指标采集 ────────────────────────────────────────────────


class MetricsCollector:
    """采集一次系统指标，返回可直接渲染的 dict。

    从 ``PerformanceMonitorWindow._collect_metrics`` / ``_sample_gpu`` /
    ``_init_gpu_monitor`` / ``_shutdown_gpu_monitor`` 搬来，**逻辑逐字保留**。

    唯一去掉的是 ``_collect_metrics`` 最后那行 ``self.after(0, lambda: self._update_ui(...))``
    —— "把结果刷到控件上"是界面侧的事，不属于采集服务。界面侧改为
    "调 collect() → 自己 after 到主线程渲染"。

    GPU 采样有独立节流（``_GPU_SAMPLE_INTERVAL`` 秒）。节流状态原先挂在窗口对象上，
    现在挂在本对象上：一个窗口对应一个采集器，语义等价。
    """

    def __init__(self) -> None:
        self._gpu_detector = None
        self._gpu_cache: dict = {}
        self._gpu_last_sample = 0.0

    # ── GPU 检测器生命周期（原 PerformanceMonitorWindow 的同名方法）──

    def init_gpu(self) -> None:
        """初始化多厂商 GPU 检测器。"""
        self._gpu_detector = _GPUDetector()
        self._gpu_detector.init()

    def shutdown_gpu(self) -> None:
        """关闭 GPU 检测器。"""
        if self._gpu_detector is not None:
            self._gpu_detector.shutdown()
            self._gpu_detector = None

    def sample_gpu(self) -> dict:
        """采样 GPU 信息（通过统一检测器）。"""
        if self._gpu_detector is not None:
            return self._gpu_detector.sample()
        return {}

    def collect(self) -> dict:
        """采集一次 CPU / 内存 / 交换区 / GPU 指标（阻塞式，应在工作线程调用）。"""
        if not _psutil_available:
            return {}

        result: dict = {}

        # ── CPU ──
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            result["cpu_percent"] = cpu_percent
            freq = psutil.cpu_freq()
            if freq and freq.current > 0:
                result["cpu_freq"] = f"{freq.current:.0f} MHz"
            else:
                result["cpu_freq"] = "N/A"
        except Exception:
            result["cpu_percent"] = 0
            result["cpu_freq"] = "N/A"

        # ── 内存 ──
        try:
            mem = psutil.virtual_memory()
            result["mem_percent"] = mem.percent
            result["mem_used"] = _format_bytes(mem.used)
            result["mem_total"] = _format_bytes(mem.total)
            swap = psutil.swap_memory()
            result["swap_used"] = _format_bytes(swap.used)
            result["swap_total"] = _format_bytes(swap.total)
        except Exception:
            result["mem_percent"] = 0
            result["mem_used"] = "N/A"
            result["mem_total"] = "N/A"
            result["swap_used"] = "N/A"
            result["swap_total"] = "N/A"

        # ── GPU ──
        now = time.time()
        if now - self._gpu_last_sample >= _GPU_SAMPLE_INTERVAL:
            self._gpu_last_sample = now
            self._gpu_cache = self.sample_gpu()
        result["gpu_name"] = self._gpu_cache.get("name", "")
        result["gpu_util"] = self._gpu_cache.get("util", "")
        result["gpu_mem"] = self._gpu_cache.get("mem", "")
        result["gpu_temp"] = self._gpu_cache.get("temp", "")

        return result


# ─── 全局热键 ────────────────────────────────────────────────


class HotkeyManager:
    """注册/注销全局热键，回调由调用方提供。

    从 ``MonitorMixin._register_monitor_hotkeys`` / ``_unregister_monitor_hotkeys``
    搬来，逻辑逐字保留：先 ``hook`` 一个预热钩子再 ``add_hotkey``（``keyboard``
    库在部分环境下需要预热），注册失败会把 ``_keyboard_available`` 置 False
    并在非 Windows 上降级为 debug 日志。注册过程本身在后台线程里跑。

    与界面的唯一接口是 ``callback``：热键触发时由调用方决定怎么切回主线程。
    """

    def __init__(self, hotkey: str = MONITOR_HOTKEY) -> None:
        self.hotkey = hotkey
        self._registered = False
        self._warmup_hook = None

    @property
    def registered(self) -> bool:
        return self._registered

    def register(self, callback) -> None:
        """注册热键（非阻塞：真正的注册在后台线程里做）。"""
        if self._registered:
            return
        if not _keyboard_available:
            logger.debug("keyboard 库不可用，性能监控热键已禁用")
            return

        def _do_register():
            global _keyboard_available
            try:
                self._warmup_hook = _keyboard_monitor.hook(lambda e: None)
                time.sleep(0.1)
                _keyboard_monitor.add_hotkey(self.hotkey, callback)
                self._registered = True
                logger.info("性能监控全局热键已注册")
            except Exception as e:
                _keyboard_available = False
                if sys.platform == "win32":
                    logger.warning(f"注册性能监控热键失败: {e}")
                else:
                    logger.debug(f"当前平台不支持全局热键，已跳过: {e}")

        threading.Thread(target=_do_register, daemon=True).start()

    def unregister(self) -> None:
        """注销热键。"""
        if not self._registered:
            return
        if not _keyboard_available:
            return
        try:
            _keyboard_monitor.remove_hotkey(self.hotkey)
            if self._warmup_hook is not None:
                self._warmup_hook()
                self._warmup_hook = None
            self._registered = False
            logger.info("性能监控全局热键已注销")
        except Exception:
            pass


__all__ = [
    "MONITOR_HOTKEY",
    "MetricsCollector",
    "HotkeyManager",
    "psutil_available",
    "pynvml_available",
    "keyboard_available",
    "gpu_detector_available",
]


