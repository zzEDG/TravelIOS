"""TravelOS 城市漫步子系统接口。"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from app.controllers.dependencies import get_amap
from app.integrations.amap_service import AmapService
from app.models.walk import WalkRequest, WalkResponse
from app.controllers.maps import get_map as get_static_map
from app.controllers.errors import TravelAPIError, TravelRoute
from app.models.travel import CityWalkPlanRequest, CityWalkPlanResponse
from app.agents.main_agent import MainAgent
from app.controllers.dependencies import get_main_agent

router = APIRouter(route_class=TravelRoute, prefix="/citywalk", tags=["城市漫步"])


@router.post("/plan", response_model=CityWalkPlanResponse)
async def plan_citywalk(request: CityWalkPlanRequest,
           agent: MainAgent = Depends(get_main_agent)) -> CityWalkPlanResponse:
    return await agent.plan_for("CITYWALK", request)


# 旧城市漫步URL只作兼容，属于同一子系统，不再单独建路由文件。
legacy_router = APIRouter(prefix="/walk", tags=["旧版兼容"], deprecated=True)

@legacy_router.post("/plan", response_model=WalkResponse, summary="生成主题漫游路线")
async def create_walk(request: WalkRequest, agent: MainAgent = Depends(get_main_agent)) -> WalkResponse:
    try:
        return WalkResponse(data=await agent.get_subagent("CITYWALK").plan_legacy(request))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="路线生成失败，请稍后重试或检查服务配置") from exc


@legacy_router.get("/map", summary="旧版地点分布图")
async def get_map(
    points: str = Query(..., min_length=3, max_length=250),
    amap: AmapService = Depends(get_amap),
) -> Response:
    try:
        return await get_static_map(points, amap)
    except TravelAPIError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
