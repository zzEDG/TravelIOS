"""ReAct 工具循环、有限重搜、ID 约束与实际规划接入的离线验证。"""
import asyncio
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError
from langchain_core.runnables import RunnableLambda
from fastapi.testclient import TestClient

from app.agents.attraction_agent import AttractionAgent
from app.agents.food_agent import FoodAgent
from app.controllers.dependencies import get_main_agent
from app.integrations.amap_service import Place, WalkingRoute, AmapServiceError
from app.main import app
from app.models.walk import WalkRequest
from app.services.candidate_search import ReActCandidateSearch, CandidateSearchError, CandidateSearchTimeout, CandidateDecision, FinishAction, CandidateModelProtocolError
from app.models.walk_preferences import WalkSearchProfile
from app.agents.citywalk_agent import CityWalkAgent
from test_discovery import state


def poi(poi_id, name='陕西面馆', category='餐饮服务;中餐厅', typecode='050100', longitude=121.461):
    return Place(poi_id,name,'测试路',category,longitude,31.221,'310000','陕西菜馆',typecode)


COMPANY = poi('company','陕西酒店管理有限公司','公司企业;公司','170000')
FIRST, SECOND = poi('first'), poi('second','秦味陕西菜馆')


def action(name, **arguments):
    return {'thought':'根据当前工具结果决定下一步。','action':{'name':name,**arguments}}


def finish(summary='完成当前类别'):
    return action('finish',summary=summary)


def search(keyword='陕西菜馆', **extra):
    return action('search_places',keyword=keyword,**extra)


def review(*ids):
    return action('review_candidates', accepted_ids=list(ids), summary='根据高德名称和类别，只保留餐厅。')


def structured_runnable(model, schema):
    def invoke(prompt):
        return schema.model_validate(model.invoke(prompt.to_messages()))
    async def ainvoke(prompt):
        return schema.model_validate(await model.ainvoke(prompt.to_messages()))
    return RunnableLambda(invoke, afunc=ainvoke)


class ToolModel:
    def __init__(self, *outputs):
        self.outputs = iter(outputs)
        self.inputs = []
        self.choices = []

    def with_structured_output(self, schema, **kwargs):
        self.schema=schema
        return structured_runnable(self,schema)

    def bind_tools(self, tools, *, tool_choice=None):
        self.choices.append(tool_choice)
        self.tools = tools
        return self

    async def ainvoke(self, messages):
        self.inputs.append(list(messages))
        return next(self.outputs)


class SearchMap:
    def __init__(self, *outputs):
        self.outputs = iter(outputs)
        self.calls = []
        self.routed = []

    async def search_around(self, city, lon, lat, keyword, radius, types):
        self.calls.append((city,lon,lat,keyword,radius,types))
        result = next(self.outputs)
        if isinstance(result, Exception):
            raise result
        return result

    async def search(self, city, keyword):
        return await self.search_around(city,0,0,keyword,0,'')

    async def walking_route(self, origin, destination):
        self.routed.append(destination.poi_id)
        return WalkingRoute(100,2,None)


class FirstChoice:
    def __init__(self):
        self.calls = []

    async def decide(self, state, questions):
        self.calls.append((state,questions))
        if all(q['type'] == 'score' for q in questions.values()):
            return {key: {'type': 'score', 'score': 2} for key in questions}
        name = next(iter(questions))
        return {name:{'type':'choice','choice':next(iter(questions[name]['criteria']))}}


class Translation:
    async def request(self, value):
        return 'Find suitable verified places.'

    async def places(self, places):
        return {p.poi_id:{'name':p.poi_id,'category':'Restaurant','address':'Example Road'} for p in places}


class CandidateSearchTests(unittest.IsolatedAsyncioTestCase):
    async def run_search(self, model, amap, **extra):
        return await ReActCandidateSearch(model).search(amap,city='上海',keyword='陕西菜馆',
            origin=(121.46,31.22),radius=1500,**extra)

    async def test_structured_actions_search_review_and_finish(self):
        model = ToolModel(
            action('search_places', **{'keyword': '陕西菜馆'}),
            action('review_candidates', **{'accepted_ids': ['first'], 'summary': '餐厅符合'}),
            finish('找到餐厅'),
        )
        result = await self.run_search(model, SearchMap([FIRST]))
        self.assertEqual(result.places, [FIRST])
        self.assertEqual(len(model.inputs), 3)
        self.assertEqual(model.choices, [])
        self.assertTrue(model.inputs[-1][-1].content.startswith('Observation:'))

    async def test_each_action_executes_and_returns_observation_before_next_model_call(self):
        amap = SearchMap([FIRST])
        class CheckingModel:
            def with_structured_output(self,schema,**kwargs): return structured_runnable(self,schema)
            def __init__(self): self.round = 0
            async def ainvoke(self,messages):
                self.round += 1
                if self.round == 1:
                    return search()
                observation = json.loads(messages[-1].content.removeprefix('Observation: '))
                if self.round == 2:
                    self_check.assertEqual(len(amap.calls),1)
                    self_check.assertEqual(observation['candidates'][0]['id'],'first')
                    self_check.assertEqual(messages[-1].type,'human')
                    return review('first')
                self_check.assertEqual(observation['accepted_ids'],['first'])
                return finish('完成')
        self_check = self
        result = await self.run_search(CheckingModel(),amap)
        self.assertEqual(result.places,[FIRST])

    async def test_wrong_review_fields_are_rejected_before_tool_execution(self):
        model=ToolModel(action('review_candidates',city='西安',weather='晴'))
        amap=SearchMap()
        with self.assertRaises(CandidateModelProtocolError):
            await self.run_search(model,amap)
        self.assertEqual(amap.calls,[])
        self.assertEqual(len(model.inputs),1)

    async def test_sync_model_runs_off_event_loop_and_finishes(self):
        main_thread = threading.get_ident()
        threads = []
        responses = iter([search(),review('first'),finish('完成')])
        class SyncModel:
            def with_structured_output(self,schema,**kwargs): return structured_runnable(self,schema)
            def invoke(self,messages):
                threads.append(threading.get_ident())
                return next(responses)
            async def ainvoke(self,messages):
                raise AssertionError('不得调用异步 SDK')
        result = await ReActCandidateSearch(SyncModel(),sync_invoke=True).search(
            SearchMap([FIRST]),city='上海',keyword='陕西菜馆',origin=(121.46,31.22),radius=1500)
        self.assertEqual(result.places,[FIRST])
        self.assertEqual(len(threads),3)
        self.assertTrue(all(t != main_thread for t in threads))

    async def test_health_responds_while_sync_model_is_blocked(self):
        import httpx
        started, release = threading.Event(), threading.Event()
        responses = iter([search(),review('first'),finish('完成')])
        class SyncModel:
            def with_structured_output(self,schema,**kwargs): return structured_runnable(self,schema)
            def invoke(self,messages):
                started.set()
                if not release.wait(2):
                    raise AssertionError('未释放同步调用')
                return next(responses)
        service = ReActCandidateSearch(SyncModel(),sync_invoke=True)
        job = asyncio.create_task(service.search(SearchMap([FIRST]),city='上海',keyword='陕西菜馆',
                                                origin=(121.46,31.22),radius=1500))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait,1))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                response = await asyncio.wait_for(client.get('/health'),.5)
            self.assertEqual(response.status_code,200)
            self.assertFalse(job.done())
        finally:
            release.set()
            await job

    async def test_cancellation_waits_for_sync_call_before_resource_cleanup(self):
        from app.dependencies import ServiceResources
        started, release, finished = threading.Event(),threading.Event(),threading.Event()
        resource_closed = []
        class SyncModel:
            def with_structured_output(self,schema,**kwargs): return structured_runnable(self,schema)
            def invoke(self,messages):
                started.set()
                try:
                    if not release.wait(2):
                        raise AssertionError('未释放同步调用')
                    if resource_closed:
                        raise AssertionError('工作线程结束前关闭了请求资源')
                    return search()
                finally:
                    finished.set()
        service = ReActCandidateSearch(SyncModel(),sync_invoke=True)
        async def request():
            async with ServiceResources() as resources:
                resources.callback(lambda:resource_closed.append(True))
                await service.search(SearchMap([FIRST]),city='上海',keyword='陕西菜馆',
                                     origin=(121.46,31.22),radius=1500)
        job = asyncio.create_task(request())
        try:
            self.assertTrue(await asyncio.to_thread(started.wait,1))
            job.cancel()
            await asyncio.sleep(0)
            self.assertFalse(job.done())
            self.assertEqual(resource_closed,[])
        finally:
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await job
        self.assertTrue(finished.is_set())
        self.assertEqual(resource_closed,[True])
        self.assertEqual(service._groups._value,2)

    async def test_queue_time_does_not_consume_execution_budget(self):
        model=ToolModel(search(),review('first'),finish('完成'))
        service=ReActCandidateSearch(model,queue_timeout=.5,execution_timeout=.02)
        await service._groups.acquire()
        await service._groups.acquire()
        job=asyncio.create_task(service.search(SearchMap([FIRST]),city='上海',keyword='陕西菜馆',
                                               origin=(121.46,31.22),radius=1500))
        try:
            await asyncio.sleep(.04)
            self.assertFalse(job.done())
        finally:
            service._groups.release()
            service._groups.release()
        result=await job
        self.assertEqual(result.places,[FIRST])

    async def test_queue_timeout_has_clear_http_504_and_no_slot_leak(self):
        from app.controllers.errors import _handle_error
        service=ReActCandidateSearch(ToolModel(),queue_timeout=.01)
        await service._groups.acquire()
        await service._groups.acquire()
        try:
            with self.assertRaises(CandidateSearchTimeout) as caught:
                await service.search(SearchMap(),city='上海',keyword='公园',origin=None,radius=1500)
            self.assertIn('排队超时',str(caught.exception))
            error=_handle_error(caught.exception)
            self.assertEqual((error.status_code,error.code),(504,50007))
            self.assertEqual(service._groups._value,0)
        finally:
            service._groups.release()
            service._groups.release()

    async def test_early_finish_does_not_skip_search_or_pending_review(self):
        model = ToolModel(
            finish('完成'),
            search(),
            finish('完成'),
            review('first'),
            finish('完成'),
        )
        result = await self.run_search(model, SearchMap([FIRST]))
        self.assertEqual(result.places, [FIRST])
        self.assertEqual(len(model.inputs), 5)

    async def test_model_can_finish_with_partial_reviewed_results(self):
        amap = SearchMap([FIRST, COMPANY])
        model = ToolModel(search(), review('first'), finish('返回合格餐厅'))
        result = await self.run_search(model, amap)
        self.assertEqual(result.places, [FIRST])
        self.assertEqual(len(amap.calls), 1)

    async def test_review_alone_does_not_implicitly_finish(self):
        model = ToolModel(search(), review('first'), *[
            action('review_candidates', **{'accepted_ids': [], 'summary': '重复审核'}) for _ in range(8)])
        with self.assertRaisesRegex(CandidateSearchError, 'Finish'):
            await self.run_search(model, SearchMap([FIRST]))

    def test_multiline_finish_is_a_typed_summary(self):
        decision=CandidateDecision.model_validate(finish('找到地点：\n- 咖啡馆甲\n- 咖啡馆乙'))
        self.assertIsInstance(decision.action,FinishAction)
        self.assertIn('\n',decision.action.summary)

    def test_unknown_actions_extra_fields_and_wrong_argument_types_are_rejected(self):
        invalid=[
            {'thought':'搜索','action':'search_places(keyword="公园")'},
            action('unknown',keyword='公园'),
            action('search_places',keyword='公园',city='其他城市'),
            action('search_places',keyword='公园',types='invalid'),
            action('review_candidates',accepted_ids='first',summary='审核'),
            {'thought':'搜索','action':{'name':'search_places','keyword':'公园'},'unrelated':'额外文本'},
        ]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValidationError):
                CandidateDecision.model_validate(value)

    async def test_all_valid_candidates_finish_without_an_unnecessary_retry(self):
        amap = SearchMap([FIRST])
        result = await self.run_search(ToolModel(search(),review('first'), finish('完成')),amap)
        self.assertEqual(result.places,[FIRST])
        self.assertFalse(result.retried)
        self.assertEqual(len(amap.calls),1)

    async def test_premature_finish_gets_observation_and_can_recover(self):
        model = ToolModel(finish('done'),search(),review('first'),
                          finish('done'))
        result = await self.run_search(model, SearchMap([FIRST]))
        self.assertEqual(result.places, [FIRST])
        self.assertIn('Finish 前必须',model.inputs[1][-1].content)


    async def test_model_selects_tools_without_forced_choice(self):
        model = ToolModel(search(),review(),search('陕西面馆'),review('first'), finish('完成'))
        result = await self.run_search(model,SearchMap([COMPANY],[FIRST]))
        self.assertEqual(result.places,[FIRST])
        self.assertEqual(model.choices, [])

    async def test_unstructured_text_is_not_interpreted_as_an_action(self):
        model=ToolModel('Thought: 搜索\nAction: search_places(keyword="公园")')
        with self.assertRaises(CandidateModelProtocolError):
            await self.run_search(model,SearchMap())
        self.assertEqual(len(model.inputs),1)

    async def test_company_is_rejected_then_query_is_refined_and_searched_again(self):
        amap = SearchMap([COMPANY],[FIRST,SECOND])
        model = ToolModel(search(),review(),search('陕西菜 面馆',types='050000'),review('first','second'), finish('完成'))
        result = await self.run_search(model,amap)
        self.assertEqual(result.places,[FIRST,SECOND])
        self.assertTrue(result.retried)
        self.assertEqual([call[3] for call in amap.calls],['陕西菜馆','陕西菜 面馆'])
        observations = [json.loads(m.content.removeprefix('Observation: ')) for messages in model.inputs for m in messages if m.type == 'human' and m.content.startswith('Observation: ')]
        self.assertTrue(any(obs.get('needs_retry') for obs in observations))
        self.assertTrue(any('陕西酒店管理有限公司' in str(obs) for obs in observations))

    async def test_partial_good_results_survive_a_retry_with_no_more_matches(self):
        amap = SearchMap([FIRST,COMPANY],[])
        result = await self.run_search(ToolModel(search(),review('first'),search('陕西面食餐厅'),review(), finish('完成')),amap)
        self.assertEqual(result.places,[FIRST])
        self.assertEqual(len(amap.calls),2)

    async def test_second_bad_batch_does_not_fall_back_to_the_company(self):
        amap = SearchMap([COMPANY],[COMPANY])
        result = await self.run_search(ToolModel(search(),review(),search('陕西菜 餐厅'),review(), finish('完成')),amap)
        self.assertEqual(result.places,[])
        self.assertEqual(len(amap.calls),2)

    async def test_invented_id_is_rejected_and_valid_review_can_recover(self):
        model = ToolModel(search(),review('invented'),review('first'), finish('完成'))
        result = await self.run_search(model,SearchMap([FIRST]))
        self.assertEqual(result.places,[FIRST])
        self.assertIn('只能选择当前搜索返回',model.inputs[-2][-1].content)

    async def test_review_cannot_finish_before_any_search(self):
        result = await self.run_search(ToolModel(review(),search(),review('first'), finish('完成')),SearchMap([FIRST]))
        self.assertEqual(result.places,[FIRST])

    async def test_model_can_choose_another_search_before_review(self):
        amap=SearchMap([COMPANY],[FIRST])
        model=ToolModel(search('陕西菜'),search('陕西面馆'),review('first'),finish())
        result=await self.run_search(model,amap)
        self.assertEqual(result.places,[FIRST])
        self.assertEqual([c[3] for c in amap.calls],['陕西菜','陕西面馆'])

    async def test_empty_or_failed_search_gets_one_retry(self):
        for first in ([], AmapServiceError('unavailable')):
            with self.subTest(first=type(first).__name__):
                result = await self.run_search(ToolModel(search(),review(),search('陕西餐厅'),review('first'), finish('完成')),
                                               SearchMap(first,[FIRST]))
                self.assertEqual(result.places,[FIRST])
                self.assertFalse(result.failed)

    async def test_structured_action_cannot_override_origin_city_or_radius(self):
        model=ToolModel(search(radius=50000,city='其他城市'))
        amap=SearchMap()
        with self.assertRaises(CandidateModelProtocolError):
            await self.run_search(model,amap)
        self.assertEqual(amap.calls,[])

    async def test_both_searches_failing_is_distinct_from_no_matching_places(self):
        result = await self.run_search(ToolModel(search(),review(),search('陕西餐厅'),review(), finish('完成')),
            SearchMap(AmapServiceError('offline'),AmapServiceError('offline')))
        self.assertTrue(result.failed)
        self.assertEqual(result.places,[])

    async def test_stalled_model_has_an_overall_timeout(self):
        class SlowModel:
            def with_structured_output(self,schema,**kwargs): return structured_runnable(self,schema)
            def bind_tools(self,tools, **kwargs): return self
            async def ainvoke(self,messages): await asyncio.Event().wait()
        timeout = asyncio.timeout
        with patch('app.services.candidate_search.asyncio.timeout',side_effect=lambda seconds:timeout(.01)):
            with self.assertRaisesRegex(CandidateSearchError,'超时'):
                await self.run_search(SlowModel(),SearchMap())

    async def test_invalid_structured_arguments_fail_without_tool_or_extra_call(self):
        bad=action('review_candidates',accepted_ids='first',summary='审核')
        model=ToolModel(bad)
        amap=SearchMap()
        with self.assertRaises(CandidateModelProtocolError):
            await self.run_search(model,amap)
        self.assertEqual(amap.calls,[])
        self.assertEqual(len(model.inputs),1)

    async def test_missing_structured_result_cannot_bypass_review(self):
        model=ToolModel(None)
        with self.assertRaises(CandidateModelProtocolError):
            await self.run_search(model,SearchMap())
        self.assertEqual(len(model.inputs),1)

    async def test_cancellation_cleans_up_running_groups_and_waiters(self):
        active = maximum = 0
        started = asyncio.Event()

        class SlowMap:
            async def search_around(self,*args):
                nonlocal active,maximum
                active += 1
                maximum = max(maximum,active)
                if active == 2:
                    started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    active -= 1

        service = ReActCandidateSearch(ToolModel(search(),search(), finish('完成')))
        tasks = [asyncio.create_task(service.search(SlowMap(),city='上海',keyword='陕西菜馆',
                    origin=(121.46,31.22),radius=1500)) for _ in range(3)]
        await asyncio.wait_for(started.wait(),2)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        self.assertEqual(maximum,2)
        self.assertEqual(active,0)

    async def test_three_planners_only_route_and_choose_reviewed_places(self):
        for skill in ('CITYWALK','FOOD','ATTRACTION'):
            with self.subTest(skill=skill):
                amap = SearchMap([COMPANY],[FIRST,SECOND])
                keyword = '博物馆' if skill == 'ATTRACTION' else '陕西菜馆'
                retry = '博物馆展馆' if skill == 'ATTRACTION' else '陕西菜餐厅'
                model = ToolModel(search(keyword),review(),search(retry),review('first','second'), finish('完成'))
                service = ReActCandidateSearch(model)
                jev = FirstChoice()
                if skill == 'CITYWALK':
                    profile = WalkSearchProfile(searches=[{'query':'陕西菜馆','english':'Shaanxi restaurant'}],
                                                request_english='Two Shaanxi restaurants')
                    agent = CityWalkAgent.__new__(CityWalkAgent)
                    agent.amap, agent.candidate_search = amap, service
                    agent.jev, agent.translator = jev, Translation()
                    agent.weather_service = SimpleNamespace(lookup=AsyncMock(return_value=(None, "offline")))
                    agent.unsplash = SimpleNamespace(cover=AsyncMock(return_value=None))
                    plan = await agent.plan_walk(
                        WalkRequest(city='上海',interests=['陕西菜馆']),profile,(121.46,31.22),2,None,'test',1)
                else:
                    agent = (FoodAgent(amap,jev,Translation()) if skill == 'FOOD' else
                             AttractionAgent(amap,jev,Translation(),weather_service=SimpleNamespace()))
                    agent.candidate_search = service
                    # 景点协议只接受景点类别；此分支验证共享检索在类型硬校验之前生效。
                    if skill == 'ATTRACTION':
                        museum1 = poi('first','陕西历史博物馆','科教文化;博物馆','140100')
                        museum2 = poi('second','陕西文化博物馆','科教文化;博物馆','140100')
                        amap.outputs = iter([[COMPANY],[museum1,museum2]])
                    trip = state(skill).model_copy(update={'target_stops':2,'food_limit':2,
                        'interests':[keyword],'cuisine_preference':['陕西菜馆'],
                        'consider_weather':False,'max_distance_meters':1500})
                    plan = await agent.execute(trip)
                self.assertEqual([p.poi.id for p in plan.places],['first','second'])
                self.assertNotIn('company',amap.routed)
                self.assertTrue(jev.calls)
                self.assertTrue(all('company' not in str(questions) for _,questions in jev.calls))

    async def test_candidate_search_error_uses_http_502_not_parameter_422(self):
        app.dependency_overrides[get_main_agent] = lambda:SimpleNamespace(
            plan_for=AsyncMock(side_effect=CandidateSearchError('候选地点检索校验失败')))
        self.addCleanup(app.dependency_overrides.clear)
        with TestClient(app) as client:
            response = client.post('/api/v1/citywalk/plan',json={'city':'上海',
                'start_location':{'longitude':121.46,'latitude':31.22},'interests':['陕西菜馆']})
        self.assertEqual(response.status_code,502)
        self.assertEqual(response.json()['code'],50006)
