"""锁定地点按原检索类别进入候选，而不是只放进总地点池。"""
import json
import tempfile
import unittest
from pathlib import Path

from app.agents.main_agent import MainAgent
from app.integrations.amap_service import Place
from app.models.travel import CityWalkPlanRequest, TravelRequest
from app.models.trip_store import TripStore
from app.services.trip_service import TripService
from narrator_fixtures import OfflineNarrationMixin
from test_walk_preferences import Nearby, Choose, ORIGIN, walk
from test_travel_stage2 import FakeExtractor, FakeRouter
from app.services.preferences import PreferencePatch


class NearbyWithAlternative(Nearby):
    def __init__(self):
        super().__init__()
        self.only_alternative = False

    async def search_around(self, city, lon, lat, keyword, radius, types):
        if keyword == "咖啡馆" and self.only_alternative:
            self.searches.append(keyword)
            return [Place("other-cafe", "另一家咖啡店", "测试路", "咖啡馆",
                          121.4702, 31.23, "", keyword)]
        return await super().search_around(city, lon, lat, keyword, radius, types)


class LockedCategoryTests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.amap = NearbyWithAlternative()
        self.jev = Choose()
        self.agent = walk(amap=self.amap, jev=self.jev)
        self.store = TripStore(Path(self.temp.name) / "trips.db")
        self.trips = TripService(self.store, self.amap)
        self.main = MainAgent(self.trips, agents={"CITYWALK": self.agent})

    async def first(self):
        return await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            city="上海", start_location=ORIGIN, target_stops=3,
            interests=["公园", "咖啡馆", "书店"], visit_order=["公园", "咖啡馆", "书店"],
        ))

    async def test_locked_place_is_candidate_for_its_category_without_new_search_hit(self):
        first = await self.first()
        cafe = next(p for p in first.data.places if p.poi.id == "cafe")
        self.assertEqual(cafe.matched_preferences, ["咖啡馆"])
        original_shortlist = self.agent.shortlist
        self.agent.shortlist = lambda *args: [p for p in original_shortlist(*args) if p.poi_id != "cafe"]
        self.jev.preferred = ["cafe"]
        self.amap.only_alternative = True
        self.jev.calls.clear()
        result = await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=first.data.trip_id, state_version=1, locked_place_ids=["cafe"],
        ))
        self.assertEqual([p.poi.id for p in result.data.places], ["park1", "cafe", "book"])
        category_call = next(questions for state, questions in self.jev.calls
                             if state["next_order_preference"] == "cafe")
        criteria = category_call["next_poi"]["criteria"]
        self.assertIn("cafe", criteria)
        self.assertIn("other-cafe", criteria)
        self.assertEqual(result.data.places[1].matched_preferences, ["咖啡馆"])

        # 继续调整时不依赖原因文案，也不依赖搜索再次命中该地点。
        self.jev.preferred = ["cafe"]
        result = await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=first.data.trip_id, state_version=2,
        ))
        self.assertEqual(result.data.places[1].poi.id, "cafe")
        self.assertEqual(result.data.places[1].matched_preferences, ["咖啡馆"])

    async def test_natural_language_lock_uses_the_same_category_candidates(self):
        first = await self.first()
        self.jev.preferred = ["cafe"]
        self.amap.only_alternative = True
        self.jev.calls.clear()
        main = MainAgent(
            self.trips,
            FakeExtractor([PreferencePatch(lock_place_names=["转角咖啡"],
                                           routing_english="Keep the cafe")]),
            FakeRouter(["CITYWALK"]), agents={"CITYWALK": self.agent},
        )
        result = await main.plan(TravelRequest(
            trip_id=first.data.trip_id, state_version=1, message="保留转角咖啡",
        ))
        self.assertEqual(result.data.places[1].poi.id, "cafe")
        category_call = next(q for state, q in self.jev.calls if state["next_order_preference"] == "cafe")
        self.assertIn("cafe", category_call["next_poi"]["criteria"])

    async def test_empty_search_still_includes_locked_place(self):
        first = await self.first()
        self.amap.no_cafe = True
        result = await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=first.data.trip_id, state_version=1, target_stops=2,
            interests=["咖啡馆"], visit_order=["咖啡馆"], locked_place_ids=["cafe"],
        ))
        self.assertEqual([p.poi.id for p in result.data.places], ["cafe"])

    async def test_two_locked_places_in_the_same_category_are_both_scheduled(self):
        original_search = self.amap.search_around
        async def two_cafes(city, lon, lat, keyword, radius, types):
            places = await original_search(city, lon, lat, keyword, radius, types)
            if keyword == "咖啡馆":
                places.append(Place("cafe2", "第二家咖啡", "测试路", "咖啡馆",
                                    121.4702, 31.23, "", keyword))
            return places
        self.amap.search_around = two_cafes
        first = await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            city="上海", start_location=ORIGIN, target_stops=3, interests=["咖啡馆", "公园"],
            visit_order=["咖啡馆", "咖啡馆", "公园"],
        ))
        self.assertEqual([p.poi.id for p in first.data.places], ["cafe", "cafe2", "park1"])
        self.amap.search_around = original_search
        self.amap.only_alternative = True
        self.jev.preferred = ["cafe", "cafe2", "park1"]
        result = await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=first.data.trip_id, state_version=1, locked_place_ids=["cafe", "cafe2"],
            visit_order=["咖啡馆", "咖啡馆", "公园"],
        ))
        self.assertEqual([p.poi.id for p in result.data.places], ["cafe", "cafe2", "park1"])

    async def test_two_visits_same_category_jev_can_pick_other_then_locked(self):
        first = await self.first()
        self.amap.only_alternative = True
        self.jev.calls.clear()
        self.jev.preferred = ["other-cafe", "cafe", "book"]
        self.amap.routes.clear()
        original_decide = self.jev.decide
        async def decide(state, questions):
            if not state["selected"]:
                self.assertTrue(all(start == "origin" for start, _ in self.amap.routes))
            return await original_decide(state, questions)
        self.jev.decide = decide
        result = await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=first.data.trip_id, state_version=1, locked_place_ids=["cafe"],
            interests=["咖啡馆", "书店"], visit_order=["咖啡馆", "咖啡馆", "书店"],
        ))
        self.assertEqual([p.poi.id for p in result.data.places], ["other-cafe", "cafe", "book"])
        first_state, first_questions = self.jev.calls[0]
        criteria = first_questions["next_poi"]["criteria"]
        self.assertTrue(criteria["cafe"]["is_locked"])
        self.assertFalse(criteria["other-cafe"]["is_locked"])
        self.assertEqual([p["id"] for p in first_state["required_locked_places"]], ["cafe"])
        self.assertIn("exactly once", first_questions["next_poi"]["instructions"])
        second_state, second_questions = self.jev.calls[1]
        self.assertEqual([p["id"] for p in second_state["required_locked_places"]], ["cafe"])
        self.assertNotIn("other-cafe", second_questions["next_poi"]["criteria"])

    async def test_two_visits_same_category_jev_can_pick_locked_then_other(self):
        first = await self.first()
        self.amap.only_alternative = True
        self.jev.calls.clear()
        self.jev.preferred = ["cafe", "other-cafe", "book"]
        result = await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=first.data.trip_id, state_version=1, locked_place_ids=["cafe"],
            interests=["咖啡馆", "书店"], visit_order=["咖啡馆", "咖啡馆", "书店"],
        ))
        self.assertEqual([p.poi.id for p in result.data.places], ["cafe", "other-cafe", "book"])
        first_criteria = self.jev.calls[0][1]["next_poi"]["criteria"]
        self.assertIn("cafe", first_criteria)
        self.assertIn("other-cafe", first_criteria)
        second_state, second_questions = self.jev.calls[1]
        self.assertEqual(second_state["required_locked_places"], [])
        self.assertTrue(second_state["selected"][0]["is_locked"])
        self.assertNotIn("cafe", second_questions["next_poi"]["criteria"])

    async def test_single_category_slot_keeps_other_candidates_for_jev(self):
        first = await self.first()
        self.amap.only_alternative = True
        self.jev.calls.clear()
        self.jev.preferred = ["park1", "other-cafe", "book"]
        with self.assertRaisesRegex(ValueError, "保留所有锁定地点"):
            await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
                trip_id=first.data.trip_id, state_version=1, locked_place_ids=["cafe"],
            ))
        state, questions = next((state, q) for state, q in self.jev.calls
                                if state["next_order_preference"] == "cafe")
        self.assertIn("cafe", questions["next_poi"]["criteria"])
        self.assertIn("other-cafe", questions["next_poi"]["criteria"])
        self.assertEqual([p["id"] for p in state["required_locked_places"]], ["cafe"])
        self.assertEqual(self.trips.get_trip(first.data.trip_id).latest_version, 1)

    async def test_jev_cannot_select_same_place_twice(self):
        first = await self.first()
        self.amap.only_alternative = True
        async def duplicate(state, questions):
            return {"next_poi": {"type": "choice", "choice": "cafe"}}
        self.jev.decide = duplicate
        with self.assertRaisesRegex(ValueError, "候选范围外"):
            await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
                trip_id=first.data.trip_id, state_version=1, locked_place_ids=["cafe"],
                interests=["咖啡馆", "书店"], visit_order=["咖啡馆", "咖啡馆", "书店"],
            ))
        self.assertEqual(self.trips.get_trip(first.data.trip_id).latest_version, 1)

    async def test_jev_declines_a_required_lock_does_not_save(self):
        first = await self.first()
        self.amap.only_alternative = True
        async def decline(state, questions):
            return {"next_poi": {"type": "choice", "choice": "__no_suitable_poi__"}}
        self.jev.decide = decline
        with self.assertRaisesRegex(ValueError, "未保留所有锁定地点"):
            await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
                trip_id=first.data.trip_id, state_version=1, locked_place_ids=["cafe"],
            ))
        self.assertEqual(self.trips.get_trip(first.data.trip_id).latest_version, 1)

    async def test_all_locked_still_follow_category_order_without_search(self):
        first = await self.first()
        ids = [p.poi.id for p in first.data.places]
        self.agent.discover = unittest.mock.AsyncMock(side_effect=AssertionError("不应搜索"))
        result = await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=first.data.trip_id, state_version=1, locked_place_ids=ids,
        ))
        self.assertEqual([p.poi.id for p in result.data.places], ids)
        self.agent.discover.assert_not_awaited()

    async def test_recovers_legacy_reason_from_older_history(self):
        first = await self.first()
        trip_id = first.data.trip_id
        await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=trip_id, state_version=1, locked_place_ids=["cafe"],
        ))
        # 模拟旧快照：没有结构化来源，最近一次锁定又覆盖了旧原因。
        with self.store._connect() as conn:
            for version in (1, 2):
                row = conn.execute("SELECT plan_json FROM trip_history WHERE trip_id=? AND version=?",
                                   (trip_id, version)).fetchone()
                plan = json.loads(row["plan_json"])
                for place in plan["places"]:
                    place.pop("matched_preferences", None)
                    if version == 2 and place["poi"]["id"] == "cafe":
                        place["why_selected"] = "用户锁定的原计划地点"
                payload = json.dumps(plan, ensure_ascii=False)
                conn.execute("UPDATE trip_history SET plan_json=? WHERE trip_id=? AND version=?",
                             (payload, trip_id, version))
                if version == 2:
                    conn.execute("UPDATE trips SET plan_json=? WHERE trip_id=?", (payload, trip_id))
        self.amap.only_alternative = True
        self.jev.preferred = ["cafe"]
        result = await self.main.plan_for("CITYWALK", CityWalkPlanRequest(
            trip_id=trip_id, state_version=2,
        ))
        self.assertEqual(result.data.places[1].poi.id, "cafe")
        self.assertEqual(result.data.places[1].matched_preferences, ["咖啡馆"])
        # 回溯旧版本时不得使用该版本之后的来源信息。
        with self.store._connect() as conn:
            row = conn.execute("SELECT plan_json FROM trip_history WHERE trip_id=? AND version=1",
                               (trip_id,)).fetchone()
            data = json.loads(row["plan_json"])
            for place in data["places"]:
                if place["poi"]["id"] == "cafe":
                    place["why_selected"] = "没有类别来源"
            conn.execute("UPDATE trip_history SET plan_json=? WHERE trip_id=? AND version=1",
                         (json.dumps(data, ensure_ascii=False), trip_id))
        self.assertEqual(self.store.walk_place_preferences(trip_id, 1, {"cafe"}), {})


if __name__ == "__main__":
    unittest.main()
