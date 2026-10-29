"""自由偏好、顺序、当前位置决策与多轮历史的离线行为验证。"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient

from app.agents.citywalk_agent import CityWalkAgent
from app.agents.main_agent import MainAgent
from app.controllers.dependencies import get_main_agent, get_trip_service
from app.integrations.amap_service import Place, WalkingRoute
from app.main import app
from app.models.travel import CityWalkPlanRequest, GeoLocation, TravelRequest
from app.models.walk import WalkRequest
from app.models.trip_store import TripStore
from app.services.preferences import PreferencePatch, PreferenceExtractionError
from app.services.trip_service import TripService
from app.models.walk_preferences import SearchPreference, WalkSearchProfile
from walk_fixtures import preference_agent
from narrator_fixtures import OfflineNarrationMixin
from test_discovery import state
from test_travel_stage2 import FakeExtractor, FakeRouter
from walk_fixtures import fake_walk_preferences


ORIGIN = GeoLocation(longitude=121.47,latitude=31.23)


class Nearby:
    def __init__(self, no_cafe=False):
        self.searches, self.routes = [], []
        self.no_cafe = no_cafe

    async def search_around(self, city, lon, lat, keyword, radius, types):
        self.searches.append(keyword)
        if keyword == '咖啡馆':
            return [] if self.no_cafe else [Place('cafe','转角咖啡','测试路','咖啡馆',121.4701,31.23,'','咖啡馆','050500')]
        if keyword == '书店':
            return [Place('book','街角书店','测试路','书店',121.474,31.23,'','书店')]
        return [Place(f'park{i}',f'公园{i}','测试路','公园',121.475+i*.001,31.23,'',keyword,'110100') for i in range(1,5)]

    async def walking_route(self, origin, destination):
        self.routes.append((origin.poi_id,destination.poi_id))
        return WalkingRoute(100,2,'121.47,31.23;121.48,31.23')


class Translate:
    async def places(self, places):
        return {p.poi_id:{'name':p.poi_id,'category':'cafe' if p.poi_id=='cafe' else 'bookstore' if p.poi_id=='book' else 'park',
                          'address':'Example Road'} for p in places}


class Choose:
    def __init__(self, preferred=()):
        self.calls=[]
        self.preferred=list(preferred)

    async def decide(self, state, questions):
        self.calls.append((state,questions))
        criteria=questions['next_poi']['criteria']
        choice=next((value for value in self.preferred if value in criteria),next(iter(criteria)))
        if choice in self.preferred:
            self.preferred.remove(choice)
        return {'next_poi':{'type':'choice','choice':choice}}


def walk(amap=None, jev=None):
    agent=CityWalkAgent.__new__(CityWalkAgent)
    agent.amap,agent.jev,agent.translator=amap or Nearby(),jev or Choose(),Translate()
    agent.parse_preferences=fake_walk_preferences
    agent.weather_service=SimpleNamespace(lookup=AsyncMock(return_value=(None,'离线无天气')))
    agent.unsplash=SimpleNamespace(cover=AsyncMock(return_value=None))
    return agent


class PreferenceTests(OfflineNarrationMixin,unittest.IsolatedAsyncioTestCase):
    def old(self, **changes):
        return state('CITYWALK').model_copy(update={'theme':'公园与自然','interests':['公园'],
            'visit_order':['公园'],'end_with':None,**changes})

    def request(self,patch,previous=None):
        previous=previous or self.old()
        return CityWalkAgent.request_from_message(
            TravelRequest(message='调整漫步',trip_id=previous.trip_id,state_version=1),patch,previous)

    async def test_add_cafe_keeps_park_priority_count_and_label(self):
        result=self.request(PreferencePatch(interests=['咖啡馆'],theme='建筑与咖啡',routing_english='Add a cafe stop'))
        self.assertEqual(result.interests,['公园','咖啡馆'])
        self.assertEqual(result.theme,'公园与自然')
        self.assertIsNone(result.target_stops)

    async def test_remove_and_reprioritize_are_different_operations(self):
        previous=self.old(interests=['公园','咖啡馆'],visit_order=['公园','咖啡馆'],end_with='咖啡馆')
        result=self.request(PreferencePatch(removed_interests=['咖啡馆'],routing_english='Remove cafe'),previous)
        self.assertEqual(result.interests,['公园'])
        self.assertEqual(result.visit_order,['公园'])
        self.assertIn('end_with',result.model_fields_set)
        self.assertIsNone(result.end_with)
        priority=self.request(PreferencePatch(priority_interests=['书店'],routing_english='Mainly bookstores'),previous)
        self.assertEqual(priority.interests,['书店','公园','咖啡馆'])
        replaced=self.request(PreferencePatch(interests=['书店'],interests_mode='REPLACE',routing_english='Only bookstores'),previous)
        self.assertEqual(replaced.interests,['书店'])
        self.assertEqual(replaced.visit_order,[])
        self.assertIsNone(replaced.end_with)

    async def test_order_can_be_updated_and_cleared_independently(self):
        previous=self.old(visit_order=['公园','书店'],end_with='咖啡馆')
        changed=self.request(PreferencePatch(visit_order=['书店','公园'],routing_english='Bookstore first'),previous)
        self.assertEqual(changed.visit_order,['书店','公园'])
        unchanged=self.request(PreferencePatch(visit_order=[],routing_english='Keep old order'),previous)
        self.assertNotIn('visit_order',unchanged.model_fields_set)
        cleared=self.request(PreferencePatch(visit_order=[],clear_visit_order=True,clear_end_with=True,
                                             routing_english='Remove order'),previous)
        self.assertEqual(cleared.visit_order,[])
        self.assertIn('end_with',cleared.model_fields_set)

    async def test_new_walk_accepts_free_preferences_without_theme(self):
        result=CityWalkAgent.request_from_message(TravelRequest(message='公园和咖啡',city='上海',location=ORIGIN),
            PreferencePatch(interests=['公园','咖啡馆'],routing_english='Parks and cafes'),None)
        self.assertEqual(result.theme,'自由漫步')
        self.assertEqual(result.interests,['公园','咖啡馆'])

    async def test_legacy_label_is_retained_as_migration_context(self):
        previous=self.old(interests=[],visit_order=[])
        result=self.request(PreferencePatch(interests=['咖啡馆'],routing_english='Add cafe'),previous)
        self.assertIn('公园与自然',result.additional_requirements)

    async def test_route_keeps_explicit_order_and_updates_current_position(self):
        jev=Choose(['park3','book','cafe'])
        agent=walk(jev=jev)
        trip=self.old(city='上海',current_location=ORIGIN,target_stops=3,
                      interests=['公园','咖啡馆','书店'],visit_order=['公园','书店'],end_with='咖啡馆')
        result=await agent.execute(trip)
        self.assertEqual([p.poi.id for p in result.places],['park3','book','cafe'])
        # 最近的咖啡店仍在最后，选完后没有按起点距离重新排序。
        self.assertEqual([r.from_poi_id for r in result.routes],[None,'park3','book'])
        self.assertAlmostEqual(jev.calls[1][0]['current_location']['longitude'],121.478)
        self.assertEqual(jev.calls[0][0]['next_order_preference'],'park')
        self.assertTrue(jev.calls[-1][0]['is_final_stop'])
        self.assertNotIn('cafe',jev.calls[0][1]['next_poi']['criteria'])

    async def test_priority_list_does_not_force_visit_order(self):
        jev=Choose(['cafe','park1'])
        result=await walk(jev=jev).execute(self.old(current_location=ORIGIN,target_stops=2,
            interests=['公园','咖啡馆'],visit_order=[]))
        self.assertEqual([p.poi.id for p in result.places],['cafe','park1'])
        self.assertEqual(jev.calls[0][0]['priority_preferences'],['park','cafe'])
        self.assertIsNone(jev.calls[0][0]['next_order_preference'])

    async def test_unavailable_final_preference_is_reported(self):
        result=await walk(amap=Nearby(no_cafe=True)).execute(self.old(current_location=ORIGIN,target_stops=3,
            interests=['公园','咖啡馆'],visit_order=[],end_with='咖啡馆'))
        self.assertEqual(len(result.places),2)
        self.assertTrue(any('尚未满足最后一站' in note for note in result.notes))
        self.assertTrue(any('咖啡馆' in note for note in result.notes))

    async def test_explicit_order_longer_than_target_is_rejected(self):
        trip=self.old(current_location=ORIGIN,target_stops=2,interests=['公园','书店','咖啡馆'],
                      visit_order=['公园','书店'],end_with='咖啡馆')
        with self.assertRaisesRegex(ValueError,'多于目标数量'):
            await walk().execute(trip)

    async def test_real_walking_budget_filters_before_jev(self):
        class Routes(Nearby):
            async def walking_route(self,origin,destination):
                return WalkingRoute(5000 if destination.poi_id=='park3' else 100,2,None)
        jev=Choose(['park3','park1','cafe'])
        result=await walk(amap=Routes(),jev=jev).execute(self.old(current_location=ORIGIN,target_stops=2,
            interests=['公园','咖啡馆'],visit_order=['公园'],end_with='咖啡馆',max_walking_meters=500))
        self.assertNotIn('park3',jev.calls[0][1]['next_poi']['criteria'])
        self.assertLessEqual(result.total_walking_meters,500)
        self.assertEqual(jev.calls[0][0]['remaining_walking_meters'],500)
        self.assertEqual(jev.calls[1][0]['remaining_walking_meters'],400)

    async def test_profile_parses_form_notes_and_validates_explicit_preferences(self):
        profile=WalkSearchProfile(searches=[SearchPreference(query='公园',english='park'),
                                          SearchPreference(query='咖啡馆',english='cafe')],
            request_english='Mainly parks; finish at a cafe',visit_order=['公园'],end_with='咖啡馆')
        invoke=AsyncMock(return_value=profile)
        model=SimpleNamespace(with_structured_output=lambda schema, **kwargs:SimpleNamespace(ainvoke=invoke))
        request=WalkRequest(city='上海',interests=['公园'],notes='最后找一家咖啡店')
        result=await preference_agent(model).parse_preferences(request)
        self.assertEqual(result.end_with,'咖啡馆')
        payload=json.loads(invoke.call_args.args[0][-1].content)
        self.assertEqual(payload['notes'],'最后找一家咖啡店')
        # 模型漏掉表单中的明确类别时，由表单合并，已有描述保持原样。
        invoke.return_value=WalkSearchProfile(searches=[SearchPreference(query='咖啡馆',english='cafe')],
                                             request_english='Only cafes')
        result=await preference_agent(model).parse_preferences(request)
        self.assertEqual(list(result.keywords),['公园','咖啡馆'])
        self.assertEqual(result.keywords['咖啡馆'],'cafe')

    async def test_edits_do_not_delete_current_model_order_end_or_searches(self):
        generated=WalkSearchProfile(searches=[SearchPreference(query='公园',english='park'),
                SearchPreference(query='书店',english='bookstore'),SearchPreference(query='咖啡馆',english='cafe')],
            request_english='New plan: bookstore, park, then cafe',visit_order=['书店','公园'],end_with='咖啡馆')
        invoke=AsyncMock(return_value=generated)
        model=SimpleNamespace(with_structured_output=lambda schema,**kwargs:SimpleNamespace(ainvoke=invoke))
        edits={'clear_visit_order':True,'clear_end_with':True,'removed_interests':['公园','咖啡馆']}
        profile=await preference_agent(model).parse_preferences(
            WalkRequest(city='上海',interests=['书店'],visit_order=[],end_with=None),edits=edits)
        self.assertEqual(profile.visit_order,['书店','公园'])
        self.assertEqual(profile.end_with,'咖啡馆')
        self.assertEqual(set(profile.keywords),{'公园','书店','咖啡馆'})
        payload=json.loads(invoke.call_args.args[0][1].content)
        self.assertEqual(payload['edits'],edits)

    async def test_empty_form_fields_without_old_constraints_are_not_cancellation(self):
        with tempfile.TemporaryDirectory() as temp:
            trips=TripService(TripStore(Path(temp)/'trips.sqlite3'),Nearby())
            previous=SimpleNamespace(state=self.old(interests=['公园'],visit_order=[],end_with=None))
            request=CityWalkPlanRequest(trip_id=previous.state.trip_id,state_version=1,
                visit_order=[],end_with=None)
            current=await trips.prepare(request,CityWalkAgent,previous)
            self.assertFalse(current._walk_edits['clear_visit_order'])
            self.assertFalse(current._walk_edits['clear_end_with'])

    async def test_end_stays_searchable_when_removed_from_interest_list(self):
        with tempfile.TemporaryDirectory() as temp:
            trips=TripService(TripStore(Path(temp)/'trips.sqlite3'),Nearby())
            previous=SimpleNamespace(state=self.old(
                interests=['咖啡店','公园','步行街','陕西饭店'],visit_order=[],end_with='陕西饭店'))
            request=CityWalkPlanRequest(trip_id=previous.state.trip_id,state_version=1,
                interests=['咖啡店','公园','步行街'],visit_order=[],end_with='陕西饭店')
            current=await trips.prepare(request,CityWalkAgent,previous)
            self.assertEqual(current._walk_edits['removed_interests'],[])
            output={
                'searches':[{'query':v,'english':v} for v in ['咖啡店','公园','步行街','陕西饭店']],
                'request_english':'Visit a cafe, park and pedestrian street, finishing at a local restaurant.',
                'visit_order':[], 'end_with':'陕西饭店',
            }
            model=SimpleNamespace(with_structured_output=lambda schema,**kwargs:
                SimpleNamespace(ainvoke=AsyncMock(return_value=schema.model_validate(output))))
            result=await preference_agent(model).parse_preferences(
                WalkRequest(city='西安',interests=current.interests,end_with=current.end_with),
                edits=current._walk_edits,target_stops=4)
            self.assertIn('陕西饭店',result.keywords)
            self.assertEqual(result.end_with,'陕西饭店')

    async def test_order_category_is_kept_while_real_deletions_are_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            trips=TripService(TripStore(Path(temp)/'trips.sqlite3'),Nearby())
            previous=SimpleNamespace(state=self.old(interests=['公园','咖啡馆','书店'],
                                                    visit_order=['公园'],end_with='咖啡馆'))
            request=CityWalkPlanRequest(trip_id=previous.state.trip_id,state_version=1,
                interests=['公园'],visit_order=['书店','公园'],end_with=None)
            current=await trips.prepare(request,CityWalkAgent,previous)
            self.assertEqual(current._walk_edits['removed_interests'],['咖啡馆'])
            self.assertTrue(current._walk_edits['clear_end_with'])

    async def test_edit_provenance_is_not_persisted_in_state_json(self):
        with tempfile.TemporaryDirectory() as temp:
            trips=TripService(TripStore(Path(temp)/'trips.sqlite3'),Nearby())
            old=SimpleNamespace(state=self.old(interests=['公园','咖啡馆'],visit_order=['公园'],end_with='咖啡馆'))
            request=CityWalkPlanRequest(trip_id=old.state.trip_id,state_version=1,
                                       interests=['公园'],visit_order=[],end_with=None)
            result=await trips.prepare(request,CityWalkAgent,old)
            self.assertTrue(result._walk_edits['clear_visit_order'])
            self.assertTrue(result._walk_edits['clear_end_with'])
            self.assertEqual(result._walk_edits['removed_interests'],['咖啡馆'])
            self.assertNotIn('_walk_edits',result.model_dump())
            reloaded=type(result).model_validate_json(result.model_dump_json())
            self.assertEqual(reloaded._walk_edits,{})

    async def test_repeated_category_in_sequence_uses_different_pois(self):
        result=await walk().execute(self.old(current_location=ORIGIN,target_stops=3,
            interests=['公园','咖啡馆'],visit_order=['公园','咖啡馆','公园']))
        self.assertTrue(result.places[0].poi.id.startswith('park'))
        self.assertEqual(result.places[1].poi.id,'cafe')
        self.assertTrue(result.places[2].poi.id.startswith('park'))
        self.assertEqual(len({p.poi.id for p in result.places}),3)

    async def test_final_category_can_also_be_visited_earlier(self):
        result=await walk().execute(self.old(current_location=ORIGIN,target_stops=3,
            interests=['公园'],visit_order=[],end_with='公园'))
        self.assertEqual(len(result.places),3)
        self.assertEqual(len({p.poi.id for p in result.places}),3)
        self.assertTrue(all(p.poi.id.startswith('park') for p in result.places))

    async def test_form_chat_and_history_keep_mixed_preferences(self):
        with tempfile.TemporaryDirectory() as temp:
            amap=Nearby()
            trips=TripService(TripStore(Path(temp)/'trips.sqlite3'),amap)
            main=MainAgent(trips,FakeExtractor([PreferencePatch(interests=['咖啡馆'],end_with='咖啡馆',
                 routing_english='Add a cafe as the final stop of this walk')]),FakeRouter(['CITYWALK']),
                 agents={'CITYWALK':walk(amap=amap)})
            app.dependency_overrides.update({get_main_agent:lambda:main,get_trip_service:lambda:trips})
            self.addCleanup(app.dependency_overrides.clear)
            with TestClient(app) as client:
                first=client.post('/api/v1/citywalk/plan',json={'city':'上海','start_location':ORIGIN.model_dump(),
                    'interests':['公园'],'target_stops':3})
                self.assertEqual(first.status_code,200,first.text)
                trip_id=first.json()['data']['trip_id']
                second=client.post('/api/v1/travel',json={'trip_id':trip_id,'state_version':1,
                    'message':'加一家咖啡馆，放在最后，总地点数不变'})
                self.assertEqual(second.status_code,200,second.text)
                self.assertEqual(second.json()['data']['places'][-1]['poi']['id'],'cafe')
                latest=client.get(f'/api/v1/trips/{trip_id}').json()['data']['state']
                self.assertEqual(latest['interests'],['公园','咖啡馆'])
                self.assertEqual(latest['target_stops'],3)
                old=client.get(f'/api/v1/trips/{trip_id}?version=1').json()['data']['state']
                self.assertEqual(old['interests'],['公园'])
                self.assertIsNone(old['end_with'])
                changes=second.json()['data']['narration']['changes']
                self.assertTrue(any('最后一站偏好' in change for change in changes))
