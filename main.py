"""Fusion Minecraft Launcher - 主程序入口
Update Log:
v1.0 - start project
v1.1 - add forge install
v1.2 - add logs module
v1.3 - add ui
v1.4 - add mouse detect
v2.0 - refactor: modular architecture, improved error handling
v3.0 - modern UI with CustomTkinter, multi-threaded operations
v3.1 - perf: lazy imports, deferred heavy initialization
v3.2 - perf: orjson JSON parsing, concurrent file verify, async batch download,
       JVM args optimization (G1GC), GC release after launch, URL rewrite cache
"""

import os
import re
import sys
import threading
import time

import logzero
from logzero import logger

from config import config


def _get_icon_path():
    """获取图标路径（兼容开发环境和 PyInstaller 打包）"""
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, 'icon.ico')


def _create_splash(ctk):
    """创建启动画面 - 屏幕中央展示图标，加载完成后关闭"""
    splash = ctk.CTkToplevel()
    splash.overrideredirect(True)
    splash.attributes('-topmost', True)
    splash.configure(fg_color='#1a1a2e')

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
    icon_path = _get_icon_path()
    if os.path.exists(icon_path):
        try:
            from PIL import Image as PILImage
            icon_img = ctk.CTkImage(PILImage.open(icon_path), size=(128, 128))
            ctk.CTkLabel(splash, image=icon_img, text='').place(relx=0.5, rely=0.38, anchor=ctk.CENTER)
        except Exception:
            ctk.CTkLabel(splash, text='\u26cf', font=ctk.CTkFont(size=64)).place(relx=0.5, rely=0.38, anchor=ctk.CENTER)
    else:
        ctk.CTkLabel(splash, text='\u26cf', font=ctk.CTkFont(size=64)).place(relx=0.5, rely=0.38, anchor=ctk.CENTER)

    # 标题文字
    ctk.CTkLabel(
        splash, text='FMCL', font=ctk.CTkFont(family='Microsoft YaHei', size=20, weight='bold'),
        text_color='#a0a0b0',
    ).place(relx=0.5, rely=0.65, anchor=ctk.CENTER)

    # 加载提示
    ctk.CTkLabel(
        splash, text='Loading...', font=ctk.CTkFont(size=12),
        text_color='#666680',
    ).place(relx=0.5, rely=0.76, anchor=ctk.CENTER)

    return splash


def set_chinese_language():
    """
    启动时自动将 .minecraft/options.txt 中的语言设置改为中文
    查找 lang: 开头的行，将其改为 lang:zh_cn
    """
    options_file = config.minecraft_dir / "options.txt"

    if not options_file.exists():
        logger.info("options.txt 不存在，跳过语言设置")
        return

    try:
        content = options_file.read_text(encoding="utf-8")
        new_content, count = re.subn(r"^lang:.*$", "lang:zh_cn", content, flags=re.MULTILINE)

        if count > 0:
            options_file.write_text(new_content, encoding="utf-8")
            logger.info("已将游戏语言设置为中文 (zh_cn)")
        else:
            # 文件中没有 lang: 行，追加到末尾
            with open(options_file, "a", encoding="utf-8") as f:
                f.write("\nlang:zh_cn")
            logger.info("options.txt 中未找到 lang 配置，已追加 lang:zh_cn")

    except Exception as e:
        logger.error(f"设置游戏语言失败: {e}")


def setup_logging():
    """配置日志系统"""
    logzero.logfile(str(config.log_file))
    logzero.loglevel(config.log_level)
    logger.info("日志系统初始化完成")


def detect_mouse_move():
    """
    鼠标位置检测功能(调试用)
    持续记录鼠标位置到文件
    """
    # 延迟导入 pyautogui，避免启动时 0.08s 的导入开销
    import pyautogui

    pos_file = config.base_dir / "pos.txt"

    try:
        with open(pos_file, "w+") as f:
            logger.info("鼠标检测线程启动")

            while True:
                try:
                    x, y = pyautogui.position()
                    position_str = f"{str(x).rjust(5)} , {str(y).rjust(5)}"

                    f.write(position_str + "\n")
                    f.flush()

                    # 降低刷新频率,避免过度写入
                    time.sleep(0.2)

                except KeyboardInterrupt:
                    logger.info("鼠标检测线程停止")
                    break

    except Exception as e:
        logger.error(f"鼠标检测线程错误: {str(e)}")


def main():
    """主程序入口"""
    try:
        # 配置日志
        setup_logging()

        logger.info("=" * 60)
        logger.info("Fusion Minecraft Launcher v3.2 启动")
        logger.info("=" * 60)

        # 确保目录存在
        config.ensure_directories()

        # 设置游戏语言为中文
        set_chinese_language()

        # ── 延迟导入：只在需要时才加载重量级模块 ──
        # customtkinter (~0.14s) 和 launcher/minecraft_launcher_lib (~0.16s)
        # 延迟导入 launcher 模块，避免模块加载阶段就触发 minecraft_launcher_lib 的导入
        import customtkinter as ctk

        # 设置CustomTkinter主题
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        # 延迟导入 UI 模块
        from ui import ModernApp

        # ── 先创建 UI，让用户尽快看到窗口 ──
        # 传入一个空的 callbacks 字典，稍后在后台线程中替换
        app = ModernApp({})
        app.withdraw()  # 先隐藏主窗口，等启动画面结束后再显示

        # 创建启动画面
        splash = _create_splash(ctk)
        splash_start = time.time()
        _launcher_result = {}  # 线程安全存储 launcher 实例
        _launcher_ready = threading.Event()

        app.set_status("正在初始化启动器核心...", "loading")

        # ── 在后台线程中初始化启动器核心 ──
        # minecraft_launcher_lib (~0.16s) 和 mirror patch 的导入在这里完成
        def _init_launcher():
            from launcher import MinecraftLauncher
            launcher = MinecraftLauncher(config)
            _launcher_result['launcher'] = launcher
            _launcher_ready.set()
            app.after(0, _try_dismiss_splash)

        def _try_dismiss_splash():
            """尝试关闭启动画面：需同时满足 1 秒和加载完成"""
            if not _launcher_ready.is_set():
                return
            elapsed = time.time() - splash_start
            remaining = max(0, 1.0 - elapsed)
            if remaining > 0:
                splash.after(int(remaining * 1000), _dismiss_splash)
            else:
                _dismiss_splash()

        def _dismiss_splash():
            """关闭启动画面，显示主窗口"""
            try:
                splash.destroy()
            except Exception:
                pass
            _on_launcher_ready(_launcher_result['launcher'])

        def _on_launcher_ready(launcher):
            """Launcher 初始化完成回调（主线程执行）"""
            app.deiconify()  # 显示主窗口
            callbacks = launcher.get_callbacks()

            # 更新 UI 回调
            app.callbacks = callbacks

            # 同步镜像源开关状态
            if "get_mirror_enabled" in callbacks:
                app.mirror_var.set(callbacks["get_mirror_enabled"]())

            # 同步最小化开关状态
            if "get_minimize_on_game_launch" in callbacks:
                app.minimize_var.set(callbacks["get_minimize_on_game_launch"]())

            app.set_status("启动器就绪", "success")

            # 启动环境初始化流程
            app._on_app_ready()

            # 启动时自动检查更新（后台静默）
            if config.auto_check_update:
                threading.Thread(target=app._check_update, args=(True,), daemon=True).start()

        # 启动后台初始化线程
        init_thread = threading.Thread(target=_init_launcher, daemon=True)
        init_thread.start()

        # 启动鼠标检测线程(守护线程,主程序退出时自动结束)
        mouse_thread = threading.Thread(target=detect_mouse_move, daemon=True)
        mouse_thread.start()

        app.protocol("WM_DELETE_WINDOW", app.on_closing)
        app.mainloop()

    except KeyboardInterrupt:
        logger.info("用户中断程序")

    except Exception as e:
        logger.error(f"程序异常退出: {str(e)}", exc_info=True)

    finally:
        logger.info("=" * 60)
        logger.info("程序退出")
        logger.info("=" * 60)
        sys.exit(0)


if __name__ == "__main__":
    main()
