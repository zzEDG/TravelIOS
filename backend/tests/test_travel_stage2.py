"""统一旅行入口的路由、澄清与状态续接回归测试。"""

import sys
import tempfile
import unittest
from narrator_fixtures import OfflineNarrationMixin
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402
from app.models.travel import (  # noqa: E402
    CityWalkPlanData, GeoLocation, PlaceCandidate, PlannedPlace,
    RouteSegment,
)
from app.services.preferences import (  # noqa: E402
    PreferenceExtractionError, PreferenceExtractor, PreferencePatch,
)
from app.agents.main_agent import MainAgent
from app.controllers.dependencies import get_main_agent
from app.services.trip_service import TripService
from app.controllers.dependencies import get_trip_service
from app.services.intent_router import RouteDecision, TravelRouter  # noqa: E402
from app.models.trip_store import TripStore  # noqa: E402


class FakeCityWalkAgent:
    def __init__(self):
        self.states = []

    async def execute(self, state):
        self.states.append(state)
        poi = PlaceCandidate(
            id="p1", name="测试建筑", category="历史建筑", address="测试路",
            location=GeoLocation(latitude=31.23, longitude=121.47),
        )
        return CityWalkPlanData(
            trip_id=state.trip_id, state_version=state.state_version,
            title="漫步", summary="两站漫步", origin=state.current_location,
            places=[
                PlannedPlace(step_order=1, poi=poi, why_selected="建筑", stay_minutes=30),
                PlannedPlace(step_order=2, poi=poi.model_copy(update={"id": "p2"}),
                             why_selected="散步", stay_minutes=30),
            ],
            routes=[RouteSegment(to_poi_id="p1", distance_meters=200, duration_minutes=3)],
            estimated_minutes=63, total_walking_meters=200,
        )


class FakeAmap:
    async def convert_gps_to_amap(self, longitude, latitude):
        return longitude + 0.01, latitude + 0.01


class FakeExtractor:
    def __init__(self, patches):
        self.patches = list(patches)
        self.priors = []

    async def extract(self, message, previous):
        self.priors.append(previous)
        return self.patches.pop(0)


class FakeRouter:
    def __init__(self, choices):
        self.choices = list(choices)
        self.inputs = []

    async def route(self, english_request, previous):
        self.inputs.append((english_request, previous))
        return RouteDecision(self.choices.pop(0), 0.82)


class Stage2Tests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    def make_main_agent(self, path, patches, choices):
        child = FakeCityWalkAgent()
        agent = MainAgent(trips=TripService(store=TripStore(path), amap=FakeAmap()), agents={'CITYWALK': child})
        extractor = FakeExtractor(patches)
        router = FakeRouter(choices)
        return MainAgent(agent.trips, extractor, router, agents=agent.agents), child, extractor, router

    async def test_http_new_trip_resume_and_stale_version(self):
        with tempfile.TemporaryDirectory() as temp:
            main_agent, child, extractor, router = self.make_main_agent(
                Path(temp) / "trips.sqlite3",
                [
                    PreferencePatch(
                        theme="建筑与咖啡", available_hours=4,
                        interests=["老建筑"], routing_english="Walk around old buildings and cafes",
                    ),
                    PreferencePatch(
                        available_hours=2, interests=[], dislikes=["商业街"],
                        additional_requirements="避开嘈杂街区",
                        routing_english="Make this walk shorter and quieter",
                    ),
                ],
                ["CITYWALK", "CITYWALK"],
            )
            app.dependency_overrides[get_main_agent] = lambda: main_agent
            app.dependency_overrides[get_trip_service] = lambda: main_agent.trips
            try:
                with patch("app.main.validate_config"), \
                        patch("app.main.print_config"), \
                        patch("builtins.print"), TestClient(app) as client:
                    created = client.post("/api/v1/travel", json={
                        "message": "在上海逛逛老建筑和咖啡馆，大约四小时",
                        "city": "上海",
                        "location": {"latitude": 31.22, "longitude": 121.46},
                    })
                    self.assertEqual(created.status_code, 200, created.text)
                    data = created.json()["data"]
                    self.assertEqual(data["routed_skill"], "CITYWALK")
                    self.assertEqual(data["state_version"], 1)
                    self.assertEqual(created.json()["meta"]["route_confidence"], 0.82)
                    trip_id = data["trip_id"]
                    self.assertEqual(child.states[0].interests, ["老建筑"])

                    resumed = client.post("/api/v1/travel", json={
                        "message": "换成两小时，避开商业街，安静一点",
                        "trip_id": trip_id, "state_version": 1,
                    })
                    self.assertEqual(resumed.status_code, 200, resumed.text)
                    self.assertEqual(resumed.json()["data"]["state_version"], 2)
                    self.assertEqual(child.states[1].available_hours, 2)
                    self.assertEqual(child.states[1].interests, ["老建筑"])
                    self.assertEqual(child.states[1].dislikes, ["商业街"])
                    self.assertEqual(child.states[1].additional_requirements, "避开嘈杂街区")
                    self.assertEqual(extractor.priors[1].state_version, 1)
                    self.assertEqual(router.inputs[1][1].trip_id, trip_id)

                    restored = client.get(f"/api/v1/trips/{trip_id}")
                    self.assertEqual(restored.json()["data"]["state"]["state_version"], 2)
                    self.assertEqual(restored.json()["data"]["state"]["dislikes"], ["商业街"])

                    stale = client.post("/api/v1/travel", json={
                        "message": "再短一点", "trip_id": trip_id, "state_version": 1,
                    })
                    self.assertEqual(stale.status_code, 409)
                    self.assertEqual(stale.json()["code"], 40005)
                    self.assertEqual(len(extractor.priors), 2)
            finally:
                app.dependency_overrides.clear()

    async def test_clarification_and_missing_location_do_not_save(self):
        with tempfile.TemporaryDirectory() as temp:
            main_agent, child, _, _ = self.make_main_agent(
                Path(temp) / "trips.sqlite3",
                [
                    PreferencePatch(routing_english="Please plan something"),
                    PreferencePatch(routing_english="I want to walk in Shanghai"),
                    PreferencePatch(routing_english="Find dinner"),
                ],
                ["CLARIFY", "CITYWALK", "FOOD"],
            )
            app.dependency_overrides[get_main_agent] = lambda: main_agent
            app.dependency_overrides[get_trip_service] = lambda: main_agent.trips
            try:
                with patch("app.main.validate_config"), \
                        patch("app.main.print_config"), \
                        patch("builtins.print"), TestClient(app) as client:
                    unclear = client.post("/api/v1/travel", json={"message": "帮我安排一下"})
                    self.assertEqual(unclear.status_code, 200)
                    self.assertEqual(unclear.json()["data"]["routed_skill"], "CLARIFY")
                    self.assertIsNone(unclear.json()["data"]["trip_id"])

                    missing = client.post("/api/v1/travel", json={
                        "message": "想在上海走走", "city": "上海",
                    })
                    self.assertEqual(missing.status_code, 200)
                    self.assertIn("location", missing.json()["data"]["question"])

                    food = client.post("/api/v1/travel", json={"message": "找家餐厅"})
                    self.assertEqual(food.status_code, 200)
                    self.assertEqual(food.json()["data"]["routed_skill"], "CLARIFY")
                    self.assertEqual(len(child.states), 0)
            finally:
                app.dependency_overrides.clear()

    async def test_extractor_and_router_validate_model_output(self):
        class FakeLLM:
            def with_structured_output(self, schema):
                return RunnableLambda(lambda messages: schema.model_validate({
                    "theme": "建筑与咖啡",
                    "routing_english": "Continue an architecture and coffee walk",
                }))
        patch_value = await PreferenceExtractor(FakeLLM()).extract("去看老建筑", None)
        self.assertEqual(patch_value.theme, "建筑与咖啡")
        self.assertIsNone(patch_value.available_hours)

        class BadLLM:
            def with_structured_output(self, schema):
                return RunnableLambda(lambda messages: schema.model_validate({"routing_english": "去看看"}))
        with self.assertRaises(PreferenceExtractionError):
            await PreferenceExtractor(BadLLM()).extract("去看看", None)

        class FakeJev:
            async def decide(self, state, questions):
                self.assertion = (state, questions)
                return {"travel_skill": {
                    "type": "choice", "choice": "CITYWALK", "confidence": 0.82,
                }}
        jev = FakeJev()
        decision = await TravelRouter(jev).route("Walk around old streets", None)
        self.assertEqual(decision.choice, "CITYWALK")
        self.assertIn("CLARIFY", jev.assertion[1]["travel_skill"]["criteria"])


if __name__ == "__main__":
    unittest.main()
