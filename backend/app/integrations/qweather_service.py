"""和风WebAPI v1每日预报；仅请求天气，不生成天气事实。"""

from datetime import date, datetime
from math import isfinite
import re
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings
from app.models.travel import GeoLocation, WeatherState


class QWeatherError(ValueError):
    pass


def _number(value):
    try:
        number = float(value)
        return number if isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _probability(section):
    value = _number(section.get("precipitation", {}).get("probability"))
    return value if value is not None and 0 <= value <= 1 else None


class QWeatherService:
    def __init__(self, host=None, api_key=None, jwt=None, client=None):
        settings = get_settings()
        self.host = (settings.qweather_api_host if host is None else host).strip()
        self.api_key = settings.qweather_api_key if api_key is None else api_key
        self.jwt = settings.qweather_jwt if jwt is None else jwt
        self.client = client or httpx.AsyncClient(timeout=12, follow_redirects=False, trust_env=False)

    async def forecast(self, location: GeoLocation, visit_date: date | None = None) -> WeatherState:
        if not self.host or not (self.api_key or self.jwt):
            raise QWeatherError("和风天气未配置，请填写QWEATHER_API_HOST和API_KEY或JWT")
        host = self.host.removeprefix("https://").rstrip("/")
        if not re.fullmatch(r"[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)*\.qweatherapi\.com", host):
            raise QWeatherError("和风API Host格式错误，请填写控制台中的专属qweatherapi.com域名")
        wanted = visit_date or datetime.now(ZoneInfo("Asia/Shanghai")).date()
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        if not 0 <= (wanted - today).days < 10:
            raise QWeatherError("所选日期超出和风未来10天预报范围，请临近出发时再查询")
        headers = {"Authorization": f"Bearer {self.jwt}"} if self.jwt else {"X-QW-Api-Key": self.api_key}
        try:
            response = await self.client.get(
                f"https://{host}/weather/v1/daily/{location.latitude:.2f}/{location.longitude:.2f}",
                params={"days": 10, "localTime": "true", "lang": "zh"},
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            # 只映射已知错误类型，不向页面透传可能含敏感信息的响应正文。
            error_type = ""
            try:
                error_data = exc.response.json()
                if isinstance(error_data, dict) and isinstance(error_data.get("error"), dict):
                    error_type = error_data["error"].get("type", "")
            except ValueError:
                pass
            if exc.response.status_code == 403 and error_type == (
                "https://dev.qweather.com/docs/resource/error-code/#security-restriction"
            ):
                raise QWeatherError(
                    "和风天气请求被安全限制拒绝（HTTP 403 / Security Restriction）。"
                    "请在和风控制台对应凭据中检查应用限制和API限制，"
                    "允许当前后端访问每日天气预报；服务未返回具体命中的规则。"
                ) from None
            raise QWeatherError(f"和风天气请求失败（HTTP {exc.response.status_code}），请检查凭据、Host和接口权限") from None
        except (httpx.HTTPError, ValueError):
            raise QWeatherError("和风天气网络或响应异常，请稍后重试") from None
        if not isinstance(data, dict) or not isinstance(data.get("days"), list):
            raise QWeatherError("和风未返回有效的每日预报")
        for item in data["days"]:
            try:
                daytime, nighttime = item["daytime"], item["nighttime"]
                local_day = datetime.fromisoformat(daytime["forecastStartTime"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Shanghai")).date()
                if local_day != wanted:
                    continue
                day_text = daytime["condition"]["text"]
                night_text = nighttime["condition"]["text"]
                if not isinstance(day_text, str) or not isinstance(night_text, str) or not day_text or not night_text:
                    continue
                maximum = _number(item.get("temperatureMax", {}).get("value"))
                minimum = _number(item.get("temperatureMin", {}).get("value"))
                return WeatherState(
                    source="QWEATHER", source_url="https://www.qweather.com",
                    forecast_date=wanted, day_condition=day_text, night_condition=night_text,
                    day_temperature_c=_number(daytime.get("temperatureMax", {}).get("value")),
                    night_temperature_c=_number(nighttime.get("temperatureMin", {}).get("value")),
                    temperature_max_c=maximum, temperature_min_c=minimum,
                    precipitation_probability_day=_probability(daytime),
                    precipitation_probability_night=_probability(nighttime),
                )
            except (KeyError, TypeError, ValueError, AttributeError):
                continue
        raise QWeatherError("和风未返回所选日期的有效预报")

    async def lookup(self, location, visit_date=None):
        try:
            return await self.forecast(location, visit_date), None
        except QWeatherError as exc:
            return None, str(exc)
