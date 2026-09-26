"""UI 能力端口（Port）。

核心层与服务层需要"告诉用户一件事"或"问用户一件事"，但**不能**直接
调用 tkinter / PySide6。做法是只依赖本模块定义的 ``UIPort`` 协议，
由 UI 层提供实现：

- ``app.ports.NullUIPort``    —— 无界面场景（CLI、pytest、服务层单测）
- ``ui.ports_tk.TkUIPort``    —— 现役 CustomTkinter 界面（阶段 1）
- ``app.bridges.UIBridge``    —— QML 界面（阶段 2）

线程约定：**端口实现必须保证线程安全，并把自己切回 UI 主线程**。
调用方可能来自任意 worker 线程（例如预下载线程里弹确认框）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Sequence, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass
class ProgressReport:
    """一次进度上报。

    Attributes:
        title: 进度窗口标题（None 表示沿用上一次）。
        message: 当前步骤说明。
        current: 已完成量；负数表示"不确定进度"。
        total: 总量；<=0 表示"不确定进度"。
        level: ``"info"`` / ``"success"`` / ``"warning"`` / ``"error"``。
    """

    title: Optional[str] = None
    message: str = ""
    current: int = -1
    total: int = 0
    level: str = "info"

    @property
    def determinate(self) -> bool:
        return self.current >= 0 and self.total > 0

    @property
    def fraction(self) -> float:
        """0.0~1.0；不确定进度时返回 0.0。"""
        if not self.determinate:
            return 0.0
        return max(0.0, min(1.0, self.current / self.total))


@dataclass
class Choice:
    """``UIPort.choose`` 的一个候选项。"""

    value: str
    label: str
    description: str = ""
    disabled: bool = False
    data: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class UIPort(Protocol):
    """核心层/服务层对"界面"的最小需求集合。

    只声明真正被服务层用到的方法；不要在协议里堆 UI 细节，
    否则每个新界面都要重新实现一遍。

    可选关键字参数约定（实现方按需支持，调用方不得依赖不支持者报错）：

    - ``blocking=True``（``show_info`` / ``show_warning`` / ``show_error``）：
      要求**阻塞到用户关闭对话框**再返回。用于"必须在继续之前让用户看到"
      的提示——例如 GTNH 兼容性警告要求用户在游戏启动前读到它。
      实现方若不支持该参数，必须退化为非阻塞（不得抛异常）；
      调用方若依赖阻塞语义，需在验收里显式验证。
    - ``timeout_s=float``（``confirm`` / ``ask_text`` / ``choose``）：
      覆盖问答类方法的等待上限。
    """

    def is_available(self) -> bool:
        """界面是否已就绪（可安全弹窗）。启动早期为 False。"""
        ...

    def show_info(self, title: str, message: str, **kwargs: Any) -> None: ...

    def show_warning(self, title: str, message: str, **kwargs: Any) -> None: ...

    def show_error(self, title: str, message: str, **kwargs: Any) -> None: ...

    def confirm(self, title: str, message: str, default: bool = False, **kwargs: Any) -> bool: ...

    def ask_text(
        self, title: str, prompt: str, initial: str = "", password: bool = False, **kwargs: Any
    ) -> Optional[str]: ...

    def choose(
        self,
        title: str,
        prompt: str,
        options: Sequence[Choice],
        default: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[str]: ...

    def show_progress(self, report: ProgressReport, **kwargs: Any) -> None: ...

    def close_progress(self, title: Optional[str] = None) -> None: ...

    def set_clipboard(self, text: str) -> bool: ...

    def notify(self, message: str, level: str = "info", **kwargs: Any) -> None: ...


class NullUIPort:
    """什么都不做的端口实现。

    用于 CLI 模式、单元测试、以及"界面尚未创建"的启动早期。
    语义约定：

    - 弹窗类调用 → 只写日志。
    - ``confirm()`` → 返回 ``default``（不阻塞、不猜测用户意图）。
    - ``ask_text()`` / ``choose()`` → 返回 ``None``（调用方必须处理"用户没回答"）。
    - ``set_clipboard()`` → 返回 ``False``，调用方需自行降级。
    """

    def __init__(self, name: str = "NullUIPort") -> None:
        self._name = name
        self.log = logging.getLogger("app.ports.null")

    def is_available(self) -> bool:
        return False

    def show_info(self, title: str, message: str, **kwargs: Any) -> None:
        self.log.info("[info] %s: %s", title, message)

    def show_warning(self, title: str, message: str, **kwargs: Any) -> None:
        self.log.warning("[warning] %s: %s", title, message)

    def show_error(self, title: str, message: str, **kwargs: Any) -> None:
        self.log.error("[error] %s: %s", title, message)

    def confirm(self, title: str, message: str, default: bool = False, **kwargs: Any) -> bool:
        self.log.info("[confirm->%s] %s: %s", default, title, message)
        return default

    def ask_text(
        self, title: str, prompt: str, initial: str = "", password: bool = False, **kwargs: Any
    ) -> Optional[str]:
        self.log.info("[ask_text->None] %s: %s", title, prompt)
        return None

    def choose(
        self,
        title: str,
        prompt: str,
        options: Sequence[Choice],
        default: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[str]:
        self.log.info("[choose->%s] %s: %s (%d options)", default, title, prompt, len(options))
        return default

    def show_progress(self, report: ProgressReport, **kwargs: Any) -> None:
        # 进度可能每秒几十次，降到 debug 以免污染日志
        self.log.debug("[progress] %s %.1f%% %s", report.title, report.fraction * 100, report.message)

    def close_progress(self, title: Optional[str] = None) -> None:
        self.log.debug("[progress-close] %s", title)

    def set_clipboard(self, text: str) -> bool:
        self.log.debug("[clipboard-refused] %d chars", len(text))
        return False

    def notify(self, message: str, level: str = "info", **kwargs: Any) -> None:
        self.log.info("[notify:%s] %s", level, message)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self._name}>"


class RecordingUIPort(NullUIPort):
    """把每次调用记入 ``calls`` 列表，供单元测试断言"服务问了界面什么"。

    ``answers`` 预置问答结果，避免测试里写回调：
    ``port = RecordingUIPort(confirm=True, text="abc", choice="ocean")``
    """

    def __init__(
        self,
        confirm: Optional[bool] = None,
        text: Optional[str] = None,
        choice: Optional[str] = None,
        available: bool = True,
        clipboard: bool = True,
    ) -> None:
        super().__init__(name="RecordingUIPort")
        self._confirm = confirm
        self._text = text
        self._choice = choice
        self._available = available
        self._clipboard = clipboard
        self.calls: List[Dict[str, Any]] = []

    def _record(self, method: str, **kwargs: Any) -> None:
        self.calls.append({"method": method, **kwargs})

    def is_available(self) -> bool:
        return self._available

    def show_info(self, title: str, message: str, **kwargs: Any) -> None:
        self._record("show_info", title=title, message=message)

    def show_warning(self, title: str, message: str, **kwargs: Any) -> None:
        self._record("show_warning", title=title, message=message)

    def show_error(self, title: str, message: str, **kwargs: Any) -> None:
        self._record("show_error", title=title, message=message)

    def confirm(self, title: str, message: str, default: bool = False, **kwargs: Any) -> bool:
        self._record("confirm", title=title, message=message, default=default)
        return default if self._confirm is None else self._confirm

    def ask_text(
        self, title: str, prompt: str, initial: str = "", password: bool = False, **kwargs: Any
    ) -> Optional[str]:
        self._record("ask_text", title=title, prompt=prompt, initial=initial, password=password)
        return self._text

    def choose(
        self,
        title: str,
        prompt: str,
        options: Sequence[Choice],
        default: Optional[str] = None,
        **kwargs: Any,
    ) -> Optional[str]:
        self._record("choose", title=title, prompt=prompt, options=[o.value for o in options], default=default)
        return self._choice if self._choice is not None else default

    def show_progress(self, report: ProgressReport, **kwargs: Any) -> None:
        self._record("show_progress", report=report)

    def close_progress(self, title: Optional[str] = None) -> None:
        self._record("close_progress", title=title)

    def set_clipboard(self, text: str) -> bool:
        self._record("set_clipboard", text=text)
        return self._clipboard

    def notify(self, message: str, level: str = "info", **kwargs: Any) -> None:
        self._record("notify", message=message, level=level)

    # ─── 断言辅助 ───────────────────────────────────────────

    def methods(self) -> List[str]:
        return [c["method"] for c in self.calls]

    def find(self, method: str) -> List[Dict[str, Any]]:
        return [c for c in self.calls if c["method"] == method]

    def clear(self) -> None:
        self.calls.clear()


__all__ = ["ProgressReport", "Choice", "UIPort", "NullUIPort", "RecordingUIPort"]
