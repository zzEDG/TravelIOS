"""PostgreSQL integration tests; each test creates its own disposable schema."""
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi.testclient import TestClient

from app.main import app
from app.controllers.dependencies import get_trip_service
from app.models.postgres_trip_store import PostgresTripStore
from app.models.travel import GeoLocation, TripState
from app.models.trip_store import TripNotFoundError, TripRecord, TripStore, TripVersionConflictError
from app.services.trip_service import TripService
from scripts.migrate_sqlite_to_postgres import migrate
from test_travel_v1 import FakeAmap, FakeCityWalkAgent


@unittest.skipUnless(os.getenv('TRAVELOS_TEST_DATABASE_URL'), 'set TRAVELOS_TEST_DATABASE_URL to run PostgreSQL integration tests')
class PostgresStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.base_url = os.environ['TRAVELOS_TEST_DATABASE_URL']
        self.schema = 'travelos_test_' + uuid4().hex
        with psycopg.connect(self.base_url, autocommit=True) as conn:
            conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        self.url = make_conninfo(self.base_url, options=f'-c search_path={self.schema}')
        self.store = PostgresTripStore(self.url)

    def drop_schema(self):
        with psycopg.connect(self.base_url, autocommit=True) as conn:
            conn.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema)))

    async def record(self):
        now = datetime.now(timezone.utc)
        state = TripState(trip_id='trip_postgres_test', state_version=1, city='上海', theme='散步',
                          current_location=GeoLocation(latitude=31.23, longitude=121.47),
                          origin_name='人民广场', available_hours=3, target_stops=2,
                          created_at=now, updated_at=now, expires_at=now + timedelta(days=30))
        plan = await FakeCityWalkAgent().execute(state)
        return TripRecord(state, plan)

    async def test_create_history_update_and_http_read(self):
        record = await self.record()
        self.store.create(record)
        state = record.state.model_copy(update={'state_version': 2, 'additional_requirements': '喜欢安静'})
        plan = record.latest_plan.model_copy(update={'state_version': 2})
        self.store.replace(TripRecord(state, plan), expected_version=1)
        self.assertEqual(self.store.get(record.state.trip_id).state.additional_requirements, '喜欢安静')
        self.assertEqual(self.store.get_version(record.state.trip_id, 1).state.state_version, 1)
        self.assertEqual([x.state_version for x in self.store.history(trip_id=record.state.trip_id).items], [2, 1])
        self.assertEqual(self.store.history(skill='CITYWALK', query='人民广场').total, 1)
        app.dependency_overrides[get_trip_service] = lambda: TripService(store=self.store, amap=FakeAmap())
        self.addCleanup(app.dependency_overrides.clear)
        with TestClient(app) as client:
            result = client.get(f'/api/v1/trips/{record.state.trip_id}')
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.json()['data']['latest_version'], 2)
            self.assertEqual(client.get('/api/v1/trips?skill=CITYWALK').json()['data']['total'], 1)

    async def test_failed_history_write_rolls_back_current_version(self):
        record = await self.record()
        self.store.create(record)
        updated = TripRecord(record.state.model_copy(update={'state_version': 2}),
                             record.latest_plan.model_copy(update={'state_version': 2}))
        with patch.object(self.store, '_append_history', side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):
                self.store.replace(updated, expected_version=1)
        self.assertEqual(self.store.get(record.state.trip_id).state.state_version, 1)
        self.assertEqual(self.store.history(trip_id=record.state.trip_id).total, 1)

    async def test_concurrent_updates_do_not_overwrite_each_other(self):
        record = await self.record()
        self.store.create(record)
        updated = TripRecord(record.state.model_copy(update={'state_version': 2}),
                             record.latest_plan.model_copy(update={'state_version': 2}))
        def update():
            try:
                self.store.replace(updated, expected_version=1)
                return 'saved'
            except TripVersionConflictError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: update(), range(2)))
        self.assertCountEqual(results, ['saved', 'conflict'])
        self.assertEqual(self.store.history(trip_id=record.state.trip_id).total, 2)

    async def test_migration_is_repeatable_and_preserves_source(self):
        record = await self.record()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.sqlite3'
            TripStore(path).create(record)
            backup, trips, history = migrate(path, self.url)
            self.assertTrue(path.exists())
            self.assertTrue(backup.exists())
            self.assertEqual((trips, history), (1, 1))
            self.assertEqual(self.store.get(record.state.trip_id).state, record.state)
            migrate(path, self.url)
            self.assertEqual(self.store.history(trip_id=record.state.trip_id).total, 1)

    async def test_migration_conflict_rolls_back_all_imported_rows(self):
        record = await self.record()
        self.store.create(record)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'old.sqlite3'
            sqlite_store = TripStore(path)
            fresh = TripRecord(record.state.model_copy(update={'trip_id': 'trip_new'}),
                               record.latest_plan.model_copy(update={'trip_id': 'trip_new'}))
            conflict = TripRecord(record.state.model_copy(update={'city': '杭州'}), record.latest_plan)
            sqlite_store.create(fresh)
            sqlite_store.create(conflict)
            with self.assertRaisesRegex(ValueError, '已有不同'):
                migrate(path, self.url)
            with self.assertRaises(TripNotFoundError):
                self.store.get('trip_new')
            self.assertEqual(self.store.get(record.state.trip_id).state.city, '上海')
