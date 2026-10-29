"""固定城市漫步地点的离线端到端验证。"""
import tempfile
import unittest
from pathlib import Path

from app.agents.main_agent import MainAgent
from app.models.travel import CityWalkPlanRequest, GeoLocation, TravelRequest
from app.models.trip_store import TripStore
from app.services.preferences import PreferenceExtractor, PreferencePatch
from app.services.trip_service import TripService
from narrator_fixtures import OfflineNarrationMixin
import test_citywalk
from test_travel_stage2 import FakeAmap, FakeExtractor, FakeRouter


class LockedCityWalkTests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.trips = TripService(TripStore(Path(self.temp.name) / "trips.db"), FakeAmap())
        self.walk, self.client = test_citywalk.CityWalkTests().make_agent()
        self.extractor = FakeExtractor([])
        self.router = FakeRouter([])
        self.main = MainAgent(self.trips, self.extractor, self.router, agents={"CITYWALK": self.walk})

    async def first(self):
        return await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            city="上海", start_location=GeoLocation(latitude=31.23, longitude=121.47),
            target_stops=3, interests=["历史建筑", "咖啡馆", "博物馆"],
        ))

    async def test_natural_language_lock_inherit_and_unlock(self):
        first = await self.first()
        trip_id = first.data.trip_id
        self.extractor.patches.extend([
            PreferencePatch(lock_place_names=["转角咖啡"], interests=["历史建筑", "博物馆"],
                            interests_mode="REPLACE", routing_english="Keep the cafe and change other places"),
            PreferencePatch(unlock_place_names=["转角咖啡"], routing_english="Unlock the cafe"),
        ])
        self.router.choices.extend(["CITYWALK", "CITYWALK"])
        result = await self.main.plan(TravelRequest(
            trip_id=trip_id, state_version=1, message="保留转角咖啡，其他换成建筑和博物馆",
        ))
        self.assertIn("p2", [p.poi.id for p in result.data.places])
        self.assertEqual(self.trips.get_trip(trip_id).state.locked_place_ids, ["p2"])
        self.assertEqual(result.data.state_version, 2)
        inherited = await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=trip_id, state_version=2,
        ))
        self.assertIn("p2", [p.poi.id for p in inherited.data.places])
        self.assertEqual(self.trips.get_trip(trip_id).state.locked_place_ids, ["p2"])
        await self.main.plan(TravelRequest(trip_id=trip_id, state_version=3, message="不必保留转角咖啡"))
        self.assertEqual(self.trips.get_trip(trip_id).state.locked_place_ids, [])

    async def test_invalid_id_and_too_many_locks_do_not_save(self):
        first = await self.first()
        trip_id = first.data.trip_id
        for ids, target in [(["unknown"], 3), (["p1", "p2", "p3"], 2)]:
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
                    trip_id=trip_id, state_version=1, target_stops=target, locked_place_ids=ids,
                ))
        self.assertEqual(self.trips.get_trip(trip_id).latest_version, 1)

    async def test_ambiguous_name_clarifies_without_saving(self):
        first = await self.first()
        self.extractor.patches.append(PreferencePatch(
            lock_place_names=["城市"], routing_english="Keep the city place",
        ))
        self.router.choices.append("CITYWALK")
        previous = self.trips.store.get(first.data.trip_id)
        previous.latest_plan.places[0].poi.name = "城市咖啡"
        previous.latest_plan.places[1].poi.name = "城市书店"
        with unittest.mock.patch.object(self.trips, "previous", return_value=previous):
            result = await self.main.plan(TravelRequest(
                trip_id=first.data.trip_id, state_version=1, message="保留城市",
            ))
        self.assertEqual(result.data.routed_skill, "CLARIFY")
        self.assertEqual(self.trips.get_trip(first.data.trip_id).latest_version, 1)

    async def test_all_locked_and_no_search(self):
        first = await self.first()
        self.walk.discover = unittest.mock.AsyncMock(side_effect=AssertionError("不应搜索"))
        result = await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=first.data.trip_id, state_version=1, target_stops=3,
            locked_place_ids=["p1", "p2", "p3"],
        ))
        self.assertEqual({p.poi.id for p in result.data.places}, {"p1", "p2", "p3"})
        self.walk.discover.assert_not_awaited()

    async def test_extractor_receives_previous_plan_and_lock_status(self):
        first = await self.first()
        previous = self.trips.store.get(first.data.trip_id)
        previous.state.locked_place_ids = ["p2"]
        previous.state._previous_places = previous.latest_plan.places
        extractor = PreferenceExtractor.__new__(PreferenceExtractor)
        extractor.chain = unittest.mock.AsyncMock()
        extractor.chain.ainvoke.return_value = PreferencePatch(routing_english="Keep the cafe")
        await extractor.extract("保留咖啡", previous.state)
        import json
        payload = json.loads(extractor.chain.ainvoke.await_args.args[0]["input"])
        place = next(p for p in payload["previous_places"] if p["id"] == "p2")
        self.assertEqual(place["name"], "转角咖啡")
        self.assertTrue(place["locked"])

    async def test_historical_version_and_explicit_clear(self):
        first = await self.first()
        trip_id = first.data.trip_id
        await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=trip_id, state_version=1, locked_place_ids=["p2"],
        ))
        result = await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=trip_id, state_version=2, base_version=1, locked_place_ids=["p1"],
        ))
        self.assertIn("p1", [p.poi.id for p in result.data.places])
        self.assertEqual(self.trips.get_trip(trip_id).state.locked_place_ids, ["p1"])
        await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=trip_id, state_version=3, locked_place_ids=[],
        ))
        self.assertEqual(self.trips.get_trip(trip_id).state.locked_place_ids, [])

    async def test_missing_lock_is_rejected_after_jev_selection(self):
        first = await self.first()
        original = self.walk.route
        async def route(start, end):
            if start.poi_id == "p1" and end.poi_id == "p2":
                return None
            return await original(start, end)
        self.walk.route = route
        with self.assertRaisesRegex(ValueError, "保留所有锁定地点"):
            await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
                trip_id=first.data.trip_id, state_version=1, target_stops=2, locked_place_ids=["p2"],
            ))
        self.assertEqual(self.trips.get_trip(first.data.trip_id).latest_version, 1)

    async def test_unreachable_lock_does_not_save(self):
        first = await self.first()
        original = self.walk.route
        async def route(start, end):
            if end.poi_id == "p2":
                return None
            return await original(start, end)
        self.walk.route = route
        with self.assertRaises(ValueError):
            await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
                trip_id=first.data.trip_id, state_version=1, locked_place_ids=["p2"],
            ))
        self.assertEqual(self.trips.get_trip(first.data.trip_id).latest_version, 1)


if __name__ == "__main__":
    unittest.main()
