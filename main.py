"""Minecraft Launcher - 主程序入口
Update Log:
v1.0 - start project
v1.1 - add forge install
v1.2 - add logs module
v1.3 - add ui
v1.4 - add mouse detect
v2.0 - refactor: modular architecture, improved error handling
"""

import sys
import threading
import time

import logzero
import pyautogui
from logzero import logger

from config import config
from launcher import MinecraftLauncher


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
        logger.info("Minecraft Launcher v2.0 启动")
        logger.info("=" * 60)
        
        # 确保目录存在
        config.ensure_directories()
        
        # 创建启动器实例
        launcher = MinecraftLauncher(config)
        
        # 启动鼠标检测线程(守护线程,主程序退出时自动结束)
        mouse_thread = threading.Thread(target=detect_mouse_move, daemon=True)
        mouse_thread.start()
        
        # 运行启动器
        launcher.run()
        
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
