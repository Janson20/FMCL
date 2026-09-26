"""启动画面与启动期错误提示（从 ``main.py`` 搬来的界面代码）。

这些代码本来就是 UI 层的东西，只是原先放在入口文件里；搬到这里之后
``main.py`` 不再需要 ``import tkinter``，启动流程、日志顺序与 splash
的重试/超时逻辑完全不变。

对外提供的可调用对象（与 main.py 里原来的私有函数一一对应）：

- ``get_icon_path()``       <- ``main._get_icon_path()``
- ``create_splash(ctk)``    <- ``main._create_splash(ctk)``
- ``show_startup_error()``  <- ``main._show_startup_error()``
- ``show_error_dialog()``   <- ``main`` 里两处 ``tkinter.messagebox.showerror`` 调用

为什么这里仍然使用原生 ``tkinter.messagebox``：启动期（UI 尚未初始化）与
主窗口错误提示原本就是原生错误框，保持原样即"零可见行为变化"；本文件属于
UI 层，使用 tkinter 不违反分层约束（``scripts/check_services_purity.py``
只扫描 services / core 层）。
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

from logzero import logger


def get_icon_path() -> str:
    """获取图标路径（兼容开发环境与 PyInstaller 打包）。

    注意：本函数原先是 ``main.py`` 的模块级函数，``__file__`` 就是仓库根目录；
    搬到 ``ui/`` 之后基准目录要再向上一层，否则会算成 ``ui/icon.ico`` 而找不到
    图标（启动画面会退化成字符图标）。打包环境下仍然优先使用 ``sys._MEIPASS``，
    与原来完全一致。
    """
    base_path = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, "icon.ico")


def create_splash(ctk: Any) -> Any:
    """创建启动画面 - 屏幕中央展示图标，加载完成后关闭。

    函数体与 ``main._create_splash`` 完全一致（含尺寸、居中算法、图标回退字符），
    只把函数名去掉下划线前缀以便对外调用。
    """
    from ui import FONT_FAMILY

    splash = ctk.CTkToplevel()
    splash.overrideredirect(True)
    splash.attributes("-topmost", True)
    splash.configure(fg_color="#1a1a2e")

    # 窗口尺寸
    w, h = 320, 320
    splash.geometry(f"{w}x{h}")
    splash.update_idletasks()  # 让窗口实际渲染后再计算居中位置

    # 始终居中于屏幕（兼容多显示器和 DPI 缩放）
    sw = splash.winfo_screenwidth()
    sh = splash.winfo_screenheight()
    x = (sw - w) // 2
    y = (sh - h) // 2
    splash.geometry(f"+{x}+{y}")

    # 加载图标
    icon_path = get_icon_path()
    if os.path.exists(icon_path):
        try:
            from PIL import Image as PILImage

            icon_img = ctk.CTkImage(PILImage.open(icon_path), size=(128, 128))
            ctk.CTkLabel(splash, image=icon_img, text="").place(relx=0.5, rely=0.38, anchor=ctk.CENTER)
        except Exception:
            ctk.CTkLabel(splash, text="\u26cf", font=ctk.CTkFont(size=64)).place(relx=0.5, rely=0.38, anchor=ctk.CENTER)
    else:
        ctk.CTkLabel(splash, text="\u26cf", font=ctk.CTkFont(size=64)).place(relx=0.5, rely=0.38, anchor=ctk.CENTER)

    # 标题文字
    ctk.CTkLabel(
        splash, text="FMCL", font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight="bold"), text_color="#a0a0b0"
    ).place(relx=0.5, rely=0.65, anchor=ctk.CENTER)

    # 加载提示
    ctk.CTkLabel(splash, text="Loading...", font=ctk.CTkFont(size=12), text_color="#666680").place(
        relx=0.5, rely=0.76, anchor=ctk.CENTER
    )

    return splash


def show_startup_error(message: str, base_dir: Any) -> None:
    """启动阶段致命错误提示（UI 尚未初始化，直接弹窗并退出）。

    等价于原来的 ``main._show_startup_error``：文案、标题、异常吞掉的行为都不变。
    """
    try:
        import tkinter.messagebox

        tkinter.messagebox.showerror(
            "FMCL 启动失败",
            f"{message}\n\n数据目录: {base_dir}\n\n请检查磁盘空间与目录权限，或重新安装启动器。",
        )
    except Exception:
        pass


def show_error_dialog(title: str, message: str, parent: Optional[Any] = None) -> None:
    """原生错误框，替换 ``main.py`` 里的 ``tkinter.messagebox.showerror`` 调用。

    Args:
        title: 弹窗标题。
        message: 正文（调用方负责拼好换行与前缀，本函数不改文案）。
        parent: 父窗口；为 None 时与原来 ``_show_init_error`` 的行为一致（无父窗口）。

    调用方若在工作线程里，需要自己切回主线程（原来的两处调用分别在主线程
    与 ``app.after(0, ...)`` 中执行）。
    """
    try:
        import tkinter.messagebox

        if parent is not None:
            tkinter.messagebox.showerror(title, message, parent=parent)
        else:
            tkinter.messagebox.showerror(title, message)
    except Exception as e:  # noqa: BLE001 - 弹窗失败不能连累调用方
        logger.warning(f"显示错误弹窗失败 ({title}): {e}")


__all__ = ["get_icon_path", "create_splash", "show_startup_error", "show_error_dialog"]
