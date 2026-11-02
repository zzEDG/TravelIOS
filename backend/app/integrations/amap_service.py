"""高德 Web 服务：真实 POI、步行路线、天气与静态地图。"""

import asyncio
import logging
import math
import os
import random
import weakref
from email.utils import parsedate_to_datetime
from datetime import timezone
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from math import asin, ceil, cos, radians, sin, sqrt
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.models.walk import Weather


BASE_URL = "https://restapi.amap.com/v3"
logger = logging.getLogger(__name__)
_QPS_CODES = {"10014", "10019", "10020", "10021"}
_QPS_INFOS = {"QPS_HAS_EXCEEDED_THE_LIMIT", "CQPS_HAS_EXCEEDED_THE_LIMIT",
              "CKQPS_HAS_EXCEEDED_THE_LIMIT", "CUQPS_HAS_EXCEEDED_THE_LIMIT"}
_SAFE_INFOS = _QPS_INFOS | {
    "DAILY_QUERY_OVER_LIMIT", "USER_DAILY_QUERY_OVER_LIMIT", "USER_ABROAD_DAILY_QUERY_OVER_LIMIT",
    "ACCESS_TOO_FREQUENT", "INVALID_USER_KEY", "SERVICE_NOT_AVAILABLE", "INVALID_USER_IP",
    "INVALID_USER_DOMAIN", "INVALID_USER_SIGNATURE", "INSUFFICIENT_PRIVILEGES", "USER_KEY_RECYCLED",
    "INVALID_PARAMS", "MISSING_REQUIRED_PARAMS", "ILLEGAL_REQUEST", "UNKNOWN_ERROR",
    "GATEWAY_TIMEOUT", "SERVER_IS_BUSY", "RESOURCE_UNAVAILABLE", "INSUFFICIENT_ABROAD_PRIVILEGES",
    "ABROAD_QUOTA_PLAN_RUN_OUT", "QUOTA_PLAN_RUN_OUT", "SERVICE_EXPIRED",
}
_RATE_LIMITERS = weakref.WeakKeyDictionary()


class _RateLimiter:
    """Evenly space request starts across all AmapService instances on one event loop."""
    def __init__(self, qps: float):
        self.interval = 1 / qps
        self.lock = asyncio.Lock()
        self.next_start = 0.0

    async def wait(self):
        loop = asyncio.get_running_loop()
        async with self.lock:
            delay = self.next_start - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self.next_start = loop.time() + self.interval


def _shared_limiter():
    loop = asyncio.get_running_loop()
    limiter = _RATE_LIMITERS.get(loop)
    if limiter is None:
        try:
            qps = float(os.getenv("AMAP_MAX_QPS", "10"))
            if not math.isfinite(qps) or qps <= 0:
                raise ValueError
        except ValueError as exc:
            raise ValueError("AMAP_MAX_QPS 必须是大于 0 的数字") from exc
        limiter = _RateLimiter(qps)
        _RATE_LIMITERS[loop] = limiter
    return limiter


def _retry_after(response):
    value = response.headers.get("retry-after")
    if not isinstance(value, str):
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = (date - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


class AmapServiceError(ValueError):
    pass


class AmapRateLimitError(AmapServiceError):
    def __init__(self, message=None):
        super().__init__(message or "高德地图接口请求过于频繁，已触发限流；部分地点或路线未能查询，请稍后重试。")


@dataclass(frozen=True)
class WalkingRoute:
    distance_m: Optional[int]
    duration_min: Optional[int]
    polyline: Optional[str]


@dataclass(frozen=True)
class Place:
    poi_id: str
    name: str
    address: str
    category: str
    longitude: float
    latitude: float
    adcode: str
    keyword: str
    typecode: str = ""


def distance_m(a: Place, b: Place) -> int:
    """仅用于候选点排序；展示给用户的步行距离来自高德路线接口。"""
    lat1, lat2 = radians(a.latitude), radians(b.latitude)
    dlat = lat2 - lat1
    dlon = radians(b.longitude - a.longitude)
    h = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return round(6371000 * 2 * asin(sqrt(h)))


class AmapService:
    def __init__(self, api_key: str, client: Optional[httpx.AsyncClient] = None):
        self.api_key = api_key
        self.client = client or httpx.AsyncClient(timeout=15, trust_env=False)
        # 同一请求最多同时向高德发出四次查询，避免候选路线瞬间打满接口。
        self._requests = asyncio.Semaphore(4)

    async def _request(self, path: str, *, expect_image=False, **params: str):
        """统一限速和高德错误处理；不记录包含 Key 的 URL 或响应正文。"""
        for attempt in range(3):
            async with self._requests:
                await _shared_limiter().wait()
                try:
                    response = await self.client.get(
                        f"{BASE_URL}{path}",
                        params={"key": self.api_key, **({} if expect_image else {"output": "JSON"}), **params},
                    )
                except httpx.RequestError as exc:
                    logger.warning("高德请求异常 path=%s http_status=none infocode=none info=none attempt=%d error_type=%s",
                                   path, attempt + 1, type(exc).__name__)
                    raise
            status = response.status_code
            content_type = response.headers.get("content-type", "")
            data = None
            if not (expect_image and isinstance(content_type, str) and content_type.startswith("image/")):
                try:
                    parsed = response.json()
                    if isinstance(parsed, dict):
                        data = parsed
                except (ValueError, TypeError):
                    pass
            code = str(data.get("infocode", "")) if data else ""
            info = str(data.get("info", "")) if data else ""
            safe_code = code if code.isascii() and code.isdigit() and len(code) <= 10 else "unknown"
            safe_info = info if info in _SAFE_INFOS else "unknown"
            if status >= 400 or (data is not None and str(data.get("status")) != "1"):
                logger.warning("高德请求失败 path=%s http_status=%s infocode=%s info=%s attempt=%d",
                               path, status, safe_code, safe_info, attempt + 1)
            if code in {"10003", "10044", "10045"} or info in {
                "DAILY_QUERY_OVER_LIMIT", "USER_DAILY_QUERY_OVER_LIMIT", "USER_ABROAD_DAILY_QUERY_OVER_LIMIT"
            }:
                raise AmapRateLimitError("高德地图日配额已用尽，请检查配额或次日重试。")
            if code == "10004" or info == "ACCESS_TOO_FREQUENT":
                raise AmapRateLimitError("高德地图分钟访问量超限，请稍后重试。")
            retryable = status == 429 or code in _QPS_CODES or info in _QPS_INFOS
            if retryable:
                if attempt == 2:
                    raise AmapRateLimitError()
                delay = _retry_after(response) if status == 429 else None
                if delay is not None and delay > 5:
                    logger.warning("高德停止重试 path=%s reason=retry_after_too_long attempt=%d", path, attempt + 1)
                    raise AmapRateLimitError("高德地图请求受限，请稍后重试。")
                if delay is None:
                    delay = 0.5 * (2 ** attempt) + random.uniform(0, 0.25)
                logger.warning("高德准备重试 path=%s delay_seconds=%.2f next_attempt=%d", path, delay, attempt + 2)
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            if expect_image and isinstance(content_type, str) and content_type.startswith("image/"):
                return response
            if data is None:
                logger.warning("高德响应无效 path=%s http_status=%s infocode=unknown info=unknown attempt=%d", path, status, attempt + 1)
                raise AmapServiceError("高德服务未返回有效数据")
            if str(data.get("status")) != "1":
                # 上游 info 可能包含敏感信息，不将原始响应暴露给调用方。
                safe_info = info if info.replace("_", "").isalnum() and info.isupper() else "未知错误"
                raise AmapServiceError(f"高德服务返回错误：{safe_info}")
            return data
        raise AssertionError("unreachable")

    async def _get(self, path: str, **params: str) -> dict[str, Any]:
        return await self._request(path, **params)

    async def search(self, city: str, keyword: str) -> list[Place]:
        data = await self._get("/place/text", city=city, citylimit="true", keywords=keyword, offset="20")
        return self._parse_places(data, keyword)

    async def search_around(self, city: str, longitude: float, latitude: float,
                      keyword: str, radius_meters: int, types: str) -> list[Place]:
        data = await self._get(
            "/place/around", city=city, citylimit="true",
            location=f"{longitude:.6f},{latitude:.6f}", keywords=keyword,
            radius=str(radius_meters), types=types, offset="20", sortrule="distance",
        )
        return self._parse_places(data, keyword)

    @staticmethod
    def _parse_places(data: dict, keyword: str) -> list[Place]:
        places: list[Place] = []
        for poi in data.get("pois", []):
            location = str(poi.get("location") or "")
            try:
                lon, lat = (float(part) for part in location.split(","))
            except (ValueError, TypeError):
                continue
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                continue
            address = poi.get("address") or ""
            places.append(Place(
                poi_id=str(poi.get("id") or ""),
                name=str(poi.get("name") or ""),
                address=address if isinstance(address, str) else "",
                category=str(poi.get("type") or keyword),
                longitude=lon,
                latitude=lat,
                adcode=str(poi.get("adcode") or ""),
                keyword=keyword,
                typecode=str(poi.get("typecode") or ""),
            ))
        return [place for place in places if place.poi_id and place.name]

    async def geocode_candidates(self, city: str, address: str) -> list[Place]:
        data = await self._get("/geocode/geo", city=city, address=address)
        found = []
        for index, entry in enumerate(data.get("geocodes") or []):
            try:
                lon, lat = (float(part) for part in entry["location"].split(","))
            except (KeyError, ValueError, TypeError):
                continue
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                continue
            label = entry.get("formatted_address") or address
            found.append(Place(
                poi_id=f"geocode:{index}", name=str(label), address=str(label),
                category="地址匹配", longitude=lon, latitude=lat,
                adcode=str(entry.get("adcode") or ""), keyword=address,
            ))
        return found

    async def geocode(self, city: str, address: str) -> Optional[tuple[float, float]]:
        if not address:
            return None
        data = await self._get("/geocode/geo", city=city, address=address)
        results = data.get("geocodes") or []
        if not results:
            return None
        try:
            lon, lat = (float(part) for part in results[0]["location"].split(","))
            return lon, lat
        except (KeyError, ValueError, TypeError):
            return None

    async def convert_gps_to_amap(self, longitude: float, latitude: float) -> tuple[float, float]:
        """将设备常见的 WGS84 坐标转换为高德 GCJ-02 坐标。"""
        data = await self._get(
            "/assistant/coordinate/convert",
            locations=f"{longitude:.6f},{latitude:.6f}",
            coordsys="gps",
        )
        try:
            converted = str(data["locations"]).split(";")[0]
            lon, lat = (float(value) for value in converted.split(","))
        except (KeyError, TypeError, ValueError) as exc:
            raise AmapServiceError("高德未返回有效的坐标转换结果") from exc
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise AmapServiceError("高德返回的转换坐标超出范围")
        return lon, lat

    async def walking_route(self, origin: Place, destination: Place) -> WalkingRoute:
        data = await self._get(
            "/direction/walking",
            origin=f"{origin.longitude},{origin.latitude}",
            destination=f"{destination.longitude},{destination.latitude}",
        )
        paths = (data.get("route") or {}).get("paths") or []
        if not paths:
            return WalkingRoute(None, None, None)
        path = paths[0]
        try:
            distance, duration = int(path["distance"]), ceil(int(path["duration"]) / 60)
        except (KeyError, ValueError, TypeError):
            distance, duration = None, None
        points: list[str] = []
        for step in path.get("steps") or []:
            for point in str(step.get("polyline") or "").split(";"):
                if point and (not points or points[-1] != point):
                    points.append(point)
        return WalkingRoute(distance, duration, ";".join(points) if len(points) >= 2 else None)

    async def walking_leg(self, origin: Place, destination: Place) -> tuple[Optional[int], Optional[int]]:
        route = await self.walking_route(origin, destination)
        return route.distance_m, route.duration_min

    async def reverse_adcode(self, longitude: float, latitude: float) -> str:
        data = await self._get("/geocode/regeo", location=f"{longitude:.6f},{latitude:.6f}", extensions="base")
        adcode = (data.get("regeocode") or {}).get("addressComponent", {}).get("adcode")
        if not isinstance(adcode, str) or not adcode.isdigit():
            raise AmapServiceError("高德未返回起点所在行政区，无法查询天气")
        return adcode

    async def weather(self, adcode: str, visit_date: Optional[date]) -> Optional[Weather]:
        if not adcode:
            return None
        data = await self._get("/weather/weatherInfo", city=adcode, extensions="all")
        forecasts = data.get("forecasts") or []
        casts = (forecasts[0].get("casts") or []) if forecasts else []
        wanted = visit_date.isoformat() if visit_date else datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
        for cast in casts:
            if cast.get("date") == wanted:
                return Weather(
                    source="AMAP", source_url="https://www.amap.com",
                    date=wanted,
                    day_weather=str(cast.get("dayweather") or ""),
                    night_weather=str(cast.get("nightweather") or ""),
                    day_temp=str(cast.get("daytemp") or ""),
                    night_temp=str(cast.get("nighttemp") or ""),
                )
        return None

    async def static_map(self, points: list[tuple[float, float]], *, start: int | None = None) -> tuple[bytes, str]:
        markers = "|".join(
            f"mid,0x0F766E,{chr(65 + index)}:{lon:.6f},{lat:.6f}"
            for index, (lon, lat) in enumerate(points)
        )
        if start is not None:
            labels = "|".join(
                f"{('起点' if index == 0 else str(start + index - 1))},0,1,16,0xFFFFFF,0x0F766E:{lon:.6f},{lat:.6f}"
                for index, (lon, lat) in enumerate(points)
            )
            response = await self._request("/staticmap", expect_image=True, size="900*500", labels=labels)
        else:
            response = await self._request("/staticmap", expect_image=True, size="900*500", markers=markers)
        if isinstance(response, dict):
            logger.warning("高德静态地图响应无效 path=/staticmap http_status=200 infocode=unknown info=unknown")
            raise ValueError("高德静态地图未返回图片")
        content_type = response.headers.get("content-type", "")
        return response.content, content_type


def amap_marker_url(place: Place) -> str:
    return "https://uri.amap.com/marker?" + urlencode({
        "position": f"{place.longitude},{place.latitude}", "name": place.name, "coordinate": "gaode"
    })
