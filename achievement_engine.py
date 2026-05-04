"""成就系统核心引擎 - Achievement Engine

RESTful-style internal API for achievement tracking and management.
Data stored in SQLite at <base_dir>/achievements.db, structured for future cloud sync.
"""

import sqlite3
import time
import threading
from pathlib import Path
from typing import Dict, List, Optional, Callable, Any
from dataclasses import dataclass

from logzero import logger

from achievement_defs import (
    AchievementDef, AchievementCategory, CATEGORY_ORDER,
    ACHIEVEMENTS, CATEGORY_META,
)


@dataclass
class AchievementProgress:
    achievement_id: str
    current_value: int = 0
    stage: int = 0
    unlocked_at: Optional[float] = None
    updated_at: float = 0.0
    notified: int = 0


class AchievementEngine:
    """成就系统核心引擎"""

    DB_FILENAME = "achievements.db"

    def __init__(self, db_dir: Path):
        self._db_path = db_dir / self.DB_FILENAME
        self._lock = threading.Lock()
        self._unlock_callbacks: List[Callable[[AchievementDef, int, str], None]] = []
        self._defs_by_id: Dict[str, AchievementDef] = {d.achievement_id: d for d in ACHIEVEMENTS}
        self._init_db()

    def _init_db(self):
        with self._lock:
            db_dir = self._db_path.parent
            db_dir.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA synchronous=NORMAL")
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS achievement_progress (
                        achievement_id TEXT PRIMARY KEY,
                        current_value INTEGER DEFAULT 0,
                        stage INTEGER DEFAULT 0,
                        unlocked_at REAL,
                        updated_at REAL,
                        notified INTEGER DEFAULT 0,
                        sync_timestamp REAL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS achievement_unlocks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        achievement_id TEXT NOT NULL,
                        stage INTEGER NOT NULL,
                        unlocked_at REAL NOT NULL,
                        notified INTEGER DEFAULT 0,
                        sync_timestamp REAL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS achievement_state (
                        key TEXT PRIMARY KEY,
                        value TEXT
                    )
                """)
                conn.commit()

    def register_unlock_callback(self, callback: Callable[[AchievementDef, int, str], None]):
        self._unlock_callbacks.append(callback)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ═══════════ RESTful-style API ═══════════

    def get_all(self) -> List[Dict[str, Any]]:
        """GET /achievements - 获取所有成就及进度"""
        result = []
        with self._lock:
            conn = self._get_conn()
            try:
                for cat in CATEGORY_ORDER:
                    cat_defs = [d for d in ACHIEVEMENTS if d.category == cat]
                    cat_items = [self._build_item(d, conn) for d in cat_defs]
                    result.append({
                        "category": cat.value,
                        "category_meta": CATEGORY_META[cat],
                        "achievements": cat_items,
                    })
            finally:
                conn.close()
        return result

    def get_by_category(self, category: AchievementCategory) -> Dict[str, Any]:
        """GET /achievements/{category} - 按分类获取成就"""
        cat_defs = [d for d in ACHIEVEMENTS if d.category == category]
        with self._lock:
            conn = self._get_conn()
            try:
                items = [self._build_item(d, conn) for d in cat_defs]
            finally:
                conn.close()
        return {"category": category.value, "category_meta": CATEGORY_META[category], "achievements": items}

    def get_one(self, achievement_id: str) -> Optional[Dict[str, Any]]:
        """GET /achievements/{id} - 获取单个成就"""
        d = self._defs_by_id.get(achievement_id)
        if d is None:
            return None
        with self._lock:
            conn = self._get_conn()
            try:
                return self._build_item(d, conn)
            finally:
                conn.close()

    def get_progress(self, achievement_id: str) -> Optional[AchievementProgress]:
        """获取单个成就的进度对象"""
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT * FROM achievement_progress WHERE achievement_id = ?", (achievement_id,)
                ).fetchone()
                if row:
                    return AchievementProgress(
                        achievement_id=row["achievement_id"],
                        current_value=row["current_value"],
                        stage=row["stage"],
                        unlocked_at=row["unlocked_at"],
                        updated_at=row["updated_at"],
                        notified=row["notified"],
                    )
                return None
            finally:
                conn.close()

    def update_progress(self, achievement_id: str, value: int = 1,
                        trigger_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """PUT/PATCH /achievements/{id}/progress - 更新成就进度"""
        d = self._defs_by_id.get(achievement_id)
        if d is None:
            logger.warning(f"未知的成就ID: {achievement_id}")
            return None

        ttype = trigger_type or d.trigger_type
        with self._lock:
            conn = self._get_conn()
            try:
                now = time.time()
                row = conn.execute(
                    "SELECT * FROM achievement_progress WHERE achievement_id = ?", (achievement_id,)
                ).fetchone()

                if row:
                    old_value = row["current_value"]
                    old_stage = row["stage"]
                    new_value = value if ttype == "set" else old_value + value
                    conn.execute(
                        "UPDATE achievement_progress SET current_value = ?, updated_at = ? WHERE achievement_id = ?",
                        (new_value, now, achievement_id)
                    )
                else:
                    old_stage = 0
                    new_value = value
                    conn.execute(
                        "INSERT INTO achievement_progress (achievement_id, current_value, stage, updated_at, sync_timestamp) VALUES (?, ?, 0, ?, ?)",
                        (achievement_id, new_value, now, now)
                    )

                new_stage = self._calc_stage(d, new_value)
                unlocked_new_stages = list(range(old_stage + 1, new_stage + 1))

                if unlocked_new_stages:
                    conn.execute(
                        "UPDATE achievement_progress SET stage = ?, unlocked_at = COALESCE(unlocked_at, ?) WHERE achievement_id = ?",
                        (new_stage, now, achievement_id)
                    )
                    for stage_num in unlocked_new_stages:
                        conn.execute(
                            "INSERT INTO achievement_unlocks (achievement_id, stage, unlocked_at, sync_timestamp) VALUES (?, ?, ?, ?)",
                            (achievement_id, stage_num, now, now)
                        )

                conn.commit()
                result = self._build_item(d, conn)

                if unlocked_new_stages:
                    self._notify_unlock(d, unlocked_new_stages[-1], result.get("current_stage_name", ""))

                return result
            finally:
                conn.close()

    def check_and_unlock(self, achievement_id: str, condition_met: bool) -> Optional[Dict[str, Any]]:
        """条件检查型成就"""
        d = self._defs_by_id.get(achievement_id)
        if d is None or not condition_met:
            return None
        existing = self.get_progress(achievement_id)
        if existing and existing.stage >= len(d.stages):
            return None
        return self.update_progress(achievement_id, value=1, trigger_type="increment")

    def reset(self, achievement_id: str) -> bool:
        """DELETE /achievements/{id} - 重置成就进度"""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM achievement_progress WHERE achievement_id = ?", (achievement_id,))
                conn.execute("DELETE FROM achievement_unlocks WHERE achievement_id = ?", (achievement_id,))
                conn.commit()
                return True
            finally:
                conn.close()

    def reset_all(self) -> bool:
        """重置所有成就进度"""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM achievement_progress")
                conn.execute("DELETE FROM achievement_unlocks")
                conn.commit()
                return True
            finally:
                conn.close()

    def get_stats(self) -> Dict[str, Any]:
        """获取成就统计信息"""
        with self._lock:
            conn = self._get_conn()
            try:
                total = len(ACHIEVEMENTS)
                unlocked = conn.execute(
                    "SELECT COUNT(DISTINCT achievement_id) FROM achievement_progress WHERE stage > 0"
                ).fetchone()[0]
                max_stages = sum(len(d.stages) for d in ACHIEVEMENTS)
                unlocked_stages = conn.execute(
                    "SELECT COALESCE(SUM(stage), 0) FROM achievement_progress"
                ).fetchone()[0]
                return {
                    "total_achievements": total,
                    "unlocked_achievements": unlocked,
                    "total_stages": max_stages,
                    "unlocked_stages": unlocked_stages,
                    "completion_percent": round(unlocked / total * 100, 1) if total > 0 else 0,
                }
            finally:
                conn.close()

    def checkin(self) -> Optional[Dict[str, Any]]:
        """每日签到"""
        today = time.strftime("%Y-%m-%d")
        with self._lock:
            conn = self._get_conn()
            try:
                row = conn.execute(
                    "SELECT value FROM achievement_state WHERE key = 'last_checkin_date'"
                ).fetchone()
                last_date = row["value"] if row else None

                if last_date == today:
                    conn.close()
                    return None

                if last_date:
                    try:
                        last_dt = time.strptime(last_date, "%Y-%m-%d")
                        today_dt = time.strptime(today, "%Y-%m-%d")
                        diff_days = (time.mktime(today_dt) - time.mktime(last_dt)) / 86400
                    except (ValueError, OverflowError):
                        diff_days = 999
                else:
                    diff_days = 999

                streak = 1
                if diff_days == 1:
                    streak_row = conn.execute(
                        "SELECT value FROM achievement_state WHERE key = 'checkin_streak'"
                    ).fetchone()
                    if streak_row:
                        streak = int(streak_row["value"]) + 1

                conn.execute("INSERT OR REPLACE INTO achievement_state (key, value) VALUES (?, ?)", ("last_checkin_date", today))
                conn.execute("INSERT OR REPLACE INTO achievement_state (key, value) VALUES (?, ?)", ("checkin_streak", str(streak)))
                conn.commit()
                conn.close()
                return self.update_progress("advanced_checkin", value=streak, trigger_type="set")
            except Exception as e:
                logger.error(f"签到异常: {e}")
                try:
                    conn.close()
                except Exception:
                    pass
                return None

    def get_sync_data(self) -> Dict[str, Any]:
        """预留：获取云同步数据"""
        with self._lock:
            conn = self._get_conn()
            try:
                progress = [dict(row) for row in conn.execute("SELECT * FROM achievement_progress").fetchall()]
                unlocks = [dict(row) for row in conn.execute("SELECT * FROM achievement_unlocks").fetchall()]
                state = [dict(row) for row in conn.execute("SELECT * FROM achievement_state").fetchall()]
                return {"progress": progress, "unlocks": unlocks, "state": state, "version": 1}
            finally:
                conn.close()

    def apply_sync_data(self, data: Dict[str, Any]) -> bool:
        """预留：应用云同步数据"""
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute("DELETE FROM achievement_progress")
                conn.execute("DELETE FROM achievement_unlocks")
                conn.execute("DELETE FROM achievement_state")
                for p in data.get("progress", []):
                    conn.execute(
                        "INSERT INTO achievement_progress (achievement_id, current_value, stage, unlocked_at, updated_at, notified, sync_timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (p["achievement_id"], p["current_value"], p["stage"], p.get("unlocked_at"), p.get("updated_at", time.time()), p.get("notified", 0), time.time())
                    )
                for u in data.get("unlocks", []):
                    conn.execute(
                        "INSERT INTO achievement_unlocks (achievement_id, stage, unlocked_at, notified, sync_timestamp) VALUES (?, ?, ?, ?, ?)",
                        (u["achievement_id"], u["stage"], u["unlocked_at"], u.get("notified", 0), time.time())
                    )
                for s in data.get("state", []):
                    conn.execute("INSERT OR REPLACE INTO achievement_state (key, value) VALUES (?, ?)", (s["key"], s["value"]))
                conn.commit()
                return True
            except Exception as e:
                logger.error(f"应用同步数据失败: {e}")
                conn.rollback()
                return False
            finally:
                conn.close()

    def get_unnotified_unlocks(self) -> List[Dict[str, Any]]:
        """获取未通知的解锁记录"""
        with self._lock:
            conn = self._get_conn()
            try:
                rows = conn.execute(
                    "SELECT * FROM achievement_unlocks WHERE notified = 0 ORDER BY unlocked_at ASC"
                ).fetchall()
                results = []
                for row in rows:
                    d = self._defs_by_id.get(row["achievement_id"])
                    if d:
                        stage = row["stage"]
                        name = d.stage_names[stage - 1] if 0 < stage <= len(d.stage_names) else d.i18n_key
                        results.append({
                            "id": row["id"], "achievement_id": row["achievement_id"],
                            "stage": stage, "stage_name": name,
                            "unlocked_at": row["unlocked_at"], "icon": d.icon,
                            "i18n_key": d.i18n_key, "category": d.category.value,
                        })
                return results
            finally:
                conn.close()

    def mark_notified(self, achievement_id: str, stage: int):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.execute(
                    "UPDATE achievement_unlocks SET notified = 1 WHERE achievement_id = ? AND stage = ?",
                    (achievement_id, stage)
                )
                conn.execute("UPDATE achievement_progress SET notified = 1 WHERE achievement_id = ?", (achievement_id,))
                conn.commit()
            finally:
                conn.close()

    def batch_mark_notified(self, unlock_ids: List[int]):
        with self._lock:
            conn = self._get_conn()
            try:
                conn.executemany("UPDATE achievement_unlocks SET notified = 1 WHERE id = ?", [(uid,) for uid in unlock_ids])
                conn.commit()
            finally:
                conn.close()

    def _calc_stage(self, d: AchievementDef, value: int) -> int:
        stage = 0
        for threshold in d.stages:
            if value >= threshold:
                stage += 1
            else:
                break
        return stage

    def _build_item(self, d: AchievementDef, conn: sqlite3.Connection) -> Dict[str, Any]:
        item: Dict[str, Any] = {
            "id": d.achievement_id, "category": d.category.value,
            "i18n_key": d.i18n_key, "desc_i18n_key": d.desc_i18n_key,
            "icon": d.icon, "stages": d.stages, "stage_names": d.stage_names,
            "max_stage": len(d.stages), "trigger_type": d.trigger_type,
        }
        row = conn.execute("SELECT * FROM achievement_progress WHERE achievement_id = ?", (d.achievement_id,)).fetchone()
        if row:
            stage = row["stage"]
            current = row["current_value"]
            item["progress_current"] = current
            item["progress_stage"] = stage
            item["progress_unlocked_at"] = row["unlocked_at"]
            item["progress_notified"] = row["notified"]
            if 0 < stage <= len(d.stage_names):
                item["current_stage_name"] = d.stage_names[stage - 1]
            else:
                item["current_stage_name"] = ""
            if stage < len(d.stages):
                item["next_threshold"] = d.stages[stage]
                item["progress_percent"] = min(100, round(current / d.stages[stage] * 100, 1))
            else:
                item["next_threshold"] = None
                item["progress_percent"] = 100
        else:
            item["progress_current"] = 0
            item["progress_stage"] = 0
            item["progress_unlocked_at"] = None
            item["progress_notified"] = 0
            item["current_stage_name"] = ""
            item["next_threshold"] = d.stages[0] if d.stages else None
            item["progress_percent"] = 0
        return item

    def _notify_unlock(self, d: AchievementDef, stage: int, stage_name: str):
        for cb in self._unlock_callbacks:
            try:
                cb(d, stage, stage_name)
            except Exception as e:
                logger.error(f"成就解锁回调异常: {e}")


_engine_instance: Optional[AchievementEngine] = None


def get_achievement_engine() -> Optional[AchievementEngine]:
    return _engine_instance


def init_achievement_engine(db_dir: Path) -> AchievementEngine:
    global _engine_instance
    _engine_instance = AchievementEngine(db_dir)
    logger.info(f"成就引擎已初始化, 数据库路径: {_engine_instance._db_path}")
    return _engine_instance
