"""日志文本框的写入辅助：统一限制保留行数。

阶段 1.22 修正（D-86）。原实现里**四个**日志文本框都只 append、从不删除：

- ``ModernAppBase.log_text``（启动器日志面板）
- ``ModernAppBase._agent_log_text``（AGENT 日志）
- ``ServerTabMixin.server_log_text``（服务器控制台）
- ``OnlineTabMixin._online_log_text``（联机日志）

服务器挂机或 AGENT 常开时，Tk 文本控件会随行数持续增长（每行都是一个
display line 对象，远比字符串本身重）。这里统一按"最多保留最近 N 行"写入。

设计约束：

- **不依赖 customtkinter**：只用到 ``insert`` / ``index`` / ``delete`` / ``see``，
  ``tkinter.Text`` 与 ``ctk.CTkTextbox`` 都满足（``ctk.END`` 就是 ``"end"``），
  这样阶段 2 换成 QML 时这个模块的语义可以直接对照迁移。
- **永不抛异常**：日志面板属于"尽力而为"的展示，丢一行日志也比打断界面流程好。
  控件已销毁、不是文本控件、被 Tk 禁用等情况下静默降级并记一条 debug 日志。

关于行数口径（**实测踩过的坑**）：Tk 的 ``Text`` 在末尾总有一个用户无法删除的
换行，因此 ``index("end-1c")`` 的行号 = **内容行数 + 1**。本模块对外统一使用
"内容行数"（即已写入的行数），并在内部做这个 ±1 换算 —— 否则裁剪会多删一行。
这个结论不是推导出来的，是用真实 ``CTkTextbox`` 写入 3 倍上限的行数测出来的。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: 单个日志文本框保留的最大行数。
#:
#: 取值权衡：太小会丢掉用户回看的上下文（崩溃排查常要往回翻），
#: 太大则失去限制的意义。5000 行 × 约 100 字符 ≈ 0.5 MB，对常驻内存可忽略。
LOG_VIEW_MAX_LINES: int = 5000

#: ``_server_log_lines``（服务器控制台的 Python 侧缓冲）保留的最大行数。
#: 崩溃报告只取最后 200 行（``ui/app_crash.py`` 的 ``[-200:]``），
#: 所以 5000 行足以覆盖需求，同时避免服务器挂机数天时列表无限增长。
LOG_BUFFER_MAX_LINES: int = 5000

#: ``index("end-1c")`` 相对"内容行数"的固定偏移（见模块 docstring）
_END_LINE_OFFSET: int = 1


def line_count(text_widget: Any) -> int:
    """返回文本框的**内容行数**（已写入的行数）；控件不可用时返回 -1。"""
    try:
        raw = int(str(text_widget.index("end-1c")).split(".")[0])
    except Exception:  # noqa: BLE001 - 控件已销毁 / 非文本控件
        return -1
    return max(0, raw - _END_LINE_OFFSET)


def append_line(text_widget: Any, message: str, max_lines: int = LOG_VIEW_MAX_LINES) -> int:
    """向文本框追加一行，并裁剪超出 ``max_lines`` 的最旧行。

    调用方负责先把控件切到可写状态（Tk 的 Text 在 ``DISABLED`` 下改不了内容）。

    Returns:
        实际删除的行数（0 表示未触发裁剪；-1 表示写入本身失败）。
    """
    try:
        text_widget.insert("end", message + "\n")
    except Exception as e:  # noqa: BLE001 - 控件已销毁时不能连累调用方
        logger.debug("追加日志失败（控件不可用）: %s", e)
        return -1

    removed = 0
    if max_lines > 0:
        total = line_count(text_widget)
        if total > max_lines:
            removed = total - max_lines
            try:
                # delete("1.0", "K.0") 删掉第 1..K-1 行，所以删 removed 行要传 K=removed+1
                text_widget.delete("1.0", f"{removed + 1}.0")
            except Exception as e:  # noqa: BLE001 - 裁剪是尽力而为
                logger.debug("裁剪日志行失败: %s", e)
                removed = 0

    try:
        text_widget.see("end")
    except Exception:  # noqa: BLE001
        pass
    return removed


__all__ = ["LOG_VIEW_MAX_LINES", "LOG_BUFFER_MAX_LINES", "append_line", "line_count"]
