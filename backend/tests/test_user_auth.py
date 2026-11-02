"""真实 PostgreSQL 隔离 schema；不调用外部服务，不修改正式账号与行程。"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4
from datetime import datetime, timedelta, timezone

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from fastapi import Depends

from app.main import app
from app.controllers.auth_dependencies import get_auth_service, require_user
from app.controllers.dependencies import get_trip_service, get_main_agent
from app.models.user_store import UserStore
from app.models.postgres_trip_store import PostgresTripStore
from app.models.trip_store import TripStore, TripRecord, TripNotFoundError, TripVersionConflictError
from app.models.travel import TripState, GeoLocation, CityWalkPlanData
from app.services.auth_service import AuthService
from app.services.trip_service import TripService
from app.agents.main_agent import MainAgent
from scripts.migrate_user_auth import migrate


@unittest.skipUnless(os.getenv("TRAVELOS_TEST_DATABASE_URL"), "requires isolated PostgreSQL tests")
class UserAuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.base = os.environ["TRAVELOS_TEST_DATABASE_URL"]
        self.schema = "auth_test_" + uuid4().hex
        with psycopg.connect(self.base, autocommit=True) as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)
        self.url = make_conninfo(self.base, options=f"-c search_path={self.schema}")
        migrate(self.url)
        migrate(self.url)
        self.users = UserStore(self.url)
        env = patch.dict(os.environ, {"AUTH_CSRF_SECRET": "test-only-secret-" * 4, "AUTH_COOKIE_SECURE": "false"})
        env.start()
        self.addCleanup(env.stop)
        app.dependency_overrides[get_auth_service] = lambda: AuthService(self.users)
        def trips(user=Depends(require_user)):
            return TripService(PostgresTripStore(self.url, user_id=user["id"]), amap=object())
        app.dependency_overrides[get_trip_service] = trips
        # actual MainAgent, but foreign-owner failures must happen before executing any subagent.
        def main(user=Depends(require_user)):
            return MainAgent(trips=trips(user), agents={})
        app.dependency_overrides[get_main_agent] = main
        self.addCleanup(app.dependency_overrides.clear)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers={"Origin": "http://localhost:5173"})
        self.addAsyncCleanup(self.client.aclose)

    def drop_schema(self):
        assert self.schema.startswith("auth_test_")
        with psycopg.connect(self.base, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))

    async def login(self, username="alice"):
        body = {"username": username, "password": "unique-test-passphrase-2026"}
        response = await self.client.post("/api/v1/auth/register", json=body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn("password_hash", response.text)
        self.assertEqual((await self.client.get("/api/v1/auth/me")).status_code, 401)
        response = await self.client.post("/api/v1/auth/login", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("HttpOnly", response.headers["set-cookie"])
        self.assertIn("Path=/api/v1", response.headers["set-cookie"])
        self.client.headers["X-CSRF-Token"] = response.json()["data"]["csrf_token"]
        return response.json()["data"]["user"]

    def record(self, trip_id="trip_auth_test"):
        now = datetime.now(timezone.utc)
        state = TripState(trip_id=trip_id, state_version=1, city="上海", theme="公园", current_location=GeoLocation(latitude=31.23, longitude=121.47), available_hours=3, target_stops=2, created_at=now, updated_at=now, expires_at=now+timedelta(days=30))
        plan = CityWalkPlanData(trip_id=trip_id, state_version=1, title="测试", summary="测试", origin=state.current_location, places=[], routes=[], notes=[], estimated_minutes=0)
        return TripRecord(state, plan)

    async def test_delete_owner_history_and_csrf(self):
        path = "/api/v1/trips/trip_auth_test"
        self.assertEqual((await self.client.delete(path)).status_code, 401)
        alice = await self.login()
        store = PostgresTripStore(self.url, user_id=alice["id"])
        record = self.record()
        store.create(record)
        token = self.client.headers.pop("X-CSRF-Token")
        self.assertEqual((await self.client.delete(path)).status_code, 403)
        self.client.headers["X-CSRF-Token"] = token
        await self.client.post("/api/v1/auth/logout", json={})
        await self.login("bob")
        self.assertEqual((await self.client.delete(path)).status_code, 404)
        self.assertEqual(store.history().total, 1)
        await self.client.post("/api/v1/auth/logout", json={})
        response = await self.client.post("/api/v1/auth/login", json={"username":"alice", "password":"unique-test-passphrase-2026"})
        self.client.headers["X-CSRF-Token"] = response.json()["data"]["csrf_token"]
        self.assertEqual((await self.client.delete(path)).status_code, 200)
        self.assertEqual((await self.client.delete(path)).status_code, 404)
        self.assertEqual((await self.client.get(path)).status_code, 404)
        self.assertEqual(store.history().total, 0)
        with store._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) AS n FROM trip_history").fetchone()["n"], 0)
        with self.assertRaises(TripNotFoundError):
            store.get_version(record.state.trip_id, 1)
        with tempfile.TemporaryDirectory() as directory:
            local = TripStore(Path(directory)/"trips.db", user_id="alice")
            local.create(record)
            with self.assertRaises(TripNotFoundError):
                TripStore(local.path, user_id="bob").delete(record.state.trip_id)
            local.delete(record.state.trip_id)
            self.assertEqual(local.history().total, 0)

    async def test_session_lifecycle_csrf_and_validation(self):
        user = await self.login()
        self.assertEqual((await self.client.get("/api/v1/auth/me")).json()["data"]["id"], user["id"])
        token = self.client.headers.pop("X-CSRF-Token")
        self.assertEqual((await self.client.post("/api/v1/auth/logout", json={})).status_code, 403)
        self.assertEqual((await self.client.get("/api/v1/auth/csrf")).json()["data"]["csrf_token"], token)
        self.client.headers["X-CSRF-Token"] = token
        self.assertEqual((await self.client.post("/api/v1/auth/logout", json={})).status_code, 200)
        self.assertEqual((await self.client.get("/api/v1/auth/me")).status_code, 401)
        self.assertEqual((await self.client.post("/api/v1/auth/logout", json={})).status_code, 200)
        self.client.headers["Origin"] = "https://evil.example"
        self.assertEqual((await self.client.post("/api/v1/auth/login", json={"username":"alice","password":"unique-test-passphrase-2026"})).status_code, 400)

    async def test_two_users_cannot_read_resume_or_write_each_others_trips(self):
        alice = await self.login()
        store_a = PostgresTripStore(self.url, user_id=alice["id"])
        record = self.record()
        store_a.create(record)
        self.assertEqual((await self.client.get("/api/v1/trips")).json()["data"]["total"], 1)
        await self.client.post("/api/v1/auth/logout", json={})
        bob = await self.login("bob")
        store_b = PostgresTripStore(self.url, user_id=bob["id"])
        self.assertEqual((await self.client.get("/api/v1/trips")).json()["data"]["total"], 0)
        for path in ("/api/v1/trips/trip_auth_test", "/api/v1/trips/trip_auth_test?version=1", "/api/v1/trips/trip_auth_test/history"):
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 404, response.text)
        response = await self.client.post("/api/v1/citywalk/plan", json={"trip_id":"trip_auth_test", "state_version":1, "base_version":1})
        self.assertEqual(response.status_code, 404, response.text)
        updated = TripRecord(record.state.model_copy(update={"state_version":2}), record.latest_plan.model_copy(update={"state_version":2}))
        with self.assertRaises(TripNotFoundError):
            store_b.replace(updated, 1)
        with self.assertRaises(TripNotFoundError):
            store_b.walk_place_preferences(record.state.trip_id, 1, {"poi"})
        store_a.replace(updated, 1)
        with self.assertRaises(TripVersionConflictError):
            store_a.replace(updated, 1)
        self.assertEqual(store_a.history(trip_id=record.state.trip_id).total, 2)

    async def test_auth_throttling_and_generic_login_failure(self):
        await self.login()
        for _ in range(9):
            response = await self.client.post("/api/v1/auth/login", json={"username":"alice", "password":"wrong-password-2026"})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["code"], 40102)
        response = await self.client.post("/api/v1/auth/login", json={"username":"alice", "password":"wrong-password-2026"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)

    async def test_disabled_and_expired_sessions_and_ownerless_trips(self):
        alice = await self.login()
        PostgresTripStore(self.url).create(self.record("trip_unowned"))
        self.assertEqual((await self.client.get("/api/v1/trips/trip_unowned")).status_code, 404)
        with self.users._connect() as conn:
            conn.execute("UPDATE user_sessions SET expires_at=NOW()-INTERVAL '1 second'")
        self.assertEqual((await self.client.get("/api/v1/auth/me")).status_code, 401)
        with self.users._connect() as conn:
            conn.execute("UPDATE users SET is_active=false")
        response = await self.client.post("/api/v1/auth/login", json={"username":"alice", "password":"unique-test-passphrase-2026"})
        self.assertEqual(response.status_code, 401)

    async def test_public_errors_and_revoked_cookie_replay(self):
        for path in ("/api/v1/trips", "/api/v1/maps/static?points=121,31;121.1,31.1"):
            self.assertEqual((await self.client.get(path)).status_code, 401)
        for path in ("/api/v1/travel", "/api/v1/citywalk/plan", "/api/v1/attraction/plan", "/api/v1/food/search", "/api/v1/locations/search"):
            self.assertEqual((await self.client.post(path,json={})).status_code, 401)
        self.assertEqual((await self.client.post("/api/walk/plan",json={})).status_code,404)
        await self.login()
        cookie = self.client.cookies.get("travelos_session")
        response = await self.client.post("/api/v1/auth/register",json={"username":" ALICE ","password":"unique-test-passphrase-2026"})
        self.assertEqual(response.status_code,409)
        response = await self.client.post("/api/v1/auth/register",json={"username":"test", "password":"secret"})
        self.assertEqual(response.status_code,422)
        self.assertNotIn("secret",response.text)
        await self.client.post("/api/v1/auth/logout",json={})
        response = await self.client.get("/api/v1/auth/me",headers={"Cookie":"travelos_session="+cookie})
        self.assertEqual(response.status_code,401)

    async def test_history_failure_rolls_back_owner_update(self):
        user = await self.login()
        store = PostgresTripStore(self.url,user_id=user["id"])
        record = self.record()
        store.create(record)
        updated = TripRecord(record.state.model_copy(update={"state_version":2}),record.latest_plan.model_copy(update={"state_version":2}))
        with patch.object(store, "_append_history", side_effect=RuntimeError("test-only")):
            with self.assertRaises(RuntimeError):
                store.replace(updated,1)
        self.assertEqual(store.get(record.state.trip_id).state.state_version,1)
        self.assertEqual(store.history(trip_id=record.state.trip_id).total,1)

    async def test_sqlite_matches_scoped_postgres_behavior(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"test.db"
            a,b=TripStore(path,user_id="a"),TripStore(path,user_id="b")
            record=self.record()
            a.create(record)
            self.assertEqual(a.history().total,1)
            self.assertEqual(b.history().total,0)
            with self.assertRaises(TripNotFoundError):
                b.get_version(record.state.trip_id,1)
            with self.assertRaises(TripNotFoundError):
                b.replace(TripRecord(record.state.model_copy(update={"state_version":2}),record.latest_plan.model_copy(update={"state_version":2})),1)
