"""位置业务：优先搜索 POI，无结果时解析地址，并按地点 ID 去重。"""
from datetime import datetime, timezone
from app.integrations.amap_service import AmapService, amap_marker_url
from app.models.travel import GeoLocation, LocationSearchRequest, PlaceCandidate

async def search_locations(request: LocationSearchRequest, amap: AmapService) -> list[PlaceCandidate]:
    places = await amap.search(request.city.strip(), request.query.strip())
    if not places:
        places = await amap.geocode_candidates(request.city.strip(), request.query.strip())
    if not places:
        raise ValueError("没有找到这个位置，请补充街道、门牌或附近地标")

    #  用字典ID 去重，保留第一个出现的地点,
    found = {}
    for place in places:
        found.setdefault(place.poi_id, place)
    return [
        PlaceCandidate(
            id=p.poi_id, name=p.name, address=p.address, category=p.category,
            location=GeoLocation(latitude=p.latitude, longitude=p.longitude),
            source_url=amap_marker_url(p), retrieved_at=datetime.now(timezone.utc),
        ) for p in list(found.values())[:8]
    ]
