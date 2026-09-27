"""性能监控悬浮窗 - 快捷键 Ctrl+Shift+M 切换显示/隐藏"""

# D-24：这一段的 import 原本还有 json / os / re / subprocess / sys / time 六个 ——
# 1.13 把 GPU 检测、CPU/内存采集、热键注册搬进 services/monitor_service.py 之后，
# 界面文件只剩"调服务 + 把结果刷到控件上"，这六个模块在这里已经零引用（AST 核对：
# Name 载入都是 0 次）。flake8 的 F401 在 .flake8 里被忽略，所以它们一直挂着没被发现。
# threading 仍要用（_do_refresh 起采集线程），保留。
import logging
import threading

import customtkinter as ctk

from ui.constants import COLORS, FONT_FAMILY
from ui.i18n import _

logger = logging.getLogger(__name__)

# ─── 依赖、常量与纯逻辑辅助（实现已搬到 services/monitor_service.py）───
#
# 阶段 1 任务 1.13：多厂商 GPU 检测/采样、CPU/内存指标采集、全局热键注册这些
# **无 UI 依赖**的逻辑已搬进 ``services/monitor_service.py``，搬运方式是按锚点
# 抽取并断言逐字一致（见 ``poc/_extract_monitor_service.py``）。
# 本文件现在只保留界面部分：悬浮窗部件、窗口生命周期、把采集结果刷到控件上。
#
# 注意依赖标志的读法：这里通过 ``_monitor_svc.<flag>`` 读取，**不能**写成
# ``from services.monitor_service import psutil_available`` —— 后者拿到的是
# 导入那一刻的副本，而 ``keyboard_available`` 会在热键注册失败时被置 False，
# 副本看不到这个翻转。
from services import monitor_service as _monitor_svc

_format_bytes = _monitor_svc._format_bytes
_format_net_speed = _monitor_svc._format_net_speed

#: 全局热键（原样保留，供热键相关代码与测试引用）
MONITOR_HOTKEY = _monitor_svc.MONITOR_HOTKEY

#: 整体刷新间隔（秒）
_REFRESH_INTERVAL = _monitor_svc._REFRESH_INTERVAL


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
        # 指标采集器：GPU 检测器与采样节流状态都挂在它身上（见 services/monitor_service.py）
        # D-24：这里原本还有 _net_prev / _net_prev_time / _disk_prev / _disk_prev_time
        # 四个"上一次 IO 计数"字段（给网络/磁盘速率用）—— 全文件只有这一处声明加
        # start() 里的一次清零，没有任何读点（AST 核对：写 2 处、读 0 处），随网络/磁盘
        # 采集一起作废，删掉。
        self._collector = _monitor_svc.MetricsCollector()

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
        """初始化多厂商 GPU 监控（实现在 services/monitor_service.py）"""
        self._collector.init_gpu()

    def _shutdown_gpu_monitor(self):
        """关闭 GPU 监控（实现在 services/monitor_service.py）"""
        self._collector.shutdown_gpu()

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
        # D-11：采集线程只**读**采集器发布的快照；写侧（init_gpu / shutdown_gpu）
        # 留在主线程（_init_gpu_monitor / _shutdown_gpu_monitor），两侧靠
        # MetricsCollector 的"整体替换 + 只读快照"约定共存，**不加锁**。
        # 约定细节见 services/monitor_service.MetricsCollector 的类 docstring；
        # 改这里之前先读它 —— 在采样线程里调 init/shutdown 会破坏那条约定。
        t = threading.Thread(target=self._collect_metrics, daemon=True)
        t.start()

    def _collect_metrics(self):
        """在子线程中采集系统指标。

        阶段 1 任务 1.13：采集逻辑（CPU / 内存 / 交换区 / GPU，含 GPU 采样节流）
        已搬进 ``services.monitor_service.MetricsCollector.collect()``，逻辑逐字保留。
        本方法只剩"调用服务 + 把结果切回主线程渲染"这两件界面侧的事。
        """
        result = self._collector.collect()
        if not result:
            return
        # 推送到主线程
        self.after(0, lambda: self._update_ui(result))

    def _sample_gpu(self) -> dict:
        """采样 GPU 信息（实现在 services/monitor_service.py）"""
        return self._collector.sample_gpu()

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
        # 热键注册/注销已搬进 services/monitor_service.HotkeyManager（逻辑逐字保留）
        self._monitor_hotkeys: "_monitor_svc.HotkeyManager | None" = None

    def _register_monitor_hotkeys(self):
        """注册监控快捷键（实现在 services/monitor_service.py）"""
        if self._monitor_hotkeys_registered:
            return
        if self._monitor_hotkeys is None:
            self._monitor_hotkeys = _monitor_svc.HotkeyManager()
        self._monitor_hotkeys.register(self._toggle_monitor)
        self._monitor_hotkeys_registered = True

    def _unregister_monitor_hotkeys(self):
        """注销监控快捷键（实现在 services/monitor_service.py）"""
        if self._monitor_hotkeys is None:
            return
        self._monitor_hotkeys.unregister()
        self._monitor_hotkeys = None
        self._monitor_hotkeys_registered = False
        self._monitor_warmup_hook = None

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
        # 通过模块属性读取，才能看到"运行期被翻转成 False"的情况
        if not _monitor_svc.psutil_available:
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
