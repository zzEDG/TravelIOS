"""本地 SQLite 行程存储；版本号用于防止并发请求覆盖新状态。"""

import sqlite3
from contextlib import contextmanager
from typing import Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter

from app.models.travel import PlanData, TripState, TripHistoryItem, TripHistoryPage


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / ".runtime" / "trips.sqlite3"


class TripNotFoundError(LookupError):
    pass


class TripExpiredError(LookupError):
    pass


class TripVersionConflictError(ValueError):
    pass


@dataclass(frozen=True)
class TripRecord:
    state: TripState
    latest_plan: PlanData


class TripStore:
    def __init__(self, path: Path | str = DEFAULT_DB_PATH, *, user_id=None):
        self.user_id = str(user_id) if user_id is not None else None
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trips (
                    trip_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL, user_id TEXT
                )
            """)
            if "user_id" not in {row[1] for row in conn.execute("PRAGMA table_info(trips)")}:
                conn.execute("ALTER TABLE trips ADD COLUMN user_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trips_expires_at ON trips(expires_at)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trip_history (
                    trip_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    skill TEXT NOT NULL,
                    city TEXT NOT NULL,
                    title TEXT NOT NULL,
                    origin_name TEXT NOT NULL,
                    state_json TEXT NOT NULL,
                    plan_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (trip_id, version)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_skill_time ON trip_history(skill, updated_at DESC)")
            # 旧数据库只有最新快照：按实际版本迁移，不虚构已被覆盖的版本。
            conn.execute("""
                INSERT OR IGNORE INTO trip_history
                SELECT trip_id, version, json_extract(state_json, '$.current_skill'),
                       json_extract(state_json, '$.city'), json_extract(plan_json, '$.title'),
                       COALESCE(json_extract(state_json, '$.origin_name'), ''),
                       state_json, plan_json, updated_at FROM trips
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _record(row: sqlite3.Row) -> TripRecord:
        return TripRecord(
            state=TripState.model_validate_json(row["state_json"]),
            latest_plan=TypeAdapter(PlanData).validate_json(row["plan_json"]),
        )

    def get(self, trip_id: str, *, allow_expired: bool = False) -> TripRecord:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM trips WHERE trip_id = ? AND user_id IS ?", (trip_id, self.user_id)).fetchone()
        if row is None:
            raise TripNotFoundError("行程不存在")
        record = self._record(row)
        if not allow_expired and record.state.expires_at <= datetime.now(timezone.utc):
            raise TripExpiredError("行程已过期，请新建行程")
        return record

    def get_version(self, trip_id: str, version: int) -> TripRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT h.* FROM trip_history h JOIN trips t ON t.trip_id=h.trip_id WHERE h.trip_id = ? AND h.version = ? AND t.user_id IS ?",
                (trip_id, version, self.user_id),
            ).fetchone()
        if row is None:
            raise TripNotFoundError("这个规划版本不存在")
        return self._record(row)

    def walk_place_preferences(self, trip_id: str, through_version: int,
                               poi_ids: set[str]) -> dict[str, list[str]]:
        """从所选版本及以前的漫步历史恢复旧地点的真实检索来源。"""
        if not poi_ids:
            return {}
        self.get(trip_id, allow_expired=True)
        found = {}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT h.plan_json FROM trip_history h JOIN trips t ON t.trip_id=h.trip_id WHERE h.trip_id = ? AND h.skill = ? AND h.version <= ? "
                "AND t.user_id IS ? ORDER BY h.version DESC", (trip_id, "CITYWALK", through_version, self.user_id),
            )
            for row in rows:
                plan = TypeAdapter(PlanData).validate_json(row["plan_json"])
                for place in plan.places:
                    if place.poi.id in poi_ids and place.poi.id not in found:
                        preferences = place.search_preferences()
                        if preferences:
                            found[place.poi.id] = preferences
                if len(found) == len(poi_ids):
                    break
        return found

    @staticmethod
    def _append_history(conn: sqlite3.Connection, record: TripRecord) -> None:
        state, plan = record.state, record.latest_plan
        conn.execute(
            "INSERT INTO trip_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (state.trip_id, state.state_version, state.current_skill, state.city,
             plan.title, state.origin_name, state.model_dump_json(),
             plan.model_dump_json(), state.updated_at.isoformat()),
        )

    def history(self, *, skill: str | None = None, query: str = "", page: int = 1,
                page_size: int = 5, trip_id: str | None = None) -> TripHistoryPage:
        filters, values = ["t.user_id IS ?"], [self.user_id]
        if trip_id:
            self.get(trip_id, allow_expired=True)
            filters.append("h.trip_id = ?")
            values.append(trip_id)
        else:
            # 每个子系统显示每条行程在该子系统的最后一次规划，切换能力后仍可找到。
            filters.append("NOT EXISTS (SELECT 1 FROM trip_history newer WHERE "
                           "newer.trip_id = h.trip_id AND newer.skill = h.skill AND newer.version > h.version)")
        if skill:
            filters.append("h.skill = ?")
            values.append(skill)
        if query.strip():
            filters.append("instr(lower(h.city || ' ' || h.title || ' ' || h.origin_name), lower(?)) > 0")
            values.append(query.strip())
        where = " AND ".join(filters)
        source = "FROM trip_history h JOIN trips t ON t.trip_id = h.trip_id WHERE " + where
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) " + source, values).fetchone()[0]
            rows = conn.execute(
                "SELECT h.*, t.version AS latest_version, t.expires_at AS latest_expires_at "
                + source + " ORDER BY h.updated_at DESC, h.trip_id DESC, h.version DESC LIMIT ? OFFSET ?",
                [*values, page_size, (page - 1) * page_size],
            ).fetchall()
        now = datetime.now(timezone.utc)
        items = []
        for row in rows:
            record = self._record(row)
            state, plan = record.state, record.latest_plan
            items.append(TripHistoryItem(
                trip_id=state.trip_id, state_version=state.state_version,
                latest_version=row["latest_version"], skill=state.current_skill, city=state.city,
                title=plan.title, summary=plan.summary, origin_name=state.origin_name,
                place_count=len(plan.places), created_at=state.created_at, updated_at=state.updated_at,
                expired=datetime.fromisoformat(row["latest_expires_at"]) <= now,
            ))
        return TripHistoryPage(items=items, total=total, page=page, page_size=page_size)

    def create(self, record: TripRecord) -> None:
        state = record.state
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO trips
                   (trip_id, version, state_json, plan_json, expires_at, created_at, updated_at, user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (state.trip_id, state.state_version, state.model_dump_json(),
                 record.latest_plan.model_dump_json(), state.expires_at.isoformat(),
                 state.created_at.isoformat(), state.updated_at.isoformat(), self.user_id),
            )
            self._append_history(conn, record)

    def replace(self, record: TripRecord, expected_version: int, *, allow_expired: bool = False) -> None:
        state = record.state
        if state.state_version != expected_version + 1:
            raise ValueError("新状态版本号无效")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT version, expires_at FROM trips WHERE trip_id = ? AND user_id IS ?", (state.trip_id, self.user_id)
            ).fetchone()
            if row is None:
                raise TripNotFoundError("行程不存在")
            if not allow_expired and datetime.fromisoformat(row["expires_at"]) <= datetime.now(timezone.utc):
                raise TripExpiredError("行程已过期，请新建行程")
            if row["version"] != expected_version:
                raise TripVersionConflictError("行程已被其他请求更新，请重新读取后再提交")
            conn.execute(
                """UPDATE trips
                   SET version = ?, state_json = ?, plan_json = ?, expires_at = ?, updated_at = ?
                   WHERE trip_id = ? AND user_id IS ?""",
                (state.state_version, state.model_dump_json(),
                 record.latest_plan.model_dump_json(), state.expires_at.isoformat(),
                 state.updated_at.isoformat(), state.trip_id, self.user_id),
            )
            self._append_history(conn, record)

    def delete(self, trip_id: str) -> None:
        """删除自己的整份规划及历史；与保存使用同一事务锁。"""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT trip_id FROM trips WHERE trip_id = ? AND user_id IS ?", (trip_id, self.user_id)).fetchone()
            if row is None:
                raise TripNotFoundError("行程不存在")
            conn.execute("DELETE FROM trip_history WHERE trip_id = ?", (trip_id,))
            conn.execute("DELETE FROM trips WHERE trip_id = ?", (trip_id,))
