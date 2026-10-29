"""回放真实模型输出，验证明确顺序与末站不受模型的表示方式影响。"""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

from fastapi.testclient import TestClient

from app.agents.main_agent import MainAgent
from app.controllers.dependencies import get_main_agent, get_trip_service
from app.integrations.amap_service import Place
from app.main import app
from app.models.trip_store import TripStore
from app.models.walk import WalkRequest
from app.services.preferences import PreferenceExtractionError
from app.services.trip_service import TripService
from walk_fixtures import preference_agent
from narrator_fixtures import OfflineNarrationMixin
from test_walk_preferences import Nearby, ORIGIN, walk


# 来自导致 502 的真实工具调用参数：模型把 end_with 也追加到了 visit_order。
CAPTURED = {
    'searches': [
        {'query':'书店','english':'Bookstore (top preference)'},
        {'query':'博物馆','english':'Museum'},
        {'query':'公园','english':'Park'},
        {'query':'陕西菜馆','english':'Authentic Shaanxi restaurant, final stop'},
    ],
    'request_english': 'Plan a 3-hour city walk in Shanghai with three stops plus a final meal. '
        'The strongest interest is bookstores (keep the original term). Visit order: bookstore, '
        'museum, park, then end at an authentic Shaanxi restaurant as the last stop. '
        'No other categories were requested.',
    'visit_order':['书店','博物馆','公园','陕西菜馆'],
    'end_with':'陕西菜馆',
}


class CapturedModel:
    def __init__(self, output=None):
        self.output = deepcopy(CAPTURED if output is None else output)

    def with_structured_output(self, schema, **kwargs):
        async def invoke(messages):
            # 按真实模型适配器的顺序，先应用传入的输出 schema，再执行业务归一化。
            return schema.model_validate(deepcopy(self.output))
        return SimpleNamespace(ainvoke=invoke)


def form_request(**changes):
    return WalkRequest(city='上海', interests=['书店'],
                       notes='给我安排三个地点，最后用一家正宗的陕西菜馆收尾吧',
                       **{'visit_order':['书店','博物馆','公园'], 'end_with':'陕西菜馆', **changes})


class WalkOrderResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_model_order_is_preserved_without_mutating_form(self):
        request = form_request()
        result = await preference_agent(CapturedModel()).parse_preferences(request)
        self.assertEqual(result.visit_order, CAPTURED['visit_order'])
        self.assertEqual(result.end_with, '陕西菜馆')
        self.assertEqual(request.visit_order, ['书店','博物馆','公园'])

    async def test_current_model_order_and_end_are_not_overwritten_by_form(self):
        output = deepcopy(CAPTURED)
        output.update(visit_order=['公园','博物馆','书店'],end_with='书店')
        result = await preference_agent(CapturedModel(output)).parse_preferences(form_request())
        self.assertEqual(result.visit_order,output['visit_order'])
        self.assertEqual(result.end_with,output['end_with'])

    async def test_model_can_still_infer_order_and_end_from_notes(self):
        result = await preference_agent(CapturedModel()).parse_preferences(
            form_request(visit_order=[], end_with=None))
        self.assertEqual(result.visit_order, ['书店','博物馆','公园','陕西菜馆'])
        self.assertEqual(result.end_with, '陕西菜馆')

    async def test_explicit_end_in_order_is_preserved_for_route_normalization(self):
        request = form_request(visit_order=['书店','博物馆','公园','陕西菜馆'])
        result = await preference_agent(CapturedModel()).parse_preferences(request)
        self.assertEqual(result.visit_order, ['书店','博物馆','公园','陕西菜馆'])
        self.assertEqual(request.visit_order[-1], '陕西菜馆')

    async def test_repeated_category_earlier_in_order_is_preserved(self):
        request = form_request(visit_order=['书店','公园','书店'], end_with='书店')
        output = deepcopy(CAPTURED)
        output.update(visit_order=['书店','公园','书店'],end_with='书店')
        result = await preference_agent(CapturedModel(output)).parse_preferences(request)
        self.assertEqual(result.visit_order, ['书店','公园','书店'])
        self.assertEqual(result.end_with, '书店')

    async def test_clear_metadata_does_not_erase_new_model_order_or_end(self):
        result = await preference_agent(CapturedModel()).parse_preferences(
            form_request(visit_order=[],end_with=None),
            edits={'clear_visit_order':True,'clear_end_with':True})
        self.assertEqual(result.visit_order,CAPTURED['visit_order'])
        self.assertEqual(result.end_with,'陕西菜馆')

    async def test_missing_explicit_end_search_is_merged_from_form(self):
        output = deepcopy(CAPTURED)
        output['searches'] = [item for item in output['searches'] if item['query'] != '陕西菜馆']
        result = await preference_agent(CapturedModel(output)).parse_preferences(form_request())
        self.assertEqual(result.end_with,'陕西菜馆')
        self.assertIn('陕西菜馆',result.keywords)
        self.assertEqual(result.visit_order,CAPTURED['visit_order'])

    async def test_unknown_inferred_reference_is_still_rejected(self):
        output = deepcopy(CAPTURED)
        output['visit_order'] = ['未知类别']
        with self.assertRaises(PreferenceExtractionError):
            await preference_agent(CapturedModel(output)).parse_preferences(
                form_request(visit_order=[], end_with=None))


class FourLocations(Nearby):
    async def search_around(self, city, lon, lat, keyword, radius, types):
        index = ['书店','博物馆','公园','陕西菜馆'].index(keyword)
        return [Place(f'poi{index}', keyword, '测试路', keyword, 121.47+index*.001,
                      31.23, '310101', keyword)]


class FourTranslations:
    async def places(self, places):
        return {p.poi_id:{'name':p.poi_id, 'category':'Known category', 'address':'Example Road'}
                for p in places}


class WalkOrderHTTPTests(OfflineNarrationMixin, unittest.TestCase):
    def test_original_form_reaches_four_stops_and_persists_normalized_history(self):
        with tempfile.TemporaryDirectory() as temp:
            trips = TripService(TripStore(Path(temp)/'trips.sqlite3'), FourLocations())
            agent = walk(amap=trips.amap)
            agent.parse_preferences = preference_agent(CapturedModel()).parse_preferences
            agent.translator = FourTranslations()
            main = MainAgent(trips, agents={'CITYWALK':agent})
            app.dependency_overrides.update({get_main_agent:lambda:main, get_trip_service:lambda:trips})
            self.addCleanup(app.dependency_overrides.clear)
            payload = {'city':'上海','start_location':ORIGIN.model_dump(), 'target_stops':4,
                       'available_hours':3, 'interests':['书店'],
                       'visit_order':['书店','博物馆','公园'],'end_with':'陕西菜馆',
                       'additional_requirements':'给我安排三个地点，最后用一家正宗的陕西菜馆收尾吧'}
            with TestClient(app) as client:
                response = client.post('/api/v1/citywalk/plan', json=payload)
                self.assertEqual(response.status_code, 200, response.text)
                data = response.json()['data']
                self.assertEqual([item['poi']['name'] for item in data['places']],
                                 ['书店','博物馆','公园','陕西菜馆'])
                saved = client.get(f"/api/v1/trips/{data['trip_id']}").json()['data']['state']
                self.assertEqual(saved['visit_order'], CAPTURED['visit_order'])
                self.assertEqual(saved['end_with'], '陕西菜馆')
                self.assertEqual(saved['target_stops'], 4)
                # 用户已把末站放进顺序列表时，路线仍只安排四站。
                explicit_end = client.post('/api/v1/citywalk/plan', json={
                    **payload, 'visit_order':['书店','博物馆','公园','陕西菜馆']})
                self.assertEqual(explicit_end.status_code, 200, explicit_end.text)
                self.assertEqual([item['poi']['name'] for item in explicit_end.json()['data']['places']],
                                 ['书店','博物馆','公园','陕西菜馆'])
                # 真实的三站/四个必去地点冲突仍由路线规则明确拒绝。
                conflict = client.post('/api/v1/citywalk/plan', json={**payload, 'target_stops':3})
                self.assertEqual(conflict.status_code, 422, conflict.text)
                self.assertIn('多于目标数量', conflict.json()['message'])
