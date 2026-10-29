"""地图控制器：将地图服务结果转换成 HTTP 图片响应。"""
from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from app.controllers.dependencies import get_amap
from app.integrations.amap_service import AmapService
from app.controllers.errors import TravelAPIError
from app.services.map_service import get_static_map, MapServiceError

router = APIRouter(tags=["统一入口"])

@router.get("/maps/static", summary="获取高德地点分布图")
async def get_map(
    points: str = Query(..., min_length=3, max_length=250),
    amap: AmapService = Depends(get_amap),
) -> Response:
    try:
        image, content_type = await get_static_map(points, amap)
        return Response(content=image, media_type=content_type,
                        headers={"Cache-Control": "public, max-age=3600"})
    except (ValueError, TypeError) as exc:
        raise TravelAPIError(422, 40002, str(exc)) from exc
    except MapServiceError as exc:
        raise TravelAPIError(502, 50002, str(exc)) from exc
