"""历史迁移、子系统筛选、旧版本编辑与并发写入，不访问外部服务。"""
import sqlite3
import tempfile
import unittest
from narrator_fixtures import OfflineNarrationMixin
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from test_travel_v1 import FakeAmap, FakeCityWalkAgent
from app.main import app
from app.agents.main_agent import MainAgent
from app.controllers.dependencies import get_main_agent
from app.services.trip_service import TripService
from app.controllers.dependencies import get_trip_service
from app.services.preferences import PreferencePatch
from test_travel_stage2 import FakeExtractor, FakeRouter
from app.models.travel import (
    AttractionPlanData, FoodPlanData, CityWalkPlanRequest, FoodSearchRequest,
)
from app.models.trip_store import TripStore, TripRecord, TripExpiredError, TripVersionConflictError


class FakeDiscoveryAgent(FakeCityWalkAgent):
    async def execute(self, state):
        data = (await super().execute(state)).model_dump(exclude={"routed_skill"})
        if state.current_skill == "FOOD":
            return FoodPlanData(**data, meal_type=state.meal_type,
                                max_distance_meters=state.max_distance_meters)
        return AttractionPlanData(**data)


class TripHistoryTests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "trips.sqlite3"
        self.store = TripStore(self.path)
        self.skill = FakeCityWalkAgent()
        self.agent = MainAgent(trips=TripService(store=self.store, amap=FakeAmap()), agents={'CITYWALK': self.skill, 'FOOD': FakeDiscoveryAgent(), 'ATTRACTION': FakeDiscoveryAgent()})
        app.dependency_overrides[get_main_agent] = lambda: self.agent
        app.dependency_overrides[get_trip_service] = lambda: self.agent.trips
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()
        self.temp.cleanup()

    def create(self, skill="CITYWALK", city="上海"):
        paths = {"CITYWALK":"citywalk/plan", "ATTRACTION":"attraction/plan", "FOOD":"food/search"}
        location_fields = {"CITYWALK":"start_location", "ATTRACTION":"location", "FOOD":"center_location"}
        body = {"city": city, "origin_name": "人民广场", "additional_requirements":"最初的偏好",
                location_fields[skill]: {"latitude":31.23,"longitude":121.47}}
        if skill == "CITYWALK":
            body.update(theme="建筑与咖啡", target_stops=2)
        response = self.client.post("/api/v1/" + paths[skill], json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["data"]["trip_id"]

    async def test_subsystem_search_pagination_and_validation(self):
        for skill in ("CITYWALK", "ATTRACTION", "FOOD"):
            self.create(skill)
        self.create(city="杭州")
        result = self.client.get("/api/v1/trips", params={"skill":"CITYWALK", "page_size":1}).json()["data"]
        self.assertEqual(result["total"], 2)
        self.assertEqual(len(result["items"]), 1)
        page2 = self.client.get("/api/v1/trips?skill=CITYWALK&page_size=1&page=2").json()["data"]
        self.assertNotEqual(result["items"][0]["trip_id"], page2["items"][0]["trip_id"])
        for skill in ("ATTRACTION", "FOOD"):
            data = self.client.get("/api/v1/trips", params={"skill":skill, "q":"人民广场"}).json()["data"]
            self.assertEqual(data["total"], 1)
            self.assertEqual(data["items"][0]["skill"], skill)
        self.assertEqual(self.client.get("/api/v1/trips?q=杭州").json()["data"]["total"], 1)
        self.assertEqual(self.client.get("/api/v1/trips?q=不存在").json()["data"]["total"], 0)
        self.assertEqual(self.client.get("/api/v1/trips?skill=UNKNOWN").status_code, 422)
        self.assertEqual(self.client.get("/api/v1/trips?page=0").status_code, 422)
        self.assertEqual(self.client.get("/api/v1/trips?page_size=51").status_code, 422)

    async def test_edit_old_versions_for_all_subsystems_and_conflict(self):
        for skill, path in (("CITYWALK","citywalk/plan"), ("ATTRACTION","attraction/plan"), ("FOOD","food/search")):
            with self.subTest(skill=skill):
                trip_id = self.create(skill)
                url = "/api/v1/" + path
                updated = self.client.post(url, json={"trip_id":trip_id,"state_version":1,
                                                      "additional_requirements":"第二版偏好"})
                self.assertEqual(updated.status_code, 200, updated.text)
                historical = self.client.get(f"/api/v1/trips/{trip_id}?version=1").json()["data"]
                self.assertEqual(historical["latest_version"], 2)
                self.assertEqual(historical["state"]["additional_requirements"], "最初的偏好")
                edited = self.client.post(url, json={"trip_id":trip_id,"state_version":2,"base_version":1})
                self.assertEqual(edited.status_code, 200, edited.text)
                self.assertEqual(edited.json()["data"]["state_version"], 3)
                self.assertEqual(self.store.get(trip_id).state.additional_requirements, "最初的偏好")
                history = self.client.get(f"/api/v1/trips/{trip_id}/history").json()["data"]
                self.assertEqual([item["state_version"] for item in history["items"]], [3,2,1])
                conflict = self.client.post(url, json={"trip_id":trip_id,"state_version":2,"base_version":1})
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(self.store.history(trip_id=trip_id).total, 3)
                self.assertEqual(self.client.get(f"/api/v1/trips/{trip_id}?version=9").status_code, 404)
                self.assertEqual(self.client.post(url, json={"trip_id":trip_id,"state_version":3,"base_version":9}).status_code, 404)
        self.assertEqual(self.client.post("/api/v1/food/search", json={"base_version":1}).status_code, 422)
        self.assertEqual(self.client.get("/api/v1/trips/trip_missing/history").status_code, 404)

    async def test_skill_switch_retains_original_subsystem_and_can_edit_it(self):
        trip_id = self.create()
        await self.agent.plan_for("FOOD", FoodSearchRequest(trip_id=trip_id,state_version=1), allow_switch=True)
        walk = self.store.history(skill="CITYWALK").items[0]
        self.assertEqual((walk.state_version, walk.latest_version), (1,2))
        self.assertEqual(self.store.history(skill="FOOD").total, 1)
        edited = await self.agent.plan_for("CITYWALK", CityWalkPlanRequest(trip_id=trip_id,state_version=2,base_version=1))
        self.assertEqual(edited.data.state_version, 3)
        self.assertEqual(self.store.history(skill="CITYWALK").total, 1)
        self.assertEqual(self.store.history(skill="FOOD").items[0].state_version, 2)

    async def test_expired_history_read_and_explicit_edit_renewal(self):
        trip_id = self.create()
        record = self.store.get(trip_id)
        expired = record.state.model_copy(update={"trip_id":"trip_expired", "expires_at":datetime.now(timezone.utc)-timedelta(days=1)})
        plan = record.latest_plan.model_copy(update={"trip_id":expired.trip_id})
        self.store.create(TripRecord(expired, plan))
        with self.assertRaises(TripExpiredError):
            self.store.get(expired.trip_id)
        self.assertEqual(self.client.get(f"/api/v1/trips/{expired.trip_id}").status_code, 410)
        self.assertEqual(self.client.get(f"/api/v1/trips/{expired.trip_id}?version=1").status_code, 200)
        self.assertTrue(self.store.history(trip_id=expired.trip_id).items[0].expired)
        response = self.client.post("/api/v1/citywalk/plan", json={"trip_id":expired.trip_id,"state_version":1,"base_version":1})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertGreater(self.store.get(expired.trip_id).state.expires_at, datetime.now(timezone.utc))

    async def test_unified_chat_uses_selected_history_before_routing(self):
        for destination in ("CITYWALK", "ATTRACTION", "FOOD"):
            with self.subTest(destination=destination):
                trip_id = self.create()
                await self.agent.plan_for("CITYWALK", CityWalkPlanRequest(trip_id=trip_id,state_version=1,
                                                               additional_requirements="新的偏好"))
                extractor = FakeExtractor([PreferencePatch(routing_english="Adjust this saved plan")])
                main_agent = MainAgent(self.agent.trips, extractor, FakeRouter([destination]), agents=self.agent.agents)
                app.dependency_overrides[get_main_agent] = lambda: main_agent
                result = self.client.post("/api/v1/travel", json={"trip_id":trip_id,"state_version":2,
                                                                "base_version":1,"message":"继续调整这版"})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()["data"]["routed_skill"], destination)
                self.assertEqual(result.json()["data"]["state_version"], 3)
                self.assertEqual(extractor.priors[0].additional_requirements, "最初的偏好")
                self.assertEqual(self.store.history(trip_id=trip_id).total, 3)
                conflict = self.client.post("/api/v1/travel", json={"trip_id":trip_id,"state_version":2,
                                                                  "base_version":1,"message":"再次修改"})
                self.assertEqual(conflict.status_code, 409)
                self.assertEqual(len(extractor.priors), 1)

    async def test_migration_restart_failure_and_atomic_conflict(self):
        trip_id = self.create()
        await self.agent.plan_for("CITYWALK", CityWalkPlanRequest(trip_id=trip_id,state_version=1))
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                conn.execute("DROP TABLE trip_history")
        finally:
            conn.close()
        migrated = TripStore(self.path)
        self.assertEqual([item.state_version for item in migrated.history().items], [2])
        self.assertEqual(TripStore(self.path).history().total, 1)
        with patch.object(self.skill, "execute", side_effect=RuntimeError("规划失败")):
            with self.assertRaises(RuntimeError):
                await self.agent.plan_for("CITYWALK", CityWalkPlanRequest(trip_id=trip_id,state_version=2,base_version=2))
        self.assertEqual(self.store.get(trip_id).state.state_version, 2)
        with patch.object(self.store, "_append_history", side_effect=sqlite3.OperationalError("写入失败")):
            with self.assertRaises(sqlite3.OperationalError):
                await self.agent.plan_for("CITYWALK", CityWalkPlanRequest(trip_id=trip_id,state_version=2))
        self.assertEqual(self.store.get(trip_id).state.state_version, 2)
        self.assertEqual(self.store.history(trip_id=trip_id).total, 1)
        old = self.store.get(trip_id)
        await self.agent.plan_for("CITYWALK", CityWalkPlanRequest(trip_id=trip_id,state_version=2))
        stale_state = old.state.model_copy(update={"state_version":3})
        stale_plan = old.latest_plan.model_copy(update={"state_version":3})
        with self.assertRaises(TripVersionConflictError):
            self.store.replace(TripRecord(stale_state,stale_plan), expected_version=2)
        self.assertEqual(TripStore(self.path).history(trip_id=trip_id).total, 2)


if __name__ == "__main__":
    unittest.main()
