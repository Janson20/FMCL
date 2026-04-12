"""Minecraft Launcher - 主程序入口
Update Log:
v1.0 - start project
v1.1 - add forge install
v1.2 - add logs module
v1.3 - add ui
v1.4 - add mouse detect
v2.0 - refactor: modular architecture, improved error handling
v3.0 - modern UI with CustomTkinter, multi-threaded operations
"""

import re
import sys
import threading
import time

import customtkinter as ctk
import logzero
from logzero import logger

from config import config
from launcher import MinecraftLauncher
from ui import ModernApp


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
    pos_file = config.base_dir / "pos.txt"

    try:
        with open(pos_file, "w+") as f:
            logger.info("鼠标检测线程启动")

            while True:
                try:
                    import pyautogui
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
        logger.info("Minecraft Launcher v3.0 启动")
        logger.info("=" * 60)

        # 确保目录存在
        config.ensure_directories()

        # 设置游戏语言为中文
        set_chinese_language()

        # 创建启动器实例
        launcher = MinecraftLauncher(config)

        # 启动鼠标检测线程(守护线程,主程序退出时自动结束)
        mouse_thread = threading.Thread(target=detect_mouse_move, daemon=True)
        mouse_thread.start()

        # 设置CustomTkinter主题
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        # 创建并运行现代化UI
        app = ModernApp(launcher.get_callbacks())
        # 同步镜像源开关状态
        if "get_mirror_enabled" in launcher.get_callbacks():
            app.mirror_var.set(launcher.get_callbacks()["get_mirror_enabled"]())
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
