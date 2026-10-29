"""用事件屏障验证真实并发、取消清理和 HTTP 并发版本控制，不调用外网。"""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from app.agents.food_agent import FoodAgent
from app.agents.main_agent import MainAgent
from app.concurrency import gather_results
from app.controllers.dependencies import get_main_agent, get_resources
from app.dependencies import ServiceResources
from app.integrations.amap_service import AmapService, WalkingRoute
from app.integrations.llm_service import get_llm
from app.main import app
from app.models.trip_store import TripStore
from app.services.preferences import PreferencePatch
from app.services.trip_service import TripService
from narrator_fixtures import OfflineNarrationMixin
import test_citywalk
from test_discovery import FakeAmap, FakeJev, FakeTranslator, state
from test_place_counts import NearbyPlaces
from test_travel_stage2 import FakeCityWalkAgent, FakeRouter


class AsyncExecutionTests(OfflineNarrationMixin, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(app.dependency_overrides.clear)

    async def test_model_adapters_use_the_owned_async_http_client(self):
        for environment in ({'DEEPSEEK_API_KEY':'offline'}, {'LLM_API_KEY':'offline'}):
            with self.subTest(provider=next(iter(environment))):
                async with httpx.AsyncClient() as client:
                    with patch.dict(os.environ, environment, clear=True):
                        model = get_llm(http_async_client=client)
                    try:
                        self.assertIs(model.http_async_client, client)
                        self.assertIs(model.root_async_client._client, client)
                    finally:
                        model.root_client.close()

    async def test_recommendation_translations_overlap(self):
        entered, release = set(), asyncio.Event()
        async def enter(name):
            entered.add(name)
            if len(entered) == 2:
                release.set()
            await release.wait()
        class Translator(FakeTranslator):
            async def request(self, text):
                await enter("request")
                return await super().request(text)
            async def places(self, places):
                await enter("places")
                return await super().places(places)
        agent = FoodAgent(FakeAmap(), FakeJev(), Translator())
        plan = await asyncio.wait_for(agent.execute(state("FOOD")), 2)
        self.assertEqual(entered, {"request", "places"})
        self.assertEqual(len(plan.places), 3)

    async def test_keyword_searches_overlap_and_keep_deduplication_order(self):
        entered, release = [], asyncio.Event()

        class Searches(FakeAmap):
            async def search_around(self, *args):
                entered.append(args[3])
                if len(entered) == 3:
                    release.set()
                await release.wait()
                return await super().search_around(*args)

        agent = FoodAgent(Searches(), FakeJev(), FakeTranslator())
        result = await asyncio.wait_for(
            agent.discover(city='上海', searches={k: {} for k in ['川菜', '粤菜', '本帮菜']},
                           origin=(121.46,31.22), radius=1500, types='050000'), 2,
        )
        self.assertEqual(entered, ['川菜', '粤菜', '本帮菜'])
        self.assertEqual(result.failures, [])
        self.assertEqual(len(result.places), len({p.poi_id for p in result.places}))

    async def test_amap_has_four_in_flight_requests_at_most(self):
        active = maximum = 0
        release = asyncio.Event()

        async def respond(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                release.set()
            try:
                await release.wait()
                await asyncio.sleep(0)
                return httpx.Response(200, json={'status':'1', 'pois':[]})
            finally:
                active -= 1

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            amap = AmapService('offline', client)
            await asyncio.wait_for(gather_results(*(amap.search('上海', str(i)) for i in range(12))), 2)
        self.assertEqual(maximum, 4)
        self.assertEqual(active, 0)

    async def test_citywalk_weather_and_cover_overlap_after_route_selection(self):
        agent, _ = test_citywalk.CityWalkTests().make_agent()
        entered, release = set(), asyncio.Event()

        async def enter(name):
            entered.add(name)
            if len(entered) == 2:
                release.set()
            await release.wait()

        async def weather(*args):
            await enter('weather')
            return None, '离线无天气'

        async def cover(*args):
            await enter('cover')
            return None

        async def route(*args):
            return WalkingRoute(100, 2, None)

        agent.weather_service.lookup = weather
        agent.unsplash.cover = cover
        agent.amap.walking_route = route
        from app.models.walk import WalkRequest
        result = await asyncio.wait_for(agent.plan_legacy(
            WalkRequest(city='上海', theme='建筑与咖啡'),
            origin_coordinates=(121.47, 31.23), target_stops=2, describe=False,
        ), 2)
        self.assertEqual(len(result.legs), 2)
        self.assertEqual(entered, {'weather','cover'})

    async def test_failure_cancels_siblings_before_closing_clients(self):
        started, cleaned = asyncio.Event(), asyncio.Event()
        error = ValueError('original error')
        async with ServiceResources() as resources:
            client = resources.amap.client

            async def pending():
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    self.assertFalse(client.is_closed)
                    cleaned.set()

            async def fail():
                await started.wait()
                raise error

            with self.assertRaises(ValueError) as caught:
                await gather_results(pending(), fail())
            self.assertIs(caught.exception, error)
            self.assertTrue(cleaned.is_set())
        self.assertTrue(client.is_closed)

    async def test_cancelling_group_waits_for_all_children(self):
        started, cleaned = asyncio.Event(), asyncio.Event()

        async def pending():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()

        job = asyncio.create_task(gather_results(pending()))
        await asyncio.wait_for(started.wait(), 2)
        job.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await job
        self.assertTrue(cleaned.is_set())

    async def test_health_remains_available_while_model_is_waiting(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def extract(*args):
            started.set()
            await release.wait()
            return PreferencePatch(routing_english='Unclear request')

        with tempfile.TemporaryDirectory() as temp:
            trips = TripService(TripStore(Path(temp)/'trips.sqlite3'), FakeAmap())
            main = MainAgent(trips, extractor=type('Extractor', (), {'extract':staticmethod(extract)})(),
                             router=FakeRouter(['CLARIFY']))
            app.dependency_overrides[get_main_agent] = lambda: main
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                slow = asyncio.create_task(client.post('/api/v1/travel', json={'message':'帮我规划'}))
                try:
                    await asyncio.wait_for(started.wait(), 2)
                    health = await asyncio.wait_for(client.get('/health'), 2)
                    self.assertEqual(health.status_code, 200)
                    self.assertFalse(slow.done())
                finally:
                    release.set()
                    result = await slow
                self.assertEqual(result.json()['data']['routed_skill'], 'CLARIFY')

    async def test_concurrent_edits_save_only_one_new_version(self):
        started, release = [], asyncio.Event()
        should_wait = False
        original = FakeCityWalkAgent()

        class Child:
            async def execute(self, trip_state):
                if should_wait:
                    started.append(trip_state.state_version)
                    if len(started) == 2:
                        release.set()
                    await release.wait()
                return await original.execute(trip_state)

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'trips.sqlite3'
            with patch('app.services.trip_service.TripStore', side_effect=lambda: TripStore(path)), \
                 patch.object(MainAgent, 'get_subagent', return_value=Child()):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    first = await client.post('/api/v1/citywalk/plan', json={
                        'city':'上海','theme':'建筑与咖啡','target_stops':2,
                        'start_location':{'latitude':31.23,'longitude':121.47},
                    })
                    self.assertEqual(first.status_code, 200, first.text)
                    trip_id = first.json()['data']['trip_id']
                    should_wait = True
                    payload = {'trip_id':trip_id,'state_version':1,'target_stops':3}
                    responses = await asyncio.wait_for(gather_results(
                        client.post('/api/v1/citywalk/plan', json=payload),
                        client.post('/api/v1/citywalk/plan', json=payload),
                    ), 5)
            self.assertEqual(sorted(r.status_code for r in responses), [200,409])
            store = TripStore(path)
            self.assertEqual(store.get(trip_id).state.state_version, 2)
            self.assertEqual(store.history(trip_id=trip_id).total, 2)

    async def test_cancelled_planning_closes_resources_without_saving(self):
        entered, clients = asyncio.Event(), []

        async def resource_dependency():
            async with ServiceResources() as resources:
                clients.append(resources.amap.client)
                yield resources

        class Child:
            async def execute(self, trip_state):
                entered.set()
                await asyncio.Event().wait()

        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'trips.sqlite3'
            app.dependency_overrides[get_resources] = resource_dependency
            with patch('app.services.trip_service.TripStore', side_effect=lambda: TripStore(path)), \
                 patch.object(MainAgent, 'get_subagent', return_value=Child()):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                    job = asyncio.create_task(client.post('/api/v1/citywalk/plan', json={
                        'city':'上海','theme':'建筑与咖啡',
                        'start_location':{'latitude':31.23,'longitude':121.47},
                    }))
                    await asyncio.wait_for(entered.wait(), 2)
                    job.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await job
            self.assertTrue(all(client.is_closed for client in clients))
            self.assertEqual(TripStore(path).history().total, 0)
