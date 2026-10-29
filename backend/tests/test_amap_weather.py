"""高德天气协议、来源、日期匹配及失败降级。"""
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from unittest.mock import patch

import httpx

from app.integrations.amap_service import AmapService
from app.integrations.amap_weather_service import AmapWeatherService
from app.models.travel import GeoLocation
from app.agents.attraction_agent import AttractionAgent
from test_discovery import FakeAmap, FakeJev, FakeTranslator, state
from candidate_fixtures import OfflineCandidateSearch


class AmapWeatherTests(unittest.IsolatedAsyncioTestCase):
    location = GeoLocation(longitude=121.47, latitude=31.23)

    async def test_protocol_source_date_and_unknown_probability(self):
        day = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        calls = []
        def handler(req):
            calls.append(req)
            if req.url.path.endswith("/regeo"):
                self.assertEqual(req.url.params["location"], "121.470000,31.230000")
                return httpx.Response(200, json={"status": "1", "regeocode": {
                    "addressComponent": {"adcode": "310101"}}})
            self.assertEqual(req.url.params["city"], "310101")
            self.assertEqual(req.url.params["extensions"], "all")
            return httpx.Response(200, json={"status": "1", "forecasts": [{"casts": [{
                "date": day.isoformat(), "dayweather": "晴", "nightweather": "多云",
                "daytemp": "25", "nighttemp": "18",
            }]}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            service = AmapWeatherService(AmapService("test-secret", client))
            weather, notice = await service.lookup(self.location)
            self.assertIsNone(notice)
            self.assertEqual(weather.source, "AMAP")
            self.assertEqual(weather.day_temperature_c, 25)
            self.assertIsNone(weather.precipitation_probability_day)
            self.assertIsNone(weather.temperature_max_c)
            weather, notice = await service.lookup(self.location, day + timedelta(days=15))
            self.assertIsNone(weather)
            self.assertIn("所选日期", notice)
        self.assertEqual(len(calls), 4)

    async def test_api_and_network_failure_do_not_expose_key(self):
        for response in [
            lambda req: httpx.Response(403, text="test-secret"),
            lambda req: httpx.Response(200, json={"status": "0", "info": "test-secret"}),
            lambda req: httpx.Response(200, json={"status": "1", "regeocode": {}}),
        ]:
            async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                weather, notice = await AmapWeatherService(AmapService("test-secret", client)).lookup(self.location)
                self.assertIsNone(weather)
                self.assertIn("高德", notice)
                self.assertNotIn("test-secret", notice)

    async def test_attraction_can_inject_amap_and_honors_switch(self):
        from app.models.walk import Weather
        amap = FakeAmap()
        day = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        with patch.object(amap, "weather", return_value=Weather(
            date=day.isoformat(), day_weather="晴", night_weather="晴", day_temp="25", night_temp="18"
        )) as lookup:
            skill = AttractionAgent(amap, FakeJev(), FakeTranslator(), weather_service=AmapWeatherService(amap))
            skill.candidate_search = OfflineCandidateSearch()
            plan = await skill.execute(state("ATTRACTION"))
            self.assertEqual(plan.weather.source, "AMAP")
            self.assertEqual(lookup.call_count, 1)
            plan = await skill.execute(state("ATTRACTION").model_copy(update={"consider_weather": False}))
            self.assertIsNone(plan.weather)
            self.assertEqual(lookup.call_count, 1)
