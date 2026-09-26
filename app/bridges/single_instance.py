"""单实例守卫 —— 第二次启动不再起一个新界面，而是把请求交给已经在跑的那个。

## 为什么用 `QLocalServer` 而不是锁文件

* **跨平台**：Windows 走命名管道、Unix 走本地 socket，Qt 自己处理差异；
* **不会留下死锁**：进程崩溃时管道随进程消失，不像锁文件需要判活；
* **自带通道**：第二个实例能把"请把窗口提到前面"这类请求**发过去**，
  锁文件只能表达"有人在跑"，没法通信。

## 语义（照 `03-phases.md` 任务 2.1 的要求）

* `try_acquire()` 返回 True：本进程是主实例，已经 `listen` 好。
* 返回 False：已经有实例在跑，本次请求已发出，调用方应当**尽快退出**。
* 主实例通过 `activateRequested` 信号收到后续启动请求（2.12 的窗口骨架接它做"提到前面"）。

启动键默认取"应用名 + 数据目录"，这样**不同数据目录/不同用户**可以各跑一个实例
（旧版没有单实例机制，用户复制一份数据目录开第二个是常见用法，不能一刀切禁掉）。
"""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger("app.bridges.single_instance")

#: 主实例与第二实例之间的握手：连接后发这一行即表示"请激活"。
ACTIVATE_MESSAGE = b"activate\n"


def default_key(app_name: str, data_dir: str) -> str:
    """由应用名与数据目录派生一个稳定的服务器名。

    `QLocalServer` 的名字在不同平台上长度有限制，且不允许路径分隔符，
    因此对"数据目录"做一次 sha1 摘要取前 12 位，而不是直接拼路径。
    """
    digest = hashlib.sha1(str(data_dir).encode("utf-8")).hexdigest()[:12]
    safe = "".join(c for c in app_name if c.isalnum() or c in "-_") or "app"
    return f"{safe}-{digest}"


class SingleInstance(QObject):
    """单实例守卫。用法::

        guard = SingleInstance(default_key("FMCL-QML", config.base_dir))
        if not guard.try_acquire():
            return 0          # 已有实例在跑，已经请它激活，本次退出
        guard.activateRequested.connect(window.raise_to_front)
    """

    #: 收到"又有一次启动请求"（由第二个实例发出）。
    activateRequested = Signal()

    def __init__(self, key: str, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._key = key
        self._server: Optional[QLocalServer] = None
        self._is_primary = False
        self._clients = 0

    # ─── 对外 ────────────────────────────────────────────────

    @property
    def key(self) -> str:
        return self._key

    @property
    def is_primary(self) -> bool:
        return self._is_primary

    @property
    def client_count(self) -> int:
        """收到过多少次启动请求（供测试与诊断用）。"""
        return self._clients

    def try_acquire(self) -> bool:
        """尝试成为主实例。返回 False 表示"已有实例，本次应退出"。"""
        if self._is_primary:
            return True

        if self._ping_existing():
            return False

        # 没有实例在跑。先 removeServer：Unix 上崩溃残留的 socket 文件会让 listen 失败，
        # Windows 的命名管道随进程消失，这一步是幂等的 no-op。
        QLocalServer.removeServer(self._key)
        server = QLocalServer(self)
        server.newConnection.connect(self._on_new_connection)
        if not server.listen(self._key):
            # 竞态：两个进程同时判活后同时 listen，只有一个成功。
            # 失败的一方按"已有实例"处理，而不是报错退出 —— 这正是单实例的语义。
            logger.info("单实例服务器监听失败（多半是并发启动的另一个实例抢先）: %s", server.errorString())
            return False

        self._server = server
        self._is_primary = True
        logger.info("单实例守卫就绪: %s", self._key)
        return True

    def release(self) -> None:
        """释放（退出路径调用）。不是必需 —— 进程结束会自动释放。"""
        if self._server is not None:
            self._server.close()
            QLocalServer.removeServer(self._key)
            self._server = None
        self._is_primary = False

    # ─── 内部 ────────────────────────────────────────────────

    def _ping_existing(self) -> bool:
        """能不能连上一个已经在跑的实例？能则顺带发出激活请求。"""
        sock = QLocalSocket()
        sock.connectToServer(self._key)
        if not sock.waitForConnected(300):
            return False
        try:
            sock.write(ACTIVATE_MESSAGE)
            sock.flush()
            sock.waitForBytesWritten(300)
        except Exception as e:  # noqa: BLE001 - 通知失败不该挡住"退出"这个结论
            logger.debug("向已有实例发送激活请求失败: %s", e)
        finally:
            sock.disconnectFromServer()
        return True

    def _on_new_connection(self) -> None:
        if self._server is None:
            return
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                break
            self._clients += 1
            conn.readyRead.connect(lambda c=conn: self._drain(c))
            conn.disconnected.connect(conn.deleteLater)
            # 有些平台不会立刻触发 readyRead（对端写得很快），主动读一次。
            self._drain(conn)
            self.activateRequested.emit()

    @staticmethod
    def _drain(conn: QLocalSocket) -> None:
        try:
            conn.readAll()
        except RuntimeError:
            pass


__all__ = ["ACTIVATE_MESSAGE", "SingleInstance", "default_key"]
