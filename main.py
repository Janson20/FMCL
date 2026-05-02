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
v3.3 - feat: multi-language support (English, Japanese, Traditional Chinese)
v3.4 - feat: pre-download Minecraft resource pack for faster version installation
"""

import os
import re
import sys
import threading
import time
from pathlib import Path

import logzero
from logzero import logger

from config import config


def _get_icon_path():
    """获取图标路径（兼容开发环境和 PyInstaller 打包）"""
    base_path = getattr(sys, '_MEIPASS', os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_path, 'icon.ico')


def _create_splash(ctk):
    """创建启动画面 - 屏幕中央展示图标，加载完成后关闭"""
    from ui import FONT_FAMILY

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
        splash, text='FMCL', font=ctk.CTkFont(family=FONT_FAMILY, size=20, weight='bold'),
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
    """配置日志系统，如果默认日志目录不可写则回退到用户目录"""
    log_file = config.log_file
    log_dir = log_file.parent

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        # 测试是否可写
        test_file = log_dir / ".fmcl_write_test"
        test_file.touch()
        test_file.unlink()
    except (PermissionError, OSError):
        # 回退到 ~/.fmcl/latest.log
        fallback_dir = Path.home() / ".fmcl"
        fallback_dir.mkdir(parents=True, exist_ok=True)
        log_file = fallback_dir / "latest.log"
        logger.warning(f"日志目录 {log_dir} 不可写，回退到 {log_file}")

    logzero.logfile(str(log_file))
    logzero.loglevel(config.log_level)
    logger.info("日志系统初始化完成")

    # 初始化结构化日志路径
    from structured_logger import slog
    structured_log_path = str(config.base_dir / "latest_structured.log")
    slog._log_path = structured_log_path


def detect_mouse_move():
    """
    鼠标位置检测功能(调试用)
    持续记录鼠标位置到文件
    仅在有图形环境时启用
    """
    # 检测是否有 X11 显示环境（Linux 无头环境如 WSL/服务器）
    display = os.environ.get("DISPLAY")
    wayland = os.environ.get("WAYLAND_DISPLAY")
    if not display and not wayland:
        logger.debug("无图形环境，跳过鼠标检测线程")
        return

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
        logger.info("Fusion Minecraft Launcher v3.4 启动")
        logger.info("=" * 60)

        # 确保目录存在
        config.ensure_directories()

        # 初始化国际化（必须在加载 UI 之前）
        from ui.i18n import init_i18n
        current_lang = init_i18n(getattr(config, 'language', None))
        logger.info(f"界面语言: {current_lang}")

        # 设置游戏语言为中文
        set_chinese_language()

        logger.info("正在加载 customtkinter...")
        # ── 延迟导入：只在需要时才加载重量级模块 ──
        # customtkinter (~0.14s) 和 launcher/minecraft_launcher_lib (~0.16s)
        # 延迟导入 launcher 模块，避免模块加载阶段就触发 minecraft_launcher_lib 的导入
        import customtkinter as ctk
        logger.info("customtkinter 加载完成")

        # 设置CustomTkinter主题
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        logger.info("正在加载 UI 模块...")
        # 延迟导入 UI 模块
        from ui import ModernApp
        logger.info("ModernApp 导入完成")

        logger.info("正在创建主窗口...")
        # ── 先创建 UI，让用户尽快看到窗口 ──
        # 传入一个空的 callbacks 字典，稍后在后台线程中替换
        app = ModernApp({})
        logger.info("主窗口创建完成")
        app.withdraw()  # 先隐藏主窗口，等启动画面结束后再显示

        logger.info("正在创建启动画面...")
        # 创建启动画面
        splash = _create_splash(ctk)
        logger.info("启动画面创建完成")
        splash_start = time.time()
        _launcher_result = {}  # 线程安全存储 launcher 实例
        _launcher_ready = threading.Event()

        app.set_status("正在初始化启动器核心...", "loading")

        # ── 在后台线程中初始化启动器核心 ──
        # minecraft_launcher_lib (~0.16s) 和 mirror patch 的导入在这里完成
        def _init_launcher():
            logger.info("_init_launcher: 1. 正在导入 MinecraftLauncher...")
            from launcher import MinecraftLauncher
            logger.info("_init_launcher: 2. 正在创建 MinecraftLauncher 实例...")
            launcher = MinecraftLauncher(config)
            logger.info("_init_launcher: 3. MinecraftLauncher 创建完成")
            _launcher_result['launcher'] = launcher
            _launcher_ready.set()
            logger.info("_init_launcher: 4. 正在调度 splash 关闭回调...")
            app.after(0, _try_dismiss_splash)
            logger.info("_init_launcher: 5. 初始化完成，后台线程即将退出")

        def _try_dismiss_splash():
            """尝试关闭启动画面：需同时满足 1 秒和加载完成"""
            logger.info("_try_dismiss_splash: 被调用")
            if not _launcher_ready.is_set():
                logger.info("_try_dismiss_splash: launcher 尚未就绪，返回")
                return
            elapsed = time.time() - splash_start
            remaining = max(0, 1.0 - elapsed)
            logger.info(f"_try_dismiss_splash: 已耗时 {elapsed:.2f}秒，剩余 {remaining:.2f}秒")
            if remaining > 0:
                logger.info("_try_dismiss_splash: 等待剩余时间后调用 _dismiss_splash")
                splash.after(int(remaining * 1000), _dismiss_splash)
            else:
                logger.info("_try_dismiss_splash: 立即调用 _dismiss_splash")
                _dismiss_splash()

        def _dismiss_splash():
            """关闭启动画面，显示主窗口"""
            logger.info("_dismiss_splash: 开始关闭启动画面")
            try:
                splash.destroy()
                logger.info("_dismiss_splash: splash.destroy() 成功")
            except Exception as e:
                logger.error(f"_dismiss_splash: splash.destroy() 失败: {e}")
            _on_launcher_ready(_launcher_result['launcher'])

        def _on_launcher_ready(launcher):
            """Launcher 初始化完成回调（主线程执行）"""
            app.deiconify()  # 显示主窗口
            callbacks = launcher.get_callbacks()

            # 更新 UI 回调
            app.callbacks = callbacks

            # 重新应用保存的主题颜色（UI 创建时用的是默认主题，需要刷新）
            if hasattr(app, '_reapply_theme'):
                app._reapply_theme()

            # 连接安装进度回调，使右下角进度条能实时反映安装进度
            launcher.on_progress = app.update_progress

            # 同步镜像源开关状态
            if "get_mirror_enabled" in callbacks:
                app.mirror_var.set(callbacks["get_mirror_enabled"]())

            # 同步最小化开关状态
            if "get_minimize_on_game_launch" in callbacks:
                app.minimize_var.set(callbacks["get_minimize_on_game_launch"]())

            # 同步备份自动备份开关状态
            if hasattr(app, 'backup_auto_launch_var'):
                app.backup_auto_launch_var.set(config.backup_auto_launch)
            if hasattr(app, 'backup_auto_exit_var'):
                app.backup_auto_exit_var.set(config.backup_auto_exit)

            app.set_status("启动器就绪", "success")

            # 预下载检查（在窗口显示后延迟执行，确保主窗口已渲染）
            def _do_predownload_check():
                from ui.i18n import _
                from launcher.predownload import run_predownload_check
                run_predownload_check(app, config.minecraft_dir, _)
                app.lift()
                app.focus_force()

            app.after(200, _do_predownload_check)

            # 启动环境初始化流程
            app._on_app_ready()

            # 更新 AGENT 助手的回调和 Token
            if hasattr(app, '_update_agent_callbacks'):
                app._update_agent_callbacks()

            # 启动时自动检查更新（后台静默）
            if config.auto_check_update:
                threading.Thread(target=app._check_update, args=(True,), daemon=True).start()

            # 启动时获取公告（后台静默）
            _fetch_and_show_notice(app)

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
        # 关闭结构化日志
        try:
            from structured_logger import slog
            slog.close()
        except Exception:
            pass
        sys.exit(0)


def _fetch_and_show_notice(app):
    """获取并显示公告（后台线程）"""
    def _do_fetch():
        from ui.dialogs import fetch_notice, show_notice_dialog
        content = fetch_notice()
        if content:
            app.after(0, lambda: show_notice_dialog(app, content))
    threading.Thread(target=_do_fetch, daemon=True).start()

def _parse_cli_args():
    """解析命令行参数，支持以下 CLI 模式:

      python main.py login -name <username> -pwd <password>
      python main.py login -name <username>
      python main.py -agent <指令>
      python main.py -A <指令>
      python main.py -A          (交互模式)

    Returns:
        ("agent", instruction) | ("login", (username, password)) | (None, None)
    """
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "login":
            username, password = None, None
            j = i + 1
            while j < len(args):
                if args[j] in ("-name", "--name"):
                    if j + 1 < len(args):
                        username = args[j + 1]
                        j += 2
                    else:
                        _print_cli_error("缺少用户名参数")
                        sys.exit(1)
                elif args[j] in ("-pwd", "--pwd", "-password", "--password"):
                    if j + 1 < len(args):
                        password = args[j + 1]
                        j += 2
                    else:
                        _print_cli_error("缺少密码参数")
                        sys.exit(1)
                else:
                    break
            if not username:
                _print_cli_error("用法: python main.py login -name <用户名> -pwd <密码>")
                sys.exit(1)
            return "login", (username, password)
        if arg in ("-A", "-agent", "--agent"):
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                return "agent", args[i + 1]
            return "agent", None
        i += 1
    return None, None


def _print_cli_error(msg: str):
    print(f"\033[91m❌  {msg}\033[0m", flush=True)


def run_agent_cli_mode(instruction=None):
    """以 CLI Agent 模式运行（无 GUI 依赖）"""
    from cli_agent import run_agent_cli
    try:
        run_agent_cli(instruction=instruction)
    except Exception as e:
        logger.error(f"Agent CLI 异常退出: {e}", exc_info=True)
    finally:
        sys.exit(0)


def run_login_mode(username: str, password: str | None):
    """以 CLI 模式登录净读 AI"""
    from cli_agent import run_login
    try:
        run_login(username, password)
    except Exception as e:
        logger.error(f"登录异常: {e}", exc_info=True)
    finally:
        sys.exit(0)


if __name__ == "__main__":
    mode, payload = _parse_cli_args()
    if mode == "login" and isinstance(payload, tuple):
        username, password = payload
        run_login_mode(username, password)
    elif mode == "agent":
        run_agent_cli_mode(payload)
    else:
        main()
