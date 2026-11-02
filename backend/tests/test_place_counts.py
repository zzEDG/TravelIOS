"""实际三子 Agent 在 HTTP 层生成五个、缩减、增加及编辑历史版本。"""
from unittest.mock import AsyncMock
import tempfile
import unittest
from narrator_fixtures import OfflineNarrationMixin
from pathlib import Path
from unittest.mock import Mock

from fastapi.testclient import TestClient

from app.agents.main_agent import MainAgent
from app.controllers.dependencies import get_main_agent
from app.agents.citywalk_agent import CityWalkAgent
from app.agents.attraction_agent import AttractionAgent
from app.agents.food_agent import FoodAgent
from app.integrations.amap_service import Place, WalkingRoute
from app.main import app
from app.models.trip_store import TripStore
from app.services.count_preferences import count_edit
from app.services.preferences import PreferenceExtractor, PreferencePatch
from app.services.trip_service import TripService
from app.controllers.dependencies import get_trip_service
from test_travel_stage2 import FakeExtractor, FakeRouter
from walk_fixtures import fake_walk_preferences


class NearbyPlaces:
    def __init__(self, count=7):
        self.count = count
        self.searches = []

    async def search_around(self, city, lon, lat, keyword, radius_meters, types):
        self.searches.append((keyword, radius_meters))
        return [
            Place(f"p{i}", f"地点{i}", "测试路", "餐厅或博物馆",
                  121.47 + i * .0001, 31.23 + i * .0001, "310000", keyword)
            for i in range(1, self.count + 1)
        ]

    async def search(self, city, keyword):
        raise AssertionError("已有起点的步行应搜索周边，不能使用全城前20条结果")

    async def walking_route(self, origin, destination):
        return WalkingRoute(100, 2, "121.47,31.23;121.471,31.231")


class Translator:
    async def brief(self, request, allowed):
        return {"city": "Shanghai", "theme": "Architecture", "notes": ""}, {word: "Category" for word in allowed}

    async def request(self, message):
        return "Find places fitting the preferences"

    async def places(self, places):
        return {p.poi_id: {"name": "Place", "category": "Category", "address": "Road"} for p in places}


class Choice:
    def __init__(self):
        self.states = []

    async def decide(self, state, questions):
        self.states.append(state)
        key = next(iter(questions))
        if all(q["type"] == "score" for q in questions.values()):
            return {name: {"type": "score", "score": 2} for name in questions}
        return {key: {"type": "choice", "choice": next(iter(questions[key]["criteria"]))}}


class LockAwareChoice(Choice):
    async def decide(self, state, questions):
        self.states.append(state)
        key = next(iter(questions))
        if all(q["type"] == "score" for q in questions.values()):
            return {name: {"type": "score", "score": 2} for name in questions}
        criteria = questions[key]["criteria"]
        available = [poi_id for poi_id, detail in criteria.items() if isinstance(detail, dict)]
        locked = [poi_id for poi_id in available if criteria[poi_id]["is_locked"]]
        return {key: {"type": "choice", "choice": (locked or available)[0]}}


class PlaceCountTests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.places = NearbyPlaces()
        self.trips = TripService(TripStore(Path(self.temp.name)/"trips.sqlite3"), self.places)
        weather = Mock()
        weather.lookup = AsyncMock(return_value=(None, "离线测试"))
        walk = CityWalkAgent.__new__(CityWalkAgent)
        walk.parse_preferences = fake_walk_preferences
        walk.amap, walk.jev, walk.translator = self.places, Choice(), Translator()
        walk.weather_service = weather
        walk.unsplash = Mock()
        walk.unsplash.cover = AsyncMock(return_value=None)
        self.children = {
            "CITYWALK": walk,
            "ATTRACTION": AttractionAgent(self.places, Choice(), Translator(), weather),
            "FOOD": FoodAgent(self.places, Choice(), Translator()),
        }
        self.main = MainAgent(self.trips, agents=self.children)
        app.dependency_overrides[get_main_agent] = lambda: self.main
        app.dependency_overrides[get_trip_service] = lambda: self.trips
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def create(self, skill, count=5, **extra):
        paths = {"CITYWALK": "citywalk/plan", "ATTRACTION": "attraction/plan", "FOOD": "food/search"}
        positions = {"CITYWALK": "start_location", "ATTRACTION": "location", "FOOD": "center_location"}
        payload = {"city": "上海", positions[skill]: {"latitude":31.23,"longitude":121.47},
                   "additional_requirements": "安静一些"}
        if skill == "FOOD":
            payload["limit"] = count
        else:
            payload.update(available_hours=3, target_stops=count)
        if skill == "CITYWALK":
            payload["theme"] = "建筑与咖啡"
        payload.update(extra)
        result = self.client.post("/api/v1/"+paths[skill], json=payload)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["data"]

    async def test_five_then_three_then_five_via_chat_for_every_subsystem(self):
        # 数量续接不能调用抽取/路由模型，也不能误切换能力。
        self.main.extractor = Mock()
        self.main.extractor.extract.side_effect = AssertionError("纯数量修改无需调用模型")
        self.main.router = Mock()
        self.main.router.route.side_effect = AssertionError("纯数量修改不能重新路由")
        for skill in self.children:
            with self.subTest(skill=skill):
                first = self.create(skill)
                self.assertEqual(len(first["places"]), 5)
                trip_id = first["trip_id"]
                for version, text, expected in ((1, "改成三个地点", 3), (2, "再加两个", 5)):
                    response = self.client.post("/api/v1/travel", json={
                        "trip_id": trip_id, "state_version": version, "base_version": version,
                        "message": text,
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    updated = response.json()["data"]
                    self.assertEqual((updated["routed_skill"], len(updated["places"])), (skill, expected))
                    self.assertEqual(updated["state_version"], version + 1)
                    state = self.trips.store.get(trip_id).state
                    self.assertEqual(state.food_limit if skill=="FOOD" else state.target_stops, expected)
                    self.assertEqual(state.additional_requirements, "安静一些")
                self.assertEqual(self.trips.store.history(trip_id=trip_id).total, 3)
                self.assertEqual(len(self.trips.store.get_version(trip_id, 2).latest_plan.places), 3)
                # 从旧版本继续改，保存仍使用最新版本锁。
                result = self.client.post("/api/v1/travel", json={
                    "trip_id":trip_id,"state_version":3,"base_version":1,"message":"改成四个地点",
                })
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(len(result.json()["data"]["places"]), 4)
                self.assertEqual(result.json()["data"]["state_version"], 4)
        self.main.extractor.extract.assert_not_called()
        self.main.router.route.assert_not_called()

    async def test_add_one_stop_with_end_preference_uses_relative_count(self):
        first = self.create("CITYWALK", 5)
        self.main.extractor = FakeExtractor([PreferencePatch(
            target_stops=1, end_with="公园", additional_requirements="最后适合散步消食",
            routing_english="Add a stop and end somewhere suitable for a walk after a meal",
        )])
        self.main.router = FakeRouter(["CITYWALK"])
        response = self.client.post("/api/v1/travel", json={
            "trip_id": first["trip_id"], "state_version": 1,
            "message": "加一个地点，再最后面好散步的，消消食",
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]
        self.assertEqual(result["state_version"], 2)
        self.assertEqual(len(result["places"]), 6)
        state = self.trips.store.get(first["trip_id"]).state
        self.assertEqual(state.target_stops, 6)
        self.assertEqual(state.end_with, "公园")
        self.assertIn("散步", state.additional_requirements)
        self.assertEqual(len(self.trips.store.get_version(first["trip_id"], 1).latest_plan.places), 5)

    async def test_shanghai_reduce_to_four_and_keep_two_named_places(self):
        from langchain_core.runnables import RunnableLambda
        search = self.places.search_around

        async def named_search(*args, **kwargs):
            places = await search(*args, **kwargs)
            from dataclasses import replace
            return [replace(p, name={"p2": "石藤咖啡(人民广场店)",
                                     "p3": "百新书局"}.get(p.poi_id, p.name)) for p in places]

        self.places.search_around = named_search
        first = self.create("CITYWALK", 6)

        class LLMWithChineseNames:
            def with_structured_output(self, schema, *, include_raw=False):
                return RunnableLambda(lambda messages: {"raw": None, "parsing_error": None, "parsed": {
                    "target_stops": 4, "lock_place_names": ["石藤咖啡", "百新书局"],
                    "routing_english": "Change the citywalk to 4 stops, keep 石藤咖啡 and 百新书局, and leave other preferences unchanged.",
                }})

        self.main.extractor = PreferenceExtractor(LLMWithChineseNames())
        self.main.router = Mock()
        from app.services.intent_router import RouteDecision
        self.main.router.route = AsyncMock(return_value=RouteDecision("CITYWALK", 1.0))
        self.children["CITYWALK"].jev = LockAwareChoice()
        response = self.client.post("/api/v1/travel", json={
            "trip_id": first["trip_id"], "state_version": 1,
            "message": "改成4个地点吧，保留石藤咖啡和百新书局",
        })
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()["data"]
        self.assertEqual(len(result["places"]), 4)
        self.assertTrue({"p2", "p3"}.issubset({p["poi"]["id"] for p in result["places"]}))
        state = self.trips.store.get(first["trip_id"]).state
        self.assertEqual(state.target_stops, 4)
        self.assertEqual(set(state.locked_place_ids), {"p2", "p3"})
        routed = self.main.router.route.await_args.args[0]
        self.assertIn("4 stops", routed)
        self.assertFalse(any("\u3400" <= ch <= "\u9fff" for ch in routed))

    async def test_keep_all_existing_places_when_adding_one(self):
        first = self.create("CITYWALK", 5)
        original_ids = {place["poi"]["id"] for place in first["places"]}
        # Jev 模拟会根据实际候选与剩余锁定地点做判断；不移除其他候选。
        chooser = LockAwareChoice()
        self.children["CITYWALK"].jev = chooser
        # 即使模型没有列出旧地点，也应识别明确的“其他的点不变”。
        self.main.extractor = FakeExtractor([PreferencePatch(
            target_stops=1, end_with="公园", additional_requirements="适合饭后散步",
            unlock_place_names=["地点1"],  # 模型误判解除锁定不能覆盖用户明确的整体保留。
            routing_english="Add one stop for a walk and keep all existing stops",
        )])
        self.main.router = FakeRouter(["CITYWALK"])
        response = self.client.post("/api/v1/travel", json={
            "trip_id": first["trip_id"], "state_version": 1,
            "message": "新增一个点，适合饭后散步，其他的点不变",
        })
        self.assertEqual(response.status_code, 200, response.text)
        updated = response.json()["data"]
        self.assertEqual(len(updated["places"]), 6)
        self.assertTrue(original_ids.issubset({place["poi"]["id"] for place in updated["places"]}))
        state = self.trips.store.get(first["trip_id"]).state
        self.assertEqual(state.target_stops, 6)
        self.assertEqual(set(state.locked_place_ids), original_ids)
        self.assertEqual(state.end_with, "公园")
        self.assertEqual({item["id"] for item in chooser.states[0]["required_locked_places"]}, original_ids)

    async def test_keep_all_uses_selected_historical_version(self):
        first = self.create("CITYWALK", 5)
        original_ids = {p["poi"]["id"] for p in first["places"]}
        shortened = self.client.post("/api/v1/citywalk/plan", json={
            "trip_id": first["trip_id"], "state_version": 1, "target_stops": 3,
        })
        self.assertEqual(shortened.status_code, 200, shortened.text)
        self.children["CITYWALK"].jev = LockAwareChoice()
        self.main.extractor = FakeExtractor([PreferencePatch(
            routing_english="Keep all saved places and add one",
        )])
        self.main.router = FakeRouter(["CITYWALK"])
        result = self.client.post("/api/v1/travel", json={
            "trip_id": first["trip_id"], "state_version": 2, "base_version": 1,
            "message": "新增一个点，其他的点不变",
        })
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["data"]["state_version"], 3)
        self.assertEqual(len(result.json()["data"]["places"]), 6)
        self.assertEqual(set(self.trips.store.get(first["trip_id"]).state.locked_place_ids), original_ids)

    async def test_keep_all_in_other_subsystems(self):
        for skill in ("ATTRACTION", "FOOD"):
            with self.subTest(skill=skill):
                first = self.create(skill, 5)
                ids = {item["poi"]["id"] for item in first["places"]}
                self.main.extractor = FakeExtractor([PreferencePatch(
                    target_stops=1, limit=1, routing_english="Add one and keep existing places",
                )])
                self.main.router = FakeRouter([skill])
                result = self.client.post("/api/v1/travel", json={
                    "trip_id": first["trip_id"], "state_version": 1,
                    "message": "新增一个点，其他的点不变",
                })
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(len(result.json()["data"]["places"]), 6)
                self.assertTrue(ids.issubset({p["poi"]["id"] for p in result.json()["data"]["places"]}))
                self.assertEqual(set(self.trips.store.get(first["trip_id"]).state.locked_place_ids), ids)

    async def test_model_can_request_bulk_keep_without_matching_literal_phrase(self):
        first = self.create("CITYWALK", 5)
        ids = {place["poi"]["id"] for place in first["places"]}
        self.children["CITYWALK"].jev = LockAwareChoice()
        self.main.extractor = FakeExtractor([PreferencePatch(
            keep_all_previous_places=True, routing_english="Keep all existing places and add one",
        )])
        self.main.router = FakeRouter(["CITYWALK"])
        response = self.client.post("/api/v1/travel", json={
            "trip_id": first["trip_id"], "state_version": 1,
            "message": "这些地方都留着，增加一个点",
        })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["data"]["places"]), 6)
        self.assertEqual(set(self.trips.store.get(first["trip_id"]).state.locked_place_ids), ids)

    async def test_form_count_changes_use_same_trip_and_update_result(self):
        for skill in self.children:
            with self.subTest(skill=skill):
                first = self.create(skill, 2)
                path = {"CITYWALK":"citywalk/plan","ATTRACTION":"attraction/plan","FOOD":"food/search"}[skill]
                field = "limit" if skill=="FOOD" else "target_stops"
                result = self.client.post("/api/v1/"+path, json={
                    "trip_id":first["trip_id"],"state_version":1,field:5,
                })
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(len(result.json()["data"]["places"]), 5)
                self.assertEqual(result.json()["data"]["trip_id"], first["trip_id"])

    async def test_explicit_count_overrides_model_old_count_in_mixed_request(self):
        for skill in self.children:
            with self.subTest(skill=skill):
                self.main.extractor = FakeExtractor([PreferencePatch(
                    theme="建筑与咖啡", target_stops=3, limit=3,
                    routing_english="Plan suitable places and keep the walk quiet",
                )])
                self.main.router = FakeRouter([skill])
                response = self.client.post("/api/v1/travel", json={
                    "message":"规划5个地点，环境安静一些", "city":"上海",
                    "location":{"latitude":31.23,"longitude":121.47},
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(len(response.json()["data"]["places"]), 5)

    async def test_insufficient_candidates_and_time_are_visible_without_fabrication(self):
        self.places.count = 3
        food = self.create("FOOD")
        self.assertEqual(len(food["places"]), 3)
        self.assertIn("目标为 5", food["notes"][0])
        self.assertIn("实际返回 3", food["notes"][0])
        self.places.count = 7
        attractions = self.create("ATTRACTION", available_hours=1)
        self.assertEqual(len(attractions["places"]), 5)
        self.assertIsNone(attractions["estimated_minutes"])
        self.assertEqual(attractions["routes"], [])

    async def test_reject_invalid_counts_without_saving_new_version(self):
        first = self.create("FOOD", 3)
        for message in ("改成1001个", "改成零个", "减少五个"):
            with self.subTest(message=message):
                response = self.client.post("/api/v1/travel", json={
                    "trip_id":first["trip_id"], "state_version":1, "message":message,
                })
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.trips.store.get(first["trip_id"]).state.state_version, 1)

    async def test_selection_model_receives_requested_and_remaining_counts(self):
        for skill, child in self.children.items():
            with self.subTest(skill=skill):
                self.create(skill)
                if skill in ("ATTRACTION", "FOOD"):
                    self.assertEqual(len(child.jev.states), 1)
                    self.assertEqual(child.jev.states[0]["target_count"], 5)
                    continue
                choices = [state for state in child.jev.states if "remaining_stops" in state or "remaining_count" in state]
                self.assertEqual(len(choices), 5)
                self.assertEqual([state.get("remaining_stops", state.get("remaining_count")) for state in choices], [5,4,3,2,1])
                self.assertTrue(all(state.get("target_stops", state.get("target_count")) == 5 for state in choices))

    async def test_count_parser_does_not_confuse_time_distance_or_ambiguous_quantities(self):
        for text in ("改成5小时", "改成5", "最多五个地点", "不要五个地点", "3个景点和2家餐厅", "3到5个地点", "上次去过五个地点，这次找餐厅", "不新增一个地点", "今天3点出发"):
            with self.subTest(text=text):
                self.assertIsNone(count_edit(text))
        self.assertEqual(count_edit("增加到五个地点").value, 5)
        self.assertEqual(count_edit("再加两个").delta, 1)
        for text in ("加一个地点，再最后面好散步的，消消食", "加上一个地点", "多加一个地点", "新增一个点，适合饭后散步，其他的点不变"):
            with self.subTest(text=text):
                edit = count_edit(text)
                self.assertEqual((edit.value, edit.delta), (1, 1))
        self.assertEqual(count_edit("加到五个地点").delta, 0)
        self.assertEqual(count_edit("地点数量改成5").value, 5)
        self.assertTrue(count_edit("餐厅数量改为五").restaurant)
        self.assertFalse(count_edit("改成五家餐厅").applies_to("CITYWALK"))
        self.assertFalse(count_edit("改成五个景点").applies_to("FOOD"))
        self.assertTrue(count_edit("改成五个地点").applies_to("FOOD"))


if __name__ == "__main__":
    unittest.main()
