"""高德 Web 服务：真实 POI、步行路线、天气与静态地图。"""

import asyncio
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo
from math import asin, ceil, cos, radians, sin, sqrt
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

from app.models.walk import Weather


BASE_URL = "https://restapi.amap.com/v3"


class AmapServiceError(ValueError):
    pass


class AmapRateLimitError(AmapServiceError):
    def __init__(self):
        super().__init__("高德地图接口请求过于频繁，已触发限流；部分地点或路线未能查询，请稍后重试。")


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

    async def _get(self, path: str, **params: str) -> dict[str, Any]:
        async with self._requests:
            response = await self.client.get(
                f"{BASE_URL}{path}",
                params={"key": self.api_key, "output": "JSON", **params},
            )
        if getattr(response, "status_code", None) == 429:
            raise AmapRateLimitError()
        response.raise_for_status()
        data = response.json()
        if str(data.get("status")) != "1":
            info = str(data.get("info", "未知错误"))
            if ("QPS" in info and "EXCEED" in info) or info == "ACCESS_TOO_FREQUENT":
                raise AmapRateLimitError()
            raise AmapServiceError(f"高德服务返回错误：{info}")
        return data

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

    async def static_map(self, points: list[tuple[float, float]]) -> tuple[bytes, str]:
        markers = "|".join(
            f"mid,0x0F766E,{chr(65 + index)}:{lon:.6f},{lat:.6f}"
            for index, (lon, lat) in enumerate(points)
        )
        response = await self.client.get(
            f"{BASE_URL}/staticmap",
            params={"key": self.api_key, "size": "900*500", "markers": markers},
        )
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if not content_type.startswith("image/"):
            raise ValueError("高德静态地图未返回图片")
        return response.content, content_type


def amap_marker_url(place: Place) -> str:
    return "https://uri.amap.com/marker?" + urlencode({
        "position": f"{place.longitude},{place.latitude}", "name": place.name, "coordinate": "gaode"
    })
