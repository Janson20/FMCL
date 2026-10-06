"""核心层对外可见的专用异常。

**为什么单独一个模块**（而不是写在 `launcher/core.py` 里）：这些异常要被
`services/` 捕获并翻译成界面文案，而 `launcher/core.py` 自己 import 了
`app.ports` 与 `services.theme_service` —— 服务再回头 import `launcher.core`
就绕成了一个环。本模块**零 import**，谁都能安全地引它。
"""

from __future__ import annotations


class InstallCancelled(Exception):
    """安装被用户取消（阶段 3 任务 3.3 的「尽力取消」）。

    语义（写清楚，免得调用方猜）：

    * **是取消，不是失败**：`install_version()` 会把它**原样抛出**，
      不会像其它异常那样被转成 `(False, version_id)` —— 调用方据此区分
      "装坏了"与"我不装了"。
    * **文件级粒度**：下载回调里发现取消标志就抛，已经下完并且哈希正确的文件
      **留在原处**（mcllib 的 `download_file` 本来就按 sha1 跳过合格文件），
      所以再点一次安装是**续装**，不是从零开始。
    * **不保证零残留**：取消可能落在"文件写了一半"或"版本 JSON 刚写、jar 还没下"
      的中间态，磁盘上因此可能留下一个不完整的版本目录。界面必须刷新列表
      （宁可让用户看见半个版本，也不要瞒着）。
    """


__all__ = ["InstallCancelled"]
