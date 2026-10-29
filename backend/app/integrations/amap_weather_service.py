"""用起点行政区查询高德预报，并适配共享天气模型。"""

from datetime import datetime
from math import isfinite
from zoneinfo import ZoneInfo

import httpx

from app.models.travel import WeatherState


def _temperature(value):
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError):
        return None


class AmapWeatherService:
    def __init__(self, amap):
        self.amap = amap

    async def lookup(self, location, visit_date=None):
        wanted = visit_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()
        try:
            adcode = await self.amap.reverse_adcode(location.longitude, location.latitude)
            forecast = await self.amap.weather(adcode, wanted)
            if forecast is None:
                return None, "高德未提供所选日期的天气预报，请临近出发时再查询。"
            return WeatherState(
                source="AMAP", source_url="https://www.amap.com",
                forecast_date=forecast.date,
                day_condition=forecast.day_weather,
                night_condition=forecast.night_weather,
                day_temperature_c=_temperature(forecast.day_temp),
                night_temperature_c=_temperature(forecast.night_temp),
            ), None
        except httpx.HTTPError:
            # HTTP异常可能携带含Key的URL，不向前端回传。
            return None, "高德天气网络请求失败，请稍后重试。"
        except (ValueError, TypeError, KeyError, AttributeError):
            return None, "高德天气或起点行政区查询失败，请检查高德服务权限与返回数据。"
