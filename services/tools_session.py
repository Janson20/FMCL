"""工具箱会话层。

承载 QML 工具箱页需要的**状态与持久化**，但不碰 Qt、不碰界面：

* 今日人品历史：按日期去重，最近 60 天，原子写 `data/tools/fortune_history.json`；
* MC 知识问答：题库读取/保存、当前题、判分、生成与自动补充；
* 多线程下载：恢复标记 `data/tools/download_resume.json`、落盘路径解析、
  线程数读取、下载完成/取消/暂停后的标记收尾。

算法与 IO 仍然全部走 `services.tool_service.ToolService`。本模块只做状态编排，
不重新实现扫描、哈希、协议解析或分段下载。

线程约定：所有方法都是同步阻塞的，由 `app/bridges/tools_bridge.py` 放到
`TaskRunner` 的工作线程里调用；本模块不创建线程，也不发信号。
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services.tool_service import DownloadResumeError, ToolService

logger = logging.getLogger(__name__)

MAX_FORTUNE_HISTORY = 60
QUIZ_REFILL_THRESHOLD = 5
MAX_DOWNLOAD_THREADS = 32

Chat = Callable[[List[Dict[str, str]]], Any]


class ToolsSession:
    """工具箱的 QML 无关状态层。"""

    def __init__(
        self,
        config: Any = None,
        *,
        service: Optional[ToolService] = None,
        chat: Optional[Chat] = None,
        data_dir: Optional[Path] = None,
    ) -> None:
        self._config = config
        self._service = service or ToolService()
        self._chat = chat
        self._data_dir = Path(data_dir) if data_dir is not None else self._resolve_data_dir()
        self._fortune_history: Optional[List[Dict[str, Any]]] = None
        self._quiz_questions: List[Dict[str, Any]] = []
        self._quiz_index = 0
        self._quiz_answered = False
        self._quiz_result: Dict[str, Any] = {}

    # ─── 配置 / 路径 ──────────────────────────────────────────

    def _config_value(self, name: str, default: Any = None) -> Any:
        if self._config is None:
            return default
        try:
            return getattr(self._config, name)
        except Exception:  # noqa: BLE001 - 配置缺失只影响该工具
            return default

    def _resolve_data_dir(self) -> Path:
        base = self._config_value("base_dir", None)
        root = Path(base) if base else Path.cwd()
        return root / "data" / "tools"

    @property
    def service(self) -> ToolService:
        return self._service

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def default_scan_dir(self) -> str:
        value = self._config_value("minecraft_dir", None)
        if value:
            return str(value)
        return str(Path.cwd() / ".minecraft")

    def download_threads(self) -> int:
        try:
            value = int(self._config_value("download_threads", 4) or 4)
        except (TypeError, ValueError):
            value = 4
        return max(1, min(MAX_DOWNLOAD_THREADS, value))

    def default_download_dir(self) -> str:
        downloads = Path.home() / "Downloads"
        if downloads.is_dir():
            return str(downloads)
        base = self._config_value("base_dir", Path.cwd()) or Path.cwd()
        return str(Path(base) / "downloads")

    def format_size(self, bytes_count: int) -> str:
        return self._service.format_size(bytes_count)

    # ─── 今日人品历史 ────────────────────────────────────────

    @property
    def fortune_history_path(self) -> Path:
        return self._data_dir / "fortune_history.json"

    def load_fortune_history(self) -> List[Dict[str, Any]]:
        if self._fortune_history is not None:
            return [dict(item) for item in self._fortune_history]
        raw = self._read_json(self.fortune_history_path)
        entries: List[Dict[str, Any]] = []
        if isinstance(raw, dict):
            rows = raw.get("entries", [])
            if isinstance(rows, list):
                for item in rows:
                    if not isinstance(item, dict):
                        continue
                    day = str(item.get("date", "") or "").strip()
                    level_key = str(item.get("levelKey", item.get("level_key", "")) or "").strip()
                    try:
                        value = int(item.get("value", -1))
                    except (TypeError, ValueError):
                        continue
                    if not day or not level_key or value < 0 or value > 100:
                        continue
                    entries.append({"date": day, "value": value, "levelKey": level_key})
        entries.sort(key=lambda item: item["date"], reverse=True)
        self._fortune_history = entries[:MAX_FORTUNE_HISTORY]
        return [dict(item) for item in self._fortune_history]

    def check_fortune(self, today: Optional[str] = None) -> Dict[str, Any]:
        result = self._service.daily_fortune(today)
        entry = {"date": result.today, "value": result.value, "levelKey": result.level_key}
        history = [item for item in self.load_fortune_history() if item["date"] != result.today]
        history.insert(0, entry)
        history = history[:MAX_FORTUNE_HISTORY]
        self._fortune_history = history
        saved, error = self._save_fortune_history(history)
        return {"result": dict(entry), "history": [dict(item) for item in history], "saved": saved, "saveError": error}

    def _save_fortune_history(self, history: List[Dict[str, Any]]) -> tuple[bool, str]:
        payload = {"version": 1, "entries": history}
        try:
            self._write_json(self.fortune_history_path, payload)
            return True, ""
        except Exception as e:  # noqa: BLE001 - 历史落盘失败不该挡住结果展示
            logger.error("保存人品历史失败: %s", e)
            return False, str(e)

    # ─── MC 知识问答 ─────────────────────────────────────────

    @property
    def quiz_path(self) -> Path:
        minecraft_dir = self._config_value("minecraft_dir", None)
        if minecraft_dir:
            return Path(minecraft_dir).parent / "quiz.json"
        return Path.cwd() / "quiz.json"

    def quiz_token(self) -> str:
        return str(self._config_value("jdz_token", "") or "").strip()

    def load_quiz(self) -> Dict[str, Any]:
        loaded = self._service.load_quiz_questions(self.quiz_path)
        if loaded is not None:
            self._quiz_questions = [dict(item) for item in loaded if isinstance(item, dict)]
            self._quiz_index = 0
            self._quiz_answered = False
            self._quiz_result = {}
        return self.quiz_snapshot()

    def _current_question(self) -> Optional[Dict[str, Any]]:
        if not self._quiz_questions:
            return None
        if self._quiz_index < 0:
            self._quiz_index = 0
        if self._quiz_index >= len(self._quiz_questions):
            self._quiz_index = len(self._quiz_questions) - 1
        return self._quiz_questions[self._quiz_index]

    def quiz_snapshot(self) -> Dict[str, Any]:
        question = self._current_question()
        current: Dict[str, Any] = {}
        if question is not None:
            options = question.get("options", [])
            if not isinstance(options, list):
                options = []
            current = {
                "id": question.get("id", self._quiz_index + 1),
                "question": str(question.get("question", "") or ""),
                "options": [str(item) for item in options],
                "isChoice": bool(options),
            }
        return {
            "hasQuestions": question is not None,
            "remaining": len(self._quiz_questions),
            "index": self._quiz_index,
            "current": current,
            "answered": bool(self._quiz_answered),
            "result": dict(self._quiz_result),
            "token": bool(self.quiz_token()),
            "needsRefill": len(self._quiz_questions) <= QUIZ_REFILL_THRESHOLD,
        }

    def answer_choice(self, index: int) -> Dict[str, Any]:
        question = self._current_question()
        if question is None or self._quiz_answered:
            return self.quiz_snapshot()
        options = question.get("options", [])
        if not isinstance(options, list):
            options = []
        selected = str(options[index]) if 0 <= index < len(options) else ""
        correct = str(question.get("answer", "") or "")
        is_correct = self._service.check_choice_answer(selected, correct)
        self._quiz_result = {
            "correct": bool(is_correct),
            "correctAnswer": correct,
            "selected": selected,
            "explanation": str(question.get("explanation", "") or ""),
        }
        self._quiz_answered = True
        return self.quiz_snapshot()

    def answer_text(self, text: str) -> Dict[str, Any]:
        question = self._current_question()
        if question is None or self._quiz_answered:
            return self.quiz_snapshot()
        answer = str(text or "").strip()
        if not answer:
            return self.quiz_snapshot()
        correct = str(question.get("answer", "") or "")
        is_correct = self._service.check_text_answer(answer, correct)
        self._quiz_result = {
            "correct": bool(is_correct),
            "correctAnswer": correct,
            "selected": answer,
            "explanation": str(question.get("explanation", "") or ""),
        }
        self._quiz_answered = True
        return self.quiz_snapshot()

    def next_quiz(self) -> Dict[str, Any]:
        if not self._quiz_questions:
            return self.quiz_snapshot()
        self._service.drop_question(self._quiz_questions, self._quiz_index)
        self._save_quiz()
        if not self._quiz_questions:
            self._quiz_index = 0
        elif self._quiz_index >= len(self._quiz_questions):
            self._quiz_index = len(self._quiz_questions) - 1
        self._quiz_answered = False
        self._quiz_result = {}
        return self.quiz_snapshot()

    def generate_quiz(self) -> Dict[str, Any]:
        token = self.quiz_token()
        questions = self._service.generate_quiz(token, chat=self._chat or self._default_chat)
        reindexed = self._service.reindex_questions(questions)
        self._quiz_questions = [dict(item) for item in reindexed if isinstance(item, dict)]
        self._quiz_index = 0
        self._quiz_answered = False
        self._quiz_result = {}
        self._save_quiz()
        return {"count": len(self._quiz_questions), "snapshot": self.quiz_snapshot()}

    def refill_quiz(self) -> Dict[str, Any]:
        if len(self._quiz_questions) > QUIZ_REFILL_THRESHOLD:
            return {"count": 0, "snapshot": self.quiz_snapshot()}
        token = self.quiz_token()
        questions = self._service.generate_quiz(token, chat=self._chat or self._default_chat)
        reindexed = self._service.merge_generated_questions(self._quiz_questions, questions)
        self._quiz_questions.extend([dict(item) for item in reindexed if isinstance(item, dict)])
        self._save_quiz()
        return {"count": len(reindexed), "snapshot": self.quiz_snapshot()}

    def _save_quiz(self) -> None:
        try:
            self._service.save_quiz_questions(self.quiz_path, self._quiz_questions)
        except Exception as e:  # noqa: BLE001 - 保存失败只影响下次启动，不打断答题
            logger.error("保存题库失败: %s", e)

    def _default_chat(self, messages: List[Dict[str, str]]) -> Any:
        from services.agent.providers.jingdu import JingduProvider

        provider = JingduProvider(api_key=self.quiz_token())
        return provider.chat(messages)

    # ─── 下载恢复 ────────────────────────────────────────────

    @property
    def download_marker_path(self) -> Path:
        return self._data_dir / "download_resume.json"

    def load_download_resume(self) -> Optional[Dict[str, Any]]:
        marker = self._read_json(self.download_marker_path)
        if not isinstance(marker, dict):
            return None
        dest = str(marker.get("dest", "") or "")
        url = str(marker.get("url", "") or "")
        if not dest or not url:
            self.clear_download_marker()
            return None
        state = self._service.download_resume_state(dest)
        if state is None:
            self.clear_download_marker()
            return None
        if str(state.get("url", "") or "") and str(state.get("url")) != url:
            self.clear_download_marker()
            return None
        if str(state.get("dest", "") or "") and str(state.get("dest")) != dest:
            self.clear_download_marker()
            return None
        return {
            "url": url,
            "dest": dest,
            "userAgent": str(marker.get("userAgent", "FMCL/2.0") or "FMCL/2.0"),
            "threads": int(state.get("threads", self.download_threads()) or self.download_threads()),
            "total": int(state.get("total", 0) or 0),
            "downloaded": int(state.get("downloaded", 0) or 0),
            "partCount": int(state.get("partCount", 0) or 0),
            "completeParts": int(state.get("completeParts", 0) or 0),
        }

    def prepare_download(self, url: str, save_dir: str, user_agent: str) -> Dict[str, Any]:
        target_dir = Path(save_dir)
        if not target_dir.is_dir():
            self._service.ensure_directory(str(target_dir))
        dest = self._service.resolve_download_target(url, "", str(target_dir))
        resume = self.load_download_resume()
        if resume is not None and (resume["dest"] != dest or resume["url"] != url):
            raise DownloadResumeError("已有未完成的下载，请先继续或放弃")
        return {
            "url": url,
            "dest": dest,
            "saveDir": str(target_dir),
            "userAgent": user_agent or "FMCL/2.0",
            "threads": int(resume["threads"]) if resume else self.download_threads(),
            "resume": resume,
        }

    def begin_download(self, url: str, dest: str, user_agent: str) -> None:
        self._write_json(
            self.download_marker_path,
            {
                "version": 1,
                "url": url,
                "dest": dest,
                "userAgent": user_agent or "FMCL/2.0",
                "updatedAt": datetime.now().isoformat(timespec="seconds"),
            },
        )

    def finish_download(self, status: str, dest: str, url: str, user_agent: str) -> None:
        if status == "success" or status == "cancelled":
            self.clear_download_marker()
            return
        self.begin_download(url, dest, user_agent)

    def discard_download(self, dest: str) -> None:
        self._service.cleanup_download_parts(dest)
        self.clear_download_marker()

    def clear_download_marker(self) -> None:
        try:
            os.remove(self.download_marker_path)
        except OSError:
            pass

    # ─── 小工具 ──────────────────────────────────────────────

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
