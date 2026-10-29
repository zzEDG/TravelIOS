"""三能力的硬约束、来源、HTTP 和 SQLite 兼容回归，不调用付费服务。"""
from unittest.mock import AsyncMock
import sys
import tempfile
import unittest
from narrator_fixtures import OfflineNarrationMixin
from unittest.mock import PropertyMock
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app
from app.models.travel import AttractionPlanRequest, FoodSearchRequest, GeoLocation, TripState
from app.integrations.amap_service import AmapService, Place, WalkingRoute
from app.services.preferences import PreferencePatch
from app.agents.main_agent import MainAgent
from app.controllers.dependencies import get_main_agent
from app.controllers.dependencies import get_amap
from app.services.trip_service import TripService
from app.controllers.dependencies import get_trip_service
from app.models.trip_store import TripStore
from app.agents.attraction_agent import AttractionAgent
from app.agents.food_agent import FoodAgent
from test_travel_stage2 import FakeCityWalkAgent, FakeExtractor, FakeRouter


class FakeAmap:
    def __init__(self, distances=None):
        self.distances = distances or {"p1": 200, "p2": 2200, "p3": 300}
        self.conversions = []
        self.calls = []

    async def search_around(self, city, longitude, latitude, keyword, radius_meters, types):
        self.calls.append(types)
        return [Place("p1", "测试一", "路1", "餐厅或博物馆", 121.461, 31.221, "310000", keyword),
                Place("p1", "重复项", "路1", "餐厅或博物馆", 121.461, 31.221, "310000", keyword),
                Place("p2", "测试二", "路2", "餐厅或博物馆", 121.462, 31.222, "310000", keyword),
                Place("p3", "测试三", "路3", "餐厅或博物馆", 121.463, 31.223, "310000", keyword),
                Place("far", "太远", "远路", "地点", 122.5, 32.5, "", keyword)]

    async def walking_route(self, origin, destination):
        d = self.distances.get(destination.poi_id)
        return WalkingRoute(d, 5 if d is not None else None, "121.46,31.22;121.461,31.221")

    async def convert_gps_to_amap(self, longitude, latitude):
        self.conversions.append((longitude, latitude))
        return longitude + .001, latitude + .001

    async def reverse_adcode(self, longitude, latitude):
        return "310101"

    async def weather(self, *args):
        return None


class FakeTranslator:
    async def request(self, text):
        return "Find appropriate verified places"
    async def places(self, places):
        return {p.poi_id: {"name": "Place", "category": "Category", "address": "Address"} for p in places}


class FakeJev:
    def __init__(self, choice=None):
        self.choice = choice
        self.inputs = []
    async def decide(self, state, questions):
        self.inputs.append((state, questions))
        if all(q["type"] == "score" for q in questions.values()):
            return {key: {"type": "score", "score": 2} for key in questions}
        choice = self.choice or next(iter(questions["place"]["criteria"]))
        return {"place": {"type": "choice", "choice": choice}}


def state(skill):
    now = datetime.now(timezone.utc)
    return TripState(
        trip_id="trip_test", state_version=1, city="上海", theme="测试主题",
        current_location=GeoLocation(latitude=31.22, longitude=121.46),
        current_skill=skill, available_hours=3, target_stops=2,
        created_at=now, updated_at=now, expires_at=now + timedelta(days=30),
    )


class DiscoveryTests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        weather = patch("app.dependencies.ServiceResources.weather", new_callable=PropertyMock)
        self.addCleanup(weather.stop)
        weather.start().return_value.lookup = AsyncMock(return_value=(None, "测试中不请求天气"))

    async def test_food_batches_all_candidates_and_reports_origin_distance(self):
        from test_place_counts import NearbyPlaces
        from app.integrations.amap_service import distance_m
        amap, jev = NearbyPlaces(12), FakeJev()
        amap.walking_route = AsyncMock(side_effect=AssertionError("No walking routes"))
        async def rank(context, questions):
            jev.inputs.append((context, questions))
            return {key: {"type": "score", "score": 2 if key == "p12" else 1}
                    for key in questions}
        jev.decide = rank
        agent = FoodAgent(amap, jev, FakeTranslator())
        trip = state("FOOD").model_copy(update={
            "current_location": GeoLocation(latitude=31.23, longitude=121.47),
        })
        plan = await agent.execute(trip)
        self.assertEqual(len(jev.inputs), 1)
        self.assertEqual(len(jev.inputs[0][0]["candidates"]), 12)
        self.assertEqual(len(jev.inputs[0][1]), 12)
        self.assertEqual([p.poi.id for p in plan.places], ["p12", "p1", "p2"])
        self.assertEqual(plan.routes, [])
        self.assertIsNone(plan.total_walking_meters)
        self.assertIsNone(plan.estimated_minutes)
        self.assertEqual(plan.route_semantics, "ALTERNATIVES")
        result = await agent.discover(city=trip.city, searches={"餐厅": {}},
                                      origin=(trip.current_location.longitude, trip.current_location.latitude),
                                      radius=trip.max_distance_meters, types="050000")
        candidates = result.places
        for place in plan.places:
            candidate = next(p for p in candidates if p.poi_id == place.poi.id)
            self.assertEqual(place.distance_from_origin_meters, round(distance_m(agent.origin(trip), candidate)))
            self.assertIsNone(place.stay_minutes)
            self.assertTrue(place.poi.source_url.startswith("https://uri.amap.com"))
        amap.walking_route.assert_not_called()

    async def test_food_filters_search_radius_without_walking_requests(self):
        amap, jev = FakeAmap(), FakeJev()
        amap.walking_route = AsyncMock(side_effect=AssertionError("No walking routes"))
        trip = state("FOOD").model_copy(update={"max_distance_meters": 250})
        plan = await FoodAgent(amap, jev, FakeTranslator()).execute(trip)
        self.assertEqual([p.poi.id for p in plan.places], ["p1"])
        self.assertEqual(set(jev.inputs[0][0]["candidates"]), {"p1"})
        amap.walking_route.assert_not_called()

    async def test_food_no_suitable_candidates(self):
        jev = FakeJev()
        jev.decide = AsyncMock(return_value={f"p{i}": {"type": "score", "score": 0} for i in range(1, 4)})
        with self.assertRaisesRegex(ValueError, "没有符合偏好"):
            await FoodAgent(FakeAmap(), jev, FakeTranslator()).execute(state("FOOD"))

    async def test_attraction_batches_all_candidates_without_walking_routes(self):
        from app.integrations.amap_service import distance_m
        from test_place_counts import NearbyPlaces
        amap, jev = NearbyPlaces(12), FakeJev()
        amap.walking_route = AsyncMock(side_effect=AssertionError("No walking routes"))
        async def rank(context, questions):
            jev.inputs.append((context, questions))
            return {key: {"type": "score", "score": 2 if key == "p12" else 1.5}
                    for key in questions}
        jev.decide = rank
        agent = AttractionAgent(amap, jev, FakeTranslator())
        s = state("ATTRACTION").model_copy(update={
            "available_hours": .5, "max_walking_meters": 100,
        })
        plan = await agent.execute(s)
        self.assertEqual(len(jev.inputs), 1)
        self.assertEqual(len(jev.inputs[0][0]["candidates"]), 12)
        self.assertEqual(len(jev.inputs[0][1]), 12)
        self.assertEqual(plan.places[0].poi.id, "p12")
        self.assertEqual(len(plan.places), 2)
        self.assertEqual(plan.routes, [])
        self.assertIsNone(plan.estimated_minutes)
        self.assertIsNone(plan.total_walking_meters)
        self.assertTrue(all(p.stay_minutes is None for p in plan.places))
        result = await agent.discover(city=s.city, searches={"景点": {}},
                                      origin=(s.current_location.longitude, s.current_location.latitude),
                                      radius=s.radius_meters, types="110000")
        candidates = result.places
        for place in plan.places:
            candidate = next(p for p in candidates if p.poi_id == place.poi.id)
            self.assertEqual(place.distance_from_origin_meters, round(distance_m(agent.origin(s), candidate)))
        amap.walking_route.assert_not_called()

    async def test_attraction_no_suitable_candidates(self):
        jev = FakeJev()
        jev.decide = AsyncMock(return_value={f"p{i}": {"type": "score", "score": 0} for i in range(1, 4)})
        with self.assertRaisesRegex(ValueError, "没有符合偏好"):
            await AttractionAgent(FakeAmap(), jev, FakeTranslator()).execute(state("ATTRACTION"))

    async def test_http_food_attraction_restore_resume_and_version(self):
        with tempfile.TemporaryDirectory() as temp:
            amap = FakeAmap()
            agent = MainAgent(trips=TripService(store=TripStore(Path(temp)/"trips.sqlite3"), amap=amap), agents={'CITYWALK': FakeCityWalkAgent(), 'FOOD': FoodAgent(amap, FakeJev(), FakeTranslator()), 'ATTRACTION': AttractionAgent(amap, FakeJev(), FakeTranslator())})
            app.dependency_overrides[get_main_agent] = lambda: agent
            app.dependency_overrides[get_trip_service] = lambda: agent.trips
            try:
                with patch("builtins.print"), TestClient(app) as client:
                    food = client.post("/api/v1/food/search", json={
                        "city": "上海", "center_location": {
                            "latitude": 31.22, "longitude": 121.46, "coordinate_system": "WGS84"},
                        "limit": 2, "cuisine_preference": ["本帮菜"],
                    })
                    self.assertEqual(food.status_code, 200, food.text)
                    data = food.json()["data"]
                    self.assertEqual(len(amap.conversions), 1)
                    restored = client.get("/api/v1/trips/"+data["trip_id"])
                    self.assertEqual(restored.json()["data"]["latest_plan"]["routed_skill"], "FOOD")
                    self.assertEqual(restored.json()["meta"]["degraded_services"], [])
                    resumed = client.post("/api/v1/food/search", json={
                        "trip_id": data["trip_id"], "state_version": 1,
                        "max_distance_meters": 250, "cuisine_preference": [],
                    })
                    self.assertEqual(resumed.status_code, 200, resumed.text)
                    self.assertEqual(len(resumed.json()["data"]["places"]), 2)
                    self.assertTrue(all(p["distance_from_origin_meters"] <= 250
                                        for p in resumed.json()["data"]["places"]))
                    self.assertEqual(resumed.json()["data"]["state_version"], 2)
                    stale = client.post("/api/v1/food/search", json={
                        "trip_id": data["trip_id"], "state_version": 1})
                    self.assertEqual(stale.status_code, 409)
                    mismatch = client.post("/api/v1/attraction/plan", json={
                        "trip_id": data["trip_id"], "state_version": 2})
                    self.assertEqual(mismatch.status_code, 422)
                    attraction = client.post("/api/v1/attraction/plan", json={
                        "city":"上海", "location":{"latitude":31.22,"longitude":121.46},
                        "available_hours":3.5, "target_stops":2, "consider_weather":False,
                    })
                    self.assertEqual(attraction.status_code, 200, attraction.text)
                    aid = attraction.json()["data"]["trip_id"]
                    self.assertEqual(client.get("/api/v1/trips/"+aid).json()["data"]["latest_plan"]["routed_skill"], "ATTRACTION")
                    invalid = client.post("/api/v1/food/search", json={
                        "city":"上海","center_location":{"latitude":31.22,"longitude":121.46},
                        "max_distance_meters":0})
                    self.assertEqual(invalid.status_code, 422)
                    self.assertEqual(invalid.json()["code"], 40001)
            finally:
                app.dependency_overrides.clear()

    async def test_unified_routes_food_then_attraction_and_keeps_same_trip(self):
        with tempfile.TemporaryDirectory() as temp:
            amap = FakeAmap()
            agent = MainAgent(trips=TripService(store=TripStore(Path(temp)/"trips.sqlite3"), amap=amap), agents={'CITYWALK': FakeCityWalkAgent(), 'FOOD': FoodAgent(amap, FakeJev(), FakeTranslator()), 'ATTRACTION': AttractionAgent(amap, FakeJev(), FakeTranslator())})
            orch = MainAgent(agent.trips, FakeExtractor([
                    PreferencePatch(cuisine_preference=["本帮菜"], routing_english="Find dinner"),
                    PreferencePatch(interests=["博物馆"], available_hours=3.5, routing_english="Visit museums"),
                ]), FakeRouter(["FOOD","ATTRACTION"]), agents=agent.agents)
            from app.models.travel import TravelRequest
            first = await orch.plan(TravelRequest(message="找饭店", city="上海",
                                           location=GeoLocation(latitude=31.22,longitude=121.46)))
            second = await orch.plan(TravelRequest(message="去景点", trip_id=first.data.trip_id,state_version=1))
            self.assertEqual(first.data.routed_skill, "FOOD")
            self.assertEqual(second.data.routed_skill, "ATTRACTION")
            self.assertEqual(first.data.trip_id, second.data.trip_id)
            self.assertEqual(second.data.state_version, 2)
            self.assertEqual(agent.trips.store.get(second.data.trip_id).state.cuisine_preference, ["本帮菜"])

    async def test_amap_around_contract_and_duration_rounds_up(self):
        captured = []
        def respond(request):
            captured.append(request)
            if request.url.path.endswith("/place/around"):
                return httpx.Response(200, json={"status":"1","pois":[
                    {"id":"p1","name":"地点","location":"121.46,31.22","type":"博物馆"},
                    {"id":"bad","name":"无坐标","location":"bad"}]})
            return httpx.Response(200, json={"status":"1","route":{"paths":[
                {"distance":"120","duration":"61","steps":[{"polyline":"121.46,31.22;121.461,31.221"}]}]}})
        amap=AmapService("test",httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        results=await amap.search_around("上海",121.46,31.22,"博物馆",1000,"140100")
        self.assertEqual(len(results),1)
        self.assertEqual(captured[0].url.params["radius"],"1000")
        self.assertEqual(captured[0].url.params["types"],"140100")
        self.assertEqual((await amap.walking_route(results[0],results[0])).duration_min,2)


    async def test_specific_keyword_does_not_mix_broad_categories(self):
        class MixedAmap(FakeAmap):
            async def search_around(self, city, longitude, latitude, keyword, radius_meters, types):
                self.calls.append(types)
                return [
                    Place("museum", "博物馆", "路", "科教文化服务;博物馆", 121.461, 31.221, "310000", keyword, "140100"),
                    Place("restaurant", "餐厅", "路", "餐饮服务", 121.462, 31.222, "310000", keyword, "050100"),
                ]
        amap = MixedAmap()
        skill = AttractionAgent(amap, FakeJev(), FakeTranslator())
        result = await skill.discover(city="上海", searches={"博物馆": {}},
                                      origin=(121.46, 31.22), radius=5000, types="110000|140100")
        candidates = result.places
        self.assertEqual([p.poi_id for p in candidates], ["museum"])
        self.assertEqual(amap.calls, [""])

    async def test_old_citywalk_json_still_restores_without_new_fields(self):
        import json
        from app.models.trip_store import TripRecord
        with tempfile.TemporaryDirectory() as temp:
            store = TripStore(Path(temp)/"trips.sqlite3")
            old = state("CITYWALK")
            store.create(TripRecord(old, await FakeCityWalkAgent().execute(old)))
            with store._connect() as conn:
                row = conn.execute("SELECT state_json, plan_json FROM trips").fetchone()
                value, plan = json.loads(row["state_json"]), json.loads(row["plan_json"])
                for name in ("radius_meters", "consider_weather", "meal_type", "cuisine_preference",
                             "food_avoid", "max_distance_meters", "food_limit"):
                    value.pop(name, None)
                for item in plan["places"]:
                    item["poi"].pop("source_url", None)
                    item["poi"].pop("retrieved_at", None)
                conn.execute("UPDATE trips SET state_json=?, plan_json=?", (json.dumps(value), json.dumps(plan)))
            restored = store.get(old.trip_id)
            self.assertEqual(restored.latest_plan.routed_skill, "CITYWALK")
            self.assertEqual(restored.state.food_limit, 3)


    async def test_map_accepts_origin_plus_five_places(self):
        with patch("app.dependencies.ServiceResources.amap", new_callable=PropertyMock) as service, patch("builtins.print"), TestClient(app) as client:
            service.return_value.static_map = AsyncMock(return_value=(b"mock-map", "image/png"))
            points = ";".join(f"121.{470+i},31.230" for i in range(6))
            response = client.get("/api/walk/map", params={"points": points})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(len(service.return_value.static_map.call_args.args[0]), 6)
            response = client.get("/api/walk/map", params={"points": points+";121.480,31.230"})
            self.assertEqual(response.status_code, 422)


    async def test_location_search_candidates_and_saved_origin_name(self):
        class LocationsAmap(FakeAmap):
            async def search(self, city, keyword):
                return [
                    Place("s1", "人民广场站一号口", "南京西路", "地铁站", 121.47, 31.23, "310000", keyword),
                    Place("s2", "人民广场站二号口", "西藏中路", "地铁站", 121.471, 31.231, "310000", keyword),
                ]
        with tempfile.TemporaryDirectory() as temp:
            amap = LocationsAmap()
            agent = MainAgent(trips=TripService(store=TripStore(Path(temp)/"trips.sqlite3"), amap=amap), agents={'CITYWALK': FakeCityWalkAgent(), 'FOOD': FoodAgent(amap, FakeJev(), FakeTranslator())})
            app.dependency_overrides[get_main_agent] = lambda: agent
            app.dependency_overrides[get_trip_service] = lambda: agent.trips
            app.dependency_overrides[get_amap] = lambda: amap
            try:
                with patch("builtins.print"), TestClient(app) as client:
                    response = client.post("/api/v1/locations/search", json={"city":"上海","query":"人民广场"})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(len(response.json()["data"]), 2)
                    chosen = response.json()["data"][1]
                    planned = client.post("/api/v1/food/search", json={
                        "city":"上海", "center_location":chosen["location"], "origin_name":chosen["name"],
                        "max_distance_meters":3000,
                    })
                    self.assertEqual(planned.status_code, 200, planned.text)
                    restored = client.get("/api/v1/trips/"+planned.json()["data"]["trip_id"])
                    self.assertEqual(restored.json()["data"]["state"]["origin_name"], chosen["name"])
                    self.assertEqual(restored.json()["data"]["state"]["current_location"],chosen["location"])
                    invalid=client.post("/api/v1/locations/search",json={"city":"上海","query":"   "})
                    self.assertEqual(invalid.status_code,422)
            finally:
                app.dependency_overrides.clear()

    async def test_location_search_address_fallback_and_no_match(self):
        class AddressAmap(FakeAmap):
            async def search(self, city, keyword):
                return []
            async def geocode_candidates(self, city, address):
                return [Place("geocode:0",address,address,"地址匹配",121.47,31.23,"310000",address)] if address=="南京西路100号" else []
        with tempfile.TemporaryDirectory() as temp:
            agent=MainAgent(trips=TripService(store=TripStore(Path(temp)/"trips.sqlite3"), amap=AddressAmap()), agents={'CITYWALK': FakeCityWalkAgent()})
            app.dependency_overrides[get_main_agent] = lambda: agent
            app.dependency_overrides[get_trip_service] = lambda: agent.trips
            app.dependency_overrides[get_amap] = lambda: agent.trips.amap
            try:
                with patch("builtins.print"), TestClient(app) as client:
                    response=client.post("/api/v1/locations/search",json={"city":"上海","query":"南京西路100号"})
                    self.assertEqual(response.status_code,200)
                    self.assertEqual(response.json()["data"][0]["category"],"地址匹配")
                    missing=client.post("/api/v1/locations/search",json={"city":"上海","query":"找不到"})
                    self.assertEqual(missing.status_code,422)
                    self.assertEqual(missing.json()["code"],40002)
            finally:
                app.dependency_overrides.clear()
