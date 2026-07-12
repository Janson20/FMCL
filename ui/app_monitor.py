"""性能监控悬浮窗 - 快捷键 Ctrl+Shift+M 切换显示/隐藏"""

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _

logger = logging.getLogger(__name__)

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

MONITOR_HOTKEY = "ctrl+shift+m"

# GPU 采集间隔（GPU 查询较慢，降低频率）
_GPU_SAMPLE_INTERVAL = 2
# 整体刷新间隔
_REFRESH_INTERVAL = 1.0


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
                ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=5
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
                ["rocm-smi", "--showproductname", "--csv"], capture_output=True, text=True, timeout=5
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
            result = subprocess.run(["amd-smi", "static", "--json"], capture_output=True, text=True, timeout=5)
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
                timeout=5,
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
            result = subprocess.run(["amd-smi", "metric", "--csv"], capture_output=True, text=True, timeout=5)
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


# ─── 性能监控窗口 ────────────────────────────────────────────


class PerformanceMonitorWindow(ctk.CTkToplevel):
    """性能监控悬浮窗 - 无边框、置顶、可拖拽"""

    def __init__(self, parent):
        super().__init__(parent)
        self._parent = parent

        # 窗口配置
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color=COLORS["bg_dark"])
        # 半透明效果
        try:
            self.attributes("-alpha", 0.80)
        except Exception:
            pass

        # 窗口尺寸与位置
        self._win_w = 400
        self._win_h = 200
        self._hint_timer_id: str | None = None
        self.geometry(f"{self._win_w}x{self._win_h}")

        # 默认位置：屏幕右上角
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        self.geometry(f"+{screen_w - self._win_w - 10}+50")

        # 拖拽支持
        self._drag_x = 0
        self._drag_y = 0
        self.bind("<Button-1>", self._on_drag_start)
        self.bind("<B1-Motion>", self._on_drag_motion)

        # 运行状态
        self._running = False
        self._refresh_timer_id = None
        self._gpu_detector: _GPUDetector | None = None

        # GPU 缓存（降低采样频率）
        self._gpu_cache: dict = {}
        self._gpu_last_sample: float = 0

        # 网络 IO 上一次值（用于计算速率）
        self._net_prev: dict = {}
        self._net_prev_time: float = 0

        # 磁盘 IO 上一次值
        self._disk_prev: dict = {}
        self._disk_prev_time: float = 0

        # 主题引用（跟随主题切换）
        self._theme_refs: list = []

        self._build_ui()
        self._init_gpu_monitor()

        # 绑定主题更新事件
        self.protocol("WM_DELETE_WINDOW", self._on_close_attempt)
        self.bind("<Destroy>", self._on_destroy)

    def _build_ui(self):
        """构建监控面板 UI - 紧凑单行布局"""
        # 标题栏
        title_bar = ctk.CTkFrame(self, fg_color=COLORS["accent"], height=28, corner_radius=0)
        title_bar.pack(fill=ctk.X)
        title_bar.pack_propagate(False)

        ctk.CTkLabel(
            title_bar,
            text=_("monitor_title"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.LEFT, padx=(12, 0), pady=3)

        ctk.CTkLabel(
            title_bar,
            text="Ctrl+Shift+M",
            font=ctk.CTkFont(family=FONT_FAMILY, size=9),
            text_color=COLORS["text_primary"],
        ).pack(side=ctk.RIGHT, padx=(0, 12), pady=3)

        self._theme_refs.append((title_bar, {"fg_color": "accent"}))

        # 内容区域
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill=ctk.BOTH, expand=True, padx=12, pady=(8, 8))

        # ── CPU 行 ──
        cpu_row = ctk.CTkFrame(content, fg_color="transparent")
        cpu_row.pack(fill=ctk.X, pady=(0, 4))

        self._cpu_header = ctk.CTkLabel(
            cpu_row,
            text=_("monitor_cpu"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=COLORS["accent"],
            width=34,
            anchor=ctk.W,
        )
        self._cpu_header.pack(side=ctk.LEFT, padx=(0, 4))

        self._cpu_progress = ctk.CTkProgressBar(
            cpu_row, width=100, height=8, fg_color=COLORS["bg_light"], progress_color=COLORS["accent"]
        )
        self._cpu_progress.pack(side=ctk.LEFT, padx=(0, 6))
        self._cpu_progress.set(0)

        self._cpu_label = ctk.CTkLabel(
            cpu_row,
            text="0%",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            width=46,
            anchor=ctk.E,
        )
        self._cpu_label.pack(side=ctk.LEFT, padx=(0, 6))

        self._cpu_freq_label = ctk.CTkLabel(
            cpu_row,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
        )
        self._cpu_freq_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        # ── 内存行 ──
        mem_row = ctk.CTkFrame(content, fg_color="transparent")
        mem_row.pack(fill=ctk.X, pady=(0, 4))

        self._mem_header = ctk.CTkLabel(
            mem_row,
            text=_("monitor_memory"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=COLORS["warning"],
            width=34,
            anchor=ctk.W,
        )
        self._mem_header.pack(side=ctk.LEFT, padx=(0, 4))

        self._mem_progress = ctk.CTkProgressBar(
            mem_row, width=100, height=8, fg_color=COLORS["bg_light"], progress_color=COLORS["warning"]
        )
        self._mem_progress.pack(side=ctk.LEFT, padx=(0, 6))
        self._mem_progress.set(0)

        self._mem_label = ctk.CTkLabel(
            mem_row,
            text="0%",
            font=ctk.CTkFont(family=FONT_FAMILY, size=11),
            text_color=COLORS["text_primary"],
            width=46,
            anchor=ctk.E,
        )
        self._mem_label.pack(side=ctk.LEFT, padx=(0, 6))

        self._mem_detail_label = ctk.CTkLabel(
            mem_row,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
        )
        self._mem_detail_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        # ── GPU 行 ──
        gpu_row = ctk.CTkFrame(content, fg_color="transparent")
        gpu_row.pack(fill=ctk.X, pady=(0, 4))

        self._gpu_header = ctk.CTkLabel(
            gpu_row,
            text=_("monitor_gpu"),
            font=ctk.CTkFont(family=FONT_FAMILY, size=11, weight="bold"),
            text_color=COLORS["success"],
            width=34,
            anchor=ctk.W,
        )
        self._gpu_header.pack(side=ctk.LEFT, padx=(0, 4))

        self._gpu_detail_label = ctk.CTkLabel(
            gpu_row,
            text="",
            font=ctk.CTkFont(family=FONT_FAMILY, size=10),
            text_color=COLORS["text_secondary"],
            anchor=ctk.W,
        )
        self._gpu_detail_label.pack(side=ctk.LEFT, fill=ctk.X, expand=True)

        # ── 快捷键提示 ──
        self._hint_label = ctk.CTkLabel(
            content, text="", font=ctk.CTkFont(family=FONT_FAMILY, size=10), text_color=COLORS["accent"], anchor=ctk.W
        )
        self._hint_label.pack(fill=ctk.X, pady=(2, 0))
        self._hint_label.pack_forget()

        # 注册主题引用
        self._theme_refs.append((self._cpu_header, {"text_color": "accent"}))
        self._theme_refs.append((self._cpu_progress, {"fg_color": "bg_light", "progress_color": "accent"}))
        self._theme_refs.append((self._cpu_label, {"text_color": "text_primary"}))
        self._theme_refs.append((self._cpu_freq_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._mem_header, {"text_color": "warning"}))
        self._theme_refs.append((self._mem_progress, {"fg_color": "bg_light", "progress_color": "warning"}))
        self._theme_refs.append((self._mem_label, {"text_color": "text_primary"}))
        self._theme_refs.append((self._mem_detail_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._gpu_header, {"text_color": "success"}))
        self._theme_refs.append((self._gpu_detail_label, {"text_color": "text_secondary"}))
        self._theme_refs.append((self._hint_label, {"text_color": "accent"}))

    def _init_gpu_monitor(self):
        """初始化多厂商 GPU 监控"""
        self._gpu_detector = _GPUDetector()
        self._gpu_detector.init()

    def _shutdown_gpu_monitor(self):
        """关闭 GPU 监控"""
        if self._gpu_detector is not None:
            self._gpu_detector.shutdown()
            self._gpu_detector = None

    def _on_drag_start(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def _on_drag_motion(self, event):
        x = self.winfo_x() + event.x - self._drag_x
        y = self.winfo_y() + event.y - self._drag_y
        self.geometry(f"+{x}+{y}")

    def _on_close_attempt(self):
        """阻止通过 WM 关闭（只能通过快捷键切换）"""
        pass

    def _on_destroy(self, event):
        """窗口被销毁时的清理"""
        self.stop()

    def start(self):
        """开始采集并显示"""
        if self._running:
            return
        self._running = True
        self._net_prev = {}
        self._net_prev_time = 0
        self._disk_prev = {}
        self._disk_prev_time = 0
        self._schedule_refresh()

    def stop(self):
        """停止采集"""
        self._running = False
        if self._refresh_timer_id is not None:
            self.after_cancel(self._refresh_timer_id)
            self._refresh_timer_id = None
        if self._hint_timer_id is not None:
            self.after_cancel(self._hint_timer_id)
            self._hint_timer_id = None
        self._shutdown_gpu_monitor()

    def show_hint(self):
        """显示快捷键提示，10 秒后自动隐藏"""
        if not self.winfo_exists():
            return
        self._hint_label.configure(text=_("monitor_hint"))
        self._hint_label.pack(fill=ctk.X, pady=(8, 0))
        self._hint_timer_id = self.after(10000, self._hide_hint)

    def _hide_hint(self):
        """隐藏快捷键提示"""
        if self.winfo_exists():
            self._hint_label.pack_forget()
        self._hint_timer_id = None

    def _schedule_refresh(self):
        """调度下一次刷新"""
        if not self._running:
            return
        self._do_refresh()
        self._refresh_timer_id = self.after(int(_REFRESH_INTERVAL * 1000), self._schedule_refresh)

    def _do_refresh(self):
        """执行一次数据采集（线程安全）"""
        t = threading.Thread(target=self._collect_metrics, daemon=True)
        t.start()

    def _collect_metrics(self):
        """在子线程中采集系统指标"""
        if not _psutil_available:
            return

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
            self._gpu_cache = self._sample_gpu()
        result["gpu_name"] = self._gpu_cache.get("name", "")
        result["gpu_util"] = self._gpu_cache.get("util", "")
        result["gpu_mem"] = self._gpu_cache.get("mem", "")
        result["gpu_temp"] = self._gpu_cache.get("temp", "")

        # 推送到主线程
        self.after(0, lambda: self._update_ui(result))

    def _sample_gpu(self) -> dict:
        """采样 GPU 信息（通过统一检测器）"""
        if self._gpu_detector is not None:
            return self._gpu_detector.sample()
        return {}

    def _update_ui(self, data: dict):
        """在主线程中更新 UI"""
        if not self.winfo_exists():
            return

        try:
            # CPU
            cpu_pct = data.get("cpu_percent", 0)
            self._cpu_progress.set(cpu_pct / 100.0)
            self._cpu_label.configure(text=f"{cpu_pct:.1f}%")
            self._cpu_freq_label.configure(text=data.get("cpu_freq", "N/A"))

            # 内存
            mem_pct = data.get("mem_percent", 0)
            self._mem_progress.set(mem_pct / 100.0)
            self._mem_label.configure(text=f"{mem_pct:.1f}%")
            self._mem_detail_label.configure(text=f"{data.get('mem_used', 'N/A')} / {data.get('mem_total', 'N/A')}")

            # GPU - 单行紧凑显示
            gpu_items = []
            if data.get("gpu_name"):
                gpu_items.append(data["gpu_name"])
            if data.get("gpu_util"):
                gpu_items.append(data["gpu_util"])
            if data.get("gpu_mem"):
                gpu_items.append(data["gpu_mem"])
            if data.get("gpu_temp"):
                gpu_items.append(data["gpu_temp"])
            self._gpu_detail_label.configure(text=" | ".join(gpu_items) if gpu_items else _("monitor_gpu_na"))

        except Exception:
            pass

    def refresh_theme(self):
        """跟随主题更新颜色"""
        for widget, color_map in self._theme_refs:
            try:
                if not widget.winfo_exists():
                    continue
                for attr, color_key in color_map.items():
                    current_cfg = widget.cget(attr) if hasattr(widget, "cget") else None
                    if current_cfg is None:
                        continue
                    # 只更新颜色属性
                    color_value = COLORS.get(color_key, color_key)
                    if isinstance(color_value, str) and color_value.startswith("#"):
                        try:
                            widget.configure(**{attr: color_value})
                        except Exception:
                            pass
                    elif isinstance(color_value, (list, tuple)):
                        try:
                            widget.configure(**{attr: color_value})
                        except Exception:
                            pass
            except Exception:
                pass


# ─── MonitorMixin ───────────────────────────────────────────


class MonitorMixin:
    """性能监控悬浮窗 Mixin - 通过 Ctrl+Shift+M 快捷键切换"""

    def _init_monitor(self):
        """初始化监控模块（延迟调用）"""
        self._monitor_window: "PerformanceMonitorWindow | None" = None
        self._monitor_hotkeys_registered: bool = False
        self._monitor_warmup_hook = None

    def _register_monitor_hotkeys(self):
        """注册监控快捷键"""
        if self._monitor_hotkeys_registered:
            return
        if not _keyboard_available:
            logger.debug("keyboard 库不可用，性能监控热键已禁用")
            return

        def _do_register():
            try:
                self._monitor_warmup_hook = _keyboard_monitor.hook(lambda e: None)
                time.sleep(0.1)
                _keyboard_monitor.add_hotkey(MONITOR_HOTKEY, self._toggle_monitor)
                self._monitor_hotkeys_registered = True
                logger.info("性能监控全局热键已注册")
            except Exception as e:
                global _keyboard_available
                _keyboard_available = False
                if sys.platform == "win32":
                    logger.warning(f"注册性能监控热键失败: {e}")
                else:
                    logger.debug(f"当前平台不支持全局热键，已跳过: {e}")

        threading.Thread(target=_do_register, daemon=True).start()

    def _unregister_monitor_hotkeys(self):
        """注销监控快捷键"""
        if not self._monitor_hotkeys_registered:
            return
        if not _keyboard_available:
            return
        try:
            _keyboard_monitor.remove_hotkey(MONITOR_HOTKEY)
            if self._monitor_warmup_hook is not None:
                self._monitor_warmup_hook()
                self._monitor_warmup_hook = None
            self._monitor_hotkeys_registered = False
            logger.info("性能监控全局热键已注销")
        except Exception:
            pass

    def _toggle_monitor(self):
        """切换性能监控窗口显示/隐藏（热键回调）"""
        self.after(0, lambda: self._toggle_monitor_ui(show_hint=False))

    def _toggle_monitor_ui(self, show_hint: bool = False):
        """在主线程中切换监控窗口"""
        if not hasattr(self, "_monitor_window") or self._monitor_window is None:
            self._show_monitor(show_hint=show_hint)
        elif self._monitor_window.winfo_exists():
            self._hide_monitor()
        else:
            self._show_monitor(show_hint=show_hint)

    def _show_monitor(self, show_hint: bool = False):
        """显示监控窗口"""
        if not _psutil_available:
            logger.warning("psutil 不可用，无法启动性能监控")
            return
        try:
            self._monitor_window = PerformanceMonitorWindow(self)
            self._monitor_window.start()
            self._monitor_window.focus_set()
            if show_hint:
                self._monitor_window.show_hint()
        except Exception as e:
            logger.error(f"创建性能监控窗口失败: {e}")

    def _hide_monitor(self):
        """隐藏并销毁监控窗口"""
        if self._monitor_window is not None:
            try:
                self._monitor_window.stop()
                self._monitor_window.destroy()
            except Exception:
                pass
            self._monitor_window = None
