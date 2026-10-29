"""位置控制器：接收起点搜索请求并返回可选地点。"""
from fastapi import APIRouter, Depends
from app.controllers.errors import TravelRoute
from app.models.travel import LocationSearchRequest, LocationSearchResponse
from app.controllers.dependencies import get_amap
from app.integrations.amap_service import AmapService
from app.services.location_service import search_locations as find_locations

router = APIRouter(route_class=TravelRoute, tags=["统一入口"])

@router.post("/locations/search", response_model=LocationSearchResponse)
async def search_locations(request: LocationSearchRequest,
                     amap: AmapService = Depends(get_amap)):
    return LocationSearchResponse(data=await find_locations(request, amap))
