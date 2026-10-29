"""请求资源生命周期与 HTTP 错误边界。"""
import sqlite3
import tempfile
import unittest
from narrator_fixtures import OfflineNarrationMixin
from pathlib import Path
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda
from app.main import app
from app.controllers.dependencies import get_main_agent, get_trip_service, get_amap
from app.dependencies import ServiceResources
from app.agents.main_agent import MainAgent
from app.agents.food_agent import FoodAgent
from app.models.travel import GeoLocation, TravelRequest
from app.models.trip_store import TripStore
from app.services.preferences import PreferencePatch
from app.services.trip_service import TripService
from test_travel_stage2 import FakeCityWalkAgent


class ServiceBoundaryTests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    async def test_selected_children_receive_the_current_requests_resources(self):
        with patch('app.dependencies.get_llm', return_value=RunnableLambda(lambda value: '')) as factory:
            with tempfile.TemporaryDirectory() as temp:
                async with ServiceResources() as resources:
                    trips = TripService(TripStore(Path(temp)/'trips.sqlite3'), resources.amap)
                    main = MainAgent(trips, resources=resources)
                    self.assertEqual(main.agents, {})
                    food = main.get_subagent('FOOD')
                    self.assertNotIn('weather', resources.__dict__)
                    self.assertNotIn('unsplash', resources.__dict__)
                    attraction = main.get_subagent('ATTRACTION')
                    walk = main.get_subagent('CITYWALK')
                    for child in (food, attraction, walk):
                        self.assertIs(child.amap, resources.amap)
                        self.assertIs(child.jev, resources.jev)
                        self.assertIs(child.translator, resources.translator)
                    self.assertIs(walk.weather_service, attraction.weather_service)
                    factory.assert_called_once()
                    clients = [resources.amap.client, resources.jev.client, resources.weather.client,
                               resources.unsplash.client, factory.call_args.kwargs['http_async_client']]
            self.assertTrue(all(client.is_closed for client in clients))

    async def test_resources_are_lazy_and_local_to_each_request(self):
        with patch('app.dependencies.get_llm') as model_factory:
            async with ServiceResources() as first, ServiceResources() as second:
                self.assertNotIn('amap', first.__dict__)
                first_map, second_map = first.amap, second.amap
                self.assertIs(first.amap, first_map)
                self.assertIsNot(first_map, second_map)
                self.assertIsNot(first_map.client, second_map.client)
                self.assertFalse(first_map.client.is_closed)
                self.assertNotIn('jev', first.__dict__)
                model_factory.assert_not_called()
            self.assertTrue(first_map.client.is_closed)
            self.assertTrue(second_map.client.is_closed)

    async def test_resources_close_even_if_model_initialization_fails(self):
        with patch('app.dependencies.get_llm', side_effect=ValueError('缺少模型配置')) as factory:
            with self.assertRaises(ValueError):
                async with ServiceResources() as resources:
                    resources.llm
            self.assertTrue(factory.call_args.kwargs['http_async_client'].is_closed)

    async def test_http_requests_use_independent_agents_and_preserve_trip_history(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch('app.services.trip_service.TripStore', side_effect=lambda: TripStore(Path(temp)/'trips.sqlite3')), \
             patch('app.controllers.dependencies.MainAgent', wraps=MainAgent) as factory, \
             patch.object(MainAgent, 'get_subagent', return_value=FakeCityWalkAgent()), \
             TestClient(app) as client:
            payload = {'city': '上海', 'theme': '建筑与咖啡', 'target_stops': 2,
                       'start_location': {'latitude': 31.23, 'longitude': 121.47}}
            first = client.post('/api/v1/citywalk/plan', json=payload)
            second = client.post('/api/v1/citywalk/plan', json={**payload, 'target_stops': 5})
            self.assertEqual((first.status_code, second.status_code), (200, 200))
            first_id, second_id = first.json()['data']['trip_id'], second.json()['data']['trip_id']
            self.assertNotEqual(first_id, second_id)
            updated = client.post('/api/v1/citywalk/plan', json={
                'trip_id': first_id, 'state_version': 1, 'target_stops': 4,
            })
            self.assertEqual(updated.status_code, 200)
            store = TripStore(Path(temp)/'trips.sqlite3')
            self.assertEqual(store.get(first_id).state.target_stops, 4)
            self.assertEqual(store.get(second_id).state.target_stops, 5)
            self.assertEqual(store.get_version(first_id, 1).state.target_stops, 2)
            with patch.object(MainAgent, 'get_subagent', side_effect=ValueError('没有合适的地点')):
                failed = client.post('/api/v1/citywalk/plan', json=payload)
            self.assertEqual(failed.status_code, 422)
            resources = [call.kwargs['resources'] for call in factory.call_args_list]
            self.assertEqual(len({id(item) for item in resources}), 4)
            self.assertTrue(all(item.amap.client.is_closed for item in resources))

    async def test_food_agent_owns_count_field_translation(self):
        request = TravelRequest(message='安排五家餐厅', city='上海',
                                location=GeoLocation(latitude=31.23, longitude=121.47))
        for explicit_limit, expected in ((None, 5), (2, 2)):
            preferences = PreferencePatch(target_stops=5, limit=explicit_limit,
                                          routing_english='Find restaurants')
            child_request = FoodAgent.request_from_message(request, preferences, None)
            self.assertEqual(child_request.limit, expected)
            self.assertEqual(preferences.limit, explicit_limit)


class SharedErrorBoundaryTests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(app.dependency_overrides.clear)

    async def test_all_business_controllers_use_the_same_error_boundary(self):
        main, trips, amap = Mock(), Mock(), Mock()
        for method in (main.plan, main.plan_for, trips.list_trips, trips.trip_history,
                       trips.get_trip, amap.search):
            method.side_effect = ValueError('参数不适用')
        app.dependency_overrides.update({get_main_agent: lambda: main,
                                         get_trip_service: lambda: trips,
                                         get_amap: lambda: amap})
        location = {'latitude': 31.23, 'longitude': 121.47}
        requests = [
            ('POST', '/travel', {'message': '规划行程'}),
            ('POST', '/citywalk/plan', {'city': '上海', 'theme': '建筑与咖啡', 'start_location': location}),
            ('POST', '/attraction/plan', {'city': '上海', 'location': location}),
            ('POST', '/food/search', {'city': '上海', 'center_location': location}),
            ('POST', '/locations/search', {'city': '上海', 'query': '人民广场'}),
            ('GET', '/trips', None),
            ('GET', '/trips/trip_example/history', None),
            ('GET', '/trips/trip_example', None),
        ]
        with TestClient(app) as client:
            for method, path, payload in requests:
                with self.subTest(path=path):
                    response = client.request(method, '/api/v1'+path, json=payload)
                    self.assertEqual(response.status_code, 422)
                    self.assertEqual(response.json()['code'], 40002)
                    self.assertEqual(response.json()['message'], '参数不适用')

    async def test_dependency_failure_uses_standard_error_response(self):
        def unavailable():
            raise sqlite3.OperationalError('storage unavailable')

        app.dependency_overrides[get_trip_service] = unavailable
        with patch('app.controllers.errors.logger'), TestClient(app) as client:
            response = client.get('/api/v1/trips')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['code'], 50010)
        self.assertNotIn('storage unavailable', response.text)
