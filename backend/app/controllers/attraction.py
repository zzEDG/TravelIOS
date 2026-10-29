"""TravelOS 景点游览子系统接口。"""

from fastapi import APIRouter, Depends
from app.models.travel import AttractionPlanRequest, AttractionPlanResponse
from app.agents.main_agent import MainAgent
from app.controllers.dependencies import get_main_agent
from app.controllers.errors import TravelRoute

router = APIRouter(route_class=TravelRoute, prefix="/attraction", tags=["景点游览"])


@router.post("/plan", response_model=AttractionPlanResponse)
async def plan_attraction(request: AttractionPlanRequest,
           agent: MainAgent = Depends(get_main_agent)) -> AttractionPlanResponse:
    return await agent.plan_for("ATTRACTION", request)
