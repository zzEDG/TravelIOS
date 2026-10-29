"""离线验证地点、路线和图片编排，不调用第三方服务。"""
from unittest.mock import AsyncMock

import json
import sys
import unittest
from datetime import date
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agents.citywalk_agent import CityWalkAgent  # noqa: E402
from app.models.travel import WeatherState
from app.models.walk import WalkRequest  # noqa: E402
from app.integrations.amap_service import AmapService  # noqa: E402
from app.integrations.unsplash_service import UnsplashService  # noqa: E402
from walk_fixtures import fake_walk_preferences
from candidate_fixtures import OfflineCandidateSearch


class FakeResponse:
    def __init__(self, data=None, content=b"PNG", content_type="application/json"):
        self.data = data or {}
        self.content = content
        self.headers = {"content-type": content_type}

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeClient:
    def __init__(self):
        self.calls = []

    async def get(self, url, params=None, headers=None):
        self.calls.append((url, params))
        if url.endswith(("/place/text", "/place/around")):
            places = {
                "历史建筑": ("p1", "老建筑", "121.470000,31.230000"),
                "咖啡馆": ("p2", "转角咖啡", "121.472000,31.231000"),
                "博物馆": ("p3", "城市博物馆", "121.474000,31.232000"),
            }
            poi_id, name, location = places[params["keywords"]]
            return FakeResponse({"status": "1", "pois": [{
                "id": poi_id, "name": name, "location": location,
                "address": "上海市测试路", "type": params["keywords"], "adcode": "310101",
            }]})
        if url.endswith("/geocode/geo"):
            return FakeResponse({"status": "1", "geocodes": [{"location": "121.469000,31.230000"}]})
        if url.endswith("/direction/walking"):
            return FakeResponse({"status": "1", "route": {"paths": [{"distance": "420", "duration": "360"}]}})
        if url.endswith("/weather/weatherInfo"):
            return FakeResponse({"status": "1", "forecasts": [{"casts": [{
                "date": date.today().isoformat(), "dayweather": "晴", "nightweather": "多云",
                "daytemp": "22", "nighttemp": "16",
            }]}]})
        if url.endswith("/staticmap"):
            return FakeResponse(content=b"PNG", content_type="image/png")
        if url.endswith("/search/photos"):
            return FakeResponse({"results": [{
                "urls": {"regular": "https://images.unsplash.com/test"},
                "user": {"name": "Test Photographer", "links": {"html": "https://unsplash.com/@test"}},
                "links": {"html": "https://unsplash.com/photos/test"},
                "alt_description": "City street",
            }]})
        raise AssertionError(url)


class FakeAgent:
    def __init__(self, output):
        self.output = output

    async def ainvoke(self, prompt):
        return self.output


class FakeJev:
    def __init__(self):
        self.calls = []

    async def decide(self, state, questions):
        self.calls.append((state, questions))
        if "next_poi" in questions:
            choice = next(iter(questions["next_poi"]["criteria"]))
            return {"next_poi": {"type": "choice", "choice": choice,
                                 "confidence": 0.9, "probabilities": {choice: 0.9}}}
        return {name: {"type": "score", "score": 3 - index * 0.5,
                       "confidence": 0.9, "legend": {}, "probabilities": {}}
                for index, name in enumerate(questions)}


class FakeTranslator:
    async def brief(self, request, allowed):
        translations = {
            "上海": "Shanghai", "建筑与咖啡": "Architecture and Coffee",
            "历史建筑": "historic building", "咖啡馆": "cafe",
            "博物馆": "museum", "书店": "bookstore",
        }
        return (
            {"city": translations.get(request.city, request.city),
             "theme": translations.get(request.theme, request.theme),
             "notes": "quiet places" if request.notes else ""},
            {word: translations[word] for word in allowed},
        )

    async def places(self, places):
        names = {"p1": "Old Building", "p2": "Corner Cafe", "p3": "City Museum"}
        return {
            p.poi_id: {"name": names[p.poi_id], "category": "attraction",
                       "address": "Test Road, Shanghai"}
            for p in places
        }


class CityWalkTests(unittest.IsolatedAsyncioTestCase):
    def make_agent(self):
        client = FakeClient()
        agent = CityWalkAgent.__new__(CityWalkAgent)
        agent.jev = FakeJev()
        agent.translator = FakeTranslator()
        agent.parse_preferences = fake_walk_preferences
        agent.candidate_search = OfflineCandidateSearch()
        from unittest.mock import Mock
        agent.narrator_agent = Mock(legacy_story=AsyncMock())
        agent.narrator_agent.legacy_story.return_value = {
            "title": "建筑与咖啡慢走", "introduction": "从老建筑走到咖啡馆。",
            "reasons": ["第一站", "第二站", "第三站"], "rain_tip": "请查看当天预报。",
        }
        agent.amap = AmapService("test", client=client)
        agent.unsplash = UnsplashService("test", client=client)
        class FakeWeather:
            async def lookup(self, location, visit_date=None):
                return WeatherState(source="QWEATHER", forecast_date=date.today(), day_condition="晴",
                                    night_condition="多云",day_temperature_c=22,night_temperature_c=16), None
        agent.weather_service = FakeWeather()
        return agent, client

    async def test_plan_uses_only_real_pois_and_credits_photo(self):
        agent, client = self.make_agent()
        plan = await agent.plan_legacy(WalkRequest(city="上海", theme="建筑与咖啡", start_place="静安寺"))
        self.assertEqual({stop.poi_id for stop in plan.stops}, {"p1", "p2", "p3"})
        self.assertEqual(len(plan.legs), 3)
        self.assertTrue(all(leg.duration_min == 6 for leg in plan.legs))
        self.assertEqual(plan.cover.photographer, "Test Photographer")
        self.assertIn("utm_source=citywalk-agent", plan.cover.photographer_url)
        self.assertEqual(plan.weather.day_weather, "晴")
        self.assertEqual(len(plan.map_points.split(";")), 3)
        self.assertEqual(await agent.amap.static_map([(stop.longitude, stop.latitude) for stop in plan.stops]), (b"PNG", "image/png"))
        self.assertTrue(any(url.endswith("/staticmap") for url, _ in client.calls))

    async def test_explicit_origin_includes_first_leg_and_walking_limit(self):
        agent, _ = self.make_agent()
        request = WalkRequest(city="上海", theme="建筑与咖啡")
        plan = await agent.plan_legacy(
            request, origin_coordinates=(121.469, 31.230),
            target_stops=2, max_walking_meters=900,
        )
        self.assertEqual(len(plan.stops), 2)
        self.assertEqual(len(plan.legs), 2)
        self.assertEqual(plan.legs[0].from_name, "当前位置")
        self.assertIsNone(plan.legs[0].from_poi_id)
        self.assertEqual(plan.legs[0].to_poi_id, plan.stops[0].poi_id)
        with self.assertRaisesRegex(ValueError, "步行距离上限"):
            await agent.plan_legacy(
                request, origin_coordinates=(121.469, 31.230),
                target_stops=2, max_walking_meters=500,
            )

    async def test_free_preferences_choose_only_verified_pois(self):
        agent, client = self.make_agent()
        plan = await agent.plan_legacy(WalkRequest(city="上海", theme="建筑与咖啡"))
        self.assertEqual({stop.poi_id for stop in plan.stops}, {"p1", "p2", "p3"})
        first_state, _ = agent.jev.calls[0]
        self.assertEqual(first_state['priority_preferences'], ['historic building','cafe','museum'])
        self.assertIn('current_location',first_state)
        choices = [q["next_poi"] for _, q in agent.jev.calls]
        self.assertEqual(len(choices), 3)
        self.assertTrue(all(q["type"] == "choice" for q in choices))
        self.assertEqual(set().union(*(set(q["criteria"]) for q in choices)), {"p1", "p2", "p3", "__no_suitable_poi__"})
        self.assertTrue(all("Old Building" in str(q["criteria"]) or "Corner Cafe" in str(q["criteria"]) or "City Museum" in str(q["criteria"]) for q in choices))
        self.assertTrue(all("老建筑" not in str(q) and "上海" not in str(q) for _, q in agent.jev.calls))

    async def test_jev_can_decline_irrelevant_candidates(self):
        agent, _ = self.make_agent()

        class NoMatchJev(FakeJev):
            async def decide(self, state, questions):
                if "next_poi" in questions:
                    return {"next_poi": {"type": "choice", "choice": "__no_suitable_poi__"}}
                return await super().decide(state, questions)

        agent.jev = NoMatchJev()
        with self.assertRaisesRegex(ValueError, "没有符合偏好"):
            await agent.plan_legacy(WalkRequest(city="上海", theme="建筑与咖啡"))

    async def test_route_keeps_two_stops_when_jev_declines_third(self):
        agent, _ = self.make_agent()

        class DeclineThirdJev(FakeJev):
            async def decide(self, state, questions):
                if "next_poi" in questions and len(state["selected"]) >= 2:
                    return {"next_poi": {"type": "choice", "choice": "__no_suitable_poi__"}}
                return await super().decide(state, questions)

        agent.jev = DeclineThirdJev()
        plan = await agent.plan_legacy(WalkRequest(city="上海", theme="建筑与咖啡"))
        self.assertEqual(len(plan.stops), 2)
        self.assertTrue(any("路线已缩短" in note for note in plan.notes))

    async def test_jev_cannot_invent_a_poi(self):
        agent, _ = self.make_agent()

        class BadJev(FakeJev):
            async def decide(self, state, questions):
                if "next_poi" in questions:
                    return {"next_poi": {"type": "choice", "choice": "invented"}}
                return await super().decide(state, questions)

        agent.jev = BadJev()
        with self.assertRaisesRegex(ValueError, "候选范围外"):
            await agent.plan_legacy(WalkRequest(city="上海", theme="建筑与咖啡"))


if __name__ == "__main__":
    unittest.main()
