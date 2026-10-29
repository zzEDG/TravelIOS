"""真实西安表单与 DS 空结构化输出的行为回归。"""
import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
import httpx
from langchain_core.messages import AIMessage
from openai import APIConnectionError
from app.models.walk import WalkRequest
from app.services.preferences import PreferenceExtractionError
from walk_fixtures import preference_agent
from app.controllers.errors import _handle_error


def form():
    return WalkRequest(city='西安',duration_hours=3,visit_date=date(2026,10,23),
        notes='咖啡店要手作的那种，而不是连锁的瑞幸什么的。陕西饭店要正宗的，不是商场里的网红店',
        interests=['咖啡店','公园','步行街'],visit_order=['咖啡店','步行街','公园'],
        end_with='陕西饭店',avoid_commercial=True)


def draft():
    return {'searches':[
        {'query':'咖啡店','english':'Independent handcrafted coffee shop, no chains'},
        {'query':'公园','english':'Park'},
        {'query':'步行街','english':'Pedestrian street'},
        {'query':'陕西饭店','english':'Authentic local Shaanxi restaurant outside malls'},
    ],'request_english':'A three-hour walk: independent cafe, pedestrian street, park, then authentic Shaanxi food.',
       'visit_order':['咖啡店','步行街','公园'],'end_with':'陕西饭店'}


def empty():
    return {'raw':AIMessage(content=''),'parsed':None,'parsing_error':None}


class WalkStructuredOutputTests(unittest.IsolatedAsyncioTestCase):
    def model(self,*outputs):
        self.invoke=AsyncMock(side_effect=list(outputs))
        self.factory=Mock(return_value=SimpleNamespace(ainvoke=self.invoke))
        return SimpleNamespace(with_structured_output=self.factory)

    async def test_successful_output_preserves_exact_form_and_actual_stop_count(self):
        request=form()
        profile=await preference_agent(self.model(draft())).parse_preferences(request,target_stops=4)
        self.assertEqual(self.invoke.await_count,1)
        self.assertEqual(list(profile.keywords),['咖啡店','公园','步行街','陕西饭店'])
        self.assertEqual(profile.visit_order,request.visit_order)
        self.assertEqual(profile.end_with,'陕西饭店')
        payload=json.loads(self.invoke.call_args.args[0][1].content)
        self.assertEqual(payload['target_stops'],4)
        self.assertEqual(payload['duration_hours'],3)
        self.assertTrue(payload['avoid_commercial'])
        self.assertEqual(payload['visit_date'],'2026-10-23')

    async def test_chinese_place_names_in_english_description_do_not_reject_empty_order_form(self):
        # 回放真实 DS 失败原因：英文说明里保留了中文类别括注。
        output=draft()
        output['request_english']='A relaxed walk in Xi an, finishing at an authentic Shaanxi restaurant (陕西饭店).'
        output['searches'][-1]['english']='Authentic Shaanxi restaurant (陕西饭店), outside shopping malls'
        output['visit_order']=['咖啡店','公园','步行街']
        request=form().model_copy(update={'visit_order':[], 'visit_date':date(2026,10,19)})
        result=await preference_agent(self.model(output)).parse_preferences(request,target_stops=4)
        self.assertEqual(result.request_english,output['request_english'])
        self.assertEqual(result.visit_order,output['visit_order'])
        self.assertEqual(result.end_with,'陕西饭店')
        self.assertEqual(self.invoke.await_count,1)

    async def test_empty_output_exposes_cause_without_retry(self):
        from pydantic import ValidationError
        planner=preference_agent(self.model(empty()))
        with self.assertRaises(PreferenceExtractionError) as caught:
            await planner.parse_preferences(form(),target_stops=4)
        self.assertEqual(self.invoke.await_count,1)
        self.assertIsInstance(caught.exception.__cause__,ValidationError)

    async def test_typed_structured_output_is_used_without_extra_api_call(self):
        from app.models.walk_preferences import WalkSearchDraft
        parsed=WalkSearchDraft.model_validate_json(json.dumps(draft(),ensure_ascii=False))
        profile=await preference_agent(self.model(parsed)).parse_preferences(form(),target_stops=4)
        self.assertEqual(profile.end_with,'陕西饭店')
        self.assertEqual(self.invoke.await_count,1)

    async def test_explicit_end_is_added_when_model_omits_its_search(self):
        missing=draft()
        missing['searches']=missing['searches'][:-1]
        request=form().model_copy(update={'visit_order':[]})
        result=await preference_agent(self.model(missing)).parse_preferences(request,target_stops=4)
        self.assertEqual(result.end_with,'陕西饭店')
        self.assertEqual(list(result.keywords),['咖啡店','公园','步行街','陕西饭店'])
        self.assertEqual(result.keywords['陕西饭店'],'陕西饭店')
        self.assertEqual(self.invoke.await_count,1)

    async def test_existing_end_description_is_preserved_without_duplicate(self):
        result=await preference_agent(self.model(draft())).parse_preferences(form(),target_stops=4)
        matches=[item for item in result.searches if item.query=='陕西饭店']
        self.assertEqual(len(matches),1)
        self.assertEqual(matches[0].english,'Authentic local Shaanxi restaurant outside malls')

    async def test_cleared_end_is_not_added_back(self):
        missing=draft()
        missing['searches']=missing['searches'][:-1]
        missing['end_with']=None
        result=await preference_agent(self.model(missing)).parse_preferences(
            form().model_copy(update={'end_with':None}),
            edits={'clear_end_with':True,'removed_interests':['陕西饭店']},target_stops=4)
        self.assertIsNone(result.end_with)
        self.assertNotIn('陕西饭店',result.keywords)

    async def test_transport_failure_is_not_retried_as_a_schema_failure(self):
        failure=APIConnectionError(request=httpx.Request('POST','https://example.com'))
        planner=preference_agent(self.model(failure))
        with self.assertRaises(PreferenceExtractionError) as caught:
            await planner.parse_preferences(form(),target_stops=4)
        self.assertEqual(self.invoke.await_count,1)
        self.assertIs(caught.exception.__cause__,failure)
        self.assertEqual(_handle_error(caught.exception).code,50007)

    async def test_target_count_is_forwarded_without_repeating_route_validation(self):
        planner=preference_agent(self.model(draft()))
        await planner.parse_preferences(form(),target_stops=3)
        payload=json.loads(self.invoke.call_args.args[0][1].content)
        self.assertEqual(payload['target_stops'],3)
        self.assertEqual(self.invoke.await_count,1)
