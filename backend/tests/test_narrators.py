"""三个实际规划与讲述 Agent 的 HTTP 链路、修改对比、降级和版本持久化。"""
from unittest.mock import AsyncMock
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.agents.main_agent import MainAgent
from app.agents.citywalk_agent import CityWalkAgent
from app.agents.attraction_agent import AttractionAgent
from app.agents.food_agent import FoodAgent
from app.agents.narrators import NARRATOR_TYPES
from app.agents.narrators.citywalk import CityWalkNarratorAgent, LegacyWalkStory
from app.controllers.dependencies import get_main_agent, get_trip_service
from app.main import app
from app.models.trip_store import TripStore
from app.services.preferences import PreferencePatch
from app.services.trip_service import TripService
from app.services.plan_changes import describe_changes
from test_place_counts import NearbyPlaces, Translator, Choice
from test_travel_stage2 import FakeExtractor, FakeRouter
from walk_fixtures import fake_walk_preferences
from candidate_fixtures import OfflineCandidateSearch


class NarrationModel:
    def __init__(self):
        self.calls = []
        self.failure = None

    def with_structured_output(self, schema):
        async def ainvoke(messages):
            if self.failure:
                raise self.failure
            payload = json.loads(messages[-1].content)
            self.calls.append((schema, messages[0].content, payload))
            if schema is LegacyWalkStory:
                return schema(title='测试漫步', introduction='根据真实地点安排漫步。',
                              reasons=['适合本次主题'] * len(payload['places']), rain_tip='出发前确认天气。')
            return schema(evaluation='这份规划结合了已核实的地点和路线，可按本次偏好安排出行。')
        return SimpleNamespace(ainvoke=ainvoke)


class NarratorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.model = NarrationModel()
        self.resources = SimpleNamespace(llm=self.model)
        self.amap = NearbyPlaces()
        self.trips = TripService(TripStore(Path(temp.name)/'trips.sqlite3'), self.amap)
        weather = Mock()
        weather.lookup = AsyncMock(return_value=(None, '离线无天气'))
        walk = CityWalkAgent.__new__(CityWalkAgent)
        walk.parse_preferences = fake_walk_preferences
        walk.amap, walk.jev, walk.translator = self.amap, Choice(), Translator()
        walk.weather_service = weather
        walk.unsplash = Mock()
        walk.unsplash.cover = AsyncMock(return_value=None)
        self.main = MainAgent(self.trips, resources=self.resources, agents={
            'CITYWALK': walk,
            'ATTRACTION': AttractionAgent(self.amap, Choice(), Translator(), weather),
            'FOOD': FoodAgent(self.amap, Choice(), Translator()),
        })
        for agent in self.main.agents.values():
            agent.candidate_search = OfflineCandidateSearch()
        app.dependency_overrides.update({get_main_agent: lambda: self.main,
                                         get_trip_service: lambda: self.trips})
        self.addCleanup(app.dependency_overrides.clear)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def create(self, skill, count=5, **extra):
        paths = {'CITYWALK': 'citywalk/plan', 'ATTRACTION': 'attraction/plan', 'FOOD': 'food/search'}
        positions = {'CITYWALK': 'start_location', 'ATTRACTION': 'location', 'FOOD': 'center_location'}
        body = {'city': '上海', positions[skill]: {'latitude': 31.23, 'longitude': 121.47}}
        if skill == 'FOOD':
            body['limit'] = count
        else:
            body.update(target_stops=count, available_hours=3)
        if skill == 'CITYWALK':
            body['theme'] = '建筑与咖啡'
        response = self.client.post('/api/v1/'+paths[skill], json={**body, **extra})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_each_subsystem_runs_its_own_narrator_once(self):
        focuses = {'CITYWALK': '城市漫步', 'ATTRACTION': '景点游览', 'FOOD': '独立备选'}
        for skill, cls in NARRATOR_TYPES.items():
            before = len(self.model.calls)
            result = self.create(skill)
            narration = result['data']['narration']
            self.assertEqual(narration['narrator'], cls.name)
            self.assertEqual(narration['changes'], [])
            self.assertIsNone(narration['based_on_version'])
            self.assertEqual(narration['source'], 'LLM')
            self.assertEqual(len(self.model.calls), before + 1)
            self.assertIn(focuses[skill], self.model.calls[-1][1])
            self.assertEqual(len(self.model.calls[-1][2]['current']['places']), 5)
            self.assertIsNone(self.model.calls[-1][2]['previous'])

    async def test_only_selected_narrator_is_initialized_and_clarification_skips_narration(self):
        self.create('FOOD')
        self.assertEqual(set(self.main.narrators), {'FOOD'})
        self.main.extractor = FakeExtractor([PreferencePatch(routing_english='Unclear request')])
        self.main.router = FakeRouter(['CLARIFY'])
        response = self.client.post('/api/v1/travel', json={'message': '帮忙安排'})
        self.assertEqual(response.json()['data']['routed_skill'], 'CLARIFY')
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(set(self.main.narrators), {'FOOD'})

    async def test_chat_count_edits_are_narrated_for_all_three_subsystems(self):
        for skill in NARRATOR_TYPES:
            first = self.create(skill)['data']
            self.main.extractor = Mock()
            self.main.extractor.extract.side_effect = AssertionError('数量修改无需抽取')
            self.main.router = Mock()
            self.main.router.route.side_effect = AssertionError('数量修改无需重路由')
            response = self.client.post('/api/v1/travel', json={
                'trip_id': first['trip_id'], 'state_version': 1, 'message': '改成三个地点',
            })
            self.assertEqual(response.status_code, 200, response.text)
            narration = response.json()['data']['narration']
            self.assertIn('目标地点数量：5 → 3', narration['changes'])
            self.assertIn('实际地点数量：5 → 3', narration['changes'])
            self.assertEqual(narration['user_request'], '改成三个地点')
            self.assertEqual(narration['based_on_version'], 1)
            self.assertEqual(self.model.calls[-1][2]['user_request'], '改成三个地点')
            self.assertIn('目标地点数量：5 → 3', self.model.calls[-1][2]['changes'])
            self.assertNotIn('narration', self.model.calls[-1][2]['previous'])

    async def test_form_edits_show_preferences_clears_and_actual_place_changes(self):
        first = self.create('FOOD', avoid=['花生'])['data']
        response = self.client.post('/api/v1/food/search', json={
            'trip_id': first['trip_id'], 'state_version': 1, 'limit': 3,
            'cuisine_preference': ['川菜'], 'avoid': [], 'max_distance_meters': 1200,
        })
        self.assertEqual(response.status_code, 200, response.text)
        changes = response.json()['data']['narration']['changes']
        self.assertIn('菜系偏好：未设置 → 川菜', changes)
        self.assertIn('忌口偏好：花生 → 未设置', changes)
        self.assertIn('搜索半径（米）：1500 → 1200', changes)
        self.assertTrue(any(text.startswith('移除地点：') for text in changes))
        self.assertEqual(self.model.calls[-1][2]['requested_fields']['avoid'], [])

    async def test_history_edit_compares_selected_base_and_keeps_its_narration(self):
        first = self.create('FOOD')['data']
        second = self.client.post('/api/v1/food/search', json={
            'trip_id': first['trip_id'], 'state_version': 1, 'limit': 3,
        })
        self.assertEqual(second.status_code, 200)
        edited = self.client.post('/api/v1/food/search', json={
            'trip_id': first['trip_id'], 'state_version': 2, 'base_version': 1, 'limit': 2,
        })
        self.assertEqual(edited.status_code, 200, edited.text)
        narration = edited.json()['data']['narration']
        self.assertEqual(narration['based_on_version'], 1)
        self.assertIn('目标地点数量：5 → 2', narration['changes'])
        restored = self.client.get('/api/v1/trips/'+first['trip_id'], params={'version': 1})
        self.assertEqual(restored.json()['data']['latest_plan']['narration'], first['narration'])
        self.assertEqual(restored.json()['data']['latest_version'], 3)
        self.assertEqual(len(self.model.calls), 3)

    async def test_narrator_failure_still_saves_plan_and_change_details(self):
        for skill in NARRATOR_TYPES:
            first = self.create(skill)['data']
            self.model.failure = RuntimeError('model unavailable')
            with patch('app.agents.narrators.base.logger'):
                response = self.client.post('/api/v1/travel', json={
                    'trip_id': first['trip_id'], 'state_version': 1, 'message': '改成三个地点',
                })
            self.model.failure = None
            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            narration = result['data']['narration']
            self.assertEqual(narration['source'], 'RULES')
            self.assertIn('目标地点数量：5 → 3', narration['changes'])
            self.assertIn('NARRATOR', result['meta']['degraded_services'])
            self.assertTrue(result['meta']['fallback_applied'])
            self.assertEqual(self.trips.store.get(first['trip_id']).latest_plan.narration.source, 'RULES')

    async def test_short_results_do_not_claim_target_is_met(self):
        self.amap.count = 3
        for skill in NARRATOR_TYPES:
            result = self.create(skill)
            self.assertIn('目标为 5 个，实际提供 3 个', result['data']['narration']['evaluation'])

    async def test_switching_subsystems_selects_new_narrator_and_explains_switch(self):
        first = self.create('CITYWALK')['data']
        self.main.extractor = FakeExtractor([PreferencePatch(limit=2, routing_english='Find dinner')])
        self.main.router = FakeRouter(['FOOD'])
        response = self.client.post('/api/v1/travel', json={
            'trip_id': first['trip_id'], 'state_version': 1, 'message': '改找两家餐厅',
        })
        self.assertEqual(response.status_code, 200, response.text)
        narration = response.json()['data']['narration']
        self.assertEqual(narration['narrator'], 'FoodNarratorAgent')
        self.assertIn('规划类型：城市漫步 → 餐厅推荐', narration['changes'])
        self.assertIn('目标地点数量：5 → 2', narration['changes'])

    async def test_old_saved_plans_restore_without_narration_and_without_model_call(self):
        first = self.create('FOOD')['data']
        with self.trips.store._connect() as connection:
            for table in ('trips', 'trip_history'):
                connection.execute(f"UPDATE {table} SET plan_json=json_remove(plan_json, '$.narration')")
        self.model.failure = AssertionError('读取历史不应调用模型')
        response = self.client.get('/api/v1/trips/'+first['trip_id'])
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['data']['latest_plan']['narration'])
        self.assertEqual(len(self.model.calls), 1)

    async def test_legacy_citywalk_story_uses_citywalk_narrator(self):
        result = await CityWalkNarratorAgent(self.resources).legacy_story({
            'city': '上海', 'theme': '建筑与咖啡', 'places': [{'name': '博物馆'}, {'name': '咖啡馆'}],
        })
        self.assertEqual(result['title'], '测试漫步')
        self.assertEqual(len(result['reasons']), 2)

    async def test_restaurant_reordering_describes_alternatives_not_a_tour_route(self):
        first = self.create('FOOD', 3)['data']
        previous = self.trips.store.get(first['trip_id'])
        revised = previous.latest_plan.model_copy(deep=True)
        revised.places.reverse()
        changes = describe_changes(previous, previous.state, revised)
        self.assertTrue(any(text.startswith('备选排序调整为：') for text in changes))
        self.assertEqual(previous.latest_plan.places[0].poi.id, first['places'][0]['poi']['id'])

    async def test_stale_update_does_not_invoke_narrator(self):
        first = self.create('FOOD', 3)['data']
        body = {'trip_id': first['trip_id'], 'state_version': 1, 'limit': 2}
        self.assertEqual(self.client.post('/api/v1/food/search', json=body).status_code, 200)
        count = len(self.model.calls)
        self.assertEqual(self.client.post('/api/v1/food/search', json=body).status_code, 409)
        self.assertEqual(len(self.model.calls), count)

    async def test_updated_route_metrics_are_reported_even_when_places_stay_the_same(self):
        first = self.create('CITYWALK', 3)['data']
        previous = self.trips.store.get(first['trip_id'])
        revised = previous.latest_plan.model_copy(deep=True)
        revised.routes[0].distance_meters += 100
        revised.total_walking_meters += 100
        changes = describe_changes(previous, previous.state, revised)
        self.assertTrue(any(text.startswith('总步行距离（米）：') for text in changes))
        self.assertIn('步行路线信息已更新，请以当前各段距离与耗时为准。', changes)
        self.assertNotIn('已重新规划；地点和已记录的主要条件未变化。', changes)
