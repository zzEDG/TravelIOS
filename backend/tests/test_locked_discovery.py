"""景点和美食锁定地点的离线端到端测试。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

from app.agents.attraction_agent import AttractionAgent
from app.agents.food_agent import FoodAgent
from app.agents.main_agent import MainAgent
from app.models.travel import AttractionPlanRequest, FoodSearchRequest, GeoLocation, TravelRequest
from app.models.trip_store import TripStore
from app.services.preferences import PreferencePatch
from app.services.trip_service import TripService
from narrator_fixtures import OfflineNarrationMixin
from test_place_counts import NearbyPlaces, Translator, Choice
from test_travel_stage2 import FakeExtractor, FakeRouter


class LockedDiscoveryTests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.amap = NearbyPlaces(12)
        self.trips = TripService(TripStore(Path(self.temp.name) / "trips.db"), self.amap)
        self.jev = Choice()
        weather = Mock()
        weather.lookup = AsyncMock(return_value=(None, "离线测试"))
        self.agents = {
            "ATTRACTION": AttractionAgent(self.amap, self.jev, Translator(), weather),
            "FOOD": FoodAgent(self.amap, self.jev, Translator()),
        }
        self.extractor = FakeExtractor([])
        self.router = FakeRouter([])
        self.main = MainAgent(self.trips, self.extractor, self.router, agents=self.agents)

    async def first(self, skill, count=3):
        location = GeoLocation(latitude=31.23, longitude=121.47)
        request = (AttractionPlanRequest(city="上海", location=location, target_stops=count,
                                         consider_weather=False)
                   if skill == "ATTRACTION" else
                   FoodSearchRequest(city="上海", center_location=location, limit=count))
        return await self.main.plan_for(skill, request)

    def followup(self, skill, trip_id, version, **kwargs):
        cls = AttractionPlanRequest if skill == "ATTRACTION" else FoodSearchRequest
        return cls(trip_id=trip_id, state_version=version, **kwargs)

    async def test_natural_language_lock_inherit_unlock_for_both(self):
        for skill in ("ATTRACTION", "FOOD"):
            with self.subTest(skill=skill):
                first = await self.first(skill)
                trip_id = first.data.trip_id
                target = first.data.places[1]
                self.extractor.patches.extend([
                    PreferencePatch(lock_place_names=[target.poi.name], routing_english="Keep this place"),
                    PreferencePatch(unlock_place_names=[target.poi.name], routing_english="Unlock this place"),
                ])
                self.router.choices.extend([skill, skill])
                second = await self.main.plan(TravelRequest(
                    trip_id=trip_id, state_version=1, message="保留地点2",
                ))
                self.assertEqual(second.data.places[0].poi.id, target.poi.id)
                self.assertEqual(self.trips.get_trip(trip_id).state.locked_place_ids, [target.poi.id])
                self.assertEqual(len({p.poi.id for p in second.data.places}), len(second.data.places))
                self.assertNotIn(target.poi.id, self.jev.states[-1]["candidates"])
                third = await self.main.plan_for(skill, self.followup(skill, trip_id, 2))
                self.assertIn(target.poi.id, [p.poi.id for p in third.data.places])
                await self.main.plan(TravelRequest(trip_id=trip_id, state_version=3, message="解除地点2锁定"))
                self.assertEqual(self.trips.get_trip(trip_id).state.locked_place_ids, [])

    async def test_full_locked_list_skips_search_and_accepts_more_than_five(self):
        for skill in ("ATTRACTION", "FOOD"):
            with self.subTest(skill=skill):
                first = await self.first(skill, 6)
                ids = [p.poi.id for p in first.data.places]
                self.assertEqual(len(ids), 6)
                count = len(self.amap.searches)
                result = await self.main.plan_for(skill, self.followup(
                    skill, first.data.trip_id, 1, locked_place_ids=ids,
                ))
                self.assertEqual([p.poi.id for p in result.data.places], ids)
                self.assertEqual(len(self.amap.searches), count)
                self.assertTrue(all(p.distance_from_origin_meters is not None for p in result.data.places))

    async def test_invalid_count_id_and_range_do_not_save(self):
        for skill in ("ATTRACTION", "FOOD"):
            with self.subTest(skill=skill):
                first = await self.first(skill)
                trip_id = first.data.trip_id
                count_field = "target_stops" if skill == "ATTRACTION" else "limit"
                with self.assertRaisesRegex(ValueError, "不属于"):
                    await self.main.plan_for(skill, self.followup(skill, trip_id, 1, locked_place_ids=["missing"]))
                with self.assertRaisesRegex(ValueError, "超过目标数量"):
                    await self.main.plan_for(skill, self.followup(
                        skill, trip_id, 1, locked_place_ids=["p1", "p2"], **{count_field: 1},
                    ))
                location_field = "location" if skill == "ATTRACTION" else "center_location"
                with self.assertRaisesRegex(ValueError, "超出当前搜索距离"):
                    await self.main.plan_for(skill, self.followup(
                        skill, trip_id, 1, locked_place_ids=["p1"],
                        **{location_field: GeoLocation(latitude=32, longitude=122)},
                    ))
                self.assertEqual(self.trips.get_trip(trip_id).latest_version, 1)

    async def test_old_version_clear_and_ambiguous_name(self):
        for skill in ("ATTRACTION", "FOOD"):
            with self.subTest(skill=skill):
                first = await self.first(skill)
                trip_id = first.data.trip_id
                await self.main.plan_for(skill, self.followup(skill, trip_id, 1, locked_place_ids=["p2"]))
                edited = await self.main.plan_for(skill, self.followup(
                    skill, trip_id, 2, base_version=1, locked_place_ids=["p1"],
                ))
                self.assertEqual(edited.data.places[0].poi.id, "p1")
                await self.main.plan_for(skill, self.followup(skill, trip_id, 3, locked_place_ids=[]))
                self.assertEqual(self.trips.get_trip(trip_id).state.locked_place_ids, [])
                self.extractor.patches.append(PreferencePatch(
                    lock_place_names=["地点"], routing_english="Keep the place",
                ))
                self.router.choices.append(skill)
                result = await self.main.plan(TravelRequest(
                    trip_id=trip_id, state_version=4, message="保留地点",
                ))
                self.assertEqual(result.data.routed_skill, "CLARIFY")
                self.assertEqual(self.trips.get_trip(trip_id).latest_version, 4)

    async def test_skill_switch_clears_lock_and_history_restores_it(self):
        first = await self.first("FOOD")
        trip_id = first.data.trip_id
        await self.main.plan_for("FOOD", self.followup("FOOD", trip_id, 1, locked_place_ids=["p2"]))
        switched = await self.main.plan_for(
            "ATTRACTION", self.followup("ATTRACTION", trip_id, 2), allow_switch=True,
        )
        self.assertEqual(switched.data.routed_skill, "ATTRACTION")
        self.assertEqual(self.trips.get_trip(trip_id).state.locked_place_ids, [])
        restored = await self.main.plan_for("FOOD", self.followup(
            "FOOD", trip_id, 3, base_version=2,
        ))
        self.assertEqual(restored.data.places[0].poi.id, "p2")
        self.assertEqual(self.trips.get_trip(trip_id).state.locked_place_ids, ["p2"])

    async def test_lock_intent_cannot_move_place_across_skills(self):
        first = await self.first("FOOD")
        self.extractor.patches.append(PreferencePatch(
            lock_place_names=["地点1"], routing_english="Keep the restaurant and visit attractions",
        ))
        self.router.choices.append("ATTRACTION")
        result = await self.main.plan(TravelRequest(
            trip_id=first.data.trip_id, state_version=1, message="保留地点1并改为景点",
        ))
        self.assertEqual(result.data.routed_skill, "CLARIFY")
        self.assertEqual(self.trips.get_trip(first.data.trip_id).latest_version, 1)

    async def test_rejected_new_candidates_do_not_discard_locked_place(self):
        for skill in ("ATTRACTION", "FOOD"):
            with self.subTest(skill=skill):
                first = await self.first(skill)
                async def reject(context, questions):
                    return {name: {"type": "score", "score": 0} for name in questions}
                old_decide = self.jev.decide
                self.jev.decide = reject
                try:
                    result = await self.main.plan_for(skill, self.followup(
                        skill, first.data.trip_id, 1, locked_place_ids=["p2"],
                    ))
                finally:
                    self.jev.decide = old_decide
                self.assertEqual([p.poi.id for p in result.data.places], ["p2"])


if __name__ == "__main__":
    unittest.main()
