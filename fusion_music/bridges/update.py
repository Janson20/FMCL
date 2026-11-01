"""更新控制器（QML 侧）：检查、下载、安装、状态机。

界面要的是一个可以直接绑定的状态，所以整个流程收成一个状态机::

    idle ──检查──> checking ──> available ──下载──> downloading ──> ready
                     │             │                                 │
                     ├─> uptodate  └─> failed <──────────────────────┘
                     │                        ──安装──> installing
                     └─> failed

几条设计取舍：

* **检查、下载、解压都在工作线程**，主线程只改状态；工作线程**绝不碰 Qt 对象**
  （进度是它写两个整数、主线程用定时器读，见 ``_progress_timer``）；
* **安装必须由用户点**：自动检查只负责「告诉你有新版」，替换程序文件这种
  不可逆的动作不替用户决定；
* 下载完**先解压成暂存目录**（此时程序还在跑，进度条才有意义），
  于是「立即安装并重启」几乎是瞬时的；
* 源码运行 / 非 Windows / 程序目录不可写时，自动安装入口全部降级为
  「打开发布页」，并由 ``installHint`` 说明原因。
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from .. import paths, shellopen
from ..core import update_install as install_ops
from ..core import updater
from ..core.updater import ReleaseAsset, ReleaseInfo, UpdaterError

logger = logging.getLogger(__name__)

#: 状态
STATE_IDLE = "idle"
STATE_CHECKING = "checking"
STATE_UPTODATE = "uptodate"
STATE_AVAILABLE = "available"
STATE_DOWNLOADING = "downloading"
STATE_READY = "ready"
STATE_INSTALLING = "installing"
STATE_FAILED = "failed"

#: 启动后多久做第一次静默检查（别和启动时的取数据抢带宽）
STARTUP_DELAY_MS = 8000
#: 定期回看：是否真的该检查由「上次检查时间」决定，这里只负责到点触发
TICK_MS = 30 * 60 * 1000
#: 进度刷新间隔（主线程读工作线程写的计数）
PROGRESS_MS = 200


class _Emitter(QObject):
    """工作线程 → 主线程的结果投递（Qt 会自动排队到接收者线程）。"""

    checked = Signal(object, str)          # ReleaseInfo | None, 错误文本
    downloaded = Signal(str, str)          # 本地路径, 错误文本
    staged = Signal(bool, str)             # 是否成功, 错误文本


class UpdateController(QObject):
    """自动更新控制器，直接暴露给 QML。"""

    stateChanged = Signal()
    progressChanged = Signal()
    message = Signal(str)
    errorOccurred = Signal(str)

    def __init__(self, config, parent=None) -> None:
        super().__init__(parent)
        self._config = config
        self._state = STATE_IDLE
        self._release: Optional[ReleaseInfo] = None
        self._asset: Optional[ReleaseAsset] = None
        self._error = ""
        self._downloaded = 0
        self._total = 0
        self._phase = ""
        self._asset_path: Optional[Path] = None
        self._staging_dir: Optional[Path] = None
        self._cancel = threading.Event()
        self._kind = updater.install_kind()

        self._emitter = _Emitter(self)
        self._emitter.checked.connect(self._on_checked)
        self._emitter.downloaded.connect(self._on_downloaded)
        self._emitter.staged.connect(self._on_staged)

        # 进度轮询：工作线程只写 _downloaded / _total，主线程定时读
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(PROGRESS_MS)
        self._progress_timer.timeout.connect(self._poll_progress)

        self._tick = QTimer(self)
        self._tick.setInterval(TICK_MS)
        self._tick.timeout.connect(self._auto_tick)

    # ── 生命周期 ────────────────────────────────────────────

    def start(self) -> None:
        """挂上启动延迟与定期检查（在界面加载完之后调用）。"""
        self._report_previous_result()
        try:
            install_ops.cleanup_stale_downloads()
            install_ops.prune_stale()
        except Exception as e:  # pragma: no cover - 清理失败不该影响启动
            logger.debug("清理更新残留失败: %s", e)
        QTimer.singleShot(STARTUP_DELAY_MS, self._auto_tick)
        self._tick.start()

    def _auto_tick(self) -> None:
        if self.busy or not self.autoCheck:
            return
        interval = max(1, int(self.intervalHours)) * 3600
        try:
            last = float(self._config.get("update.last_check", 0) or 0)
        except (TypeError, ValueError):
            last = 0.0
        if time.time() - last < interval:
            return
        self.checkNow()

    # ── 只读信息 ────────────────────────────────────────────

    @Property(str, constant=True)
    def currentVersion(self) -> str:  # noqa: N802
        return str(paths.APP_VERSION)

    @Property(str, notify=stateChanged)
    def state(self) -> str:
        return self._state

    @Property(str, notify=stateChanged)
    def installKind(self) -> str:  # noqa: N802
        return self._kind

    @Property(bool, notify=stateChanged)
    def canInstall(self) -> bool:  # noqa: N802
        return install_ops.can_auto_install(self._kind)[0]

    @Property(str, notify=stateChanged)
    def installHint(self) -> str:  # noqa: N802
        return install_ops.can_auto_install(self._kind)[1]

    @Property(bool, notify=stateChanged)
    def busy(self) -> bool:
        return self._state in (STATE_CHECKING, STATE_DOWNLOADING, STATE_INSTALLING)

    @Property(bool, notify=stateChanged)
    def updateAvailable(self) -> bool:  # noqa: N802
        return self._state in (STATE_AVAILABLE, STATE_DOWNLOADING, STATE_READY)

    @Property(bool, notify=stateChanged)
    def ready(self) -> bool:
        return self._state == STATE_READY

    @Property(str, notify=stateChanged)
    def latestVersion(self) -> str:  # noqa: N802
        return self._release.version if self._release else ""

    @Property(str, notify=stateChanged)
    def latestName(self) -> str:  # noqa: N802
        if not self._release:
            return ""
        return self._release.name or self._release.tag

    @Property(str, notify=stateChanged)
    def releaseNotes(self) -> str:  # noqa: N802
        return updater.release_notes_excerpt(self._release.body if self._release else "")

    @Property(str, notify=stateChanged)
    def releaseUrl(self) -> str:  # noqa: N802
        if self._release and self._release.html_url:
            return self._release.html_url
        return f"https://github.com/{updater.DEFAULT_REPO}/releases"

    @Property(str, notify=stateChanged)
    def publishedAt(self) -> str:  # noqa: N802
        text = self._release.published_at if self._release else ""
        return str(text or "")[:10]

    @Property(str, notify=stateChanged)
    def assetName(self) -> str:  # noqa: N802
        return self._asset.name if self._asset else ""

    @Property(str, notify=stateChanged)
    def assetSize(self) -> str:  # noqa: N802
        return self._asset.size_text if self._asset else ""

    @Property(str, notify=stateChanged)
    def statusText(self) -> str:  # noqa: N802
        if self._state == STATE_CHECKING:
            return "正在检查更新…"
        if self._state == STATE_UPTODATE:
            return f"已是最新版本（{self.currentVersion}）"
        if self._state == STATE_AVAILABLE:
            return f"发现新版本 {self.latestVersion}"
        if self._state == STATE_DOWNLOADING:
            return f"正在下载 {self.progressPercent}%（{self.progressText}）"
        if self._state == STATE_READY:
            return "已下载完成，可以安装"
        if self._state == STATE_INSTALLING:
            return "正在准备安装…"
        if self._state == STATE_FAILED:
            return self._error or "更新失败"
        return ""

    @Property(str, notify=stateChanged)
    def errorText(self) -> str:  # noqa: N802
        return self._error

    @Property(float, notify=progressChanged)
    def progress(self) -> float:
        if self._total <= 0:
            return 0.0
        return max(0.0, min(1.0, self._downloaded / float(self._total)))

    @Property(int, notify=progressChanged)
    def progressPercent(self) -> int:  # noqa: N802
        return int(round(self.progress * 100))

    @Property(str, notify=progressChanged)
    def progressText(self) -> str:  # noqa: N802
        if self._phase != "download":
            return ""
        if self._total <= 0:
            return updater.human_size(self._downloaded) if self._downloaded else ""
        return f"{updater.human_size(self._downloaded)} / {updater.human_size(self._total)}"

    # ── 设置 ────────────────────────────────────────────────

    @Property(bool, notify=stateChanged)
    def autoCheck(self) -> bool:  # noqa: N802
        return bool(self._config.get("update.auto_check", True))

    @Slot(bool)
    def setAutoCheck(self, value: bool) -> None:  # noqa: N802
        self._config.set("update.auto_check", bool(value))
        self.stateChanged.emit()
        if value:
            self._auto_tick()

    @Property(int, notify=stateChanged)
    def intervalHours(self) -> int:  # noqa: N802
        try:
            return max(1, int(self._config.get("update.interval_hours", 24) or 24))
        except (TypeError, ValueError):
            return 24

    @Slot(int)
    def setIntervalHours(self, hours: int) -> None:  # noqa: N802
        self._config.set("update.interval_hours", max(1, int(hours)))
        self.stateChanged.emit()

    @Property(bool, notify=stateChanged)
    def includePrerelease(self) -> bool:  # noqa: N802
        return bool(self._config.get("update.include_prerelease", False))

    @Slot(bool)
    def setIncludePrerelease(self, value: bool) -> None:  # noqa: N802
        self._config.set("update.include_prerelease", bool(value))
        self.stateChanged.emit()

    @Property("QVariantList", constant=True)
    def intervalOptions(self):  # noqa: N802
        return [
            {"id": 6, "name": "每 6 小时"},
            {"id": 24, "name": "每天"},
            {"id": 72, "name": "每 3 天"},
            {"id": 168, "name": "每周"},
        ]

    # ── 检查 ────────────────────────────────────────────────

    @Slot()
    def checkNow(self) -> None:  # noqa: N802
        """立即检查（用户点按钮走这里，启动后的静默检查也走这里）。"""
        if self.busy:
            return
        self._error = ""
        self._set_state(STATE_CHECKING)
        threading.Thread(target=self._check_worker, daemon=True,
                         name="update-check").start()

    def _check_worker(self) -> None:
        error = ""
        release: Optional[ReleaseInfo] = None
        try:
            session = updater.build_session(self._proxy())
            release = updater.fetch_latest(
                updater.DEFAULT_REPO,
                session=session,
                timeout=self._timeout(),
                include_prerelease=self.includePrerelease,
            )
        except UpdaterError as e:
            error = str(e)
        except Exception as e:  # pragma: no cover - 兜底
            logger.exception("检查更新失败")
            error = f"检查更新失败：{e}"
        self._emitter.checked.emit(release, error)

    @Slot(object, str)
    def _on_checked(self, release: Any, error: str) -> None:
        # 成败都记一次检查时间：失败时也别每次启动都重试
        self._config.set("update.last_check", time.time())
        if error or release is None:
            self._error = error or "检查更新失败"
            self._set_state(STATE_FAILED)
            return
        self._release = release
        skipped = str(self._config.get("update.skip_version", "") or "")
        if not updater.is_newer(release.version, self.currentVersion):
            self._set_state(STATE_UPTODATE)
            return
        if skipped and skipped == release.version:
            self._set_state(STATE_UPTODATE)
            self.message.emit(f"已跳过版本 {release.version}")
            return
        self._asset = release.asset_for(self._kind, release.version)
        self._set_state(STATE_AVAILABLE)
        self.message.emit(f"发现新版本 {release.version}")

    # ── 下载 ────────────────────────────────────────────────

    @Slot()
    def download(self) -> None:
        if self._state not in (STATE_AVAILABLE, STATE_FAILED) or self._release is None:
            return
        asset = self._asset or self._release.asset_for(self._kind, self._release.version)
        if asset is None:
            self._fail("这个版本没有适用于当前运行方式的安装包，请到发布页手动下载")
            return
        if not updater.url_allowed(asset.url):
            self._fail("下载地址不在允许的域名内，已拒绝")
            return
        if not asset.sha256:
            self._fail("发布信息没有提供校验摘要，已放弃自动更新（可打开发布页手动下载）")
            return
        self._asset = asset
        self._cancel.clear()
        self._downloaded = 0
        self._total = int(asset.size or 0)
        self._phase = "download"
        self._set_state(STATE_DOWNLOADING)
        self._progress_timer.start()
        threading.Thread(target=self._download_worker, args=(asset,), daemon=True,
                         name="update-download").start()

    def _download_worker(self, asset: ReleaseAsset) -> None:
        error = ""
        path = ""
        try:
            session = updater.build_session(self._proxy())
            target = updater.download(
                asset,
                paths.updates_dir(),
                session=session,
                timeout=max(30, self._timeout()),
                progress=self._note_progress,
                should_cancel=self._cancel.is_set,
            )
            path = str(target)
        except UpdaterError as e:
            error = str(e)
        except Exception as e:  # pragma: no cover - 兜底
            logger.exception("下载更新失败")
            error = f"下载失败：{e}"
        self._emitter.downloaded.emit(path, error)

    def _note_progress(self, current: int, total: int) -> None:
        """工作线程调用：只写两个整数，不碰任何 Qt 对象。"""
        self._downloaded = int(current)
        if total:
            self._total = int(total)

    def _poll_progress(self) -> None:
        """主线程定时读进度（工作线程不会碰 Qt，见模块说明）。"""
        self.progressChanged.emit()
        self.stateChanged.emit()
        if not self.busy:
            self._progress_timer.stop()

    @Slot(str, str)
    def _on_downloaded(self, path: str, error: str) -> None:
        if error or not path:
            self._progress_timer.stop()
            self._fail(error or "下载失败")
            return
        self._asset_path = Path(path)
        self.message.emit("下载完成，正在解压…")
        self._phase = "stage"
        self._downloaded = 0
        self._total = 0
        self._set_state(STATE_INSTALLING)
        # 进度轮询不停：解压同样要显示百分比（进度文本只在下下载阶段显示字节数）
        threading.Thread(target=self._stage_worker, daemon=True,
                         name="update-stage").start()

    def _stage_worker(self) -> None:
        ok, error = True, ""
        try:
            version = self._release.version if self._release else "unknown"
            staging = paths.updates_dir() / f"{install_ops.STAGING_PREFIX}{version}"
            self._staging_dir = install_ops.stage_release(
                self._asset_path, self._kind, staging,
                progress=self._note_progress,
            )
        except Exception as e:
            logger.exception("解压更新包失败")
            ok, error = False, f"解压更新包失败：{e}"
        self._emitter.staged.emit(ok, error)

    @Slot(bool, str)
    def _on_staged(self, ok: bool, error: str) -> None:
        self._progress_timer.stop()
        if not ok:
            self._fail(error or "解压更新包失败")
            return
        self._set_state(STATE_READY)
        self.message.emit("更新已就绪，点「立即安装并重启」即可完成")

    @Slot()
    def cancelDownload(self) -> None:  # noqa: N802
        if self._state == STATE_DOWNLOADING:
            self._cancel.set()
            self.message.emit("已取消下载")

    @Slot()
    def skipVersion(self) -> None:  # noqa: N802
        """跳过这个版本（直到出现更新的版本再提示）。"""
        if self._release is None:
            return
        self._config.set("update.skip_version", self._release.version)
        self._set_state(STATE_UPTODATE)
        self.message.emit(f"已跳过版本 {self._release.version}")

    # ── 安装 ────────────────────────────────────────────────

    @Slot(result=bool)
    def installAndRestart(self) -> bool:  # noqa: N802
        """替换程序文件并重启；返回是否真的开始安装。"""
        ok, reason = install_ops.can_auto_install(self._kind)
        if not ok:
            self.errorOccurred.emit(reason)
            return False
        if self._state != STATE_READY or self._staging_dir is None:
            self.errorOccurred.emit("还没有下载好可安装的更新")
            return False

        from PySide6.QtGui import QGuiApplication

        install_dir = paths.program_dir()
        try:
            exe_name = Path(sys.executable).name
            backup_dir = (paths.updates_dir()
                          / f"{install_ops.BACKUP_PREFIX}{self.latestVersion or 'old'}")
            script = install_ops.write_script(install_ops.build_script())
            install_ops.write_pending({
                "from": self.currentVersion,
                "to": self.latestVersion,
                "kind": self._kind,
                "asset": self.assetName,
            })
            self._set_state(STATE_INSTALLING)
            install_ops.launch_script(
                script,
                kind=self._kind,
                pid=os.getpid(),
                install_dir=install_dir,
                staging_dir=self._staging_dir,
                exe_name=exe_name,
                backup_dir=backup_dir,
                log_path=install_ops.log_file(),
                data_dir=paths.data_dir(),
            )
        except Exception as e:
            logger.exception("启动更新脚本失败")
            install_ops.clear_pending()
            self._fail(f"无法启动更新程序：{e}")
            return False

        logger.info("即将退出以完成更新：%s -> %s", self.currentVersion, self.latestVersion)
        self.message.emit("正在重启以完成更新…")
        # 交给事件循环退出：aboutToQuit 会照常保存会话与配置
        QTimer.singleShot(500, lambda: QGuiApplication.exit(0))
        return True

    @Slot()
    def openReleasePage(self) -> None:  # noqa: N802
        """打开发布页（手动下载 / 看完整说明）。"""
        url = self.releaseUrl
        try:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices

            QDesktopServices.openUrl(QUrl(url))
        except Exception as e:
            logger.warning("打开发布页失败: %s", e)
            self.errorOccurred.emit(f"无法打开浏览器：{url}")

    @Slot()
    def openUpdateLog(self) -> None:  # noqa: N802
        """打开更新日志（出问题时让用户直接看这个文件）。"""
        target = install_ops.log_file()
        try:
            shellopen.open_path(target)
        except OSError as e:
            logger.debug("打开更新日志失败: %s", e)
            self.errorOccurred.emit("无法打开更新日志")

    @Property(str, notify=stateChanged)
    def lastLogTail(self) -> str:  # noqa: N802
        return install_ops.read_log_tail(6)

    # ── 内部 ────────────────────────────────────────────────

    def _set_state(self, state: str) -> None:
        self._state = state
        self.stateChanged.emit()
        self.progressChanged.emit()

    def _fail(self, text: str) -> None:
        self._error = str(text)
        self._phase = ""
        self._progress_timer.stop()
        self._set_state(STATE_FAILED)
        self.errorOccurred.emit(self._error)

    def _proxy(self) -> str:
        return str(self._config.get("advanced.proxy", "") or "")

    def _timeout(self) -> int:
        try:
            return max(5, int(self._config.get("advanced.timeout", 15) or 15))
        except (TypeError, ValueError):
            return 15

    def _report_previous_result(self) -> None:
        """上次「安装并重启」成没成：靠更新前写下的标记判断。"""
        pending = install_ops.read_pending()
        if not pending:
            return
        target = str(pending.get("to") or "")
        install_ops.clear_pending()
        if target and target == self.currentVersion:
            logger.info("上次自动更新成功：%s", target)
            self.message.emit(f"已更新到 {self.currentVersion}")
            return
        tail = install_ops.read_log_tail(3)
        logger.warning("上次自动更新未生效（期望 %s，当前 %s）", target, self.currentVersion)
        self.errorOccurred.emit(
            "上次自动更新没有生效，已回到当前版本"
            + (f"；更新日志：{tail}" if tail else "")
        )
