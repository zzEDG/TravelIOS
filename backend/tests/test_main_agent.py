"""主 Agent 的委派隔离、三子 Agent 实际执行和共用状态生命周期。"""
from unittest.mock import AsyncMock
import tempfile
import unittest
from narrator_fixtures import OfflineNarrationMixin
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.agents.main_agent import AGENT_TYPES, MainAgent
from app.controllers.dependencies import get_main_agent
from app.agents.attraction_agent import AttractionAgent
from app.agents.food_agent import FoodAgent
from app.main import app
from app.models.travel import CityWalkPlanRequest, GeoLocation, TravelRequest
from app.models.trip_store import TripStore
from app.services.preferences import PreferencePatch
from app.services.trip_service import TripService
from app.controllers.dependencies import get_trip_service
import test_citywalk
from test_discovery import FakeAmap, FakeJev, FakeTranslator
from test_travel_stage2 import FakeExtractor, FakeRouter, FakeCityWalkAgent
from test_trip_history import FakeDiscoveryAgent


LOCATION = GeoLocation(latitude=31.23, longitude=121.47)


class MainAgentTests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.trips = TripService(TripStore(Path(self.temp.name)/"trips.sqlite3"), FakeAmap())

    async def test_router_initializes_and_executes_only_selected_child(self):
        for selected in AGENT_TYPES:
            with self.subTest(selected=selected), ExitStack() as stack:
                constructors, executions = {}, {}
                for name, cls in AGENT_TYPES.items():
                    fake = FakeCityWalkAgent() if name == "CITYWALK" else FakeDiscoveryAgent()
                    constructors[name] = stack.enter_context(patch.object(cls, "__init__", return_value=None))
                    executions[name] = stack.enter_context(patch.object(cls, "execute", side_effect=fake.execute))
                extractor = FakeExtractor([PreferencePatch(
                    theme="建筑与咖啡", routing_english="Plan the requested activity",
                )])
                router = FakeRouter([selected])
                main = MainAgent(self.trips, extractor, router)
                result = await main.plan(TravelRequest(message="帮我规划", city="上海", location=LOCATION))
                self.assertEqual(result.data.routed_skill, selected)
                self.assertEqual(result.meta.route_confidence, .82)
                for name in AGENT_TYPES:
                    self.assertEqual(constructors[name].call_count, int(name == selected))
                    self.assertEqual(executions[name].call_count, int(name == selected))
                # 表单续接使用同一个子 Agent，跳过需求抽取和意图路由。
                followup = AGENT_TYPES[selected].request_model(
                    trip_id=result.data.trip_id, state_version=1,
                )
                updated = await main.plan_for(selected, followup)
                self.assertEqual(updated.data.state_version, 2)
                self.assertEqual(constructors[selected].call_count, 1)
                self.assertEqual(executions[selected].call_count, 2)
                self.assertEqual(len(extractor.priors), 1)
                self.assertEqual(len(router.inputs), 1)
                self.assertEqual(self.trips.trip_history(result.data.trip_id).total, 2)

    async def test_clarification_never_initializes_children_or_saves_history(self):
        cases = [
            ("CLARIFY", {}, {}),
            ("FOOD", {}, {}),
            ("ATTRACTION", {"city": "上海"}, {}),
            ("CITYWALK", {"city": "上海", "location": LOCATION}, {}),
        ]
        with ExitStack() as stack:
            constructors = [
                stack.enter_context(patch.object(cls, "__init__", side_effect=AssertionError("不应初始化")))
                for cls in AGENT_TYPES.values()
            ]
            for destination, request_fields, preferences in cases:
                main = MainAgent(
                    self.trips,
                    FakeExtractor([PreferencePatch(routing_english="Plan something", **preferences)]),
                    FakeRouter([destination]),
                )
                result = await main.plan(TravelRequest(message="请安排一下", **request_fields))
                self.assertEqual(result.data.routed_skill, "CLARIFY")
            for constructor in constructors:
                constructor.assert_not_called()
        self.assertEqual(self.trips.list_trips().total, 0)

    async def test_all_three_real_children_run_through_main_agent(self):
        walk, _ = test_citywalk.CityWalkTests().make_agent()
        weather = Mock()
        weather.lookup = AsyncMock(return_value=(None, "离线测试"))
        main = MainAgent(
            self.trips,
            FakeExtractor([
                PreferencePatch(theme="建筑与咖啡", routing_english="A themed city walk"),
                PreferencePatch(interests=["博物馆"], routing_english="Visit museums"),
                PreferencePatch(cuisine_preference=["本帮菜"], limit=2, routing_english="Find dinner"),
            ]),
            FakeRouter(["CITYWALK", "ATTRACTION", "FOOD"]),
            agents={
                "CITYWALK": walk,
                "ATTRACTION": AttractionAgent(FakeAmap(), FakeJev(), FakeTranslator(), weather),
                "FOOD": FoodAgent(FakeAmap(), FakeJev(), FakeTranslator()),
            },
        )
        result = await main.plan(TravelRequest(message="主题漫步", city="上海", location=LOCATION))
        trip_id = result.data.trip_id
        self.assertEqual(len(result.data.places), 3)
        self.assertEqual(result.data.weather.day_temperature_c, 22)
        for version, name in ((1, "ATTRACTION"), (2, "FOOD")):
            result = await main.plan(TravelRequest(message="调整目标", trip_id=trip_id, state_version=version))
            self.assertEqual(result.data.routed_skill, name)
            self.assertEqual(result.data.trip_id, trip_id)
            self.assertEqual(result.data.state_version, version + 1)
            self.assertEqual(len(result.data.places), 2)
        self.assertEqual(result.data.route_semantics, "ALTERNATIVES")
        self.assertTrue(all(segment.from_poi_id is None for segment in result.data.routes))
        self.assertEqual(self.trips.trip_history(trip_id).total, 3)

    async def test_switch_defaults_explicit_clear_and_null_fields(self):
        child = FakeCityWalkAgent()
        main = MainAgent(
            self.trips,
            FakeExtractor([
                PreferencePatch(available_hours=.5, target_stops=1, routing_english="A short attraction visit"),
                PreferencePatch(theme="建筑与咖啡", routing_english="Switch to a city walk"),
                PreferencePatch(interests=[], routing_english="Make the walk quieter"),
                PreferencePatch(interests=[], clear_interests=True, routing_english="Clear my interests"),
            ]),
            FakeRouter(["ATTRACTION", "CITYWALK", "CITYWALK", "CITYWALK"]),
            agents={"CITYWALK": child, "ATTRACTION": FakeDiscoveryAgent()},
        )
        first = await main.plan_for("CITYWALK", CityWalkPlanRequest(
            city="上海", theme="建筑与咖啡", start_location=LOCATION,
            target_stops=5, interests=["建筑"], travel_date=date.today(), max_walking_meters=5000,
        ))
        trip_id = first.data.trip_id
        for version in range(1, 5):
            await main.plan(TravelRequest(message="修改计划", trip_id=trip_id, state_version=version))
        self.assertEqual((child.states[1].available_hours, child.states[1].target_stops), (3, 3))
        self.assertEqual(child.states[2].interests, ["建筑"])
        self.assertEqual(child.states[3].interests, [])
        self.assertEqual(child.states[3].max_walking_meters, 5000)
        await main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=trip_id, state_version=5, travel_date=None, max_walking_meters=None,
        ))
        self.assertIsNone(child.states[-1].travel_date)
        self.assertIsNone(child.states[-1].max_walking_meters)
        self.assertEqual(self.trips.get_trip(trip_id, version=1).state.interests, ["建筑"])

    async def test_invalid_child_result_does_not_create_history(self):
        main = MainAgent(self.trips, agents={"FOOD": FakeCityWalkAgent()})
        request = AGENT_TYPES["FOOD"].request_model(city="上海", center_location=LOCATION)
        with self.assertRaisesRegex(ValueError, "能力类型不一致"):
            await main.plan_for("FOOD", request)
        self.assertEqual(self.trips.list_trips().total, 0)

    async def test_history_endpoints_do_not_depend_on_main_agent(self):
        main_factory = Mock(side_effect=AssertionError("历史查询不应加载 Agent"))
        app.dependency_overrides[get_main_agent] = main_factory
        app.dependency_overrides[get_trip_service] = lambda: self.trips
        self.addCleanup(app.dependency_overrides.clear)
        with patch("builtins.print"), TestClient(app) as client:
            response = client.get("/api/v1/trips?skill=FOOD")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["total"], 0)
        main_factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
