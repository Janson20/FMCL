"""工具箱 QML 桥（阶段 3 任务 3.20）。

链路：

    qml/pages/tools/*.qml
        -> Tools.xxx
    app/bridges/tools_bridge.py（本模块，零业务规则）
        -> services/tools_session.py（状态 / 历史 / 题库 / 下载标记）
        -> services/tool_service.py（扫描 / 协议 / 哈希 / 问答 / 分段下载）

线程约定与 3.13、3.14 相同：长活全部走 AppContext 的 TaskRunner，worker 只
通过排队信号投递结果，真正改 QObject 属性的只有主线程的 _deliver()。
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, Qt, QUrl, Signal, Slot

from services.i18n_service import _
from services.tool_service import DownloadResumeError
from services.tools_session import ToolsSession

logger = logging.getLogger("app.bridges.tools_bridge")


class ToolsBridge(QObject):
    """上下文属性 Tools。"""

    junkChanged = Signal()
    portChanged = Signal()
    hashChanged = Signal()
    coordChanged = Signal()
    fortuneChanged = Signal()
    factChanged = Signal()
    quizChanged = Signal()
    downloadChanged = Signal()
    statusMessage = Signal(str, str)
    posted = Signal("QVariantMap")

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._context: Any = None
        self._tasks: Any = None
        self._session: Optional[ToolsSession] = None
        self._handles: Dict[str, Any] = {}
        self._closing = False
        self.posted.connect(self._deliver, Qt.QueuedConnection)

        self._junk_groups: List[Dict[str, Any]] = []
        self._junk_state = "idle"
        self._junk_error = ""
        self._junk_notice = ""
        self._junk_notice_level = "info"
        self._junk_count = 0
        self._junk_total_size = 0
        self._junk_selected_count = 0
        self._junk_selected_size = 0
        self._junk_token = 0

        self._port_busy = False
        self._port_action = ""
        self._port_error = ""
        self._port_history: List[Dict[str, Any]] = []
        self._server_info: Dict[str, Any] = {}
        self._port_token = 0

        self._hash_busy = False
        self._hash_error = ""
        self._hash_result: Dict[str, Any] = {}
        self._hash_token = 0

        self._coord_result: Dict[str, Any] = {}

        self._fortune_result: Dict[str, Any] = {}
        self._fortune_history: List[Dict[str, Any]] = []
        self._fortune_error = ""

        self._fact: Dict[str, Any] = {}

        self._quiz_token = False
        self._quiz_busy = False
        self._quiz_refilling = False
        self._quiz_error = ""
        self._quiz_current: Dict[str, Any] = {}
        self._quiz_remaining = 0
        self._quiz_has_questions = False
        self._quiz_answered = False
        self._quiz_result: Dict[str, Any] = {}
        self._quiz_needs_refill = True
        self._quiz_notice = ""
        self._quiz_token_id = 0

        self._download_state = "idle"
        self._download_progress: Dict[str, Any] = {}
        self._download_error = ""
        self._download_target = ""
        self._download_resume: Dict[str, Any] = {}
        self._download_threads_used = 0
        self._download_url = ""
        self._download_ua = "FMCL/2.0"
        self._download_pause: Optional[threading.Event] = None
        self._download_cancel: Optional[threading.Event] = None
        self._download_token = 0
        self._quiz_token_id = 0

    # ─── 装配 ───────────────────────────────────────────────

    def bind(self, context: Any = None) -> None:
        self._context = context
        self._tasks = getattr(context, "tasks", None) if context is not None else None
        config = getattr(context, "config", None) if context is not None else None
        self._session = ToolsSession(config)
        logger.info("工具箱桥已装配（扫描目录 %s）", self.defaultFolder)

    def detach(self) -> None:
        """退出时：下载请求暂停（保留分片），其余后台任务取消。"""
        self._closing = True
        if self._download_pause is not None:
            self._download_pause.set()
        for key, handle in list(self._handles.items()):
            if key == "tools.download":
                continue
            try:
                handle.cancel()
            except Exception:  # noqa: BLE001
                pass
        self._handles.clear()

    # ─── 通用属性 ───────────────────────────────────────────

    @Property(bool, constant=True)
    def available(self) -> bool:
        return self._session is not None

    @Property(str, constant=True)
    def defaultFolder(self) -> str:  # noqa: N802
        return self._session.default_scan_dir() if self._session is not None else ""

    @Property(str, constant=True)
    def defaultDownloadDir(self) -> str:  # noqa: N802
        return self._session.default_download_dir() if self._session is not None else ""

    @Property(int, constant=True)
    def configuredThreads(self) -> int:  # noqa: N802
        return self._session.download_threads() if self._session is not None else 0

    @Slot(str, result=str)
    def localPath(self, url: str) -> str:  # noqa: N802
        """把 FileDialog / FolderDialog 的 URL 转成本地路径。"""
        return QUrl(str(url or "")).toLocalFile()

    # ─── 垃圾清理 ───────────────────────────────────────────

    @Property(str, notify=junkChanged)
    def junkState(self) -> str:  # noqa: N802
        return self._junk_state

    @Property(str, notify=junkChanged)
    def junkError(self) -> str:  # noqa: N802
        return self._junk_error

    @Property(str, notify=junkChanged)
    def junkNotice(self) -> str:  # noqa: N802
        return self._junk_notice

    @Property(str, notify=junkChanged)
    def junkNoticeLevel(self) -> str:  # noqa: N802
        return self._junk_notice_level

    @Property("QVariantList", notify=junkChanged)
    def junkGroups(self) -> List[Dict[str, Any]]:  # noqa: N802
        return self._junk_groups

    @Property(int, notify=junkChanged)
    def junkCount(self) -> int:  # noqa: N802
        return self._junk_count

    @Property(str, notify=junkChanged)
    def junkTotalSizeText(self) -> str:  # noqa: N802
        return self._size_text(self._junk_total_size)

    @Property(int, notify=junkChanged)
    def junkSelectedCount(self) -> int:  # noqa: N802
        return self._junk_selected_count

    @Property(str, notify=junkChanged)
    def junkSelectedSizeText(self) -> str:  # noqa: N802
        return self._size_text(self._junk_selected_size)

    @Property(bool, notify=junkChanged)
    def junkBusy(self) -> bool:  # noqa: N802
        return self._junk_state in ("scanning", "deleting")

    @Slot(str, str)
    def scanJunk(self, folder: str, depth_text: str) -> None:  # noqa: N802
        session = self._session
        if session is None or self.junkBusy:
            return
        path = str(folder or "").strip() or session.default_scan_dir()
        from pathlib import Path

        if not Path(path).is_dir():
            self._set_junk_error(_("tool_clean_junk_folder_error"))
            return
        if session.service.is_protected_system_dir(path):
            self._set_junk_error(_("tool_clean_junk_system_dir_error"))
            return
        try:
            depth = session.service.parse_max_depth(depth_text)
        except Exception:  # noqa: BLE001
            self._set_junk_error(_("tool_clean_junk_depth_error"))
            return

        self._junk_token += 1
        token = self._junk_token
        self._junk_state = "scanning"
        self._junk_error = ""
        self._junk_notice = ""
        self._junk_groups = []
        self._junk_count = 0
        self._junk_total_size = 0
        self._junk_selected_count = 0
        self._junk_selected_size = 0
        self.junkChanged.emit()

        def work() -> Dict[str, Any]:
            scan = session.service.scan_junk_files(path, depth)
            groups = []
            for group in session.service.group_junk_files(scan.base, scan.files):
                files = []
                for file_path, size in group.files:
                    files.append(
                        {
                            "path": str(file_path),
                            "name": session.service.relative_path_from(str(file_path), scan.base),
                            "size": int(size),
                            "sizeText": session.service.format_size(int(size)),
                            "checked": True,
                        }
                    )
                for item in files:
                    item["groupKey"] = group.dir
                    item["dirKey"] = group.dir
                groups.append(
                    {
                        "key": group.dir,
                        "display": group.display,
                        "count": len(files),
                        "size": sum(item["size"] for item in files),
                        "sizeText": session.service.format_size(sum(item["size"] for item in files)),
                        "checked": True,
                        "checkState": 2,
                        "expanded": False,
                        "files": files,
                    }
                )
            return {"base": scan.base, "groups": groups, "count": len(scan.files), "total": scan.total_size}

        self._run("tools.junk.scan", work, "junk-scan-done", token)

    @Slot(str, bool)
    def setJunkGroupChecked(self, key: str, checked: bool) -> None:  # noqa: N802
        group = self._junk_group(key)
        if group is None:
            return
        for item in group["files"]:
            item["checked"] = bool(checked)
        self._recompute_junk()
        self.junkChanged.emit()

    @Slot(str, str, bool)
    def setJunkFileChecked(self, key: str, path: str, checked: bool) -> None:  # noqa: N802
        group = self._junk_group(key)
        if group is None:
            return
        for item in group["files"]:
            if item["path"] == path:
                item["checked"] = bool(checked)
                break
        self._recompute_junk()
        self.junkChanged.emit()

    @Slot(str)
    def toggleJunkGroupExpanded(self, key: str) -> None:  # noqa: N802
        group = self._junk_group(key)
        if group is None:
            return
        group["expanded"] = not bool(group.get("expanded", False))
        self.junkChanged.emit()

    @Slot()
    def deleteJunk(self) -> None:  # noqa: N802
        session = self._session
        if session is None or self.junkBusy:
            return
        selected = []
        for group in self._junk_groups:
            for item in group["files"]:
                if item.get("checked", False):
                    selected.append((group["key"], item["path"], item["size"]))
        if not selected:
            self._junk_notice = _("tool_clean_junk_select_none")
            self._junk_notice_level = "warning"
            self.junkChanged.emit()
            return

        token = self._junk_token
        self._junk_state = "deleting"
        self._junk_error = ""
        self._junk_notice = ""
        self.junkChanged.emit()
        selected_paths = {str(item[1]) for item in selected}

        def work() -> Dict[str, Any]:
            result = session.service.delete_junk_files(selected)
            return {"deleted": result.deleted, "failed": result.failed, "deleted_size": result.deleted_size}

        def done(result: Any) -> None:
            self._post(
                {
                    "kind": "junk-delete-done",
                    "key": "tools.junk.delete",
                    "token": token,
                    "result": result,
                    "paths": sorted(selected_paths),
                }
            )

        def failed(error: Any) -> None:
            self._post({"kind": "junk-delete-error", "key": "tools.junk.delete", "token": token, "error": str(error)})

        self._submit("tools.junk.delete", work, done, failed)

    def _junk_group(self, key: str) -> Optional[Dict[str, Any]]:
        for group in self._junk_groups:
            if group.get("key") == key:
                return group
        return None

    def _recompute_junk(self) -> None:
        count = 0
        total_size = 0
        selected_count = 0
        selected_size = 0
        for group in self._junk_groups:
            group_checked = 0
            group_size = 0
            for item in group["files"]:
                count += 1
                total_size += int(item["size"])
                group_size += int(item["size"])
                if item.get("checked", False):
                    group_checked += 1
                    selected_count += 1
                    selected_size += int(item["size"])
            group["count"] = len(group["files"])
            group["size"] = group_size
            group["sizeText"] = self._size_text(group_size)
            group["checked"] = group_checked == len(group["files"]) and group_checked > 0
            group["checkState"] = 2 if group["checked"] else (1 if group_checked > 0 else 0)
        self._junk_count = count
        self._junk_total_size = total_size
        self._junk_selected_count = selected_count
        self._junk_selected_size = selected_size

    def _apply_junk_scan(self, payload: Dict[str, Any]) -> None:
        self._junk_groups = list(payload.get("groups", []) or [])
        self._junk_state = "ready"
        self._junk_error = ""
        self._junk_notice = ""
        self._junk_notice_level = "info"
        self._recompute_junk()
        self.junkChanged.emit()

    def _apply_junk_delete(self, payload: Dict[str, Any], selected_paths: set) -> None:
        kept_groups = []
        for group in self._junk_groups:
            files = [item for item in group["files"] if item["path"] not in selected_paths]
            if files:
                group["files"] = files
                kept_groups.append(group)
        self._junk_groups = kept_groups
        self._junk_state = "ready"
        self._junk_error = ""
        self._recompute_junk()
        deleted = int(payload.get("deleted", 0))
        failed = int(payload.get("failed", 0))
        size = self._size_text(int(payload.get("deleted_size", 0)))
        self._junk_notice = _("tool_clean_junk_done", deleted=deleted, failed=failed, size=size)
        self._junk_notice_level = "success" if failed == 0 else "warning"
        self.junkChanged.emit()

    def _set_junk_error(self, message: str) -> None:
        self._junk_state = "error"
        self._junk_error = message
        self._junk_notice = message
        self._junk_notice_level = "error"
        self.junkChanged.emit()

    def _size_text(self, value: int) -> str:
        if self._session is None:
            return ""
        return self._session.format_size(int(value))

    # ─── 端口检测 ───────────────────────────────────────────

    @Property(bool, notify=portChanged)
    def portBusy(self) -> bool:  # noqa: N802
        return self._port_busy

    @Property(str, notify=portChanged)
    def portAction(self) -> str:  # noqa: N802
        return self._port_action

    @Property(str, notify=portChanged)
    def portError(self) -> str:  # noqa: N802
        return self._port_error

    @Property("QVariantList", notify=portChanged)
    def portHistory(self) -> List[Dict[str, Any]]:  # noqa: N802
        return self._port_history

    @Property("QVariantMap", notify=portChanged)
    def serverInfo(self) -> Dict[str, Any]:  # noqa: N802
        return self._server_info

    @Slot(str, str)
    def testPort(self, host: str, port_text: str) -> None:  # noqa: N802
        session = self._session
        if session is None or self._port_busy:
            return
        host = str(host or "").strip()
        if not host:
            self._port_error = _("tool_port_no_host")
            self.portChanged.emit()
            return
        if not str(port_text or "").strip():
            self._port_error = _("tool_port_no_port")
            self.portChanged.emit()
            return
        try:
            port = session.service.parse_port(str(port_text).strip())
        except Exception:  # noqa: BLE001
            self._port_error = _("tool_port_invalid_port")
            self.portChanged.emit()
            return
        self._port_token += 1
        token = self._port_token
        self._port_busy = True
        self._port_action = "test"
        self._port_error = ""
        self.portChanged.emit()

        def work() -> Dict[str, Any]:
            probe = session.service.probe_port(host, port)
            return {
                "host": probe.host,
                "port": probe.port,
                "open": probe.status == "open",
                "latency": probe.latency_ms,
                "error": probe.error or "",
            }

        self._run("tools.port.test", work, "port-test-done", token)

    @Slot(str, str)
    def peekServer(self, host: str, port_text: str) -> None:  # noqa: N802
        session = self._session
        if session is None or self._port_busy:
            return
        host = str(host or "").strip()
        if not host:
            self._port_error = _("tool_port_no_host")
            self.portChanged.emit()
            return
        try:
            port = session.service.parse_port(str(port_text or "25565").strip())
        except Exception:  # noqa: BLE001
            self._port_error = _("tool_port_invalid_port")
            self.portChanged.emit()
            return
        self._port_token += 1
        token = self._port_token
        self._port_busy = True
        self._port_action = "peek"
        self._port_error = ""
        self._server_info = {}
        self.portChanged.emit()

        def work() -> Dict[str, Any]:
            peek = session.service.peek_server(host, port)
            if peek.error or peek.info is None:
                return {"error": peek.error or _("tool_peek_unknown_error"), "latency": peek.latency_ms}
            summary = session.service.summarize_server_info(peek.info)
            favicon = ""
            raw_favicon = peek.info.get("favicon") if isinstance(peek.info, dict) else None
            if raw_favicon:
                try:
                    image = session.service.decode_favicon_data(str(raw_favicon))
                    if image:
                        import base64

                        encoded = base64.b64encode(image).decode("ascii")
                        favicon = "data:image/png;base64," + encoded
                except Exception:  # noqa: BLE001
                    favicon = ""
            latency = int(peek.latency_ms)
            latency_level = "success" if latency < 150 else ("warning" if latency < 400 else "error")
            return {
                "motd": summary.desc_text,
                "version": summary.version_name,
                "online": summary.online,
                "maxPlayers": summary.max_players,
                "sample": summary.sample_text,
                "latency": latency,
                "latencyLevel": latency_level,
                "favicon": favicon,
            }

        self._run("tools.port.peek", work, "port-peek-done", token)

    @Slot()
    def clearPortHistory(self) -> None:  # noqa: N802
        self._port_history = []
        self.portChanged.emit()

    # ─── Hash / 坐标 / 运势 / 冷知识 ─────────────────────────

    @Property(bool, notify=hashChanged)
    def hashBusy(self) -> bool:  # noqa: N802
        return self._hash_busy

    @Property(str, notify=hashChanged)
    def hashError(self) -> str:  # noqa: N802
        return self._hash_error

    @Property("QVariantMap", notify=hashChanged)
    def hashResult(self) -> Dict[str, Any]:  # noqa: N802
        return self._hash_result

    @Slot(str, str)
    def calculateHash(self, path: str, algorithm: str) -> None:  # noqa: N802
        session = self._session
        if session is None or self._hash_busy:
            return
        from pathlib import Path

        file_path = Path(str(path or "").strip())
        if not str(path or "").strip() or not file_path.is_file():
            self._hash_error = _("tool_hash_file_error")
            self._hash_result = {}
            self.hashChanged.emit()
            return
        algo = str(algorithm or "SHA256").upper()
        self._hash_token += 1
        token = self._hash_token
        self._hash_busy = True
        self._hash_error = ""
        self._hash_result = {}
        self.hashChanged.emit()

        def work() -> Dict[str, Any]:
            digest = session.service.hash_file(str(file_path), algo)
            return {
                "algorithm": algo,
                "digest": digest,
                "fileName": file_path.name,
                "fileSizeText": session.service.format_size(file_path.stat().st_size),
                "copyText": f"{algo}:  {digest}",
            }

        self._run("tools.hash", work, "hash-done", token)

    @Property("QVariantMap", notify=coordChanged)
    def coordResult(self) -> Dict[str, Any]:  # noqa: N802
        return self._coord_result

    @Slot(str, str, str, str)
    def convertCoordinates(self, x_text: str, y_text: str, z_text: str, target: str) -> None:  # noqa: N802
        session = self._session
        if session is None:
            return
        x = session.service.parse_coordinate(str(x_text))
        y = session.service.parse_coordinate(str(y_text))
        z = session.service.parse_coordinate(str(z_text))
        result = session.service.convert_coordinates(x, y, z, str(target or "nether"))
        self._coord_result = {
            "x": result.x,
            "y": result.y,
            "z": result.z,
            "rx": result.rx,
            "rz": result.rz,
            "target": str(target or "nether"),
            "text": f"{result.rx}, {result.y}, {result.rz}",
            "desc": result.desc,
        }
        self.coordChanged.emit()

    @Property("QVariantMap", notify=fortuneChanged)
    def fortuneResult(self) -> Dict[str, Any]:  # noqa: N802
        return self._fortune_result

    @Property("QVariantList", notify=fortuneChanged)
    def fortuneHistory(self) -> List[Dict[str, Any]]:  # noqa: N802
        return self._fortune_history

    @Property(int, notify=fortuneChanged)
    def fortuneHistoryCount(self) -> int:  # noqa: N802
        return len(self._fortune_history)

    @Property(str, notify=fortuneChanged)
    def fortuneError(self) -> str:  # noqa: N802
        return self._fortune_error

    @Slot()
    def loadFortune(self) -> None:  # noqa: N802
        if self._session is None:
            return
        self._fortune_history = self._session.load_fortune_history()
        self._fortune_error = ""
        self.fortuneChanged.emit()

    @Slot()
    def checkFortune(self) -> None:  # noqa: N802
        if self._session is None:
            return
        try:
            payload = self._session.check_fortune()
        except Exception as e:  # noqa: BLE001
            self._fortune_error = str(e)
            self.fortuneChanged.emit()
            return
        self._fortune_result = dict(payload.get("result", {}) or {})
        self._fortune_history = list(payload.get("history", []) or [])
        self._fortune_error = ""
        if not payload.get("saved", True):
            self._fortune_error = _("tool_fortune_history_save_failed")
            self.statusMessage.emit(self._fortune_error, "warning")
        self.fortuneChanged.emit()

    @Property("QVariantMap", notify=factChanged)
    def fact(self) -> Dict[str, Any]:  # noqa: N802
        return self._fact

    @Slot()
    def loadFact(self) -> None:  # noqa: N802
        self._set_fact(False)

    @Slot()
    def randomFact(self) -> None:  # noqa: N802
        self._set_fact(True)

    def _set_fact(self, random: bool) -> None:
        if self._session is None:
            return
        from services.tool_service import MC_FACTS

        service = self._session.service
        index = service.random_fact_index() if random else service.fact_of_the_day()
        self._fact = {
            "index": int(index) + 1,
            "indexZero": int(index),
            "total": len(MC_FACTS),
            "text": MC_FACTS[index],
            "tagKey": "tool_fact_random_title" if random else "tool_fact_today",
        }
        self.factChanged.emit()

    # ─── MC 知识问答 ────────────────────────────────────────

    @Property(bool, notify=quizChanged)
    def quizToken(self) -> bool:  # noqa: N802
        return self._quiz_token

    @Property(bool, notify=quizChanged)
    def quizBusy(self) -> bool:  # noqa: N802
        return self._quiz_busy

    @Property(bool, notify=quizChanged)
    def quizRefilling(self) -> bool:  # noqa: N802
        return self._quiz_refilling

    @Property(str, notify=quizChanged)
    def quizError(self) -> str:  # noqa: N802
        return self._quiz_error

    @Property(str, notify=quizChanged)
    def quizNotice(self) -> str:  # noqa: N802
        return self._quiz_notice

    @Property("QVariantMap", notify=quizChanged)
    def quizCurrent(self) -> Dict[str, Any]:  # noqa: N802
        return self._quiz_current

    @Property(int, notify=quizChanged)
    def quizRemaining(self) -> int:  # noqa: N802
        return self._quiz_remaining

    @Property(bool, notify=quizChanged)
    def quizHasQuestions(self) -> bool:  # noqa: N802
        return self._quiz_has_questions

    @Property(bool, notify=quizChanged)
    def quizAnswered(self) -> bool:  # noqa: N802
        return self._quiz_answered

    @Property("QVariantMap", notify=quizChanged)
    def quizResult(self) -> Dict[str, Any]:  # noqa: N802
        return self._quiz_result

    @Property(bool, notify=quizChanged)
    def quizNeedsRefill(self) -> bool:  # noqa: N802
        return self._quiz_needs_refill

    @Slot()
    def activateQuiz(self) -> None:  # noqa: N802
        if self._session is None or self._quiz_busy or self._quiz_refilling:
            return
        snapshot = self._session.load_quiz()
        self._apply_quiz(snapshot)
        self._quiz_notice = ""
        self.quizChanged.emit()
        if self._quiz_token and self._quiz_needs_refill:
            self._refill_quiz()

    @Slot()
    def generateQuiz(self) -> None:  # noqa: N802
        session = self._session
        if session is None or self._quiz_busy or self._quiz_refilling:
            return
        if not session.quiz_token():
            self._quiz_token = False
            self._quiz_error = _("tool_quiz_not_logged_in")
            self.quizChanged.emit()
            return
        self._quiz_token_id += 1
        token = self._quiz_token_id
        self._quiz_busy = True
        self._quiz_error = ""
        self._quiz_notice = ""
        self.quizChanged.emit()

        def work() -> Dict[str, Any]:
            return session.generate_quiz()

        self._run("tools.quiz.generate", work, "quiz-generate-done", token)

    @Slot(int)
    def answerQuizChoice(self, index: int) -> None:  # noqa: N802
        if self._session is None or self._quiz_busy:
            return
        snapshot = self._session.answer_choice(int(index))
        self._apply_quiz(snapshot)
        self.quizChanged.emit()

    @Slot(str)
    def answerQuizText(self, text: str) -> None:  # noqa: N802
        if self._session is None or self._quiz_busy:
            return
        snapshot = self._session.answer_text(str(text or ""))
        self._apply_quiz(snapshot)
        self.quizChanged.emit()

    @Slot()
    def nextQuiz(self) -> None:  # noqa: N802
        if self._session is None or self._quiz_busy:
            return
        snapshot = self._session.next_quiz()
        self._apply_quiz(snapshot)
        self.quizChanged.emit()
        if self._quiz_token and self._quiz_needs_refill:
            self._refill_quiz()

    def _refill_quiz(self) -> None:
        session = self._session
        if session is None or self._quiz_refilling or self._quiz_busy:
            return
        if not session.quiz_token() or not self._quiz_needs_refill:
            return
        self._quiz_refilling = True
        self.quizChanged.emit()
        self._quiz_token_id += 1
        token = self._quiz_token_id

        def work() -> Dict[str, Any]:
            return session.refill_quiz()

        self._run("tools.quiz.refill", work, "quiz-refill-done", token)

    def _apply_quiz(self, snapshot: Dict[str, Any]) -> None:
        self._quiz_token = bool(snapshot.get("token", self._quiz_token))
        self._quiz_current = dict(snapshot.get("current", {}) or {})
        self._quiz_remaining = int(snapshot.get("remaining", 0) or 0)
        self._quiz_has_questions = bool(snapshot.get("hasQuestions", False))
        self._quiz_answered = bool(snapshot.get("answered", False))
        self._quiz_result = dict(snapshot.get("result", {}) or {})
        self._quiz_needs_refill = bool(snapshot.get("needsRefill", True))

    # ─── 多线程下载 ─────────────────────────────────────────

    @Property(str, notify=downloadChanged)
    def downloadState(self) -> str:  # noqa: N802
        return self._download_state

    @Property("QVariantMap", notify=downloadChanged)
    def downloadProgress(self) -> Dict[str, Any]:  # noqa: N802
        return self._download_progress

    @Property(str, notify=downloadChanged)
    def downloadError(self) -> str:  # noqa: N802
        return self._download_error

    @Property(str, notify=downloadChanged)
    def downloadTarget(self) -> str:  # noqa: N802
        return self._download_target

    @Property("QVariantMap", notify=downloadChanged)
    def downloadResume(self) -> Dict[str, Any]:  # noqa: N802
        return self._download_resume

    @Property(bool, notify=downloadChanged)
    def downloadHasResume(self) -> bool:  # noqa: N802
        return bool(self._download_resume)

    @Property(int, notify=downloadChanged)
    def downloadThreadsUsed(self) -> int:  # noqa: N802
        return self._download_threads_used

    @Property(bool, notify=downloadChanged)
    def downloadBusy(self) -> bool:  # noqa: N802
        return self._download_state in ("connecting", "downloading", "pausing", "cancelling")

    @Property(bool, notify=downloadChanged)
    def downloadCanPause(self) -> bool:  # noqa: N802
        return self._download_state in ("connecting", "downloading")

    @Property(bool, notify=downloadChanged)
    def downloadCanCancel(self) -> bool:  # noqa: N802
        return self._download_state in ("connecting", "downloading", "pausing")

    @Property(bool, notify=downloadChanged)
    def downloadCanDiscard(self) -> bool:  # noqa: N802
        return self._download_state in ("paused", "error", "cancelled") and not self.downloadBusy

    @Slot()
    def activateDownload(self) -> None:  # noqa: N802
        session = self._session
        if session is None or self.downloadBusy:
            return
        resume = self._decorate_resume(session.load_download_resume())
        if resume:
            self._download_resume = resume
            self._download_target = str(resume.get("dest", "") or "")
            self._download_threads_used = int(resume.get("threads", 0) or 0)
            self._download_state = "paused"
            self._download_error = ""
            self._download_progress = self._progress_map(
                int(resume.get("downloaded", 0) or 0), int(resume.get("total", 0) or 0), 0.0
            )
        elif self._download_state == "paused":
            self._download_state = "idle"
            self._download_resume = {}
            self._download_progress = {}
            self._download_target = ""
            self._download_threads_used = 0
            self._download_error = ""
        self.downloadChanged.emit()

    @Slot(str, str, str)
    def startDownload(self, url: str, user_agent: str, save_dir: str) -> None:  # noqa: N802
        session = self._session
        if session is None or self.downloadBusy:
            return
        url = str(url or "").strip()
        user_agent = str(user_agent or "").strip() or "FMCL/2.0"
        save_dir = str(save_dir or "").strip()
        if not url:
            self._download_abort(_("tool_download_no_url"))
            return
        if not save_dir:
            self._download_abort(_("tool_download_save_dir_error"))
            return
        try:
            prepared = session.prepare_download(url, save_dir, user_agent)
            session.begin_download(url, prepared["dest"], user_agent)
        except DownloadResumeError:
            self._download_abort(_("tool_download_resume_conflict"))
            return
        except Exception as e:  # noqa: BLE001
            self._download_abort(_("tool_download_error", error=str(e)))
            return

        self._download_token += 1
        token = self._download_token
        self._download_url = url
        self._download_ua = user_agent
        self._download_target = str(prepared["dest"])
        self._download_threads_used = int(prepared["threads"])
        self._download_resume = self._decorate_resume(prepared.get("resume") or {})
        self._download_state = "connecting"
        self._download_error = ""
        self._download_progress = {
            "indeterminate": True,
            "fraction": 0.0,
            "currentText": "",
            "totalText": "",
            "speedText": "",
        }
        self._download_pause = threading.Event()
        self._download_cancel = threading.Event()
        pause_event = self._download_pause
        cancel_event = self._download_cancel
        self.downloadChanged.emit()

        last_emit = [0.0]

        def on_progress(downloaded: int, total: int, speed: float) -> None:
            now = time.monotonic()
            if downloaded < total and now - last_emit[0] < 0.1:
                return
            last_emit[0] = now
            self._post(
                {
                    "kind": "download-progress",
                    "token": token,
                    "downloaded": int(downloaded),
                    "total": int(total),
                    "speed": float(speed),
                }
            )

        def work() -> Dict[str, Any]:
            outcome = session.service.download_multi(
                url,
                str(prepared["dest"]),
                user_agent=user_agent,
                threads=int(prepared["threads"]),
                pause_check=pause_event.is_set,
                cancel_check=cancel_event.is_set,
                on_progress=on_progress,
            )
            return {"status": outcome.status, "path": outcome.path, "size": outcome.size}

        def done(result: Any) -> None:
            self._post({"kind": "download-done", "key": "tools.download", "token": token, "result": result})

        def failed(error: Any) -> None:
            self._post({"kind": "download-error", "key": "tools.download", "token": token, "error": str(error)})

        self._submit("tools.download", work, done, failed)

    @Slot()
    def pauseDownload(self) -> None:  # noqa: N802
        if self._download_pause is None or not self.downloadCanPause:
            return
        self._download_pause.set()
        self._download_state = "pausing"
        self.downloadChanged.emit()

    @Slot()
    def cancelDownload(self) -> None:  # noqa: N802
        if self._download_cancel is None or not self.downloadCanCancel:
            return
        self._download_cancel.set()
        self._download_state = "cancelling"
        self.downloadChanged.emit()

    @Slot()
    def discardDownload(self) -> None:  # noqa: N802
        session = self._session
        if session is None or self.downloadBusy:
            return
        dest = str(self._download_target or self._download_resume.get("dest", "") or "")
        if dest:
            session.discard_download(dest)
        self._download_state = "idle"
        self._download_error = ""
        self._download_progress = {}
        self._download_target = ""
        self._download_resume = {}
        self._download_threads_used = 0
        self._download_pause = None
        self._download_cancel = None
        self.downloadChanged.emit()

    def _download_abort(self, message: str) -> None:
        """起始校验 / 建目录失败：不再沿用上一次的 success 状态。"""
        self._download_state = "error"
        self._download_error = message
        self._download_progress = {}
        self._download_target = str(self._download_resume.get("dest", "") or "")
        self._download_pause = None
        self._download_cancel = None
        self.downloadChanged.emit()

    def _progress_map(self, downloaded: int, total: int, speed: float) -> Dict[str, Any]:
        total = max(0, int(total))
        downloaded = max(0, min(int(downloaded), total)) if total else max(0, int(downloaded))
        fraction = (downloaded / total) if total > 0 else 0.0
        speed_text = ""
        if speed > 0:
            speed_text = (self._size_text(int(speed)) + "/s") if self._session is not None else ""
        return {
            "indeterminate": total <= 0,
            "fraction": fraction,
            "downloaded": downloaded,
            "total": total,
            "currentText": self._size_text(downloaded),
            "totalText": self._size_text(total),
            "speedText": speed_text,
        }

    # ─── 任务 / 排队 / 分发 ─────────────────────────────────

    def _run(self, key: str, work: Any, done_kind: str, token: int = 0) -> None:
        error_kind = done_kind.replace("-done", "-error")

        def done(result: Any) -> None:
            self._post({"kind": done_kind, "key": key, "token": token, "result": result})

        def failed(error: Any) -> None:
            self._post({"kind": error_kind, "key": key, "token": token, "error": str(error)})

        self._submit(key, work, done, failed)

    def _submit(self, key: str, work: Any, done: Any, failed: Any) -> None:
        if self._tasks is None:
            try:
                result = work()
            except Exception as e:  # noqa: BLE001
                failed(e)
            else:
                done(result)
            return
        try:
            handle = self._tasks.submit(work, name=key, on_done=done, on_error=failed)
        except Exception as e:  # noqa: BLE001
            failed(e)
            return
        self._handles[key] = handle

    def _post(self, payload: Dict[str, Any]) -> None:
        if self._closing:
            return
        self.posted.emit(payload)

    def _deliver(self, payload: Dict[str, Any]) -> None:
        if self._closing:
            return
        kind = str(payload.get("kind", "") or "")
        key = str(payload.get("key", "") or "")
        if key:
            self._handles.pop(key, None)
        token = int(payload.get("token", 0) or 0)

        if kind == "junk-scan-done":
            if token != self._junk_token:
                return
            self._apply_junk_scan(dict(payload.get("result", {}) or {}))
            return
        if kind == "junk-scan-error":
            if token != self._junk_token:
                return
            self._set_junk_error(_("tool_clean_junk_error", error=str(payload.get("error", ""))))
            return
        if kind == "junk-delete-done":
            if token != self._junk_token:
                return
            paths = {str(item) for item in (payload.get("paths", []) or [])}
            self._apply_junk_delete(dict(payload.get("result", {}) or {}), paths)
            return
        if kind == "junk-delete-error":
            if token != self._junk_token:
                return
            self._junk_state = "ready"
            self._junk_notice = _("tool_clean_junk_error", error=str(payload.get("error", "")))
            self._junk_notice_level = "error"
            self.junkChanged.emit()
            return

        if kind == "port-test-done":
            if token != self._port_token:
                return
            result = dict(payload.get("result", {}) or {})
            row = {
                "host": str(result.get("host", "") or ""),
                "port": int(result.get("port", 0) or 0),
                "open": bool(result.get("open", False)),
                "latency": float(result.get("latency", 0.0) or 0.0),
                "latencyText": f"{float(result.get('latency', 0.0) or 0.0):g} ms",
                "error": str(result.get("error", "") or ""),
                "statusKey": "tool_port_open" if result.get("open") else "tool_port_closed",
            }
            row["label"] = f"{row['host']}:{row['port']}"
            self._port_history.insert(0, row)
            self._port_history = self._port_history[:10]
            self._port_busy = False
            self._port_action = ""
            self._port_error = ""
            self.portChanged.emit()
            return
        if kind == "port-test-error":
            if token != self._port_token:
                return
            self._port_busy = False
            self._port_action = ""
            self._port_error = _("tool_port_test_error", error=str(payload.get("error", "")))
            self.portChanged.emit()
            return
        if kind == "port-peek-done":
            if token != self._port_token:
                return
            result = dict(payload.get("result", {}) or {})
            self._port_busy = False
            self._port_action = ""
            if result.get("error"):
                self._server_info = {}
                self._port_error = _("tool_peek_error", error=str(result.get("error", "")))
            else:
                self._server_info = result
                self._port_error = ""
            self.portChanged.emit()
            return
        if kind == "port-peek-error":
            if token != self._port_token:
                return
            self._port_busy = False
            self._port_action = ""
            self._server_info = {}
            self._port_error = _("tool_peek_error", error=str(payload.get("error", "")))
            self.portChanged.emit()
            return

        if kind == "hash-done":
            if token != self._hash_token:
                return
            self._hash_busy = False
            self._hash_error = ""
            self._hash_result = dict(payload.get("result", {}) or {})
            self.hashChanged.emit()
            return
        if kind == "hash-error":
            if token != self._hash_token:
                return
            self._hash_busy = False
            self._hash_result = {}
            self._hash_error = _("tool_hash_calc_error", error=str(payload.get("error", "")))
            self.hashChanged.emit()
            return

        if kind == "quiz-generate-done":
            if token != self._quiz_token_id:
                return
            result = dict(payload.get("result", {}) or {})
            self._quiz_busy = False
            self._quiz_error = ""
            self._apply_quiz(dict(result.get("snapshot", {}) or {}))
            count = int(result.get("count", 0) or 0)
            self._quiz_notice = _("tool_quiz_generated", count=count)
            self.quizChanged.emit()
            self.statusMessage.emit(self._quiz_notice, "success")
            if self._quiz_token and self._quiz_needs_refill:
                self._refill_quiz()
            return
        if kind == "quiz-generate-error":
            if token != self._quiz_token_id:
                return
            self._quiz_busy = False
            self._quiz_error = _("tool_quiz_gen_error", error=str(payload.get("error", "")))
            self.quizChanged.emit()
            return
        if kind == "quiz-refill-done":
            if token != self._quiz_token_id:
                return
            result = dict(payload.get("result", {}) or {})
            self._quiz_refilling = False
            self._apply_quiz(dict(result.get("snapshot", {}) or {}))
            count = int(result.get("count", 0) or 0)
            if count > 0:
                self._quiz_notice = _("tool_quiz_auto_refilled", count=count)
                self.statusMessage.emit(self._quiz_notice, "success")
            self.quizChanged.emit()
            return
        if kind == "quiz-refill-error":
            if token != self._quiz_token_id:
                return
            self._quiz_refilling = False
            self.quizChanged.emit()
            self.statusMessage.emit(_("tool_quiz_gen_error", error=str(payload.get("error", ""))), "warning")
            return

        if kind == "download-progress":
            if token != self._download_token:
                return
            if self._download_state == "connecting":
                self._download_state = "downloading"
            self._download_progress = self._progress_map(
                int(payload.get("downloaded", 0) or 0),
                int(payload.get("total", 0) or 0),
                float(payload.get("speed", 0.0) or 0.0),
            )
            self.downloadChanged.emit()
            return
        if kind == "download-done":
            if token != self._download_token:
                return
            result = dict(payload.get("result", {}) or {})
            self._finish_download(str(result.get("status", "success")), result)
            return
        if kind == "download-error":
            if token != self._download_token:
                return
            self._fail_download(str(payload.get("error", "")))
            return

    def _finish_download(self, status: str, result: Dict[str, Any]) -> None:
        size = int(result.get("size", 0) or 0)
        path = str(result.get("path", "") or self._download_target)
        if status == "success":
            try:
                actual = os.path.getsize(path) if os.path.isfile(path) else -1
            except OSError:
                actual = -1
            if actual != size:
                if self._session is not None:
                    self._session.clear_download_marker()
                self._download_state = "error"
                self._download_error = _("tool_download_file_missing")
                self._download_target = path
                self._download_resume = {}
                self._download_progress = {}
                self.statusMessage.emit(self._download_error, "error")
                self.downloadChanged.emit()
                return
        if self._session is not None:
            try:
                self._session.finish_download(status, path, self._download_url, self._download_ua)
            except Exception as e:  # noqa: BLE001
                logger.warning("更新下载恢复标记失败: %s", e)
        if status == "success":
            self._download_state = "success"
            self._download_error = ""
            self._download_target = path
            self._download_resume = {}
            self._download_progress = self._progress_map(size, size, 0.0)
            self.statusMessage.emit(_("tool_download_success", path=path), "success")
        elif status == "paused":
            self._download_state = "paused"
            self._download_error = ""
            self._download_resume = self._load_resume()
            total = int(self._download_resume.get("total", size) or size)
            downloaded = int(self._download_resume.get("downloaded", 0) or 0)
            self._download_progress = self._progress_map(downloaded, total, 0.0)
            self.statusMessage.emit(_("tool_download_paused"), "info")
        elif status == "cancelled":
            self._download_state = "cancelled"
            self._download_error = ""
            self._download_resume = {}
            self._download_progress = {}
            self.statusMessage.emit(_("tool_download_cancelled"), "info")
        self.downloadChanged.emit()

    def _fail_download(self, error: str) -> None:
        if error == "cancelled":
            if self._session is not None:
                try:
                    self._session.finish_download(
                        "cancelled", self._download_target, self._download_url, self._download_ua
                    )
                except Exception:  # noqa: BLE001
                    pass
            self._download_state = "cancelled"
            self._download_error = ""
            self._download_resume = {}
            self._download_progress = {}
            self.statusMessage.emit(_("tool_download_cancelled"), "info")
            self.downloadChanged.emit()
            return
        if self._session is not None:
            try:
                self._session.finish_download("error", self._download_target, self._download_url, self._download_ua)
            except Exception:  # noqa: BLE001
                pass
        self._download_state = "error"
        self._download_error = _("tool_download_error", error=error or _("tool_peek_unknown_error"))
        self._download_resume = self._load_resume()
        self.downloadChanged.emit()

    def _load_resume(self) -> Dict[str, Any]:
        if self._session is None:
            return {}
        try:
            return self._decorate_resume(self._session.load_download_resume())
        except Exception:  # noqa: BLE001
            return {}

    def _decorate_resume(self, info: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not info:
            return {}
        data = dict(info)
        data["downloadedText"] = self._size_text(int(data.get("downloaded", 0) or 0))
        data["totalText"] = self._size_text(int(data.get("total", 0) or 0))
        return data
