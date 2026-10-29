"""停留建议与高德限流的端到端离线验证。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from fastapi.testclient import TestClient

from app.agents.citywalk_agent import CityWalkAgent
from app.agents.main_agent import MainAgent
from app.controllers.dependencies import get_main_agent, get_trip_service
from app.integrations.amap_service import AmapRateLimitError, AmapService, WalkingRoute
from app.main import app
from app.models.trip_store import TripStore
from app.services.candidate_search import ReActCandidateSearch
from app.services.trip_service import TripService
from candidate_fixtures import OfflineCandidateSearch
from test_candidate_search import SearchMap, ToolModel, search
from test_place_counts import NearbyPlaces, Translator, Choice
from walk_fixtures import fake_walk_preferences


class SuggestionModel:
    def __init__(self):
        self.fail = False

    def with_structured_output(self, schema):
        async def invoke(messages):
            if self.fail:
                raise RuntimeError("offline")
            facts = json.loads(messages[-1].content)["current"]
            suggestions = [{"poi_id": place["poi"]["id"], "suggestion": "可根据兴趣停留约 10 分钟"}
                           for place in facts["places"]]
            suggestions += [{"poi_id": "not-in-plan", "suggestion": "无效地点"},
                            {"poi_id": suggestions[0]["poi_id"], "suggestion": "重复地点"}]
            return schema(evaluation="步行时间不含停留，请自行安排。",
                          stay_recommendations=suggestions)
        return SimpleNamespace(ainvoke=invoke)


class StayAndRateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.amap = NearbyPlaces(12)
        async def route(origin, destination):
            return WalkingRoute(100, 20, "121.47,31.23;121.471,31.231")
        self.amap.walking_route = route
        self.trips = TripService(TripStore(Path(self.temp.name) / "trips.db"), self.amap)
        self.model = SuggestionModel()
        self.walk = CityWalkAgent.__new__(CityWalkAgent)
        self.walk.amap, self.walk.jev, self.walk.translator = self.amap, Choice(), Translator()
        self.walk.parse_preferences = fake_walk_preferences
        self.walk.candidate_search = OfflineCandidateSearch()
        self.walk.weather_service = Mock(lookup=AsyncMock(return_value=(None, "离线天气")))
        self.walk.unsplash = Mock(cover=AsyncMock(return_value=None))
        self.main = MainAgent(self.trips, resources=SimpleNamespace(llm=self.model),
                              agents={"CITYWALK": self.walk})
        app.dependency_overrides.update({get_main_agent: lambda: self.main,
                                         get_trip_service: lambda: self.trips})
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def create(self):
        return self.client.post("/api/v1/citywalk/plan", json={
            "city": "上海", "start_location": {"latitude": 31.23, "longitude": 121.47},
            "target_stops": 5, "available_hours": 2, "interests": ["公园"],
        })

    async def test_five_places_use_walking_time_and_agent_advice_only(self):
        response = self.create()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(len(data["places"]), 5)
        self.assertEqual(data["estimated_minutes"], 100)
        self.assertEqual(data["duration_semantics"], "WALKING_ONLY")
        self.assertTrue(all(place["stay_minutes"] is None for place in data["places"]))
        advice = data["narration"]["stay_recommendations"]
        self.assertEqual([x["poi_id"] for x in advice], [p["poi"]["id"] for p in data["places"]])
        self.assertTrue(all("10 分钟" in x["suggestion"] for x in advice))
        self.assertEqual(data["narration"]["source"], "LLM")
        loaded = self.client.get("/api/v1/trips/" + data["trip_id"]).json()["data"]["latest_plan"]
        self.assertEqual(loaded["narration"]["stay_recommendations"], advice)

    async def test_history_comparison_identifies_changed_time_semantics(self):
        from app.services.plan_changes import describe_changes
        first = self.create().json()["data"]
        previous = self.trips.store.get(first["trip_id"])
        legacy_plan = previous.latest_plan.model_copy(update={
            "duration_semantics": "TOTAL_ESTIMATE", "estimated_minutes": 175,
        })
        from app.models.trip_store import TripRecord
        changes = describe_changes(TripRecord(previous.state, legacy_plan),
                                   previous.state, previous.latest_plan)
        self.assertTrue(any("时长口径已变化" in change for change in changes))
        self.assertFalse(any("估计总时长（分钟）" in change for change in changes))

    async def test_advice_agent_failure_does_not_invent_or_block_plan(self):
        self.model.fail = True
        response = self.create()
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()["data"]
        self.assertEqual(len(data["places"]), 5)
        self.assertEqual(data["narration"]["source"], "RULES")
        self.assertEqual(data["narration"]["stay_recommendations"], [])
        self.assertIn("不含停留", data["narration"]["evaluation"])
        self.assertIn("NARRATOR", response.json()["meta"]["degraded_services"])

    async def test_some_limited_routes_are_shown_in_result_and_history(self):
        async def route(origin, destination):
            if destination.poi_id == "p2":
                raise AmapRateLimitError()
            return WalkingRoute(100, 20, "121.47,31.23;121.471,31.231")
        self.amap.walking_route = route
        response = self.create()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(len(result["data"]["places"]), 5)
        self.assertIn("AMAP_RATE_LIMIT", result["meta"]["degraded_services"])
        self.assertTrue(any("限流" in note for note in result["data"]["notes"]))
        restored = self.client.get("/api/v1/trips/" + result["data"]["trip_id"]).json()
        self.assertIn("AMAP_RATE_LIMIT", restored["meta"]["degraded_services"])

    async def test_limited_locked_point_returns_rate_limit_and_keeps_old_plan(self):
        first = self.create().json()["data"]
        locked_id = first["places"][1]["poi"]["id"]
        async def route(origin, destination):
            if destination.poi_id == locked_id:
                raise AmapRateLimitError()
            return WalkingRoute(100, 20, "121.47,31.23;121.471,31.231")
        self.amap.walking_route = route
        response = self.client.post("/api/v1/citywalk/plan", json={
            "trip_id": first["trip_id"], "state_version": 1, "locked_place_ids": [locked_id],
        })
        self.assertEqual(response.status_code, 429, response.text)
        self.assertIn("限流", response.json()["message"])
        self.assertEqual(self.trips.get_trip(first["trip_id"]).latest_version, 1)

    async def test_all_limited_routes_return_clear_429_without_saving(self):
        async def route(origin, destination):
            raise AmapRateLimitError()
        self.amap.walking_route = route
        response = self.create()
        self.assertEqual(response.status_code, 429, response.text)
        self.assertEqual(response.json()["code"], 50011)
        self.assertIn("高德", response.json()["message"])
        self.assertIn("限流", response.json()["message"])
        self.assertEqual(self.trips.list_trips().total, 0)

    async def test_amap_and_candidate_search_preserve_rate_limit_reason(self):
        response = Mock(status_code=200)
        response.json.return_value = {"status": "0", "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT"}
        client = Mock(get=AsyncMock(return_value=response))
        amap = AmapService("unused", client)
        with self.assertRaises(AmapRateLimitError):
            await amap.search_around("上海", 121.47, 31.23, "公园", 1000, "")
        model = ToolModel(search())
        with self.assertRaises(AmapRateLimitError):
            await ReActCandidateSearch(model).search(
                SearchMap(AmapRateLimitError()), city="上海", keyword="公园",
                origin=(121.47, 31.23), radius=1000,
            )


if __name__ == "__main__":
    unittest.main()
