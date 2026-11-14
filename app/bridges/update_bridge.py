"""更新桥（阶段 3 任务 3.28）—— QML 上下文属性 Update。

设置 → 关于 的「检查更新 / 立即更新」与启动期自动检查的通知落点。
检查/下载/安装都在后台线程；进度用 Qt 信号回主线程，桥本身不碰 updater 的
下载细节（那些在 services/update_service.py）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from PySide6.QtCore import Property, QObject, Signal, Slot

from services.i18n_service import _

logger = logging.getLogger("app.bridges.update_bridge")

_ERROR_KEYS = {
    "no_asset": "about_update_no_asset",
    "download_failed": "about_update_download_failed",
    "install_failed": "about_update_install_failed",
    "platform_unsupported": "about_update_platform",
}


class UpdateBridge(QObject):
    """上下文属性 Update。"""

    changed = Signal()
    progressChanged = Signal()
    updateAvailable = Signal(str, str)
    installerStarted = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._service: Any = None
        self._release: Optional[dict] = None
        self._busy = False
        self._checking = False
        self._downloading = False
        self._progress = 0.0
        self._status = ""
        self._error = ""
        self._version = ""
        self._notes = ""

    def bind(self, context: Any = None) -> None:
        self._service = None
        if context is not None:
            try:
                self._service = context.try_get("update")
            except Exception as exc:  # noqa: BLE001 - 更新服务缺席只影响这个入口
                logger.warning("取更新服务失败: %s", exc)

    # ── 属性 ────────────────────────────────────────────────

    @Property(str, constant=True)
    def currentVersion(self) -> str:  # noqa: N802
        if self._service is None:
            return ""
        try:
            return str(self._service.current_version() or "")
        except Exception:
            return ""

    @Property(bool, notify=changed)
    def busy(self) -> bool:
        return self._busy

    @Property(bool, notify=changed)
    def checking(self) -> bool:
        return self._checking

    @Property(bool, notify=changed)
    def downloading(self) -> bool:
        return self._downloading

    @Property(float, notify=progressChanged)
    def progress(self) -> float:
        return self._progress

    @Property(str, notify=changed)
    def statusText(self) -> str:  # noqa: N802
        return self._status

    @Property(str, notify=changed)
    def errorText(self) -> str:  # noqa: N802
        return self._error

    @Property(str, notify=changed)
    def version(self) -> str:
        return self._version

    @Property(str, notify=changed)
    def notes(self) -> str:
        return self._notes

    @Property(bool, notify=changed)
    def available(self) -> bool:
        return bool(self._version) and self._release is not None

    # ── 动作 ────────────────────────────────────────────────

    @Slot()
    def check(self) -> None:  # noqa: N802
        if self._busy or self._service is None:
            return
        self._busy = True
        self._checking = True
        self._error = ""
        self._status = _("about_update_checking")
        self.changed.emit()
        threading.Thread(target=self._check_worker, daemon=True, name="update-check").start()

    def _check_worker(self) -> None:
        try:
            release = self._service.check_release()
        except Exception as exc:  # noqa: BLE001 - 网络失败只报状态
            logger.warning("检查更新失败: %s", exc)
            release = None
            self._error = _("about_update_failed")
        if release:
            self._release = dict(release)
            self._version = str(release.get("version") or "")
            self._notes = str(release.get("body") or "")
            self._status = _("about_update_available").format(version=self._version)
            self.updateAvailable.emit(self._version, self._notes)
        elif not self._error:
            self._release = None
            self._version = ""
            self._notes = ""
            self._status = _("about_update_latest")
        self._busy = False
        self._checking = False
        self.changed.emit()

    @Slot(str, str)
    def offer(self, version: str, notes: str) -> None:  # noqa: N802
        """启动期自动检查发现新版本时由 main_qml 转发；只提示，不自动下载。"""
        text = str(version or "").strip()
        if not text:
            return
        self._version = text
        self._notes = str(notes or "")
        self._status = _("about_update_available").format(version=text)
        self.updateAvailable.emit(text, self._notes)
        self.changed.emit()

    @Slot()
    def start(self) -> None:  # noqa: N802
        """发现新版本后下载并启动安装程序；成功后由 QML 关闭主窗口。"""
        if self._busy or self._service is None:
            return
        self._busy = True
        self._downloading = True
        self._progress = 0.0
        self._error = ""
        self._status = _("about_update_downloading")
        self.changed.emit()
        self.progressChanged.emit()
        threading.Thread(target=self._start_worker, daemon=True, name="update-install").start()

    def _start_worker(self) -> None:
        try:
            release = self._release
            if release is None:
                release = self._service.check_release()
            if not release:
                self._error = _("about_update_latest")
                return
            self._release = dict(release)
            self._version = str(release.get("version") or "")
            self._notes = str(release.get("body") or "")

            def on_progress(current: int, total: int) -> None:
                try:
                    self._progress = max(0.0, min(1.0, float(current) / float(total))) if total else 0.0
                except (TypeError, ValueError):
                    self._progress = 0.0
                self.progressChanged.emit()

            path, error = self._service.download(release, on_progress)
            if not path:
                self._error = _(_ERROR_KEYS.get(error, "about_update_download_failed"))
                return
            self._downloading = False
            self._status = _("about_update_installing")
            self.changed.emit()
            ok, error = self._service.install(path)
            if not ok:
                self._error = _(_ERROR_KEYS.get(error, "about_update_install_failed"))
                return
            self._service.mark_updated()
            self._status = _("about_update_installed")
            self.installerStarted.emit()
        except Exception as exc:  # noqa: BLE001 - 更新链失败只提示，不掀翻界面
            logger.warning("执行更新失败: %s", exc)
            self._error = _("about_update_failed")
        finally:
            self._busy = False
            self._downloading = False
            self.changed.emit()
            self.progressChanged.emit()


__all__ = ["UpdateBridge"]
