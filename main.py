"""Fusion Minecraft Launcher - 主程序入口
Update Log:
v1.0 - start project
v1.1 - add forge install
v1.2 - add logs module
v1.3 - add ui
v2.0 - refactor: modular architecture, improved error handling
v3.0 - modern UI with CustomTkinter, multi-threaded operations
v3.1 - perf: lazy imports, deferred heavy initialization
v3.2 - perf: orjson JSON parsing, concurrent file verify, async batch download,
       JVM args optimization (G1GC), GC release after launch, URL rewrite cache
v3.3 - feat: multi-language support (English, Japanese, Traditional Chinese)
v3.4 - feat: pre-download Minecraft resource pack for faster version installation
"""

import importlib.util
import os
import re
import sys
import threading
import time
from pathlib import Path

import logzero
from logzero import logger

from config import config
from scripts.build_plan import UI_BACKENDS, normalize_backend


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

    logzero.logfile(str(log_file), encoding="utf-8")
    logzero.loglevel(config.log_level)
    logger.info("日志系统初始化完成")

    # 初始化结构化日志路径
    from structured_logger import slog

    structured_log_path = str(config.base_dir / "latest_structured.log")
    slog._log_path = structured_log_path


def _show_startup_error(message: str, base_dir) -> None:
    """启动阶段致命错误提示（UI 尚未初始化，直接弹窗并退出）。

    实现已搬到 ``ui/splash.py``（界面代码不再留在入口文件里）。
    这里用延迟导入而非模块顶层导入：既让 ``main.py -A`` 这类 CLI 模式不加载
    界面模块，也不改变模块导入与日志顺序。
    """
    try:
        from ui.splash import show_startup_error

        show_startup_error(message, base_dir)
    except Exception as e:
        logger.error(f"显示启动错误弹窗失败: {e}")


def _auto_refresh_tokens(account_system):
    try:
        count = account_system.auto_refresh_all_tokens()
        if count > 0:
            logger.info(
                f"\u542f\u52a8\u65f6\u81ea\u52a8\u5237\u65b0\u4e86 {count} \u4e2a\u5fae\u8f6f\u8d26\u53f7\u7684 Token"
            )
    except Exception as e:
        logger.warning(
            f"\u542f\u52a8\u65f6\u81ea\u52a8\u5237\u65b0 Token \u5931\u8d25 (\u4e0d\u5f71\u54cd\u6b63\u5e38\u4f7f\u7528): {e}"
        )


def main():
    """主程序入口"""
    # UI 能力端口（阶段 1 任务 1.3）：主窗口创建后装配，退出路径里 stop()
    ui_port = None
    try:
        # 配置日志
        setup_logging()

        logger.info("=" * 60)
        logger.info("Fusion Minecraft Launcher v3.4 启动")
        logger.info(f"数据目录: {config.base_dir}")
        logger.info("=" * 60)

        # 确保目录存在
        try:
            config.ensure_directories()
        except Exception as e:
            _show_startup_error(f"无法创建必要的目录:\n{e}", config.base_dir)
            return

        # 初始化账号系统并迁移旧配置
        try:
            config.migrate_accounts()
        except Exception as e:
            logger.warning(f"\u8d26\u53f7\u8fc1\u79fb\u5931\u8d25 (\u4e0d\u5f71\u54cd\u542f\u52a8): {e}")

        # 初始化国际化（必须在加载 UI 之前）
        from ui.i18n import init_i18n

        current_lang = init_i18n(getattr(config, "language", None))
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

        from ui.dialogs import set_app_reference

        set_app_reference(app)

        # ── UI 能力端口：核心层/服务层与界面交互的唯一入口（阶段 1 任务 1.3） ──
        # 必须在 UI 主线程创建并 start()：端口用 root.after 轮询消费 worker 线程的请求
        # （绝不让 worker 直接 root.after / 碰 Tk）。界面销毁后请求会被安全丢弃。
        from ui.ports_tk import TkUIPort

        ui_port = TkUIPort(app)
        ui_port.start()

        # ── AppContext：服务注册表 + 共享设施（阶段 1 任务 1.17） ──
        # 在这之前 AppContext 虽然完整，却没有任何生产代码构造它 —— 后果是
        # 界面侧的 `_get_xxx_service(owner)` 永远拿不到上下文，**每个窗口各自 new 一份服务**。
        # 这里补上接线，并采用**懒注册**：服务实例在第一次被取用时才建，
        # 启动期不必为 onnxruntime / pygame / winsdk 之类重依赖付代价。
        # `start_all()` 目前是空操作（13 个服务都没覆盖 Service.start），
        # 但保留它，后续服务要读盘/建连接池时不需要再改启动路径。
        from app.bootstrap import attach, build_context

        context = build_context(config=config, ui=ui_port, scheduler=lambda fn: app.after(0, fn))
        attach(context, app)
        logger.info("AppContext 已建立：注册 %d 个服务（懒实例化）", len(context.names()))
        try:
            context.start_all()
        except Exception as e:  # noqa: BLE001 - 服务启动失败绝不能挡住界面
            logger.error(f"服务启动失败: {e}", exc_info=True)

        app.withdraw()  # 先隐藏主窗口，等启动画面结束后再显示

        logger.info("正在创建启动画面...")
        # 创建启动画面（界面代码已搬到 ui/splash.py）
        from ui.splash import create_splash, show_error_dialog

        splash = create_splash(ctk)
        logger.info("启动画面创建完成")
        splash_start = time.time()
        _launcher_result = {}  # 线程安全存储 launcher 实例
        _launcher_ready = threading.Event()
        _ach_init_done = threading.Event()

        app.set_status("正在初始化启动器核心...", "loading")

        # ── 提前初始化成就系统（仅创建引擎，快速完成） ──
        _ach_engine_result = {}

        def _init_achievements():
            try:
                from achievement_engine import init_achievement_engine

                ach_engine = init_achievement_engine(config.base_dir)
                _ach_engine_result["engine"] = ach_engine
            except Exception as e:
                logger.error(f"成就引擎初始化失败: {e}")
            finally:
                _ach_init_done.set()
                app.after(0, _try_dismiss_splash)

        # ── 在后台线程中初始化启动器核心 ──
        # minecraft_launcher_lib (~0.16s) 和 mirror patch 的导入在这里完成
        def _init_launcher():
            try:
                logger.info("_init_launcher: 1. 正在导入 MinecraftLauncher...")
                from launcher import MinecraftLauncher

                logger.info("_init_launcher: 2. 正在创建 MinecraftLauncher 实例...")
                launcher = MinecraftLauncher(config)
                logger.info("_init_launcher: 3. MinecraftLauncher 创建完成")
                _launcher_result["launcher"] = launcher
                # 把 launcher 也放进 AppContext（服务/界面以后可以用 ctx.try_get("launcher")
                # 取到同一个实例，而不是各自持有一份引用）。失败不影响启动。
                try:
                    context.register_instance("launcher", launcher, replace=True)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"把 launcher 注册进 AppContext 失败: {e}")
                _launcher_ready.set()
                logger.info("_init_launcher: 4. 正在调度 splash 关闭回调...")
                app.after(0, _try_dismiss_splash)
                logger.info("_init_launcher: 5. 初始化完成，后台线程即将退出")
            except Exception as e:
                logger.error(f"_init_launcher: 启动器初始化失败: {e}", exc_info=True)
                _launcher_result["error"] = str(e)
                _launcher_ready.set()
                app.after(0, lambda err=str(e): _show_init_error(app, f"启动器核心初始化失败:\n{err}"))

        def _try_dismiss_splash():
            """尝试关闭启动画面：需同时满足 launcher、achievements 就绪 且 1 秒"""
            if not _launcher_ready.is_set() or not _ach_init_done.is_set():
                # 条件不满足时重新调度自己，直到超时
                elapsed = time.time() - splash_start
                if elapsed < 30:
                    splash.after(200, _try_dismiss_splash)
                else:
                    logger.warning("_try_dismiss_splash: 等待超时 30 秒，强制关闭启动画面")
                    _dismiss_splash()
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
            if "launcher" not in _launcher_result or _launcher_result["launcher"] is None:
                # 启动器初始化失败，显示主窗口但状态栏提示错误
                app.deiconify()
                error_msg = _launcher_result.get("error", "未知错误")
                app.set_status(f"启动器初始化失败: {error_msg}", "error")
                logger.error(f"启动器初始化失败，无法继续: {error_msg}")
                return
            _on_launcher_ready(_launcher_result["launcher"])
            threading.Thread(target=_post_init_achievements, daemon=True).start()

        def _show_init_error(app_ref, message):
            """显示初始化失败错误弹窗"""
            try:
                splash.destroy()
            except Exception:
                pass
            app_ref.deiconify()
            app_ref.set_status(f"启动器初始化失败: {message}", "error")
            show_error_dialog("启动失败", f"{message}\n\n请检查日志文件获取详细信息。")

        def _post_init_achievements():
            """启动后后台执行：成就同步 + 签到（不阻塞 UI）"""
            from achievement_engine import get_achievement_engine

            ach_engine = get_achievement_engine()
            if not ach_engine:
                return
            token = config.jdz_token
            if token:
                try:
                    from achievement_sync import run_sync

                    app.after(0, lambda: app.set_status("正在同步成就云存档...", "loading"))
                    run_sync(token, ach_engine._db_path, engine=ach_engine)
                    app.after(0, lambda: app.set_status("成就云存档同步完成", "success"))
                except Exception as e:
                    logger.error(f"成就同步异常: {e}")
                    app.after(0, lambda err=str(e): app.set_status(f"成就云存档同步失败: {err}", "warning"))
            try:
                result = ach_engine.checkin()
                if result and result.get("success"):
                    app.after(0, lambda: app.set_status(f"签到成功! 连续 {result.get('streak', 0)} 天", "success"))
            except Exception as e:
                logger.error(f"签到异常: {e}")
                app.after(0, lambda err=str(e): app.set_status(f"每日签到失败: {err}", "warning"))
            app.after(0, app._refresh_achievements)

        def _on_launcher_ready(launcher):
            """Launcher 初始化完成回调（主线程执行）"""
            app.deiconify()  # 显示主窗口

            # ── UI 能力端口注入（阶段 1 任务 1.3） ──
            # 核心层不再 import tkinter，需要问答/进度/剪贴板时走这个端口。
            # 账号模块的注入放在这里而不是更早：launcher 包（minecraft_launcher_lib）
            # 是刻意延迟到后台线程再导入的，提前 import 会破坏原有启动顺序。
            try:
                launcher.set_ui_port(ui_port)
                logger.info("UI 能力端口已注入启动器核心")
            except AttributeError:
                logger.warning("launcher 尚未提供 set_ui_port()，跳过核心注入（core.py 侧改动未合入）")
            try:
                from launcher.account import set_ui_port as set_account_ui_port

                set_account_ui_port(ui_port)
                logger.info("UI 能力端口已注入账号模块")
            except Exception as e:
                logger.warning(f"账号模块 UI 端口注入失败: {e}")

            callbacks = launcher.get_callbacks()

            # 注册配置错误回调（弹出错误弹窗）
            def _config_error_handler(title, message):
                app.after(0, lambda: show_error_dialog(title, message, parent=app))

            config.set_error_callback(_config_error_handler)

            # 注册安全存储错误回调
            try:
                from secure_storage import set_error_callback as set_sec_error_cb

                set_sec_error_cb(_config_error_handler)
            except ImportError:
                pass

            # 更新 UI 回调
            app.callbacks = callbacks

            # ── 恢复音乐播放状态（歌单、歌曲位置等） ──
            if hasattr(app, "_music_on_launcher_ready"):
                app.after(100, app._music_on_launcher_ready)

            # ── 插件系统初始化 ──
            try:
                from plugin_manager import PluginManager

                plugins_root = config.base_dir / "plugins"
                plugin_manager = PluginManager(plugins_root)
                app.callbacks["get_plugin_manager"] = lambda pm=plugin_manager: pm
                # 将 plugin_manager 注入 launcher 以便发射钩子
                launcher._plugin_manager = plugin_manager

                # 设置通知回调：将插件通知显示到主界面状态栏
                def _plugin_notify(plugin_id, title, message, level):
                    app.set_status(f"[Plugin:{plugin_id}] {title}: {message}", level)

                plugin_manager.set_notify_callback(_plugin_notify)
                # 扫描已安装插件（内部自动恢复持久化状态并启用）
                discovered = plugin_manager.scan()
                if discovered:
                    logger.info(f"发现 {len(discovered)} 个插件: {discovered}")
                # 触发启动完成钩子
                from plugin_manager.base import HookPoint

                plugin_manager.emit(HookPoint.APP_STARTUP)

                # 注册插件标签页
                tab_registrations = plugin_manager.emit(HookPoint.UI_TAB_REGISTER)
                if tab_registrations:
                    _build_plugin_tabs(app, plugin_manager, tab_registrations)
            except Exception as e:
                logger.warning(f"插件系统初始化失败: {e}")
                app.set_status(f"插件系统加载失败: {e}", "warning")

            # ── 成就系统 wiring（引擎已在 splash 期间初始化） ──
            from achievement_engine import get_achievement_engine

            ach_engine = get_achievement_engine()
            if ach_engine:
                ach_engine.register_unlock_callback(app._on_achievement_unlock)

            # ── 账号系统注入 ──
            try:
                from launcher.account import get_account_system

                account_system = get_account_system()
                if account_system:
                    launcher.set_account_system(account_system)
                    logger.info("账号系统已注入启动器")
                    app._update_sidebar_account()
                    threading.Thread(target=lambda: _auto_refresh_tokens(account_system), daemon=True).start()
            except Exception as e:
                logger.warning(f"账号系统注入失败: {e}")
                app.set_status(f"账号系统加载失败: {e}", "warning")

            # 重新应用保存的主题颜色（UI 创建时用的是默认主题，需要刷新）
            if hasattr(app, "_reapply_theme"):
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
            if hasattr(app, "backup_auto_launch_var"):
                app.backup_auto_launch_var.set(config.backup_auto_launch)
            if hasattr(app, "backup_auto_exit_var"):
                app.backup_auto_exit_var.set(config.backup_auto_exit)

            app.set_status("启动器就绪", "success")

            # 启动顺序：协议同意 → 公告 → 预下载
            app._on_app_ready(on_agreement_complete=lambda: _show_notice_then_predownload(app, ui_port))

            # 更新 AGENT 助手的回调和 Token
            if hasattr(app, "_update_agent_callbacks"):
                app._update_agent_callbacks()

            # 启动时自动检查更新（后台静默）
            if config.auto_check_update:
                threading.Thread(target=app._check_update, args=(True,), daemon=True).start()

        # 启动后台初始化线程
        init_thread = threading.Thread(target=_init_launcher, daemon=True)
        init_thread.start()

        # 启动成就系统初始化线程（与启动器并行，包含云存档同步）
        ach_thread = threading.Thread(target=_init_achievements, daemon=True)
        ach_thread.start()

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
        # 停止 UI 能力端口：取消 after 轮询、放行等待中的请求（界面可能已销毁）
        if ui_port is not None:
            try:
                ui_port.stop()
            except Exception as e:
                logger.warning(f"UI 能力端口停止失败: {e}")
        # 逆序停服务并关闭任务池（阶段 1 任务 1.17）。任何服务的异常都不得打断退出流程，
        # 所以 stop_all 内部已经逐个兜住异常。
        try:
            context.stop_all()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"服务停止失败: {e}")
        # 关闭结构化日志
        try:
            from structured_logger import slog

            slog.close()
        except Exception:
            pass
        sys.exit(0)


def _show_notice_then_predownload(app, ui_port):
    """公告展示 → 确认后预下载检查"""

    def _do_fetch():
        from ui.dialogs import fetch_notice, show_notice_dialog

        content = fetch_notice()
        if content:
            app.after(0, lambda: show_notice_dialog(app, content, on_dismiss=lambda: _do_predownload_check(app, ui_port)))
        else:
            app.after(0, lambda: _do_predownload_check(app, ui_port))

    threading.Thread(target=_do_fetch, daemon=True).start()


def _do_predownload_check(app, ui_port):
    """预下载检查（界面交互全部通过 UI 能力端口）"""
    from launcher.predownload import run_predownload_check
    from ui.i18n import _

    run_predownload_check(ui_port, config.minecraft_dir, _)
    app.lift()
    app.focus_force()


def _parse_cli_args():
    """解析命令行参数，支持以下 CLI 模式:

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
            username = None
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
                    _print_cli_error("-pwd 参数已弃用（密码在进程列表中可见，不安全）")
                    _print_cli_error("请使用交互方式输入密码（程序会提示输入）")
                    if j + 1 < len(args):
                        j += 2  # 跳过密码值，不再读取
                    else:
                        j += 1
                else:
                    break
            if not username:
                _print_cli_error("用法: python main.py login -name <用户名>")
                sys.exit(1)
            return "login", (username, None)  # password 始终为 None，由 run_login 通过 getpass 获取
        if arg in ("-A", "-agent", "--agent"):
            if i + 1 < len(args) and not args[i + 1].startswith("-"):
                return "agent", args[i + 1]
            return "agent", None
        i += 1
    return None, None


def _print_cli_error(msg: str):
    print(f"\033[91m❌  {msg}\033[0m", flush=True)


def _attach_console():
    """尝试附加到父进程控制台（打包后 GUI 子系统的 exe 需要此步骤）"""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        kernel32 = ctypes.windll.kernel32
        if not kernel32.AttachConsole(-1):
            kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8")
        sys.stderr = open("CONOUT$", "w", encoding="utf-8")
        sys.stdin = open("CONIN$", "r", encoding="utf-8")
    except Exception:
        pass


def run_agent_cli_mode(instruction=None):
    """以 CLI Agent 模式运行（无 GUI 依赖）"""
    _attach_console()
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


# ── 插件 UI 构建辅助函数 ──


def _build_plugin_tabs(app, plugin_manager, registrations):
    """根据 UI_TAB_REGISTER 钩子结果，在 tabview 中创建插件标签页"""
    import customtkinter as ctk
    from logzero import logger

    for plugin_id, tab_info in registrations:
        if not isinstance(tab_info, dict):
            continue
        tab_text = tab_info.get("text", plugin_id)
        instance = plugin_manager._instances.get(plugin_id)
        if instance is None:
            logger.warning(f"插件 {plugin_id} 注册了标签页但无实例")
            continue
        try:
            tab_frame = app.tabview.add(tab_text)
            tab_frame.configure(fg_color="transparent")
            tab_ui = instance.get_tab_ui(tab_frame)
            if tab_ui is not None:
                tab_ui.pack(fill=ctk.BOTH, expand=True)
            logger.info(f"插件标签页已注册: {plugin_id} -> {tab_text}")
        except Exception as e:
            logger.warning(f"构建插件标签页失败 ({plugin_id}): {e}")


# ── 界面后端分派（阶段 3 前置）──


def _parse_ui_arg(argv):
    """从命令行里取 `--ui <后端>`（支持 `--ui=qml`）。没写就返回 None。

    刻意**不用** argparse：入口现有的 CLI 语义是手写解析（`_parse_cli_args`），
    多引一个解析器会让"两套 CLI 语义"出现分叉（迁移红线 2）。
    """
    for index, arg in enumerate(argv):
        if arg == "--ui" and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--ui="):
            return arg.split("=", 1)[1]
    return None


def _resolve_ui_backend(argv=None):
    """决定这次启动用哪套界面：命令行 > 配置 > 默认。返回 `UI_BACKENDS` 里的值。

    默认永远是 `tk`（`config.DEFAULT_UI_BACKEND`）：阶段 3 期间 QML 页面还是占位壳，
    提前切默认等于把空页面发给用户。阶段 4.1 才改默认值。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    requested = _parse_ui_arg(argv)
    if requested is not None:
        backend = normalize_backend(requested)
        if backend != requested.strip().lower():
            logger.warning(f"命令行指定的界面后端 {requested!r} 不是合法值（{'/'.join(UI_BACKENDS)}），已回落到 {backend}")
        return backend
    return normalize_backend(getattr(config, "ui_backend", None))


def _probe_qml_backend():
    """探测 QML 后端能否启动，返回 `(可用, 不可用的原因)`。**只探测，不装配**。

    为什么是"先探测"而不是"先跑、崩了再回退"：`main_qml.main()` 自己的启动期异常路径会弹
    致命错误窗并返回 1（`main_qml.show_fatal_error`）。若在外层再回退到 Tk，用户会先看到
    "QML 启动失败"再看到一个经典界面 —— 两个界面同时存在，原因还说不清。所以回退只发生在
    **装配之前**就能判定"根本起不来"的情况（没装 Qt / 没有 FluentUI 模块 / 没有 QML 入口）。

    探测全部是"查文件 + 查模块是否存在"，不加载 Qt、不建引擎，因此可以放心在入口调。
    """
    if importlib.util.find_spec("PySide6") is None:
        return False, "没装 PySide6（需要 pyside6-essentials 与 pyside6-addons）"
    try:
        import main_qml
    except Exception as exc:  # noqa: BLE001 - 任何导入期异常都算"起不来"
        return False, f"导入 main_qml 失败：{type(exc).__name__}: {exc}"
    module_dir = main_qml.qml_import_path()
    if not Path(module_dir).is_dir():
        return False, f"FluentUI QML 模块目录不存在：{module_dir}"
    entry = main_qml.app_qml_path("App.qml")
    if not Path(entry).is_file():
        return False, f"QML 根组件不存在：{entry}"
    return True, ""


def _notify_ui_fallback(reason):
    """用户**显式**要求 qml 却回退了 Tk 时，把原因说给他看。

    只在命令行显式指定时调用：配置文件里的 qml 回退只记日志 —— 否则一台装不上 Qt 的机器
    每次启动都弹一次，用户除了关掉什么也做不了。

    弹窗本身走 `ui/splash.py::show_error_dialog`（入口不得内联 Tk 界面代码 —— 分层闸门
    `scripts/check_services_purity.py` 的"入口文件"那一项会拦；启动期弹窗本来就属于 ui/ 层）。
    """
    try:
        from ui.splash import show_error_dialog

        show_error_dialog("FMCL 界面回退", f"QML 界面无法启动，已回退到经典界面：\n\n{reason}")
    except Exception as exc:  # noqa: BLE001 - 连提示都弹不出来时必须留日志
        logger.warning(f"回退提示无法显示（{type(exc).__name__}: {exc}）")


def _dispatch_ui(argv):
    """决定这次启动起哪套界面，返回 `(动作, 原因)`。

    动作只有三种值，**纯决策**（不弹窗、不装配、不起事件循环），所以入口分派是可测的：

    * `"qml"`：用 QML 界面起（探测通过）；
    * `"fallback"`：要求了 qml 但根本起不来 → 回退经典界面，原因在第二个元素里；
    * `"tk"`：本来就该用经典界面。

    回退只发生在**装配之前**能判定"根本起不来"的情况；QML 装配期自己的异常仍由
    `main_qml.main()` 的致命错误窗负责（见 `_probe_qml_backend` 的说明）。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    if _resolve_ui_backend(argv) != "qml":
        return "tk", ""
    usable, why = _probe_qml_backend()
    if usable:
        return "qml", ""
    logger.warning(f"QML 后端不可用，回退经典界面：{why}")
    return "fallback", why


def _run_qml_backend(argv):
    """装配并进入 QML 事件循环，返回退出码。"""
    import main_qml

    logger.info(f"界面后端：qml（{main_qml.app_qml_path('App.qml')}）")
    return int(main_qml.main(list(argv), run_loop=True))


if __name__ == "__main__":
    mode, payload = _parse_cli_args()
    if mode == "login" and isinstance(payload, tuple):
        username, password = payload
        run_login_mode(username, password)
    elif mode == "agent":
        run_agent_cli_mode(payload)
    else:
        _argv = sys.argv[1:]
        _action, _why = _dispatch_ui(_argv)
        if _action == "qml":
            sys.exit(_run_qml_backend(_argv))
        if _action == "fallback" and _parse_ui_arg(_argv) is not None:
            # 只有"用户显式要求 qml"才弹提示：配置文件里的 qml 回退只记日志，
            # 否则一台装不上 Qt 的机器每次启动都弹一次。
            _notify_ui_fallback(_why)
        main()
