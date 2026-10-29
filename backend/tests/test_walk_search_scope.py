"""类别检索只接收自身要求，并发消息历史互相独立。"""
import asyncio
import json
import unittest
from types import SimpleNamespace
from langchain_core.runnables import RunnableLambda
from app.concurrency import gather_results
from app.integrations.amap_service import Place
from app.models.walk import WalkRequest
from app.services.candidate_search import ReActCandidateSearch
from app.models.walk_preferences import WalkSearchProfile
from app.agents.citywalk_agent import CityWalkAgent

ORIGIN=(108.947,34.223)

def place(keyword):
    return Place(keyword,keyword,'测试地址',keyword,*ORIGIN,'',keyword)


class SearchScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_walk_category_receives_its_own_description(self):
        calls={}
        class Search:
            async def search(self,amap,**kwargs):
                calls[kwargs['keyword']]=kwargs['context']
                return SimpleNamespace(places=[place(kwargs['keyword'])],failed=False)
        request=WalkRequest(city='西安',interests=['咖啡店'],end_with='陕西饭店',avoid_commercial=True,
            notes='咖啡店要手作的，不要瑞幸。陕西饭店要正宗的，不是商场里的网红店')
        profile=WalkSearchProfile(searches=[
            {'query':'咖啡店','english':'Independent handcrafted coffee shop, no chain brands'},
            {'query':'陕西饭店','english':'Authentic local Shaanxi restaurant outside shopping malls'},
        ],request_english='Cafe and local food',end_with='陕西饭店')
        from unittest.mock import AsyncMock
        agent=CityWalkAgent.__new__(CityWalkAgent)
        agent.amap=agent.jev=None
        agent.translator=SimpleNamespace(places=AsyncMock(side_effect=RuntimeError("search complete")))
        agent.candidate_search=Search()
        with self.assertRaisesRegex(RuntimeError, "search complete"):
            await agent.plan_walk(request,profile,ORIGIN,2,None,"test",1)
        self.assertEqual([p.poi_id for p in agent.translator.places.call_args.args[0]],
                         ["咖啡店", "陕西饭店"])
        self.assertNotIn('notes',calls['咖啡店'])
        self.assertEqual(calls['咖啡店']['category_description'],profile.keywords['咖啡店'])
        self.assertNotIn('Shaanxi',json.dumps(calls['咖啡店']))
        self.assertTrue(calls['咖啡店']['avoid_commercial'])
        self.assertEqual(calls['陕西饭店']['category_description'],profile.keywords['陕西饭店'])

    async def test_shared_discovery_keeps_category_quota_radius_and_provenance(self):
        near = place("shared")
        far = Place("far", "远处", "", "公园", 120, 40, "", "")
        parks = [Place(f"park{i}", "公园", "", "公园", ORIGIN[0]+i*.001, ORIGIN[1], "", "")
                 for i in range(1, 6)]
        class Search:
            async def search(self, amap, **kwargs):
                values = [far, *reversed(parks), near] if kwargs["keyword"] == "公园" else [near, place("cafe")]
                return SimpleNamespace(places=values, failed=False)
        agent = CityWalkAgent.__new__(CityWalkAgent)
        agent.amap = None
        agent.candidate_search = Search()
        result = await agent.discover(city="西安", searches={"公园": {}, "咖啡": {}},
                                      origin=ORIGIN, radius=4000, per_category_limit=3)
        self.assertEqual([p.poi_id for p in result.places], ["shared", "park1", "park2", "cafe"])
        self.assertEqual(result.matches["shared"], {"公园", "咖啡"})
        self.assertEqual(result.matches["cafe"], {"咖啡"})
        self.assertEqual(result.failures, [])

    async def test_shared_discovery_partial_and_total_search_failure(self):
        from app.integrations.amap_service import AmapServiceError
        class Search:
            async def search(self, amap, **kwargs):
                failed = kwargs["keyword"] == "故障"
                return SimpleNamespace(places=[] if failed else [place("ok")], failed=failed)
        agent = CityWalkAgent.__new__(CityWalkAgent)
        agent.amap = None
        agent.candidate_search = Search()
        result = await agent.discover(city="西安", searches={"故障": {}, "咖啡": {}},
                                      origin=None, radius=4000)
        self.assertEqual(result.failures, ["故障"])
        self.assertEqual([p.poi_id for p in result.places], ["ok"])
        with self.assertRaises(AmapServiceError):
            await agent.discover(city="西安", searches={"故障": {}}, origin=ORIGIN, radius=4000)

    async def test_concurrent_categories_do_not_share_message_lists_or_observations(self):
        entered=set()
        release=asyncio.Event()
        histories={}
        counts={}
        class Model:
            def with_structured_output(self,schema,**kwargs):
                async def run(prompt):
                    return schema.model_validate(await self.ainvoke(prompt.to_messages()))
                return RunnableLambda(run)
            async def ainvoke(self,messages):
                keyword=json.loads(messages[1].content)['category']
                histories.setdefault(keyword,[]).append(messages)
                counts[keyword]=counts.get(keyword,0)+1
                if counts[keyword]==1:
                    entered.add(keyword)
                    if len(entered)==2:
                        release.set()
                    await release.wait()
                    action={'name':'search_places','keyword':keyword}
                elif counts[keyword]==2:
                    action={'name':'review_candidates','accepted_ids':[keyword],'summary':'符合当前类别'}
                else:
                    action={'name':'finish','summary':'完成当前类别'}
                return {'thought':'处理当前类别。','action':action}
        class Map:
            async def search_around(self,city,lon,lat,keyword,radius,types):
                return [place(keyword)]
        service=ReActCandidateSearch(Model())
        results=await asyncio.wait_for(gather_results(*(
            service.search(Map(),city='西安',keyword=k,origin=ORIGIN,radius=4000)
            for k in ('咖啡店','陕西饭店'))),2)
        self.assertIsNot(histories['咖啡店'][0],histories['陕西饭店'][0])
        for keyword,history in histories.items():
            observations=[json.loads(m.content.removeprefix('Observation: '))
                for m in history[-1] if m.type=='human' and m.content.startswith('Observation: ')]
            candidates=[p['id'] for o in observations for p in o.get('candidates',[])]
            self.assertEqual(candidates,[keyword])
        self.assertEqual([r.places[0].poi_id for r in results],['咖啡店','陕西饭店'])
