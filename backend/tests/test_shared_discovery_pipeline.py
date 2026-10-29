"""三个 Agent 共用候选搜索及地点响应的离线回归。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from app.agents.attraction_agent import AttractionAgent
from app.agents.citywalk_agent import CityWalkAgent
from app.agents.food_agent import FoodAgent
from app.agents.main_agent import MainAgent
from app.models.travel import AttractionPlanRequest, CityWalkPlanRequest, FoodSearchRequest, GeoLocation, TripState
from app.models.trip_store import TripStore
from app.services.trip_service import TripService
from narrator_fixtures import OfflineNarrationMixin
from test_place_counts import NearbyPlaces, Translator, Choice
from walk_fixtures import fake_walk_preferences


class SharedDiscoveryPipelineTests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.amap = NearbyPlaces(12)
        self.trips = TripService(TripStore(Path(self.temp.name) / "trips.db"), self.amap)
        weather = Mock()
        weather.lookup = AsyncMock(return_value=(None, "离线测试"))
        walk = CityWalkAgent.__new__(CityWalkAgent)
        walk.amap, walk.jev, walk.translator = self.amap, Choice(), Translator()
        walk.parse_preferences = fake_walk_preferences
        walk.weather_service = weather
        walk.unsplash = Mock()
        walk.unsplash.cover = AsyncMock(return_value=None)
        self.agents = {
            "CITYWALK": walk,
            "ATTRACTION": AttractionAgent(self.amap, Choice(), Translator(), weather),
            "FOOD": FoodAgent(self.amap, Choice(), Translator()),
        }
        for agent in self.agents.values():
            agent.discover = AsyncMock(wraps=agent.discover)
        self.main = MainAgent(self.trips, agents=self.agents)

    async def test_shared_search_uses_state_count_and_skill_radius(self):
        loc = GeoLocation(latitude=31.23, longitude=121.47)
        requests = {
            "CITYWALK": CityWalkPlanRequest(city="上海", start_location=loc, interests=["咖啡馆"],
                                            target_stops=3, radius_meters=1200),
            "ATTRACTION": AttractionPlanRequest(city="上海", location=loc, target_stops=3,
                                                radius_meters=1400, consider_weather=False),
            "FOOD": FoodSearchRequest(city="上海", center_location=loc, limit=3,
                                      max_distance_meters=1600),
        }
        for skill, radius in (("CITYWALK", 1200), ("ATTRACTION", 1400), ("FOOD", 1600)):
            with self.subTest(skill=skill):
                response = await self.main.plan_for(skill, requests[skill])
                kwargs = self.agents[skill].discover.await_args.kwargs
                self.assertEqual(kwargs["radius"], radius)
                self.assertEqual(kwargs["search_limit"], 6)
                self.assertEqual(len(response.data.places), 3)
                self.assertTrue(all(p.poi.source == "AMAP" and p.poi.source_url
                                    for p in response.data.places))
                if skill == "CITYWALK":
                    self.assertEqual(len(response.data.routes), 3)
                    self.assertEqual(self.trips.get_trip(response.data.trip_id).state.radius_meters, radius)

    async def test_request_count_and_search_limit_match_state_bounds(self):
        loc = GeoLocation(latitude=31.23, longitude=121.47)
        attraction = AttractionPlanRequest(city="上海", location=loc, target_stops=50)
        attraction_state = await self.trips.prepare(attraction, AttractionAgent, None)
        self.assertEqual(attraction_state.target_stops, 50)
        self.assertEqual(TripState.model_validate_json(attraction_state.model_dump_json()).target_stops, 50)
        food = FoodSearchRequest(city="上海", center_location=loc, limit=1000)
        food_state = await self.trips.prepare(food, FoodAgent, None)
        self.assertEqual(food_state.search_limit, 1000)
        self.assertEqual(TripState.model_validate_json(food_state.model_dump_json()).search_limit, 1000)


if __name__ == "__main__":
    unittest.main()
