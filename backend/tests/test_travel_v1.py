"""V2 CityWalk 的状态、坐标、HTTP 契约与旧接口共存测试。"""

import sys
import tempfile
import unittest
from narrator_fixtures import OfflineNarrationMixin
from unittest.mock import patch
from pathlib import Path

from fastapi.testclient import TestClient


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402
from app.models.travel import (  # noqa: E402
    CityWalkPlanData, GeoLocation, PlaceCandidate, PlannedPlace, RouteSegment,
)
from app.agents.main_agent import MainAgent
from app.controllers.dependencies import get_main_agent
from app.services.trip_service import TripService
from app.controllers.dependencies import get_trip_service
from app.integrations.amap_service import AmapService, Place  # noqa: E402
from app.models.trip_store import TripStore  # noqa: E402


class FakeAmap:
    def __init__(self):
        self.converted = []

    async def convert_gps_to_amap(self, longitude, latitude):
        self.converted.append((longitude, latitude))
        return longitude + 0.01, latitude + 0.01


class FakeCityWalkAgent:
    def __init__(self):
        self.states = []

    async def execute(self, state):
        self.states.append(state)
        place1 = PlaceCandidate(
            id="p1", name="老建筑", category="历史建筑", address="测试路1号",
            location=GeoLocation(latitude=31.231, longitude=121.471),
        )
        place2 = PlaceCandidate(
            id="p2", name="转角咖啡", category="咖啡馆", address="测试路2号",
            location=GeoLocation(latitude=31.232, longitude=121.472),
        )
        return CityWalkPlanData(
            trip_id=state.trip_id, state_version=state.state_version,
            title="上海漫游", summary="两站步行路线",
            origin=state.current_location,
            places=[
                PlannedPlace(step_order=1, poi=place1, why_selected="建筑", stay_minutes=30),
                PlannedPlace(step_order=2, poi=place2, why_selected="歇脚", stay_minutes=30),
            ],
            routes=[
                RouteSegment(from_poi_id=None, to_poi_id="p1",
                             distance_meters=400, duration_minutes=6, polyline="121.46,31.22;121.471,31.231"),
                RouteSegment(from_poi_id="p1", to_poi_id="p2",
                             distance_meters=200, duration_minutes=3, polyline="121.471,31.231;121.472,31.232"),
            ],
            estimated_minutes=69, total_walking_meters=600,
        )


class TravelV1Tests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    async def test_lifespan_runs_on_startup_and_shutdown(self):
        with patch("app.main.validate_config") as validate, \
                patch("app.main.print_config"), \
                patch("builtins.print") as output:
            with TestClient(app) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                validate.assert_called_once()
            self.assertTrue(any(
                "应用正在关闭" in str(call)
                for call in output.call_args_list
            ))

    async def test_create_restore_resume_and_conflict(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "trips.sqlite3"
            amap, child = FakeAmap(), FakeCityWalkAgent()
            agent = MainAgent(trips=TripService(store=TripStore(db_path), amap=amap), agents={'CITYWALK': child})
            app.dependency_overrides[get_main_agent] = lambda: agent
            app.dependency_overrides[get_trip_service] = lambda: agent.trips
            try:
                with patch("app.main.validate_config"), TestClient(app) as client:
                    created = client.post("/api/v1/citywalk/plan", json={
                        "city": "上海", "theme": "建筑与咖啡", "available_hours": 3,
                        "target_stops": 2,
                        "start_location": {
                            "latitude": 31.22, "longitude": 121.46,
                            "coordinate_system": "WGS84",
                        },
                    })
                    self.assertEqual(created.status_code, 200, created.text)
                    body = created.json()
                    self.assertEqual(body["code"], 0)
                    self.assertEqual(body["data"]["state_version"], 1)
                    self.assertEqual(body["data"]["routes"][0]["from_poi_id"], None)
                    self.assertEqual(body["data"]["origin"]["coordinate_system"], "GCJ-02")
                    self.assertEqual(body["data"]["origin"]["longitude"], 121.47)
                    self.assertEqual(amap.converted, [(121.46, 31.22)])
                    trip_id = body["data"]["trip_id"]

                    restored = client.get(f"/api/v1/trips/{trip_id}")
                    self.assertEqual(restored.status_code, 200)
                    self.assertEqual(restored.json()["data"]["state"]["state_version"], 1)
                    self.assertEqual(TripStore(db_path).get(trip_id).state.city, "上海")

                    updated = client.post("/api/v1/citywalk/plan", json={
                        "trip_id": trip_id, "state_version": 1,
                        "additional_requirements": "安静一些",
                    })
                    self.assertEqual(updated.status_code, 200, updated.text)
                    self.assertEqual(updated.json()["data"]["state_version"], 2)
                    self.assertEqual(child.states[-1].additional_requirements, "安静一些")
                    self.assertEqual(child.states[-1].current_location.longitude, 121.47)

                    stale = client.post("/api/v1/citywalk/plan", json={
                        "trip_id": trip_id, "state_version": 1,
                    })
                    self.assertEqual(stale.status_code, 409)
                    self.assertEqual(stale.json()["code"], 40005)
                    self.assertIsNone(stale.json()["data"])

                    invalid = client.post("/api/v1/citywalk/plan", json={
                        "city": "上海", "theme": "建筑与咖啡",
                        "start_location": {"latitude": 99, "longitude": 121},
                    })
                    self.assertEqual(invalid.status_code, 422)
                    self.assertEqual(invalid.json()["code"], 40001)
                    self.assertIn("start_location.latitude", str(invalid.json()["details"]))

                    missing = client.get("/api/v1/trips/trip_missing")
                    self.assertEqual(missing.status_code, 404)
                    self.assertEqual(missing.json()["code"], 40004)
            finally:
                app.dependency_overrides.clear()

    async def test_amap_coordinate_conversion_and_step_polyline(self):
        class Response:
            def __init__(self, body):
                self.body = body

            def raise_for_status(self):
                pass

            def json(self):
                return self.body

        class Client:
            async def get(self, url, params):
                if url.endswith("/assistant/coordinate/convert"):
                    self.last_conversion = params
                    return Response({"status": "1", "locations": "121.470000,31.230000"})
                if url.endswith("/direction/walking"):
                    return Response({"status": "1", "route": {"paths": [{
                        "distance": "420", "duration": "360",
                        "steps": [
                            {"polyline": "121.470,31.230;121.471,31.231"},
                            {"polyline": "121.471,31.231;121.472,31.232"},
                        ],
                    }]}})
                raise AssertionError(url)

        client = Client()
        service = AmapService("test", client=client)
        self.assertEqual(await service.convert_gps_to_amap(121.46, 31.22), (121.47, 31.23))
        self.assertEqual(client.last_conversion["coordsys"], "gps")
        first = Place("a", "起点", "", "", 121.47, 31.23, "", "")
        second = Place("b", "终点", "", "", 121.472, 31.232, "", "")
        route = await service.walking_route(first, second)
        self.assertEqual((route.distance_m, route.duration_min), (420, 6))
        self.assertEqual(route.polyline, "121.470,31.230;121.471,31.231;121.472,31.232")

    async def test_old_route_remains_in_openapi(self):
        paths = app.openapi()["paths"]
        self.assertIn("/api/walk/plan", paths)
        self.assertIn("/api/walk/map", paths)
        self.assertIn("/api/v1/citywalk/plan", paths)


if __name__ == "__main__":
    unittest.main()
