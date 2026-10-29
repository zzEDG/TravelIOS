"""和风v1协议与实际子 Agent接入测试，不使用真实密钥。"""
import sys
import unittest
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.models.travel import GeoLocation, WeatherState
from app.integrations.qweather_service import QWeatherService, QWeatherError
from app.agents.attraction_agent import AttractionAgent
from test_discovery import FakeAmap, FakeJev, FakeTranslator, state


def payload(day=None):
    day = day or datetime.now(ZoneInfo("Asia/Shanghai")).date()
    return {"days":[{
        "temperatureMax":{"value":28}, "temperatureMin":{"value":19},
        "daytime":{"forecastStartTime":day.isoformat()+"T07:00:00+08:00",
                   "condition":{"text":"小雨"}, "temperatureMax":{"value":27},
                   "precipitation":{"probability":0.65}},
        "nighttime":{"condition":{"text":"多云"}, "temperatureMin":{"value":19},
                     "precipitation":{"probability":0.1}},
    }]}


class QWeatherTests(unittest.IsolatedAsyncioTestCase):
    location = GeoLocation(latitude=31.23,longitude=121.47)

    def service(self, handler, **kwargs):
        return QWeatherService(host="account.xy.qweatherapi.com",api_key="test-secret",jwt="",
                               client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),**kwargs)

    async def test_current_v1_endpoint_header_date_temperature_and_probability(self):
        calls=[]
        def handler(req):
            calls.append(req)
            return httpx.Response(200,json=payload())
        weather=await self.service(handler).forecast(self.location)
        self.assertEqual(calls[0].url.path,"/weather/v1/daily/31.23/121.47")
        self.assertEqual(calls[0].url.params["days"],"10")
        self.assertEqual(calls[0].url.params["localTime"],"true")
        self.assertEqual(calls[0].headers["X-QW-Api-Key"],"test-secret")
        self.assertNotIn("test-secret",str(calls[0].url))
        self.assertEqual(weather.source,"QWEATHER")
        self.assertEqual(weather.temperature_max_c,28)
        self.assertEqual(weather.precipitation_probability_day,.65)
        self.assertIsNone(weather.rain_probability)
        self.assertIsNone(weather.is_outdoor_favorable)

    async def test_jwt_takes_precedence_and_never_sends_two_auth_methods(self):
        calls=[]
        def handler(req):
            calls.append(req)
            return httpx.Response(200,json=payload())
        service=QWeatherService(host="https://account.qweatherapi.com/",api_key="key",jwt="token",
                               client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        await service.forecast(self.location)
        self.assertEqual(calls[0].headers["Authorization"],"Bearer token")
        self.assertNotIn("X-QW-Api-Key",calls[0].headers)

    async def test_failure_missing_config_and_out_of_range_are_diagnostic(self):
        service=self.service(lambda req:httpx.Response(401,json={}))
        weather,notice=await service.lookup(self.location)
        self.assertIsNone(weather)
        self.assertIn("HTTP 401",notice)
        missing=QWeatherService(host="",api_key="",jwt="")
        self.assertIn("未配置",(await missing.lookup(self.location))[1])
        with self.assertRaises(QWeatherError):
            await QWeatherService(host="https://unrelated.example",api_key="key",jwt="").forecast(self.location)
        future=datetime.now(ZoneInfo("Asia/Shanghai")).date()+timedelta(days=10)
        self.assertIn("10天",(await service.lookup(self.location,future))[1])
        wrong_day=future
        self.assertIn("所选日期",(await self.service(lambda req:httpx.Response(200,json=payload(wrong_day))).lookup(self.location))[1])

    async def test_security_restriction_explains_console_action_without_echoing_body(self):
        data = {"error": {
            "type": "https://dev.qweather.com/docs/resource/error-code/#security-restriction",
            "detail": "sensitive test-secret",
        }}
        weather, notice = await self.service(lambda req: httpx.Response(403, json=data)).lookup(self.location)
        self.assertIsNone(weather)
        self.assertIn("Security Restriction", notice)
        self.assertIn("应用限制和API限制", notice)
        self.assertNotIn("test-secret", notice)
        # 其他403和非JSON错误不能误判为安全限制。
        for body in ({"error": {"type": "other"}}, {"error": None}, []):
            with self.subTest(body=body):
                notice = (await self.service(lambda req: httpx.Response(403, json=body)).lookup(self.location))[1]
                self.assertIn("HTTP 403", notice)
                self.assertNotIn("Security Restriction", notice)
        notice = (await self.service(lambda req: httpx.Response(403, text="not-json")).lookup(self.location))[1]
        self.assertIn("HTTP 403", notice)

    async def test_missing_or_invalid_probability_does_not_turn_into_zero(self):
        response=payload()
        response["days"][0]["daytime"]["precipitation"]["probability"]=65
        response["days"][0]["nighttime"].pop("precipitation")
        weather=await self.service(lambda req:httpx.Response(200,json=response)).forecast(self.location)
        self.assertIsNone(weather.precipitation_probability_day)
        self.assertIsNone(weather.precipitation_probability_night)

    async def test_attraction_uses_weather_service_and_honors_weather_switch(self):
        class FakeWeather:
            def __init__(self): self.calls=[]
            async def lookup(self,location,visit_date):
                self.calls.append((location,visit_date))
                return WeatherState(source="QWEATHER",forecast_date=datetime.now(ZoneInfo("Asia/Shanghai")).date(),
                                    day_condition="小雨",night_condition="多云",precipitation_probability_day=.65),None
        weather=FakeWeather()
        skill=AttractionAgent(FakeAmap(),FakeJev(),FakeTranslator(),weather_service=weather)
        from candidate_fixtures import OfflineCandidateSearch
        skill.candidate_search=OfflineCandidateSearch()
        s=state("ATTRACTION")
        plan=await skill.execute(s)
        self.assertEqual(plan.weather.source,"QWEATHER")
        self.assertEqual(len(weather.calls),1)
        self.assertEqual(weather.calls[0][0],s.current_location)
        disabled=await skill.execute(s.model_copy(update={"consider_weather":False}))
        self.assertIsNone(disabled.weather)
        self.assertEqual(len(weather.calls),1)
