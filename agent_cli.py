"""FMCL Agent CLI 独立入口 - 供 PyInstaller 打包为控制台可执行文件

用法:
    FMCL-Agent <自然语言指令>
    FMCL-Agent                    # 进入交互模式
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cli_agent import run_agent_cli


def main():
    instruction = None
    if len(sys.argv) > 1:
        instruction = " ".join(sys.argv[1:])
    run_agent_cli(instruction=instruction)


if __name__ == "__main__":
    main()
