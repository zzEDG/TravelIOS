"""PostgreSQL 行程存储，事务性保存当前状态及历史版本。"""
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

import psycopg
from psycopg.rows import dict_row
from pydantic import TypeAdapter

from app.models.travel import PlanData, TripHistoryItem, TripHistoryPage, TripState
from app.models.trip_store import TripExpiredError, TripNotFoundError, TripRecord, TripVersionConflictError

DEFAULT_DATABASE_URL = 'postgresql:///travelos'


class PostgresTripStore:
    def __init__(self, database_url: str | None = None, *, user_id=None):
        self.user_id = user_id
        self.database_url = database_url or os.getenv('DATABASE_URL') or DEFAULT_DATABASE_URL
        with self._connect() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS trips (
                trip_id TEXT PRIMARY KEY, version INTEGER NOT NULL,
                state_json TEXT NOT NULL, plan_json TEXT NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL, user_id UUID
            )''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_trips_expires_at ON trips(expires_at)')
            conn.execute('''CREATE TABLE IF NOT EXISTS trip_history (
                trip_id TEXT NOT NULL REFERENCES trips(trip_id), version INTEGER NOT NULL,
                skill TEXT NOT NULL, city TEXT NOT NULL, title TEXT NOT NULL,
                origin_name TEXT NOT NULL, state_json TEXT NOT NULL, plan_json TEXT NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL, PRIMARY KEY (trip_id, version)
            )''')
            conn.execute('CREATE INDEX IF NOT EXISTS idx_history_skill_time ON trip_history(skill, updated_at DESC)')

    @contextmanager
    def _connect(self) -> Iterator[psycopg.Connection]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as conn:
            yield conn

    @staticmethod
    def _record(row: dict) -> TripRecord:
        return TripRecord(
            state=TripState.model_validate_json(row['state_json']),
            latest_plan=TypeAdapter(PlanData).validate_json(row['plan_json']),
        )

    def get(self, trip_id: str, *, allow_expired: bool = False) -> TripRecord:
        with self._connect() as conn:
            row = conn.execute('SELECT * FROM trips WHERE trip_id = %s AND user_id IS NOT DISTINCT FROM %s::uuid', (trip_id, self.user_id)).fetchone()
        if row is None:
            raise TripNotFoundError('行程不存在')
        record = self._record(row)
        if not allow_expired and record.state.expires_at <= datetime.now(timezone.utc):
            raise TripExpiredError('行程已过期，请新建行程')
        return record

    def get_version(self, trip_id: str, version: int) -> TripRecord:
        with self._connect() as conn:
            row = conn.execute('SELECT h.* FROM trip_history h JOIN trips t ON h.trip_id=t.trip_id WHERE h.trip_id = %s AND h.version = %s AND t.user_id IS NOT DISTINCT FROM %s::uuid',
                               (trip_id, version, self.user_id)).fetchone()
        if row is None:
            raise TripNotFoundError('这个规划版本不存在')
        return self._record(row)

    def walk_place_preferences(self, trip_id: str, through_version: int,
                               poi_ids: set[str]) -> dict[str, list[str]]:
        if not poi_ids:
            return {}
        self.get(trip_id, allow_expired=True)
        found = {}
        with self._connect() as conn:
            rows = conn.execute(
                'SELECT h.plan_json FROM trip_history h JOIN trips t ON t.trip_id=h.trip_id WHERE h.trip_id = %s AND h.skill = %s AND h.version <= %s '
                'AND t.user_id IS NOT DISTINCT FROM %s::uuid ORDER BY h.version DESC', (trip_id, 'CITYWALK', through_version, self.user_id))
            for row in rows:
                plan = TypeAdapter(PlanData).validate_json(row['plan_json'])
                for place in plan.places:
                    if place.poi.id in poi_ids and place.poi.id not in found:
                        preferences = place.search_preferences()
                        if preferences:
                            found[place.poi.id] = preferences
                if len(found) == len(poi_ids):
                    break
        return found

    @staticmethod
    def _append_history(conn: psycopg.Connection, record: TripRecord) -> None:
        state, plan = record.state, record.latest_plan
        conn.execute('''INSERT INTO trip_history
            (trip_id, version, skill, city, title, origin_name, state_json, plan_json, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)''',
            (state.trip_id, state.state_version, state.current_skill, state.city,
             plan.title, state.origin_name, state.model_dump_json(), plan.model_dump_json(), state.updated_at))

    def history(self, *, skill: str | None = None, query: str = '', page: int = 1,
                page_size: int = 5, trip_id: str | None = None) -> TripHistoryPage:
        filters, values = ["t.user_id IS NOT DISTINCT FROM %s::uuid"], [self.user_id]
        if trip_id:
            self.get(trip_id, allow_expired=True)
            filters.append('h.trip_id = %s')
            values.append(trip_id)
        else:
            filters.append('NOT EXISTS (SELECT 1 FROM trip_history newer WHERE '
                           'newer.trip_id = h.trip_id AND newer.skill = h.skill AND newer.version > h.version)')
        if skill:
            filters.append('h.skill = %s')
            values.append(skill)
        if query.strip():
            filters.append("strpos(lower(h.city || ' ' || h.title || ' ' || h.origin_name), lower(%s)) > 0")
            values.append(query.strip())
        source = 'FROM trip_history h JOIN trips t ON t.trip_id = h.trip_id WHERE ' + ' AND '.join(filters)
        with self._connect() as conn:
            total = conn.execute('SELECT COUNT(*) AS total ' + source, values).fetchone()['total']
            rows = conn.execute(
                'SELECT h.*, t.version AS latest_version, t.expires_at AS latest_expires_at '
                + source + ' ORDER BY h.updated_at DESC, h.trip_id DESC, h.version DESC LIMIT %s OFFSET %s',
                [*values, page_size, (page - 1) * page_size]).fetchall()
        now = datetime.now(timezone.utc)
        items = []
        for row in rows:
            record = self._record(row)
            state, plan = record.state, record.latest_plan
            items.append(TripHistoryItem(
                trip_id=state.trip_id, state_version=state.state_version,
                latest_version=row['latest_version'], skill=state.current_skill, city=state.city,
                title=plan.title, summary=plan.summary, origin_name=state.origin_name,
                place_count=len(plan.places), created_at=state.created_at, updated_at=state.updated_at,
                expired=row['latest_expires_at'] <= now,
            ))
        return TripHistoryPage(items=items, total=total, page=page, page_size=page_size)

    def create(self, record: TripRecord) -> None:
        state = record.state
        with self._connect() as conn:
            conn.execute('''INSERT INTO trips
                (trip_id, version, state_json, plan_json, expires_at, created_at, updated_at, user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                (state.trip_id, state.state_version, state.model_dump_json(),
                 record.latest_plan.model_dump_json(), state.expires_at, state.created_at, state.updated_at, self.user_id))
            self._append_history(conn, record)

    def replace(self, record: TripRecord, expected_version: int, *, allow_expired: bool = False) -> None:
        state = record.state
        if state.state_version != expected_version + 1:
            raise ValueError('新状态版本号无效')
        with self._connect() as conn:
            row = conn.execute('SELECT version, expires_at FROM trips WHERE trip_id = %s AND user_id IS NOT DISTINCT FROM %s::uuid FOR UPDATE',
                               (state.trip_id, self.user_id)).fetchone()
            if row is None:
                raise TripNotFoundError('行程不存在')
            if not allow_expired and row['expires_at'] <= datetime.now(timezone.utc):
                raise TripExpiredError('行程已过期，请新建行程')
            if row['version'] != expected_version:
                raise TripVersionConflictError('行程已被其他请求更新，请重新读取后再提交')
            conn.execute('''UPDATE trips SET version = %s, state_json = %s, plan_json = %s,
                expires_at = %s, updated_at = %s WHERE trip_id = %s AND user_id IS NOT DISTINCT FROM %s::uuid''',
                (state.state_version, state.model_dump_json(), record.latest_plan.model_dump_json(),
                 state.expires_at, state.updated_at, state.trip_id, self.user_id))
            self._append_history(conn, record)

    def delete(self, trip_id: str) -> None:
        """删除自己的整份规划及历史；与保存使用同一事务锁。"""
        with self._connect() as conn:
            row = conn.execute("SELECT trip_id FROM trips WHERE trip_id = %s AND user_id IS NOT DISTINCT FROM %s::uuid FOR UPDATE", (trip_id, self.user_id)).fetchone()
            if row is None:
                raise TripNotFoundError("行程不存在")
            conn.execute("DELETE FROM trip_history WHERE trip_id = %s", (trip_id,))
            conn.execute("DELETE FROM trips WHERE trip_id = %s", (trip_id,))
